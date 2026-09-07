# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




from __future__ import annotations

import math
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
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP, QDate, QDateTime, QLocale, Qt, QTime

from ..core import layer_order, limits
from ..core.crs_ref import crs_ref
from ..core.qt_compat import enum_member
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from . import raster_overviews
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
from .colour_text import hex_from_qcolor, qcolor_from_text
from .processing_guards import crs_plausibility

RASTER_STYLES = ("singleband_pseudocolor", "singleband_gray", "multiband_color", "hillshade", "paletted")

RASTER_LIMITS = ("cumulative_cut", "min_max")


_RASTER_OPTIONS = {
    "interpolation": ("interpolated", {
        "discrete": SHADER_DISCRETE, "exact": SHADER_EXACT, "interpolated": SHADER_INTERPOLATED}),
    "classification": ("continuous", {
        "continuous": SHADER_CLASS_CONTINUOUS, "equal_interval": SHADER_CLASS_EQUAL_INTERVAL,
        "quantile": SHADER_CLASS_QUANTILE}),
    "contrast": ("stretch", {
        "clip": CONTRAST_CLIP_MINMAX, "none": CONTRAST_NONE, "stretch": CONTRAST_STRETCH_MINMAX,
        "stretch_clip": CONTRAST_STRETCH_CLIP_MINMAX}),
    "gradient": ("black_to_white", {"black_to_white": GRAY_BLACK_TO_WHITE, "white_to_black": GRAY_WHITE_TO_BLACK}),
}

_STATS_SAMPLE = 250_000



_MAX_RASTER_CLASSES = 256

LAYER_PROPERTIES = ("opacity", "name", "min_scale", "max_scale", "scale_visibility")


