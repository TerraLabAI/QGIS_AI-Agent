# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


















from __future__ import annotations

import html
import math
import sys
import time
from collections import deque

from qgis.PyQt.QtCore import QEvent, QObject, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QBrush, QColor, QDesktopServices, QIcon, QKeySequence, QLinearGradient, QPainter, QPen
from qgis.PyQt.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..core import dictation as core
from ..core.host_platform import prefers_reduced_motion
from ..core.served_state import DICTATION_PRIVACY_URL
from ..core.settings import GROUP, profile_settings
from .external_links import open_external_url
from .font_scale import scale_px_length, scale_qss_font_px
from .icons import pixmap_for
from .settings_pages import PRIMARY_BTN_QSS
from .shared import available_screen_rect, exec_dialog
from .style import ACCENT, BTN_PX, FONT_HINT, GREEN_TINT, INK, INK_2, INK_3, accent_ink, qcolor, with_platform_font
from .widgets import IconButton

SHORTCUT = "Ctrl+Shift+D"
_NOTICE_KEY = f"{GROUP}/dictation_notice_seen"


_MIC_OK_KEY = f"{GROUP}/dictation_mic_ok"
_RING_AFTER_MS = 700
_BAR_W, _BAR_GAP = 2.5, 2.5
_STEP = _BAR_W + _BAR_GAP
_TICK_MS = 60
_FRAME_MS = 16
_FADE_PX = 64
_APPEAR_S = 0.18
_SWEEP_S = 1.5
_FRESH_BARS = 26
_GLYPH = 16


def shortcut_text() -> str:
    return QKeySequence(SHORTCUT).toString(QKeySequence.SequenceFormat.NativeText)


