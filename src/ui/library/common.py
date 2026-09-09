# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The tokens the library's three surfaces share: the rail, the grid, the detail."""









from __future__ import annotations

from ..font_scale import scale_qss_font_px
from ..style import FONT_BASE, FONT_BODY, FONT_HINT, FONT_MICRO, HAIRLINE, MUTED, RADIUS_CARD
from ..styles import BRAND_BLUE, BRAND_GREEN, WARNING_TEXT
from ..use_cases import group_accent, group_glyph


DIALOG_W, DIALOG_H = 940, 640


RAIL_W = 216


CARD_W, CARD_H = 306, 98
CARD_GAP = 12
GRID_MARGIN = 20


COLUMNS = 2

GROUP_ACCENTS = {
    "explore": BRAND_BLUE,
    "map": BRAND_GREEN,
    "analyse": WARNING_TEXT,
    "terrain": "#8d6e63",
    "share": "#c96a8c",
}
GROUP_GLYPHS = {
    "explore": "globe",
    "map": "layers",
    "analyse": "chart",
    "terrain": "terrain",
    "share": "layout",
}




_SPARE_ACCENTS = ("#5c6bc0", "#26a69a", "#ef6c00", "#7e57c2", "#00897b", "#d81b60")


def accent_of(group: str) -> str:
    key = str(group or "")
    served = group_accent(key)
    if served:
        return served
    if key in GROUP_ACCENTS:
        return GROUP_ACCENTS[key]
    from ..use_cases import use_case_groups

    spare = [k for k, _ in use_case_groups() if k not in GROUP_ACCENTS and not group_accent(k)]
    if key in spare:
        return _SPARE_ACCENTS[spare.index(key) % len(_SPARE_ACCENTS)]
    return BRAND_BLUE


def glyph_of(group: str) -> str:
    """The rail glyph of a group: served, else built in, else a plain circle."""
    key = str(group or "")
    return group_glyph(key) or GROUP_GLYPHS.get(key, "circle")


CARD_QSS = scale_qss_font_px(
    f"QFrame#exampleCard {{ background: palette(base); border: 1px solid {HAIRLINE};"
    f" border-radius: {RADIUS_CARD}px; }}"
    f"QFrame#exampleCard:hover {{ border-color: rgba(139, 172, 39, 0.45);"
    " background: rgba(139, 172, 39, 0.06); }"
    f"QLabel#cardTitle {{ font-size: {FONT_BASE}px; font-weight: 600; color: palette(text);"
    " background: transparent; }"
    f"QLabel#cardNote {{ font-size: {FONT_HINT}px; color: {MUTED}; background: transparent; }}"
    f"QLabel#cardTag {{ font-size: {FONT_MICRO}px; color: {MUTED}; background: transparent; }}"
)

DIALOG_QSS = scale_qss_font_px(
    f"QLabel#libraryHeading {{ font-size: {FONT_BODY}px; font-weight: 600;"
    " color: palette(text); background: transparent; }"
    f"QLabel#libraryCount {{ font-size: {FONT_HINT}px; color: {MUTED}; background: transparent; }}"
    f"QLineEdit#librarySearch {{ background: rgba(128,128,128,0.08);"
    f" border: 1px solid {HAIRLINE}; border-radius: 8px; padding: 6px 10px;"
    f" font-size: {FONT_BASE}px; color: palette(text);"
    " selection-background-color: rgba(128,128,128,0.35); }"
    "QLineEdit#librarySearch:focus { border-color: rgba(139, 172, 39, 0.45); }"
    f"QLabel#libraryEmpty {{ font-size: {FONT_BASE}px; color: {MUTED};"
    " padding: 40px 12px; background: transparent; }"
    f"QLabel#librarySection {{ font-size: {FONT_HINT}px; font-weight: 600; color: {MUTED};"
    " background: transparent; }"
    "QScrollArea#libraryScroll { background: transparent; border: none; }"
    "QScrollArea#libraryScroll > QWidget > QWidget { background: transparent; }"
    "QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }"
    "QScrollBar::handle:vertical { background: rgba(128,128,128,0.35);"
    " border-radius: 4px; min-height: 28px; }"
    "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
    "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
)
