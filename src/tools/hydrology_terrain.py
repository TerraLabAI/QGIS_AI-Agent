# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The routing algorithms, and the numbers every hydrology module reads."""







from __future__ import annotations

import math
import struct

from qgis.core import QgsGeometry

from ..core.tool_registry import tool_error





_MAX_RADIUS_KM = 80.0
_DEFAULT_RADIUS_KM = 15.0
_DEFAULT_SNAP_M = 300.0





_BYTES_PER_CELL = 180

_MEMORY_SHARE = 0.5

_D8 = ((-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1))



_WATER_LEVEL = 0.0
_MIN_WATER_M2 = 1_000_000.0
_MIN_WATER_CELLS = 100







_THRESHOLD_SHARE = 1000
_MIN_THRESHOLD_CELLS = 100
_MIN_THRESHOLD_M2 = 50_000.0



_MAX_STREAM_SEGMENTS = 20_000
_MAX_ORDER = 12


_OUTLET_FORMS = ('Pass outlet as {"lon":77.6,"lat":8.2}, {"x":..,"y":..,"crs":"EPSG:32643"}, '
                 '{"layer_name":"Outlet"} (+"feature_id") or {"wkt":"POINT(77.6 8.2)"}.')




_CHAIN_MARGIN_SHARE = 0.1
_CHAIN_MIN_MARGIN_M = 1000.0
_CHAIN_MAX_MARGIN_KM = 50.0



_CHAIN_TIME_SHARE = 0.8

_CHECK_EVERY = 65536


_OTHER_EXITS = 3
_EXIT_CANDIDATES = 256


_UTM_MAX_LAT = 84.0




_SINK_SHARE = 0.1
_EXIT_WHY = {
    "coast": "where the drainage meets the sea (a flat at 0 m)",
    "boundary": "where the drainage leaves the area across its boundary",
    "nodata": "where the drainage reaches the DEM's nodata (a lake or a void)",
    "edge": "where the drainage leaves the DEM at its edge",
}


def _exit_cells(inside, invalid, water, covered, down, accumulation, np) -> list:
    """Where the area's drainage ends or leaves it, the most accumulating first: [(flat index, why)]."""







    height, width = inside.shape
    flat_inside = inside.ravel()
    ends = down == np.arange(flat_inside.size)
    candidates = np.flatnonzero(flat_inside & (ends | ~flat_inside[down]))
    if candidates.size == 0:
        return []
    collected = accumulation.ravel()[candidates]
    if candidates.size > _EXIT_CANDIDATES:
        keep = np.argpartition(-collected, _EXIT_CANDIDATES - 1)[:_EXIT_CANDIDATES]
        candidates, collected = candidates[keep], collected[keep]
    near_water = _dilate(water, np)
    near_outside = _dilate(~covered, np)
    near_nodata = _dilate(invalid & ~water & covered, np)
    out = []
    for flat in candidates[np.argsort(-collected, kind="stable")].tolist():
        r, c = divmod(flat, width)
        if not ends[flat]:
            why = "boundary"
        elif near_water[r, c]:
            why = "coast"
        elif near_outside[r, c]:
            why = "edge"
        elif near_nodata[r, c]:
            why = "nodata"
        else:
            why = "edge"
        out.append((flat, why))
    return out


def _border_nodata(invalid, covered, geotransform, crs_wkt, gdal, ogr, osr, np):
    """The nodata regions inside the DEM's footprint (8-connected) that reach the border of the window or the footprint's end, numbered from 1."""





    height, width = invalid.shape
    regions = np.zeros(invalid.shape, dtype="int32")
    holes = invalid & covered
    reach = np.zeros(invalid.shape, dtype=bool)
    reach[0, :] = reach[-1, :] = reach[:, 0] = reach[:, -1] = True
    if not covered.all():
        reach |= _dilate(~covered, np)
    if not (holes & reach).any():
        return regions
    driver = gdal.GetDriverByName("MEM")
    raster = driver.Create("", width, height, 1, gdal.GDT_Byte)
    raster.SetGeoTransform(geotransform)
    raster.SetProjection(crs_wkt)
    band = raster.GetRasterBand(1)
    band.WriteArray(holes.astype("uint8"))
    band.SetNoDataValue(0)
    srs = osr.SpatialReference()
    srs.ImportFromWkt(crs_wkt)
    memory = ogr.GetDriverByName("Memory").CreateDataSource("nodata")
    layer = memory.CreateLayer("nodata", srs=srs, geom_type=ogr.wkbPolygon)
    layer.CreateField(ogr.FieldDefn("region", ogr.OFTInteger))
    gdal.Polygonize(band, band, layer, 0, ["8CONNECTED=8"], callback=None)
    layer.ResetReading()
    feature, number = layer.GetNextFeature(), 0
    while feature is not None:
        number += 1
        feature.SetField(0, number)
        layer.SetFeature(feature)
        feature = layer.GetNextFeature()
    target = driver.Create("", width, height, 1, gdal.GDT_Int32)
    target.SetGeoTransform(geotransform)
    target.SetProjection(crs_wkt)
    gdal.RasterizeLayer(target, [1], layer, options=["ATTRIBUTE=region"])
    regions = np.where(holes, target.GetRasterBand(1).ReadAsArray(), 0).astype("int32")
    reached = np.unique(regions[reach & holes])
    return np.where(np.isin(regions, reached[reached > 0]), regions, 0).astype("int32")


