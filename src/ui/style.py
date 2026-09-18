# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat panel's design tokens: the Beautiful UI line (docs/DESIGN.md)."""


























from __future__ import annotations

from .font_scale import scale_qss_font_px
from .styles import BRAND_GREEN_TEXT, BTN_GREEN, BTN_GREEN_HOVER, DARK_UI


def is_dark() -> bool:
    """Whether the QGIS palette is a dark one."""





    return DARK_UI














_LIGHT = {
    "page": "#f7f8fa", "canvas": "#eef0f3", "surface": "#ffffff", "inset": "#f4f6f8",
    "hover": "#eff1f4", "hover_2": "#e2e5ea", "field": "#eef0f3",
    "ink": "#1f2124", "ink_2": "#5b5f66", "ink_3": "#868b93",
    "ink_hover": "#33363b",
    "line": "#e2e5ea", "line_strong": "#cdd2d9", "line_soft": "#eef0f3",
    "stripe": "rgba(73, 73, 73, 0.075)", "stripe_bg": "#f5f5f5",
    "green": "#199a4d", "green_tint": "#e8f5ed",
    "orange": "#ef720d", "orange_tint": "#fdf1e5",
    "red": "#e3474c", "red_tint": "#fcecec",
    "tooltip_bg": "#25272b", "tooltip_fg": "#f6f7f8", "tooltip_muted": "#a5a8ad",
    "tooltip_border": "#3a3c40",

    "accent_ink": BRAND_GREEN_TEXT,
    "accent_tint": "rgba(67, 160, 71, 0.10)",
    "accent_tint_on": "rgba(67, 160, 71, 0.20)",
    "shadow_alpha": 0.10,
}
_DARK = {
    "page": "#17181a", "canvas": "#1c1d1f", "surface": "#232427", "inset": "#1f2022",
    "hover": "#2a2b2e", "hover_2": "#313236", "field": "#2b2c2f",
    "ink": "#f2f3f4", "ink_2": "#a5a8ad", "ink_3": "#6c6f75",
    "ink_hover": "#d7dade",
    "line": "#2e3033", "line_strong": "#3a3c40", "line_soft": "#27282b",
    "stripe": "rgba(255, 255, 255, 0.055)", "stripe_bg": "#1b1c1e",
    "green": "#3cbb72", "green_tint": "rgba(60, 187, 114, 0.14)",
    "orange": "#f68f3c", "orange_tint": "rgba(246, 143, 60, 0.14)",
    "red": "#ee5c61", "red_tint": "rgba(238, 92, 97, 0.14)",
    "tooltip_bg": "#111214", "tooltip_fg": "#f2f3f4", "tooltip_muted": "#a5a8ad",
    "tooltip_border": "#2e3033",
    "accent_ink": "#66bb6a",
    "accent_tint": "rgba(67, 160, 71, 0.16)",
    "accent_tint_on": "rgba(67, 160, 71, 0.26)",
    "shadow_alpha": 0.34,
}

DARK = is_dark()
T = _DARK if DARK else _LIGHT


PAGE = T["page"]
CANVAS = T["canvas"]
SURFACE = T["surface"]
INSET = T["inset"]
HOVER = T["hover"]
HOVER_ON = T["hover_2"]
FIELD = T["field"]
STRIPE = T["stripe"]
STRIPE_BG = T["stripe_bg"]

INK = T["ink"]
INK_2 = T["ink_2"]
INK_3 = T["ink_3"]


INK_HOVER = T["ink_hover"]

LINE = T["line"]
LINE_STRONG = T["line_strong"]
LINE_SOFT = T["line_soft"]

GREEN = T["green"]
GREEN_TINT = T["green_tint"]
ORANGE = T["orange"]
ORANGE_TINT = T["orange_tint"]



ON_ORANGE = "#2a1400"
ORANGE_DARK = "#e07f2f" if DARK else "#d5640a"
RED = T["red"]
RED_TINT = T["red_tint"]
TOOLTIP_BG = T["tooltip_bg"]
TOOLTIP_FG = T["tooltip_fg"]
TOOLTIP_MUTED = T["tooltip_muted"]
TOOLTIP_BORDER = T["tooltip_border"]






