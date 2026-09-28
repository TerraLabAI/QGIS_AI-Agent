# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


from __future__ import annotations

import contextlib
import time

from qgis.core import QgsVectorLayer
from qgis.utils import iface

from ..core import layer_order, limits
from ..core.logger import log_warning
from ..core.qt_compat import enum_member
from ._compat import QVAR_DOUBLE, QVAR_INT, QVAR_LONGLONG, QVAR_STRING
from .layer_lookup import _field_not_found_error, _find_layer, _layer_not_found_error



_MAX_CATEGORIES = 500





_READABLE_CATEGORIES = 30
_OTHER_LABEL = "Other"
_OTHER_COLOR = "#9e9e9e"






_MAX_COLOURED_CATEGORIES = 10_000


def _style_to_put_back(layer) -> str:





    try:
        from ..core.snapshot import style_xml

        return style_xml(layer)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Previous style not read: {exc}")
        return ""


def _previous_style_keys(layer, kept: str, changed: str = "symbology") -> dict:








    if not kept:
        return {}
    try:
        from ..core.snapshot import remember_style

        path = remember_style(layer, kept)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Previous style not kept: {exc}")
        return {}
    if not path:
        return {}
    return {
        "previous_style_qml": path,
        "previous_style_note": (f"this call changed the layer's {changed}; apply_style_qml with this "
                                "layer_name and this path puts back exactly what it looked like before "
                                "this call, and nothing else of the project"),
    }


def _set_layer_legend_image(layer, args: dict) -> dict:





    return {"supported": False, "layer": layer.name() if layer is not None else args.get("layer_name"),
            "capability": "manual_legend_image", "reason":
            "QGIS exposes no public API to attach a legend image to a map layer. "
            "A layout image item or the service's native legend URL; no layer property was changed."}


def _set_diagram_renderer(layer, args: dict, kept_style: str = "") -> dict:
    from .diagram_style import set_diagram_renderer

    return set_diagram_renderer(layer, args, kept_style, _previous_style_keys)


