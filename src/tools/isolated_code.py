# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






































from __future__ import annotations

import hashlib
import io
import json
import os
import pickle  # nosec B403
import shutil
import site
import subprocess  # nosec B404
import sys
import tempfile
import threading
import time
import types
from collections import OrderedDict

from ..core import background, code_guard, code_split, code_tripwire, limits, net, security, tuning
from ..core import code_effects as ce
from ..core.background import run_on_main_thread
from ..core.host_platform import IS_WINDOWS, remove_tree
from ..core.layer_order import WEB_SERVICE_PROVIDERS
from ..core.logger import log_warning
from ..core.policy import AGENT_HOME, create_managed_temp_dir
from ..core.serialization import cut_string
from ..core.tool_registry import tool_error
from . import code_runtime
from .isolated_centerlines import _child_environment, usable_python

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_WORKER = os.path.join(_PLUGIN_ROOT, "src", "workers", "isolated_code_worker.py")



MAX_OUTPUT = 12_000
MAX_RESULT_CHARS = 1_000_000
MAX_LAYERS = 100
POLL_S = 0.05
STOP_GRACE_S = 0.5



NAMES_MAX_BYTES = 256 * 1024 * 1024

VECTOR_PROVIDERS = frozenset({"ogr", "delimitedtext", "spatialite", "gpx"})







RASTER_PROVIDERS = frozenset({"gdal"})

_REMOTE_MARKERS = ("/vsicurl", "/vsis3", "/vsigs", "/vsiaz", "/vsiadls", "/vsioss", "/vsiswift", "/vsihdfs",
                   "/vsiwebhdfs", "http://", "https://", "ftp://")




_IN_QGIS: OrderedDict[str, None] = OrderedDict()






_CHILD_UNAVAILABLE = [False]
_QGIS_CHILD_UNAVAILABLE = [False]



_CLOSES_PROJECT = frozenset({"QgsProject.clear", "QgsProject.read", "QgsProject.removeAllMapLayers"})





_COPY_FAILED: set[str] = set()
_PYTHON: list[str] = []


def _fingerprint(code: str) -> str:
    return hashlib.sha1(code.encode("utf-8")).hexdigest()  # nosec B324


