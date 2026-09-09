# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
















from __future__ import annotations

from html import escape

from qgis.PyQt.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QRectF,
    Qt,
    QVariantAnimation,
    pyqtSignal,
)
from qgis.PyQt.QtGui import QPainter
from qgis.PyQt.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .cards_click import _ClickRow
from .font_scale import scale_qss_font_px
from .icons import render_pixmap
from .style import (
    FONT_BODY,
    FONT_HINT,
    HOVER,
    INK,
    INK_2,
    INK_3,
    MOTION_FADE_UP_MS,
    RADIUS_CHIP,
    SPACE_CARD,
    accent_color,
    qcolor,
)
from .widgets import break_anywhere


GLYPH_PX = 13
GLYPH_SLOT_PX = 16

ROW_MIN_PX = 28


_ROW_PAD_X = 6
_ROW_PAD_Y = 2
_ROW_GAP = SPACE_CARD + 2

_RISE_PX = 8

_LABEL_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BODY}px; color: {INK}; background: transparent; border: none; }}"
)
_MEDIUM_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BODY}px; font-weight: 500; color: {INK};"
    " background: transparent; border: none; }"
)
_SECOND_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BODY}px; color: {INK_2}; background: transparent; border: none; }}"
)
_HINT_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_HINT}px; color: {INK_3}; background: transparent; border: none; }}"
)


def fade_up(widget: QWidget, owner) -> None:







    layout = widget.layout()
    try:
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)
        margins = layout.contentsMargins() if layout is not None else None
        rise = QVariantAnimation(widget)
        rise.setDuration(MOTION_FADE_UP_MS)
        rise.setEasingCurve(QEasingCurve.Type.OutQuint)
        rise.setStartValue(0.0)
        rise.setEndValue(1.0)

        def _step(value):
            try:
                effect.setOpacity(float(value))
                if margins is not None:
                    lift = int(round(_RISE_PX * (1.0 - float(value))))
                    layout.setContentsMargins(margins.left(), margins.top() + lift,
                                              margins.right(), max(0, margins.bottom() - lift))
            except RuntimeError:
                pass

        def _done():
            try:
                widget.setGraphicsEffect(None)
                if margins is not None:
                    layout.setContentsMargins(margins)
            except RuntimeError:
                pass
            owner.release(rise)

        rise.valueChanged.connect(_step)
        rise.finished.connect(_done)


        widget._fade_anim = rise
        owner.hold(rise)
        rise.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    except (RuntimeError, AttributeError, TypeError):
        try:
            widget.setGraphicsEffect(None)
        except (RuntimeError, AttributeError):
            pass


class HoverRow(_ClickRow):



    hovered = pyqtSignal(bool)

    def __init__(self, parent=None, radius: int = RADIUS_CHIP):
        super().__init__(parent)
        self._radius = radius
        self._hot = False
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def is_hot(self) -> bool:
        return self._hot

    def enterEvent(self, event):  # noqa: N802
        self._hot = True
        self.update()
        self.hovered.emit(True)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._hot = False
        self.update()
        self.hovered.emit(False)
        super().leaveEvent(event)

    def paintEvent(self, event):  # noqa: N802
        if self._hot and self.cursor().shape() == Qt.CursorShape.PointingHandCursor:
            try:
                painter = QPainter(self)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(qcolor(HOVER))
                painter.drawRoundedRect(QRectF(self.rect()), self._radius, self._radius)
                painter.end()
            except Exception:  # nosec B110
                pass
        super().paintEvent(event)


class Chevron(QWidget):



    def __init__(self, size: int, parent=None):
        super().__init__(parent)
        self._size = size
        self._angle = 0.0
        self._hot = False
        self._turn: QVariantAnimation | None = None
        self.setFixedSize(size, size)

    def set_open(self, open_: bool, duration_ms: int = 0) -> None:
        target = 180.0 if open_ else 0.0
        self._stop_turn()
        if duration_ms <= 0 or not self.isVisible() or self._angle == target:
            self._angle = target
            self.update()
            return
        turn = QVariantAnimation(self)
        turn.setDuration(duration_ms)
        turn.setEasingCurve(QEasingCurve.Type.OutQuint)
        turn.setStartValue(self._angle)
        turn.setEndValue(target)
        turn.valueChanged.connect(self._on_turn)
        self._turn = turn
        turn.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _on_turn(self, value) -> None:
        try:
            self._angle = float(value)
            self.update()
        except RuntimeError:
            pass

    def _stop_turn(self) -> None:
        turn, self._turn = self._turn, None
        if turn is not None:
            try:
                turn.stop()
            except (RuntimeError, AttributeError):
                pass

    def cleanup(self) -> None:
        self._stop_turn()

    def set_hot(self, hot: bool) -> None:
        self._hot = bool(hot)
        self.update()

    def paintEvent(self, event):  # noqa: N802
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            colour = accent_color() if self._hot else qcolor(INK_3)
            ratio = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
            pixmap = render_pixmap("chevron_down", colour, self._size, ratio)
            half = self._size / 2.0
            painter.translate(half, half)
            painter.rotate(self._angle)
            painter.drawPixmap(QRectF(-half, -half, self._size, self._size), pixmap,
                               QRectF(pixmap.rect()))
            painter.end()
        except Exception:  # noqa: BLE001
            return


def _row_layout(row: QWidget, height: int | None = None) -> QHBoxLayout:
    lay = QHBoxLayout(row)
    lay.setContentsMargins(_ROW_PAD_X, _ROW_PAD_Y, _ROW_PAD_X, _ROW_PAD_Y)
    lay.setSpacing(_ROW_GAP)
    if height is not None:
        row.setMinimumHeight(height)
    return lay






class ThoughtRow(QWidget):



    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._full = text
        lay = QVBoxLayout(self)
        lay.setContentsMargins(_ROW_PAD_X, _ROW_PAD_Y + 2, _ROW_PAD_X, _ROW_PAD_Y + 2)
        lay.setSpacing(0)
        self._text = QLabel(self)
        self._text.setObjectName("thought")
        self._text.setStyleSheet(_SECOND_QSS)
        self._text.setTextFormat(Qt.TextFormat.RichText)
        self._text.setText(f'<p style="line-height:150%; margin:0">{escape(break_anywhere(text))}</p>')
        self._text.setWordWrap(True)
        self._text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._text)

    def text(self) -> str:
        return self._full


__all__ = ["Chevron", "GLYPH_PX", "GLYPH_SLOT_PX", "HoverRow", "ROW_MIN_PX",
           "ThoughtRow", "_LABEL_QSS", "fade_up"]
