# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

















from __future__ import annotations

from qgis.PyQt.QtCore import QEasingCurve, QEvent, QRectF, QSize, Qt
from qgis.PyQt.QtGui import QColor, QPainter, QPen
from qgis.PyQt.QtWidgets import QPushButton, QWidget

from ..icons import icon_for
from ..shared import event_pos, tr
from . import common as C
from .pictures import PictureView, Tween

_STEP, _STEP_LARGE = 0.02, 0.10
_SWEEP_MS = 1000

_SWEEP_REACH = 0.14
_KEYBOARD_REASONS = (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
                     Qt.FocusReason.ShortcutFocusReason)


class SliderView(PictureView):


    def __init__(self, url: str, parent=None, radius: int = 16, label: str = "",
                 label_px: int = 20, pair: tuple = ("", ""), split: float = 0.5,
                 fallback=None, enlarge: bool = True):
        super().__init__(url, parent=parent, full=True, radius=radius, label=label,
                         label_px=label_px, pair=pair, split=split, fallback=fallback)
        self._dragging = False
        self._keyboard = False
        self._intro_pending = False
        self._sweep = Tween(self, _SWEEP_MS, self._set_pos, QEasingCurve.Type.InOutSine)
        self._args = {"url": url, "radius": radius, "label": label, "label_px": label_px,
                      "pair": pair, "split": split, "fallback": fallback}
        self.set_handle(C.px(16))
        if self.is_pair():
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.setCursor(Qt.CursorShape.SplitHCursor)
            self.setMouseTracking(False)
            self.setAccessibleName(tr("Before and after comparison"))
            self._speak()
        self._enlarge = None
        if enlarge and (self.is_pair() or self._url):
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            button = QPushButton(self)
            side = C.px(32)
            button.setFixedSize(side, side)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            button.setAutoDefault(False)
            button.setToolTip(tr("Enlarge"))
            button.setAccessibleName(tr("Enlarge"))
            button.setIcon(icon_for(button, "expand", 16, QColor("#ffffff")))
            button.setIconSize(QSize(16, 16))
            button.setStyleSheet(
                f"QPushButton {{ background: rgba(0,0,0,140); border: none; border-radius: {side // 2}px; }}"
                "QPushButton:hover { background: rgba(0,0,0,190); }")
            button.clicked.connect(self.show_enlarged)
            self._enlarge = button



    def play_intro(self) -> None:

        self._intro_pending = self.is_pair()
        self._maybe_sweep()

    def _maybe_sweep(self) -> None:
        if not self._intro_pending or not self.isVisible() or not self.has_pair():
            return
        self._intro_pending = False
        s = self.split
        low, high = max(0.04, s - _SWEEP_REACH), min(0.96, s + _SWEEP_REACH)
        self._sweep.run(s, s, keys=((0.3, low), (0.7, high)))

    def _cancel_sweep(self) -> None:
        self._intro_pending = False
        self._sweep.stop()

    def _on_ready(self, url: str) -> None:
        super()._on_ready(url)
        self._maybe_sweep()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._maybe_sweep()

    def hideEvent(self, event):  # noqa: N802
        self._sweep.stop()
        super().hideEvent(event)



    def enterEvent(self, event):  # noqa: N802
        self._cancel_sweep()
        super().enterEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        self._cancel_sweep()
        if event.button() == Qt.MouseButton.LeftButton and self.is_pair():
            self._dragging = True
            self._keyboard = False
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self._move_to(event_pos(event).x())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._dragging:
            self._move_to(event_pos(event).x())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _move_to(self, x: float) -> None:
        self.follow(x)
        self._speak()



    def keyPressEvent(self, event):  # noqa: N802
        self._cancel_sweep()
        if not self.is_pair():
            super().keyPressEvent(event)
            return
        key = event.key()
        step = _STEP_LARGE if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else _STEP
        target = {Qt.Key.Key_Left: self.pos - step, Qt.Key.Key_Right: self.pos + step,
                  Qt.Key.Key_Home: 0.0, Qt.Key.Key_End: 1.0}.get(key)
        if target is None:

            event.ignore()
            return
        self._set_pos(target)
        self._speak()
        if not self._keyboard:
            self._keyboard = True
            self.update()
        event.accept()

    def focusInEvent(self, event):  # noqa: N802
        self._keyboard = event.reason() in _KEYBOARD_REASONS
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event):  # noqa: N802
        self.update()
        super().focusOutEvent(event)

    def _speak(self) -> None:
        self.setAccessibleDescription(tr("Divider at {n}%").format(n=round(self.pos * 100)))



    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        if not (self.hasFocus() and self._keyboard):
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(QPen(QColor(C.T.text), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            r = self._radius - 1
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), r, r)
        finally:
            painter.end()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if self._enlarge is not None:
            gap = C.px(10)
            self._enlarge.move(self.width() - self._enlarge.width() - gap, self.height()
                               - self._enlarge.height() - gap)



    def show_enlarged(self) -> None:
        window = self.window()
        if window is None:
            return
        Enlarged(window, self._args, self.pos).show_over()


class Enlarged(QWidget):





    def __init__(self, window: QWidget, args: dict, pos: float):
        super().__init__(window)
        self.setObjectName("libraryEnlarged")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("QWidget#libraryEnlarged { background: rgba(0,0,0,215); }")
        self.view = SliderView(parent=self, enlarge=False, **args)
        self.view.pos = pos
        close = QPushButton(self)
        side = C.px(36)
        close.setFixedSize(side, side)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setAutoDefault(False)
        close.setToolTip(tr("Close"))
        close.setAccessibleName(tr("Close"))
        close.setIcon(icon_for(close, "close", 18, QColor("#ffffff")))
        close.setIconSize(QSize(18, 18))
        close.setStyleSheet(f"QPushButton {{ background: rgba(255,255,255,40); border: none;"
                            f" border-radius: {side // 2}px; }}"
                            "QPushButton:hover { background: rgba(255,255,255,70); }")
        close.clicked.connect(self.leave)
        self._close = close
        window.installEventFilter(self)

    def show_over(self) -> None:
        self._place()
        self.show()
        self.raise_()
        self.view.setFocus(Qt.FocusReason.OtherFocusReason)

    def _place(self) -> None:
        window = self.parentWidget()
        self.setGeometry(0, 0, window.width(), window.height())
        margin = C.px(56)
        room_w, room_h = max(1, self.width() - 2 * margin), max(1, self.height() - 2 * margin)
        width = min(room_w, round(room_h * 16 / 9))
        height = round(width * 9 / 16)
        self.view.setFixedSize(width, height)
        self.view.move((self.width() - width) // 2, (self.height() - height) // 2)
        gap = C.px(14)
        self._close.move(self.width() - self._close.width() - gap, gap)

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._place()
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.leave()
            event.accept()
            return
        event.accept()

    def mousePressEvent(self, event):  # noqa: N802
        if not self.view.geometry().contains(event_pos(event)):
            self.leave()
        event.accept()

    def leave(self) -> None:
        try:
            self.parentWidget().removeEventFilter(self)
        except (AttributeError, RuntimeError):
            pass
        self.hide()
        self.deleteLater()
