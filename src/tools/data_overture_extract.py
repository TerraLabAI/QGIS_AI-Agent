# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


from __future__ import annotations

import json
import os

from qgis.core import QgsProject, QgsTask, QgsVectorLayer

from ..core import limits, net
from ..core.background import run_on_main_thread
from ..core.host_platform import remove_quietly, remove_tree
from ..core.logger import log_warning
from ..core.policy import create_managed_temp_dir
from . import volume_guard
from .data_common import _USER_AGENT, _bbox_km2, _footprint_box
from .data_inspect import _VSICURL_TIMEOUT_S, _human_bytes, _safe_extract_stem, _tune_gdal_for_range_reads
from .data_osm_geometry import _LIFTED_CHECK_EVERY, _LIFTED_FAMILIES, _beyond_bbox
from .data_overture import (
    _CLIP_CHECK_EVERY,
    _OVERTURE_LICENCES,
    _OVERTURE_MAX_KM2,
    _OVERTURE_MAX_SPAN_DEG,
    _OVERTURE_MAX_TILES,
    _OVERTURE_TILE_ZOOM,
    _clip_split_refusal,
    _division_names,
    _divisions_subtypes_asked,
    _filter_miss_suggestion,
    _named_sentence,
    _osm_themes,
    _outline_contains,
    _overture_attribution,
    _overture_boxes,
    _overture_divisions_named,
    _overture_divisions_presence,
    _overture_feature_key,
    _overture_filter_miss,
    _overture_layer,
    _overture_layer_name,
    _overture_matches,
    _overture_source,
    _overture_stream,
    _overture_themes,
    _overture_tile_url,
    _overture_tiles,
    _split_box,
    _stop_if_cancelled,
    _stream_refusal,
    _subset_string,
    _tile_sample,
    _tiles_base,
)
from .data_places import _resolve_outline


def _town_elsewhere(levels) -> str:



    return (f"Overture has no outline for this town here. The {', '.join(sorted(levels))} in this box are "
            "larger units: a county or region often carries the town's name but is not the town. "
            "OpenStreetMap's place or boundary polygon (fetch_osm_data) is the town's own outline where one exists.")


def _say_named(empty: dict, asked: list, names: list, box) -> bool:





    named = _overture_divisions_named(box, names) if names and asked else None
    if named is None:
        return False
    empty["named_here"] = named[:6]
    if not named:
        empty["message"] = f"{empty.get('message') or ''} {_named_sentence(asked, names, [])}".strip()
        return False
    empty["message"] = _named_sentence(asked, names, named)
    if "locality" in asked:
        empty["suggestion"] = _town_elsewhere({entry["subtype"] for entry in named})
    else:
        empty["suggestion"] = (f'filter {{"subtype": "{named[0]["subtype"]}"}} reads the {named[0]["subtype"]} '
                               f'named {named[0]["name"]!r}.')
    return True


def _overture_clip(theme: str, box, args: dict, deadline: float | None = None, later: list | None = None) -> dict:







    west, south, east, north = box
    call = {"theme": theme, "mode": "clip",
            "bbox": {"south": south, "west": west, "north": north, "east": east}}
    for key in ("layer_name", "confirm_area_km2", "filter", "full_extent"):
        if args.get(key) is not None:
            call[key] = args[key]
    return _fetch_overture(call, deadline=deadline, later=later)

_LIFTED_CLOCK_SHARE = 0.8


_LIFTED_OUTLINE_VERTICES = 4000
_OUTLINE_PRECISION_DEG = 0.002


def _lifted_deadline() -> float:






    import time

    budget = min(float(limits.current("OVERTURE_LIFTED_READ_SECONDS")),
                 limits.current("CALL_MAX_SECONDS_BACKGROUND") * _LIFTED_CLOCK_SHARE)
    return time.monotonic() + budget


def _extract_miss_reason(text: str) -> str:

    text = str(text or "").lower()


    absent = ("404" in text or "does not exist in the file system" in text or "no such file" in text)
    return "absent" if absent else "unknown"


def _features_until_failure(layer, failures: list):


    features = iter(layer)
    while True:
        try:
            feature = next(features)
        except StopIteration:
            return
        except RuntimeError as exc:
            failures.append(str(exc))
            return
        yield feature





_FAMILY_OF_FLAT: dict = {}


def _lifted_family(geometry_type: int) -> str:
    if not _FAMILY_OF_FLAT:
        from osgeo import ogr

        _FAMILY_OF_FLAT.update({ogr.wkbPoint: "points", ogr.wkbMultiPoint: "points",
                                ogr.wkbLineString: "lines", ogr.wkbMultiLineString: "lines",
                                ogr.wkbPolygon: "polygons", ogr.wkbMultiPolygon: "polygons",
                                "flatten": ogr.GT_Flatten})
    return _FAMILY_OF_FLAT.get(_FAMILY_OF_FLAT["flatten"](geometry_type), "")


def _outline_shape(outline: dict):

    from osgeo import ogr

    polys = outline.get("polys") or []
    coordinates = [[[list(point) for point in exterior]] + [[list(point) for point in hole] for hole in holes]
                   for exterior, holes in polys]
    shape = ogr.CreateGeometryFromJson(json.dumps({"type": "MultiPolygon", "coordinates": coordinates}))
    if shape is None:
        return None
    if sum(len(ring) for polygon in coordinates for ring in polygon) > _LIFTED_OUTLINE_VERTICES:
        shape = shape.SimplifyPreserveTopology(_OUTLINE_PRECISION_DEG)
    if not shape.IsValid():
        shape = shape.Buffer(0)
    return shape





_SELECTED_WHOLE_THEMES = frozenset({"divisions", "boundaries"})


def _outline_test(outline: dict, cut: bool = False):





















    shape = _outline_shape(outline)
    if shape is None:
        return None
    min_x, max_x, min_y, max_y = shape.GetEnvelope()
    core = shape.PointOnSurface()
    inner: list = []

    def deep_inside(geometry) -> bool:








        if not inner:
            inner.append(shape.Buffer(-_OUTLINE_PRECISION_DEG))
        if inner[0] is None or inner[0].IsEmpty() or not geometry.Intersects(inner[0]):
            return False
        shrunk = geometry.Buffer(-_OUTLINE_PRECISION_DEG)
        return shrunk is not None and not shrunk.IsEmpty() and shrunk.Intersects(inner[0])

    def keep(geometry):
        low_x, high_x, low_y, high_y = geometry.GetEnvelope()
        if high_x < min_x or low_x > max_x or high_y < min_y or low_y > max_y:
            return None
        family = _lifted_family(geometry.GetGeometryType())
        if cut:
            return _cut(geometry, shape, family)
        if family == "polygons":
            if not geometry.IsValid():
                geometry = geometry.Buffer(0)
            probe = geometry.PointOnSurface()
            if probe is not None and not probe.IsEmpty() and _outline_contains(probe.GetX(), probe.GetY(), outline):
                return geometry
            if core is not None and geometry.Contains(core):
                return geometry
            return geometry if deep_inside(geometry) else None
        if family == "lines":
            return geometry if geometry.Intersects(shape) and not geometry.Touches(shape) else None
        return geometry if shape.Intersects(geometry) else None

    return keep


