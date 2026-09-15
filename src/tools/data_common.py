# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared helpers for the data tools: file names, vector sources, service config, the OSM recent-answer cache, HTTP and CRS/viewbox conversion."""

from __future__ import annotations

import json
import os
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY, QgsProject, QgsVectorLayer

from ..core import limits, net, tuning
from ..core.background import breathe, gc_paused, run_on_main_thread
from ..core.logger import log_warning
from ..core.policy import create_managed_temp_dir




_run_on_main_thread = run_on_main_thread


_WINDOWS_RESERVED_NAMES = (
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)

_WINDOWS_FORBIDDEN_CHARS = '\\/:*?"<>|'


def _avoid_reserved_name(stem: str) -> str:
    """Prefix the old DOS device names so they can be opened as files."""





    first = stem.split(".", 1)[0].strip(" ").lower()
    return f"_{stem}" if first in _WINDOWS_RESERVED_NAMES else stem


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






_OWN_GEOCODE_TIMEOUT = 6
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


def _text(value) -> str:
    return str(value).strip() if value not in (None, "") else ""










_bbox_km2 = limits.bbox_km2


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
