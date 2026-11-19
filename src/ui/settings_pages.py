# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

from qgis.PyQt.QtCore import QRectF, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
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
    QVBoxLayout,
    QWidget,
)

from ..core.profile import MEMORY_NOTE_MAX_CHARS
from ..core.settings import state_dir
from . import tokens
from .font_scale import scale_px_length
from .icons import ink_of, pixmap_for
from .style import (
    ACCENT,
    FIELD,
    HOVER_ON,
    INK_2,
    INK_3,
    LINE,
    LINE_STRONG,
    RED,
    RED_TINT,
)
from .styles import DARK_UI, INK_HOVER_FILL, INK_PRESSED_FILL, PANEL_BASE, PANEL_TEXT
from .widgets import IconButton, plain_tip




MUTED = INK_2
HAIRLINE = LINE
TINT = FIELD
TINT_HOVER = HOVER_ON
TINT_ON = LINE_STRONG
ACCENT_TINT = "rgba(67, 160, 71, 0.10)"
ACCENT_TINT_ON = "rgba(67, 160, 71, 0.20)"
ACCENT_BORDER = "rgba(67, 160, 71, 0.45)"


_T = tokens.DARK if DARK_UI else tokens.LIGHT
EDGE = _T["border_default"]
EDGE_HOVER = _T["border_strong"]
RADIUS_CARD_PX, RADIUS_CONTROL_PX, RADIUS_THUMB_PX = 14, 8, 6
CONTROL_PX = 30


FOCUS_RING = "#81c784" if DARK_UI else _T["accent_hover"]
FOCUS_RING_ON_FILL = "#ffffff" if DARK_UI else "#0f3d13"

PAGE_TITLE_QSS = "font-size: 20px; font-weight: 600; color: " + PANEL_TEXT + "; background: transparent;"
PAGE_SUBTITLE_QSS = f"font-size: 12px; color: {MUTED}; background: transparent;"
ROW_TITLE_QSS = "font-size: 13px; color: " + PANEL_TEXT + "; background: transparent;"
ROW_NOTE_QSS = f"font-size: 11px; color: {MUTED}; background: transparent;"
SAVED_HINT_QSS = f"font-size: 11px; color: {MUTED}; background: transparent;"
GROUP_TITLE_QSS = "font-size: 13px; font-weight: 600; color: " + PANEL_TEXT + "; background: transparent;"




GROUP_QSS = GROUP_FLAT_QSS = (
    "QFrame#settingsGroup { background: transparent; border: none; }"
    "QFrame#settingsGroup > QFrame#settingsRow { border: none; background: transparent; }"
)





def nav_qss(name: str) -> str:
    return (
        f"QListWidget#{name} {{ background: transparent; border: none; outline: none;"
        " padding: 4px 6px; font-size: 13px; }"

        f"QListWidget#{name}::item {{ padding: 6px 7px; border: 1px solid transparent; border-radius: 8px;"
        f" color: {PANEL_TEXT}; margin: 1px 0; }}"
        f"QListWidget#{name}::item:hover {{ background: {_T['surface_hover']}; }}"
        f"QListWidget#{name}::item:selected {{ background: {_T['surface_pressed']}; color: {PANEL_TEXT}; }}"

        f"QListWidget#{name}::item:selected:focus {{ border-color: {FOCUS_RING}; }}"
    )



ROW_DIVIDER_QSS = (f"QFrame {{ background: {HAIRLINE}; border: none; margin: 0 14px;"
                   " max-height: 1px; min-height: 1px; }")

