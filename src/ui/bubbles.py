# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The two message turns and the status line."""









from __future__ import annotations

import os
import time

from qgis.PyQt.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from qgis.PyQt.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .attach_card import AttachCard
from .attachments import attachment_caption, attachment_kind, file_art, tile_pixmap
from .file_links import linkify_paths
from .image_preview import ClickableThumb, open_image_preview
from .layer_card import LayerCard
from .loader import GAP_PX, LINE_PX, DotsLoader, ElapsedClock, ShimmerLabel
from .markdown_view import MarkdownView
from .source_marks import SourcesButton
from .style import (
    FONT_BODY,
    INK,
    INK_2,
    INK_3,
    MOTION_FADE_UP_MS,
    SPACE_TIGHT,
)
from .widgets import FlowLayout


_USER_BUBBLE_SHARE = 0.85




USER_PILL_RADIUS = 18
_BUBBLE_PAD_X = 10
_BUBBLE_PAD_Y = 6



_BUBBLE_BORDER = 1





_STREAM_INTERVAL_MS = 40
_STREAM_INTERVAL_MAX_MS = 250
_STREAM_DUTY = 4


_AGENT_MAX_WIDTH = 720
_IMAGE_THUMB_HEIGHT = 72


def _tag(text: str, parent=None) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("tag")
    label.setToolTip(text)
    return label


