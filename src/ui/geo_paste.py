# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later










from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from qgis.core import QgsGeometry, QgsJsonUtils, QgsPointXY
from qgis.PyQt.QtCore import QCoreApplication, QLocale, QXmlStreamReader






CARD_CHARS = 2000
CARD_LINES = 40

_URL = re.compile(r"^(?:https?|s3|gs)://\S+$", re.IGNORECASE)
_ARCGIS = re.compile(r"/rest/services/.+/(mapserver|featureserver|imageserver)(?:/|$)")
_OGC = ("wms", "wfs", "wmts", "wcs")
_WKT = re.compile(
    r"^(?:SRID=(\d+)\s*;\s*)?((?:MULTI)?(?:POINT|LINESTRING|POLYGON|CURVE|SURFACE)|GEOMETRYCOLLECTION|"
    r"CIRCULARSTRING|COMPOUNDCURVE|CURVEPOLYGON|TRIANGLE|TIN|POLYHEDRALSURFACE)\s*(?:ZM|Z|M)?\s*\(",
    re.IGNORECASE)
_NUM = r"[-+]?\d{1,8}(?:\.\d+)?"
_PAIR = re.compile(rf"^\(?\s*({_NUM})\s*(?:[,;]\s*|\s+)({_NUM})\s*\)?$")

_PAIR_COMMA = re.compile(r"^\(?\s*([-+]?\d{1,8},\d+)\s*(?:;\s*|\s+)([-+]?\d{1,8},\d+)\s*\)?$")
_DMS = (r"(\d{1,3}(?:[.,]\d+)?)\s*°\s*(?:(\d{1,2}(?:[.,]\d+)?)\s*['′’]\s*)?"
        r"(?:(\d{1,2}(?:[.,]\d+)?)\s*(?:[\"″”]|'')\s*)?([NSEWO])")
_DMS_PAIR = re.compile(rf"^{_DMS}\s*[,;]?\s*{_DMS}$", re.IGNORECASE)
_GEOJSON_GEOMETRIES = ("Point", "MultiPoint", "LineString", "MultiLineString", "Polygon",
                       "MultiPolygon", "GeometryCollection")


def _tr(text: str) -> str:
    return QCoreApplication.translate("GeoPaste", text)


def read_paste(text: str) -> dict | None:








    body = str(text or "").strip()
    if not body:
        return None
    one_line = "\n" not in body
    if one_line and _URL.match(body):
        return _url_card(body)
    card = (_geojson_card(body) if body[0] == "{" else None) or _wkt_card(body)
    if card is None and one_line:
        card = _point_card(body)
    if card is not None:
        return card
    lines = body.count("\n") + 1
    if len(body) > CARD_CHARS or lines > CARD_LINES:
        return _text_card(body, lines)
    return None


def splice(text: str, pastes) -> str:



    ordered = sorted((max(0, min(int(pos), len(text))), index, str(raw or ""))
                     for index, (pos, raw) in enumerate(pastes or ()))
    pieces: list[tuple[bool, str]] = []
    last = 0
    for pos, _index, raw in ordered:
        pieces += [(False, text[last:pos]), (True, raw)]
        last = pos
    pieces.append((False, text[last:]))
    out = ""
    after_paste = ""
    for is_paste, piece in pieces:
        if not piece:
            continue
        if out and not out[-1].isspace() and not piece[0].isspace() and (is_paste or after_paste):
            several = "\n" in (piece if is_paste else after_paste) or (is_paste and after_paste)
            out += "\n" if several else " "
        out += piece
        after_paste = piece if is_paste else ""
    return out


def _url_card(url: str) -> dict | None:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme.lower() in ("s3", "gs"):
        host = parts.netloc
    kind = _url_kind(url, parts)
    if not kind or not host:
        return None
    return {"glyph": "lu.globe", "tone": "service", "service": _service_of(kind), "name": host, "kind": kind,
            "tip": url, "mono": True}


