# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Building blocks of the Settings dialog: the page frame, the setting rows, the switch, the segmented choice, the radio-like choice row, the."""






from __future__ import annotations

from qgis.PyQt.QtCore import QRectF, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont, QFontMetrics, QPainter, QPalette
from qgis.PyQt.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.profile import MEMORY_NOTE_MAX_CHARS
from .icons import ink_of, pixmap_for
from .style import (
    ACCENT,
    ACCENT_DARK,
    FIELD,
    GREEN,
    HOVER_ON,
    INK,
    INK_2,
    INK_3,
    LINE,
    LINE_STRONG,
    ON_ACCENT,
    RADIUS_CARD,
    RED,
    RED_TINT,
    SURFACE,
)
from .styles import INK_HOVER_FILL, INK_PRESSED_FILL
from .widgets import IconButton






MUTED = INK_2
HAIRLINE = LINE
TINT = FIELD
TINT_HOVER = HOVER_ON
TINT_ON = LINE_STRONG

ACCENT_TINT = "rgba(139, 172, 39, 0.10)"
ACCENT_TINT_ON = "rgba(139, 172, 39, 0.20)"
ACCENT_BORDER = "rgba(139, 172, 39, 0.45)"

PAGE_TITLE_QSS = "font-size: 16px; font-weight: 600; color: palette(text); background: transparent;"
PAGE_SUBTITLE_QSS = f"font-size: 12px; color: {MUTED}; background: transparent;"
ROW_TITLE_QSS = "font-size: 13px; color: palette(text); background: transparent;"
ROW_NOTE_QSS = f"font-size: 11px; color: {MUTED}; background: transparent;"
SAVED_HINT_QSS = f"font-size: 11px; color: {MUTED}; background: transparent;"
GROUP_TITLE_QSS = "font-size: 12px; font-weight: 600; color: palette(text); background: transparent;"


GROUP_QSS = (
    "QFrame#settingsGroup { background: transparent;"
    f" border: 1px solid {HAIRLINE}; border-radius: 10px; }}"
    "QFrame#settingsGroup > QFrame#settingsRow { border: none; background: transparent; }"
)

GROUP_FLAT_QSS = (
    "QFrame#settingsGroup { background: transparent; border: none; }"
    "QFrame#settingsGroup > QFrame#settingsRow { border: none; background: transparent; }"
)






def nav_qss(name: str) -> str:
    return (
        f"QListWidget#{name} {{ background: transparent; border: none; outline: none;"
        " padding: 4px 6px; font-size: 13px; }"
        f"QListWidget#{name}::item {{ padding: 7px 8px; border-radius: 8px; color: palette(text);"
        " margin: 1px 0; }"
        f"QListWidget#{name}::item:hover {{ background: {ACCENT_TINT}; }}"
        f"QListWidget#{name}::item:selected {{ background: {ACCENT_TINT_ON}; }}"
    )


def sidebar_qss(name: str) -> str:
    return (
        f"QFrame#{name} {{ background: {TINT};"
        f" border: none; border-right: 1px solid {HAIRLINE}; }}"
        f"QFrame#{name} QLabel {{ background: transparent; border: none; }}"
    )


ROW_DIVIDER_QSS = (f"QFrame {{ background: {HAIRLINE}; border: none;"
                   " max-height: 1px; min-height: 1px; }")