class UserBubble(QWidget):
    """A user turn, ChatGPT's stack: the layer and file cards first, on their own ground, then the tinted text box under them."""








    layer_clicked = pyqtSignal(str)

    def __init__(self, text: str, chips=None, attachments=None, parent=None):
        super().__init__(parent)
        self._text = text or ""
        self._chips = [c for c in (chips or []) if isinstance(c, dict)]
        self._attachments = [a for a in (attachments or []) if isinstance(a, dict)]
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACE_TIGHT)

        pictures = [a for a in self._attachments if a.get("kind") == "image" and a.get("data_base64")]
        files = [a for a in self._attachments if a not in pictures]
        self._tags_host = None
        self._tags_flow = None
        if self._chips or files or pictures:
            self._tags_host = QWidget(self)
            self._tags_flow = FlowLayout(self._tags_host, 4, 4)
            for chip in self._chips:
                card = LayerCard(chip, self._tags_host, closable=False)
                card.set_quiet_hover()
                card.layer_clicked.connect(self.layer_clicked.emit)
                self._tags_flow.addWidget(card)
            for item in files:
                art, glyph = file_art(self._tags_host, item)
                self._tags_flow.addWidget(
                    AttachCard(self._attachment_text(item), attachment_kind(item),
                               art, self._tags_host, glyph=glyph,
                               tooltip=attachment_caption(item)))
            for picture in pictures:
                pixmap = tile_pixmap(picture, self._tags_host, _IMAGE_THUMB_HEIGHT)
                if pixmap is None:
                    self._tags_flow.addWidget(_tag(self._attachment_text(picture), self._tags_host))
                    continue
                thumb = ClickableThumb(self._tags_host)
                thumb.setPixmap(pixmap)
                thumb.setToolTip(self.tr("{name}. Click to open.").format(name=self._attachment_text(picture)))
                thumb.clicked.connect(lambda item=picture, host=self: open_image_preview(item, host))
                self._tags_flow.addWidget(thumb)
            tags_row = QHBoxLayout()
            tags_row.setContentsMargins(0, 0, 0, 0)
            tags_row.setSpacing(0)
            tags_row.addStretch(1)
            tags_row.addWidget(self._tags_host)
            outer.addLayout(tags_row)

        self._frame = QFrame(self)
        self._frame.setObjectName("userBubble")
        col = QVBoxLayout(self._frame)
        col.setContentsMargins(_BUBBLE_PAD_X, _BUBBLE_PAD_Y, _BUBBLE_PAD_X, _BUBBLE_PAD_Y)
        col.setSpacing(SPACE_TIGHT)
        self._view = MarkdownView(self._frame)
        self._view.set_plain_text(self._text)
        self._view.height_changed.connect(self._on_text_height)
        col.addWidget(self._view)
        self._pin_floor(self._view.height())

        self._frame.setVisible(bool(self._text.strip()) or self._tags_host is None)
        text_row = QHBoxLayout()
        text_row.setContentsMargins(0, 0, 0, 0)
        text_row.setSpacing(0)
        text_row.addStretch(1)
        text_row.addWidget(self._frame)
        outer.addLayout(text_row)




        self._ideal = self._view.ideal_width() + 4 + 2 * _BUBBLE_BORDER

    @staticmethod
    def _chip_text(chip) -> str:
        if isinstance(chip, dict):
            return str(chip.get("label") or chip.get("value") or "")
        return str(chip)

    @staticmethod
    def _attachment_text(attachment) -> str:
        if isinstance(attachment, dict):
            name = attachment.get("name") or os.path.basename(str(attachment.get("path") or ""))
            return str(name)
        return os.path.basename(str(attachment))

    def text(self) -> str:
        return self._text

    def to_markdown(self) -> str:
        lines = [f"**{self.tr('You')}:** {self._text}"]
        extras = [self._chip_text(c) for c in self._chips]
        extras += [self._attachment_text(a) for a in self._attachments]
        extras = [e for e in extras if e]
        if extras:
            lines.append(f"_{self.tr('Context')}: {', '.join(extras)}_")
        return "\n".join(lines)

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._fit()

    def _pin_floor(self, text_height: int) -> None:
        """The pill's minimum height follows its text."""














        self._frame.setMinimumHeight(max(USER_PILL_RADIUS * 2,
                                         int(text_height) + 2 * (_BUBBLE_PAD_Y + _BUBBLE_BORDER)))

    def _on_text_height(self, height: int) -> None:
        """The pill wrapped to another number of lines."""







        self._pin_floor(height)
        self.updateGeometry()
        QTimer.singleShot(0, self._settle)

    def _settle(self) -> None:
        try:
            self.updateGeometry()
            layout = self.layout()
        except RuntimeError:
            return
        if layout is not None:
            layout.activate()

    def minimumSizeHint(self):  # noqa: N802 - Qt override
        """A turn never claims a floor wider than a pill's worth of text."""











        return QSize(60, super().minimumSizeHint().height())

    def _tags_row_width(self) -> int:
        """What the cards need on one line, before the flow layout wraps."""
        flow = self._tags_flow
        if flow is None:
            return 0
        total = 0
        for index in range(flow.count()):
            item = flow.itemAt(index)
            if item is None:
                continue
            if total:
                total += 4
            total += item.sizeHint().width()
        return total

    def _fit(self) -> None:
        """Hug the text, up to the bubble's share of the panel."""

        available = self.width()
        if available < 60:
            return
        cap = int(available * _USER_BUBBLE_SHARE)
        want = self._ideal + 2 * _BUBBLE_PAD_X + 2
        self._frame.setFixedWidth(max(60, min(cap, want)))
        if self._tags_host is not None:
            width = max(60, min(cap, self._tags_row_width()))
            self._tags_host.setFixedWidth(width)
            self._tags_host.setFixedHeight(max(0, self._tags_flow.heightForWidth(width)))


