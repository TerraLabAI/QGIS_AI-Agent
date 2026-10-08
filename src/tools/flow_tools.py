# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later































from __future__ import annotations

import math
import os
import re
import threading
import time
import unicodedata

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsDistanceArea,
    QgsFeatureRequest,
    QgsFeedback,
    QgsField,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
    QgsVectorLayerFeatureSource,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP, QCoreApplication

from ..core import background, limits, net, output_paths, security
from ..core.background import run_on_main_thread
from ..core.qt_compat import enum_member
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .layer_lookup import _field_not_found_error, _find_layer, _is_qgis_null, _layer_not_found_error

SHAPES = ("straight", "curved", "geodesic")


_STOP_POLL_S = 0.05


_REPORT_CAP = 20


_CURVE_OFFSET = 0.12
_CURVE_SEGMENTS = 24




_GEODESIC_SPAN_KM = 1500.0
_EARTH_RADIUS_KM = 6371.0088


def _tr(text: str) -> str:
    return QCoreApplication.translate("AIAgent", text)


def register_flow_tools(registry: ToolRegistry):


    registry.register(Tool(
        name="create_flow_lines",
        danger="write",
        label=QT_TRANSLATE_NOOP(
        "AIAgent", "Flow lines from {origin_field} to {destination_field} of {table_layer}"),
        input_schema={
            "type": "object",
            "properties": {
                "table_layer": {"type": "string"},
                "origin_field": {"type": "string"},
                "destination_field": {"type": "string"},
                "value_field": {"type": "string"},
                "places_layer": {"type": "string"},
                "places_key_field": {"type": "string"},
                "origin_x_field": {"type": "string"},
                "origin_y_field": {"type": "string"},
                "destination_x_field": {"type": "string"},
                "destination_y_field": {"type": "string"},
                "coordinate_crs": {"type": "string"},
                "shape": {"type": "string", "enum": list(SHAPES)},
                "carry_fields": {"type": "array", "items": {"type": "string"}},
                "layer_name": {"type": "string"},
                "output_path": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },
            "required": ["table_layer", "origin_field", "destination_field"],
        },
        handler=_create_flow_lines,


        background=True,
    ))




def _normalize_key(text) -> str:






    folded = unicodedata.normalize("NFKD", str(text or "").strip())
    return "".join(ch for ch in folded if not unicodedata.combining(ch)).casefold()


def _stopped() -> dict:
    return tool_error("Stopped before the flow lines were written.", "CANCELLED", "No file was written.")




