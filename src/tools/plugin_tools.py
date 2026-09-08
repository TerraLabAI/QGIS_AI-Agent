# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Plugin management tools: list, info, reload, and trigger plugin actions."""
from __future__ import annotations

from qgis.utils import active_plugins, available_plugins, pluginMetadata, plugins, reloadPlugin

from ..core.logger import log_warning
from ..core.policy import ToolPolicyGroup




from ..core.qgis_plugins import explore_menu as _explore_menu
from ..core.qgis_plugins import menu_of as _menu_of
from ..core.tool_registry import Tool, ToolRegistry


def _known() -> dict:
    """What the server said about QGIS plugins, keyed by folder, or empty."""






    try:
        from ..ui.shared import known_plugins_by_folder

        return known_plugins_by_folder()
    except Exception:  # noqa: BLE001
        return {}


def _with_skill(name: str, result: dict) -> dict:
    """Attach how to drive this plugin, when we have been taught how."""
    row = _known().get(str(name))
    skill = str((row or {}).get("skill") or "").strip()
    if skill:
        result["how_to_use"] = skill
    return result


def skill_for_algorithm(algorithm_id: str) -> tuple[str, str]:
    """The plugin behind a Processing algorithm id, and how to drive it."""






    provider = str(algorithm_id or "").split(":", 1)[0].strip()
    if not provider:
        return ("", "")
    for folder, row in _known().items():
        if provider in (row.get("providers") or ()):
            return (folder, str(row.get("skill") or "").strip())
    return ("", "")


def _installed() -> set:
    """Every plugin folder QGIS knows about, active or not."""
    folders = set()
    try:
        folders = set(available_plugins) if available_plugins else set()
    except Exception:  # nosec B110 - plugin metadata is optional
        pass
    folders.update(active_plugins or [])
    return folders


def _display_name(folder: str) -> str:
    """The name in the plugin's metadata.txt, or "" when it has none to give."""
    try:
        return str(pluginMetadata(folder, "name") or "")
    except Exception:  # nosec B110 - plugin metadata is optional
        return ""


def _squash(text: str) -> str:
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def resolve_plugin(name: str) -> str:
    """The installed folder a model meant, or "" when nothing matches."""











    wanted = str(name or "").strip()
    if not wanted:
        return ""
    folders = _installed()
    if wanted in folders:
        return wanted
    squashed = _squash(wanted)
    if not squashed:
        return ""
    for folder in sorted(folders):
        if _squash(folder) == squashed:
            return folder
    for folder in sorted(folders):
        if _squash(_display_name(folder)) == squashed:
            return folder




    starts = [f for f in sorted(folders)
              if _squash(_display_name(f)).startswith(squashed) or _squash(f).startswith(squashed)]
    return starts[0] if len(starts) == 1 else ""


def _providers_of(folder: str) -> dict:
    """Processing provider ids this plugin registered, read from the live registry."""





    try:
        from qgis.core import QgsApplication

        registry = QgsApplication.processingRegistry()
    except Exception:  # noqa: BLE001 - outside QGIS there is no registry
        return {}
    found = {}
    for provider in registry.providers() or []:
        module = str(getattr(type(provider), "__module__", "") or "")
        if module.split(".")[0] == str(folder):


            found[provider.id()] = len(provider.algorithms() or [])
    return found


def _reach(folder: str) -> dict:
    """How this plugin can be driven here, and the plain answer when it cannot."""







    menu = None
    try:
        menu = _menu_of(folder)
    except Exception:  # noqa: BLE001 - a broken plugin must not break the answer
        menu = None
    providers = _providers_of(folder)
    reach = {"menu": menu is not None, "processing_providers": providers}
    if menu is None and not any(providers.values()):
        reach["note"] = (
            "Loaded, but it registers no menu and no Processing algorithms in this QGIS, "
            "so there is no way in from here. Usually a Python package it needs is missing; "
            "get_message_log has the reason. Tell the user rather than offering a route."
        )
    return reach


