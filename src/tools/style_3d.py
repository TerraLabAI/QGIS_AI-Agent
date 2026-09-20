# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
















from __future__ import annotations

from qgis.core import (
    Qgis,
    QgsAnnotationLayer,
    QgsAnnotationPointTextItem,
    QgsCategorizedSymbolRenderer,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsFeatureRequest,
    QgsGraduatedSymbolRenderer,
    QgsPalLayerSettings,
    QgsProject,
    QgsProperty,
    QgsPropertyCollection,
    QgsRenderContext,
    QgsRuleBasedRenderer,
    QgsSingleSymbolRenderer,
)
from qgis.PyQt.QtGui import QColor


MAX_FLOATING = 500
LABELS_PROPERTY = "terralab/labels_3d_of"

FIDS_PROPERTY = "terralab/labels_3d_fids"


def _quoted(color: QColor) -> str:
    return QgsExpression.quotedString(color.name())


def _attribute(renderer, layer) -> str:

    text = renderer.classAttribute()
    return QgsExpression.quotedColumnRef(text) if layer.fields().indexOf(text) >= 0 else text


def _colour_cases(renderer, layer) -> tuple[str | None, QColor | None, str]:

    if isinstance(renderer, QgsGraduatedSymbolRenderer):
        value = f"({_attribute(renderer, layer)})"
        ranges = [r for r in renderer.ranges() if r.renderState() and r.symbol() is not None]
        if not ranges:
            return None, None, "graduated, no class drawn"
        arms = []
        for index, item in enumerate(ranges):
            low = ">=" if index == 0 else ">"
            arms.append(f"WHEN {value} {low} {item.lowerValue()!r} AND {value} <= {item.upperValue()!r} "
                        f"THEN {_quoted(item.symbol().color())}")
        return ("CASE " + " ".join(arms) + " END", ranges[0].symbol().color(),
                f"graduated on {renderer.classAttribute()}, {len(ranges)} classes")
    if isinstance(renderer, QgsCategorizedSymbolRenderer):
        value = f"({_attribute(renderer, layer)})"
        arms, fallback, first = [], None, None
        for item in renderer.categories():
            if not item.renderState() or item.symbol() is None:
                continue
            first = first or item.symbol().color()
            if item.value() is None or item.value() == "":
                fallback = item.symbol().color()
                continue
            quoted = QgsExpression.quotedValue(item.value())
            arms.append(f"WHEN {value} = {quoted} THEN {_quoted(item.symbol().color())}")
        if not arms:
            return None, first, "categorized, no category drawn"
        tail = f" ELSE {_quoted(fallback)}" if fallback is not None else ""
        return ("CASE " + " ".join(arms) + tail + " END", first,
                f"categorized on {renderer.classAttribute()}, {len(arms)} categories")
    if isinstance(renderer, QgsRuleBasedRenderer):
        arms, rest, first = [], None, None
        for rule in renderer.rootRule().descendants():
            if rule.children() or rule.symbol() is None or not rule.active():
                continue
            first = first or rule.symbol().color()

            if rule.isElse():
                rest = rest or rule.symbol().color()
                continue
            test = rule.filterExpression() or "TRUE"
            arms.append(f"WHEN ({test}) THEN {_quoted(rule.symbol().color())}")
        if not arms:
            return None, first, "rule-based, no rule with a symbol"
        tail = f" ELSE {_quoted(rest)}" if rest is not None else ""
        return "CASE " + " ".join(arms) + tail + " END", first, f"rule-based, {len(arms)} rules"
    symbol = renderer.symbol() if isinstance(renderer, QgsSingleSymbolRenderer) else None
    if symbol is None:
        symbols = renderer.symbols(QgsRenderContext()) if renderer is not None else []
        symbol = symbols[0] if symbols else None
    return None, (QColor(symbol.color()) if symbol is not None else None), "one colour"


def _outline(renderer) -> tuple[QColor | None, float | None]:

    symbols = renderer.symbols(QgsRenderContext()) if renderer is not None else []
    for symbol in symbols:
        for index in range(symbol.symbolLayerCount()):
            part = symbol.symbolLayer(index)
            if hasattr(part, "strokeColor") and hasattr(part, "strokeWidth") and part.strokeStyle() != 0:
                return QColor(part.strokeColor()), float(part.strokeWidth())
    return None, None


def vector_renderer(layer):



    renderer = layer.renderer3D() if hasattr(layer, "renderer3D") else None
    if renderer is None or renderer.type() != "vector":
        return None
    from qgis._3d import QgsVectorLayer3DRenderer
    from qgis.PyQt import sip

    return sip.cast(renderer, QgsVectorLayer3DRenderer)


