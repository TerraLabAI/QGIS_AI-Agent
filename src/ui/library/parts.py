# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

















from __future__ import annotations

from qgis.PyQt.QtCore import QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont, QFontMetrics
from qgis.PyQt.QtWidgets import (
    QBoxLayout,
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
from .pressable import Pressable, labels_through, mouse_through

_SEARCH_W = 240
_LABEL_W = 168


def search_pill(parent: QWidget, name: str, placeholder: str) -> QLineEdit:


    field = QLineEdit(parent)
    field.setObjectName(name)
    field.setPlaceholderText(placeholder)
    field.setClearButtonEnabled(True)
    glyph = QLabel(field)
    glyph.setObjectName("searchGlyph")
    glyph.setPixmap(pixmap_for(glyph, "search", 16, QColor(C.T.text_2)))
    glyph.setStyleSheet("background: transparent;")
    glyph.setFixedSize(16, 16)
    mouse_through(glyph)
    set_pill_wide(field, False)
    return field


_WIDE_SEARCH_W = 640


def set_pill_wide(field: QLineEdit, wide: bool, room: int = 0) -> None:


    height = C.px(44 if wide else 36)
    field.setStyleSheet(C.search_qss(field.objectName(), 44 if wide else 36))
    field.setFixedHeight(height)
    width = min(C.px(_WIDE_SEARCH_W), room) if wide and room > 0 else (
        C.px(_WIDE_SEARCH_W) if wide else C.px(_SEARCH_W))
    field.setFixedWidth(max(C.px(_SEARCH_W), width))
    glyph = field.findChild(QLabel, "searchGlyph")
    if glyph is not None:
        glyph.move(C.px(16), max(0, (height - 16) // 2))


def primary_button(parent: QWidget, text: str) -> QPushButton:

    button = QPushButton(text, parent)
    button.setStyleSheet(C.primary_qss())
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setAutoDefault(False)
    return button


def secondary_button(parent: QWidget, text: str) -> QPushButton:

    button = QPushButton(text, parent)
    button.setStyleSheet(C.secondary_qss())
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


def paragraph(parent: QWidget, text: str, colour: str = "", size: int = C.READ_PX,
              rich: bool = False) -> QLabel:



    body = str(text or "") if rich else C.escape(text).replace("\n", "<br>")
    widget = QLabel(parent)
    widget.setTextFormat(Qt.TextFormat.RichText)
    widget.setWordWrap(True)
    widget.setStyleSheet(C.text_qss(size, colour or C.T.text))
    widget.setText(f'<div style="line-height: {C.LINE_HEIGHT}%;">{body}</div>')
    widget.setMaximumWidth(C.px(C.MEASURE_W))
    widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def edge(widget: QWidget) -> QWidget:

    widget.setContentsMargins(C.px(C.EDGE), 0, C.px(C.EDGE), 0)
    return widget


class PageHeader(QWidget):









    def __init__(self, parent=None, title: str = "", subtitle: str = "",
                 right: QWidget | None = None):
        super().__init__(parent)
        self._hero = False
        words = QVBoxLayout(self)
        words.setContentsMargins(C.px(C.EDGE), 0, C.px(C.EDGE), 0)
        words.setSpacing(0)
        row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(C.px(C.SPACE_2))
        self._row = row
        self.title = label(self, title, C.TITLE_PX, C.T.text, C.MEDIUM, wrap=True)
        row.addWidget(self.title, 1, Qt.AlignmentFlag.AlignVCenter)
        self.right = right
        if right is not None:
            right.setParent(self)
            row.addWidget(right, 0, Qt.AlignmentFlag.AlignVCenter)
        words.addLayout(row)
        self._gap_line = words.count()
        words.addSpacing(C.px(6))
        self.subtitle = label(self, subtitle, C.LEAD_PX, C.T.text_2, wrap=True)
        self.subtitle.setVisible(bool(subtitle))
        words.addWidget(self.subtitle)
        self._lines: list = []
        self._words = words

    def set_hero(self, hero: bool) -> None:

        hero = bool(hero)
        if hero == self._hero:
            return
        self._hero = hero
        centre = Qt.AlignmentFlag.AlignHCenter
        self.title.setStyleSheet(C.text_qss(C.HERO_PX if hero else C.TITLE_PX, C.T.text, C.MEDIUM))
        self.title.setAlignment(centre if hero else Qt.AlignmentFlag.AlignLeft)
        self.subtitle.setAlignment(centre if hero else Qt.AlignmentFlag.AlignLeft)
        if not hero:
            self.subtitle.setMinimumSize(0, 0)
            self.subtitle.setMaximumSize(16777215, 16777215)
        else:
            self._fit_hero_line()
        self._words.setAlignment(self.subtitle, centre if hero else Qt.AlignmentFlag.AlignLeft)
        spacer = self._words.itemAt(self._gap_line)
        if spacer is not None and spacer.spacerItem() is not None:
            spacer.spacerItem().changeSize(0, C.px(C.SPACE_1 if hero else 6))
        for widget in self._lines:
            self._words.setAlignment(widget, centre if hero else Qt.AlignmentFlag.AlignLeft)
            widget.setContentsMargins(0, C.px(C.SPACE_3) if hero else C.px(C.SPACE_2), 0, 0)
        if self.right is not None:
            if hero:
                self._row.removeWidget(self.right)
                self._words.addSpacing(C.px(C.SPACE_4))
                self._words.addWidget(self.right, 0, centre)
            else:
                self._words.removeWidget(self.right)
                last = self._words.itemAt(self._words.count() - 1)
                if last is not None and last.spacerItem() is not None:
                    self._words.removeItem(last)
                self._row.addWidget(self.right, 0, Qt.AlignmentFlag.AlignVCenter)
            if isinstance(self.right, QLineEdit):
                set_pill_wide(self.right, hero, self.width() - 2 * C.px(C.EDGE))
        self.updateGeometry()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if self._hero:
            self._fit_hero_line()
            if isinstance(self.right, QLineEdit):
                set_pill_wide(self.right, True, self.width() - 2 * C.px(C.EDGE))
            return
        if self.right is None:
            return
        self.title.ensurePolished()
        need = (self.title.fontMetrics().horizontalAdvance(self.title.text())
                + self.right.width() + C.px(C.SPACE_3) + 2 * C.px(C.EDGE))
        stacked = self.width() < need
        direction = (QBoxLayout.Direction.TopToBottom if stacked
                     else QBoxLayout.Direction.LeftToRight)
        if self._row.direction() != direction:
            self._row.setDirection(direction)
            self._row.setAlignment(self.right, Qt.AlignmentFlag.AlignLeft if stacked
                                   else Qt.AlignmentFlag.AlignVCenter)

    def _fit_hero_line(self) -> None:


        line = self.subtitle
        line.ensurePolished()
        room = min(C.px(520), max(1, self.width() - 2 * C.px(C.EDGE)))
        advance = line.fontMetrics().horizontalAdvance(line.text()) + 4
        width = advance if advance <= room else min(room, advance // 2 + C.px(48))
        line.setFixedWidth(max(1, width))
        line.setFixedHeight(line.heightForWidth(width))

    def add_line(self, widget: QWidget) -> None:

        widget.setParent(self)
        self._words.insertWidget(self._words.indexOf(self.subtitle) + 1 + len(self._lines), widget)
        self._lines.append(widget)

    def set_text(self, title: str, subtitle: str = "") -> None:
        self.title.setText(str(title or ""))
        self.subtitle.setText(str(subtitle or ""))
        self.subtitle.setVisible(bool(subtitle))
        if self._hero:
            self._fit_hero_line()



_CRUMB_W = 200


class Breadcrumb(QWidget):


    crumb = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(C.px(C.EDGE), 0, C.px(C.EDGE), 0)
        self._row.setSpacing(C.px(4))


        self.setFixedHeight(C.px(C.TOP_BAR_H))

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
            link = QPushButton(self._elided(name), self)
            if link.text() != name:
                link.setToolTip(name)
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

    def _elided(self, name: str) -> str:

        font = QFont(self.font())
        font.setPixelSize(C.px(C.BODY_PX))
        return QFontMetrics(font).elidedText(name, Qt.TextElideMode.ElideRight, C.px(_CRUMB_W))


class SectionHead(Pressable):


    def __init__(self, text: str, parent=None):
        super().__init__(parent, radius=C.px(8))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(C.px(C.EDGE), C.px(4), C.px(C.EDGE), C.px(4))
        row.setSpacing(C.px(4))
        row.addWidget(label(self, text, C.SECTION_PX, C.T.text, C.MEDIUM), 0,
                      Qt.AlignmentFlag.AlignVCenter)
        chevron = QLabel(self)
        chevron.setPixmap(pixmap_for(chevron, "chevron_right", 16, QColor(C.T.text_2)))
        chevron.setStyleSheet("background: transparent;")
        row.addWidget(chevron, 0, Qt.AlignmentFlag.AlignVCenter)
        labels_through(self)
        self.setAccessibleName(text)


def section_title(parent: QWidget, text: str) -> QLabel:

    return edge(label(parent, text, C.SECTION_PX, C.T.text, C.MEDIUM))


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
        key = label(row, name, C.READ_PX, C.T.text_2, wrap=True)
        key.setFixedWidth(C.px(_LABEL_W))
        lay.addWidget(key, 0, Qt.AlignmentFlag.AlignTop)
        if isinstance(value, QWidget):
            value.setParent(row)
            lay.addWidget(value, 1)
        else:
            text = paragraph(row, str(value or ""), rich=rich)
            text.setMaximumWidth(16777215)
            text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            if rich:
                text.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
                text.setOpenExternalLinks(False)
                text.linkActivated.connect(self.link_activated.emit)
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
            last = index == len(actions) - 1
            button = (primary_button if last else secondary_button)(self, text)
            button.clicked.connect(lambda _c=False, f=callback: f())
            row.addWidget(button)
            self.buttons.append(button)
        row.addStretch(1)
        col.addLayout(row)
