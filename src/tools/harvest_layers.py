# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Layer and style tools harvested from qgis-mcp."""














from __future__ import annotations

import os

from qgis.core import (
    QgsColorRampShader,
    QgsContrastEnhancement,
    QgsCoordinateReferenceSystem,
    QgsHillshadeRenderer,
    QgsMultiBandColorRenderer,
    QgsProject,
    QgsRasterShader,
    QgsRectangle,
    QgsSingleBandGrayRenderer,
    QgsSingleBandPseudoColorRenderer,
    QgsStyle,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsVectorLayerJoinInfo,
)

from ..core import layer_order, limits
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from ._compat import (
    CONTRAST_CLIP_MINMAX,
    CONTRAST_NONE,
    CONTRAST_STRETCH_CLIP_MINMAX,
    CONTRAST_STRETCH_MINMAX,
    GRAY_BLACK_TO_WHITE,
    GRAY_WHITE_TO_BLACK,
    RASTER_STATS_ALL,
    SHADER_CLASS_CONTINUOUS,
    SHADER_CLASS_EQUAL_INTERVAL,
    SHADER_CLASS_QUANTILE,
    SHADER_DISCRETE,
    SHADER_EXACT,
    SHADER_INTERPOLATED,
    is_raster,
    is_vector,
)
from ._layers import layer_not_found, resolve_layer
from .processing_guards import crs_plausibility

RASTER_STYLES = ("singleband_pseudocolor", "singleband_gray", "multiband_color", "hillshade")
_SHADER_INTERPOLATION = {"interpolated": SHADER_INTERPOLATED, "discrete": SHADER_DISCRETE, "exact": SHADER_EXACT}
_SHADER_CLASSIFICATION = {
    "continuous": SHADER_CLASS_CONTINUOUS,
    "equal_interval": SHADER_CLASS_EQUAL_INTERVAL,
    "quantile": SHADER_CLASS_QUANTILE,
}
_CONTRAST = {
    "none": CONTRAST_NONE,
    "stretch": CONTRAST_STRETCH_MINMAX,
    "clip": CONTRAST_CLIP_MINMAX,
    "stretch_clip": CONTRAST_STRETCH_CLIP_MINMAX,
}
_GRAY_GRADIENTS = {"black_to_white": GRAY_BLACK_TO_WHITE, "white_to_black": GRAY_WHITE_TO_BLACK}

_STATS_SAMPLE = 250_000



_MAX_RASTER_CLASSES = 256

LAYER_PROPERTIES = ("opacity", "name", "min_scale", "max_scale", "scale_visibility")