def _plan(args: dict) -> dict:
    ref = str(args.get("table_layer") or "").strip()
    table = _find_layer(ref)
    if table is None:
        return _layer_not_found_error(ref)
    if not isinstance(table, QgsVectorLayer):
        return tool_error(f"{table.name()} is not a vector layer or table.", "INVALID_ARGS",
                          "the origin-destination table, a vector layer or a geometryless CSV.")
    fields = table.fields()
    plan: dict = {"table_id": table.id(), "table_name": table.name(), "notes": []}
    for key in ("origin_field", "destination_field"):
        name = str(args.get(key) or "").strip()
        index = fields.indexOf(name)
        if index < 0:
            return _field_not_found_error(table, name)
        plan[key] = {"name": fields.at(index).name(), "index": index}
    value_index = None
    value_name = str(args.get("value_field") or "").strip()
    if value_name:
        value_index = fields.indexOf(value_name)
        if value_index < 0:
            return _field_not_found_error(table, value_name)
        plan["value_field"] = fields.at(value_index).name()
    plan["value_index"] = value_index

    carried: list[tuple[int, str]] = []
    for name in (args.get("carry_fields") or [])[:8]:
        index = fields.indexOf(str(name))
        if index < 0:
            return _field_not_found_error(table, str(name))
        carried.append((index, fields.at(index).name()))
    plan["carried"] = carried

    places_name = str(args.get("places_layer") or "").strip()
    coord_keys = ("origin_x_field", "origin_y_field", "destination_x_field", "destination_y_field")
    coord_given = all(str(args.get(k) or "").strip() for k in coord_keys)
    if not places_name and not coord_given:
        return tool_error(
            "Neither a places layer nor the four coordinate fields (origin_x_field, origin_y_field, "
            "destination_x_field, destination_y_field) were given.", "INVALID_ARGS",
            hint="flow_needs_places_or_coordinates")
    if places_name:
        places = _find_layer(places_name)
        if places is None:
            return _layer_not_found_error(places_name)
        if not isinstance(places, QgsVectorLayer) or not places.isSpatial():
            return tool_error(f"{places.name()} has no geometry to draw a flow line to.", "INVALID_ARGS",
                              "places_layer takes a point, line or polygon layer.")
        key_name = str(args.get("places_key_field") or "").strip()
        key_index = places.fields().indexOf(key_name)
        if key_index < 0:
            return _field_not_found_error(places, key_name)
        plan["mode"] = "places"
        plan["places_id"] = places.id()
        plan["places_name"] = places.name()
        plan["places_key_index"] = key_index
        plan["crs"] = places.crs()
        if coord_given:
            plan["notes"].append("places_layer was given, so the coordinate fields were not used.")
    else:
        for key in coord_keys:
            name = str(args[key]).strip()
            index = fields.indexOf(name)
            if index < 0:
                return _field_not_found_error(table, name)
            plan[key] = {"name": fields.at(index).name(), "index": index}
        crs_text = str(args.get("coordinate_crs") or "").strip()
        crs = QgsCoordinateReferenceSystem(crs_text) if crs_text else None
        if crs_text and not crs.isValid():
            return tool_error(f"coordinate_crs {crs_text!r} is not a CRS QGIS recognises.", "INVALID_ARGS",
                              "an EPSG code, for example EPSG:4326.")
        table_crs_known = table.isSpatial() and table.crs().isValid()
        if crs is None:
            crs = table.crs() if table_crs_known else QgsCoordinateReferenceSystem("EPSG:4326")
        plan["mode"] = "coordinates"
        plan["crs"] = crs




        plan["crs_is_degree_guess"] = not crs_text and not table_crs_known

    layer_name = str(args.get("layer_name") or "").strip() or _tr("Flow lines: {}").format(table.name())
    target, table_name, refused = _output_target(args, layer_name)
    if refused:
        return refused
    plan["target"] = target
    plan["gpkg_table"] = table_name
    plan["layer_name"] = layer_name
    plan["shape"] = str(args.get("shape") or "").strip().lower()
    if plan["shape"] and plan["shape"] not in SHAPES:
        return tool_error(f"shape must be one of {list(SHAPES)}, got {args['shape']!r}.", "INVALID_ARGS")
    plan["max_table_rows"] = int(limits.current("FLOW_MAX_TABLE_ROWS"))
    plan["max_places"] = int(limits.current("FLOW_MAX_PLACES"))
    plan["max_lines"] = int(limits.current("FLOW_MAX_LINES"))
    return plan


def _output_target(args: dict, layer_name: str):

    asked = str(args.get("output_path") or "").strip()
    expanded = security.expand_path(asked) if asked else ""
    table_name = re.sub(r"[^a-z0-9_]+", "_", layer_name.lower()).strip("_") or "flow_lines"
    if expanded and not os.path.isdir(expanded):
        target = expanded
        extension = os.path.splitext(target)[1].lower()
        if not extension:
            target += ".gpkg"
        elif extension != ".gpkg":
            return None, None, tool_error(f"{os.path.basename(target)} is not a GeoPackage name.", "INVALID_ARGS",
                                          "output_path ends in .gpkg, a GeoPackage.")
        error = security.validate_path(target, write=True)
        if error:
            return None, None, tool_error(error, "PERMISSION_DENIED",
                                          "Allowed: the project folder, your home folder or the temp folder.")
        if os.path.exists(target) and args.get("overwrite") is not True:
            return None, None, tool_error(f"{target} already exists.", "INVALID_ARGS",
                                          hint="flow_output_exists", path=target)
        return target, table_name, None
    stem = re.sub(r"\s+", "_", output_paths.safe_file_name(layer_name, "flow_lines"))[:120]
    folder = expanded or output_paths.default_folder()
    for index in range(1, 1000):
        candidate = os.path.join(folder, f"{stem}_{index}.gpkg" if index > 1 else f"{stem}.gpkg")
        if not os.path.exists(candidate):
            error = security.validate_path(candidate, write=True)
            if error:
                return None, None, tool_error(error, "PERMISSION_DENIED", hint="georef_no_writable_folder")
            return candidate, table_name, None
    return None, None, tool_error(f"{folder} already holds 999 flow line files named {stem}.", "INVALID_ARGS",
                                  hint="chart_name_space_full", folder=folder, stem=stem)




