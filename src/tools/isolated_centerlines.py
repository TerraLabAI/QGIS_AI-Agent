# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Crash-contained polygon centerlines through the installed QGIS provider."""






from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess  # nosec B404 - fixed QGIS interpreter and bundled worker only
import sys
import time
from contextlib import closing
from typing import Any

from ..core import layer_order
from ..core.logger import log_warning
from ..core.policy import create_managed_temp_dir
from ..core.qt_compat import enum_member
from ..core.security import validate_path
from ..core.snapshot_files import sqlite_read_only_uri
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .layer_lookup import _find_layer, _layer_not_found_error

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_WORKER = os.path.join(_PLUGIN_ROOT, "src", "workers", "isolated_centerlines_worker.py")
_GPKG_PIPE_RE = re.compile(r"^(?P<path>.+\.gpkg)(?:\|layername=(?P<table>[^|]+))?(?:\|.*)?$", re.IGNORECASE)
_GPKG_OGR_RE = re.compile(
    r"^ogr:dbname='(?P<path>[^']+\.gpkg)'\s+table=\"(?P<table>[^\"]+)\"",
    re.IGNORECASE,
)
_MAX_FEATURES = 10_000
_MAX_SECONDS = 30 * 60


def _gpkg_source(source: str) -> tuple[str, str] | None:
    """Return the local GeoPackage path and optional table from a QGIS URI."""
    text = str(source or "").strip()
    if not text or "://" in text or text.startswith(("memory:", "/vsi")):
        return None
    match = _GPKG_OGR_RE.match(text) or _GPKG_PIPE_RE.match(text)
    if not match:
        return None
    path = os.path.abspath(os.path.expanduser(match.group("path")))
    return path, str(match.groupdict().get("table") or "")


def _gpkg_copy(layer) -> tuple[str, str] | None:
    """The layer written to a GeoPackage in a managed temp folder, as (path, table), or None."""
    from qgis.core import QgsCoordinateTransformContext, QgsProject, QgsVectorFileWriter

    path = os.path.join(create_managed_temp_dir("centerlines-input"), "input.gpkg")
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = "input"
    try:
        context = QgsProject.instance().transformContext()
    except Exception:  # noqa: BLE001 - a default context writes the same features
        context = QgsCoordinateTransformContext()
    try:
        written = QgsVectorFileWriter.writeAsVectorFormatV3(layer, path, context, options)
    except Exception as exc:  # noqa: BLE001 - the caller answers with the export route
        log_warning(f"centerlines: GeoPackage copy failed: {exc}")
        return None
    error = written[0] if isinstance(written, tuple) else written
    if error != enum_member(QgsVectorFileWriter, "WriterError", "NoError") or not os.path.isfile(path):
        log_warning(f"centerlines: GeoPackage copy failed: {written}")
        return None
    return path, "input"


def _auto_spacing(layer, count: int) -> float:
    """A boundary spacing in metres when the call gave none."""






    from qgis.core import QgsDistanceArea, QgsPointXY, QgsProject

    fallback = 10.0
    try:
        extent = layer.extent()
        measure = QgsDistanceArea()
        measure.setSourceCrs(layer.crs(), QgsProject.instance().transformContext())
        measure.setEllipsoid("EPSG:7030")
        diagonal = measure.measureLine(QgsPointXY(extent.xMinimum(), extent.yMinimum()),
                                       QgsPointXY(extent.xMaximum(), extent.yMaximum()))
    except Exception:  # noqa: BLE001 - a layer that cannot be measured gets the middle value
        return fallback
    if not diagonal or diagonal <= 0:
        return fallback
    size = diagonal / max(1.0, float(count) ** 0.5)
    return round(max(1.0, min(50.0, size / 30.0)), 1)