def _sinks(regions, invalid, down, np):
    """The cells of the _border_nodata regions that are a sea or a lake written as nodata."""





    height, width = regions.shape
    padded = np.pad(regions, 1)
    beside = np.zeros_like(regions)
    for dr, dc in _D8:
        beside = np.maximum(beside, padded[1 + dr:1 + dr + height, 1 + dc:1 + dc + width])
    flat_beside = beside.ravel()
    rim = np.flatnonzero(((beside > 0) & ~invalid).ravel())
    if rim.size == 0:
        return np.zeros(regions.shape, dtype=bool)
    target = down[rim]
    away = (target != rim) & (flat_beside[target] == 0)
    ids = flat_beside[rim]
    size = int(regions.max()) + 1
    total = np.bincount(ids, minlength=size)
    inland = np.bincount(ids[away], minlength=size)
    sink = np.flatnonzero((total > 0) & (inland < _SINK_SHARE * total))
    return np.isin(regions, sink[sink > 0])


def _dilate(mask, np):
    """The cells of mask and their eight neighbours."""
    height, width = mask.shape
    padded = np.pad(mask, 1, constant_values=False)
    grown = mask.copy()
    for dr, dc in _D8:
        grown |= padded[1 + dr:1 + dr + height, 1 + dc:1 + dc + width]
    return grown


def _srs(osr, wkt: str | None = None, epsg: int | None = None):
    srs = osr.SpatialReference()
    if epsg is not None:
        srs.ImportFromEPSG(epsg)
    else:
        srs.ImportFromWkt(wkt)
    try:
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    except AttributeError:  # pragma: no cover - GDAL < 3
        pass
    return srs


def _authid(srs) -> str:
    name, code = srs.GetAuthorityName(None), srs.GetAuthorityCode(None)
    return f"{name}:{code}" if name and code else ""


def _parse_bbox(value):
    """(xmin, ymin, xmax, ymax) in degrees, or the refusal."""
    try:
        xmin, ymin, xmax, ymax = (float(value[key]) for key in ("xmin", "ymin", "xmax", "ymax"))
    except (KeyError, TypeError, ValueError):
        return tool_error("bbox needs xmin, ymin, xmax and ymax in degrees.", "INVALID_ARGS",
                          'Pass {"xmin": 77.5, "ymin": 8.1, "xmax": 77.8, "ymax": 8.4}, or area with a polygon layer.')
    if not (-180.0 <= xmin < xmax <= 180.0 and -90.0 <= ymin < ymax <= 90.0):
        return tool_error("bbox is empty or off the world: xmin under xmax and ymin under ymax, in degrees.",
                          "INVALID_ARGS", "Check the order: xmin, ymin, xmax, ymax.")
    return xmin, ymin, xmax, ymax


def _box_geometry(ogr, xmin: float, ymin: float, xmax: float, ymax: float):
    ring = ogr.Geometry(ogr.wkbLinearRing)
    for x, y in ((xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax), (xmin, ymin)):
        ring.AddPoint_2D(x, y)
    polygon = ogr.Geometry(ogr.wkbPolygon)
    polygon.AddGeometry(ring)
    return polygon


