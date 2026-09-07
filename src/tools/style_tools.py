# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Symbology, labeling and screenshot handlers, plus the batch-command runner (kept here since it is small and only wires into the registry)."""



from __future__ import annotations

import contextlib
import os

from qgis.core import QgsMapRendererSequentialJob, QgsMapSettings, QgsVectorLayer
from qgis.utils import iface

from ..core import limits
from ..core.qt_compat import enum_member
from . import guards
from ._images import image_to_base64, normalize_fmt
from .layer_lookup import _field_not_found_error, _find_layer, _layer_not_found_error



_MAX_CATEGORIES = 500





_READABLE_CATEGORIES = 30
_OTHER_LABEL = "Other"
_OTHER_COLOR = "#9e9e9e"


def _make_batch_handler(registry):
    def _batch_commands(args: dict) -> dict:
        commands = args.get("commands")
        if not isinstance(commands, list) or not commands:
            return {"_error": "commands must be a non-empty array of {name, arguments}"}
        stop_on_error = args.get("stop_on_error", True)
        results = []
        for i, cmd in enumerate(commands):
            if not isinstance(cmd, dict) or not cmd.get("name"):
                res = {"_error": "each command needs a 'name'"}
            elif cmd["name"] == "batch_commands":
                res = {"_error": "batch_commands cannot be nested"}
            elif guards.always_confirm(cmd["name"], cmd.get("arguments")):
                res = {"_error": f"{cmd['name']} needs its own confirmation card and cannot run inside "
                                 "batch_commands"}
            else:
                res = registry.execute(cmd["name"], cmd.get("arguments", {}) or {})
            results.append({"name": cmd.get("name"), "result": res})
            if stop_on_error and isinstance(res, dict) and res.get("_error"):
                return {"results": results, "executed": i + 1, "stopped_at": i, "error": res["_error"]}
        return {"results": results, "executed": len(results), "stopped_at": None}

    return _batch_commands


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
    )
    from qgis.PyQt.QtGui import QColor







    for theirs, ours in (("layer", "layer_name"), ("fill_color", "color"), ("symbol_size", "size")):
        if args.get(ours) in (None, "") and args.get(theirs) not in (None, ""):
            args[ours] = args[theirs]
    target = args.get("layer_name")
    if not target:
        return {"_error": "layer_name is required.", "code": "INVALID_ARGS",
                "suggestion": "Name the layer to style, or pass its id."}
    layer = _find_layer(target)
    if not layer:
        return _layer_not_found_error(target)

    if not isinstance(layer, QgsVectorLayer):
        return {"_error": f"Layer {target!r} is not a vector layer"}

    style_type = str(args.get("style_type") or "").strip()
    if not style_type:
        return {"_error": "style_type is required.", "code": "INVALID_ARGS",
                "suggestion": "single, categorized, graduated or cluster."}

    bad_argument = _style_args_error(layer, args)
    if bad_argument:
        return bad_argument



    color_ramp_name = args.get("color_ramp")
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
                except Exception:  # nosec B110 - fallback only
                    ramp = None
        if ramp is None:
            return {
                "_error": f"Unknown color ramp: {color_ramp_name!r}.",
                "available_ramps": default_style.colorRampNames(),
            }

    fold_note: dict = {}
    if style_type == "single":
        color = args.get("color", "#3388ff")
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.setColor(QColor(color))
        renderer = QgsSingleSymbolRenderer(symbol)
        layer.setRenderer(renderer)

    elif style_type == "categorized":
        field = args.get("field")
        if not field:
            return {"_error": "Field is required for categorized style"}

        idx = layer.fields().indexOf(field)
        if idx < 0:
            return _field_not_found_error(layer, field)





        unique_values = list(layer.uniqueValues(idx, _MAX_CATEGORIES + 1))
        n = len(unique_values)





        overflow = n > _MAX_CATEGORIES





        with contextlib.suppress(TypeError):
            unique_values = sorted(unique_values, key=lambda v: (str(type(v)), v))





        max_classes = _requested_classes(args) or _READABLE_CATEGORIES
        folded, scanned = [], 0
        if n > max_classes:
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
            categories.append(QgsRendererCategory(value, symbol, str(value)))
        if folded or folded is None:




            other = QgsSymbol.defaultSymbol(layer.geometryType())
            other.setColor(QColor(_OTHER_COLOR))
            tail = list(folded) if folded else ""
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
            if scanned and scanned < layer.featureCount():
                fold_note["warning"] += f" (frequencies measured on the first {scanned} features)"

        renderer = QgsCategorizedSymbolRenderer(field, categories)
        layer.setRenderer(renderer)

    elif style_type == "graduated":
        field = args.get("field")
        if not field:
            return {"_error": "Field is required for graduated style"}

        idx = layer.fields().indexOf(field)
        if idx < 0:
            return _field_not_found_error(layer, field)

        mode_names = {
            "equal_interval": "EqualInterval",
            "quantile": "Quantile",
            "jenks": "Jenks",
            "pretty": "Pretty",
        }
        mode_name = mode_names.get(args.get("classification_mode", "equal_interval"), "EqualInterval")
        classes = int(args.get("classes", 5) or 5)
        base_symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        if ramp is None:
            ramp = QgsStyle.defaultStyle().colorRamp("Viridis") or QgsStyle.defaultStyle().colorRamp("Blues")
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
        layer.setRenderer(renderer)

    elif style_type == "cluster":
        renderer = _cluster_renderer(layer, args)
        if isinstance(renderer, dict):
            return renderer
        layer.setRenderer(renderer)

    else:
        return {"_error": f"Unknown style type: {style_type}"}

    if style_type != "cluster":
        _apply_symbol_tweaks(renderer, args)

    if args.get("size_expression") and style_type != "cluster":
        for symbol in renderer.symbols(QgsRenderContext()):
            symbol.setDataDefinedSize(QgsProperty.fromExpression(args["size_expression"]))

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
    if ramp is not None:
        result["color_ramp"] = color_ramp_name
    if args.get("classification_mode"):
        result["classification_mode"] = args["classification_mode"]

    for key in ("stroke_color", "stroke_width", "size", "fill"):
        if args.get(key) is not None:
            result[key] = args[key]
    if style_type == "graduated":
        result["classes"] = len(renderer.ranges())
    elif style_type == "categorized":
        result["classes"] = len(renderer.categories())
        result.update(fold_note)
    if style_type == "cluster":
        result["cluster_distance_mm"] = renderer.tolerance()
        result["separates_on_zoom"] = True
    if args.get("null_class_color"):
        result["null_class_note"] = "Use apply_style_qml to add the requested NULL rule."
    return result


