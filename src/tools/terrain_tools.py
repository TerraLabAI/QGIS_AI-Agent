# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Relief visualisations of a DEM, and the candidate earthworks they show."""



































from __future__ import annotations

import math
import os
import shutil
import time

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsColorRampShader,
    QgsContrastEnhancement,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsGeometry,
    QgsProject,
    QgsRasterLayer,
    QgsRasterShader,
    QgsRectangle,
    QgsRendererCategory,
    QgsSingleBandGrayRenderer,
    QgsSingleBandPseudoColorRenderer,
    QgsSymbol,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QColor

from ..core import limits, net
from ..core.logger import log_warning
from ..core.policy import create_managed_temp_dir
from ..core.qt_compat import field_type
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from ._compat import CONTRAST_STRETCH_MINMAX, GRAY_BLACK_TO_WHITE, GRAY_WHITE_TO_BLACK, SHADER_INTERPOLATED
from .data_tools import _run_on_main_thread
from .layer_lookup import _find_layer, _layer_not_found_error

PRODUCTS = ("lrm", "svf", "openness_positive", "openness_negative", "multi_hillshade", "slope")
DEFAULT_PRODUCTS = ("lrm", "svf", "multi_hillshade", "slope")

_STACK = ("multi_hillshade", "slope", "openness_negative", "openness_positive", "svf", "lrm")
_NUMPY_PRODUCTS = frozenset({"lrm", "svf", "openness_positive", "openness_negative"})

_BLOCK = 1024
_NODATA = -9999.0
_FLOAT_TIFF = ["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=3", "BIGTIFF=IF_SAFER"]
_BYTE_TIFF = ["TILED=YES", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER"]

_DEFAULT_LRM_RADIUS_M = 20.0
_DEFAULT_SVF_RADIUS_M = 10.0
_DEFAULT_DIRECTIONS = 16


_MAX_SCAN_STEPS = 64


_MAX_LRM_RADIUS_PX = 1024
_MAX_SVF_RADIUS_PX = 512

_DEFAULT_THRESHOLD_M = 0.3
_DEFAULT_MIN_AREA_M2 = 10.0
_DEFAULT_MAX_AREA_M2 = 50_000.0
_DEFAULT_MAX_CANDIDATES = 200
_MAX_CANDIDATES = 2000
_DEFAULT_REFERENCE_DISTANCE_M = 50.0



_MAX_POLYGONS = 400_000
_MAX_MEASURED = 50_000

MOUND = "mound or barrow"
PIT = "pit or hollow"
RING = "enclosure or ring"
LINEAR = "linear (road, wall, ditch)"
RECTANGULAR = "rectangular"
IRREGULAR = "irregular"
_TYPE_COLOURS = {
    MOUND: "#d7301f", PIT: "#2b8cbe", RING: "#ae017e", LINEAR: "#fe9929",
    RECTANGULAR: "#238443", IRREGULAR: "#737373",
}

CAUTION = ("These are relief anomalies for a person to check on the relief images and in the field, not "
           "confirmed or dated sites. Shape and height alone cannot tell Roman, Celtic, medieval or modern "
           "work apart, nor an earthwork from a natural form (doline, landslide scar, tree throw, charcoal "
           "platform): compare with the heritage register and excavation records before naming a period.")

_EXTENT_SCHEMA = {
    "anyOf": [
        {"type": "string", "enum": ["canvas"]},
        {
            "type": "object",
            "properties": {k: {"type": "number"} for k in ("xmin", "ymin", "xmax", "ymax")},
            "required": ["xmin", "ymin", "xmax", "ymax"],
        },
    ],
}


def register_terrain_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="terrain_visualisation",
        input_schema={
            "type": "object",
            "properties": {
                "dem": {"type": "string"},
                "products": {"type": "array", "items": {"type": "string", "enum": list(PRODUCTS)},
                             "minItems": 1, "uniqueItems": True},
                "lrm_radius_m": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000},
                "svf_radius_m": {"type": "number", "exclusiveMinimum": 0, "maximum": 500},
                "directions": {"type": "integer", "minimum": 8, "maximum": 32},
                "extent": _EXTENT_SCHEMA,
                "name_prefix": {"type": "string"},
            },
            "required": ["dem"],
        },
        handler=_terrain_visualisation,
        background=True,
    ))
    registry.register(Tool(
        name="detect_terrain_anomalies",
        input_schema={
            "type": "object",
            "properties": {
                "dem": {"type": "string"},
                "lrm_layer": {"type": "string"},
                "lrm_radius_m": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000},
                "threshold_m": {"type": "number", "minimum": 0.01, "maximum": 50},
                "kinds": {"type": "array", "items": {"type": "string", "enum": ["raised", "sunken"]},
                          "minItems": 1, "uniqueItems": True},
                "min_area_m2": {"type": "number", "minimum": 0},
                "max_area_m2": {"type": "number", "exclusiveMinimum": 0},
                "reference_layers": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
                "reference_distance_m": {"type": "number", "exclusiveMinimum": 0, "maximum": 10000},
                "max_candidates": {"type": "integer", "minimum": 1, "maximum": _MAX_CANDIDATES},
                "extent": _EXTENT_SCHEMA,
                "name": {"type": "string"},
            },
        },
        handler=_detect_terrain_anomalies,
        background=True,
    ))




def _raster_facts(name: str, extent) -> dict:
    layer = _find_layer(name)
    if layer is None:
        return _layer_not_found_error(name)
    if not isinstance(layer, QgsRasterLayer):
        return tool_error(f"{layer.name()} is not a raster layer.", "INVALID_ARGS",
                          "Pass the elevation raster (DEM or DTM); list_layers shows each layer's type.")
    if layer.providerType() != "gdal":
        return tool_error(
            f"{layer.name()} uses the {layer.providerType()} provider; the relief is computed from a file.",
            "INVALID_ARGS", "Load the DEM itself (GeoTIFF, COG, VRT) with add_data, not a WMS or XYZ view of it.")
    crs = layer.crs()
    facts = {
        "name": layer.name(),
        "layer_id": layer.id(),
        "source": str(layer.source() or ""),
        "crs_wkt": crs.toWkt(),
        "crs_authid": crs.authid(),
        "geographic": bool(crs.isGeographic()),
        "bands": int(layer.bandCount()),
        "window": None,
    }
    if extent == "canvas":
        try:
            from qgis.utils import iface

            canvas = iface.mapCanvas()
            transform = QgsCoordinateTransform(canvas.mapSettings().destinationCrs(), crs, QgsProject.instance())
            rect = transform.transformBoundingBox(canvas.extent())
        except Exception as exc:  # noqa: BLE001 - no canvas (headless) or an impossible transform
            return tool_error(f"The canvas extent could not be read in the DEM's CRS: {exc}", "INVALID_ARGS",
                              "Pass extent as {xmin, ymin, xmax, ymax} in the DEM's CRS instead.")
        facts["window"] = [rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum()]
    elif isinstance(extent, dict):
        facts["window"] = [float(extent["xmin"]), float(extent["ymin"]), float(extent["xmax"]), float(extent["ymax"])]
    return facts


