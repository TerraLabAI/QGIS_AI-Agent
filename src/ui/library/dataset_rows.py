# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout

from . import common as C
from .parts import label
from .pressable import Pressable, labels_through

_HIT_MARK_PX = 36
_HIT_H = 64


class DatasetHit(Pressable):


    def __init__(self, row: dict, family: dict, accent: str, parent=None):
        super().__init__(parent, radius=C.RADIUS_TILE)
        self.family = str(row.get("family") or "")
        self.key = self.family
        self._title = str(row.get("title") or row.get("id") or "")
        self._note = str(family.get("name") or row.get("provider") or "")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(C.px(_HIT_H))
        line = QHBoxLayout(self)
        line.setContentsMargins(C.px(C.EDGE), 0, C.px(C.EDGE), 0)
        line.setSpacing(C.px(12))
        mark = QLabel(self)
        from .pictures import wear_square_mark


        look = dict(family)
        if row.get("logo_url"):
            look["logo_url"] = row["logo_url"]
        wear_square_mark(mark, look, accent, C.px(_HIT_MARK_PX))
        line.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        text.addStretch(1)
        self._name = label(self, self._title, C.NAME_PX, C.T.text, C.MEDIUM)
        self._name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text.addWidget(self._name)
        self._sub = label(self, self._note, C.BODY_PX, C.T.text_2)
        self._sub.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text.addWidget(self._sub)
        text.addStretch(1)
        line.addLayout(text, 1)
        labels_through(self)
        self.setAccessibleName(self._title)
        self._laid = -1

    def set_card_width(self, width: int) -> None:
        width = max(1, int(width))
        self.setFixedWidth(width)
        if width == self._laid:
            return
        self._laid = width
        room = max(1, width - C.px(_HIT_MARK_PX) - C.px(12) - 2 * C.px(C.EDGE))
        self._name.setText(self._name.fontMetrics().elidedText(self._title, Qt.TextElideMode.ElideRight, room))
        self._sub.setText(self._sub.fontMetrics().elidedText(self._note, Qt.TextElideMode.ElideRight, room))
