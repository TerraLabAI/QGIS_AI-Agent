# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

import os
import time

from qgis.PyQt.QtCore import (
    QT_TRANSLATE_NOOP,
    QAbstractAnimation,
    QCoreApplication,
    QEasingCurve,
    QEvent,
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
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import openui, trace
from ..core.qt_compat import one_shot
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
from .loader import GAP_PX, LINE_PX, DotsLoader, ShimmerLabel
from .markdown_view import MarkdownView
from .shared import AGENT_ACTION
from .source_marks import SourcesButton
from .style import (
    _BTN_PRIMARY,
    _BTN_QUIET,
    ACCENT_INK,
    BTN_SMALL_PX,
    FONT_BODY,
    FONT_MICRO,
    GREEN,
    HIT_PX,
    HOVER,
    HOVER_ON,
    INK,
    INK_2,
    INK_3,
    LINE,
    LINE_STRONG,
    MOTION_FADE_MS,
    RADIUS_CARD,
    RADIUS_CHIP,
    RADIUS_CONTROL,
    RED,
    SPACE_OUTER,
    SPACE_STAGE,
    SPACE_TIGHT,
    SURFACE,
    keyboard_ring,
    medium_font_weight,
    qcolor,
)
from .widgets import FlowLayout, IconButton, placeholder_ink

_USER_BUBBLE_SHARE = 0.85




USER_PILL_RADIUS = 18
_BUBBLE_PAD_X = 10
_BUBBLE_PAD_Y = 6



_BUBBLE_BORDER = 1






_STREAM_INTERVAL_MS = 40
_STREAM_INTERVAL_MAX_MS = 250
_STREAM_DUTY = 4


_STREAM_STATS: dict = {}
_FRAME_MS = 16.7


def take_stream_stats(run_id: str):
    return _STREAM_STATS.pop(run_id, None)


_AGENT_MAX_WIDTH = 720
_IMAGE_THUMB_HEIGHT = 72

SOURCE_PAGE_SCHEME = "library-source:"


def _tag(text: str, parent=None) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("tag")
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
        self._view.chip_private_links()
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
        from qgis.PyQt.QtGui import QTextCharFormat

        document = self._view.document()
        for label, value in sources:
            cursor = document.find("@" + label)
            while not cursor.isNull():
                link = QTextCharFormat()
                link.setAnchor(True)
                link.setAnchorHref(SOURCE_PAGE_SCHEME + value)
                link.setForeground(qcolor(ACCENT_INK))
                link.setFontWeight(medium_font_weight())
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


class _Thumb(IconButton):



    def __init__(self, parent, name: str, tooltip: str, tone: str):
        super().__init__(parent, None, 14, tooltip)
        self.setFixedSize(HIT_PX, HIT_PX)
        self._name, self._tone, self._tooltip = name, tone, tooltip
        self.set_icon(self._name, 14, QColor(INK_3))

    def show_chosen(self, chosen: bool) -> None:
        if chosen:
            self.set_icon(self._name + "-solid", 14, QColor(self._tone))
        else:
            self.set_icon(self._name, 14, QColor(INK_3))
        self.setToolTip(QCoreApplication.translate("AgentBubble", "Remove rating") if chosen else self._tooltip)



_REASONS = (
    ("wrong_result", QT_TRANSLATE_NOOP("RatingCard", "Wrong result")),
    ("not_what_i_asked", QT_TRANSLATE_NOOP("RatingCard", "Not what I asked")),
    ("too_slow", QT_TRANSLATE_NOOP("RatingCard", "Too slow")),
)
_REASON_MAX = 500
_THANKS_MS = 1800

_CARD = (
    f"QFrame#ratingCard {{ background: {SURFACE}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_CARD}px; }}"
    f"QLabel#ratingQuestion {{ color: {INK}; font-size: {FONT_BODY}px; font-weight: 600; }}"
    f"QLabel#ratingNote {{ color: {INK_3}; font-size: {FONT_MICRO}px; }}"
    f"QLabel#ratingThanks {{ color: {INK_2}; font-size: {FONT_BODY}px; }}"
    f"QPlainTextEdit {{ background: transparent; color: {INK}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_CONTROL}px; padding: 4px 6px; font-size: {FONT_BODY}px; }}"
    f"QPlainTextEdit:focus {{ border-color: {LINE_STRONG}; }}"
    f"QPushButton#ratingReason {{ background: transparent; color: {INK_2}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_CHIP}px; padding: 3px 8px; font-size: {FONT_BODY}px; }}"
    f"QPushButton#ratingReason:hover {{ background: {HOVER}; color: {INK}; }}"
    f"QPushButton#ratingReason:checked {{ background: {HOVER_ON}; color: {INK};"
    f" border-color: {LINE_STRONG}; }}"
)


class RatingCard(QFrame):









    sent = pyqtSignal(str, str)
    dismissed = pyqtSignal()
    finished = pyqtSignal()

    def __init__(self, rating: str, parent=None):
        super().__init__(parent)
        self.setObjectName("ratingCard")
        self.setStyleSheet(_CARD)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        col = QVBoxLayout(self)
        col.setContentsMargins(SPACE_STAGE, SPACE_OUTER + 2, SPACE_OUTER, SPACE_STAGE)
        col.setSpacing(SPACE_OUTER)
        self._body = QWidget(self)
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, SPACE_TIGHT, 0)
        body.setSpacing(SPACE_OUTER)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(SPACE_TIGHT)
        asked = self.tr("What went wrong?") if rating == "down" else self.tr("What worked well?")
        self._question = question = QLabel(asked, self)
        question.setObjectName("ratingQuestion")
        question.setWordWrap(True)
        head.addWidget(question, 1)
        self._close = IconButton(self, "lu.x", 14, self.tr("Close"))
        self._close.setFixedSize(HIT_PX, HIT_PX)
        self._close.setProperty(AGENT_ACTION, "rating_close")
        self._close.clicked.connect(self.dismiss)
        head.addWidget(self._close, 0, Qt.AlignmentFlag.AlignTop)
        col.addLayout(head)

        self._reasons: dict[str, QPushButton] = {}
        self._chips = None
        if rating == "down":
            self._chips = QWidget(self._body)
            flow = FlowLayout(self._chips, 6, 6)
            for code, label in _REASONS:
                chip = QPushButton(QCoreApplication.translate("RatingCard", label), self._chips)
                chip.setObjectName("ratingReason")
                chip.setCheckable(True)
                chip.setCursor(Qt.CursorShape.PointingHandCursor)
                chip.setProperty(AGENT_ACTION, "rating_reason_" + code)
                chip.toggled.connect(lambda on, c=code: self._on_reason(c, on))
                flow.addWidget(chip)
                self._reasons[code] = chip
            body.addWidget(self._chips)

        self._field = QPlainTextEdit(self._body)
        self._field.setPlaceholderText(self.tr("Add details (optional)") if rating == "down"
                                       else self.tr("Tell us more (optional)"))
        placeholder_ink(self._field)
        self._field.setTabChangesFocus(True)
        self._field.setFixedHeight(58)
        self._field.textChanged.connect(self._on_text)
        self._field.installEventFilter(self)
        body.addWidget(self._field)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.setSpacing(SPACE_OUTER)

        self._note = QLabel(self.tr("Your words are sent with this chat and recent logs."), self._body)
        self._note.setObjectName("ratingNote")
        self._note.setWordWrap(True)
        foot.addWidget(self._note, 1, Qt.AlignmentFlag.AlignVCenter)
        self._send = QPushButton(self.tr("Send"), self._body)
        self._send.setStyleSheet(_BTN_PRIMARY)
        keyboard_ring(self._send)
        self._send.setFixedHeight(BTN_SMALL_PX)
        self._send.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send.setProperty(AGENT_ACTION, "rating_send")
        self._send.clicked.connect(self._on_send)
        foot.addWidget(self._send, 0, Qt.AlignmentFlag.AlignVCenter)
        body.addLayout(foot)
        col.addWidget(self._body)

        self._thanks = QLabel(self.tr("Thanks for the feedback"), self)
        self._thanks.setObjectName("ratingThanks")
        self._thanks.hide()
        col.addWidget(self._thanks)
        self._busy = False
        self._fade = None
        self._sync()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if self._chips is not None:
            self._chips.setFixedHeight(self._chips.layout().heightForWidth(max(60, self._chips.width())))

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self._field and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.dismiss()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
                    event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)):
                self._on_send()
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.dismiss()
            return
        super().keyPressEvent(event)

    def reason_code(self) -> str:
        return next((code for code, chip in self._reasons.items() if chip.isChecked()), "")

    def text(self) -> str:
        return self._field.toPlainText().strip()[:_REASON_MAX]

    def _on_reason(self, code: str, on: bool) -> None:

        if on:
            for other, chip in self._reasons.items():
                if other != code and chip.isChecked():
                    chip.setChecked(False)
        self._sync()

    def _on_text(self) -> None:
        if len(self._field.toPlainText()) > _REASON_MAX:
            cursor = self._field.textCursor()
            self._field.setPlainText(self._field.toPlainText()[:_REASON_MAX])
            cursor.setPosition(_REASON_MAX)
            self._field.setTextCursor(cursor)
        self._sync()

    def _sync(self) -> None:
        self._send.setEnabled(not self._busy and bool(self.text() or self.reason_code()))

    def _on_send(self) -> None:
        if self._busy or not (self.text() or self.reason_code()):
            return
        self._busy = True
        self._body.setEnabled(False)
        self._sync()
        self.sent.emit(self.reason_code(), self.text())

    def dismiss(self) -> None:
        if not self._busy:
            self.dismissed.emit()

    def done(self, ok: bool) -> None:

        if not self._busy:
            return
        try:
            self._busy = False
            if not ok:
                self._body.setEnabled(True)
                self._note.setText(self.tr("Could not send. Try again."))
                self._sync()
                return
            self._body.hide()
            self._close.hide()
            self._question.hide()
            self.setStyleSheet(_CARD.replace(f"border: 1px solid {LINE};", "border: none;", 1)
                               .replace(f"background: {SURFACE};", "background: transparent;", 1))
            self.layout().setContentsMargins(0, SPACE_TIGHT, 0, 0)
            self._thanks.show()
            one_shot(self, self._fade_out, _THANKS_MS).start()
        except RuntimeError:
            pass

    def _fade_out(self) -> None:
        try:
            effect = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(effect)
            fade = QPropertyAnimation(effect, b"opacity", self)
            fade.setDuration(MOTION_FADE_MS)
            fade.setEasingCurve(QEasingCurve.Type.OutQuint)
            fade.setStartValue(1.0)
            fade.setEndValue(0.0)
            fade.finished.connect(self.finished.emit)
            self._fade = fade
            fade.start()
        except RuntimeError:
            pass


