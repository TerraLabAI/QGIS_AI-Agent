# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



















from __future__ import annotations

import contextlib

from qgis.core import QgsVectorLayer

from ..core.qt_compat import enum_member
from .colour_text import qcolor_from_text


_PALETTE = ("#66c2a5", "#fc8d62", "#8da0cb", "#e78ac3", "#a6d854", "#ffd92f",
            "#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02")
_KINDS = ("pie", "bar", "stacked_bar")

SCAN = 200_000


def _quoted(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _category_colors(layer, fields: list) -> dict:

    renderer = layer.renderer()
    if renderer is None or renderer.type() != "categorizedSymbol":
        return {}
    by_name: dict = {}
    for category in renderer.categories():
        symbol = category.symbol()
        if symbol is None:
            continue
        for key in (category.value(), category.label()):
            text = str(key or "").strip().casefold()
            if text and text not in by_name:
                by_name[text] = symbol.color()
    found = {}
    for field in fields:
        index = layer.fields().indexOf(field)
        alias = layer.attributeAlias(index) if index >= 0 else ""
        for key in (field, alias):
            color = by_name.get(str(key or "").strip().casefold())
            if color is not None:
                found[field] = color
                break
    return found


def diagram_plan(layer, args: dict) -> dict:






    if not isinstance(layer, QgsVectorLayer) or not layer.isSpatial():
        return {"_error": "Diagram renderers require a spatial vector layer.", "code": "INVALID_ARGS"}
    fields = args.get("fields") or args.get("diagram_fields")
    numeric_names = [f.name() for f in layer.fields() if f.isNumeric()]
    if not isinstance(fields, list) or not fields or len(fields) > 12:
        return {"_error": "fields must contain 1 to 12 numeric field names for a diagram.", "code": "INVALID_ARGS",
                "suggestion": ("fields: the numeric fields to draw, one slice or bar each. Numeric fields here: "
                               + (", ".join(numeric_names[:20]) or "none") + ".")}
    fields = [str(f) for f in fields]
    names = {f.name(): f for f in layer.fields()}
    missing = [f for f in fields if f not in names]
    if missing:
        return {"_error": f"Unknown diagram field: {missing[0]}", "code": "INVALID_ARGS",
                "suggestion": "Numeric fields here: " + (", ".join(numeric_names[:20]) or "none") + "."}
    non_numeric = [f for f in fields if not names[f].isNumeric()]
    if non_numeric:
        return {"_error": f"Diagram fields must be numeric: {', '.join(non_numeric)}", "code": "INVALID_ARGS",
                "suggestion": ("A text field with numbers in it needs a numeric copy first (add_field with an "
                               'expression such as to_real("field")). Numeric fields here: '
                               + (", ".join(numeric_names[:20]) or "none") + ".")}
    kind = str(args.get("diagram_type") or "").lower()
    if kind not in _KINDS:
        style = str(args.get("style_type") or "").lower()
        kind = style if style in _KINDS else "pie"
    explicit = [qcolor_from_text(str(c)) for c in (args.get("colors") or [])]
    if any(not c.isValid() for c in explicit):
        return {"_error": "colors must be colour names or hex values such as '#1b9e77'.", "code": "INVALID_ARGS"}
    total = " + ".join(f"coalesce({_quoted(f)}, 0)" for f in fields)


    measure = total if kind != "bar" else (
        f"max({', '.join(f'coalesce({_quoted(f)}, 0)' for f in fields)})" if len(fields) > 1
        else f"coalesce({_quoted(fields[0])}, 0)")
    return {"fields": fields, "kind": kind, "explicit": explicit, "measure": measure,
            "scaled": str(args.get("diagram_scale") or "total").lower() != "fixed"}


def set_diagram_renderer(layer, args: dict, plan: dict, largest: float, kept_style: str, previous_keys) -> dict:


    from qgis.core import (
        QgsDiagramLayerSettings,
        QgsDiagramSettings,
        QgsHistogramDiagram,
        QgsLinearlyInterpolatedDiagramRenderer,
        QgsPieDiagram,
        QgsSingleCategoryDiagramRenderer,
        QgsWkbTypes,
    )
    from qgis.PyQt.QtCore import QSizeF
    from qgis.PyQt.QtGui import QColor

    fields, kind, explicit, measure, scaled = (plan["fields"], plan["kind"], plan["explicit"], plan["measure"],
                                               plan["scaled"])
    matched = _category_colors(layer, fields) if not explicit else {}
    colors, colors_from = [], "colors" if explicit else ("categorized style" if matched else "default palette")
    for i, field in enumerate(fields):
        if explicit:
            colors.append(explicit[i % len(explicit)])
        else:
            colors.append(matched.get(field) or QColor(_PALETTE[i % len(_PALETTE)]))
    try:
        size = float(args.get("diagram_size") or 12)
    except (TypeError, ValueError):
        size = 12.0
    if not scaled:
        largest = 0.0
    try:
        settings = QgsDiagramSettings()
        settings.categoryAttributes = [_quoted(f) for f in fields]
        settings.categoryColors = colors
        settings.categoryLabels = [layer.attributeAlias(layer.fields().indexOf(f)) or f for f in fields]
        settings.size = QSizeF(size, size)
        settings.scaleBasedVisibility = False
        settings.enabled = True
        with contextlib.suppress(Exception):
            settings.penColor = QColor("#ffffff")
            settings.penWidth = 0.2
        if kind == "pie":
            diagram = QgsPieDiagram()
            settings.scaleByArea = True
        elif kind == "stacked_bar":
            from qgis.core import QgsStackedBarDiagram

            diagram = QgsStackedBarDiagram()
            settings.barWidth = max(2.0, size / 4.0)
        else:
            diagram = QgsHistogramDiagram()
            settings.barWidth = max(1.5, size / (2.0 * len(fields)))
        if scaled and largest > 0:
            renderer = QgsLinearlyInterpolatedDiagramRenderer()
            renderer.setClassificationAttributeExpression(measure)
            with contextlib.suppress(Exception):
                renderer.setClassificationAttributeIsExpression(True)
            renderer.setUpperValue(largest)
            renderer.setUpperSize(QSizeF(size, size))
            renderer.setLowerValue(0.0)
            renderer.setLowerSize(QSizeF(0.0, 0.0))
            with contextlib.suppress(Exception):
                from qgis.core import QgsDataDefinedSizeLegend

                legend = QgsDataDefinedSizeLegend()
                collapsed = enum_member(QgsDataDefinedSizeLegend, "LegendType", "LegendCollapsed", None)
                if collapsed is not None:
                    legend.setLegendType(collapsed)
                renderer.setDataDefinedSizeLegend(legend)
        else:
            renderer = QgsSingleCategoryDiagramRenderer()
        renderer.setDiagram(diagram)
        renderer.setDiagramSettings(settings)
        with contextlib.suppress(Exception):
            renderer.setAttributeLegend(True)
        placement = QgsDiagramLayerSettings()
        if layer.geometryType() == enum_member(QgsWkbTypes, "GeometryType", "PolygonGeometry"):
            placement.setPlacement(enum_member(QgsDiagramLayerSettings, "Placement", "OverPoint"))
        placement.setShowAllDiagrams(True)
        layer.setDiagramLayerSettings(placement)
        layer.setDiagramRenderer(renderer)
        layer.triggerRepaint()
    except Exception as exc:  # noqa: BLE001
        return {"_error": f"QGIS diagram renderer is unavailable: {exc}", "code": "EXECUTION_FAILED"}
    out = {"styled": layer.name(), "type": "diagram", "diagram_type": kind, "fields": fields,
           "colors": [c.name() for c in colors], "colors_from": colors_from, "native": "QgsDiagramRenderer",
           "size_mm": size, "legend": "one entry per field in the layer's legend",
           "note": "the layer keeps its own fill and outline; the chart is drawn over each feature"}
    if scaled and largest > 0:
        out["size_mode"] = ("area follows each feature's total" if kind == "pie" else
                            "length follows each value" if kind == "bar" else "height follows each feature's total")
        out["scale_max_value"] = largest
    else:
        out["size_mode"] = "fixed"
    out.update(previous_keys(layer, kept_style))
    return out