def _style_gray(layer, lo: float, hi: float, invert: bool) -> None:
    provider = layer.dataProvider()
    renderer = QgsSingleBandGrayRenderer(provider, 1)
    renderer.setGradient(GRAY_WHITE_TO_BLACK if invert else GRAY_BLACK_TO_WHITE)
    enhancement = QgsContrastEnhancement(provider.dataType(1))
    enhancement.setContrastEnhancementAlgorithm(CONTRAST_STRETCH_MINMAX)
    enhancement.setMinimumValue(lo)
    enhancement.setMaximumValue(hi)
    renderer.setContrastEnhancement(enhancement)
    layer.setRenderer(renderer)


def _style_diverging(layer, half_range: float) -> None:
    """Blue below the surroundings, white level, red above: symmetric around zero."""
    provider = layer.dataProvider()
    function = QgsColorRampShader(-half_range, half_range)
    function.setColorRampType(SHADER_INTERPOLATED)
    item = QgsColorRampShader.ColorRampItem
    function.setColorRampItemList([
        item(-half_range, QColor("#2166ac"), f"-{half_range:g} m (lower)"),
        item(0.0, QColor("#f7f7f7"), "0"),
        item(half_range, QColor("#b2182b"), f"+{half_range:g} m (higher)"),
    ])
    shader = QgsRasterShader()
    shader.setRasterShaderFunction(function)
    renderer = QgsSingleBandPseudoColorRenderer(provider, 1, shader)
    if callable(getattr(renderer, "setClassificationMin", None)):
        renderer.setClassificationMin(-half_range)
        renderer.setClassificationMax(half_range)
    layer.setRenderer(renderer)


def _add_products(entries: list) -> list:
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    added = []
    for entry in entries:
        layer = QgsRasterLayer(entry["path"], entry["name"], "gdal")
        if not layer.isValid():
            added.append({"product": entry["product"], "error": f"QGIS cannot open {entry['path']}"})
            continue
        if entry["product"] == "lrm":
            _style_diverging(layer, entry["half_range"])
        else:
            _style_gray(layer, entry["lo"], entry["hi"], entry["invert"])
        layer.renderer().setOpacity(entry["opacity"])
        project.addMapLayer(layer)
        if not entry["visible"]:
            node = root.findLayer(layer.id())
            if node is not None:
                node.setItemVisibilityChecked(False)
        added.append({"product": entry["product"], "layer_name": layer.name(), "layer_id": layer.id()})
    return added


def _reference_geometries(names: list, crs_wkt: str, cap: int, window: tuple | None = None) -> dict:
    """The geometries of the named layers that can score a shape, as WKB in the raster's CRS."""






    target = QgsCoordinateReferenceSystem.fromWkt(crs_wkt)
    out, used = [], []
    for name in names:
        layer = _find_layer(name)
        if layer is None:
            return _layer_not_found_error(name)
        if not isinstance(layer, QgsVectorLayer):
            return tool_error(f"{layer.name()} is not a vector layer.", "INVALID_ARGS",
                              "reference_layers are the points or lines of interest the user already drew.")
        transform = QgsCoordinateTransform(layer.crs(), target, QgsProject.instance())
        request = QgsFeatureRequest().setNoAttributes()
        if window is not None:
            try:
                back = QgsCoordinateTransform(target, layer.crs(), QgsProject.instance())
                request.setFilterRect(back.transformBoundingBox(QgsRectangle(*window)))
            except Exception as exc:  # noqa: BLE001 - QgsCsException: a window off this CRS's world reads it all
                log_warning(f"reference layer {layer.name()} read whole, its window does not project: {exc}")
                request = QgsFeatureRequest().setNoAttributes()
        for feature in layer.getFeatures(request):
            if len(out) >= cap:
                break
            geometry = QgsGeometry(feature.geometry())
            if geometry.isEmpty():
                continue
            try:
                geometry.transform(transform)
            except Exception:  # nosec B112 - a feature that does not project is not a reference
                continue
            out.append(bytes(geometry.asWkb()))
        used.append(layer.name())
    return {"wkb": out, "layers": used, "capped": len(out) >= cap}


def _add_candidates(crs_wkt: str, name: str, rows: list) -> dict:
    crs = QgsCoordinateReferenceSystem.fromWkt(crs_wkt)
    layer = QgsVectorLayer(f"Polygon?crs={crs.authid() or 'EPSG:4326'}", name, "memory")
    if not crs.authid():
        layer.setCrs(crs)
    provider = layer.dataProvider()
    specs = (("rank", "Int"), ("type_hint", "String"), ("relief", "String"), ("peak_m", "Double"),
             ("mean_m", "Double"), ("area_m2", "Double"), ("perim_m", "Double"), ("circular", "Double"),
             ("elongation", "Double"), ("rectangul", "Double"), ("solidity", "Double"), ("hole_share", "Double"),
             ("near_ref_m", "Double"), ("score", "Double"), ("status", "String"))
    provider.addAttributes([QgsField(field, field_type(kind)) for field, kind in specs])
    layer.updateFields()
    features = []
    for row in rows:
        feature = QgsFeature(layer.fields())
        geometry = QgsGeometry()
        geometry.fromWkb(row["wkb"])
        feature.setGeometry(geometry)
        for field, _kind in specs:
            value = row.get(field)
            if value is not None:
                feature.setAttribute(field, value)
        features.append(feature)
    provider.addFeatures(features)
    layer.updateExtents()
    categories = []
    for hint, colour in _TYPE_COLOURS.items():
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        fill = QColor(colour)
        fill.setAlpha(60)
        symbol.setColor(fill)
        try:
            symbol.symbolLayer(0).setStrokeColor(QColor(colour))
            symbol.symbolLayer(0).setStrokeWidth(0.6)
        except Exception:  # nosec B110 - a symbol without a stroke keeps its fill
            pass
        categories.append(QgsRendererCategory(hint, symbol, hint))
    layer.setRenderer(QgsCategorizedSymbolRenderer("type_hint", categories))
    QgsProject.instance().addMapLayer(layer)
    return {"layer_id": layer.id(), "layer_name": layer.name()}




def _libs():
    try:
        import numpy as np
        from osgeo import gdal, ogr, osr
    except ImportError as exc:  # pragma: no cover - every QGIS ships both
        return None, tool_error(f"numpy or GDAL is missing from this QGIS: {exc}", "EXECUTION_FAILED",
                                "Use the Relief Visualization Toolbox plugin instead.")
    gdal.UseExceptions()
    return (np, gdal, ogr, osr), None


