# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

from functools import reduce

from qgis.core import (
    Qgis,
    QgsColorRampShader,
    QgsContrastEnhancement,
    QgsMapLayer,
    QgsSingleBandGrayRenderer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant

from ..core.qt_compat import enum_member, field_type

_ABSENT = object()


def _lookup(owner, dotted: str):





    return reduce(lambda obj, attr: getattr(obj, attr, _ABSENT), dotted.split("."), owner)


def enum_value(*candidates):






    found = (_lookup(owner, dotted) for owner, dotted in candidates)
    return next((value for value in found if value is not _ABSENT and value is not None), None)


LAYER_VECTOR = enum_value((Qgis, "LayerType.Vector"), (QgsMapLayer, "VectorLayer"))
LAYER_RASTER = enum_value((Qgis, "LayerType.Raster"), (QgsMapLayer, "RasterLayer"))

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


def _field_kind(name: str):

    try:
        return field_type(name)
    except AttributeError:
        return None


QVAR_STRING = _field_kind("String")
QVAR_INT = _field_kind("Int")
QVAR_LONGLONG = _field_kind("LongLong")
QVAR_DOUBLE = _field_kind("Double")
QVAR_BOOL = _field_kind("Bool")
QVAR_DATE = _field_kind("Date")
QVAR_DATETIME = _field_kind("DateTime")

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





    if value is None or (isinstance(value, QVariant) and value.isNull()):
        return None
    if isinstance(value, QVariant):
        value = value.value()
    if isinstance(value, (bool, int, float, str)):
        return value
    to_python = next((getattr(value, name) for name in _QT_TIME_TO_PYTHON
                      if callable(getattr(value, name, None))), None)
    if to_python is not None:
        try:
            return to_python().isoformat()
        except Exception:  # noqa: BLE001
            return _text(value)
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    return _text(value)


def _text(value):
    try:
        return str(value)
    except Exception:  # noqa: BLE001
        return None


_QT_TIME_TO_PYTHON = ("toPyDateTime", "toPyDate", "toPyTime")


def composition_mode(name: str):

    from qgis.core import Qgis, QgsPainting

    member = "".join(part.capitalize() for part in name.split("_"))
    mode = enum_member(Qgis, "BlendMode", member, None)
    if mode is None:
        mode = enum_member(QgsPainting, "BlendMode", "Blend" + member, None)
    if mode is None:
        return None
    return QgsPainting.getCompositionMode(mode)