PRIMARY_BTN_QSS = (
    "QPushButton { background: palette(text); color: palette(base); border: none;"
    " border-radius: 8px; padding: 7px 14px; font-size: 12px; font-weight: 600; }"
    f"QPushButton:hover {{ background: {INK_HOVER_FILL}; }}"
    f"QPushButton:pressed {{ background: {INK_PRESSED_FILL}; }}"
)
GHOST_BTN_QSS = (
    f"QPushButton {{ background: transparent; color: palette(text); border: 1px solid {HAIRLINE};"
    " border-radius: 8px; padding: 6px 14px; font-size: 12px; }"
    f"QPushButton:hover {{ background: {ACCENT_TINT}; border-color: {ACCENT_BORDER}; }}"
    f"QPushButton:pressed {{ background: {ACCENT_TINT_ON}; }}"
)
DANGER_GHOST_BTN_QSS = (
    f"QPushButton {{ background: transparent; color: {RED}; border: 1px solid {HAIRLINE};"
    " border-radius: 8px; padding: 6px 14px; font-size: 12px; }"
    f"QPushButton:hover {{ background: {RED_TINT}; border-color: {RED}; }}"
)
LINK_BTN_QSS = (
    f"QPushButton {{ background: transparent; color: {MUTED}; border: none; padding: 2px 4px;"
    " font-size: 11px; text-decoration: underline; }"
    "QPushButton:hover { color: palette(text); }"
)
COMBO_QSS = (
    f"QComboBox {{ color: palette(text); background: palette(base); border: 1px solid {HAIRLINE};"
    " border-radius: 8px; padding: 5px 28px 5px 10px; font-size: 12px; min-height: 18px; }"
    "QComboBox:hover { border-color: rgba(128,128,128,0.45); }"
    "QComboBox::drop-down { border: none; width: 26px; subcontrol-origin: padding;"
    " subcontrol-position: center right; }"
    "QComboBox QAbstractItemView { color: palette(text); background: palette(base);"
    f" border: 1px solid {HAIRLINE}; selection-background-color: {TINT_ON};"
    " selection-color: palette(text); outline: none; }"
)


def combo_qss(widget) -> str:
    """``COMBO_QSS`` plus the chevron: a styled combo drops the native arrow, so the glyph is rendered once per ink into the plugin's state folder."""

    return COMBO_QSS + _chevron_rule(widget)


def _chevron_rule(widget) -> str:
    import os

    from ..core.settings import state_dir

    ink = QColor(ink_of(widget))
    ink.setAlphaF(0.8)
    path = os.path.join(state_dir(), f"chevron_down_{ink.rgba():08x}.png")
    if not os.path.exists(path):
        try:
            pixmap_for(widget, "chevron_down", 12, ink).save(path, "PNG")
        except Exception:  # noqa: BLE001 - an arrow-less combo still works
            return ""
    url = path.replace("\\", "/")
    return f'QComboBox::down-arrow {{ image: url("{url}"); width: 12px; height: 12px; }}'


INPUT_QSS = (
    f"QLineEdit {{ color: palette(text); background: palette(base); border: 1px solid {HAIRLINE};"
    " border-radius: 8px; padding: 6px 10px; font-size: 12px; }"
    "QLineEdit:focus { border-color: rgba(128,128,128,0.55); }"
)
TEXTAREA_QSS = (
    f"QPlainTextEdit {{ color: palette(text); background: palette(base); border: 1px solid {HAIRLINE};"
    " border-radius: 8px; padding: 6px 8px; font-size: 12px; }"
    "QPlainTextEdit:focus { border-color: rgba(128,128,128,0.55); }"
)
SEGMENT_QSS = (
    "QPushButton { background: transparent; color: palette(text); border: none;"
    " border-radius: 6px; padding: 4px 12px; font-size: 12px; }"
    f"QPushButton:hover {{ background: {ACCENT_TINT}; }}"
    f"QPushButton:checked {{ background: {ACCENT_TINT_ON}; font-weight: 600; }}"
)
_SEGMENT_PAD_PX = 12
SEGMENT_FRAME_QSS = f"QFrame#segmented {{ background: {TINT}; border: 1px solid {HAIRLINE}; border-radius: 8px; }}"











