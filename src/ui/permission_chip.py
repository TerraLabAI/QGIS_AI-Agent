# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



















from __future__ import annotations

from qgis.PyQt.QtCore import QCoreApplication, QEvent, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QIcon
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.plan import autopilot_allowed, stated
from ..core.served_state import get_pro_offer
from .confirm_dialog import ask_confirm
from .font_scale import scale_qss_font_px
from .icons import icon_for, pixmap_for
from .quota_card import get_pro_label, pro_price_per_month, trial_short
from .shared import event_pos, paint_styled_ground, place_by_box, round_popup_corners, settle_hover, show_popped
from .style import (
    _BTN_MODE,
    ACCENT,
    ACCENT_INK,
    BTN_SMALL_PX,
    FIELD,
    FONT_BODY,
    FONT_HINT,
    HOVER,
    HOVER_ON,
    INK,
    INK_2,
    LINE_STRONG,
    OFFER_SOFT,
    OFFER_SOFT_INK,
    RADIUS_CARD,
    RADIUS_CONTROL,
    SURFACE,
    WEIGHT_MEDIUM,
    repolish,
)
from .styles import BRAND_BLUE, BRAND_RED
from .widgets import paint_keyboard_ring

CAREFUL, ASK, AUTO = "careful", "ask", "auto"
MODES = (CAREFUL, ASK, AUTO)


def autopilot_pickable(paid: bool) -> bool:









    return autopilot_allowed() if stated("yolo") else bool(paid)


DEFAULT_MODE = ASK




MODE_VISUALS = {
    CAREFUL: ("shield", BRAND_BLUE),
    ASK: ("shield_check", ACCENT),
    AUTO: ("bolt", BRAND_RED),
}
_TILE_SIZE = 26
_TILE_GLYPH = 15

_ROW_TILE = 24
_ROW_GLYPH = 19
_ROW_PAD_X = 10
_ROW_PAD_Y = 10
_ROW_SPACING = 12
_ROW_GAP = 2
_POPOVER_MARGIN = 6
_WRAP_SLACK = 6
_POPOVER_WIDTH = 320
_POPOVER_MIN_WIDTH = 260
_CHECK_SIZE = 16
_CHEVRON_SIZE = 12
_SHIELD_SIZE = 16

_CHIP_GLYPHS = {CAREFUL: "lu.shield", ASK: "lu.shield-check", AUTO: "lu.zap"}


def mode_visual(approval: str) -> tuple:

    return MODE_VISUALS.get(normalize_mode(approval), MODE_VISUALS[DEFAULT_MODE])


def paint_tile(tile: QLabel, glyph: str, accent: str, glyph_size: int = _TILE_GLYPH,
               tinted: bool = True) -> None:



    color = QColor(accent if tinted else INK_2)
    tile.setStyleSheet("QLabel { background: transparent; border: none; }")
    tile.setPixmap(pixmap_for(tile, glyph, glyph_size, color))


def glyph_tile(widget, glyph: str, accent: str, size: int = _TILE_SIZE,
               glyph_size: int = _TILE_GLYPH, tinted: bool = True) -> QLabel:

    tile = QLabel(widget)
    tile.setFixedSize(size, size)
    tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
    paint_tile(tile, glyph, accent, glyph_size, tinted)
    return tile


def mode_tile(widget, approval: str, size: int = _TILE_SIZE, glyph_size: int = _TILE_GLYPH,
              tinted: bool = True) -> QLabel:

    glyph, accent = mode_visual(approval)
    return glyph_tile(widget, glyph, accent, size, glyph_size, tinted)


def select_qss(name: str) -> str:




    return _BTN_MODE + scale_qss_font_px(


        f"QToolButton#{name} {{ border-color: transparent; color: {INK_2};"
        f" padding: 0 {_CHEVRON_SIZE + 10}px 0 6px; font-size: {FONT_BODY - 1}px; }}"
        f"QToolButton#{name}:hover {{ color: {INK}; }}"
        f'QToolButton#{name}:pressed, QToolButton#{name}[active="true"] {{ color: {INK}; }}'
    )


_CHIP_QSS = select_qss("permissionChip")

