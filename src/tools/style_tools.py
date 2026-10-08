# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


from __future__ import annotations

import contextlib
import time

from qgis.core import QgsProject, QgsVectorLayer
from qgis.utils import iface

from ..core import background, layer_order, limits, net
from ..core.background import run_on_main_thread
from ..core.logger import log_warning
from ..core.qt_compat import enum_member
from ..core.tool_registry import coded_fact, tool_error
from . import style_3d, style_rows
from ._compat import QVAR_DOUBLE, QVAR_INT, QVAR_LONGLONG, QVAR_STRING
from .colour_text import qcolor_from_text
from .layer_lookup import _field_not_found_error, _find_layer, _layer_not_found_error



_MAX_CATEGORIES = 500




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
            "QGIS exposes no public API to attach a legend image to a map layer; no layer property was changed.",
            "next": coded_fact(hint="legend_image_unsupported")}


def _set_diagram_renderer(layer, args: dict, plan: dict, largest: float, kept_style: str = "") -> dict:
    from .diagram_style import set_diagram_renderer

    return set_diagram_renderer(layer, args, plan, largest, kept_style, _previous_style_keys)


def _legacy_names(args: dict) -> None:









    for theirs, ours in (("layer", "layer_name"), ("fill_color", "color"), ("symbol_size", "size")):
        if args.get(ours) in (None, "") and args.get(theirs) not in (None, ""):
            args[ours] = args[theirs]


def _style_reads_rows(args: dict) -> bool:







    args = args if isinstance(args, dict) else {}
    _legacy_names(args)
    if args.get("legend_image") is not None or not args.get("layer_name"):
        return False
    layer = _find_layer(args.get("layer_name"))
    if not isinstance(layer, QgsVectorLayer):
        return False
    style_type = str(args.get("style_type") or "").strip()
    if not (style_type in ("categorized", "graduated", "pie", "bar", "diagram")
            or (args.get("size_field") and style_type != "cluster")
            or _style_for_geometry(layer, style_type, args)[0] == "categorized"):
        return False
    if layer_order.is_remote_vector(layer):
        return True
    count = layer_order.feature_count_of(layer)
    return count is None or count > limits.current("SYNC_FEATURE_LOOP_MAX")


def _set_layer_style(args: dict) -> dict:


    result = _set_layer_style_2d(args)
    if not isinstance(result, dict) or "_error" in result:
        return result
    return run_on_main_thread(_with_3d_colours, args, result, timeout=60)


def _with_3d_colours(args: dict, result: dict) -> dict:
    layer = _find_layer(args.get("layer_name") or "")
    if isinstance(layer, QgsVectorLayer):
        painted = style_3d.colour_from_2d(layer)
        if painted:
            result["renderer_3d"] = painted
    return result


def _set_layer_style_2d(args: dict) -> dict:










    plan = run_on_main_thread(_style_plan, args, timeout=60)
    if "_rows" not in plan:
        return plan
    facts = style_rows.read(plan.pop("_rows"))
    if "_error" in facts:
        return facts
    facts.update(_style_decide(plan, facts))
    if net.is_cancelled():
        return tool_error("Stopped before the style was applied.", "CANCELLED",
                          "Nothing was changed: the layer keeps its style.")
    return run_on_main_thread(_style_apply, plan, facts, timeout=60)



_MODE_NAMES = {"equal_interval": "EqualInterval", "quantile": "Quantile", "jenks": "Jenks", "pretty": "Pretty"}


_JENKS_VALUES = 3000