def register_harvest_layers_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="set_raster_style",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "style_type": {"type": "string", "enum": list(RASTER_STYLES)},
                "band": {"type": "integer"},
                "color_ramp": {"type": "string"},
                "classes": {"type": "integer"},
                "min_value": {"type": "number"},
                "max_value": {"type": "number"},
                "classification": {
                    "type": "string",
                    "enum": ["continuous", "equal_interval", "quantile"],
                },
                "interpolation": {
                    "type": "string",
                    "enum": ["interpolated", "discrete", "exact"],
                },
                "gradient": {
                    "type": "string",
                    "enum": ["black_to_white", "white_to_black"],
                },
                "contrast": {
                    "type": "string",
                    "enum": ["none", "stretch", "clip", "stretch_clip"],
                },
                "red_band": {"type": "integer"},
                "green_band": {"type": "integer"},
                "blue_band": {"type": "integer"},
                "azimuth": {"type": "number"},
                "altitude": {"type": "number"},
                "z_factor": {"type": "number"},
                "opacity": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["layer_name", "style_type"],
        },
        handler=_set_raster_style,
    ))

    registry.register(Tool(
        name="apply_style_qml",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["layer_name", "path"],
        },
        handler=_apply_style_qml,
    ))

    registry.register(Tool(
        name="save_style_qml",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "path": {
                    "type": "string",
                },
                "overwrite": {"type": "boolean"},
            },
            "required": ["layer_name", "path"],
        },
        handler=_save_style_qml,
    ))

    registry.register(Tool(
        name="set_layer_property",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "property": {"type": "string", "enum": list(LAYER_PROPERTIES)},
                "value": {
                    "type": ["string", "number", "boolean"],
                },
            },
            "required": ["layer_name", "property", "value"],
        },
        handler=_set_layer_property,
    ))

    registry.register(Tool(
        name="set_layer_order",
        input_schema={
            "type": "object",
            "properties": {
                "layers": {
                    "type": "array", "items": {"type": "string"}, "minItems": 1,
                },
                "layer_name": {"type": "string"},
                "position": {
                    "type": "string", "enum": ["top", "bottom", "above", "below"],
                },
                "reference_layer": {"type": "string"},
            },
            "required": [],
        },
        handler=_set_layer_order,
    ))

    registry.register(Tool(
        name="set_layer_crs",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "crs": {"type": "string"},
            },
            "required": ["layer_name", "crs"],
        },
        handler=_set_layer_crs,
    ))

    registry.register(Tool(
        name="get_layer_crs",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
            },
            "required": ["layer_name"],
        },
        handler=_get_layer_crs,
    ))

    registry.register(Tool(
        name="delete_field",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "field_name": {"type": "string"},
            },
            "required": ["layer_name", "field_name"],
        },
        handler=_delete_field,
    ))

    registry.register(Tool(
        name="rename_field",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "old_name": {"type": "string"},
                "new_name": {"type": "string"},
            },
            "required": ["layer_name", "old_name", "new_name"],
        },
        handler=_rename_field,
    ))

    registry.register(Tool(
        name="add_table_join",
        input_schema={
            "type": "object",
            "properties": {
                "target_layer": {
                    "type": "string",
                },
                "join_layer": {
                    "type": "string",
                },
                "target_field": {"type": "string"},
                "join_field": {"type": "string"},
                "prefix": {
                    "type": "string",
                },
            },
            "required": ["target_layer", "join_layer", "target_field", "join_field"],
        },
        handler=_add_table_join,
    ))

    registry.register(Tool(
        name="set_layer_metadata",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "title": {"type": "string"},
                "abstract": {"type": "string"},
                "keywords": {"type": "array", "items": {"type": "string"}},
                "rights": {"type": "string"},
            },
            "required": ["layer_name"],
        },
        handler=_set_layer_metadata,
    ))

    registry.register(Tool(
        name="set_field_aliases",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "aliases": {
                    "type": "object", "additionalProperties": {"type": "string"},
                },
            },
            "required": ["layer_name", "aliases"],
        },
        handler=_set_field_aliases,
    ))

    registry.register(Tool(
        name="duplicate_layer",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "new_name": {"type": "string"},
            },
            "required": ["layer_name"],
        },
        handler=_duplicate_layer,
    ))





def _units(crs) -> str:
    try:
        return QgsUnitTypes.toString(crs.mapUnits())
    except Exception:  # nosec B110 - canvas refresh is optional
        return "unknown"


def _layer(name: str):
    layer = resolve_layer(name)
    if layer is None:
        return None, layer_not_found(name)
    return layer, None


def _vector(name: str):
    layer, error = _layer(name)
    if error:
        return None, error
    if not is_vector(layer):
        return None, tool_error(
            f"Layer {layer.name()!r} is not a vector layer.",
            "INVALID_ARGS",
            "list_layers shows each layer's type; pass a vector layer.",
        )
    return layer, None


def _raster(name: str):
    layer, error = _layer(name)
    if error:
        return None, error
    if not is_raster(layer):
        return None, tool_error(
            f"Layer {layer.name()!r} is not a raster layer.",
            "INVALID_ARGS",
            "list_layers shows each layer's type; pass a raster layer. Vector layers use set_layer_style.",
        )
    return layer, None


def _field_error(layer, field_name: str) -> dict:
    from .core_tools import _field_not_found_error

    return _field_not_found_error(layer, field_name)


def _expand(path: str) -> str:
    return os.path.normpath(os.path.abspath(os.path.expanduser(os.path.expandvars(path))))


