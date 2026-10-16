# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later









from __future__ import annotations

from qgis.core import QgsLocatorFilter, QgsLocatorResult
from qgis.PyQt.QtCore import QCoreApplication

from ..core.logger import log_warning
from ..core.qt_compat import enum_member


def tr(text: str) -> str:
    return QCoreApplication.translate("AIAgentLocatorFilter", text)


class AIAgentLocatorFilter(QgsLocatorFilter):


    PREFIX = "ai"

    def __init__(self, open_panel, prefill, parent=None):
        super().__init__(parent)
        self._open_panel = open_panel
        self._prefill = prefill

    def clone(self):
        return AIAgentLocatorFilter(self._open_panel, self._prefill)

    def name(self):
        return "ai_agent"

    def displayName(self):
        return tr("Ask AI Agent")

    def prefix(self):
        return self.PREFIX

    def priority(self):
        return enum_member(QgsLocatorFilter, "Priority", "Low")

    def flags(self):
        return enum_member(QgsLocatorFilter, "Flag", "FlagFast")

    def fetchResults(self, string, _context, _feedback):
        text = (string or "").strip()
        if not text:
            return
        result = QgsLocatorResult()
        result.filter = self
        result.displayString = tr("Ask AI Agent: {text}").format(text=text)
        result.description = tr("Opens the panel with this question ready to send")
        result.userData = text
        result.score = 1.0
        self.resultFetched.emit(result)

    def triggerResult(self, result):
        text = str(getattr(result, "userData", "") or "").strip()
        if not text:
            return
        try:
            self._open_panel()
            self._prefill(text)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Locator hand-off failed: {exc}")


def register(iface, open_panel, prefill):

    if not hasattr(iface, "registerLocatorFilter"):
        return None
    locator_filter = AIAgentLocatorFilter(open_panel, prefill)
    try:
        iface.registerLocatorFilter(locator_filter)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Locator filter unavailable: {exc}")
        return None
    return locator_filter


def deregister(iface, locator_filter) -> None:
    if locator_filter is None:
        return
    try:
        iface.deregisterLocatorFilter(locator_filter)
    except Exception:  # nosec B110
        pass
