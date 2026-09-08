# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Brand colors and QSS constants shared with AI Segmentation and AI Edit, plus the tiny style helpers."""







from __future__ import annotations

from .font_scale import scale_qss_font_px as _scale_qss_font_px




BTN_GREEN = "#43a047"
BTN_GREEN_HOVER = "#2e7d32"
BTN_GREEN_DISABLED = "#c8e6c9"



BRAND_GREEN = "#8bac27"
BRAND_GREEN_TEXT = "#4d7c0f"
BRAND_BLUE = "#1e88e5"
BRAND_BLUE_HOVER = "#1976d2"
BRAND_RED = "#d32f2f"
BRAND_RED_HOVER = "#b71c1c"
BRAND_GRAY = "#757575"
BRAND_GRAY_HOVER = "#616161"
BRAND_DISABLED = "#b0bec5"
DISABLED_TEXT = "#666666"


def _dark_ui() -> bool:
    """Whether QGIS is running a dark theme."""




    try:
        from qgis.PyQt.QtGui import QPalette
        from qgis.PyQt.QtWidgets import QApplication

        app = QApplication.instance()
        palette = app.palette() if app is not None else QPalette()
        return palette.color(QPalette.ColorRole.Window).lightness() < 128
    except Exception:  # noqa: BLE001 - a missing application is a light theme
        return False


DARK_UI = _dark_ui()




MUTED_TEXT = "#a5a8ad" if DARK_UI else "#5b5f66"

MUTED_TEXT_SOFT = "#6c6f75" if DARK_UI else "#868b93"




INK_HOVER_FILL = "#d7dade" if DARK_UI else "#33363b"
INK_PRESSED_FILL = "#c2c6cb" if DARK_UI else "#43464c"
ERROR_TEXT = "#ef5350"
SUCCESS_TEXT = "#66bb6a"


WARNING_TEXT = "#f5a623"






_CARD_QSS = (
    "QWidget#{name} {{ background-color: palette(base);"
    " border: 1px solid rgba(128, 128, 128, 0.2); border-radius: 12px; }}"
)



_CARD_MARGINS = (12, 10, 12, 10)



_SUBCARD_QSS = (
    "QWidget#{name} {{ background-color: rgba(128, 128, 128, 0.08);"
    " border: 1px solid rgba(128, 128, 128, 0.2); border-radius: 10px; }}"
)
_SUBCARD_MARGINS = (12, 10, 12, 10)



_CARD_CHILD_BTN_RESET_QSS = "QPushButton { border: none; }"

























_MSG_TINTS = {
    "neutral": ("rgba(128, 128, 128, 0.12)", "rgba(128, 128, 128, 0.25)"),
    "info": ("rgba(30, 136, 229, 0.08)", "rgba(30, 136, 229, 0.22)"),
    "armed": ("rgba(30, 136, 229, 0.12)", "rgba(30, 136, 229, 0.40)"),
    "success": ("rgba(139, 172, 39, 0.14)", "rgba(139, 172, 39, 0.45)"),
    "warning": ("rgba(245, 166, 35, 0.12)", "rgba(245, 166, 35, 0.45)"),
    "error": ("rgba(229, 72, 77, 0.14)", "rgba(229, 72, 77, 0.45)"),
    "error_transient": ("rgba(229, 72, 77, 0.25)", "rgba(229, 72, 77, 0.60)"),
    "premium": ("rgba(30, 136, 229, 0.12)", "rgba(30, 136, 229, 0.40)"),
}


_PREMIUM_STAR = "★"




_MSG_GLYPHS = {
    "neutral": "",
    "info": "💡",
    "armed": "✎",
    "success": "✓",
    "warning": "⚠︎",
    "error": "✕",
    "error_transient": "✕",
    "premium": _PREMIUM_STAR,
}


def _msg_text(kind: str, text: str) -> str:
    """Prefix a message with its kind's quiet monochrome glyph."""
    glyph = _MSG_GLYPHS.get(kind, "")
    return f"{glyph}  {text}" if glyph else text