def _set_layer_style(args: dict) -> dict:
    from qgis.core import (
        QgsCategorizedSymbolRenderer,
        QgsGraduatedSymbolRenderer,
        QgsProperty,
        QgsRenderContext,
        QgsRendererCategory,
        QgsSingleSymbolRenderer,
        QgsStyle,
        QgsSymbol,
        QgsWkbTypes,
    )
    from qgis.PyQt.QtGui import QColor







    for theirs, ours in (("layer", "layer_name"), ("fill_color", "color"), ("symbol_size", "size")):
        if args.get(ours) in (None, "") and args.get(theirs) not in (None, ""):
            args[ours] = args[theirs]
    target = args.get("layer_name")
    if not target:
        return {"_error": "layer_name is required.", "code": "INVALID_ARGS",
                "suggestion": "It takes the layer name or id."}
    layer = _find_layer(target)
    if not layer:
        return _layer_not_found_error(target)

    if args.get("legend_image") is not None:
        return _set_layer_legend_image(layer, args)
    if not isinstance(layer, QgsVectorLayer):






        return _style_raster_layer(layer, target, args)

    style_type = str(args.get("style_type") or "").strip()
    if not style_type:
        return {"_error": "style_type is required.", "code": "INVALID_ARGS",
                "suggestion": "single, categorized, graduated or cluster."}

    bad_argument = _style_args_error(layer, args)
    if bad_argument:
        return bad_argument




    if not layer.isSpatial():
        return {"_error": f"Layer {layer.name()!r} has no geometry, so there is nothing on the map to style.",
                "code": "INVALID_ARGS",
                "suggestion": "Style the layer that draws these rows, or join this table to it first."}
    if (args.get("size_expression") and style_type != "cluster"
            and layer.geometryType() == enum_member(QgsWkbTypes, "GeometryType", "PolygonGeometry")):
        return {"_error": (f"size_expression sizes point markers or line widths, and {layer.name()!r} "
                           "is a polygon layer. Nothing was changed."),
                "code": "INVALID_ARGS",
                "suggestion": "A graduated style on the field shows its values on polygons."}
    proportional = None
    if args.get("size_field") and style_type != "cluster":
        proportional = _proportional_request(layer, args)
        if "_error" in proportional:
            return proportional






    style_type, coerced_note = _style_for_geometry(layer, style_type, args)



    color_ramp_name = args.get("color_ramp")


    invert_ramp = args.get("invert_ramp") is True
    if isinstance(color_ramp_name, str) and color_ramp_name.lower().endswith("_r"):
        color_ramp_name, invert_ramp = color_ramp_name[:-2], not invert_ramp
    ramp = None
    if color_ramp_name and style_type in ("categorized", "graduated"):
        default_style = QgsStyle.defaultStyle()
        ramp = default_style.colorRamp(color_ramp_name)
        if ramp is None:


            same = [n for n in default_style.colorRampNames() if n.lower() == color_ramp_name.lower()]
            if same:
                ramp = default_style.colorRamp(same[0])
            else:
                try:
                    from qgis.core import QgsColorBrewerColorRamp
                    scheme = next((s for s in QgsColorBrewerColorRamp.listSchemeNames()
                                   if s.lower() == color_ramp_name.lower()), None)
                    ramp = QgsColorBrewerColorRamp(scheme, 9) if scheme else None
                except Exception:  # nosec B110
                    ramp = None
        if ramp is None:
            from .elevation_style import elevation_ramp, is_elevation_alias

            if is_elevation_alias(color_ramp_name):




                ramp = elevation_ramp()
            else:
                return {
                    "_error": f"Unknown color ramp: {color_ramp_name!r}.",
                    "available_ramps": default_style.colorRampNames(),
                }
    if style_type == "graduated" and ramp is None:
        color_ramp_name = "Viridis"
        ramp = QgsStyle.defaultStyle().colorRamp("Viridis") or QgsStyle.defaultStyle().colorRamp("Blues")
    if ramp is not None and invert_ramp:
        ramp = ramp.clone()
        ramp.invert()


    if style_type in ("pie", "bar", "diagram"):
        return _set_diagram_renderer(layer, args, kept_style=_style_to_put_back(layer))



    kept_style = _style_to_put_back(layer)

    fold_note: dict = {}

    auto_choice: dict = {}

    built_classes = style_type in ("categorized", "graduated")
    if style_type == "single":
        color = args.get("color", "#3388ff")
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.setColor(QColor(color))
        renderer = QgsSingleSymbolRenderer(symbol)
        layer.setRenderer(renderer)

    elif style_type == "categorized" and _relabels_only(layer, args):



        renderer = layer.renderer().clone()
        ramp = None
        built_classes = False
        layer.setRenderer(renderer)

    elif style_type == "categorized" and (args.get("color_field") or args.get("keep_colors") is True):


        built = _coloured_categories(layer, args)
        if "_error" in built:
            return built
        renderer = built.pop("renderer")
        fold_note = built
        ramp = None

        built_classes = False
        layer.setRenderer(renderer)

    elif style_type == "categorized":

        expression = str(args.get("value_expression") or "").strip()
        field = expression or args.get("field")
        if not field:
            return {"_error": "Field (or value_expression) is required for categorized style"}

        idx = -1 if expression else layer.fields().indexOf(field)
        if idx < 0 and not expression:
            return _field_not_found_error(layer, field)

        expression_counts: dict = {}
        if expression:


            values, expression_scanned = _expression_values(
                layer, expression, limits.current("MAX_FEATURES_MATERIALISED"))
            by_text: dict = {}
            for value in values:
                key = str(value)
                expression_counts[key] = expression_counts.get(key, 0) + 1
                by_text.setdefault(key, value)
            unique_values = list(by_text.values())
        else:




            unique_values = list(layer.uniqueValues(idx, _MAX_CATEGORIES + 1))


        unique_values = [value for value in unique_values if value is not None]
        n = len(unique_values)




        overflow = n > _MAX_CATEGORIES





        with contextlib.suppress(TypeError):
            unique_values = sorted(unique_values, key=lambda v: (str(type(v)), v))





        max_classes = _requested_classes(args) or _READABLE_CATEGORIES
        folded, scanned = [], 0
        if n > max_classes:
            if expression:
                counts, scanned = expression_counts, expression_scanned
            else:
                counts, scanned = _value_frequencies(layer, idx, limits.current("MAX_FEATURES_MATERIALISED"))
            if overflow:
                n = len(counts)
                by_text = {str(v): v for v in unique_values}
                ranked = sorted(counts, key=lambda text: (-counts[text], text))
                unique_values = [by_text.get(text, text) for text in ranked[:max_classes]]
                folded = None
            else:
                ranked = sorted(unique_values, key=lambda v: (-counts.get(str(v), 0), str(v)))
                kept = {str(v) for v in ranked[:max_classes]}
                folded = [v for v in unique_values if str(v) not in kept]
                unique_values = [v for v in unique_values if str(v) in kept]

        shown = len(unique_values)
        categories = []
        for i, value in enumerate(unique_values):
            symbol = QgsSymbol.defaultSymbol(layer.geometryType())
            if ramp is not None:
                frac = i / (shown - 1) if shown > 1 else 0.0
                symbol.setColor(ramp.color(frac))
            else:
                hue = (i * 37) % 360
                symbol.setColor(QColor.fromHsl(hue, 178, 128))
            categories.append(QgsRendererCategory(value, symbol, _category_label(value)))
        if folded or folded is None:




            other = QgsSymbol.defaultSymbol(layer.geometryType())
            other.setColor(QColor(_OTHER_COLOR))
            tail = list(folded) if folded else ""
            if tail or folded is None:
                categories.append(QgsRendererCategory(tail, other, _OTHER_LABEL))
            n_folded = len(folded) if folded else max(n - shown, 0)
            fold_note = {
                "classes_shown": shown,
                "classes_folded": n_folded,
                "warning": (
                    f"{n} distinct values in {field}: the {shown} most frequent got a class each, "
                    f"{n_folded} others share one grey class; a graduated style or a field with "
                    "fewer values reads better"
                ),
            }



            total = layer_order.feature_count_of(layer)
            if scanned and (total is None or scanned < total):
                fold_note["warning"] += f" (frequencies measured on the first {scanned} features)"



        if (args.get("multi_channel", True) is not False
                and "size" not in args and "size_expression" not in args and "size_field" not in args
                and len(categories) == 2):
            sizes = (float(args.get("multi_channel_min_size", 2.0)), float(args.get("multi_channel_max_size", 5.0)))
            for symbol, value in zip((c.symbol() for c in categories), sizes):
                if hasattr(symbol, "setSize"):
                    symbol.setSize(value)
                elif hasattr(symbol, "setWidth"):
                    symbol.setWidth(value)
            fold_note["multi_channel"] = "two classes use color and size; explicit size arguments were not supplied"


        renderer = QgsCategorizedSymbolRenderer(field, categories)
        layer.setRenderer(renderer)

    elif style_type == "graduated":

        expression = str(args.get("value_expression") or "").strip()
        field = expression or args.get("field")
        if not field:
            return {"_error": "Field (or value_expression) is required for graduated style"}

        idx = -1 if expression else layer.fields().indexOf(field)
        if idx < 0 and not expression:
            return _field_not_found_error(layer, field)




        if not expression and not layer.fields().at(idx).isNumeric():
            type_name = layer.fields().at(idx).typeName() or "text"
            field = 'to_real("{}")'.format(str(field).replace('"', '""'))
            fold_note["field_note"] = (f"{args['field']} is {type_name}: its values were read as numbers with "
                                       "to_real, and a value that is not a number counts as no value")

        mode_names = {
            "equal_interval": "EqualInterval",
            "quantile": "Quantile",
            "jenks": "Jenks",
            "pretty": "Pretty",
        }
        breaks = _class_limits(args.get("breaks"))
        if isinstance(breaks, dict):
            return breaks
        asked_mode = args.get("classification_mode")
        if not asked_mode and not breaks:



            from .style_defaults import graduated_choice

            auto_choice = _expression_choice(layer, expression) if expression else graduated_choice(layer, idx)
            asked_mode = auto_choice.get("mode") or "equal_interval"
        mode_name = mode_names.get(asked_mode, "EqualInterval")
        classes = int(args.get("classes", 5) or 5)
        base_symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        if breaks:


            renderer = _fixed_ranges(field, breaks, base_symbol, ramp)
            fold_note["breaks"] = breaks
        else:
            try:


                from qgis.core import QgsApplication
                method = QgsApplication.classificationMethodRegistry().method(mode_name)
                renderer = QgsGraduatedSymbolRenderer(field, [])
                renderer.setSourceSymbol(base_symbol)
                renderer.setClassificationMethod(method)
                if ramp is not None:
                    renderer.setSourceColorRamp(ramp.clone())
                renderer.updateClasses(layer, classes)
            except Exception:
                try:
                    fallback = enum_member(QgsGraduatedSymbolRenderer, "Mode", "EqualInterval")
                    mode = getattr(QgsGraduatedSymbolRenderer, mode_name, fallback)
                    renderer = QgsGraduatedSymbolRenderer.createRenderer(
                        layer, field, classes, mode, base_symbol, ramp.clone() if ramp is not None else None
                    )
                except Exception as exc:
                    return {
                        "_error": f"Graduated renderer is not available on this QGIS version: {exc}",
                        "code": "EXECUTION_FAILED",
                    }
        if len(renderer.ranges()) == 0:
            return {
                "_error": f"Field {field!r} has no values to classify.",
                "code": "INVALID_ARGS",
                "suggestion": "The field must hold numbers on at least one feature, or another field works.",
            }
        _readable_range_labels(renderer, args.get("units"))
        layer.setRenderer(renderer)

    elif style_type == "cluster":
        renderer = _cluster_renderer(layer, args)
        if isinstance(renderer, dict):
            return renderer
        layer.setRenderer(renderer)

    else:
        return {"_error": f"Unknown style type: {style_type}"}



    bounds = ([(float(r.lowerValue()), float(r.upperValue())) for r in renderer.ranges()]
              if style_type == "graduated" else [])
    no_value: dict = {}
    if built_classes:
        classed = renderer
        renderer, no_value = _no_value_class(layer, renderer, args)
        if renderer is not classed:
            layer.setRenderer(renderer)

    labelled: dict = {}
    if style_type == "categorized" and str(args.get("label_field") or "").strip():
        labelled = _labels_from_field(layer, renderer, str(args["label_field"]).strip())

    applied = _apply_symbol_tweaks(renderer, args) if style_type != "cluster" else {"stroke_color"}

    if args.get("size_expression") and style_type != "cluster":
        for symbol in renderer.symbols(QgsRenderContext()):
            if hasattr(symbol, "setDataDefinedSize"):
                symbol.setDataDefinedSize(QgsProperty.fromExpression(args["size_expression"]))
            elif hasattr(symbol, "setDataDefinedWidth"):
                symbol.setDataDefinedWidth(QgsProperty.fromExpression(args["size_expression"]))
    proportional_done = ""
    if proportional and not args.get("size_expression"):
        from .style_defaults import proportional_size

        proportional_done = proportional_size(renderer, proportional["field"], proportional["low"],
                                              proportional["high"], proportional["min_size"],
                                              proportional["max_size"], proportional["markers"])

    opacity = args.get("opacity")
    if opacity is not None:
        try:
            layer.setOpacity(float(max(0.0, min(1.0, opacity))))
        except (TypeError, ValueError):
            pass

    layer.triggerRepaint()

    if iface is not None and iface.layerTreeView() is not None:
        iface.layerTreeView().refreshLayerSymbology(layer.id())
    result = {"styled": layer.name(), "type": style_type}
    result.update(_previous_style_keys(layer, kept_style))
    result.update(_wider_than_the_selection(layer))
    if coerced_note:
        result["style_type_changed"] = coerced_note
    if ramp is not None:
        result["color_ramp"] = color_ramp_name
        if invert_ramp:
            result["ramp_reversed"] = True
    if built_classes and str(args.get("value_expression") or "").strip():
        result["value_expression"] = str(args["value_expression"]).strip()
    if args.get("classification_mode"):
        result["classification_mode"] = args["classification_mode"]
    elif auto_choice:
        result["classification_mode"] = auto_choice["mode"]
        result["classification_reason"] = auto_choice["reason"]
        if auto_choice.get("note"):
            result["classification_note"] = auto_choice["note"]


    not_applied = []
    for key in ("stroke_color", "stroke_width", "stroke_width_unit", "size", "fill"):
        if args.get(key) is None:
            continue
        if key in applied:
            result[key] = args[key]
        else:
            not_applied.append(key)
    if not_applied:
        result["not_applied"] = not_applied
        result["not_applied_note"] = ("these arguments have no place on this layer's symbols and changed nothing: "
                                      "size sizes point markers, fill is a polygon's inside, stroke_width and "
                                      "stroke_color are a line or an outline")
    if style_type == "graduated":
        result["classes"] = len(bounds)
        result.update(fold_note)
        explicit_mode = str(args.get("classification_mode") or "").strip().lower()
        if explicit_mode and not fold_note.get("breaks"):




            from .style_defaults import class_counts, dominant_class_note

            counts = class_counts(layer, idx, bounds)
            if counts:
                result["class_counts"] = counts
                warning = dominant_class_note(counts, field, mode_name)
                if warning:
                    result["classification_warning"] = warning
            if explicit_mode == "pretty" and len(bounds) != classes:
                result["classes_requested"] = classes
                result["classification_note"] = (
                    f"pretty chose round-number breaks and came out to {len(bounds)} classes, not the "
                    f"{classes} asked for; jenks or quantile classification_mode matches the count exactly"
                )
    elif style_type == "categorized":
        if hasattr(renderer, "categories"):
            result["classes"] = len(renderer.categories())



            from .style_defaults import closest_class_colours

            with contextlib.suppress(Exception):
                result.update(closest_class_colours(layer, renderer, layer_order.is_remote_vector(layer)))
        result.update(fold_note)
        result.update(labelled)
    if style_type == "cluster":
        result["cluster_distance_mm"] = renderer.tolerance()
        result["separates_on_zoom"] = True
    result.update(no_value)
    if proportional_done:
        result["size_field"] = {
            "field": proportional["field"], "values": [proportional["low"], proportional["high"]],
            "sizes_mm": [proportional["min_size"], proportional["max_size"]], "scale": proportional_done,
            "note": ("marker area follows the value (square-root size), so a value twice as large draws twice "
                     "the area; the legend shows the sizes" if proportional_done == "area"
                     else "line width follows the value linearly")}
    elif proportional and args.get("size_expression"):
        result["size_field_note"] = "size_expression was given as well and sizes the symbols; size_field was not used"
    return result


def _class_limits(breaks):

    if not breaks:
        return []
    try:
        limits = sorted({float(value) for value in breaks})
    except (TypeError, ValueError):
        return {"_error": "breaks are numbers, the class limits from lowest to highest.", "code": "INVALID_ARGS"}
    if len(limits) < 2:
        return {"_error": "breaks needs at least two different limits (one class).", "code": "INVALID_ARGS",
                "suggestion": "[0, 20, 40, 60, 80, 100] gives five classes over 0 to 100."}
    return limits


def _fixed_ranges(attribute: str, limits: list, base_symbol, ramp):

    from qgis.core import QgsGraduatedSymbolRenderer, QgsRendererRange

    count = len(limits) - 1
    ranges = []
    for i in range(count):
        symbol = base_symbol.clone()
        symbol.setColor(ramp.color(i / (count - 1) if count > 1 else 0.0))
        ranges.append(QgsRendererRange(limits[i], limits[i + 1], symbol, ""))
    renderer = QgsGraduatedSymbolRenderer(attribute, ranges)
    renderer.setSourceSymbol(base_symbol.clone())
    renderer.setSourceColorRamp(ramp.clone())
    return renderer