def _grid(facts: dict, gdal, osr) -> tuple:
    """(grid, None) for the window of the raster to work on, (None, error) otherwise."""
    try:
        dataset = gdal.Open(facts["source"], gdal.GA_ReadOnly)
    except RuntimeError as exc:
        dataset, reason = None, str(exc)
    else:
        reason = ""
    if dataset is None:
        return None, tool_error(f"GDAL could not open {facts['name']}: {reason[:160]}", "EXECUTION_FAILED",
                                "Load the DEM file itself with add_data; a layer QGIS draws through another "
                                "provider cannot be read cell by cell.")
    x0, dx, rx, y0, ry, dy = dataset.GetGeoTransform()
    if dx == 0 or dy == 0 or rx != 0 or ry != 0:
        return None, tool_error(f"{facts['name']} has a rotated or missing geotransform.", "EXECUTION_FAILED",
                                "Warp it north-up first: run_processing gdal:warpreproject.")
    full_w, full_h = dataset.RasterXSize, dataset.RasterYSize
    col0, row0, width, height = 0, 0, full_w, full_h
    if facts.get("window"):
        xmin, ymin, xmax, ymax = facts["window"]
        cols = sorted(((xmin - x0) / dx, (xmax - x0) / dx))
        rows = sorted(((ymin - y0) / dy, (ymax - y0) / dy))
        col0, row0 = max(0, int(math.floor(cols[0]))), max(0, int(math.floor(rows[0])))
        col1, row1 = min(full_w, int(math.ceil(cols[1]))), min(full_h, int(math.ceil(rows[1])))
        width, height = col1 - col0, row1 - row0
        if width < 3 or height < 3:
            return None, tool_error(f"The extent covers fewer than three cells of {facts['name']}.", "INVALID_ARGS",
                                    "Check the extent is in the DEM's CRS and overlaps it (get_layer_info).")
    srs = osr.SpatialReference()
    srs.ImportFromWkt(facts["crs_wkt"])
    if facts["geographic"]:
        lat = y0 + (row0 + height / 2.0) * dy
        unit_m = None
        cell_x_m = abs(dx) * 111_320.0 * max(0.05, math.cos(math.radians(lat)))
        cell_y_m = abs(dy) * 110_540.0

        cell_x_z, cell_y_z = cell_x_m, cell_y_m
    else:
        unit_m = float(srs.GetLinearUnits() or 1.0)
        cell_x_m, cell_y_m = abs(dx) * unit_m, abs(dy) * unit_m


        cell_x_z, cell_y_z = abs(dx), abs(dy)
    band = dataset.GetRasterBand(1)
    return {
        "dataset": dataset, "band": band, "nodata": band.GetNoDataValue(),
        "x0": x0, "dx": dx, "y0": y0, "dy": dy, "full_w": full_w, "full_h": full_h,
        "col0": col0, "row0": row0, "width": width, "height": height,
        "cell_x_m": cell_x_m, "cell_y_m": cell_y_m, "cell_x_z": cell_x_z, "cell_y_z": cell_y_z,
        "unit_m": unit_m, "wkt": facts["crs_wkt"], "geographic": facts["geographic"],
        "geotransform": (x0 + col0 * dx, dx, 0.0, y0 + row0 * dy, 0.0, dy),
    }, None


def _cells_error(grid: dict, what: str) -> dict | None:
    cells = grid["width"] * grid["height"]
    allowed = int(limits.current("TERRAIN_MAX_CELLS"))
    if cells <= allowed:
        return None
    side_m = math.sqrt(allowed * grid["cell_x_m"] * grid["cell_y_m"])
    return limits.refusal(
        f"The {what} window", f"{grid['width']:,} by {grid['height']:,} cells ({cells:,})", f"{allowed:,} cells",
        f"Pass extent 'canvas' after zooming to the area of interest, or a box about {side_m:,.0f} m across in "
        f"the DEM's CRS; run tile by tile for a larger area.")


def _stop(cancelled) -> None:
    if cancelled is not None and cancelled():
        raise InterruptedError("stopped")


def _blocks(width: int, height: int, size: int = _BLOCK):
    for by in range(0, height, size):
        for bx in range(0, width, size):
            yield bx, by, min(size, width - bx), min(size, height - by)


def _read_padded(grid: dict, bx: int, by: int, bw: int, bh: int, pad: int, np, band=None, nodata=None):
    """Cells of the block plus ``pad`` on each side, NaN outside the raster or on nodata."""




    band = band if band is not None else grid["band"]
    nodata = nodata if band is not grid["band"] else grid["nodata"]
    left, top = grid["col0"] + bx - pad, grid["row0"] + by - pad
    width, height = bw + 2 * pad, bh + 2 * pad
    x_lo, y_lo = max(0, left), max(0, top)
    x_hi, y_hi = min(grid["full_w"], left + width), min(grid["full_h"], top + height)
    out = np.full((height, width), np.nan, dtype="float32")
    if x_hi > x_lo and y_hi > y_lo:
        raw = band.ReadAsArray(x_lo, y_lo, x_hi - x_lo, y_hi - y_lo)
        values = raw.astype("float32")
        if nodata is not None:
            values[raw == nodata] = np.nan
        out[y_lo - top:y_hi - top, x_lo - left:x_hi - left] = values
    return out




def local_relief(padded, pad: int, bw: int, bh: int, rx: int, ry: int, np):
    """The block's DEM minus its mean over (2rx+1) by (2ry+1) cells, NaN-aware."""








    valid = np.isfinite(padded)
    base = float(np.nanmin(padded)) if valid.any() else 0.0
    height, width = padded.shape
    sums = np.zeros((height + 1, width + 1), dtype="float64")
    counts = np.zeros((height + 1, width + 1), dtype="float64")
    np.cumsum(np.cumsum(np.where(valid, padded - base, 0.0), axis=0, dtype="float64"), axis=1, out=sums[1:, 1:])
    np.cumsum(np.cumsum(valid, axis=0, dtype="float64"), axis=1, out=counts[1:, 1:])
    top, left = pad - ry, pad - rx
    bottom, right = pad + ry + 1, pad + rx + 1

    def window(table):
        return (table[bottom:bottom + bh, right:right + bw] - table[top:top + bh, right:right + bw]
                - table[bottom:bottom + bh, left:left + bw] + table[top:top + bh, left:left + bw])

    total, count = window(sums), window(counts)
    core = padded[pad:pad + bh, pad:pad + bw]
    with np.errstate(invalid="ignore", divide="ignore"):
        relief = (core - base) - total / count
    relief[(count < 0.9 * (2 * rx + 1) * (2 * ry + 1)) | ~np.isfinite(core)] = np.nan
    return relief.astype("float32")


def scan_distances(radius_px: int, cap: int = _MAX_SCAN_STEPS) -> list:
    """Distances in cells along one direction: all of them, or geometric past ``cap``."""
    radius_px = max(1, int(radius_px))
    if radius_px <= cap:
        return list(range(1, radius_px + 1))
    head = 16
    out = list(range(1, head + 1))
    ratio = (radius_px / float(head)) ** (1.0 / (cap - head))
    value = float(head)
    while len(out) < cap - 1:
        value *= ratio
        step = int(round(value))
        if step > out[-1] and step < radius_px:
            out.append(step)
    out.append(radius_px)
    return out


