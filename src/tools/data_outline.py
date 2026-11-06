# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



from __future__ import annotations

import json



_LIFTED_OUTLINE_VERTICES = 4000
_OUTLINE_PRECISION_DEG = 0.002


def _polys_bbox(polys: list):

    xs: list = []
    ys: list = []
    for exterior, _holes in polys:
        for x, y in exterior:
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _ring_contains(x: float, y: float, ring: list) -> bool:

    inside = False
    count = len(ring)
    previous = count - 1
    for current in range(count):
        xi, yi = ring[current]
        xj, yj = ring[previous]
        if (yi > y) != (yj > y):
            if x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
        previous = current
    return inside






_RING_BAND_EDGES = 8
_RING_MAX_BANDS = 4096


def _ring_index(ring: list):

    count = len(ring)
    if count < 3:
        return (0.0, 1.0, 1, [[]])
    ys = [point[1] for point in ring]
    south, north = min(ys), max(ys)
    bands = max(1, min(_RING_MAX_BANDS, count // _RING_BAND_EDGES))
    height = (north - south) / bands or 1.0
    table: list = [[] for _ in range(bands)]
    last = bands - 1
    previous = count - 1
    for current in range(count):
        xi, yi = ring[current]
        xj, yj = ring[previous]
        previous = current
        if yi == yj:

            continue
        low, high = (yi, yj) if yi < yj else (yj, yi)
        first = min(last, int((low - south) / height))
        final = min(last, int((high - south) / height))
        edge = (xi, yi, xj, yj)
        for band in range(first, final + 1):
            table[band].append(edge)
    return (south, height, bands, table)


def _ring_index_contains(x: float, y: float, index) -> bool:





    south, height, bands, table = index
    band = min(bands - 1, max(0, int((y - south) / height)))
    inside = False
    for xi, yi, xj, yj in table[band]:
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
    return inside


def _outline_contains(x: float, y: float, outline: dict) -> bool:

    west, south, east, north = outline["bbox"]
    if not (west <= x <= east and south <= y <= north):
        return False
    indexed = outline.get("_rings")
    if indexed is None:

        indexed = outline["_rings"] = [
            (_ring_index(exterior), _polys_bbox([(exterior, [])]), [_ring_index(hole) for hole in holes])
            for exterior, holes in outline["polys"]]
    for exterior, bounds, holes in indexed:
        if bounds is None or not (bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]):
            continue
        if not _ring_index_contains(x, y, exterior):
            continue
        if any(_ring_index_contains(x, y, hole) for hole in holes):
            continue
        return True
    return False


def _outline_shape(outline: dict, simplify: bool = True):



    from osgeo import ogr

    polys = outline.get("polys") or []
    coordinates = [[[list(point) for point in exterior]] + [[list(point) for point in hole] for hole in holes]
                   for exterior, holes in polys]
    shape = ogr.CreateGeometryFromJson(json.dumps({"type": "MultiPolygon", "coordinates": coordinates}))
    if shape is None:
        return None
    if simplify and sum(len(ring) for polygon in coordinates for ring in polygon) > _LIFTED_OUTLINE_VERTICES:


        sizes = sorted(max(east - west, north - south) for west, south, east, north in
                       (_polys_bbox([(exterior, [])]) for exterior, _holes in polys))
        shape = shape.SimplifyPreserveTopology(min(_OUTLINE_PRECISION_DEG, sizes[len(sizes) // 2] / 20.0))
    if not shape.IsValid():
        shape = shape.Buffer(0)
    return shape


def _prepared(shape):






    try:
        return shape.CreatePreparedGeometry()
    except Exception:  # noqa: BLE001
        return None
