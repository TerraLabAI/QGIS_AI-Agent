# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later









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
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .attach_card import AttachCard
from .attachments import attachment_caption, attachment_kind, card_art, is_picture, tile_pixmap
from .file_links import linkify_paths
from .file_preview import can_open, open_file_preview
from .icons import icon_for
from .image_preview import ClickableThumb, open_image_preview
from .layer_card import LayerCard
from .layer_links import LAYER_URL, follow_layer_links
from .loader import GAP_PX, LINE_PX, DotsLoader, ElapsedClock, ShimmerLabel
from .markdown_view import MarkdownView
from .source_marks import SourcesButton
from .style import (
    _BTN_QUIET,
    INK_2,
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

SOURCE_PAGE_SCHEME = "library-source:"


def _tag(text: str, parent=None) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("tag")
    label.setToolTip(text)
    return label


class UserBubble(QWidget):











    layer_clicked = pyqtSignal(str)
    source_clicked = pyqtSignal(str)

    def __init__(self, text: str, chips=None, attachments=None, parent=None):
        super().__init__(parent)
        self._text = text or ""
        self.run_id = ""
        self._chips = [c for c in (chips or []) if isinstance(c, dict)]
        self._attachments = [a for a in (attachments or []) if isinstance(a, dict)]
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACE_TIGHT)

        pictures = [a for a in self._attachments if is_picture(a)]
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
                card.source_clicked.connect(self.source_clicked.emit)
                self._tags_flow.addWidget(card)
            for item in files:
                art, glyph, color = card_art(self._tags_host, item)
                clickable = can_open(item)
                card = AttachCard(self._attachment_text(item), attachment_kind(item),
                                  art, self._tags_host, glyph=glyph, tile_color=color,
                                  clickable=clickable, tooltip=attachment_caption(item))
                if clickable:

                    card.set_quiet_hover()
                    card.clicked.connect(lambda item=item, host=self: open_file_preview(item, host))
                self._tags_flow.addWidget(card)
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
        self._view.chip_open_data_links()
        self._link_mentions()
        self._view.link_activated.connect(self._on_link)
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

    def _link_mentions(self) -> None:


        sources = [(str(c.get("label") or ""), str(c.get("value") or "")) for c in self._chips
                   if str(c.get("kind") or "") == "source" and c.get("value") and c.get("label")]
        if not sources:
            return
        from qgis.PyQt.QtGui import QFont, QTextCharFormat

        from .style import ACCENT_INK, qcolor

        document = self._view.document()
        for label, value in sources:
            cursor = document.find("@" + label)
            while not cursor.isNull():
                link = QTextCharFormat()
                link.setAnchor(True)
                link.setAnchorHref(SOURCE_PAGE_SCHEME + value)
                link.setForeground(qcolor(ACCENT_INK))
                link.setFontWeight(QFont.Weight.Medium)
                cursor.mergeCharFormat(link)
                cursor = document.find("@" + label, cursor.position())

    def _on_link(self, href: str) -> None:
        href = str(href or "")
        if href.startswith(SOURCE_PAGE_SCHEME):

            cursor = self._view.textCursor()
            cursor.clearSelection()
            self._view.setTextCursor(cursor)
            self._view.clearFocus()
            self.source_clicked.emit(href[len(SOURCE_PAGE_SCHEME):])

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

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._fit()

    def _pin_floor(self, text_height: int) -> None:















        self._frame.setMinimumHeight(max(USER_PILL_RADIUS * 2,
                                         int(text_height) + 2 * (_BUBBLE_PAD_Y + _BUBBLE_BORDER)))

    def _on_text_height(self, height: int) -> None:








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

    def minimumSizeHint(self):  # noqa: N802












        return QSize(60, super().minimumSizeHint().height())

    def _tags_row_width(self) -> int:

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


def _set_layout_visible(layout, visible: bool) -> None:
    if layout is None:
        return
    for i in range(layout.count()):
        item = layout.itemAt(i)
        if item.widget() is not None:
            item.widget().setVisible(visible)
        elif item.layout() is not None:
            _set_layout_visible(item.layout(), visible)


