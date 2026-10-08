# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


















from __future__ import annotations

import math
import os
import threading
import time
import uuid

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsFeedback,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRendererCategory,
    QgsSymbol,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsVectorLayerFeatureSource,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP, QCoreApplication
from qgis.PyQt.QtGui import QColor

from ..core import background, limits, net, security, tuning
from ..core.background import run_on_main_thread
from ..core.host_platform import remove_quietly
from ..core.invariants import metres_per_map_unit
from ..core.layer_order import feature_count_of
from ..core.policy import create_managed_temp_dir
from ..core.qt_compat import enum_member, field_type
from ..core.tool_registry import Tool, ToolRegistry, coded_fact, tool_error
from . import spatial_stats_math as sm
from .layer_lookup import _field_not_found_error, _find_layer, _is_qgis_null, _layer_not_found_error

METHODS = ("getis_ord_gi_star", "local_moran", "standard_deviational_ellipse")
NEIGHBOURS = ("queen", "rook", "distance_band", "k_nearest")

DEFAULT_PERMUTATIONS = 999


MAX_PERMUTATIONS = 9_999

SEED = 12345
DEFAULT_K = 8


MAX_LINKS = 6_000_000
SIGNIFICANCE = 0.05

_STOP_POLL_S = 0.05

_NAMED = 20
_GROUPS_SHOWN = 30

_GI_CLASSES = (
    (3, "Hot spot, 99% confidence", "#b2182b"),
    (2, "Hot spot, 95% confidence", "#ef8a62"),
    (1, "Hot spot, 90% confidence", "#fddbc7"),
    (0, "Not significant", "#d9d9d9"),
    (-1, "Cold spot, 90% confidence", "#d1e5f0"),
    (-2, "Cold spot, 95% confidence", "#67a9cf"),
    (-3, "Cold spot, 99% confidence", "#2166ac"),
)
_LISA_CLASSES = (
    ("HH", "High-High cluster", "#d7191c"),
    ("LL", "Low-Low cluster", "#2c7bb6"),
    ("HL", "High-Low outlier", "#fdae61"),
    ("LH", "Low-High outlier", "#abd9e9"),
    ("not significant", "Not significant", "#d9d9d9"),
)
_NO_NEIGHBOUR_LABEL = "No neighbour or no value (not tested)"


def _tr(text: str) -> str:
    return QCoreApplication.translate("AIAgent", text)


def register_spatial_stats_tools(registry: ToolRegistry):

    registry.register(Tool(
        name="spatial_statistics",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Spatial statistics of {layer}[: {field}]"),
        input_schema={
            "type": "object",
            "properties": {
                "layer": {"type": "string"},
                "method": {"type": "string", "enum": list(METHODS)},
                "field": {"type": "string"},
                "neighbours": {"type": "string", "enum": list(NEIGHBOURS)},
                "distance": {"type": "number", "exclusiveMinimum": 0,
                             "description": "Distance band in metres; omitted, gives every feature a neighbour."},
                "k": {"type": "integer", "minimum": 1},
                "permutations": {"type": "integer", "minimum": 99},
                "fdr": {"type": "boolean"},
                "std_devs": {"type": "integer", "enum": [1, 2, 3]},
                "confidence": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1},
                "weight_field": {"type": "string"},
                "group_field": {"type": "string"},
                "selected_only": {"type": "boolean"},
                "output_name": {"type": "string"},
            },
            "required": ["layer", "method"],
        },
        handler=_spatial_statistics,


        background=True,
    ))




def _check_args(args: dict) -> dict | None:
    method = str(args.get("method") or "")
    if method not in METHODS:
        return tool_error(f"method {method!r} is not one this tool runs.", "INVALID_ARGS",
                          hint="spatial_stats_method", method=method)
    neighbours = str(args.get("neighbours") or "")
    if neighbours and neighbours not in NEIGHBOURS:
        return tool_error(f"neighbours {neighbours!r} is not one this tool knows.", "INVALID_ARGS",
                          "queen or rook (polygons), distance_band or k_nearest.")
    if method != "standard_deviational_ellipse" and not str(args.get("field") or "").strip():
        return tool_error(f"{method} needs the numeric field it tests.", "INVALID_ARGS",
                          hint="spatial_stats_field_needed", method=method)
    if method == "standard_deviational_ellipse":
        if args.get("std_devs") is not None and args.get("confidence") is not None:
            return tool_error("std_devs or confidence, not both.", "INVALID_ARGS",
                              hint="spatial_stats_ellipse_spread", std_devs=args.get("std_devs"),
                              confidence=args.get("confidence"))
        if args.get("std_devs") is not None and args.get("std_devs") not in (1, 2, 3):
            return tool_error("std_devs is 1, 2 or 3.", "INVALID_ARGS", "1, 2 or 3.")
        confidence = args.get("confidence")
        if confidence is not None and not (isinstance(confidence, (int, float)) and 0 < confidence < 1):
            return tool_error("confidence is a share between 0 and 1.", "INVALID_ARGS", "0.95 means 95%.")
    permutations = args.get("permutations")
    most = tuning.ceiling("spatial_stats_max_permutations", MAX_PERMUTATIONS, 99)
    if permutations is not None and (not isinstance(permutations, int) or not 99 <= permutations <= most):
        return tool_error(f"permutations is a whole number from 99 to {most}.", "INVALID_ARGS",
                          "Left out, it is 999.")
    if neighbours == "distance_band" and args.get("distance") is not None:
        try:
            if not float(args["distance"]) > 0:
                raise ValueError
        except (TypeError, ValueError):
            return tool_error("distance is a positive number of metres.", "INVALID_ARGS",
                              hint="spatial_stats_distance_default", distance=str(args.get("distance")))
    if args.get("k") is not None and (not isinstance(args["k"], int) or args["k"] < 1):
        return tool_error("k is a whole number of neighbours, 1 or more.", "INVALID_ARGS", "Left out, it is 8.")
    return None


