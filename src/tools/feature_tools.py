# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
import math

from qgis.core import (
    QgsCoordinateTransform,
    QgsExpression,
    QgsFeature,
    QgsFeatureRequest,
    QgsGeometry,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core.geometry_budget import VertexBudget
from ..core.qt_compat import enum_member
from ..core.serialization import size_budget
from ..core.tool_registry import Tool, ToolRegistry, coded_fact, tool_error
from . import vector_write
from .layer_lookup import _find_layer, _layer_not_found_error



_MAX_FEATURE_BATCH = 5000


def register_feature_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="add_features",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Add features to {layer_name}"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "features": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "geometry_wkt": {
                                "type": "string",
                            },
                            "attributes": {"type": "object"},
                        },
                    },
                },
            },
            "required": ["layer_name", "features"],
        },
        handler=_add_features,
    ))

    registry.register(Tool(
        name="update_features",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Update features of {layer_name}"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "updates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "fid": {"type": "integer"},
                            "attributes": {"type": "object"},
                        },
                        "required": ["fid", "attributes"],
                    },
                },
            },
            "required": ["layer_name", "updates"],
        },
        handler=_update_features,
    ))

    registry.register(Tool(
        name="delete_features",
        danger="destructive",
        label=QT_TRANSLATE_NOOP("AIAgent", "Delete features from {layer_name}"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "fids": {
                    "type": "array",
                    "items": {"type": "integer"},
                },
            },
            "required": ["layer_name", "fids"],
        },
        handler=_delete_features,
    ))

    registry.register(Tool(
        name="select_features",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Select features in {layer_name}"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "expression": {"type": "string"},
                "fids": {
                    "type": "array",
                    "items": {"type": "integer"},
                },
                "behavior": {"type": "string", "enum": ["replace", "add", "remove", "filter"]},
            },
            "required": ["layer_name"],
        },
        handler=_select_features,
    ))

    registry.register(Tool(
        name="get_selection",
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Read the selection[ of {layer_name}]"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
            },
            "required": ["layer_name"],
        },
        handler=_get_selection,
    ))

    registry.register(Tool(
        name="clear_selection",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Clear the selection[ of {layer_name}]"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
            },
            "required": [],
        },
        handler=_clear_selection,
    ))

    registry.register(Tool(
        name="select_by_attribute",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Select in {layer_name} where {field_name} {operator} {value}"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "field_name": {"type": "string"},
                "operator": {
                    "type": "string",
                    "enum": ["=", "!=", "<", ">", "<=", ">=", "contains", "starts_with", "ends_with"],
                },
                "value": {"type": ["string", "number"]},
                "behavior": {"type": "string", "enum": ["replace", "add", "remove", "filter"]},
            },
            "required": ["layer_name", "field_name", "operator", "value"],
        },
        handler=_select_by_attribute,
    ))

    registry.register(Tool(
        name="select_by_geometry",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Select in {layer_name} by shape"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "mode": {
                    "type": "string",
                    "enum": ["largest", "smallest", "intersecting", "inside", "touching"],
                },
                "reference_layer": {
                    "type": "string",
                },
                "behavior": {"type": "string", "enum": ["replace", "add", "remove", "filter"]},
            },
            "required": ["layer_name", "mode"],
        },
        handler=_select_by_geometry,
    ))


def _find_vector_layer(name_or_id: str):












    layer = _find_layer(name_or_id)
    if layer is None or not isinstance(layer, QgsVectorLayer):
        return None
    return layer


def _vector_layer(name_or_id: str):





    layer = _find_layer(name_or_id)
    if layer is None:
        return None, _layer_not_found_error(name_or_id)
    if not isinstance(layer, QgsVectorLayer):
        kind = type(layer).__name__.replace("Qgs", "").replace("Layer", "").lower() or "other"
        return None, {
            "_error": (f"Layer {layer.name()!r} is a {kind} layer, and this tool works on vector features."),
            "code": "INVALID_ARGS",
            "suggestion": ("A vector layer is needed. get_raster_band_stats and the raster tools read "
                           "and write pixels for a raster."),
        }
    return layer, None


