# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


from __future__ import annotations

import json
import os
import re

from ..core import limits
from ..core.background import breathe, gc_paused
from .data_common import _is_number, _overpass_timeout












_LIFTED_CHECK_EVERY = 2000
_LIFTED_FAMILIES = ("points", "lines", "polygons")






_OVERPASS_TIMEOUT_MARKERS = ("runtime error", "out of memory")


def _overpass_timeout_remark(answer) -> str:

    remark = answer.get("remark") if isinstance(answer, dict) else None
    if isinstance(remark, str) and any(marker in remark.lower() for marker in _OVERPASS_TIMEOUT_MARKERS):
        return remark
    return ""

















_OSM_STREAM_QUERY_SHARE = 0.45
_OSM_STREAM_DOWNLOAD_SHARE = 0.6
_OSM_STREAM_CONVERT_SHARE = 0.85

_OSM_STREAM_SILENCE_S = 15


_OSM_STREAM_DISK_SHARE = 6
_OSM_STREAM_MIN_BYTES = 64 * 1024 * 1024
_OSM_STREAM_COMMIT_EVERY = 50_000


_OSM_STREAM_MAX_FIELDS = 1000
_OSM_STREAM_RENAMED = {"fid": "tag_fid", "geom": "tag_geom", "other_tags": "tag_other_tags"}
_OSM_STREAM_OWN_FIELDS = ("osm_id", "osm_type")
_OSM_GEOMETRY_TYPES = {"points": "Point", "lines": "Line", "polygons": "Polygon"}
_OVERPASS_REMARK_TAIL = re.compile(r'"remark"\s*:\s*("(?:[^"\\]|\\.)*")\s*\}\s*$')


class _LoadStopped(Exception):


    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _OverpassFile:


    EDGE = 64 * 1024
    PIECE = 1024 * 1024

    def __init__(self, path: str):
        self.path = path

    def remark(self) -> str:





        with open(self.path, "rb") as handle:
            head = handle.read(self.EDGE)
            size = os.fstat(handle.fileno()).st_size
            handle.seek(max(0, size - self.EDGE))
            tail = handle.read()
        text = head.decode("utf-8", "replace").lstrip()
        marker = text.find('"elements"')
        if not text.startswith("{") or marker < 0 or text.find("[", marker) < 0:
            raise ValueError("Invalid response from Overpass API")
        found = _OVERPASS_REMARK_TAIL.search(tail.decode("utf-8", "replace"))
        if not found:
            return ""
        try:
            return _overpass_timeout_remark({"remark": json.loads(found.group(1))})
        except ValueError:
            return ""

    def elements(self, check=None):

        decoder = json.JSONDecoder()
        with open(self.path, encoding="utf-8", errors="replace") as handle:
            text = handle.read(self.PIECE)
            marker = text.find('"elements"')
            start = text.find("[", marker) if marker >= 0 else -1
            if start < 0:
                raise ValueError("Invalid response from Overpass API")
            index, ended, want, count = start + 1, False, self.PIECE, 0
            while True:
                length = len(text)
                while index < length and text[index] in " \t\r\n,":
                    index += 1
                end = 0
                if index < length:
                    if text[index] == "]":
                        return
                    try:
                        item, end = decoder.raw_decode(text, index)
                    except ValueError:
                        end = -1
                    if end > 0:
                        if not isinstance(item, dict) or "type" not in item:
                            raise ValueError("Invalid response from Overpass API")
                        index, want = end, self.PIECE
                        count += 1
                        if check is not None and count % _LIFTED_CHECK_EVERY == 0:
                            check()
                        breathe(count)
                        yield item
                        continue
                if ended:
                    raise ValueError("Invalid response from Overpass API")

                piece = handle.read(want)
                ended = not piece
                text = text[index:] + piece
                index = 0
                want = min(want * 2, 64 * self.PIECE) if end < 0 else self.PIECE


