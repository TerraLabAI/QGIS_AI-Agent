# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The drop overlay: what covers the panel while a layer or a file is dragged over it."""










from __future__ import annotations

from qgis.PyQt.QtCore import QAbstractAnimation, QEasingCurve, QPropertyAnimation, QRectF, Qt
from qgis.PyQt.QtGui import QColor, QFont, QPainter, QPen
from qgis.PyQt.QtWidgets import QGraphicsOpacityEffect, QWidget

from .font_scale import widget_pixel_ratio
from .icons import ink_of, paper_of, render_pixmap
from .style import FONT_BASE, FONT_BODY, RADIUS_PANEL, accent_color

_INSET = 12
_GLYPH = 32
_GLYPH_GAP = 12
_LINE_GAP = 4
_GROUND_ALPHA = 225
_FIELD_ALPHA = 26
_BORDER_ALPHA = 150
_MUTED_ALPHA = 165

_FADE_MS = 110


class DropOverlay(QWidget):
    """Covers its parent while a drag is over it; never takes the drop itself."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("dropOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAcceptDrops(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._title = self.tr("Add to the chat")
        self._subtitle = self.tr("Drop a layer or a file here")
        self._glyph = "layers"
        self._fade = QGraphicsOpacityEffect(self)
        self._fade.setOpacity(1.0)
        self.setGraphicsEffect(self._fade)
        self.hide()



    def show_over(self, host: QWidget, glyph: str = "layers") -> None:
        """Cover ``host`` (the widget this overlay is a child of) and raise."""
        self._glyph = glyph or "layers"
        self.setGeometry(host.rect())
        self.raise_()
        if not self.isVisible():
            self.show()
            self._fade_in()
        self.update()

    def _fade_in(self) -> None:
        try:
            self._fade.setOpacity(0.0)
            fade = QPropertyAnimation(self._fade, b"opacity", self)
            fade.setDuration(_FADE_MS)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.setEasingCurve(QEasingCurve.Type.OutCubic)


            self._fade_anim = fade
            fade.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        except (RuntimeError, AttributeError, TypeError):
            self._fade.setOpacity(1.0)

    def stop_animations(self) -> None:
        """Stop the fade, before the overlay or the panel under it goes."""
        fade = getattr(self, "_fade_anim", None)
        self._fade_anim = None
        if fade is None:
            return
        try:
            fade.stop()
        except (RuntimeError, AttributeError):
            pass

    def sync_geometry(self, host: QWidget) -> None:
        if self.isVisible():
            self.setGeometry(host.rect())



    def paintEvent(self, event):  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            ink = ink_of(self)
            paper = paper_of(self)
            ground = QColor(paper)
            ground.setAlpha(_GROUND_ALPHA)
            painter.fillRect(self.rect(), ground)

            field = QRectF(self.rect()).adjusted(_INSET, _INSET, -_INSET, -_INSET)
            accent = accent_color()
            tint = QColor(accent)
            tint.setAlpha(_FIELD_ALPHA)
            border = QColor(accent)
            border.setAlpha(_BORDER_ALPHA)
            pen = QPen(border, 1.5)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(tint)
            painter.drawRoundedRect(field, RADIUS_PANEL, RADIUS_PANEL)

            title_font = QFont(self.font())
            title_font.setPixelSize(FONT_BASE + 3)
            title_font.setWeight(QFont.Weight.DemiBold)
            body_font = QFont(self.font())
            body_font.setPixelSize(FONT_BODY)
            painter.setFont(title_font)
            title_h = painter.fontMetrics().height()
            painter.setFont(body_font)
            body_h = painter.fontMetrics().height()

            total = _GLYPH + _GLYPH_GAP + title_h + _LINE_GAP + body_h
            top = field.center().y() - total / 2
            ratio = widget_pixel_ratio(self)
            glyph = render_pixmap(self._glyph, accent, _GLYPH, ratio)
            painter.drawPixmap(int(field.center().x() - _GLYPH / 2), int(top), glyph)

            y = top + _GLYPH + _GLYPH_GAP
            painter.setFont(title_font)
            painter.setPen(ink)
            painter.drawText(QRectF(field.left(), y, field.width(), title_h),
                             int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
                             self._title)
            y += title_h + _LINE_GAP
            muted = QColor(ink)
            muted.setAlpha(_MUTED_ALPHA)
            painter.setFont(body_font)
            painter.setPen(muted)
            painter.drawText(QRectF(field.left(), y, field.width(), body_h),
                             int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
                             self._subtitle)
        finally:
            painter.end()
