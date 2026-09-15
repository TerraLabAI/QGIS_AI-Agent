# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""A read-only, frameless, auto-height text view for one message."""























from __future__ import annotations

import bisect
import math
import re

from qgis.PyQt.QtCore import QEvent, QRect, QRectF, QSize, Qt, QUrl, pyqtSignal
from qgis.PyQt.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QFontInfo,
    QPainter,
    QPen,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextDocumentFragment,
    QTextFormat,
    QTextImageFormat,
    QTextLength,
    QTextOption,
    QTextTableFormat,
)
from qgis.PyQt.QtWidgets import QFrame, QSizePolicy, QTextBrowser, QTextEdit

from .font_scale import widget_pixel_ratio
from .icons import ink_of
from .shared import qt_enum_int, resolve_qt_enum
from .source_marks import MARK_PX, source_host, source_mark_pixmap
from .style import FIELD, FONT_HINT, FONT_PROSE, INK_2, INK_3, MONO_FAMILY, qcolor



_CODE_BG = QColor(128, 128, 128, 31)
_CODE_BLOCK_BG = QColor(128, 128, 128, 26)
_CODE_BLOCK_BORDER = QColor(128, 128, 128, 50)
_QUOTE_BAR = QColor(128, 128, 128, 110)
_RULE = QColor(128, 128, 128, 70)
_TABLE_BORDER = QColor(128, 128, 128, 60)


def _format_property(name: str):
    """A QTextFormat property id, flat on Qt5, under ``Property`` on Qt6."""
    value = getattr(QTextFormat, name, None)
    if value is None:
        value = getattr(getattr(QTextFormat, "Property", None), name, None)
    return value


_BLOCK_CODE_LANGUAGE = _format_property("BlockCodeLanguage")
_BLOCK_QUOTE_LEVEL = _format_property("BlockQuoteLevel")
_HR_WIDTH = _format_property("BlockTrailingHorizontalRulerWidth")
_FONT_SIZE_ADJUSTMENT = _format_property("FontSizeAdjustment")
_FONT_PIXEL_SIZE = _format_property("FontPixelSize")


def _markdown_features():
    """GitHub markdown with HTML parsing off, so a ``<`` in a sentence about distances stays a ``<``; None where the dialect flags do not exist."""

    scope = getattr(QTextDocument, "MarkdownFeature", QTextDocument)
    github = getattr(scope, "MarkdownDialectGitHub", None)
    no_html = getattr(scope, "MarkdownNoHTML", None)
    if github is None or no_html is None:
        return None
    try:
        flags = github | no_html

        wrap = getattr(QTextDocument, "MarkdownFeatures", None)
        return wrap(flags) if wrap is not None else flags
    except TypeError:
        return None


def _probe_markdown_features(flags):
    """Some bindings refuse the dialect flags: find out once, on a scratch document, so the first bubble never raises."""

    if flags is None:
        return None
    try:
        from qgis.PyQt.QtGui import QTextDocument
        QTextDocument().setMarkdown("probe", flags)
        return flags
    except TypeError:
        return None


_MARKDOWN_FEATURES = _probe_markdown_features(_markdown_features())






_BODY_PX = FONT_PROSE
_LINE_HEIGHT_PERCENT = 150
_PARAGRAPH_GAP = 9
_ITEM_GAP = 4
_LIST_INDENT = 20
_CODE_PAD = 10
_CODE_RADIUS = 8
_CODE_FONT_SCALE = 0.92
_QUOTE_INDENT = 14
_QUOTE_BAR_WIDTH = 3

_HEADINGS = {
    1: (3, True, 14, 6),
    2: (1, True, 12, 5),
    3: (0, False, 10, 4),
}
_TABLE_CELL_PAD = 5
_RULE_HEIGHT = 12




SOURCE_SCHEME = "source:"



GONE_SCHEME = "gone:"
_CHIP_PAD = "\u00a0"


