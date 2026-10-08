# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





from __future__ import annotations

import contextlib
import math
import os
import threading
import time

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsFeatureRequest,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRasterDataProvider,
    QgsRectangle,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsVectorLayerFeatureSource,
)
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP, QThread, QTimer

from ..core import background, limits, net
from ..core.background import run_on_main_thread
from ..core.crs_ref import crs_ref
from ..core.feature_requests import feature_request
from ..core.invariants import metres_per_map_unit
from ..core.layer_order import feature_count_of, is_remote_vector
from ..core.qt_compat import enum_member
from ..core.serialization import size_budget
from ..core.tool_registry import Tool, ToolRegistry, coded_fact, tool_error
from . import guards, kml_description, vector_write
from ._compat import FIELD_TYPES, QVAR_DOUBLE, WKB_NO_GEOMETRY, is_raster, is_vector, py_value
from ._layers import layer_not_found, resolve_layer
from .danger import sql_mutates

_SQL_ROW_CAP = 1000



_COUNTED_VALUES_MAX = 50
_UNIQUE_VALUES_DEFAULT = 1000
_STOP_POLL_S = 0.05


_CLICK_PX = 101
_CLICK_PIXEL_M = 0.5
_SERVICE_WAIT_S = 30.0


def register_harvest_analysis_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="execute_sql",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Run a SQL query"),
        input_schema={
            "type": "object",
            "properties": {
                "description": {"type": "string", "maxLength": 80},
                "query": {
                    "type": "string",
                },
                "layers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 200,
                },
                "as_layer": {
                    "type": "boolean",
                },
                "layer_name": {
                    "type": "string",
                },
                "geometry_field": {
                    "type": "string",
                },
                "uid_field": {
                    "type": "string",
                },
                "crs": {
                    "type": "string",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": _SQL_ROW_CAP},
            },
            "required": ["query"],
        },
        handler=_execute_sql,
        argument_check=guards.sql_refusal,
        destructive_when=sql_mutates,
    ))

    registry.register(Tool(
        name="field_calculator",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Calculate {field_name} in {layer_name}"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "field_name": {"type": "string"},
                "expression": {
                    "type": "string",
                },
                "field_type": {
                    "type": "string",
                    "enum": ["string", "int", "double", "bool", "date", "datetime"],
                },
                "length": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 65535,
                },
                "precision": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 30,
                },
            },
            "required": ["layer_name", "field_name", "expression"],


            "x-fills-any-size": True,
        },
        handler=_field_calculator,

        background=True,
    ))

    registry.register(Tool(
        name="get_unique_values",
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Unique values of {field} in {layer_name}"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "field": {"type": "string"},
                "limit": {"type": "integer", "minimum": -1, "maximum": 5000},
                "with_counts": {"type": "boolean"},
            },
            "required": ["layer_name", "field"],


            "x-reads-services-off-thread": True,
        },
        handler=_get_unique_values,


        background=_unique_values_off_main,
    ))

    registry.register(Tool(
        name="identify_features",
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Identify features at a point"),
        input_schema={
            "type": "object",
            "properties": {
                "description": {"type": "string", "maxLength": 80},
                "point": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 2,
                    "maxItems": 2,
                },
                "crs": {
                    "type": "string",
                },
                "tolerance": {
                    "type": "number",
                    "minimum": 0,
                },
                "layers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 200,
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 5000},
            },
            "required": ["point"],


            "x-asks-map-services": True,
        },
        handler=_identify_features,


        background=_identify_asks_services,
    ))

    registry.register(Tool(
        name="validate_expression",
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Check an expression"),
        input_schema={
            "type": "object",
            "properties": {
                "expression": {"type": "string"},
                "layer_name": {
                    "type": "string",
                },
            },
            "required": ["expression"],
        },
        handler=_validate_expression,
    ))

    registry.register(Tool(
        name="get_layer_extent",
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Read the extent of {layer_name}"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
            },
            "required": ["layer_name"],
        },
        handler=_get_layer_extent,
    ))





def _units(crs) -> str:
    try:
        return QgsUnitTypes.toString(crs.mapUnits())
    except Exception:
        return "unknown"


def _vector(name: str):
    layer = resolve_layer(name)
    if layer is None:
        return None, layer_not_found(name)
    if not is_vector(layer):
        return None, tool_error(
            f"Layer {layer.name()!r} is not a vector layer.",
            "INVALID_ARGS",
            hint="layer_not_vector", layer=layer.name(),
        )
    return layer, None


def _raster(name: str):
    layer = resolve_layer(name)
    if layer is None:
        return None, layer_not_found(name)
    if not is_raster(layer):
        return None, tool_error(
            f"Layer {layer.name()!r} is not a raster layer.",
            "INVALID_ARGS",
            hint="layer_not_raster", layer=layer.name(),
        )
    return layer, None


def _field_error(layer, field_name: str) -> dict:
    from .layer_lookup import _field_not_found_error

    return _field_not_found_error(layer, field_name)


def _expand(path: str) -> str:
    return os.path.normpath(os.path.abspath(os.path.expanduser(os.path.expandvars(path))))





def _sql_table_name(name: str, used: set) -> str:
    base = name or "layer"
    table = base
    counter = 2
    while table in used:
        table = f"{base}_{counter}"
        counter += 1
    used.add(table)
    return table