def _worker_outcome(returncode: int, status: dict, output_path: str, log_tail: str) -> dict:
    """Turn a child exit and its result file into one fail-closed outcome."""
    reported_error = isinstance(status, dict) and status.get("ok") is False and status.get("error")
    if returncode != 0 and not reported_error:
        detail = f" The isolated log ended with: {log_tail}" if log_tail else ""
        return tool_error(
            f"The isolated QGIS centerline process stopped with exit code {returncode}.{detail}",
            "ISOLATED_PROCESS_CRASH",
            "The main QGIS stayed protected and the source layer was not changed. "
            "Do not retry unchanged; reduce the input or inspect the isolated log.",
        )
    if not isinstance(status, dict) or not status.get("ok"):
        detail = str((status or {}).get("error") or log_tail or "no result was reported")
        return tool_error(
            f"The isolated centerline calculation failed: {detail}",
            "EXECUTION_FAILED",
            "The source layer was not changed. Check that the Geometric Attributes provider is installed.",
        )
    try:
        feature_count = int(status.get("feature_count") or 0)
    except (TypeError, ValueError):
        feature_count = 0
    if feature_count < 1:
        return tool_error(
            "The isolated calculation produced no centerline features.",
            "EMPTY_OUTPUT",
            "Retry with method 0 and a densify_distance near a thirtieth of a feature's width in metres "
            "(5 to 20 for city blocks); a smaller set of valid polygons also helps.",
        )
    if not os.path.isfile(output_path):
        return tool_error(
            "The isolated calculation reported success but wrote no output file.",
            "MISSING_OUTPUT",
            "The source layer was not changed. Inspect the isolated log before retrying.",
        )
    return {
        "output_path": output_path,
        "feature_count": feature_count,
        "output_crs": str(status.get("output_crs") or ""),
        "empty_count": int(status.get("empty_count") or 0),
        "invalid_count": int(status.get("invalid_count") or 0),
    }


def _read_only_uri(path: str) -> str:
    """A read-only SQLite URI for a local file, whatever characters its path holds."""







    return sqlite_read_only_uri(path)


def _sqlite_snapshot(source: str, destination: str) -> None:
    """Make a transactionally consistent copy while QGIS keeps the source open."""





    with closing(sqlite3.connect(_read_only_uri(source), uri=True)) as source_db, \
            closing(sqlite3.connect(destination)) as destination_db:
        source_db.backup(destination_db)


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _tail(path: str, limit: int = 1_500) -> str:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - limit))
            return " ".join(handle.read().decode("utf-8", "replace").split())[-limit:]
    except OSError:
        return ""


def usable_python(path: str) -> bool:
    """A real interpreter file, never the Microsoft Store alias."""






    if not path or not os.path.isfile(path):
        return False
    parts = os.path.abspath(path).lower().replace("\\", "/").split("/")
    return "windowsapps" not in parts


def _qgis_python() -> str:
    """Locate the Python interpreter shipped beside the running QGIS."""
    from qgis.core import QgsApplication

    versioned = f"python{sys.version_info.major}.{sys.version_info.minor}"
    app_dir = QgsApplication.applicationDirPath()
    candidates = [
        sys.executable,
        os.path.join(app_dir, versioned),
        os.path.join(app_dir, "python3"),
        os.path.join(app_dir, "python.exe"),



        os.path.join(sys.prefix, "python.exe"),
        os.path.join(sys.exec_prefix, "python.exe"),
    ]
    for candidate in candidates:
        if usable_python(candidate) and os.path.basename(candidate).lower().startswith("python"):
            return candidate
    for name in (versioned, "python3", "python"):
        found = shutil.which(name)
        if usable_python(found):
            return found
    return ""


def _child_environment(python_executable: str, work_dir: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "QT_QPA_PLATFORM": "offscreen",
        "QGIS_CUSTOM_CONFIG_PATH": os.path.join(work_dir, "qgis-profile"),
        "QGIS_AI_AGENT_HOME": os.path.join(work_dir, "agent-home"),
        "TMPDIR": work_dir,
        "TMP": work_dir,
        "TEMP": work_dir,



        "PYTHONIOENCODING": "utf-8",
    })
    marker = f"{os.sep}Contents{os.sep}MacOS{os.sep}"
    if marker in python_executable:
        contents = python_executable.split(marker, 1)[0] + f"{os.sep}Contents"
        frameworks = os.path.join(contents, "Frameworks")
        qgis_resources = os.path.join(contents, "Resources", "qgis")
        env.update({
            "PYTHONHOME": frameworks,
            "PYTHONNOUSERSITE": "1",
            "DYLD_FRAMEWORK_PATH": frameworks,
            "PROJ_DATA": os.path.join(qgis_resources, "proj"),
            "PYTHONPATH": os.path.join(qgis_resources, "python", "plugins"),
        })
    elif os.name == "nt":



        try:
            import qgis

            qgis_python = os.path.dirname(os.path.dirname(os.path.abspath(qgis.__file__)))
        except Exception:  # noqa: BLE001 - no QGIS here: the child reports its own import error
            qgis_python = ""
        if qgis_python:
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = os.pathsep.join([qgis_python] + ([existing] if existing else []))
    return env


