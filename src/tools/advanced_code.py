# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import ast
import contextlib
import difflib
import importlib
import json
import re

import qgis
from qgis.core import (
    QgsProject,
)
from qgis.utils import iface

from ..core import code_guard, code_namespace, code_owners, code_split, code_tripwire
from ..core.background import on_main_thread, run_on_main_thread
from ..core.logger import log_debug
from ..core.security import safe_read_text, validate_path
from ..core.serialization import carried_code, cut_string
from ..core.tool_registry import coded_fact
from . import code_runtime, isolated_code

_QT_CORE_NAMES = ("QVariant", "Qt", "QSize", "QSizeF", "QPointF", "QPoint", "QRectF", "QRect",
                  "QDate", "QTime", "QDateTime", "QUrl")
_QT_GUI_NAMES = ("QColor", "QFont", "QImage", "QPainter", "QBrush", "QPen", "QTransform")


_QGIS_CORE_NAMESPACE_CACHE: dict | None = None


def _qgis_core_namespace() -> dict:








    global _QGIS_CORE_NAMESPACE_CACHE
    if _QGIS_CORE_NAMESPACE_CACHE is None:
        _QGIS_CORE_NAMESPACE_CACHE = {k: v for k, v in vars(qgis.core).items()
                                      if (k.startswith("Qgs") or k in ("NULL", "Qgis"))
                                      and k not in code_guard.DENIED_NAMES}
    return _QGIS_CORE_NAMESPACE_CACHE


_QT_NAMES_CACHE: dict | None = None


def _qt_names() -> dict:






    global _QT_NAMES_CACHE
    if _QT_NAMES_CACHE is not None:
        return _QT_NAMES_CACHE
    out: dict = {}
    for module, wanted in (("QtCore", _QT_CORE_NAMES), ("QtGui", _QT_GUI_NAMES)):
        try:
            mod = __import__(f"qgis.PyQt.{module}", fromlist=["*"])
        except ImportError:
            continue
        for name in wanted:
            if name in code_guard.DENIED_NAMES:
                continue
            value = getattr(mod, name, None)
            if value is not None:
                out[name] = value
    _QT_NAMES_CACHE = out
    return out


def _execute_code(args: dict) -> dict:








    code = str(args.get("code") or "")
    if not on_main_thread():



        try:
            wrong = run_on_main_thread(_preflight, code, timeout=10)
        except Exception:  # noqa: BLE001
            wrong = None
        if wrong:
            return wrong
        return isolated_code.run(args, run_in_qgis=_run_code_in_qgis, api_help=_api_help)
    return run_on_main_thread(_run_code_in_qgis, args)




_SIP_LAYER_CONTAINERS_WARM = [False]


def _partial_state(out: dict, noted: object) -> None:










    if noted:
        out["changed"] = noted
    files = code_guard.take_written()
    if files:


        out["files_written"] = files
    out["execution_state"] = "partial" if (noted or files) else "unknown"