def _requested_classes(args: dict) -> int:
    """The class ceiling the caller asked for, 0 when they asked for nothing."""






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
    """Main thread: how often each value of ``index`` occurs, keyed by ``str``."""







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


def _symbol_layer_property(member: str):
    """``QgsSymbolLayer.Property.Size`` on Qt6, ``PropertySize`` on Qt5."""
    from qgis.core import QgsSymbolLayer

    scoped = getattr(QgsSymbolLayer, "Property", None)
    found = getattr(scoped, member, None) if scoped is not None else None
    if found is not None:
        return found
    return getattr(QgsSymbolLayer, f"Property{member}")


def _cluster_renderer(layer, args: dict):
    """Dense points as clusters that carry their count and split as you zoom in."""











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

    if layer.geometryType() != QgsWkbTypes.GeometryType.PointGeometry:
        return {"_error": f"Layer {layer.name()!r} is not a point layer.",
                "suggestion": "Clusters group points. Use single, categorized or graduated here."}

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


def _apply_symbol_tweaks(renderer, args: dict):
    from qgis.PyQt.QtGui import QColor

    stroke_color = args.get("stroke_color")
    stroke_width = args.get("stroke_width")
    size = args.get("size")
    fill = args.get("fill")

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
            except (TypeError, ValueError):
                pass
        for i in range(symbol.symbolLayerCount()):
            sl = symbol.symbolLayer(i)
            if fill in ("none", "solid") and hasattr(sl, "setBrushStyle"):
                from qgis.PyQt.QtCore import Qt as _Qt

                sl.setBrushStyle(_Qt.BrushStyle.NoBrush if fill == "none" else _Qt.BrushStyle.SolidPattern)
            if stroke_color is not None and hasattr(sl, "setStrokeColor"):
                sl.setStrokeColor(QColor(stroke_color))
            if stroke_width is not None and hasattr(sl, "setStrokeWidth"):
                try:
                    sl.setStrokeWidth(float(stroke_width))
                except (TypeError, ValueError):
                    pass


