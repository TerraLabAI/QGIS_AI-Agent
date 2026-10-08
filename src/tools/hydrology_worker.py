# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





from __future__ import annotations

import heapq
import json
import math
import os
import struct
import time

from ..core import limits, machine, net, tuning
from ..core.background import run_on_main_thread
from ..core.host_platform import remove_tree
from ..core.policy import create_managed_temp_dir
from ..core.tool_registry import coded_fact, tool_error
from .hydrology_layers import (
    _add_drainage_layers,
    _add_ramp_raster,
    _add_stream_layers,
    _add_watershed_layer,
    _dem_facts,
    _ellipsoid_area_km2,
    _mask_geometry,
    _resolve_outlet,
    _style_dem,
)
from .hydrology_terrain import (
    _BYTES_PER_CELL,
    _CHAIN_MARGIN_SHARE,
    _CHAIN_MIN_MARGIN_M,
    _CHAIN_TIME_SHARE,
    _DEFAULT_RADIUS_KM,
    _DEFAULT_SNAP_M,
    _EXIT_WHY,
    _MAX_RADIUS_KM,
    _MAX_STREAM_SEGMENTS,
    _MEMORY_SHARE,
    _MIN_THRESHOLD_CELLS,
    _MIN_THRESHOLD_M2,
    _OTHER_EXITS,
    _THRESHOLD_SHARE,
    _UTM_MAX_LAT,
    _accumulate,
    _authid,
    _bbox_around,
    _border_nodata,
    _box_geometry,
    _d8_downstream,
    _dilate,
    _envelope,
    _exit_cells,
    _longest_flow_path,
    _open_water,
    _parse_bbox,
    _polygonise,
    _priority_flood,
    _rasterise,
    _segment_rows,
    _sinks,
    _srs,
    _strahler_network,
    _upstream_of,
)





_MIN_ROUTED_SIDE = 200


def _cell_cap() -> tuple[int, str]:

    cap = int(limits.current("HYDROLOGY_MAX_CELLS"))
    try:
        free_mb = machine.sample().available_mb
    except Exception:  # noqa: BLE001
        free_mb = None
    if free_mb:
        fits = int(free_mb * _served("memory") * 1024 * 1024 / _BYTES_PER_CELL)
        if fits < cap:
            return max(fits, 1), f"{free_mb:,} MB free on this computer"
    return cap, "the cell cap"


def _fitted_window(src_width: int, src_height: int, max_cells: int) -> tuple[int, int] | None:




    if src_width * src_height <= max_cells:
        return src_width, src_height
    shrink = math.sqrt(src_width * src_height / float(max_cells))
    width, height = max(3, int(src_width / shrink)), max(3, int(src_height / shrink))
    while width * height > max_cells and width > 3 and height > 3:
        width, height = width - 1, height - 1
    if width * height > max_cells or min(width, height) < _MIN_ROUTED_SIDE:
        return None
    return width, height


def _stop_checkpoint():



    cancel = net.current_cancel_check()

    def checkpoint() -> None:
        if cancel is not None and cancel():
            raise InterruptedError("stopped")

    return checkpoint


def _delineate_watershed(args: dict) -> dict:
    try:
        return _delineate(args, _stop_checkpoint())
    except InterruptedError:
        return tool_error("delineate_watershed was stopped: no layer was added.", "CANCELLED", "Nothing to undo.")


