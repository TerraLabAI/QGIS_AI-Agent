# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only inspection handlers: features, expressions, field/raster stats, renderer introspection, provider capabilities and geometry."""


from __future__ import annotations

import json
import math
import re
import threading
import time

from qgis.core import QgsExpression, QgsFeatureRequest, QgsGeometry, QgsProject, QgsRasterLayer, QgsVectorLayer

from ..core import ground, net
from ..core.feature_requests import feature_request, first_feature
from ..core.geometry_budget import VertexBudget
from ..core.logger import log_debug
from ..core.policy import MAX_LIST_CHARS
from ..core.qt_compat import enum_member
from ..core.vsi import streamed_in_place
from ._layers import loaded_feature_count
from .data_tools import _run_on_main_thread
from .layer_lookup import _field_not_found_error, _find_layer, _jsonable_value, _layer_not_found_error





STATS_CHUNK = 20_000

















_SCAN_PERMITS = threading.BoundedSemaphore(1)


_SCAN_WAIT_S = 240.0












_SPATIAL_PREDICATES = frozenset({
    "intersects", "within", "contains", "overlaps", "touches", "crosses", "disjoint",
    "overlay_intersects", "overlay_within", "overlay_contains", "overlay_touches",
    "overlay_crosses", "overlay_disjoint", "overlay_equals",
})

_PREDICATE_MEANS = {
    "intersects": "every feature touching the area at all, a boundary or a single corner included",
    "within": "only the features lying entirely inside the area",
    "contains": "only the features that hold the whole area",
    "overlaps": "only the features that lie partly in and partly out",
    "touches": "only the features meeting the boundary without sharing any inside",
    "crosses": "only the features crossing the area",
    "disjoint": "only the features with nothing in the area",
}



_PREDICATE_ALTERNATIVE = {"intersects": "within", "touches": "within", "overlaps": "within",
                          "crosses": "within", "within": "intersects"}



_COUNT_SCAN_MAX = 200_000
_COUNT_BUDGET_S = 0.3


def _count_matching(layer, expression: str) -> tuple:
    """``(features matching *expression*, whether the scan finished)``."""





    from qgis.core import QgsExpressionContext, QgsExpressionContextUtils

    expr = QgsExpression(expression)
    if expr.hasParserError():
        return 0, False
    request = QgsFeatureRequest()
    request.setFilterExpression(expression)
    context = QgsExpressionContext()
    context.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    request.setExpressionContext(context)
    try:
        columns = list(expr.referencedColumns())




        if "*" not in columns:
            request.setSubsetOfAttributes(columns, layer.fields())
        if not expr.needsGeometry():
            request.setFlags(QgsFeatureRequest.Flag.NoGeometry)
    except Exception as exc:  # noqa: BLE001 - a request that cannot be narrowed is still a count
        log_debug(f"_count_matching: narrowing the request failed: {exc}")
    deadline = time.monotonic() + _COUNT_BUDGET_S
    counted = 0
    iterator = layer.getFeatures(request)
    try:
        for counted, _feature in enumerate(iterator, start=1):
            if counted >= _COUNT_SCAN_MAX:
                return counted, False
            if counted % 1024 == 0 and time.monotonic() > deadline:
                return counted, False
    except Exception:  # noqa: BLE001 - a provider that stops mid scan has still counted this far
        return counted, False
    finally:
        try:
            iterator.close()
        except Exception:  # nosec B110 - an iterator already spent needs no closing
            pass
    return counted, True


def _swapped_predicate(expression: str, before: str, after: str):
    """*expression* with one spatial function swapped for another, or None."""





    swapped, hits = re.subn(rf"\b{before}\s*\(", f"{after}(", str(expression or ""), flags=re.IGNORECASE)
    if not hits:
        return None
    return None if QgsExpression(swapped).hasParserError() else swapped


def _spatial_criterion(layer, expression: str, expr, exact: bool) -> dict:
    """The rule a filtered count used, and the count under the obvious alternative."""





    try:
        used = _SPATIAL_PREDICATES.intersection(name.lower() for name in expr.referencedFunctions())
    except Exception:  # noqa: BLE001 - an expression that cannot list its functions carries no criterion
        return {}
    if not used:
        return {}
    predicate = next((name for name in ("within", "intersects", "contains", "overlaps",
                                        "touches", "crosses", "disjoint") if name in used), min(used))
    criterion = {"predicate": predicate, "counts": _PREDICATE_MEANS.get(predicate, predicate)}
    other = _PREDICATE_ALTERNATIVE.get(predicate) if len(used) == 1 else None
    if other and exact:
        swapped = _swapped_predicate(expression, predicate, other)
        if swapped:
            alternative, alternative_exact = _count_matching(layer, swapped)
            if alternative_exact:
                criterion["under_" + other] = alternative
                criterion["under_" + other + "_counts"] = _PREDICATE_MEANS.get(other, other)
    criterion["note"] = ("A spatial count is only a number once the rule is named. Give the rule with the "
                         "figure, in the user's words, and say the other number when it differs.")
    return {"criterion": criterion}