def _run_code_in_qgis(args: dict) -> dict:









    code = args.get("code", "")
    if not code.strip():
        return {"_error": "No code provided"}



    refused = code_guard.refusal_for(code)
    if refused:
        return {"_error": refused.pop("error"), "_code": refused.pop("code"), **refused}




    wrong = _preflight(code)
    if wrong:
        return wrong
    try:



        compiled = code_owners.compile_snippet(code, "<snippet>")
    except SyntaxError as exc:
        return {"executed": False, "_code": "INVALID_ARGS",
                "_error": f"execute_code raised SyntaxError: {exc.msg} (line {exc.lineno})",
                "suggestion": "", "hint": "code_syntax_error", "line": exc.lineno}

    context = code_runtime.current()
    project = QgsProject.instance()
    stdout_capture = code_guard.CappedOutput()

    def _print(*args, **kwargs):
        kwargs.setdefault("file", stdout_capture)
        print(*args, **kwargs)




    namespace = code_runtime.kept(context.chat)
    namespace.update(_qgis_core_namespace())



    namespace.update(_qt_names())
    _processing = code_guard.safe_processing_module()
    if _processing is not None:
        namespace["processing"] = _processing
    namespace.update(code_runtime.bindings(iface, project))
    safe_builtins = code_guard.build_safe_builtins()
    namespace.update({
        "__builtins__": safe_builtins,
        "qgis": qgis,
        "print": _print,
        "read_text": safe_read_text,
        "validate_path": validate_path,
        code_owners.HELPER: code_owners.owned_call,
    })
    for alias, module in code_namespace.prelude(code_split.names_mentioned(code), safe_builtins["__import__"]).items():
        namespace.setdefault(alias, module)
    bound = set(namespace) - set(code_runtime.kept(context.chat))

    if not _SIP_LAYER_CONTAINERS_WARM[0]:
        _SIP_LAYER_CONTAINERS_WARM[0] = True


        project.mapLayers()
        project.mapLayersByName("")

    before = code_namespace.project_state(project)
    code_guard.INVALID_GEOMETRY.clear()



    code_guard.WRITTEN_FILES[:] = []



    code_guard.harden_gdal_config()
    wire = code_tripwire.armed(context.granted) if code_tripwire.enabled() else None
    arm = None
    error = None
    edits = code_namespace.EditWatch(project)
    try:
        with wire if wire is not None else contextlib.nullcontext() as arm:
            code_guard.run_with_timeout(
                lambda: exec(compiled, namespace),  # nosec B102
                context.timeout_s,
            )
    except (code_tripwire.NeedsPermission, code_tripwire.Refused):
        pass
    except code_guard.CodeTimeout:
        error = "timeout"
    except Exception as exc:  # noqa: BLE001
        error = exc
        tb = exc.__traceback__
    finally:
        edits.close()
        code_runtime.keep(context.chat, namespace, bound)

        code_guard.restore_gdal_config()
    trip = getattr(arm, "trip", None)
    if isinstance(trip, code_tripwire.NeedsPermission):
        return {
            "executed": False,
            "_code": "NEEDS_PERMISSION",
            "_error": f"The snippet {trip.reason}, which needs the user's permission. Nothing after that call ran.",
            "needs_permission": {"class": trip.cls, "reason": trip.reason},
            "stdout": _cap(stdout_capture.getvalue()),
            "suggestion": "",
            "hint": "code_needs_permission",
        }
    if isinstance(trip, code_tripwire.Refused):
        out = {
            "executed": False,
            "_code": "PERMISSION_DENIED",
            "_error": f"execute_code stopped: the snippet {trip.reason}. Nothing after that call ran.",
            "stdout": _cap(stdout_capture.getvalue()),
            "suggestion": "",
            "hint": "code_permission_refused",
            "reason": trip.reason,
        }


        _partial_state(out, code_namespace.changed(before, project, edits))
        return out
    if error == "timeout":
        out = {
            "executed": False,
            "_error": f"execute_code stopped after {int(context.timeout_s)} s.",
            "_code": "EXEC_TIMEOUT",
            "stdout": _cap(stdout_capture.getvalue()),
            "suggestion": "",
            "hint": "code_timeout",
            "timeout_s": int(context.timeout_s),
        }
        _partial_state(out, code_namespace.changed(before, project, edits))
        return out
    if error is not None:



        out = {
            "executed": False,
            "_error": f"execute_code raised {type(error).__name__}: {error}",
            "_code": "EXEC_RUNTIME_ERROR",
            "stdout": _cap(stdout_capture.getvalue()),
        }


        if code_guard.INVALID_GEOMETRY:
            out.update(code_guard.INVALID_GEOMETRY)


        coded = carried_code(error)
        if coded is not None:
            out.setdefault("hint", coded.hint)
            for fact, value in coded.facts.items():
                out.setdefault(fact, value)


        frames = code_runtime.snippet_traceback(tb, code)
        if frames:

            out["traceback"] = "\n".join(frames)
            out["failed_at"] = frames[-1]
        help_text = ""
        if isinstance(error, NameError):
            help_text = code_runtime.dropped_help(context.chat, getattr(error, "name", None) or "")
        help_text = help_text or _api_help(error)
        if help_text:
            out["api"] = help_text
        _partial_state(out, code_namespace.changed(before, project, edits))
        return out


    out = {"executed": True}
    _put_capped(out, "stdout", stdout_capture.getvalue())
    if "result" in namespace:
        value, kind = code_namespace.result_json(namespace["result"])
        text = json.dumps(value, default=repr, ensure_ascii=False)
        if len(text) > _MAX_OUTPUT:
            out["result"] = _cap(text)
            out["result_chars"] = len(text)
        else:
            out["result"] = value
        out["result_type"] = kind
    else:
        out["result_set"] = False
    noted = code_namespace.changed(before, project, edits)
    if noted:
        out["changed"] = noted





    made = code_guard.take_written()
    if made:
        out["files_written"] = made
    shown = code_namespace.shown_files(namespace)
    if shown:
        out["files"] = [{"path": path} for path in shown]
    return out











