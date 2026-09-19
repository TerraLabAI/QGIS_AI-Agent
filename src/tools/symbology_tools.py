# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








































from __future__ import annotations

import enum
import math
import re

from qgis.core import QgsApplication, QgsRasterLayer, QgsVectorLayer
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core.logger import log_warning
from ..core.qt_compat import enum_member
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from ._compat import CONTRAST_NONE, CONTRAST_STRETCH_MINMAX
from .colour_text import qcolor_from_text
from .layer_lookup import _find_layer, _layer_not_found_error
from .style_tools import _expression_error, _previous_style_keys, _style_to_put_back, _wider_than_the_selection

BLEND_MODES = ("normal", "multiply", "screen", "overlay", "darken", "lighten", "dodge", "burn",
               "soft_light", "hard_light", "difference", "subtract", "addition")
RESAMPLING = ("nearest", "bilinear", "cubic")


MIN_MAX = {"cumulative_cut": "CumulativeCut", "min_max": "MinMax", "std_dev": "StdDev"}
MIN_MAX_EXTENT = {"whole_raster": "WholeRaster", "current_canvas": "CurrentCanvas", "updated_canvas": "UpdatedCanvas"}
MIN_MAX_ACCURACY = {"estimate": "Estimated", "actual": "Exact"}
STRETCH = {"stretch_to_min_max": "StretchToMinimumMaximum",
           "stretch_and_clip_to_min_max": "StretchAndClipToMinimumMaximum",
           "clip_to_min_max": "ClipToMinimumMaximum", "no_stretch": "NoEnhancement"}

_RANGED_RENDERERS = ("singlebandgray", "singlebandpseudocolor", "multibandcolor")


_SUB_SYMBOL_DEPTH = 3



_RENDERER_CLASSES = {
    "singleSymbol": "QgsSingleSymbolRenderer",
    "categorizedSymbol": "QgsCategorizedSymbolRenderer",
    "graduatedSymbol": "QgsGraduatedSymbolRenderer",
    "RuleRenderer": "QgsRuleBasedRenderer",
    "pointDisplacement": "QgsPointDisplacementRenderer",
    "pointCluster": "QgsPointClusterRenderer",
    "mergedFeatureRenderer": "QgsMergedFeatureRenderer",
    "invertedPolygonRenderer": "QgsInvertedPolygonRenderer",
    "heatmapRenderer": "QgsHeatmapRenderer",
    "25dRenderer": "Qgs25DRenderer",
    "embeddedSymbol": "QgsEmbeddedSymbolRenderer",
    "nullSymbol": "QgsNullSymbolRenderer",
}

_CLASSED = ("categorizedSymbol", "graduatedSymbol")

_WRAPPERS = ("pointDisplacement", "pointCluster", "mergedFeatureRenderer", "invertedPolygonRenderer",
             "25dRenderer", "embeddedSymbol")


_NO_SYMBOL_LAYERS = ("heatmapRenderer", "25dRenderer")



_OUTER_EFFECTS = ("dropShadow", "outerGlow")
_INNER_EFFECTS = ("innerShadow", "innerGlow")




_VALUE_RANGES = {
    "InterpolatedLine": (
        (("lineStartWidthValue", "lineEndWidthValue"), "width_varying_is_variable_width",
         "width_varying_minimum_value", "width_varying_maximum_value"),
    ),
}

_ENCODED_COLOUR = re.compile(r"^\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(,.*)?$")
_NUMBERS = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*(?:,\s*(\d+(?:\.\d+)?)\s*)?$")
_UNIT_WORDS = {
    "mm": "MM", "millimeter": "MM", "millimeters": "MM", "millimetre": "MM", "millimetres": "MM",
    "pt": "Point", "pts": "Point", "point": "Point", "points": "Point",
    "px": "Pixel", "pixel": "Pixel", "pixels": "Pixel",
    "map": "MapUnit", "mapunit": "MapUnit", "mapunits": "MapUnit", "map_units": "MapUnit",
    "in": "Inch", "inch": "Inch", "inches": "Inch",
    "m": "RenderMetersInMapUnits", "meters": "RenderMetersInMapUnits", "metres": "RenderMetersInMapUnits",
    "meters_at_scale": "RenderMetersInMapUnits", "metersinmapunits": "RenderMetersInMapUnits",
    "%": "Percentage", "percent": "Percentage", "percentage": "Percentage",
}


def register_symbology_tools(registry: ToolRegistry):


    effect = {
        "type": "object",
        "properties": {"type": {"type": "string"}, "properties": {"type": "object"}},
        "required": ["type"],
    }
    symbol_layer = {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "properties": {"type": "object"},
            "data_defined": {"type": "object"},
            "enabled": {"type": "boolean"},
            "effect": {"type": "array", "items": effect},

            "sub_symbol": {
                "type": "object",
                "properties": {"symbol_layers": {"type": "array", "items": {"type": "object"}}},
                "required": ["symbol_layers"],
            },
        },
        "required": ["type"],
    }
    registry.register(Tool(
        name="set_layer_symbology",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Style {layer_name}"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "renderer": {"type": "string"},
                "symbol_layers": {"type": "array", "items": symbol_layer},
                "rules": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "filter": {"type": "string"},
                            "label": {"type": "string"},
                            "symbol_layers": {"type": "array", "items": symbol_layer},
                            "min_scale": {"type": "number", "minimum": 0},
                            "max_scale": {"type": "number", "minimum": 0},
                        },
                    },
                },
                "renderer_options": {"type": "object"},
                "effect": {"type": "array", "items": effect},
                "blend_mode": {"type": "string", "enum": list(BLEND_MODES)},
                "feature_blend_mode": {"type": "string", "enum": list(BLEND_MODES)},
                "opacity": {"type": "number", "minimum": 0, "maximum": 1},
                "resampling": {"type": "string", "enum": list(RESAMPLING)},
                "resampling_zoomed_out": {"type": "string", "enum": list(RESAMPLING)},
                "brightness": {"type": "integer", "minimum": -255, "maximum": 255},
                "contrast": {"type": "integer", "minimum": -100, "maximum": 100},
                "saturation": {"type": "integer", "minimum": -100, "maximum": 100},
                "gamma": {"type": "number", "minimum": 0.1, "maximum": 10},
                "min_max": {"type": "string", "enum": list(MIN_MAX)},
                "cumulative_cut": {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 100},
                                   "minItems": 2, "maxItems": 2},
                "std_dev_factor": {"type": "number", "exclusiveMinimum": 0, "maximum": 10},
                "min_max_extent": {"type": "string", "enum": list(MIN_MAX_EXTENT)},
                "min_max_accuracy": {"type": "string", "enum": list(MIN_MAX_ACCURACY)},
                "stretch": {"type": "string", "enum": list(STRETCH)},
            },
            "required": ["layer_name"],


            "x-sub-symbol": True,
        },
        handler=_set_layer_symbology,
    ))




