# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which ``execute_code`` snippets run in their own process, and how."""


























from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import site
import subprocess  # nosec B404 - the QGIS interpreter and the bundled worker only
import sys
import time
from collections import OrderedDict

from ..core import code_guard, code_split, net, tuning
from ..core.background import run_on_main_thread
from ..core.host_platform import remove_tree
from ..core.logger import log_warning
from ..core.policy import AGENT_HOME, create_managed_temp_dir
from ..core.serialization import cut_string
from .isolated_centerlines import _child_environment, usable_python

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_WORKER = os.path.join(_PLUGIN_ROOT, "src", "workers", "isolated_code_worker.py")



MAX_OUTPUT = 12_000
MAX_RESULT_CHARS = 1_000_000
MAX_LAYERS = 100
POLL_S = 0.05
STOP_GRACE_S = 0.5

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
    return hashlib.sha1(code.encode("utf-8")).hexdigest()  # nosec B324 - a cache key, not security


def python_for_child() -> str:
    """The interpreter that can import this plugin's guard and PyQGIS, or ""."""




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
    except Exception:  # noqa: BLE001 - no QGIS here: the earlier candidates decide
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
    """Why this layer cannot be copied into the child, or "" when it can."""
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
    if any(marker in lowered for marker in _REMOTE_MARKERS):
        return "is read over the network"
    if isinstance(layer, QgsVectorLayer) and layer.isModified():
        return "has unsaved edits"


    if isinstance(layer, QgsVectorLayer) and layer.auxiliaryLayer() is not None:
        return "has auxiliary storage"
    return ""


def _layer_xml(layer) -> str:
    """The layer's full definition, as QGIS writes it into a project file."""











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
_OUTSIDE_NAMES: frozenset[str] | None = None


def _outside_functions() -> frozenset[str]:
    """``_OUTSIDE_FUNCTIONS`` plus every function of ``_OUTSIDE_FUNCTION_GROUPS``, built once."""
    global _OUTSIDE_NAMES
    if _OUTSIDE_NAMES is None:
        from qgis.core import QgsExpression

        names = set(_OUTSIDE_FUNCTIONS)
        for function in QgsExpression.Functions():
            if _OUTSIDE_FUNCTION_GROUPS.intersection(function.groups()):
                names.add(function.name())
                names.update(function.aliases())
        _OUTSIDE_NAMES = frozenset(names)
    return _OUTSIDE_NAMES


def _python_function(name: str) -> bool:
    """Is this expression function registered from Python in this process?"""







    from qgis.core import QgsExpression, QgsExpressionFunction

    index = QgsExpression.functionIndex(name)
    functions = QgsExpression.Functions()
    if not 0 <= index < len(functions):
        return False
    return type(functions[index]).__module__ != QgsExpressionFunction.__module__


def _outside_reference(layer) -> str:
    """Why an expression field of this layer would answer differently in the child, or ""."""











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
    """The ids of the layers this one joins, in join order, without duplicates."""
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
    """The qgis.core method names a snippet must not reach a child with."""













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
    """What the child needs for this snippet, or why it must run inside QGIS."""














    reason = code_split.live_reason(code, changing=_project_changing_methods())
    if reason:
        return {"reason": reason}
    if not code_split.needs_qgis(code):
        return {"reason": "", "needs_qgis": False, "layers": [], "project": {}}


    from qgis.core import QgsMapLayer, QgsProject, QgsRasterLayer, QgsUnitTypes, QgsVectorLayer  # noqa: F401

    project = QgsProject.instance()
    dynamic = code_split.lists_layers(code)
    pending = [layer for layer in project.mapLayers().values()
               if dynamic or (layer.name() and layer.name() in code) or layer.id() in code]
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
        except Exception as exc:  # noqa: BLE001 - a definition that cannot be written keeps the snippet in QGIS
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


def isolation_enabled() -> bool:
    """False once a served row switched the separate process off for every install."""






    return tuning.flag("execute_code", "isolated_enabled", True)


def runs_isolated(args: dict) -> bool:
    """The tool's background predicate: may this snippet run in its own process?"""
    code = str(args.get("code") or "")
    if not code or not isolation_enabled():
        return False
    if _fingerprint(code) in _IN_QGIS or _CHILD_UNAVAILABLE[0]:
        return False
    if not python_for_child():
        return False
    try:
        planned = plan(code)
    except Exception as exc:  # noqa: BLE001 - a plan that cannot be built runs inside QGIS
        log_warning(f"execute_code isolation check failed: {exc}")
        return False
    if planned.get("reason"):
        return False

    return not (planned.get("needs_qgis") and _QGIS_CHILD_UNAVAILABLE[0])


def _is_user_site(entry: str) -> bool:
    """True when *entry* is the user site folder or sits under ``site.USER_BASE``."""







    target = os.path.normcase(os.path.abspath(entry))
    roots: list[str] = []
    try:
        user_site = site.getusersitepackages()
    except Exception:  # noqa: BLE001 - a site without the function simply has no user site
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
    """The child's environment: its own QGIS profile, its own scratch folder."""
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
    """End a child that is still running: terminate, then kill after the grace."""
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


def _cap(text: str) -> str:
    return cut_string(text, MAX_OUTPUT)


def _put_capped(out: dict, key: str, text: str, true_len=None) -> None:
    out[key] = _cap(text)
    length = true_len or len(text)
    if length > MAX_OUTPUT:
        out[f"{key}_chars"] = length


