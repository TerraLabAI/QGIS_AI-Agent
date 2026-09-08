# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Analysis tools harvested from the other QGIS agent projects."""














from __future__ import annotations

import contextlib
import math
import os

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

from ..core import limits
from ..core.qt_compat import enum_member
from ..core.serialization import size_budget
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from ._compat import FIELD_TYPES, QVAR_DOUBLE, WKB_NO_GEOMETRY, is_raster, is_vector, py_value
from ._layers import layer_not_found, resolve_layer

_SQL_ROW_CAP = 1000




_MAX_SYNC_FEATURES = limits.SYNC_FEATURE_LOOP_MAX



_COUNTED_VALUES_MAX = 50
_UNIQUE_VALUES_DEFAULT = 1000


def register_harvest_analysis_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="execute_sql",
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
                "limit": {"type": "integer", "minimum": 1, "maximum": _SQL_ROW_CAP},
            },
            "required": ["query"],
        },
        handler=_execute_sql,
    ))

    registry.register(Tool(
        name="field_calculator",
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
        },
        handler=_field_calculator,
    ))

    registry.register(Tool(
        name="get_unique_values",
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
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
            },
            "required": ["layer_name"],
        },
        handler=_get_layer_extent,
    ))

    from .harvest_processing import register_harvest_processing_tools

    register_harvest_processing_tools(registry)





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


def _capped_count(layer, cap: int):
    """Rows up to ``cap``, or ``">cap"`` past it: never the whole result twice."""
    request = QgsFeatureRequest()
    request.setLimit(cap + 1)
    try:
        request.setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry"))
    except Exception:  # nosec B110 - a hint
        pass
    n = sum(1 for _ in layer.getFeatures(request))
    return n if n <= cap else f">{cap}"


