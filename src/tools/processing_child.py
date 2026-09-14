# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

































from __future__ import annotations

import json
import os
import subprocess  # nosec B404
import time

from qgis.core import (
    QgsMapLayer,
    QgsProcessingFeatureSourceDefinition,
    QgsProject,
    QgsSettings,
    QgsTask,
)

from ..core.host_platform import IS_WINDOWS
from ..core.logger import log_warning
from ..core.policy import create_managed_temp_dir
from ..core.qt_compat import enum_member

_WORKER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "workers",
                       "processing_worker.py")


_PLAIN_TYPES = frozenset({
    "number", "distance", "area", "volume", "duration", "scale", "string", "boolean", "enum",
    "field", "band", "expression", "crs", "extent", "point", "geometry", "range", "matrix",
    "file", "color", "datetime", "aggregates", "fields_mapping", "fieldmapping", "execute_sql",
    "coordinateoperation", "rasteroptions", "alignrasterlayers",
})

_LAYER_TYPES = frozenset({"source", "raster", "multilayer", "mesh", "pointcloud"})
_LAYER_OUTPUTS = frozenset({"outputVector", "outputRaster", "outputLayer", "outputMultilayers",
                            "outputMesh", "outputPointCloud", "outputVectorTile"})
_FILE_PROVIDERS = frozenset({"ogr", "gdal"})
_POLL_S = 0.2
_STOP_GRACE_S = 2.0


def child_capable(alg) -> bool:

    try:
        destinations = set()
        for definition in alg.parameterDefinitions():
            if definition.isDestination():
                destinations.add(definition.name())
            elif definition.type() not in _PLAIN_TYPES | _LAYER_TYPES:
                return False
        if not destinations:
            return False
        return all(output.name() in destinations or output.type() not in _LAYER_OUTPUTS
                   for output in alg.outputDefinitions())
    except Exception:  # noqa: BLE001
        return False


def _layer_source(value):

    if isinstance(value, QgsProcessingFeatureSourceDefinition):
        return None
    layer = value if isinstance(value, QgsMapLayer) else None
    if layer is None and isinstance(value, str):
        project = QgsProject.instance()
        layer = project.mapLayer(value)
        if layer is None:
            found = project.mapLayersByName(value)
            layer = found[0] if len(found) == 1 else None
        if layer is None:
            path = value.split("|", 1)[0]
            return value if path.startswith("/vsi") or os.path.exists(path) else None
    if layer is None or not layer.isValid() or layer.providerType() not in _FILE_PROVIDERS:
        return None
    if getattr(layer, "isModified", lambda: False)():
        return None
    source = layer.source()
    path = source.split("|", 1)[0]
    return source if path.startswith("/vsi") or os.path.exists(path) else None