def _msg_label_qss(kind: str) -> str:
    """QSS for a single-QLabel message of the given taxonomy kind."""
    fill, border = _MSG_TINTS[kind]
    text = ERROR_TEXT if kind.startswith("error") else "palette(text)"
    return _scale_qss_font_px(
        f"QLabel {{ background-color: {fill}; border: 1px solid {border};"
        f" border-radius: 10px; padding: 8px 10px; font-size: 12px;"
        f" color: {text}; }}"
    )


def _msg_card_qss(name: str, kind: str) -> str:
    """QSS for a message CARD (a named QWidget with child labels)."""







    return (
        _CARD_QSS.format(name=name)
        + "QLabel { background: transparent; border: none; color: palette(text); }"
    )


def _micro_header(text: str, gloss: str | None = None):
    """Micro section header: a quiet 10px bold label in NORMAL case, THE one way to introduce a subsection inside a card."""




    from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QWidget

    w = QWidget()
    row = QHBoxLayout(w)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    lbl = QLabel(text)
    lbl.setStyleSheet(_scale_qss_font_px(
        "font-size: 10px; font-weight: bold;"
        " color: palette(text); background: transparent; border: none;"))
    row.addWidget(lbl)
    if gloss:
        gl = QLabel(gloss)
        gl.setStyleSheet(_scale_qss_font_px(
            f"font-size: 10px; color: {MUTED_TEXT};"
            " background: transparent; border: none;"))
        row.addWidget(gl)
        w.gloss_label = gl
    row.addStretch(1)
    w.header_label = lbl
    return w


def _card_divider():
    """1px full-width separator between the sub-blocks of ONE card."""
    from qgis.PyQt.QtWidgets import QFrame

    line = QFrame()
    line.setFrameShape(QFrame.Shape.NoFrame)
    line.setFixedHeight(1)
    line.setStyleSheet("background: rgba(128, 128, 128, 0.16); border: none;")
    return line


def _step_dial(num: int, state: str = "todo"):
    """20px round step dial: ``todo`` grey outline number, ``active`` filled brand-blue number, ``done`` a lime outlined check."""

    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtWidgets import QLabel

    from .font_scale import scale_px_length

    lbl = QLabel("✓" if state == "done" else str(num))
    side = scale_px_length(20)
    radius = side // 2
    lbl.setFixedSize(side, side)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    if state == "active":
        qss = (f"background: {BRAND_BLUE}; color: #000000; border: none;"
               f" border-radius: {radius}px; font-size: 11px; font-weight: 700;")
    elif state == "done":
        qss = (f"background: transparent; color: {BRAND_GREEN};"
               " border: 1px solid rgba(139, 172, 39, 0.75);"
               f" border-radius: {radius}px; font-size: 11px; font-weight: 700;")
    else:
        qss = (f"background: transparent; color: {MUTED_TEXT};"
               " border: 1px solid rgba(128, 128, 128, 0.45);"
               f" border-radius: {radius}px; font-size: 11px; font-weight: 600;")
    lbl.setStyleSheet(_scale_qss_font_px(qss))
    return lbl




_SECTION_TOGGLE_QSS = (
    "QPushButton { font-size: 11px; color: palette(text);"
    " font-weight: bold; background-color: rgba(128, 128, 128, 0.10);"
    " border: 1px solid rgba(128, 128, 128, 0.30); border-radius: 4px;"
    " padding: 8px 10px; text-align: left; }"
    f"QPushButton:hover {{ color: {BRAND_BLUE};"
    " border-color: rgba(30, 136, 229, 0.7); }"
    " QPushButton:disabled { color: rgba(128, 128, 128, 0.55);"
    " background-color: rgba(128, 128, 128, 0.06);"
    " border-color: rgba(128, 128, 128, 0.20); }"
)


