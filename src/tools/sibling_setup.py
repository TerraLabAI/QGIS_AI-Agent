# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






















from __future__ import annotations

import os

from ..core.logger import log, log_warning
from ._widgets import AI_EDIT_KEYS, AI_SEGMENT_KEYS






PRODUCTS = {
    "ai_segment": {
        "keys": tuple(AI_SEGMENT_KEYS),
        "product_id": "ai-segmentation",

        "commands": ("detect_auto", "auto_detect_status", "cancel_auto", "set_auto_zone"),
    },
    "ai_edit": {
        "keys": tuple(AI_EDIT_KEYS),
        "product_id": "ai-edit",
    },
}


def _name(tool: str) -> str:

    from ..ui.cross_plugin_discovery import sibling_info
    info = sibling_info(PRODUCTS[tool]["product_id"])
    return (info or {}).get("name") or PRODUCTS[tool]["product_id"]


def _label(tool: str) -> str:

    from ..ui.cross_plugin_discovery import sibling_info
    info = sibling_info(PRODUCTS[tool]["product_id"])
    return (info or {}).get("label") or _name(tool)





def _plugin_dirs() -> list[str]:
    import qgis.utils
    dirs = [path for path in (getattr(qgis.utils, "plugin_paths", None) or []) if isinstance(path, str)]
    try:
        from qgis.core import QgsApplication
        dirs.append(os.path.join(QgsApplication.qgisSettingsDirPath(), "python", "plugins"))
    except Exception:  # nosec B110
        pass
    return dirs


def _enabled_in_plugin_manager(folder: str) -> bool:

    try:
        from qgis.core import QgsSettings
        return bool(QgsSettings().value("PythonPlugins/" + folder, False, type=bool))
    except Exception:  # noqa: BLE001
        return False


def presence(keys) -> dict:






    import qgis.utils
    loaded = getattr(qgis.utils, "plugins", None) or {}
    for key in keys:
        plugin = loaded.get(key)
        if plugin is not None:
            return {"state": "loaded", "folder": key, "plugin": plugin}
    available = set(getattr(qgis.utils, "available_plugins", None) or [])
    folder = next((key for key in keys if key in available), None)
    if folder is None:
        folder = next((key for base in _plugin_dirs() for key in keys
                       if os.path.isfile(os.path.join(base, key, "metadata.txt"))), None)
    if folder is None:
        return {"state": "absent", "folder": None, "plugin": None}
    started = folder in (getattr(qgis.utils, "active_plugins", None) or [])
    enabled = started or _enabled_in_plugin_manager(folder)
    return {"state": "not_started" if enabled else "disabled", "folder": folder, "plugin": None}





def _aiseg_signed_in(plugin) -> bool | None:

    from .integration_tools import _aiseg_module
    module = _aiseg_module(plugin, "core.activation_manager")
    fn = getattr(module, "is_plugin_activated", None) if module is not None else None
    if not callable(fn):
        return None
    try:
        return bool(fn())
    except Exception:  # noqa: BLE001
        return None


def _aiedit_signed_in(plugin) -> bool | None:
    from .adapters.ai_edit_access import ACCESS
    auth = getattr(plugin, "_auth_manager", None) or ACCESS.auth(plugin)
    fn = getattr(auth, "has_activation_key", None) if auth is not None else None
    if not callable(fn):
        return None
    try:
        return bool(fn())
    except Exception:  # noqa: BLE001
        return None



_SIGNED_IN = {"ai_segment": _aiseg_signed_in, "ai_edit": _aiedit_signed_in}


def signed_in(tool: str, plugin) -> bool | None:
    return _SIGNED_IN.get(tool, _aiedit_signed_in)(plugin)




SETUP_HINT = "Call {tool} action setup now, without asking first: its permission card is the yes."


def not_running(tool: str, found: dict) -> dict:







    name, label = _name(tool), _label(tool)
    state = found.get("state")
    if state == "disabled":
        out = {"installed": True, "enabled": False, "ready": False, "state": "PLUGIN_DISABLED",
               "action_required": f"{name} is installed but switched off. setup switches it on."}
    elif state == "not_started":
        out = {"installed": True, "enabled": True, "ready": False, "state": "PLUGIN_NOT_STARTED",
               "action_required": (f"{name} is installed but did not start in this QGIS session. "
                                   "Ask the person to restart QGIS.")}
    else:
        out = {"installed": False, "ready": False, "state": "NOT_INSTALLED",
               "action_required": (f"{name} is not installed. setup opens the Plugin Manager on it; "
                                   "the person clicks Install, no restart needed.")}
    out["name"] = name
    out["label"] = label
    out["next_step"] = SETUP_HINT.format(tool=tool)
    return out