def _url_kind(url: str, parts) -> str:
    path = parts.path.lower()
    low = url.lower()
    if "{z}" in low and "{x}" in low and ("{y}" in low or "{-y}" in low):
        if path.endswith((".pbf", ".mvt")):
            return _tr("Vector tiles")
        return _tr("XYZ tiles")
    query = {key.lower(): value for key, value in parse_qsl(parts.query)}
    service = str(query.get("service") or "").lower()
    segments = [s for s in path.split("/") if s]
    if service not in _OGC:
        if path.endswith("wmtscapabilities.xml"):
            service = "wmts"
        else:
            service = next((s for s in reversed(segments) if s in _OGC), "")
    if service:
        return {"wms": _tr("WMS service"), "wfs": _tr("WFS service"), "wmts": _tr("WMTS service"),
                "wcs": _tr("WCS service")}[service]
    arcgis = _ARCGIS.search(path)
    if arcgis:
        return {"mapserver": _tr("ArcGIS MapServer"), "featureserver": _tr("ArcGIS FeatureServer"),
                "imageserver": _tr("ArcGIS ImageServer")}[arcgis.group(1)]
    if "stac" in parts.netloc.lower().split(".") or any("stac" in s for s in segments):
        return _tr("STAC catalog")
    if "collections" in segments and segments[-1] == "items":
        return _tr("OGC API Features")
    files = (
        ((".tif", ".tiff"), _tr("GeoTIFF (COG)")),
        ((".pmtiles",), _tr("PMTiles")),
        ((".geojson",), _tr("GeoJSON file")),
        ((".gpkg",), _tr("GeoPackage file")),
        ((".fgb",), _tr("FlatGeobuf file")),
        ((".parquet", ".geoparquet"), _tr("Parquet file")),
        ((".kml", ".kmz"), _tr("KML file")),
        ((".laz", ".las", ".copc"), _tr("Point cloud")),
    )
    for suffixes, kind in files:
        if path.endswith(suffixes):
            return kind
    return ""


def _shape_word(name: str) -> tuple[str, str]:

    key = name.upper().replace("_", "")
    words = {
        "POINT": (_tr("Point"), "lu.map-pin"),
        "MULTIPOINT": (_tr("Multipoint"), "lu.map-pin"),
        "LINESTRING": (_tr("Line"), "lu.spline"),
        "MULTILINESTRING": (_tr("Multiline"), "lu.spline"),
        "POLYGON": (_tr("Polygon"), "lu.pentagon"),
        "MULTIPOLYGON": (_tr("Multipolygon"), "lu.pentagon"),
    }
    return words.get(key, (_tr("Geometry"), "lu.pentagon"))


def _vertices(count: int) -> str:
    if count == 1:
        return _tr("1 vertex")
    return _tr("{n} vertices").format(n=QLocale().toString(int(count)))


def _wkt_card(body: str) -> dict | None:
    match = _WKT.match(body)

    if not match or not body.endswith(")") or body.count("(") != body.count(")"):
        return None
    srid, shape = match.group(1), match.group(2)
    geometry = QgsGeometry.fromWkt(body.split(";", 1)[1].strip() if srid else body)
    if geometry.isNull() or geometry.isEmpty():
        return None
    word, glyph = _shape_word(shape)
    tone = "point" if shape.upper() == "POINT" else "geometry"
    if shape.upper() == "POINT":
        point = geometry.asPoint()
        detail = f"{point.x():g} {point.y():g}"
    else:
        detail = _vertices(geometry.constGet().nCoordinates())
    if srid:
        detail = f"{detail} · EPSG:{srid}"
    return {"glyph": glyph, "tone": tone, "name": _tr("{shape} WKT").format(shape=word), "kind": detail,
            "tip": body[:300], "mono": True}


def _coordinate_count(coordinates) -> int:
    if isinstance(coordinates, list) and coordinates and all(
            isinstance(v, (int, float)) for v in coordinates):
        return 1
    if isinstance(coordinates, list):
        return sum(_coordinate_count(part) for part in coordinates)
    return 0


