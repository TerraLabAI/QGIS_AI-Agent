# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The empty thread."""











from __future__ import annotations

from qgis.PyQt.QtCore import QSize, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .font_scale import scale_qss_font_px
from .icons import icon_for
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
)





_DISCLOSURE_QSS = scale_qss_font_px(
    f"font-size: {FONT_HINT}px; color: {MUTED}; background: transparent;"
    " border: none; padding: 0 8px;")





_QUESTION_QSS = scale_qss_font_px(
    f"font-size: {FONT_BASE + 4}px; font-weight: 500; color: {INK};"
    " background: transparent; border: none;")


_ROW_QSS = scale_qss_font_px(
    f"QPushButton {{ background: transparent; color: {MUTED}; border: none;"
    f" border-radius: 14px; padding: 6px 12px; font-size: {FONT_BODY}px; text-align: left; }}"
    f"QPushButton:hover {{ background: {ACCENT_TINT}; color: {INK}; }}"
    f"QPushButton:pressed {{ background: {ACCENT_TINT_ON}; }}"
)
_ROW_GLYPH = 18


def _row(parent: QWidget, glyph: str, text: str) -> QPushButton:
    button = QPushButton(text, parent)
    button.setStyleSheet(_ROW_QSS)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setAutoDefault(False)
    button.setIcon(icon_for(button, glyph, _ROW_GLYPH))
    button.setIconSize(QSize(_ROW_GLYPH, _ROW_GLYPH))
    return button


class EmptyState(QWidget):
    """The question and the quiet rows the panel hangs under the box."""


    suggestion_clicked = pyqtSignal(str)
    examples_requested = pyqtSignal()
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

        question = QLabel(self.tr("What are we working on?"), block)
        question.setStyleSheet(_QUESTION_QSS)
        question.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        question.setWordWrap(True)
        col.addWidget(question)







        rows = QWidget(self)
        rows.setObjectName("emptyRows")
        rows_outer = QVBoxLayout(rows)
        rows_outer.setContentsMargins(0, 0, 0, 0)
        rows_outer.setSpacing(SPACE_TIGHT)
        pills = QWidget(rows)
        rows_col = QHBoxLayout(pills)
        rows_col.setContentsMargins(0, 0, 0, 0)
        rows_col.setSpacing(SPACE_TIGHT)
        rows_col.addStretch(1)




        self._examples_row = _row(pills, "book", self.tr("Examples"))
        self._examples_row.clicked.connect(self.examples_requested.emit)
        rows_col.addWidget(self._examples_row)
        self._tutorial_row = _row(pills, "play", self.tr("Tutorial"))
        self._tutorial_row.clicked.connect(self.tutorial_requested.emit)
        rows_col.addWidget(self._tutorial_row)
        rows_col.addStretch(1)
        rows_outer.addWidget(pills)














        disclosure = QLabel(
            self.tr("You are talking to an AI system. It can be wrong: check its changes."), rows)
        disclosure.setObjectName("aiDisclosure")
        disclosure.setStyleSheet(_DISCLOSURE_QSS)
        disclosure.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        disclosure.setWordWrap(True)
        rows_outer.addWidget(disclosure)
        self._disclosure = disclosure
        self._rows = rows
        self._mention_row = None

        outer.addWidget(block)
        outer.addStretch(4)


        self._outer = outer

    def rows_widget(self) -> QWidget:
        """The pill and the disclosure, for the panel to hang under the box."""





        return self._rows

    def set_centred(self, centred: bool) -> None:
        """Sit the question just above the composer, or float in the middle."""








        top, bottom = (1, 0) if centred else (3, 4)
        try:
            self._outer.setStretch(0, top)
            self._outer.setStretch(2, bottom)
        except (AttributeError, RuntimeError):
            pass