def register_harvest_layers_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="set_raster_style",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Style {layer_name}"),
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
                "hillshade_overlay": {"type": "boolean"},
                "min_max": {"type": "string", "enum": list(RASTER_LIMITS)},
                "cumulative_cut": {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 100},
                                   "minItems": 2, "maxItems": 2},

                "color_stops": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 8},
                "invert_ramp": {"type": "boolean"},
            },
            "required": ["layer_name", "style_type"],
        },
        handler=_set_raster_style,


        prepare=raster_overviews.prepare_raster_style,
    ))

    registry.register(Tool(
        name="apply_style_qml",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Apply the style {path} to {layer_name}"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "path": {"type": "string"},
                "classification_field": {"type": "string"},
            },
            "required": ["layer_name", "path"],
        },
        handler=_apply_style_qml,
    ))

    registry.register(Tool(
        name="save_style_qml",
        danger="destructive",
        label=QT_TRANSLATE_NOOP("AIAgent", "Save the style of {layer_name}"),
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
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Set {property} on {layer_name}"),
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
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Reorder the layers"),
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


            "x-order-groups": True,
        },
        handler=_set_layer_order,
    ))

    registry.register(Tool(
        name="set_layer_crs",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Set the CRS of {layer_name} to {crs}"),
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
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Read the CRS of {layer_name}"),
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
        danger="destructive",
        label=QT_TRANSLATE_NOOP("AIAgent", "Delete the field {field_name} from {layer_name}"),
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
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Rename {old_name} to {new_name} in {layer_name}"),
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
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Join a table to {layer_name}"),
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
        danger="write",
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
        danger="write",
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
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Duplicate {layer_name}[ as {new_name}]"),
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
    except Exception:  # nosec B110
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
            "list_layers shows each layer's type; vector layers only.",
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
            "list_layers shows each layer's type; raster layers only. Vector layers use set_layer_style.",
        )
    return layer, None


def _field_error(layer, field_name: str) -> dict:
    from .layer_lookup import _field_not_found_error

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
    except Exception:  # nosec B110
        pass


def _raster_option(args: dict, name: str):

    default, choices = _RASTER_OPTIONS[name]
    word = args.get(name, default)
    constant = choices.get(word)
    if constant is not None:
        return constant, None
    return None, tool_error(f"Unknown {name}: {word!r}.", "INVALID_ARGS", f"One of {sorted(choices)}.")





def _raster_ramp(args: dict, default=None, default_name: str = "Viridis") -> tuple:









    from qgis.core import QgsColorBrewerColorRamp, QgsGradientColorRamp, QgsGradientStop

    invert = args.get("invert_ramp") is True
    stops = args.get("color_stops") or []
    if stops:
        colours = []
        for value in stops:
            colour = qcolor_from_text(str(value))
            if not colour.isValid():
                return None, "", tool_error(f"color_stops: {value!r} is not a colour.", "INVALID_ARGS",
                                            "Hex colours such as ['#ffffff', '#add8e6'], lowest value first.")
            colours.append(colour)
        if len(colours) < 2:
            return None, "", tool_error("color_stops needs at least two colours, lowest value first.",
                                        "INVALID_ARGS", "['#ffffff', '#add8e6'] is white to light blue.")
        if invert:
            colours.reverse()
        last = len(colours) - 1
        ramp = QgsGradientColorRamp(colours[0], colours[-1], False,
                                    [QgsGradientStop(i / last, colours[i]) for i in range(1, last)])
        return ramp, "color_stops " + ", ".join(hex_from_qcolor(c) for c in colours), None
    name = str(args.get("color_ramp") or "").strip()
    if name.lower().endswith("_r"):
        name, invert = name[:-2], not invert
    if not name:
        ramp, described = default, default_name
        if ramp is None:
            ramp = QgsStyle.defaultStyle().colorRamp(default_name)
    else:
        style = QgsStyle.defaultStyle()
        ramp, described = style.colorRamp(name), name
        if ramp is None:
            same = [n for n in style.colorRampNames() if n.lower() == name.lower()]
            if same:
                ramp, described = style.colorRamp(same[0]), same[0]
        if ramp is None:
            scheme = next((n for n in QgsColorBrewerColorRamp.listSchemeNames() if n.lower() == name.lower()), None)
            if scheme:
                ramp, described = QgsColorBrewerColorRamp(scheme, 9), scheme
        if ramp is None:
            from .elevation_style import elevation_ramp, is_elevation_alias

            if is_elevation_alias(name):
                ramp, described = elevation_ramp(), "elevation tints"
        if ramp is None:
            return None, "", tool_error(
                f"Color ramp not found: {name!r}.", "INVALID_ARGS",
                "color_stops (hex colours, lowest value first) or a QGIS ramp such as "
                + ", ".join(style.colorRampNames()[:40]) + "; a name ending in _r is reversed.")
    if ramp is not None and invert:
        ramp = ramp.clone()
        ramp.invert()
        described += " reversed"
    return ramp, described, None


def _band_span(provider, band: int, low, high) -> tuple:

    if low is None or high is None:
        from .raster_overviews import read_ahead

        def measure() -> tuple:
            stats = provider.bandStatistics(band, RASTER_STATS_ALL, QgsRectangle(), _STATS_SAMPLE)
            return stats.minimumValue, stats.maximumValue

        found_low, found_high = read_ahead(("stats", int(band)), measure)
        low = found_low if low is None else low
        high = found_high if high is None else high
    return float(low), float(high)


def _stretch(provider, band: int, low: float, high: float, algorithm):

    stretch = QgsContrastEnhancement(provider.dataType(band))
    stretch.setMinimumValue(low)
    stretch.setMaximumValue(high)
    stretch.setContrastEnhancementAlgorithm(algorithm)
    return stretch


def _limits_plan(args: dict) -> tuple:







    cut_given = args.get("cumulative_cut") not in (None, "", [])
    limits = args.get("min_max") or ("cumulative_cut" if cut_given else None)
    if limits is None:
        return None, None
    if limits not in RASTER_LIMITS:
        return None, tool_error(f"min_max {limits!r} is not one of {', '.join(RASTER_LIMITS)}.", "INVALID_ARGS",
                                "The mean plus or minus N standard deviations is set_layer_symbology min_max std_dev.")
    if args.get("min_value") is not None or args.get("max_value") is not None:
        if limits == "min_max" and not cut_given:





            return {"limits": "typed", "cut": None}, None
        return None, tool_error(
            "min_value/max_value type the range by hand; min_max cumulative_cut has QGIS compute one; "
            "not both.", "INVALID_ARGS",
            "min_value/max_value give the range the user asked for; min_max cumulative_cut alone gives a "
            "computed cumulative count cut.")
    if cut_given and limits != "cumulative_cut":
        return None, tool_error(f"cumulative_cut goes with min_max cumulative_cut, not {limits}.", "INVALID_ARGS")
    cut = None
    if limits == "cumulative_cut":
        raw = args.get("cumulative_cut") if cut_given else [2, 98]
        try:
            low, high = (float(v) for v in raw)
        except (TypeError, ValueError):
            low = high = -1.0
        if not 0 <= low < high <= 100:
            return None, tool_error(f"cumulative_cut {raw!r} is not [lower, upper] percent with 0 <= lower < upper "
                                    "<= 100.", "INVALID_ARGS", "QGIS's default is [2, 98].")
        cut = (low, high)
    return {"limits": limits, "cut": cut}, None


def _percent(value: float) -> str:
    return f"{value:g}"


def _band_limits(layer, band: int, plan, min_value, max_value, notes: dict, long_tail: bool = True) -> tuple:





    from .style_defaults import min_max_origin

    provider = layer.dataProvider()
    if plan is not None and plan["cut"] is not None:
        low, high = plan["cut"]
        lo, hi = (float(v) for v in provider.cumulativeCut(band, low / 100.0, high / 100.0, QgsRectangle(),
                                                            _STATS_SAMPLE))
        if not (math.isfinite(lo) and math.isfinite(hi)):
            lo, hi = _band_span(provider, band, None, None)
        how = f"cumulative_cut {_percent(low)}-{_percent(high)} %, computed by QGIS (Min / Max value settings)"
        return lo, hi, min_max_origin("CumulativeCut", plan["cut"]), how
    lo, hi = _band_span(provider, band, min_value, max_value)
    if plan is not None and plan["limits"] == "typed":
        return lo, hi, min_max_origin("None"), (
            "typed in the call (min_value/max_value) and kept as typed: min_max min_max beside them leaves the "
            "range whole, no stretch replaces it")
    if plan is not None:
        return lo, hi, min_max_origin("MinMax"), "min_max: the band's minimum and maximum, computed by QGIS"
    if min_value is not None and max_value is not None and long_tail:




        full_lo, full_hi = _band_span(provider, band, None, None)
        slack = 0.01 * (full_hi - full_lo)
        if slack > 0 and abs(lo - full_lo) <= slack and abs(hi - full_hi) <= slack:
            cut_lo, cut_hi = _long_tail(layer, band, full_lo, full_hi, notes)
            if (cut_lo, cut_hi) != (full_lo, full_hi):
                return cut_lo, cut_hi, min_max_origin("CumulativeCut", (2, 98)), (
                    "cumulative_cut 2-98 %, chosen by the tool: min_value/max_value were the band's full range "
                    "and the band has a long tail (see stretch); min_max min_max keeps the full range")
    if min_value is not None or max_value is not None:
        return lo, hi, min_max_origin("None"), (
            "typed in the call (min_value/max_value), not a cumulative count cut; min_max cumulative_cut has "
            "QGIS compute one")
    if long_tail:
        cut_lo, cut_hi = _long_tail(layer, band, lo, hi, notes)
        if (cut_lo, cut_hi) != (lo, hi):
            return cut_lo, cut_hi, min_max_origin("CumulativeCut", (2, 98)), (
                "cumulative_cut 2-98 %, chosen by the tool because the band has a long tail (see stretch)")
    return lo, hi, min_max_origin("MinMax"), "min_max: the band's minimum and maximum"


def _set_raster_style(args: dict) -> dict:
    layer, error = _raster(args["layer_name"])
    if error:
        return error

    with raster_overviews.answers_for(layer):
        return _style_raster(layer, args)


def _alpha_band(layer, provider, band_count: int) -> int:
    current = layer.renderer()
    used = current.alphaBand() if current is not None else -1
    if 0 < used <= band_count:
        return used
    from qgis.core import QgsRaster

    from ..core.qt_compat import enum_member

    alpha = enum_member(QgsRaster, "ColorInterpretation", "AlphaBand")
    for band in range(1, band_count + 1):
        if provider.colorInterpretation(band) == alpha:
            return band
    return 0


def _style_raster(layer, args: dict) -> dict:
    provider = layer.dataProvider()
    band_count = provider.bandCount()
    style_type = args["style_type"]



    if style_type == "hillshade" or args.get("hillshade_overlay"):
        from .processing_guards import hillshade_of_hillshade

        refused = hillshade_of_hillshade(layer, "the hillshade renderer" if style_type == "hillshade"
                                         else "hillshade_overlay")
        if refused:
            return refused
    plan, error = _limits_plan(args)
    if error:
        return error
    if plan is not None and style_type not in ("singleband_pseudocolor", "singleband_gray", "multiband_color"):
        return tool_error(f"min_max sets the range of a gray, pseudocolor or multiband style, not {style_type}.",
                          "INVALID_ARGS", "style_type singleband_pseudocolor (or singleband_gray) takes it.")
    if (args.get("color_stops") or args.get("invert_ramp") is True) and style_type not in (
            "singleband_pseudocolor", "paletted"):
        return tool_error(f"color_stops and invert_ramp colour a singleband_pseudocolor or paletted style, "
                          f"not {style_type}.", "INVALID_ARGS",
                          "singleband_pseudocolor colours it; a gray style takes gradient white_to_black.")
    min_value, max_value = args.get("min_value"), args.get("max_value")
    notes: dict = {}
    codes: list = []



    ramp_shaped = any(args.get(key) is not None for key in
                      ("classification", "interpolation", "min_value", "max_value", "classes", "min_max",
                       "cumulative_cut", "color_stops"))
    if style_type == "singleband_pseudocolor" and not ramp_shaped:
        from .style_defaults import raster_codes

        try:
            wanted = int(args.get("band", 1))
        except (TypeError, ValueError):
            wanted = 0
        codes = raster_codes(layer, wanted) if 1 <= wanted <= band_count else []
        if codes:
            style_type = "paletted"
            shown = ", ".join(str(c) for c in codes[:8]) + (", ..." if len(codes) > 8 else "")
            notes["style_type_changed"] = (
                f"band {wanted} holds {len(codes)} whole-number class codes ({shown}), so each code got its own "
                "colour (paletted) instead of a continuous ramp, which would draw a gradient between categories. "
                "classification or min_value keeps a ramp.")
    build = _RASTER_BUILDERS.get(style_type) if isinstance(style_type, str) else None
    if build is None:
        return tool_error(f"Unknown style_type: {style_type!r}.", "INVALID_ARGS", f"One of {list(RASTER_STYLES)}.")
    job = {"layer": layer, "provider": provider, "bands": band_count, "args": args, "plan": plan,
           "notes": notes, "codes": codes, "low": min_value, "high": max_value}
    renderer, fields, error = build(job)
    if error:
        return error
    applied: dict = {"style_type": style_type}
    applied.update(fields)





    alpha = _alpha_band(layer, provider, band_count)
    if alpha and alpha != applied.get("band") and hasattr(renderer, "setAlphaBand"):
        renderer.setAlphaBand(alpha)





    if args.get("opacity") is not None:
        try:
            opacity = float(args["opacity"])
        except (TypeError, ValueError):
            return tool_error("opacity must be a number between 0 and 1.", "INVALID_ARGS",
                              "0.65 lets the basemap read through the surface.")
        if not 0.0 <= opacity <= 1.0:
            return tool_error(f"opacity={opacity} is outside 0 to 1.", "INVALID_ARGS",
                              "1 is opaque, 0 invisible; 0.6 to 0.8 lets a basemap through.")
        renderer.setOpacity(opacity)
        applied["opacity"] = opacity

    layer.setRenderer(renderer)
    if args.get("hillshade_overlay") and style_type in ("singleband_pseudocolor", "singleband_gray", "paletted"):
        from .elevation_style import add_relief_overlay

        relief = add_relief_overlay(layer, applied.get("band", 1))
        if relief.get("_error"):
            notes["hillshade_overlay_error"] = relief["_error"]
        else:
            applied["hillshade_overlay"] = relief
    elif style_type == "singleband_pseudocolor" and applied.get("color_ramp") == "elevation tints":
        notes["relief"] = ("hillshade_overlay true adds a hillshade copy of this DEM blended with multiply over it, "
                           "the usual relief look")
    _refresh(layer)
    out = {"layer_id": layer.id(), "name": layer.name(), "applied": applied, "value_units": "raster band values"}
    out.update(notes)
    return out


def _band_arg(job: dict, name: str, fallback: int):

    number = int(job["args"].get(name, fallback))
    count = job["bands"]
    if 1 <= number <= count:
        return number, None
    return None, tool_error(
        f"{name}={number} is out of range: {job['layer'].name()!r} has {count} band(s).",
        "INVALID_ARGS",
        f"band is between 1 and {count}.",
    )


def _build_pseudocolor(job: dict) -> tuple:

    layer, provider, args = job["layer"], job["provider"], job["args"]
    band, error = _band_arg(job, "band", 1)
    if error:
        return None, None, error
    lo, hi, origin, how = _band_limits(layer, band, job["plan"], job["low"], job["high"], job["notes"])
    interpolation, error = _raster_option(args, "interpolation")
    if error:
        return None, None, error
    classification, error = _raster_option(args, "classification")
    if error:
        return None, None, error


    classes = int(args.get("classes") or 5)
    if not 2 <= classes <= _MAX_RASTER_CLASSES:
        return None, None, tool_error(
            f"classes={classes} is outside 2 to {_MAX_RASTER_CLASSES}.", "INVALID_ARGS",
            f"A colour ramp past {_MAX_RASTER_CLASSES} classes is not readable on a map; "
            "5 to 12 is the usual range.")
    fallback, fallback_name = None, "Viridis"
    if not args.get("color_ramp") and not args.get("color_stops"):
        from .elevation_style import elevation_ramp, looks_like_elevation

        if looks_like_elevation(layer, band):

            fallback, fallback_name = elevation_ramp(), "elevation tints"
    ramp, ramp_name, error = _raster_ramp(args, fallback, fallback_name)
    if error:
        return None, None, error
    colour_function = QgsColorRampShader(lo, hi, ramp, interpolation, classification)
    colour_function.classifyColorRamp(classes, band, QgsRectangle(), provider)
    raster_shader = QgsRasterShader()
    raster_shader.setRasterShaderFunction(colour_function)
    renderer = QgsSingleBandPseudoColorRenderer(provider, band, raster_shader)
    if callable(getattr(renderer, "setClassificationMin", None)):
        renderer.setClassificationMin(lo)
        renderer.setClassificationMax(hi)
    renderer.setMinMaxOrigin(origin)
    return renderer, {"band": band, "min": lo, "max": hi, "min_max": how, "color_ramp": ramp_name,
                      "classes": classes}, None


def _build_gray(job: dict) -> tuple:

    layer, provider, args = job["layer"], job["provider"], job["args"]
    band, error = _band_arg(job, "band", 1)
    if error:
        return None, None, error
    lo, hi, origin, how = _band_limits(layer, band, job["plan"], job["low"], job["high"], job["notes"])
    gradient, error = _raster_option(args, "gradient")
    if error:
        return None, None, error
    algorithm, error = _raster_option(args, "contrast")
    if error:
        return None, None, error
    renderer = QgsSingleBandGrayRenderer(provider, band)
    renderer.setGradient(gradient)
    renderer.setContrastEnhancement(_stretch(provider, band, lo, hi, algorithm))
    renderer.setMinMaxOrigin(origin)
    return renderer, {
        "band": band, "min": lo, "max": hi, "min_max": how,
        "gradient": args.get("gradient", "black_to_white"),
        "contrast": args.get("contrast", "stretch"),
    }, None


def _build_multiband(job: dict) -> tuple:

    layer, provider, args = job["layer"], job["provider"], job["args"]
    channels = []
    for name, fallback in (("red_band", 1), ("green_band", 2), ("blue_band", 3)):
        band, error = _band_arg(job, name, fallback)
        if error:
            return None, None, error
        channels.append(band)
    algorithm, error = _raster_option(args, "contrast")
    if error:
        return None, None, error
    red, green, blue = channels
    renderer = QgsMultiBandColorRenderer(provider, red, green, blue)
    per_band = []
    origin = how = None
    for band, attach in ((red, renderer.setRedContrastEnhancement), (green, renderer.setGreenContrastEnhancement),
                         (blue, renderer.setBlueContrastEnhancement)):
        lo, hi, origin, how = _band_limits(layer, band, job["plan"], job["low"], job["high"], job["notes"],
                                           long_tail=False)
        attach(_stretch(provider, band, lo, hi, algorithm))
        per_band.append({"band": band, "min": lo, "max": hi})
    renderer.setMinMaxOrigin(origin)
    return renderer, {"bands": per_band, "min_max": how, "contrast": args.get("contrast", "stretch")}, None


def _build_hillshade(job: dict) -> tuple:

    args = job["args"]
    band, error = _band_arg(job, "band", 1)
    if error:
        return None, None, error
    light = {"azimuth": float(args.get("azimuth", 315.0)), "altitude": float(args.get("altitude", 45.0))}
    z_factor = float(args.get("z_factor", 1.0))
    applied = {"band": band, **light, "z_factor": z_factor}


    from .elevation_style import degrees_z_factor

    metres = degrees_z_factor(job["layer"])
    if metres:
        z_factor *= metres
        applied.update({"z_factor": z_factor, "z_factor_exaggeration": applied["z_factor"],
                        "z_factor_note": ("the DEM's cells are degrees and its heights metres: z_factor is the "
                                          "exaggeration times 1 / (111320 * cos(latitude)), the metres per degree "
                                          "at this layer's latitude")})
    renderer = QgsHillshadeRenderer(job["provider"], band, light["azimuth"], light["altitude"])
    renderer.setZFactor(z_factor)
    return renderer, applied, None


def _build_paletted(job: dict) -> tuple:

    from .style_defaults import paletted_renderer, raster_codes

    layer, args = job["layer"], job["args"]
    band, error = _band_arg(job, "band", 1)
    if error:
        return None, None, error
    codes = job["codes"] or raster_codes(layer, band, strict=False)
    if not codes:
        return None, None, tool_error(
            f"Band {band} of {layer.name()!r} holds no whole-number classes to list (it is continuous, or has "
            "more than 256 values).", "INVALID_ARGS",
            "singleband_pseudocolor with classes draws a continuous band; set_raster_class_style "
            "sets the values and colours of each class.")
    ramp = None
    if args.get("color_ramp") or args.get("color_stops"):
        ramp, _described, error = _raster_ramp(args)
        if error:
            return None, None, error
    renderer = paletted_renderer(layer, band, codes, ramp)
    try:
        colours = [entry.color.name() for entry in renderer.classes()][:30]
    except Exception:  # noqa: BLE001
        colours = []


    return renderer, {"band": band, "classes": len(codes), "codes": codes[:30],
                      **({"colours": colours} if colours else {}),
                      "note": "codes read from QGIS's histogram of the band (250 000 pixel sample); the colours "
                              "run through color_stops or color_ramp in code order, a default ramp without them; "
                              "set_raster_class_style sets each code's own colour and label"}, None


_RASTER_BUILDERS = {
    "singleband_pseudocolor": _build_pseudocolor,
    "singleband_gray": _build_gray,
    "multiband_color": _build_multiband,
    "hillshade": _build_hillshade,
    "paletted": _build_paletted,
}


def _long_tail(layer, band: int, lo: float, hi: float, notes: dict) -> tuple:

    from .style_defaults import long_tail_cut

    cut = long_tail_cut(layer, band, lo, hi)
    if not cut:
        return lo, hi
    notes["stretch"] = {"min": cut["min"], "max": cut["max"], "full_range": [lo, hi], "reason": cut["reason"]}
    return cut["min"], cut["max"]





def _apply_style_qml(args: dict) -> dict:
    layer, error = _layer(args["layer_name"])
    if error:
        return error
    path = _expand(args["path"])
    extension = os.path.splitext(path)[1].lower()
    if extension not in (".qml", ".sld"):
        return tool_error("Style path must end in .qml or .sld.", "INVALID_ARGS",
                          "path is local, not a URL.")
    if not os.path.isfile(path):
        return tool_error(
            f"Style file not found: {path}", "INVALID_ARGS",
            "save_style_qml writes a .qml file; path must exist already.",
        )



    from qgis.PyQt.QtXml import QDomDocument

    before = QDomDocument()
    kept = ""
    try:
        layer.exportNamedStyle(before)
        kept = before.toString()
    except Exception:  # nosec B110
        kept = ""

    classification_field = str(args.get("classification_field") or "").strip()
    if classification_field and hasattr(layer, "fields") and layer.fields().indexOf(classification_field) < 0:
        return tool_error(f"Classification field not found: {classification_field}", "INVALID_ARGS",
                          "It must be a field of the target layer.")
    if extension == ".sld" and not hasattr(layer, "loadSldStyle"):
        return tool_error("This QGIS version cannot load SLD styles.", "STYLE_FAILED",
                          "QML works here; SLD needs a newer QGIS.")
    message, success = (layer.loadSldStyle(path) if extension == ".sld" else layer.loadNamedStyle(path))
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
    if classification_field:
        renderer = getattr(layer, "renderer", lambda: None)()
        setter = getattr(renderer, "setClassAttribute", None)
        if setter is not None:
            setter(classification_field)
    _refresh(layer)
    out = {"layer_id": layer.id(), "name": layer.name(), "path": path, "format": extension[1:], "message": message}
    if classification_field:
        out["classification_field"] = classification_field



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
        return tool_error(f"Failed to save style: {message}", "STYLE_FAILED", "path is writable and ends in .qml.")
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
                    "0 is transparent, 1 is opaque.",
                )
            layer.setOpacity(opacity)
            applied = opacity
        elif prop == "name":
            from ._layers import take_layer_name

            applied, renamed = take_layer_name(str(value), keep_id=layer.id())
            layer.setName(applied)
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
            return tool_error(f"Unknown property: {prop!r}.", "INVALID_ARGS", f"One of {list(LAYER_PROPERTIES)}.")
    except (TypeError, ValueError) as e:
        return tool_error(
            f"Bad value for {prop}: {e}", "INVALID_ARGS",
            "opacity, min_scale and max_scale take numbers; scale_visibility takes true or false.",
        )
    _refresh(layer)
    out = {"layer_id": layer.id(), "name": layer.name(), "property": prop, "value": applied}
    if prop == "name" and renamed:
        out["renamed_intermediates"] = renamed
    return out