def _unknown_fields_error(layer, unknown: list) -> dict:





    all_fields = sorted(f.name() for f in layer.fields())
    return {
        "_error": (
            f"Unknown field(s) {', '.join(repr(n) for n in unknown)} on layer {layer.name()!r}. "
            f"Available: {', '.join(repr(n) for n in all_fields)}"
        ),
        "_code": "INVALID_ARGS",
        "fields": all_fields,

        **coded_fact(hint="unknown_fields", unknown=sorted(unknown), layer=layer.name()),
    }


def _add_features(args: dict) -> dict:
    layer, error = _vector_layer(args["layer_name"])
    if error:
        return error

    features_data = args.get("features", [])
    if not features_data:
        return {"_error": "No features provided"}
    if len(features_data) > _MAX_FEATURE_BATCH:
        return {
            "_error": f"{len(features_data)} features given, above the {_MAX_FEATURE_BATCH} this tool "
                      "builds in one call.",
            "_code": "INVALID_ARGS",
            "suggestion": f"Batches of {_MAX_FEATURE_BATCH} or fewer work.",
        }




    available = {f.name() for f in layer.fields()}
    unknown = sorted({
        name
        for feat_data in features_data
        for name in feat_data.get("attributes", {})
        if name not in available
    })
    if unknown:
        return _unknown_fields_error(layer, unknown)


    new_features = []
    truncations: list = []
    for position, feat_data in enumerate(features_data):
        feature = QgsFeature(layer.fields())

        geom_wkt = feat_data.get("geometry_wkt")
        if geom_wkt:
            geom = QgsGeometry.fromWkt(geom_wkt)
            if geom.isNull():
                return {"_error": f"Invalid WKT geometry: {geom_wkt[:100]}", "code": "INVALID_ARGS",
                        "suggestion": ("valid WKT in the layer's CRS, for example "
                                       "'POINT(x y)' or 'POLYGON((x y, x y, x y, x y))'.")}



            problem = vector_write.geometry_problem(layer, geom, check_crs=position == 0)
            if problem is not None:
                problem["feature_index"] = position
                return problem
            feature.setGeometry(geom)

        attrs = feat_data.get("attributes", {})
        for field_name, value in attrs.items():
            idx = layer.fields().indexOf(field_name)
            if idx >= 0:
                feature.setAttribute(idx, value)
        if len(truncations) < 50:
            for warning in vector_write.value_warnings(layer, attrs):
                warning["feature_index"] = position
                truncations.append(warning)

        new_features.append(feature)




    if layer.isEditable():








        new_fids: list = []

        def _record(fid):
            new_fids.append(fid)

        layer.featureAdded.connect(_record)
        try:
            ok = layer.addFeatures(new_features)
        finally:
            layer.featureAdded.disconnect(_record)
        if not ok:
            return _write_refused(layer, "add features", len(new_features))
        layer.triggerRepaint()
        one_per_feature = len(set(new_fids)) == len(new_fids) == len(new_features)
        result = {
            "added": len(new_features),
            "fids": new_fids if one_per_feature else None,
            "committed": False,
            "note": (
                "added to the open edit session, not committed; qgis_edit_commit or a discard "
                "decides it. fids are the edit buffer's temporary ids and change once committed"
            ),
        }
        if truncations:
            result["truncated_values"] = truncations[:20]
        return result


    started, error = vector_write.open_edit(layer, "add features")
    if error:
        return error

    if not layer.addFeatures(new_features):
        vector_write.abort_edit(layer, started)
        return _write_refused(layer, "add features", len(new_features))



    committed_feats = []

    def _capture(_layer_id, feats):
        committed_feats.extend(feats)

    layer.committedFeaturesAdded.connect(_capture)
    try:
        failure = vector_write.finish_edit(layer, started, f"The {len(new_features)} feature(s)")
    finally:
        layer.committedFeaturesAdded.disconnect(_capture)

    if failure is not None:
        return failure

    if committed_feats:
        result = {"added": len(new_features), "fids": [f.id() for f in committed_feats], "committed": True}
        if truncations:
            result["truncated_values"] = truncations[:20]
        return result




    result = {"added": len(new_features), "committed": True, "fids": None,
              "fids_unresolved": True,
              "note": ("this provider did not report the ids it assigned; get_features reads them "
                       "back if needed")}
    if truncations:
        result["truncated_values"] = truncations[:20]
    return result


