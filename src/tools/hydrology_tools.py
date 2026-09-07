# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The catchment upstream of a point, from a DEM, with nothing but numpy."""





















from __future__ import annotations

import heapq
import math

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsDistanceArea,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)

from ..core.qt_compat import field_type
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .data_tools import _run_on_main_thread
from .layer_lookup import _find_layer, _layer_not_found_error




_MAX_CELLS = 4_000_000


_MAX_RADIUS_KM = 80.0
_DEFAULT_RADIUS_KM = 15.0
_DEFAULT_SNAP_M = 300.0

_D8 = ((-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1))


def register_hydrology_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="delineate_watershed",
        input_schema={
            "type": "object",
            "properties": {
                "dem": {"type": "string"},
                "outlet": {
                    "type": "object",
                    "properties": {
                        "lon": {"type": "number", "minimum": -180, "maximum": 180},
                        "lat": {"type": "number", "minimum": -90, "maximum": 90},
                    },
                    "required": ["lon", "lat"],
                },
                "radius_km": {"type": "number", "minimum": 0.5, "maximum": _MAX_RADIUS_KM},
                "snap_m": {"type": "number", "minimum": 0, "maximum": 5000},
                "name": {"type": "string"},
            },
            "required": ["dem", "outlet"],
        },
        handler=_delineate_watershed,
        background=True,
    ))




def _dem_facts(dem_name: str) -> dict:
    layer = _find_layer(dem_name)
    if layer is None:
        return _layer_not_found_error(dem_name)
    if not isinstance(layer, QgsRasterLayer):
        return tool_error(f"{layer.name()} is not a raster layer.", "INVALID_ARGS",
                          "dem is the elevation raster: find_datasets 'elevation' lists the DEMs, add_data loads one.")
    crs = layer.crs()
    return {
        "name": layer.name(),
        "source": str(layer.source() or ""),
        "crs_wkt": crs.toWkt(),
        "crs_authid": crs.authid(),
        "geographic": bool(crs.isGeographic()),
        "width": int(layer.width()),
        "height": int(layer.height()),
    }


def _ellipsoid_area_km2(wkb: bytes, crs_wkt: str) -> float:
    geometry = QgsGeometry()
    geometry.fromWkb(wkb)
    measure = QgsDistanceArea()
    measure.setSourceCrs(QgsCoordinateReferenceSystem.fromWkt(crs_wkt), QgsProject.instance().transformContext())
    measure.setEllipsoid("EPSG:7030")
    return float(measure.measureArea(geometry)) / 1e6


def _add_watershed_layer(wkb: bytes, crs_wkt: str, name: str, attributes: dict) -> dict:
    crs = QgsCoordinateReferenceSystem.fromWkt(crs_wkt)
    layer = QgsVectorLayer(f"Polygon?crs={crs.toWkt()}", name, "memory")
    provider = layer.dataProvider()
    fields = [
        QgsField("area_km2", field_type("Double")),
        QgsField("mean_slope_deg", field_type("Double")),
        QgsField("outlet_lon", field_type("Double")),
        QgsField("outlet_lat", field_type("Double")),
        QgsField("cells", field_type("Int")),
    ]
    provider.addAttributes(fields)
    layer.updateFields()
    feature = QgsFeature(layer.fields())
    geometry = QgsGeometry()
    geometry.fromWkb(wkb)
    feature.setGeometry(geometry)
    for key, value in attributes.items():
        feature.setAttribute(key, value)
    provider.addFeatures([feature])
    layer.updateExtents()
    QgsProject.instance().addMapLayer(layer)
    return {"layer_id": layer.id(), "layer_name": layer.name()}




