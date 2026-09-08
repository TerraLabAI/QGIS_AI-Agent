# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The code block of a tool call: the site's Code Block, in Qt."""

















from __future__ import annotations

import difflib
import json
import re

from qgis.PyQt.QtCore import QRect, QSize, Qt, QTimer
from qgis.PyQt.QtGui import (
    QFont,
    QPainter,
    QSyntaxHighlighter,
    QTextBlockFormat,
    QTextBlockUserData,
    QTextCharFormat,
    QTextCursor,
    QTextOption,
)
from qgis.PyQt.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .card_base import mono_font
from .font_scale import scale_qss_font_px
from .icons import pixmap_for
from .shared import qt_enum_int, resolve_qt_enum
from .style import (
    ACCENT_INK,
    FONT_BODY,
    FONT_HINT,
    GREEN,
    GREEN_TINT,
    HOVER_ON,
    INK,
    INK_2,
    INK_3,
    INSET,
    LINE,
    LINE_STRONG,
    MONO_FAMILY,
    ORANGE,
    RADIUS_CARD,
    RADIUS_CHIP,
    RED,
    RED_TINT,
    qcolor,
)
from .widgets import ElidedLabel

_HEAD_PX = 40
_GUTTER_PX = 28
_PAD_PX = 12
_LINE_HEIGHT = 1.6

_MAX_LINES = 18
_COPIED_MS = 1500



_MAX_HIGHLIGHT_LINES = 400

_BLOCK_QSS = scale_qss_font_px(
    f"QFrame#codeBlock {{ background: {INSET}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_CARD}px; }}"
    f"QWidget#codeHead {{ background: {HOVER_ON}; border: none;"
    f" border-bottom: 1px solid {LINE};"
    f" border-top-left-radius: {RADIUS_CARD - 1}px; border-top-right-radius: {RADIUS_CARD - 1}px; }}"
    f"QLabel#codeName {{ font-family: {MONO_FAMILY}; font-size: {FONT_HINT}px;"
    f" color: {INK_2}; background: transparent; border: none; }}"
    "QToolButton#codeCopy { background: transparent; border: none;"
    f" border-radius: {RADIUS_CHIP}px; padding: 0 6px; font-size: {FONT_BODY}px;"
    f" font-weight: 500; color: {INK_2}; }}"
    f"QToolButton#codeCopy:hover {{ color: {INK}; }}"
    "QPlainTextEdit#codeBody { background: transparent; border: none;"
    f" color: {INK_2}; selection-background-color: {LINE_STRONG}; }}"
    "QPlainTextEdit#codeBody QScrollBar:vertical { background: transparent; width: 8px;"
    " margin: 2px 2px 2px 0; border: none; }"
    f"QPlainTextEdit#codeBody QScrollBar::handle:vertical {{ background: {LINE_STRONG};"
    " border-radius: 3px; min-height: 24px; }"
    f"QPlainTextEdit#codeBody QScrollBar::handle:vertical:hover {{ background: {INK_3}; }}"
    "QPlainTextEdit#codeBody QScrollBar::add-line:vertical,"
    " QPlainTextEdit#codeBody QScrollBar::sub-line:vertical { height: 0px; border: none; }"
    "QPlainTextEdit#codeBody QScrollBar::add-page:vertical,"
    " QPlainTextEdit#codeBody QScrollBar::sub-page:vertical { background: transparent; }"
    "QPlainTextEdit#codeBody QScrollBar:horizontal { background: transparent; height: 8px;"
    " margin: 0 2px 2px 2px; border: none; }"
    f"QPlainTextEdit#codeBody QScrollBar::handle:horizontal {{ background: {LINE_STRONG};"
    " border-radius: 3px; min-width: 24px; }"
    "QPlainTextEdit#codeBody QScrollBar::add-line:horizontal,"
    " QPlainTextEdit#codeBody QScrollBar::sub-line:horizontal { width: 0px; border: none; }"
    "QPlainTextEdit#codeBody QScrollBar::add-page:horizontal,"
    " QPlainTextEdit#codeBody QScrollBar::sub-page:horizontal { background: transparent; }"
)






_PY_KEYWORDS = [
    "False", "None", "True", "and", "as", "assert", "async", "await", "break", "class",
    "continue", "def", "del", "elif", "else", "except", "finally", "for", "from", "global",
    "if", "import", "in", "is", "lambda", "nonlocal", "not", "or", "pass", "raise",
    "return", "try", "while", "with", "yield", "print", "self",
]
_JSON_WORDS = ("true", "false", "null")