def python_for_child() -> str:





    if _PYTHON:
        return _PYTHON[0]
    candidates: list[str] = []
    if os.path.basename(sys.executable or "").lower().startswith("python"):
        candidates.append(sys.executable)
    versioned = f"python{sys.version_info.major}.{sys.version_info.minor}"
    for base in (sys.prefix, sys.exec_prefix):
        candidates.extend((
            os.path.join(base, "python.exe"),
            os.path.join(base, "bin", versioned),
            os.path.join(base, "bin", "python3"),
        ))
    try:
        from qgis.core import QgsApplication

        app_dir = QgsApplication.applicationDirPath()
    except Exception:  # noqa: BLE001
        app_dir = ""
    if app_dir:
        candidates.extend((
            os.path.join(app_dir, versioned),
            os.path.join(app_dir, "python3"),
            os.path.join(app_dir, "python.exe"),
        ))
    for name in (versioned, "python3", "python"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    answer = next((path for path in candidates if usable_python(path)), "")
    _PYTHON.append(answer)
    return answer


def _ineligible(layer) -> str:

    from qgis.core import QgsRasterLayer, QgsVectorLayer

    if not layer.isValid():
        return "is not valid"
    if isinstance(layer, QgsVectorLayer):
        if layer.providerType() not in VECTOR_PROVIDERS:
            return "is not a local file layer"
    elif isinstance(layer, QgsRasterLayer):
        if layer.providerType() not in RASTER_PROVIDERS:
            return "is not a local file layer"
    else:
        return "is not a local file layer"
    source = str(layer.source() or "")
    lowered = source.lower()
    if "authcfg" in lowered:
        return "uses a saved login"
    if any(marker in lowered for marker in tuning.names("remote_markers", _REMOTE_MARKERS)):
        return "is read over the network"
    if isinstance(layer, QgsVectorLayer) and layer.isModified():
        return "has unsaved edits"


    if isinstance(layer, QgsVectorLayer) and layer.auxiliaryLayer() is not None:
        return "has auxiliary storage"
    return ""


def _layer_xml(layer) -> str:












    from qgis.core import QgsReadWriteContext
    from qgis.PyQt.QtXml import QDomDocument

    document = QDomDocument()
    element = document.createElement("maplayer")
    document.appendChild(element)
    written = layer.writeLayerXml(element, document, QgsReadWriteContext())
    text = document.toString()
    if written is False or not text:
        raise ValueError("QGIS wrote no layer definition")
    check = QDomDocument()
    result = check.setContent(text)
    parsed = result[0] if isinstance(result, tuple) else result
    if not parsed:
        raise ValueError("the written definition could not be parsed")
    return text







_OUTSIDE_FUNCTION_GROUPS = frozenset({"Aggregates", "Map Layers", "Rasters", "Sensors"})
_OUTSIDE_FUNCTIONS = frozenset({
    "get_feature", "get_feature_by_id", "is_selected", "num_selected", "represent_value", "represent_attributes",
    "display_expression", "maptip", "is_attribute_valid", "is_feature_valid", "sqlite_fetch_and_increment",
    "eval", "eval_template", "env", "is_layer_visible", "project_color", "project_color_object",
    "item_variables", "map_credits",
})





_LAYER_VARIABLES = frozenset({
    "layer", "layer_id", "layer_name", "layer_crs", "layer_crs_ellipsoid", "feature", "id", "geometry",
    "row_number",
})


_OUTSIDE_NAMES: tuple | None = None


def _outside_functions() -> frozenset[str]:

    global _OUTSIDE_NAMES
    functions = tuning.names("outside_functions", _OUTSIDE_FUNCTIONS)
    groups = tuning.names("outside_function_groups", _OUTSIDE_FUNCTION_GROUPS)
    if _OUTSIDE_NAMES is None or _OUTSIDE_NAMES[0] != (functions, groups):
        from qgis.core import QgsExpression

        names = set(functions)
        for function in QgsExpression.Functions():
            if groups.intersection(function.groups()):
                names.add(function.name())
                names.update(function.aliases())
        _OUTSIDE_NAMES = ((functions, groups), frozenset(names))
    return _OUTSIDE_NAMES[1]


def _python_function(name: str) -> bool:








    from qgis.core import QgsExpression, QgsExpressionFunction

    index = QgsExpression.functionIndex(name)
    functions = QgsExpression.Functions()
    if not 0 <= index < len(functions):
        return False
    return type(functions[index]).__module__ != QgsExpressionFunction.__module__


def _outside_reference(layer) -> str:












    from qgis.core import Qgis, QgsExpression, QgsFields, QgsVectorLayer

    if not isinstance(layer, QgsVectorLayer):
        return ""
    origin = getattr(QgsFields, "OriginExpression", None)
    if origin is None:
        origin = Qgis.FieldOrigin.Expression
    fields = layer.fields()
    for index in range(fields.count()):
        if fields.fieldOrigin(index) != origin:
            continue
        expression = QgsExpression(layer.expressionField(index) or "")
        name = fields.at(index).name()
        for function in sorted(expression.referencedFunctions()):
            if function in _outside_functions() or function.startswith("overlay_"):
                return f"has an expression field {name!r} that calls {function}()"
            if _python_function(function):
                return f"has an expression field {name!r} that calls {function}(), a function defined in Python"
        for variable in sorted(expression.referencedVariables()):
            if variable not in _LAYER_VARIABLES:
                what = f"@{variable}" if variable else "a variable whose name is computed"
                return f"has an expression field {name!r} that reads {what}"
    return ""


def _joined_layer_ids(layer) -> list[str]:

    from qgis.core import QgsVectorLayer

    if not isinstance(layer, QgsVectorLayer):
        return []
    ids: list[str] = []
    for info in layer.vectorJoins() or ():
        partner = info.joinLayer()
        identifier = partner.id() if partner is not None else info.joinLayerId()
        if identifier and identifier not in ids:
            ids.append(identifier)
    return ids






_COMMON_ATTRIBUTES = frozenset(
    name
    for kind in (str, bytes, list, dict, set, tuple, io.TextIOWrapper, io.BufferedReader,
                 io.BufferedWriter, io.StringIO, io.BytesIO)
    for name in dir(kind)
)










_READ_ONLY_CLASSES = (
    "QgsFeatureRequest", "QgsDistanceArea", "QgsExpression", "QgsExpressionContext", "QgsGeometry", "QgsFeature",
    "QgsFields", "QgsField", "QgsRectangle", "QgsPointXY", "QgsCoordinateTransform", "QgsCoordinateReferenceSystem",
    "QgsSpatialIndex",
)




_READ_ONLY_KEEP = frozenset({"setName", "setMetadata", "setEditorWidgetSetup", "setDefaultValueDefinition",
                             "setReadOnly"})



_TRAP_METHODS: frozenset[str] | None = None


def _project_changing_methods() -> frozenset[str]:














    global _TRAP_METHODS
    if _TRAP_METHODS is not None:
        return _TRAP_METHODS
    import qgis.core as core

    bases = [base for base in (getattr(core, name, None) for name in code_split.TRAP_BASES)
             if isinstance(base, type)]
    read_only: set[str] = set()
    for class_name in _READ_ONLY_CLASSES:
        value = getattr(core, class_name, None)
        if isinstance(value, type):
            read_only.update(dir(value))
    read_only -= _READ_ONLY_KEEP
    names: set[str] = set()
    if bases:
        for value in vars(core).values():
            if not isinstance(value, type) or not any(issubclass(value, base) for base in bases):
                continue
            names.update(name for name in dir(value)
                         if name not in code_split.TRAP_KEEP
                         and name not in _COMMON_ATTRIBUTES
                         and name not in read_only
                         and code_split.TRAP_RE.match(name))
    _TRAP_METHODS = frozenset(names)
    return _TRAP_METHODS


def plan(code: str) -> dict:















    reason = code_split.live_reason(code, changing=_project_changing_methods())
    if reason:
        return {"reason": reason}


    workspace = security.workspace_dir()



    read_roots = _read_roots()
    if not code_split.needs_qgis(code):
        return {"reason": "", "needs_qgis": False, "layers": [], "project": {}, "workspace": workspace,
                "read_roots": read_roots, "write_roots": security.write_roots()}


    from qgis.core import QgsMapLayer, QgsProject, QgsRasterLayer, QgsUnitTypes, QgsVectorLayer  # noqa: F401

    project = QgsProject.instance()
    dynamic = code_split.lists_layers(code)

    helper_names = {name.casefold() for name in code_split.helper_layer_names(code)}
    pending = [layer for layer in project.mapLayers().values()
               if dynamic or (layer.name() and layer.name() in code) or layer.id() in code
               or layer.name().casefold() in helper_names]
    partners: set[str] = set()
    seen: set[str] = set()
    reached = []
    while pending:
        layer = pending.pop(0)
        if layer.id() in seen:
            continue
        seen.add(layer.id())
        why = (("could not be rebuilt in a separate process earlier this session" if layer.id() in _COPY_FAILED
                else "") or _ineligible(layer) or _outside_reference(layer))
        if why:


            origin = "joined " if layer.id() in partners else ""
            return {"reason": f"{origin}layer {layer.name()!r} {why}"}
        reached.append(layer)
        for partner_id in _joined_layer_ids(layer):
            partner = project.mapLayer(partner_id)
            if partner is None or partner.id() in seen:
                continue
            partners.add(partner.id())
            pending.append(partner)
    if len(reached) > MAX_LAYERS:
        return {"reason": f"reaches more than {MAX_LAYERS} layers"}
    chosen: list[dict] = []
    for layer in reached:
        is_vector = isinstance(layer, QgsVectorLayer)
        try:
            xml = _layer_xml(layer)
        except Exception as exc:  # noqa: BLE001
            return {"reason": f"layer {layer.name()!r} cannot be described: {exc}"}
        crs = layer.crs()
        chosen.append({
            "id": layer.id(),
            "name": layer.name(),
            "kind": "vector" if is_vector else "raster",
            "provider": layer.providerType(),
            "source": layer.source(),
            "subset": layer.subsetString() if is_vector else "",
            "crs_wkt": crs.toWkt() if crs.isValid() else "",
            "xml": xml,
        })
    if any(entry["id"] in code for entry in chosen) and not hasattr(QgsMapLayer, "setId"):
        return {"reason": "names a layer id this QGIS cannot give a copied layer"}
    project_crs = project.crs()
    return {
        "reason": "",
        "needs_qgis": True,
        "workspace": workspace,
        "read_roots": read_roots,
        "write_roots": security.write_roots(),
        "layers": chosen,
        "project": {
            "crs_wkt": project_crs.toWkt() if project_crs.isValid() else "",
            "ellipsoid": project.ellipsoid(),

            "distance_units": QgsUnitTypes.encodeUnit(project.distanceUnits()),
            "area_units": QgsUnitTypes.encodeUnit(project.areaUnits()),
            "file_name": project.fileName(),
            "home_path": project.homePath(),
        },
    }


def _read_roots():
    return security.read_roots() if security.read_scope_enabled() else None


def isolation_enabled() -> bool:







    return tuning.flag("execute_code", "isolated_enabled", True)


def runs_isolated(args: dict) -> bool:

    code = str(args.get("code") or "")
    if not code or not isolation_enabled():
        return False
    if _fingerprint(code) in _IN_QGIS or _CHILD_UNAVAILABLE[0]:
        return False
    if not python_for_child():
        return False
    try:
        planned = plan(code)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"execute_code isolation check failed: {exc}")
        return False
    if planned.get("reason"):
        return False

    return not (planned.get("needs_qgis") and _QGIS_CHILD_UNAVAILABLE[0])


