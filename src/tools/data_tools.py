# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
from __future__ import annotations

"""External data tools, geocoding, OSM, routing, WMS/WFS/XYZ tile sources."""
import concurrent.futures  # noqa: E402
import contextlib  # noqa: E402
import html  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import posixpath  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import unicodedata  # noqa: E402
import urllib.error  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402
import xml.etree.ElementTree as ET  # noqa: E402  # nosec B405 - XML input rejects DTD and entities before parsing
import zipfile  # noqa: E402

from qgis.core import (  # noqa: E402
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDataSourceUri,
    QgsDistanceArea,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)

from ..core import catalog, limits, net, security, tuning, vsi  # noqa: E402
from ..core.background import breathe, gc_paused, run_on_main_thread  # noqa: E402
from ..core.follow import view_kept  # noqa: E402
from ..core.geometry_budget import VertexBudget  # noqa: E402
from ..core.layer_order import cover_report, place_basemap  # noqa: E402
from ..core.logger import log_warning  # noqa: E402
from ..core.policy import create_managed_temp_dir  # noqa: E402
from ..core.provider_uri import crs_problem, encode_uri_url  # noqa: E402
from ..core.qt_compat import enum_member  # noqa: E402
from ..core.tool_registry import Tool, ToolRegistry  # noqa: E402
from . import volume_guard  # noqa: E402
from .csv_loader import CSV_EXTENSIONS  # noqa: E402




_run_on_main_thread = run_on_main_thread


_WINDOWS_RESERVED_NAMES = (
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)

_WINDOWS_FORBIDDEN_CHARS = '\\/:*?"<>|'


def _avoid_reserved_name(stem: str) -> str:
    """Prefix the old DOS device names so they can be opened as files."""




    return f"_{stem}" if stem.lower() in _WINDOWS_RESERVED_NAMES else stem


def _safe_filename(name: str, default: str = "download") -> str:
    """Turn an untrusted name into one open() accepts on every OS."""






    cleaned = "".join(
        "_" if (c in _WINDOWS_FORBIDDEN_CHARS or ord(c) < 32) else c
        for c in (name or "")
    ).strip(" .")
    if not cleaned:
        return default
    stem, ext = os.path.splitext(cleaned)
    stem = _avoid_reserved_name(stem.strip(" .")) or default
    return stem + ext


def _geojson_file(geojson_str: str, layer_name: str, subdir: str = "osm") -> str:
    """Write the GeoJSON to a managed temporary file and return its path."""

    tmp_dir = create_managed_temp_dir(subdir)
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in (layer_name or "layer"))[:60] or "layer"
    safe = _avoid_reserved_name(safe)
    path = os.path.join(tmp_dir, f"{safe}.geojson")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(geojson_str)
    return path







_GPKG_FROM_BYTES = 256 * 1024


def _vector_uri_for(path: str) -> str:
    """The source to open ``path`` by: the file itself, or a GeoPackage made from it off the main thread when it is a GeoJSON of any size."""










    lower = path.lower()
    if not lower.endswith((".geojson", ".json")):
        return path
    try:
        if os.path.getsize(path) < _GPKG_FROM_BYTES:
            return path
    except OSError:
        return path
    try:
        from osgeo import gdal

        gdal.UseExceptions()
        name = os.path.splitext(os.path.basename(path))[0]
        gpkg = os.path.join(os.path.dirname(path), f"{name}.gpkg")
        gdal.VectorTranslate(gpkg, path, format="GPKG", layerName=name)
    except Exception as exc:  # noqa: BLE001 - the GeoJSON still opens, only slower
        log_warning(f"GeoPackage conversion skipped for {os.path.basename(path)}: {exc}")
        return path
    return f"{gpkg}|layername={name}"


def _vector_source_from_geojson(geojson_str: str, layer_name: str, subdir: str = "osm") -> str:
    """Worker thread: the GeoJSON on disk, converted when large, as an OGR source string."""
    return _vector_uri_for(_geojson_file(geojson_str, layer_name, subdir))








_JSON_PIECEWISE_CHARS = 2 * 1024 * 1024


def _json_array_items(text: str, key: str) -> list | None:
    """The items of the array under top-level ``key``, decoded one at a time."""





    marker = text.find(f'"{key}"')
    if marker < 0:
        return None
    start = text.find("[", marker)
    if start < 0:
        return None
    decoder = json.JSONDecoder()
    items: list = []
    index = start + 1
    length = len(text)
    try:
        while True:
            while index < length and text[index] in " \t\r\n,":
                index += 1
            if index >= length:
                return None
            if text[index] == "]":
                return items
            item, index = decoder.raw_decode(text, index)
            items.append(item)
            breathe(len(items))
    except ValueError:
        return None


def _json_object(text: str, array_key: str) -> dict:
    """``json.loads(text)``, the array under ``array_key`` decoded piecewise when the text is large."""
    if len(text) >= _JSON_PIECEWISE_CHARS:
        with gc_paused():
            items = _json_array_items(text, array_key)
        if items is not None:
            return {array_key: items}
    with gc_paused():
        return json.loads(text)


def _feature_collection_file(features: list, layer_name: str, subdir: str) -> str:
    """Write a FeatureCollection of ``features`` to a managed file, one feature per dump."""
    tmp_dir = create_managed_temp_dir(subdir)
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in (layer_name or "layer"))[:60] or "layer"
    safe = _avoid_reserved_name(safe)
    path = os.path.join(tmp_dir, f"{safe}.geojson")
    with open(path, "w", encoding="utf-8") as handle, gc_paused():
        handle.write('{"type": "FeatureCollection", "features": [')
        for index, feature in enumerate(features):
            if index:
                handle.write(",\n")
            handle.write(json.dumps(feature))
            breathe(index)
        handle.write("]}")
    return path


def _vector_source_from_features(features: list, layer_name: str, subdir: str = "osm") -> str:
    """Worker thread: ``features`` on disk, converted when large, as an OGR source string."""
    return _vector_uri_for(_feature_collection_file(features, layer_name, subdir))


def _ogr_feature_count(uri: str) -> int | None:
    """Worker thread: how many features an OGR source holds, without QGIS."""

    try:
        from osgeo import ogr

        ogr.UseExceptions()
        path, _, layer = uri.partition("|layername=")
        source = ogr.Open(path)
        if source is None:
            return None
        found = source.GetLayerByName(layer) if layer else source.GetLayer(0)
        if found is None:
            return None
        count = int(found.GetFeatureCount())
        return count if count >= 0 else None
    except Exception:  # noqa: BLE001
        return None


def _layer_from_source(uri: str, layer_name: str):
    """Main thread: the OGR layer for a source a worker prepared (``_vector_source_from_geojson``)."""
    return QgsVectorLayer(uri, layer_name, "ogr")


def _layer_from_geojson_str(geojson_str: str, layer_name: str, subdir: str = "osm"):
    """Build an OGR vector layer from a GeoJSON string by writing it to a temp file and loading by path."""








    return QgsVectorLayer(_geojson_file(geojson_str, layer_name, subdir), layer_name, "ogr")






_USER_AGENT = net.user_agent()










def _service(key: str, default: str) -> str:
    return tuning.service_url(key, default)


_NOMINATIM_URL = "https://nominatim.openstreetmap.org"








_OVERPASS_CONNECT_TIMEOUT = 5.0



_OSRM_PROFILES = {
    "driving": "https://routing.openstreetmap.de/routed-car/route/v1/driving",
    "walking": "https://routing.openstreetmap.de/routed-foot/route/v1/driving",
    "cycling": "https://routing.openstreetmap.de/routed-bike/route/v1/driving",
}


def _osrm_base(profile: str) -> str:
    name = profile if profile in _OSRM_PROFILES else "driving"
    return _service(f"osrm_{name}", _OSRM_PROFILES[name])


_GEOCODE_TIMEOUT = 25
_OVERPASS_TIMEOUT = 45
_ROUTE_TIMEOUT = 30
_DOWNLOAD_TIMEOUT = 45


def _overpass_timeout() -> int:
    """The Overpass read budget, in seconds: the shipped 45 unless the server's policy says otherwise (``net.overpass_timeout_s`` in."""


    return tuning.limit("net", "overpass_timeout_s", _OVERPASS_TIMEOUT)


def _download_timeout() -> int:
    """Same for a file download (``net.download_timeout_s``)."""
    return tuning.limit("net", "download_timeout_s", _DOWNLOAD_TIMEOUT)


_PORTAL_TIMEOUT = 8
_INSPECT_TIMEOUT = 20
_MAX_DOWNLOAD_SIZE = 100 * 1024 * 1024



_MAX_EXTRACTED_SIZE = 1024 * 1024 * 1024





_MAX_ARCHIVE_ENTRIES = 10_000


_TOTAL_TIMEOUT_FACTOR = 3





_OSM_RECENT: dict[str, tuple[float, str, bytes]] = {}
_OSM_RECENT_SECONDS = 600
_OSM_RECENT_BYTES = 200 * 1024 * 1024

_OSM_RECENT_MIN_BYTES = 1024 * 1024


def _osm_recent(query: str):
    """(endpoint, raw) for a query answered in the last ten minutes, or None."""
    import time

    entry = _OSM_RECENT.get(query)
    if entry is None:
        return None
    stamped, endpoint, raw = entry
    if time.monotonic() - stamped > _OSM_RECENT_SECONDS:
        _OSM_RECENT.pop(query, None)
        return None
    return endpoint, raw


def _osm_remember(query: str, endpoint: str, raw: bytes) -> None:
    import time

    now = time.monotonic()
    for key, (stamped, _e, _r) in list(_OSM_RECENT.items()):
        if now - stamped > _OSM_RECENT_SECONDS:
            _OSM_RECENT.pop(key, None)
    size = len(raw)
    if size > _OSM_RECENT_BYTES or size < _OSM_RECENT_MIN_BYTES:
        return
    while _OSM_RECENT and sum(len(r) for _s, _e, r in _OSM_RECENT.values()) + size > _OSM_RECENT_BYTES:
        oldest = min(_OSM_RECENT, key=lambda k: _OSM_RECENT[k][0])
        _OSM_RECENT.pop(oldest, None)
    _OSM_RECENT[query] = (now, endpoint, raw)




_DOWNLOAD_TOTAL_TIMEOUT = 300


_CACHE_GEOCODE_S = 600
_CACHE_CATALOG_S = 300
_MAX_ROUTE_WAYPOINTS = 25

_VECTOR_FORMAT_HINTS = {"geojson", "json", "gpkg", "geopackage", "shp", "shapefile", "kml", "csv"}
_SERVICE_FORMAT_HINTS = {"wfs", "wms"}
_SOCRATA_GEOMETRY_TYPES = ("point", "multipoint", "line", "multiline", "polygon", "multipolygon", "location")




_OPEN_DATA_PORTALS = {
    "data_gouv_fr": {
        "name": "data.gouv.fr",
        "api_type": "data_gouv",
        "base_url": "https://www.data.gouv.fr",
        "search_url": "https://www.data.gouv.fr/api/1/datasets/?q={query}&page_size=10",
    },
    "opendatasoft_paris": {
        "name": "Paris Open Data",
        "api_type": "opendatasoft",
        "base_url": "https://opendata.paris.fr",
        "search_url": "https://opendata.paris.fr/api/explore/v2.1/catalog/datasets?where={query}&limit=10",
    },
    "opendatasoft_public": {
        "name": "OpenDataSoft Public",
        "api_type": "opendatasoft",
        "base_url": "https://public.opendatasoft.com",
        "search_url": "https://public.opendatasoft.com/api/explore/v2.1/catalog/datasets?where={query}&limit=10",
    },
    "data_europa_eu": {
        "name": "European Data Portal",
        "api_type": "ckan",
        "base_url": "https://data.europa.eu",
        "search_url": "https://data.europa.eu/api/hub/search/search?page=0&limit=10&q={query}",
    },
    "arcgis_hub": {
        "name": "ArcGIS Hub",
        "api_type": "arcgis_hub",
        "base_url": "https://hub.arcgis.com",



        "search_url": (
            "https://hub.arcgis.com/api/v3/datasets?q={query}"
            "&filter%5Btype%5D=Feature%20Layer&page%5Bsize%5D=10"
            "&fields%5Bdatasets%5D=name,type,url,source,owner,tags,searchDescription,recordCount,licenseInfo"
        ),
    },
}


def _open_data_portals() -> dict:
    """The portal table, with whatever a row corrected in it."""







    return tuning.service_rows("open_data_portals", _OPEN_DATA_PORTALS)


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
            },
            "required": ["query", "bbox"],
        },
        handler=_fetch_osm_data,
        background=True,
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
                    "items": {"type": "string", "enum": list(_FOOTPRINT_SOURCES)},
                },
                "layer_name": {"type": "string"},
            },
            "required": ["bbox"],
        },
        handler=_fetch_building_footprints,
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
            },
            "required": ["theme"],
        },
        handler=_fetch_overture,
        background=True,
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
        (node("fetch_overture", ("properties", "theme")), "overture_themes", _OVERTURE_THEMES),
        (node("fetch_building_footprints", ("properties", "sources", "items")),
         "footprint_sources", _FOOTPRINT_SOURCES),
    )
    for schema, key, shipped in wanted:
        if isinstance(schema, dict) and isinstance(schema.get("enum"), list):
            _ENUM_NODES.append((schema, key, tuple(shipped)))
    if _ENUM_NODES:
        tuning.subscribe(_refresh_served_vocabularies)
        _refresh_served_vocabularies()


def _refresh_served_vocabularies() -> None:
    """Put the served vocabulary into the registered schemas. Socket thread."""
    for schema, key, shipped in _ENUM_NODES:
        schema["enum"] = list(tuning.service_list(key, shipped))


def _http_get(url: str, timeout: int = 15, cache_ttl: float = 0.0) -> bytes:
    """A guarded GET, capped in size and in total time."""






    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    return net.fetch(req, timeout=timeout, max_bytes=_MAX_DOWNLOAD_SIZE,
                     total_timeout=timeout * _TOTAL_TIMEOUT_FACTOR, cache_ttl=cache_ttl).body