FONT_PROSE = 14
FONT_BASE = 13
FONT_BODY = 12
FONT_HINT = 11
FONT_MICRO = 10
LETTER_SPACING = -0.14



SPACE_OUTER = 8
SPACE_CARD = 6
SPACE_TIGHT = 4
SPACE_STAGE = 12




RADIUS_CHIP = 6
RADIUS_CONTROL = 8
RADIUS_ROW = RADIUS_CONTROL
RADIUS_CARD = 10
RADIUS_BOX = 12
RADIUS_PANEL = 14
RADIUS_WINDOW = RADIUS_PANEL
RADIUS_COMPOSER = 14



BTN_PX = 32
BTN_SMALL_PX = 28
ROW_PX = 28
CHIP_PX = 22
COMPOSER_PX = 52



MOTION_HOVER_MS = 120
MOTION_FOLD_MS = 300
MOTION_FADE_UP_MS = 300
MOTION_POP_MS = 160

MIN_PANEL_WIDTH = 320




MUTED = INK_2
MUTED_SOFT = INK_3
HAIRLINE = LINE
HAIRLINE_STRONG = LINE_STRONG
TINT = FIELD
TINT_HOVER = HOVER
TINT_ON = HOVER_ON






ACCENT = BTN_GREEN
ACCENT_DARK = BTN_GREEN_HOVER
ACCENT_TINT = "rgba(67, 160, 71, 0.10)"
ACCENT_TINT_ON = "rgba(67, 160, 71, 0.20)"
ACCENT_BORDER_SOFT = "rgba(67, 160, 71, 0.24)"
ACCENT_BORDER = "rgba(67, 160, 71, 0.45)"



ACCENT_INK_LIGHT = BRAND_GREEN_TEXT
ACCENT_INK_DARK = "#66bb6a"
ACCENT_INK = T["accent_ink"]


ON_ACCENT = "#000000"



MONO_FAMILY = "Consolas, 'DejaVu Sans Mono', Menlo, monospace"




USER_PILL_LINE = LINE_STRONG if not DARK else "rgba(255, 255, 255, 0.05)"











_BTN_ICON = (
    "QToolButton { background: transparent; border: none; padding: 3px;"
    f" border-radius: {RADIUS_CONTROL}px; }}"
    f"QToolButton:hover {{ background: {HOVER}; }}"
    f"QToolButton:pressed {{ background: {HOVER_ON}; }}"
    f'QToolButton[active="true"] {{ background: {ACCENT_TINT}; }}'
    "QToolButton:disabled { background: transparent; }"
    "QToolButton::menu-indicator { image: none; width: 0; }"
)








_BTN_SEND = (
    f"QToolButton {{ background: {ACCENT}; border: none; border-radius: 16px;"
    " padding: 0; }"
    f"QToolButton:hover {{ background: {ACCENT_DARK}; }}"
    f"QToolButton:pressed {{ background: {ACCENT_DARK}; }}"

    f"QToolButton:disabled {{ background: {ACCENT_BORDER}; }}"



    f'QToolButton[offline="true"] {{ background: {ACCENT_BORDER}; }}'
    f'QToolButton[offline="true"]:hover {{ background: {ACCENT}; }}'
    f'QToolButton[offline="true"]:pressed {{ background: {ACCENT_DARK}; }}'

    f'QToolButton[running="true"] {{ background: {INK}; }}'
    f'QToolButton[running="true"]:hover {{ background: {INK_HOVER}; }}'
)




def _vote_qss(tint: str) -> str:
    return (
        "QToolButton { background: transparent; border: none; padding: 3px;"
        f" border-radius: {RADIUS_CONTROL}px; }}"
        f"QToolButton:hover, QToolButton:pressed {{ background: {tint}; }}"
        f'QToolButton[active="true"] {{ background: {tint}; }}'
        "QToolButton:disabled { background: transparent; }"
    )


_BTN_VOTE_UP = _vote_qss(GREEN_TINT)
_BTN_VOTE_DOWN = _vote_qss(RED_TINT)




