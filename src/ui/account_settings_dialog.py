# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Account settings dialog for AI Agent."""










from __future__ import annotations

from typing import NamedTuple

from qgis.PyQt.QtCore import QLocale, QRect, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .external_links import open_external_url
from .font_scale import apply_font_scale_to_tree, scale_px_length
from .shared import (
    PRODUCT_ID,
    PRODUCT_NAME,
    available_screen_rect,
    exec_dialog,
    format_reset_date,
    get_dashboard_url,
    get_plan_name,
    get_privacy_url,
    get_support_email,
    get_terms_url,
    get_upgrade_url,
    safe_disconnect,
    served_label_map,
)
from .styles import (
    _BTN_BLUE_OUTLINE,
    _BTN_BLUE_PRIMARY,
    BRAND_BLUE,
    BRAND_BLUE_HOVER,
    BRAND_GREEN,
    BRAND_GREEN_TEXT,
    BRAND_RED,
    MUTED_TEXT,
    MUTED_TEXT_SOFT,
    WARNING_TEXT,
)
from .widgets import WrapLabel


_SCREEN_MARGIN_PX = 48


_ACCOUNT_OFFLINE_CODES = frozenset({
    "NO_INTERNET", "DNS_ERROR", "CONNECTION_REFUSED", "PROXY_ERROR",
    "TIMEOUT", "SSL_ERROR",
})

_STATUS_DISPLAY = {
    "active": ("Active", BRAND_GREEN_TEXT),
    "trialing": ("Free trial", WARNING_TEXT),
    "past_due": ("Payment due", WARNING_TEXT),
    "canceled": ("Cancelled", BRAND_RED),
}

_LINK_BTN = (
    f"QPushButton {{ border: none; color: {BRAND_BLUE}; font-size: 11px;"
    f" text-decoration: underline; padding: 2px 4px; background: transparent; }}"
    f"QPushButton:hover {{ color: {BRAND_BLUE_HOVER}; }}"
)


_MANAGE_LINK = (
    f"QPushButton {{ border: none; background: transparent; color: {BRAND_BLUE};"
    f" font-size: 11px; font-weight: 600; padding: 2px 4px; }}"
    f"QPushButton:hover {{ color: {BRAND_BLUE_HOVER};"
    f" text-decoration: underline; }}"
)
_SIGNOUT_LINK = (
    "QPushButton { border: none; background: transparent; color: palette(text);"
    " font-size: 11px; text-decoration: underline; padding: 2px 4px; }"
    f"QPushButton:hover {{ color: {BRAND_RED}; }}"
)
_CARD_STYLE = (
    "QFrame { background: rgba(128,128,128,0.08);"
    " border: 1px solid rgba(128,128,128,0.2);"
    " border-radius: 6px; }"
    "QLabel { background: transparent; border: none; }"
    "QPushButton { background: transparent; }"
)
_COPIED_MS = 2000


class PlanRuns(NamedTuple):
    """What the plan card draws: who the user is and what is spent."""

    is_subscriber: bool
    used: int | None
    limit: int | None
    reset_iso: str | None


def _as_int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def resolve_plan_runs(usage: dict, sub: dict) -> PlanRuns:
    """Read the newer usage names first, the account row second."""
    usage = usage if isinstance(usage, dict) else {}
    sub = sub if isinstance(sub, dict) else {}
    if "is_subscriber" in usage:
        is_subscriber = bool(usage["is_subscriber"])
    elif "is_free_tier" in usage:
        is_subscriber = not bool(usage["is_free_tier"])
    else:
        is_subscriber = str(sub.get("plan", "")).lower() == "pro"
    used = _as_int(usage.get("runs_used"))
    if used is None:
        used = _as_int(sub.get("usage_this_month"))
    limit = _as_int(usage.get("runs_limit"))
    if limit is None:
        limit = _as_int(sub.get("quota_limit"))
    reset = (usage.get("period_end") or usage.get("reset_date")
             or sub.get("current_period_end"))
    return PlanRuns(is_subscriber, used, limit, str(reset) if reset else None)


def format_count(value) -> str:
    try:
        return QLocale().toString(int(value or 0))
    except (TypeError, ValueError):
        return "0"


