# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later














from __future__ import annotations

from qgis.PyQt.QtCore import QRectF, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QPainter
from qgis.PyQt.QtWidgets import QFrame, QLabel, QWidget

from ..shared import event_pos


def mouse_through(*widgets) -> None:
    for widget in widgets:
        widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)


def labels_through(root: QWidget) -> None:

    mouse_through(*root.findChildren(QLabel))


class Pressable(QFrame):


    clicked = pyqtSignal()

    def __init__(self, parent=None, radius: int = 10, rest=None, hover=None, press=None,
                 static: bool = False):
        super().__init__(parent)
        from . import common

        self._radius = radius
        self._rest = rest
        self._hover_colour = hover or (lambda: common.T.hover)
        self._press_colour = press or (lambda: common.T.press)
        self._hovered = False
        self._pressed = False
        self._static = bool(static)
        self._focused = False
        if not static:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _state_changed(self) -> None:

        self.update()

    def set_focused(self, focused: bool) -> None:
        self._focused = bool(focused)
        self.update()

    @staticmethod
    def _colour(value):
        value = value() if callable(value) else value
        return QColor(value) if value else None

    def enterEvent(self, event):  # noqa: N802
        if not self._static:
            self._hovered = True
            self._state_changed()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._hovered = False
        self._pressed = False
        self._state_changed()
        super().leaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802


        if event.button() == Qt.MouseButton.LeftButton and not self._static:
            self._pressed = True
            self._state_changed()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._pressed:
            self._pressed = False
            inside = self.rect().contains(event_pos(event))
            self._state_changed()
            event.accept()
            if inside:
                self.clicked.emit()
            return
        super().mouseReleaseEvent(event)

    def background(self) -> QColor | None:
        if self._pressed:
            return self._colour(self._press_colour)
        if self._hovered:
            return self._colour(self._hover_colour) or self._colour(self._rest)
        return self._colour(self._rest)

    def paintEvent(self, event):  # noqa: N802
        colour = self.background()
        if colour is not None and colour.alpha() > 0:
            painter = QPainter(self)
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(colour)
                painter.drawRoundedRect(QRectF(self.rect()), self._radius, self._radius)
            finally:
                painter.end()
        super().paintEvent(event)
