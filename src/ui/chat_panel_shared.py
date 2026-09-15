# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the split chat panel shares: its per-run record and its small helpers."""





from __future__ import annotations

import time

from qgis.PyQt.QtWidgets import QFrame, QVBoxLayout, QWidget

from .bubbles import AgentBubble
from .shared import tr
from .trace import RunTrace











_NOTHING_CHANGED = "No layer, feature or file changed"


def _is_nothing_changed(line: str) -> bool:
    return line in (_NOTHING_CHANGED, tr(_NOTHING_CHANGED))


class _Run:
    """What the panel keeps per run: its bubble and its cards."""

    __slots__ = ("run_id", "bubble", "trace", "tools", "permissions", "started", "segment_break",
                 "waited", "wait_started", "blocked_noted")

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.bubble: AgentBubble | None = None
        self.trace: RunTrace | None = None
        self.tools: list = []
        self.permissions: list = []

        self.segment_break = False

        self.started = time.monotonic()

        self.waited = 0.0
        self.wait_started = 0.0

        self.blocked_noted = False


def _wrap(widget: QWidget, margins: tuple, parent=None) -> QWidget:
    """A widget with side margins, so bars align with the list's own."""
    host = QWidget(parent)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(*margins)
    lay.setSpacing(0)
    lay.addWidget(widget)
    return host


def _layer_id_named(name: str) -> str:
    """The id of the project layer called ``name``, or "" outside QGIS."""
    try:
        from qgis.core import QgsProject

        layers = QgsProject.instance().mapLayersByName(name)
    except Exception:  # noqa: BLE001 - no project here
        return ""
    return layers[0].id() if layers else ""


def _turn_divider(parent=None) -> QFrame:
    """The hairline between two turns of a thread."""
    line = QFrame(parent)
    line.setObjectName("turnDivider")
    line.setFrameShape(QFrame.Shape.NoFrame)
    line.setFixedHeight(1)
    return line
