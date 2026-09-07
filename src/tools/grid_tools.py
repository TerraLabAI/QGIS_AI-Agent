# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Discrete global grid tools: geohash, Open Location Code, H3 and S2."""
























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



_MAX_ENCODE_FEATURES = 200_000


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









GEOHASH_ALPHABET = "0123456789bcdefghjkmnpqrstuvwxyz"  # pragma: allowlist secret - base32 digits, not a key
_GEOHASH_INDEX = {char: value for value, char in enumerate(GEOHASH_ALPHABET)}
_BITS = (16, 8, 4, 2, 1)


def geohash_encode(latitude: float, longitude: float, precision: int = 9) -> str:
    """The geohash of a point, ``precision`` characters long."""
    if precision < 1:
        raise ValueError("geohash precision must be at least 1")
    lat_low, lat_high = -90.0, 90.0
    lon_low, lon_high = -180.0, 180.0
    code = []
    bit = 0
    char = 0
    even = True
    while len(code) < precision:
        if even:
            middle = (lon_low + lon_high) / 2
            if longitude > middle:
                char |= _BITS[bit]
                lon_low = middle
            else:
                lon_high = middle
        else:
            middle = (lat_low + lat_high) / 2
            if latitude > middle:
                char |= _BITS[bit]
                lat_low = middle
            else:
                lat_high = middle
        even = not even
        if bit < 4:
            bit += 1
        else:
            code.append(GEOHASH_ALPHABET[char])
            bit = 0
            char = 0
    return "".join(code)


def geohash_decode_bbox(code: str) -> tuple:
    """``(south, west, north, east)`` of a geohash cell, in degrees."""
    if not code:
        raise ValueError("empty geohash")
    lat_low, lat_high = -90.0, 90.0
    lon_low, lon_high = -180.0, 180.0
    even = True
    for char in code.lower():
        try:
            value = _GEOHASH_INDEX[char]
        except KeyError:
            raise ValueError(f"'{char}' is not a geohash character") from None
        for mask in _BITS:
            bit_set = bool(value & mask)
            if even:
                middle = (lon_low + lon_high) / 2
                if bit_set:
                    lon_low = middle
                else:
                    lon_high = middle
            else:
                middle = (lat_low + lat_high) / 2
                if bit_set:
                    lat_low = middle
                else:
                    lat_high = middle
            even = not even
    return lat_low, lon_low, lat_high, lon_high


def geohash_decode(code: str) -> tuple:
    """``(latitude, longitude)`` of the cell centre."""
    south, west, north, east = geohash_decode_bbox(code)
    return (south + north) / 2, (west + east) / 2


