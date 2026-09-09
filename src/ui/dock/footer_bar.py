# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the dock footer row paints for itself: the two vector glyphs, and the cross-promo button that gives way when the panel is narrow."""







from __future__ import annotations

import math

from qgis.PyQt.QtCore import QPointF, Qt
from qgis.PyQt.QtGui import (
    QFontMetrics,
    QFontMetricsF,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from qgis.PyQt.QtWidgets import QSizePolicy

from ..font_scale import widget_pixel_ratio
from ..icons import ink_of
from ..widgets import IconButton


FOOTER_GLYPH_PX = 20


FOOTER_CTA_MIN_PX = 56


def _footer_paint_ratio(widget) -> float:
    """Pixels the SCREEN puts behind one drawing unit, never zero."""





    ratio = 0.0
    try:
        screen = widget.screen()
        if screen is not None:
            ratio = float(screen.devicePixelRatio())
    except (AttributeError, RuntimeError):
        ratio = 0.0
    return max(ratio, widget_pixel_ratio(widget), 1.0)


def _footer_glyph_pixmap(widget) -> QPixmap:
    """Transparent square at the screen's own pixel count, tagged with it."""
    ratio = _footer_paint_ratio(widget)
    physical = max(1, int(round(FOOTER_GLYPH_PX * ratio)))
    pixmap = QPixmap(physical, physical)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    return pixmap


def footer_gear_icon(widget) -> QIcon:
    """Settings glyph: a vector gear in the palette text colour."""
    size = FOOTER_GLYPH_PX
    ink = ink_of(widget)
    pixmap = _footer_glyph_pixmap(widget)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    centre = size / 2.0
    teeth = 8
    step = 2.0 * math.pi / teeth
    r_tip = size * 0.46
    r_root = size * 0.34
    half_tip = step * 0.18
    half_root = step * 0.30
    path = QPainterPath()
    for i in range(teeth):
        angle = i * step
        corners = (
            (angle - half_root, r_root),
            (angle - half_tip, r_tip),
            (angle + half_tip, r_tip),
            (angle + half_root, r_root),
        )
        for ang, radius in corners:
            point = QPointF(centre + radius * math.cos(ang),
                            centre + radius * math.sin(ang))
            if i == 0 and ang == corners[0][0]:
                path.moveTo(point)
            else:
                path.lineTo(point)
    path.closeSubpath()
    path.addEllipse(QPointF(centre, centre), size * 0.15, size * 0.15)
    path.setFillRule(Qt.FillRule.OddEvenFill)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(ink)
    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


def footer_book_icon(widget) -> QIcon:
    """Tutorial glyph: two stroked pages meeting on a spine."""
    size = FOOTER_GLYPH_PX
    ink = ink_of(widget)
    pixmap = _footer_glyph_pixmap(widget)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(ink)
    pen.setWidthF(2.2)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    centre = size / 2.0
    top, bottom = size * 0.18, size * 0.82
    edge = size * 0.10
    for outer in (edge, size - edge):
        page = QPainterPath()
        page.moveTo(QPointF(centre, top))
        page.quadTo(
            QPointF((centre + outer) / 2.0, top - size * 0.09),
            QPointF(outer, top - size * 0.05),
        )
        page.lineTo(QPointF(outer, bottom - size * 0.05))
        page.quadTo(
            QPointF((centre + outer) / 2.0, bottom - size * 0.09),
            QPointF(centre, bottom),
        )
        page.closeSubpath()
        painter.drawPath(page)
    painter.end()
    return QIcon(pixmap)


class _ElidingFooterButton(IconButton):
    """Footer button whose label gives way when the panel is narrow."""






    def __init__(self, parent=None):
        super().__init__(parent)
        self._full_label = ""


        self.setSizePolicy(
            QSizePolicy.Policy.Maximum, self.sizePolicy().verticalPolicy())

    def set_label(self, text: str) -> None:
        """The text to show whenever there is room for all of it."""
        self._full_label = text
        self._apply_elided_label()

    def sizeHint(self):  # noqa: N802 - Qt override
        hint = super().sizeHint()
        chrome = self._label_chrome()
        hint.setWidth(self._full_label_width() + chrome)
        return hint

    def _full_label_width(self) -> int:
        """Width the whole label needs, rounded UP."""
        return int(math.ceil(
            QFontMetricsF(self.font()).horizontalAdvance(self._full_label)))

    def minimumSizeHint(self):  # noqa: N802 - Qt override
        hint = super().minimumSizeHint()
        hint.setWidth(min(hint.width(), FOOTER_CTA_MIN_PX))
        return hint

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._apply_elided_label()

    def _apply_elided_label(self) -> None:
        room = max(0, self.width() - self._label_chrome())
        if room >= self._full_label_width():
            text = self._full_label
        else:
            text = QFontMetrics(self.font()).elidedText(
                self._full_label, Qt.TextElideMode.ElideRight, room)


        if text != self.text():
            super().setText(text)

    def _label_chrome(self) -> int:
        """What the button spends on a label besides the glyphs."""
        metrics = QFontMetrics(self.font())
        return max(
            0, super().sizeHint().width() - metrics.horizontalAdvance(self.text()))
