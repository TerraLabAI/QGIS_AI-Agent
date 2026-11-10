# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


















from __future__ import annotations

from typing import NamedTuple

from qgis.PyQt.QtCore import QLocale, Qt
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.logger import log_warning
from ..core.numbers import as_int
from ..core.profile import clear_memory_notes
from ..core.served_state import (
    PRODUCT_ID,
    get_dashboard_url,
    get_plan_name,
    get_pricing_url,
    get_pro_runs_per_month,
    get_served_pro_points,
    get_student_offer,
    get_support_email,
)
from .account_avatar import AccountAvatarLoader, cached_avatar_pixmap, is_avatar_url_usable
from .confirm_dialog import ask_confirm
from .delete_account_dialog import ask_delete_account
from .external_links import open_external_url
from .font_scale import apply_font_scale_to_tree, scale_px_length, scale_qss_font_px
from .quota_card import _STUDENT_QSS, note_student_offer_shown, open_student_offer, trial_cta
from .settings_pages import (
    DANGER_GHOST_BTN_QSS,
    GET_PRO_BTN_QSS,
    GHOST_BTN_QSS,
    PRIMARY_BTN_QSS,
    PROGRESS_QSS,
    ROW_NOTE_QSS,
    USAGE_KEYS,
    Page,
    SettingGroup,
    SettingRow,
    SkeletonRow,
    Switch,
)
from .settings_retention import RetentionRow
from .shared import format_reset_date, safe_disconnect
from .styles import PANEL_TEXT
from .widgets import ElidedLabel

_AVATAR_D = 36


_ACCOUNT_OFFLINE_CODES = frozenset({
    "NO_INTERNET", "DNS_ERROR", "CONNECTION_REFUSED", "PROXY_ERROR",
    "TIMEOUT", "SSL_ERROR",
})


class _PlanRuns(NamedTuple):


    is_subscriber: bool
    used: int | None
    limit: int | None
    reset_iso: str | None


    trial_ends_on: str | None = None
    trial_interval: str | None = None


def _resolve_plan_runs(usage: dict, sub: dict) -> _PlanRuns:

    usage = usage if isinstance(usage, dict) else {}
    sub = sub if isinstance(sub, dict) else {}
    if "is_subscriber" in usage:
        is_subscriber = bool(usage["is_subscriber"])
    elif "is_free_tier" in usage:
        is_subscriber = not bool(usage["is_free_tier"])
    else:
        is_subscriber = str(sub.get("plan", "")).lower() == "pro"
    used = as_int(usage.get("runs_used"))
    if used is None:
        used = as_int(sub.get("usage_this_month"))
    limit = as_int(usage.get("runs_limit"))
    if limit is None:
        limit = as_int(sub.get("quota_limit"))
    reset = (usage.get("period_end") or usage.get("reset_date")
             or sub.get("current_period_end"))
    trial_end = usage.get("plan_trial_ends_on")
    interval = usage.get("plan_trial_interval")
    return _PlanRuns(is_subscriber, used, limit, str(reset) if reset else None,
                     str(trial_end) if trial_end and is_subscriber else None,
                     interval if interval in ("month", "year") else None)


def _format_count(value) -> str:
    try:
        return QLocale().toString(int(value or 0))
    except (TypeError, ValueError):
        return "0"
_SETTINGS_UPSELL_SEEN = [False]


def _note_settings_upsell() -> None:

    if _SETTINGS_UPSELL_SEEN[0]:
        return
    _SETTINGS_UPSELL_SEEN[0] = True
    try:
        from ..core import telemetry
        from ..core import telemetry_events as ev
        from ..core.served_state import get_pro_offer

        telemetry.track(ev.PRO_UPSELL_VIEWED, {"where": "settings", "cta_source": "settings",
                                               "offer": get_pro_offer()})
    except Exception:  # nosec B110
        pass