def _jenks_sample(values: list) -> list:











    ordered = sorted(values)
    count, steps = len(ordered), _JENKS_VALUES - 2
    return [ordered[0], ordered[-1]] + [ordered[max(1, -(-step * (count - 2) // steps))] for step in range(steps)]


def _style_plan(args: dict) -> dict:



    from qgis.core import QgsWkbTypes

    _legacy_names(args)
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
        return tool_error(f"Layer {layer.name()!r} has no geometry, so there is nothing on the map to style.",
                          "INVALID_ARGS", hint="style_table_no_geometry", layer=layer.name())
    if (args.get("size_expression") and style_type != "cluster"
            and layer.geometryType() == enum_member(QgsWkbTypes, "GeometryType", "PolygonGeometry")):
        return tool_error(f"size_expression sizes point markers or line widths, and {layer.name()!r} "
                          "is a polygon layer. Nothing was changed.",
                          "INVALID_ARGS", hint="style_size_expression_polygon", layer=layer.name())
    proportional = None
    if args.get("size_field") and style_type != "cluster":
        proportional = _proportional_request(layer, args)
        if "_error" in proportional:
            return proportional






    style_type, coerced_note = _style_for_geometry(layer, style_type, args)







    ramp, color_ramp_name = None, ""
    if style_type == "graduated" or (style_type == "categorized"
                                     and (args.get("color_ramp") or args.get("color_stops"))):
        from .harvest_layers import _raster_ramp

        ramp, color_ramp_name, ramp_error = _raster_ramp(args)
        if ramp_error:
            return ramp_error
    invert_ramp = color_ramp_name.endswith(" reversed")
    if invert_ramp:
        color_ramp_name = color_ramp_name[:-len(" reversed")]

    plan = {"args": args, "layer_id": layer.id(), "style_type": style_type, "kind": style_type,
            "coerced_note": coerced_note, "ramp": ramp, "color_ramp_name": color_ramp_name,
            "invert_ramp": invert_ramp, "proportional": proportional}
    rows = style_rows.Read(layer)
    if proportional:
        rows.add("size_range", style_rows.MinMax(rows.field(proportional["index"])))
    if style_type in ("pie", "bar", "diagram"):

        from .diagram_style import SCAN, diagram_plan

        diagram = diagram_plan(layer, args)
        if "_error" in diagram:
            return diagram
        plan["diagram"] = diagram
        if diagram["scaled"]:
            rows.add("largest", style_rows.Largest(rows.expression(diagram["measure"]), SCAN))
    elif style_type == "categorized":
        refused = _plan_categorized(layer, args, plan, rows)
        if refused:
            return refused
    elif style_type == "graduated":
        refused = _plan_graduated(layer, args, plan, rows)
        if refused:
            return refused
    elif style_type not in ("single", "cluster"):
        return {"_error": f"Unknown style type: {style_type}"}
    if not rows:
        return _style_apply(plan, {})
    plan["started"] = time.monotonic()
    plan["_rows"] = rows.spec()
    return plan


def _plan_categorized(layer, args: dict, plan: dict, rows) -> dict | None:

    from qgis.core import QgsExpression

    from .style_defaults import touching_classes

    fields = layer.fields()
    cap = limits.current("MAX_FEATURES_MATERIALISED")
    plan.update(cap=cap, total=layer_order.feature_count_of(layer))
    if _relabels_only(layer, args):



        plan["kind"] = "relabel"
        attribute = layer.renderer().classAttribute()
    elif args.get("color_field") or args.get("keep_colors") is True:


        refused = _plan_coloured(layer, args, plan, rows)
        if refused:
            return refused
        attribute = plan["field"]
    else:

        expression = str(args.get("value_expression") or "").strip()
        field = expression or args.get("field")
        if not field:
            return {"_error": "Field (or value_expression) is required for categorized style"}
        idx = -1 if expression else fields.indexOf(field)
        if idx < 0 and not expression:
            return _field_not_found_error(layer, field)

        plan.update(kind="categorized", field=field, expression=expression,
                    numeric=not expression and fields.at(idx).isNumeric())
        if expression:


            rows.add("expression_values", style_rows.Values(rows.expression(expression), cap))
        else:


            key = rows.field(idx)
            rows.add("distinct", style_rows.Distinct(key, _MAX_CATEGORIES, cap))
            rows.add("frequencies", style_rows.Frequencies(key, cap))
        _plan_nulls(layer, field, plan, rows)
        attribute = field
    if _lists_no_value(args):
        _plan_nulls(layer, attribute, plan, rows, always=True)
    label_field = str(args.get("label_field") or "").strip()
    if label_field and fields.indexOf(attribute) >= 0 and fields.indexOf(label_field) >= 0:
        rows.add("labels", style_rows.LabelVotes(rows.field(fields.indexOf(attribute)),
                                                 rows.field(fields.indexOf(label_field)), cap))
    if touching_classes(layer, layer_order.is_remote_vector(layer)):

        rows.add("touching", style_rows.Touching(rows.expression(
            QgsExpression.quotedColumnRef(attribute) if fields.indexOf(attribute) >= 0 else attribute)))
        rows.needs(geometry=True)
    return None


def _plan_graduated(layer, args: dict, plan: dict, rows) -> dict | None:

    from qgis.core import QgsApplication, QgsExpression

    from .style_defaults import FIELD_SAMPLE, _number

    fields = layer.fields()

    expression = str(args.get("value_expression") or "").strip()
    field = expression or args.get("field")
    if not field:
        return {"_error": "Field (or value_expression) is required for graduated style"}

    idx = -1 if expression else fields.indexOf(field)
    if idx < 0 and not expression:
        return _field_not_found_error(layer, field)

    field_note: dict = {}



    if not expression and not fields.at(idx).isNumeric():
        type_name = fields.at(idx).typeName() or "text"
        field = 'to_real("{}")'.format(str(field).replace('"', '""'))
        field_note["field_note"] = (f"{args['field']} is {type_name}: its values were read as numbers with "
                                    "to_real, and a value that is not a number counts as no value")

    breaks = _class_limits(args.get("breaks"))
    if isinstance(breaks, dict):
        return breaks
    explicit = str(args.get("classification_mode") or "").strip().lower()

    auto = not args.get("classification_mode") and not breaks
    registry = QgsApplication.classificationMethodRegistry()
    plan.update(kind="graduated", field=field, expression=expression, field_note=field_note, breaks=breaks,
                auto=auto, classes=int(args.get("classes", 5) or 5), total=layer_order.feature_count_of(layer),
                field_name=fields.at(idx).name() if idx >= 0 else "",
                methods={name: registry.method(name) for name in _MODE_NAMES.values()})
    if auto or (explicit and not breaks):



        rows.add("sample", style_rows.Values(rows.expression(expression), FIELD_SAMPLE) if expression
                 else style_rows.Numbers(rows.field(idx), FIELD_SAMPLE, _number))
    if not breaks:



        at = fields.indexFromName(field)
        plan["by_expression"] = at < 0
        mode = None if auto else _MODE_NAMES.get(args.get("classification_mode"), "EqualInterval")
        values = at < 0 or mode is None or plan["methods"][mode].valuesRequired()
        if at >= 0:
            rows.add("doubles", style_rows.Doubles(rows.field(at), values, True, _null_reads_zero(fields.at(at))))
        else:


            parsed = QgsExpression(field)
            ref = fields.lookupField(next(iter(parsed.referencedColumns()), "")) if parsed.isField() else -1
            rows.add("doubles", style_rows.Doubles(rows.expression(field), True, False,
                                                   ref >= 0 and _null_reads_zero(fields.at(ref))))
    _plan_nulls(layer, field, plan, rows)
    return None


def _null_reads_zero(field) -> bool:

    return field.isNumeric() or style_rows.type_code(field.type()) == 1


def _plan_nulls(layer, attribute: str, plan: dict, rows, always: bool = False) -> None:




    if "nulls" in rows.jobs or not (always or plan["total"]):
        return
    index = layer.fields().indexOf(attribute)
    rows.add("nulls", style_rows.Nulls(rows.field(index), False) if index >= 0
             else style_rows.Nulls(rows.expression(f"({attribute}) IS NULL"), True))


def _lists_no_value(args: dict) -> bool:

    return any(isinstance(entry, dict) and _category_key(entry.get("value"), False) is None
               for entry in args.get("categories") or [])


def _style_decide(plan: dict, facts: dict) -> dict:



    if plan["kind"] == "categorized":
        return _decide_categories(plan, facts)
    if plan["kind"] == "graduated":
        return _decide_classes(plan, facts)
    return {}


def _decide_categories(plan: dict, facts: dict) -> dict:

    args, expression = plan["args"], plan["expression"]
    expression_counts: dict = {}
    if expression:
        values, expression_scanned = facts["expression_values"]["values"], facts["expression_values"]["scanned"]
        by_text: dict = {}
        for value in values:
            key = str(value)
            expression_counts[key] = expression_counts.get(key, 0) + 1
            by_text.setdefault(key, value)
        unique_values = list(by_text.values())
    else:
        unique_values = facts["distinct"]




    unique_values = [value for value in unique_values
                     if value is not None and not (hasattr(value, "isNull") and value.isNull())]
    n = len(unique_values)



    overflow = n > _MAX_CATEGORIES





    with contextlib.suppress(TypeError):
        unique_values = sorted(unique_values, key=lambda v: (str(type(v)), v))





    from .style_defaults import _rules

    max_classes = _requested_classes(args) or _rules("categories").get("readable") or _MAX_CATEGORIES
    folded, scanned = [], 0

    other_catch_all = False
    numeric = plan["numeric"]
    listed = {_category_key(entry.get("value"), numeric) for entry in args.get("categories") or []
              if isinstance(entry, dict)} - {None}
    if n > max_classes:
        if expression:
            counts, scanned = expression_counts, expression_scanned
        else:
            counts, scanned = facts["frequencies"]["counts"], facts["frequencies"]["scanned"]
        if overflow:
            n = len(counts)
            by_text = {str(v): v for v in unique_values}
            ranked = sorted((text for text in counts if text != "NULL"), key=lambda text: (-counts[text], text))
            unique_values = [by_text.get(text, text) for text in ranked[:max_classes]] + [
                by_text.get(text, text) for text in ranked[max_classes:]
                if _category_key(by_text.get(text, text), numeric) in listed]
            folded = None
            other_catch_all = True
        else:
            ranked = sorted(unique_values, key=lambda v: (-counts.get(str(v), 0), str(v)))
            kept = {str(v) for v in ranked[:max_classes]} | {
                str(v) for v in unique_values if _category_key(v, numeric) in listed}
            folded = [v for v in unique_values if str(v) not in kept]
            unique_values = [v for v in unique_values if str(v) in kept]
    return {"categories": {"values": unique_values, "folded": folded, "n": n, "scanned": scanned,
                           "other_catch_all": other_catch_all}}


def _decide_classes(plan: dict, facts: dict) -> dict:


    from .style_defaults import _number, class_counts, graduated_choice

    args = plan["args"]
    auto_choice: dict = {}
    asked_mode = args.get("classification_mode")
    if plan["auto"]:
        auto_choice = (_expression_choice(plan["expression"], facts["sample"]["values"]) if plan["expression"]
                       else graduated_choice(plan["field_name"], facts["sample"]))
        asked_mode = auto_choice.get("mode") or "equal_interval"
    mode_name = _MODE_NAMES.get(asked_mode, "EqualInterval")
    out = {"auto_choice": auto_choice, "mode_name": mode_name, "ranges": []}
    if plan["breaks"]:
        return out
    method, doubles = plan["methods"][mode_name], facts["doubles"]
    count = max(1, plan["classes"])
    if plan["by_expression"] or method.valuesRequired():
        values = doubles["values"]
        if mode_name == "Jenks" and len(values) > _JENKS_VALUES:
            out["classes_from"] = (f"{_JENKS_VALUES:,} of {len(values):,} values: the smallest, the largest and "
                                   "the values at evenly spaced ranks between them")
            values = _jenks_sample(values)
        found = method.classes(values, count) if values else []
    else:
        found = method.classes(doubles["min"], doubles["max"], count)
    out["ranges"] = [(r.label(), r.lowerBound(), r.upperBound()) for r in found]
    if str(args.get("classification_mode") or "").strip():
        sample = facts["sample"]
        if plan["expression"]:
            sample = [number for number in map(_number, sample["values"]) if number is not None]
        out["class_counts"] = class_counts(sample, [(float(low), float(high)) for _label, low, high in out["ranges"]])
    return out


def _style_apply(plan: dict, facts: dict) -> dict:

    from qgis.core import (
        QgsCategorizedSymbolRenderer,
        QgsProperty,
        QgsRenderContext,
        QgsRendererCategory,
        QgsSingleSymbolRenderer,
        QgsSymbol,
        QgsWkbTypes,
    )
    from qgis.PyQt.QtGui import QColor

    args = plan["args"]
    layer = QgsProject.instance().mapLayer(plan["layer_id"])
    if not isinstance(layer, QgsVectorLayer):
        return tool_error(f"{args.get('layer_name')} left the project before it was styled.", "EXECUTION_FAILED",
                          hint="chart_layer_gone", layer=str(args.get("layer_name")))
    if not background.still_awaited():
        return tool_error("Stopped before the style was applied.", "CANCELLED",
                          "Nothing was changed: the layer keeps its style.")
    style_type, kind, ramp = plan["style_type"], plan["kind"], plan["ramp"]
    color_ramp_name, invert_ramp, coerced_note = plan["color_ramp_name"], plan["invert_ramp"], plan["coerced_note"]
    proportional = plan["proportional"]
    if proportional:
        low, high = facts["size_range"]
        if low is None:
            return {"_error": f"size_field {proportional['field']!r} has no values to size by.", "code": "INVALID_ARGS"}
        proportional = dict(proportional, low=low, high=high if high > low else low + 1.0)

    if "diagram" in plan:
        return _set_diagram_renderer(layer, args, plan["diagram"], facts.get("largest", 0.0),
                                     kept_style=_style_to_put_back(layer))



    kept_style = _style_to_put_back(layer)

    fold_note: dict = {}

    other_catch_all = False

    auto_choice: dict = facts.get("auto_choice") or {}

    built_classes = kind in ("categorized", "graduated")
    if style_type == "single":
        color = args.get("color", "#3388ff")
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.setColor(qcolor_from_text(color))
        renderer = QgsSingleSymbolRenderer(symbol)

    elif kind == "relabel":
        renderer = layer.renderer().clone()
        ramp = None

    elif kind == "coloured":
        built = _coloured_categories(layer, plan, facts["colours"])
        if "_error" in built:
            return built
        renderer = built.pop("renderer")
        fold_note = built
        ramp = None

    elif kind == "categorized":
        field = plan["field"]
        decided = facts["categories"]
        unique_values, folded, n, scanned = decided["values"], decided["folded"], decided["n"], decided["scanned"]
        other_catch_all = decided["other_catch_all"]
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
                    f"{n_folded} others share one grey class"
                ),
                "advice": coded_fact(hint="style_categories_folded", field=field, distinct=n, shown=shown,
                                     folded=n_folded),
            }



            total = plan["total"]
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

    elif kind == "graduated":
        field = plan["field"]
        fold_note.update(plan["field_note"])
        mode_name = facts["mode_name"]
        classes = plan["classes"]
        base_symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        breaks = plan["breaks"]
        if breaks:


            renderer = _fixed_ranges(field, breaks, base_symbol, ramp)
            fold_note["breaks"] = breaks
        else:
            renderer = _classified(field, base_symbol, ramp, mode_name, facts["ranges"])
            if isinstance(renderer, dict):
                return renderer
        if len(renderer.ranges()) == 0:
            return tool_error(f"Field {field!r} has no values to classify.", "INVALID_ARGS",
                              hint="style_field_no_values", field=field)
        _readable_range_labels(renderer, args.get("units"))

    else:
        renderer = _cluster_renderer(layer, args)
        if isinstance(renderer, dict):
            return renderer


    bounds = ([(float(r.lowerValue()), float(r.upperValue())) for r in renderer.ranges()]
              if style_type == "graduated" else [])


    class_colors = ([r.symbol().color().name() for r in renderer.ranges() if r.symbol() is not None]
                    if style_type == "graduated" else [])
    no_value: dict = {}
    if built_classes:
        renderer, no_value = _no_value_class(layer, renderer, args, _missing(plan, facts))

    labelled: dict = {}
    if style_type == "categorized" and str(args.get("label_field") or "").strip():
        labelled = _labels_from_field(layer, renderer, str(args["label_field"]).strip(), facts.get("labels"),
                                      plan["cap"], plan["total"])




    classed_lines = (style_type in ("categorized", "graduated")
                     and layer.geometryType() == enum_member(QgsWkbTypes, "GeometryType", "LineGeometry"))
    applied = _apply_symbol_tweaks(renderer, args, classed_lines) if style_type != "cluster" else {"stroke_color"}
    if style_type == "categorized" and args.get("categories"):

        labelled.update(_listed_categories(layer, renderer, args["categories"], other_catch_all,
                                           bool(facts.get("nulls"))))
        if labelled.get("null_class"):

            no_value.pop("no_value_note", None)

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





    layer.setRenderer(renderer)
    renderer = layer.renderer()

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
        note = ("these arguments have no place on this layer's symbols and changed nothing: "
                "size sizes point markers, fill is a polygon's inside, stroke_width and "
                "stroke_color are a line or an outline")
        if classed_lines and "stroke_color" in not_applied:
            rest = [key for key in not_applied if key != "stroke_color"]
            note = ("stroke_color changed nothing: a line's colour is its class's, so one stroke_color would "
                    "draw every class the same and the classes keep their own colours"
                    + (f"; {', '.join(rest)} has no place on this layer's symbols" if rest else ""))
        result["not_applied_note"] = note
    if style_type == "graduated":
        result["classes"] = len(bounds)
        if class_colors:
            result["class_colors"] = class_colors
        if facts.get("classes_from"):
            result["classes_from"] = facts["classes_from"]
        result.update(fold_note)
        explicit_mode = str(args.get("classification_mode") or "").strip().lower()
        if explicit_mode and not fold_note.get("breaks"):




            from .style_defaults import dominant_class_note

            counts = facts.get("class_counts")
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
                result.update(closest_class_colours(renderer, facts.get("touching")))
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
            "note": (coded_fact(hint="style_proportional_area", field=proportional["field"])
                     if proportional_done == "area"
                     else coded_fact(hint="style_proportional_line", field=proportional["field"]))}
    elif proportional and args.get("size_expression"):
        result["size_field_note"] = "size_expression was given as well and sizes the symbols; size_field was not used"
    return result


def _classified(attribute: str, base_symbol, ramp, mode_name: str, ranges: list):



    from qgis.core import QgsApplication, QgsClassificationRange, QgsGraduatedSymbolRenderer, QgsRendererRange

    method = QgsApplication.classificationMethodRegistry().method(mode_name)
    if method is None:
        return {"_error": f"Graduated renderer is not available on this QGIS version: no {mode_name} method.",
                "code": "EXECUTION_FAILED"}
    renderer = QgsGraduatedSymbolRenderer(attribute, [])
    renderer.setSourceSymbol(base_symbol)
    renderer.setClassificationMethod(method)
    if ramp is not None:
        renderer.setSourceColorRamp(ramp.clone())
    for label, lower, upper in ranges:
        renderer.addClassRange(QgsRendererRange(QgsClassificationRange(label, lower, upper),
                                                renderer.sourceSymbol().clone()))
    renderer.updateColorRamp(None)
    return renderer


def _missing(plan: dict, facts: dict):

    return facts.get("nulls") if plan.get("total") else None


def _class_limits(breaks):

    if not breaks:
        return []
    try:
        limits = sorted({float(value) for value in breaks})
    except (TypeError, ValueError):
        return {"_error": "breaks are numbers, the class limits from lowest to highest.", "code": "INVALID_ARGS"}
    if len(limits) < 2:
        return tool_error("breaks needs at least two different limits (one class).", "INVALID_ARGS",
                          hint="style_breaks_too_few", given=len(limits))
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


def _no_value_class(layer, renderer, args: dict, missing) -> tuple:











    from qgis.core import QgsRendererCategory, QgsRuleBasedRenderer, QgsSymbol

    attribute = renderer.classAttribute()
    note: dict = {"features_without_value": missing} if missing else {}
    colour = str(args.get("null_class_color") or "").strip()
    label = str(args.get("null_class_label") or "").strip() or "No data"
    catch_all = renderer.type() == "categorizedSymbol" and any(
        not isinstance(c.value(), list) and (c.value() is None or str(c.value()) in ("", "NULL"))
        for c in renderer.categories())
    if not (colour or args.get("null_class_label")) or missing == 0:
        if missing and catch_all:
            note["no_value_note"] = f"{missing} features have no value in {attribute}; the catch-all class draws them"
        elif missing:
            note["no_value_note"] = (f"{missing} features have no value in {attribute} and are not drawn; "
                                     "null_class_color draws them in a class of their own")
        return renderer, note
    symbol = QgsSymbol.defaultSymbol(layer.geometryType())
    symbol.setColor(qcolor_from_text(colour or "#bdbdbd"))
    note["null_class"] = {"label": label, "color": symbol.color().name()}
    if renderer.type() == "categorizedSymbol":
        if catch_all:
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
        return tool_error(f"size_field {field!r} is {layer.fields().at(index).typeName()}, not a number.",
                          "INVALID_ARGS", hint="style_size_field_not_numeric", field=field,
                          field_type=layer.fields().at(index).typeName())
    geometry = layer.geometryType()
    if geometry == enum_member(QgsWkbTypes, "GeometryType", "PolygonGeometry"):
        return tool_error(f"size_field sizes point markers or line widths, and {layer.name()!r} is a polygon "
                          "layer. Nothing was changed.", "INVALID_ARGS", hint="style_size_field_polygon",
                          layer=layer.name(), field=field)
    markers = geometry == enum_member(QgsWkbTypes, "GeometryType", "PointGeometry")
    default_min, default_max = (2.0, 10.0) if markers else (0.3, 3.0)
    try:
        min_size = float(args.get("min_size") if args.get("min_size") is not None else default_min)
        max_size = float(args.get("max_size") if args.get("max_size") is not None else default_max)
    except (TypeError, ValueError):
        min_size, max_size = default_min, default_max
    if not max_size > min_size:
        min_size, max_size = default_min, default_max
    return {"field": field, "index": index, "min_size": min_size, "max_size": max_size, "markers": markers}





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
        return tool_error(f"Layer {target!r} is a {kind} layer, and set_layer_style paints vectors and rasters.",
                          "INVALID_ARGS", hint="style_layer_kind_unsupported", layer=target, kind=kind)

    bands = layer.bandCount()
    opacity = args.get("opacity")
    if bands != 1:


        return tool_error(f"Layer {target!r} has {bands} bands, so it is drawn as a colour image and has no "
                          "single value to classify.", "INVALID_ARGS", hint="style_raster_multiband",
                          layer=target, bands=bands)

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


def _numeric_class(layer, attribute) -> bool:

    index = layer.fields().indexOf(attribute) if attribute else -1
    return index >= 0 and layer.fields().at(index).isNumeric()


def _category_key(value, numeric: bool):


    if value is None or str(value) == "NULL":
        return None
    if numeric:
        with contextlib.suppress(TypeError, ValueError):
            return float(value)
    return str(value).strip()


def _listed_categories(layer, renderer, entries: list, other_catch_all: bool = False,
                       holds_no_value: bool = False) -> dict:







    from qgis.core import QgsRendererCategory, QgsSymbol

    numeric = _numeric_class(layer, renderer.classAttribute())
    index: dict = {}
    for i, category in enumerate(renderer.categories()):
        value = category.value()
        if isinstance(value, list):
            continue


        if value in (None, "") and other_catch_all:
            continue
        index.setdefault(None if value in (None, "") else _category_key(value, numeric), i)
    applied, unmatched, in_other, null_class = 0, [], [], None
    for entry in entries:
        value = entry.get("value")
        key = _category_key(value, numeric)
        i = index.get(key)
        if key is None:
            if not holds_no_value:
                unmatched.append(value)
                continue
            if other_catch_all:
                in_other.append(value)
                continue
            if i is None:
                renderer.addCategory(QgsRendererCategory("", QgsSymbol.defaultSymbol(layer.geometryType()),
                                                         "No data"))
                i = index[None] = len(renderer.categories()) - 1
        if i is None:
            unmatched.append(value)
            continue

        category = renderer.categories()[i]
        symbol = category.symbol().clone()
        if entry.get("color") not in (None, ""):
            symbol.setColor(qcolor_from_text(str(entry["color"])))
        if entry.get("size") is not None and hasattr(symbol, "setSize"):
            symbol.setSize(float(entry["size"]))
        if entry.get("width") is not None and hasattr(symbol, "setWidth"):
            symbol.setWidth(float(entry["width"]))
        renderer.updateCategorySymbol(i, symbol)
        if str(entry.get("label") or "").strip():
            renderer.updateCategoryLabel(i, str(entry["label"]).strip())
        if key is None:
            null_class = {"label": renderer.categories()[i].label(), "color": symbol.color().name()}
        applied += 1
    out = {"categories_applied": applied}
    if null_class:
        out["null_class"] = null_class
    if unmatched:
        out["categories_unmatched"] = unmatched
        out["categories_unmatched_note"] = "no feature holds these listed values, so they have no class"
    if in_other:
        out["categories_in_other"] = in_other
        out["categories_in_other_note"] = ("past the class cap the features with no value are drawn in the Other "
                                           "class, with every value that has no class of its own")
    return out


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


def _labels_from_field(layer, renderer, label_field: str, read: dict | None, cap: int, total) -> dict:



    fields = layer.fields()
    attribute = renderer.classAttribute() if hasattr(renderer, "classAttribute") else ""
    idx, lidx = fields.indexOf(attribute), fields.indexOf(label_field)
    if idx < 0 or lidx < 0 or read is None:
        return {"label_field_note": (f"the classes read {attribute!r}, an expression rather than a field, so "
                                     "their labels were left as they were")}
    votes, scanned = read["votes"], read["scanned"]
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
    if scanned >= cap and (total is None or scanned < total):
        out["label_warning"] = f"labels read on the first {scanned} features"
    return out


def _plan_coloured(layer, args: dict, plan: dict, rows) -> dict | None:











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
        return tool_error("color_field and keep_colors are two colour sources; pass one.", "INVALID_ARGS",
                          hint="style_colour_sources_conflict", color_field=color_field)
    if layer_order.is_remote_vector(layer):
        return tool_error(f"{layer.name()!r} is a web service layer: reading each feature's colour holds QGIS "
                          "while the service sends every row. Nothing was changed.", "INVALID_ARGS",
                          hint="style_remote_colour_read", layer=layer.name())

    if color_field:
        cidx = fields.indexOf(color_field)
        if cidx < 0:
            return _field_not_found_error(layer, color_field)
        colour_key, memo = rows.field(cidx), {}

        def colour_of(row):
            return _colour_name(row.value(colour_key), memo)

        job = style_rows.ColourVotes(rows.field(idx), plan["cap"], colour_of)
        plan.update(memo=memo, source=f"field {color_field}")
    else:
        if layer.renderer() is None:
            return {"_error": f"{layer.name()!r} has no style to keep colours from.", "code": "INVALID_ARGS",
                    "suggestion": "color_field or color_ramp is needed."}


        probe, probe_context = _render_clone(layer)
        probe.startRender(probe_context, fields)
        used = {str(name) for name in probe.usedAttributes(probe_context)}
        probe.stopRender(probe_context)
        current, context = _render_clone(layer)

        attribute = current.classAttribute() if hasattr(current, "classAttribute") else ""
        rows.needs(used | {field}, geometry=not (attribute and fields.indexOf(attribute) >= 0))
        symbols: dict = {}

        def colour_of(row):
            context.expressionContext().setFeature(row.feature)
            symbol = current.originalSymbolForFeature(row.feature, context)
            if symbol is None:
                return None
            name = symbol.color().name()
            if name not in symbols:
                symbols[name] = symbol.clone()
            return name

        job = style_rows.ColourVotes(rows.field(idx), plan["cap"], colour_of, current, context, fields)
        plan.update(symbols=symbols,
                    source=f"the layer's own style ({current.type()}{' on ' + attribute if attribute else ''})")
    rows.add("colours", job)
    plan.update(kind="coloured", field=field, color_field=color_field)
    return None


def _render_clone(layer) -> tuple:

    from qgis.core import QgsExpressionContext, QgsExpressionContextUtils, QgsRenderContext

    context = QgsRenderContext()
    context.setExpressionContext(QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer)))
    return layer.renderer().clone(), context