_SPATIAL_INDEX_HINT = coded_fact(hint="api_spatial_index_list")


def _called_name(node) -> str:
    func = getattr(node, "func", None)
    if isinstance(func, ast.Name):
        return func.id
    return getattr(func, "attr", "") or ""


def _list_names(tree) -> set:

    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        is_list = isinstance(value, (ast.List, ast.ListComp))
        if isinstance(value, ast.Call) and _called_name(value) in ("list", "sorted"):
            is_list = True
        if not is_list:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _subscripted_layer_names(tree) -> list:






    wanted = []
    projects = {"project"}

    def singleton(value):
        return (
            isinstance(value, ast.Call) and not value.args and not value.keywords
            and isinstance(value.func, ast.Attribute) and value.func.attr == "instance"
            and isinstance(value.func.value, ast.Name) and value.func.value.id == "QgsProject"
        )

    for statement in tree.body:
        if isinstance(statement, (ast.Import, ast.ImportFrom)):

            if isinstance(statement, ast.ImportFrom) and statement.module == "qgis.core" and all(
                item.name == "QgsProject" and item.asname is None for item in statement.names
            ):
                continue
            break
        if not isinstance(statement, ast.Assign) or not all(
            isinstance(target, ast.Name) for target in statement.targets
        ):
            break
        value = statement.value
        targets = {target.id for target in statement.targets}
        if "QgsProject" in targets:
            break
        if singleton(value) or isinstance(value, ast.Name) and value.id in projects:
            projects.update(targets)
            continue
        if not isinstance(value, ast.Subscript) or not isinstance(value.slice, ast.Constant):
            break
        if type(value.slice.value) is not int or value.slice.value != 0:
            break
        call = value.value
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            break
        receiver = call.func.value
        if call.func.attr != "mapLayersByName" or not (
            singleton(receiver) or isinstance(receiver, ast.Name) and receiver.id in projects
        ):
            break
        if len(call.args) != 1 or call.keywords:
            break
        first = call.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            break
        wanted.append((first.value, getattr(value, "lineno", 0)))
        projects.difference_update(targets)
    return wanted


