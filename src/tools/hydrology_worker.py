# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The three hydrology handlers, and map_drainage's chain, off the main thread."""




from __future__ import annotations

import heapq
import json
import math
import struct
import time

from ..core import limits, machine, net
from ..core.tool_registry import tool_error
from .data_tools import _run_on_main_thread
from .hydrology_layers import (
    _add_drainage_layers,
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



def _cell_cap() -> tuple[int, str]:
    """Cells one call may route here, now, and what set the number."""
    cap = int(limits.current("HYDROLOGY_MAX_CELLS"))
    try:
        free_mb = machine.sample().available_mb
    except Exception:  # noqa: BLE001 - an unreadable machine keeps the ceiling
        free_mb = None
    if free_mb:
        fits = int(free_mb * _MEMORY_SHARE * 1024 * 1024 / _BYTES_PER_CELL)
        if fits < cap:
            return max(fits, 1), f"{free_mb:,} MB free on this computer"
    return cap, "the cell cap"


def _stop_checkpoint():
    """A check the routing loops call every _CHECK_EVERY cells: raises InterruptedError once the call is stopped or past its budget."""


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
    except ImportError as exc:  # pragma: no cover - every QGIS ships both
        return tool_error(f"numpy or GDAL is missing from this QGIS: {exc}", "EXECUTION_FAILED",
                          "Run the GRASS recipe instead: r.watershed then r.water.outlet.")
    gdal.UseExceptions()

    facts = _run_on_main_thread(_dem_facts, str(args["dem"]))
    if "_error" in facts:
        return facts
    resolved = _run_on_main_thread(_resolve_outlet, args["outlet"], facts["crs_wkt"])
    if "_error" in resolved:
        return resolved
    lon, lat = resolved["lon"], resolved["lat"]
    ox, oy = resolved["x"], resolved["y"]
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



    cell_x_m, cell_y_m = _cell_metres(facts, dx, dy, lat)
    col_c = int(math.floor((ox - x0) / dx))
    row_c = int(math.floor((oy - y0) / dy))
    if not (0 <= col_c < facts["width"] and 0 <= row_c < facts["height"]):
        return tool_error(
            f"The outlet ({lon:.5f}, {lat:.5f}) lies outside {facts['name']}.", "INVALID_ARGS",
            "Check the DEM's extent with get_layer_info, or load the tile that covers the outlet.")
    half_cols = int(radius_km * 1000.0 / cell_x_m)
    half_rows = int(radius_km * 1000.0 / cell_y_m)
    c_min, c_max = max(0, col_c - half_cols), min(facts["width"], col_c + half_cols + 1)
    r_min, r_max = max(0, row_c - half_rows), min(facts["height"], row_c + half_rows + 1)
    width, height = c_max - c_min, r_max - r_min
    max_cells, bound_by = _cell_cap()
    if width * height > max_cells:
        fits_km = radius_km * math.sqrt(max_cells / float(width * height))
        return tool_error(
            f"The window is {width:,} by {height:,} cells at {cell_x_m:.1f} m; {bound_by} allows {max_cells:,}.",
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
    water = _open_water(dem, invalid, cell_x_m, cell_y_m, gdal, np)
    invalid |= water
    if invalid.all():
        return tool_error("The DEM holds no data around the outlet.", "EXECUTION_FAILED",
                          "The tile is empty there: load the neighbouring tile or another DEM.")
    original = dem.copy()

    filled = _priority_flood(dem, invalid, np, heapq, checkpoint)
    down = _d8_downstream(filled, invalid, cell_x_m, cell_y_m, np)
    accumulation = _accumulate(filled, invalid, down, np, checkpoint)

    r_s, c_s, moved_m = _snap(accumulation, invalid, row_c - r_min, col_c - c_min, snap_m, cell_x_m, cell_y_m, np)
    if r_s is None:
        return _outlet_off_land(invalid, row_c - r_min, col_c - c_min, snap_m, cell_x_m, cell_y_m,
                                bool(water.any()), np)

    mask = _upstream_of(down, r_s * width + c_s, width * height, np).reshape(height, width)
    mask &= ~invalid
    cells = int(mask.sum())
    if cells < 2:
        return tool_error(
            "Nothing drains to that cell: the outlet is a ridge or the DEM is flat there.", "EXECUTION_FAILED",
            "Place the outlet on the stream just downstream of the lake or dam, or widen snap_m so it finds it.")
    touches_edge = bool(mask[0, :].any() or mask[-1, :].any() or mask[:, 0].any() or mask[:, -1].any())



    z_y, z_x = (cell_y_m, cell_x_m) if facts["geographic"] else (abs(dy), abs(dx))
    gy, gx = np.gradient(np.where(invalid, np.nan, original), z_y, z_x)
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
        "outlet": _outlet_summary(resolved),
        "outlet_snapped": {"lon": round(s_lon, 6), "lat": round(s_lat, 6), "moved_m": round(moved_m)},
        "dem": facts["name"],
        "window_km": round(2 * radius_km, 1),
        "method": ("D8 on the DEM filled with Priority-Flood, outlet snapped to the strongest flow within "
                   f"{snap_m:g} m, area on the WGS84 ellipsoid, slope from the unfilled DEM"),
    }
    if water.any():
        water_km2 = float(water.sum()) * cell_x_m * cell_y_m / 1e6
        out["water_note"] = (f"{water_km2:.1f} km2 of the analysed area is a flat at exactly 0 m (the sea, or nodata "
                             "written as 0) and was left out as open water: flow ends at its shore.")
    if touches_edge:
        out["warning"] = _cut_warning(radius_km, max(cell_x_m, cell_y_m), max_cells)
    if facts["geographic"]:
        out["crs_note"] = _degrees_note(facts, lat)
    return out


def _degrees_note(facts: dict, lat: float) -> str:
    """Said whenever the DEM is in degrees, so nobody reprojects it by hand afterwards."""





    return (f"{facts['name']} is in degrees ({facts['crs_authid'] or 'geographic'}); each cell was measured in "
            f"metres at latitude {lat:.2f}, so it was used as it is and needs no reprojection.")


def _extract_stream_network(args: dict) -> dict:
    """Streams with their Strahler order, from the same filled D8 routing as the watershed."""




    try:
        return _extract_streams(args, _stop_checkpoint())
    except InterruptedError:
        return tool_error("extract_stream_network was stopped: no layer was added.", "CANCELLED", "Nothing to undo.")


def _extract_streams(args: dict, checkpoint) -> dict:
    try:
        import numpy as np
        from osgeo import gdal, ogr, osr
    except ImportError as exc:  # pragma: no cover - every QGIS ships both
        return tool_error(f"numpy or GDAL is missing from this QGIS: {exc}", "EXECUTION_FAILED",
                          "Run the GRASS recipe instead: r.stream.extract then r.stream.order.")
    gdal.UseExceptions()

    facts = _run_on_main_thread(_dem_facts, str(args["dem"]))
    if "_error" in facts:
        return facts
    dataset = gdal.Open(facts["source"], gdal.GA_ReadOnly)
    if dataset is None:
        return tool_error(f"GDAL could not open {facts['name']} ({facts['source'][:80]}).", "EXECUTION_FAILED",
                          "Load the DEM file itself with add_data: a WMS or XYZ layer holds no elevation values.")
    x0, dx, _, y0, _, dy = dataset.GetGeoTransform()
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
    to_wgs84 = osr.CoordinateTransformation(raster_srs, wgs84)

    resolved = mask = None
    warnings = []
    radius_km = float(args.get("radius_km") or _DEFAULT_RADIUS_KM)
    if args.get("outlet"):
        resolved = _run_on_main_thread(_resolve_outlet, args["outlet"], facts["crs_wkt"])
        if "_error" in resolved:
            return resolved
        lat_ref = resolved["lat"]
        cell_x_m, cell_y_m = _cell_metres(facts, dx, dy, lat_ref)
        col_c = int(math.floor((resolved["x"] - x0) / dx))
        row_c = int(math.floor((resolved["y"] - y0) / dy))
        if not (0 <= col_c < facts["width"] and 0 <= row_c < facts["height"]):
            return tool_error(f"The outlet ({resolved['lon']:.5f}, {resolved['lat']:.5f}) lies outside "
                              f"{facts['name']}.", "INVALID_ARGS",
                              "Check the DEM's extent with get_layer_info, or load the tile that covers the outlet.")
        half_cols = int(radius_km * 1000.0 / cell_x_m)
        half_rows = int(radius_km * 1000.0 / cell_y_m)
        c_min, c_max = max(0, col_c - half_cols), min(facts["width"], col_c + half_cols + 1)
        r_min, r_max = max(0, row_c - half_rows), min(facts["height"], row_c + half_rows + 1)
        area = "the watershed of the outlet"
    elif args.get("mask_layer"):
        mask = _run_on_main_thread(_mask_geometry, str(args["mask_layer"]), facts["crs_wkt"])
        if "_error" in mask:
            return mask
        xmin, ymin, xmax, ymax = mask["extent"]
        cols = sorted(((xmin - x0) / dx, (xmax - x0) / dx))
        rows = sorted(((ymin - y0) / dy, (ymax - y0) / dy))
        c_min, c_max = max(0, int(math.floor(cols[0])) - 2), min(facts["width"], int(math.ceil(cols[1])) + 2)
        r_min, r_max = max(0, int(math.floor(rows[0])) - 2), min(facts["height"], int(math.ceil(rows[1])) + 2)
        if c_min >= c_max or r_min >= r_max:
            return tool_error(f"{mask['name']} does not overlap {facts['name']}.", "INVALID_ARGS",
                              "Pass the DEM that covers the area, or check both extents with get_layer_info.")
        if cols[0] < 0 or rows[0] < 0 or cols[1] > facts["width"] or rows[1] > facts["height"]:
            warnings.append(f"{mask['name']} reaches beyond {facts['name']}: streams there are missing, and the "
                            "flow entering from outside the DEM is not counted.")
        _, lat_ref, _ = to_wgs84.TransformPoint((xmin + xmax) / 2.0, (ymin + ymax) / 2.0)
        cell_x_m, cell_y_m = _cell_metres(facts, dx, dy, lat_ref)
        area = f"the polygons of {mask['name']}" + (" (selected)" if mask["selected"] else "")
    else:
        c_min, c_max, r_min, r_max = 0, facts["width"], 0, facts["height"]
        _, lat_ref, _ = to_wgs84.TransformPoint(x0 + dx * facts["width"] / 2.0, y0 + dy * facts["height"] / 2.0)
        cell_x_m, cell_y_m = _cell_metres(facts, dx, dy, lat_ref)
        area = f"the whole of {facts['name']}"
    width, height = c_max - c_min, r_max - r_min
    max_cells, bound_by = _cell_cap()
    if width * height > max_cells:
        if resolved is not None:
            fits_km = radius_km * math.sqrt(max_cells / float(width * height))
            advice = (f"Pass radius_km {fits_km:.1f} or less, or resample the DEM coarser with gdal:warpreproject "
                      "when the basin is larger than that.")
        else:
            advice = ("Pass an outlet (the streams of its watershed) or a smaller mask_layer, or resample the DEM "
                      "coarser with gdal:warpreproject.")
        return tool_error(f"The area is {width:,} by {height:,} cells at {cell_x_m:.1f} m; {bound_by} allows "
                          f"{max_cells:,}.", "INVALID_ARGS", advice)
    if width < 3 or height < 3:
        return tool_error("The area holds fewer than three cells of the DEM.", "INVALID_ARGS",
                          "Increase radius_km, or pass a larger mask_layer.")

    band = dataset.GetRasterBand(1)
    dem = band.ReadAsArray(c_min, r_min, width, height).astype("float64")
    nodata = band.GetNoDataValue()
    invalid = ~np.isfinite(dem)
    if nodata is not None:
        invalid |= dem == nodata
    water = _open_water(dem, invalid, cell_x_m, cell_y_m, gdal, np)
    invalid |= water
    if invalid.all():
        return tool_error("The DEM holds no data over that area.", "EXECUTION_FAILED",
                          "Load the tile that covers it, or another DEM.")
    original = dem.copy()
    filled = _priority_flood(dem, invalid, np, heapq, checkpoint)
    down = _d8_downstream(filled, invalid, cell_x_m, cell_y_m, np)
    accumulation = _accumulate(filled, invalid, down, np, checkpoint)
    window_gt = (x0 + c_min * dx, dx, y0 + r_min * dy, dy)

    outlet_flat = None
    snapped = None
    if resolved is not None:
        snap_m = float(args.get("snap_m") if args.get("snap_m") is not None else _DEFAULT_SNAP_M)
        r_s, c_s, moved_m = _snap(accumulation, invalid, row_c - r_min, col_c - c_min, snap_m, cell_x_m, cell_y_m, np)
        if r_s is None:
            return _outlet_off_land(invalid, row_c - r_min, col_c - c_min, snap_m, cell_x_m, cell_y_m,
                                    bool(water.any()), np)
        outlet_flat = r_s * width + c_s
        basin = _upstream_of(down, outlet_flat, width * height, np).reshape(height, width) & ~invalid
        if int(basin.sum()) < 2:
            return tool_error("Nothing drains to that cell: the outlet is a ridge or the DEM is flat there.",
                              "EXECUTION_FAILED", "Place the outlet on a valley floor, or widen snap_m.")
        if basin[0, :].any() or basin[-1, :].any() or basin[:, 0].any() or basin[:, -1].any():
            warnings.append(_cut_warning(radius_km, max(cell_x_m, cell_y_m), max_cells))
        s_lon, s_lat, _ = to_wgs84.TransformPoint(window_gt[0] + (c_s + 0.5) * dx, window_gt[2] + (r_s + 0.5) * dy)
        snapped = {"lon": round(s_lon, 6), "lat": round(s_lat, 6), "moved_m": round(moved_m)}
    elif mask is not None:
        basin = _rasterise(mask["wkbs"], (window_gt[0], dx, 0.0, window_gt[2], 0.0, dy), width, height,
                           facts["crs_wkt"], gdal, ogr, osr, np) & ~invalid
        if not basin.any():
            return tool_error(f"{mask['name']} covers no cell centre of {facts['name']} holding data.",
                              "INVALID_ARGS", "Pass a larger polygon, or the DEM that covers it.")
    else:
        basin = ~invalid

    cell_km2 = cell_x_m * cell_y_m / 1e6
    analysed = int(basin.sum())
    given = args.get("threshold_cells") is not None or args.get("threshold_km2") is not None
    if args.get("threshold_cells") is not None:
        threshold = max(2, int(args["threshold_cells"]))
    elif args.get("threshold_km2") is not None:
        threshold = max(2, int(math.ceil(float(args["threshold_km2"]) / cell_km2)))
    else:
        threshold = max(_MIN_THRESHOLD_CELLS, analysed // _THRESHOLD_SHARE,
                        int(math.ceil(_MIN_THRESHOLD_M2 / 1e6 / cell_km2)))
    segment_cap = min(_MAX_STREAM_SEGMENTS, int(limits.current("MAX_FEATURES_CREATED")))
    raised_from = None
    while True:
        streams = (accumulation >= threshold) & basin
        if not streams.any():
            most = int(accumulation[basin].max())
            return tool_error(f"No cell collects {threshold:,} cells of flow here (the most is {most:,}).",
                              "INVALID_ARGS", f"Pass threshold_cells {max(2, most // 4):,} or less: the area is "
                                              "small for the threshold.")
        cells, order, segments = _strahler_network(filled, down, streams, np)
        if len(segments) <= segment_cap:
            break
        if given:
            wanted = int(math.ceil(threshold * len(segments) / float(segment_cap)))
            return tool_error(f"That threshold draws {len(segments):,} stream segments; one call adds up to "
                              f"{segment_cap:,}.", "INVALID_ARGS",
                              f"Pass threshold_cells {wanted:,} or more ({wanted * cell_km2:.3g} km2), "
                              "or a smaller area.")
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
                          f"Pass min_order {max_order} or less, or a smaller threshold_cells (now {threshold:,}) for a "
                          "denser network with higher orders.")
    rows = _segment_rows(cells, order, segments, accumulation, window_gt, width, cell_x_m, cell_y_m, cell_km2,
                         min_order, np, down=down)
    if not rows:


        drawable = sorted({order[seg[0]] for seg, junction in segments
                           if len(seg) + (1 if junction >= 0 else 0) >= 2})
        return tool_error(f"No stream of Strahler order {min_order} or more is longer than one cell here.",
                          "INVALID_ARGS",
                          (f"Pass min_order {drawable[-1]} or less" if drawable else "Pass a smaller threshold_cells")
                          + ", or move the outlet a little downstream of the confluence.")

    path = None
    if resolved is not None and args.get("longest_flow_path"):
        chain, length_m = _longest_flow_path(filled, basin, down, outlet_flat, cell_x_m, cell_y_m, np)
        if len(chain) >= 2:
            flat_ids = np.asarray(chain, dtype="int64")
            r, c = np.divmod(flat_ids, width)
            coords = np.column_stack((window_gt[0] + (c + 0.5) * dx, window_gt[2] + (r + 0.5) * dy)).astype("<f8")
            source, mouth = original.ravel()[chain[0]], original.ravel()[chain[-1]]
            path = {"wkb": struct.pack("<BII", 1, 2, len(chain)) + coords.tobytes(),
                    "length_km": round(length_m / 1000.0, 3), "drop_m": round(float(source - mouth), 1),
                    "name": f"Longest flow path {length_m / 1000.0:.1f} km"}

    name = str(args.get("name") or ("Streams (Strahler)" if min_order == 1 else f"Streams, Strahler {min_order}+"))
    rows.sort(key=lambda row: row[1])
    added = _run_on_main_thread(_add_stream_layers, facts["crs_wkt"], name, rows, path, timeout=120)




    out = {
        "layer_name": added["layer_name"],
        "layer_id": added["layer_id"],
        "segments": added["segments"],
        "max_order": max_order,
        "segments_by_order": {str(k): counts[k] for k in sorted(counts)},
        "kept_orders": sorted(int(k) for k in added["segments_by_order"]),
        "length_km": added["length_km"],
        "threshold_cells": threshold,
        "threshold_km2": round(threshold * cell_km2, 4),
        "area": area,
        "analysed_km2": round(analysed * cell_km2, 2),
        "cell_size_m": round((cell_x_m + cell_y_m) / 2.0, 2),
        "dem": facts["name"],
        "method": ("D8 on the DEM filled with Priority-Flood; a cell is a stream when threshold_cells drain through "
                   "it; Strahler order down the D8 tree, two equal orders meeting make the next one"),
    }
    if water.any():
        water_km2 = float(water.sum()) * cell_x_m * cell_y_m / 1e6
        out["water_note"] = (f"{water_km2:.1f} km2 of the analysed area is a flat at exactly 0 m (the sea, or nodata "
                             "written as 0) and was left out as open water: flow ends at its shore.")
    if raised_from:
        out["threshold_note"] = (f"The default threshold {raised_from:,} drew more than {segment_cap:,} segments, "
                                 f"so it was raised to {threshold:,}.")
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
        out["warning"] = " ".join(warnings)
    if facts["geographic"]:
        out["crs_note"] = _degrees_note(facts, lat_ref)
    return out