def scan_offsets(radius_m: float, directions: int, grid: dict) -> tuple:
    """Per direction, the (dy, dx, distance in height units) of each cell looked at, and the pad."""
    step_m = min(grid["cell_x_m"], grid["cell_y_m"])
    distances = scan_distances(int(round(radius_m / step_m)))
    table, pad = [], 1
    for k in range(directions):
        azimuth = 2.0 * math.pi * k / directions
        seen, row = set(), []
        for cells in distances:
            ground = cells * step_m
            dx = int(round(ground * math.sin(azimuth) / grid["cell_x_m"]))
            dy = int(round(-ground * math.cos(azimuth) / grid["cell_y_m"]))
            if (dy, dx) == (0, 0) or (dy, dx) in seen:
                continue
            seen.add((dy, dx))
            row.append((dy, dx, math.hypot(dx * grid["cell_x_z"], dy * grid["cell_y_z"])))
            pad = max(pad, abs(dx), abs(dy))
        table.append(row)
    return table, pad, len(distances)


def horizon_scan(padded, pad: int, bw: int, bh: int, offsets: list, np, svf=True, positive=True, negative=True):
    """Sky-view factor (0-1) and openness from above and below (degrees) of the block's cells."""
    core = padded[pad:pad + bh, pad:pad + bw]
    out_svf = np.zeros(core.shape, dtype="float32") if svf else None
    out_pos = np.zeros(core.shape, dtype="float32") if positive else None
    out_neg = np.zeros(core.shape, dtype="float32") if negative else None
    ratio = np.empty(core.shape, dtype="float32")
    need_max = svf or positive
    for row in offsets:
        highest = np.full(core.shape, -np.inf, dtype="float32") if need_max else None
        lowest = np.full(core.shape, np.inf, dtype="float32") if negative else None
        for dy, dx, distance in row:
            neighbour = padded[pad + dy:pad + dy + bh, pad + dx:pad + dx + bw]
            np.subtract(neighbour, core, out=ratio)
            ratio /= distance
            if need_max:
                np.fmax(highest, ratio, out=highest)
            if negative:
                np.fmin(lowest, ratio, out=lowest)
        if need_max:
            highest[~np.isfinite(highest)] = 0.0
            angle = np.arctan(highest)
            if svf:
                out_svf += 1.0 - np.sin(np.maximum(angle, 0.0))
            if positive:
                out_pos += (math.pi / 2.0) - angle
        if negative:
            lowest[~np.isfinite(lowest)] = 0.0
            out_neg += (math.pi / 2.0) + np.arctan(lowest)
    count = float(len(offsets))
    invalid = ~np.isfinite(core)
    results = {}
    if svf:
        out_svf /= count
        out_svf[invalid] = np.nan
        results["svf"] = out_svf
    if positive:
        out_pos = np.degrees(out_pos / count).astype("float32")
        out_pos[invalid] = np.nan
        results["openness_positive"] = out_pos
    if negative:
        out_neg = np.degrees(out_neg / count).astype("float32")
        out_neg[invalid] = np.nan
        results["openness_negative"] = out_neg
    return results




def _create_tiff(gdal, path: str, grid: dict, dtype, options, nodata):
    dataset = gdal.GetDriverByName("GTiff").Create(path, grid["width"], grid["height"], 1, dtype, options)
    dataset.SetGeoTransform(grid["geotransform"])
    dataset.SetProjection(grid["wkt"])
    if nodata is not None:
        dataset.GetRasterBand(1).SetNoDataValue(nodata)
    return dataset


def _gdal_product(gdal, grid: dict, source: str, product: str, path: str, cancelled) -> None:
    """Slope or the multi-directional hillshade over the window, with GDAL's own code."""
    vrt = f"/vsimem/terrain_{os.getpid()}_{time.monotonic_ns()}.vrt"
    gdal.Translate(vrt, grid["dataset"], format="VRT",
                   srcWin=[grid["col0"], grid["row0"], grid["width"], grid["height"]])

    def progress(_complete, _message, _data):
        return 0 if (cancelled is not None and cancelled()) else 1

    settings = {"computeEdges": True, "format": "GTiff", "creationOptions": _BYTE_TIFF if product == "multi_hillshade"
                else _FLOAT_TIFF, "callback": progress}
    if grid["geographic"]:
        settings["scale"] = 111_120.0
    try:
        if product == "multi_hillshade":
            settings["multiDirectional"] = True
            gdal.DEMProcessing(path, vrt, "hillshade", options=gdal.DEMProcessingOptions(**settings))
        else:
            settings["slopeFormat"] = "degree"
            gdal.DEMProcessing(path, vrt, "slope", options=gdal.DEMProcessingOptions(**settings))
    finally:
        gdal.Unlink(vrt)
    _stop(cancelled)


def _finish(gdal, np, path: str, overviews: bool = True) -> dict:
    """Overviews for drawing a 0.25 m tile at full extent, and the value range for the style."""
    dataset = gdal.Open(path, gdal.GA_Update if overviews else gdal.GA_ReadOnly)
    width, height = dataset.RasterXSize, dataset.RasterYSize
    if overviews:
        levels, level = [], 2
        while max(width, height) / level >= 256:
            levels.append(level)
            level *= 2
        if levels:
            dataset.BuildOverviews("AVERAGE", levels)
    band = dataset.GetRasterBand(1)
    shrink = max(1.0, max(width, height) / 1024.0)
    raw = band.ReadAsArray(0, 0, width, height, buf_xsize=max(1, int(width / shrink)),
                           buf_ysize=max(1, int(height / shrink)))
    values = raw.astype("float32")
    nodata = band.GetNoDataValue()
    if nodata is not None:
        values = values[raw != nodata]
    values = values[np.isfinite(values)]
    dataset = None
    if values.size == 0:
        return {"p2": 0.0, "p98": 1.0, "abs98": 1.0}
    p2, p98 = (float(v) for v in np.percentile(values, [2, 98]))
    return {"p2": p2, "p98": p98, "abs98": float(np.percentile(np.abs(values), 98))}


def _numpy_products(np, gdal, grid: dict, wanted: set, paths: dict, lrm_r: tuple, offsets, pad_scan, cancelled):
    outputs = {product: _create_tiff(gdal, paths[product], grid, gdal.GDT_Float32, _FLOAT_TIFF, _NODATA)
               for product in wanted}
    rx, ry = lrm_r
    pad = max(rx if "lrm" in wanted else 0, ry if "lrm" in wanted else 0,
              pad_scan if wanted & {"svf", "openness_positive", "openness_negative"} else 0)
    try:
        for bx, by, bw, bh in _blocks(grid["width"], grid["height"]):
            _stop(cancelled)
            padded = _read_padded(grid, bx, by, bw, bh, pad, np)
            blocks = {}
            if "lrm" in wanted:
                blocks["lrm"] = local_relief(padded, pad, bw, bh, rx, ry, np)
            scanned = wanted & {"svf", "openness_positive", "openness_negative"}
            if scanned:
                blocks.update(horizon_scan(padded, pad, bw, bh, offsets, np, svf="svf" in scanned,
                                           positive="openness_positive" in scanned,
                                           negative="openness_negative" in scanned))
            for product, values in blocks.items():
                values[~np.isfinite(values)] = _NODATA
                outputs[product].GetRasterBand(1).WriteArray(values, bx, by)
        for product in list(outputs):
            outputs[product].FlushCache()
    finally:

        outputs.clear()


