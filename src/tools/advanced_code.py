# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""execute_code: the in-QGIS runner, its guard rails and the API hints a wrong PyQGIS call gets back."""
from __future__ import annotations

import ast
import difflib
import importlib
import re
import traceback
from collections import OrderedDict

import qgis
from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsMapSettings,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)
from qgis.utils import iface

from ..core import code_guard
from ..core.background import on_main_thread, run_on_main_thread
from ..core.logger import log_debug
from ..core.security import safe_read_text, validate_path
from ..core.serialization import cut_string
from . import isolated_code

_QT_CORE_NAMES = ("QVariant", "Qt", "QSize", "QSizeF", "QPointF", "QPoint", "QRectF", "QRect",
                  "QDate", "QTime", "QDateTime", "QUrl")
_QT_GUI_NAMES = ("QColor", "QFont", "QImage", "QPainter", "QBrush", "QPen", "QTransform")


def _qt_names() -> dict:
    """The Qt classes execute_code offers by name, none of them in code_guard.DENIED_NAMES."""
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
    return out








_FAILED_SNIPPETS: OrderedDict[str, dict] = OrderedDict()
_FAILED_SNIPPETS_MAX = 32


def _snippet_key(code: str) -> str:
    from ..core import layer_order

    try:
        return f"{layer_order.current_run() or ''}\n{code}"[:8000]
    except Exception:  # noqa: BLE001 - a key we cannot build is a snippet we do not remember
        return ""


def _repeat_snippet(key: str) -> dict | None:
    first = _FAILED_SNIPPETS.get(key) if key else None
    if first is None:
        return None
    _FAILED_SNIPPETS.move_to_end(key)
    first["attempts"] = first.get("attempts", 1) + 1
    return {
        "executed": False,
        "_error": (f"This exact snippet already raised in this answer: {first['error']} "
                   f"It was not run again, because not a character of it changed."),
        "_code": "REPEATED_FAILURE",
        "attempts": first["attempts"],
        "first_failure": first["error"],
        "api": first.get("api", ""),
        "suggestion": (first.get("suggestion")
                       or "Rewrite the snippet around the error above, or use the tool that does this "
                          "without code (search_tools). Sending it again cannot work."),
    }


def _remember_snippet(key: str, out: dict) -> None:
    if not key or not isinstance(out, dict):
        return
    message = str(out.get("_error") or "")
    if not message or out.get("_code") not in ("EXEC_RUNTIME_ERROR", "EXEC_TIMEOUT"):
        return
    _FAILED_SNIPPETS[key] = {"error": cut_string(message, 400),
                             "api": cut_string(str(out.get("api") or ""), 400),
                             "suggestion": cut_string(str(out.get("suggestion") or ""), 300),
                             "attempts": 1}
    _FAILED_SNIPPETS.move_to_end(key)
    while len(_FAILED_SNIPPETS) > _FAILED_SNIPPETS_MAX:
        _FAILED_SNIPPETS.popitem(last=False)


def _execute_code(args: dict) -> dict:
    """Run a snippet, in its own process when the snippet only reads or computes."""








    code = str(args.get("code") or "")
    key = _snippet_key(code)
    repeat = _repeat_snippet(key)
    if repeat is not None:
        return repeat
    if not on_main_thread():



        try:
            wrong = run_on_main_thread(_preflight, code, timeout=10)
        except Exception:  # noqa: BLE001 - a check that cannot run never stops a snippet
            wrong = None
        if wrong:
            return wrong
        out = isolated_code.run(args, run_in_qgis=_run_code_in_qgis, api_help=_api_help)
    else:
        out = run_on_main_thread(_run_code_in_qgis, args)
    _remember_snippet(key, out)
    return out




_SIP_LAYER_CONTAINERS_WARM = [False]