_SCROLLBAR_QSS = (
    "QScrollBar:vertical { background: transparent; border: none; width: 10px; margin: 0; }"
    "QScrollBar:horizontal { background: transparent; border: none; height: 10px; margin: 0; }"
    "QScrollBar::handle:vertical { background: rgba(128,128,128,0.35); border: none;"
    " border-radius: 3px; min-height: 32px; margin: 2px 3px; }"
    "QScrollBar::handle:horizontal { background: rgba(128,128,128,0.35); border: none;"
    " border-radius: 3px; min-width: 32px; margin: 3px 2px; }"
    "QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover"
    " { background: rgba(128,128,128,0.6); }"
    "QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; border: none;"
    " background: transparent; }"
    "QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }"
)
SCROLL_QSS = (
    "QScrollArea { background: transparent; border: none; }"
    "QScrollArea > QWidget > QWidget { background: transparent; }"
) + _SCROLLBAR_QSS


class Switch(QAbstractButton):
    """A ChatGPT-style pill switch: ink track when on, faint track when off."""

    def __init__(self, parent=None, checked: bool = False):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(bool(checked))
        self.setCursor(Qt.CursorShape.PointingHandCursor)




        self.setFixedSize(38, 22)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt signature
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        ink = QColor(ink_of(self))
        on = self.isChecked()
        track = QColor(ink)
        track.setAlphaF(0.85 if on else 0.28)
        if not self.isEnabled():
            track.setAlphaF(0.18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        knob_d = rect.height() - 6
        x = rect.right() - 3 - knob_d if on else rect.left() + 3
        if on:
            knob = QColor(self.palette().color(QPalette.ColorRole.Base))
        else:
            knob = QColor(ink)
            knob.setAlphaF(0.9)
        painter.setBrush(knob)
        painter.drawEllipse(QRectF(x, rect.top() + 3, knob_d, knob_d))
        painter.end()


class SettingRow(QFrame):
    """A title, an optional muted note, and a control at the right."""

    def __init__(self, title: str, note: str = "", control: QWidget | None = None,
                 parent=None, control_below: bool = False):
        super().__init__(parent)
        self.setObjectName("settingsRow")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        outer = QVBoxLayout(self) if control_below else QHBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(10 if control_below else 16)
        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(2)
        self.title_label = QLabel(title, self)
        self.title_label.setStyleSheet(ROW_TITLE_QSS)
        self.title_label.setWordWrap(True)
        words.addWidget(self.title_label)
        self.note_label = QLabel(note, self)
        self.note_label.setStyleSheet(ROW_NOTE_QSS)
        self.note_label.setWordWrap(True)
        self.note_label.setVisible(bool(note))
        words.addWidget(self.note_label)
        if control_below:
            outer.addLayout(words)
            if control is not None:
                outer.addWidget(control)
        else:
            outer.addLayout(words, 1)
            if control is not None:
                outer.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        self._name_control(control, title, note)

    @staticmethod
    def _name_control(control, title: str, note: str) -> None:
        """Give the row's words to the control that carries the setting."""






        if control is None:
            return
        try:
            if not control.accessibleName():
                control.setAccessibleName(title)
            if note and not control.accessibleDescription():
                control.setAccessibleDescription(note)
        except (RuntimeError, AttributeError):
            return

    def set_note(self, note: str) -> None:
        self.note_label.setText(note)
        self.note_label.setVisible(bool(note))


class SettingGroup(QFrame):
    """The rounded frame that stacks rows with hairlines between them."""

    def __init__(self, parent=None, flat: bool = False):
        super().__init__(parent)
        self.setObjectName("settingsGroup")
        self.setStyleSheet(GROUP_FLAT_QSS if flat else GROUP_QSS)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._col = QVBoxLayout(self)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(0)

    def add_row(self, row: QWidget) -> QWidget:
        if self._col.count():
            line = QFrame(self)
            line.setStyleSheet(ROW_DIVIDER_QSS)
            line.setFixedHeight(1)
            self._col.addWidget(line)
        self._col.addWidget(row)
        return row

    def clear(self) -> None:
        while self._col.count():
            item = self._col.takeAt(0)
            widget = item.widget()
            if widget is not None:



                widget.hide()
                widget.setParent(None)
                widget.deleteLater()


class SectionCard(QFrame):
    """The group frame, around a section that is not a stack of rows."""






    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsGroup")
        self.setStyleSheet(GROUP_QSS)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._col = QVBoxLayout(self)
        self._col.setContentsMargins(12, 12, 12, 10)
        self._col.setSpacing(8)

    def add(self, widget: QWidget) -> QWidget:
        widget.setParent(self)
        self._col.addWidget(widget)
        return widget


class Segmented(QFrame):
    """Two to four exclusive choices in one rounded frame."""

    changed = pyqtSignal(str)

    def __init__(self, choices: list, current: str, parent=None):
        super().__init__(parent)
        self.setObjectName("segmented")
        self.setStyleSheet(SEGMENT_FRAME_QSS)
        row = QHBoxLayout(self)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for value, label in choices:
            btn = QPushButton(label, self)
            btn.setCheckable(True)
            btn.setAutoDefault(False)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(SEGMENT_QSS)


            bold = QFont(btn.font())
            bold.setBold(True)
            btn.setMinimumWidth(QFontMetrics(bold).horizontalAdvance(label) + 2 * _SEGMENT_PAD_PX)
            btn.setChecked(value == current)
            btn.clicked.connect(lambda _=False, v=value: self.changed.emit(v))
            self._group.addButton(btn)
            row.addWidget(btn)
            self._buttons[value] = btn

    def set_value(self, value: str) -> None:
        btn = self._buttons.get(value)
        if btn is not None:
            btn.setChecked(True)


class ChoiceRow(QFrame):
    """A radio-like row: a dot, a bold name, a muted line. Click anywhere."""

    picked = pyqtSignal(str)

    def __init__(self, value: str, name: str, note: str, parent=None, tile=None):
        super().__init__(parent)
        self.value = value
        self.setObjectName("settingsRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(12)
        self._dot = QLabel(self)
        self._dot.setFixedSize(16, 16)
        self._dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self._dot, 0, Qt.AlignmentFlag.AlignTop)
        if tile is not None:
            tile.setParent(self)
            row.addWidget(tile, 0, Qt.AlignmentFlag.AlignTop)
        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(2)
        self._name = QLabel(name, self)
        self._name.setStyleSheet(ROW_TITLE_QSS)
        words.addWidget(self._name)
        self._note = QLabel(note, self)
        self._note.setStyleSheet(ROW_NOTE_QSS)
        self._note.setWordWrap(True)
        words.addWidget(self._note)
        row.addLayout(words, 1)
        self.set_current(False)

    def set_current(self, current: bool) -> None:
        self._current = bool(current)
        if current:
            self._dot.setPixmap(pixmap_for(self, "check", 14))
            self._dot.setStyleSheet("background: transparent; border: none;")
            self._name.setStyleSheet(ROW_TITLE_QSS + " font-weight: 600;")
        else:
            self._dot.setPixmap(pixmap_for(self, "circle", 14, self._faint_ink()))
            self._name.setStyleSheet(ROW_TITLE_QSS)

    def _faint_ink(self) -> QColor:
        ink = QColor(ink_of(self))
        ink.setAlphaF(0.4)
        return ink

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if event.button() == Qt.MouseButton.LeftButton:
            self.picked.emit(self.value)
            event.accept()
            return
        super().mousePressEvent(event)


class NoteRow(QFrame):
    """One memory note: the text, its source, a pencil that rewords it, an x that removes it."""








    removed = pyqtSignal(str)
    edited = pyqtSignal(str, str)

    def __init__(self, text: str, caption: str, parent=None, note_id: str = ""):
        super().__init__(parent)
        self.text = text
        self.note_id = note_id or text
        self.setObjectName("settingsRow")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 8, 8)
        row.setSpacing(8)
        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(1)
        body = QLabel(text, self)
        body.setStyleSheet("font-size: 12px; color: palette(text); background: transparent;")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        words.addWidget(body)
        self._body = body
        self._field = None
        self._words = words
        if caption:
            meta = QLabel(caption, self)
            meta.setStyleSheet(ROW_NOTE_QSS)
            words.addWidget(meta)
        row.addLayout(words, 1)
        pencil = IconButton(self, "pencil", 12, "")
        pencil.setFixedSize(24, 24)
        pencil.clicked.connect(self.start_editing)
        row.addWidget(pencil, 0, Qt.AlignmentFlag.AlignTop)
        self.edit_button = pencil
        close = IconButton(self, "close", 12, "")
        close.setFixedSize(24, 24)
        close.clicked.connect(lambda: self.removed.emit(self.note_id))
        row.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        self.close_button = close

    def start_editing(self) -> None:
        """Put a field where the sentence is, with the sentence in it."""
        if self._field is not None:
            return
        field = QLineEdit(self.text, self)
        field.setStyleSheet(INPUT_QSS)
        field.setMaxLength(MEMORY_NOTE_MAX_CHARS)
        field.returnPressed.connect(self._commit)
        field.editingFinished.connect(self._commit)
        self._words.replaceWidget(self._body, field)
        self._body.hide()
        self._field = field
        self.edit_button.setEnabled(False)
        field.setFocus()
        field.selectAll()

    def _commit(self) -> None:
        """Send the new wording once, whether it arrived by Enter or by leaving the field."""
        field, self._field = self._field, None
        if field is None:
            return
        text = field.text().strip()
        field.blockSignals(True)
        self._words.replaceWidget(field, self._body)
        field.deleteLater()
        self._body.show()
        self.edit_button.setEnabled(True)
        if text and text != self.text:
            self.edited.emit(self.note_id, text)











