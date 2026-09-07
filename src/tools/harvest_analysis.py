# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





from __future__ import annotations

import contextlib
import math
import os
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
    QgsRectangle,
    QgsUnitTypes,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core import background, limits, net
from ..core.crs_ref import crs_ref
from ..core.feature_requests import feature_request
from ..core.layer_order import feature_count_of
from ..core.serialization import size_budget
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from . import guards, vector_write
from ._compat import FIELD_TYPES, QVAR_DOUBLE, WKB_NO_GEOMETRY, is_raster, is_vector, py_value
from ._layers import layer_not_found, resolve_layer
from .danger import sql_mutates
from .data_tools import _run_on_main_thread

_SQL_ROW_CAP = 1000



_COUNTED_VALUES_MAX = 50
_UNIQUE_VALUES_DEFAULT = 1000


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
        },
        handler=_get_unique_values,
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
        },
        handler=_identify_features,
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
            "list_layers shows each layer's type; pass a vector layer.",
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
            "list_layers shows each layer's type; pass a raster layer.",
        )
    return layer, None


def _field_error(layer, field_name: str) -> dict:
    from .core_tools import _field_not_found_error

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
        return tool_error("query is empty.", "INVALID_ARGS", "Pass a SELECT statement over the layer names.")
    crs_arg = args.get("crs")
    qgs_crs = None
    if crs_arg:
        from qgis.core import QgsCoordinateReferenceSystem

        qgs_crs = QgsCoordinateReferenceSystem(crs_arg)
        if not qgs_crs.isValid():
            return tool_error(
                f"'{crs_arg}' is not a CRS QGIS recognises.",
                "INVALID_CRS",
                "Use an authority id such as 'EPSG:4326' or 'EPSG:2154'.",
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
                "Load a vector layer with add_data first, pass layers by name, or pass "
                "layers: [] for a query with no table (a literal SELECT).",
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
            "Run a spatial join as Processing with async true (native:extractbylocation, "
            "native:joinattributesbylocation), which uses a spatial index; or filter or clip the big layer "
            "first (set_layer_filter, native:extractbyextent) and query the extract.",
            code="TOO_MANY_FEATURES")
    _describe_result(definition, query, args, has_tables=bool(sources))
    vlayer = QgsVectorLayer(definition.toString(), args.get("layer_name") or "sql_result", "virtual")
    if not vlayer.isValid():
        detail = _sql_error_detail(vlayer)
        return tool_error(
            f"Invalid SQL or virtual layer for query: {query}" + (f" ({detail})" if detail else ""),
            _classify_sql_error(detail),
            f"Table names available in FROM/JOIN: {sorted(tables)}. Double-quote names with spaces. "
            "get_layer_info lists a layer's columns. This is SQLite/SpatiaLite, not PostgreSQL: no "
            "width_bucket or other Postgres-only window/aggregate functions.",
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











    state = _run_on_main_thread(_calc_open, args, timeout=60)
    if state.get("_error"):
        return state
    slices = 0
    finished = False
    try:
        while True:
            if _stopped():
                return tool_error("The run was stopped. Nothing was written and the layer is unchanged.",
                                  "CANCELLED", "Wait for the next user message.")
            if _run_on_main_thread(_calc_step, state, timeout=120):
                break
            slices += 1
            background.breathe(slices, every=1)
        out = _run_on_main_thread(_calc_finish, state, timeout=120)
        finished = True
        return out
    finally:
        if not finished:


            layer = state["layer"]
            background.main_thread_invoker().invoke(lambda: _calc_close(layer))


def _stopped() -> bool:
    cancelled = net.current_cancel_check()
    try:
        return callable(cancelled) and bool(cancelled())
    except Exception:  # noqa: BLE001
        return False


def _calc_close(layer) -> None:

    with contextlib.suppress(RuntimeError):
        vector_write.force_out_of_edit(layer)


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
            "Call validate_expression to see the parse error and the referenced columns, then fix it.",
        )



    if layer.isEditable():
        return tool_error(
            f"{layer.name()!r} has unsaved edits open.",
            "EDIT_FAILED",
            "Call qgis_edit_commit to keep them or qgis_edit_rollback to discard them, then calculate.",
        )

    on_error = str(args.get("on_error") or "abort").lower()
    if on_error not in ("abort", "skip"):
        return tool_error(f"{on_error!r} is not an on_error policy.", "INVALID_ARGS",
                          "Use 'abort' (default: write nothing when an expression fails) or 'skip'.")




    if feature_count_of(layer) is None:
        return tool_error(
            f"{layer.name()!r} is read from a web service, so this tool does not calculate it in place.",
            "INVALID_ARGS",
            "Save it to a GeoPackage first (export_layer) and calculate on that copy.",
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
            "Close any other editor of this layer and call again.",
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
                "Export the layer to GeoPackage (export_layer) and calculate on the copy.",
            )
        layer.updateFields()
        idx = layer.fields().indexOf(field_name)
        created = True



    request = feature_request(expression=expr, fields=layer.fields())
    return {"layer": layer, "field_name": field_name, "idx": idx, "expr": expr, "ctx": ctx,
            "features": layer.getFeatures(request), "on_error": on_error, "created": created,
            "type_plan": type_plan, "measurement": measurement, "updated": 0, "refused": [],
            "eval_errors": 0, "first_error": "", "failed": None}


