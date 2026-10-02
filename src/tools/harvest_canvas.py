# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsFeatureRequest,
    QgsGeometry,
    QgsLabeling,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP
from qgis.utils import iface

from ..core.follow import hold_view
from ..core.layer_order import is_remote_vector
from ..core.qt_compat import enum_member
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .feature_tools import _find_vector_layer
from .layer_lookup import _find_layer, _layer_not_found_error


def register_harvest_canvas_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="get_layer_labeling",
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Read the labels of {layer_name}"),
        input_schema={
            "type": "object",
            "properties": {"layer_name": {"type": "string"}},
            "required": ["layer_name"],
        },
        handler=_get_layer_labeling,
    ))

    registry.register(Tool(
        name="get_canvas_scale",
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Read the map scale"),
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_get_canvas_scale,
    ))

    registry.register(Tool(
        name="set_canvas_scale",
        sets_view=True,
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Set the map scale"),
        input_schema={
            "type": "object",
            "properties": {
                "scale": {"type": "number", "minimum": 0.001, "maximum": 1000000000000},
                "rotation": {"type": "number", "minimum": -360000, "maximum": 360000},
            },
            "required": [],
        },
        handler=_set_canvas_scale,
    ))

    registry.register(Tool(
        name="update_feature_geometry",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Reshape a feature of {layer_name}"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "updates": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 5000,
                    "items": {
                        "type": "object",
                        "properties": {
                            "fid": {"type": "integer"},
                            "geometry_wkt": {"type": "string"},
                        },
                        "required": ["fid", "geometry_wkt"],
                    },
                },
            },
            "required": ["layer_name", "updates"],
        },
        handler=_update_feature_geometry,
    ))






def _enum_name(value) -> str:
    return getattr(value, "name", None) or str(value)


def _flag_names(flags, enum, names) -> list:

    out = []
    for name in names:
        member = getattr(enum, name, None)
        try:
            if member is not None and flags & member:
                out.append(name)
        except TypeError:
            continue
    return out


def _label_settings_summary(settings) -> dict:
    text_format = settings.format()
    summary = {
        "field": settings.fieldName,
        "is_expression": bool(settings.isExpression),
        "font_family": text_format.font().family(),
        "font_size": text_format.size(),
        "size_unit": _enum_name(text_format.sizeUnit()),
        "color": text_format.color().name(),
        "placement": _enum_name(settings.placement),
        "priority": settings.priority,
    }




    try:
        buffer = text_format.buffer()
        summary["buffer"] = {"enabled": buffer.enabled(), "size": buffer.size(),
                             "size_unit": _enum_name(buffer.sizeUnit()), "color": buffer.color().name()}
    except Exception:  # nosec B110
        pass

    try:
        summary["distance"] = settings.dist
        summary["distance_unit"] = _enum_name(settings.distUnits)
        placement_settings = settings.placementSettings()
        summary["overlap_handling"] = _enum_name(placement_settings.overlapHandling())
        summary["allow_degraded_placement"] = bool(placement_settings.allowDegradedPlacement())
        summary["polygon_placement"] = _flag_names(settings.polygonPlacementFlags(), Qgis.LabelPolygonPlacementFlag,
                                                   ("AllowPlacementOutsideOfPolygon", "AllowPlacementInsideOfPolygon"))
        line_settings = settings.lineSettings()
        summary["line_placement"] = _flag_names(line_settings.placementFlags(), QgsLabeling.LinePlacementFlag,
                                                ("OnLine", "AboveLine", "BelowLine", "MapOrientationIndependent"))
        obstacle_settings = settings.obstacleSettings()
        summary["is_obstacle"] = bool(obstacle_settings.isObstacle())
    except Exception:  # nosec B110
        pass
    try:
        summary["scale_visibility"] = bool(settings.scaleVisibility)
        if settings.scaleVisibility:
            summary["minimum_scale"] = settings.minimumScale
            summary["maximum_scale"] = settings.maximumScale
    except Exception:  # nosec B110
        pass
    return summary


