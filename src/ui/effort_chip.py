# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The effort chip in the composer's bottom row, right before Send."""





























from __future__ import annotations

import math

from qgis.PyQt.QtCore import (
    QCoreApplication,
    QEasingCurve,
    QEvent,
    QPoint,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    pyqtSignal,
)
from qgis.PyQt.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPen, QPolygonF, QRadialGradient
from qgis.PyQt.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.plan import effort_name, efforts_allowed, efforts_stated
from .font_scale import scale_qss_font_px
from .icons import pixmap_for
from .permission_chip import select_qss
from .shared import event_pos, get_effort_text
from .style import (
    ACCENT,
    ACCENT_DARK,
    BTN_SMALL_PX,
    DARK,
    FIELD,
    FONT_BODY,
    FONT_MICRO,
    FONT_PROSE,
    INK,
    INK_2,
    INK_3,
    LINE,
    LINE_STRONG,
    MOTION_POP_MS,
    ON_ACCENT,
    RADIUS_CARD,
    SURFACE,
    qcolor,
    repolish,
)

LOW, MEDIUM, HIGH = "low", "medium", "high"
EFFORTS = (LOW, MEDIUM, HIGH)







DEFAULT_EFFORT = LOW


FREE_EFFORTS = (LOW,)


def pickable_efforts(paid: bool) -> tuple:
    """The stops this account may keep, in order."""








    if efforts_stated():
        kept = tuple(e for e in EFFORTS if e in efforts_allowed())
        if kept:
            return kept
    return EFFORTS if paid else FREE_EFFORTS


_CHIP_QSS = select_qss("effortChip")

_POPOVER_QSS = scale_qss_font_px(
    f"QFrame#effortPopover {{ background: {SURFACE};"
    f" border: 1px solid {LINE_STRONG}; border-radius: {RADIUS_CARD}px; }}"
    f"QLabel#effortTitle {{ font-size: {FONT_PROSE}px; font-weight: 600;"
    f" color: {INK}; background: transparent; border: none; }}"
    f"QLabel#effortNote {{ font-size: {FONT_BODY}px; color: {INK_2};"
    " background: transparent; border: none; }"


    f"QPushButton#effortCta {{ background: {ACCENT}; color: {ON_ACCENT}; border: none;"
    f" border-radius: 12px; padding: 0 10px; font-size: {FONT_MICRO + 1}px; font-weight: 600; }}"
    f"QPushButton#effortCta:hover {{ background: {ACCENT_DARK}; }}"
    f"QPushButton#effortCta:pressed {{ background: {ACCENT_DARK}; }}"
    "QLabel { background: transparent; border: none; }"
)




_POPOVER_WIDTH = 320
_CARD_MARGIN = 16
_CARD_SPACING = 6
_TEXT_WIDTH = _POPOVER_WIDTH - 2 * _CARD_MARGIN





_TRACK_PX = 18
_HANDLE_PX = 24
_HANDLE_GROW = 1.15
_STOP_PX = 3
_SLIDER_PAD = 8
_SLIDER_PX = int(_HANDLE_PX * _HANDLE_GROW) + 2 * _SLIDER_PAD
_CTA_PX = 24
_TWINKLE_MS = 40




_LEVEL_MOTION = (


    (3, 1.10, 0.22, 0.05, 0),
    (5, 1.17, 0.32, 0.09, 0),
    (7, 1.24, 0.42, 0.13, 2600),
)
_CHARGE_MS = 520
_RING_MS = 420




_ARC_MS = 720
_HEAT_MS = 360
_HEAT_LIGHTER = 122
_CHEVRON_SIZE = 12
_LOCK_PX = 10
_CHIP_GAP = 4


def normalize_effort(effort: str) -> str:
    """The level as this build spells it, the legacy ``instant`` included."""




    return effort_name(effort) or DEFAULT_EFFORT


def effort_texts(widget: QWidget | None = None) -> tuple:
    """``(effort, short name, one-sentence description)`` for the three levels."""











    shipped = (
        (LOW, "Low",
         QCoreApplication.translate(
             "EffortChip",
             "Starts working as fast as possible. Best for quick edits and questions. Does not plan or research.")),
        (MEDIUM, "Medium",
         QCoreApplication.translate(
             "EffortChip", "Smart agent that plans, checks its results and looks up algorithms and documentation.")),
        (HIGH, "High",
         QCoreApplication.translate(
             "EffortChip", "Deeper planning, research and independent direction for harder tasks.")),
    )





    return tuple((level, *get_effort_text(level, name, note)) for level, name, note in shipped)


