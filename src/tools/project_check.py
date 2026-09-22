# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later














from __future__ import annotations

import json
import os
import subprocess  # nosec B404
import time

from ..core import tuning
from ..core.host_platform import IS_WINDOWS
from ..core.policy import create_managed_temp_dir
from ..core.security import validate_path
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .isolated_centerlines import _child_environment, _qgis_python, _read_json, _tail

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_WORKER = os.path.join(_PLUGIN_ROOT, "src", "workers", "project_check_worker.py")



_MAX_SECONDS = 180


def _verdict(report: dict, path: str) -> dict:

    broken = list(report.get("broken_layers") or ())
    outside = [item for layout in report.get("layouts") or ()
               for item in (layout.get("items_outside_page") or ())]
    out = {"project": path, "reopened": True, **{key: value for key, value in report.items()
                                                 if key != "ok"}}
    if broken:
        out["verdict"] = "problem"
        out["note"] = ("Reopened in a separate QGIS, and " + ", ".join(str(name) for name in broken[:6])
                       + " did not come back: the layer is invalid or its file is not where the project "
                         "says it is. Data that lives only in this QGIS session (a memory layer, a "
                         "scratch layer) is gone the moment the project is reopened, so write it to a "
                         "file with export_file and save again.")
    elif outside:
        out["verdict"] = "problem"
        out["note"] = ("Every layer came back, but " + ", ".join(str(item) for item in outside[:6])
                       + " sits off the page in a layout and is cut on export.")
    else:
        out["verdict"] = "ok"
        out["note"] = (f"Reopened in a separate QGIS: {report.get('layer_count', 0)} layer(s) valid with "
                       f"their sources found, {report.get('layout_count', 0)} layout(s). The user will "
                       "see this project as it is now.")
    return out


def _run_child(path: str, stopped, python_executable: str) -> dict:
    work_dir = create_managed_temp_dir("projectcheck-")
    job_path = os.path.join(work_dir, "job.json")
    status_path = os.path.join(work_dir, "status.json")
    log_path = os.path.join(work_dir, "worker.log")
    with open(job_path, "w", encoding="utf-8") as handle:


        json.dump({"project_path": path, "plugin_root": _PLUGIN_ROOT,
                   "max_layers": tuning.ceiling("project_check_max_layers", 200, 20),
                   "max_layouts": tuning.ceiling("project_check_max_layouts", 20, 2)},
                  handle, ensure_ascii=False)

    if not python_executable:
        return tool_error(
            "The Python interpreter shipped with QGIS could not be found, so the saved project "
            "cannot be opened anywhere else.",
            "QGIS_PYTHON_NOT_FOUND",
            "The file was still written. Open it yourself, or check it after restarting QGIS.")
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} \
        if IS_WINDOWS and hasattr(subprocess, "CREATE_NO_WINDOW") else {}
    started = time.monotonic()
    with open(log_path, "wb") as log_handle:
        process = subprocess.Popen(  # nosec B603
            [python_executable, "-s", _WORKER, job_path, status_path],
            cwd=work_dir,
            env=_child_environment(python_executable, work_dir),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            **kwargs,
        )
        while process.poll() is None:
            if stopped is not None and stopped():
                process.terminate()
                return tool_error("The check was stopped.", "CANCELLED")
            if time.monotonic() - started > _MAX_SECONDS:
                process.terminate()
                return tool_error(
                    f"The separate QGIS did not finish reading the project in {_MAX_SECONDS} s.",
                    "TIMEOUT",
                    "The file was still written. A project this slow to open is usually waiting on a "
                    "remote layer: check the sources with list_layers.")
            time.sleep(0.2)

    report = _read_json(status_path)
    if not report.get("ok"):
        detail = str(report.get("error") or _tail(log_path) or "no result was reported")


        return tool_error(
            f"A separate QGIS could not reopen {os.path.basename(path)}: {detail}",
            "PROJECT_DOES_NOT_REOPEN",
            "Your own QGIS is untouched and the file is still on disk. Check the layer sources with "
            "list_layers, write any scratch layer to a file with export_file, and save again.")
    return _verdict(report, path)


def _check_saved_project(args: dict) -> dict:

    from ..core import net
    from ..core.background import run_on_main_thread

    path = str(args.get("path") or "").strip()
    if not path:
        path = run_on_main_thread(_open_project_file, timeout=10)
        if not path:
            return tool_error(
                "This QGIS has no saved project to reopen.", "INVALID_ARGS",
                "Save it first with save_project, then check that file.")
    path_error = validate_path(path)
    if path_error:
        return {"_error": path_error, "_code": "INVALID_ARGS"}
    if not os.path.isfile(path):
        return tool_error(f"No project file at {path}.", "INVALID_ARGS",
                          "save_project answers with the path it wrote.")

    python_executable = run_on_main_thread(_qgis_python, timeout=10)
    return _run_child(path, net.current_cancel_check(), python_executable)


def _open_project_file() -> str:
    from qgis.core import QgsProject

    return str(QgsProject.instance().fileName() or "")


def register_project_check_tools(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="check_saved_project",
        danger="read",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": [],
        },
        handler=_check_saved_project,
        background=True,
    ))
