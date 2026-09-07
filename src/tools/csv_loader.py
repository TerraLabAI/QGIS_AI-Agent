# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later









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

from ..core.host_platform import IS_WINDOWS
from ..core.tool_registry import tool_error

CSV_EXTENSIONS = (".csv", ".tsv", ".txt")

_LAT = ("latitude", "lat", "y", "ycoord", "y_coord", "northing", "lat_dd", "latitude_dd")
_LON = ("longitude", "lon", "lng", "long", "x", "xcoord", "x_coord", "easting", "lon_dd", "longitude_dd")
_WKT = ("wkt", "geometry", "geom", "the_geom", "wkt_geom", "geom_wkt")
_DECIMAL_COMMA = re.compile(r"^-?\d+,\d+$")


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.strip().lower())


def detect_encoding(sample: bytes) -> str:









    if sample.startswith(codecs.BOM_UTF8):
        return "UTF-8"
    if sample.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):

        return "UTF-16"
    try:



        codecs.getincrementaldecoder("utf-8")().decode(sample, final=False)
    except UnicodeDecodeError:
        return _ansi_encoding()
    return "UTF-8"




_ANSI_NAMES = {874: "cp874", 932: "Shift_JIS", 936: "GBK", 949: "EUC-KR", 950: "Big5",
               **{page: f"windows-{page}" for page in range(1250, 1259)}}


def _ansi_encoding() -> str:







    if IS_WINDOWS:
        try:
            import ctypes

            name = _ANSI_NAMES.get(int(ctypes.windll.kernel32.GetACP()))
            if name:
                return name
        except Exception:  # nosec B110
            pass
    return "windows-1252"


def sniff(path: str) -> dict:

    with open(path, "rb") as fh:
        raw = fh.read(65536)
    encoding = detect_encoding(raw)


    sample = raw.decode("utf-8-sig" if encoding == "UTF-8" else encoding, errors="replace")
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") > sample.count(",") else ","




    try:
        rows = list(itertools.islice(csv.reader(io.StringIO(sample), delimiter=delimiter), 50))
    except csv.Error:

        rows = [line.split(delimiter) for line in sample.splitlines()[:50]]
    rows = [r for r in rows if r]
    header = rows[0] if rows else []
    body = rows[1:]
    keys = {_norm(h): h for h in header}
    lat = next((keys[k] for k in map(_norm, _LAT) if k in keys), None)
    lon = next((keys[k] for k in map(_norm, _LON) if k in keys), None)
    wkt = next((keys[k] for k in map(_norm, _WKT) if k in keys), None)
    decimal = "."
    projected = False
    box = None
    if lat and lon and body:
        i_lat, i_lon = header.index(lat), header.index(lon)
        cells = [r[i] for r in body for i in (i_lat, i_lon) if i < len(r)]
        if cells and all(_DECIMAL_COMMA.match(c.strip()) for c in cells if c.strip()):
            decimal = ","
        xs = _numbers(r[i_lon] for r in body if i_lon < len(r))
        ys = _numbers(r[i_lat] for r in body if i_lat < len(r))
        projected = bool(xs and ys) and not lonlat_fits(min(xs), max(xs), min(ys), max(ys))
        if xs and ys:
            box = (min(xs), min(ys), max(xs), max(ys))
    return {"delimiter": delimiter, "header": header, "rows": len(body),
            "lat": lat, "lon": lon, "wkt": wkt, "decimal": decimal, "projected": projected,
            "encoding": encoding, "box": box}


def _numbers(cells) -> list[float]:

    out = []
    for cell in cells:
        try:
            out.append(float(cell.strip().replace(",", ".")))
        except ValueError:
            continue
    return out


def lonlat_fits(xmin: float, xmax: float, ymin: float, ymax: float) -> bool:






    def fits(x0, x1, y0, y1):
        return x0 >= -180.0 and x1 <= 180.0 and y0 >= -90.0 and y1 <= 90.0
    return fits(xmin, xmax, ymin, ymax) or fits(ymin, ymax, xmin, xmax)


def build_uri(path: str, info: dict, crs: str | None = None) -> tuple[str, str]:


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
        params.append("crs=" + (crs or "EPSG:4326"))
    return QUrl.fromLocalFile(os.path.abspath(path)).toString() + "?" + "&".join(params), geometry


_GEOMETRY_SAMPLE_LIMIT = 2000


