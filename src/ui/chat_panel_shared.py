# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

import time

from qgis.PyQt.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .bubbles import AgentBubble
from .shared import tr
from .trace import RunTrace











_NOTHING_CHANGED = "No layer, feature or file changed"


def _is_nothing_changed(line: str) -> bool:
    return line in (_NOTHING_CHANGED, tr(_NOTHING_CHANGED))


class _Run:


    __slots__ = ("run_id", "bubble", "trace", "plan", "tools", "permissions", "started",
                 "segment_break", "waited", "wait_started")

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.bubble: AgentBubble | None = None
        self.trace: RunTrace | None = None

        self.plan = None
        self.tools: list = []
        self.permissions: list = []

        self.segment_break = False

        self.started = time.monotonic()

        self.waited = 0.0
        self.wait_started = 0.0


def _wrap(widget: QWidget, margins: tuple, parent=None) -> QWidget:

    host = QWidget(parent)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(*margins)
    lay.setSpacing(0)
    lay.addWidget(widget)
    return host


def _turn_divider(parent=None) -> QFrame:

    line = QFrame(parent)
    line.setObjectName("turnDivider")
    line.setFrameShape(QFrame.Shape.NoFrame)
    line.setFixedHeight(1)
    return line


class CompactionDivider(QWidget):





    def __init__(self, label: str, tip: str, parent=None):
        super().__init__(parent)
        self.setObjectName("compactionDivider")
        self.setToolTip(tip)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 12, 0, 12)
        row.setSpacing(10)
        self.label = QLabel(label, self)
        self.label.setObjectName("compactionLabel")
        self.label.setToolTip(tip)
        row.addWidget(self._rule(), 1)
        row.addWidget(self.label, 0)
        row.addWidget(self._rule(), 1)

    def _rule(self) -> QFrame:

        line = QFrame(self)
        line.setObjectName("compactionRule")
        line.setFrameShape(QFrame.Shape.NoFrame)
        line.setFixedHeight(1)
        return line