def _representative_point(geometry):
    from qgis.core import Qgis

    point_kind = enum_member(Qgis, "GeometryType", "Point", enum_member(Qgis, "WkbType", "Point", None))
    try:
        if geometry.type() == point_kind:
            return geometry.asPoint()
    except Exception:  # noqa: BLE001  # nosec B110
        pass
    surface = geometry.pointOnSurface()
    if surface is not None and not surface.isEmpty():
        return surface.asPoint()
    centroid = geometry.centroid()
    return centroid.asPoint()


def _open_places(plan: dict, state: dict) -> dict | None:







    layer = QgsProject.instance().mapLayer(plan["places_id"])
    if not isinstance(layer, QgsVectorLayer):
        return tool_error(f"{plan['places_name']} left the project before it was read.", "EXECUTION_FAILED",
                          hint="flow_input_left_project", variant="places", layer=plan["places_name"])
    request = QgsFeatureRequest()
    request.setSubsetOfAttributes([plan["places_key_index"]])
    request.setLimit(plan["max_places"] + 1)
    feedback = QgsFeedback()
    if hasattr(request, "setFeedback"):
        request.setFeedback(feedback)
    state.update(source=QgsVectorLayerFeatureSource(layer), request=request, feedback=feedback)
    return None


def _read_places_index(state: dict, plan: dict, cancelled) -> dict:




    halted, over, failure = threading.Event(), threading.Event(), []
    index: dict = {"exact": {}, "norm": {}, "read": 0, "cut": False}

    def reader(source, request, feedback):
        try:
            with background.on_any_failure(failure.append):
                _fetch_places(source, request, plan, index, halted)
        finally:
            over.set()

    background.start_kept_thread(reader, state["source"], state["request"], state["feedback"],
                                 name="create_flow_lines places")
    while not over.wait(_STOP_POLL_S):
        if net.is_cancelled(cancelled):
            halted.set()
            state["feedback"].cancel()
            raise InterruptedError("Stopped while the places were read")
    if failure:
        raise failure[0]
    return index


def _fetch_places(source, request, plan: dict, index: dict, halted) -> None:


    key_index = plan["places_key_index"]
    exact, norm = index["exact"], index["norm"]
    features = source.getFeatures(request)
    try:
        for count, feature in enumerate(features, 1):
            if halted.is_set():
                return
            if index["read"] >= plan["max_places"]:
                index["cut"] = True
                return
            index["read"] += 1
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                continue
            raw = feature[key_index]
            if raw is None or _is_qgis_null(raw):
                continue
            key = str(raw)
            point = _representative_point(geometry)
            exact.setdefault(key, []).append((point.x(), point.y()))
            norm.setdefault(_normalize_key(key), []).append((key, point.x(), point.y()))
            background.breathe(count)
    finally:
        features.close()




def _open_table(plan: dict, state: dict):
    layer = QgsProject.instance().mapLayer(plan["table_id"])
    if not isinstance(layer, QgsVectorLayer):
        return tool_error(f"{plan['table_name']} left the project before it was read.", "EXECUTION_FAILED",
                          hint="flow_input_left_project", variant="table", layer=plan["table_name"])
    indexes = [plan["origin_field"]["index"], plan["destination_field"]["index"]]
    if plan["value_index"] is not None:
        indexes.append(plan["value_index"])
    if plan["mode"] == "coordinates":
        indexes += [plan[k]["index"] for k in
                    ("origin_x_field", "origin_y_field", "destination_x_field", "destination_y_field")]
    indexes += [index for index, _name in plan["carried"]]
    request = QgsFeatureRequest()
    request.setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry"))
    request.setSubsetOfAttributes(sorted(set(indexes)))
    request.setLimit(plan["max_table_rows"] + 1)
    feedback = QgsFeedback()
    if hasattr(request, "setFeedback"):
        request.setFeedback(feedback)
    state.update(source=QgsVectorLayerFeatureSource(layer), request=request, feedback=feedback)
    return None


