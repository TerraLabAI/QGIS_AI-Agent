# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The library's left rail: All, then one row per group."""







from __future__ import annotations

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from ..font_scale import scale_px_length, scale_qss_font_px
from ..icons import pixmap_for
from ..settings_pages import sidebar_qss
from ..shared import event_pos
from ..style import FONT_BASE, FONT_BODY, MUTED
from .common import RAIL_W, accent_of

_ROW_QSS = scale_qss_font_px(
    "QFrame#railRow { background: transparent; border: none; border-radius: 8px; }"
    "QFrame#railRow:hover { background: rgba(139, 172, 39, 0.10); }"
    'QFrame#railRow[current="true"] { background: rgba(139, 172, 39, 0.20); }'



    'QFrame#railRow[done="true"] { background: transparent; }'
    'QFrame#railRow[done="true"]:hover { background: transparent; }'
    f"QLabel#railLabel {{ font-size: {FONT_BASE}px; color: palette(text); background: transparent; }}"
    f'QFrame#railRow[done="true"] QLabel#railLabel {{ color: {MUTED}; }}'
    f"QLabel#railCount {{ font-size: {FONT_BODY}px; color: {MUTED}; background: transparent; }}"
)


class RailRow(QFrame):
    """One navigation row: a tinted glyph, a label, a muted count."""

    clicked = pyqtSignal(str)

    def __init__(self, key: str, glyph: str, label: str, tint: str = "", parent=None):
        super().__init__(parent)
        self.key = key
        self.setObjectName("railRow")
        self.setStyleSheet(_ROW_QSS)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("current", "false")
        self.setProperty("done", "false")
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 7, 10, 7)
        row.setSpacing(8)
        icon = QLabel(self)
        size = scale_px_length(16)


        icon.setPixmap(pixmap_for(self, glyph, size, QColor(tint) if tint else None))
        icon.setFixedWidth(size)
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self._label = QLabel(label, self)
        self._label.setObjectName("railLabel")
        row.addWidget(self._label, 1)
        self._count = QLabel("", self)
        self._count.setObjectName("railCount")
        row.addWidget(self._count, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_count(self, count: int) -> None:
        self._count.setText(str(count) if count else "")

    def set_current(self, current: bool) -> None:
        self._flag("current", current)

    def set_done(self, done: bool) -> None:
        """Greyed, no hover, no hand: the row has nothing left to offer."""
        self._flag("done", done)
        self.setEnabled(not done)
        self.setCursor(Qt.CursorShape.ArrowCursor if done
                       else Qt.CursorShape.PointingHandCursor)

    def _flag(self, name: str, on: bool) -> None:
        self.setProperty(name, "true" if on else "false")

        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event_pos(event)):
            self.clicked.emit(self.key)
        super().mouseReleaseEvent(event)


class LibraryRail(QFrame):
    """The whole rail, built once from the group list it is handed."""

    selected = pyqtSignal(str)

    def __init__(self, heading: str, rows: list, parent=None):
        super().__init__(parent)
        self.setObjectName("librarySidebar")
        self.setStyleSheet(sidebar_qss("librarySidebar"))
        self.setFixedWidth(scale_px_length(RAIL_W))
        self._rows: dict = {}
        col = QVBoxLayout(self)
        col.setContentsMargins(6, 14, 6, 12)
        col.setSpacing(2)
        title = QLabel(heading, self)
        title.setStyleSheet("font-size: 12px; font-weight: 600; color: palette(text);")
        title.setContentsMargins(10, 0, 0, 6)
        col.addWidget(title)
        for key, glyph, label in rows:
            row = RailRow(key, glyph, label, accent_of(key), self)
            row.clicked.connect(self.selected.emit)
            self._rows[key] = row
            col.addWidget(row)
        col.addStretch(1)

    def set_counts(self, counts: dict) -> None:
        for key, row in self._rows.items():
            row.set_count(int(counts.get(key, 0)))

    def set_current(self, key: str) -> None:
        for row_key, row in self._rows.items():
            row.set_current(row_key == key)

    def keys(self) -> list:
        return list(self._rows)