def clock(seconds: float) -> str:
    whole = max(0, int(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


def _ease_out(share: float) -> float:
    share = max(0.0, min(1.0, share))
    return 1.0 - (1.0 - share) ** 3


class _Painted(QWidget):


    def paintEvent(self, _event):  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._paint(painter)
        except Exception:  # noqa: BLE001  # nosec B110
            pass
        finally:
            painter.end()


class DictationBar(_Painted):


    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._levels: deque[float] = deque(maxlen=400)
        self._pending = 0.0
        self._tick_at = self._begun = self._frozen_at = time.monotonic()
        self._frozen = False
        self._on_key = None
        self._bars = QTimer(self)
        self._bars.setInterval(_TICK_MS)
        self._bars.timeout.connect(self._tick)
        self._frames = QTimer(self)
        self._frames.setInterval(_FRAME_MS)
        self._frames.timeout.connect(self.update)
        self.setAccessibleName(self.tr("Recording"))

    def on_key(self, handler) -> None:

        self._on_key = handler

    def begin(self) -> None:
        self._levels.clear()
        self._pending = 0.0
        self._frozen = False
        self._tick_at = self._begun = time.monotonic()
        self._bars.start()
        self._frames.start()
        self.update()

    def add_level(self, level: float) -> None:
        self._pending = max(self._pending, float(level))

    def freeze(self) -> None:

        self._frozen = True
        self._frozen_at = time.monotonic()
        self._bars.stop()
        if prefers_reduced_motion():
            self._frames.stop()
        else:
            self._frames.start()
        self.update()

    def end(self) -> None:
        self._bars.stop()
        self._frames.stop()

    def hideEvent(self, event):  # noqa: N802
        self.end()
        super().hideEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        if self._on_key is not None and self._on_key(event.key()):
            event.accept()
            return
        super().keyPressEvent(event)

    def _tick(self) -> None:
        self._levels.append(self._pending)
        self._pending = 0.0
        self._tick_at = time.monotonic()

    def _paint(self, painter: QPainter) -> None:
        now = time.monotonic()
        width, mid = self.width(), self.height() / 2.0
        tallest = max(2.0, mid - 3.0)
        appear = _ease_out((now - self._begun) / _APPEAR_S)


        spent = 1.0 if self._frozen else min(1.0, (now - self._tick_at) * 1000 / _TICK_MS)
        voice, quiet, fresh = QColor(qcolor(INK)), QColor(qcolor(INK_3)), QColor(qcolor(ACCENT))
        sweep = None
        if self._frozen and self._frames.isActive():
            sweep = -80 + ((now - self._frozen_at) % _SWEEP_S) / _SWEEP_S * (width + 160)
        painter.setPen(Qt.PenStyle.NoPen)
        x = width - _BAR_W - (0.0 if self._frozen else spent * _STEP)
        levels = reversed(self._levels)
        age = 0
        while x >= -_BAR_W:
            level = next(levels, 0.0)
            if age == 0 and not self._frozen:
                level *= _ease_out(spent)
            spoken = level > 0.05
            colour = QColor(voice if spoken else quiet)
            if spoken and not self._frozen and age < _FRESH_BARS:

                keep = max(0.0, 1.0 - (age + spent) / _FRESH_BARS) ** 0.5
                colour = QColor(*(round(a + (b - a) * keep) for a, b in
                                  zip(voice.getRgb()[:3], fresh.getRgb()[:3])))
            age += 1
            alpha = appear * min(1.0, max(0.0, x / _FADE_PX))
            if self._frozen:
                lit = math.exp(-((x - sweep) / 46.0) ** 2) if sweep is not None else 0.0
                alpha *= (0.38 + 0.62 * lit) if spoken else 1.0
            colour.setAlphaF(colour.alphaF() * alpha)
            half = max(_BAR_W / 2.0, level * tallest) if spoken else _BAR_W / 2.0
            painter.setBrush(colour)
            painter.drawRoundedRect(QRectF(x, mid - half, _BAR_W, half * 2), _BAR_W / 2.0, _BAR_W / 2.0)
            x -= _STEP


class _Status(_Painted):






    _BARS = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._word = ""
        self._time = ""
        self._live = False
        self._level = 0.0
        self._since = time.monotonic()
        self._frames = QTimer(self)
        self._frames.setInterval(33)
        self._frames.timeout.connect(self.update)
        self.setFixedHeight(BTN_PX)

    def show_listening(self, word: str, clock_text: str) -> None:
        self._set(word, clock_text, True)

    def show_word(self, text: str) -> None:
        self._set(text, "", False)

    def add_level(self, level: float) -> None:

        self._level = max(float(level), self._level * 0.82)

    def _set(self, word: str, clock_text: str, live: bool) -> None:
        if (word, clock_text, live) == (self._word, self._time, self._live):
            return
        if live != self._live:
            self._since = time.monotonic()
        self._word, self._time, self._live = word, clock_text, live
        self.updateGeometry()
        self.update()

    def showEvent(self, event):  # noqa: N802
        self._since = time.monotonic()
        self._level = 0.0
        if not prefers_reduced_motion():
            self._frames.start()
        super().showEvent(event)

    def hideEvent(self, event):  # noqa: N802
        self._frames.stop()
        super().hideEvent(event)

    def sizeHint(self) -> QSize:  # noqa: N802
        self.ensurePolished()
        metrics = self.fontMetrics()

        digits = "".join("0" if c.isdigit() else c for c in self._time)
        wide = metrics.horizontalAdvance(self._word) + (metrics.horizontalAdvance("  " + digits) if digits else 0)
        return QSize(8 + (18 if self._live else 0) + wide + 6, BTN_PX)

    def _paint(self, painter: QPainter) -> None:
        x, mid = 8.0, self.height() / 2.0
        moving = self._frames.isActive()
        spent = time.monotonic() - self._since
        metrics = painter.fontMetrics()
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if self._live:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor(ACCENT))
            self._level *= 0.94 if moving else 1.0
            for index in range(self._BARS):
                sway = 0.5 + 0.5 * math.sin(spent * 6.5 + index * 2.1) if moving else 0.5
                half = 1.5 + (1.2 + 4.3 * min(1.0, self._level)) * (0.45 + 0.55 * sway)
                painter.drawRoundedRect(QRectF(x + index * 4.5, mid - half, 2.5, 2 * half), 1.25, 1.25)
            x += 18
        painter.setPen(qcolor(INK_2))
        if not self._live and moving:

            wide = max(1, metrics.horizontalAdvance(self._word))
            at = x - wide + (spent % _SWEEP_S) / _SWEEP_S * 3 * wide
            light = QLinearGradient(at - wide * 0.6, 0, at + wide * 0.6, 0)
            light.setColorAt(0.0, qcolor(INK_3))
            light.setColorAt(0.5, qcolor(INK))
            light.setColorAt(1.0, qcolor(INK_3))
            painter.setPen(QPen(QBrush(light), 1))
        painter.drawText(QRectF(x, 0, self.width() - x, self.height()), flags, self._word)
        if self._time:
            x += metrics.horizontalAdvance(self._word + "  ")
            painter.setPen(qcolor(INK_3))

            cell = metrics.horizontalAdvance("0")
            centred = int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            for char in self._time:
                wide = cell if char.isdigit() else metrics.horizontalAdvance(char)
                painter.drawText(QRectF(x, 0, wide, self.height()), centred, char)
                x += wide


class _Ring(_Painted):


    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedSize(parent.size())
        self._since = time.monotonic()
        self._frames = QTimer(self)
        self._frames.setInterval(_FRAME_MS)
        self._frames.timeout.connect(self.update)
        self.hide()

    def showEvent(self, event):  # noqa: N802
        self._since = time.monotonic()
        self._frames.start()
        super().showEvent(event)

    def hideEvent(self, event):  # noqa: N802
        self._frames.stop()
        super().hideEvent(event)

    def _paint(self, painter: QPainter) -> None:
        side = 15.0
        box = QRectF((self.width() - side) / 2.0, (self.height() - side) / 2.0, side, side)
        track = QColor(qcolor(INK_3))
        track.setAlphaF(0.35)
        pen = QPen(track, 1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(box)
        pen.setColor(qcolor(INK_2))
        painter.setPen(pen)
        turn = 0.0 if prefers_reduced_motion() else (time.monotonic() - self._since) * 420.0
        painter.drawArc(box, int((90 - turn) * 16), -100 * 16)


class _Voice(_Painted):


    _BARS = (0.45, 0.8, 1.0, 0.6, 0.3)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._disc = scale_px_length(64)
        self.setFixedSize(scale_px_length(190), scale_px_length(76))
        self._glyph = pixmap_for(self, "lu.mic", scale_px_length(26), QColor(accent_ink()))
        self._since = time.monotonic()
        self._frames = QTimer(self)
        self._frames.setInterval(40)
        self._frames.timeout.connect(self.update)

    def showEvent(self, event):  # noqa: N802
        self._frames.start()
        super().showEvent(event)

    def hideEvent(self, event):  # noqa: N802
        self._frames.stop()
        super().hideEvent(event)

    def _paint(self, painter: QPainter) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        accent = QColor(accent_ink())
        mid_x, mid_y = self.width() / 2.0, self.height() / 2.0
        wash = QColor(accent)
        wash.setAlphaF(0.14)
        painter.setBrush(wash)
        painter.drawEllipse(QRectF(mid_x - self._disc / 2.0, mid_y - self._disc / 2.0, self._disc, self._disc))
        ratio = self._glyph.devicePixelRatio() or 1.0
        painter.drawPixmap(int(mid_x - self._glyph.width() / ratio / 2), int(mid_y - self._glyph.height() / ratio / 2),
                           self._glyph)
        phase = (time.monotonic() - self._since) * 3.2
        width, step = scale_px_length(3), scale_px_length(8)
        tallest = self.height() * 0.62
        for index, share in enumerate(self._BARS):
            height = max(width, tallest * share * (0.62 + 0.38 * math.sin(phase - index * 0.85)))
            bar = QColor(accent)
            bar.setAlphaF(0.85 - index * 0.15)
            painter.setBrush(bar)
            offset = self._disc / 2.0 + step * (index + 1)
            for x in (mid_x + offset - width / 2.0, mid_x - offset - width / 2.0):
                painter.drawRoundedRect(QRectF(x, mid_y - height / 2.0, width, height), width / 2.0, width / 2.0)


class _Notice(QDialog):









    def __init__(self, parent, tr):
        super().__init__(parent)
        with_platform_font(self)
        self.setObjectName("dictationNotice")
        self.setWindowTitle(tr("Voice dictation"))
        self.setModal(True)
        pad = 28
        available = available_screen_rect(self)
        width = scale_px_length(360)
        if available is not None:
            width = max(280, min(width, available.width() - 48))
        self.setFixedWidth(width)
        text_width = width - 2 * pad

        column = QVBoxLayout(self)
        column.setContentsMargins(pad, 26, pad, 18)
        column.setSpacing(0)
        column.addWidget(_Voice(self), 0, Qt.AlignmentFlag.AlignHCenter)
        column.addSpacing(14)
        column.addWidget(self._line(tr("Talk to your AI agent"), text_width,
                                    f"font-size: 19px; font-weight: 600; color: {INK};"))
        column.addSpacing(8)
        column.addWidget(self._line(tr("Speak, and your words become text you can edit before sending."),
                                    text_width, f"font-size: 13px; color: {INK_2};"))
        column.addSpacing(20)
        column.addWidget(self._privacy(tr, text_width))
        column.addSpacing(18)

        self.confirm_button = QPushButton(tr("Start dictating"), self)
        self.confirm_button.setObjectName("dictationNoticeConfirm")
        self.confirm_button.setStyleSheet(
            PRIMARY_BTN_QSS + scale_qss_font_px("QPushButton { padding: 10px 14px; font-size: 13px;"
                                                " border-radius: 10px; }"))
        self.confirm_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.confirm_button.clicked.connect(self.accept)
        column.addWidget(self.confirm_button)
        column.addSpacing(4)
        self.cancel_button = QPushButton(tr("Not now"), self)
        self.cancel_button.setObjectName("dictationNoticeCancel")
        self.cancel_button.setFlat(True)
        self.cancel_button.setStyleSheet(scale_qss_font_px(
            f"QPushButton {{ background: transparent; border: none; color: {INK_2}; font-size: 12px;"
            f" padding: 8px 14px; }} QPushButton:hover {{ color: {INK}; }}"))
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self.reject)
        column.addWidget(self.cancel_button)

        self.confirm_button.setDefault(True)
        self.confirm_button.setFocus()

    def _privacy(self, tr, width: int) -> QFrame:
        box = QFrame(self)
        box.setObjectName("dictationNoticePrivacy")
        box.setStyleSheet(f"QFrame#dictationNoticePrivacy {{ background: {GREEN_TINT}; border-radius: 10px; }}")
        row = QHBoxLayout(box)
        row.setContentsMargins(12, 12, 14, 12)
        row.setSpacing(10)
        glyph = scale_px_length(20)
        shield = QLabel(box)
        shield.setPixmap(pixmap_for(self, "lu.shield-check", glyph, QColor(accent_ink())))
        shield.setFixedWidth(glyph + 2)
        shield.setStyleSheet("background: transparent;")
        row.addWidget(shield, 0, Qt.AlignmentFlag.AlignTop)
        text_width = width - 12 - 14 - 10 - glyph - 2
        words = QVBoxLayout()
        words.setSpacing(3)
        words.addWidget(self._line(tr("Your voice stays private"), text_width,
                                   f"font-size: 13px; font-weight: 600; color: {INK};", centred=False))
        link = (f'<a href="{html.escape(DICTATION_PRIVACY_URL, quote=True)}" style="color: {accent_ink()};'
                f' text-decoration: none;">{html.escape(tr("Privacy Policy"))}</a>')
        facts = self._line(html.escape(tr("Recorded only when you press the microphone, transcribed in the EU,"
                                          " never stored by TerraLab and never used to train AI.")) + " " + link,
                           text_width, f"font-size: 12px; color: {INK_2};", centred=False, rich=True)
        facts.setObjectName("dictationNoticePrivacyText")
        facts.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse
                                      | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        facts.setOpenExternalLinks(False)
        facts.linkActivated.connect(lambda url: open_external_url(url, parent=self))
        words.addWidget(facts)
        row.addLayout(words, 1)
        return box

    def _line(self, text: str, width: int, qss: str, centred: bool = True, rich: bool = False) -> QLabel:
        label = QLabel(text, self)
        label.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
        label.setAlignment(Qt.AlignmentFlag.AlignHCenter if centred else Qt.AlignmentFlag.AlignLeft)
        label.setWordWrap(True)
        label.setStyleSheet(scale_qss_font_px(qss + " background: transparent;"))
        label.setFixedWidth(width)
        label.ensurePolished()
        label.setMinimumHeight(label.heightForWidth(width))
        return label


def _settings_url() -> str:
    if sys.platform == "darwin":
        return "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
    if sys.platform == "win32":
        return "ms-settings:privacy-microphone"
    return ""


class ComposerDictation(QObject):


    RETRY, SETTINGS = "dictation-retry", "dictation-settings"
    attached = pyqtSignal(object)

    def __init__(self, composer, row, column, input_widget, send_btn):
        super().__init__(composer)
        self._composer = composer
        self._input = input_widget
        self._send_btn = send_btn
        self._dictation: core.Dictation | None = None
        self._seen = False
        self.button = IconButton(composer, "lu.mic", _GLYPH)
        self.button.setFixedSize(BTN_PX, BTN_PX)
        self.button.clicked.connect(self._on_button)
        self.button.hide()
        row.insertWidget(row.indexOf(send_btn), self.button, 0, Qt.AlignmentFlag.AlignVCenter)
        self._ring = _Ring(self.button)
        self._eager = False
        self._ring_wait = QTimer(self)
        self._ring_wait.setSingleShot(True)
        self._ring_wait.setInterval(_RING_AFTER_MS)
        self._ring_wait.timeout.connect(self._late_ring)
        self.bar = DictationBar(composer)
        self.bar.on_key(self._on_bar_key)
        self.bar.hide()
        column.insertWidget(column.indexOf(input_widget) + 1, self.bar)
        self.status = _Status(composer)
        self.status.setStyleSheet(scale_qss_font_px(f"font-size: {FONT_HINT + 1}px;"))
        self.status.hide()
        row.insertWidget(0, self.status, 0, Qt.AlignmentFlag.AlignVCenter)
        self._clock = QTimer(self)
        self._clock.setInterval(250)
        self._clock.timeout.connect(self._show_time)
        self._paint_button()
        composer.installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802


        if (watched is self._composer and event.type() == QEvent.Type.Hide and not event.spontaneous()
                and self.state in (core.STARTING, core.ASKING, core.RECORDING)):
            self._dictation.cancel()
        return False



    def attach(self, dictation: core.Dictation) -> None:
        self._dictation = dictation
        dictation.state_changed.connect(self._on_state)
        dictation.level.connect(self.bar.add_level)
        dictation.level.connect(self.status.add_level)
        dictation.text_ready.connect(self._on_text)
        dictation.failed.connect(self._on_failed)
        dictation.cut_at_limit.connect(self._on_cut)
        dictation.availability_changed.connect(self.sync)
        self.attached.emit(dictation)
        self.sync()

    @property
    def state(self) -> str:
        return self._dictation.state if self._dictation is not None else core.IDLE

    @property
    def active(self) -> bool:


        return self.state in (core.RECORDING, core.TRANSCRIBING) or (self.state == core.STARTING and self._eager)

    def sync(self) -> None:

        if self._dictation is None:
            return
        if self._dictation.available:
            self._seen = True
        self.button.setVisible(self._seen)
        self.button.setEnabled(self.state != core.IDLE or not self._composer.dictation_blocked())



    def toggle(self) -> None:

        if self._dictation is None or not self._seen:
            return
        if self.state == core.IDLE:
            self._start()
        else:
            self._dictation.confirm()

    def confirm(self) -> None:
        if self._dictation is not None:
            self._dictation.confirm()

    def cancel(self) -> None:
        if self._dictation is not None:
            self._dictation.cancel()

    def follow_link(self, href: str) -> bool:
        if href == self.RETRY and self._dictation is not None:
            self._dictation.retry()
            return True
        if href == self.SETTINGS:
            QDesktopServices.openUrl(QUrl(_settings_url()))
            return True
        return False

    def _on_button(self) -> None:
        if self.state == core.IDLE:
            self._start()
        else:
            self.cancel()

    def _on_bar_key(self, key: int) -> bool:
        if key == Qt.Key.Key_Escape:
            self.cancel()
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.confirm()
            return True
        return False

    def _start(self) -> None:
        if self._dictation is None or self._composer.dictation_blocked():
            return
        if not self._notice_accepted():
            return
        self._dictation.start()

    def _notice_accepted(self) -> bool:

        store = profile_settings()
        if str(store.value(_NOTICE_KEY, "")) == "1":
            return True
        dialog = _Notice(self._composer.window(), self.tr)
        if exec_dialog(dialog) != QDialog.DialogCode.Accepted:
            return False
        store.setValue(_NOTICE_KEY, "1")
        return True



    def _on_state(self, state: str) -> None:
        if state == core.IDLE:
            self._clock.stop()
            if not self.bar.isHidden():
                self.bar.hide()
                self._input.show()
                self._input.setFocus(Qt.FocusReason.OtherFocusReason)
        elif state == core.STARTING:
            self._composer.dictation_clear_hint()



            self._eager = str(profile_settings().value(_MIC_OK_KEY, "")) == "1"
            if self._eager:
                self._open_clip()
                self.status.show_listening(self.tr("Listening"), clock(0))
            else:
                self._ring_wait.start()
        elif state == core.ASKING:
            self._composer.dictation_clear_hint()
            if self._eager:

                self._eager = False
                profile_settings().remove(_MIC_OK_KEY)
                self.bar.hide()
                self._input.show()
        elif state == core.RECORDING:
            if self.bar.isHidden():
                self._open_clip()
            profile_settings().setValue(_MIC_OK_KEY, "1")
            self._show_time()
            self._clock.start()
        elif state == core.TRANSCRIBING:
            self._clock.stop()
            self._show_bar()
            self.bar.freeze()
            self.status.show_word(self.tr("Transcribing..."))
        self.status.setVisible(self.active)
        if state != core.STARTING:
            self._ring_wait.stop()
        self._ring.setVisible(state == core.ASKING)
        self._paint_button()
        self.sync()
        self._composer.dictation_state_changed()

    def _open_clip(self) -> None:
        self._show_bar()
        self.bar.begin()
        self.bar.setFocus(Qt.FocusReason.OtherFocusReason)

    def _late_ring(self) -> None:
        if self.state == core.STARTING and not self._eager:
            self._ring.show()
            self._paint_button()

    def _show_bar(self) -> None:
        if self.bar.isHidden():
            self.bar.setFixedHeight(max(BTN_PX, self._input.height()))
            self._input.hide()
            self.bar.show()

    def _show_time(self) -> None:
        if self._dictation is None:
            return
        elapsed, longest = self._dictation.elapsed(), self._dictation.max_seconds

        self.status.show_listening(
            self.tr("Listening"), f"{clock(elapsed)} / {clock(longest)}" if longest - elapsed <= 20 else clock(elapsed))

    def _on_text(self, text: str) -> None:
        cursor = self._input.textCursor()
        before = self._input.toPlainText()[:cursor.selectionStart()]
        lead = " " if before and not before[-1].isspace() else ""
        self._input.append_text(lead + text)
        self._input.setFocus(Qt.FocusReason.OtherFocusReason)

    def _on_cut(self) -> None:
        minutes = max(1, (self._dictation.max_seconds if self._dictation else 180) // 60)
        self._composer.show_hint(self.tr("Stopped at {n} minutes, the longest one recording can be.").format(n=minutes))

    def _on_failed(self, code: str) -> None:
        if code == "mic_denied":
            profile_settings().remove(_MIC_OK_KEY)
        text, link = self._words(code)
        self._composer.dictation_warn(text, link)

    def _words(self, code: str) -> tuple[str, tuple[str, str] | None]:
        can_retry = self._dictation is not None and self._dictation.can_retry
        retry = (self.tr("Try again"), self.RETRY) if can_retry else None
        settings = (self.tr("Open settings"), self.SETTINGS) if _settings_url() else None
        if code == "no_microphone":
            return self.tr("No microphone found. Plug one in or check your sound settings."), None
        if code == "mic_denied":
            if sys.platform == "darwin":
                return self.tr('The microphone is off for "AI Agent for QGIS". Turn it on in System Settings,'
                               " Privacy & Security, Microphone."), settings
            return self.tr("The microphone is blocked for QGIS in your system's privacy settings."), settings
        if code == "mic_unanswered":
            return self.tr("macOS asked to use the microphone and got no answer. Press the microphone to see"
                           " the question again."), None
        if code == "mic_silent":
            if sys.platform == "win32":
                return self.tr("No sound came from the microphone. In Windows settings, Privacy, Microphone,"
                               " let desktop apps use it."), settings
            return self.tr("No sound came from the microphone. Check that it is not muted."), settings
        if code == "mic_error":
            return self.tr("The microphone could not be opened. Check that no other app holds it,"
                           " then try again."), None
        if code == "unsupported":
            return self.tr("This QGIS installation cannot record sound, so dictation is not available here."), None
        if code == "nothing_heard":
            return self.tr("Nothing was heard. Speak closer to the microphone and try again."), None
        if code == "offline":
            return self.tr("No connection to TerraLab, so nothing was transcribed."), retry
        if code == "daily_limit":
            return self.tr("You reached today's dictation limit. It opens again tomorrow."), None
        if code == "too_long":
            return self.tr("That recording is too long to transcribe. Record a shorter one."), None
        if code == "rate_limited":
            return self.tr("Too many recordings in a minute. Wait a little."), retry
        if code == "busy":
            return self.tr("Dictation is busy right now."), retry
        if code == "unavailable":
            return self.tr("Dictation is not available right now."), None
        return self.tr("The recording could not be transcribed."), retry

    def _paint_button(self) -> None:
        if self.state == core.IDLE:
            name, said = "lu.mic", self.tr("Dictate")
            tip = self.tr("Dictate ({shortcut})").format(shortcut=shortcut_text())
        else:
            name, tip, said = "lu.x", self.tr("Cancel (Esc)"), self.tr("Cancel dictation")
        if self.active or self.state == core.IDLE:
            self.button.set_icon(name, _GLYPH, qcolor(INK_2 if self.state == core.IDLE else INK))
        elif self._ring.isHidden():

            self.button.set_icon("lu.mic", _GLYPH, qcolor(INK))
        else:
            self.button.set_glyph_icon(lambda _button: QIcon(), _GLYPH)
        self.button.setToolTip(tip)
        self.button.setAccessibleName(said)
