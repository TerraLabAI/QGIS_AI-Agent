# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat panel's design tokens: the Beautiful UI line (docs/DESIGN.md)."""


























from __future__ import annotations

from .font_scale import scale_qss_font_px
from .styles import _MSG_TINTS, BRAND_GREEN, BRAND_GREEN_TEXT, DARK_UI


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
    "accent_tint": "rgba(139, 172, 39, 0.10)",
    "accent_tint_on": "rgba(139, 172, 39, 0.20)",
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
    "accent_ink": "#a3c644",
    "accent_tint": "rgba(139, 172, 39, 0.16)",
    "accent_tint_on": "rgba(139, 172, 39, 0.26)",
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





ACCENT = BRAND_GREEN
ACCENT_DARK = "#76a32a"
ACCENT_TINT = "rgba(139, 172, 39, 0.10)"
ACCENT_TINT_ON = "rgba(139, 172, 39, 0.20)"
ACCENT_BORDER_SOFT = "rgba(139, 172, 39, 0.24)"
ACCENT_BORDER = "rgba(139, 172, 39, 0.45)"



ACCENT_INK_LIGHT = BRAND_GREEN_TEXT
ACCENT_INK_DARK = "#a3c644"
ACCENT_INK = T["accent_ink"]


ON_ACCENT = "#14210A"



MONO_FAMILY = "Consolas, 'DejaVu Sans Mono', Menlo, monospace"




USER_PILL_LINE = LINE_STRONG if not DARK else "rgba(255, 255, 255, 0.05)"

NEUTRAL_FILL, NEUTRAL_BORDER = _MSG_TINTS["neutral"]
SUCCESS_FILL, SUCCESS_BORDER = _MSG_TINTS["success"]











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
    f'QToolButton[running="true"] {{ background: {INK}; }}'
    f'QToolButton[running="true"]:hover {{ background: {INK_HOVER}; }}'
)




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


_BTN_CHIP_ADD = (
    "QToolButton { background: transparent;"
    f" border: 1px dashed {LINE_STRONG}; border-radius: {RADIUS_CHIP}px;"
    f" padding: 0 6px; font-size: {FONT_HINT}px; color: {INK_2}; }}"
    f"QToolButton:hover {{ background: {HOVER}; border-color: {INK_3};"
    f" color: {INK}; }}"
    "QToolButton::menu-indicator { image: none; width: 0; }"
)


_BTN_HELP = (
    "QToolButton { background: transparent; border: none; padding: 0;"
    f" border-radius: {RADIUS_CONTROL}px; font-size: {FONT_BASE}px; font-weight: 700;"
    f" color: {INK_2}; }}"
    f"QToolButton:hover {{ background: {HOVER}; color: {INK}; }}"
    f'QToolButton[active="true"] {{ background: {HOVER_ON}; }}'
    "QToolButton::menu-indicator { image: none; width: 0; }"
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




for _qss_name in ("_BTN_MODE", "_BTN_CHIP_ADD", "_BTN_HELP", "_BTN_PRIMARY",
                  "_BTN_PRIMARY_WIDE", "_BTN_GHOST", "_BTN_DANGER_GHOST", "_BTN_QUIET"):
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
    f"QLabel#hdrTitle {{ font-size: {FONT_BASE}px; font-weight: 600;"
    " color: palette(text); background: transparent; border: none; }"
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

        f"QFrame#turnDivider {{ background: {LINE_SOFT}; border: none; }}"

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


def drop_shadow(widget, level: str = "overlay") -> None:
    """The site's elevation on a top-level popover, as a drop shadow effect."""







    try:
        from qgis.PyQt.QtGui import QColor
        from qgis.PyQt.QtWidgets import QGraphicsDropShadowEffect
    except Exception:  # noqa: BLE001 - no Qt here
        return
    blur, offset, alpha = {
        "card": (6, 2, 0.20 if DARK else 0.06),
        "raised": (10, 2, 0.22 if DARK else 0.08),
        "overlay": (28, 8, 0.34 if DARK else 0.10),
    }.get(level, (28, 8, 0.34 if DARK else 0.10))
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, offset)
    color = QColor(0, 0, 0)
    color.setAlphaF(alpha)
    effect.setColor(color)
    widget.setGraphicsEffect(effect)