def _qcolor(value):




    from qgis.core import QgsSymbolLayerUtils
    from qgis.PyQt.QtGui import QColor

    text = str(value or "").strip()
    if not text:
        return None
    match = _NUMBERS.match(text)
    if match:
        r, g, b, a = match.groups()
        numbers = [float(r), float(g), float(b)]
        if any(n > 255 for n in numbers):
            return None
        alpha = 255.0 if a is None else float(a)
        if a is not None and "." in a and alpha <= 1.0:
            alpha *= 255.0
        if alpha > 255:
            return None
        return QColor(int(numbers[0]), int(numbers[1]), int(numbers[2]), int(round(alpha)))
    if _ENCODED_COLOUR.match(text):
        colour = QgsSymbolLayerUtils.decodeColor(text)
        return colour if colour.isValid() else None
    colour = qcolor_from_text(text)
    return colour if colour.isValid() else None


def _render_unit(value):

    from qgis.core import QgsUnitTypes

    text = str(value or "").strip()
    unit, ok = QgsUnitTypes.decodeRenderUnit(text)
    if ok:
        return unit, True
    alias = _UNIT_WORDS.get(text.lower().replace(" ", "_")) or _UNIT_WORDS.get(text.lower().replace(" ", ""))
    if alias:
        return QgsUnitTypes.decodeRenderUnit(alias)
    return unit, False


def _norm(name) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _as_property(value, default):

    if isinstance(value, bool):
        value = 1 if value else 0
    if isinstance(default, str):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        if isinstance(value, (list, tuple)) and value and all(
                isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):

            return ",".join(_as_property(v, default) for v in value)
        return str(value) if not isinstance(value, (dict, list)) else value
    if isinstance(default, dict) and isinstance(value, dict):
        merged = dict(default)
        merged.update(value)
        return merged
    return value


def _encode_value(key: str, value, default) -> tuple:

    from qgis.core import QgsSymbolLayerUtils, QgsUnitTypes

    if isinstance(default, str) and _ENCODED_COLOUR.match(default) and not isinstance(value, (dict, list)):
        colour = _qcolor(value)
        if colour is None:
            return None, (f"{key}: {value!r} is not a colour: '#rrggbb', '#rrggbbaa' (alpha last), a name "
                          "such as 'steelblue', or 'r,g,b,a' with alpha 0 to 255.")
        return QgsSymbolLayerUtils.encodeColor(colour), None
    if key.endswith("_unit") and isinstance(default, str) and QgsUnitTypes.decodeRenderUnit(default)[1]:
        unit, ok = _render_unit(value)
        if not ok:
            return None, (f"{key}: {value!r} is not a unit: MM, Point, Pixel, MapUnit, Inch or "
                          "RenderMetersInMapUnits.")
        return QgsUnitTypes.encodeUnit(unit), None
    return _as_property(value, default), None


def _merged_properties(what: str, defaults: dict, given) -> tuple:

    if given in (None, ""):
        return dict(defaults), None
    if not isinstance(given, dict):
        return None, tool_error(f"{what}: properties must be an object of QGIS property keys.", "INVALID_ARGS")
    known = {key.lower(): key for key in defaults}
    merged = dict(defaults)
    unknown = []
    for key, value in given.items():
        real = known.get(str(key).lower())
        if real is None:
            unknown.append(str(key))
            continue
        encoded, problem = _encode_value(real, value, defaults[real])
        if problem:
            return None, tool_error(f"{what}: {problem}", "INVALID_ARGS")
        merged[real] = encoded
    if unknown:

        listing = ", ".join(f"{k}={_short(v)}" for k, v in sorted(defaults.items()) if not k.endswith("unit_scale"))
        return None, tool_error(
            f"{what} has no property {', '.join(unknown)}. Nothing was changed.", "INVALID_ARGS",
            f"Its keys, with their defaults: {listing}")
    return merged, None


def _short(value) -> str:
    text = str(value)
    return text if len(text) <= 40 else text[:37] + "..."









_LEGACY_ALIASES = {
    "SimpleLine": {
        "color": "line_color", "width": "line_width", "width_unit": "line_width_unit", "style": "line_style",
    },
    "SimpleFill": {


        "color_border": "outline_color", "style_border": "outline_style", "width_border": "outline_width",
        "line_color": "outline_color", "line_style": "outline_style", "line_width": "outline_width",
        "line_width_unit": "outline_width_unit",
    },
    "ShapeburstFill": {
        "shade_distance": "max_distance",
    },

    "InterpolatedLine": {
        "color": "single_color", "width": "line_width", "width_unit": "line_width_unit",
    },
}







_SHAPEBURST_TWO_COLOUR = {"color1": "color", "color2": "gradient_color2"}
_SHAPEBURST_TWO_COLOUR_TYPES = {"", "0", "simpletwocolor", "twocolor", "twocolour"}


def _apply_property_aliases(type_name: str, given) -> tuple:

    if not isinstance(given, dict) or not given:
        return given, {}
    lower = {str(k).lower(): k for k in given}
    out = dict(given)
    mapped: dict = {}

    def rename(alias_key: str, real_key: str) -> None:
        if alias_key in lower and real_key.lower() not in lower:
            original = lower[alias_key]
            out[real_key] = out.pop(original)
            mapped[original] = real_key

    for alias_key, real_key in _LEGACY_ALIASES.get(type_name, {}).items():
        rename(alias_key, real_key)
    if type_name == "ShapeburstFill":
        raw_type = given[lower["color_type"]] if "color_type" in lower else ""
        if _norm(raw_type) in _SHAPEBURST_TWO_COLOUR_TYPES:
            for alias_key, real_key in _SHAPEBURST_TWO_COLOUR.items():
                rename(alias_key, real_key)
    return out, mapped


def _stable_key(key: str, default) -> bool:






    if key.endswith("_unit"):
        return True
    if isinstance(default, str) and _ENCODED_COLOUR.match(default):
        return True
    try:
        float(default)
        return True
    except (TypeError, ValueError):
        return False


def _same_value(read_back, sent) -> bool:






    if str(read_back) == str(sent):
        return True
    if isinstance(read_back, str) and isinstance(sent, str) and _ENCODED_COLOUR.match(read_back) \
            and _ENCODED_COLOUR.match(sent):
        from qgis.core import QgsSymbolLayerUtils

        before, after = QgsSymbolLayerUtils.decodeColor(sent), QgsSymbolLayerUtils.decodeColor(read_back)
        return before.isValid() and after.isValid() and before.rgba() == after.rgba()
    try:
        return math.isclose(float(read_back), float(sent), rel_tol=1e-6, abs_tol=1e-9)
    except (TypeError, ValueError):
        return False




