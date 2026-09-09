# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
























from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout

from ..shared import event_pos
from . import common as C
from .pictures import PictureView
from .pressable import Pressable, labels_through


_INSET = 6


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


def tile_picture(case, owner, parent, radius: int, label: str = "") -> PictureView:


    before, after = case.pair(small=True) if hasattr(case, "pair") else ("", "")
    single = getattr(case, "image_small", "") or getattr(case, "image", "")
    return PictureView(single, owner=owner, parent=parent, radius=radius, label=label,
                       pair=(before, after), split=getattr(case, "split", 0.5))


def has_picture(case) -> bool:
    return bool(getattr(case, "image", "") or getattr(case, "image_small", "")
                or (getattr(case, "before", "") and getattr(case, "after", "")))


class PictureHover:




    def _hover_picture(self) -> None:
        self._picture.set_shade(1.0 if self._pressed else 0.6 if self._hovered else 0.0)
        self._picture.set_hover(self._hovered)
        if not self._hovered:
            self._picture.rest()

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._hovered and self._picture.is_pair():
            self._picture.follow(event_pos(event).x() - self._picture.x())
        super().mouseMoveEvent(event)


def tile_height(width: int) -> int:

    return round(width * 9 / 16) + C.px(C.TILE_TEXT_H)


class ExampleTile(PictureHover, Pressable):


    def __init__(self, case, parent=None, show_note: bool = True):
        super().__init__(parent, radius=C.RADIUS_TILE)
        self.case = case
        self._title_text = str(case.title or "")
        self._note_text = str(case.outcome or "")
        self._pictured = has_picture(case)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self._picture = tile_picture(case, self, self, C.RADIUS_TILE,
                                     "" if self._pictured else self._title_text)
        self._picture.setAccessibleName(self._note_text)
        col.addWidget(self._picture)
        col.addSpacing(C.px(C.SPACE_1))
        self._title = QLabel(self)
        self._title.setStyleSheet(C.text_qss(C.BODY_PX, C.T.text, C.MEDIUM))
        self._title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._title.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._title.setContentsMargins(_INSET, 0, _INSET, 0)
        self._title.setVisible(self._pictured)
        col.addWidget(self._title)
        col.addSpacing(2)
        self._note = QLabel(self)
        self._note.setStyleSheet(C.text_qss(C.SMALL_PX, C.T.text_2))
        self._note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._note.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._note.setContentsMargins(_INSET, 0, _INSET, 0)
        self._note.setVisible(show_note)
        col.addWidget(self._note)
        col.addStretch(1)
        labels_through(self)
        self._laid_width = -1

    def _state_changed(self) -> None:
        self._hover_picture()
        self.update()

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
        room = max(1, width - 2 * _INSET)
        if self._pictured:
            first, second = elide_two_lines(self._title.fontMetrics(), self._title_text, room)
            self._title.setText(first + ("\n" + second if second else ""))
        first, second = elide_two_lines(self._note.fontMetrics(), self._note_text, room)
        self._note.setText(first + ("\n" + second if second else ""))

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._picture.setFixedHeight(round(self.width() * 9 / 16))
        self._relayout(self.width())



ROW_PICTURE_W = 112


class ExampleRow(PictureHover, Pressable):




    def __init__(self, case, parent=None):
        super().__init__(parent, radius=C.RADIUS_TILE)
        self.case = case
        self._title_text = str(case.title or "")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        picture_h = round(C.px(ROW_PICTURE_W) * 9 / 16)
        self.setFixedHeight(picture_h + 2 * C.px(_INSET))
        line = QHBoxLayout(self)
        line.setContentsMargins(C.px(_INSET), C.px(_INSET), C.px(_INSET), C.px(_INSET))
        line.setSpacing(C.px(12))
        self._picture = tile_picture(case, self, self, C.px(8))
        self._picture.setFixedSize(C.px(ROW_PICTURE_W), picture_h)
        self._picture.setAccessibleName(str(case.outcome or ""))
        line.addWidget(self._picture, 0, Qt.AlignmentFlag.AlignVCenter)
        self._title = QLabel(self)
        self._title.setStyleSheet(C.text_qss(C.BODY_PX, C.T.text, C.MEDIUM))
        self._title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        line.addWidget(self._title, 1, Qt.AlignmentFlag.AlignVCenter)
        labels_through(self)
        self.setAccessibleName(self._title_text)
        self._laid_width = -1

    def _state_changed(self) -> None:
        self._hover_picture()
        self.update()

    def set_focused(self, focused: bool) -> None:
        super().set_focused(focused)
        self._picture.set_focused(focused)

    def set_tile_width(self, width: int) -> None:
        width = max(1, int(width))
        self.setFixedWidth(width)
        if width == self._laid_width:
            return
        self._laid_width = width
        room = max(1, width - C.px(ROW_PICTURE_W) - C.px(12) - 2 * C.px(_INSET))
        first, second = elide_two_lines(self._title.fontMetrics(), self._title_text, room)
        self._title.setText(first + ("\n" + second if second else ""))