def register_plugin_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="list_plugins",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_list_plugins,
    ))

    registry.register(Tool(
        name="get_plugin_info",
        input_schema={
            "type": "object",
            "properties": {
                "plugin_name": {"type": "string"},
            },
            "required": ["plugin_name"],
        },
        handler=_get_plugin_info,
    ))

    registry.register(Tool(
        name="trigger_plugin_action",
        input_schema={
            "type": "object",
            "properties": {
                "plugin_name": {
                    "type": "string",
                },
                "action_path": {
                    "type": "string",
                },
            },
            "required": ["plugin_name"],
        },
        handler=_trigger_plugin_action,
    ))

    registry.register(Tool(
        name="trigger_menu_action",
        input_schema={
            "type": "object",
            "properties": {
                "action_path": {
                    "type": "string",
                },
                "list_only": {
                    "type": "boolean",
                },
                "defer": {
                    "type": "boolean",
                },
            },
            "required": [],
        },
        handler=_trigger_menu_action,
        policy_group=ToolPolicyGroup.PROJECT_WRITE,
    ))

    registry.register(Tool(
        name="reload_plugin",
        input_schema={
            "type": "object",
            "properties": {
                "plugin_name": {"type": "string"},
            },
            "required": ["plugin_name"],
        },
        handler=_reload_plugin,
        destructive=True,
    ))

    registry.register(Tool(
        name="get_tool_result",
        input_schema={
            "type": "object",
            "properties": {
                "request_token": {
                    "type": "string",
                },
                "wait_seconds": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 60,
                },
            },
            "required": ["request_token"],
        },
        handler=_get_tool_result,
    ))


def _paths_to(root_menu, label: str, key, limit: int = 3) -> list:
    """Full menu paths whose leaf is ``label``, searched from the top."""




    wanted = key(label)
    found: list = []






    def walk(menu, prefix: str, depth: int) -> None:
        if len(found) >= limit or depth > 6:
            return
        for action in menu.actions():
            text = str(action.text() or "").replace("&", "").strip()
            if not text:
                continue
            path = f"{prefix}/{text}" if prefix else text
            submenu = action.menu()
            if submenu is not None:
                walk(submenu, path, depth + 1)
            elif key(text) == wanted:
                found.append(path)
            if len(found) >= limit:
                return

    walk(root_menu, "", 0)
    return found


def _resolve_menu_action(root_menu, action_path: str):
    """Walk a '/'-separated action_path from a QMenu (or QMenuBar, same .actions() interface) down to a leaf QAction."""







    import difflib
    import re








    def key(text: str) -> str:
        """A label reduced to what a match should care about."""





        return re.sub(r"\s*/\s*", "/", str(text or "").replace("&", "")).strip().lower()

    parts = [p.strip() for p in action_path.split("/")]
    current_menu = root_menu
    i = 0
    while i < len(parts):
        part = parts[i]
        span = 1
        for length in range(len(parts) - i, 0, -1):
            candidate = key("/".join(parts[i:i + length]))
            if any(key(a.text()) == candidate for a in current_menu.actions()):
                part, span = "/".join(parts[i:i + length]).strip(), length
                break
        part_lower = key(part)
        found = False
        for action in current_menu.actions():
            text = action.text().replace("&", "")
            if key(text) == part_lower:
                if i + span >= len(parts):
                    if action.menu():
                        return None, {
                            "_error": f"'{action_path}' is a submenu, not a triggerable action.",
                            "available_actions": _explore_menu(action.menu(), action_path + "/"),
                        }
                    return action, None
                if not action.menu():
                    return None, {"_error": f"'{part}' is not a submenu, cannot navigate further."}
                current_menu = action.menu()
                found = True
                break
        if found:
            i += span
            continue
        siblings = [a.text().replace("&", "") for a in current_menu.actions() if a.text().replace("&", "")]
        suggestions = difflib.get_close_matches(part, siblings, n=3, cutoff=0.4)
        msg = f"Action '{part}' not found in menu."





        deeper = _paths_to(root_menu, part, key)
        if deeper:
            msg += f" It is at {', '.join(repr(d) for d in deeper)}; pass the whole path."
        elif suggestions:
            msg += f" Did you mean: {', '.join(repr(s) for s in suggestions)}?"
        else:
            msg += f" Available: {', '.join(repr(s) for s in siblings[:15])}"
        return None, {"_error": msg}
    return None, {"_error": f"Could not resolve action path: {action_path}"}


def _trigger_plugin_action(args: dict) -> dict:
    asked = args["plugin_name"]
    name = resolve_plugin(asked) or asked
    action_path = args.get("action_path")
    plugin = plugins.get(name)
    if not plugin:
        return {"_error": f"Plugin '{asked}' is not loaded. Check list_plugins for available names."}

    menu = _menu_of(name)
    if menu is None:




        return _with_skill(name, {
            "_error": f"Plugin '{name}' registers no menu, so there is no action to trigger.",
            "code": "INVALID_ARGS",
            "suggestion": "Look for a Processing provider from this plugin with list_algorithms, "
                          "or open its dock with trigger_menu_action under View > Panels.",
        })

    available = _explore_menu(menu)

    if not action_path:
        return _with_skill(name, {
            "plugin": name, "available_actions": available,
            "hint": "Call again with action_path to trigger one. A label containing a slash "
                    "(Import KML/KMZ) is passed whole; the path still splits on '/' between levels.",
        })

    target_action, error = _resolve_menu_action(menu, action_path)
    if error:
        return error

    target_action.trigger()
    return {"triggered": action_path, "plugin": name}