def _effect_stack(specs, where: str) -> tuple:

    if not specs:
        return None, [], None
    if isinstance(specs, dict):
        specs = [specs]
    registry = QgsApplication.paintEffectRegistry()
    by_name = {_norm(name): name for name in registry.effects()}
    effects = []
    for spec in specs:
        name = by_name.get(_norm(spec.get("type") if isinstance(spec, dict) else spec))
        if name is None or name == "effectStack":
            valid = [n for n in registry.effects() if n != "effectStack"]
            asked = spec.get("type") if isinstance(spec, dict) else spec
            return None, [], tool_error(f"{where}: unknown effect {asked!r}. Nothing was changed.",
                                        "INVALID_ARGS", f"Effects: {', '.join(valid)}.")
        defaults = registry.createEffect(name, {}).properties()
        props, error = _merged_properties(f"{where} effect {name}", defaults,
                                          spec.get("properties") if isinstance(spec, dict) else None)
        if error:
            return None, [], error
        effects.append((name, props))
    names = [name for name, _ in effects]
    if "drawSource" not in names and any(n in _OUTER_EFFECTS + _INNER_EFFECTS for n in names):

        position = max((i + 1 for i, n in enumerate(names) if n in _OUTER_EFFECTS), default=0)
        effects.insert(position, ("drawSource", {}))
    from qgis.core import QgsEffectStack

    stack = QgsEffectStack()
    for name, props in effects:
        stack.appendEffect(registry.createEffect(name, props))
    return stack, [name for name, _ in effects], None




def _symbol_type(layer):
    from qgis.core import QgsSymbol

    symbol = QgsSymbol.defaultSymbol(layer.geometryType())
    return (symbol.type(), symbol) if symbol is not None else (None, None)


def _allowed_types(symbol_type) -> list:

    from qgis.core import Qgis, QgsSymbol

    registry = QgsApplication.symbolLayerRegistry()
    names = list(registry.symbolLayersForType(symbol_type))
    fill = enum_member(Qgis, "SymbolType", "Fill", None) or enum_member(QgsSymbol, "SymbolType", "Fill")
    line = enum_member(Qgis, "SymbolType", "Line", None) or enum_member(QgsSymbol, "SymbolType", "Line")
    if symbol_type == fill:
        names += [n for n in registry.symbolLayersForType(line) if n not in names]
    return names


def _main_colour_keys(type_name: str, defaults: dict) -> set:

    from qgis.PyQt.QtGui import QColor

    probe = QgsApplication.symbolLayerRegistry().createSymbolLayer(type_name, dict(defaults))
    if probe is None:
        return set()
    probe.setColor(QColor(1, 2, 3, 4))
    after = probe.properties()
    return {key for key, value in after.items() if str(defaults.get(key)) != str(value)}


def _dd_definitions() -> dict:
    from qgis.core import QgsSymbolLayer


    member = getattr(QgsSymbolLayer, "Property", None)

    def as_key(key):
        try:
            return member(key) if isinstance(member, type) and issubclass(member, enum.Enum) else key
        except ValueError:
            return key

    return {_norm(d.name()): (as_key(key), d.name()) for key, d in QgsSymbolLayer.propertyDefinitions().items()}


def _data_defined(layer, where: str, given) -> tuple:

    from qgis.core import QgsProperty

    if not given:
        return [], None
    if not isinstance(given, dict):
        return None, tool_error(f"{where}: data_defined maps a property name to an expression.", "INVALID_ARGS")
    definitions = _dd_definitions()
    fields = {f.name() for f in layer.fields()}
    out = []
    for name, expression in given.items():
        found = definitions.get(_norm(name))
        if found is None:
            names = sorted(d[1] for d in definitions.values())
            return None, tool_error(f"{where}: {name!r} is not a data-defined property. Nothing was changed.",
                                    "INVALID_ARGS", f"Data-defined properties: {', '.join(names)}.")
        text = str(expression if expression is not None else "").strip()
        if not text:
            return None, tool_error(f"{where}: data_defined {name!r} needs an expression or a field name.",
                                    "INVALID_ARGS")
        if text in fields:
            prop = QgsProperty.fromField(text)
        else:
            bad = _expression_error(layer, text, f"data_defined {found[1]}")
            if bad:
                return None, bad
            prop = QgsProperty.fromExpression(text)
        out.append((found[0], found[1], prop, text))
    return out, None


def _range_of(layer, expressions: list) -> tuple:

    from qgis.core import Qgis, QgsAggregateCalculator

    low = enum_member(Qgis, "Aggregate", "Min", None) or enum_member(QgsAggregateCalculator, "Aggregate", "Min")
    high = enum_member(Qgis, "Aggregate", "Max", None) or enum_member(QgsAggregateCalculator, "Aggregate", "Max")
    lows, highs = [], []
    for text in expressions:
        try:
            lo, ok_lo = layer.aggregate(low, text)
            hi, ok_hi = layer.aggregate(high, text)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"set_layer_symbology: range of {text!r} not read: {exc}")
            return None
        if ok_lo and ok_hi and lo is not None and hi is not None:
            try:
                lows.append(float(lo))
                highs.append(float(hi))
            except (TypeError, ValueError):
                return None
    if not lows or not all(map(math.isfinite, lows + highs)):
        return None
    return min(lows), max(highs)


def _symbol_layer(layer, spec, allowed: list, where: str, depth: int = 0) -> tuple:

    registry = QgsApplication.symbolLayerRegistry()
    if not isinstance(spec, dict) or not spec.get("type"):
        return None, None, False, tool_error(f"{where}: each symbol layer needs a type.", "INVALID_ARGS",
                                             f"Types for this layer: {', '.join(allowed)}.")
    by_name = {_norm(name): name for name in allowed}
    for name in allowed:
        metadata = registry.symbolLayerMetadata(name)
        if metadata is not None:
            by_name.setdefault(_norm(metadata.visibleName()), name)
    type_name = by_name.get(_norm(spec["type"]))
    if type_name is None:
        return None, None, False, tool_error(
            f"{where}: {spec['type']!r} is not a symbol layer this layer's geometry takes. Nothing was changed.",
            "INVALID_ARGS", f"Types for this layer: {', '.join(allowed)}.")
    base = registry.createSymbolLayer(type_name, {})
    if base is None:
        return None, None, False, tool_error(f"{where}: QGIS could not create a {type_name}.", "EXECUTION_FAILED")
    defaults = base.properties()
    raw_properties = spec.get("properties")
    aliased_properties, mapped = _apply_property_aliases(type_name, raw_properties)
    props, error = _merged_properties(f"{where} ({type_name})", defaults, aliased_properties)
    if error:
        return None, None, False, error
    dd, error = _data_defined(layer, f"{where} ({type_name})", spec.get("data_defined"))
    if error:
        return None, None, False, error
    given = {str(k).lower() for k in (aliased_properties or {})}
    notes = []
    for dd_names, switch, low_key, high_key in _VALUE_RANGES.get(type_name, ()):
        driven = [text for key, name, _prop, text in dd if name in dd_names]
        if not driven:
            continue
        if switch.lower() not in given:
            props[switch] = _as_property(1, defaults.get(switch))
        if low_key.lower() not in given and high_key.lower() not in given:
            found = _range_of(layer, sorted(set(driven)))
            if found is not None:
                props[low_key] = _as_property(found[0], defaults.get(low_key))
                props[high_key] = _as_property(found[1], defaults.get(high_key))
                notes.append(f"{low_key} and {high_key} read from the layer: {found[0]:g} to {found[1]:g}")
    created = registry.createSymbolLayer(type_name, props)
    if created is None:
        return None, None, False, tool_error(f"{where}: QGIS refused these {type_name} properties.",
                                             "INVALID_ARGS", "Start from the defaults and change one key at a time.")


    known = {key.lower(): key for key in defaults}
    requested_keys = sorted({known[k] for k in given if k in known})
    final = created.properties() if requested_keys else {}
    not_applied = [k for k in requested_keys
                   if _stable_key(k, defaults.get(k)) and not _same_value(final.get(k), props.get(k))]
    for key, _name, prop, _text in dd:
        created.setDataDefinedProperty(key, prop)
    if spec.get("enabled") is not None:
        created.setEnabled(bool(spec["enabled"]))
    stack, effect_names, error = _effect_stack(spec.get("effect"), f"{where} ({type_name})")
    if error:
        return None, None, False, error
    if stack is not None:
        created.setPaintEffect(stack)
    sub_summary, sub_colour, error = _sub_symbol(layer, created, allowed, spec.get("sub_symbol"),
                                                 f"{where} ({type_name})", depth)
    if error:
        return None, None, False, error
    colour_keys = _main_colour_keys(type_name, defaults)

    colour_given = sub_colour or bool(colour_keys & {k for k in defaults if k.lower() in given})
    summary = {"type": type_name}
    if raw_properties:
        summary["properties"] = {k: final.get(k) for k in requested_keys}
    if mapped:
        summary["mapped"] = mapped
    if not_applied:
        summary["not_applied"] = not_applied
    if dd:
        summary["data_defined"] = {name: text for _key, name, _prop, text in dd}
    if effect_names:
        summary["effect"] = effect_names
    if sub_summary:
        summary["sub_symbol"] = sub_summary
    if notes:
        summary["defaults_applied"] = notes
    return created, summary, colour_given, None