def signed_out(tool: str, status: dict) -> dict:





    name, label = _name(tool), _label(tool)
    status = dict(status)
    status.pop("hint", None)
    status.update({
        "ready": False,
        "state": "NOT_SIGNED_IN",
        "name": name,
        "label": label,
        "action_required": (f"{label} is not connected to a TerraLab account yet. setup opens its one-click "
                            "sign-in page; the person clicks Connect there, free account included."),
        "next_step": SETUP_HINT.format(tool=tool),
    })
    return status


def show_panel(tool: str) -> bool:








    found = presence(PRODUCTS[tool]["keys"])
    plugin = found.get("plugin")
    if plugin is None:
        return False
    try:
        from ..ui.cross_plugin_discovery import _activate_dock
        return bool(_activate_dock(plugin))
    except Exception as exc:  # noqa: BLE001
        log_warning(f"{_label(tool)} panel could not be opened: {exc}")
        return False


def _switch_on(folder: str) -> bool:

    import qgis.utils
    from qgis.core import QgsSettings
    if not qgis.utils.loadPlugin(folder):
        return False
    if not qgis.utils.startPlugin(folder):
        return False
    QgsSettings().setValue("PythonPlugins/" + folder, True)
    return True


def _dock_of(plugin):
    for attr in ("dock_widget", "_dock_widget"):
        dock = getattr(plugin, attr, None)
        if dock is not None:
            return dock
    return None


def _press_connect(plugin) -> bool:

    from ..ui.cross_plugin_discovery import _activate_dock
    _activate_dock(plugin)
    dock = _dock_of(plugin)
    press = getattr(dock, "_on_connect_clicked", None) if dock is not None else None
    if not callable(press):
        return False
    try:


        show_signed_out = getattr(dock, "set_activated_state", None)
        if callable(show_signed_out):
            show_signed_out(False)
        press()
        return True
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Sibling connect press failed: {exc}")
        return False


def outdated(tool: str, plugin) -> bool:

    commands = PRODUCTS[tool].get("commands") or ()
    api = getattr(plugin, "mcp_api", None)
    return bool(commands) and (api is None or any(getattr(api, name, None) is None for name in commands))


def setup(tool: str) -> dict:

    product = PRODUCTS[tool]
    name, label = _name(tool), _label(tool)
    found = presence(product["keys"])
    state = found["state"]

    if state == "absent":
        from ..ui.cross_plugin_discovery import SIBLINGS, open_plugin_manager_later
        sibling = SIBLINGS[product["product_id"]]
        open_plugin_manager_later(sibling["name"], sibling["url"])
        log(f"setup: Plugin Manager opened on {label}")
        return {
            "done": "plugin_manager_opened",
            "tell_user": (f"The Plugin Manager is open on {name}: click Install. "
                          "It starts right away, no restart."),
            "next_step": f"Stop here. When the person says it is installed, call {tool} action status.",
        }

    if state == "disabled":
        if _switch_on(found["folder"]):
            log(f"setup: {label} switched on")
            return {"done": "plugin_switched_on",
                    "next_step": f"Call {tool} action status: it names what is left, if anything."}
        return {"done": "nothing",
                "tell_user": (f"{label} could not be switched on here. Tick it in Plugins > "
                              "Manage and Install Plugins > Installed."),
                "next_step": "Stop here and wait for the person."}

    if state == "not_started":
        return {"done": "nothing",
                "tell_user": f"{label} is installed but did not start. Restart QGIS, then ask again.",
                "next_step": "Stop here and wait for the person."}

    plugin = found["plugin"]
    if outdated(tool, plugin):


        from ..ui.cross_plugin_discovery import SIBLINGS, open_plugin_manager_later
        sibling = SIBLINGS[product["product_id"]]
        open_plugin_manager_later(sibling["name"], sibling["url"])
        log(f"setup: Plugin Manager opened on {label} for an upgrade")
        return {
            "done": "plugin_manager_opened",
            "tell_user": f"The Plugin Manager is open on {name}: click Upgrade.",
            "next_step": f"Stop here. When the person says it is upgraded, call {tool} action status.",
        }
    if signed_in(tool, plugin):
        return {"done": "nothing", "state": "READY",
                "next_step": f"Nothing to set up. Call {tool} action status, then carry on."}

    if _press_connect(plugin):
        log(f"setup: {label} sign-in page opened")
        return {
            "done": "sign_in_opened",
            "tell_user": (f"A terra-lab.ai page opened in your browser: click Connect to link {label} to "
                          "your TerraLab account (free). Then tell me, and I carry on."),
            "next_step": (f"Stop here. When the person says it is done, call {tool} action status. "
                          "Never call setup again for the same step."),
        }
    from ..ui.cross_plugin_discovery import _activate_dock
    _activate_dock(plugin)
    return {
        "done": "panel_opened",
        "tell_user": f"The {label} panel is open: click Sign in there, then tell me.",
        "next_step": f"Stop here. When the person says it is done, call {tool} action status.",
    }