def _refresh(layer):
    layer.triggerRepaint()
    try:
        from qgis.utils import iface

        if iface is not None:
            iface.layerTreeView().refreshLayerSymbology(layer.id())
            iface.mapCanvas().refresh()
    except Exception:  # nosec B110 - canvas refresh is optional
        pass


def _pick(mapping: dict, key, label: str):
    value = mapping.get(key)
    if value is None:
        return None, tool_error(
            f"Unknown {label}: {key!r}.",
            "INVALID_ARGS",
            f"Use one of {sorted(mapping)}.",
        )
    return value, None





def _color_ramp(name: str, fallback: str = "Viridis"):
    style = QgsStyle.defaultStyle()
    return style.colorRamp(name) or style.colorRamp(fallback)


def _band_range(provider, band: int, min_value, max_value):
    """A band's min and max, from the arguments or the band statistics."""
    if min_value is not None and max_value is not None:
        return float(min_value), float(max_value)
    stats = provider.bandStatistics(band, RASTER_STATS_ALL, QgsRectangle(), _STATS_SAMPLE)
    lo = stats.minimumValue if min_value is None else float(min_value)
    hi = stats.maximumValue if max_value is None else float(max_value)
    return float(lo), float(hi)


def _contrast_enhancement(provider, band: int, lo: float, hi: float, algorithm):
    enhancement = QgsContrastEnhancement(provider.dataType(band))
    enhancement.setContrastEnhancementAlgorithm(algorithm)
    enhancement.setMinimumValue(lo)
    enhancement.setMaximumValue(hi)
    return enhancement


