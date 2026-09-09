# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The text box of the composer: one to six lines, Enter sends, ``@`` names a thing."""



































from __future__ import annotations

import sys

from qgis.PyQt.QtCore import QEvent, QRectF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QFont, QPainter, QTextCharFormat, QTextCursor, QTextFormat, QTextOption
from qgis.PyQt.QtWidgets import QFrame, QPlainTextEdit, QWidget

from .icons import icon_for
from .mention_search import FILES, LAYER, PLUGIN, SOURCE, layer_rows, sheet_rows
from .mention_sheet import MentionSheet, popup_keys
from .paste_text import clean_pasted_text
from .style import ACCENT_INK, ACCENT_TINT, RADIUS_CHIP, accent_color, qcolor

MIN_LINES = 1
MAX_LINES = 6




_CHIP_KIND = QTextFormat.Property.UserProperty + 1
_CHIP_VALUE = QTextFormat.Property.UserProperty + 2
_CHIP_LABEL = QTextFormat.Property.UserProperty + 3
_CHIP_GLYPH_NAME = QTextFormat.Property.UserProperty + 4


_CHIP_PAD = 3
_CHIP_GLYPH = 13


_CHIP_GLYPH_ROOM = _CHIP_GLYPH + 3


EMPTY_CHAT_LINES = 2


