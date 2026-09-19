# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

import math

from qgis.core import (
    QgsApplication,
    QgsColorRampShader,
    QgsPointCloudAttributeByRampRenderer,
    QgsPointCloudClassifiedRenderer,
    QgsPointCloudLayer,
    QgsPointCloudRgbRenderer,
    QgsStyle,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core.tool_registry import Tool, ToolRegistry, tool_error
from ._compat import SHADER_CLASS_CONTINUOUS, SHADER_INTERPOLATED
from .layer_lookup import _find_layer

TOOL = "configure_pointcloud_style"
_MODES = ("rgb", "classification", "elevation_ramp")
_RENDERERS = {"rgb": "rgb", "classification": "classified", "elevation_ramp": "ramp"}


def register_pointcloud_style_tools(registry: ToolRegistry):

    registry.register(Tool(
        name="configure_pointcloud_style",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Style the point cloud {layer_name}"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "mode": {"type": "string", "enum": list(_MODES)},
                "red_attribute": {"type": "string"}, "green_attribute": {"type": "string"},
                "blue_attribute": {"type": "string"}, "attribute": {"type": "string"},
                "minimum": {"type": "number"}, "maximum": {"type": "number"},
                "color_ramp": {"type": "string"}, "z_scale": {"type": "number", "exclusiveMinimum": 0},
                "z_offset": {"type": "number"}, "elevation_limit": {"type": "number"},
            },
            "required": ["layer_name", "mode"],
        },
        handler=_configure_pointcloud_style,
    ))


def _attributes(layer) -> dict:

    attrs = layer.attributes()
    return {attrs.at(index).name().casefold(): attrs.at(index).name() for index in range(attrs.count())}


def _attribute(attributes: dict, requested: str, label: str) -> tuple[str | None, dict | None]:
    actual = attributes.get(str(requested or "").casefold())
    if actual:
        return actual, None
    available = ", ".join(sorted(attributes.values())[:20]) or "none"
    return None, tool_error(f"{label} attribute {requested!r} is not on this point cloud.", "INVALID_ARGS",
                            f"Use one of: {available}.")


def _renderer_available(mode: str) -> bool:

    registry = QgsApplication.pointCloudRendererRegistry()
    return registry is not None and _RENDERERS[mode] in registry.renderersList()


def _range(layer, attribute: str, minimum, maximum) -> tuple[float | None, float | None, dict | None]:
    try:
        low = float(minimum) if minimum is not None else float(layer.statistics().statisticsOf(attribute).minimum())
        high = float(maximum) if maximum is not None else float(layer.statistics().statisticsOf(attribute).maximum())
    except Exception:  # noqa: BLE001
        return None, None, tool_error(f"QGIS has no numeric range for {attribute!r} on this point cloud.",
                                      "EXECUTION_FAILED", "Pass minimum and maximum explicitly.")
    if not (math.isfinite(low) and math.isfinite(high) and low < high):
        return None, None, tool_error("minimum must be below maximum and both must be finite.", "INVALID_ARGS",
                                      "Pass two finite values with minimum below maximum.")
    return low, high, None


def _ramp(name: str):
    style = QgsStyle.defaultStyle()
    ramp = style.colorRamp(name) if name else None
    if ramp is None and name:
        matches = [candidate for candidate in style.colorRampNames() if candidate.casefold() == name.casefold()]
        ramp = style.colorRamp(matches[0]) if matches else None
    return ramp


def _configure_pointcloud_style(args: dict) -> dict:

    layer = _find_layer(str(args.get("layer_name") or ""))
    if layer is None:
        return tool_error(f"Layer {args.get('layer_name')!r} was not found.", "LAYER_NOT_FOUND")
    if not isinstance(layer, QgsPointCloudLayer):
        return tool_error(f"{layer.name()!r} is not a point-cloud layer.", "INVALID_ARGS",
                          "Load a LAS, LAZ, COPC, EPT or VPC point cloud first.")
    mode = str(args.get("mode") or "")
    if mode not in _MODES:
        return tool_error(f"mode {mode!r} is not supported.", "INVALID_ARGS",
                          "Pass rgb, classification or elevation_ramp.")
    if not _renderer_available(mode):
        return tool_error(f"This QGIS build does not provide the {mode} point-cloud renderer.", "EXECUTION_FAILED")

    attributes = _attributes(layer)
    result = {"layer_name": layer.name(), "mode": mode}
    if mode == "rgb":
        channels = []
        for key, default in (("red_attribute", "Red"), ("green_attribute", "Green"), ("blue_attribute", "Blue")):
            actual, error = _attribute(attributes, args.get(key) or default, key.replace("_", " "))
            if error:
                return error
            channels.append(actual)
        renderer = QgsPointCloudRgbRenderer()
        renderer.setRedAttribute(channels[0])
        renderer.setGreenAttribute(channels[1])
        renderer.setBlueAttribute(channels[2])
        result["attributes"] = {"red": channels[0], "green": channels[1], "blue": channels[2]}
    elif mode == "classification":
        attribute, error = _attribute(attributes, args.get("attribute") or "Classification", "classification")
        if error:
            return error
        renderer = QgsPointCloudClassifiedRenderer(
            attribute, QgsApplication.pointCloudRendererRegistry().classificationAttributeCategories(layer)
        )
        result["attribute"] = attribute
    else:
        attribute, error = _attribute(attributes, args.get("attribute") or "Z", "elevation")
        if error:
            return error
        low, high, error = _range(layer, attribute, args.get("minimum"), args.get("maximum"))
        if error:
            return error
        ramp_name = str(args.get("color_ramp") or "")
        color_ramp = _ramp(ramp_name)
        if ramp_name and color_ramp is None:
            return tool_error(f"Colour ramp {ramp_name!r} was not found.", "INVALID_ARGS",
                              "Pass a ramp shown in QGIS's Style Manager, or omit color_ramp.")
        shader = QgsColorRampShader(low, high)
        shader.setColorRampType(SHADER_INTERPOLATED)
        shader.setClassificationMode(SHADER_CLASS_CONTINUOUS)
        if color_ramp is not None:
            shader.setSourceColorRamp(color_ramp.clone())
        renderer = QgsPointCloudAttributeByRampRenderer()
        renderer.setAttribute(attribute)
        renderer.setMinimum(low)
        renderer.setMaximum(high)
        renderer.setColorRampShader(shader)
        result.update({"attribute": attribute, "minimum": low, "maximum": high,
                       **({"color_ramp": ramp_name} if ramp_name else {})})

    layer.setRenderer(renderer)
    elevation = layer.elevationProperties()
    if args.get("z_scale") is not None:
        elevation.setZScale(float(args["z_scale"]))
        result["z_scale"] = float(args["z_scale"])
    if args.get("z_offset") is not None:
        elevation.setZOffset(float(args["z_offset"]))
        result["z_offset"] = float(args["z_offset"])
    limit = args.get("elevation_limit")
    setter = getattr(elevation, "setElevationLimit", None)
    if limit is not None and callable(setter):
        setter(float(limit))
        result["elevation_limit"] = float(limit)
    elif limit is not None:
        result["elevation_limit_note"] = "This QGIS version has no point-cloud elevation limit API."
    layer.triggerRepaint()
    return result