PRO_CARD_QSS = (
    f"QFrame#proCard {{ background: {ACCENT_TINT}; border: 1px solid {ACCENT_BORDER};"
    " border-radius: 10px; }"
    "QFrame#proCard QLabel { background: transparent; border: none; }"
)
PRO_CARD_TITLE_QSS = "font-size: 12px; font-weight: 600; color: palette(text); background: transparent;"
PRO_BADGE_QSS = (
    f"QLabel#proBadge {{ font-size: 10px; font-weight: 700; letter-spacing: 0.5px; color: {MUTED};"
    f" background: {TINT}; border: 1px solid {HAIRLINE}; border-radius: 7px; padding: 1px 7px; }}"
)





LOCKED_OPACITY = 0.42


def set_section_locked(*widgets, locked: bool = True) -> None:
    """Grey a Pro-only control and take its keyboard away, or give it back."""














    from qgis.PyQt.QtWidgets import QGraphicsOpacityEffect

    for widget in widgets:
        if widget is None:
            continue
        try:
            widget.setEnabled(not locked)
            if not locked:
                widget.setGraphicsEffect(None)
                continue
            effect = QGraphicsOpacityEffect(widget)
            effect.setOpacity(LOCKED_OPACITY)
            widget.setGraphicsEffect(effect)
        except RuntimeError:
            continue


