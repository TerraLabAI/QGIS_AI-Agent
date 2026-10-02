# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


from __future__ import annotations

import json
import math
import os
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY, QgsProject, QgsVectorLayer

from ..core import limits, net, tuning
from ..core.background import breathe, gc_paused, main_qthread, on_main_thread
from ..core.logger import log_warning
from ..core.policy import create_managed_temp_dir
from ..core.tool_registry import ServedValueMissing

_WINDOWS_RESERVED_NAMES = (
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)

_WINDOWS_FORBIDDEN_CHARS = '\\/:*?"<>|'
_MAX_STEM_CHARS = 100
_MAX_EXT_CHARS = 16


def _avoid_reserved_name(stem: str) -> str:






    first = stem.split(".", 1)[0].strip(" ").lower()
    return f"_{stem}" if first in _WINDOWS_RESERVED_NAMES else stem


def _safe_filename(name: str, default: str = "download") -> str:







    cleaned = "".join(
        "_" if (c in _WINDOWS_FORBIDDEN_CHARS or ord(c) < 32) else c
        for c in (name or "")
    ).strip(" .")
    if not cleaned:
        return default
    stem, ext = os.path.splitext(cleaned)
    if len(ext) > _MAX_EXT_CHARS:
        stem, ext = cleaned, ""




    stem = stem[:_MAX_STEM_CHARS].strip(" .")
    stem = _avoid_reserved_name(stem) or default
    return stem + ext


def _geojson_file(geojson_str: str, layer_name: str, subdir: str = "osm") -> str:


    tmp_dir = create_managed_temp_dir(subdir)
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in (layer_name or "layer"))[:60] or "layer"
    safe = _avoid_reserved_name(safe)
    path = os.path.join(tmp_dir, f"{safe}.geojson")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(geojson_str)
    return path







_GPKG_FROM_BYTES = 256 * 1024






_READ_WHOLE = (".geojson", ".json", ".xlsx", ".xls", ".ods")


def _vector_uri_for(path: str) -> str:











    if not path.lower().endswith(_READ_WHOLE):
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

        source = gdal.OpenEx(path, gdal.OF_VECTOR)
        first = source.GetLayer(0).GetName() if source.GetLayerCount() else None
        gdal.VectorTranslate(gpkg, source, format="GPKG", layerName=name, layers=[first] if first else None)
        source = None
    except Exception as exc:  # noqa: BLE001
        log_warning(f"GeoPackage conversion skipped for {os.path.basename(path)}: {exc}")
        return path
    return f"{gpkg}|layername={name}"


def _vector_source_from_geojson(geojson_str: str, layer_name: str, subdir: str = "osm") -> str:

    return _vector_uri_for(_geojson_file(geojson_str, layer_name, subdir))







_JSON_PIECEWISE_CHARS = 2 * 1024 * 1024


def _json_array_items(text: str, key: str) -> tuple | None:







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
                return items, start, index + 1
            item, index = decoder.raw_decode(text, index)
            items.append(item)
            breathe(len(items))
    except ValueError:
        return None


def _json_object(text: str, array_key: str) -> dict:




    if len(text) >= _JSON_PIECEWISE_CHARS:
        with gc_paused():
            found = _json_array_items(text, array_key)
        if found is not None:
            items, start, end = found
            try:
                rest = json.loads(text[:start] + "[]" + text[end:])
            except ValueError:
                rest = None
            if isinstance(rest, dict):
                rest[array_key] = items
                return rest
    with gc_paused():
        return json.loads(text)


def _feature_collection_file(features: list, layer_name: str, subdir: str) -> str:

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

    return _vector_uri_for(_feature_collection_file(features, layer_name, subdir))


def worker_options(kind=QgsVectorLayer):

    options = kind.LayerOptions()
    options.skipCrsValidation = True
    return options


def built_here(make, read_extent: bool = True):



















    if on_main_thread():
        return None
    try:
        layer = make()
        if layer is None:
            return None
        if layer.isValid():
            if layer.isSpatial() and not layer.crs().isValid():
                return None
            if read_extent:
                layer.extent()
    except InterruptedError:
        raise
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Layer not built off the main thread: {exc}")
        return None
    target = main_qthread()
    if target is None:
        return None
    layer.moveToThread(target)
    return layer


def _layer_from_source(uri: str, layer_name: str, built=None):


    return built if built is not None else QgsVectorLayer(uri, layer_name, "ogr")


