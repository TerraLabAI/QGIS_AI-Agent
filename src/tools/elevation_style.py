# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
























from __future__ import annotations

import re
import urllib.parse

_ELEVATION_WORDS = re.compile(
    r"(?<![a-z0-9])(dem|dsm|dtm|elevation|elevations|altitude|altimetry|srtm|nasadem|aw3d30|cop30|cop90|"
    r"copernicus[ _-]?dem|glo[ _-]?30|glo[ _-]?90|eu[ _-]?dem|eu[ _-]?dtm|gmted|mnt|mns|rge[ _-]?alti|"

    r"mdt\d*|mde\d*|elevaci[oó]n\w*|elevac[aã]o|altitud|"
    r"terrain[ _-]?model|height[ _-]?above[ _-]?sea)(?![a-z])",
    re.IGNORECASE,
)
_SAMPLE_PIXELS = 512
_LOWEST_M, _HIGHEST_M = -450.0, 8900.0

_STOPS = ((0.0, "#2c7a4b"), (0.2, "#86c06c"), (0.4, "#e8e3a0"), (0.6, "#d6ae72"), (0.8, "#a57c56"),
          (1.0, "#f5f2ee"))


def _file_name(source: str) -> str:

    text = str(source or "").split("|", 1)[0]
    if "://" in text:
        text = urllib.parse.urlsplit(text[text.index("http"):] if "http" in text else text).path
    return text.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def names_elevation(name: str | None, source: str | None) -> bool:

    return bool(_ELEVATION_WORDS.search(str(name or "")) or _ELEVATION_WORDS.search(_file_name(source or "")))


def elevation_stretch(source: str, name: str | None) -> dict | None:

    if not names_elevation(name, source):
        return None
    cut = sample_stretch(source)
    if cut is None or not (_LOWEST_M <= cut["min"] < cut["max"] <= _HIGHEST_M):
        return None
    return cut


def sample_stretch(source: str) -> dict | None:

    try:
        import numpy as np
        from osgeo import gdal
    except ImportError:  # pragma: no cover
        return None
    try:
        dataset = gdal.Open(str(source).split("|", 1)[0])
        if dataset is None or dataset.RasterCount != 1:
            return None
        band = dataset.GetRasterBand(1)
        kind = gdal.GetDataTypeName(band.DataType) or ""
        if kind in ("Byte", "Int8") or kind.startswith("C") or band.GetColorTable() is not None:
            return None
        width, height = dataset.RasterXSize, dataset.RasterYSize
        step = max(1.0, max(width, height) / float(_SAMPLE_PIXELS))
        sample = band.ReadAsArray(0, 0, width, height, buf_xsize=max(1, int(width / step)),
                                  buf_ysize=max(1, int(height / step)))
        if sample is None:
            return None
        values = sample.astype("float64")
        keep = np.isfinite(values)
        nodata = band.GetNoDataValue()
        if nodata is not None:
            keep &= values != nodata
        valid = values[keep]
        if valid.size < 16:
            return None
        low, high = (float(v) for v in np.percentile(valid, [2.0, 98.0]))
    except Exception:  # noqa: BLE001
        return None
    if not low < high:
        return None
    return {"min": low, "max": high}


def apply_elevation_style(layer, stretch: dict | None) -> str:

    if not stretch:
        return ""
    try:
        from qgis.core import QgsColorRampShader, QgsRasterShader, QgsSingleBandPseudoColorRenderer
        from qgis.PyQt.QtGui import QColor

        from ._compat import SHADER_CLASS_CONTINUOUS, SHADER_INTERPOLATED
        from .style_defaults import min_max_origin

        if layer.bandCount() != 1:
            return ""
        current = layer.renderer()
        if current is None or current.type() != "singlebandgray":
            return ""
        low, high = float(stretch["min"]), float(stretch["max"])
        function = QgsColorRampShader(low, high)
        function.setColorRampType(SHADER_INTERPOLATED)
        function.setClassificationMode(SHADER_CLASS_CONTINUOUS)
        items = []
        for offset, color in _STOPS:
            value = low + (high - low) * offset
            items.append(QgsColorRampShader.ColorRampItem(value, QColor(color), f"{value:,.0f}"))
        function.setColorRampItemList(items)
        shader = QgsRasterShader()
        shader.setRasterShaderFunction(function)
        renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
        renderer.setClassificationMin(low)
        renderer.setClassificationMax(high)
        renderer.setOpacity(current.opacity())

        renderer.setMinMaxOrigin(min_max_origin("CumulativeCut", (2, 98)))
        layer.setRenderer(renderer)
    except Exception:  # noqa: BLE001
        return ""
    return f"elevation ramp from {low:,.0f} to {high:,.0f} (2-98 % cumulative cut)"


