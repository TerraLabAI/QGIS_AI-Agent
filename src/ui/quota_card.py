# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later























from __future__ import annotations

import html

from qgis.PyQt.QtCore import QCoreApplication, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QIcon
from qgis.PyQt.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .font_scale import scale_px_length, scale_qss_font_px
from .icons import pixmap_for
from .shared import (
    get_pro_price,
    get_pro_price_excludes_tax,
    get_pro_runs_per_month,
    get_promo_active,
    get_support_email,
)
from .style import (
    _BTN_GHOST,
    _BTN_PRIMARY_WIDE,
    ACCENT,
    ACCENT_INK,
    BTN_PILL_PX,
    BTN_PRIMARY_WIDE_PX,
    FONT_BASE,
    FONT_BODY,
    FONT_HINT,
    HAIRLINE,
    INK,
    INK_3,
    MUTED,
    RADIUS_PANEL,
    SPACE_TIGHT,
)

_CARD_QSS = (
    f"QWidget#quotaCard {{ background: palette(base); border: 1px solid {HAIRLINE};"
    f" border-radius: {RADIUS_PANEL}px; }}"
    "QLabel { background: transparent; border: none; }"
)
_TITLE_QSS = f"font-size: {FONT_BASE}px; font-weight: 600; color: palette(text);"
_BODY_QSS = f"font-size: {FONT_BODY}px; color: palette(text);"
_MUTED_QSS = f"font-size: {FONT_HINT}px; color: {MUTED};"
_HEAD_QSS = f"font-size: {FONT_BODY}px; font-weight: 600; color: palette(text);"
_PRICE_QSS = f"font-size: {FONT_BODY}px; font-weight: 500; color: palette(text);"


_INVOICE_QSS = (
    "QPushButton { background: transparent; border: none; padding: 2px 4px;"
    f" color: {INK_3}; font-size: {FONT_HINT}px; font-weight: 400; }}"
    f"QPushButton:hover {{ color: {MUTED}; text-decoration: underline; }}"
)
_CHECK_PX = 12
_CHEVRON_PX = 16
_CARD_MARGINS = (16, 14, 16, 14)
_COPIED_MS = 2000

_WARNING_QSS = f"font-size: {FONT_HINT}px; color: {INK}; font-weight: 500; background: transparent;"




WARN_AT_RUNS_LEFT = 3


class RunsLeftLine(QWidget):






    requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("runsLeftLine")
        self.where = ""
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)



        self._label = QLabel(self)
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self._label.setWordWrap(True)
        self._label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self._label.linkActivated.connect(lambda _href: self.requested.emit(self.where))
        self._plain = ""
        row.addWidget(self._label, 1)

    def set_balance(self, left: int, limit: int, trial: tuple | None = None) -> None:


        self.where = "warning" if left <= WARN_AT_RUNS_LEFT else ""
        if self.where == "warning":
            count = (self.tr("1 free run left") if left == 1
                     else self.tr("{n} free runs left").format(n=left))

            link = (trial_cta(trial[0]) if trial
                    else self.tr("Get more runs with Pro"))
            self._label.setStyleSheet(scale_qss_font_px(_WARNING_QSS))
            self._label.setText(
                f'{html.escape(count)}&nbsp; <a href="pro" style="color: {ACCENT_INK};'
                f' text-decoration: none;">{html.escape(link)}</a>')
            self._plain = f"{count} {link}"
        else:

            self._label.setText("")
            self._plain = ""
        tip = ""
        if self.where == "warning":
            tip = (trial_promise(*trial) if trial else
                   self.tr("Pro: {n} runs a month, Medium and High effort for harder tasks, Autopilot")
                   .format(n=get_pro_runs_per_month()))
        self._label.setToolTip(tip)

    def text(self) -> str:
        return self._plain


class _Disclosure(QWidget):




    toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._open = False
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE_TIGHT)
        self._label = QLabel(self)
        self._label.setStyleSheet(scale_qss_font_px(_MUTED_QSS))
        self._label.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self._label, 0, Qt.AlignmentFlag.AlignVCenter)
        self._chevron = QLabel(self)
        self._chevron.setFixedSize(_CHEVRON_PX, _CHEVRON_PX)
        self._chevron.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self._chevron, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._paint()

    def set_text(self, text: str) -> None:
        self._label.setText(text)
        self.setAccessibleName(text)

    def is_open(self) -> bool:
        return self._open

    def set_open(self, on: bool) -> None:
        if bool(on) == self._open:
            return
        self._open = bool(on)
        self._paint()
        self.toggled.emit(self._open)

    def _paint(self) -> None:
        self._chevron.setPixmap(pixmap_for(self, "chevron_down" if self._open else "chevron_right",
                                           _CHEVRON_PX, QColor(INK_3)))

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.set_open(not self._open)
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.set_open(not self._open)
            return
        super().keyPressEvent(event)


def pro_price_per_month() -> str:




    if not get_promo_active():
        return ""
    price = get_pro_price()
    if not price:
        return ""
    if get_pro_price_excludes_tax():
        return QCoreApplication.translate("QuotaCard", "{amount}/month excl. VAT").format(amount=price)
    return QCoreApplication.translate("QuotaCard", "{amount}/month").format(amount=price)