def _set_raster_style(args: dict) -> dict:
    layer, error = _raster(args["layer_name"])
    if error:
        return error
    provider = layer.dataProvider()
    band_count = provider.bandCount()
    style_type = args["style_type"]

    def check_band(value, label):
        b = int(value)
        if not 1 <= b <= band_count:
            return None, tool_error(
                f"{label}={b} is out of range: {layer.name()!r} has {band_count} band(s).",
                "INVALID_ARGS",
                f"Pass a band between 1 and {band_count}.",
            )
        return b, None

    min_value, max_value = args.get("min_value"), args.get("max_value")
    applied: dict = {"style_type": style_type}

    if style_type == "singleband_pseudocolor":
        band, error = check_band(args.get("band", 1), "band")
        if error:
            return error
        lo, hi = _band_range(provider, band, min_value, max_value)
        interpolation, error = _pick(_SHADER_INTERPOLATION, args.get("interpolation", "interpolated"), "interpolation")
        if error:
            return error
        classification, error = _pick(
            _SHADER_CLASSIFICATION, args.get("classification", "continuous"), "classification",
        )
        if error:
            return error




        classes = int(args.get("classes") or 5)
        if classes < 2 or classes > _MAX_RASTER_CLASSES:
            return tool_error(
                f"classes={classes} is outside 2 to {_MAX_RASTER_CLASSES}.", "INVALID_ARGS",
                f"A colour ramp past {_MAX_RASTER_CLASSES} classes is not readable on a map; "
                "5 to 12 is the usual range.")
        ramp_name = args.get("color_ramp") or "Viridis"
        ramp = _color_ramp(ramp_name)
        if ramp is None:
            return tool_error(
                f"Color ramp not found: {ramp_name!r}.", "INVALID_ARGS",
                "Use a QGIS ramp name such as Viridis, Spectral, RdYlGn, Blues.",
            )
        shader_fn = QgsColorRampShader(lo, hi, ramp, interpolation, classification)
        shader_fn.classifyColorRamp(classes, band, QgsRectangle(), provider)
        shader = QgsRasterShader()
        shader.setRasterShaderFunction(shader_fn)
        renderer = QgsSingleBandPseudoColorRenderer(provider, band, shader)
        with_range = getattr(renderer, "setClassificationMin", None)
        if callable(with_range):
            renderer.setClassificationMin(lo)
            renderer.setClassificationMax(hi)
        applied.update({"band": band, "min": lo, "max": hi, "color_ramp": ramp_name, "classes": classes})
    elif style_type == "singleband_gray":
        band, error = check_band(args.get("band", 1), "band")
        if error:
            return error
        lo, hi = _band_range(provider, band, min_value, max_value)
        gradient, error = _pick(_GRAY_GRADIENTS, args.get("gradient", "black_to_white"), "gradient")
        if error:
            return error
        contrast, error = _pick(_CONTRAST, args.get("contrast", "stretch"), "contrast")
        if error:
            return error
        renderer = QgsSingleBandGrayRenderer(provider, band)
        renderer.setGradient(gradient)
        renderer.setContrastEnhancement(_contrast_enhancement(provider, band, lo, hi, contrast))
        applied.update({
            "band": band, "min": lo, "max": hi,
            "gradient": args.get("gradient", "black_to_white"),
            "contrast": args.get("contrast", "stretch"),
        })
    elif style_type == "multiband_color":
        bands = []
        for key, default in (("red_band", 1), ("green_band", 2), ("blue_band", 3)):
            band, error = check_band(args.get(key, default), key)
            if error:
                return error
            bands.append(band)
        contrast, error = _pick(_CONTRAST, args.get("contrast", "stretch"), "contrast")
        if error:
            return error
        renderer = QgsMultiBandColorRenderer(provider, *bands)
        setters = (
            renderer.setRedContrastEnhancement,
            renderer.setGreenContrastEnhancement,
            renderer.setBlueContrastEnhancement,
        )
        ranges = []
        for setter, band in zip(setters, bands):
            lo, hi = _band_range(provider, band, min_value, max_value)
            setter(_contrast_enhancement(provider, band, lo, hi, contrast))
            ranges.append({"band": band, "min": lo, "max": hi})
        applied.update({"bands": ranges, "contrast": args.get("contrast", "stretch")})
    elif style_type == "hillshade":
        band, error = check_band(args.get("band", 1), "band")
        if error:
            return error
        azimuth = float(args.get("azimuth", 315.0))
        altitude = float(args.get("altitude", 45.0))
        z_factor = float(args.get("z_factor", 1.0))
        renderer = QgsHillshadeRenderer(provider, band, azimuth, altitude)
        renderer.setZFactor(z_factor)
        applied.update({"band": band, "azimuth": azimuth, "altitude": altitude, "z_factor": z_factor})
    else:
        return tool_error(f"Unknown style_type: {style_type!r}.", "INVALID_ARGS", f"Use one of {list(RASTER_STYLES)}.")

    layer.setRenderer(renderer)





    if args.get("opacity") is not None:
        try:
            opacity = float(args["opacity"])
        except (TypeError, ValueError):
            return tool_error("opacity must be a number between 0 and 1.", "INVALID_ARGS",
                              "Pass 0.65 for a surface you want the basemap to read through.")
        if not 0.0 <= opacity <= 1.0:
            return tool_error(f"opacity={opacity} is outside 0 to 1.", "INVALID_ARGS",
                              "1 is opaque, 0 invisible; 0.6 to 0.8 lets a basemap through.")
        renderer.setOpacity(opacity)
        applied["opacity"] = opacity
    _refresh(layer)
    return {"layer_id": layer.id(), "name": layer.name(), "applied": applied, "value_units": "raster band values"}





def _apply_style_qml(args: dict) -> dict:
    layer, error = _layer(args["layer_name"])
    if error:
        return error
    path = _expand(args["path"])
    if not os.path.isfile(path):
        return tool_error(
            f"Style file not found: {path}", "INVALID_ARGS",
            "Pass the path of an existing .qml file, or save one with save_style_qml.",
        )



    from qgis.PyQt.QtXml import QDomDocument

    before = QDomDocument()
    kept = ""
    try:
        layer.exportNamedStyle(before)
        kept = before.toString()
    except Exception:  # nosec B110 - the restore is a courtesy, the load still runs
        kept = ""

    message, success = layer.loadNamedStyle(path)
    if not success:
        if kept:
            restore = QDomDocument()
            if restore.setContent(kept):
                layer.importNamedStyle(restore)
                _refresh(layer)
        return tool_error(
            f"Failed to apply style: {message}", "STYLE_FAILED",
            "The QML must match the layer type (vector QML on a vector layer). "
            "The layer keeps the style it had.",
        )
    _refresh(layer)
    out = {"layer_id": layer.id(), "name": layer.name(), "path": path, "message": message}



    if message and message.strip() and "loaded" not in message.lower():
        out["partial"] = message.strip()
    return out