def _cut(geometry, shape, family: str):

    if not family:
        return None
    if family == "points":
        return geometry if shape.Intersects(geometry) else None
    if family == "polygons" and not geometry.IsValid():
        geometry = geometry.Buffer(0)
    if geometry.Within(shape):
        return geometry
    return _own_kind(geometry.Intersection(shape), family)


def _own_kind(part, family: str):

    from osgeo import ogr

    if part is None or part.IsEmpty():
        return None
    if _lifted_family(part.GetGeometryType()) == family:
        return part


    pieces = ogr.Geometry(ogr.wkbGeometryCollection)
    stack = [part]
    while stack:
        piece = stack.pop()
        flat = ogr.GT_Flatten(piece.GetGeometryType())
        if flat in (ogr.wkbGeometryCollection, ogr.wkbMultiLineString, ogr.wkbMultiPolygon, ogr.wkbMultiPoint):
            stack.extend(piece.GetGeometryRef(i) for i in range(piece.GetGeometryCount()))
        elif _lifted_family(piece.GetGeometryType()) == family and not piece.IsEmpty():
            pieces.AddGeometry(piece)
    if pieces.IsEmpty():
        return None
    return ogr.ForceTo(pieces, _multi_kind(family))


def _multi_kind(family: str) -> int:
    from osgeo import ogr

    return {"points": ogr.wkbMultiPoint, "lines": ogr.wkbMultiLineString}.get(family, ogr.wkbMultiPolygon)


def _made_valid(geometry, family: str):


    if geometry.IsValid():
        return geometry
    try:
        made = geometry.MakeValid()
    except Exception:  # noqa: BLE001
        made = geometry.Buffer(0) if family == "polygons" else None
    return _own_kind(made, family) if made is not None else None


def _union_pieces(pieces: list):






    from osgeo import ogr

    families = {_lifted_family(piece.GetGeometryType()) for piece in pieces if piece is not None}
    if len(families) != 1 or "" in families or any(piece is None or piece.IsEmpty() for piece in pieces):
        return None
    family = families.pop()
    shapes = [_made_valid(piece, family) for piece in pieces]


    while len(shapes) > 1:
        if any(shape is None for shape in shapes):
            return None
        paired = [shapes[index].Union(shapes[index + 1]) for index in range(0, len(shapes) - 1, 2)]
        shapes = paired + shapes[len(paired) * 2:]
    merged = shapes[0]
    merged = _made_valid(merged, family) if merged is not None and not merged.IsEmpty() else None
    return ogr.ForceTo(merged, _multi_kind(family)) if merged is not None else None


def _osm_piece_key(get) -> tuple | None:

    osm_id = get("osm_id")
    return None if osm_id in (None, "") else (get("osm_type") or "", osm_id)


def _merge_pieces(features: list, stopped=None) -> list:







    from osgeo import ogr

    places: dict = {}
    for index, feature in enumerate(features):
        properties = feature.get("properties") if isinstance(feature, dict) else None
        key = _osm_piece_key(properties.get) if isinstance(properties, dict) else None
        if key is not None:
            places.setdefault(key, []).append(index)
    merged: dict = {}
    for number, indexes in enumerate(group for group in places.values() if len(group) > 1):
        if number % _CLIP_CHECK_EVERY == 0:
            _stop_if_cancelled(stopped)
        shapes = [features[index].get("geometry") for index in indexes]
        if not all(isinstance(shape, dict) for shape in shapes):
            continue
        union = _union_pieces([ogr.CreateGeometryFromJson(json.dumps(shape)) for shape in shapes])
        if union is None:
            continue
        merged.update(dict.fromkeys(indexes[1:]))
        merged[indexes[0]] = {**features[indexes[0]], "geometry": json.loads(union.ExportToJson())}
    if not merged:
        return features
    kept = []
    for index, feature in enumerate(features):
        if index not in merged:
            kept.append(feature)
        elif merged[index] is not None:
            kept.append(merged[index])
    return kept



_CLASS_COUNT_SHOWN = 12

_NAMES_SHOWN = 30



_OVERTURE_CLASS_FIELDS = ("subtype", "class", "category")


_OSM_COVER_KEY = "natural"


def _osm_class_fields(theme: str) -> tuple:


    routing = volume_guard._routing()
    keys = {key for key, owner in routing.theme_keys.items() if owner == theme}
    keys |= {key for key, (_values, owner) in routing.value_themes.items() if owner == theme}
    published = routing.fields.get(theme) or frozenset()
    ordered = tuple(sorted(key for key in keys if not published or key in published))
    return ordered + ((_OSM_COVER_KEY,) if _OSM_COVER_KEY in published and _OSM_COVER_KEY not in keys else ())


def _class_of(get, theme: str, osm_fields: tuple) -> str:

    if osm_fields:
        for field in osm_fields:
            value = get(field)
            if value not in (None, ""):
                return f"{field}={value}"
        return "untagged"
    values = [get(field) for field in _OVERTURE_CLASS_FIELDS]
    return "/".join(str(value) for value in values if value not in (None, "")) or "unclassified"


class _ClassCounter:


    def __init__(self, theme: str):
        self.theme = theme
        self.osm_fields = _osm_class_fields(theme) if theme in _osm_themes() else ()
        self.counts: dict = {}

    def add(self, get, count: int = 1) -> None:
        label = _class_of(get, self.theme, self.osm_fields)
        self.counts[label] = self.counts.get(label, 0) + count

    def result(self) -> dict:
        ranked = sorted(self.counts.items(), key=lambda pair: -pair[1])
        shown = dict(ranked[:_CLASS_COUNT_SHOWN])
        rest = sum(count for _label, count in ranked[_CLASS_COUNT_SHOWN:])
        if rest:
            shown["other"] = shown.get("other", 0) + rest
        return shown



_COVER_BATCH = 2_000