def trial_cta(days: int) -> str:

    return QCoreApplication.translate("QuotaCard", "Try Pro free for {n} days").format(n=days)


def trial_reassurance() -> str:


    return QCoreApplication.translate("QuotaCard", "No commitment, cancel in one click")


def trial_promise(days: int, _ends_on: str | None = None) -> str:

    return f"{trial_cta(days)}. {trial_reassurance()}"


class QuotaCard(QWidget):



    plans_requested = pyqtSignal(str)

    invoice_requested = pyqtSignal(str)

    contact_requested = pyqtSignal()

    return_closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = ""





        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._card = QWidget(self)
        self._card.setObjectName("quotaCard")
        self._card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._card.setStyleSheet(_CARD_QSS)
        col = QVBoxLayout(self._card)
        col.setContentsMargins(*_CARD_MARGINS)
        col.setSpacing(SPACE_TIGHT)


        self._title = QLabel(self._card)
        self._title.setStyleSheet(scale_qss_font_px(_TITLE_QSS))
        self._note = QLabel(self._card)
        self._note.setStyleSheet(scale_qss_font_px(_MUTED_QSS))
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self._title, 1)

        self._close = QToolButton(self._card)
        self._close.setIcon(QIcon(pixmap_for(self._close, "close", _CHEVRON_PX, QColor(INK_3))))
        self._close.setAutoRaise(True)
        self._close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close.setAccessibleName(self.tr("Close"))
        self._close.setToolTip(self.tr("Close"))
        self._close.setStyleSheet("QToolButton { background: transparent; border: none; }")
        self._close.clicked.connect(self.return_closed.emit)
        self._close.hide()
        head.addWidget(self._close, 0, Qt.AlignmentFlag.AlignTop)
        col.addLayout(head)
        col.addWidget(self._note)



        self._plan = QWidget(self._card)
        plan = QVBoxLayout(self._plan)
        plan.setContentsMargins(0, SPACE_TIGHT, 0, 0)
        plan.setSpacing(SPACE_TIGHT)


        self._button = QPushButton(self._plan)
        self._button.setStyleSheet(_BTN_PRIMARY_WIDE)
        self._button.setFixedHeight(scale_px_length(BTN_PRIMARY_WIDE_PX))
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.setAutoDefault(False)
        plan.addWidget(self._button)

        self._promise = QLabel(self._plan)
        self._promise.setStyleSheet(scale_qss_font_px(_MUTED_QSS))
        self._promise.setWordWrap(True)
        self._promise.setTextFormat(Qt.TextFormat.PlainText)
        self._promise.hide()
        plan.addWidget(self._promise)
        self._invoice = QPushButton(self.tr("Invoice for my company"), self._plan)
        self._invoice.setStyleSheet(scale_qss_font_px(_INVOICE_QSS))
        self._invoice.setCursor(Qt.CursorShape.PointingHandCursor)
        self._invoice.setAutoDefault(False)
        self._invoice.clicked.connect(lambda: self.invoice_requested.emit(
            "checkout_return" if self.state == "checkout_return" else "exhausted_card"))
        plan.addWidget(self._invoice, 0, Qt.AlignmentFlag.AlignHCenter)
        self._contact = QPushButton(self.tr("Write to us"), self._plan)
        self._contact.setStyleSheet(scale_qss_font_px(_INVOICE_QSS))
        self._contact.setCursor(Qt.CursorShape.PointingHandCursor)
        self._contact.setAutoDefault(False)
        self._contact.clicked.connect(self.contact_requested.emit)
        self._contact.hide()
        plan.addWidget(self._contact, 0, Qt.AlignmentFlag.AlignHCenter)
        self._more = _Disclosure(self._plan)
        plan.addWidget(self._more)
        self._details = QWidget(self._plan)
        details = QVBoxLayout(self._details)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(SPACE_TIGHT)
        self._pitch = QLabel(self._details)
        self._pitch.setStyleSheet(scale_qss_font_px(_HEAD_QSS))
        details.addWidget(self._pitch)
        self._checks: list[QLabel] = []
        self._check_icons: list[QLabel] = []
        for _ in range(4):
            line = QHBoxLayout()
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(SPACE_TIGHT + 2)
            tick = QLabel(self._details)
            tick.setFixedSize(scale_px_length(_CHECK_PX), scale_px_length(_CHECK_PX))
            line.addWidget(tick, 0, Qt.AlignmentFlag.AlignTop)
            text = QLabel(self._details)
            text.setStyleSheet(scale_qss_font_px(_BODY_QSS))
            text.setWordWrap(True)
            text.setTextFormat(Qt.TextFormat.PlainText)
            line.addWidget(text, 1)
            details.addLayout(line)
            self._check_icons.append(tick)
            self._checks.append(text)
        self._price = QLabel(self._details)
        self._price.setStyleSheet(scale_qss_font_px(_PRICE_QSS))
        details.addSpacing(2)
        details.addWidget(self._price)
        self._use = QLabel(self._details)
        self._use.setStyleSheet(scale_qss_font_px(_MUTED_QSS))
        details.addWidget(self._use)
        self._details.hide()
        self._more.toggled.connect(self._details.setVisible)
        plan.addWidget(self._details)
        col.addWidget(self._plan)


        self._body = QLabel(self._card)
        self._body.setStyleSheet(scale_qss_font_px(_BODY_QSS))
        col.addWidget(self._body)
        self._ghost = QPushButton(self._card)
        self._ghost.setStyleSheet(_BTN_GHOST)
        self._ghost.setFixedHeight(scale_px_length(BTN_PILL_PX))
        self._ghost.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ghost.setAutoDefault(False)
        self._ghost.clicked.connect(self._copy_email)
        col.addWidget(self._ghost, 0, Qt.AlignmentFlag.AlignLeft)

        self._escape = QLabel(self._card)
        self._escape.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._escape.setStyleSheet(scale_qss_font_px(_MUTED_QSS))
        col.addWidget(self._escape)
        for label in (self._title, self._note, self._body, self._escape, self._pitch,
                      self._price, self._use):
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
        outer.addWidget(self._card)
        self.hide()



    def show_exhausted(self, is_subscriber: bool, limit: int, reset_text: str = "",
                       trial: tuple | None = None) -> None:





        self._title.show()
        self._close.hide()
        self._contact.hide()
        if is_subscriber:
            self.state = "paid_out"
            self._title.setText(self.tr("Your runs are used up"))
            if limit and reset_text:
                self._note.setText(self.tr("You used all {n} runs this month. They come back on {date}.")
                                   .format(n=limit, date=reset_text))
            elif reset_text:
                self._note.setText(self.tr("They come back on {date}.").format(date=reset_text))
            else:
                self._note.setText(self.tr("You used all {n} runs this month.").format(n=limit) if limit else "")
            self._note.show()
            self._plan.hide()
            self._body.setText(self.tr("Need more this month? Write to us and we set up a custom quota."))
            self._body.show()
            self._ghost.setText(self.tr("Copy email"))
            self._ghost.show()
            self._escape.setText(get_support_email())
            self._escape.show()
        else:
            if self.state != "free_out":
                self._more.set_open(False)
            self.state = "free_out"

            self._title.setText(self.tr("Your free runs are used up"))
            for w in (self._body, self._ghost, self._escape):
                w.hide()
            self._note.setText(self.tr("Free runs come back on {date}.").format(date=reset_text) if reset_text
                               else self.tr("Free runs come back at your monthly reset."))
            self._note.show()
            self._pitch.setText(self.tr("Pro: more and better"))
            lines = (
                self.tr("{n} runs a month").format(n=get_pro_runs_per_month()),
                self.tr("Medium and High effort for harder tasks"),
                self.tr("Autopilot"),
                self.tr("Memory and your instructions"),
            )
            for label, tick, line in zip(self._checks, self._check_icons, lines):
                label.setText(line)
                tick.setPixmap(pixmap_for(tick, "check", _CHECK_PX, QColor(ACCENT)))
            price = pro_price_per_month()
            self._price.setText(self.tr("{price} · cancel anytime").format(price=price) if price
                                else self.tr("Cancel anytime"))
            self._use.setText(self.tr("Free is for personal and study use. Pro covers work for clients and employers."))
            if trial:

                self._button.setText(trial_cta(trial[0]))
                self._promise.setText(trial_reassurance())
                self._invoice.setText(self.tr("My company pays by quote"))
            else:
                self._button.setText(self.tr("Get Pro"))
                self._invoice.setText(self.tr("Invoice for my company"))
            self._promise.setVisible(bool(trial))
            self._price.setVisible(not trial)
            self._more.set_text(self.tr("What Pro includes"))
            self._details.setVisible(self._more.is_open())
            self._more.show()
            self._route_button(lambda: self.plans_requested.emit("exhausted_card"))
            self._plan.show()
        self.show()

    def show_checkout_return(self) -> None:


        self.state = "checkout_return"
        self._title.setText(self.tr("Didn't finish upgrading?"))
        self._title.show()
        self._note.setText(self.tr("Your plan is still Free. If something got in the way:"))
        self._note.show()
        for w in (self._body, self._ghost, self._escape, self._promise, self._more, self._details):
            w.hide()
        self._button.setText(self.tr("Back to the Pro page"))
        self._route_button(lambda: self.plans_requested.emit("checkout_return"))
        self._invoice.setText(self.tr("My company pays by quote"))
        self._contact.show()
        self._close.show()
        self._plan.show()
        self.show()

    def clear(self) -> None:
        self.state = ""
        self.hide()



    def _route_button(self, slot) -> None:
        try:
            self._button.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass  # nosec B110
        if slot is not None:
            self._button.clicked.connect(slot)

    def _copy_email(self) -> None:
        try:
            QApplication.clipboard().setText(get_support_email())
        except (RuntimeError, AttributeError):
            return
        self._ghost.setText(self.tr("Copied"))
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._ghost.setText(self.tr("Copy email")))
        timer.start(_COPIED_MS)