class MarkdownView(QTextBrowser):
    """Read-only markdown (or monospace plain text) that sizes to its content."""

    link_activated = pyqtSignal(str)


    passage_selected = pyqtSignal(str, object)
    passage_cleared = pyqtSignal()





    height_changed = pyqtSignal(int)

    def __init__(self, parent=None, mono: bool = False, px: int | None = None):
        super().__init__(parent)
        self._mono = mono
        self._px = int(px) if px else _BODY_PX
        self._streaming = False


        self._stream = None
        self._holding = False
        self._had_selection = False
        self.setObjectName("monoText" if mono else "chatText")
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self._on_anchor)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)

        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setAutoFillBackground(False)
        self.viewport().setAutoFillBackground(False)

        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        doc = self.document()


        doc.setUndoRedoEnabled(False)
        doc.setDocumentMargin(6 if mono else 2)

        doc.setIndentWidth(_LIST_INDENT)





        self._panels: tuple = ([], [], [])
        self._panels_key: tuple | None = None
        if mono:
            doc.setDefaultFont(_fixed_font())
        else:
            self._apply_body_font()
        doc.documentLayout().documentSizeChanged.connect(self._on_document_size)
        doc.contentsChanged.connect(self._sync_height)
        self.selectionChanged.connect(self._on_selection_changed)
        self._sync_height()



    def set_markdown(self, text: str) -> None:
        self._stream = None
        doc = self.document()
        if hasattr(doc, "setMarkdown"):
            _import_markdown(doc, text or "")
            self._decorate()
        else:
            doc.setPlainText(text or "")
        self._sync_height()

    def set_plain_text(self, text: str) -> None:
        self._stream = None
        self.document().setPlainText(text or "")
        self._sync_height()

    def stream_markdown(self, text: str) -> None:
        """The document ``set_markdown(text)`` builds, for an answer that grows by its end: only the block still open is parsed again."""












        text = text or ""
        doc = self.document()
        state = self._stream
        if state is None or not text.startswith(state.text):
            state = _Stream()
        state.text = text
        state.cuts.feed(text)
        if state.cuts.pending_ref_def(text):
            state.cuts.stuck = True
        closed = state.cuts.last()
        incremental = (not state.broken and not state.cuts.stuck and closed > 0
                       and hasattr(doc, "setMarkdown"))
        if incremental:


            self._holding = True
            try:
                if state.stable <= 0:

                    _import_markdown(doc, text[:closed])
                    self._decorate()
                    state.close_at(closed, doc)
                state.broken = not self._stream_into(state, closed)
            finally:
                self._holding = False
        if not incremental or state.broken:
            self.set_markdown(text)
            state.stable = 0
        self._stream = state
        self._sync_height()

    def _stream_into(self, state, closed: int) -> bool:
        """Drop the open block, append the blocks closed since, then the open one."""



        cursor = QTextCursor(self.document())
        cursor.setPosition(state.stable_pos)
        kept_block, kept_chars = cursor.blockFormat(), cursor.blockCharFormat()
        cursor.movePosition(_END, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()



        cursor.setBlockFormat(kept_block)
        cursor.setBlockCharFormat(kept_chars)
        if closed > state.stable:
            if not self._append_after_context(state, state.stable, closed):
                return False
            self._decorate(state.stable_pos + 1)
            state.close_at(closed, self.document())
        if not self._append_after_context(state, closed, len(state.text)):
            return False
        self._decorate(state.stable_pos + 1)
        return True

    def _append_after_context(self, state, begin: int, end: int) -> bool:
        """Parse ``text[begin:end]`` after the block that precedes ``begin`` and append what follows that block to the document."""

        start, _ = state.context
        context = state.text[start:begin]


        font = self.document().defaultFont()
        if state.context_mark is None:
            alone = QTextDocument()
            alone.setDefaultFont(font)
            _import_markdown(alone, context)
            last = alone.lastBlock()
            state.context_mark = (alone.characterCount() - 1, last.blockNumber(), last.text())
        mark, number, last_text = state.context_mark
        scratch = QTextDocument()
        scratch.setDefaultFont(font)
        _import_markdown(scratch, context + state.text[begin:end])
        block = scratch.findBlock(mark)
        if (not block.isValid() or block.blockNumber() != number or block.text() != last_text
                or block.position() + block.length() - 1 != mark):
            return False
        selection = QTextCursor(scratch)
        selection.setPosition(mark)
        selection.movePosition(_END, QTextCursor.MoveMode.KeepAnchor)
        if selection.hasSelection():
            cursor = QTextCursor(self.document())
            cursor.movePosition(_END)
            cursor.insertFragment(QTextDocumentFragment(selection))
        return True

    def text(self) -> str:
        return self.document().toPlainText()

    def ideal_width(self) -> int:
        """Width of the widest unwrapped line, for a bubble that hugs its text."""
        doc = self.document().clone(self)
        try:
            doc.setTextWidth(-1)
            width = int(math.ceil(doc.idealWidth()))
        finally:
            doc.deleteLater()
        return width

    def _decorate(self, start: int = 0) -> None:
        """Set the reading rhythm, size the headings, colour the links and tint the code, which setMarkdown leaves bare or HTML-sized."""

















        doc = self.document()
        anchors, inline_code, code_spans, code_blocks = [], [], [], []
        prose, headings, quotes = [], [], []
        empties, rules, sources, gone = [], [], [], []
        block = doc.begin()
        if start > 0:
            block = doc.findBlock(start)
            if not block.isValid():
                block = doc.lastBlock()
            for _ in range(2):
                if block.previous().isValid():
                    block = block.previous()
        while block.isValid():
            block_format = block.blockFormat()
            is_code_block = _is_code_block(block)
            level = block_format.headingLevel() if hasattr(block_format, "headingLevel") else 0
            fresh = block.position() >= start
            if is_code_block:
                code_blocks.append(block.position())
                if fresh:
                    code_spans.append((block.position(), max(0, block.length() - 1)))
            elif _is_rule(block):
                rules.append(block.position())
            elif _is_filler(block):
                empties.append(block.position())
            else:
                is_item = block.textList() is not None

                is_last = not block.next().isValid() or _in_table(doc, block.position())
                prose.append((block.position(), is_item, is_last))
                if level:
                    headings.append((block.position(), max(0, block.length() - 1), level, fresh))
                if _BLOCK_QUOTE_LEVEL is not None and block_format.hasProperty(_BLOCK_QUOTE_LEVEL):
                    quotes.append(block.position())
            it = block.begin() if fresh else block.end()
            while not it.atEnd():
                fragment = it.fragment()
                if fragment.isValid():
                    fmt = fragment.charFormat()
                    span = (fragment.position(), fragment.length())
                    if fmt.isAnchor():
                        href = fmt.anchorHref()
                        if href.startswith(SOURCE_SCHEME):
                            sources.append((fragment.position(), fragment.length(), href))
                        elif href.startswith(GONE_SCHEME):
                            gone.append(span)
                        else:
                            anchors.append(span)
                    elif fmt.fontFixedPitch() and not is_code_block:
                        inline_code.append(span)
                it += 1
            block = block.next()
        base_px = self._body_px()
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        for position, is_item, is_last in prose:
            fmt = QTextBlockFormat()
            fmt.setLineHeight(_LINE_HEIGHT_PERCENT, _proportional_height())
            fmt.setTopMargin(0)
            fmt.setBottomMargin(0 if is_last else (_ITEM_GAP if is_item else _PARAGRAPH_GAP))
            cursor.setPosition(position)
            cursor.mergeBlockFormat(fmt)
        for position, length, level, fresh in headings:
            delta, bold, above, below = _HEADINGS.get(level, _HEADINGS[3])
            block_fmt = QTextBlockFormat()
            block_fmt.setLineHeight(130, _proportional_height())
            block_fmt.setTopMargin(0 if position == 0 else above)
            block_fmt.setBottomMargin(below)
            cursor.setPosition(position)
            cursor.mergeBlockFormat(block_fmt)
            if not fresh:


                continue
            char_fmt = QTextCharFormat()
            if _FONT_SIZE_ADJUSTMENT is not None:
                char_fmt.setProperty(_FONT_SIZE_ADJUSTMENT, 0)
            _set_pixel_size(char_fmt, base_px + delta)
            char_fmt.setFontWeight(_weight(bold))
            cursor.setPosition(position)
            cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
            cursor.mergeCharFormat(char_fmt)
        for position in quotes:
            fmt = QTextBlockFormat()
            fmt.setLeftMargin(_QUOTE_INDENT)
            fmt.setRightMargin(0)
            cursor.setPosition(position)
            cursor.mergeBlockFormat(fmt)


        for position in empties:
            fmt = QTextBlockFormat()
            fmt.setLineHeight(0, _fixed_height())
            fmt.setTopMargin(0)
            fmt.setBottomMargin(0)
            cursor.setPosition(position)
            cursor.mergeBlockFormat(fmt)

        for position in rules:
            fmt = QTextBlockFormat()
            fmt.setLineHeight(_RULE_HEIGHT, _fixed_height())
            fmt.setTopMargin(0)
            fmt.setBottomMargin(_PARAGRAPH_GAP)
            if _HR_WIDTH is not None:
                fmt.setProperty(_HR_WIDTH, QTextLength(resolve_qt_enum(QTextLength, "Type", "FixedLength"), 0))
            cursor.setPosition(position)
            cursor.mergeBlockFormat(fmt)
        link_format = QTextCharFormat()
        link_format.setForeground(QBrush(ink_of(self)))
        link_format.setFontUnderline(True)
        for position, length in anchors:
            cursor.setPosition(position)
            cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
            cursor.mergeCharFormat(link_format)


        gone_format = QTextCharFormat()
        gone_format.setAnchor(False)
        gone_format.setAnchorHref("")
        gone_format.setFontUnderline(False)
        gone_format.setForeground(QBrush(qcolor(INK_3)))
        for position, length in gone:
            cursor.setPosition(position)
            cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
            cursor.mergeCharFormat(gone_format)
        mono_px = max(9, int(round(base_px * _CODE_FONT_SCALE)))
        code_format = QTextCharFormat()
        code_format.setBackground(QBrush(_CODE_BG))
        _set_family(code_format, _fixed_font().family())
        _set_pixel_size(code_format, mono_px)
        for position, length in inline_code:
            cursor.setPosition(position)
            cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
            cursor.mergeCharFormat(code_format)
        block_code_format = QTextCharFormat()
        _set_family(block_code_format, _fixed_font().family())
        _set_pixel_size(block_code_format, mono_px)
        for position, length in code_spans:
            cursor.setPosition(position)
            cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
            cursor.mergeCharFormat(block_code_format)
        for position in code_blocks:
            block_format = QTextBlockFormat()
            block_format.setLeftMargin(_CODE_PAD)
            block_format.setRightMargin(_CODE_PAD)
            block_format.setLineHeight(130, _proportional_height())


            current = doc.findBlock(position)
            first = not _is_code_block(current.previous())
            last = not _is_code_block(current.next())
            block_format.setTopMargin(_CODE_PAD if first else 0)
            block_format.setBottomMargin(_CODE_PAD + _PARAGRAPH_GAP if last else 0)
            cursor.setPosition(position)
            cursor.mergeBlockFormat(block_format)
        cursor.endEditBlock()
        self._decorate_tables(start)
        self._insert_source_chips(sources)

    def _insert_source_chips(self, sources: list) -> None:
        """Turn each ``source:`` link into the site's inline chip: the mark, then the host in mono on the field, the whole thing the anchor."""






        if not sources or self._mono:
            return
        doc = self.document()
        ratio = widget_pixel_ratio(self)
        image_kind = resolve_qt_enum(QTextDocument, "ResourceType", "ImageResource")
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        for position, length, href in sorted(sources, reverse=True):
            url = href[len(SOURCE_SCHEME):]
            host = source_host(url)
            chip = QTextCharFormat()
            chip.setAnchor(True)
            chip.setAnchorHref(href)
            chip.setFontUnderline(False)
            chip.setForeground(QBrush(qcolor(INK_2)))
            chip.setBackground(QBrush(qcolor(FIELD)))
            _set_family(chip, MONO_FAMILY.split(",")[0].strip().strip("'"))
            _set_pixel_size(chip, FONT_HINT)
            cursor.setPosition(position)
            cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
            cursor.setCharFormat(chip)
            cursor.setPosition(position + length)
            cursor.insertText(_CHIP_PAD, chip)
            name = f"source-mark:{host}"
            doc.addResource(image_kind, QUrl(name), source_mark_pixmap(host, MARK_PX, ratio))
            mark = QTextImageFormat()
            mark.setName(name)
            mark.setWidth(MARK_PX)
            mark.setHeight(MARK_PX)
            mark.setAnchor(True)
            mark.setAnchorHref(href)
            mark.setVerticalAlignment(resolve_qt_enum(QTextCharFormat, "VerticalAlignment", "AlignMiddle"))
            cursor.setPosition(position)
            cursor.insertText(_CHIP_PAD, chip)
            cursor.insertImage(mark)
            cursor.insertText(_CHIP_PAD, chip)
        cursor.endEditBlock()

    def _decorate_tables(self, start: int = 0) -> None:
        """Hairline borders, collapsed, with a little padding in each cell."""
        doc = self.document()
        for frame in _frames(doc.rootFrame()):
            if not hasattr(frame, "columns") or frame.lastPosition() < start:
                continue
            fmt = QTextTableFormat(frame.format())
            fmt.setBorder(1)
            fmt.setBorderBrush(QBrush(_TABLE_BORDER))
            fmt.setCellPadding(_TABLE_CELL_PAD)
            fmt.setCellSpacing(0)
            if hasattr(fmt, "setBorderCollapse"):
                fmt.setBorderCollapse(True)
            fmt.setTopMargin(2)
            fmt.setBottomMargin(_PARAGRAPH_GAP)
            frame.setFormat(fmt)



    def _body_px(self) -> int:
        px = self.document().defaultFont().pixelSize()
        if px <= 0:
            px = self.fontMetrics().height() - 3
        return max(10, px)

    def _apply_body_font(self) -> None:
        """The reply reads one pixel above the panel's base size; a font scale above the default keeps its own size."""


        font = QFont(self.font())
        px = font.pixelSize()
        if px <= 0:


            px = QFontInfo(font).pixelSize()
        font.setPixelSize(max(px, self._px))
        self.document().setDefaultFont(font)

    def changeEvent(self, event):  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange and not self._mono:
            self._apply_body_font()
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.PaletteChange):



            self._stream = None



    def paintEvent(self, event):  # noqa: N802 - Qt override
        """The rounded panel behind a fenced code block and the bar beside a quote, under the text."""

        if not self._mono:
            code, quotes, rules = self._panel_rects()
            if code or quotes or rules:
                painter = QPainter(self.viewport())
                try:
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    painter.setPen(QPen(_CODE_BLOCK_BORDER, 1))
                    painter.setBrush(QBrush(_CODE_BLOCK_BG))
                    for rect in code:
                        painter.drawRoundedRect(rect, _CODE_RADIUS, _CODE_RADIUS)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QBrush(_QUOTE_BAR))
                    for rect in quotes:
                        painter.drawRoundedRect(rect, 1.5, 1.5)
                    painter.setBrush(QBrush(_RULE))
                    for rect in rules:
                        painter.drawRect(rect)
                finally:
                    painter.end()
        super().paintEvent(event)



    def set_streaming(self, streaming: bool) -> None:
        """Whether the answer is still arriving. Nothing is drawn for it."""
        self._streaming = bool(streaming)

    def is_streaming(self) -> bool:
        return self._streaming



    def selected_text(self) -> str:
        return self.textCursor().selectedText().replace("\u2029", "\n")

    def selection_rect(self) -> QRect:
        """The selection's bounding box in global coordinates."""
        cursor = self.textCursor()
        start = QTextCursor(cursor)
        start.setPosition(cursor.selectionStart())
        end = QTextCursor(cursor)
        end.setPosition(cursor.selectionEnd())
        rect = self.cursorRect(start).united(self.cursorRect(end))
        if rect.height() > self.cursorRect(start).height():

            rect.setLeft(0)
            rect.setRight(self.viewport().width())
        return QRect(self.viewport().mapToGlobal(rect.topLeft()), rect.size())

    def _on_selection_changed(self) -> None:
        has = self.textCursor().hasSelection() and bool(self.selected_text().strip())
        if self._had_selection and not has:
            self.passage_cleared.emit()
        self._had_selection = has

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton or self._mono:
            return
        text = self.selected_text()
        if self.textCursor().hasSelection() and text.strip():
            self.passage_selected.emit(text, self.selection_rect())

    def _panel_rects(self) -> tuple:
        """Viewport rectangles of the code panels and quote bars, one per run of consecutive blocks."""

        doc = self.document()
        layout = doc.documentLayout()
        width = self.viewport().width()
        try:
            key = (doc.revision(), width, layout.documentSize().height())
        except Exception:  # noqa: BLE001 - a document that will not answer is simply not cached
            key = None
        if key is not None and key == self._panels_key:
            return self._panels
        margin = doc.documentMargin()
        code, quotes, rules = [], [], []
        code_run = quote_run = None
        block = doc.begin()
        while block.isValid():
            fmt = block.blockFormat()
            is_code = _is_code_block(block)
            is_quote = (not is_code and _BLOCK_QUOTE_LEVEL is not None
                        and fmt.hasProperty(_BLOCK_QUOTE_LEVEL))
            rect = layout.blockBoundingRect(block)
            if is_code:
                top = rect.top() - (fmt.topMargin() if code_run is None else 0)
                bottom = rect.bottom() + (fmt.bottomMargin() - _PARAGRAPH_GAP
                                          if not _is_code_block(block.next()) else 0)
                code_run = (top, bottom) if code_run is None else (code_run[0], bottom)
                if not _is_code_block(block.next()):
                    code.append(QRectF(margin + 0.5, code_run[0] + 0.5,
                                       max(1, width - 2 * margin - 1), code_run[1] - code_run[0]))
                    code_run = None
            elif _is_rule(block):
                y = rect.top() + rect.height() / 2.0
                rules.append(QRectF(margin, y, max(1, width - 2 * margin), 1))
            elif is_quote:
                quote_run = (rect.top(), rect.bottom()) if quote_run is None else (quote_run[0], rect.bottom())
                nxt = block.next()
                if not (nxt.isValid() and _BLOCK_QUOTE_LEVEL is not None
                        and nxt.blockFormat().hasProperty(_BLOCK_QUOTE_LEVEL)):
                    quotes.append(QRectF(margin + 2, quote_run[0], _QUOTE_BAR_WIDTH,
                                         quote_run[1] - quote_run[0]))
                    quote_run = None
            block = block.next()
        self._panels = (code, quotes, rules)
        self._panels_key = key
        return self._panels



    def _on_document_size(self, _size) -> None:
        self._sync_height()

    def _sync_height(self) -> None:
        if self._holding:
            return
        doc = self.document()
        margin = int(2 * doc.documentMargin())
        one_line = self.fontMetrics().lineSpacing() + margin
        height = int(math.ceil(doc.size().height())) + 2 * self.frameWidth()
        height = max(height, one_line)
        if height != self.height() or self.maximumHeight() != height:
            self.setFixedHeight(height)
            self.height_changed.emit(height)

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._sync_height()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(40, self.height())

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(super().sizeHint().width(), self.height())

    def wheelEvent(self, event):  # noqa: N802 - Qt override

        event.ignore()



    def _on_anchor(self, url: QUrl) -> None:
        href = url.toString()[:4096]
        self.link_activated.emit(href)
        if href.startswith(SOURCE_SCHEME):

            url = QUrl(href[len(SOURCE_SCHEME):])
        if url.scheme() in ("http", "https", "mailto") and not url.userInfo():

            if url.scheme() == "mailto" or bool(url.host()):





                from .external_links import open_email, open_external_url

                if url.scheme() == "mailto":
                    open_email(url.toString(), url.path(), self)
                else:
                    open_external_url(url.toString(), self)


