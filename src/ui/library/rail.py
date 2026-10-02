# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



























from __future__ import annotations

import math

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFontMetricsF
from qgis.PyQt.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from ..icons import pixmap_for
from ..shared import tr
from ..widgets import ElidedLabel
from . import common as C
from .parts import category_badge
from .pressable import Pressable, labels_through

_ROW_H = 36
_ROW_PAD = 10
_GLYPH_PX = 18
_BADGE_PX = 20
_CHEVRON_PX = 24
_RAIL_PAD = 8
_RAIL_MAX_W = 288


class RailRow(Pressable):


    selected = pyqtSignal(str)

    def __init__(self, key: str, text: str, glyph: str = "", parent=None, indent: int = 0,
                 badge: tuple | None = None):
        super().__init__(parent, radius=C.RADIUS_ROW,
                         rest=lambda: C.T.selected if self._current else None,
                         hover=lambda: C.T.selected if self._current else C.T.hover,
                         press=lambda: C.T.press)
        self.key = key
        self._current = False
        self.setFixedHeight(C.px(_ROW_H))
        row = QHBoxLayout(self)
        self._indent = indent
        row.setContentsMargins(C.px(_ROW_PAD) + indent, 0, C.px(_ROW_PAD), 0)
        row.setSpacing(C.px(_ROW_PAD))
        self._glyph = bool(glyph) or badge is not None
        if badge is not None:
            row.addWidget(category_badge(self, badge[0], badge[1], C.px(_BADGE_PX)),
                          0, Qt.AlignmentFlag.AlignVCenter)
        elif glyph:
            icon = QLabel(self)
            icon.setPixmap(pixmap_for(icon, glyph, _GLYPH_PX, QColor(C.T.text)))
            icon.setStyleSheet("background: transparent;")
            icon.setFixedWidth(_GLYPH_PX)
            row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.text = ElidedLabel(text, self)
        self.text.setStyleSheet(C.text_qss(C.BODY_PX, C.T.text))
        row.addWidget(self.text, 1, Qt.AlignmentFlag.AlignVCenter)
        self._row_layout = row
        labels_through(self)
        self.clicked.connect(lambda: self.selected.emit(self.key))
        self.setAccessibleName(text)

    def natural_width(self) -> int:

        self.text.ensurePolished()


        width = math.ceil(QFontMetricsF(self.text.font()).horizontalAdvance(self.text.full_text()))
        if self._glyph:
            width += C.px(_BADGE_PX) + C.px(_ROW_PAD)
        if getattr(self, "chevron", None) is not None:
            width += C.px(_CHEVRON_PX) + C.px(_ROW_PAD)
        return width + 2 * C.px(_ROW_PAD) + self._indent

    def set_current(self, current: bool) -> None:
        if self._current != bool(current):
            self._current = bool(current)
            self.update()


    def add_chevron(self) -> _Chevron:

        self.chevron = _Chevron(self)
        self._row_layout.addWidget(self.chevron, 0, Qt.AlignmentFlag.AlignVCenter)
        return self.chevron


class _Chevron(Pressable):


    def __init__(self, parent=None):
        super().__init__(parent, radius=C.px(6), hover=lambda: C.T.press, press=lambda: C.T.press)
        self.setFixedSize(C.px(_CHEVRON_PX), C.px(_CHEVRON_PX))
        self._icon = QLabel(self)
        self._icon.setStyleSheet("background: transparent;")
        self._icon.setGeometry(0, 0, C.px(_CHEVRON_PX), C.px(_CHEVRON_PX))
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        labels_through(self)
        self.set_open(True)

    def set_open(self, open_: bool) -> None:
        name = "chevron_down" if open_ else "chevron_right"
        self._icon.setPixmap(pixmap_for(self._icon, name, 14, QColor(C.T.text_2)))
        text = tr("Hide categories") if open_ else tr("Show categories")
        self.setToolTip(text)
        self.setAccessibleName(text)


class LibraryRail(QFrame):


    selected = pyqtSignal(str)

    def __init__(self, places: list, groups: list, parent=None, looks: dict | None = None):
        super().__init__(parent)
        self.setObjectName("librarySidebar")
        self.setStyleSheet(f"QFrame#librarySidebar {{ background: {C.T.rail}; border: none; }}")
        self._rows: dict = {}
        col = QVBoxLayout(self)
        col.setContentsMargins(C.px(_RAIL_PAD), C.px(12), C.px(_RAIL_PAD), C.px(12))
        col.setSpacing(C.px(2))
        self._group_keys: list = []
        self._other_places: list = [key for key, _glyph, _text in places[1:]]
        looks = looks or {}
        self._open = True
        self._in_examples = True
        for index, (key, glyph, text) in enumerate(places):
            col.addWidget(self._row(key, text, glyph))
            if index == 0:
                self._rows[key].add_chevron().clicked.connect(self._toggle)
                for group_key, group_text in groups:
                    col.addWidget(self._row(group_key, group_text, indent=C.px(_ROW_PAD),
                                            badge=looks.get(group_key, ("", ""))))
                    self._group_keys.append(group_key)
        col.addStretch(1)
        widest = max((row.natural_width() for row in self._rows.values()), default=0)
        self.setFixedWidth(max(C.px(C.RAIL_W), min(C.px(_RAIL_MAX_W), widest + 2 * C.px(_RAIL_PAD))))

    def _row(self, key: str, text: str, glyph: str = "", indent: int = 0,
             badge: tuple | None = None) -> RailRow:
        row = RailRow(key, text, glyph, self, indent, badge)
        row.selected.connect(self.selected.emit)
        self._rows[key] = row
        return row

    def set_current(self, key: str) -> None:
        for row_key, row in self._rows.items():
            row.set_current(row_key == key)



        in_examples = key not in self._other_places
        if in_examples != self._in_examples:
            self._in_examples = in_examples
            self._fold(in_examples)

    def _toggle(self) -> None:
        self._fold(not self._open)

    def _fold(self, open_: bool) -> None:
        self._open = bool(open_)
        for group_key in self._group_keys:
            self._rows[group_key].setVisible(self._open)
        first = next(iter(self._rows.values()), None)
        if first is not None and getattr(first, "chevron", None) is not None:
            first.chevron.set_open(self._open)

    def groups_open(self) -> bool:
        return self._open

    def keys(self) -> list:
        return list(self._rows)
