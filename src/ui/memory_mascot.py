# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later











from __future__ import annotations

import json

from qgis.PyQt.QtCore import QEvent, QObject, QPoint, QTimer

from ..core import mascot as table_of
from ..core.host_platform import prefers_reduced_motion
from ..core.qt_compat import qt_deleted
from .font_scale import scale_px_length
from .mascot import BOX_H, BOX_W, GAZE, LIVE, STILL, Mascot

SAVED, UNDONE = "memory.saved", "memory.undone"
ADDED, EDITED, DELETED, CLEARED = "memory.added", "memory.edited", "memory.deleted", "memory.cleared"
EMPTY, REST, ON, OFF, ADD_HOVER = "memory.empty", "memory.rest", "memory.on", "memory.off", "memory.add.hover"

MOVES = {
    SAVED: {"burst": ("proud", 1500), "react": ("nod", 0.9)},
    UNDONE: {"burst": ("calm", 1400), "react": ("duck", 0.9)},
    ADDED: {"burst": ("happy", 1400), "react": ("bounce", 0.9)},
    EDITED: {"react": ("nod", 0.7)},
    DELETED: {"burst": ("calm", 1200), "react": ("duck", 0.9)},
    CLEARED: {"burst": ("dizzy", 1600), "react": ("shake", 0.9)},
    EMPTY: {"mood": "curious"},
    REST: {"mood": "idle"},
    ON: {"mood": "idle", "burst": ("wake", 1300), "react": ("stretch", 1.0)},
    OFF: {"mood": "sleep"},
    ADD_HOVER: {"hover": "hope"},
}
_BESIDE_GAP = 8
_GLANCE_MS = 1800


def play(mascot, event: str, look=None) -> None:

    if qt_deleted(mascot):
        return
    if event in mascot.table()["events"]:
        mascot.fire(event, look)
    else:
        move = MOVES.get(event) or {}
        mascot.stir()
        if "mood" in move:
            mascot.set_mood(move["mood"])
        if "hover" in move:
            mascot.hover(move["hover"], look)
        if "burst" in move:
            mascot.burst(*move["burst"])
        if "react" in move:
            mascot.react(*move["react"])
    if look is not None and event in (SAVED, UNDONE):
        mascot.look_at(look)
        QTimer.singleShot(_GLANCE_MS, lambda: None if qt_deleted(mascot) else mascot.look_at(None))


class HoverWatch(QObject):


    def __init__(self, widget, enter, leave):
        super().__init__(widget)
        self._enter, self._leave = enter, leave
        widget.installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() == QEvent.Type.Enter:
            self._enter()
        elif event.type() in (QEvent.Type.Leave, QEvent.Type.Hide):
            self._leave()
        return False


class Companion(QObject):







    def __init__(self, host, settings, size_px: int, beside=None):
        super().__init__(host)
        self._host = host
        self._settings = settings
        self._size = scale_px_length(size_px)
        self._beside = beside
        self.mascot = Mascot(host, self._table())
        self.refresh_mode()
        host.installEventFilter(self)
        if beside is not None:
            beside.installEventFilter(self)
        self._place()
        self.mascot.show()

    def _table(self) -> dict:
        try:
            stored = table_of.merged(json.loads(self._settings.mascot_table or "null"))
        except (ValueError, TypeError, AttributeError):
            stored = None
        return stored or table_of.BUILT_IN

    def refresh_mode(self) -> None:
        if not self.mascot.table()["enabled"]:
            mode = STILL
        elif prefers_reduced_motion():
            mode = GAZE
        else:
            mode = LIVE
        self.mascot.set_mode(mode)

    def play(self, event: str, look=None) -> None:
        play(self.mascot, event, look)

    def _place(self) -> None:
        label = self._beside
        if label is not None:
            half = self._size * BOX_W / BOX_H / 2.0
            corner = label.mapTo(self._host, QPoint(0, 0))
            metrics = label.fontMetrics()
            text_end = corner.x() + metrics.horizontalAdvance(label.text())
            baseline = corner.y() + (label.height() + metrics.ascent() - metrics.descent()) / 2.0
            self.mascot.put(text_end + scale_px_length(_BESIDE_GAP) + half, baseline, self._size)
            self.mascot.raise_()
        else:
            self.mascot.put(self._host.width() / 2.0, self._host.height() - 2.0, self._size)

    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Move):
            self._place()
        return False