def _geometry_kind(layer) -> str:
    kind = layer.geometryType()
    for name, member in (("point", "PointGeometry"), ("line", "LineGeometry"), ("polygon", "PolygonGeometry")):
        if kind == enum_member(QgsWkbTypes, "GeometryType", member):
            return name
    return ""


def _numeric(field) -> bool:
    return bool(field.isNumeric())


def _field(layer, name: str, role: str, numeric: bool):

    index = layer.fields().indexOf(name)
    if index < 0:
        index = layer.fields().lookupField(name)
    if index < 0:
        return None, _field_not_found_error(layer, name)
    field = layer.fields().at(index)
    if numeric and not _numeric(field):
        return None, tool_error(
            f"{field.name()!r} of {layer.name()} is {field.typeName()}, and the {role} must be a number.",
            "INVALID_ARGS", hint="spatial_stats_field_not_numeric", layer=layer.name(), field=field.name(),
            field_type=field.typeName(), role=role)
    return index, None


def _plan(args: dict) -> dict:

    ref = str(args.get("layer") or "").strip()
    layer = _find_layer(ref)
    if layer is None:
        return _layer_not_found_error(ref)
    if not isinstance(layer, QgsVectorLayer):
        return tool_error(f"{layer.name()} is not a vector layer.", "INVALID_ARGS",
                          hint="spatial_stats_not_vector", layer=layer.name())
    kind = _geometry_kind(layer)
    if not kind:
        return tool_error(f"{layer.name()} has no geometry.", "INVALID_ARGS",
                          "point, line or polygon layer needed.")
    method = str(args["method"])
    plan: dict = {"layer_id": layer.id(), "layer": layer.name(), "method": method, "kind": kind, "notes": []}

    if method == "standard_deviational_ellipse":
        for key, role in (("weight_field", "weight"), ("group_field", "group")):
            name = str(args.get(key) or "").strip()
            if name:
                index, refused = _field(layer, name, role, numeric=(key == "weight_field"))
                if refused:
                    return refused
                plan[key] = {"name": layer.fields().at(index).name(), "index": index}
        if args.get("field"):
            plan["notes"].append("field is not read by the ellipse.")
            plan["coded"] = coded_fact(hint="spatial_stats_ellipse_field_ignored", field=str(args.get("field")))
        neighbours = ""
        if kind != "point":
            plan["notes"].append(f"{layer.name()} holds {kind}s, so the ellipse is drawn around their centroids.")
    else:
        index, refused = _field(layer, str(args.get("field") or "").strip(), "field", numeric=True)
        if refused:
            return refused
        plan["field"] = {"name": layer.fields().at(index).name(), "index": index}
        neighbours = str(args.get("neighbours") or "")
        if not neighbours:
            neighbours = "queen" if kind == "polygon" else "distance_band"
        if neighbours in ("queen", "rook") and kind != "polygon":
            return tool_error(f"{neighbours} contiguity needs polygons, and {layer.name()} holds {kind}s.",
                              "INVALID_ARGS", hint="spatial_stats_contiguity_kind", neighbours=neighbours,
                              layer=layer.name(), kind=kind)
        if kind != "polygon" or neighbours in ("distance_band", "k_nearest"):
            if kind != "point":
                plan["notes"].append(f"Distances are measured between the {kind}s' centroids.")
        plan["distance"] = float(args["distance"]) if args.get("distance") is not None else None
        plan["k"] = int(args.get("k") or DEFAULT_K)
        if neighbours != "k_nearest" and args.get("k") is not None:
            plan["notes"].append("k is read only with neighbours k_nearest.")
        if neighbours != "distance_band" and args.get("distance") is not None:
            plan["notes"].append("distance is read only with neighbours distance_band.")
        plan["permutations"] = int(args.get("permutations") or DEFAULT_PERMUTATIONS)
        plan["fdr"] = bool(args.get("fdr"))
        if method != "getis_ord_gi_star" and args.get("fdr"):
            plan["notes"].append("fdr applies to getis_ord_gi_star only.")
    plan["neighbours"] = neighbours
    plan["std_devs"] = args.get("std_devs")
    plan["confidence"] = args.get("confidence")
    if method == "standard_deviational_ellipse" and plan["std_devs"] is None and plan["confidence"] is None:
        plan["std_devs"] = 1



    crs = layer.crs()
    plan["crs"] = crs
    plan["transform"] = None
    plan["work_crs"] = crs
    metric = method == "standard_deviational_ellipse" or neighbours in ("distance_band", "k_nearest")
    if metric and crs.isValid() and crs.isGeographic():
        from .processing_guards import _centre_lonlat, _utm_authid

        centre = _centre_lonlat(layer)
        if centre is None:
            return tool_error(f"{layer.name()} is in degrees and its extent is empty.", "INVALID_ARGS",
                              hint="spatial_stats_degrees_empty", layer=layer.name(), crs=crs.authid())
        utm = QgsCoordinateReferenceSystem(_utm_authid(*centre))
        plan["transform"] = QgsCoordinateTransform(crs, utm, QgsProject.instance().transformContext())
        plan["work_crs"] = utm
        extent = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem("EPSG:4326"),
                                        QgsProject.instance()).transformBoundingBox(layer.extent())
        span = extent.width()
        plan["notes"].append(
            f"{layer.name()} is in degrees ({crs.authid() or 'geographic'}), so its coordinates were moved to "
            f"{utm.authid()} ({utm.description()}), the UTM zone of its centre, to measure distances in metres"
            + (f"; it spans {span:.0f} degrees of longitude, so distances far from the centre are stretched."
               if span > 12 else "."))
    elif metric and not crs.isValid():
        plan["notes"].append(f"{layer.name()} has no CRS, so distances are in its own coordinate units.")
    plan["metres_per_unit"] = metres_per_map_unit(plan["work_crs"])

    selected = bool(args.get("selected_only"))
    if selected:
        ids = list(layer.selectedFeatureIds())
        if not ids:
            return tool_error(f"selected_only was asked and nothing is selected in {layer.name()}.", "INVALID_ARGS",
                              "Without selected_only the whole layer is used.")
        plan["selected_ids"] = ids





    count = len(plan["selected_ids"]) if selected else feature_count_of(layer)
    cap = int(limits.current("SPATIAL_STATS_MAX_FEATURES"))
    plan["max_features"] = cap
    if count is not None and count > cap:
        return _too_many(plan, count)
    plan["display_field"] = _display_field(layer)
    plan["fields"] = QgsFields(layer.fields())
    plan["wkb_type"] = layer.wkbType()
    plan["transform_context"] = QgsProject.instance().transformContext()
    plan["output_name"] = str(args.get("output_name") or "").strip()
    return plan


