# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
































from __future__ import annotations

import functools
import threading
from dataclasses import dataclass

from ..core import code_effects as ce
from ..core import code_namespace, code_tripwire


_NOT_FROM_CODE = frozenset({"execute_code", "ask_user", "verify_run", "call_tool",
                            "search_tools", "install_dependency", "reload_plugin", "trigger_plugin_action",
                            "trigger_menu_action"})


@dataclass
class CallContext:


    granted: str = ce.D
    chat: str = ""
    timeout_s: float = 120.0
    registry: object = None

    restore_previous: object = None




_LOCAL = threading.local()


def _stack() -> list[CallContext]:
    stack = getattr(_LOCAL, "stack", None)
    if stack is None:
        stack = _LOCAL.stack = []
    return stack


class calling:  # noqa: N801


    def __init__(self, context: CallContext) -> None:
        self.context = context

    def __enter__(self) -> CallContext:
        _stack().append(self.context)
        return self.context

    def __exit__(self, *exc) -> bool:
        stack = _stack()
        if stack and stack[-1] is self.context:
            stack.pop()
        return False


def current() -> CallContext:

    stack = _stack()
    return stack[-1] if stack else CallContext()




_KEPT: dict[str, dict] = {}


_DROPPED: dict[str, dict] = {}
_DROPPED_MAX = 200
_WATCHING = [False]


def forget(chat: str | None = None) -> None:

    if chat is None:
        _KEPT.clear()
        _DROPPED.clear()
    else:
        _KEPT.pop(chat, None)
        _DROPPED.pop(chat, None)


def _watch_project() -> None:
    if _WATCHING[0]:
        return
    try:
        from qgis.core import QgsProject

        QgsProject.instance().cleared.connect(forget)
        _WATCHING[0] = True
    except Exception:  # noqa: BLE001  # nosec B110
        pass


def shutdown() -> None:







    if _WATCHING[0]:
        try:
            from qgis.core import QgsProject

            QgsProject.instance().cleared.disconnect(forget)
        except Exception:  # noqa: BLE001  # nosec B110
            pass
        _WATCHING[0] = False
    forget()


def kept(chat: str) -> dict:
    _watch_project()
    return dict(_KEPT.get(chat, {}))


def keep(chat: str, namespace: dict, bound: set, lost: dict | None = None) -> None:





    names = {}
    dropped = dict(_DROPPED.get(chat, {}))
    for key, value in namespace.items():
        if key.startswith("__") or key in bound or key in ("result", "show_files"):
            continue
        if code_namespace.keepable(value):
            names[key] = value
        else:
            dropped[key] = type(value).__name__
    dropped.update(lost or {})
    for key in names:
        dropped.pop(key, None)
    _KEPT[chat] = names
    _DROPPED[chat] = dict(list(dropped.items())[-_DROPPED_MAX:])


def dropped_help(chat: str, name: str) -> str:

    held = _DROPPED.get(chat, {}).get(str(name or ""))
    if held is None:
        return ""
    return (f"{name} was made by an earlier snippet of this chat but not kept: it held a {held or 'value'}, "
            "which is not kept between calls (QGIS owns it and can free it, or it could not come back from "
            f"the separate process). A snippet rebuilds {name} from a layer name or id, a path "
            "or plain values kept from before.")




def bindings(iface, project) -> dict:

    from qgis.core import edit

    canvas = iface.mapCanvas() if iface is not None else None
    try:
        active = iface.activeLayer() if iface is not None else None
    except Exception:  # noqa: BLE001
        active = None
    from ..core.output_paths import default_folder

    try:
        exports = default_folder()
    except Exception:  # noqa: BLE001
        exports = ""
    return {
        "iface": iface, "project": project, "canvas": canvas, "active": active,
        "layer": code_namespace.layer_lookup(project), "layers": lambda: code_namespace.ordered_layers(project),
        "edit": edit, "tools": Tools(),





        "exports": exports,
    }




def _registry():
    return current().registry


def tool_code_class(name: str, args: dict | None = None, registry=None) -> str | None:

    registry = registry if registry is not None else _registry()
    tool = registry.get_tool(name) if registry is not None else None
    if tool is None or name in _NOT_FROM_CODE or tool.catalog or tool.open_world:
        return None
    args = args if isinstance(args, dict) else {}
    background = tool.background
    try:
        if background if not callable(background) else (args and background(args)):
            return None
    except Exception:  # noqa: BLE001
        return None
    from . import guards
    from .danger import effective_danger

    if guards.always_confirm(name, args) or guards.unvouched_urls(name, args):
        return None
    try:
        from . import cost_guard

        if cost_guard.costly_label(name, args):
            return None
    except ImportError:
        pass
    danger = effective_danger(name, args) if args else tool.danger
    cls = {"read": ce.R, "write": ce.P}.get(danger, ce.FW)
    if name in guards.DATA_MUTATORS:

        cls = ce.higher(cls, ce.FW)
    for key in guards.WRITE_PATH_ARGS.get(name, ()):
        if isinstance(args.get(key), str) and args[key].strip():
            cls = ce.higher(cls, code_tripwire.file_class(args[key])[0])
    return cls


class Tools:


    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        if tool_code_class(name) is None:
            raise AttributeError(f"tools.{name} is not callable from code: it fetches, spends credits, runs "
                                 "in the background or always asks the user.")
        return functools.partial(self._call, name)

    def __dir__(self):
        registry = _registry()
        names = registry.tool_names() if registry is not None else []
        return [n for n in names if tool_code_class(n, registry=registry) is not None]

    @staticmethod
    def _call(name: str, arguments: dict | None = None, **kwargs):
        from . import guards

        args = dict(arguments or {}, **kwargs)
        cls = tool_code_class(name, args)
        if cls is None:
            raise PermissionError(f"tools.{name} with these arguments is not callable from code; call the tool itself.")
        arm = code_tripwire._current()
        if arm is not None:
            arm.need(cls, f"runs the tool {name}")
        verdict = guards.check_call(name, args)
        if verdict.get("error"):
            raise ValueError(f"tools.{name}: {verdict['error']} {verdict.get('suggestion') or ''}".strip())
        registry = _registry()
        with code_tripwire.suspended():
            result = registry.execute(name, args)
        if isinstance(result, dict) and result.get("_error"):
            raise RuntimeError(f"tools.{name}: {result['_error']} {result.get('suggestion') or ''}".strip())
        return result


def snippet_traceback(tb, code: str = "") -> list[str]:





    import traceback

    lines = code.splitlines()

    def source(frame) -> str:
        text = frame.line or (lines[frame.lineno - 1] if 0 < (frame.lineno or 0) <= len(lines) else "")
        return text.strip()[:200]

    return [f"line {frame.lineno}: {source(frame)}"
            for frame in traceback.extract_tb(tb) if frame.filename == "<snippet>"]
