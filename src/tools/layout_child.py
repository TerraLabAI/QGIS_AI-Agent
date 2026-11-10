# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

















from __future__ import annotations

import json
import os
import subprocess  # nosec B404
import time

from qgis.core import QgsProject, QgsSettings

from ..core.host_platform import IS_WINDOWS
from ..core.policy import create_managed_temp_dir
from ..core.snapshot_project import _paths_absolute, _signals_blocked
from .child_python import environment as _environment
from .child_python import python_for_child, start_refused

_WORKER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "workers",
                       "layout_export_worker.py")
_PROVIDERS = frozenset({"ogr", "gdal", "delimitedtext", "spatialite", "gpx", "wms"})
_FORMATS = frozenset({"pdf", "svg", "png", "jpg", "tif"})


_SETTING_PREFIXES = ("svg/", "proxy/", "qgis/networkAndProxy/")
_POLL_S = 0.2
COPY_FILE = "export.qgs"


def _drawn_layers(layout) -> list | None:

    from qgis.core import QgsLayoutItemMap

    project = QgsProject.instance()
    layers: dict = {}
    for item in layout.items():
        if not isinstance(item, QgsLayoutItemMap):
            continue
        if item.keepLayerSet():
            found = item.layers()
        elif item.followVisibilityPreset():
            found = project.mapThemeCollection().mapThemeVisibleLayers(item.followVisibilityPresetName())
        else:
            found = project.layerTreeRoot().checkedLayers()
        if getattr(item, "dataDefinedProperties", None) is not None:
            from qgis.core import QgsLayoutObject

            properties = item.dataDefinedProperties()
            for key in ("MapLayers", "MapStylePreset"):
                prop = getattr(QgsLayoutObject, key, None)
                if prop is not None and properties.isActive(prop):
                    return None
        for layer in found:
            if layer is not None:
                layers[layer.id()] = layer
    return list(layers.values())


def _travels(layer) -> bool:

    from qgis.core import QgsVectorLayer

    try:
        if not layer.isValid() or layer.providerType() not in _PROVIDERS:
            return False
        source = str(layer.source() or "")
        if "authcfg=" in source or "password=" in source.lower():
            return False
        if isinstance(layer, QgsVectorLayer):
            if layer.isEditable() and layer.isModified():
                return False
            if layer.auxiliaryLayer() is not None:
                return False
        return True
    except (RuntimeError, AttributeError):
        return False


def why_not(args: dict, layout) -> str:


    fmt = str(args.get("format") or os.path.splitext(str(args.get("output_path") or ""))[1][1:]).lower()
    fmt = {"jpeg": "jpg", "tiff": "tif"}.get(fmt, fmt)
    if fmt not in _FORMATS:
        return "format"
    if fmt != "pdf" and (args.get("georeference") or args.get("meters_per_pixel") is not None):
        return "world file"
    if layout is None or getattr(layout, "pageCollection", None) is None:
        return "report"
    if fmt != "pdf" and layout.pageCollection().pageCount() > 1:
        return "pages"
    atlas = getattr(layout, "atlas", None)
    if callable(atlas) and atlas() is not None and atlas().enabled():
        return "atlas"
    layers = _drawn_layers(layout)
    if layers is None:
        return "map layers by expression"
    for layer in layers:
        if not _travels(layer):
            return f"layer {layer.name()}"
    if not python_for_child():
        return "no QGIS Python"
    return ""


def separate(args: dict) -> bool:

    if not isinstance(args, dict) or not args.get("layout_name") or not args.get("output_path"):
        return False
    layout = QgsProject.instance().layoutManager().layoutByName(str(args["layout_name"]))
    return not why_not(args, layout)


def write_copy() -> str:






    project = QgsProject.instance()
    folder = create_managed_temp_dir("layout-export-")
    path = os.path.join(folder, COPY_FILE)
    name, dirty = project.fileName() or "", project.isDirty()
    try:
        with _signals_blocked(project), _paths_absolute(project):
            ok = bool(project.write(path))
    finally:
        project.setFileName(name)
        project.setDirty(dirty)
    if not ok:
        raise OSError(project.error() or "QGIS gave no reason")
    return path


SECRET_SETTINGS = ("proxy/proxyUser", "proxy/proxyPassword")
SECRET_SETTINGS_ENV = "QGIS_AI_AGENT_LAYOUT_SETTINGS"  # nosec B105  # pragma: allowlist secret


def settings() -> dict:

    store = QgsSettings()
    return {key: store.value(key) for key in store.allKeys() if key.startswith(_SETTING_PREFIXES)}


def run(job: dict, stopped, budget_s: float) -> dict:


    started = time.monotonic()
    python = python_for_child()
    folder = os.path.dirname(job["project_path"])
    job_path, status_path, log_path = (os.path.join(folder, name) for name in ("job.json", "status.json",
                                                                               "worker.log"))

    held = {key: job["settings"].pop(key) for key in SECRET_SETTINGS if key in job.get("settings", {})}
    environment = _environment(python, folder)
    if held:
        environment[SECRET_SETTINGS_ENV] = json.dumps(held)
    with open(job_path, "w", encoding="utf-8") as handle:
        json.dump(job, handle, ensure_ascii=False)
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                   | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
    else:
        kwargs["start_new_session"] = True
    with open(log_path, "wb") as log_handle:
        try:
            process = subprocess.Popen(  # nosec B603
                [python, "-s", _WORKER, job_path, status_path],
                cwd=folder, env=environment, stdin=subprocess.DEVNULL,
                stdout=log_handle, stderr=subprocess.STDOUT, **kwargs)
        except OSError as exc:

            return {"ok": False, "error": start_refused(exc), "wall_s": round(time.monotonic() - started, 2)}
        while process.poll() is None:
            if (stopped is not None and stopped()) or time.monotonic() - started > budget_s:
                process.kill()
                process.wait(timeout=5)
                return {"ok": False, "stopped": bool(stopped is not None and stopped()),
                        "error": "stopped" if stopped is not None and stopped()
                        else f"no page after {budget_s:.0f} s", "wall_s": round(time.monotonic() - started, 2)}
            time.sleep(_POLL_S)
    try:
        with open(status_path, encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, ValueError):
        try:
            with open(log_path, "rb") as handle:
                tail = handle.read()[-800:].decode("utf-8", "replace")
        except OSError:
            tail = ""
        report = {"ok": False, "error": "the separate QGIS ended without a result"
                                        + (f": {' '.join(tail.split())}" if tail else "")}
    report["wall_s"] = round(time.monotonic() - started, 2)
    return report
