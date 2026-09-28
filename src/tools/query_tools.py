# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



from __future__ import annotations

import contextlib
import json
import math
import re
import threading
import time

from qgis.core import (
    QgsExpression,
    QgsFeatureRequest,
    QgsFeedback,
    QgsGeometry,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsVectorLayerFeatureSource,
)

from ..core import background, ground, limits, net
from ..core.crs_ref import crs_ref
from ..core.feature_requests import feature_request, first_feature
from ..core.geometry_budget import VertexBudget
from ..core.logger import log_debug
from ..core.policy import MAX_LIST_CHARS
from ..core.qt_compat import enum_member
from ..core.tool_registry import coded_fact, tool_error
from ..core.vsi import streamed_in_place
from ._layers import loaded_feature_count
from .data_tools import _run_on_main_thread
from .layer_lookup import _field_not_found_error, _find_layer, _jsonable_value, _layer_not_found_error
from .project_tools import _is_remote_vector, filter_facts





STATS_CHUNK = 20_000

















_SCAN_PERMITS = threading.BoundedSemaphore(1)


_SCAN_WAIT_S = 240.0












_SPATIAL_PREDICATES = frozenset({
    "intersects", "within", "contains", "overlaps", "touches", "crosses", "disjoint",
    "overlay_intersects", "overlay_within", "overlay_contains", "overlay_touches",
    "overlay_crosses", "overlay_disjoint", "overlay_equals",
})




_PREDICATE_HINT = {
    "intersects": coded_fact(hint="predicate_intersects"),
    "within": coded_fact(hint="predicate_within"),
    "contains": coded_fact(hint="predicate_contains"),
    "overlaps": coded_fact(hint="predicate_overlaps"),
    "touches": coded_fact(hint="predicate_touches"),
    "crosses": coded_fact(hint="predicate_crosses"),
    "disjoint": coded_fact(hint="predicate_disjoint"),
}



_PREDICATE_ALTERNATIVE = {"intersects": "within", "touches": "within", "overlaps": "within",
                          "crosses": "within", "within": "intersects"}



_COUNT_SCAN_MAX = 200_000
_COUNT_BUDGET_S = 0.3


def _count_matching(layer, expression: str) -> tuple:






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
    except Exception as exc:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
        return counted, False
    finally:
        try:
            iterator.close()
        except Exception:  # nosec B110
            pass
    return counted, True


def _swapped_predicate(expression: str, before: str, after: str):






    swapped, hits = re.subn(rf"\b{before}\s*\(", f"{after}(", str(expression or ""), flags=re.IGNORECASE)
    if not hits:
        return None
    return None if QgsExpression(swapped).hasParserError() else swapped


def _spatial_criterion(layer, expression: str, expr, exact: bool) -> dict:






    try:
        used = _SPATIAL_PREDICATES.intersection(name.lower() for name in expr.referencedFunctions())
    except Exception:  # noqa: BLE001
        return {}
    if not used:
        return {}
    predicate = next((name for name in ("within", "intersects", "contains", "overlaps",
                                        "touches", "crosses", "disjoint") if name in used), min(used))
    criterion = {"predicate": predicate, "counts": predicate,
                 **_PREDICATE_HINT.get(predicate, {})}
    other = _PREDICATE_ALTERNATIVE.get(predicate) if len(used) == 1 else None
    if other and exact:
        swapped = _swapped_predicate(expression, predicate, other)
        if swapped:
            alternative, alternative_exact = _count_matching(layer, swapped)
            if alternative_exact:
                criterion["under_" + other] = alternative
                criterion["under_" + other + "_counts"] = other
                other_fact = _PREDICATE_HINT.get(other, {})
                if other_fact.get("hint"):
                    criterion["under_" + other + "_hint"] = other_fact["hint"]
    criterion["note"] = ("A spatial count is only a number once the rule is named. The figure and the rule "
                         "name go together; the other number can differ.")
    return {"criterion": criterion}


def _get_features_is_remote(layer_args: dict) -> bool:





    layer = _find_layer(str((layer_args or {}).get("layer_name") or ""))
    return isinstance(layer, QgsVectorLayer) and _is_remote_vector(layer)