def _save_style_qml(args: dict) -> dict:
    layer, error = _layer(args["layer_name"])
    if error:
        return error
    path = _expand(args["path"])
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    message, success = layer.saveNamedStyle(path)
    if not success:
        return tool_error(f"Failed to save style: {message}", "STYLE_FAILED", "Pass a writable path ending in .qml.")
    return {
        "layer_id": layer.id(),
        "name": layer.name(),
        "path": path,
        "file_size": os.path.getsize(path) if os.path.exists(path) else None,
        "size_units": "bytes",
    }





def _to_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "y", "on")
    return bool(value)


def _set_layer_property(args: dict) -> dict:
    layer, error = _layer(args["layer_name"])
    if error:
        return error
    prop = args["property"]
    value = args["value"]
    try:
        if prop == "opacity":
            opacity = float(value)
            if not 0.0 <= opacity <= 1.0:
                return tool_error(
                    f"opacity {opacity} is outside 0.0-1.0.", "INVALID_ARGS",
                    "Pass a value between 0 (transparent) and 1 (opaque).",
                )
            layer.setOpacity(opacity)
            applied = opacity
        elif prop == "name":
            layer.setName(str(value))
            applied = str(value)
        elif prop == "scale_visibility":
            applied = _to_bool(value)
            layer.setScaleBasedVisibility(applied)
        elif prop == "min_scale":
            applied = float(value)
            layer.setMinimumScale(applied)
        elif prop == "max_scale":
            applied = float(value)
            layer.setMaximumScale(applied)
        else:
            return tool_error(f"Unknown property: {prop!r}.", "INVALID_ARGS", f"Use one of {list(LAYER_PROPERTIES)}.")
    except (TypeError, ValueError) as e:
        return tool_error(
            f"Bad value for {prop}: {e}", "INVALID_ARGS",
            "opacity, min_scale and max_scale take numbers; scale_visibility takes true or false.",
        )
    _refresh(layer)
    return {"layer_id": layer.id(), "name": layer.name(), "property": prop, "value": applied}


def _set_layer_order(args: dict) -> dict:
    """The whole order of a group, or one layer moved relative to the others."""









    if args.get("layer_name") or args.get("position"):
        return _move_one_layer(args)
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    nodes = []
    resolved = []
    for name in args.get("layers") or []:
        layer, error = _layer(name)
        if error:
            return error
        node = root.findLayer(layer.id())
        if node is None:
            return tool_error(
                f"Layer {layer.name()!r} has no node in the layer tree.", "INVALID_ARGS",
                "get_layer_tree shows the tree; non-spatial tables have no node.",
            )
        if node in nodes:
            return tool_error(f"Layer {layer.name()!r} is listed twice.", "INVALID_ARGS", "List each layer once.")
        nodes.append(node)
        resolved.append(layer)
    if not nodes:
        return tool_error("Nothing to reorder.", "INVALID_ARGS",
                          "For one layer, pass layer_name and position (top, bottom, above, below). "
                          "For a whole group, pass layers from top to bottom.")
    parent = nodes[0].parent()
    for node in nodes[1:]:
        if node.parent() is not parent:
            return tool_error(
                "The list form reorders one group; these layers are in different groups.",
                "INVALID_ARGS",
                "Move one layer at a time instead: set_layer_order with layer_name and "
                "position top, bottom, above or below, which works across groups.",
            )
    siblings = list(parent.children())
    slots = sorted(siblings.index(n) for n in nodes)
    new_order = list(siblings)
    for slot, node in zip(slots, nodes):
        new_order[slot] = node
    clones = [n.clone() for n in new_order]
    parent.insertChildNodes(0, clones)
    for node in siblings:
        parent.removeChildNode(node)
    root.setHasCustomLayerOrder(False)
    return {
        "order": [layer.name() for layer in resolved],
        "layer_ids": [layer.id() for layer in resolved],
        "group": parent.name() if hasattr(parent, "name") and parent is not root else "(root)",
        "custom_draw_order_cleared": True,
    }


