# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later














from __future__ import annotations

import contextlib
import os
from functools import partial

from qgis.PyQt.QtCore import QTimer

from .core import telemetry, tuning
from .core import telemetry_events as ev
from .core.logger import log_warning



_REPOSITORY_WAIT_S = 90
_REPOSITORY_WAIT_S_RANGE = (5, 600)


def offer_upgradeable_update(plugin, payload: dict | None = None) -> None:















    from .api.terralab_client import plugin_version
    from .core.plugin_release import put_off_version
    from .core.versions import is_newer
    from .ui.shared import (
        get_latest_version,
        get_min_supported_version,
        get_release_notes_line,
        get_update_policy,
    )

    panel = getattr(plugin.dock, "panel", None)
    installed = plugin_version()
    served_version = get_latest_version()
    listed = upgradeable_version(plugin)
    released = plugin._released_version if is_newer(plugin._released_version, installed) else ""
    available = listed if listed and not is_newer(released, listed) else released
    too_old = is_newer(get_min_supported_version(), installed)
    if not available:
        if is_newer(served_version, installed) or too_old:
            refresh_plugin_repository(plugin, served_version)
        elif panel is not None and hasattr(panel, "clear_update"):
            panel.clear_update()
        return
    if not listed or is_newer(available, listed):



        refresh_plugin_repository(plugin, available)
    if not listed:
        if panel is not None and hasattr(panel, "clear_update"):
            panel.clear_update()
        return
    available = listed
    if panel is None:
        return
    required = (too_old or get_update_policy() == "require") and available == listed
    if required and too_old and is_newer(get_min_supported_version(), available):



        log_warning(f"The plugin repository only offers {available}, below the "
                    f"{get_min_supported_version()} the service requires; no update is offered.")
        if hasattr(panel, "clear_update"):
            panel.clear_update()
        return
    if not required and put_off_version() == available:
        return
    note = get_release_notes_line() if served_version == available else ""
    panel.show_update(available, note, required, installed)
    if plugin._shown_update_version != available:
        plugin._shown_update_version = available
        telemetry.track(ev.PLUGIN_UPDATE_PROMPT_SHOWN, {
            "offered_version": available, "required": required,
            "trigger": "plugin_registry"})


def read_released_once(plugin) -> None:

    if plugin._unloading or plugin._release_reply is not None:
        return
    try:
        from .core.plugin_release import read_released

        plugin._release_reply = read_released(partial(on_released, plugin))
    except Exception as exc:  # noqa: BLE001
        log_warning(f"plugins.qgis.org not asked for the latest AI Agent: {exc}")


def on_released(plugin, version: str, error: str) -> None:
    if plugin._unloading:
        return
    if error:
        log_warning(f"plugins.qgis.org did not say which AI Agent is out: {error}")
        return
    plugin._released_version = version
    offer_upgradeable_update(plugin)


def refresh_plugin_repository(plugin, served_version: str) -> None:







    if plugin._update_refresh_requested:
        if plugin._update_refresh_timer is None or not plugin._update_refresh_timer.isActive():
            suppress_update(plugin, served_version, "not_listed")
        return
    plugin._update_refresh_requested = True
    try:
        from pyplugin_installer.installer_data import repositories

        from .ui.plugin_self_update import request_repository_fetch

        enabled = list(repositories.allEnabled())
        if not enabled:
            suppress_update(plugin, served_version, "no_repository")
            return
        repositories.checkingDone.connect(plugin._on_plugin_repository_checked)
        for key in enabled:
            request_repository_fetch(repositories, key)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Plugin repository refresh skipped: {exc}")
        suppress_update(plugin, served_version, "refresh_failed")
        return
    timer = QTimer(plugin.dock) if plugin.dock is not None else QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(lambda: on_plugin_repository_late(plugin, served_version))
    timer.start(1000 * int(tuning.threshold(
        "update_fetch_timeout_s", _REPOSITORY_WAIT_S, *_REPOSITORY_WAIT_S_RANGE)))
    plugin._update_refresh_timer = timer


def on_plugin_repository_late(plugin, served_version: str) -> None:

    if plugin._unloading:
        return
    with contextlib.suppress(Exception):
        from pyplugin_installer.installer_data import repositories

        repositories.checkingDone.disconnect(plugin._on_plugin_repository_checked)
    log_warning("The QGIS plugin repository did not answer in time; the update offer uses "
                "plugins.qgis.org's own page instead.")
    suppress_update(plugin, served_version, "refresh_failed")


def on_plugin_repository_checked(plugin) -> None:

    if plugin._unloading:
        return
    if plugin._update_refresh_timer is not None:
        plugin._update_refresh_timer.stop()
    try:
        from pyplugin_installer.installer_data import plugins, repositories

        with contextlib.suppress(TypeError, RuntimeError):
            repositories.checkingDone.disconnect(plugin._on_plugin_repository_checked)
        plugins.rebuild()
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Plugin list rebuild skipped: {exc}")
    offer_upgradeable_update(plugin)


def suppress_update(plugin, served_version: str, reason: str) -> None:

    if not served_version or served_version in plugin._suppressed_update_versions:
        return
    plugin._suppressed_update_versions.add(served_version)
    telemetry.track(ev.PLUGIN_UPDATE_PROMPT_SUPPRESSED, {
        "served_version": served_version, "reason": reason})


def upgradeable_version(plugin) -> str:

    try:
        from pyplugin_installer.installer_data import plugins

        plugin_key = os.path.basename(plugin.plugin_dir)
        data = plugins.all().get(plugin_key)
        if data and data.get("status") == "upgradeable":
            return str(data.get("version_available") or "")
    except Exception as exc:  # noqa: BLE001
        log_warning(f"QGIS plugin list not read: {exc}")
    return ""