def _set_layer_order(args: dict) -> dict:










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
            return tool_error(f"Layer {layer.name()!r} is listed twice.", "INVALID_ARGS", "Each layer once.")
        nodes.append(node)
        resolved.append(layer)
    if not nodes:
        return tool_error("Nothing to reorder.", "INVALID_ARGS",
                          "One layer takes layer_name and position (top, bottom, above, below); "
                          "a whole group takes layers, top to bottom.")
    parent = nodes[0].parent()
    for node in nodes[1:]:
        if node.parent() is not parent:
            return tool_error(
                "The list form reorders one group; these layers are in different groups.",
                "INVALID_ARGS",
                "set_layer_order with layer_name and position top, bottom, above or below "
                "moves one layer at a time, across groups.",
            )
    _arrange_children(parent, nodes)
    root.setHasCustomLayerOrder(False)
    return {
        "order": [layer.name() for layer in resolved],
        "layer_ids": [layer.id() for layer in resolved],
        "group": parent.name() if hasattr(parent, "name") and parent is not root else "(root)",
        "custom_draw_order_cleared": True,
    }


def _arrange_children(parent, listed: list) -> None:







    current = list(parent.children())
    held = [position for position, child in enumerate(current) if child in listed]
    incoming = dict(zip(held, listed))
    arranged = [incoming.get(position, child) for position, child in enumerate(current)]
    parent.insertChildNodes(0, [child.clone() for child in arranged])
    for child in current:
        parent.removeChildNode(child)