def _get_features(args: dict) -> dict:
    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])

    if not isinstance(layer, QgsVectorLayer):
        return {"_error": f"Layer '{args['layer_name']}' is not a vector layer"}

    limit = max(int(args.get("limit", 100) or 100), 1)
    offset = max(int(args.get("offset", 0) or 0), 0)
    include_geometry = bool(args.get("include_geometry", False))

    available = {f.name() for f in layer.fields()}
    names = [f.name() for f in layer.fields()]
    fields_arg = args.get("fields")
    missing: list = []
    if isinstance(fields_arg, list) and fields_arg:
        selected = [f for f in fields_arg if f in available]
        if not selected:
            return {"_error": f"None of the requested fields exist: {fields_arg}", "fields": names}


        missing = [f for f in fields_arg if f not in available]
        names = selected

    request = QgsFeatureRequest()
    expression = args.get("expression")




    if expression and streamed_in_place(layer):
        return {
            "_error": (f"'{layer.name()}' is read over HTTP where it is published, so filtering it "
                       "scans the whole file across the network instead of reading its index."),
            "_code": "INVALID_ARGS",
            "_suggestion": ("Clip it to the area first, then filter the local copy. "
                            "Reading a page without an expression stays fast."),
        }
    if expression:
        from qgis.core import QgsExpressionContext, QgsExpressionContextUtils
        expr = QgsExpression(expression)
        if expr.hasParserError():
            return {"_error": f"Invalid expression: {expr.parserErrorString()}", "_code": "INVALID_ARGS"}
        request.setFilterExpression(expression)





        ctx = QgsExpressionContext()
        ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
        request.setExpressionContext(ctx)


    native_offset = hasattr(request, "setOffset")
    if native_offset:
        request.setOffset(offset)
        request.setLimit(limit + 1)
    else:
        request.setLimit(offset + limit + 1)

    collected = []
    size = 0
    over_budget = False
    for i, feat in enumerate(layer.getFeatures(request)):
        if not native_offset and i < offset:
            continue
        row = {"_fid": feat.id()}
        for name in names:
            row[name] = feat[name]
        if include_geometry:
            geom = feat.geometry()
            row["_wkt"] = None if (geom is None or geom.isNull()) else geom.asWkt(6)




        size += len(json.dumps(row, default=str))
        if collected and size > MAX_LIST_CHARS:
            over_budget = True
            break
        collected.append(row)

    fetched = len(collected)
    features = collected[:limit]
    result = {"layer": layer.name(), "count": len(features), "offset": offset, "features": features}





    total = loaded_feature_count(layer, layer.featureCount())
    if isinstance(total, int) and total >= 0:
        result["layer_feature_count"] = total
    if expression:
        matched, exact = _count_matching(layer, expression)
        result["matched" if exact else "matched_at_least"] = matched
        result["count_is"] = ("rows in this page; 'matched' is how many features the expression selects in "
                              "the whole layer")
        result.update(_spatial_criterion(layer, expression, expr, exact))
    if missing:
        result["fields_not_found"] = missing
        result["fields"] = [f.name() for f in layer.fields()]
    if include_geometry:
        result["geometry_crs"] = layer.crs().authid()
    if over_budget or fetched > limit:
        result["next_offset"] = offset + len(features)
        result["more_available"] = True
    if over_budget:
        result["page_limited_by"] = (
            f"{MAX_LIST_CHARS} characters. Ask for fewer fields, a filter or "
            "the next page with offset."
        )
    return result











_MEASURE_FUNCTIONS = frozenset({"$area", "$perimeter", "$length"})


def _measurement(expr, layer) -> dict:
    """The unit a measuring expression answers in, and the ellipsoid behind it."""





    try:
        used = _MEASURE_FUNCTIONS.intersection(expr.referencedFunctions())
    except Exception:  # noqa: BLE001 - an expression that cannot list its functions says nothing here
        return {}
    if not used or layer is None:
        return {}
    measured_on = ground.measure_on_ellipsoid(expr, layer)
    out: dict = {}
    if measured_on:
        out["ellipsoid"] = measured_on
        out["corrected"] = ("This project measures planar (ellipsoid NONE), so the expression was measured on "
                            "the WGS84 ellipsoid here and the value below is ground measure.")
        if "$area" in used:
            out["area_units"] = "square metres of ground"
        if used - {"$area"}:
            out["length_units"] = "metres of ground"
        return {"measurement": out}
    try:
        from qgis.core import QgsUnitTypes

        project = QgsProject.instance()
        out["ellipsoid"] = str(project.ellipsoid() or "")
        if "$area" in used:
            out["area_units"] = QgsUnitTypes.toString(project.areaUnits())
        if used - {"$area"}:
            out["length_units"] = QgsUnitTypes.toString(project.distanceUnits())
    except Exception:  # noqa: BLE001 - a unit we cannot name is left unnamed, never guessed
        return {}
    return {"measurement": out} if out else {}


