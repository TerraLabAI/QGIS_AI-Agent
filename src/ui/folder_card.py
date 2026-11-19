# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QPainter
from qgis.PyQt.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget

from .card_base import _Card
from .card_controls import (
    _ASK_CARD_QSS,
    _ASK_DONE_LINE_QSS,
    _ASK_MARGINS,
    _BTN_GHOST_PILL,
    _BTN_PRIMARY_PILL,
    _BTN_QUIET_LINK,
    AnswerFooter,
    _pill,
    ask_head,
)
from .font_scale import scale_qss_font_px
from .icons import icon_for, pixmap_for
from .permission_chip import _CHEVRON_SIZE, _POPOVER_MARGIN, select_qss
from .shared import AGENT_ACTION, AGENT_CARD_KIND, paint_styled_ground, place_by_box, round_popup_corners, show_popped
from .style import (
    BTN_SMALL_PX,
    FONT_BODY,
    FONT_HINT,
    INK,
    INK_2,
    INSET,
    LINE_STRONG,
    MONO_FAMILY,
    RADIUS_CARD,
    RADIUS_CONTROL,
    SPACE_OUTER,
    SURFACE,
    WEIGHT_MEDIUM,
    repolish,
)
from .widgets import ElidedLabel

DENY, READ, WRITE = "deny", "read", "write"

_WELL_QSS = scale_qss_font_px(
    f"QFrame#folderWell {{ background: {INSET}; border: none; border-radius: {RADIUS_CONTROL}px; }}"
    f"QLabel#folderPath {{ color: {INK}; font-family: {MONO_FAMILY}; font-size: {FONT_BODY}px;"
    " background: transparent; }"
    f"QLabel#folderNote {{ color: {INK_2}; font-size: {FONT_HINT}px; background: transparent; }}"
)
_NOTE_QSS = scale_qss_font_px(f"color: {INK_2}; font-size: {FONT_HINT}px; background: transparent;")
_POPOVER_QSS = scale_qss_font_px(
    f"QFrame#folderPopover {{ background: {SURFACE}; border: 1px solid {LINE_STRONG};"
    f" border-radius: {RADIUS_CARD}px; }}"
    f"QLabel#folderHead {{ color: {INK}; font-size: {FONT_BODY}px; font-weight: {WEIGHT_MEDIUM};"
    " background: transparent; }"
    f"QFrame#folderRow {{ background: {INSET}; border: none; border-radius: {RADIUS_CONTROL}px; }}"
    f"QLabel#folderRowPath {{ color: {INK}; font-family: {MONO_FAMILY}; font-size: {FONT_HINT}px;"
    " background: transparent; }"
    f"QLabel#folderRowRight {{ color: {INK_2}; font-size: {FONT_HINT}px; background: transparent; }}"
)
_POPOVER_WIDTH = 360


def _is_share(path: str) -> bool:
    return str(path).replace("/", "\\").startswith("\\\\")


class FolderCard(_Card):


    decided = pyqtSignal(str, str)

    def __init__(self, folder: str, parent=None):
        super().__init__(None, parent, frame_qss=_ASK_CARD_QSS)
        self.set_margins(*_ASK_MARGINS)
        self._col.setSpacing(SPACE_OUTER)
        self.folder = folder
        self.decision: str | None = None
        self.setProperty(AGENT_CARD_KIND, "folder")
        self._body = QWidget(self)
        col = QVBoxLayout(self._body)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(SPACE_OUTER)
        col.addWidget(ask_head(self._body, self.tr("Let AI Agent open this folder?")))
        well = QFrame(self._body)
        well.setObjectName("folderWell")
        well.setStyleSheet(_WELL_QSS)
        lines = QVBoxLayout(well)
        lines.setContentsMargins(10, 8, 10, 8)
        lines.setSpacing(2)
        path = QLabel(folder, well)
        path.setObjectName("folderPath")
        path.setWordWrap(True)
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lines.addWidget(path)
        if _is_share(folder):
            where = QLabel(self.tr("A folder on another computer of your network."), well)
            where.setObjectName("folderNote")
            where.setWordWrap(True)
            lines.addWidget(where)
        col.addWidget(well)
        note = QLabel(self.tr("What AI Agent reads there is sent to the AI model. Kept for this project."), self._body)
        note.setWordWrap(True)
        note.setStyleSheet(_NOTE_QSS)
        col.addWidget(note)
        buttons = []
        for choice, text, qss in ((DENY, self.tr("Deny"), _BTN_GHOST_PILL),
                                  (WRITE, self.tr("Read and write"), _BTN_GHOST_PILL),
                                  (READ, self.tr("Read only"), _BTN_PRIMARY_PILL)):
            button = QPushButton(text, self._body)
            button.setStyleSheet(qss)
            _pill(button)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setProperty(AGENT_ACTION, choice)
            button.setProperty("agentFolder", folder)
            button.clicked.connect(lambda _=False, c=choice: self._answer(c))
            buttons.append(button)
        buttons[-1].setDefault(True)
        footer = AnswerFooter(self._body)
        footer.set_widgets(None, buttons)
        col.addWidget(footer)
        self._col.addWidget(self._body)
        self._done = ElidedLabel("", self, mode=Qt.TextElideMode.ElideMiddle)
        self._done.setStyleSheet(_ASK_DONE_LINE_QSS)
        self._done.hide()
        self._col.addWidget(self._done)

    def _answer(self, choice: str) -> None:
        if self.decision is not None:
            return
        self.decision = choice
        words = {DENY: self.tr("Denied"), READ: self.tr("Read only"), WRITE: self.tr("Read and write")}[choice]
        self._done.setText(f"{words} · {self.folder}")
        self._body.hide()
        self._done.show()
        self.decided.emit(self.folder, choice)

    def close_unanswered(self) -> None:

        if self.decision is None:
            self.decision = DENY
            self._done.setText(f"{self.tr('Denied')} · {self.folder}")
            self._body.hide()
            self._done.show()