_BTN_MODE = (
    f"QToolButton {{ background: transparent; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_CONTROL}px; padding: 3px 8px; font-size: {FONT_BODY}px;"
    f" font-weight: 500; color: {INK}; }}"
    f"QToolButton:hover {{ background: {HOVER}; border-color: {LINE_STRONG}; }}"
    f'QToolButton:pressed, QToolButton[active="true"] {{ background: {HOVER_ON};'
    f" border-color: {LINE_STRONG}; }}"
    "QToolButton::menu-indicator { image: none; width: 0; }"
)





_BTN_SCROLL_PILL = (
    f"QToolButton#scrollPill {{ background: {SURFACE}; border: 1px solid {LINE_STRONG};"
    " border-radius: 14px; padding: 0; }"
    f"QToolButton#scrollPill:hover {{ background: {HOVER}; border-color: {INK_3}; }}"
    f"QToolButton#scrollPill:pressed {{ background: {HOVER_ON}; }}"
)


_BTN_CHIP_CLOSE = (
    "QToolButton { background: transparent; border: none; padding: 0;"
    " border-radius: 7px; }"
    f"QToolButton:hover {{ background: {HOVER_ON}; }}"
)



_BTN_THUMB_CLOSE = (
    "QToolButton { background: rgba(0, 0, 0, 0.62); border: 1px solid rgba(255, 255, 255, 0.75);"
    " border-radius: 9px; padding: 0; }"
    "QToolButton:hover { background: rgba(0, 0, 0, 0.88); }"
)


_BTN_AVATAR = (
    "QToolButton { background: transparent; border: none; padding: 2px;"
    " border-radius: 13px; }"
    f"QToolButton:hover {{ background: {HOVER}; }}"
)









BTN_PILL_PX = BTN_PX
BTN_PRIMARY_WIDE_PX = 36

_BTN_PRIMARY = (
    f"QPushButton {{ background: {ACCENT}; color: {ON_ACCENT};"
    f" border: none; border-radius: {RADIUS_CONTROL}px; padding: 0 12px;"
    f" font-size: {FONT_BODY}px; font-weight: 500; }}"
    f"QPushButton:hover {{ background: {ACCENT_DARK}; }}"
    f"QPushButton:pressed {{ background: {ACCENT_DARK}; }}"
    f"QPushButton:disabled {{ background: {ACCENT_BORDER}; color: {ON_ACCENT}; }}"
)


_BTN_PRIMARY_WIDE = (
    f"QPushButton {{ background: {ACCENT}; color: {ON_ACCENT};"
    f" border: none; border-radius: {RADIUS_CONTROL}px;"
    f" padding: 0 18px; font-size: {FONT_BASE}px; font-weight: 500; }}"
    f"QPushButton:hover {{ background: {ACCENT_DARK}; }}"
    f"QPushButton:pressed {{ background: {ACCENT_DARK}; }}"
    f"QPushButton:disabled {{ background: {ACCENT_BORDER}; color: {ON_ACCENT}; }}"
)
_BTN_GHOST = (
    f"QPushButton {{ background: {SURFACE}; color: {INK};"
    f" border: 1px solid {LINE_STRONG}; border-radius: {RADIUS_CONTROL}px;"
    f" padding: 0 10px; font-size: {FONT_BODY}px; font-weight: 500; }}"
    f"QPushButton:hover {{ background: {HOVER}; }}"
    f"QPushButton:pressed {{ background: {HOVER_ON}; }}"
    f"QPushButton:disabled {{ color: {INK_3}; border-color: {LINE}; }}"
)
_BTN_DANGER_GHOST = (
    f"QPushButton {{ background: {SURFACE}; color: {RED};"
    f" border: 1px solid {LINE_STRONG}; border-radius: {RADIUS_CONTROL}px;"
    f" padding: 0 10px; font-size: {FONT_BODY}px; font-weight: 500; }}"
    f"QPushButton:hover {{ background: {RED_TINT}; border-color: {RED}; }}"
)

