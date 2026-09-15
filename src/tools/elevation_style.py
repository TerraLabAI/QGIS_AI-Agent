# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""A readable look for an elevation raster the moment it is added."""






















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
    """The last path segment of a file, a /vsicurl source or a URL, without its query."""
    text = str(source or "").split("|", 1)[0]
    if "://" in text:
        text = urllib.parse.urlsplit(text[text.index("http"):] if "http" in text else text).path
    return text.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def names_elevation(name: str | None, source: str | None) -> bool:
    """Whether the layer name or the file name says this is an elevation model."""
    return bool(_ELEVATION_WORDS.search(str(name or "")) or _ELEVATION_WORDS.search(_file_name(source or "")))


def elevation_stretch(source: str, name: str | None) -> dict | None:
    """The 2-98 % cut of an elevation raster, or None when it is not one. GDAL only, any thread."""
    if not names_elevation(name, source):
        return None
    cut = sample_stretch(source)
    if cut is None or not (_LOWEST_M <= cut["min"] < cut["max"] <= _HIGHEST_M):
        return None
    return cut


def sample_stretch(source: str) -> dict | None:
    """The 2-98 % cut of a one-band numeric raster, read on a 512 pixel sample, or None. GDAL only, any thread."""
    try:
        import numpy as np
        from osgeo import gdal
    except ImportError:  # pragma: no cover - every QGIS ships both
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
    except Exception:  # noqa: BLE001 - a raster that cannot be sampled keeps QGIS's own look
        return None
    if not low < high:
        return None
    return {"min": low, "max": high}


def apply_elevation_style(layer, stretch: dict | None) -> str:
    """Set the ramp on a layer still wearing QGIS's default grey. Main thread. Returns what was done, or ""."""
    if not stretch:
        return ""
    try:
        from qgis.core import QgsColorRampShader, QgsRasterShader, QgsSingleBandPseudoColorRenderer
        from qgis.PyQt.QtGui import QColor

        from ._compat import SHADER_CLASS_CONTINUOUS, SHADER_INTERPOLATED

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
        layer.setRenderer(renderer)
    except Exception:  # noqa: BLE001 - a style that cannot be built leaves the layer as QGIS drew it
        return ""
    return f"elevation ramp from {low:,.0f} to {high:,.0f} (2-98 % cumulative cut)"


def apply_gray_stretch(layer, stretch: dict | None) -> str:
    """Stretch a layer still in QGIS's default grey to *stretch*. Main thread. Returns what was done, or ""."""
    if not stretch:
        return ""
    try:
        from qgis.core import QgsContrastEnhancement

        from ._compat import CONTRAST_STRETCH_MINMAX

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
        layer.setRenderer(renderer)
    except Exception:  # noqa: BLE001 - a style that cannot be built leaves the layer as QGIS drew it
        return ""
    return f"grey stretch from {low:,.4g} to {high:,.4g} (2-98 % cumulative cut)"


__all__ = ["apply_elevation_style", "apply_gray_stretch", "elevation_stretch", "names_elevation", "sample_stretch"]