def _no_value_class(layer, renderer, args: dict) -> tuple:








    from qgis.core import Qgis, QgsAggregateCalculator, QgsRendererCategory, QgsRuleBasedRenderer, QgsSymbol
    from qgis.PyQt.QtGui import QColor

    attribute = renderer.classAttribute()
    missing = None
    total = layer_order.feature_count_of(layer)
    count = enum_member(Qgis, "Aggregate", "Count", None) or enum_member(QgsAggregateCalculator, "Aggregate", "Count")
    if total:
        try:
            counted, ok = layer.aggregate(count, attribute)
            if ok and counted is not None:
                missing = max(0, int(total) - int(counted))
        except Exception as exc:  # noqa: BLE001
            log_warning(f"set_layer_style: no-value count of {attribute!r} not read: {exc}")
    note: dict = {"features_without_value": missing} if missing else {}
    colour = str(args.get("null_class_color") or "").strip()
    label = str(args.get("null_class_label") or "").strip() or "No data"
    if not (colour or args.get("null_class_label")) or missing == 0:
        if missing:
            note["no_value_note"] = (f"{missing} features have no value in {attribute} and are not drawn; "
                                     "null_class_color draws them in a class of their own")
        return renderer, note
    symbol = QgsSymbol.defaultSymbol(layer.geometryType())
    symbol.setColor(QColor(colour or "#bdbdbd"))
    note["null_class"] = {"label": label, "color": symbol.color().name()}
    if renderer.type() == "categorizedSymbol":
        if any(not isinstance(c.value(), list) and (c.value() is None or str(c.value()) in ("", "NULL"))
               for c in renderer.categories()):
            note["null_class"]["note"] = "the features with no value are drawn in the existing catch-all class"
            return renderer, note
        renderer.addCategory(QgsRendererCategory("", symbol, label))
        return renderer, note
    rules = QgsRuleBasedRenderer.convertFromRenderer(renderer)
    subject = '"{}"'.format(attribute.replace('"', '""')) if layer.fields().indexOf(attribute) >= 0 else attribute
    rules.rootRule().appendChild(QgsRuleBasedRenderer.Rule(symbol, 0, 0, f"({subject}) IS NULL", label))
    note["drawn_as"] = ("rule-based: one rule per class and one for the features with no value, which a "
                        "graduated renderer does not draw")
    return rules, note


def _proportional_request(layer, args: dict) -> dict:

    from qgis.core import QgsWkbTypes

    field = str(args.get("size_field"))
    index = layer.fields().indexOf(field)
    if index < 0:
        return _field_not_found_error(layer, field)
    if not layer.fields().at(index).isNumeric():
        return {"_error": f"size_field {field!r} is {layer.fields().at(index).typeName()}, not a number.",
                "code": "INVALID_ARGS",
                "suggestion": "A numeric field, or a numeric copy of this one, is needed."}
    geometry = layer.geometryType()
    if geometry == enum_member(QgsWkbTypes, "GeometryType", "PolygonGeometry"):
        return {"_error": (f"size_field sizes point markers or line widths, and {layer.name()!r} is a polygon "
                           "layer. Nothing was changed."), "code": "INVALID_ARGS",
                "suggestion": ("native:centroids (run_processing) makes points to size for proportional "
                               "symbols; a graduated style also works.")}
    markers = geometry == enum_member(QgsWkbTypes, "GeometryType", "PointGeometry")
    try:
        low, high = float(layer.minimumValue(index)), float(layer.maximumValue(index))
    except (TypeError, ValueError):
        return {"_error": f"size_field {field!r} has no values to size by.", "code": "INVALID_ARGS"}
    default_min, default_max = (2.0, 10.0) if markers else (0.3, 3.0)
    try:
        min_size = float(args.get("min_size") if args.get("min_size") is not None else default_min)
        max_size = float(args.get("max_size") if args.get("max_size") is not None else default_max)
    except (TypeError, ValueError):
        min_size, max_size = default_min, default_max
    if not max_size > min_size:
        min_size, max_size = default_min, default_max
    return {"field": field, "low": low, "high": high if high > low else low + 1.0,
            "min_size": min_size, "max_size": max_size, "markers": markers}





_VECTOR_ONLY_ARGS = ("field", "fill", "stroke_color", "stroke_width", "stroke_width_unit", "label_field", "size",
                     "size_expression", "size_field",
                     "cluster_distance", "min_size", "max_size", "label_color", "null_class_color")


def _wider_than_the_selection(layer) -> dict:










    try:
        selected = int(layer.selectedFeatureCount())
    except Exception:  # noqa: BLE001
        return {}
    if selected <= 0:
        return {}



    total = layer_order.feature_count_of(layer)
    if total is None or total <= selected:
        return {}
    return {
        "affects": f"how all {total:,} features of {layer.name()!r} draw, not only the {selected} selected",
        "highlight_instead": ("a style is permanent and covers the whole layer; to point at particular "
                              "features without touching symbology, call flash_features with their fids "
                              "or an expression: it blinks them three times over half a second and "
                              "changes nothing"),
    }


def _style_raster_layer(layer, target: str, args: dict) -> dict:

    from qgis.core import QgsRasterLayer

    from .elevation_style import apply_ramp_style

    if not isinstance(layer, QgsRasterLayer):
        kind = type(layer).__name__.replace("Qgs", "").replace("Layer", "").lower() or "unknown"
        return {"_error": f"Layer {target!r} is a {kind} layer, and set_layer_style paints vectors and rasters.",
                "code": "INVALID_ARGS",
                "suggestion": "set_layer_property changes its opacity; apply_style_qml applies a "
                              "style written for this layer kind."}

    bands = layer.bandCount()
    opacity = args.get("opacity")
    if bands != 1:


        return {"_error": f"Layer {target!r} has {bands} bands, so it is drawn as a colour image and has no "
                          "single value to classify.",
                "code": "INVALID_ARGS",
                "suggestion": "Colour ramps need one band. set_layer_property changes its opacity; "
                              "run_processing native:rastercalc picks one band."}

    color_ramp = str(args.get("color_ramp") or "").strip()
    band = int(args.get("band") or 1)
    if band > layer.bandCount():
        band = 0
    codes, low, high, stretch = [], None, None, {}
    if band and not _requested_classes(args):



        from .elevation_style import band_range
        from .style_defaults import long_tail_cut, raster_codes

        codes = raster_codes(layer, band)
        if not codes:
            found = band_range(layer, band)
            stretch = long_tail_cut(layer, band, *found) if found else {}
            if stretch:
                low, high = stretch["min"], stretch["max"]
    kept_style = _style_to_put_back(layer)
    if codes:
        from qgis.core import QgsStyle

        from .style_defaults import paletted_renderer

        ramp = QgsStyle.defaultStyle().colorRamp(color_ramp) if color_ramp else None
        renderer = paletted_renderer(layer, band, codes, ramp)
        current = layer.renderer()
        if current is not None:
            renderer.setOpacity(current.opacity())
        layer.setRenderer(renderer)
        shown = ", ".join(str(c) for c in codes[:8]) + (", ..." if len(codes) > 8 else "")
        outcome = {"band": band, "renderer": "paletted", "classes": len(codes), "codes": codes[:30],
                   "style_type_reason": (f"band {band} holds {len(codes)} whole-number class codes ({shown}), so "
                                         "each code got its own colour instead of a ramp through them")}
    else:
        outcome = apply_ramp_style(
            layer, band=band or int(args.get("band") or 1), ramp_name=color_ramp,
            classes=_requested_classes(args), unit=str(args.get("units") or ""), low=low, high=high)
        if outcome.get("_error"):
            return outcome
        if stretch:
            outcome["stretch_reason"] = stretch["reason"]

    applied_opacity = _apply_layer_opacity(layer, opacity)
    layer.triggerRepaint()
    if iface is not None and iface.layerTreeView() is not None:
        iface.layerTreeView().refreshLayerSymbology(layer.id())
    result = {"styled": layer.name(), "type": "raster_paletted" if codes else "raster_ramp", **outcome}
    result.update(_previous_style_keys(layer, kept_style))
    if color_ramp:
        result["color_ramp"] = color_ramp
    elif args.get("color"):
        result["color_note"] = ("One colour paints every pixel the same, so the classes use the default "
                                "elevation tints. color_ramp names a QGIS ramp: Terrain, Viridis, "
                                "Spectral, Blues.")
    if applied_opacity is not None:
        result["opacity"] = applied_opacity
    ignored = [key for key in _VECTOR_ONLY_ARGS if args.get(key) not in (None, "")]
    if ignored:
        result["not_applied"] = ignored
        result["not_applied_note"] = ("a raster has no symbols, so these changed nothing; a raster takes "
                                      "color_ramp, classes, band, units and opacity")
    mode = str(args.get("classification_mode") or "").strip()
    if mode and mode != "equal_interval" and not codes:
        result["classification_mode"] = "equal_interval"
        result["classification_note"] = (f"{mode} classes need a histogram of every pixel; the classes here "
                                         "are equal intervals over the band's range.")
    return result


def _apply_layer_opacity(layer, opacity):

    if opacity is None:
        return None
    try:
        value = float(max(0.0, min(1.0, float(opacity))))
    except (TypeError, ValueError):
        return None
    layer.setOpacity(value)
    return value


def _requested_classes(args: dict) -> int:







    for key in ("max_classes", "classes"):
        value = args.get(key)
        if value in (None, ""):
            continue
        try:
            asked = int(value)
        except (TypeError, ValueError):
            continue
        if asked > 0:
            return min(asked, _MAX_CATEGORIES)
    return 0


