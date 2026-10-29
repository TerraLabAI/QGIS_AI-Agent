# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

from typing import Any

from qgis.utils import plugins

from .._widgets import plugin_candidates, plugin_widgets


class PluginAdapter:


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

    def root_widgets(self):
        _, inst = self.find_instance()
        return plugin_widgets(inst) if inst is not None else []

    def snapshot_roots(self):
        return self.root_widgets()


class GenericAdapter(PluginAdapter):





    def __init__(self, name: str):
        self._name = name

    def display_name(self) -> str:
        key, _ = self.find_instance()
        return key or self._name