def _sql_error_detail(vlayer) -> str:









    try:
        detail = vlayer.error().summary() or vlayer.error().message()
    except Exception:  # nosec B110
        detail = ""
    if detail:
        return detail
    try:
        provider = vlayer.dataProvider()
        if provider is not None:
            return provider.error().summary() or provider.error().message()
    except Exception:  # nosec B110
        pass
    return ""





_SQL_ERROR_PATTERNS = (
    ("no such function", "UNSUPPORTED_FUNCTION"),
    ("no such module", "UNKNOWN_TABLE"),
    ("no such table", "UNKNOWN_TABLE"),



    ("in query not found", "UNKNOWN_TABLE"),
)


def _classify_sql_error(detail: str) -> str:

    lowered = (detail or "").lower()
    for needle, code in _SQL_ERROR_PATTERNS:
        if needle in lowered:
            return code
    return "SQL_SYNTAX"


def _capped_count(layer, cap: int):



    request = feature_request(attributes=[], geometry=False, limit=cap + 1)
    n = sum(1 for _ in layer.getFeatures(request))
    return n if n <= cap else f">{cap}"


def _describe_result(definition, query: str, args: dict, has_tables: bool) -> None:

    definition.setQuery(query)
    column = args.get("geometry_field")
    if not column:
        definition.setGeometryWkbType(WKB_NO_GEOMETRY)
    else:
        definition.setGeometryField(column)
        if not has_tables:





            from qgis.core import QgsWkbTypes

            definition.setGeometryWkbType(QgsWkbTypes.Type.GeometryCollection)
    if args.get("uid_field"):
        definition.setUid(args["uid_field"])


def _read_rows(vlayer, fields: list, wanted: int) -> tuple:

    rows: list = []
    features = vlayer.getFeatures(feature_request(geometry=False, limit=wanted + 1))
    for feature in features:
        if len(rows) == wanted:
            return rows, True
        rows.append({name: py_value(feature[name]) for name in fields})
    return rows, False


def _execute_sql(args: dict) -> dict:
    from qgis.core import QgsVirtualLayerDefinition

    query = str(args.get("query") or "").strip()
    if not query:
        return tool_error("query is empty.", "INVALID_ARGS", "A SELECT statement over the layer names.")
    crs_arg = args.get("crs")
    qgs_crs = None
    if crs_arg:
        from qgis.core import QgsCoordinateReferenceSystem

        qgs_crs = QgsCoordinateReferenceSystem(crs_arg)
        if not qgs_crs.isValid():
            return tool_error(
                f"'{crs_arg}' is not a CRS QGIS recognises.",
                "INVALID_CRS",
                "An authority id such as 'EPSG:4326' or 'EPSG:2154'.",
            )
    project = QgsProject.instance()





    if "layers" in args:
        sources = []
        for name in args["layers"] or []:
            layer, error = _vector(name)
            if error:
                return error
            sources.append(layer)
    else:
        sources = [layer for layer in project.mapLayers().values() if is_vector(layer)]
        if not sources:
            return tool_error(
                "No vector layers to query.",
                "INVALID_ARGS",
                hint="sql_no_vector_layers",
            )

    definition = QgsVirtualLayerDefinition()
    used: set = set()
    tables = {}
    counts: list = []
    unmeasured: list = []
    ceiling = limits.current("MAX_FEATURES_MATERIALISED")
    lowered = query.lower()
    for layer in sources:
        table = _sql_table_name(layer.name(), used)
        definition.addSource(table, layer.id())
        tables[table] = layer.id()




        if table.lower() in lowered or layer.name().lower() in lowered:






            counted = feature_count_of(layer)
            if counted is None:
                unmeasured.append(layer.name())
            else:
                counts.append(max(counted, 0))





    read = 1 if len(counts) > 1 else sum(counts)
    if len(counts) > 1:
        for counted in counts:
            read *= counted
    if unmeasured or read > ceiling:
        if not unmeasured:
            measured = (f"{' x '.join(f'{c:,}' for c in counts)} = {read:,} row pairs"
                        if len(counts) > 1 else f"{read:,} features")
        else:
            named = f"{', '.join(unmeasured)}, whose size only its server gives"
            measured = f"{read:,} features plus {named}" if read else named
        return limits.refusal(
            "The tables this query reads", measured, f"{ceiling:,} for a query run on the main thread",
            "A spatial join as Processing with async true (native:extractbylocation, "
            "native:joinattributesbylocation) uses a spatial index; set_layer_filter or "
            "native:extractbyextent narrows the big layer before the query.",
            code="TOO_MANY_FEATURES")
    _describe_result(definition, query, args, has_tables=bool(sources))
    vlayer = QgsVectorLayer(definition.toString(), args.get("layer_name") or "sql_result", "virtual")
    if not vlayer.isValid():
        detail = _sql_error_detail(vlayer)
        return tool_error(
            f"Invalid SQL or virtual layer for query: {query}" + (f" ({detail})" if detail else ""),
            _classify_sql_error(detail),
            hint="sql_invalid", tables=sorted(tables),
        )

    if qgs_crs is not None and not sources:




        vlayer.setCrs(qgs_crs)

    if args.get("as_layer"):
        project.addMapLayer(vlayer)
        return {
            "layer_id": vlayer.id(),
            "name": vlayer.name(),


            "feature_count": _capped_count(vlayer, _SQL_ROW_CAP),
            "count_units": "features",
            "crs": crs_ref(vlayer.crs()),
            "units": _units(vlayer.crs()),
            "fields": [f.name() for f in vlayer.fields()],
            "tables": tables,
        }

    wanted = max(1, min(int(args.get("limit") or _SQL_ROW_CAP), _SQL_ROW_CAP))
    fields = [f.name() for f in vlayer.fields()]
    rows, truncated = _read_rows(vlayer, fields, wanted)


    rows, omitted = size_budget(rows, 4_000)
    out = {
        "fields": fields,
        "rows": rows,
        "count": len(rows),
        "count_units": "rows",
        "truncated": truncated or omitted > 0,
        "tables": tables,
    }
    if omitted:
        out["omitted"] = omitted
    return out