def _value_frequencies(layer, index: int, scan_cap: int) -> tuple[dict, int]:








    from qgis.core import QgsFeatureRequest

    request = QgsFeatureRequest().setSubsetOfAttributes([index])
    flag = enum_member(QgsFeatureRequest, "Flag", "NoGeometry", None)
    if flag is not None:
        request.setFlags(flag)
    counts: dict = {}
    scanned = 0
    features = layer.getFeatures(request)
    try:
        for feature in features:
            key = str(feature[index])
            counts[key] = counts.get(key, 0) + 1
            scanned += 1
            if scanned >= scan_cap:
                break
    finally:
        with contextlib.suppress(Exception):
            features.close()
    return counts, scanned


def _colour_name(value, memo: dict) -> str | None:








    if value is None:
        return None
    text = str(value).strip()
    if text in memo:
        return memo[text]
    from qgis.PyQt.QtGui import QColor

    colour = QColor(text) if text and text.upper() != "NULL" else QColor()
    if not colour.isValid() and text and text.upper() != "NULL":
        parts = text.replace(",", " ").replace(";", " ").split()
        if len(parts) in (3, 4) and all(p.isdigit() and int(p) <= 255 for p in parts):
            colour = QColor(*(int(p) for p in parts))
        elif len(text) in (6, 8) and all(c in "0123456789abcdefABCDEF" for c in text):
            colour = QColor("#" + text)
    name = None
    if colour.isValid():
        name = colour.name() if colour.alpha() == 255 else colour.name(enum_member(QColor, "NameFormat", "HexArgb"))
    memo[text] = name
    return name


def _relabels_only(layer, args: dict) -> bool:


    if not str(args.get("label_field") or "").strip():
        return False
    if args.get("color_ramp") or args.get("color_field") or args.get("keep_colors") is True:
        return False
    current = layer.renderer()
    if current is None or current.type() != "categorizedSymbol":
        return False
    field = str(args.get("field") or "").strip()
    if not field:
        args["field"] = current.classAttribute()
        return True
    return field == current.classAttribute()


def _labels_from_field(layer, renderer, label_field: str) -> dict:


    from qgis.core import QgsFeatureRequest

    fields = layer.fields()
    attribute = renderer.classAttribute() if hasattr(renderer, "classAttribute") else ""
    idx, lidx = fields.indexOf(attribute), fields.indexOf(label_field)
    if idx < 0 or lidx < 0:
        return {"label_field_note": (f"the classes read {attribute!r}, an expression rather than a field, so "
                                     "their labels were left as they were")}
    request = QgsFeatureRequest()
    request.setSubsetOfAttributes([idx, lidx])
    no_geometry = enum_member(QgsFeatureRequest, "Flag", "NoGeometry", None)
    if no_geometry is not None:
        request.setFlags(no_geometry)
    cap = limits.current("MAX_FEATURES_MATERIALISED")
    votes: dict = {}
    scanned = 0
    for feature in layer.getFeatures(request):
        scanned += 1
        label = feature[lidx]
        if label is not None and str(label) != "NULL" and str(label).strip():
            bucket = votes.setdefault(str(feature[idx]), {})
            text = str(label).strip()
            bucket[text] = bucket.get(text, 0) + 1
        if scanned >= cap:
            break
    relabelled, several = 0, []
    for index, category in enumerate(renderer.categories()):
        value = category.value()
        if isinstance(value, list) or value in (None, ""):
            continue
        bucket = votes.get(str(value))
        if not bucket:
            continue
        ranked = sorted(bucket, key=lambda text: (-bucket[text], text))
        renderer.updateCategoryLabel(index, ranked[0])
        relabelled += 1
        if len(ranked) > 1:
            several.append({"value": str(value), "labels": ranked[:4]})
    out = {"labels_from": label_field, "classes_relabelled": relabelled}
    if several:
        out["classes_with_several_labels"] = len(several)
        out["several_labels_examples"] = several[:5]
        out["several_labels_note"] = "these classes held several labels; each took the one most of its features have"
    total = layer_order.feature_count_of(layer)
    if scanned >= cap and (total is None or scanned < total):
        out["label_warning"] = f"labels read on the first {scanned} features"
    return out


def _coloured_categories(layer, args: dict) -> dict:











    from qgis.core import (
        QgsCategorizedSymbolRenderer,
        QgsExpressionContext,
        QgsExpressionContextUtils,
        QgsFeatureRequest,
        QgsProperty,
        QgsRenderContext,
        QgsRendererCategory,
        QgsSingleSymbolRenderer,
        QgsSymbol,
        QgsWkbTypes,
    )
    from qgis.PyQt.QtGui import QColor

    field = str(args.get("field") or "").strip()
    if not field:
        return {"_error": "Field is required for categorized style", "code": "INVALID_ARGS",
                "suggestion": "field names the values that become the classes."}
    fields = layer.fields()
    idx = fields.indexOf(field)
    if idx < 0:
        return _field_not_found_error(layer, field)
    color_field = str(args.get("color_field") or "").strip()
    if color_field and args.get("keep_colors") is True:
        return {"_error": "color_field and keep_colors are two colour sources; pass one.", "code": "INVALID_ARGS",
                "suggestion": ("color_field when a field holds each feature's colour, keep_colors to keep the "
                               "colours the layer shows now.")}
    if layer_order.is_remote_vector(layer):
        return {"_error": (f"{layer.name()!r} is a web service layer: reading each feature's colour holds QGIS "
                           "while the service sends every row. Nothing was changed."),
                "code": "INVALID_ARGS",
                "suggestion": "A GeoPackage copy from export_layer styles fast; color_ramp needs no read."}

    request = QgsFeatureRequest()
    no_geometry = enum_member(QgsFeatureRequest, "Flag", "NoGeometry", None)
    memo: dict = {}
    symbols: dict = {}
    current = context = None
    source = ""
    if color_field:
        cidx = fields.indexOf(color_field)
        if cidx < 0:
            return _field_not_found_error(layer, color_field)
        request.setSubsetOfAttributes([idx, cidx])
        if no_geometry is not None:
            request.setFlags(no_geometry)
        source = f"field {color_field}"

        def colour_of(feature):
            return _colour_name(feature[cidx], memo)
    else:
        if layer.renderer() is None:
            return {"_error": f"{layer.name()!r} has no style to keep colours from.", "code": "INVALID_ARGS",
                    "suggestion": "color_field or color_ramp is needed."}
        current = layer.renderer().clone()
        context = QgsRenderContext()
        context.setExpressionContext(QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer)))
        current.startRender(context, fields)
        request.setSubsetOfAttributes(sorted(set(current.usedAttributes(context)) | {field}), fields)

        attribute = current.classAttribute() if hasattr(current, "classAttribute") else ""
        if attribute and fields.indexOf(attribute) >= 0 and no_geometry is not None:
            request.setFlags(no_geometry)
        source = f"the layer's own style ({current.type()}{' on ' + attribute if attribute else ''})"

        def colour_of(feature):
            context.expressionContext().setFeature(feature)
            symbol = current.originalSymbolForFeature(feature, context)
            if symbol is None:
                return None
            name = symbol.color().name()
            if name not in symbols:
                symbols[name] = symbol.clone()
            return name

    started = time.monotonic()
    scan_cap = limits.current("MAX_FEATURES_MATERIALISED")
    votes: dict = {}
    values: dict = {}
    blank: dict = {}
    scanned = no_value = unreadable = 0
    features = layer.getFeatures(request)
    try:
        for feature in features:
            scanned += 1
            value = feature[idx]
            colour = colour_of(feature)
            if value is None or str(value) == "NULL":
                no_value += 1
                bucket = blank
            else:
                key = str(value)
                bucket = votes.get(key)
                if bucket is None:
                    bucket = votes[key] = {}
                    values[key] = value
            if colour is None:
                unreadable += 1
            else:
                bucket[colour] = bucket.get(colour, 0) + 1
            if scanned >= scan_cap:
                break
    finally:
        if current is not None:
            with contextlib.suppress(Exception):
                current.stopRender(context)
        with contextlib.suppress(Exception):
            features.close()

    total = layer_order.feature_count_of(layer)
    partial = scanned >= scan_cap and (total is None or scanned < total)
    n = len(values)
    if not n:
        return {"_error": f"Field {field!r} has no values to classify.", "code": "INVALID_ARGS",
                "suggestion": "A field with a value on at least one feature is needed."}
    geometry = layer.geometryType()
    line = geometry == enum_member(QgsWkbTypes, "GeometryType", "LineGeometry")
    note: dict = {"color_source": source, "features_read": scanned}


    base = QgsSymbol.defaultSymbol(geometry)
    with contextlib.suppress(Exception):
        drawn = layer.renderer().symbols(QgsRenderContext()) if layer.renderer() is not None else []
        if drawn and drawn[0] is not None and drawn[0].type() == base.type():
            base = drawn[0].clone()

    if n > _MAX_COLOURED_CATEGORIES:
        if not color_field:
            return {"_error": (f"{n} distinct values in {field}: more classes than one style builds "
                               f"({_MAX_COLOURED_CATEGORIES}). Nothing was changed."),
                    "code": "INVALID_ARGS",
                    "suggestion": ("Classify on a field with fewer values, or pass color_field naming a field "
                                   "that holds each feature's colour: past this count it is drawn per feature.")}

        direct = all(QColor(text).isValid() for text, name in memo.items() if name)
        quoted = '"' + color_field.replace('"', '""') + '"'
        expression = quoted if direct else f"regexp_replace(trim({quoted}), '[\\\\s,;]+', ',')"
        symbol = base.clone()
        member = _symbol_layer_property("StrokeColor" if line else "FillColor")
        for i in range(symbol.symbolLayerCount()):
            symbol.symbolLayer(i).setDataDefinedProperty(member, QgsProperty.fromExpression(expression))
        note.update({
            "renderer": QgsSingleSymbolRenderer(symbol),
            "drawn_as": (f"{n} distinct values in {field}, more than {_MAX_COLOURED_CATEGORIES} classes: one "
                         f"symbol whose colour reads {color_field} on every feature, so the map shows each "
                         "feature's own colour and the legend holds one entry"),
            "color_expression": expression,
            "seconds": round(time.monotonic() - started, 2),
        })
        return note

    ordered = list(values.values())
    with contextlib.suppress(TypeError):
        ordered = sorted(ordered, key=lambda v: (str(type(v)), v))
    categories, mixed = [], []
    no_colour = 0

    def symbol_for(bucket: dict):
        if not bucket:
            symbol = base.clone()
            symbol.setColor(QColor(_OTHER_COLOR))
            return symbol, False
        colour = max(sorted(bucket), key=bucket.get)
        if colour in symbols:
            return symbols[colour].clone(), True
        symbol = base.clone()
        symbol.setColor(QColor(colour))
        return symbol, True

    for value in ordered:
        bucket = votes[str(value)]
        symbol, coloured = symbol_for(bucket)
        no_colour += 0 if coloured else 1
        if len(bucket) > 1:
            mixed.append({"value": str(value), "colors": sorted(bucket, key=bucket.get, reverse=True)[:4]})
        categories.append(QgsRendererCategory(value, symbol, _category_label(value)))
    if no_value or partial:


        symbol, _coloured = symbol_for(blank)
        label = "(no value)" if not partial else ("(no value, or past the first "
                                                   f"{scanned} features read)")
        categories.append(QgsRendererCategory("", symbol, label))
    note["renderer"] = QgsCategorizedSymbolRenderer(field, categories)
    if mixed:
        note["classes_with_mixed_colors"] = len(mixed)
        note["mixed_color_examples"] = mixed[:5]
        note["mixed_note"] = ("these classes held features of several colours; each took the colour most of its "
                              "features had")
    if no_colour:
        note["classes_without_color"] = no_colour
        note["classes_without_color_note"] = (f"no feature of these classes had a readable colour: drawn grey "
                                              f"{_OTHER_COLOR}")
    if unreadable:
        note["features_without_color"] = unreadable
    if no_value:
        note["features_without_value"] = no_value
    if partial:
        note["warning"] = (f"colours read on the first {scanned} of {total if total is not None else 'all'} "
                           "features; values past them fall in the last class")
    note["seconds"] = round(time.monotonic() - started, 2)
    return note


