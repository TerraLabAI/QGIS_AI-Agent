# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Atlas, attribute tables, pictures and the 3D view capture for print layouts."""








from __future__ import annotations

import base64
import os
import tempfile
import uuid

from qgis.core import (
    QgsExpression,
    QgsLayoutExporter,
    QgsLayoutFrame,
    QgsLayoutItemAttributeTable,
    QgsLayoutItemMap,
    QgsLayoutItemPicture,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsPrintLayout,
    QgsProject,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QImage
from qgis.utils import iface

from ..core import limits
from ..core.security import expand_path, validate_path
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .core_tools import _find_layer, _layer_not_found_error
from .data_tools import _safe_filename
from .harvest_project import qgis_enum
from .layout_tools import _first_map_item, _mm_point, _mm_size, _resolve_layout, _with_new_item

_PICTURE_MODES = ["zoom", "stretch", "clip", "zoom_resize_frame", "frame_to_image_size"]
_ATLAS_FORMATS = ["pdf", "png", "jpg", "tif", "svg"]




_LAYOUT_TABLE_ROWS = 20


def register_harvest_layout_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="add_layout_picture",
        input_schema={
            "type": "object",
            "properties": {
                "layout_name": {"type": "string"},
                "path": {"type": "string"},
                "x": {"type": "number", "minimum": -10000, "maximum": 10000},
                "y": {"type": "number", "minimum": -10000, "maximum": 10000},
                "width": {"type": "number", "minimum": 0.001, "maximum": 10000},
                "height": {"type": "number", "minimum": 0.001, "maximum": 10000},
                "resize_mode": {"type": "string", "enum": _PICTURE_MODES},
            },
            "required": ["layout_name", "path"],
        },
        handler=_add_layout_picture,
    ))

    registry.register(Tool(
        name="add_layout_table",
        input_schema={
            "type": "object",
            "properties": {
                "layout_name": {"type": "string"},
                "layer_name": {"type": "string"},
                "x": {"type": "number", "minimum": -10000, "maximum": 10000},
                "y": {"type": "number", "minimum": -10000, "maximum": 10000},
                "width": {"type": "number", "minimum": 0.001, "maximum": 10000},
                "height": {"type": "number", "minimum": 0.001, "maximum": 10000},
                "max_rows": {"type": "integer", "minimum": 1, "maximum": 5000,
                             "default": _LAYOUT_TABLE_ROWS},
                "fields": {"type": "array", "items": {"type": "string"}, "maxItems": 1000},
                "filter_expression": {"type": "string"},
                "visible_only": {"type": "boolean"},
                "selected_only": {"type": "boolean"},
            },
            "required": ["layout_name", "layer_name"],
        },
        handler=_add_layout_table,
    ))

    registry.register(Tool(
        name="configure_atlas",
        input_schema={
            "type": "object",
            "properties": {
                "layout_name": {"type": "string"},
                "coverage_layer": {"type": "string"},
                "enabled": {"type": "boolean"},
                "page_name_expression": {"type": "string"},
                "filter_expression": {"type": "string"},
                "sort_expression": {"type": "string"},
                "sort_ascending": {"type": "boolean"},
                "drive_maps": {"type": "boolean"},
                "margin": {"type": "number", "minimum": 0, "maximum": 100},
                "hide_coverage": {"type": "boolean"},
            },
            "required": ["layout_name", "coverage_layer"],
        },
        handler=_configure_atlas,
    ))

    registry.register(Tool(
        name="export_atlas",
        input_schema={
            "type": "object",
            "properties": {
                "layout_name": {"type": "string"},
                "output_path": {"type": "string"},
                "format": {"type": "string", "enum": _ATLAS_FORMATS},
                "dpi": {"type": "integer", "minimum": 10, "maximum": 600},
                "filename_expression": {"type": "string"},
                "overwrite": {"type": "boolean"},
                "single_file": {"type": "boolean"},
            },
            "required": ["layout_name", "output_path"],
        },
        handler=_export_atlas,
        destructive=True,
    ))

    registry.register(Tool(
        name="get_3d_screenshot",
        input_schema={
            "type": "object",
            "properties": {
                "view_index": {"type": "integer", "minimum": 0, "maximum": 1000},
                "dpi": {"type": "integer", "minimum": 10, "maximum": 600},
                "pitch": {"type": "number", "minimum": 0, "maximum": 90},
                "heading": {"type": "number", "minimum": -360000, "maximum": 360000},
                "distance": {"type": "number", "minimum": 0.001, "maximum": 1000000000},
                "save_path": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },
            "required": [],
        },
        handler=_get_3d_screenshot,
    ))






