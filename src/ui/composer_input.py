# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








































from __future__ import annotations

import re
import sys

from qgis.PyQt.QtCore import QEvent, QPointF, QRectF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import (
    QFont,
    QFontMetrics,
    QKeySequence,
    QPainter,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
    QTextLayout,
    QTextOption,
)
from qgis.PyQt.QtWidgets import QDockWidget, QFrame, QPlainTextEdit, QWidget

from .icons import icon_for
from .layer_icons import follows_layers, layer_name
from .mention_search import FILES, LAYER, PLUGIN, SOURCE, layer_rows, sheet_rows
from .mention_sheet import MentionSheet, popup_keys
from .style import (
    _SCROLL_AREA_QSS,
    ACCENT_INK,
    ACCENT_TINT,
    INK_3,
    LINE_STRONG,
    RADIUS_CHIP,
    RED,
    RED_TINT,
    accent_color,
    medium_font_weight,
    qcolor,
)
from .widgets import placeholder_ink

_KEY = "Tab"








_PASTE_SEPARATORS = str.maketrans({"\u2029": "\n", "\u2028": "\n", "\u00a0": " "})
_PASTE_NEWLINES = re.compile(r"\r\n?")
_PASTE_BULLETS = re.compile(r"^[ \t]*[•‣▪○◦⁃⁌⁍∙][ \t]*", re.MULTILINE)
_PASTE_TRAILING_SPACE = re.compile(r"[ \t]+$", re.MULTILINE)
_PASTE_BLANK_RUN = re.compile(r"\n{3,}")

MIN_LINES = 1



MAX_LINES = 8
MAX_SHARE = 0.4




_CHIP_KIND = QTextFormat.Property.UserProperty + 1
_CHIP_VALUE = QTextFormat.Property.UserProperty + 2
_CHIP_LABEL = QTextFormat.Property.UserProperty + 3
_CHIP_GLYPH_NAME = QTextFormat.Property.UserProperty + 4


_CHIP_PAD = 3
_CHIP_GLYPH = 13


_CHIP_GLYPH_ROOM = _CHIP_GLYPH + 3

EMPTY_CHAT_LINES = 1





_SPREADSHEET_FORMATS = ('value="Csv"', 'value="XML Spreadsheet"', 'value="Biff')


def _chips_in(document, text: str, offset: int = 0) -> list[tuple]:







    cursor = QTextCursor(document)
    spans: list[tuple] = []
    at = text.find("@")
    while at >= 0:
        cursor.setPosition(offset + at + 1)
        fmt = cursor.charFormat()
        value = str(fmt.property(_CHIP_VALUE) or "")
        if not value:
            at = text.find("@", at + 1)
            continue
        end = at + 1
        while end < len(text):
            cursor.setPosition(offset + end + 1)
            if str(cursor.charFormat().property(_CHIP_VALUE) or "") != value:
                break
            end += 1
        spans.append((offset + at, offset + end, str(fmt.property(_CHIP_KIND) or ""), value,
                      str(fmt.property(_CHIP_LABEL) or ""), str(fmt.property(_CHIP_GLYPH_NAME) or "")))
        at = text.find("@", end)
    return spans


class _StaleMarks(QSyntaxHighlighter):




    def __init__(self, box: ComposerInput):
        super().__init__(box.document())
        self._box = box
        self._ink = QTextCharFormat()
        self._ink.setForeground(qcolor(RED))

    def highlightBlock(self, text):  # noqa: N802
        stale = self._box._stale
        if not stale or "@" not in text:
            return
        block = self.currentBlock()
        for start, end, _kind, value, label, _glyph in _chips_in(block.document(), text, block.position()):
            if (value, label) not in stale:
                continue
            at = start - block.position()

            mark = QTextCursor(block.document())
            mark.setPosition(start + 1)
            skip = 1 if mark.charFormat().foreground().color().alpha() == 0 else 0
            self.setFormat(at + skip, end - start - skip, self._ink)


def _is_spreadsheet_copy(source) -> bool:

    try:
        if not source.hasText() or not source.text().strip():
            return False
        return any(mark in fmt for fmt in source.formats() for mark in _SPREADSHEET_FORMATS)
    except (AttributeError, RuntimeError, TypeError):
        return False