def _make_field(name: str, field_type: str, length: int, precision: int):
    qtype = FIELD_TYPES.get((field_type or "double").lower(), QVAR_DOUBLE)
    try:
        return QgsField(name, qtype, field_type, int(length or 0), int(precision or 0))
    except TypeError:
        from qgis.PyQt.QtCore import QMetaType

        meta = {
            "string": QMetaType.Type.QString, "int": QMetaType.Type.Int, "double": QMetaType.Type.Double,
            "bool": QMetaType.Type.Bool, "date": QMetaType.Type.QDate, "datetime": QMetaType.Type.QDateTime,
        }.get((field_type or "double").lower(), QMetaType.Type.Double)
        return QgsField(name, meta, field_type, int(length or 0), int(precision or 0))


def _field_calculator(args: dict) -> dict:















    opened = run_on_main_thread(_calc_open, args, timeout=60)
    if opened.get("_error"):
        return opened
    held = opened["held"]
    slices = 0
    try:
        while True:
            if net.is_cancelled():
                return tool_error("The run was stopped. Nothing was written and the layer is unchanged.",
                                  "CANCELLED", "The user stopped the run.")
            if run_on_main_thread(_calc_step, held, timeout=120):
                break
            slices += 1
            background.breathe(slices, every=1)
        return run_on_main_thread(_calc_finish, held, timeout=120)
    finally:



        held.release()


def _calc_close(state: dict) -> None:

    with contextlib.suppress(RuntimeError):
        state["features"].close()
    with contextlib.suppress(RuntimeError):
        vector_write.force_out_of_edit(state["layer"])


def _calc_open(args: dict) -> dict:


    layer, error = _vector(args["layer_name"])
    if error:
        return error
    field_name = args["field_name"]
    expression = args["expression"]
    expr = QgsExpression(expression)
    if expr.hasParserError():
        return tool_error(
            f"Expression parse error: {expr.parserErrorString()}",
            "INVALID_ARGS",
            hint="expression_parse_error",
        )



    if layer.isEditable():
        return tool_error(
            f"{layer.name()!r} has unsaved edits open.",
            "EDIT_FAILED",
            hint="layer_has_open_edits", layer=layer.name(),
        )

    on_error = str(args.get("on_error") or "abort").lower()
    if on_error not in ("abort", "skip"):
        return tool_error(f"{on_error!r} is not an on_error policy.", "INVALID_ARGS",
                          "'abort' (default: write nothing when an expression fails) or 'skip'.")




    if feature_count_of(layer) is None:
        return tool_error(
            f"{layer.name()!r} is read from a web service, so this tool does not calculate it in place.",
            "INVALID_ARGS",
            hint="web_layer_not_calculated", layer=layer.name(),
        )

    idx = layer.fields().indexOf(field_name)
    if idx < 0:



        from .layer_io_tools import shapefile_field_name_error

        too_long = shapefile_field_name_error(layer, field_name)
        if too_long:
            return too_long

    ctx = QgsExpressionContext()
    ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))




    from .query_tools import _measurement

    measurement = _measurement(expr, layer)
    expr.prepare(ctx)


    started_here, cannot_edit = vector_write.open_edit(layer, "add fields")
    if cannot_edit:
        return cannot_edit
    if not started_here:
        return tool_error(
            f"Could not take the edit session on {layer.name()!r}.",
            "EDIT_FAILED",
            "This layer's edit session is already open elsewhere.",
        )





    created = False
    type_plan: dict = {}
    if idx < 0:
        field_type = str(args.get("field_type") or "double")
        new_field = _make_field(field_name, field_type, args.get("length", 0), args.get("precision", 0))




        type_plan = vector_write.plan_field_type(layer, new_field.type(),
                                                 new_field.length(), new_field.precision())
        if type_plan.get("type_name"):
            new_field = QgsField(field_name, type_plan["type"], type_plan["type_name"],
                                 type_plan["length"], type_plan["precision"])
        if not layer.addAttribute(new_field):
            vector_write.force_out_of_edit(layer)
            return tool_error(
                f"The provider refused to add field {field_name!r}.",
                "INVALID_ARGS",
                hint="field_refused", field_name=field_name,
            )
        layer.updateFields()
        idx = layer.fields().indexOf(field_name)
        created = True



    request = feature_request(expression=expr, fields=layer.fields())
    state = {"layer": layer, "field_name": field_name, "idx": idx, "expr": expr, "ctx": ctx,
             "features": layer.getFeatures(request), "on_error": on_error, "created": created,
             "type_plan": type_plan, "measurement": measurement, "updated": 0, "refused": [],
             "eval_errors": 0, "first_error": "", "failed": None}


    return {"held": background.hand_over(state, drop=_calc_close)}