def _layout_success():
    return qgis_enum(QgsLayoutExporter, "Success", "ExportResult.Success")


def _export_failed(result) -> bool:
    success = _layout_success()
    try:
        return int(result) != int(success)
    except (TypeError, ValueError):
        return result != success


def _expression_error(text, field_name: str):
    """tool_error when ``text`` is a non-empty expression QGIS cannot parse."""
    if not isinstance(text, str) or not text.strip():
        return None
    expression = QgsExpression(text)
    if not expression.hasParserError():
        return None
    return tool_error(
        f"{field_name} does not parse: {expression.parserErrorString()}",
        "INVALID_ARGS",
        f"Quote field names with \"double quotes\" and strings with 'single quotes'. "
        f"evaluate_expression checks {field_name} before you pass it here.",
    )


def _layout_or_error(name: str):
    """layout_tools' resolver, with the code and suggestion the executor expects."""
    layout, error = _resolve_layout(name)
    if error:
        error.setdefault("code", "INVALID_ARGS")
        error.setdefault("suggestion", "Call list_layouts for the exact name, or create_print_layout to make one.")
    return layout, error


def _resolve_vector_layer(name_or_id: str):
    """Return (layer, None) or (None, error_dict)."""
    layer = _find_layer(name_or_id)
    if not layer:
        return None, _layer_not_found_error(name_or_id)
    if not isinstance(layer, QgsVectorLayer):
        return None, tool_error(f"Layer {name_or_id!r} is not a vector layer", "INVALID_ARGS",
                                "Pass a vector layer; list_layers shows the type of each layer.")
    return layer, None


def _open_3d_views():
    """The open 3D map canvases, [] when none, None when this QGIS cannot list them."""
    if hasattr(iface, "mapCanvases3D"):
        try:
            return list(iface.mapCanvases3D())
        except Exception:  # nosec B110 - 3D view API is optional
            pass
    try:
        from qgis._3d import Qgs3DMapCanvas
        return list(iface.mainWindow().findChildren(Qgs3DMapCanvas))
    except Exception:
        return None






def _add_layout_picture(args: dict) -> dict:
    layout, error = _layout_or_error(args["layout_name"])
    if error:
        return error
    path = expand_path(args["path"])
    if not os.path.isfile(path):
        return tool_error(f"Picture file not found: {path}", "INVALID_ARGS",
                          "Pass the path of an existing PNG, JPG or SVG; QGIS ships SVGs under "
                          "QgsApplication.svgPaths().")

    picture = QgsLayoutItemPicture(layout)
    picture.setPicturePath(path)
    modes = {"zoom": "Zoom", "stretch": "Stretch", "clip": "Clip",
             "zoom_resize_frame": "ZoomResizeFrame", "frame_to_image_size": "FrameToImageSize"}
    mode_name = modes[args.get("resize_mode") or "zoom"]
    mode = qgis_enum(QgsLayoutItemPicture, mode_name, f"ResizeMode.{mode_name}")
    if mode is not None:
        picture.setResizeMode(mode)
    picture.attemptMove(_mm_point(args.get("x", 10), args.get("y", 10)))
    picture.attemptResize(_mm_size(args.get("width", 30), args.get("height", 30)))
    layout.addLayoutItem(picture)

    result = _with_new_item(layout, picture)
    result["path"] = path
    if hasattr(picture, "isMissingImage") and picture.isMissingImage():
        result["warning"] = "QGIS could not read the image; check the file format"
    return result