def _run_child(job: dict, task) -> dict:
    work_dir = job["work_dir"]
    source_copy = os.path.join(work_dir, "input-copy.gpkg")
    output_path = os.path.join(work_dir, "centerlines.gpkg")
    job_path = os.path.join(work_dir, "job.json")
    status_path = os.path.join(work_dir, "status.json")
    log_path = os.path.join(work_dir, "worker.log")

    _sqlite_snapshot(job["source_path"], source_copy)
    child_job = dict(job, source_path=source_copy, output_path=output_path)
    with open(job_path, "w", encoding="utf-8") as handle:
        json.dump(child_job, handle, ensure_ascii=False)

    python_executable = _qgis_python()
    if not python_executable:
        return tool_error(
            "The Python interpreter shipped with QGIS could not be found.",
            "QGIS_PYTHON_NOT_FOUND",
            "Repair the QGIS installation before running isolated centerlines.",
        )
    command = [python_executable, "-s", _WORKER, job_path, status_path]


    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} \
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW") else {}
    started = time.monotonic()
    with open(log_path, "wb") as log_handle:
        process = subprocess.Popen(  # nosec B603 - fixed interpreter and bundled worker
            command,
            cwd=work_dir,
            env=_child_environment(python_executable, work_dir),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            **kwargs,
        )
        while process.poll() is None:
            if task.isCanceled():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                return tool_error("The isolated centerline calculation was canceled.", "CANCELED")
            if time.monotonic() - started > _MAX_SECONDS:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                return tool_error(
                    "The isolated centerline calculation exceeded 30 minutes and was stopped.",
                    "TIMEOUT",
                    "Reduce the input layer or select a smaller area before retrying.",
                )
            time.sleep(0.2)
        return _worker_outcome(process.returncode, _read_json(status_path), output_path, _tail(log_path))


def _input_job(args: dict) -> dict:
    from qgis.core import QgsVectorLayer, QgsWkbTypes

    layer_name = str(args.get("layer_name") or "").strip()
    layer = _find_layer(layer_name)
    if layer is None:
        return _layer_not_found_error(layer_name)
    polygon_type = enum_member(QgsWkbTypes, "GeometryType", "PolygonGeometry")
    if not isinstance(layer, QgsVectorLayer) or QgsWkbTypes.geometryType(layer.wkbType()) != polygon_type:
        return tool_error("Centerlines require a polygon vector layer.", "INVALID_ARGS")


    provider_dir = os.path.join(os.path.dirname(_PLUGIN_ROOT), "geometric_attributes")
    if not os.path.isfile(os.path.join(provider_dir, "Centerlines.py")):
        return tool_error(
            "The Geometric Attributes QGIS plugin is not installed in this profile.",
            "PROVIDER_NOT_INSTALLED",
            "Install and enable Geometric Attributes, then restart QGIS.",
        )
    if layer.isModified():
        return tool_error(
            "The input layer has unsaved edits, so its on-disk snapshot would be incomplete.",
            "INVALID_ARGS",
            "Save or roll back the layer edits, then call create_polygon_centerlines again.",
        )
    count = int(layer.featureCount())
    if count < 1:
        return tool_error("The polygon layer is empty.", "EMPTY_INPUT")
    if count > _MAX_FEATURES and not bool(args.get("confirm_large")):
        return tool_error(
            f"The layer has {count:,} polygons; safe centerlines are limited to "
            f"{_MAX_FEATURES:,} without confirmation.",
            "CONFIRM_REQUIRED",
            "Clip or filter the layer first, or ask the user and retry with confirm_large true.",
        )
    source = _gpkg_source(layer.source())
    if source is None:



        source = _gpkg_copy(layer)
        if source is None:
            return tool_error(
                "The polygon layer could not be copied to a GeoPackage for the isolated calculation.",
                "EXECUTION_FAILED",
                "Export it with export_layer to a .gpkg, load that file, then call create_polygon_centerlines on it.",
            )
    source_path, table = source
    path_error = validate_path(source_path, write=False)
    if path_error or not os.path.isfile(source_path):
        return tool_error(path_error or f"Input GeoPackage not found: {source_path}", "INVALID_ARGS")
    work_dir = create_managed_temp_dir("isolated-centerlines")
    return {
        "work_dir": work_dir,
        "source_path": source_path,
        "source_table": table,
        "provider_dir": provider_dir,
        "method": int(args.get("method", 0) or 0),
        "trim_iterations": float(args.get("trim_iterations", 0) or 0),
        "simplify_distance": float(args.get("simplify_distance", 0) or 0),
        "densify_distance": float(args.get("densify_distance", 0) or 0) or _auto_spacing(layer, count),
        "output_name": str(args.get("output_name") or "Polygon centerlines"),
        "input_feature_count": count,
    }


