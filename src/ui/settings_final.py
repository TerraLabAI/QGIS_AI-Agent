# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

from datetime import datetime, timezone

from qgis.PyQt.QtCore import QEasingCurve, QEvent, QObject, QPoint, QRectF, Qt, QTimer, QVariantAnimation
from qgis.PyQt.QtGui import QColor, QPainter, QPen
from qgis.PyQt.QtWidgets import (
    QAbstractButton,
    QFrame,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.qt_compat import qt_deleted
from . import tokens
from .font_scale import scale_px_length
from .mascot import LIVE
from .memory_mascot import Companion
from .settings_pages import (  # noqa: F401
    EDGE,
    FOCUS_RING,
    FOCUS_RING_ON_FILL,
    RADIUS_CARD_PX,
    RADIUS_CONTROL_PX,
)
from .style import OFFER_FILL_PRESSED, lay_shadow, paint_shadow
from .styles import DARK_UI

T = tokens.DARK if DARK_UI else tokens.LIGHT

UPGRADE_FILL, UPGRADE_DEEP = T["accent_hover"], "#1b5e20"
UPGRADE_QSS = (
    f"QPushButton {{ background: {UPGRADE_FILL}; color: #ffffff; border: 1px solid transparent;"
    f" border-radius: {RADIUS_CONTROL_PX}px; padding: 7px 16px; font-size: 12px; font-weight: 600; }}"
    f"QPushButton:hover {{ background: {UPGRADE_DEEP}; }}"
    f"QPushButton:pressed {{ background: {OFFER_FILL_PRESSED}; padding: 8px 16px 6px 16px; }}"
)

CARD_LINE = EDGE


def lift(widget: QWidget) -> None:



    if getattr(widget, "_plate", None) is None:
        widget._plate = lay_shadow(widget, "lift", RADIUS_CARD_PX)


def reset_is_today(iso: str) -> bool:

    try:
        when = datetime.fromisoformat(str(iso or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone().date() == datetime.now().astimezone().date()


def days_until(iso: str):

    try:
        when = datetime.fromisoformat(str(iso or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0, (when - datetime.now(timezone.utc)).days + 1)


class UsageBar(QWidget):


    def __init__(self, parent, left: int, limit: int):
        super().__init__(parent)
        self._part = max(0.0, min(1.0, left / float(max(1, limit))))
        self.setFixedHeight(scale_px_length(4))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(f"{round(self._part * 100)}%")

    def paintEvent(self, _event):  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            h, w = float(self.height()), float(self.width())
            painter.setBrush(QColor(T["surface_field"]))
            painter.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
            if self._part > 0:
                painter.setBrush(QColor(T["accent"]))
                painter.drawRoundedRect(QRectF(0, 0, max(h, w * self._part), h), h / 2, h / 2)
        finally:
            painter.end()


def _refocus(widget: QWidget) -> None:
    window = widget.window()
    if not window.isActiveWindow():
        window.activateWindow()
    widget.setFocus(Qt.FocusReason.PopupFocusReason)


class InfoPopover(QFrame):


    def __init__(self, anchor: QWidget, text: str):
        super().__init__(anchor.window(), Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self._anchor = anchor
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 14)
        card = self._card = QFrame(self)
        card.setObjectName("infoCard")
        card.setStyleSheet(f"QFrame#infoCard {{ background: {T['surface_raised']}; border: 1px solid {CARD_LINE};"
                           f" border-radius: {RADIUS_CONTROL_PX + 2}px; }}")
        col = QVBoxLayout(card)
        col.setContentsMargins(12, 10, 12, 10)
        words = QLabel(text, card)
        words.setWordWrap(True)
        words.setStyleSheet(f"font-size: 12px; color: {T['text_primary']}; background: transparent;")
        words.setMaximumWidth(scale_px_length(260))
        words.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        col.addWidget(words)
        outer.addWidget(card)
        self.adjustSize()

    def paintEvent(self, event):  # noqa: N802
        paint_shadow(self, self._card.geometry(), RADIUS_CONTROL_PX + 2, "overlay")
        super().paintEvent(event)

    def show_under(self) -> None:
        a = self._anchor
        spot = a.mapToGlobal(QPoint(a.width() // 2, a.height()))
        self.move(spot.x() - 24, spot.y() - 2)
        self.show()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):  # noqa: N802
        anchor = self._anchor
        try:
            anchor.setFocus(Qt.FocusReason.PopupFocusReason)
        except RuntimeError:
            pass


        QTimer.singleShot(0, lambda: None if qt_deleted(anchor) else _refocus(anchor))
        super().closeEvent(event)


class InfoButton(QAbstractButton):


    def __init__(self, parent: QWidget, text: str, topic: str = ""):
        super().__init__(parent)
        self._text = text
        self.setFixedSize(scale_px_length(16), scale_px_length(16))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName(f"{topic} (i)" if topic else "(i)")
        self.set_text(text)
        self.clicked.connect(lambda _checked=False: self.open())

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            return
        super().keyPressEvent(event)

    def set_text(self, text: str) -> None:
        self._text = text
        self.setToolTip(text)
        self.setAccessibleDescription(text)

    def text_shown(self) -> str:
        return self._text

    def open(self) -> InfoPopover:
        popover = InfoPopover(self, self._text)
        popover.show_under()
        return popover

    def enterEvent(self, event):  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event):  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            s = float(min(self.width(), self.height()))
            lit = self.underMouse() or self.isDown() or self.hasFocus()
            ink = QColor(T["text_primary"] if lit else T["text_tertiary"])
            if self.hasFocus():
                painter.setPen(QPen(QColor(FOCUS_RING), 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QRectF(1, 1, s - 2, s - 2))
            pen = QPen(ink, max(1.0, s / 14))
            painter.setPen(pen)
            painter.setBrush(QColor(T["surface_field"]) if self.isDown() else Qt.BrushStyle.NoBrush)
            inset = s * 0.14
            painter.drawEllipse(QRectF(inset, inset, s - 2 * inset, s - 2 * inset))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ink)
            dot = s * 0.11
            painter.drawEllipse(QRectF(s / 2 - dot / 2, s * 0.29, dot, dot))
            stem = s * 0.10
            painter.drawRoundedRect(QRectF(s / 2 - stem / 2, s * 0.45, stem, s * 0.27), stem / 2, stem / 2)
        finally:
            painter.end()


class NoteToInfo(QObject):




    def __init__(self, note: QLabel, info: InfoButton | None, tip_on: QWidget | None = None):
        super().__init__(note)
        self._note, self._info, self._tip_on = note, info, tip_on
        note.installEventFilter(self)
        self._sync()

    def _sync(self) -> None:
        text = self._note.text()
        if self._info is not None:
            self._info.set_text(text)
            self._info.setVisible(bool(text))
        elif self._tip_on is not None:
            self._tip_on.setToolTip(text)
            self._tip_on.setAccessibleDescription(text)
        self._note.hide()

    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() == QEvent.Type.Show:
            self._sync()
        return False


class Peek(Companion):





    NEAR_PX, FAR_PX, BACK_MS = 56, 96, 500

    def __init__(self, host: QWidget, card: QWidget, settings, size_px: int, at: float = 0.82,
                 perched: bool = False):
        self._card, self._at_x, self._sink = card, at, 0.0

        self._hidden = 0.14 if perched else 0.34
        self._rest_sink = 0.0
        self._near = False
        self._far_since = None
        super().__init__(host, settings, size_px, beside=card)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_sink)
        self.mascot.on_tick = self._watch

    def _set_sink(self, value) -> None:
        self._sink = float(value)
        self._place()

    def sink_to(self, value: float) -> None:

        if self.mascot.mode() != LIVE:
            value = 0.0
        self._anim.stop()
        self._anim.setStartValue(self._sink)
        self._anim.setEndValue(float(value))
        self._anim.start()

    def rest_at(self, value: float) -> None:

        self._rest_sink = value
        if not self._near:
            self.sink_to(value)

    def near(self, near: bool) -> None:
        if near == self._near:
            return
        self._near = near
        if near:
            self.mascot.react("duck", 0.3)
        self.sink_to(1.0 if near else self._rest_sink)

    def _watch(self) -> None:


        import time

        from qgis.PyQt.QtGui import QCursor

        head = self.mascot.mapToGlobal(self.mascot.rect().center())
        d = (QCursor.pos() - head).manhattanLength()
        if d < scale_px_length(self.NEAR_PX):
            self._far_since = None
            self.near(True)
        elif self._near and d > scale_px_length(self.FAR_PX):
            now = time.perf_counter()
            self._far_since = self._far_since or now
            if now - self._far_since > self.BACK_MS / 1000.0:
                self.near(False)

    def _place(self) -> None:
        card = getattr(self, "_card", None)

        if card is None or not self._host.isAncestorOf(card):
            return
        top = card.mapTo(self._host, QPoint(0, 0))
        cx = top.x() + card.width() * self._at_x
        bottom = top.y() + self._size * (self._hidden + 0.62 * getattr(self, "_sink", 0.0))
        self.mascot.put(cx, bottom, self._size, clip_bottom=top.y() + 1)
        self.mascot.raise_()


def quota_mood(left, limit) -> str:

    if left is None or limit is None:
        return "idle"
    if left <= 0:
        return "sleep"
    if left <= max(1, limit // 5):
        return "worry"
    return "calm"