def _move_one_layer(args: dict) -> dict:
    """One layer to the top, the bottom, or next to another one. Main thread only."""
    name = str(args.get("layer_name") or "").strip()
    if not name:
        return tool_error("layer_name is empty.", "INVALID_ARGS",
                          "Name the layer to move, or pass layers for a whole order.")
    position = str(args.get("position") or "").strip().lower()
    if position not in ("top", "bottom", "above", "below"):
        return tool_error("position is top, bottom, above or below.", "INVALID_ARGS",
                          "above and below also need reference_layer.")
    layer, error = _layer(name)
    if error:
        return error
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    node = root.findLayer(layer.id())
    if node is None:
        return tool_error(f"Layer {layer.name()!r} has no node in the layer tree.", "INVALID_ARGS",
                          "get_layer_tree shows the tree; non-spatial tables have no node.")
    parent = node.parent() or root
    reference = str(args.get("reference_layer") or "").strip()

    if position in ("above", "below"):
        if not reference:
            return tool_error(f"{position} needs reference_layer.", "INVALID_ARGS",
                              "Name the layer to sit next to, or use top or bottom.")
        other, error = _layer(reference)
        if error:
            return error
        if other.id() == layer.id():
            return tool_error("A layer cannot be moved relative to itself.", "INVALID_ARGS",
                              "Name another layer, or use top or bottom.")
        other_node = root.findLayer(other.id())
        if other_node is None:
            return tool_error(f"Layer {other.name()!r} has no node in the layer tree.", "INVALID_ARGS",
                              "Order the layer against one that is in the tree.")
        parent = other_node.parent() or root
        siblings = [child for child in parent.children() if child is not node]
        index = siblings.index(other_node)
        target = index if position == "above" else index + 1
    else:
        siblings = [child for child in parent.children() if child is not node]
        target = 0 if position == "top" else len(siblings)

    moved = layer_order.move_to_index(node, parent, target)
    root.setHasCustomLayerOrder(False)
    out = {"layer": layer.name(), "layer_id": layer.id(), "requested": position, "moved": moved,
           "group": parent.name() if hasattr(parent, "name") and parent is not root else "(root)"}
    if reference and position in ("above", "below"):
        out["reference_layer"] = reference


    out.update(layer_order.cover_report(layer, root))
    return out


def _crs_info(layer) -> dict:
    crs = layer.crs()
    proj = crs.toProj() if hasattr(crs, "toProj") else crs.toProj4()
    return {
        "layer_id": layer.id(),
        "name": layer.name(),
        "authid": crs.authid(),
        "description": crs.description(),
        "is_geographic": crs.isGeographic(),
        "units": _units(crs),
        "proj": proj,
    }


def _get_layer_crs(args: dict) -> dict:
    layer, error = _layer(args["layer_name"])
    if error:
        return error
    return _crs_info(layer)


def _set_layer_crs(args: dict) -> dict:
    layer, error = _layer(args["layer_name"])
    if error:
        return error
    wanted = str(args["crs"]).strip()
    new_crs = QgsCoordinateReferenceSystem(wanted)
    if not new_crs.isValid() and wanted.startswith("+proj="):





        try:
            new_crs = QgsCoordinateReferenceSystem.fromProj(wanted)
        except AttributeError:
            built = QgsCoordinateReferenceSystem()
            for method in ("createFromProj", "createFromProj4"):
                maker = getattr(built, method, None)
                if maker is not None and maker(wanted):
                    break
            new_crs = built
    if not new_crs.isValid():
        return tool_error(
            f"Invalid CRS: {args['crs']}", "CRS_INVALID",
            "Pass an authority id such as EPSG:4326 or EPSG:32632, or a proj4 string "
            "starting with +proj= for a projection that has no code.",
        )



    implausible = crs_plausibility(layer, new_crs)
    if implausible:
        return implausible
    before = layer.crs().authid()
    layer.setCrs(new_crs)
    _refresh(layer)
    out = _crs_info(layer)
    out["previous_authid"] = before
    out["note"] = (
        "Declaration changed only; the coordinates were not reprojected. "
        "To reproject, run_processing native:reprojectlayer."
    )
    return out