_COMBO_THEME_QSS = (
    "QComboBox { color: palette(text); background-color: palette(base);"
    " border: 1px solid rgba(128, 128, 128, 0.35); border-radius: 3px;"
    " padding: 2px 8px; }"
    "QComboBox QAbstractItemView { color: palette(text);"
    " background-color: palette(base);"
    " selection-background-color: rgba(30, 136, 229, 0.35); }"
)


_INPUT_THEME_QSS = (
    "QLineEdit { border: 1px solid rgba(128, 128, 128, 0.35);"
    " border-radius: 6px; padding: 7px 10px; background: palette(base);"
    " color: palette(text); }"
    f"QLineEdit:focus {{ border: 1px solid {BRAND_BLUE}; }}"
)



_PROGRESS_THIN_QSS = (
    "QProgressBar { background: rgba(128, 128, 128, 0.25); border: none;"
    " border-radius: 2px; max-height: 3px; min-height: 3px; }"
    f"QProgressBar::chunk {{ background: {BRAND_BLUE}; border-radius: 2px; }}"
)


_INSTRUCTIONS_CARD_QSS = (
    "QLabel {"
    " background-color: rgba(128, 128, 128, 0.12);"
    " border: 1px solid rgba(128, 128, 128, 0.25);"
    " border-radius: 4px;"
    " padding: 8px;"
    " font-size: 12px;"
    " color: palette(text);"
    "}"
)
_INSTRUCTIONS_HINT_QSS = (
    "QLabel {"
    " background: transparent;"
    " border: none;"
    " padding: 2px 0px;"
    " font-size: 11px;"
    f" color: {MUTED_TEXT};"
    "}"
)








_BTN_LABEL_WEIGHT = "font-weight: 600;"

_BTN_GREEN = (
    f"QPushButton {{ background-color: {BTN_GREEN}; color: #000000;"
    f" padding: 8px 16px; border: none; border-radius: 4px;"
    f" {_BTN_LABEL_WEIGHT} }}"
    f"QPushButton:hover {{ background-color: {BTN_GREEN_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BTN_GREEN_DISABLED};"
    f" color: {DISABLED_TEXT}; }}"
)

_BTN_GREEN_AUTH = (
    f"QPushButton {{ background-color: {BTN_GREEN}; color: #000000;"
    f" border: none; border-radius: 4px; {_BTN_LABEL_WEIGHT} }}"
    f"QPushButton:hover {{ background-color: {BTN_GREEN_HOVER}; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED};"
    f" color: {DISABLED_TEXT}; }}"
)

_BTN_BLUE = (
    f"QPushButton {{ background-color: {BRAND_BLUE}; color: #000000;"
    f" padding: 6px 12px; border: none; border-radius: 4px;"
    f" {_BTN_LABEL_WEIGHT} }}"
    f"QPushButton:hover {{ background-color: {BRAND_BLUE_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED};"
    f" color: {DISABLED_TEXT}; }}"
)

_BTN_BLUE_AUTH = (
    f"QPushButton {{ background-color: {BRAND_BLUE}; color: #000000;"
    f" border: none; border-radius: 4px; {_BTN_LABEL_WEIGHT} }}"
    f"QPushButton:hover {{ background-color: {BRAND_BLUE_HOVER}; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED}; }}"
)




_UPDATE_CARD_STYLE = (
    "QWidget#updateCard { background-color: palette(base);"
    " border: 1px solid rgba(128, 128, 128, 0.2); border-radius: 16px; }"
    "QLabel { background: transparent; border: none; }"
)
_UPDATE_TITLE_STYLE = (
    "font-size: 13px; font-weight: 600; color: palette(text);"
    " background: transparent;"
)
_UPDATE_NOTE_STYLE = (
    f"font-size: 11px; color: {MUTED_TEXT}; background: transparent;"
)
_UPDATE_LATER_STYLE = (
    f"QPushButton {{ background: transparent; color: {MUTED_TEXT};"
    f" border: none; font-size: 11px; padding: 2px 6px; {_BTN_LABEL_WEIGHT} }}"
    "QPushButton:hover { color: palette(text); text-decoration: underline; }"
)


