# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The hosted Overture/OSM tile service: themes, tiles, filters, stream and box splitting, and the outline helpers shared with the place."""

from __future__ import annotations

import http.client
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request

from qgis.core import QgsProject

from ..core import catalog, limits, net, tuning
from . import volume_guard
from .data_common import (
    _MAX_DOWNLOAD_SIZE,
    _TOTAL_TIMEOUT_FACTOR,
    _USER_AGENT,
    _bbox_km2,
    _is_number,
    _layer_from_source,
    _run_on_main_thread,
    _service,
    _vector_source_from_features,
)
from .data_inspect import _add_vector_over_range_requests, _tune_gdal_for_range_reads






_OVERTURE_API = "https://aca-terralab-opendata.proudsky-7d379d48.westeurope.azurecontainerapps.io"


_OVERTURE_TILES = "https://stterralabopendata.blob.core.windows.net/overture/2026-08-19"







_OSM_TILES = "https://stterralabopendata.blob.core.windows.net/osm/planet-2026-09"
_OSM_THEMES = ("landuse", "pois", "waterways", "water_areas", "power", "boundaries", "railways", "routes",
               "transit_stops")
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
class _ThemeLicences(dict):
    """The licence each hosted theme ships with, read through the served ``overture:<theme>`` row first."""






    def __getitem__(self, theme):
        served = catalog.theme_licence(f"overture:{theme}")
        return served["licence"] if served and served["licence"] else super().__getitem__(theme)

    def get(self, theme, default=None):
        served = catalog.theme_licence(f"overture:{theme}")
        return served["licence"] if served and served["licence"] else super().get(theme, default)


_OVERTURE_LICENCES = _ThemeLicences({"buildings": "ODbL 1.0", "roads": "ODbL 1.0", "divisions": "ODbL 1.0",
                                     "places": "CDLA Permissive 2.0",
                                     "addresses": "mixed open licences per country",
                                     **dict.fromkeys(_OSM_THEMES, "ODbL 1.0"), "protected_areas": "ODbL 1.0"})
_OVERTURE_ATTRIBUTION = "© Overture Maps Foundation, © OpenStreetMap contributors"
_OVERTURE_BUILDING_ATTRIBUTION = (_OVERTURE_ATTRIBUTION + ", Microsoft Building Footprints, "
                                  "Google Open Buildings, Esri Community Maps")
_OSM_ATTRIBUTION = "© OpenStreetMap contributors"


def _overture_attribution(theme: str) -> str:
    """The served ``overture:<theme>`` attribution, else the one this build ships."""
    served = catalog.theme_licence(f"overture:{theme}")
    if served and served["attribution"]:
        return served["attribution"]
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


def _overture_matches(feature: dict, wanted: dict | list) -> bool:
    """The client-side filter: every named field equals the value asked for, except `ANY_VALUE`, which any value the field carries matches."""





    if isinstance(wanted, list):
        return any(_overture_matches(feature, one) for one in wanted)
    properties = feature.get("properties") or {}
    for field, value in wanted.items():
        got = properties.get(field)
        values = value if isinstance(value, (list, tuple, set)) else [value]
        if any(str(one) == volume_guard.ANY_VALUE for one in values):
            if got is None or not str(got).strip():
                return False
            continue
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


def _filter_miss_suggestion(empty: dict) -> str:
    """The next call for a filter that matched nothing, from what ``_overture_filter_miss`` found, or ""."""
    if empty.get("fields_missing"):




        useful = [f for f in empty["fields_available"]
                  if f not in ("osm_id", "osm_type", "other_tags", "id")
                  and not f.startswith("addr_")]
        return "Filter on a field this theme carries: " + ", ".join((useful or empty["fields_available"])[:8]) + "."
    if empty.get("values_present"):
        return "Ask again with one of the values in values_present, or drop the filter."
    return ""


_FILTER_SAMPLE = 2000


def _tile_sample(url: str, box=None) -> list:
    """Up to 2000 features of one published file inside *box*, unfiltered, as ``{"properties": ...}``."""






    if not url:
        return []
    from osgeo import gdal, ogr

    net.check_url(url)
    _tune_gdal_for_range_reads()

    gdal.UseExceptions()
    sample: list = []
    try:
        gdal.ErrorReset()
        source = ogr.Open(f"/vsicurl/{url}")
        if source is None:
            return []
        layer = source.GetLayer(0)
        if box is not None:
            layer.SetSpatialFilterRect(*box)
        definition = layer.GetLayerDefn()
        names = [definition.GetFieldDefn(i).GetName() for i in range(definition.GetFieldCount())]
        for feature in layer:
            sample.append({"properties": {name: feature.GetField(name) for name in names}})
            if len(sample) >= _FILTER_SAMPLE:
                break
    except RuntimeError:
        pass
    return sample


