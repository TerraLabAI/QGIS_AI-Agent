# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core.tool_registry import Tool, ToolRegistry
from ._compat import QVAR_STRING
from .layer_lookup import _find_layer


def register_coordinate_feature_tools(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="create_coordinate_feature",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Create a {geometry_type} feature[ in {target_layer}]"),
        input_schema={
            "type": "object",
            "properties": {
                "geometry_type": {"type": "string", "enum": ["point", "line", "polygon"]},
                "coordinates": {"type": "array", "items": {
                    "type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}},
                "target_layer": {"type": "string"},
                "layer_name": {"type": "string"},
                "source_crs": {"type": "string"},
                "target_crs": {"type": "string"},
                "fields": {"type": "object"},
            },
            "required": ["geometry_type", "coordinates"],
            "additionalProperties": False,
        },
        handler=_create_coordinate_feature,
    ))


def _crs(value: str | None) -> QgsCoordinateReferenceSystem | None:
    if not value:
        return None
    result = QgsCoordinateReferenceSystem(str(value))
    return result if result.isValid() else None


def _crs_decision(coords: list[list[float]], source: str | None) -> tuple[str | None, dict | None]:
    if source:
        if _crs(source) is None:
            return None, {"_error": f"source_crs {source!r} is invalid; EPSG:4326 is a valid authority.",
                          "_code": "INVALID_ARGS"}
        return source, None
    if not coords:
        return None, {"_error": "coordinates must not be empty", "_code": "INVALID_ARGS"}
    degree_like = all(-180 <= p[0] <= 180 and -90 <= p[1] <= 90 for p in coords)


    if degree_like:
        return "EPSG:4326", None
    return None, {"_error": "Coordinate CRS is ambiguous or looks projected; source_crs (EPSG:2154, "
                            "EPSG:4326) names it explicitly.", "_code": "CRS_REQUIRED"}


def _create_coordinate_feature(args: dict) -> dict:
    geometry_type = str(args.get("geometry_type") or "").lower()
    coords = args.get("coordinates")
    if geometry_type not in {"point", "line", "polygon"} or not isinstance(coords, list):
        return {"_error": "geometry_type must be point, line, or polygon, with an ordered coordinates array.",
                "_code": "INVALID_ARGS"}
    try:
        points = [[float(p[0]), float(p[1])] for p in coords]
    except (TypeError, ValueError, IndexError):
        return {"_error": "Each coordinate must be a numeric [x, y] pair.", "_code": "INVALID_ARGS"}
    minimum = {"point": 1, "line": 2, "polygon": 4}[geometry_type]
    if len(points) < minimum:
        return {"_error": f"{geometry_type} needs at least {minimum} coordinate(s).", "_code": "INVALID_ARGS"}
    if geometry_type == "polygon" and points[0] != points[-1]:
        return {"_error": "Polygon coordinates must repeat the first point at the end to close the ring.",
                "_code": "INVALID_ARGS"}
    source_authid, error = _crs_decision(points, args.get("source_crs"))
    if error:
        return error
    source = _crs(source_authid)
    target_layer = _find_layer(str(args.get("target_layer") or "")) if args.get("target_layer") else None
    if target_layer is not None and not isinstance(target_layer, QgsVectorLayer):
        return {"_error": "target_layer must be a vector layer.", "_code": "INVALID_ARGS"}
    layer_authid = target_layer.crs().authid() if target_layer else None
    requested_target = _crs(args.get("target_crs")) if args.get("target_crs") else None
    if args.get("target_crs") and requested_target is None:
        return {"_error": f"Invalid target_crs {args['target_crs']!r}.", "_code": "INVALID_ARGS"}
    if target_layer and requested_target and requested_target.authid() != layer_authid:
        return {"_error": f"target_layer uses {layer_authid}; omit target_crs or match the layer CRS.",
                "_code": "CRS_MISMATCH"}
    target_authid = layer_authid or args.get("target_crs") or source_authid
    target = _crs(target_authid)
    if target is None:
        return {"_error": "target_crs must be valid, e.g. EPSG:2154.", "_code": "INVALID_ARGS"}
    if target_layer is not None and target_layer.geometryType() != {"point": 0, "line": 1, "polygon": 2}[geometry_type]:
        return {"_error": f"target_layer geometry type is not {geometry_type}.", "_code": "INVALID_ARGS"}
    qpoints = [QgsPointXY(x, y) for x, y in points]
    if source != target:
        transform = QgsCoordinateTransform(source, target, QgsProject.instance())
        qpoints = [transform.transform(p) for p in qpoints]
    if geometry_type == "point":
        geometry = QgsGeometry.fromPointXY(qpoints[0])
    elif geometry_type == "line":
        geometry = QgsGeometry.fromPolylineXY(qpoints)
    else:
        geometry = QgsGeometry.fromPolygonXY([qpoints])
    created = False
    if target_layer is None:
        layer_name = str(args.get("layer_name") or f"Coordinate {geometry_type}")
        layer = QgsVectorLayer(f"{geometry_type}?crs={target.authid()}", layer_name, "memory")
        if not layer.isValid():
            return {"_error": "Could not create the in-memory target layer.", "_code": "WRITE_FAILED"}
        for name in (args.get("fields") or {}):
            layer.dataProvider().addAttributes([QgsField(str(name), QVAR_STRING)])
        layer.updateFields()
        QgsProject.instance().addMapLayer(layer)
        created = True
    else:
        layer = target_layer
    feature = QgsFeature(layer.fields())
    feature.setGeometry(geometry)
    for name, value in (args.get("fields") or {}).items():
        if layer.fields().indexOf(str(name)) >= 0:
            feature.setAttribute(str(name), value)
    started = False
    if not layer.isEditable():
        started = layer.startEditing()
    if not layer.addFeature(feature):
        if started:
            layer.rollBack()
        return {"_error": "QGIS refused the feature.", "_code": "WRITE_FAILED"}
    if started and not layer.commitChanges():
        layer.rollBack()
        return {"_error": "Could not commit the feature.", "_code": "WRITE_FAILED"}
    layer.triggerRepaint()
    return {"layer_name": layer.name(), "layer_id": layer.id(), "geometry_type": geometry_type,
            "crs": target.authid(), "created_layer": created, "added": 1}