def _http_fetch(url: str, timeout: int = 15, cache_ttl: float = 0.0) -> tuple[bytes, dict, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/json, application/geo+json, application/xml, text/xml;q=0.9, */*;q=0.8",
        },
    )
    answer = net.fetch(req, timeout=timeout, max_bytes=_MAX_DOWNLOAD_SIZE,
                       total_timeout=timeout * _TOTAL_TIMEOUT_FACTOR, cache_ttl=cache_ttl)
    return answer.body, answer.headers, answer.url


def _canvas_viewbox_4326() -> str | None:
    """Return the current canvas extent as a Nominatim viewbox string (lon1,lat1,lon2,lat2)."""
    try:
        from qgis.utils import iface as qgis_iface
        canvas = qgis_iface.mapCanvas()
        extent = canvas.extent()
        canvas_crs = canvas.mapSettings().destinationCrs()
        if canvas_crs.authid() != "EPSG:4326":
            xform = QgsCoordinateTransform(
                canvas_crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance()
            )
            extent = xform.transformBoundingBox(extent)
        w, e = extent.xMinimum(), extent.xMaximum()
        s, n = extent.yMinimum(), extent.yMaximum()
        if abs(e - w) > 0.001 and abs(n - s) > 0.001:
            return f"{w:.6f},{s:.6f},{e:.6f},{n:.6f}"
    except Exception:  # nosec B110 - canvas transform is optional
        pass
    return None


def _project_crs_transform(points: list) -> tuple | None:
    """Transform a list of (lon, lat) EPSG:4326 points into the project CRS."""






    project_crs = QgsProject.instance().crs()
    if not project_crs.isValid() or project_crs.authid() == "EPSG:4326":
        return None
    source = QgsCoordinateReferenceSystem("EPSG:4326")
    xform = QgsCoordinateTransform(source, project_crs, QgsProject.instance())
    transformed = []
    for lon, lat in points:
        pt = xform.transform(QgsPointXY(lon, lat))
        transformed.append((pt.x(), pt.y()))
    return project_crs.authid(), transformed


def _is_number(value) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False











_PHOTON_URL = "https://geocode.terra-lab.ai"
_BAN_URL = "https://api-adresse.data.gouv.fr"
_CARTOCIUDAD_URL = "https://www.cartociudad.es/geocoder/api/geocoder"
_PELIAS_URL = "https://api.geocode.earth/v1"

_DEFAULT_GEOCODE_PROVIDER = "photon"


def _viewbox_bounds(viewbox: str | None) -> tuple | None:
    """Split the canvas viewbox string (west,south,east,north) into four floats."""
    if not viewbox:
        return None
    try:
        west, south, east, north = (float(part) for part in viewbox.split(","))
    except (TypeError, ValueError):
        return None
    return west, south, east, north


def _viewbox_centre(viewbox: str | None) -> tuple | None:
    bounds = _viewbox_bounds(viewbox)
    if not bounds:
        return None
    west, south, east, north = bounds
    lon, lat = (west + east) / 2.0, (south + north) / 2.0




    if lon != lon or lat != lat or not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        return None
    return lon, lat


def _fold(value) -> str:
    """Lowercase, accent-free text, so "Zurich" and "Zürich" compare equal."""
    text = unicodedata.normalize("NFKD", _text(value).casefold())
    return "".join(character for character in text if not unicodedata.combining(character))






_GENERIC_PLACE_WORDS = re.compile(
    r"\b(?:city\s+cent(?:re|er)|town\s+cent(?:re|er)|centre[\s-]?ville|downtown|cent(?:re|er)|"
    r"old\s+town|inner\s+city|cbd)\b", re.IGNORECASE)


def _without_generic_place_words(query: str) -> str:
    """The query minus its filler, when a place name is left after it."""
    parts = [part.strip() for part in query.split(",")]
    head = _GENERIC_PLACE_WORDS.sub(" ", parts[0])
    head = re.sub(r"\s{2,}", " ", head).strip(" -,")
    head = re.sub(r"^(?:de|du|d'|of|the)\s+", "", head, flags=re.IGNORECASE)
    if not head:
        return query
    return ", ".join([head] + [part for part in parts[1:] if part])


def _answers_by_name(query: str, results: list) -> bool:
    """True when a hit is actually named what the query asked for."""





    asked = _fold(query.split(",")[0])
    if not asked:
        return True
    for hit in results:
        label = _fold(hit.get("display_name"))
        if label.startswith(asked) and not label[len(asked):len(asked) + 1].isalnum():
            return True
    return False


def _country_list(country_codes: str) -> list:
    """The comma-separated ISO 3166-1 alpha-2 argument as a clean lowercase list."""
    return [code.strip().lower() for code in (country_codes or "").split(",") if code.strip()]


def _join_url(base: str, path: str) -> str:
    return base.rstrip("/") + path


def _text(value) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _label_from(*parts) -> str:
    """Join address pieces into one display line, dropping blanks and repeats."""





    seen: set = set()
    ordered = []
    for part in parts:
        text = _text(part)
        if text and text not in seen:
            seen.add(text)
            ordered.append(text)
    return ", ".join(ordered)


def _bbox_from_corners(south, north, west, east) -> list | None:
    """A bounding box in Nominatim's [south, north, west, east] order, or None."""
    try:
        return [float(south), float(north), float(west), float(east)]
    except (TypeError, ValueError):
        return None


def _feature_point(feature: dict) -> tuple | None:
    """The (lon, lat) of a GeoJSON Point feature, or None when it is not one."""
    geometry = feature.get("geometry") if isinstance(feature, dict) else None
    coords = geometry.get("coordinates") if isinstance(geometry, dict) else None
    if not isinstance(coords, (list, tuple)) or len(coords) < 2:
        return None
    if not (_is_number(coords[0]) and _is_number(coords[1])):
        return None
    return float(coords[0]), float(coords[1])


def _geojson_features(payload) -> list:
    features = payload.get("features") if isinstance(payload, dict) else None
    return [f for f in features if isinstance(f, dict)] if isinstance(features, list) else []




def _nominatim_forward_url(base, query, limit, country_codes, viewbox) -> str:
    params: dict = {"q": query, "format": "json", "limit": limit, "addressdetails": 1}
    if viewbox:
        params["viewbox"] = viewbox
        params["bounded"] = 0
    if country_codes:
        params["countrycodes"] = country_codes
    return _join_url(base, "/search") + "?" + urllib.parse.urlencode(params)


def _nominatim_reverse_url(base, lat, lon) -> str:
    params = urllib.parse.urlencode({"lat": lat, "lon": lon, "format": "json", "addressdetails": 1})
    return _join_url(base, "/reverse") + "?" + params


def _parse_nominatim_forward(payload, verbose: bool) -> list | None:
    if not isinstance(payload, list):
        return None
    hits = []
    for r in payload:
        if not (isinstance(r, dict) and _is_number(r.get("lat")) and _is_number(r.get("lon"))):
            continue
        hit = {
            "display_name": r.get("display_name"),
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "type": r.get("type"),
            "class": r.get("class"),
        }
        if verbose:


            hit["boundingbox"] = r.get("boundingbox")
            hit["osm_id"] = r.get("osm_id")
            hit["score"] = r.get("importance")
        hits.append(hit)
    return hits


def _parse_nominatim_reverse(payload) -> dict | None:
    if not isinstance(payload, dict) or "error" in payload:
        return None
    if not (_is_number(payload.get("lat")) and _is_number(payload.get("lon"))):
        return None
    return {
        "display_name": payload.get("display_name"),
        "lat": float(payload["lat"]),
        "lon": float(payload["lon"]),
        "address": payload.get("address", {}),
        "osm_id": payload.get("osm_id"),
    }




def _photon_forward_url(base, query, limit, country_codes, viewbox) -> str:
    params: dict = {"q": query, "limit": limit}
    if viewbox:







        centre = _viewbox_centre(viewbox)
        if centre:
            params["lon"], params["lat"] = f"{centre[0]:.6f}", f"{centre[1]:.6f}"
            params["zoom"] = 10
    return _join_url(base, "/api") + "?" + urllib.parse.urlencode(params)


def _photon_reverse_url(base, lat, lon) -> str:
    return _join_url(base, "/reverse") + "?" + urllib.parse.urlencode({"lat": lat, "lon": lon, "limit": 1})


def _photon_label(props: dict) -> str:
    street = " ".join(p for p in (_text(props.get("housenumber")), _text(props.get("street"))) if p)
    return _label_from(props.get("name"), street, props.get("postcode"), props.get("city"),
                       props.get("state"), props.get("country"))


def _photon_hit(feature: dict, verbose: bool) -> dict | None:
    point = _feature_point(feature)
    if not point:
        return None
    props = feature.get("properties") or {}
    hit = {
        "display_name": _photon_label(props) or None,
        "lat": point[1],
        "lon": point[0],
        "type": props.get("type") or props.get("osm_value"),
        "class": props.get("osm_key"),
    }
    if props.get("countrycode"):
        hit["country_code"] = str(props["countrycode"]).lower()
    if verbose:
        extent = props.get("extent")
        if isinstance(extent, list) and len(extent) == 4:

            hit["boundingbox"] = _bbox_from_corners(extent[3], extent[1], extent[0], extent[2])
        hit["osm_id"] = props.get("osm_id")
        hit["score"] = None
    return hit


def _parse_photon_forward(payload, verbose: bool) -> list | None:
    if not isinstance(payload, dict):
        return None
    return [hit for hit in (_photon_hit(f, verbose) for f in _geojson_features(payload)) if hit]


def _parse_photon_reverse(payload) -> dict | None:
    features = _geojson_features(payload) if isinstance(payload, dict) else []
    if not features:
        return None
    hit = _photon_hit(features[0], False)
    if not hit:
        return None
    props = features[0].get("properties") or {}
    return {
        "display_name": hit["display_name"],
        "lat": hit["lat"],
        "lon": hit["lon"],
        "address": {k: v for k, v in props.items() if k not in ("extent", "osm_id", "osm_type")},
        "osm_id": props.get("osm_id"),
    }










_BAN_COUNTRY_WORDS = ("france", "fr", "francia", "frankreich")


def _ban_query(query: str) -> str:
    """The query without a trailing country, unless that is all there is."""
    parts = [part.strip() for part in str(query or "").split(",")]
    while len(parts) > 1 and parts[-1].lower().strip(". ") in _BAN_COUNTRY_WORDS:
        parts.pop()
    trimmed = ", ".join(part for part in parts if part)
    return trimmed or str(query or "").strip()


def _ban_forward_url(base, query, limit, country_codes, viewbox) -> str:
    params: dict = {"q": _ban_query(query), "limit": limit, "autocomplete": 0}
    centre = _viewbox_centre(viewbox)
    if centre:

        params["lon"], params["lat"] = f"{centre[0]:.6f}", f"{centre[1]:.6f}"
    return _join_url(base, "/search/") + "?" + urllib.parse.urlencode(params)


def _ban_reverse_url(base, lat, lon) -> str:
    return _join_url(base, "/reverse/") + "?" + urllib.parse.urlencode({"lat": lat, "lon": lon, "limit": 1})


def _ban_hit(feature: dict, verbose: bool) -> dict | None:
    point = _feature_point(feature)
    if not point:
        return None
    props = feature.get("properties") or {}
    hit = {
        "display_name": props.get("label"),
        "lat": point[1],
        "lon": point[0],
        "type": props.get("type"),
        "class": "address",
    }
    if verbose:
        hit["id"] = props.get("id")
        hit["score"] = props.get("score")
    return hit


def _parse_ban_forward(payload, verbose: bool) -> list | None:
    if not isinstance(payload, dict):
        return None
    return [hit for hit in (_ban_hit(f, verbose) for f in _geojson_features(payload)) if hit]


def _parse_ban_reverse(payload) -> dict | None:
    features = _geojson_features(payload) if isinstance(payload, dict) else []
    if not features:
        return None
    hit = _ban_hit(features[0], False)
    if not hit:
        return None
    props = features[0].get("properties") or {}
    return {
        "display_name": hit["display_name"],
        "lat": hit["lat"],
        "lon": hit["lon"],
        "address": {k: v for k, v in props.items() if k not in ("x", "y", "score", "distance", "_type")},
        "osm_id": None,
    }




def _cartociudad_forward_url(base, query, limit, country_codes, viewbox) -> str:


    return _join_url(base, "/candidates") + "?" + urllib.parse.urlencode({"q": query, "limit": limit})


def _cartociudad_reverse_url(base, lat, lon) -> str:
    return _join_url(base, "/reverseGeocode") + "?" + urllib.parse.urlencode({"lat": lat, "lon": lon})


def _cartociudad_label(r: dict) -> str:
    address = _text(r.get("address"))
    via = _text(r.get("tip_via"))
    if address and via and address.upper().startswith(via.upper()):


        street = address
    else:
        street = " ".join(p for p in (via, address, _text(r.get("portalNumber"))) if p)
    town = _text(r.get("poblacion")) or _text(r.get("muni"))
    return _label_from(street, r.get("postalCode"), town, r.get("province"))


def _cartociudad_hit(r, verbose: bool) -> dict | None:


    if not (isinstance(r, dict) and _is_number(r.get("lat")) and _is_number(r.get("lng"))):
        return None
    lat, lon = float(r["lat"]), float(r["lng"])
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    hit = {
        "display_name": _cartociudad_label(r) or None,
        "lat": lat,
        "lon": lon,
        "type": r.get("type"),
        "class": "address",
    }
    if verbose:
        hit["id"] = r.get("id")
        hit["score"] = None
    return hit


def _parse_cartociudad_forward(payload, verbose: bool) -> list | None:
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        return None
    return [hit for hit in (_cartociudad_hit(r, verbose) for r in payload) if hit]


def _parse_cartociudad_reverse(payload) -> dict | None:
    if isinstance(payload, list):
        payload = payload[0] if payload else None
    hit = _cartociudad_hit(payload, False)
    if not hit:
        return None
    fields = ("type", "tip_via", "address", "portalNumber", "postalCode", "poblacion", "muni",
              "province", "comunidadAutonoma", "refCatastral")
    return {
        "display_name": hit["display_name"],
        "lat": hit["lat"],
        "lon": hit["lon"],
        "address": {k: payload[k] for k in fields if _text(payload.get(k))},
        "osm_id": None,
    }




def _pelias_forward_url(base, query, limit, country_codes, viewbox) -> str:
    params: dict = {"text": query, "size": limit}
    bounds = _viewbox_bounds(viewbox)
    if bounds:
        west, south, east, north = bounds
        params["boundary.rect.min_lon"] = f"{west:.6f}"
        params["boundary.rect.min_lat"] = f"{south:.6f}"
        params["boundary.rect.max_lon"] = f"{east:.6f}"
        params["boundary.rect.max_lat"] = f"{north:.6f}"
    countries = _country_list(country_codes)
    if countries:
        params["boundary.country"] = ",".join(countries).upper()
    return _join_url(base, "/search") + "?" + urllib.parse.urlencode(params)


def _pelias_reverse_url(base, lat, lon) -> str:
    params: dict = {"point.lat": lat, "point.lon": lon, "size": 1}
    return _join_url(base, "/reverse") + "?" + urllib.parse.urlencode(params)


def _pelias_hit(feature: dict, verbose: bool) -> dict | None:
    point = _feature_point(feature)
    if not point:
        return None
    props = feature.get("properties") or {}
    hit = {
        "display_name": props.get("label"),
        "lat": point[1],
        "lon": point[0],
        "type": props.get("layer"),
        "class": props.get("source"),
    }
    if verbose:
        bbox = feature.get("bbox")
        if isinstance(bbox, list) and len(bbox) == 4:
            hit["boundingbox"] = _bbox_from_corners(bbox[1], bbox[3], bbox[0], bbox[2])
        hit["id"] = props.get("gid")
        hit["score"] = props.get("confidence")
    return hit


def _parse_pelias_forward(payload, verbose: bool) -> list | None:
    if not isinstance(payload, dict):
        return None
    return [hit for hit in (_pelias_hit(f, verbose) for f in _geojson_features(payload)) if hit]


def _parse_pelias_reverse(payload) -> dict | None:
    features = _geojson_features(payload) if isinstance(payload, dict) else []
    if not features:
        return None
    hit = _pelias_hit(features[0], False)
    if not hit:
        return None
    return {
        "display_name": hit["display_name"],
        "lat": hit["lat"],
        "lon": hit["lon"],
        "address": features[0].get("properties") or {},
        "osm_id": None,
    }


_GEOCODE_PROVIDERS = {
    "nominatim": {
        "name": "OpenStreetMap Nominatim",
        "base": _NOMINATIM_URL,
        "service": "nominatim",
        "scope": "worldwide",
        "forward_url": _nominatim_forward_url,
        "reverse_url": _nominatim_reverse_url,
        "parse_forward": _parse_nominatim_forward,
        "parse_reverse": _parse_nominatim_reverse,
    },
    "photon": {
        "name": "Photon (TerraLab, OpenStreetMap)",
        "base": _PHOTON_URL,
        "service": "photon",
        "scope": "worldwide",
        "forward_url": _photon_forward_url,
        "reverse_url": _photon_reverse_url,
        "parse_forward": _parse_photon_forward,
        "parse_reverse": _parse_photon_reverse,
    },
    "ban": {
        "name": "Base Adresse Nationale (France)",
        "base": _BAN_URL,
        "service": "ban",
        "scope": "France",
        "forward_url": _ban_forward_url,
        "reverse_url": _ban_reverse_url,
        "parse_forward": _parse_ban_forward,
        "parse_reverse": _parse_ban_reverse,
    },
    "cartociudad": {
        "name": "CartoCiudad (IGN Spain)",
        "base": _CARTOCIUDAD_URL,
        "service": "cartociudad",
        "scope": "Spain",
        "forward_url": _cartociudad_forward_url,
        "reverse_url": _cartociudad_reverse_url,
        "parse_forward": _parse_cartociudad_forward,
        "parse_reverse": _parse_cartociudad_reverse,
    },



    "pelias": {
        "name": "Pelias",
        "base": "",
        "scope": "the instance you point it at",
        "forward_url": _pelias_forward_url,
        "reverse_url": _pelias_reverse_url,
        "parse_forward": _parse_pelias_forward,
        "parse_reverse": _parse_pelias_reverse,
    },
}

_GEOCODE_PROVIDER_IDS = tuple(_GEOCODE_PROVIDERS)


def _resolve_geocode_provider(args: dict) -> tuple:
    """Return (provider_id, provider, base) or raise ValueError."""






    provider_id = _text(args.get("provider")).lower() or _DEFAULT_GEOCODE_PROVIDER
    provider = _GEOCODE_PROVIDERS.get(provider_id)
    if provider is None:
        raise ValueError(f"Unknown geocoding provider '{provider_id}'. "
                         f"Choose one of: {', '.join(_GEOCODE_PROVIDER_IDS)}.")

    base = _text(args.get("endpoint")) or _service(str(provider.get("service") or ""), provider["base"])
    if not base:
        raise ValueError(f"{provider['name']} has no public endpoint. Pass endpoint with your own instance, "
                         "or use 'ban' in France, 'cartociudad' in Spain, 'photon' or 'nominatim' anywhere.")
    return provider_id, provider, base














_BACKEND_GEO_TIMEOUT = 25


def _backend_geo_url(path: str) -> str:
    """The address of one backend geo endpoint, on whichever server we are paired with."""
    from ..core.settings import Settings

    parts = urllib.parse.urlsplit(Settings().server_url)
    scheme = "http" if parts.scheme == "ws" else "https"
    return f"{scheme}://{parts.netloc}{path}"


def _backend_geo(path: str, params: dict, cache_ttl: float = 0.0) -> dict | None:
    """Ask the backend, or None when it cannot answer."""








    from ..core.settings import Settings

    key = Settings().activation_key
    if not key:
        return None
    try:
        url = f"{_backend_geo_url(path)}?{urllib.parse.urlencode(params, doseq=True)}"
        request = urllib.request.Request(url, headers={
            "User-Agent": _USER_AGENT, "Accept": "application/json",
            "Authorization": f"Bearer {key}"})
        raw = net.fetch(request, timeout=_BACKEND_GEO_TIMEOUT, max_bytes=_MAX_DOWNLOAD_SIZE,
                        total_timeout=_BACKEND_GEO_TIMEOUT * _TOTAL_TIMEOUT_FACTOR,
                        cache_ttl=cache_ttl).body
        answer = json.loads(raw)
        return answer if isinstance(answer, dict) and not answer.get("error") else None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log_warning(f"backend geo fallback failed: {exc}")
        return None


def _geocode_fetch(url: str):
    """Fetch a geocoder answer, parsed, or None when the body is empty."""





    budget = tuning.limit("net", "geocode_timeout_s", _GEOCODE_TIMEOUT)
    data = _http_get(url, timeout=budget, cache_ttl=_CACHE_GEOCODE_S)
    if not data.strip():
        return None
    return json.loads(data)


def _geocode(args: dict) -> dict:




    query = _without_generic_place_words(str(args.get("query") or "").strip())
    if not query:
        return {"_error": "The place to look up is empty.", "code": "INVALID_ARGS",
                "suggestion": "Pass the place name, address or postcode to geocode."}



    try:
        limit = max(1, min(int(args.get("limit") or 5), 10))
    except (TypeError, ValueError):
        return {"_error": f"limit must be a whole number from 1 to 10, got {args.get('limit')!r}.",
                "code": "INVALID_ARGS", "suggestion": "Pass limit as an integer, or leave it out for 5."}
    country_codes = (args.get("country_codes") or "").strip()

    try:
        provider_id, provider, base = _resolve_geocode_provider(args)
    except ValueError as e:
        return {"_error": str(e)}



    try:
        viewbox = _run_on_main_thread(_canvas_viewbox_4326)
    except Exception:  # nosec B110 - canvas transform is optional
        viewbox = None

    url = provider["forward_url"](base, query, limit, country_codes, viewbox)

    fell_back = False
    try:
        payload = _geocode_fetch(url)
    except urllib.error.HTTPError as e:






        payload = None
        if e.code == 400 and viewbox:
            try:
                payload = _geocode_fetch(provider["forward_url"](base, query, limit, country_codes, None))
            except (urllib.error.URLError, OSError):
                payload = None
        if payload is None:
            failure = {"_error": f"Geocoding request failed: {e}"}
            fell_back = True
        else:
            failure = None
    except (urllib.error.URLError, OSError) as e:
        failure = {"_error": f"Geocoding request failed: {e}"}
        payload, fell_back = None, True
    except ValueError:
        failure = {"_error": "Geocoding service returned an unreadable answer. Try again in a moment."}
        payload, fell_back = None, True
    else:
        failure = None

    results = provider["parse_forward"](payload, bool(args.get("verbose", False))) if payload is not None else []
    if results is None:
        failure = {"_error": f"Geocoding service error: {str(payload)[:200]}"}
        results, fell_back = [], True



    wanted = set(_country_list(args.get("country_codes") or ""))

    def _keep_wanted(hits: list) -> list:
        if not (wanted and hits):
            return hits
        kept = [hit for hit in hits if not hit.get("country_code") or hit["country_code"] in wanted]
        return kept or hits

    results = _keep_wanted(results)







    if viewbox and results and not fell_back and not _answers_by_name(query, results):
        try:
            plain = provider["parse_forward"](
                _geocode_fetch(provider["forward_url"](base, query, limit, country_codes, None)),
                bool(args.get("verbose", False)))
        except (urllib.error.URLError, OSError, ValueError):
            plain = None
        if plain and _answers_by_name(query, _keep_wanted(plain)):
            results = _keep_wanted(plain)

    if fell_back:



        answer = _backend_geo("/geo/geocode", {"q": query, "limit": limit, "country": country_codes},
                              cache_ttl=_CACHE_GEOCODE_S)
        hits = (answer or {}).get("results") or []
        if not hits:
            return failure
        results, provider_id = hits, "azure_maps"
    output = results[:limit]
    if not output:
        return {"results": [], "count": 0, "provider": provider_id,
                "message": f"No results found for '{query}'"}

    verbose = bool(args.get("verbose", False))


    project_crs = None
    try:
        transformed = _run_on_main_thread(
            _project_crs_transform, [(r["lon"], r["lat"]) for r in output]
        )
        if transformed:
            authid, coords = transformed
            project_crs = authid
            for r, (x, y) in zip(output, coords):
                r["x_project_crs"] = x
                r["y_project_crs"] = y
                if verbose:
                    r["project_crs"] = authid
    except Exception:  # nosec B110 - canvas transform is optional
        pass

    out = {"results": output, "count": len(output), "provider": provider_id}
    if fell_back:
        out["note"] = ("The geocoder you asked for did not answer, so this came from the AI Agent backend's "
                       "fallback. Attribution: (c) Microsoft, (c) TomTom.")
    if project_crs:

        out["project_crs"] = project_crs
    layer_name = str(args.get("layer_name") or "").strip()
    if layer_name:
        try:
            out["layer"] = _run_on_main_thread(_add_geocode_layer, layer_name, query, output[0])
        except Exception as e:  # noqa: BLE001 - the hits are still a full answer
            out["layer_error"] = f"Could not add the point layer: {e}"
    return out


def _add_geocode_layer(name: str, query: str, hit: dict) -> dict:
    """One-point memory layer for the first geocode hit, added to the project and zoomed to."""
    from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsProject, QgsVectorLayer

    layer = QgsVectorLayer(
        "Point?crs=EPSG:4326&field=name:string(200)&field=display_name:string(500)&field=lat:double&field=lon:double",
        name, "memory",
    )
    feature = QgsFeature(layer.fields())
    feature.setAttributes([query, hit.get("display_name"), hit["lat"], hit["lon"]])
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(hit["lon"], hit["lat"])))
    layer.dataProvider().addFeatures([feature])
    layer.updateExtents()
    QgsProject.instance().addMapLayer(layer)
    try:
        from qgis.utils import iface
        canvas = iface.mapCanvas()
        canvas.setExtent(canvas.mapSettings().layerExtentToOutputExtent(layer, layer.extent()).buffered(0))
        canvas.zoomScale(5000)
        canvas.refresh()
    except Exception:  # nosec B110 - zoom is a convenience
        pass
    return {"layer_id": layer.id(), "layer_name": name, "feature_count": 1, "crs": "EPSG:4326",
            "added_to_project": True}


def _reverse_geocode(args: dict) -> dict:
    lat = args["lat"]
    lon = args["lon"]

    try:
        provider_id, provider, base = _resolve_geocode_provider(args)
    except ValueError as e:
        return {"_error": str(e)}

    url = provider["reverse_url"](base, lat, lon)

    try:
        payload = _geocode_fetch(url)
    except (urllib.error.URLError, OSError) as e:
        return {"_error": f"Reverse geocoding failed: {e}"}
    except ValueError:
        return {"_error": "Reverse geocoding returned an unreadable answer. Try again in a moment."}

    if payload is None:
        return {"_error": f"{provider['name']} found no address at that point."}
    out = provider["parse_reverse"](payload)
    if out is None:
        return {"_error": f"{provider['name']} found no address at that point."}
    out["provider"] = provider_id


    try:
        transformed = _run_on_main_thread(
            _project_crs_transform, [(out["lon"], out["lat"])]
        )
        if transformed:
            authid, coords = transformed
            out["x_project_crs"] = coords[0][0]
            out["y_project_crs"] = coords[0][1]
            out["project_crs"] = authid
    except Exception:  # nosec B110 - project CRS transform is optional
        pass

    return out









OSM_REFUSE_KM2 = limits.MAX_FETCH_KM2
_bbox_km2 = limits.bbox_km2


def _overture_clip(theme: str, box, args: dict) -> dict:
    """One theme clipped to *box* by our open data API, as fetch_overture would do it."""





    west, south, east, north = box
    call = {"theme": theme, "mode": "clip",
            "bbox": {"south": south, "west": west, "north": north, "east": east}}
    for key in ("layer_name", "confirm_area_km2", "confirm_large", "filter"):
        if args.get(key) is not None:
            call[key] = args[key]
    return _fetch_overture(call)


def _osm_from_hosted(themes: list, box, args: dict, km2: float) -> dict:
    """A fetch_osm_data call above the quiet band, served from TerraLab's tiles."""










    base_name = str(args.get("layer_name") or "").strip()
    layers, errors = [], []
    for theme in themes:
        name = base_name if base_name and len(themes) == 1 else f"{base_name} {theme}".strip()
        per_theme = dict(args)
        if name:
            per_theme["layer_name"] = name
        classes = _osm_road_classes(str(args.get("query") or "")) if theme == "roads" else []
        if classes:




            per_theme["filter"] = {"class": classes}
        out = _overture_clip(theme, box, per_theme)
        if out.get("_error"):
            errors.append(f"{theme}: {out['_error']}")
            continue
        layers.append(out)
    if not layers:
        return {"_error": f"{km2:,.0f} km² of {', '.join(themes)} could not be served from TerraLab's tiles: "
                + "; ".join(errors),
                "area_km2": round(km2, 1), "code": limits.CEILING_CODE,
                "suggestion": errors and "" or volume_guard.ZONE_ARGUMENTS.format(quiet=volume_guard.quiet_km2())}
    return {"served_by": "TerraLab hosted tiles", "area_km2": round(km2, 1),
            "themes": [layer["theme"] for layer in layers], "layers": layers,
            "feature_count": sum(int(layer.get("feature_count") or 0) for layer in layers),
            "skipped_themes": errors, "crs": "EPSG:4326",
            "licence": ", ".join(sorted({layer.get("licence", "") for layer in layers if layer.get("licence")})),
            "_note": (f"Overpass was not asked: {km2:,.0f} km² is above its quiet band, so the query's tags "
                      "were mapped to TerraLab's hosted themes and clipped to the box by our open data API. "
                      "A highway= value in the query is kept as the road class filter; any other tag "
                      "(cycleway=*, a waterway type) is a layer filter to apply next, or ask Overpass "
                      f"under {volume_guard.quiet_km2():g} km².")}


_HIGHWAY_VALUE = re.compile(r'\bhighway\b\s*"?\s*[=~]\s*"?([a-z_|()^$]+)', re.IGNORECASE)


def _osm_road_classes(query: str) -> list:
    """The highway values an Overpass query asks for, as Overture road classes."""




    found: list = []
    for match in _HIGHWAY_VALUE.finditer(query):
        for value in match.group(1).lower().split("|"):
            value = value.strip("^$() ")
            if value and value not in found:
                found.append(value)
    return found


def _osm_endpoints(km2: float) -> list:
    """The mirrors one query may be sent to, best first."""









    mirrors = catalog.overpass_mirrors()
    if km2 <= volume_guard.dense_max_km2():
        return mirrors
    return [url for url in mirrors if volume_guard.own_overpass_host() in str(url).lower()]


def _osm_area_refusal(km2: float, box=None, args: dict | None = None) -> dict | None:
    """The refusal of a fetch_osm_data request over *km2*, or None when it may be sent."""







    themes, hosted_cap = volume_guard.hosted_ceiling("fetch_osm_data", args or {})
    ceiling = hosted_cap if themes else OSM_REFUSE_KM2
    if not themes:





        ceiling = max(ceiling, volume_guard.selective_max_km2("fetch_osm_data", args or {}))
    if km2 < ceiling:
        return None




    fit = volume_guard.fitting_zone(dict(zip(("south", "west", "north", "east"), box)), ceiling) if box else {}
    if themes:
        what = (f"{km2:,.0f} square kilometres is over the {ceiling:,.0f} TerraLab's tiles clip "
                f"{', '.join(themes)} to in one fetch_osm_data call; the request was not sent.")
    else:
        what = (f"{km2:,.0f} square kilometres is over the {ceiling:,.0f} one fetch_osm_data "
                "call may cover; the request was not sent.")
    out = {"_error": what, "area_km2": round(km2, 1), "code": limits.CEILING_CODE}
    if fit:
        out.update(fit)
        out["suggestion"] = volume_guard.fitting_sentence(fit).strip()
    if themes:
        out["suggestion"] = (out.get("suggestion", "") + volume_guard.HOSTED_INSTEAD).strip()
    return out












_FOOTPRINTS_URL = "https://terra-lab.ai/api/building-footprints"




_FOOTPRINT_SOURCES = ("microsoft", "google", "openstreetmap", "overture")
_FOOTPRINT_DEFAULT_SOURCES = ("microsoft", "openstreetmap")
_FOOTPRINT_SERVICE_SOURCES = ("microsoft", "google", "openstreetmap")
_FOOTPRINT_LABELS = {"microsoft": "Microsoft Buildings",
                     "google": "Google Open Buildings",
                     "openstreetmap": "OSM Buildings",
                     "overture": "Overture Buildings"}





_FOOTPRINT_MAX_KM2 = volume_guard.FOOTPRINTS_MAX_KM2
_FOOTPRINT_MAX_SPAN_DEG = 0.5



_FOOTPRINT_TIMEOUT = 75


def _footprint_box(bbox: dict):
    """(west, south, east, north) from either bbox convention, or a reason."""
    if not isinstance(bbox, dict):
        return None, "bbox must be an object with either {south, west, north, east} or {xmin, ymin, xmax, ymax}"
    west = bbox.get("west", bbox.get("xmin"))
    south = bbox.get("south", bbox.get("ymin"))
    east = bbox.get("east", bbox.get("xmax"))
    north = bbox.get("north", bbox.get("ymax"))
    if None in (west, south, east, north):
        return None, "bbox must provide either {south, west, north, east} or {xmin, ymin, xmax, ymax}"
    try:
        box = (float(west), float(south), float(east), float(north))
    except (TypeError, ValueError):
        return None, "bbox values must be numbers in EPSG:4326 degrees"
    if box[2] <= box[0] or box[3] <= box[1]:
        return None, "bbox must have east greater than west and north greater than south"
    return box, ""


def _fetch_building_footprints(args: dict) -> dict:
    box, problem = _footprint_box(args.get("bbox") or {})
    if box is None:
        return {"_error": problem}
    west, south, east, north = box




    asked = args.get("sources") or list(_FOOTPRINT_DEFAULT_SOURCES)
    if isinstance(asked, str):
        asked = [part.strip() for part in asked.split(",")]
    known = tuning.service_list("footprint_sources", _FOOTPRINT_SOURCES)
    sources = [name for name in known if name in asked]
    if not sources:
        return {"_error": f"sources must name at least one of: {', '.join(known)}"}



    span = max(east - west, north - south)
    if span > _FOOTPRINT_MAX_SPAN_DEG:
        return {"_error": f"Each side of the box must stay under {_FOOTPRINT_MAX_SPAN_DEG} degrees.",
                "suggestion": "Ask for a smaller extent, or one arrondissement or district at a time."}
    area_km2 = _bbox_km2(south, west, north, east)
    cap_km2 = volume_guard.footprints_max_km2()
    if area_km2 > cap_km2:
        return {"_error": f"This box is {area_km2:.0f} km2 and the footprint service takes at most "
                f"{cap_km2:.0f} km2.",
                "area_km2": round(area_km2, 1),
                "suggestion": "Zoom in, or fetch the area in several smaller boxes."}

    added, empty = [], []



    if "overture" in sources:
        payload = _overture_call("buildings", (west, south, east, north))
        if payload.get("_error"):
            empty.append({"source": "overture", "status": "unavailable",
                          "message": payload["_error"]})
        else:
            features = payload.get("features") or []
            if features:
                label = _FOOTPRINT_LABELS["overture"]
                name = (f"{args['layer_name']} {label}" if args.get("layer_name") and len(sources) > 1
                        else args.get("layer_name") or label)
                made = _overture_layer(features, name, args)
                if made.get("_error"):
                    return made
                made.update({"source": "overture",
                             "licence": payload.get("licence") or _OVERTURE_LICENCES["buildings"],
                             "attribution": payload.get("attribution") or _OVERTURE_BUILDING_ATTRIBUTION})
                added.append(made)
            else:
                empty.append({"source": "overture", "status": "empty"})
    service_sources = [name for name in sources if name != "overture"]
    if not service_sources:
        if not added:
            return {"features": 0,
                    "message": "Overture published no buildings for this box",
                    "empty_sources": empty,
                    "suggestion": "Add microsoft and openstreetmap to sources for the same box."}
        return {"layers": added, "empty_sources": empty, "crs": "EPSG:4326",
                "area_km2": round(area_km2, 1)}

    def _ask(names: list) -> tuple:
        """(payload, refusal) for one call; exactly one of the two is None."""
        query = urllib.parse.urlencode({"bbox": f"{west},{south},{east},{north}",
                                        "sources": ",".join(names)})
        request = urllib.request.Request(f"{_service('footprints', _FOOTPRINTS_URL)}?{query}",
                                         headers={"User-Agent": _USER_AGENT})



        budget = tuning.limit("net", "footprints_timeout_s", _FOOTPRINT_TIMEOUT)
        try:
            answer = net.fetch(request, timeout=budget, max_bytes=_MAX_DOWNLOAD_SIZE,
                               total_timeout=budget * _TOTAL_TIMEOUT_FACTOR,
                               cache_ttl=600.0)
        except urllib.error.HTTPError as error:



            detail = ""
            try:
                detail = json.loads(error.read().decode("utf-8", "replace")).get("error", "")
            except (ValueError, OSError, AttributeError):
                pass
            return None, {"_error": detail or f"The footprint service answered {error.code}.",
                          "suggestion": "Draw a smaller box." if error.code in (400, 413)
                          else "Try again shortly."}
        except (net.FetchDeadline, net.FetchTruncated, net.NetworkUnreachable, OSError) as error:
            return None, {"_error": f"Could not reach the footprint service: {error}",
                          "suggestion": "Use fetch_osm_data with way[building] for the same box instead."}
        try:
            return json.loads(answer.body), None
        except (ValueError, UnicodeDecodeError):
            return None, {"_error": "The footprint service did not answer with JSON."}

    payload, refusal = _ask(service_sources)
    if refusal is not None:
        return refusal











    if len(service_sources) > 1:
        beaten = [entry.get("sourceId", "") for entry in payload.get("sources", [])
                  if entry.get("status") == "failed" and entry.get("sourceId")]
        for name in beaten:
            alone, _ = _ask([name])
            recovered = next((entry for entry in (alone or {}).get("sources", [])
                              if entry.get("sourceId") == name
                              and (entry.get("featureCollection") or {}).get("features")), None)
            if recovered is None:
                continue
            payload["sources"] = [recovered if entry.get("sourceId") == name else entry
                                  for entry in payload["sources"]]

    for entry in payload.get("sources", []):
        source_id = entry.get("sourceId", "")
        collection = entry.get("featureCollection")
        features = (collection or {}).get("features") or []
        label = _FOOTPRINT_LABELS.get(source_id, source_id or "Buildings")
        name = args.get("layer_name") or label
        if len(sources) > 1:
            name = f"{args['layer_name']} {label}" if args.get("layer_name") else label
        if not features:






            said = {"source": source_id, "status": entry.get("status", "empty")}
            files = entry.get("files") or []
            if files:
                said["files"] = [{"url": f.get("url", ""), "size_mb": round((f.get("sizeBytes") or 0) / 1e6, 1)}
                                 for f in files[:3]]
            empty.append(said)
            continue
        refused = volume_guard.too_many(len(features), args, f"The {name} answer")
        if refused:
            return refused

        uri = _vector_source_from_features(features, name, "footprints")

        def _create(uri=uri, name=name):
            layer = _layer_from_source(uri, name)
            if not layer.isValid():
                return {"_error": f"Could not build a layer from the {name} footprints."}
            QgsProject.instance().addMapLayer(layer)
            return {"layer_name": layer.name(), "feature_count": layer.featureCount()}

        made = _run_on_main_thread(_create, timeout=45)
        if made.get("_error"):
            return made
        made.update({"source": source_id, "licence": entry.get("licence", ""),
                     "attribution": entry.get("attribution", "")})
        added.append(made)

    if not added:
        return {"features": 0,
                "message": "No building footprints published for this box by " + ", ".join(sources),
                "empty_sources": empty,
                "suggestion": 'OpenStreetMap covers most cities; try sources=["openstreetmap"].'}
    return {"layers": added, "empty_sources": empty, "crs": "EPSG:4326",
            "area_km2": round(area_km2, 1), "extracted_on": payload.get("extractedOn", "")}







_OVERTURE_API = "https://aca-terralab-opendata.proudsky-7d379d48.westeurope.azurecontainerapps.io"


_OVERTURE_TILES = "https://stterralabopendata.blob.core.windows.net/overture/2026-08-19"




_OSM_TILES = "https://stterralabopendata.blob.core.windows.net/osm/planet-2026-09"
_OSM_THEMES = ("landuse", "pois", "waterways", "water_areas", "power", "boundaries", "railways")
_OVERTURE_THEMES = ("buildings", "places", "roads", "addresses", "divisions") + _OSM_THEMES




_OVERTURE_MAX_KM2 = 100.0
_OVERTURE_MAX_SPAN_DEG = 1.0





_OVERTURE_TIMEOUT = 75







def _overture_limit() -> int:
    return min(200_000, volume_guard.hard_max_features())




_OVERTURE_TILE_ZOOM = 8
_OVERTURE_MAX_TILES = 4

_OVERTURE_DIVISION_SUBTYPES = ("country", "dependency", "region", "county", "localadmin", "locality",
                               "macrohood", "neighborhood")
_OVERTURE_LICENCES = {"buildings": "ODbL 1.0", "roads": "ODbL 1.0", "divisions": "ODbL 1.0",
                      "places": "CDLA Permissive 2.0",
                      "addresses": "mixed open licences per country",
                      **dict.fromkeys(_OSM_THEMES, "ODbL 1.0")}
_OVERTURE_ATTRIBUTION = "© Overture Maps Foundation, © OpenStreetMap contributors"
_OVERTURE_BUILDING_ATTRIBUTION = (_OVERTURE_ATTRIBUTION + ", Microsoft Building Footprints, "
                                  "Google Open Buildings, Esri Community Maps")
_OSM_ATTRIBUTION = "© OpenStreetMap contributors"


def _overture_attribution(theme: str) -> str:
    if theme in _osm_themes():
        return _OSM_ATTRIBUTION
    return _OVERTURE_BUILDING_ATTRIBUTION if theme == "buildings" else _OVERTURE_ATTRIBUTION


def _overture_source(theme: str) -> str:
    """The name a user reads for where a theme comes from."""
    return "OpenStreetMap" if theme in _osm_themes() else "Overture"


def _overture_layer_name(theme: str, args: dict) -> str:
    return args.get("layer_name") or (f"OSM {theme}" if theme in _osm_themes() else f"Overture {theme}")


def _overture_tile(lon: float, lat: float) -> tuple:
    """The zoom-8 Web Mercator tile a point falls in, as (x, y)."""
    import math

    side = 2 ** _OVERTURE_TILE_ZOOM
    lat = max(-85.05112878, min(85.05112878, lat))
    radians = math.radians(lat)
    x = int((lon + 180.0) / 360.0 * side)
    y = int((1.0 - math.log(math.tan(radians) + 1.0 / math.cos(radians)) / math.pi) / 2.0 * side)
    return (max(0, min(side - 1, x)), max(0, min(side - 1, y)))


def _overture_tiles(box) -> list:
    """Every zoom-8 tile a box touches, in reading order."""
    west, south, east, north = box
    left, top = _overture_tile(west, north)
    right, bottom = _overture_tile(east, south)
    return [(x, y) for y in range(min(top, bottom), max(top, bottom) + 1)
            for x in range(min(left, right), max(left, right) + 1)]


def _osm_themes() -> tuple:
    """The OpenStreetMap themes the hosted pyramid publishes, served or shipped."""
    return tuple(tuning.service_list("osm_themes", _OSM_THEMES))


def _overture_themes() -> tuple:
    return tuple(tuning.service_list("overture_themes", _OVERTURE_THEMES))


def _division_subtypes() -> tuple:
    return tuple(tuning.service_list("division_subtypes", _OVERTURE_DIVISION_SUBTYPES))


def _tiles_base(theme: str) -> str:
    """The published pyramid a theme is read from."""




    if theme in _osm_themes():
        return _service("osm_tiles", _OSM_TILES)
    return _service("overture_tiles", _OVERTURE_TILES)


def _overture_tile_url(theme: str, x: int, y: int) -> str:
    return f"{_tiles_base(theme)}/{theme}/{_OVERTURE_TILE_ZOOM}/{x}/{y}.fgb"


def _overture_matches(feature: dict, wanted: dict) -> bool:
    """The client-side filter: every named field equals the value asked for."""
    properties = feature.get("properties") or {}
    for field, value in wanted.items():
        got = properties.get(field)
        if isinstance(value, (list, tuple, set)):
            if not any(str(got).lower() == str(one).lower() for one in value):
                return False
        elif str(got).lower() != str(value).lower():
            return False
    return True


def _overture_filter_miss(served: list, wanted: dict) -> dict:
    """Why a filter matched none of the features the service did serve."""








    fields: dict[str, int] = {}
    for feature in served[:2000]:
        for key, value in (feature.get("properties") or {}).items():
            if value not in (None, ""):
                fields[key] = fields.get(key, 0) + 1
    out: dict = {}
    absent = [str(field) for field in wanted if str(field) not in fields]
    if absent:
        out["fields_missing"] = absent
        out["fields_available"] = sorted(fields, key=lambda k: -fields[k])[:25]
        return out
    values: dict[str, list] = {}
    for field in wanted:
        counts: dict[str, int] = {}
        for feature in served[:2000]:
            value = (feature.get("properties") or {}).get(str(field))
            if value not in (None, ""):
                text = str(value)
                counts[text] = counts.get(text, 0) + 1
        if counts:
            values[str(field)] = sorted(counts, key=lambda k: -counts[k])[:12]
    if values:
        out["values_present"] = values
    return out


def _overture_trace(headers) -> dict:
    """The few response headers that make a call findable in the server log."""




    out: dict = {}
    if headers is None:
        return out
    for header, key in (("X-Request-Id", "request_id"), ("Retry-After", "retry_after")):
        try:
            value = headers.get(header)
        except Exception:  # noqa: BLE001 - a mapping we cannot read carries no trace
            value = None
        if value:
            out[key] = str(value)
    return out


def _overture_call(theme: str, box, timeout: int = 0, subtype: str = ""):
    """The service's answer for one box, or {"_error": ...} with a suggestion."""
    timeout = timeout or tuning.limit("net", "hosted_timeout_s", _OVERTURE_TIMEOUT)
    west, south, east, north = box
    params = {"bbox": f"{west},{south},{east},{north}", "limit": _overture_limit()}
    if subtype:
        params["subtype"] = subtype
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{_service('overture_api', _OVERTURE_API)}/v1/{theme}?{query}",
                                     headers={"User-Agent": _USER_AGENT})
    try:
        answer = net.fetch(request, timeout=timeout, max_bytes=_MAX_DOWNLOAD_SIZE,
                           total_timeout=timeout * _TOTAL_TIMEOUT_FACTOR, cache_ttl=600.0)
    except urllib.error.HTTPError as error:



        detail = ""
        try:
            detail = json.loads(error.read().decode("utf-8", "replace")).get("error", "")
        except (ValueError, OSError, AttributeError):
            pass


        said = {"_error": detail or f"The Overture service answered {error.code}.",
                "status": int(error.code),
                "suggestion": ("Draw a smaller box." if error.code in (400, 413)
                               else "Wait a moment and ask again.")}
        said.update(_overture_trace(getattr(error, "headers", None)))
        return said
    except (net.FetchDeadline, net.FetchTruncated, net.NetworkUnreachable, OSError) as error:
        said = {"_error": f"Could not reach the Overture service: {error}"}
        if theme in ("buildings", "roads", "places") or theme in _osm_themes():
            said["suggestion"] = f"Use fetch_osm_data over the same box for {theme} instead."
        return said
    try:
        payload = json.loads(answer.body)
    except (ValueError, UnicodeDecodeError):
        return {"_error": "The Overture service did not answer with JSON.",
                **_overture_trace(getattr(answer, "headers", None))}
    if not isinstance(payload, dict):
        return {"_error": "The Overture service answered no object.",
                **_overture_trace(getattr(answer, "headers", None))}


    payload.setdefault("_trace", _overture_trace(getattr(answer, "headers", None)))
    return payload


def _overture_layer(features: list, name: str, args: dict) -> dict:
    """Worker thread writes the file, main thread opens it: one layer, or a refusal."""
    refused = volume_guard.too_many(len(features), args, f"The Overture {name} answer")
    if refused:
        return refused
    uri = _vector_source_from_features(features, name, "overture")

    def _create():
        layer = _layer_from_source(uri, name)
        if not layer.isValid():
            return {"_error": f"Could not build a layer from the Overture answer for {name}."}
        QgsProject.instance().addMapLayer(layer)






        return {"layer_name": layer.name(), "layer_id": layer.id(), "feature_count": layer.featureCount()}

    return _run_on_main_thread(_create, timeout=45)


def _overture_stream(theme: str, box, args: dict) -> dict:
    """The published tiles themselves, opened in place over range requests."""





    layer_name = _overture_layer_name(theme, args)
    urls: list = []
    if theme == "divisions":


        wanted = args.get("filter") if isinstance(args.get("filter"), dict) else {}
        subtype = str(wanted.get("subtype") or "").strip().lower()
        if subtype not in _division_subtypes():
            return {"_error": "Streaming divisions needs the subtype: it is one whole-world file each.",
                    "suggestion": 'Pass filter {"subtype": "..."} with one of: '
                                  + ", ".join(_division_subtypes()) + "."}
        urls.append((subtype, f"{_tiles_base('divisions')}/divisions/{subtype}.fgb"))
    else:
        tiles = _overture_tiles(box)
        if len(tiles) > _OVERTURE_MAX_TILES:
            return {"_error": f"This box spans {len(tiles)} {_overture_source(theme)} tiles and one call "
                    f"opens at most "
                    f"{_OVERTURE_MAX_TILES}.",
                    "suggestion": "Ask for a smaller box, or fetch the area in several calls."}
        urls = [(f"{x}/{y}", _overture_tile_url(theme, x, y)) for x, y in tiles]

    layers, missing = [], []
    for label, url in urls:
        made = _add_vector_over_range_requests(url, f"{layer_name} {label}")
        if made.get("_error"):



            missing.append({"tile": label, "reason": _tile_miss_reason(made)})
            continue
        made["tile"] = label
        layers.append(made)
    refused = [entry for entry in missing if entry["reason"] != "absent"]
    if not layers:
        return {"_error": f"None of the {_overture_source(theme)} {theme} tiles for this box could be opened.",
                "missing_tiles": missing,
                "coverage": "none",
                "suggestion": 'Try mode "clip", which asks the service for the box instead.'}
    out = {"mode": "stream", "theme": theme, "layers": layers, "tiles": [label for label, _ in urls],
           "missing_tiles": missing, "crs": "EPSG:4326",
           "coverage": "partial" if refused else "complete",
           "licence": _OVERTURE_LICENCES.get(theme, ""), "attribution": _overture_attribution(theme),
           "_note": "The tiles are read in place rather than copied: features load as the user pans, and a "
                    'filter or a style applies to the whole tile. Use mode "clip" for a local copy of one '
                    "district."}
    if refused:
        out["_note"] += (f" {len(refused)} of the {len(urls)} tiles covering this box could not be read, so "
                         f"part of the area is missing from the map, not empty.")
    return out


def _tile_miss_reason(made: dict) -> str:
    """Why one stream tile did not open: "absent", "refused" or "unknown"."""
    status = made.get("status") or made.get("_status")
    if status in (404, 410):
        return "absent"
    if isinstance(status, int):
        return "refused"
    text = str(made.get("_error") or "").lower()
    if "404" in text or "not found" in text:
        return "absent"
    if any(word in text for word in ("403", "401", "denied", "forbidden", "refused", "blocked")):
        return "refused"
    return "unknown"


def _subset_string(wanted: dict, keys: set) -> str:
    """An OGR subset string for the plain equality filters a call asked for."""





    clauses = []
    for key in sorted(keys):
        value = wanted.get(key)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(key)):
            return ""
        values = list(value) if isinstance(value, (list, tuple, set)) else [value]
        if not values or not all(isinstance(v, (str, int, float)) and not isinstance(v, bool) for v in values):
            return ""
        quoted = [str(v) if isinstance(v, (int, float)) else "'" + str(v).replace("'", "''") + "'" for v in values]
        clauses.append(f'"{key}" = {quoted[0]}' if len(quoted) == 1 else f'"{key}" IN ({", ".join(quoted)})')
    return " AND ".join(clauses)


def _stream_filter_unsupported(theme: str, wanted) -> set:
    """The filter keys mode "stream" cannot honour for this theme."""
    if not isinstance(wanted, dict) or not wanted:
        return set()
    keys = {str(key) for key in wanted}
    if theme == "divisions":

        keys -= {"subtype"}
    return keys























_CLIP_VERTEX_SCAN = 4096



_CLIP_SUBTYPE_ORDER = ("neighborhood", "macrohood", "locality", "localadmin", "county", "region", "country")


_CLIP_MAX_PARTS = 200



_CLIP_MAX_BOXES = 12


def _rings_of(geometry) -> list:
    """``[(exterior, [holes...]), ...]`` in lon/lat from a GeoJSON geometry."""
    if not isinstance(geometry, dict):
        return []
    kind = str(geometry.get("type") or "")
    if kind == "GeometryCollection":
        out: list = []
        for part in geometry.get("geometries") or []:
            out.extend(_rings_of(part))
        return out
    coords = geometry.get("coordinates")
    if kind == "Polygon" and isinstance(coords, list):
        polygons = [coords]
    elif kind == "MultiPolygon" and isinstance(coords, list):
        polygons = [part for part in coords if isinstance(part, list)]
    else:
        return []
    out = []
    for polygon in polygons:
        rings = []
        for ring in polygon:
            if not isinstance(ring, list):
                continue
            points = [(float(p[0]), float(p[1])) for p in ring
                      if isinstance(p, (list, tuple)) and len(p) >= 2
                      and _is_number(p[0]) and _is_number(p[1])]
            if len(points) >= 4:
                rings.append(points)
        if rings:
            out.append((rings[0], rings[1:]))
    return out


def _polys_bbox(polys: list):
    """``(west, south, east, north)`` of an outline, or None when it is empty."""
    xs: list = []
    ys: list = []
    for exterior, _holes in polys:
        for x, y in exterior:
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _ring_contains(x: float, y: float, ring: list) -> bool:
    """Ray casting: is the point inside this closed ring."""
    inside = False
    count = len(ring)
    previous = count - 1
    for current in range(count):
        xi, yi = ring[current]
        xj, yj = ring[previous]
        if (yi > y) != (yj > y):
            if x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
        previous = current
    return inside


def _outline_contains(x: float, y: float, outline: dict) -> bool:
    """Is the point inside the outline, holes taken out."""
    west, south, east, north = outline["bbox"]
    if not (west <= x <= east and south <= y <= north):
        return False
    for exterior, holes in outline["polys"]:
        if not _ring_contains(x, y, exterior):
            continue
        if any(_ring_contains(x, y, hole) for hole in holes):
            continue
        return True
    return False


def _coords_walk(coords, cap: int):
    """Up to *cap* ``(lon, lat)`` pairs out of any GeoJSON coordinate nesting."""
    stack = [coords]
    seen = 0
    while stack and seen < cap:
        item = stack.pop()
        if not isinstance(item, (list, tuple)) or not item:
            continue
        if len(item) >= 2 and _is_number(item[0]) and _is_number(item[1]):
            seen += 1
            yield float(item[0]), float(item[1])
            continue
        stack.extend(part for part in item if isinstance(part, (list, tuple)))


def _feature_inside(feature: dict, outline: dict) -> bool:
    """Does any part of this feature fall inside the outline."""





    geometry = feature.get("geometry") if isinstance(feature, dict) else None
    if not isinstance(geometry, dict):
        return False
    coords = geometry.get("coordinates")
    if coords is None:
        return False
    box = None
    for x, y in _coords_walk(coords, _CLIP_VERTEX_SCAN):
        if _outline_contains(x, y, outline):
            return True
        box = ((min(box[0], x), min(box[1], y), max(box[2], x), max(box[3], y))
               if box else (x, y, x, y))
    if box is None:
        return False
    west, south, east, north = outline["bbox"]
    return box[0] <= west and box[1] <= south and box[2] >= east and box[3] >= north


def _outline_of_layer(name: str):
    """The outline of a polygon layer already in the project, or None/an error."""





    def _read():


        from qgis.core import QgsGeometry, QgsWkbTypes

        from ._layers import resolve_layer

        layer = resolve_layer(name)
        if layer is None or not hasattr(layer, "getFeatures"):
            return None
        polygon_type = enum_member(QgsWkbTypes, "GeometryType", "PolygonGeometry")
        if QgsWkbTypes.geometryType(layer.wkbType()) != polygon_type:
            return {"_error": f"The layer {layer.name()!r} draws no outline: it is not polygons.",
                    "code": "INVALID_ARGS",
                    "suggestion": "Pass the boundary layer, or the name of the place itself."}
        selected = layer.selectedFeatureCount()
        count = selected or layer.featureCount()
        if count > _CLIP_MAX_PARTS:
            return {"_error": f"{layer.name()!r} holds {count:,} polygons, which is a map and not one outline.",
                    "code": "INVALID_ARGS",
                    "suggestion": "Select the feature to clip to, or pass the place name instead."}
        target = QgsCoordinateReferenceSystem("EPSG:4326")
        xform = None
        if layer.crs().isValid() and layer.crs() != target:
            xform = QgsCoordinateTransform(layer.crs(), target, QgsProject.instance())
        polys: list = []
        budget = VertexBudget()
        for feature in (layer.getSelectedFeatures() if selected else layer.getFeatures()):
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                continue
            too_big = budget.oversize(geometry)
            if too_big or budget.exhausted():
                return {"_error": f"{layer.name()!r} is too detailed to clip to on this machine: "
                                  + (f"one polygon of {too_big:,} vertices" if too_big
                                     else f"more than {budget.total:,} vertices in total") + ".",
                        "code": "INVALID_ARGS",
                        "suggestion": "Simplify it first (native:simplifygeometries through run_processing), "
                                      "select one feature, or pass a bbox instead of clip_to."}
            if xform is not None:
                moved = QgsGeometry(geometry)
                if moved.transform(xform) != 0:
                    continue
                geometry = moved
            polys.extend(_rings_of(json.loads(geometry.asJson(7))))
        if not polys:
            return {"_error": f"{layer.name()!r} holds no polygon to clip to.",
                    "code": "INVALID_ARGS",
                    "suggestion": "Pass a boundary layer with features in it, or the place name."}
        return {"polys": polys, "label": layer.name(), "outline_source": f"layer {layer.name()}",
                "selected_only": bool(selected)}

    return _run_on_main_thread(_read, timeout=30)


def _outline_of_place(name: str):
    """The administrative outline of a named place, out of our own divisions."""






    found = _geocode({"query": name, "limit": 3, "verbose": True})
    hits = [hit for hit in (found.get("results") or []) if _is_number(hit.get("lon"))]
    if not hits:
        return {"_error": f"No place was found under the name {name!r}.",
                "code": "INVALID_ARGS",
                "suggestion": "Check the spelling, add the country, or pass a bbox instead of clip_to."}
    hit = hits[0]
    lon, lat = float(hit["lon"]), float(hit["lat"])


    span = 0.003
    box = (lon - span, lat - span, lon + span, lat + span)
    wanted = _fold(str(name).split(",")[0])
    fallback = None
    for subtype in _CLIP_SUBTYPE_ORDER:
        payload = _overture_call("divisions", box, subtype=subtype)
        if payload.get("_error"):
            continue
        for feature in payload.get("features") or []:
            polys = _rings_of(feature.get("geometry"))
            bounds = _polys_bbox(polys)
            if not bounds:
                continue
            if not _outline_contains(lon, lat, {"polys": polys, "bbox": bounds}):
                continue
            label = str((feature.get("properties") or {}).get("name") or name)
            outline = {"polys": polys, "label": label,
                       "outline_source": f"Overture divisions, subtype {subtype}"}
            if _fold(label) == wanted:
                return outline


            if subtype not in ("neighborhood", "macrohood"):
                fallback = fallback or outline
    if fallback:




        asked = str(name).split(",")[0].strip()
        fallback["note"] = (
            f"No division is named {asked!r} at any level from neighbourhood up, so the clip used "
            f"the smallest division covering it, {fallback['label']}. For the place itself, pass a "
            f"bbox about 2 km around the geocoded point instead of clip_to and say so. Do not ask "
            f"the user.")
        return fallback
    return {"_error": f"{name!r} was found, but no administrative outline covers it.",
            "code": "INVALID_ARGS",
            "suggestion": ("Load the boundary first (theme divisions) and pass its layer name as clip_to, "
                           "or pass a bbox.")}


def _resolve_outline(clip_to: str):
    """``(outline, None)`` for a name, or ``(None, error)``."""




    made = _outline_of_layer(clip_to)
    if made is None:
        made = _outline_of_place(clip_to)
    if not isinstance(made, dict) or made.get("_error"):
        return None, (made if isinstance(made, dict) else {"_error": f"Could not resolve {clip_to!r}."})
    bounds = _polys_bbox(made.get("polys") or [])
    if not bounds:
        return None, {"_error": f"{clip_to!r} resolved to an empty outline.", "code": "INVALID_ARGS",
                      "suggestion": "Pass a bbox instead of clip_to."}
    made["bbox"] = bounds
    return made, None


def _split_box(box, max_km2: float, max_span: float) -> list:
    """*box* cut into a grid of boxes each under both of the service's caps."""






    import math

    west, south, east, north = box
    columns = max(1, int(math.ceil((east - west) / max_span))) if max_span > 0 else 1
    rows = max(1, int(math.ceil((north - south) / max_span))) if max_span > 0 else 1
    area = _bbox_km2(south, west, north, east)
    while max_km2 > 0 and area / (columns * rows) > max_km2:
        if columns * rows >= _CLIP_MAX_BOXES:



            return []
        if (east - west) / columns >= (north - south) / rows:
            columns += 1
        else:
            rows += 1
    if columns * rows > _CLIP_MAX_BOXES:
        return []
    width = (east - west) / columns
    height = (north - south) / rows
    return [(west + column * width, south + row * height,
             west + (column + 1) * width, south + (row + 1) * height)
            for row in range(rows) for column in range(columns)]


def _overture_feature_key(feature: dict):
    """What makes two copies of one feature the same one across two boxes."""
    props = feature.get("properties") if isinstance(feature, dict) else None
    if isinstance(props, dict):
        for field in ("id", "division_id", "osm_id", "gers_id"):
            value = props.get(field)
            if value not in (None, ""):
                return f"{field}:{value}"
    return None


def _overture_boxes(theme: str, box, max_km2: float, max_span: float, args: dict):
    """The service's answer for *box*, in as many calls as its caps need."""





    subtype = ""
    if theme == "divisions":
        wanted = args.get("filter") if isinstance(args.get("filter"), dict) else {}
        subtype = str(wanted.get("subtype") or "").strip().lower()
    pieces = _split_box(box, max_km2, max_span)
    if not pieces:
        west, south, east, north = box
        return None, {"_error": (f"This outline is {_bbox_km2(south, west, north, east):,.0f} km2 and would "
                                 f"take more than {_CLIP_MAX_BOXES} clips of the {max_km2:,.0f} km2 one "
                                 f"{theme} call covers."),
                      "code": limits.CEILING_CODE,
                      "suggestion": 'Use mode "stream", which has no area cap, or clip to a smaller place.'}
    features: list = []
    seen: set = set()
    truncated = False
    meta: dict = {}
    for piece in pieces:
        payload = _overture_call(theme, piece, subtype=subtype)
        if payload.get("_error"):
            if len(pieces) == 1:
                return None, payload


            meta.setdefault("missing_boxes", []).append(
                {"bbox": [round(value, 5) for value in piece], "reason": payload["_error"][:120]})
            continue
        truncated = truncated or bool(payload.get("truncated"))
        for key in ("release", "licence", "attribution"):
            if payload.get(key) and not meta.get(key):
                meta[key] = payload[key]
        if not meta.get("_trace") and payload.get("_trace"):
            meta["_trace"] = payload["_trace"]
        for feature in payload.get("features") or []:
            identity = _overture_feature_key(feature)
            if identity is not None:
                if identity in seen:
                    continue
                seen.add(identity)
            features.append(feature)
        if len(features) >= _overture_limit():
            truncated = True
            break
    if features == [] and meta.get("missing_boxes") and len(meta["missing_boxes"]) == len(pieces):
        return None, {"_error": f"None of the {len(pieces)} clips covering this outline could be served.",
                      "missing_boxes": meta["missing_boxes"],
                      "suggestion": 'Try mode "stream", or ask again in a moment.'}
    meta["truncated"] = truncated
    meta["boxes"] = len(pieces)
    return features, meta


def _fetch_overture(args: dict) -> dict:
    theme = str(args.get("theme") or "").strip().lower()
    if theme not in _overture_themes():
        return {"_error": f"theme must be one of: {', '.join(_overture_themes())}."}
    mode = str(args.get("mode") or "clip").strip().lower()
    if mode not in ("clip", "stream"):
        return {"_error": 'mode must be "clip" (the service clips the box) or "stream" (the tiles in place).'}
    wanted = args.get("filter")
    forced_clip = False



    clip_to = str(args.get("clip_to") or "").strip()
    outline = None
    if clip_to:
        outline, refusal = _resolve_outline(clip_to)
        if outline is None:
            return refusal
        if mode == "stream":



            forced_clip = True
            mode = "clip"

    raw_box = args.get("bbox") or {}
    if outline is not None and not raw_box:
        box = outline["bbox"]
    elif not raw_box:
        return {"_error": "This call says where to fetch with neither a bbox nor clip_to.",
                "code": "INVALID_ARGS",
                "suggestion": 'Pass clip_to with the place name ("Paris"), or a bbox in EPSG:4326 degrees.'}
    else:
        box, problem = _footprint_box(raw_box)
        if box is None:
            return {"_error": problem, "code": "INVALID_ARGS"}
        if outline is not None:



            west = max(box[0], outline["bbox"][0])
            south = max(box[1], outline["bbox"][1])
            east = min(box[2], outline["bbox"][2])
            north = min(box[3], outline["bbox"][3])
            if east <= west or north <= south:
                return {"_error": f"The bbox and {clip_to!r} do not overlap.", "code": "INVALID_ARGS",
                        "suggestion": "Drop the bbox and let clip_to give the box, or drop clip_to."}
            box = (west, south, east, north)
    west, south, east, north = box

    if theme == "divisions" and mode == "stream":




        forced_clip = True
        mode = "clip"
    if mode == "stream":



        unsupported = _stream_filter_unsupported(theme, wanted)
        subset = _subset_string(wanted, unsupported) if unsupported else ""
        if unsupported and not subset:
            return {"_error": (f'mode "stream" opens the published tiles as they are and cannot filter on '
                               f"{', '.join(sorted(unsupported))}."),
                    "code": "INVALID_ARGS",
                    "suggestion": ('Use mode "clip", which asks the service for the box and applies the '
                                   'filter, or drop the filter and set a filter on the layer afterwards.')}
        streamed = _overture_stream(theme, box, args)
        if subset and not streamed.get("_error"):




            def _apply_subset():
                applied = 0
                for made in streamed.get("layers") or []:
                    layer = QgsProject.instance().mapLayer(str(made.get("layer_id") or ""))
                    if layer is not None and layer.setSubsetString(subset):
                        applied += 1
                return applied
            try:
                applied = _run_on_main_thread(_apply_subset)
            except Exception:  # nosec B110 - the tiles are on the map either way
                applied = 0
            streamed["filter"] = subset
            streamed["filter_applied_to"] = applied
            streamed["_note"] = (streamed.get("_note") or "") + f" Provider filter {subset} set on the tile layers."
        return streamed




    max_km2, max_span = volume_guard.hosted_caps().get(theme, (_OVERTURE_MAX_KM2, _OVERTURE_MAX_SPAN_DEG))
    area_km2 = _bbox_km2(south, west, north, east)
    if outline is None:




        if max(east - west, north - south) > max_span:
            return {"_error": f"Each side of the box must stay under {max_span:.0f} degree.",
                    "suggestion": 'Ask for a district or a town at a time, or use mode "stream" for a '
                                  'whole city, or clip_to the place by name.'}
        if area_km2 > max_km2:
            return {"_error": f"This box is {area_km2:.0f} km2 and the Overture service takes at most "
                    f"{max_km2:.0f} km2.",
                    "area_km2": round(area_km2, 1),
                    "suggestion": 'Zoom in, use mode "stream", or pass clip_to with the place name, '
                                  'which splits the outline into clips of that size.'}






    subtype = ""
    if theme == "divisions":
        subtype = str(wanted.get("subtype") or "").strip().lower() if isinstance(wanted, dict) else ""
        if subtype not in _division_subtypes():




            return {"_error": "Divisions are one whole-world file per subtype, so the subtype is not optional.",
                    "suggestion": 'Pass filter {"subtype": "..."} with one of: '
                                  + ", ".join(_division_subtypes())
                                  + '. A commune or a city is "locality", a city district (a Paris or Lyon '
                                  'arrondissement, a borough) "localadmin", a named quarter (Le Marais, '
                                  'Kitsilano, Kreuzberg) "macrohood", a smaller neighbourhood "neighborhood", '
                                  'a department "county", a region "region". Call again with the subtype; '
                                  'do not ask the user.'}
    features, payload = _overture_boxes(theme, box, max_km2, max_span, args)
    if features is None:
        return payload

    served = features
    if isinstance(wanted, dict) and wanted:
        features = [f for f in features if _overture_matches(f, wanted)]
    served_in_box = len(features)
    dropped_outside = 0
    if outline is not None:
        kept = [f for f in features if _feature_inside(f, outline)]
        dropped_outside = len(features) - len(kept)
        features = kept
    name = _overture_layer_name(theme, args)
    if not features:
        truncated = bool(payload.get("truncated"))
        empty = {"feature_count": 0, "theme": theme, "area_km2": round(area_km2, 1),
                 "release": payload.get("release", ""),
                 "truncated": truncated, "served": len(served)}
        empty.update(payload.get("_trace") or {})
        if outline is not None and served_in_box:


            empty["clipped_to"] = outline["label"]
            empty["dropped_outside"] = dropped_outside
            if outline.get("note"):
                empty["clip_note"] = outline["note"]
            empty["message"] = (f"{served_in_box} {theme} came back for the box around {outline['label']}, "
                                f"and none of them fall inside its outline.")
            empty["suggestion"] = "Drop clip_to to keep what the box holds, or try another theme."
            return empty
        if isinstance(wanted, dict) and wanted and served:
            empty.update(_overture_filter_miss(served, wanted))
        if truncated:




            empty["message"] = (f"The service stopped at its limit after {len(served)} {theme}, and none "
                                f"of those matched the filter. Whether the box holds a match further on is "
                                f"not known from this answer.")
            empty["suggestion"] = "Ask again over a smaller box, where the whole answer fits under the limit."
        else:
            empty["message"] = (f"{_overture_source(theme)} has no {theme} in this box"
                                + (" that match the filter" if wanted else "") + ".")
            if empty.get("fields_missing"):




                useful = [f for f in empty["fields_available"]
                          if f not in ("osm_id", "osm_type", "other_tags", "id")
                          and not f.startswith("addr_")]
                empty["suggestion"] = ("Filter on a field this theme carries: "
                                       + ", ".join((useful or empty["fields_available"])[:8]) + ".")
            elif empty.get("values_present"):
                empty["suggestion"] = "Ask again with one of the values in values_present, or drop the filter."
            else:
                empty["suggestion"] = "Widen the box, drop the filter, or try another theme."
        return empty

    made = _overture_layer(features, name, args)
    if made.get("_error"):
        return made
    made.update({
        "theme": theme, "mode": "clip", "crs": "EPSG:4326", "area_km2": round(area_km2, 1),
        "release": payload.get("release", ""),
        "licence": payload.get("licence") or _OVERTURE_LICENCES.get(theme, ""),
        "attribution": payload.get("attribution") or _overture_attribution(theme),
        "truncated": bool(payload.get("truncated")),
        **(payload.get("_trace") or {}),
    })
    if payload.get("boxes", 1) > 1:
        made["boxes"] = payload["boxes"]
    if payload.get("missing_boxes"):
        made["missing_boxes"] = payload["missing_boxes"]
        made["coverage"] = "partial"
    if outline is not None:
        made["clipped_to"] = outline["label"]
        made["outline_source"] = outline["outline_source"]
        made["dropped_outside"] = dropped_outside
        if outline.get("note"):
            made["clip_note"] = outline["note"]
        made["bbox"] = {"south": round(south, 5), "west": round(west, 5),
                        "north": round(north, 5), "east": round(east, 5)}
    else:





        reach = _beyond_bbox(features, (west, south, east, north))
        if reach:
            made["beyond_bbox"] = reach
    if theme == "buildings":


        counts: dict = {}
        for feature in features:
            origin = str((feature.get("properties") or {}).get("source") or "unknown")
            counts[origin] = counts.get(origin, 0) + 1
        made["by_source"] = dict(sorted(counts.items(), key=lambda pair: -pair[1]))
    if made.get("truncated"):
        made["_note"] = "The service stopped at its limit: this is part of the box, not all of it."
    elif forced_clip:
        made["_note"] = ('mode "stream" opens whole tiles, which cannot be clipped to an outline or to one '
                         'division, so this came back clipped instead.')
    return made





_OVERPASS_REJECTED = ("400", "414")


_OVERPASS_HTTP_PREFIX = re.compile(r"^HTTP Error \d{3}:\s*")


def _overpass_reason(exc) -> str:
    """The Error lines out of an Overpass 400 page, or the plain HTTP reason."""





    try:
        body = exc.read(4096).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - a body we cannot read is not worth an error of its own
        body = ""



    with contextlib.suppress(Exception):
        exc.close()



    text = re.sub(r"(?i)</p>|<br\s*/?>", "\n", body)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    seen, kept = set(), []
    for line in text.splitlines():
        line = " ".join(line.split()).strip(" .")
        if not line or not line.lower().startswith("error"):
            continue

        line = line[len("error"):].lstrip(" :")
        if not line or line.lower() in seen:
            continue
        seen.add(line.lower())
        kept.append(line)
        if len(kept) == 3:
            break
    return "; ".join(kept) if kept else f"HTTP {exc.code}"


def _overpass_rejection(errors) -> str:
    """The reason, when every mirror refused the query rather than failing to answer."""





    messages = [_OVERPASS_HTTP_PREFIX.sub("", str(item).split(": ", 1)[-1].strip())
                for item in (errors or [])]
    if not messages:
        return ""
    if not all(any(code in text for code in _OVERPASS_REJECTED) or "parse error" in text.lower()
               for text in messages):
        return ""
    return messages[0]


def _overpass_why(errors) -> str:
    """A short ", because ..." naming the first mirror failure, or nothing."""
    for item in errors or []:
        text = str(item).strip()
        if text:
            return f" ({text[:160]})"
    return ""


def _fetch_osm_data(args: dict) -> dict:
    query = args["query"]



    clamped = volume_guard.clamp_to_cap("fetch_osm_data", args)
    bbox = args["bbox"]
    layer_name = args.get("layer_name", "OSM Data")



    if "xmin" in bbox:
        south = bbox.get("ymin", bbox.get("south"))
        west = bbox.get("xmin", bbox.get("west"))
        north = bbox.get("ymax", bbox.get("north"))
        east = bbox.get("xmax", bbox.get("east"))
    else:
        south = bbox.get("south")
        west = bbox.get("west")
        north = bbox.get("north")
        east = bbox.get("east")
    if None in (south, west, north, east):
        return {"_error": f"bbox must provide either {{south, west, north, east}} or "
                f"{{xmin, ymin, xmax, ymax}}; got keys {sorted(bbox)}.",
                "code": "INVALID_ARGS",
                "suggestion": "Pass the four numbers in EPSG:4326 degrees under one of those two key sets."}

    try:
        area_km2 = _bbox_km2(south, west, north, east)
    except (TypeError, ValueError):
        area_km2 = 0.0
    hosted = volume_guard.hosted_fallback("fetch_osm_data", args)
    if hosted:
        served = _osm_from_hosted(hosted, (west, south, east, north), args, area_km2)
        if clamped and not served.get("_error"):
            served["asked_km2"] = clamped["asked_km2"]
            served["bbox"] = clamped["bbox"]
            served["_note"] = clamped["note"] + " " + str(served.get("_note") or "")
        return served
    refused = _osm_area_refusal(area_km2, (south, west, north, east), args)
    if refused:
        return refused

    bbox_str = f"{south},{west},{north},{east}"
    final_query = query.replace("{{bbox}}", bbox_str)

    final_query = _with_drawable_out(final_query)
    final_query = _with_overpass_settings(final_query)

    post_data = urllib.parse.urlencode({"data": final_query}).encode("utf-8")

    budget = _overpass_timeout()

    def ask(url: str):
        req = urllib.request.Request(url, data=post_data, headers={"User-Agent": _USER_AGENT})
        try:
            return net.fetch(req, timeout=budget, max_bytes=_MAX_DOWNLOAD_SIZE,
                             total_timeout=budget * _TOTAL_TIMEOUT_FACTOR,
                             connect_timeout=_OVERPASS_CONNECT_TIMEOUT).body
        except urllib.error.HTTPError as exc:




            if exc.code not in (400, 414):
                raise
            raise urllib.error.HTTPError(
                exc.url, exc.code, _overpass_reason(exc), exc.headers, None) from exc





    cached = _osm_recent(final_query)
    errors: list = []
    if cached is not None:
        endpoint_used, raw = cached
    else:
        endpoints = _osm_endpoints(area_km2)
        if not endpoints:



            return {"_error": (f"{area_km2:,.0f} square kilometres needs TerraLab's own Overpass instance, "
                               "which this session is not being served."),
                    "area_km2": round(area_km2, 1), "code": limits.CEILING_CODE,
                    "suggestion": (f"Ask for at most {volume_guard.dense_max_km2():,.0f} km\u00b2, or call "
                                   "fetch_overture with the matching theme, which is served from our tiles.")}
        endpoint_used, raw, errors = net.race([(url, lambda u=url: ask(u)) for url in endpoints])
        if raw is not None:
            _osm_remember(final_query, endpoint_used, raw)

    if raw is None:





        rejected = _overpass_rejection(errors)
        if rejected:


            return {"_error": f"Overpass rejected the query: {rejected}",
                    "query": final_query,
                    "suggestion": "The servers are up; fix the query it names, or narrow the box, "
                                  "and call again."}



        turbo_query = urllib.parse.quote(final_query, safe="")
        turbo_url = f"https://overpass-turbo.eu/?Q={turbo_query}&R"
        why = _overpass_why(errors)
        return {"_error": f"Overpass API timed out{why}. Fallback overpass_turbo: {turbo_url}"}

    try:
        osm_data = _json_object(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw, "elements")
    except (json.JSONDecodeError, ValueError):
        return {"_error": "Invalid response from Overpass API"}

    elements = osm_data.get("elements", [])
    if not elements:
        return {"features": 0, "empty": True}

    geojson = _osm_to_geojson(elements, query)
    if not geojson["features"]:




        return {
            "_error": f"Overpass returned {len(elements)} element(s) and none of them could be drawn, "
            "so no layer was added.",
            "query": final_query,
        }
    refused = volume_guard.too_many(len(geojson["features"]), args, "The OpenStreetMap answer")
    if refused:
        refused["area_km2"] = round(area_km2, 1)
        return refused

    uri = _vector_source_from_features(geojson["features"], layer_name, "osm")

    def _create():
        layer = _layer_from_source(uri, layer_name)
        if not layer.isValid():
            return {"_error": "Failed to create layer from OSM data"}
        QgsProject.instance().addMapLayer(layer)
        return {
            "layer_name": layer.name(),
            "layer_id": layer.id(),
            "feature_count": layer.featureCount(),
            "geometry_type": (
                layer.geometryType().name
                if hasattr(layer.geometryType(), "name")
                else str(layer.geometryType())
            ),
            "crs": "EPSG:4326",
            "source_endpoint": endpoint_used,
        }

    mix = _geometry_counts(geojson["features"])

    out = _run_on_main_thread(_create, timeout=30)
    if not out.get("_error"):



        if len(mix) > 1:
            out["geometry_mix"] = mix
        out["area_km2"] = round(area_km2, 1)
        reach = _beyond_bbox(geojson["features"], (west, south, east, north))
        if reach:
            out["beyond_bbox"] = reach
        if clamped:
            out["asked_km2"] = clamped["asked_km2"]
            out["bbox"] = clamped["bbox"]
            out["_note"] = clamped["note"]
    return out



_OSM_ASKS_NODES = re.compile(r"(?<![A-Za-z_])(node|nwr|nw|nr)\s*[\[(;]")


def _osm_to_geojson(elements: list, query: str = "") -> dict:
    with gc_paused():
        return _osm_to_geojson_inner(elements, query)


def _osm_to_geojson_inner(elements: list, query: str = "") -> dict:
    """The Overpass answer as GeoJSON, with the scaffolding left out."""

























    features = []
    nodes = {}



    for index, el in enumerate(elements):





        if el["type"] == "node" and "lon" in el and "lat" in el:
            nodes[el["id"]] = (el["lon"], el["lat"])
        breathe(index)

    ways = {}
    for el in elements:
        if el["type"] == "way":
            ways[el["id"]] = _osm_way_coords(el, nodes)
            breathe(len(ways))





    in_relations = {member.get("ref")
                    for el in elements if el["type"] == "relation"
                    for member in el.get("members", []) if member.get("type") == "way"}

    wants_nodes = not query or bool(_OSM_ASKS_NODES.search(query))
    for index, el in enumerate(elements):
        breathe(index)
        geom = None
        if el["type"] == "node" and "lat" in el and "lon" in el:
            if not el.get("tags") or not wants_nodes:
                continue
            geom = {"type": "Point", "coordinates": [el["lon"], el["lat"]]}
        elif el["type"] == "way":
            if not el.get("tags") and el["id"] in in_relations:
                continue
            geom = _osm_line_or_polygon(ways.get(el["id"]) or [], el.get("tags"))
        elif el["type"] == "relation":
            geom = _osm_relation_geometry(el, ways)
        if geom is None and el["type"] in ("way", "relation"):
            geom = _osm_center_point(el)

        if geom:
            props = dict(el.get("tags", {}).items())
            props["osm_id"] = el["id"]
            props["osm_type"] = el["type"]
            features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": props,
            })




    counts = _geometry_counts(features)
    if len(counts) > 1:
        features.sort(key=lambda f: -counts[f["geometry"]["type"]])
    return {"type": "FeatureCollection", "features": features}








