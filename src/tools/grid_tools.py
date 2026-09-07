# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later























from __future__ import annotations

import importlib
import importlib.util
import itertools
import math

from ..core.logger import log
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .data_tools import _run_on_main_thread
from .deps_tools import OPTIONAL_DEPENDENCIES



SYSTEMS = ("geohash", "olc", "h3", "s2")


GEOHASH_MIN_PRECISION = 1
GEOHASH_MAX_PRECISION = 12
OLC_CODE_LENGTHS = (2, 4, 6, 8, 10, 11, 12, 13, 14, 15)
H3_MAX_RESOLUTION = 15
S2_MAX_LEVEL = 30



DEFAULT_MAX_CELLS = 10000
HARD_MAX_CELLS = 200000


AUTO_TARGET_CELLS = 10000


EARTH_AREA_KM2 = 510065621.724

_KM_PER_DEG_LAT = 110.574
_KM_PER_DEG_LON_EQ = 111.32

OPTIONAL_DEPENDENCIES.setdefault("h3", {
    "package": "h3",
    "import_name": "h3",
    "unlocks": "H3 hexagonal grids in create_grid_layer, encode_cells and decode_cell",
    "post_install": "Reload the plugin (QGIS plugin manager), then pass system:'h3' to the grid tools.",
})
OPTIONAL_DEPENDENCIES.setdefault("s2", {
    "package": "s2sphere",
    "import_name": "s2sphere",
    "unlocks": "S2 quadrilateral grids in create_grid_layer, encode_cells and decode_cell",
    "post_install": "Reload the plugin (QGIS plugin manager), then pass system:'s2' to the grid tools.",
})











GEOHASH_ALPHABET = "0123456789bcdefghjkmnpqrstuvwxyz"  # pragma: allowlist secret
_GEOHASH_INDEX = {char: value for value, char in enumerate(GEOHASH_ALPHABET)}
_GEOHASH_CHAR_BITS = 5


def _narrow(interval: list, upper: bool) -> None:

    middle = (interval[0] + interval[1]) / 2
    if upper:
        interval[0] = middle
    else:
        interval[1] = middle


def geohash_encode(latitude: float, longitude: float, precision: int = 9) -> str:

    if precision < 1:
        raise ValueError("geohash precision must be at least 1")
    axes = ([-180.0, 180.0, longitude], [-90.0, 90.0, latitude])
    packed = 0
    total = _GEOHASH_CHAR_BITS * precision
    for position in range(total):
        axis = axes[position % 2]
        upper = axis[2] > (axis[0] + axis[1]) / 2
        _narrow(axis, upper)
        packed = (packed << 1) | int(upper)
    mask = (1 << _GEOHASH_CHAR_BITS) - 1
    return "".join(
        GEOHASH_ALPHABET[(packed >> shift) & mask]
        for shift in range(total - _GEOHASH_CHAR_BITS, -1, -_GEOHASH_CHAR_BITS)
    )


def geohash_decode_bbox(code: str) -> tuple:

    if not code:
        raise ValueError("empty geohash")
    longitudes = [-180.0, 180.0]
    latitudes = [-90.0, 90.0]
    position = 0
    for char in code.lower():
        value = _GEOHASH_INDEX.get(char)
        if value is None:
            raise ValueError(f"'{char}' is not a geohash character")
        for shift in range(_GEOHASH_CHAR_BITS - 1, -1, -1):
            _narrow(latitudes if position % 2 else longitudes, bool((value >> shift) & 1))
            position += 1
    return latitudes[0], longitudes[0], latitudes[1], longitudes[1]


def geohash_decode(code: str) -> tuple:

    south, west, north, east = geohash_decode_bbox(code)
    return (south + north) / 2, (west + east) / 2


def geohash_divisions(precision: int) -> tuple:

    total_bits = 5 * precision
    lat_bits = total_bits // 2
    lon_bits = total_bits - lat_bits
    return 2 ** lat_bits, 2 ** lon_bits

