def _delineate_watershed(args: dict) -> dict:
    try:
        import numpy as np
        from osgeo import gdal, ogr, osr
    except ImportError as exc:  # pragma: no cover - every QGIS ships both
        return tool_error(f"numpy or GDAL is missing from this QGIS: {exc}", "EXECUTION_FAILED",
                          "Run the GRASS recipe instead: r.watershed then r.water.outlet.")
    gdal.UseExceptions()

    facts = _run_on_main_thread(_dem_facts, str(args["dem"]))
    if "_error" in facts:
        return facts
    outlet = args["outlet"]
    lon, lat = float(outlet["lon"]), float(outlet["lat"])
    radius_km = float(args.get("radius_km") or _DEFAULT_RADIUS_KM)
    snap_m = float(args.get("snap_m") if args.get("snap_m") is not None else _DEFAULT_SNAP_M)

    dataset = gdal.Open(facts["source"], gdal.GA_ReadOnly)
    if dataset is None:
        return tool_error(f"GDAL could not open {facts['name']} ({facts['source'][:80]}).", "EXECUTION_FAILED",
                          "A layer QGIS draws but GDAL cannot reopen is usually a provider other than gdal "
                          "(WMS, XYZ): load the DEM file itself with add_data.")
    transform = dataset.GetGeoTransform()
    x0, dx, _, y0, _, dy = transform
    if dx == 0 or dy == 0:
        return tool_error("The DEM has no usable geotransform.", "EXECUTION_FAILED",
                          "Load a georeferenced DEM: find_datasets 'elevation'.")


    raster_srs = osr.SpatialReference()
    raster_srs.ImportFromWkt(facts["crs_wkt"])
    wgs84 = osr.SpatialReference()
    wgs84.ImportFromEPSG(4326)
    try:
        wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        raster_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    except AttributeError:  # pragma: no cover - GDAL < 3
        pass
    to_raster = osr.CoordinateTransformation(wgs84, raster_srs)
    ox, oy, _ = to_raster.TransformPoint(lon, lat)



    if facts["geographic"]:
        cell_x_m = abs(dx) * 111_320.0 * max(0.05, math.cos(math.radians(lat)))
        cell_y_m = abs(dy) * 110_540.0
    else:
        cell_x_m, cell_y_m = abs(dx), abs(dy)
    col_c = int((ox - x0) / dx)
    row_c = int((oy - y0) / dy)
    if not (0 <= col_c < facts["width"] and 0 <= row_c < facts["height"]):
        return tool_error(
            f"The outlet ({lon:.5f}, {lat:.5f}) lies outside {facts['name']}.", "INVALID_ARGS",
            "Check the DEM's extent with get_layer_info, or load the tile that covers the outlet.")
    half_cols = int(radius_km * 1000.0 / cell_x_m)
    half_rows = int(radius_km * 1000.0 / cell_y_m)
    c_min, c_max = max(0, col_c - half_cols), min(facts["width"], col_c + half_cols + 1)
    r_min, r_max = max(0, row_c - half_rows), min(facts["height"], row_c + half_rows + 1)
    width, height = c_max - c_min, r_max - r_min
    if width * height > _MAX_CELLS:
        fits_km = radius_km * math.sqrt(_MAX_CELLS / float(width * height))
        return tool_error(
            f"The window is {width:,} by {height:,} cells at {cell_x_m:.1f} m; the cap is {_MAX_CELLS:,}.",
            "INVALID_ARGS",
            f"Pass radius_km {fits_km:.1f} or less, or run it on a coarser DEM (Copernicus 30 m) when the basin "
            f"is larger than that: at this resolution the whole basin does not fit.")
    if width < 3 or height < 3:
        return tool_error("The window around the outlet holds fewer than three cells.", "INVALID_ARGS",
                          "Increase radius_km.")

    band = dataset.GetRasterBand(1)
    dem = band.ReadAsArray(c_min, r_min, width, height).astype("float64")
    nodata = band.GetNoDataValue()
    invalid = ~np.isfinite(dem)
    if nodata is not None:
        invalid |= dem == nodata
    if invalid.all():
        return tool_error("The DEM holds no data around the outlet.", "EXECUTION_FAILED",
                          "The tile is empty there: load the neighbouring tile or another DEM.")
    original = dem.copy()

    filled = _priority_flood(dem, invalid, np, heapq)
    down = _d8_downstream(filled, invalid, cell_x_m, cell_y_m, np)
    accumulation = _accumulate(filled, invalid, down, np)


    r_o, c_o = row_c - r_min, col_c - c_min
    snap_cols = int(round(snap_m / cell_x_m))
    snap_rows = int(round(snap_m / cell_y_m))
    r_lo, r_hi = max(0, r_o - snap_rows), min(height, r_o + snap_rows + 1)
    c_lo, c_hi = max(0, c_o - snap_cols), min(width, c_o + snap_cols + 1)
    patch = accumulation[r_lo:r_hi, c_lo:c_hi].astype("float64")
    patch[invalid[r_lo:r_hi, c_lo:c_hi]] = -1.0
    best = int(np.argmax(patch))
    r_s, c_s = r_lo + best // patch.shape[1], c_lo + best % patch.shape[1]
    if invalid[r_s, c_s]:
        return tool_error("Every cell within snap_m of the outlet is nodata.", "EXECUTION_FAILED",
                          "Move the outlet onto the DEM or widen snap_m.")
    moved_m = math.hypot((c_s - c_o) * cell_x_m, (r_s - r_o) * cell_y_m)

    mask = _upstream_of(down, r_s * width + c_s, width * height, np).reshape(height, width)
    mask &= ~invalid
    cells = int(mask.sum())
    if cells < 2:
        return tool_error(
            "Nothing drains to that cell: the outlet is a ridge or the DEM is flat there.", "EXECUTION_FAILED",
            "Place the outlet on the stream just downstream of the lake or dam, or widen snap_m so it finds it.")
    touches_edge = bool(mask[0, :].any() or mask[-1, :].any() or mask[:, 0].any() or mask[:, -1].any())


    gy, gx = np.gradient(np.where(invalid, np.nan, original), cell_y_m, cell_x_m)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    mean_slope = float(np.nanmean(np.where(mask, slope, np.nan)))

    wkb = _polygonise(mask, (x0 + c_min * dx, dx, 0.0, y0 + r_min * dy, 0.0, dy), facts["crs_wkt"], gdal, ogr, osr)
    if wkb is None:
        return tool_error("The watershed mask could not be polygonised.", "EXECUTION_FAILED",
                          "Retry with a slightly different outlet; if it persists, report the DEM.")




    area_km2 = _run_on_main_thread(_ellipsoid_area_km2, wkb, facts["crs_wkt"])

    to_wgs84 = osr.CoordinateTransformation(raster_srs, wgs84)
    sx = x0 + (c_min + c_s + 0.5) * dx
    sy = y0 + (r_min + r_s + 0.5) * dy
    s_lon, s_lat, _ = to_wgs84.TransformPoint(sx, sy)

    name = str(args.get("name") or f"Watershed {area_km2:.1f} km2")
    added = _run_on_main_thread(_add_watershed_layer, wkb, facts["crs_wkt"], name, {
        "area_km2": round(area_km2, 3), "mean_slope_deg": round(mean_slope, 2),
        "outlet_lon": round(s_lon, 6), "outlet_lat": round(s_lat, 6), "cells": cells,
    })
    out = {
        "layer_name": added["layer_name"],
        "layer_id": added["layer_id"],
        "area_km2": round(area_km2, 3),
        "mean_slope_deg": round(mean_slope, 2),
        "cells": cells,
        "cell_size_m": round((cell_x_m + cell_y_m) / 2.0, 2),
        "outlet": {"lon": lon, "lat": lat},
        "outlet_snapped": {"lon": round(s_lon, 6), "lat": round(s_lat, 6), "moved_m": round(moved_m)},
        "dem": facts["name"],
        "window_km": round(2 * radius_km, 1),
        "method": ("D8 on the DEM filled with Priority-Flood, outlet snapped to the strongest flow within "
                   f"{snap_m:g} m, area on the WGS84 ellipsoid, slope from the unfilled DEM"),
    }
    if touches_edge:
        out["warning"] = _cut_warning(radius_km, max(cell_x_m, cell_y_m))
    return out