def _write_refused(layer, action: str, count: int) -> dict:






    error = vector_write.cannot_edit_error(layer, action)
    name = error.get("layer") or "the layer"
    head = f"The layer {name!r} refused to {action} ({count} asked for)."
    if error.get("reason") == "start_editing_refused":


        error["_error"] = (f"{head} The edit session opened, the provider "
                           f"({error.get('provider') or 'unknown'}"
                           f"{'; ' + str(error.get('storage')) if error.get('storage') else ''}) then refused "
                           "the write and reported no reason.")
        error["reason"] = "write_refused"



        error.update(coded_fact(hint="write_refused_no_reason", layer=name, action=action, count=count))
    else:
        error["_error"] = f"{head} {error.get('_error', '')}"
    return error


def _update_features(args: dict) -> dict:
    layer, error = _vector_layer(args["layer_name"])
    if error:
        return error

    updates = args.get("updates", [])
    if not updates:
        return {"_error": "No updates provided"}
    if len(updates) > _MAX_FEATURE_BATCH:


        return {
            "_error": f"{len(updates)} updates given, above the {_MAX_FEATURE_BATCH} this tool writes in one call.",
            "_code": "INVALID_ARGS",
            "suggestion": f"Batches of {_MAX_FEATURE_BATCH} or fewer work.",
        }




    empty_updates = [u for u in updates if not u.get("attributes")]
    updates = [u for u in updates if u.get("attributes")]
    if not updates:
        return {"updated": 0, "noop": len(empty_updates),
                "note": "every update carried no attributes, so nothing was written"}



    available = {f.name() for f in layer.fields()}
    unknown = sorted({
        name
        for update in updates
        for name in update.get("attributes", {})
        if name not in available
    })
    if unknown:
        return _unknown_fields_error(layer, unknown)

    started, error = vector_write.open_edit(layer, "change attributes")
    if error:
        return error

    truncations: list = []
    updated = 0
    partial: list = []
    not_found: list = []
    refused: list = []
    unknown_fields: dict = {}
    for update in updates:
        fid = update["fid"]
        feat = layer.getFeature(fid)
        if not feat.isValid():


            not_found.append(fid)
            continue
        attrs = update.get("attributes", {})




        wrote = 0
        if len(truncations) < 50:
            for warning in vector_write.value_warnings(layer, attrs):
                warning["fid"] = fid
                truncations.append(warning)
        for field_name, value in attrs.items():
            idx = layer.fields().indexOf(field_name)
            if idx < 0:
                unknown_fields.setdefault(field_name, [])
                unknown_fields[field_name].append(fid)
            elif layer.changeAttributeValue(fid, idx, value):
                wrote += 1
            else:
                refused.append({"fid": fid, "field": field_name})



        if wrote == len(attrs):
            updated += 1
        elif wrote:
            partial.append(fid)

    failure = vector_write.finish_edit(layer, started, f"The {updated + len(partial)} attribute change(s)")
    if failure is not None:
        return failure

    result: dict = {"updated": updated}
    if not started:
        result["committed"] = False
        result["note"] = "written into the open edit session, not committed; qgis_edit_commit decides it"
    if truncations:
        result["truncated_values"] = truncations[:20]
    if partial:
        result["partially_updated"] = partial[:20]
        result["partially_updated_count"] = len(partial)
    if empty_updates:
        result["noop"] = len(empty_updates)
    if not_found:
        result["not_found"] = not_found
        if not updated and not partial:
            result["_error"] = "None of the requested feature IDs exist in this layer"
            result["code"] = "INVALID_ARGS"
            result["suggestion"] = "Feature ids come from the _fid field of get_features."
            return result
    if unknown_fields:
        result["unknown_fields"] = sorted(unknown_fields)
        result["fields"] = [f.name() for f in layer.fields()]
        if not updated and not partial:
            result["_error"] = (f"The layer {layer.name()!r} has none of the fields "
                                f"{sorted(unknown_fields)}, so nothing was written.")
            result["code"] = "INVALID_ARGS"
            result["suggestion"] = "'fields' lists the names; add_field adds one."
            return result
    if refused:
        result["refused"] = refused[:20]
        if not updated and not partial:
            result["_error"] = (f"The provider of {layer.name()!r} refused every attribute write "
                                f"({len(refused)} of them); the layer reported no error.")
            result["code"] = "INVALID_ARGS"
            result["suggestion"] = ("The source is probably read-only or the value does not fit the field. "
                                    "export_layer to GeoPackage gives an editable copy.")
    return result


