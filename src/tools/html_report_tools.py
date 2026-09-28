# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






















from __future__ import annotations

import base64
import os

from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core import output_paths, report_page
from ..core.background import run_on_main_thread
from ..core.context import tr
from ..core.security import validate_path, validate_read
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from ..core.writeback import write_atomic

MAX_FIGURES = 12
MAX_FIGURE_WIDTH = 1600
MAX_FIGURE_HEIGHT = 1200


def register_html_report_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="write_html_report",
        danger="write",
        replaces_file_at="path",
        label=QT_TRANSLATE_NOOP("AIAgent", "Write the report {title}"),
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 200},
                "html": {"type": "string", "minLength": 1},
                "figures": {
                    "type": "array",
                    "maxItems": MAX_FIGURES,
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"},
                            "layer_names": {"type": "array", "items": {"type": "string"}, "maxItems": 200},
                            "extent": {
                                "type": "object",
                                "properties": {"xmin": {"type": "number"}, "ymin": {"type": "number"},
                                               "xmax": {"type": "number"}, "ymax": {"type": "number"}},
                                "required": ["xmin", "ymin", "xmax", "ymax"],
                            },
                            "crs": {"type": "string"},
                            "width": {"type": "integer", "minimum": 1, "maximum": MAX_FIGURE_WIDTH},
                            "height": {"type": "integer", "minimum": 1, "maximum": MAX_FIGURE_HEIGHT},
                        },
                        "required": ["id"],
                    },
                },
                "path": {"type": "string"},
                "overwrite": {"type": "boolean"},
                "open": {"type": "boolean"},
            },
            "required": ["title", "html"],
        },
        handler=_write_html_report,
        background=True,
    ))


def _write_html_report(args: dict) -> dict:
    title = str(args.get("title") or "").strip()
    page = args.get("html")
    if not title:
        return tool_error("title is empty.", "INVALID_ARGS", "title needs a short name.")
    if not isinstance(page, str) or not page.strip():
        return tool_error("html is empty.", "INVALID_ARGS", "html is the whole page: <!doctype html> to </html>.")
    if len(page.encode("utf-8")) > report_page.MAX_PAGE_BYTES:
        return tool_error(f"html is over {report_page.MAX_PAGE_BYTES} bytes.", "INVALID_ARGS",
                          "figures render maps without pasted images; long tables add bytes too.")



    target = str(args.get("path") or "").strip()
    if not target:
        target = os.path.join(output_paths.default_folder(), output_paths.safe_file_name(title, "report") + ".html")
    if os.path.splitext(target)[1].lower() not in (".html", ".htm"):
        target += ".html"
    overwrite = bool(args.get("overwrite"))
    refusal = validate_path(target, write=True, overwrite=overwrite)
    if refusal:
        return tool_error(refusal, "INVALID_ARGS",
                          "overwrite true replaces it; another path works too." if os.path.exists(target)
                          else "The project folder, Documents and the exports folder take it.")

    figures, figure_facts, figure_errors = _render_figures(args.get("figures") or [])
    declared = set(figures)
    placed = report_page.figure_ids(page)
    unplaced = [name for name in declared if name not in placed]

    page = report_page.as_document(page, title)

    page, embedded = report_page.embed_images(page, figures, os.path.dirname(target), refusal=validate_read)

    bridge = run_on_main_thread(_bridge_settings, timeout=10)
    port = None
    if bridge is not None:
        port, token = bridge
        page = report_page.with_bridge(page, port, token, {
            "show": tr("Show in QGIS"), "done": tr("Shown in QGIS"), "away": tr("QGIS is not running"),
        })
    page = report_page.with_policy(page, port)

    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        write_atomic(target, page)
    except OSError as exc:
        return tool_error(f"The page could not be written: {str(exc)[:200]}", "EXECUTION_FAILED",
                          "The folder may not exist or lack room; another path helps.")

    opened = False
    if args.get("open", True):
        opened = bool(run_on_main_thread(_open_in_browser, target, timeout=15))

    result = {
        "path": target,
        "bytes": os.path.getsize(target),
        "opened": opened,
        "qgis_links": bridge is not None,
        "figures": figure_facts,
        "files": [{"path": target, "kind": "html"}],
    }
    if figure_errors:
        result["figure_errors"] = figure_errors
    if embedded["figures_missing"]:
        result["figures_missing"] = embedded["figures_missing"]
        result["warning"] = ("The page places figures no entry of figures declares: "
                             + ", ".join(embedded["figures_missing"]) + "; those show blank.")
    if unplaced:
        result["figures_unplaced"] = unplaced

        result["figures_unplaced_note"] = ('These maps are rendered and not on the page: an <img src="figure:<id>"> '
                                           "tag places each (" + ", ".join(f'<img src="figure:{n}">' for n in unplaced)
                                           + ").")
    if embedded["files_embedded"]:
        result["images_embedded"] = embedded["files_embedded"]
    if embedded["files_skipped"]:
        result["images_skipped"] = embedded["files_skipped"]
    if not opened and args.get("open", True):
        result["note"] = "The browser did not open the page; the path is above."
    return result


def _render_figures(declared) -> tuple[dict, dict, list]:

    from .advanced_render import _render_map

    figures: dict[str, tuple[str, bytes]] = {}
    facts: dict[str, dict] = {}
    errors: list[dict] = []
    if not isinstance(declared, list):
        return figures, facts, [{"error": "figures must be a list"}]
    for entry in declared[:MAX_FIGURES]:
        if not isinstance(entry, dict):
            errors.append({"error": "a figure must be an object with an id"})
            continue
        name = str(entry.get("id") or "").strip()
        if not report_page.FIGURE_ID.match(name):
            errors.append({"id": name, "error": "id must be letters, digits, - or _, at most 64 characters"})
            continue
        render_args = {"warmup": True}
        for key in ("layer_names", "extent", "crs"):
            if entry.get(key):
                render_args[key] = entry[key]
        width, height = entry.get("width"), entry.get("height")
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            render_args["width"] = min(width, MAX_FIGURE_WIDTH)
            render_args["height"] = min(height, MAX_FIGURE_HEIGHT)
        drawn = _render_map(render_args)
        if not isinstance(drawn, dict) or "_error" in drawn or not drawn.get("image_base64"):
            message = drawn.get("_error") if isinstance(drawn, dict) else "no image"
            errors.append({"id": name, "error": str(message)[:300]})
            continue
        try:
            raw = base64.b64decode(drawn["image_base64"])
        except (ValueError, TypeError):
            errors.append({"id": name, "error": "the render came back unreadable"})
            continue
        mime = "image/png" if drawn.get("format") == "png" else "image/jpeg"
        figures[name] = (mime, raw)
        fact = {"width": drawn.get("width"), "height": drawn.get("height"),
                "layers_rendered": drawn.get("layers_rendered")}


        for key in ("blank", "blank_note", "extent", "render_stable"):
            if drawn.get(key) is not None:
                fact[key] = drawn[key]
        facts[name] = fact
    return figures, facts, errors


def _bridge_settings():
    from ..core import report_bridge

    return report_bridge.ensure_started()


def _open_in_browser(path: str) -> bool:
    from ..ui.external_links import open_local_path

    return open_local_path(path)