def _polygon_cover(geometries, area_shape, stopped=None):




    from osgeo import ogr

    total = area_shape.GetArea() if area_shape is not None else 0.0
    if total <= 0:
        return None
    unions, batch = [], ogr.Geometry(ogr.wkbMultiPolygon)
    for geometry in geometries:
        if geometry is None or _lifted_family(geometry.GetGeometryType()) != "polygons":
            continue
        if not geometry.IsValid():
            geometry = geometry.Buffer(0)
        flat = ogr.GT_Flatten(geometry.GetGeometryType())
        parts = ([geometry.GetGeometryRef(i) for i in range(geometry.GetGeometryCount())]
                 if flat == ogr.wkbMultiPolygon else [geometry])
        for part in parts:
            batch.AddGeometry(ogr.ForceTo(part.Clone(), ogr.wkbPolygon))
        if batch.GetGeometryCount() >= _COVER_BATCH:
            _stop_if_cancelled(stopped)
            unions.append(batch.UnionCascaded())
            batch = ogr.Geometry(ogr.wkbMultiPolygon)
    if batch.GetGeometryCount():
        unions.append(batch.UnionCascaded())
    if not unions:
        return 0.0



    while len(unions) > 1:
        paired = []
        for index in range(0, len(unions) - 1, 2):
            _stop_if_cancelled(stopped)
            paired.append(unions[index].Union(unions[index + 1]))
        if len(unions) % 2:
            paired.append(unions[-1])
        unions = paired
    covered = unions[0]
    inside = covered.Intersection(area_shape) if covered is not None else None
    return round(min(1.0, (inside.GetArea() if inside is not None else 0.0) / total), 3)


def _served_cover(features: list, outline, box, stopped):

    from osgeo import ogr

    geometries = []
    for feature in features:
        shape = feature.get("geometry") if isinstance(feature, dict) else None
        if isinstance(shape, dict) and str(shape.get("type") or "").endswith("Polygon"):
            geometries.append(ogr.CreateGeometryFromJson(json.dumps(shape)))
    if not geometries:
        return None
    west, south, east, north = box
    area = _outline_shape(outline) if outline is not None else None
    if area is None:
        ring = ogr.Geometry(ogr.wkbLinearRing)
        for x, y in ((west, south), (east, south), (east, north), (west, north), (west, south)):
            ring.AddPoint_2D(x, y)
        area = ogr.Geometry(ogr.wkbPolygon)
        area.AddGeometry(ring)
    return _polygon_cover(geometries, area, stopped)


def _cover_entry(share, area_label: str, theme: str) -> dict:
    percent = round(share * 100)
    return {"share": share, "of": area_label,
            "note": (f"{percent}% of {area_label} lies under a returned {theme} polygon; the other "
                     f"{100 - percent}% has none in the source, which says nothing of what is there.")}


def _tile_bounds(label: str):

    import math

    try:
        x, y = (int(part) for part in str(label).split("/"))
    except ValueError:
        return None
    side = 2 ** _OVERTURE_TILE_ZOOM

    def latitude(row: int) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * row / side))))

    return (x / side * 360.0 - 180.0, latitude(y + 1), (x + 1) / side * 360.0 - 180.0, latitude(y))


def _strictly_inside(geometry, bounds) -> bool:

    try:
        min_x, max_x, min_y, max_y = geometry.GetEnvelope()
    except Exception:  # noqa: BLE001
        return False
    west, south, east, north = bounds
    margin = 1e-7
    return west + margin < min_x and max_x < east - margin and south + margin < min_y and max_y < north - margin