class AgentBubble(QWidget):
















    link_activated = pyqtSignal(str)


    restore_clicked = pyqtSignal()


    rated = pyqtSignal(str)
    rating_words = pyqtSignal(str, str, str)

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
        self._report.setProperty(AGENT_ACTION, "report")
        self._report.setStyleSheet(_BTN_QUIET)
        keyboard_ring(self._report)
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
        self._restore.setProperty(AGENT_ACTION, "restore")
        self._restore.setStyleSheet(_BTN_QUIET)
        keyboard_ring(self._restore)
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
            thumb.setProperty(AGENT_ACTION, "rate_" + rating)
            thumb.clicked.connect(lambda _=False, r=rating: self._on_thumb(r))
            thumb.hide()
            actions.addWidget(thumb, 0, Qt.AlignmentFlag.AlignVCenter)
        self._rating = ""
        self._words: RatingCard | None = None
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
        self._timer = one_shot(self, self._flush_pending, _STREAM_INTERVAL_MS)
        self._interval_ms = _STREAM_INTERVAL_MS
        self._drawn_at = 0.0
        if text:
            self.set_text(text)

    def append_token(self, text: str) -> None:




        if not text or self._finished:
            return
        self._pending.append(text)
        if self._timer.isActive():
            return
        waited_ms = (time.perf_counter() - self._drawn_at) * 1000.0
        if waited_ms >= self._interval_ms:
            self._flush_pending()
        else:
            self._timer.start(max(1, int(self._interval_ms - waited_ms)))

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
            self._interval_ms = int(min(_STREAM_INTERVAL_MAX_MS, max(_STREAM_INTERVAL_MS, spent_ms * _STREAM_DUTY)))
            if self.run_id:
                stats = _STREAM_STATS.setdefault(self.run_id, [0, 0, 0.0, time.perf_counter()])
                stats[0] += 1
                stats[1] += spent_ms > _FRAME_MS
                stats[2] = max(stats[2], spent_ms)
        self._drawn_at = time.perf_counter()
        trace.mark("render", s=time.perf_counter() - started, chars=len(self._text), streaming=streaming)

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
        for thumb in self._thumbs.values():
            thumb.setVisible(rateable)
        shown = has_sources or not self._restore.isHidden() or not self._report.isHidden() or rateable
        if shown != (not self._actions.isHidden()):
            self._actions.setVisible(shown)
            self._on_text_height(0)

    def set_rating(self, rating: str) -> None:

        rating = rating if rating in ("up", "down") else ""
        self._rating = rating
        for name, thumb in self._thumbs.items():
            thumb.show_chosen(name == rating)
        self._sync_actions()

    def _on_thumb(self, rating: str) -> None:


        rating = "" if rating == self._rating else rating
        self.set_rating(rating)
        self.rated.emit(rating)
        self._close_words()
        if not rating:
            return
        card = RatingCard(rating, self)
        card.sent.connect(lambda code, text, r=rating: self.rating_words.emit(r, code, text))
        card.dismissed.connect(self._close_words)
        card.finished.connect(self._close_words)
        self._words = card
        self._col.addWidget(card)
        card.show()
        self._fade_up(card)

        self._on_text_height(0)

    def _close_words(self) -> None:
        card, self._words = self._words, None
        if card is None:
            return
        try:
            card.hide()
            self._col.removeWidget(card)
            card.deleteLater()
        except RuntimeError:
            return
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
            fade.setDuration(MOTION_FADE_MS)
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









    def __init__(self, text: str = "", parent=None, reduced_motion=None):
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
        row.addStretch(1)
        self.setFixedHeight(LINE_PX)
        self.set_text(text)
        self.start()

    def set_text(self, text: str) -> None:


        text = (text or "").rstrip(". \u2026")
        self._label.setText(text or self.tr("Thinking"))

    def text(self) -> str:
        return self._label.text()

    def start(self) -> None:
        motion = not (callable(self._reduced_motion) and self._reduced_motion())
        for part in (self._dots, self._label):
            part.set_motion(motion)
            part.start()

    def stop(self) -> None:
        self._dots.stop()
        self._label.stop()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self.start()

    def cleanup(self) -> None:

        self.stop()