def _add_layout_table(args: dict) -> dict:
    layout, error = _layout_or_error(args["layout_name"])
    if error:
        return error
    layer, error = _resolve_vector_layer(args["layer_name"])
    if error:
        return error

    fields = list(args.get("fields") or [])
    unknown = [f for f in fields if layer.fields().indexOf(f) < 0]
    if unknown:
        return tool_error(f"Unknown field(s) {unknown} on layer {layer.name()!r}", "INVALID_ARGS",
                          f"Available: {[f.name() for f in layer.fields()]}")



    problem = _expression_error(args.get("filter_expression"), "filter_expression")
    if problem:
        return problem
    map_item = None
    if args.get("visible_only"):
        map_item = _first_map_item(layout)
        if map_item is None:
            return tool_error("visible_only needs a map frame in the layout", "INVALID_ARGS",
                              "Call add_layout_map first, or drop visible_only.")

    table = QgsLayoutItemAttributeTable.create(layout)
    table.setVectorLayer(layer)
    table.setMaximumNumberOfFeatures(int(args.get("max_rows") or _LAYOUT_TABLE_ROWS))
    if fields:
        table.setDisplayedFields(fields)



    clauses = []
    if args.get("filter_expression"):
        clauses.append(f"({args['filter_expression']})")
    if args.get("selected_only"):
        ids = list(layer.selectedFeatureIds())
        clauses.append("FALSE" if not ids else "$id IN (" + ",".join(str(fid) for fid in ids) + ")")
    if clauses:
        table.setFilterFeatures(True)
        table.setFeatureFilter(" AND ".join(clauses))
    if map_item is not None:
        table.setMap(map_item)
        table.setDisplayOnlyVisibleFeatures(True)
    layout.addMultiFrame(table)

    frame = QgsLayoutFrame(layout, table)
    frame.attemptMove(_mm_point(args.get("x", 10), args.get("y", 10)))
    frame.attemptResize(_mm_size(args.get("width", 180), args.get("height", 80)))
    table.addFrame(frame)

    result = _with_new_item(layout, frame)
    result["table_uuid"] = table.uuid()
    result["layer"] = layer.name()
    result["max_rows"] = table.maximumNumberOfFeatures()
    return result


def _configure_atlas(args: dict) -> dict:
    layout, error = _layout_or_error(args["layout_name"])
    if error:
        return error
    layer, error = _resolve_vector_layer(args["coverage_layer"])
    if error:
        return error





    for key in ("filter_expression", "sort_expression", "page_name_expression"):
        problem = _expression_error(args.get(key), key)
        if problem:
            return problem

    atlas = layout.atlas()
    atlas.setEnabled(bool(args.get("enabled", True)))
    atlas.setCoverageLayer(layer)
    atlas.setHideCoverage(bool(args.get("hide_coverage", False)))
    if args.get("page_name_expression"):
        atlas.setPageNameExpression(args["page_name_expression"])



    if "filter_expression" in args and not str(args["filter_expression"] or "").strip():
        atlas.setFilterFeatures(False)
        atlas.setFilterExpression("")
    elif args.get("filter_expression"):
        atlas.setFilterFeatures(True)
        outcome = atlas.setFilterExpression(args["filter_expression"])
        ok, detail = (outcome[0], outcome[1]) if isinstance(outcome, (list, tuple)) else (bool(outcome), "")
        if not ok:
            atlas.setFilterFeatures(False)
            return tool_error(f"Invalid filter_expression: {detail}", "INVALID_ARGS",
                              "Check the expression with evaluate_expression on the coverage layer.")
    if "sort_expression" in args and not str(args["sort_expression"] or "").strip():
        atlas.setSortFeatures(False)
    elif args.get("sort_expression"):
        atlas.setSortFeatures(True)
        atlas.setSortExpression(args["sort_expression"])
        atlas.setSortAscending(bool(args.get("sort_ascending", True)))

    driven = 0
    if args.get("drive_maps", True):
        margin = float(args.get("margin", 0.1))


        if margin > 1.0:
            margin = margin / 100.0
        auto_mode = qgis_enum(QgsLayoutItemMap, "Auto", "AtlasScalingMode.Auto")
        for item in layout.items():
            if isinstance(item, QgsLayoutItemMap):
                item.setAtlasDriven(True)
                if auto_mode is not None:
                    item.setAtlasScalingMode(auto_mode)
                item.setAtlasMargin(margin)
                driven += 1

    atlas.updateFeatures()
    names = []
    for index in range(min(atlas.count(), 5)):
        try:
            name = atlas.nameForPage(index)
        except Exception:
            break
        if name:
            names.append(name)
    result = {
        "layout": layout.name(),
        "coverage_layer": layer.name(),
        "enabled": atlas.enabled(),
        "count": atlas.count(),
        "driven_maps": driven,
        "page_names": names,
    }
    if driven == 0:
        result["note"] = "no map frame follows the atlas yet: add_layout_map, then call configure_atlas again"
    return result