def _cut_warning(radius_km: float, cell_m: float) -> str:
    """What to do next when the basin is cut by the window, as one call."""







    fits_km = min(_MAX_RADIUS_KM, math.sqrt(_MAX_CELLS) * cell_m / 2000.0)
    window = 2 * radius_km
    if radius_km + 0.5 < fits_km:
        return (f"The watershed reaches the edge of the {window:g} km analysis window, so it is cut. "
                f"Call again with radius_km {fits_km:.0f}, the largest this DEM allows, and nothing else changed.")
    coarser = int(math.ceil(cell_m * 3 / 10.0)) * 10
    return (f"The watershed reaches the edge of the {window:g} km analysis window, so it is cut, and "
            f"radius_km {fits_km:.0f} is the largest this DEM allows (cap {_MAX_RADIUS_KM:.0f} km, "
            f"{_MAX_CELLS:,} cells). A basin wider than {2 * fits_km:.0f} km is a river's: either resample "
            f"the DEM to {coarser} m with gdal:warpreproject and call again with radius_km up to "
            f"{_MAX_RADIUS_KM:.0f}, or pick an outlet on a smaller stream and say which. Do not ask the user.")




def _priority_flood(dem, invalid, np, heapq):
    """Priority-Flood+epsilon (Barnes, Lehman, Mulla 2014): every cell ends strictly above the neighbour it drains to, so no pit and no flat."""





    height, width = dem.shape
    padded = np.pad(invalid, 1, constant_values=True)
    edge = np.zeros_like(invalid)
    edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True
    for dr, dc in _D8:
        edge |= padded[1 + dr:1 + dr + height, 1 + dc:1 + dc + width]
    edge &= ~invalid
    filled = dem.ravel().tolist()
    closed = bytearray(invalid.ravel().astype("uint8").tobytes())
    heap = [(filled[i], i) for i in np.flatnonzero(edge).tolist()]
    for _, i in heap:
        closed[i] = 1
    heapq.heapify(heap)
    offsets = [(dr, dc, dr * width + dc) for dr, dc in _D8]
    nextafter = math.nextafter if hasattr(math, "nextafter") else (lambda a, b: np.nextafter(a, b).item())
    heappop, heappush = heapq.heappop, heapq.heappush
    while heap:
        z, i = heappop(heap)
        r, c = divmod(i, width)
        for dr, dc, di in offsets:
            nr, nc = r + dr, c + dc
            if nr < 0 or nc < 0 or nr >= height or nc >= width:
                continue
            j = i + di
            if closed[j]:
                continue
            closed[j] = 1
            zn = filled[j]
            if zn <= z:
                zn = nextafter(z, math.inf)
                filled[j] = zn
            heappush(heap, (zn, j))
    return np.array(filled, dtype="float64").reshape(height, width)


