# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


























from __future__ import annotations

import math
import re



UNIT_SAMPLE = 50






UNIT_TOLERANCE = 3.0


RASTER_SAMPLE = 250_000


SUM_MAX_FEATURES = 2000
AOI_MAX_FEATURES = 200


AOI_TOLERANCE = 0.01


ZONE_TOLERANCE = 0.1
ZONE_MIN_PIXELS = 50



_AREA_UNITS = {
    "km2": 1e6, "sqkm": 1e6, "kmsq": 1e6, "skm": 1e6,
    "ha": 1e4, "hectare": 1e4, "hectares": 1e4,
    "m2": 1.0, "sqm": 1.0, "msq": 1.0,
    "acre": 4046.8564224, "acres": 4046.8564224,
    "mi2": 2589988.110336, "sqmi": 2589988.110336,
    "ft2": 0.09290304, "sqft": 0.09290304,
}
_LENGTH_UNITS = {"km": 1000.0, "m": 1.0, "mi": 1609.344, "ft": 0.3048}



_AREA_WORDS = ("area", "surface", "surf", "superficie", "sup", "aire", "flaeche", "flache")
_LENGTH_WORDS = ("length", "len", "longueur", "laenge", "lange")
_PERIMETER_WORDS = ("perimeter", "perimetre", "perim")
_NOT_A_MEASURE = frozenset({"per", "dens", "density", "densite", "pop", "hab", "inhab", "ratio", "pct",
                            "percent", "share", "rate", "par", "by"})
_SPLIT_RE = re.compile(r"[^a-z0-9]+")
_CAMEL_RE = re.compile(r"([a-z0-9])([A-Z])")


def _tokens(name: str) -> list[str]:
    text = _CAMEL_RE.sub(r"\1_\2", str(name or "")).replace("²", "2").lower()
    return [token for token in _SPLIT_RE.split(text) if token]


def _split_word(token: str, words: tuple, units: dict) -> str | None:

    for word in words:
        if token.startswith(word) and token[len(word):] in units:
            return token[len(word):]
    return None


def field_unit(field_name: str, geometry: str) -> tuple[str, str, float] | None:






    tokens = _tokens(field_name)
    if not tokens or _NOT_A_MEASURE.intersection(tokens):
        return None
    kind = str(geometry or "").lower()
    if kind.startswith("polygon"):
        for token in tokens:
            unit = token if token in _AREA_UNITS else _split_word(token, _AREA_WORDS, _AREA_UNITS)
            if unit is None:
                continue
            others = [t for t in tokens if t != token]


            if any(not (t.startswith(_AREA_WORDS) or t.isdigit()) for t in others):
                continue
            if token != unit or not others or any(t.startswith(_AREA_WORDS) for t in others):
                return "area", unit, _AREA_UNITS[unit]
        measure, words = "perimeter", _PERIMETER_WORDS
    elif kind.startswith("line"):
        measure, words = "length", _LENGTH_WORDS
    else:
        return None
    for token in tokens:
        unit = token if token in _LENGTH_UNITS else _split_word(token, words, _LENGTH_UNITS)
        if unit is None:
            continue
        others = [t for t in tokens if t != token]
        if any(not (t.startswith(words) or t.isdigit()) for t in others):
            continue
        if token != unit or any(t.startswith(words) for t in others):
            return measure, unit, _LENGTH_UNITS[unit]
    return None


def _geometry_kind(layer) -> str:
    try:
        from qgis.core import QgsWkbTypes

        return str(QgsWkbTypes.geometryDisplayString(layer.geometryType()))
    except Exception:  # noqa: BLE001
        return ""




_DEGREE_METRES = 111319.49079327357


def metres_per_map_unit(crs, degrees: bool = False) -> float | None:


    try:
        from qgis.core import Qgis, QgsUnitTypes

        from .qt_compat import enum_member

        if not crs.isValid():
            return None
        if crs.isGeographic():
            return _DEGREE_METRES if degrees else None
        metres = enum_member(Qgis, "DistanceUnit", "Meters", None)
        if metres is None:
            metres = enum_member(QgsUnitTypes, "DistanceUnit", "DistanceMeters")
        factor = float(QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), metres))
    except Exception:  # noqa: BLE001
        return None
    return factor if factor > 0 else None


