# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later









from __future__ import annotations

import os
import time

from qgis.PyQt.QtCore import (
    QAbstractAnimation,
    QCoreApplication,
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
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import openui
from .attach_card import AttachCard
from .attachments import attachment_caption, attachment_kind, card_art, is_picture, tile_pixmap
from .blocks import BlockPlaceholder, BlockView
from .file_links import linkify_paths
from .file_preview import can_open, open_file_preview
from .icons import icon_for
from .image_preview import ClickableThumb, open_image_preview
from .layer_card import LayerCard
from .layer_icons import follows_layers
from .layer_links import LAYER_URL, follow_layer_links
from .loader import GAP_PX, LINE_PX, DotsLoader, ElapsedClock, ShimmerLabel
from .markdown_view import MarkdownView
from .source_marks import SourcesButton
from .style import (
    _BTN_PRIMARY,
    _BTN_QUIET,
    FONT_BODY,
    FONT_HINT,
    GREEN,
    INK,
    INK_2,
    INK_3,
    LINE,
    LINE_STRONG,
    MOTION_FADE_UP_MS,
    RED,
    SPACE_OUTER,
    SPACE_TIGHT,
)
from .widgets import FlowLayout, IconButton

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


class _Thumb(IconButton):



    def __init__(self, parent, name: str, tooltip: str, tone: str):
        super().__init__(parent, None, 14, tooltip)
        self.setFixedSize(22, 22)
        self._name, self._tone = name, tone
        self.set_icon(self._name, 14, QColor(INK_3))

    def lock_chosen(self) -> None:
        self.set_icon(self._name + "-solid", 14, QColor(self._tone))

        self.setToolTip(QCoreApplication.translate("RatingWords", "Thanks for the feedback"))
        self.setCursor(Qt.CursorShape.ArrowCursor)


_MORE_FIELD = (
    "QLineEdit { background: transparent; border: none;"
    f" border-bottom: 1px solid {LINE}; color: {INK}; font-size: {FONT_BODY}px; padding: 4px 2px; }}"
    f"QLineEdit:focus {{ border-bottom-color: {LINE_STRONG}; }}"
)


class RatingWords(QWidget):






    sent = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, SPACE_TIGHT, 0, 0)
        col.setSpacing(SPACE_TIGHT)
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        self._field = QLineEdit(self)
        self._field.setMaxLength(1500)
        self._field.setPlaceholderText(self.tr("Tell us more"))
        self._field.setStyleSheet(_MORE_FIELD)
        self._field.textChanged.connect(self._sync)
        self._field.returnPressed.connect(self._on_send)
        line.addWidget(self._field, 1)
        self._send = QPushButton(self.tr("Send"), self)
        self._send.setStyleSheet(_BTN_PRIMARY)
        self._send.setFixedHeight(28)
        self._send.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send.setProperty("agentAction", "rating_send")
        self._send.clicked.connect(self._on_send)
        line.addWidget(self._send)
        col.addLayout(line)
        self._line = line

        self._hint = QLabel(
            self.tr("Sent with this conversation and your recent logs, to fix issues and improve AI Agent."), self)
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(f"color: {INK_3}; font-size: {FONT_HINT}px;")
        col.addWidget(self._hint)
        self._note = QLabel(self)
        self._note.setStyleSheet(f"color: {INK_3}; font-size: {FONT_HINT}px;")
        self._note.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._note.hide()
        col.addWidget(self._note)
        self._sync()

    def focus(self) -> None:
        self._field.setFocus()

    def is_done(self) -> bool:
        return self._line is None

    def _sync(self) -> None:
        self._send.setEnabled(bool(self._field.text().strip()) and self._field.isEnabled())

    def _on_send(self) -> None:
        text = self._field.text().strip()
        if not text or not self._field.isEnabled():
            return
        self._field.setEnabled(False)
        self._sync()
        self.sent.emit(text)

    def done(self, ok: bool) -> None:
        try:
            if ok:
                _set_layout_visible(self._line, False)
                self._line = None
                self._hint.hide()
                self._note.setText(self.tr("Thanks for the feedback"))
            else:
                self._field.setEnabled(True)
                self._sync()
                self._note.setText(self.tr("Could not send. Try again."))
            self._note.show()
        except RuntimeError:
            pass


