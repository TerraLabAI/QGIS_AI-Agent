# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run one ``execute_code`` snippet in a disposable process."""






















from __future__ import annotations

import importlib
import json
import os
import sys
import traceback
import types

_APPLICATION = None
_TRAPPED: list[str] = []
_TRAPPED_LINES: list[int] = []


_SNIPPET_FILE = "<string>"
_QT_CORE_NAMES = ("QVariant", "Qt", "QSize", "QSizeF", "QPointF", "QPoint", "QRectF", "QRect", "QDate", "QTime",
                  "QDateTime", "QUrl")


class NeedsLiveQgis(BaseException):
    """Raised when the snippet tries to change the project it only has a copy of."""


class LayerCopyFailed(Exception):
    """Raised when a layer the parent sent full XML for cannot be rebuilt here."""






    def __init__(self, message: str, layer_id: str = ""):
        super().__init__(message)
        self.layer_id = layer_id


def _write(path: str, value: dict) -> None:
    temporary = path + ".partial"
    with open(temporary, "w", encoding="utf-8") as handle:

        json.dump(value, handle, ensure_ascii=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _load_guard(plugin_root: str):
    """The plugin's guard modules, imported without running any ``__init__.py``."""
    packages = {
        "_ai_agent_plugin": [plugin_root],
        "_ai_agent_plugin.src": [os.path.join(plugin_root, "src")],
        "_ai_agent_plugin.src.core": [os.path.join(plugin_root, "src", "core")],
    }
    for name, paths in packages.items():
        module = types.ModuleType(name)
        module.__path__ = paths
        sys.modules[name] = module
    guard = importlib.import_module("_ai_agent_plugin.src.core.code_guard")
    security = importlib.import_module("_ai_agent_plugin.src.core.security")
    code_split = importlib.import_module("_ai_agent_plugin.src.core.code_split")
    return guard, security, code_split


def _layer_from_xml(kind: str, xml_text: str):
    """The parent's layer rebuilt from its full definition, or None."""






    from qgis.core import QgsRasterLayer, QgsReadWriteContext, QgsVectorLayer
    from qgis.PyQt.QtXml import QDomDocument

    if not xml_text:
        return None
    document = QDomDocument()
    result = document.setContent(xml_text)
    parsed = result[0] if isinstance(result, tuple) else result
    if not parsed:
        return None
    if kind == "vector":
        layer = QgsVectorLayer()
    elif kind == "raster":
        layer = QgsRasterLayer()
    else:
        return None
    if not layer.readLayerXml(document.documentElement(), QgsReadWriteContext()):
        return None
    return layer


def _layer_from_description(entry: dict):
    """The plain (source, name, provider) build, kept for a job without XML."""





    from qgis.core import QgsCoordinateReferenceSystem, QgsRasterLayer, QgsVectorLayer

    kind = str(entry.get("kind") or "")
    source = str(entry.get("source") or "")
    name = str(entry.get("name") or "")
    provider = str(entry.get("provider") or "")
    if kind == "vector":
        layer = QgsVectorLayer(source, name, provider)
    elif kind == "raster":
        layer = QgsRasterLayer(source, name, provider)
    else:
        return None
    if not layer.isValid():
        return None
    layer_wkt = str(entry.get("crs_wkt") or "")
    if layer_wkt:
        layer_crs = QgsCoordinateReferenceSystem.fromWkt(layer_wkt)
        if layer_crs.isValid():
            layer.setCrs(layer_crs)
    if kind == "vector":
        subset = str(entry.get("subset") or "")
        if subset:
            layer.setSubsetString(subset)
    return layer


def _project_and_layers(job: dict) -> None:
    """Rebuild the parent's project settings and copy of each reached layer here."""









    from qgis.core import QgsCoordinateReferenceSystem, QgsProject, QgsUnitTypes

    project = QgsProject.instance()
    description = job.get("project") or {}
    crs_wkt = str(description.get("crs_wkt") or "")
    if crs_wkt:
        crs = QgsCoordinateReferenceSystem.fromWkt(crs_wkt)
        if crs.isValid():
            project.setCrs(crs)
    ellipsoid = str(description.get("ellipsoid") or "")
    if ellipsoid:
        project.setEllipsoid(ellipsoid)

    for key, decode, apply in (("distance_units", QgsUnitTypes.decodeDistanceUnit, project.setDistanceUnits),
                               ("area_units", QgsUnitTypes.decodeAreaUnit, project.setAreaUnits)):
        encoded = str(description.get(key) or "")
        if encoded:
            decoded = decode(encoded)
            unit, known = decoded if isinstance(decoded, tuple) else (decoded, True)
            if known:
                apply(unit)
    file_name = str(description.get("file_name") or "")
    if file_name:
        project.setFileName(file_name)
    home_path = str(description.get("home_path") or "")
    if home_path:
        project.setPresetHomePath(home_path)
    copied = []
    for entry in job.get("layers") or []:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind") or "")
        if "xml" in entry:
            layer = _layer_from_xml(kind, str(entry.get("xml") or ""))
            if layer is None or not layer.isValid():
                name = str(entry.get("name") or entry.get("id") or "layer")
                raise LayerCopyFailed(f"layer {name!r} could not be rebuilt from its definition",
                                      str(entry.get("id") or ""))
        else:
            layer = _layer_from_description(entry)
        if layer is None:
            continue
        if hasattr(layer, "setId") and entry.get("id"):
            layer.setId(str(entry["id"]))
        project.addMapLayer(layer)
        copied.append(layer)



    for layer in copied:
        layer.resolveReferences(project)


def _snippet_line() -> int:
    """The snippet line of the top-level statement running now, or 0."""





    line = 0


    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_code.co_filename == _SNIPPET_FILE and frame.f_code.co_name == "<module>":
            line = frame.f_lineno
        frame = frame.f_back
    return line


def _install_traps(code_split) -> None:
    """Make every project-changing method on the copied classes raise."""






    import qgis.core as core

    bases = [base for base in (getattr(core, name, None) for name in code_split.TRAP_BASES)
             if isinstance(base, type)]
    if not bases:
        return

    def make_trap(class_name: str, method_name: str):
        def trap(*_args, **_kwargs):
            _TRAPPED_LINES.append(_snippet_line())
            _TRAPPED.append(f"{class_name}.{method_name}")
            raise NeedsLiveQgis(f"{class_name}.{method_name} changes the project")
        return trap

    for value in vars(core).values():
        if not isinstance(value, type) or not any(issubclass(value, base) for base in bases):
            continue
        class_name = value.__name__
        for name, member in list(vars(value).items()):
            if name.startswith("_") or name in code_split.TRAP_KEEP:
                continue


            if not (callable(member) or type(member).__name__ == "methoddescriptor"):
                continue
            if not code_split.TRAP_RE.match(name):
                continue
            assignment_refused = False
            try:
                setattr(value, name, make_trap(class_name, name))
            except (AttributeError, TypeError):
                assignment_refused = True
            if assignment_refused:
                continue


def _run(job: dict, status_path: str) -> int:
    plugin_root = str(job["plugin_root"])
    guard, security, code_split = _load_guard(plugin_root)
    needs_qgis = bool(job.get("needs_qgis"))
    if needs_qgis:
        global _APPLICATION
        from qgis.core import QgsApplication, QgsProject

        _APPLICATION = QgsApplication([], False)
        _APPLICATION.initQgis()
        try:
            _project_and_layers(job)
        except LayerCopyFailed as exc:


            _write(status_path, {"phase": "done", "layer_copy_failed": str(exc), "layer_id": exc.layer_id})
            return 0


        QgsProject.instance().mapLayers()
        QgsProject.instance().mapLayersByName("")
        _install_traps(code_split)

    stdout_path = str(job["stdout_path"])
    sink = _Sink(stdout_path, int(job.get("max_output_chars") or 200_000))

    def _print(*args, **kwargs):
        kwargs.setdefault("file", sink)
        print(*args, **kwargs)

    namespace = {
        "__builtins__": guard.build_safe_builtins(),
        "print": _print,
        "read_text": security.safe_read_text,
        "validate_path": security.validate_path,
    }
    if needs_qgis:
        import qgis.core as core
        from qgis.PyQt import QtCore, QtGui

        for name, value in vars(core).items():
            if (name.startswith("Qgs") or name == "NULL") and name not in guard.DENIED_NAMES:
                namespace[name] = value
        namespace["project"] = QgsProject.instance()
        for module, names in ((QtCore, _QT_CORE_NAMES), (QtGui, ("QColor",))):
            for name in names:
                value = getattr(module, name, None)
                if value is not None and name not in guard.DENIED_NAMES:
                    namespace[name] = value

    _write(status_path, {"phase": "running"})
    code = str(job.get("code") or "")
    exception = None
    exception_traceback = ""
    try:
        exec(compile(code, _SNIPPET_FILE, "exec"), namespace)  # nosec B102 - execute_code, user-approved
    except BaseException as exc:  # noqa: BLE001 - the parent reads every failure from the status file
        exception = exc


        exception_traceback = traceback.format_exc()[-8_000:]
    finally:
        sink.flush()

    status: dict = {"phase": "done", "stdout_dropped": sink.dropped}
    if _TRAPPED:
        status.update({"ok": False, "needs_qgis": _TRAPPED[0], "needs_qgis_line": _TRAPPED_LINES[0]})
    elif exception is not None:
        status.update({
            "ok": False,
            "type": type(exception).__name__,
            "message": str(exception)[:2000],
            "traceback": exception_traceback,
        })
    else:
        status["ok"] = True
        status["result_set"] = "result" in namespace
        if status["result_set"]:
            text = str(namespace["result"])
            status["result"] = text[:int(job.get("max_result_chars") or 1_000_000)]
            status["result_chars"] = len(text)
    _write(status_path, status)
    return 0


class _Sink:
    """A text sink that keeps the first ``cap`` characters and counts the rest."""






    def __init__(self, path: str, cap: int):
        self._cap = max(0, int(cap))
        self._kept = 0


        self._handle = open(path, "w", encoding="utf-8", errors="backslashreplace")
        self.dropped = 0

    def write(self, text) -> int:
        text = str(text)
        room = self._cap - self._kept
        if room > 0:
            self._handle.write(text[:room])
            self._kept += min(len(text), room)
        self.dropped += max(0, len(text) - room)
        self._handle.flush()
        return len(text)

    def flush(self) -> None:
        self._handle.flush()


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        return 2
    job_path, status_path = argv[1:]
    try:
        with open(job_path, encoding="utf-8") as handle:
            job = json.load(handle)
    except BaseException as exc:  # noqa: BLE001 - a job that cannot be read is a setup failure
        _write(status_path, {
            "phase": "setup",
            "ok": False,
            "setup_error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-4_000:],
        })
        return 1
    job_file_left = False
    try:
        os.remove(job_path)
    except OSError:


        job_file_left = True  # noqa: F841 - bandit B110 rejects the bare pass this replaces
    try:
        return _run(job, status_path)
    except BaseException as exc:  # noqa: BLE001 - any setup failure is reported, never a bare process death
        _write(status_path, {
            "phase": "setup",
            "ok": False,
            "setup_error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-4_000:],
        })
        return 1


if __name__ == "__main__":
    code = main(sys.argv)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