def _evaluate_expression(args: dict) -> dict:
    from qgis.core import QgsExpressionContext, QgsExpressionContextUtils

    expression = args.get("expression")
    if not expression:
        return {"_error": "expression is required"}
    expr = QgsExpression(expression)
    if expr.hasParserError():
        return {"_error": f"Expression parser error: {expr.parserErrorString()}", "_code": "INVALID_ARGS"}

    ctx = QgsExpressionContext()
    layer = None
    layer_name = args.get("layer_name")
    if layer_name:
        layer = _find_layer(layer_name)
        if not layer:
            return _layer_not_found_error(layer_name)
        ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    else:
        ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(None))

    measurement = _measurement(expr, layer)
    expr.prepare(ctx)
    feature_id = args.get("feature_id")
    is_vector = isinstance(layer, QgsVectorLayer)
    needs_feature = _needs_feature(expr)



    if is_vector and feature_id is not None:
        request = feature_request(expression=expr, fields=layer.fields()).setFilterFid(int(feature_id))
        feat = first_feature(layer, request)
        if feat is None or not feat.isValid():



            if int(feature_id) <= 0 and not needs_feature:
                value = expr.evaluate(ctx)
                if expr.hasEvalError():
                    return {"_error": f"Expression evaluation error: {expr.evalErrorString()}", "_code": "INVALID_ARGS"}
                return {"expression": expression, "layer": layer.name(), "result": _jsonable_value(value),
                        "note": "evaluated in the layer scope, no feature: an aggregate over the whole layer",
                        **measurement}
            first = first_feature(layer, feature_request(attributes=[], geometry=False, limit=1))
            hint = f"; ids start at {first.id()}" if first is not None and first.isValid() else ""
            return {"_error": f"No feature with id {feature_id} in '{layer.name()}'{hint}", "_code": "INVALID_ARGS",
                    "_suggestion": "Leave feature_id out for an aggregate over the layer, or pass an id from "
                                   "get_features."}
        ctx.setFeature(feat)
        value = expr.evaluate(ctx)
        if expr.hasEvalError():
            return {"_error": f"Expression evaluation error: {expr.evalErrorString()}", "_code": "INVALID_ARGS"}
        return {"expression": expression, "feature_id": int(feature_id), "result": _jsonable_value(value),
                **measurement}

    if is_vector and needs_feature:
        limit = args.get("limit", 10)
        try:
            limit = max(1, min(int(limit), 1000))
        except (TypeError, ValueError):
            limit = 10
        results = []
        request = feature_request(expression=expr, fields=layer.fields(), limit=limit)
        for feat in layer.getFeatures(request):
            ctx.setFeature(feat)
            value = expr.evaluate(ctx)
            if expr.hasEvalError():
                return {
                    "_error": f"Expression evaluation error on feature {feat.id()}: {expr.evalErrorString()}",
                    "_code": "INVALID_ARGS",
                }
            results.append({"feature_id": feat.id(), "result": _jsonable_value(value)})
        return {
            "expression": expression,
            "per_feature": True,
            "evaluated": len(results),
            "feature_count": layer.featureCount(),
            "results": results,
            **measurement,
            "_note": "Expression references geometry/fields → evaluated per feature. Pass feature_id for one feature, "
            "or wrap in an aggregate (sum/mean/count) for a single layer-wide value.",
        }


    value = expr.evaluate(ctx)
    if expr.hasEvalError():
        return {"_error": f"Expression evaluation error: {expr.evalErrorString()}", "_code": "INVALID_ARGS"}
    out = {"expression": expression, "result": _jsonable_value(value), **measurement}
    if value is None and needs_feature and not is_vector:
        out["_note"] = (
            "Result is null because the expression needs a feature but no vector layer_name was given. Pass layer_name "
            "(and optionally feature_id)."
        )
    return out


def _needs_feature(expr) -> bool:
    """Whether *expr* reads the feature it is evaluated on."""








    try:
        root = expr.rootNode()
        if root is not None:
            return _node_needs_feature(root)
    except Exception:  # noqa: BLE001  # nosec B110 - the whole-expression reading below is the fallback
        pass
    return expr.needsGeometry() or bool(expr.referencedColumns())


def _node_needs_feature(node) -> bool:
    from qgis.core import (
        QgsExpressionNodeBetweenOperator,
        QgsExpressionNodeBinaryOperator,
        QgsExpressionNodeColumnRef,
        QgsExpressionNodeCondition,
        QgsExpressionNodeFunction,
        QgsExpressionNodeInOperator,
        QgsExpressionNodeLiteral,
        QgsExpressionNodeUnaryOperator,
    )

    if node is None or isinstance(node, QgsExpressionNodeLiteral):
        return False
    if isinstance(node, QgsExpressionNodeColumnRef):
        return True
    if isinstance(node, QgsExpressionNodeBinaryOperator):
        return _node_needs_feature(node.opLeft()) or _node_needs_feature(node.opRight())
    if isinstance(node, QgsExpressionNodeUnaryOperator):
        return _node_needs_feature(node.operand())
    if isinstance(node, QgsExpressionNodeInOperator):
        return _node_needs_feature(node.node()) or any(_node_needs_feature(n) for n in node.list().list())
    if isinstance(node, QgsExpressionNodeBetweenOperator):
        return any(_node_needs_feature(n) for n in (node.node(), node.lowerBound(), node.higherBound()))
    if isinstance(node, QgsExpressionNodeCondition):
        return (any(_node_needs_feature(c.whenExp()) or _node_needs_feature(c.thenExp()) for c in node.conditions())
                or _node_needs_feature(node.elseExp()))
    if isinstance(node, QgsExpressionNodeFunction):
        function = QgsExpression.Functions()[node.fnIndex()]
        arguments = node.args().list() if node.args() is not None else []
        if "Aggregates" in function.groups():


            if function.name() == "relation_aggregate" or "parent" in node.referencedVariables():
                return True
            names = [parameter.name() for parameter in function.parameters()]
            if "group_by" in names and names.index("group_by") < len(arguments):
                grouped = arguments[names.index("group_by")]
                return not (isinstance(grouped, QgsExpressionNodeLiteral) and grouped.value() is None)
            return False
        if function.usesGeometry(node) or function.referencedColumns(node):
            return True
        if function.name() in ("$id", "$currentfeature"):
            return True
        return any(_node_needs_feature(n) for n in arguments)

    return node.needsGeometry() or bool(node.referencedColumns())


def _get_field_statistics(args: dict) -> dict:
    """Every statistic of one field, from one pass read a slice at a time."""
















    if not _SCAN_PERMITS.acquire(timeout=_SCAN_WAIT_S):
        return {"_error": ("Another field is still being scanned; this one waited "
                           f"{_SCAN_WAIT_S:.0f} seconds for its turn."),
                "_code": "INVALID_ARGS",
                "_suggestion": "Ask for one field at a time, or read the column with get_features."}
    try:
        return _scan_field(args)
    finally:
        _SCAN_PERMITS.release()


def _scan_field(args: dict) -> dict:
    """One scan, holding one provider iterator, under the permit taken above."""
    state = _run_on_main_thread(_stats_open, args["layer_name"], args.get("field"), timeout=60)
    if state.get("_error"):
        return state
    try:
        while True:
            cancelled = net.current_cancel_check()
            try:
                stopped = callable(cancelled) and bool(cancelled())
            except Exception:  # noqa: BLE001 - a broken optional cancel check does not fail a read
                stopped = False
            if stopped:
                return {"_error": "The run was stopped.", "_code": "CANCELLED",
                        "_suggestion": "Wait for the next user message."}
            if _run_on_main_thread(_stats_read, state, timeout=120):
                break
    finally:
        _run_on_main_thread(_stats_close, state, timeout=30)
    return _stats_summary(state)