def _read_table(state: dict, plan: dict, cancelled) -> list:



    halted, over, failure = threading.Event(), threading.Event(), []
    rows: list = []

    def reader(source, request, feedback):
        try:
            with background.on_any_failure(failure.append):
                _fetch_table(source, request, state, plan, rows, halted)
        finally:
            over.set()

    background.start_kept_thread(reader, state["source"], state["request"], state["feedback"],
                                 name="create_flow_lines read")
    while not over.wait(_STOP_POLL_S):
        if net.is_cancelled(cancelled):
            halted.set()
            state["feedback"].cancel()
            raise InterruptedError("Stopped while the table was read")
    if failure:
        raise failure[0]
    return rows


def _fetch_table(source, request, state: dict, plan: dict, rows: list, halted) -> None:
    origin_i, destination_i = plan["origin_field"]["index"], plan["destination_field"]["index"]
    value_i = plan["value_index"]
    coord_idx = None
    if plan["mode"] == "coordinates":
        coord_idx = tuple(plan[k]["index"] for k in
                          ("origin_x_field", "origin_y_field", "destination_x_field", "destination_y_field"))
    carried_idx = [index for index, _name in plan["carried"]]
    features = source.getFeatures(request)
    try:
        for index, feature in enumerate(features, 1):
            if halted.is_set():
                return
            if state["read"] >= plan["max_table_rows"]:
                state["cut"] = True
                break
            state["read"] += 1
            origin_raw = feature[origin_i]
            destination_raw = feature[destination_i]
            value_raw = feature[value_i] if value_i is not None else None
            coords = tuple(feature[i] for i in coord_idx) if coord_idx else None
            carried = tuple(feature[i] for i in carried_idx)
            rows.append((origin_raw, destination_raw, value_raw, coords, carried))
            background.breathe(index)
        return
    finally:
        features.close()


def _num(value):
    if value is None or _is_qgis_null(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value) -> str:
    if value is None or _is_qgis_null(value):
        return ""
    return str(value)




def _lookup(key: str, index: dict) -> tuple:

    exact, norm = index["exact"], index["norm"]
    if key in exact:
        candidates = exact[key]
        return candidates[0], "exact", len(candidates) > 1
    normalized = _normalize_key(key)
    bucket = norm.get(normalized)
    if not bucket:
        return None, None, False
    distinct = {(round(x, 9), round(y, 9)) for _raw, x, y in bucket}
    return (bucket[0][1], bucket[0][2]), "normalized", len(distinct) > 1


def _not_degrees(*values) -> bool:

    for index, value in enumerate(values):
        if value is None:
            continue
        limit = 180.0 if index % 2 == 0 else 90.0
        if abs(value) > limit:
            return True
    return False