_BTN_QUIET = (
    "QPushButton { background: transparent; border: none;"
    f" color: {INK_2}; font-size: {FONT_BODY}px; font-weight: 500; padding: 4px 6px;"
    f" border-radius: {RADIUS_CONTROL}px; }}"
    f"QPushButton:hover {{ color: {INK}; background: {HOVER}; }}"
)

PRO_PILL_PX = 24






_BTN_PRO_PILL = (
    "QPushButton#hdrProPill { background: transparent;"
    f" color: {INK}; border: 1px solid {LINE_STRONG};"
    f" padding: 0 10px 0 8px; font-size: {FONT_BODY}px; font-weight: 600; }}"
    f"QPushButton#hdrProPill:hover {{ background: {HOVER}; }}"
    f"QPushButton#hdrProPill:pressed {{ background: {HOVER_ON}; }}"
    f"QPushButton#hdrProPill:focus {{ border: 2px solid {ACCENT_INK}; padding: 0 9px 0 7px; }}"
    'QPushButton#hdrProPill[compact="true"] { padding: 0; }'
    f'QPushButton#hdrProPill[compact="true"]:focus {{ padding: 0; }}'
)




for _qss_name in ("_BTN_MODE", "_BTN_PRIMARY", "_BTN_PRIMARY_WIDE", "_BTN_GHOST",
                  "_BTN_DANGER_GHOST", "_BTN_QUIET", "_BTN_PRO_PILL"):
    globals()[_qss_name] = scale_qss_font_px(globals()[_qss_name])
del _qss_name





_MENU_QSS = (
    f"QMenu {{ background: {SURFACE}; border: 1px solid {LINE_STRONG};"
    f" border-radius: {RADIUS_CARD}px; padding: 6px; }}"
    "QMenu::item { background: transparent; padding: 7px 8px;"
    f" border-radius: {RADIUS_CHIP}px; color: {INK_2}; font-size: {FONT_BODY}px; }}"
    f"QMenu::item:selected {{ background: {HOVER}; color: {INK}; }}"
    f"QMenu::item:disabled {{ color: {INK_3}; }}"
    f"QMenu::separator {{ height: 1px; background: {LINE};"
    " margin: 4px 8px; }"
)




_HEADER_QSS = scale_qss_font_px(
    "QWidget#hdr { background: transparent;"
    f" border-bottom: 1px solid {LINE}; }}"
    f"QLabel#hdrTitle {{ font-size: {FONT_BODY}px; font-weight: 600;"
    " color: palette(text); background: transparent; border: none; }"
    f"QLabel#hdrByline {{ font-size: {FONT_MICRO}px;"
    f" color: {MUTED}; background: transparent; border: none; }}"
    "QLabel { background: transparent; border: none; }"
    + _MENU_QSS
)