def _geometry_check(layer, geometry: str, info: dict) -> str | None:

    if geometry not in ("point", "wkt"):
        return None
    lat_field, lon_field = info.get("lat"), info.get("lon")
    lat_idx = layer.fields().indexOf(lat_field) if lat_field else -1
    lon_idx = layer.fields().indexOf(lon_field) if lon_field else -1
    total = 0
    empty = 0
    swap_suspect = False
    request = QgsFeatureRequest().setLimit(_GEOMETRY_SAMPLE_LIMIT)


    if lat_idx >= 0 and lon_idx >= 0 and not info.get("projected"):
        request.setSubsetOfAttributes(sorted({lat_idx, lon_idx}))
    for feat in layer.getFeatures(request):
        total += 1
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            empty += 1
        if lat_idx >= 0 and lon_idx >= 0 and not info.get("projected"):
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

    try:
        from qgis.core import QgsWkbTypes

        wkb = layer.wkbType()
        if wkb in (QgsWkbTypes.Type.NoGeometry, QgsWkbTypes.Type.Unknown):
            return "none"
        flat = QgsWkbTypes.flatType(QgsWkbTypes.singleType(wkb))
        return "point" if flat == QgsWkbTypes.Type.Point else "wkt"
    except Exception:  # noqa: BLE001
        return "none"





_WORLD_WIDTH_DEGREES = 90.0


_AREA_MARGIN_DEGREES = 1.0


def _fits_area(candidate, extent) -> bool | None:


    from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform

    try:
        bounds = candidate.bounds()
        if bounds.isEmpty() or bounds.width() >= _WORLD_WIDTH_DEGREES:
            return None
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        box = QgsCoordinateTransform(candidate, wgs84, QgsProject.instance()).transformBoundingBox(extent)
    except Exception:  # noqa: BLE001
        return False
    margin = _AREA_MARGIN_DEGREES
    return (box.xMinimum() >= bounds.xMinimum() - margin and box.xMaximum() <= bounds.xMaximum() + margin
            and box.yMinimum() >= bounds.yMinimum() - margin and box.yMaximum() <= bounds.yMaximum() + margin)


def _covers(candidate, extent) -> bool:

    return _fits_area(candidate, extent) is True


def _sidecar_crs(path: str):

    from qgis.core import QgsCoordinateReferenceSystem

    stem = os.path.splitext(path)[0]
    for prj in (stem + ".prj", stem + ".PRJ"):
        if not os.path.isfile(prj):
            continue
        try:
            with open(prj, encoding="utf-8", errors="replace") as fh:
                crs = QgsCoordinateReferenceSystem.fromWkt(fh.read().strip())
        except OSError:
            return None
        if crs.isValid() and not crs.isGeographic():
            return crs, os.path.basename(prj)
    return None


def _project_candidates(extent) -> list:

    project = QgsProject.instance()
    sources = [(project.crs(), "the project CRS")]
    sources += [(layer.crs(), f"the CRS of layer '{layer.name()}'") for layer in project.mapLayers().values()]
    found: dict = {}
    for candidate, origin in sources:
        if not candidate.isValid() or candidate.isGeographic():
            continue
        key = candidate.authid() or candidate.toWkt()
        if key not in found and _covers(candidate, extent):
            found[key] = (candidate, origin)
    return list(found.values())


def _outside_named_crs(path: str, named, extent):

    if extent.isNull() or _fits_area(named, extent) is not False:
        return None
    label = named.authid() or named.description()
    candidates = [c.authid() for c, _origin in _project_candidates(extent) if c.authid() and c.authid() != label]
    box = [round(v, 2) for v in (extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum())]
    return tool_error(
        f"CRS_OUTSIDE_AREA: read in {label}, the coordinates of {os.path.basename(path)} ({box}) land outside "
        f"the area {label} is defined for, so they were not written in it. Nothing was loaded.",
        code="INVALID_ARGS",
        hint="csv_crs_outside_area", variant="candidates" if candidates else "none",
        crs=label, coordinates=box, candidate_crs=candidates)


def _off_project(path: str, named, extent):






    from . import crs_landing

    anchor_list = crs_landing.anchors()
    if extent.isNull() or not anchor_list:
        return None
    on, km = crs_landing.landing(named, extent, anchor_list)
    if on:
        return None
    label = named.authid() or named.description()
    found = crs_landing.candidates(extent, anchor_list, exclude=named.authid())
    if not found:
        return None
    best = found[0]
    where = ", ".join(f"'{n}'" for n in best["lands_on"])
    far = f"{km:.0f} km from every layer of the project" if km is not None else "nowhere near the project"
    box = [round(v, 2) for v in (extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum())]
    candidate_crs = [c["crs"] for c in found[:3]]
    facts = {"km": round(km)} if km is not None else {}
    return tool_error(
        f"CRS_OFF_PROJECT: read in {label}, the coordinates of {os.path.basename(path)} ({box}) land {far}; "
        f"{crs_landing.sentence(found)}. Nothing was loaded.",
        code="INVALID_ARGS",
        hint="csv_crs_off_project", crs=label, candidate_crs=candidate_crs, best=best["crs"], lands_on=where,
        coordinates=box, **facts)