def _delete_field(args: dict) -> dict:
    layer, error = _vector(args["layer_name"])
    if error:
        return error
    field_name = args["field_name"]
    idx = layer.fields().indexOf(field_name)
    if idx < 0:
        return _field_error(layer, field_name)
    if layer.isEditable():
        return tool_error(
            f"Layer {layer.name()!r} has an open edit session.", "INVALID_ARGS",
            "Commit or roll back with qgis_edit_commit / qgis_edit_rollback first.",
        )
    if not layer.dataProvider().deleteAttributes([idx]):
        return tool_error(
            f"The provider refused to delete field {field_name!r}.",
            "INVALID_ARGS",
            "The data source may be read-only or not support schema changes; "
            "export_layer to GeoPackage and retry on the copy.",
        )
    layer.updateFields()
    return {
        "layer_id": layer.id(),
        "name": layer.name(),
        "deleted": field_name,
        "fields": [f.name() for f in layer.fields()],
    }


def _rename_field(args: dict) -> dict:
    layer, error = _vector(args["layer_name"])
    if error:
        return error
    old_name, new_name = args["old_name"], args["new_name"]
    idx = layer.fields().indexOf(old_name)
    if idx < 0:
        return _field_error(layer, old_name)
    if layer.fields().indexOf(new_name) >= 0:
        return tool_error(
            f"Field {new_name!r} already exists on {layer.name()!r}.", "INVALID_ARGS",
            "Pick a name that is not taken.",
        )
    if layer.isEditable():
        return tool_error(
            f"Layer {layer.name()!r} has an open edit session.", "INVALID_ARGS",
            "Commit or roll back with qgis_edit_commit / qgis_edit_rollback first.",
        )
    if not layer.dataProvider().renameAttributes({idx: new_name}):
        return tool_error(
            f"The provider refused to rename field {old_name!r}.",
            "INVALID_ARGS",
            "Shapefiles cap names at 10 characters and some providers cannot rename; "
            "export_layer to GeoPackage and retry.",
        )
    layer.updateFields()
    return {
        "layer_id": layer.id(),
        "name": layer.name(),
        "old_name": old_name,
        "new_name": new_name,
        "fields": [f.name() for f in layer.fields()],
    }


def _add_table_join(args: dict) -> dict:
    target, error = _vector(args["target_layer"])
    if error:
        return error
    join, error = _vector(args["join_layer"])
    if error:
        return error
    target_field, join_field = args["target_field"], args["join_field"]
    if target.fields().indexOf(target_field) < 0:
        return _field_error(target, target_field)
    if join.fields().indexOf(join_field) < 0:
        return _field_error(join, join_field)
    before = {f.name() for f in target.fields()}
    info = QgsVectorLayerJoinInfo()
    info.setTargetFieldName(target_field)
    info.setJoinLayerId(join.id())
    info.setJoinLayer(join)
    info.setJoinFieldName(join_field)
    info.setUsingMemoryCache(True)
    prefix = args.get("prefix")
    if prefix:
        info.setPrefix(prefix)
    if not target.addJoin(info):
        return tool_error(
            "Failed to add the table join.",
            "JOIN_FAILED",
            "A join on the same layer may already exist; the key fields must hold the same kind of value.",
        )
    target.updateFields()
    added = [f.name() for f in target.fields() if f.name() not in before]
    return {
        "layer_id": target.id(),
        "name": target.name(),
        "join_layer_id": join.id(),
        "joined_fields": added,
        "field_count": len(added),
        "count_units": "fields",
    }





_MAX_ABSTRACT_CHARS = 2000
_MAX_KEYWORDS = 30


