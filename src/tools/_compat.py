# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""QGIS 3.28 to 4.x enum spellings, resolved at runtime."""









from __future__ import annotations

from qgis.core import (
    Qgis,
    QgsColorRampShader,
    QgsContrastEnhancement,
    QgsMapLayer,
    QgsRasterBandStats,
    QgsSingleBandGrayRenderer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant

_MISSING = object()


def enum_value(*candidates):
    """The first ``(root, "dotted.path")`` pair that resolves, else None."""
    for root, path in candidates:
        obj = root
        for part in path.split("."):
            obj = getattr(obj, part, _MISSING)
            if obj is _MISSING:
                break
        else:
            return obj
    return None


LAYER_VECTOR = enum_value((Qgis, "LayerType.Vector"), (QgsMapLayer, "VectorLayer"))
LAYER_RASTER = enum_value((Qgis, "LayerType.Raster"), (QgsMapLayer, "RasterLayer"))

RASTER_STATS_ALL = enum_value((Qgis, "RasterBandStatistic.All"), (QgsRasterBandStats, "All"))

SHADER_INTERPOLATED = enum_value((QgsColorRampShader, "Type.Interpolated"), (QgsColorRampShader, "Interpolated"))
SHADER_DISCRETE = enum_value((QgsColorRampShader, "Type.Discrete"), (QgsColorRampShader, "Discrete"))
SHADER_EXACT = enum_value((QgsColorRampShader, "Type.Exact"), (QgsColorRampShader, "Exact"))
SHADER_CLASS_CONTINUOUS = enum_value(
    (QgsColorRampShader, "ClassificationMode.Continuous"), (QgsColorRampShader, "Continuous")
)
SHADER_CLASS_EQUAL_INTERVAL = enum_value(
    (QgsColorRampShader, "ClassificationMode.EqualInterval"), (QgsColorRampShader, "EqualInterval")
)
SHADER_CLASS_QUANTILE = enum_value(
    (QgsColorRampShader, "ClassificationMode.Quantile"), (QgsColorRampShader, "Quantile")
)

CONTRAST_NONE = enum_value(
    (QgsContrastEnhancement, "ContrastEnhancementAlgorithm.NoEnhancement"),
    (QgsContrastEnhancement, "NoEnhancement"),
)
CONTRAST_STRETCH_MINMAX = enum_value(
    (QgsContrastEnhancement, "ContrastEnhancementAlgorithm.StretchToMinimumMaximum"),
    (QgsContrastEnhancement, "StretchToMinimumMaximum"),
)
CONTRAST_CLIP_MINMAX = enum_value(
    (QgsContrastEnhancement, "ContrastEnhancementAlgorithm.ClipToMinimumMaximum"),
    (QgsContrastEnhancement, "ClipToMinimumMaximum"),
)
CONTRAST_STRETCH_CLIP_MINMAX = enum_value(
    (QgsContrastEnhancement, "ContrastEnhancementAlgorithm.StretchAndClipToMinimumMaximum"),
    (QgsContrastEnhancement, "StretchAndClipToMinimumMaximum"),
)

GRAY_BLACK_TO_WHITE = enum_value(
    (QgsSingleBandGrayRenderer, "Gradient.BlackToWhite"), (QgsSingleBandGrayRenderer, "BlackToWhite")
)
GRAY_WHITE_TO_BLACK = enum_value(
    (QgsSingleBandGrayRenderer, "Gradient.WhiteToBlack"), (QgsSingleBandGrayRenderer, "WhiteToBlack")
)

WKB_NO_GEOMETRY = enum_value(
    (Qgis, "WkbType.NoGeometry"), (QgsWkbTypes, "Type.NoGeometry"), (QgsWkbTypes, "NoGeometry")
)

QVAR_STRING = enum_value((QVariant, "String"), (QVariant, "Type.String"))
QVAR_INT = enum_value((QVariant, "Int"), (QVariant, "Type.Int"))
QVAR_LONGLONG = enum_value((QVariant, "LongLong"), (QVariant, "Type.LongLong"))
QVAR_DOUBLE = enum_value((QVariant, "Double"), (QVariant, "Type.Double"))
QVAR_BOOL = enum_value((QVariant, "Bool"), (QVariant, "Type.Bool"))
QVAR_DATE = enum_value((QVariant, "Date"), (QVariant, "Type.Date"))
QVAR_DATETIME = enum_value((QVariant, "DateTime"), (QVariant, "Type.DateTime"))

FIELD_TYPES = {
    "string": QVAR_STRING,
    "text": QVAR_STRING,
    "int": QVAR_INT,
    "integer": QVAR_INT,
    "long": QVAR_LONGLONG,
    "double": QVAR_DOUBLE,
    "float": QVAR_DOUBLE,
    "real": QVAR_DOUBLE,
    "bool": QVAR_BOOL,
    "boolean": QVAR_BOOL,
    "date": QVAR_DATE,
    "datetime": QVAR_DATETIME,
}


def is_vector(layer) -> bool:
    return layer is not None and layer.type() == LAYER_VECTOR


def is_raster(layer) -> bool:
    return layer is not None and layer.type() == LAYER_RASTER


def py_value(value):
    """A feature attribute as a JSON-safe Python value."""
    if value is None:
        return None
    if isinstance(value, QVariant):
        if value.isNull():
            return None
        value = value.value()
    if isinstance(value, (bool, int, float, str)):
        return value
    for method in ("toPyDateTime", "toPyDate", "toPyTime"):
        fn = getattr(value, method, None)
        if callable(fn):
            try:
                return fn().isoformat()
            except Exception:
                break
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    try:
        return str(value)
    except Exception:
        return None
