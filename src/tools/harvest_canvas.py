# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Canvas scale and rotation, label read-back, and feature geometry updates."""








from __future__ import annotations

from qgis.core import QgsGeometry, QgsVectorLayer, QgsWkbTypes
from qgis.utils import iface

from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .core_tools import _find_layer, _layer_not_found_error
from .feature_tools import _find_vector_layer


def register_harvest_canvas_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="get_layer_labeling",
        input_schema={
            "type": "object",
            "properties": {"layer_name": {"type": "string"}},
            "required": ["layer_name"],
        },
        handler=_get_layer_labeling,
    ))

    registry.register(Tool(
        name="get_canvas_scale",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_get_canvas_scale,
    ))

    registry.register(Tool(
        name="set_canvas_scale",
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
        summary["buffer"] = {"enabled": buffer.enabled(), "size": buffer.size(), "color": buffer.color().name()}
    except Exception:  # nosec B110 - optional label data is unavailable
        pass
    try:
        summary["scale_visibility"] = bool(settings.scaleVisibility)
        if settings.scaleVisibility:
            summary["minimum_scale"] = settings.minimumScale
            summary["maximum_scale"] = settings.maximumScale
    except Exception:  # nosec B110 - optional label data is unavailable
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
        except Exception:  # nosec B110 - optional label data is unavailable
            pass
        result["rules"] = rules
        result["rule_count"] = len(rules)
    else:
        result.update(_label_settings_summary(labeling.settings()))
    return result


def _get_canvas_scale(args: dict) -> dict:
    canvas = iface.mapCanvas()
    return {
        "scale": canvas.scale(),
        "rotation": canvas.rotation(),
        "magnification": canvas.magnificationFactor(),
        "crs": canvas.mapSettings().destinationCrs().authid(),
    }


def _set_canvas_scale(args: dict) -> dict:
    scale, rotation = args.get("scale"), args.get("rotation")
    if scale is None and rotation is None:
        return tool_error("Pass scale and/or rotation", "INVALID_ARGS", "scale is the denominator, e.g. 25000.")
    if scale is not None and float(scale) <= 0:
        return tool_error(f"scale must be a positive denominator, got {scale}", "INVALID_ARGS",
                          "Use 25000 for 1:25000.")
    canvas = iface.mapCanvas()
    if scale is not None:
        canvas.zoomScale(float(scale))
    if rotation is not None:
        canvas.setRotation(float(rotation) % 360.0)
    canvas.refresh()
    return {"scale": canvas.scale(), "rotation": canvas.rotation(),
            "magnification": canvas.magnificationFactor()}


def _update_feature_geometry(args: dict) -> dict:
    layer = _find_vector_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])
    updates = args["updates"]
    layer_multi = QgsWkbTypes.isMultiType(layer.wkbType())
    layer_type = layer.geometryType()

    geometries = {}
    for index, update in enumerate(updates):
        fid = update["fid"]
        if not layer.getFeature(fid).isValid():
            return tool_error(f"Update {index}: no feature with fid {fid} in layer {layer.name()!r}", "INVALID_ARGS",
                              "Feature ids come from the _fid field of get_features.")
        geom = QgsGeometry.fromWkt(update["geometry_wkt"])
        if geom.isNull():
            return tool_error(f"Update {index}: invalid geometry_wkt: {update['geometry_wkt'][:100]!r}", "INVALID_ARGS",
                              "WKT axis order is (x y) in the layer's CRS.")
        if geom.type() != layer_type:
            return tool_error(
                f"Update {index}: geometry is {QgsWkbTypes.displayString(geom.wkbType())}, the layer holds "
                f"{QgsWkbTypes.displayString(layer.wkbType())}", "INVALID_ARGS",
                "Pass a geometry of the layer's type.")
        if layer_multi and not QgsWkbTypes.isMultiType(geom.wkbType()):
            geom.convertToMultiType()
        geometries[fid] = geom

    was_editing = layer.isEditable()
    if not was_editing and not layer.startEditing():
        return tool_error("Cannot start editing on this layer", "EXECUTION_FAILED",
                          "The provider is read-only; export_layer to a GeoPackage first.")
    for fid, geom in geometries.items():
        if not layer.changeGeometry(fid, geom):
            if not was_editing:
                layer.rollBack()
            return tool_error(f"Failed to update the geometry of fid {fid}", "EXECUTION_FAILED",
                              "Check the provider allows geometry changes (get_provider_capabilities).")
    if not was_editing:
        if not layer.commitChanges():
            errors = layer.commitErrors()
            layer.rollBack()
            return tool_error("; ".join(errors) or "Commit failed", "EXECUTION_FAILED",
                              "Read the provider error; the change was rolled back.")
    layer.updateExtents()
    layer.triggerRepaint()
    result = {"updated": len(geometries), "fids": list(geometries), "committed": not was_editing}
    if was_editing:
        result["note"] = "changed in the open edit session (not committed, commit or discard it yourself)"
    return result
