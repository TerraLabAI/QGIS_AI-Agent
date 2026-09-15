# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
# ruff: noqa: F401 - this facade re-exports the names its callers import from here



"""The catchment upstream of a point, from a DEM, with nothing but numpy."""































from __future__ import annotations

import heapq
import json
import math
import struct
import time

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsWkbTypes,
)

from ..core import limits, machine, net
from ..core.qt_compat import field_type
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .data_tools import _run_on_main_thread
from .hydrology_layers import (
    _add_drainage_layers,
    _add_stream_layers,
    _add_watershed_layer,
    _dem_facts,
    _ellipsoid_area_km2,
    _finite,
    _guessed_crs,
    _mask_geometry,
    _outlet_error,
    _outlet_from_layer,
    _parse_crs,
    _resolve_outlet,
    _single_point,
    _style_dem,
    _style_streams,
)
from .hydrology_terrain import (
    _BYTES_PER_CELL,
    _CHAIN_MARGIN_SHARE,
    _CHAIN_MAX_MARGIN_KM,
    _CHAIN_MIN_MARGIN_M,
    _CHAIN_TIME_SHARE,
    _CHECK_EVERY,
    _D8,
    _DEFAULT_RADIUS_KM,
    _DEFAULT_SNAP_M,
    _EXIT_CANDIDATES,
    _EXIT_WHY,
    _MAX_ORDER,
    _MAX_RADIUS_KM,
    _MAX_STREAM_SEGMENTS,
    _MEMORY_SHARE,
    _MIN_THRESHOLD_CELLS,
    _MIN_THRESHOLD_M2,
    _MIN_WATER_CELLS,
    _MIN_WATER_M2,
    _OTHER_EXITS,
    _OUTLET_FORMS,
    _SINK_SHARE,
    _THRESHOLD_SHARE,
    _UTM_MAX_LAT,
    _WATER_LEVEL,
    _accumulate,
    _authid,
    _bbox_around,
    _border_nodata,
    _box_geometry,
    _d8_downstream,
    _dilate,
    _envelope,
    _exit_cells,
    _longest_flow_path,
    _open_water,
    _parse_bbox,
    _polygonise,
    _priority_flood,
    _rasterise,
    _segment_rows,
    _sinks,
    _srs,
    _strahler_network,
    _upstream_of,
)
from .hydrology_worker import (
    _cell_cap,
    _cell_metres,
    _clock_refusal,
    _cut_warning,
    _degrees_note,
    _delineate_watershed,
    _drainage,
    _extract_stream_network,
    _map_drainage,
    _outlet_off_land,
    _outlet_summary,
    _snap,
    _Stopped,
    _stopped_answer,
)
from .layer_lookup import _find_layer, _layer_not_found_error


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


__all__ = ["register_hydrology_tools"]