def _ellipsoid(layer):

    try:
        from qgis.core import QgsDistanceArea, QgsProject

        project = QgsProject.instance()
        measure = QgsDistanceArea()
        measure.setSourceCrs(layer.crs(), project.transformContext())
        ellipsoid = str(project.ellipsoid() or "")
        measure.setEllipsoid(ellipsoid if ellipsoid and ellipsoid.upper() != "NONE" else "EPSG:7030")
        return measure if measure.willUseEllipsoid() else None
    except Exception:  # noqa: BLE001
        return None


def _measures(geometry, measure: str, ellipsoid, planar_factor) -> tuple:

    def kept(value):
        return value if value is not None and value > 0 and math.isfinite(value) else None

    try:
        on_ellipsoid = planar = None
        if ellipsoid is not None:
            if measure == "area":
                on_ellipsoid = float(ellipsoid.measureArea(geometry))
            elif measure == "length":
                on_ellipsoid = float(ellipsoid.measureLength(geometry))
            else:
                on_ellipsoid = float(ellipsoid.measurePerimeter(geometry))
        if planar_factor:
            if measure == "area":
                planar = float(geometry.area()) * planar_factor * planar_factor
            elif measure == "length":
                planar = float(geometry.length()) * planar_factor
            else:
                planar = float(geometry.constGet().perimeter()) * planar_factor
    except Exception:  # noqa: BLE001
        return None, None
    return kept(on_ellipsoid), kept(planar)


def _median(values: list) -> float | None:
    if not values:
        return None
    values = sorted(values)
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else math.sqrt(values[middle - 1] * values[middle])


def _number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _times(ratio: float) -> str:

    exponent = round(math.log10(ratio))
    if exponent and abs(math.log10(ratio) - exponent) < 0.05:
        ratio = 10.0 ** exponent
    if ratio >= 1:
        return f"{ratio:,.0f} times" if ratio >= 10 else f"{ratio:.1f} times"
    inverse = 1 / ratio
    return f"1/{inverse:,.0f} of" if inverse >= 10 else f"1/{inverse:.1f} of"


def unit_factor(layer, field_name: str) -> dict | None:











    try:
        from qgis.core import QgsFeatureRequest

        from .geometry_budget import VertexBudget

        promised = field_unit(field_name, _geometry_kind(layer))
        if promised is None:
            return None
        measure, unit, per_unit = promised
        index = layer.fields().indexOf(field_name)
        if index < 0:
            return None
        ellipsoid = _ellipsoid(layer)


        planar = metres_per_map_unit(layer.crs(), degrees=True)
        if ellipsoid is None and not planar:
            return None
        budget = VertexBudget()
        request = QgsFeatureRequest().setLimit(UNIT_SAMPLE)
        request.setSubsetOfAttributes([index])

        ratios: tuple[list[float], list[float]] = ([], [])
        read = 0
        for feature in layer.getFeatures(request):
            if budget.exhausted():
                break
            read += 1
            value = _number(feature.attribute(index))
            geometry = feature.geometry()
            if not value or value < 0 or geometry is None or geometry.isNull() or budget.oversize(geometry):
                continue
            for kept, measured in zip(ratios, _measures(geometry, measure, ellipsoid, planar)):
                if measured:
                    kept.append(value * per_unit / measured)
    except Exception:  # noqa: BLE001
        return None
    sampled = max(len(ratios[0]), len(ratios[1]))
    if not sampled or sampled < min(3, read):
        return None
    medians = [median for median in (_median(ratios[0]), _median(ratios[1])) if median]

    agrees = any(1 / UNIT_TOLERANCE <= median <= UNIT_TOLERANCE for median in medians)
    out = {"field": field_name, "unit": unit, "measure": measure, "sampled": sampled,
           "median_ratio": float(f"{medians[0]:.4g}")}
    try:
        out["layer_id"] = str(layer.id())
    except Exception:  # noqa: BLE001  # nosec B110
        pass
    if agrees:
        return out
    out["warning"] = (f"Recompute {field_name!r}: its values are {_times(medians[0])} the {measure} in {unit} "
                      f"measured on each geometry ({sampled} sampled): a wrong unit factor.")
    return out