def _sub_symbol(layer, owner, allowed: list, spec, where: str, depth: int) -> tuple:






    if spec in (None, "", [], {}):
        return None, False, None
    current = owner.subSymbol()
    if current is None:
        registry = QgsApplication.symbolLayerRegistry()
        owners = []
        for name in allowed:
            probe = registry.createSymbolLayer(name, {})
            if probe is not None and probe.subSymbol() is not None:
                owners.append(name)
        return None, False, tool_error(
            f"{where} draws no symbol of its own, so it takes no sub_symbol. Nothing was changed.", "INVALID_ARGS",
            f"Types with a sub_symbol here: {', '.join(owners)}." if owners else "")
    if depth >= _SUB_SYMBOL_DEPTH:
        return None, False, tool_error(f"{where}: sub_symbol nests at most {_SUB_SYMBOL_DEPTH} deep.", "INVALID_ARGS")
    if isinstance(spec, list):
        spec = {"symbol_layers": spec}
    unknown = sorted(str(k) for k in spec if k != "symbol_layers") if isinstance(spec, dict) else []
    if not isinstance(spec, dict) or unknown:
        return None, False, tool_error(
            f"{where}: sub_symbol takes symbol_layers only{' (not ' + ', '.join(unknown) + ')' if unknown else ''}. "
            "Nothing was changed.", "INVALID_ARGS",
            'sub_symbol: {"symbol_layers": [{"type": ..., "properties": {...}}]}, as the top-level ones.')
    kind = {0: "marker", 1: "line", 2: "fill"}.get(int(getattr(current.type(), "value", current.type())), "symbol")
    built, summaries, error = _build_layers(layer, spec.get("symbol_layers"), _allowed_types(current.type()),
                                            f"{where} sub_symbol ({kind})", depth + 1)
    if error:
        return None, False, error
    symbol = current.clone()
    if not _fill_symbol(symbol, built, None) or not owner.setSubSymbol(symbol):
        return None, False, tool_error(f"{where}: a sub_symbol layer does not fit its {kind} symbol.", "INVALID_ARGS")
    return {"symbol": kind, "symbol_layers": summaries}, any(given for _l, given in built), None


def _build_layers(layer, specs, allowed: list, where: str, depth: int = 0) -> tuple:

    if not isinstance(specs, list) or not specs:
        return None, None, tool_error(f"{where}: symbol_layers must be a non-empty list, bottom layer first.",
                                      "INVALID_ARGS")
    built, summaries = [], []
    for i, spec in enumerate(specs, start=1):
        created, summary, colour_given, error = _symbol_layer(layer, spec, allowed, f"{where} symbol layer {i}",
                                                              depth)
        if error:
            return None, None, error
        built.append((created, colour_given))
        summaries.append(summary)
    return built, summaries, None


def _fill_symbol(symbol, layers: list, keep_colour) -> bool:

    while symbol.symbolLayerCount():
        symbol.deleteSymbolLayer(0)
    for created, colour_given in layers:
        piece = created.clone()
        if keep_colour is not None and not colour_given:
            piece.setColor(keep_colour)
        if not symbol.appendSymbolLayer(piece):
            return False
    return True


def _new_symbol(layer, layers: list, colour=None):
    from qgis.core import QgsSymbol

    symbol = QgsSymbol.defaultSymbol(layer.geometryType())
    return symbol if _fill_symbol(symbol, layers, colour) else None




def _renderer_class(name: str):
    import qgis.core as core

    return getattr(core, _RENDERER_CLASSES.get(name, ""), None)


def _named_constant(classes, name):







    target = _norm(name)
    for cls in classes:
        if cls is None:
            continue
        for attr in dir(cls):
            if attr.startswith("_") or _norm(attr) != target:
                continue
            try:
                found = getattr(cls, attr)
            except Exception:  # noqa: BLE001  # nosec B112
                continue
            if isinstance(found, (enum.Enum, int)) and not callable(found):
                return found
    return None


def _option_value(key: str, value, current, renderer_cls=None) -> tuple:

    from qgis.core import QgsColorRamp
    from qgis.PyQt.QtGui import QColor

    if key.endswith("_unit"):


        unit, ok = _render_unit(value)
        return (unit, None) if ok else (None, f"{key}: {value!r} is not a unit (MM, Point, Pixel, MapUnit).")
    if isinstance(current, QColor):
        colour = _qcolor(value)
        return (colour, None) if colour is not None else (None, f"{key}: {value!r} is not a colour.")
    if isinstance(current, QgsColorRamp) or (current is None and "ramp" in key):
        ramp = _named_ramp(str(value))
        if ramp is None:
            return None, f"{key}: {value!r} is not a colour ramp of the QGIS style (Viridis, Magma, Blues, RdYlGn...)."
        return ramp, None
    if isinstance(current, bool):
        return bool(value), None
    if isinstance(current, enum.Enum):
        if type(current).__name__ == "RenderUnit":
            unit, ok = _render_unit(value)
            return (unit, None) if ok else (None, f"{key}: {value!r} is not a unit (MM, Point, Pixel, MapUnit).")
        found = _named_constant((type(current), renderer_cls), value) if isinstance(value, str) else None
        if found is None:
            return None, f"{key}: {value!r} is not one of {', '.join(type(current).__members__)}."
        return found, None
    if isinstance(current, int):
        if isinstance(value, str):

            found = _named_constant((type(current), renderer_cls), value)
            if found is None:
                return None, f"{key}: {value!r} is not a number or a named option on this renderer."
            return found, None
        return int(value), None
    if isinstance(current, float):
        try:
            return float(value), None
        except (TypeError, ValueError):
            return None, f"{key}: {value!r} is not a number."
    return value, None


