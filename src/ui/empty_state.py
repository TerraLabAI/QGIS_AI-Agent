# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later












from __future__ import annotations

from qgis.PyQt.QtCore import QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .font_scale import scale_px_length, scale_qss_font_px
from .icons import icon_for, logo_size
from .shared import tr
from .style import (
    ACCENT_TINT,
    ACCENT_TINT_ON,
    FONT_BASE,
    FONT_BODY,
    FONT_HINT,
    INK,
    MUTED,
    SPACE_OUTER,
    SPACE_TIGHT,
    WEIGHT_MEDIUM,
)
from .widgets import FlowLayout



_DISCLOSURE_QSS = scale_qss_font_px(
    f"font-size: {FONT_HINT}px; color: {MUTED}; background: transparent;"
    " border: none; padding: 0 2px;")





_QUESTION_QSS = scale_qss_font_px(
    f"font-size: {FONT_BASE + 8}px; font-weight: {WEIGHT_MEDIUM}; color: {INK};"
    " background: transparent; border: none;")


_ROW_QSS = scale_qss_font_px(
    f"QPushButton {{ background: transparent; color: {MUTED}; border: none;"
    f" border-radius: 14px; padding: 6px 12px; font-size: {FONT_BODY}px; text-align: left; }}"
    f"QPushButton:hover {{ background: {ACCENT_TINT}; color: {INK}; }}"
    f"QPushButton:pressed {{ background: {ACCENT_TINT_ON}; }}"
)
_ROW_GLYPH = 18

_MARK_PX = 40
_MARK_GAP = 14


_FOOT_QSS = scale_qss_font_px(
    f"QPushButton {{ background: transparent; color: {MUTED}; border: none;"
    f" border-radius: 6px; padding: 4px 8px; font-size: {FONT_HINT}px; text-align: left; }}"
    f"QPushButton:hover {{ background: {ACCENT_TINT}; color: {INK}; }}"
    f"QPushButton:pressed {{ background: {ACCENT_TINT_ON}; }}"
)
_FOOT_GLYPH = 14


def _row(parent: QWidget, glyph: str, text: str) -> QPushButton:
    button = QPushButton(text, parent)
    button.setStyleSheet(_ROW_QSS)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setAutoDefault(False)
    button.setIcon(icon_for(button, glyph, _ROW_GLYPH, QColor(MUTED)))
    button.setIconSize(QSize(_ROW_GLYPH, _ROW_GLYPH))
    return button


def feedback_foot(parent: QWidget) -> QWidget:










    foot = QWidget(parent)
    line = QHBoxLayout(foot)
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(SPACE_TIGHT)
    button = QPushButton(tr("Send feedback"), foot)
    button.setObjectName("feedbackFoot")
    button.setStyleSheet(_FOOT_QSS)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setAutoDefault(False)
    button.setIcon(icon_for(button, "lu.bug", _FOOT_GLYPH, QColor(MUTED)))
    button.setIconSize(QSize(_FOOT_GLYPH, _FOOT_GLYPH))
    button.setToolTip(tr("Report a bug, suggest an idea, or share what's working well."))
    line.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    disclosure = QLabel(tr("AI can make mistakes"), foot)
    disclosure.setObjectName("aiDisclosure")
    disclosure.setStyleSheet(_DISCLOSURE_QSS)
    disclosure.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    disclosure.setWordWrap(True)
    line.addWidget(disclosure, 1)
    return foot