def _too_many(plan: dict, count: int) -> dict:
    return tool_error(
        f"{plan['layer']} has {count:,} features, over the {plan['max_features']:,} one spatial_statistics call "
        "reads (SPATIAL_STATS_MAX_FEATURES).", "INVALID_ARGS",
        hint="spatial_stats_too_many", layer=plan["layer"], count=count, cap=plan["max_features"], kind=plan["kind"])


def _display_field(layer) -> str:
    try:
        name = layer.displayField()
    except Exception:  # noqa: BLE001
        return ""
    index = layer.fields().indexOf(name) if name else -1

    if index < 0 or layer.fields().at(index).isNumeric():
        return ""
    return name




def _open(plan: dict, state: dict) -> dict | None:

    layer = QgsProject.instance().mapLayer(plan["layer_id"])
    if not isinstance(layer, QgsVectorLayer):
        return tool_error(f"{plan['layer']} left the project before it was read.", "EXECUTION_FAILED",
                          "Loading it again makes it available.")
    request = QgsFeatureRequest()
    if plan["method"] == "standard_deviational_ellipse":
        columns = [plan[key]["index"] for key in ("weight_field", "group_field") if plan.get(key)]
        request.setSubsetOfAttributes(sorted(set(columns)))
    if plan.get("selected_ids") is not None:
        request.setFilterFids(plan["selected_ids"])
    request.setLimit(plan["max_features"] + 1)
    feedback = QgsFeedback()
    if hasattr(request, "setFeedback"):
        request.setFeedback(feedback)
    state.update(source=QgsVectorLayerFeatureSource(layer), request=request, feedback=feedback)
    return None


def _read(state: dict, plan: dict, rows: list, cancelled) -> None:






    halted, over, failure = threading.Event(), threading.Event(), []

    def reader(source, request, feedback):
        try:
            with background.on_any_failure(failure.append):
                _fetch(source, request, state, plan, rows, halted)
        finally:
            over.set()

    background.start_kept_thread(reader, state["source"], state["request"], state["feedback"],
                                 name="spatial_statistics read")
    while not over.wait(_STOP_POLL_S):
        if net.is_cancelled(cancelled):
            halted.set()
            state["feedback"].cancel()
            raise InterruptedError("Stopped while the features were read")
    if failure:
        raise failure[0]


def _plain_number(value):
    if value is None or _is_qgis_null(value) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _moved(point, transform):
    if transform is None:
        return point.x(), point.y()
    moved = transform.transform(point)
    return moved.x(), moved.y()