PRIMARY_BTN_QSS = (
    "QPushButton { background: " + PANEL_TEXT + "; color: " + PANEL_BASE + "; border: none;"
    " border-radius: 8px; padding: 7px 14px; font-size: 12px; font-weight: 600; }"
    f"QPushButton:hover {{ background: {INK_HOVER_FILL}; }}"
    f"QPushButton:pressed {{ background: {INK_PRESSED_FILL}; }}"
    f"QPushButton:focus {{ border: 2px solid {FOCUS_RING}; padding: 5px 12px; }}"
)
GHOST_BTN_QSS = (
    f"QPushButton {{ background: transparent; color: {PANEL_TEXT}; border: 1px solid {HAIRLINE};"
    " border-radius: 8px; padding: 6px 14px; font-size: 12px; }"
    f"QPushButton:hover {{ background: {ACCENT_TINT}; border-color: {ACCENT_BORDER}; }}"
    f"QPushButton:pressed {{ background: {ACCENT_TINT_ON}; }}"
    f"QPushButton:focus {{ border: 2px solid {FOCUS_RING}; padding: 5px 13px; }}"
)
DANGER_GHOST_BTN_QSS = (
    f"QPushButton {{ background: transparent; color: {RED}; border: 1px solid {HAIRLINE};"
    " border-radius: 8px; padding: 6px 14px; font-size: 12px; }"
    f"QPushButton:hover {{ background: {RED_TINT}; }}"
    f"QPushButton:pressed {{ background: {RED_TINT}; border-color: {RED}; }}"

    f"QPushButton:disabled {{ color: {INK_3}; border-color: {HAIRLINE}; background: transparent; }}"
)
COMBO_QSS = (
    f"QComboBox {{ color: {PANEL_TEXT}; background: {PANEL_BASE}; border: 1px solid {HAIRLINE};"
    " border-radius: 8px; padding: 5px 28px 5px 10px; font-size: 12px; min-height: 18px; }"
    f"QComboBox:hover {{ border-color: {EDGE_HOVER}; }}"
    f"QComboBox:focus {{ border: 2px solid {FOCUS_RING}; padding: 4px 27px 4px 9px; }}"
    "QComboBox::drop-down { border: none; width: 26px; subcontrol-origin: padding;"
    " subcontrol-position: center right; }"
    "QComboBox QAbstractItemView { color: " + PANEL_TEXT + "; background: " + PANEL_BASE + ";"
    f" border: 1px solid {HAIRLINE}; selection-background-color: {TINT_ON};"
    " selection-color: " + PANEL_TEXT + "; outline: none; }"
)


def combo_qss(widget) -> str:


    return COMBO_QSS + _chevron_rule(widget)


def _chevron_rule(widget) -> str:
    import os

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
    f"QLineEdit {{ color: {PANEL_TEXT}; background: {PANEL_BASE}; border: 1px solid {HAIRLINE};"
    " border-radius: 8px; padding: 6px 10px; font-size: 12px; }"
    f"QLineEdit:hover {{ border-color: {EDGE_HOVER}; }}"
    f"QLineEdit:focus {{ border: 2px solid {FOCUS_RING}; padding: 5px 9px; }}"
)
TEXTAREA_QSS = (
    f"QPlainTextEdit {{ color: {PANEL_TEXT}; background: {PANEL_BASE}; border: 1px solid {HAIRLINE};"
    " border-radius: 8px; padding: 6px 8px; font-size: 12px; }"
    f"QPlainTextEdit:hover {{ border-color: {EDGE_HOVER}; }}"
    f"QPlainTextEdit:focus {{ border: 2px solid {FOCUS_RING}; padding: 5px 7px; }}"
)