_OSM_ASKS_AREAS = re.compile(r"(?<![A-Za-z_])(way|rel|relation|nwr|nw|nr|wr)\s*[\[(]")
_OSM_RECURSION = re.compile(r">>?\s*;")
_OSM_PLAIN_OUT = re.compile(r"(?<![A-Za-z_])out\s*(?:body|meta)?\s*(?:qt|asc)?\s*;")


def _with_drawable_out(query: str) -> str:
    """`out geom;` in place of an out mode that sends no coordinates."""
    if not _OSM_ASKS_AREAS.search(query) or _OSM_RECURSION.search(query):
        return query
    if not _OSM_PLAIN_OUT.search(query):
        return query
    return _OSM_PLAIN_OUT.sub("out geom;", query)


def _with_overpass_settings(query: str) -> str:
    """Add the settings the request needs, without repeating one the query has."""










    stripped = query.lstrip()
    head = ""
    if stripped.startswith("["):
        end = stripped.find(";")
        head = stripped[:end] if end != -1 else stripped
    additions = ""
    if "[out:" not in head:
        additions += "[out:json]"
    if "[timeout:" not in head:
        additions += f"[timeout:{_overpass_timeout()}]"
    if not additions:
        return query
    if stripped.startswith("["):
        return additions + stripped
    return f"{additions};{stripped}"