class AgentBubble(QWidget):

















    link_activated = pyqtSignal(str)


    restore_clicked = pyqtSignal()


    rated = pyqtSignal(str)
    rating_words = pyqtSignal(str, str)

    report_clicked = pyqtSignal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._col = QVBoxLayout(self)
        self._col.setContentsMargins(2, 2, 2, 2)
        self._col.setSpacing(0)
        self._view = MarkdownView(self)
        self._view.link_activated.connect(self.link_activated.emit)
        self._view.height_changed.connect(self._on_text_height)
        self._col.addWidget(self._view)


        self._parts = QWidget(self)
        self._parts_col = QVBoxLayout(self._parts)
        self._parts_col.setContentsMargins(0, 0, 0, 0)
        self._parts_col.setSpacing(0)
        self._parts.hide()
        self._part_widgets: list = []
        self._col.addWidget(self._parts)
        self._actions = QWidget(self)
        actions = QHBoxLayout(self._actions)
        actions.setContentsMargins(0, SPACE_OUTER, 0, 0)
        actions.setSpacing(4)
        self._sources = SourcesButton(self._actions)
        self._sources.hide()
        actions.addWidget(self._sources, 0, Qt.AlignmentFlag.AlignVCenter)



        self._report = QPushButton(self.tr("Make a report"), self._actions)
        self._report.setObjectName("answerReport")
        self._report.setProperty("agentAction", "report")
        self._report.setStyleSheet(_BTN_QUIET)
        self._report.setIcon(icon_for(self._report, "lu.file-text", 14, QColor(INK_2)))
        self._report.setIconSize(QSize(14, 14))
        self._report.setToolTip(
            self.tr("Write the request for an HTML report of this analysis, with its maps and charts"))
        self._report.setCursor(Qt.CursorShape.PointingHandCursor)
        self._report.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._report.clicked.connect(self._on_report)
        self._report.hide()
        actions.addWidget(self._report, 0, Qt.AlignmentFlag.AlignVCenter)
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


        self._thumbs = {
            "up": _Thumb(self._actions, "lu.thumbs-up", self.tr("Good answer"), GREEN),
            "down": _Thumb(self._actions, "lu.thumbs-down", self.tr("Bad answer"), RED),
        }
        for rating, thumb in self._thumbs.items():
            thumb.setProperty("agentAction", "rate_" + rating)
            thumb.clicked.connect(lambda _=False, r=rating: self._on_thumb(r))
            thumb.hide()
            actions.addWidget(thumb, 0, Qt.AlignmentFlag.AlignVCenter)
        self._rating = ""
        self._words: RatingWords | None = None
        self._actions.hide()
        follows_layers(self)
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
        self._pending.append(text)
        if not self._timer.isActive():
            self._timer.start()

    def flush(self) -> None:

        self._timer.stop()
        self._flush_pending(final=True)

    def end_text(self) -> None:


        self._timer.stop()
        self._flush_pending(final=True)

    def finish_streaming(self) -> None:


        self._timer.stop()
        self._finished = True
        self._flush_pending(final=True)
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


        written = self._raw_text()
        self.set_text(text)
        self._written = written

    def _render(self, streaming: bool = False, gone=frozenset()) -> None:










        started = time.perf_counter()
        parts = openui.split(self._text) if openui.FENCE_TAG in self._text else [("md", self._text)]
        if not streaming:

            parts = [(p[0], p[1], True) if p[0] == "block" else p for p in parts]
        first = parts[0][1] if parts[0][0] == "md" else ""
        if streaming:
            self._view.stream_markdown(first)
        else:

            self._followed = follow_layer_links(self._text, gone)
            self._view.set_markdown(linkify_paths(follow_layer_links(first, gone)))
        self._view.setVisible(bool(first.strip()) or len(parts) == 1)
        self._render_parts(parts[1:] if parts[0][0] == "md" else parts, streaming, gone)
        self._linked = not streaming
        if streaming:
            spent_ms = (time.perf_counter() - started) * 1000.0
            interval = int(min(_STREAM_INTERVAL_MAX_MS, max(_STREAM_INTERVAL_MS, spent_ms * _STREAM_DUTY)))
            self._timer.setInterval(interval)

    def _render_parts(self, parts: list, streaming: bool, gone) -> None:




        keep = 0
        for index, part in enumerate(parts):
            if index >= len(self._part_widgets):
                break
            signature, _widget = self._part_widgets[index]
            if part[0] != signature[0] or (part[0] == "block" and part[1:] != signature[1:]):
                break
            keep = index + 1
        for _signature, widget in self._part_widgets[keep:]:
            self._parts_col.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        del self._part_widgets[keep:]
        for index, part in enumerate(parts):
            if index < keep:
                widget = self._part_widgets[index][1]
            else:
                widget = self._part_widget(part)
                self._parts_col.addWidget(widget)
                self._part_widgets.append((part, widget))
            if part[0] == "md":
                if streaming:
                    widget.stream_markdown(part[1])
                else:
                    widget.set_markdown(linkify_paths(follow_layer_links(part[1], gone)))
                widget.setVisible(bool(part[1].strip()))
        self._parts.setVisible(bool(parts))
        if keep < len(parts):
            self._on_text_height(0)

    def _part_widget(self, part: tuple) -> QWidget:
        if part[0] == "md":
            view = MarkdownView(self._parts)
            view.link_activated.connect(self.link_activated.emit)
            view.height_changed.connect(self._on_text_height)
            return view
        if not part[2]:
            return BlockPlaceholder(self._parts)
        block = openui.parse(part[1])
        if block is not None:
            return BlockView(block, self._parts)
        view = MarkdownView(self._parts)
        view.set_plain_text(part[1])
        view.height_changed.connect(self._on_text_height)
        return view

    def text(self) -> str:
        return self._text + "".join(self._pending)

    def _raw_text(self) -> str:

        return self.text() if self._written is None else self._written

    def copy_text(self) -> str:


        return openui.readable(self._raw_text())

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

    def set_report_offer(self, shown: bool) -> None:
        if bool(shown) == (not self._report.isHidden()):
            return
        self._report.setVisible(bool(shown))
        self._sync_actions()

    def report_offered(self) -> bool:
        return not self._report.isHidden()

    def _on_report(self) -> None:
        self.set_report_offer(False)
        self.report_clicked.emit()

    def restore_mode(self) -> str:
        if self._restore.isHidden():
            return ""
        return "redo" if self._undone else "undo"

    def set_changes(self, row: QWidget, animate: bool = True) -> None:



        holder = QWidget(self)
        box = QVBoxLayout(holder)
        box.setContentsMargins(0, SPACE_OUTER, 0, 0)
        box.setSpacing(0)
        box.addWidget(row)
        row.destroyed.connect(holder.deleteLater)
        self._col.insertWidget(self._col.indexOf(self._actions), holder)
        row.show()
        if animate:
            self._fade_up(row)
        self._on_text_height(0)

    def _sync_actions(self) -> None:

        has_sources = bool(self._sources.sources())
        rateable = self._finished and bool(self._text.strip()) and bool(self.run_id)
        for name, thumb in self._thumbs.items():
            thumb.setVisible(rateable and self._rating in ("", name))
        shown = has_sources or not self._restore.isHidden() or not self._report.isHidden() or rateable
        if shown != (not self._actions.isHidden()):
            self._actions.setVisible(shown)
            self._on_text_height(0)

    def set_rating(self, rating: str) -> None:

        if rating not in ("up", "down") or self._rating:
            return
        self._rating = rating
        self._thumbs[rating].lock_chosen()
        self._sync_actions()

    def _on_thumb(self, rating: str) -> None:

        if self._rating:
            return
        self.set_rating(rating)
        self.rated.emit(rating)
        if self._words is None:
            self._words = RatingWords(self)
            self._words.sent.connect(lambda text: self.rating_words.emit(self._rating, text))
            self._col.addWidget(self._words)
            self._words.show()
            self._words.focus()
            self._on_text_height(0)

    def words_done(self, ok: bool) -> None:
        if self._words is not None:
            self._words.done(ok)
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