def _rings(geometry) -> list:

    if geometry.isMultipart():
        parts = geometry.asMultiPolygon()
    else:
        parts = [geometry.asPolygon()]
    if not parts or not parts[0]:
        straight = QgsGeometry(geometry.constGet().segmentize()) if geometry.constGet() is not None else geometry
        parts = straight.asMultiPolygon() if straight.isMultipart() else [straight.asPolygon()]
    return [[(p.x(), p.y()) for p in ring] for part in parts for ring in part]


def _fetch(source, request, state: dict, plan: dict, rows: list, halted) -> None:

    method = plan["method"]

    transform = QgsCoordinateTransform(plan["transform"]) if plan["transform"] is not None else None
    contiguity = plan["neighbours"] in ("queen", "rook")
    ellipse = method == "standard_deviational_ellipse"
    field_index = plan["field"]["index"] if plan.get("field") else None
    weight_index = plan["weight_field"]["index"] if plan.get("weight_field") else None
    group_index = plan["group_field"]["index"] if plan.get("group_field") else None
    label_index = plan["fields"].indexOf(plan["display_field"]) if plan.get("display_field") else -1
    features = source.getFeatures(request)
    try:
        for index, feature in enumerate(features, 1):
            if halted.is_set():
                return
            if state["read"] >= plan["max_features"]:
                state["cut"] = True
                return
            state["read"] += 1
            geometry = feature.geometry()
            row = {"fid": feature.id()}
            if geometry is None or geometry.isNull() or geometry.isEmpty():
                row["where"] = None
            elif contiguity:
                row["where"] = _rings(geometry)
            else:
                if plan["kind"] == "point" and not geometry.isMultipart():
                    point = geometry.asPoint()
                else:
                    centre = geometry.centroid()
                    point = centre.asPoint() if centre is not None and not centre.isEmpty() else None
                row["where"] = _moved(QgsPointXY(point), transform) if point is not None else None
            if ellipse:
                row["weight"] = _plain_number(feature[weight_index]) if weight_index is not None else 1.0
                if group_index is not None:
                    group = feature[group_index]
                    row["group"] = None if group is None or _is_qgis_null(group) else str(group)
            else:
                row["value"] = _plain_number(feature[field_index])
                row["attributes"] = feature.attributes()
                row["geometry"] = QgsGeometry(geometry) if geometry is not None else QgsGeometry()
                if label_index >= 0:
                    label = feature[label_index]
                    row["label"] = None if label is None or _is_qgis_null(label) else str(label)[:60]
            rows.append(row)
            background.breathe(index)
    finally:
        features.close()




def _name(row: dict) -> str:
    return f"{row['fid']} ({row['label']})" if row.get("label") else str(row["fid"])


def _neighbours(plan: dict, rows: list, cancelled) -> tuple:

    method = plan["neighbours"]
    if method in ("queen", "rook"):
        xs = [x for row in rows for ring in row["where"] for x, _y in ring]
        ys = [y for row in rows for ring in row["where"] for _x, y in ring]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-9) if xs else 1.0
        found = sm.contiguity([row["where"] for row in rows], method == "rook", span * 1e-9, cancelled)
        return found, {"method": method, "rule": "shared edge" if method == "rook" else "shared vertex"}
    points = [row["where"] for row in rows]
    metres_per_unit = plan.get("metres_per_unit", 1.0)
    factor = metres_per_unit or 1.0
    units = "metres" if metres_per_unit is not None else "the layer's coordinate units"
    if method == "k_nearest":
        found, distances = sm.k_nearest(points, plan["k"], cancelled)
        farthest = max((d[-1] for d in distances if d), default=0.0)
        return found, {"method": "k_nearest", "k": min(plan["k"], max(len(points) - 1, 0)),
                       "farthest_neighbour": round(farthest * factor, 3), "units": units}
    distance = plan.get("distance")
    facts = {"method": "distance_band", "units": units}
    if distance is None:
        distance = sm.band_for_one_neighbour(points, cancelled)
        facts["picked"] = "the smallest band that gives every feature at least one neighbour"
    else:
        distance /= factor
    facts["distance"] = round(distance * factor, 3)
    links = tuning.ceiling("spatial_stats_max_links", MAX_LINKS, 500_000)
    return sm.distance_band(points, distance, links, cancelled), facts


def _cardinality(neighbours: list) -> dict:
    counts = [len(near) for near in neighbours if near]
    if not counts:
        return {}
    return {"min": min(counts), "mean": round(sum(counts) / len(counts), 2), "max": max(counts)}


