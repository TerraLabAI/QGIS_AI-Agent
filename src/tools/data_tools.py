# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
# ruff: noqa: F401 - this facade re-exports the names its callers import from here
"""The implementation lives in the data_* sibling modules; this facade keeps the names the rest of the plugin and the tests import from here."""

from __future__ import annotations

from ..core import limits, net, tuning
from ..core.tool_registry import Tool, ToolRegistry
from . import volume_guard
from .data_basemaps import (
    VECTOR_TILE_EXTENSIONS,
    _add_oapif_layer,
    _add_vector_tile_layer,
    _add_xyz_layer,
    _list_xyz_sources,
    _oapif_collection,
    _resolve_tilejson,
    _twin_of,
    add_xyz_runs_in_background,
)
from .data_common import (
    _CACHE_GEOCODE_S,
    _GEOCODE_TIMEOUT,
    _NOMINATIM_URL,
    _OPEN_DATA_PORTALS,
    _OSRM_PROFILES,
    _OWN_GEOCODE_TIMEOUT,
    _USER_AGENT,
    _WINDOWS_FORBIDDEN_CHARS,
    _WINDOWS_RESERVED_NAMES,
    _avoid_reserved_name,
    _bbox_km2,
    _canvas_viewbox_4326,
    _footprint_box,
    _osrm_base,
    _project_crs_transform,
    _run_on_main_thread,
    _safe_filename,
)
from .data_geocoding import (
    _BACKEND_GEOCODE_BATCH_MAX,
    _DEFAULT_GEOCODE_PROVIDER,
    _GEOCODE_PROVIDER_IDS,
    _GEOCODE_PROVIDERS,
    _PHOTON_URL,
    _geocode,
    _geocode_one_address,
    _get_route,
    _measure_distance,
    _parse_cartociudad_forward,
    _parse_nominatim_forward,
    _parse_photon_forward,
    _reverse_geocode,
)
from .data_inspect import (
    _add_vector_from_url,
    _add_vector_over_range_requests,
    _extract_remote_vector,
    _inspect_data_source,
    _inspect_is_remote,
    expand_link,
    hosted_department_url,
)
from .data_ogc import (
    _WFS_CAPS_MAX_BYTES,
    _WFS_MATCHED_RE,
    _WFS_TYPENAME_RE,
    WFS_WARN_FEATURES,
    WFS_WIRE_BYTES_PER_FEATURE,
    _add_wcs_layer,
    _add_wfs_layer,
    _add_wms_layer,
    _wfs_failure,
    _wfs_hits,
    _wfs_restrict_to_view,
)
from .data_osm import (
    _FOOTPRINT_DEFAULT_SOURCES,
    _FOOTPRINT_SOURCE_INPUTS,
    _FOOTPRINT_SOURCES,
    _fetch_building_footprints,
    _fetch_osm_data,
    _fetch_osm_data_preflight,
    _osm_area_refusal,
    _osm_from_hosted,
    _own_overpass_unreachable,
)
from .data_osm_geometry import (
    _geometry_counts,
    _osm_to_geojson,
)
from .data_overture import (
    _OSM_THEMES,
    _OVERTURE_API,
    _OVERTURE_THEMES,
    _OVERTURE_TILES,
    _overture_matches,
    _overture_tile,
    _overture_tile_url,
    _overture_tiles,
)
from .data_overture_extract import (
    _fetch_overture,
    _fetch_overture_preflight,
    _overture_clip,
    _overture_extract,
)
from .data_portals import (
    _hub_resources,
    _normalise_format_preference,
    _parse_arcgis_hub_results,
    _parse_ckan_results,
    _portal_text,
    _search_open_data,
)


def _reads_what_fits(name: str, handler):
    """*handler* with a box only just over its ceiling shrunk back to it."""














    def read(args, *rest, **kwargs):
        clamped = volume_guard.clamp_to_cap(name, args) if isinstance(args, dict) else {}
        out = handler(args, *rest, **kwargs)
        if clamped and isinstance(out, dict) and not out.get("_error"):
            out["bbox_read"] = clamped["bbox"]
            out["area_km2"] = clamped["area_km2"]
            out["asked_km2"] = clamped["asked_km2"]
            out["_note"] = (out.get("_note") + " " if out.get("_note") else "") + clamped["note"]
        return out

    read.__name__ = getattr(handler, "__name__", name)
    read.__doc__ = getattr(handler, "__doc__", None)
    return read