def _calc_step(held) -> bool:

    state = held.peek()
    if state is None:
        return True
    layer, expr, ctx, idx = state["layer"], state["expr"], state["ctx"], state["idx"]


    most = max(1, int(limits.current("SYNC_FEATURE_LOOP_MAX")))
    deadline = time.perf_counter() + background.SLOW_MAIN_THREAD_S * 0.8
    for done, feat in enumerate(state["features"], 1):
        ctx.setFeature(feat)
        value = expr.evaluate(ctx)
        if expr.hasEvalError():
            state["eval_errors"] += 1
            if not state["first_error"]:
                state["first_error"] = expr.evalErrorString()
            if state["on_error"] == "abort":
                state["failed"] = feat.id()
                state["features"].close()
                return True
            continue


        if layer.changeAttributeValue(feat.id(), idx, value):
            state["updated"] += 1
        else:
            state["refused"].append(feat.id())
        if done >= most or time.perf_counter() >= deadline:
            return False
    state["features"].close()
    return True


def _calc_finish(held) -> dict:

    state = held.take()
    if state is None:
        return tool_error("The call ended before the calculation was written. The layer is unchanged.",
                          "CANCELLED", "The call is over.")
    try:
        return _calc_commit(state)
    except BaseException:
        _calc_close(state)
        raise


def _calc_commit(state: dict) -> dict:

    layer = state["layer"]
    if state["failed"] is not None:



        vector_write.force_out_of_edit(layer)
        return tool_error(
            f"The expression failed on feature {state['failed']}: {state['first_error']}. "
            f"Nothing was written and the layer is unchanged.",
            "INVALID_ARGS",
            hint="calc_expression_failed", feature=state["failed"],
        )
    if not background.still_awaited():

        vector_write.force_out_of_edit(layer)
        return tool_error("The call ended before the calculation was written. The layer is unchanged.",
                          "CANCELLED", "The call is over.")
    if not layer.commitChanges():
        errors = layer.commitErrors()


        vector_write.force_out_of_edit(layer)
        from .layer_io_tools import commit_failure_error

        failure = commit_failure_error(layer, errors, hint="field_type_mismatch")
        failure["code"] = "COMMIT_FAILED"
        return failure
    refused, eval_errors = state["refused"], state["eval_errors"]
    out = {
        "layer_id": layer.id(),
        "name": layer.name(),
        "field_name": state["field_name"],
        "created": state["created"],
        "updated": state["updated"],
        "feature_count": layer.featureCount(),
        "count_units": "features",
        "on_error": state["on_error"],
        **state["measurement"],
    }
    if state["created"] and state["type_plan"].get("note"):
        out["type_note"] = state["type_plan"]["note"]
    if refused:
        out["refused"] = refused[:20]
        out["refused_count"] = len(refused)
        out["partial"] = True
    if eval_errors:
        out["eval_errors"] = eval_errors
        out["first_eval_error"] = state["first_error"]
        out["partial"] = True
    return out





def _by_kind_then_value(value) -> tuple:
    return str(type(value)), value


def _ordered_values(raw) -> list:




    plain = [converted for converted in map(py_value, raw) if converted is not None]
    try:
        return sorted(plain, key=_by_kind_then_value)
    except TypeError:
        return plain


def _unique_limit(args: dict) -> tuple[int, dict | None]:









    ceiling = limits.current("MAX_FEATURES_PER_CALL")
    try:
        asked = int(args.get("limit") if args.get("limit") is not None else _UNIQUE_VALUES_DEFAULT)
    except (TypeError, ValueError):
        return 0, tool_error(f"limit must be a whole number, got {args.get('limit')!r}.", "INVALID_ARGS",
                             f"limit defaults to {_UNIQUE_VALUES_DEFAULT}; it can go up to "
                             f"{ceiling}.")
    return (ceiling if asked <= 0 else min(asked, ceiling)), None


def _unique_values_off_main(args: dict) -> bool:










    args = args or {}
    layer = resolve_layer(str(args.get("layer_name") or ""))
    return is_vector(layer) and (is_remote_vector(layer) or bool(args.get("with_counts")))


def _invalid_layer_error(layer) -> dict | None:





    if layer.isValid():
        return None
    return tool_error(
        f"Layer {layer.name()!r} is not valid: its data source could not be opened.",
        "LAYER_INVALID",
        hint="layer_invalid", layer=layer.name())


def _count_key(value):


    return None if py_value(value) is None else (type(value).__name__, str(value))