class ComposerInput(QPlainTextEdit):
    """Auto-height plain text input with Enter-to-send and ``@`` mentions."""





    submitted = pyqtSignal()
    mention_chosen = pyqtSignal(object)

    mentions_changed = pyqtSignal()

    files_requested = pyqtSignal()

    recall_requested = pyqtSignal()
    height_changed = pyqtSignal()


    image_pasted = pyqtSignal(object)
    paths_pasted = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("composerInput")
        self.setFrameShape(QFrame.Shape.NoFrame)

        self.setAcceptDrops(False)

        self.setTabChangesFocus(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.document().setDocumentMargin(4)



        self._min_lines = MIN_LINES


        self._send_on_modifier = False
        self._provider = None
        self._static_items: list = []


        self._anchor: QWidget | None = None


        self._mode = "@"


        self._spans: list[tuple] = []
        self._has_chips = False

        self._plugins: list | None = None






        self._candidates: list | None = None
        self._connector_rows: list | None = None
        self._sheet = MentionSheet(self)
        self._sheet.picked.connect(self._insert_mention)

        self.textChanged.connect(self._sync_height)
        self.textChanged.connect(self._sync_spans)
        self._sync_height()



    def set_mention_provider(self, provider) -> None:
        """``provider() -> [{kind, label, value, detail?}]``, asked on every ``@``."""
        self._provider = provider if callable(provider) else None
        self._candidates = None

    def set_sheet_anchor(self, widget: QWidget | None) -> None:
        """The widget the sheet opens above: the composer frame."""
        self._anchor = widget

    def set_mentions(self, items) -> None:
        """Fill the ``@`` sheet by hand: ``[{kind, label, value, detail?}]``."""




        self._static_items = [dict(i) for i in items or [] if isinstance(i, dict)]
        self._candidates = None
        self._sheet.set_rows(self._static_items)

    def sheet(self) -> MentionSheet:
        return self._sheet

    def _connectors(self) -> list:
        """Every connector the server named, never raising."""
        try:
            from .shared import get_connectors

            return get_connectors()
        except Exception:  # noqa: BLE001 - the sheet must open with the plugins alone
            return []

    def _plugin_candidates(self) -> list:
        """The QGIS plugins the agent can actually drive here, merged."""










        if self._plugins is not None:
            return self._plugins
        self._plugins = []
        try:
            from ..core.qgis_plugins import installed_plugins, plugin_logo
            from .shared import PLUGIN_FOLDERS, get_hidden_plugins, known_plugins_by_folder

            local = installed_plugins()
            known = known_plugins_by_folder()
            skip = set(PLUGIN_FOLDERS) | get_hidden_plugins()
        except Exception:  # noqa: BLE001 - the sheet must open with the connectors alone
            return []
        rows = []
        for folder, row in (local or {}).items():
            if folder in skip or not row.get("loaded"):
                continue
            merged = dict(known.get(folder) or {})
            merged.update(row)
            if not str(merged.get("icon") or "").strip():
                try:
                    merged["icon"] = plugin_logo(folder)
                except Exception:  # noqa: BLE001 - a missing logo is a glyph
                    merged["icon"] = ""
            rows.append(merged)
        self._plugins = rows
        return rows

    def _visible_layer_ids(self) -> set:
        """The layers ticked in the Layers panel, for the ``+`` sheet's order."""
        try:
            from qgis.core import QgsProject

            root = QgsProject.instance().layerTreeRoot()
            return {str(node.layerId()) for node in root.findLayers() if node.isVisible()}
        except Exception:  # noqa: BLE001 - optional QGIS context
            return set()

    def _refresh_mentions(self, word: str = "") -> None:
        """Ask the provider of the open trigger, then rank for what was typed."""
        if self._candidates is None:
            self._candidates = (self._ask(self._provider) if self._provider is not None
                                else list(self._static_items))
        items = self._candidates
        if self._mode == LAYER:
            self._sheet.set_rows(layer_rows(word, items, self._visible_layer_ids()))
            return
        if self._connector_rows is None:
            self._connector_rows = self._connectors() if self._provider is not None else []
        connectors = self._connector_rows


        rows = sheet_rows(word, items, connectors, self._plugin_candidates(),
                          {"sources": self.tr("Connectors"), "plugins": self.tr("QGIS plugins")})
        self._sheet.set_rows(rows)

    @staticmethod
    def _ask(provider) -> list:
        try:
            result = provider() or []
            return list(result)[:1000]
        except Exception:  # noqa: BLE001 - a broken provider must not break typing
            return []

    def begin_mention(self, mode: str = "@") -> None:
        """Insert ``@`` at the caret (with a space before it if needed) and open the sheet."""
        self._mode = mode
        self._plugins = None
        self._candidates = None
        self._connector_rows = None
        cursor = self.textCursor()
        cursor.setCharFormat(QTextCharFormat())
        before = cursor.block().text()[:cursor.positionInBlock()]
        text = "@" if (not before or before[-1].isspace()) else " @"
        cursor.insertText(text, QTextCharFormat())
        self.setTextCursor(cursor)
        self.setCurrentCharFormat(QTextCharFormat())
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self._update_completion()

    def begin_layer_mention(self) -> None:
        """The ``+`` sheet's "Add a layer": the same sheet, on this project's layers."""
        self.begin_mention(LAYER)

    def _mention_prefix(self):
        """``(start, word)`` of the ``@word`` under the caret, or None."""
        return self._trigger_prefix("@")

    def _trigger_prefix(self, trigger: str):
        """``(start, word)`` of the ``<trigger>word`` under the caret."""
        cursor = self.textCursor()
        before = cursor.block().text()[:cursor.positionInBlock()]
        at = before.rfind(trigger)
        if at < 0:
            return None
        if at > 0 and not before[at - 1].isspace():
            return None
        word = before[at + 1:]
        if any(ch.isspace() for ch in word):
            return None
        return cursor.position() - len(word) - 1, word

    def _update_completion(self) -> None:
        found = self._mention_prefix()
        if found is None:
            self._close_sheet()
            return
        _start, word = found
        self._refresh_mentions(word)
        if self._sheet.row_count() == 0:
            self._close_sheet()
            return
        self._sheet.open_above(self._anchor if self._anchor is not None else self)

    def _close_sheet(self) -> None:
        """The sheet goes and the ``@`` means the connectors again."""
        self._sheet.hide()
        self._mode = "@"
        self._plugins = None
        self._candidates = None
        self._connector_rows = None

    def _insert_mention(self, data) -> None:
        """The pick becomes a chip in the text."""
        if not isinstance(data, dict) or data.get("kind") == "header":
            return
        found = self._mention_prefix()
        cursor = self.textCursor()
        if found is not None:
            start, word = found
            cursor.setPosition(start)
            cursor.setPosition(start + len(word) + 1, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
        self.setTextCursor(cursor)
        self._mode = "@"
        if data.get("kind") == FILES:
            self.files_requested.emit()
            return
        self.insert_chip(data)
        self.mention_chosen.emit(data)



    def insert_chip(self, data: dict) -> bool:
        """Write ``@Name`` at the caret as a chip carrying ``{kind, label, value}``."""




        if not isinstance(data, dict):
            return False
        kind = str(data.get("kind") or "")
        value = str(data.get("value") or "")
        label = str(data.get("label") or value)
        if not value or kind in ("header", FILES):
            return False
        if any(span[2] == kind and span[3] == value for span in self._spans):

            return False
        cursor = self.textCursor()
        before = cursor.block().text()[:cursor.positionInBlock()]
        if before and not before[-1].isspace():
            cursor.insertText(" ", QTextCharFormat())
        body = QTextCharFormat()
        body.setProperty(_CHIP_KIND, kind)
        body.setProperty(_CHIP_VALUE, value)
        body.setProperty(_CHIP_LABEL, label)
        body.setProperty(_CHIP_GLYPH_NAME, str(data.get("glyph") or ""))
        body.setForeground(qcolor(ACCENT_INK))
        body.setFontWeight(QFont.Weight.Medium)
        cursor.insertText("@", self._mark_format(body, data))
        cursor.insertText(label, body)
        cursor.insertText(" ", QTextCharFormat())
        self.setTextCursor(cursor)
        self.setCurrentCharFormat(QTextCharFormat())
        self._has_chips = True
        self._sync_spans()
        self.mentions_changed.emit()
        return True

    def _mark_format(self, body: QTextCharFormat, data: dict) -> QTextCharFormat:
        """The format of the chip's ``@``: room for the glyph painted over it."""






        mark = QTextCharFormat(body)
        if self._chip_icon(data) is None:
            return mark
        try:
            room = _CHIP_GLYPH_ROOM - self.fontMetrics().horizontalAdvance("@")
            if room > 0:
                mark.setFontLetterSpacing(float(room))
                mark.setFontLetterSpacingType(QFont.SpacingType.AbsoluteSpacing)
            mark.setForeground(qcolor("rgba(0, 0, 0, 0)"))
        except (AttributeError, TypeError):
            return QTextCharFormat(body)
        return mark

    def _chip_icon(self, data: dict):
        """The glyph a chip wears, or None when the row named none."""
        glyph = str(data.get("glyph") or "")
        kind = str(data.get("kind") or "")
        if not glyph:
            glyph = {SOURCE: "globe", PLUGIN: "package", LAYER: "layers"}.get(kind, "")
        if not glyph:
            return None
        icon = icon_for(self, glyph, _CHIP_GLYPH, accent_color())
        return None if icon.isNull() else icon

    def mentions(self) -> list[dict]:
        """The chips in the text, in order: ``[{kind, label, value}]``."""
        out, seen = [], set()
        for _start, _end, kind, value, label, _glyph in self._spans:
            key = (kind, value)
            if key in seen:
                continue
            seen.add(key)
            out.append({"kind": kind, "label": label, "value": value})
        return out

    def _sync_spans(self) -> None:
        """Recompute where the chips sit; the painter and ``mentions`` read this."""
        spans = self._read_spans() if self._has_chips else []
        if spans != self._spans:
            changed = [(s[2], s[3]) for s in spans] != [(s[2], s[3]) for s in self._spans]
            self._spans = spans
            if changed:
                self.mentions_changed.emit()
        if not spans:
            self._has_chips = False

    def _read_spans(self) -> list[tuple]:
        """Where the chips sit, read off the document."""





        text = self.toPlainText()
        cursor = QTextCursor(self.document())
        spans: list[tuple] = []
        at = text.find("@")
        while at >= 0:
            cursor.setPosition(at + 1)
            fmt = cursor.charFormat()
            value = str(fmt.property(_CHIP_VALUE) or "")
            if not value:
                at = text.find("@", at + 1)
                continue
            end = at + 1
            while end < len(text):
                cursor.setPosition(end + 1)
                if str(cursor.charFormat().property(_CHIP_VALUE) or "") != value:
                    break
                end += 1
            spans.append((at, end, str(fmt.property(_CHIP_KIND) or ""), value,
                          str(fmt.property(_CHIP_LABEL) or ""),
                          str(fmt.property(_CHIP_GLYPH_NAME) or "")))
            at = text.find("@", end)
        return spans

    def _span_at(self, position: int, edge: str):
        """The chip a Backspace (``end``) or a Delete (``start``) would bite into."""




        for span in self._spans:
            if edge == "end" and span[0] < position <= span[1]:
                return span
            if edge == "start" and span[0] <= position < span[1]:
                return span
        return None

    def _take_chip(self, span) -> None:
        """Delete a whole chip, and the space it was written with."""
        start, end = span[0], span[1]
        text = self.toPlainText()
        if end < len(text) and text[end] == " ":
            end += 1
        elif start > 0 and text[start - 1] == " ":
            start -= 1
        cursor = self.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        self.setTextCursor(cursor)
        self.setCurrentCharFormat(QTextCharFormat())



    def canInsertFromMimeData(self, source) -> bool:  # noqa: N802 - Qt override
        return bool(source.hasImage() or source.hasUrls()) or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):  # noqa: N802 - Qt override
        if source.hasImage():
            from qgis.PyQt.QtGui import QImage

            data = source.imageData()
            image = data.toImage() if hasattr(data, "toImage") else QImage(data)
            if not image.isNull():
                self.image_pasted.emit(image)
            return
        if source.hasUrls():
            paths = [u.toLocalFile() for u in source.urls() if u.isLocalFile() and u.toLocalFile()]
            if paths:
                self.paths_pasted.emit(paths)
                return



        text = clean_pasted_text(source.text())
        if text:
            self.insertPlainText(text)



    def event(self, event):  # noqa: N802 - Qt override




        try:
            key_press = event.type() == QEvent.Type.KeyPress
        except (AttributeError, RuntimeError):
            key_press = False
        if key_press and self._sheet.isVisible() and event.key() in (
                Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self._sheet_key(Qt.Key.Key_Tab)
            return True
        return super().event(event)

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        if self._sheet.isVisible() and event.key() in popup_keys():
            self._sheet_key(event.key())
            return
        if self._spans and self._chip_key(event):
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):




            modifiers = event.modifiers()
            with_modifier = bool(modifiers & (Qt.KeyboardModifier.ControlModifier
                                              | Qt.KeyboardModifier.MetaModifier))
            if self._send_on_modifier:
                if with_modifier:
                    self.submitted.emit()
                    return
            elif not (modifiers & Qt.KeyboardModifier.ShiftModifier):
                self.submitted.emit()
                return
        if event.key() == Qt.Key.Key_Up and not self.toPlainText():
            self.recall_requested.emit()
            return
        if self._word_delete(event):
            self._update_completion()
            return
        super().keyPressEvent(event)
        self._update_completion()

    def _word_delete(self, event) -> bool:
        """Ctrl+Backspace and Ctrl+Delete take a word, on a Mac too."""








        if event.key() not in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            return False
        modifiers = event.modifiers()
        wanted = Qt.KeyboardModifier.MetaModifier
        if sys.platform != "darwin":
            wanted |= Qt.KeyboardModifier.ControlModifier
        if not (modifiers & wanted):
            return False
        cursor = self.textCursor()
        if not cursor.hasSelection():
            backward = event.key() == Qt.Key.Key_Backspace
            span = self._span_at(cursor.position(), "end" if backward else "start")
            if span is None:
                probe = QTextCursor(cursor)
                probe.movePosition(QTextCursor.MoveOperation.PreviousWord if backward
                                   else QTextCursor.MoveOperation.NextWord)
                span = self._span_at(probe.position(), "end" if backward else "start")
            if span is not None:
                self._take_chip(span)
                return True
            cursor.movePosition(QTextCursor.MoveOperation.PreviousWord if backward
                                else QTextCursor.MoveOperation.NextWord,
                                QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        self.setTextCursor(cursor)
        return True

    def _chip_key(self, event) -> bool:
        """Backspace and Delete take a whole chip; typing never joins one."""






        cursor = self.textCursor()
        if not cursor.hasSelection():
            if event.key() == Qt.Key.Key_Backspace:
                span = self._span_at(cursor.position(), "end")
                if span is not None:
                    self._take_chip(span)
                    return True
            elif event.key() == Qt.Key.Key_Delete:
                span = self._span_at(cursor.position(), "start")
                if span is not None:
                    self._take_chip(span)
                    return True
        if event.text() and event.text().isprintable():
            if str(self.currentCharFormat().property(_CHIP_VALUE) or ""):

                self.setCurrentCharFormat(QTextCharFormat())
        return False

    def _sheet_key(self, key) -> None:
        """The keys the open sheet answers: walk, pick, close."""
        if key == Qt.Key.Key_Escape:
            self._close_sheet()
        elif key == Qt.Key.Key_Down:
            self._sheet.step(1)
        elif key == Qt.Key.Key_Up:
            self._sheet.step(-1)
        elif key == Qt.Key.Key_PageDown:
            self._sheet.page(1)
        elif key == Qt.Key.Key_PageUp:
            self._sheet.page(-1)
        else:
            self._sheet.take_current()

    def set_send_on_modifier(self, on: bool) -> None:
        """True: Enter breaks the line and Ctrl/Cmd+Enter sends. False: the reverse."""
        self._send_on_modifier = bool(on)

    def sends_on_modifier(self) -> bool:
        """Which gesture sends, for the button's own help. Read at build time."""
        return self._send_on_modifier

    def focusOutEvent(self, event):  # noqa: N802 - Qt override
        super().focusOutEvent(event)


        if event.reason() != Qt.FocusReason.PopupFocusReason:
            self._close_sheet()

    def changeEvent(self, event):  # noqa: N802 - Qt override
        super().changeEvent(event)
        try:
            if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
                self._sync_height()
        except (RuntimeError, AttributeError):
            pass



    def _line_count(self) -> int:
        doc = self.document()
        layout = doc.documentLayout()
        count = 0
        block = doc.begin()
        while block.isValid():
            layout.blockBoundingRect(block)
            text_layout = block.layout()
            lines = text_layout.lineCount() if text_layout is not None else 1
            count += max(1, lines)
            block = block.next()
        return max(1, count)

    def set_min_lines(self, lines: int) -> None:
        """How tall the empty box is: 2 on a fresh chat, 1 once it is going."""
        wanted = max(MIN_LINES, min(MAX_LINES, int(lines or MIN_LINES)))
        if wanted == self._min_lines:
            return
        self._min_lines = wanted
        self._sync_height()

    def setPlaceholderText(self, text: str) -> None:  # noqa: N802 - Qt override
        """A new prompt is a new height: the box is measured on the words it is about to show, not on the ones it showed a moment ago."""

        super().setPlaceholderText(text)
        self._sync_height()

    def _placeholder_lines(self) -> int:
        """How many lines the placeholder wraps to at this width."""







        text = self.placeholderText()
        if not text or self.toPlainText():
            return 1
        margin = int(self.document().documentMargin())
        room = self.viewport().width() - 2 * margin
        if room <= 20:
            return 1
        rect = self.fontMetrics().boundingRect(
            0, 0, room, 0,
            int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignTop), text)
        spacing = max(1, self.fontMetrics().lineSpacing())
        return max(1, min(MAX_LINES, -(-rect.height() // spacing)))

    def _sync_height(self) -> None:

        if getattr(self, "_syncing_height", False):
            return
        self._syncing_height = True
        try:
            self._sync_height_once()
        finally:
            self._syncing_height = False

    def _sync_height_once(self) -> None:
        lines = max(self._min_lines, self._placeholder_lines(),
                    min(MAX_LINES, self._line_count()))
        lines = min(MAX_LINES, lines)
        margin = int(self.document().documentMargin())
        height = lines * self.fontMetrics().lineSpacing() + 2 * margin + 2 * self.frameWidth() + 2
        if height != self.height() or self.maximumHeight() != height:
            self.setFixedHeight(height)
            self.height_changed.emit()








        if self.viewport().height() != height:
            self.setViewportMargins(0, 0, 0, 0)

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._sync_height()



    def _chip_rects(self) -> list:
        """``(rect, glyph rect, data)`` for every chip on screen."""
        out = []
        cursor = QTextCursor(self.document())
        for start, end, kind, value, label, glyph in self._spans:
            cursor.setPosition(start)
            head = self.cursorRect(cursor)
            cursor.setPosition(start + 1)
            after_mark = self.cursorRect(cursor)
            cursor.setPosition(end)
            tail = self.cursorRect(cursor)
            if tail.top() != head.top():

                continue
            rect = QRectF(head.left() - _CHIP_PAD, head.top() + 1,
                          tail.left() - head.left() + 2 * _CHIP_PAD, head.height() - 2)
            mark = QRectF(head.left(), head.top(), after_mark.left() - head.left(), head.height())
            out.append((rect, mark, {"kind": kind, "value": value, "label": label,
                                     "glyph": glyph}))
        return out

    def paintEvent(self, event):  # noqa: N802 - Qt override
        rects = self._chip_rects() if self._spans else []
        if rects:
            painter = QPainter(self.viewport())
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor(ACCENT_TINT))
            for rect, _mark, _data in rects:
                painter.drawRoundedRect(rect, RADIUS_CHIP, RADIUS_CHIP)
            painter.end()
        super().paintEvent(event)
        if not rects:
            return
        painter = QPainter(self.viewport())
        for _rect, mark, data in rects:
            icon = self._chip_icon(data)
            if icon is None or mark.width() < _CHIP_GLYPH:
                continue
            left = int(mark.left() + (mark.width() - _CHIP_GLYPH) / 2.0)
            top = int(mark.center().y() - _CHIP_GLYPH / 2.0)
            icon.paint(painter, left, top, _CHIP_GLYPH, _CHIP_GLYPH)
        painter.end()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(super().sizeHint().width(), self.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(60, self.height())



    def text(self) -> str:
        """What the message says, chips included: they are words in the sentence."""
        return self.toPlainText()

    def set_text(self, text: str) -> None:
        self.setPlainText(text or "")
        self._has_chips = False
        self._spans = []
        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.setTextCursor(cursor)
        self.setCurrentCharFormat(QTextCharFormat())

    def append_text(self, text: str, at_end: bool = False) -> None:
        """Add plain words at the caret, never in a chip's format."""





        if not text:
            return
        if at_end:
            cursor = self.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.setTextCursor(cursor)
        self.setCurrentCharFormat(QTextCharFormat())
        self.insertPlainText(str(text))

    def clear(self) -> None:
        """Empty the box: the chips go with the message they belonged to."""
        super().clear()
        self._has_chips = False
        self._spans = []
        self.setCurrentCharFormat(QTextCharFormat())
