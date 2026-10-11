# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later










from __future__ import annotations

from qgis.PyQt.QtCore import QRectF, QSize, Qt
from qgis.PyQt.QtGui import QBrush, QPainter
from qgis.PyQt.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..core import openui
from .shared import tr
from .style import (
    ACCENT,
    FONT_BASE,
    FONT_BODY,
    FONT_HINT,
    INK,
    INK_2,
    INK_3,
    INSET,
    LINE_SOFT,
    LINE_STRONG,
    RADIUS_CHIP,
    RADIUS_CONTROL,
    SPACE_CARD,
    SPACE_OUTER,
    SPACE_TIGHT,
    TAGS,
    qcolor,
)
from .widgets import ChatLabel, ElidedLabel

_VALUE_PX = 15
_BAR_PX = 8
_LABEL_COL_PX = 84
_SHOWN_COL_PX = 64


def _label(text: str, px: int, color: str, weight: int = 400, wrap: bool = True,
           elide: bool = False, align=None) -> QLabel:
    label = ElidedLabel(text) if elide else ChatLabel(text, wrap=wrap, selectable=True)
    label.setStyleSheet(f"font-size: {px}px; color: {color}; font-weight: {weight}; background: transparent;")
    if align is not None:
        label.setAlignment(align)
    if wrap and not elide:
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


def _tag(text: str, tone: str) -> QLabel:
    background, ink = TAGS.get(tone, TAGS["neutral"])
    label = ElidedLabel(text)
    label.setStyleSheet(f"font-size: {FONT_HINT}px; font-weight: 600; color: {ink}; background: {background};"
                        f" border-radius: {RADIUS_CHIP - 1}px; padding: 0 6px;")
    label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return label


class _Bar(QWidget):


    def __init__(self, share: float, parent=None):
        super().__init__(parent)
        self._share = max(0.0, min(1.0, share))
        self.setFixedHeight(_BAR_PX)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(40, _BAR_PX)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(16, _BAR_PX)

    def paintEvent(self, _event):  # noqa: N802
        width = self.width() * self._share
        if width <= 0:
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(qcolor(ACCENT)))
            radius = _BAR_PX / 2.0
            painter.drawRoundedRect(QRectF(0, 0, max(width, _BAR_PX), _BAR_PX), radius, radius)
        finally:
            painter.end()


class BlockPlaceholder(QLabel):


    def __init__(self, parent=None):
        super().__init__(tr("Preparing a summary…"), parent)
        self.setObjectName("answerBlockPending")
        self.setStyleSheet(f"font-size: {FONT_BODY}px; color: {INK_3}; background: transparent;")