def _band_number(field: str) -> int:
    """The band a raster field name points at ("Band 1", "band_2"), else 1."""
    match = re.search(r"(\d+)", str(field or ""))
    return int(match.group(1)) if match else 1


def _not_a_vector_error(layer, field: str = "") -> dict:
    """A raster asked for vector statistics, answered with the tool that reads it."""






    name = layer.name()
    if isinstance(layer, QgsRasterLayer):
        band = _band_number(field)
        return {
            "_error": (f"Layer {name!r} is a raster, and this tool reads attribute columns of a vector layer. "
                       "A raster has no attribute table, it has bands."),
            "code": "INVALID_ARGS",
            "suggestion": (f"Call get_raster_band_stats with layer_name={name!r} and band={band} for the min, max, "
                           "mean and standard deviation of its pixels."),
            "band": band,
        }
    kind = type(layer).__name__.replace("Qgs", "").replace("Layer", "").lower() or "other"
    return {
        "_error": f"Layer {name!r} is a {kind} layer, and this tool reads the attribute columns of a vector layer.",
        "code": "INVALID_ARGS",
        "suggestion": "Name a vector layer, or call list_layers to see which layers in this project are vector.",
    }


def _stats_open(layer_name: str, field: str) -> dict:
    """Main thread: the layer, the field, and the iterator the slices advance."""
    layer = _find_layer(layer_name)
    if not layer:
        return _layer_not_found_error(layer_name)
    if not isinstance(layer, QgsVectorLayer):
        return _not_a_vector_error(layer, field)
    if not field:
        return {"_error": f"Which field of {layer.name()!r}? This tool summarises one attribute column.",
                "code": "INVALID_ARGS",
                "fields": [f.name() for f in layer.fields()],
                "suggestion": "Pass 'field' with one of the names listed in 'fields'."}
    index = layer.fields().indexOf(field)
    if index < 0:



        return _field_not_found_error(layer, field)
    request = QgsFeatureRequest().setFlags(QgsFeatureRequest.Flag.NoGeometry)
    request.setSubsetOfAttributes([index])
    return {"layer_name": layer.name(), "field": field, "index": index,
            "features": layer.getFeatures(request), "numbers": [], "others": [],
            "total": 0, "missing": 0, "numeric_only": True}


def _stats_read(state: dict) -> bool:
    """Main thread: fold at most ``STATS_CHUNK`` rows in."""





    numbers, others, index = state["numbers"], state["others"], state["index"]
    for read, feature in enumerate(state["features"], start=1):
        state["total"] += 1
        value = feature[index]
        if value is None or (hasattr(value, "isNull") and value.isNull()):
            state["missing"] += 1
        elif state["numeric_only"] and not isinstance(value, bool) and isinstance(value, (int, float)):
            numbers.append(float(value))
        else:
            state["numeric_only"] = False
            others.append(value)
        if read >= STATS_CHUNK:
            return False
    return True


def _stats_close(state: dict) -> None:
    """Main thread: the provider's iterator is released where it was opened."""
    features = state.pop("features", None)
    if features is not None:
        try:
            features.close()
        except Exception:  # nosec B110 - an iterator already spent needs no closing
            pass


def _stats_summary(state: dict) -> dict:
    """The summaries themselves, in the worker."""








    from qgis.core import QgsStatisticalSummary, QgsStringStatisticalSummary

    name, field = state["layer_name"], state["field"]
    numbers, others, missing = state["numbers"], state["others"], state["missing"]
    stats: dict = {"count": state["total"]}
    if state["numeric_only"] and numbers:
        summary = QgsStatisticalSummary()
        summary.calculate(numbers)

        stats["count_distinct"] = summary.variety() + (1 if missing else 0)
        stats["count_missing"] = missing
        for key, value in (("min", summary.min()), ("max", summary.max()), ("sum", summary.sum()),
                           ("mean", summary.mean()), ("median", summary.median()),
                           ("stdev", summary.stDev()), ("q1", summary.firstQuartile()),
                           ("q3", summary.thirdQuartile()), ("iqr", summary.interQuartileRange())):
            stats[key] = _jsonable_value(value)
        return _statistics_result(name, field, stats, _fragment_split(numbers))

    values = [_stat_text(value) for value in others + numbers]
    if values or missing:
        summary = QgsStringStatisticalSummary()
        summary.calculate(values + [None] * missing)
        stats["count_distinct"] = summary.countDistinct()
        stats["count_missing"] = summary.countMissing()
        if values:
            stats["min"] = _jsonable_value(summary.min())
            stats["max"] = _jsonable_value(summary.max())
    else:
        stats["count_distinct"] = 0
        stats["count_missing"] = missing
    return _statistics_result(name, field, stats)














_FRAGMENT_SHARE_PCT = 1.0
_FRAGMENT_MIN_FEATURES = 5


_FRAGMENT_MIN_ROW_SHARE = 0.2
_FRAGMENT_MIN_SPREAD = 100.0