def _projected_source(path: str, layer, info: dict, crs: str | None):




















    from qgis.core import QgsRectangle

    try:
        extent = QgsRectangle(layer.extent())
        if not layer.crs().isGeographic():
            return _outside_named_crs(path, layer.crs(), extent) or _off_project(path, layer.crs(), extent)
    except Exception:  # noqa: BLE001
        return None
    if extent.isNull() or lonlat_fits(extent.xMinimum(), extent.xMaximum(), extent.yMinimum(), extent.yMaximum()):
        return None
    base = os.path.basename(path)
    columns = (f"columns {info.get('lon')}/{info.get('lat')}" if info.get("lat") and info.get("lon")
               else f"WKT column {info.get('wkt')}")
    def num(value: float) -> str:
        return f"{value:.0f}" if abs(value) >= 1000 else f"{value:.6g}"
    numbers = (f"x runs from {num(extent.xMinimum())} to {num(extent.xMaximum())} and y from "
               f"{num(extent.yMinimum())} to {num(extent.yMaximum())}")
    if crs:
        return {
            "_error": (f"PROJECTED_COORDINATES: {base} was asked for in {crs}, a longitude/latitude CRS, but its "
                       f"{columns} hold projected coordinates ({numbers}). Nothing was loaded."),
            "code": "INVALID_ARGS",
            "suggestion": ("Call again with crs set to the projected CRS the data is in (a UTM zone, a national "
                           "grid) as the source or the user names it; ask the user when nothing names it."),
        }
    sidecar = _sidecar_crs(path)
    if sidecar is not None:
        found, prj = sidecar
        label = found.authid() or found.description()
        return found, (f"Loaded in {label}, read from {prj} next to the file: the coordinates are projected "
                       f"({numbers}), not longitude/latitude.")
    candidates = _project_candidates(extent)
    if len(candidates) == 1:
        found, origin = candidates[0]
        label = found.authid() or found.description()
        return found, (f"No CRS was given and the coordinates are projected ({numbers}), not longitude/latitude: "
                       f"loaded in {label}, {origin}, the only one in the project whose area of use holds them. "
                       f"Tell the user, and load again with crs=<EPSG code> if the source names another.")
    named = ", ".join(f"{(c.authid() or c.description())} ({origin})" for c, origin in candidates)
    from . import crs_landing

    landing = crs_landing.candidates(extent, crs_landing.anchors())
    candidate_crs = list(dict.fromkeys([c["crs"] for c in landing[:3]]
                                       + [c.authid() for c, _origin in candidates if c.authid()]))
    return {
        "_error": (f"PROJECTED_COORDINATES: the {columns} of {base} hold projected coordinates ({numbers}), not "
                   f"longitude/latitude, and nothing says which CRS they are in. Loaded as EPSG:4326 they "
                   f"would land nowhere, so nothing was loaded."
                   + (f" More than one CRS of the project fits them: {named}." if candidates else "")
                   + (f" Read against the project's layers: {crs_landing.sentence(landing)}." if landing else "")),
        "code": "INVALID_ARGS",
        "candidate_crs": candidate_crs,
        "suggestion": (f"Call the same loader again with crs={landing[0]['crs']} when the user expects the file on "
                       f"{', '.join(repr(n) for n in landing[0]['lands_on'])}, unless the source or its metadata "
                       "names another CRS; if not, ask the user." if landing else
                       "Call the same loader again with crs=<EPSG code> for the CRS the source, its metadata or the "
                       "user names (a UTM zone, a national grid). When nothing names it, ask the user: the "
                       "numbers alone cannot tell one UTM zone from another."),
    }


def load_csv(path: str, name: str, crs: str | None = None) -> dict:

    try:
        info = sniff(path)
    except OSError as exc:
        return {"_error": f"Cannot read {os.path.basename(path)}: {exc}"}
    uri, geometry = build_uri(path, info, crs)
    layer = QgsVectorLayer(uri, name, "delimitedtext")
    crs_note = ""
    if not layer.isValid():
        layer = QgsVectorLayer(path, name, "ogr")
        if not layer.isValid():
            return {"_error": f"Failed to load the table from: {path}"}



        geometry = _geometry_kind_of(layer)
    elif geometry != "none":
        decided = _projected_source(path, layer, info, crs)
        if isinstance(decided, dict):
            return decided
        if decided is not None:
            source_crs, crs_note = decided
            if source_crs.authid():
                uri, geometry = build_uri(path, info, source_crs.authid())
                layer = QgsVectorLayer(uri, name, "delimitedtext")
            else:
                layer.setCrs(source_crs)
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
    elif geometry == "wkt":



        out["wkt_field"] = info.get("wkt")
        out["_note"] = (f"Geometry read from the WKT column {info.get('wkt')!r}. "
                        "Pass crs=<EPSG code> if the coordinates are not longitude/latitude.")
    else:
        out["_note"] = ("No latitude/longitude or WKT column found: loaded as an attribute table "
                        "without geometry. Join it to a layer or geocode its address field.")
    if crs_note:
        out["_note"] = f"{crs_note} {out['_note']}" if out.get("_note") else crs_note
    geometry_note = _geometry_check(layer, geometry, info)
    if geometry_note:
        out["_note"] = f"{out['_note']} {geometry_note}" if out.get("_note") else geometry_note
    return out