_BTN_BLUE_PRIMARY = (
    f"QPushButton {{ background-color: {BRAND_BLUE}; color: #000000;"
    f" padding: 8px 16px; border: none; border-radius: 4px;"
    f" {_BTN_LABEL_WEIGHT} }}"
    f"QPushButton:hover {{ background-color: {BRAND_BLUE_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED};"
    f" color: {DISABLED_TEXT}; }}"
)



_BTN_START_FONT_PX = 13


def _btn_start_qss(base: str) -> str:
    """A primary button constant, with the label size a page's Start carries."""
    return base + f"QPushButton {{ font-size: {_BTN_START_FONT_PX}px; }}"




_BTN_GHOST = (
    "QPushButton { background-color: transparent; color: palette(text);"
    " padding: 8px 16px; border-radius: 4px;"
    f" {_BTN_LABEL_WEIGHT}"
    " border: 1px solid rgba(128, 128, 128, 0.35); }"
    "QPushButton:hover { background-color: rgba(128, 128, 128, 0.15);"
    " border: 1px solid rgba(128, 128, 128, 0.5); }"
    f"QPushButton:disabled {{ background-color: rgba(128, 128, 128, 0.08);"
    f" border: 1px solid rgba(128, 128, 128, 0.15); color: {DISABLED_TEXT}; }}"
)



_BTN_BLUE_OUTLINE = (
    f"QPushButton {{ background-color: transparent; color: {BRAND_BLUE};"
    f" border: 1px solid {BRAND_BLUE}; border-radius: 4px; font-weight: 600;"
    " padding: 6px 12px; }"
    "QPushButton:hover { background-color: rgba(30, 136, 229, 0.12); }"
    f"QPushButton:disabled {{ color: {DISABLED_TEXT};"
    f" border-color: {DISABLED_TEXT}; }}"
)



_BTN_RED_OUTLINE = (
    f"QPushButton {{ background-color: transparent; color: {BRAND_RED};"
    " border: 1px solid rgba(211, 47, 47, 0.55); border-radius: 4px;"
    f" {_BTN_LABEL_WEIGHT}"
    " padding: 6px 12px; }"
    "QPushButton:hover { background-color: rgba(211, 47, 47, 0.12); }"
    f"QPushButton:disabled {{ color: {DISABLED_TEXT};"
    " border-color: rgba(128, 128, 128, 0.35); }"
)



_BTN_LINK = (
    f"QPushButton {{ background: transparent; border: none; color: {BRAND_BLUE};"
    " font-size: 11px; text-align: left; padding: 2px 0px; }"
    "QPushButton:hover { text-decoration: underline; }"
)
_BTN_LINK_MUTED = (
    "QPushButton { background: transparent; border: none;"
    f" color: {MUTED_TEXT}; font-size: 11px; padding: 4px 8px; }}"
    f"QPushButton:hover {{ color: {ERROR_TEXT}; text-decoration: underline; }}"
)


_BTN_LINK_STRONG = (
    "QPushButton { background: transparent; border: none;"
    " color: palette(text); font-size: 11px; font-weight: 600;"
    " padding: 4px 8px; text-decoration: underline; }"
    f"QPushButton:hover {{ color: {ERROR_TEXT}; }}"
)



_CHIP_QSS = (
    "QPushButton { background: rgba(30, 136, 229, 0.10);"
    " border: 1px solid rgba(30, 136, 229, 0.35); border-radius: 6px;"
    " color: palette(text); font-size: 12px; text-align: left;"
    " padding: 6px 10px; }"
    "QPushButton:hover { background: rgba(30, 136, 229, 0.20); }"
)



