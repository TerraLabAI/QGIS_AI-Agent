# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The ``+`` sheet, opened by the composer's round ``+`` button."""
















from __future__ import annotations

from qgis.PyQt.QtCore import QPoint, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import QApplication, QFrame, QVBoxLayout, QWidget

from .popover_rows import _POPOVER_QSS, _Row
from .style import ACCENT, INK_2, LINE_STRONG, RADIUS_CARD, SURFACE



SHEET_WIDTH = 260
_MIN_WIDTH = 200

FILES_ACCENT = INK_2
LAYER_ACCENT = ACCENT

_SHEET_QSS = _POPOVER_QSS + (
    f"QFrame#attachPopover {{ background: {SURFACE};"
    f" border: 1px solid {LINE_STRONG}; border-radius: {RADIUS_CARD}px; }}"
    "QFrame#attachPopover QLabel#exampleTitle { font-weight: 500; }"
)


def _anchored_x(top_left: QPoint, panel: QWidget | None, width: int) -> int:
    """The popover's left edge: the anchor's own, kept inside ``panel``."""
    x = top_left.x()
    if panel is None:
        return x
    left = panel.mapToGlobal(QPoint(0, 0)).x() + 4
    right = left + panel.width() - 8
    return max(left, min(x, right - width))


def place_above(popover: QWidget, anchor: QWidget, panel: QWidget | None, width: int) -> None:
    """Move ``popover`` above ``anchor``, left edges aligned, kept inside ``panel``; below the anchor when the screen has no room above."""


    top_left = anchor.mapToGlobal(QPoint(0, 0))
    x = _anchored_x(top_left, panel, width)
    y = top_left.y() - popover.height() - 6
    screen = QApplication.screenAt(top_left) if hasattr(QApplication, "screenAt") else None
    if screen is not None and y < screen.availableGeometry().top():
        y = top_left.y() + anchor.height() + 6
    popover.move(QPoint(x, y))


def place_below(popover: QWidget, anchor: QWidget, panel: QWidget | None, width: int) -> None:
    """Move ``popover`` under ``anchor``, left edges aligned, kept inside ``panel``; above the anchor when the screen has no room below."""





    top_left = anchor.mapToGlobal(QPoint(0, 0))
    x = _anchored_x(top_left, panel, width)
    y = top_left.y() + anchor.height() + 6
    screen = QApplication.screenAt(top_left) if hasattr(QApplication, "screenAt") else None
    if screen is not None:
        bottom = screen.availableGeometry().bottom()
        if y + popover.height() > bottom:
            above = top_left.y() - popover.height() - 6


            y = above if above >= screen.availableGeometry().top() else max(
                screen.availableGeometry().top(), bottom - popover.height())
    popover.move(QPoint(x, y))


class AttachPopover(QFrame):
    """The sheet. One signal per row; the sheet hides itself before emitting."""

    add_files_requested = pyqtSignal()
    add_layer_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("attachPopover")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_SHEET_QSS)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._rows: list[_Row] = []
        self._focus = -1

        self._col = QVBoxLayout(self)

        self._col.setContentsMargins(6, 6, 6, 6)
        self._col.setSpacing(2)
        self._add_row(
            _Row("paperclip", FILES_ACCENT, self.tr("Add photos & files"), "", "", self),
            self.add_files_requested)
        self._add_row(



            _Row("layers", LAYER_ACCENT, self.tr("Attach a layer of this project"), "", "", self),
            self.add_layer_requested)

    def _add_row(self, row: _Row, signal) -> None:
        row.clicked.connect(lambda: self._choose(signal))
        self._col.addWidget(row)
        self._rows.append(row)

    def _choose(self, signal) -> None:
        self.hide()
        signal.emit()



    def show_above(self, anchor: QWidget, panel: QWidget | None = None) -> None:
        """Open above ``anchor``, left edges aligned, kept inside ``panel``."""
        panel = panel or anchor.window()
        width = SHEET_WIDTH
        if panel is not None:
            width = max(_MIN_WIDTH, min(SHEET_WIDTH, panel.width() - 8))
        self._set_focus(-1)
        self.setFixedWidth(width)
        self.ensurePolished()
        for row in self._rows:
            row._elide()
        self._col.activate()
        self.setFixedHeight(self._col.sizeHint().height())
        place_above(self, anchor, panel, width)
        self.show()
        self.setFocus(Qt.FocusReason.PopupFocusReason)

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        for row in self._rows:
            row._elide()



    def _set_focus(self, index: int) -> None:
        for i, row in enumerate(self._rows):
            row.set_focused(i == index)
        self._focus = index

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.hide()
            return
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self._rows:
            step = 1 if key == Qt.Key.Key_Down else -1
            self._set_focus((self._focus + step) % len(self._rows))
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and 0 <= self._focus < len(self._rows):
            self._rows[self._focus].clicked.emit()
            return
        super().keyPressEvent(event)
