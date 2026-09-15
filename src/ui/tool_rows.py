# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The parts a tool chip row is built from."""





from __future__ import annotations

from qgis.PyQt.QtCore import (
    QRectF,
    QSize,
    Qt,
    pyqtSignal,
)
from qgis.PyQt.QtGui import QPainter, QPainterPath
from qgis.PyQt.QtWidgets import QLabel, QSizePolicy

from .card_base import mono_font
from .cards_click import _ClickRow
from .style import (
    CHIP_PX,
    FIELD,
    FONT_HINT,
    INK_2,
    RADIUS_CHIP,
    qcolor,
)


class _HoverRow(_ClickRow):
    """A click row that says when the pointer is on it, for the parts that only show under the pointer (the chevron)."""


    hovered = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def enterEvent(self, event):  # noqa: N802 - Qt override
        self.hovered.emit(True)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        self.hovered.emit(False)
        super().leaveEvent(event)


class _MonoChip(QLabel):
    """The argument of a tool row: mono on the field, 22 px, 6 px corners, elided at the right."""



    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self._font = mono_font(FONT_HINT)
        self.setFixedHeight(CHIP_PX)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt override
        self._full = " ".join(str(text or "").split())
        super().setText(self._full)



        self.setToolTip(self._full)
        self.updateGeometry()
        self.update()

    def full_text(self) -> str:
        return self._full

    def _text_width(self) -> int:
        from qgis.PyQt.QtGui import QFontMetrics

        return QFontMetrics(self._font).horizontalAdvance(self._full)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self._text_width() + 12 + 2, CHIP_PX)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(min(48, self.sizeHint().width()), CHIP_PX)

    def paintEvent(self, event):  # noqa: N802 - Qt override
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            path = QPainterPath()
            path.addRoundedRect(QRectF(self.rect()), RADIUS_CHIP, RADIUS_CHIP)
            painter.fillPath(path, qcolor(FIELD))
            painter.setFont(self._font)
            painter.setPen(qcolor(INK_2))
            from qgis.PyQt.QtGui import QFontMetrics

            text = QFontMetrics(self._font).elidedText(
                self._full, Qt.TextElideMode.ElideRight, max(0, self.width() - 12))
            painter.drawText(QRectF(6, 0, self.width() - 12, self.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            painter.end()
        except Exception:  # noqa: BLE001 - paint must never raise
            return


__all__ = ["_HoverRow", "_MonoChip"]