_BTN_CHIP = (
    "QPushButton { background: rgba(128, 128, 128, 0.08);"
    " border: 1px solid rgba(128, 128, 128, 0.40); border-radius: 6px;"
    f" padding: 6px 12px; font-size: 12px; color: palette(text);"
    f" {_BTN_LABEL_WEIGHT} }}"
    "QPushButton:hover { background: rgba(139, 172, 39, 0.18);"
    " border-color: rgba(139, 172, 39, 0.65); }"
    "QPushButton:pressed { background: rgba(139, 172, 39, 0.32);"
    " border-color: rgba(139, 172, 39, 0.85); }"
    "QPushButton:disabled { color: rgba(128, 128, 128, 0.40);"
    " background: transparent; border-color: rgba(128, 128, 128, 0.20); }"
)


def _segmented_switch_qss(name: str) -> str:
    """QSS for a two-half segmented control on the given frame objectName."""

    return (
        f"QFrame#{name} {{"
        "  background: rgba(128, 128, 128, 0.05);"
        "  border: 1px solid rgba(128, 128, 128, 0.35);"
        "  border-radius: 6px;"
        "}"
        "QPushButton {"
        "  background: transparent;"
        "  border: 1px solid transparent;"
        "  border-radius: 5px;"
        "  padding: 6px 0px;"
        "  font-size: 12px;"
        "  color: palette(text);"
        "}"
        "QPushButton:hover {"
        "  background: rgba(128, 128, 128, 0.12);"
        "}"
        "QPushButton:checked {"
        f"  background: {BRAND_BLUE};"
        "  color: #ffffff;"
        "  font-weight: 700;"
        f"  border: 1px solid {BRAND_BLUE};"
        "}"
        f"QPushButton:checked:hover {{"
        f"  background: {BRAND_BLUE_HOVER};"
        f"  border-color: {BRAND_BLUE_HOVER};"
        f"}}"
    )


def _btn_toggle_qss(rgb: tuple[int, int, int], text: str, armed_text: str,
                    weight: int = 700, quiet: bool = False,
                    filled: bool = False) -> str:
    """Armable toggle button (tinted outline at rest, solid fill while the ``armed`` dynamic property is true)."""


    r, g, b = rgb
    solid = f"rgb({r}, {g}, {b})"
    dark = f"rgb({int(r * 0.8)}, {int(g * 0.8)}, {int(b * 0.8)})"
    if quiet:
        rest = (
            "QPushButton { background: transparent; color: palette(text);"
            " border: 1px solid rgba(128, 128, 128, 0.40); border-radius: 6px;"
            " padding: 6px 12px; font-size: 12px; }"
            f"QPushButton:hover {{ background: rgba({r}, {g}, {b}, 0.14);"
            f" border-color: rgba({r}, {g}, {b}, 0.55); }}"
        )
    elif filled:
        rest = (
            f"QPushButton {{ background: {solid}; color: #000000;"
            f" border: none; border-radius: 6px; padding: 9px 16px;"
            f" font-size: 12px; font-weight: {weight}; }}"
            f"QPushButton:hover {{ background: {dark}; }}"
        )
    else:
        rest = (
            f"QPushButton {{ background: rgba({r}, {g}, {b}, 0.12); color: {text};"
            f" border: 1px solid rgba({r}, {g}, {b}, 0.55); border-radius: 6px;"
            f" padding: 9px 16px; font-size: 12px; font-weight: {weight}; }}"
            f"QPushButton:hover {{ background: rgba({r}, {g}, {b}, 0.22); }}"
        )
    combined = rest
    if filled:
        combined += (f'QPushButton[armed="true"] {{ background: {dark};'
                     f" color: #000000; border: none; }}")
    else:
        combined += (f'QPushButton[armed="true"] {{ background: {solid};'
                     f" color: {armed_text}; border: 1px solid {solid}; }}")
    combined += "QPushButton:disabled { background: transparent;"
    combined += " color: rgba(128, 128, 128, 0.5); border-color: rgba(128, 128, 128, 0.3); }"
    return _scale_qss_font_px(combined)


_BTN_GRAY = (
    f"QPushButton {{ background-color: {BRAND_GRAY}; color: #000000;"
    f" padding: 4px 8px; border: none; border-radius: 4px;"
    f" {_BTN_LABEL_WEIGHT} }}"
    f"QPushButton:hover {{ background-color: {BRAND_GRAY_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED}; color: {DISABLED_TEXT}; }}"
)