def _capture_format(args: dict, default: str) -> tuple[str, int]:
    """Resolve (format, quality) for a capture. save_path extension wins if it names one."""
    fmt = args.get("format")
    save_path = args.get("save_path")
    if not fmt and save_path:
        fmt = os.path.splitext(str(save_path))[1].lstrip(".")
    quality = max(min(int(args.get("quality", 95) or 95), 100), 10)
    return normalize_fmt(fmt, default), quality


def _take_screenshot(args: dict) -> dict:




    max_width = max(100, min(int(args.get("max_width", 1600) or 1600), limits.MAX_RENDER_WIDTH_PX))
    fmt, quality = _capture_format(args, "jpeg")






    layers_only = bool(args.get("layers_only", False))

    canvas = iface.mapCanvas()
    if canvas.width() <= 0 or canvas.height() <= 0:
        return {"_error": "Map canvas has no size (window may be minimized)"}




    if hasattr(canvas, "waitWhileRendering"):
        canvas.waitWhileRendering()

    if not layers_only:
        from qgis.PyQt.QtCore import Qt

        image = canvas.grab().toImage()
        if not image.isNull():
            width = image.width()
            height = image.height()
            if width > max_width:
                image = image.scaledToWidth(max_width, Qt.TransformationMode.SmoothTransformation)
                width, height = image.width(), image.height()
            save_path = args.get("save_path")
            if save_path:
                if not image.save(save_path):
                    return {"_error": f"Could not write screenshot to {save_path}"}
                return {"saved_path": save_path, "width": width, "height": height, "format": fmt, "overlays": True}
            return {
                "image_base64": image_to_base64(image, fmt, quality),
                "width": width,
                "height": height,
                "format": fmt,
                "overlays": True,
            }


    settings = QgsMapSettings(canvas.mapSettings())

    width = min(canvas.width(), max_width)
    height = int(width * canvas.height() / canvas.width())

    from qgis.PyQt.QtCore import QSize

    settings.setOutputSize(QSize(width, height))

    job = QgsMapRendererSequentialJob(settings)
    job.start()
    job.waitForFinished()

    image = job.renderedImage()
    save_path = args.get("save_path")
    if save_path:
        if not image.save(save_path):
            return {"_error": f"Could not write screenshot to {save_path}"}
        return {"saved_path": save_path, "width": width, "height": height, "format": fmt}
    return {"image_base64": image_to_base64(image, fmt, quality), "width": width, "height": height, "format": fmt}


def _expression_error(layer, text: str, argument: str = "Label expression") -> dict | None:
    """None when *text* is an expression this layer can evaluate, else why not."""




    from qgis.core import QgsExpression, QgsExpressionContext, QgsExpressionContextUtils

    expression = QgsExpression(text)
    context = QgsExpressionContext()
    context.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    expression.prepare(context)
    if expression.hasParserError():


        return {"_error": f"{argument} parse error: {expression.parserErrorString().strip()}",
                "_code": "EXPRESSION_INVALID",
                "suggestion": "Call validate_expression to see the error, or pass a field name.",
                "fields": [f.name() for f in layer.fields()]}
    names = {f.name() for f in layer.fields()}
    unknown = sorted(str(column) for column in expression.referencedColumns() if str(column) not in names)
    if unknown:
        return _field_not_found_error(layer, unknown[0])
    return None