def polygon_symbol(layer):

    renderer = vector_renderer(layer)
    symbol = renderer.symbol() if renderer is not None else None
    return symbol if symbol is not None and symbol.type() == "polygon" else None


def material_access(symbol) -> tuple:


    return (getattr(symbol, "materialSettings", None) or symbol.material,
            getattr(symbol, "setMaterialSettings", None) or symbol.setMaterial)


def _with_colours(material, cases):






    from qgis._3d import QgsPhongMaterialSettings
    from qgis.core import QgsPropertyDefinition, QgsReadWriteContext
    from qgis.PyQt.QtXml import QDomDocument

    document = QDomDocument()
    element = document.createElement("material")
    material.writeXml(element, QgsReadWriteContext())
    held = element.firstChildElement("data-defined-properties")
    if held.isNull():
        return material
    colours = QgsPropertyCollection()
    colours.setProperty(0, QgsProperty.fromExpression(cases))
    colours.setProperty(1, QgsProperty.fromExpression(f"darker({cases}, 200)"))
    template = getattr(QgsPropertyDefinition, "StandardPropertyTemplate", QgsPropertyDefinition).ColorNoAlpha
    written = document.createElement("data-defined-properties")
    colours.writeXml(written, {0: QgsPropertyDefinition("diffuse", "Diffuse", template),
                               1: QgsPropertyDefinition("ambient", "Ambient", template)})
    element.replaceChild(written, held)
    painted = QgsPhongMaterialSettings()
    painted.readXml(element, QgsReadWriteContext())
    return painted


def paint(symbol, layer) -> dict:

    from qgis._3d import QgsAbstractMaterialSettings, QgsPhongMaterialSettings, QgsPolygon3DSymbol
    from qgis.PyQt import sip


    symbol = sip.cast(symbol, QgsPolygon3DSymbol)
    renderer = layer.renderer()
    cases, first, reads = _colour_cases(renderer, layer)




    read_material, hand_material = material_access(symbol)
    kept = read_material()
    material = QgsPhongMaterialSettings()
    for name in ("Specular", "Shininess", "Opacity"):
        if hasattr(kept, name[0].lower() + name[1:]) and hasattr(material, "set" + name):
            getattr(material, "set" + name)(getattr(kept, name[0].lower() + name[1:])())
    base = QColor(first) if first is not None else QColor("#b4b4b4")
    material.setDiffuse(base)
    material.setAmbient(base.darker(200))
    facts = {"color_from": reads, "color": base.name()}


    if hasattr(material, "setDataDefinedProperties"):
        colours = QgsPropertyCollection(material.dataDefinedProperties())
        for key in (QgsAbstractMaterialSettings.Property.Diffuse, QgsAbstractMaterialSettings.Property.Ambient):
            colours.setProperty(key, QgsProperty())
        if cases:
            colours.setProperty(QgsAbstractMaterialSettings.Property.Diffuse, QgsProperty.fromExpression(cases))
            colours.setProperty(QgsAbstractMaterialSettings.Property.Ambient,
                                QgsProperty.fromExpression(f"darker({cases}, 200)"))
        material.setDataDefinedProperties(colours)
    elif cases:
        material = _with_colours(material, cases)
    hand_material(material)
    edge, width_mm = _outline(renderer)
    if edge is not None and hasattr(symbol, "setEdgesEnabled"):
        symbol.setEdgesEnabled(True)
        symbol.setEdgeColor(edge)

        symbol.setEdgeWidth(max(1.0, round(width_mm * 96.0 / 25.4, 1)))
        facts["edges"] = {"color": edge.name(), "width_px": symbol.edgeWidth()}
    return facts


def colour_from_2d(layer) -> dict | None:

    symbol = polygon_symbol(layer)
    if symbol is None:
        return None
    renderer = vector_renderer(layer).clone()
    facts = paint(renderer.symbol(), layer)
    layer.setRenderer3D(renderer)
    layer.triggerRepaint()
    return facts




def _roof(layer):

    from qgis._3d import QgsPolygon3DSymbol
    from qgis.core import QgsAbstract3DSymbol
    from qgis.PyQt import sip

    symbol = polygon_symbol(layer)
    if symbol is None:
        return None
    symbol = sip.cast(symbol, QgsPolygon3DSymbol)

    collection = QgsPropertyCollection(symbol.dataDefinedProperties())
    height = QgsProperty(collection.property(QgsAbstract3DSymbol.Property.ExtrusionHeight))
    if height.isActive():
        text = height.expressionString() or QgsExpression.quotedColumnRef(height.field())
        return f"({text}) + {symbol.offset()!r}"
    return f"{symbol.extrusionHeight() + symbol.offset()!r}"


