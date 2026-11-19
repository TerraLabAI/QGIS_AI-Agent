# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






























from __future__ import annotations

import math

from qgis.PyQt.QtCore import (
    QCoreApplication,
    QEasingCurve,
    QElapsedTimer,
    QEvent,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    pyqtSignal,
)
from qgis.PyQt.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPen, QRadialGradient
from qgis.PyQt.QtWidgets import (
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

from ..core.host_platform import prefers_reduced_motion
from ..core.numbers import as_float
from ..core.plan import effort_name, efforts_allowed, efforts_stated, efforts_trial_locked, efforts_trial_note
from ..core.served_state import get_effort_text, get_pro_offer, offer_text
from .cards_click import PressSurface
from .font_scale import scale_qss_font_px
from .icons import pixmap_for
from .permission_chip import select_qss
from .quota_card import get_pro_label, pro_price_per_month, trial_cta
from .shared import (
    event_pos,
    paint_styled_ground,
    place_by_box,
    round_popup_corners,
    settle_hover,
    show_popped,
)
from .style import (
    BTN_SMALL_PX,
    DARK,
    FIELD,
    FONT_BODY,
    FONT_HINT,
    FONT_MICRO,
    INK,
    INK_2,
    INK_3,
    LINE,
    LINE_STRONG,
    MOTION_POP_MS,
    OFFER_FILL,
    OFFER_FILL_HOVER,
    OFFER_FILL_PRESSED,
    ON_OFFER,
    RADIUS_CARD,
    SURFACE,
    WEIGHT_MEDIUM,
    qcolor,
    repolish,
)
from .widgets import ElidedLabel, paint_keyboard_ring

LOW, MEDIUM, HIGH = "low", "medium", "high"
EFFORTS = (LOW, MEDIUM, HIGH)







DEFAULT_EFFORT = LOW


FREE_EFFORTS = (LOW,)


def pickable_efforts(paid: bool) -> tuple:









    if efforts_stated():
        kept = tuple(e for e in EFFORTS if e in efforts_allowed())
        if kept:
            return kept
    return EFFORTS if paid else FREE_EFFORTS


def trial_locked(paid: bool) -> tuple:

    return tuple(e for e in EFFORTS if e in efforts_trial_locked()) if paid else ()


_CHIP_QSS = select_qss("effortChip")

_POPOVER_QSS = scale_qss_font_px(
    f"QFrame#effortPopover {{ background: {SURFACE};"
    f" border: 1px solid {LINE_STRONG}; border-radius: {RADIUS_CARD}px; }}"
    f"QLabel#effortNote {{ font-size: {FONT_BODY}px; color: {INK_2};"
    " background: transparent; border: none; }"
    f"QLabel#effortRunNote {{ font-size: {FONT_BODY}px; color: {INK_3};"
    " background: transparent; border: none; }"
    f"QLabel#effortPrice {{ font-size: {FONT_BODY}px; font-weight: {WEIGHT_MEDIUM}; color: {INK};"
    " background: transparent; border: none; }"


    f"QPushButton#effortCta {{ background: {OFFER_FILL}; color: {ON_OFFER}; border: none;"
    f" border-radius: 12px; padding: 0 10px; font-size: {FONT_MICRO + 1}px; font-weight: 600; }}"
    f"QPushButton#effortCta:hover {{ background: {OFFER_FILL_HOVER}; }}"
    f"QPushButton#effortCta:pressed {{ background: {OFFER_FILL_PRESSED}; }}"


    f"QLabel#effortHead {{ font-size: {FONT_HINT}px; font-weight: {WEIGHT_MEDIUM};"
    f" color: {INK_3}; background: transparent; border: none; }}"
    "QLabel { background: transparent; border: none; }"
)




_POPOVER_WIDTH = 320
_CARD_MARGIN = 16
_CARD_SPACING = 6


_TEXT_WIDTH = _POPOVER_WIDTH - 2 * _CARD_MARGIN - 2





_TRACK_PX = 18
_HANDLE_PX = 24
_HANDLE_GROW = 1.15
_STOP_PX = 3
_SLIDER_PAD = 8
_SLIDER_PX = int(_HANDLE_PX * _HANDLE_GROW) + 2 * _SLIDER_PAD
_CTA_PX = 24
_TWINKLE_MS = 16


_REV_BAND = ((0.0, "#3f9d5c"), (0.38, "#7fbd55"), (0.62, "#e6c84c"), (0.82, "#ee8d3f"), (1.0, "#e0503f"))


def rev_colour(at: float) -> QColor:

    at = max(0.0, min(1.0, at))
    for (a, low), (b, high) in zip(_REV_BAND, _REV_BAND[1:]):
        if at <= b:
            low, high, mix = QColor(low), QColor(high), (at - a) / (b - a)
            return QColor(*(round(x + (y - x) * mix) for x, y in zip(low.getRgb()[:3], high.getRgb()[:3])))
    return QColor(_REV_BAND[-1][1])






_LEVEL_MOTION = (


    (3, 1.10, 0.22, 0.05, 0),
    (5, 1.17, 0.32, 0.09, 0),
    (7, 1.24, 0.42, 0.13, 2600),
)
_CHARGE_MS = 520
_RING_MS = 420


_HEAT_MS = 360
_CHEVRON_SIZE = 12
_GAUGE_PX = 16

_NEEDLE_DEG = (155.0, 90.0, 25.0)
_DIAL_PEN = 1.7


def _between(stops: tuple, level: float) -> float:
    low = min(len(stops) - 2, int(level))
    return stops[low] + (stops[low + 1] - stops[low]) * (level - low)


def paint_dial(painter: QPainter, box: QRectF, level: float, ink: QColor, faint: QColor) -> None:


    level = max(0.0, min(2.0, float(level)))
    scale = min(box.width(), box.height()) / 16.0
    radius = 6.4 * scale
    centre = QPointF(box.center().x(), box.top() + 9.4 * scale)
    arc = QRectF(centre.x() - radius, centre.y() - radius, 2 * radius, 2 * radius)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    pen = QPen(faint, _DIAL_PEN * scale)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.drawArc(arc, 210 * 16, -240 * 16)
    pen.setColor(ink)
    painter.setPen(pen)
    painter.drawArc(arc, 210 * 16, -int(_between((60.0, 120.0, 240.0), level) * 16))
    turn = math.radians(_between(_NEEDLE_DEG, level))
    painter.drawLine(centre, QPointF(centre.x() + math.cos(turn) * radius * 0.66,
                                     centre.y() - math.sin(turn) * radius * 0.66))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(ink)
    painter.drawEllipse(centre, 1.5 * scale, 1.5 * scale)
    painter.restore()


_LOCK_PX = 10
_CHIP_GAP = 4


def normalize_effort(effort: str) -> str:


    return effort_name(effort) or DEFAULT_EFFORT


def effort_texts(widget: QWidget | None = None) -> tuple:












    shipped = (
        (LOW, "Low",
         QCoreApplication.translate(
             "EffortChip",
             "Quick answers and small edits.")),
        (MEDIUM, "Medium",
         QCoreApplication.translate(
             "EffortChip", "Everyday tasks, done with care.")),
        (HIGH, "High",
         QCoreApplication.translate(
             "EffortChip", "The biggest jobs, with more time.")),
    )





    return tuple((level, *get_effort_text(level, name, note)) for level, name, note in shipped)


def _index(effort: str) -> int:
    return EFFORTS.index(normalize_effort(effort))


class EffortSlider(QWidget):

















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

        self.keyed = False
        self._hover = False
        self._scale = 1.0
        self._phase = 0.0
        self._beat = 0.0
        self._clock = QElapsedTimer()
        self._anim = QVariantAnimation(self)

        self._anim.setDuration(MOTION_POP_MS + 120)
        back = QEasingCurve(QEasingCurve.Type.OutBack)
        back.setOvershoot(1.2)
        self._anim.setEasingCurve(back)
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


        self._heat = 1.0 if self._value == len(EFFORTS) - 1 else 0.0
        self._heat_anim = QVariantAnimation(self)
        self._heat_anim.setDuration(_HEAT_MS)
        self._heat_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._heat_anim.valueChanged.connect(self._on_heat)

    def value(self) -> str:
        return EFFORTS[self._value]

    def set_value(self, effort: str, animate: bool = False) -> None:



        if _index(effort) == self._value and not self._dragging:
            return
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

        self._locked_from = max(0, min(int(index), len(EFFORTS)))
        self.update()

    def is_locked(self, effort: str) -> bool:
        return _index(effort) >= self._locked_from

    def _track(self) -> QRectF:
        top = (self.height() - _TRACK_PX) / 2
        return QRectF(_SLIDER_PAD, top, self.width() - 2 * _SLIDER_PAD, _TRACK_PX)

    def _span(self) -> tuple[float, float]:

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

        dt = min(0.05, self._clock.restart() / 1000.0) if self._clock.isValid() else 0.0
        if not self._clock.isValid():
            self._clock.start()
        self._phase = (self._phase + _LEVEL_MOTION[self._value][3] * 0.8 * dt * 30.0) % (2 * math.pi)
        self._beat += dt
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
        if not prefers_reduced_motion():
            self._clock.invalidate()
            self._twinkle.start()
            if self._value:
                self._pos = 0.0
                self._glide_to(self._value)
        ambient_ms = _LEVEL_MOTION[self._value][4]
        if ambient_ms:
            self._ambient.start(int(ambient_ms))

    def hideEvent(self, event):  # noqa: N802
        self.stop_motion()
        super().hideEvent(event)

    def stop_motion(self) -> None:

        self._twinkle.stop()
        self._ambient.stop()
        self._charge_anim.stop()
        self._ring_anim.stop()
        self._charge = -1.0
        self._ring = -1.0

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
        self.keyed = False
        self._anim.stop()
        self._swell_to(self._rest_scale())
        self._pos = self._caught(self._pos_at(event_pos(event).x()))
        self._set_preview(int(round(self._pos)))
        self.update()

    @staticmethod
    def _caught(pos: float) -> float:


        stop = round(pos)
        return stop + (pos - stop) * 0.35 if abs(pos - stop) < 0.2 else pos

    def mouseMoveEvent(self, event):  # noqa: N802
        if not self._dragging:
            super().mouseMoveEvent(event)
            return
        self._pos = self._caught(self._pos_at(event_pos(event).x()))
        self._set_preview(int(round(self._pos)))
        self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or not self._dragging:
            super().mouseReleaseEvent(event)
            return
        self._dragging = False
        self._swell_to(self._rest_scale())
        self._commit(int(round(self._pos_at(event_pos(event).x()))))

    def keyPressEvent(self, event):  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Down):
            self._commit(max(0, self._value - 1), glide=False)
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Up):
            self._commit(min(len(EFFORTS) - 1, self._value + 1), glide=False)
        elif key == Qt.Key.Key_Home:
            self._commit(0, glide=False)
        elif key == Qt.Key.Key_End:
            self._commit(len(EFFORTS) - 1, glide=False)
        else:
            super().keyPressEvent(event)

    def _commit(self, target: int, glide: bool = True) -> None:






        changed = target != self._value
        if target >= self._locked_from:
            self.locked_reached.emit(EFFORTS[target])
        self._value = target
        self.keyed = not glide
        self._set_preview(target)
        if glide and not prefers_reduced_motion():
            self._glide_to(target)
        else:
            self._anim.stop()
            self._pos = float(target)
            self.update()
        if glide:
            self._pulse(target)
        else:
            self._heat_anim.stop()
            self._heat = 1.0 if target == len(EFFORTS) - 1 else 0.0

            self._ambient.stop()
            ambient_ms = _LEVEL_MOTION[target][4]
            if ambient_ms:
                self._ambient.start(int(ambient_ms))
        if changed:
            self.changed.emit(EFFORTS[target])
        self.keyed = False

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        track = self._track()
        radius = _TRACK_PX / 2
        handle_x = self._stop_x(max(-0.03, min(len(EFFORTS) - 1 + 0.03, self._pos)))
        centre = QPointF(handle_x, track.center().y())
        painter.setPen(QPen(qcolor(LINE), 1))
        painter.setBrush(qcolor(FIELD))
        painter.drawRoundedRect(track.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


        trail = QRectF(track.left(), track.top(), max(0.0, handle_x - track.left()), track.height())
        if trail.width() > radius:
            band = QLinearGradient(QPointF(track.left(), 0), QPointF(track.right(), 0))
            push = max(0.0, min(2.0, self._pos)) / 2.0
            for at, colour in _REV_BAND:
                tint = QColor(colour)
                tint.setAlphaF(0.035 + 0.045 * push)
                band.setColorAt(at, tint)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(band)
            for spread in (1.0, 2.5, 4.0, 5.5):
                under = trail.adjusted(0, spread * 0.6, 0, spread * 1.2)
                painter.drawRoundedRect(under, radius + spread, radius + spread)
        if trail.width() > 1:
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setClipRect(trail)
            gradient = QLinearGradient(QPointF(track.left(), 0), QPointF(track.right(), 0))
            for at, colour in _REV_BAND:
                gradient.setColorAt(at, QColor(colour))
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
            painter.setPen(Qt.PenStyle.NoPen)
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
        here = rev_colour((handle_x - track.left()) / max(1.0, track.width()))
        rev = max(0.0, min(2.0, self._pos)) / 2.0
        needle = self._pos
        if self._twinkle.isActive() and rev > 0.05:


            pace = 34.0 + 46.0 * rev
            reach = 1.05 * rev ** 1.5
            centre = centre + QPointF(reach * 0.55 * math.sin(self._beat * pace * 1.31 + 1.7),
                                      reach * math.sin(self._beat * pace))
            needle += 0.05 * rev * math.sin(self._beat * pace * 0.83)
        if self._heat > 0.01:



            breath = 0.5 + 0.5 * math.sin(self._phase * 0.6)
            halo = QRadialGradient(centre, r + _SLIDER_PAD)
            warm = QColor(here)
            warm.setAlphaF(self._heat * (0.10 + 0.16 * breath))
            halo.setColorAt(0.5, warm)
            warm.setAlphaF(0.0)
            halo.setColorAt(1.0, warm)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(centre, r + _SLIDER_PAD, r + _SLIDER_PAD)
        if glow > 0.01:
            halo = QRadialGradient(centre, r + _SLIDER_PAD)
            lit = QColor(here)
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
        face = r * 1.18
        paint_dial(painter, QRectF(centre.x() - face / 2, centre.y() - face / 2, face, face),
                   needle, QColor("#1b1c1f"), QColor(0, 0, 0, 38))
        if self._ring >= 0.0:


            reach = _LEVEL_MOTION[self._ring_level][0]
            ring = QColor(here)
            ring.setAlphaF(0.55 * (1.0 - self._ring))
            painter.setPen(QPen(ring, 2.2 - 1.4 * self._ring))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            rr = r + 2 + reach * self._ring
            painter.drawEllipse(centre, rr, rr)
        painter.end()


class _StopNames(QWidget):











    def __init__(self, slider: EffortSlider, names: list, parent=None):
        super().__init__(parent)
        self._slider = slider
        self._names = list(names)
        self.trial: tuple = ()
        font = QFont(self.font())
        font.setPixelSize(max(9, FONT_MICRO))
        self.setFont(font)
        self.setFixedHeight(QFontMetrics(font).height() + 2)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stops = []
        for index, name in enumerate(self._names):
            stop = PressSurface(self)
            stop.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            stop.setAccessibleName(self.tr("Effort: {level}").format(level=name))
            stop.clicked.connect(lambda _c=False, i=index: self._slider._commit(i))
            self._stops.append(stop)

    def _place_stops(self, metrics: QFontMetrics) -> None:


        for index, (stop, name) in enumerate(zip(self._stops, self._names)):
            width = metrics.horizontalAdvance(name) + 8
            x = int(round(max(0.0, min(float(self.width() - width), self._slider._stop_x(index) - width / 2))))
            if stop.geometry() != QRect(x, 0, width, self.height()):
                stop.setGeometry(x, 0, width, self.height())
            if EFFORTS[index] in self.trial:
                state = self.tr("Locked until the end of the free trial")
            elif index >= self._slider._locked_from:
                state = self.tr("Locked: {tag}").format(tag=get_pro_label())
            elif index == self._slider._value:
                state = self.tr("Selected")
            else:
                state = ""
            if stop.accessibleDescription() != state:
                stop.setAccessibleDescription(state)

    def mousePressEvent(self, event):  # noqa: N802

        if event.button() == Qt.MouseButton.LeftButton:
            x = event_pos(event).x()
            self._slider._commit(min(range(len(self._names)), key=lambda i: abs(self._slider._stop_x(i) - x)))

    def paintEvent(self, event):  # noqa: N802
        self._place_stops(QFontMetrics(self.font()))
        painter = QPainter(self)
        try:
            plain = QFont(self.font())
            strong = QFont(self.font())
            strong.setWeight(QFont.Weight.DemiBold)
            for index, name in enumerate(self._names):

                here = index == self._slider._preview
                painter.setFont(strong if here else plain)
                metrics = QFontMetrics(strong if here else plain)
                width = metrics.horizontalAdvance(name)
                x = self._slider._stop_x(index) - width / 2
                x = max(0.0, min(float(self.width() - width), x))
                locked = index >= self._slider._locked_from
                painter.setPen(qcolor(INK if here and not locked else INK_3 if locked else INK_2))
                painter.drawText(int(round(x)), metrics.ascent() + 1, name)
                if EFFORTS[index] in self.trial:
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    EffortChip._draw_lock(painter, x - _LOCK_PX - 3, self.height(), qcolor(INK_3))
        finally:
            painter.end()


class EffortPopover(QFrame):


    changed = pyqtSignal(str)
    upgrade_requested = pyqtSignal()
    upsell_shown = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        round_popup_corners(self)
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


        self._fade.setEnabled(False)
        self._words.setGraphicsEffect(self._fade)
        self._dip = QVariantAnimation(self)
        self._dip.setDuration(220)
        self._dip.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._dip.valueChanged.connect(self._on_dip)
        self._pending: tuple[str, str] | None = None


        self._name = ""
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self._head = QLabel(QCoreApplication.translate("EffortChip", "Effort"), self)
        self._head.setObjectName("effortHead")
        self._head.setFixedHeight(_CTA_PX)
        head.addWidget(self._head, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)



        self._cta = QPushButton(get_pro_label(), self)
        self._cta.setObjectName("effortCta")
        self._cta.setFixedHeight(_CTA_PX)
        self._cta.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cta.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._cta.clicked.connect(self._on_upgrade)
        self._cta.hide()
        head.addWidget(self._cta, 0, Qt.AlignmentFlag.AlignVCenter)
        words.addLayout(head)
        self._note = QLabel(self)
        self._note.setObjectName("effortNote")
        self._note.setWordWrap(True)


        self._note.setFixedWidth(_TEXT_WIDTH)
        words.addWidget(self._note)





        self._price = ElidedLabel("", self)
        self._price.setObjectName("effortPrice")
        self._price.setFixedWidth(_TEXT_WIDTH)
        self._price.hide()
        words.addWidget(self._price)
        col.addWidget(self._words)
        col.addSpacing(2)
        self.slider = EffortSlider(self)
        self.slider.setFixedWidth(_TEXT_WIDTH)
        self.slider.previewed.connect(self._show)
        self.slider.changed.connect(self._on_changed)
        self.slider.locked_reached.connect(self._show)
        col.addWidget(self.slider)
        self._stops = _StopNames(self.slider, [self._texts.get(e, ("", ""))[0] for e in EFFORTS], self)
        self._stops.setFixedWidth(_TEXT_WIDTH)
        col.addWidget(self._stops)

        self._run_note = ElidedLabel(QCoreApplication.translate("EffortChip", "Applies from your next message."), self)
        self._run_note.setObjectName("effortRunNote")
        self._run_note.setFixedWidth(_TEXT_WIDTH)
        self._run_note.hide()
        col.addWidget(self._run_note)
        self._live: tuple = ()
        self._paid = False
        self._trial: tuple = ()
        self._show(DEFAULT_EFFORT)

    def set_current(self, effort: str) -> None:
        self.slider.set_value(effort)
        self._show(effort)

    def set_running(self, running: bool) -> None:
        self._run_note.setVisible(bool(running))
        self.adjustSize()

    def set_live(self, efforts) -> None:
        self._live = tuple(efforts or ())
        self._sync_run_note(EFFORTS[self.slider._preview])

    def _sync_run_note(self, effort: str) -> None:

        live = normalize_effort(effort) in self._live
        self._run_note.setText(QCoreApplication.translate("EffortChip", "Applies from the next step.") if live
                               else QCoreApplication.translate("EffortChip", "Applies from your next message."))

    def set_paid(self, paid: bool) -> None:

        self._paid = bool(paid)
        self._trial = trial_locked(self._paid)
        locked_from = len(pickable_efforts(self._paid))
        if self._trial:
            locked_from = min(locked_from, EFFORTS.index(self._trial[0]))
        self.slider.set_locked_from(locked_from)

        self._cta.setText(trial_cta() if get_pro_offer() == "trial" else get_pro_label())
        self._stops.trial = self._trial
        self._stops.update()
        self._price.setVisible(self.slider.is_locked(HIGH))
        self._show(EFFORTS[self.slider._preview])

    def fit_text(self) -> None:











        self._note.ensurePolished()
        metrics = self._note.fontMetrics()
        flags = int(Qt.TextFlag.TextWordWrap)
        tallest = 0
        for _name, note in self._texts.values():
            tallest = max(tallest, metrics.boundingRect(0, 0, _TEXT_WIDTH, 0, flags, note).height())
        if tallest:
            self._note.setFixedHeight(tallest)
        self._price.ensurePolished()
        self._price.setFixedHeight(self._price.fontMetrics().height())

    def _show(self, effort: str) -> None:
        self._sync_run_note(effort)
        name, note = self._texts.get(normalize_effort(effort), ("", ""))
        keyed = self.slider.keyed
        self.slider.keyed = False
        if keyed:
            self._dip.stop()
            self._pending = None
            self._fade.setOpacity(1.0)
            self._fade.setEnabled(False)
        elif self.isVisible() and (name != self._name or note != self._note.text()):
            self._pending = (name, note, effort)
            if self._dip.state() != QVariantAnimation.State.Running:
                self._dip.setStartValue(0.0)
                self._dip.setEndValue(1.0)
                self._fade.setEnabled(True)
                self._dip.start()
            return
        self._set_words(name, note, effort, self.isVisible())

    def _set_words(self, name: str, note: str, effort: str, visible: bool) -> None:

        trial = normalize_effort(effort) in self._trial
        upsell = self.slider.is_locked(effort) and not trial
        self._name = name
        self._note.setText(note)
        self._stops.update()
        self._cta.setVisible(upsell)
        self._price.setText(self._price_text() if upsell else self._trial_text() if trial else "")
        if upsell and visible:
            self.upsell_shown.emit()

    @staticmethod
    def _trial_text() -> str:
        return efforts_trial_note() or QCoreApplication.translate(
            "EffortChip", "Available after your free trial, if you keep Pro.")

    @staticmethod
    def _price_text() -> str:
        price = pro_price_per_month()
        if price:
            return offer_text("effort.price", QCoreApplication.translate("EffortChip", "Pro · {price}"), price=price)
        return offer_text("effort.included", QCoreApplication.translate("EffortChip", "Included in Pro"))

    def _on_dip(self, value) -> None:
        t = as_float(value)
        if t is None:
            return
        if t >= 0.5 and self._pending is not None:
            name, note, effort = self._pending
            self._pending = None
            self._set_words(name, note, effort, True)

        self._fade.setOpacity(0.25 + 0.75 * abs(2 * t - 1))
        if t >= 1.0:
            self._fade.setEnabled(False)

    def _on_changed(self, effort: str) -> None:
        self._show(effort)
        if normalize_effort(effort) in self._trial:

            self.slider.set_value(MEDIUM, animate=True)
            effort = MEDIUM
        self.changed.emit(effort)

    def _on_upgrade(self) -> None:
        self.hide()
        self.upgrade_requested.emit()

    def paintEvent(self, event):  # noqa: N802
        paint_styled_ground(self)
        super().paintEvent(event)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self.slider.setFocus(Qt.FocusReason.PopupFocusReason)
        if not self._cta.isHidden():
            self.upsell_shown.emit()

    def hideEvent(self, event):  # noqa: N802
        self.slider.stop_motion()
        self._dip.stop()
        self._pending = None
        self._fade.setOpacity(1.0)
        self._fade.setEnabled(False)
        super().hideEvent(event)


class EffortChip(QToolButton):



    effort_changed = pyqtSignal(str)
    upgrade_requested = pyqtSignal()
    upsell_shown = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("effortChip")
        self.setStyleSheet(_CHIP_QSS)
        self.setProperty("active", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)


        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setToolTip(self.tr("How hard the AI agent works on the next message"))
        self.setAccessibleName(self.tr("Effort"))
        self._effort = DEFAULT_EFFORT
        self._paid = False
        self._running = False
        self._live: tuple = ()
        self._compact = False
        self._popover: EffortPopover | None = None

        self._needle = float(EFFORTS.index(self._effort))
        self._glide = QVariantAnimation(self)
        self._glide.setDuration(280)
        self._glide.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._glide.valueChanged.connect(self._on_glide)



        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.clicked.connect(self._open)
        self._paint()

    def effort(self) -> str:

        if self._effort in trial_locked(self._paid):
            return MEDIUM
        return self._effort if self._effort in pickable_efforts(self._paid) else LOW

    def chosen(self) -> str:

        return self._effort

    def is_locked(self) -> bool:
        return self._effort not in pickable_efforts(self._paid)

    def set_effort(self, effort: str) -> None:

        self._effort = normalize_effort(effort)
        self._turn_needle()
        self._paint()
        if self._popover is not None:
            self._popover.set_current(self._effort)

    def set_paid(self, paid: bool) -> None:

        self._paid = bool(paid)
        if self._popover is not None:
            self._popover.set_paid(self._paid)
            self._popover.set_current(self._effort)
        self._paint()

    def set_running(self, running: bool) -> None:


        running = bool(running)
        if running == self._running:
            return
        self._running = running
        if self._popover is not None:
            self._popover.set_running(running)
        self._paint()

    def is_running(self) -> bool:
        return self._running

    def set_live(self, efforts) -> None:

        self._live = tuple(efforts or ())
        if self._popover is not None:
            self._popover.set_live(self._live)


    mode = effort
    set_mode = set_effort

    def _names(self) -> dict:
        return {effort: name for effort, name, _note in effort_texts(self)}

    def set_compact(self, compact: bool) -> None:






        compact = bool(compact)
        if compact == self._compact:
            return
        self._compact = compact
        self._paint()

    def width_for(self, compact: bool) -> int:





        lock = _LOCK_PX + _CHIP_GAP if self.is_locked() else 0
        if compact:
            return BTN_SMALL_PX + lock
        name = self._names().get(self._effort, "")
        return (6 + _GAUGE_PX + _CHIP_GAP + QFontMetrics(self._strong_font()).horizontalAdvance(name)
                + _CHIP_GAP + lock + _CHEVRON_SIZE + 6)

    def _paint(self) -> None:




        if self._running:
            self.setToolTip(self.tr("Applies from your next message."))
        elif self.is_locked():


            name = self._names().get(self._effort, "")
            self.setToolTip(self.tr("{level} needs Pro. Pick Low to send.").format(level=name))
        elif self._compact:
            self.setToolTip(self.tr("Effort {level}: how hard the AI agent works on the next message")
                            .format(level=self._names().get(self._effort, "")))
        else:
            self.setToolTip(self.tr("How hard the AI agent works on the next message"))
        self.setAccessibleDescription(self._names().get(self._effort, ""))
        self.updateGeometry()
        self.update()

    def _strong_font(self) -> QFont:



        self.ensurePolished()
        return QFont(self.font())

    def _turn_needle(self) -> None:

        target = float(EFFORTS.index(self._effort)) if self._effort in EFFORTS else 0.0
        self._glide.stop()
        if target == self._needle or not self.isVisible() or prefers_reduced_motion():
            self._needle = target
            self.update()
            return
        self._glide.setStartValue(self._needle)
        self._glide.setEndValue(target)
        self._glide.start()

    def _on_glide(self, value) -> None:
        self._needle = float(value)
        self.update()

    def _draw_gauge(self, painter: QPainter, x: float, height: float, lit: bool) -> None:
        faint = QColor(qcolor(INK_3))
        faint.setAlphaF(0.5)
        paint_dial(painter, QRectF(x, (height - _GAUGE_PX) / 2, _GAUGE_PX, _GAUGE_PX), self._needle,
                   QColor(qcolor(INK if lit else INK_2)), faint)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.width_for(self._compact), BTN_SMALL_PX)

    def minimumSizeHint(self) -> QSize:  # noqa: N802



        return QSize(self.width_for(True), BTN_SMALL_PX)

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        strong = self._strong_font()
        name = self._names().get(self._effort, "")
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        height = self.rect().height()
        lit = self.underMouse() or bool(self.property("active"))
        x = 6.0
        self._draw_gauge(painter, x, height, lit)
        x += _GAUGE_PX + _CHIP_GAP
        if not self._compact:
            painter.setFont(strong)
            painter.setPen(qcolor(INK if lit else INK_2))
            painter.drawText(QRectF(x, 0, QFontMetrics(strong).horizontalAdvance(name) + 2, height),
                             int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), name)
            x += QFontMetrics(strong).horizontalAdvance(name) + _CHIP_GAP
        if self.is_locked():
            self._draw_lock(painter, x, height, qcolor(INK_3))
            x += _LOCK_PX + _CHIP_GAP
        if not self._compact:
            glyph = pixmap_for(self, "chevron_down", _CHEVRON_SIZE)
            y = (height - glyph.height() / glyph.devicePixelRatio()) / 2
            painter.drawPixmap(QPointF(x, y), glyph)
        painter.end()
        paint_keyboard_ring(self)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            return
        super().keyPressEvent(event)

    @staticmethod
    def _draw_lock(painter: QPainter, x: float, height: float, colour: QColor) -> None:

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

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)


        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.FontChange):
            self.updateGeometry()
            self.update()

    def popover(self) -> EffortPopover:
        if self._popover is None:
            self._popover = EffortPopover(self)
            self._popover.changed.connect(self._on_changed)
            self._popover.upgrade_requested.connect(self.upgrade_requested.emit)
            self._popover.upsell_shown.connect(self.upsell_shown.emit)
            self._popover.installEventFilter(self)
        self._popover.set_paid(self._paid)
        self._popover.set_current(self._effort)
        self._popover.set_running(self._running)
        self._popover.set_live(self._live)
        return self._popover

    def _open(self) -> None:
        popover = self.popover()
        popover.setFixedWidth(_POPOVER_WIDTH)



        popover.fit_text()
        popover.layout().activate()
        popover.adjustSize()
        self.setProperty("active", True)
        repolish(self)
        show_popped(popover, place_by_box(popover, self.parentWidget() or self, right=True))

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self._popover and event.type() == QEvent.Type.Hide:
            self.setProperty("active", False)
            settle_hover(self)
        return super().eventFilter(watched, event)

    def _on_changed(self, effort: str) -> None:
        effort = normalize_effort(effort)
        if effort == self._effort:
            return
        self._effort = effort
        self._turn_needle()
        self._paint()
        self.effort_changed.emit(effort)