def _features_plan(args: dict, for_worker: bool = False) -> dict:






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
    expr = None




    if expression and streamed_in_place(layer):
        return {
            "_error": (f"'{layer.name()}' is read over HTTP where it is published, so filtering it "
                       "scans the whole file across the network instead of reading its index."),
            "_code": "INVALID_ARGS",
            "_suggestion": ("A local clip of the area filters fast; reading a page without an "
                            "expression stays fast."),
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

    plan = {"layer": layer, "layer_name": layer.name(), "names": names, "missing": missing,
            "limit": limit, "offset": offset, "include_geometry": include_geometry,
            "native_offset": native_offset, "request": request, "expression": expression, "expr": expr,
            "all_fields": [f.name() for f in layer.fields()], "geometry_crs": crs_ref(layer.crs()),
            "filter_facts": filter_facts(layer)}
    if not for_worker:
        return plan
    needed = list(names)
    needs_geometry = include_geometry
    if expr is not None:
        columns = list(expr.referencedColumns())
        needed = None if "*" in columns else needed + columns
        needs_geometry = needs_geometry or expr.needsGeometry()
        plan["count_request"] = _count_request(layer, expression, request.expressionContext(), expr)
    if needed is not None:
        request.setSubsetOfAttributes(sorted(set(needed)), layer.fields())
    if not needs_geometry:
        request.setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry"))
    feedback = QgsFeedback()
    if hasattr(request, "setFeedback"):
        request.setFeedback(feedback)
    plan.update(source=QgsVectorLayerFeatureSource(layer), feedback=feedback)
    plan.pop("layer")
    return plan


def _count_request(layer, expression: str, context, expr):

    request = QgsFeatureRequest()
    request.setFilterExpression(expression)
    request.setExpressionContext(context)
    columns = list(expr.referencedColumns())
    if "*" not in columns:
        request.setSubsetOfAttributes(columns, layer.fields())
    if not expr.needsGeometry():
        request.setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry"))
    request.setLimit(_COUNT_SCAN_MAX)
    return request


def _page_rows(features, plan: dict, halted=None) -> tuple:

    collected = []
    size = 0
    over_budget = False
    for i, feat in enumerate(features):
        if halted is not None and halted.is_set():
            break
        if not plan["native_offset"] and i < plan["offset"]:
            continue
        row = {"_fid": feat.id()}
        for name in plan["names"]:
            row[name] = feat[name]
        if plan["include_geometry"]:
            geom = feat.geometry()
            row["_wkt"] = None if (geom is None or geom.isNull()) else geom.asWkt(6)




        size += len(json.dumps(row, default=str))
        if collected and size > MAX_LIST_CHARS:
            over_budget = True
            break
        collected.append(row)
    return collected, over_budget


def _features_result(plan: dict, collected: list, over_budget: bool, total, count, criterion: dict) -> dict:

    limit, offset = plan["limit"], plan["offset"]
    fetched = len(collected)
    features = collected[:limit]
    result = {"layer": plan["layer_name"], "count": len(features), "offset": offset, "features": features}





    if isinstance(total, int) and total >= 0:
        result["layer_feature_count"] = total
    result.update(plan.get("filter_facts") or {})
    if count is not None:
        matched, exact = count
        result["matched" if exact else "matched_at_least"] = matched
        result["count_is"] = ("rows in this page; 'matched' is how many features the expression selects in "
                              "the whole layer")
        result.update(criterion)
    if plan["missing"]:
        result["fields_not_found"] = plan["missing"]
        result["fields"] = plan["all_fields"]
    if plan["include_geometry"]:
        result["geometry_crs"] = plan["geometry_crs"]
    if over_budget or fetched > limit:
        result["next_offset"] = offset + len(features)
        result["more_available"] = True
    if over_budget:
        result["page_limited_by"] = (
            f"{MAX_LIST_CHARS} characters. Fewer fields, a filter or "
            "the next page with offset fit."
        )
    return result


def _get_features(args: dict) -> dict:
    if not background.on_main_thread():

        return _get_features_off_thread(args)
    plan = _features_plan(args)
    if "_error" in plan:
        return plan
    layer = plan["layer"]
    collected, over_budget = _page_rows(layer.getFeatures(plan["request"]), plan)
    count = criterion = None
    if plan["expression"]:
        count = _count_matching(layer, plan["expression"])
        criterion = _spatial_criterion(layer, plan["expression"], plan["expr"], count[1])
    return _features_result(plan, collected, over_budget, loaded_feature_count(layer, layer.featureCount()),
                            count, criterion or {})










_STOP_POLL_S = 0.05

_REMOTE_COUNT_BUDGET_S = 20.0


def _get_features_off_thread(args: dict) -> dict:
    cancelled = net.current_cancel_check()
    plan = _run_on_main_thread(_features_plan, args, True, timeout=60)
    if "_error" in plan:
        return plan
    out: dict = {}
    halted, over, failure = threading.Event(), threading.Event(), []

    def reader(source, request, count_request, feedback):
        try:
            with background.on_any_failure(failure.append):
                features = source.getFeatures(request)
                try:
                    out["page"] = _page_rows(features, plan, halted)
                finally:
                    features.close()
                if count_request is not None and not halted.is_set():
                    out["count"] = _count_source(source, count_request, halted)
        finally:
            over.set()

    thread = threading.Thread(target=reader, name="get_features read",
                              args=(plan.pop("source"), plan["request"], plan.get("count_request"),
                                    plan["feedback"]))
    thread.start()
    while not over.wait(_STOP_POLL_S):
        if callable(cancelled) and cancelled():
            halted.set()
            plan["feedback"].cancel()
            return tool_error("Stopped while the features were read from the service.", "CANCELLED",
                              "Nothing was changed.")
    if failure:
        raise failure[0]
    collected, over_budget = out.get("page", ([], False))

    criterion = _spatial_criterion(None, plan["expression"], plan["expr"], False) if plan["expression"] else {}
    return _features_result(plan, collected, over_budget, None, out.get("count"), criterion)


def _count_source(source, request, halted) -> tuple:

    deadline = time.monotonic() + _REMOTE_COUNT_BUDGET_S
    counted = 0
    features = source.getFeatures(request)
    try:
        for counted, _feature in enumerate(features, start=1):
            if halted.is_set() or counted >= _COUNT_SCAN_MAX or time.monotonic() > deadline:
                return counted, False
    finally:
        features.close()
    return counted, True











_MEASURE_FUNCTIONS = frozenset({"$area", "$perimeter", "$length"})


def _measurement(expr, layer) -> dict:






    try:
        used = _MEASURE_FUNCTIONS.intersection(expr.referencedFunctions())
    except Exception:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
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
                    "_suggestion": "Without feature_id it aggregates over the layer; get_features gives "
                                   "the ids."}
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
            "_note": "Expression references geometry/fields, so it is evaluated per feature. feature_id "
            "picks one feature; an aggregate (sum/mean/count) gives one layer-wide value.",
        }


    value = expr.evaluate(ctx)
    if expr.hasEvalError():
        return {"_error": f"Expression evaluation error: {expr.evalErrorString()}", "_code": "INVALID_ARGS"}
    out = {"expression": expression, "result": _jsonable_value(value), **measurement}
    if value is None and needs_feature and not is_vector:
        out["_note"] = (
            "Result is null: the expression needs a feature but no vector layer_name was given. "
            "layer_name (and feature_id) supplies one."
        )
    return out