def _fragment_split(values: list) -> dict:
    """How many of *values* together carry ``_FRAGMENT_SHARE_PCT`` of their sum."""





    if len(values) < _FRAGMENT_MIN_FEATURES or any(value <= 0 for value in values):
        return {}
    ordered = sorted(values)
    count = len(ordered)
    total = math.fsum(ordered)
    median = ordered[count // 2]
    if total <= 0 or median <= 0 or ordered[-1] / median < _FRAGMENT_MIN_SPREAD:
        return {}
    cut = total * _FRAGMENT_SHARE_PCT / 100.0
    running = 0.0
    small = 0
    for value in ordered:
        running += value
        if running > cut:
            break
        small += 1
    if small < max(2, int(round(_FRAGMENT_MIN_ROW_SHARE * count))):
        return {}
    carried = math.fsum(ordered[:small])
    return {
        "features": count,
        "smallest": small,
        "smallest_share_of_sum_pct": round(100.0 * carried / total, 3),
        "smallest_largest_value": ordered[small - 1],
        "rest": count - small,
        "rest_share_of_sum_pct": round(100.0 * (total - carried) / total, 3),
    }


def _fragment_note(split: dict, field: str) -> str:
    """The sentence that stops the count being given as a number of objects."""
    return (f"'count' is {split['features']} rows of this layer, one per geometry. "
            f"{split['smallest']} of them together carry {split['smallest_share_of_sum_pct']}% of the sum "
            f"of {field!r} (none above {split['smallest_largest_value']:.4g}), and the other "
            f"{split['rest']} carry "
            f"{split['rest_share_of_sum_pct']}%. If this layer came out of a clip, an intersection or an "
            f"overlay, those small rows are boundary fragments of features that are mostly outside it. "
            f"Before giving the count as a number of objects, say which rule it uses: touching the area, "
            f"lying entirely inside it, or having its centroid inside it. Those are three different numbers.")


def _stat_text(value) -> str:
    """A value as text that sorts the way the value does."""







    if type(value).__name__ in ("QDate", "QDateTime", "QTime"):
        from qgis.PyQt.QtCore import Qt

        return value.toString(Qt.DateFormat.ISODate)
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def _statistics_result(layer_name: str, field: str, stats: dict, split: dict = None) -> dict:


    order = ("count", "count_distinct", "count_missing", "min", "max", "sum", "mean",
             "median", "stdev", "q1", "q3", "iqr")
    ordered = {key: stats[key] for key in order if key in stats}
    out = {"layer": layer_name, "field": field, "statistics": ordered}
    if split:
        out["count_breakdown"] = split
        out["count_note"] = _fragment_note(split, field)
    return out


def _get_renderer_info(args: dict) -> dict:
    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])
    if not isinstance(layer, QgsVectorLayer):
        return {"_error": f"Layer '{args['layer_name']}' is not a vector layer"}

    renderer = layer.renderer()
    if renderer is None:
        return {"layer": layer.name(), "renderer": None}

    def _color(symbol):
        try:
            return symbol.color().name() if symbol else None
        except Exception:  # nosec B112 - unsupported item is omitted
            return None

    rtype = renderer.type()
    info = {"type": rtype, "opacity": layer.opacity()}
    omitted = 0
    try:
        if rtype == "categorizedSymbol":
            info["field"] = renderer.classAttribute()
            categories = renderer.categories()
            info["categories"] = [
                {"value": _jsonable_value(c.value()), "label": c.label(), "color": _color(c.symbol())}
                for c in categories[:5000]
            ]
            omitted = max(0, len(categories) - 5000)
        elif rtype == "graduatedSymbol":
            info["field"] = renderer.classAttribute()
            ranges = renderer.ranges()
            info["ranges"] = [
                {"lower": r.lowerValue(), "upper": r.upperValue(), "label": r.label(), "color": _color(r.symbol())}
                for r in ranges[:5000]
            ]
            omitted = max(0, len(ranges) - 5000)
        elif rtype == "singleSymbol":
            info["color"] = _color(renderer.symbol())
    except Exception as e:
        info["_warning"] = f"Partial renderer introspection: {e}"
    if omitted:
        info["classes_omitted"] = omitted
    return {"layer": layer.name(), "renderer": info}


def _raster_sample(args: dict) -> dict:
    from qgis.core import QgsCoordinateTransform, QgsPointXY

    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])
    if not isinstance(layer, QgsRasterLayer):
        return {"_error": f"Layer '{args['layer_name']}' is not a raster layer"}





    provider_type = layer.providerType()
    if provider_type == "wms":
        return {
            "_error": (
                f"Layer '{layer.name()}' is a WMS/WMTS/XYZ tile layer and has no sampleable pixel "
                "values (it serves rendered image tiles, not numeric bands)."
            ),
            "_code": "INVALID_ARGS",
            "provider_type": provider_type,
        }

    point = QgsPointXY(float(args["x"]), float(args["y"]))
    crs_id = args.get("crs")
    if crs_id:
        from qgis.core import QgsCoordinateReferenceSystem
        src = QgsCoordinateReferenceSystem(crs_id)
        if not src.isValid():
            return {"_error": f"Invalid coordinate CRS: {crs_id}", "_code": "INVALID_ARGS",
                    "_suggestion": "Pass an EPSG code such as EPSG:4326."}
        if src.isValid() and src != layer.crs():
            try:
                point = QgsCoordinateTransform(src, layer.crs(), QgsProject.instance()).transform(point)
            except Exception as e:
                return {"_error": f"Coordinate transform failed: {e}"}

    band = int(args.get("band", 1))
    if band < 1 or band > layer.bandCount():
        return {"_error": f"Band {band} is outside the raster's 1..{layer.bandCount()} range.",
                "_code": "INVALID_ARGS", "_suggestion": "Pass an available raster band."}
    try:
        value, ok = layer.dataProvider().sample(point, band)
    except Exception as e:
        return {"_error": f"Sampling failed: {e}"}
    if not ok:
        return {"_error": "No data at this location (outside extent or nodata)", "_code": "INVALID_ARGS"}



    out = {"layer": layer.name(), "band": band, "value": value, "x": point.x(), "y": point.y(),
           "crs": layer.crs().authid(), "coordinates_are": "the sampled point in the layer's own CRS"}
    units = _crs_units(layer.crs())
    if units:
        out["crs_units"] = units
    if crs_id and str(crs_id).strip().upper() != str(layer.crs().authid()).upper():
        out["asked_in_crs"] = str(crs_id)
    return out