class EmptyState(QWidget):


    examples_requested = pyqtSignal()
    sources_requested = pyqtSignal()
    tutorial_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, SPACE_OUTER, 16, SPACE_OUTER)
        outer.setSpacing(SPACE_OUTER)
        outer.addStretch(3)

        block = QWidget(self)
        col = QVBoxLayout(block)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(SPACE_OUTER + 4)




        hero = QHBoxLayout()
        hero.setContentsMargins(0, 0, 0, 0)
        hero.setSpacing(scale_px_length(_MARK_GAP))



        self._mark_seat = QWidget(block)
        self._mark_seat.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        seat = QVBoxLayout(self._mark_seat)
        seat.setContentsMargins(0, 0, 0, 0)
        seat.setSpacing(0)
        self._mark_slot = QWidget(self._mark_seat)
        self._mark_slot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        seat.addWidget(self._mark_slot)
        self.set_mark_px(scale_px_length(_MARK_PX))
        hero.addStretch(1)
        hero.addWidget(self._mark_seat, 0, Qt.AlignmentFlag.AlignBottom)
        self._question = question = QLabel(self.tr("How can I help?"), block)
        question.setStyleSheet(_QUESTION_QSS)
        question.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hero.addWidget(question, 0, Qt.AlignmentFlag.AlignBottom)
        hero.addStretch(1)
        self._hero = hero
        col.addLayout(hero)






        rows = QWidget(self)
        rows.setObjectName("emptyRows")
        rows_outer = QVBoxLayout(rows)
        rows_outer.setContentsMargins(0, 0, 0, 0)
        rows_outer.setSpacing(SPACE_TIGHT)
        pills = QWidget(rows)
        rows_col = FlowLayout(pills, h_spacing=SPACE_TIGHT, v_spacing=SPACE_TIGHT,
                              centred=True)





        self._examples_row = _row(pills, "book", self.tr("Examples"))
        self._examples_row.clicked.connect(self.examples_requested.emit)
        rows_col.addWidget(self._examples_row)

        self._tutorial_row = _row(pills, "play", self.tr("Tutorial"))
        self._tutorial_row.clicked.connect(self.tutorial_requested.emit)
        rows_col.addWidget(self._tutorial_row)
        self._sources_row = _row(pills, "lu.database", self.tr("Data"))
        self._sources_row.clicked.connect(self.sources_requested.emit)
        rows_col.addWidget(self._sources_row)
        rows_outer.addWidget(pills)





        self._rows = rows

        outer.addWidget(block)
        outer.addStretch(4)


        self._outer = outer

    def mark_slot(self) -> QWidget:

        return self._mark_slot

    def set_mark_shown(self, shown: bool) -> None:



        if self._mark_seat.isHidden() != shown:
            return
        self._mark_seat.setVisible(shown)
        self.layout().activate()

    def question(self) -> QLabel:
        return self._question

    def set_mark_px(self, px: int) -> None:
        self._mark_slot.setFixedSize(logo_size(int(px)).width(), int(px))
        if hasattr(self, "_question"):
            self._fit_question()

    def _fit_question(self) -> None:

        question = self._question
        question.ensurePolished()
        room = self.width() - 32 - self._mark_slot.width() - scale_px_length(_MARK_GAP)
        metrics = question.fontMetrics()
        wide = metrics.horizontalAdvance(question.text()) + 2
        wrapped = wide > room
        question.setWordWrap(wrapped)
        question.setFixedWidth(max(40, min(wide, room)))





        under = round((self._mark_slot.height() * 0.85 - metrics.capHeight()) / 2) - metrics.descent()
        question.setContentsMargins(0, 0, 0, 0 if wrapped else max(0, under))
        align = Qt.AlignmentFlag.AlignVCenter if wrapped else Qt.AlignmentFlag.AlignBottom
        self._hero.setAlignment(self._mark_seat, align)
        self._hero.setAlignment(question, align)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._fit_question()

    def hover_rows(self) -> tuple:
        return self._examples_row, self._tutorial_row, self._sources_row

    def rows_widget(self) -> QWidget:






        return self._rows

    def set_centred(self, centred: bool) -> None:









        top, bottom = (1, 0) if centred else (3, 4)
        try:
            self._outer.setStretch(0, top)
            self._outer.setStretch(2, bottom)
        except (AttributeError, RuntimeError):
            pass