def _index(effort: str) -> int:
    return EFFORTS.index(normalize_effort(effort))






_SPECKS = (
    (0.06, 0.30, 1.0, 0.55, 0.0), (0.11, 0.70, 0.7, 0.35, 1.1), (0.17, 0.45, 1.3, 0.75, 2.3),
    (0.24, 0.22, 0.8, 0.45, 3.4), (0.29, 0.78, 1.0, 0.60, 4.6), (0.36, 0.40, 0.7, 0.35, 5.7),
    (0.42, 0.62, 1.2, 0.70, 0.8), (0.49, 0.28, 0.8, 0.40, 1.9), (0.55, 0.74, 1.0, 0.55, 3.1),
    (0.61, 0.36, 0.7, 0.35, 4.2), (0.68, 0.58, 1.3, 0.75, 5.4), (0.74, 0.24, 0.8, 0.45, 0.4),
    (0.80, 0.70, 1.0, 0.60, 1.5), (0.86, 0.42, 0.7, 0.35, 2.7), (0.92, 0.64, 1.1, 0.65, 3.8),
)


class EffortSlider(QWidget):
    """Three stops on one track; the handle snaps to the nearest on release."""
















    previewed = pyqtSignal(str)
    changed = pyqtSignal(str)
    locked_reached = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("effortSlider")
        self.setFixedHeight(_SLIDER_PX)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self._value = _index(DEFAULT_EFFORT)

        self._pos = float(self._value)
        self._preview = self._value
        self._locked_from = len(EFFORTS)
        self._dragging = False
        self._hover = False
        self._scale = 1.0
        self._phase = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(MOTION_POP_MS + 120)
        self._anim.setEasingCurve(QEasingCurve.Type.OutQuint)
        self._anim.valueChanged.connect(self._on_anim)
        self._swell = QVariantAnimation(self)
        self._swell.setDuration(140)
        self._swell.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._swell.valueChanged.connect(self._on_swell)
        self._twinkle = QTimer(self)
        self._twinkle.setInterval(_TWINKLE_MS)
        self._twinkle.timeout.connect(self._on_twinkle)



        self._charge = -1.0
        self._charge_alpha = 0.0
        self._charge_anim = QVariantAnimation(self)
        self._charge_anim.setDuration(_CHARGE_MS)
        self._charge_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._charge_anim.valueChanged.connect(self._on_charge)
        self._charge_anim.finished.connect(self._end_charge)
        self._ring = -1.0
        self._ring_level = 0
        self._ring_anim = QVariantAnimation(self)
        self._ring_anim.setDuration(_RING_MS)
        self._ring_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._ring_anim.valueChanged.connect(self._on_ring)
        self._ring_anim.finished.connect(self._end_ring)
        self._ambient = QTimer(self)
        self._ambient.timeout.connect(self._on_ambient)


        self._arc = -1.0
        self._arc_anim = QVariantAnimation(self)
        self._arc_anim.setDuration(_ARC_MS)
        self._arc_anim.setEasingCurve(QEasingCurve.Type.Linear)
        self._arc_anim.valueChanged.connect(self._on_arc)
        self._arc_anim.finished.connect(self._end_arc)
        self._heat = 1.0 if self._value == len(EFFORTS) - 1 else 0.0
        self._heat_anim = QVariantAnimation(self)
        self._heat_anim.setDuration(_HEAT_MS)
        self._heat_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._heat_anim.valueChanged.connect(self._on_heat)



    def value(self) -> str:
        return EFFORTS[self._value]

    def set_value(self, effort: str, animate: bool = False) -> None:
        """Move the handle to ``effort`` without emitting."""
        self._value = _index(effort)
        self._preview = self._value
        self._heat_anim.stop()
        self._heat = 1.0 if self._value == len(EFFORTS) - 1 else 0.0
        if animate:
            self._glide_to(self._value)
        else:
            self._anim.stop()
            self._pos = float(self._value)
            self.update()

    def set_locked_from(self, index: int) -> None:
        """Stops at ``index`` and beyond are paid: shown, kept, and named as Pro."""
        self._locked_from = max(0, min(int(index), len(EFFORTS)))
        self.update()

    def is_locked(self, effort: str) -> bool:
        return _index(effort) >= self._locked_from



    def _track(self) -> QRectF:
        top = (self.height() - _TRACK_PX) / 2
        return QRectF(_SLIDER_PAD, top, self.width() - 2 * _SLIDER_PAD, _TRACK_PX)

    def _span(self) -> tuple[float, float]:
        """The handle centre's leftmost x and its travel, inside the padded track."""
        left = _SLIDER_PAD + _HANDLE_PX / 2
        return left, max(1.0, self.width() - 2 * _SLIDER_PAD - _HANDLE_PX)

    def _stop_x(self, index: float) -> float:
        left, span = self._span()
        return left + span * index / (len(EFFORTS) - 1)

    def _pos_at(self, x: float) -> float:
        left, span = self._span()
        return max(0.0, min(float(len(EFFORTS) - 1), (x - left) / span * (len(EFFORTS) - 1)))



    def _on_anim(self, value) -> None:
        try:
            self._pos = float(value)
        except (TypeError, ValueError):
            return
        self.update()

    def _on_swell(self, value) -> None:
        try:
            self._scale = float(value)
        except (TypeError, ValueError):
            return
        self.update()

    def _on_twinkle(self) -> None:
        self._phase = (self._phase + _LEVEL_MOTION[self._value][3]) % (2 * math.pi)
        self.update()

    def _on_charge(self, value) -> None:
        try:
            self._charge = float(value)
        except (TypeError, ValueError):
            return
        self.update()

    def _end_charge(self) -> None:
        self._charge = -1.0
        self.update()

    def _on_ring(self, value) -> None:
        try:
            self._ring = float(value)
        except (TypeError, ValueError):
            return
        self.update()

    def _end_ring(self) -> None:
        self._ring = -1.0
        self.update()

    def _on_arc(self, value) -> None:
        try:
            self._arc = float(value)
        except (TypeError, ValueError):
            return
        self.update()

    def _end_arc(self) -> None:
        self._arc = -1.0
        self.update()

    def _on_heat(self, value) -> None:
        try:
            self._heat = float(value)
        except (TypeError, ValueError):
            return
        self.update()

    def _heat_to(self, heat: float) -> None:
        self._heat_anim.stop()
        self._heat_anim.setStartValue(float(self._heat))
        self._heat_anim.setEndValue(float(heat))
        self._heat_anim.start()

    def _on_ambient(self) -> None:
        if self.isVisible() and not self._dragging and self._charge < 0:
            self._send_charge(_LEVEL_MOTION[self._value][2] * 0.45)

    def _send_charge(self, alpha: float) -> None:
        self._charge_alpha = alpha
        self._charge_anim.stop()
        self._charge_anim.setStartValue(0.0)
        self._charge_anim.setEndValue(1.0)
        self._charge_anim.start()

    def _pulse(self, level: int) -> None:
        """What landing on ``level`` looks like: the charge, the ring, the pace."""
        _reach, bounce, alpha, _step, ambient_ms = _LEVEL_MOTION[level]
        self._ring_level = level
        self._ring_anim.stop()
        self._ring_anim.setStartValue(0.0)
        self._ring_anim.setEndValue(1.0)
        self._ring_anim.start()

        self._swell.stop()
        self._swell.setDuration(320)
        self._swell.setStartValue(float(self._scale))
        self._swell.setKeyValueAt(0.4, float(bounce))
        self._swell.setEndValue(float(self._rest_scale()))
        self._swell.start()
        self._send_charge(alpha)
        self._ambient.stop()
        if ambient_ms:
            self._ambient.start(int(ambient_ms))
        top = level == len(EFFORTS) - 1
        self._heat_to(1.0 if top else 0.0)
        if top:
            self._arc_anim.stop()
            self._arc_anim.setStartValue(0.0)
            self._arc_anim.setEndValue(1.0)
            self._arc_anim.start()

    def _glide_to(self, index: int) -> None:
        self._anim.stop()
        self._anim.setStartValue(float(self._pos))
        self._anim.setEndValue(float(index))
        self._anim.start()

    def _swell_to(self, scale: float) -> None:
        self._swell.stop()
        self._swell.setDuration(140)
        self._swell.setKeyValues([])
        self._swell.setStartValue(float(self._scale))
        self._swell.setEndValue(float(scale))
        self._swell.start()

    def _rest_scale(self) -> float:
        return _HANDLE_GROW if self._dragging else (1.06 if self._hover else 1.0)

    def _set_preview(self, index: int) -> None:
        if index != self._preview:
            self._preview = index
            self.previewed.emit(EFFORTS[index])



    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._twinkle.start()
        ambient_ms = _LEVEL_MOTION[self._value][4]
        if ambient_ms:
            self._ambient.start(int(ambient_ms))

    def hideEvent(self, event):  # noqa: N802
        self.stop_motion()
        super().hideEvent(event)

    def stop_motion(self) -> None:
        """Every timer and animation off: the card is closing."""
        self._twinkle.stop()
        self._ambient.stop()
        self._charge_anim.stop()
        self._ring_anim.stop()
        self._arc_anim.stop()
        self._charge = -1.0
        self._ring = -1.0
        self._arc = -1.0

    def enterEvent(self, event):  # noqa: N802
        self._hover = True
        self._swell_to(self._rest_scale())
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._hover = False
        self._swell_to(self._rest_scale())
        super().leaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._dragging = True
        self._anim.stop()
        self._swell_to(self._rest_scale())
        self._pos = self._pos_at(event_pos(event).x())
        self._set_preview(int(round(self._pos)))
        self.update()

    def mouseMoveEvent(self, event):  # noqa: N802
        if not self._dragging:
            super().mouseMoveEvent(event)
            return
        self._pos = self._pos_at(event_pos(event).x())
        self._set_preview(int(round(self._pos)))
        self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or not self._dragging:
            super().mouseReleaseEvent(event)
            return
        self._dragging = False
        self._swell_to(self._rest_scale())
        self._commit(int(round(self._pos_at(event_pos(event).x()))))

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Down):
            self._commit(max(0, self._value - 1))
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Up):
            self._commit(min(len(EFFORTS) - 1, self._value + 1))
        elif key == Qt.Key.Key_Home:
            self._commit(0)
        elif key == Qt.Key.Key_End:
            self._commit(len(EFFORTS) - 1)
        else:
            super().keyPressEvent(event)

    def _commit(self, target: int) -> None:
        """A released or keyed stop is kept, paid or not: a free account sees the level it reached for, with the card saying what it takes to run it."""

        changed = target != self._value
        if target >= self._locked_from:
            self.locked_reached.emit(EFFORTS[target])
        self._value = target
        self._set_preview(target)
        self._glide_to(target)
        self._pulse(target)
        if changed:
            self.changed.emit(EFFORTS[target])



    def _draw_arc(self, painter: QPainter, track: QRectF, handle_x: float) -> None:
        """The electric arc of a landing on High: a jagged line from the left end of the trail to the handle, a soft accent stroke under a thin white."""




        t = self._arc
        fade = 1.0 if t < 0.66 else max(0.0, 1.0 - (t - 0.66) / 0.34)
        if fade <= 0.0:
            return
        left = track.left() + track.height() / 2
        right = handle_x - _HANDLE_PX / 2 + 2
        if right - left < 16:
            return


        head = left + (right - left) * min(1.0, t / 0.34)
        mid = track.center().y()
        amp = track.height() * 0.28
        n = max(6, int((head - left) / 9))
        points = [QPointF(left, mid)]
        for i in range(1, n):
            x = left + (head - left) * i / n
            jitter = math.sin(i * 12.9898 + t * 57.0) * math.cos(i * 4.1 + t * 23.0)
            points.append(QPointF(x, mid + amp * jitter))
        points.append(QPointF(head, mid))
        line = QPolygonF(points)
        soft = QColor(ACCENT).lighter(130)
        soft.setAlphaF(0.55 * fade)
        pen = QPen(soft, 4.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolyline(line)
        core = QColor(255, 255, 255)
        core.setAlphaF(0.92 * fade)
        pen = QPen(core, 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline(line)

    def paintEvent(self, event):  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        track = self._track()
        radius = _TRACK_PX / 2
        handle_x = self._stop_x(self._pos)
        centre = QPointF(handle_x, track.center().y())

        painter.setPen(QPen(qcolor(LINE), 1))
        painter.setBrush(qcolor(FIELD))
        painter.drawRoundedRect(track.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


        trail = QRectF(track.left(), track.top(), max(0.0, handle_x - track.left()), track.height())
        if trail.width() > 1:
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setClipRect(trail)
            gradient = QLinearGradient(QPointF(track.left(), 0), QPointF(handle_x, 0))
            gradient.setColorAt(0.0, QColor(ACCENT_DARK))
            lit = QColor(ACCENT)
            if self._heat > 0.0:
                hot = QColor(ACCENT).lighter(_HEAT_LIGHTER)
                lit = QColor(
                    int(lit.red() + (hot.red() - lit.red()) * self._heat),
                    int(lit.green() + (hot.green() - lit.green()) * self._heat),
                    int(lit.blue() + (hot.blue() - lit.blue()) * self._heat))
                gradient.setColorAt(0.55, QColor(ACCENT))
            gradient.setColorAt(1.0, lit)
            painter.setBrush(gradient)
            painter.drawRoundedRect(track, radius, radius)
            sheen = QLinearGradient(QPointF(0, track.top()), QPointF(0, track.bottom()))
            sheen.setColorAt(0.0, QColor(255, 255, 255, 46))
            sheen.setColorAt(0.5, QColor(255, 255, 255, 0))
            painter.setBrush(sheen)
            painter.drawRoundedRect(track, radius, radius)
            if self._charge >= 0.0 and trail.width() > 2 * radius:


                band = max(28.0, trail.width() * 0.22)
                cx = trail.left() - band / 2 + (trail.width() + band) * self._charge
                light = QLinearGradient(QPointF(cx - band / 2, 0), QPointF(cx + band / 2, 0))
                white = QColor(255, 255, 255, 0)
                light.setColorAt(0.0, white)
                white.setAlphaF(self._charge_alpha)
                light.setColorAt(0.5, white)
                white.setAlphaF(0.0)
                light.setColorAt(1.0, white)
                painter.setBrush(light)
                painter.drawRoundedRect(track, radius, radius)
            speck = QColor("#ffffff")
            for fx, fy, r, alpha, phase in _SPECKS:
                x = track.left() + radius + (track.width() - 2 * radius) * fx
                if x > handle_x - _HANDLE_PX / 2 - 2:
                    continue
                wave = math.sin(self._phase + phase)
                breath = 0.7 + 0.3 * wave


                flare = self._heat * max(0.0, wave - 0.9) * 6.0
                speck.setAlphaF(min(1.0, alpha * breath + flare))
                painter.setBrush(speck)
                painter.drawEllipse(QPointF(x, track.top() + track.height() * fy), r + flare * 0.8, r + flare * 0.8)
            if self._arc >= 0.0:
                self._draw_arc(painter, track, handle_x)
            painter.restore()


        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(len(EFFORTS)):
            x = self._stop_x(index)
            if x <= handle_x + 1:
                continue
            painter.setBrush(qcolor(INK_3 if index >= self._locked_from else INK_2))
            painter.drawEllipse(QPointF(x, track.center().y()), _STOP_PX / 2, _STOP_PX / 2)


        r = _HANDLE_PX / 2 * self._scale
        glow = (self._scale - 1.0) / (_HANDLE_GROW - 1.0)
        if self._heat > 0.01:



            breath = 0.5 + 0.5 * math.sin(self._phase * 0.6)
            halo = QRadialGradient(centre, r + _SLIDER_PAD)
            warm = QColor(ACCENT)
            warm.setAlphaF(self._heat * (0.10 + 0.16 * breath))
            halo.setColorAt(0.5, warm)
            warm.setAlphaF(0.0)
            halo.setColorAt(1.0, warm)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(centre, r + _SLIDER_PAD, r + _SLIDER_PAD)
        if glow > 0.01:
            halo = QRadialGradient(centre, r + _SLIDER_PAD)
            lit = QColor(ACCENT)
            lit.setAlphaF(0.34 * glow)
            halo.setColorAt(0.55, lit)
            lit.setAlphaF(0.0)
            halo.setColorAt(1.0, lit)
            painter.setBrush(halo)
            painter.drawEllipse(centre, r + _SLIDER_PAD, r + _SLIDER_PAD)






        painter.setBrush(QColor(0, 0, 0, 70 if DARK else 34))
        painter.drawEllipse(centre + QPointF(0, 1.5), r, r)
        painter.setBrush(QColor(0, 0, 0, 28 if DARK else 14))
        painter.drawEllipse(centre + QPointF(0, 3), r * 0.92, r * 0.92)
        painter.setPen(QPen(QColor(0, 0, 0, 36) if DARK else qcolor(INK_3), 1))
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(centre, r - 0.5, r - 0.5)
        if self._ring >= 0.0:


            reach = _LEVEL_MOTION[self._ring_level][0]
            ring = QColor(ACCENT)
            ring.setAlphaF(0.55 * (1.0 - self._ring))
            painter.setPen(QPen(ring, 2.2 - 1.4 * self._ring))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            rr = r + 2 + reach * self._ring
            painter.drawEllipse(centre, rr, rr)
        painter.end()


class EffortPopover(QFrame):
    """The card: the level's name, one sentence, the slider. Closes on a click outside."""

    changed = pyqtSignal(str)
    upgrade_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("effortPopover")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_POPOVER_QSS)
        self._texts = {effort: (name, note) for effort, name, note in effort_texts(self)}
        col = QVBoxLayout(self)
        col.setContentsMargins(_CARD_MARGIN, _CARD_MARGIN - 2, _CARD_MARGIN, _CARD_MARGIN)
        col.setSpacing(_CARD_SPACING)



        self._words = QWidget(self)
        words = QVBoxLayout(self._words)
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(_CARD_SPACING)
        self._fade = QGraphicsOpacityEffect(self._words)
        self._fade.setOpacity(1.0)
        self._words.setGraphicsEffect(self._fade)
        self._dip = QVariantAnimation(self)
        self._dip.setDuration(220)
        self._dip.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._dip.valueChanged.connect(self._on_dip)
        self._pending: tuple[str, str] | None = None
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self._title = QLabel(self)
        self._title.setObjectName("effortTitle")
        head.addWidget(self._title, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)



        self._cta = QPushButton(QCoreApplication.translate("EffortChip", "Upgrade to Pro"), self)
        self._cta.setObjectName("effortCta")
        self._cta.setFixedHeight(_CTA_PX)
        self._cta.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cta.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._cta.clicked.connect(self._on_upgrade)
        self._cta.hide()
        head.addWidget(self._cta, 0, Qt.AlignmentFlag.AlignVCenter)
        self._title.setFixedHeight(_CTA_PX)
        words.addLayout(head)
        self._note = QLabel(self)
        self._note.setObjectName("effortNote")
        self._note.setWordWrap(True)


        self._note.setFixedWidth(_TEXT_WIDTH)
        words.addWidget(self._note)
        col.addWidget(self._words)
        col.addSpacing(6)
        self.slider = EffortSlider(self)
        self.slider.setFixedWidth(_TEXT_WIDTH)
        self.slider.previewed.connect(self._show)
        self.slider.changed.connect(self._on_changed)
        self.slider.locked_reached.connect(self._show)
        col.addWidget(self.slider)
        self._paid = False
        self._show(DEFAULT_EFFORT)

    def set_current(self, effort: str) -> None:
        self.slider.set_value(effort)
        self._show(effort)

    def set_paid(self, paid: bool) -> None:
        """A paid account owns every stop; a free one sees the paid two named as Pro."""
        self._paid = bool(paid)
        self.slider.set_locked_from(len(pickable_efforts(self._paid)))
        self._show(EFFORTS[self.slider._preview])

    def fit_text(self) -> None:
        """Give the description the height its tallest wording really needs."""










        self._note.ensurePolished()
        metrics = self._note.fontMetrics()
        flags = int(Qt.TextFlag.TextWordWrap)
        tallest = 0
        for _name, note in self._texts.values():
            tallest = max(tallest, metrics.boundingRect(0, 0, _TEXT_WIDTH, 0, flags, note).height())
        if tallest:
            self._note.setFixedHeight(tallest)

    def _show(self, effort: str) -> None:
        name, note = self._texts.get(normalize_effort(effort), ("", ""))
        locked = self.slider.is_locked(effort)
        if self.isVisible() and (name != self._title.text() or note != self._note.text()):

            self._pending = (name, note, locked)
            if self._dip.state() != QVariantAnimation.State.Running:
                self._dip.setStartValue(0.0)
                self._dip.setEndValue(1.0)
                self._dip.start()
            return
        self._title.setText(name)
        self._note.setText(note)
        self._cta.setVisible(locked)

    def _on_dip(self, value) -> None:
        try:
            t = float(value)
        except (TypeError, ValueError):
            return
        if t >= 0.5 and self._pending is not None:
            name, note, locked = self._pending
            self._pending = None
            self._title.setText(name)
            self._note.setText(note)
            self._cta.setVisible(locked)

        self._fade.setOpacity(0.25 + 0.75 * abs(2 * t - 1))

    def _on_changed(self, effort: str) -> None:
        self._show(effort)
        self.changed.emit(effort)

    def _on_upgrade(self) -> None:
        self.hide()
        self.upgrade_requested.emit()

    def showEvent(self, event):  # noqa: N802 - Qt override
        super().showEvent(event)
        self.slider.setFocus(Qt.FocusReason.PopupFocusReason)

    def hideEvent(self, event):  # noqa: N802 - Qt override
        self.slider.stop_motion()
        self._dip.stop()
        self._pending = None
        self._fade.setOpacity(1.0)
        super().hideEvent(event)


class EffortChip(QToolButton):
    """``Effort`` + the level's name + a chevron, a small lock before it on a locked pick."""


    effort_changed = pyqtSignal(str)
    upgrade_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("effortChip")
        self.setStyleSheet(_CHIP_QSS)
        self.setProperty("active", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)


        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setToolTip(self.tr("How hard the agent works on the next message"))
        self.setAccessibleName(self.tr("Effort"))
        self._effort = DEFAULT_EFFORT
        self._paid = False


        self._compact = False
        self._popover: EffortPopover | None = None



        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.clicked.connect(self._open)
        self._paint()



    def effort(self) -> str:
        """What the next message runs on. A locked level answers Low."""
        return self._effort if self._effort in pickable_efforts(self._paid) else LOW

    def chosen(self) -> str:
        """Where the handle rests, locked or not: what the chip shows."""
        return self._effort

    def is_locked(self) -> bool:
        return self._effort not in pickable_efforts(self._paid)

    def set_effort(self, effort: str) -> None:
        """Show ``effort`` without emitting: the settings are the source of truth."""
        self._effort = normalize_effort(effort)
        self._paint()
        if self._popover is not None:
            self._popover.set_current(self._effort)

    def set_paid(self, paid: bool) -> None:
        """The session frame's plan: the paid stops are kept, or they carry Upgrade."""
        self._paid = bool(paid)
        if self._popover is not None:
            self._popover.set_paid(self._paid)
            self._popover.set_current(self._effort)
        self._paint()

    def is_paid(self) -> bool:
        return self._paid


    mode = effort
    set_mode = set_effort

    def _names(self) -> dict:
        return {effort: name for effort, name, _note in effort_texts(self)}

    def set_compact(self, compact: bool) -> None:
        """Drop the word ``Effort``, keeping the level, the lock and the chevron."""






        compact = bool(compact)
        if compact == self._compact:
            return
        self._compact = compact
        self._paint()

    def is_compact(self) -> bool:
        return self._compact

    def width_for(self, compact: bool) -> int:
        """The width this chip would ask for with or without the lead word."""




        plain, strong = self._fonts()
        word = "" if compact else QCoreApplication.translate("EffortChip", "Effort")
        name = self._names().get(self._effort, "")
        width = (QFontMetrics(strong).horizontalAdvance(name)
                 + _CHIP_GAP + _CHEVRON_SIZE + 6 + 8)
        if word:
            width += QFontMetrics(plain).horizontalAdvance(word + " ")
        if self.is_locked():
            width += _LOCK_PX + _CHIP_GAP
        return width

    def _label(self) -> tuple[str, str]:
        word = "" if self._compact else QCoreApplication.translate("EffortChip", "Effort")
        return word, self._names().get(self._effort, "")

    def _paint(self) -> None:
        """After the level or the plan changed: the tooltip, then the size."""



        if self.is_locked():
            self.setToolTip(self.tr("Pro only: this message runs on Low"))
        else:
            self.setToolTip(self.tr("How hard the agent works on the next message"))
        self.updateGeometry()
        self.update()

    def _fonts(self) -> tuple[QFont, QFont]:



        plain = QFont(self.font())
        plain.setWeight(QFont.Weight.Normal)
        strong = QFont(self.font())
        strong.setWeight(QFont.Weight.Medium)
        return plain, strong

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self.width_for(self._compact), BTN_SMALL_PX)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override



        return QSize(self.width_for(True), BTN_SMALL_PX)

    def paintEvent(self, event):  # noqa: N802 - Qt override
        super().paintEvent(event)
        plain, strong = self._fonts()
        word, name = self._label()
        lit = self.underMouse() or bool(self.property("active"))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        x = 8
        height = self.rect().height()



        if word:
            painter.setFont(plain)
            painter.setPen(qcolor(INK if lit else INK_2))
            painter.drawText(QRectF(x, 0, QFontMetrics(plain).horizontalAdvance(word + " ") + 2, height),
                             int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), word + " ")
            x += QFontMetrics(plain).horizontalAdvance(word + " ")
        painter.setFont(strong)
        painter.setPen(qcolor(INK))
        painter.drawText(QRectF(x, 0, QFontMetrics(strong).horizontalAdvance(name) + 2, height),
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), name)
        x += QFontMetrics(strong).horizontalAdvance(name) + _CHIP_GAP
        if self.is_locked():
            self._draw_lock(painter, x, height, qcolor(INK_3))
            x += _LOCK_PX + _CHIP_GAP
        glyph = pixmap_for(self, "chevron_down", _CHEVRON_SIZE)
        y = (height - glyph.height() / glyph.devicePixelRatio()) / 2
        painter.drawPixmap(QPointF(x, y), glyph)
        painter.end()

    @staticmethod
    def _draw_lock(painter: QPainter, x: float, height: float, colour: QColor) -> None:
        """A small padlock, the width of _LOCK_PX: a rounded body under an open arch."""
        top = (height - _LOCK_PX) / 2
        body = QRectF(x + 1, top + 4.5, _LOCK_PX - 2, _LOCK_PX - 4.5)
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawRoundedRect(body, 1.5, 1.5)
        pen = QPen(colour, 1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        arch = QRectF(x + 2.5, top + 0.5, _LOCK_PX - 5, _LOCK_PX - 4)
        painter.drawArc(arch, 0, 180 * 16)
        painter.restore()

    def changeEvent(self, event):  # noqa: N802 - Qt override
        super().changeEvent(event)


        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.FontChange):
            self.updateGeometry()
            self.update()



    def popover(self) -> EffortPopover:
        if self._popover is None:
            self._popover = EffortPopover(self)
            self._popover.changed.connect(self._on_changed)
            self._popover.upgrade_requested.connect(self.upgrade_requested.emit)
            self._popover.installEventFilter(self)
        self._popover.set_paid(self._paid)
        self._popover.set_current(self._effort)
        return self._popover

    def _open(self) -> None:
        popover = self.popover()
        popover.setFixedWidth(_POPOVER_WIDTH)



        popover.fit_text()
        popover.layout().activate()
        popover.adjustSize()
        above = self.mapToGlobal(QPoint(0, 0))
        self.setProperty("active", True)
        repolish(self)


        x = above.x() + self.width() - popover.width()
        y = above.y() - popover.height() - 6
        screen = QApplication.screenAt(above) if hasattr(QApplication, "screenAt") else None
        if screen is not None:
            geometry = screen.availableGeometry()
            x = max(geometry.left() + 4, min(x, geometry.right() - popover.width() - 4))
            if y < geometry.top():
                y = above.y() + self.height() + 6
        popover.move(x, y)
        popover.show()

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if watched is self._popover and event.type() == QEvent.Type.Hide:
            self.setProperty("active", False)
            repolish(self)
            self.update()
        return super().eventFilter(watched, event)

    def _on_changed(self, effort: str) -> None:
        effort = normalize_effort(effort)
        if effort == self._effort:
            return
        self._effort = effort
        self._paint()
        self.effort_changed.emit(effort)