def _named_ramp(name: str):

    from qgis.core import QgsColorBrewerColorRamp, QgsStyle

    style = QgsStyle.defaultStyle()
    same = [n for n in style.colorRampNames() if n.lower() == name.lower()]
    if same:
        return style.colorRamp(same[0])
    scheme = next((s for s in QgsColorBrewerColorRamp.listSchemeNames() if s.lower() == name.lower()), None)
    return QgsColorBrewerColorRamp(scheme, 9) if scheme else None


def _camel(key: str) -> str:
    parts = [p for p in re.split(r"[_\s]+", str(key)) if p]
    return "".join(p[:1].upper() + p[1:] for p in parts)


def _options_of(renderer) -> list:

    from qgis.core import QgsColorRamp
    from qgis.PyQt.QtGui import QColor

    names = []
    for attr in dir(renderer):
        if not attr.startswith("set") or len(attr) < 4:
            continue
        getter = getattr(renderer, attr[3].lower() + attr[4:], None)
        if not callable(getter):
            continue
        try:
            value = getter()
        except Exception:  # noqa: BLE001  # nosec B112
            continue
        if isinstance(value, (bool, int, float, str, QColor, QgsColorRamp, enum.Enum)):
            names.append(re.sub(r"(?<!^)(?=[A-Z])", "_", attr[3:]).lower())
    return sorted(names)


def _apply_options(renderer, options) -> tuple:

    if not options:
        return {}, None
    if not isinstance(options, dict):
        return None, tool_error("renderer_options maps an option name to its value.", "INVALID_ARGS")
    applied = {}
    for key, value in options.items():
        camel = _camel(key)
        setter = getattr(renderer, "set" + camel, None)
        getter = getattr(renderer, camel[:1].lower() + camel[1:], None)
        current = None
        ok = callable(setter) and callable(getter)
        if ok:
            try:
                current = getter()
            except Exception:  # noqa: BLE001
                ok = False
        if not ok:
            return None, tool_error(
                f"The {renderer.type()} renderer has no option {key!r}. Nothing was changed.", "INVALID_ARGS",
                f"Its options: {', '.join(_options_of(renderer))}.")
        converted, problem = _option_value(str(key), value, current, type(renderer))
        if problem:
            return None, tool_error(problem + " Nothing was changed.", "INVALID_ARGS")
        try:
            setter(converted)
        except Exception as exc:  # noqa: BLE001
            return None, tool_error(f"renderer option {key!r}: {exc}", "INVALID_ARGS")
        applied[str(key)] = value
    return applied, None


def _any_size_given(specs) -> bool:


    if not isinstance(specs, list):
        return False
    return any(isinstance(spec, dict) and any(str(k).lower() == "size" for k in (spec.get("properties") or {}))
               for spec in specs)


def _restyle_symbols(renderer, symbol_type, layers: list, keep_size: bool = False) -> tuple:


    from qgis.core import QgsMarkerSymbol, QgsRenderContext

    count = 0
    for symbol in renderer.symbols(QgsRenderContext()):
        if symbol is None or symbol.type() != symbol_type:
            continue
        is_marker = isinstance(symbol, QgsMarkerSymbol)
        size = symbol.size() if keep_size and is_marker else None
        if not _fill_symbol(symbol, layers, symbol.color()):
            return count, tool_error("A symbol layer does not fit this layer's symbols. Nothing was changed.",
                                     "INVALID_ARGS")
        if size is not None:
            symbol.setSize(size)
        count += 1
    return count, None


def _rule_renderer(layer, rules, allowed, top_layers=None) -> tuple:







    from qgis.core import QgsRuleBasedRenderer, QgsSymbol
    from qgis.PyQt.QtGui import QColor

    root = QgsRuleBasedRenderer.Rule(None)
    summaries = []
    for i, spec in enumerate(rules, start=1):
        if not isinstance(spec, dict):
            return None, None, tool_error(f"rule {i} must be an object with filter and symbol_layers.", "INVALID_ARGS")
        where = f"rule {i}"
        text = str(spec.get("filter") or "").strip()
        is_else = text.upper() == "ELSE"
        if text and not is_else:
            bad = _expression_error(layer, text, f"{where} filter")
            if bad:
                return None, None, bad
        colour = QColor.fromHsl((i - 1) * 37 % 360, 178, 128)
        own_specs = spec.get("symbol_layers")
        if own_specs:
            layers, pieces, error = _build_layers(layer, own_specs, allowed, where)
            if error:
                return None, None, error
            symbol = _new_symbol(layer, layers, colour)
            if symbol is None:
                return None, None, tool_error(f"{where}: a symbol layer does not fit this geometry.", "INVALID_ARGS")
        elif top_layers is not None:
            pieces = None
            symbol = _new_symbol(layer, top_layers, colour)
            if symbol is None:
                return None, None, tool_error(f"{where}: a symbol layer does not fit this geometry.", "INVALID_ARGS")
        else:
            symbol, pieces = QgsSymbol.defaultSymbol(layer.geometryType()), None
            symbol.setColor(colour)
        rule = QgsRuleBasedRenderer.Rule(symbol)
        if is_else:
            rule.setIsElse(True)
        elif text:
            rule.setFilterExpression(text)
        label = str(spec.get("label") or text or f"Rule {i}")
        rule.setLabel(label)
        for key, setter in (("min_scale", rule.setMinimumScale), ("max_scale", rule.setMaximumScale)):
            if spec.get(key):
                setter(float(spec[key]))
        root.appendChild(rule)
        summaries.append({"label": label, "filter": text, **({"symbol_layers": pieces} if pieces else {})})
    return QgsRuleBasedRenderer(root), summaries, None