def _run_code_in_qgis(args: dict) -> dict:
    """Run code on the QGIS main thread inside the code guard (core/code_guard.py)."""





    code = args.get("code", "")
    if not code.strip():
        return {"_error": "No code provided"}



    refused = code_guard.refusal_for(code)
    if refused:
        return {"_error": refused["error"], "_code": refused["code"], "suggestion": refused["suggestion"]}




    wrong = _preflight(code)
    if wrong:
        return wrong

    stdout_capture = code_guard.CappedOutput()

    def _print(*args, **kwargs):
        kwargs.setdefault("file", stdout_capture)
        print(*args, **kwargs)






    namespace = {k: v for k, v in vars(qgis.core).items()
                 if (k.startswith("Qgs") or k == "NULL") and k not in code_guard.DENIED_NAMES}






    namespace.update(_qt_names())
    try:
        import processing as _processing
        namespace["processing"] = _processing
    except ImportError:
        pass
    namespace.update({
        "__builtins__": code_guard.build_safe_builtins(),
        "iface": iface,
        "QgsProject": QgsProject,
        "QgsApplication": QgsApplication,
        "QgsVectorLayer": QgsVectorLayer,
        "QgsRasterLayer": QgsRasterLayer,
        "QgsCoordinateReferenceSystem": QgsCoordinateReferenceSystem,
        "QgsMapSettings": QgsMapSettings,
        "qgis": qgis,
        "project": QgsProject.instance(),
        "print": _print,
        "read_text": safe_read_text,
        "validate_path": validate_path,
    })

    if not _SIP_LAYER_CONTAINERS_WARM[0]:
        _SIP_LAYER_CONTAINERS_WARM[0] = True


        QgsProject.instance().mapLayers()
        QgsProject.instance().mapLayersByName("")

    try:
        code_guard.run_with_timeout(
            lambda: exec(code, namespace),  # nosec B102 - execute_code requires plugin permission approval
            code_guard.DEFAULT_TIMEOUT_S,
        )


        out = {"executed": True}
        _put_capped(out, "stdout", stdout_capture.getvalue())
        if "result" in namespace:
            _put_capped(out, "result", str(namespace["result"]))
        else:
            out["result_set"] = False
        return out
    except code_guard.CodeTimeout:
        return {
            "executed": False,
            "_error": f"execute_code stopped after {code_guard.DEFAULT_TIMEOUT_S} s.",
            "_code": "EXEC_TIMEOUT",
            "stdout": _cap(stdout_capture.getvalue()),
            "suggestion": "Narrow the work (fewer features, one layer) or use run_processing with async=true.",
        }
    except Exception as e:



        out = {
            "executed": False,
            "_error": f"execute_code raised {type(e).__name__}: {e}",
            "_code": "EXEC_RUNTIME_ERROR",
            "stdout": _cap(stdout_capture.getvalue()),
            "traceback": _cap(traceback.format_exc()),
        }



        number, source = _failing_line(code)
        if number:
            out["failed_at"] = f"line {number}: {source}"
        help_text = _api_help(e)
        if help_text:
            out["api"] = help_text



            out.setdefault("suggestion", help_text)
        return out








_SPATIAL_INDEX_HINT = (
    "QgsSpatialIndex takes a layer, a feature source or a feature iterator, never a list: "
    "QgsSpatialIndex(layer.getFeatures()) or QgsSpatialIndex(layer). To index features you already "
    "hold in a list, build an empty QgsSpatialIndex() and call index.addFeature(f) for each one.")


def _called_name(node) -> str:
    func = getattr(node, "func", None)
    if isinstance(func, ast.Name):
        return func.id
    return getattr(func, "attr", "") or ""


def _list_names(tree) -> set:
    """Names this snippet binds to a list, so a list passed on is recognisable."""
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
    """The literal names of ``mapLayersByName('x')[0]``, which raises IndexError when absent."""
    wanted = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        call = node.value
        if not isinstance(call, ast.Call) or _called_name(call) != "mapLayersByName":
            continue
        if len(call.args) != 1:
            continue
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value.strip():
            wanted.append((first.value, getattr(node, "lineno", 0)))
    return wanted