def _analyse(plan: dict, rows: list, cancelled) -> dict:

    usable = [row for row in rows if row["value"] is not None and row["where"]]
    no_value = [row for row in rows if row["value"] is None]
    no_geometry = [row for row in rows if row["value"] is not None and not row["where"]]
    summary: dict = {}
    if no_value:
        summary["without_value"] = {"count": len(no_value), "features": [_name(r) for r in no_value[:_NAMED]]}
    if no_geometry:
        summary["without_geometry"] = {"count": len(no_geometry),
                                       "features": [_name(r) for r in no_geometry[:_NAMED]]}
    if len(usable) < 4:
        return {"_refusal": tool_error(
            f"{len(usable)} feature(s) of {plan['layer']} have a value in {plan['field']['name']!r} and a geometry; "
            "the statistic needs at least 4.", "INVALID_ARGS", "A layer or selection with more features.")}
    values = [row["value"] for row in usable]
    if max(values) == min(values):
        return {"_refusal": tool_error(
            f"Every feature has {plan['field']['name']} = {values[0]:g}, so nothing stands out to test.",
            "INVALID_ARGS", hint="spatial_stats_constant_field", field=plan["field"]["name"], value=values[0])}
    try:
        neighbours, facts = _neighbours(plan, usable, cancelled)
    except sm.TooManyLinks as exc:
        return {"_refusal": tool_error(
            f"The distance band links about {exc.links:,} pairs of features, over the {exc.limit:,} one call holds.",
            "INVALID_ARGS", hint="spatial_stats_too_many_links", links=exc.links, limit=exc.limit,
            distance=plan.get("distance"))}
    islands = [row for row, near in zip(usable, neighbours) if not near]
    facts["neighbours_per_feature"] = _cardinality(neighbours)
    summary["neighbours"] = facts
    if islands:
        summary["without_neighbour"] = {"count": len(islands), "features": [_name(r) for r in islands[:_NAMED]],
                                        "note": "left NULL in the output: nothing to compare them with"}
    if len(islands) == len(usable):
        return {"_refusal": tool_error(
            f"No feature of {plan['layer']} has a neighbour with neighbours {plan['neighbours']}.", "INVALID_ARGS",
            hint="spatial_stats_no_neighbours", layer=plan["layer"], neighbours=plan["neighbours"],
            kind=plan["kind"])}
    if plan["method"] == "getis_ord_gi_star":
        return _gi(plan, usable, values, neighbours, summary)
    return _moran(plan, usable, values, neighbours, summary, cancelled)


def _gi(plan: dict, usable: list, values: list, neighbours: list, summary: dict) -> dict:
    scores = sm.gi_star(values, neighbours)
    p_values = [None if z is None else sm.two_sided_p(z) for z in scores]
    thresholds = sm.fdr_thresholds([p for p in p_values if p is not None]) if plan["fdr"] else None
    bins = [sm.gi_bin(z, p, thresholds) for z, p in zip(scores, p_values)]
    for row, z, p, level, near in zip(usable, scores, p_values, bins, neighbours):
        row["out"] = [z, p, level, len(near)]
    counts = {label: sum(1 for b in bins if b == level) for level, label, _c in _GI_CLASSES}
    summary["classes"] = {label: count for label, count in counts.items() if count}
    ranked = sorted((z, index) for index, z in enumerate(scores) if z is not None)
    summary["hottest"] = [{"feature": _name(usable[i]), "value": values[i], "z": round(z, 3)}
                          for z, i in reversed(ranked[-5:]) if z > 0]
    summary["coldest"] = [{"feature": _name(usable[i]), "value": values[i], "z": round(z, 3)}
                          for z, i in ranked[:5] if z < 0]
    summary["test"] = ("Getis-Ord Gi*, binary weights with each feature counted as its own neighbour, analytical "
                       "z-score, two-sided p; bins 3/2/1 hot and -1/-2/-3 cold at 99/95/90% confidence")
    summary["false_discovery_rate"] = (
        "on: Benjamini-Hochberg over every tested feature, per confidence level" if plan["fdr"]
        else "off: each feature is tested at its own confidence level; pass fdr true to correct for the many tests")
    return {"fields": [("gi_z", "Double"), ("gi_p", "Double"), ("gi_bin", "Int"), ("gi_nbrs", "Int")],
            "summary": summary}


def _moran(plan: dict, usable: list, values: list, neighbours: list, summary: dict, cancelled) -> dict:
    z = sm.standardise(values)
    whole = sm.global_moran(z, neighbours)
    local, lag, p_values = sm.local_moran(z, neighbours, plan["permutations"], SEED, cancelled)
    counts: dict = {}
    for row, zi, li, lg, p, near in zip(usable, z, local, lag, p_values, neighbours):
        if li is None:
            label = None
        elif p is not None and p <= SIGNIFICANCE:
            label = sm.quadrant(zi, lg)
        else:
            label = "not significant"
        if label:
            counts[label] = counts.get(label, 0) + 1
        row["out"] = [li, p, label, len(near)]
    rand = whole["randomisation"]
    summary["global_moran"] = {
        "I": round(whole["I"], 4), "expected_I": round(whole["expected_I"], 4),
        "z": None if rand["z"] is None else round(rand["z"], 3),
        "p": None if rand["p"] is None else float(f"{rand['p']:.3g}"),
        "assumption": "randomisation (the variance of I under random relabelling of the observed values, with "
                      "their kurtosis); under normality z = "
                      + ("n/a" if whole["normality"]["z"] is None else f"{whole['normality']['z']:.3f}"),
        "reads": _reading(whole["I"], whole["expected_I"], rand["p"]),
    }
    summary["classes"] = {label: counts[label] for label, _t, _c in _LISA_CLASSES if counts.get(label)}
    summary["test"] = (
        f"Local Moran's I, row-standardised weights, two-sided Monte Carlo pseudo p from "
        f"{plan['permutations']} conditional permutations (seed {SEED}, so a rerun gives the same answer). "
        "p = min(2 * (min(N_ge, N_le) + 1) / (permutations + 1), 1), with ties counted in both tails "
        f"and the finite simulation correction; minimum attainable p = {2.0 / (plan['permutations'] + 1):g}. "
        f"A quadrant where p <= {SIGNIFICANCE}, otherwise not significant")
    return {"fields": [("lisa_i", "Double"), ("lisa_p", "Double"), ("lisa_q", "String"), ("lisa_nbrs", "Int")],
            "summary": summary}


