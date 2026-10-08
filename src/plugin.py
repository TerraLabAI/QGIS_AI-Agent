# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

import contextlib
import os
import time

from qgis.core import Qgis, QgsApplication
from qgis.PyQt.QtCore import QCoreApplication, Qt, QTimer
from qgis.PyQt.QtGui import QIcon, QKeySequence
from qgis.PyQt.QtWidgets import QAction, QApplication, QDockWidget, QMenu

from . import LOADED_AT
from .core import i18n, improve, policy, telemetry, tuning
from .core import telemetry_events as ev
from .core.logger import log, log_warning, start_log_capture, stop_log_capture
from .core.settings import Settings
from .ui.shared import TOGGLE_PANEL_KEYS

try:
    from .ui.terralab_toolbar import add_action_to_toolbar, get_or_create_terralab_toolbar, remove_action_from_toolbar
except ImportError:
    add_action_to_toolbar = get_or_create_terralab_toolbar = remove_action_from_toolbar = None
try:
    from .ui.terralab_menu import (
        add_plugin_to_menu,
        add_to_plugins_menu,
        get_or_create_terralab_menu,
        remove_from_plugins_menu,
        remove_plugin_from_menu,
    )
except ImportError:
    add_plugin_to_menu = add_to_plugins_menu = get_or_create_terralab_menu = None
    remove_from_plugins_menu = remove_plugin_from_menu = None

PRODUCT_ID = "ai-agent"
PLUGIN_NAME = "AI Agent"


DEFAULT_SHORTCUT = TOGGLE_PANEL_KEYS
_DOCK_AREA = getattr(getattr(Qt, "DockWidgetArea", Qt), "RightDockWidgetArea", getattr(Qt, "RightDockWidgetArea", 2))



_DOCK_MIN_HEIGHT_SHARE = 0.5
_DOCK_HEIGHT_SHARE = 0.85
_DOCK_WIDTH_PX = 400








_CONFIG_REFRESH_MS = 30 * 60 * 1000
_CONFIG_MIN_GAP_S = 60.0



_CONFIG_REFRESH_MS_RANGE = (60_000, 24 * 60 * 60 * 1000)
_CONFIG_MIN_GAP_S_RANGE = (5.0, 3600.0)


_REPOSITORY_WAIT_S = 90
_REPOSITORY_WAIT_S_RANGE = (5, 600)

_RELEASE_READ_DELAY_MS = 3000


def tr(text: str) -> str:
    return QCoreApplication.translate("AIAgentPlugin", text)