def _is_user_site(entry: str) -> bool:








    target = os.path.normcase(os.path.abspath(entry))
    roots: list[str] = []
    try:
        user_site = site.getusersitepackages()
    except Exception:  # noqa: BLE001
        user_site = ""
    if isinstance(user_site, str) and user_site:
        roots.append(user_site)
    user_base = getattr(site, "USER_BASE", None)
    if isinstance(user_base, str) and user_base:
        roots.append(user_base)
    for root in roots:
        root = os.path.normcase(os.path.abspath(root))
        if target == root or target.startswith(root + os.sep):
            return True
    return False


def _environment(python: str, work_dir: str) -> dict:

    if "qgis" in python.lower():
        environment = _child_environment(python, work_dir)
    else:
        environment = dict(os.environ)
        environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QGIS_CUSTOM_CONFIG_PATH"] = os.path.join(work_dir, "qgis-profile")
    environment["QGIS_AI_AGENT_HOME"] = AGENT_HOME
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"


    environment["PYTHONIOENCODING"] = "utf-8"
    environment.pop("PYTHONSTARTUP", None)




    inherited = [entry for entry in sys.path if isinstance(entry, str) and entry and os.path.exists(entry)]
    application = [entry for entry in inherited if not _is_user_site(entry)]
    user_site = [entry for entry in inherited if _is_user_site(entry)]
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join(
        application + user_site + ([existing] if existing else []))
    return environment