def _coloured_categories(layer, plan: dict, read: dict) -> dict:


    from qgis.core import (
        QgsCategorizedSymbolRenderer,
        QgsProperty,
        QgsRenderContext,
        QgsRendererCategory,
        QgsSingleSymbolRenderer,
        QgsSymbol,
        QgsWkbTypes,
    )
    from qgis.PyQt.QtGui import QColor

    field, color_field = plan["field"], plan["color_field"]
    memo, symbols = plan.get("memo") or {}, plan.get("symbols") or {}
    votes, values, blank = read["votes"], read["values"], read["blank"]
    scanned, no_value, unreadable = read["scanned"], read["no_value"], read["unreadable"]
    total = plan["total"]
    partial = scanned >= plan["cap"] and (total is None or scanned < total)
    n = len(values)
    if not n:
        return {"_error": f"Field {field!r} has no values to classify.", "code": "INVALID_ARGS",
                "suggestion": "A field with a value on at least one feature is needed."}
    geometry = layer.geometryType()
    line = geometry == enum_member(QgsWkbTypes, "GeometryType", "LineGeometry")
    note: dict = {"color_source": plan["source"], "features_read": scanned}


    base = QgsSymbol.defaultSymbol(geometry)
    with contextlib.suppress(Exception):
        drawn = layer.renderer().symbols(QgsRenderContext()) if layer.renderer() is not None else []
        if drawn and drawn[0] is not None and drawn[0].type() == base.type():
            base = drawn[0].clone()

    if n > _MAX_COLOURED_CATEGORIES:
        if not color_field:
            return tool_error(f"{n} distinct values in {field}: more classes than one style builds "
                              f"({_MAX_COLOURED_CATEGORIES}). Nothing was changed.", "INVALID_ARGS",
                              hint="style_categories_over_cap", field=field, distinct=n,
                              cap=_MAX_COLOURED_CATEGORIES)

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
            "seconds": round(time.monotonic() - plan["started"], 2),
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
                           "features")
        note["advice"] = coded_fact(hint="style_colours_partial_scan", scanned=scanned)
    note["seconds"] = round(time.monotonic() - plan["started"], 2)
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
    from qgis.PyQt.QtGui import QFont





    if layer.geometryType() != enum_member(QgsWkbTypes, "GeometryType", "PointGeometry"):
        return tool_error(f"Layer {layer.name()!r} is not a point layer.", hint="style_cluster_needs_points",
                          layer=layer.name())

    color = qcolor_from_text(str(args.get("color") or "#2b83ba"))
    label_color = qcolor_from_text(str(args.get("label_color") or "#ffffff"))
    stroke = qcolor_from_text(str(args.get("stroke_color") or "#ffffff"))
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


