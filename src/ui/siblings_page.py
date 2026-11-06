# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later












from __future__ import annotations

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from ..core.qgis_plugins import plugin_logo
from ..core.siblings import is_sibling_installed, sibling_info
from .cross_plugin_discovery import open_sibling
from .font_scale import scale_px_length
from .icons import pixmap_for
from .logo_tile import logo_pixmap
from .settings_pages import (
    GHOST_BTN_QSS,
    GROUP_TITLE_QSS,
    PRIMARY_BTN_QSS,
    ROW_NOTE_QSS,
    ROW_TITLE_QSS,
    SettingGroup,
)
from .style import GREEN

_LOGO_PX = 32


class SiblingRow(QFrame):


    install_requested = pyqtSignal(str)

    def __init__(self, product_id: str, title: str, note: str, parent=None):
        super().__init__(parent)
        self.product_id = str(product_id)
        sibling = sibling_info(self.product_id) or {}
        self.setObjectName("settingsRow")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        line = QHBoxLayout(self)
        line.setContentsMargins(14, 10, 14, 10)
        line.setSpacing(12)


        logo_path = plugin_logo(str(sibling.get("folder") or ""))
        chip = logo_pixmap(logo_path, scale_px_length(_LOGO_PX),
                           self.devicePixelRatioF()) if logo_path else None
        if chip is not None:
            mark = QLabel(self)
            mark.setPixmap(chip)
            line.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(2)
        name = QLabel(title, self)
        name.setStyleSheet(ROW_TITLE_QSS)
        words.addWidget(name)
        body = QLabel(note, self)
        body.setStyleSheet(ROW_NOTE_QSS)
        body.setWordWrap(True)
        words.addWidget(body)
        line.addLayout(words, 1)
        self._check = QLabel(self)
        self._check.setPixmap(pixmap_for(self, "check", 12, QColor(GREEN)))
        line.addWidget(self._check, 0, Qt.AlignmentFlag.AlignVCenter)
        self._state = QLabel(self.tr("Installed"), self)
        self._state.setStyleSheet(ROW_NOTE_QSS)
        line.addWidget(self._state, 0, Qt.AlignmentFlag.AlignVCenter)
        line.addSpacing(4)
        self._action = QPushButton(self)
        self._action.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action.setAutoDefault(False)
        self._action.clicked.connect(lambda: self.install_requested.emit(self.product_id))
        line.addWidget(self._action, 0, Qt.AlignmentFlag.AlignVCenter)
        self.refresh()

    def refresh(self) -> None:

        installed = is_sibling_installed(self.product_id)
        self._action.setText(self.tr("Open") if installed else self.tr("Install"))
        self._action.setStyleSheet(GHOST_BTN_QSS if installed else PRIMARY_BTN_QSS)
        self._action.setToolTip(
            self.tr("Show the plugin's panel.") if installed
            else self.tr("Opens the QGIS plugin manager on this plugin."))
        self._check.setVisible(installed)
        self._state.setVisible(installed)


class SiblingsSection(QWidget):


    opened = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 12, 0, 0)
        col.setSpacing(8)
        head = QLabel(self.tr("Other TerraLab plugins"), self)
        head.setStyleSheet(GROUP_TITLE_QSS)
        head.setContentsMargins(14, 0, 0, 0)
        col.addWidget(head)
        group = SettingGroup(self)
        self._rows = [
            SiblingRow("ai-edit", "AI Edit",
                       self.tr("Edit your imagery with a single sentence."), group),
            SiblingRow("ai-segmentation", "AI Segmentation",
                       self.tr("Turn buildings, trees or water into polygons."), group),
        ]
        for row in self._rows:
            row.install_requested.connect(self._on_install)
            group.add_row(row)
        col.addWidget(group)

    def refresh(self) -> None:
        for row in self._rows:
            row.refresh()

    def _on_install(self, product_id: str) -> None:
        outcome = open_sibling(product_id)
        self.opened.emit(product_id, outcome)


        self.refresh()