def _stream_filter_miss(theme: str, box, streamed: dict, wanted: dict, subset: str) -> dict | None:
    """The clip path's answer for a filter that matched nothing, for tiles opened in place."""









    made = [str(entry.get("layer_id") or "") for entry in streamed.get("layers") or []]

    def _counts():
        project = QgsProject.instance()
        return [(layer.subsetString() == subset, int(layer.featureCount()))
                for layer in (project.mapLayer(layer_id) for layer_id in made) if layer is not None]

    seen = _run_on_main_thread(_counts)

    if not seen or any(filtered and count != 0 for filtered, count in seen):
        return None
    first = (streamed.get("layers") or [{}])[0]
    sample = _tile_sample(str(first.get("url") or ""), box)
    if any(_overture_matches(feature, wanted) for feature in sample):
        return None
    empty = {"feature_count": 0, "theme": theme, "mode": "stream", "filter": subset,
             "tiles": streamed.get("tiles") or []}
    if sample:
        empty.update(_overture_filter_miss(sample, wanted))
    empty["message"] = f"{_overture_source(theme)} has no {theme} in this box that match the filter."
    empty["suggestion"] = _filter_miss_suggestion(empty) or "Widen the box, drop the filter, or try another theme."
    _run_on_main_thread(lambda: QgsProject.instance().removeMapLayers([layer_id for layer_id in made if layer_id]))
    return empty


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
    except (net.FetchDeadline, net.FetchTruncated, net.NetworkUnreachable, OSError,
            http.client.HTTPException) as error:


        broken = net.describe_failure(error)
        if broken:



            return {"_error": broken, "code": net.NETWORK_ERROR, "suggestion": net.NETWORK_SUGGESTION}
        if isinstance(error, net.FetchDeadline):

            return {"_error": f"The Overture service's answer did not arrive in time. {error.reason}",
                    "suggestion": "Ask for a smaller box, which is a smaller answer to download."}


        return {"_error": f"Could not reach the Overture service: {error}"}
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


def _overture_divisions_presence(box) -> dict:
    """Count of every divisions subtype over the same box that has at least one feature, best-effort."""









    west, south, east, north = box
    params = {"bbox": f"{west},{south},{east},{north}"}
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{_service('overture_api', _OVERTURE_API)}/v1/divisions/count?{query}",
                                     headers={"User-Agent": _USER_AGENT})
    try:
        answer = net.fetch(request, timeout=tuning.limit("net", "hosted_timeout_s", _OVERTURE_TIMEOUT),
                           max_bytes=_MAX_DOWNLOAD_SIZE, cache_ttl=600.0)
        payload = json.loads(answer.body)
    except Exception:  # noqa: BLE001 - an enrichment on an already-built answer, never worth failing over
        return {}
    by_subtype = payload.get("by_subtype") if isinstance(payload, dict) else None
    if not isinstance(by_subtype, dict):
        return {}
    return {str(subtype): int(count) for subtype, count in by_subtype.items()
           if isinstance(count, (int, float)) and count > 0}


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




_STREAM_SPLIT_MAX_CALLS = 4


def _tile_bounds(x: int, y: int) -> tuple:
    """(west, south, east, north) of one zoom-8 Web Mercator tile, in degrees."""
    side = 2 ** _OVERTURE_TILE_ZOOM

    def _lat(row: int) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * row / side))))

    return (x / side * 360.0 - 180.0, _lat(y + 1), (x + 1) / side * 360.0 - 180.0, _lat(y))


def _stream_boxes(box) -> list:
    """*box* cut along tile edges into boxes of at most 2 by 2 zoom-8 tiles, each inside *box*."""




    west, south, east, north = box
    tiles = _overture_tiles(box)
    columns = sorted({x for x, _ in tiles})
    rows = sorted({y for _, y in tiles})
    pieces = []
    for row_start in range(0, len(rows), 2):
        for column_start in range(0, len(columns), 2):
            group_columns = columns[column_start:column_start + 2]
            group_rows = rows[row_start:row_start + 2]
            tile_west, _, _, tile_north = _tile_bounds(group_columns[0], group_rows[0])
            _, tile_south, tile_east, _ = _tile_bounds(group_columns[-1], group_rows[-1])
            piece = (math.ceil(max(west, tile_west + 1e-4) * 1e4) / 1e4,
                     math.ceil(max(south, tile_south + 1e-4) * 1e4) / 1e4,
                     math.floor(min(east, tile_east - 1e-4) * 1e4) / 1e4,
                     math.floor(min(north, tile_north - 1e-4) * 1e4) / 1e4)
            if piece[0] < piece[2] and piece[1] < piece[3]:
                pieces.append(piece)
    return pieces