def panel_qss() -> str:
    """One stylesheet for the whole ChatPanel, keyed on object names."""







    bubble = (







        f"QFrame#userBubble {{ background: {FIELD};"
        f" border: 1px solid {USER_PILL_LINE}; border-radius: 18px; }}"
        f"QFrame#userBubble QLabel#tag {{ background: {HOVER_ON}; border: none;"
        f" border-radius: {RADIUS_CHIP}px; padding: 1px 6px; font-size: {FONT_HINT}px; color: {INK}; }}"
    )
    return scale_qss_font_px(
        f"QWidget#chatPanel {{ background: {PAGE}; }}"


        "QWidget#messageContent { background: transparent; }"


        "QTextBrowser#chatText { background: transparent; border: none;"
        f" font-size: {FONT_BASE}px; color: {INK}; }}"
        + bubble

        + f"QTextBrowser#monoText {{ background: {FIELD};"
        f" border: 1px solid {LINE}; border-radius: {RADIUS_CHIP}px;"
        f" font-family: {MONO_FAMILY}; font-size: {FONT_HINT}px;"
        f" color: {INK_2}; }}"


        f"QLabel#statusText {{ font-size: {FONT_BODY}px; font-weight: 500;"
        f" color: {INK_2}; background: transparent; border: none; }}"
        f"QLabel#statusTime {{ font-family: {MONO_FAMILY}; font-size: {FONT_HINT}px;"
        f" color: {INK_3}; background: transparent; border: none; }}"


        f"QFrame#composer {{ background: {SURFACE}; border: 1px solid {LINE_STRONG};"
        f" border-radius: {RADIUS_COMPOSER}px; }}"
        f'QFrame#composer[focused="true"] {{ border: 1px solid {ACCENT_BORDER}; }}'
        "QPlainTextEdit#composerInput { background: transparent; border: none;"
        f" font-size: {FONT_BASE}px; color: {INK}; }}"



        f"QFrame#attachTile {{ background: transparent; border: none; border-radius: {RADIUS_CARD}px; }}"
        "QWidget#attachCardHost, QFrame#attachCardTag { background: transparent; border: none; }"
        f"QFrame#attachCard {{ background: {SURFACE}; border: 1px solid {LINE};"
        f" border-radius: {RADIUS_CARD}px; }}"
        f'QFrame#attachCard[clickable="true"]:hover {{ border-color: {LINE_STRONG};'
        f" background: {HOVER}; }}"
        f"QLabel#attachGlyph {{ background: {FIELD}; border: none; border-radius: {RADIUS_CONTROL}px; }}"
        f"QLabel#attachName {{ font-size: {FONT_BASE + 1}px; font-weight: 500;"
        f" color: {INK}; background: transparent; border: none; }}"
        f"QLabel#attachKind {{ font-size: {FONT_BASE}px; color: {INK_2};"
        " background: transparent; border: none; }"

        f"QLabel#hdrTitle {{ font-size: {FONT_BASE}px; font-weight: 600;"
        f" color: {INK}; background: transparent; }}"
        f"QLabel#footerText {{ font-size: {FONT_HINT}px; color: {INK_3};"
        " background: transparent; border: none; }"

        f"QLabel#cardTitle {{ font-size: {FONT_BASE}px; font-weight: 600;"
        f" color: {INK}; background: transparent; border: none; }}"
        f"QLabel#cardText {{ font-size: {FONT_BODY}px; color: {INK};"
        " background: transparent; border: none; }"
        f"QLabel#hint {{ font-size: {FONT_HINT}px; color: {INK_2};"
        " background: transparent; border: none; }"
        f"QLabel#micro {{ font-size: {FONT_MICRO}px; font-weight: 500; color: {INK_3};"
        " background: transparent; border: none; }"
        f"QLabel#errorText {{ font-size: {FONT_BODY}px; color: {RED};"
        " background: transparent; border: none; }"
        f"QLabel#mono {{ font-family: {MONO_FAMILY}; font-size: {FONT_HINT}px;"
        f" color: {INK_2}; background: transparent; border: none; }}"


        f"QLabel#monoChip {{ font-family: {MONO_FAMILY}; font-size: {FONT_HINT}px;"
        f" color: {INK_2}; background: {FIELD}; border: none;"
        f" border-radius: {RADIUS_CHIP}px; padding: 0 6px; }}"


        f"QLabel#planStep {{ font-size: {FONT_BODY}px; color: {INK_2};"
        " background: transparent; border: none; }"
        f'QLabel#planStep[state="active"] {{ color: {INK}; font-weight: 500; }}'
        f'QLabel#planStep[state="done"] {{ color: {INK}; }}'
        f"QLabel#toolLine {{ font-size: {FONT_BODY}px; font-weight: 500; color: {INK};"
        " background: transparent; border: none; }"
        f'QLabel#toolLine[failed="true"] {{ color: {INK_2}; }}'
        f"QLabel#toolDuration {{ font-family: {MONO_FAMILY}; font-size: {FONT_MICRO}px; color: {INK_3};"
        " background: transparent; border: none; }"
        f"QLabel#traceHead {{ font-size: {FONT_BASE}px; color: {INK_2};"
        " background: transparent; border: none; }"

        f"QLabel#answerNote {{ font-size: {FONT_BODY}px; color: {INK_2};"
        " background: transparent; border: none; }"

        f"QFrame#turnDivider {{ background: {LINE_SOFT}; border: none; }}"



        f"QFrame#compactionRule {{ background: {LINE}; border: none; }}"
        f"QLabel#compactionLabel {{ font-size: {FONT_HINT}px; color: {INK_2};"
        " background: transparent; border: none; }"

        f"QLabel#decisionLine {{ font-size: {FONT_HINT}px; color: {INK_2};"
        " background: transparent; border: none; }"
        + _MENU_QSS
    )