def _readable_range_labels(renderer, unit=None) -> None:







    try:
        ranges = list(renderer.ranges())
        if not ranges:
            return
        span = abs(float(ranges[-1].upperValue()) - float(ranges[0].lowerValue()))
        digits = 0 if span >= 50 else (1 if span >= 5 else (2 if span >= 0.5 else 4))
        tail = f" {unit}" if unit else ""
        for index, item in enumerate(ranges):
            low, high = float(item.lowerValue()), float(item.upperValue())
            renderer.updateRangeLabel(index, f"{low:,.{digits}f} - {high:,.{digits}f}{tail}")
    except Exception as exc:  # noqa: BLE001
        log_warning(f"_readable_range_labels: relabeling failed: {exc}")


def _category_label(value) -> str:

    try:
        if value is None:
            return "(no value)"
        text = str(value).strip()
    except Exception:  # noqa: BLE001
        return "(no value)"
    return text if text and text.upper() != "NULL" else "(no value)"


def _geometry_word(layer) -> str:

    from qgis.core import QgsWkbTypes

    for word, member in (("point", "PointGeometry"), ("line", "LineGeometry"), ("polygon", "PolygonGeometry")):
        if layer.geometryType() == enum_member(QgsWkbTypes, "GeometryType", member, None):
            return word
    return ""


def _style_for_geometry(layer, style_type: str, args: dict) -> tuple:






    if style_type != "cluster":
        return style_type, ""
    word = _geometry_word(layer)
    if word in ("", "point"):
        return style_type, ""
    field = args.get("field")
    chosen = "categorized" if field and layer.fields().indexOf(str(field)) >= 0 else "single"
    return chosen, (f"Clusters group points and {layer.name()!r} is a {word} layer, so it was styled "
                    f"{chosen} instead. Nothing else about the request changed.")


def _symbol_layer_property(member: str):

    from qgis.core import QgsSymbolLayer

    scoped = getattr(QgsSymbolLayer, "Property", None)
    found = getattr(scoped, member, None) if scoped is not None else None
    if found is not None:
        return found
    return getattr(QgsSymbolLayer, f"Property{member}")


def _cluster_renderer(layer, args: dict):












    from qgis.core import (
        QgsFontMarkerSymbolLayer,
        QgsMarkerSymbol,
        QgsPointClusterRenderer,
        QgsProperty,
        QgsSimpleMarkerSymbolLayer,
        QgsSingleSymbolRenderer,
        QgsSymbol,
        QgsWkbTypes,
    )
    from qgis.PyQt.QtGui import QColor, QFont





    if layer.geometryType() != enum_member(QgsWkbTypes, "GeometryType", "PointGeometry"):
        return {"_error": f"Layer {layer.name()!r} is not a point layer.",
                "suggestion": "Clusters group points. Single, categorized or graduated fit here."}

    color = QColor(str(args.get("color") or "#2b83ba"))
    label_color = QColor(str(args.get("label_color") or "#ffffff"))
    stroke = QColor(str(args.get("stroke_color") or "#ffffff"))
    try:
        distance = float(args.get("cluster_distance", 12) or 12)
    except (TypeError, ValueError):
        distance = 12.0
    distance = max(1.0, min(60.0, distance))
    try:
        min_size = float(args.get("min_size", 8) or 8)
        max_size = float(args.get("max_size", 26) or 26)
    except (TypeError, ValueError):
        min_size, max_size = 8.0, 26.0
    if max_size < min_size:
        min_size, max_size = max_size, min_size



    disc = QgsSimpleMarkerSymbolLayer()
    disc.setColor(color)
    disc.setStrokeColor(stroke)
    disc.setStrokeWidth(0.4)
    disc.setDataDefinedProperty(
        _symbol_layer_property("Size"),
        QgsProperty.fromExpression(
            f"coalesce(scale_linear(@cluster_size, 1, 250, {min_size}, {max_size}), {min_size})"
        ),
    )



    count = QgsFontMarkerSymbolLayer()
    count.setColor(label_color)
    count.setSize(max(6.0, min_size * 0.55))
    count.setFontFamily(QFont().family())
    count.setDataDefinedProperty(
        _symbol_layer_property("Character"), QgsProperty.fromExpression("@cluster_size")
    )
    count.setDataDefinedProperty(
        _symbol_layer_property("Size"),
        QgsProperty.fromExpression(
            f"coalesce(scale_linear(@cluster_size, 1, 250, {min_size * 0.55}, {max_size * 0.42}), {min_size * 0.55})"
        ),
    )

    cluster_symbol = QgsMarkerSymbol()
    cluster_symbol.changeSymbolLayer(0, disc)
    cluster_symbol.appendSymbolLayer(count)

    renderer = QgsPointClusterRenderer()
    renderer.setClusterSymbol(cluster_symbol)
    renderer.setTolerance(distance)
    from qgis.core import QgsUnitTypes

    renderer.setToleranceUnit(enum_member(QgsUnitTypes, "RenderUnit", "RenderMillimeters"))



    single = QgsSymbol.defaultSymbol(layer.geometryType())
    single.setColor(color)
    if hasattr(single, "setSize"):
        single.setSize(max(1.6, min_size * 0.3))
    renderer.setEmbeddedRenderer(QgsSingleSymbolRenderer(single))
    return renderer


def _apply_symbol_tweaks(renderer, args: dict) -> set:








    from qgis.core import QgsLineSymbolLayer
    from qgis.PyQt.QtGui import QColor

    stroke_color = args.get("stroke_color")
    stroke_width = args.get("stroke_width")
    size = args.get("size")
    fill = args.get("fill")


    width_unit = _stroke_unit(args.get("stroke_width_unit"))
    applied: set = set()

    symbols = []
    if hasattr(renderer, "symbols"):
        try:
            from qgis.core import QgsRenderContext

            symbols = list(renderer.symbols(QgsRenderContext()))
        except Exception:
            symbols = []
    if not symbols and hasattr(renderer, "symbol"):
        symbols = [renderer.symbol()]

    for symbol in symbols:
        if symbol is None:
            continue
        if size is not None and hasattr(symbol, "setSize"):
            try:
                symbol.setSize(float(size))
                applied.add("size")
            except (TypeError, ValueError):
                pass
        for i in range(symbol.symbolLayerCount()):
            sl = symbol.symbolLayer(i)
            line_layer = isinstance(sl, QgsLineSymbolLayer)
            if fill in ("none", "solid") and hasattr(sl, "setBrushStyle"):
                from qgis.PyQt.QtCore import Qt as _Qt

                sl.setBrushStyle(_Qt.BrushStyle.NoBrush if fill == "none" else _Qt.BrushStyle.SolidPattern)
                applied.add("fill")
            if stroke_color is not None:
                if line_layer:
                    sl.setColor(QColor(stroke_color))
                    applied.add("stroke_color")
                elif hasattr(sl, "setStrokeColor"):
                    sl.setStrokeColor(QColor(stroke_color))
                    applied.add("stroke_color")
            if stroke_width is not None:
                try:
                    if line_layer:
                        sl.setWidth(float(stroke_width))
                        applied.add("stroke_width")
                    elif hasattr(sl, "setStrokeWidth"):
                        sl.setStrokeWidth(float(stroke_width))
                        applied.add("stroke_width")
                except (TypeError, ValueError):
                    pass
            if width_unit is not None:
                if line_layer and hasattr(sl, "setWidthUnit"):
                    sl.setWidthUnit(width_unit)
                    applied.add("stroke_width_unit")
                elif hasattr(sl, "setStrokeWidthUnit"):
                    sl.setStrokeWidthUnit(width_unit)
                    applied.add("stroke_width_unit")
    return applied


