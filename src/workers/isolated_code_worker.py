# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





























from __future__ import annotations

import hashlib
import importlib
import json
import os
import pickle  # nosec B403
import sys
import tempfile
import time
import traceback
import types

_APPLICATION = None
_TRAPPED: list[str] = []
_TRAPPED_LINES: list[int] = []



_SNIPPET_FILE = "<snippet>"
_QT_CORE_NAMES = ("QVariant", "Qt", "QSize", "QSizeF", "QPointF", "QPoint", "QRectF", "QRect", "QDate", "QTime",
                  "QDateTime", "QUrl")


class NeedsLiveQgis(BaseException):
    pass


class LayerCopyFailed(Exception):







    def __init__(self, message: str, layer_id: str = ""):
        super().__init__(message)
        self.layer_id = layer_id


class _OnAnyFailure:







    def __init__(self, handle) -> None:
        self._handle = handle

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            return False
        self._handle(exc)
        return True


class _TooBig(Exception):
    pass


class _Measured:





    def __init__(self, handle, room: int):
        self._handle, self._room = handle, room
        self.size = 0
        self.digest = hashlib.sha1()  # nosec B324

    def write(self, data) -> int:
        self.size += len(data)
        if self.size > self._room:
            raise _TooBig
        self.digest.update(data)
        return self._handle.write(data)


def _module(name: str, importer):

    parent, _, leaf = name.rpartition(".")
    return getattr(importer(parent, None, None, (leaf,), 0), leaf) if parent else importer(name)


def _load_names(job: dict, namespace: dict) -> str:

    importer = namespace["__builtins__"]["__import__"]
    folder = str(job.get("names_dir") or "")
    for name, module in (job.get("modules") or {}).items():
        try:
            namespace[name] = _module(str(module), importer)
        except Exception:  # noqa: BLE001
            return str(name)
    for entry in job.get("names") or []:
        name = str(entry.get("name") or "")
        try:
            with open(os.path.join(folder, str(entry.get("file") or "")), "rb") as handle:
                namespace[name] = pickle.load(handle)  # nosec B301
        except Exception:  # noqa: BLE001
            return name
    return ""


def _names_out(job: dict, namespace: dict, bound: set, helpers) -> dict:

    folder = str(job.get("names_dir") or "")
    room = int(job.get("max_names_bytes") or 0)
    sent = {str(entry.get("name")): str(entry.get("sha1")) for entry in job.get("names") or []}
    sent.update({str(name): "" for name in job.get("modules") or {}})
    names, modules, not_kept, kinds, owned = [], {}, [], {}, []
    for index, (key, value) in enumerate(list(namespace.items())):
        if key.startswith("__") or key in bound or key in ("result", "show_files"):
            continue
        kinds[key] = type(value).__name__
        if not helpers.keepable(value):
            owned.append(key)
            continue
        if isinstance(value, types.ModuleType):
            modules[key] = value.__name__
            continue
        path = os.path.join(folder, f"out{index}.pkl")
        try:
            with open(path, "wb") as handle:
                sink = _Measured(handle, room)
                pickle.Pickler(sink, protocol=4).dump(value)
        except Exception:  # noqa: BLE001
            not_kept.append(key)
            continue
        if sent.get(key) == sink.digest.hexdigest():
            continue
        room -= sink.size
        names.append({"name": key, "file": os.path.basename(path)})
    deleted = [name for name in sent if name not in namespace]
    return {"names": names, "modules": modules, "not_kept": not_kept, "deleted": deleted,

            "lost": {name: kinds[name] for name in not_kept + owned}}


def _replace(source: str, target: str) -> None:


    for pause in (0.05, 0.1, 0.2, 0.3, 0.5, 0.85):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if sys.platform != "win32":
                raise
            time.sleep(pause)
    os.replace(source, target)


def _write(path: str, value: dict) -> None:
    temporary = path + ".partial"
    with open(temporary, "w", encoding="utf-8") as handle:

        json.dump(value, handle, ensure_ascii=True)
        handle.flush()
        os.fsync(handle.fileno())
    _replace(temporary, path)


def _load_guard(plugin_root: str):

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
    helpers = importlib.import_module("_ai_agent_plugin.src.core.code_namespace")
    return guard, security, code_split, helpers


def _tripwire():
    return importlib.import_module("_ai_agent_plugin.src.core.code_tripwire")


def _layer_from_xml(kind: str, xml_text: str):







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






    line = 0


    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_code.co_filename == _SNIPPET_FILE and frame.f_code.co_name == "<module>":
            line = frame.f_lineno
        frame = frame.f_back
    return line