def _geojson_crs(data: dict) -> str:

    name = str(((data.get("crs") or {}).get("properties") or {}).get("name") or "")
    code = re.search(r"EPSG:+(\d+)$", name, re.IGNORECASE)
    if code and code.group(1) != "4326":
        return f"EPSG:{code.group(1)}"
    return "WGS 84"


def _geojson_card(body: str) -> dict | None:
    if not body.endswith("}"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    kind = data.get("type")
    crs = _geojson_crs(data)
    if kind == "FeatureCollection" and isinstance(data.get("features"), list):
        count = len(data["features"])
        detail = (_tr("1 feature") if count == 1
                  else _tr("{n} features").format(n=QLocale().toString(count)))
        return {"glyph": "lu.braces", "tone": "geometry", "name": "GeoJSON", "kind": f"{detail} · {crs}",
                "tip": body[:300], "mono": True}
    geometry = data.get("geometry") if kind == "Feature" else data
    if not isinstance(geometry, dict) or geometry.get("type") not in _GEOJSON_GEOMETRIES:
        return None
    word, _glyph = _shape_word(geometry["type"])
    if geometry["type"] == "GeometryCollection":
        count = sum(_coordinate_count(g.get("coordinates")) for g in geometry.get("geometries") or []
                    if isinstance(g, dict))
    else:
        count = _coordinate_count(geometry.get("coordinates"))
    if not count:
        return None
    return {"glyph": "lu.braces", "tone": "geometry", "name": _tr("{shape} GeoJSON").format(shape=word),
            "kind": f"{_vertices(count)} · {crs}", "tip": body[:300], "mono": True}


def _point_card(body: str) -> dict | None:
    match = _PAIR.match(body) or _PAIR_COMMA.match(body)
    degrees = False
    if match:
        a, b = (float(v.replace(",", ".")) for v in match.groups())
        fractional = all(("." in v or "," in v) for v in match.groups())
        if fractional and min(abs(a), abs(b)) <= 90 and max(abs(a), abs(b)) <= 180:
            degrees = True
        elif not min(abs(a), abs(b)) >= 1000:

            return None
    elif _DMS_PAIR.match(body):
        degrees = True
    else:
        return None
    kind = _tr("Point · WGS 84") if degrees else _tr("Point")
    return {"glyph": "lu.map-pin", "tone": "point", "name": " ".join(body.split()), "kind": kind, "tip": body,
            "mono": False}


def _text_format(body: str) -> str:
    head = body.lstrip()
    if head[:1] in "{[":
        try:
            json.loads(body)
            return "JSON"
        except ValueError:
            pass
    if head[:1] == "<":
        low = head[:400].lower()
        return "KML" if "<kml" in low else "GML" if "gml" in low else "XML"
    sample = [line for line in body.splitlines()[:20] if line.strip()]
    if len(sample) >= 3:
        for sep, name in (("\t", "TSV"), (";", "CSV"), (",", "CSV")):
            first = sample[0].count(sep)
            if first and all(line.count(sep) == first for line in sample):
                return name
    return ""


def _text_card(body: str, lines: int) -> dict:
    fmt = _text_format(body)

    count = (_tr("{n} characters").format(n=QLocale().toString(len(body))) if lines == 1
             else _tr("{n} lines").format(n=QLocale().toString(lines)))
    table = fmt in ("CSV", "TSV")
    return {"glyph": "lu.table-2" if table else "lu.file-text", "tone": "table" if table else "text",
            "name": _tr("Pasted text"),
            "kind": f"{count} · {fmt}" if fmt else count, "tip": body[:300], "mono": bool(fmt)}


def _service_of(kind: str) -> str:
    for service, words in (("wms", _tr("WMS service")), ("wfs", _tr("WFS service")),
                           ("wmts", _tr("WMTS service")), ("arcgis", _tr("ArcGIS MapServer")),
                           ("arcgis", _tr("ArcGIS FeatureServer"))):
        if kind == words:
            return service
    return ""


def capabilities_url(url: str, service: str) -> str:


    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        return ""
    if service == "arcgis":
        path = parts.path.rstrip("/")
        if not path.lower().endswith(("/mapserver", "/featureserver")):
            return ""
        return urlunsplit((parts.scheme, parts.netloc, path, "f=json", ""))
    if service not in ("wms", "wfs", "wmts"):
        return ""
    query = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() not in ("service", "request")]
    query += [("SERVICE", service.upper()), ("REQUEST", "GetCapabilities")]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def count_layers(service: str, body: bytes) -> int:





    if service == "arcgis":
        try:
            data = json.loads(body.decode("utf-8", errors="replace"))
        except ValueError:
            return 0
        if not isinstance(data, dict):
            return 0
        return sum(len(data.get(key) or []) for key in ("layers", "tables") if isinstance(data.get(key), list))
    reader = QXmlStreamReader(body)
    stack: list[list] = []
    count = 0
    while not reader.atEnd():
        reader.readNext()
        if reader.isStartElement():
            name = reader.name()
            name = str(name.toString() if hasattr(name, "toString") else name)
            parent = stack[-1][0] if stack else ""
            if name == "Name" and parent == "Layer" and stack:
                stack[-1][1] = True
            if (service == "wfs" and name == "FeatureType") or (
                    service == "wmts" and name == "Layer" and parent == "Contents"):
                count += 1
            stack.append([name, False])
        elif reader.isEndElement() and stack:
            name, named = stack.pop()
            if service == "wms" and name == "Layer" and named:
                count += 1
    return 0 if reader.hasError() and not count else count


