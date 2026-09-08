# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later










from __future__ import annotations

import functools
import json

from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .font_scale import scale_point_size
from .icons import ink_of, pixmap_for
from .style import (
    FONT_HINT,
    INK_2,
    MONO_FAMILY,
    MOTION_FADE_UP_MS,
    MUTED,
    SPACE_CARD,
)
from .styles import (
    _CARD_CHILD_BTN_RESET_QSS,
    _CARD_MARGINS,
    _CARD_QSS,
    ERROR_TEXT,
    WARNING_TEXT,
    _msg_card_qss,
)
from .widgets import ChatLabel



_HINT_QSS = f"font-size: {FONT_HINT}px; color: {INK_2}; background: transparent; border: none;"

_GLYPH_SLOT_PX = 14
_GLYPH_PX = 12




_MSG_KIND_ICONS = {
    "neutral": "dash",
    "info": "circle",
    "armed": "pencil",
    "success": "check",
    "warning": "warning",
    "error": "close",
    "error_transient": "close",
    "premium": "spark",
}


_MSG_KIND_COLOURS = {
    "neutral": MUTED,
    "info": None,
    "armed": None,
    "success": None,
    "warning": WARNING_TEXT,
    "error": ERROR_TEXT,
    "error_transient": ERROR_TEXT,
    "premium": None,
}


def msg_kind_icon(kind: str) -> str:

    return _MSG_KIND_ICONS.get(kind, _MSG_KIND_ICONS["neutral"])


def msg_kind_colour(kind: str) -> str:


    colour = _MSG_KIND_COLOURS.get(kind, _MSG_KIND_COLOURS["neutral"])
    return colour or ink_of(None).name()


_ACRONYMS = {"ai", "gee", "osm", "wms", "wfs", "wmts", "xyz", "crs", "stac",
             "cog", "gpkg", "url", "id", "ui", "qgis", "csv", "kml", "pmtiles"}


def humanise_tool_name(name: str) -> str:

    words = [w for w in (name or "").replace("-", "_").split("_") if w]
    if not words:
        return name or ""
    out = []
    for i, word in enumerate(words):
        if word.lower() in _ACRONYMS:
            out.append(word.upper())
        elif i == 0:
            out.append(word.capitalize())
        else:
            out.append(word.lower())
    return " ".join(out)




TR_CONTEXT = "AIAgent"


def tr(text: str) -> str:

    return QCoreApplication.translate(TR_CONTEXT, text)


def format_duration(seconds) -> str:








    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return ""
    if value < 0:
        return ""
    if value >= 59.5:
        minutes, rest = divmod(int(round(value)), 60)
        if rest:
            return tr("{minutes} min {seconds} s").format(minutes=minutes, seconds=rest)
        return tr("{minutes} min").format(minutes=minutes)
    if value < 1:
        return tr("{count} ms").format(count=max(1, int(round(value * 1000))))
    if value < 10:
        return tr("{count} s").format(count=f"{value:.1f}")
    return tr("{count} s").format(count=int(round(value)))


def _args_text(args) -> str:
    try:
        return json.dumps(args or {}, indent=2, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(args)


def plain_label(text: str, parent: QWidget = None) -> QLabel:







    return ChatLabel(text, parent)



_UNBOUNDED_PX = 16777215


def glyph_row(parent: QWidget, glyph: str, colour: QColor, text: str,
              text_qss: str = "", object_name: str = "", glyph_px: int = _GLYPH_PX) -> QWidget:




    row = QWidget(parent)
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(SPACE_CARD)
    icon = QLabel(row)
    icon.setFixedSize(_GLYPH_SLOT_PX, _GLYPH_SLOT_PX + 2)
    icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
    icon.setPixmap(pixmap_for(row, glyph, glyph_px, colour))
    lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)


    label = ChatLabel(text, row, wrap=True, selectable=True)
    if object_name:
        label.setObjectName(object_name)
    if text_qss:
        label.setStyleSheet(text_qss)




    lay.addWidget(label, 1, Qt.AlignmentFlag.AlignTop)
    row.icon_label = icon
    row.text_label = label
    return row