def _stop(process) -> None:

    process.terminate()
    try:
        process.wait(timeout=STOP_GRACE_S)
        return
    except subprocess.TimeoutExpired:
        process.kill()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired as exc:
        log_warning(f"execute_code child did not exit after kill: {exc}")


def _read_status(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _read_text(path: str, limit: int) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read(limit)
    except OSError:
        return ""


def _tail(path: str, limit: int = 800) -> str:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - limit))
            return " ".join(handle.read().decode("utf-8", "replace").split())[-limit:]
    except OSError:
        return ""


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





_DATA_GLOBALS = frozenset({
    ("builtins", "complex"), ("builtins", "set"), ("builtins", "frozenset"), ("builtins", "bytearray"),
    ("collections", "OrderedDict"),
    ("datetime", "date"), ("datetime", "datetime"), ("datetime", "time"), ("datetime", "timedelta"),
    ("datetime", "timezone"),
    ("numpy", "ndarray"), ("numpy", "dtype"),
    ("numpy.core.multiarray", "_reconstruct"), ("numpy.core.multiarray", "scalar"),
    ("numpy._core.multiarray", "_reconstruct"), ("numpy._core.multiarray", "scalar"),
})


class _PlainData(pickle.Unpickler):
    def find_class(self, module, name):
        if (module, name) in _DATA_GLOBALS:
            return super().find_class(module, name)
        raise pickle.UnpicklingError(f"{module}.{name} is not plain data")


def _module(name: str):

    importer = code_guard.build_safe_builtins()["__import__"]
    parent, _, leaf = name.rpartition(".")
    return getattr(importer(parent, None, None, (leaf,), 0), leaf) if parent else importer(name)