def _d8_downstream(filled, invalid, cell_x_m, cell_y_m, np):
    """The flat index of the cell each cell drains to; itself when it is an outlet of the window (no lower neighbour) or nodata."""

    height, width = filled.shape
    padded = np.pad(np.where(invalid, -np.inf, filled), 1, constant_values=-np.inf)
    valid_padded = np.pad(~invalid, 1, constant_values=False)
    best_drop = np.zeros_like(filled)
    best_dir = np.full(filled.shape, -1, dtype="int8")
    for index, (dr, dc) in enumerate(_D8):
        neighbour = padded[1 + dr:1 + dr + height, 1 + dc:1 + dc + width]
        ok = valid_padded[1 + dr:1 + dr + height, 1 + dc:1 + dc + width]
        distance = math.hypot(dr * cell_y_m, dc * cell_x_m)
        drop = np.where(ok, (filled - neighbour) / distance, -np.inf)
        better = drop > best_drop
        best_drop = np.where(better, drop, best_drop)
        best_dir = np.where(better, index, best_dir)
    rows, cols = np.indices(filled.shape)
    drs = np.array([d[0] for d in _D8] + [0], dtype="int64")
    dcs = np.array([d[1] for d in _D8] + [0], dtype="int64")
    target_r = rows + drs[best_dir]
    target_c = cols + dcs[best_dir]
    down = (target_r * width + target_c).astype("int64")
    down[invalid] = (rows * width + cols)[invalid]
    return down.ravel()


def _accumulate(filled, invalid, down, np):
    """Upstream cell counts, summed from the highest cell down."""
    flat = np.where(invalid, -np.inf, filled).ravel()
    order = np.argsort(-flat, kind="stable")
    acc = np.ones(flat.shape[0], dtype="int64")
    acc[invalid.ravel()] = 0
    down_list = down.tolist()
    acc_list = acc.tolist()
    for i in order.tolist():
        j = down_list[i]
        if j != i:
            acc_list[j] += acc_list[i]
    return np.array(acc_list, dtype="int64").reshape(filled.shape)


def _upstream_of(down, outlet, size, np):
    """Every cell whose D8 path reaches ``outlet``, by pointer jumping: the reached set doubles its depth each pass, so a path of a million cells."""


    reached = np.zeros(size, dtype=bool)
    reached[outlet] = True
    jump = down.copy()
    jump[outlet] = outlet
    for _ in range(64):
        before = int(reached.sum())
        reached |= reached[jump]
        jump = jump[jump]
        if int(reached.sum()) == before:
            break
    return reached


def _polygonise(mask, geotransform, crs_wkt, gdal, ogr, osr):
    """The mask as one (multi)polygon WKB in the raster's CRS."""
    height, width = mask.shape
    driver = gdal.GetDriverByName("MEM")
    raster = driver.Create("", width, height, 1, gdal.GDT_Byte)
    raster.SetGeoTransform(geotransform)
    raster.SetProjection(crs_wkt)
    band = raster.GetRasterBand(1)
    band.WriteArray(mask.astype("uint8"))
    band.SetNoDataValue(0)
    srs = osr.SpatialReference()
    srs.ImportFromWkt(crs_wkt)
    memory = ogr.GetDriverByName("Memory").CreateDataSource("watershed")
    layer = memory.CreateLayer("watershed", srs=srs, geom_type=ogr.wkbPolygon)
    layer.CreateField(ogr.FieldDefn("value", ogr.OFTInteger))
    gdal.Polygonize(band, band, layer, 0, [], callback=None)
    parts = []
    for feature in layer:
        geometry = feature.GetGeometryRef()
        if geometry is not None:
            parts.append(QgsGeometry.fromWkt(geometry.ExportToWkt()))
    if not parts:
        return None
    merged = QgsGeometry.unaryUnion(parts) if len(parts) > 1 else parts[0]
    merged = merged.makeValid()
    return bytes(merged.asWkb())


__all__ = ["register_hydrology_tools"]
