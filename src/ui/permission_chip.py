# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The permission mode chip in the composer's bottom row."""
















from __future__ import annotations

from qgis.PyQt.QtCore import QCoreApplication, QEvent, QPoint, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.plan import autopilot_allowed, stated
from .font_scale import scale_qss_font_px
from .icons import icon_for, pixmap_for
from .shared import exec_dialog
from .style import (
    _BTN_MODE,
    BTN_SMALL_PX,
    FIELD,
    FONT_BODY,
    FONT_HINT,
    FONT_MICRO,
    HOVER,
    INK,
    INK_2,
    INK_3,
    LINE,
    LINE_STRONG,
    RADIUS_CARD,
    RADIUS_CONTROL,
    SURFACE,
    repolish,
)
from .styles import BRAND_BLUE, BRAND_GREEN, BRAND_RED

CAREFUL, ASK, AUTO = "careful", "ask", "auto"
MODES = (CAREFUL, ASK, AUTO)


def autopilot_pickable(paid: bool) -> bool:
    """Whether the Autopilot row may be picked, rather than wear the Pro tag."""








    return autopilot_allowed() if stated("yolo") else bool(paid)


DEFAULT_MODE = ASK




MODE_VISUALS = {
    CAREFUL: ("shield", BRAND_BLUE),
    ASK: ("shield_check", BRAND_GREEN),
    AUTO: ("bolt", BRAND_RED),
}
_TILE_SIZE = 26
_ROW_PAD_X = 10
_ROW_PAD_Y = 7
_ROW_SPACING = 10
_POPOVER_MARGIN = 4

_WRAP_SLACK = 6
_TILE_GLYPH = 15
_POPOVER_WIDTH = 300
_CHECK_SIZE = 14
_CHEVRON_SIZE = 12
_SHIELD_SIZE = 14


def mode_visual(approval: str) -> tuple:
    """``(glyph name, accent colour hex)`` for a mode."""
    return MODE_VISUALS.get(normalize_mode(approval), MODE_VISUALS[DEFAULT_MODE])


def glyph_tile(widget, glyph: str, accent: str, size: int = _TILE_SIZE,
               glyph_size: int = _TILE_GLYPH) -> QLabel:
    """A small rounded tile: ``glyph`` in ``accent`` on a faint tint of it."""
    color = QColor(accent)
    tile = QLabel(widget)
    tile.setFixedSize(size, size)
    tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
    tile.setStyleSheet(
        f"QLabel {{ background: rgba({color.red()}, {color.green()}, {color.blue()}, 0.16);"
        f" border: none; border-radius: {size // 3 + 2}px; }}")
    tile.setPixmap(pixmap_for(widget, glyph, glyph_size, color))
    return tile


def mode_tile(widget, approval: str, size: int = _TILE_SIZE, glyph_size: int = _TILE_GLYPH) -> QLabel:
    """A small rounded tile, the mode's glyph in its accent on a faint tint of it."""
    glyph, accent = mode_visual(approval)
    return glyph_tile(widget, glyph, accent, size, glyph_size)


def select_qss(name: str) -> str:
    """The site's select button for a ``QToolButton`` named ``name``: the ``_BTN_MODE`` family (surface on a hairline, 8 px corners, 28 px, the."""



    return _BTN_MODE + (


        f"QToolButton#{name} {{ border-color: transparent; color: {INK_2};"
        f" padding: 0 {_CHEVRON_SIZE + 10}px 0 6px; font-size: {FONT_BODY}px; }}"
        f"QToolButton#{name}:hover {{ color: {INK}; }}"
        f'QToolButton#{name}:pressed, QToolButton#{name}[active="true"] {{ color: {INK}; }}'
    )



chip_qss = select_qss


_CHIP_QSS = select_qss("permissionChip")

