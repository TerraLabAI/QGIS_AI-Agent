# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import math

from qgis.core import Qgis, QgsApplication, QgsPalettedRasterRenderer, QgsRasterLayer

try:

    from qgis.core import QgsRasterAttributeTable
except ImportError:  # pragma: no cover
    QgsRasterAttributeTable = None
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP, QMetaType
from qgis.PyQt.QtGui import QColor

from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .layer_lookup import _find_layer
from .style_tools import _previous_style_keys, _style_to_put_back


def _color(value):
    color = QColor(str(value or ""))
    return color if color.isValid() else None


def _registry_names():
    registry = getattr(QgsApplication, "rasterRendererRegistry", None)
    if registry is not None:
        registry = registry()
    else:
        from qgis.core import QgsRasterRendererRegistry
        registry = QgsRasterRendererRegistry()
    return registry.renderersList()


def register_raster_style_tools(registry: ToolRegistry) -> None:
    classes = {"type": "object", "properties": {
        "value": {"type": "number"}, "color": {"type": "string"}, "label": {"type": "string"}},
        "required": ["value", "color"], "additionalProperties": False}
    registry.register(Tool(
        name="set_raster_class_style",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Style raster classes on {layer_name}"),
        input_schema={
        "type": "object", "properties": {"layer_name": {"type": "string"},
        "band": {"type": "integer", "minimum": 1}, "classes": {"type": "array", "items": classes}},
        "required": ["layer_name", "classes"], "additionalProperties": False}, handler=_set_raster_class_style))
    registry.register(Tool(
        name="set_raster_attribute_table",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Set the raster attribute table on {layer_name}"),
        input_schema={
        "type": "object", "properties": {"layer_name": {"type": "string"},
        "band": {"type": "integer", "minimum": 1}, "classes": {"type": "array", "items": classes}},
        "required": ["layer_name", "classes"], "additionalProperties": False}, handler=_set_raster_attribute_table))


def _validate(args):
    layer = _find_layer(str(args.get("layer_name") or ""))
    if layer is None:
        return None, tool_error(f"Layer {args.get('layer_name')!r} was not found.", "LAYER_NOT_FOUND")
    if not isinstance(layer, QgsRasterLayer):
        return None, tool_error("This tool needs a raster layer.", "INVALID_ARGS")
    try:
        band = int(args.get("band") or 1)
    except (TypeError, ValueError):
        return None, tool_error("band must be a positive integer.", "INVALID_ARGS")
    if band < 1 or band > layer.bandCount():
        return None, tool_error(f"Band {band} is outside 1..{layer.bandCount()}.", "INVALID_ARGS")
    values = []
    for item in args.get("classes") or []:
        if not isinstance(item, dict):
            return None, tool_error("classes must contain objects with value and color.", "INVALID_ARGS")
        try:
            value = float(item["value"])
        except (KeyError, TypeError, ValueError):
            return None, tool_error("Each class value must be a finite number.", "INVALID_ARGS")
        color = _color(item.get("color"))
        if not math.isfinite(value) or color is None:
            return None, tool_error("Each class needs a finite value and a valid color.", "INVALID_ARGS")
        if value in {v for v, _, _ in values}:
            return None, tool_error(f"Duplicate raster class value {value}.", "INVALID_ARGS")
        values.append((value, color, str(item.get("label") or value)))
    if not values:
        return None, tool_error("classes must contain at least one class.", "INVALID_ARGS")
    return (layer, band, values), None


def _set_raster_class_style(args):
    validated, error = _validate(args)
    if error:
        return error
    layer, band, values = validated
    if "paletted" not in _registry_names():
        return tool_error("This QGIS build does not provide the native paletted raster renderer.", "UNSUPPORTED")
    classes = [QgsPalettedRasterRenderer.Class(value, color, label) for value, color, label in values]
    renderer = QgsPalettedRasterRenderer(layer.dataProvider(), band, classes)
    previous = _style_to_put_back(layer)
    layer.setRenderer(renderer)
    layer.triggerRepaint()
    result = {"layer_name": layer.name(), "layer_id": layer.id(), "band": band,
              "renderer": "paletted", "classes": len(classes)}
    result.update(_previous_style_keys(layer, previous))
    return result


def _set_raster_attribute_table(args):
    validated, error = _validate(args)
    if error:
        return error
    layer, band, values = validated
    if QgsRasterAttributeTable is None:
        return tool_error("Raster Attribute Tables need QGIS 3.30 or later; this QGIS is older.",
                          "UNSUPPORTED")
    rat = QgsRasterAttributeTable()
    usages = Qgis.RasterAttributeTableFieldUsage
    for name, usage, meta_type in (("Value", usages.MinMax, QMetaType.Type.Double),
                                   ("ClassName", usages.Name, QMetaType.Type.QString)):
        ok, message = rat.appendField(name, usage, meta_type)
        if not ok:
            return tool_error(f"Could not create raster attribute table field {name}: {message}", "WRITE_FAILED")
    ok, message = rat.insertColor(2)
    if not ok:
        return tool_error(f"Could not create raster attribute table color fields: {message}", "WRITE_FAILED")
    for value, color, label in values:
        ok, message = rat.appendRow([value, label, color.red(), color.green(), color.blue(), color.alpha()])
        if not ok:
            return tool_error(f"Could not add raster attribute table class: {message}", "WRITE_FAILED")
    valid, message = rat.isValid()
    if not valid:
        return tool_error(f"QGIS rejected the raster attribute table: {message}", "WRITE_FAILED")
    setter = getattr(layer.dataProvider(), "setAttributeTable", None)
    if setter is None:
        return tool_error("This raster provider cannot attach a Raster Attribute Table.", "UNSUPPORTED")
    try:
        setter(band, rat)
    except Exception as exc:
        return tool_error(f"This raster provider could not attach the Raster Attribute Table: {exc}", "WRITE_FAILED")
    layer.triggerRepaint()
    return {"layer_name": layer.name(), "layer_id": layer.id(), "band": band,
            "classes": len(values), "fields": [field.name for field in rat.fields()], "attached": True}


__all__ = ["register_raster_style_tools"]