_CARD_QSS = (
    f"QFrame#card {{ background: {SURFACE}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_CARD}px; }}"
    f"QFrame#sheet {{ background: {SURFACE}; border: 1px solid {LINE_STRONG};"
    f" border-radius: {RADIUS_PANEL}px; }}"
    f"QFrame#inset {{ background: {INSET}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_CONTROL}px; }}"
)




_SCROLL_AREA_QSS = (
    "QScrollArea { background: transparent; border: none; }"
    "QScrollBar:vertical { background: transparent; width: 8px;"
    " margin: 2px 2px 2px 0; border: none; }"
    f"QScrollBar::handle:vertical {{ background: {LINE_STRONG};"
    " border-radius: 3px; min-height: 24px; }"
    f"QScrollBar::handle:vertical:hover {{ background: {INK_3}; }}"
    "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {"
    " height: 0px; width: 0px; border: none; background: none; }"
    "QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical {"
    " height: 0px; width: 0px; image: none; }"
    "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {"
    " background: transparent; }"
)








COMPLETER_POPUP_RADIUS = RADIUS_CARD
_COMPLETER_POPUP_QSS = scale_qss_font_px(
    f"QListView#mentionPopup {{ background: {SURFACE}; color: {INK};"
    f" border: 1px solid {LINE_STRONG}; border-radius: {COMPLETER_POPUP_RADIUS}px;"
    f" padding: 6px; font-size: {FONT_BASE}px; outline: none; }}"
    "QListView#mentionPopup::item { border: none; background: transparent; }"
    "QListView#mentionPopup::item:selected { background: transparent; }"

    "QListView#mentionPopup QScrollBar:vertical { background: transparent;"
    " width: 8px; margin: 2px 2px 2px 0; border: none; }"
    "QListView#mentionPopup QScrollBar::handle:vertical {"
    f" background: {LINE_STRONG}; border-radius: 3px; min-height: 24px; }}"
    "QListView#mentionPopup QScrollBar::handle:vertical:hover {"
    f" background: {INK_3}; }}"
    "QListView#mentionPopup QScrollBar::add-line:vertical,"
    " QListView#mentionPopup QScrollBar::sub-line:vertical {"
    " height: 0px; width: 0px; border: none; background: none; }"
    "QListView#mentionPopup QScrollBar::up-arrow:vertical,"
    " QListView#mentionPopup QScrollBar::down-arrow:vertical {"
    " height: 0px; width: 0px; image: none; }"
    "QListView#mentionPopup QScrollBar::add-page:vertical,"
    " QListView#mentionPopup QScrollBar::sub-page:vertical { background: transparent; }"
    "QListView#mentionPopup QScrollBar:horizontal { height: 0px; }"
)


def repolish(widget) -> None:
    """Re-run the stylesheet after a dynamic property changed."""
    try:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()
    except (RuntimeError, AttributeError):
        pass


def accent_ink() -> str:
    """The green that can carry a glyph or a word on the current theme."""




    return ACCENT_INK


def accent_color():
    """``accent_ink`` as a QColor, for the glyphs painted by ``icons.py``."""
    from qgis.PyQt.QtGui import QColor

    return QColor(accent_ink())









def qcolor(token: str):
    """A token (``#rrggbb`` or ``rgba(r, g, b, a)``) as a QColor."""





    from qgis.PyQt.QtGui import QColor

    text = str(token or "").strip()
    if text.startswith("rgba(") or text.startswith("rgb("):
        parts = [p.strip() for p in text[text.index("(") + 1:text.rindex(")")].split(",")]
        try:
            r, g, b = (int(float(p)) for p in parts[:3])
            alpha = float(parts[3]) if len(parts) > 3 else 1.0
        except (ValueError, IndexError):
            return QColor()
        color = QColor(r, g, b)
        color.setAlphaF(max(0.0, min(1.0, alpha)))
        return color
    return QColor(text)


