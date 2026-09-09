# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""An "AI Agent" page in QGIS Options (Settings > Options), where users look for settings."""







from __future__ import annotations

from qgis.gui import QgsOptionsPageWidget, QgsOptionsWidgetFactory
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..core.logger import log_warning


def tr(text: str) -> str:
    return QCoreApplication.translate("AIAgentOptionsPage", text)


class AIAgentOptionsPage(QgsOptionsPageWidget):
    def __init__(self, parent, open_panel, open_settings):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        intro = QLabel(tr("AI Agent by TerraLab drives QGIS in plain language: load data, style layers, run "
                          "analyses, build layouts. It asks before risky changes and you can undo a run."))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        hints = QLabel(tr('Open the panel with Ctrl+Alt+A, or type "ai" followed by a question in the locator '
                          "bar (Ctrl+K). Right-click a layer, a feature or the map to ask about it."))
        hints.setWordWrap(True)
        layout.addWidget(hints)
        buttons = QHBoxLayout()
        panel_button = QPushButton(tr("Open AI Agent"))
        panel_button.clicked.connect(lambda _checked=False: open_panel())
        settings_button = QPushButton(tr("AI Agent settings..."))
        settings_button.clicked.connect(lambda _checked=False: open_settings())
        buttons.addWidget(panel_button)
        buttons.addWidget(settings_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(1)

    def apply(self):  # noqa: D102 - nothing to save: the page holds no setting
        return None


class AIAgentOptionsFactory(QgsOptionsWidgetFactory):
    def __init__(self, icon: QIcon, open_panel, open_settings):
        super().__init__(tr("AI Agent"), icon)
        self._open_panel = open_panel
        self._open_settings = open_settings

    def createWidget(self, parent):
        return AIAgentOptionsPage(parent, self._open_panel, self._open_settings)


def register(iface, icon: QIcon, open_panel, open_settings):
    """Register the page; None when this QGIS has no options factories (tests)."""
    if not hasattr(iface, "registerOptionsWidgetFactory"):
        return None
    factory = AIAgentOptionsFactory(icon, open_panel, open_settings)
    try:
        iface.registerOptionsWidgetFactory(factory)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Options page unavailable: {exc}")
        return None
    return factory


def deregister(iface, factory) -> None:
    if factory is None:
        return
    try:
        iface.unregisterOptionsWidgetFactory(factory)
    except Exception:  # nosec B110 - QGIS may already have dropped it
        pass