def apply_gray_stretch(layer, stretch: dict | None) -> str:

    if not stretch:
        return ""
    try:
        from qgis.core import QgsContrastEnhancement

        from ._compat import CONTRAST_STRETCH_MINMAX
        from .style_defaults import min_max_origin

        current = layer.renderer()
        if layer.bandCount() != 1 or current is None or current.type() != "singlebandgray":
            return ""
        low, high = float(stretch["min"]), float(stretch["max"])
        enhancement = QgsContrastEnhancement(layer.dataProvider().dataType(1))
        enhancement.setMinimumValue(low)
        enhancement.setMaximumValue(high)
        enhancement.setContrastEnhancementAlgorithm(CONTRAST_STRETCH_MINMAX, True)
        renderer = current.clone()
        renderer.setContrastEnhancement(enhancement)
        renderer.setMinMaxOrigin(min_max_origin("CumulativeCut", (2, 98)))
        layer.setRenderer(renderer)
    except Exception:  # noqa: BLE001
        return ""
    return f"grey stretch from {low:,.4g} to {high:,.4g} (2-98 % cumulative cut)"


def min_max_stats():

    from qgis.core import QgsRasterBandStats

    from ..core.qt_compat import enum_member



    return enum_member(QgsRasterBandStats, "Stats", "Min") | enum_member(QgsRasterBandStats, "Stats", "Max")


def band_range(layer, band: int = 1) -> tuple | None:





    try:
        from .raster_overviews import read_ahead

        def measure() -> tuple:
            stats = layer.dataProvider().bandStatistics(int(band), min_max_stats(), layer.extent(), 250000)
            return stats.minimumValue, stats.maximumValue

        low, high = (float(value) for value in read_ahead(("range", int(band)), measure))
    except Exception:  # noqa: BLE001
        return None
    import math as _math

    if not (_math.isfinite(low) and _math.isfinite(high)) or not low < high:
        return None
    return low, high


def _class_label(low: float, high: float, span: float, unit: str) -> str:






    digits = 0 if span >= 50 else (1 if span >= 5 else (2 if span >= 0.5 else 4))
    tail = f" {unit}" if unit else ""
    return f"{low:,.{digits}f} - {high:,.{digits}f}{tail}"


def _ramp_colors(ramp_name: str, count: int) -> list:

    from qgis.PyQt.QtGui import QColor

    ramp = None
    if ramp_name:
        try:
            from qgis.core import QgsStyle

            style = QgsStyle.defaultStyle()
            ramp = style.colorRamp(ramp_name)
            if ramp is None:
                same = [n for n in style.colorRampNames() if n.lower() == str(ramp_name).lower()]
                ramp = style.colorRamp(same[0]) if same else None
        except Exception:  # noqa: BLE001
            ramp = None
    if ramp is not None:
        return [ramp.color(i / (count - 1) if count > 1 else 0.0) for i in range(count)]
    colors = []
    for i in range(count):
        offset = i / (count - 1) if count > 1 else 0.0
        before = max(s for s in _STOPS if s[0] <= offset)
        after = min((s for s in _STOPS if s[0] >= offset), default=before)
        if after[0] == before[0]:
            colors.append(QColor(before[1]))
            continue
        t = (offset - before[0]) / (after[0] - before[0])
        a, b = QColor(before[1]), QColor(after[1])
        colors.append(QColor(int(a.red() + (b.red() - a.red()) * t),
                             int(a.green() + (b.green() - a.green()) * t),
                             int(a.blue() + (b.blue() - a.blue()) * t)))
    return colors


def apply_ramp_style(layer, band: int = 1, ramp_name: str = "", classes: int = 0,
                     unit: str = "", low: float | None = None, high: float | None = None) -> dict:






    from qgis.core import QgsColorRampShader, QgsRasterShader, QgsSingleBandPseudoColorRenderer

    from ._compat import SHADER_CLASS_EQUAL_INTERVAL, SHADER_DISCRETE, SHADER_INTERPOLATED

    band = max(1, int(band or 1))
    if band > layer.bandCount():
        return {"_error": f"Band {band} does not exist: {layer.name()!r} has {layer.bandCount()}.",
                "code": "INVALID_ARGS", "suggestion": f"band is 1 to {layer.bandCount()}."}
    if low is None or high is None:
        found = band_range(layer, band)
        if found is None:
            return {"_error": f"Band {band} of {layer.name()!r} has no readable minimum and maximum.",
                    "code": "EXECUTION_FAILED",
                    "suggestion": "get_raster_info shows if the band is empty or all nodata."}
        low, high = found
    low, high = float(low), float(high)
    steps = int(classes or 0)
    items = []
    if steps >= 2:
        steps = min(steps, 64)
        colors = _ramp_colors(ramp_name, steps)
        width = (high - low) / steps
        for i in range(steps):
            start, end = low + width * i, low + width * (i + 1)
            items.append(QgsColorRampShader.ColorRampItem(end, colors[i], _class_label(start, end, width, unit)))
        shader_type, classification = SHADER_DISCRETE, SHADER_CLASS_EQUAL_INTERVAL
    else:
        stops = 6
        colors = _ramp_colors(ramp_name, stops)
        for i in range(stops):
            value = low + (high - low) * i / (stops - 1)
            digits = 0 if (high - low) >= 50 else 2
            items.append(QgsColorRampShader.ColorRampItem(
                value, colors[i], f"{value:,.{digits}f}{(' ' + unit) if unit else ''}"))
        shader_type, classification = SHADER_INTERPOLATED, SHADER_CLASS_EQUAL_INTERVAL
    function = QgsColorRampShader(low, high)
    function.setColorRampType(shader_type)
    function.setClassificationMode(classification)
    function.setColorRampItemList(items)
    shader = QgsRasterShader()
    shader.setRasterShaderFunction(function)
    renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), band, shader)
    renderer.setClassificationMin(low)
    renderer.setClassificationMax(high)
    current = layer.renderer()
    if current is not None:
        renderer.setOpacity(current.opacity())
    layer.setRenderer(renderer)
    return {"band": band, "min": round(low, 6), "max": round(high, 6),
            "classes": len(items) if steps >= 2 else 0,
            "class_labels": [item.label for item in items]}