def _needs_feature(expr) -> bool:









    try:
        root = expr.rootNode()
        if root is not None:
            return _node_needs_feature(root)
    except Exception:  # noqa: BLE001  # nosec B110
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

















    if not _SCAN_PERMITS.acquire(timeout=_SCAN_WAIT_S):
        return {"_error": ("Another field is still being scanned; this one waited "
                           f"{_SCAN_WAIT_S:.0f} seconds for its turn."),
                "_code": "INVALID_ARGS",
                "_suggestion": "One field at a time fits; get_features reads the column."}
    try:
        return _scan_field(args)
    finally:
        _SCAN_PERMITS.release()


def _scan_field(args: dict) -> dict:

    state = _run_on_main_thread(_stats_open, args["layer_name"], args.get("field"), timeout=60)
    if state.get("_error"):
        return state



    remote = state.pop("source", None)
    if remote is not None:
        state["features"] = remote.getFeatures(state["request"])
    try:
        while True:
            cancelled = net.current_cancel_check()
            try:
                stopped = callable(cancelled) and bool(cancelled())
            except Exception:  # noqa: BLE001
                stopped = False
            if stopped:
                return {"_error": "The run was stopped.", "_code": "CANCELLED",
                        "_suggestion": "The user stopped the run."}
            if remote is not None:
                if _stats_read(state):
                    break
                background.breathe(state["total"])
            elif _run_on_main_thread(_stats_read, state, timeout=120):
                break
    finally:
        if remote is not None:
            _stats_close(state)
        else:
            _run_on_main_thread(_stats_close, state, timeout=30)

    return {**_stats_summary(state), **state["filter_facts"]}


def _band_number(field: str) -> int:

    match = re.search(r"(\d+)", str(field or ""))
    return int(match.group(1)) if match else 1


def _not_a_vector_error(layer, field: str = "") -> dict:







    name = layer.name()
    if isinstance(layer, QgsRasterLayer):
        band = _band_number(field)
        return {
            "_error": (f"Layer {name!r} is a raster, and this tool reads attribute columns of a vector layer. "
                       "A raster has no attribute table, it has bands."),
            "code": "INVALID_ARGS",
            "suggestion": (f"get_raster_band_stats with layer_name={name!r} and band={band} gives the min, "
                           "max, mean and standard deviation of its pixels."),
            "band": band,
        }
    kind = type(layer).__name__.replace("Qgs", "").replace("Layer", "").lower() or "other"
    return {
        "_error": f"Layer {name!r} is a {kind} layer, and this tool reads the attribute columns of a vector layer.",
        "code": "INVALID_ARGS",
        "suggestion": "list_layers shows which layers in this project are vector.",
    }