def _send_names(kept: dict, folder: str) -> tuple[list, dict, list]:

    files: list[dict] = []
    modules: dict[str, str] = {}
    left_out: list[str] = []
    room = NAMES_MAX_BYTES
    for index, (name, value) in enumerate(sorted(kept.items())):
        if isinstance(value, types.ModuleType):
            modules[name] = value.__name__
            continue
        path = os.path.join(folder, f"in{index}.pkl")
        try:
            with open(path, "wb") as handle:
                sink = _Measured(handle, room)
                pickle.Pickler(sink, protocol=4).dump(value)
        except Exception:  # noqa: BLE001
            left_out.append(name)
            continue
        room -= sink.size
        files.append({"name": name, "file": os.path.basename(path), "sha1": sink.digest.hexdigest()})
    return files, modules, left_out


def _names_back(status: dict, folder: str) -> dict:

    values: dict = {}
    not_kept = [str(name) for name in status.get("not_kept") or []]
    for entry in status.get("names") or []:
        name = str(entry.get("name") or "")
        try:
            with open(os.path.join(folder, os.path.basename(str(entry.get("file") or ""))), "rb") as handle:
                values[name] = _PlainData(handle).load()  # nosec B301
        except Exception:  # noqa: BLE001
            not_kept.append(name)
    for name, module in (status.get("modules") or {}).items():
        try:
            values[str(name)] = _module(str(module))
        except Exception:  # noqa: BLE001
            not_kept.append(str(name))
    deleted = [str(name) for name in status.get("deleted") or []]
    lost = {str(name): str(kind) for name, kind in (status.get("lost") or {}).items()}
    lost.update({name: lost.get(name, "") for name in not_kept})


    return {"values": values, "drop": deleted + list(lost), "not_kept": not_kept, "lost": lost}


def _store_names(chat: str, values: dict, drop: list, lost: dict | None = None) -> None:

    names = code_runtime.kept(chat)
    for name in drop:
        names.pop(name, None)
    names.update(values)
    code_runtime.keep(chat, names, set(), lost=lost)


def _put_names(out: dict, status: dict, folder: str) -> None:
    names = _names_back(status, folder)
    out["_names"] = names
    if names["not_kept"]:

        out["not_kept"] = names["not_kept"]


def _cap(text: str) -> str:
    return cut_string(text, MAX_OUTPUT)


def _put_capped(out: dict, key: str, text: str, true_len=None) -> None:
    out[key] = _cap(text)
    length = true_len or len(text)
    if length > MAX_OUTPUT:
        out[f"{key}_chars"] = length


