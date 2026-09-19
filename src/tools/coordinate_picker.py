# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP
from qgis.utils import iface

from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .layer_lookup import _find_layer


def register_coordinate_picker_tools(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="pick_coordinate",
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Read a coordinate from the map"),
        input_schema={"type": "object", "properties": {
            "canvas_pixel": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
            "point": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
            "crs": {"type": "string"}, "layer_name": {"type": "string"}, "feature_id": {"type": "integer"},
            "vertex_index": {"type": "integer", "minimum": 0}, "snap_tolerance": {"type": "number", "minimum": 0},
            "zoom_scale": {"type": "number", "exclusiveMinimum": 0}, "output_crs": {"type": "string"},
        }, "required": [], "additionalProperties": False}, handler=_pick_coordinate,
    ))


def _crs(value, fallback):
    if not value:
        return fallback
    result = QgsCoordinateReferenceSystem(str(value))
    return result if result.isValid() else None


def _pick_coordinate(args: dict) -> dict:
    canvas = iface.mapCanvas()
    if canvas is None:
        return tool_error("The QGIS map canvas is not available.", "EXECUTION_FAILED", "Open a map view first.")
    if args.get("zoom_scale") is not None:
        canvas.zoomScale(float(args["zoom_scale"]))
    canvas_crs = canvas.mapSettings().destinationCrs()
    source_crs = _crs(args.get("crs"), canvas_crs)
    if args.get("crs") and source_crs is None:
        return tool_error(f"Invalid crs {args['crs']!r}.", "INVALID_ARGS", "Pass an authority such as EPSG:4326.")
    raw = args.get("canvas_pixel")
    if raw is not None:
        if args.get("point") is not None:
            return tool_error("Pass canvas_pixel or point, not both.", "INVALID_ARGS", "Choose one coordinate source.")
        try:
            point = canvas.getCoordinateTransform().toMapCoordinates(
                int(round(float(raw[0]))), int(round(float(raw[1])))
            )
        except (TypeError, ValueError, IndexError, AttributeError):
            return tool_error(
                "canvas_pixel must be [x, y] in viewport pixels.",
                "INVALID_ARGS",
                "Read the pixel from the canvas viewport.",
            )
        source, source_crs = "canvas_pixel", canvas_crs
    elif args.get("point") is not None:
        try:
            point = QgsPointXY(float(args["point"][0]), float(args["point"][1]))
        except (TypeError, ValueError, IndexError):
            return tool_error(
                "point must be a numeric [x, y].", "INVALID_ARGS", "Pass coordinates in crs or the canvas CRS."
            )
        source = "point"
    elif args.get("layer_name"):
        layer = _find_layer(str(args["layer_name"]))
        if not isinstance(layer, QgsVectorLayer):
            return tool_error(
                "layer_name must identify a vector layer.",
                "INVALID_ARGS",
                "Pass a vector layer containing the target geometry.",
            )
        feature_id = args.get("feature_id")
        if feature_id is None:
            return tool_error(
                "feature_id is required when selecting a geometry.", "INVALID_ARGS", "Pass the target feature id."
            )
        feature = layer.getFeature(int(feature_id))
        if not feature.isValid() or feature.geometry().isNull():
            return tool_error(
                f"No geometry was found for feature {feature_id}.", "INVALID_ARGS", "Pass an existing feature id."
            )
        vertices = list(feature.geometry().vertices())
        index = args.get("vertex_index")
        if index is not None:
            if index >= len(vertices):
                return tool_error(
                    "vertex_index is outside the target geometry.", "INVALID_ARGS", "Pass an existing vertex index."
                )
            point = QgsPointXY(vertices[index])
        else:
            point = QgsPointXY(feature.geometry().centroid().asPoint())
        source, source_crs = "geometry", layer.crs()
    else:
        return tool_error(
            "No coordinate source was provided.",
            "INVALID_ARGS",
            "Pass canvas_pixel, point, or layer_name with feature_id.",
        )
    tolerance = float(args.get("snap_tolerance") or 0.0)
    snapped = False
    if tolerance > 0 and args.get("layer_name") and source != "geometry":
        layer = _find_layer(str(args["layer_name"]))
        if isinstance(layer, QgsVectorLayer):
            local = QgsCoordinateTransform(source_crs, layer.crs(), QgsProject.instance()).transform(point)
            best, best_distance = None, None



            search_box = QgsRectangle(local.x() - tolerance, local.y() - tolerance,
                                      local.x() + tolerance, local.y() + tolerance)
            request = QgsFeatureRequest().setFilterRect(search_box).setSubsetOfAttributes([])
            for feature in layer.getFeatures(request):
                distance, candidate, _after, _left = feature.geometry().closestSegmentWithContext(local)


                if (
                    distance >= 0
                    and distance <= tolerance * tolerance
                    and (best_distance is None or distance < best_distance)
                ):
                    best, best_distance = candidate, distance
            if best is not None:
                point = QgsCoordinateTransform(layer.crs(), source_crs, QgsProject.instance()).transform(best)
                snapped = True
    target_crs = _crs(args.get("output_crs"), source_crs)
    if args.get("output_crs") and target_crs is None:
        return tool_error(
            f"Invalid output_crs {args['output_crs']!r}.", "INVALID_ARGS", "Pass an authority such as EPSG:4326."
        )
    if target_crs != source_crs:
        point = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance()).transform(point)
    return {"x": point.x(), "y": point.y(), "crs": target_crs.authid(), "source": source,
            "canvas_crs": canvas_crs.authid(), "snapped": snapped, "snap_tolerance": tolerance,
            "zoom_scale": canvas.scale()}