def _osm_way_coords(el: dict, nodes: dict) -> list:
    """The coordinates of a way, whichever way Overpass chose to send them."""





    inline = el.get("geometry")
    if isinstance(inline, list):
        return [(p["lon"], p["lat"]) for p in inline
                if isinstance(p, dict) and "lon" in p and "lat" in p]
    return [nodes[nid] for nid in el.get("nodes", []) if nid in nodes]


def _osm_center_point(el: dict) -> dict | None:
    """The point `out center;` sends instead of a shape."""








    center = el.get("center")
    if isinstance(center, dict) and "lon" in center and "lat" in center:
        return {"type": "Point", "coordinates": [center["lon"], center["lat"]]}
    return None







_OSM_LINEAR_KEYS = ("highway", "barrier", "railway", "waterway")

_OSM_AREA_VALUES = {
    "highway": {"services", "rest_area", "platform", "pedestrian"},
    "waterway": {"riverbank", "dock", "boatyard"},
    "railway": {"platform", "station"},
    "barrier": set(),
}


def _osm_is_area(tags: dict | None) -> bool | None:
    """OSM's own answer for a closed way: True area, False line, None undecided."""
    tags = tags or {}
    explicit = str(tags.get("area", "")).strip().lower()
    if explicit in ("yes", "true", "1"):
        return True
    if explicit in ("no", "false", "0"):
        return False
    for key in _OSM_LINEAR_KEYS:
        value = tags.get(key)
        if value is None:
            continue
        return str(value).strip().lower() in _OSM_AREA_VALUES.get(key, ())
    return None


def _osm_line_or_polygon(coords: list, tags: dict | None = None) -> dict | None:
    """A closed run of points, read the way OSM means it."""






    if len(coords) < 2:
        return None
    closed = coords[0] == coords[-1] and len(coords) >= 4
    if closed and _osm_is_area(tags) is not False:
        return {"type": "Polygon", "coordinates": [coords]}
    return {"type": "LineString", "coordinates": coords}


def _osm_relation_geometry(el: dict, ways: dict) -> dict | None:
    """A relation drawn from the ways it is made of."""







    parts = []
    for member in el.get("members", []):
        if member.get("type") != "way":
            continue
        coords = _osm_way_coords(member, {}) or ways.get(member.get("ref")) or []
        if len(coords) >= 2:
            parts.append(coords)
    if not parts and isinstance(el.get("geometry"), list):

        coords = _osm_way_coords(el, {})
        if len(coords) >= 2:
            parts.append(coords)
    if not parts:
        return None
    if all(part[0] == part[-1] and len(part) >= 4 for part in parts):
        return {"type": "MultiPolygon", "coordinates": [[part] for part in parts]}
    return {"type": "MultiLineString", "coordinates": parts}