def _stroke_unit(unit):

    from qgis.core import Qgis, QgsUnitTypes

    from ._compat import enum_value

    if unit == "map_units":
        return enum_value((Qgis, "RenderUnit.MapUnits"), (QgsUnitTypes, "RenderMapUnits"))
    if unit == "mm":
        return enum_value((Qgis, "RenderUnit.Millimeters"), (QgsUnitTypes, "RenderMillimeters"))
    return None











_FLASH_DEFAULT_FLASHES = 3
_FLASH_DEFAULT_DURATION_MS = 500
_FLASH_MAX_FLASHES = 10
_FLASH_MAX_DURATION_MS = 5000


_FLASH_MAX_FEATURES = 200


def _flash_ids(layer, args: dict) -> tuple:






    from qgis.core import QgsFeatureRequest

    raw = args.get("fids")
    expression = str(args.get("expression") or "").strip()
    if raw is None and not expression:
        return [], {"_error": "flash_features needs 'fids' or 'expression'.",
                    "_code": "INVALID_ARGS",
                    "_suggestion": "fids: [554], or expression: \"name = 'Ilha'\". "
                                   "get_features returns the fid of every row it prints."}
    if raw is not None and expression:
        return [], {"_error": "'fids' and 'expression' cannot both be set.",
                    "_code": "INVALID_ARGS",
                    "_suggestion": "'fids' fits known ids; 'expression' otherwise."}

    if raw is not None:
        wanted = list(raw) if isinstance(raw, (list, tuple, set)) else [raw]
        try:
            wanted = [int(value) for value in wanted]
        except (TypeError, ValueError):
            return [], {"_error": "'fids' must be whole feature ids.",
                        "_code": "INVALID_ARGS",
                        "_suggestion": "get_features prints the fid values as numbers."}
        if not wanted:
            return [], {"_error": "'fids' is empty.", "_code": "INVALID_ARGS",
                        "_suggestion": "It needs a feature id or an expression."}



        wanted = list(dict.fromkeys(wanted))
        request = QgsFeatureRequest().setFilterFids(wanted)
        request.setNoAttributes()
    else:
        wanted = None
        invalid = _expression_error(layer, expression, "Expression")
        if invalid:
            return [], invalid
        request = QgsFeatureRequest().setFilterExpression(expression)
    request.setLimit(_FLASH_MAX_FEATURES + 1)

    found: list = []
    try:
        for feature in layer.getFeatures(request):
            found.append(int(feature.id()))
    except Exception as e:  # noqa: BLE001
        return [], {"_error": f"Reading the features to flash failed: {e}"}
    if len(found) > _FLASH_MAX_FEATURES:
        return [], {"_error": f"More than {_FLASH_MAX_FEATURES} features match: a flash that covers the map "
                              "points at nothing.",
                    "_code": "INVALID_ARGS",
                    "_suggestion": "A narrower expression fits; set_layer_style shows the whole group."}
    if not found:
        if wanted is not None:
            return [], {"_error": f"No feature of {layer.name()!r} carries any of these ids: "
                                  f"{', '.join(str(v) for v in wanted[:20])}.",
                        "_code": "INVALID_ARGS",
                        "_suggestion": "get_features on this layer gives the fid it prints."}
        return [], {"_error": f"No feature of {layer.name()!r} matches {expression!r}.",
                    "_code": "INVALID_ARGS",
                    "_suggestion": "get_features with the same expression shows what it selects."}
    return found, None


def _flash_features(args: dict) -> dict:









    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])
    if not isinstance(layer, QgsVectorLayer):
        return {"_error": f"Layer {layer.name()!r} is not a vector layer, and only features can be flashed.",
                "_code": "INVALID_ARGS",
                "_suggestion": "A vector layer is needed; zoom to part of a raster instead."}

    canvas = iface.mapCanvas() if iface is not None else None
    if canvas is None or not hasattr(canvas, "flashFeatureIds"):
        return {"_error": "This QGIS has no map canvas feature flash.",
                "_code": "EXECUTION_FAILED",
                "_suggestion": "select_features marks them; zoom_to_selected zooms in."}

    ids, error = _flash_ids(layer, args)
    if error:
        return error

    flashes = max(1, min(int(args.get("flashes") or _FLASH_DEFAULT_FLASHES), _FLASH_MAX_FLASHES))
    duration = max(50, min(int(args.get("duration") or _FLASH_DEFAULT_DURATION_MS), _FLASH_MAX_DURATION_MS))

    from qgis.PyQt.QtGui import QColor

    try:


        canvas.flashFeatureIds(layer, ids, QColor(255, 0, 0, 255), QColor(255, 0, 0, 0), flashes, duration)
    except Exception as e:  # noqa: BLE001
        return {"_error": f"Flashing the features failed: {e}"}

    return {
        "layer": layer.name(),
        "flashed": len(ids),
        "feature_ids": ids[:50],
        "flashes": flashes,
        "duration_ms": duration,
        "changed_nothing": True,
        "note": (f"{len(ids)} feature(s) of {layer.name()!r} blink {flashes} times over {duration} ms. "
                 "The layer's symbology, its selection and its data are untouched and nothing stays on "
                 "screen, so this is what points at a feature temporarily; a style call is permanent and "
                 "covers the whole layer."),
    }


def _expression_error(layer, text: str, argument: str = "Label expression") -> dict | None:





    from qgis.core import QgsExpression, QgsExpressionContext, QgsExpressionContextUtils

    expression = QgsExpression(text)
    context = QgsExpressionContext()
    context.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    expression.prepare(context)
    if expression.hasParserError():


        return {"_error": f"{argument} parse error: {expression.parserErrorString().strip()}",
                "_code": "EXPRESSION_INVALID",
                "suggestion": "validate_expression shows the error; a field name also works.",
                "fields": [f.name() for f in layer.fields()]}
    names = {f.name() for f in layer.fields()}
    unknown = sorted(str(column) for column in expression.referencedColumns() if str(column) not in names)
    if unknown:
        return _field_not_found_error(layer, unknown[0])
    return None


def _label_expression_error(layer, field: str) -> dict | None:
    return _expression_error(layer, field, "Label expression")


def _color_error(value, argument: str) -> dict | None:






    from qgis.PyQt.QtGui import QColor

    if value in (None, ""):
        return None
    text = str(value)
    valid = getattr(QColor, "isValidColorName", None) or getattr(QColor, "isValidColor", None)
    if valid is not None and valid(text):
        return None
    if QColor(text).isValid():
        return None
    return {"_error": f"{argument} {text!r} is not a colour Qt understands.",
            "code": "INVALID_ARGS",
            "suggestion": ("A hex colour ('#2b83ba') or an SVG name ('steelblue', 'darkgreen') is a "
                           "colour; a phrase is not.")}






_COLOUR_ARGS = (("color", "color"), ("stroke_color", "stroke_color"),
                ("label_color", "label_color"), ("null_class_color", "null_class_color"))


def _style_args_error(layer, args: dict) -> dict | None:
    for key, label in _COLOUR_ARGS:
        bad = _color_error(args.get(key), label)
        if bad:
            return bad
    label_field = str(args.get("label_field") or "").strip()
    if label_field:
        if str(args.get("style_type") or "").strip() != "categorized":
            return {"_error": "label_field names the legend labels of a categorized style.", "code": "INVALID_ARGS",
                    "suggestion": "style_type categorized is needed, or no label_field."}
        if layer.fields().indexOf(label_field) < 0:
            return _field_not_found_error(layer, label_field)
        if layer_order.is_remote_vector(layer):
            return {"_error": (f"{layer.name()!r} is a web service layer: reading each feature's label holds QGIS "
                               "while the service sends every row. Nothing was changed."), "code": "INVALID_ARGS",
                    "suggestion": "export_layer copies it to a GeoPackage to style there."}
    expression = args.get("size_expression")
    if expression:
        bad = _expression_error(layer, str(expression), "size_expression")
        if bad:
            return bad
    value_expression = str(args.get("value_expression") or "").strip()
    if value_expression:
        if str(args.get("style_type") or "").strip() not in ("graduated", "categorized"):
            return {"_error": "value_expression is classified by a graduated or categorized style.",
                    "code": "INVALID_ARGS",
                    "suggestion": "style_type graduated (numbers) or categorized (classes) fits."}
        if args.get("color_field") or args.get("keep_colors") is True or label_field:
            return {"_error": ("value_expression takes its colours from color_ramp; color_field, keep_colors and "
                               "label_field read a field's own values."), "code": "INVALID_ARGS",
                    "suggestion": "field classifies the values without them."}
        bad = _expression_error(layer, value_expression, "value_expression")
        if bad:
            return bad
    return None


def _expression_values(layer, text: str, cap: int) -> tuple[list, int]:




    from qgis.core import QgsExpression, QgsExpressionContext, QgsExpressionContextUtils, QgsFeatureRequest

    expression = QgsExpression(text)
    context = QgsExpressionContext()
    context.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    expression.prepare(context)
    request = QgsFeatureRequest()
    request.setLimit(int(cap))
    columns = expression.referencedColumns()
    if QgsFeatureRequest.ALL_ATTRIBUTES not in columns:
        request.setSubsetOfAttributes(sorted(str(c) for c in columns), layer.fields())
    flag = enum_member(QgsFeatureRequest, "Flag", "NoGeometry", None)
    if flag is not None and not expression.needsGeometry():
        request.setFlags(flag)
    values, scanned = [], 0
    features = layer.getFeatures(request)
    try:
        for feature in features:
            scanned += 1
            context.setFeature(feature)
            value = expression.evaluate(context)
            if value is None or (hasattr(value, "isNull") and value.isNull()) or str(value) == "NULL":
                continue
            values.append(value)
    finally:
        with contextlib.suppress(Exception):
            features.close()
    return values, scanned