_BTN_RED = (
    f"QPushButton {{ background-color: rgba(211,47,47,0.12); color: {BRAND_RED};"
    f" padding: 6px 12px; border: none; border-radius: 4px;"
    f" {_BTN_LABEL_WEIGHT} }}"
    f"QPushButton:hover {{ background-color: rgba(211,47,47,0.22); }}"
)



_BTN_PAIR_NEUTRAL = (
    "QPushButton { background-color: rgba(128,128,128,0.16); color: palette(text);"
    f" border: none; border-radius: 4px; {_BTN_LABEL_WEIGHT} }}"
    "QPushButton:hover { background-color: rgba(128,128,128,0.28); }"
)
_BTN_PAIR_CANCEL = (
    f"QPushButton {{ background-color: rgba(211,47,47,0.12); color: {BRAND_RED};"
    f" border: none; border-radius: 4px; {_BTN_LABEL_WEIGHT} }}"
    f"QPushButton:hover {{ background-color: rgba(211,47,47,0.22); }}"
)






_FOOTER_ICON_BTN_STYLE = (
    "QToolButton { background: transparent; border: none; padding: 6px 10px;"
    " font-size: 22px; font-weight: 600;"
    " color: palette(text); border-radius: 4px; }"
    'QToolButton[hover="true"] { background: rgba(128,128,128,0.15); }'
    'QToolButton[active="true"] { background: rgba(139, 172, 39, 0.55); }'
    'QToolButton[active="true"][hover="true"] { background: rgba(139, 172, 39, 0.75); }'
    "QToolButton::menu-indicator { image: none; width: 0; }"
)



_HELP_ICON_BTN_STYLE = (
    "QToolButton { background: transparent; border: none; padding: 6px 10px;"
    " font-size: 22px; font-weight: 600;"
    " color: palette(text); border-radius: 4px; }"
    'QToolButton[hover="true"] { background: rgba(139, 172, 39, 0.35); }'
    'QToolButton[active="true"] { background: rgba(139, 172, 39, 0.55); }'
    'QToolButton[active="true"][hover="true"] { background: rgba(139, 172, 39, 0.75); }'
    "QToolButton::menu-indicator { image: none; width: 0; }"
)

_FOOTER_MENU_STYLE = (
    "QMenu { background: palette(base); border: 1px solid rgba(128,128,128,0.35);"
    " border-radius: 6px; padding: 4px; }"
    "QMenu::item { background: transparent; padding: 6px 14px; border-radius: 4px;"
    " color: palette(text); }"
    "QMenu::item:selected { background: rgba(128,128,128,0.18); }"
    "QMenu::item:disabled { color: rgba(128,128,128,0.55); }"
    "QMenu::separator { height: 1px; background: rgba(128,128,128,0.25);"
    " margin: 4px 8px; }"
)



_FOOTER_CTA_BTN_STYLE = (
    "QToolButton { background: transparent; border: none; padding: 6px 10px;"
    " font-size: 11px; font-weight: 600;"
    " color: palette(text); border-radius: 4px; }"
    'QToolButton[hover="true"] { background: rgba(128,128,128,0.15); }'
)





for _qss_name in (
    "_SECTION_TOGGLE_QSS",
    "_INSTRUCTIONS_CARD_QSS",
    "_INSTRUCTIONS_HINT_QSS",
    "_BTN_LINK",
    "_BTN_LINK_MUTED",
    "_BTN_LINK_STRONG",
    "_CHIP_QSS",
    "_BTN_CHIP",
    "_FOOTER_ICON_BTN_STYLE",
    "_HELP_ICON_BTN_STYLE",
    "_FOOTER_CTA_BTN_STYLE",
):
    globals()[_qss_name] = _scale_qss_font_px(globals()[_qss_name])
del _qss_name