def _tree_node(name: str, root):









    project = QgsProject.instance()
    exact = project.mapLayer(name) or any(
        candidate.name() == name for candidate in project.mapLayers().values() if candidate is not None)
    group = None if exact or not name else root.findGroup(name)
    if group is not None:
        return group, None, None
    layer = resolve_layer(name)
    if layer is not None:
        node = root.findLayer(layer.id())
        if node is None:
            return None, layer, tool_error(f"Layer {layer.name()!r} has no node in the layer tree.", "INVALID_ARGS",
                                           "get_layer_tree shows the tree; non-spatial tables have no node.")
        return node, layer, None
    return None, None, layer_not_found(name)


def _move_one_layer(args: dict) -> dict:

    name = str(args.get("layer_name") or "").strip()
    if not name:
        return tool_error("layer_name is empty.", "INVALID_ARGS",
                          "layer_name is the layer or group to move; layers is a whole order.")
    position = str(args.get("position") or "").strip().lower()
    if position not in ("top", "bottom", "above", "below"):
        return tool_error("position is top, bottom, above or below.", "INVALID_ARGS",
                          "above and below also need reference_layer.")
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    node, layer, error = _tree_node(name, root)
    if error:
        return error
    parent = node.parent() or root
    came_from = parent
    reference = str(args.get("reference_layer") or "").strip()

    if position in ("above", "below"):
        if not reference:
            return tool_error(f"{position} needs reference_layer.", "INVALID_ARGS",
                              "top or bottom need no reference_layer.")
        other_node, _other, error = _tree_node(reference, root)
        if error:
            return error
        if other_node is node:
            return tool_error("A node cannot be moved relative to itself.", "INVALID_ARGS",
                              "It must be a different layer or group.")
        parent = other_node.parent() or root
        ancestor = parent
        while ancestor is not None:
            if ancestor is node:
                return tool_error(f"{reference!r} is inside the group {name!r} being moved.", "INVALID_ARGS",
                                  "reference_layer must be outside the group being moved.")
            ancestor = ancestor.parent()
        siblings = [child for child in parent.children() if child is not node]
        index = siblings.index(other_node)
        target = index if position == "above" else index + 1
    else:
        siblings = [child for child in parent.children() if child is not node]
        target = 0 if position == "top" else len(siblings)

    moved = layer_order.move_to_index(node, parent, target)
    root.setHasCustomLayerOrder(False)
    where = parent.name() if hasattr(parent, "name") and parent is not root else "(root)"
    if layer is not None:
        out = {"layer": layer.name(), "layer_id": layer.id(), "requested": position, "moved": moved, "group": where}
    else:
        out = {"moved_group": name, "requested": position, "moved": moved, "group": where}
    if reference and position in ("above", "below"):
        out["reference_layer"] = reference



    if moved and parent is not came_from:
        out["left_group"] = came_from.name() if came_from is not root else "(root)"
    if layer is not None:


        out.update(layer_order.cover_report(layer, root))
        return out


    children = list(parent.children())
    landed = next((child for child in children if child.name() == name
                   and not hasattr(child, "layerId")), None)
    if landed is not None:
        out["position"] = children.index(landed) + 1
        out["of"] = len(children)
        out["layers_inside"] = [found.name() for found in landed.findLayers()]
    return out