def _reading(moran: float, expected: float, p) -> str:
    if p is None:
        return ("spatial autocorrelation inference is undefined for these weights and values: "
                "no z-score or p-value could be computed")
    if p > SIGNIFICANCE:
        return "no evidence of global clustering or dispersion at this threshold"
    if moran > expected:
        return "clustered: similar values sit next to each other more than chance would place them"
    return "dispersed: neighbours differ more than chance would make them"


def _ellipses(plan: dict, rows: list) -> dict:

    scale, expected = sm.ellipse_scale(plan["std_devs"], plan["confidence"])
    groups: dict = {}
    skipped = {"without_geometry": 0, "without_weight": 0, "without_group": 0}
    for row in rows:
        if not row["where"]:
            skipped["without_geometry"] += 1
            continue
        weight = row["weight"]
        if weight is None or weight < 0:
            skipped["without_weight"] += 1
            continue
        key = row.get("group", "") if plan.get("group_field") else ""
        if key is None:
            skipped["without_group"] += 1
            continue
        groups.setdefault(key, ([], []))
        groups[key][0].append(row["where"])
        groups[key][1].append(weight)
    shapes, too_few = [], []
    for key in sorted(groups):
        points, weights = groups[key]
        shape = sm.ellipse(points, weights)
        if shape is None:
            too_few.append(key)
            continue
        shape["group"] = key
        shape["ring"] = sm.ellipse_ring(shape, scale)
        shape["inside"] = sm.share_inside(points, shape, scale)
        shapes.append(shape)
    if not shapes:
        return {"_refusal": tool_error(
            "No group has 3 points with a positive total weight, so no ellipse can be drawn.", "INVALID_ARGS",
            "A layer with more points, or no weight_field/group_field, is needed.")}
    size = (f"{plan['confidence']:.0%} confidence ellipse, k = sqrt(-2 ln(1 - p)) = {scale:.3f} sigma for a "
            "bivariate normal" if plan["confidence"] is not None else
            f"{plan['std_devs']} standard deviation(s), ArcGIS's formula with its sqrt(2) correction: semi-axes "
            f"= {plan['std_devs']} x sqrt(2) x sigma")
    return {"shapes": shapes, "scale": scale, "expected": expected, "size": size,
            "skipped": {k: v for k, v in skipped.items() if v}, "too_few": too_few}




def _free_names(existing: QgsFields, wanted: list) -> list:
    taken = {existing.at(i).name().lower() for i in range(existing.count())}
    out = []
    for name, kind in wanted:
        chosen, number = name, 2
        while chosen.lower() in taken:
            chosen, number = f"{name}_{number}", number + 1
        taken.add(chosen.lower())
        out.append((chosen, kind))
    return out


def _writer(path: str, table: str, fields: QgsFields, wkb_type, crs, context):
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = table
    options.fileEncoding = "UTF-8"
    writer = QgsVectorFileWriter.create(path, fields, wkb_type, crs, context, options)
    if writer.hasError() != enum_member(QgsVectorFileWriter, "WriterError", "NoError"):
        message = writer.errorMessage()
        del writer
        raise OSError(message or "the GeoPackage could not be created")
    return writer


def _target(plan: dict, stem: str) -> tuple:
    from ..core import output_paths

    folder = create_managed_temp_dir("spatial-stats")
    name = output_paths.safe_file_name(f"{plan['layer']} {stem}", stem).replace(" ", "_")[:80]
    path = os.path.join(folder, f"{name}.gpkg")
    if not security.fits_path(path):
        path = os.path.join(folder, f"{stem}-{uuid.uuid4().hex[:8]}.gpkg")
    return path, stem