class _ExtractRead:










    def __init__(self, *, theme, name, path, directory, box, sources, where, code_where, in_python, wanted,
                 inside, dedupe):
        self.theme, self.name, self.path, self.directory = theme, name, path, directory
        self.box, self.sources = box, list(sources)
        self.where, self.code_where, self.in_python, self.wanted = where, code_where, in_python, wanted
        self.inside, self.dedupe = inside, dedupe
        self.queue = list(sources)
        self.seen: set = set()
        self.pending: dict = {}
        self.retried: set = set()
        self.skip: dict = {}
        self.missing: list = []
        self.finished: list = []
        self.counts = dict.fromkeys(_LIFTED_FAMILIES, 0)
        self.outside = 0





        self.edge: dict = {}
        self.left_out: dict = {}
        self.classes = _ClassCounter(theme)
        self.tables: dict = {}
        self.layer_ids: dict = {}
        self.made: dict = {}

    def read(self, target, deadline: float, cancelled=None, room=None) -> str:

        import time

        from osgeo import gdal, ogr, osr

        west, south, east, north = self.box
        wgs84 = osr.SpatialReference()
        wgs84.ImportFromEPSG(4326)
        wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        kinds = {"points": ogr.wkbMultiPoint, "lines": ogr.wkbMultiLineString, "polygons": ogr.wkbMultiPolygon}
        outputs = {family: target.GetLayerByName(table) for family, table in self.tables.items()}
        stopped = ""
        while self.queue:
            label, url = self.queue.pop(0)
            if cancelled is not None and cancelled():
                self.queue.insert(0, (label, url))
                return "cancelled"
            if time.monotonic() > deadline:
                self.queue.insert(0, (label, url))
                return "clock"
            if room is not None and not room():
                self.queue.insert(0, (label, url))
                return self._merge(target, cancelled) or "disk"
            gdal.ErrorReset()
            local = ""
            try:
                source = ogr.Open(f"/vsicurl/{url}")
                message = "" if source is not None else gdal.GetLastErrorMsg()
            except RuntimeError as exc:
                source, message = None, str(exc)
            if source is None:



                reason = _extract_miss_reason(message) if message else "absent"
                if reason != "absent":
                    source, local, message = self._downloaded(label, url, deadline, cancelled, message)
                if source is None:
                    self.missing.append({"tile": label, "reason": reason, "detail": str(message)[:200]})
                    continue
            layer_in = source.GetLayer(0)
            layer_in.SetSpatialFilterRect(west, south, east, north)
            clause = " AND ".join(part for part in (self.where, self.code_where) if part)
            if clause:
                try:



                    unusable = layer_in.SetAttributeFilter(clause) not in (0, None)
                except RuntimeError:
                    unusable = True
                if unusable:
                    layer_in.SetAttributeFilter(self.code_where or None)
                    self.in_python = self.in_python or self.wanted
            definition = layer_in.GetLayerDefn()
            field_names = [definition.GetFieldDefn(i).GetName() for i in range(definition.GetFieldCount())]
            field_set = set(field_names)

            field_index = {name: index for index, name in enumerate(field_names)}
            id_index = field_index.get("id")


            fields_ready: set = set()
            target.StartTransaction()
            failed: list = []




            file_ids = self.pending.setdefault(label, set())
            bounds = _tile_bounds(label)
            skip = self.skip.get(label, 0)

            read_outside = 0
            gdal.ErrorReset()
            for number, feature in enumerate(_features_until_failure(layer_in, failed), start=1):
                if number % _LIFTED_CHECK_EVERY == 0:
                    if cancelled is not None and cancelled():
                        stopped = "cancelled"
                    elif time.monotonic() > deadline:
                        stopped = "clock"
                    if stopped:


                        self.skip[label] = number - 1
                        break









                    target.CommitTransaction()
                    target.StartTransaction()
                if number <= skip:
                    continue
                geometry = feature.GetGeometryRef()
                family = _lifted_family(geometry.GetGeometryType()) if geometry is not None else ""
                if not family:
                    continue
                if self.in_python is not None and not _overture_matches(
                        {"properties": {field: feature.GetField(field) for field in field_names}}, self.in_python):
                    continue

                def get(field, row=feature, indexes=field_index):
                    index = indexes.get(field)
                    return None if index is None else row.GetField(index)

                piece = None
                if (not self.dedupe and "osm_id" in field_set
                        and (bounds is None or not _strictly_inside(geometry, bounds))):
                    piece = _osm_piece_key(get)
                if self.inside is not None:
                    kept = self.inside(geometry)
                    if kept is None:
                        self.outside += 1
                        read_outside += 1
                        if piece is not None and self.theme in _SELECTED_WHOLE_THEMES:
                            self.left_out.setdefault(piece, []).append(bytes(geometry.ExportToWkb()))
                        continue
                    geometry = kept
                if self.dedupe:
                    identity = feature.GetField(id_index) if id_index is not None else None
                    if identity:
                        if identity in self.seen or identity in file_ids:
                            continue
                        file_ids.add(identity)
                        if bounds is None or not _strictly_inside(geometry, bounds):
                            self.seen.add(identity)
                out = outputs.get(family)
                if out is None:
                    table = f"{_safe_extract_stem(self.name)}_{family}"
                    out = target.CreateLayer(table, wgs84, kinds[family], options=["SPATIAL_INDEX=YES"])
                    outputs[family] = out
                    self.tables[family] = table
                if family not in fields_ready:
                    known = {out.GetLayerDefn().GetFieldDefn(i).GetName()
                             for i in range(out.GetLayerDefn().GetFieldCount())}
                    for i, field_name in enumerate(field_names):
                        if field_name not in known:
                            out.CreateField(definition.GetFieldDefn(i))
                    fields_ready.add(family)
                written = ogr.Feature(out.GetLayerDefn())
                written.SetFrom(feature, 1)
                written.SetGeometry(ogr.ForceTo(geometry.Clone(), kinds[family]))
                out.CreateFeature(written)
                if piece is not None:
                    self.edge.setdefault((family, *piece), []).append(written.GetFID())
                self.counts[family] += 1
                self.classes.add(get)
            if not failed and not stopped and gdal.GetLastErrorType() >= gdal.CE_Failure:


                failed.append(gdal.GetLastErrorMsg() or "the read ended on an error")
            target.CommitTransaction()
            source = None
            if local:
                remove_quietly(local)
            if stopped:
                self.queue.insert(0, (label, url))
                return stopped
            if failed:







                if self.dedupe and label not in self.retried and time.monotonic() < deadline:
                    self.retried.add(label)

                    self.outside -= read_outside
                    clear = getattr(gdal, "VSICurlPartialClearCache", None)
                    if clear is not None:
                        clear(f"/vsicurl/{url}")
                    self.queue.append((label, url))
                    continue
                self.pending.pop(label, None)
                self.missing.append({"tile": label, "reason": _extract_miss_reason(failed[0])})
                continue
            self.pending.pop(label, None)
            self.skip.pop(label, None)
            self.finished.append(label)
        return self._merge(target, cancelled)

    def _merge(self, target, cancelled=None) -> str:







        from osgeo import ogr

        groups = [(key, fids) for key, fids in self.edge.items() if len(fids) > 1 or key[1:] in self.left_out]
        if not groups:
            return ""
        stopped = ""
        target.StartTransaction()
        try:
            for number, (key, fids) in enumerate(groups):
                if number % _LIFTED_CHECK_EVERY == 0 and cancelled is not None and cancelled():
                    stopped = "cancelled"
                    break
                family = key[0]
                out = target.GetLayerByName(self.tables[family])
                rows = [out.GetFeature(fid) for fid in fids]
                if any(row is None or row.GetGeometryRef() is None for row in rows):
                    continue
                left = [ogr.CreateGeometryFromWkb(wkb) for wkb in self.left_out.get(key[1:], ())]
                left = [shape for shape in left
                        if shape is not None and _lifted_family(shape.GetGeometryType()) == family]
                union = _union_pieces([row.GetGeometryRef() for row in rows] + left)
                if union is None:
                    continue
                first = rows[0]
                first.SetGeometry(union)
                out.SetFeature(first)
                for fid in fids[1:]:
                    out.DeleteFeature(fid)
                self.edge[key] = fids[:1]
                self.counts[family] -= len(fids) - 1
                names = {first.GetFieldDefnRef(i).GetName() for i in range(first.GetFieldCount())}
                self.classes.add(lambda field, row=first, names=names: row.GetField(field) if field in names else None,
                                 1 - len(fids))
                if left:
                    self.outside -= len(left)
                    self.left_out.pop(key[1:], None)
        finally:
            target.CommitTransaction()
        return stopped

    def _downloaded(self, label: str, url: str, deadline: float, cancelled, why: str):











        import time
        import urllib.request

        from osgeo import ogr

        left = deadline - time.monotonic()
        if left <= 1:
            return None, "", why
        path = os.path.join(self.directory, f"{_safe_extract_stem(self.theme + '_' + label)}.fgb")
        try:
            net.fetch_to_file(urllib.request.Request(url, headers={"User-Agent": _USER_AGENT}), path,
                              timeout=_VSICURL_TIMEOUT_S, max_bytes=limits.current("MAX_STREAM_BYTES"),
                              total_timeout=left, cancel=cancelled)
            source = ogr.Open(path)
        except Exception as exc:  # noqa: BLE001
            remove_quietly(path)
            return None, "", f"{why}; downloaded: {exc}"
        if source is None:
            remove_quietly(path)
            return None, "", why
        return source, path, why

    def unread(self) -> list:

        absent = {entry["tile"] for entry in self.missing if entry["reason"] == "absent"}
        return [label for label, _url in self.sources if label not in self.finished and label not in absent]

    def refused(self) -> list:
        return [entry["tile"] for entry in self.missing if entry["reason"] != "absent"]

    def add_layers(self) -> list:

        several = len(self.tables) > 1
        added = []
        for family in _LIFTED_FAMILIES:
            if family not in self.tables or family in self.layer_ids:
                continue
            shown = f"{self.name} {family}" if several else self.name
            layer = QgsVectorLayer(f"{self.path}|layername={self.tables[family]}", shown, "ogr")
            if not layer.isValid():
                continue
            QgsProject.instance().addMapLayer(layer)
            self.layer_ids[family] = layer.id()
            added.append({"layer_name": layer.name(), "layer_id": layer.id(), "feature_count": self.counts[family],
                          "geometry": family, "licence": _OVERTURE_LICENCES.get(self.theme, ""),
                          "attribution": _overture_attribution(self.theme)})
        return added


