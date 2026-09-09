# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



















from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QLabel, QSizePolicy, QVBoxLayout

from ..shared import tr
from . import common as C
from .common import source_rows
from .pictures import PictureView
from .pressable import Pressable, labels_through


def elide_two_lines(metrics, text: str, width: int) -> tuple:






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


def tile_height(width: int) -> int:

    return round(width * 9 / 16) + C.px(C.TILE_TEXT_H)


class ExampleTile(Pressable):


    def __init__(self, case, parent=None, show_note: bool = True):
        super().__init__(parent, radius=C.RADIUS_TILE, hover=lambda: None, press=lambda: None)
        self.case = case
        self._title_text = str(case.title or "")
        self._note_text = str(case.outcome or "")
        self._pictured = bool(getattr(case, "image", ""))
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self._picture = PictureView(getattr(case, "image", ""), owner=self, parent=self,
                                    radius=C.RADIUS_TILE,
                                    label="" if self._pictured else self._title_text)
        self._picture.setAccessibleName(self._note_text)
        col.addWidget(self._picture)
        col.addSpacing(C.px(C.SPACE_1))
        self._title = QLabel(self)
        self._title.setStyleSheet(C.text_qss(C.BODY_PX, C.T.text, C.MEDIUM))
        self._title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._title.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._title.setVisible(self._pictured)
        col.addWidget(self._title)
        col.addSpacing(2)
        self._note = QLabel(self)
        self._note.setStyleSheet(C.text_qss(C.SMALL_PX, C.T.text_2))
        self._note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._note.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._note.setVisible(show_note)
        col.addWidget(self._note)
        col.addStretch(1)
        labels_through(self)



        names = ", ".join(str(r.get("name") or "") for r in source_rows(case))
        self.setToolTip("\n".join(t for t in (
            self._title_text, self._note_text,
            tr("Data: {names}").format(names=names) if names else "",
            tr("Sample data included") if getattr(case, "samples", ()) else "") if t))
        self._laid_width = -1

    def _state_changed(self) -> None:
        self._picture.set_shade(1.0 if self._pressed else 0.6 if self._hovered else 0.0)

    def set_focused(self, focused: bool) -> None:
        super().set_focused(focused)
        self._picture.set_focused(focused)

    def set_tile_width(self, width: int) -> None:

        width = max(1, int(width))
        self.setFixedWidth(width)
        self.setFixedHeight(tile_height(width))
        self._picture.setFixedHeight(round(width * 9 / 16))
        self._relayout(width)

    def _relayout(self, width: int) -> None:
        if width == self._laid_width:
            return
        self._laid_width = width
        if self._pictured:
            first, second = elide_two_lines(self._title.fontMetrics(), self._title_text, width)
            self._title.setText(first + ("\n" + second if second else ""))
            self._note.setText(self._note.fontMetrics().elidedText(
                self._note_text, Qt.TextElideMode.ElideRight, width))
        else:

            first, second = elide_two_lines(self._note.fontMetrics(), self._note_text, width)
            self._note.setText(first + ("\n" + second if second else ""))

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._picture.setFixedHeight(round(self.width() * 9 / 16))
        self._relayout(self.width())