def _vector_renderer(layer, args: dict, result: dict) -> tuple:

    from qgis.core import QgsSingleSymbolRenderer

    symbol_type, _symbol = _symbol_type(layer)
    if symbol_type is None:
        return None, tool_error(f"Layer {layer.name()!r} has no geometry, so it draws no symbols.", "INVALID_ARGS",
                                "Style the layer that draws these rows, or join this table to it first.")
    allowed = _allowed_types(symbol_type)
    registry = QgsApplication.rendererRegistry()
    try:
        compatible = list(registry.renderersList(layer))
    except TypeError:
        compatible = list(registry.renderersList())
    wanted = str(args.get("renderer") or "").strip()
    name = ""
    if wanted:
        name = {_norm(n): n for n in compatible}.get(_norm(wanted), "")
        if not name:
            for candidate in compatible:
                metadata = registry.rendererMetadata(candidate)
                if metadata is not None and _norm(metadata.visibleName()) == _norm(wanted):
                    name = candidate
        if not name:
            return None, tool_error(
                f"{wanted!r} is not a renderer for {layer.name()!r} (a {_geometry_word(symbol_type)} layer). "
                "Nothing was changed.", "INVALID_ARGS", f"Renderers for this layer: {', '.join(compatible)}.")

    specs = args.get("symbol_layers")
    layers = summaries = None
    if specs:
        layers, summaries, error = _build_layers(layer, specs, allowed, "symbol_layers")
        if error:
            return None, error
        result["symbol_layers"] = summaries
    rules = args.get("rules")
    if rules and name and name != "RuleRenderer":
        return None, tool_error("rules build a rule-based renderer: pass renderer RuleRenderer or leave it out.",
                                "INVALID_ARGS")
    if rules and not name:
        name = "RuleRenderer"

    current = layer.renderer()
    current_type = current.type() if current is not None else ""
    if name in _CLASSED and name != current_type:
        return None, tool_error(
            f"{name} classes come from a field, a ramp and a class count, which set_layer_style builds.",
            "INVALID_ARGS", "set_layer_style (categorized or graduated), then this tool without renderer, "
            "gives every class these symbol layers.")
    if not name or name in _CLASSED:

        if layers is None:
            return None, None
        renderer = current.clone() if current is not None else None
        if renderer is None:
            return None, tool_error(f"Layer {layer.name()!r} has no renderer to restyle.", "INVALID_ARGS",
                                    "renderer singleSymbol accepts the symbol_layers.")
        count, error = _restyle_symbols(renderer, symbol_type, layers, keep_size=not _any_size_given(specs))
        if error:
            return None, error
        if not count:
            return None, tool_error(f"The {current_type} renderer of {layer.name()!r} draws no symbol these layers "
                                    "could go on.", "INVALID_ARGS",
                                    "renderer singleSymbol accepts the symbol_layers.")
        result["symbols_restyled"] = count
        return renderer, None

    if name == "nullSymbol":
        return _renderer_class(name)(), None
    if name == "RuleRenderer" and rules:
        renderer, rule_summaries, error = _rule_renderer(layer, rules, allowed, layers)
        if error:
            return None, error
        result["rules"] = rule_summaries
        return renderer, None
    cls = _renderer_class(name)
    if cls is None or not hasattr(cls, "convertFromRenderer"):
        return None, tool_error(f"The {name} renderer cannot be built from a call on this QGIS.", "INVALID_ARGS",
                                "apply_style_qml with a .qml saved from the Layer Styling panel sets any renderer.")
    if layers is not None and name in _NO_SYMBOL_LAYERS:


        try:
            options = _options_of(cls())
        except Exception:  # noqa: BLE001
            options = []
        hint = f"Its options: {', '.join(options)}." if options else ""
        return None, tool_error(
            f"The {name} renderer draws its own symbol; it does not take symbol_layers. Nothing was changed.",
            "INVALID_ARGS", hint)
    if layers is not None and name != "RuleRenderer":
        symbol = _new_symbol(layer, layers)
        if symbol is None:
            return None, tool_error("A symbol layer does not fit this geometry. Nothing was changed.", "INVALID_ARGS")
        base = QgsSingleSymbolRenderer(symbol)
    else:
        base = current.clone() if current is not None else None
    if base is None:
        return None, tool_error(f"The {name} renderer needs symbol_layers here.", "INVALID_ARGS")
    renderer = cls.convertFromRenderer(base)
    if renderer is None:
        return None, tool_error(f"QGIS cannot turn the {base.type()} renderer into {name}.", "INVALID_ARGS",
                                "symbol_layers wraps a single symbol.")
    if name == "RuleRenderer" and layers is not None:
        count, error = _restyle_symbols(renderer, symbol_type, layers, keep_size=not _any_size_given(specs))
        if error:
            return None, error
        result["symbols_restyled"] = count
    if name in _WRAPPERS:
        embedded = getattr(renderer, "embeddedRenderer", lambda: None)()
        if embedded is not None:
            result["draws"] = embedded.type()
    return renderer, None


def _geometry_word(symbol_type) -> str:
    return {0: "point", 1: "line", 2: "polygon"}.get(int(getattr(symbol_type, "value", symbol_type)), "vector")




def _composition_mode(name: str):

    from qgis.core import Qgis, QgsPainting

    member = "".join(part.capitalize() for part in name.split("_"))
    mode = enum_member(Qgis, "BlendMode", member, None)
    if mode is None:
        mode = enum_member(QgsPainting, "BlendMode", "Blend" + member, None)
    if mode is None:
        return None
    return QgsPainting.getCompositionMode(mode)


def _resampler(name: str):
    from qgis.core import QgsBilinearRasterResampler, QgsCubicRasterResampler

    return {"bilinear": QgsBilinearRasterResampler, "cubic": QgsCubicRasterResampler}.get(name, lambda: None)()


_MIN_MAX_KEYS = ("min_max", "cumulative_cut", "std_dev_factor", "min_max_extent", "min_max_accuracy", "stretch")
_RASTER_ONLY = ("resampling", "resampling_zoomed_out", "brightness", "contrast", "saturation", "gamma") + _MIN_MAX_KEYS
_VECTOR_ONLY = ("renderer", "symbol_layers", "rules", "renderer_options", "effect", "feature_blend_mode")


def _set_layer_symbology(args: dict) -> dict:
    target = args.get("layer_name")
    if not target:
        return tool_error("layer_name is required.", "INVALID_ARGS", "It takes the layer name or id.")
    layer = _find_layer(target)
    if not layer:
        return _layer_not_found_error(target)
    is_vector = isinstance(layer, QgsVectorLayer)
    is_raster = isinstance(layer, QgsRasterLayer)
    if not (is_vector or is_raster):
        return tool_error(f"Layer {layer.name()!r} is neither a vector nor a raster layer.", "INVALID_ARGS",
                          "apply_style_qml sets the style of any other kind of layer.")
    wrong = [k for k in (_VECTOR_ONLY if is_raster else _RASTER_ONLY) if args.get(k) not in (None, "", [], {})]
    if wrong:
        kind = "raster" if is_raster else "vector"
        hint = ("A raster's renderer is set_raster_style (hillshade, pseudocolor); here it takes blend_mode, "
                "opacity, resampling, brightness, contrast, saturation, gamma, min_max and stretch."
                if is_raster else "resampling, brightness, contrast, saturation, gamma, min_max and stretch are "
                "raster settings.")
        return tool_error(f"Not for a {kind} layer: {', '.join(wrong)}. Nothing was changed.", "INVALID_ARGS", hint)

    result: dict = {"styled": layer.name()}
    renderer = None
    renderer_effect = None
    options_applied = {}
    if is_vector:
        renderer, error = _vector_renderer(layer, args, result)
        if error:
            return error
        if args.get("renderer_options"):
            target_renderer = renderer if renderer is not None else (layer.renderer().clone()
                                                                     if layer.renderer() is not None else None)
            if target_renderer is None:
                return tool_error("This layer has no renderer to set options on.", "INVALID_ARGS")
            options_applied, error = _apply_options(target_renderer, args["renderer_options"])
            if error:
                return error
            renderer = target_renderer
        if args.get("effect"):
            renderer_effect, names, error = _effect_stack(args["effect"], "effect")
            if error:
                return error
            renderer = renderer if renderer is not None else layer.renderer().clone()
            result["effect"] = names
    modes = {}
    for key in ("blend_mode", "feature_blend_mode"):
        if args.get(key):
            mode = _composition_mode(str(args[key]))
            if mode is None:
                return tool_error(f"{key} {args[key]!r} is not available on this QGIS.", "INVALID_ARGS",
                                  f"Blend modes: {', '.join(BLEND_MODES)}.")
            modes[key] = mode
    range_plan = None
    if is_raster:
        range_plan, error = _range_plan(layer, args)
        if error:
            return error
    if not (renderer is not None or modes or any(args.get(k) is not None for k in ("opacity",) + _RASTER_ONLY)):
        return tool_error("Nothing to change: pass renderer, symbol_layers, rules, effect, blend_mode, opacity "
                          "or a raster setting.", "INVALID_ARGS")


    kept_style = _style_to_put_back(layer)
    if renderer is not None:
        if renderer_effect is not None:
            renderer.setPaintEffect(renderer_effect)
        result["renderer"] = renderer.type()
        layer.setRenderer(renderer)
        if options_applied:
            result["renderer_options"] = options_applied
    if "blend_mode" in modes:
        layer.setBlendMode(modes["blend_mode"])
        result["blend_mode"] = args["blend_mode"]
    if "feature_blend_mode" in modes:
        layer.setFeatureBlendMode(modes["feature_blend_mode"])
        result["feature_blend_mode"] = args["feature_blend_mode"]
    if args.get("opacity") is not None:
        opacity = float(args["opacity"])
        if is_raster and layer.renderer() is not None:
            layer.renderer().setOpacity(opacity)
        else:
            layer.setOpacity(opacity)
        result["opacity"] = opacity
    if is_raster:
        result.update(_raster_rendering(layer, args))
        if range_plan is not None:
            result.update(_apply_range(layer, range_plan))


        layer.emitStyleChanged()
    layer.triggerRepaint()
    try:
        from qgis.utils import iface

        if iface is not None and iface.layerTreeView() is not None:
            iface.layerTreeView().refreshLayerSymbology(layer.id())
    except Exception as exc:  # noqa: BLE001
        log_warning(f"set_layer_symbology: legend not refreshed: {exc}")
    if is_vector and renderer is not None and "renderer" not in args and "rules" not in args:
        result["renderer_kept"] = True
    result.update(_previous_style_keys(layer, kept_style))
    if is_vector:
        result.update(_wider_than_the_selection(layer))
    return result