def _unique_values_plan(args: dict) -> dict:

    layer, error = _vector(args["layer_name"])
    if error:
        return error
    error = _invalid_layer_error(layer)
    if error:
        return error
    field = args["field"]
    idx = layer.fields().indexOf(field)
    if idx < 0:
        return _field_error(layer, field)
    limit, error = _unique_limit(args)
    if error:
        return error
    request = QgsFeatureRequest().setFlags(QgsFeatureRequest.Flag.NoGeometry)
    request.setSubsetOfAttributes([idx])
    return {"source": QgsVectorLayerFeatureSource(layer), "request": request, "index": idx, "limit": limit,
            "scan_cap": limits.current("MAX_FEATURES_MATERIALISED"), "layer_id": layer.id(),
            "name": layer.name(), "field": field}


def _unique_values_off_thread(args: dict) -> dict:



    cancelled = net.current_cancel_check()
    plan = run_on_main_thread(_unique_values_plan, args, timeout=60)
    if "source" not in plan:
        return plan
    with_counts = bool(args.get("with_counts"))
    seen: dict = {}
    state = {"scanned": 0, "complete": False}
    halted, over, failure = threading.Event(), threading.Event(), []

    def reader(source, request):
        try:
            with background.on_any_failure(failure.append):
                features = source.getFeatures(request)
                try:
                    for feature in features:
                        if halted.is_set():
                            return
                        value = feature[plan["index"]]
                        entry = seen.setdefault(_count_key(value), [value, 0])
                        entry[1] += 1
                        state["scanned"] += 1
                        if state["scanned"] >= plan["scan_cap"] or (not with_counts and len(seen) > plan["limit"]):
                            return
                    state["complete"] = True
                finally:
                    features.close()
        finally:
            over.set()

    background.start_kept_thread(reader, plan.pop("source"), plan["request"], name="get_unique_values read")
    while not over.wait(_STOP_POLL_S):
        if callable(cancelled) and cancelled():
            halted.set()
            return tool_error("Stopped while the values were read.", "CANCELLED",
                              "Nothing was changed.")
    if failure:
        raise failure[0]
    values = _ordered_values([entry[0] for entry in seen.values()])
    kept, omitted = size_budget(values[:plan["limit"]], 4_000)
    out = {"layer_id": plan["layer_id"], "name": plan["name"], "field": plan["field"], "values": kept,
           "count": min(len(values), plan["limit"]), "count_units": "distinct values",
           "truncated": len(values) > plan["limit"] or omitted > 0 or not state["complete"]}
    if omitted:
        out["omitted"] = omitted
    if not state["complete"]:
        out["read_note"] = f"read the first {state['scanned']} features of the layer"
    if with_counts:
        ranked = sorted(seen.values(), key=lambda entry: (-entry[1], str(entry[0])))
        out["counts"] = [{"value": py_value(value), "count": count}
                         for value, count in ranked[:_COUNTED_VALUES_MAX]]
        out["counts_order"] = "most frequent first"
        out["counted_features"] = state["scanned"]
    return out


def _value_frequencies(layer, index: int, scan_cap: int) -> tuple[dict, int]:








    request = QgsFeatureRequest().setSubsetOfAttributes([index])
    flag = enum_member(QgsFeatureRequest, "Flag", "NoGeometry", None)
    if flag is not None:
        request.setFlags(flag)
    counts: dict = {}
    scanned = 0
    features = layer.getFeatures(request)
    try:
        for feature in features:
            value = feature[index]
            counts.setdefault(_count_key(value), [value, 0])[1] += 1
            scanned += 1
            if scanned >= scan_cap:
                break
    finally:
        with contextlib.suppress(Exception):
            features.close()
    return counts, scanned


def _get_unique_values(args: dict) -> dict:
    if not background.on_main_thread():

        return _unique_values_off_thread(args)
    layer, error = _vector(args["layer_name"])
    if error:
        return error
    error = _invalid_layer_error(layer)
    if error:
        return error
    field = args["field"]
    idx = layer.fields().indexOf(field)
    if idx < 0:
        return _field_error(layer, field)
    limit, error = _unique_limit(args)
    if error:
        return error
    raw = layer.uniqueValues(idx, limit)
    values = _ordered_values(raw)

    kept, omitted = size_budget(values, 4_000)
    out = {
        "layer_id": layer.id(),
        "name": layer.name(),
        "field": field,
        "values": kept,
        "count": len(values),
        "count_units": "distinct values",
        "truncated": (limit > 0 and len(values) >= limit) or omitted > 0,
    }
    if omitted:
        out["omitted"] = omitted
    if args.get("with_counts"):



        counts, scanned = _value_frequencies(layer, idx, limits.current("MAX_FEATURES_MATERIALISED"))
        ranked = sorted(counts.values(), key=lambda entry: (-entry[1], str(entry[0])))


        out["counts"] = [{"value": py_value(value), "count": count}
                         for value, count in ranked[:_COUNTED_VALUES_MAX]]
        out["counts_order"] = "most frequent first"
        out["counted_features"] = scanned


        total = feature_count_of(layer)
        if total is not None and scanned < total:
            out["counts_note"] = f"counted on the first {scanned} of {total} features"
    return out





def _search_area(layer, project, point_crs, x: float, y: float, tolerance: float):





    here = QgsPointXY(x, y)
    square = QgsRectangle(x - tolerance, y - tolerance, x + tolerance, y + tolerance)
    if layer.crs() == point_crs:
        return here, square, tolerance
    to_layer = QgsCoordinateTransform(point_crs, layer.crs(), project)
    here = to_layer.transform(here)
    if not tolerance > 0:
        return here, QgsRectangle(here.x(), here.y(), here.x(), here.y()), tolerance
    square = to_layer.transformBoundingBox(square)


    return here, square, max(square.width(), square.height()) / 2.0