def _apply_symbol_tweaks(renderer, args: dict, classed_lines: bool = False) -> set:









    from qgis.core import QgsLineSymbolLayer

    stroke_color = None if classed_lines else args.get("stroke_color")
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
                    sl.setColor(qcolor_from_text(stroke_color))
                    applied.add("stroke_color")
                elif hasattr(sl, "setStrokeColor"):
                    sl.setStrokeColor(qcolor_from_text(stroke_color))
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
        return [], tool_error("flash_features needs 'fids' or 'expression'.", "INVALID_ARGS",
                              hint="flash_needs_target", layer=layer.name())
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
        return [], tool_error(f"More than {_FLASH_MAX_FEATURES} features match: a flash that covers the map "
                              "points at nothing.", "INVALID_ARGS", hint="flash_too_many",
                              limit=_FLASH_MAX_FEATURES)
    if not found:
        if wanted is not None:
            return [], tool_error(f"No feature of {layer.name()!r} carries any of these ids: "
                                  f"{', '.join(str(v) for v in wanted[:20])}.", "INVALID_ARGS",
                                  hint="flash_ids_not_found", layer=layer.name(), fids=wanted[:20])
        return [], tool_error(f"No feature of {layer.name()!r} matches {expression!r}.", "INVALID_ARGS",
                              hint="flash_expression_no_match", layer=layer.name(), expression=expression)
    return found, None