def _close_dataset(dataset) -> None:









    close = getattr(dataset, "Close", None)
    if close is None:
        return
    try:
        close()
    except RuntimeError as exc:
        log_warning(f"lifted extract: GeoPackage close: {exc}")


def _overture_extract(theme: str, box, wanted, outline, args: dict, forced_clip: bool = False,
                      deadline: float | None = None, later: list | None = None) -> dict:











    import time

    from osgeo import gdal, ogr

    started = time.monotonic()
    if deadline is None:
        deadline = _lifted_deadline()
    west, south, east, north = box
    name = _overture_layer_name(theme, args)
    filters = dict(wanted) if isinstance(wanted, dict) else {}
    if theme == "divisions":
        subtypes, refusal = _divisions_subtypes_asked(wanted, args)
        if refusal:
            return refusal
        filters.pop("subtype", None)
        sources = [(subtype, f"{_tiles_base('divisions')}/divisions/{subtype}.fgb") for subtype in subtypes]
    else:
        sources = [(f"{x}/{y}", _overture_tile_url(theme, x, y)) for x, y in _overture_tiles(box)]
    for _label, url in sources[:1]:
        net.check_url(url)
    _tune_gdal_for_range_reads()
    gdal.UseExceptions()
    where = _subset_string(filters, set(filters)) if filters else ""
    in_python = wanted if isinstance(wanted, list) or (filters and not where) else None
    division = (outline or {}).get("division") or {}
    code_field = {"country": "country", "region": "region"}.get(str(division.get("subtype") or ""))
    code_where = ""
    if theme == "divisions" and code_field and division.get(code_field):
        code_where = f'"{code_field}" = ' + "'" + str(division[code_field]).replace("'", "''") + "'"
    inside = (_outline_test(outline, cut=theme not in _SELECTED_WHOLE_THEMES)
              if outline is not None and not code_where else None)
    directory = create_managed_temp_dir("lifted")
    path = os.path.join(directory, f"{_safe_extract_stem(name)}.gpkg")
    reader = _ExtractRead(theme=theme, name=name, path=path, directory=directory, box=box, sources=sources,
                          where=where, code_where=code_where, in_python=in_python, wanted=wanted,
                          inside=inside, dedupe=theme not in _osm_themes())
    target = ogr.GetDriverByName("GPKG").CreateDataSource(path)
    completed = False
    try:
        stopped = reader.read(target, deadline, net.current_cancel_check())
        completed = True
    finally:
        _close_dataset(target)
        target = None
        if not completed:

            remove_tree(directory)
    if stopped == "cancelled":


        remove_tree(directory)
        return {"_error": "The load was stopped before it finished.", "code": "CANCELLED"}


    continuing = stopped == "clock" and bool(reader.queue) and later is not None
    total = sum(reader.counts.values())
    size = os.path.getsize(path) if os.path.exists(path) else 0
    wall = time.monotonic() - started
    area = round(_bbox_km2(south, west, north, east), 1)
    read = len(reader.finished)
    refused = reader.refused()
    unread = reader.unread()
    shown = ", ".join(unread[:8]) + (f" and {len(unread) - 8} more" if len(unread) > 8 else "")
    if continuing:
        later.append(reader)
    if not total and unread and not continuing:
        remove_tree(directory)
        why = " and ".join(part for part in (
            f"{len(refused)} could not be read" if refused else "",
            "the read reached its time limit" if stopped == "clock" else "") if part)
        return {"_error": (f"No {theme} came back from the {read} of {len(sources)} published files read, but "
                           f"{why}, leaving {len(unread)} unread ({shown}): this does not show that the area "
                           "has none."),
                "code": "TIMEOUT" if stopped == "clock" and not refused else "EXECUTION_FAILED",
                "theme": theme, "box_km2": area, "lifted": True, "files_read": read, "unread_files": unread,
                "missing_tiles": reader.missing, "coverage": "partial" if read else "none",
                "suggestion": ("Tell the user the files could not all be read, which is not the same as an empty "
                               "area. Try once more; if it fails again, offer a smaller place.")}
    if not total and not continuing:
        empty = {"feature_count": 0, "theme": theme, "box_km2": area, "lifted": True, "files_read": read,
                 "missing_tiles": reader.missing,
                 "message": (f"{_overture_source(theme)} has no {theme} in the area the user asked for"
                             + (" that match the filter" if wanted else "") + "."),
                 "suggestion": "Say so; drop the filter or try another theme if the user wants something here."}
        flat = wanted if isinstance(wanted, dict) else {k: v for one in wanted or [] for k, v in one.items()}
        flat = {key: value for key, value in flat.items() if not (theme == "divisions" and key == "subtype")}
        if flat and sources:



            sample = _tile_sample(sources[0][1], box)
            if sample and not any(_overture_matches(feature, flat) for feature in sample):
                empty.update(_overture_filter_miss(sample, flat))
                empty["suggestion"] = _filter_miss_suggestion(empty) or empty["suggestion"]
        remove_tree(directory)
        return empty
    added = run_on_main_thread(reader.add_layers, timeout=_VSICURL_TIMEOUT_S) if total else []
    if total and not added:
        return {"_error": f"QGIS could not read the GeoPackage written for {name}.", "code": "EXECUTION_FAILED",
                "path": path}
    made = {"theme": theme, "mode": "clip", "lifted": True, "crs": "EPSG:4326", "box_km2": area,
            "feature_count": total, "size_bytes": size, "size": _human_bytes(size), "files_read": read,
            "wall_s": round(wall, 1), "path": path, "truncated": stopped == "clock" and not continuing,
            "licence": _OVERTURE_LICENCES.get(theme, ""), "attribution": _overture_attribution(theme),
            "by_class": reader.classes.result()}
    if len(added) > 1:
        made["layers"] = added
    elif added:
        made.update(added[0])
    if reader.missing:
        made["missing_tiles"] = reader.missing
    if unread and not continuing:
        made["unread_files"] = unread
        made["coverage"] = "partial"
    if outline is not None:
        made["clipped_to"] = outline["label"]
        made["outline_source"] = outline["outline_source"]
        made["cut_at_outline"] = inside is not None and theme not in _SELECTED_WHOLE_THEMES
        if inside is not None:
            made["dropped_outside"] = reader.outside
    made["_note"] = (f"Loaded in full, as the user asked: {total:,} features, {_human_bytes(size)} on disk in a "
                     f"GeoPackage, read from the published files in {wall:.0f} s. Tell the user the size.")
    if continuing:
        made["files_left"] = len(reader.queue)
        made["_note"] = (f"{total:,} features from {read} of {len(sources)} published files are on the map; the "
                         f"other {len(reader.queue)} are being read in the background into the same GeoPackage, "
                         "and the layers are refreshed when it completes.")
    elif stopped == "clock":
        made["_note"] += (f" The read stopped at the time limit after {read} of {len(sources)} files, so part of "
                          f"the area is missing ({shown}): say which, and offer the rest in a second call.")
    elif refused:
        made["_note"] += (f" {len(refused)} of the {len(sources)} files could not be read ({shown}), so part of "
                          "the area is missing from the map, not empty.")
    elif forced_clip:
        made["_note"] += " The published tiles were read into one file rather than opened as many layers."
    reader.made = made
    return made