def _features_at(layer, project, point_crs, x: float, y: float, tolerance: float, most: int):


    search = _search(layer, project, point_crs, x, y, tolerance, most)
    if isinstance(search, str):
        return search
    return _matching(layer.getFeatures(search["request"]), search)


def _search(layer, project, point_crs, x: float, y: float, tolerance: float, most: int):


    try:
        here, box, reach = _search_area(layer, project, point_crs, x, y, tolerance)
    except Exception as exc:
        return f"CRS transform failed: {exc}"
    return {"request": QgsFeatureRequest().setFilterRect(box), "target": QgsGeometry.fromPointXY(here),
            "reach": reach, "names": [field.name() for field in layer.fields()], "most": most}


def _matching(features, search: dict, stopped=lambda: False) -> list:

    target, reach, names = search["target"], search["reach"], search["names"]
    found: list = []
    for feature in features:
        if stopped():
            break
        shape = feature.geometry()
        if shape is None or shape.isEmpty():
            continue
        missed = shape.distance(target) > reach if reach > 0 else not shape.intersects(target)
        if missed:
            continue
        row = {name: py_value(feature[name]) for name in names}
        row["_fid"] = feature.id()
        found.append(row)
        if len(found) >= search["most"]:
            break
    return found


def _identify_targets(args: dict):

    if args.get("layers"):
        targets = []
        for name in args["layers"]:
            layer = resolve_layer(name)
            if layer is None:
                return None, layer_not_found(name)
            targets.append(layer)
        return targets, None
    return [node.layer() for node in QgsProject.instance().layerTreeRoot().findLayers()
            if node.isVisible() and node.layer() is not None], None


def _identify_asks_services(args: dict) -> bool:






    targets, _error = _identify_targets(args or {})
    return any(_click_formats(layer) or _rows_from_network(layer) for layer in targets or ())


def _rows_from_network(layer) -> bool:
    return is_vector(layer) and is_remote_vector(layer)


def _click_formats(layer) -> list:








    if not is_raster(layer) or layer.providerType() != "wms":
        return []
    capabilities = layer.dataProvider().capabilities()
    formats = []
    for name in ("Text", "Html", "Feature"):
        fmt = QgsRasterDataProvider.identifyFormatFromName(name)
        if capabilities & QgsRasterDataProvider.identifyFormatToCapability(fmt):
            formats.append(fmt)
    return formats


def _identify_features(args: dict) -> dict:
    if not background.on_main_thread():

        return _identify_off_thread(args)
    return _identify_here(args, False)[0]


def _identify_here(args: dict, ask_services: bool):




    project = QgsProject.instance()
    point = args.get("point") or []
    if len(point) != 2:
        return tool_error("point must be [x, y].", "INVALID_ARGS", "point is a two-number array."), []
    x, y = float(point[0]), float(point[1])
    tolerance = float(args.get("tolerance") or 0.0)
    limit = max(1, int(args.get("limit") or 10))
    point_crs = QgsCoordinateReferenceSystem(args["crs"]) if args.get("crs") else project.crs()
    if not point_crs.isValid():
        return tool_error(f"Invalid CRS: {args.get('crs')}", "CRS_INVALID",
                          "An authority id such as EPSG:4326 fits."), []
    targets, error = _identify_targets(args)
    if error:
        return error, []

    results, skipped, asks = [], [], []
    searched = 0
    for layer in targets:
        if not layer.isValid():
            skipped.append({"layer_id": layer.id(), "name": layer.name(),
                            "kind": "layer not valid: its data source could not be opened "
                                    "(database or service unreachable, or the file is gone)"})
            continue
        if is_vector(layer) and ask_services and _rows_from_network(layer):
            searched += 1
            search = _search(layer, project, point_crs, x, y, tolerance, limit)
            if isinstance(search, str):
                results.append({"layer_id": layer.id(), "name": layer.name(), "error": search})
            else:
                asks.append(_ServiceAsk({**search, "layer_id": layer.id(), "name": layer.name(),
                                         "source": QgsVectorLayerFeatureSource(layer)}))
            continue
        if is_vector(layer):
            searched += 1
            found = _features_at(layer, project, point_crs, x, y, tolerance, limit)
            if isinstance(found, str):
                results.append({"layer_id": layer.id(), "name": layer.name(), "error": found})
            elif found:
                results.append({"layer_id": layer.id(), "name": layer.name(), "features": found,
                                "count": len(found)})
            continue
        formats = _click_formats(layer)
        if formats and ask_services:
            searched += 1
            ask = _service_ask(layer, project, point_crs, x, y, formats, limit)
            if isinstance(ask, str):
                results.append({"layer_id": layer.id(), "name": layer.name(), "error": ask})
            else:
                asks.append(ask)
            continue
        kind = _unread_kind(layer, bool(formats))
        if kind:
            skipped.append({"layer_id": layer.id(), "name": layer.name(), "kind": kind})
            continue
        searched += 1
        values = _pixel_values_at(layer, project, point_crs, x, y)
        if isinstance(values, str):
            results.append({"layer_id": layer.id(), "name": layer.name(), "error": values})
        elif values:
            results.append({"layer_id": layer.id(), "name": layer.name(), "pixel_values": values})
    out = {
        "point": [x, y],
        "crs": point_crs.authid(),
        "tolerance": tolerance,
        "units": _units(point_crs),
        "layers_searched": searched,
        "results": results,
        "count": sum(r.get("count", 0) for r in results),
        "count_units": "features",
    }
    if skipped:
        out["layers_not_searched"] = skipped
    return out, asks


