# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later























from __future__ import annotations

import re




TOLERANCE = 0.05



_PROBE = 1000.0


def metres_per_unit(crs, x: float, y: float) -> float | None:







    try:
        from qgis.core import QgsDistanceArea, QgsPointXY, QgsProject

        if crs is None or not crs.isValid() or crs.isGeographic():
            return None
        measure = QgsDistanceArea()
        measure.setSourceCrs(crs, QgsProject.instance().transformContext())
        measure.setEllipsoid("WGS84")

        ground = measure.measureLine(QgsPointXY(x - _PROBE / 2.0, y),
                                     QgsPointXY(x + _PROBE / 2.0, y))
    except Exception:  # noqa: BLE001
        return None
    if not ground or ground <= 0:
        return None
    return ground / _PROBE


def layer_metres_per_unit(layer) -> float | None:

    try:
        extent = layer.extent()


        if extent.isNull():
            return None
        centre = extent.center()
        return metres_per_unit(layer.crs(), centre.x(), centre.y())
    except Exception:  # noqa: BLE001
        return None


def pixel_facts(layer) -> dict:














    try:
        width, height = int(layer.width()), int(layer.height())
        if width <= 0 or height <= 0:
            return {}
        px, py = float(layer.rasterUnitsPerPixelX()), float(layer.rasterUnitsPerPixelY())
    except Exception:  # noqa: BLE001
        return {}
    out = {"width": width, "height": height, "size_units": "pixels",
           "pixel_size": {"x": round(px, 6), "y": round(py, 6), "units": "layer units"}}



    try:
        provider = layer.dataProvider()
        kinds = [data_type_name(provider, band) for band in range(1, min(int(layer.bandCount()), _TYPES_LISTED) + 1)]
    except Exception:  # noqa: BLE001
        kinds = []
    if kinds:
        out["data_type"] = kinds[0] if len(set(kinds)) == 1 else kinds
    point = _work_point(layer)
    cell = pixel_on_ground(layer.crs(), point.x(), point.y(), px, py) if point is not None else None
    if cell:
        across, down, area = cell
        out["pixel_area_m2"] = round(area, 3)
        out["pixel_note"] = (
            f"At layer coordinates ({point.x():.12g}, {point.y():.12g}), a pixel measures "
            f"{across:.3g} m by {down:.3g} m and {area:.4g} m² on the WGS84 ellipsoid. "
            "This is a local sample: ground pixel areas can vary across the raster, so multiplying "
            "pixel counts by pixel_area_m2 does not measure their aggregate ground area.")
    return out



_TYPES_LISTED = 32


def data_type_name(provider, band: int) -> str:

    from qgis.core import QgsRasterLayer

    kind = provider.dataType(band)
    try:
        return str(QgsRasterLayer.dataTypeToString(kind))
    except Exception:  # noqa: BLE001
        return str(getattr(kind, "name", kind))


