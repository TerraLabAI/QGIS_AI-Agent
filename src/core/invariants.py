# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


























from __future__ import annotations

import math
import re

from .numbers import as_finite



UNIT_SAMPLE = 50







RASTER_SAMPLE = 250_000


SUM_MAX_FEATURES = 2000
AOI_MAX_FEATURES = 200


def _rules() -> dict:

    try:
        from . import tool_rules
    except ImportError:
        return {}
    return tool_rules.section("invariants")


def _words(key: str) -> tuple:
    return tuple(_rules().get(key) or ())


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






    rules = _rules()
    area_units, length_units = rules.get("area_units") or {}, rules.get("length_units") or {}
    area_words, length_words, perimeter_words = _words("area_words"), _words("length_words"), _words("perimeter_words")
    tokens = _tokens(field_name)
    if not tokens or not area_units or not length_units or set(_words("not_a_measure")).intersection(tokens):
        return None
    kind = str(geometry or "").lower()
    if kind.startswith("polygon"):
        for token in tokens:
            unit = token if token in area_units else _split_word(token, area_words, area_units)
            if unit is None:
                continue
            others = [t for t in tokens if t != token]


            if any(not (t.startswith(area_words) or t.isdigit()) for t in others):
                continue
            if token != unit or not others or any(t.startswith(area_words) for t in others):
                return "area", unit, area_units[unit]
        measure, words = "perimeter", perimeter_words
    elif kind.startswith("line"):
        measure, words = "length", length_words
    else:
        return None
    for token in tokens:
        unit = token if token in length_units else _split_word(token, words, length_units)
        if unit is None:
            continue
        others = [t for t in tokens if t != token]
        if any(not (t.startswith(words) or t.isdigit()) for t in others):
            continue
        if token != unit or any(t.startswith(words) for t in others):
            return measure, unit, length_units[unit]
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
        metres = enum_member(Qgis, "DistanceUnit", "Meters", None)
        degrees_unit = enum_member(Qgis, "DistanceUnit", "Degrees", None)
        if metres is None:
            metres = enum_member(QgsUnitTypes, "DistanceUnit", "DistanceMeters")
            degrees_unit = enum_member(QgsUnitTypes, "DistanceUnit", "DistanceDegrees")
        if crs.isGeographic() or crs.mapUnits() == degrees_unit:
            return _DEGREE_METRES if degrees else None
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

        tolerance = _rules().get("unit_tolerance")
        promised = field_unit(field_name, _geometry_kind(layer)) if tolerance else None
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
            value = as_finite(feature.attribute(index))
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

    agrees = any(1 / tolerance <= median <= tolerance for median in medians)
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


def _range_of(row) -> tuple[float, float, str] | None:
    return (float(row["low"]), float(row["high"]), row["what"]) if row else None


def normalised_difference_range() -> tuple[float, float, str] | None:

    return _range_of((_rules().get("ranges") or {}).get("normalised_difference"))


def expected_range(algorithm_id: str, parameters: dict) -> tuple[float, float, str] | None:

    algorithm_id = str(algorithm_id or "").lower()
    parameters = parameters if isinstance(parameters, dict) else {}
    ranges = _rules().get("ranges") or {}
    if algorithm_id in _algs("slope_algorithms", _SLOPE):
        if str(parameters.get("AS_PERCENT")).strip().lower() in ("true", "1"):
            return None
        return _range_of(ranges.get("slope"))
    if algorithm_id in _algs("aspect_algorithms", _ASPECT):
        return _range_of(ranges.get("aspect"))
    key = (_rules().get("calculators") or {}).get(algorithm_id)
    if key and normalised_difference(parameters.get(key)):
        return _range_of(ranges.get("normalised_difference"))
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
    tolerance = _rules().get("aoi_tolerance")
    if tolerance is None:
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
    if share > 1 + tolerance:
        name = overlay_layer.name() if hasattr(overlay_layer, "name") else "the overlay"
        out["warning"] = (f"its polygons cover {share:.0%} of {name!r}: they overlap, so class areas summed from it "
                          "count ground twice. Dissolve by class first.")
    return out


def zonal_coverage(algorithm_id: str, parameters: dict, output_layer) -> dict | None:






    if str(algorithm_id or "").lower() not in _algs("zonal_histogram", _ZONAL_HISTOGRAM) or output_layer is None:
        return None
    rules = _rules()
    tolerance, min_pixels = rules.get("zone_tolerance"), rules.get("zone_min_pixels")
    if tolerance is None or min_pixels is None:
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
            if area < min_pixels * pixel:
                continue
            pixels = sum(as_finite(feature.attribute(i)) or 0.0 for i in columns)
            counted += pixels * pixel
            zone += area
            zones += 1
    except Exception:  # noqa: BLE001
        return None
    if not zones or zone <= 0:
        return None
    share = counted / zone
    out = {"zones": zones, "share": round(share, 4)}
    if share < 1 - tolerance:
        out["warning"] = (f"the class pixels cover {share:.0%} of the zone area ({zones} zones): the raster misses "
                          "part of the zones, so class areas do not add up to them.")
    elif share > 1 + tolerance:
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

        from .layer_order import extent_known

        out_extent = extent_known(output_layer)
        clip = extent_known(overlay_layer)
        if out_extent is None or clip is None or out_extent.isEmpty() or clip.isEmpty():
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