def _export_atlas(args: dict) -> dict:
    layout, error = _layout_or_error(args["layout_name"])
    if error:
        return error
    atlas = layout.atlas()
    if not atlas.enabled() or atlas.coverageLayer() is None:
        return tool_error("Atlas not enabled on this layout", "INVALID_ARGS", "Call configure_atlas first.")

    output_path = expand_path(args["output_path"])
    fmt = (args.get("format") or "pdf").lower()


    dpi = max(10, min(int(args.get("dpi", 300) or 300), limits.MAX_RENDER_DPI))
    path_error = validate_path(output_path, write=True)
    if path_error:
        return tool_error(path_error, "INVALID_ARGS", "Pick a writable output_path.")

    if args.get("filename_expression"):
        outcome = atlas.setFilenameExpression(args["filename_expression"])
        ok, detail = (outcome[0], outcome[1]) if isinstance(outcome, (list, tuple)) else (bool(outcome), "")
        if not ok:
            return tool_error(f"Invalid filename_expression: {detail}", "INVALID_ARGS",
                              "Use an expression that yields a distinct string per feature, e.g. 'page_'||\"name\".")
    atlas.updateFeatures()
    if atlas.count() == 0:
        return tool_error("The atlas has no page: the coverage layer is empty or the filter matches nothing",
                          "INVALID_ARGS", "Check the coverage layer and filter_expression in configure_atlas.")

    one_pdf_per_page = fmt == "pdf" and args.get("single_file") is False
    if fmt == "pdf" and not one_pdf_per_page:
        parent = os.path.dirname(output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        settings = QgsLayoutExporter.PdfExportSettings()
        settings.dpi = dpi
        result, detail = QgsLayoutExporter.exportToPdf(atlas, output_path, settings)
        if _export_failed(result):
            return tool_error(f"Atlas export failed: {detail or result}")
        if not os.path.exists(output_path):
            return tool_error(f"Export reported success but no file was found at {output_path}")
        return {"exported": output_path, "format": fmt, "dpi": dpi, "pages": atlas.count(),
                "file_size": os.path.getsize(output_path)}

    if os.path.exists(output_path) and not os.path.isdir(output_path):
        return tool_error(f"Atlas {'PDF' if one_pdf_per_page else 'image'} output_path is not a folder: "
                          f"{output_path}", "INVALID_ARGS",
                          "Pick a folder for one file per page.")
    try:
        os.makedirs(output_path, exist_ok=True)
    except OSError as exc:
        return tool_error(f"Could not create atlas output folder {output_path}: {exc}", "EXECUTION_FAILED",
                          "Pick another writable folder.")







    names, name_error = _atlas_page_names(atlas)
    if name_error:
        return name_error
    before = set(os.listdir(output_path))


    base = os.path.join(output_path, _safe_filename(layout.name(), "layout"))
    if one_pdf_per_page:




        settings = QgsLayoutExporter.PdfExportSettings()
        settings.dpi = dpi
        result, detail = QgsLayoutExporter.exportToPdfs(atlas, base, settings)
    elif fmt == "svg":
        settings = QgsLayoutExporter.SvgExportSettings()
        settings.dpi = dpi
        result, detail = QgsLayoutExporter.exportToSvg(atlas, base, settings)
    else:
        settings = QgsLayoutExporter.ImageExportSettings()
        settings.dpi = dpi
        result, detail = QgsLayoutExporter.exportToImage(atlas, base, fmt, settings)
    if _export_failed(result):
        return tool_error(f"Atlas export failed: {detail or result}")

    written = sorted(entry for entry in (set(os.listdir(output_path)) - before)
                     if os.path.isfile(os.path.join(output_path, entry)))
    out = {"exported": output_path, "format": fmt, "dpi": dpi, "pages": atlas.count(),
           "files": written[:20], "file_count": len(written)}
    if names:
        out["page_names"] = names[:20]
    if len(written) != atlas.count():



        out["file_count_note"] = (f"{atlas.count()} pages were rendered and {len(written)} file names are new "
                                  f"in the folder; the rest replaced files that were already there.")
    return out


def _atlas_page_names(atlas) -> tuple:
    """(page names, refusal) for an atlas about to write one file per feature."""












    try:
        if not (atlas.filenameExpression() or "").strip():
            return [], None
    except Exception:  # nosec B110 - an atlas that cannot say keeps the check below
        pass
    try:
        count = int(atlas.count())
    except Exception:  # noqa: BLE001 - nothing to walk
        return [], None

    names = []
    try:
        if not atlas.beginRender():
            return [], None
        try:
            for index in range(count):
                if not atlas.seekTo(index):
                    break
                names.append(str(atlas.currentFilename() or ""))
        finally:
            atlas.endRender()
    except Exception as exc:  # noqa: BLE001 - see below


        return [], tool_error(
            f"The page names of this atlas could not be worked out before exporting ({exc}), so the export "
            f"was not started.",
            "EXECUTION_FAILED",
            "Remove filename_expression to name the pages after the layout, or simplify it.",
        )

    problems = _bad_page_names(names)
    if problems:
        return names, tool_error(
            "The filename expression produces page names that are not plain file names: "
            + "; ".join(problems[:5]) + ("" if len(problems) <= 5 else f" (and {len(problems) - 5} more)"),
            "INVALID_ARGS",
            "Every page must be one ordinary file name, unique within the folder. Wrap the expression so it "
            "yields a plain name, for example replace('\"name\"', '/', '-'), or add the feature id to make "
            "duplicates distinct.",
        )
    return names, None


def _bad_page_names(names: list) -> list:
    """Why each page name is not a plain, unique file component."""
    from .data_tools import _WINDOWS_FORBIDDEN_CHARS, _WINDOWS_RESERVED_NAMES

    problems = []
    seen: dict = {}
    for name in names:
        text = str(name or "")
        if not text.strip():
            problems.append("one page has an empty name")
            continue
        if os.path.isabs(text) or (len(text) > 1 and text[1] == ":"):
            problems.append(f"{text!r} is an absolute path")
            continue
        if "/" in text or "\\" in text:
            problems.append(f"{text!r} carries a path separator")
            continue
        if text in (".", "..") or ".." in text.split(os.sep):
            problems.append(f"{text!r} points outside the output folder")
            continue
        forbidden = sorted({c for c in text if c in _WINDOWS_FORBIDDEN_CHARS or ord(c) < 32})
        if forbidden:
            problems.append(f"{text!r} holds {' '.join(repr(c) for c in forbidden)}, which Windows refuses")
            continue
        if os.path.splitext(text)[0].strip(" .").lower() in _WINDOWS_RESERVED_NAMES:
            problems.append(f"{text!r} is a reserved device name on Windows")
            continue
        key = text.lower()
        if key in seen:

            problems.append(f"{text!r} is used by more than one page, so they would overwrite each other")
            continue
        seen[key] = True
    return problems


def _get_3d_screenshot(args: dict) -> dict:
    try:
        from qgis._3d import Qgs3DMapSettings, QgsLayoutItem3DMap
    except ImportError as e:
        return tool_error(f"3D support is unavailable in this QGIS build (qgis._3d import failed: {e})",
                          "EXECUTION_FAILED", "Use take_screenshot for the 2D canvas.")
    views = _open_3d_views()
    if views is None:
        return tool_error("This QGIS cannot list its 3D views (QGIS 3.36 or newer is needed)",
                          "EXECUTION_FAILED", "Use take_screenshot for the 2D canvas.")
    if not views:
        return tool_error("No 3D map view is open. Open one via View > 3D Map Views > New 3D Map View, "
                          "frame your scene, then retry.", "INVALID_ARGS",
                          "trigger_menu_action can open the 3D view.")
    view_index = int(args.get("view_index", 0))
    if view_index < 0 or view_index >= len(views):
        return tool_error(f"view_index {view_index} out of range (open 3D views: {len(views)})", "INVALID_ARGS",
                          f"Use a view_index from 0 to {len(views) - 1}.")
    canvas3d = views[view_index]


    dpi = max(10, min(int(args.get("dpi", 96)), limits.MAX_RENDER_DPI))


    map_settings = Qgs3DMapSettings(canvas3d.mapSettings())
    pose = canvas3d.cameraController().cameraPose()
    if args.get("pitch") is not None:
        pose.setPitchAngle(max(0.0, min(float(args["pitch"]), 90.0)))
    if args.get("heading") is not None:
        pose.setHeadingAngle(float(args["heading"]) % 360.0)
    if args.get("distance") is not None and float(args["distance"]) > 0:
        pose.setDistanceFromCenterPoint(float(args["distance"]))

    size = canvas3d.size()
    page_w = 200.0
    page_h = page_w * max(1, size.height()) / max(1, size.width())
    layout = QgsPrintLayout(QgsProject.instance())
    layout.initializeDefaults()
    layout.pageCollection().page(0).setPageSize(QgsLayoutSize(page_w, page_h))
    item = QgsLayoutItem3DMap(layout)
    item.attemptMove(QgsLayoutPoint(0, 0))
    item.attemptResize(QgsLayoutSize(page_w, page_h))
    item.setMapSettings(map_settings)
    item.setCameraPose(pose)
    layout.addLayoutItem(item)

    save_path = args.get("save_path")
    if save_path:
        save_path = expand_path(save_path)
        path_error = validate_path(save_path, write=True)
        if path_error:
            return tool_error(path_error, "INVALID_ARGS", "Pick a writable save_path.")
        target = save_path
    else:
        target = os.path.join(tempfile.gettempdir(), f"agent_3d_{uuid.uuid4().hex[:8]}.png")

    export_settings = QgsLayoutExporter.ImageExportSettings()
    export_settings.dpi = dpi
    result = QgsLayoutExporter(layout).exportToImage(target, export_settings)
    if _export_failed(result) or not os.path.exists(target):
        return tool_error(f"3D layout export failed (export code {result})", "EXECUTION_FAILED",
                          "Check the 3D view renders on screen, then retry with a lower dpi.")

    image = QImage(target)
    camera = {"pitch": pose.pitchAngle(), "heading": pose.headingAngle(),
              "distance": pose.distanceFromCenterPoint()}
    common = {"width": image.width(), "height": image.height(), "format": "png",
              "view_index": view_index, "open_3d_views": len(views), "camera": camera}
    if save_path:
        return {"saved_path": target, **common}
    try:
        with open(target, "rb") as handle:
            data = handle.read()
    finally:
        try:
            os.remove(target)
        except OSError:
            pass
    return {"image_base64": base64.b64encode(data).decode("ascii"), **common}
