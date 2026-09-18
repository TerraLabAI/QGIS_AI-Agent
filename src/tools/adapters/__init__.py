# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Adapter registry + resolution for the plugin-debug subsystem."""
from __future__ import annotations

from .base import GenericAdapter, PluginAdapter

_REGISTRY: list[PluginAdapter] = []
_builtins: dict = {"loaded": False}


def register_adapter(adapter: PluginAdapter):
    _REGISTRY.append(adapter)


def _load_builtins():
    if _builtins["loaded"]:
        return
    _builtins["loaded"] = True



    from .ai_edit import _ADAPTER as ai_edit_adapter
    from .ai_segmentation import AiSegmentationAdapter

    register_adapter(ai_edit_adapter)
    register_adapter(AiSegmentationAdapter())


def all_adapters() -> list[PluginAdapter]:
    _load_builtins()
    return list(_REGISTRY)


def _normalize(text: str) -> str:
    return (text or "").strip().lower().replace("_", "-")


def get_adapter(name: str) -> PluginAdapter:
    """Resolve an adapter by alias or plugin key; fall back to GenericAdapter."""
    _load_builtins()
    target = _normalize(name)
    if target:
        for adapter in _REGISTRY:
            if target in (_normalize(a) for a in adapter.aliases):
                return adapter
            if target in (_normalize(k) for k in adapter.keys):
                return adapter

            if any(target in _normalize(k) for k in adapter.keys):
                return adapter
    return GenericAdapter(name)