def _flash_features(args: dict) -> dict:









    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])
    if not isinstance(layer, QgsVectorLayer):
        return tool_error(f"Layer {layer.name()!r} is not a vector layer, and only features can be flashed.",
                          "INVALID_ARGS", hint="flash_needs_vector", layer=layer.name())

    canvas = iface.mapCanvas() if iface is not None else None
    if canvas is None or not hasattr(canvas, "flashFeatureIds"):
        return tool_error("This QGIS has no map canvas feature flash.", "EXECUTION_FAILED",
                          hint="flash_unavailable")

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
                 "screen."),
        "advice": coded_fact(hint="flash_is_temporary"),
    }


def _expression_error(layer, text: str, argument: str = "Label expression") -> dict | None:





    from qgis.core import QgsExpression, QgsExpressionContext, QgsExpressionContextUtils

    expression = QgsExpression(text)
    context = QgsExpressionContext()
    context.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    expression.prepare(context)
    if expression.hasParserError():


        return tool_error(f"{argument} parse error: {expression.parserErrorString().strip()}",
                          "EXPRESSION_INVALID", hint="expression_parse_error", argument=argument,
                          fields=[f.name() for f in layer.fields()])
    names = {f.name() for f in layer.fields()}
    unknown = sorted(str(column) for column in expression.referencedColumns() if str(column) not in names)
    if unknown:
        return _field_not_found_error(layer, unknown[0])
    return None