class AccountPageMixin:


    def _build_account(self) -> Page:




        page = Page(self.tr("Account"), self.tr("Your sign-in, your plan and your privacy."), self)
        self._account_box = QWidget(page)
        self._account_col = QVBoxLayout(self._account_box)
        self._account_col.setContentsMargins(0, 0, 0, 0)
        self._account_col.setSpacing(14)



        page.add(self._account_box)
        self._add_advanced(page)
        return page

    def set_account_info(self, info: dict) -> None:

        info = dict(info or {})
        if "telemetry_enabled" in info:
            self.set_telemetry_enabled(bool(info["telemetry_enabled"]))
        if "improve_enabled" in info:
            self.set_improve_enabled(bool(info["improve_enabled"]))
        self._sync_improve_plan(info)
        self._sync_trial_offer(info)
        self._clear_account()
        rail = getattr(self, "set_rail_plan", None)
        if rail is not None:
            rail("", "", None, None)


        self._account_email = ""
        code = str(info.get("error_code") or "").strip().upper()
        if info.get("loading"):
            self._account_state = "loading"
        elif code in ("SIGNED_OUT", "INVALID_KEY"):
            self._account_state = "signed_out"
        elif info.get("error") or code:
            self._account_state = "error"
        else:
            self._account_state = "loaded"
        if info.get("loading"):
            group = SettingGroup(self._account_box)
            group.add_row(SkeletonRow(group, self.tr("Loading account info...")))
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

    def _sync_trial_offer(self, info: dict) -> None:

        failed = bool(info.get("loading") or info.get("error") or info.get("error_code"))
        self.trial_offered = not failed and self._trial_offered(info)
        self._sync_pro_notes()

    def _trial_offered(self, info: dict) -> bool:

        days = dict(info.get("usage") or {}).get("trial_days_available", info.get("trial_days_available"))
        return (isinstance(days, int) and not isinstance(days, bool) and 0 < days <= 31
                and not self._is_subscriber(info))

    def _is_subscriber(self, info: dict) -> bool:

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







        code = (code or "").strip().upper()
        action = None
        if code in ("SIGNED_OUT", "INVALID_KEY"):


            title = (self.tr("This computer was signed out") if code == "INVALID_KEY"
                     else self.tr("Sign in to see your account"))
            note = self.tr("Your plan, tasks and settings appear here once you sign in.")
            action = self._button(self.tr("Sign in"), PRIMARY_BTN_QSS)
            action.clicked.connect(self._on_sign_in)
        elif code == "SUBSCRIPTION_INACTIVE":
            title = self.tr("Your last payment may have failed")
            note = self.tr("Update your payment method to fix it.")
            action = self._button(self.tr("Update payment method"), PRIMARY_BTN_QSS)
            action.clicked.connect(lambda: self._open_dashboard("error_card"))
        elif code in _ACCOUNT_OFFLINE_CODES:
            title = self.tr("Could not reach TerraLab")
            note = self.tr("Check your internet connection, then retry.")
            action = self._button(self.tr("Retry"), GHOST_BTN_QSS)
            action.clicked.connect(self._on_retry)
        else:
            title = self.tr("Could not load your account")
            note = self.tr("Try again in a moment.")
            action = self._button(self.tr("Retry"), GHOST_BTN_QSS)
            action.clicked.connect(self._on_retry)
        group = SettingGroup(self._account_box)
        row = SettingRow(title, note, action, group)
        detail = (message or "").strip()
        if detail and detail != title and code not in ("SIGNED_OUT", "INVALID_KEY"):
            row.setToolTip(detail if not code else f"{detail} ({code})")
        group.add_row(row)
        self._account_col.addWidget(group)

    def _show_account(self, account: dict, usage: dict) -> None:



        email = str(account.get("email") or "-")
        self._account_email = "" if email == "-" else email
        group = SettingGroup(self._account_box)

        sub = self._find_subscription(account)
        plan = _resolve_plan_runs(usage, sub or {})
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
            "background: rgba(128,128,128,0.2); color: " + PANEL_TEXT + ";"
            f" border-radius: {diameter // 2}px; font-size: 15px; font-weight: 700;")
        chip_row.addWidget(avatar, 0, Qt.AlignmentFlag.AlignVCenter)
        self._avatar_label = avatar
        self._show_account_picture(account.get("avatar_url"), diameter)
        id_col = QVBoxLayout()
        id_col.setContentsMargins(0, 0, 0, 0)
        id_col.setSpacing(1)
        email_lbl = ElidedLabel(email, chip, Qt.TextElideMode.ElideMiddle)
        email_lbl.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {PANEL_TEXT}; background: transparent;")
        id_col.addWidget(email_lbl)
        chip_row.addLayout(id_col, 1)
        out = self._button(self.tr("Sign out"), GHOST_BTN_QSS)
        out.clicked.connect(self._on_sign_out)
        chip_row.addWidget(out, 0, Qt.AlignmentFlag.AlignVCenter)
        group.add_row(chip)

        group.add_row(self._plan_row(plan, plan_name, group))
        student = self._student_row(group)
        if student is not None:
            group.add_row(student)
        self._account_col.addWidget(group)

        if not plan.is_subscriber:
            _note_settings_upsell()

    def _plan_row(self, plan, plan_name: str, parent: QWidget) -> QFrame:



        if plan.limit is not None and plan.used is not None:
            left = max(0, int(plan.limit) - int(plan.used))
            runs = self.tr("{left} of {limit} tasks left").format(
                left=_format_count(left), limit=_format_count(plan.limit))
        elif plan.used is not None:
            runs = self.tr("{used} tasks this month").format(used=_format_count(plan.used))
        else:
            runs = ""
        reset_str = format_reset_date(plan.reset_iso or "")
        reset = self.tr("Resets {date}").format(date=reset_str) if reset_str else ""
        title = plan_name if plan.is_subscriber else self.tr(
            "{plan} · Personal, non-commercial use").format(plan=plan_name)


        trial_end = format_reset_date(getattr(plan, "trial_ends_on", None) or "")
        if trial_end:
            title = self.tr("{plan} · Free trial").format(plan=plan_name)
            then = {"month": self.tr("Then billed monthly"),
                    "year": self.tr("Then billed yearly")}.get(getattr(plan, "trial_interval", None) or "", "")
            reset = " · ".join(part for part in (
                self.tr("Free trial ends {date}").format(date=trial_end), then) if part)
        row = QFrame(parent)
        row.setObjectName("settingsRow")
        row.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        col = QVBoxLayout(row)
        col.setContentsMargins(14, 10, 14, 12)
        col.setSpacing(8)
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(12)
        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(2)


        title_lbl = QLabel(title, row)
        title_lbl.setStyleSheet(ROW_NOTE_QSS)
        title_lbl.setWordWrap(True)
        words.addWidget(title_lbl)
        if runs:
            runs_lbl = QLabel(runs, row)
            runs_lbl.setStyleSheet(scale_qss_font_px(
                "font-size: 20px; font-weight: 600; color: " + PANEL_TEXT + "; background: transparent;"))
            runs_lbl.setWordWrap(True)
            words.addWidget(runs_lbl)
        line.addLayout(words, 1)
        if plan.is_subscriber:
            manage = self._button(self.tr("Manage plan"), GHOST_BTN_QSS)
            manage.setToolTip(self.tr("Your plan, payment method and invoices, on the TerraLab "
                                      "website. Payment never happens inside QGIS."))
            manage.clicked.connect(lambda: self._open_dashboard("account_card"))
        else:


            manage = self._button(trial_cta() if getattr(self, "trial_offered", False)
                                  else self.tr("Get Pro"), GET_PRO_BTN_QSS)
            manage.clicked.connect(lambda: self._on_upgrade("account"))
        line.addWidget(manage, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addLayout(line)
        if plan.is_subscriber:
            row.setToolTip(self.tr("Need more than {n} tasks a month? Write to {email}.").format(
                n=get_pro_runs_per_month(), email=get_support_email()))
        rail = getattr(self, "set_rail_plan", None)
        if rail is not None:
            left_n = max(0, int(plan.limit) - int(plan.used)) if (
                plan.limit is not None and plan.used is not None) else None
            rail(plan_name, runs, left_n, plan.limit)
        if plan.limit is not None and plan.used is not None:
            bar = QProgressBar(row)
            bar.setRange(0, max(int(plan.limit), 1))
            bar.setValue(max(0, min(int(plan.limit - plan.used), int(plan.limit))))
            bar.setTextVisible(False)
            bar.setFixedHeight(4)
            bar.setStyleSheet(PROGRESS_QSS)
            bar.setAccessibleName(runs)
            col.addWidget(bar)
        if reset:
            reset_lbl = QLabel(reset, row)
            reset_lbl.setStyleSheet(ROW_NOTE_QSS)
            reset_lbl.setWordWrap(True)
            col.addWidget(reset_lbl)
        if not plan.is_subscriber:

            points = get_served_pro_points()
            adds = QLabel(self.tr("What Pro adds: {points}").format(
                points=", ".join(points) if points else
                self.tr("commercial use, more tasks, memory, your instructions and higher effort")), row)
            adds.setTextFormat(Qt.TextFormat.PlainText)
            adds.setStyleSheet(ROW_NOTE_QSS)
            adds.setWordWrap(True)
            col.addWidget(adds)
        return row

    def _student_row(self, parent: QWidget) -> QFrame | None:

        offer = get_student_offer()
        if not offer:
            return None
        row = QFrame(parent)
        row.setObjectName("settingsRow")
        line = QHBoxLayout(row)
        line.setContentsMargins(10, 4, 14, 8)
        if offer["state"] == "offer":
            link = QPushButton(offer["label"].replace("&", "&&"), row)
            link.setStyleSheet(scale_qss_font_px(_STUDENT_QSS))
            link.setCursor(Qt.CursorShape.PointingHandCursor)
            link.setAutoDefault(False)
            link.clicked.connect(lambda: open_student_offer("account", self))
            line.addWidget(link, 0, Qt.AlignmentFlag.AlignLeft)
            note_student_offer_shown("account")
        else:
            text = QLabel(offer["label"], row)
            text.setTextFormat(Qt.TextFormat.PlainText)
            text.setStyleSheet(ROW_NOTE_QSS)
            text.setWordWrap(True)
            line.setContentsMargins(14, 4, 14, 8)
            line.addWidget(text, 1)
        line.addStretch(1)
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

    def _on_upgrade(self, surface: str = "account") -> None:







        self.upgrade_surface = surface
        if self.receivers(self.upgrade_requested) > 0:
            self.upgrade_requested.emit()
            return
        open_external_url(get_pricing_url(), parent=self)

    def _on_retry(self) -> None:
        self.set_account_info({"loading": True})
        self.refresh_requested.emit()

    def _on_sign_in(self) -> None:

        self.sign_in_requested.emit()
        self.accept()

    def _on_sign_out(self) -> None:


        if not ask_confirm(self, title=self.tr("Sign out of AI Agent?"),
                           note=self.tr("You can sign back in anytime from QGIS."),
                           confirm_text=self.tr("Sign out"), glyph="person",
                           object_name="signOutConfirm"):
            return
        self.sign_out_requested.emit()
        self.accept()

    def _add_advanced(self, page: Page) -> None:







        page.add_group_title(self.tr("Privacy"))

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

        self._retention = RetentionRow(self, group)
        group.add_row(self._retention.row)
        divider = group.layout().itemAt(group.layout().count() - 2)
        self._retention.divider = divider.widget() if divider is not None else None
        self._retention.load()





        self._improve_switch = Switch(group, self._improve_enabled)
        self._improve_switch.toggled.connect(self._on_improve_toggled)
        self._improve_row = SettingRow(self.tr("Allow chat review"),
                                       self._improve_note(False), self._improve_switch, group)
        self._improve_row.setToolTip(self._improve_tip(False))
        group.add_row(self._improve_row)

        reset = QPushButton(self.tr("Reset"), group)
        reset.setStyleSheet(GHOST_BTN_QSS)
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.setAutoDefault(False)
        reset.clicked.connect(self._on_reset)
        reset_row = SettingRow(self.tr("Reset all settings"), "", reset, group)
        reset_row.setToolTip(self.tr("Language, style, permissions, your profile and your memory "
                                     "notes go back to their defaults."))
        group.add_row(reset_row)
        page.add(group)
        self._add_danger_zone(page)

    def _add_danger_zone(self, page: Page) -> None:

        page.body.insertSpacing(page.body.count() - 1, 20)
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

        button = getattr(self, "_delete_account_btn", None)
        row = getattr(self, "_delete_account_row", None)
        if button is None or row is None:
            return
        email = str(getattr(self, "_account_email", "") or "").strip()
        state = getattr(self, "_account_state", "loading")
        try:
            button.setEnabled(bool(email))
            if email:
                row.set_note(self.tr("Erases your account and its data. All TerraLab plugins stop."))
                row.setToolTip(self.tr("To confirm, you type your email address again."))
            elif state == "signed_out":
                row.set_note(self.tr("Sign in first to delete your account."))
                row.setToolTip("")
            else:


                row.set_note("")
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

        if subscriber:
            return self.tr("We may read a chat only to fix a bug, never to train AI. Chats are kept 30 days.")
        return self.tr("We may read a chat only to fix a wrong answer or a bug, never to train AI.")

    def _improve_tip(self, subscriber: bool) -> str:
        if subscriber:
            return self.tr("Your chats are kept 30 days so a conversation survives an update. "
                           "Off, the team never reads them. 0 days of history stores nothing.")
        return self.tr("Turn it off any time, with no other effect.")

    def _sync_improve_plan(self, info: dict) -> None:

        row = getattr(self, "_improve_row", None)
        if row is None:
            return
        if info.get("loading") or info.get("error") or info.get("error_code"):
            return
        subscriber = self._is_subscriber(info)
        try:
            row.set_note(self._improve_note(subscriber))
            row.setToolTip(self._improve_tip(subscriber))
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
        if not ask_confirm(self, title=self.tr("Reset all settings?"),
                           note=self.tr("Language, style, permissions, your profile and your "
                                        "memory notes go back to their defaults. You stay "
                                        "signed in."),
                           confirm_text=self.tr("Reset"), glyph="warning",
                           object_name="resetConfirm"):
            return
        for timer in self._pending.values():
            timer.stop()
        self._store.reset_preferences()



        try:
            clear_memory_notes(self._store)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Memory folder not emptied on reset: {exc}")
        self._values["mode"], self._values["approval"] = self._store.mode, self._store.approval
        self._sync_from_store()
        self._show_saved()
        self.values_changed.emit(self.values())
        self._emit_profile()