def pixel_on_ground(crs, x: float, y: float, px: float, py: float) -> tuple | None:






    try:
        from qgis.core import QgsDistanceArea, QgsGeometry, QgsPointXY, QgsProject, QgsRectangle

        if crs is None or not crs.isValid() or crs.isGeographic() or px <= 0 or py <= 0:
            return None
        cells = max(1, min(100, int(_PROBE // max(px, py))))
        half_x, half_y = cells * px / 2.0, cells * py / 2.0
        measure = QgsDistanceArea()
        measure.setSourceCrs(crs, QgsProject.instance().transformContext())
        measure.setEllipsoid("WGS84")
        area = measure.measureArea(QgsGeometry.fromRect(QgsRectangle(x - half_x, y - half_y, x + half_x, y + half_y)))
        across = measure.measureLine(QgsPointXY(x - half_x, y), QgsPointXY(x + half_x, y))
        down = measure.measureLine(QgsPointXY(x, y - half_y), QgsPointXY(x, y + half_y))
    except Exception:  # noqa: BLE001
        return None
    if not area or not across or not down or min(area, across, down) <= 0:
        return None
    return across / cells, down / cells, area / (cells * cells)


def _work_point(layer):



    try:
        extent = layer.extent()
        if extent.isNull():
            return None
        centre = extent.center()
    except Exception:  # noqa: BLE001
        return None
    try:
        from qgis.core import QgsCoordinateTransform, QgsProject
        from qgis.utils import iface

        canvas = iface.mapCanvas() if iface is not None else None
        view = canvas.extent() if canvas is not None else None
        if view is not None and not view.isNull():
            to_layer = QgsCoordinateTransform(canvas.mapSettings().destinationCrs(), layer.crs(),
                                              QgsProject.instance().transformContext())
            point = to_layer.transform(view.center())
            if extent.contains(point):
                return point
    except Exception:  # noqa: BLE001  # nosec B110
        pass
    return centre


def canvas_metres_per_unit(canvas) -> float | None:

    try:
        extent = canvas.extent()
        if extent.isNull():
            return None
        centre = extent.center()
        return metres_per_unit(canvas.mapSettings().destinationCrs(), centre.x(), centre.y())
    except Exception:  # noqa: BLE001
        return None


def distorted(scale: float | None) -> bool:

    return scale is not None and abs(scale - 1.0) > TOLERANCE





AREA_TOLERANCE = 0.02


def area_expression_error(layer) -> float | None:












    try:
        from qgis.core import (
            QgsDistanceArea,
            QgsExpression,
            QgsExpressionContext,
            QgsExpressionContextUtils,
            QgsProject,
            QgsWkbTypes,
        )

        from .feature_requests import feature_request, first_feature
        from .qt_compat import enum_member





        polygon = enum_member(QgsWkbTypes, "GeometryType", "PolygonGeometry")
        if layer is None or layer.geometryType() != polygon:
            return None
        project = QgsProject.instance()


        feature = first_feature(layer, feature_request(attributes=[], limit=1))
        if feature is None or not feature.hasGeometry():
            return None

        expression = QgsExpression("$area")
        context = QgsExpressionContext()
        context.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
        context.setFeature(feature)
        reported = expression.evaluate(context)

        measure = QgsDistanceArea()
        measure.setSourceCrs(layer.crs(), project.transformContext())
        measure.setEllipsoid("WGS84")
        truth = measure.measureArea(feature.geometry())
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(reported, (int, float)) or isinstance(reported, bool):
        return None
    if not truth or truth <= 0 or reported <= 0:
        return None
    return float(reported) / float(truth)


def area_distorted(ratio: float | None) -> bool:

    return ratio is not None and abs(ratio - 1.0) > AREA_TOLERANCE


def measure_on_ellipsoid(expression, layer) -> str:












    try:
        from qgis.core import QgsDistanceArea, QgsProject

        project = QgsProject.instance()
        current = str(project.ellipsoid() or "").strip()
        if current and current.upper() != "NONE":
            return ""
        area_unit, distance_unit = _metre_units()
        measure = QgsDistanceArea()
        measure.setSourceCrs(layer.crs(), project.transformContext())
        measure.setEllipsoid("WGS84")
        expression.setGeomCalculator(measure)
        expression.setAreaUnits(area_unit)
        expression.setDistanceUnits(distance_unit)
    except Exception:  # noqa: BLE001
        return ""
    return "WGS84"


def _metre_units():

    from qgis.core import Qgis, QgsUnitTypes

    from .qt_compat import enum_member




    area_unit = enum_member(Qgis, "AreaUnit", "SquareMeters", None)
    if area_unit is None:
        area_unit = enum_member(QgsUnitTypes, "AreaUnit", "AreaSquareMeters")
    distance_unit = enum_member(Qgis, "DistanceUnit", "Meters", None)
    if distance_unit is None:
        distance_unit = enum_member(QgsUnitTypes, "DistanceUnit", "DistanceMeters")
    return area_unit, distance_unit


_MEASURING = re.compile(r"\$(?:area|length|perimeter)\b", re.IGNORECASE)
_PLANAR = re.compile(r"\b(?:area|length|perimeter)\s*\(\s*\$geometry", re.IGNORECASE)


def _texts(value, depth: int = 0):

    if isinstance(value, str):
        yield value
    elif depth < 3 and isinstance(value, (list, tuple)):
        for item in value:
            yield from _texts(item, depth + 1)
    elif depth < 3 and isinstance(value, dict):
        for item in value.values():
            yield from _texts(item, depth + 1)


def processing_measurement(context, parameters, measured_on: str) -> dict:





    texts = list(_texts(parameters))
    ellipsoidal = any(_MEASURING.search(text) for text in texts)
    planar = any(_PLANAR.search(text) for text in texts)
    if not ellipsoidal and not planar:
        return {}
    out: dict = {}
    if planar:
        out["planar"] = "area($geometry), length($geometry) and perimeter($geometry) are planar, in layer CRS units."
    if ellipsoidal:
        try:
            from qgis.core import QgsUnitTypes

            ellipsoid = str(context.ellipsoid() or "").strip()
            if ellipsoid and ellipsoid.upper() != "NONE":
                out["ellipsoid"] = ellipsoid
                out["area_units"] = QgsUnitTypes.toString(context.areaUnit())
                out["length_units"] = QgsUnitTypes.toString(context.distanceUnit())
            else:
                out["ellipsoid"] = "NONE"
                out["planar_measure"] = "$area, $length and $perimeter measured planar, in layer CRS units."
        except Exception:  # noqa: BLE001  # nosec B110
            pass
        if measured_on:
            out["corrected"] = ("This project measures planar (ellipsoid NONE), so $area, $length and $perimeter "
                                "were measured on the WGS84 ellipsoid here: ground square metres and metres.")
    return {"measurement": out} if out else {}


def measure_processing_on_ellipsoid(context) -> str:











    try:
        current = str(context.ellipsoid() or "").strip()
        if current and current.upper() != "NONE":
            return ""
        area_unit, distance_unit = _metre_units()
        context.setEllipsoid("WGS84")
        context.setAreaUnit(area_unit)
        context.setDistanceUnit(distance_unit)
    except Exception:  # noqa: BLE001
        return ""
    return "WGS84"
