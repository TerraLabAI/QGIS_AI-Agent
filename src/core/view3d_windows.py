# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
























from __future__ import annotations

from qgis.PyQt import sip
from qgis.PyQt.QtGui import QWindow

from .qt_compat import qgis_iface

_runs: set = set()

_waiting: list = []


def window_of(canvas):


    holder = canvas.parent() if hasattr(canvas, "parent") else None
    iface = qgis_iface()
    main = iface.mainWindow() if iface is not None else None
    if holder is None or (main is not None and holder is main.windowHandle()):
        return None
    return holder


def is_shown(holder) -> bool:
    return bool(holder.isVisible()) and holder.visibility() != QWindow.Visibility.Minimized


def bring_front(holder, activate: bool = True) -> None:


    if holder in _waiting:
        _waiting.remove(holder)
    if not is_shown(holder):


        holder.setProperty("_q_showWithoutActivating", not activate)
        holder.showNormal()
        holder.setProperty("_q_showWithoutActivating", False)
    holder.raise_()
    if activate:
        holder.requestActivate()


def touched(canvas, created: bool) -> dict | None:


    holder = window_of(canvas)
    if holder is None:
        return None
    if not _runs:
        bring_front(holder)
        return {"shown": is_shown(holder), "raised": True}
    if created:
        holder.hide()
    if holder not in _waiting:
        _waiting.append(holder)
    return {"shown": is_shown(holder), "in_front_at_run_end": True}


def begin_run(run_id: str) -> None:
    _runs.add(run_id)


def end_run(run_id: str) -> None:

    _runs.discard(run_id)
    show_waiting()


def show_waiting() -> None:
    waiting = list(_waiting)
    _waiting.clear()
    for holder in waiting:
        if not sip.isdeleted(holder):
            bring_front(holder, activate=False)


def shutdown() -> None:

    _runs.clear()
    show_waiting()
