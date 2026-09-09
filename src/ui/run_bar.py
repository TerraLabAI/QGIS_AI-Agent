# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chip of a changed layer, and the QGIS menu behind it."""














from __future__ import annotations

from qgis.PyQt.QtCore import QPoint, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import QMenu, QPushButton

from .icons import icon_for
from .layer_icons import layer_icon
from .shared import exec_menu
from .style import (
    ACCENT_BORDER,
    ACCENT_TINT,
    FONT_HINT,
    HAIRLINE,
    MUTED,
    TINT,
    TINT_HOVER,
)


ACTION_SHOW = "show"
ACTION_ZOOM = "zoom"
ACTION_TABLE = "table"
ACTION_PROPERTIES = "properties"

_MAX_CHIPS = 6





CHIP_HEIGHT = 26
_CHIP_RADIUS = CHIP_HEIGHT // 2




_CHIP_NAME_CHARS = 15
_CHIP_QSS = (
    f"QPushButton {{ background: {TINT}; color: palette(text);"
    f" border: 1px solid {HAIRLINE}; border-radius: {_CHIP_RADIUS}px;"
    f" padding: 0 10px 0 8px; font-size: {FONT_HINT}px; text-align: left; }}"
    f"QPushButton:hover {{ background: {ACCENT_TINT};"
    f" border: 1px solid {ACCENT_BORDER}; }}"
    f"QPushButton:disabled {{ color: {MUTED};"
    f" background: {TINT_HOVER}; border: 1px solid {HAIRLINE}; }}"
    "QPushButton::menu-indicator { image: none; width: 0px; }"
)
_MORE_QSS = (
    f"QLabel {{ color: {MUTED}; font-size: {FONT_HINT}px; background: transparent; border: none; }}"
)


def _short_name(name: str) -> str:
    """A layer name at chip length, cut in the middle so the tail survives."""
    if len(name) <= _CHIP_NAME_CHARS:
        return name
    keep = _CHIP_NAME_CHARS - 1


    head = max(4, keep // 2 - 1)
    return name[:head] + "\u2026" + name[-(keep - head):]


class _LayerChip(QPushButton):
    """One changed layer. Click opens the QGIS action menu for it."""

    action_requested = pyqtSignal(str, str)

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.layer_id = str(item.get("id") or "")
        self.layer_name = str(item.get("name") or self.layer_id or "?")
        self.what = str(item.get("what") or "")
        self.delta = int(item.get("delta") or 0)
        self.setStyleSheet(_CHIP_QSS)
        self.setFixedHeight(CHIP_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        icon = layer_icon(self.layer_id) if self.layer_id else None
        self.setIcon(icon if icon is not None and not icon.isNull() else icon_for(self, "layers", 12))
        name = _short_name(self.layer_name)
        detail = self._detail()
        self.setText(f"{name}  {detail}" if detail else name)
        self.setToolTip(self._tooltip())
        self.setEnabled(bool(self.layer_id) and self.what != "removed")
        self.clicked.connect(self._open_menu)

    def _detail(self) -> str:
        if self.what == "added":
            return self.tr("new")
        if self.what == "removed":
            return self.tr("removed")
        if self.what == "features":
            return f"{self.delta:+d}"
        if self.what == "crs":
            return self.tr("CRS")
        if self.what == "file":
            return self.tr("saved")
        return ""

    def _tooltip(self) -> str:
        if self.what == "added":
            return self.tr("New layer. Click to open it in QGIS.")
        if self.what == "removed":
            return self.tr("This layer was removed from the project.")
        if self.what == "features":
            if abs(self.delta) == 1:
                return self.tr("{n:+d} feature. Click to open it in QGIS.").format(n=self.delta)
            return self.tr("{n:+d} features. Click to open it in QGIS.").format(n=self.delta)
        if self.what == "crs":
            return self.tr("Its CRS changed. Click to open it in QGIS.")
        if self.what == "file":
            return self.tr("Its file was written on disk. Click to open it in QGIS.")
        return self.tr("Click to open it in QGIS.")

    def _open_menu(self) -> None:
        menu = QMenu(self)



        entries = (
            (ACTION_SHOW, "layers", self.tr("Show in Layers panel")),
            (ACTION_ZOOM, "expand", self.tr("Zoom to layer")),
        )
        for action, glyph, label in entries:
            act = menu.addAction(icon_for(menu, glyph, 16), label)
            act.triggered.connect(lambda _=False, a=action: self.action_requested.emit(self.layer_id, a))
        exec_menu(menu, self.mapToGlobal(QPoint(0, self.height() + 2)))
        menu.deleteLater()