def _plain(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        items = [_plain(item) for item in value]
        return _NOT_PLAIN if any(item is _NOT_PLAIN for item in items) else items
    authid = getattr(value, "authid", None)
    if callable(authid):
        return value.authid() or value.toWkt()
    return _NOT_PLAIN


_NOT_PLAIN = object()


def _settings() -> dict:

    out = {}
    settings = QgsSettings()
    for key in settings.allKeys():
        if not key.startswith("Processing/Configuration/"):
            continue
        value = settings.value(key)
        if isinstance(value, (bool, int, float, str)):
            out[key] = value
    return out


def _plugin_of(alg) -> list[dict]:

    import sys

    provider = alg.provider()
    package = (type(provider).__module__ if provider is not None else "").split(".", 1)[0]
    return [{"package": package}] if hasattr(sys.modules.get(package), "classFactory") else []


def plan(alg, algorithm_id: str, parameters: dict, context) -> dict | None:

    if hasattr(alg, "childAlgorithms") or not child_capable(alg):
        return None
    job_parameters = {}
    try:
        for definition in alg.parameterDefinitions():
            name = definition.name()
            if name not in parameters:
                continue
            value = parameters[name]
            if definition.isDestination():
                if not isinstance(value, str) or not value or value.startswith("memory:"):
                    return None
                job_parameters[name] = value
            elif definition.type() in _LAYER_TYPES and value not in (None, ""):
                items = value if isinstance(value, (list, tuple)) else [value]
                sources = [_layer_source(item) for item in items]
                if any(source is None for source in sources):
                    return None
                job_parameters[name] = sources if isinstance(value, (list, tuple)) else sources[0]
            else:
                plain = _plain(value)
                if plain is _NOT_PLAIN:
                    return None
                job_parameters[name] = plain
        project = QgsProject.instance()
        provider = alg.provider()
        return {
            "algorithm_id": algorithm_id,
            "provider_id": provider.id() if provider is not None else "",
            "parameters": job_parameters,
            "plugins": _plugin_of(alg),
            "settings": _settings(),
            "project": {"crs": project.crs().authid() or project.crs().toWkt(),
                        "ellipsoid": project.ellipsoid()},
            "invalid_geometry_check": int(getattr(context.invalidGeometryCheck(), "value",
                                                  context.invalidGeometryCheck())),
        }
    except Exception as exc:  # noqa: BLE001
        log_warning(f"{algorithm_id}: not handed to a separate QGIS: {exc}")
        return None


def _end(process) -> None:

    if process.poll() is not None:
        return
    if IS_WINDOWS:
        system_root = os.environ.get("SYSTEMROOT", "C:\\Windows")
        taskkill = os.path.join(system_root, "System32", "taskkill.exe")
        try:
            subprocess.run([taskkill, "/T", "/F", "/PID", str(process.pid)],  # nosec B603
                           capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError) as exc:
            log_warning(f"Processing child tree not ended by taskkill: {exc}")
    else:
        import signal

        for sig, wait in ((signal.SIGTERM, _STOP_GRACE_S), (signal.SIGKILL, 2.0)):
            try:
                os.killpg(process.pid, sig)
            except OSError:
                break
            try:
                process.wait(timeout=wait)
                return
            except subprocess.TimeoutExpired:
                continue
    try:
        process.kill()
        process.wait(timeout=2)
    except (OSError, subprocess.SubprocessError):
        pass


class ChildRun(QgsTask):






    def __init__(self, description: str, job: dict, done):
        super().__init__(description, enum_member(QgsTask, "Flag", "CanCancel"))
        self.job, self.done, self.report = job, done, {}

    def run(self) -> bool:  # noqa: D401
        from .isolated_code import _environment, python_for_child

        started = time.monotonic()
        python = python_for_child()
        if not python:
            self.report = {"ok": False, "error": "The Python interpreter shipped with QGIS was not found, "
                                                 "so no separate QGIS could run the algorithm."}
            return False
        work_dir = create_managed_temp_dir("processing-child")
        paths = {name: os.path.join(work_dir, name) for name in ("job.json", "status.json", "progress.txt",
                                                                  "worker.log")}
        with open(paths["job.json"], "w", encoding="utf-8") as handle:
            json.dump(self.job, handle, ensure_ascii=False)
        kwargs = {}
        if IS_WINDOWS:
            kwargs["creationflags"] = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                       | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
        else:
            kwargs["start_new_session"] = True
        with open(paths["worker.log"], "wb") as log_handle:
            process = subprocess.Popen(  # nosec B603
                [python, "-s", _WORKER, paths["job.json"], paths["status.json"], paths["progress.txt"]],
                cwd=work_dir, env=_environment(python, work_dir), stdin=subprocess.DEVNULL,
                stdout=log_handle, stderr=subprocess.STDOUT, **kwargs)
            while process.poll() is None:
                if self.isCanceled():
                    _end(process)
                    self.report = {"ok": False, "error": "Stopped.", "work_dir": work_dir}
                    return False
                try:
                    with open(paths["progress.txt"], encoding="utf-8") as handle:

                        self.setProgress(min(100.0, max(0.0, float(handle.read() or 0))))
                except (OSError, ValueError):
                    pass
                time.sleep(_POLL_S)
        try:
            with open(paths["status.json"], encoding="utf-8") as handle:
                self.report = json.load(handle)
        except (OSError, ValueError):
            try:
                with open(paths["worker.log"], "rb") as handle:
                    tail = handle.read()[-1500:].decode("utf-8", "replace")
            except OSError:
                tail = ""
            self.report = {"ok": False, "error": "The separate QGIS ended without a result"
                                                 + (f": {' '.join(tail.split())}" if tail else ".")}
        if self.report.get("unavailable"):
            self.report["parameters"] = self.job["parameters"]
        self.report["wall_s"] = round(time.monotonic() - started, 2)
        self.report["work_dir"] = work_dir
        return bool(self.report.get("ok"))

    def finished(self, result: bool) -> None:
        try:
            self.done(bool(result), self.report)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Processing child result not handled: {exc}")