def _task_response(task_id: str) -> dict:
    return {
        "task_id": task_id,
        "status": "running",
        "algorithm": "create_polygon_centerlines",
        "note": "Running in a separate QGIS process. The source and main QGIS stay protected.",
        "poll": {
            "tool": "get_task_status",
            "args": {"task_id": task_id},
            "interval_s": 1.0,
            "label": "Creating protected polygon centerlines",
        },
    }


def _start_centerlines(args: dict) -> dict:
    from qgis.core import QgsTask

    from .processing_tools import _process_outputs, register_task

    job = _input_job(args)
    if job.get("_error"):
        return job
    flags = enum_member(QgsTask, "Flag", "CanCancel", getattr(QgsTask, "CanCancel", 0))

    class IsolatedCenterlineTask(QgsTask):
        def __init__(self):
            super().__init__("AI Agent: protected polygon centerlines", flags)
            self.outcome: dict[str, Any] = {}
            self.entry: dict[str, Any] | None = None
            self.run_token = layer_order.current_run()

        def run(self) -> bool:
            self.outcome = _run_child(job, self)
            return True

        def finished(self, _ok: bool) -> None:
            entry = self.entry
            if entry is None or entry.get("status") == "canceled":
                return
            if self.outcome.get("_error"):
                entry["status"] = "error"
                entry["error"] = self.outcome["_error"]
                entry["code"] = self.outcome.get("code")
                entry["suggestion"] = self.outcome.get("suggestion")
            else:
                try:
                    with layer_order.adopted(self.run_token):
                        entry["outputs"] = _process_outputs(
                            {"OUTPUT": self.outcome["output_path"]},
                            output_name=job["output_name"],
                        )
                    entry["status"] = "complete"
                    entry["progress"] = 100
                    entry["source_unchanged"] = True
                    entry["isolated_process"] = True
                    entry["feature_count"] = self.outcome["feature_count"]
                    entry["output_crs"] = self.outcome.get("output_crs")
                    entry["empty_count"] = self.outcome.get("empty_count", 0)
                    entry["invalid_count"] = self.outcome.get("invalid_count", 0)
                except Exception as exc:  # noqa: BLE001 - never cross the Qt completion boundary
                    entry["status"] = "error"
                    entry["error"] = f"The centerlines were written but could not be loaded: {exc}"
            entry.pop("task", None)

    task = IsolatedCenterlineTask()

    def connect(_task_id: str, entry: dict) -> None:
        task.entry = entry

    task_id, _entry = register_task(task, "create_polygon_centerlines", connect=connect)
    return _task_response(task_id)


def register_isolated_centerline_tools(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="create_polygon_centerlines",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "method": {"type": "integer", "minimum": 0, "maximum": 2, "default": 0},
                "trim_iterations": {"type": "number", "minimum": 0, "default": 0},
                "simplify_distance": {"type": "number", "minimum": 0, "default": 0},
                "densify_distance": {"type": "number", "minimum": 0, "default": 0},
                "output_name": {"type": "string", "default": "Polygon centerlines"},
                "confirm_large": {"type": "boolean", "default": False},
            },
            "required": ["layer_name"],
        },
        handler=_start_centerlines,
    ))