def _envelope(geometries) -> tuple[float, float, float, float]:
    """(xmin, xmax, ymin, ymax) over OGR geometries, in GetEnvelope's order."""
    boxes = [geometry.GetEnvelope() for geometry in geometries]
    return (min(b[0] for b in boxes), max(b[1] for b in boxes), min(b[2] for b in boxes), max(b[3] for b in boxes))


def _bbox_around(lon: float, lat: float, side_km: float) -> dict:
    """A bbox in degrees side_km across, centred on (lon, lat)."""
    half_m = max(0.5, side_km) * 500.0
    dlon = half_m / (111_320.0 * max(0.05, math.cos(math.radians(lat))))
    dlat = half_m / 110_540.0
    return {"xmin": round(max(-180.0, lon - dlon), 5), "ymin": round(max(-90.0, lat - dlat), 5),
            "xmax": round(min(180.0, lon + dlon), 5), "ymax": round(min(90.0, lat + dlat), 5)}




def _open_water(dem, invalid, cell_x_m, cell_y_m, gdal, np):
    """Cells of a connected flat at exactly _WATER_LEVEL covering at least _MIN_WATER_M2 (8-connected)."""
    zero = (dem == _WATER_LEVEL) & ~invalid
    if not zero.any():
        return np.zeros(dem.shape, dtype=bool)
    height, width = dem.shape
    threshold = max(_MIN_WATER_CELLS, int(math.ceil(_MIN_WATER_M2 / (cell_x_m * cell_y_m))))
    driver = gdal.GetDriverByName("MEM")
    source = driver.Create("", width, height, 1, gdal.GDT_Byte)
    source.GetRasterBand(1).WriteArray(zero.astype("uint8"))
    target = driver.Create("", width, height, 1, gdal.GDT_Byte)
    gdal.SieveFilter(source.GetRasterBand(1), None, target.GetRasterBand(1), threshold, 8)
    dst = target.GetRasterBand(1).ReadAsArray()
    return dst.astype(bool) & zero


def _priority_flood(dem, invalid, np, heapq, checkpoint=None):
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
        for _ in range(_CHECK_EVERY):
            if not heap:
                break
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
        if checkpoint is not None:
            checkpoint()
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





    undecided = (best_dir == -1) & ~invalid
    if undecided.any():
        best_raw = np.zeros_like(filled)
        for index, (dr, dc) in enumerate(_D8):
            neighbour = padded[1 + dr:1 + dr + height, 1 + dc:1 + dc + width]
            ok = valid_padded[1 + dr:1 + dr + height, 1 + dc:1 + dc + width]
            raw = np.where(ok & undecided, filled - neighbour, -np.inf)
            better = raw > best_raw
            best_raw = np.where(better, raw, best_raw)
            best_dir = np.where(better, index, best_dir)
    rows, cols = np.indices(filled.shape)
    drs = np.array([d[0] for d in _D8] + [0], dtype="int64")
    dcs = np.array([d[1] for d in _D8] + [0], dtype="int64")
    target_r = rows + drs[best_dir]
    target_c = cols + dcs[best_dir]
    down = (target_r * width + target_c).astype("int64")
    down[invalid] = (rows * width + cols)[invalid]
    return down.ravel()


def _accumulate(filled, invalid, down, np, checkpoint=None):
    """Upstream cell counts, summed from the highest cell down."""
    flat = np.where(invalid, -np.inf, filled).ravel()
    order = np.argsort(-flat, kind="stable").tolist()
    acc = np.ones(flat.shape[0], dtype="int64")
    acc[invalid.ravel()] = 0
    down_list = down.tolist()
    acc_list = acc.tolist()
    for start in range(0, len(order), _CHECK_EVERY):
        for i in order[start:start + _CHECK_EVERY]:
            j = down_list[i]
            if j != i:
                acc_list[j] += acc_list[i]
        if checkpoint is not None:
            checkpoint()
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


def _strahler_network(filled, down, streams, np):
    """Strahler order of every stream cell, and the network cut into segments."""













    flat = streams.ravel()
    cells = np.flatnonzero(flat)
    count = int(cells.size)
    targets = down[cells]
    flows_on = (targets != cells) & flat[targets]
    down_pos = np.where(flows_on, np.searchsorted(cells, targets), -1)
    indegree = np.bincount(down_pos[flows_on], minlength=count)
    topological = np.argsort(-filled.ravel()[cells], kind="stable")
    next_of = down_pos.tolist()
    order, top, ties = [0] * count, [0] * count, [0] * count
    for p in topological.tolist():
        highest = top[p]
        value = 1 if highest == 0 else (highest + 1 if ties[p] > 1 else highest)
        order[p] = value
        q = next_of[p]
        if q >= 0:
            if value > top[q]:
                top[q], ties[q] = value, 1
            elif value == top[q]:
                ties[q] += 1
    fed = indegree.tolist()
    segments = []
    for start in np.flatnonzero(indegree != 1).tolist():
        path, q = [start], next_of[start]
        while q >= 0 and fed[q] == 1:
            path.append(q)
            q = next_of[q]
        segments.append((path, q))
    return cells, order, segments