def _set_layer_metadata(args: dict) -> dict:
    layer, error = _layer(args["layer_name"])
    if error:
        return error
    metadata = layer.metadata()
    applied: dict = {}
    title = args.get("title")
    if isinstance(title, str) and title.strip():
        metadata.setTitle(title.strip()[:200])
        applied["title"] = metadata.title()
    abstract = args.get("abstract")
    if isinstance(abstract, str) and abstract.strip():
        metadata.setAbstract(abstract.strip()[:_MAX_ABSTRACT_CHARS])
        applied["abstract_chars"] = len(metadata.abstract())
    keywords = args.get("keywords")
    if isinstance(keywords, list):
        words = [str(k).strip()[:60] for k in keywords if str(k).strip()][:_MAX_KEYWORDS]
        if words:
            metadata.setKeywords({"keywords": words})
            applied["keywords"] = words
    rights = args.get("rights")
    if isinstance(rights, str) and rights.strip():
        metadata.setRights([rights.strip()[:500]])
        applied["rights"] = metadata.rights()
    if not applied:
        return tool_error("Nothing to set.", "INVALID_ARGS", "Pass title, abstract, keywords or rights.")
    if not metadata.identifier():
        metadata.setIdentifier(layer.id())
    if not metadata.type():
        metadata.setType("dataset")
    layer.setMetadata(metadata)
    return {"layer_id": layer.id(), "name": layer.name(), "applied": applied,
            "note": "Kept in the project file; save_layer_to_gpkg carries it into the GeoPackage."}


def _set_field_aliases(args: dict) -> dict:
    layer, error = _layer(args["layer_name"])
    if error:
        return error
    if not isinstance(layer, QgsVectorLayer):
        return tool_error("Only vector layers have fields.", "INVALID_ARGS", "Pick a vector layer.")
    aliases = args.get("aliases")
    if not isinstance(aliases, dict) or not aliases:
        return tool_error("aliases must map field names to aliases.", "INVALID_ARGS",
                          "Example: {'POP_2021': 'Population 2021'}.")
    fields = layer.fields()
    applied: dict = {}
    unknown: list[str] = []
    for name, alias in aliases.items():
        index = fields.indexOf(str(name))
        if index < 0:
            unknown.append(str(name))
            continue
        layer.setFieldAlias(index, str(alias or "").strip()[:100])
        applied[str(name)] = layer.attributeAlias(index)
    if not applied:
        return tool_error(f"No such field: {', '.join(unknown)}.", "INVALID_ARGS",
                          f"Fields: {', '.join(f.name() for f in fields)[:600]}.")
    out = {"layer_id": layer.id(), "name": layer.name(), "aliases": applied}
    if unknown:
        out["unknown_fields"] = unknown
    return out


def _duplicate_layer(args: dict) -> dict:
    layer, error = _layer(args["layer_name"])
    if error:
        return error
    if isinstance(layer, QgsVectorLayer) and layer.providerType() == "memory":




        count = layer.featureCount()


        ceiling = min(limits.current("MAX_FEATURES_MATERIALISED"), limits.current("MAX_FEATURES_CREATED"))
        if count is not None and count > ceiling:
            return limits.refusal(
                f"The layer {layer.name()!r}", f"{count:,} features",
                f"{ceiling:,} for a copy made in memory",
                "Save it to a file first with export_layer or save_layer_to_gpkg, which streams, "
                "then add the file. A copy this size in memory is minutes of frozen QGIS.")
    clone = layer.clone()
    clone.setName(args.get("new_name") or f"{layer.name()} copy")
    if isinstance(layer, QgsVectorLayer) and layer.providerType() == "memory" and clone.featureCount() == 0:







        batch: list = []
        for feature in layer.getFeatures():
            batch.append(feature)
            if len(batch) >= 5_000:
                clone.dataProvider().addFeatures(batch)
                batch = []
        if batch:
            clone.dataProvider().addFeatures(batch)
        clone.updateExtents()
    QgsProject.instance().addMapLayer(clone)
    out = {
        "layer_id": clone.id(),
        "name": clone.name(),
        "source_layer_id": layer.id(),
        "crs": clone.crs().authid(),
        "units": _units(clone.crs()),
    }
    if isinstance(clone, QgsVectorLayer):
        out["feature_count"] = clone.featureCount()
        out["count_units"] = "features"
    return out