def run_child(code: str, planned: dict, python: str, cancel_check, timeout_s: float,
              poll_s: float = POLL_S) -> dict:
    """Start the worker, poll it, and turn its status file into one outcome."""





    work_dir = create_managed_temp_dir("execute-code")
    job_path = os.path.join(work_dir, "job.json")
    status_path = os.path.join(work_dir, "status.json")
    stdout_path = os.path.join(work_dir, "stdout.txt")
    log_path = os.path.join(work_dir, "worker.log")
    started = time.monotonic()
    try:
        job = {
            "code": code,
            "plugin_root": _PLUGIN_ROOT,
            "needs_qgis": bool(planned.get("needs_qgis")),
            "stdout_path": stdout_path,
            "layers": planned.get("layers") or [],
            "project": planned.get("project") or {},
            "max_output_chars": code_guard.MAX_OUTPUT_CHARS,
            "max_result_chars": MAX_RESULT_CHARS,
        }
        with open(job_path, "w", encoding="utf-8") as handle:
            json.dump(job, handle, ensure_ascii=False)
        command = [python, "-s", _WORKER, job_path, status_path]
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} \
            if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW") else {}
        with open(log_path, "wb") as log_handle:
            process = subprocess.Popen(  # nosec B603 - the QGIS interpreter and the bundled worker only
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
                            "suggestion": "Narrow the work (fewer features, one layer) or use "
                                          "run_processing with async=true.",
                            "isolated": True,
                        }
                    time.sleep(poll_s)
            finally:
                if process.poll() is None:
                    _stop(process)
        status = _read_status(status_path)
        stdout = _read_text(stdout_path, code_guard.MAX_OUTPUT_CHARS)
        dropped = status.get("stdout_dropped")
        if dropped:
            stdout += (f"\n\n[TRUNCATED: {int(dropped):,} more characters were printed, showing the first "
                       f"{code_guard.MAX_OUTPUT_CHARS:,}]")
        phase = str(status.get("phase") or "")
        if phase == "done" and status.get("layer_copy_failed"):


            return {"_fallback": "layer_copy", "detail": str(status.get("layer_copy_failed")),
                    "layer_id": str(status.get("layer_id") or ""), "stdout": _cap(stdout)}
        if phase == "done" and status.get("needs_qgis"):
            line = status.get("needs_qgis_line")
            return {"_fallback": "needs_qgis", "detail": str(status.get("needs_qgis")),
                    "line": line if isinstance(line, int) else 0, "stdout": _cap(stdout)}
        if phase == "done" and status.get("ok"):
            out = {"executed": True, "isolated": True}
            _put_capped(out, "stdout", stdout)
            if status.get("result_set"):
                _put_capped(out, "result", str(status.get("result") or ""), status.get("result_chars"))
            else:
                out["result_set"] = False
            return out
        if phase == "done":
            exc_type = str(status.get("type") or "Error")
            message = str(status.get("message") or "")
            return {
                "executed": False,
                "_error": f"execute_code raised {exc_type}: {message}",
                "_code": "EXEC_RUNTIME_ERROR",
                "stdout": _cap(stdout),
                "traceback": _cap(str(status.get("traceback") or "")),
                "_exc_type": exc_type,
                "_exc_message": message,
                "isolated": True,
            }
        if phase == "running":
            return {
                "executed": False,
                "_error": f"The separate Python process running this snippet ended with exit code "
                          f"{process.returncode} before it finished. QGIS itself was not affected.",
                "_code": "EXEC_CRASHED",
                "stdout": _cap(stdout),
                "suggestion": "Do not send the same code again unchanged: bound the work with a "
                              "QgsFeatureRequest or use a dedicated tool.",
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
                "suggestion": "Its lines already ran: do not resend it unchanged. Put plain text in result.",
                "isolated": True,
            }
        return {"_fallback": "setup", "detail": detail}
    finally:
        remove_tree(work_dir)


def run(args: dict, run_in_qgis, api_help) -> dict:
    """Worker thread: plan on the main thread, then run the child or QGIS."""




    code = str(args.get("code") or "")
    if not isolation_enabled():

        return _in_qgis(args, run_in_qgis)
    cancel_check = net.current_cancel_check()
    try:
        planned = run_on_main_thread(plan, code, timeout=30)
    except Exception as exc:  # noqa: BLE001 - a plan that failed is a reason to run inside QGIS
        planned = {"reason": f"the isolation plan failed: {exc}"}
    if planned.get("reason"):
        return _in_qgis(args, run_in_qgis)
    try:
        outcome = run_child(code, planned, python_for_child(), cancel_check, code_guard.DEFAULT_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - a runner that failed is reported, never raised at the executor
        outcome = {"_fallback": "setup", "detail": f"{type(exc).__name__}: {exc}"}
    fallback = outcome.pop("_fallback", "")
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
            "suggestion": "Send only the project change as a new execute_code call, on its own; the "
                          "lines before it already ran.",
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
    help_text = ""
    if exc_type in ("AttributeError", "NameError", "TypeError"):
        try:
            exc = {"AttributeError": AttributeError, "NameError": NameError, "TypeError": TypeError}[exc_type](
                exc_message)
            help_text = run_on_main_thread(api_help, exc, timeout=10)
        except Exception:  # noqa: BLE001 - a hint that cannot be built is a hint the client does without
            help_text = ""
    if not help_text:



        try:
            from .advanced_code import code_help

            help_text = code_help(exc_type, exc_message)
        except Exception:  # noqa: BLE001 - a hint that cannot be built is one the client does without
            help_text = ""
    if help_text:
        outcome["api"] = help_text
        outcome.setdefault("suggestion", help_text)
    return outcome


def _in_qgis(args: dict, run_in_qgis) -> dict:
    """The in-process path, reached from a worker thread or a failed child."""
    return run_on_main_thread(run_in_qgis, args, timeout=code_guard.DEFAULT_TIMEOUT_S + 60)