def _execute_sql(args: dict) -> dict:
    from qgis.core import QgsVirtualLayerDefinition

    query = str(args.get("query") or "").strip()
    if not query:
        return tool_error("query is empty.", "INVALID_ARGS", "Pass a SELECT statement over the layer names.")
    project = QgsProject.instance()
    if args.get("layers"):
        sources = []
        for name in args["layers"]:
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
            "Load a vector layer with add_data first, or pass layers by name.",
        )

    definition = QgsVirtualLayerDefinition()
    used: set = set()
    tables = {}
    read = 0
    ceiling = limits.current("MAX_FEATURES_MATERIALISED")
    lowered = query.lower()
    for layer in sources:
        table = _sql_table_name(layer.name(), used)
        definition.addSource(table, layer.id())
        tables[table] = layer.id()




        if table.lower() in lowered or layer.name().lower() in lowered:
            try:
                read += max(int(layer.featureCount()), 0)
            except Exception:  # noqa: BLE001 - a table that cannot count is not the one to refuse on  # nosec B110
                pass
    if read > ceiling:
        return limits.refusal(
            "The tables this query reads", f"{read:,} features", f"{ceiling:,} for a query run on the main thread",
            "Filter or clip the big layer first (set_layer_filter, native:extractbyextent through run_processing "
            "with async true) and query the extract, or run the spatial part as a Processing algorithm.")
    definition.setQuery(query)
    geometry_field = args.get("geometry_field")
    if geometry_field:
        definition.setGeometryField(geometry_field)
    else:
        definition.setGeometryWkbType(WKB_NO_GEOMETRY)
    if args.get("uid_field"):
        definition.setUid(args["uid_field"])

    layer_name = args.get("layer_name") or "sql_result"
    vlayer = QgsVectorLayer(definition.toString(), layer_name, "virtual")
    if not vlayer.isValid():
        detail = ""
        try:
            detail = vlayer.error().summary() or vlayer.error().message()
        except Exception:
            detail = ""
        message = f"Invalid SQL or virtual layer for query: {query}"
        if detail:
            message += f" ({detail})"
        return tool_error(
            message,
            "INVALID_ARGS",
            f"Table names available in FROM/JOIN: {sorted(tables)}. Double-quote names with spaces. "
            "get_layer_info lists a layer's columns.",
        )

    if args.get("as_layer"):
        project.addMapLayer(vlayer)
        return {
            "layer_id": vlayer.id(),
            "name": vlayer.name(),


            "feature_count": _capped_count(vlayer, _SQL_ROW_CAP),
            "count_units": "features",
            "crs": vlayer.crs().authid(),
            "units": _units(vlayer.crs()),
            "fields": [f.name() for f in vlayer.fields()],
            "tables": tables,
        }

    limit = int(args.get("limit") or _SQL_ROW_CAP)
    limit = max(1, min(limit, _SQL_ROW_CAP))
    fields = [f.name() for f in vlayer.fields()]
    rows = []
    truncated = False
    for i, feat in enumerate(vlayer.getFeatures()):
        if i >= limit:
            truncated = True
            break
        rows.append({fn: py_value(feat[fn]) for fn in fields})


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
            f"Layer {layer.name()!r} has an open edit session.",
            "INVALID_ARGS",
            "Commit or roll back with qgis_edit_commit / qgis_edit_rollback first.",
        )

    on_error = str(args.get("on_error") or "abort").lower()
    if on_error not in ("abort", "skip"):
        return tool_error(f"{on_error!r} is not an on_error policy.", "INVALID_ARGS",
                          "Use 'abort' (default: write nothing when an expression fails) or 'skip'.")




    total = layer.featureCount()
    if total > _MAX_SYNC_FEATURES:
        return tool_error(
            f"{layer.name()!r} holds {total} features, above the {_MAX_SYNC_FEATURES} this tool "
            f"calculates in one pass on the interface thread.",
            "INVALID_ARGS",
            "Run qgis:fieldcalculator through run_processing, which goes to the task manager and "
            "leaves QGIS responsive.",
        )

    idx = layer.fields().indexOf(field_name)
    created = False

    ctx = QgsExpressionContext()
    ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    expr.prepare(ctx)
    if not layer.startEditing():
        return tool_error(
            f"Could not start editing {layer.name()!r}.",
            "EDIT_FAILED",
            "The provider may be read-only; export_layer to GeoPackage and retry on the copy.",
        )





    if idx < 0:
        field_type = str(args.get("field_type") or "double")
        new_field = _make_field(field_name, field_type, args.get("length", 0), args.get("precision", 0))
        if not layer.addAttribute(new_field):
            layer.rollBack()
            return tool_error(
                f"The provider refused to add field {field_name!r}.",
                "INVALID_ARGS",
                "Export the layer to GeoPackage (export_layer) and calculate on the copy.",
            )
        layer.updateFields()
        idx = layer.fields().indexOf(field_name)
        created = True

    updated = 0
    refused: list = []
    eval_errors = 0
    first_error = ""
    for feat in layer.getFeatures():
        ctx.setFeature(feat)
        value = expr.evaluate(ctx)
        if expr.hasEvalError():
            eval_errors += 1
            if not first_error:
                first_error = expr.evalErrorString()
            if on_error == "abort":



                layer.rollBack()
                return tool_error(
                    f"The expression failed on feature {feat.id()}: {first_error}. "
                    f"Nothing was written and the layer is unchanged.",
                    "INVALID_ARGS",
                    "Fix the expression, or pass on_error='skip' to write the rows that do evaluate.",
                )
            continue


        if layer.changeAttributeValue(feat.id(), idx, value):
            updated += 1
        else:
            refused.append(feat.id())
    if not layer.commitChanges():
        errors = "; ".join(layer.commitErrors())
        layer.rollBack()
        return tool_error(
            f"Commit failed: {errors}", "COMMIT_FAILED", "Check the field type matches the expression result."
        )
    out = {
        "layer_id": layer.id(),
        "name": layer.name(),
        "field_name": field_name,
        "created": created,
        "updated": updated,
        "feature_count": layer.featureCount(),
        "count_units": "features",
        "on_error": on_error,
    }
    if refused:
        out["refused"] = refused[:20]
        out["refused_count"] = len(refused)
        out["partial"] = True
    if eval_errors:
        out["eval_errors"] = eval_errors
        out["first_eval_error"] = first_error
        out["partial"] = True
    return out