def _stats_open(layer_name: str, field: str) -> dict:

    layer = _find_layer(layer_name)
    if not layer:
        return _layer_not_found_error(layer_name)
    if not isinstance(layer, QgsVectorLayer):
        return _not_a_vector_error(layer, field)
    if not field:
        return {"_error": f"Which field of {layer.name()!r}? This tool summarises one attribute column.",
                "code": "INVALID_ARGS",
                "fields": [f.name() for f in layer.fields()],
                "suggestion": "'fields' lists the names 'field' accepts."}
    index = layer.fields().indexOf(field)
    if index < 0:



        return _field_not_found_error(layer, field)
    request = QgsFeatureRequest().setFlags(QgsFeatureRequest.Flag.NoGeometry)
    request.setSubsetOfAttributes([index])
    state = {"layer_name": layer.name(), "field": field, "index": index, "numbers": [], "others": [],
             "total": 0, "missing": 0, "numeric_only": True, "filter_facts": filter_facts(layer)}
    if _is_remote_vector(layer):

        state.update(source=QgsVectorLayerFeatureSource(layer), request=request)
    else:
        state["features"] = layer.getFeatures(request)
    return state


def _stats_read(state: dict) -> bool:






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

    features = state.pop("features", None)
    if features is not None:
        try:
            features.close()
        except Exception:  # nosec B110
            pass


def _stats_summary(state: dict) -> dict:









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

    return (f"'count' is {split['features']} rows of this layer, one per geometry. "
            f"{split['smallest']} of them together carry {split['smallest_share_of_sum_pct']}% of the sum "
            f"of {field!r} (none above {split['smallest_largest_value']:.4g}), and the other "
            f"{split['rest']} carry "
            f"{split['rest_share_of_sum_pct']}%. If this layer came out of a clip, an intersection or an "
            f"overlay, those small rows are boundary fragments of features that are mostly outside it. "
            f"The count depends on the rule: touching the area, lying entirely inside it, or having its "
            f"centroid inside it are three different numbers.")


def _stat_text(value) -> str:








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


def _symbol_info(symbol, budget: dict, depth: int = 0):

    if symbol is None:
        return None
    if budget["symbols"] <= 0 or depth > 3:
        return {"details_omitted": True}
    budget["symbols"] -= 1
    from qgis.core import QgsUnitTypes

    color = symbol.color()
    info = {"color": color.name(), "color_alpha": color.alphaF(), "opacity": symbol.opacity(),
            "data_defined": bool(symbol.hasDataDefinedProperties())}
    for name in ("width", "size", "angle"):
        getter = getattr(symbol, name, None)
        if callable(getter):
            info[name] = getter()
    for name in ("outputUnit", "widthUnit", "sizeUnit"):
        getter = getattr(symbol, name, None)
        if callable(getter):
            info[name] = QgsUnitTypes.encodeUnit(getter())
    layers = []
    total = symbol.symbolLayerCount()
    for idx in range(min(total, 8)):
        if budget["layers"] <= 0:
            break
        budget["layers"] -= 1
        part = symbol.symbolLayer(idx)
        raw = part.properties()
        properties = {}
        for key in list(raw)[:40]:
            value = str(raw[key])[:500]
            cost = len(key) + len(value)
            if cost > budget["property_chars"]:
                break
            budget["property_chars"] -= cost
            properties[key] = value
        keys = list(properties)
        entry = {"type": part.layerType(), "properties": properties,
                 "data_defined": bool(part.hasDataDefinedProperties()), "enabled": part.enabled()}
        if len(keys) < len(raw):
            entry["properties_omitted"] = len(raw) - len(keys)
        truncated = [key for key in keys if len(str(raw[key])) > 500]
        if truncated:
            entry["properties_truncated"] = truncated
        child = part.subSymbol()
        if child is not None:
            entry["sub_symbol"] = _symbol_info(child, budget, depth + 1)
        layers.append(entry)
    info["symbol_layers"] = layers
    if len(layers) < total:
        info["symbol_layers_omitted"] = total - len(layers)
    return info


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
        except Exception:  # nosec B112
            return None

    rtype = renderer.type()
    info = {"type": rtype, "opacity": layer.opacity()}
    budget = {"symbols": 16, "layers": 64, "property_chars": 12000}
    try:
        if rtype in ("categorizedSymbol", "graduatedSymbol"):
            info.update(_class_page(renderer, rtype, args, _color, budget))
        elif rtype == "singleSymbol":
            info["color"] = _color(renderer.symbol())
            info["symbol"] = _symbol_info(renderer.symbol(), budget)
        elif rtype == "embeddedSymbol":
            info["default_symbol"] = _symbol_info(renderer.defaultSymbol(), budget)
            info["feature_symbols"] = "provider_defined_not_inspected"
            info["copy_note"] = (
                "The default symbol is only a fallback. This renderer can draw a different embedded symbol "
                "for each source feature. Copying the renderer or this fallback alone does not guarantee "
                "the same appearance on another provider or layer.")
        elif rtype == "RuleRenderer":
            stack = [(renderer.rootRule(), None)]
            rules = []
            while stack and len(rules) < 128:
                rule, parent_index = stack.pop()
                index = len(rules)
                rules.append({"parent": parent_index, "label": rule.label(), "filter": rule.filterExpression(),
                              "else": rule.isElse(), "active": rule.active(),
                              "minimum_scale": rule.minimumScale(), "maximum_scale": rule.maximumScale(),
                              "symbol": _symbol_info(rule.symbol(), budget)})
                stack.extend((child, index) for child in reversed(rule.children()))
            info["rules"] = rules
            if stack:
                info["rules_truncated"] = True
        else:
            info["inspection_note"] = (
                "This renderer has no complete property inspector here; save_style_qml gives its native "
                "style, which type and opacity alone do not reconstruct.")
    except Exception as e:
        info["_warning"] = f"Partial renderer introspection: {e}"
    return {"layer": layer.name(), "renderer": info}