def accent_pill():
    """The pill behind the row the pointer is on, as a QColor."""






    from qgis.PyQt.QtGui import QColor

    color = QColor(ACCENT)
    color.setAlpha(72 if DARK else 56)
    return color


def hover_pill():
    """The hover step as a QColor, for a painted row under the pointer."""
    return qcolor(HOVER)


def muted_ink(ink=None):
    """The second ink for a painted surface."""




    return qcolor(INK_2)


def _shadow_level(level: str) -> tuple:
    """``(blur, offset, alpha)`` of one step of the site's elevation stack."""
    return {
        "card": (6, 2, 0.20 if DARK else 0.06),
        "raised": (10, 2, 0.22 if DARK else 0.08),
        "overlay": (28, 8, 0.34 if DARK else 0.10),
    }.get(level, (28, 8, 0.34 if DARK else 0.10))



_SHADOW_PIXMAPS: dict = {}
_SHADOW_PIXMAPS_KEPT = 8


def _shadow_pixmap(width: int, height: int, radius: float, level: str, ratio: float):
    """The blurred shape alone, ``blur`` px of margin on every side, built once per size."""
    key = (width, height, radius, level, ratio)
    cached = _SHADOW_PIXMAPS.pop(key, None)
    if cached is None:
        from qgis.PyQt.QtCore import QRectF, Qt
        from qgis.PyQt.QtGui import QColor, QImage, QPainter, QPixmap
        from qgis.PyQt.QtWidgets import QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene

        blur, _offset, alpha = _shadow_level(level)
        pad = blur * ratio
        size_w, size_h = int(round(width * ratio + 2 * pad)), int(round(height * ratio + 2 * pad))
        shape = QImage(size_w, size_h, QImage.Format.Format_ARGB32_Premultiplied)
        shape.fill(Qt.GlobalColor.transparent)
        painter = QPainter(shape)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            color = QColor(0, 0, 0)
            color.setAlphaF(alpha)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(pad, pad, width * ratio, height * ratio),
                                    radius * ratio, radius * ratio)
        finally:
            painter.end()

        scene = QGraphicsScene()
        scene.setSceneRect(0, 0, size_w, size_h)
        item = QGraphicsPixmapItem(QPixmap.fromImage(shape))
        effect = QGraphicsBlurEffect()
        effect.setBlurRadius(blur * ratio)
        item.setGraphicsEffect(effect)
        scene.addItem(item)
        out = QImage(size_w, size_h, QImage.Format.Format_ARGB32_Premultiplied)
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        try:
            scene.render(painter, QRectF(0, 0, size_w, size_h), QRectF(0, 0, size_w, size_h))
        finally:
            painter.end()
        cached = QPixmap.fromImage(out)
        cached.setDevicePixelRatio(ratio)
        while len(_SHADOW_PIXMAPS) >= _SHADOW_PIXMAPS_KEPT:
            _SHADOW_PIXMAPS.pop(next(iter(_SHADOW_PIXMAPS)))
    _SHADOW_PIXMAPS[key] = cached
    return cached


def paint_shadow(host, rect, radius: float, level: str = "overlay") -> None:
    """The popover's elevation painted by its translucent host, under ``rect``."""







    from qgis.PyQt.QtGui import QPainter

    ratio_of = getattr(host, "devicePixelRatioF", None) or host.devicePixelRatio
    ratio = max(1.0, float(ratio_of()))
    try:
        pixmap = _shadow_pixmap(rect.width(), rect.height(), float(radius), level, ratio)
    except Exception:  # noqa: BLE001 - a popover without its shadow still works
        return
    blur, offset, _alpha = _shadow_level(level)
    painter = QPainter(host)
    try:
        painter.drawPixmap(rect.x() - blur, rect.y() - blur + offset, pixmap)
    finally:
        painter.end()


