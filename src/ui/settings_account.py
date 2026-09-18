# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Account, Advanced and the danger zone: the pages `SettingsDialog` builds for who is signed in and this install's own settings."""













from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.logger import log_warning
from ..core.profile import clear_memory_notes
from .account_settings_dialog import _ACCOUNT_OFFLINE_CODES, format_count, resolve_plan_runs
from .delete_account_dialog import ask_delete_account
from .external_links import open_external_url
from .font_scale import apply_font_scale_to_tree, scale_px_length
from .settings_pages import (
    DANGER_GHOST_BTN_QSS,
    GHOST_BTN_QSS,
    PRIMARY_BTN_QSS,
    PROGRESS_QSS,
    ROW_NOTE_QSS,
    ROW_TITLE_QSS,
    USAGE_KEYS,
    Page,
    SettingGroup,
    SettingRow,
    Switch,
    set_section_locked,
)
from .shared import (
    PRODUCT_ID,
    exec_dialog,
    format_reset_date,
    get_dashboard_url,
    get_plan_name,
    get_pricing_url,
    get_served_pro_points,
    safe_disconnect,
)


_AVATAR_D = 36


class AccountPageMixin:
    """Who is signed in, and the settings that belong to the account."""



    def _build_account(self) -> Page:
        """Who is signed in, then the settings that belong to the account."""







        page = Page(self.tr("Account"), self.tr("Your sign-in, your plan and your privacy."), self)
        self._account_box = QWidget(page)
        self._account_col = QVBoxLayout(self._account_box)
        self._account_col.setContentsMargins(0, 0, 0, 0)
        self._account_col.setSpacing(14)



        page.add(self._account_box)
        self._add_advanced(page)
        return page

    def set_account_info(self, info: dict) -> None:
        """Repaint the Account page from one dict: loading, error, or the account."""
        info = dict(info or {})
        if "telemetry_enabled" in info:
            self.set_telemetry_enabled(bool(info["telemetry_enabled"]))
        if "improve_enabled" in info:
            self.set_improve_enabled(bool(info["improve_enabled"]))
        self._sync_improve_plan(info)


        try:
            self._paint_billing(info)
        except RuntimeError:
            pass
        self._sync_upgrade_pill(info)
        self._clear_account()


        self._account_email = ""
        if info.get("loading"):
            group = SettingGroup(self._account_box)
            group.add_row(SettingRow(self.tr("Loading account info..."), "", None, group))
            self._account_col.addWidget(group)
        elif info.get("error") or info.get("error_code"):
            self._show_account_error(str(info.get("error") or ""), str(info.get("error_code") or ""))
        else:
            usage = dict(info.get("usage") or {})
            for key in USAGE_KEYS:
                if key in info and key not in usage:
                    usage[key] = info[key]
            self._show_account(info, usage)
        self._sync_delete_account()
        apply_font_scale_to_tree(self._account_box)

    def _sync_upgrade_pill(self, info: dict) -> None:
        """Show the rail's upgrade only to an account that can take it up."""




        pill = getattr(self, "_upgrade_pill", None)
        if pill is None:
            return
        if info.get("loading") or info.get("error") or info.get("error_code"):
            pill.hide()
            return
        pill.setVisible(not self._is_subscriber(info))

    def _is_subscriber(self, info: dict) -> bool:
        """Does this account pay? Read from whichever of the three fields arrived."""
        usage = dict(info.get("usage") or {})
        for key in ("is_subscriber", "is_free_tier"):
            if key in info and key not in usage:
                usage[key] = info[key]
        if "is_subscriber" in usage:
            return bool(usage.get("is_subscriber"))
        if "is_free_tier" in usage:
            return not bool(usage.get("is_free_tier"))
        return bool(self._find_subscription(info))

    def _clear_account(self) -> None:
        self._cancel_avatar_load()
        self._avatar_label = None
        while self._account_col.count():
            item = self._account_col.takeAt(0)
            widget = item.widget()
            if widget is not None:



                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _muted(self, text: str, parent: QWidget | None = None) -> QLabel:


        label = QLabel(text, parent or getattr(self, "_account_box", None) or self)
        label.setStyleSheet(ROW_NOTE_QSS)
        label.setWordWrap(True)
        label.setContentsMargins(4, 4, 4, 4)
        return label

    def _show_account_error(self, message: str, code: str) -> None:
        """One card: what went wrong in a few words, and the buttons that fix it."""
        code = (code or "").strip().upper()
        retry = False
        manage = False
        note = ""
        if code == "SIGNED_OUT":
            title = message or self.tr("Sign in to see your account.")
        elif code == "SUBSCRIPTION_INACTIVE":
            title = self.tr("Your last payment may have failed")
            note = self.tr("Update your payment method to fix it.")
            manage = True
        elif code == "INVALID_KEY":
            title = self.tr("This computer is no longer signed in")
            note = self.tr("Sign out, then sign in again.")
        elif code in _ACCOUNT_OFFLINE_CODES:
            title = self.tr("Could not reach TerraLab")
            note = self.tr("Check your internet connection, then retry.")
            retry = True
        else:
            title = message or self.tr("Could not load your account")
            note = self.tr("Try again in a moment.")
            retry = True
        group = SettingGroup(self._account_box)
        holder = None
        if code != "SIGNED_OUT":
            holder = QWidget(group)
            buttons = QHBoxLayout(holder)
            buttons.setContentsMargins(0, 0, 0, 0)
            buttons.setSpacing(8)
            if manage:
                btn = self._button(self.tr("Update payment method"), PRIMARY_BTN_QSS)
                btn.clicked.connect(lambda: self._open_dashboard("error_card"))
                buttons.addWidget(btn)
            if retry:
                btn = self._button(self.tr("Retry"), GHOST_BTN_QSS)
                btn.clicked.connect(self._on_retry)
                buttons.addWidget(btn)
            out = self._button(self.tr("Sign out"), GHOST_BTN_QSS)
            out.clicked.connect(self._on_sign_out)
            buttons.addWidget(out)
        group.add_row(SettingRow(title, note, holder, group))
        self._account_col.addWidget(group)

    def _show_account(self, account: dict, usage: dict) -> None:
        """One account card: who, which plan, how many runs, and for a free account what Pro adds."""

        email = str(account.get("email") or "-")
        self._account_email = "" if email == "-" else email
        group = SettingGroup(self._account_box)

        sub = self._find_subscription(account)
        plan = resolve_plan_runs(usage, sub or {})
        plan_name = (get_plan_name("pro", self.tr("Pro plan")) if plan.is_subscriber
                     else get_plan_name("free", self.tr("Free plan")))

        chip = QFrame(group)
        chip.setObjectName("settingsRow")
        chip_row = QHBoxLayout(chip)
        chip_row.setContentsMargins(14, 12, 14, 12)
        chip_row.setSpacing(12)
        diameter = scale_px_length(_AVATAR_D)
        avatar = QLabel(email[:1].upper() if email and email != "-" else "?", chip)
        avatar.setFixedSize(diameter, diameter)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(
            "background: rgba(128,128,128,0.2); color: palette(text);"
            f" border-radius: {diameter // 2}px; font-size: 15px; font-weight: 700;")
        chip_row.addWidget(avatar, 0, Qt.AlignmentFlag.AlignVCenter)
        self._avatar_label = avatar
        self._show_account_picture(account.get("avatar_url"), diameter)
        id_col = QVBoxLayout()
        id_col.setContentsMargins(0, 0, 0, 0)
        id_col.setSpacing(1)
        email_lbl = QLabel(email, chip)
        email_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        email_lbl.setStyleSheet("font-size: 13px; font-weight: 600; color: palette(text); background: transparent;")
        id_col.addWidget(email_lbl)



        plan_line = plan_name if plan.is_subscriber else self.tr(
            "{plan} · Personal, non-commercial use").format(plan=plan_name)
        plan_lbl = QLabel(plan_line, chip)
        plan_lbl.setStyleSheet(ROW_NOTE_QSS)
        plan_lbl.setWordWrap(True)
        id_col.addWidget(plan_lbl)
        chip_row.addLayout(id_col, 1)
        manage = self._button(self.tr("Manage"), GHOST_BTN_QSS)
        manage.setToolTip(self.tr("Manage account in browser"))
        manage.setAccessibleName(self.tr("Manage account in browser"))
        manage.clicked.connect(lambda: self._open_dashboard("account_card"))
        chip_row.addWidget(manage, 0, Qt.AlignmentFlag.AlignVCenter)
        out = self._button(self.tr("Sign out"), GHOST_BTN_QSS)
        out.clicked.connect(self._on_sign_out)
        chip_row.addWidget(out, 0, Qt.AlignmentFlag.AlignVCenter)
        group.add_row(chip)

        runs = self._runs_row(plan, group)
        if runs is not None:
            group.add_row(runs)

        if not plan.is_subscriber:


            upgrade = self._button(self.tr("Upgrade"), PRIMARY_BTN_QSS)
            upgrade.setAccessibleName(self.tr("Upgrade to Pro"))
            upgrade.clicked.connect(self._on_upgrade)
            points = get_served_pro_points()
            group.add_row(SettingRow(self.tr("Pro: commercial use and more runs"),
                                     " · ".join(points) if points else
                                     self.tr("Plus memory, your instructions and higher effort."),
                                     upgrade, group))
        self._account_col.addWidget(group)

    def _runs_row(self, plan, parent: QWidget) -> QFrame | None:
        """The balance on one line, the reset day at its right, the bar under it."""
        if plan.limit is not None and plan.used is not None:
            left = max(0, int(plan.limit) - int(plan.used))
            title = self.tr("{left} of {limit} runs left").format(
                left=format_count(left), limit=format_count(plan.limit))
        elif plan.used is not None:
            title = self.tr("{used} runs this month").format(used=format_count(plan.used))
        else:
            title = ""
        reset_str = format_reset_date(plan.reset_iso or "")
        reset = self.tr("Resets {date}").format(date=reset_str) if reset_str else ""
        if not title and not reset:
            return None
        row = QFrame(parent)
        row.setObjectName("settingsRow")
        row.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        col = QVBoxLayout(row)
        col.setContentsMargins(14, 10, 14, 12)
        col.setSpacing(8)
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(12)
        title_lbl = QLabel(title or reset, row)
        title_lbl.setStyleSheet(ROW_TITLE_QSS)
        line.addWidget(title_lbl, 1)
        if title and reset:
            reset_lbl = QLabel(reset, row)
            reset_lbl.setStyleSheet(ROW_NOTE_QSS)
            line.addWidget(reset_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addLayout(line)
        if plan.limit is not None and plan.used is not None:
            bar = QProgressBar(row)
            bar.setRange(0, max(int(plan.limit), 1))
            bar.setValue(max(0, min(int(plan.limit - plan.used), int(plan.limit))))
            bar.setTextVisible(False)
            bar.setFixedHeight(6)
            bar.setStyleSheet(PROGRESS_QSS)
            bar.setAccessibleName(title)
            col.addWidget(bar)
        return row

    def _button(self, text: str, qss: str) -> QPushButton:
        btn = QPushButton(text, self._account_box)
        btn.setStyleSheet(qss)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setAutoDefault(False)
        btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        return btn

    @staticmethod
    def _find_subscription(data: dict) -> dict | None:
        subs = data.get("subscriptions") or []
        for pid in (f"{PRODUCT_ID}-pro", PRODUCT_ID):
            for s in subs:
                if isinstance(s, dict) and s.get("product_id") == pid:
                    return s
        return None

    def _show_account_picture(self, url, diameter: int) -> None:
        from .account_avatar import AccountAvatarLoader, cached_avatar_pixmap, is_avatar_url_usable

        if not is_avatar_url_usable(url):
            return
        pixmap = cached_avatar_pixmap(url, diameter)
        if pixmap is not None:
            self._paint_account_picture(pixmap)
            return
        self._avatar_loader = AccountAvatarLoader(self)
        self._avatar_loader.loaded.connect(self._paint_account_picture)
        self._avatar_loader.fetch(url, diameter)

    def _paint_account_picture(self, pixmap) -> None:
        label = self._avatar_label
        if label is None or pixmap is None or pixmap.isNull():
            return
        try:
            label.setText("")
            label.setStyleSheet("background: transparent; border: none;")
            label.setPixmap(pixmap)
        except RuntimeError:
            self._avatar_label = None

    def _cancel_avatar_load(self) -> None:
        loader, self._avatar_loader = self._avatar_loader, None
        if loader is None:
            return
        safe_disconnect(loader, "loaded")
        try:
            loader.abort()
        except RuntimeError:
            pass

    def _open_dashboard(self, source: str = "account_card") -> None:
        self.dashboard_opened.emit(source)
        open_external_url(get_dashboard_url(), parent=self)

    def _on_upgrade(self) -> None:
        """The plans, on the site."""







        if self.receivers(self.upgrade_requested) > 0:
            self.upgrade_requested.emit()
            return
        open_external_url(get_pricing_url(), parent=self)

    def _on_retry(self) -> None:
        self.set_account_info({"loading": True})
        self.refresh_requested.emit()

    def _on_sign_out(self) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(self.tr("Sign out"))
        box.setText(self.tr("Sign out of AI Agent?"))
        box.setInformativeText(self.tr("You can sign back in anytime from QGIS."))
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if exec_dialog(box) != QMessageBox.StandardButton.Yes:
            return
        self.sign_out_requested.emit()
        self.accept()



    def _add_advanced(self, page: Page) -> None:
        """Usage statistics and reset."""






        page.add_group_title(self.tr("Advanced"))




        group = SettingGroup(page)
        self._telemetry_switch = Switch(group, self._telemetry_enabled)
        self._telemetry_switch.toggled.connect(self._on_telemetry_toggled)


        telemetry_note = self.tr("Never your prompts, layers, coordinates or files.")
        telemetry_detail = self.tr("Errors, versions and which features you use, linked to your "
                                   "account. On Pro, only that you used the app and when.")
        self._telemetry_switch.setAccessibleDescription(f"{telemetry_note} {telemetry_detail}")
        telemetry_row = SettingRow(self.tr("Share usage statistics"), telemetry_note,
                                   self._telemetry_switch, group)
        telemetry_row.setToolTip(telemetry_detail)
        group.add_row(telemetry_row)





        self._improve_switch = Switch(group, self._improve_enabled)
        self._improve_switch.toggled.connect(self._on_improve_toggled)
        self._improve_row = SettingRow(self.tr("Help improve AI Agent"),
                                       self._improve_note(False), self._improve_switch, group)
        self._improve_row.setToolTip(self._improve_tip(False))
        group.add_row(self._improve_row)
        reset = QPushButton(self.tr("Reset"), group)
        reset.setStyleSheet(DANGER_GHOST_BTN_QSS)
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.setAutoDefault(False)
        reset.clicked.connect(self._on_reset)
        reset_row = SettingRow(self.tr("Reset all settings"),
                               self.tr("Back to defaults, memory notes included. You stay signed in."),
                               reset, group)
        reset_row.setToolTip(self.tr("Language, style, permissions, your profile and your memory "
                                     "notes go back to their defaults."))
        group.add_row(reset_row)
        page.add(group)
        self._add_danger_zone(page)

    def _add_danger_zone(self, page: Page) -> None:
        """Its own heading and its own group: this one leaves the plugin."""




        page.add_group_title(self.tr("Danger zone"))
        group = SettingGroup(page)
        button = QPushButton(self.tr("Delete"), group)
        button.setStyleSheet(DANGER_GHOST_BTN_QSS)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAutoDefault(False)
        button.clicked.connect(self._on_delete_account)
        self._delete_account_btn = button


        self._delete_account_row = SettingRow(self.tr("Delete my account"), "", button, group)
        group.add_row(self._delete_account_row)
        page.add(group)
        self._sync_delete_account()

    def _sync_delete_account(self) -> None:
        """Nothing to delete until the account has been loaded and named."""
        button = getattr(self, "_delete_account_btn", None)
        row = getattr(self, "_delete_account_row", None)
        if button is None or row is None:
            return
        email = str(getattr(self, "_account_email", "") or "").strip()
        try:
            button.setEnabled(bool(email))
            if email:
                row.set_note(self.tr("Erases your account and its data. All TerraLab plugins stop."))
                row.setToolTip(self.tr("To confirm, you type your email address again."))
            else:
                row.set_note(self.tr("Sign in first to delete your account."))
                row.setToolTip("")
        except RuntimeError:
            pass

    def _on_delete_account(self) -> None:
        email = str(getattr(self, "_account_email", "") or "").strip()
        if not email:
            return
        typed = ask_delete_account(email, self)
        if not typed:
            return
        self.delete_account_requested.emit(typed)
        self.accept()

    def _improve_note(self, subscriber: bool) -> str:
        """What the row says. A paying account is told the answer, not asked it."""
        if subscriber:
            return self.tr("Off on your plan: chats are never read to improve it.")
        return self.tr("Lets us read a chat only to fix a wrong answer or a bug.")

    def _improve_tip(self, subscriber: bool) -> str:
        if subscriber:
            return self.tr("What you write is never read to improve the product, no matter "
                           "how this switch is set.")
        return self.tr("Turn it off any time, with no other effect.")

    def _sync_improve_plan(self, info: dict) -> None:
        """A paid account cannot be asked this: the plan has already refused for it."""
        row = getattr(self, "_improve_row", None)
        switch = getattr(self, "_improve_switch", None)
        if row is None or switch is None:
            return
        if info.get("loading") or info.get("error") or info.get("error_code"):
            return
        subscriber = self._is_subscriber(info)
        try:
            row.set_note(self._improve_note(subscriber))
            row.setToolTip(self._improve_tip(subscriber))
            if subscriber:



                switch.blockSignals(True)
                switch.setChecked(False)
                switch.blockSignals(False)
                set_section_locked(switch)
        except RuntimeError:
            pass

    def set_improve_enabled(self, enabled: bool) -> None:
        self._improve_enabled = bool(enabled)
        switch = getattr(self, "_improve_switch", None)
        if switch is not None:
            switch.blockSignals(True)
            switch.setChecked(self._improve_enabled)
            switch.blockSignals(False)

    def _on_improve_toggled(self, on: bool) -> None:
        self._improve_enabled = bool(on)
        self._show_saved()
        self.improve_toggled.emit(self._improve_enabled)

    def set_telemetry_enabled(self, enabled: bool) -> None:
        self._telemetry_enabled = bool(enabled)
        switch = getattr(self, "_telemetry_switch", None)
        if switch is not None:
            switch.blockSignals(True)
            switch.setChecked(self._telemetry_enabled)
            switch.blockSignals(False)

    def _on_telemetry_toggled(self, on: bool) -> None:
        self._telemetry_enabled = bool(on)
        self._show_saved()
        self.telemetry_toggled.emit(self._telemetry_enabled)

    def _on_reset(self) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(self.tr("Reset all settings"))
        box.setText(self.tr("Reset all settings?"))
        box.setInformativeText(self.tr("Language, style, permissions, your profile and your memory "
                                       "notes go back to their defaults. You stay signed in."))
        reset = box.addButton(self.tr("Reset"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(self.tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        exec_dialog(box)
        if box.clickedButton() is not reset:
            return
        for timer in self._pending.values():
            timer.stop()
        self._store.reset_preferences()



        try:
            clear_memory_notes(self._store)
        except Exception as exc:  # noqa: BLE001 - a folder that cannot be emptied never blocks the reset
            log_warning(f"Memory folder not emptied on reset: {exc}")
        self._values["mode"], self._values["approval"] = self._store.mode, self._store.approval
        self._sync_from_store()
        self._show_saved()
        self.values_changed.emit(self.values())
        self._emit_profile()