class ProCard(QFrame):
    """What Pro adds here, and the one button that goes to the plans."""

    upgrade_requested = pyqtSignal()

    def __init__(self, title: str, note: str, action: str, parent=None):
        super().__init__(parent)
        self.setObjectName("proCard")
        self.setStyleSheet(PRO_CARD_QSS)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(12)

        gem = QLabel(self)
        gem.setPixmap(pixmap_for(self, "gem", 15))
        gem.setFixedWidth(18)
        row.addWidget(gem, 0, Qt.AlignmentFlag.AlignTop)

        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(3)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        name = QLabel(title, self)
        name.setStyleSheet(PRO_CARD_TITLE_QSS)
        name.setWordWrap(True)
        head.addWidget(name, 0)
        badge = QLabel(self.tr("PRO"), self)
        badge.setObjectName("proBadge")
        badge.setStyleSheet(PRO_BADGE_QSS)
        head.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        words.addLayout(head)
        body = QLabel(note, self)
        body.setStyleSheet(ROW_NOTE_QSS)
        body.setWordWrap(True)
        words.addWidget(body)
        row.addLayout(words, 1)

        button = QPushButton(action, self)
        button.setStyleSheet(PRIMARY_BTN_QSS)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAutoDefault(False)
        button.clicked.connect(self.upgrade_requested.emit)
        row.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.button = button