OLC_ALPHABET = "23456789CFGHJMPQRVWX"
OLC_SEPARATOR = "+"
OLC_SEPARATOR_POSITION = 8
OLC_PADDING = "0"
_OLC_BASE = 20
_OLC_PAIR_LENGTH = 10
_OLC_MAX_DIGITS = 15
_OLC_GRID_ROWS = 5
_OLC_GRID_COLUMNS = 4
_OLC_PAIRS = _OLC_PAIR_LENGTH // 2
_OLC_GRID_DIGITS = _OLC_MAX_DIGITS - _OLC_PAIR_LENGTH


_OLC_PAIR_STEPS = _OLC_BASE ** 3
_OLC_LAT_STEPS = _OLC_PAIR_STEPS * _OLC_GRID_ROWS ** _OLC_GRID_DIGITS
_OLC_LON_STEPS = _OLC_PAIR_STEPS * _OLC_GRID_COLUMNS ** _OLC_GRID_DIGITS



_OLC_PAIR_WEIGHTS = tuple(_OLC_BASE ** (_OLC_PAIRS - 1 - pair) for pair in range(_OLC_PAIRS))
_OLC_GRID_WEIGHTS = tuple(
    (_OLC_GRID_ROWS ** (_OLC_GRID_DIGITS - 1 - digit), _OLC_GRID_COLUMNS ** (_OLC_GRID_DIGITS - 1 - digit))
    for digit in range(_OLC_GRID_DIGITS)
)


def _clip_latitude(latitude: float) -> float:
    return min(90.0, max(-90.0, latitude))


def _normalize_longitude(longitude: float) -> float:
    while longitude < -180:
        longitude += 360
    while longitude >= 180:
        longitude -= 360
    return longitude