def _worker_layer(uri: str, layer_name: str):

    return built_here(lambda: QgsVectorLayer(uri, layer_name, "ogr", worker_options()))


def _layer_from_geojson_str(geojson_str: str, layer_name: str, subdir: str = "osm"):









    return QgsVectorLayer(_geojson_file(geojson_str, layer_name, subdir), layer_name, "ogr")






_USER_AGENT = net.user_agent()










def _service(key: str) -> str:

    value = tuning.service_url(key, "")
    if not value:
        raise ServedValueMissing(f"services.{key}")
    return value










_OVERPASS_CONNECT_TIMEOUT = 5.0

_OSRM_PROFILES = ("driving", "walking", "cycling")


def _osrm_base(profile: str) -> str:
    return _service(f"osrm_{profile if profile in _OSRM_PROFILES else 'driving'}")


_GEOCODE_TIMEOUT = 25






_OWN_GEOCODE_TIMEOUT = 6
_OVERPASS_TIMEOUT = 45
_ROUTE_TIMEOUT = 30
_DOWNLOAD_TIMEOUT = 45


def _overpass_timeout() -> int:



    return tuning.limit("net", "overpass_timeout_s", _OVERPASS_TIMEOUT)


def _download_timeout() -> int:

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





_PORTAL_PARSERS = frozenset({"data_gouv", "opendatasoft", "ckan", "socrata", "arcgis_hub"})


def _open_data_portals() -> dict:

    served = tuning.service_doc("open_data_portals")
    rows = {key: dict(row) for key, row in (served or {}).items()
            if isinstance(row, dict) and row.get("api_type") in _PORTAL_PARSERS and row.get("search_url")}
    if not rows:
        raise ServedValueMissing("services.open_data_portals")
    return rows


def _http_get(url: str, timeout: int = 15, cache_ttl: float = 0.0) -> bytes:







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
    except Exception:  # nosec B110
        pass
    return None


def _project_crs_transform(points: list) -> tuple | None:







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


def _viewbox_bounds(viewbox: str | None) -> tuple | None:

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




    if math.isnan(lon) or math.isnan(lat) or not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        return None
    return lon, lat


def _fold(value) -> str:

    text = unicodedata.normalize("NFKD", _text(value).casefold())
    return "".join(character for character in text if not unicodedata.combining(character))


def _text(value) -> str:
    return str(value).strip() if value not in (None, "") else ""










_bbox_km2 = limits.bbox_km2


def _footprint_box(bbox: dict):

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




__all__ = [
    "_CACHE_CATALOG_S",
    "_CACHE_GEOCODE_S",
    "_DOWNLOAD_TOTAL_TIMEOUT",
    "_GEOCODE_TIMEOUT",
    "_INSPECT_TIMEOUT",
    "_MAX_ARCHIVE_ENTRIES",
    "_MAX_DOWNLOAD_SIZE",
    "_MAX_EXTRACTED_SIZE",
    "_MAX_ROUTE_WAYPOINTS",
    "_PORTAL_PARSERS",
    "_OSM_RECENT",
    "_OSM_RECENT_MIN_BYTES",
    "_OSRM_PROFILES",
    "_OVERPASS_CONNECT_TIMEOUT",
    "_OWN_GEOCODE_TIMEOUT",
    "_PORTAL_TIMEOUT",
    "_ROUTE_TIMEOUT",
    "_SERVICE_FORMAT_HINTS",
    "_SOCRATA_GEOMETRY_TYPES",
    "_TOTAL_TIMEOUT_FACTOR",
    "_USER_AGENT",
    "_VECTOR_FORMAT_HINTS",
    "_WINDOWS_FORBIDDEN_CHARS",
    "_WINDOWS_RESERVED_NAMES",
    "_avoid_reserved_name",
    "_bbox_km2",
    "_canvas_viewbox_4326",
    "_download_timeout",
    "_fold",
    "_footprint_box",
    "_http_fetch",
    "_http_get",
    "_is_number",
    "_json_object",
    "_layer_from_geojson_str",
    "_layer_from_source",
    "_open_data_portals",
    "_osm_recent",
    "_osm_remember",
    "_osrm_base",
    "_overpass_timeout",
    "_project_crs_transform",
    "_safe_filename",
    "_service",
    "_text",
    "_vector_source_from_features",
    "_vector_uri_for",
    "_viewbox_bounds",
    "_viewbox_centre",
]
