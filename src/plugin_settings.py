# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later











from __future__ import annotations

from qgis.PyQt.QtCore import QCoreApplication

from .core import improve, telemetry
from .core import telemetry_events as ev
from .core.logger import log_warning
from .core.qt_compat import disconnect_quietly
from .core.served_state import get_pricing_url, get_pro_offer
from .core.settings import Settings
from .ui.external_links import open_external_url
from .ui.settings_dialog import SettingsDialog
from .ui.shared import exec_dialog


def tr(text: str) -> str:

    return QCoreApplication.translate("AIAgentPlugin", text)


def open_settings_dialog(plugin):



    if plugin.dock is None:


        plugin._mark_open("settings")
        plugin._ensure_dock()
        if plugin.dock is not None:
            plugin.dock.setVisible(False)
            plugin._wire_dock()
    settings = plugin._settings or Settings()
    plugin._settings = settings
    account = plugin.controller.account if plugin.controller is not None else None
    info = {"telemetry_enabled": telemetry.is_telemetry_enabled(),
            "improve_enabled": improve.is_improve_enabled()}
    if account is not None and account.has_activation_key:
        info["loading"] = True
    else:
        info["error"], info["error_code"] = tr("Sign in to see your account."), "SIGNED_OUT"
    controller = plugin.controller
    values = {"mode": settings.mode, "approval": settings.approval, "server_url": settings.server_url,
              "settings": settings}
    dialog = SettingsDialog(plugin.iface.mainWindow(), info, values)
    dialog.telemetry_toggled.connect(telemetry.set_telemetry_enabled)




    dialog.improve_toggled.connect(
        lambda on, d=dialog: improve_choice(d, on))
    dialog.values_changed.connect(lambda values: apply_settings_values(plugin, values))
    if controller is not None:
        dialog.project_memory_requested.connect(controller.request_project_memory)
    dialog.upgrade_requested.connect(lambda d=dialog: open_plans_from_settings(plugin, d))

    if plugin.panel is not None:
        dialog.help_requested.connect(plugin.panel.open_help)




    dialog.prompt_chosen.connect(lambda text, chip=None: take_settings_prompt(plugin, text, chip))
    dialog.example_chosen.connect(lambda slug: settings_example_chosen(plugin, slug))



    session = getattr(plugin.controller, "session", None) if plugin.controller is not None else None
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

        dialog.sign_out_requested.connect(lambda: plugin.controller._on_sign_out("settings"))


        dialog.sign_in_requested.connect(plugin.controller._on_sign_in)
        dialog.delete_account_requested.connect(account.delete_account_async)
        if account.has_activation_key:
            account.fetch_account_async()
    try:
        exec_dialog(dialog)
    finally:
        if connected_repaint is not None:
            disconnect_quietly(session, "session_started", connected_repaint)
        if account is not None:
            for signal, slot in ((account.account_loaded, dialog.set_account_info),):
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
            disconnect_quietly(account, "account_failed")
        dialog.deleteLater()


def open_plans_from_settings(plugin, dialog) -> None:


    def report(link: str) -> None:
        telemetry.track(ev.SUBSCRIBE_LINK_CLICKED,
                        {"source": "settings", "where": "settings", "checkout_link": link,
                         "offer": get_pro_offer()})

    account = plugin.controller.account if plugin.controller is not None else None
    if account is not None:

        account.open_plans("plugin_settings", on_outcome=report,
                           trial_surface=f"settings_{getattr(dialog, 'upgrade_surface', 'account')}"
                           if getattr(dialog, "trial_offered", False) else "")
        return
    report("fallback")
    open_external_url(get_pricing_url(), parent=dialog)


def improve_choice(dialog, on: bool) -> None:

    if improve.set_improve_enabled(bool(on)):
        return
    try:
        dialog.improve_not_saved()
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Improve preference not written: {exc}")


def apply_settings_values(plugin, values: dict) -> None:
    settings = plugin._settings or Settings()
    plugin._settings = settings
    settings.mode = str(values.get("mode") or settings.mode)
    settings.approval = str(values.get("approval") or settings.approval)
    shortcut = str(values.get("send_shortcut") or "").strip()
    if shortcut:
        settings.send_shortcut = shortcut
        plugin._apply_send_shortcut(settings.send_shortcut)
    old_url = settings.server_url
    new_url = str(values.get("server_url") or "").strip() or old_url
    settings.server_url = new_url
    if plugin.controller is not None and new_url != old_url:
        plugin.controller.session.disconnect_from_server()
        plugin.controller.session.forget_session()
        plugin.controller.session.connect_to_server()


def take_settings_prompt(plugin, text: str, chip=None) -> None:


    plugin._show_dock()
    if plugin.panel is not None:
        plugin.panel.composer.take_example_prompt(text, chip)


def settings_example_chosen(plugin, slug: str) -> None:
    if plugin.panel is not None:
        plugin.panel.composer.example_chosen.emit(slug)