def unit_still_wrong(layer_id: str, field_name: str) -> bool | None:


    try:
        from qgis.core import QgsProject

        layer = QgsProject.instance().mapLayer(str(layer_id or ""))
        if layer is None or layer.fields().indexOf(field_name) < 0:
            return False
    except Exception:  # noqa: BLE001
        return None
    found = unit_factor(layer, field_name)
    if found is None:
        return None
    return "warning" in found







_SLOPE = frozenset()
_ASPECT = frozenset()

_CALCULATORS = {"native:rastercalc": "EXPRESSION", "qgis:rastercalculator": "EXPRESSION",
                "native:virtualrastercalc": "EXPRESSION", "gdal:rastercalculator": "FORMULA"}
_CASTS_RE = re.compile(r"(?:numpy\.|np\.)?(?:float32|float64|float|double)\(([^()]+)\)", re.IGNORECASE)
_ASTYPE_RE = re.compile(r"\.astype\([^()]*\)")
_OPERAND = r'"[^"]+"|[A-Za-z_][\w@.]*'
_NORMALISED_RE = re.compile(
    rf"\(\(?({_OPERAND})\)?-\(?({_OPERAND})\)?\)/\(\(?({_OPERAND})\)?\+\(?({_OPERAND})\)?\)")


def normalised_difference(expression) -> bool:


    text = re.sub(r"\s+", "", str(expression or ""))
    previous = None
    while previous != text:
        previous, text = text, _CASTS_RE.sub(r"\1", text)
    text = _ASTYPE_RE.sub("", text)
    for match in _NORMALISED_RE.finditer(text):
        a, b, c, d = match.groups()
        if a != b and {a, b} == {c, d}:
            return True
    return False


def _algs(key: str, shipped: frozenset) -> frozenset:

    try:
        from . import tuning
    except ImportError:
        return shipped
    return tuning.check_algs(key, shipped)


def expected_range(algorithm_id: str, parameters: dict) -> tuple[float, float, str] | None:

    algorithm_id = str(algorithm_id or "").lower()
    parameters = parameters if isinstance(parameters, dict) else {}
    if algorithm_id in _algs("slope_algorithms", _SLOPE):
        if str(parameters.get("AS_PERCENT")).strip().lower() in ("true", "1"):
            return None
        return 0.0, 90.0, "a slope in degrees"
    if algorithm_id in _algs("aspect_algorithms", _ASPECT):
        return 0.0, 360.0, "an aspect in degrees"
    key = _CALCULATORS.get(algorithm_id)
    if key and normalised_difference(parameters.get(key)):
        return -1.0, 1.0, "a normalised difference index"
    return None


def band_range(layer) -> tuple[float, float] | None:

    try:
        import warnings as _warnings

        from qgis.core import QgsRasterBandStats

        from .qt_compat import enum_member

        provider = layer.dataProvider()
        with _warnings.catch_warnings():


            _warnings.simplefilter("ignore", DeprecationWarning)
            wanted = (enum_member(QgsRasterBandStats, "Stats", "Min")
                      | enum_member(QgsRasterBandStats, "Stats", "Max"))
            stats = provider.bandStatistics(1, wanted, layer.extent(), RASTER_SAMPLE)
        low, high = float(stats.minimumValue), float(stats.maximumValue)
    except Exception:  # noqa: BLE001
        return None

    if low > high or not (math.isfinite(low) and math.isfinite(high)):
        return None
    return low, high


def index_range(layer, expected: tuple[float, float, str] | None) -> dict | None:

    if expected is None or layer is None:
        return None
    found = band_range(layer)
    if found is None:
        return None
    low, high = found
    floor, ceiling, what = expected
    slack = 1e-6 * max(1.0, ceiling - floor)
    out = {"low": float(f"{low:.6g}"), "high": float(f"{high:.6g}"), "expected": [floor, ceiling]}
    if low < floor - slack or high > ceiling + slack:
        out["warning"] = (f"values run from {low:.4g} to {high:.4g}, outside the {floor:g} to {ceiling:g} of "
                          f"{what}: check the bands and compute in floating point.")
    return out