def _crs_info(layer) -> dict:

    crs = layer.crs()

    as_proj = getattr(crs, "toProj", None) or crs.toProj4
    out = {"layer_id": layer.id(), "name": layer.name()}
    out.update(authid=crs.authid(), description=crs.description(), is_geographic=crs.isGeographic(),
               units=_units(crs), proj=as_proj())
    return out


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
            "An authority id such as EPSG:4326 or EPSG:32632, or a proj4 string "
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
        "run_processing native:reprojectlayer does that."
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
            "qgis_edit_commit or qgis_edit_rollback ends the open session.",
        )
    if not layer.dataProvider().deleteAttributes([idx]):
        return tool_error(
            f"The provider refused to delete field {field_name!r}.",
            "INVALID_ARGS",
            "The data source may be read-only or not support schema changes; "
            "export_layer makes a GeoPackage copy that does.",
        )
    layer.updateFields()
    return {
        "layer_id": layer.id(),
        "name": layer.name(),
        "deleted": field_name,
        "fields": [f.name() for f in layer.fields()],
    }


def _non_null_count(layer, field: str, limit: int = 500) -> int:

    from qgis.core import QgsFeatureRequest

    idx = layer.fields().indexOf(field)
    if idx < 0:
        return 0
    request = QgsFeatureRequest().setLimit(limit).setSubsetOfAttributes([idx])
    request.setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry"))
    count = 0
    for feature in layer.getFeatures(request):
        value = feature.attribute(idx)
        if value is not None and not (hasattr(value, "isNull") and value.isNull()) \
                and str(value) != "NULL":
            count += 1
    return count


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
            "new_name must be unused.",
        )
    if layer.isEditable():
        return tool_error(
            f"Layer {layer.name()!r} has an open edit session.", "INVALID_ARGS",
            "qgis_edit_commit or qgis_edit_rollback ends the open session.",
        )





    storage = ""
    try:
        storage = str(layer.dataProvider().storageType() or "")
    except Exception:  # noqa: BLE001
        storage = ""
    if "flatgeobuf" in storage.lower() or layer.source().split("|", 1)[0].lower().endswith(".fgb"):
        return tool_error(
            f"{layer.name()!r} is a FlatGeobuf file, and renaming a field there empties every value of it.",
            "INVALID_ARGS",
            f"Nothing was changed. run_processing native:renametablefield with INPUT {layer.id()!r}, "
            f"FIELD {old_name!r}, NEW_NAME {new_name!r} and output_name {layer.name()!r}.",
        )
    before = _non_null_count(layer, old_name)
    if not layer.dataProvider().renameAttributes({idx: new_name}):
        return tool_error(
            f"The provider refused to rename field {old_name!r}.",
            "INVALID_ARGS",
            "Shapefiles cap names at 10 characters and some providers cannot rename; "
            "export_layer makes a copy that can.",
        )
    layer.updateFields()
    after = _non_null_count(layer, new_name)
    if before and not after:
        return tool_error(
            f"The provider renamed {old_name!r} to {new_name!r} and lost its values: {before} non-null before, "
            f"none after.", "EXECUTION_FAILED",
            "A layer recreated from its source, then run_processing native:renametablefield, avoids this.",
        )
    return {
        "layer_id": layer.id(),
        "name": layer.name(),
        "old_name": old_name,
        "new_name": new_name,
        "fields": [f.name() for f in layer.fields()],
    }