_CLASS_PAGE = 100


def _class_page(renderer, rtype: str, args: dict, colour_of, budget: dict) -> dict:







    categorized = rtype == "categorizedSymbol"
    items = list(renderer.categories() if categorized else renderer.ranges())
    out: dict = {"field": renderer.classAttribute(), "class_count": len(items)}
    ramp = renderer.sourceColorRamp() if hasattr(renderer, "sourceColorRamp") else None
    if ramp is not None:
        described = {"type": ramp.type()}
        with contextlib.suppress(Exception):
            described["from"] = ramp.color1().name()
            described["to"] = ramp.color2().name()
        out["ramp"] = described
    colours = [colour_of(item.symbol()) for item in items]
    out["distinct_colors"] = len({c for c in colours if c})
    wanted = str(args.get("filter") or "").strip().lower()
    rows = list(zip(items, colours))
    if wanted:
        rows = [(item, colour) for item, colour in rows
                if wanted in (str(item.value()) if categorized else f"{item.lowerValue()} {item.upperValue()}").lower()
                or wanted in str(item.label()).lower()]
        out["filter"] = wanted
        out["classes_matched"] = len(rows)
    try:
        offset = max(0, int(args.get("offset") or 0))
        limit = max(1, int(args.get("limit") or _CLASS_PAGE))
    except (TypeError, ValueError):
        offset, limit = 0, _CLASS_PAGE
    page = rows[offset:offset + limit]
    listed = []
    for position, (item, colour) in enumerate(page):
        if categorized:
            row = {"value": _jsonable_value(item.value()), "label": item.label(), "color": colour}
        else:
            row = {"lower": item.lowerValue(), "upper": item.upperValue(), "label": item.label(), "color": colour}
        if position < 16:
            row["symbol"] = _symbol_info(item.symbol(), budget)
            row["enabled"] = item.renderState()
        listed.append(row)
    out["categories" if categorized else "ranges"] = listed
    if offset:
        out["offset"] = offset
    if len(page) > 16:
        out["symbol_details_omitted"] = len(page) - 16
    if offset + len(page) < len(rows):
        out["next_offset"] = offset + len(page)
        out["classes_not_listed"] = len(rows) - len(page)
    return out


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
                    "_suggestion": "An EPSG code such as EPSG:4326 is valid."}
        if src.isValid() and src != layer.crs():
            try:
                point = QgsCoordinateTransform(src, layer.crs(), QgsProject.instance()).transform(point)
            except Exception as e:
                return {"_error": f"Coordinate transform failed: {e}"}

    band = int(args.get("band", 1))
    if band < 1 or band > layer.bandCount():
        return {"_error": f"Band {band} is outside the raster's 1..{layer.bandCount()} range.",
                "_code": "INVALID_ARGS", "_suggestion": "Bands are numbered from 1."}
    try:
        value, ok = layer.dataProvider().sample(point, band)
    except Exception as e:
        return {"_error": f"Sampling failed: {e}"}
    if not ok:
        return {"_error": "No data at this location (outside extent or nodata)", "_code": "INVALID_ARGS"}



    out = {"layer": layer.name(), "band": band, "value": value, "x": point.x(), "y": point.y(),
           "crs": crs_ref(layer.crs()), "coordinates_are": "the sampled point in the layer's own CRS"}
    units = _crs_units(layer.crs())
    if units:
        out["crs_units"] = units
    if crs_id and str(crs_id).strip().upper() != str(layer.crs().authid()).upper():
        out["asked_in_crs"] = str(crs_id)
    return out