def _expression_choice(layer, text: str) -> dict:

    from .style_defaults import skewness

    numbers = []
    for value in _expression_values(layer, text, 20_000)[0]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number == number:
            numbers.append(number)
    g1 = skewness(numbers)
    if g1 is None:
        return {}
    if abs(g1) >= 1.0:
        return {"mode": "jenks", "skewness": round(g1, 2),
                "reason": (f"{text} is skewed (skewness {g1:.1f}), so natural breaks (Jenks) were used: equal "
                           "intervals would put almost every feature in one class")}
    return {"mode": "equal_interval", "skewness": round(g1, 2),
            "reason": (f"{text} is spread fairly evenly (skewness {g1:.1f}), so equal intervals keep classes of "
                       "the same width")}


def _set_layer_labels(args: dict) -> dict:
    from qgis.core import Qgis, QgsPalLayerSettings, QgsTextFormat, QgsVectorLayerSimpleLabeling, QgsWkbTypes
    from qgis.PyQt.QtGui import QColor, QFont

    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])
    if not isinstance(layer, QgsVectorLayer):
        return {"_error": f"Layer '{args['layer_name']}' is not a vector layer"}

    if args.get("segment_lengths"):
        return _label_polygon_edges(layer, args)

    enabled = args.get("enabled", True)
    if not enabled:
        kept_style = _style_to_put_back(layer)
        layer.setLabelsEnabled(False)
        layer.triggerRepaint()
        return {"labels": "disabled", "layer": args["layer_name"],
                **_previous_style_keys(layer, kept_style, "labels")}

    field = args.get("field")



    base = None
    labeling_now = layer.labeling()
    if not args.get("rules") and not args.get("reset") and labeling_now is not None \
            and labeling_now.type() == "simple":
        with contextlib.suppress(Exception):
            base = QgsPalLayerSettings(labeling_now.settings())
    if not field and not args.get("rules") and not args.get("expression") and base is None:
        return {"_error": "field or rules is required when labels are enabled.", "code": "INVALID_ARGS"}
    size = args.get("size", 10 if base is None else None)
    color = args.get("color", "#000000" if base is None else None)
    for key, label in (("color", "color"), ("buffer_color", "buffer_color")):
        bad = _color_error(args.get(key), label)
        if bad:
            return bad

    def make_settings(spec: dict, kept=None):


        fresh = kept is None
        rule_field = spec.get("field", field)
        rule_size = spec.get("size", size if fresh or "size" in args else None)
        rule_color = spec.get("color", color if fresh or "color" in args else None)
        settings = QgsPalLayerSettings() if fresh else kept
        numeric = False
        try:
            idx = layer.fields().indexOf(rule_field)
            numeric = idx >= 0 and layer.fields().at(idx).isNumeric()
        except Exception:  # nosec B110
            pass
        expression = spec.get("expression", args.get("expression"))
        if expression is None and args.get("format_numbers", True) and numeric:
            decimals = int(spec.get("decimals", args.get("decimals", 2)))
            expression = f'format_number("{rule_field}", {max(0, decimals)})'
            unit = spec.get("unit", args.get("unit", ""))
            if unit:
                expression += " || ' ' || " + repr(str(unit))
        label_value = expression or rule_field
        if label_value:
            settings.fieldName = label_value
            is_expression = layer.fields().indexOf(label_value) < 0
            if is_expression:
                error = _label_expression_error(layer, label_value)
                if error:
                    return error
            settings.isExpression = is_expression
        placement = str(spec.get("placement", args.get("placement", ""))).lower().replace("-", "_")
        geometry = layer.geometryType()
        if not placement and fresh and geometry == enum_member(QgsWkbTypes, "GeometryType", "LineGeometry"):
            placement = "line"
        if (not placement and fresh and geometry == enum_member(QgsWkbTypes, "GeometryType", "PointGeometry")
                and spec.get("offset", args.get("offset")) is None):




            placement = "cartographic"
            if spec.get("distance", args.get("distance")) is None:
                settings.dist = 1.0
        if placement:
            member = _LABEL_PLACEMENTS.get(placement)
            if member is None:
                return {"_error": f"placement {placement!r} is not one of {', '.join(_LABEL_PLACEMENTS)}.",
                        "code": "INVALID_ARGS"}
            value = enum_member(Qgis, "LabelPlacement", member, None)
            if value is None:
                value = enum_member(QgsPalLayerSettings, "Placement", member, None)
            if value is None:
                return {"_error": f"placement {placement!r} needs a newer QGIS than this one.",
                        "code": "INVALID_ARGS"}
            settings.placement = value
        placed = _label_placement_extras(layer, settings, spec, args, fresh)
        if isinstance(placed, dict):
            return placed
        ranked = _label_rank(settings, spec, args)
        if ranked:
            return ranked
        text_format = QgsTextFormat() if fresh else settings.format()
        font = QFont() if fresh else text_format.font()
        family = str(spec.get("font", args.get("font")) or "").strip()
        if family:
            font.setFamily(family)
        if rule_size is not None:
            font.setPointSizeF(float(rule_size))
        for key, setter in (("italic", font.setItalic), ("bold", font.setBold)):
            value = spec.get(key, args.get(key))
            if value or (value is not None and not fresh):
                setter(bool(value))
        spacing = spec.get("letter_spacing", args.get("letter_spacing"))
        if spacing is not None:
            try:
                font.setLetterSpacing(enum_member(QFont, "SpacingType", "AbsoluteSpacing"), float(spacing))
            except (TypeError, ValueError):
                return {"_error": "letter_spacing must be a number of points.", "code": "INVALID_ARGS"}
        text_format.setFont(font)
        if rule_size is not None:
            text_format.setSize(float(rule_size))
        if rule_color is not None:
            text_format.setColor(QColor(rule_color))
        styled = _label_text_look(text_format, spec, args)
        if styled:
            return styled
        buffer_size = spec.get("buffer_size", args.get("buffer_size"))
        buffer_color = spec.get("buffer_color", args.get("buffer_color"))
        halo = text_format.buffer()
        if buffer_size:
            halo.setEnabled(True)
            halo.setSize(float(buffer_size))
            halo.setColor(QColor(buffer_color or "#ffffff"))
        elif buffer_size is not None and not fresh:
            halo.setEnabled(False)
        elif buffer_color and not fresh:
            halo.setColor(QColor(buffer_color))
        text_format.setBuffer(halo)
        settings.setFormat(text_format)
        avoid_overlaps = spec.get("avoid_overlaps", args.get("avoid_overlaps", True if fresh else None))
        if avoid_overlaps is not None:
            settings.displayAll = not avoid_overlaps
        if avoid_overlaps:
            try:
                placement_settings = settings.placementSettings()
                placement_settings.setOverlapHandling(Qgis.LabelOverlapHandling.PreventOverlap)
                settings.setPlacementSettings(placement_settings)
            except Exception:  # nosec B110
                pass
        suppress_edge = spec.get("suppress_edge", args.get("suppress_edge", True if fresh else None))
        if suppress_edge is not None and (suppress_edge or not fresh):
            for attr in ("partialLabels", "partialsLabels"):
                if hasattr(settings, attr):
                    setattr(settings, attr, not suppress_edge)
        return settings

    rules = args.get("rules") or []
    if rules:
        from qgis.core import QgsRuleBasedLabeling
        root = QgsRuleBasedLabeling.Rule(None, 0, 0, "", "root")
        for index, spec in enumerate(rules):
            if not isinstance(spec, dict):
                return {"_error": f"rules[{index}] must be an object.", "code": "INVALID_ARGS"}
            settings = make_settings(spec)
            if isinstance(settings, dict):
                return settings
            expression = str(spec.get("filter") or spec.get("visibility") or "").strip()
            if expression:
                error = _expression_error(layer, expression, f"rules[{index}] filter")
                if error:
                    return error
            rule = QgsRuleBasedLabeling.Rule(
                settings, float(spec.get("max_scale", 0) or 0), float(spec.get("min_scale", 0) or 0),
                expression, str(spec.get("name") or f"Rule {index + 1}"))
            root.appendChild(rule)
    else:
        settings = make_settings({}, base)
        if isinstance(settings, dict):
            return settings




    min_scale = float(args.get("min_scale") or 0)
    max_scale = float(args.get("max_scale") or 0)
    if min_scale or max_scale:
        settings.scaleVisibility = True
        settings.minimumScale = min_scale
        settings.maximumScale = max_scale

    kept_style = _style_to_put_back(layer)
    if rules:
        labeling = QgsRuleBasedLabeling(root)
    else:
        labeling = QgsVectorLayerSimpleLabeling(settings)
    layer.setLabeling(labeling)
    layer.setLabelsEnabled(True)
    layer.triggerRepaint()
    out = {"labels": "enabled", "layer": args["layer_name"]}
    if base is not None:
        out["kept"] = ("the layer's own label settings (font, placement, callouts, data-defined positions) were "
                       "kept and only the arguments given changed; reset true starts a new setup")
    if field:
        out["field"] = field
    if rules:
        out["rules"] = len(rules)
    else:
        placement_name = getattr(settings.placement, "name", "")
        if placement_name:
            out["placement"] = placement_name
    extras = [key for key in ("line_position", "distance", "offset", "data_defined", "font", "italic", "bold",
                              "merge_lines", "priority", "z_index", "obstacle", "letter_spacing", "capitalization",
                              "opacity", "repeat_distance", "color", "size", "buffer_color", "buffer_size",
                              "avoid_overlaps") if args.get(key) not in (None, "", {}, [])]
    if extras:
        out["applied"] = extras
    out.update(_previous_style_keys(layer, kept_style, "labels"))
    if min_scale or max_scale:
        out["scale_visibility"] = {
            "min_scale": min_scale, "max_scale": max_scale,
            "note": ("labels draw between 1:max_scale (zoomed in) and "
                     "1:min_scale (zoomed out); 0 is no limit")}
    return out