def _segment_rows(cells, order, segments, accumulation, window_gt, width, cell_x_m, cell_y_m, cell_km2,
                  min_order, np, down=None) -> list:
    """(line WKB, strahler, length_m, upstream_km2) for every segment of min_order and up."""
    wx0, dx, wy0, dy = window_gt
    acc = accumulation.ravel()
    rows = []
    for path, confluence in segments:
        value = order[path[0]]
        if value < min_order:
            continue
        chain = path + [confluence] if confluence >= 0 else path
        flat_ids = cells[np.asarray(chain, dtype="int64")]
        if len(chain) < 2:


            here = int(flat_ids[0])
            target = int(down[here]) if down is not None else -1
            if target < 0 or target == here:
                continue
            flat_ids = np.asarray([here, target], dtype="int64")
        r, c = np.divmod(flat_ids, width)
        coords = np.column_stack((wx0 + (c + 0.5) * dx, wy0 + (r + 0.5) * dy)).astype("<f8")
        length_m = float(np.hypot(np.diff(r) * cell_y_m, np.diff(c) * cell_x_m).sum())






        rows.append((struct.pack("<BII", 1, 2, len(flat_ids)) + coords.tobytes(), int(value), round(length_m, 1),
                     round(float(acc[cells[path[-1]]]) * cell_km2, 4)))
    return rows


def _longest_flow_path(filled, basin, down, outlet_flat, cell_x_m, cell_y_m, np):
    """The longest D8 path that ends at the outlet, from its source down, and its length in metres."""
    height, width = filled.shape
    cells = np.flatnonzero(basin.ravel())
    position = np.full(height * width, -1, dtype="int64")
    position[cells] = np.arange(cells.size)
    targets = down[cells]
    down_pos = np.where(targets != cells, position[targets], -1)
    steps = np.hypot((targets // width - cells // width) * cell_y_m, (targets % width - cells % width) * cell_x_m)
    next_of, step, longest = down_pos.tolist(), steps.tolist(), [0.0] * int(cells.size)
    for p in np.argsort(-filled.ravel()[cells], kind="stable").tolist():
        q = next_of[p]
        if q >= 0 and longest[p] + step[p] > longest[q]:
            longest[q] = longest[p] + step[p]
    chain, here = [outlet_flat], outlet_flat
    while True:
        r, c = divmod(here, width)
        best, best_length = -1, -1.0
        for dr, dc in _D8:
            nr, nc = r + dr, c + dc
            if 0 <= nr < height and 0 <= nc < width:
                j = nr * width + nc
                pj = int(position[j])
                if pj >= 0 and int(down[j]) == here and longest[pj] + step[pj] > best_length:
                    best, best_length = j, longest[pj] + step[pj]
        if best < 0:
            break
        chain.append(best)
        here = best
    chain.reverse()
    return chain, longest[int(position[outlet_flat])]


def _rasterise(wkbs, geotransform, width, height, crs_wkt, gdal, ogr, osr, np):
    """The cells whose centre falls inside any of the polygons."""
    raster = gdal.GetDriverByName("MEM").Create("", width, height, 1, gdal.GDT_Byte)
    raster.SetGeoTransform(geotransform)
    raster.SetProjection(crs_wkt)
    srs = osr.SpatialReference()
    srs.ImportFromWkt(crs_wkt)
    memory = ogr.GetDriverByName("Memory").CreateDataSource("mask")
    layer = memory.CreateLayer("mask", srs=srs, geom_type=ogr.wkbUnknown)
    for wkb in wkbs:
        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetGeometry(ogr.CreateGeometryFromWkb(wkb))
        layer.CreateFeature(feature)
    gdal.RasterizeLayer(raster, [1], layer, burn_values=[1])
    return raster.GetRasterBand(1).ReadAsArray().astype(bool)


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