def _radius_px(radius_m: float, grid: dict) -> tuple:
    return (max(1, int(round(radius_m / grid["cell_x_m"]))), max(1, int(round(radius_m / grid["cell_y_m"]))))




def _terrain_visualisation(args: dict) -> dict:
    libs, error = _libs()
    if error:
        return error
    np, gdal, _ogr, osr = libs
    products = list(args.get("products") or DEFAULT_PRODUCTS)
    unknown = [p for p in products if p not in PRODUCTS]
    if unknown:
        return tool_error(f"Unknown products: {', '.join(map(str, unknown))}.", "INVALID_ARGS",
                          f"Choose among {', '.join(PRODUCTS)}.")
    lrm_radius_m = float(args.get("lrm_radius_m") or _DEFAULT_LRM_RADIUS_M)
    svf_radius_m = float(args.get("svf_radius_m") or _DEFAULT_SVF_RADIUS_M)
    directions = int(args.get("directions") or _DEFAULT_DIRECTIONS)
    cancelled = net.current_cancel_check()

    facts = _run_on_main_thread(_raster_facts, str(args["dem"]), args.get("extent"))
    if "_error" in facts or facts.get("isError"):
        return facts
    grid, error = _grid(facts, gdal, osr)
    if error:
        return error
    error = _cells_error(grid, "relief")
    if error:
        return error
    lrm_r = _radius_px(lrm_radius_m, grid)
    if "lrm" in products and max(lrm_r) > _MAX_LRM_RADIUS_PX:
        return tool_error(
            f"lrm_radius_m {lrm_radius_m:g} is {max(lrm_r)} cells at {grid['cell_x_m']:.2f} m, over "
            f"{_MAX_LRM_RADIUS_PX}.", "INVALID_ARGS",
            f"Pass lrm_radius_m {_MAX_LRM_RADIUS_PX * min(grid['cell_x_m'], grid['cell_y_m']):.0f} or less, or "
            f"resample the DEM coarser first.")
    offsets, pad_scan, steps = scan_offsets(svf_radius_m, directions, grid)
    if any(p in ("svf", "openness_positive", "openness_negative") for p in products):
        if pad_scan > _MAX_SVF_RADIUS_PX:
            return tool_error(
                f"svf_radius_m {svf_radius_m:g} is {pad_scan} cells, over {_MAX_SVF_RADIUS_PX}.", "INVALID_ARGS",
                f"Pass svf_radius_m {_MAX_SVF_RADIUS_PX * min(grid['cell_x_m'], grid['cell_y_m']):.0f} or less.")

    prefix = str(args.get("name_prefix") or facts["name"]).strip() or "DEM"
    folder = create_managed_temp_dir("terrain")
    names = {
        "lrm": f"{prefix} LRM {lrm_radius_m:g} m",
        "svf": f"{prefix} SVF {svf_radius_m:g} m",
        "openness_positive": f"{prefix} openness + {svf_radius_m:g} m",
        "openness_negative": f"{prefix} openness - {svf_radius_m:g} m",
        "multi_hillshade": f"{prefix} multi-hillshade",
        "slope": f"{prefix} slope",
    }
    paths = {p: os.path.join(folder, f"{p}.tif") for p in products}
    started = time.monotonic()
    timings = {}
    try:
        wanted = set(products) & _NUMPY_PRODUCTS
        if wanted:
            _numpy_products(np, gdal, grid, wanted, paths, lrm_r, offsets, pad_scan, cancelled)
            timings["+".join(sorted(wanted))] = round(time.monotonic() - started, 1)
        for product in products:
            if product not in _NUMPY_PRODUCTS:
                mark = time.monotonic()
                _gdal_product(gdal, grid, facts["source"], product, paths[product], cancelled)
                timings[product] = round(time.monotonic() - mark, 1)
        mark = time.monotonic()
        stats = {product: _finish(gdal, np, paths[product]) for product in products}
        timings["overviews and ranges"] = round(time.monotonic() - mark, 1)
    except InterruptedError:
        failure = tool_error("Stopped before the relief images were finished.", "CANCELLED",
                             "Nothing was added to the project.")
    except RuntimeError as exc:
        failure = tool_error(f"GDAL failed while computing the relief: {exc}", "EXECUTION_FAILED",
                             "Check the DEM opens with get_layer_info; a remote tile may have timed out.")
    else:
        failure = None
    if failure is not None:


        shutil.rmtree(folder, ignore_errors=True)
        return failure
    seconds = time.monotonic() - started

    blend = "lrm" in products and bool({"svf", "multi_hillshade"} & set(products))
    entries = []
    for product in _STACK:
        if product not in products:
            continue
        s = stats[product]
        entry = {"product": product, "path": paths[product], "name": names[product], "opacity": 1.0,
                 "visible": product in ("lrm", "svf", "multi_hillshade") or len(products) == 1, "invert": False}
        if product == "lrm":
            entry["half_range"] = round(min(5.0, max(0.2, s["abs98"])), 2)
            entry["opacity"] = 0.6 if blend else 1.0
            entry["range"] = [-entry["half_range"], entry["half_range"]]
            entry["style"] = "blue lower, white level, red higher, symmetric"
        elif product == "multi_hillshade":
            entry.update(lo=0.0, hi=255.0, style="gray 0-255")
        else:
            lo, hi = s["p2"], s["p98"]
            if product == "svf":
                hi = min(1.0, max(hi, lo + 1e-3))
            if product == "slope":
                lo = 0.0
            entry.update(lo=round(lo, 4), hi=round(max(hi, lo + 1e-3), 4))

            entry["invert"] = product in ("slope", "openness_negative")
            entry["style"] = f"gray {'white to black' if entry['invert'] else 'black to white'}, 2-98 %"
        entry.setdefault("range", [entry.get("lo"), entry.get("hi")])
        entries.append(entry)
    try:
        added = _run_on_main_thread(_add_products, entries, timeout=60)
    except InterruptedError:
        shutil.rmtree(folder, ignore_errors=True)
        return tool_error("Stopped before the relief images were added.", "CANCELLED",
                          "Nothing was added to the project.")
    by_product = {a["product"]: a for a in added}
    layers = []
    for entry in reversed(entries):
        got = by_product.get(entry["product"], {})
        layers.append({"product": entry["product"], "layer_name": got.get("layer_name"),
                       "error": got.get("error"), "path": entry["path"], "style": entry["style"],
                       "range": entry["range"], "opacity": entry["opacity"], "visible": entry["visible"]})
    top = next((layer for layer in layers if layer["layer_name"]), None)
    result = {
        "layer_name": top["layer_name"] if top else None,
        "layers": layers,
        "dem": facts["name"],
        "cells": grid["width"] * grid["height"],
        "cell_size_m": round((grid["cell_x_m"] + grid["cell_y_m"]) / 2.0, 3),
        "seconds": round(seconds, 1),
        "seconds_by_step": timings,
        "parameters": {
            "lrm_radius_m": lrm_radius_m, "lrm_window_cells": f"{2 * lrm_r[0] + 1} x {2 * lrm_r[1] + 1}",
            "svf_radius_m": svf_radius_m, "directions": directions, "scan_steps": steps,
        },
        "read_it": ("LRM: red stands above its surroundings (bank, mound, wall, platform edge), blue below "
                    "(ditch, hollow way, pit); the value is metres. SVF and positive openness: dark where the "
                    "sky is hidden (ditch floors, the inside of a bank), light on crests. Multi-hillshade and "
                    "slope show the form without a single sun direction hiding features parallel to it."),
        "next": ("To turn the LRM into candidate polygons, call detect_terrain_anomalies with lrm_layer set to "
                 "the LRM layer and the user's own points or lines of interest as reference_layers."),
    }
    if facts.get("window"):
        result["window_cells"] = [grid["width"], grid["height"]]
    return result




