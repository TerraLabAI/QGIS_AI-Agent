# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
















from __future__ import annotations

import math

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core import tuning
from ..core.feature_requests import feature_request, first_feature
from ..core.qt_compat import enum_member
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from ._layers import unique_layer_name
from .layer_lookup import _find_layer, _layer_not_found_error




MAX_LOTS = 200
MAX_VERTICES = 20_000


AREA_TOLERANCE = 1e-7
MAX_ITERATIONS = 100


def register_parcel_tools(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="split_parcel",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Split a parcel of {layer} into lots"),
        input_schema={
            "type": "object",
            "properties": {
                "layer": {"type": "string", "minLength": 1},
                "feature_id": {"type": "integer"},
                "parts": {"type": "integer", "minimum": 2, "maximum": MAX_LOTS},
                "lot_area": {"type": "number", "exclusiveMinimum": 0},
                "direction": {"type": "number", "minimum": 0, "maximum": 360},
                "reference_layer": {"type": "string", "minLength": 1},
                "output_name": {"type": "string"},
            },
            "required": ["layer"],
            "additionalProperties": False,
        },
        handler=_split_parcel,
    ))


def _parcel(layer, args: dict):

    if args.get("feature_id") is not None:
        feature = layer.getFeature(int(args["feature_id"]))
        if not feature.isValid() or not feature.hasGeometry():
            return None, tool_error(f"'{layer.name()}' has no feature {args['feature_id']} with a geometry.",
                                    "INVALID_ARGS", "Read the ids with get_features, or select the parcel instead.")
        return feature, None
    selected = layer.selectedFeatureIds()
    if len(selected) == 1:
        return layer.getFeature(selected[0]), None
    if not selected and layer.featureCount() == 1:
        return first_feature(layer, feature_request()), None
    what = f"{len(selected)} are selected" if selected else f"it has {layer.featureCount()} features"
    return None, tool_error(f"Which parcel of '{layer.name()}'? {what}.", "INVALID_ARGS",
                            "Pass feature_id, or select exactly one parcel first.")


def _azimuth(dx: float, dy: float) -> float:
    return math.degrees(math.atan2(dx, dy)) % 360.0


def _reference_azimuth(args: dict, calc_crs):

    reference = _find_layer(str(args["reference_layer"]))
    if reference is None:
        return None, _layer_not_found_error(str(args["reference_layer"]))
    if not isinstance(reference, QgsVectorLayer) or \
            QgsWkbTypes.geometryType(reference.wkbType()) != enum_member(Qgis, "GeometryType", "Line"):
        return None, tool_error(f"'{reference.name()}' is not a line layer.", "INVALID_ARGS",
                                "Pass a line layer whose line gives the cut direction, or a direction in degrees.")
    ids = reference.selectedFeatureIds()

    feature = (reference.getFeature(ids[0]) if ids
              else first_feature(reference, feature_request(attributes=[])))
    if feature is None or not feature.hasGeometry():
        return None, tool_error(f"'{reference.name()}' has no line.", "INVALID_ARGS",
                                "Draw the reference line first, or pass a direction in degrees.")
    geometry = QgsGeometry(feature.geometry())
    transform = QgsCoordinateTransform(reference.crs(), calc_crs, QgsProject.instance())
    geometry.transform(transform)
    vertices = list(geometry.vertices())
    first, last = vertices[0], vertices[-1]
    if math.hypot(last.x() - first.x(), last.y() - first.y()) == 0:
        return None, tool_error(f"The line in '{reference.name()}' starts and ends at one point.", "INVALID_ARGS",
                                "Use a straight line, or pass a direction in degrees.")
    return _azimuth(last.x() - first.x(), last.y() - first.y()), None


def _default_azimuth(geometry: QgsGeometry) -> float:

    box = geometry.orientedMinimumBoundingBox()[0]
    points = list(box.vertices())[:3]
    (a, b, c) = points
    side1 = (b.x() - a.x(), b.y() - a.y())
    side2 = (c.x() - b.x(), c.y() - b.y())
    short = side1 if math.hypot(*side1) <= math.hypot(*side2) else side2
    return _azimuth(*short)


