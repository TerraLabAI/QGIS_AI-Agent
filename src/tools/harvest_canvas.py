# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

from qgis.core import Qgis, QgsGeometry, QgsLabeling, QgsVectorLayer, QgsWkbTypes
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP
from qgis.utils import iface

from ..core.follow import hold_view
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .core_tools import _find_layer, _layer_not_found_error
from .feature_tools import _find_vector_layer


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
        result["labels_placed_last_render"] = placed
    return result


def _labels_placed(layer_id: str):





    try:
        results = iface.mapCanvas().labelingResults()
        if results is None:
            return None
        positions = results.allLabels()
        return sum(1 for position in positions
                   if position.layerID == layer_id and not getattr(position, "isUnplaced", False))
    except Exception:  # nosec B110
        return None


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
        return tool_error("Pass scale and/or rotation", "INVALID_ARGS", "scale is the denominator, e.g. 25000.")
    if wanted_scale is not None and float(wanted_scale) <= 0:
        return tool_error(f"scale must be a positive denominator, got {wanted_scale}", "INVALID_ARGS",
                          "Use 25000 for 1:25000.")
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
            "Pass a geometry of the layer's type.")
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
                          "Check the provider allows geometry changes (get_provider_capabilities).")
    if own_session and not layer.commitChanges():
        reasons = layer.commitErrors()
        layer.rollBack()
        return tool_error("; ".join(reasons) or "Commit failed", "EXECUTION_FAILED",
                          "Read the provider error; the change was rolled back.")
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
                          "The provider is read-only; export_layer to a GeoPackage first.")
    refusal = _write_geometries(layer, shapes, own_session=not session_open)
    if refusal:
        return refusal
    layer.updateExtents()
    layer.triggerRepaint()
    result = {"updated": len(shapes), "fids": list(shapes), "committed": not session_open}
    if session_open:
        result["note"] = "changed in the open edit session (not committed, commit or discard it yourself)"
    return result