def _geojson_bounds(features: list, cap: int = 400_000) -> tuple | None:
    """(west, south, east, north) of the features, reading at most ``cap`` points."""
    west = south = east = north = None
    seen = 0

    def walk(coords) -> bool:
        """False when the point budget is spent."""
        nonlocal west, south, east, north, seen
        if not isinstance(coords, (list, tuple)) or not coords:
            return True
        if _is_number(coords[0]) and len(coords) >= 2 and _is_number(coords[1]):
            lon, lat = float(coords[0]), float(coords[1])
            west = lon if west is None or lon < west else west
            east = lon if east is None or lon > east else east
            south = lat if south is None or lat < south else south
            north = lat if north is None or lat > north else north
            seen += 1
            return seen < cap
        return all(walk(item) for item in coords)

    for feature in features:
        geometry = feature.get("geometry") if isinstance(feature, dict) else None
        if isinstance(geometry, dict) and not walk(geometry.get("coordinates")):
            break
    return (west, south, east, north) if west is not None else None


def _beyond_bbox(features: list, box: tuple) -> dict | None:
    """How far past the requested box the answer reaches, when it is far."""










    west, south, east, north = box
    span_x, span_y = abs(east - west), abs(north - south)
    bounds = _geojson_bounds(features)
    if not bounds or span_x <= 0 or span_y <= 0:
        return None
    times = max(abs(bounds[2] - bounds[0]) / span_x, abs(bounds[3] - bounds[1]) / span_y)
    if times < 3.0:
        return None
    return {
        "times_wider": round(times, 1),
        "extent": [round(value, 6) for value in bounds],
        "note": ("A feature crossing the box arrives whole, so one long line stretches the layer past "
                 "it. The features are the right ones; clip before measuring, buffering or counting."),
    }


def _geometry_counts(features: list) -> dict:
    counts: dict[str, int] = {}
    for f in features:
        name = f["geometry"]["type"]
        counts[name] = counts.get(name, 0) + 1
    return counts









def _qms_catalog():
    """Return QMS's {id: DataSourceInfo} catalog, or None if QMS is unavailable."""
    try:
        from quick_map_services.data_sources_list import DataSourcesList
    except Exception:
        return None
    try:
        return DataSourcesList().data_sources
    except Exception:
        return None


def _browser_xyz_connections() -> dict:
    """User's registered XYZ Tile connections from the QGIS Browser panel."""
    from qgis.core import QgsSettings
    out = {}
    settings = QgsSettings()
    settings.beginGroup("qgis/connections-xyz")
    for name in settings.childGroups():
        url = settings.value(f"{name}/url")
        if url:
            out[name] = {
                "url": url,
                "zmax": settings.value(f"{name}/zmax", 19),
                "zmin": settings.value(f"{name}/zmin", 0),
            }
    settings.endGroup()
    return out


def _list_xyz_sources(args: dict) -> dict:
    qms_catalog = _qms_catalog()
    if qms_catalog is not None:
        by_group: dict = {}
        for ds_id, ds in qms_catalog.items():
            group = getattr(ds, "group", None) or "other"
            by_group.setdefault(group, []).append({
                "id": ds_id,
                "name": getattr(ds, "alias", ds_id),
                "type": getattr(ds, "type", None),
            })
        for items in by_group.values():
            items.sort(key=lambda s: (s["name"] or ""))
        result = {
            "provider": "QuickMapServices",
            "groups": by_group,
            "count": len(qms_catalog),
            "_note": "Add any of these with add_xyz_layer(source=<id or name>).",
        }
        browser = _browser_xyz_connections()
        if browser:
            result["browser_xyz_connections"] = sorted(browser.keys())
        return result


    browser = _browser_xyz_connections()
    if browser:
        return {
            "provider": "Browser XYZ connections",
            "sources": [{"id": n, "name": n, "url": c["url"]} for n, c in browser.items()],
            "count": len(browser),
            "_warning": "QuickMapServices not importable; showing your Browser XYZ connections.",
        }
    presets = catalog.basemaps()
    return {
        "provider": "built-in presets",
        "sources": [{"id": k, "name": v["name"], "max_zoom": v["max_zoom"], "attribution": v["attribution"]}
                    for k, v in presets.items()],
        "count": len(presets),
        "_warning": "QuickMapServices not importable; using built-in presets.",
    }






_BASEMAP_SYNONYMS = {"openstreetmap": "osm", "mapnik": "standard"}


def _basemap_words(text: str) -> set:
    """The lowercase words of a basemap id or name, punctuation dropped."""
    return {_BASEMAP_SYNONYMS.get(word, word)
            for word in re.split(r"[^0-9a-z]+", (text or "").lower()) if word}


def _resolve_qms_source(catalog: dict, source: str):
    """Resolve a source string to a QMS DataSourceInfo by id, then alias (ci)."""
    if source in catalog:
        return catalog[source], None
    low = source.strip().lower()
    matches = [ds for ds in catalog.values() if (getattr(ds, "alias", "") or "").strip().lower() == low]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        labels = [f"{getattr(d, 'id', '?')} ({getattr(d, 'group', '?')})" for d in matches]
        return None, {"_error": f"Ambiguous basemap '{source}', matches: {', '.join(labels)}. Use the id.",
                      "_code": "INVALID_ARGS"}



    if _basemap_words(low) <= {"osm", "standard"} and "osm_mapnik" in catalog:
        return catalog["osm_mapnik"], None

    subs = [ds_id for ds_id in catalog if low in ds_id.lower()]
    if len(subs) == 1:
        return catalog[subs[0]], None











    wanted = _basemap_words(source)
    if wanted:
        scored = [(len(_basemap_words(f"{ds_id} {getattr(ds, 'alias', '') or ''}") - wanted)
                   + (1 if str(getattr(ds, "type", "") or "").upper() == "MVT" else 0), ds_id, ds)
                  for ds_id, ds in catalog.items()
                  if wanted <= _basemap_words(f"{ds_id} {getattr(ds, 'alias', '') or ''}")]
        if scored:
            fewest = min(extra for extra, _, _ in scored)
            best = [(ds_id, ds) for extra, ds_id, ds in scored if extra == fewest]
            if len(best) == 1:
                return best[0][1], None
            return None, {"_error": f"Several basemaps match '{source}': "
                          f"{', '.join(ds_id for ds_id, _ in sorted(best)[:6])}. Use the id.",
                          "_code": "INVALID_ARGS"}
    return None, None


def _twin_of(layer, candidates) -> object | None:
    """The layer among ``candidates`` reading exactly the same source, if any."""










    if layer is None:
        return None
    try:
        uri = layer.source()
    except Exception:  # noqa: BLE001 - a layer that cannot say what it reads is not a twin
        return None
    for other in candidates:
        if other is None or other is layer:
            continue



        try:
            same = other.source() == uri
        except Exception:  # noqa: BLE001 - a layer that cannot say what it reads is not a twin
            same = False
        if same:
            return other
    return None


def _stacked(layer) -> dict:
    """Put a new basemap over the basemaps already there, and say where it landed."""








    try:
        place_basemap(layer)
        return cover_report(layer)
    except Exception as exc:  # noqa: BLE001 - a layer that is added is added, placed or not
        log_warning(f"Basemap not placed on top of the basemap block: {exc}")
        return {}


def _preset_named(presets: dict, source: str) -> dict | None:
    """A basemap preset addressed by the name it is published under, not its id."""







    wanted = "".join(ch for ch in str(source or "").lower() if ch.isalnum())
    if not wanted:
        return None
    for preset in presets.values():
        if not isinstance(preset, dict):
            continue
        name = "".join(ch for ch in str(preset.get("name") or "").lower() if ch.isalnum())
        if name and name == wanted:
            return preset
    return None


def _withdrawn_basemap(*urls) -> dict | None:
    """The refusal owed for a tile URL this product may not serve, or None."""










    for url in urls:
        host = urllib.parse.urlsplit(str(url or "")).hostname or ""
        reason = net.withdrawn_reason(host)
        if reason:
            return {"_error": reason, "_code": "INVALID_ARGS",
                    "_suggestion": "Call list_xyz_sources and add one of the basemaps it names."}
    return None


def _add_xyz_layer(args: dict) -> dict:
    source = args["source"]
    name = args.get("name")


    qms_catalog = _qms_catalog()
    if qms_catalog is not None and not _looks_like_xyz_url(source):
        ds, err = _resolve_qms_source(qms_catalog, source)
        if err:
            return err
        if ds is not None:
            refused = _withdrawn_basemap(getattr(ds, "tms_url", ""), getattr(ds, "wms_url", ""))
            if refused:
                return refused
            try:
                from quick_map_services.qgis_map_helpers import add_layer_to_map
            except Exception as exc:
                return {"_error": f"QuickMapServices present but add_layer_to_map unavailable: {exc}"}
            project = QgsProject.instance()
            before = dict(project.mapLayers())
            try:


                with view_kept():
                    add_layer_to_map(ds)
            except Exception as exc:
                return {"_error": f"QMS failed to add '{getattr(ds, 'alias', source)}': {exc}",
                        "_code": "QMS_ADD_FAILED"}
            added = [layer for lid, layer in project.mapLayers().items()
                     if lid not in before and layer is not None]





            twins = [_twin_of(layer, before.values()) for layer in added]
            if added and all(twins):
                kept = twins[0]
                for layer in added:
                    project.removeMapLayer(layer.id())
                if name:
                    kept.setName(name)
                return {
                    "provider": "QuickMapServices",
                    "source_id": getattr(ds, "id", source),
                    "layer_id": kept.id(),
                    "layer_name": kept.name(),
                    "type": getattr(ds, "type", None),
                    "already_present": True,
                    "message": f"'{kept.name()}' already reads this exact source, so it was reused. "
                    "Adding it twice would make the name ambiguous for every later tool.",
                }

            if name and added:
                added[0].setName(name)
            out = {
                "provider": "QuickMapServices",
                "source_id": getattr(ds, "id", source),
                "layer_id": added[0].id() if added else None,
                "layer_name": added[0].name() if added else getattr(ds, "alias", source),
                "type": getattr(ds, "type", None),
            }
            if added:
                out.update(_stacked(added[0]))
            return out


    browser = _browser_xyz_connections()
    if source in browser:
        conn = browser[source]
        return (_withdrawn_basemap(conn["url"])
                or _build_raw_xyz(conn["url"], name or source, conn.get("zmax", 19), conn.get("zmin", 0), source))


    if _looks_like_xyz_url(source):
        return (_withdrawn_basemap(source)
                or _build_raw_xyz(source, name or "XYZ Tiles", 19, 0, source))


    presets = catalog.basemaps()
    builtin = presets.get(str(source).strip().lower()) or _preset_named(presets, source)
    if builtin is not None:
        return _build_raw_xyz(builtin["url"], name or builtin["name"], builtin["max_zoom"], 0, source,
                              attribution=builtin.get("attribution", ""))


    available = sorted(qms_catalog.keys())[:25] if qms_catalog else list(presets.keys())
    return {
        "_error": f"Unknown basemap '{source}'.",
        "_code": "INVALID_ARGS",
        "_suggestion": "Call list_xyz_sources to see QuickMapServices ids/names.",
        "examples": available,
    }


VECTOR_TILE_EXTENSIONS = (".pbf", ".mvt")

_CACHE_STYLE_S = 3600
_MAX_STYLE_BYTES = 8 * 1024 * 1024


def _fetch_gl_style(url: str):
    """The provider's MapBox GL style as a dict, or (None, reason)."""
    try:
        raw = _http_get(url, timeout=20, cache_ttl=_CACHE_STYLE_S)
    except (urllib.error.URLError, OSError) as exc:
        return None, f"style not fetched ({exc})"
    try:
        style = json.loads(raw)
    except ValueError as exc:
        return None, f"style is not JSON ({exc})"
    if not isinstance(style, dict) or "layers" not in style:
        return None, "style is not a MapBox GL style document"
    return style, ""


def _apply_gl_style(layer, style: dict) -> tuple[bool, str]:
    """Convert a MapBox GL style onto a vector tile layer."""





    try:
        from qgis.core import QgsMapBoxGlStyleConversionContext, QgsMapBoxGlStyleConverter
    except ImportError:
        return False, "this QGIS has no MapBox GL style converter"
    converter = QgsMapBoxGlStyleConverter()
    context = QgsMapBoxGlStyleConversionContext()
    try:
        result = converter.convert(style, context)
    except Exception as exc:  # noqa: BLE001 - a style we cannot read is not a failed load
        return False, f"style not applied ({exc})"
    if int(result) != 0:
        return False, f"style not applied ({converter.errorMessage() or 'conversion failed'})"
    renderer = converter.renderer()
    if renderer is not None:
        layer.setRenderer(renderer)
    labeling = converter.labeling()
    if labeling is not None:
        layer.setLabeling(labeling)
    return True, ""


def _resolve_tilejson(url: str):
    """``(template, zmin, zmax, attribution)`` from a TileJSON document, or ``(None, why, 0, "")``."""










    try:
        raw = _http_get(url, timeout=20, cache_ttl=_CACHE_STYLE_S)
    except (urllib.error.URLError, OSError) as exc:
        return None, f"TileJSON not fetched ({exc})", 0, ""
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        return None, f"not a TileJSON document ({exc})", 0, ""
    if not isinstance(doc, dict):
        return None, "not a TileJSON document", 0, ""
    tiles = doc.get("tiles")
    template = ""
    if isinstance(tiles, list):
        for candidate in tiles:
            if isinstance(candidate, str) and _looks_like_xyz_url(candidate):
                template = candidate
                break
    if not template:
        return None, "the TileJSON names no usable tile template", 0, ""
    try:
        zmin = int(doc.get("minzoom", 0))
        zmax = int(doc.get("maxzoom", 14))
    except (TypeError, ValueError):
        zmin, zmax = 0, 14
    return template, zmin, zmax, str(doc.get("attribution") or "")






_OAPIF_ITEMS_RE = re.compile(r"^(?P<collection>.*/collections/[^/]+)/items/?$")


def _oapif_collection(url: str) -> str | None:
    """The collection URL when *url* is an OGC API - Features items endpoint, else None."""
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    found = _OAPIF_ITEMS_RE.match(parts.path)
    if not found:
        return None
    return f"{parts.scheme}://{parts.netloc}{found.group('collection')}"


def _add_oapif_layer(args: dict) -> dict:
    """Add an OGC API - Features collection through the driver that pages it."""












    url = args.get("url") or ""
    collection = _oapif_collection(url)
    if not collection:
        return {"_error": f"{url} is not an OGC API - Features items endpoint.",
                "code": "INVALID_ARGS",
                "suggestion": "Pass a URL of the shape .../collections/<id>/items."}
    name = args.get("name") or collection.rsplit("/", 1)[-1]

    def _create():
        layer = QgsVectorLayer(f"OAPIF:{collection}", name, "ogr")
        if not layer.isValid():
            return {"_invalid": True}
        QgsProject.instance().addMapLayer(layer)
        return {"layer_name": layer.name(), "layer_id": layer.id(),
                "feature_count": layer.featureCount(), "url": collection,
                "provider": "OGC API - Features"}

    out = _run_on_main_thread(_create, timeout=60)
    if out.get("_invalid"):
        return {"_error": f"The OGC API - Features collection at {collection} would not load.",
                "code": "EXECUTION_FAILED",
                "suggestion": ("Check the collection exists with inspect_data_source. A GDAL older than "
                               "3.0 has no OAPIF driver; the items page still reads a page at a time.")}
    return out


def _add_vector_tile_layer(args: dict) -> dict:
    """A vector tile service: real geometry and attributes, not a picture."""






    url = str(args.get("url") or args.get("source") or "").strip()
    tilejson_note = ""
    tilejson_attribution = ""
    resolved_zoom = None
    if url and not _looks_like_xyz_url(url):

        template, a, b, tilejson_attribution = _resolve_tilejson(url)
        if template is None:





            looks_like_style = "style" in urllib.parse.urlparse(url).path.lower()
            suggestion = ("Pass the tile template, for example "
                          "https://host/tms/1.0.0/LAYER/{z}/{x}/{y}.pbf, or the "
                          "TileJSON URL of the service.")
            if looks_like_style:
                suggestion = ("That address looks like a MapLibre style document. It goes in the style "
                              "argument, with the tile template as the source: source "
                              "https://tiles.openfreemap.org/planet, style "
                              "https://tiles.openfreemap.org/styles/liberty.")
            return {"_error": f"{url} is neither a tile template nor a TileJSON document ({a}).",
                    "_code": "INVALID_ARGS", "_suggestion": suggestion}


        probe = template.replace("{z}", "0").replace("{x}", "0").replace("{y}", "0").replace("{s}", "a")
        template_error = security.validate_url(probe)
        if template_error:
            return {"_error": f"The TileJSON at {url} names a tile template that is not fetched: {template_error}",
                    "_code": "PERMISSION_DENIED"}
        tilejson_note = f"tile template read from the TileJSON at {url}"
        url, resolved_zoom = template, (a, b)
    if not _looks_like_xyz_url(url):
        return {"_error": "A vector tile source is a template with {z}, {x} and {y}.",
                "_code": "INVALID_ARGS",
                "_suggestion": "Pass the tile template, for example "
                               "https://host/tms/1.0.0/LAYER/{z}/{x}/{y}.pbf"}
    name = args.get("name") or posixpath.basename(urllib.parse.urlparse(url).path) or "Vector tiles"
    zmin, zmax = _zoom_range(args)


    if resolved_zoom is not None and "zmin" not in args and "zmax" not in args:
        zmin, zmax = resolved_zoom
    style_url = str(args.get("style") or "").strip()
    style, style_note = (None, "")
    if style_url:
        style, style_note = _fetch_gl_style(style_url)

    uri = f"type=xyz&url={encode_uri_url(url)}&zmin={zmin}&zmax={zmax}"

    def _create():
        from qgis.core import QgsVectorTileLayer

        layer = QgsVectorTileLayer(uri, name)
        if not layer.isValid():
            return {"_error": f"QGIS could not open the vector tile service at {url}.",
                    "_code": "INVALID_ARGS",
                    "_suggestion": "Check the template resolves for one tile, and that the zoom range is right."}
        out = {"layer_name": layer.name(), "layer_id": layer.id(), "url": url,
               "provider": "vector tiles", "zmin": zmin, "zmax": zmax}
        if tilejson_note:
            out["resolved_from"] = tilejson_note
        if tilejson_attribution:
            _credit(layer, tilejson_attribution)
            out["attribution"] = tilejson_attribution
        if style is not None:
            applied, why = _apply_gl_style(layer, style)
            out["styled"] = applied
            if not applied:
                out["_note"] = why
        elif style_note:
            out["_note"] = style_note
        elif style_url == "":




            out["_note"] = ("No style was passed, so QGIS draws these tiles with its own default "
                            "renderer and the map will look almost empty. Add the service's MapLibre "
                            "style with the style argument, or style the layer by hand.")
        with view_kept():
            QgsProject.instance().addMapLayer(layer)
        out.update(_stacked(layer))
        return out

    return _run_on_main_thread(_create, timeout=60)


def _zoom_range(args: dict) -> tuple[int, int]:
    def _clamp(value, fallback):
        try:
            return max(0, min(int(value), 24))
        except (TypeError, ValueError):
            return fallback

    zmin = _clamp(args.get("zmin"), 0)
    zmax = _clamp(args.get("zmax"), 19)
    return (zmin, zmax) if zmin <= zmax else (zmax, zmin)


def _looks_like_xyz_url(source: str) -> bool:
    return "{z}" in source and "{x}" in source and "{y}" in source


def _build_raw_xyz(url: str, name: str, zmax, zmin, source_label: str, attribution: str = "") -> dict:
    uri = f"type=xyz&url={encode_uri_url(url)}&zmax={zmax}&zmin={zmin}"
    layer = QgsRasterLayer(uri, name, "wms")
    if not layer.isValid():
        return {"_error": f"Failed to create XYZ layer from: {source_label}"}
    _credit(layer, attribution)




    kept = _twin_of(layer, QgsProject.instance().mapLayers().values())
    if kept is not None:
        return {"layer_name": kept.name(), "layer_id": kept.id(), "source": source_label,
                "already_present": True,
                "message": f"'{kept.name()}' already reads these tiles, so it was reused."}
    with view_kept():
        QgsProject.instance().addMapLayer(layer)
    out = {"layer_name": layer.name(), "layer_id": layer.id(), "source": source_label}
    if attribution:
        out["attribution"] = attribution
    out.update(_stacked(layer))
    return out


def _credit(layer, attribution: str) -> None:
    """Write the provider's credit line on the layer, where QGIS shows it."""







    text = (attribution or "").strip()[:500]
    if not text:
        return
    try:
        layer.setAttribution(text)
        metadata = layer.metadata()
        if not metadata.rights():
            metadata.setRights([text])
            layer.setMetadata(metadata)
    except Exception as exc:  # noqa: BLE001 - the layer still draws without its credit line
        log_warning(f"Attribution not set on {layer.name()}: {exc}")









_WMTS_CAPS_MAX_BYTES = 8 * 1024 * 1024
_WMTS_CAPS_TTL_S = 900
_WMTS_LAYER_RE = re.compile(rb"<Layer>(.*?)</Layer>", re.DOTALL)
_WMTS_IDENTIFIER_RE = re.compile(rb"<ows:Identifier>\s*([^<\s][^<]*?)\s*</ows:Identifier>")
_WMTS_SET_RE = re.compile(rb"<TileMatrixSet>\s*([^<\s][^<]*?)\s*</TileMatrixSet>")
_WMTS_FORMAT_RE = re.compile(rb"<Format>\s*([^<\s][^<]*?)\s*</Format>")
_WMTS_STYLE_RE = re.compile(rb"<Style[^>]*>.*?<ows:Identifier>\s*([^<\s][^<]*?)\s*</ows:Identifier>", re.DOTALL)
_WMTS_SET_DEF_RE = re.compile(
    rb"<TileMatrixSet>\s*<ows:Identifier>\s*([^<\s][^<]*?)\s*</ows:Identifier>.*?"
    rb"<ows:SupportedCRS>\s*([^<\s][^<]*?)\s*</ows:SupportedCRS>", re.DOTALL)


_WMTS_EPSG_RE = re.compile(r"(\d{4,6})\s*$")


def _wmts_capabilities_url(url: str) -> str:
    """The GetCapabilities address of a WMTS, from whatever form of its URL was given."""
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parts.query)
    if any(key.lower() == "service" for key in query) or parts.path.lower().endswith(".xml"):
        return url
    return url + ("&" if "?" in url else "?") + urllib.parse.urlencode(
        {"SERVICE": "WMTS", "REQUEST": "GetCapabilities", "VERSION": "1.0.0"})


def _wmts_describe(url: str) -> dict[str, dict]:
    """``{layer id: {sets, formats, styles}}`` for a WMTS, empty when it will not describe itself."""




    try:
        answer = net.fetch(_wmts_capabilities_url(url), timeout=20, max_bytes=_WMTS_CAPS_MAX_BYTES,
                           total_timeout=25, cache_ttl=_WMTS_CAPS_TTL_S)
    except Exception as exc:  # noqa: BLE001 - a service that will not describe itself still reports
        log_warning(f"WMTS capabilities probe failed for {url}: {exc}")
        return {}
    body = answer.body or b""
    crs_of = {name.decode("utf-8", "replace"): crs.decode("utf-8", "replace")
              for name, crs in _WMTS_SET_DEF_RE.findall(body)}
    out: dict[str, dict] = {}
    for block in _WMTS_LAYER_RE.findall(body):
        found = _WMTS_IDENTIFIER_RE.search(block)
        if not found:
            continue
        layer_id = found.group(1).decode("utf-8", "replace")
        sets = [name.decode("utf-8", "replace") for name in _WMTS_SET_RE.findall(block)]
        out[layer_id] = {
            "sets": sets,
            "formats": [f.decode("utf-8", "replace") for f in _WMTS_FORMAT_RE.findall(block)],
            "styles": [st.decode("utf-8", "replace") for st in _WMTS_STYLE_RE.findall(block)],
            "crs": {name: crs_of.get(name, "") for name in sets},
        }
    return out





_WMTS_NAMED_CRS = {
    "urn:ogc:def:crs:ogc:1.3:crs84": "OGC:CRS84",
    "urn:ogc:def:crs:ogc::crs84": "OGC:CRS84",
    "http://www.opengis.net/def/crs/ogc/1.3/crs84": "OGC:CRS84",
    "crs84": "OGC:CRS84",
}


def _wmts_epsg(supported_crs: str) -> str:
    """``EPSG:<code>`` (or a named CRS) out of any form a SupportedCRS is written in."""
    text = (supported_crs or "").strip()
    named = _WMTS_NAMED_CRS.get(text.lower())
    if named:
        return named
    found = _WMTS_EPSG_RE.search(text)
    return f"EPSG:{found.group(1)}" if found else ""


