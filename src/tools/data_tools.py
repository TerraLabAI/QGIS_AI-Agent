# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
# ruff: noqa: F401


from __future__ import annotations

from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core import tuning
from ..core.tool_registry import Tool, ToolRegistry
from . import volume_guard
from .data_basemaps import (
    _add_xyz_layer,
    _list_xyz_sources,
    add_xyz_runs_in_background,
)
from .data_common import (
    _OSRM_PROFILES,
    _USER_AGENT,
    _WINDOWS_FORBIDDEN_CHARS,
    _WINDOWS_RESERVED_NAMES,
    _avoid_reserved_name,
    _canvas_viewbox_4326,
    _safe_filename,
)
from .data_geocoding import (
    _BACKEND_GEOCODE_BATCH_MAX,
    _GEOCODE_PROVIDER_IDS,
    _geocode,
    _geocode_one_address,
    _get_route,
    _measure_distance,
    _reverse_geocode,
)
from .data_inspect import (
    _add_vector_from_url,
    _add_vector_over_range_requests,
    _extract_remote_vector,
    _inspect_data_source,
    _inspect_is_remote,
    expand_link,
)
from .data_ogc import (
    _add_wfs_layer,
    _add_wms_layer,
)
from .data_osm import (
    _FOOTPRINT_SOURCE_INPUTS,
    _FOOTPRINT_SOURCES,
    _fetch_building_footprints,
    _fetch_osm_data,
    _fetch_osm_data_preflight,
)
from .data_overture import (
    _overture_tiles,
)
from .data_overture_extract import (
    _fetch_overture,
    _fetch_overture_preflight,
)
from .data_portals import (
    _search_open_data,
)


def _reads_what_fits(name: str, handler):















    def read(args, *rest, **kwargs):
        clamped = volume_guard.clamp_to_cap(name, args) if isinstance(args, dict) else {}
        out = handler(args, *rest, **kwargs)
        if clamped and isinstance(out, dict) and not out.get("_error"):
            out["bbox_read"] = clamped["bbox"]
            out["box_km2"] = clamped["box_km2"]
            out["asked_km2"] = clamped["asked_km2"]
            out["_note"] = (out.get("_note") + " " if out.get("_note") else "") + clamped["note"]
        return out

    read.__name__ = getattr(handler, "__name__", name)
    read.__doc__ = getattr(handler, "__doc__", None)
    return read




_OSM_PLANET_THEMES = frozenset(
    ("landuse", "pois", "waterways", "water_areas", "power", "boundaries", "railways", "routes", "transit_stops",
     "protected_areas"))


def _overture_label(args: dict) -> str:
    if str(args.get("theme") or "").strip().lower() in _OSM_PLANET_THEMES:
        return QT_TRANSLATE_NOOP("AIAgent", "Fetch OpenStreetMap {theme}[ as {layer_name}]")
    return ""