_CLIPS_TO_OVERLAY = frozenset()
_ZONAL_HISTOGRAM = frozenset()


def _area_sum(layer, limit: int, budget) -> float | None:

    try:
        from qgis.core import QgsFeatureRequest

        count = int(layer.featureCount())
        if count < 0 or count > limit or not _geometry_kind(layer).lower().startswith("polygon"):
            return None
        measure = _ellipsoid(layer)
        if measure is None:
            return None
        total = 0.0
        for feature in layer.getFeatures(QgsFeatureRequest().setNoAttributes()):
            if budget.exhausted():
                return None
            geometry = feature.geometry()
            if geometry is None or geometry.isNull():
                continue
            if budget.oversize(geometry):
                return None
            total += float(measure.measureArea(geometry))
    except Exception:  # noqa: BLE001
        return None
    return total if math.isfinite(total) else None


def overlay_area(algorithm_id: str, output_layer, overlay_layer) -> dict | None:





    if (str(algorithm_id or "").lower() not in _algs("clips_to_overlay", _CLIPS_TO_OVERLAY)
            or output_layer is None or overlay_layer is None):
        return None
    try:
        from .geometry_budget import VertexBudget

        budget = VertexBudget()
        aoi = _area_sum(overlay_layer, AOI_MAX_FEATURES, budget)
        if not aoi:
            return None
        total = _area_sum(output_layer, SUM_MAX_FEATURES, budget)
        if total is None:
            return None
    except Exception:  # noqa: BLE001
        return None
    share = total / aoi
    out = {"output_m2": round(total, 1), "area_of_interest_m2": round(aoi, 1), "share": round(share, 4)}
    if share > 1 + AOI_TOLERANCE:
        name = overlay_layer.name() if hasattr(overlay_layer, "name") else "the overlay"
        out["warning"] = (f"its polygons cover {share:.0%} of {name!r}: they overlap, so class areas summed from it "
                          "count ground twice. Dissolve by class first.")
    return out


def zonal_coverage(algorithm_id: str, parameters: dict, output_layer) -> dict | None:






    if str(algorithm_id or "").lower() not in _algs("zonal_histogram", _ZONAL_HISTOGRAM) or output_layer is None:
        return None
    try:
        from qgis.core import QgsCoordinateTransform, QgsFeatureRequest, QgsProject

        from .geometry_budget import VertexBudget

        parameters = parameters if isinstance(parameters, dict) else {}
        raster = _raster_of(parameters.get("INPUT_RASTER"))
        if raster is None:
            return None
        per_metre = metres_per_map_unit(raster.crs())
        width, height = float(raster.rasterUnitsPerPixelX()), float(raster.rasterUnitsPerPixelY())
        if not per_metre or width <= 0 or height <= 0:
            return None
        pixel = width * height
        prefix = str(parameters.get("COLUMN_PREFIX") or "HISTO_")
        fields = output_layer.fields()
        columns = [i for i in range(fields.count()) if fields.at(i).name().startswith(prefix)]
        if not columns:
            return None
        transform = None
        if output_layer.crs() != raster.crs():
            transform = QgsCoordinateTransform(output_layer.crs(), raster.crs(), QgsProject.instance())
        budget = VertexBudget()
        request = QgsFeatureRequest().setLimit(AOI_MAX_FEATURES)
        request.setSubsetOfAttributes(columns)
        counted = zone = 0.0
        zones = 0
        for feature in output_layer.getFeatures(request):
            if budget.exhausted():
                break
            geometry = feature.geometry()
            if geometry is None or geometry.isNull() or budget.oversize(geometry):
                continue
            if transform is not None:
                geometry.transform(transform)
            area = float(geometry.area())
            if area < ZONE_MIN_PIXELS * pixel:
                continue
            pixels = sum(_number(feature.attribute(i)) or 0.0 for i in columns)
            counted += pixels * pixel
            zone += area
            zones += 1
    except Exception:  # noqa: BLE001
        return None
    if not zones or zone <= 0:
        return None
    share = counted / zone
    out = {"zones": zones, "share": round(share, 4)}
    if share < 1 - ZONE_TOLERANCE:
        out["warning"] = (f"the class pixels cover {share:.0%} of the zone area ({zones} zones): the raster misses "
                          "part of the zones, so class areas do not add up to them.")
    elif share > 1 + ZONE_TOLERANCE:
        out["warning"] = (f"the class pixels add up to {share:.0%} of the zone area ({zones} zones): check the "
                          "raster's CRS and pixel size before reporting areas.")
    return out