def _wmts_uri(url: str, layer: str, described: dict) -> tuple[str, dict] | None:
    """The provider URI for one WMTS layer, and what was chosen, or None when it is not there."""
    row = described.get(layer)
    if not row or not row["sets"]:
        return None


    chosen = next((name for name in row["sets"] if _wmts_epsg(row["crs"].get(name, "")) == "EPSG:3857"),
                  row["sets"][0])
    crs = _wmts_epsg(row["crs"].get(chosen, ""))
    if not crs:




        return None, {"unresolved_crs": row["crs"].get(chosen, ""), "tile_matrix_set": chosen}
    img_format = next((f for f in row["formats"] if f.endswith("png")), (row["formats"] or ["image/png"])[0])
    style = (row["styles"] or ["default"])[0]
    uri = (f"url={encode_uri_url(_wmts_capabilities_url(url))}"
           f"&layers={urllib.parse.quote(layer)}"
           f"&styles={urllib.parse.quote(style)}"
           f"&format={urllib.parse.quote(img_format)}"
           f"&tileMatrixSet={urllib.parse.quote(chosen)}"
           f"&crs={crs}")
    return uri, {"tile_matrix_set": chosen, "crs": crs, "format": img_format, "style": style}


def _is_wmts(url: str) -> bool:
    """Whether this address is a WMTS rather than a WMS."""
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parts.query)
    service = next((v[0] for k, v in query.items() if k.lower() == "service"), "")
    return service.upper() == "WMTS" or "wmts" in parts.path.lower()


def _add_wmts_layer(url: str, layer: str, name: str) -> dict:
    """Add one layer of a WMTS, configured from what the service says about it."""









    described = _wmts_describe(url)
    if not described:
        return {"_error": f"No answer from the WMTS at {url} that names any layer.",
                "code": "EXECUTION_FAILED",
                "suggestion": "Check the URL with inspect_data_source."}
    built = _wmts_uri(url, layer, described)
    if built is None:
        close = [name for name in described if layer.lower() in name.lower()][:3]
        shown = close or list(described)[:_WFS_NAMES_SHOWN]
        more = "" if len(described) <= len(shown) else f", and {len(described) - len(shown)} more"
        return {"_error": f"The service does not publish a layer {layer!r}. It offers: "
                f"{', '.join(shown)}{more}.",
                "code": "INVALID_ARGS",
                "suggestion": "Call add_data again with one of those layer names."}
    uri, chosen = built
    if uri is None:
        return {"_error": (f"The tile matrix set {chosen['tile_matrix_set']!r} of layer {layer!r} is published "
                           f"in {chosen['unresolved_crs']!r}, which this plugin cannot turn into a CRS QGIS "
                           f"knows, so the layer was not added."),
                "code": "EXECUTION_FAILED",
                "suggestion": ("Pick a tile matrix set the service publishes in an EPSG code, or load the "
                               "service as a WMS with add_wms_layer.")}

    def _create():
        made = QgsRasterLayer(uri, name, "wms")
        if not made.isValid():
            return {"_invalid": True}
        with view_kept():
            QgsProject.instance().addMapLayer(made)
        out = {"layer_name": made.name(), "layer_id": made.id(), "url": url,
               "wmts_layer": layer, **chosen}
        out.update(_stacked(made))
        return out

    out = _run_on_main_thread(_create, timeout=30)
    if out.get("_invalid"):
        return {"_error": f"The WMTS layer {layer!r} would not load from {url}.",
                "code": "EXECUTION_FAILED",
                "suggestion": (f"The service publishes it in {', '.join(described[layer]['sets'])}. "
                               "Check the layer name with inspect_data_source.")}
    return out


def _add_wms_layer(args: dict) -> dict:
    url = args["url"]
    layers = args["layers"]
    name = args.get("name") or f"WMS - {layers}"
    crs = args.get("crs", "EPSG:4326")
    img_format = args.get("format", "image/png")
    problem = crs_problem(crs)
    if problem:
        return {"_error": problem, "_code": "INVALID_ARGS",
                "_suggestion": "Pass the CRS on its own, without any other provider parameter."}
    if _is_wmts(url):
        return _add_wmts_layer(url, layers, args.get("name") or f"WMTS - {layers}")

    uri = (
        f"url={encode_uri_url(url)}"
        f"&layers={urllib.parse.quote(layers)}"
        f"&crs={crs}"
        f"&format={urllib.parse.quote(img_format)}"
        f"&styles="
    )

    def _create():
        layer = QgsRasterLayer(uri, name, "wms")
        if not layer.isValid():
            return {"_error": f"Failed to connect to WMS: {url}"}
        with view_kept():
            QgsProject.instance().addMapLayer(layer)
        out = {"layer_name": layer.name(), "layer_id": layer.id(), "url": url, "wms_layers": layers}
        out.update(_stacked(layer))
        return out

    return _run_on_main_thread(_create, timeout=30)



















WFS_WARN_FEATURES = 50_000
WFS_WIRE_BYTES_PER_FEATURE = 150






WFS_SILENT_PAGES = (100, 500, 1_000, 2_000, 5_000, 10_000, 25_000)


def _wfs_silent_pages() -> tuple:
    """The page sizes to test a silent truncation against, served or shipped."""






    return tuple(tuning.service_list("wfs_silent_pages", WFS_SILENT_PAGES))


_WFS_HITS_MAX_BYTES = 64 * 1024
_WFS_HITS_TTL_S = 300
_WFS_MATCHED_RE = re.compile(rb'numberMatched\s*=\s*"(\d+)"')


_WFS_TYPENAME_RE = re.compile(rb"<(?:\w+:)?Name>\s*([^<\s][^<]*?)\s*</(?:\w+:)?Name>")



_WFS_CAPS_MAX_BYTES = 12 * 1024 * 1024
_WFS_CAPS_TTL_S = 900
_WFS_NAMES_SHOWN = 12


def _wfs_typenames(url: str) -> list[str]:
    """The type names the service advertises, for a request that named one it does not have."""




    query = {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetCapabilities"}
    probe = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
    try:
        answer = net.fetch(probe, timeout=20, max_bytes=_WFS_CAPS_MAX_BYTES,
                           total_timeout=25, cache_ttl=_WFS_CAPS_TTL_S)
    except Exception as exc:  # noqa: BLE001 - a service that will not describe itself is still reported
        log_warning(f"WFS capabilities probe failed for {url}: {exc}")
        return []
    names: list[str] = []
    for found in _WFS_TYPENAME_RE.finditer(answer.body or b""):
        name = found.group(1).decode("utf-8", "replace").strip()
        if name and ":" in name and name not in names:
            names.append(name)
    return names


_WFS_TYPE_BLOCK_RE = re.compile(rb"<(?:\w+:)?FeatureType\b[^>]*>(.*?)</(?:\w+:)?FeatureType>", re.DOTALL)
_WFS_CRS_RE = re.compile(rb"<(?:\w+:)?(?:Default|Other)CRS>\s*([^<\s][^<]*?)\s*</")
_WFS_CRS_SHOWN = 6


def _wfs_epsg(text: str) -> str:
    """"urn:ogc:def:crs:EPSG::4326" and "EPSG:4326" are the same CRS, said twice."""
    parts = [p for p in text.replace("::", ":").split(":") if p]
    if len(parts) >= 2 and parts[-2].upper() == "EPSG" and parts[-1].isdigit():
        return f"EPSG:{parts[-1]}"
    return text.strip()


def _wfs_type_crs(url: str, typename: str) -> list[str]:
    """The CRS this type name advertises, default first, or []."""




    query = {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetCapabilities"}
    probe = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
    try:
        answer = net.fetch(probe, timeout=20, max_bytes=_WFS_CAPS_MAX_BYTES,
                           total_timeout=25, cache_ttl=_WFS_CAPS_TTL_S)
    except Exception as exc:  # noqa: BLE001 - a service that will not describe itself is still reported
        log_warning(f"WFS capabilities probe failed for {url}: {exc}")
        return []
    wanted = typename.strip()
    for block in _WFS_TYPE_BLOCK_RE.finditer(answer.body or b""):
        body = block.group(1)
        found = _WFS_TYPENAME_RE.search(body)
        if not found:
            continue
        if found.group(1).decode("utf-8", "replace").strip() != wanted:
            continue
        seen: list[str] = []
        for one in _WFS_CRS_RE.finditer(body):
            code = _wfs_epsg(one.group(1).decode("utf-8", "replace"))
            if code and code not in seen:
                seen.append(code)
        return seen
    return []


def _wfs_failure(url: str, typename: str, crs: str, qgis_message: str,
                 hits: int | None = None) -> str:
    """Why the WFS layer is invalid, in the service's own terms rather than ours."""











    detail = (qgis_message or "").strip()
    names = _wfs_typenames(url)
    if names and typename not in names:
        close = [n for n in names if typename.split(":")[-1].lower() in n.lower()][:3]
        shown = close or names[:_WFS_NAMES_SHOWN]
        more = "" if len(names) <= len(shown) else f", and {len(names) - len(shown)} more"
        return (f"The service does not publish a type name {typename!r}. It offers: "
                f"{', '.join(shown)}{more}. Call add_wfs_layer again with one of those."
                + (f" QGIS said: {detail}" if detail else ""))
    if names:
        tail = detail if detail.endswith((".", "!", "?")) else detail + "."
        head = (f"{typename} exists on this service but the layer would not load"
                + (f": {tail}" if detail else "."))






        offered = _wfs_type_crs(url, typename)
        if offered and crs not in offered:
            shown = ", ".join(offered[:_WFS_CRS_SHOWN])
            more = "" if len(offered) <= _WFS_CRS_SHOWN else f", and {len(offered) - _WFS_CRS_SHOWN} more"
            return (f"{head} It does not publish {crs}. Its CRS are: {shown}{more}. "
                    f"Call add_wfs_layer again with crs={offered[0]!r}.")
        if hits is not None and hits > WFS_WARN_FEATURES:
            return (f"{head} The type holds {hits:,} features and the whole of it was asked for. "
                    f"Zoom the canvas to the area of interest and call again, or pass "
                    f"max_features up to {limits.MAX_FEATURES_PER_CALL:,} for a sample."
                    + (f" {crs} is offered, so the CRS is not the problem." if offered else ""))





        return (f"{head} Try a smaller max_features"
                + (f"; {crs} is offered, so the CRS is not the problem." if offered else
                   f", or a srsname other than {crs}."))
    return (f"No answer from the WFS at {url} that names any type. "
            + (f"QGIS said: {detail}. " if detail else "")
            + "Check the URL with inspect_data_source.")


def _wfs_hits(url: str, typename: str, crs: str) -> int | None:
    """How many features the service holds for this type name, or None."""






    query = {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
             "TYPENAMES": typename, "RESULTTYPE": "hits", "SRSNAME": crs}
    probe = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
    try:
        answer = net.fetch(probe, timeout=20, max_bytes=_WFS_HITS_MAX_BYTES,
                           total_timeout=25, cache_ttl=_WFS_HITS_TTL_S)
    except Exception as exc:  # noqa: BLE001 - a service that will not be probed still loads
        log_warning(f"WFS hits probe failed for {typename}: {exc}")
        return None
    found = _WFS_MATCHED_RE.search(answer.body or b"")
    if not found:
        return None
    try:
        return int(found.group(1))
    except ValueError:
        return None


def _wfs_restrict_to_view(hits: int) -> bool:
    """Whether a type name of *hits* features is loaded for the map view only."""






    return hits > WFS_WARN_FEATURES


def _misses_the_view(layer) -> str:
    """Say so when a layer's own extent does not reach the map, else ""."""









    try:
        from qgis.utils import iface as qgis_iface

        canvas = qgis_iface.mapCanvas() if qgis_iface is not None else None
        if canvas is None:
            return ""
        view = canvas.extent()
        if view.isEmpty():
            return ""
        extent = layer.extent()
        if extent.isEmpty():
            return "The layer reports an empty extent, so nothing will draw."
        target = canvas.mapSettings().destinationCrs()
        if layer.crs().isValid() and target.isValid() and layer.crs() != target:
            extent = QgsCoordinateTransform(layer.crs(), target,
                                            QgsProject.instance()).transformBoundingBox(extent)
        if extent.intersects(view):
            return ""
        return ("The layer's own extent does not reach the current view, so the map will look "
                "empty. Either the service published a wrong bounding box, or the data is "
                "somewhere else: zoom_to_layer shows where it says it is.")
    except Exception:  # noqa: BLE001 - a note is never worth an exception
        return ""


def _add_wfs_layer(args: dict) -> dict:
    url = args["url"]
    typename = args["typename"]
    name = args.get("name") or f"WFS - {typename}"
    crs = args.get("crs", "EPSG:4326")
    try:





        max_features = max(1, min(int(args.get("max_features") or 1000), limits.MAX_FEATURES_PER_CALL))
    except (TypeError, ValueError):
        return {"_error": f"max_features must be a whole number from 1 to "
                f"{limits.MAX_FEATURES_PER_CALL}, got {args.get('max_features')!r}.",
                "code": "INVALID_ARGS",
                "suggestion": "Pass a whole number, or leave max_features out for 1000."}

    problem = crs_problem(crs)
    if problem:
        return {"_error": problem, "_code": "INVALID_ARGS",
                "_suggestion": "Pass the CRS on its own, without any other provider parameter."}

    hits = _wfs_hits(url, typename, crs)
    restrict = _wfs_restrict_to_view(hits) if hits is not None else False
    warning = suggestion = ""







    source = QgsDataSourceUri()
    source.setParam("url", url)
    source.setParam("typename", typename)
    source.setParam("srsname", crs)
    source.setParam("version", "2.0.0")
    source.setParam("maxNumFeatures", str(max_features))
    if restrict:
        source.setParam("restrictToRequestBBOX", "1")
    uri = source.uri(False)

    def _create():
        layer = QgsVectorLayer(uri, name, "WFS")
        if not layer.isValid():


            try:
                said = layer.error().summary()
            except Exception:  # noqa: BLE001 - an invalid layer may have no error object
                said = ""
            return {"_invalid": True, "_qgis_message": said}
        QgsProject.instance().addMapLayer(layer)
        out = {
            "layer_name": layer.name(),
            "layer_id": layer.id(),
            "feature_count": layer.featureCount(),
            "url": url,
            "typename": typename,
        }
        missed = _misses_the_view(layer)
        if missed:
            out["_note"] = missed
        return out

    out = _run_on_main_thread(_create, timeout=30)
    if out.get("_invalid"):
        return {"_error": _wfs_failure(url, typename, crs, out.get("_qgis_message") or "", hits)}
    if out.get("_error"):
        return out
    if hits is not None:
        out["features_available"] = hits
        out["estimated_bytes"] = hits * WFS_WIRE_BYTES_PER_FEATURE
        out["restricted_to_view"] = restrict
    if restrict:










        warning = (f"Only the features under the map view are fetched: this type name has "
                   f"{hits:,} of them. An expression filter, get_features or a Processing run "
                   "sees nothing outside the current view, however right the field name is.")
        suggestion = ("Narrow at the source: set_layer_filter sends the expression to the "
                      "service, so the layer holds that subset wherever the map is. Zoom to the "
                      "area first if what you want is the view's features.")
    count = out.get("feature_count")




    silent_page = (hits is None and isinstance(count, int)
                   and count in _wfs_silent_pages() and count < max_features)
    if not restrict and isinstance(count, int) and count > 0 and (
            count == max_features or silent_page or (hits is not None and count < hits)):




        out["truncated"] = True
        if hits is not None:
            warning = (f"Only {count:,} of the {hits:,} features were loaded: the service cuts the "
                       "answer off without saying so.")
        elif silent_page:
            warning = (f"Exactly {count:,} features came back for a request that asked for "
                       f"{max_features:,}, and the service published no total: {count:,} is this "
                       "service's own page, so the layer is probably a fraction of the answer.")
        else:
            warning = (f"Exactly {count:,} features came back, which is the request cap: the layer "
                       "is probably truncated.")
        suggestion = (f"Narrow the request with a bbox or a filter. max_features stops at "
                      f"{limits.MAX_FEATURES_PER_CALL} for one call, so more than that needs a smaller area, "
                      f"not a larger number.")
    if restrict and count == 0 and hits:











        offered = [code for code in _wfs_type_crs(url, typename) if code != crs]
        out["empty_in_view"] = True
        warning = (f"The layer loaded and holds nothing: the service returned no feature under the "
                   f"map view, although this type name has {hits:,} in total.")
        suggestion = (f"Either the view is over ground this type does not cover, or the service "
                      f"disagrees about a bbox in {crs}. Move the view to an area it covers, or "
                      f"call again in the CRS the service uses natively"
                      + (f": it also offers {', '.join(offered[:_WFS_CRS_SHOWN])}." if offered else "."))
    if warning:
        out["warning"] = warning
    if suggestion:
        out["suggestion"] = suggestion
    return out





RANGE_READABLE_EXTENSIONS = (".parquet", ".geoparquet", ".fgb", ".gpkg", ".pmtiles")
_VSICURL_TIMEOUT_S = 120




_WARN_REMOTE_BYTES = 200 * 1024 * 1024
_COUNT_CEILING = 200_000



_RANGE_PROBE_BYTES = 1024


_CACHE_PROBE_S = 300.0


def _remote_size(url: str) -> int:
    """Bytes the server says the file is, or 0 when it will not say."""




    try:
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Range": "bytes=0-0"})






        answer = net.fetch(request, timeout=15, max_bytes=_RANGE_PROBE_BYTES, total_timeout=20,
                           cache_ttl=_CACHE_PROBE_S)






        total = str(answer.headers.get("content-range") or "").rsplit("/", 1)[-1]
        if total.isdigit():
            return int(total)
        length = answer.headers.get("content-length")
        return int(length) if str(length).isdigit() else 0
    except Exception:  # noqa: BLE001 - not knowing the size is not a failure
        return 0


def _human_bytes(size: int) -> str:
    step = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if step < 1024 or unit == "TB":
            return f"{step:.0f} {unit}" if unit in ("B", "KB") else f"{step:.1f} {unit}"
        step /= 1024
    return f"{size} B"


def _tune_gdal_for_range_reads() -> None:
    """Apply the process-wide remote-read settings, once."""







    vsi.apply_persistent()


def _range_readable(url: str) -> bool:
    path = urllib.parse.urlparse(url).path.lower()
    return path.endswith(RANGE_READABLE_EXTENSIONS)


def _extract_remote_vector(source: str, url: str, name: str, sublayer: str | None, bbox: list) -> dict:
    """Cut the box out of a remote indexed file into a local GeoPackage."""








    from osgeo import gdal

    gdal.UseExceptions()
    west, south, east, north = bbox
    directory = create_managed_temp_dir("extract")
    path = os.path.join(directory, f"{_safe_extract_stem(name)}.gpkg")
    kwargs = {"format": "GPKG", "spatFilter": [west, south, east, north],
              "spatSRS": "EPSG:4326", "dstSRS": "EPSG:4326"}
    if sublayer:
        kwargs["layers"] = [str(sublayer)]
    try:
        written = gdal.VectorTranslate(path, source, **kwargs)
    except RuntimeError as exc:
        return {"_error": f"The extract from {url} failed: {exc}",
                "code": "EXECUTION_FAILED",
                "suggestion": "Try a smaller box, or add the source without bbox and work at its own scale."}
    if written is None:
        return {"_error": f"The extract from {url} produced nothing.",
                "code": "EXECUTION_FAILED",
                "suggestion": "Try a larger box: the file may hold nothing here."}
    out_layer = written.GetLayer(0)
    count = int(out_layer.GetFeatureCount()) if out_layer is not None else 0
    layer_in_file = out_layer.GetName() if out_layer is not None else None
    del out_layer
    del written
    return {"path": path, "feature_count": count, "layer_in_file": layer_in_file}


def _safe_extract_stem(text: str) -> str:
    """A file stem from a layer name: letters, digits, dash and underscore."""
    stem = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(text or "extract"))
    return stem.strip("_")[:60] or "extract"


def _add_vector_over_range_requests(url: str, layer_name: str | None, sublayer: str | None = None,
                                    bbox=None) -> dict:
    """Open a remote cloud-native vector file in place, without downloading it."""












    net.check_url(url)
    _tune_gdal_for_range_reads()
    name = layer_name or os.path.splitext(posixpath.basename(urllib.parse.urlparse(url).path))[0] or "layer"
    size = _remote_size(url)
    source = f"/vsicurl/{url}"
    if sublayer:



        if "|" in str(sublayer):
            return {"_error": f"'{sublayer}' is not a layer name: the | character separates provider options.",
                    "code": "INVALID_ARGS",
                    "suggestion": "Pass the layer name on its own; inspect_data_source lists them."}
        source = f"{source}|layername={sublayer}"

    if bbox:
        cut = _extract_remote_vector(source.split("|", 1)[0], url, name, sublayer, bbox)
        if "_error" in cut:
            return cut
        local = cut["path"]
        if cut.get("layer_in_file"):
            local = f"{local}|layername={cut['layer_in_file']}"

        def _create_local():
            layer = QgsVectorLayer(local, name, "ogr")
            if not layer.isValid():
                return {"_error": f"QGIS could not read the extract cut from {url}.",
                        "_code": "EXECUTION_FAILED",
                        "_suggestion": "Add the source without bbox and work at its own scale."}
            QgsProject.instance().addMapLayer(layer)
            return {
                "layer_name": layer.name(),
                "layer_id": layer.id(),
                "url": url,
                "provider": "ogr, local extract",
                "crs": layer.crs().authid(),
                "fields": [f.name() for f in layer.fields()],
                "feature_count": cut["feature_count"],
                "extract_bbox": list(bbox),
                "_note": ("The box was cut out of the remote file through its spatial index and written "
                          "locally, so this layer holds only the area asked for and every read of it is "
                          "local. The whole file was not downloaded."),
            }

        return _run_on_main_thread(_create_local, timeout=_VSICURL_TIMEOUT_S)

    def _create():
        layer = QgsVectorLayer(source, name, "ogr")
        if not layer.isValid():
            return {"_error": f"QGIS could not read {url} over HTTP range requests.",
                    "_code": "INVALID_ARGS",
                    "_suggestion": "The server may not support range requests. Ask for a direct download link "
                                   "to a GeoJSON or GeoPackage extract instead."}
        QgsProject.instance().addMapLayer(layer)
        out = {
            "layer_name": layer.name(),
            "layer_id": layer.id(),
            "url": url,
            "provider": "ogr over /vsicurl/",
            "crs": layer.crs().authid(),
            "fields": [f.name() for f in layer.fields()],
            "_note": "Read in place over HTTP range requests: the whole file is not copied, but the header, "
                     "the spatial index and every block the view touches are fetched, and how much that is "
                     "depends on the format's index, not on the extent alone.",
        }
        if size:
            out["size_bytes"] = size
            out["size"] = _human_bytes(size)
            if size >= _WARN_REMOTE_BYTES:










                out["_note"] += (f" At {_human_bytes(size)} this file is only usable zoomed in. Unless the user "
                                 f"wants it whole, call add_data again with bbox=[west, south, east, north] for "
                                 f"the area of interest: the box is cut out through the file's own index and the "
                                 f"extract is local. Do not filter or list features on this layer as it stands: "
                                 f"without a box, every such read walks the file over the network.")
        if sublayer:
            out["layer"] = sublayer
        else:
            others = _sublayers_of(layer)
            if len(others) > 1:
                out["layers_available"] = others
                out["_note"] += f" This source holds {len(others)} layers; pass layer=<name> to pick another."


        if size and size < _WARN_REMOTE_BYTES:
            count = layer.featureCount()
            if count is not None and 0 <= count <= _COUNT_CEILING:
                out["feature_count"] = int(count)
        return out

    return _run_on_main_thread(_create, timeout=_VSICURL_TIMEOUT_S)







_SINGLE_LAYER_EXTENSIONS = (".fgb", ".geojson", ".json", ".parquet", ".pmtiles", ".shp", ".csv", ".kml")


def _sublayers_of(layer) -> list:
    """Layer names inside a multi-layer container, cheap and best effort."""
    try:
        source = str(layer.source() or "").split("|", 1)[0]


        source = (urllib.parse.urlparse(source).path or source).lower()
        if source.endswith(_SINGLE_LAYER_EXTENSIONS):
            return []
        try:
            from qgis.core import QgsProviderRegistry

            details = QgsProviderRegistry.instance().querySublayers(source, "ogr")
            names = [str(d.name()) for d in details]
        except Exception:  # noqa: BLE001 - older QGIS without querySublayers
            names = [str(entry).split("!!::!!")[-1] for entry in (layer.dataProvider().subLayers() or [])]
        return [n for n in dict.fromkeys(names) if n][:20]
    except Exception:  # noqa: BLE001 - a driver without sublayers is the normal case
        return []


_ARCHIVE_VECTOR_EXTENSIONS = (".shp", ".gpkg", ".geojson")


def _archive_entries(root: str) -> list:
    """Every file name under *root*, at any depth."""
    names = []
    for folder, _dirs, files in os.walk(root):
        names.extend(os.path.join(folder, f) for f in files)
    return names


def _archive_vector_files(root: str) -> dict:
    """The vector files an unpacked archive holds, by extension, deepest last."""







    out = {ext: [] for ext in _ARCHIVE_VECTOR_EXTENSIONS}
    for path in _archive_entries(root):
        ext = os.path.splitext(path)[1].lower()
        if ext == ".json":
            ext = ".geojson"
        if ext in out:
            out[ext].append(path)
    for ext in out:
        out[ext].sort(key=lambda f: (f.count(os.sep), f.lower()))
    return out


def _discard_download(tmp_dir: str, result: dict) -> dict:
    """Drop the temp dir this one call created, then return its error."""




    try:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:  # noqa: BLE001  # nosec B110 - the error being returned is the point
        pass
    return result




_SHAPEFILE_COMPANIONS = (".dbf", ".shx")


def _extract_member(zf, member, tmp_dir: str) -> None:
    """Unpack one archive entry under a name every OS will open."""








    parts = [part for part in member.filename.replace("\\", "/").split("/")
             if part not in ("", ".", "..")]
    if not parts:
        return
    target = os.path.join(tmp_dir, *[_safe_filename(part, "entry") for part in parts])
    if member.is_dir():
        os.makedirs(target, exist_ok=True)
        return
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with zf.open(member) as source, open(target, "wb") as sink:
        shutil.copyfileobj(source, sink)


def _shapefile_is_complete(path: str) -> bool:
    """True when the .dbf and .shx that make a .shp readable sit beside it."""
    stem = os.path.splitext(path)[0]
    siblings = {}
    try:
        directory = os.path.dirname(path) or "."
        for name in os.listdir(directory):
            siblings[name.lower()] = True
    except OSError:
        return False
    base = os.path.basename(stem).lower()
    return all(f"{base}{ext}" in siblings for ext in _SHAPEFILE_COMPANIONS)








_CONTENT_TYPE_EXTS = {"application/zip": ".zip", "application/x-zip-compressed": ".zip",
                      "application/geopackage+sqlite3": ".gpkg", "application/geo+json": ".geojson",
                      "application/json": ".geojson", "application/vnd.google-earth.kml+xml": ".kml",
                      "text/csv": ".csv"}


def _extension_of_download(path_name: str, headers, body: bytes) -> str:
    """The extension the file really has: the path's, the disposition's, the type's, or the bytes'."""
    ext = os.path.splitext(path_name)[1].lower()
    if ext:
        return ext
    disposition = headers.get("content-disposition") or ""
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disposition, re.IGNORECASE)
    if match:
        ext = os.path.splitext(match.group(1).strip())[1].lower()
        if ext:
            return ext
    content_type = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if content_type in _CONTENT_TYPE_EXTS:
        return _CONTENT_TYPE_EXTS[content_type]
    if body[:4] == b"PK\x03\x04":
        return ".zip"
    if body[:15] == b"SQLite format 3":
        return ".gpkg"
    return ""


