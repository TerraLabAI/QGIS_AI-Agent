# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later













from __future__ import annotations

from qgis.PyQt.QtCore import QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..icons import icon_for, pixmap_for
from . import common as C
from .pressable import mouse_through

_SEARCH_W = 240
_LABEL_W = 168


def search_pill(parent: QWidget, name: str, placeholder: str) -> QLineEdit:

    field = QLineEdit(parent)
    field.setObjectName(name)
    field.setStyleSheet(C.search_qss(name))
    field.setPlaceholderText(placeholder)
    field.setClearButtonEnabled(True)
    field.setFixedWidth(C.px(_SEARCH_W))
    glyph = QLabel(field)
    glyph.setPixmap(pixmap_for(glyph, "search", 16, QColor(C.T.text_2)))
    glyph.setStyleSheet("background: transparent;")
    glyph.setFixedSize(16, 16)
    glyph.move(14, max(0, (C.px(36) - 16) // 2))
    mouse_through(glyph)
    return field


def primary_button(parent: QWidget, text: str) -> QPushButton:
    button = QPushButton(text, parent)
    button.setStyleSheet(C.primary_qss())
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setAutoDefault(False)
    return button


def label(parent: QWidget, text: str, size: int = C.BODY_PX, colour: str = "",
          weight: int = 400, wrap: bool = False) -> QLabel:
    widget = QLabel(str(text or ""), parent)
    widget.setStyleSheet(C.text_qss(size, colour, weight))
    widget.setWordWrap(wrap)
    if wrap:
        widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


class PageHeader(QWidget):



    def __init__(self, parent=None, title: str = "", subtitle: str = "",
                 right: QWidget | None = None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(C.px(C.SPACE_2))
        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(C.px(4))
        self.title = label(self, title, C.TITLE_PX, C.T.text, C.MEDIUM, wrap=True)
        words.addWidget(self.title)
        self.subtitle = label(self, subtitle, C.BODY_PX, C.T.text_2, wrap=True)
        self.subtitle.setVisible(bool(subtitle))
        words.addWidget(self.subtitle)
        row.addLayout(words, 1)
        self.right = right
        if right is not None:
            right.setParent(self)
            row.addWidget(right, 0, Qt.AlignmentFlag.AlignTop)

    def set_text(self, title: str, subtitle: str = "") -> None:
        self.title.setText(str(title or ""))
        self.subtitle.setText(str(subtitle or ""))
        self.subtitle.setVisible(bool(subtitle))


class Breadcrumb(QWidget):


    crumb = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(C.px(4))
        self.setFixedHeight(C.px(28))

    def set_trail(self, names: list) -> None:
        while self._row.count():
            item = self._row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        names = [str(n or "") for n in names if str(n or "")]
        for index, name in enumerate(names):
            if index:
                chevron = QLabel(self)
                chevron.setPixmap(pixmap_for(chevron, "chevron_right", 14, QColor(C.T.text_2)))
                chevron.setStyleSheet("background: transparent;")
                self._row.addWidget(chevron, 0, Qt.AlignmentFlag.AlignVCenter)
            if index == len(names) - 1:
                self._row.addWidget(label(self, name, C.BODY_PX, C.T.text), 0,
                                    Qt.AlignmentFlag.AlignVCenter)
                continue
            link = QPushButton(name, self)
            link.setCursor(Qt.CursorShape.PointingHandCursor)
            link.setAutoDefault(False)
            link.setStyleSheet(C.qss(
                f"QPushButton {{ background: transparent; border: none; padding: 2px 4px;"
                f" margin-left: -4px; font-size: {C.BODY_PX}px; color: {C.T.text_2};"
                f" border-radius: 6px; }}"
                f"QPushButton:hover {{ color: {C.T.text}; background: {C.T.hover}; }}"))
            link.clicked.connect(lambda _c=False, i=index: self.crumb.emit(i))
            self._row.addWidget(link, 0, Qt.AlignmentFlag.AlignVCenter)
        self._row.addStretch(1)


def text_button(parent: QWidget, text: str) -> QPushButton:

    button = QPushButton(text, parent)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setAutoDefault(False)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setStyleSheet(C.qss(
        f"QPushButton {{ background: transparent; border: none; padding: 6px 10px;"
        f" font-size: {C.BODY_PX}px; font-weight: {C.MEDIUM}; color: {C.T.text_2};"
        f" border-radius: {C.px(16)}px; }}"
        f"QPushButton:hover {{ color: {C.T.text}; background: {C.T.hover}; }}"
        f"QPushButton:pressed {{ background: {C.T.press}; }}"))
    return button


def section_title(parent: QWidget, text: str) -> QLabel:
    return label(parent, text, C.SECTION_PX, C.T.text, C.MEDIUM)


class UserBubble(QWidget):



    def __init__(self, text: str, parent=None, rich: bool = False):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addStretch(1)
        self._bubble = QFrame(self)
        self._bubble.setObjectName("userBubble")
        self._bubble.setStyleSheet(
            f"QFrame#userBubble {{ background: {C.T.bubble}; border: none;"
            f" border-radius: {C.px(20)}px; }}")
        inner = QVBoxLayout(self._bubble)
        inner.setContentsMargins(C.px(16), C.px(10), C.px(16), C.px(10))
        self.text = QLabel(self._bubble)
        self.text.setStyleSheet(C.text_qss(C.BODY_PX, C.T.text))
        self.text.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
        self.text.setText(str(text or ""))
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        inner.addWidget(self.text)
        row.addWidget(self._bubble, 0)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)


        pad = 2 * C.px(16) + 2
        room = max(C.px(160), int(self.width() * 0.8))
        self.text.ensurePolished()
        ideal = self.text.fontMetrics().horizontalAdvance(self.text.text()) + pad
        self._bubble.setFixedWidth(min(room, ideal))


class InfoTable(QFrame):


    link_activated = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("infoTable")
        self.setStyleSheet(
            f"QFrame#infoTable {{ background: transparent; border: 1px solid {C.T.border};"
            f" border-radius: {C.px(C.RADIUS_TILE)}px; }}"
            f"QFrame#infoRule {{ background: {C.T.border}; border: none; }}")
        self._col = QVBoxLayout(self)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(0)
        self._rows = 0

    def rows(self) -> int:
        return self._rows

    def add(self, name: str, value, rich: bool = False) -> None:

        if self._rows:
            rule = QFrame(self)
            rule.setObjectName("infoRule")
            rule.setFixedHeight(1)
            self._col.addWidget(rule)
        row = QWidget(self)
        row.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(C.px(16), C.px(12), C.px(16), C.px(12))
        lay.setSpacing(C.px(C.SPACE_2))
        key = label(row, name, C.BODY_PX, C.T.text_2, wrap=True)
        key.setFixedWidth(C.px(_LABEL_W))
        lay.addWidget(key, 0, Qt.AlignmentFlag.AlignTop)
        if isinstance(value, QWidget):
            value.setParent(row)
            lay.addWidget(value, 1)
        else:
            text = QLabel(row)
            text.setStyleSheet(C.text_qss(C.BODY_PX, C.T.text))
            text.setWordWrap(True)
            text.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
            text.setText(str(value or ""))
            text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            if rich:
                text.setOpenExternalLinks(False)
                text.linkActivated.connect(self.link_activated.emit)
            else:
                text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lay.addWidget(text, 1)
        self._col.addWidget(row)
        self._rows += 1


class ArrowButton(QPushButton):


    def __init__(self, glyph: str, parent=None):
        super().__init__(parent)
        side = C.px(32)
        self.setFixedSize(side, side)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoDefault(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setIcon(icon_for(self, glyph, 16, QColor(C.T.text),
                              disabled_color=QColor(C.T.border)))
        self.setIconSize(QSize(16, 16))
        self.setStyleSheet(
            f"QPushButton {{ background: {C.T.bg}; border: 1px solid {C.T.border};"
            f" border-radius: {side // 2}px; }}"
            f"QPushButton:hover {{ background: {C.T.hover}; }}"
            f"QPushButton:pressed {{ background: {C.T.press}; }}"
            f"QPushButton:disabled {{ background: {C.T.bg}; border-color: {C.T.hover}; }}")
