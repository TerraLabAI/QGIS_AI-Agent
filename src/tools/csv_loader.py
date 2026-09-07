# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Load a delimited text file the way a GIS user expects."""








from __future__ import annotations

import codecs
import csv
import io
import itertools
import os
import re
from urllib.parse import quote

from qgis.core import QgsFeatureRequest, QgsProject, QgsVectorLayer
from qgis.PyQt.QtCore import QUrl

CSV_EXTENSIONS = (".csv", ".tsv", ".txt")

_LAT = ("latitude", "lat", "y", "ycoord", "y_coord", "northing", "lat_dd", "latitude_dd")
_LON = ("longitude", "lon", "lng", "long", "x", "xcoord", "x_coord", "easting", "lon_dd", "longitude_dd")
_WKT = ("wkt", "geometry", "geom", "the_geom", "wkt_geom")
_DECIMAL_COMMA = re.compile(r"^-?\d+,\d+$")
_METRIC = re.compile(r"^-?\d{5,}([.,]\d+)?$")


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.strip().lower())


def detect_encoding(sample: bytes) -> str:
    """The name the delimitedtext provider should use for these bytes."""








    if sample.startswith(codecs.BOM_UTF8):
        return "UTF-8"
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return "windows-1252"
    return "UTF-8"


def sniff(path: str) -> dict:
    """Delimiter, header, first rows, coordinate columns, decimal separator, encoding."""
    with open(path, "rb") as fh:
        raw = fh.read(65536)
    encoding = detect_encoding(raw)


    sample = raw.decode("utf-8-sig" if encoding == "UTF-8" else encoding, errors="replace")
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") > sample.count(",") else ","




    rows = list(itertools.islice(csv.reader(io.StringIO(sample), delimiter=delimiter), 50))
    rows = [r for r in rows if r]
    header = rows[0] if rows else []
    body = rows[1:]
    keys = {_norm(h): h for h in header}
    lat = next((keys[k] for k in map(_norm, _LAT) if k in keys), None)
    lon = next((keys[k] for k in map(_norm, _LON) if k in keys), None)
    wkt = next((keys[k] for k in map(_norm, _WKT) if k in keys), None)
    decimal = "."
    metric = False
    if lat and lon and body:
        i_lat, i_lon = header.index(lat), header.index(lon)
        cells = [r[i] for r in body for i in (i_lat, i_lon) if i < len(r)]
        if cells and all(_DECIMAL_COMMA.match(c.strip()) for c in cells if c.strip()):
            decimal = ","
        metric = any(_METRIC.match(c.strip()) for c in cells if c.strip())
    return {"delimiter": delimiter, "header": header, "rows": len(body),
            "lat": lat, "lon": lon, "wkt": wkt, "decimal": decimal, "metric": metric,
            "encoding": encoding}


def build_uri(path: str, info: dict, crs: str | None = None) -> tuple[str, str]:
    """(uri, geometry) for the delimitedtext provider."""

    delimiter = "\\t" if info["delimiter"] == "\t" else info["delimiter"]
    params = ["type=csv", "delimiter=" + delimiter, "detectTypes=yes", "trimFields=yes",


              "encoding=" + info.get("encoding", "UTF-8")]
    if info["decimal"] == ",":
        params.append("decimalPoint=,")
    geometry = "none"
    if info.get("wkt"):
        params.append("wktField=" + quote(info["wkt"]))
        geometry = "wkt"
    elif info.get("lat") and info.get("lon"):
        params += ["xField=" + quote(info["lon"]), "yField=" + quote(info["lat"])]
        geometry = "point"
    else:
        params.append("geomType=none")
    if geometry != "none":
        params.append("crs=" + (crs or ("EPSG:3857" if info.get("metric") else "EPSG:4326")))
    return QUrl.fromLocalFile(os.path.abspath(path)).toString() + "?" + "&".join(params), geometry


_GEOMETRY_SAMPLE_LIMIT = 2000


