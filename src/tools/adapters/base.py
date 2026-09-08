# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Per-plugin debug adapters."""







from __future__ import annotations

from typing import Any

from qgis.utils import plugins

from .._widgets import (
    plugin_candidates,
    plugin_widgets,
    widget_summary,
)


class PluginAdapter:
    """Base adapter. Subclasses override what differs; defaults cover the rest."""


    keys: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()

    def display_name(self) -> str:
        return self.keys[0] if self.keys else "plugin"



    def candidate_keys(self) -> list[str]:
        if self.keys:
            return list(self.keys)

        return plugin_candidates(getattr(self, "_name", "") or self.display_name())

    def find_instance(self) -> tuple[str | None, Any]:
        for key in self.candidate_keys():
            inst = plugins.get(key)
            if inst is not None:
                return key, inst
        return None, None

    def is_loaded(self) -> bool:
        _, inst = self.find_instance()
        return inst is not None

    def api(self):
        _, inst = self.find_instance()
        return getattr(inst, "mcp_api", None) if inst is not None else None



    def root_widgets(self):
        _, inst = self.find_instance()
        return plugin_widgets(inst) if inst is not None else []

    def snapshot_roots(self):
        return self.root_widgets()



    def state(self) -> dict:
        """Sanitized, secret-free state. Subclasses add domain fields."""
        key, inst = self.find_instance()
        if inst is None:
            return {"available": False, "plugin": self.display_name()}
        roots = self.root_widgets()
        state: dict[str, Any] = {
            "available": True,
            "plugin": key,
            "class": inst.__class__.__name__,
            "docks": [
                {"text": widget_summary(w)["text"], "visible": w.isVisible()}
                for w in roots
            ],
        }
        api = self.api()
        if api is not None and hasattr(api, "get_status"):
            try:
                status = api.get_status()
                if isinstance(status, dict):
                    state["status"] = status
            except Exception as err:
                state["status_error"] = str(err)
        return state



    def reset(self, scenario: str | None) -> dict:
        return {"_error": f"{self.display_name()} has no reset scenarios"}

    def seed(self, scenario: str | None) -> dict:
        return {"_error": f"{self.display_name()} has no seed scenarios"}

    def flows(self) -> dict[str, str]:
        return {}

    def run_flow(self, name: str, params: dict) -> dict:
        return {"_error": f"Unknown flow '{name}'", "available_flows": self.flows()}

    def log_tags(self) -> tuple[str, ...]:
        return ()


class GenericAdapter(PluginAdapter):
    """Fallback adapter for any plugin without a dedicated adapter."""




    def __init__(self, name: str):
        self._name = name

    def display_name(self) -> str:
        key, _ = self.find_instance()
        return key or self._name