def register_data_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="geocode",
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
        },
        handler=_fetch_osm_data,
        background=True,
        preflight=_fetch_osm_data_preflight,
    ))

    registry.register(Tool(
        name="fetch_building_footprints",
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
        input_schema={
            "type": "object",
            "properties": {
                "description": {"type": "string", "maxLength": 80},
                "theme": {"type": "string", "enum": list(_OVERTURE_THEMES)},
                "mode": {"type": "string", "enum": ["clip", "stream"]},
                "clip_to": {"type": "string"},
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
                "filter": {"type": "object"},
                "layer_name": {"type": "string"},
                "full_extent": {
                    "type": "object",
                    "properties": {"quote": {"type": "string"}, "place": {"type": "string"}},
                    "required": ["quote", "place"],
                },
            },
            "required": ["theme"],
        },
        handler=_reads_what_fits("fetch_overture", _fetch_overture),
        background=True,
        preflight=_reads_what_fits("fetch_overture", _fetch_overture_preflight),
    ))

    registry.register(Tool(
        name="list_xyz_sources",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_list_xyz_sources,
    ))

    registry.register(Tool(
        name="add_xyz_layer",
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
        input_schema={
            "type": "object",
            "properties": {
                "confirm_large": {"type": "boolean"},
                "url": {"type": "string"},
                "typename": {"type": "string"},
                "name": {"type": "string"},
                "crs": {"type": "string"},
                "max_features": {"type": "integer", "minimum": 1, "maximum": 250000},
            },
            "required": ["url", "typename"],
        },
        handler=_add_wfs_layer,
        background=True,
    ))

    registry.register(Tool(
        name="add_vector_from_url",
        input_schema={
            "type": "object",
            "properties": {
                "confirm_large": {"type": "boolean"},
                "url": {
                    "type": "string",
                },
                "layer_name": {
                    "type": "string",
                },





                "layer": {
                    "type": "string",
                },
            },
            "required": ["url"],
        },
        handler=_add_vector_from_url,
        background=True,
    ))

    registry.register(Tool(
        name="get_route",
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



    from .road_matching import register_road_matching

    register_road_matching(registry)

    registry.register(Tool(
        name="measure_distance",
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
         "overture_themes", _OVERTURE_THEMES, ()),
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
    """Put the served vocabulary into the registered schemas. Socket thread."""
    for schema, key, shipped, aliases in _ENUM_NODES:
        schema["enum"] = list(dict.fromkeys((*tuning.service_list(key, shipped), *aliases)))




__all__ = [
    "register_data_tools",
    "VECTOR_TILE_EXTENSIONS",
    "WFS_WARN_FEATURES",
    "WFS_WIRE_BYTES_PER_FEATURE",
    "_BACKEND_GEOCODE_BATCH_MAX",
    "_CACHE_GEOCODE_S",
    "_DEFAULT_GEOCODE_PROVIDER",
    "_FOOTPRINT_DEFAULT_SOURCES",
    "_GEOCODE_PROVIDERS",
    "_GEOCODE_TIMEOUT",
    "_NOMINATIM_URL",
    "_OPEN_DATA_PORTALS",
    "_OSM_THEMES",
    "_OSRM_PROFILES",
    "_OVERTURE_API",
    "_OVERTURE_TILES",
    "_OWN_GEOCODE_TIMEOUT",
    "_PHOTON_URL",
    "_USER_AGENT",
    "_WFS_CAPS_MAX_BYTES",
    "_WFS_MATCHED_RE",
    "_WFS_TYPENAME_RE",
    "_WINDOWS_FORBIDDEN_CHARS",
    "_WINDOWS_RESERVED_NAMES",
    "_add_oapif_layer",
    "_add_vector_over_range_requests",
    "_add_vector_tile_layer",
    "_add_wcs_layer",
    "_avoid_reserved_name",
    "_bbox_km2",
    "_canvas_viewbox_4326",
    "_extract_remote_vector",
    "_footprint_box",
    "_geocode_one_address",
    "_geometry_counts",
    "_hub_resources",
    "_normalise_format_preference",
    "_oapif_collection",
    "_osm_area_refusal",
    "_osm_from_hosted",
    "_osm_to_geojson",
    "_osrm_base",
    "_overture_clip",
    "_overture_extract",
    "_overture_matches",
    "_overture_tile",
    "_overture_tile_url",
    "_overture_tiles",
    "_own_overpass_unreachable",
    "_parse_arcgis_hub_results",
    "_parse_cartociudad_forward",
    "_parse_ckan_results",
    "_parse_nominatim_forward",
    "_parse_photon_forward",
    "_portal_text",
    "_project_crs_transform",
    "_resolve_tilejson",
    "_run_on_main_thread",
    "_safe_filename",
    "_twin_of",
    "_wfs_failure",
    "_wfs_hits",
    "_wfs_restrict_to_view",
    "expand_link",
    "hosted_department_url",
    "limits",
    "net",
    "volume_guard",
]
