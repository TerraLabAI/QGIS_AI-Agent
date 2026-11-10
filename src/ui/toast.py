# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later









from __future__ import annotations

from qgis.PyQt.QtCore import QEasingCurve, QEvent, QPropertyAnimation, Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton

from ..core.qt_compat import disconnect_quietly, one_shot
from .font_scale import scale_qss_font_px
from .icons import pixmap_for
from .style import FONT_BODY, ORANGE, RADIUS_CARD, TOOLTIP_BG, TOOLTIP_BORDER, TOOLTIP_FG, TOOLTIP_MUTED
from .widgets import ElidedLabel

TOAST_MS = 3000
WARN_MS = 6000

_FADE_IN_MS = 180
_FADE_OUT_MS = 120
_BOTTOM_GAP = 12

_QSS = scale_qss_font_px(
    f"QFrame#restoreToast {{ background: {TOOLTIP_BG}; border: 1px solid {TOOLTIP_BORDER};"
    f" border-radius: {RADIUS_CARD}px; }}"
    f"QLabel#toastText {{ color: {TOOLTIP_FG}; font-size: {FONT_BODY}px; background: transparent; }}"
    f"QLabel#toastDot {{ color: {TOOLTIP_MUTED}; font-size: {FONT_BODY}px; background: transparent; }}"
    "QPushButton#toastLink { background: transparent; border: none; padding: 0;"
    f" color: {TOOLTIP_FG}; font-size: {FONT_BODY}px; font-weight: 600; }}"
    "QPushButton#toastLink:hover { text-decoration: underline; }"
    f"QPushButton#toastLink:disabled {{ color: {TOOLTIP_MUTED}; text-decoration: none; }}"
)


class Toast(QFrame):


    def __init__(self, host):
        super().__init__(host)
        self.setObjectName("restoreToast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_QSS)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 7, 12, 7)
        row.setSpacing(6)
        self._glyph = QLabel(self)
        self._glyph.setFixedSize(14, 14)
        row.addWidget(self._glyph, 0, Qt.AlignmentFlag.AlignVCenter)


        self._text = ElidedLabel("", self)
        self._text.setObjectName("toastText")
        row.addWidget(self._text, 1, Qt.AlignmentFlag.AlignVCenter)
        self._dot = QLabel("·", self)
        self._dot.setObjectName("toastDot")
        row.addWidget(self._dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self._link = QPushButton(self)
        self._link.setObjectName("toastLink")
        self._link.setCursor(Qt.CursorShape.PointingHandCursor)
        self._link.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._link.clicked.connect(self._on_link)
        row.addWidget(self._link, 0, Qt.AlignmentFlag.AlignVCenter)
        self._on_link_cb = None
        self._idle_only = False
        self._ms = TOAST_MS



        self._fade = None
        self._fading_out = False
        self._timer = one_shot(self, self.dismiss)
        host.installEventFilter(self)
        self.hide()

    def show_message(self, text: str, *, link: str = "", on_link=None, warn: bool = False,
                     tooltip: str = "", idle_only: bool = False, ms: int = 0) -> None:



        self._idle_only = bool(idle_only)
        if not idle_only:
            self._link.setEnabled(True)
            self._link.setToolTip("")
        self._text.setText(str(text or ""))
        self._link.setText(str(link or ""))
        self._link.setVisible(bool(link))
        self._dot.setVisible(bool(link))
        self._on_link_cb = on_link if link else None
        self._glyph.setVisible(bool(warn))
        if warn:
            self._glyph.setPixmap(pixmap_for(self, "warning", 14, QColor(ORANGE)))
        self.setToolTip(str(tooltip or ""))
        self._ms = int(ms) or (WARN_MS if warn else TOAST_MS)
        self.adjustSize()
        self._place()
        was_shown = self.isVisible() and not self._fading_out

        start = self._opacity() if self.isVisible() else 0.0
        self.show()
        self.raise_()
        if not was_shown:
            self._start_fade(start, 1.0)
        self._timer.start(self._ms)

    def text(self) -> str:
        return self._text.text()

    def set_link_enabled(self, enabled: bool) -> None:

        if not self._idle_only:
            return
        self._link.setEnabled(bool(enabled))
        self._link.setToolTip("" if enabled else self.tr("Available when the AI agent finishes"))

    def dismiss(self) -> None:
        if self.isHidden() or self._fading_out:
            return
        self._start_fade(1.0, 0.0)

    def _opacity(self) -> float:
        effect = self.graphicsEffect()
        return float(effect.opacity()) if isinstance(effect, QGraphicsOpacityEffect) else 1.0

    def _start_fade(self, start: float, end: float) -> None:
        self._stop_fade()
        self._fading_out = end < start
        effect = QGraphicsOpacityEffect(self)
        effect.setOpacity(start)
        self.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", self)
        fade.setDuration(_FADE_OUT_MS if self._fading_out else _FADE_IN_MS)
        fade.setEasingCurve(QEasingCurve.Type.OutQuint)
        fade.setStartValue(start)
        fade.setEndValue(end)
        fade.finished.connect(self._fade_done)
        self._fade = fade
        fade.start()

    def _stop_fade(self) -> None:
        fade, self._fade = self._fade, None
        if fade is not None:
            disconnect_quietly(fade, "finished", self._fade_done)
            fade.stop()
            fade.deleteLater()
        self.setGraphicsEffect(None)

    def _fade_done(self) -> None:
        out = self._fading_out
        self._stop_fade()
        self._fading_out = False
        if out:
            self.hide()

    def _on_link(self) -> None:
        callback = self._on_link_cb
        self._timer.stop()
        self._stop_fade()
        self._fading_out = False
        self.hide()
        if callback is not None:
            callback()

    def _place(self) -> None:
        host = self.parentWidget()
        if host is None:
            return
        width = min(self.sizeHint().width(), max(120, host.width() - 24))
        self.resize(width, self.sizeHint().height())
        self.move(max(0, (host.width() - width) // 2), max(0, host.height() - self.height() - _BOTTOM_GAP))

    def enterEvent(self, event):  # noqa: N802
        self._timer.stop()
        if self._fading_out:
            self._start_fade(self._opacity(), 1.0)
            self._fading_out = False
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        if self.isVisible():
            self._timer.start(self._ms // 2)
        super().leaveEvent(event)

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize and self.isVisible():
            self._place()
        return False