def _outlet_summary(resolved: dict) -> dict:
    """The outlet as it was read: degrees, where it came from, and a CRS that was guessed."""
    out = {"lon": round(resolved["lon"], 6), "lat": round(resolved["lat"], 6), "from": resolved["from"]}
    if resolved.get("crs_assumed"):
        out["crs_assumed"] = resolved["crs"]
    return out


def _cell_metres(facts: dict, dx: float, dy: float, lat: float) -> tuple[float, float]:
    """The size of one cell on the ground, in metres, at this latitude."""
    if facts["geographic"]:
        return (abs(dx) * 111_320.0 * max(0.05, math.cos(math.radians(lat))), abs(dy) * 110_540.0)
    unit = _metres_per_unit(facts["crs_wkt"])
    return abs(dx) * unit, abs(dy) * unit


def _metres_per_unit(crs_wkt: str) -> float:
    """Metres in one unit of a projected CRS: 1 for metres, 0.3048006 for a US survey foot."""



    unit = 1.0
    try:
        from osgeo import osr

        srs = osr.SpatialReference()
        srs.ImportFromWkt(crs_wkt)
        unit = float(srs.GetLinearUnits() or 1.0)
    except Exception:  # noqa: BLE001 - a CRS GDAL cannot read keeps its unit as metres
        return 1.0
    return unit if unit > 0 else 1.0