def _raster_of(value):

    try:
        from qgis.core import QgsProject

        if hasattr(value, "rasterUnitsPerPixelX"):
            return value
        project = QgsProject.instance()
        text = str(value or "")
        layer = project.mapLayer(text)
        if layer is None:
            matches = project.mapLayersByName(text)
            layer = matches[0] if len(matches) == 1 else None
        return layer if hasattr(layer, "rasterUnitsPerPixelX") else None
    except Exception:  # noqa: BLE001
        return None



_CLIP_VECTOR = frozenset()
_CLIP_RASTER = frozenset()


def clip_extent(algorithm_id: str, parameters: dict, output_layer, overlay_layer) -> dict | None:







    algorithm_id = str(algorithm_id or "").lower()
    parameters = parameters if isinstance(parameters, dict) else {}
    if output_layer is None or overlay_layer is None:
        return None
    if algorithm_id in _algs("clip_raster", _CLIP_RASTER):
        if str(parameters.get("CROP_TO_CUTLINE", True)).strip().lower() in ("false", "0"):
            return None
    elif algorithm_id not in _algs("clip_vector", _CLIP_VECTOR):
        return None
    try:
        from qgis.core import QgsCoordinateTransform, QgsProject

        out_extent = output_layer.extent()
        clip = overlay_layer.extent()
        if out_extent.isEmpty() or clip.isEmpty():
            return None
        if overlay_layer.crs() != output_layer.crs() and overlay_layer.crs().isValid() and output_layer.crs().isValid():
            clip = QgsCoordinateTransform(overlay_layer.crs(), output_layer.crs(),
                                          QgsProject.instance()).transformBoundingBox(clip)
        slack = 0.01 * max(clip.width(), clip.height())
        if hasattr(output_layer, "rasterUnitsPerPixelX"):
            slack += 1.5 * max(float(output_layer.rasterUnitsPerPixelX()), float(output_layer.rasterUnitsPerPixelY()))
        clip.grow(slack)
        inside = bool(clip.contains(out_extent))
    except Exception:  # noqa: BLE001
        return None
    out = {"inside_clip_extent": inside}
    if not inside:
        name = overlay_layer.name() if hasattr(overlay_layer, "name") else "the clip layer"
        out["warning"] = (f"it extends beyond {name!r}, so it was not clipped: run the clip again for this "
                          "layer alone.")
    return out




_ONE_TO_ONE = {"native:joinattributestable": ("1",), "qgis:joinattributestable": ("1",),
               "native:joinattributesbylocation": ("1", "2"), "qgis:joinattributesbylocation": ("1", "2")}


def join_rows(algorithm_id: str, parameters: dict, features_in, features_out) -> str | None:

    algorithm_id = str(algorithm_id or "").lower()
    parameters = parameters if isinstance(parameters, dict) else {}
    if not isinstance(features_in, int) or not isinstance(features_out, int) or features_in <= 0:
        return None
    if algorithm_id in ("native:joinbynearest", "qgis:joinbynearest"):

        if str(parameters.get("NEIGHBORS", 1)).strip() not in ("", "1", "None"):
            return None
        one_to_one = True
    else:
        methods = _ONE_TO_ONE.get(algorithm_id)
        if methods is None:
            return None
        default = "1" if "attributestable" in algorithm_id else "0"
        method = str(parameters.get("METHOD", default)).strip()
        one_to_one = (method if method not in ("None", "") else default) in methods
    if features_out > features_in:
        return (f"the join returned {features_out} rows from {features_in} features: matched features are "
                "repeated, so totals over it count them more than once.")
    discards = str(parameters.get("DISCARD_NONMATCHING")).strip().lower() in ("true", "1")
    if one_to_one and not discards and features_out < features_in:
        return (f"a one-to-one join kept {features_out} of {features_in} features: rows were lost, so counts "
                "and totals over it are short.")
    return None