def _get_layer_labeling(args: dict) -> dict:
    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])
    if not isinstance(layer, QgsVectorLayer):
        return tool_error(f"Layer {args['layer_name']!r} is not a vector layer", "INVALID_ARGS",
                          "Labels apply to vector layers only.")
    result = {"layer": layer.name(), "layer_id": layer.id(), "enabled": layer.labelsEnabled()}
    labeling = layer.labeling()
    if labeling is None:
        result["type"] = None
        result["note"] = "no labeling configured; set_layer_labels creates one"
        return result
    result["type"] = labeling.type()
    if labeling.type() == "rule-based":
        rules = []
        try:
            for rule in labeling.rootRule().children()[:20]:
                entry = {"description": rule.description(), "filter": rule.filterExpression(), "active": rule.active()}
                if rule.settings() is not None:
                    entry.update(_label_settings_summary(rule.settings()))
                rules.append(entry)
        except Exception:  # nosec B110
            pass
        result["rules"] = rules
        result["rule_count"] = len(rules)
    else:
        result.update(_label_settings_summary(labeling.settings()))
    placed = _labels_placed(layer.id())
    if placed is not None:
        result["labels_placed_last_render"] = placed[0]
        if placed[2] is not None:
            result["labels_overlapping_last_render"] = placed[2]

        settings = labeling.settings() if labeling.type() != "rule-based" else None
        result.update(_labels_in_view(layer, settings, placed[1]))
    return result


def _labels_placed(layer_id: str, results=None):













    try:
        if results is None:
            results = iface.mapCanvas().labelingResults()
        if results is None:
            return None
        positions = [position for position in results.allLabels()
                     if not getattr(position, "isUnplaced", False)]
        groups = {}
        for index, position in enumerate(positions):
            linked = getattr(position, "groupedLabelId", 0)
            groups.setdefault((position.layerID, linked) if linked else index, []).append(position)
        labels = list(groups.values())
        mine = [parts for parts in labels if parts[0].layerID == layer_id]
        return (len(mine), {parts[0].featureId for parts in mine},
                _overlapping(labels, layer_id) if len(positions) <= _LABEL_OVERLAP_CAP else None)
    except Exception:  # nosec B110
        return None




_LABEL_OVERLAP_CAP = 1500


def _drawn_shape(position):


    shape = getattr(position, "labelGeometry", None)
    if shape is not None and not shape.isEmpty():
        return shape
    corners = list(getattr(position, "cornerPoints", None) or [])
    if len(corners) >= 3:
        return QgsGeometry.fromPolygonXY([corners + corners[:1]])
    return QgsGeometry.fromRect(position.labelRect)


def _overlapping(labels: list, layer_id: str) -> int:



    parts = sorted((part.labelRect.xMinimum(), part.labelRect.yMinimum(), part.labelRect.xMaximum(),
                    part.labelRect.yMaximum(), label, slot)
                   for label, label_parts in enumerate(labels) for slot, part in enumerate(label_parts))
    shapes = {}

    def shape(entry):
        key = (entry[4], entry[5])
        if key not in shapes:
            shapes[key] = _drawn_shape(labels[entry[4]][entry[5]])
        return shapes[key]

    hit = set()
    for i, a in enumerate(parts):
        for j in range(i + 1, len(parts)):
            b = parts[j]
            if b[0] >= a[2]:
                break
            if a[4] == b[4] or (a[4] in hit and b[4] in hit) or not (a[1] < b[3] and b[1] < a[3]):
                continue
            if shape(a).intersection(shape(b)).area() > 0:
                hit.update((a[4], b[4]))
    return sum(1 for label in hit if labels[label][0].layerID == layer_id)





_LABEL_VIEW_FEATURES = 2000


def _labels_in_view(layer, settings, drawn: set, view=None) -> dict:




    if is_remote_vector(layer) or layer.featureCount() > _LABEL_VIEW_FEATURES:
        return {}
    try:
        if view is None:
            canvas = iface.mapCanvas()
            view = (canvas.extent(), canvas.mapSettings().destinationCrs())
        extent = QgsCoordinateTransform(view[1], layer.crs(), QgsProject.instance()).transformBoundingBox(view[0])
        request = QgsFeatureRequest().setFilterRect(extent)
        request.setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry"))
        text = None
        if settings is not None and settings.fieldName:
            text = QgsExpression(settings.fieldName if settings.isExpression
                                 else QgsExpression.quotedColumnRef(settings.fieldName))
        context = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
        in_view, missing = 0, []
        for feature in layer.getFeatures(request):
            in_view += 1
            if feature.id() in drawn or text is None or len(missing) >= 5:
                continue
            context.setFeature(feature)
            value = text.evaluate(context)
            if value is not None and not (hasattr(value, "isNull") and value.isNull()) and str(value).strip():
                missing.append(str(value)[:60])
    except Exception:  # nosec B110
        return {}
    out = {"features_in_view": in_view}
    if missing:
        out["not_drawn"] = missing
    return out