def _crs_units(crs) -> str:

    try:
        from qgis.core import QgsUnitTypes

        return str(QgsUnitTypes.toString(crs.mapUnits()))
    except Exception:  # noqa: BLE001
        return ""


def _extent_block(rect, crs) -> dict:








    block = {"xmin": rect.xMinimum(), "ymin": rect.yMinimum(),
             "xmax": rect.xMaximum(), "ymax": rect.yMaximum(),
             "crs": crs_ref(crs)}
    units = _crs_units(crs)
    if units:
        block["units"] = units
        block["width"] = rect.width()
        block["height"] = rect.height()
    return block


def _remote_raster(layer) -> bool:

    if streamed_in_place(layer):
        return True
    try:
        source = str(layer.source() or "")
        provider = str(layer.providerType() or "").lower()
    except Exception:  # noqa: BLE001
        return False
    return source.startswith(("http://", "https://")) or provider in ("wms", "wcs", "arcgismapserver")











_REMOTE_STATS_PIXELS_MAX = 60_000_000


def _canvas_window(layer, extent):

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
    except Exception:  # noqa: BLE001
        return None


def _measured_window(layer) -> tuple:





    from qgis.core import QgsRectangle

    try:
        full = int(layer.width() or 0) * int(layer.height() or 0)
        px = abs(float(layer.rasterUnitsPerPixelX() or 0))
        py = abs(float(layer.rasterUnitsPerPixelY() or 0))
        extent = QgsRectangle(layer.extent())
    except Exception:  # noqa: BLE001
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




_EXACT_STATS_PIXELS_MAX = 250_000_000




_PICTURE_PROVIDERS = ("wms", "arcgismapserver")


def _coverage_note(out: dict, counted: int, mean: float, coverage, provider: str = "gdal") -> None:


    if counted == 0 and provider in _PICTURE_PROVIDERS:
        out["note"] = ("No pixel values to count: this layer is a map service that serves a picture, not the "
                       "values behind it. That says nothing about its content: the layer is not empty, keep it. "
                       "For values, find the service's data download or its WCS / feature layer.")
    elif counted == 0:
        out["note"] = ("No valid pixel: every cell of this band is nodata. The raster was written empty "
                       "(wrong extent or CRS at creation); only recreating it fixes that.")
    elif not math.isfinite(mean):


        for key in ("mean", "sum"):
            value = out.get(key)
            if isinstance(value, float) and not math.isfinite(value):
                out.pop(key)
        out["note"] = (f"{counted:,} cells carry a value, but some hold NaN that the band does not declare as "
                       "nodata, so the mean could not be computed here; min, max and the count stand and the "
                       "raster is not empty. native:zonalstatisticsfb over the area gives the mean of the valid cells.")
    elif coverage is not None and coverage < COVERAGE_FULL_PCT:
        out["note"] = (f"{coverage}% of the cells carry a value; the rest is nodata, from a clip, a cloud "
                       "mask, or the edge of the source. Every number above describes those cells only. "
                       "Two rasters of the same area whose coverage differs were not measured on the same "
                       "pixels; the difference is not necessarily change on the ground.")
    else:
        out.pop("note", None)


def _exact_band_stats(source: str, band: int, cancelled) -> dict | None:





    try:
        from osgeo import gdal
    except ImportError:
        return None
    gdal.SetThreadLocalConfigOption("GDAL_PAM_ENABLED", "NO")
    try:
        dataset = gdal.OpenEx(source, gdal.OF_RASTER | gdal.OF_READONLY)
        if dataset is None or band > dataset.RasterCount:
            return None
        raster_band = dataset.GetRasterBand(band)
        lo, hi, mean, std = raster_band.ComputeStatistics(
            False, callback=lambda *_: 0 if cancelled is not None and cancelled() else 1)
        valid = raster_band.GetMetadataItem("STATISTICS_VALID_PERCENT")
        cells = dataset.RasterXSize * dataset.RasterYSize
        scale, offset = raster_band.GetScale() or 1.0, raster_band.GetOffset() or 0.0
    except (RuntimeError, TypeError, ValueError):
        return None
    finally:
        gdal.SetThreadLocalConfigOption("GDAL_PAM_ENABLED", None)
    if valid is None:
        return None
    lo, hi = sorted((lo * scale + offset, hi * scale + offset))
    counted = int(round(float(valid) / 100.0 * cells))
    mean = mean * scale + offset
    return {"min": lo, "max": hi, "mean": mean, "stddev": std * abs(scale), "range": hi - lo,
            "sum": mean * counted, "pixels_counted": counted, "coverage_pct": round(float(valid), 1)}




