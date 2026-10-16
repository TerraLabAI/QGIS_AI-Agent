# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later









from __future__ import annotations

import math

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProcessingParameterNumber,
    QgsProject,
    QgsRasterLayer,
)

from ..core import ground, tuning
from .layer_lookup import _find_layer

_DISTANCE_NAMES = ("DISTANCE", "RADIUS", "TOLERANCE", "BUFFER", "MAX_DISTANCE", "SEARCH_DISTANCE",
                   "SNAP_TOLERANCE", "SPACING", "INTERVAL", "CELLSIZE", "PIXEL_SIZE")





_PERCENTAGE_PARAMETERS = frozenset({("native:voronoipolygons", "BUFFER"), ("gdal:pointsalonglines", "DISTANCE")})



_INTEGER = getattr(getattr(Qgis, "ProcessingNumberParameterType", None), "Integer", None)
if _INTEGER is None:
    _INTEGER = QgsProcessingParameterNumber.Integer

_DEGREES_ALLOWED = 0.01









def _utm_authid(lon: float, lat: float) -> str:

    lon = max(-180.0, min(180.0, float(lon)))
    zone = min(60, int((lon + 180.0) // 6.0) + 1)
    return f"EPSG:{32600 + zone}" if float(lat) >= 0 else f"EPSG:{32700 + zone}"


def _centre_lonlat(layer) -> tuple[float, float] | None:

    try:
        extent = layer.extent()
        if extent.isNull():
            return None
        centre = extent.center()
        crs = layer.crs()
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        if not crs.isValid() or crs == wgs84:
            return centre.x(), centre.y()
        moved = QgsCoordinateTransform(crs, wgs84, QgsProject.instance()).transform(centre)
        return moved.x(), moved.y()
    except Exception:  # noqa: BLE001
        return None


def _suggest_metric_crs(layer) -> str:










    project_crs = QgsProject.instance().crs()
    if project_crs.isValid() and not project_crs.isGeographic():
        scale = ground.layer_metres_per_unit(_ProjectCrsProbe(project_crs, layer))
        if scale is None or abs(scale - 1.0) <= ground.TOLERANCE:
            return project_crs.authid()
    centre = _centre_lonlat(layer)
    if centre is None:
        return "EPSG:3857"
    return _utm_authid(centre[0], centre[1])


class _ProjectCrsProbe:







    def __init__(self, crs, layer):
        self._crs = crs
        self._layer = layer

    def crs(self):
        return self._crs

    def extent(self):
        source = self._layer.crs()
        extent = self._layer.extent()
        if not source.isValid() or source == self._crs:
            return extent
        return QgsCoordinateTransform(source, self._crs, QgsProject.instance()).transformBoundingBox(extent)


def _geographic_distance_check(alg, parameters: dict, confirmed: bool) -> tuple[dict | None, str]:














    notes: list[str] = []
    for definition in alg.parameterDefinitions():
        name = definition.name()
        if (str(alg.id() or "").casefold(), name.upper()) in _PERCENTAGE_PARAMETERS:
            continue
        try:
            typed_distance = definition.type() == "distance"
            counted = definition.type() == "number" and definition.dataType() == _INTEGER
        except Exception:  # nosec B110
            typed_distance, counted = False, False
        if counted or (not typed_distance and name.upper() not in _DISTANCE_NAMES):
            continue
        value = parameters.get(name)
        degrees_allowed = tuning.threshold("processing_degrees_allowed", _DEGREES_ALLOWED, 0.001, 0.1)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or abs(float(value)) < degrees_allowed:
            continue
        parent = ""
        if hasattr(definition, "parentParameterName"):
            try:
                parent = definition.parentParameterName() or ""
            except Exception:  # nosec B110
                parent = ""
        source = parameters.get(parent or "INPUT")
        layer = _find_layer(source) if isinstance(source, str) else None
        if layer is None or not hasattr(layer, "crs"):
            continue
        if name.upper() == "INTERVAL" and not typed_distance and isinstance(layer, QgsRasterLayer):



            continue
        crs = layer.crs()
        if not crs.isValid():
            continue
        if not crs.isGeographic():



            scale = ground.layer_metres_per_unit(layer)
            if scale is None or abs(scale - 1.0) <= ground.TOLERANCE:
                continue
            notes.append(f"{name} {value:g} on layer '{layer.name()}' in {crs.authid()} covers "
                         f"{float(value) * scale:.4g} m on the ground: one {crs.authid()} unit is {scale:.4g} m "
                         "where this layer sits.")
            continue
        if confirmed:
            continue
        suggested = _suggest_metric_crs(layer)
        return {
            "_error": (
                f"GEOGRAPHIC_DISTANCE: {name} {value:g} on layer '{layer.name()}' in {crs.authid()} means "
                f"{value:g} degrees, about {float(value) * 111:g} km, not metres."
            ),



            "code": "CRS_GUARD",
            "layer_crs": crs.authid(),
            "suggested_crs": suggested,
            "suggestion": (
                f"native:reprojectlayer with TARGET_CRS {suggested} runs the algorithm in metres. "
                "confirm_large true keeps degrees when a value in degrees is really intended."
            ),
        }, ""
    return None, " ".join(notes)




_TERRAIN_BY_CELL = frozenset()


def _terrain_on_degrees_check(algorithm_id: str, parameters: dict) -> dict | None:









    if algorithm_id not in tuning.check_algs("terrain_by_cell", _TERRAIN_BY_CELL):
        return None
    source = parameters.get("INPUT")
    layer = source if hasattr(source, "crs") else (_find_layer(source) if isinstance(source, str) else None)
    if layer is None or not hasattr(layer, "crs"):
        return None
    crs = layer.crs()
    if not crs.isValid() or not crs.isGeographic():
        return None
    suggested = _suggest_metric_crs(layer)
    return {
        "_error": (
            f"TERRAIN_ON_DEGREES: '{layer.name()}' is in {crs.authid()}, so its cells are degrees while its "
            f"heights are not; {algorithm_id} on this grid is wrong on at least one axis, whatever SCALE is."
        ),
        "code": "CRS_GUARD",
        "layer_crs": crs.authid(),
        "suggested_crs": suggested,
        "suggestion": (
            f"gdal:warpreproject with TARGET_CRS {suggested} lets {algorithm_id} "
            "run on the result with SCALE 1."
        ),
    }









_ON_DEGREES = {
    "gdal:viewshed": (True, "every sight line mixes a horizontal distance in degrees with heights in metres, "
                            "and MAX_DISTANCE (100 when left out) reads as degrees", "INPUT"),
    "native:rastersurfacevolume": (True, "each cell's area is in square degrees, so the volume comes out in "
                                         "degrees squared times metres", "INPUT"),
    "qgis:idwinterpolation": (False, "the distances IDW weights by are degrees, and a degree of longitude is "
                                     "shorter than a degree of latitude away from the equator; PIXEL_SIZE (0.1 "
                                     "when left out) is in degrees too", ""),
    "qgis:tininterpolation": (False, "PIXEL_SIZE (0.1 when left out, about 11 km) is in degrees, and the "
                                     "cells are not square on the ground", ""),
    "qgis:heatmapkerneldensityestimation": (False, "RADIUS (100 when left out) and PIXEL_SIZE are in degrees",
                                            "INPUT"),
    "gdal:gridinversedistance": (False, "the search radii and the distance weights are in degrees", "INPUT"),
    "gdal:gridinversedistancenearestneighbor": (False, "the search radius and the distance weights are in "
                                                       "degrees", "INPUT"),
    "gdal:gridaverage": (False, "the search radii are in degrees", "INPUT"),
    "gdal:griddatametrics": (False, "the search radii are in degrees", "INPUT"),
}


def _interpolation_source(value):

    if not isinstance(value, str) or not value:
        return None
    first = value.split("::|::", 1)[0].split("::~::", 1)[0]
    return _find_layer(first) if first else None


def _metric_grid_on_degrees_check(algorithm_id: str, parameters: dict, confirmed: bool) -> dict | None:

    row = _ON_DEGREES.get(str(algorithm_id or "").casefold())
    if row is None:
        return None
    always, why, key = row
    if confirmed and not always:
        return None
    layer = _find_layer(parameters.get(key)) if key and isinstance(parameters.get(key), str) else (
        _interpolation_source(parameters.get("INTERPOLATION_DATA")) if not key else None)
    if layer is None or not hasattr(layer, "crs"):
        return None
    crs = layer.crs()
    if not crs.isValid() or not crs.isGeographic():
        return None
    suggested = _suggest_metric_crs(layer)
    reproject = ("gdal:warpreproject" if isinstance(layer, QgsRasterLayer) else "native:reprojectlayer")
    return {
        "_error": (f"CRS_ON_DEGREES: '{layer.name()}' is in {crs.authid()}, a CRS in degrees: {algorithm_id} "
                   f"on it is wrong, because {why}."),
        "code": "CRS_GUARD",
        "layer_crs": crs.authid(),
        "suggested_crs": suggested,
        "suggestion": (f"{reproject} with TARGET_CRS {suggested} runs "
                       f"{algorithm_id} on the result in metres."
                       + ("" if always else " confirm_large true keeps values really meant in degrees.")),
    }






_FLOW_FROM_DEM = frozenset({
    "grass:r.flow", "grass7:r.flow",
    "saga:flowaccumulationtopdown", "sagang:flowaccumulationtopdown",
    "saga:flowaccumulationrecursive", "sagang:flowaccumulationrecursive",
    "sagang:flowaccumulationparallelizable", "saga:flowdirection", "sagang:flowdirection",
    "wbt:d8pointer", "wbt:dinfpointer", "wbt:fd8pointer", "wbt:rho8pointer",
})

_FILL_MARKS = ("fillsink", "fill.dir", "filldepression", "breachdepression", "pitremove", "sinkremoval",
               "fillsinkswangliu")

_FILL_ALGORITHMS = ("native:fillsinkswangliu", "grass:r.fill.dir", "grass7:r.fill.dir", "sagang:fillsinkswangliu",
                    "wbt:filldepressions", "wbt:breachdepressions")


def _was_filled(layer) -> bool:

    try:
        history = " ".join(str(item) for item in layer.metadata().history()).casefold()
    except Exception:  # noqa: BLE001
        return False
    return any(mark in history for mark in _FILL_MARKS)





_fill_step_cache: dict = {"value": "", "count": -1}


def _fill_step_available() -> str:

    try:
        algs = QgsApplication.processingRegistry().algorithms()
    except Exception:  # noqa: BLE001
        return ""
    if len(algs) != _fill_step_cache["count"]:
        ids = {a.id().casefold(): a.id() for a in algs}
        _fill_step_cache["value"] = next((ids[a] for a in _FILL_ALGORITHMS if a in ids), "")
        _fill_step_cache["count"] = len(algs)
    return _fill_step_cache["value"]


def _flow_dem(alg, algorithm_id: str, parameters: dict):

    if str(algorithm_id or "").casefold() not in _FLOW_FROM_DEM:
        return None
    layer = None
    for definition in alg.parameterDefinitions():
        try:
            if definition.type() == "raster" and not definition.isDestination():
                layer = _find_layer(parameters.get(definition.name())) \
                    if isinstance(parameters.get(definition.name()), str) else None
                break
        except Exception:  # noqa: BLE001  # nosec B112
            continue
    return layer if isinstance(layer, QgsRasterLayer) else None


def _flow_in_degrees_check(alg, algorithm_id: str, parameters: dict) -> dict | None:

    layer = _flow_dem(alg, algorithm_id, parameters)
    if layer is None:
        return None
    crs = layer.crs()
    if crs.isValid() and crs.isGeographic():
        suggested = _suggest_metric_crs(layer)
        return {
            "_error": (f"CRS_ON_DEGREES: '{layer.name()}' is in {crs.authid()}, so its cells are degrees while "
                       f"its heights are metres: {algorithm_id} routes water on slopes it cannot measure."),
            "code": "CRS_GUARD",
            "layer_crs": crs.authid(),
            "suggested_crs": suggested,
            "suggestion": (f"map_drainage reprojects and fills the DEM itself. Or gdal:warpreproject TARGET_CRS "
                           f"{suggested} then a sink fill prepares it for {algorithm_id}."),
        }
    return None


def unfilled_dem_warning(alg, algorithm_id: str, parameters: dict, confirmed: bool) -> str:







    if confirmed:
        return ""
    layer = _flow_dem(alg, algorithm_id, parameters)
    if layer is None or _was_filled(layer):
        return ""
    fill = _fill_step_available()
    step = f"{fill} on '{layer.name()}'" if fill else "a sink fill on the DEM"
    return (f"UNFILLED_DEM: the history of '{layer.name()}' names no sink fill. If it is a raw DEM, "
            f"{algorithm_id} stopped the water at every pit, so the streams break and the catchments fragment: "
            f"run {step} and route its output again, or call map_drainage, which fills and routes in one call. "
            f"A DEM already filled or breached elsewhere is fine as it is.")








_DEGREE_MIN_X = -180.0
_DEGREE_MAX_X = 360.0
_DEGREE_BOUND_Y = 90.0


_HALF_CELL_MAX = 2.5


def degree_slack(layer) -> tuple[float, float]:

    try:
        if isinstance(layer, QgsRasterLayer):
            half_x = abs(float(layer.rasterUnitsPerPixelX())) / 2.0
            half_y = abs(float(layer.rasterUnitsPerPixelY())) / 2.0
            half_max = tuning.threshold("processing_half_cell_max", _HALF_CELL_MAX, 1.0, 5.0)
            if half_x <= half_max and half_y <= half_max:
                return half_x, half_y
    except Exception:  # noqa: BLE001  # nosec B110
        pass
    return 0.0, 0.0


def fits_degrees(xmin: float, xmax: float, ymin: float, ymax: float,
                 slack_x: float = 0.0, slack_y: float = 0.0) -> bool:

    return (xmin >= _DEGREE_MIN_X - slack_x and xmax <= _DEGREE_MAX_X + slack_x
            and xmax - xmin <= 360.0 + 2 * slack_x
            and ymin >= -_DEGREE_BOUND_Y - slack_y and ymax <= _DEGREE_BOUND_Y + slack_y)


def _looks_like_degrees(extent, layer=None) -> bool:
    slack_x, slack_y = degree_slack(layer)
    return fits_degrees(extent.xMinimum(), extent.xMaximum(), extent.yMinimum(), extent.yMaximum(),
                        slack_x, slack_y)


def crs_plausibility(layer, new_crs) -> dict | None:










    try:
        if layer is None or not hasattr(layer, "extent") or not new_crs.isValid():
            return None
        extent = layer.extent()
        if extent is None or extent.isEmpty():
            return None
        degrees = _looks_like_degrees(extent, layer)
    except Exception:  # nosec B110
        return None

    name = layer.name()
    if new_crs.isGeographic() and not degrees:
        return {
            "_error": (
                f"The coordinates in '{name}' run to {extent.xMaximum():.0f}, {extent.yMaximum():.0f}, which "
                f"cannot be degrees, so declaring {new_crs.authid()} would put the layer in the wrong place."
            ),
            "code": "CRS_GUARD",
            "suggestion": (
                "native:reprojectlayer TARGET_CRS EPSG:4326 moves an already correctly declared layer into "
                "degrees. Setting the CRS relabels the coordinates, it does not move them."
            ),
        }
    if not new_crs.isGeographic() and degrees:
        return {
            "_error": (
                f"The coordinates in '{name}' all sit inside 180 by 90, so they are degrees, and "
                f"{new_crs.authid()} is in metres. Declaring it would not reproject the layer."
            ),
            "code": "CRS_GUARD",
            "suggestion": (
                f"The coordinates are usually EPSG:4326; from there, native:reprojectlayer TARGET_CRS "
                f"{new_crs.authid()} gives metres."
            ),
        }
    return _outside_area_of_use(layer, new_crs, extent)




_AREA_OF_USE_MARGIN = 2.0


def _centre_in_area_of_use(crs, point) -> bool | None:

    try:
        bounds = crs.bounds()
        if bounds is None or bounds.isEmpty():
            return None
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        lonlat = point if crs.isGeographic() else \
            QgsCoordinateTransform(crs, wgs84, QgsProject.instance()).transform(point)
        x, y = lonlat.x(), lonlat.y()
        if math.isnan(x) or math.isnan(y) or math.isinf(x) or math.isinf(y):
            return False
        if crs.isGeographic() and x > 180.0:

            x -= 360.0
        m = tuning.threshold("processing_area_of_use_margin", _AREA_OF_USE_MARGIN, 0.5, 10.0)
        return (bounds.xMinimum() - m <= x <= bounds.xMaximum() + m
                and bounds.yMinimum() - m <= y <= bounds.yMaximum() + m)
    except Exception:  # noqa: BLE001
        return False


def _outside_area_of_use(layer, new_crs, extent) -> dict | None:







    try:
        current = layer.crs()
        if not current.isValid() or current == new_crs:
            return None
        centre = extent.center()
    except Exception:  # nosec B110
        return None
    if _centre_in_area_of_use(current, centre) is not True or _centre_in_area_of_use(new_crs, centre) is not False:
        return None
    return {
        "_error": (
            f"'{layer.name()}' is declared {current.authid()} and its coordinates fit that CRS; read as "
            f"{new_crs.authid()} they fall outside the area that CRS covers, so declaring it would move the layer."
        ),
        "code": "CRS_GUARD",
        "suggestion": (
            f"native:reprojectlayer (gdal:warpreproject for a raster) TARGET_CRS "
            f"{new_crs.authid()} moves it there. Setting the CRS relabels the coordinates, it does not move them."
        ),
    }
