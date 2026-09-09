# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The account's run balance, at the moments it changes what the user can do."""










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
from .icons import pixmap_for
from .shared import (
    get_paywall_points,
    get_plan_name,
    get_pro_price,
    get_pro_runs_per_month,
    get_support_email,
)
from .style import (
    _BTN_GHOST,
    _BTN_PRIMARY,
    _BTN_PRIMARY_WIDE,
    BTN_PILL_PX,
    BTN_PRIMARY_WIDE_PX,
    FONT_BASE,
    FONT_BODY,
    FONT_HINT,
    HAIRLINE,
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
_COMPACT_QSS = f"font-size: {FONT_BODY}px; font-weight: 600; color: palette(text);"
_MUTED_QSS = f"font-size: {FONT_HINT}px; color: {MUTED};"
_PLAN_QSS = f"font-size: {FONT_BASE + 3}px; font-weight: 600; color: palette(text);"
_PRICE_QSS = f"font-size: {FONT_BASE + 9}px; font-weight: 600; color: palette(text);"
_PERIOD_QSS = f"font-size: {FONT_BODY}px; color: {MUTED};"
_CARD_MARGINS = (16, 14, 16, 14)
_CHECK_PX = 14
_COPIED_MS = 2000


class QuotaCard(QWidget):
    """Low-balance nudge or end-of-allowance wall; hidden otherwise."""

    upgrade_requested = pyqtSignal()

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


        self._row_host = QWidget(self._card)
        row = QHBoxLayout(self._row_host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE_CARD + 2)
        self._compact = QLabel(self._row_host)
        self._compact.setWordWrap(True)
        self._compact.setStyleSheet(scale_qss_font_px(_COMPACT_QSS))
        row.addWidget(self._compact, 1)
        self._compact_btn = QPushButton(self.tr("Upgrade to Pro"), self._row_host)
        self._compact_btn.setStyleSheet(_BTN_PRIMARY)
        self._compact_btn.setFixedHeight(scale_px_length(BTN_PILL_PX))
        self._compact_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._compact_btn.setAutoDefault(False)
        self._compact_btn.clicked.connect(self.upgrade_requested.emit)
        row.addWidget(self._compact_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addWidget(self._row_host)


        self._title = QLabel(self._card)
        self._title.setStyleSheet(scale_qss_font_px(_TITLE_QSS))
        self._note = QLabel(self._card)
        self._note.setStyleSheet(scale_qss_font_px(_MUTED_QSS))
        col.addWidget(self._title)
        col.addWidget(self._note)


        self._plan = QWidget(self._card)
        plan = QVBoxLayout(self._plan)
        plan.setContentsMargins(0, SPACE_CARD + 4, 0, 0)
        plan.setSpacing(SPACE_TIGHT)
        self._plan_name = QLabel(get_plan_name("pro", self.tr("Pro"), "short_name"), self._plan)
        self._plan_name.setStyleSheet(scale_qss_font_px(_PLAN_QSS))
        plan.addWidget(self._plan_name)
        price_row = QHBoxLayout()
        price_row.setContentsMargins(0, 0, 0, 0)
        price_row.setSpacing(SPACE_CARD)
        self._price = QLabel(self._plan)
        self._price.setStyleSheet(scale_qss_font_px(_PRICE_QSS))
        price_row.addWidget(self._price, 0, Qt.AlignmentFlag.AlignBottom)
        self._period = QLabel(self.tr("a month"), self._plan)
        self._period.setStyleSheet(scale_qss_font_px(_PERIOD_QSS))
        price_row.addWidget(self._period, 0, Qt.AlignmentFlag.AlignBottom)
        price_row.addStretch(1)
        plan.addLayout(price_row)
        self._pitch = QLabel(self.tr("Keep the agent working on your projects."), self._plan)
        self._pitch.setStyleSheet(scale_qss_font_px(_PERIOD_QSS))
        plan.addWidget(self._pitch)
        plan.addSpacing(SPACE_CARD)
        self._button = QPushButton(self._plan)
        self._button.setStyleSheet(_BTN_PRIMARY_WIDE)
        self._button.setFixedHeight(scale_px_length(BTN_PRIMARY_WIDE_PX))
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.setAutoDefault(False)
        plan.addWidget(self._button)
        plan.addSpacing(SPACE_CARD)
        self._points_host = QWidget(self._plan)
        self._points = QVBoxLayout(self._points_host)
        self._points.setContentsMargins(0, 0, 0, 0)
        self._points.setSpacing(SPACE_TIGHT + 2)
        plan.addWidget(self._points_host)
        plan.addSpacing(SPACE_TIGHT)
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
        for label in (self._title, self._note, self._body, self._compact, self._escape, self._pitch):
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
        outer.addWidget(self._card)
        self.hide()



    def show_low(self, left: int) -> None:
        """A free account near the end of its grant: one row, ignorable."""
        self.state = "low"
        self._compact.setText(
            self.tr("1 free run left.") if left == 1
            else self.tr("{n} free runs left.").format(n=left))
        self._row_host.show()
        for w in (self._title, self._note, self._plan, self._body, self._ghost, self._escape):
            w.hide()
        self.show()

    def show_exhausted(self, is_subscriber: bool, limit: int, reset_text: str = "") -> None:
        """The grant or the month is spent: the composer is off, this says why."""
        self._row_host.hide()
        self._title.show()
        self._note.show()
        self._escape.show()
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
            self._plan.hide()
            self._body.setText(self.tr("Need more this month? Write to us and we set up a custom quota."))
            self._body.show()
            self._ghost.setText(self.tr("Copy email"))
            self._ghost.show()
            self._escape.setText(get_support_email())
        else:
            self.state = "free_out"
            self._title.setText(self.tr("Your free runs are used up"))
            if reset_text:
                self._note.setText(self.tr("You used all {n} free runs this month. They come back on {date}.")
                                   .format(n=limit, date=reset_text))
            else:
                self._note.setText(
                    self.tr("You used all {n} free runs this month. They come back on your renewal date.")
                    .format(n=limit))
            self._body.hide()
            self._ghost.hide()
            self._price.setText(get_pro_price())
            self._button.setText(self.tr("Upgrade to Pro"))
            self._route_button(self.upgrade_requested.emit)







            self._set_points(self._plan_points())
            self._plan.show()
            self._escape.setText(self.tr("Custom needs? Write to us: {email}").format(email=get_support_email()))
        self.show()

    def clear(self) -> None:
        self.state = ""
        self.hide()



    def _plan_points(self) -> list:
        """The Pro selling points, served or shipped, with the run count filled in."""
        runs = get_pro_runs_per_month()
        out = []
        for line in get_paywall_points():
            text = self.tr(line)
            try:
                text = text.format(n=runs)
            except (IndexError, KeyError, ValueError):


                pass
            out.append(text)
        return out

    def _set_points(self, lines) -> None:
        while self._points.count():
            item = self._points.takeAt(0)
            widget = item.widget()
            if widget is not None:





                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        for text in lines:
            row = QWidget(self._points_host)
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(SPACE_CARD + 2)
            check_px = scale_px_length(_CHECK_PX)
            glyph = QLabel(row)
            glyph.setFixedSize(check_px + 2, check_px + 2)
            glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            glyph.setPixmap(pixmap_for(row, "check", check_px))
            lay.addWidget(glyph, 0, Qt.AlignmentFlag.AlignTop)
            label = QLabel(text, row)
            label.setStyleSheet(scale_qss_font_px(_BODY_QSS))
            label.setWordWrap(True)
            lay.addWidget(label, 1)
            self._points.addWidget(row)

    def _route_button(self, slot) -> None:
        try:
            self._button.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass  # nosec B110 - nothing was connected
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