class AIAgentPlugin:
    def __init__(self, iface):
        self.iface = iface

        from .core import crash_note
        crash_note.start()
        self.plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.action = None
        self.dock = None
        self.controller = None
        self.map_hooks = None
        self.locator_filter = None
        self.options_factory = None
        self.registry = None
        self.terralab_toolbar = None
        self.terralab_menu = None
        self._settings = None
        self._started = False
        self._unloading = False
        self._config_task = None
        self._shortcut_registered = False
        self._config_refresh_timer = None
        self._shown_update_version = ""
        self._suppressed_update_versions: set[str] = set()
        self._update_refresh_requested = False
        self._update_refresh_timer = None
        self._released_version = ""
        self._release_reply = None
        self._config_failure_reported = False
        self._loaded_at = time.monotonic()
        self._config_asked_at = None
        self._startup_watch = None
        self._palette_filter = None
        self._dock_wired = False
        self._telemetry_started = False
        self._early_account = None


        self._load_ms = None
        self._open_began = 0.0
        self._open_facts: dict = {}

    def _register_shortcut(self, sequence: str) -> bool:

        register = getattr(self.iface, "registerMainWindowAction", None)
        if register is None:
            return False
        try:
            register(self.action, sequence)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Shortcut not registered with QGIS: {exc}")
            return False
        self._shortcut_registered = True
        return True

    def _unregister_shortcut(self) -> None:
        if not self._shortcut_registered:
            return
        self._shortcut_registered = False
        unregister = getattr(self.iface, "unregisterMainWindowAction", None)
        if unregister is None:
            return
        try:
            unregister(self.action)
        except Exception:  # nosec B110
            pass



    def initGui(self):
        i18n.install()
        start_log_capture()


        try:
            from .core import stalls

            stalls.start_from_environment()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Stall profiler not started: {exc}")
        icon_path = os.path.join(self.plugin_dir, "icons", "icon.png")
        try:
            icon = QIcon(icon_path) if os.path.exists(icon_path) else QgsApplication.getThemeIcon("/mActionOptions.svg")
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Plugin icon not loaded: {exc}")
            icon = QIcon()
        self.action = QAction(icon, PLUGIN_NAME, self.iface.mainWindow())
        self.action.setObjectName("aiAgentToggleDock")
        self.action.setToolTip("AI Agent by TerraLab\n" + tr("Your AI agent inside QGIS"))
        self.action.setWhatsThis(tr("Open the AI Agent panel: ask for anything in QGIS and it does the work."))



        if not self._register_shortcut(DEFAULT_SHORTCUT):
            self.action.setShortcut(QKeySequence(DEFAULT_SHORTCUT))
            shortcut_context = getattr(getattr(Qt, "ShortcutContext", Qt), "ApplicationShortcut", None)
            if shortcut_context is not None:
                self.action.setShortcutContext(shortcut_context)
        self.action.triggered.connect(self.toggle_dock)
        try:
            from .core import view3d_guard

            view3d_guard.install()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"3D view guard not installed: {exc}")
        try:
            from .core import designer_guard

            designer_guard.install()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Layout designer guard not installed: {exc}")
        try:


            from .core import data_access

            data_access.install()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Data key not installed: {exc}")
        try:



            from qgis.core import QgsProject

            QgsProject.instance().writeProject.connect(self._on_project_write_zone)
            QgsProject.instance().readProject.connect(self._on_project_read_zone)
            self._on_project_read_zone()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Shared zone hooks not installed: {exc}")
        try:
            from .ui.locator import register as register_locator

            self.locator_filter = register_locator(self.iface, self._show_dock, self._prefill_composer)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Locator filter not installed: {exc}")
        try:
            from .ui.options_page import register as register_options

            self.options_factory = register_options(self.iface, icon, self._show_dock, self.open_settings)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Options page not installed: {exc}")

        if get_or_create_terralab_toolbar is not None:
            try:
                self.terralab_toolbar = get_or_create_terralab_toolbar(self.iface)
                add_action_to_toolbar(self.terralab_toolbar, self.action, PRODUCT_ID)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Shared toolbar unavailable: {exc}")
                self.terralab_toolbar = None
        if self.terralab_toolbar is None:
            self.iface.addToolBarIcon(self.action)

        if get_or_create_terralab_menu is not None:
            try:
                self.terralab_menu = get_or_create_terralab_menu(self.iface.mainWindow())
                add_plugin_to_menu(self.terralab_menu, self.action, PRODUCT_ID)
                add_to_plugins_menu(self.iface, self.action)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Shared menu unavailable: {exc}")
                self.terralab_menu = None
        if self.terralab_menu is None:
            self.iface.addPluginToMenu("AI Agent by TerraLab", self.action)
        self._note_version_change()



        self._open_once_started()
        QTimer.singleShot(_RELEASE_READ_DELAY_MS, self._read_released)
        QTimer.singleShot(_RELEASE_READ_DELAY_MS, self._tidy_folders)


        self._load_ms = int((time.monotonic() - LOADED_AT) * 1000)

    def _tidy_folders(self) -> None:

        if self._unloading:
            return
        try:
            policy.prune_agent_scratch_dirs()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Scratch folder prune not started: {exc}")
        try:



            from .ui.shared import drop_legacy_cache_dir

            drop_legacy_cache_dir()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Old cache folder not cleared: {exc}")

    def _open_once_started(self) -> None:










        if not self._qgis_started():
            signal = getattr(self.iface, "initializationCompleted", None)
            try:
                signal.connect(self._on_initialization_completed)
                self._startup_watch = signal
                return
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Start-up wait not installed: {exc}")
        QTimer.singleShot(0, self._open_at_startup)

    def _qgis_started(self) -> bool:






        from qgis.PyQt.QtCore import QThread

        try:
            if QThread.currentThread().loopLevel() > 0:
                return True
        except Exception:  # nosec B110
            pass
        try:
            return bool(self.iface.mainWindow().isVisible())
        except Exception:  # noqa: BLE001
            return True

    def _on_initialization_completed(self) -> None:
        self._drop_startup_watch()
        QTimer.singleShot(0, self._open_at_startup)

    def _drop_startup_watch(self) -> None:


        signal, self._startup_watch = self._startup_watch, None
        if signal is not None:
            with contextlib.suppress(TypeError, RuntimeError):
                signal.disconnect(self._on_initialization_completed)

    def _note_version_change(self) -> None:





        try:
            from .api.terralab_client import plugin_version

            settings = self._settings or Settings()
            self._settings = settings
            current = plugin_version()
            previous = settings.last_run_version
            if previous != current:
                if previous:
                    telemetry.track(ev.PLUGIN_UPDATED, {"previous_version": previous})
                settings.last_run_version = current
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Version bookkeeping skipped: {exc}")

    def _open_at_startup(self, registry_turn_done: bool = False):


        if self._unloading:
            return
        if self.dock is None and not (self._settings or Settings()).dock_visible:
            self._start_hidden()
            return
        self._mark_open("startup")
        if not registry_turn_done and self.registry is None and self.dock is None:





            try:
                self.registry = self._build_registry()
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Tool registry not built at startup: {exc}")
            QTimer.singleShot(0, lambda: self._open_at_startup(True))
            return
        created = self.dock is None
        self._ensure_dock()
        if self.dock is None:
            return
        settings = self._settings or Settings()


        if created:
            self.dock.setVisible(settings.dock_visible)
        self._wire_dock()

    def _start_hidden(self) -> None:










        began = time.monotonic()
        self._settings = self._settings or Settings()
        self._install_map_hooks()
        try:
            from .api.account import Account

            account = Account(self._settings)
            telemetry.set_auth_provider(account.get_auth_header)
            telemetry.new_session()
            telemetry.start_flush_timer()
            self._telemetry_started = True


            signed_in = account.has_activation_key
            telemetry.track(ev.PLUGIN_OPENED, {"signed_in": signed_in, "panel_visible": False,
                                               "load_ms": self._load_ms,
                                               "start_ms": int((time.monotonic() - began) * 1000)})
            self._early_account = account
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Session telemetry not started: {exc}")

    def _wire_dock(self) -> None:

        if self.dock is None or self._dock_wired:
            return
        self._dock_wired = True
        self.dock.visibilityChanged.connect(self._remember_visibility)
        self.dock.visibilityChanged.connect(self._on_dock_visibility_changed)
        if not self.dock.isHidden():


            self._remember_visibility()
        if self.dock.isVisible():
            self._on_dock_visibility_changed(True)

    def _remember_visibility(self, _visible: bool = False):








        if (self._unloading or self._settings is None or self.dock is None
                or not self.iface.mainWindow().isVisible()):
            return
        try:
            self._settings.dock_visible = not self.dock.isHidden()
        except RuntimeError:  # nosec B110
            pass

    def _on_dock_visibility_changed(self, visible: bool) -> None:






        if self._unloading:
            return
        if not visible:
            if self._config_refresh_timer is not None:
                self._config_refresh_timer.stop()
            return

        QTimer.singleShot(0, self._fit_dock)
        min_gap_s = tuning.threshold("config_min_gap_s", _CONFIG_MIN_GAP_S, *_CONFIG_MIN_GAP_S_RANGE)
        asked = self._config_asked_at
        if asked is None or time.monotonic() - asked >= min_gap_s:
            self._refresh_server_config()

        self._offer_upgradeable_update()
        if self._config_refresh_timer is None:
            self._config_refresh_timer = QTimer(self.dock)
            self._config_refresh_timer.timeout.connect(self._refresh_server_config)
        self._config_refresh_timer.setInterval(
            tuning.threshold("config_refresh_ms", _CONFIG_REFRESH_MS, *_CONFIG_REFRESH_MS_RANGE))
        if not self._config_refresh_timer.isActive():
            self._config_refresh_timer.start()

    def _fit_dock(self) -> None:











        dock = self.dock
        if dock is None or self._unloading:
            return
        try:
            window = self.iface.mainWindow()
            if dock.isFloating() or not dock.isVisible() or window is None:
                return
            from .ui.font_scale import scale_px_length

            area = window.dockWidgetArea(dock)
            left, right = dock.x(), dock.x() + dock.width()
            column = [other for other in window.findChildren(QDockWidget)
                      if other.isVisible() and not other.isFloating()
                      and window.dockWidgetArea(other) == area
                      and other.x() < right and left < other.x() + other.width()]
            height = max(o.y() + o.height() for o in column) - min(o.y() for o in column)
            if dock.height() < height * _DOCK_MIN_HEIGHT_SHARE:
                window.resizeDocks([dock], [int(height * _DOCK_HEIGHT_SHARE)], Qt.Orientation.Vertical)
            width = scale_px_length(_DOCK_WIDTH_PX)
            if dock.width() < width:
                window.resizeDocks([dock], [width], Qt.Orientation.Horizontal)
        except (RuntimeError, AttributeError) as exc:
            log_warning(f"Panel size not adjusted: {exc}")

    def _refresh_server_config(self) -> None:
        if self._config_task is not None and self._config_task.is_active():
            return
        self._config_asked_at = time.monotonic()
        try:
            from .api.account import GenericRequestTask
            from .api.terralab_client import TerraLabClient

            locale = self._settings.locale if self._settings is not None else ""
            account = self.controller.account if self.controller is not None else None
            auth = account.get_auth_header() if account is not None else {}
            task = GenericRequestTask(
                tr("Refreshing AI Agent settings"),
                lambda: TerraLabClient().get_config(lang=locale, auth=auth),
                hidden=True,
            )
            task.succeeded.connect(self._on_server_config_loaded)
            task.failed.connect(self._on_server_config_failed)
            self._config_task = task
            QgsApplication.taskManager().addTask(task)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Could not refresh AI Agent settings: {exc}")

    def _on_server_config_failed(self, message: str, code: str) -> None:

        if self._unloading:
            return
        log_warning(f"AI Agent settings not refreshed ({code}): {message}")
        if not self._config_failure_reported:
            self._config_failure_reported = True
            from .core.telemetry_errors import track_plugin_error

            track_plugin_error("config_refresh", str(code or "UNKNOWN"))

    def _on_server_config_loaded(self, payload: object) -> None:




        if self._unloading or not isinstance(payload, dict):
            return
        from .ui.shared import set_server_config

        set_server_config(payload)
        panel = getattr(self.dock, "panel", None)
        if panel is not None and hasattr(panel, "apply_server_config"):
            panel.apply_server_config(payload)
        self._offer_upgradeable_update(payload)

    def _offer_upgradeable_update(self, payload: dict | None = None) -> None:















        from .api.terralab_client import plugin_version
        from .core.plugin_release import put_off_version
        from .core.versions import is_newer
        from .ui.shared import (
            get_latest_version,
            get_min_supported_version,
            get_release_notes_line,
            get_update_policy,
        )

        panel = getattr(self.dock, "panel", None)
        installed = plugin_version()
        served_version = get_latest_version()
        listed = self._upgradeable_version()
        released = self._released_version if is_newer(self._released_version, installed) else ""
        available = listed if listed and not is_newer(released, listed) else released
        too_old = is_newer(get_min_supported_version(), installed)
        if not available:
            if is_newer(served_version, installed) or too_old:
                self._refresh_plugin_repository(served_version)
            elif panel is not None and hasattr(panel, "clear_update"):
                panel.clear_update()
            return
        if not listed or is_newer(available, listed):



            self._refresh_plugin_repository(available)
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
        if self._shown_update_version != available:
            self._shown_update_version = available
            telemetry.track(ev.PLUGIN_UPDATE_PROMPT_SHOWN, {
                "offered_version": available, "required": required,
                "trigger": "plugin_registry"})

    def _read_released(self) -> None:

        if self._unloading or self._release_reply is not None:
            return
        try:
            from .core.plugin_release import read_released

            self._release_reply = read_released(self._on_released)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"plugins.qgis.org not asked for the latest AI Agent: {exc}")

    def _on_released(self, version: str, error: str) -> None:
        if self._unloading:
            return
        if error:
            log_warning(f"plugins.qgis.org did not say which AI Agent is out: {error}")
            return
        self._released_version = version
        self._offer_upgradeable_update()

    def _refresh_plugin_repository(self, served_version: str) -> None:







        if self._update_refresh_requested:
            if self._update_refresh_timer is None or not self._update_refresh_timer.isActive():
                self._suppress_update(served_version, "not_listed")
            return
        self._update_refresh_requested = True
        try:
            from pyplugin_installer.installer_data import repositories

            from .ui.plugin_self_update import request_repository_fetch

            enabled = list(repositories.allEnabled())
            if not enabled:
                self._suppress_update(served_version, "no_repository")
                return
            repositories.checkingDone.connect(self._on_plugin_repository_checked)
            for key in enabled:
                request_repository_fetch(repositories, key)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Plugin repository refresh skipped: {exc}")
            self._suppress_update(served_version, "refresh_failed")
            return
        timer = QTimer(self.dock) if self.dock is not None else QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._on_plugin_repository_late(served_version))
        timer.start(1000 * int(tuning.threshold(
            "update_fetch_timeout_s", _REPOSITORY_WAIT_S, *_REPOSITORY_WAIT_S_RANGE)))
        self._update_refresh_timer = timer

    def _on_plugin_repository_late(self, served_version: str) -> None:

        if self._unloading:
            return
        with contextlib.suppress(Exception):
            from pyplugin_installer.installer_data import repositories

            repositories.checkingDone.disconnect(self._on_plugin_repository_checked)
        log_warning("The QGIS plugin repository did not answer in time; the update offer uses "
                    "plugins.qgis.org's own page instead.")
        self._suppress_update(served_version, "refresh_failed")

    def _on_plugin_repository_checked(self) -> None:

        if self._unloading:
            return
        if self._update_refresh_timer is not None:
            self._update_refresh_timer.stop()
        try:
            from pyplugin_installer.installer_data import plugins, repositories

            with contextlib.suppress(TypeError, RuntimeError):
                repositories.checkingDone.disconnect(self._on_plugin_repository_checked)
            plugins.rebuild()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Plugin list rebuild skipped: {exc}")
        self._offer_upgradeable_update()

    def _suppress_update(self, served_version: str, reason: str) -> None:

        if not served_version or served_version in self._suppressed_update_versions:
            return
        self._suppressed_update_versions.add(served_version)
        telemetry.track(ev.PLUGIN_UPDATE_PROMPT_SUPPRESSED, {
            "served_version": served_version, "reason": reason})

    def _upgradeable_version(self) -> str:

        try:
            from pyplugin_installer.installer_data import plugins

            plugin_key = os.path.basename(self.plugin_dir)
            data = plugins.all().get(plugin_key)
            if data and data.get("status") == "upgradeable":
                return str(data.get("version_available") or "")
        except Exception as exc:  # noqa: BLE001
            log_warning(f"QGIS plugin list not read: {exc}")
        return ""

    @staticmethod
    def _forget_network_state() -> None:









        for module, name in (("net", "clear_cache"), ("net", "politeness_reset"),
                             ("net", "forget_link_state"), ("security", "forget_resolved_hosts")):
            try:
                target = __import__(f"{__package__}.core.{module}", fromlist=[name])
                getattr(target, name)()
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Network state not cleared on unload ({module}.{name}): {exc}")

    @staticmethod
    def _stop_report_bridge() -> None:

        from .core import report_bridge

        report_bridge.shutdown()

    @staticmethod
    def _stop_profiler() -> None:


        from .core import stalls

        stalls.stop()

    @staticmethod
    def _stop_snapshot_jobs() -> None:
        from .core import snapshot

        snapshot.shutdown()

    @staticmethod
    def _stop_style_watch() -> None:
        from .core import snapshot_style

        snapshot_style.shutdown()

    @staticmethod
    def _stop_code_runtime() -> None:
        from .tools import code_runtime

        code_runtime.shutdown()

        from .core import settings

        settings.stop_watching_project()

    @staticmethod
    def _stop_3d_view_guard() -> None:
        from .core import view3d_guard

        view3d_guard.shutdown()

        from .core import designer_guard

        designer_guard.shutdown()

    @staticmethod
    def _stop_dependency_installs() -> None:



        from .tools import deps_tools

        deps_tools.shutdown()

    @staticmethod
    def _stop_processing_tasks() -> None:






        import sys



        module = sys.modules.get(__package__ + ".tools.processing_run")
        if module is None:
            return
        asked = module.shutdown()
        if asked:
            log_warning(f"Unload: asked {asked} background algorithm(s) to stop")

    def _install_palette_filter(self, check) -> None:

        from qgis.PyQt.QtCore import QEvent, QObject

        kinds = (QEvent.Type.ApplicationPaletteChange, QEvent.Type.PaletteChange)

        class _PaletteFilter(QObject):
            def eventFilter(self, _watched, event):  # noqa: N802
                if event.type() in kinds:
                    check()
                return False

        window = self.iface.mainWindow()
        palette_filter = _PaletteFilter(window)
        window.installEventFilter(palette_filter)
        self._palette_filter = palette_filter

    def _remove_palette_filter(self) -> None:
        palette_filter = getattr(self, "_palette_filter", None)
        self._palette_filter = None
        if palette_filter is None:
            return
        with contextlib.suppress(AttributeError, RuntimeError):
            self.iface.mainWindow().removeEventFilter(palette_filter)
            palette_filter.deleteLater()

    def _drop_window_watches(self, dock) -> None:









        from qgis.PyQt.QtGui import QGuiApplication

        check = getattr(dock, "_theme_watch", None)
        if check is not None:
            with contextlib.suppress(AttributeError, TypeError, RuntimeError):
                QGuiApplication.instance().paletteChanged.disconnect(check)
            with contextlib.suppress(AttributeError, TypeError, RuntimeError):
                QGuiApplication.styleHints().colorSchemeChanged.disconnect(check)
        self._remove_palette_filter()
        repaint = getattr(dock, "_screen_watch", None)
        if repaint is None:
            return
        handles = []
        with contextlib.suppress(AttributeError, RuntimeError):
            handles.append(dock.window().windowHandle())
        with contextlib.suppress(AttributeError, RuntimeError):
            handles.append(self.iface.mainWindow().windowHandle())
        for handle in {id(h): h for h in handles if h is not None}.values():
            with contextlib.suppress(TypeError, RuntimeError):
                handle.screenChanged.disconnect(repaint)

    @staticmethod
    def _drop_package_modules() -> None:











        import sys

        root = __name__.partition(".")[0]
        for name in [key for key in sys.modules if key == root or key.startswith(root + ".")]:
            sys.modules.pop(name, None)

    def unload(self):
        self._unloading = True
        with contextlib.suppress(Exception):
            from .core import crash_note
            crash_note.stop()
        try:
            telemetry.track(ev.PLUGIN_UNLOADED, {"session_ms": int((time.monotonic() - self._loaded_at) * 1000)})
            telemetry.flush(final=True)
        except Exception:  # nosec B110
            pass
        stop_log_capture()
        with contextlib.suppress(Exception):
            self._on_approval_waiting(False)
        with contextlib.suppress(Exception):
            self._drop_bar_item("_finished_bar_item")
        for stop in (self._stop_profiler, self._stop_snapshot_jobs, self._stop_style_watch,
                     self._stop_3d_view_guard, self._stop_dependency_installs, self._stop_processing_tasks,
                     self._stop_report_bridge, self._stop_code_runtime):
            try:
                stop()
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Unload: {exc}")
        self._forget_network_state()
        with contextlib.suppress(Exception):

            from .core import sibling_sign_in
            sibling_sign_in.cancel("ai-agent")
        if self._update_refresh_requested:

            with contextlib.suppress(Exception):
                from pyplugin_installer.installer_data import repositories

                repositories.checkingDone.disconnect(self._on_plugin_repository_checked)
        if self._update_refresh_timer is not None:
            with contextlib.suppress(RuntimeError):
                self._update_refresh_timer.stop()
            self._update_refresh_timer = None
        if self._release_reply is not None:

            with contextlib.suppress(RuntimeError, TypeError):
                self._release_reply.finished.disconnect()
                self._release_reply.abort()
            self._release_reply = None
        if self._config_refresh_timer is not None:
            self._config_refresh_timer.stop()
            self._config_refresh_timer = None
        if self._config_task is not None:


            try:
                self._config_task.succeeded.disconnect(self._on_server_config_loaded)
            except Exception:  # nosec B110
                pass
            try:
                self._config_task.cancel()
            except Exception:  # nosec B110
                pass
            self._config_task = None






        for label, step in (
            ("start-up wait", self._drop_startup_watch),
            ("i18n", i18n.uninstall),
            ("layer address check", self._remove_layer_egress),
            ("data key cookies", self._remove_data_access),
            ("shared zone hooks", self._remove_zone_hooks),
            ("map hooks", self._remove_map_hooks),
            ("locator", self._remove_locator),
            ("options page", self._remove_options_page),
        ):
            try:
                step()
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Unload ({label}): {exc}")
        if self.controller is not None:
            try:
                self.controller.shutdown()
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Controller shutdown: {exc}")
        elif self._telemetry_started:


            with contextlib.suppress(Exception):
                telemetry.shutdown()
        self._early_account = None
        if self.dock is not None:
            self._drop_window_watches(self.dock)
            for slot in (self._remember_visibility, self._on_dock_visibility_changed):
                try:
                    self.dock.visibilityChanged.disconnect(slot)
                except Exception:  # nosec B110
                    pass
            try:
                self.dock.cleanup()
            except (RuntimeError, AttributeError) as exc:
                log_warning(f"Dock cleanup: {exc}")
            try:
                self.iface.removeDockWidget(self.dock)
                self.dock.deleteLater()
            except (RuntimeError, AttributeError):
                pass
            self.dock = None
        self.controller = None
        if self.action is None:
            self._drop_package_modules()
            return
        self._unregister_shortcut()
        try:
            self.action.triggered.disconnect(self.toggle_dock)
        except (TypeError, RuntimeError, AttributeError):
            pass
        if self.terralab_menu is not None and remove_plugin_from_menu is not None:
            for fn in (lambda: remove_from_plugins_menu(self.iface, self.action),
                       lambda: remove_plugin_from_menu(self.terralab_menu, self.action, self.iface.mainWindow())):
                try:
                    fn()
                except (RuntimeError, AttributeError):
                    pass
            self.terralab_menu = None
        else:
            try:
                self.iface.removePluginMenu("AI Agent by TerraLab", self.action)
            except (RuntimeError, AttributeError) as exc:
                log_warning(f"Unload (plugin menu): {exc}")
        if self.terralab_toolbar is not None and remove_action_from_toolbar is not None:
            try:
                remove_action_from_toolbar(self.terralab_toolbar, self.action, self.iface.mainWindow())
            except (RuntimeError, AttributeError):
                pass
            self.terralab_toolbar = None
        else:
            try:
                self.iface.removeToolBarIcon(self.action)
            except (RuntimeError, AttributeError) as exc:
                log_warning(f"Unload (toolbar icon): {exc}")
        try:
            self.action.deleteLater()
        except (RuntimeError, AttributeError):  # nosec B110
            pass
        self.action = None
        self._drop_package_modules()

    @staticmethod
    def _remove_data_access() -> None:
        from .core import data_access

        data_access.uninstall()

    @staticmethod
    def _remove_layer_egress() -> None:
        from .core import layer_egress

        layer_egress.uninstall()

    @staticmethod
    def _on_project_read_zone(*_args) -> None:

        try:
            from .core import zone_of_interest

            zone_of_interest.restore_zone_layer()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Shared zone not restored: {exc}")

    @staticmethod
    def _on_project_write_zone(*_args) -> None:

        try:
            from .core import zone_of_interest

            zone_of_interest.store_project_zone_shapes()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Shared zone not stored: {exc}")

    def _remove_zone_hooks(self) -> None:
        from qgis.core import QgsProject

        project = QgsProject.instance()
        for signal, slot in ((project.writeProject, self._on_project_write_zone),
                             (project.readProject, self._on_project_read_zone)):
            with contextlib.suppress(TypeError, RuntimeError):
                signal.disconnect(slot)

    def _remove_map_hooks(self) -> None:
        hooks, self.map_hooks = self.map_hooks, None
        if hooks is not None:
            hooks.remove()

    def _remove_locator(self) -> None:
        located, self.locator_filter = self.locator_filter, None
        if located is not None:
            from .ui.locator import deregister as deregister_locator

            deregister_locator(self.iface, located)

    def _remove_options_page(self) -> None:
        factory, self.options_factory = self.options_factory, None
        if factory is not None:
            from .ui.options_page import deregister as deregister_options

            deregister_options(self.iface, factory)



    def _mark_open(self, how: str) -> None:


        if self.dock is None and not self._open_began:
            self._open_began = time.monotonic()
            self._open_facts["opened_by"] = how

    def _show_dock(self):

        self._mark_open("show")
        self._ensure_dock()
        if self.dock is None:
            return
        self.dock.show()
        self.dock.raise_()
        self._wire_dock()
        panel = getattr(self.dock, "panel", None)
        if panel is not None and hasattr(panel, "composer"):
            panel.composer.focus_input()

    def _prefill_composer(self, text: str, chip=None):

        panel = getattr(self.dock, "panel", None) if self.dock is not None else None
        composer = getattr(panel, "composer", None)
        if composer is None:
            return
        if isinstance(chip, dict) and chip.get("value"):


            composer.set_text("")
            composer.pin_mention(chip)
            composer.append_text(text)
        else:
            composer.set_text(text)
        composer.focus_input()

    def _take_settings_prompt(self, text: str, chip=None) -> None:


        self._show_dock()
        panel = getattr(self.dock, "panel", None) if self.dock is not None else None
        composer = getattr(panel, "composer", None)
        if composer is not None and hasattr(composer, "take_example_prompt"):
            composer.take_example_prompt(text, chip)

    def _settings_example_chosen(self, slug: str) -> None:
        panel = getattr(self.dock, "panel", None) if self.dock is not None else None
        composer = getattr(panel, "composer", None)
        if composer is not None:
            composer.example_chosen.emit(slug)

    def _pin_chip(self, chip: dict):
        panel = getattr(self.dock, "panel", None) if self.dock is not None else None
        if panel is not None:
            panel.add_context_chip(chip)

    def toggle_dock(self):
        created = self.dock is None
        self._mark_open("click")
        self._ensure_dock()
        if self.dock is None:
            return

        if self.dock.isVisible() and not created:
            self.dock.hide()
            return
        self.dock.show()
        self.dock.raise_()
        self._wire_dock()

    def _build_registry(self):
        began = time.monotonic()
        from .tools import build_registry






        include_debug = os.environ.get("AI_AGENT_DEBUG_TOOLS", "1") != "0"
        include_dev = os.environ.get("AI_AGENT_DEBUG_TOOLS") == "1"
        registry = build_registry(include_debug=include_debug, include_dev=include_dev)
        self._open_facts["registry_ms"] = int((time.monotonic() - began) * 1000)
        log(f"Tool registry built: {len(registry.visible_names())} visible tools, "
            f"debug={include_debug}, dev={include_dev}")



        try:
            from .ui.shared import set_tool_names

            set_tool_names(registry.tool_names)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Tool names not cached: {exc}")
        return registry

    def _ensure_dock(self):
        if self.dock is not None or self._unloading:
            return
        dock = None
        try:
            if self.registry is None:
                self.registry = self._build_registry()
            began = time.monotonic()
            from .ui.dock import AIAgentDock

            try:
                dock = AIAgentDock(self.iface.mainWindow())
            except TypeError:
                dock = AIAgentDock()
            self._open_facts["dock_ms"] = int((time.monotonic() - began) * 1000)
            began = time.monotonic()
            from .core.controller import AgentController

            self._settings = Settings()
            self.controller = AgentController(self.iface, dock.panel, self.registry, self._settings)
            self.controller.layer_action_requested.connect(self._on_layer_action)
            self.controller.settings_requested.connect(self.open_settings)
            self.controller.notice.connect(self._on_notice)
            if hasattr(self.controller, "approval_waiting"):
                self.controller.approval_waiting.connect(self._on_approval_waiting)
            if hasattr(self.controller, "run_finished"):
                self.controller.run_finished.connect(self._on_run_finished)
            self._add_dock(dock)
            self.dock = dock
            self._watch_screen(dock)
            self._watch_theme(dock)
            self._apply_send_shortcut(self._settings.send_shortcut)
            self._install_map_hooks()


            self._started = True
            self.controller.start()
            self._open_facts["controller_ms"] = int((time.monotonic() - began) * 1000)

            self.controller.open_stamps(dict(self._open_facts, load_ms=self._load_ms), self._open_began)
            self._early_account = None
        except Exception as exc:  # noqa: BLE001
            log_warning(f"AI Agent could not open its panel: {exc}")
            if self.dock is None and dock is not None:


                try:
                    dock.deleteLater()
                except RuntimeError:
                    pass
            self._push(tr("AI Agent could not open its panel: {error}").format(error=exc), "warning")

    def _add_dock(self, dock) -> None:










        window = self.iface.mainWindow()
        try:
            restored = bool(window.restoreDockWidget(dock))
        except (AttributeError, RuntimeError, TypeError):
            restored = False
        if not restored:
            self.iface.addDockWidget(_DOCK_AREA, dock)
            return
        menu = window.findChild(QMenu, "mPanelMenu")
        if menu is not None:
            menu.addAction(dock.toggleViewAction())

    def _install_map_hooks(self) -> None:
        if self.map_hooks is not None:
            return
        try:
            from .ui.map_hooks import MapHooks
            self.map_hooks = MapHooks(self.iface, self._show_dock, self._pin_chip)
            self.map_hooks.install()
        except Exception as exc:  # noqa: BLE001
            self.map_hooks = None
            log_warning(f"Map hooks unavailable: {exc}")

    def _on_notice(self, kind: str, message: str) -> None:









        panel = getattr(self.dock, "panel", None)
        composer = getattr(panel, "composer", None)
        try:
            shown = (self.dock is not None and composer is not None
                     and composer.isVisible() and QApplication.activeModalWidget() is None)
        except RuntimeError:
            shown = False
        if shown:
            try:
                if kind == "warning":
                    composer.show_warning(message)
                else:
                    composer.show_hint(message)
                return
            except (AttributeError, RuntimeError):
                pass
        self._push(message, kind)

    def _where_is_the_user(self):

        dock = self.dock
        try:
            main_window = self.iface.mainWindow()
            dock_seen = bool(dock is not None and dock.isVisible() and not dock.visibleRegion().isEmpty())
            window_active = bool(main_window is not None and main_window.isActiveWindow()
                                 and not main_window.isMinimized())
        except RuntimeError:
            return None
        return main_window, window_active, dock_seen

    def _call_user(self, attr: str, message: str, level) -> None:



        seen = self._where_is_the_user()
        if seen is None:
            return
        main_window, window_active, dock_seen = seen
        if not window_active and main_window is not None:
            with contextlib.suppress(RuntimeError, AttributeError):
                QApplication.alert(main_window, 0)
        if dock_seen or getattr(self, attr, None) is not None:
            return
        try:
            from qgis.PyQt.QtWidgets import QPushButton

            bar = self.iface.messageBar()
            item = bar.createMessage(PLUGIN_NAME, message)
            button = QPushButton(tr("Show"), item)
            button.clicked.connect(lambda _=False, attr=attr: (self._drop_bar_item(attr), self._show_dock()))
            item.layout().addWidget(button)
            setattr(self, attr, bar.pushWidget(item, level, 0) or item)
        except (RuntimeError, AttributeError, TypeError) as exc:
            log_warning(f"Reminder not shown: {exc}")

    def _drop_bar_item(self, attr: str) -> None:
        bar_item = getattr(self, attr, None)
        setattr(self, attr, None)
        if bar_item is not None:
            with contextlib.suppress(RuntimeError, AttributeError, TypeError):
                self.iface.messageBar().popWidget(bar_item)

    def _on_approval_waiting(self, waiting: bool) -> None:









        if not waiting:
            self._drop_bar_item("_approval_bar_item")
            return
        self._drop_bar_item("_finished_bar_item")
        self._call_user("_approval_bar_item", tr("The agent is waiting for your answer."),
                        Qgis.MessageLevel.Warning)

    def _on_run_finished(self, status: str) -> None:






        self._drop_bar_item("_finished_bar_item")
        if status == "cancelled":
            return
        if status == "done":
            message, level = tr("The agent finished."), Qgis.MessageLevel.Success
        else:
            message, level = tr("The agent stopped before finishing."), Qgis.MessageLevel.Warning
        self._call_user("_finished_bar_item", message, level)

    def _push(self, message: str, kind: str = "info"):
        level = Qgis.MessageLevel.Warning if kind == "warning" else Qgis.MessageLevel.Info
        try:
            self.iface.messageBar().pushMessage(PLUGIN_NAME, message, level=level, duration=8)
        except (RuntimeError, AttributeError):
            log(message)



    def _watch_screen(self, dock, tries: int = 5) -> None:







        try:
            from .ui.shared import watch_screen_changes

            if watch_screen_changes(dock) or tries <= 0:
                return
            QTimer.singleShot(400, lambda: self._watch_screen(dock, tries - 1))
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Screen change watch not installed: {exc}")

    def _watch_theme(self, dock) -> None:












        try:
            from qgis.PyQt.QtGui import QGuiApplication

            from .ui import style, styles

            def check(_palette=None):
                try:



                    if styles._dark_ui() == style.DARK:
                        return
                except Exception:  # noqa: BLE001
                    return


                if getattr(self, "_theme_notice_shown", False):
                    return
                self._theme_notice_shown = True
                self._push(tr("QGIS changed theme. Reload AI Agent, or restart QGIS, for its "
                              "panel to follow."), "warning")

            app = QGuiApplication.instance()
            if app is None:
                return








            signal = getattr(app, "paletteChanged", None)
            if signal is not None:
                signal.connect(check)
            else:
                self._install_palette_filter(check)
            scheme = getattr(QGuiApplication.styleHints(), "colorSchemeChanged", None)
            if scheme is not None:
                scheme.connect(check)

            dock._theme_watch = check
        except (AttributeError, TypeError, RuntimeError) as exc:


            log_warning(f"Theme change watch not installed: {exc}")

    def open_settings(self):









        try:
            self._open_settings()
        except ImportError as exc:
            log_warning(f"Settings dialog import failed: {exc}")
            self._push(tr("AI Agent was updated while QGIS was running. Restart QGIS to open its "
                          "settings."), "warning")
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Settings dialog failed to open: {exc}")
            self._push(tr("AI Agent could not open its settings: {error}").format(error=exc),
                       "warning")

    def _open_plans_from_settings(self, dialog) -> None:

        from .ui.shared import get_pro_offer

        def report(link: str) -> None:
            telemetry.track(ev.SUBSCRIBE_LINK_CLICKED,
                            {"source": "settings", "where": "settings", "checkout_link": link,
                             "offer": get_pro_offer()})

        account = self.controller.account if self.controller is not None else None
        if account is not None:
            account.open_plans("plugin_settings", on_outcome=report)
            return
        from .ui.external_links import open_external_url
        from .ui.shared import get_pricing_url
        report("fallback")
        open_external_url(get_pricing_url(), parent=dialog)

    def _open_settings(self):


        from .ui.settings_dialog import SettingsDialog
        from .ui.shared import exec_dialog

        if self.dock is None:


            self._mark_open("settings")
            self._ensure_dock()
            if self.dock is not None:
                self.dock.setVisible(False)
                self._wire_dock()
        settings = self._settings or Settings()
        self._settings = settings
        account = self.controller.account if self.controller is not None else None
        info = {"telemetry_enabled": telemetry.is_telemetry_enabled(),
                "improve_enabled": improve.is_improve_enabled()}
        if account is not None and account.has_activation_key:
            info["loading"] = True
        else:
            info["error"], info["error_code"] = tr("Sign in to see your account."), "SIGNED_OUT"
        values = {"mode": settings.mode, "approval": settings.approval, "server_url": settings.server_url,
                  "settings": settings}
        dialog = SettingsDialog(self.iface.mainWindow(), info, values)
        dialog.telemetry_toggled.connect(telemetry.set_telemetry_enabled)





        dialog.improve_toggled.connect(
            lambda on, d=dialog: self._improve_choice(d, on))
        dialog.values_changed.connect(self._apply_settings_values)
        dialog.upgrade_requested.connect(lambda d=dialog: self._open_plans_from_settings(d))

        panel = getattr(self.dock, "panel", None)
        if panel is not None and hasattr(panel, "open_help"):
            dialog.help_requested.connect(panel.open_help)




        dialog.prompt_chosen.connect(self._take_settings_prompt)
        dialog.example_chosen.connect(self._settings_example_chosen)



        session = getattr(self.controller, "session", None) if self.controller is not None else None
        connected_repaint = None
        if session is not None:
            def repaint_plan(_frame=None):
                try:
                    dialog.apply_plan()
                except RuntimeError:
                    pass
            try:
                session.session_started.connect(repaint_plan)
            except (AttributeError, TypeError):
                pass
            else:
                connected_repaint = repaint_plan
        if account is not None:
            account.account_loaded.connect(dialog.set_account_info)
            account.account_failed.connect(
                lambda message, code: dialog.set_account_info({"error": message, "error_code": code}))
            dialog.refresh_requested.connect(account.fetch_account_async)
            dialog.sign_out_requested.connect(account.sign_out)


            dialog.sign_in_requested.connect(self.controller._on_sign_in)
            dialog.delete_account_requested.connect(account.delete_account_async)
            if account.has_activation_key:
                account.fetch_account_async()
        try:
            exec_dialog(dialog)
        finally:
            if connected_repaint is not None:
                try:
                    session.session_started.disconnect(connected_repaint)
                except (TypeError, RuntimeError):
                    pass
            if account is not None:
                for signal, slot in ((account.account_loaded, dialog.set_account_info),):
                    try:
                        signal.disconnect(slot)
                    except (TypeError, RuntimeError):
                        pass
                try:
                    account.account_failed.disconnect()
                except (TypeError, RuntimeError):
                    pass
            dialog.deleteLater()

    def _improve_choice(self, dialog, on: bool) -> None:

        if improve.set_improve_enabled(bool(on)):
            return
        try:
            dialog.improve_not_saved()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Improve preference not written: {exc}")

    def _apply_settings_values(self, values: dict) -> None:
        settings = self._settings or Settings()
        self._settings = settings
        settings.mode = str(values.get("mode") or settings.mode)
        settings.approval = str(values.get("approval") or settings.approval)
        shortcut = str(values.get("send_shortcut") or "").strip()
        if shortcut:
            settings.send_shortcut = shortcut
            self._apply_send_shortcut(settings.send_shortcut)
        old_url = settings.server_url
        new_url = str(values.get("server_url") or "").strip() or old_url
        settings.server_url = new_url
        if self.controller is not None and new_url != old_url:
            self.controller.session.disconnect_from_server()
            self.controller.session.forget_session()
            self.controller.session.connect_to_server()

    def _apply_send_shortcut(self, value: str) -> None:

        panel = getattr(self.dock, "panel", None)
        composer = getattr(panel, "composer", None)
        if composer is None:
            return
        try:
            composer.set_send_shortcut(str(value))
        except (AttributeError, RuntimeError):
            pass

    def _on_layer_action(self, layer_id: str, action: str) -> None:

        from qgis.core import QgsProject, QgsVectorLayer
        layer = QgsProject.instance().mapLayer(layer_id) if layer_id else None
        if layer is None:
            self._push(tr("This layer is no longer in the project."), "warning")
            return
        iface = self.iface
        try:
            iface.setActiveLayer(layer)
            view = iface.layerTreeView()
            if view is not None:
                view.setCurrentLayer(layer)
            if action in ("show", "zoom"):
                self._show_layers_panel()
            if action == "zoom":
                iface.zoomToActiveLayer()
            elif action == "table":
                if isinstance(layer, QgsVectorLayer):
                    iface.showAttributeTable(layer)
                else:
                    iface.showLayerProperties(layer)
            elif action == "properties":
                iface.showLayerProperties(layer)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Layer action {action} failed: {exc}")
            self._push(tr("QGIS could not open that layer: {error}").format(error=exc), "warning")

    def _show_layers_panel(self) -> None:
        window = self.iface.mainWindow()
        panel = window.findChild(QDockWidget, "Layers") if window is not None else None
        if panel is None:
            return
        panel.show()
        panel.raise_()