_POPOVER_QSS = scale_qss_font_px(
    f"QFrame#permissionPopover {{ background: {SURFACE};"
    f" border: 1px solid {LINE_STRONG}; border-radius: {RADIUS_CARD}px; }}"
    f"QFrame#permissionRow {{ background: transparent; border: none; border-radius: {RADIUS_CONTROL}px; }}"
    f'QFrame#permissionRow[hover="true"] {{ background: {HOVER}; }}'
    f"QLabel#permissionName {{ font-size: {FONT_BODY}px; font-weight: 500;"
    f" color: {INK}; background: transparent; border: none; }}"
    f"QLabel#permissionNote {{ font-size: {FONT_HINT}px; color: {INK_2};"
    " background: transparent; border: none; }"
    f"QLabel#permissionHead {{ font-size: {FONT_MICRO}px; font-weight: 600;"
    f" letter-spacing: 0.7px; color: {INK_3}; background: transparent; border: none; }}"
    f"QLabel#permissionTag {{ font-size: {FONT_MICRO}px; color: {INK_2}; background: {FIELD};"
    f" border: 1px solid {LINE}; border-radius: 8px; padding: 1px 7px; }}"
    "QLabel { background: transparent; border: none; }"
)


def normalize_mode(approval: str) -> str:
    approval = str(approval or "")
    return approval if approval in MODES else DEFAULT_MODE


