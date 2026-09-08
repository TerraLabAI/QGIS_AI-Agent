# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The row and the sheet stylesheet the frameless popovers share."""











from __future__ import annotations

from qgis.PyQt.QtCore import QRectF, Qt, pyqtSignal
from qgis.PyQt.QtGui import QBrush, QPainter
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .font_scale import scale_qss_font_px
from .permission_chip import glyph_tile
from .shared import event_pos
from .style import (
    FONT_BASE,
    FONT_BODY,
    FONT_HINT,
    FONT_MICRO,
    INK,
    INK_2,
    INK_3,
    LINE,
    LINE_STRONG,
    RADIUS_CARD,
    RADIUS_CONTROL,
    SURFACE,
    hover_pill,
)

_ROW_RADIUS = RADIUS_CONTROL
_ROW_PAD_X = 8
_ROW_PAD_Y = 5
_TILE = 26


_POPOVER_QSS = scale_qss_font_px(

    f"QFrame#examplesPopover {{ background: {SURFACE};"
    f" border: 1px solid {LINE_STRONG}; border-radius: {RADIUS_CARD}px; }}"
    f"QLabel#examplesSection {{ font-size: {FONT_HINT}px; color: {INK_3};"
    " padding: 8px 8px 2px 8px; background: transparent; }"
    f"QLabel#exampleTitle {{ font-size: {FONT_BODY}px; color: {INK}; background: transparent; }}"
    f"QLabel#exampleNote {{ font-size: {FONT_HINT}px; color: {INK_2}; background: transparent; }}"
    f"QLabel#exampleTag {{ font-size: {FONT_MICRO}px; color: {INK_3}; background: transparent; }}"
    f"QLineEdit#examplesSearch {{ background: transparent; border: none; padding: 0;"
    f" font-size: {FONT_BASE}px; color: {INK};"
    f" selection-background-color: {LINE_STRONG}; }}"
    f"QLabel#examplesCount {{ font-size: {FONT_MICRO}px; color: {INK_3}; background: transparent; }}"
    f"QFrame#examplesRule {{ background: {LINE}; border: none;"
    " max-height: 1px; min-height: 1px; }"
    f"QLabel#examplesEmpty {{ font-size: {FONT_BODY}px; color: {INK_3};"
    " padding: 20px 8px; background: transparent; }"
    "QScrollArea#examplesScroll { background: transparent; border: none; }"
    "QScrollArea#examplesScroll > QWidget > QWidget { background: transparent; }"





    f"QScrollBar:vertical {{ background: {LINE}; width: 10px; margin: 2px;"
    " border: none; border-radius: 5px; }"
    f"QScrollBar::handle:vertical {{ background: {INK_3};"
    " border-radius: 4px; min-height: 28px; margin: 1px; }"
    f"QScrollBar::handle:vertical:hover {{ background: {INK_2}; }}"
    "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
    "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
    "QScrollBar:horizontal { height: 0; }"
)


class _Row(QWidget):
    """One clickable line: a tinted tile, a title over a muted note, a tag."""

    clicked = pyqtSignal()

    def __init__(self, glyph: str, accent: str, title: str, note: str, tag: str = "", parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._hover = False
        self._focused = False





        self._width_hint = 0
        self._full_title = title
        self._full_note = note
        row = QHBoxLayout(self)
        row.setContentsMargins(_ROW_PAD_X, _ROW_PAD_Y, _ROW_PAD_X, _ROW_PAD_Y)
        row.setSpacing(8)
        row.addWidget(glyph_tile(self, glyph, accent, _TILE, 15), 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        self._title = QLabel(self)
        self._title.setObjectName("exampleTitle")
        self._title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        col.addWidget(self._title)
        self._note = QLabel(self)
        self._note.setObjectName("exampleNote")
        self._note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        if not note:
            self._note.hide()
        col.addWidget(self._note)
        row.addLayout(col, 1)
        self._tag = QLabel(tag, self)
        self._tag.setObjectName("exampleTag")
        self._tag.setVisible(bool(tag))
        row.addWidget(self._tag, 0, Qt.AlignmentFlag.AlignVCenter)
        self._elide()

    def set_tag(self, text: str) -> None:
        self._tag.setText(text)
        self._tag.setVisible(bool(text))

    def set_focused(self, focused: bool) -> None:
        self._focused = bool(focused)
        self.update()

    def text_width(self) -> int:
        """What one line of this row has to itself, hint included."""
        base = self._width_hint or self.width()
        return max(40, base - 2 * _ROW_PAD_X - _TILE - 8 - self._tag.sizeHint().width() - 8)

    def set_width_hint(self, width: int) -> None:
        self._width_hint = max(0, int(width or 0))

    def _elide(self) -> None:
        width = self._title.width()
        if width < 30 or self._width_hint:
            width = self.text_width()
        width = max(40, width)
        self._title.setText(self._title.fontMetrics().elidedText(
            self._full_title, Qt.TextElideMode.ElideRight, width))
        self._note.setText(self._note.fontMetrics().elidedText(
            self._full_note, Qt.TextElideMode.ElideRight, width))

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._elide()

    def enterEvent(self, event):  # noqa: N802 - Qt override
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event_pos(event)):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):  # noqa: N802 - Qt override
        if self._hover or self._focused:
            painter = QPainter(self)
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(hover_pill()))
                painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                                        _ROW_RADIUS, _ROW_RADIUS)
            finally:
                painter.end()
        super().paintEvent(event)