_QUANTILE_PIXELS = 4_000_000



_PERCENTILES = (5, 10, 20, 25, 30, 40, 50, 60, 70, 75, 80, 90, 95)


class _Bounds:


    def __init__(self, xmin: float, ymin: float, xmax: float, ymax: float):
        self._box = (xmin, ymin, xmax, ymax)

    def xMinimum(self) -> float:  # noqa: N802
        return self._box[0]

    def yMinimum(self) -> float:  # noqa: N802
        return self._box[1]

    def xMaximum(self) -> float:  # noqa: N802
        return self._box[2]

    def yMaximum(self) -> float:  # noqa: N802
        return self._box[3]


def _band_quantiles(read: dict, cancelled) -> dict:





    try:
        import numpy as np
        from osgeo import gdal
    except ImportError:
        return {}
    gdal.SetThreadLocalConfigOption("GDAL_PAM_ENABLED", "NO")
    dataset = None
    try:
        dataset = gdal.OpenEx(read["source"], gdal.OF_RASTER | gdal.OF_READONLY)
        if dataset is None or read["band"] > dataset.RasterCount:
            return {}
        window = _Bounds(*read["window"]) if read.get("window") else None
        xoff, yoff, width, height = _window_pixels(dataset, window)
        step = max(1.0, math.sqrt(width * height / float(_QUANTILE_PIXELS)))
        buf_w, buf_h = max(1, int(width / step)), max(1, int(height / step))
        raster_band = dataset.GetRasterBand(read["band"])
        array = raster_band.ReadAsArray(xoff, yoff, width, height, buf_xsize=buf_w, buf_ysize=buf_h)
        nodata = raster_band.GetNoDataValue() if read.get("source_nodata", True) else None
        scale, offset = raster_band.GetScale() or 1.0, raster_band.GetOffset() or 0.0
    except (RuntimeError, TypeError, ValueError, AttributeError):
        return {}
    finally:
        dataset = None
        gdal.SetThreadLocalConfigOption("GDAL_PAM_ENABLED", None)
    if array is None or (cancelled is not None and cancelled()):
        return {}
    flat = array.ravel()
    valid = np.ones(flat.shape, dtype=bool)
    if np.issubdtype(flat.dtype, np.floating):
        valid &= np.isfinite(flat)
    if nodata is not None:
        valid &= flat != nodata
    for low, high in read.get("user_nodata") or ():
        valid &= ~((flat >= low) & (flat <= high))
    values = flat[valid].astype("float64") * scale + offset
    if not values.size:
        return {}
    found = np.percentile(values, _PERCENTILES)
    return {"quantiles": {f"p{p}": round(float(v), 4) for p, v in zip(_PERCENTILES, found)},
            "quantiles_note": (f"Percentiles of {int(values.size):,} valid cells"
                               + (f", read decimated by {step:.1f} in each direction" if step > 1.0 else "")
                               + ". Equal-count class breaks: 4 classes p25 p50 p75, 5 classes p20 p40 p60 p80, "
                                 "10 classes every p10; use them as the breaks, no code needed.")}


def _get_raster_band_stats(args: dict) -> dict:
    out = _run_on_main_thread(_band_stats_on_main, args, timeout=limits.current("CALL_MAX_SECONDS_MAIN"))
    source = out.pop("_exact_source", None) if isinstance(out, dict) else None
    quantile_read = out.pop("_quantile_read", None) if isinstance(out, dict) else None
    cancelled = net.current_cancel_check()
    if quantile_read:
        out.update(_band_quantiles(quantile_read, cancelled))
    if not source:
        return out
    exact = _exact_band_stats(source, int(args.get("band", 1)), cancelled)
    if cancelled is not None and cancelled():
        return {"_error": "The run was stopped.", "code": "CANCELLED",
                "suggestion": "The user stopped the run."}
    if exact is None:
        return out
    out.update(exact)
    for key in ("sum_is_sample", "sum_note", "extremes_are_sample", "extremes_note"):
        out.pop(key, None)
    out["measured"] = "every pixel"
    _coverage_note(out, exact["pixels_counted"], exact["mean"], exact["coverage_pct"])
    return out