_POPOVER_QSS = scale_qss_font_px(
    f"QFrame#permissionPopover {{ background: {SURFACE};"
    f" border: 1px solid {LINE_STRONG}; border-radius: {RADIUS_CARD}px; }}"
    f"QFrame#permissionRow {{ background: transparent; border: none; border-radius: {RADIUS_CONTROL}px; }}"
    f'QFrame#permissionRow[current="true"] {{ background: {FIELD}; }}'
    f'QFrame#permissionRow[hover="true"] {{ background: {HOVER}; }}'
    f'QFrame#permissionRow[held="true"] {{ background: {HOVER_ON}; }}'
    f"QLabel#permissionName {{ font-size: {FONT_BODY}px; font-weight: {WEIGHT_MEDIUM};"
    f" color: {INK}; background: transparent; border: none; }}"
    f"QLabel#permissionNote {{ font-size: {FONT_HINT}px; color: {INK_2};"
    " background: transparent; border: none; }"
    f"QLabel#permissionHead {{ font-size: {FONT_BODY}px; font-weight: {WEIGHT_MEDIUM};"
    f" color: {INK}; background: transparent; border: none; }}"


    f"QPushButton#permissionTag {{ font-size: {FONT_HINT}px; font-weight: {WEIGHT_MEDIUM}; color: {OFFER_SOFT_INK};"
    f" background: {OFFER_SOFT}; border: none; border-radius: 10px; padding: 2px 8px; min-height: 16px; }}"
    f"QLabel#permissionPrice {{ font-size: {FONT_HINT}px; color: {INK_2};"
    " background: transparent; border: none; }"
    "QLabel { background: transparent; border: none; }"
)


def normalize_mode(approval: str) -> str:
    approval = str(approval or "")
    return approval if approval in MODES else DEFAULT_MODE


