# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""One card in the library grid."""










from __future__ import annotations

from qgis.PyQt.QtCore import QRectF, Qt, pyqtSignal
from qgis.PyQt.QtGui import QBrush, QColor, QPainter, QPen
from qgis.PyQt.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout

from ..permission_chip import glyph_tile
from ..shared import event_pos
from ..style import RADIUS_CARD
from .common import CARD_QSS, accent_of

_TILE = 30
_TILE_GLYPH = 16
_PAD = 12


def elide_two_lines(metrics, text: str, width: int) -> tuple:
    """``text`` laid out over at most two lines of ``width``, the second elided."""





    words = str(text or "").split()
    if not words or width < 20:
        return (str(text or ""), "")
    first: list = []
    index = 0
    while index < len(words):
        trial = " ".join([*first, words[index]])
        if first and metrics.horizontalAdvance(trial) > width:
            break
        first.append(words[index])
        index += 1
    rest = " ".join(words[index:])
    if not rest:
        return (" ".join(first), "")
    return (" ".join(first), metrics.elidedText(rest, Qt.TextElideMode.ElideRight, width))


class ExampleCard(QFrame):
    """A clickable tile for one use case."""

    clicked = pyqtSignal()

    def __init__(self, case, parent=None):
        super().__init__(parent)
        self.setObjectName("exampleCard")
        self.setStyleSheet(CARD_QSS)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.case = case
        self._focused = False
        self._outcome = str(case.outcome or "")

        row = QHBoxLayout(self)
        row.setContentsMargins(_PAD, _PAD, _PAD, _PAD)
        row.setSpacing(10)
        row.addWidget(glyph_tile(self, case.glyph, accent_of(case.group), _TILE, _TILE_GLYPH),
                      0, Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        self._title = QLabel(self)
        self._title.setObjectName("cardTitle")
        self._title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._full_title = str(case.title or "")
        col.addWidget(self._title)

        self._note = QLabel(self)
        self._note.setObjectName("cardNote")
        self._note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._note.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        col.addWidget(self._note, 1)

        footer = QLabel(self)
        footer.setObjectName("cardTag")
        footer.setText(str((case.uses or ("",))[0] or "").strip())
        col.addWidget(footer)
        row.addLayout(col, 1)
        self._relayout()

    def set_focused(self, focused: bool) -> None:
        self._focused = bool(focused)
        self.update()

    def _relayout(self) -> None:
        width = self.width() - 2 * _PAD - _TILE - 10
        if width < 40:
            width = 220
        self._title.setText(self._title.fontMetrics().elidedText(
            self._full_title, Qt.TextElideMode.ElideRight, width))
        first, second = elide_two_lines(self._note.fontMetrics(), self._outcome, width)
        self._note.setText(first + ("\n" + second if second else ""))

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._relayout()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event_pos(event)):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):  # noqa: N802 - Qt override
        super().paintEvent(event)
        if not self._focused:
            return



        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(QPen(QColor(139, 172, 39, 200), 2))
            painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1),
                                    RADIUS_CARD, RADIUS_CARD)
        finally:
            painter.end()
