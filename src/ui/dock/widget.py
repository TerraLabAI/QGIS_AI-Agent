# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later










from __future__ import annotations

from qgis.gui import QgsDockWidget
from qgis.PyQt.QtCore import Qt

from ..chat_panel import ChatPanel

DOCK_OBJECT_NAME = "AIAgentDock"


class AIAgentDock(QgsDockWidget):


    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("AI Agent"))
        self.setObjectName(DOCK_OBJECT_NAME)
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.panel = ChatPanel(self)
        self.setWidget(self.panel)
        header = self.panel.detach_header()
        header.set_dock(self)
        self.setTitleBarWidget(header)
        self.setAcceptDrops(True)



    def dragEnterEvent(self, event):  # noqa: N802
        self.panel.dragEnterEvent(event)

    def dragMoveEvent(self, event):  # noqa: N802
        self.panel.dragMoveEvent(event)

    def dragLeaveEvent(self, event):  # noqa: N802
        self.panel.dragLeaveEvent(event)

    def dropEvent(self, event):  # noqa: N802
        self.panel.dropEvent(event)

    def cleanup(self) -> None:

        try:
            self.panel.cleanup()
        except (RuntimeError, AttributeError):
            pass