def float_height(layer, fids) -> float:


    roof_text = _roof(layer)
    top = 0.0
    if roof_text and fids:
        roof = QgsExpression(roof_text)
        context = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
        roof.prepare(context)
        for feature in layer.getFeatures(QgsFeatureRequest().setFilterFids(list(fids))):
            context.setFeature(feature)
            try:
                top = max(top, float(roof.evaluate(context)))
            except (TypeError, ValueError):
                pass
    return round(top + max(15.0, top * 0.08), 1)


def refloat_labels(layers) -> list:



    from qgis._3d import QgsAnnotationLayer3DRenderer
    from qgis.PyQt import sip

    moved = []
    project = QgsProject.instance()
    for notes in layers:
        if not isinstance(notes, QgsAnnotationLayer) or not notes.customProperty(LABELS_PROPERTY):
            continue
        source = project.mapLayer(str(notes.customProperty(LABELS_PROPERTY)))
        current = notes.renderer3D()
        if source is None or current is None:
            continue
        current = sip.cast(current, QgsAnnotationLayer3DRenderer)
        try:
            fids = [int(fid) for fid in str(notes.customProperty(FIDS_PROPERTY) or "").split(",") if fid]
        except ValueError:
            continue
        height = float_height(source, fids)
        if abs(height - current.zOffset()) < 0.05:
            continue
        floating = current.clone()
        floating.setZOffset(height)
        notes.setRenderer3D(floating)
        moved.append({"layer": notes.name(), "height_above_ground_m": height})
    return moved


def float_labels(layer) -> dict:

    try:
        from qgis._3d import QgsAnnotationLayer3DRenderer
    except ImportError:
        QgsAnnotationLayer3DRenderer = None
    if QgsAnnotationLayer3DRenderer is None:
        return {"_error": "This QGIS draws no text in a 3D view: annotation texts in 3D arrived in QGIS 4.0.",
                "code": "UNSUPPORTED_QGIS_VERSION", "suggestion": "The labels are set on the 2D map."}
    labeling = layer.labeling()
    if labeling is None or labeling.type() != "simple":
        return {"_error": "in_3d reads one set of labels; this layer's labels are rule-based.", "code": "INVALID_ARGS",
                "suggestion": "A layer labelled by field or expression floats its labels in 3D."}
    settings = QgsPalLayerSettings(labeling.settings())
    text = settings.fieldName if settings.isExpression else QgsExpression.quotedColumnRef(settings.fieldName)
    label = QgsExpression(text)
    properties = QgsPropertyCollection(settings.dataDefinedProperties())
    show = QgsProperty(properties.property(QgsPalLayerSettings.Property.Show))
    shown = QgsExpression(show.expressionString()) if show.isActive() and show.expressionString() else None
    context = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    for expression in (label, shown):
        if expression is not None:
            expression.prepare(context)
    points, fids = [], []
    for feature in layer.getFeatures(QgsFeatureRequest()):
        context.setFeature(feature)
        if shown is not None and not shown.evaluate(context):
            continue
        name = label.evaluate(context)
        if name is None or str(name) == "" or feature.geometry().isEmpty():
            continue
        points.append((str(name), feature.geometry().pointOnSurface().asPoint()))
        fids.append(feature.id())
        if len(points) > MAX_FLOATING:
            return {"_error": f"More than {MAX_FLOATING} labels would float in 3D.", "code": "INVALID_ARGS",
                    "suggestion": "A Show filter in data_defined keeps the ones to float."}
    project = QgsProject.instance()
    name = f"{layer.name()} labels 3D"
    from .processing_run import remove_layers

    remove_layers([old.id() for old in project.mapLayers().values()
                   if isinstance(old, QgsAnnotationLayer) and old.customProperty(LABELS_PROPERTY) == layer.id()])
    notes = QgsAnnotationLayer(name, QgsAnnotationLayer.LayerOptions(project.transformContext()))
    notes.setCrs(layer.crs())
    notes.setCustomProperty(LABELS_PROPERTY, layer.id())
    notes.setCustomProperty(FIDS_PROPERTY, ",".join(str(fid) for fid in fids))
    text_format = settings.format()
    for words, point in points:
        item = QgsAnnotationPointTextItem(words, point)
        item.setFormat(text_format)
        notes.addItem(item)
    height = float_height(layer, fids)
    floating = QgsAnnotationLayer3DRenderer()
    floating.setAltitudeClamping(Qgis.AltitudeClamping.Relative)
    floating.setZOffset(height)



    floating.setShowCalloutLines(False)
    floating.setTextFormat(text_format)
    notes.setRenderer3D(floating)
    project.addMapLayer(notes)
    layer.setLabelsEnabled(False)
    layer.triggerRepaint()
    return {"layer": name, "labels": len(points), "height_above_ground_m": height,
            "layer_labels": "off: the annotation layer draws each name in 2D and in 3D"}