def looks_like_elevation(layer, band: int = 1) -> bool:

    if not names_elevation(layer.name(), layer.source()):
        return False
    found = band_range(layer, band)
    return found is not None and _LOWEST_M <= found[0] < found[1] <= _HIGHEST_M


def elevation_ramp():

    from qgis.core import QgsGradientColorRamp, QgsGradientStop
    from qgis.PyQt.QtGui import QColor

    ramp = QgsGradientColorRamp(QColor(_STOPS[0][1]), QColor(_STOPS[-1][1]))
    ramp.setStops([QgsGradientStop(offset, QColor(color)) for offset, color in _STOPS[1:-1]])
    return ramp







_RAMP_ALIASES = frozenset({"terrain", "elevation", "hypsometric", "altitude"})


def is_elevation_alias(ramp_name: str | None) -> bool:

    return str(ramp_name or "").strip().lower() in _RAMP_ALIASES


RELIEF_PROPERTY = "ai_agent/relief_of"


def _early_bilinear(layer) -> None:







    from qgis.core import Qgis, QgsRasterDataProvider

    from ..core.qt_compat import enum_member

    provider = layer.dataProvider()
    try:
        if not provider.enableProviderResampling(True):
            return
        method = enum_member(QgsRasterDataProvider, "ResamplingMethod", "Bilinear", None)
        if method is not None:
            provider.setZoomedInResamplingMethod(method)
            provider.setZoomedOutResamplingMethod(method)
        stage = enum_member(Qgis, "RasterResamplingStage", "Provider", None)
        if stage is not None:
            layer.setResamplingStage(stage)
    except Exception:  # noqa: BLE001
        return


def degrees_z_factor(layer) -> float | None:






    import math

    crs = layer.crs()
    if not (crs.isValid() and crs.isGeographic()):
        return None
    latitude = layer.extent().center().y()
    return 1.0 / (111320.0 * max(0.1, math.cos(math.radians(latitude))))


def add_relief_overlay(layer, band: int = 1) -> dict:











    from qgis.core import QgsBilinearRasterResampler, QgsHillshadeRenderer, QgsProject, QgsRasterLayer

    from ..core import layer_order
    from .symbology_tools import _composition_mode

    project = QgsProject.instance()
    for other in project.mapLayers().values():
        if (isinstance(other, QgsRasterLayer) and other.id() != layer.id()
                and other.customProperty(RELIEF_PROPERTY, "") == layer.id()):
            return {"layer": other.name(), "layer_id": other.id(), "reused": True}
    copy = layer.clone()
    if copy is None or not copy.isValid():
        return {"_error": "The DEM could not be copied for its hillshade."}
    copy.setName(f"{layer.name()} relief")
    renderer = QgsHillshadeRenderer(copy.dataProvider(), int(band), 315.0, 45.0)
    z_factor = degrees_z_factor(layer) or 1.0
    renderer.setZFactor(z_factor)
    copy.setRenderer(renderer)
    pipe = copy.resampleFilter()
    if pipe is not None:

        pipe.setZoomedInResampler(QgsBilinearRasterResampler())
        pipe.setZoomedOutResampler(QgsBilinearRasterResampler())
    _early_bilinear(copy)
    mode = _composition_mode("multiply")
    if mode is not None:
        copy.setBlendMode(mode)
    copy.setCustomProperty(RELIEF_PROPERTY, layer.id())
    layer_order.keep_place(copy)
    root = project.layerTreeRoot()
    node = root.findLayer(layer.id())
    project.addMapLayer(copy, False)
    parent = node.parent() if node is not None else root
    index = parent.children().index(node) if node is not None else 0
    parent.insertLayer(index, copy)
    copy.triggerRepaint()
    return {"layer": copy.name(), "layer_id": copy.id(), "renderer": "hillshade", "blend_mode": "multiply",
            "azimuth": 315, "altitude": 45, "z_factor": round(z_factor, 9)}


__all__ = ["RELIEF_PROPERTY", "add_relief_overlay", "apply_elevation_style", "apply_gray_stretch",
           "apply_ramp_style", "band_range", "degrees_z_factor", "elevation_ramp", "elevation_stretch",
           "looks_like_elevation",
           "names_elevation", "sample_stretch"]