def _write_copy(plan: dict, rows: list, new_fields: list, cancelled) -> tuple:

    stem = "hotspots" if plan["method"] == "getis_ord_gi_star" else "lisa"
    path, table = _target(plan, stem)
    fields = QgsFields(plan["fields"])
    names = _free_names(fields, new_fields)
    for name, kind in names:
        fields.append(QgsField(name, field_type(kind)))
    writer = _writer(path, table, fields, plan["wkb_type"], plan["crs"], plan["transform_context"])
    try:
        for index, row in enumerate(rows):
            if index % sm.CHECK_EVERY == 0 and net.is_cancelled(cancelled):
                raise InterruptedError("Stopped while the output was written")
            feature = QgsFeature(fields)
            feature.setGeometry(row["geometry"])
            added = row.get("out") or [None] * len(names)
            feature.setAttributes(list(row["attributes"]) + list(added))
            if not writer.addFeature(feature):
                raise OSError(writer.errorMessage() or f"feature {row['fid']} could not be written")
    finally:
        del writer
    return path, table, [name for name, _kind in names]


def _write_ellipses(plan: dict, found: dict, cancelled) -> tuple:
    path, table = _target(plan, "ellipse")
    fields = QgsFields()
    spec = [("group", "String"), ("points", "Int"), ("weight_sum", "Double"), ("center_x", "Double"),
            ("center_y", "Double"), ("x_std_dist", "Double"), ("y_std_dist", "Double"), ("rotation", "Double"),
            ("sigma_long", "Double"), ("sigma_short", "Double"), ("semi_long", "Double"), ("semi_short", "Double"),
            ("size", "String"), ("share_inside", "Double")]
    for name, kind in spec:
        fields.append(QgsField(name, field_type(kind)))
    polygon = enum_member(QgsWkbTypes, "Type", "Polygon", None)
    if polygon is None:
        from qgis.core import Qgis
        polygon = Qgis.WkbType.Polygon
    writer = _writer(path, table, fields, polygon, plan["work_crs"], plan["transform_context"])
    label = (f"{plan['confidence']:.0%} confidence" if plan["confidence"] is not None
             else f"{plan['std_devs']} standard deviation(s)")
    try:
        for shape in found["shapes"]:
            if net.is_cancelled(cancelled):
                raise InterruptedError("Stopped while the output was written")
            feature = QgsFeature(fields)
            feature.setGeometry(QgsGeometry.fromPolygonXY([[QgsPointXY(x, y) for x, y in shape["ring"]]]))
            root2 = math.sqrt(2.0)
            feature.setAttributes([
                shape["group"] or None, shape["count"], shape["weight_sum"], shape["mean_x"], shape["mean_y"],
                shape["sigma_short"] * root2, shape["sigma_long"] * root2, shape["rotation_deg"],
                shape["sigma_long"], shape["sigma_short"], shape["sigma_long"] * found["scale"],
                shape["sigma_short"] * found["scale"], label, shape["inside"]])
            if not writer.addFeature(feature):
                raise OSError(writer.errorMessage() or "an ellipse could not be written")
    finally:
        del writer
    return path, table


def _symbol(kind: str, color: str):
    geometry = {"point": "PointGeometry", "line": "LineGeometry", "polygon": "PolygonGeometry"}[kind]
    symbol = QgsSymbol.defaultSymbol(enum_member(QgsWkbTypes, "GeometryType", geometry))
    symbol.setColor(QColor(color))
    if kind == "polygon" and symbol.symbolLayerCount():
        layer = symbol.symbolLayer(0)
        if hasattr(layer, "setStrokeColor"):
            layer.setStrokeColor(QColor("#808080"))
            layer.setStrokeWidth(0.1)
    return symbol


def _add_layer(plan: dict, path: str, table: str, field: str, name: str) -> dict:

    from ._layers import take_layer_name

    layer = QgsVectorLayer(f"{path}|layername={table}", name, "ogr")
    if not layer.isValid():
        return tool_error(f"The output {path} was written but QGIS cannot open it.", "EXECUTION_FAILED",
                          "The QGIS log has the reason; the disk may be full.")
    kind = _geometry_kind(layer)
    if field and kind:
        classes = _GI_CLASSES if plan["method"] == "getis_ord_gi_star" else _LISA_CLASSES
        categories = [QgsRendererCategory(value, _symbol(kind, color), _tr(label)) for value, label, color in classes]

        categories.append(QgsRendererCategory("", _symbol(kind, "#ffffff"), _tr(_NO_NEIGHBOUR_LABEL)))
        layer.setRenderer(QgsCategorizedSymbolRenderer(field, categories))
    elif kind == "polygon":
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        outline = symbol.symbolLayer(0)
        outline.setColor(QColor(0, 0, 0, 0))
        if hasattr(outline, "setStrokeColor"):
            outline.setStrokeColor(QColor("#b2182b"))
            outline.setStrokeWidth(0.6)
        from qgis.core import QgsSingleSymbolRenderer
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    new_name, renamed = take_layer_name(name, keep_id=layer.id())
    layer.setName(new_name)
    QgsProject.instance().addMapLayer(layer)
    out = {"layer_id": layer.id(), "layer_name": layer.name(), "path": path, "features": int(layer.featureCount())}
    if renamed:
        out["renamed_intermediates"] = renamed
    return out




def _stopped() -> dict:
    return tool_error("Stopped before the statistics were written.", "CANCELLED", "No layer was added.")