def _preflight(code: str) -> dict | None:
    """Refuse a snippet whose failure is already decided, or None to run it."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    except Exception:  # noqa: BLE001 - anything we cannot parse is simply run
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
                    "api": _SPATIAL_INDEX_HINT,
                    "suggestion": _SPATIAL_INDEX_HINT,
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
                "suggestion": (("Closest names: " + ", ".join(near) + ". ") if near else "")
                              + "mapLayersByName matches the name exactly; list_layers gives the names and ids.",
            }
    except Exception:  # noqa: BLE001 - a check that cannot run never stops a snippet
        return None
    return None














_ATTR_RE = re.compile(r"'([A-Za-z_][\w]*)'(?: object)? has no attribute '([A-Za-z_][\w]*)'")



_NAME_RE = re.compile(r"name '([A-Za-z_][\w]*)' is not defined")



_CALL_RE = re.compile(r"([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\(\)")
_ENUM_RE = re.compile(r"member of enum '([A-Za-z_][\w]*)' is expected not '([A-Za-z_][\w]*)'")
_API_MODULES = ("qgis.core", "qgis.gui", "qgis.PyQt.QtCore", "qgis.PyQt.QtGui", "qgis.PyQt.QtWidgets")
_API_HELP_CHARS = 600




_LIST_INDEX_HINT = (
    "Something came back empty and was indexed anyway: mapLayersByName(name) returns [] unless the name "
    "matches exactly, and getFeatures(), selectedFeatures() and a [f for f in ...] list are empty when the "
    "filter or the selection matched nothing. Test the list, or put its len() in result, before [0].")
_SIGNATURE_HINTS: tuple[tuple[str, str], ...] = (
    ("QgsSpatialIndex(): arguments did not match", _SPATIAL_INDEX_HINT),
    ("object has no attribute 'isNullable'",
     "QgsField has no isNullable(). A field's constraints are field.constraints().constraints(), tested "
     "against QgsFieldConstraints.ConstraintNotNull; field.typeName(), length() and precision() do exist."),
    ("QgsVectorLayer(): arguments did not match",
     "QgsVectorLayer takes strings: QgsVectorLayer(path_or_uri, name, 'ogr' or 'memory'). A layer already "
     "in the project comes from QgsProject.instance().mapLayersByName(name), not from the constructor."),
    ("QgsFeatureRequest(): arguments did not match",
     "QgsFeatureRequest() takes no argument; add the filter with setFilterExpression(text), "
     "setFilterFids(list), setFilterRect(rectangle) or setSubsetOfAttributes(list, fields)."),
)

_TYPE_HINTS = {
    "IndexError": _LIST_INDEX_HINT,
    "KeyError": ("That key is not there: a feature's attribute is f['field'] with a field name the layer "
                 "really has (print [fl.name() for fl in layer.fields()] into result first)."),
    "StopIteration": ("A QgsFeatureIterator is walked once: call layer.getFeatures() again, or keep the "
                      "features in a list before you read them twice."),
    "ZeroDivisionError": "Guard the divisor: a count, an area or a length can be zero on an empty selection.",
}


def code_help(exc_type: str, message: str) -> str:
    """The hint for an exception known by its text alone, or ""."""




    text = str(message or "")
    for needle, suggestion in _SIGNATURE_HINTS:
        if needle in text:
            return suggestion
    return _TYPE_HINTS.get(str(exc_type or ""), "")


def _scoped_enum(cls, wanted: str) -> str:
    """``QFont.Weight.Bold`` for a flat ``QFont.Bold``, or ""."""





    try:
        holders = [name for name in dir(cls) if not name.startswith("_")]
    except Exception:  # noqa: BLE001 - a class that will not list itself gives no scope
        return ""
    for holder_name in holders:
        try:
            holder = getattr(cls, holder_name, None)
        except Exception as exc:  # noqa: S112 - an attribute that raises is not a scope
            log_debug(f"_scoped_enum: getattr({holder_name!r}) failed: {exc}")
            continue
        if not isinstance(holder, type):
            continue
        member = getattr(holder, wanted, None)
        if member is None or isinstance(member, type) or callable(member):
            continue
        return f"{getattr(cls, '__name__', 'the class')}.{holder_name}.{wanted}"
    return ""


def _failing_line(code: str) -> tuple:
    """The snippet's own line that raised, as ``(number, text)``, or ``(0, "")``."""
    import sys

    try:
        frames = traceback.extract_tb(sys.exc_info()[2])
        lines = code.splitlines()
        for frame in reversed(frames):
            if frame.filename == "<string>" and frame.lineno and 0 < frame.lineno <= len(lines):
                return frame.lineno, lines[frame.lineno - 1].strip()[:200]
    except Exception:  # noqa: BLE001 - a line we cannot find is a line we do not report
        return 0, ""
    return 0, ""


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
    """An enum by name, whether it sits at module level or under ``Qgis``."""
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
    """The module a bare name lives in, or ''."""
    for module_name in _API_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        if getattr(module, name, None) is not None:
            return module_name
    return ""


_FAMILY_SHOWN = 8


def _api_family(cls_name: str, wanted: str, public: list) -> str:
    """The members that share the verb *wanted* starts with, or ''."""
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
    shown = ", ".join(kin[:_FAMILY_SHOWN])
    more = "" if len(kin) <= _FAMILY_SHOWN else f", and {len(kin) - _FAMILY_SHOWN} more"
    return f"{cls_name} has no {wanted}. Its {verb} methods are: {shown}{more}."


def _api_help(exc: Exception) -> str:
    """The real signature, or the names that do exist, for a call that was wrong."""
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
                return (f"{match.group(1)} has no {match.group(2)} of its own: it is scoped as {scoped} "
                        "in this QGIS. Write the scoped form.")
            public = [a for a in dir(cls) if not a.startswith("_")]
            near = difflib.get_close_matches(match.group(2), public, n=5, cutoff=0.6)
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
            return (f"{wanted} is real but execute_code does not bind it. It is in {module_name}: "
                    f"write from {module_name} import {wanted}, or {module_name}.{wanted} where "
                    "qgis is already bound.")
        if isinstance(exc, TypeError):
            enum = _ENUM_RE.search(text)
            if enum:
                family = _api_enum(enum.group(1))
                members = list(getattr(family, "__members__", {}) or {})
                if members:
                    return (f"{enum.group(2)} belongs to another family. {enum.group(1)} is: "
                            + ", ".join(members[:12]) + ("." if len(members) <= 12 else ", ..."))
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
    except Exception:  # noqa: BLE001 - a hint that cannot be built is a hint the model does without
        return ""
    return ""




_MAX_OUTPUT = 12_000


def _cap(text: str) -> str:
    return cut_string(text, _MAX_OUTPUT)


def _put_capped(out: dict, key: str, text: str) -> None:
    """Store the text cut to head and tail, with its true length when it was cut."""
    out[key] = _cap(text)
    if len(text) > _MAX_OUTPUT:
        out[f"{key}_chars"] = len(text)