def _line_height_type(name: str, fallback: int) -> int:
    """The integer QTextBlockFormat.setLineHeight takes for this line height type."""





    return qt_enum_int(QTextBlockFormat, "LineHeightTypes", name, fallback)


def _proportional_height() -> int:
    return _line_height_type("ProportionalHeight", 1)


def _fixed_height() -> int:
    return _line_height_type("FixedHeight", 2)


def _weight(bold: bool):
    """Bold for the big headings, semibold for the small one."""
    if bold:
        return resolve_qt_enum(QFont, "Weight", "Bold")
    return resolve_qt_enum(QFont, "Weight", "DemiBold")


def _fixed_font() -> QFont:
    fixed = resolve_qt_enum(QFontDatabase, "SystemFont", "FixedFont")
    return QFontDatabase.systemFont(fixed)


def _set_pixel_size(fmt: QTextCharFormat, px: int) -> None:
    """A pixel size on a char format, whichever property the binding has."""
    if _FONT_PIXEL_SIZE is not None:
        fmt.setProperty(_FONT_PIXEL_SIZE, int(px))
    else:
        fmt.setFontPointSize(int(px) * 0.75)


def _set_family(fmt: QTextCharFormat, family: str) -> None:
    if hasattr(fmt, "setFontFamilies"):
        fmt.setFontFamilies([family])
    else:
        fmt.setFontFamily(family)