def _get_unique_values(args: dict) -> dict:
    layer, error = _vector(args["layer_name"])
    if error:
        return error
    field = args["field"]
    idx = layer.fields().indexOf(field)
    if idx < 0:
        return _field_error(layer, field)








    try:
        asked = int(args.get("limit") if args.get("limit") is not None else _UNIQUE_VALUES_DEFAULT)
        limit = limits.MAX_FEATURES_PER_CALL if asked <= 0 else min(asked, limits.MAX_FEATURES_PER_CALL)
    except (TypeError, ValueError):
        return tool_error(f"limit must be a whole number, got {args.get('limit')!r}.", "INVALID_ARGS",
                          f"Leave it out for {_UNIQUE_VALUES_DEFAULT}, or pass a number up to "
                          f"{limits.MAX_FEATURES_PER_CALL}.")
    raw = layer.uniqueValues(idx, limit)
    values = [py_value(v) for v in raw]
    values = [v for v in values if v is not None]
    with contextlib.suppress(TypeError):
        values = sorted(values, key=lambda x: (str(type(x)), x))

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
        out["counts"] = [{"value": value, "count": count} for value, count in ranked[:_COUNTED_VALUES_MAX]]
        out["counts_order"] = "most frequent first"
        out["counted_features"] = scanned
        if scanned < layer.featureCount():
            out["counts_note"] = f"counted on the first {scanned} of {layer.featureCount()} features"
    return out





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

    source_rect = QgsRectangle(x - tolerance, y - tolerance, x + tolerance, y + tolerance)
    results = []
    searched = 0
    for layer in targets:
        if not is_vector(layer):
            continue
        searched += 1
        rect = source_rect
        pt = QgsPointXY(x, y)
        layer_tolerance = tolerance
        if layer.crs() != point_crs:
            transform = QgsCoordinateTransform(point_crs, layer.crs(), project)
            try:
                pt = transform.transform(pt)
                if tolerance > 0:
                    rect = transform.transformBoundingBox(source_rect)
                    layer_tolerance = max(rect.width(), rect.height()) / 2.0
                else:
                    rect = QgsRectangle(pt.x(), pt.y(), pt.x(), pt.y())
            except Exception as e:
                results.append({"layer_id": layer.id(), "name": layer.name(), "error": f"CRS transform failed: {e}"})
                continue
        pt_geom = QgsGeometry.fromPointXY(pt)
        request = QgsFeatureRequest().setFilterRect(rect)
        feats = []
        for feat in layer.getFeatures(request):
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            if layer_tolerance > 0:
                if geom.distance(pt_geom) > layer_tolerance:
                    continue
            elif not geom.intersects(pt_geom):
                continue
            attrs = {f.name(): py_value(feat[f.name()]) for f in layer.fields()}
            attrs["_fid"] = feat.id()
            feats.append(attrs)
            if len(feats) >= limit:
                break
        if feats:
            results.append({"layer_id": layer.id(), "name": layer.name(), "features": feats, "count": len(feats)})
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
    expr = QgsExpression(expression)
    columns = sorted(str(c) for c in expr.referencedColumns())
    result = {
        "expression": expression,
        "valid": not expr.hasParserError(),
        "referenced_columns": columns,
    }
    with contextlib.suppress(Exception):
        result["referenced_functions"] = sorted(expr.referencedFunctions())
    with contextlib.suppress(Exception):
        result["referenced_variables"] = sorted(expr.referencedVariables())
    if expr.hasParserError():
        result["error"] = expr.parserErrorString()
        result["suggestion"] = "Fix the syntax: field names in double quotes, strings in single quotes."

    if args.get("layer_name"):
        layer, error = _vector(args["layer_name"])
        if error:
            return error
        fields = [f.name() for f in layer.fields()]
        unknown = [c for c in columns if c != "*" and c not in fields]
        result["layer"] = layer.name()
        result["unknown_columns"] = unknown
        if unknown:
            result["valid"] = False
            result["suggestion"] = f"Columns not on {layer.name()!r}: {unknown}. Fields: {fields[:20]}."
        if not expr.hasParserError():
            ctx = QgsExpressionContext()
            ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
            expr.prepare(ctx)
            if expr.hasEvalError():
                result["eval_error"] = expr.evalErrorString()
                result["valid"] = False
            else:
                for feat in layer.getFeatures(QgsFeatureRequest().setLimit(1)):
                    ctx.setFeature(feat)
                    value = expr.evaluate(ctx)
                    if expr.hasEvalError():
                        result["eval_error"] = expr.evalErrorString()
                        result["valid"] = False
                    else:
                        result["sample_value"] = py_value(value)
                        result["sample_fid"] = feat.id()
    return result





def _get_layer_extent(args: dict) -> dict:
    layer = resolve_layer(args["layer_name"])
    if layer is None:
        return layer_not_found(args["layer_name"])
    extent = layer.extent()
    crs = layer.crs()
    bounds = (extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum())


    if extent.isNull() or not all(math.isfinite(b) for b in bounds):
        return {
            "layer_id": layer.id(), "name": layer.name(), "empty": True,
            "xmin": None, "ymin": None, "xmax": None, "ymax": None,
            "crs": crs.authid(), "units": _units(crs),
        }
    return {
        "layer_id": layer.id(),
        "name": layer.name(),
        "xmin": bounds[0], "ymin": bounds[1], "xmax": bounds[2], "ymax": bounds[3],
        "width": extent.width(),
        "height": extent.height(),
        "center": [extent.center().x(), extent.center().y()],
        "crs": crs.authid(),
        "units": _units(crs),
        "is_geographic": crs.isGeographic(),
    }