def _proportional_height() -> int:
    """The int ``QTextBlockFormat.setLineHeight`` takes for a proportional height."""





    return qt_enum_int(QTextBlockFormat, "LineHeightTypes", "ProportionalHeight", 1)


def _demibold() -> int:
    """DemiBold on this Qt, as the int ``setFontWeight`` takes."""






    member = resolve_qt_enum(QFont, "Weight", "DemiBold")
    value = getattr(member, "value", member)
    return int(value) if isinstance(value, int) else 63


def _fmt(colour: str, weight: int = 400) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setForeground(qcolor(colour))
    if weight >= 500:
        try:
            fmt.setFontWeight(_demibold())
        except (TypeError, ValueError):
            fmt.setFontWeight(resolve_qt_enum(QFont, "Weight", "DemiBold"))
    return fmt






_PATTERNS: dict = {}


def _patterns(language: str) -> dict:
    """The compiled patterns for ``language``, as (pattern, format slot) pairs."""
    found = _PATTERNS.get(language)
    if found is not None:
        return found
    words = _PY_KEYWORDS if language == "python" else _JSON_WORDS
    rules = []
    if language in ("python", "json"):
        rules.append((re.compile(r"\b(?:" + "|".join(map(re.escape, words)) + r")\b"),
                      "_keyword"))
        rules.append((re.compile(r"\b\d+(?:\.\d+)?(?:e[+-]?\d+)?\b", re.IGNORECASE),
                      "_number"))
    if language == "python":
        rules.append((re.compile(r"\b(?:def|class)\s+(\w+)"), "_name"))
        rules.append((re.compile(r"\b([A-Za-z_]\w*)(?=\()"), "_name"))
    string_re = re.compile(
        r"'''.*?'''|\"\"\".*?\"\"\"|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"", re.DOTALL)
    comment_re = re.compile(r"#[^\n]*") if language == "python" else None
    if language == "json":
        rules.append((re.compile(r"\"(?:\\.|[^\"\\])*\"(?=\s*:)"), "_name"))
    found = {"rules": rules, "string": string_re, "comment": comment_re}
    _PATTERNS[language] = found
    return found


class _Highlighter(QSyntaxHighlighter):
    """The site's four colours on Python (and on JSON, where a key is a name and a value a string or a number)."""


    def __init__(self, document, language: str):
        super().__init__(document)
        self.language = language
        self._keyword = _fmt(ACCENT_INK)
        self._string = _fmt(ORANGE)
        self._number = _fmt(ORANGE)
        self._comment = _fmt(INK_3)
        self._name = _fmt(INK, 500)
        patterns = _patterns(language)
        self._rules = [(pattern, getattr(self, slot)) for pattern, slot in patterns["rules"]]
        self._string_re = patterns["string"]
        self._comment_re = patterns["comment"]

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt override
        if not self._rules:
            return
        for pattern, fmt in self._rules:
            for match in pattern.finditer(text):
                group = 1 if match.lastindex else 0
                self.setFormat(match.start(group), match.end(group) - match.start(group), fmt)
        spans = []
        for match in self._string_re.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self._string)
            spans.append((match.start(), match.end()))
        if self._comment_re is not None:




            for match in self._comment_re.finditer(text):
                if any(start <= match.start() < end for start, end in spans):
                    continue
                self.setFormat(match.start(), match.end() - match.start(), self._comment)

        if self.language == "json":
            for match in self._rules[-1][0].finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), self._name)






class _LineData(QTextBlockUserData):
    """What the gutter shows for one line: a number, or a diff prefix."""

    def __init__(self, mark: str):
        super().__init__()
        self.mark = mark


class _Gutter(QWidget):
    def __init__(self, body: _Body):
        super().__init__(body)
        self._body = body

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(_GUTTER_PX, 0)

    def paintEvent(self, event):  # noqa: N802 - Qt override
        try:
            self._body.paint_gutter(event, self)
        except Exception:  # noqa: BLE001 - paint must never raise
            return