def _band_stats_on_main(args: dict) -> dict:


    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])
    if not isinstance(layer, QgsRasterLayer):
        return {"_error": f"Layer '{args['layer_name']}' is not a raster layer"}

    band = int(args.get("band", 1))
    if band < 1 or band > layer.bandCount():
        return {"_error": f"Band {band} is outside the raster's 1..{layer.bandCount()} range.",
                "_code": "INVALID_ARGS", "_suggestion": "Bands are numbered from 1."}
    provider = layer.dataProvider()
    window, why = _measured_window(layer)
    try:
        from qgis.core import QgsRectangle

        from ._compat import RASTER_STATS_ALL
        extent = QgsRectangle() if window is None else window


        stats = provider.bandStatistics(band, RASTER_STATS_ALL, extent, 250000)
    except Exception:  # nosec B110
        try:
            stats = provider.bandStatistics(band)
        except Exception as e:
            return {"_error": f"Band statistics failed: {e}"}
    out = {
        "layer": layer.name(),
        "band": band,
        "band_count": layer.bandCount(),
        "crs": crs_ref(layer.crs()),
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




        out["extremes_are_sample"] = True
        out["extremes_note"] = (f"min and max are those of the {read:,} pixels read, not the raster's: its "
                                "highest and lowest cells can lie between them.")
        if (window is None and full <= _EXACT_STATS_PIXELS_MAX and layer.providerType() == "gdal"
                and not _remote_raster(layer) and not provider.userNoDataValues(band)
                and (provider.useSourceNoDataValue(band) or not provider.sourceHasNoDataValue(band))):
            out["_exact_source"] = str(layer.source() or "")
    _coverage_note(out, counted, float(stats.mean), coverage, str(layer.providerType() or "").lower())
    if window is not None:
        out["measured_over"] = _extent_block(window, layer.crs())
        full_extent = layer.extent()
        if not full_extent.isNull() and full_extent.width() > 0 and full_extent.height() > 0:
            out["measured_over"]["share_of_raster_pct"] = round(
                100.0 * (window.width() * window.height()) / (full_extent.width() * full_extent.height()), 2)
        out["window_note"] = (f"{why}. Every number above describes that window only, not the whole raster; "
                              "gdal:cliprasterbyextent over the area gives a clip that measures the whole raster.")
    elif read <= 0:


        out["sum_is_sample"] = True
        out["sum_note"] = (f"Read on {counted:,} cells; how many the band holds could not be read here, so "
                           "sum is the sum of those cells and not the band's total.")


    grid = ground.pixel_facts(layer)
    if grid:
        out["grid"] = grid
    if args.get("class_counts"):
        out.update(_class_counts(layer, provider, band, window))
    if layer.providerType() == "gdal":

        out["_quantile_read"] = {
            "source": str(provider.dataSourceUri() or ""), "band": band,
            "window": (None if window is None else
                       (window.xMinimum(), window.yMinimum(), window.xMaximum(), window.yMaximum())),
            "source_nodata": bool(provider.useSourceNoDataValue(band)),
            "user_nodata": [(r.min(), r.max()) for r in provider.userNoDataValues(band)],
        }
    return out





_CLASS_COUNT_PIXELS = 4_000_000
_CLASS_COUNT_MAX_CLASSES = 256


def _window_pixels(dataset, window) -> tuple:





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
    except Exception:  # noqa: BLE001
        return 0, 0, width, height
    if right <= left or bottom <= top:
        return 0, 0, width, height
    return left, top, right - left, bottom - top


def _class_counts(layer, provider, band: int, window=None) -> dict:







    try:
        import numpy as np
        from osgeo import gdal
    except Exception as exc:  # nosec B110
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
    except Exception as exc:  # nosec B110
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
        except Exception:  # nosec B112
            continue
    storage = None
    try:
        storage = provider.storageType()
    except Exception:  # nosec B110
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
                    "suggestion": "list_layers shows the names for layer_name."}
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
    except Exception:  # nosec B110
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
            f"native:checkvalidity (run_processing, async) checks them in the background; "
            f"native:fixgeometries fixes them."
        )
    if stopped:
        out["stopped"] = stopped
        out["suggestion"] = (
            f"The check stopped after {checked} of {total} features to keep QGIS responsive "
            f"({budget.stop_reason(stopped)} reached). native:checkvalidity through "
            f"run_processing with async true checks the whole layer."
        )
    return out


def _invalid_reasons(geom) -> list:







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
    except Exception:  # noqa: BLE001
        return []
    for err in errors:
        reasons.append(err.what())
        if len(reasons) >= 3:
            break
    return reasons