def _calc_step(state: dict) -> bool:

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


def _calc_finish(state: dict) -> dict:

    layer = state["layer"]
    if state["failed"] is not None:



        vector_write.force_out_of_edit(layer)
        return tool_error(
            f"The expression failed on feature {state['failed']}: {state['first_error']}. "
            f"Nothing was written and the layer is unchanged.",
            "INVALID_ARGS",
            "Fix the expression, or pass on_error='skip' to write the rows that do evaluate.",
        )
    if not background.still_awaited():

        vector_write.force_out_of_edit(layer)
        return tool_error("The call ended before the calculation was written. The layer is unchanged.",
                          "CANCELLED", "Wait for the next user message.")
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


def _get_unique_values(args: dict) -> dict:
    layer, error = _vector(args["layer_name"])
    if error:
        return error
    field = args["field"]
    idx = layer.fields().indexOf(field)
    if idx < 0:
        return _field_error(layer, field)






    ceiling = limits.current("MAX_FEATURES_PER_CALL")
    try:
        asked = int(args.get("limit") if args.get("limit") is not None else _UNIQUE_VALUES_DEFAULT)
        limit = ceiling if asked <= 0 else min(asked, ceiling)
    except (TypeError, ValueError):
        return tool_error(f"limit must be a whole number, got {args.get('limit')!r}.", "INVALID_ARGS",
                          f"Leave it out for {_UNIQUE_VALUES_DEFAULT}, or pass a number up to "
                          f"{ceiling}.")
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



        from .style_tools import _value_frequencies
        counts, scanned = _value_frequencies(layer, idx, limits.current("MAX_FEATURES_MATERIALISED"))
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))




        typed = {str(value): py_value(value) for value in raw}
        out["counts"] = [{"value": typed.get(value, value), "count": count}
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


    try:
        here, box, reach = _search_area(layer, project, point_crs, x, y, tolerance)
    except Exception as exc:
        return f"CRS transform failed: {exc}"
    target = QgsGeometry.fromPointXY(here)
    names = [field.name() for field in layer.fields()]
    found: list = []
    for feature in layer.getFeatures(QgsFeatureRequest().setFilterRect(box)):
        shape = feature.geometry()
        if shape is None or shape.isEmpty():
            continue
        missed = shape.distance(target) > reach if reach > 0 else not shape.intersects(target)
        if missed:
            continue
        row = {name: py_value(feature[name]) for name in names}
        row["_fid"] = feature.id()
        found.append(row)
        if len(found) >= most:
            break
    return found


def _identify_features(args: dict) -> dict:
    project = QgsProject.instance()
    point = args.get("point") or []
    if len(point) != 2:
        return tool_error("point must be [x, y].", "INVALID_ARGS", "Pass the point as a two-number array.")
    x, y = float(point[0]), float(point[1])
    tolerance = float(args.get("tolerance") or 0.0)
    limit = max(1, int(args.get("limit") or 10))
    point_crs = QgsCoordinateReferenceSystem(args["crs"]) if args.get("crs") else project.crs()
    if not point_crs.isValid():
        return tool_error(f"Invalid CRS: {args.get('crs')}", "CRS_INVALID", "Pass an authority id such as EPSG:4326.")

    if args.get("layers"):
        targets = []
        for name in args["layers"]:
            layer = resolve_layer(name)
            if layer is None:
                return layer_not_found(name)
            targets.append(layer)
    else:
        targets = [
            node.layer() for node in project.layerTreeRoot().findLayers()
            if node.isVisible() and node.layer() is not None
        ]

    results = []
    searched = 0
    for layer in (candidate for candidate in targets if is_vector(candidate)):
        searched += 1
        found = _features_at(layer, project, point_crs, x, y, tolerance, limit)
        if isinstance(found, str):
            results.append({"layer_id": layer.id(), "name": layer.name(), "error": found})
        elif found:
            results.append({"layer_id": layer.id(), "name": layer.name(), "features": found, "count": len(found)})
    return {
        "point": [x, y],
        "crs": point_crs.authid(),
        "tolerance": tolerance,
        "units": _units(point_crs),
        "layers_searched": searched,
        "results": results,
        "count": sum(r.get("count", 0) for r in results),
        "count_units": "features",
    }





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
        report["suggestion"] = "Fix the syntax: field names in double quotes, strings in single quotes."
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
