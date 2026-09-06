# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""How many ground metres one CRS unit is worth, where the work is happening."""























from __future__ import annotations




TOLERANCE = 0.05



_PROBE = 1000.0


def metres_per_unit(crs, x: float, y: float) -> float | None:
    """Ground metres per unit of ``crs`` at the point (``x``, ``y``) read in it."""






    try:
        from qgis.core import QgsDistanceArea, QgsPointXY, QgsProject

        if crs is None or not crs.isValid() or crs.isGeographic():
            return None
        measure = QgsDistanceArea()
        measure.setSourceCrs(crs, QgsProject.instance().transformContext())
        measure.setEllipsoid("WGS84")

        ground = measure.measureLine(QgsPointXY(x - _PROBE / 2.0, y),
                                     QgsPointXY(x + _PROBE / 2.0, y))
    except Exception:  # noqa: BLE001 - a measurement about the work never breaks it
        return None
    if not ground or ground <= 0:
        return None
    return ground / _PROBE


def layer_metres_per_unit(layer) -> float | None:
    """``metres_per_unit`` at the centre of a layer's extent."""
    try:
        extent = layer.extent()


        if extent.isNull():
            return None
        centre = extent.center()
        return metres_per_unit(layer.crs(), centre.x(), centre.y())
    except Exception:  # noqa: BLE001
        return None


def pixel_facts(layer) -> dict:
    """What one pixel of a raster is worth, in layer units and on the ground."""







    try:
        width, height = int(layer.width()), int(layer.height())
        if width <= 0 or height <= 0:
            return {}
        px, py = float(layer.rasterUnitsPerPixelX()), float(layer.rasterUnitsPerPixelY())
    except Exception:  # noqa: BLE001
        return {}
    out = {"width": width, "height": height, "size_units": "pixels",
           "pixel_size": {"x": round(px, 6), "y": round(py, 6), "units": "layer units"}}
    scale = layer_metres_per_unit(layer)
    if scale:
        area = px * py * scale * scale
        out["pixel_area_m2"] = round(area, 3)
        out["pixel_note"] = (f"One pixel is {px * scale:.3g} m by {py * scale:.3g} m of ground, {area:.4g} m²: "
                             f"a pixel count times pixel_area_m2 is an area, never times an assumed 100 m².")
    return out


def canvas_metres_per_unit(canvas) -> float | None:
    """``metres_per_unit`` at the centre of what the canvas is showing."""
    try:
        extent = canvas.extent()
        if extent.isNull():
            return None
        centre = extent.center()
        return metres_per_unit(canvas.mapSettings().destinationCrs(), centre.x(), centre.y())
    except Exception:  # noqa: BLE001
        return None


def distorted(scale: float | None) -> bool:
    """True when a length in these units is not the length it is called."""
    return scale is not None and abs(scale - 1.0) > TOLERANCE





AREA_TOLERANCE = 0.02


def area_expression_error(layer) -> float | None:
    """``$area`` divided by the true ground area, on one feature, or None."""














    try:
        from qgis.core import (
            QgsDistanceArea,
            QgsExpression,
            QgsExpressionContext,
            QgsExpressionContextUtils,
            QgsProject,
            QgsWkbTypes,
        )

        from .qt_compat import enum_member





        polygon = enum_member(QgsWkbTypes, "GeometryType", "PolygonGeometry")
        if layer is None or layer.geometryType() != polygon:
            return None
        project = QgsProject.instance()
        feature = next(layer.getFeatures(), None)
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
    except Exception:  # noqa: BLE001 - a measurement about the work never breaks it
        return None
    if not isinstance(reported, (int, float)) or isinstance(reported, bool):
        return None
    if not truth or truth <= 0 or reported <= 0:
        return None
    return float(reported) / float(truth)


def area_distorted(ratio: float | None) -> bool:
    """True when ``$area`` is not the ground area it will be read as."""
    return ratio is not None and abs(ratio - 1.0) > AREA_TOLERANCE


def measure_on_ellipsoid(expression, layer) -> str:
    """Make ``$area``, ``$perimeter`` and ``$length`` answer in ground metres."""













    try:
        from qgis.core import QgsDistanceArea, QgsProject, QgsUnitTypes

        project = QgsProject.instance()
        current = str(project.ellipsoid() or "").strip()
        if current and current.upper() != "NONE":
            return ""
        measure = QgsDistanceArea()
        measure.setSourceCrs(layer.crs(), project.transformContext())
        measure.setEllipsoid("WGS84")
        expression.setGeomCalculator(measure)
        expression.setAreaUnits(QgsUnitTypes.AreaUnit.SquareMeters)
        expression.setDistanceUnits(QgsUnitTypes.DistanceUnit.Meters)
    except Exception:  # noqa: BLE001 - a project that measures planar is not an error
        return ""
    return "WGS84"
