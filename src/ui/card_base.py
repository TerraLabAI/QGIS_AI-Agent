# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""What every thread card shares: the frame, the text helpers, the formats."""









from __future__ import annotations

import json

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .icons import ink_of, pixmap_for
from .style import (
    FONT_HINT,
    INK_2,
    INK_3,
    MONO_FAMILY,
    MOTION_FADE_UP_MS,
    MUTED,
    SPACE_CARD,
    qcolor,
)
from .styles import (
    _CARD_CHILD_BTN_RESET_QSS,
    _CARD_MARGINS,
    _CARD_QSS,
    ERROR_TEXT,
    WARNING_TEXT,
    _msg_card_qss,
)



_HINT_QSS = f"font-size: {FONT_HINT}px; color: {INK_2}; background: transparent; border: none;"

_MUTED_GREY = qcolor(INK_3)

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
    """The painted glyph name of a taxonomy kind (``dash`` when unknown)."""
    return _MSG_KIND_ICONS.get(kind, _MSG_KIND_ICONS["neutral"])


def msg_kind_colour(kind: str) -> str:
    """The glyph and title colour of a taxonomy kind, as a hex colour (the palette ink for the kinds that carry none of their own)."""

    colour = _MSG_KIND_COLOURS.get(kind, _MSG_KIND_COLOURS["neutral"])
    return colour or ink_of(None).name()


_ACRONYMS = {"ai", "gee", "osm", "wms", "wfs", "wmts", "xyz", "crs", "stac",
             "cog", "gpkg", "url", "id", "ui", "qgis", "csv", "kml", "pmtiles"}


def humanise_tool_name(name: str) -> str:
    """``run_processing`` reads as ``Run processing``, acronyms kept."""
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


def format_duration(seconds) -> str:
    """``1.2 s`` under ten seconds, ``12 s`` under a minute, ``1 min 12 s`` past it."""


    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return ""
    if value < 0:
        return ""
    if value >= 60:
        minutes = int(value // 60)
        rest = int(round(value - minutes * 60))
        return f"{minutes} min {rest} s"
    if value < 1:
        return f"{max(1, int(round(value * 1000)))} ms"
    if value < 10:
        return f"{value:.1f} s"
    return f"{int(round(value))} s"


def _args_text(args) -> str:
    try:
        return json.dumps(args or {}, indent=2, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(args)


def _args_summary(args) -> str:
    """The arguments on one line, ``key=value`` pairs, for a collapsed card."""
    if not isinstance(args, dict) or not args:
        return ""
    parts = []
    for key, value in args.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        text = " ".join(str(text).split())
        if len(text) > 60:
            text = text[:57] + "..."
        parts.append(f"{key}={text}")
    return "  ".join(parts)


def plain_label(text: str, parent: QWidget = None) -> QLabel:
    """A QLabel that renders ``text`` as plain text, never as HTML."""






    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label



_UNBOUNDED_PX = 16777215


def glyph_row(parent: QWidget, glyph: str, colour: QColor, text: str,
              text_qss: str = "", object_name: str = "", glyph_px: int = _GLYPH_PX) -> QWidget:
    """One row: a small painted glyph in ``colour`` at the left, the text beside it, word-wrapped and selectable."""



    row = QWidget(parent)
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(SPACE_CARD)
    icon = QLabel(row)
    icon.setFixedSize(_GLYPH_SLOT_PX, _GLYPH_SLOT_PX + 2)
    icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
    icon.setPixmap(pixmap_for(row, glyph, glyph_px, colour))
    lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
    label = QLabel(text, row)
    if object_name:
        label.setObjectName(object_name)
    if text_qss:
        label.setStyleSheet(text_qss)


    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)




    lay.addWidget(label, 1, Qt.AlignmentFlag.AlignTop)
    row.icon_label = icon
    row.text_label = label
    return row


class _Card(QWidget):
    """A design-system card with a vertical layout and a taxonomy kind."""








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
        """Record the card's taxonomy kind."""

        self.kind = kind or None
        if kind:
            self.setStyleSheet(_msg_card_qss(self.objectName(), kind) + _CARD_CHILD_BTN_RESET_QSS)
        else:
            self.setStyleSheet(_CARD_QSS.format(name=self.objectName())
                               + "QLabel { background: transparent; border: none; }"
                               + _CARD_CHILD_BTN_RESET_QSS)

    def set_frame(self, frame_qss: str) -> None:
        """A frame of the card's own, untinted: ``frame_qss`` carries a literal ``{name}`` slot for the card's object name."""

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


def mono_font(px: int, weight: int = 400):
    """The panel's mono face at ``px`` pixels, as a QFont for a painted chip."""




    from qgis.PyQt.QtGui import QFont

    families = [f.strip().strip("'\"") for f in MONO_FAMILY.split(",")]
    font = QFont()
    try:
        font.setFamilies(families)
    except AttributeError:
        font.setFamily(families[0])
    font.setStyleHint(QFont.StyleHint.TypeWriter)
    font.setPixelSize(int(px))
    try:
        font.setWeight(QFont.Weight.Medium if weight >= 500 else QFont.Weight.Normal)
    except AttributeError:
        font.setWeight(63 if weight >= 500 else 50)
    return font


def reduced_motion(widget) -> bool:
    """Whether ``widget`` should skip its animation and land on the end state."""









    try:
        if not widget.isVisible():
            return True
        window = widget.window()
        return window is None or not window.isVisible()
    except (RuntimeError, AttributeError):
        return True


def fade_up(widget, layout=None) -> None:
    """The site's ``fade-up`` on a new row: opacity 0 to 1 and 8 px up, over 300 ms, OutQuint."""







    if reduced_motion(widget):
        return
    try:
        from qgis.PyQt.QtCore import QEasingCurve, QVariantAnimation
        from qgis.PyQt.QtWidgets import QGraphicsOpacityEffect
    except Exception:  # noqa: BLE001 - no Qt here
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
    """Stop a fade-up still running on a row about to be hidden or deleted."""
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