def _label_expression_error(layer, field: str) -> dict | None:
    return _expression_error(layer, field, "Label expression")


def _label_sample(layer, settings) -> str | None:


    from qgis.core import QgsExpression, QgsExpressionContext, QgsExpressionContextUtils, QgsFeatureRequest

    try:
        name = settings.fieldName
        expression = QgsExpression(name if settings.isExpression else QgsExpression.quotedColumnRef(name))
        context = QgsExpressionContext()
        context.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
        expression.prepare(context)
        for feature in layer.getFeatures(QgsFeatureRequest().setLimit(50)):
            context.setFeature(feature)
            value = expression.evaluate(context)
            if value not in (None, "") and not expression.hasEvalError() and str(value) != "NULL":
                return str(value)[:60]
    except Exception:  # nosec B110
        pass
    return None


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
    return tool_error(f"{argument} {text!r} is not a colour Qt understands.", "INVALID_ARGS",
                      hint="colour_not_understood", argument=argument, value=text)






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
            return tool_error(f"{layer.name()!r} is a web service layer: reading each feature's label holds QGIS "
                              "while the service sends every row. Nothing was changed.", "INVALID_ARGS",
                              hint="style_remote_label_read", layer=layer.name())
    categories = args.get("categories")
    if categories:
        if str(args.get("style_type") or "").strip() != "categorized":
            return {"_error": "categories gives the classes of a categorized style.", "code": "INVALID_ARGS",
                    "suggestion": "style_type categorized with field takes categories."}
        for entry in categories:
            if not isinstance(entry, dict) or "value" not in entry:
                return {"_error": "Each entry of categories is an object with a value.", "code": "INVALID_ARGS",
                        "suggestion": 'For example {"value": "forest", "label": "Forest", "color": "#2e7d32"}.'}
            bad = _color_error(entry.get("color"), "categories color")
            if bad:
                return bad
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
            return tool_error("value_expression takes its colours from color_ramp; color_field, keep_colors and "
                              "label_field read a field's own values.", "INVALID_ARGS",
                              hint="style_value_expression_colour_conflict", value_expression=value_expression)
        bad = _expression_error(layer, value_expression, "value_expression")
        if bad:
            return bad
    return None