def _raster_rendering(layer, args: dict) -> dict:
    out = {}
    if args.get("resampling"):
        resample = layer.resampleFilter()
        zoomed_out = args.get("resampling_zoomed_out") or args["resampling"]
        resample.setZoomedInResampler(_resampler(args["resampling"]))
        resample.setZoomedOutResampler(_resampler(zoomed_out))
        out["resampling"] = {"zoomed_in": args["resampling"], "zoomed_out": zoomed_out}
    elif args.get("resampling_zoomed_out"):
        layer.resampleFilter().setZoomedOutResampler(_resampler(args["resampling_zoomed_out"]))
        out["resampling"] = {"zoomed_out": args["resampling_zoomed_out"]}
    brightness = layer.brightnessFilter()
    for key, setter in (("brightness", "setBrightness"), ("contrast", "setContrast"), ("gamma", "setGamma")):
        if args.get(key) is not None and hasattr(brightness, setter):
            getattr(brightness, setter)(args[key])
            out[key] = args[key]
    if args.get("saturation") is not None:
        layer.hueSaturationFilter().setSaturation(int(args["saturation"]))
        out["saturation"] = args["saturation"]
    return out




def _range_plan(layer, args: dict) -> tuple:

    from qgis.core import QgsContrastEnhancement, QgsRasterMinMaxOrigin

    given = [k for k in _MIN_MAX_KEYS if args.get(k) not in (None, "", [])]
    if not given:
        return None, None
    renderer = layer.renderer()
    kind = str(renderer.type()) if renderer is not None else ""
    if kind not in _RANGED_RENDERERS:
        return None, tool_error(
            f"{', '.join(given)}: the {kind or 'current'} renderer of {layer.name()!r} has no min/max to set. "
            "Nothing was changed.", "INVALID_ARGS",
            "set_raster_style singleband_gray, singleband_pseudocolor or multiband_color first, then this tool.")
    limits = args.get("min_max") or ("cumulative_cut" if args.get("cumulative_cut") else
                                     "std_dev" if args.get("std_dev_factor") else None)
    if limits is not None and limits not in MIN_MAX:
        return None, tool_error(f"min_max {limits!r} is not one of {', '.join(MIN_MAX)}.", "INVALID_ARGS")
    for key, needs in (("cumulative_cut", "cumulative_cut"), ("std_dev_factor", "std_dev")):
        if args.get(key) not in (None, "", []) and limits != needs:
            return None, tool_error(f"{key} goes with min_max {needs}, not {limits}. Nothing was changed.",
                                    "INVALID_ARGS")
    if limits is None and (args.get("min_max_extent") or args.get("min_max_accuracy")):

        current = QgsRasterMinMaxOrigin.limitsString(renderer.minMaxOrigin().limits())
        limits = next((name for name, qgis in MIN_MAX.items() if qgis == current), None)
        if limits is None:
            return None, tool_error("This layer's range was typed by hand: pass min_max with min_max_extent or "
                                    "min_max_accuracy.", "INVALID_ARGS")
    plan = {"kind": kind, "limits": limits, "extent": args.get("min_max_extent") or "whole_raster",
            "accuracy": args.get("min_max_accuracy") or "estimate", "rect": None, "stretch": args.get("stretch"),
            "algorithm": None, "cut": None, "std_dev": None}
    for key, names in (("extent", MIN_MAX_EXTENT), ("accuracy", MIN_MAX_ACCURACY), ("stretch", STRETCH)):
        if plan[key] is not None and plan[key] not in names:
            return None, tool_error(f"{key} {plan[key]!r} is not one of {', '.join(names)}.", "INVALID_ARGS")
    if limits == "cumulative_cut":
        cut = args.get("cumulative_cut") or [2, 98]
        try:
            low, high = (float(v) for v in cut)
        except (TypeError, ValueError):
            low = high = -1.0
        if not 0 <= low < high <= 100:
            return None, tool_error(f"cumulative_cut {cut!r} is not [lower, upper] percent with 0 <= lower < "
                                    "upper <= 100.", "INVALID_ARGS", "QGIS's default is [2, 98].")
        plan["cut"] = (low, high)
    if limits == "std_dev":
        try:
            factor = float(args.get("std_dev_factor") or QgsRasterMinMaxOrigin.DEFAULT_STDDEV_FACTOR)
        except (TypeError, ValueError):
            factor = 0.0
        if not 0 < factor <= 10:
            return None, tool_error("std_dev_factor is a number of standard deviations above 0 (2 by default).",
                                    "INVALID_ARGS")
        plan["std_dev"] = factor
    if limits is not None and plan["extent"] != "whole_raster":
        rect, problem = _canvas_extent(layer)
        if problem:
            return None, tool_error(problem, "INVALID_ARGS", "min_max_extent whole_raster reads the whole raster.")
        plan["rect"] = rect
    if plan["stretch"]:
        if kind == "singlebandpseudocolor" and plan["stretch"] == "no_stretch":
            return None, tool_error("A pseudocolor ramp always spans its min and max: no_stretch is for gray and "
                                    "multiband color. Nothing was changed.", "INVALID_ARGS",
                                    "clip_to_min_max leaves the values outside the range undrawn.")
        plan["algorithm"] = enum_member(QgsContrastEnhancement, "ContrastEnhancementAlgorithm",
                                        STRETCH[plan["stretch"]])
    return plan, None