def _sample_key_values(layer, field: str, limit: int = 5, scan: int = 200) -> list:

    from qgis.core import QgsFeatureRequest

    idx = layer.fields().indexOf(field)
    if idx < 0:
        return []
    request = QgsFeatureRequest().setLimit(scan).setSubsetOfAttributes([idx])
    request.setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry"))
    seen: set = set()
    values: list = []
    for feature in layer.getFeatures(request):
        value = feature.attribute(idx)
        if value is None or (hasattr(value, "isNull") and value.isNull()):
            continue
        if repr(value) in seen:
            continue
        seen.add(repr(value))
        values.append(value)
        if len(values) >= limit:
            break
    return values





_JOIN_CHECK_MAX_KEYS = 50_000


_JOIN_CHECK_REMOTE_ROWS = 500
_UNMODELLED_KEY = object()


def _shortest_double_precision() -> int:
    option = enum_member(QLocale, "FloatingPointPrecisionOption", "FloatingPointShortest", -128)
    return int(getattr(option, "value", option))


_SHORTEST_DOUBLE = _shortest_double_precision()
_ISO_DATE = enum_member(Qt, "DateFormat", "ISODate")
_ISO_WITH_MS = enum_member(Qt, "DateFormat", "ISODateWithMs")


def _is_remote_vector(layer) -> bool:

    return layer_order.is_web_service(layer)