def _match_rows(rows: list, plan: dict, places_index: dict | None) -> tuple:
    matched: list[dict] = []
    counts = {"matched_exact_origin": 0, "matched_exact_destination": 0,
              "matched_normalized_origin": 0, "matched_normalized_destination": 0}
    unmatched: dict[tuple, int] = {}
    ambiguous: dict[str, int] = {}
    self_flows = 0
    null_values = 0
    has_value = plan["value_index"] is not None

    for origin_raw, destination_raw, value_raw, coords, carried in rows:
        origin_text, destination_text = _text(origin_raw), _text(destination_raw)
        value = _num(value_raw) if has_value else None
        if has_value and value is None:
            null_values += 1
        if plan["mode"] == "places":
            if not origin_text or not destination_text:
                key = (not origin_text and "origin" or "destination")
                unmatched[(origin_text or destination_text, key)] = \
                    unmatched.get((origin_text or destination_text, key), 0) + 1
                continue
            origin_point, origin_method, origin_ambiguous = _lookup(origin_text, places_index)
            destination_point, destination_method, destination_ambiguous = _lookup(destination_text, places_index)
            ok = True
            for text, point, field, method, is_ambiguous in (
                (origin_text, origin_point, "origin", origin_method, origin_ambiguous),
                (destination_text, destination_point, "destination", destination_method, destination_ambiguous),
            ):
                if point is None:
                    unmatched[(text, field)] = unmatched.get((text, field), 0) + 1
                    ok = False
                    continue



                counts[f"matched_{method}_{field}"] += 1
                if is_ambiguous:
                    ambiguous[text] = ambiguous.get(text, 0) + 1
            if not ok:
                continue
            same_place = (round(origin_point[0], 9), round(origin_point[1], 9)) == \
                         (round(destination_point[0], 9), round(destination_point[1], 9))
        else:
            ox, oy, dx, dy = (_num(v) for v in coords)
            if ox is None or oy is None:
                unmatched[(origin_text or "(no coordinates)", "origin")] = \
                    unmatched.get((origin_text or "(no coordinates)", "origin"), 0) + 1
                continue
            if dx is None or dy is None:
                unmatched[(destination_text or "(no coordinates)", "destination")] = \
                    unmatched.get((destination_text or "(no coordinates)", "destination"), 0) + 1
                continue
            if plan.get("crs_is_degree_guess") and _not_degrees(ox, oy, dx, dy):
                return None, tool_error(
                    f"{plan['table_name']} has coordinates outside longitude/latitude ranges "
                    "(|x| over 180 or |y| over 90), but no coordinate_crs was given and the table "
                    "carries no CRS of its own, so EPSG:4326 was only a guess.", "INVALID_ARGS",
                    hint="flow_coordinates_not_degrees", table=plan["table_name"])
            origin_point, destination_point = (ox, oy), (dx, dy)
            same_place = (round(ox, 9), round(oy, 9)) == (round(dx, 9), round(dy, 9))
        if _normalize_key(origin_text) == _normalize_key(destination_text) or same_place:
            self_flows += 1
            continue
        matched.append({
            "origin": origin_text, "destination": destination_text, "value": value,
            "origin_point": origin_point, "destination_point": destination_point,
            "carried": carried,
        })

    unmatched_sorted = sorted(unmatched.items(), key=lambda item: (-item[1], item[0]))
    ambiguous_sorted = sorted(ambiguous.items(), key=lambda item: (-item[1], item[0]))
    report = {
        **counts,
        "self_flows_skipped": self_flows,
        "unmatched": [{"key": key, "field": field, "rows": n} for (key, field), n in unmatched_sorted[:_REPORT_CAP]],
        "unmatched_more": max(0, len(unmatched_sorted) - _REPORT_CAP),
        "ambiguous_places": [{"key": key, "rows": n} for key, n in ambiguous_sorted[:_REPORT_CAP]],
        "ambiguous_places_more": max(0, len(ambiguous_sorted) - _REPORT_CAP),
    }
    if has_value:
        report["null_value_rows"] = null_values
    return matched, report




def _haversine_km(a: tuple, b: tuple) -> float:
    lon1, lat1, lon2, lat2 = math.radians(a[0]), math.radians(a[1]), math.radians(b[0]), math.radians(b[1])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def _decide_shape(plan: dict, matched: list) -> tuple:

    if plan["shape"]:
        return plan["shape"], False
    crs = plan["crs"]
    if crs.isGeographic() and matched:
        longest = max(_haversine_km(m["origin_point"], m["destination_point"]) for m in matched)
        if longest > _GEODESIC_SPAN_KM:
            return "geodesic", True
    return "straight", True




def _curve_parts(origin: tuple, destination: tuple, origin_key: str, destination_key: str) -> list:
    ox, oy = origin
    dx, dy = destination
    length = math.hypot(dx - ox, dy - oy)
    if length == 0:
        return [[(ox, oy), (dx, dy)]]
    nx, ny = -(dy - oy) / length, (dx - ox) / length
    sign = 1 if origin_key < destination_key else -1
    offset = _CURVE_OFFSET * length * sign
    mx, my = (ox + dx) / 2 + nx * offset, (oy + dy) / 2 + ny * offset
    points = []
    for i in range(_CURVE_SEGMENTS + 1):
        t = i / _CURVE_SEGMENTS
        x = (1 - t) ** 2 * ox + 2 * (1 - t) * t * mx + t ** 2 * dx
        y = (1 - t) ** 2 * oy + 2 * (1 - t) * t * my + t ** 2 * dy
        points.append((x, y))
    return [points]