def drop_shadow(widget, level: str = "overlay") -> None:
    """The site's elevation on a top-level popover, as a drop shadow effect."""







    try:
        from qgis.PyQt.QtGui import QColor
        from qgis.PyQt.QtWidgets import QGraphicsDropShadowEffect
    except Exception:  # noqa: BLE001 - no Qt here
        return
    blur, offset, alpha = _shadow_level(level)
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, offset)
    color = QColor(0, 0, 0)
    color.setAlphaF(alpha)
    effect.setColor(color)
    widget.setGraphicsEffect(effect)


__all__ = [
    "ACCENT",
    "ACCENT_BORDER",
    "ACCENT_BORDER_SOFT",
    "ACCENT_DARK",
    "ACCENT_INK",
    "ACCENT_INK_DARK",
    "ACCENT_INK_LIGHT",
    "ACCENT_TINT",
    "ACCENT_TINT_ON",
    "BTN_PILL_PX",
    "BTN_PRIMARY_WIDE_PX",
    "BTN_PX",
    "BTN_SMALL_PX",
    "CANVAS",
    "CHIP_PX",
    "COMPLETER_POPUP_RADIUS",
    "COMPOSER_PX",
    "DARK",
    "FIELD",
    "FONT_BASE",
    "FONT_BODY",
    "FONT_HINT",
    "FONT_MICRO",
    "FONT_PROSE",
    "GREEN",
    "GREEN_TINT",
    "HAIRLINE",
    "HAIRLINE_STRONG",
    "HOVER",
    "HOVER_ON",
    "INK",
    "INK_2",
    "INK_3",
    "INK_HOVER",
    "INSET",
    "LETTER_SPACING",
    "LINE",
    "LINE_SOFT",
    "LINE_STRONG",
    "MIN_PANEL_WIDTH",
    "MONO_FAMILY",
    "MOTION_FADE_UP_MS",
    "MOTION_FOLD_MS",
    "MOTION_HOVER_MS",
    "MOTION_POP_MS",
    "MUTED",
    "MUTED_SOFT",
    "ON_ACCENT",
    "ON_ORANGE",
    "ORANGE",
    "ORANGE_DARK",
    "ORANGE_TINT",
    "PAGE",
    "PRO_PILL_PX",
    "RADIUS_BOX",
    "RADIUS_CARD",
    "RADIUS_CHIP",
    "RADIUS_COMPOSER",
    "RADIUS_CONTROL",
    "RADIUS_PANEL",
    "RADIUS_ROW",
    "RADIUS_WINDOW",
    "RED",
    "RED_TINT",
    "ROW_PX",
    "SPACE_CARD",
    "SPACE_OUTER",
    "SPACE_STAGE",
    "SPACE_TIGHT",
    "STRIPE",
    "STRIPE_BG",
    "SURFACE",
    "T",
    "TINT",
    "TINT_HOVER",
    "TINT_ON",
    "TOOLTIP_BG",
    "TOOLTIP_BORDER",
    "TOOLTIP_FG",
    "TOOLTIP_MUTED",
    "USER_PILL_LINE",
    "_BTN_AVATAR",
    "_BTN_CHIP_CLOSE",
    "_BTN_DANGER_GHOST",
    "_BTN_GHOST",
    "_BTN_ICON",
    "_BTN_MODE",
    "_BTN_PRIMARY",
    "_BTN_PRIMARY_WIDE",
    "_BTN_PRO_PILL",
    "_BTN_QUIET",
    "_BTN_SCROLL_PILL",
    "_BTN_SEND",
    "_BTN_THUMB_CLOSE",
    "_BTN_VOTE_DOWN",
    "_BTN_VOTE_UP",
    "_CARD_QSS",
    "_COMPLETER_POPUP_QSS",
    "_HEADER_QSS",
    "_MENU_QSS",
    "_SCROLL_AREA_QSS",
    "accent_color",
    "accent_ink",
    "accent_pill",
    "drop_shadow",
    "hover_pill",
    "is_dark",
    "muted_ink",
    "paint_shadow",
    "panel_qss",
    "qcolor",
    "repolish",
]