def _crs_units(crs) -> str:
    """The name of the unit an extent or a distance in *crs* is written in."""
    try:
        from qgis.core import QgsUnitTypes

        return str(QgsUnitTypes.toString(crs.mapUnits()))
    except Exception:  # noqa: BLE001 - a unit we cannot name is left unnamed, never guessed
        return ""


def _extent_block(rect, crs) -> dict:
    """An extent with the CRS it is written in and the name of its unit."""







    block = {"xmin": rect.xMinimum(), "ymin": rect.yMinimum(),
             "xmax": rect.xMaximum(), "ymax": rect.yMaximum(),
             "crs": crs.authid() if crs is not None else ""}
    units = _crs_units(crs)
    if units:
        block["units"] = units
        block["width"] = rect.width()
        block["height"] = rect.height()
    return block


def _remote_raster(layer) -> bool:
    """Whether reading this raster's pixels means reading them over the network."""
    if streamed_in_place(layer):
        return True
    try:
        source = str(layer.source() or "")
        provider = str(layer.providerType() or "").lower()
    except Exception:  # noqa: BLE001 - a layer that cannot name its source is read as local
        return False
    return source.startswith(("http://", "https://")) or provider in ("wms", "wcs", "arcgismapserver")











_REMOTE_STATS_PIXELS_MAX = 60_000_000


def _canvas_window(layer, extent):
    """What the user is looking at, in the layer's CRS, clipped to the layer. Main thread."""
    try:
        from qgis.core import QgsCoordinateTransform
        from qgis.utils import iface

        canvas = iface.mapCanvas() if iface is not None else None
        if canvas is None:
            return None
        view = canvas.extent()
        crs = canvas.mapSettings().destinationCrs()
        if crs.isValid() and layer.crs().isValid() and crs != layer.crs():
            view = QgsCoordinateTransform(crs, layer.crs(), QgsProject.instance()).transformBoundingBox(view)
        if view.isNull() or view.isEmpty() or not view.intersects(extent):
            return None
        return view.intersect(extent)
    except Exception:  # noqa: BLE001 - no canvas, no window, the caller falls back to the centre
        return None


def _measured_window(layer) -> tuple:
    """``(window in the layer's CRS, why)`` for a raster too large to read whole over HTTP."""




    from qgis.core import QgsRectangle

    try:
        full = int(layer.width() or 0) * int(layer.height() or 0)
        px = abs(float(layer.rasterUnitsPerPixelX() or 0))
        py = abs(float(layer.rasterUnitsPerPixelY() or 0))
        extent = QgsRectangle(layer.extent())
    except Exception:  # noqa: BLE001 - a raster that cannot describe its grid is read as before
        return None, ""
    if full <= _REMOTE_STATS_PIXELS_MAX or px <= 0 or py <= 0 or extent.isNull() or not _remote_raster(layer):
        return None, ""
    window = _canvas_window(layer, extent)
    if window is None or window.isNull() or window.isEmpty():
        window = QgsRectangle(extent)
    pixels = (window.width() / px) * (window.height() / py)
    if pixels > _REMOTE_STATS_PIXELS_MAX:
        share = math.sqrt(_REMOTE_STATS_PIXELS_MAX / pixels)
        centre = window.center()
        half_width, half_height = window.width() * share / 2.0, window.height() * share / 2.0
        window = QgsRectangle(centre.x() - half_width, centre.y() - half_height,
                              centre.x() + half_width, centre.y() + half_height)
    return window, (f"{layer.name()!r} is read over the network and spans {layer.width():,} by "
                    f"{layer.height():,} pixels. Reading all of them for one summary held QGIS 47 s in "
                    "production, so a window was read instead")





COVERAGE_FULL_PCT = 98.0


def _get_raster_band_stats(args: dict) -> dict:
    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])
    if not isinstance(layer, QgsRasterLayer):
        return {"_error": f"Layer '{args['layer_name']}' is not a raster layer"}

    band = int(args.get("band", 1))
    if band < 1 or band > layer.bandCount():
        return {"_error": f"Band {band} is outside the raster's 1..{layer.bandCount()} range.",
                "_code": "INVALID_ARGS", "_suggestion": "Pass an available raster band."}
    provider = layer.dataProvider()
    window, why = _measured_window(layer)
    try:
        from qgis.core import QgsRasterBandStats, QgsRectangle
        extent = QgsRectangle() if window is None else window
        stats = provider.bandStatistics(band, enum_member(QgsRasterBandStats, "Stats", "All"), extent, 250000)
    except Exception:  # nosec B110 - provider detail is optional
        try:
            stats = provider.bandStatistics(band)
        except Exception as e:
            return {"_error": f"Band statistics failed: {e}"}
    out = {
        "layer": layer.name(),
        "band": band,
        "band_count": layer.bandCount(),
        "crs": layer.crs().authid(),
        "min": stats.minimumValue,
        "max": stats.maximumValue,
        "mean": stats.mean,
        "stddev": stats.stdDev,
        "range": stats.range,
        "sum": stats.sum,
    }
    counted = int(getattr(stats, "elementCount", 0) or 0)
    out["pixels_counted"] = counted





    read = int(getattr(stats, "width", 0) or 0) * int(getattr(stats, "height", 0) or 0)
    if read <= 0:
        read = int(layer.width() or 0) * int(layer.height() or 0)
    coverage = round(100.0 * counted / read, 1) if read > 0 and counted <= read else None
    if coverage is not None:
        out["coverage_pct"] = coverage
    full = int(layer.width() or 0) * int(layer.height() or 0)
    if full > read > 0:



        out["sum_is_sample"] = True
        out["sum_note"] = (f"Read on {read:,} of the raster's {full:,} pixels: sum is not its total. "
                           "native:zonalstatisticsfb over the area reads every pixel.")


    if counted == 0 or not math.isfinite(float(stats.mean)):
        out["note"] = ("No valid pixel: every cell of this band is nodata. The raster was written empty "
                       "(wrong extent or CRS at creation); do not reproject it, recreate it.")
    elif coverage is not None and coverage < COVERAGE_FULL_PCT:
        out["note"] = (f"{coverage}% of the cells carry a value; the rest is nodata, from a clip, a cloud "
                       "mask, or the edge of the source. Every number above describes those cells only. "
                       "Two rasters of the same area whose coverage differs were not measured on the same "
                       "pixels: say so rather than reading the difference as change on the ground.")
    if window is not None:
        out["measured_over"] = _extent_block(window, layer.crs())
        full_extent = layer.extent()
        if not full_extent.isNull() and full_extent.width() > 0 and full_extent.height() > 0:
            out["measured_over"]["share_of_raster_pct"] = round(
                100.0 * (window.width() * window.height()) / (full_extent.width() * full_extent.height()), 2)
        out["window_note"] = (f"{why}. Every number above describes that window only, not the whole raster: "
                              "give them as the window's, or clip the raster first "
                              "(gdal:cliprasterbyextent over the area) and measure the clip.")
    elif read <= 0:


        out["sum_is_sample"] = True
        out["sum_note"] = (f"Read on {counted:,} cells; how many the band holds could not be read here, so "
                           "sum is the sum of those cells and not the band's total.")


    grid = ground.pixel_facts(layer)
    if grid:
        out["grid"] = grid
    if args.get("class_counts"):
        out.update(_class_counts(layer, provider, band, window))
    return out