def register_data_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="geocode",
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Find {query} on the map"),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                },
                "country_codes": {
                    "type": "string",
                },
                "limit": {"type": "integer"},
                "verbose": {"type": "boolean"},
                "layer_name": {
                    "type": "string",
                },
                "provider": {
                    "type": "string",
                    "enum": list(_GEOCODE_PROVIDER_IDS),
                },
                "endpoint": {
                    "type": "string",
                },
            },
            "required": ["query"],
        },
        handler=_geocode,
        background=True,
    ))

    registry.register(Tool(
        name="reverse_geocode",
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Find the address at a point"),
        input_schema={
            "type": "object",
            "properties": {
                "lat": {"type": "number"},
                "lon": {"type": "number"},
                "provider": {
                    "type": "string",
                    "enum": list(_GEOCODE_PROVIDER_IDS),
                },
                "endpoint": {
                    "type": "string",
                },
            },
            "required": ["lat", "lon"],
        },
        handler=_reverse_geocode,
        background=True,
    ))

    registry.register(Tool(
        name="fetch_osm_data",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Fetch OpenStreetMap data[ as {layer_name}]"),
        provider="overpass",
        input_schema={
            "type": "object",
            "properties": {
                "description": {"type": "string", "maxLength": 80},
                "confirm_area_km2": {"type": "number", "minimum": 0},
                "confirm_large": {"type": "boolean"},
                "query": {
                    "type": "string",
                },
                "bbox": {
                    "type": "object",
                    "properties": {
                        "south": {"type": "number"},
                        "west": {"type": "number"},
                        "north": {"type": "number"},
                        "east": {"type": "number"},
                        "xmin": {"type": "number"},
                        "ymin": {"type": "number"},
                        "xmax": {"type": "number"},
                        "ymax": {"type": "number"},
                    },
                },
                "layer_name": {"type": "string"},
                "full_extent": {
                    "type": "object",
                    "properties": {"quote": {"type": "string"}, "place": {"type": "string"}},
                    "required": ["quote", "place"],
                },
            },
            "required": ["query", "bbox"],



            "x-overpass-tiles": True,
        },
        handler=_fetch_osm_data,
        background=True,
        preflight=_fetch_osm_data_preflight,
    ))

    registry.register(Tool(
        name="fetch_building_footprints",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Fetch building footprints[ as {layer_name}]"),
        provider="footprints",
        input_schema={
            "type": "object",
            "properties": {
                "confirm_area_km2": {"type": "number", "minimum": 0},
                "confirm_large": {"type": "boolean"},
                "bbox": {
                    "type": "object",
                    "properties": {
                        "south": {"type": "number"},
                        "west": {"type": "number"},
                        "north": {"type": "number"},
                        "east": {"type": "number"},
                        "xmin": {"type": "number"},
                        "ymin": {"type": "number"},
                        "xmax": {"type": "number"},
                        "ymax": {"type": "number"},
                    },
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(_FOOTPRINT_SOURCE_INPUTS)},
                },
                "layer_name": {"type": "string"},
            },
            "required": ["bbox"],
        },
        handler=_reads_what_fits("fetch_building_footprints", _fetch_building_footprints),
        background=True,
    ))

    registry.register(Tool(
        name="fetch_overture",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Fetch Overture {theme}[ as {layer_name}]"),
        label_for=_overture_label,
        input_schema={
            "type": "object",


            "x-filter-compare": True,
            "properties": {
                "description": {"type": "string", "maxLength": 80},

                "theme": {"type": "string", "enum": []},
                "mode": {"type": "string", "enum": ["clip", "stream"]},
                "clip_to": {"type": "string"},
                "confirm_area_km2": {"type": "number", "minimum": 0},
                "bbox": {
                    "type": "object",
                    "properties": {
                        "south": {"type": "number"},
                        "west": {"type": "number"},
                        "north": {"type": "number"},
                        "east": {"type": "number"},
                        "xmin": {"type": "number"},
                        "ymin": {"type": "number"},
                        "xmax": {"type": "number"},
                        "ymax": {"type": "number"},
                    },
                },
                "filter": {"type": "object"},
                "layer_name": {"type": "string"},
                "full_extent": {
                    "type": "object",
                    "properties": {"quote": {"type": "string"}, "place": {"type": "string"}},
                    "required": ["quote", "place"],
                },
            },
            "required": ["theme"],



            "x-clip-any-layer": True,




            "x-stream-filter-local": True,



            "x-server-subtype": True,
        },




        handler=_fetch_overture,
        background=True,
        preflight=_fetch_overture_preflight,
    ))

    registry.register(Tool(
        name="list_xyz_sources",
        danger="read",
        catalog=True,
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_list_xyz_sources,
    ))

    registry.register(Tool(
        name="add_xyz_layer",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Add the basemap[ {name}]"),
        input_schema={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                },
                "name": {"type": "string"},
            },
            "required": ["source"],
        },
        handler=_add_xyz_layer,

        background=add_xyz_runs_in_background,
    ))

    registry.register(Tool(
        name="add_wms_layer",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Add the WMS layer[ {name}]"),
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "layers": {"type": "string"},
                "name": {"type": "string"},
                "crs": {"type": "string"},
                "format": {"type": "string"},
            },
            "required": ["url", "layers"],
        },
        handler=_add_wms_layer,
        background=True,
    ))

    registry.register(Tool(
        name="add_wfs_layer",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Add the WFS layer[ {name}]"),
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "typename": {"type": "string"},
                "name": {"type": "string"},
                "crs": {"type": "string"},
                "max_features": {"type": "integer", "minimum": 1, "maximum": 250000},


                "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},


                "where": {"type": "string"},


                "full_extent": {
                    "type": "object",
                    "properties": {"quote": {"type": "string"}, "place": {"type": "string"}},
                    "required": ["quote", "place"],
                },
            },
            "required": ["url", "typename"],
        },
        handler=_add_wfs_layer,
        background=True,
    ))

    registry.register(Tool(
        name="add_vector_from_url",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Add {url}[ as {layer_name}]"),
        input_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                },
                "layer_name": {
                    "type": "string",
                },





                "layer": {
                    "type": "string",
                },

                "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},


                "full_extent": {
                    "type": "object",
                    "properties": {"quote": {"type": "string"}, "place": {"type": "string"}},
                    "required": ["quote", "place"],
                },
            },
            "required": ["url"],
        },
        handler=_add_vector_from_url,
        background=True,
    ))

    registry.register(Tool(
        name="get_route",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Compute a route"),
        input_schema={
            "type": "object",
            "properties": {
                "waypoints": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "lon": {"type": "number"},
                            "lat": {"type": "number"},
                        },
                        "required": ["lon", "lat"],
                    },
                    "minItems": 2,
                },
                "profile": {
                    "type": "string",
                    "enum": ["driving", "walking", "cycling"],
                },
                "layer_name": {"type": "string"},
            },
            "required": ["waypoints"],
        },
        handler=_get_route,
        background=True,
    ))

    registry.register(Tool(
        name="measure_distance",
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Measure a distance"),
        input_schema={
            "type": "object",
            "properties": {
                "from_lon": {"type": "number", "minimum": -180, "maximum": 180},
                "from_lat": {"type": "number", "minimum": -90, "maximum": 90},
                "to_lon": {"type": "number", "minimum": -180, "maximum": 180},
                "to_lat": {"type": "number", "minimum": -90, "maximum": 90},
            },
            "required": ["from_lon", "from_lat", "to_lon", "to_lat"],
        },
        handler=_measure_distance,
    ))

    registry.register(Tool(
        name="inspect_data_source",
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Inspect {url}"),
        input_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                },
            },
            "required": ["url"],
        },
        handler=_inspect_data_source,
        background=_inspect_is_remote,
    ))

    registry.register(Tool(
        name="search_open_data",
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Search open data[ for {query}]"),
        catalog=True,
        input_schema={
            "type": "object",
            "properties": {
                "description": {"type": "string", "maxLength": 80},
                "query": {
                    "type": "string",
                },
                "portal": {
                    "type": "string",
                },
                "portal_url": {
                    "type": "string",
                },
                "portal_type": {
                    "type": "string",
                    "enum": ["opendatasoft", "ckan", "socrata"],
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                },
                "format_preference": {
                    "type": "string",









                },
            },
            "required": ["query"],
        },
        handler=_search_open_data,
        background=True,
    ))

    _watch_served_vocabularies(registry)