def geohash_divisions(precision: int) -> tuple:
    """``(latitude cells, longitude cells)`` covering the globe at ``precision``."""
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
_OLC_GRID_LENGTH = _OLC_MAX_DIGITS - _OLC_PAIR_LENGTH
_OLC_GRID_ROWS = 5
_OLC_GRID_COLUMNS = 4
_OLC_PAIR_PRECISION = _OLC_BASE ** 3
_OLC_LAT_PRECISION = _OLC_PAIR_PRECISION * _OLC_GRID_ROWS ** _OLC_GRID_LENGTH
_OLC_LON_PRECISION = _OLC_PAIR_PRECISION * _OLC_GRID_COLUMNS ** _OLC_GRID_LENGTH
_OLC_PAIR_FIRST_PLACE = _OLC_BASE ** (_OLC_PAIR_LENGTH // 2 - 1)


def _clip_latitude(latitude: float) -> float:
    return min(90.0, max(-90.0, latitude))


def _normalize_longitude(longitude: float) -> float:
    while longitude < -180:
        longitude += 360
    while longitude >= 180:
        longitude -= 360
    return longitude


def olc_encode(latitude: float, longitude: float, code_length: int = 10) -> str:
    """The Open Location Code of a point, ``code_length`` digits long."""
    if code_length < 2 or (code_length < _OLC_PAIR_LENGTH and code_length % 2 == 1):
        raise ValueError(f"invalid Open Location Code length: {code_length}")
    code_length = min(code_length, _OLC_MAX_DIGITS)
    latitude = _clip_latitude(latitude)
    longitude = _normalize_longitude(longitude)


    if latitude == 90:
        latitude -= olc_cell_size(code_length)[0]



    lat_value = int(round((latitude + 90) * _OLC_LAT_PRECISION, 6))
    lon_value = int(round((longitude + 180) * _OLC_LON_PRECISION, 6))

    code = ""
    if code_length > _OLC_PAIR_LENGTH:
        for _ in range(_OLC_GRID_LENGTH):
            index = (lat_value % _OLC_GRID_ROWS) * _OLC_GRID_COLUMNS + (lon_value % _OLC_GRID_COLUMNS)
            code = OLC_ALPHABET[index] + code
            lat_value //= _OLC_GRID_ROWS
            lon_value //= _OLC_GRID_COLUMNS
    else:
        lat_value //= _OLC_GRID_ROWS ** _OLC_GRID_LENGTH
        lon_value //= _OLC_GRID_COLUMNS ** _OLC_GRID_LENGTH

    for _ in range(_OLC_PAIR_LENGTH // 2):
        code = OLC_ALPHABET[lon_value % _OLC_BASE] + code
        code = OLC_ALPHABET[lat_value % _OLC_BASE] + code
        lat_value //= _OLC_BASE
        lon_value //= _OLC_BASE

    code = code[:OLC_SEPARATOR_POSITION] + OLC_SEPARATOR + code[OLC_SEPARATOR_POSITION:]
    if code_length >= OLC_SEPARATOR_POSITION:
        return code[:code_length + 1]
    return code[:code_length] + OLC_PADDING * (OLC_SEPARATOR_POSITION - code_length) + OLC_SEPARATOR


def _olc_clean(code: str) -> str:
    """The digits of a full Open Location Code, validated the way the spec says."""









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
    """``(south, west, north, east)`` of an Open Location Code cell."""
    cleaned = _olc_clean(code)

    lat_value = -90 * _OLC_PAIR_PRECISION
    lon_value = -180 * _OLC_PAIR_PRECISION
    grid_lat = 0
    grid_lon = 0

    digits = min(len(cleaned), _OLC_PAIR_LENGTH)
    place = _OLC_PAIR_FIRST_PLACE
    for index in range(0, digits, 2):
        lat_value += OLC_ALPHABET.index(cleaned[index]) * place
        lon_value += OLC_ALPHABET.index(cleaned[index + 1]) * place
        if index < digits - 2:
            place //= _OLC_BASE
    lat_size = place / _OLC_PAIR_PRECISION
    lon_size = place / _OLC_PAIR_PRECISION

    if len(cleaned) > _OLC_PAIR_LENGTH:
        row_place = _OLC_GRID_ROWS ** (_OLC_GRID_LENGTH - 1)
        column_place = _OLC_GRID_COLUMNS ** (_OLC_GRID_LENGTH - 1)
        digits = min(len(cleaned), _OLC_MAX_DIGITS)
        for index in range(_OLC_PAIR_LENGTH, digits):
            value = OLC_ALPHABET.index(cleaned[index])
            grid_lat += (value // _OLC_GRID_COLUMNS) * row_place
            grid_lon += (value % _OLC_GRID_COLUMNS) * column_place
            if index < digits - 1:
                row_place //= _OLC_GRID_ROWS
                column_place //= _OLC_GRID_COLUMNS
        lat_size = row_place / _OLC_LAT_PRECISION
        lon_size = column_place / _OLC_LON_PRECISION

    south = lat_value / _OLC_PAIR_PRECISION + grid_lat / _OLC_LAT_PRECISION
    west = lon_value / _OLC_PAIR_PRECISION + grid_lon / _OLC_LON_PRECISION
    return south, west, south + lat_size, west + lon_size


def olc_decode(code: str) -> tuple:
    """``(latitude, longitude)`` of the cell centre."""
    south, west, north, east = olc_decode_bbox(code)
    return (south + north) / 2, (west + east) / 2


def olc_code_length(code: str) -> int:
    return len(_olc_clean(code))


def olc_cell_size(code_length: int) -> tuple:
    """``(latitude height, longitude width)`` in degrees at ``code_length``."""
    lat_cells, lon_cells = olc_divisions(code_length)
    return 180.0 / lat_cells, 360.0 / lon_cells


def olc_divisions(code_length: int) -> tuple:
    """``(latitude cells, longitude cells)`` covering the globe at ``code_length``."""




    pairs = min(code_length, _OLC_PAIR_LENGTH) // 2
    lat_cells = 9 * _OLC_BASE ** (pairs - 1)
    lon_cells = 18 * _OLC_BASE ** (pairs - 1)
    if code_length > _OLC_PAIR_LENGTH:
        extra = code_length - _OLC_PAIR_LENGTH
        lat_cells *= _OLC_GRID_ROWS ** extra
        lon_cells *= _OLC_GRID_COLUMNS ** extra
    return lat_cells, lon_cells





def _optional_module(feature: str):
    """The imported module for an optional feature, or None when it is absent."""
    spec = OPTIONAL_DEPENDENCIES.get(feature) or {}
    import_name = spec.get("import_name", feature)
    try:
        if importlib.util.find_spec(import_name) is None:
            return None
        return importlib.import_module(import_name)
    except Exception:  # noqa: BLE001 - a broken install is a missing package here
        return None


def _missing_dependency_error(feature: str) -> dict:
    spec = OPTIONAL_DEPENDENCIES.get(feature) or {}
    package = spec.get("package", feature)
    return tool_error(
        f"The '{package}' package is not installed, so {feature.upper()} grids are unavailable. "
        f"Geohash and Open Location Code need no package and work now.",
        code="INVALID_ARGS",
        suggestion=(
            f"Call install_dependency {{feature:'{feature}'}}, then reload the plugin. "
            f"Or use system:'geohash' or system:'olc', which need nothing installed."
        ),
    )


def _h3_call(module, names: tuple, *args, **kwargs):
    """Call the first of ``names`` the installed h3 exposes (v4 then v3 spelling)."""
    for name in names:
        function = getattr(module, name, None)
        if function is not None:
            return function(*args, **kwargs)
    raise RuntimeError(f"This h3 version exposes none of {names}. Upgrade the h3 package.")





def _validate_resolution(system: str, resolution) -> tuple:
    """``(resolution, None)`` or ``(None, error dict)``."""
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
    """Planar area of a ``(west, south, east, north)`` box, GeoLibre's estimate."""
    west, south, east, north = bbox
    mid_latitude = math.radians((south + north) / 2)
    lon_span = east - west
    if lon_span < 0:
        lon_span += 360
    return abs(north - south) * _KM_PER_DEG_LAT * lon_span * _KM_PER_DEG_LON_EQ * math.cos(mid_latitude)


def _average_cell_area_km2(system: str, resolution: int, module=None) -> float:
    """Mean cell area, used only to pick a resolution and to warn before building."""
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
    """The finest resolution whose estimated count stays under ``target``."""
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
    """A closed polygon from ``(longitude, latitude)`` pairs."""
    ring = list(points)
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    coordinates = ", ".join(f"{lon:.10f} {lat:.10f}" for lon, lat in ring)
    return f"POLYGON(({coordinates}))"


def _estimate_cells(system: str, bbox: tuple, resolution: int, module=None) -> float:
    """How many cells the extent will hold, before any are built."""
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
    """``(cells, None)`` for geohash/olc, or ``(None, error dict)`` past the limit."""





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
    """The Stop check of the task this worker runs in, or None outside one."""
    try:
        from ..core import net

        return net.current_cancel_check()
    except Exception:  # noqa: BLE001 - no task, no cancellation
        return None


def _too_many_cells(system: str, resolution: int, count, limit: int) -> dict:
    readable = int(count) if count != float("inf") else "more than can be counted"
    return tool_error(
        f"{system} resolution {resolution} needs about {readable} cells over this extent, "
        f"above the {limit} limit.",
        code="INVALID_ARGS",
        suggestion=(
            "Use a coarser resolution, a smaller extent, or raise max_cells "
            f"(hard cap {HARD_MAX_CELLS})."
        ),
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
    """``(bbox tuple, None)`` or ``(None, error dict)`` for a bbox argument."""
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
                code="INVALID_ARGS",
                suggestion="Ask for the two halves separately: west..180 and -180..east.",
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
    """True when west>east reads as a box across the antimeridian, not an inverted pair."""






    if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0):
        return False
    wrapped = (east + 360.0) - west
    inverted = west - east
    return wrapped < inverted


def _layer_bbox_4326(layer_name: str):
    """The layer's extent as ``[west, south, east, north]``. Main thread only."""
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
    """The canvas extent as ``[west, south, east, north]``. Main thread only."""
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
    """``(bbox, source, None)`` or ``(None, None, error dict)``."""
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
            code="INVALID_ARGS",
            suggestion="Pass bbox {west, south, east, north} or layer.",
        )
    bbox, error = _read_bbox(raw)
    return (None, None, error) if error else (bbox, "canvas", None)





def detect_system(cell_id: str) -> str | None:
    """The grid system a cell id belongs to, or None when it is not decidable."""





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
        except Exception:  # noqa: BLE001  # nosec B110 - an unparseable id is simply not h3
            pass
    s2_module = _optional_module("s2")
    if s2_module is not None:
        try:
            if s2_module.CellId.from_token(lowered).is_valid():
                candidates.append("s2")
        except Exception:  # noqa: BLE001  # nosec B110 - an unparseable token is simply not s2
            pass
    if len(candidates) == 1:
        return candidates[0]


    return None


def _describe_cell(system: str, cell_id: str) -> dict:
    """Centre, bounds and footprint of one cell. Raises on an invalid id."""
    if system == "geohash":
        south, west, north, east = geohash_decode_bbox(cell_id)
        resolution = len(cell_id)
        wkt = _rect_wkt(south, west, north, east)
    elif system == "olc":
        south, west, north, east = olc_decode_bbox(cell_id)
        resolution = olc_code_length(cell_id)
        wkt = _rect_wkt(south, west, north, east)
    elif system == "h3":
        module = _optional_module("h3")
        boundary = _h3_call(module, ("cell_to_boundary", "h3_to_geo_boundary"), cell_id)
        centre = _h3_call(module, ("cell_to_latlng", "h3_to_geo"), cell_id)
        resolution = int(_h3_call(module, ("get_resolution", "h3_get_resolution"), cell_id))
        latitudes = [point[0] for point in boundary]
        longitudes = [point[1] for point in boundary]
        south, north = min(latitudes), max(latitudes)
        west, east = min(longitudes), max(longitudes)
        wkt = _ring_wkt([(lon, lat) for lat, lon in boundary])
        return {
            "cell_id": str(cell_id), "system": system, "resolution": resolution,
            "center_lat": float(centre[0]), "center_lon": float(centre[1]),
            "bbox": {"west": west, "south": south, "east": east, "north": north}, "wkt": wkt,
        }
    else:
        module = _optional_module("s2")
        record = _s2_cell_record(module, module.CellId.from_token(cell_id))
        cell = module.Cell(module.CellId.from_token(cell_id))
        rect = cell.get_rect_bound()
        return {
            "cell_id": record["cell_id"], "system": system, "resolution": cell.level(),
            "center_lat": record["center_lat"], "center_lon": record["center_lon"],
            "bbox": {
                "west": rect.lng_lo().degrees, "south": rect.lat_lo().degrees,
                "east": rect.lng_hi().degrees, "north": rect.lat_hi().degrees,
            },
            "wkt": record["wkt"],
        }

    return {
        "cell_id": cell_id, "system": system, "resolution": resolution,
        "center_lat": (south + north) / 2, "center_lon": (west + east) / 2,
        "bbox": {"west": west, "south": south, "east": east, "north": north},
        "wkt": wkt,
    }




def register_grid_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="create_grid_layer",
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

    registry.register(Tool(
        name="encode_cells",
        input_schema={
            "type": "object",
            "properties": {
                "layer": {"type": "string"},
                "system": {
                    "type": "string",
                    "enum": list(SYSTEMS),
                },
                "resolution": {
                    "type": "integer",
                },
                "field_name": {
                    "type": "string",
                },
            },
            "required": ["layer", "system", "resolution"],
        },
        handler=_encode_cells,
        background=True,
    ))

    registry.register(Tool(
        name="decode_cell",
        input_schema={
            "type": "object",
            "properties": {
                "cell_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "system": {
                    "type": "string",
                    "enum": list(SYSTEMS),
                },
            },
            "required": ["cell_ids"],
        },
        handler=_decode_cell,
        background=True,
    ))

    log("Grid tools registered (geohash, olc, h3, s2)")





def _check_system(args: dict) -> tuple:
    """``(system, module, None)`` or ``(None, None, error dict)``."""
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
    except Exception as exc:  # noqa: BLE001 - a package failure is a result, not a crash
        return tool_error(f"Building {system} cells failed: {exc}", code="GRID_FAILED")
    if error:
        return error
    if not cells:
        return tool_error(
            f"No {system} cell falls in this extent.", code="INVALID_ARGS",
            suggestion="Widen the extent or use a coarser resolution.",
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
    """Create the memory layer and fill it. Main thread only."""
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
    for cell in cells:
        feature = QgsFeature(layer.fields())
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


def _encode_cells(args: dict) -> dict:
    system, module, error = _check_system(args)
    if error:
        return error

    resolution, error = _validate_resolution(system, args.get("resolution"))
    if error:
        return error

    layer_name = args.get("layer")
    if not layer_name:
        return tool_error("layer is required", code="BAD_ARGUMENT")

    read = _run_on_main_thread(_read_layer_points, layer_name, timeout=120)
    if isinstance(read, dict) and read.get("_error"):
        return read
    layer_id = read["layer_id"]
    points = read["points"]

    field_name = (args.get("field_name") or f"{system}_{resolution}").strip()
    values = {}
    skipped = 0
    try:
        for feature_id, longitude, latitude in points:
            if longitude is None or latitude is None:
                skipped += 1
                continue
            values[feature_id] = _encode_point(system, module, latitude, longitude, resolution)
    except Exception as exc:  # noqa: BLE001 - a package failure is a result, not a crash
        return tool_error(f"Encoding to {system} failed: {exc}", code="ENCODE_FAILED")

    written = _run_on_main_thread(_write_cell_field, layer_id, field_name, values, timeout=120)
    if isinstance(written, dict) and written.get("_error"):
        return written

    sample = [values[key] for key in list(values)[:5]]
    out = {
        "layer": written["layer"],
        "field": field_name,
        "system": system,
        "resolution": resolution,
        "features_tagged": written["updated"],
        "features_without_geometry": skipped,
        "distinct_cells": len(set(values.values())),
        "sample_cell_ids": sample,


        "point_method": "centroid",
        "_next": "Aggregate on this field (get_field_statistics, or run_processing 'native:aggregate').",
    }
    if read.get("out_of_domain"):
        out["features_out_of_domain"] = read["out_of_domain"]
    if written.get("refused"):
        out["refused"] = written["refused"]
        out["refused_count"] = written["refused_count"]
        out["partial"] = True
    return out


def _encode_point(system: str, module, latitude: float, longitude: float, resolution: int) -> str:
    if system == "geohash":
        return geohash_encode(latitude, longitude, resolution)
    if system == "olc":
        return olc_encode(latitude, longitude, resolution)
    if system == "h3":
        return str(_h3_call(module, ("latlng_to_cell", "geo_to_h3"), latitude, longitude, resolution))
    cell_id = module.CellId.from_lat_lng(module.LatLng.from_degrees(latitude, longitude))
    return cell_id.parent(resolution).to_token()


def _read_layer_points(layer_name: str):
    """``[(feature id, longitude, latitude), ...]`` in EPSG:4326. Main thread only."""
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsCoordinateTransform,
        QgsProject,
        QgsVectorLayer,
    )

    from .core_tools import _find_layer

    layer = _find_layer(layer_name)
    if layer is None:
        return {"_error": f"Layer '{layer_name}' not found.", "code": "LAYER_NOT_FOUND"}
    if not isinstance(layer, QgsVectorLayer):
        return {"_error": f"Layer '{layer_name}' is not a vector layer", "code": "INVALID_ARGS"}

    count = layer.featureCount()
    if count is not None and count > _MAX_ENCODE_FEATURES:
        return {
            "_error": f"Layer '{layer_name}' has {count} features, above the {_MAX_ENCODE_FEATURES} this tool "
                      "encodes on the main thread.",
            "code": "INVALID_ARGS",


            "suggestion": "Set a filter on the layer so it holds fewer features, then encode it.",
        }

    transform = None
    crs = layer.crs()
    if not crs.isValid():




        return {
            "_error": f"Layer '{layer.name()}' has no valid CRS, so its coordinates cannot be read as "
                      f"longitude and latitude.",
            "code": "INVALID_ARGS",
            "suggestion": "Set the layer CRS in Layer Properties > Source, then encode it again.",
        }
    if crs.authid() != "EPSG:4326":
        transform = QgsCoordinateTransform(
            crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance()
        )

    points = []
    out_of_domain = 0
    for feature in layer.getFeatures():
        geometry = feature.geometry()
        if geometry is None or geometry.isEmpty():
            points.append((feature.id(), None, None))
            continue
        centroid = geometry.centroid()
        if centroid.isEmpty():
            points.append((feature.id(), None, None))
            continue
        point = centroid.asPoint()
        if transform is not None:
            try:
                point = transform.transform(point)
            except Exception:  # noqa: BLE001 - a point the operation cannot carry is not a coordinate
                points.append((feature.id(), None, None))
                out_of_domain += 1
                continue
        longitude, latitude = point.x(), point.y()


        if not (math.isfinite(longitude) and math.isfinite(latitude)
                and -180.0 <= longitude <= 180.0 and -90.0 <= latitude <= 90.0):
            points.append((feature.id(), None, None))
            out_of_domain += 1
            continue
        points.append((feature.id(), longitude, latitude))



    return {"layer_id": layer.id(), "points": points, "out_of_domain": out_of_domain}


def _write_cell_field(layer_id: str, field_name: str, values: dict):
    """Add the field if needed and write one id per feature."""





    from qgis.core import QgsField, QgsProject, QgsVectorLayer

    from ._compat import QVAR_STRING

    layer = QgsProject.instance().mapLayer(layer_id)
    if layer is None:
        return {"_error": "The layer the cells were computed for is no longer in the project, so nothing "
                          "was written.",
                "code": "LAYER_NOT_FOUND",
                "suggestion": "Run the encoding again on the layer that is open now."}
    if not isinstance(layer, QgsVectorLayer):
        return {"_error": f"Layer '{layer.name()}' is not a vector layer", "code": "INVALID_ARGS"}

    count = layer.featureCount()
    if count is not None and count > _MAX_ENCODE_FEATURES:
        return {
            "_error": f"Layer '{layer.name()}' has {count} features, above the {_MAX_ENCODE_FEATURES} this tool "
                      "writes on the main thread.",
            "code": "INVALID_ARGS",
            "suggestion": "Set a filter on the layer so it holds fewer features, then encode it.",
        }

    longest = max((len(str(v)) for v in values.values()), default=0)
    existing = layer.fields().indexOf(field_name)
    if existing >= 0:



        field = layer.fields().at(existing)
        problem = _cell_field_problem(field, longest)
        if problem:
            return {"_error": f"Field {field_name!r} on '{layer.name()}' {problem}.",
                    "code": "INVALID_ARGS",
                    "suggestion": f"Pass another field_name; the ids are up to {longest} characters of text."}

    was_editing = layer.isEditable()
    if not was_editing and not layer.startEditing():
        return {"_error": f"Cannot start editing on layer '{layer.name()}'", "code": "INVALID_ARGS"}

    if existing < 0:


        layer.addAttribute(QgsField(field_name, QVAR_STRING, "", max(32, longest)))
        layer.updateFields()
    index = layer.fields().indexOf(field_name)
    if index < 0:
        if not was_editing:
            layer.rollBack()
        return {
            "_error": f"Provider refused to add field '{field_name}' (read-only source?).",
            "code": "INVALID_ARGS",
        }

    updated = 0
    refused = []
    for feature_id, cell_id in values.items():
        if layer.changeAttributeValue(feature_id, index, cell_id):
            updated += 1
        else:


            refused.append(feature_id)

    if not was_editing and not layer.commitChanges():
        errors = "; ".join(layer.commitErrors()[:3])
        layer.rollBack()
        return {"_error": f"Could not commit the cell ids: {errors}", "code": "COMMIT_FAILED"}

    out = {"layer": layer.name(), "updated": updated}
    if refused:


        out["refused"] = refused[:20]
        out["refused_count"] = len(refused)
    return out


def _cell_field_problem(field, longest: int) -> str:
    """Why an existing field cannot hold these cell ids, or "" when it can."""
    from ._compat import QVAR_STRING

    try:
        if field.type() != QVAR_STRING:
            return f"is {field.typeName() or field.type()}, and a cell id is text"
        length = int(field.length() or 0)
        if 0 < length < longest:
            return f"holds {length} characters and the ids need {longest}"
    except Exception:  # noqa: BLE001 - a provider that cannot describe the field is taken at its word
        return ""
    return ""





_MAX_ID_CHARS_BY_SYSTEM = {
    "geohash": GEOHASH_MAX_PRECISION,
    "olc": _OLC_MAX_DIGITS + 1,
    "h3": 16,
    "s2": 16,
}


_MAX_CELL_ID_CHARS = max(_MAX_ID_CHARS_BY_SYSTEM.values())


def _decode_cell(args: dict) -> dict:
    raw = args.get("cell_ids")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        return tool_error("cell_ids must be a non-empty list of strings", code="BAD_ARGUMENT")
    if len(raw) > 500:
        return tool_error(
            f"cell_ids holds {len(raw)} ids, at most 500 per call.", code="INVALID_ARGS",
            suggestion="Split the list, or use create_grid_layer for a whole extent.",
        )

    forced = (args.get("system") or "").strip().lower() or None
    if forced and forced not in SYSTEMS:
        return tool_error(f"system must be one of {list(SYSTEMS)}, got {args['system']!r}", code="BAD_SYSTEM")
    if forced in ("h3", "s2") and _optional_module(forced) is None:
        return _missing_dependency_error(forced)

    decoded = []
    failed = []
    for cell_id in raw:
        code = str(cell_id).strip()





        if len(code) > _MAX_CELL_ID_CHARS:
            failed.append({"cell_id": code[:32] + "...",
                           "reason": f"a cell id is at most {_MAX_CELL_ID_CHARS} characters"})
            continue
        system = forced or detect_system(code)
        if system is None:
            failed.append({"cell_id": code, "reason": "system not recognised, pass 'system'"})
            continue
        longest = _MAX_ID_CHARS_BY_SYSTEM.get(system)
        if longest is not None and len(code) > longest:
            failed.append({"cell_id": code[:32] + ("..." if len(code) > 32 else ""),
                           "reason": f"a {system} id is at most {longest} characters"})
            continue
        if system in ("h3", "s2") and _optional_module(system) is None:
            return _missing_dependency_error(system)
        try:
            decoded.append(_describe_cell(system, code))
        except Exception as exc:  # noqa: BLE001 - one bad id must not lose the rest
            failed.append({"cell_id": code, "reason": str(exc)})

    result = {"cells": decoded, "decoded": len(decoded)}
    if failed:
        result["failed"] = failed
        result["suggestion"] = "Pass 'system' explicitly for ids the detector could not place."
    return result