def _is_code_block(block) -> bool:
    """A fenced code line."""

    if not block.isValid():
        return False
    fmt = block.blockFormat()
    tagged = bool(fmt.nonBreakableLines() or (
        _BLOCK_CODE_LANGUAGE is not None and fmt.hasProperty(_BLOCK_CODE_LANGUAGE)))
    if tagged and not block.text():
        nxt = block.next()
        if not nxt.isValid():
            return False
        nxt_fmt = nxt.blockFormat()
        return bool(nxt_fmt.nonBreakableLines() or (
            _BLOCK_CODE_LANGUAGE is not None and nxt_fmt.hasProperty(_BLOCK_CODE_LANGUAGE)))
    return tagged


def _is_rule(block) -> bool:
    """A horizontal rule: an empty block the importer tagged with a ruler width."""


    return _HR_WIDTH is not None and block.blockFormat().hasProperty(_HR_WIDTH)


def _is_filler(block) -> bool:
    """An empty block that is neither a rule nor a real paragraph: what the importer leaves around a table, after a fence, and at the very end."""

    return not block.text() and block.textList() is None and not _is_rule(block)


def _in_table(doc, position: int) -> bool:
    cursor = QTextCursor(doc)
    cursor.setPosition(position)
    return cursor.currentTable() is not None