def _snap(accumulation, invalid, r_o: int, c_o: int, snap_m: float, cell_x_m: float, cell_y_m: float, np):
    """The most accumulating valid cell within snap_m of (r_o, c_o), and how far it moved."""
    height, width = accumulation.shape
    snap_cols = int(round(snap_m / cell_x_m))
    snap_rows = int(round(snap_m / cell_y_m))
    r_lo, r_hi = max(0, r_o - snap_rows), min(height, r_o + snap_rows + 1)
    c_lo, c_hi = max(0, c_o - snap_cols), min(width, c_o + snap_cols + 1)
    patch = accumulation[r_lo:r_hi, c_lo:c_hi].astype("float64")
    patch[invalid[r_lo:r_hi, c_lo:c_hi]] = -1.0
    best = int(np.argmax(patch))
    r_s, c_s = r_lo + best // patch.shape[1], c_lo + best % patch.shape[1]
    if invalid[r_s, c_s]:
        return None, None, None
    return r_s, c_s, math.hypot((c_s - c_o) * cell_x_m, (r_s - r_o) * cell_y_m)


def _outlet_off_land(invalid, r_o, c_o, snap_m, cell_x_m, cell_y_m, water_any, np) -> dict:
    """What to say when snap_m found no land: how far the nearest land cell is, and the snap_m that reaches it."""
    rows, cols = np.nonzero(~invalid)
    distance = float(np.hypot((rows - r_o) * cell_y_m, (cols - c_o) * cell_x_m).min())
    needed = int(math.ceil((distance + max(cell_x_m, cell_y_m)) / 10.0) * 10)
    where = "open water (a flat at 0 m, the sea)" if water_any else "nodata"
    message = (f"The outlet is on {where}, {distance:,.0f} m from the nearest land cell of the DEM, farther than "
               f"snap_m {snap_m:g}.")
    if needed <= 5000:
        suggestion = (f"Call again with snap_m {needed} and nothing else changed: the outlet moves to the strongest "
                      "flow on the shore within that distance.")
    else:
        suggestion = (f"Place the outlet on the coast or the river: the nearest land cell is {distance / 1000.0:.1f} "
                      "km away, beyond the 5 km snap_m cap. Or call map_drainage with no outlet: it picks the mouth.")
    return tool_error(message, "INVALID_ARGS", suggestion)


