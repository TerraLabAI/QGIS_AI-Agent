# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
















from __future__ import annotations

import contextlib

from qgis.PyQt.QtCore import QObject, QTimer

from ..core import tuning
from .shared import tr


UPDATE_RESULT_INSTALLED = "installed"
UPDATE_RESULT_RESTART = "restart_needed"
UPDATE_RESULT_FELL_BACK = "fell_back"
UPDATE_RESULT_FAILED = "failed"

_FETCH_TIMEOUT_S = 30
_FETCH_TIMEOUT_S_RANGE = (5, 180)
_RESULT_NOTE_SECONDS = 10


_BUSY_RECHECK_MS = 1000


class PluginSelfUpdate(QObject):


    def __init__(self, installer_key: str, target_version: str, fallback, parent=None,
                 on_result=None, is_busy=None):
        super().__init__(parent)
        self._key = installer_key
        self._target = target_version
        self._fallback = fallback

        self._on_result = on_result
        self._is_busy = is_busy
        self._done = False
        self._timer: QTimer | None = None
        self._repositories = None
        self._installed_note = tr("AI Agent {version} is installed.")
        self._restart_note = tr("AI Agent {version} is installed. Restart QGIS to use it.")

    def start(self) -> None:

        try:
            from pyplugin_installer.installer_data import plugins, repositories
        except Exception:  # noqa: BLE001
            self._give_up()
            return
        self._repositories = repositories
        try:
            if self._listed_upgradeable(plugins):
                self._schedule_install()
                return
            enabled = list(repositories.allEnabled())
            if not enabled:
                self._give_up()
                return
            repositories.checkingDone.connect(self._on_fetched)
            for key in enabled:
                request_repository_fetch(repositories, key)
        except Exception:  # noqa: BLE001
            self._give_up()
            return
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._give_up)
        self._timer.start(1000 * int(tuning.threshold(
            "update_fetch_timeout_s", _FETCH_TIMEOUT_S, *_FETCH_TIMEOUT_S_RANGE)))

    def _listed_upgradeable(self, plugins) -> bool:
        data = plugins.all().get(self._key)
        return bool(data) and data.get("status") == "upgradeable"

    def _disconnect_fetch(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        with contextlib.suppress(TypeError, RuntimeError, AttributeError):
            self._repositories.checkingDone.disconnect(self._on_fetched)

    def _on_fetched(self) -> None:
        if self._done:
            return
        self._disconnect_fetch()
        try:
            from pyplugin_installer.installer_data import plugins

            plugins.rebuild()
            listed = self._listed_upgradeable(plugins)
        except Exception:  # noqa: BLE001
            listed = False
        if listed:
            self._schedule_install()
        else:
            self._give_up()

    def _schedule_install(self) -> None:

        QTimer.singleShot(0, self._install)

    def _work_in_progress(self) -> bool:
        if self._is_busy is None:
            return False
        try:
            return bool(self._is_busy())
        except Exception:  # noqa: BLE001
            return False

    def _install(self) -> None:
        if self._done:
            return
        if self._work_in_progress():

            QTimer.singleShot(_BUSY_RECHECK_MS, self._install)
            return
        self._done = True
        version = self._target
        try:
            import pyplugin_installer
            from pyplugin_installer.installer_data import plugins

            listed = plugins.all().get(self._key) or {}
            version = str(listed.get("version_available") or "") or version

            _flush_telemetry()
            installer = pyplugin_installer.instance()
            try:
                installer.installPlugin(self._key, quiet=True)
            except TypeError:
                installer.installPlugin(self._key)
            data = plugins.all().get(self._key) or {}
            installed = data.get("status") == "installed" and not data.get("error")
            version = str(data.get("version_installed") or "") or version
        except Exception:  # noqa: BLE001
            installed = False
        if not installed:
            self._run_fallback(UPDATE_RESULT_FAILED)
            return
        self._report(UPDATE_RESULT_INSTALLED if self._push_result_note(version)
                     else UPDATE_RESULT_RESTART, version)
        self.deleteLater()

    def _report(self, result: str, version: str = "") -> None:
        if self._on_result is None:
            return
        with contextlib.suppress(Exception):
            self._on_result(result, version)

    def _push_result_note(self, version: str) -> bool:

        loaded = False
        try:
            from qgis.core import Qgis
            from qgis.utils import iface, isPluginLoaded

            loaded = isPluginLoaded(self._key)
            iface.messageBar().pushMessage(
                "AI Agent", (self._installed_note if loaded else self._restart_note).format(version=version),
                level=Qgis.MessageLevel.Success if loaded else Qgis.MessageLevel.Warning,
                duration=_RESULT_NOTE_SECONDS)
        except Exception:  # noqa: BLE001  # nosec B110
            pass
        return bool(loaded)

    def _give_up(self) -> None:
        if self._done:
            return
        self._done = True
        self._disconnect_fetch()
        self._run_fallback()

    def _run_fallback(self, result: str = UPDATE_RESULT_FELL_BACK) -> None:
        self._report(result)
        with contextlib.suppress(Exception):
            self._fallback()
        self.deleteLater()


def _flush_telemetry() -> None:

    try:
        from ..core.telemetry import flush

        flush()
    except Exception:  # noqa: BLE001  # nosec B110
        pass


def request_repository_fetch(repositories, key: str) -> None:

    try:
        repositories.requestFetching(key, force_reload=True)
    except TypeError:
        repositories.requestFetching(key)


def one_click_result_tracker(version: str):






    from ..core import telemetry, telemetry_events

    names = (telemetry.__name__, telemetry_events.__name__)

    def _send(result: str, installed_version: str = "") -> None:
        import importlib

        tel, events = (importlib.import_module(name) for name in names)
        tel.track(events.PLUGIN_UPDATE_PROMPT_CLICKED,
                  {"offered_version": installed_version or version or "unknown",
                   "action": "one_click", "result": result})
        tel.flush()
    return _send



_ACTIVE_UPDATE: list = []


def start_plugin_self_update(installer_key: str, target_version: str, fallback,
                             on_result=None, is_busy=None) -> PluginSelfUpdate:

    parent = None
    try:
        from qgis.utils import iface

        parent = iface.mainWindow()
    except Exception:  # noqa: BLE001  # nosec B110
        pass
    runner = PluginSelfUpdate(installer_key, target_version, fallback, parent,
                              on_result=on_result, is_busy=is_busy)
    _ACTIVE_UPDATE[:] = [runner]
    runner.start()
    return runner