def _canvas_extent(layer) -> tuple:

    try:
        from qgis.utils import iface

        canvas = iface.mapCanvas() if iface is not None else None
    except Exception:  # noqa: BLE001
        canvas = None
    if canvas is None:
        return None, "There is no map canvas here to read the range from. Nothing was changed."
    rect = canvas.mapSettings().outputExtentToLayerExtent(layer, canvas.extent())
    if rect.isEmpty() or not rect.intersects(layer.extent()):
        return None, f"The map canvas shows none of {layer.name()!r}. Nothing was changed."
    return rect, None


def _enhancements(renderer) -> list:

    kind = str(renderer.type())
    if kind == "singlebandgray":
        found = [renderer.contrastEnhancement()]
    elif kind == "multibandcolor":
        found = [renderer.redContrastEnhancement(), renderer.greenContrastEnhancement(),
                 renderer.blueContrastEnhancement()]
    else:
        found = []
    return [ce for ce in found if ce is not None]


def _band_ranges(renderer) -> list:

    def rounded(value):
        return float(f"{float(value):.6g}")

    kind = str(renderer.type())

    band = renderer.inputBand() if hasattr(renderer, "inputBand") else None
    if kind == "singlebandpseudocolor":
        return [{"band": band if band is not None else renderer.band(), "min": rounded(renderer.classificationMin()),
                 "max": rounded(renderer.classificationMax())}]
    if kind == "singlebandgray":
        pairs = ((band if band is not None else renderer.grayBand(), renderer.contrastEnhancement()),)
    else:
        pairs = ((renderer.redBand(), renderer.redContrastEnhancement()),
                 (renderer.greenBand(), renderer.greenContrastEnhancement()),
                 (renderer.blueBand(), renderer.blueContrastEnhancement()))
    return [{"band": band, "min": rounded(ce.minimumValue()), "max": rounded(ce.maximumValue())}
            for band, ce in pairs if ce is not None and band > 0]


def _kept_stops(renderer):






    shader = renderer.shader()
    function = shader.rasterShaderFunction() if shader is not None else None
    if function is None or not hasattr(function, "sourceColorRamp") or function.sourceColorRamp() is not None:
        return None
    stops = list(function.colorRampItemList())
    if not stops:
        return None
    return stops, float(renderer.classificationMin()), float(renderer.classificationMax())


_NUMBER = re.compile(r"[-+]?\d(?:[\d,]*\d)?(?:\.\d+)?")


def _rescale_stops(renderer, kept) -> None:





    from qgis.core import QgsColorRampShader

    stops, old_low, old_high = kept
    low, high = float(renderer.classificationMin()), float(renderer.classificationMax())
    span = old_high - old_low
    if not (math.isfinite(span) and span > 0 and math.isfinite(low) and math.isfinite(high)):
        return
    anchors = [old_low, old_high] + [s.value for s in stops if math.isfinite(s.value)]
    grouped = any("," in s.label for s in stops)

    def moved(value):
        return low + (value - old_low) * (high - low) / span if math.isfinite(value) else value

    def relabel(match):
        text = match.group(0)
        number = float(text.replace(",", ""))
        decimals = len(text.split(".", 1)[1]) if "." in text else 0
        near = 0.5 * 10 ** -decimals + 1e-9 * abs(number)
        anchor = next((a for a in anchors if abs(a - number) <= near), None)
        if anchor is None:
            return text
        value = moved(anchor)
        new = f"{value:{',' if grouped else ''}.{decimals}f}"
        return "+" + new if text.startswith("+") and value > 0 else new

    function = renderer.shader().rasterShaderFunction()
    function.setColorRampItemList([QgsColorRampShader.ColorRampItem(moved(s.value), s.color,
                                                                    _NUMBER.sub(relabel, s.label))
                                   for s in stops])
    function.setMinimumValue(low)
    function.setMaximumValue(high)


def _apply_range(layer, plan: dict) -> dict:

    from qgis.core import QgsRasterMinMaxOrigin, QgsRectangle

    renderer = layer.renderer()
    out = {}
    if plan["limits"] is not None:
        origin = QgsRasterMinMaxOrigin(renderer.minMaxOrigin())
        origin.setLimits(QgsRasterMinMaxOrigin.limitsFromString(MIN_MAX[plan["limits"]]))
        origin.setExtent(QgsRasterMinMaxOrigin.extentFromString(MIN_MAX_EXTENT[plan["extent"]]))
        origin.setStatAccuracy(QgsRasterMinMaxOrigin.statAccuracyFromString(MIN_MAX_ACCURACY[plan["accuracy"]]))
        if plan["cut"] is not None:
            origin.setCumulativeCutLower(plan["cut"][0] / 100.0)
            origin.setCumulativeCutUpper(plan["cut"][1] / 100.0)
        if plan["std_dev"] is not None:
            origin.setStdDevFactor(plan["std_dev"])
        renderer.setMinMaxOrigin(origin)
        algorithm = plan["algorithm"]
        if algorithm is None:
            current = _enhancements(renderer)
            algorithm = current[0].contrastEnhancementAlgorithm() if current else CONTRAST_STRETCH_MINMAX
            if current and algorithm == CONTRAST_NONE:

                algorithm = CONTRAST_STRETCH_MINMAX
                out["stretch"] = "stretch_to_min_max"
                out["stretch_note"] = "the layer had no stretch, which ignores min and max, so it now stretches"
        kept = _kept_stops(renderer) if plan["kind"] == "singlebandpseudocolor" else None
        sample = 0 if plan["accuracy"] == "actual" else int(QgsRasterLayer.SAMPLE_SIZE)


        layer.setContrastEnhancement(algorithm, origin.limits(), plan["rect"] or QgsRectangle(), sample)
        renderer = layer.renderer()
        if kept is not None:
            _rescale_stops(renderer, kept)
        renderer.setMinMaxOrigin(origin)
        described = {"limits": plan["limits"], "extent": plan["extent"], "accuracy": plan["accuracy"]}
        if plan["cut"] is not None:
            described["cumulative_cut"] = list(plan["cut"])
        if plan["std_dev"] is not None:
            described["std_dev_factor"] = plan["std_dev"]
        described["bands"] = _band_ranges(renderer)
        out["min_max"] = described
    elif plan["algorithm"] is not None:
        for enhancement in _enhancements(renderer):
            enhancement.setContrastEnhancementAlgorithm(plan["algorithm"], True)
    if plan["stretch"]:
        if plan["kind"] == "singlebandpseudocolor":
            shader = renderer.shader()
            function = shader.rasterShaderFunction() if shader is not None else None
            if function is not None and hasattr(function, "setClip"):

                function.setClip(plan["stretch"] != "stretch_to_min_max")
        out["stretch"] = plan["stretch"]
    return out