def _olc_digits(lat_steps: int, lon_steps: int) -> str:

    lat_pairs, lat_grid = divmod(lat_steps, _OLC_GRID_ROWS ** _OLC_GRID_DIGITS)
    lon_pairs, lon_grid = divmod(lon_steps, _OLC_GRID_COLUMNS ** _OLC_GRID_DIGITS)
    digits = []
    for weight in _OLC_PAIR_WEIGHTS:
        digits.append(OLC_ALPHABET[(lat_pairs // weight) % _OLC_BASE])
        digits.append(OLC_ALPHABET[(lon_pairs // weight) % _OLC_BASE])
    for row_weight, column_weight in _OLC_GRID_WEIGHTS:
        row = (lat_grid // row_weight) % _OLC_GRID_ROWS
        column = (lon_grid // column_weight) % _OLC_GRID_COLUMNS
        digits.append(OLC_ALPHABET[row * _OLC_GRID_COLUMNS + column])
    return "".join(digits)


def olc_encode(latitude: float, longitude: float, code_length: int = 10) -> str:

    if code_length < 2 or (code_length < _OLC_PAIR_LENGTH and code_length % 2 == 1):
        raise ValueError(f"invalid Open Location Code length: {code_length}")
    code_length = min(code_length, _OLC_MAX_DIGITS)
    latitude = _clip_latitude(latitude)
    longitude = _normalize_longitude(longitude)


    if latitude == 90:
        latitude -= olc_cell_size(code_length)[0]



    lat_steps = int(round((latitude + 90) * _OLC_LAT_STEPS, 6))
    lon_steps = int(round((longitude + 180) * _OLC_LON_STEPS, 6))
    body = _olc_digits(lat_steps, lon_steps)[:code_length]

    if code_length < OLC_SEPARATOR_POSITION:
        return body + OLC_PADDING * (OLC_SEPARATOR_POSITION - code_length) + OLC_SEPARATOR
    return body[:OLC_SEPARATOR_POSITION] + OLC_SEPARATOR + body[OLC_SEPARATOR_POSITION:]


def _olc_clean(code: str) -> str:










    raw = (code or "").upper().strip()
    if not raw:
        raise ValueError("empty Open Location Code")
    if raw.count(OLC_SEPARATOR) != 1:
        raise ValueError("an Open Location Code has exactly one '+' separator")
    separator_at = raw.index(OLC_SEPARATOR)
    if separator_at != OLC_SEPARATOR_POSITION or separator_at % 2 == 1:
        raise ValueError(
            f"the '+' of a full Open Location Code sits after {OLC_SEPARATOR_POSITION} characters; "
            f"a short code like '{raw}' needs a reference location, which this tool does not take")
    body = raw.replace(OLC_SEPARATOR, "")
    if len(raw) > separator_at + 1 and len(raw) - separator_at - 1 < 2:
        raise ValueError("an Open Location Code has at least two characters after the '+'")
    padding = 0
    while body.endswith(OLC_PADDING):
        body = body[:-1]
        padding += 1
    if OLC_PADDING in body:
        raise ValueError("the '0' padding of an Open Location Code only ever comes at the end")
    if padding:
        if padding % 2 == 1 or len(body) < 2 or len(body) > OLC_SEPARATOR_POSITION - 2:
            raise ValueError("the padding of an Open Location Code is an even amount, before the separator")
        if len(raw) > separator_at + 1:
            raise ValueError("a padded Open Location Code carries nothing after its '+'")
    for char in body:
        if char not in OLC_ALPHABET:
            raise ValueError(f"'{char}' is not an Open Location Code character")
    if len(body) < 2:
        raise ValueError("an Open Location Code carries at least two characters")
    if len(body) % 2 == 1 and len(body) < _OLC_PAIR_LENGTH:
        raise ValueError("the paired part of an Open Location Code has an even number of characters")


    if OLC_ALPHABET.index(body[0]) > 8:
        raise ValueError("the first character of an Open Location Code puts it past the north pole")
    if OLC_ALPHABET.index(body[1]) > 17:
        raise ValueError("the second character of an Open Location Code puts it past longitude 180")
    return body[:_OLC_MAX_DIGITS]


def olc_decode_bbox(code: str) -> tuple:

    body = _olc_clean(code)
    pair_part = body[:_OLC_PAIR_LENGTH]
    grid_part = body[_OLC_PAIR_LENGTH:_OLC_MAX_DIGITS]


    lat_pairs = -90 * _OLC_PAIR_STEPS
    lon_pairs = -180 * _OLC_PAIR_STEPS
    used = len(pair_part) // 2
    for weight, (lat_char, lon_char) in zip(_OLC_PAIR_WEIGHTS, zip(pair_part[0::2], pair_part[1::2])):
        lat_pairs += OLC_ALPHABET.index(lat_char) * weight
        lon_pairs += OLC_ALPHABET.index(lon_char) * weight
    height = _OLC_PAIR_WEIGHTS[used - 1] / _OLC_PAIR_STEPS
    width = _OLC_PAIR_WEIGHTS[used - 1] / _OLC_PAIR_STEPS


    lat_rows = 0
    lon_columns = 0
    for (row_weight, column_weight), char in zip(_OLC_GRID_WEIGHTS, grid_part):
        row, column = divmod(OLC_ALPHABET.index(char), _OLC_GRID_COLUMNS)
        lat_rows += row * row_weight
        lon_columns += column * column_weight
    if grid_part:
        row_weight, column_weight = _OLC_GRID_WEIGHTS[len(grid_part) - 1]
        height = row_weight / _OLC_LAT_STEPS
        width = column_weight / _OLC_LON_STEPS

    south = lat_pairs / _OLC_PAIR_STEPS + lat_rows / _OLC_LAT_STEPS
    west = lon_pairs / _OLC_PAIR_STEPS + lon_columns / _OLC_LON_STEPS
    return south, west, south + height, west + width


def olc_code_length(code: str) -> int:
    return len(_olc_clean(code))


def olc_cell_size(code_length: int) -> tuple:

    lat_cells, lon_cells = olc_divisions(code_length)
    return 180.0 / lat_cells, 360.0 / lon_cells


def olc_divisions(code_length: int) -> tuple:





    pairs = min(code_length, _OLC_PAIR_LENGTH) // 2
    lat_cells = 9 * _OLC_BASE ** (pairs - 1)
    lon_cells = 18 * _OLC_BASE ** (pairs - 1)
    if code_length > _OLC_PAIR_LENGTH:
        extra = code_length - _OLC_PAIR_LENGTH
        lat_cells *= _OLC_GRID_ROWS ** extra
        lon_cells *= _OLC_GRID_COLUMNS ** extra
    return lat_cells, lon_cells





def _optional_module(feature: str):

    spec = OPTIONAL_DEPENDENCIES.get(feature) or {}
    import_name = spec.get("import_name", feature)
    try:
        if importlib.util.find_spec(import_name) is None:
            return None
        return importlib.import_module(import_name)
    except Exception:  # noqa: BLE001
        return None


def _missing_dependency_error(feature: str) -> dict:
    spec = OPTIONAL_DEPENDENCIES.get(feature) or {}
    package = spec.get("package", feature)
    return tool_error(
        f"The '{package}' package is not installed, so {feature.upper()} grids are unavailable. "
        f"Geohash and Open Location Code need no package and work now.",
        code="INVALID_ARGS", hint="grid_package_missing", feature=feature,
    )


def _h3_call(module, names: tuple, *args, **kwargs):

    for name in names:
        function = getattr(module, name, None)
        if function is not None:
            return function(*args, **kwargs)
    raise RuntimeError(f"This h3 version exposes none of {names}. Upgrade the h3 package.")





def _validate_resolution(system: str, resolution) -> tuple:

    try:
        value = int(resolution)
    except (TypeError, ValueError):
        return None, tool_error(f"resolution must be a whole number, got {resolution!r}", code="BAD_RESOLUTION")
    if system == "geohash":
        if not GEOHASH_MIN_PRECISION <= value <= GEOHASH_MAX_PRECISION:
            return None, tool_error(
                f"geohash precision must be {GEOHASH_MIN_PRECISION} to {GEOHASH_MAX_PRECISION}, got {value}",
                code="BAD_RESOLUTION",
            )
    elif system == "olc":
        if value not in OLC_CODE_LENGTHS:
            return None, tool_error(
                f"Open Location Code length must be one of {list(OLC_CODE_LENGTHS)}, got {value}",
                code="BAD_RESOLUTION",
            )
    elif system == "h3":
        if not 0 <= value <= H3_MAX_RESOLUTION:
            return None, tool_error(f"h3 resolution must be 0 to {H3_MAX_RESOLUTION}, got {value}",
                                    code="BAD_RESOLUTION")
    elif system == "s2":
        if not 0 <= value <= S2_MAX_LEVEL:
            return None, tool_error(f"s2 level must be 0 to {S2_MAX_LEVEL}, got {value}", code="BAD_RESOLUTION")
    return value, None


def _resolutions(system: str) -> tuple:
    if system == "geohash":
        return tuple(range(GEOHASH_MIN_PRECISION, GEOHASH_MAX_PRECISION + 1))
    if system == "olc":
        return OLC_CODE_LENGTHS
    if system == "h3":
        return tuple(range(H3_MAX_RESOLUTION + 1))
    return tuple(range(S2_MAX_LEVEL + 1))


def bbox_area_km2(bbox: tuple) -> float:






    west, south, east, north = bbox
    height_km = abs(north - south) * _KM_PER_DEG_LAT
    width_deg = east - west if east >= west else east - west + 360
    shrink = math.cos(math.radians((south + north) / 2))


    return height_km * width_deg * _KM_PER_DEG_LON_EQ * shrink


def _average_cell_area_km2(system: str, resolution: int, module=None) -> float:

    if system == "geohash":
        lat_cells, lon_cells = geohash_divisions(resolution)
        return EARTH_AREA_KM2 / (lat_cells * lon_cells)
    if system == "olc":
        lat_cells, lon_cells = olc_divisions(resolution)
        return EARTH_AREA_KM2 / (lat_cells * lon_cells)
    if system == "s2":
        return EARTH_AREA_KM2 / (6 * 4 ** resolution)
    return _h3_call(module, ("average_hexagon_area", "hex_area"), resolution, "km^2")


def _suggest_resolution(system: str, area_km2: float, target: int, module=None) -> int:

    candidates = _resolutions(system)
    for resolution in reversed(candidates):
        cell_area = _average_cell_area_km2(system, resolution, module)
        if cell_area > 0 and area_km2 / cell_area <= target:
            return resolution
    return candidates[0]


def _rect_wkt(south: float, west: float, north: float, east: float) -> str:
    return (
        f"POLYGON(({west:.10f} {south:.10f}, {east:.10f} {south:.10f}, "
        f"{east:.10f} {north:.10f}, {west:.10f} {north:.10f}, {west:.10f} {south:.10f}))"
    )


def _ring_wkt(points: list) -> str:

    ring = list(points)
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    coordinates = ", ".join(f"{lon:.10f} {lat:.10f}" for lon, lat in ring)
    return f"POLYGON(({coordinates}))"


def _estimate_cells(system: str, bbox: tuple, resolution: int, module=None) -> float:

    west, south, east, north = bbox
    if system in ("geohash", "olc"):
        lat_cells, lon_cells = (
            geohash_divisions(resolution) if system == "geohash" else olc_divisions(resolution)
        )
        rows = math.ceil((north + 90) * lat_cells / 180.0) - math.floor((south + 90) * lat_cells / 180.0)
        columns = math.ceil((east + 180) * lon_cells / 360.0) - math.floor((west + 180) * lon_cells / 360.0)
        return max(1, rows) * max(1, columns)
    cell_area = _average_cell_area_km2(system, resolution, module)
    if cell_area <= 0:
        return float("inf")
    return bbox_area_km2(bbox) / cell_area


def _rect_cells(system: str, bbox: tuple, resolution: int, limit: int) -> tuple:






    west, south, east, north = bbox
    lat_cells, lon_cells = (
        geohash_divisions(resolution) if system == "geohash" else olc_divisions(resolution)
    )
    encode = geohash_encode if system == "geohash" else olc_encode
    lat_step = 180.0 / lat_cells
    lon_step = 360.0 / lon_cells

    first_row = max(0, math.floor((south + 90) / lat_step))
    last_row = min(lat_cells - 1, math.ceil((north + 90) / lat_step) - 1)
    first_column = max(0, math.floor((west + 180) / lon_step))
    last_column = min(lon_cells - 1, math.ceil((east + 180) / lon_step) - 1)
    if last_row < first_row or last_column < first_column:
        return [], None

    total = (last_row - first_row + 1) * (last_column - first_column + 1)
    if total > limit:
        return None, _too_many_cells(system, resolution, total, limit)

    cells = []
    cancel = _cancel_check()
    for row in range(first_row, last_row + 1):



        if cancel is not None and cancel():
            return None, tool_error("The run was stopped while the grid was being built.", code="CANCELLED")
        cell_south = -90.0 + row * lat_step
        cell_north = -90.0 + (row + 1) * lat_step
        centre_lat = (cell_south + cell_north) / 2
        for column in range(first_column, last_column + 1):
            cell_west = -180.0 + column * lon_step
            cell_east = -180.0 + (column + 1) * lon_step
            centre_lon = (cell_west + cell_east) / 2
            cells.append({
                "cell_id": encode(centre_lat, centre_lon, resolution),
                "wkt": _rect_wkt(cell_south, cell_west, cell_north, cell_east),
                "center_lat": centre_lat,
                "center_lon": centre_lon,
            })
    return cells, None


def _cancel_check():

    try:
        from ..core import net

        return net.current_cancel_check()
    except Exception:  # noqa: BLE001
        return None


def _too_many_cells(system: str, resolution: int, count, limit: int) -> dict:
    readable = int(count) if count != float("inf") else "more than can be counted"
    return tool_error(
        f"{system} resolution {resolution} needs about {readable} cells over this extent, "
        f"above the {limit} limit.",
        code="INVALID_ARGS", hint="grid_too_many_cells", hard_cap=HARD_MAX_CELLS,
    )


def _h3_cells(module, bbox: tuple, resolution: int, limit: int) -> tuple:
    west, south, east, north = bbox
    ring = [(south, west), (south, east), (north, east), (north, west)]
    ids = None
    shape_class = getattr(module, "LatLngPoly", None)
    if shape_class is not None:
        shape = shape_class(ring)
        for name in ("h3shape_to_cells", "polygon_to_cells"):
            function = getattr(module, name, None)
            if function is not None:



                ids = list(itertools.islice(function(shape, resolution), limit + 1))
                if len(ids) > limit:
                    return None, _too_many_cells("h3", resolution, float("inf"), limit)
                break
    if ids is None:
        polyfill = getattr(module, "polyfill", None)
        if polyfill is None:
            raise RuntimeError("This h3 version exposes neither LatLngPoly nor polyfill. Upgrade the h3 package.")
        geojson = {"type": "Polygon", "coordinates": [[[lon, lat] for lat, lon in ring] + [[west, south]]]}
        ids = list(itertools.islice(polyfill(geojson, resolution, geo_json_conformant=True), limit + 1))
        if len(ids) > limit:
            return None, _too_many_cells("h3", resolution, float("inf"), limit)

    cells = []
    for cell_id in ids:
        boundary = _h3_call(module, ("cell_to_boundary", "h3_to_geo_boundary"), cell_id)
        centre = _h3_call(module, ("cell_to_latlng", "h3_to_geo"), cell_id)
        cells.append({
            "cell_id": str(cell_id),
            "wkt": _ring_wkt([(lon, lat) for lat, lon in boundary]),
            "center_lat": float(centre[0]),
            "center_lon": float(centre[1]),
        })
    return cells, None


def _s2_cells(module, bbox: tuple, resolution: int, limit: int) -> tuple:
    west, south, east, north = bbox
    coverer = module.RegionCoverer()
    coverer.min_level = resolution
    coverer.max_level = resolution
    coverer.max_cells = min(limit, HARD_MAX_CELLS)
    rect = module.LatLngRect(
        module.LatLng.from_degrees(max(-89.999999, south), west),
        module.LatLng.from_degrees(min(89.999999, north), east),
    )
    ids = list(coverer.get_covering(rect))
    if len(ids) > limit:
        return None, _too_many_cells("s2", resolution, len(ids), limit)

    cells = []
    for cell_id in ids:
        cells.append(_s2_cell_record(module, cell_id))
    return cells, None


def _s2_cell_record(module, cell_id) -> dict:
    cell = module.Cell(cell_id)
    ring = []
    for vertex in range(4):
        point = module.LatLng.from_point(cell.get_vertex(vertex))
        ring.append((point.lng().degrees, point.lat().degrees))
    centre = module.LatLng.from_point(cell.get_center())
    return {
        "cell_id": cell_id.to_token(),
        "wkt": _ring_wkt(ring),
        "center_lat": centre.lat().degrees,
        "center_lon": centre.lng().degrees,
    }





def _read_bbox(value) -> tuple:

    if isinstance(value, (list, tuple)):
        if len(value) != 4:
            return None, tool_error("bbox as a list must be [west, south, east, north]", code="INVALID_ARGS")
        west, south, east, north = (float(item) for item in value)
    elif isinstance(value, dict):
        west = value.get("west", value.get("xmin"))
        south = value.get("south", value.get("ymin"))
        east = value.get("east", value.get("xmax"))
        north = value.get("north", value.get("ymax"))
        if None in (west, south, east, north):
            return None, tool_error(
                "bbox must give {west, south, east, north} or {xmin, ymin, xmax, ymax}", code="INVALID_ARGS",
            )
        west, south, east, north = float(west), float(south), float(east), float(north)
    else:
        return None, tool_error("bbox must be a list or an object", code="INVALID_ARGS")

    for label, number in (("west", west), ("south", south), ("east", east), ("north", north)):
        if not math.isfinite(number):
            return None, tool_error(f"bbox {label} is not a finite number", code="INVALID_ARGS")
    if south > north:
        south, north = north, south
    if west > east:




        if _crosses_dateline(west, east):
            return None, tool_error(
                f"This bbox crosses the antimeridian (west {west} is east of east {east}), and this tool "
                f"builds one rectangle, not two.",
                code="INVALID_ARGS", hint="grid_antimeridian",
            )
        west, east = east, west
    south = max(-90.0, south)
    north = min(90.0, north)
    if east - west >= 360 or west < -180 or east > 180:
        west, east = -180.0, 180.0
    if north - south <= 0 or east - west <= 0:
        return None, tool_error("bbox has no area", code="INVALID_ARGS")
    return (west, south, east, north), None


def _crosses_dateline(west: float, east: float) -> bool:







    if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0):
        return False
    wrapped = (east + 360.0) - west
    inverted = west - east
    return wrapped < inverted


def _layer_bbox_4326(layer_name: str):

    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsCoordinateTransform,
        QgsProject,
    )

    from .core_tools import _find_layer

    layer = _find_layer(layer_name)
    if layer is None:
        return None
    extent = layer.extent()
    crs = layer.crs()
    if crs.isValid() and crs.authid() != "EPSG:4326":
        transform = QgsCoordinateTransform(
            crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance()
        )
        extent = transform.transformBoundingBox(extent)
    return [extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()]


def _canvas_bbox_4326():

    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsCoordinateTransform,
        QgsProject,
    )
    from qgis.utils import iface as qgis_iface

    canvas = qgis_iface.mapCanvas()
    extent = canvas.extent()
    crs = canvas.mapSettings().destinationCrs()
    if crs.authid() != "EPSG:4326":
        transform = QgsCoordinateTransform(
            crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance()
        )
        extent = transform.transformBoundingBox(extent)
    return [extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()]


def _resolve_extent(args: dict) -> tuple:

    if args.get("bbox") is not None:
        bbox, error = _read_bbox(args["bbox"])
        return (None, None, error) if error else (bbox, "bbox", None)

    layer_name = args.get("layer")
    if layer_name:
        raw = _run_on_main_thread(_layer_bbox_4326, layer_name, timeout=30)
        if raw is None:
            return None, None, tool_error(
                f"Layer '{layer_name}' not found.",
                code="LAYER_NOT_FOUND",
                suggestion="Call list_layers to see the exact layer names and ids.",
            )
        bbox, error = _read_bbox(raw)
        return (None, None, error) if error else (bbox, f"layer:{layer_name}", None)

    raw = _run_on_main_thread(_canvas_bbox_4326, timeout=30)
    if not raw:
        return None, None, tool_error(
            "No extent given and the map canvas extent is unavailable.",
            code="INVALID_ARGS", hint="grid_no_extent",
        )
    bbox, error = _read_bbox(raw)
    return (None, None, error) if error else (bbox, "canvas", None)





def detect_system(cell_id: str) -> str | None:






    code = (cell_id or "").strip()
    if not code:
        return None
    if OLC_SEPARATOR in code:
        return "olc"
    lowered = code.lower()




    candidates = []
    if len(lowered) <= GEOHASH_MAX_PRECISION and all(char in _GEOHASH_INDEX for char in lowered):
        candidates.append("geohash")
    h3_module = _optional_module("h3")
    if h3_module is not None:
        try:
            if _h3_call(h3_module, ("is_valid_cell", "h3_is_valid"), lowered):
                candidates.append("h3")
        except Exception:  # noqa: BLE001  # nosec B110
            pass
    s2_module = _optional_module("s2")
    if s2_module is not None:
        try:
            if s2_module.CellId.from_token(lowered).is_valid():
                candidates.append("s2")
        except Exception:  # noqa: BLE001  # nosec B110
            pass
    if len(candidates) == 1:
        return candidates[0]


    return None




def register_grid_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="create_grid_layer",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {
                "system": {
                    "type": "string",
                    "enum": list(SYSTEMS),
                },
                "resolution": {
                    "type": "integer",
                },
                "bbox": {
                    "type": "object",
                    "properties": {
                        "west": {"type": "number"},
                        "south": {"type": "number"},
                        "east": {"type": "number"},
                        "north": {"type": "number"},
                    },
                    "required": ["west", "south", "east", "north"],
                },
                "layer": {
                    "type": "string",
                },
                "max_cells": {
                    "type": "integer",
                },
                "layer_name": {
                    "type": "string",
                },
            },
            "required": ["system"],
        },
        handler=_create_grid_layer,
        background=True,
    ))




    log("Grid tools registered (geohash, olc, h3, s2)")





def _check_system(args: dict) -> tuple:

    system = (args.get("system") or "").strip().lower()
    if system not in SYSTEMS:
        return None, None, tool_error(
            f"system must be one of {list(SYSTEMS)}, got {args.get('system')!r}", code="BAD_SYSTEM",
        )
    if system in ("h3", "s2"):
        module = _optional_module(system)
        if module is None:
            return None, None, _missing_dependency_error(system)
        return system, module, None
    return system, None, None


def _create_grid_layer(args: dict) -> dict:
    system, module, error = _check_system(args)
    if error:
        return error

    bbox, source, error = _resolve_extent(args)
    if error:
        return error

    try:
        limit = int(args.get("max_cells") or DEFAULT_MAX_CELLS)
    except (TypeError, ValueError):
        return tool_error("max_cells must be a whole number", code="BAD_ARGUMENT")
    limit = max(1, min(limit, HARD_MAX_CELLS))

    if args.get("resolution") is None:
        resolution = _suggest_resolution(system, bbox_area_km2(bbox), min(limit, AUTO_TARGET_CELLS), module)
    else:
        resolution, error = _validate_resolution(system, args["resolution"])
        if error:
            return error

    estimate = _estimate_cells(system, bbox, resolution, module)
    if estimate > limit:
        return _too_many_cells(system, resolution, estimate, limit)

    try:
        if system in ("geohash", "olc"):
            cells, error = _rect_cells(system, bbox, resolution, limit)
        elif system == "h3":
            cells, error = _h3_cells(module, bbox, resolution, limit)
        else:
            cells, error = _s2_cells(module, bbox, resolution, limit)
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"Building {system} cells failed: {exc}", code="GRID_FAILED")
    if error:
        return error
    if not cells:
        return tool_error(
            f"No {system} cell falls in this extent.", code="INVALID_ARGS", hint="grid_empty",
        )

    name = args.get("layer_name") or f"{system} grid res {resolution}"
    created = _run_on_main_thread(_build_grid_layer, name, system, resolution, cells, timeout=120)
    if isinstance(created, dict) and created.get("_error"):
        return created

    out = {
        "layer_name": created["name"],
        "layer_id": created["layer_id"],
        "system": system,
        "resolution": resolution,

        "cells": created.get("inserted", len(cells)),



        "containment": _CONTAINMENT[system],
        "extent_source": source,
        "bbox": {"west": bbox[0], "south": bbox[1], "east": bbox[2], "north": bbox[3]},
        "crs": "EPSG:4326",
        "storage": "memory",
        "_next": "save_layer_to_gpkg persists it; set_layer_style can colour it by a joined value.",
    }
    if created.get("rejected"):
        out["rejected_cells"] = created["rejected"]
    return out




_CONTAINMENT = {
    "geohash": "intersects",
    "olc": "intersects",
    "s2": "intersects",
    "h3": "center",
}


def _build_grid_layer(name: str, system: str, resolution: int, cells: list) -> dict:

    from qgis.core import QgsFeature, QgsGeometry, QgsProject, QgsVectorLayer

    uri = (
        "Polygon?crs=EPSG:4326&field=cell_id:string(32)&field=system:string(16)"
        "&field=resolution:integer&field=center_lat:double&field=center_lon:double"
    )
    layer = QgsVectorLayer(uri, name, "memory")
    if not layer.isValid():
        return {"_error": "Failed to create the grid memory layer"}

    features = []
    rejected = 0
    fields = layer.fields()
    for cell in cells:
        feature = QgsFeature(fields)
        geometry = QgsGeometry.fromWkt(cell["wkt"])
        if geometry.isEmpty():


            rejected += 1
            continue
        feature.setGeometry(geometry)
        feature.setAttributes([
            cell["cell_id"], system, resolution,
            round(cell["center_lat"], 10), round(cell["center_lon"], 10),
        ])
        features.append(feature)

    if not features:
        return {"_error": f"None of the {len(cells)} {system} cells converted to a usable geometry, "
                          f"so no layer was added.",
                "code": "GRID_FAILED"}
    ok, _ = layer.dataProvider().addFeatures(features)
    if not ok:
        return {"_error": "The memory provider refused the grid features"}
    layer.updateExtents()
    QgsProject.instance().addMapLayer(layer)
    return {"name": layer.name(), "layer_id": layer.id(),
            "inserted": len(features), "rejected": rejected}