def _join_key(value):









    if value is None or (hasattr(value, "isNull") and value.isNull()):
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return QLocale.c().toString(value, "g", _SHORTEST_DOUBLE)
    if isinstance(value, str):
        return value
    if isinstance(value, QDateTime):
        return value.toString(_ISO_WITH_MS)
    if isinstance(value, QDate):
        return value.toString(_ISO_DATE)
    if isinstance(value, QTime):
        return value.toString(_ISO_WITH_MS)
    return _UNMODELLED_KEY


def _join_match_count(target, target_field: str, join, join_field: str):









    from qgis.core import QgsFeatureRequest

    join_idx = join.fields().indexOf(join_field)
    if _is_remote_vector(join):
        request = QgsFeatureRequest().setLimit(_JOIN_CHECK_REMOTE_ROWS + 1).setSubsetOfAttributes([join_idx])
        request.setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry"))
        join_values = [feature.attribute(join_idx) for feature in join.getFeatures(request)]
        if len(join_values) > _JOIN_CHECK_REMOTE_ROWS:
            return None
    else:
        join_values = join.uniqueValues(join_idx, _JOIN_CHECK_MAX_KEYS + 1)
        if len(join_values) > _JOIN_CHECK_MAX_KEYS:
            return None
    join_keys: set = set()
    for value in join_values:
        key = _join_key(value)
        if key is _UNMODELLED_KEY:
            return None
        if key is not None:
            join_keys.add(key)

    target_idx = target.fields().indexOf(target_field)
    if _is_remote_vector(target):
        request = QgsFeatureRequest().setLimit(_JOIN_CHECK_REMOTE_ROWS).setSubsetOfAttributes([target_idx])
        request.setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry"))
        by_repr: dict = {}
        for feature in target.getFeatures(request):
            value = feature.attribute(target_idx)
            by_repr.setdefault(repr(value), value)
        target_values = list(by_repr.values())
        complete = False
    else:
        target_values = list(target.uniqueValues(target_idx, _JOIN_CHECK_MAX_KEYS + 1))
        complete = len(target_values) <= _JOIN_CHECK_MAX_KEYS
        target_values = target_values[:_JOIN_CHECK_MAX_KEYS]

    checked = matched = 0
    unmatched_keys: list = []
    for value in sorted(target_values, key=repr):
        key = _join_key(value)
        if key is _UNMODELLED_KEY:
            return None
        checked += 1
        if key is not None and key in join_keys:
            matched += 1
        elif len(unmatched_keys) < 5:
            unmatched_keys.append(value)
    return {"checked": checked, "matched": matched, "unmatched_keys": unmatched_keys, "complete": complete}