class _Body(QPlainTextEdit):
    """The listing: read-only, no wrap, a gutter painted at its left."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("codeBody")
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFont(mono_font(FONT_BODY))
        self.viewport().setAutoFillBackground(False)
        self.document().setDocumentMargin(0)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                     | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self._gutter = _Gutter(self)
        self._gutter_font = mono_font(FONT_HINT)
        self._diff = False
        self._lines = 0
        self.setViewportMargins(_GUTTER_PX + 4, _PAD_PX, _PAD_PX, _PAD_PX)
        self.updateRequest.connect(self._scroll_gutter)



        self.horizontalScrollBar().rangeChanged.connect(self._on_hbar_range)



    def set_lines(self, lines: list, diff: bool = False) -> None:
        """Fill the document line by line."""




        self._diff = diff
        self.clear()
        overflow = len(lines) - _MAX_HIGHLIGHT_LINES
        shown = lines[:_MAX_HIGHLIGHT_LINES] if overflow > 0 else lines
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()
        for i, item in enumerate(shown):
            if i:
                cursor.insertBlock()
            prefix, text = item if diff else ("", item)
            block_fmt = QTextBlockFormat()
            block_fmt.setLineHeight(int(_LINE_HEIGHT * 100), _proportional_height())
            char_fmt = QTextCharFormat()
            if diff and prefix == "+":
                block_fmt.setBackground(qcolor(GREEN_TINT))
                char_fmt.setForeground(qcolor(GREEN))
            elif diff and prefix == "-":
                block_fmt.setBackground(qcolor(RED_TINT))
                char_fmt.setForeground(qcolor(RED))
            elif diff and prefix == "@":
                char_fmt.setForeground(qcolor(INK_3))
            cursor.setBlockFormat(block_fmt)
            cursor.setCharFormat(char_fmt)
            cursor.insertText(text)
            cursor.block().setUserData(_LineData(prefix if diff else str(i + 1)))
        if overflow > 0:
            if shown:
                cursor.insertBlock()
            block_fmt = QTextBlockFormat()
            block_fmt.setLineHeight(int(_LINE_HEIGHT * 100), _proportional_height())
            char_fmt = QTextCharFormat()
            char_fmt.setForeground(qcolor(INK_3))
            cursor.setBlockFormat(block_fmt)
            cursor.setCharFormat(char_fmt)
            cursor.insertText(self.tr("… {n} more lines").format(n=overflow))
            cursor.block().setUserData(_LineData(""))
        cursor.endEditBlock()
        self._fit_height(len(shown) + (1 if overflow > 0 else 0))
        self._gutter.update()

    def _fit_height(self, count: int | None = None) -> None:
        if count is not None:
            self._lines = int(count)
        line_px = self.fontMetrics().lineSpacing() * _LINE_HEIGHT
        shown = max(1, min(self._lines, _MAX_LINES))
        extra = 0
        if self._lines > _MAX_LINES:
            extra = 2
        bar = self.horizontalScrollBar()
        if bar is not None and bar.maximum() > 0:
            extra += bar.sizeHint().height()
        self.setFixedHeight(int(round(shown * line_px)) + 2 * _PAD_PX + extra)

    def _on_hbar_range(self, _low: int, _high: int) -> None:
        self._fit_height()



    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        rect = self.contentsRect()
        self._gutter.setGeometry(QRect(rect.left(), rect.top(), _GUTTER_PX, rect.height()))

    def _scroll_gutter(self, rect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())

    def paint_gutter(self, event, gutter: QWidget) -> None:
        painter = QPainter(gutter)
        painter.setFont(self._gutter_font)
        block = self.firstVisibleBlock()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top()) + _PAD_PX
        height = self.fontMetrics().lineSpacing()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible():
                data = block.userData()
                mark = data.mark if isinstance(data, _LineData) else ""
                colour = INK_3
                if self._diff and mark == "+":
                    colour = GREEN
                elif self._diff and mark == "-":
                    colour = RED
                painter.setPen(qcolor(colour))
                painter.drawText(QRect(0, top, _GUTTER_PX - 8, height),
                                 Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, mark)
            block_height = int(self.blockBoundingRect(block).height())
            top += block_height
            block = block.next()
        painter.end()

    def wheelEvent(self, event):  # noqa: N802 - Qt override



        vbar = self.verticalScrollBar()
        hbar = self.horizontalScrollBar()
        if vbar.maximum() == 0 and hbar.maximum() == 0:
            event.ignore()
            return
        super().wheelEvent(event)






class CodeBlock(QFrame):
    """The card: a head with the name and Copy, the numbered listing under it."""


    def __init__(self, code: str = "", name: str = "", language: str = "python", parent=None):
        super().__init__(parent)
        self.setObjectName("codeBlock")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_BLOCK_QSS)
        self._text = ""
        self._highlighter: _Highlighter | None = None
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        self._head = QWidget(self)
        self._head.setObjectName("codeHead")
        self._head.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._head.setFixedHeight(_HEAD_PX)
        head = QHBoxLayout(self._head)
        head.setContentsMargins(_PAD_PX, 0, 6, 0)
        head.setSpacing(7)
        glyph = QLabel(self._head)
        glyph.setFixedSize(16, 16)
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        glyph.setPixmap(pixmap_for(self, "code", 15, qcolor(INK_3)))
        head.addWidget(glyph, 0, Qt.AlignmentFlag.AlignVCenter)


        self._name = ElidedLabel("", self._head)
        self._name.setObjectName("codeName")
        head.addWidget(self._name, 1, Qt.AlignmentFlag.AlignVCenter)
        self._copy = QToolButton(self._head)
        self._copy.setObjectName("codeCopy")
        self._copy.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy.setAutoRaise(True)
        self._copy.setFixedHeight(24)
        self._copy.setIcon(_copy_icon(self))
        self._copy.setIconSize(QSize(11, 11))
        self._copy.setText(self.tr("Copy"))
        self._copy.clicked.connect(self._on_copy)
        self._copied_timer = QTimer(self)
        self._copied_timer.setSingleShot(True)
        self._copied_timer.timeout.connect(lambda: self._copy.setText(self.tr("Copy")))
        head.addWidget(self._copy, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addWidget(self._head)

        self._body = _Body(self)
        col.addWidget(self._body)
        self.set_code(code, name, language)



    def _detach_highlighter(self) -> None:
        """Let go of the current highlighter before its document changes under it, or before it is replaced: left attached, the old one kept."""


        if self._highlighter is not None:
            self._highlighter.setDocument(None)
            self._highlighter.deleteLater()
            self._highlighter = None

    def set_code(self, code: str, name: str = "", language: str = "python") -> None:
        self._text = str(code or "")
        self._name.setText(name or self._default_name(language))
        lines = self._text.splitlines() or [""]
        self._body.set_lines(lines, diff=False)
        self._detach_highlighter()
        self._highlighter = _Highlighter(self._body.document(), language)

    def set_json(self, value, name: str = "") -> None:
        """The parameters of a processing call, pretty-printed."""
        try:
            text = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            text = str(value)
        self.set_code(text, name or "parameters.json", "json")

    def set_diff(self, before: str, after: str, name: str = "") -> None:
        """A unified diff of ``before`` and ``after``: added lines green on the green tint, removed lines red on the red tint, the prefix in the."""


        self._detach_highlighter()
        self._text = ""





        raw = list(difflib.unified_diff((before or "").splitlines(), (after or "").splitlines(),
                                        lineterm="", n=3))
        rows = []
        for index, line in enumerate(raw):



            if index < 2 and line.startswith(("---", "+++")):
                continue
            prefix = line[:1]
            if line.startswith("@@"):
                rows.append(("@", line))
            elif prefix in ("+", "-"):
                rows.append((prefix, line[1:]))
            else:
                rows.append(("", line[1:] if prefix == " " else line))
        if not rows:
            rows = [("", self.tr("No changes"))]
            self._text = ""
        else:
            self._text = "\n".join(raw)
        self._name.setText(name or self.tr("changes"))
        self._body.set_lines(rows, diff=True)

    def text(self) -> str:
        return self._text

    @staticmethod
    def _default_name(language: str) -> str:
        return {"python": "script.py", "json": "parameters.json"}.get(language, "text")



    def _on_copy(self) -> None:
        try:
            QApplication.clipboard().setText(self._text)
        except (RuntimeError, AttributeError):
            return
        self._copy.setText(self.tr("Copied"))



        self._copied_timer.start(_COPIED_MS)

    def to_markdown(self) -> str:
        from .transcript import fence

        return fence(self._text, "python" if self._highlighter is not None
                     and self._highlighter.language == "python" else "")


def _copy_icon(widget):
    from qgis.PyQt.QtGui import QIcon

    icon = QIcon()
    icon.addPixmap(pixmap_for(widget, "copy", 11, qcolor(INK_2)))
    icon.addPixmap(pixmap_for(widget, "copy", 11, qcolor(INK)), QIcon.Mode.Active)
    return icon


__all__ = ["CodeBlock"]