def _trigger_menu_path(action_path: str) -> None:
    """Find the action again and trigger it, or do nothing if it has gone."""




    from qgis.utils import iface

    try:
        window = iface.mainWindow() if iface is not None else None
        action, error = _resolve_menu_action(window.menuBar(), action_path) if window else (None, True)
        if action is not None and not error:
            action.trigger()
    except Exception as exc:  # noqa: BLE001 - a deferred trigger never takes QGIS with it
        log_warning(f"Deferred menu action {action_path!r} was not triggered: {exc}")


def _trigger_menu_action(args: dict) -> dict:
    from qgis.utils import iface

    action_path = args.get("action_path")
    list_only = args.get("list_only", False)

    menu_bar = iface.mainWindow().menuBar()



    if not action_path or list_only:
        return {
            "available_actions": _explore_menu(menu_bar),
            "hint": "Call again with action_path to trigger one, e.g. 'Layer/Add Layer/Add Vector Layer...'.",
        }

    target_action, error = _resolve_menu_action(menu_bar, action_path)
    if error:
        return error

    if args.get("defer"):




        from qgis.PyQt.QtCore import QTimer



















        QTimer.singleShot(0, lambda: _trigger_menu_path(action_path))
        return {
            "triggered": action_path,
            "deferred": True,
            "note": "Wait ~1s, then inspect with accessibility_snapshot/screenshot; dismiss a modal with close_dialog.",
        }

    target_action.trigger()
    return {"triggered": action_path}


def _list_plugins(args: dict) -> dict:
    known = _known()
    plugins = []
    all_plugins = set()
    try:
        all_plugins = set(available_plugins) if available_plugins else set()
    except Exception:  # nosec B110 - plugin metadata is optional
        pass
    all_plugins.update(active_plugins or [])

    for name in sorted(all_plugins):
        info = {
            "name": name,
            "enabled": name in (active_plugins or []),
        }
        try:
            info["display_name"] = pluginMetadata(name, "name") or name
            info["version"] = pluginMetadata(name, "version") or "unknown"
        except Exception:
            info["display_name"] = name
            info["version"] = "unknown"




        if name in known:
            info["how_to_use_available"] = True
        plugins.append(info)

    return {
        "plugins": plugins,
        "count": len(plugins),
        "hint": "This is the whole list: a plugin absent from it is not installed, and one "
                "present is. For any row with how_to_use_available, get_plugin_info returns "
                "which tool drives it and the menu path or algorithm ids to use. If the user "
                "named a plugin, drive that one; if a built-in tool does the job better, say "
                "in one line which you used instead and why.",
    }


def _get_plugin_info(args: dict) -> dict:
    asked = args["plugin_name"]
    name = resolve_plugin(asked)
    if not name:
        return {"_error": f"Plugin not found: {asked}",
                "suggestion": "list_plugins gives the folder name, which is what this tool takes."}
    fields = ["name", "version", "description", "author", "email", "homepage", "category", "qgisMinimumVersion"]
    enabled = name in (active_plugins or [])
    info = {"plugin_dir": name, "enabled": enabled}
    if enabled:
        info["registers"] = _reach(name)
    for field in fields:
        try:
            val = pluginMetadata(name, field)
            if val:
                info[field] = val
        except Exception:  # nosec B110 - plugin metadata is optional
            pass

    return _with_skill(name, info)


def _reload_plugin(args: dict) -> dict:
    asked = args["plugin_name"]
    name = resolve_plugin(asked) or asked
    if name in ("AI_Agent", "QGIS_AI-Agent", "QGIS_AI-Agent-Team", "QGIS_AI_Agent", "qgis_ai_agent"):
        return {
            "_error": "Cannot reload AI Agent from inside a run. Reload it from the QGIS plugin manager.",
            "code": "PERMISSION_DENIED",
        }

    if name not in (active_plugins or []):
        return {"_error": f"Plugin '{name}' is not active. Only active plugins can be reloaded."}

    try:
        reloadPlugin(name)
    except Exception as e:
        return {"_error": f"Failed to reload plugin: {e}"}

    return {"reloaded": name}


def _get_tool_result(args: dict) -> dict:
    """Nothing parks a result any more: an oversized one is cut with a count, a late one is dropped."""


    token = str(args.get("request_token") or "").strip()
    return {
        "_error": f"Unknown or expired request token '{token}'.",
        "code": "TOKEN_UNKNOWN",
        "suggestion": ("Results are no longer paged: an oversized result comes back cut, with the count of "
                       "what was left out. Run the original tool again with narrower arguments."),
    }
