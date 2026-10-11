# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later















from __future__ import annotations

import math
import time

from qgis.PyQt.QtCore import QRectF, QSize, Qt, QTimer
from qgis.PyQt.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from .font_scale import scale_point_size
from .style import FONT_BODY, INK, INK_2, INK_3, qcolor


GRID_PX = 15
_DOT_PX = 4.0
_DOT_GAP = 1.5
_DOT_RADIUS = 1.0

_CYCLE_S = 0.65
_REST = 0.15

_DELAYS = ((0.09, 0.18, 0.27), (0.0, 0.09, 0.18), (0.09, 0.18, 0.27))

_SHIMMER_S = 1.4

_TICK_MS = 40

_CLOCK_MS = 100

LINE_PX = 20
GAP_PX = 8


def _ease(t: float) -> float:

    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def pixel_on(phase: float) -> float:

    phase %= 1.0
    if phase < 0.18:
        return _REST + (1.0 - _REST) * _ease(phase / 0.18)
    if phase < 0.42:
        return 1.0
    if phase < 0.62:
        return 1.0 - (1.0 - _REST) * _ease((phase - 0.42) / 0.20)
    return _REST


def format_elapsed(seconds: float) -> str:

    try:
        seconds = float(seconds)
    except (TypeError, ValueError, OverflowError):
        seconds = 0.0
    if math.isnan(seconds) or math.isinf(seconds):
        seconds = 0.0
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    return f"{minutes}m {seconds - minutes * 60:.1f}s"


class _Ticker(QWidget):



    def __init__(self, interval_ms: int, parent=None):
        super().__init__(parent)
        self._wanted = False
        self._motion = True



        self._clock = time.perf_counter()
        self._timer = QTimer(self)
        self._timer.setInterval(max(16, min(int(interval_ms), 60_000)))
        self._timer.timeout.connect(self.update)

    def start(self) -> None:
        self._wanted = True
        self._sync()

    def stop(self) -> None:
        self._wanted = False
        self._timer.stop()
        self.update()

    def set_motion(self, enabled: bool) -> None:

        self._motion = bool(enabled)
        self._sync()

    def _sync(self) -> None:
        if self._wanted and self._motion and self.isVisible():
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._sync()

    def hideEvent(self, event):  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def _elapsed(self) -> float:
        return time.perf_counter() - self._clock


class DotsLoader(_Ticker):


    def __init__(self, parent=None):
        super().__init__(_TICK_MS, parent)
        self._ink = qcolor(INK_3)
        self.setFixedSize(GRID_PX, GRID_PX)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def _opacity(self, row: int, col: int) -> float:
        if not self._timer.isActive():


            return 1.0 if (row, col) == (1, 1) else _REST
        phase = (self._elapsed() - _DELAYS[row][col]) / _CYCLE_S
        return pixel_on(phase)

    def paintEvent(self, event):  # noqa: N802


        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            step = _DOT_PX + _DOT_GAP
            for row in range(3):
                for col in range(3):
                    colour = QColor(self._ink)
                    colour.setAlphaF(self._opacity(row, col))
                    painter.setBrush(colour)
                    painter.drawRoundedRect(
                        QRectF(col * step, row * step, _DOT_PX, _DOT_PX), _DOT_RADIUS, _DOT_RADIUS)
            painter.end()
        except Exception:  # noqa: BLE001
            return


class ShimmerLabel(_Ticker):



    def __init__(self, text: str = "", parent=None):
        super().__init__(_TICK_MS, parent)
        self._text = text or ""
        self._base = qcolor(INK_2)
        self._light = qcolor(INK)
        font = QFont(self.font())
        font.setPixelSize(scale_point_size(FONT_BODY))
        font.setWeight(QFont.Weight.Medium)
        self.setFont(font)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(LINE_PX)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def setText(self, text: str) -> None:  # noqa: N802
        self._text = text or ""
        self.setToolTip(self._text if len(self._text) > 60 else "")
        self.updateGeometry()
        self.update()

    def text(self) -> str:
        return self._text

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.fontMetrics().horizontalAdvance(self._text) + 2, LINE_PX)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(24, LINE_PX)

    def _brush(self, width: int):
        if not self._timer.isActive() or width <= 0:
            return self._base


        centre = (1.5 - 2.0 * ((self._elapsed() / _SHIMMER_S) % 1.0)) * width
        band = max(24.0, width / 3.0)
        gradient = QLinearGradient(centre - band, 0, centre + band, 0)
        gradient.setColorAt(0.0, self._base)
        gradient.setColorAt(0.5, self._light)
        gradient.setColorAt(1.0, self._base)
        return gradient

    def paintEvent(self, event):  # noqa: N802
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = self.contentsRect()
            metrics = self.fontMetrics()
            text = metrics.elidedText(self._text, Qt.TextElideMode.ElideRight, rect.width())
            width = metrics.horizontalAdvance(text)
            pen = QPen()
            pen.setBrush(self._brush(width))
            painter.setPen(pen)
            painter.setFont(self.font())
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)
            painter.end()
        except Exception:  # noqa: BLE001
            return





_USER_WAITS: list = []
_USER_WAIT_SINCE: list = []


def user_waiting(waiting: bool) -> None:


    now = time.monotonic()
    if waiting and not _USER_WAIT_SINCE:
        _USER_WAIT_SINCE.append(now)
    elif not waiting and _USER_WAIT_SINCE:
        _USER_WAITS.append((_USER_WAIT_SINCE.pop(), now))
        del _USER_WAITS[:-200]


def waited_between(start: float, end: float | None = None) -> float:

    end = time.monotonic() if end is None else float(end)
    spans = list(_USER_WAITS) + [(_USER_WAIT_SINCE[0], end)] if _USER_WAIT_SINCE else list(_USER_WAITS)
    return sum(max(0.0, min(b, end) - max(a, start)) for a, b in spans)


class ElapsedClock(QWidget):



    def __init__(self, started: float | None = None, parent=None):
        super().__init__(parent)
        self._started = float(started) if started is not None else time.monotonic()
        self._label = _mono_label(self)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._label)
        self._timer = QTimer(self)
        self._timer.setInterval(_CLOCK_MS)
        self._timer.timeout.connect(self._tick)
        self._tick()

    def set_started(self, started: float) -> None:
        self._started = float(started)
        self._tick()

    def elapsed(self) -> float:
        now = time.monotonic()
        return max(0.0, now - self._started - waited_between(self._started, now))

    def _tick(self) -> None:
        self._label.setText(format_elapsed(self.elapsed()))

    def hideEvent(self, event):  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def stop(self) -> None:
        self._timer.stop()

    def freeze(self, seconds=None) -> None:


        self._timer.stop()
        try:
            value = float(seconds) if seconds is not None else self.elapsed()
        except (TypeError, ValueError):
            value = self.elapsed()
        self._frozen = value
        self._label.setText(format_elapsed(value))

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if getattr(self, "_frozen", None) is not None:
            return
        self._tick()
        self._timer.start()


def _mono_label(parent):

    label = QLabel(parent)
    label.setObjectName("statusTime")
    font = QFont(label.font())

    font.setStyleHint(QFont.StyleHint.Monospace)
    label.setFont(font)
    return label


__all__ = ["DotsLoader", "ElapsedClock", "GAP_PX", "LINE_PX", "ShimmerLabel",
           "format_elapsed", "pixel_on", "user_waiting", "waited_between"]