def _geodesic_parts(qda: QgsDistanceArea, origin: tuple, destination: tuple) -> list:
    distance = qda.measureLine(QgsPointXY(*origin), QgsPointXY(*destination))
    interval = max(50_000.0, distance / 40.0) if distance and math.isfinite(distance) else 100_000.0
    parts = qda.geodesicLine(QgsPointXY(*origin), QgsPointXY(*destination), interval, True)
    return [[(p.x(), p.y()) for p in part] for part in parts] or [[origin, destination]]


def _uri_field_spec(name: str, field: QgsField | None) -> str:
    if field is None:
        return f"{name}:string(255)"
    kind = field.typeName().lower()
    if kind in ("integer", "int4", "smallint"):
        return f"{name}:integer"
    if kind in ("integer64", "int8", "bigint"):
        return f"{name}:integer64"
    if kind in ("real", "double", "double precision", "numeric", "float"):
        return f"{name}:double"
    if kind == "date":
        return f"{name}:date"
    if kind in ("datetime", "timestamp"):
        return f"{name}:datetime"
    if kind in ("bool", "boolean"):
        return f"{name}:bool"
    width = field.length() or 255
    return f"{name}:string({width})"


def _build_output(plan: dict, matched: list, shape: str) -> dict:


    from qgis.core import QgsFeature, QgsGeometry

    from .persist_tools import _write_vector
    from .processing_run import _process_outputs
    from .symbology_tools import _set_layer_symbology

    cut = len(matched) > plan["max_lines"]



    ranked = sorted(matched, key=lambda m: (m["value"] is None, -(m["value"] if m["value"] is not None else 0.0)))
    top = ranked[:plan["max_lines"]]




    kept = list(reversed(top))

    table_layer = QgsProject.instance().mapLayer(plan["table_id"])
    carried_fields = [(table_layer.fields().at(index) if table_layer is not None else None, name)
                      for index, name in plan["carried"]]
    field_specs = ["origin:string(255)", "destination:string(255)", "value:double"]
    field_specs += [_uri_field_spec(re.sub(r"[^A-Za-z0-9_]", "_", name)[:60], field) for field, name in carried_fields]
    uri = "MultiLineString?" + "&".join(f"field={spec}" for spec in field_specs)
    memory = QgsVectorLayer(uri, "flow_lines", "memory")
    if not memory.isValid():
        return {"_error": "Could not build the flow lines layer.", "code": "EXECUTION_FAILED"}


    memory.setCrs(plan["crs"])

    qda = None
    if shape == "geodesic":
        qda = QgsDistanceArea()
        qda.setSourceCrs(plan["crs"], QgsProject.instance().transformContext())
        qda.setEllipsoid(plan["crs"].ellipsoidAcronym() or "WGS84")

    features = []
    antimeridian_split = 0
    memory_fields = memory.fields()
    for flow in kept:
        if shape == "curved":
            parts = _curve_parts(flow["origin_point"], flow["destination_point"], flow["origin"], flow["destination"])
        elif shape == "geodesic":
            parts = _geodesic_parts(qda, flow["origin_point"], flow["destination_point"])
            if len(parts) > 1:
                antimeridian_split += 1
        else:
            parts = [[flow["origin_point"], flow["destination_point"]]]
        polylines = [[QgsPointXY(x, y) for x, y in part] for part in parts]
        geometry = QgsGeometry.fromMultiPolylineXY(polylines)
        if geometry.isEmpty():
            continue
        feature = QgsFeature(memory_fields)
        feature.setGeometry(geometry)
        attributes = [flow["origin"], flow["destination"], flow["value"]] + list(flow["carried"])
        feature.setAttributes(attributes)
        features.append(feature)

    if not features:
        return {"_error": "No row matched both ends and drew a line: nothing was written.", "code": "INVALID_ARGS"}
    ok, _ = memory.dataProvider().addFeatures(features)
    if not ok:
        return {"_error": "The memory provider refused the flow line features."}
    memory.updateExtents()

    folder = os.path.dirname(plan["target"])
    os.makedirs(folder, exist_ok=True)
    written = _write_vector(memory, plan["target"], plan["gpkg_table"])
    if written:
        return {"_error": f"The flow lines could not be written: {written[:200]}", "code": "EXECUTION_FAILED"}

    outputs = _process_outputs({"OUTPUT": f"{plan['target']}|layername={plan['gpkg_table']}"},
                               output_name=plan["layer_name"])
    added = outputs.get("OUTPUT") or {}
    layer_name = added.get("layer_name") or plan["layer_name"]

    values = [flow["value"] for flow in kept if flow["value"] is not None]
    if values:
        vmin, vmax = min(values), max(values)
        if vmax > vmin:
            width_expr = f'scale_linear("value", {vmin!r}, {vmax!r}, 0.3, 3.0)'
        else:
            width_expr = "1.2"
    else:
        width_expr = "0.8"
    style_result = _set_layer_symbology({
        "layer_name": layer_name,
        "symbol_layers": [{
            "type": "ArrowLine",
            "properties": {"head_type": 0, "arrow_type": 0, "is_curved": shape == "curved"},
            "data_defined": {"arrow_width": width_expr, "arrow_start_width": width_expr},
        }],
    })

    return {
        "output_path": plan["target"],
        "layer_name": layer_name,
        "lines_written": len(features),
        "cut": cut,
        "antimeridian_split": antimeridian_split,
        "styled": not style_result.get("_error"),
    }