def _frames(frame):
    """Every frame under ``frame``, tables included, depth first."""
    for child in frame.childFrames():
        yield child
        yield from _frames(child)


def _import_markdown(doc, text: str) -> None:
    """setMarkdown with the dialect flags when the binding takes them."""
    if _MARKDOWN_FEATURES is None:
        doc.setMarkdown(soft_breaks(text))
    else:
        doc.setMarkdown(soft_breaks(text), _MARKDOWN_FEATURES)


_END = resolve_qt_enum(QTextCursor, "MoveOperation", "End")
_FENCE_LINE = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")
_LIST_ITEM = re.compile(r"^ {0,3}(?:[-*+]|\d{1,9}[.)])(?:[ \t]|$)")
_ATX_HEADING = re.compile(r"^ {0,3}#{1,6}(?:[ \t]|$)")
_TABLE_RULE = re.compile(r"^ {0,3}\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")




_LINK_REF_DEF = re.compile(r"^ {0,3}\[[^\[\]]+\]:\s*\S")


class _Stream:
    """What ``stream_markdown`` built last: the text, where it can be cut, and how much of it the document holds for good."""


    def __init__(self):
        self.text = ""
        self.cuts = _StreamCuts()
        self.stable = 0
        self.stable_pos = 0
        self.context = (0, 0)
        self.context_mark = None
        self.broken = False

    def close_at(self, offset: int, doc) -> None:
        self.context = (self.cuts.before(offset), offset)
        self.context_mark = None
        self.stable = offset
        self.stable_pos = doc.characterCount() - 1