class AgentBubble(QWidget):
















    link_activated = pyqtSignal(str)


    restore_clicked = pyqtSignal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._col = QVBoxLayout(self)
        self._col.setContentsMargins(2, 2, 2, 2)
        self._col.setSpacing(0)
        self._view = MarkdownView(self)
        self._view.link_activated.connect(self.link_activated.emit)
        self._view.height_changed.connect(self._on_text_height)
        self._col.addWidget(self._view)

        self._actions = QWidget(self)
        actions = QHBoxLayout(self._actions)
        actions.setContentsMargins(0, SPACE_TIGHT, 0, 0)
        actions.setSpacing(4)
        self._sources = SourcesButton(self._actions)
        self._sources.hide()
        actions.addWidget(self._sources, 0, Qt.AlignmentFlag.AlignVCenter)
        actions.addStretch(1)




        self._restore = QPushButton(self._actions)
        self._restore.setObjectName("answerRestore")
        self._restore.setProperty("agentAction", "restore")
        self._restore.setStyleSheet(_BTN_QUIET)
        self._restore.setCursor(Qt.CursorShape.PointingHandCursor)
        self._restore.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._restore.clicked.connect(self.restore_clicked.emit)
        self._restore.hide()
        self._undone = False
        self._later = 0
        actions.addWidget(self._restore, 0, Qt.AlignmentFlag.AlignVCenter)
        self._actions.hide()
        self._col.addWidget(self._actions)
        self._fade = None
        self._text = ""
        self._written: str | None = None
        self.run_id = ""
        self._pending: list[str] = []
        self._finished = False
        self._linked = False
        self._followed = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_STREAM_INTERVAL_MS)
        self._timer.timeout.connect(self._flush_pending)
        if text:
            self.set_text(text)



    def append_token(self, text: str) -> None:

        if not text or self._finished:
            return
        if not self._finished:
            self._view.set_streaming(True)
        self._pending.append(text)
        if not self._timer.isActive():
            self._timer.start()

    def flush(self) -> None:

        self._timer.stop()
        self._flush_pending(final=True)

    def end_text(self) -> None:


        self._timer.stop()
        self._flush_pending(final=True)
        self._view.set_streaming(False)

    def finish_streaming(self) -> None:


        self._timer.stop()
        self._finished = True
        self._flush_pending(final=True)
        self._view.set_streaming(False)
        self._sync_actions()

    def is_finished(self) -> bool:
        return self._finished

    def set_text(self, text: str) -> None:
        self._timer.stop()
        self._pending = []
        self._text = text or ""
        self._written = None
        self._render()

    def set_linked_text(self, text: str) -> None:


        written = self.copy_text()
        self.set_text(text)
        self._written = written

    def _render(self, streaming: bool = False, gone=frozenset()) -> None:










        started = time.perf_counter()
        if streaming:
            self._view.stream_markdown(self._text)
        else:

            self._followed = follow_layer_links(self._text, gone)
            self._view.set_markdown(linkify_paths(self._followed))
        self._linked = not streaming
        if streaming:
            spent_ms = (time.perf_counter() - started) * 1000.0
            interval = int(min(_STREAM_INTERVAL_MAX_MS, max(_STREAM_INTERVAL_MS, spent_ms * _STREAM_DUTY)))
            self._timer.setInterval(interval)

    def text(self) -> str:
        return self._text + "".join(self._pending)

    def copy_text(self) -> str:

        return self.text() if self._written is None else self._written

    def follow_project(self, gone=frozenset()) -> None:



        if not self._linked or LAYER_URL not in self._text:
            return
        if follow_layer_links(self._text, gone) != self._followed:
            self._render(gone=gone)

    def to_markdown(self) -> str:
        parts = [f"**{self.tr('Agent')}:**"]
        parts.append(self.copy_text())
        return "\n\n".join(parts)

    def _flush_pending(self, final: bool = False) -> None:


        if self._pending:
            self._text += "".join(self._pending)
            self._pending = []
        elif not final or self._linked:
            return
        self._render(streaming=not final and not self._finished)



    def set_sources(self, items) -> None:

        self._sources.set_sources(items)
        self._sources.setVisible(bool(self._sources.sources()))
        self._sync_actions()

    def sources(self) -> list:
        return self._sources.sources()

    def set_wordless(self, wordless: bool) -> None:


        self._view.setVisible(not wordless)
        self._on_text_height(0)

    def set_restore(self, mode: str, tooltip: str = "", later: int = 0) -> None:







        mode = mode if mode in ("undo", "redo") else ""
        self._later = max(0, int(later or 0)) if mode == "undo" else 0
        if mode:
            label = self.tr("Redo changes") if mode == "redo" else self.tr("Undo changes")
            glyph = "lu.redo-2" if mode == "redo" else "lu.undo-2"
            self._restore.setText(label)
            self._restore.setIcon(icon_for(self._restore, glyph, 14, QColor(INK_2)))
            self._restore.setIconSize(QSize(14, 14))
            self._restore.setToolTip(tooltip)
            self._restore.setAccessibleName(label)
            self._restore.setAccessibleDescription(tooltip)
        self._restore.setVisible(bool(mode))
        self._undone = mode == "redo"
        self._sync_actions()

    def restore_mode(self) -> str:
        if self._restore.isHidden():
            return ""
        return "redo" if self._undone else "undo"

    def set_changes(self, row: QWidget, animate: bool = True) -> None:

        self._col.insertWidget(self._col.indexOf(self._actions), row)
        row.show()
        if animate:
            self._fade_up(row)
        self._on_text_height(0)

    def _sync_actions(self) -> None:

        has_sources = bool(self._sources.sources())
        shown = has_sources or not self._restore.isHidden()
        if shown != (not self._actions.isHidden()):
            self._actions.setVisible(shown)
            self._on_text_height(0)

    def _fade_up(self, widget: QWidget) -> None:




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






            fade.setProperty("faded_widget", widget)
            self._fade = fade
            fade.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        except (RuntimeError, AttributeError, TypeError):
            self._drop_effect(widget)

    def _stop_fade(self) -> None:

        fade, self._fade = self._fade, None
        if fade is None:
            return
        try:
            widget = fade.property("faded_widget")
        except (RuntimeError, AttributeError):
            widget = None
        try:
            fade.stop()
        except (RuntimeError, AttributeError):
            pass
        if widget is not None:
            self._drop_effect(widget)

    @staticmethod
    def _drop_effect(widget: QWidget) -> None:
        try:
            widget.setGraphicsEffect(None)
        except (RuntimeError, AttributeError):
            pass

    def cleanup(self) -> None:

        self._timer.stop()
        self._stop_fade()
        self._view.set_streaming(False)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        extra = max(0, self.width() - 4 - _AGENT_MAX_WIDTH)
        margins = self._col.contentsMargins()
        if margins.right() != 2 + extra:
            self._col.setContentsMargins(2, 2, 2 + extra, 2)

    def _on_text_height(self, _height: int) -> None:









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

    def hide_clock(self, hidden: bool) -> None:
        self._clock.setVisible(not hidden)

    def start(self) -> None:
        motion = not (callable(self._reduced_motion) and self._reduced_motion())
        for part in (self._dots, self._label):
            part.set_motion(motion)
            part.start()

    def stop(self) -> None:
        self._dots.stop()
        self._label.stop()
        self._clock.stop()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self.start()

    def cleanup(self) -> None:

        self.stop()