class StepLine(QWidget):
    """The line over an answer that came from a plan step (Chat, docs/DESIGN.md): the step name in ink medium, the source (a connector or a tool)."""



    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE_TIGHT)
        self._name = QLabel(self)
        self._name.setStyleSheet(
            f"font-size: {FONT_BODY}px; font-weight: 500; color: {INK};"
            " background: transparent; border: none;")
        self._source = QLabel(self)
        self._source.setStyleSheet(
            f"font-size: {FONT_BODY}px; color: {INK_2}; background: transparent; border: none;")
        self._time = QLabel(self)
        self._time.setStyleSheet(
            f"font-size: {FONT_BODY}px; color: {INK_3}; background: transparent; border: none;")
        for label in (self._name, self._source, self._time):
            row.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)

    def set_step(self, name: str, source: str = "", seconds=None) -> None:
        self._name.setText(str(name or ""))
        self._source.setText(str(source or ""))
        self._source.setVisible(bool(source))
        text = ""
        if seconds is not None and float(seconds) > 0:



            text = self.tr("for {n}s").format(n=max(1, int(round(float(seconds)))))
        self._time.setText(text)
        self._time.setVisible(bool(text))
        self.setVisible(bool(name))

    def text(self) -> str:
        parts = [self._name.text(), self._source.text(), self._time.text()]
        return "  ".join(p for p in parts if p)


class AgentBubble(QWidget):
    """An agent turn: prose on the panel ground, left aligned, at full width up to a comfortable measure, streamed in 40 ms ticks (Streaming Text."""
















    link_activated = pyqtSignal(str)
    retry_requested = pyqtSignal()
    feedback = pyqtSignal(bool)
    message_requested = pyqtSignal(str)

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._col = QVBoxLayout(self)
        self._col.setContentsMargins(2, 2, 2, 2)
        self._col.setSpacing(0)
        self._step = StepLine(self)
        self._step.hide()
        self._col.addWidget(self._step)
        self._view = MarkdownView(self)
        self._view.link_activated.connect(self.link_activated.emit)
        self._view.height_changed.connect(self._on_text_height)
        self._col.addWidget(self._view)
        self._sources = SourcesButton(self)
        self._sources.hide()
        self._col.addWidget(self._sources, 0, Qt.AlignmentFlag.AlignLeft)
        self._fade = None
        self._text = ""
        self._pending: list[str] = []
        self._finished = False
        self._linked = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_STREAM_INTERVAL_MS)
        self._timer.timeout.connect(self._flush_pending)
        if text:
            self.set_text(text)



    def append_token(self, text: str) -> None:
        """Queue a token; the view repaints at most every 40 ms."""
        if not text or self._finished:
            return
        if not self._finished:
            self._view.set_streaming(True)
        self._pending.append(text)
        if not self._timer.isActive():
            self._timer.start()

    def flush(self) -> None:
        """Paint everything queued, now, links included. Called when the run ends."""
        self._timer.stop()
        self._flush_pending(final=True)

    def finish_streaming(self) -> None:
        """The answer is complete: the caret goes, the paths become links."""
        self._timer.stop()
        self._finished = True
        self._flush_pending(final=True)
        self._view.set_streaming(False)

    def is_finished(self) -> bool:
        return self._finished

    def set_text(self, text: str) -> None:
        self._timer.stop()
        self._pending = []
        self._text = text or ""
        self._render()

    def _render(self, streaming: bool = False) -> None:
        """Paint ``_text`` with its file paths turned into links."""







        started = time.perf_counter()
        self._view.set_markdown(self._text if streaming else linkify_paths(self._text))
        self._linked = not streaming
        if streaming:
            spent_ms = (time.perf_counter() - started) * 1000.0
            interval = int(min(_STREAM_INTERVAL_MAX_MS, max(_STREAM_INTERVAL_MS, spent_ms * _STREAM_DUTY)))
            self._timer.setInterval(interval)

    def text(self) -> str:
        return self._text + "".join(self._pending)

    def to_markdown(self) -> str:
        parts = [f"**{self.tr('Agent')}:**"]
        if self._step.isVisible():
            parts.append(f"_{self._step.text()}_")
        parts.append(self.text())
        return "\n\n".join(parts)

    def _flush_pending(self, final: bool = False) -> None:
        """A tick paints what arrived; ``final`` paints the whole text with its links, once."""

        if self._pending:
            self._text += "".join(self._pending)
            self._pending = []
        elif not final or self._linked:
            return
        self._render(streaming=not final and not self._finished)



    def set_step_line(self, name: str, source: str = "", seconds=None) -> None:
        """The plan step this answer belongs to, its source and how long it took."""
        self._step.set_step(name, source, seconds)

    def set_sources(self, items) -> None:
        """``[{name, url, glyph?}]``: the stacked marks and ``N sources``."""
        self._sources.set_sources(items)
        self._sources.setVisible(bool(self._sources.sources()))

    def sources(self) -> list:
        return self._sources.sources()

    def _fade_up(self, widget: QWidget) -> None:
        """The site's fade-up: opacity 0 to 1."""



        self._stop_fade()
        try:
            effect = QGraphicsOpacityEffect(widget)
            effect.setOpacity(0.0)
            widget.setGraphicsEffect(effect)
            fade = QPropertyAnimation(effect, b"opacity", widget)
            fade.setDuration(MOTION_FADE_UP_MS)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.setEasingCurve(QEasingCurve.Type.OutQuint)
            fade.finished.connect(lambda: self._drop_effect(widget))
            self._fade = fade
            fade.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        except (RuntimeError, AttributeError, TypeError):
            self._drop_effect(widget)

    def _stop_fade(self) -> None:
        fade, self._fade = self._fade, None
        if fade is not None:
            try:
                fade.stop()
            except (RuntimeError, AttributeError):
                pass

    @staticmethod
    def _drop_effect(widget: QWidget) -> None:
        try:
            widget.setGraphicsEffect(None)
        except (RuntimeError, AttributeError):
            pass

    def cleanup(self) -> None:
        """Before the bubble goes: stop what still moves."""
        self._timer.stop()
        self._stop_fade()
        self._view.set_streaming(False)

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        extra = max(0, self.width() - 4 - _AGENT_MAX_WIDTH)
        margins = self._col.contentsMargins()
        if margins.right() != 2 + extra:
            self._col.setContentsMargins(2, 2, 2 + extra, 2)

    def _on_text_height(self, _height: int) -> None:
        """The prose wrapped to another number of lines."""








        self.updateGeometry()
        QTimer.singleShot(0, self._settle)

    def _settle(self) -> None:
        try:
            self.updateGeometry()
            layout = self.layout()
        except RuntimeError:
            return
        if layout is not None:
            layout.activate()


