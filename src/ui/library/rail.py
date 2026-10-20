# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
























from __future__ import annotations

import math

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFontMetricsF
from qgis.PyQt.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from ..icons import has_glyph, pixmap_for
from ..widgets import ElidedLabel
from . import common as C
from .pressable import Pressable, labels_through

_ROW_H = 36
_ROW_PAD = 10
_GLYPH_PX = 18
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
        if badge is not None:

            glyph = badge[0] if has_glyph(badge[0]) else "layers"
        self._glyph_name = glyph
        self._glyph = bool(glyph)
        self._icon = None
        if glyph:
            self._icon = QLabel(self)
            self._icon.setStyleSheet("background: transparent;")
            self._icon.setFixedWidth(_GLYPH_PX)
            row.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
            self._paint_glyph()
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
            width += C.px(_GLYPH_PX) + C.px(_ROW_PAD)
        return width + 2 * C.px(_ROW_PAD) + self._indent

    def set_current(self, current: bool) -> None:
        if self._current != bool(current):
            self._current = bool(current)
            self._paint_glyph()
            self.update()

    def _paint_glyph(self) -> None:

        if self._icon is not None:
            self._icon.setPixmap(pixmap_for(self._icon, self._glyph_name, _GLYPH_PX,
                                            QColor(C.T.text if self._current else C.T.text_2)))


class RedirectRow(RailRow):




    def __init__(self, key: str, text: str, glyph: str, parent=None):
        super().__init__(key, text, glyph, parent)
        self.text.setStyleSheet(C.text_qss(C.BODY_PX, C.T.text_2))
        self._out = QLabel(self)
        self._out.setStyleSheet("background: transparent;")
        self._row_layout.addWidget(self._out, 0, Qt.AlignmentFlag.AlignVCenter)
        labels_through(self)
        self._paint_out()

    def _paint_out(self) -> None:
        ink = C.T.text if self._hovered else C.T.text_2
        self._out.setPixmap(pixmap_for(self._out, "external", 12, QColor(ink)))
        if self._icon is not None:
            self._icon.setPixmap(pixmap_for(self._icon, self._glyph_name, _GLYPH_PX, QColor(ink)))
        self.text.setStyleSheet(C.text_qss(C.BODY_PX, ink))

    def _state_changed(self) -> None:
        self._paint_out()
        self.update()

    def natural_width(self) -> int:
        return super().natural_width() + 12 + C.px(_ROW_PAD)


class LibraryRail(QFrame):


    selected = pyqtSignal(str)

    def __init__(self, place: tuple, groups: list, parent=None, looks: dict | None = None):
        super().__init__(parent)
        self.setObjectName("librarySidebar")
        self.setStyleSheet(f"QFrame#librarySidebar {{ background: {C.T.rail}; border: none; }}")
        self._rows: dict = {}
        col = QVBoxLayout(self)
        col.setContentsMargins(C.px(_RAIL_PAD), C.px(12), C.px(_RAIL_PAD), C.px(12))
        col.setSpacing(C.px(2))
        self._col = col
        key, _glyph, text = place
        self._places: list = [key]
        self._names = {key: text}

        self._groups: dict = {key: []}
        self._owner: dict = {}
        self._active = key


        col.addWidget(self._row(key, self.tr("Home"), "home"))
        col.addSpacing(C.px(8))
        self._first_group = col.count()
        col.addStretch(1)
        self.set_groups(key, groups, looks)

    def add_redirect(self, key: str, glyph: str, text: str) -> None:

        rule = QFrame(self)
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {C.T.border}; border: none;")
        self._col.addSpacing(C.px(8))
        self._col.addWidget(rule)
        self._col.addSpacing(C.px(8))
        row = RedirectRow(key, text, glyph, self)
        row.selected.connect(self.selected.emit)
        self._col.addWidget(row)
        self._redirect = row
        widest = max(self.width(), row.natural_width() + 2 * C.px(_RAIL_PAD))
        self.setFixedWidth(max(C.px(C.RAIL_W), min(C.px(_RAIL_MAX_W), widest)))

    def _row(self, key: str, text: str, glyph: str = "", badge: tuple | None = None) -> RailRow:
        row = RailRow(key, text, glyph, self, 0, badge)
        row.selected.connect(self.selected.emit)
        self._rows[key] = row
        return row

    def set_groups(self, place: str, groups: list, looks: dict | None = None) -> None:



        if place not in self._groups:
            return
        looks = looks or {}
        for key in self._groups[place]:
            row = self._rows.pop(key, None)
            self._owner.pop(key, None)
            if row is not None:
                row.hide()
                row.setParent(None)
                row.deleteLater()
        self._groups[place] = []

        at = self._first_group + sum(len(self._groups[p]) for p in self._places[
            :self._places.index(place)])
        for offset, (key, text) in enumerate(groups):
            row = self._row(key, text, badge=looks.get(key, ("", "")))
            self._col.insertWidget(at + offset, row)
            self._groups[place].append(key)
            self._owner[key] = place
        self._show_active()
        self._fit_width()

    def _fit_width(self) -> None:
        widest = max([row.natural_width() for row in self._rows.values()] or [0])
        self.setFixedWidth(max(C.px(C.RAIL_W), min(C.px(_RAIL_MAX_W), widest + 2 * C.px(_RAIL_PAD))))

    def set_current(self, key: str) -> None:


        for row_key, row in self._rows.items():
            row.set_current(row_key == key)
        place = self._owner.get(key, key if key in self._groups else "")
        if place and place != self._active:
            self._active = place
            self._show_active()

    def place(self) -> str:

        return self._active

    def _show_active(self) -> None:
        for place, keys in self._groups.items():
            for key in keys:
                self._rows[key].setVisible(place == self._active)

    def keys(self) -> list:
        return list(self._rows)
