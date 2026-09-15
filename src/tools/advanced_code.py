# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""execute_code: the in-QGIS runner, its guard rails and the API hints a wrong PyQGIS call gets back."""
from __future__ import annotations

import difflib
import importlib
import re
import traceback

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


def _execute_code(args: dict) -> dict:
    """Run a snippet, in its own process when the snippet only reads or computes."""








    if not on_main_thread():
        return isolated_code.run(args, run_in_qgis=_run_code_in_qgis, api_help=_api_help)
    return run_on_main_thread(_run_code_in_qgis, args)




_SIP_LAYER_CONTAINERS_WARM = [False]


def _run_code_in_qgis(args: dict) -> dict:
    """Run code on the QGIS main thread inside the code guard (core/code_guard.py)."""





    code = args.get("code", "")
    if not code.strip():
        return {"_error": "No code provided"}



    refused = code_guard.refusal_for(code)
    if refused:
        return {"_error": refused["error"], "_code": refused["code"], "suggestion": refused["suggestion"]}

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
        help_text = _api_help(e)
        if help_text:
            out["api"] = help_text
        return out














_ATTR_RE = re.compile(r"'([A-Za-z_][\w]*)'(?: object)? has no attribute '([A-Za-z_][\w]*)'")



_NAME_RE = re.compile(r"name '([A-Za-z_][\w]*)' is not defined")



_CALL_RE = re.compile(r"([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\(\)")
_ENUM_RE = re.compile(r"member of enum '([A-Za-z_][\w]*)' is expected not '([A-Za-z_][\w]*)'")
_API_MODULES = ("qgis.core", "qgis.gui", "qgis.PyQt.QtCore", "qgis.PyQt.QtGui", "qgis.PyQt.QtWidgets")
_API_HELP_CHARS = 600


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
    try:
        if isinstance(exc, AttributeError):
            match = _ATTR_RE.search(text)
            if not match:
                return ""
            cls = _api_class(match.group(1))
            if cls is None:
                return ""
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

