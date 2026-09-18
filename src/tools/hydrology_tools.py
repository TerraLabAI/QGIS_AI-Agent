# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
# ruff: noqa: F401 - this facade re-exports the names its callers import from here



"""The catchment upstream of a point, from a DEM, with nothing but numpy."""































from __future__ import annotations

from ..core.tool_registry import Tool, ToolRegistry
from .hydrology_layers import (
    _resolve_outlet,
)
from .hydrology_terrain import (
    _CHAIN_MAX_MARGIN_KM,
    _MAX_ORDER,
    _MAX_RADIUS_KM,
    _d8_downstream,
    _strahler_network,
)
from .hydrology_worker import (
    _delineate_watershed,
    _extract_stream_network,
    _map_drainage,
)


def register_hydrology_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="delineate_watershed",
        input_schema={
            "type": "object",
            "properties": {
                "dem": {"type": "string"},
                "outlet": {
                    "type": "object",
                    "properties": {
                        "lon": {"type": "number", "minimum": -180, "maximum": 180},
                        "lat": {"type": "number", "minimum": -90, "maximum": 90},
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "crs": {"type": "string"},
                        "layer_name": {"type": "string"},
                        "feature_id": {"type": "integer"},
                        "wkt": {"type": "string"},
                    },
                },
                "radius_km": {"type": "number", "minimum": 0.5, "maximum": _MAX_RADIUS_KM},
                "snap_m": {"type": "number", "minimum": 0, "maximum": 5000},
                "name": {"type": "string"},
            },
            "required": ["dem", "outlet"],
        },
        handler=_delineate_watershed,
        background=True,
    ))
    registry.register(Tool(
        name="extract_stream_network",
        input_schema={
            "type": "object",
            "properties": {
                "dem": {"type": "string"},
                "outlet": {
                    "type": "object",
                    "properties": {
                        "lon": {"type": "number", "minimum": -180, "maximum": 180},
                        "lat": {"type": "number", "minimum": -90, "maximum": 90},
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "crs": {"type": "string"},
                        "layer_name": {"type": "string"},
                        "feature_id": {"type": "integer"},
                        "wkt": {"type": "string"},
                    },
                },
                "mask_layer": {"type": "string"},
                "radius_km": {"type": "number", "minimum": 0.5, "maximum": _MAX_RADIUS_KM},
                "snap_m": {"type": "number", "minimum": 0, "maximum": 5000},
                "threshold_cells": {"type": "integer", "minimum": 2},
                "threshold_km2": {"type": "number", "minimum": 0.0001},
                "min_order": {"type": "integer", "minimum": 1, "maximum": _MAX_ORDER},
                "longest_flow_path": {"type": "boolean"},
                "name": {"type": "string"},
            },
            "required": ["dem"],
        },
        handler=_extract_stream_network,
        background=True,
    ))
    registry.register(Tool(
        name="map_drainage",
        input_schema={
            "type": "object",
            "properties": {
                "dem": {"type": "string"},
                "area": {"type": "string"},
                "bbox": {
                    "type": "object",
                    "properties": {
                        "xmin": {"type": "number", "minimum": -180, "maximum": 180},
                        "ymin": {"type": "number", "minimum": -90, "maximum": 90},
                        "xmax": {"type": "number", "minimum": -180, "maximum": 180},
                        "ymax": {"type": "number", "minimum": -90, "maximum": 90},
                    },
                },
                "outlet": {
                    "type": "object",
                    "properties": {
                        "lon": {"type": "number", "minimum": -180, "maximum": 180},
                        "lat": {"type": "number", "minimum": -90, "maximum": 90},
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "crs": {"type": "string"},
                        "layer_name": {"type": "string"},
                        "feature_id": {"type": "integer"},
                        "wkt": {"type": "string"},
                    },
                },
                "snap_m": {"type": "number", "minimum": 0, "maximum": 5000},
                "margin_km": {"type": "number", "minimum": 0, "maximum": _CHAIN_MAX_MARGIN_KM},
                "threshold_km2": {"type": "number", "minimum": 0.0001},
                "name": {"type": "string"},
            },
            "required": ["dem"],
        },
        handler=_map_drainage,
        background=True,
    ))




__all__ = [
    "register_hydrology_tools",
    "_d8_downstream",
    "_resolve_outlet",
    "_strahler_network",
]