class _StreamCuts:
    """Where a growing answer can be cut into parts that render apart the way they render together, read one complete line at a time."""
















    def __init__(self):
        self.offsets: list[int] = []
        self.stuck = False
        self._read = 0
        self._prev = "start"
        self._fence = None
        self._toggle = False
        self._list = False
        self._pipe = False
        self._table = False

    def last(self) -> int:
        return self.offsets[-1] if self.offsets else 0

    def before(self, offset: int) -> int:
        index = bisect.bisect_left(self.offsets, offset)
        return self.offsets[index - 1] if index > 0 else 0

    def pending_ref_def(self, text: str) -> bool:
        """Whether the line still being typed, its newline not in yet, already reads as a link reference definition."""




        return self._fence is None and bool(_LINK_REF_DEF.match(text[self._read:]))

    def feed(self, text: str) -> None:
        pos = self._read
        while not self.stuck:
            end = text.find("\n", pos)
            if end < 0:
                break
            self._line(text[pos:end], pos)
            pos = end + 1
        self._read = pos

    def _line(self, line: str, offset: int) -> None:
        stripped = line.strip()
        toggles = stripped.startswith("```") or stripped.startswith("~~~")
        if self._fence is not None:
            marker, length, indent = self._fence
            if toggles:
                self._toggle = not self._toggle
            match = _FENCE_LINE.match(line)
            if (match and match.group(2)[0] == marker and len(match.group(2)) >= length
                    and not match.group(3).strip()):
                self._fence = None



                self._prev = "fence_close" if not indent and not self._list else "text"
            elif stripped and len(line) - len(line.lstrip(" ")) < indent:
                self.stuck = True
            return
        if stripped and not self._toggle and line[0] not in " \t" and (
                (self._prev == "blank" and not (self._list and _LIST_ITEM.match(line)))
                or self._prev in ("heading", "fence_close")
                or (self._prev == "text" and not self._table and line[0] == "#"
                    and _ATX_HEADING.match(line))):
            self.offsets.append(offset)
            self._list = False
        if toggles:
            self._toggle = not self._toggle
        if not stripped:
            self._prev = "blank"
            self._pipe = self._table = False
            return
        if _LINK_REF_DEF.match(line):
            self.stuck = True
            return
        match = _FENCE_LINE.match(line)
        if match and not (match.group(2)[0] == "`" and "`" in match.group(3)):
            if self._table:
                self.stuck = True
                return
            self._fence = (match.group(2)[0], len(match.group(2)), len(match.group(1)))
            self._prev = "fence_open"
            return
        if _ATX_HEADING.match(line):
            if self._table:
                self.stuck = True
                return
            self._prev = "heading" if line[0] == "#" else "text"
            return
        if self._pipe and _TABLE_RULE.match(line):
            self._table = True
        if "|" in line:
            self._pipe = True
        if _LIST_ITEM.match(line):
            self._list = True
        self._prev = "text"


_LIST_OR_HEADING = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|#{1,6}\s|>|\|)")


def soft_breaks(text: str) -> str:
    """A single newline between two text lines becomes a hard break."""





    out, in_fence = [], False
    lines = text.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            out.append(line)
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        joinable = (not in_fence and stripped and nxt.strip()
                    and not _LIST_OR_HEADING.match(line)
                    and not _LIST_OR_HEADING.match(nxt)
                    and not line.endswith("  ") and not line.endswith("\\"))
        out.append(line + "  " if joinable else line)
    return "\n".join(out)