def _delete_features(args: dict) -> dict:
    layer, error = _vector_layer(args["layer_name"])
    if error:
        return error

    fids = args.get("fids", [])
    if not fids:
        return {"_error": "No feature IDs provided", "code": "INVALID_ARGS",
                "suggestion": "Feature ids come from the _fid field of get_features."}
    if len(fids) > _MAX_FEATURE_BATCH:
        return {
            "_error": f"{len(fids)} feature ids given, above the {_MAX_FEATURE_BATCH} this tool deletes in one call.",
            "_code": "INVALID_ARGS",
            "suggestion": f"Batches of {_MAX_FEATURE_BATCH} or fewer work.",
        }




    existing = {
        f.id()
        for f in layer.getFeatures(
            QgsFeatureRequest().setFilterFids(fids).setSubsetOfAttributes([])
            .setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry"))
        )
    }


    wanted = list(dict.fromkeys(fids))
    duplicates = len(fids) - len(wanted)
    to_delete = [fid for fid in wanted if fid in existing]
    not_found = [fid for fid in wanted if fid not in existing]

    if not to_delete:
        return {
            "deleted": 0,
            "not_found": not_found,
            "_error": "None of the requested feature IDs exist in this layer",
            "code": "INVALID_ARGS",
            "suggestion": ("Feature ids come from the _fid field of get_features; a commit renumbers "
                           "the ids an open edit session handed out."),
        }

    started, error = vector_write.open_edit(layer, "delete features")
    if error:
        return error

    if not layer.deleteFeatures(to_delete):
        vector_write.abort_edit(layer, started)
        return _write_refused(layer, "delete features", len(to_delete))

    failure = vector_write.finish_edit(layer, started, f"The {len(to_delete)} deletion(s)")
    if failure is not None:
        return failure

    result = {"deleted": len(to_delete)}
    if duplicates:
        result["duplicate_ids_ignored"] = duplicates
    if not_found:
        result["not_found"] = not_found
    if not started:
        result["committed"] = False
        result["note"] = "deleted in the open edit session (not committed)"
    return result






_SELECT_BEHAVIORS = {
    "replace": "SetSelection",
    "add": "AddToSelection",
    "remove": "RemoveFromSelection",
    "filter": "IntersectSelection",
}


def _select_behavior(args: dict):

    behavior = args.get("behavior") or "replace"
    member = _SELECT_BEHAVIORS.get(behavior) if isinstance(behavior, str) else None
    if member is None:
        return None, None, tool_error(
            f"behavior {behavior!r} is not one of {', '.join(_SELECT_BEHAVIORS)}. Nothing was selected.",
            "INVALID_ARGS",
            "replace selects only the matches, add adds them to the current selection, remove takes "
            "them out of it, filter keeps the selected features that also match.")
    if behavior == "replace":

        return behavior, None, None
    value = None
    try:
        from qgis.core import Qgis
        value = enum_member(Qgis, "SelectBehavior", member, None)
    except ImportError:
        value = None
    if value is None:
        value = enum_member(QgsVectorLayer, "SelectBehavior", member)
    return behavior, value, None