SEGMENT_QSS = (
    f"QPushButton {{ background: transparent; color: {_T['text_secondary']}; border: 1px solid transparent;"
    f" border-radius: {RADIUS_THUMB_PX}px; padding: 0 12px; font-size: 12px; }}"
    f"QPushButton:hover {{ color: {_T['text_primary']}; }}"
    f"QPushButton:checked {{ background: {_T['surface_raised']}; color: {_T['text_primary']};"
    f" border: 1px solid {EDGE}; font-weight: 600; }}"
    f"QPushButton:focus {{ border: 1px solid {FOCUS_RING}; }}"
)
_SEGMENT_PAD_PX = 14
SEGMENT_FRAME_QSS = (f"QFrame#segmented {{ background: {_T['surface_field']}; border: none;"
                     f" border-radius: {RADIUS_CONTROL_PX}px; }}")











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
        track = QColor(ACCENT) if on else QColor(ink)
        if not on:
            track.setAlphaF(0.28)
        if not self.isEnabled():
            track.setAlphaF(0.18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        knob_d = rect.height() - 6
        x = rect.right() - 3 - knob_d if on else rect.left() + 3
        if on:

            knob = QColor("#ffffff")
        else:
            knob = QColor(ink)
            knob.setAlphaF(0.9)
        painter.setBrush(knob)
        painter.drawEllipse(QRectF(x, rect.top() + 3, knob_d, knob_d))
        if self.hasFocus():

            ring = QColor(ink)
            ring.setAlphaF(0.55)
            painter.setPen(QPen(ring, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            inner = rect.adjusted(1, 1, -1, -1)
            painter.drawRoundedRect(inner, inner.height() / 2, inner.height() / 2)
        painter.end()


class SkeletonRow(QWidget):




    def __init__(self, parent=None, spoken: str = ""):
        super().__init__(parent)
        self.setAccessibleName(spoken)
        self.setFixedHeight(64)

    def paintEvent(self, _event):  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(FIELD))
            mid = self.height() / 2
            painter.drawEllipse(QRectF(14, mid - 16, 32, 32))
            width = max(80, self.width() - 60)
            painter.drawRoundedRect(QRectF(58, mid - 11, width * 0.32, 9), 4.5, 4.5)
            painter.drawRoundedRect(QRectF(58, mid + 4, width * 0.2, 8), 4, 4)
        finally:
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


        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
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
        self._col.setContentsMargins(12, 8, 12, 8)
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


        text = QFont(self.font())
        text.setPixelSize(scale_px_length(12))
        text.setBold(True)
        metrics = QFontMetrics(text)
        thumb = max(scale_px_length(CONTROL_PX - 6), metrics.height() + 8)
        self.setFixedHeight(thumb + 6)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for value, label in choices:
            btn = QPushButton(label, self)
            btn.setCheckable(True)
            btn.setAutoDefault(False)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(SEGMENT_QSS)





            btn.setFixedHeight(thumb)
            btn.setMinimumWidth(metrics.horizontalAdvance(label) + 2 * scale_px_length(_SEGMENT_PAD_PX))
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

        body.setTextFormat(Qt.TextFormat.PlainText)
        body.setStyleSheet("font-size: 12px; color: " + PANEL_TEXT + "; background: transparent;")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        body.setToolTip(plain_tip(text))
        words.addWidget(body)
        self._body = body
        self._field = None
        self._words = words
        if caption:
            meta = QLabel(caption, self)
            meta.setStyleSheet(ROW_NOTE_QSS)
            words.addWidget(meta)
        row.addLayout(words, 1)
        pencil = IconButton(self, "pencil", 12, self.tr("Edit this note"))
        pencil.setFixedSize(24, 24)
        pencil.clicked.connect(self.start_editing)
        row.addWidget(pencil, 0, Qt.AlignmentFlag.AlignTop)
        self.edit_button = pencil
        close = IconButton(self, "close", 12, self.tr("Delete this note"))
        close.setFixedSize(24, 24)
        close.clicked.connect(lambda: self.removed.emit(self.note_id))
        row.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)

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


class Page(QWidget):






    def __init__(self, title: str, subtitle: str, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QVBoxLayout()
        head.setContentsMargins(24, 24, 24, 10)
        head.setSpacing(3)
        self.title_label = QLabel(title, self)
        self.title_label.setStyleSheet(PAGE_TITLE_QSS)
        head.addWidget(self.title_label)
        self.subtitle_label = QLabel(subtitle, self)
        self.subtitle_label.setStyleSheet(PAGE_SUBTITLE_QSS)
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.hide()
        self.setAccessibleDescription(subtitle)
        outer.addLayout(head)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(SCROLL_QSS)
        self._body = QWidget(self._scroll)
        self.body = QVBoxLayout(self._body)

        self.body.setContentsMargins(10, 4, 10, 24)
        self.body.setSpacing(8)
        self.body.addStretch(1)
        self._scroll.setWidget(self._body)
        outer.addWidget(self._scroll, 1)

    def add(self, widget: QWidget) -> QWidget:

        self.body.insertWidget(self.body.count() - 1, widget)
        return widget

    def add_group_title(self, text: str, aside: QWidget | None = None) -> QLabel:

        label = QLabel(text, self._body)
        label.setObjectName("settingsGroupTitle")
        label.setStyleSheet(GROUP_TITLE_QSS)

        label.setContentsMargins(14, 18, 0, 2)
        if aside is None:
            return self.add(label)
        row = QWidget(self._body)
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(label, 1, Qt.AlignmentFlag.AlignBottom)
        aside.setParent(row)
        line.addWidget(aside, 0, Qt.AlignmentFlag.AlignBottom)
        self.add(row)
        return label



USAGE_KEYS = ("runs_used", "runs_limit", "period_end", "reset_date", "is_subscriber", "is_free_tier")
