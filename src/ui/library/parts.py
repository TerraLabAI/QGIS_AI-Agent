# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
















from __future__ import annotations

from qgis.PyQt.QtCore import QRectF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QPainter, QPixmap
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

from ..font_scale import widget_pixel_ratio
from ..icons import has_glyph, icon_for, pixmap_for, render_pixmap
from . import common as C
from .pressable import mouse_through

_SEARCH_W = 240
_LABEL_W = 168

_BADGE_GLYPH = "layers"
_BADGE_GREY = "#8e8e93"


def badge_pixmap(glyph: str, accent: str, side: int, ratio: float = 1.0) -> QPixmap:

    fill = QColor(accent) if accent and QColor(accent).isValid() else QColor(_BADGE_GREY)
    name = glyph if has_glyph(glyph) else _BADGE_GLYPH
    physical = max(1, int(round(side * ratio)))
    pixmap = QPixmap(physical, physical)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        corner = side * 0.26
        painter.drawRoundedRect(QRectF(0, 0, side, side), corner, corner)
        inner = max(8, round(side * 0.74))
        offset = (side - inner) / 2.0
        painter.drawPixmap(QRectF(offset, offset, inner, inner),
                           render_pixmap(name, QColor("#ffffff"), inner, ratio),
                           QRectF(0, 0, inner * ratio, inner * ratio))
    finally:
        painter.end()
    return pixmap


def category_badge(parent: QWidget, glyph: str, accent: str, side: int) -> QLabel:

    badge = QLabel(parent)
    badge.setStyleSheet("background: transparent;")
    badge.setFixedSize(side, side)
    badge.setPixmap(badge_pixmap(glyph, accent, side, widget_pixel_ratio(badge)))
    mouse_through(badge)
    return badge


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

        self._badge_slot = QHBoxLayout()
        self._badge_slot.setContentsMargins(0, 0, 0, 0)
        self._badge_slot.setSpacing(C.px(10))
        self._badge = None
        self._badge_slot.addWidget(self.title, 1, Qt.AlignmentFlag.AlignVCenter)
        words.addLayout(self._badge_slot)
        self.subtitle = label(self, subtitle, C.BODY_PX, C.T.text_2, wrap=True)
        self.subtitle.setVisible(bool(subtitle))
        words.addWidget(self.subtitle)
        row.addLayout(words, 1)
        self.right = right
        if right is not None:
            right.setParent(self)
            row.addWidget(right, 0, Qt.AlignmentFlag.AlignTop)

    def set_badge(self, look: tuple | None) -> None:

        if self._badge is not None:
            self._badge.hide()
            self._badge.setParent(None)
            self._badge.deleteLater()
            self._badge = None
        if look is not None:
            self._badge = category_badge(self, look[0], look[1], C.px(24))
            self._badge_slot.insertWidget(0, self._badge, 0, Qt.AlignmentFlag.AlignVCenter)

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


class EmptyState(QWidget):





    def __init__(self, parent: QWidget, query: str, line: str, actions: list):
        super().__init__(parent)
        from ..shared import tr

        self.setObjectName("libEmptyState")
        col = QVBoxLayout(self)
        col.setContentsMargins(C.px(16), C.px(40), C.px(16), C.px(40))
        col.setSpacing(C.px(8))
        title = label(self, tr("No results for “{q}”").format(q=query), C.SECTION_PX,
                      C.T.text, C.MEDIUM, wrap=True)
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(title)
        hint = label(self, line, C.BODY_PX, C.T.text_2, wrap=True)
        hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(hint)
        col.addSpacing(C.px(8))
        row = QHBoxLayout()
        row.setSpacing(C.px(8))
        row.addStretch(1)
        self.buttons: list = []
        for index, (text, callback) in enumerate(actions):
            button = QPushButton(text, self)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAutoDefault(False)
            button.setStyleSheet(C.primary_qss() if index == len(actions) - 1 else C.ghost_qss())
            button.clicked.connect(lambda _c=False, f=callback: f())
            row.addWidget(button)
            self.buttons.append(button)
        row.addStretch(1)
        col.addLayout(row)