class _Card(QWidget):









    _serial = 0

    def __init__(self, kind: str | None = None, parent=None, frame_qss: str | None = None):
        super().__init__(parent)
        _Card._serial += 1
        self.setObjectName(f"card{_Card._serial}")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.kind: str | None = None
        if frame_qss:
            self.set_frame(frame_qss)
        else:
            self.set_kind(kind)
        self._col = QVBoxLayout(self)
        self._col.setContentsMargins(*_CARD_MARGINS)
        self._col.setSpacing(SPACE_CARD)

    def set_kind(self, kind: str | None) -> None:


        self.kind = kind or None
        if kind:
            self.setStyleSheet(_msg_card_qss(self.objectName(), kind) + _CARD_CHILD_BTN_RESET_QSS)
        else:
            self.setStyleSheet(_CARD_QSS.format(name=self.objectName())
                               + "QLabel { background: transparent; border: none; }"
                               + _CARD_CHILD_BTN_RESET_QSS)

    def set_frame(self, frame_qss: str) -> None:


        self.kind = None
        self.setStyleSheet(frame_qss.replace("{name}", self.objectName())
                           + "QLabel { background: transparent; border: none; }"
                           + _CARD_CHILD_BTN_RESET_QSS)

    def set_margins(self, left: int, top: int, right: int, bottom: int) -> None:
        self._col.setContentsMargins(left, top, right, bottom)

    def _button(self, text: str, qss: str, slot) -> QPushButton:
        button = QPushButton(text, self)
        button.setStyleSheet(qss)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAutoDefault(False)
        button.clicked.connect(slot)
        return button









_FADE_UP_WINDOW_S = 1.5
_FADE_UP_RISE_PX = 8


@functools.lru_cache(maxsize=32)
def mono_font(px: int, weight: int = 400):











    from qgis.PyQt.QtGui import QFont

    families = [f.strip().strip("'\"") for f in MONO_FAMILY.split(",")]
    font = QFont()
    try:
        font.setFamilies(families)
    except AttributeError:
        font.setFamily(families[0])
    font.setStyleHint(QFont.StyleHint.TypeWriter)
    font.setPixelSize(scale_point_size(int(px)))
    try:
        font.setWeight(QFont.Weight.Medium if weight >= 500 else QFont.Weight.Normal)
    except AttributeError:
        font.setWeight(63 if weight >= 500 else 50)
    return font


def reduced_motion(widget) -> bool:










    try:
        if not widget.isVisible():
            return True
        window = widget.window()
        return window is None or not window.isVisible()
    except (RuntimeError, AttributeError):
        return True


def fade_up(widget, layout=None) -> None:








    if reduced_motion(widget):
        return
    try:
        from qgis.PyQt.QtCore import QEasingCurve, QVariantAnimation
        from qgis.PyQt.QtWidgets import QGraphicsOpacityEffect
    except Exception:  # noqa: BLE001
        return
    layout = layout or widget.layout()
    margins = layout.contentsMargins() if layout is not None else None
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    anim = QVariantAnimation()
    anim.setDuration(MOTION_FADE_UP_MS)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutQuint)

    def step(value) -> None:
        try:
            effect.setOpacity(float(value))
            if margins is not None:
                rise = int(round(_FADE_UP_RISE_PX * (1.0 - float(value))))
                layout.setContentsMargins(margins.left(), margins.top() + rise,
                                          margins.right(), margins.bottom())
        except RuntimeError:
            anim.stop()

    def done() -> None:
        try:
            if margins is not None:
                layout.setContentsMargins(margins)
            widget.setGraphicsEffect(None)
            widget._fade_up_anim = None
        except RuntimeError:
            pass

    anim.valueChanged.connect(step)
    anim.finished.connect(done)
    widget._fade_up_anim = anim
    anim.start()


def stop_fade_up(widget) -> None:

    anim = getattr(widget, "_fade_up_anim", None)
    if anim is None:
        return
    try:
        anim.stop()
    except (RuntimeError, AttributeError):
        pass
    try:
        widget._fade_up_anim = None
    except (RuntimeError, AttributeError):
        pass


__all__ = [
    "_Card",
    "_FADE_UP_WINDOW_S",
    "_HINT_QSS",
    "_UNBOUNDED_PX",
    "_args_text",
    "fade_up",
    "format_duration",
    "glyph_row",
    "humanise_tool_name",
    "mono_font",
    "msg_kind_colour",
    "msg_kind_icon",
    "plain_label",
    "reduced_motion",
    "stop_fade_up",
]