def _select_ids(layer, ids, behavior) -> None:
    if behavior is None:
        layer.selectByIds(ids)
    else:
        layer.selectByIds(ids, behavior)


def _select_expression(layer, expression: str, behavior) -> None:
    if behavior is None:
        layer.selectByExpression(expression)
    else:
        layer.selectByExpression(expression, behavior)


def _with_behavior(out: dict, behavior: str, previously: int) -> dict:

    if behavior != "replace":
        out["behavior"] = behavior
        out["previously_selected"] = previously
    return out


def _select_features(args: dict) -> dict:
    layer, error = _vector_layer(args["layer_name"])
    if error:
        return error
    behavior_name, behavior, error = _select_behavior(args)
    if error:
        return error
    previously = layer.selectedFeatureCount()

    expression = args.get("expression")
    fids = args.get("fids")

    if not fids and isinstance(fids, (list, tuple)) and expression:




        fids = None
    if fids is not None and expression:
        return {"_error": "'fids' and 'expression' both given: which one wins was never defined.",
                "_code": "INVALID_ARGS"}
    if fids is not None:



        _select_ids(layer, list(fids), behavior)
    elif expression:
        expr = QgsExpression(expression)
        if expr.hasParserError():
            return {"_error": f"Invalid expression: {expr.parserErrorString()}"}
        _select_expression(layer, expression, behavior)
    else:
        return {"_error": "'expression' or 'fids' is required"}

    return _with_behavior({"selected_count": layer.selectedFeatureCount()}, behavior_name, previously)


def _get_selection(args: dict) -> dict:
    layer, error = _vector_layer(args["layer_name"])
    if error:
        return error



    all_ids = list(layer.selectedFeatureIds())
    total = len(all_ids)
    sample_ids = all_ids[:50]

    features = []
    if sample_ids:
        field_names = [f.name() for f in layer.fields()]

        request = (QgsFeatureRequest().setFilterFids(sample_ids)
                   .setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry")))
        for feat in layer.getFeatures(request):
            row = {"_fid": feat.id()}
            for name in field_names:
                value = feat[name]
                if isinstance(value, str) and len(value) > 120:
                    value = value[:120] + "…"
                row[name] = value
            features.append(row)



    features, _dropped = size_budget(features, 4_000)
    out = {"layer": args["layer_name"], "selected_count": total, "features": features}
    if total > len(features):
        out["omitted"] = total - len(features)
    return out


def _clear_selection(args: dict) -> dict:
    layer_name = args.get("layer_name")
    if layer_name:
        layer, error = _vector_layer(layer_name)
        if error:
            return error
        layer.removeSelection()
        return {"cleared": layer_name}
    for layer in QgsProject.instance().mapLayers().values():
        if isinstance(layer, QgsVectorLayer):
            layer.removeSelection()
    return {"cleared": "all"}


def _field_is_text(layer, index: int) -> bool:





    try:
        field = layer.fields().at(index)
        type_name = str(field.typeName() or "").lower()
        if type_name:
            return any(word in type_name for word in ("string", "text", "char", "varchar"))
        return str(field.type()).lower().find("string") >= 0
    except Exception:  # noqa: BLE001
        return False


def _select_by_attribute(args: dict) -> dict:
    from .layer_lookup import _field_not_found_error, _layer_not_found_error
    layer = _find_vector_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])

    behavior_name, behavior, error = _select_behavior(args)
    if error:
        return error
    field_name = args["field_name"]
    operator = args["operator"]
    value = args["value"]

    field_index = layer.fields().indexOf(field_name)
    if field_index < 0:
        return _field_not_found_error(layer, field_name)
    field_is_text = _field_is_text(layer, field_index)



    field_ref = QgsExpression.quotedColumnRef(field_name)
    if operator in ("contains", "starts_with", "ends_with"):






        literal = QgsExpression.quotedString(str(value))
        if operator == "contains":
            expr_str = f"strpos({field_ref}, {literal}) > 0"
        elif operator == "starts_with":
            expr_str = f"left({field_ref}, length({literal})) = {literal}"
        else:
            expr_str = f"right({field_ref}, length({literal})) = {literal}"
    elif field_is_text:





        expr_str = f"{field_ref} {operator} {QgsExpression.quotedString(str(value))}"
    else:
        if isinstance(value, bool):
            expr_str = f"{field_ref} {operator} {int(value)}"
        elif isinstance(value, int):


            expr_str = f"{field_ref} {operator} {value}"
        else:
            try:
                numeric_val = float(value)
                if not math.isfinite(numeric_val):
                    return {"_error": f"{value!r} is not a finite number, so no comparison can be built from it.",
                            "code": "INVALID_ARGS",
                            "suggestion": "a real number, or 'is null' through evaluate_expression."}
                if numeric_val == int(numeric_val):
                    expr_str = f"{field_ref} {operator} {int(numeric_val)}"
                else:
                    expr_str = f"{field_ref} {operator} {numeric_val}"
            except (ValueError, TypeError, OverflowError):
                expr_str = f"{field_ref} {operator} {QgsExpression.quotedString(str(value))}"

    expr = QgsExpression(expr_str)
    if expr.hasParserError():
        return {"_error": f"The expression built from these arguments does not parse "
                          f"({expr_str}): {expr.parserErrorString()}",
                "code": "INVALID_ARGS",
                "suggestion": "field_name, operator or value may be the cause; evaluate_expression tests "
                              "one without changing the selection."}

    previously = layer.selectedFeatureCount()
    _select_expression(layer, expr_str, behavior)


    field = layer.fields().at(field_index)
    if field_is_text:
        field_kind = "text"
    elif field.isNumeric():
        field_kind = "number"
    else:
        field_kind = str(field.typeName() or "number").lower()
    return _with_behavior(
        {"selected_count": layer.selectedFeatureCount(), "expression": expr_str, "field_type": field_kind},
        behavior_name, previously)