_LABEL_PLACEMENTS = {"curved": "Curved", "curved_line": "Curved", "parallel": "Line", "line": "Line",
                     "around_point": "AroundPoint", "aroundpoint": "AroundPoint", "horizontal": "Horizontal",
                     "cartographic": "OrderedPositionsAroundPoint", "over_point": "OverPoint",
                     "perimeter": "Line", "perimeter_curved": "PerimeterCurved", "free": "Free"}
_CAPITALIZATION = {"upper": "AllUppercase", "lower": "AllLowercase", "title": "TitleCase",
                   "small_caps": "SmallCaps", "none": "MixedCase"}


def _label_rank(settings, spec: dict, args: dict) -> dict | None:







    priority = spec.get("priority", args.get("priority"))
    if priority is not None:
        try:
            settings.priority = max(0, min(10, int(priority)))
        except (TypeError, ValueError):
            return {"_error": "priority is a whole number from 0 (lowest) to 10.", "code": "INVALID_ARGS"}
    z_index = spec.get("z_index", args.get("z_index"))
    if z_index is not None:
        try:
            settings.zIndex = float(z_index)
        except (TypeError, ValueError):
            return {"_error": "z_index must be a number; higher draws on top.", "code": "INVALID_ARGS"}
    obstacle = spec.get("obstacle", args.get("obstacle"))
    if obstacle is not None:
        obstacles = settings.obstacleSettings()
        obstacles.setIsObstacle(bool(obstacle))
        settings.setObstacleSettings(obstacles)
    repeat = spec.get("repeat_distance", args.get("repeat_distance"))
    if repeat is not None:
        try:
            settings.repeatDistance = max(0.0, float(repeat))
        except (TypeError, ValueError):
            return {"_error": "repeat_distance must be a number of millimetres.", "code": "INVALID_ARGS"}
    return None


def _label_text_look(text_format, spec: dict, args: dict) -> dict | None:

    from qgis.core import Qgis

    case = str(spec.get("capitalization", args.get("capitalization")) or "").lower().replace("-", "_")
    if case:
        member = _CAPITALIZATION.get(case)
        if member is None:
            return {"_error": f"capitalization {case!r} is not one of {', '.join(_CAPITALIZATION)}.",
                    "code": "INVALID_ARGS"}
        value = enum_member(Qgis, "Capitalization", member, None)
        if value is None:
            from qgis.core import QgsStringUtils
            value = enum_member(QgsStringUtils, "Capitalization", member, None)
        if value is not None:
            text_format.setCapitalization(value)
    opacity = spec.get("opacity", args.get("opacity"))
    if opacity is not None:
        try:
            text_format.setOpacity(max(0.0, min(1.0, float(opacity))))
        except (TypeError, ValueError):
            return {"_error": "opacity is a number from 0 (invisible) to 1.", "code": "INVALID_ARGS"}
    return None





_LINE_POSITIONS = {"on": ("OnLine",), "above": ("AboveLine", "MapOrientation"),
                   "below": ("BelowLine", "MapOrientation"),
                   "above_below": ("AboveLine", "BelowLine", "MapOrientation"),
                   "on_above_below": ("OnLine", "AboveLine", "BelowLine", "MapOrientation")}


def _label_placement_extras(layer, settings, spec: dict, args: dict, fresh: bool = True):







    from qgis.core import Qgis, QgsPalLayerSettings, QgsProperty, QgsWkbTypes

    done = []
    position = str(spec.get("line_position", args.get("line_position")) or "").lower().replace("-", "_")
    if position:
        names = _LINE_POSITIONS.get(position)
        if names is None:
            return {"_error": f"line_position {position!r} is not one of {', '.join(_LINE_POSITIONS)}.",
                    "code": "INVALID_ARGS"}
        flags = None
        for name in names:
            flag = enum_member(Qgis, "LabelLinePlacementFlag", name, None)
            if flag is None:
                continue
            flags = flag if flags is None else flags | flag
        if flags is not None:
            line_settings = settings.lineSettings()
            line_settings.setPlacementFlags(flags)
            settings.setLineSettings(line_settings)
            done.append("line_position")
    merge = spec.get("merge_lines", args.get("merge_lines"))
    if merge is None and fresh and layer.geometryType() == enum_member(QgsWkbTypes, "GeometryType", "LineGeometry"):


        merge = True
    if merge is not None:
        line_settings = settings.lineSettings()
        line_settings.setMergeLines(bool(merge))
        settings.setLineSettings(line_settings)
    distance = spec.get("distance", args.get("distance"))
    if distance is not None:
        try:
            settings.dist = float(distance)
            done.append("distance")
        except (TypeError, ValueError):
            return {"_error": "distance must be a number of millimetres.", "code": "INVALID_ARGS"}
    offset = spec.get("offset", args.get("offset"))
    if offset is not None:
        try:
            dx, dy = (float(v) for v in offset)
        except (TypeError, ValueError):
            return {"_error": "offset must be two numbers, [x, y] in millimetres.", "code": "INVALID_ARGS"}
        settings.xOffset, settings.yOffset = dx, dy

        if settings.placement == enum_member(Qgis, "LabelPlacement", "AroundPoint"):
            settings.placement = enum_member(Qgis, "LabelPlacement", "OverPoint")
        done.append("offset")
    overrides = spec.get("data_defined", args.get("data_defined")) or {}
    if overrides:
        if not isinstance(overrides, dict):
            return {"_error": "data_defined must map label property names to expressions.", "code": "INVALID_ARGS"}
        known = {definition.name().casefold(): key
                 for key, definition in QgsPalLayerSettings.propertyDefinitions().items()}
        properties = settings.dataDefinedProperties()
        for name, value in overrides.items():
            key = known.get(str(name).casefold())
            if key is None:
                return {"_error": f"Unknown label property {name!r} in data_defined.", "code": "INVALID_ARGS",
                        "suggestion": "Label properties: PositionX, PositionY, OffsetXY, LabelRotation, Show, Size, "
                                      "Color, Bold, Italic, Family, BufferDraw, Hali, Vali, MinScale, MaxScale."}
            text = str(value)
            if layer.fields().indexOf(text) < 0:
                error = _expression_error(layer, text, f"data_defined {name}")
                if error:
                    return error
            properties.setProperty(key, QgsProperty.fromField(text) if layer.fields().indexOf(text) >= 0
                                   else QgsProperty.fromExpression(text))
        settings.setDataDefinedProperties(properties)
        done.append("data_defined")
    return done


def _label_polygon_edges(layer, args: dict) -> dict:






    from qgis.core import (
        QgsDistanceArea,
        QgsFeature,
        QgsFeatureRequest,
        QgsField,
        QgsGeometry,
        QgsProject,
        QgsVectorLayer,
    )
    unit = str(args.get("unit") or "m").lower()
    factors = {"m": 1.0, "meter": 1.0, "metre": 1.0, "km": 0.001,
               "ft": 3.280839895, "feet": 3.280839895}
    if unit not in factors:
        return {"_error": "unit must be m, km or ft.", "code": "INVALID_ARGS"}
    if layer.geometryType() != 2:
        return {"_error": "segment_lengths requires a polygon layer.", "code": "INVALID_ARGS"}
    old_id = str(layer.customProperty("terralab/segment_lengths_layer", ""))
    if old_id:
        old = QgsProject.instance().mapLayer(old_id)
        if old is not None:
            QgsProject.instance().removeMapLayer(old_id)
    name = str(args.get("output_name") or f"{layer.name()} boundary dimensions")
    out_layer = QgsVectorLayer(f"LineString?crs={layer.crs().authid()}", name, "memory")
    if not out_layer.isValid():
        return {"_error": "Could not create the boundary-dimension layer.", "code": "WRITE_FAILED"}
    out_layer.dataProvider().addAttributes([
        QgsField("source_fid", QVAR_LONGLONG), QgsField("edge_index", QVAR_INT),
        QgsField("length", QVAR_DOUBLE), QgsField("label", QVAR_STRING)])
    out_layer.updateFields()
    distance = QgsDistanceArea()
    distance.setSourceCrs(layer.crs(), QgsProject.instance().transformContext())
    distance.setEllipsoid(str(args.get("ellipsoid") or QgsProject.instance().ellipsoid() or "WGS84"))
    decimals = max(0, min(6, int(args.get("decimals", 2))))
    features = []


    geometry_only_request = QgsFeatureRequest().setSubsetOfAttributes([])
    out_fields = out_layer.fields()
    for source_feature in layer.getFeatures(geometry_only_request):
        polygon = (
            source_feature.geometry().asMultiPolygon()
            if source_feature.geometry().isMultipart()
            else [source_feature.geometry().asPolygon()]
        )
        for ring in polygon:
            for ring_points in ring:
                if len(ring_points) < 2:
                    continue
                for index, (start, end) in enumerate(zip(ring_points, ring_points[1:])):
                    length = distance.measureLine(start, end) * factors[unit]
                    feature = QgsFeature(out_fields)
                    feature.setGeometry(QgsGeometry.fromPolylineXY([start, end]))
                    feature.setAttributes([int(source_feature.id()), index, length,
                                           f"{length:,.{decimals}f} {unit}"])
                    features.append(feature)
    out_layer.dataProvider().addFeatures(features)
    QgsProject.instance().addMapLayer(out_layer)
    layer.setCustomProperty("terralab/segment_lengths_layer", out_layer.id())
    label_result = _set_layer_labels({"layer_name": out_layer.id(), "field": "label",
                                      "placement": "line", "format_numbers": False, "merge_lines": False,
                                      "suppress_edge": False, "size": args.get("size", 9),
                                      "color": args.get("color", "#202020"),
                                      "buffer_size": args.get("buffer_size", 1),
                                      "buffer_color": args.get("buffer_color", "#ffffff")})
    if isinstance(label_result, dict) and label_result.get("_error"):
        return label_result
    return {"layer": layer.name(), "boundary_layer": out_layer.name(),
            "boundary_layer_id": out_layer.id(), "segments": len(features),
            "unit": unit, "measurement_crs": layer.crs().authid(),
            "replaced_previous": bool(old_id), "labels": "enabled"}
