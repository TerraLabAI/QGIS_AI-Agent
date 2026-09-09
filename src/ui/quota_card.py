# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
















from __future__ import annotations

from qgis.PyQt.QtCore import Qt, QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .font_scale import scale_px_length, scale_qss_font_px
from .shared import get_pro_price, get_pro_runs_per_month, get_support_email
from .style import (
    _BTN_GHOST,
    _BTN_PRIMARY_WIDE,
    _BTN_QUIET,
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
    SPACE_CARD,
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
_CARD_MARGINS = (16, 14, 16, 14)
_COPIED_MS = 2000


_COUNTER_QSS = f"font-size: {FONT_HINT}px; color: {INK_3}; background: transparent;"
_WARNING_QSS = f"font-size: {FONT_HINT}px; color: {INK}; font-weight: 500; background: transparent;"
_COUNTER_LINK_QSS = (
    "QPushButton { background: transparent; border: none; padding: 0 2px;"
    f" color: {ACCENT_INK}; font-size: {FONT_HINT}px; font-weight: 500; }}"
    "QPushButton:hover { text-decoration: underline; }"
)



WARN_AT_RUNS_LEFT = 3


class RunsLeftLine(QWidget):






    requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("runsLeftLine")
        self.where = "counter"
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE_TIGHT)
        row.addStretch(1)
        self._label = QLabel(self)
        self._label.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self._label, 0, Qt.AlignmentFlag.AlignVCenter)
        self._link = QPushButton(self)
        self._link.setStyleSheet(scale_qss_font_px(_COUNTER_LINK_QSS))
        self._link.setCursor(Qt.CursorShape.PointingHandCursor)
        self._link.setAutoDefault(False)
        self._link.setFlat(True)
        self._link.clicked.connect(lambda: self.requested.emit(self.where))
        row.addWidget(self._link, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)

    def set_balance(self, left: int, limit: int) -> None:
        self.where = "warning" if left <= WARN_AT_RUNS_LEFT else "counter"
        if self.where == "warning":
            self._label.setText(self.tr("1 free run left") if left == 1
                                else self.tr("{n} free runs left").format(n=left))
            self._label.setStyleSheet(scale_qss_font_px(_WARNING_QSS))
            self._link.setText(self.tr("Get more runs"))
        else:
            self._label.setText(self.tr("{left} of {limit} free runs left").format(left=left, limit=limit))
            self._label.setStyleSheet(scale_qss_font_px(_COUNTER_QSS))
            self._link.setText(self.tr("Get Pro"))
        self._link.setToolTip(self.tr("{n} runs a month with Pro").format(n=get_pro_runs_per_month()))

    def text(self) -> str:
        return f"{self._label.text()} {self._link.text()}"


class QuotaCard(QWidget):




    checkout_requested = pyqtSignal(str)

    invoice_requested = pyqtSignal()

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
        col.addWidget(self._title)
        col.addWidget(self._note)


        self._plan = QWidget(self._card)
        plan = QVBoxLayout(self._plan)
        plan.setContentsMargins(0, 0, 0, 0)
        plan.setSpacing(SPACE_CARD + 2)
        self._pitch = QLabel(self._plan)
        self._pitch.setStyleSheet(scale_qss_font_px(_BODY_QSS))
        plan.addWidget(self._pitch)
        self._button = QPushButton(self._plan)
        self._button.setStyleSheet(_BTN_PRIMARY_WIDE)
        self._button.setFixedHeight(scale_px_length(BTN_PRIMARY_WIDE_PX))
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.setAutoDefault(False)
        plan.addWidget(self._button)
        self._invoice = QPushButton(self.tr("Invoice for my company"), self._plan)
        self._invoice.setStyleSheet(_BTN_QUIET)
        self._invoice.setCursor(Qt.CursorShape.PointingHandCursor)
        self._invoice.setAutoDefault(False)
        self._invoice.clicked.connect(self.invoice_requested.emit)
        plan.addWidget(self._invoice, 0, Qt.AlignmentFlag.AlignHCenter)
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
        for label in (self._title, self._note, self._body, self._escape, self._pitch):
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
        outer.addWidget(self._card)
        self.hide()



    def show_exhausted(self, is_subscriber: bool, limit: int, reset_text: str = "") -> None:

        self._title.show()
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
            self.state = "free_out"


            self._title.setText(self.tr("Your free runs are used up"))
            for w in (self._note, self._body, self._ghost, self._escape):
                w.hide()
            runs = get_pro_runs_per_month()
            price = get_pro_price()
            self._pitch.setText(
                self.tr("Pro gives you {n} runs a month for {amount}.").format(n=runs, amount=price) if price
                else self.tr("Pro gives you {n} runs a month.").format(n=runs))
            self._button.setText(self.tr("Get Pro"))
            self._route_button(lambda: self.checkout_requested.emit("exhausted_card"))
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