def _join_spec(join, join_field: str, target_field: str, prefix):

    spec = QgsVectorLayerJoinInfo()
    spec.setJoinFieldName(join_field)
    spec.setTargetFieldName(target_field)


    spec.setJoinLayerId(join.id())
    spec.setJoinLayer(join)
    if prefix:
        spec.setPrefix(prefix)
    spec.setUsingMemoryCache(True)
    return spec


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
    target_type = target.fields().field(target.fields().indexOf(target_field)).typeName()
    join_type = join.fields().field(join.fields().indexOf(join_field)).typeName()




    match = _join_match_count(target, target_field, join, join_field)
    if match and match["complete"] and match["checked"] and match["matched"] == 0:
        join_samples = _sample_key_values(join, join_field)
        return tool_error(
            f"The join would match none of the {match['checked']} distinct {target_field!r} values "
            f"({target_type}) on {target.name()!r} against {join_field!r} ({join_type}) on "
            f"{join.name()!r}, so it was not added.",
            "JOIN_NO_MATCHES",
            f"Sample {target_field!r} values: {match['unmatched_keys']!r}. Sample {join_field!r} values: "
            f"{join_samples!r}. add_field/field_calculator (to_string or to_int) casts one side "
            f"when the types differ.",
        )

    before = {f.name() for f in target.fields()}
    if not target.addJoin(_join_spec(join, join_field, target_field, args.get("prefix"))):
        return tool_error(
            "Failed to add the table join.",
            "JOIN_FAILED",
            "A join on the same layer may already exist; the key fields must hold the same kind of value.",
        )
    target.updateFields()
    added = [f.name() for f in target.fields() if f.name() not in before]

    result = {
        "layer_id": target.id(),
        "name": target.name(),
        "join_layer_id": join.id(),
        "joined_fields": added,
        "field_count": len(added),
        "count_units": "fields",
    }
    if match and match["checked"] and match["matched"] / match["checked"] < 0.5:
        checked, matched = match["checked"], match["matched"]
        scope = "" if match["complete"] else f" (counted on {checked} of the layer's distinct values)"
        result["warning"] = (
            f"Only {matched} of {checked} distinct {target_field!r} values found a match in {join_field!r}"
            f"{scope} ({target_type} vs {join_type}). Unmatched sample of {target_field!r}: "
            f"{match['unmatched_keys']!r}."
        )
        result["matched"] = matched
        result["unmatched"] = checked - matched
        result["match_units"] = "distinct key values"
        result["unmatched_keys"] = match["unmatched_keys"]
        result["target_field_type"] = target_type
        result["join_field_type"] = join_type
    return result





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
        return tool_error("Nothing to set.", "INVALID_ARGS", "title, abstract, keywords or rights.")
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
        return tool_error("Only vector layers have fields.", "INVALID_ARGS", "Vector layers only.")
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
    if isinstance(layer, QgsVectorLayer):




        from qgis.core import QgsFeatureRequest, QgsMapLayerStyle






        count = layer_order.feature_count_of(layer)
        ceiling = min(limits.current("MAX_FEATURES_MATERIALISED"), limits.current("MAX_FEATURES_CREATED"))
        if count is not None and count > ceiling:
            return limits.refusal(
                f"The layer {layer.name()!r}", f"{count:,} features",
                f"{ceiling:,} for an independent copy made in memory",
                "export_layer or save_layer_to_gpkg writes a separate file that can be added.")


        clone = layer.materialize(QgsFeatureRequest().setLimit(ceiling + 1))
        if clone is None or not clone.isValid():
            return tool_error("QGIS could not create an independent copy.", "EXECUTION_FAILED",
                              "export_layer writes a separate file that can be added.")
        if clone.featureCount() > ceiling:
            return limits.refusal(
                f"The layer {layer.name()!r}", f"more than {ceiling:,} features",
                f"{ceiling:,} for an independent copy made in memory",
                "export_layer avoids this; no partial copy was added.")
        style = QgsMapLayerStyle()
        style.readFromLayer(layer)
        style.writeToLayer(clone)
        clone.setMetadata(layer.metadata())
    else:
        clone = layer.clone()
    clone.setName(args.get("new_name") or f"{layer.name()} copy")
    QgsProject.instance().addMapLayer(clone)
    out = {
        "layer_id": clone.id(),
        "name": clone.name(),
        "source_layer_id": layer.id(),
        "crs": crs_ref(clone.crs()),
        "units": _units(clone.crs()),
    }
    if isinstance(clone, QgsVectorLayer):
        out["feature_count"] = clone.featureCount()
        out["count_units"] = "features"
        out["independent_data"] = True
    return out