_CONTINUE_KEEP_FREE_BYTES = 1024 * 1024 * 1024
_CONTINUE_POLL_S = 5.0
_CONTINUE_POLL_TIMEOUT_S = 1800.0


def _tr(text: str) -> str:
    from qgis.PyQt.QtCore import QCoreApplication

    return QCoreApplication.translate("OvertureExtract", text)


class ExtractContinuation(QgsTask):











    def __init__(self, readers: list, label: str):
        from ..core import layer_order
        from ..core.qt_compat import enum_member

        super().__init__(label, enum_member(QgsTask, "Flag", "CanCancel"))
        self.readers = readers
        self.entry: dict | None = None
        self.stopped = ""
        self.error = ""
        self.run_token = layer_order.current_run()
        self.files = sum(len(reader.queue) for reader in readers)
        self.started = 0.0

    def _room(self, path: str):
        import shutil

        def enough() -> bool:
            try:
                return shutil.disk_usage(os.path.dirname(path) or ".").free > _CONTINUE_KEEP_FREE_BYTES
            except OSError:
                return True
        return enough

    def run(self) -> bool:
        import math
        import time

        from osgeo import gdal, ogr

        self.started = time.monotonic()
        done = 0
        try:
            gdal.UseExceptions()
            for reader in self.readers:
                target = ogr.Open(reader.path, 1)
                if target is None:
                    self.error = f"The GeoPackage of {reader.name} could not be reopened."
                    return False
                try:
                    before = len(reader.queue)

                    def progress_cancelled(reader=reader, before=before, done=done):

                        self.setProgress(100.0 * (done + before - len(reader.queue)) / max(self.files, 1))
                        return self.isCanceled()
                    self.stopped = reader.read(target, math.inf, progress_cancelled, self._room(reader.path))
                    done += before - len(reader.queue)
                finally:
                    _close_dataset(target)
                    target = None
                if self.stopped:
                    return self.stopped != "cancelled"
        except Exception as exc:  # noqa: BLE001
            self.error = f"The background read stopped: {exc}"
            return False
        return not self.isCanceled()

    def finished(self, ok: bool) -> None:
        import time

        entry = self.entry
        wall = time.monotonic() - self.started if self.started else 0.0
        try:
            report = self._refresh()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"lifted extract continuation: {exc}")
            report = {"layers": [], "feature_count": 0}
        for reader in self.readers:
            if not reader.layer_ids and not any(reader.counts.values()):
                remove_tree(reader.directory)
        if entry is None:
            return
        if entry.get("status") == "canceled" or self.stopped == "cancelled" or (not ok and not self.error):
            entry["status"] = "canceled"
            entry["note"] = (f"The background read was stopped; the {report['feature_count']:,} features already "
                             "read stay on the map.")
            return
        if self.error:
            entry.update({"status": "error", "error": self.error, "code": "EXECUTION_FAILED",
                          "suggestion": (f"{report['feature_count']:,} features are on the map; the rest of the "
                                         "files were not read. Tell the user which part is missing.")})
            return
        total = report["feature_count"]
        note = (f"Loaded in full, as the user asked: {total:,} features, the last {self.files} files read in the "
                f"background in {wall:.0f} s. Tell the user it is complete and the size.")
        if not total:
            themes = ", ".join(report["themes"])
            note = (f"No {themes} in the area the user asked for, read in full from the published files. "
                    "Say so; drop the filter or try another theme if the user wants something here.")
        if report.get("unread_files"):
            note = (f"{total:,} features are on the map; {len(report['unread_files'])} files could not be read "
                    f"({', '.join(report['unread_files'][:8])}), so that part is missing, not empty. Say so.")
        if self.stopped == "disk":
            note = (f"{total:,} features are on the map; the read stopped with less than "
                    f"{_CONTINUE_KEEP_FREE_BYTES / 1024 ** 3:.0f} GB left on the drive, so the rest is missing.")
        entry.update({"status": "complete", "progress": 100, "feature_count": total,
                      "outputs": report, "files_written": sorted({reader.path for reader in self.readers}),
                      "note": note})
        if report.get("unread_files") or self.stopped:
            entry["warning"] = note
        self._tell_user(total, bool(report.get("unread_files") or self.stopped))

    def _refresh(self) -> dict:

        from ..core import layer_order

        layers, unread = [], []
        for reader in self.readers:
            project = QgsProject.instance()
            for layer_id in reader.layer_ids.values():
                layer = project.mapLayer(layer_id)
                if layer is None:
                    continue
                layer.dataProvider().reloadData()
                layer.updateExtents()
                layer.triggerRepaint()
            with layer_order.adopted(self.run_token):
                reader.add_layers()
            for family, layer_id in reader.layer_ids.items():
                layer = project.mapLayer(layer_id)
                layers.append({"theme": reader.theme, "geometry": family, "layer_id": layer_id,
                               "layer_name": layer.name() if layer is not None else "",
                               "feature_count": reader.counts.get(family, 0)})
            unread += reader.unread()
            reader.made.update({"feature_count": sum(reader.counts.values()), "files_read": len(reader.finished),
                                "files_left": len(reader.queue), "by_class": reader.classes.result()})
            if "dropped_outside" in reader.made:
                reader.made["dropped_outside"] = reader.outside
        report = {"layers": layers, "feature_count": sum(sum(r.counts.values()) for r in self.readers),
                  "themes": sorted({reader.theme for reader in self.readers}),
                  "licence": ", ".join(sorted({_OVERTURE_LICENCES.get(r.theme, "") for r in self.readers} - {""})),
                  "attribution": "; ".join(sorted({_overture_attribution(r.theme) for r in self.readers})),
                  "lifted": True, "coverage": "partial" if unread or self.stopped else "complete"}
        if unread:
            report["unread_files"] = unread
        return report

    def _tell_user(self, total: int, partial: bool) -> None:
        try:
            from qgis.core import Qgis
            from qgis.utils import iface

            names = ", ".join(sorted({reader.name for reader in self.readers}))
            text = (_tr("{names}: loaded in full, {count} features.") if not partial
                    else _tr("{names}: {count} features loaded; part of the area could not be read."))
            level = Qgis.MessageLevel.Warning if partial else Qgis.MessageLevel.Success
            iface.messageBar().pushMessage("AI Agent", text.format(names=names, count=f"{total:,}"),
                                           level=level, duration=10)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"lifted extract continuation: message bar: {exc}")


