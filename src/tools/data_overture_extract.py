# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""A load the user asked for in full: the lifted extract, its deadline and the fetch_overture entry point."""

from __future__ import annotations

import json
import os
import shutil

from qgis.core import QgsProject, QgsVectorLayer

from ..core import limits, net
from ..core.policy import create_managed_temp_dir
from . import volume_guard
from .data_common import _bbox_km2, _footprint_box, _run_on_main_thread
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
    _divisions_subtypes_asked,
    _feature_inside,
    _filter_miss_suggestion,
    _osm_themes,
    _overture_attribution,
    _overture_boxes,
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
    _stream_filter_miss,
    _stream_filter_unsupported,
    _stream_refusal,
    _subset_string,
    _tile_sample,
    _tiles_base,
)
from .data_places import _resolve_outline


def _overture_clip(theme: str, box, args: dict, deadline: float | None = None) -> dict:
    """One theme clipped to *box* by our open data API, as fetch_overture would do it."""






    west, south, east, north = box
    call = {"theme": theme, "mode": "clip",
            "bbox": {"south": south, "west": west, "north": north, "east": east}}
    for key in ("layer_name", "confirm_area_km2", "confirm_large", "filter", "full_extent"):
        if args.get(key) is not None:
            call[key] = args[key]
    return _fetch_overture(call, deadline=deadline)

_LIFTED_CLOCK_SHARE = 0.8


_LIFTED_OUTLINE_VERTICES = 4000


def _lifted_deadline() -> float:
    """When a lifted read has to stop, as ``time.monotonic``: one per tool call."""
    import time

    return time.monotonic() + limits.current("CALL_MAX_SECONDS_BACKGROUND") * _LIFTED_CLOCK_SHARE


def _extract_miss_reason(text: str) -> str:
    """Why a published file did not open: "absent" (nothing is published there) or "unknown"."""
    text = str(text or "").lower()


    absent = ("404" in text or "does not exist in the file system" in text or "no such file" in text)
    return "absent" if absent else "unknown"


def _features_until_failure(layer, failures: list):
    """The features of an ogr layer read over the network; a read that fails part way ends them, its message added to *failures*, rather than."""

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


def _lifted_family(geometry_type: int) -> str:
    from osgeo import ogr

    flat = ogr.GT_Flatten(geometry_type)
    if flat in (ogr.wkbPoint, ogr.wkbMultiPoint):
        return "points"
    if flat in (ogr.wkbLineString, ogr.wkbMultiLineString):
        return "lines"
    if flat in (ogr.wkbPolygon, ogr.wkbMultiPolygon):
        return "polygons"
    return ""


def _lifted_outline_test(outline: dict):
    """A test that keeps a feature inside the outline, or None when the outline cannot be built."""





    from osgeo import ogr

    polys = outline.get("polys") or []
    coordinates = [[[list(point) for point in exterior]] + [[list(point) for point in hole] for hole in holes]
                   for exterior, holes in polys]
    shape = ogr.CreateGeometryFromJson(json.dumps({"type": "MultiPolygon", "coordinates": coordinates}))
    if shape is None:
        return None
    if sum(len(ring) for polygon in coordinates for ring in polygon) > _LIFTED_OUTLINE_VERTICES:
        shape = shape.SimplifyPreserveTopology(0.002)
    if not shape.IsValid():
        shape = shape.Buffer(0)
    min_x, max_x, min_y, max_y = shape.GetEnvelope()

    def keep(geometry) -> bool:
        low_x, high_x, low_y, high_y = geometry.GetEnvelope()
        if high_x < min_x or low_x > max_x or high_y < min_y or low_y > max_y:
            return False
        family = _lifted_family(geometry.GetGeometryType())
        if family == "polygons":
            probe = geometry.PointOnSurface()
            return probe is not None and shape.Contains(probe)
        if family == "lines":
            return geometry.Intersects(shape) and not geometry.Touches(shape)
        return shape.Intersects(geometry)

    return keep


def _tile_bounds(label: str):
    """(west, south, east, north) of the zoom-8 tile a label "x/y" names, or None for another source."""
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
    """True when a geometry's envelope stays inside a tile, off its edges: no other tile holds it."""
    try:
        min_x, max_x, min_y, max_y = geometry.GetEnvelope()
    except Exception:  # noqa: BLE001 - a geometry without an envelope is remembered for the whole load
        return False
    west, south, east, north = bounds
    margin = 1e-7
    return west + margin < min_x and max_x < east - margin and south + margin < min_y and max_y < north - margin