def _expression_choice(text: str, values: list) -> dict:


    from .style_defaults import graduated_choice

    numbers = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number == number:
            numbers.append(number)
    return graduated_choice(text, numbers)


def _set_layer_labels(args: dict) -> dict:

    result = _set_layer_labels_2d(args)
    if not args.get("in_3d") or not isinstance(result, dict) or "_error" in result \
            or result.get("labels") == "disabled":
        return result
    floating = style_3d.float_labels(_find_layer(args["layer_name"]))
    if "_error" in floating:
        return {**floating, "_error": floating["_error"] + " The 2D labels were set."}
    result["labels_3d"] = floating
    return result


def _set_layer_labels_2d(args: dict) -> dict:
    from qgis.core import Qgis, QgsPalLayerSettings, QgsTextFormat, QgsVectorLayerSimpleLabeling, QgsWkbTypes
    from qgis.PyQt.QtGui import QFont

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
            text_format.setColor(qcolor_from_text(rule_color))
        styled = _label_text_look(text_format, spec, args)
        if styled:
            return styled
        buffer_size = spec.get("buffer_size", args.get("buffer_size"))
        buffer_color = spec.get("buffer_color", args.get("buffer_color"))
        halo = text_format.buffer()
        if buffer_size:
            halo.setEnabled(True)
            halo.setSize(float(buffer_size))
            halo.setColor(qcolor_from_text(buffer_color or "#ffffff"))
        elif buffer_size is not None and not fresh:
            halo.setEnabled(False)
        elif buffer_color and not fresh:
            halo.setColor(qcolor_from_text(buffer_color))
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
        sample = _label_sample(layer, settings)
        if sample:
            out["label_sample"] = sample
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
            "note": coded_fact(hint="label_scale_visibility", min_scale=min_scale, max_scale=max_scale)}
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
                return tool_error(f"Unknown label property {name!r} in data_defined.", "INVALID_ARGS",
                                  hint="label_property_unknown", name=str(name))
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
        from .processing_run import remove_layers


        remove_layers([old_id])
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