def _delineate(args: dict, checkpoint) -> dict:
    try:
        import numpy as np
        from osgeo import gdal, ogr, osr
    except ImportError as exc:  # pragma: no cover
        return tool_error(f"numpy or GDAL is missing from this QGIS: {exc}", "EXECUTION_FAILED",
                          hint="hydrology_numpy_gdal_missing", variant="watershed")
    gdal.UseExceptions()

    facts = run_on_main_thread(_dem_facts, str(args["dem"]))
    if "_error" in facts:
        return facts
    resolved = run_on_main_thread(_resolve_outlet, args["outlet"], facts["crs_wkt"])
    if "_error" in resolved:
        return resolved
    lon, lat = resolved["lon"], resolved["lat"]
    ox, oy = resolved["x"], resolved["y"]
    radius_km = float(args.get("radius_km") or _DEFAULT_RADIUS_KM)
    if radius_km > _max_radius_km():
        return tool_error(f"radius_km {radius_km:g} is over the {_max_radius_km():g} km limit.", "INVALID_ARGS",
                          hint="hydrology_radius_over_limit", radius_km=radius_km, max_radius_km=_max_radius_km())
    snap_m = float(args.get("snap_m") if args.get("snap_m") is not None else _DEFAULT_SNAP_M)

    dataset = gdal.Open(facts["source"], gdal.GA_ReadOnly)
    if dataset is None:
        return tool_error(f"GDAL could not open {facts['name']} ({facts['source'][:80]}).", "EXECUTION_FAILED",
                          hint="hydrology_gdal_cannot_open", dem=facts["name"])
    transform = dataset.GetGeoTransform()
    x0, dx, _, y0, _, dy = transform
    if dx == 0 or dy == 0:
        return tool_error("The DEM has no usable geotransform.", "EXECUTION_FAILED",
                          hint="hydrology_dem_no_geotransform", dem=facts["name"])

    raster_srs = osr.SpatialReference()
    raster_srs.ImportFromWkt(facts["crs_wkt"])
    wgs84 = osr.SpatialReference()
    wgs84.ImportFromEPSG(4326)
    try:
        wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        raster_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    except AttributeError:  # pragma: no cover
        pass



    cell_x_m, cell_y_m = _cell_metres(facts, dx, dy, lat)
    col_c = int(math.floor((ox - x0) / dx))
    row_c = int(math.floor((oy - y0) / dy))
    if not (0 <= col_c < facts["width"] and 0 <= row_c < facts["height"]):
        return tool_error(
            f"The outlet ({lon:.5f}, {lat:.5f}) lies outside {facts['name']}.", "INVALID_ARGS",
            hint="hydrology_outlet_outside_dem", dem=facts["name"])
    half_cols = int(radius_km * 1000.0 / cell_x_m)
    half_rows = int(radius_km * 1000.0 / cell_y_m)
    c_min, c_max = max(0, col_c - half_cols), min(facts["width"], col_c + half_cols + 1)
    r_min, r_max = max(0, row_c - half_rows), min(facts["height"], row_c + half_rows + 1)
    src_width, src_height = c_max - c_min, r_max - r_min
    if src_width < 3 or src_height < 3:
        return tool_error("The window around the outlet holds fewer than three cells.", "INVALID_ARGS",
                          hint="hydrology_window_too_small", radius_km=radius_km)



    max_cells, bound_by = _cell_cap()
    fitted = _fitted_window(src_width, src_height, max_cells)
    if fitted is None:
        return tool_error(
            f"The window is {src_width:,} by {src_height:,} cells at {cell_x_m:.1f} m; "
            f"{bound_by} allows {max_cells:,}, "
            f"under {_MIN_ROUTED_SIDE} by {_MIN_ROUTED_SIDE} even at a coarser cell.", "EXECUTION_FAILED",
            hint="hydrology_cell_cap", max_cells=max_cells)
    width, height = fitted
    native_x_m, native_y_m = cell_x_m, cell_y_m
    dx, dy = dx * src_width / width, dy * src_height / height
    cell_x_m, cell_y_m = cell_x_m * src_width / width, cell_y_m * src_height / height
    resampled = (width, height) != (src_width, src_height)

    wx0, wy0 = x0 + c_min * transform[1], y0 + r_min * transform[5]
    r_o = min(height - 1, int(math.floor((oy - wy0) / dy)))
    c_o = min(width - 1, int(math.floor((ox - wx0) / dx)))

    band = dataset.GetRasterBand(1)
    if resampled:
        dem = band.ReadAsArray(c_min, r_min, src_width, src_height, buf_xsize=width, buf_ysize=height,
                               resample_alg=gdal.GRIORA_Average).astype("float64")
    else:
        dem = band.ReadAsArray(c_min, r_min, width, height).astype("float64")
    nodata = band.GetNoDataValue()
    invalid = ~np.isfinite(dem)
    if nodata is not None:
        invalid |= dem == nodata
    water = _open_water(dem, invalid, cell_x_m, cell_y_m, gdal, np)
    nodata_cells = invalid.copy()
    invalid |= water
    if invalid.all():
        return tool_error("The DEM holds no data around the outlet.", "EXECUTION_FAILED",
                          hint="hydrology_dem_empty_at_outlet", dem=facts["name"])
    original = dem.copy()

    filled = _priority_flood(dem, invalid, np, heapq, checkpoint)
    down = _d8_downstream(filled, invalid, cell_x_m, cell_y_m, np)
    accumulation = _accumulate(filled, invalid, down, np, checkpoint)

    r_s, c_s, moved_m = _snap(accumulation, invalid, r_o, c_o, snap_m, cell_x_m, cell_y_m, np)
    if r_s is None:
        return _outlet_off_land(invalid, r_o, c_o, snap_m, cell_x_m, cell_y_m, bool(water.any()), np)

    mask = _upstream_of(down, r_s * width + c_s, width * height, np).reshape(height, width)
    mask &= ~invalid
    cells = int(mask.sum())
    if cells < 2:
        return tool_error(
            "Nothing drains to that cell: the outlet is a ridge or the DEM is flat there.", "EXECUTION_FAILED",
            hint="hydrology_nothing_drains", variant="watershed", snap_m=snap_m)


    sides = ((mask[0, :], r_min == 0), (mask[-1, :], r_max == facts["height"]),
             (mask[:, 0], c_min == 0), (mask[:, -1], c_max == facts["width"]))



    regions = _border_nodata(nodata_cells, np.ones(dem.shape, dtype=bool),
                             (wx0, dx, 0.0, wy0, 0.0, dy), facts["crs_wkt"],
                             gdal, ogr, osr, np)
    beyond_dem = False
    if regions.any():
        ends = (regions > 0) & ~_sinks(regions, invalid, down, np)
        beyond_dem = bool((mask & _dilate(ends, np)).any())
    touches_edge = beyond_dem or any(edge.any() for edge, _dem_end in sides)
    only_dem_edge = touches_edge and all(dem_end for edge, dem_end in sides if edge.any())



    z_y, z_x = (cell_y_m, cell_x_m) if facts["geographic"] else (abs(dy), abs(dx))
    gy, gx = np.gradient(np.where(invalid, np.nan, original), z_y, z_x)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    mean_slope = float(np.nanmean(np.where(mask, slope, np.nan)))

    wkb = _polygonise(mask, (wx0, dx, 0.0, wy0, 0.0, dy), facts["crs_wkt"], gdal, ogr, osr)
    if wkb is None:
        return tool_error("The watershed mask could not be polygonised.", "EXECUTION_FAILED",
                          hint="hydrology_polygonise_failed", variant="watershed")




    area_km2 = run_on_main_thread(_ellipsoid_area_km2, wkb, facts["crs_wkt"])

    to_wgs84 = osr.CoordinateTransformation(raster_srs, wgs84)
    sx = wx0 + (c_s + 0.5) * dx
    sy = wy0 + (r_s + 0.5) * dy
    s_lon, s_lat, _ = to_wgs84.TransformPoint(sx, sy)

    name = str(args.get("name") or f"Watershed {area_km2:.1f} km2")
    added = run_on_main_thread(_add_watershed_layer, wkb, facts["crs_wkt"], name, {
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
        "outlet": _outlet_summary(resolved),
        "outlet_snapped": {"lon": round(s_lon, 6), "lat": round(s_lat, 6), "moved_m": round(moved_m)},
        "dem": facts["name"],
        "window_km": round(2 * radius_km, 1),
        "method": coded_fact(hint="hydrology_method", variant="watershed", snap_m=snap_m),
    }
    if water.any():
        water_km2 = float(water.sum()) * cell_x_m * cell_y_m / 1e6
        out["water_note"] = (f"{water_km2:.1f} km2 of the analysed area is a flat at exactly 0 m (the sea, or nodata "
                             "written as 0) and was left out as open water: flow ends at its shore.")
    if only_dem_edge:


        out["checks"] = _checks([_finding(
            f"The watershed reaches the edge of {facts['name']} itself, so it is cut where the DEM ends.",
            "hydrology_basin_cut_dem_itself", dem=facts["name"])])
    elif touches_edge and radius_km < _max_radius_km():
        wider = min(_max_radius_km(), math.ceil(radius_km * 2.0))
        out["checks"] = _checks([_finding(
            f"The watershed reaches the edge of the {2 * radius_km:g} km analysis window, so it is incomplete.",
            "hydrology_basin_cut_window_wider", window_km=2 * radius_km, radius_km_wider=wider)])
    elif touches_edge:
        out["checks"] = _checks([_cut_warning(radius_km, max(cell_x_m, cell_y_m), max_cells)])
    if resampled:
        out["resample_note"] = coded_fact(
            hint="hydrology_resample_note", variant="watershed", routed_m=round((cell_x_m + cell_y_m) / 2.0, 1),
            dem=facts["name"], native_m=float(f"{(native_x_m + native_y_m) / 2.0:.2g}"), src_width=src_width,
            src_height=src_height, width=width, height=height, bound_by=bound_by, max_cells=max_cells)
    if facts["geographic"]:
        out["crs_note"] = _degrees_note(facts, lat)
    return out


def _degrees_note(facts: dict, lat: float) -> dict:






    return coded_fact(hint="hydrology_degrees_note", dem=facts["name"], crs=facts["crs_authid"] or "geographic",
                      latitude=round(lat, 2))


def _extract_stream_network(args: dict) -> dict:





    try:
        return _extract_streams(args, _stop_checkpoint())
    except InterruptedError:
        return tool_error("extract_stream_network was stopped: no layer was added.", "CANCELLED", "Nothing to undo.")


def _extract_streams(args: dict, checkpoint) -> dict:
    try:
        import numpy as np
        from osgeo import gdal, ogr, osr
    except ImportError as exc:  # pragma: no cover
        return tool_error(f"numpy or GDAL is missing from this QGIS: {exc}", "EXECUTION_FAILED",
                          hint="hydrology_numpy_gdal_missing", variant="streams")
    gdal.UseExceptions()

    facts = run_on_main_thread(_dem_facts, str(args["dem"]))
    if "_error" in facts:
        return facts
    dataset = gdal.Open(facts["source"], gdal.GA_ReadOnly)
    if dataset is None:
        return tool_error(f"GDAL could not open {facts['name']} ({facts['source'][:80]}).", "EXECUTION_FAILED",
                          hint="hydrology_gdal_cannot_open", variant="streams", dem=facts["name"])
    x0, dx, _, y0, _, dy = dataset.GetGeoTransform()
    if dx == 0 or dy == 0:
        return tool_error("The DEM has no usable geotransform.", "EXECUTION_FAILED",
                          hint="hydrology_dem_no_geotransform", dem=facts["name"])
    raster_srs = osr.SpatialReference()
    raster_srs.ImportFromWkt(facts["crs_wkt"])
    wgs84 = osr.SpatialReference()
    wgs84.ImportFromEPSG(4326)
    try:
        wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        raster_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    except AttributeError:  # pragma: no cover
        pass
    to_wgs84 = osr.CoordinateTransformation(raster_srs, wgs84)

    resolved = mask = None
    warnings, coded = [], {}
    radius_km = float(args.get("radius_km") or _DEFAULT_RADIUS_KM)
    if radius_km > _max_radius_km():
        return tool_error(f"radius_km {radius_km:g} is over the {_max_radius_km():g} km limit.", "INVALID_ARGS",
                          hint="hydrology_radius_over_limit", radius_km=radius_km, max_radius_km=_max_radius_km())
    if args.get("outlet"):
        resolved = run_on_main_thread(_resolve_outlet, args["outlet"], facts["crs_wkt"])
        if "_error" in resolved:
            return resolved
        lat_ref = resolved["lat"]
        cell_x_m, cell_y_m = _cell_metres(facts, dx, dy, lat_ref)
        col_c = int(math.floor((resolved["x"] - x0) / dx))
        row_c = int(math.floor((resolved["y"] - y0) / dy))
        if not (0 <= col_c < facts["width"] and 0 <= row_c < facts["height"]):
            return tool_error(f"The outlet ({resolved['lon']:.5f}, {resolved['lat']:.5f}) lies outside "
                              f"{facts['name']}.", "INVALID_ARGS",
                              hint="hydrology_outlet_outside_dem", dem=facts["name"])
        half_cols = int(radius_km * 1000.0 / cell_x_m)
        half_rows = int(radius_km * 1000.0 / cell_y_m)
        c_min, c_max = max(0, col_c - half_cols), min(facts["width"], col_c + half_cols + 1)
        r_min, r_max = max(0, row_c - half_rows), min(facts["height"], row_c + half_rows + 1)
        area = "the watershed of the outlet"
    elif args.get("mask_layer"):
        mask = run_on_main_thread(_mask_geometry, str(args["mask_layer"]), facts["crs_wkt"])
        if "_error" in mask:
            return mask
        xmin, ymin, xmax, ymax = mask["extent"]
        cols = sorted(((xmin - x0) / dx, (xmax - x0) / dx))
        rows = sorted(((ymin - y0) / dy, (ymax - y0) / dy))
        c_min, c_max = max(0, int(math.floor(cols[0])) - 2), min(facts["width"], int(math.ceil(cols[1])) + 2)
        r_min, r_max = max(0, int(math.floor(rows[0])) - 2), min(facts["height"], int(math.ceil(rows[1])) + 2)
        if c_min >= c_max or r_min >= r_max:
            return tool_error(f"{mask['name']} does not overlap {facts['name']}.", "INVALID_ARGS",
                              hint="hydrology_mask_outside_dem", mask=mask["name"], dem=facts["name"])
        if cols[0] < 0 or rows[0] < 0 or cols[1] > facts["width"] or rows[1] > facts["height"]:
            _warn(warnings, coded, f"{mask['name']} reaches beyond {facts['name']}.", "hydrology_area_beyond_dem",
                  area=mask["name"], dem=facts["name"])
        _, lat_ref, _ = to_wgs84.TransformPoint((xmin + xmax) / 2.0, (ymin + ymax) / 2.0)
        cell_x_m, cell_y_m = _cell_metres(facts, dx, dy, lat_ref)
        area = f"the polygons of {mask['name']}" + (" (selected)" if mask["selected"] else "")
    else:
        c_min, c_max, r_min, r_max = 0, facts["width"], 0, facts["height"]
        _, lat_ref, _ = to_wgs84.TransformPoint(x0 + dx * facts["width"] / 2.0, y0 + dy * facts["height"] / 2.0)
        cell_x_m, cell_y_m = _cell_metres(facts, dx, dy, lat_ref)
        area = f"the whole of {facts['name']}"
    src_width, src_height = c_max - c_min, r_max - r_min
    if src_width < 3 or src_height < 3:
        return tool_error("The area holds fewer than three cells of the DEM.", "INVALID_ARGS",
                          hint="hydrology_area_too_small", variant="streams")



    max_cells, bound_by = _cell_cap()
    fitted = _fitted_window(src_width, src_height, max_cells)
    if fitted is None:
        return tool_error(f"The area is {src_width:,} by {src_height:,} cells at {cell_x_m:.1f} m; {bound_by} "
                          f"allows {max_cells:,}, under {_MIN_ROUTED_SIDE} by {_MIN_ROUTED_SIDE} even at a "
                          "coarser cell.", "EXECUTION_FAILED",
                          hint="hydrology_cell_cap", max_cells=max_cells)
    width, height = fitted
    resampled = (width, height) != (src_width, src_height)
    native_m = (cell_x_m + cell_y_m) / 2.0

    wx0, wy0 = x0 + c_min * dx, y0 + r_min * dy
    dx, dy = dx * src_width / width, dy * src_height / height
    cell_x_m, cell_y_m = cell_x_m * src_width / width, cell_y_m * src_height / height

    per_cell = src_width * src_height / float(width * height)

    band = dataset.GetRasterBand(1)
    if resampled:
        dem = band.ReadAsArray(c_min, r_min, src_width, src_height, buf_xsize=width, buf_ysize=height,
                               resample_alg=gdal.GRIORA_Average).astype("float64")
    else:
        dem = band.ReadAsArray(c_min, r_min, width, height).astype("float64")
    nodata = band.GetNoDataValue()
    invalid = ~np.isfinite(dem)
    if nodata is not None:
        invalid |= dem == nodata
    water = _open_water(dem, invalid, cell_x_m, cell_y_m, gdal, np)
    invalid |= water
    if invalid.all():
        return tool_error("The DEM holds no data over that area.", "EXECUTION_FAILED",
                          hint="hydrology_dem_no_data", variant="streams", dem=facts["name"])
    original = dem.copy()
    filled = _priority_flood(dem, invalid, np, heapq, checkpoint)
    down = _d8_downstream(filled, invalid, cell_x_m, cell_y_m, np)
    accumulation = _accumulate(filled, invalid, down, np, checkpoint)
    window_gt = (wx0, dx, wy0, dy)

    outlet_flat = None
    snapped = None
    if resolved is not None:
        snap_m = float(args.get("snap_m") if args.get("snap_m") is not None else _DEFAULT_SNAP_M)
        r_o = min(height - 1, int(math.floor((resolved["y"] - wy0) / dy)))
        c_o = min(width - 1, int(math.floor((resolved["x"] - wx0) / dx)))
        r_s, c_s, moved_m = _snap(accumulation, invalid, r_o, c_o, snap_m, cell_x_m, cell_y_m, np)
        if r_s is None:
            return _outlet_off_land(invalid, r_o, c_o, snap_m, cell_x_m, cell_y_m, bool(water.any()), np)
        outlet_flat = r_s * width + c_s
        basin = _upstream_of(down, outlet_flat, width * height, np).reshape(height, width) & ~invalid
        if int(basin.sum()) < 2:
            return tool_error("Nothing drains to that cell: the outlet is a ridge or the DEM is flat there.",
                              "EXECUTION_FAILED", hint="hydrology_nothing_drains", variant="streams",
                              snap_m=snap_m)
        if basin[0, :].any() or basin[-1, :].any() or basin[:, 0].any() or basin[:, -1].any():
            _warn(warnings, coded, *_cut_warning(radius_km, max(cell_x_m, cell_y_m), max_cells))
        s_lon, s_lat, _ = to_wgs84.TransformPoint(window_gt[0] + (c_s + 0.5) * dx, window_gt[2] + (r_s + 0.5) * dy)
        snapped = {"lon": round(s_lon, 6), "lat": round(s_lat, 6), "moved_m": round(moved_m)}
    elif mask is not None:
        basin = _rasterise(mask["wkbs"], (window_gt[0], dx, 0.0, window_gt[2], 0.0, dy), width, height,
                           facts["crs_wkt"], gdal, ogr, osr, np) & ~invalid
        if not basin.any():
            return tool_error(f"{mask['name']} covers no cell centre of {facts['name']} holding data.",
                              "INVALID_ARGS", hint="hydrology_mask_no_cells", mask=mask["name"],
                              dem=facts["name"])
    else:
        basin = ~invalid

    cell_km2 = cell_x_m * cell_y_m / 1e6
    analysed = int(basin.sum())
    given = args.get("threshold_cells") is not None or args.get("threshold_km2") is not None
    if args.get("threshold_cells") is not None:
        threshold = max(2, int(math.ceil(int(args["threshold_cells"]) / per_cell)))
    elif args.get("threshold_km2") is not None:
        threshold = max(2, int(math.ceil(float(args["threshold_km2"]) / cell_km2)))
    else:
        threshold = max(_MIN_THRESHOLD_CELLS, analysed // _THRESHOLD_SHARE,
                        int(math.ceil(_MIN_THRESHOLD_M2 / 1e6 / cell_km2)))
    segment_cap = min(_served("segments"), int(limits.current("MAX_FEATURES_CREATED")))
    raised_from = None
    while True:
        streams = (accumulation >= threshold) & basin
        if not streams.any():
            most = int(accumulation[basin].max())
            return tool_error(f"No cell collects {_dem_cells(threshold, per_cell):,} cells of flow here (the most is "
                              f"{_dem_cells(most, per_cell):,}).", "INVALID_ARGS",
                              hint="hydrology_threshold_cells_too_high",
                              suggested_threshold_cells=max(2, _dem_cells(most, per_cell) // 4))
        cells, order, segments = _strahler_network(filled, down, streams, np)
        if len(segments) <= segment_cap:
            break
        if given:
            wanted = int(math.ceil(threshold * len(segments) / float(segment_cap)))
            return tool_error(f"That threshold draws {len(segments):,} stream segments; one call adds up to "
                              f"{segment_cap:,}.", "INVALID_ARGS",
                              hint="hydrology_threshold_cells_too_low",
                              suggested_threshold_cells=_dem_cells(wanted, per_cell),
                              suggested_threshold_km2=float(f"{wanted * cell_km2:.3g}"))
        raised_from = raised_from or threshold
        threshold *= 2
        checkpoint()

    counts: dict[int, int] = {}
    for path, _junction in segments:
        counts[order[path[0]]] = counts.get(order[path[0]], 0) + 1
    max_order = max(counts)
    min_order = int(args.get("min_order") or 1)
    if min_order > max_order:
        return tool_error(f"The highest Strahler order here is {max_order}, under min_order {min_order}.",
                          "INVALID_ARGS",
                          hint="hydrology_min_order_too_high", max_order=max_order,
                          threshold_cells=_dem_cells(threshold, per_cell))
    rows = _segment_rows(cells, order, segments, accumulation, window_gt, width, cell_x_m, cell_y_m, cell_km2,
                         min_order, np, down=down)
    if not rows:


        drawable = sorted({order[seg[0]] for seg, junction in segments
                           if len(seg) + (1 if junction >= 0 else 0) >= 2})
        return tool_error(f"No stream of Strahler order {min_order} or more is longer than one cell here.",
                          "INVALID_ARGS", hint="hydrology_streams_one_cell",
                          **({"variant": "min_order", "max_drawable_order": drawable[-1]} if drawable else {}))

    path = None
    if resolved is not None and args.get("longest_flow_path"):
        path = _path_line(filled, basin, down, outlet_flat, cell_x_m, cell_y_m, original, 1.0,
                          (window_gt[0], dx, window_gt[2], dy), "Longest flow path", np)

    name = str(args.get("name") or ("Streams (Strahler)" if min_order == 1 else f"Streams, Strahler {min_order}+"))
    rows.sort(key=lambda row: row[1])
    added = run_on_main_thread(_add_stream_layers, facts["crs_wkt"], name, rows, path, timeout=120)




    out = {
        "layer_name": added["layer_name"],
        "layer_id": added["layer_id"],
        "segments": added["segments"],
        "max_order": max_order,
        "segments_by_order": {str(k): counts[k] for k in sorted(counts)},
        "kept_orders": sorted(int(k) for k in added["segments_by_order"]),
        "length_km": added["length_km"],
        "threshold_cells": _dem_cells(threshold, per_cell),
        "threshold_km2": round(threshold * cell_km2, 4),
        "area": area,
        "analysed_km2": round(analysed * cell_km2, 2),
        "cell_size_m": round((cell_x_m + cell_y_m) / 2.0, 2),
        "dem": facts["name"],
        "method": coded_fact(hint="hydrology_method", variant="streams"),
    }
    if water.any():
        water_km2 = float(water.sum()) * cell_x_m * cell_y_m / 1e6
        out["water_note"] = (f"{water_km2:.1f} km2 of the analysed area is a flat at exactly 0 m (the sea, or nodata "
                             "written as 0) and was left out as open water: flow ends at its shore.")
    if raised_from:
        out["threshold_note"] = (f"The default threshold {_dem_cells(raised_from, per_cell):,} drew more than "
                                 f"{segment_cap:,} segments, so it was raised to {_dem_cells(threshold, per_cell):,}.")
    if resampled:
        out["resample_note"] = coded_fact(
            hint="hydrology_resample_note", variant="streams", routed_m=round((cell_x_m + cell_y_m) / 2.0, 1),
            dem=facts["name"], native_m=float(f"{native_m:.2g}"), src_width=src_width, src_height=src_height,
            width=width, height=height, bound_by=bound_by, max_cells=max_cells)
    if resolved is not None:
        out["outlet"] = _outlet_summary(resolved)
        out["outlet_snapped"] = snapped
    if path and added.get("path_layer_id"):
        out["longest_flow_path"] = {"layer_name": added.get("path_layer_name"), "layer_id": added.get("path_layer_id"),
                                    "length_km": path["length_km"], "drop_m": path["drop_m"]}
    elif path:
        warnings.append("The longest flow path was a degenerate line (a raster-to-vector artifact) and was not added.")
    if added.get("dropped"):
        warnings.append(f"{added['dropped']} degenerate stream segment(s) (zero-length or self-touching, from the "
                        "raster-to-vector step) were left out.")
    if warnings:
        out["checks"] = _checks_of(warnings, coded)
    if facts["geographic"]:
        out["crs_note"] = _degrees_note(facts, lat_ref)
    return out


def _finding(plain: str, code: str, **facts) -> tuple:

    return plain, code, facts


def _warn(warnings: list, coded: dict, plain: str, code: str, facts: dict | None = None, **more) -> None:

    coded[len(warnings)] = {"code": code, **(facts or {}), **more}
    warnings.append(plain)


def _checks_of(warnings: list, coded: dict) -> dict:

    out: dict = {"warnings": warnings}
    if coded:
        out["findings"] = [{"at": at, **finding} for at, finding in coded.items()]
    return out


def _checks(findings: list) -> dict:
    warnings: list = []
    coded: dict = {}
    for plain, code, facts in findings:
        _warn(warnings, coded, plain, code, facts)
    return _checks_of(warnings, coded)


def _dem_cells(routed: int, per_cell: float) -> int:

    return int(round(routed * per_cell))


def _path_line(filled, basin, down, outlet_flat: int, cell_x_m: float, cell_y_m: float, original, z_unit: float,
               grid: tuple, prefix: str, np) -> dict | None:





    chain, length_m = _longest_flow_path(filled, basin, down, outlet_flat, cell_x_m, cell_y_m, np)
    if len(chain) < 2:
        return None
    x0, dx, y0, dy = grid
    r, c = np.divmod(np.asarray(chain, dtype="int64"), filled.shape[1])
    coords = np.column_stack((x0 + (c + 0.5) * dx, y0 + (r + 0.5) * dy)).astype("<f8")
    source, mouth = original.ravel()[chain[0]], original.ravel()[chain[-1]]
    return {"wkb": struct.pack("<BII", 1, 2, len(chain)) + coords.tobytes(),
            "length_km": round(length_m / 1000.0, 3), "drop_m": round(float(source - mouth) * z_unit, 1),
            "name": f"{prefix} {length_m / 1000.0:.1f} km"}


def _outlet_summary(resolved: dict) -> dict:

    out = {"lon": round(resolved["lon"], 6), "lat": round(resolved["lat"], 6), "from": resolved["from"]}
    if resolved.get("crs_assumed"):
        out["crs_assumed"] = resolved["crs"]
    return out


def _cell_metres(facts: dict, dx: float, dy: float, lat: float) -> tuple[float, float]:

    if facts["geographic"]:
        return (abs(dx) * 111_320.0 * max(0.05, math.cos(math.radians(lat))), abs(dy) * 110_540.0)
    unit = _metres_per_unit(facts["crs_wkt"])
    return abs(dx) * unit, abs(dy) * unit


def _metres_per_unit(crs_wkt: str) -> float:




    try:
        from osgeo import osr

        srs = osr.SpatialReference()
        srs.ImportFromWkt(crs_wkt)
        unit = float(srs.GetLinearUnits() or 1.0)
    except Exception:  # noqa: BLE001
        return 1.0
    return unit if unit > 0 else 1.0


def _snap(accumulation, invalid, r_o: int, c_o: int, snap_m: float, cell_x_m: float, cell_y_m: float, np):

    height, width = accumulation.shape
    snap_cols = int(round(snap_m / cell_x_m))
    snap_rows = int(round(snap_m / cell_y_m))
    r_lo, r_hi = max(0, r_o - snap_rows), min(height, r_o + snap_rows + 1)
    c_lo, c_hi = max(0, c_o - snap_cols), min(width, c_o + snap_cols + 1)
    patch = accumulation[r_lo:r_hi, c_lo:c_hi].astype("float64")
    patch[invalid[r_lo:r_hi, c_lo:c_hi]] = -1.0


    rows, cols = np.ogrid[r_lo - r_o:r_hi - r_o, c_lo - c_o:c_hi - c_o]
    patch[np.hypot(cols * cell_x_m, rows * cell_y_m) > max(snap_m, 0.0)] = -1.0
    best = int(np.argmax(patch))
    r_s, c_s = r_lo + best // patch.shape[1], c_lo + best % patch.shape[1]
    if invalid[r_s, c_s]:
        return None, None, None
    return r_s, c_s, math.hypot((c_s - c_o) * cell_x_m, (r_s - r_o) * cell_y_m)


def _outlet_off_land(invalid, r_o, c_o, snap_m, cell_x_m, cell_y_m, water_any, np) -> dict:

    rows, cols = np.nonzero(~invalid)
    distance = float(np.hypot((rows - r_o) * cell_y_m, (cols - c_o) * cell_x_m).min())
    needed = int(math.ceil((distance + max(cell_x_m, cell_y_m)) / 10.0) * 10)
    where = "open water (a flat at 0 m, the sea)" if water_any else "nodata"
    message = (f"The outlet is on {where}, {distance:,.0f} m from the nearest land cell of the DEM, farther than "
               f"snap_m {snap_m:g}.")
    if needed <= 5000:
        return tool_error(message, "INVALID_ARGS", hint="hydrology_outlet_off_land", snap_m_needed=needed)
    return tool_error(message, "INVALID_ARGS", hint="hydrology_outlet_off_land", variant="far",
                      distance_km=round(distance / 1000.0, 1))


def _max_radius_km() -> float:





    return tuning.ceiling("hydrology_max_radius_km", _MAX_RADIUS_KM, 10.0)


_SERVED = {"segments": ("hydrology_max_stream_segments", _MAX_STREAM_SEGMENTS, 2_000),
           "memory": ("hydrology_memory_share", _MEMORY_SHARE, 0.2),
           "chain": ("hydrology_chain_time_share", _CHAIN_TIME_SHARE, 0.5)}


def _served(name: str):

    return tuning.ceiling(*_SERVED[name])


def _cut_warning(radius_km: float, cell_m: float, max_cells: int) -> tuple:

    window = 2 * radius_km
    plain = f"The watershed reaches the edge of the {window:g} km analysis window, so it is incomplete."
    if radius_km >= _max_radius_km():
        return _finding(plain, "hydrology_cut_window_limit", window_km=window, max_radius_km=_max_radius_km())



    fits_km = min(_max_radius_km(), math.floor((math.sqrt(max_cells) - 1) * cell_m / 200.0) / 10.0)
    if radius_km + 0.1 < fits_km:
        return _finding(plain, "hydrology_cut_window_wider", window_km=window, fits_km=fits_km)
    coarser = int(math.ceil(max(cell_m * 1.5, 2000.0 * _max_radius_km() / math.sqrt(max_cells) * 1.1) / 10.0)) * 10
    return _finding(plain, "hydrology_cut_window_coarser", window_km=window, max_cells=max_cells,
                    coarser_m=coarser, max_radius_km=_max_radius_km())




class _Stopped(Exception):


    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _map_drainage(args: dict) -> dict:




















    try:
        import numpy as np
        from osgeo import gdal, ogr, osr
    except ImportError as exc:  # pragma: no cover
        return tool_error(f"numpy or GDAL is missing from this QGIS: {exc}", "EXECUTION_FAILED",
                          hint="hydrology_numpy_gdal_missing", variant="chain")
    gdal.UseExceptions()
    started = time.monotonic()
    cancel = net.current_cancel_check()
    budget_s = float(limits.current("CALL_MAX_SECONDS_BACKGROUND")) * _served("chain")
    progress: dict = {"started": started}

    def stop_reason() -> str:
        if cancel is not None and cancel():
            return "stopped"
        if time.monotonic() - started > budget_s:
            return "clock"
        return ""

    def checkpoint() -> None:
        reason = stop_reason()
        if reason:
            raise _Stopped(reason)

    try:
        return _drainage(args, progress, checkpoint, stop_reason, np, gdal, ogr, osr)
    except InterruptedError:
        return _stopped_answer(progress)
    except _Stopped as stopped:
        if stopped.reason == "stopped":
            return _stopped_answer(progress)
        return _clock_refusal(progress, time.monotonic() - started, budget_s)


def _coarsened_note(coarsened, cell_m: float, max_cells: int, dem_name: str) -> dict | str:

    if not coarsened:
        return ""
    cells, was_m = coarsened
    return coded_fact(hint="hydrology_coarsened_note", cells=cells, was_m=round(was_m), max_cells=max_cells,
                      routed_m=round(cell_m, 3), dem=dem_name)


def _stopped_answer(progress: dict) -> dict:
    loaded = " (the DEM it loaded stays)" if progress.get("dem_loaded") else ""
    return tool_error(f"map_drainage was stopped: no watershed or stream layer was added{loaded}.", "CANCELLED",
                      "Nothing to undo.")


def _clock_refusal(progress: dict, elapsed: float, budget: float) -> dict:
    cells, km2, centre = progress.get("cells"), progress.get("km2"), progress.get("centre")
    message = (f"map_drainage ran {elapsed:.0f} s, past the {budget:.0f} s it may take on this computer"
               + (f", on {cells:,} cells (about {km2:,.0f} km2)" if cells and km2 else "")
               + ", and stopped before adding any layer.")
    if not km2 or not centre:
        return tool_error(message, "INVALID_ARGS", hint="hydrology_clock_exceeded")
    box = _bbox_around(centre[0], centre[1], math.sqrt(km2) / 2.0)
    return tool_error(message, "INVALID_ARGS", hint="hydrology_clock_exceeded", variant="box", bbox=json.dumps(box))


def _drainage(args, progress, checkpoint, stop_reason, np, gdal, ogr, osr) -> dict:
    wgs84 = _srs(osr, epsg=4326)


    label, geometries = "", None
    if args.get("area"):
        area = run_on_main_thread(_mask_geometry, str(args["area"]), wgs84.ExportToWkt(), "area")
        if "_error" in area:
            return area
        label = area["name"]
        geometries = [ogr.CreateGeometryFromWkb(bytes(wkb)) for wkb in area["wkbs"]]
    elif args.get("bbox"):
        box = _parse_bbox(args["bbox"])
        if isinstance(box, dict):
            return box
        label, geometries = "Area", [_box_geometry(ogr, *box)]

    dem_ref = str(args.get("dem") or "").strip()
    if dem_ref.lower().startswith(("http://", "https://")):
        from .stac_tools import _add_cog_layer

        loaded = _add_cog_layer({"url": dem_ref, "name": f"{label} DEM" if label else "DEM"})
        if not isinstance(loaded, dict) or loaded.get("_error") or not loaded.get("layer_id"):
            return loaded if isinstance(loaded, dict) else tool_error("The DEM at that url could not be loaded.")
        progress["dem_loaded"] = True
        dem_ref = str(loaded["layer_id"])
    checkpoint()
    facts = run_on_main_thread(_dem_facts, dem_ref)
    if "_error" in facts:
        return facts
    dem_style = {"renderer": facts.get("renderer", "")}
    if facts.get("renderer") == "singlebandgray":
        from .elevation_style import sample_stretch

        stretch = sample_stretch(facts["source"])
        if stretch:
            dem_style = run_on_main_thread(_style_dem, facts["layer_id"], stretch) or dem_style
    dataset = gdal.Open(facts["source"], gdal.GA_ReadOnly)
    if dataset is None:
        return tool_error(f"GDAL could not open {facts['name']} ({facts['source'][:80]}).", "EXECUTION_FAILED",
                          hint="hydrology_gdal_cannot_open", variant="chain", dem=facts["name"])
    x0, dx, _, y0, _, dy = dataset.GetGeoTransform()
    if dx == 0 or dy == 0:
        return tool_error("The DEM has no usable geotransform.", "EXECUTION_FAILED",
                          hint="hydrology_dem_no_geotransform", variant="chain", dem=facts["name"])
    dem_srs = _srs(osr, wkt=facts["crs_wkt"])
    xs = sorted((x0, x0 + dx * facts["width"]))
    ys = sorted((y0, y0 + dy * facts["height"]))
    footprint = _box_geometry(ogr, xs[0], ys[0], xs[1], ys[1])
    footprint.Segmentize(max(xs[1] - xs[0], ys[1] - ys[0]) / 32.0)
    if geometries is None:
        whole = footprint.Clone()
        whole.Transform(osr.CoordinateTransformation(dem_srs, wgs84))
        geometries = [whole]
    lon0, lon1, lat0, lat1 = _envelope(geometries)
    lon_c, lat_c = (lon0 + lon1) / 2.0, (lat0 + lat1) / 2.0
    progress["centre"] = (lon_c, lat_c)


    metres_per_unit = 1.0 if facts["geographic"] else float(dem_srs.GetLinearUnits() or 1.0)
    if not facts["geographic"] and abs(metres_per_unit - 1.0) < 1e-6:
        analysis_srs, warp = dem_srs, False
    elif abs(lat_c) <= _UTM_MAX_LAT:
        zone = min(60, max(1, int(math.floor((lon_c + 180.0) / 6.0)) + 1))
        analysis_srs, warp = _srs(osr, epsg=(32600 if lat_c >= 0 else 32700) + zone), True
    else:
        analysis_srs, warp = dem_srs, False
    analysis_wkt = analysis_srs.ExportToWkt()
    analysis_id = _authid(analysis_srs) or facts["crs_authid"]
    if facts["geographic"]:
        cell_x_m, cell_y_m = _cell_metres(facts, dx, dy, lat_c)
    else:
        cell_x_m, cell_y_m = abs(dx) * metres_per_unit, abs(dy) * metres_per_unit

    projected = []
    to_analysis = osr.CoordinateTransformation(wgs84, analysis_srs)
    for geometry in geometries:
        clone = geometry.Clone()
        clone.Transform(to_analysis)
        projected.append(clone)
    ax0, ax1, ay0, ay1 = _envelope(projected)
    in_analysis = footprint.Clone()
    in_analysis.Transform(osr.CoordinateTransformation(dem_srs, analysis_srs))
    fx0, fx1, fy0, fy1 = in_analysis.GetEnvelope()
    if args.get("margin_km") is not None:
        margin_m = float(args["margin_km"]) * 1000.0
    else:
        span_m = max((ax1 - ax0) * (cell_x_m / abs(dx) if not warp and facts["geographic"] else 1.0),
                     (ay1 - ay0) * (cell_y_m / abs(dy) if not warp and facts["geographic"] else 1.0))
        margin_m = max(_CHAIN_MIN_MARGIN_M, _CHAIN_MARGIN_SHARE * span_m)
    mx = margin_m if warp or not facts["geographic"] else margin_m / cell_x_m * abs(dx)
    my = margin_m if warp or not facts["geographic"] else margin_m / cell_y_m * abs(dy)
    wx0, wx1 = max(fx0, ax0 - mx), min(fx1, ax1 + mx)
    wy0, wy1 = max(fy0, ay0 - my), min(fy1, ay1 + my)
    limited = {"west": ax0 - mx < fx0, "east": ax1 + mx > fx1, "south": ay0 - my < fy0, "north": ay1 + my > fy1}
    if wx0 >= wx1 or wy0 >= wy1:
        return tool_error(f"{label or 'The area'} does not overlap {facts['name']}.", "INVALID_ARGS",
                          hint="hydrology_area_outside_dem", area=label or "The area", dem=facts["name"])
    warnings, coded = [], {}




    if label and (ax0 < fx0 or ax1 > fx1 or ay0 < fy0 or ay1 > fy1):
        _warn(warnings, coded, f"{label} reaches beyond {facts['name']}.", "hydrology_area_beyond_dem",
              area=label, dem=facts["name"])

    if warp:
        res = max(0.1, round((cell_x_m + cell_y_m) / 2.0, 1))
        cell_x_m = cell_y_m = res
        wx0, wy0 = math.floor(wx0 / res) * res, math.floor(wy0 / res) * res
        width, height = int(math.ceil((wx1 - wx0) / res)), int(math.ceil((wy1 - wy0) / res))
    else:
        cols = sorted(((wx0 - x0) / dx, (wx1 - x0) / dx))
        rows = sorted(((wy0 - y0) / dy, (wy1 - y0) / dy))
        c_min, c_max = max(0, int(math.floor(cols[0]))), min(facts["width"], int(math.ceil(cols[1])))
        r_min, r_max = max(0, int(math.floor(rows[0]))), min(facts["height"], int(math.ceil(rows[1])))
        width, height = c_max - c_min, r_max - r_min
    max_cells, bound_by = _cell_cap()
    coarsened = None
    if width * height > max_cells and (warp or not facts["geographic"]):




        coarsened = (width * height, (cell_x_m + cell_y_m) / 2.0)
        res = float(math.ceil(math.sqrt((wx1 - wx0) * (wy1 - wy0) / (0.95 * max_cells))))
        warp, cell_x_m, cell_y_m = True, res, res
        wx0, wy0 = math.floor(wx0 / res) * res, math.floor(wy0 / res) * res
        width, height = int(math.ceil((wx1 - wx0) / res)), int(math.ceil((wy1 - wy0) / res))
    cell_km2 = cell_x_m * cell_y_m / 1e6
    progress.update(cells=width * height, km2=width * height * cell_km2)
    if width * height > max_cells:
        fits_km2 = max_cells * cell_km2
        side_km = 0.95 * math.sqrt(fits_km2) / (1.0 + 2.0 * _CHAIN_MARGIN_SHARE)


        coarser_m = int(math.ceil(max(cell_x_m, cell_y_m) * math.sqrt(width * height / max_cells) * 1.1 / 10.0)) * 10
        return tool_error(
            f"{label or facts['name']} and its margin are {width:,} by {height:,} cells at {cell_x_m:.0f} m, about "
            f"{width * height * cell_km2:,.0f} km2; {bound_by} allows {max_cells:,} cells, about {fits_km2:,.0f} km2 "
            "at this resolution.", "INVALID_ARGS", hint="hydrology_area_over_cap",
            bbox=json.dumps(_bbox_around(lon_c, lat_c, side_km)), side_km=round(side_km), coarser_m=coarser_m)
    if width < 3 or height < 3:
        return tool_error("The area holds fewer than three cells of the DEM.", "INVALID_ARGS",
                          hint="hydrology_area_too_small", variant="chain")

    checkpoint()
    band = dataset.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    if warp:
        try:
            warped = gdal.Warp("", dataset, format="MEM", dstSRS=analysis_wkt,
                               outputBounds=(wx0, wy0, wx0 + width * res, wy0 + height * res), xRes=res, yRes=res,
                               resampleAlg="bilinear", srcNodata=nodata, dstNodata=float("nan"),
                               outputType=gdal.GDT_Float64,
                               callback=lambda complete, message, data: 0 if stop_reason() else 1)
        except RuntimeError as exc:
            checkpoint()
            return tool_error(f"GDAL could not reproject {facts['name']} to {analysis_id}: {exc}", "EXECUTION_FAILED",
                              hint="hydrology_warp_failed", dem=facts["name"], crs=analysis_id)
        checkpoint()
        dem = warped.GetRasterBand(1).ReadAsArray().astype("float64")
        geotransform = warped.GetGeoTransform()
        height, width = dem.shape
        invalid = ~np.isfinite(dem)



        covered = _rasterise([bytes(in_analysis.ExportToWkb())], geotransform, width, height, analysis_wkt,
                             gdal, ogr, osr, np)
    else:
        dem = band.ReadAsArray(c_min, r_min, width, height).astype("float64")
        geotransform = (x0 + c_min * dx, dx, 0.0, y0 + r_min * dy, 0.0, dy)
        invalid = ~np.isfinite(dem)
        if nodata is not None:
            invalid |= dem == nodata
        covered = np.ones(dem.shape, dtype=bool)



    regions = _border_nodata(invalid, covered, geotransform, analysis_wkt, gdal, ogr, osr, np)
    covered &= regions == 0
    checkpoint()
    dem[invalid] = 0.0
    water = _open_water(dem, invalid, cell_x_m, cell_y_m, gdal, np)
    invalid |= water
    if invalid.all():
        return tool_error(f"{facts['name']} holds no data over {label or 'that area'}.", "EXECUTION_FAILED",
                          hint="hydrology_dem_no_data", dem=facts["name"], area=label or "that area")
    original = np.where(invalid, np.nan, dem)
    filled = _priority_flood(dem, invalid, np, heapq, checkpoint)
    checkpoint()
    down = _d8_downstream(filled, invalid, cell_x_m, cell_y_m, np)
    if regions.any():

        covered |= _sinks(regions, invalid, down, np)
    checkpoint()
    accumulation = _accumulate(filled, invalid, down, np, checkpoint)
    checkpoint()

    if label:
        inside = _rasterise([bytes(g.ExportToWkb()) for g in projected], geotransform, width, height, analysis_wkt,
                            gdal, ogr, osr, np) & ~invalid
        if not inside.any():
            return tool_error(f"{label} covers no cell of {facts['name']} holding data.", "INVALID_ARGS",
                              hint="hydrology_area_no_cells", area=label, dem=facts["name"])
    else:
        inside = ~invalid

    if args.get("twi") is True:


        checkpoint()
        sides = [side for side, cut in limited.items() if cut] if label else []
        where = {
            "label": label, "beyond": bool(warnings) and bool(label), "sides": sides, "warp": warp,
            "wider": _bbox_around(lon_c, lat_c, 2.0 * max(lon1 - lon0, lat1 - lat0) * 111.0) if sides else None,
            "degrees_note": _degrees_note(facts, lat_c) if facts["geographic"] and not warp else "",
            "coarsened": _coarsened_note(coarsened, cell_x_m, max_cells, facts["name"]),
        }
        grid = {"geotransform": geotransform, "wkt": analysis_wkt, "crs": analysis_id,
                "cell_x_m": cell_x_m, "cell_y_m": cell_y_m,
                "z_unit": 1.0 if facts["geographic"] else metres_per_unit}
        return _twi_answer(args, facts, where, grid, inside, invalid, original, accumulation, progress, dem_style,
                           checkpoint, np, gdal)

    gx0, gdx, _, gy0, _, gdy = geotransform
    to_degrees = osr.CoordinateTransformation(analysis_srs, wgs84)
    size = width * height

    def degrees_of(flat: int) -> tuple[float, float]:
        r, c = divmod(int(flat), width)
        lon, lat, _ = to_degrees.TransformPoint(gx0 + (c + 0.5) * gdx, gy0 + (r + 0.5) * gdy)
        return round(lon, 6), round(lat, 6)

    exits = []
    if args.get("outlet"):
        resolved = run_on_main_thread(_resolve_outlet, args["outlet"], analysis_wkt)
        if "_error" in resolved:
            return resolved
        col = int(math.floor((resolved["x"] - gx0) / gdx))
        row = int(math.floor((resolved["y"] - gy0) / gdy))
        if not (0 <= col < width and 0 <= row < height):
            return tool_error(f"The outlet ({resolved['lon']:.5f}, {resolved['lat']:.5f}) lies outside the analysed "
                              f"window of {label or facts['name']}.", "INVALID_ARGS",
                              hint="hydrology_outlet_outside_window", area=label or facts["name"])
        snap_m = float(args.get("snap_m") if args.get("snap_m") is not None else _DEFAULT_SNAP_M)
        r_s, c_s, moved_m = _snap(accumulation, invalid, row, col, snap_m, cell_x_m, cell_y_m, np)
        if r_s is None:
            return _outlet_off_land(invalid, row, col, snap_m, cell_x_m, cell_y_m, bool(water.any()), np)
        outlet_flat = r_s * width + c_s
        lon, lat = degrees_of(outlet_flat)
        outlet = {"lon": lon, "lat": lat, "picked": "given", "given": _outlet_summary(resolved),
                  "moved_m": round(moved_m)}
    else:
        exits = _exit_cells(inside, invalid, water, covered, down, accumulation, np)
        if not exits:
            return tool_error(f"No drainage leaves {label or facts['name']}.", "EXECUTION_FAILED",
                              hint="hydrology_no_exit", area=label or facts["name"])
        outlet_flat, why = exits[0]
        lon, lat = degrees_of(outlet_flat)
        outlet = {"lon": lon, "lat": lat, "picked": "automatically", "why": _EXIT_WHY[why]}

    basin_flat = _upstream_of(down, outlet_flat, size, np) & ~invalid.ravel()
    cells = int(basin_flat.sum())
    if cells < 2:
        return tool_error("Nothing drains to that cell: the outlet is a ridge or the DEM is flat there.",
                          "EXECUTION_FAILED", hint="hydrology_nothing_drains", variant="chain")
    outlet["upstream_km2"] = round(float(accumulation.ravel()[outlet_flat]) * cell_km2, 3)
    others = []
    basins = [basin_flat]
    for flat, why in exits[1:]:
        if len(others) >= _OTHER_EXITS:
            break
        if any(mask[flat] for mask in basins):
            continue
        basins.append(_upstream_of(down, flat, size, np))
        o_lon, o_lat = degrees_of(flat)
        others.append({"lon": o_lon, "lat": o_lat,
                       "upstream_km2": round(float(accumulation.ravel()[flat]) * cell_km2, 3),
                       "why": _EXIT_WHY[why]})
    checkpoint()
    basin = basin_flat.reshape(height, width)
    top, bottom = ("north", "south") if gdy < 0 else ("south", "north")
    touched = [side for side, edge in ((top, basin[0, :]), (bottom, basin[-1, :]), ("west", basin[:, 0]),
                                       ("east", basin[:, -1])) if edge.any()]
    beyond_dem = bool((basin & _dilate(~covered, np)).any())
    with np.errstate(invalid="ignore", divide="ignore"):

        z_unit = 1.0 if facts["geographic"] else metres_per_unit
        gy, gx = np.gradient(original, cell_y_m / z_unit, cell_x_m / z_unit)
        slope = np.degrees(np.arctan(np.hypot(gx, gy)))
        slopes = slope[basin & np.isfinite(slope)]
    mean_slope = float(slopes.mean()) if slopes.size else 0.0
    wkb = _polygonise(basin, geotransform, analysis_wkt, gdal, ogr, osr)
    if wkb is None:
        return tool_error("The watershed mask could not be polygonised.", "EXECUTION_FAILED",
                          hint="hydrology_polygonise_failed", variant="chain")
    checkpoint()






    stream_area = ((inside | basin) if label or not args.get("outlet") else basin) & ~invalid
    analysed = int(stream_area.sum())
    given = args.get("threshold_km2") is not None
    if given:
        threshold = max(2, int(math.ceil(float(args["threshold_km2"]) / cell_km2)))
    else:
        threshold = max(_MIN_THRESHOLD_CELLS, analysed // _THRESHOLD_SHARE,
                        int(math.ceil(_MIN_THRESHOLD_M2 / 1e6 / cell_km2)))
    segment_cap = min(_served("segments"), int(limits.current("MAX_FEATURES_CREATED")))
    raised_from, rows, counts, lowered = None, [], {}, False
    while True:
        streams = (accumulation >= threshold) & stream_area
        if not streams.any():
            most = int(accumulation[stream_area].max())
            if given:
                return tool_error(f"No cell collects {threshold * cell_km2:.3g} km2 of flow here (the most is "
                                  f"{most * cell_km2:.3g} km2).", "INVALID_ARGS",
                                  hint="hydrology_threshold_km2_too_high",
                                  suggested_threshold_km2=float(f"{max(2, most // 4) * cell_km2:.3g}"))
            if lowered or most < 8:
                break
            threshold, lowered = max(2, most // 4), True
            continue
        stream_cells, order, segments = _strahler_network(filled, down, streams, np)
        if len(segments) <= segment_cap:
            rows = _segment_rows(stream_cells, order, segments, accumulation, (gx0, gdx, gy0, gdy), width,
                                 cell_x_m, cell_y_m, cell_km2, 1, np, down=down)
            for path, _junction in segments:
                counts[order[path[0]]] = counts.get(order[path[0]], 0) + 1
            break
        if given:
            wanted = threshold * len(segments) / float(segment_cap)
            return tool_error(f"That threshold draws {len(segments):,} stream segments; one call adds up to "
                              f"{segment_cap:,}.", "INVALID_ARGS",
                              hint="hydrology_threshold_km2_too_low",
                              suggested_threshold_km2=float(f"{wanted * cell_km2:.3g}"))
        raised_from = raised_from or threshold
        threshold *= 2
        checkpoint()
    rows.sort(key=lambda row: row[1])

    checkpoint()
    area_km2 = run_on_main_thread(_ellipsoid_area_km2, wkb, analysis_wkt)
    prefix = str(args.get("name") or label or "").strip()
    watershed_name = f"{prefix} watershed {area_km2:.1f} km2" if prefix else f"Watershed {area_km2:.1f} km2"
    streams_name = f"{prefix} streams (Strahler)" if prefix else "Streams (Strahler)"



    path = None
    if args.get("longest_flow_path") is True:
        path = _path_line(filled, basin, down, outlet_flat, cell_x_m, cell_y_m, original, z_unit,
                          (gx0, gdx, gy0, gdy), f"{prefix} longest flow path" if prefix else "Longest flow path", np)
        checkpoint()
    raster = None
    if args.get("accumulation") is True:
        raster = _accumulation_raster(accumulation, stream_area, geotransform, analysis_wkt, cell_km2,
                                      threshold * cell_km2,
                                      f"{prefix} flow accumulation" if prefix else "Flow accumulation", np, gdal)
        if "_error" in raster:
            return raster
        checkpoint()
    added = run_on_main_thread(_add_drainage_layers, analysis_wkt, streams_name, rows, wkb, watershed_name, {
        "area_km2": round(area_km2, 3), "mean_slope_deg": round(mean_slope, 2),
        "outlet_lon": lon, "outlet_lat": lat, "cells": cells,
    }, path, raster, timeout=120)
    if not isinstance(added, dict) or "_error" in added:
        if raster:
            remove_tree(raster["folder"])
        return added if isinstance(added, dict) else tool_error("The drainage layers could not be added.")




    out = {
        "layer_name": added["watershed"]["layer_name"],
        "layer_id": added["watershed"]["layer_id"],
        "watershed": {"layer_name": added["watershed"]["layer_name"], "layer_id": added["watershed"]["layer_id"],
                      "area_km2": round(area_km2, 3), "cells": cells, "mean_slope_deg": round(mean_slope, 2)},
        "streams": ({"layer_name": added["streams"]["layer_name"], "layer_id": added["streams"]["layer_id"],
                     "segments": added["streams"]["segments"], "max_order": added["streams"]["max_order"],
                     "segments_by_order": added["streams"]["segments_by_order"],
                     "length_km": added["streams"]["length_km"],
                     "threshold_km2": round(threshold * cell_km2, 4)} if added.get("streams") else None),
        "outlet": outlet,
        "dem": {"layer_name": facts["name"], "layer_id": facts["layer_id"], **dem_style,
                **({"loaded_from_url": True} if progress.get("dem_loaded") else {})},
        "analysis": {"crs": analysis_id, "cell_size_m": round((cell_x_m + cell_y_m) / 2.0, 2), "cells": size,
                     "window_km": [round(width * cell_x_m / 1000.0, 1), round(height * cell_y_m / 1000.0, 1)]},
        "area": label or f"the whole of {facts['name']}",
        "seconds": round(time.monotonic() - progress.get("started", time.monotonic()), 1),
        "method": coded_fact(hint="hydrology_method", variant="chain"),
    }
    if label:
        out["watershed"]["inside_area_pct"] = round(100.0 * float((basin & inside).sum()) / cells, 1)
    drawn_path = added.get("path") or {}
    if drawn_path.get("path_layer_id"):
        out["longest_flow_path"] = {"layer_name": drawn_path["path_layer_name"],
                                    "layer_id": drawn_path["path_layer_id"],
                                    "length_km": path["length_km"], "drop_m": path["drop_m"]}
    elif args.get("longest_flow_path") is True:
        warnings.append("The longest flow path was a single cell or a degenerate line and was not added.")
    if added.get("accumulation"):
        out["accumulation"] = {"layer_name": added["accumulation"]["layer_name"],
                               "layer_id": added["accumulation"]["layer_id"],
                               "unit": "km2 drained through each cell, the cell itself included",
                               "max_km2": raster["max_km2"], "path": raster["path"],
                               "style": coded_fact(hint="hydrology_accumulation_style",
                                                   threshold_km2=float(f"{threshold * cell_km2:.3g}"))}
    if others:
        out["other_exits"] = others
    if water.any():
        out["water_note"] = (f"{float(water.sum()) * cell_km2:.1f} km2 of the analysed window is a flat at exactly 0 m "
                             "(the sea, or nodata written as 0) and was left out as open water: flow ends at its "
                             "shore.")
    if warp:
        out["crs_note"] = coded_fact(hint="hydrology_reprojected_note", variant="layers", dem=facts["name"],
                                     dem_crs=facts["crs_authid"] or "not in metres", analysis_crs=analysis_id,
                                     cell_m=cell_x_m)
    elif facts["geographic"]:
        out["crs_note"] = _degrees_note(facts, lat_c)
    if coarsened:
        out["resolution_note"] = _coarsened_note(coarsened, cell_x_m, max_cells, facts["name"])
    if raised_from:
        out["threshold_note"] = (f"The default threshold drew more than {segment_cap:,} segments, so it was raised to "
                                 f"{threshold * cell_km2:.3g} km2.")
    streams_dropped = (added.get("streams") or {}).get("dropped")
    if streams_dropped:
        warnings.append(f"{streams_dropped} degenerate stream segment(s) (zero-length or self-touching, from the "
                        "raster-to-vector step) were left out.")
    if beyond_dem or (touched and any(limited[side] for side in touched)):
        out["watershed"]["cut"] = True
        wider = _bbox_around(lon_c, lat_c, 2.0 * max(lon1 - lon0, lat1 - lat0) * 111.0)
        where = f"the {'/'.join(touched)} edge" if touched and not beyond_dem else "the edge"
        _warn(warnings, coded, f"The watershed reaches {where} of {facts['name']}, so its upstream part is missing.",
              "hydrology_basin_cut_dem_edge", edge=where, dem=facts["name"], bbox=json.dumps(wider))
    elif touched:
        out["watershed"]["cut"] = True
        _warn(warnings, coded, f"The watershed reaches the {'/'.join(touched)} edge of the analysed window, so it "
              "is cut.", "hydrology_basin_cut_window_edge", sides="/".join(touched),
              margin_km=round(max(2.0, 2.0 * margin_m / 1000.0)))
    if warnings:
        out["checks"] = _checks_of(warnings, coded)
    return out


def _accumulation_raster(accumulation, keep, geotransform, wkt: str, cell_km2: float, threshold_km2: float,
                         name: str, np, gdal) -> dict:


    rows, cols = np.flatnonzero(keep.any(axis=1)), np.flatnonzero(keep.any(axis=0))
    r0, r1, c0, c1 = int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1
    km2 = np.where(keep, accumulation.astype("float64") * cell_km2, _TWI_NODATA)[r0:r1, c0:c1]
    gx0, gdx, _, gy0, _, gdy = geotransform
    folder = create_managed_temp_dir("accumulation")
    path = os.path.join(folder, "accumulation.tif")
    try:
        dataset = gdal.GetDriverByName("GTiff").Create(path, c1 - c0, r1 - r0, 1, gdal.GDT_Float32, _TWI_TIFF)
        dataset.SetGeoTransform((gx0 + c0 * gdx, gdx, 0.0, gy0 + r0 * gdy, 0.0, gdy))
        dataset.SetProjection(wkt)
        band = dataset.GetRasterBand(1)
        band.SetNoDataValue(_TWI_NODATA)
        band.WriteArray(km2.astype("float32"))
        band = None
        dataset.FlushCache()
        dataset = None
    except RuntimeError as exc:
        remove_tree(folder)
        return tool_error(f"GDAL could not write the flow accumulation: {exc}", "EXECUTION_FAILED",
                          hint="hydrology_write_failed", what="flow accumulation")
    return {"path": path, "folder": folder, "name": name, "low": 0.0, "high": max(threshold_km2, 2.0 * cell_km2),
            "max_km2": round(float(accumulation[keep].max()) * cell_km2, 3)}













_TWI_MIN_SLOPE_RAD = 0.001
_TWI_NODATA = -9999.0
_TWI_TIFF = ["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=3"]


def _slope_rise(z, step_x: float, step_y: float, np):






    padded = np.pad(z, 1, constant_values=np.nan)
    centre = padded[1:-1, 1:-1]

    def mean_of(a, b):
        fa, fb = np.isfinite(a), np.isfinite(b)
        count = fa.astype("int8") + fb.astype("int8")
        total = np.where(fa, a, 0.0) + np.where(fb, b, 0.0)
        return np.where(count > 0, total / np.maximum(count, 1), 0.0)

    gx = mean_of((padded[1:-1, 2:] - centre) / step_x, (centre - padded[1:-1, :-2]) / step_x)
    gy = mean_of((padded[2:, 1:-1] - centre) / step_y, (centre - padded[:-2, 1:-1]) / step_y)
    return np.hypot(gx, gy)


def _wetness_index(original, accumulation, invalid, cell_x_m: float, cell_y_m: float, z_unit: float, np):






    rise = _slope_rise(original, cell_x_m / z_unit, cell_y_m / z_unit, np)
    tan_beta = np.maximum(rise, math.tan(_TWI_MIN_SLOPE_RAD))
    specific_area = accumulation.astype("float64") * math.sqrt(cell_x_m * cell_y_m)
    with np.errstate(divide="ignore", invalid="ignore"):
        twi = np.log(specific_area / tan_beta)
    twi[invalid | (accumulation < 1) | ~np.isfinite(twi)] = np.nan
    return twi


def _twi_answer(args, facts, where, grid, inside, invalid, original, accumulation, progress, dem_style, checkpoint,
                np, gdal) -> dict:

    label = where["label"]
    twi = _wetness_index(original, accumulation, invalid, grid["cell_x_m"], grid["cell_y_m"], grid["z_unit"], np)
    twi[~inside] = np.nan
    finite = np.isfinite(twi)
    if not finite.any():
        return tool_error(f"No cell of {label or facts['name']} holds elevation to compute the index on.",
                          "EXECUTION_FAILED", hint="hydrology_twi_no_elevation", area=label or facts["name"],
                          dem=facts["name"])
    rows, cols = np.flatnonzero(finite.any(axis=1)), np.flatnonzero(finite.any(axis=0))
    r0, r1, c0, c1 = int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1
    twi = twi[r0:r1, c0:c1]
    values = twi[np.isfinite(twi)]
    low, median, high = (float(v) for v in np.percentile(values, [2, 50, 98]))
    gx0, gdx, _, gy0, _, gdy = grid["geotransform"]
    checkpoint()
    folder = create_managed_temp_dir("twi")
    path = os.path.join(folder, "twi.tif")
    try:
        dataset = gdal.GetDriverByName("GTiff").Create(path, c1 - c0, r1 - r0, 1, gdal.GDT_Float32, _TWI_TIFF)
        dataset.SetGeoTransform((gx0 + c0 * gdx, gdx, 0.0, gy0 + r0 * gdy, 0.0, gdy))
        dataset.SetProjection(grid["wkt"])
        band = dataset.GetRasterBand(1)
        band.SetNoDataValue(_TWI_NODATA)
        band.WriteArray(np.where(np.isfinite(twi), twi, _TWI_NODATA).astype("float32"))
        band = None
        dataset.FlushCache()
        dataset = None
    except RuntimeError as exc:
        remove_tree(folder)
        return tool_error(f"GDAL could not write the wetness index: {exc}", "EXECUTION_FAILED",
                          hint="hydrology_write_failed", what="wetness index")
    checkpoint()
    prefix = str(args.get("name") or label or facts["name"]).strip() or "DEM"
    added = run_on_main_thread(_add_ramp_raster, path, f"{prefix} TWI", low, max(high, low + 1e-3),
                                "wetness index", timeout=60)
    if not isinstance(added, dict) or "_error" in added:
        remove_tree(folder)
        return added if isinstance(added, dict) else tool_error("The wetness index layer could not be added.")

    cell_m = round(math.sqrt(grid["cell_x_m"] * grid["cell_y_m"]), 2)
    out = {
        "layer_name": added["layer_name"],
        "layer_id": added["layer_id"],
        "twi": {"min": round(float(values.min()), 2), "p2": round(low, 2), "median": round(median, 2),
                "p98": round(high, 2), "max": round(float(values.max()), 2), "cells": int(values.size)},
        "style": coded_fact(hint="hydrology_twi_style", renderer=added["renderer"], low=round(low, 1),
                            high=round(high, 1)),
        "dem": {"layer_name": facts["name"], "layer_id": facts["layer_id"], **dem_style,
                **({"loaded_from_url": True} if progress.get("dem_loaded") else {})},
        "analysis": {"crs": grid["crs"], "cell_size_m": cell_m, "cells": int(accumulation.size)},
        "area": label or f"the whole of {facts['name']}",
        "path": path,
        "seconds": round(time.monotonic() - progress.get("started", time.monotonic()), 1),
        "method": coded_fact(hint="hydrology_twi_method", cell_m=cell_m),
        "read_it": coded_fact(hint="hydrology_twi_read_it"),
    }
    if where["warp"]:
        out["crs_note"] = coded_fact(hint="hydrology_reprojected_note", variant="index", dem=facts["name"],
                                     dem_crs=facts["crs_authid"] or "not in metres", analysis_crs=grid["crs"],
                                     cell_m=cell_m)
    elif where["degrees_note"]:
        out["crs_note"] = where["degrees_note"]
    if where["coarsened"]:
        out["resolution_note"] = where["coarsened"]
    warnings = []
    if where["beyond"]:
        warnings.append(f"{label} reaches beyond {facts['name']}: the index covers only the part the DEM holds.")
    if where["sides"]:

        out["edge_note"] = coded_fact(hint="hydrology_twi_edge", dem=facts["name"],
                                      sides="/".join(where["sides"]), bbox=json.dumps(where["wider"]))
    if warnings:
        out["checks"] = {"warnings": warnings}
    return out
