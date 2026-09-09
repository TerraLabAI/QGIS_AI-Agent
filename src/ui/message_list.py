# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The scrolling stack of messages and cards."""






from __future__ import annotations

from qgis.PyQt.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPropertyAnimation,
    QSize,
    Qt,
    QVariantAnimation,
)
from qgis.PyQt.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .card_base import reduced_motion
from .icons import icon_for
from .style import _BTN_SCROLL_PILL, _SCROLL_AREA_QSS, SPACE_OUTER



_NAV_KEYS = frozenset((
    Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
    Qt.Key.Key_Home, Qt.Key.Key_End,
))

_STICKY_SLACK = 4


_TOP_AIR = 24


_FADE_MS = 130


_SCROLL_MS = 220


_PILL_PX = 28
_PILL_ICON_PX = 14


class MessageList(QScrollArea):
    """Vertical stack with sticky auto-scroll and card registries."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("messageList")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setAutoFillBackground(False)
        self.viewport().setAutoFillBackground(False)



        self._scroll_styled = False
        self.setStyleSheet(_SCROLL_AREA_QSS)

        self._build_content()


        self._stick = True
        self._ride = None
        bar = self.verticalScrollBar()
        bar.rangeChanged.connect(self._on_range_changed)
        bar.valueChanged.connect(self._on_value_changed)
        bar.sliderPressed.connect(self._on_bar_pressed)
        bar.actionTriggered.connect(lambda _action: self._on_bar_pressed())


        self.tool_cards: dict = {}
        self.traces: dict = {}
        self.trace_blocks: dict = {}
        self.permission_cards: dict = {}



        self._pill = QToolButton(self)
        self._pill.setObjectName("scrollPill")
        self._pill.setStyleSheet(_BTN_SCROLL_PILL)



        self._pill.setToolTip(self.tr("Scroll to bottom"))
        self._pill.setAccessibleName(self.tr("Scroll to bottom"))
        self._pill.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._pill.setIconSize(QSize(_PILL_ICON_PX, _PILL_ICON_PX))
        self._pill.setFixedSize(_PILL_PX, _PILL_PX)
        self._pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pill.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._pill.clicked.connect(self.scroll_to_bottom)
        self._pill.hide()

    def _build_content(self) -> None:
        """A fresh, empty stack in the viewport."""


        self._content = QWidget(self)
        self._content.setObjectName("messageContent")
        self._content.setAutoFillBackground(False)
        self._layout = QVBoxLayout(self._content)


        self._layout.setContentsMargins(12, _TOP_AIR, 12, SPACE_OUTER)
        self._layout.setSpacing(SPACE_OUTER)
        self._layout.addStretch(1)
        self.setWidget(self._content)

    def showEvent(self, event):  # noqa: N802 - Qt override
        super().showEvent(event)
        if not self._scroll_styled:
            self._scroll_styled = True
            self.setStyleSheet(_SCROLL_AREA_QSS)
            self._pill.setIcon(icon_for(self._pill, "arrow_down", _PILL_ICON_PX))

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._update_pill()

    def _place_pill(self) -> None:



        viewport = self.viewport().geometry()
        x = viewport.x() + (viewport.width() - _PILL_PX) // 2
        y = viewport.y() + viewport.height() - _PILL_PX - SPACE_OUTER
        self._pill.setGeometry(max(0, x), max(0, y), _PILL_PX, _PILL_PX)
        self._pill.raise_()



    def add_widget(self, widget: QWidget, before: QWidget | None = None, animate: bool = True) -> None:
        """Append ``widget``, or insert it just above ``before``."""




        if widget is None or widget is self._content:
            return


        if self._layout.indexOf(widget) >= 0:
            return
        index = self._layout.count() - 1
        if before is not None:
            found = self._layout.indexOf(before)
            if found >= 0:
                index = found
        self._layout.insertWidget(index, widget)
        if animate and self.isVisible():
            self._fade_in(widget)

    def _fade_in(self, widget: QWidget) -> None:
        """One short fade, then the effect is dropped: a graphics effect left on a streaming bubble repaints the whole thing on every token."""

        try:
            effect = QGraphicsOpacityEffect(widget)
            effect.setOpacity(0.0)
            widget.setGraphicsEffect(effect)
            fade = QPropertyAnimation(effect, b"opacity", widget)
            fade.setDuration(_FADE_MS)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.setEasingCurve(QEasingCurve.Type.OutCubic)
            fade.finished.connect(lambda: self._drop_effect(widget))







            widget._fade_anim = fade
            fade.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        except (RuntimeError, AttributeError, TypeError):
            self._drop_effect(widget)

    @staticmethod
    def _stop_fade(widget: QWidget) -> None:
        """Stop a fade still running on a widget about to be deleted."""
        fade = getattr(widget, "_fade_anim", None)
        if fade is None:
            return
        try:
            fade.stop()
        except (RuntimeError, AttributeError):
            pass
        try:
            widget._fade_anim = None
        except (RuntimeError, AttributeError):
            pass

    @staticmethod
    def _drop_effect(widget: QWidget) -> None:
        try:
            widget.setGraphicsEffect(None)
        except (RuntimeError, AttributeError):
            pass

    def remove_widget(self, widget: QWidget) -> None:
        if widget is None:
            return
        self._stop_fade(widget)
        if self._layout.indexOf(widget) < 0:
            return
        try:
            self._layout.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        except (RuntimeError, AttributeError):
            pass




        self._forget(widget)

    def _forget(self, widget) -> None:
        """Drop every registry entry pointing at ``widget``."""
        for registry in (self.permission_cards, self.tool_cards, self.traces):
            for key in [k for k, v in registry.items() if v is widget]:
                registry.pop(key, None)
        for run_id, blocks in list(self.trace_blocks.items()):
            if widget in blocks:
                kept = [b for b in blocks if b is not widget]
                if kept:
                    self.trace_blocks[run_id] = kept
                else:
                    self.trace_blocks.pop(run_id, None)

    def widgets(self) -> list:
        items = []
        for i in range(self._layout.count()):
            item = self._layout.itemAt(i)
            w = item.widget() if item is not None else None
            if w is not None:
                items.append(w)
        return items

    def last_widget(self, ignore=None) -> QWidget | None:
        """The bottom-most widget, skipping ``ignore`` (the status line)."""
        for w in reversed(self.widgets()):
            if w is not ignore:
                return w
        return None

    def is_empty(self) -> bool:
        return self._layout.count() <= 1

    def stop_animations(self) -> None:
        """Stop everything still moving, before the list or its widgets go."""
        self._stop_ride()
        for widget in self.widgets():
            self._stop_fade(widget)
            stop = getattr(widget, "cleanup", None)
            if callable(stop):
                try:
                    stop()
                except (RuntimeError, AttributeError):
                    pass

    def clear(self) -> None:
        """Empty the stack."""



        self._stop_ride()
        for widget in self.widgets():
            self._stop_fade(widget)
            stop = getattr(widget, "cleanup", None)
            if callable(stop):
                try:
                    stop()
                except (RuntimeError, AttributeError):
                    pass
        old = self.takeWidget()
        self._build_content()
        if old is not None:
            old.hide()
            old.deleteLater()
        self.tool_cards.clear()
        self.traces.clear()
        self.trace_blocks.clear()
        self.permission_cards.clear()
        self._stick = True


        self._pill.hide()



    def register_tool_card(self, tool_call_id: str, card) -> None:
        self.tool_cards[tool_call_id] = card

    def tool_card(self, tool_call_id: str):
        return self.tool_cards.get(tool_call_id)

    def register_trace(self, run_id: str, trace) -> None:
        """One more activity block of the run: ``trace`` gives the latest, ``traces_of`` every block in order."""

        self.traces[run_id] = trace
        self.trace_blocks.setdefault(run_id, []).append(trace)

    def trace(self, run_id: str):
        return self.traces.get(run_id)

    def traces_of(self, run_id: str) -> list:
        return list(self.trace_blocks.get(run_id, ()))

    def register_permission_card(self, tool_call_id: str, card) -> None:
        self.permission_cards[tool_call_id] = card

    def permission_card(self, tool_call_id: str):
        return self.permission_cards.get(tool_call_id)



    def scroll_to_bottom(self, animate: bool = True) -> None:
        """Go to the newest message, riding down when the reader asked for it."""
        bar = self.verticalScrollBar()
        target = bar.maximum()
        self._stick = True
        self._pill.hide()
        if not animate or reduced_motion(self) or abs(target - bar.value()) < 8:
            bar.setValue(target)
            return
        self._stop_ride()
        ride = QVariantAnimation(self)
        ride.setDuration(_SCROLL_MS)
        ride.setStartValue(bar.value())
        ride.setEndValue(target)
        ride.setEasingCurve(QEasingCurve.Type.OutCubic)
        ride.valueChanged.connect(lambda value: bar.setValue(int(value)))



        ride.finished.connect(self._ride_finished)
        self._ride = ride
        ride.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _stop_ride(self) -> None:
        """Stop a scroll still gliding, for the same reason as a fade."""
        ride = getattr(self, "_ride", None)
        self._ride = None
        if ride is None:
            return
        try:
            ride.stop()
        except (RuntimeError, AttributeError):
            pass

    def _ride_finished(self) -> None:
        """The ride reached the bottom: the disc goes by position again."""
        self._ride = None
        self._update_pill()

    def at_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum() - _STICKY_SLACK

    def wheelEvent(self, event):  # noqa: N802 - Qt override



        self._stop_ride()
        super().wheelEvent(event)

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        if event.key() in _NAV_KEYS:
            self._stop_ride()
        super().keyPressEvent(event)

    def _on_bar_pressed(self) -> None:
        self._stop_ride()

    def _on_value_changed(self, value: int) -> None:
        bar = self.verticalScrollBar()
        self._stick = value >= bar.maximum() - _STICKY_SLACK
        self._update_pill()

    def _on_range_changed(self, _low: int, high: int) -> None:
        if self._stick:
            self.verticalScrollBar().setValue(high)
        self._update_pill()

    def _update_pill(self) -> None:
        """The disc is on screen exactly when pressing it would move the view."""







        bar = self.verticalScrollBar()
        if bar.maximum() <= 0 or self.at_bottom() or getattr(self, "_ride", None) is not None:
            self._pill.hide()
            return
        self._place_pill()
        self._pill.show()