def _install_traps(code_split) -> None:







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
    guard, security, code_split, helpers = _load_guard(plugin_root)

    security.set_workspace(str(job.get("workspace") or ""))

    security.set_read_roots(job.get("read_roots"))

    security.set_write_roots(job.get("write_roots"))
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
            if (name.startswith("Qgs") or name in ("NULL", "Qgis")) and name not in guard.DENIED_NAMES:
                namespace[name] = value
        namespace["project"] = QgsProject.instance()

        namespace["layer"] = helpers.layer_lookup(QgsProject.instance())
        namespace["layers"] = lambda: helpers.ordered_layers(QgsProject.instance())
        for module, qt_names in ((QtCore, _QT_CORE_NAMES), (QtGui, ("QColor",))):
            for name in qt_names:
                value = getattr(module, name, None)
                if value is not None and name not in guard.DENIED_NAMES:
                    namespace[name] = value
    for alias, module in helpers.prelude(code_split.names_mentioned(str(job.get("code") or "")),
                                         namespace["__builtins__"]["__import__"]).items():
        namespace[alias] = module
    bound = set(namespace)
    missing = _load_names(job, namespace)
    if missing:

        _write(status_path, {"phase": "done", "names_failed": missing})
        return 0




    guard.harden_gdal_config()
    _write(status_path, {"phase": "running"})
    code = str(job.get("code") or "")
    caught: list = []



    workspace = str(job.get("workspace") or "")
    if workspace and os.path.isdir(workspace):
        os.chdir(workspace)
    temp_dir = str(job.get("temp_dir") or "")
    if temp_dir and os.path.isdir(temp_dir):
        tempfile.tempdir = temp_dir



    arm = _tripwire().guard_child(str(job.get("granted") or "D"))

    def _keep(exc: BaseException) -> None:


        caught.extend((exc, traceback.format_exc()[-8_000:]))

    try:

        with _OnAnyFailure(_keep):
            exec(compile(code, _SNIPPET_FILE, "exec"), namespace)  # nosec B102
    finally:
        sink.flush()
    exception, exception_traceback = caught or (None, "")

    status: dict = {"phase": "done", "stdout_dropped": sink.dropped}

    trip = getattr(arm, "trip", None)
    if trip is not None:


        if type(trip).__name__ == "NeedsPermission":
            status.update({"ok": False, "needs_permission": {"class": str(getattr(trip, "cls", "") or ""),
                                                             "reason": str(getattr(trip, "reason", "") or "")}})
        else:
            status.update({"ok": False, "refused": str(getattr(trip, "reason", "") or trip)})
        made = guard.take_written()
        if made:
            status["files"] = [{"path": path} for path in made]
        status.update(_names_out(job, namespace, bound, helpers))
    elif _TRAPPED:
        status.update({"ok": False, "needs_qgis": _TRAPPED[0], "needs_qgis_line": _TRAPPED_LINES[0]})
    elif exception is not None:
        status.update({
            "ok": False,
            "type": type(exception).__name__,
            "message": str(exception)[:2000],
            "traceback": exception_traceback,
        })
        if isinstance(exception, NameError) and getattr(exception, "name", None):
            status["name"] = str(exception.name)


        made = guard.take_written()
        if made:
            status["files"] = [{"path": path} for path in made]
        status.update(_names_out(job, namespace, bound, helpers))
    else:
        status["ok"] = True


        made = guard.take_written()
        if made:
            status["files"] = [{"path": path} for path in made]

        shown = helpers.shown_files(namespace)
        if shown:
            status["show_files"] = shown
        status["result_set"] = "result" in namespace
        if status["result_set"]:

            value, status["result_type"] = helpers.result_json(namespace["result"])
            text = json.dumps(value, default=repr, ensure_ascii=False)
            status["result"] = text[:int(job.get("max_result_chars") or 1_000_000)]
            status["result_chars"] = len(text)
        status.update(_names_out(job, namespace, bound, helpers))
    _write(status_path, status)
    return 0


class _Sink:







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
    failed: list = []

    def _report(exc: BaseException) -> None:
        failed.append(exc)
        _write(status_path, {
            "phase": "setup",
            "ok": False,
            "setup_error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-4_000:],
        })


    job: dict = {}
    with _OnAnyFailure(_report), open(job_path, encoding="utf-8") as handle:
        job = json.load(handle)
    if failed:
        return 1
    try:
        os.remove(job_path)
    except OSError:


        pass
    with _OnAnyFailure(_report):
        exit_code = _run(job, status_path)

    return 1 if failed else exit_code


if __name__ == "__main__":
    code = main(sys.argv)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