_CLASS_COUNT_PIXELS = 4_000_000
_CLASS_COUNT_MAX_CLASSES = 256


def _window_pixels(dataset, window) -> tuple:
    """``(xoff, yoff, xsize, ysize)`` of *window* in the dataset's own grid, clamped to it."""




    width, height = dataset.RasterXSize, dataset.RasterYSize
    if window is None:
        return 0, 0, width, height
    try:
        origin_x, step_x, _, origin_y, _, step_y = dataset.GetGeoTransform()
        if not step_x or not step_y:
            return 0, 0, width, height
        left = int(max(0, min(width - 1, (window.xMinimum() - origin_x) / step_x)))
        right = int(max(1, min(width, math.ceil((window.xMaximum() - origin_x) / step_x))))
        top = int(max(0, min(height - 1, (window.yMaximum() - origin_y) / step_y)))
        bottom = int(max(1, min(height, math.ceil((window.yMinimum() - origin_y) / step_y))))
    except Exception:  # noqa: BLE001 - a dataset that cannot place the window is read whole
        return 0, 0, width, height
    if right <= left or bottom <= top:
        return 0, 0, width, height
    return left, top, right - left, bottom - top


def _class_counts(layer, provider, band: int, window=None) -> dict:
    """Share and area per distinct value of a categorical band."""






    try:
        import numpy as np
        from osgeo import gdal
    except Exception as exc:  # nosec B110 - the table is optional
        return {"class_counts_note": f"GDAL or numpy unavailable: {exc}"}
    source = provider.dataSourceUri()
    try:



        dataset = gdal.Open(source) if source else None
    except RuntimeError:
        dataset = None
    if dataset is None:
        return {"class_counts_note": "the source is not a GDAL raster (a tile service or a WMS has no class table)"}
    try:
        xoff, yoff, width, height = _window_pixels(dataset, window)
        scale = max(1.0, math.sqrt(width * height / float(_CLASS_COUNT_PIXELS)))
        buf_w, buf_h = max(1, int(width / scale)), max(1, int(height / scale))
        raster_band = dataset.GetRasterBand(band)
        array = raster_band.ReadAsArray(xoff, yoff, width, height, buf_xsize=buf_w, buf_ysize=buf_h)
        nodata = raster_band.GetNoDataValue()
    except Exception as exc:  # nosec B110 - a read failure is reported, not raised
        return {"class_counts_note": f"could not read the band: {exc}"}
    finally:
        del dataset
    if array is None:
        return {"class_counts_note": "could not read the band"}
    flat = array.ravel()
    valid = np.ones(flat.shape, dtype=bool)
    if np.issubdtype(flat.dtype, np.floating):
        valid &= np.isfinite(flat)
    if nodata is not None:
        valid &= flat != nodata
    values, counts = np.unique(flat[valid], return_counts=True)
    sampled = int(valid.size)
    out = {"pixels_sampled": sampled,
           "nodata_share_pct": round(100.0 * (1 - valid.sum() / sampled), 2) if sampled else None}
    if scale > 1.0:
        out["class_counts_note"] = (f"read decimated by {scale:.1f} in each direction; shares are exact to a "
                                    "fraction of a percent")
    if len(values) > _CLASS_COUNT_MAX_CLASSES:
        out["class_counts_note"] = (f"continuous band: {len(values)} distinct values in the sample, no class table; "
                                    "threshold it first (raster_calculator) and count the classes of the result")
        return out
    total = int(counts.sum()) or 1
    factor = (width * height) / float(buf_w * buf_h)
    pixel_area = ground.pixel_facts(layer).get("pixel_area_m2")
    classes = []
    for value, count in zip(values.tolist(), counts.tolist()):
        row = {"value": value, "share_pct": round(100.0 * count / total, 2), "pixels": int(round(count * factor))}
        if pixel_area:
            row["area_km2"] = round(count * factor * pixel_area / 1e6, 3)
        classes.append(row)
    classes.sort(key=lambda row: -row["share_pct"])
    out["classes"] = classes
    if window is None:
        out["classes_order"] = "largest share first; share_pct is of the valid pixels"
    else:
        out["classes_order"] = ("largest share first; share_pct is of the valid pixels inside measured_over, "
                                "not of the whole raster, and the areas are that window's")
    return out