class _Sweep:


    def __init__(self, geometry: QgsGeometry, azimuth: float):
        rad = math.radians(azimuth)
        self.along = (math.sin(rad), math.cos(rad))
        self.normal = (math.cos(rad), -math.sin(rad))
        self.geometry = geometry
        offsets = [p.x() * self.normal[0] + p.y() * self.normal[1] for p in geometry.vertices()]
        spans = [p.x() * self.along[0] + p.y() * self.along[1] for p in geometry.vertices()]
        self.low, self.high = min(offsets), max(offsets)
        margin = (self.high - self.low) + (max(spans) - min(spans)) + 1.0
        self.span = (min(spans) - margin, max(spans) + margin)
        self.margin = margin

    def _point(self, offset: float, span: float) -> QgsPointXY:
        return QgsPointXY(offset * self.normal[0] + span * self.along[0],
                          offset * self.normal[1] + span * self.along[1])

    def slab(self, start: float, end: float) -> QgsGeometry:
        s0, s1 = self.span
        ring = [self._point(start, s0), self._point(end, s0), self._point(end, s1), self._point(start, s1),
                self._point(start, s0)]
        return QgsGeometry.fromPolygonXY([ring])

    def area_below(self, offset: float) -> float:
        return self.geometry.intersection(self.slab(self.low - self.margin, offset)).area()

    def cut_at(self, target: float, total: float) -> float:
        low, high = self.low, self.high
        for _ in range(MAX_ITERATIONS):
            middle = (low + high) / 2.0
            area = self.area_below(middle)
            if abs(area - target) <= AREA_TOLERANCE * total:
                return middle
            if area < target:
                low = middle
            else:
                high = middle
        return (low + high) / 2.0


def _calc_crs(layer_crs):

    if layer_crs.isGeographic():
        return None, 1.0
    factor = QgsUnitTypes.fromUnitToUnitFactor(layer_crs.mapUnits(), enum_member(Qgis, "DistanceUnit", "Meters"))
    return layer_crs, factor * factor


