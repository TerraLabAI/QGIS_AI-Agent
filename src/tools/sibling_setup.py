# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






















from __future__ import annotations

import os

from ..core.logger import log, log_warning
from ._widgets import AI_EDIT_KEYS, AI_SEGMENT_KEYS, sibling_api_version, sibling_plugin






PRODUCTS = {
    "ai_segment": {
        "keys": tuple(AI_SEGMENT_KEYS),
        "product_id": "ai-segmentation",
        "min_api": 3,

        "commands": ("detect_auto", "auto_detect_status", "cancel_auto", "set_auto_zone"),
    },
    "ai_edit": {
        "keys": tuple(AI_EDIT_KEYS),
        "product_id": "ai-edit",
        "min_api": 2,
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
    key, plugin = sibling_plugin(keys)
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




def not_running(tool: str, found: dict) -> dict:






    name, label = _name(tool), _label(tool)
    state = found.get("state")
    if state == "disabled":

        out = {"installed": True, "enabled": False, "ready": False, "state": "PLUGIN_DISABLED"}
    elif state == "not_started":
        out = {"installed": True, "enabled": True, "ready": False, "state": "PLUGIN_NOT_STARTED"}
    else:
        out = {"installed": False, "ready": False, "state": "NOT_INSTALLED"}
    out["name"] = name
    out["label"] = label
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

    product = PRODUCTS[tool]
    commands = product.get("commands") or ()
    api = getattr(plugin, "mcp_api", None)
    if commands and (api is None or any(getattr(api, name, None) is None for name in commands)):
        return True
    version = sibling_api_version(plugin)
    return version is not None and version < product.get("min_api", 0)


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
            "name": name, "tool": tool,
            "tell_user_hint": "setup_install_opened", "next_step_hint": "setup_install_opened_next",


            "wait_for_plugin": {"keys": list(product["keys"]), "name": name,
                                "then": {"tool": tool, "next_step_hint": "setup_status_next"}},
        }

    if state == "disabled":
        if _switch_on(found["folder"]):
            log(f"setup: {label} switched on")
            return {"done": "plugin_switched_on", "tool": tool, "next_step_hint": "setup_status_next"}
        return {"done": "nothing", "label": label,
                "tell_user_hint": "setup_switch_on_failed", "next_step_hint": "setup_by_hand_next"}

    if state == "not_started":
        return {"done": "nothing", "label": label,
                "tell_user_hint": "setup_start_failed", "next_step_hint": "setup_by_hand_next"}

    plugin = found["plugin"]
    if outdated(tool, plugin):


        from ..ui.cross_plugin_discovery import SIBLINGS, open_plugin_manager_later
        sibling = SIBLINGS[product["product_id"]]
        open_plugin_manager_later(sibling["name"], sibling["url"])
        log(f"setup: Plugin Manager opened on {label} for an upgrade")
        return {
            "done": "plugin_manager_opened", "name": name, "tool": tool,
            "tell_user_hint": "setup_upgrade_opened", "next_step_hint": "setup_upgrade_opened_next",
        }
    if signed_in(tool, plugin):
        return {"done": "nothing", "state": "READY", "tool": tool, "next_step_hint": "setup_ready_next"}

    if _press_connect(plugin):
        log(f"setup: {label} sign-in page opened")
        return {
            "done": "sign_in_opened", "label": label, "tool": tool,
            "tell_user_hint": "setup_sign_in_opened", "next_step_hint": "setup_sign_in_opened_next",
        }
    from ..ui.cross_plugin_discovery import _activate_dock
    _activate_dock(plugin)
    return {
        "done": "panel_opened", "label": label, "tool": tool,
        "tell_user_hint": "setup_panel_opened", "next_step_hint": "setup_panel_opened_next",
    }