def _spatial_statistics(args: dict) -> dict:
    refused = _check_args(args)
    if refused:
        return refused
    started = time.monotonic()
    cancelled = net.current_cancel_check()
    state: dict = {"read": 0, "cut": False}
    rows: list = []
    try:
        plan = run_on_main_thread(_plan, args, timeout=60)
        if "_error" in plan:
            return plan
        opened = run_on_main_thread(_open, plan, state, timeout=60)
        if opened:
            return opened
        _read(state, plan, rows, cancelled)
    except InterruptedError:
        return _stopped()
    finally:
        state.pop("request", None)
        state.pop("source", None)
    if state["cut"]:
        return _too_many(plan, state["read"] + 1)
    read_s = time.monotonic() - started
    result = {"layer": plan["layer"], "method": plan["method"], "features_read": state["read"]}
    if plan.get("selected_ids") is not None:
        result["selected_only"] = True
    path = ""
    try:
        if plan["method"] == "standard_deviational_ellipse":
            found = _ellipses(plan, rows)
            if "_refusal" in found:
                return found["_refusal"]
            path, table = _write_ellipses(plan, found, cancelled)
            name = plan["output_name"] or f"{plan['layer']} ellipse"
            added = run_on_main_thread(_add_layer, plan, path, table, "", name, timeout=60)
            if "_error" in added:
                return added
            result.update(_ellipse_result(plan, found))
        else:
            computed = _analyse(plan, rows, cancelled)
            if "_refusal" in computed:
                return computed["_refusal"]
            compute_s = time.monotonic() - started - read_s
            path, table, names = _write_copy(plan, rows, computed["fields"], cancelled)
            default = "hotspots (Gi*)" if plan["method"] == "getis_ord_gi_star" else "clusters (local Moran)"
            name = plan["output_name"] or f"{plan['layer']} {default}"
            added = run_on_main_thread(_add_layer, plan, path, table, names[2], name, timeout=60)
            if "_error" in added:
                return added
            result["field"] = plan["field"]["name"]
            result["fields_added"] = {"z" if plan["method"] == "getis_ord_gi_star" else "I": names[0],
                                      "p": names[1], "class": names[2], "neighbours": names[3]}
            result.update(computed["summary"])
            result["compute_seconds"] = round(compute_s, 1)
    except InterruptedError:
        if path:
            remove_quietly(path)
        return _stopped()
    except OSError as exc:
        if path:
            remove_quietly(path)
        return tool_error(f"The output could not be written: {str(exc)[:200]}", "EXECUTION_FAILED",
                          "A full disk is a likely cause.")
    result["output"] = added
    if plan["work_crs"] != plan["crs"] or plan["method"] == "standard_deviational_ellipse":
        result["distance_crs"] = plan["work_crs"].authid() or "the layer's own coordinates"
    if plan["notes"]:
        result["note"] = " ".join(plan["notes"])
    if plan.get("coded"):
        result.update(plan["coded"])
    result["seconds"] = round(time.monotonic() - started, 1)
    return result


def _ellipse_result(plan: dict, found: dict) -> dict:
    units = plan["work_crs"].mapUnits() if plan["work_crs"].isValid() else None
    unit_name = ""
    if units is not None:
        from qgis.core import QgsUnitTypes
        unit_name = QgsUnitTypes.toString(units)
    rows = []
    for shape in found["shapes"][:_GROUPS_SHOWN]:
        rows.append({
            **({"group": shape["group"]} if plan.get("group_field") else {}),
            "points": shape["count"], "center": [round(shape["mean_x"], 3), round(shape["mean_y"], 3)],
            "rotation_deg": round(shape["rotation_deg"], 1),
            "semi_long": round(shape["sigma_long"] * found["scale"], 3),
            "semi_short": round(shape["sigma_short"] * found["scale"], 3),
            "share_inside": round(shape["inside"], 3),
        })
    out = {"ellipses": rows, "ellipse_count": len(found["shapes"]), "size": found["size"],
           "expected_share_inside": round(found["expected"], 3),
           "rotation": "bearing of the long axis, clockwise from north (0 to 180)",
           "units": unit_name or "the layer's coordinate units",
           "fields": "x_std_dist and y_std_dist are ArcGIS's (sigma x sqrt(2)) along the short and long axes; "
                     "semi_long and semi_short are the drawn ellipse's half axes"}
    if len(found["shapes"]) > _GROUPS_SHOWN:
        out["ellipses_cut"] = f"the first {_GROUPS_SHOWN} of {len(found['shapes'])} groups; the layer has them all"
    if found["too_few"]:
        out["groups_without_ellipse"] = {"count": len(found["too_few"]), "why": "under 3 points or no weight",
                                         "groups": found["too_few"][:_NAMED]}
    if found["skipped"]:
        out["skipped"] = found["skipped"]
    if plan.get("weight_field"):
        out["weight_field"] = plan["weight_field"]["name"]
    return out


__all__ = ["register_spatial_stats_tools", "METHODS", "NEIGHBOURS"]