def place(text: str):






    body = str(text or "").strip()
    match = _WKT.match(body)
    if match and _wkt_card(body) is not None:
        srid = match.group(1)
        geometry = QgsGeometry.fromWkt(body.split(";", 1)[1].strip() if srid else body)
        return geometry, f"EPSG:{srid}" if srid else ""
    if body[:1] == "{" and _geojson_card(body) is not None:
        data = json.loads(body)
        crs = _geojson_crs(data)
        crs = "EPSG:4326" if crs == "WGS 84" else crs
        features = data.get("features") if data.get("type") == "FeatureCollection" else [data]
        parts = []
        for feature in features or []:
            geometry = feature.get("geometry") if feature.get("type") == "Feature" else feature
            if isinstance(geometry, dict):
                parts.append(QgsJsonUtils.geometryFromGeoJson(json.dumps(geometry)))
        parts = [g for g in parts if not g.isNull()]
        if not parts:
            return None
        return (QgsGeometry.collectGeometry(parts) if len(parts) > 1 else parts[0]), crs
    card = _point_card(body) if "\n" not in body else None
    if card is None:
        return None
    dms = _DMS_PAIR.match(body)
    if dms:
        values = {}
        for degrees, minutes, seconds, hemisphere in (dms.groups()[:4], dms.groups()[4:]):
            value = sum(float(str(v or 0).replace(",", ".")) / factor
                        for v, factor in ((degrees, 1), (minutes, 60), (seconds, 3600)))
            letter = hemisphere.upper()
            values["y" if letter in "NS" else "x"] = -value if letter in "SW" else value
        if set(values) != {"x", "y"}:
            return None
        return QgsGeometry.fromPointXY(QgsPointXY(values["x"], values["y"])), "EPSG:4326"
    match = _PAIR.match(body) or _PAIR_COMMA.match(body)
    a, b = (float(v.replace(",", ".")) for v in match.groups())
    if card["kind"] == _tr("Point"):
        return QgsGeometry.fromPointXY(QgsPointXY(a, b)), ""
    lon, lat = (a, b) if abs(a) > 90 else (b, a)
    return QgsGeometry.fromPointXY(QgsPointXY(lon, lat)), "EPSG:4326"
