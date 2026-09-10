# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""QGIS entry point for AI Agent by TerraLab."""







from __future__ import annotations

import contextlib
import os
import time

from qgis.core import Qgis, QgsApplication
from qgis.PyQt.QtCore import QCoreApplication, Qt, QTimer
from qgis.PyQt.QtGui import QIcon, QKeySequence
from qgis.PyQt.QtWidgets import QAction, QDockWidget

from .core import i18n, improve, policy, telemetry
from .core import telemetry_events as ev
from .core.logger import log, log_warning, start_log_capture, stop_log_capture
from .core.settings import Settings

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


DEFAULT_SHORTCUT = "Ctrl+Alt+A"
_DOCK_AREA = getattr(getattr(Qt, "DockWidgetArea", Qt), "RightDockWidgetArea", getattr(Qt, "RightDockWidgetArea", 2))


_SLOW_PROMPT_S = 0.25


def tr(text: str) -> str:
    return QCoreApplication.translate("AIAgentPlugin", text)


class AIAgentPlugin:
    def __init__(self, iface):
        self.iface = iface
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
        self._loaded_at = time.monotonic()

    def _register_shortcut(self, sequence: str) -> bool:
        """Hand the shortcut to QGIS's own registry; False when it will not take it."""
        register = getattr(self.iface, "registerMainWindowAction", None)
        if register is None:
            return False
        try:
            register(self.action, sequence)
        except Exception as exc:  # noqa: BLE001 - a shortcut is never a blocker
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
        except Exception:  # nosec B110 - QGIS may already have torn the registry down
            pass



    def initGui(self):
        i18n.install()
        start_log_capture()


        try:
            from .core import stalls

            stalls.start_from_environment()
        except Exception as exc:  # noqa: BLE001 - a diagnostic never stops the plugin
            log_warning(f"Stall profiler not started: {exc}")
        icon_path = os.path.join(self.plugin_dir, "icons", "icon.png")
        try:
            icon = QIcon(icon_path) if os.path.exists(icon_path) else QgsApplication.getThemeIcon("/mActionOptions.svg")
        except Exception as exc:  # noqa: BLE001 - a missing icon must not disable the plugin
            log_warning(f"Plugin icon not loaded: {exc}")
            icon = QIcon()
        self.action = QAction(icon, PLUGIN_NAME, self.iface.mainWindow())
        self.action.setObjectName("aiAgentToggleDock")
        self.action.setToolTip("AI Agent by TerraLab\n" + tr("Drive QGIS in plain language"))
        self.action.setWhatsThis(tr("Open the AI Agent panel and drive QGIS in plain language."))



        if not self._register_shortcut(DEFAULT_SHORTCUT):
            self.action.setShortcut(QKeySequence(DEFAULT_SHORTCUT))
            shortcut_context = getattr(getattr(Qt, "ShortcutContext", Qt), "ApplicationShortcut", None)
            if shortcut_context is not None:
                self.action.setShortcutContext(shortcut_context)
        self.action.triggered.connect(self.toggle_dock)
        try:
            from .ui.locator import register as register_locator

            self.locator_filter = register_locator(self.iface, self._show_dock, self._prefill_composer)
        except Exception as exc:  # noqa: BLE001 - the locator is a convenience, never a blocker
            log_warning(f"Locator filter not installed: {exc}")
        try:
            from .ui.options_page import register as register_options

            self.options_factory = register_options(self.iface, icon, self._show_dock, self.open_settings)
        except Exception as exc:  # noqa: BLE001 - the options page is a signpost, never a blocker
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
        try:
            policy.prune_agent_scratch_dirs()
        except Exception as exc:  # noqa: BLE001 - housekeeping never blocks the load
            log_warning(f"Scratch folder prune not started: {exc}")
        try:



            from .ui.shared import drop_legacy_cache_dir

            drop_legacy_cache_dir()
        except Exception as exc:  # noqa: BLE001 - same, never blocks the load
            log_warning(f"Old cache folder not cleared: {exc}")


        QTimer.singleShot(0, self._open_at_startup)

    def _note_version_change(self) -> None:
        """One event when the installed version differs from the last one that ran."""




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
        except Exception as exc:  # noqa: BLE001 - bookkeeping never blocks the load
            log_warning(f"Version bookkeeping skipped: {exc}")

    def _open_at_startup(self):


        if self._unloading:
            return
        created = self.dock is None
        self._ensure_dock()
        if self.dock is None:
            return
        settings = self._settings or Settings()


        if created:
            self.dock.setVisible(settings.dock_visible)
        self.dock.visibilityChanged.connect(self._remember_visibility)
        self.dock.visibilityChanged.connect(self._on_dock_visibility_changed)
        if self.dock.isVisible():
            self._on_dock_visibility_changed(True)

    def _remember_visibility(self, visible: bool):

        if self._unloading or self._settings is None or not self.iface.mainWindow().isVisible():
            return
        self._settings.dock_visible = bool(visible)

    def _on_dock_visibility_changed(self, visible: bool) -> None:
        """Refresh served product settings when the user opens the dock."""
        if not visible or self._unloading:
            return
        self._refresh_server_config()
        if self._config_refresh_timer is None:
            self._config_refresh_timer = QTimer(self.dock)
            self._config_refresh_timer.setInterval(30 * 60 * 1000)
            self._config_refresh_timer.timeout.connect(self._refresh_server_config)
            self._config_refresh_timer.start()

    def _refresh_server_config(self) -> None:
        if self._config_task is not None and self._config_task.is_active():
            return
        try:
            from .api.account import GenericRequestTask
            from .api.terralab_client import TerraLabClient

            locale = self._settings.locale if self._settings is not None else ""
            task = GenericRequestTask(
                tr("Refreshing AI Agent settings"),
                lambda: TerraLabClient().get_config(lang=locale),
                hidden=True,
            )
            task.succeeded.connect(self._on_server_config_loaded)
            task.failed.connect(lambda _message, _code: None)
            self._config_task = task
            QgsApplication.taskManager().addTask(task)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Could not refresh AI Agent settings: {exc}")

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
        """Offer, or require, the update QGIS itself can install."""














        from .api.terralab_client import plugin_version
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
        too_old = is_newer(get_min_supported_version(), installed)
        if not is_newer(served_version, installed) and not too_old:
            if panel is not None and hasattr(panel, "clear_update"):
                panel.clear_update()
            return
        available = self._upgradeable_version()
        if not available:
            self._refresh_plugin_repository(served_version)
            return
        if panel is None:
            return
        required = too_old or get_update_policy() == "require"
        if required and too_old and is_newer(get_min_supported_version(), available):






            log_warning(f"The plugin repository only offers {available}, below the "
                        f"{get_min_supported_version()} the service requires; no update is offered.")
            if hasattr(panel, "clear_update"):
                panel.clear_update()
            return
        panel.show_update(available, get_release_notes_line(), required, installed)
        if self._shown_update_version != available:
            self._shown_update_version = available
            telemetry.track(ev.PLUGIN_UPDATE_PROMPT_SHOWN, {
                "offered_version": available, "required": required,
                "trigger": "served_latest_version"})

    def _refresh_plugin_repository(self, served_version: str) -> None:
        """One non-blocking repository refresh per session, then look again."""





        if self._update_refresh_requested:
            self._suppress_update(served_version, "not_listed")
            return
        self._update_refresh_requested = True
        try:
            from pyplugin_installer.installer_data import repositories

            enabled = list(repositories.allEnabled())
            if not enabled:
                self._suppress_update(served_version, "no_repository")
                return
            repositories.checkingDone.connect(self._on_plugin_repository_checked)
            for key in enabled:
                repositories.requestFetching(key, force_reload=True)
        except Exception as exc:  # noqa: BLE001 - the installer is optional
            log_warning(f"Plugin repository refresh skipped: {exc}")
            self._suppress_update(served_version, "refresh_failed")

    def _on_plugin_repository_checked(self) -> None:
        """The repository answered: rebuild the list and offer what it holds."""
        if self._unloading:
            return
        try:
            from pyplugin_installer.installer_data import plugins, repositories

            with contextlib.suppress(TypeError, RuntimeError):
                repositories.checkingDone.disconnect(self._on_plugin_repository_checked)
            plugins.rebuild()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Plugin list rebuild skipped: {exc}")
        self._offer_upgradeable_update()

    def _suppress_update(self, served_version: str, reason: str) -> None:
        """Report an offer we chose not to make, once per version per session."""
        if not served_version or served_version in self._suppressed_update_versions:
            return
        self._suppressed_update_versions.add(served_version)
        telemetry.track(ev.PLUGIN_UPDATE_PROMPT_SUPPRESSED, {
            "served_version": served_version, "reason": reason})

    def _upgradeable_version(self) -> str:
        """The version QGIS says is available for this plugin, or an empty string."""
        try:
            from pyplugin_installer.installer_data import plugins

            plugin_key = os.path.basename(self.plugin_dir)
            data = plugins.all().get(plugin_key)
            if data and data.get("status") == "upgradeable":
                return str(data.get("version_available") or "")
        except Exception:  # nosec B110 - update check is optional
            pass
        return ""

    @staticmethod
    def _forget_network_state() -> None:
        """Drop the module-level network state, so a reload starts clean."""








        for module, name in (("net", "clear_cache"), ("net", "politeness_reset"),
                             ("net", "forget_link_state"), ("security", "forget_resolved_hosts")):
            try:
                target = __import__(f"{__package__}.core.{module}", fromlist=[name])
                getattr(target, name)()
            except Exception as exc:  # noqa: BLE001 - unload never fails on tidying
                log_warning(f"Network state not cleared on unload ({module}.{name}): {exc}")

    @staticmethod
    def _stop_profiler() -> None:
        """The stall sampler thread goes with the plugin; a reload must not leave one walking every thread's frames every few milliseconds."""

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
    def _stop_dependency_installs() -> None:
        """A pip install cannot be interrupted, so the reload must hand it over instead of walking away from it: the thread to the application object."""


        from .tools import deps_tools

        deps_tools.shutdown()

    @staticmethod
    def _stop_processing_tasks() -> None:
        """A background algorithm outlives the dictionary that tracks it."""





        import sys



        module = sys.modules.get(__package__ + ".tools.processing_tools")
        if module is None:
            return
        asked = module.shutdown()
        if asked:
            log_warning(f"Unload: asked {asked} background algorithm(s) to stop")

    def unload(self):
        self._unloading = True
        try:
            telemetry.track(ev.PLUGIN_UNLOADED, {"session_ms": int((time.monotonic() - self._loaded_at) * 1000)},
                            flush_now=True)
        except Exception:  # nosec B110 - telemetry never blocks an unload
            pass
        stop_log_capture()
        for stop in (self._stop_profiler, self._stop_snapshot_jobs, self._stop_style_watch,
                     self._stop_dependency_installs, self._stop_processing_tasks):
            try:
                stop()
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Unload: {exc}")
        self._forget_network_state()
        if self._update_refresh_requested:

            with contextlib.suppress(Exception):
                from pyplugin_installer.installer_data import repositories

                repositories.checkingDone.disconnect(self._on_plugin_repository_checked)
        if self._config_refresh_timer is not None:
            self._config_refresh_timer.stop()
            self._config_refresh_timer = None
        if self._config_task is not None:


            try:
                self._config_task.succeeded.disconnect(self._on_server_config_loaded)
            except Exception:  # nosec B110 - Qt connection may be gone
                pass
            try:
                self._config_task.cancel()
            except Exception:  # nosec B110 - the task may already have finished
                pass
            self._config_task = None







        for label, step in (
            ("i18n", i18n.uninstall),
            ("map hooks", self._remove_map_hooks),
            ("locator", self._remove_locator),
            ("options page", self._remove_options_page),
        ):
            try:
                step()
            except Exception as exc:  # noqa: BLE001 - the next step still has to run
                log_warning(f"Unload ({label}): {exc}")
        if self.controller is not None:
            try:
                self.controller.shutdown()
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Controller shutdown: {exc}")
        if self.dock is not None:
            for slot in (self._remember_visibility, self._on_dock_visibility_changed):
                try:
                    self.dock.visibilityChanged.disconnect(slot)
                except Exception:  # nosec B110 - Qt connection may be gone
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
        except (RuntimeError, AttributeError):  # nosec B110 - the wrapper may be gone
            pass
        self.action = None

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



    def _show_dock(self):
        """Open the panel and put the cursor in the composer."""
        self._ensure_dock()
        if self.dock is None:
            return
        self.dock.show()
        self.dock.raise_()
        panel = getattr(self.dock, "panel", None)
        if panel is not None and hasattr(panel, "composer"):
            panel.composer.focus_input()

    def _prompt_from_settings(self, text: str, chip=None):
        """A prompt card in Settings: open the panel and put the sentence in the box."""














        text = str(text or "").strip()
        pin = dict(chip) if isinstance(chip, dict) and chip.get("value") else None
        if not text and pin is None:
            return
        chosen_at = time.monotonic()
        QTimer.singleShot(0, lambda: self._deliver_prompt(text, pin, chosen_at))

    def _deliver_prompt(self, text: str, chip=None, chosen_at: float = 0.0):
        """Open the panel and fill the box, and say so when it took a while."""







        started = time.monotonic()
        self._show_dock()
        self._prefill_composer(text, chip)
        waited = (started - chosen_at) if chosen_at else 0.0
        took = time.monotonic() - started
        if waited > _SLOW_PROMPT_S or took > _SLOW_PROMPT_S:
            log_warning(f"Prompt card was slow: waited {waited:.2f}s for the event loop, "
                        f"fill took {took:.2f}s")

    def _prefill_composer(self, text: str, chip=None):
        """Put a question in the composer, cursor at the end, ready for Enter."""
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

    def _pin_chip(self, chip: dict):
        panel = getattr(self.dock, "panel", None) if self.dock is not None else None
        if panel is not None:
            panel.add_context_chip(chip)

    def toggle_dock(self):
        created = self.dock is None
        self._ensure_dock()
        if self.dock is None:
            return

        if self.dock.isVisible() and not created:
            self.dock.hide()
            return
        self.dock.show()
        self.dock.raise_()

    def _build_registry(self):
        from .tools import build_registry







        include_debug = os.environ.get("AI_AGENT_DEBUG_TOOLS", "1") != "0"
        include_dev = os.environ.get("AI_AGENT_DEBUG_TOOLS") == "1"
        registry = build_registry(include_debug=include_debug, include_dev=include_dev)
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
            from .core.controller import AgentController
            from .ui.dock import AIAgentDock

            if self.registry is None:
                self.registry = self._build_registry()
            try:
                dock = AIAgentDock(self.iface.mainWindow())
            except TypeError:
                dock = AIAgentDock()
            self._settings = Settings()
            self.controller = AgentController(self.iface, dock.panel, self.registry, self._settings)
            self.controller.layer_action_requested.connect(self._on_layer_action)
            self.controller.settings_requested.connect(self.open_settings)
            self.controller.notice.connect(lambda kind, message: self._push(message, kind))
            self.iface.addDockWidget(_DOCK_AREA, dock)
            self.dock = dock
            self._watch_screen(dock)
            self._watch_theme(dock)
            self._apply_send_shortcut(self._settings.send_shortcut)
            try:
                from .ui.map_hooks import MapHooks
                self.map_hooks = MapHooks(self.iface, self._show_dock, self._pin_chip)
                self.map_hooks.install()
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Map hooks unavailable: {exc}")


            self._started = True
            self.controller.start()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"AI Agent could not open its panel: {exc}")
            if self.dock is None and dock is not None:


                try:
                    dock.deleteLater()
                except RuntimeError:
                    pass
            self._push(tr("AI Agent could not open its panel: {error}").format(error=exc), "warning")

    def _push(self, message: str, kind: str = "info"):
        level = Qgis.MessageLevel.Warning if kind == "warning" else Qgis.MessageLevel.Info
        try:
            self.iface.messageBar().pushMessage(PLUGIN_NAME, message, level=level, duration=8)
        except (RuntimeError, AttributeError):
            log(message)



    def _watch_screen(self, dock, tries: int = 5) -> None:
        """Keep the panel sharp when QGIS is dragged to a second monitor."""






        try:
            from .ui.shared import watch_screen_changes

            if watch_screen_changes(dock) or tries <= 0:
                return
            QTimer.singleShot(400, lambda: self._watch_screen(dock, tries - 1))
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Screen change watch not installed: {exc}")

    def _watch_theme(self, dock) -> None:
        """Say so when the QGIS theme flips, because the panel cannot follow it."""











        try:
            from qgis.PyQt.QtGui import QGuiApplication

            from .ui import style

            def check(_palette=None):
                try:
                    if style.is_dark() == style.DARK:
                        return
                except Exception:  # noqa: BLE001 - no palette to read, nothing to say
                    return


                if getattr(self, "_theme_notice_shown", False):
                    return
                self._theme_notice_shown = True
                self._push(tr("QGIS changed theme. Reload AI Agent, or restart QGIS, for its "
                              "panel to follow."), "warning")

            app = QGuiApplication.instance()
            if app is None:
                return
            app.paletteChanged.connect(check)

            dock._theme_watch = check
        except (AttributeError, TypeError, RuntimeError) as exc:



            log_warning(f"Theme change watch not installed: {exc}")

    def open_settings(self):
        """The gear."""








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

    def _open_settings(self):
        """The same account panel as AI Segmentation and AI Edit, with the agent's default mode between the plan and the contact cards."""

        from .ui.settings_dialog import SettingsDialog
        from .ui.shared import exec_dialog, get_connectors

        settings = self._settings or Settings()
        self._settings = settings
        account = self.controller.account if self.controller is not None else None
        info = {"telemetry_enabled": telemetry.is_telemetry_enabled(),
                "improve_enabled": improve.is_improve_enabled()}
        if account is not None and account.has_activation_key:
            info["loading"] = True
        else:
            info["error"], info["error_code"] = tr("Sign in to see your account."), "SIGNED_OUT"
        registry = self.registry
        values = {"mode": settings.mode, "approval": settings.approval, "server_url": settings.server_url,
                  "settings": settings,
                  "tool_names": list(registry.tool_names) if registry is not None else []}
        dialog = SettingsDialog(self.iface.mainWindow(), info, values)
        dialog.telemetry_toggled.connect(telemetry.set_telemetry_enabled)





        dialog.improve_toggled.connect(
            lambda on, d=dialog: self._improve_choice(d, on))
        dialog.values_changed.connect(self._apply_settings_values)
        dialog.upgrade_requested.connect(
            lambda: telemetry.track(ev.SUBSCRIBE_LINK_CLICKED, {"source": "settings"}))


        dialog.prompt_chosen.connect(self._prompt_from_settings)

        panel = getattr(self.dock, "panel", None)
        if panel is not None and hasattr(panel, "open_help"):
            dialog.help_requested.connect(panel.open_help)



        session = getattr(self.controller, "session", None) if self.controller is not None else None
        connected_repaint = None
        if session is not None:
            def repaint_connectors(_frame=None):
                try:
                    dialog.set_connectors(get_connectors())


                    dialog.apply_plan()
                except RuntimeError:
                    pass
            try:
                session.session_started.connect(repaint_connectors)
            except (AttributeError, TypeError):
                pass
            else:
                connected_repaint = repaint_connectors
        if account is not None:
            account.account_loaded.connect(dialog.set_account_info)
            account.account_failed.connect(
                lambda message, code: dialog.set_account_info({"error": message, "error_code": code}))
            dialog.refresh_requested.connect(account.fetch_account_async)
            dialog.sign_out_requested.connect(account.sign_out)
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
        """Persist the answer, and stop the dialog claiming a write that failed."""
        if improve.set_improve_enabled(bool(on)):
            return
        try:
            dialog.improve_not_saved()
        except Exception as exc:  # noqa: BLE001 - the dialog may be closing
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
        """Tell the composer which key sends. Silent when the panel is not built."""
        panel = getattr(self.dock, "panel", None)
        composer = getattr(panel, "composer", None)
        if composer is None:
            return
        try:
            composer.set_send_shortcut(str(value))
        except (AttributeError, RuntimeError):
            pass

    def _on_layer_action(self, layer_id: str, action: str) -> None:
        """A run-bar chip: drive QGIS itself rather than a dialog of our own."""
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