class ComposerInput(QPlainTextEdit):






    submitted = pyqtSignal()
    mention_chosen = pyqtSignal(object)
    mentions_changed = pyqtSignal()
    stale_changed = pyqtSignal()
    files_requested = pyqtSignal()
    recall_requested = pyqtSignal()
    height_changed = pyqtSignal()
    image_pasted = pyqtSignal(object)
    paths_pasted = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("composerInput")
        self.setAccessibleName(self.tr("Message AI Agent"))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAcceptDrops(False)
        self.setTabChangesFocus(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.document().setDocumentMargin(4)



        self._min_lines = MIN_LINES

        self._ghost = ""
        self._ghost_cache: tuple = ((), None)


        self._send_on_modifier = False
        self._provider = None
        self._static_items: list = []


        self._anchor: QWidget | None = None


        self._mode = "@"


        self._spans: list[tuple] = []
        self._has_chips = False

        self._stale: dict[tuple, str | None] = {}
        self._stale_marks = _StaleMarks(self)
        follows_layers(self)
        self._plugins: list | None = None






        self._candidates: list | None = None
        self._connector_rows: list | None = None
        self._visible_ids: set | None = None
        self._sheet = MentionSheet(self)
        self._sheet.picked.connect(self._insert_mention)

        self._height_host: QWidget | None = None


        self.paste_taker = None
        self.undo_paste = None
        self._raw_paste = False

        self.textChanged.connect(self._sync_height)
        self.textChanged.connect(self._sync_spans)
        self._sync_height()

    def set_mention_provider(self, provider) -> None:

        self._provider = provider if callable(provider) else None
        self._candidates = None
        self._visible_ids = None

    def set_sheet_anchor(self, widget: QWidget | None) -> None:

        self._anchor = widget

    def set_mentions(self, items) -> None:





        self._static_items = [dict(i) for i in items or [] if isinstance(i, dict)]
        self._candidates = None
        self._visible_ids = None
        self._sheet.set_rows(self._static_items)

    def sheet(self) -> MentionSheet:
        return self._sheet

    def _connectors(self) -> list:

        try:
            from ..core.served_state import get_connectors

            return get_connectors()
        except Exception:  # noqa: BLE001
            return []

    def _plugin_candidates(self) -> list:











        if self._plugins is not None:
            return self._plugins
        self._plugins = []
        try:
            from ..core.host_platform import is_own_folder
            from ..core.qgis_plugins import installed_plugins, plugin_logo
            from ..core.served_state import get_hidden_plugins, known_plugins_by_folder

            local = installed_plugins()
            known = known_plugins_by_folder()

            skip = get_hidden_plugins()
        except Exception:  # noqa: BLE001
            return []
        rows = []
        for folder, row in (local or {}).items():
            if folder in skip or is_own_folder(folder) or not row.get("loaded"):
                continue
            merged = dict(known.get(folder) or {})
            merged.update(row)
            if not str(merged.get("icon") or "").strip():
                try:
                    merged["icon"] = plugin_logo(folder)
                except Exception:  # noqa: BLE001
                    merged["icon"] = ""
            rows.append(merged)
        self._plugins = rows
        return rows

    def _visible_layer_ids(self) -> set:

        try:
            from qgis.core import QgsProject

            root = QgsProject.instance().layerTreeRoot()
            return {str(node.layerId()) for node in root.findLayers() if node.isVisible()}
        except Exception:  # noqa: BLE001
            return set()

    def _visible_once(self) -> set:

        if self._visible_ids is None:
            self._visible_ids = self._visible_layer_ids()
        return self._visible_ids

    def _refresh_mentions(self, word: str = "") -> None:

        if self._candidates is None:
            self._candidates = (self._ask(self._provider) if self._provider is not None
                                else list(self._static_items))
        items = self._candidates
        if self._mode == LAYER:
            self._sheet.set_rows(layer_rows(word, items, self._visible_once()))
            return
        if self._connector_rows is None:
            self._connector_rows = self._connectors() if self._provider is not None else []
        connectors = self._connector_rows
        rows = sheet_rows(word, items, connectors, self._plugin_candidates(),
                          {"layers": self.tr("Layers"), "sources": self.tr("Data sources"),
                           "plugins": self.tr("QGIS plugins"),
                           "hint": self.tr("Type to search layers, data sources and QGIS plugins")},
                          self._visible_once())
        self._sheet.set_rows(rows)

    @staticmethod
    def _ask(provider) -> list:
        try:
            result = provider() or []
            return list(result)[:1000]
        except Exception:  # noqa: BLE001
            return []

    def begin_mention(self, mode: str = "@") -> None:

        self._mode = mode
        self._plugins = None
        self._candidates = None
        self._visible_ids = None
        self._connector_rows = None
        cursor = self.textCursor()
        cursor.setCharFormat(QTextCharFormat())
        before = cursor.block().text()[:cursor.positionInBlock()]
        text = "@" if (not before or before[-1].isspace()) else " @"
        cursor.insertText(text, QTextCharFormat())
        self.setTextCursor(cursor)
        self.setCurrentCharFormat(QTextCharFormat())
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self._update_completion(pop=True)

    def has_layers(self) -> bool:






        if self._provider is None:
            items = self._static_items
        else:
            items = self._ask(self._provider)
        return any(isinstance(i, dict) and i.get("kind") == LAYER for i in items or [])

    def begin_layer_mention(self) -> None:

        self.begin_mention(LAYER)

    def _mention_prefix(self):

        return self._trigger_prefix("@")

    def _trigger_prefix(self, trigger: str):

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

    def _update_completion(self, pop: bool = False) -> None:
        found = self._mention_prefix()
        if found is None:
            self._close_sheet()
            return
        _start, word = found
        self._refresh_mentions(word)
        if self._sheet.row_count() == 0:
            self._close_sheet()
            return
        self._sheet.open_above(self._anchor if self._anchor is not None else self, pop)

    def _close_sheet(self) -> None:

        self._sheet.hide()
        self._mode = "@"
        self._plugins = None
        self._candidates = None
        self._visible_ids = None
        self._connector_rows = None

    def _insert_mention(self, data) -> None:

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
        mark, body = self._chip_formats(kind, value, label, data)
        cursor.insertText("@", mark)
        cursor.insertText(label, body)
        cursor.insertText(" ", QTextCharFormat())
        self.setTextCursor(cursor)
        self.setCurrentCharFormat(QTextCharFormat())
        self._has_chips = True
        self._sync_spans()
        self.mentions_changed.emit()
        return True

    def _chip_formats(self, kind: str, value: str, label: str, data: dict) -> tuple:

        body = QTextCharFormat()
        body.setProperty(_CHIP_KIND, kind)
        body.setProperty(_CHIP_VALUE, value)
        body.setProperty(_CHIP_LABEL, label)
        body.setProperty(_CHIP_GLYPH_NAME, str(data.get("glyph") or ""))
        body.setForeground(qcolor(ACCENT_INK))
        body.setFontWeight(medium_font_weight())
        return self._mark_format(body, data), body

    def chip_marks(self) -> list[dict]:

        return [{"kind": kind, "label": label, "value": value, "glyph": glyph, "at": start}
                for start, _end, kind, value, label, glyph in self._spans]

    def restore_chips(self, marks) -> int:





        text = self.toPlainText()
        cursor = QTextCursor(self.document())
        done = 0
        for mark in marks or ():
            at, label, value = mark.get("at"), str(mark.get("label") or ""), str(mark.get("value") or "")
            if not isinstance(at, int) or not value or text[at:at + 1 + len(label)] != "@" + label:
                continue
            fmt_mark, body = self._chip_formats(str(mark.get("kind") or ""), value, label, mark)
            cursor.setPosition(at)
            cursor.setPosition(at + 1, QTextCursor.MoveMode.KeepAnchor)
            cursor.setCharFormat(fmt_mark)
            cursor.setPosition(at + 1)
            cursor.setPosition(at + 1 + len(label), QTextCursor.MoveMode.KeepAnchor)
            cursor.setCharFormat(body)
            done += 1
        if done:
            self._has_chips = True
            self.setCurrentCharFormat(QTextCharFormat())
            self._sync_spans()
            self._mark_stale()
        return done

    def follow_project(self, gone=frozenset()) -> None:

        self._mark_stale(gone)

    def _mark_stale(self, gone=frozenset()) -> None:

        stale = {}
        for _start, _end, kind, value, label, _glyph in self._spans:
            if kind != LAYER:
                continue
            name = None if value in gone else layer_name(value)
            if name != label:
                stale[(value, label)] = name
        if stale == self._stale:
            return
        self._stale = stale
        self._stale_marks.rehighlight()
        self.viewport().update()
        self.stale_changed.emit()

    def stale_mentions(self) -> list[tuple]:

        return [(label, name) for (_value, label), name in self._stale.items()]

    def _mark_format(self, body: QTextCharFormat, data: dict) -> QTextCharFormat:







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

    def _chip_icon(self, data: dict, color=None):

        glyph = str(data.get("glyph") or "")
        kind = str(data.get("kind") or "")
        if not glyph:
            glyph = {SOURCE: "globe", PLUGIN: "package", LAYER: "layers"}.get(kind, "")
        if not glyph:
            return None
        icon = icon_for(self, glyph, _CHIP_GLYPH, color if color is not None else accent_color())
        return None if icon.isNull() else icon

    def mentions(self) -> list[dict]:

        out, seen = [], set()
        for _start, _end, kind, value, label, _glyph in self._spans:
            key = (kind, value)
            if key in seen:
                continue
            seen.add(key)
            out.append({"kind": kind, "label": label, "value": value})
        return out

    def adopt_new_names(self, label: str | None = None) -> int:




        renamed = [span for span in self._spans
                   if span[2] == LAYER and self._stale.get((span[3], span[4])) is not None
                   and label in (None, span[4])]
        if not renamed:
            return 0
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()
        for start, end, kind, value, label, glyph in reversed(renamed):
            name = self._stale[(value, label)]
            mark, body = self._chip_formats(kind, value, name, {"kind": kind, "glyph": glyph})
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            cursor.insertText("@", mark)
            cursor.insertText(name, body)
        cursor.endEditBlock()
        self.setCurrentCharFormat(QTextCharFormat())
        self._sync_spans()
        self._mark_stale()
        return len(renamed)

    def _sync_spans(self) -> None:

        spans = self._read_spans() if self._has_chips else []
        if spans != self._spans:

            changed = [s[2:5] for s in spans] != [s[2:5] for s in self._spans]
            self._spans = spans
            if changed:
                self._mark_stale()
                self.mentions_changed.emit()
        if not spans:
            self._has_chips = False

    def _read_spans(self) -> list[tuple]:

        return _chips_in(self.document(), self.toPlainText())

    def _span_at(self, position: int, edge: str):





        for span in self._spans:
            if edge == "end" and span[0] < position <= span[1]:
                return span
            if edge == "start" and span[0] <= position < span[1]:
                return span
        return None

    def _take_chip(self, span) -> None:

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

    def canInsertFromMimeData(self, source) -> bool:  # noqa: N802
        return bool(source.hasImage() or source.hasUrls()) or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):  # noqa: N802
        if source.hasImage() and not _is_spreadsheet_copy(source):
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



        text = source.text() or ""
        text = _PASTE_NEWLINES.sub("\n", text).translate(_PASTE_SEPARATORS)
        text = _PASTE_BULLETS.sub("", text)
        text = _PASTE_TRAILING_SPACE.sub("", text)
        text = _PASTE_BLANK_RUN.sub("\n\n", text).strip()
        if text and not self._raw_paste and self.paste_taker is not None:
            if self.paste_taker(text, self.textCursor()):
                return
        if text:
            self.insertPlainText(text)

    def event(self, event):  # noqa: N802




        try:
            key_press = event.type() == QEvent.Type.KeyPress
        except (AttributeError, RuntimeError):
            key_press = False
        if key_press and self._sheet.isVisible() and event.key() in (
                Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self._sheet_key(Qt.Key.Key_Tab)
            return True
        return super().event(event)

    def keyPressEvent(self, event):  # noqa: N802
        if self._paste_key(event):
            return
        if self._sheet.isVisible() and event.key() in popup_keys():
            self._sheet_key(event.key())
            return
        if self._spans and self._chip_key(event):
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):




            modifiers = event.modifiers()
            with_modifier = bool(modifiers & (Qt.KeyboardModifier.ControlModifier
                                              | Qt.KeyboardModifier.MetaModifier))
            sends = with_modifier if self._send_on_modifier else not (modifiers & Qt.KeyboardModifier.ShiftModifier)
            if sends:


                if not event.isAutoRepeat():
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

    def _paste_key(self, event) -> bool:


        modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if event.key() == Qt.Key.Key_V and modifiers == (Qt.KeyboardModifier.ControlModifier
                                                       | Qt.KeyboardModifier.ShiftModifier):
            self._raw_paste = True
            try:
                self.paste()
            finally:
                self._raw_paste = False
            return True
        return bool(event.matches(QKeySequence.StandardKey.Undo) and self.undo_paste is not None
                    and self.undo_paste())

    def _word_delete(self, event) -> bool:









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

        self._send_on_modifier = bool(on)

    def sends_on_modifier(self) -> bool:

        return self._send_on_modifier

    def focusOutEvent(self, event):  # noqa: N802
        super().focusOutEvent(event)


        if event.reason() != Qt.FocusReason.PopupFocusReason:
            self._close_sheet()

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        try:
            if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
                self._sync_height()
        except (RuntimeError, AttributeError):
            pass

    def _line_count(self, cap: int) -> int:





        doc = self.document()
        layout = doc.documentLayout()
        ghost = self._ghost_layout()
        count = 0
        block = doc.begin()
        while block.isValid() and count < cap:
            layout.blockBoundingRect(block)
            text_layout = ghost if ghost is not None and block == doc.lastBlock() else block.layout()
            lines = text_layout.lineCount() if text_layout is not None else 1
            count += max(1, lines)
            block = block.next()
        return max(1, count)

    def set_ghost(self, text: str) -> None:


        if text == self._ghost:
            return
        self._ghost = text
        self._sync_height()
        self.viewport().update()

    def ghost(self) -> str:
        return self._ghost

    def _ghost_layout(self):

        if not self._ghost:
            return None
        block = self.document().lastBlock()
        own = block.layout()
        if own is None or own.lineCount() == 0:
            self.document().documentLayout().blockBoundingRect(block)
            own = block.layout()
        if own is None or own.lineCount() == 0:
            return None
        first = own.lineAt(0)
        font = self.document().defaultFont()
        key = (block.text(), self._ghost, first.width(), first.x(), font.key())
        if self._ghost_cache[0] == key:
            return self._ghost_cache[1]

        gap = "\u00a0" * (1 + -(-int(self._key_size()[0]) // max(1, QFontMetrics(font).horizontalAdvance("\u00a0"))))
        layout = QTextLayout(block.text() + self._ghost + gap, font)
        layout.setTextOption(own.textOption())
        layout.beginLayout()
        top = first.y()
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(first.width())
            line.setPosition(QPointF(first.x(), top))
            top += line.height()
        layout.endLayout()
        self._ghost_cache = (key, layout)
        return layout

    def _max_lines(self) -> int:

        host = getattr(self, "_height_host", None)
        if host is None:
            return MAX_LINES
        try:
            room = int(host.height() * MAX_SHARE)
        except RuntimeError:
            return MAX_LINES
        return max(MAX_LINES, room // max(1, self.fontMetrics().lineSpacing()))

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if self._height_host is None:


            host = self.parentWidget()
            while host is not None and not isinstance(host, QDockWidget) and not host.isWindow():
                host = host.parentWidget()
            self._height_host = host
            if host is not None:
                host.installEventFilter(self)


            self.verticalScrollBar().setStyleSheet(_SCROLL_AREA_QSS)
        self._sync_height()

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self._height_host and event.type() == QEvent.Type.Resize:
            self._sync_height()
        return super().eventFilter(watched, event)

    def set_min_lines(self, lines: int) -> None:

        wanted = max(MIN_LINES, min(self._max_lines(), int(lines or MIN_LINES)))
        if wanted == self._min_lines:
            return
        self._min_lines = wanted
        self._sync_height()

    def setPlaceholderText(self, text: str) -> None:  # noqa: N802


        super().setPlaceholderText(text)
        placeholder_ink(self)
        self._sync_height()

    def _placeholder_lines(self) -> int:








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
        return max(1, min(self._max_lines(), -(-rect.height() // spacing)))

    def _sync_height(self) -> None:

        if getattr(self, "_syncing_height", False):
            return
        self._syncing_height = True
        try:
            self._sync_height_once()
        finally:
            self._syncing_height = False

    def _sync_height_once(self) -> None:
        cap = self._max_lines()
        lines = min(cap, max(self._min_lines, self._placeholder_lines(), self._line_count(cap)))
        margin = int(self.document().documentMargin())
        height = lines * self.fontMetrics().lineSpacing() + 2 * margin + 2 * self.frameWidth() + 2
        if height != self.height() or self.maximumHeight() != height:
            self.setFixedHeight(height)
            self.height_changed.emit()








        if self.viewport().height() != height:
            self.setViewportMargins(0, 0, 0, 0)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._sync_height()

    def _chip_rects(self) -> list:

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

    def _paint_ghost(self) -> None:
        layout = self._ghost_layout()
        if layout is None:
            return
        block = self.document().lastBlock()
        grey = QTextLayout.FormatRange()
        grey.start, grey.length = len(block.text()), len(self._ghost)
        grey.format = QTextCharFormat()
        grey.format.setForeground(qcolor(INK_3))
        painter = QPainter(self.viewport())


        painter.setPen(Qt.GlobalColor.transparent)
        origin = self.blockBoundingGeometry(block).translated(self.contentOffset()).topLeft()
        layout.draw(painter, origin, [grey])
        self._paint_key(painter, layout, origin)
        painter.end()

    def _key_font(self) -> QFont:
        font = QFont(self.document().defaultFont())
        font.setPointSizeF(font.pointSizeF() * 0.8)
        return font

    def _key_size(self) -> tuple[float, float]:
        metrics = QFontMetrics(self._key_font())
        return metrics.horizontalAdvance(_KEY) + 10, metrics.height() + 2

    def _paint_key(self, painter, layout, origin) -> None:

        end = len(self.document().lastBlock().text()) + len(self._ghost)
        line = layout.lineForTextPosition(end)
        if not line.isValid():
            return
        font = self._key_font()
        w, h = self._key_size()
        x = origin.x() + line.cursorToX(end)[0] + 6
        y = origin.y() + line.y() + (line.height() - h) / 2
        rect = QRectF(x, y, w, h)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(qcolor(LINE_STRONG))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        painter.setFont(font)
        painter.setPen(qcolor(INK_3))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), _KEY)

    def paintEvent(self, event):  # noqa: N802
        rects = self._chip_rects() if self._spans else []
        if rects:
            painter = QPainter(self.viewport())
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            for rect, _mark, data in rects:
                painter.setBrush(qcolor(RED_TINT if self._is_stale(data) else ACCENT_TINT))
                painter.drawRoundedRect(rect, RADIUS_CHIP, RADIUS_CHIP)
            painter.end()
        super().paintEvent(event)
        self._paint_ghost()
        if not rects:
            return
        painter = QPainter(self.viewport())
        for _rect, mark, data in rects:
            icon = self._chip_icon(data, qcolor(RED) if self._is_stale(data) else None)
            if icon is None or mark.width() < _CHIP_GLYPH:
                continue
            left = int(mark.left() + (mark.width() - _CHIP_GLYPH) / 2.0)
            top = int(mark.center().y() - _CHIP_GLYPH / 2.0)
            icon.paint(painter, left, top, _CHIP_GLYPH, _CHIP_GLYPH)
        painter.end()

    def _is_stale(self, data: dict) -> bool:
        return (data["value"], data["label"]) in self._stale

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(super().sizeHint().width(), self.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(60, self.height())

    def text(self) -> str:

        return self.toPlainText()

    def set_text(self, text: str) -> None:
        self.setPlainText(text or "")
        self._has_chips = False
        self._spans = []
        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.setTextCursor(cursor)
        self.setCurrentCharFormat(QTextCharFormat())
        self._mark_stale()

    def append_text(self, text: str, at_end: bool = False) -> None:






        if not text:
            return
        if at_end:
            cursor = self.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.setTextCursor(cursor)
        self.setCurrentCharFormat(QTextCharFormat())
        self.insertPlainText(str(text))

    def clear(self) -> None:

        super().clear()
        self._has_chips = False
        self._spans = []
        self.setCurrentCharFormat(QTextCharFormat())
        self._mark_stale()
