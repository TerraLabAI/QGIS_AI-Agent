# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later











from __future__ import annotations

import contextlib
import math

from ..core import tuning
from ..core.qt_compat import enum_member
from ..core.tool_registry import coded_fact



_FIELD_SAMPLE = 20_000

_RASTER_SAMPLE = 250_000


def _rules(section: str) -> dict:

    doc = tuning.service_doc("style_rules") or {}
    value = doc.get(section)
    return value if isinstance(value, dict) else {}


def _number(value):

    if value is None:
        return None
    with contextlib.suppress(AttributeError):
        if value.isNull():
            return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def field_values(layer, index: int, cap: int = _FIELD_SAMPLE) -> list:

    from qgis.core import QgsFeatureRequest

    request = QgsFeatureRequest().setSubsetOfAttributes([index])
    flag = enum_member(QgsFeatureRequest, "Flag", "NoGeometry", None)
    if flag is not None:
        request.setFlags(flag)
    request.setLimit(cap)
    values = []
    features = layer.getFeatures(request)
    try:
        for feature in features:
            number = _number(feature[index])
            if number is not None:
                values.append(number)
    finally:
        with contextlib.suppress(Exception):
            features.close()
    return values


def skewness(values: list):

    count = len(values)
    if count < 8:
        return None
    mean = sum(values) / count
    m2 = sum((v - mean) ** 2 for v in values) / count
    if m2 <= 0:
        return None
    m3 = sum((v - mean) ** 3 for v in values) / count
    return m3 / m2 ** 1.5


def _short(number: float) -> str:

    if abs(number) >= 100 or number == int(number):
        return f"{number:,.0f}"
    return f"{number:.3g}"