def _metric_transform(grid: dict, osr):
    """(to_metric, metres per unit) for measuring shapes: identity on a projected CRS."""
    if not grid["geographic"]:
        return None, grid["unit_m"] or 1.0
    lon = grid["geotransform"][0] + grid["width"] * grid["dx"] / 2.0
    lat = grid["geotransform"][3] + grid["height"] * grid["dy"] / 2.0
    source = osr.SpatialReference()
    source.ImportFromWkt(grid["wkt"])
    local = osr.SpatialReference()
    local.ImportFromProj4(f"+proj=laea +lat_0={lat:.6f} +lon_0={lon:.6f} +datum=WGS84 +units=m +no_defs")
    try:
        source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        local.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    except AttributeError:  # pragma: no cover - GDAL < 3
        pass
    return osr.CoordinateTransformation(source, local), 1.0


def shape_metrics(geometry: QgsGeometry, cell: float) -> dict:
    """Unit-free shape numbers of one polygon, measured on its outline smoothed by one cell."""





    smooth = geometry.simplify(0.75 * cell)
    if smooth.isEmpty() or smooth.area() <= 0:
        smooth = geometry
    area = smooth.area()
    filled = QgsGeometry(smooth)
    filled = filled.removeInteriorRings() if hasattr(filled, "removeInteriorRings") else filled
    filled_area = max(filled.area(), area, 1e-12)
    outline = max(filled.length(), 1e-12)
    hull = smooth.convexHull().area() or filled_area
    elongation, rectangularity = 1.0, 0.0
    try:
        box = smooth.orientedMinimumBoundingBox()
        box_area, box_w, box_h = float(box[1]), float(box[3]), float(box[4])
        if box_area > 0 and min(box_w, box_h) > 0:
            elongation = max(box_w, box_h) / min(box_w, box_h)
            rectangularity = filled_area / box_area
    except Exception:  # nosec B110 - a degenerate sliver keeps the defaults
        pass
    return {
        "circularity": min(1.0, 4.0 * math.pi * filled_area / (outline * outline)),
        "elongation": elongation,
        "rectangularity": min(1.0, rectangularity),
        "solidity": min(1.0, area / hull),
        "hole_share": max(0.0, 1.0 - area / filled_area),
    }


def type_hint(raised: bool, m: dict) -> str:
    """What a shape looks like. Ordered: a ring with a hole is a ring whatever its box."""


    if m["hole_share"] >= 0.15 and m["circularity"] >= 0.6 and m["elongation"] < 2.5:
        return RING
    if m["elongation"] >= 3.5:
        return LINEAR
    if m["rectangularity"] >= 0.85:
        return RECTANGULAR
    if m["circularity"] >= 0.7 and m["elongation"] < 2.0:
        return MOUND if raised else PIT
    return IRREGULAR


def relief_weight(peak_m: float) -> float:
    """1 over the heights most earthworks have (0.5 to 1.5 m), rising from 0 below, falling to 0 at 6 m."""




    height = abs(peak_m)
    if height < 0.5:
        return height / 0.5
    if height <= 1.5:
        return 1.0
    return max(0.0, 1.0 - (height - 1.5) / 4.5)


def candidate_score(hint: str, peak_m: float, m: dict, near: float | None) -> float:
    """0-100: relief in the earthwork range (60 %) and shape regularity (40 %); a quarter goes to proximity."""
    shape = {RING: m["circularity"], RECTANGULAR: m["rectangularity"], MOUND: m["circularity"],
             PIT: m["circularity"],

             LINEAR: m["rectangularity"] * min(1.0, m["elongation"] / 8.0)}.get(hint, 0.1)
    base = 0.6 * relief_weight(peak_m) + 0.4 * shape
    return round(100.0 * (base if near is None else 0.75 * base + 0.25 * near), 1)


def _reference_buckets(wkbs: list, size: float) -> dict:
    """Reference geometries filed by the grid cells their bounding boxes cover."""
    buckets = {"size": size, "cells": {}}
    for wkb in wkbs:
        geometry = QgsGeometry()
        geometry.fromWkb(wkb)
        box = geometry.boundingBox()
        for cx in range(int(math.floor(box.xMinimum() / size)), int(math.floor(box.xMaximum() / size)) + 1):
            for cy in range(int(math.floor(box.yMinimum() / size)), int(math.floor(box.yMaximum() / size)) + 1):
                buckets["cells"].setdefault((cx, cy), []).append(geometry)
    return buckets


def _nearest_reference(buckets: dict, geometry: QgsGeometry) -> float | None:
    """Distance to the nearest reference within one bucket around the shape, None past that."""



    size = buckets["size"]
    box = geometry.boundingBox()
    seen, best = set(), None
    for cx in range(int(math.floor(box.xMinimum() / size)) - 1, int(math.floor(box.xMaximum() / size)) + 2):
        for cy in range(int(math.floor(box.yMinimum() / size)) - 1, int(math.floor(box.yMaximum() / size)) + 2):
            for reference in buckets["cells"].get((cx, cy), ()):
                if id(reference) in seen:
                    continue
                seen.add(id(reference))
                distance = reference.distance(geometry)
                best = distance if best is None else min(best, distance)
    return best