def _too_many_vertices(too_big, budget) -> str:
    return (f"one geometry of {too_big:,} vertices" if too_big
            else f"more than {budget.total:,} vertices in total")


def _select_by_geometry(args: dict) -> dict:
    layer, error = _vector_layer(args["layer_name"])
    if error:
        return error

    behavior_name, behavior, error = _select_behavior(args)
    if error:
        return error
    previously = layer.selectedFeatureCount()
    mode = args["mode"]

    if mode in ("largest", "smallest"):
        geom_type = layer.geometryType()
        if geom_type == enum_member(QgsWkbTypes, "GeometryType", "PointGeometry"):


            return {"_error": f"{mode!r} ranks features by area or length, and a point layer has neither.",
                    "code": "INVALID_ARGS",
                    "suggestion": ("select_by_attribute ranks by a field; an extent-based mode with a "
                                   "reference layer is another.")}
        best_fid = None
        best_size = None
        polygons = geom_type == enum_member(QgsWkbTypes, "GeometryType", "PolygonGeometry")





        calculator = None
        if layer.crs().isGeographic():
            from qgis.core import QgsDistanceArea

            calculator = QgsDistanceArea()
            calculator.setSourceCrs(layer.crs(), QgsProject.instance().transformContext())
            ellipsoid = str(QgsProject.instance().ellipsoid() or "")
            calculator.setEllipsoid(ellipsoid if ellipsoid.upper() not in ("", "NONE") else "WGS84")


        for feat in layer.getFeatures(QgsFeatureRequest().setSubsetOfAttributes([])):
            geom = feat.geometry()
            if geom.isNull():
                continue
            if calculator is not None:
                size = calculator.measureArea(geom) if polygons else calculator.measureLength(geom)
            else:
                size = geom.area() if polygons else geom.length()

            if best_size is None or mode == "largest" and size > best_size or mode == "smallest" and size < best_size:
                best_size = size
                best_fid = feat.id()

        if best_fid is None:
            return {"_error": "No features with valid geometry found"}

        _select_ids(layer, [best_fid], behavior)
        if calculator is not None:
            units = "square metres" if polygons else "metres"
        else:
            try:
                from qgis.core import QgsUnitTypes

                units = QgsUnitTypes.toString(layer.crs().mapUnits()) + (" squared" if polygons else "")
            except Exception:  # noqa: BLE001
                units = "layer CRS units" + (" squared" if polygons else "")
        return _with_behavior({"selected_count": layer.selectedFeatureCount(), "mode": mode, "size": best_size,
                               "size_units": units}, behavior_name, previously)

    ref_name = args.get("reference_layer")
    if not ref_name:
        return {"_error": f"reference_layer is required for mode '{mode}'", "code": "INVALID_ARGS",
                "suggestion": "reference_layer is the layer the selection is measured against."}

    ref_layer, ref_error = _vector_layer(ref_name)
    if ref_error:


        ref_error["_error"] = f"reference_layer: {ref_error['_error']}"
        return ref_error





    budget = VertexBudget()
    ref_geoms = []
    for f in ref_layer.getFeatures(QgsFeatureRequest().setSubsetOfAttributes([])):
        geom = f.geometry()
        if geom.isNull():
            continue
        too_big = budget.oversize(geom)
        if too_big or budget.exhausted():
            return tool_error(
                f"Reference layer {ref_name!r} is too detailed to select against on this machine: "
                f"{_too_many_vertices(too_big, budget)}. Nothing was selected.",
                "INVALID_ARGS",
                "native:simplifygeometries or native:dissolve through run_processing with async true "
                "simplifies it first; native:extractbylocation runs in the background.")
        ref_geoms.append(geom)
    if not ref_geoms:
        return {"_error": "Reference layer has no valid geometries"}

    ref_geom = QgsGeometry.unaryUnion(ref_geoms)
    if ref_geom.isNull():
        return {"_error": "Reference layer has no valid geometries"}




    if ref_layer.crs() != layer.crs():
        transform = QgsCoordinateTransform(
            ref_layer.crs(), layer.crs(), QgsProject.instance().transformContext()
        )
        try:
            result = ref_geom.transform(transform)
        except Exception as exc:
            return {"_error": f"Failed to reproject reference geometry: {exc}"}
        if result != 0:
            return {"_error": (
                f"Failed to reproject reference geometry from "
                f"{ref_layer.crs().authid()} to {layer.crs().authid()}"
            )}







    engine = None
    try:
        engine = QgsGeometry.createGeometryEngine(ref_geom.constGet())
        engine.prepareGeometry()
    except Exception:  # noqa: BLE001
        engine = None

    def _matches(geom) -> bool:
        if engine is not None:
            inner = geom.constGet()
            if mode == "intersecting":
                return bool(engine.intersects(inner))
            if mode == "inside":
                return bool(engine.contains(inner))
            return bool(engine.touches(inner))
        return bool(
            mode == "intersecting" and geom.intersects(ref_geom)
            or mode == "inside" and geom.within(ref_geom)
            or mode == "touching" and geom.touches(ref_geom)
        )

    selected_ids = []
    request = (
        QgsFeatureRequest()
        .setFilterRect(ref_geom.boundingBox())
        .setSubsetOfAttributes([])
    )
    for feat in layer.getFeatures(request):
        stopped = budget.exhausted()
        if stopped:
            return tool_error(
                f"select_by_geometry stopped after {len(selected_ids)} matches to keep QGIS responsive "
                f"({budget.stop_reason(stopped)} reached). Nothing was selected.",
                "INVALID_ARGS",
                "native:extractbylocation or native:selectbylocation through run_processing with async "
                "true does the same test in the background.")
        geom = feat.geometry()
        if geom.isNull():
            continue
        if budget.oversize(geom):

            budget.walked += budget.per_geometry
        if _matches(geom):
            selected_ids.append(feat.id())

    _select_ids(layer, selected_ids, behavior)
    return _with_behavior({"selected_count": layer.selectedFeatureCount(), "mode": mode, "reference_layer": ref_name},
                          behavior_name, previously)