def run_child(code: str, planned: dict, python: str, cancel_check, timeout_s: float,
              poll_s: float = POLL_S) -> dict:








    work_dir = create_managed_temp_dir("execute-code")
    names_dir = os.path.join(work_dir, "names")
    job_path = os.path.join(work_dir, "job.json")
    status_path = os.path.join(work_dir, "status.json")
    stdout_path = os.path.join(work_dir, "stdout.txt")
    log_path = os.path.join(work_dir, "worker.log")
    started = time.monotonic()
    try:
        os.makedirs(names_dir, exist_ok=True)
        name_files, modules, left_out = _send_names(planned.get("kept") or {}, names_dir)
        if left_out:
            return {"_fallback": "kept_names", "detail": ", ".join(left_out)}
        job = {
            "code": code,
            "plugin_root": _PLUGIN_ROOT,
            "needs_qgis": bool(planned.get("needs_qgis")),
            "stdout_path": stdout_path,
            "layers": planned.get("layers") or [],
            "project": planned.get("project") or {},
            "workspace": planned.get("workspace") or "",
            "read_roots": planned["read_roots"] if "read_roots" in planned else _read_roots(),
            "write_roots": planned.get("write_roots") or {},

            "granted": planned.get("granted") or ce.D,


            "temp_dir": tempfile.gettempdir(),
            "max_output_chars": code_guard.MAX_OUTPUT_CHARS,
            "max_result_chars": MAX_RESULT_CHARS,
            "names_dir": names_dir,
            "names": name_files,
            "modules": modules,
            "max_names_bytes": NAMES_MAX_BYTES,
        }
        with open(job_path, "w", encoding="utf-8") as handle:
            json.dump(job, handle, ensure_ascii=False)
        command = [python, "-s", _WORKER, job_path, status_path]
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} \
            if IS_WINDOWS and hasattr(subprocess, "CREATE_NO_WINDOW") else {}
        with open(log_path, "wb") as log_handle:
            process = subprocess.Popen(  # nosec B603
                command,
                cwd=work_dir,
                env=_environment(python, work_dir),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                **kwargs,
            )
            try:
                while process.poll() is None:
                    if cancel_check is not None and cancel_check():
                        _stop(process)
                        return {
                            "executed": False,
                            "_error": "execute_code was stopped.",
                            "_code": "CANCELLED",
                            "stdout": _cap(_read_text(stdout_path, code_guard.MAX_OUTPUT_CHARS)),
                            "isolated": True,
                        }
                    if time.monotonic() - started > timeout_s:
                        _stop(process)
                        return {
                            "executed": False,
                            "_error": f"execute_code stopped after {int(timeout_s)} s.",
                            "_code": "EXEC_TIMEOUT",
                            "stdout": _cap(_read_text(stdout_path, code_guard.MAX_OUTPUT_CHARS)),
                            "hint": "exec_timeout",
                            "timeout_s": int(timeout_s),
                            "isolated": True,
                        }
                    time.sleep(poll_s)
            finally:
                if process.poll() is None:
                    _stop(process)
        status = _read_status(status_path)


        security.note_own_paths([entry.get("path") for entry in (status.get("files") or [])
                                 if isinstance(entry, dict) and entry.get("path")])
        stdout = _read_text(stdout_path, code_guard.MAX_OUTPUT_CHARS)
        dropped = status.get("stdout_dropped")
        if dropped:
            stdout += (f"\n\n[TRUNCATED: {int(dropped):,} more characters were printed, showing the first "
                       f"{code_guard.MAX_OUTPUT_CHARS:,}]")
        phase = str(status.get("phase") or "")
        if phase == "done" and status.get("names_failed"):

            return {"_fallback": "kept_names", "detail": str(status.get("names_failed"))}
        if phase == "done" and status.get("layer_copy_failed"):


            return {"_fallback": "layer_copy", "detail": str(status.get("layer_copy_failed")),
                    "layer_id": str(status.get("layer_id") or ""), "stdout": _cap(stdout)}
        if phase == "done" and status.get("needs_qgis"):
            line = status.get("needs_qgis_line")
            return {"_fallback": "needs_qgis", "detail": str(status.get("needs_qgis")),
                    "line": line if isinstance(line, int) else 0, "stdout": _cap(stdout)}
        if phase == "done" and (status.get("needs_permission") or status.get("refused")):
            out = _tripped(status, stdout)
            _put_names(out, status, names_dir)
            return out
        if phase == "done" and status.get("ok"):
            out = {"executed": True, "isolated": True}
            _put_capped(out, "stdout", stdout)
            if status.get("result_set"):
                text = str(status.get("result") or "null")
                parsed = len(text) <= MAX_OUTPUT
                if parsed:
                    try:
                        out["result"] = json.loads(text)
                    except ValueError:
                        parsed = False
                if not parsed:

                    _put_capped(out, "result", text, status.get("result_chars"))
                out["result_type"] = str(status.get("result_type") or "")
            else:
                out["result_set"] = False



            made = [str(entry.get("path")) for entry in (status.get("files") or [])
                    if isinstance(entry, dict) and entry.get("path")]
            if made:
                out["files_written"] = made
            shown = [path for path in (status.get("show_files") or [])
                     if isinstance(path, str) and os.path.isfile(path)]
            if shown:
                out["files"] = [{"path": path} for path in shown]
            _put_names(out, status, names_dir)
            return out
        if phase == "done":
            exc_type = str(status.get("type") or "Error")
            message = str(status.get("message") or "")


            written = [str(entry.get("path")) for entry in (status.get("files") or [])
                       if isinstance(entry, dict) and entry.get("path")]
            out = {
                "executed": False,
                "_error": f"execute_code raised {exc_type}: {message}",
                "_code": "EXEC_RUNTIME_ERROR",
                "stdout": _cap(stdout),
                "traceback": _cap(str(status.get("traceback") or "")),
                "_exc_type": exc_type,
                "_exc_message": message,
                "_exc_name": str(status.get("name") or ""),
                "isolated": True,
                "execution_state": "partial" if written else "unknown",
            }
            if written:
                out["files_written"] = written

            _put_names(out, status, names_dir)
            return out
        if phase == "running":
            return {
                "executed": False,
                "_error": f"The separate Python process running this snippet ended with exit code "
                          f"{process.returncode} before it finished. QGIS itself was not affected.",
                "_code": "EXEC_CRASHED",
                "stdout": _cap(stdout),
                "hint": "exec_crashed",
                "exit_code": process.returncode,
                "isolated": True,
            }
        detail = str(status.get("setup_error") or _tail(log_path) or f"exit code {process.returncode}")
        if os.path.exists(stdout_path):






            return {
                "executed": False,
                "_error": f"The snippet ran in a separate Python process, but its answer could not be "
                          f"read back: {detail}",
                "_code": "EXEC_RUNTIME_ERROR",
                "stdout": _cap(stdout),
                "hint": "exec_answer_unreadable",
                "isolated": True,
            }
        return {"_fallback": "setup", "detail": detail}
    finally:
        remove_tree(work_dir)


