# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later











from __future__ import annotations

import contextlib
import time

from . import telemetry
from . import telemetry_events as ev

WINDOW_S = 30 * 60
MAX_PER_RUN = 20

_noted: dict = {}
_sent: dict = {}
_undoing = 0


@contextlib.contextmanager
def undoing():

    global _undoing
    _undoing += 1
    try:
        yield
    finally:
        _undoing -= 1


def _prune(now: float) -> None:
    for layer_id in [lid for lid, (_run, ended) in _noted.items() if now - ended > WINDOW_S]:
        del _noted[layer_id]
    live = {run_id for run_id, _ended in _noted.values()}
    for run_id in [rid for rid in _sent if rid not in live]:
        del _sent[run_id]


def note(run_id: str, layer_ids) -> None:

    now = time.monotonic()
    _prune(now)
    for layer_id in layer_ids or ():
        _noted[str(layer_id)] = (run_id, now)


def forget() -> None:
    _noted.clear()
    _sent.clear()


def _kind(layer) -> str:
    from qgis.core import QgsRasterLayer, QgsVectorLayer

    if isinstance(layer, QgsVectorLayer):
        return "vector"
    if isinstance(layer, QgsRasterLayer):
        return "raster"
    return "other"


def on_clearing(*_args) -> None:

    if not _undoing:
        forget()


def on_removing(items, by_run: bool = False) -> None:

    if not _noted:
        return
    now = time.monotonic()
    _prune(now)
    via_undo = bool(_undoing)
    if by_run and not via_undo:
        for item in items or ():
            _noted.pop(item if isinstance(item, str) else _id(item), None)
        return
    from qgis.core import QgsProject

    project = QgsProject.instance()
    for item in items or ():
        layer = project.mapLayer(item) if isinstance(item, str) else item
        layer_id = item if isinstance(item, str) else _id(item)
        noted = _noted.pop(layer_id, None)
        if noted is None:
            continue
        run_id, ended = noted
        if _sent.get(run_id, 0) >= MAX_PER_RUN:
            continue
        _sent[run_id] = _sent.get(run_id, 0) + 1
        telemetry.track(ev.AGENT_LAYER_REMOVED, {
            "run_id": run_id, "minutes_since_run_end": round((now - ended) / 60, 1),
            "layer_kind": _kind(layer), "via_undo": via_undo})


def _id(layer) -> str:
    try:
        return str(layer.id())
    except (AttributeError, RuntimeError):
        return ""