def _split_parcel(args: dict) -> dict:
    layer = _find_layer(str(args.get("layer") or ""))
    if layer is None:
        return _layer_not_found_error(str(args.get("layer") or ""))
    if not isinstance(layer, QgsVectorLayer) or QgsWkbTypes.geometryType(layer.wkbType()) != enum_member(
        Qgis, "GeometryType", "Polygon"
    ):
        return tool_error(
            f"'{layer.name()}' is not a polygon layer.", "INVALID_ARGS", "Pass the parcel's polygon layer."
        )
    parts, lot_area = args.get("parts"), args.get("lot_area")
    if (parts is None) == (lot_area is None):
        return tool_error("Give parts (equal lots) or lot_area (square metres per lot), one of the two.",
                          "INVALID_ARGS", "parts: 4 for four equal lots; lot_area: 2000 for lots of 2,000 m2.")
    if args.get("direction") is not None and args.get("reference_layer"):
        return tool_error("Give direction or reference_layer, not both.", "INVALID_ARGS",
                          "reference_layer when the user drew or named a line, direction for a bearing.")
    feature, refusal = _parcel(layer, args)
    if refusal:
        return refusal
    geometry = QgsGeometry(feature.geometry())
    max_vertices = tuning.ceiling("parcel_max_vertices", MAX_VERTICES, 2_000)
    if geometry.constGet().nCoordinates() > max_vertices:
        return tool_error(f"The parcel has over {max_vertices} vertices.", "INVALID_ARGS",
                          "Simplify it first (run_processing native:simplifygeometries), then split.")
    if not geometry.isGeosValid():
        geometry = geometry.makeValid()

    layer_crs = layer.crs()
    calc_crs, square_metres = _calc_crs(layer_crs)
    to_calc = back = None
    if calc_crs is None:
        centroid = QgsGeometry(geometry).centroid().asPoint()
        zone = int((centroid.x() + 180.0) // 6.0) % 60 + 1
        calc_crs = QgsCoordinateReferenceSystem(f"EPSG:{(32600 if centroid.y() >= 0 else 32700) + zone}")
        to_calc = QgsCoordinateTransform(layer_crs, calc_crs, QgsProject.instance())
        back = QgsCoordinateTransform(calc_crs, layer_crs, QgsProject.instance())
        geometry.transform(to_calc)
    total = geometry.area()
    if total <= 0:
        return tool_error("The parcel has no area.", "INVALID_ARGS", "Check its geometry with check_geometry_validity.")

    if args.get("reference_layer"):
        azimuth, refusal = _reference_azimuth(args, calc_crs)
        if refusal:
            return refusal
        how = f"parallel to the line in {args['reference_layer']}"
    elif args.get("direction") is not None:
        azimuth, how = float(args["direction"]) % 360.0, "the direction asked"
    else:
        azimuth, how = _default_azimuth(geometry), "the parcel's short side (lots are slices across its length)"

    azimuth %= 180.0
    if parts is not None:
        count = int(parts)
        targets = [total * k / count for k in range(1, count)]
        wanted = total / count
    else:
        wanted = float(lot_area) / square_metres
        count = math.ceil(total / wanted - 1e-9)
        if count < 2:
            return tool_error(f"The parcel is {total * square_metres:,.0f} m2, not more than one lot of "
                              f"{float(lot_area):,.0f} m2.", "INVALID_ARGS", "Ask for a smaller lot_area.")
        max_lots = tuning.ceiling("parcel_max_lots", MAX_LOTS, 10)
        if count > max_lots:
            return tool_error(f"That makes {count} lots; the limit is {max_lots}.", "INVALID_ARGS",
                              "Ask for larger lots, or split a smaller parcel.")
        targets = [wanted * k for k in range(1, count)]

    sweep = _Sweep(geometry, azimuth)
    cuts = [sweep.low - sweep.margin] + [sweep.cut_at(target, total) for target in targets] + \
        [sweep.high + sweep.margin]
    lots = [geometry.intersection(sweep.slab(cuts[i], cuts[i + 1])) for i in range(count)]

    name = unique_layer_name(str(args.get("output_name") or "").strip() or f"{layer.name()} lots")
    out_layer = QgsVectorLayer("MultiPolygon?field=lot:integer&field=area_m2:double&field=area_ha:double"
                               "&field=parent_fid:integer", name, "memory")
    out_layer.setCrs(layer_crs)
    features, report = [], []
    multipart = 0
    for number, lot in enumerate(lots, start=1):
        area_m2 = lot.area() * square_metres
        if lot.isMultipart() and len(lot.asGeometryCollection()) > 1:
            multipart += 1
        placed = QgsGeometry(lot)
        if back is not None:
            placed.transform(back)
        placed.convertToMultiType()
        out = QgsFeature(out_layer.fields())
        out.setGeometry(placed)
        out.setAttributes([number, round(area_m2, 2), round(area_m2 / 10_000.0, 4), int(feature.id())])
        features.append(out)
        report.append({"lot": number, "area_m2": round(area_m2, 2), "area_ha": round(area_m2 / 10_000.0, 4)})
    out_layer.dataProvider().addFeatures(features)
    out_layer.updateExtents()
    QgsProject.instance().addMapLayer(out_layer)

    compared = report if parts is not None else report[:-1]
    target_m2 = wanted * square_metres
    deviation = max(abs(item["area_m2"] - target_m2) / target_m2 * 100.0 for item in compared) if compared else 0.0
    result = {
        "layer": out_layer.name(), "layer_id": out_layer.id(), "lot_count": count, "lots": report[:50],
        "parcel_area_m2": round(total * square_metres, 2), "target_lot_area_m2": round(target_m2, 2),
        "max_deviation_percent": round(deviation, 4), "cut_azimuth_deg": round(azimuth, 4),
        "direction_from": how, "measured_in": calc_crs.authid(),
        "area_basis": "planar, in " + calc_crs.authid() + (
            " (the UTM zone of the parcel; the layer is geographic)" if back is not None else ""),
    }
    if parts is None:
        result["remainder_lot"] = report[-1]
    if multipart:
        result["note"] = (f"{multipart} lots came out in several pieces: the parcel's outline folds back across "
                          f"the cut direction. Try another direction, or keep them as multipart lots.")
    return result