def _add_vector_from_url(args: dict) -> dict:
    url = args["url"]
    layer_name = args.get("layer_name")

    if _range_readable(url):
        return _add_vector_over_range_requests(url, layer_name, args.get("layer"), args.get("bbox"))





    size = _remote_size(url)
    if size and size > _MAX_DOWNLOAD_SIZE:
        return {"_error": f"That file is {_human_bytes(size)} and a download stops at "
                f"{_human_bytes(_MAX_DOWNLOAD_SIZE)}.",
                "code": "EXECUTION_FAILED",

                "suggestion": "Call find_datasets for the same theme and take a row that streams or is "
                              "served per area, or ask the service's own WFS or API for this area only."}

    try:



        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        answer = net.fetch(req, timeout=_download_timeout(), max_bytes=_MAX_DOWNLOAD_SIZE,
                           total_timeout=_DOWNLOAD_TOTAL_TIMEOUT)
    except net.FetchTooLarge:


        return {"_error": "File too large (max 100 MB)",
                "suggestion": "Take the service's own subset instead (its WFS or API, filtered to "
                              "the area), or the lighter row the catalog names beside this one."}
    except net.FetchDeadline as e:
        return {"_error": f"The download did not finish in time: {e}",
                "suggestion": "Ask the user for a smaller extract, or a direct link to a lighter format."}
    except net.FetchCancelled:
        return {"_error": "The run was stopped.", "code": "CANCELLED",
                "suggestion": "Stop here and wait for the next user message."}
    except (urllib.error.URLError, OSError) as e:
        return {"_error": f"Failed to download: {e}"}
    content = answer.body
    content_type = answer.headers.get("content-type", "")

    parsed = urllib.parse.urlparse(url)



    filename = _safe_filename(posixpath.basename(parsed.path))
    ext = _extension_of_download(filename, answer.headers, content)
    if ext and not filename.lower().endswith(ext):
        filename += ext
    if not ext and "json" in content_type:
        ext = ".geojson"
        filename += ext

    tmp_dir = create_managed_temp_dir("download")
    filepath = os.path.join(tmp_dir, filename)

    with open(filepath, "wb") as f:
        f.write(content)

    others: list = []
    if ext == ".zip":
        try:
            with zipfile.ZipFile(filepath, "r") as zf:
                members = zf.infolist()
                if len(members) > _MAX_ARCHIVE_ENTRIES:
                    return _discard_download(tmp_dir, {
                        "_error": f"The archive holds {len(members)} entries, over the "
                        f"{_MAX_ARCHIVE_ENTRIES} this tool unpacks in one call.",
                        "code": "INVALID_ARGS",
                        "suggestion": "Ask the provider for the layer you need rather than a bulk archive, "
                                      "or name one file inside it with a /vsizip/ path.",
                    })
                extracted = 0
                for member in members:
                    target_path = os.path.realpath(os.path.join(tmp_dir, member.filename))
                    if (
                        not target_path.startswith(os.path.realpath(tmp_dir) + os.sep)
                        and target_path != os.path.realpath(tmp_dir)
                    ):
                        return _discard_download(
                            tmp_dir, {"_error": f"ZIP archive contains an unsafe path: {member.filename}"})
                    extracted += member.file_size






                if extracted > _MAX_EXTRACTED_SIZE:
                    return _discard_download(tmp_dir, {
                        "_error": f"The archive unpacks to {_human_bytes(extracted)}, over the "
                        f"{_human_bytes(_MAX_EXTRACTED_SIZE)} limit for one download.",
                        "suggestion": "Ask the provider's service for the area of interest instead of "
                                      "the whole archive: a WFS with a bbox, or a regional extract.",
                    })



                cancel = net.current_cancel_check()
                for member in members:
                    if cancel is not None and cancel():
                        return _discard_download(tmp_dir, {
                            "_error": "The run was stopped while the archive was being unpacked.",
                            "code": "CANCELLED",
                        })
                    _extract_member(zf, member, tmp_dir)





            found = _archive_vector_files(tmp_dir)
            shp_files, gpkg_files, geojson_files = found[".shp"], found[".gpkg"], found[".geojson"]
            others = [os.path.relpath(f, tmp_dir)
                      for f in (shp_files + gpkg_files + geojson_files)][:10]



            shp_files = [f for f in shp_files if _shapefile_is_complete(f)]
            if shp_files:
                filepath = shp_files[0]
            elif gpkg_files:
                filepath = gpkg_files[0]
            elif geojson_files:
                filepath = geojson_files[0]
            else:
                inside = sorted({os.path.splitext(n)[1].lower() for n in _archive_entries(tmp_dir)} - {""})
                return {
                    "_error": "The ZIP archive holds no .shp, .gpkg or .geojson file"
                              + (f"; it holds {', '.join(inside[:8])}." if inside else "."),
                    "code": "INVALID_ARGS",
                    "suggestion": "Ask the provider for a shapefile, GeoPackage or GeoJSON export, or name "
                                  "the file inside the archive with a /vsizip/ path.",
                }
        except zipfile.BadZipFile:
            return {"_error": f"{url} did not download a valid ZIP archive.", "code": "INVALID_ARGS",
                    "suggestion": "Check the URL: a download page or an error page often arrives instead of "
                                  "the file. inspect_data_source says what the URL really serves."}

    if not layer_name:
        layer_name = os.path.splitext(os.path.basename(filepath))[0]

    final_layer_name = layer_name


    counted = _ogr_feature_count(filepath)
    if counted is not None:
        refused = volume_guard.too_many(counted, args, os.path.basename(filepath))
        if refused:
            return refused
    final_filepath = _vector_uri_for(filepath)

    def _create():
        layer = QgsVectorLayer(final_filepath, final_layer_name, "ogr")
        if not layer.isValid():
            return {"_error": f"QGIS could not open {os.path.basename(final_filepath)} as a vector layer: "
                    f"{layer.error().summary() or 'the format is unsupported or the file is empty'}.",
                    "code": "INVALID_ARGS",
                    "suggestion": "inspect_data_source says what the URL really serves; add_data with "
                                  "kind='raster' loads an image instead."}
        if counted is None:
            refused = volume_guard.too_many_features(layer, args, os.path.basename(final_filepath))
            if refused:
                return refused
        QgsProject.instance().addMapLayer(layer)
        return {
            "layer_name": layer.name(),
            "layer_id": layer.id(),
            "feature_count": layer.featureCount(),
            "geometry_type": (
                layer.geometryType().name
                if hasattr(layer.geometryType(), "name")
                else str(layer.geometryType())
            ),
            "crs": layer.crs().authid(),
            "fields": [f.name() for f in layer.fields()],
        }

    out = _run_on_main_thread(_create, timeout=30)


    if isinstance(out, dict) and out.get("_error") is None and len(others) > 1:
        out["file_opened"] = os.path.relpath(filepath, tmp_dir)
        out["files_available"] = others
    return out


def _get_route(args: dict) -> dict:
    waypoints = args["waypoints"]
    profile = args.get("profile", "driving")
    layer_name = args.get("layer_name", "Route")

    if len(waypoints) < 2:
        return {"_error": "At least 2 waypoints required"}
    if len(waypoints) > _MAX_ROUTE_WAYPOINTS:
        return {"_error": f"Too many waypoints (max {_MAX_ROUTE_WAYPOINTS} per route)"}

    coords_str = ";".join(f"{wp['lon']},{wp['lat']}" for wp in waypoints)
    base = _osrm_base(profile)
    url = f"{base}/{coords_str}?overview=full&geometries=geojson&steps=false"

    _osrm_hint = (
        "The public OSRM server (routing.openstreetmap.de) is rate-limited and "
        "frequently slow or down. Retry, reduce waypoints, or for measuring straight-line "
        "distance use measure_distance instead."
    )
    failure = None
    result = {}
    try:
        budget = tuning.limit("net", "route_timeout_s", _ROUTE_TIMEOUT)
        data = _http_get(url, timeout=budget, cache_ttl=_CACHE_CATALOG_S)
    except (urllib.error.URLError, OSError) as e:
        reason = str(e) or repr(e) or "network timeout"
        failure = {
            "_error": f"OSRM routing request failed: {reason}",
            "_code": "OSRM_UNREACHABLE",
            "_suggestion": _osrm_hint,
        }
    else:
        try:
            result = json.loads(data)
        except (ValueError, TypeError) as e:
            snippet = (data[:200].decode("utf-8", "replace") if isinstance(data, (bytes, bytearray))
                       else str(data)[:200])
            failure = {
                "_error": f"OSRM returned a non-JSON response ({e}): {snippet}",
                "_code": "OSRM_BAD_RESPONSE",
                "_suggestion": _osrm_hint,
            }
        else:
            if result.get("code") != "Ok":
                detail = result.get("message") or result.get("code") or "unknown OSRM error"
                failure = {"_error": f"OSRM error: {detail}", "_code": "OSRM_ERROR",
                           "_suggestion": _osrm_hint}
            elif not result.get("routes"):
                failure = {
                    "_error": "No route found between the given waypoints",
                    "_code": "INVALID_ARGS",
                    "_suggestion": _osrm_hint,
                }

    fell_back = failure is not None
    if fell_back:



        answer = _backend_geo("/geo/route", {"point": [f"{wp['lat']},{wp['lon']}" for wp in waypoints],
                                             "profile": profile}, cache_ttl=_CACHE_CATALOG_S)
        geometry = (answer or {}).get("geometry")
        if not geometry:
            return failure
        distance_km = round(float(answer["distance_m"]) / 1000, 2)
        duration_min = round(float(answer["duration_s"]) / 60, 1)
    else:
        route = result["routes"][0]
        distance_km = round(route["distance"] / 1000, 2)
        duration_min = round(route["duration"] / 60, 1)
        geometry = route["geometry"]

    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "distance_km": distance_km,
                "duration_min": duration_min,
                "profile": profile,
            },
        }],
    }
    geojson_str = json.dumps(geojson)

    def _create():
        layer = _layer_from_geojson_str(geojson_str, layer_name, "route")
        if not layer.isValid():
            return {"_error": "Failed to create route layer"}
        QgsProject.instance().addMapLayer(layer)
        return {
            "layer_name": layer.name(),


            "layer_id": layer.id(),
            "distance_km": distance_km,
            "duration_min": duration_min,
            "profile": profile,
            "waypoint_count": len(waypoints),
            "note": ("The public OSRM demo server did not answer, so this route came from the AI Agent "
                     "backend's fallback. Attribution: (c) Microsoft, (c) TomTom."
                     if fell_back else
                     "Routed by the public OSRM demo server run by FOSSGIS: a demonstration service with no "
                     "guarantee, not for production use. Credit OSRM and OpenStreetMap contributors."),
        }

    return _run_on_main_thread(_create, timeout=10)


def _measure_distance(args: dict) -> dict:
    d = QgsDistanceArea()
    d.setEllipsoid("WGS84")
    d.setSourceCrs(
        QgsCoordinateReferenceSystem("EPSG:4326"),
        QgsProject.instance().transformContext(),
    )

    p1 = QgsPointXY(args["from_lon"], args["from_lat"])
    p2 = QgsPointXY(args["to_lon"], args["to_lat"])

    dist_m = d.measureLine(p1, p2)
    return {
        "distance_m": round(dist_m, 1),
        "distance_km": round(dist_m / 1000, 3),
    }


_LOCAL_VECTOR = {".geojson", ".json", ".gpkg", ".kml", ".kmz", ".shp", ".zip", ".gml", ".fgb", ".sqlite", ".parquet"}
_LOCAL_RASTER = {".tif", ".tiff", ".geotiff", ".jp2", ".img", ".vrt", ".asc", ".nc", ".png", ".jpg"}


def _inspect_local_file(path: str) -> dict:
    """A file on disk: what it is and the one call that loads it."""
    ext = os.path.splitext(path)[1].lower()
    name = os.path.splitext(os.path.basename(path))[0] or "dataset"
    out = {"source_family": "local_file", "path": path, "format": ext.lstrip("."),
           "size_bytes": os.path.getsize(path)}
    if ext in CSV_EXTENSIONS:
        from .csv_loader import sniff
        try:
            info = sniff(path)
        except OSError as exc:
            return {"_error": f"Failed to read {path}: {exc}"}
        out.update({
            "delimiter": info["delimiter"], "decimal_separator": info["decimal"],
            "columns": info["header"][:40], "rows_sampled": info["rows"],
            "x_column": info["lon"], "y_column": info["lat"], "wkt_column": info["wkt"],
        })
        out["import_method"] = "add_vector_layer"
        out["import_arguments"] = {"path": path, "name": name}
        if info["lat"] and info["lon"] or info["wkt"]:
            out["message"] = ("Loads as points through the delimited text provider; the delimiter, "
                              "decimal comma and coordinate columns are handled for you.")
            if info.get("metric"):
                out["message"] += " Coordinates look projected: pass crs=<EPSG code> if you know it."
        else:
            out["message"] = "No coordinate column: loads as an attribute table. Join it or geocode an address field."
        return out
    if ext in _LOCAL_VECTOR:
        out.update({"import_method": "add_data", "import_arguments": {"source": path, "name": name},
                    "message": "Direct vector import is available."})
        if ext in (".gpkg", ".sqlite", ".gdb", ".kml", ".kmz", ".gml"):
            from .core_tools import _sublayer_names, describe_sublayers
            names = _sublayer_names(path)
            if len(names) > 1:
                out["layers"] = describe_sublayers(path, names)
                out["message"] = (f"Holds {len(names)} layers. Call add_data with layer=<name> for each one "
                                  "wanted; the list above already gives geometry, count and CRS.")
        return out
    if ext in _LOCAL_RASTER:
        out.update({"import_method": "add_raster_layer", "import_arguments": {"path": path, "name": name},
                    "message": "Direct raster import is available."})
        return out
    from .layer_io_tools import point_cloud_provider
    provider = point_cloud_provider(path)
    if provider:
        out.update({"import_method": "add_data",
                    "import_arguments": {"source": path, "name": name, "kind": "pointcloud"},
                    "provider": provider,
                    "message": ("A point cloud. It loads as a point cloud layer, not a vector one, and the "
                                "pdal: algorithms build a DEM or a canopy height model from it.")})
        return out
    if ext in {".txt", ".md", ".pdf", ".docx"}:
        out.update({"message": "A document, not a dataset. Read it with read_text if the user wants its content."})
        return out
    out["message"] = "Unknown extension. Try add_vector_layer, then add_raster_layer."
    return out


def _inspect_is_remote(args: dict) -> bool:
    """inspect_data_source runs off-thread only for a URL."""

    url = str(args.get("url") or "").strip()
    return url.lower().startswith(("http://", "https://"))


_DIRECT_IMPORT_EXTS = frozenset({".geojson", ".json", ".gpkg", ".kml", ".csv", ".zip", ".shp"})








_HOSTED_DEPARTMENT_PATHS = (
    (re.compile(r"(/ign/rgealti/5m/rgealti-5m-D)(\d{1,2}|2[ab])(\.tif)$", re.IGNORECASE), 3),
    (re.compile(r"(/reference/ign/lidarhd-mnt1m/)(\d)(\.tif)$", re.IGNORECASE), 2),
)


def hosted_department_url(url: str) -> str:
    """The same hosted tile with its department code at the width the store uses."""
    if "stterralabopendata.blob.core.windows.net" not in (url or ""):
        return url
    parts = urllib.parse.urlsplit(url)
    for pattern, width in _HOSTED_DEPARTMENT_PATHS:
        match = pattern.search(parts.path)
        if match:
            head, code, tail = match.groups()
            code = code.upper().zfill(width)
            path = parts.path[:match.start()] + head + code + tail
            return urllib.parse.urlunsplit(parts._replace(path=path))
    return url


_FORMAT_SEGMENTS = {"geojson": ".geojson", "gpkg": ".gpkg", "kml": ".kml",
                    "csv": ".csv", "shp": ".shp", "shapefile": ".zip", "zip": ".zip"}


def _format_named_by_url(url: str) -> str | None:
    """The extension a format-in-the-path export URL stands for, else None."""





    parts = urllib.parse.urlparse(url)
    last = parts.path.rstrip("/").rsplit("/", 1)[-1].lower()
    if last in _FORMAT_SEGMENTS:
        return _FORMAT_SEGMENTS[last]
    for value in urllib.parse.parse_qs(parts.query).get("format", []):
        if value.lower() in _FORMAT_SEGMENTS:
            return _FORMAT_SEGMENTS[value.lower()]
    return None


def _inspect_by_head(url: str, ext: str) -> dict | None:
    """The direct-import answer for a file URL, from a HEAD instead of the body."""




    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": _USER_AGENT})
    try:


        answer = net.fetch(req, timeout=_PORTAL_TIMEOUT, max_bytes=1024,
                           total_timeout=_PORTAL_TIMEOUT)
    except (urllib.error.URLError, OSError):
        return None
    final_url = answer.url or url
    path = urllib.parse.urlparse(final_url).path.lower()
    final_ext = os.path.splitext(path)[1] or ext
    if final_ext not in _DIRECT_IMPORT_EXTS:
        return None
    result = _build_direct_import_result(
        final_url, os.path.basename(path) or "dataset", "vector_file", final_ext.lstrip("."))
    length = next((str(v) for k, v in (answer.headers or {}).items()
                   if str(k).lower() == "content-length"), "").strip()
    if length.isdigit():
        result["size_bytes"] = int(length)
    return result










_INSPECT_HTTP_ADVICE: dict[int, tuple[str, str, str]] = {
    400: ("INVALID_ARGS", "the server rejected the request itself",
          "A query string this service does not accept. Ask for its capabilities document, "
          "or drop the parameters and probe the base URL."),
    401: ("EXECUTION_FAILED", "the service wants credentials",
          "This source is not open. Say so and look for an open mirror with find_datasets "
          "rather than retrying."),
    403: ("EXECUTION_FAILED", "the service refused us",
          "Either the source needs an account or the host blocks unknown clients. Do not retry the "
          "same URL; find another route to the same data."),
    404: ("INVALID_ARGS", "there is nothing at this address",
          "The path is wrong, not the service. Probe the parent directory or the service root, "
          "and read the link out of what it lists."),
    405: ("INVALID_ARGS", "the service refuses this method",
          "Usually a service endpoint asked for as a file. Add the service's own query "
          "(WFS GetCapabilities, an API's collections path) instead of the bare URL."),
    409: ("INVALID_ARGS", "the request conflicts with what the service holds",
          "Two parameters disagree, most often a format or a version the collection does not "
          "publish. Ask the service what it offers before asking again."),
    410: ("INVALID_ARGS", "this address is gone for good",
          "The dataset moved or was withdrawn. Search for its current home; retrying cannot work."),
    429: ("EXECUTION_FAILED", "the host is rate limiting us",
          "Wait before asking this host again, and do not open more requests to it in the meantime."),
}
_INSPECT_SERVER_ERROR = ("EXECUTION_FAILED", "the service failed on its side",
                         "The address looks right and the server broke. This is the one case worth "
                         "one retry; if it fails again, use another source.")


def _inspect_failure(url: str, exc: Exception) -> dict:
    """The refusal, with the status named and the next move stated."""
    status = getattr(exc, "code", None)
    if isinstance(status, int):
        code, what, suggestion = _INSPECT_HTTP_ADVICE.get(
            status, _INSPECT_SERVER_ERROR if status >= 500 else
            ("EXECUTION_FAILED", "the service refused the request", "Read the status and change the address."))
        return {"_error": f"HTTP {status} from {url}: {what}.", "code": code,
                "status": status, "suggestion": suggestion}
    if isinstance(exc, (net.FetchDeadline, TimeoutError)):
        return {"_error": f"The probe of {url} ran past its deadline.", "code": "EXECUTION_FAILED",
                "suggestion": "The host is slow rather than wrong. Ask for a smaller extract, "
                              "or use a source that answers."}
    if isinstance(exc, net.FetchTooLarge):
        return {"_error": f"{url} serves more than this probe reads.", "code": "EXECUTION_FAILED",
                "suggestion": "It is a bulk file, not a service description. Pass it straight to "
                              "add_data instead of inspecting it."}
    return {"_error": f"Failed to inspect source: {exc}", "code": "EXECUTION_FAILED",
            "suggestion": "The host did not answer at all. Check the domain, or use another source."}


def _inspect_data_source(args: dict) -> dict:
    url = hosted_department_url(str(args["url"]).strip())
    local = url[7:] if url.startswith("file://") else url
    local = os.path.expanduser(local)
    if os.path.isfile(local):
        return _inspect_local_file(os.path.abspath(local))
    if not url.lower().startswith(("http://", "https://")):
        return {"_error": f"Not a URL and no file at this path: {url}",
                "suggestion": "Check the path with the user, or give a direct download URL."}





    head_ext = os.path.splitext(urllib.parse.urlparse(url).path.lower())[1]
    if head_ext in _DIRECT_IMPORT_EXTS and head_ext != ".json":
        probed = _inspect_by_head(url, head_ext)
        if probed is not None:
            return probed





    named = _format_named_by_url(url)
    if named:
        return _inspect_by_head(url, named) or _build_direct_import_result(
            url, os.path.basename(urllib.parse.urlparse(url).path.rstrip("/")) or "dataset",
            "vector_file", named.lstrip("."))

    try:
        body, headers, final_url = _http_fetch(url, timeout=_INSPECT_TIMEOUT, cache_ttl=_CACHE_CATALOG_S)
    except (urllib.error.URLError, OSError) as e:
        return _inspect_failure(url, e)

    content_type = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    path = urllib.parse.urlparse(final_url).path.lower()
    ext = _extension_of_download(path, headers, body)
    if "json" in content_type and ext == ".geojson" and not path.endswith((".json", ".geojson")):
        ext = ""
    text = body.decode("utf-8", "ignore")

    if ext in _DIRECT_IMPORT_EXTS:
        return _build_direct_import_result(
            final_url,
            os.path.basename(path) or "dataset",
            "vector_file",
            ext.lstrip(".") or content_type,
        )

    if "xml" in content_type or text.lstrip().startswith("<?xml"):
        return _inspect_xml_payload(final_url, text)

    if "json" in content_type or text.lstrip().startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {
                "source_family": "unknown",
                "final_url": final_url,
                "content_type": content_type,
                "message": "Response looked like JSON but could not be parsed.",
            }
        result = _inspect_json_payload(final_url, payload)
        result["content_type"] = content_type
        return result

    return {
        "source_family": "unknown",
        "final_url": final_url,
        "content_type": content_type or "unknown",
        "message": "Could not classify this source. Try a direct download URL or a service capabilities endpoint.",
    }


_DEFAULT_FORMAT_PREFERENCE = "geojson"