class _FolderRow(QFrame):
    removed = pyqtSignal(str)

    def __init__(self, folder: str, write: bool, parent=None):
        super().__init__(parent)
        self.setObjectName("folderRow")
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 6, 6)
        row.setSpacing(8)
        path = ElidedLabel(folder, self, mode=Qt.TextElideMode.ElideMiddle)
        path.setObjectName("folderRowPath")
        path.setToolTip(folder)
        row.addWidget(path, 1)
        right = QLabel(self.tr("Read and write") if write else self.tr("Read only"), self)
        right.setObjectName("folderRowRight")
        row.addWidget(right)
        remove = QPushButton(self.tr("Remove"), self)
        remove.setStyleSheet(_BTN_QUIET_LINK)
        remove.setCursor(Qt.CursorShape.PointingHandCursor)
        remove.setProperty(AGENT_ACTION, "remove_folder")
        remove.setProperty("agentFolder", folder)
        remove.clicked.connect(lambda: self.removed.emit(folder))
        row.addWidget(remove)
        remove.ensurePolished()
        self.setMinimumHeight(remove.sizeHint().height() + 12)


class FolderPopover(QFrame):
    removed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        round_popup_corners(self)
        self.setObjectName("folderPopover")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_POPOVER_QSS)
        self._col = QVBoxLayout(self)
        self._col.setContentsMargins(_POPOVER_MARGIN + 4, _POPOVER_MARGIN + 4, _POPOVER_MARGIN + 4, _POPOVER_MARGIN + 4)
        self._col.setSpacing(6)

    def set_grants(self, grants) -> None:
        while self._col.count():
            item = self._col.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        head = QLabel(self.tr("Folders AI Agent can open in this project"), self)
        head.setObjectName("folderHead")
        head.setWordWrap(True)
        self._col.addWidget(head)
        for folder, write in grants:
            row = _FolderRow(folder, write, self)
            row.removed.connect(self.removed.emit)
            self._col.addWidget(row)
        note = QLabel(self.tr("The project folder and its layers' folders are always open for reading."), self)
        note.setWordWrap(True)
        note.setStyleSheet(_NOTE_QSS)
        self._col.addWidget(note)

    def paintEvent(self, event):  # noqa: N802
        paint_styled_ground(self)
        super().paintEvent(event)


class FolderChip(QToolButton):


    removed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("folderChip")
        self.setStyleSheet(select_qss("folderChip"))
        self.setProperty("active", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setIconSize(QSize(16, 16))
        self.setIcon(icon_for(self, "folder", 16, QColor(INK_2)))
        self.setToolTip(self.tr("Folders AI Agent can open in this project"))
        self.setAccessibleName(self.tr("Folders"))
        self._grants: list = []
        self._compact = False
        self._popover: FolderPopover | None = None
        self.clicked.connect(self._open)
        self.hide()

    def set_grants(self, grants) -> None:
        self._grants = [(str(f), bool(w)) for f, w in grants or ()]
        count = len(self._grants)
        words = self.tr("1 folder") if count == 1 else self.tr("{n} folders").format(n=count)


        self.setText(str(count) if self._compact else words)
        self.setAccessibleDescription(words)
        self.setVisible(count > 0)
        self.setFixedWidth(self.sizeHint().width())
        if self._popover is not None:
            if count:
                self._popover.set_grants(self._grants)
                self._popover.adjustSize()
            else:
                self._popover.hide()
        self.updateGeometry()

    def set_compact(self, compact: bool) -> None:
        if bool(compact) != self._compact:
            self._compact = bool(compact)
            self.set_grants(self._grants)

    def width_for(self, compact: bool) -> int:
        count = len(self._grants)
        text = str(count) if compact else (
            self.tr("1 folder") if count == 1 else self.tr("{n} folders").format(n=count))
        self.ensurePolished()
        return 6 + 16 + 4 + self.fontMetrics().horizontalAdvance(text) + _CHEVRON_SIZE + 10 + 8

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.width_for(self._compact), max(super().sizeHint().height(), BTN_SMALL_PX))

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.drawPixmap(self.width() - _CHEVRON_SIZE - 6, (self.height() - _CHEVRON_SIZE) // 2,
                           pixmap_for(self, "chevron_down", _CHEVRON_SIZE))
        painter.end()

    def _open(self) -> None:
        if self._popover is None:
            self._popover = FolderPopover(self)
            self._popover.removed.connect(self.removed.emit)
            self._popover.installEventFilter(self)
        self._popover.set_grants(self._grants)
        box = self.parentWidget() or self
        self._popover.setFixedWidth(max(260, min(_POPOVER_WIDTH, box.width())))
        self._popover.ensurePolished()
        self._popover.layout().activate()
        self._popover.adjustSize()
        self.setProperty("active", True)
        repolish(self)
        show_popped(self._popover, place_by_box(self._popover, box))

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self._popover and event.type() == QEvent.Type.Hide:
            self.setProperty("active", False)
            repolish(self)
        return super().eventFilter(watched, event)
