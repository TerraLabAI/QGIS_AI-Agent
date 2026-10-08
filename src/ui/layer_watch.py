# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






























from __future__ import annotations

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QObject, QTimer

from ..core.logger import log_warning
from .layer_icons import layer_followers


def _ids(items) -> frozenset:

    out = set()
    for item in items or ():
        if isinstance(item, str):
            out.add(item)
            continue
        try:
            out.add(str(item.id()))
        except (AttributeError, RuntimeError):
            continue
    return frozenset(out)


class LayerWatch(QObject):


    def __init__(self, root, parent=None):
        super().__init__(parent)
        self._root = root
        self._bound: list = []
        self._later = QTimer(self)
        self._later.setSingleShot(True)
        self._later.setInterval(0)
        self._later.timeout.connect(self.refresh)
        try:
            project = QgsProject.instance()
            pairs = ((project.layersWillBeRemoved, self._on_removing),
                     (project.layersAdded, self._on_added),
                     (project.layerTreeRoot().nameChanged, self.refresh))
        except (AttributeError, RuntimeError) as exc:
            log_warning(f"Layer cards will not follow the project: {exc}")
            return
        for signal, slot in pairs:
            try:
                signal.connect(slot)
            except (TypeError, RuntimeError) as exc:
                log_warning(f"Layer cards will not follow a project signal: {exc}")
                continue
            self._bound.append((signal, slot))

    def follow(self, gone=frozenset()) -> int:


        root = self._root
        read = 0
        for widget in layer_followers():
            try:
                if not root.isAncestorOf(widget):
                    continue
                widget.follow_project(gone)
            except RuntimeError:

                continue
            read += 1
        return read

    def refresh(self, *_signal_args) -> None:

        self.follow()

    def _on_removing(self, items) -> None:
        gone = _ids(items)
        if gone:
            self.follow(gone)

    def _on_added(self, *_signal_args) -> None:
        try:
            self._later.start()
        except RuntimeError:

            return

    def close(self) -> None:

        bound, self._bound = self._bound, []
        for signal, slot in bound:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                continue
        try:
            self._later.stop()
        except RuntimeError:
            return