def _view_state(canvas) -> dict:

    return {"scale": canvas.scale(), "rotation": canvas.rotation(), "magnification": canvas.magnificationFactor()}


def _get_canvas_scale(args: dict) -> dict:
    canvas = iface.mapCanvas()
    state = _view_state(canvas)
    state["crs"] = canvas.mapSettings().destinationCrs().authid()
    return state


def _set_canvas_scale(args: dict) -> dict:
    wanted_scale = args.get("scale")
    wanted_rotation = args.get("rotation")
    if wanted_scale is None and wanted_rotation is None:
        return tool_error("No scale or rotation given", "INVALID_ARGS", "scale is the denominator, e.g. 25000.")
    if wanted_scale is not None and float(wanted_scale) <= 0:
        return tool_error(f"scale must be a positive denominator, got {wanted_scale}", "INVALID_ARGS",
                          "25000 means 1:25000.")
    canvas = iface.mapCanvas()
    if wanted_scale is not None:
        canvas.zoomScale(float(wanted_scale))
    if wanted_rotation is not None:

        canvas.setRotation(float(wanted_rotation) % 360.0)
    canvas.refresh()
    answer = {}
    if wanted_scale is not None:
        reprojected = hold_view(canvas)
        if reprojected:
            answer["project_crs_changed"] = reprojected
    answer.update(_view_state(canvas))
    return answer


def _checked_geometry(layer, position: int, update: dict):

    fid = update["fid"]
    if not layer.getFeature(fid).isValid():
        return tool_error(f"Update {position}: no feature with fid {fid} in layer {layer.name()!r}", "INVALID_ARGS",
                          "Feature ids come from the _fid field of get_features.")
    wkt = update["geometry_wkt"]
    shape = QgsGeometry.fromWkt(wkt)
    if shape.isNull():
        return tool_error(f"Update {position}: invalid geometry_wkt: {wkt[:100]!r}", "INVALID_ARGS",
                          "WKT axis order is (x y) in the layer's CRS.")
    if shape.type() != layer.geometryType():
        return tool_error(
            f"Update {position}: geometry is {QgsWkbTypes.displayString(shape.wkbType())}, the layer holds "
            f"{QgsWkbTypes.displayString(layer.wkbType())}", "INVALID_ARGS",
            "geometry_wkt takes the layer's type.")
    if QgsWkbTypes.isMultiType(layer.wkbType()) and not QgsWkbTypes.isMultiType(shape.wkbType()):
        shape.convertToMultiType()
    return shape


def _write_geometries(layer, shapes: dict, own_session: bool):

    for fid, shape in shapes.items():
        if layer.changeGeometry(fid, shape):
            continue
        if own_session:
            layer.rollBack()
        return tool_error(f"Failed to update the geometry of fid {fid}", "EXECUTION_FAILED",
                          "get_provider_capabilities shows if geometry changes are allowed.")
    if own_session and not layer.commitChanges():
        reasons = layer.commitErrors()
        layer.rollBack()
        return tool_error("; ".join(reasons) or "Commit failed", "EXECUTION_FAILED",
                          "The change was rolled back.")
    return None


def _update_feature_geometry(args: dict) -> dict:
    layer = _find_vector_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])

    shapes = {}
    for position, update in enumerate(args["updates"]):
        shape = _checked_geometry(layer, position, update)
        if isinstance(shape, dict):
            return shape
        shapes[update["fid"]] = shape

    session_open = layer.isEditable()
    if not session_open and not layer.startEditing():
        return tool_error("Cannot start editing on this layer", "EXECUTION_FAILED",
                          "The provider is read-only. export_layer makes a writable copy.")
    refusal = _write_geometries(layer, shapes, own_session=not session_open)
    if refusal:
        return refusal
    layer.updateExtents()
    layer.triggerRepaint()
    result = {"updated": len(shapes), "fids": list(shapes), "committed": not session_open}
    if session_open:
        result["note"] = "changed in the open edit session; not committed yet"
    return result
