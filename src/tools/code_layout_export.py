# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



















from __future__ import annotations

import os
import threading
import weakref

from qgis.core import QgsFeedback, QgsLayoutExporter, QgsPrintLayout, QgsProject, QgsReadWriteContext
from qgis.PyQt.QtCore import QCoreApplication, QTimer
from qgis.PyQt.QtXml import QDomDocument

from ..core import background, limits
from ..core.host_platform import remove_tree
from ..core.logger import log
from ..core.qt_compat import enum_member
from . import code_clock, code_processing, layout_child
from .code_tripwire import AS_WRITTEN

_POLL_MS = 100



_QUICK_PAGE_S = 2.0
_quick_call = None
_SETTINGS = {"exportToPdf": "PdfExportSettings", "exportToImage": "ImageExportSettings"}
LAYOUT_FILE = "layout.xml"


def tr(text: str) -> str:
    return QCoreApplication.translate("CodeLayoutExport", text)


class _Child:


    def __init__(self):
        self.stop = threading.Event()
        self.report: dict | None = None

    def cancel(self) -> None:
        self.stop.set()


def drawn_apart(method: str, args: tuple, kwargs: dict):


    try:
        plan = _plan(method, args, kwargs)
    except Exception as exc:  # noqa: BLE001
        log(f"layout export from code stays in QGIS: {exc}")
        return AS_WRITTEN
    if plan is None:
        return AS_WRITTEN
    job, target, context = plan
    try:
        return _drawn_by_child(job, target, context)
    finally:
        remove_tree(os.path.dirname(job["project_path"]))


def _drawn_by_child(job: dict, target: str, context):
    global _quick_call
    child = _Child()
    wait = code_processing.Wait(child, context.run_id, context.stop)
    timer = QTimer()
    timer.setInterval(_POLL_MS)

    def look() -> None:
        if wait.window is not None:
            wait.window.busy()
        if child.report is not None:
            wait.finished(bool(child.report.get("ok")), child.report)

    def start() -> None:
        budget = float(limits.current("CALL_MAX_SECONDS_BACKGROUND")) * 0.8
        background.start_kept_thread(_draw, job, child, budget, name="ai-agent-layout-export")
        timer.start()

    timer.timeout.connect(look)
    try:
        code_processing.wait_under_window(wait, tr("the layout export"), QgsFeedback(), start)
    finally:
        timer.stop()
    report = child.report or {}
    success = enum_member(QgsLayoutExporter, "ExportResult", "Success")
    staged = job["output_path"]
    if not (report.get("ok") and report.get("result") == int(getattr(success, "value", success))
            and os.path.exists(staged)):
        reason = str(report.get("error") or report.get("error_text") or f"export result {report.get('result')}")
        log(f"layout export from code drawn in QGIS after all: {reason[:300]}")
        return AS_WRITTEN
    try:
        layout_child.put_in_place(staged, target)
    except OSError:

        return enum_member(QgsLayoutExporter, "ExportResult", "FileError")
    log("layout export from code drawn in a separate QGIS: "
        + ", ".join(f"{key} {report[key]}" for key in ("read_s", "simplify_s", "export_s", "wall_s") if key in report))
    if float(report.get("simplify_s") or 0) + float(report.get("export_s") or 0) < _QUICK_PAGE_S:
        _quick_call = weakref.ref(context)
    return success


def _draw(job: dict, child: _Child, budget_s: float) -> None:

    try:
        child.report = layout_child.run(job, child.stop.is_set, budget_s)
    except Exception as exc:  # noqa: BLE001
        child.report = {"ok": False, "error": str(exc)}


def _plan(method: str, args: tuple, kwargs: dict):


    exporter = args[0] if args else None
    if not isinstance(exporter, QgsLayoutExporter):
        return None
    rest = args[1:]
    if len(rest) > 2 or set(kwargs) - {"filePath", "settings"}:
        return None
    path = rest[0] if rest else kwargs.get("filePath")
    settings = rest[1] if len(rest) > 1 else kwargs.get("settings")
    if not isinstance(path, str) or not path.strip():
        return None
    if not isinstance(settings, getattr(QgsLayoutExporter, _SETTINGS[method])):
        return None
    context = code_processing.call_context()
    if context is None or code_clock.clock_ran_out():
        return None
    if _quick_call is not None and _quick_call() is context:
        return None
    layout = exporter.layout()
    project = QgsProject.instance()
    if not isinstance(layout, QgsPrintLayout) or layout.project() is not project:
        return None
    target = os.path.abspath(path)
    if not os.path.isdir(os.path.dirname(target)):
        return None
    fmt = "pdf" if method == "exportToPdf" else os.path.splitext(target)[1][1:].lower()
    fmt = {"jpeg": "jpg", "tiff": "tif"}.get(fmt, fmt)
    asked = {"format": fmt, "output_path": target, "georeference": bool(getattr(settings, "generateWorldFile", False))}
    if layout_child.why_not(asked, layout):
        return None
    changed = _changed(settings)
    if changed is None:
        return None
    dpi = float(changed.get("dpi") or 0)
    if dpi <= 0:
        dpi = float(layout.renderContext().dpi())
    changed["dpi"] = dpi
    copy = layout_child.write_copy()
    folder = os.path.dirname(copy)
    job = {"project_path": copy, "layout_name": layout.name(), "format": fmt,
           "output_path": os.path.join(folder, "page." + fmt), "dpi": int(round(dpi)),
           "export_settings": changed, "settings": layout_child.settings()}
    if changed.get("simplifyGeometries") is False:
        job["simplify_for_print"] = False
    if project.layoutManager().layoutByName(layout.name()) is not layout:

        document = QDomDocument("layout")
        document.appendChild(layout.writeXml(document, QgsReadWriteContext()))
        job["layout_xml"] = os.path.join(folder, LAYOUT_FILE)
        with open(job["layout_xml"], "w", encoding="utf-8") as handle:
            handle.write(document.toString())
    return job, target, context


def _changed(settings) -> dict | None:



    default = type(settings)()
    out: dict = {}
    for name in dir(settings):
        if name.startswith("_"):
            continue
        value = getattr(settings, name)
        if callable(value):
            continue
        base = getattr(default, name)
        if value == base:
            continue
        if type(value) in (bool, int, float):
            out[name] = value
            continue
        if type(value) is not type(base):
            return None
        try:
            out[name] = {"enum": int(getattr(value, "value", value))}
        except (TypeError, ValueError):
            return None
    return out