def _preflight(code: str) -> dict | None:

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    except Exception:  # noqa: BLE001
        return None
    try:
        lists = _list_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _called_name(node) != "QgsSpatialIndex" or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, (ast.List, ast.ListComp)) or (isinstance(first, ast.Name) and first.id in lists):
                return {
                    "executed": False,
                    "_error": (f"QgsSpatialIndex is given a list on line {getattr(node, 'lineno', 0)}, which "
                               f"raises TypeError: none of its overloads takes one. Nothing was run."),
                    "_code": "INVALID_ARGS",
                    "api": dict(_SPATIAL_INDEX_HINT),
                    "suggestion": "",
                }
        missing = []
        for name, lineno in _subscripted_layer_names(tree):
            if not QgsProject.instance().mapLayersByName(name):
                missing.append((name, lineno))
        if missing:
            existing = [layer.name() for layer in QgsProject.instance().mapLayers().values()][:20]
            first_name, first_line = missing[0]
            near = difflib.get_close_matches(first_name, existing, n=3, cutoff=0.5)
            return {
                "executed": False,
                "_error": (f"No layer in this project is called {first_name!r}, so "
                           f"mapLayersByName({first_name!r})[0] on line {first_line} would raise IndexError. "
                           f"Nothing was run."),
                "_code": "INVALID_ARGS",
                "layers": existing,
                "suggestion": "",
                "hint": "layer_name_not_found",
                "name": first_name,
                "line": first_line,
                **({"variant": "closest", "closest": near} if near else {}),
            }
    except Exception:  # noqa: BLE001
        return None
    return None














_ATTR_RE = re.compile(r"'([A-Za-z_][\w]*)'(?: object)? has no attribute '([A-Za-z_][\w]*)'")



_NAME_RE = re.compile(r"name '([A-Za-z_][\w]*)' is not defined")



_CALL_RE = re.compile(r"([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\(\)")
_ENUM_RE = re.compile(r"member of enum '([A-Za-z_][\w]*)' is expected not '([A-Za-z_][\w]*)'")
_API_MODULES = ("qgis.core", "qgis.gui", "qgis.PyQt.QtCore", "qgis.PyQt.QtGui", "qgis.PyQt.QtWidgets")
_API_HELP_CHARS = 600




_LIST_INDEX_HINT = coded_fact(hint="api_empty_indexed")
_SIGNATURE_HINTS: tuple[tuple[str, dict], ...] = (
    ("QgsSpatialIndex(): arguments did not match", _SPATIAL_INDEX_HINT),
    ("object has no attribute 'isNullable'", coded_fact(hint="api_field_is_nullable")),
    ("QgsVectorLayer(): arguments did not match", coded_fact(hint="api_vector_layer_ctor")),
    ("QgsFeatureRequest(): arguments did not match", coded_fact(hint="api_feature_request_ctor")),
)

_TYPE_HINTS = {
    "IndexError": _LIST_INDEX_HINT,
    "KeyError": coded_fact(hint="api_key_missing"),
    "StopIteration": coded_fact(hint="api_iterator_spent"),
    "ZeroDivisionError": coded_fact(hint="api_zero_divisor"),
}


def code_help(exc_type: str, message: str) -> dict | str:





    text = str(message or "")
    for needle, suggestion in _SIGNATURE_HINTS:
        if needle in text:
            return dict(suggestion)
    found = _TYPE_HINTS.get(str(exc_type or ""))
    return dict(found) if found else ""


def _scoped_enum(cls, wanted: str) -> str:






    try:
        holders = [name for name in dir(cls) if not name.startswith("_")]
    except Exception:  # noqa: BLE001
        return ""
    for holder_name in holders:
        try:
            holder = getattr(cls, holder_name, None)
        except Exception as exc:  # noqa: S112
            log_debug(f"_scoped_enum: getattr({holder_name!r}) failed: {exc}")
            continue
        if not isinstance(holder, type):
            continue
        member = getattr(holder, wanted, None)
        if member is None or isinstance(member, type) or callable(member):
            continue
        return f"{getattr(cls, '__name__', 'the class')}.{holder_name}.{wanted}"
    return ""


def _api_class(name: str):
    for module_name in _API_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        found = getattr(module, name, None)
        if isinstance(found, type):
            return found
    return None