class BlockView(QFrame):


    def __init__(self, block: openui.Block, parent=None):
        super().__init__(parent)
        self.block = block
        self.setObjectName("answerBlock")
        self.setStyleSheet("QFrame#answerBlock { background: transparent; border: none; }")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, SPACE_TIGHT, 0, SPACE_OUTER)
        col.setSpacing(SPACE_CARD)
        build = getattr(self, "_build_" + block.kind.lower(), None)
        if build is not None:
            build(col)

    def text(self) -> str:
        return openui.to_text(self.block)

    def _build_stats(self, col: QVBoxLayout) -> None:
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(SPACE_CARD)
        for index, item in enumerate(self.block.items):
            card = QFrame(self)
            card.setObjectName("answerStat")
            card.setStyleSheet(f"QFrame#answerStat {{ background: {INSET}; border-radius: {RADIUS_CONTROL}px; }}")
            inner = QVBoxLayout(card)
            inner.setContentsMargins(10, 8, 10, 8)
            inner.setSpacing(1)
            if item["label"]:
                inner.addWidget(_label(item["label"], FONT_HINT, INK_3, elide=True))
            inner.addWidget(_label(item["value"], _VALUE_PX, INK, 650, elide=True))
            if item["sub"]:
                inner.addWidget(_label(item["sub"], FONT_HINT, INK_2))
            inner.addStretch(1)
            grid.addWidget(card, index // 2, index % 2)
        for column in range(2):
            grid.setColumnStretch(column, 1)
        col.addLayout(grid)

    def _build_bars(self, col: QVBoxLayout) -> None:
        if self.block.title:
            col.addWidget(_label(self.block.title, FONT_BODY, INK, 600))
        top = max((item["value"] for item in self.block.items), default=0.0) or 1.0
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(SPACE_OUTER)
        grid.setVerticalSpacing(5)
        for row, item in enumerate(self.block.items):
            name = _label(item["label"], FONT_BODY, INK, elide=True)
            name.setMaximumWidth(_LABEL_COL_PX + 40)
            grid.addWidget(name, row, 0)
            grid.addWidget(_Bar(item["value"] / top, self), row, 1, Qt.AlignmentFlag.AlignVCenter)
            shown = _label(item["shown"], FONT_BODY, INK_2, elide=True,
                           align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            shown.setMaximumWidth(_SHOWN_COL_PX + 30)
            grid.addWidget(shown, row, 2)
        grid.setColumnStretch(1, 1)
        col.addLayout(grid)

    def _build_table(self, col: QVBoxLayout) -> None:
        columns = self.block.items
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(0)
        grid.setVerticalSpacing(0)
        numeric = [c["kind"] == "number" for c in columns]
        rows = len(columns[0]["values"])
        for index, column in enumerate(columns):
            right = Qt.AlignmentFlag.AlignRight if numeric[index] else Qt.AlignmentFlag.AlignLeft
            head = _label(column["name"], FONT_HINT, INK_2, 600,
                          align=right | Qt.AlignmentFlag.AlignBottom)
            head.setStyleSheet(head.styleSheet() + f" background: {INSET}; padding: 5px 8px;")
            grid.addWidget(head, 0, index)
            for row in range(rows):
                value = column["values"][row]
                cell = QWidget(self)
                cell.setObjectName("answerCell")
                line = f"border-bottom: 1px solid {LINE_SOFT};" if row < rows - 1 else ""
                cell.setStyleSheet(f"QWidget#answerCell {{ background: transparent; {line} }}")
                cell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                box = QHBoxLayout(cell)
                box.setContentsMargins(8, 6, 8, 6)
                if isinstance(value, tuple):
                    box.addWidget(_tag(value[0], value[1]), 0, Qt.AlignmentFlag.AlignLeft)
                    box.addStretch(1)
                else:
                    box.addWidget(_label(value, FONT_BODY, INK, wrap=not numeric[index], elide=numeric[index],
                                         align=right | Qt.AlignmentFlag.AlignVCenter))
                grid.addWidget(cell, row + 1, index)
            grid.setColumnStretch(index, 1)
        col.addLayout(grid)
        rows_left, cols_left = self.block.more_rows, self.block.more_cols
        if rows_left or cols_left:
            said = []
            if rows_left:
                said.append(tr("+{0} more rows").format(rows_left) if rows_left > 1 else tr("+1 more row"))
            if cols_left:
                said.append(tr("+{0} more columns").format(cols_left) if cols_left > 1 else tr("+1 more column"))
            col.addWidget(_label(", ".join(said), FONT_HINT, INK_3))

    def _build_callout(self, col: QVBoxLayout) -> None:
        tone = self.block.tone
        edge = LINE_STRONG if tone == "neutral" else TAGS.get(tone, TAGS["neutral"])[1]
        box = QFrame(self)
        box.setObjectName("answerCallout")
        box.setStyleSheet(f"QFrame#answerCallout {{ background: {INSET}; border-left: 3px solid {edge};"
                          f" border-top-right-radius: {RADIUS_CONTROL}px;"
                          f" border-bottom-right-radius: {RADIUS_CONTROL}px; }}")
        inner = QVBoxLayout(box)
        inner.setContentsMargins(10, 7, 10, 7)
        inner.setSpacing(1)
        if self.block.title:
            inner.addWidget(_label(self.block.title, FONT_BODY, INK, 600))
        if self.block.text:
            inner.addWidget(_label(self.block.text, FONT_BODY, INK_2))
        col.addWidget(box)

    def _build_steps(self, col: QVBoxLayout) -> None:
        for number, item in enumerate(self.block.items, 1):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(SPACE_OUTER)
            digit = _label(str(number), FONT_BODY, INK_3, 600, wrap=False)
            digit.setFixedWidth(14)
            digit.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
            row.addWidget(digit, 0, Qt.AlignmentFlag.AlignTop)
            words = QVBoxLayout()
            words.setSpacing(1)
            if item["title"]:
                words.addWidget(_label(item["title"], FONT_BASE, INK, 600))
            if item["detail"]:
                words.addWidget(_label(item["detail"], FONT_BODY, INK_2))
            row.addLayout(words, 1)
            col.addLayout(row)