def continue_in_background(readers: list, made: dict) -> dict:

    from .processing_run import register_task

    names = ", ".join(sorted({reader.name for reader in readers}))
    label = f"AI Agent: rest of {names}"

    def start():
        task = ExtractContinuation(readers, label)

        def connect(_task_id: str, entry: dict) -> None:
            task.entry = entry

        task_id, _entry = register_task(task, "fetch_overture (full extent, rest of the files)", connect=connect)
        return task_id

    task_id = run_on_main_thread(start)
    made.update({
        "task_id": task_id, "status": "running",
        "poll": {"tool": "get_task_status", "args": {"task_id": task_id}, "interval_s": _CONTINUE_POLL_S,
                 "timeout_s": _CONTINUE_POLL_TIMEOUT_S, "label": _tr("Reading {names}").format(names=names)},
    })
    made["_note"] = (str(made.get("_note") or "") + " It continues as a QGIS background task (QGIS stays "
                     "responsive); get_task_status answers when it completes, and Stop cancels it.").strip()
    return made


def _fetch_overture_preflight(args: dict) -> dict:

    return _fetch_overture(args, check_only=True)


def _fetch_overture(args: dict, deadline: float | None = None, check_only: bool = False,
                    later: list | None = None) -> dict:






    if deadline is None and volume_guard.lifted(args):
        deadline = _lifted_deadline()
    stopped = net.current_cancel_check()
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
        problem = volume_guard.not_degrees(*box)
        if problem:
            return {"_error": problem, "code": "INVALID_ARGS",
                    "suggestion": 'Pass clip_to with the place name ("Paris"), or the bbox in EPSG:4326 degrees.'}
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
    if (mode == "stream" and theme != "divisions" and volume_guard.lifted(args)
            and len(_overture_tiles(box)) > _OVERTURE_MAX_TILES):


        forced_clip = True
        mode = "clip"
    if mode == "stream":
        refused = _stream_refusal(theme, box)
        if check_only or refused:
            return refused or {}
        if not wanted:
            return _overture_stream(theme, box, args)








        readers = [] if later is None else later
        made = _overture_extract(theme, box, wanted, None, args, forced_clip=True,
                                 deadline=deadline or _lifted_deadline(), later=readers)
        if later is None and readers and not made.get("_error"):
            return continue_in_background(readers, made)
        return made

    if outline is None and volume_guard.lifted(args):






        found, _refused = _resolve_outline(str(args["full_extent"]["place"]).strip(), layers=False)
        if found is not None:
            found_west, found_south, found_east, found_north = found["bbox"]
            if not (found_east < west or found_west > east or found_north < south or found_south > north):
                outline = found




    max_km2, max_span = volume_guard.hosted_caps().get(theme, (_OVERTURE_MAX_KM2, _OVERTURE_MAX_SPAN_DEG))
    area_km2 = _bbox_km2(south, west, north, east)
    if volume_guard.lifted(args) and (max(east - west, north - south) > max_span or area_km2 > max_km2):
        if check_only:
            return {}


        readers = [] if later is None else later
        made = _overture_extract(theme, box, wanted, outline, args, forced_clip, deadline=deadline, later=readers)
        if later is None and readers and not made.get("_error"):
            return continue_in_background(readers, made)
        return made
    if outline is None:




        if max(east - west, north - south) > max_span:
            return {"_error": f"Each side of the box must stay under {max_span:.0f} degree.",
                    "routes": volume_guard.LIFT_HINT.strip(),






                    "code": "INVALID_ARGS",
                    "suggestion": ('Whole zone: clip_to the place by name (its outline in clips), full_extent '
                                   'if the user named it, or mode "stream" for a city. A smaller box is only part.'),
                    **volume_guard.coded(hint="overture_too_big", variant="span")}



        if area_km2 > max_km2 * volume_guard.NEAR_MISS:
            return {"_error": f"This box is {area_km2:.0f} km2 and the Overture service takes at most "
                    f"{max_km2:.0f} km2.",
                    "routes": volume_guard.LIFT_HINT.strip(),
                    "code": "INVALID_ARGS",
                    "box_km2": round(area_km2, 1),
                    "suggestion": ('Whole zone: clip_to the place name splits its outline into clips of that '
                                   'size; full_extent if the user named it; mode "stream" for a city. Zooming in '
                                   'loads only part.'),
                    **volume_guard.coded(hint="overture_too_big", variant="area")}






    subtypes = [""]
    if theme == "divisions":
        subtypes, refusal = _divisions_subtypes_asked(wanted, args)
        if refusal:
            return refusal
    if check_only:

        return {} if _split_box(box, max_km2, max_span) else _clip_split_refusal(theme, box, max_km2)
    features, payload, failed, missed = None, None, None, []
    for subtype in subtypes:
        got, meta = _overture_boxes(theme, box, max_km2, max_span, args, subtype=subtype, outline=outline)
        if got is None:
            failed = failed or meta
            if len(subtypes) > 1:
                missed.append({"subtype": subtype, "reason": str(meta.get("_error") or "")[:120]})
            continue
        if features is None:
            features, payload = got, meta
            continue
        seen = {key for key in map(_overture_feature_key, features) if key is not None}
        features = features + [feature for feature in got if _overture_feature_key(feature) not in seen]
        payload["truncated"] = bool(payload.get("truncated") or meta.get("truncated"))
    if features is None:
        return failed
    if missed:
        payload.setdefault("missing_boxes", []).extend(missed)

    served = features
    if isinstance(wanted, (dict, list)) and wanted:
        features = [f for f in features if _overture_matches(f, wanted)]
    if theme in _osm_themes():


        features = _merge_pieces(features, stopped)
    served_in_box = len(features)
    dropped_outside = 0
    cut = theme not in _SELECTED_WHOLE_THEMES
    if outline is not None:
        from osgeo import ogr

        inside = _outline_test(outline, cut=cut)
        if inside is None:
            return {"_error": f"The outline of {outline['label']!r} could not be read as a polygon.",
                    "code": "INVALID_ARGS", "suggestion": "Pass a bbox instead of clip_to."}
        kept = []
        for index, feature in enumerate(features):
            if index % _CLIP_CHECK_EVERY == 0:
                _stop_if_cancelled(stopped)
            shape = feature.get("geometry") if isinstance(feature, dict) else None
            geometry = ogr.CreateGeometryFromJson(json.dumps(shape)) if isinstance(shape, dict) else None
            part = inside(geometry) if geometry is not None else None
            if part is None:
                continue

            kept.append({**feature, "geometry": json.loads(part.ExportToJson())} if cut and part is not geometry
                        else feature)
        dropped_outside = len(features) - len(kept)
        features = kept
    name = _overture_layer_name(theme, args)
    if not features:
        truncated = bool(payload.get("truncated"))


        empty = {"feature_count": 0, "theme": theme, "box_km2": round(area_km2, 1),
                 "bbox": {"west": round(west, 5), "south": round(south, 5),
                          "east": round(east, 5), "north": round(north, 5)},
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
            if theme == "divisions":
                _say_named(empty, [s for s in subtypes if s], _division_names(wanted, args, outline), box)
            return empty
        if isinstance(wanted, (dict, list)) and wanted:
            flat = wanted if isinstance(wanted, dict) else {k: v for one in wanted for k, v in one.items()}


            sample = served or ([] if not payload.get("service_filter") else
                                _tile_sample(_overture_tile_url(theme, *_overture_tiles(box)[0]), box))
            if sample:
                empty.update(_overture_filter_miss(sample, flat))
        if truncated:




            empty["message"] = (f"The service stopped at its limit after {len(served)} {theme}, and none "
                                f"of those matched the filter. Whether the box holds a match further on is "
                                f"not known from this answer.")
            empty["suggestion"] = "Ask again over a smaller box, where the whole answer fits under the limit."
        elif served and theme == "divisions" and any(subtypes):



            level = " or ".join(s for s in subtypes if s)
            empty["message"] = (f"{len(served):,} {level} divisions came back in this box and none matched "
                                f"the rest of the filter; values_present holds what they carry.")
            empty["suggestion"] = (_filter_miss_suggestion(empty)
                                   or "Drop the name from the filter to see every division of that level here.")
            _say_named(empty, [s for s in subtypes if s], _division_names(wanted, args, outline), box)
        else:
            asked = [s for s in subtypes if s] if theme == "divisions" else []
            names = _division_names(wanted, args, outline) if asked else []
            if _say_named(empty, asked, names, box):
                return empty
            elsewhere = _overture_divisions_presence(box) if asked else {}
            for subtype in asked:
                elsewhere.pop(subtype, None)
            if elsewhere:

                ranked = sorted(elsewhere.items(), key=lambda item: -item[1])
                named = ", ".join(f"{name} has {count:,} here" for name, count in ranked[:2])
                empty["message"] = f"No {' or '.join(asked)} divisions in this box, but {named}."
                empty["other_subtypes_here"] = dict(ranked)
                if "locality" in asked:
                    empty["suggestion"] = _town_elsewhere(elsewhere)
                else:
                    empty["suggestion"] = (
                        f"A {ranked[0][0]} is another admin level than the {' or '.join(asked)} asked, not the "
                        f'same place under another name: load it (filter {{"subtype": "{ranked[0][0]}"}}) only '
                        "if that level is what the user wants, and name the layer and the answer after it.")
            else:
                empty["message"] = (f"{_overture_source(theme)} has no {theme} in this box"
                                    + (" that match the filter" if wanted else "") + ".")
                empty["suggestion"] = (_filter_miss_suggestion(empty)
                                       or "Widen the box, drop the filter, or try another theme.")
            if names and empty.get("named_here") == []:
                empty["message"] = f"{_named_sentence(asked, names, [])} {empty['message']}"
        return empty

    made = _overture_layer(features, name, args)
    if made.get("_error"):
        return made
    made.update({
        "theme": theme, "mode": "clip", "crs": "EPSG:4326", "box_km2": round(area_km2, 1),
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
        made["cut_at_outline"] = cut
        if outline.get("note"):
            made["clip_note"] = outline["note"]
        made["bbox"] = {"south": round(south, 5), "west": round(west, 5),
                        "north": round(north, 5), "east": round(east, 5)}
    else:




        reach = _beyond_bbox(features, (west, south, east, north))
        if reach:
            made["beyond_bbox"] = reach
    if theme == "divisions":




        nested = [feature for feature in features
                  if isinstance(feature, dict) and (feature.get("properties") or {}).get("nested_in")]



        if len(features) <= _NAMES_SHOWN:
            made["names"] = [str((feature.get("properties") or {}).get("name") or "") for feature in features
                             if isinstance(feature, dict)]
        if nested:
            sample = nested[0]["properties"]
            made["nested"] = (f"{len(nested)} of {len(features)} lie inside another feature of the same subtype "
                              f"in this answer, named by their nested_in field (e.g. {sample.get('name')!r} in "
                              f"{sample.get('nested_in')!r}); the other {len(features) - len(nested)} are not "
                              "inside one, and filter [{field: nested_in, value: none}] loads those alone.")
    if theme == "buildings":


        counts: dict = {}
        for feature in features:
            origin = str((feature.get("properties") or {}).get("source") or "unknown")
            counts[origin] = counts.get(origin, 0) + 1
        made["by_source"] = dict(sorted(counts.items(), key=lambda pair: -pair[1]))
    classes = _ClassCounter(theme)
    for feature in features:
        properties = (feature.get("properties") or {}) if isinstance(feature, dict) else {}
        classes.add(properties.get)
    made["by_class"] = classes.result()
    cover = _served_cover(features, outline, (west, south, east, north), stopped)
    if cover is not None:
        made["polygon_cover"] = _cover_entry(cover, outline["label"] if outline is not None else "the box", theme)
    if made.get("truncated"):


        made["coverage"] = "partial"
        made["_note"] = (f"The service stopped at its limit of {len(features):,} features: this layer covers part "
                         "of the box, and the rest is missing from the map, not empty.")
        made["suggestion"] = ("For the rest: a smaller box around the part that matters, or, if the user's own "
                              "words ask for the whole place, the same call with full_extent.")
        if payload.get("unread_boxes"):
            made["unread_boxes"] = payload["unread_boxes"]
    elif forced_clip:
        made["_note"] = ('mode "stream" opens whole tiles, which cannot be clipped to an outline or to one '
                         'division, so this came back clipped instead.')
    return made