def _tripped(status: dict, stdout: str) -> dict:






    written = [str(entry.get("path")) for entry in (status.get("files") or [])
               if isinstance(entry, dict) and entry.get("path")]
    need = status.get("needs_permission")
    if need:
        out = {
            "executed": False,
            "isolated": True,
            "_code": "NEEDS_PERMISSION",
            "_error": f"The snippet {need.get('reason') or 'went past its class'}, which needs the user's "
                      "permission. Nothing after that call ran.",
            "needs_permission": {"class": str(need.get("class") or ce.D), "reason": str(need.get("reason") or "")},
            "stdout": _cap(stdout),
            "hint": "exec_needs_permission",
        }
    else:
        out = {
            "executed": False,
            "isolated": True,
            "_code": "PERMISSION_DENIED",
            "_error": f"execute_code stopped: the snippet {status.get('refused')}. Nothing after that call ran.",
            "stdout": _cap(stdout),
            "hint": "snippet_call_refused",
            "refused": str(status.get("refused") or ""),
        }
    if written:
        out["files_written"] = written
        out["execution_state"] = "partial"
    return out


def _web_service_layers(code: str) -> list:




    from qgis.core import QgsProject, QgsVectorLayer

    return [layer for layer in QgsProject.instance().mapLayers().values()
            if isinstance(layer, QgsVectorLayer) and layer.isValid()
            and str(layer.providerType() or "").lower() in WEB_SERVICE_PROVIDERS
            and ((layer.name() and layer.name() in code) or layer.id() in code)]


def runs_in_task(args: dict) -> bool:

    if runs_isolated(args):
        return True
    try:
        return bool(_web_service_layers(str(args.get("code") or "")))
    except Exception as exc:  # noqa: BLE001
        log_warning(f"execute_code web service check failed: {exc}")
        return False


def _web_service_sources(code: str) -> list:
    from qgis.core import QgsVectorLayerFeatureSource

    return [(layer.name(), QgsVectorLayerFeatureSource(layer)) for layer in _web_service_layers(code)]


def _read_web_services(code: str, cancel_check) -> dict | None:








    from qgis.core import QgsFeatureRequest, QgsFeedback

    try:
        sources = run_on_main_thread(_web_service_sources, code, timeout=30)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"execute_code web service pre-read skipped: {exc}")
        return None

    for name, source in sources:
        request, feedback = QgsFeatureRequest(), QgsFeedback()
        if hasattr(request, "setFeedback"):
            request.setFeedback(feedback)
        over, failure = threading.Event(), []

        def reader(source=source, request=request, feedback=feedback, over=over, failure=failure):
            try:
                features = source.getFeatures(request)
                try:
                    for _feature in features:
                        if feedback.isCanceled():
                            break
                finally:
                    features.close()
            except Exception as exc:  # noqa: BLE001
                failure.append(exc)
            finally:
                over.set()

        background.start_kept_thread(reader, name="execute_code web service read")
        while not over.wait(POLL_S):
            if callable(cancel_check) and cancel_check():
                feedback.cancel()
                return tool_error(f"Stopped while the layer {name!r} was read from its service.", "CANCELLED",
                                  "Nothing was changed.")
        if failure:
            log_warning(f"execute_code could not read {name!r} ahead of the snippet: {failure[0]}")
    return None


def _plan_with_names(code: str, chat: str) -> dict:

    planned = plan(code)
    if not planned.get("reason"):
        kept = code_runtime.kept(chat)
        planned["kept"] = {name: kept[name] for name in code_split.names_read(code) if name in kept}
    return planned