_ENUM_NODES: list = []


def _watch_served_vocabularies(registry: ToolRegistry) -> None:
    def node(name: str, path: tuple):



        getter = getattr(registry, "get_tool", None)
        tool = getter(name) if callable(getter) else None
        found = getattr(tool, "input_schema", None)
        for step in path:
            found = (found or {}).get(step)
            if not isinstance(found, dict):
                return None
        return found

    wanted = (
        (node("fetch_overture", ("properties", "theme")),
         "overture_themes", (), ()),
        (node("fetch_building_footprints", ("properties", "sources", "items")),
         "footprint_sources", _FOOTPRINT_SOURCES, ("osm",)),
    )
    for schema, key, shipped, aliases in wanted:
        if isinstance(schema, dict) and isinstance(schema.get("enum"), list):
            _ENUM_NODES.append((schema, key, tuple(shipped), tuple(aliases)))
    if _ENUM_NODES:
        tuning.subscribe(_refresh_served_vocabularies)
        _refresh_served_vocabularies()


def _refresh_served_vocabularies() -> None:

    for schema, key, shipped, aliases in _ENUM_NODES:
        schema["enum"] = list(dict.fromkeys((*tuning.service_list(key, shipped), *aliases)))




__all__ = [
    "register_data_tools",
    "_BACKEND_GEOCODE_BATCH_MAX",
    "_OSRM_PROFILES",
    "_USER_AGENT",
    "_WINDOWS_FORBIDDEN_CHARS",
    "_WINDOWS_RESERVED_NAMES",
    "_add_vector_over_range_requests",
    "_avoid_reserved_name",
    "_canvas_viewbox_4326",
    "_extract_remote_vector",
    "_geocode_one_address",
    "_overture_tiles",
    "_safe_filename",
    "expand_link",
    "volume_guard",
]