_FORMAT_PREFERENCE_WORDS = {
    "geojson": "geojson",
    "geo json": "geojson",
    "gpkg": "gpkg",
    "geopackage": "gpkg",
    "geo package": "gpkg",
    "shp": "shp",
    "shapefile": "shp",
    "shape file": "shp",
    "any": "any",
}


_PORTAL_RESULT_CAP = 15


def _portal_by_name(value) -> dict | None:
    """The portal the model named, by its id or by the name we show it."""









    wanted = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
    if not wanted:
        return None
    for key, portal in _open_data_portals().items():
        for spelling in (key, portal.get("name", "")):
            if re.sub(r"[^a-z0-9]+", "", str(spelling).lower()) == wanted:
                return portal
    return None


def _normalise_format_preference(value) -> str:
    """The hint the model wrote, read as one of the four formats the search knows."""












    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    if not text:
        return _DEFAULT_FORMAT_PREFERENCE
    first, position = _DEFAULT_FORMAT_PREFERENCE, len(text)
    for word, fmt in _FORMAT_PREFERENCE_WORDS.items():
        found = re.search(rf"\b{word}\b", text)
        if found and found.start() < position:
            first, position = fmt, found.start()
    return first


def _search_open_data(args: dict) -> dict:
    query = args["query"]
    portal_id = args.get("portal")
    portal_url = args.get("portal_url")
    portal_type = args.get("portal_type", "opendatasoft")
    format_pref = _normalise_format_preference(args.get("format_preference"))




    try:
        cap = int(args.get("limit") or _PORTAL_RESULT_CAP)
    except (TypeError, ValueError):
        cap = _PORTAL_RESULT_CAP
    cap = max(1, min(cap, _PORTAL_RESULT_CAP))

    if portal_url:
        custom_portal = _build_custom_portal(portal_url, portal_type)
        if custom_portal is None:
            return {"_error": f"Unsupported portal_type '{portal_type}' for custom portal_url"}
        portals_to_search = [custom_portal]
    elif portal_id:
        portal = _portal_by_name(portal_id)
        if not portal:
            named = ", ".join(f"{key} ({p['name']})" for key, p in _open_data_portals().items())
            return {"_error": f"Unknown portal '{portal_id}'. Available: {named}"}
        portals_to_search = [portal]
    else:
        portals_to_search = list(_open_data_portals().values())

    all_results = []
    errors = []

    def _ask_portal(portal: dict, text: str) -> list:
        if portal["api_type"] == "opendatasoft":
            text = _ods_where(text)
        url = portal["search_url"].format(query=urllib.parse.quote(text))
        budget = tuning.limit("net", "portal_timeout_s", _PORTAL_TIMEOUT)
        parsed = json.loads(_http_get(url, timeout=budget, cache_ttl=_CACHE_CATALOG_S))
        if portal["api_type"] == "data_gouv":
            return _parse_data_gouv_results(parsed, format_pref)
        if portal["api_type"] == "opendatasoft":
            return _parse_opendatasoft_results(parsed, portal["base_url"], format_pref)
        if portal["api_type"] == "ckan":
            return _parse_ckan_results(parsed, format_pref)
        if portal["api_type"] == "socrata":
            return _parse_socrata_results(parsed)
        if portal["api_type"] == "arcgis_hub":
            return _parse_arcgis_hub_results(parsed)
        return []

    def _query_portal(portal: dict) -> tuple[list, str | None]:
        try:
            results = _ask_portal(portal, query)
            asked = query





            if not results:
                for narrower in _portal_narrowings(query):
                    results = _ask_portal(portal, narrower)
                    if results:
                        asked = narrower
                        break

            for r in results:
                r["portal"] = portal["name"]
                if asked != query:
                    r["matched_query"] = asked
            return results, None
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            return [], f"{portal['name']}: {e}"
        except Exception as e:  # noqa: BLE001 - an odd payload loses one portal, not the search
            return [], f"{portal['name']}: unexpected answer ({type(e).__name__}: {e})"

    if len(portals_to_search) == 1:
        results, error = _query_portal(portals_to_search[0])
        all_results.extend(results)
        if error:
            errors.append(error)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(portals_to_search)) as pool:
            for results, error in pool.map(_query_portal, portals_to_search):
                all_results.extend(results)
                if error:
                    errors.append(error)

    response = {
        "results": all_results[:cap],
        "count": len(all_results),
        "portals_searched": len(portals_to_search) - len(errors),
        "hint": "Follow each result's import_method and import_arguments. Use inspect_data_source when a service "
        "needs discovery first.",
    }
    narrowed = sorted({r["matched_query"] for r in all_results if r.get("matched_query")})
    if narrowed:
        response["narrowed_to"] = narrowed
        response["narrowed_note"] = (
            "A portal matches every word at once, so the sentence found nothing and the search was narrowed to "
            + ", ".join(repr(n) for n in narrowed)
            + ". The results are about that, not about the rest of the question, so read the titles before loading."
        )
    if errors:
        response["errors"] = errors
    return response


def _portal_text(value, limit: int = 200) -> str:
    """A portal field as text, whatever the portal actually sent."""






    if isinstance(value, dict):
        for key in ("en", "fr", "value", "text"):
            if isinstance(value.get(key), str):
                value = value[key]
                break
        else:
            value = next((v for v in value.values() if isinstance(v, str)), "")
    elif isinstance(value, (list, tuple)):
        value = next((v for v in value if isinstance(v, str)), "")
    return str(value or "")[:limit]











_PORTAL_STOP_WORDS = frozenset({
    "au", "aux", "avec", "dans", "de", "des", "du", "en", "et", "la", "le", "les", "ou", "par",
    "pour", "sur", "un", "une",
    "and", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with",
    "carte", "couche", "data", "dataset", "donnee", "donnees", "fichier", "layer", "liste",
    "map", "niveau",
})
_PORTAL_NARROW_WIDTHS = (3, 2, 1)


def _portal_terms(query: str) -> list[str]:
    """The words worth searching on, in the order they were written."""





    seen: list[str] = []
    for word in re.findall(r"[^\W\d_]{3,}", str(query or ""), re.UNICODE):
        word = word.lower()
        if word in _PORTAL_STOP_WORDS or word in seen:
            continue
        seen.append(word)
    return seen


def _portal_narrowings(query: str) -> list[str]:
    """Shorter queries to try, widest first, when the sentence found nothing."""
    terms = _portal_terms(query)
    tries: list[str] = []
    for width in _PORTAL_NARROW_WIDTHS:
        if len(terms) > width:
            candidate = " ".join(terms[:width])
            if candidate not in tries:
                tries.append(candidate)
    return tries


def _parse_data_gouv_results(data: dict, format_pref: str) -> list:
    results = []
    for ds in data.get("data", [])[:10]:
        title = _portal_text(ds.get("title"), 300)
        description = _portal_text(ds.get("description"))
        resource = _pick_resource(ds.get("resources", []), format_pref)
        if not resource:
            continue
        url = resource.get("url")
        fmt = (resource.get("format") or "").lower()
        if not url:
            continue
        results.append(_build_catalog_result(title, description, url, fmt, "data_gouv"))
    return results


def _parse_opendatasoft_results(data: dict, base_url: str, format_pref: str) -> list:
    results = []
    fmt_ext = "geojson" if format_pref in ("geojson", "any") else format_pref

    for ds in data.get("results", [])[:10]:
        dataset_id = ds.get("dataset_id", "")
        metas = ds.get("metas", {}).get("default", {})
        title = _portal_text(metas.get("title"), 300) or dataset_id
        description = _portal_text(metas.get("description"))
        download_url = f"{base_url}/api/explore/v2.1/catalog/datasets/{dataset_id}/exports/{fmt_ext}"
        result = _build_catalog_result(title, description, download_url, fmt_ext, "opendatasoft")
        result["dataset_id"] = dataset_id
        records = metas.get("records_count")
        if isinstance(records, int):
            result["records"] = records






        result["area_filter"] = (
            "This URL is the whole dataset. For one area only, append "
            "?where=in_bbox(geo_shape, lat_min, lon_min, lat_max, lon_max) to it."
        )
        results.append(result)
    return results


def _parse_ckan_results(data: dict, format_pref: str) -> list:
    results = []
    datasets = data.get("result", {}).get("results", []) if "result" in data else data.get("results", [])
    if not isinstance(datasets, list):
        return results

    for ds in datasets[:10]:
        if not isinstance(ds, dict):
            continue
        title = _portal_text(ds.get("title"), 300)
        description = _portal_text(ds.get("notes") or ds.get("description"))
        resource = _pick_resource(ds.get("resources", []), format_pref)
        if not resource:
            continue
        url = resource.get("url") or resource.get("download_url")
        fmt = (resource.get("format") or "").lower()
        if not url:
            continue
        results.append(_build_catalog_result(title, description, url, fmt, "ckan"))
    return results


def _parse_socrata_results(data: dict) -> list:
    results = []
    for ds in data.get("results", [])[:10]:
        resource = ds.get("resource", {})
        metadata = ds.get("metadata", {})
        domain = metadata.get("domain")
        dataset_id = resource.get("id")
        if not domain or not dataset_id:
            continue

        datatypes = [str(value).lower() for value in resource.get("columns_datatype", [])]
        is_geospatial = any(any(geom in datatype for geom in _SOCRATA_GEOMETRY_TYPES) for datatype in datatypes)
        if not is_geospatial:
            continue

        title = _portal_text(resource.get("name"), 300) or dataset_id
        description = _portal_text(resource.get("description"))
        download_url = f"https://{domain}/resource/{dataset_id}.geojson?$limit=10000"
        result = _build_catalog_result(title, description, download_url, "geojson", "socrata")
        result["dataset_id"] = dataset_id
        result["landing_page"] = ds.get("link") or ds.get("permalink")
        results.append(result)
    return results


def _parse_arcgis_hub_results(data: dict) -> list:
    """ArcGIS Hub items as layers the plugin can load without a discovery step."""






    results = []
    for item in data.get("data", [])[:10]:
        if not isinstance(item, dict):
            continue
        attributes = item.get("attributes") or {}
        url = _portal_text(attributes.get("url"), 500)
        if not url.startswith("https://"):
            continue
        title = _portal_text(attributes.get("name"), 300) or _portal_text(item.get("id"), 300)
        description = _portal_text(attributes.get("searchDescription"))
        owner = _portal_text(attributes.get("source") or attributes.get("owner"), 120)
        result = {
            "title": title,
            "description": description,
            "format": "arcgis",
            "source_family": "arcgis_hub",
            "service_url": url,
            "import_method": "add_arcgis_rest_layer",
            "import_arguments": {"url": url, "name": title},
        }
        if owner:
            result["publisher"] = owner
        licence = _portal_text(attributes.get("licenseInfo"), 200)
        if licence:
            result["license"] = licence
        count = attributes.get("recordCount")
        if isinstance(count, int):
            result["feature_count"] = count
        item_id = _portal_text(item.get("id"), 120)
        if item_id:
            result["landing_page"] = f"https://hub.arcgis.com/datasets/{item_id}"
        results.append(result)
    return results


def _ods_where(text: str) -> str:
    """The Explore v2.1 catalogue ignores ``q``: "aménagements cyclables" on opendata.paris.fr came back as the whole catalogue, elections first."""




    words = [w.replace('"', "") for w in str(text or "").split()]
    words = [w for w in words if w]
    return " AND ".join(f'"{w}"' for w in words) or '""'


def _build_custom_portal(portal_url: str, portal_type: str) -> dict | None:
    base = portal_url.rstrip("/")
    if portal_type == "opendatasoft":
        search_url = f"{base}/api/explore/v2.1/catalog/datasets?where={{query}}&limit=10"
    elif portal_type == "ckan":
        search_url = f"{base}/api/3/action/package_search?q={{query}}&rows=10"
    elif portal_type == "socrata":
        if base.endswith("/api/catalog/v1"):
            search_url = f"{base}?q={{query}}&limit=10"
            base = base[: -len("/api/catalog/v1")]
        else:
            search_url = f"{base}/api/catalog/v1?q={{query}}&limit=10"
    else:
        return None
    return {
        "name": base,
        "api_type": portal_type,
        "base_url": base,
        "search_url": search_url,
    }


def _pick_resource(resources: list, format_pref: str) -> dict | None:
    preferred = None
    fallback = None
    for res in resources:
        fmt = (res.get("format") or "").lower()
        url = res.get("url") or res.get("download_url")
        if not url:
            continue
        if format_pref != "any" and format_pref in fmt:
            preferred = res
            break
        if fallback is None and any(marker in fmt for marker in _VECTOR_FORMAT_HINTS | _SERVICE_FORMAT_HINTS):
            fallback = res
    return preferred or fallback


def _build_catalog_result(title: str, description: str, url: str, fmt: str, source_family: str) -> dict:
    result = {
        "title": title,
        "description": description,
        "format": fmt,
        "source_family": source_family,
    }
    result.update(_build_import_strategy(url, title, fmt))
    return result


def _build_direct_import_result(url: str, title: str, source_family: str, fmt: str) -> dict:
    return {
        "source_family": source_family,
        "final_url": url,
        "format": fmt,
        "import_method": "add_vector_from_url",
        "import_arguments": {"url": url, "layer_name": os.path.splitext(title)[0] or title},
    }


def _build_import_strategy(url: str, title: str, fmt: str) -> dict:
    """The next call and its arguments; the sentence that routes to it is the server's."""
    fmt_lower = (fmt or "").lower()
    if "wms" in fmt_lower or "wfs" in fmt_lower:
        return {
            "service_url": url,
            "import_method": "inspect_data_source",
            "import_arguments": {"url": url},
        }
    return {
        "download_url": url,
        "import_method": "add_vector_from_url",
        "import_arguments": {"url": url, "layer_name": title},
    }


def _inspect_json_payload(final_url: str, payload: dict) -> dict:
    if payload.get("type") == "FeatureCollection":
        title = os.path.basename(urllib.parse.urlparse(final_url).path) or "dataset"
        return _build_direct_import_result(final_url, title, "geojson_feature_collection", "geojson")

    if "stac_version" in payload:
        return {
            "source_family": "stac_api",
            "final_url": final_url,
            "stac_version": payload.get("stac_version"),
            "collections": _extract_collection_ids(payload),
            "message": "STAC API detected. Use a STAC-aware client or browse collections/items next.",
        }

    if isinstance(payload.get("collections"), list):
        source_family = (
            "stac_api"
            if any("extent" in c or "license" in c for c in payload.get("collections", []))
            else "ogc_api"
        )
        return {
            "source_family": source_family,
            "final_url": final_url,
            "collections": _extract_collection_ids(payload),
            "message": "Collection API detected. Browse a collection items endpoint or use a dedicated connector.",
        }

    if isinstance(payload.get("result"), dict) and isinstance(payload["result"].get("results"), list):
        return {
            "source_family": "ckan_catalog",
            "final_url": final_url,
            "dataset_count": len(payload["result"].get("results", [])),
            "message": "CKAN catalog detected. search_open_data can query CKAN portals with portal_type='ckan'.",
        }

    if isinstance(payload.get("results"), list) and payload["results"]:
        first = payload["results"][0]
        if "dataset_id" in first:
            return {
                "source_family": "opendatasoft_catalog",
                "final_url": final_url,
                "dataset_count": len(payload["results"]),
                "message": "OpenDataSoft catalog detected. search_open_data can query this portal directly.",
            }
        if "resource" in first:
            return {
                "source_family": "socrata_catalog",
                "final_url": final_url,
                "dataset_count": len(payload["results"]),
                "message": "Socrata catalog detected. search_open_data can query this portal with "
                "portal_type='socrata'.",
            }

    if isinstance(payload.get("data"), list):
        return {
            "source_family": "data_gouv_catalog",
            "final_url": final_url,
            "dataset_count": len(payload["data"]),
            "message": "data.gouv.fr catalog response detected.",
        }

    return {
        "source_family": "json_api",
        "final_url": final_url,
        "message": "JSON source detected, but no direct import strategy was inferred.",
    }


def _inspect_xml_payload(final_url: str, payload_text: str) -> dict:
    upper = payload_text.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        return {
            "source_family": "xml",
            "final_url": final_url,
            "message": "XML with DTD or entities is not supported.",
        }
    del upper
    if len(payload_text) > _XML_TREE_CAP:
        streamed = _stream_capabilities(final_url, payload_text)
        if streamed is not None:
            return streamed
        return {
            "source_family": "xml",
            "final_url": final_url,
            "message": "XML payload too large to parse safely (>5MB).",
        }
    try:
        root = ET.fromstring(payload_text)  # nosec B314 - DTD and entities rejected above
    except ET.ParseError:
        return {
            "source_family": "xml",
            "final_url": final_url,
            "message": "XML source detected, but it could not be parsed safely.",
        }

    root_name = root.tag.split("}", 1)[-1].lower()
    if root_name == "featurecollection":
        return _wfs_hits_result(final_url, root.attrib)
    if root_name == "wms_capabilities":
        return {
            "source_family": "wms_capabilities",
            "final_url": final_url,
            "service_url": _strip_query_string(final_url),
            "import_method": "add_wms_layer",
            "import_arguments": {"url": _strip_query_string(final_url)},
            "available_layers": _extract_named_xml_elements(root, "Name"),
            "message": "WMS capabilities detected. Choose one of the available_layers values for add_wms_layer.layers.",
        }
    if root_name == "wfs_capabilities":
        return {
            "source_family": "wfs_capabilities",
            "final_url": final_url,
            "service_url": _strip_query_string(final_url),
            "import_method": "add_wfs_layer",
            "import_arguments": {"url": _strip_query_string(final_url)},
            "available_typenames": _extract_feature_type_names(root),
            "message": "WFS capabilities detected. Choose one of the available_typenames values for "
            "add_wfs_layer.typename.",
        }
    listing = _inspect_object_listing(final_url, root, root_name)
    if listing is not None:
        return listing
    return {
        "source_family": "xml",
        "final_url": final_url,
        "message": "XML source detected, but it was not recognized as WMS or WFS capabilities.",
    }







_XML_TREE_CAP = 5_000_000
_STREAM_CHUNK = 1 << 20


def _stream_capabilities(final_url: str, payload_text: str) -> dict | None:
    """The same capabilities answer, read without building the tree."""




    parser = ET.XMLPullParser(("start", "end"))
    root_name = ""
    names: list[str] = []
    try:
        for start in range(0, len(payload_text), _STREAM_CHUNK):
            parser.feed(payload_text[start:start + _STREAM_CHUNK])
            for event, element in parser.read_events():
                tag = element.tag.split("}", 1)[-1]
                if event == "start":
                    if not root_name:
                        root_name = tag.lower()
                        if root_name not in ("wms_capabilities", "wfs_capabilities"):
                            return None
                    continue
                if tag == "Name" and element.text:
                    text = element.text.strip()
                    if text and text not in names:
                        names.append(text)
                element.clear()
            if len(names) >= _CAPABILITIES_MAX_NAMES:
                break
    except ET.ParseError:
        if not names:
            return None
    names = names[:_CAPABILITIES_MAX_NAMES]
    service_url = _strip_query_string(final_url)
    note = (f"The document is over {_XML_TREE_CAP // 1_000_000} MB, so it was read in one pass and "
            f"the first {len(names)} names are listed. Ask the service for one of them rather than "
            "for the whole catalogue.")
    if root_name == "wms_capabilities":
        return {
            "source_family": "wms_capabilities",
            "final_url": final_url,
            "service_url": service_url,
            "import_method": "add_wms_layer",
            "import_arguments": {"url": service_url},
            "available_layers": names,
            "truncated": True,
            "message": "WMS capabilities detected. Choose one of the available_layers values for "
                       f"add_wms_layer.layers. {note}",
        }
    return {
        "source_family": "wfs_capabilities",
        "final_url": final_url,
        "service_url": service_url,
        "import_method": "add_wfs_layer",
        "import_arguments": {"url": service_url},
        "available_typenames": names,
        "truncated": True,
        "message": "WFS capabilities detected. Choose one of the available_typenames values for "
                   f"add_wfs_layer.typename. {note}",
    }


def _wfs_hits_result(final_url: str, attrib: dict) -> dict:
    """What a ``RESULTTYPE=hits`` request answers: the count, and no features."""





    matched = attrib.get("numberMatched") or attrib.get("numberOfFeatures")
    returned = attrib.get("numberReturned")
    out = {
        "source_family": "wfs_feature_count",
        "final_url": final_url,
        "service_url": _strip_query_string(final_url),
        "import_method": "add_wfs_layer",
        "import_arguments": {"url": _strip_query_string(final_url)},
    }
    if matched not in (None, "", "unknown"):
        out["feature_count"] = matched
        out["message"] = (f"The service reports {matched} features for this request. Load them with "
                          "add_wfs_layer, and pass a bounding box to keep the download to the view.")
    else:
        out["message"] = ("A WFS feature collection, with no count in it. Repeat the request with "
                          "RESULTTYPE=hits to get one, or load it with add_wfs_layer.")
    if returned not in (None, ""):
        out["features_returned"] = returned
    return out







_LISTING_MAX_FOLDERS = 40
_LISTING_MAX_FILES = 40


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _child_text(element, name: str) -> str:
    for child in element:
        if _local(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _inspect_object_listing(final_url: str, root, root_name: str) -> dict | None:
    """An S3 or Azure Blob listing read as a directory, or None when it is neither."""








    if root_name == "listbucketresult":
        store = "s3"
        prefix = _child_text(root, "Prefix")
        token = _child_text(root, "NextContinuationToken") or _child_text(root, "NextMarker")
        folders = [_child_text(el, "Prefix") for el in root if _local(el.tag) == "CommonPrefixes"]
        files = [(_child_text(el, "Key"), _child_text(el, "Size"))
                 for el in root if _local(el.tag) == "Contents"]
        container = _child_text(root, "Name")
    elif root_name == "enumerationresults":
        store = "azure_blob"
        prefix = _child_text(root, "Prefix")
        token = _child_text(root, "NextMarker")
        blobs = next((el for el in root if _local(el.tag) == "Blobs"), None)
        folders = [_child_text(el, "Name") for el in (blobs or []) if _local(el.tag) == "BlobPrefix"]
        files = []
        for el in (blobs or []):
            if _local(el.tag) != "Blob":
                continue
            properties = next((c for c in el if _local(c.tag) == "Properties"), None)
            size = _child_text(properties, "Content-Length") if properties is not None else ""
            files.append((_child_text(el, "Name"), size))
        container = (root.get("ContainerName") or "").rsplit("/", 1)[-1]
    else:
        return None

    base = _strip_query_string(final_url).rstrip("/")
    listed = []
    for key, size in files[:_LISTING_MAX_FILES]:
        if not key:
            continue
        entry = {"key": key, "url": f"{base}/{key}"}
        if size.isdigit():
            entry["size_bytes"] = int(size)
        kind = deduce_listing_kind(key)
        if kind:
            entry["kind"] = kind
        listed.append(entry)

    out = {
        "source_family": "object_listing",
        "final_url": final_url,
        "store": store,
        "folders": [f for f in folders[:_LISTING_MAX_FOLDERS] if f],
        "files": listed,
        "message": ("A bucket listing, not a dataset. Each folder is a prefix to list next, and a file whose "
                    "entry carries a kind loads with add_data using that url and kind."),
    }
    if container:
        out["container"] = container
    if prefix:
        out["prefix"] = prefix
    if len(folders) > _LISTING_MAX_FOLDERS or len(files) > _LISTING_MAX_FILES:
        out["listing_truncated"] = True
        out["message"] += (f" This page held {len(folders)} folders and {len(files)} keys and only the first "
                           "forty of each are above: narrow the prefix rather than reading this as the whole "
                           "list.")
    if token:
        out["next_page"] = token
        out["message"] += (" More entries exist: repeat the call with continuation-token=<next_page> on S3, "
                           "marker=<next_page> on Azure.")
    return out


def deduce_listing_kind(key: str) -> str:
    """The add_data kind for a key in a listing, or "" when it is not a layer file."""
    from .layer_io_tools import point_cloud_provider
    lower = key.lower()
    if point_cloud_provider(lower):
        return "pointcloud"
    if lower.endswith(".pmtiles"):
        return "pmtiles"
    if lower.endswith((".tif", ".tiff")):
        return "cog"
    if lower.endswith((".fgb", ".parquet", ".geoparquet", ".gpkg", ".geojson", ".shp", ".zip")):
        return "vector"
    if lower.endswith("ept.json"):
        return "pointcloud"
    return ""


def _extract_collection_ids(payload: dict) -> list[str]:
    collections = payload.get("collections", [])
    ids = []
    for collection in collections[:20]:
        identifier = collection.get("id") or collection.get("name")
        if identifier:
            ids.append(str(identifier))
    return ids


def _strip_query_string(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


_CAPABILITIES_MAX_NAMES = 20


def _extract_named_xml_elements(root: ET.Element, local_name: str) -> list[str]:
    values = []
    for el in root.iter():
        if el.tag.split("}", 1)[-1] == local_name and el.text:
            text = el.text.strip()
            if text and text not in values:
                values.append(text)
    return values[:_CAPABILITIES_MAX_NAMES]


def _extract_feature_type_names(root: ET.Element) -> list[str]:
    return _extract_named_xml_elements(root, "Name")