def _geometry_check(layer, geometry: str, info: dict) -> str | None:
    """Null-geometry rate and a lat/lon-swap heuristic, sampled cheaply (main-thread, at most 2000 features)."""
    if geometry not in ("point", "wkt"):
        return None
    lat_field, lon_field = info.get("lat"), info.get("lon")
    lat_idx = layer.fields().indexOf(lat_field) if lat_field else -1
    lon_idx = layer.fields().indexOf(lon_field) if lon_field else -1
    total = 0
    empty = 0
    swap_suspect = False
    request = QgsFeatureRequest().setLimit(_GEOMETRY_SAMPLE_LIMIT)
    for feat in layer.getFeatures(request):
        total += 1
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            empty += 1
        if lat_idx >= 0 and lon_idx >= 0 and not info.get("metric"):
            try:
                lat_val, lon_val = float(feat[lat_idx]), float(feat[lon_idx])
            except (TypeError, ValueError):
                continue
            if abs(lat_val) > 90 and abs(lon_val) <= 90:
                swap_suspect = True
    if swap_suspect:
        return ("Sampled latitude values fall outside [-90, 90] while longitude does not: "
                "the lat/lon columns may be swapped.")
    if total and empty * 2 > total:
        return ("More than half of the sampled features have no geometry: the coordinate columns may hold "
                "DMS text (e.g. 45 deg 30' N) rather than decimal degrees.")
    return None


def _geometry_kind_of(layer) -> str:
    """What the loaded layer actually carries: "point", "wkt" (any other shape) or "none"."""
    try:
        from qgis.core import QgsWkbTypes

        wkb = layer.wkbType()
        if wkb in (QgsWkbTypes.Type.NoGeometry, QgsWkbTypes.Type.Unknown):
            return "none"
        flat = QgsWkbTypes.flatType(QgsWkbTypes.singleType(wkb))
        return "point" if flat == QgsWkbTypes.Type.Point else "wkt"
    except Exception:  # noqa: BLE001 - a provider that cannot describe itself keeps the old answer
        return "none"


def load_csv(path: str, name: str, crs: str | None = None) -> dict:
    """Add the file to the project; points when it has coordinates."""
    try:
        info = sniff(path)
    except OSError as exc:
        return {"_error": f"Cannot read {os.path.basename(path)}: {exc}"}
    uri, geometry = build_uri(path, info, crs)
    layer = QgsVectorLayer(uri, name, "delimitedtext")
    if not layer.isValid():
        layer = QgsVectorLayer(path, name, "ogr")
        if not layer.isValid():
            return {"_error": f"Failed to load the table from: {path}"}



        geometry = _geometry_kind_of(layer)
    QgsProject.instance().addMapLayer(layer)
    out = {
        "name": layer.name(),
        "layer_id": layer.id(),
        "feature_count": layer.featureCount(),
        "crs": layer.crs().authid() if geometry != "none" else (layer.crs().authid() or ""),
        "geometry": geometry,
        "delimiter": info["delimiter"],
        "fields": [f.name() for f in layer.fields()][:40],
    }
    if geometry == "point":
        out["x_field"], out["y_field"] = info["lon"], info["lat"]
        if info["decimal"] == ",":
            out["decimal_separator"] = ","
        if not crs and info.get("metric"):
            out["_note"] = ("Coordinates look projected (large values); loaded as EPSG:3857 by guess. "
                            "Reload with crs=<EPSG code> if the source CRS is known.")
    elif geometry == "wkt":



        out["wkt_field"] = info.get("wkt")
        out["_note"] = (f"Geometry read from the WKT column {info.get('wkt')!r}. "
                        "Pass crs=<EPSG code> if the coordinates are not longitude/latitude.")
    else:
        out["_note"] = ("No latitude/longitude or WKT column found: loaded as an attribute table "
                        "without geometry. Join it to a layer or geocode its address field.")
    geometry_note = _geometry_check(layer, geometry, info)
    if geometry_note:
        out["_note"] = f"{out['_note']} {geometry_note}" if out.get("_note") else geometry_note
    return out