def _overture_extract(theme: str, box, wanted, outline, args: dict, forced_clip: bool = False,
                      deadline: float | None = None) -> dict:
    """The load the user asked for in full, read from the published files into a GeoPackage."""








    import time

    from osgeo import gdal, ogr, osr

    started = time.monotonic()
    if deadline is None:
        deadline = _lifted_deadline()
    west, south, east, north = box
    name = _overture_layer_name(theme, args)
    place = str((args.get("full_extent") or {}).get("place") or "").strip()
    if outline is None and place:




        found, _refused = _resolve_outline(place, layers=False)
        if found is not None:
            found_west, found_south, found_east, found_north = found["bbox"]
            if not (found_east < west or found_west > east or found_north < south or found_south > north):
                outline = found
    filters = dict(wanted) if isinstance(wanted, dict) else {}
    if theme == "divisions":
        subtypes, refusal = _divisions_subtypes_asked(wanted)
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
    inside = _lifted_outline_test(outline) if outline is not None and not code_where else None
    dedupe = theme not in _osm_themes()
    directory = create_managed_temp_dir("lifted")
    path = os.path.join(directory, f"{_safe_extract_stem(name)}.gpkg")
    target = ogr.GetDriverByName("GPKG").CreateDataSource(path)
    wgs84 = osr.SpatialReference()
    wgs84.ImportFromEPSG(4326)
    wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    kinds = {"points": ogr.wkbMultiPoint, "lines": ogr.wkbMultiLineString, "polygons": ogr.wkbMultiPolygon}
    outputs: dict = {}
    counts = dict.fromkeys(_LIFTED_FAMILIES, 0)
    seen: set = set()
    missing: list = []
    finished: list = []
    stopped = ""
    cancelled = net.current_cancel_check()
    queue = list(sources)
    retried: set = set()
    pending: dict = {}
    completed = False
    try:
        while queue:
            label, url = queue.pop(0)
            if cancelled is not None and cancelled():
                stopped = "cancelled"
                break
            if time.monotonic() > deadline:
                stopped = "clock"
                break
            gdal.ErrorReset()
            try:
                source = ogr.Open(f"/vsicurl/{url}")
            except RuntimeError as exc:
                missing.append({"tile": label, "reason": _extract_miss_reason(str(exc))})
                continue
            if source is None:



                message = gdal.GetLastErrorMsg()
                missing.append({"tile": label, "reason": _extract_miss_reason(message) if message else "absent"})
                continue
            layer_in = source.GetLayer(0)
            layer_in.SetSpatialFilterRect(west, south, east, north)
            clause = " AND ".join(part for part in (where, code_where) if part)
            if clause:
                try:



                    unusable = layer_in.SetAttributeFilter(clause) not in (0, None)
                except RuntimeError:
                    unusable = True
                if unusable:
                    layer_in.SetAttributeFilter(code_where or None)
                    in_python = in_python or wanted
            definition = layer_in.GetLayerDefn()
            field_names = [definition.GetFieldDefn(i).GetName() for i in range(definition.GetFieldCount())]
            target.StartTransaction()
            failed: list = []




            file_ids = pending.setdefault(label, set())
            bounds = _tile_bounds(label)
            gdal.ErrorReset()
            for number, feature in enumerate(_features_until_failure(layer_in, failed), start=1):
                if number % _LIFTED_CHECK_EVERY == 0:
                    if cancelled is not None and cancelled():
                        stopped = "cancelled"
                        break
                    if time.monotonic() > deadline:
                        stopped = "clock"
                        break
                geometry = feature.GetGeometryRef()
                family = _lifted_family(geometry.GetGeometryType()) if geometry is not None else ""
                if not family:
                    continue
                if in_python is not None and not _overture_matches(
                        {"properties": {field: feature.GetField(field) for field in field_names}}, in_python):
                    continue
                if inside is not None and not inside(geometry):
                    continue
                if dedupe:
                    identity = feature.GetField("id") if "id" in field_names else None
                    if identity:
                        if identity in seen or identity in file_ids:
                            continue
                        file_ids.add(identity)
                        if bounds is None or not _strictly_inside(geometry, bounds):
                            seen.add(identity)
                out = outputs.get(family)
                if out is None:
                    table = f"{_safe_extract_stem(name)}_{family}"
                    out = target.CreateLayer(table, wgs84, kinds[family], options=["SPATIAL_INDEX=YES"])
                    outputs[family] = out
                known = {out.GetLayerDefn().GetFieldDefn(i).GetName()
                         for i in range(out.GetLayerDefn().GetFieldCount())}
                for i in range(definition.GetFieldCount()):
                    if definition.GetFieldDefn(i).GetName() not in known:
                        out.CreateField(definition.GetFieldDefn(i))
                written = ogr.Feature(out.GetLayerDefn())
                written.SetFrom(feature, 1)
                written.SetGeometry(ogr.ForceTo(geometry.Clone(), kinds[family]))
                out.CreateFeature(written)
                counts[family] += 1
            if not failed and not stopped and gdal.GetLastErrorType() >= gdal.CE_Failure:


                failed.append(gdal.GetLastErrorMsg() or "the read ended on an error")
            target.CommitTransaction()
            source = None
            if stopped:
                break
            if failed:







                if dedupe and label not in retried and time.monotonic() < deadline:
                    retried.add(label)
                    clear = getattr(gdal, "VSICurlPartialClearCache", None)
                    if clear is not None:
                        clear(f"/vsicurl/{url}")
                    queue.append((label, url))
                    continue
                pending.pop(label, None)
                missing.append({"tile": label, "reason": _extract_miss_reason(failed[0])})
                continue
            pending.pop(label, None)
            finished.append(label)
        completed = True
    finally:
        tables = {family: out.GetName() for family, out in outputs.items()}
        outputs = {}
        target = None
        if not completed:

            shutil.rmtree(directory, ignore_errors=True)
    if stopped == "cancelled":


        shutil.rmtree(directory, ignore_errors=True)
        return {"_error": "The load was stopped before it finished.", "code": "CANCELLED"}
    total = sum(counts.values())
    size = os.path.getsize(path) if os.path.exists(path) else 0
    wall = time.monotonic() - started
    area = round(_bbox_km2(south, west, north, east), 1)
    read = len(finished)


    absent = {entry["tile"] for entry in missing if entry["reason"] == "absent"}
    refused = [entry["tile"] for entry in missing if entry["reason"] != "absent"]
    unread = [label for label, _url in sources if label not in finished and label not in absent]
    shown = ", ".join(unread[:8]) + (f" and {len(unread) - 8} more" if len(unread) > 8 else "")
    if not total and unread:
        shutil.rmtree(directory, ignore_errors=True)
        why = " and ".join(part for part in (
            f"{len(refused)} could not be read" if refused else "",
            "the read reached its time limit" if stopped == "clock" else "") if part)
        return {"_error": (f"No {theme} came back from the {read} of {len(sources)} published files read, but "
                           f"{why}, leaving {len(unread)} unread ({shown}): this does not show that the area "
                           "has none."),
                "code": "TIMEOUT" if stopped == "clock" and not refused else "EXECUTION_FAILED",
                "theme": theme, "area_km2": area, "lifted": True, "files_read": read, "unread_files": unread,
                "missing_tiles": missing, "coverage": "partial" if read else "none",
                "suggestion": ("Tell the user the files could not all be read, which is not the same as an empty "
                               "area. Try once more; if it fails again, offer a smaller place.")}
    if not total:
        empty = {"feature_count": 0, "theme": theme, "area_km2": area, "lifted": True, "files_read": read,
                 "missing_tiles": missing,
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
        shutil.rmtree(directory, ignore_errors=True)
        return empty
    several = len(tables) > 1

    def _create():
        added = []
        for family in _LIFTED_FAMILIES:
            if family not in tables:
                continue
            shown = f"{name} {family}" if several else name
            layer = QgsVectorLayer(f"{path}|layername={tables[family]}", shown, "ogr")
            if not layer.isValid():
                continue
            QgsProject.instance().addMapLayer(layer)
            added.append({"layer_name": layer.name(), "layer_id": layer.id(), "feature_count": counts[family],
                          "geometry": family, "licence": _OVERTURE_LICENCES.get(theme, ""),
                          "attribution": _overture_attribution(theme)})
        return added

    added = _run_on_main_thread(_create, timeout=_VSICURL_TIMEOUT_S)
    if not added:
        return {"_error": f"QGIS could not read the GeoPackage written for {name}.", "code": "EXECUTION_FAILED",
                "path": path}
    made = {"theme": theme, "mode": "clip", "lifted": True, "crs": "EPSG:4326", "area_km2": area,
            "feature_count": total, "size_bytes": size, "size": _human_bytes(size), "files_read": read,
            "wall_s": round(wall, 1), "path": path, "truncated": stopped == "clock",
            "licence": _OVERTURE_LICENCES.get(theme, ""), "attribution": _overture_attribution(theme)}
    if several:
        made["layers"] = added
    else:
        made.update(added[0])
    if missing:
        made["missing_tiles"] = missing
    if unread:
        made["unread_files"] = unread
        made["coverage"] = "partial"
    if outline is not None:
        made["clipped_to"] = outline["label"]
        made["outline_source"] = outline["outline_source"]
    made["_note"] = (f"Loaded in full, as the user asked: {total:,} features, {_human_bytes(size)} on disk in a "
                     f"GeoPackage, read from the published files in {wall:.0f} s. Tell the user the size.")
    if stopped == "clock":
        made["_note"] += (f" The read stopped at the time limit after {read} of {len(sources)} files, so part of "
                          f"the area is missing ({shown}): say which, and offer the rest in a second call.")
    elif refused:
        made["_note"] += (f" {len(refused)} of the {len(sources)} files could not be read ({shown}), so part of "
                          "the area is missing from the map, not empty.")
    elif forced_clip:
        made["_note"] += " The published tiles were read into one file rather than opened as many layers."
    return made


def _fetch_overture_preflight(args: dict) -> dict:
    """What fetch_overture refuses before it loads anything: the executor asks it before a size card."""
    return _fetch_overture(args, check_only=True)


def _fetch_overture(args: dict, deadline: float | None = None, check_only: bool = False) -> dict:






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



        unsupported = _stream_filter_unsupported(theme, wanted)
        subset = _subset_string(wanted, unsupported) if unsupported else ""
        if unsupported and not subset:
            return {"_error": (f'mode "stream" opens the published tiles as they are and cannot filter on '
                               f"{', '.join(sorted(unsupported))}."),
                    "code": "INVALID_ARGS",
                    "suggestion": ('Use mode "clip", which asks the service for the box and applies the '
                                   'filter, or drop the filter and set a filter on the layer afterwards.')}
        if check_only:
            return _stream_refusal(theme, box) or {}
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
            missed = _stream_filter_miss(theme, box, streamed, wanted, subset)
            if missed:
                return missed
        return streamed




    max_km2, max_span = volume_guard.hosted_caps().get(theme, (_OVERTURE_MAX_KM2, _OVERTURE_MAX_SPAN_DEG))
    area_km2 = _bbox_km2(south, west, north, east)
    if volume_guard.lifted(args) and (max(east - west, north - south) > max_span or area_km2 > max_km2):
        if check_only:
            return {}
        return _overture_extract(theme, box, wanted, outline, args, forced_clip, deadline=deadline)
    if outline is None:




        if max(east - west, north - south) > max_span:
            return {"_error": f"Each side of the box must stay under {max_span:.0f} degree." + volume_guard.LIFT_HINT,
                    "suggestion": 'Ask for a district or a town at a time, or use mode "stream" for a '
                                  'whole city, or clip_to the place by name.'}
        if area_km2 > max_km2:
            return {"_error": f"This box is {area_km2:.0f} km2 and the Overture service takes at most "
                    f"{max_km2:.0f} km2." + volume_guard.LIFT_HINT,
                    "area_km2": round(area_km2, 1),
                    "suggestion": 'Zoom in, use mode "stream", or pass clip_to with the place name, '
                                  'which splits the outline into clips of that size.'}






    subtypes = [""]
    if theme == "divisions":
        subtypes, refusal = _divisions_subtypes_asked(wanted)
        if refusal:
            return refusal
    if check_only:

        return {} if _split_box(box, max_km2, max_span) else _clip_split_refusal(theme, box, max_km2)
    features, payload, failed, missed = None, None, None, []
    for subtype in subtypes:
        got, meta = _overture_boxes(theme, box, max_km2, max_span, args, subtype=subtype)
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
    served_in_box = len(features)
    dropped_outside = 0
    if outline is not None:
        kept = []
        for index, feature in enumerate(features):
            if index % _CLIP_CHECK_EVERY == 0:
                _stop_if_cancelled(stopped)
            if _feature_inside(feature, outline):
                kept.append(feature)
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
        if isinstance(wanted, (dict, list)) and wanted and served:
            flat = wanted if isinstance(wanted, dict) else {k: v for one in wanted for k, v in one.items()}
            empty.update(_overture_filter_miss(served, flat))
        if truncated:




            empty["message"] = (f"The service stopped at its limit after {len(served)} {theme}, and none "
                                f"of those matched the filter. Whether the box holds a match further on is "
                                f"not known from this answer.")
            empty["suggestion"] = "Ask again over a smaller box, where the whole answer fits under the limit."
        else:
            asked = [s for s in subtypes if s] if theme == "divisions" else []
            elsewhere = _overture_divisions_presence(box) if asked else {}
            for subtype in asked:
                elsewhere.pop(subtype, None)
            if elsewhere:

                ranked = sorted(elsewhere.items(), key=lambda item: -item[1])
                named = ", ".join(f"{name} has {count:,} here" for name, count in ranked[:2])
                empty["message"] = f"No {' or '.join(asked)} divisions in this box, but {named}."
                empty["other_subtypes_here"] = dict(ranked)
                empty["suggestion"] = (f'Call again with filter {{"subtype": "{ranked[0][0]}"}} if that is the '
                                       "admin level meant, or drop the filter for every subtype at once.")
            else:
                empty["message"] = (f"{_overture_source(theme)} has no {theme} in this box"
                                    + (" that match the filter" if wanted else "") + ".")
                empty["suggestion"] = (_filter_miss_suggestion(empty)
                                       or "Widen the box, drop the filter, or try another theme.")
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
