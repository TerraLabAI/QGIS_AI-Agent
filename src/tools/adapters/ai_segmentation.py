# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""AI Segmentation adapter (stub)."""




from __future__ import annotations

from typing import Any

from .._widgets import AI_SEGMENT_KEYS
from .base import PluginAdapter


class AiSegmentationAdapter(PluginAdapter):
    keys = AI_SEGMENT_KEYS
    aliases = ("ai-segmentation", "ai-segment", "aisegmentation", "ai segmentation")

    def display_name(self) -> str:
        return "AI Segmentation"

    def log_tags(self) -> tuple[str, ...]:
        return ("AI Segmentation",)

    def flows(self) -> dict[str, str]:
        return {}



    def current_zone(self) -> tuple[Any, Any] | None:
        """The zone a detection would sweep right now: ``(geometry, crs)`` or None."""









        _, plugin = self.find_instance()
        if plugin is None:
            return None
        try:
            from qgis.core import QgsGeometry
        except ImportError:  # pragma: no cover - QGIS is always there in the plugin
            return None
        rect = getattr(plugin, "_auto_zone", None)
        polygon = getattr(plugin, "_auto_zone_polygon", None)
        geom = None
        if polygon is not None:
            try:
                geom = QgsGeometry(polygon)
            except Exception:  # noqa: BLE001 - a plugin's internals are not a contract
                geom = None
        if geom is None and rect is not None:
            try:
                geom = QgsGeometry.fromRect(rect)
            except Exception:  # noqa: BLE001
                geom = None
        if geom is None:
            return None
        crs = None
        source = getattr(plugin, "_zone_source_crs", None)
        if callable(source) and rect is not None:
            try:
                crs = source(rect)
            except Exception:  # noqa: BLE001
                crs = None
        return geom, crs