def _stream_split_refusal(theme: str, box, count: int) -> dict:
    """A stream box over the tile cap: the boxes that fit, when a few calls cover it."""





    source = _overture_source(theme)
    pieces = _stream_boxes(box)
    if pieces and len(pieces) <= _STREAM_SPLIT_MAX_CALLS and all(
            len(_overture_tiles(piece)) <= _OVERTURE_MAX_TILES for piece in pieces):
        listed = "; ".join("[" + ", ".join(f"{value:g}" for value in piece) + "]" for piece in pieces)
        return {"_error": (f"This box spans {count} {source} tiles and one call opens at most {_OVERTURE_MAX_TILES}. "
                           f"These {len(pieces)} boxes [west, south, east, north] cover it, each within "
                           f"{_OVERTURE_MAX_TILES} tiles: {listed}." + volume_guard.LIFT_HINT),
                "code": limits.CEILING_CODE,
                "suggestion": "Call fetch_overture once per box above, with the same theme, mode and filter."}
    calls = len(pieces) or -(-count // _OVERTURE_MAX_TILES)
    return {"_error": (f"This box spans {count} {source} tiles and one call opens at most {_OVERTURE_MAX_TILES}: "
                       f"covering it would take {calls} calls, each adding its tiles as layers."
                       + volume_guard.LIFT_HINT),
            "code": limits.CEILING_CODE,
            "suggestion": "Ask for a smaller box, a district or a town rather than a province, and say which part."}


def _stream_refusal(theme: str, box) -> dict | None:
    """The refusal a tile box meets before any tile is opened, or None."""
    if theme == "divisions":
        return None
    count = len(_overture_tiles(box))
    return _stream_split_refusal(theme, box, count) if count > _OVERTURE_MAX_TILES else None


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
        refused = _stream_refusal(theme, box)
        if refused:
            return refused
        urls = [(f"{x}/{y}", _overture_tile_url(theme, x, y)) for x, y in _overture_tiles(box)]

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
        if any(str(one) == volume_guard.ANY_VALUE for one in values):
            return ""
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

_HIT_SUBTYPES = {"country": "country", "state": "region", "county": "county"}



_CLIP_MAX_BOXES = 12



_CLIP_CHECK_EVERY = 50


def _stop_if_cancelled(cancelled) -> None:
    """Raise when the task this handler runs in was cancelled; *cancelled* is ``net.current_cancel_check()``."""
    if cancelled is not None and cancelled():
        raise InterruptedError("Stopped: the call was cancelled while its outline clip ran.")


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






_RING_BAND_EDGES = 8
_RING_MAX_BANDS = 4096


def _ring_index(ring: list):
    """``(south, height, bands, edges per band)`` for ``_ring_index_contains``."""
    count = len(ring)
    if count < 3:
        return (0.0, 1.0, 1, [[]])
    ys = [point[1] for point in ring]
    south, north = min(ys), max(ys)
    bands = max(1, min(_RING_MAX_BANDS, count // _RING_BAND_EDGES))
    height = (north - south) / bands or 1.0
    table: list = [[] for _ in range(bands)]
    last = bands - 1
    previous = count - 1
    for current in range(count):
        xi, yi = ring[current]
        xj, yj = ring[previous]
        previous = current
        if yi == yj:

            continue
        low, high = (yi, yj) if yi < yj else (yj, yi)
        first = min(last, int((low - south) / height))
        final = min(last, int((high - south) / height))
        edge = (xi, yi, xj, yj)
        for band in range(first, final + 1):
            table[band].append(edge)
    return (south, height, bands, table)


def _ring_index_contains(x: float, y: float, index) -> bool:
    """``_ring_contains`` over the edges of the point's band."""




    south, height, bands, table = index
    band = min(bands - 1, max(0, int((y - south) / height)))
    inside = False
    for xi, yi, xj, yj in table[band]:
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
    return inside


def _outline_contains(x: float, y: float, outline: dict) -> bool:
    """Is the point inside the outline, holes taken out."""
    west, south, east, north = outline["bbox"]
    if not (west <= x <= east and south <= y <= north):
        return False
    indexed = outline.get("_rings")
    if indexed is None:

        indexed = outline["_rings"] = [
            (_ring_index(exterior), _polys_bbox([(exterior, [])]), [_ring_index(hole) for hole in holes])
            for exterior, holes in outline["polys"]]
    for exterior, bounds, holes in indexed:
        if bounds is None or not (bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]):
            continue
        if not _ring_index_contains(x, y, exterior):
            continue
        if any(_ring_index_contains(x, y, hole) for hole in holes):
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


def _split_box(box, max_km2: float, max_span: float) -> list:
    """*box* cut into a grid of boxes each under both of the service's caps."""






    import math

    west, south, east, north = box
    columns = max(1, int(math.ceil((east - west) / max_span))) if max_span > 0 else 1
    rows = max(1, int(math.ceil((north - south) / max_span))) if max_span > 0 else 1
    def largest(columns: int, rows: int) -> float:


        width, height = (east - west) / columns, (north - south) / rows
        return max(_bbox_km2(south + row * height, west, south + (row + 1) * height, west + width)
                   for row in range(rows))

    while max_km2 > 0 and largest(columns, rows) > max_km2:
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




_DIVISION_WORDS = {"state": "region", "province": "region", "department": "county", "departement": "county",
                   "city": "locality", "town": "locality", "village": "locality", "municipality": "locality",
                   "commune": "locality", "neighbourhood": "neighborhood"}

_DIVISION_SUBTYPES_PER_CALL = 3


def _divisions_subtypes_asked(wanted) -> tuple:
    """``(subtypes, None)`` for the divisions subtypes a filter names, or ``([], refusal)``."""




    raw = wanted.get("subtype") if isinstance(wanted, dict) else None
    asked = raw if isinstance(raw, (list, tuple)) else [raw]
    names = [name for name in dict.fromkeys(str(one or "").strip().lower() for one in asked) if name]
    known = _division_subtypes()
    if not names:




        return [], {"_error": "Divisions are one whole-world file per subtype, so the subtype is not optional.",
                    "suggestion": 'Pass filter {"subtype": "..."} with one of: '
                                  + ", ".join(known)
                                  + '. A commune or a city is "locality", a city district (a Paris or Lyon '
                                  'arrondissement, a borough) "localadmin", a named quarter (Le Marais, '
                                  'Kitsilano, Kreuzberg) "macrohood", a smaller neighbourhood "neighborhood", '
                                  'a department "county", a region "region". Call again with the subtype; '
                                  'do not ask the user.'}
    unknown = [name for name in names if name not in known]
    if unknown:
        word = unknown[0]
        meant = _DIVISION_WORDS.get(word)
        if meant:
            return [], {"_error": f"{word!r} is not a divisions subtype: a {word} is {meant!r} in Overture.",
                        "code": "INVALID_ARGS",
                        "suggestion": f'Call again with filter {{"subtype": "{meant}"}}; do not ask the user.'}
        return [], {"_error": f"{word!r} is not a divisions subtype. The subtypes are: {', '.join(known)}.",
                    "code": "INVALID_ARGS",
                    "suggestion": 'Call again with one of them: a city is "locality", a state or province "region".'}
    if len(names) > _DIVISION_SUBTYPES_PER_CALL:
        return [], {"_error": (f"{len(names)} subtypes were named and one call reads at most "
                               f"{_DIVISION_SUBTYPES_PER_CALL}: each is a whole-world file."),
                    "code": "INVALID_ARGS",
                    "suggestion": "Name the one or two subtypes the place really is."}
    return names, None


def _overture_feature_key(feature: dict):
    """What makes two copies of one feature the same one across two boxes."""
    props = feature.get("properties") if isinstance(feature, dict) else None
    if isinstance(props, dict):
        for field in ("id", "division_id", "osm_id", "gers_id"):
            value = props.get(field)
            if value not in (None, ""):
                return f"{field}:{value}"
    return None


def _clip_split_refusal(theme: str, box, max_km2: float) -> dict:
    """The refusal of a box more clips of *max_km2* than one call makes would cover."""
    west, south, east, north = box
    return {"_error": (f"This outline is {_bbox_km2(south, west, north, east):,.0f} km2 and would "
                       f"take more than {_CLIP_MAX_BOXES} clips of the {max_km2:,.0f} km2 one "
                       f"{theme} call covers." + volume_guard.LIFT_HINT),



            "code": limits.CEILING_CODE}


def _overture_boxes(theme: str, box, max_km2: float, max_span: float, args: dict, subtype: str | None = None):
    """The service's answer for *box*, in as many calls as its caps need."""






    if subtype is None:
        subtype = ""
        if theme == "divisions":
            wanted = args.get("filter") if isinstance(args.get("filter"), dict) else {}
            subtype = str(wanted.get("subtype") or "").strip().lower()
    pieces = _split_box(box, max_km2, max_span)
    if not pieces:
        return None, _clip_split_refusal(theme, box, max_km2)
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