def _detect_terrain_anomalies(args: dict) -> dict:
    """detect_terrain_anomalies, its scratch rasters removed however the call ends."""





    folder = create_managed_temp_dir("anomalies")
    try:
        return _detect_anomalies(args, folder)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _detect_anomalies(args: dict, folder: str) -> dict:
    libs, error = _libs()
    if error:
        return error
    np, gdal, ogr, osr = libs
    source_name = args.get("lrm_layer") or args.get("dem")
    if not source_name:
        return tool_error("Pass dem, or lrm_layer with an LRM made by terrain_visualisation.", "INVALID_ARGS",
                          "dem is the elevation raster; the local relief is computed from it on the way.")
    threshold = float(args.get("threshold_m") or _DEFAULT_THRESHOLD_M)
    kinds = set(args.get("kinds") or ("raised", "sunken"))
    min_area = float(args["min_area_m2"]) if args.get("min_area_m2") is not None else _DEFAULT_MIN_AREA_M2
    max_area = float(args.get("max_area_m2") or _DEFAULT_MAX_AREA_M2)
    keep = min(_MAX_CANDIDATES, int(args.get("max_candidates") or _DEFAULT_MAX_CANDIDATES),
               int(limits.current("MAX_FEATURES_CREATED")))
    reference_m = float(args.get("reference_distance_m") or _DEFAULT_REFERENCE_DISTANCE_M)
    lrm_radius_m = float(args.get("lrm_radius_m") or _DEFAULT_LRM_RADIUS_M)
    cancelled = net.current_cancel_check()

    facts = _run_on_main_thread(_raster_facts, str(source_name), args.get("extent"))
    if "_error" in facts or facts.get("isError"):
        return facts
    grid, error = _grid(facts, gdal, osr)
    if error:
        return error
    error = _cells_error(grid, "anomaly search")
    if error:
        return error
    to_metric, unit_m = _metric_transform(grid, osr)
    cell_m = min(grid["cell_x_m"], grid["cell_y_m"])
    pixel_m2 = grid["cell_x_m"] * grid["cell_y_m"]
    started = time.monotonic()
    try:
        if args.get("lrm_layer"):
            lrm_band, lrm_nodata, lrm_col0, lrm_row0 = grid["band"], grid["nodata"], grid["col0"], grid["row0"]
            lrm_source = "lrm_layer"
        else:
            rx, ry = _radius_px(lrm_radius_m, grid)
            if max(rx, ry) > _MAX_LRM_RADIUS_PX:
                return tool_error(f"lrm_radius_m {lrm_radius_m:g} is {max(rx, ry)} cells, over "
                                  f"{_MAX_LRM_RADIUS_PX}.", "INVALID_ARGS", "Pass a smaller lrm_radius_m.")
            lrm_path = os.path.join(folder, "lrm.tif")
            _numpy_products(np, gdal, grid, {"lrm"}, {"lrm": lrm_path}, (rx, ry), None, 0, cancelled)
            lrm_dataset = gdal.Open(lrm_path)
            lrm_band, lrm_nodata, lrm_col0, lrm_row0 = lrm_dataset.GetRasterBand(1), _NODATA, 0, 0
            lrm_source = f"computed from {facts['name']} with lrm_radius_m {lrm_radius_m:g}"
        lrm_grid = dict(grid, band=lrm_band, nodata=lrm_nodata, col0=lrm_col0, row0=lrm_row0)
        if not args.get("lrm_layer"):
            lrm_grid.update(full_w=grid["width"], full_h=grid["height"])


        classes_path = os.path.join(folder, "classes.tif")
        classes = _create_tiff(gdal, classes_path, grid, gdal.GDT_Byte, _BYTE_TIFF, 0)
        class_band = classes.GetRasterBand(1)
        for bx, by, bw, bh in _blocks(grid["width"], grid["height"]):
            _stop(cancelled)
            values = _read_padded(lrm_grid, bx, by, bw, bh, 0, np, band=lrm_band, nodata=lrm_nodata)
            out = np.zeros(values.shape, dtype="uint8")
            with np.errstate(invalid="ignore"):
                if "raised" in kinds:
                    out[values >= threshold] = 1
                if "sunken" in kinds:
                    out[values <= -threshold] = 2
            class_band.WriteArray(out, bx, by)
        class_band.FlushCache()


        min_px = max(2, int(math.ceil(min_area / pixel_m2)))
        gdal.SieveFilter(class_band, None, class_band, min_px, 8, callback=None)
        class_band.FlushCache()
        _stop(cancelled)
        srs = osr.SpatialReference()
        srs.ImportFromWkt(grid["wkt"])
        memory = (ogr.GetDriverByName("MEM") or ogr.GetDriverByName("Memory")).CreateDataSource("anomalies")
        polygons = memory.CreateLayer("anomalies", srs=srs, geom_type=ogr.wkbPolygon)
        polygons.CreateField(ogr.FieldDefn("cls", ogr.OFTInteger))
        gdal.Polygonize(class_band, class_band, polygons, 0, ["8CONNECTED=8"], callback=None)
        found = polygons.GetFeatureCount()
        if found > _MAX_POLYGONS:
            return tool_error(
                f"{found:,} shapes stand {threshold:g} m out of the local relief: that is noise, not features.",
                "INVALID_ARGS", f"Raise threshold_m (try {threshold * 2:g}) or min_area_m2, or pass a smaller extent.")


        kept = []
        for feature in polygons:
            geometry = feature.GetGeometryRef()
            if geometry is None:
                continue
            if to_metric is not None:
                geometry_m = geometry.Clone()
                geometry_m.Transform(to_metric)
                area_m2 = geometry_m.GetArea()
            else:
                geometry_m = geometry
                area_m2 = geometry.GetArea() * unit_m * unit_m
            if area_m2 < min_area or area_m2 > max_area:
                continue
            kept.append((int(feature.GetField("cls")), bytes(geometry.ExportToWkb()),
                         bytes(geometry_m.ExportToWkb()), area_m2))
            if len(kept) > _MAX_MEASURED:
                return tool_error(
                    f"Over {_MAX_MEASURED:,} shapes between {min_area:g} and {max_area:g} m2 at {threshold:g} m.",
                    "INVALID_ARGS", "Raise threshold_m or min_area_m2, or pass a smaller extent.")
        _stop(cancelled)


        labels_path = os.path.join(folder, "labels.tif")
        labels = _create_tiff(gdal, labels_path, grid, gdal.GDT_Int32, _BYTE_TIFF, 0)
        burn = memory.CreateLayer("kept", srs=srs, geom_type=ogr.wkbPolygon)
        burn.CreateField(ogr.FieldDefn("lab", ogr.OFTInteger))
        for index, (_cls, wkb, _wkb_m, _area) in enumerate(kept, start=1):
            row = ogr.Feature(burn.GetLayerDefn())
            row.SetField("lab", index)
            row.SetGeometry(ogr.CreateGeometryFromWkb(wkb))
            burn.CreateFeature(row)
        if kept:
            gdal.RasterizeLayer(labels, [1], burn, options=["ATTRIBUTE=lab"])
        label_band = labels.GetRasterBand(1)
        size = len(kept) + 1
        sign = np.ones(size, dtype="float32")
        sign[[i for i, item in enumerate(kept, start=1) if item[0] == 2]] = -1.0
        peak = np.full(size, -np.inf, dtype="float64")
        sums = np.zeros(size, dtype="float64")
        counts = np.zeros(size, dtype="float64")
        for bx, by, bw, bh in _blocks(grid["width"], grid["height"]) if kept else ():
            _stop(cancelled)
            lab = label_band.ReadAsArray(bx, by, bw, bh)
            mask = lab > 0
            if not mask.any():
                continue
            values = _read_padded(lrm_grid, bx, by, bw, bh, 0, np, band=lrm_band, nodata=lrm_nodata)
            mask &= np.isfinite(values)
            index = lab[mask]
            signed = values[mask] * sign[index]
            np.maximum.at(peak, index, signed)
            sums += np.bincount(index, weights=signed, minlength=size)
            counts += np.bincount(index, minlength=size)
    except InterruptedError:
        return tool_error("Stopped before the candidates were finished.", "CANCELLED", "Nothing was added.")
    except RuntimeError as exc:
        return tool_error(f"GDAL failed while extracting the anomalies: {exc}", "EXECUTION_FAILED",
                          "Check the raster opens with get_layer_info.")


    distance_unit_m = (unit_m if to_metric is None else
                       (grid["cell_x_m"] / abs(grid["dx"]) + grid["cell_y_m"] / abs(grid["dy"])) / 2.0)
    references = None
    if args.get("reference_layers"):


        reach = 9.0 * reference_m / distance_unit_m
        gx, gy = grid["geotransform"][0], grid["geotransform"][3]
        xs = sorted((gx, gx + grid["width"] * grid["dx"]))
        ys = sorted((gy, gy + grid["height"] * grid["dy"]))
        window = (xs[0] - reach, ys[0] - reach, xs[1] + reach, ys[1] + reach)
        try:
            references = _run_on_main_thread(_reference_geometries, list(args["reference_layers"]), grid["wkt"],
                                             int(limits.current("MAX_FEATURES_MATERIALISED")), window, timeout=30)
        except InterruptedError:
            return tool_error("Stopped before the candidates were finished.", "CANCELLED", "Nothing was added.")
        except TimeoutError:
            return tool_error("Reading the reference layers took QGIS more than 30 s, so no candidate was added.",
                              "EXECUTION_FAILED",
                              "Pass fewer or smaller reference_layers (a selection saved as its own layer), or none.")
        if "_error" in references or references.get("isError"):
            return references
    buckets = None
    if references and references["wkb"]:


        bucket = max(reference_m * 3.0 / distance_unit_m, 1e-9)
        buckets = _reference_buckets(references["wkb"], bucket)

    rows = []
    for number, (cls, wkb, wkb_m, area_m2) in enumerate(kept, start=1):


        if number % 256 == 0 and cancelled is not None and cancelled():
            return tool_error("Stopped before the candidates were finished.", "CANCELLED", "Nothing was added.")
        if counts[number] <= 0:
            continue
        geometry_m = QgsGeometry()
        geometry_m.fromWkb(wkb_m)
        metrics = shape_metrics(geometry_m, cell_m if to_metric is not None else cell_m / unit_m)
        raised = cls == 1
        peak_m = float(peak[number]) * (1.0 if raised else -1.0)
        mean_m = float(sums[number] / counts[number]) * (1.0 if raised else -1.0)
        geometry = QgsGeometry()
        geometry.fromWkb(wkb)
        near_m, near = None, None
        if buckets is not None:
            near = 0.0
            nearest = _nearest_reference(buckets, geometry)
            if nearest is not None:
                near_m = nearest * distance_unit_m
                near = 1.0 if near_m <= reference_m else max(0.0, 1.0 - (near_m - reference_m) / (2.0 * reference_m))
        hint = type_hint(raised, metrics)
        perimeter = geometry_m.length() * (1.0 if to_metric is not None else unit_m)
        rows.append({
            "wkb": wkb, "type_hint": hint, "relief": "raised" if raised else "sunken",
            "peak_m": round(peak_m, 2), "mean_m": round(mean_m, 2), "area_m2": round(area_m2, 1),
            "perim_m": round(perimeter, 1), "circular": round(metrics["circularity"], 2),
            "elongation": round(metrics["elongation"], 2), "rectangul": round(metrics["rectangularity"], 2),
            "solidity": round(metrics["solidity"], 2), "hole_share": round(metrics["hole_share"], 2),
            "near_ref_m": None if near_m is None else round(near_m, 1),
            "score": candidate_score(hint, peak_m, metrics, near),
            "status": "candidate, not verified",
            "_centre": geometry.centroid().asPoint(),
        })
    rows.sort(key=lambda r: (-r["score"], -abs(r["peak_m"])))
    total = len(rows)
    rows = rows[:keep]
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    seconds = time.monotonic() - started

    if not rows:
        return {
            "layer_name": None, "candidates": 0, "shapes_found": found,
            "note": (f"No shape between {min_area:g} and {max_area:g} m2 stands {threshold:g} m out of the local "
                     f"relief. Lower threshold_m (0.15 for subtle banks on a 0.5 m DTM) or min_area_m2."),
            "caution": CAUTION,
        }
    name = str(args.get("name") or f"{facts['name']} relief anomalies").strip()
    added = _run_on_main_thread(_add_candidates, grid["wkt"], name,
                                [{k: v for k, v in row.items() if k != "_centre"} for row in rows], timeout=60)
    by_type = {}
    for row in rows:
        by_type[row["type_hint"]] = by_type.get(row["type_hint"], 0) + 1
    top = [{"rank": r["rank"], "type_hint": r["type_hint"], "peak_m": r["peak_m"], "area_m2": r["area_m2"],
            "near_ref_m": r["near_ref_m"], "score": r["score"],
            "x": round(r["_centre"].x(), 6 if grid["geographic"] else 1),
            "y": round(r["_centre"].y(), 6 if grid["geographic"] else 1)} for r in rows[:10]]
    result = {
        "layer_name": added["layer_name"],
        "layer_id": added["layer_id"],
        "candidates": len(rows),
        "candidates_before_cut": total,
        "shapes_found": found,
        "by_type_hint": by_type,
        "top": top,
        "lrm": lrm_source,
        "parameters": {"threshold_m": threshold, "kinds": sorted(kinds), "min_area_m2": min_area,
                       "max_area_m2": max_area, "reference_distance_m": reference_m if references else None},
        "references": ({"layers": references["layers"], "features": len(references["wkb"]),
                        "capped": references["capped"]} if references else None),
        "seconds": round(seconds, 1),
        "method": ("LRM thresholded at +/- threshold_m, regions under min_area_m2 sieved out (GDAL, 8-connected), "
                   "polygonised; type_hint from the outline smoothed by one cell: a hole inside a round outline is a "
                   "ring, an oriented box 3.5 times longer than wide is linear, a box filled to 85 % is "
                   "rectangular, a round compact shape is a mound (raised) or pit (sunken). score is 60 % peak "
                   "relief (full between 0.5 and 1.5 m, none past 6 m) and 40 % shape regularity (a linear "
                   "shape must also be straight), with 25 % of it given to distance from the reference layers "
                   "when those are passed."),
        "caution": CAUTION,
    }
    if total > len(rows):
        result["cut"] = f"The {len(rows)} best of {total} are in the layer; raise max_candidates for more."
    return result


__all__ = ["register_terrain_tools", "PRODUCTS", "local_relief", "horizon_scan", "scan_distances",
           "shape_metrics", "type_hint"]
