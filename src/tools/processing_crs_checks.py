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
from ..core.tool_registry import tool_error
from ._compat import enum_value
from .layer_lookup import _find_layer


def _rules(section: str):


    return (tuning.service_doc("processing_rules") or {}).get(section)


def _distance_names() -> frozenset:


    return frozenset(_rules("distance_parameters") or ())






_PERCENTAGE_PARAMETERS = frozenset({("native:voronoipolygons", "BUFFER"), ("gdal:pointsalonglines", "DISTANCE")})



_INTEGER = enum_value((Qgis, "ProcessingNumberParameterType.Integer"), (QgsProcessingParameterNumber, "Integer"))

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
        if counted or (not typed_distance and name.upper() not in _distance_names()):
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



        return tool_error(
            f"GEOGRAPHIC_DISTANCE: {name} {value:g} on layer '{layer.name()}' in {crs.authid()} means "
            f"{value:g} degrees, about {float(value) * 111:g} km, not metres.",
            "CRS_GUARD", hint="processing_distance_degrees", layer_crs=crs.authid(), suggested_crs=suggested,
        ), ""
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
    return tool_error(
        f"TERRAIN_ON_DEGREES: '{layer.name()}' is in {crs.authid()}, so its cells are degrees while its "
        f"heights are not; {algorithm_id} on this grid is wrong on at least one axis, whatever SCALE is.",
        "CRS_GUARD", hint="processing_terrain_degrees", layer_crs=crs.authid(), suggested_crs=suggested,
        algorithm=str(algorithm_id),
    )









def _interpolation_source(value):

    if not isinstance(value, str) or not value:
        return None
    first = value.split("::|::", 1)[0].split("::~::", 1)[0]
    return _find_layer(first) if first else None


def _metric_grid_on_degrees_check(algorithm_id: str, parameters: dict, confirmed: bool) -> dict | None:

    folded = str(algorithm_id or "").casefold()
    row = (_rules("on_degrees") or {}).get(folded)
    if not isinstance(row, dict):
        return None
    always, key = bool(row.get("always")), str(row.get("source") or "")
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
    parameter = str(row.get("parameter") or "")
    value = parameters.get(parameter) if parameter else None
    if parameter and value in (None, ""):
        value = row.get("default")
    facts = {"parameter": parameter, "value": value} if parameter and value is not None else {}
    said = f" {parameter} is {value}." if facts else ""
    return tool_error(
        f"CRS_ON_DEGREES: '{layer.name()}' is in {crs.authid()}, a CRS in degrees, and {algorithm_id} reads "
        f"its distances and cells in the layer's units.{said}",
        "CRS_GUARD", hint="processing_grid_degrees", layer_crs=crs.authid(), suggested_crs=suggested,
        reproject=reproject, algorithm=str(algorithm_id), variant=folded, **facts,
    )






_FLOW_FROM_DEM = frozenset()


def _sink_fill(part: str) -> tuple:
    value = (_rules("sink_fill") or {}).get(part)
    return tuple(value) if isinstance(value, list) else ()


def _was_filled(layer) -> bool:

    try:
        history = " ".join(str(item) for item in layer.metadata().history()).casefold()
    except Exception:  # noqa: BLE001
        return False
    return any(mark in history for mark in _sink_fill("marks"))





_fill_step_cache: dict = {"value": "", "count": -1, "names": ()}


def _fill_step_available() -> str:

    names = _sink_fill("algorithms")
    try:
        algs = QgsApplication.processingRegistry().algorithms()
    except Exception:  # noqa: BLE001
        return ""
    if len(algs) != _fill_step_cache["count"] or names != _fill_step_cache["names"]:
        ids = {a.id().casefold(): a.id() for a in algs}
        _fill_step_cache["value"] = next((ids[a] for a in names if a in ids), "")
        _fill_step_cache["count"], _fill_step_cache["names"] = len(algs), names
    return _fill_step_cache["value"]


def _flow_dem(alg, algorithm_id: str, parameters: dict):

    if str(algorithm_id or "").casefold() not in tuning.check_algs("flow_from_dem", _FLOW_FROM_DEM):
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
        return tool_error(
            f"CRS_ON_DEGREES: '{layer.name()}' is in {crs.authid()}, so its cells are degrees while "
            f"its heights are metres: {algorithm_id} routes water on slopes it cannot measure.",
            "CRS_GUARD", hint="processing_flow_degrees", layer_crs=crs.authid(), suggested_crs=suggested,
            algorithm=str(algorithm_id),
        )
    return None


def unfilled_dem_warning(alg, algorithm_id: str, parameters: dict, confirmed: bool) -> dict | None:







    if confirmed or not _sink_fill("marks"):
        return None
    layer = _flow_dem(alg, algorithm_id, parameters)
    if layer is None or _was_filled(layer):
        return None
    fill = _fill_step_available()
    return {"fact": f"the history of '{layer.name()}' names no sink fill.", "hint": "processing_unfilled_dem",
            "dem": layer.name(), "algorithm": str(algorithm_id),
            **({"fill_step": fill, "variant": "named"} if fill else {})}








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
        return tool_error(
            f"The coordinates in '{name}' run to {extent.xMaximum():.0f}, {extent.yMaximum():.0f}, which "
            f"cannot be degrees, so declaring {new_crs.authid()} would put the layer in the wrong place.",
            "CRS_GUARD", hint="crs_declared_degrees_over_metres",
        )
    if not new_crs.isGeographic() and degrees:
        return tool_error(
            f"The coordinates in '{name}' all sit inside 180 by 90, so they are degrees, and "
            f"{new_crs.authid()} is in metres. Declaring it would not reproject the layer.",
            "CRS_GUARD", hint="crs_declared_metres_over_degrees", crs=new_crs.authid(),
        )
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
    return tool_error(
        f"'{layer.name()}' is declared {current.authid()} and its coordinates fit that CRS; read as "
        f"{new_crs.authid()} they fall outside the area that CRS covers, so declaring it would move the layer.",
        "CRS_GUARD", hint="crs_declared_outside_area", crs=new_crs.authid(),
    )