class StatusLine(QWidget):
    """The live line under the stream: the site's Loading State, Dots."""









    def __init__(self, text: str = "", parent=None, started: float | None = None,
                 reduced_motion=None):
        super().__init__(parent)
        self._reduced_motion = reduced_motion
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(GAP_PX)
        self._dots = DotsLoader(self)
        row.addWidget(self._dots, 0, Qt.AlignmentFlag.AlignVCenter)
        self._label = ShimmerLabel("", self)
        self._label.setObjectName("statusText")
        row.addWidget(self._label, 0, Qt.AlignmentFlag.AlignVCenter)
        self._clock = ElapsedClock(started, self)
        row.addWidget(self._clock, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        self.setFixedHeight(LINE_PX)
        self.set_text(text)
        self.start()

    def set_text(self, text: str) -> None:


        text = (text or "").rstrip(". \u2026")
        self._label.setText(text or self.tr("Thinking"))

    def text(self) -> str:
        return self._label.text()

    def set_started(self, started: float) -> None:
        self._clock.set_started(started)

    def start(self) -> None:
        motion = not (callable(self._reduced_motion) and self._reduced_motion())
        for part in (self._dots, self._label):
            part.set_motion(motion)
            part.start()

    def stop(self) -> None:
        self._dots.stop()
        self._label.stop()
        self._clock.stop()

    def showEvent(self, event):  # noqa: N802 - Qt override
        super().showEvent(event)
        self.start()

    def cleanup(self) -> None:
        """Called before the line is removed from the list."""
        self.stop()
