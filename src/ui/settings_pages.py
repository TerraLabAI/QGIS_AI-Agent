# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

from qgis.PyQt.QtCore import QRectF, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPalette
from qgis.PyQt.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.profile import MEMORY_NOTE_MAX_CHARS
from .icons import ink_of, pixmap_for
from .style import (
    ACCENT,
    ACCENT_DARK,
    FIELD,
    HOVER_ON,
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

ACCENT_TINT = "rgba(67, 160, 71, 0.10)"
ACCENT_TINT_ON = "rgba(67, 160, 71, 0.20)"
ACCENT_BORDER = "rgba(67, 160, 71, 0.45)"

PAGE_TITLE_QSS = "font-size: 16px; font-weight: 600; color: palette(text); background: transparent;"
PAGE_SUBTITLE_QSS = f"font-size: 12px; color: {MUTED}; background: transparent;"
ROW_TITLE_QSS = "font-size: 13px; color: palette(text); background: transparent;"
ROW_NOTE_QSS = f"font-size: 11px; color: {MUTED}; background: transparent;"
SAVED_HINT_QSS = f"font-size: 11px; color: {MUTED}; background: transparent;"
GROUP_TITLE_QSS = "font-size: 12px; font-weight: 600; color: palette(text); background: transparent;"





GROUP_QSS = (
    f"QFrame#settingsGroup {{ background: {SURFACE};"
    f" border: 1px solid {LINE}; border-radius: {RADIUS_CARD}px; }}"
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

    f"QPushButton:disabled {{ color: {INK_3}; border-color: {HAIRLINE}; background: transparent; }}"
)


GET_PRO_BTN_QSS = (
    f"QPushButton {{ background: {ACCENT}; color: {ON_ACCENT}; border: none;"
    " border-radius: 8px; padding: 7px 14px; font-size: 12px; font-weight: 600; }"
    f"QPushButton:hover {{ background: {ACCENT_DARK}; }}"
)
DISCLOSURE_QSS = (
    f"QToolButton {{ background: transparent; border: none; color: {MUTED};"
    " font-size: 11px; padding: 2px 0; }"
    "QToolButton:hover { color: palette(text); }"
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
        except Exception:  # noqa: BLE001
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


    def __init__(self, parent=None, checked: bool = False):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(bool(checked))
        self.setCursor(Qt.CursorShape.PointingHandCursor)




        self.setFixedSize(38, 22)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def paintEvent(self, _event) -> None:  # noqa: N802
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





            btn.ensurePolished()
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


class NoteRow(QFrame):









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
        body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        body.setToolTip(text)
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

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)

        self._body.setMaximumHeight(2 * self._body.fontMetrics().lineSpacing())

    def start_editing(self) -> None:

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





LOCKED_OPACITY = 0.42


def set_section_locked(*widgets, locked: bool = True) -> None:















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


class Disclosure(QWidget):






    toggled = pyqtSignal(bool)

    def __init__(self, label: str, body: QWidget, parent=None, expanded: bool = False):
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        self.button = QToolButton(self)
        self.button.setText(label)
        self.button.setCheckable(True)
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.button.setStyleSheet(DISCLOSURE_QSS)
        self.button.setAutoRaise(True)
        self.button.toggled.connect(self.set_expanded)
        col.addWidget(self.button, 0, Qt.AlignmentFlag.AlignLeft)
        self.body = body
        body.setParent(self)
        col.addWidget(body)
        self.set_expanded(expanded)

    def set_expanded(self, on: bool) -> None:
        on = bool(on)
        if self.button.isChecked() != on:
            self.button.blockSignals(True)
            self.button.setChecked(on)
            self.button.blockSignals(False)
        self.button.setIcon(QIcon(pixmap_for(self, "chevron_down" if on else "chevron_right", 10)))
        self.body.setVisible(on)
        self.toggled.emit(on)

    def is_expanded(self) -> bool:
        return self.button.isChecked()


class ProCard(QFrame):








    upgrade_requested = pyqtSignal()

    def __init__(self, title: str, note: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("proCard")
        self.setStyleSheet(PRO_CARD_QSS)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(4)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        name = QLabel(title, self)
        name.setStyleSheet(PRO_CARD_TITLE_QSS)
        name.setWordWrap(True)
        row.addWidget(name, 1, Qt.AlignmentFlag.AlignVCenter)
        button = QPushButton(self.tr("Get Pro"), self)
        button.setStyleSheet(GET_PRO_BTN_QSS)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAutoDefault(False)
        button.clicked.connect(self.upgrade_requested.emit)
        row.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(row)
        self.button = button
        self.details = None
        if note:
            body = QLabel(note)
            body.setStyleSheet(ROW_NOTE_QSS)
            body.setWordWrap(True)
            self.details = Disclosure(self.tr("What Pro adds"), body, self)
            outer.addWidget(self.details)


class Page(QWidget):


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

        self.body.insertWidget(self.body.count() - 1, widget)
        return widget

    def add_group_title(self, text: str) -> QLabel:
        label = QLabel(text, self._body)
        label.setStyleSheet(GROUP_TITLE_QSS)
        label.setContentsMargins(2, 4, 0, 0)
        return self.add(label)





USAGE_KEYS = ("runs_used", "runs_limit", "period_end", "reset_date", "is_subscriber", "is_free_tier")
PROGRESS_QSS = (
    "QProgressBar { background: rgba(128,128,128,0.18); border: none; border-radius: 3px;"
    " max-height: 6px; min-height: 6px; }"
    "QProgressBar::chunk { background: palette(text); border-radius: 3px; }"
)