def run(args: dict, run_in_qgis, api_help) -> dict:





    code = str(args.get("code") or "")
    if not isolation_enabled():

        return _in_qgis(args, run_in_qgis)
    cancel_check = net.current_cancel_check()
    chat = code_runtime.current().chat
    try:
        planned = run_on_main_thread(_plan_with_names, code, chat, timeout=30)
    except Exception as exc:  # noqa: BLE001
        planned = {"reason": f"the isolation plan failed: {exc}"}
    if planned.get("reason"):
        return _in_qgis(args, run_in_qgis)


    planned["granted"] = code_runtime.current().granted if code_tripwire.enabled() else ce.D





    timeout_s = min(limits.current("EXECUTE_CODE_MAX_SECONDS"), float(code_runtime.current().timeout_s))
    try:
        outcome = run_child(code, planned, python_for_child(), cancel_check, timeout_s)
    except Exception as exc:  # noqa: BLE001
        outcome = {"_fallback": "setup", "detail": f"{type(exc).__name__}: {exc}"}
    fallback = outcome.pop("_fallback", "")
    names = outcome.pop("_names", None)
    if names and (names["values"] or names["drop"]):
        try:
            run_on_main_thread(_store_names, chat, names["values"], names["drop"], names["lost"], timeout=10)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"execute_code could not keep the names of its separate process: {exc}")
    if fallback == "kept_names":

        return _in_qgis(args, run_in_qgis)
    if fallback == "layer_copy":




        failed = str(outcome.get("layer_id") or "")
        planned_ids = [str(entry.get("id") or "") for entry in planned.get("layers") or []]
        _COPY_FAILED.update([failed] if failed else planned_ids)
        _COPY_FAILED.discard("")
        return _in_qgis(args, run_in_qgis)
    if fallback == "needs_qgis":


        _IN_QGIS[_fingerprint(code)] = None
        while len(_IN_QGIS) > 256:
            _IN_QGIS.popitem(last=False)
        trapped = str(outcome.get("detail") or "a project-changing call")
        if trapped not in _CLOSES_PROJECT and code_split.quiet_before(code, int(outcome.get("line") or 0)):




            return _in_qgis(args, run_in_qgis)





        return {
            "executed": False,
            "isolated": True,
            "_error": f"execute_code stopped at {trapped}, which changes the project: the snippet ran "
                      f"in a separate Python process up to that call and stopped there, so the lines "
                      f"before it ran (a file they wrote is written) and the project was not changed.",
            "_code": "EXEC_RUNTIME_ERROR",
            "stdout": outcome.get("stdout", ""),
            "hint": "exec_stopped_at_project_change",
            "stopped_at": str(trapped),
        }
    if fallback == "setup":
        detail = outcome.get("detail") or "no detail"
        if planned.get("needs_qgis"):

            _QGIS_CHILD_UNAVAILABLE[0] = True
            scope = "for QGIS snippets"
        else:
            _CHILD_UNAVAILABLE[0] = True
            scope = "for every snippet"
        log_warning(f"execute_code cannot start its separate process {scope}, it runs inside QGIS "
                    f"from now on: {detail}")
        return _in_qgis(args, run_in_qgis)
    exc_type = outcome.pop("_exc_type", "")
    exc_message = outcome.pop("_exc_message", "")
    exc_name = outcome.pop("_exc_name", "")


    help_text = code_runtime.dropped_help(chat, exc_name) if exc_name else ""
    if not help_text and exc_type in ("AttributeError", "NameError", "TypeError"):
        try:
            exc = {"AttributeError": AttributeError, "NameError": NameError, "TypeError": TypeError}[exc_type](
                exc_message)
            help_text = run_on_main_thread(api_help, exc, timeout=10)
        except Exception:  # noqa: BLE001
            help_text = ""
    if not help_text:



        try:
            from .advanced_code import code_help

            help_text = code_help(exc_type, exc_message)
        except Exception:  # noqa: BLE001
            help_text = ""
    if help_text:
        outcome["api"] = help_text
    return outcome


def _in_qgis(args: dict, run_in_qgis) -> dict:






    stopped = _read_web_services(str(args.get("code") or ""), net.current_cancel_check())
    if stopped is not None:
        return stopped
    return run_on_main_thread(_in_context, code_runtime.current(), run_in_qgis, args,
                              timeout=limits.current("EXECUTE_CODE_MAX_SECONDS") + 60)


def _in_context(context, run_in_qgis, args: dict) -> dict:
    with code_runtime.calling(context):
        return run_in_qgis(args)
