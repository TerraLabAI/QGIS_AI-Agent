# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later























from __future__ import annotations

from dataclasses import dataclass

from ...core.host_platform import IS_WINDOWS
from ..font_scale import scale_px_length, scale_qss_font_px
from ..style import DARK


@dataclass(frozen=True)
class Palette:
    bg: str
    rail: str
    text: str
    text_2: str
    hover: str
    selected: str
    press: str
    border: str
    tile: str
    shimmer: str
    bubble: str
    primary: str
    on_primary: str
    primary_hover: str
    shade: float


LIGHT = Palette(
    bg="#ffffff", rail="#f9f9f9", text="#0d0d0d", text_2="#5d5d5d",
    hover="#f3f3f3", selected="#ececec", press="#e3e3e3", border="#e5e5e5",
    tile="#f3f3f3", shimmer="#fafafa", bubble="#f4f4f4",
    primary="#0d0d0d", on_primary="#ffffff", primary_hover="#2f2f2f", shade=0.08,
)
DARK_PALETTE = Palette(
    bg="#212121", rail="#181818", text="#ececec", text_2="#b4b4b4",
    hover="#2f2f2f", selected="#303030", press="#3a3a3a", border="#383838",
    tile="#2a2a2a", shimmer="#343434", bubble="#303030",
    primary="#ffffff", on_primary="#0d0d0d", primary_hover="#dcdcdc", shade=0.16,
)


def palette(dark: bool | None = None) -> Palette:
    return DARK_PALETTE if (DARK if dark is None else dark) else LIGHT




T = palette()

SPACE_1, SPACE_2, SPACE_3, SPACE_4, SPACE_5 = 8, 16, 24, 32, 40
TITLE_PX, SECTION_PX, BODY_PX, SMALL_PX = 24, 16, 14, 13


MEDIUM = 600 if IS_WINDOWS else 500
RADIUS_TILE, RADIUS_HERO, RADIUS_ROW = 12, 16, 10


DIALOG_W, DIALOG_H = 1080, 720
RAIL_W = 232


COLUMN_W = 820
DETAIL_W = 720

TILE_MIN_W = 200
TILE_GAP = SPACE_2
COLUMNS = 3

TILE_TEXT_H = 90


def px(value: int) -> int:
    return scale_px_length(int(value))


def qss(text: str) -> str:

    return scale_qss_font_px(text)


def text_qss(size: int = BODY_PX, colour: str = "", weight: int = 400) -> str:
    colour = colour or T.text
    bold = f" font-weight: {weight};" if weight != 400 else ""
    return qss(f"font-size: {size}px; color: {colour};{bold} background: transparent;")


def scroll_qss() -> str:
    return (
        "QScrollArea { background: transparent; border: none; }"
        "QScrollArea > QWidget > QWidget { background: transparent; }"
        "QScrollBar:vertical { background: transparent; border: none; width: 10px; margin: 0; }"
        "QScrollBar:horizontal { background: transparent; border: none; height: 0; margin: 0; }"
        "QScrollBar::handle:vertical { background: rgba(128,128,128,0.35); border: none;"
        " border-radius: 3px; min-height: 32px; margin: 2px 3px; }"
        "QScrollBar::handle:vertical:hover { background: rgba(128,128,128,0.6); }"
        "QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; border: none;"
        " background: transparent; }"
        "QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }"
    )


def search_qss(name: str) -> str:

    return qss(
        f"QLineEdit#{name} {{ background: {T.bg}; border: 1px solid {T.border};"
        f" border-radius: 18px; padding: 0 12px 0 36px; font-size: {BODY_PX}px;"
        f" color: {T.text}; selection-background-color: {T.selected};"
        f" selection-color: {T.text}; min-height: 34px; max-height: 34px; }}"
        f"QLineEdit#{name}:focus {{ border-color: {T.text_2}; }}"
    )


def primary_qss() -> str:

    return qss(
        f"QPushButton {{ background: {T.primary}; color: {T.on_primary}; border: none;"
        f" border-radius: 8px; padding: 0 18px; min-height: 36px; max-height: 36px;"
        f" font-size: {BODY_PX}px; font-weight: {MEDIUM}; }}"
        f"QPushButton:hover {{ background: {T.primary_hover}; }}"
        f"QPushButton:pressed {{ background: {T.text_2}; }}"
        f"QPushButton:focus {{ outline: none; }}"
    )


def ghost_qss() -> str:

    return qss(
        f"QPushButton {{ background: {T.bg}; color: {T.text}; border: 1px solid {T.border};"
        f" border-radius: 8px; padding: 0 12px; min-height: 28px; max-height: 28px;"
        f" font-size: {SMALL_PX}px; font-weight: {MEDIUM}; }}"
        f"QPushButton:hover {{ background: {T.hover}; }}"
        f"QPushButton:pressed {{ background: {T.press}; }}"
    )


def page_qss(name: str) -> str:

    return qss(
        f"QDialog#{name} {{ background: {T.bg}; }}"
        f"QLabel#libEmpty {{ font-size: {BODY_PX}px; color: {T.text_2};"
        " padding: 40px 16px; background: transparent; }"
        f"QToolTip {{ color: {T.text}; background: {T.bg}; border: 1px solid {T.border}; }}"
    ) + scroll_qss()


def link_html(href: str, text: str) -> str:

    return (f'<a href="{escape(href)}" style="color: {T.text}; text-decoration: underline;">'
            f"{escape(text)}</a>")


def escape(text: str) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def source_rows(case) -> list:







    wanted = [str(i) for i in getattr(case, "connectors", ()) or [] if str(i)]
    if not wanted:
        return []
    from ..connectors_page import accent_of as category_accent
    from ..shared import get_connectors

    known = {str(row.get("id")): row for row in (get_connectors() or []) if isinstance(row, dict)}
    rows = []
    for key in wanted:
        row = known.get(key)
        if row is not None:
            rows.append({**row, "_accent": category_accent(row.get("category"))})
    return rows