def _unread_kind(layer, answers_clicks: bool) -> str:







    if not is_raster(layer):
        return f"{_layer_type_name(layer)} layer, not identified by this tool"
    if answers_clicks:
        return "web service that answers clicks (GetFeatureInfo): not asked on QGIS's main thread"
    provider = layer.providerType()
    if provider == "wms":
        return ("web map or tile service (wms) whose capabilities offer no GetFeatureInfo for this "
                "layer (no queryable layer, or tiles only): its picture carries no values")
    source = layer.source().lower()
    if provider != "gdal" or "/vsicurl" in source or "://" in source:
        return f"remote or {provider} raster: its pixels are not read here; raster_sample reads one"
    return ""


def _service_ask(layer, project, point_crs, x: float, y: float, formats: list, limit: int):








    point = QgsPointXY(x, y)
    if point_crs != layer.crs():
        try:
            point = QgsCoordinateTransform(point_crs, layer.crs(), project).transform(point)
        except Exception as exc:
            return f"CRS transform failed: {exc}"
    half = _CLICK_PX / 2 * _CLICK_PIXEL_M / (metres_per_map_unit(layer.crs(), degrees=True) or 1.0)
    return _ServiceAsk({
        "layer_id": layer.id(), "name": layer.name(), "provider": layer.dataProvider().clone(),
        "point": point, "limit": limit, "formats": formats,
        "box": QgsRectangle(point.x() - half, point.y() - half, point.x() + half, point.y() + half)})


class _ServiceAsk(QThread):


















    def __init__(self, ask: dict):
        super().__init__()
        self.ask = ask
        if "provider" in ask:
            ask["provider"].moveToThread(self)
        self.cancelled = None
        self.row = None

    def run(self):
        if "source" in self.ask:
            self.row = _read_source_rows(self.ask, self)
            return
        provider = self.ask.pop("provider")
        try:
            self.row = _ask_service(self.ask, provider, self)
        except Exception as exc:  # noqa: BLE001
            self.row = {"layer_id": self.ask["layer_id"], "name": self.ask["name"], "read": "GetFeatureInfo",
                        "error": f"GetFeatureInfo failed: {exc}"[:500]}
        finally:
            sip.delete(provider)


def _identify_off_thread(args: dict) -> dict:



    cancelled = net.current_cancel_check()
    out, asks = run_on_main_thread(_identify_here, args, True, timeout=60)
    try:
        for ask in asks:
            ask.cancelled = cancelled
            ask.start()
    finally:
        for ask in asks:
            ask.wait()
    if any(ask.row is None for ask in asks):
        return tool_error("Stopped while a web service was asked what lies at the point.", "CANCELLED",
                          "Nothing was changed.")
    for ask in asks:
        if ask.row.get("error") or ask.row.get("features") or ask.row.get("text"):
            out["results"].append(ask.row)
            out["count"] += ask.row.get("count", 0)
    return out


def _ask_service(ask: dict, provider, thread: _ServiceAsk):



    row = {"layer_id": ask["layer_id"], "name": ask["name"], "read": "GetFeatureInfo"}
    failure = ""
    for fmt in ask["formats"]:
        before = provider.lastError()
        answer, state = _ask_once(ask, provider, fmt, thread)
        if state["stopped"]:
            return None
        if state["late"]:
            return {**row, "error": f"the service did not answer within {_SERVICE_WAIT_S:.0f} s"}
        if not answer.isValid():
            failure = answer.error().message()
            continue
        content = _answer_content(answer.results(), ask["limit"])
        if content:
            return {**row, "format": QgsRasterDataProvider.identifyFormatName(fmt), **content}
        if provider.lastError() != before:
            failure = provider.lastError()
            continue
        return row
    return {**row, "error": f"GetFeatureInfo failed: {failure}"[:500]}


def _ask_once(ask: dict, provider, fmt, thread: _ServiceAsk):



    with _watched(thread) as state:
        return provider.identify(ask["point"], fmt, ask["box"], _CLICK_PX, _CLICK_PX), state


def _read_source_rows(ask: dict, thread: _ServiceAsk):


    source = ask.pop("source")
    row = {"layer_id": ask["layer_id"], "name": ask["name"]}
    state = {"stopped": False, "late": False}
    try:
        with _watched(thread) as state:
            features = source.getFeatures(ask["request"])
            try:
                found = _matching(features, ask, lambda: state["stopped"] or state["late"])
            finally:
                features.close()
    except Exception as exc:  # noqa: BLE001
        found = None
        row["error"] = f"reading the layer failed: {exc}"[:500]
    finally:
        source = None
    if state["stopped"]:
        return None
    if state["late"]:
        return {**row, "error": f"the service did not answer within {_SERVICE_WAIT_S:.0f} s"}
    return {**row, "features": found, "count": len(found)} if found else row


