# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QHBoxLayout, QPushButton, QWidget

from .settings_pages import GHOST_BTN_QSS, Page, SectionCard


class ProjectMemoryPageMixin:
    def _build_project_memory(self, page: Page) -> None:
        page.add_group_title(self.tr("Project memory"))
        mem = SectionCard(page)
        page.add(mem)
        buttons = QWidget(mem)
        blay = QHBoxLayout(buttons)
        blay.setContentsMargins(0, 0, 0, 0)
        blay.setSpacing(8)
        for label, action in ((self.tr("Show project memory"), "show"),
                              (self.tr("Clear project memory"), "clear")):
            button = QPushButton(label, buttons)
            button.setObjectName(f"projectMemory_{action}")
            button.setStyleSheet(GHOST_BTN_QSS)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAutoDefault(False)
            button.clicked.connect(lambda _checked=False, a=action: self.project_memory_requested.emit(a))
            blay.addWidget(button, 0)
        blay.addStretch(1)
        mem.add(buttons)
