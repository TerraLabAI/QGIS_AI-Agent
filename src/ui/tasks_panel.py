# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

from qgis.PyQt.QtCore import QCoreApplication, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .cards_run import PermissionCard
from .style import (
    _BTN_QUIET,
    FONT_BODY,
    FONT_HINT,
    GREEN,
    INK,
    INK_2,
    INK_3,
    LINE,
    ORANGE,
    RADIUS_CARD,
    RED,
    ROW_PX,
    SURFACE,
)
from .widgets import ChatLabel, ElidedLabel

FINAL = ("done", "failed", "stopped")

MAX_ROWS = 6


def _status_words():
    def tr(text):
        return QCoreApplication.translate("TasksPanel", text)
    return {
        "queued": (tr("Queued"), INK_3),
        "running": (tr("Working"), INK_2),
        "waiting_tool": (tr("Working"), INK_2),
        "paused_offline": (tr("Paused"), ORANGE),
        "done": (tr("Done"), GREEN),
        "failed": (tr("Failed"), RED),
        "stopped": (tr("Stopped"), INK_3),
    }


class _TaskRow(QFrame):
    stop_clicked = pyqtSignal(str)
    decided = pyqtSignal(str, str, object)

    def __init__(self, task_id: str, parent=None):
        super().__init__(parent)
        self.task_id = task_id
        self.status = "queued"
        self.setObjectName("taskRow")
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        self._state = QLabel(self)
        self._state.setFixedWidth(64)
        self._title = ElidedLabel("", self)
        self._title.setStyleSheet(f"color: {INK}; font-size: {FONT_BODY}px; font-weight: 500;")
        self._stop = QPushButton(self.tr("Stop"), self)
        self._stop.setStyleSheet(_BTN_QUIET)
        self._stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop.setFixedHeight(ROW_PX - 4)
        self._stop.clicked.connect(lambda: self.stop_clicked.emit(self.task_id))
        line.addWidget(self._state)
        line.addWidget(self._title, 1)
        line.addWidget(self._stop)
        col.addLayout(line)
        self._note = ElidedLabel("", self)
        self._note.setStyleSheet(f"color: {INK_3}; font-size: {FONT_HINT}px;")
        self._note.setContentsMargins(72, 0, 0, 0)
        col.addWidget(self._note)
        self._summary = ChatLabel("", self, wrap=True)
        self._summary.setStyleSheet(f"color: {INK_2}; font-size: {FONT_BODY}px;")
        self._summary.setContentsMargins(72, 2, 0, 4)
        self._summary.hide()
        col.addWidget(self._summary)

        self._asks = []
        self._card = None
        self._ask_col = QVBoxLayout()
        self._ask_col.setContentsMargins(0, 2, 0, 4)
        col.addLayout(self._ask_col)
        self.setMinimumHeight(ROW_PX)

        for label in (self._state, self._title, self._note, self._summary):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._title_text = ""

    def show_update(self, title: str, status: str, note: str) -> None:
        self.status = status
        word, colour = _status_words().get(status, (status, INK_2))
        self._state.setText(word)
        self._state.setStyleSheet(f"color: {colour}; font-size: {FONT_HINT}px; font-weight: 500;")
        self._title_text = title
        self._title.setText(title)
        self._note.setText(note if status != "done" else "")
        self._note.setVisible(bool(self._note.text()))
        self._stop.setVisible(status not in FINAL)
        self.setCursor(Qt.CursorShape.PointingHandCursor if self._summary.text() else Qt.CursorShape.ArrowCursor)

    def show_summary(self, summary: str) -> None:
        self._summary.setText(summary)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def ask(self, tool_call_id: str, sentence: str, name: str = "", args=None, addresses=None, grant: str = "",
            preview=None) -> None:
        self._asks.append((tool_call_id, sentence, name, dict(args or {}), addresses, grant, preview))
        self._show_ask()

    def _show_ask(self) -> None:
        if self._card is not None or not self._asks:
            return
        tool_call_id, sentence, name, args, addresses, grant, preview = self._asks.pop(0)
        self._card = PermissionCard(tool_call_id, sentence, args, self, name=name, addresses=addresses,
                                    grant=grant, preview=preview)
        self._card.decided.connect(self._answer)
        self._ask_col.addWidget(self._card)

    def _drop_card(self) -> None:
        if self._card is not None:
            self._ask_col.removeWidget(self._card)
            self._card.deleteLater()
            self._card = None

    def _answer(self, tool_call_id: str, decision: str, edited) -> None:
        self._drop_card()
        self.decided.emit(tool_call_id, decision, edited)
        self._show_ask()

    def drop_asks(self) -> None:
        self._asks = []
        self._drop_card()

    def mousePressEvent(self, event):  # noqa: N802
        event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self._summary.text():
            self._summary.setVisible(not self._summary.isVisible())
        super().mouseReleaseEvent(event)


class TasksPanel(QFrame):


    stop_requested = pyqtSignal(str)
    permission_decided = pyqtSignal(str, str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("tasksPanel")
        self.setStyleSheet(
            f"QFrame#tasksPanel {{ background: {SURFACE}; border: 1px solid {LINE};"
            f" border-radius: {RADIUS_CARD}px; }}"
            "QFrame#taskRow { background: transparent; border: none; }")
        self._col = QVBoxLayout(self)
        self._col.setContentsMargins(12, 8, 8, 8)
        self._col.setSpacing(4)
        head = QLabel(self.tr("Tasks"), self)
        head.setStyleSheet(f"color: {INK_2}; font-size: {FONT_HINT}px; font-weight: 500; border: none;")
        self._col.addWidget(head)
        self._rows = {}
        self.hide()

    def task_update(self, frame: dict) -> None:
        task_id = str(frame.get("task_id") or "")
        if not task_id:
            return
        row = self._rows.get(task_id)
        if row is None:
            row = _TaskRow(task_id, self)
            row.stop_clicked.connect(self._stop)
            row.decided.connect(self.permission_decided)
            self._rows[task_id] = row
            self._col.addWidget(row)
            self._trim()
        row.show_update(str(frame.get("title") or ""), str(frame.get("status") or ""), str(frame.get("note") or ""))
        if row.status in FINAL:
            row.drop_asks()
        self.show()

    def ask_permission(self, task_id: str, tool_call_id: str, sentence: str, name: str = "", args=None,
                       addresses=None, grant: str = "", preview=None) -> bool:

        row = self._rows.get(task_id)
        if row is None:
            return False
        row.ask(tool_call_id, sentence, name, args, addresses, grant, preview)
        return True

    def task_result(self, frame: dict) -> None:
        row = self._rows.get(str(frame.get("task_id") or ""))
        if row is not None:
            row.show_summary(str(frame.get("summary") or ""))

    def is_live(self, task_id: str) -> bool:
        row = self._rows.get(task_id)
        return row is not None and row.status not in FINAL

    def _stop(self, task_id: str) -> None:
        row = self._rows.get(task_id)
        if row is not None:
            row.show_update(row._title_text, row.status, self.tr("Stopping..."))
        self.stop_requested.emit(task_id)

    def _trim(self) -> None:
        ended = [tid for tid, row in self._rows.items() if row.status in FINAL]
        while len(self._rows) > MAX_ROWS and ended:
            row = self._rows.pop(ended.pop(0))
            self._col.removeWidget(row)
            row.deleteLater()