def graduated_choice(layer, index: int) -> dict:







    rules = _rules("graduated")
    skewed_mode, even_mode, cut = rules.get("skewed_mode"), rules.get("even_mode"), rules.get("skew_min")
    if not skewed_mode or not even_mode or cut is None:
        return {}
    values = field_values(layer, index)
    g1 = skewness(values)
    if g1 is None:
        return {}
    ordered = sorted(values)
    field = layer.fields().at(index).name()
    facts = {"field": field, "skewness": round(g1, 1), "median": _short(ordered[len(ordered) // 2]),
             "low": _short(ordered[0]), "high": _short(ordered[-1])}
    if abs(g1) >= cut:
        mode = skewed_mode
        reason = coded_fact(hint="style_graduated_skewed", mode=mode, side="large" if g1 > 0 else "small", **facts)
    else:
        mode = even_mode
        reason = coded_fact(hint="style_graduated_even", mode=mode, **facts)
    out = {"mode": mode, "skewness": round(g1, 2), "reason": reason}
    distinct = sorted(set(values))
    most = rules.get("codes_max_distinct")
    if most and len(distinct) <= most and all(v == int(v) for v in distinct):
        shown = ", ".join(_short(v) for v in distinct[:6]) + ("..." if len(distinct) > 6 else "")
        out["note"] = coded_fact(hint="style_codes_note", field=field, distinct=len(distinct), values=shown)
    return out


def class_counts(layer, index: int, ranges: list[tuple[float, float]], cap: int = _FIELD_SAMPLE) -> list[int]:







    if not ranges:
        return []
    values = field_values(layer, index, cap)
    counts = [0] * len(ranges)
    for value in values:
        for i, (_, high) in enumerate(ranges):
            if value <= high:
                counts[i] += 1
                break
        else:
            counts[-1] += 1
    return counts


def dominant_class_note(counts: list[int], field: str, mode_name: str) -> dict:


    rules = _rules("dominant")
    share_min, spread = rules.get("share"), rules.get("spread_modes")
    total = sum(counts)
    if share_min is None or spread is None or total <= 0 or mode_name in spread:
        return {}
    share = max(counts) / total
    if share < share_min:
        return {}
    return coded_fact(hint="style_dominant_class", share=round(share, 3), worst=counts.index(max(counts)) + 1,
                      classes=len(counts), field=field, method=mode_name)




_INTEGER_TYPES = ("Byte", "Int8", "UInt16", "Int16", "UInt32", "Int32")


def is_integer_band(layer, band: int) -> bool:
    from qgis.core import Qgis

    kind = layer.dataProvider().dataType(int(band))
    return any(kind == enum_member(Qgis, "DataType", name, None) for name in _INTEGER_TYPES)


def raster_codes(layer, band: int = 1, strict: bool = True) -> list:









    from .elevation_style import band_range, names_elevation

    rules = _rules("raster_codes") if strict else {}
    if strict and (not rules.get("max_codes") or not rules.get("max_consecutive")):
        return []
    try:
        if not is_integer_band(layer, band):
            return [] if strict else _whole_float_codes(layer, band)
        if strict and names_elevation(layer.name(), layer.source()):
            return []
        found = band_range(layer, band)
        if found is None:
            return []
        low, high = int(round(found[0])), int(round(found[1]))
        bins = high - low + 1
        if bins < 2 or bins > 4096:
            return []
        provider = layer.dataProvider()

        histogram = provider.histogram(int(band), bins, low - 0.5, high + 0.5, layer.extent(), _RASTER_SAMPLE)
        counts = list(histogram.histogramVector)
    except Exception:  # noqa: BLE001
        return []
    if len(counts) != bins:
        return []
    codes = [low + i for i, count in enumerate(counts) if count > 0]
    if not strict:
        return codes if 1 <= len(codes) <= 256 else []
    if not 2 <= len(codes) <= rules["max_codes"]:
        return []
    consecutive = codes[-1] - codes[0] + 1 == len(codes)
    if consecutive and len(codes) > rules["max_consecutive"]:
        return []
    return codes


def _whole_float_codes(layer, band: int) -> list:






    from array import array

    from qgis.core import Qgis

    provider = layer.dataProvider()
    kind = provider.dataType(int(band))
    code = {enum_member(Qgis, "DataType", "Float32", None): "f",
            enum_member(Qgis, "DataType", "Float64", None): "d"}.get(kind)
    if code is None:
        return []
    width = max(1, min(500, layer.width()))
    height = max(1, min(500, layer.height()))
    block = provider.block(int(band), layer.extent(), width, height)
    if block is None or not block.isValid():
        return []
    values = array(code)
    values.frombytes(bytes(block.data()))
    nodata = provider.sourceNoDataValue(int(band)) if provider.sourceHasNoDataValue(int(band)) else None
    found = set()
    for value in values:
        if math.isnan(value) or value == nodata:
            continue
        if value != math.floor(value):
            return []
        found.add(int(value))
        if len(found) > 256:
            return []
    return sorted(found)


def code_colors(count: int, ramp=None) -> list:

    from qgis.PyQt.QtGui import QColor

    if ramp is not None:
        return [ramp.color(i / (count - 1) if count > 1 else 0.0) for i in range(count)]

    return [QColor.fromHsl(int(i * 137.508) % 360, 170, 140) for i in range(count)]


def paletted_renderer(layer, band: int, codes: list, ramp=None):

    from qgis.core import QgsPalettedRasterRenderer

    colors = code_colors(len(codes), ramp)
    classes = [QgsPalettedRasterRenderer.Class(float(code), color, str(code)) for code, color in zip(codes, colors)]
    return QgsPalettedRasterRenderer(layer.dataProvider(), int(band), classes)


def long_tail_cut(layer, band: int, low: float, high: float) -> dict:






    rules = _rules("stretch")
    lower, upper, ratio = rules.get("cut_low"), rules.get("cut_high"), rules.get("tail_ratio")
    if lower is None or upper is None or ratio is None:
        return {}
    try:
        cut_low, cut_high = layer.dataProvider().cumulativeCut(int(band), lower / 100.0, upper / 100.0,
                                                               layer.extent(), _RASTER_SAMPLE)
        cut_low, cut_high = float(cut_low), float(cut_high)
    except Exception:  # noqa: BLE001
        return {}
    if not (math.isfinite(cut_low) and math.isfinite(cut_high)) or not cut_low < cut_high:
        return {}
    if (high - low) <= ratio * (cut_high - cut_low):
        return {}
    return {"min": cut_low, "max": cut_high,
            "reason": coded_fact(hint="style_long_tail_cut", low=_short(low), high=_short(high),
                                 cut_min=_short(cut_low), cut_max=_short(cut_high), cut_low=lower, cut_high=upper,
                                 kept=round(upper - lower, 1))}


def min_max_origin(limits: str, cut: tuple | None = None):







    from qgis.core import QgsRasterMinMaxOrigin

    origin = QgsRasterMinMaxOrigin()
    origin.setLimits(QgsRasterMinMaxOrigin.limitsFromString(limits))
    origin.setExtent(QgsRasterMinMaxOrigin.extentFromString("WholeRaster"))
    origin.setStatAccuracy(QgsRasterMinMaxOrigin.statAccuracyFromString("Estimated"))
    if cut is not None:
        origin.setCumulativeCutLower(float(cut[0]) / 100.0)
        origin.setCumulativeCutUpper(float(cut[1]) / 100.0)
    return origin




_SCALE_TYPES = {"linear": "Linear", "area": "Area", "flannery": "Flannery", "exponential": "Exponential"}


def proportional_size(renderer, field: str, low: float, high: float, min_size: float, max_size: float,
                      markers: bool) -> str:








    from qgis.core import QgsDataDefinedSizeLegend, QgsProperty, QgsRenderContext, QgsSizeScaleTransformer

    rules = _rules("proportional")
    scale = rules.get("marker_scale" if markers else "line_scale") or "linear"
    exponent = rules.get("marker_exponent" if markers else "line_exponent") or 1.0
    kind = enum_member(QgsSizeScaleTransformer, "ScaleType", _SCALE_TYPES.get(scale, "Linear"), None)
    if kind is None:
        return ""
    applied = False
    for symbol in renderer.symbols(QgsRenderContext()):
        prop = QgsProperty.fromField(field)
        prop.setTransformer(QgsSizeScaleTransformer(kind, low, high, min_size, max_size, 0, exponent))
        if markers and hasattr(symbol, "setDataDefinedSize"):
            symbol.setDataDefinedSize(prop)
            applied = True
        elif not markers and hasattr(symbol, "setDataDefinedWidth"):
            symbol.setDataDefinedWidth(prop)
            applied = True
    if not applied:
        return ""
    if markers and hasattr(renderer, "setDataDefinedSizeLegend"):
        with contextlib.suppress(Exception):
            legend = QgsDataDefinedSizeLegend()
            collapsed = enum_member(QgsDataDefinedSizeLegend, "LegendType", "LegendCollapsed", None)
            if collapsed is not None:
                legend.setLegendType(collapsed)
            legend.setTitle(field)
            renderer.setDataDefinedSizeLegend(legend)
    return scale if scale in _SCALE_TYPES else "linear"




_STYLE_CATEGORIES_CACHE = None


def _style_categories():
    global _STYLE_CATEGORIES_CACHE
    if _STYLE_CATEGORIES_CACHE is not None:
        return _STYLE_CATEGORIES_CACHE
    from qgis.core import QgsMapLayer

    wanted = None
    for name in ("Symbology", "Labeling", "Diagrams"):
        member = enum_member(QgsMapLayer, "StyleCategory", name, None)
        if member is not None:
            wanted = member if wanted is None else wanted | member
    _STYLE_CATEGORIES_CACHE = wanted
    return wanted


def carry_style(source, target, algorithm_id: str, replace: bool = False) -> dict:











    from qgis.core import QgsRasterLayer, QgsReadWriteContext, QgsRenderContext, QgsVectorLayer
    from qgis.PyQt.QtXml import QDomDocument

    if source is None or target is None or source.id() == target.id():
        return {}
    names: set = set()
    if isinstance(source, QgsVectorLayer) and isinstance(target, QgsVectorLayer):
        renderer, current = source.renderer(), target.renderer()
        if renderer is None or current is None or (current.type() != "singleSymbol" and not replace):
            return {}
        styled = renderer.type() != "singleSymbol" or source.labelsEnabled() or source.diagramRenderer() is not None
        if not styled or source.geometryType() != target.geometryType():
            return {}
        names = set(target.fields().names())
        try:
            used = set(renderer.usedAttributes(QgsRenderContext()))
        except Exception:  # noqa: BLE001
            return {}
        if not used <= names:
            return {}
    elif isinstance(source, QgsRasterLayer) and isinstance(target, QgsRasterLayer):
        if (algorithm_id not in tuning.check_algs("raster_keeps_values", ())
                or source.bandCount() != target.bandCount()):
            return {}
        if source.renderer() is None:
            return {}
    else:
        return {}
    categories = _style_categories()
    if categories is None:
        return {}
    document = QDomDocument("qgis")
    try:
        message = source.exportNamedStyle(document, QgsReadWriteContext(), categories)
        if message:
            return {}
        imported = target.importNamedStyle(document, categories)
    except Exception:  # noqa: BLE001
        return {}
    if isinstance(imported, tuple) and imported and not imported[0]:
        return {}
    if isinstance(target, QgsVectorLayer):

        with contextlib.suppress(Exception):
            settings = target.labeling().settings() if target.labeling() is not None else None
            if settings is not None and not settings.isExpression and settings.fieldName not in names:
                target.setLabelsEnabled(False)
    target.triggerRepaint()
    kind = source.renderer().type() if source.renderer() is not None else ""
    return {"from": source.name(), "renderer": kind,
            "note": "the output keeps the input's style, so its colours and classes read the same"}


def style_computed_hillshade(layer) -> dict:










    from .harvest_view import _style_hillshade
    from .processing_guards import computed_hillshade
    from .symbology_tools import _composition_mode

    if not computed_hillshade(layer):
        return {}
    try:
        _style_hillshade(layer, 1.0)
        mode = _composition_mode("multiply")
        if mode is not None:
            layer.setBlendMode(mode)
        layer.triggerRepaint()
    except Exception:  # noqa: BLE001
        return {}
    return {"renderer": "singlebandgray 0-255", "blend": "multiply" if mode is not None else "normal",
            "note": ("already drawn as a hillshade: its values are light, not heights. Do not restyle it or "
                     "shade it again; to see colours under it, style the DEM below it.")}