class _ModeRow(QFrame):
    """One mode: bold name, muted line, check when current."""

    picked = pyqtSignal(str)
    locked_picked = pyqtSignal(str)

    def __init__(self, approval: str, name: str, note: str, parent=None):
        super().__init__(parent)
        self.approval = approval
        self.setObjectName("permissionRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("hover", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(_ROW_PAD_X, _ROW_PAD_Y, _ROW_PAD_X, _ROW_PAD_Y)
        row.setSpacing(_ROW_SPACING)
        row.addWidget(mode_tile(self, approval), 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)



        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self._name = QLabel(name, self)
        self._name.setObjectName("permissionName")
        head.addWidget(self._name)
        head.addStretch(1)
        self._tag = QLabel(QCoreApplication.translate("PermissionChip", "Upgrade"), self)
        self._tag.setObjectName("permissionTag")
        self._tag.hide()
        head.addWidget(self._tag, 0, Qt.AlignmentFlag.AlignVCenter)
        self._check = QLabel(self)
        self._check.setFixedSize(_CHECK_SIZE, _CHECK_SIZE)
        head.addWidget(self._check, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addLayout(head)
        self._note = QLabel(note, self)
        self._note.setObjectName("permissionNote")
        self._note.setWordWrap(True)
        col.addWidget(self._note)
        row.addLayout(col, 1)
        self._current = False
        self._locked = False
        self.set_current(False)

    def set_locked(self, locked: bool) -> None:
        """A locked row carries the Upgrade tag and never the check."""
        self._locked = bool(locked)
        self._tag.setVisible(self._locked)
        self._check.setVisible(not self._locked)
        if self._locked:
            self._check.clear()

    def is_locked(self) -> bool:
        return self._locked

    def set_current(self, current: bool) -> None:
        self._current = bool(current)
        if self._current:
            self._check.setPixmap(pixmap_for(self, "check", _CHECK_SIZE))
        else:
            self._check.clear()

    def fit_to_width(self, width: int) -> None:
        """Take the height this row needs at ``width``."""






        self.ensurePolished()
        self._name.ensurePolished()
        self._note.ensurePolished()
        text_width = max(60, width - 2 * _POPOVER_MARGIN - 2 * _ROW_PAD_X
                         - _TILE_SIZE - _ROW_SPACING)
        metrics = self._note.fontMetrics()
        if metrics.horizontalAdvance(self._note.text()) <= text_width - _WRAP_SLACK:



            note_height = metrics.height()
        else:
            note_height = self._note.heightForWidth(text_width)
            if note_height <= 0:
                note_height = metrics.boundingRect(
                    0, 0, text_width, 0, int(Qt.TextFlag.TextWordWrap),
                    self._note.text()).height()
        self._note.setFixedHeight(note_height)




        head = max(self._name.sizeHint().height(),
                   self._tag.sizeHint().height() if self._locked else _CHECK_SIZE)
        self.setFixedHeight(2 * _ROW_PAD_Y + head + 2 + note_height)

    def enterEvent(self, event):  # noqa: N802 - Qt override
        self.setProperty("hover", True)
        repolish(self)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        self.setProperty("hover", False)
        repolish(self)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            (self.locked_picked if self._locked else self.picked).emit(self.approval)
        super().mouseReleaseEvent(event)

    def changeEvent(self, event):  # noqa: N802 - Qt override
        super().changeEvent(event)
        try:
            if event.type() == QEvent.Type.PaletteChange and self._current:
                self._check.setPixmap(pixmap_for(self, "check", _CHECK_SIZE))
        except (RuntimeError, AttributeError):
            pass


class PermissionPopover(QFrame):
    """The neutral card with the three mode rows. Closes on a pick or a click outside."""

    picked = pyqtSignal(str)
    upgrade_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("permissionPopover")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_POPOVER_QSS)
        col = QVBoxLayout(self)
        col.setContentsMargins(_POPOVER_MARGIN, _POPOVER_MARGIN, _POPOVER_MARGIN, _POPOVER_MARGIN)
        col.setSpacing(0)


        self._head = QLabel(QCoreApplication.translate(
            "PermissionChip", "AI permissions").upper(), self)
        self._head.setObjectName("permissionHead")
        self._head.setContentsMargins(_ROW_PAD_X, 6, _ROW_PAD_X, 4)
        col.addWidget(self._head)
        self._rows: dict[str, _ModeRow] = {}
        for approval, name, note in mode_texts(self):
            row = _ModeRow(approval, name, note, self)
            row.picked.connect(self._on_picked)
            row.locked_picked.connect(self._on_locked)
            self._rows[approval] = row
            col.addWidget(row)

    def set_current(self, approval: str) -> None:
        approval = normalize_mode(approval)
        for key, row in self._rows.items():
            row.set_current(key == approval and not row.is_locked())

    def set_paid(self, paid: bool) -> None:
        """Autopilot is pickable where the plan allows it; elsewhere it gets the tag."""
        row = self._rows.get(AUTO)
        if row is not None:
            row.set_locked(not autopilot_pickable(paid))

    def fit(self, width: int) -> None:
        """Fix the sheet at ``width``, tall enough for every row's own line."""



        self.ensurePolished()
        self.setFixedWidth(width)
        height = 2 * _POPOVER_MARGIN + self._head.sizeHint().height()
        for row in self._rows.values():
            row.fit_to_width(width)
            height += row.height()
        self.setFixedHeight(height)

    def _on_picked(self, approval: str) -> None:
        self.hide()
        self.picked.emit(approval)

    def _on_locked(self, _approval: str) -> None:
        self.hide()
        self.upgrade_requested.emit()


def mode_texts(widget: QWidget | None = None) -> tuple:
    """``(approval, short name, one-line description)`` for the three modes."""




    return (
        (CAREFUL, QCoreApplication.translate("PermissionChip", "Ask first"),
         QCoreApplication.translate("PermissionChip", "Asks your permission for every change.")),
        (ASK, QCoreApplication.translate("PermissionChip", "Balanced"),
         QCoreApplication.translate(
             "PermissionChip", "Asks before deleting or overwriting.")),
        (AUTO, QCoreApplication.translate("PermissionChip", "Autopilot"),
         QCoreApplication.translate(
             "PermissionChip", "Never asks. Works on its own.")),
    )


class PermissionChip(QToolButton):
    """Shield + mode name + chevron. ``mode_changed(approval)`` after a confirmed pick."""

    mode_changed = pyqtSignal(str)
    upgrade_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("permissionChip")
        self.setStyleSheet(_CHIP_QSS)
        self.setProperty("active", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setIconSize(QSize(_SHIELD_SIZE, _SHIELD_SIZE))
        self.setToolTip(self.tr("What AI Agent may do without asking"))
        self.setAccessibleName(self.tr("Permission mode"))
        self._mode = DEFAULT_MODE
        self._paid = True


        self._compact = False
        self._popover: PermissionPopover | None = None



        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.clicked.connect(self._open)
        self._paint()



    def mode(self) -> str:
        """What the next message runs on. A locked Autopilot answers Balanced."""
        return self._mode if (self._mode != AUTO or autopilot_pickable(self._paid)) else ASK

    def set_mode(self, approval: str) -> None:
        """Show ``approval`` without emitting: the settings are the source of truth."""
        self._mode = normalize_mode(approval)
        self._paint()
        if self._popover is not None:
            self._popover.set_current(self._mode)

    def set_paid(self, paid: bool) -> None:
        """The session frame's plan: Autopilot is pickable, or it carries Upgrade."""
        self._paid = bool(paid)
        if self._popover is not None:
            self._popover.set_paid(self._paid)
            self._popover.set_current(self._mode)
        self._paint()

    def is_paid(self) -> bool:
        return self._paid

    def _names(self) -> dict:
        return {approval: name for approval, name, _note in mode_texts(self)}

    def set_compact(self, compact: bool) -> None:
        """Drop the mode's name, keeping the shield and the chevron."""







        compact = bool(compact)
        if compact == self._compact:
            return
        self._compact = compact
        self._paint()

    def is_compact(self) -> bool:
        return self._compact

    def width_for(self, compact: bool) -> int:
        """The width this chip would ask for with or without its name."""




        name = self._names().get(self.mode(), "")
        text = 0 if compact else self.fontMetrics().horizontalAdvance(name) + 4
        return 6 + _SHIELD_SIZE + text + _CHEVRON_SIZE + 10 + 2

    def _paint(self) -> None:
        shown = self.mode()
        glyph, accent = mode_visual(shown)
        self.setIcon(icon_for(self, glyph, _SHIELD_SIZE, QColor(accent)))


        name = self._names().get(shown, "")
        self.setText("" if self._compact else name)
        self.setAccessibleName(self.tr("Permission mode"))
        self.setAccessibleDescription(name)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override




        hint = super().sizeHint()
        return QSize(self.width_for(self._compact), max(hint.height(), BTN_SMALL_PX))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override



        return QSize(self.width_for(True), self.sizeHint().height())

    def paintEvent(self, event):  # noqa: N802 - Qt override
        super().paintEvent(event)
        from qgis.PyQt.QtGui import QPainter

        pixmap = pixmap_for(self, "chevron_down", _CHEVRON_SIZE)
        painter = QPainter(self)
        x = self.width() - _CHEVRON_SIZE - 6
        y = (self.height() - _CHEVRON_SIZE) // 2
        painter.drawPixmap(x, y, pixmap)
        painter.end()

    def changeEvent(self, event):  # noqa: N802 - Qt override
        super().changeEvent(event)
        try:
            if event.type() == QEvent.Type.PaletteChange:
                self._paint()
        except (RuntimeError, AttributeError):
            pass



    def popover(self) -> PermissionPopover:
        if self._popover is None:
            self._popover = PermissionPopover(self)
            self._popover.picked.connect(self._on_picked)
            self._popover.upgrade_requested.connect(self.upgrade_requested.emit)
            self._popover.installEventFilter(self)
        self._popover.set_paid(self._paid)
        self._popover.set_current(self._mode)
        return self._popover

    def _open(self) -> None:
        popover = self.popover()
        popover.fit(_POPOVER_WIDTH)
        above = self.mapToGlobal(QPoint(0, 0))
        self.setProperty("active", True)
        repolish(self)
        y = above.y() - popover.height() - 6
        screen = QApplication.screenAt(above) if hasattr(QApplication, "screenAt") else None
        if screen is not None and y < screen.availableGeometry().top():
            y = above.y() + self.height() + 6
        popover.move(above.x(), y)
        popover.show()

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if watched is self._popover and event.type() == QEvent.Type.Hide:
            self.setProperty("active", False)
            repolish(self)
        return super().eventFilter(watched, event)

    def _on_picked(self, approval: str) -> None:
        approval = normalize_mode(approval)
        if approval == AUTO and not autopilot_pickable(self._paid):
            self.upgrade_requested.emit()
            return
        if approval == self._mode:
            return
        if approval == AUTO and not self._confirm_autopilot():
            return
        self.set_mode(approval)
        self.mode_changed.emit(approval)

    def _confirm_autopilot(self) -> bool:
        box = QMessageBox(self.window())
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.tr("Switch to Autopilot?"))
        box.setText(self.tr("Switch to Autopilot?"))
        box.setInformativeText(self.tr(
            "AI Agent will delete layers and overwrite files without asking. "
            "Running code and anything that spends credits still ask. "
            "Undo covers the project, not every file on disk. "
            "The next chat starts in Balanced again."))
        switch = box.addButton(self.tr("Switch"), QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton(self.tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        exec_dialog(box)
        return box.clickedButton() is switch