def _api_enum(name: str):

    found = _api_class(name)
    if found is not None:
        return found
    for module_name in _API_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        for holder in ("Qgis", "QgsPalLayerSettings", "QgsWkbTypes"):
            parent = getattr(module, holder, None)
            nested = getattr(parent, name, None) if parent is not None else None
            if nested is not None and hasattr(nested, "__members__"):
                return nested
    return None


def _api_module_of(name: str) -> str:

    for module_name in _API_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        if getattr(module, name, None) is not None:
            return module_name
    return ""


_FAMILY_SHOWN = 8


def _api_family(cls_name: str, wanted: str, public: list) -> dict | str:

    verb = ""
    for ch in wanted:
        if ch.islower() or ch == "_":
            verb += ch
        else:
            break
    if len(verb) < 2 or verb == wanted:
        return ""
    kin = sorted(a for a in public if a.startswith(verb) and a != wanted)
    if not kin:
        return ""
    return coded_fact(hint="api_method_family", cls=cls_name, wanted=wanted, verb=verb, methods=kin[:_FAMILY_SHOWN],
                      **({"variant": "more", "more": len(kin) - _FAMILY_SHOWN} if len(kin) > _FAMILY_SHOWN else {}))


def _inside_a_word(wanted: str, name: str) -> bool:






    folded, start = name.casefold(), 0
    found = False
    while True:
        at = folded.find(wanted.casefold(), start)
        if at < 0:
            return found
        if at == 0 or name[at - 1] == "_" or name[at].isupper():
            return False
        found, start = True, at + 1


def _api_help(exc: Exception) -> dict | str:

    text = str(exc)
    known = code_help(type(exc).__name__, text)
    if known:
        return known
    try:
        if isinstance(exc, AttributeError):
            match = _ATTR_RE.search(text)
            if not match:
                return ""
            cls = _api_class(match.group(1))
            if cls is None:
                return ""
            scoped = _scoped_enum(cls, match.group(2))
            if scoped:
                return coded_fact(hint="api_enum_scoped", cls=match.group(1), name=match.group(2), scoped=scoped)
            public = [a for a in dir(cls) if not a.startswith("_")]
            near = [a for a in difflib.get_close_matches(match.group(2), public, n=5, cutoff=0.6)
                    if not _inside_a_word(match.group(2), a)]
            if not near:




                return _api_family(match.group(1), match.group(2), public)
            return f"{match.group(1)} has no {match.group(2)}. Nearest: " + ", ".join(near) + "."
        if isinstance(exc, NameError):
            match = _NAME_RE.search(text)
            if not match:
                return ""
            wanted = match.group(1)
            module_name = _api_module_of(wanted)
            if not module_name:
                return ""
            return coded_fact(hint="api_name_unbound", name=wanted, module=module_name)
        if isinstance(exc, TypeError):
            enum = _ENUM_RE.search(text)
            if enum:
                family = _api_enum(enum.group(1))
                members = list(getattr(family, "__members__", {}) or {})
                if members:
                    return coded_fact(hint="api_enum_family", member=enum.group(2), family=enum.group(1),
                                      members=members[:12], **({"variant": "more"} if len(members) > 12 else {}))
                return ""
            match = _CALL_RE.search(text)
            if not match:
                return ""
            cls = _api_class(match.group(1))
            member = getattr(cls, match.group(2), None) if cls is not None else None
            doc = (getattr(member, "__doc__", "") or "").strip()
            if not doc:
                return ""
            lines = [line.strip() for line in doc.splitlines() if line.strip()][:6]



            return cut_string("\n".join(lines), _API_HELP_CHARS)
    except Exception:  # noqa: BLE001
        return ""
    return ""




_MAX_OUTPUT = 12_000


def _cap(text: str) -> str:
    return cut_string(text, _MAX_OUTPUT)


def _put_capped(out: dict, key: str, text: str) -> None:

    out[key] = _cap(text)
    if len(text) > _MAX_OUTPUT:
        out[f"{key}_chars"] = len(text)