def _cut_warning(radius_km: float, cell_m: float, max_cells: int) -> str:
    """What to do next when the basin is cut by the window, as one call."""





    fits_km = min(_MAX_RADIUS_KM, math.sqrt(max_cells) * cell_m / 2000.0)
    window = 2 * radius_km
    if radius_km + 0.5 < fits_km:
        return (f"The watershed reaches the edge of the {window:g} km analysis window, so it is cut. "
                f"Call again with radius_km {fits_km:.0f}, the largest this DEM allows, and nothing else changed.")
    coarser = int(math.ceil(cell_m * 3 / 10.0)) * 10
    return (f"The watershed reaches the edge of the {window:g} km analysis window, so it is cut, and "
            f"radius_km {fits_km:.0f} is the largest this DEM allows (cap {_MAX_RADIUS_KM:.0f} km, "
            f"{max_cells:,} cells). A basin wider than {2 * fits_km:.0f} km is a river's: either resample "
            f"the DEM to {coarser} m with gdal:warpreproject and call again with radius_km up to "
            f"{_MAX_RADIUS_KM:.0f}, or pick an outlet on a smaller stream and say which. Do not ask the user.")




class _Stopped(Exception):
    """The chain stopped between two blocks of cells: Stop, or its own clock."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _map_drainage(args: dict) -> dict:
    """The DEM styled, the watershed and the streams of an area, the outlet picked when none is given."""


















    try:
        import numpy as np
        from osgeo import gdal, ogr, osr
    except ImportError as exc:  # pragma: no cover - every QGIS ships both
        return tool_error(f"numpy or GDAL is missing from this QGIS: {exc}", "EXECUTION_FAILED",
                          "Run delineate_watershed and extract_stream_network instead.")
    gdal.UseExceptions()
    started = time.monotonic()
    cancel = net.current_cancel_check()
    budget_s = float(limits.current("CALL_MAX_SECONDS_BACKGROUND")) * _CHAIN_TIME_SHARE
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
        return tool_error(message, "INVALID_ARGS", "Pass a smaller area, or a 90 m DEM (get_dem demtype COP90).")
    box = _bbox_around(centre[0], centre[1], math.sqrt(km2) / 2.0)
    return tool_error(message, "INVALID_ARGS",
                      f"Pass bbox {json.dumps(box)} (a quarter of that area, around its centre) instead of area, or a "
                      "90 m DEM for the whole area: get_dem demtype COP90, then its url as dem.")


def _drainage(args, progress, checkpoint, stop_reason, np, gdal, ogr, osr) -> dict:
    wgs84 = _srs(osr, epsg=4326)


    label, geometries = "", None
    if args.get("area"):
        area = _run_on_main_thread(_mask_geometry, str(args["area"]), wgs84.ExportToWkt())
        if "_error" in area:
            area["suggestion"] = str(area.get("suggestion") or "").replace("mask_layer", "area")
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
    facts = _run_on_main_thread(_dem_facts, dem_ref)
    if "_error" in facts:
        return facts
    dem_style = {"renderer": facts.get("renderer", "")}
    if facts.get("renderer") == "singlebandgray":
        from .elevation_style import sample_stretch

        stretch = sample_stretch(facts["source"])
        if stretch:
            dem_style = _run_on_main_thread(_style_dem, facts["layer_id"], stretch) or dem_style
    dataset = gdal.Open(facts["source"], gdal.GA_ReadOnly)
    if dataset is None:
        return tool_error(f"GDAL could not open {facts['name']} ({facts['source'][:80]}).", "EXECUTION_FAILED",
                          "Load the DEM file itself: get_dem, then its url as dem, or add_data.")
    x0, dx, _, y0, _, dy = dataset.GetGeoTransform()
    if dx == 0 or dy == 0:
        return tool_error("The DEM has no usable geotransform.", "EXECUTION_FAILED",
                          "Load a georeferenced DEM: get_dem, or find_datasets 'elevation'.")
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
                          "Get the DEM of the area: get_dem with the area's bbox in degrees, then its url as dem.")
    warnings = []
    if ax0 < fx0 or ax1 > fx1 or ay0 < fy0 or ay1 > fy1:
        warnings.append(f"{label} reaches beyond {facts['name']}: streams there are missing, and the flow "
                        "entering from outside the DEM is not counted.")

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
    cell_km2 = cell_x_m * cell_y_m / 1e6
    progress.update(cells=width * height, km2=width * height * cell_km2)
    max_cells, bound_by = _cell_cap()
    if width * height > max_cells:
        fits_km2 = max_cells * cell_km2
        side_km = 0.95 * math.sqrt(fits_km2) / (1.0 + 2.0 * _CHAIN_MARGIN_SHARE)
        return tool_error(
            f"{label or facts['name']} and its margin are {width:,} by {height:,} cells at {cell_x_m:.0f} m, about "
            f"{width * height * cell_km2:,.0f} km2; {bound_by} allows {max_cells:,} cells, about {fits_km2:,.0f} km2 "
            "at this resolution.", "INVALID_ARGS",
            f"Pass bbox {json.dumps(_bbox_around(lon_c, lat_c, side_km))} ({side_km:.0f} km across, around the "
            "area's centre) instead, or a 90 m DEM for the whole area: get_dem demtype COP90, then its url as dem.")
    if width < 3 or height < 3:
        return tool_error("The area holds fewer than three cells of the DEM.", "INVALID_ARGS", "Pass a larger area.")

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
                              "Load the DEM file itself (get_dem, then its url as dem) and call again.")
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
                          "Get the DEM of the area: get_dem with its bbox, then its url as dem.")
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
                              "Pass the DEM that covers the area, or a larger area.")
    else:
        inside = ~invalid

    gx0, gdx, _, gy0, _, gdy = geotransform
    to_degrees = osr.CoordinateTransformation(analysis_srs, wgs84)
    size = width * height

    def degrees_of(flat: int) -> tuple[float, float]:
        r, c = divmod(int(flat), width)
        lon, lat, _ = to_degrees.TransformPoint(gx0 + (c + 0.5) * gdx, gy0 + (r + 0.5) * gdy)
        return round(lon, 6), round(lat, 6)

    exits = []
    if args.get("outlet"):
        resolved = _run_on_main_thread(_resolve_outlet, args["outlet"], analysis_wkt)
        if "_error" in resolved:
            return resolved
        col = int(math.floor((resolved["x"] - gx0) / gdx))
        row = int(math.floor((resolved["y"] - gy0) / gdy))
        if not (0 <= col < width and 0 <= row < height):
            return tool_error(f"The outlet ({resolved['lon']:.5f}, {resolved['lat']:.5f}) lies outside the analysed "
                              f"window of {label or facts['name']}.", "INVALID_ARGS",
                              "Pass an outlet inside the area, or leave outlet out: the tool then picks where the "
                              "drainage leaves the area.")
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
                              "Pass an outlet: {lon, lat} of the river mouth or of the point to drain to.")
        outlet_flat, why = exits[0]
        lon, lat = degrees_of(outlet_flat)
        outlet = {"lon": lon, "lat": lat, "picked": "automatically", "why": _EXIT_WHY[why]}

    basin_flat = _upstream_of(down, outlet_flat, size, np) & ~invalid.ravel()
    cells = int(basin_flat.sum())
    if cells < 2:
        return tool_error("Nothing drains to that cell: the outlet is a ridge or the DEM is flat there.",
                          "EXECUTION_FAILED",
                          "Leave outlet out: the tool then picks where the drainage leaves the area.")
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
                          "Pass an outlet a little downstream; if it persists, report the DEM.")
    checkpoint()


    stream_area = (inside | basin) & ~invalid
    analysed = int(stream_area.sum())
    given = args.get("threshold_km2") is not None
    if given:
        threshold = max(2, int(math.ceil(float(args["threshold_km2"]) / cell_km2)))
    else:
        threshold = max(_MIN_THRESHOLD_CELLS, analysed // _THRESHOLD_SHARE,
                        int(math.ceil(_MIN_THRESHOLD_M2 / 1e6 / cell_km2)))
    segment_cap = min(_MAX_STREAM_SEGMENTS, int(limits.current("MAX_FEATURES_CREATED")))
    raised_from, rows, counts, lowered = None, [], {}, False
    while True:
        streams = (accumulation >= threshold) & stream_area
        if not streams.any():
            most = int(accumulation[stream_area].max())
            if given:
                return tool_error(f"No cell collects {threshold * cell_km2:.3g} km2 of flow here (the most is "
                                  f"{most * cell_km2:.3g} km2).", "INVALID_ARGS",
                                  f"Pass threshold_km2 {max(2, most // 4) * cell_km2:.3g} or less.")
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
                              f"Pass threshold_km2 {wanted * cell_km2:.3g} or more, or a smaller area.")
        raised_from = raised_from or threshold
        threshold *= 2
        checkpoint()
    rows.sort(key=lambda row: row[1])

    checkpoint()
    area_km2 = _run_on_main_thread(_ellipsoid_area_km2, wkb, analysis_wkt)
    prefix = str(args.get("name") or label or "").strip()
    watershed_name = f"{prefix} watershed {area_km2:.1f} km2" if prefix else f"Watershed {area_km2:.1f} km2"
    streams_name = f"{prefix} streams (Strahler)" if prefix else "Streams (Strahler)"
    checkpoint()
    added = _run_on_main_thread(_add_drainage_layers, analysis_wkt, streams_name, rows, wkb, watershed_name, {
        "area_km2": round(area_km2, 3), "mean_slope_deg": round(mean_slope, 2),
        "outlet_lon": lon, "outlet_lat": lat, "cells": cells,
    }, timeout=120)




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
        "method": ("D8 on the DEM filled with Priority-Flood; without an outlet, the exit of the area (a cell whose "
                   "flow ends at the sea, nodata or the DEM's edge, or crosses the area's boundary) collecting the "
                   "most flow; watershed area on the WGS84 ellipsoid; streams where threshold_km2 drains through, "
                   "ordered by Strahler"),
    }
    if label:
        out["watershed"]["inside_area_pct"] = round(100.0 * float((basin & inside).sum()) / cells, 1)
    if others:
        out["other_exits"] = others
    if water.any():
        out["water_note"] = (f"{float(water.sum()) * cell_km2:.1f} km2 of the analysed window is a flat at exactly 0 m "
                             "(the sea, or nodata written as 0) and was left out as open water: flow ends at its "
                             "shore.")
    if warp:
        out["crs_note"] = (f"{facts['name']} ({facts['crs_authid'] or 'not in metres'}) was reprojected in memory to "
                           f"{analysis_id} at {cell_x_m:g} m for the routing, and the layers are in {analysis_id}: "
                           "nothing needs reprojecting by hand.")
    elif facts["geographic"]:
        out["crs_note"] = _degrees_note(facts, lat_c)
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
        warnings.append(f"The watershed reaches {where} of {facts['name']}, so its upstream part is missing: get_dem "
                        f"with bbox {json.dumps(wider)}, then call again with its url as dem and nothing else changed. "
                        "Do not ask the user.")
    elif touched:
        out["watershed"]["cut"] = True
        warnings.append(f"The watershed reaches the {'/'.join(touched)} edge of the analysed window, so it is cut: "
                        f"call again with margin_km {max(2.0, 2.0 * margin_m / 1000.0):.0f} and nothing else "
                        "changed.")
    if warnings:
        out["warning"] = " ".join(warnings)
    return out
