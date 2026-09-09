# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""ChatGPT's attached-file card, shared by the composer and the user bubble."""










from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, QObject, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QCursor, QPixmap
from qgis.PyQt.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .icons import pixmap_for
from .shared import event_pos
from .style import _BTN_THUMB_CLOSE, HAIRLINE_STRONG, HOVER
from .widgets import ElidedLabel, IconButton

CARD_RADIUS = 12
TILE_SIZE = 36
TILE_GLYPH = 18
CARD_PAD_X = 8
CARD_PAD_Y = 8
CARD_GAP = 10
CARD_HEIGHT = TILE_SIZE + 2 * CARD_PAD_Y
NAME_MAX_WIDTH = 170
NAME_MIN_WIDTH = 80

CLOSE_SIZE = 18
CLOSE_OVERHANG = 6








_QUIET_HOVER_QSS = (
    'QFrame#attachCard[clickable="true"]:hover { background: ' + HOVER + ";"
    " border: 1px solid " + HAIRLINE_STRONG + "; }"
)

_HOVER_EVENTS = frozenset({
    QEvent.Type.Enter, QEvent.Type.Leave, QEvent.Type.HoverEnter,
    QEvent.Type.HoverLeave, QEvent.Type.HoverMove, QEvent.Type.FocusIn,
    QEvent.Type.FocusOut, QEvent.Type.Show, QEvent.Type.Move,
})


class HoverReveal(QObject):
    """Shows ``badge`` only while the pointer is on ``host``, or while the badge holds the keyboard focus."""








    def __init__(self, host: QWidget, badge: QWidget):
        super().__init__(host)
        self._host = host
        self._badge = badge
        badge.setVisible(False)
        host.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        host.installEventFilter(self)
        for child in host.findChildren(QWidget):
            child.installEventFilter(self)

    def eventFilter(self, obj, event):  # noqa: N802 - Qt override
        if event.type() in _HOVER_EVENTS:
            self.sync()
        return False

    def sync(self) -> None:
        try:
            host, badge = self._host, self._badge
            inside = host.rect().contains(host.mapFromGlobal(QCursor.pos()))
            badge.setVisible(bool(inside) or badge.hasFocus())
            if inside:
                badge.raise_()
        except RuntimeError:
            pass


class AttachCard(QWidget):
    """One attached thing: a tile, a name over its kind, an optional x."""

    removed = pyqtSignal()
    clicked = pyqtSignal()

    def __init__(self, name: str, kind: str, pixmap: QPixmap | None = None, parent=None,
                 closable: bool = False, clickable: bool = False, tooltip: str = "",
                 glyph: str = "file"):
        super().__init__(parent)
        self.setObjectName("attachCardHost")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self._closable = bool(closable)
        self._clickable = bool(clickable)
        self._name = str(name or "")
        overhang = CLOSE_OVERHANG if self._closable else 0

        self._frame = QFrame(self)
        self._frame.setObjectName("attachCard")
        self._frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._frame.setFixedHeight(CARD_HEIGHT)
        self._frame.setProperty("clickable", self._clickable)
        if self._clickable:
            self._frame.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(self._frame)
        row.setContentsMargins(CARD_PAD_X, CARD_PAD_Y, CARD_PAD_X + 4, CARD_PAD_Y)
        row.setSpacing(CARD_GAP)

        self._tile = QLabel(self._frame)
        self._tile.setObjectName("attachGlyph")
        self._tile.setFixedSize(TILE_SIZE, TILE_SIZE)
        self._tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tile.setPixmap(pixmap if pixmap is not None and not pixmap.isNull()
                             else pixmap_for(self, glyph, TILE_GLYPH))
        row.addWidget(self._tile, 0, Qt.AlignmentFlag.AlignVCenter)

        lines = QVBoxLayout()
        lines.setContentsMargins(0, 0, 0, 0)
        lines.setSpacing(1)





        self._name_label = ElidedLabel(self._name, self._frame,
                                       Qt.TextElideMode.ElideMiddle)
        self._name_label.setObjectName("attachName")
        self._name_label.setMaximumWidth(NAME_MAX_WIDTH)
        self._name_label.setMinimumWidth(min(NAME_MIN_WIDTH, 40))
        lines.addWidget(self._name_label)
        self._kind_label = QLabel(str(kind or ""), self._frame)
        self._kind_label.setObjectName("attachKind")
        lines.addWidget(self._kind_label)
        row.addLayout(lines, 1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, overhang, overhang, 0)
        outer.setSpacing(0)
        outer.addWidget(self._frame)

        self.set_tooltip(tooltip)

        self._close = None
        self._reveal = None
        if self._closable:
            self._close = IconButton(self, None, 10, self.tr("Remove"), _BTN_THUMB_CLOSE)
            self._close.set_icon("close", 10, QColor(255, 255, 255))
            self._close.setFixedSize(CLOSE_SIZE, CLOSE_SIZE)
            self._close.clicked.connect(self.removed.emit)
            self._close.raise_()


            self._reveal = HoverReveal(self, self._close)
        self.setFixedHeight(CARD_HEIGHT + overhang)



        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(self._name)

    def focusInEvent(self, event):  # noqa: N802 - Qt override
        super().focusInEvent(event)
        if self._close is not None:
            self._close.show()
            self._close.raise_()

    def focusOutEvent(self, event):  # noqa: N802 - Qt override
        super().focusOutEvent(event)
        if self._close is not None and not self.underMouse():
            self._close.hide()

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        key = event.key()
        if self._closable and key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.removed.emit()
            event.accept()
            return
        if self._clickable and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def set_quiet_hover(self, quiet: bool = True) -> None:
        """Trade the green wash for a firmer hairline on hover."""




        self._frame.setStyleSheet(_QUIET_HOVER_QSS if quiet else "")



    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        hint = self._frame.sizeHint()
        overhang = CLOSE_OVERHANG if self._closable else 0
        return QSize(hint.width() + overhang, CARD_HEIGHT + overhang)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return self.sizeHint()

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if self._close is not None:
            self._close.move(self.width() - CLOSE_SIZE, 0)
            self._close.raise_()



    def set_kind(self, kind: str) -> None:
        self._kind_label.setText(str(kind or ""))

    def set_tooltip(self, tooltip: str) -> None:
        tip = tooltip or self._name
        self.setAccessibleDescription(tip)
        self._frame.setToolTip(tip)
        self._name_label.setToolTip(tip)
        self._kind_label.setToolTip(tip)

    def name(self) -> str:
        return self._name

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if (self._clickable and event.button() == Qt.MouseButton.LeftButton
                and self._frame.geometry().contains(event_pos(event))):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)