class _ModeRow(QFrame):



    picked = pyqtSignal(str)
    locked_picked = pyqtSignal(str)

    def __init__(self, approval: str, name: str, line: str, detail: str, parent=None):
        super().__init__(parent)
        self.approval = approval
        self.setObjectName("permissionRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("hover", False)
        self.setProperty("current", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(detail)
        row = QHBoxLayout(self)
        row.setContentsMargins(_ROW_PAD_X, _ROW_PAD_Y, _ROW_PAD_X, _ROW_PAD_Y)
        row.setSpacing(_ROW_SPACING)
        self._tile = mode_tile(self, approval, _ROW_TILE, _ROW_GLYPH)
        row.addWidget(self._tile, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(_ROW_GAP)
        self._name = QLabel(name, self)
        self._name.setObjectName("permissionName")
        col.addWidget(self._name)
        self._note = QLabel(line, self)
        self._note.setObjectName("permissionNote")
        self._note.setWordWrap(True)
        col.addWidget(self._note)
        self._price = QLabel(self)
        self._price.setObjectName("permissionPrice")
        self._price.hide()
        col.addWidget(self._price)
        row.addLayout(col, 1)
        self._tag = QPushButton(get_pro_label(), self)
        self._tag.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tag.setObjectName("permissionTag")
        self._tag.hide()
        row.addWidget(self._tag, 0, Qt.AlignmentFlag.AlignVCenter)
        self._check = QLabel(self)
        self._check.setFixedSize(_CHECK_SIZE, _CHECK_SIZE)
        row.addWidget(self._check, 0, Qt.AlignmentFlag.AlignVCenter)
        for label in (self._tile, self._name, self._note, self._price, self._tag, self._check):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._current = False
        self._locked = False
        self.set_current(False)

    def set_locked(self, locked: bool) -> None:

        self._locked = bool(locked)
        self._label_tag()
        self._tag.setVisible(self._locked)

        price = pro_price_per_month() if self._locked else ""
        self._price.setText(price)
        self._price.setVisible(bool(price))
        self._check.setVisible(not self._locked)
        if self._locked:
            self._check.clear()

    def _label_tag(self) -> None:

        self._tag.setText(trial_short() if get_pro_offer() == "trial" else get_pro_label())

    def is_locked(self) -> bool:
        return self._locked

    def set_current(self, current: bool) -> None:
        self._current = bool(current)
        self.setProperty("current", self._current)
        repolish(self)
        if self._current:
            self._check.setPixmap(pixmap_for(self, "check", _CHECK_SIZE, QColor(ACCENT_INK)))
        else:
            self._check.clear()

    def fit_to_width(self, width: int) -> None:







        for widget in (self, self._name, self._note, self._price, self._tag):
            widget.ensurePolished()
        room = width - 2 * _POPOVER_MARGIN - 2 * _ROW_PAD_X - _ROW_TILE - _ROW_SPACING
        self._label_tag()
        trailing = self._tag.sizeHint().width() if self._locked else _CHECK_SIZE
        text_width = max(60, room - _ROW_SPACING - trailing)
        metrics = self._note.fontMetrics()
        if metrics.horizontalAdvance(self._note.text()) <= text_width - _WRAP_SLACK:


            note_height = metrics.height()
        else:
            note_height = metrics.boundingRect(
                0, 0, text_width, 0, int(Qt.TextFlag.TextWordWrap), self._note.text()).height()
        self._note.setFixedSize(text_width, note_height)
        price = (_ROW_GAP + self._price.fontMetrics().height()) if self._price.text() else 0
        text = self._name.sizeHint().height() + _ROW_GAP + note_height + price
        self.setFixedHeight(2 * _ROW_PAD_Y + max(_ROW_TILE, text))

    def enterEvent(self, event):  # noqa: N802
        self.setProperty("hover", True)
        repolish(self)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.setProperty("hover", False)
        repolish(self)
        super().leaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        self.setProperty("held", True)
        repolish(self)
        event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802
        self.setProperty("held", False)
        repolish(self)

        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event_pos(event)):
            self.pick()
        super().mouseReleaseEvent(event)

    def pick(self) -> None:
        (self.locked_picked if self._locked else self.picked).emit(self.approval)

    def is_current(self) -> bool:
        return self._current

    def set_keyed(self, keyed: bool) -> None:

        self.setProperty("hover", bool(keyed))
        repolish(self)

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        try:
            if event.type() == QEvent.Type.PaletteChange and self._current:
                self._check.setPixmap(pixmap_for(self, "check", _CHECK_SIZE, QColor(ACCENT_INK)))
        except (RuntimeError, AttributeError):
            pass


class PermissionPopover(QFrame):


    picked = pyqtSignal(str)
    upgrade_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        round_popup_corners(self)
        self.setObjectName("permissionPopover")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_POPOVER_QSS)
        col = QVBoxLayout(self)
        col.setContentsMargins(_POPOVER_MARGIN, _POPOVER_MARGIN, _POPOVER_MARGIN, _POPOVER_MARGIN)
        col.setSpacing(2)


        self._head = QLabel(QCoreApplication.translate(
            "PermissionChip", "AI permissions"), self)
        self._head.setObjectName("permissionHead")
        self._head.setContentsMargins(_ROW_PAD_X, 8, _ROW_PAD_X, 6)
        col.addWidget(self._head)
        self._rows: dict[str, _ModeRow] = {}
        lines = mode_lines()
        for approval, name, note in mode_texts(self):
            row = _ModeRow(approval, name, lines[approval], note, self)
            row.picked.connect(self._on_picked)
            row.locked_picked.connect(self._on_locked)
            self._rows[approval] = row
            col.addWidget(row)

    def set_current(self, approval: str) -> None:
        approval = normalize_mode(approval)
        for key, row in self._rows.items():
            row.set_current(key == approval and not row.is_locked())

    def set_paid(self, paid: bool) -> None:

        row = self._rows.get(AUTO)
        if row is not None:
            row.set_locked(not autopilot_pickable(paid))

    def shows_pro_tag(self) -> bool:
        row = self._rows.get(AUTO)
        return row is not None and row.is_locked()

    def fit(self, width: int) -> None:




        self.ensurePolished()
        self.setFixedWidth(width)
        height = 2 * _POPOVER_MARGIN + self._head.sizeHint().height()
        for row in self._rows.values():
            row.fit_to_width(width)
            height += row.height() + self.layout().spacing()
        self.setFixedHeight(height)

    def paintEvent(self, event):  # noqa: N802
        paint_styled_ground(self)
        super().paintEvent(event)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._keyed = None

    def keyPressEvent(self, event):  # noqa: N802

        key = event.key()
        rows = list(self._rows.values())
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            step = 1 if key in (Qt.Key.Key_Down, Qt.Key.Key_Tab) else -1
            keyed = getattr(self, "_keyed", None)
            if keyed is None:

                target = next((i for i, row in enumerate(rows) if row.is_current()), 0)
            else:
                target = (rows.index(keyed) + step) % len(rows)
            self._keyed = rows[target]
            for row in rows:
                row.set_keyed(row is self._keyed)
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and getattr(self, "_keyed", None):
            self._keyed.pick()
            return
        super().keyPressEvent(event)

    def _on_picked(self, approval: str) -> None:
        self.hide()
        self.picked.emit(approval)

    def _on_locked(self, _approval: str) -> None:
        self.hide()
        self.upgrade_requested.emit()


def mode_lines() -> dict:


    return {
        CAREFUL: QCoreApplication.translate("PermissionChip", "Asks before every change."),
        ASK: QCoreApplication.translate("PermissionChip", "Asks only before risky changes."),
        AUTO: QCoreApplication.translate("PermissionChip", "Works on its own and rarely asks."),
    }


def mode_texts(widget: QWidget | None = None) -> tuple:





    return (
        (CAREFUL, QCoreApplication.translate("PermissionChip", "Ask first"),
         QCoreApplication.translate("PermissionChip", "Asks your permission for every change.")),
        (ASK, QCoreApplication.translate("PermissionChip", "Balanced"),
         QCoreApplication.translate(
             "PermissionChip", "Asks before deleting, overwriting, installing or reaching an unknown site.")),
        (AUTO, QCoreApplication.translate("PermissionChip", "Autopilot"),
         QCoreApplication.translate(
             "PermissionChip",
             "Works on its own. Asks only about credits, installs, other plugins and unknown sites.")),
    )


class PermissionChip(QToolButton):


    mode_changed = pyqtSignal(str)
    upgrade_requested = pyqtSignal()
    upsell_shown = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("permissionChip")
        self.setStyleSheet(_CHIP_QSS)
        self.setProperty("active", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
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

        return self._mode if (self._mode != AUTO or autopilot_pickable(self._paid)) else ASK

    def set_mode(self, approval: str) -> None:

        self._mode = normalize_mode(approval)
        self._paint()
        if self._popover is not None:
            self._popover.set_current(self._mode)

    def set_paid(self, paid: bool) -> None:

        self._paid = bool(paid)
        if self._popover is not None:
            self._popover.set_paid(self._paid)
            self._popover.set_current(self._mode)
        self._paint()

    def _names(self) -> dict:
        return {approval: name for approval, name, _note in mode_texts(self)}

    def set_compact(self, compact: bool) -> None:








        compact = bool(compact)
        if compact == self._compact:
            return
        self._compact = compact
        self._paint()

    def width_for(self, compact: bool) -> int:





        if compact:
            return BTN_SMALL_PX
        name = self._names().get(self.mode(), "")
        return 6 + _SHIELD_SIZE + self.fontMetrics().horizontalAdvance(name) + 4 + _CHEVRON_SIZE + 10 + 2

    def _chip_visual(self) -> tuple:


        return _CHIP_GLYPHS[self.mode()], INK_2

    def _paint(self) -> None:
        shown = self.mode()
        glyph, accent = self._chip_visual()


        self.setIcon(QIcon() if self._compact
                     else icon_for(self, glyph, _SHIELD_SIZE, QColor(accent)))
        name = self._names().get(shown, "")
        self.setText("" if self._compact else name)
        if self._compact and name:
            self.setToolTip(self.tr("{mode}: what AI Agent may do without asking").format(mode=name))
        else:
            self.setToolTip(self.tr("What AI Agent may do without asking"))
        self.setAccessibleName(self.tr("Permission mode"))
        self.setAccessibleDescription(name)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802




        hint = super().sizeHint()
        return QSize(self.width_for(self._compact), max(hint.height(), BTN_SMALL_PX))

    def minimumSizeHint(self) -> QSize:  # noqa: N802



        return QSize(self.width_for(True), self.sizeHint().height())

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        from qgis.PyQt.QtGui import QPainter

        painter = QPainter(self)
        if self._compact:
            glyph, accent = self._chip_visual()
            painter.drawPixmap((self.width() - _SHIELD_SIZE) // 2, (self.height() - _SHIELD_SIZE) // 2,
                               pixmap_for(self, glyph, _SHIELD_SIZE, QColor(accent)))
        else:
            painter.drawPixmap(self.width() - _CHEVRON_SIZE - 6, (self.height() - _CHEVRON_SIZE) // 2,
                               pixmap_for(self, "chevron_down", _CHEVRON_SIZE))
        painter.end()
        paint_keyboard_ring(self)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            return
        super().keyPressEvent(event)

    def changeEvent(self, event):  # noqa: N802
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
        box = self.parentWidget() or self
        popover.fit(max(_POPOVER_MIN_WIDTH, min(_POPOVER_WIDTH, box.width())))
        self.setProperty("active", True)
        repolish(self)
        show_popped(popover, place_by_box(popover, box))
        if popover.shows_pro_tag():
            self.upsell_shown.emit()

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self._popover and event.type() == QEvent.Type.Hide:
            self.setProperty("active", False)
            settle_hover(self)
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

        glyph, accent = mode_visual(AUTO)
        return ask_confirm(
            self.window(),
            window_title=self.tr("Autopilot"),
            title=self.tr("Turn on Autopilot?"),
            points=(
                ("lu.navigation", self.tr("Works on its own") + "\n" + self.tr(
                    "The AI agent runs every step without asking, even to delete a layer or run code."), accent),
                ("shield_check", self.tr("Still asks for what matters") + "\n" + self.tr(
                    "Spending credits or reaching an unknown site."), ACCENT),
                ("undo", self.tr("Undo is there") + "\n" + self.tr(
                    "It brings your project back. Files on disk are not covered."), BRAND_BLUE),
            ),
            note=self.tr("The next chat starts in Balanced again."),
            cancel_text=self.tr("Cancel"),
            confirm_text=self.tr("Turn on Autopilot"),
            glyph=glyph,
            accent=accent,
            object_name="autopilotConfirm",
        )