def _create_flow_lines(args: dict) -> dict:
    started = time.monotonic()
    cancelled = net.current_cancel_check()
    plan = run_on_main_thread(_plan, args, timeout=60)
    if "_error" in plan:
        return plan

    places_index = None
    if plan["mode"] == "places":
        places_state: dict = {}
        try:
            opened = run_on_main_thread(_open_places, plan, places_state, timeout=60)
            if opened:
                return opened
            places_index = _read_places_index(places_state, plan, cancelled)
        except InterruptedError:
            return _stopped()
        finally:
            places_state.pop("request", None)
            places_state.pop("source", None)

    state: dict = {"read": 0, "cut": False}
    try:
        opened = run_on_main_thread(_open_table, plan, state, timeout=60)
        if opened:
            return opened
        rows = _read_table(state, plan, cancelled)
    except InterruptedError:
        return _stopped()
    finally:
        state.pop("request", None)
        state.pop("source", None)

    matched, report = _match_rows(rows, plan, places_index)
    if matched is None:
        return report
    if net.is_cancelled(cancelled):
        return _stopped()
    if not matched:
        result = {"rows_read": state["read"], "lines_written": 0, **report}
        result["_error"] = "No row matched a place at both ends and was not a self-flow: no line was drawn."
        result["code"] = "INVALID_ARGS"
        result["hint"] = "flow_no_match"
        return result

    shape, shape_auto = _decide_shape(plan, matched)
    built = run_on_main_thread(_build_output, plan, matched, shape, timeout=180)
    if "_error" in built:
        return built
    if net.is_cancelled(cancelled):
        return _stopped()

    result = {"rows_read": state["read"], "matched_rows": len(matched), **report, **built,
              "shape": shape, "shape_chosen": "auto" if shape_auto else "requested"}
    if plan.get("value_field"):
        result["value_field"] = plan["value_field"]
    if plan.get("notes"):
        result["note"] = " ".join(plan["notes"])
    if state["cut"]:
        result.setdefault("cut_reasons", []).append(
            f"Only the first {plan['max_table_rows']:,} table rows were read (FLOW_MAX_TABLE_ROWS).")
    if places_index and places_index.get("cut"):
        result.setdefault("cut_reasons", []).append(
            f"Only the first {plan['max_places']:,} places were read (FLOW_MAX_PLACES).")
    if built.get("cut"):
        result.setdefault("cut_reasons", []).append(
            f"Only the first {plan['max_lines']:,} matched flows were drawn (FLOW_MAX_LINES), the heaviest "
            "kept when a value was given.")
    result.pop("cut", None)
    result["seconds"] = round(time.monotonic() - started, 1)
    return result