class AccountSettingsDialog(QDialog):
    """Account, plan, contact and privacy, rendered from dicts."""

    sign_out_requested = pyqtSignal()
    refresh_requested = pyqtSignal()
    telemetry_toggled = pyqtSignal(bool)
    dashboard_opened = pyqtSignal(str)
    upgrade_requested = pyqtSignal()

    def __init__(self, parent=None, account: dict | None = None,
                 usage: dict | None = None, telemetry_enabled: bool = True,
                 contact_call_url: str = "", support_email: str = ""):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Account settings"))
        self.setModal(True)
        self.setMinimumWidth(scale_px_length(460))
        self.setMaximumWidth(scale_px_length(640))
        self._telemetry_enabled = bool(telemetry_enabled)
        self._contact_call_url = contact_call_url or ""
        self._support_email = support_email or get_support_email()
        self._avatar_label = None
        self._avatar_loader = None
        self._avatar_requested = False
        self._telemetry_checkbox = None

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 16, 16, 12)
        self._layout.setSpacing(12)

        self._loading_label = QLabel(self.tr("Loading account info..."), self)
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label.setStyleSheet("color: palette(text); padding: 16px;")
        self._layout.addWidget(self._loading_label)

        self._error_widget = QWidget(self)
        error_layout = QVBoxLayout(self._error_widget)
        error_layout.setContentsMargins(0, 0, 0, 0)
        error_layout.setSpacing(8)
        self._error_label = QLabel(self._error_widget)
        self._error_label.setWordWrap(True)
        self._error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_label.setStyleSheet(f"color: {BRAND_RED}; padding: 12px;")
        error_layout.addWidget(self._error_label)
        self._retry_btn = QPushButton(self.tr("Retry"), self._error_widget)



        self._retry_btn.setMaximumWidth(max(scale_px_length(100),
                                            self._retry_btn.sizeHint().width()))
        self._retry_btn.setAutoDefault(False)
        self._retry_btn.setDefault(True)
        self._retry_btn.clicked.connect(self._on_retry)
        self._error_manage_btn = QPushButton(self.tr("Update payment method"), self._error_widget)
        self._error_manage_btn.setStyleSheet(_BTN_BLUE_PRIMARY)
        self._error_manage_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._error_manage_btn.setAutoDefault(False)
        self._error_manage_btn.setMinimumHeight(36)
        self._error_manage_btn.setToolTip(self.tr("Opens your terra-lab.ai account in the browser."))
        self._error_manage_btn.clicked.connect(lambda: self._open_dashboard("error_card"))
        self._error_manage_btn.setVisible(False)
        self._error_sign_out_btn = QPushButton(self.tr("Sign out"), self._error_widget)
        self._error_sign_out_btn.setStyleSheet(_LINK_BTN)
        self._error_sign_out_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._error_sign_out_btn.setAutoDefault(False)
        self._error_sign_out_btn.clicked.connect(lambda: self._on_sign_out("error_card"))
        retry_row = QHBoxLayout()
        retry_row.addStretch()
        retry_row.addWidget(self._retry_btn)
        retry_row.addStretch()
        error_layout.addLayout(retry_row)
        error_layout.addWidget(self._error_manage_btn)
        signout_row = QHBoxLayout()
        signout_row.addStretch()
        signout_row.addWidget(self._error_sign_out_btn)
        signout_row.addStretch()
        error_layout.addLayout(signout_row)
        self._error_widget.setVisible(False)
        self._layout.addWidget(self._error_widget)

        self._content_widget = QWidget(self)
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(10)


        self._content_scroll = QScrollArea(self)
        self._content_scroll.setWidgetResizable(True)
        self._content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._content_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }")
        self._content_scroll.setWidget(self._content_widget)
        self._content_scroll.setVisible(False)
        self._layout.addWidget(self._content_scroll, 1)

        if account is not None:
            self.set_account_data(account, usage)
        else:
            self.show_loading()



    def show_loading(self) -> None:
        self._loading_label.setVisible(True)
        self._error_widget.setVisible(False)
        self._content_scroll.setVisible(False)

    def set_account_data(self, account: dict, usage: dict | None = None) -> None:
        """Rebuild every card from the account and usage dicts."""
        self._loading_label.setVisible(False)
        self._error_widget.setVisible(False)
        account = account if isinstance(account, dict) else {}
        usage = usage if isinstance(usage, dict) else {}
        self._clear_cards()
        self._content_layout.addWidget(self._build_account_card(account))
        sub = self._find_subscription(account)
        plan = resolve_plan_runs(usage, sub or {})
        self._content_layout.addWidget(self._build_plan_card(sub or {}, plan))
        self._content_layout.addWidget(self._build_contact_card(plan.is_subscriber))
        self._content_layout.addWidget(self._build_privacy_card())
        self._content_layout.addWidget(self._build_legal_footer())
        self._content_scroll.setVisible(True)
        apply_font_scale_to_tree(self)
        self.adjustSize()
        self._fit_to_screen()

    def show_error(self, message: str, code: str = "") -> None:
        """One plain sentence per failure kind, and the one action it has."""
        self._loading_label.setVisible(False)
        code = (code or "").strip().upper()
        if code == "SUBSCRIPTION_INACTIVE":
            self._error_label.setText(self.tr(
                "There's a problem with your subscription. Your last payment "
                "may have failed. Open your account to update your payment "
                "method or review your plan."))
            self._retry_btn.setVisible(False)
            self._error_manage_btn.setVisible(True)
        elif code == "INVALID_KEY":
            self._error_label.setText(self.tr(
                "This computer is no longer signed in. Sign out, then sign in "
                "again to reconnect it."))
            self._retry_btn.setVisible(False)
            self._error_manage_btn.setVisible(False)
        elif code in _ACCOUNT_OFFLINE_CODES:
            self._error_label.setText(self.tr(
                "Could not reach TerraLab. Check your internet connection, "
                "then try again."))
            self._retry_btn.setVisible(True)
            self._error_manage_btn.setVisible(False)
        else:
            self._error_label.setText(str(message or "")[:1000] or self.tr(
                "Could not load your account. Try again in a moment."))
            self._retry_btn.setVisible(True)
            self._error_manage_btn.setVisible(False)
        self._error_widget.setVisible(True)


        self._clear_cards()
        self._content_layout.addWidget(self._build_privacy_card())
        self._content_scroll.setVisible(True)
        apply_font_scale_to_tree(self)
        self.adjustSize()
        self._fit_to_screen()

    def set_telemetry_enabled(self, enabled: bool) -> None:
        self._telemetry_enabled = bool(enabled)
        box = self._telemetry_checkbox
        if box is not None:
            box.blockSignals(True)
            box.setChecked(self._telemetry_enabled)
            box.blockSignals(False)

    def _clear_cards(self) -> None:
        self._cancel_avatar_load()
        self._avatar_label = None
        self._avatar_requested = False
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:



                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _on_retry(self) -> None:
        self.show_loading()
        self.refresh_requested.emit()

    @staticmethod
    def _find_subscription(data: dict) -> dict | None:
        subs = data.get("subscriptions") if isinstance(data, dict) else []
        if not isinstance(subs, (list, tuple)):
            return None
        for pid in (f"{PRODUCT_ID}-pro", PRODUCT_ID):
            for s in subs:
                if isinstance(s, dict) and s.get("product_id") == pid:
                    return s
        return None



    def _build_account_card(self, data: dict) -> QFrame:
        card = QFrame(self)
        card.setStyleSheet(_CARD_STYLE)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        email = str(data.get("email") or "-")

        chip = QFrame(card)
        chip.setStyleSheet(
            "QFrame { background: palette(base);"
            " border: 1px solid rgba(128,128,128,0.25); border-radius: 8px; }"
            "QLabel { background: transparent; border: none; }")
        chip_row = QHBoxLayout(chip)
        chip_row.setContentsMargins(12, 10, 12, 10)
        chip_row.setSpacing(11)
        diameter = scale_px_length(38)
        avatar = QLabel(email[:1].upper() if email and email != "-" else "?", chip)
        avatar.setFixedSize(diameter, diameter)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(
            f"background: {BRAND_GREEN}; color: #14210A;"
            f" border-radius: {diameter // 2}px; font-size: 17px; font-weight: 700;")
        chip_row.addWidget(avatar)
        self._avatar_label = avatar
        self._show_account_picture(data.get("avatar_url"), diameter)

        id_col = QVBoxLayout()
        id_col.setSpacing(2)
        email_val = QLabel(email, chip)
        email_val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        email_val.setStyleSheet("font-size: 13px; font-weight: 600; color: palette(text);")
        id_col.addWidget(email_val)
        status_lbl = QLabel("✓ " + self.tr("Connected"), chip)
        status_lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {BRAND_GREEN_TEXT};")
        id_col.addWidget(status_lbl)
        chip_row.addLayout(id_col, 1)

        sign_out_btn = QPushButton(self.tr("Sign out"), chip)
        sign_out_btn.setStyleSheet(_SIGNOUT_LINK)
        sign_out_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sign_out_btn.setAutoDefault(False)
        sign_out_btn.clicked.connect(lambda: self._on_sign_out("account_card"))
        chip_row.addWidget(sign_out_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(chip)
        return card

    def _show_account_picture(self, url, diameter: int) -> None:
        from .account_avatar import (
            AccountAvatarLoader,
            cached_avatar_pixmap,
            is_avatar_url_usable,
        )

        if not is_avatar_url_usable(url):
            return
        pixmap = cached_avatar_pixmap(url, diameter)
        if pixmap is not None:
            self._paint_account_picture(pixmap)
            return
        if self._avatar_requested:
            return
        self._avatar_requested = True
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



    def _build_plan_card(self, sub: dict, plan: PlanRuns) -> QFrame:
        card = QFrame(self)
        card.setStyleSheet(_CARD_STYLE)
        col = QVBoxLayout(card)
        col.setContentsMargins(12, 10, 12, 12)
        col.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        title = QLabel(f"<b>{PRODUCT_NAME}</b>", card)
        title.setStyleSheet("font-size: 13px; color: palette(text);")
        header.addWidget(title, 1)
        manage_btn = QPushButton(self.tr("Manage account") + " ↗", card)
        manage_btn.setStyleSheet(_MANAGE_LINK)
        manage_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        manage_btn.setAutoDefault(False)
        manage_btn.setToolTip(self.tr("Opens your terra-lab.ai dashboard in the browser."))
        manage_btn.clicked.connect(lambda: self._open_dashboard("account_card"))
        header.addWidget(manage_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addLayout(header)

        status = str(sub.get("status") or "active")
        status_text, status_color = _STATUS_DISPLAY.get(
            status, (status.replace("_", " ").capitalize(), BRAND_RED))





        status_text = served_label_map("subscription_status_labels").get(status) or self.tr(status_text)
        plan_name = (get_plan_name("pro", self.tr("Pro plan")) if plan.is_subscriber
                     else get_plan_name("free", self.tr("Free plan")))
        plan_status = QLabel(
            f"{plan_name} · <span style='color:{status_color};'>{status_text}</span>", card)
        plan_status.setStyleSheet("font-size: 12px; color: palette(text);")
        col.addWidget(plan_status)

        if not plan.is_subscriber:
            note = QLabel(self.tr("Personal, non-commercial use. A paid plan adds "
                                  "commercial use for one person."), card)
            note.setWordWrap(True)
            note.setStyleSheet("font-size: 11px; color: palette(text);")
            col.addWidget(note)

        if plan.limit is not None and plan.used is not None:
            left = max(0, plan.limit - plan.used)
            spent = left <= 0
            text_color = BRAND_RED if spent else "palette(text)"
            fill = BRAND_RED if spent else BRAND_GREEN
            runs_text = self.tr("{used} of {limit} runs used this month").format(
                used=format_count(plan.used), limit=format_count(plan.limit))
            col.addWidget(self._dashboard_quota_label(runs_text, text_color))
            col.addWidget(self._credits_bar(left, plan.limit, fill))
        elif plan.used is not None:
            runs_lbl = QLabel(self.tr("{used} runs this month").format(used=format_count(plan.used)), card)
            runs_lbl.setStyleSheet("font-size: 12px; color: palette(text);")
            col.addWidget(runs_lbl)

        reset_str = format_reset_date(plan.reset_iso or "")
        if reset_str:
            reset_label = QLabel(self.tr("Resets {date}").format(date=reset_str), card)
            reset_label.setStyleSheet("font-size: 10px; color: palette(text);")
            col.addWidget(reset_label)

        if not plan.is_subscriber:
            col.addSpacing(6)
            upgrade_btn = QPushButton(self.tr("Upgrade to Pro"), card)
            upgrade_btn.setStyleSheet(_BTN_BLUE_PRIMARY)
            upgrade_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            upgrade_btn.setAutoDefault(False)
            upgrade_btn.setToolTip(self.tr("Opens terra-lab.ai in your browser."))
            upgrade_btn.clicked.connect(self._on_upgrade)
            col.addWidget(upgrade_btn)
        return card

    def _dashboard_quota_label(self, text: str, text_color: str) -> QLabel:
        label = QLabel(
            f'<a href="{get_dashboard_url()}" style="text-decoration: none;">'
            f'<span style="color: {text_color};">{text}</span>'
            f' <span style="color: {BRAND_BLUE};">↗</span></a>', self)
        label.setOpenExternalLinks(True)
        label.setCursor(Qt.CursorShape.PointingHandCursor)
        label.setToolTip(self.tr("Opens your terra-lab.ai dashboard: your plan, your runs "
                                 "and your payment details."))
        label.setStyleSheet(f"font-size: 12px; color: {text_color};")
        return label

    @staticmethod
    def _credits_bar(remaining: int, total: int, fill: str) -> QProgressBar:
        bar = QProgressBar()
        bar.setRange(0, max(int(total), 1))
        bar.setValue(max(0, min(int(remaining), int(total))))
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        bar.setStyleSheet(
            "QProgressBar { background: rgba(128,128,128,0.15);"
            " border: none; border-radius: 3px; }"
            f"QProgressBar::chunk {{ background: {fill}; border-radius: 3px; }}")
        return bar

    def _on_upgrade(self) -> None:
        self.upgrade_requested.emit()
        open_external_url(get_upgrade_url(), parent=self)



    def _build_contact_card(self, is_subscriber: bool) -> QFrame:
        card = QFrame(self)
        card.setStyleSheet(_CARD_STYLE)




        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        card.setSizePolicy(policy)
        row = QHBoxLayout(card)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(10)
        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(2)
        title_text = self.tr("Need more than Pro?") if is_subscriber else self.tr("Working in a team?")
        title = QLabel(f"<b>{title_text}</b>", card)
        title.setStyleSheet("font-size: 13px; color: palette(text);")
        title.setWordWrap(True)
        words.addWidget(title)




        body = WrapLabel(self.tr("Custom quota, team seats, invoices, or a custom AI solution."), card)
        body.setStyleSheet("font-size: 12px; color: palette(text);")
        words.addWidget(body)
        row.addLayout(words, 1)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        copy_btn = QPushButton(self.tr("Copy email"), card)
        copy_btn.setStyleSheet(_BTN_BLUE_OUTLINE)
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setAutoDefault(False)
        copy_btn.clicked.connect(lambda _=False, btn=copy_btn: self._on_contact_copy(btn))
        buttons.addWidget(copy_btn, 0)
        if self._contact_call_url:
            call_btn = QPushButton(self.tr("Book a call"), card)
            call_btn.setStyleSheet(_BTN_BLUE_OUTLINE)
            call_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            call_btn.setAutoDefault(False)
            call_btn.clicked.connect(
                lambda _=False, url=self._contact_call_url: open_external_url(url, parent=self))
            buttons.addWidget(call_btn, 0)
        row.addLayout(buttons, 0)
        row.setAlignment(buttons, Qt.AlignmentFlag.AlignVCenter)
        return card

    def _on_contact_copy(self, button: QPushButton) -> None:
        try:
            QApplication.clipboard().setText(self._support_email)
        except (RuntimeError, AttributeError):
            return
        button.setText(self.tr("Copied"))
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._restore_copy_label(button))
        timer.start(_COPIED_MS)

    def _restore_copy_label(self, button) -> None:
        try:
            button.setText(self.tr("Copy email"))
        except RuntimeError:
            pass  # nosec B110 - the card was rebuilt under us



    def _build_privacy_card(self) -> QFrame:
        card = QFrame(self)
        card.setStyleSheet(_CARD_STYLE)
        col = QVBoxLayout(card)
        col.setContentsMargins(12, 10, 12, 10)
        col.setSpacing(6)
        title = QLabel(f"<b>{self.tr('Privacy')}</b>", card)
        title.setStyleSheet("font-size: 13px; color: palette(text);")
        col.addWidget(title)
        self._telemetry_checkbox = QCheckBox(self.tr("Share usage statistics with TerraLab"), card)
        self._telemetry_checkbox.setChecked(self._telemetry_enabled)
        self._telemetry_checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self._telemetry_checkbox.setStyleSheet("font-size: 12px; color: palette(text);")
        self._telemetry_checkbox.toggled.connect(self._on_telemetry_toggled)
        col.addWidget(self._telemetry_checkbox)
        caption = QLabel(self.tr(
            "Errors, versions and which features you use, linked to your account. "
            "Never your prompts, your layers, your coordinates or your files. "
            "On Pro, lifecycle and counts only, no content."), card)
        caption.setWordWrap(True)
        caption.setStyleSheet(f"font-size: 11px; color: {MUTED_TEXT};")
        col.addWidget(caption)
        return card

    def _on_telemetry_toggled(self, enabled: bool) -> None:
        self._telemetry_enabled = bool(enabled)
        self.telemetry_toggled.emit(self._telemetry_enabled)

    def _build_legal_footer(self) -> QFrame:
        footer = QFrame(self)
        footer.setObjectName("legalFooter")
        footer.setStyleSheet(
            "QFrame#legalFooter { border: none; border-top: 1px solid rgba(127,127,127,0.18); }")
        row = QHBoxLayout(footer)
        row.setContentsMargins(0, 8, 0, 0)
        row.setSpacing(0)
        row.addStretch()
        legal = QLabel(
            f'<a href="{get_terms_url()}" style="color: {MUTED_TEXT};'
            f' text-decoration: none;">{self.tr("Terms")}</a>'
            f' <span style="color: {MUTED_TEXT_SOFT};">·</span> '
            f'<a href="{get_privacy_url()}" style="color: {MUTED_TEXT};'
            f' text-decoration: none;">{self.tr("Privacy")}</a>', footer)
        legal.setOpenExternalLinks(True)
        legal.setStyleSheet("font-size: 10px;")
        row.addWidget(legal)
        row.addStretch()
        return footer



    def _fit_to_screen(self) -> None:
        """Open at the height the cards need, capped by the screen, centred."""
        available = available_screen_rect(self)
        if available is None:
            return
        width = min(self.width(), available.width())
        frame_extra = max(0, self.frameGeometry().height() - self.height())
        cap = max(1, available.height() - frame_extra - _SCREEN_MARGIN_PX)
        height = min(cap, max(1, self.minimumSizeHint().height(), self._height_for_cards(width)))
        if height != self.height() or width != self.width():
            self.resize(width, height)
        frame = self.frameGeometry()
        frame.moveCenter(self._centre_anchor(available))
        left = min(max(available.left(), frame.left()),
                   max(available.left(), available.right() - frame.width()))
        top = min(max(available.top(), frame.top()),
                  max(available.top(), available.bottom() - frame.height()))
        self.move(left, top)

    def _centre_anchor(self, available: QRect):
        try:
            parent = self.parentWidget()
            if parent is not None and parent.isVisible():
                centre = parent.window().frameGeometry().center()
                if available.contains(centre):
                    return centre
        except (AttributeError, RuntimeError):
            pass  # nosec B110 - placement is best-effort
        return available.center()

    def _height_for_cards(self, width: int) -> int:
        self._layout.activate()
        margins = self._layout.contentsMargins()
        border = self._content_scroll.frameWidth() * 2
        viewport_width = max(1, width - margins.left() - margins.right() - border)
        inner = self._content_widget
        needed = inner.sizeHint().height()
        if inner.hasHeightForWidth():
            needed = max(needed, inner.heightForWidth(viewport_width))
        extra = 0
        if self._error_widget.isVisible():
            extra += self._error_widget.sizeHint().height() + self._layout.spacing()
        return needed + extra + margins.top() + margins.bottom() + border



    def _on_sign_out(self, source: str = "account_card") -> None:
        from qgis.PyQt.QtWidgets import QMessageBox

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(self.tr("Sign out"))
        box.setText(self.tr("Sign out of AI Agent?"))
        box.setInformativeText(self.tr("You can sign back in anytime from QGIS."))
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if exec_dialog(box) != QMessageBox.StandardButton.Yes:
            return
        del source
        self.sign_out_requested.emit()
        self.accept()

    def done(self, result):  # noqa: N802 - Qt signature
        self._cancel_avatar_load()
        super().done(result)

    def closeEvent(self, event):  # noqa: N802 - Qt signature
        self._cancel_avatar_load()
        super().closeEvent(event)
