# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later









from __future__ import annotations

from typing import NamedTuple

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.numbers import as_int
from ..core.profile import PROFILE_LINE_MAX_CHARS
from ..core.qt_compat import disconnect_quietly
from ..core.served_state import (
    PRODUCT_ID,
    build_utm_url,
    get_plan_name,
    get_pricing_url,
    get_student_offer,
)
from .account_avatar import AccountAvatarLoader, cached_avatar_pixmap
from .confirm_dialog import ask_confirm
from .delete_account_dialog import ask_delete_account
from .external_links import open_external_url
from .font_scale import scale_qss_font_px
from .quota_card import note_student_offer_shown, open_student_offer
from .settings_pages import (
    DANGER_GHOST_BTN_QSS,
    GHOST_BTN_QSS,
    PRIMARY_BTN_QSS,
    ROW_NOTE_QSS,
    USAGE_KEYS,
    Page,
    SettingGroup,
    SettingRow,
    SkeletonRow,
)
from .style import ACCENT_INK, FONT_HINT, WEIGHT_MEDIUM
from .thumbnail_cache import is_picture_url_usable


_ACCOUNT_OFFLINE_CODES = frozenset({
    "NO_INTERNET", "DNS_ERROR", "CONNECTION_REFUSED", "PROXY_ERROR",
    "TIMEOUT", "SSL_ERROR",
})

_STUDENT_QSS = (f"QPushButton {{ font-size: {FONT_HINT}px; color: {ACCENT_INK}; font-weight: {WEIGHT_MEDIUM};"
                " border: none; background: transparent; padding: 0; text-align: left; }"
                " QPushButton:hover { text-decoration: underline; }")


class _PlanRuns(NamedTuple):


    is_subscriber: bool
    used: int | None
    limit: int | None
    reset_iso: str | None


    trial_ends_on: str | None = None
    trial_interval: str | None = None

    ends_on: str | None = None


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
    ends_on = usage.get("keep_ends_on")
    return _PlanRuns(is_subscriber, used, limit, str(reset) if reset else None,
                     str(trial_end) if trial_end and is_subscriber else None,
                     interval if interval in ("month", "year") else None,
                     str(ends_on) if ends_on and is_subscriber else None)


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
        self._add_danger_zone(page)
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
        self._shell_after_account()
        self._dress()

    def _sync_trial_offer(self, info: dict) -> None:

        failed = bool(info.get("loading") or info.get("error") or info.get("error_code"))
        self.trial_offered = not failed and self._trial_offered(info)
        if failed:
            self.offer_kind = "none"

    def _trial_offered(self, info: dict) -> bool:

        from ..core.plan_moment import offer_kind

        usage = dict(info.get("usage") or {})
        for key in ("trial_days_available", "offer_kind"):
            if key in info and key not in usage:
                usage[key] = info[key]
        from ..core.plan_moment import trial_days

        self.offer_kind = offer_kind(usage, self._is_subscriber(info))
        self.trial_days = trial_days(usage.get("trial_days_available")) or 7
        return self.offer_kind == "trial"

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
        sub = self._find_subscription(account)
        plan = _resolve_plan_runs(usage, sub or {})
        plan_name = (get_plan_name("pro", self.tr("Pro plan")) if plan.is_subscriber
                     else get_plan_name("free", self.tr("Free plan")))
        self._prefill_profile(account)
        self._shell_account(account, email, plan_name, plan)
        self._fill_usage(plan, plan_name)
        if not plan.is_subscriber:
            _note_settings_upsell()

    def _prefill_profile(self, account: dict) -> None:



        first = " ".join(str(account.get("first_name") or "").split())[:PROFILE_LINE_MAX_CHARS]
        job = " ".join(str(account.get("job_title") or "").split())
        company = " ".join(str(account.get("company") or "").split())
        role = (f"{job}, {company}" if job and company else job)[:PROFILE_LINE_MAX_CHARS]
        done = set(filter(None, self._store.profile_prefilled.split(",")))
        changed = False
        for value, attr, edit in ((first, "profile_name", self._name_edit),
                                  (role, "profile_role", self._role_edit)):
            if not value or attr in done:
                continue
            done.add(attr)
            if not getattr(self._store, attr) and not edit.text().strip():
                setattr(self._store, attr, value)
                edit.blockSignals(True)
                edit.setText(value)
                edit.blockSignals(False)
                changed = True
        self._store.profile_prefilled = ",".join(sorted(done))
        if changed:
            self._emit_profile()

    def _student_row(self, parent: QWidget) -> QFrame | None:

        offer = get_student_offer()
        if not offer:
            return None
        row = QFrame(parent)
        row.setObjectName("settingsRow")
        line = QHBoxLayout(row)
        line.setContentsMargins(14, 4, 14, 8)
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

        if not is_picture_url_usable(url):
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
        disconnect_quietly(loader, "loaded")
        try:
            loader.abort()
        except RuntimeError:
            pass

    def _open_dashboard(self, source: str = "account_card") -> None:

        self.dashboard_opened.emit(source)
        open_external_url(build_utm_url("/dashboard/settings", "settings_manage_plan", "plan"), parent=self)

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

    def _add_danger_zone(self, page: Page) -> None:

        page.body.insertSpacing(page.body.count() - 1, 20)
        group = SettingGroup(page)
        button = QPushButton(self.tr("Delete"), group)
        button.setStyleSheet(DANGER_GHOST_BTN_QSS)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAutoDefault(False)
        button.clicked.connect(self._on_delete_account)
        self._delete_account_btn = button


        self._delete_account_row = SettingRow(self.tr("Delete account"), "", button, group)
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

    def _improve_tip(self, subscriber: bool) -> str:

        if subscriber:
            return self.tr("Usage stats: only that you used the app and when. Chats are kept as your History "
                           "retention says, so a conversation survives an update; we may read one, or have a "
                           "model score it, to fix bugs, never to train AI. 0 days of history stores nothing.")
        return self.tr("Usage stats: errors, versions and the features you use, never your prompts, layers or "
                       "files. We may read a chat, or have a model score it, to fix bugs, never to train AI.")

    def _sync_improve_plan(self, info: dict) -> None:

        row = getattr(self, "_improve_row", None)
        if row is None:
            return
        if info.get("loading") or info.get("error") or info.get("error_code"):
            return
        try:
            row.setToolTip(self._improve_tip(self._is_subscriber(info)))
        except RuntimeError:
            pass

    def _help_improve_on(self) -> bool:
        return bool(self._telemetry_enabled) and bool(self._improve_enabled)

    def _sync_help_improve(self) -> None:
        switch = getattr(self, "_improve_switch", None)
        if switch is not None:
            switch.blockSignals(True)
            switch.setChecked(self._help_improve_on())
            switch.blockSignals(False)

    def set_improve_enabled(self, enabled: bool) -> None:
        self._improve_enabled = bool(enabled)
        self._sync_help_improve()

    def set_telemetry_enabled(self, enabled: bool) -> None:
        self._telemetry_enabled = bool(enabled)
        self._sync_help_improve()

    def _on_help_improve_toggled(self, on: bool) -> None:
        self._show_saved()
        if bool(on) != self._telemetry_enabled:
            self._telemetry_enabled = bool(on)
            self.telemetry_toggled.emit(self._telemetry_enabled)
        if bool(on) != self._improve_enabled:
            self._improve_enabled = bool(on)
            self.improve_toggled.emit(self._improve_enabled)