def _label_expression_error(layer, field: str) -> dict | None:
    return _expression_error(layer, field, "Label expression")


def _color_error(value, argument: str) -> dict | None:
    """None when Qt understands *value* as a colour, else why not."""





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
            "suggestion": ("Pass a hex colour such as '#2b83ba', or one of the SVG colour names "
                           "('steelblue', 'darkgreen'). Descriptive phrases are not colours.")}






_COLOUR_ARGS = (("color", "color"), ("stroke_color", "stroke_color"),
                ("label_color", "label_color"), ("null_class_color", "null_class_color"))


def _style_args_error(layer, args: dict) -> dict | None:
    for key, label in _COLOUR_ARGS:
        bad = _color_error(args.get(key), label)
        if bad:
            return bad
    expression = args.get("size_expression")
    if expression:
        bad = _expression_error(layer, str(expression), "size_expression")
        if bad:
            return bad
    return None


def _set_layer_labels(args: dict) -> dict:
    from qgis.core import QgsPalLayerSettings, QgsTextFormat, QgsVectorLayerSimpleLabeling
    from qgis.PyQt.QtGui import QColor, QFont

    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])
    if not isinstance(layer, QgsVectorLayer):
        return {"_error": f"Layer '{args['layer_name']}' is not a vector layer"}

    enabled = args.get("enabled", True)
    if not enabled:
        layer.setLabelsEnabled(False)
        layer.triggerRepaint()
        return {"labels": "disabled", "layer": args["layer_name"]}

    field = args["field"]
    size = args.get("size", 10)
    color = args.get("color", "#000000")
    for key, label in (("color", "color"), ("buffer_color", "buffer_color")):
        bad = _color_error(args.get(key), label)
        if bad:
            return bad

    settings = QgsPalLayerSettings()
    settings.fieldName = field



    is_expression = layer.fields().indexOf(field) < 0
    if is_expression:
        error = _label_expression_error(layer, field)
        if error:
            return error
    settings.isExpression = is_expression

    text_format = QgsTextFormat()
    font = QFont()
    font.setPointSizeF(size)
    text_format.setFont(font)
    text_format.setSize(size)
    text_format.setColor(QColor(color))
    buffer_size = args.get("buffer_size")
    if buffer_size:
        halo = text_format.buffer()
        halo.setEnabled(True)
        halo.setSize(float(buffer_size))
        halo.setColor(QColor(args.get("buffer_color", "#ffffff")))
        text_format.setBuffer(halo)
    settings.setFormat(text_format)

    avoid_overlaps = bool(args.get("avoid_overlaps", True))
    settings.displayAll = not avoid_overlaps
    if avoid_overlaps:
        try:
            from qgis.core import Qgis
            placement = settings.placementSettings()
            placement.setOverlapHandling(Qgis.LabelOverlapHandling.PreventOverlap)
            settings.setPlacementSettings(placement)
        except Exception:  # nosec B110 - older QGIS has no overlap handling
            pass






    min_scale = float(args.get("min_scale") or 0)
    max_scale = float(args.get("max_scale") or 0)
    if min_scale or max_scale:
        settings.scaleVisibility = True
        settings.minimumScale = min_scale
        settings.maximumScale = max_scale
    labeling = QgsVectorLayerSimpleLabeling(settings)
    layer.setLabeling(labeling)
    layer.setLabelsEnabled(True)
    layer.triggerRepaint()
    out = {"labels": "enabled", "layer": args["layer_name"], "field": field}
    if min_scale or max_scale:
        out["scale_visibility"] = {
            "min_scale": min_scale, "max_scale": max_scale,
            "note": ("labels draw between 1:max_scale (zoomed in) and "
                     "1:min_scale (zoomed out); 0 is no limit")}
    return out
