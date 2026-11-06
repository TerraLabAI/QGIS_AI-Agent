# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

from ..core import protocol
from ..core.project_identity import project_key
from .controller_shared import tr


def _message_box():
    from qgis.PyQt.QtWidgets import QApplication, QMessageBox
    return QApplication.activeWindow(), QMessageBox


class _ControllerMemory:
    def _memory_init(self) -> None:
        if hasattr(self._session, "project_memory"):
            self._session.project_memory.connect(self._on_project_memory)

    def request_project_memory(self, action: str) -> None:

        if action not in ("show", "clear"):
            return
        parent, box = _message_box()
        key = project_key()
        if not key:
            box.information(parent, tr("Project memory"),
                            tr("Save the project first: its memory is kept under the project file."))
            return
        if action == "clear" and box.question(
                parent, tr("Project memory"),
                tr("Clear what the AI agent remembers about this project?")) != box.StandardButton.Yes:
            return
        if not (self._session.is_online and self._session.send_frame(protocol.project_memory_request(action, key))):
            box.information(parent, tr("Project memory"),
                            tr("The AI agent is not connected. Try again in a moment."))

    def _on_project_memory(self, frame: dict) -> None:
        parent, box = _message_box()
        text = str(frame.get("text") or "") or (tr("Project memory cleared.") if frame.get("cleared")
                                                 else tr("Nothing is kept about this project yet."))
        box.information(parent, tr("Project memory"), text)
