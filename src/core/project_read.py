# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later















from __future__ import annotations

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QTimer

from .logger import log_warning
from .qt_compat import disconnect_quietly

_reading = False
_waiting: list = []


def reading() -> bool:

    return _reading


def when_done(fn) -> None:

    if fn not in _waiting:
        _waiting.append(fn)


def _layer_loaded(done: int, _total: int) -> None:
    global _reading
    if done == 0:
        _reading = True


def _read(*_document) -> None:

    QTimer.singleShot(0, _release)


def _release() -> None:
    global _reading
    _reading = False
    waiting = list(_waiting)
    del _waiting[:]
    for fn in waiting:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Work held during a project read failed to start: {exc}")


def install() -> None:

    project = QgsProject.instance()
    project.layerLoaded.connect(_layer_loaded)
    project.readProject.connect(_read)


def shutdown() -> None:

    global _reading
    project = QgsProject.instance()
    disconnect_quietly(project, "layerLoaded", _layer_loaded)
    disconnect_quietly(project, "readProject", _read)
    _reading = False
    del _waiting[:]