@contextlib.contextmanager
def _watched(thread: _ServiceAsk):



    deadline = time.monotonic() + _SERVICE_WAIT_S
    state = {"stopped": False, "late": False}
    timer = QTimer()

    def watch():
        state["stopped"] = callable(thread.cancelled) and bool(thread.cancelled())
        state["late"] = time.monotonic() > deadline
        if state["stopped"] or state["late"]:
            timer.stop()
            thread.exit()

    timer.setInterval(int(_STOP_POLL_S * 1000))
    timer.timeout.connect(watch)
    timer.start()
    try:
        yield state
    finally:
        timer.stop()


def _answer_content(results: dict, limit: int) -> dict:



    features, texts = [], []
    for value in results.values():
        if isinstance(value, list):
            for store in value:
                names = [field.name() for field in store.fields()]
                features += [dict(zip(names, map(py_value, feature.attributes()))) for feature in store.features()]
        elif isinstance(value, str) and value.strip():
            pairs, _other = kml_description.parse(value)
            if not pairs:
                texts.append(value.strip())
            record: dict = {}
            for name, text in pairs:
                if name in record:
                    features.append(record)
                    record = {}
                record[name] = text
            if record:
                features.append(record)
    content: dict = {}
    if features:
        kept, omitted = size_budget(features[:limit], 2_500)
        content.update(features=kept, count=len(kept))
        if omitted or len(features) > limit:
            content["more"] = len(features) - len(kept)
    if texts:
        content["text"] = "\n\n".join(texts)[:2_500]
    return content


def _layer_type_name(layer) -> str:

    name = type(layer).__name__
    return name[3:-5].lower() if name.startswith("Qgs") and name.endswith("Layer") else name


def _pixel_values_at(layer, project, point_crs, x: float, y: float):

    point = QgsPointXY(x, y)
    if point_crs != layer.crs():
        try:
            point = QgsCoordinateTransform(point_crs, layer.crs(), project).transform(point)
        except Exception as exc:
            return f"CRS transform failed: {exc}"
    provider = layer.dataProvider()
    if not layer.extent().contains(point):
        return {}
    values = {}
    for band in range(1, layer.bandCount() + 1):
        value, ok = provider.sample(point, band)
        values[layer.bandName(band)] = py_value(value) if ok else None
    return values





def _validate_expression(args: dict) -> dict:
    expression = str(args.get("expression") or "")
    parsed = QgsExpression(expression)
    broken = parsed.hasParserError()
    columns = sorted(str(c) for c in parsed.referencedColumns())
    report = {"expression": expression, "valid": not broken, "referenced_columns": columns}
    for key, reader in (("referenced_functions", parsed.referencedFunctions),
                        ("referenced_variables", parsed.referencedVariables)):
        with contextlib.suppress(Exception):
            report[key] = sorted(reader())
    if broken:
        report["error"] = parsed.parserErrorString()
        report.update(coded_fact(hint="expression_quoting"))
    if not args.get("layer_name"):
        return report
    layer, error = _vector(args["layer_name"])
    if error:
        return error
    fields = [f.name() for f in layer.fields()]
    unknown = [c for c in columns if c != "*" and c not in fields]
    report["layer"] = layer.name()
    report["unknown_columns"] = unknown
    if unknown:
        report["valid"] = False
        report["suggestion"] = f"Columns not on {layer.name()!r}: {unknown}. Fields: {fields[:20]}."
    if not broken:
        _try_on_first_feature(parsed, layer, report)
    return report


def _try_on_first_feature(parsed, layer, report: dict) -> None:

    context = QgsExpressionContext()
    context.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    parsed.prepare(context)
    if parsed.hasEvalError():
        report["eval_error"] = parsed.evalErrorString()
        report["valid"] = False
        return
    first = next(iter(layer.getFeatures(QgsFeatureRequest().setLimit(1))), None)
    if first is None:
        return
    context.setFeature(first)
    value = parsed.evaluate(context)
    if parsed.hasEvalError():
        report["eval_error"] = parsed.evalErrorString()
        report["valid"] = False
    else:
        report["sample_value"] = py_value(value)
        report["sample_fid"] = first.id()





def _get_layer_extent(args: dict) -> dict:
    layer = resolve_layer(args["layer_name"])
    if layer is None:
        return layer_not_found(args["layer_name"])
    box, crs = layer.extent(), layer.crs()
    corners = {"xmin": box.xMinimum(), "ymin": box.yMinimum(), "xmax": box.xMaximum(), "ymax": box.yMaximum()}
    out = {"layer_id": layer.id(), "name": layer.name()}



    if box.isNull() or any(not math.isfinite(corner) for corner in corners.values()):
        out["empty"] = True
        out.update(dict.fromkeys(corners))
        out.update(crs=crs.authid(), units=_units(crs))
        return out
    out.update(corners)
    middle = box.center()
    out.update(width=box.width(), height=box.height(), center=[middle.x(), middle.y()], crs=crs.authid(),
               units=_units(crs), is_geographic=crs.isGeographic())
    return out