class Page(QWidget):
    """Title, one muted subtitle, then a scrolling column of groups."""

    def __init__(self, title: str, subtitle: str, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QVBoxLayout()
        head.setContentsMargins(24, 22, 24, 12)
        head.setSpacing(3)
        self.title_label = QLabel(title, self)
        self.title_label.setStyleSheet(PAGE_TITLE_QSS)
        head.addWidget(self.title_label)
        self.subtitle_label = QLabel(subtitle, self)
        self.subtitle_label.setStyleSheet(PAGE_SUBTITLE_QSS)
        self.subtitle_label.setWordWrap(True)
        head.addWidget(self.subtitle_label)
        outer.addLayout(head)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(SCROLL_QSS)
        self._body = QWidget(self._scroll)
        self.body = QVBoxLayout(self._body)
        self.body.setContentsMargins(24, 4, 24, 20)
        self.body.setSpacing(14)
        self.body.addStretch(1)
        self._scroll.setWidget(self._body)
        outer.addWidget(self._scroll, 1)

    def add(self, widget: QWidget) -> QWidget:
        """Insert above the trailing stretch."""
        self.body.insertWidget(self.body.count() - 1, widget)
        return widget

    def add_group_title(self, text: str) -> QLabel:
        label = QLabel(text, self._body)
        label.setStyleSheet(GROUP_TITLE_QSS)
        label.setContentsMargins(2, 4, 0, 0)
        return self.add(label)











BILLING_CARD_QSS = (
    f"QFrame#billingCard {{ background: {SURFACE}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_CARD}px; }}"
    "QFrame#billingCard QLabel { background: transparent; border: none; }"
)
BILLING_NAME_QSS = (f"font-size: 11px; font-weight: 700; letter-spacing: 0.6px; text-transform: uppercase;"
                    f" color: {INK_3}; background: transparent;")
BILLING_STATUS_QSS = f"font-size: 12px; color: {INK_2}; background: transparent;"
BILLING_PRICE_QSS = f"font-size: 26px; font-weight: 600; color: {INK}; background: transparent;"



BILLING_STAT_QSS = f"font-size: 26px; font-weight: 600; color: {INK}; background: transparent;"
BILLING_SUBTITLE_QSS = f"font-size: 12px; color: {INK_2}; background: transparent;"
BILLING_POINT_QSS = f"font-size: 12px; color: {INK}; background: transparent;"
BILLING_FOOT_QSS = f"font-size: 11px; color: {INK_3}; background: transparent;"
BILLING_PRIMARY_QSS = (
    f"QPushButton {{ background: {ACCENT}; color: {ON_ACCENT}; border: none;"
    " border-radius: 8px; padding: 8px 16px; font-size: 13px; font-weight: 600; }"
    f"QPushButton:hover {{ background: {ACCENT_DARK}; }}"
)



BILLING_POINT_H = 28
BILLING_CHECK_PX = 14





BILLING_STACK_W = 720
_BILLING_CARD_MIN_W = 240


class BillingCard(QFrame):
    """One plan as a card: a title row, then whatever the caller stacks in it."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("billingCard")
        self.setStyleSheet(BILLING_CARD_QSS)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(_BILLING_CARD_MIN_W)
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(16, 14, 16, 14)
        self.column.setSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self.title_label = QLabel(title, self)
        self.title_label.setStyleSheet(BILLING_NAME_QSS)
        self.title_label.setWordWrap(True)
        head.addWidget(self.title_label, 0)
        head.addStretch(1)
        self.head = head
        self.column.addLayout(head)




        self.column.addStretch(1)

    def add(self, widget: QWidget) -> QWidget:
        self.column.insertWidget(self.column.count() - 1, widget)
        return widget

    def add_layout(self, layout) -> None:
        self.column.insertLayout(self.column.count() - 1, layout)

    def add_stat(self, value: str, caption: str = "") -> QLabel:
        """The card's one figure, and the words under it that give it its unit."""
        label = QLabel(value, self)
        label.setStyleSheet(BILLING_STAT_QSS)
        label.setWordWrap(True)
        self.add(label)
        if caption:
            self.add_status(caption)
        return label

    def add_note(self, text: str) -> QLabel:
        """The faintest line the card carries, under the button."""
        label = QLabel(text, self)
        label.setStyleSheet(BILLING_FOOT_QSS)
        label.setWordWrap(True)
        return self.add(label)

    def add_status(self, text: str) -> QLabel:
        """The sentence under the plan name: what is in force, how much is left."""
        label = QLabel(text, self)
        label.setStyleSheet(BILLING_STATUS_QSS)
        label.setWordWrap(True)
        return self.add(label)

    def add_price(self, price: str) -> QLabel:
        label = QLabel(price, self)
        label.setStyleSheet(BILLING_PRICE_QSS)
        label.setWordWrap(True)
        return self.add(label)

    def add_subtitle(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setStyleSheet(BILLING_SUBTITLE_QSS)
        label.setWordWrap(True)
        return self.add(label)

    def add_point(self, text: str) -> QWidget:
        """One feature line: a green check, the words, and no hairline under it."""
        row = QWidget(self)
        row.setMinimumHeight(BILLING_POINT_H)
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        tick = QLabel(row)
        tick.setPixmap(pixmap_for(row, "check", BILLING_CHECK_PX, QColor(GREEN)))
        tick.setFixedWidth(BILLING_CHECK_PX + 2)
        line.addWidget(tick, 0, Qt.AlignmentFlag.AlignVCenter)
        label = QLabel(text, row)
        label.setStyleSheet(BILLING_POINT_QSS)
        label.setWordWrap(True)
        line.addWidget(label, 1)
        return self.add(row)

    def add_action(self, button: QPushButton) -> QPushButton:
        """The card's one button, held to the bottom right of the card."""
        button.setStyleSheet(BILLING_PRIMARY_QSS)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAutoDefault(False)
        row = QHBoxLayout()
        row.setContentsMargins(0, 8, 0, 0)
        row.addWidget(button, 1)
        self.add_layout(row)
        return button


class BillingCardRow(QWidget):
    """Two cards side by side on a wide dialog, stacked under 720 px."""







    def __init__(self, parent=None):
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(12)
        self._cards: list = []
        self._stacked = False

    def add_card(self, card: QWidget) -> QWidget:
        self._cards.append(card)
        card.setParent(self)
        self._place()
        return card

    def resizeEvent(self, event):  # noqa: N802 (Qt)
        super().resizeEvent(event)
        window = self.window()
        width = window.width() if window is not None else self.width()
        stacked = int(width) < BILLING_STACK_W
        if stacked != self._stacked:
            self._stacked = stacked
            self._place()

    def _place(self) -> None:
        for index, card in enumerate(self._cards):
            self._grid.removeWidget(card)
            if self._stacked:
                self._grid.addWidget(card, index, 0)
            else:
                self._grid.addWidget(card, 0, index)
        columns = 1 if self._stacked else max(len(self._cards), 1)
        for column in range(max(len(self._cards), 1)):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)