def _get_provider_capabilities(args: dict) -> dict:
    from qgis.core import QgsVectorDataProvider

    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])
    if not isinstance(layer, QgsVectorLayer):
        return _not_a_vector_error(layer)

    provider = layer.dataProvider()
    caps = provider.capabilities()
    names = []
    for cap in (
        "AddFeatures", "DeleteFeatures", "ChangeAttributeValues", "ChangeGeometries",
        "AddAttributes", "DeleteAttributes", "RenameAttributes", "CreateSpatialIndex",
        "SelectAtId", "TransactionSupport",
    ):
        flag = getattr(QgsVectorDataProvider, cap, None)
        try:
            if flag is not None and (caps & flag):
                names.append(cap)
        except Exception:  # nosec B112 - geometry fetch hint is optional
            continue
    storage = None
    try:
        storage = provider.storageType()
    except Exception:  # nosec B110 - provider detail is optional
        pass
    result = {
        "layer": layer.name(),
        "editable": layer.isEditable(),
        "read_only": layer.readOnly(),
        "storage": storage,
        "capabilities": names,
    }




    from . import vector_write

    facts = vector_write.storage_facts(layer)
    result["path"] = facts["path"]
    result["field_name_limit"] = vector_write.field_name_limit(layer)
    blocked = vector_write.cannot_edit_error(layer, "edit") if not layer.isEditable() else None
    if blocked is not None and blocked.get("reason") != "start_editing_refused":
        result["writable"] = False
        result["not_writable_reason"] = blocked.get("reason")
        result["not_writable_detail"] = blocked.get("_error")
        result["not_writable_fix"] = blocked.get("suggestion")
    else:
        result["writable"] = bool(names)
    if result["field_name_limit"]:
        result["note"] = (f"This format keeps field names to {result['field_name_limit']} characters of plain "
                          "ASCII; longer names are shortened when they are written.")
    return result


def _check_geometry_validity(args: dict) -> dict:
    name = args.get("layer_name")
    if not name:


        from qgis.utils import iface
        active = iface.activeLayer() if iface else None
        if not isinstance(active, QgsVectorLayer):
            return {"_error": "No layer named and no vector layer is active.",
                    "_code": "INVALID_ARGS",
                    "suggestion": "Pass layer_name; list_layers shows the names."}
        layer, name = active, active.name()
    else:
        layer = _find_layer(name)
    if not layer:
        return _layer_not_found_error(name)
    if not isinstance(layer, QgsVectorLayer):
        return {"_error": f"Layer '{name}' is not a vector layer"}

    limit = min(max(int(args.get("limit", 50) or 50), 1), 500)
    max_checked = 100_000




    budget = VertexBudget()
    max_vertices = budget.per_geometry



    total = loaded_feature_count(layer, layer.featureCount())
    request = QgsFeatureRequest()
    try:
        request.setSubsetOfAttributes([])
    except Exception:  # nosec B110 - geometry fetch hint is optional
        pass

    checked = 0
    invalid = []
    too_large = []
    stopped = None
    complete = False
    for feat in layer.getFeatures(request):
        if checked >= max_checked:
            break
        stopped = budget.exhausted()
        if stopped:
            break
        checked += 1
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        vertices = budget.oversize(geom)
        if vertices:

            too_large.append({"_fid": feat.id(), "vertices": vertices})
            continue
        try:
            if geom.isGeosValid():
                continue
            reasons = _invalid_reasons(geom)
        except Exception:
            reasons = ["invalid geometry"]
        invalid.append({"_fid": feat.id(), "reasons": reasons or ["invalid geometry"]})
        if len(invalid) >= limit:
            break
    else:
        complete = True


    complete = complete or (total >= 0 and checked >= total)
    out = {
        "layer": layer.name(),
        "checked": checked,
        "total": total,
        "invalid_count": len(invalid),
        "invalid": invalid,
        "all_valid": len(invalid) == 0 and complete and not too_large,
        "complete": complete,
        "features_omitted": max(0, total - checked) if total >= 0 else None,
    }
    if too_large:
        out["unchecked_large"] = too_large[:limit]
        out["unchecked_large_count"] = len(too_large)
        out["note"] = (
            f"{len(too_large)} geometr{'y' if len(too_large) == 1 else 'ies'} over {max_vertices:,} "
            f"vertices were counted but not checked: validating one of that size holds QGIS for minutes. "
            f"Run the Processing algorithm native:checkvalidity (run_processing, async) to check them "
            f"in the background, or fix them with native:fixgeometries."
        )
    if stopped:
        out["stopped"] = stopped
        out["suggestion"] = (
            f"The check stopped after {checked} of {total} features to keep QGIS responsive "
            f"({budget.stop_reason(stopped)} reached). For the whole layer run "
            f"native:checkvalidity through run_processing with async true."
        )
    return out


def _invalid_reasons(geom) -> list:
    """Up to three reasons a geometry is invalid, from GEOS."""






    engine = enum_member(QgsGeometry, "ValidationMethod", "ValidatorGeos", None)
    if engine is None:
        try:
            from qgis.core import Qgis
            engine = enum_member(Qgis, "GeometryValidationEngine", "Geos", None)
        except ImportError:
            engine = None
    reasons = []
    try:
        errors = geom.validateGeometry(engine) if engine is not None else geom.validateGeometry()
    except Exception:  # noqa: BLE001 - the verdict stands without its reasons
        return []
    for err in errors:
        reasons.append(err.what())
        if len(reasons) >= 3:
            break
    return reasons