class _NodeIndex:








    def __init__(self):
        from array import array

        self._ids = array("q")
        self._lons = array("i")
        self._lats = array("i")
        self._map: dict | None = None
        self._packed_limit = max(1, int(limits.current("MAX_DOWNLOAD_BYTES")) // 16)
        self._map_limit = int(limits.current("MAX_FEATURES_MATERIALISED"))

    def _refusal(self, limit: int) -> str:
        return (f"The answer names more than {limit:,} nodes for its ways to be drawn from, over what this "
                "computer keeps in memory for one load.")

    def add(self, node_id, lon, lat) -> str:

        if self._map is not None:
            if len(self._map) >= self._map_limit:
                return self._refusal(self._map_limit)
            self._map[node_id] = (lon, lat)
            return ""
        if self._ids and node_id <= self._ids[-1]:
            if len(self._ids) >= self._map_limit:
                return self._refusal(self._map_limit)
            self._map = {self._ids[i]: (self._lons[i] / 1e7, self._lats[i] / 1e7) for i in range(len(self._ids))}
            del self._ids[:], self._lons[:], self._lats[:]
            self._map[node_id] = (lon, lat)
            return ""
        if len(self._ids) >= self._packed_limit:
            return self._refusal(self._packed_limit)
        self._ids.append(int(node_id))
        self._lons.append(round(float(lon) * 1e7))
        self._lats.append(round(float(lat) * 1e7))
        return ""

    def coords(self, refs) -> list:

        import bisect

        if self._map is not None:
            return [self._map[ref] for ref in refs if ref in self._map]
        ids, found, size = self._ids, [], len(self._ids)
        for ref in refs:
            at = bisect.bisect_left(ids, ref)
            if at < size and ids[at] == ref:
                found.append((self._lons[at] / 1e7, self._lats[at] / 1e7))
        return found


def _osm_wkb(geometry: dict):

    import struct
    import sys
    from array import array
    from itertools import chain

    def points(run) -> bytes:
        flat = array("d", chain.from_iterable((point[0], point[1]) for point in run))
        if sys.byteorder != "little":
            flat.byteswap()
        return struct.pack("<I", len(run)) + flat.tobytes()

    def line(run) -> bytes:
        return struct.pack("<BI", 1, 2) + points(run)

    def polygon(rings) -> bytes:
        return struct.pack("<BII", 1, 3, len(rings)) + b"".join(points(ring) for ring in rings)

    kind, coordinates = geometry.get("type"), geometry.get("coordinates")
    if kind == "Point":
        return "points", struct.pack("<BIdd", 1, 1, float(coordinates[0]), float(coordinates[1]))
    if kind == "LineString":
        return "lines", struct.pack("<BII", 1, 5, 1) + line(coordinates)
    if kind == "MultiLineString":
        return "lines", struct.pack("<BII", 1, 5, len(coordinates)) + b"".join(line(run) for run in coordinates)
    if kind == "Polygon":
        return "polygons", struct.pack("<BII", 1, 6, 1) + polygon(coordinates)
    if kind == "MultiPolygon":
        return "polygons", struct.pack("<BII", 1, 6, len(coordinates)) + b"".join(polygon(p) for p in coordinates)
    return None, None


_OSM_STREAM_WRITE_FAILED = "write_failed"


def _packed(coords: list):

    from array import array
    from itertools import chain

    return array("d", chain.from_iterable(coords))


def _unpacked(flat) -> list:

    return list(zip(flat[0::2], flat[1::2]))


def _osm_stream_convert(answer: _OverpassFile, query: str, final_query: str, path: str, stem: str,
                        check, clock_check) -> dict:









    from osgeo import ogr, osr

    wants_nodes = _osm_asks(query, _OSM_NODE_WORDS)
    wants_ways = _osm_asks(query, _OSM_WAY_WORDS)
    ceiling = int(limits.current("MAX_FEATURES_MATERIALISED"))
    fields: dict = {}

    used = {name.lower() for name in (*_OSM_STREAM_OWN_FIELDS, *_OSM_STREAM_RENAMED)}
    overflow = False
    in_relations: set = set()
    needs_ways: set = set()
    ways_after_relations = False
    nodes = _NodeIndex() if _OSM_RECURSION.search(final_query) else None
    refusal = ""
    elements = 0

    def refused(sentence: str) -> dict:
        return {"_error": sentence + " Nothing was added.", "code": limits.CEILING_CODE,
                "suggestion": ("The ways with out geom, instead of (._;>;);out body, give each way its own "
                               "points; a smaller area also helps.")}

    def way_coords(el) -> list:
        if isinstance(el.get("geometry"), list) or nodes is None:
            return _osm_way_coords(el, {})
        return nodes.coords(el.get("nodes") or [])

    for el in answer.elements(check):
        elements += 1
        kind = el["type"]
        if kind == "node" and nodes is not None and "lat" in el and "lon" in el:
            refusal = nodes.add(el["id"], el["lon"], el["lat"])
            if refusal:
                return refused(refusal)
        elif kind == "way" and needs_ways:
            ways_after_relations = True
        tags = el.get("tags")
        if isinstance(tags, dict) and tags and (kind != "node" or wants_nodes):
            for key in tags:
                folded = str(key).lower()
                if folded in fields or folded in _OSM_STREAM_OWN_FIELDS:
                    continue
                if len(fields) >= _OSM_STREAM_MAX_FIELDS:
                    overflow = True
                    continue
                name = _OSM_STREAM_RENAMED.get(folded, str(key)) or "tag"
                while name.lower() in used:
                    name += "_"
                used.add(name.lower())
                fields[folded] = name
        if kind == "relation":
            for member in el.get("members") or []:
                if isinstance(member, dict) and member.get("type") == "way":
                    in_relations.add(member.get("ref"))
                    if not isinstance(member.get("geometry"), list):
                        needs_ways.add(member.get("ref"))
            if len(in_relations) > ceiling:
                return {"_error": (f"The answer's relations are made of more than {ceiling:,} ways, over what this "
                                   "computer keeps in memory for one load. Nothing was added."),
                        "code": limits.CEILING_CODE,
                        "suggestion": "A smaller area, or the relations' own tags without their members, fits."}
    if not elements:
        return {"elements": 0}

    names = list(fields.values())
    positions = {folded: 2 + at for at, folded in enumerate(fields)}
    other = 2 + len(names) if overflow else -1
    relation_ways: dict = {}
    if ways_after_relations:

        for el in answer.elements(check):
            if el["type"] == "way" and el["id"] in needs_ways:
                relation_ways[el["id"]] = _packed(way_coords(el))
    kinds ={"points": ogr.wkbPoint, "lines": ogr.wkbMultiLineString, "polygons": ogr.wkbMultiPolygon}
    wgs84 = osr.SpatialReference()
    wgs84.ImportFromEPSG(4326)
    wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    target = ogr.GetDriverByName("GPKG").CreateDataSource(path)
    if target is None:
        return {"_error": "The GeoPackage for this load could not be created. Nothing was added.",
                "code": "EXECUTION_FAILED"}
    write_failed = _OSM_STREAM_WRITE_FAILED
    layers: dict = {}
    counts = dict.fromkeys(_LIFTED_FAMILIES, 0)
    stopped = ""
    written = 0
    refusal = ""

    def layer_for(family: str):
        layer = layers.get(family)
        if layer is None:
            layer = target.CreateLayer(f"{stem}_{family}", wgs84, kinds[family], options=["SPATIAL_INDEX=YES"])
            layer.CreateField(ogr.FieldDefn("osm_id", ogr.OFTInteger64))
            layer.CreateField(ogr.FieldDefn("osm_type", ogr.OFTString))
            for name in names:
                layer.CreateField(ogr.FieldDefn(name, ogr.OFTString))
            if overflow:
                layer.CreateField(ogr.FieldDefn("other_tags", ogr.OFTString))
            layers[family] = layer
        return layer

    try:
        target.StartTransaction()
        for el in answer.elements(check):
            kind = el["type"]
            geometry = None
            if kind == "node" and "lat" in el and "lon" in el:
                if not el.get("tags") or not wants_nodes:
                    continue
                geometry = {"type": "Point", "coordinates": [el["lon"], el["lat"]]}
            elif kind == "way":
                coords = way_coords(el)
                if el["id"] in needs_ways and el["id"] not in relation_ways:
                    if len(relation_ways) >= ceiling:
                        refusal = (f"The answer's relations are made of more than {ceiling:,} ways, over what "
                                   "this computer keeps in memory for one load.")
                        break
                    relation_ways[el["id"]] = _packed(coords)
                if el["id"] in in_relations and not (el.get("tags") and wants_ways):
                    continue
                geometry = _osm_line_or_polygon(coords, el.get("tags"))
            elif kind == "relation":
                members = {member.get("ref"): _unpacked(relation_ways[member.get("ref")])
                           for member in el.get("members") or []
                           if isinstance(member, dict) and member.get("ref") in relation_ways}
                geometry = _osm_relation_geometry(el, members)
            if geometry is None and kind in ("way", "relation"):
                geometry = _osm_center_point(el)
            if geometry is None:
                continue
            family, wkb = _osm_wkb(geometry)
            if family is None:
                continue
            layer = layer_for(family)
            feature = ogr.Feature(layer.GetLayerDefn())
            feature.SetFieldInteger64(0, int(el["id"]))
            feature.SetFieldString(1, str(kind))
            extra = {}
            for key, value in (el.get("tags") or {}).items():
                folded = str(key).lower()
                if folded in _OSM_STREAM_OWN_FIELDS:
                    continue
                at = positions.get(folded)
                if at is None:
                    extra[key] = value
                else:
                    feature.SetFieldString(at, value if isinstance(value, str) else str(value))
            if extra and other >= 0:
                feature.SetFieldString(other, json.dumps(extra, ensure_ascii=False))
            feature.SetGeometryDirectly(ogr.CreateGeometryFromWkb(wkb))
            if layer.CreateFeature(feature) != 0:
                refusal = write_failed
                break
            counts[family] += 1
            written += 1
            if written % _OSM_STREAM_COMMIT_EVERY == 0:
                if target.CommitTransaction() != 0:
                    refusal = write_failed
                    break
                if clock_check():
                    stopped = "clock"
                    target.StartTransaction()
                    break
                target.StartTransaction()
        if target.CommitTransaction() != 0 and not refusal:
            refusal = write_failed
        extent = None
        for layer in layers.values():
            west, east, south, north = layer.GetExtent()
            extent = ((west, south, east, north) if extent is None else
                      (min(extent[0], west), min(extent[1], south), max(extent[2], east), max(extent[3], north)))
        tables = {family: layer.GetName() for family, layer in layers.items()}
    finally:
        layers = {}
        target = None
    if refusal == _OSM_STREAM_WRITE_FAILED:
        return {"_error": "Writing the GeoPackage failed partway (the disk may be full). Nothing was added.",
                "code": "EXECUTION_FAILED"}
    if refusal:
        return refused(refusal)
    return {"elements": elements, "counts": counts, "tables": tables, "extent": extent, "stopped": stopped}



_OSM_STATEMENT = re.compile(r"(?<![A-Za-z_.])(node|way|relation|rel|nwr|nw|nr|wr)\s*[\[(;]")
_OSM_NODE_WORDS = frozenset({"node", "nwr", "nw", "nr"})
_OSM_WAY_WORDS = frozenset({"way", "nwr", "nw", "wr"})


def _osm_asks(query: str, words: frozenset) -> bool:








    if not query:
        return True
    return any(match.group(1) in words for match in _OSM_STATEMENT.finditer(query))


def _osm_to_geojson(elements: list, query: str = "") -> dict:
    with gc_paused():
        return _osm_to_geojson_inner(elements, query)


def _osm_to_geojson_inner(elements: list, query: str = "") -> dict:
























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

    wants_nodes = _osm_asks(query, _OSM_NODE_WORDS)
    wants_ways = _osm_asks(query, _OSM_WAY_WORDS)
    for index, el in enumerate(elements):
        breathe(index)
        geom = None
        if el["type"] == "node" and "lat" in el and "lon" in el:
            if not el.get("tags") or not wants_nodes:
                continue
            geom = {"type": "Point", "coordinates": [el["lon"], el["lat"]]}
        elif el["type"] == "way":
            if el["id"] in in_relations and not (el.get("tags") and wants_ways):
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

    if not _OSM_ASKS_AREAS.search(query) or _OSM_RECURSION.search(query):
        return query
    if not _OSM_PLAIN_OUT.search(query):
        return query
    return _OSM_PLAIN_OUT.sub("out geom;", query)


def _bounded_query(query: str, box) -> str:










    south, west, north, east = box
    text = f"{south},{west},{north},{east}"
    body = query.replace("{{bbox}}", text).lstrip()
    setting = f"[bbox:{text}]"
    if body.startswith("["):

        end = body.find(";")
        if end > 0 and body[:end].rstrip().endswith("]"):
            head, rest = body[:end].rstrip(), body[end + 1:]
            start = head.find("[bbox:")
            if start >= 0:
                close = head.find("]", start)
                head = head[:start] + head[close + 1:] if close > start else head[:start]
            return f"{head}{setting};{rest}"
    return f"{setting};{body}"


def _with_overpass_settings(query: str, timeout: int | None = None) -> str:












    stripped = query.lstrip()
    head = ""
    if stripped.startswith("["):
        end = stripped.find(";")
        head = stripped[:end] if end != -1 else stripped
    additions = ""
    if "[out:" not in head:
        additions += "[out:json]"
    if "[timeout:" not in head:
        additions += f"[timeout:{timeout or _overpass_timeout()}]"
    if not additions:
        return query
    if stripped.startswith("["):
        return additions + stripped
    return f"{additions};{stripped}"


def _osm_way_coords(el: dict, nodes: dict) -> list:






    inline = el.get("geometry")
    if isinstance(inline, list):
        return [(p["lon"], p["lat"]) for p in inline
                if isinstance(p, dict) and "lon" in p and "lat" in p]
    return [nodes[nid] for nid in el.get("nodes", []) if nid in nodes]


def _osm_center_point(el: dict) -> dict | None:








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







    if len(coords) < 2:
        return None
    closed = coords[0] == coords[-1] and len(coords) >= 4
    if closed and _osm_is_area(tags) is not False:
        return {"type": "Polygon", "coordinates": [coords]}
    return {"type": "LineString", "coordinates": coords}





_OSM_AREA_RELATIONS = frozenset({"multipolygon", "boundary"})


def _osm_relation_geometry(el: dict, ways: dict) -> dict | None:













    parts, roles, seen = [], [], set()
    for member in el.get("members", []):
        if member.get("type") != "way":
            continue
        ref = member.get("ref")
        if ref is not None and ref in seen:
            continue
        seen.add(ref)
        coords = _osm_way_coords(member, {}) or ways.get(ref) or []
        if len(coords) >= 2:
            parts.append(coords)
            roles.append(str(member.get("role") or "outer"))
    if not parts and isinstance(el.get("geometry"), list):

        coords = _osm_way_coords(el, {})
        if len(coords) >= 2:
            parts.append(coords)
            roles.append("outer")
    if not parts:
        return None
    if str((el.get("tags") or {}).get("type") or "") in _OSM_AREA_RELATIONS:
        area = _osm_area_of(parts, roles)
        if area is not None:
            return area
    if all(part[0] == part[-1] and len(part) >= 4 for part in parts):
        return {"type": "MultiPolygon", "coordinates": [[part] for part in parts]}
    return {"type": "MultiLineString", "coordinates": parts}


def _osm_area_of(parts: list, roles: list) -> dict | None:

    outers = _osm_rings([part for part, role in zip(parts, roles) if role != "inner"])
    inners = _osm_rings([part for part, role in zip(parts, roles) if role == "inner"])
    if not outers or inners is None:
        return None
    from .data_overture import _ring_contains

    polygons = [[ring] for ring in outers]
    for hole in inners:
        x, y = hole[0]
        holder = next((polygon for polygon in polygons if _ring_contains(x, y, polygon[0])), None)
        if holder is None:
            return None
        holder.append(hole)
    return {"type": "MultiPolygon", "coordinates": polygons}



_OSM_NO_AREA = 1e-12


def _osm_rings(parts: list) -> list | None:






    rings = [list(part) for part in parts if len(part) >= 4 and part[0] == part[-1]]
    runs = {index: part for index, part in enumerate(parts) if not (len(part) >= 4 and part[0] == part[-1])}
    ends: dict = {}
    for index, run in runs.items():
        ends.setdefault(run[0], []).append(index)
        ends.setdefault(run[-1], []).append(index)

    def take(index: int) -> list:
        run = runs.pop(index)
        ends[run[0]].remove(index)
        ends[run[-1]].remove(index)
        return run

    while runs:
        breathe(len(rings))
        ring = list(take(next(iter(runs))))
        while ring[0] != ring[-1]:
            following = ends.get(ring[-1])
            if not following:
                return None
            run = take(following[0])
            ring.extend(run[1:] if run[0] == ring[-1] else run[-2::-1])
        rings.append(ring)
    for ring in rings:
        if len(ring) < 4 or abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:]))) < _OSM_NO_AREA:
            return None
    return rings


def _geojson_bounds(features: list, cap: int = 400_000) -> tuple | None:

    west = south = east = north = None
    seen = 0

    def walk(coords) -> bool:

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









    return _reach_past(_geojson_bounds(features), box)


def _reach_past(bounds, box: tuple) -> dict | None:

    west, south, east, north = box
    span_x, span_y = abs(east - west), abs(north - south)
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




__all__ = [
    "_LIFTED_CHECK_EVERY",
    "_LIFTED_FAMILIES",
    "_LoadStopped",
    "_OSM_GEOMETRY_TYPES",
    "_OSM_STREAM_CONVERT_SHARE",
    "_OSM_STREAM_DISK_SHARE",
    "_OSM_STREAM_DOWNLOAD_SHARE",
    "_OSM_STREAM_MIN_BYTES",
    "_OSM_STREAM_QUERY_SHARE",
    "_OSM_STREAM_SILENCE_S",
    "_OVERPASS_TIMEOUT_MARKERS",
    "_OverpassFile",
    "_beyond_bbox",
    "_geometry_counts",
    "_osm_stream_convert",
    "_osm_to_geojson",
    "_overpass_timeout_remark",
    "_reach_past",
    "_with_drawable_out",
    "_with_overpass_settings",
]
