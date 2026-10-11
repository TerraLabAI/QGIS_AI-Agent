# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

import time

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from .bubbles import AgentBubble
from .font_scale import scale_qss_font_px
from .shared import tr
from .style import FONT_HINT, INK, INK_3
from .trace import RunTrace











_NOTHING_CHANGED = "No layer, feature or file changed"


def _is_nothing_changed(line: str) -> bool:
    return line in (_NOTHING_CHANGED, tr(_NOTHING_CHANGED))


class _Run:


    __slots__ = ("run_id", "bubble", "trace", "plan", "tools", "permissions", "started",
                 "segment_break", "produced")

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.bubble: AgentBubble | None = None
        self.trace: RunTrace | None = None

        self.plan = None
        self.tools: list = []
        self.permissions: list = []

        self.segment_break = False

        self.started = time.monotonic()

        self.produced = False


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


class RestoreDivider(CompactionDivider):









    redo_requested = pyqtSignal()
    _STUB = 12

    def __init__(self, text: str, link: str = "", parent=None):
        super().__init__("", "", parent)
        self.setObjectName("restoreDivider")
        self.key = None
        self._full = ""
        label = self.label
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(False)
        label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self._dot = QLabel("\u00b7", self)
        self._dot.setObjectName("compactionLabel")
        self._link = QPushButton(self)
        self._link.setObjectName("restoreDividerLink")
        self._link.setCursor(Qt.CursorShape.PointingHandCursor)
        self._link.setFlat(True)
        self._link.setStyleSheet(scale_qss_font_px(
            f"QPushButton#restoreDividerLink {{ background: transparent; border: none; padding: 0;"
            f" color: {INK}; font-size: {FONT_HINT}px; text-decoration: underline; }}"
            f"QPushButton#restoreDividerLink:disabled {{ color: {INK_3}; text-decoration: none; }}"))
        self._link.clicked.connect(self.redo_requested.emit)
        row = self.layout()
        for rule in self.findChildren(QFrame, "compactionRule"):
            rule.setMinimumWidth(self._STUB)
        at = row.indexOf(label)
        row.insertWidget(at + 1, self._dot, 0)
        row.insertWidget(at + 2, self._link, 0)
        row.setSpacing(6)
        self.set_text(text, link)

    def set_text(self, text: str, link: str = "") -> None:
        self._full = str(text)
        self._link.setText(str(link or ""))
        self._link.setVisible(bool(link))
        self._dot.setVisible(bool(link))
        self._elide()

    def set_link_enabled(self, enabled: bool) -> None:

        self._link.setEnabled(bool(enabled))
        self._link.setToolTip("" if enabled else tr("Available when the agent finishes"))

    def link_enabled(self) -> bool:
        return self._link.isVisible() and self._link.isEnabled()

    def _elide(self) -> None:
        label = self.label
        label.ensurePolished()
        metrics = label.fontMetrics()
        row = self.layout()
        tail = 0
        if not self._link.isHidden():
            self._link.ensurePolished()
            tail = (self._link.sizeHint().width() + metrics.horizontalAdvance("\u00b7")
                    + 2 * row.spacing())
        room = max(0, self.width() - 2 * (self._STUB + row.spacing()) - tail) if self.width() > 0 else 10 ** 6
        shown = metrics.elidedText(self._full, Qt.TextElideMode.ElideRight, room)
        label.setText(shown)
        label.setToolTip(self._full if shown != self._full else "")
        label.setFixedWidth(min(room, metrics.horizontalAdvance(shown) + 2))

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def text(self) -> str:
        link = self._link.text() if not self._link.isHidden() else ""
        return f"{self._full} \u00b7 {link}" if link else self._full
