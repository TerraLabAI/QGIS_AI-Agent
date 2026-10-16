# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

from .font_scale import scale_qss_font_px as _scale_qss_font_px




BTN_GREEN = "#43a047"
BTN_GREEN_HOVER = "#2e7d32"


BRAND_GREEN = "#8bac27"
BRAND_GREEN_TEXT = "#4d7c0f"
BRAND_BLUE = "#1e88e5"
BRAND_RED = "#d32f2f"
DISABLED_TEXT = "#666666"


def _dark_ui() -> bool:





    try:
        from qgis.PyQt.QtGui import QPalette
        from qgis.PyQt.QtWidgets import QApplication

        app = QApplication.instance()
        palette = app.palette() if app is not None else QPalette()
        return palette.color(QPalette.ColorRole.Window).lightness() < 128
    except Exception:  # noqa: BLE001
        return False


DARK_UI = _dark_ui()



MUTED_TEXT = "#a5a8ad" if DARK_UI else "#5b5f66"




INK_HOVER_FILL = "#d7dade" if DARK_UI else "#33363b"
INK_PRESSED_FILL = "#c2c6cb" if DARK_UI else "#43464c"
ERROR_TEXT = "#ef5350"


WARNING_TEXT = "#f5a623"






_CARD_QSS = (
    "QWidget#{name} {{ background-color: palette(base);"
    " border: 1px solid rgba(128, 128, 128, 0.2); border-radius: 12px; }}"
)



_CARD_MARGINS = (12, 10, 12, 10)



_CARD_CHILD_BTN_RESET_QSS = "QPushButton { border: none; }"

























_MSG_TINTS = {
    "neutral": ("rgba(128, 128, 128, 0.12)", "rgba(128, 128, 128, 0.25)"),
    "info": ("rgba(30, 136, 229, 0.08)", "rgba(30, 136, 229, 0.22)"),
    "armed": ("rgba(30, 136, 229, 0.12)", "rgba(30, 136, 229, 0.40)"),
    "success": ("rgba(67, 160, 71, 0.14)", "rgba(67, 160, 71, 0.45)"),
    "warning": ("rgba(245, 166, 35, 0.12)", "rgba(245, 166, 35, 0.45)"),
    "error": ("rgba(229, 72, 77, 0.14)", "rgba(229, 72, 77, 0.45)"),
    "error_transient": ("rgba(229, 72, 77, 0.25)", "rgba(229, 72, 77, 0.60)"),
    "premium": ("rgba(30, 136, 229, 0.12)", "rgba(30, 136, 229, 0.40)"),
}


def _msg_label_qss(kind: str) -> str:

    fill, border = _MSG_TINTS[kind]
    text = ERROR_TEXT if kind.startswith("error") else "palette(text)"
    return _scale_qss_font_px(
        f"QLabel {{ background-color: {fill}; border: 1px solid {border};"
        f" border-radius: 10px; padding: 8px 10px; font-size: 12px;"
        f" color: {text}; }}"
    )


def _msg_card_qss(name: str, kind: str) -> str:








    return (
        _CARD_QSS.format(name=name)
        + "QLabel { background: transparent; border: none; color: palette(text); }"
    )






_BTN_LABEL_WEIGHT = "font-weight: 600;"


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


__all__ = [
    "BRAND_BLUE",
    "BRAND_GREEN",
    "BRAND_GREEN_TEXT",
    "BRAND_RED",
    "BTN_GREEN",
    "BTN_GREEN_HOVER",
    "DARK_UI",
    "DISABLED_TEXT",
    "ERROR_TEXT",
    "INK_HOVER_FILL",
    "INK_PRESSED_FILL",
    "MUTED_TEXT",
    "WARNING_TEXT",
    "_BTN_GHOST",
    "_CARD_CHILD_BTN_RESET_QSS",
    "_CARD_MARGINS",
    "_CARD_QSS",
    "_MSG_TINTS",
    "_UPDATE_CARD_STYLE",
    "_UPDATE_LATER_STYLE",
    "_UPDATE_NOTE_STYLE",
    "_UPDATE_TITLE_STYLE",
    "_msg_card_qss",
    "_msg_label_qss",
]
