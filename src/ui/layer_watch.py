# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Everything in the panel that names a layer follows the layer."""


























from __future__ import annotations

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QObject, QTimer

from ..core.logger import log_warning
from .bubbles import AgentBubble
from .layer_card import LayerCard
from .layer_links import RunChangesRow


_FOLLOWERS = (RunChangesRow, LayerCard, AgentBubble)


def _ids(items) -> frozenset:
    """The layer ids of a project signal, which sends ids or layers."""
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
    """Tells what names a layer under ``root`` when the project changed."""

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
        """Everything under the root that names a layer matches the project as it is now."""

        try:
            followers = self._root.findChildren(_FOLLOWERS)
        except (RuntimeError, TypeError):

            return 0
        for widget in followers:
            try:
                widget.follow_project(gone)
            except RuntimeError:

                continue
        return len(followers)

    def refresh(self, *_signal_args) -> None:
        """A layer was renamed, or layers came back: read everything again."""
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
        """Let go of the project. Safe to call twice."""
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
