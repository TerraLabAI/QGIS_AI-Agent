# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



















from __future__ import annotations

import math

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFontMetricsF
from qgis.PyQt.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from ..icons import pixmap_for
from ..widgets import ElidedLabel
from . import common as C
from .parts import label
from .pressable import Pressable, labels_through

_ROW_H = 36
_ROW_PAD = 10
_GLYPH_PX = 18
_RAIL_PAD = 8
_RAIL_MAX_W = 288


class RailRow(Pressable):


    selected = pyqtSignal(str)

    def __init__(self, key: str, text: str, glyph: str = "", parent=None):
        super().__init__(parent, radius=C.RADIUS_ROW,
                         rest=lambda: C.T.selected if self._current else None,
                         hover=lambda: C.T.selected if self._current else C.T.hover,
                         press=lambda: C.T.press)
        self.key = key
        self._current = False
        self.setFixedHeight(C.px(_ROW_H))
        row = QHBoxLayout(self)
        row.setContentsMargins(C.px(_ROW_PAD), 0, C.px(_ROW_PAD), 0)
        row.setSpacing(C.px(_ROW_PAD))
        self._glyph = bool(glyph)
        if glyph:
            icon = QLabel(self)
            icon.setPixmap(pixmap_for(icon, glyph, _GLYPH_PX, QColor(C.T.text)))
            icon.setStyleSheet("background: transparent;")
            icon.setFixedWidth(_GLYPH_PX)
            row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.text = ElidedLabel(text, self)
        self.text.setStyleSheet(C.text_qss(C.BODY_PX, C.T.text))
        row.addWidget(self.text, 1, Qt.AlignmentFlag.AlignVCenter)
        labels_through(self)
        self.clicked.connect(lambda: self.selected.emit(self.key))
        self.setAccessibleName(text)

    def natural_width(self) -> int:

        self.text.ensurePolished()


        width = math.ceil(QFontMetricsF(self.text.font()).horizontalAdvance(self.text.full_text()))
        if self._glyph:
            width += _GLYPH_PX + C.px(_ROW_PAD)
        return width + 2 * C.px(_ROW_PAD)

    def set_current(self, current: bool) -> None:
        if self._current != bool(current):
            self._current = bool(current)
            self.update()


class LibraryRail(QFrame):


    selected = pyqtSignal(str)

    def __init__(self, places: list, heading: str, groups: list, parent=None):
        super().__init__(parent)
        self.setObjectName("librarySidebar")
        self.setStyleSheet(f"QFrame#librarySidebar {{ background: {C.T.rail}; border: none; }}")
        self._rows: dict = {}
        col = QVBoxLayout(self)
        col.setContentsMargins(C.px(_RAIL_PAD), C.px(12), C.px(_RAIL_PAD), C.px(12))
        col.setSpacing(C.px(2))
        for key, glyph, text in places:
            col.addWidget(self._row(key, text, glyph))
        if groups:
            col.addSpacing(C.px(20))
            head = label(self, heading, C.SMALL_PX, C.T.text_2)
            head.setContentsMargins(C.px(10), 0, 0, C.px(6))
            col.addWidget(head)
            for key, text in groups:
                col.addWidget(self._row(key, text))
        col.addStretch(1)
        widest = max((row.natural_width() for row in self._rows.values()), default=0)
        self.setFixedWidth(max(C.px(C.RAIL_W), min(C.px(_RAIL_MAX_W), widest + 2 * C.px(_RAIL_PAD))))

    def _row(self, key: str, text: str, glyph: str = "") -> RailRow:
        row = RailRow(key, text, glyph, self)
        row.selected.connect(self.selected.emit)
        self._rows[key] = row
        return row

    def set_current(self, key: str) -> None:
        for row_key, row in self._rows.items():
            row.set_current(row_key == key)

    def keys(self) -> list:
        return list(self._rows)
