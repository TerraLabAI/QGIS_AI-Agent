# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





from __future__ import annotations

import math

from qgis.core import QgsLayoutMeasurementConverter, QgsRectangle, QgsUnitTypes

from ..core import licence
from ..core.numbers import as_float
from ..core.qt_compat import enum_member
from ..core.tool_registry import tool_error


_KNOWN_SIZES_MM = {
    "A4": (210, 297),
    "A3": (297, 420),
    "A2": (420, 594),
    "A1": (594, 841),
    "A0": (841, 1189),
    "Letter": (216, 279),
}


def _page_summary(layout) -> dict:
    page = layout.pageCollection().page(0)
    try:
        conv = QgsLayoutMeasurementConverter()
        size_mm = conv.convert(page.pageSize(), enum_member(QgsUnitTypes, "LayoutUnit", "LayoutMillimeters"))
        w, h = round(size_mm.width(), 1), round(size_mm.height(), 1)
    except Exception:
        w = h = None

    orientation = None
    page_name = None
    if w is not None and h is not None:
        orientation = "landscape" if w > h else "portrait"
        lo, hi = sorted((w, h))
        for name, (pw, ph) in _KNOWN_SIZES_MM.items():
            if abs(lo - pw) <= 2 and abs(hi - ph) <= 2:
                page_name = name
                break
    return {"page_size": page_name, "orientation": orientation, "width_mm": w, "height_mm": h}


def _page_text(layout) -> str:

    try:
        page = _page_summary(layout)
        name = page["page_size"] or "custom"
        return f"{name} {page['orientation']}, {page['width_mm']:g} x {page['height_mm']:g} mm"
    except Exception:  # noqa: BLE001
        return ""


def _metres_per_map_unit(crs) -> float | None:

    try:
        from qgis.core import Qgis, QgsUnitTypes

        metres = enum_member(Qgis, "DistanceUnit", "Meters", None)
        degrees = enum_member(Qgis, "DistanceUnit", "Degrees", None)
        if metres is None:
            metres = enum_member(QgsUnitTypes, "DistanceUnit", "DistanceMeters")
            degrees = enum_member(QgsUnitTypes, "DistanceUnit", "DistanceDegrees")
        units = crs.mapUnits()
        if units == degrees:
            return None
        factor = float(QgsUnitTypes.fromUnitToUnitFactor(units, metres))
    except Exception:  # noqa: BLE001
        return None
    return factor if factor > 0 else None


def _ground_text(reference_map, rect) -> str:

    factor = _metres_per_map_unit(reference_map.crs())
    if factor is not None:
        return f"{rect.width() * factor / 1000:.1f} x {rect.height() * factor / 1000:.1f} km"
    return f"{rect.width():.4f} x {rect.height():.4f} degrees"


def _apply_scale(reference_map, wanted, layout_name: str):




    wanted = as_float(wanted, 0.0)
    if not math.isfinite(wanted) or wanted < 1:
        return tool_error("scale must be a number of 1 or more, the denominator of 1:scale.",
                          "INVALID_ARGS", hint="layout_scale_invalid")
    if reference_map is None:
        return tool_error(f"Layout '{layout_name}' has no map item, so it has no scale to set.",
                          "INVALID_ARGS", hint="layout_no_map")
    before = QgsRectangle(reference_map.extent())
    reference_map.setScale(wanted)
    got = reference_map.scale()
    if not got > 0 or abs(got - wanted) > wanted * 0.001:
        reference_map.zoomToExtent(before)
        return tool_error(f"The map could not be set to 1:{wanted:,.0f}; it reads 1:{got:,.0f}.",
                          "EXECUTION_FAILED", hint="layout_scale_not_applied")
    after = reference_map.extent()
    if after.width() < before.width() * 0.999 or after.height() < before.height() * 0.999:
        return (f"At 1:{wanted:,.0f} the map frame shows {_ground_text(reference_map, after)} around "
                f"the same centre, less than the {_ground_text(reference_map, before)} it showed; a "
                "larger page or map frame, or a larger scale number, shows more.")
    return ""


def _map_layers(item) -> list:

    from qgis.core import QgsProject

    try:
        return list(item.layersToRender())
    except (RuntimeError, AttributeError, TypeError):
        pass
    project = QgsProject.instance()
    try:
        if item.followVisibilityPreset():
            name = item.followVisibilityPresetName()
            themes = project.mapThemeCollection()
            if themes.hasMapTheme(name):
                return list(themes.mapThemeVisibleLayers(name))
        if item.keepLayerSet():
            return list(item.layers())
    except (RuntimeError, AttributeError):
        pass
    return list(project.layerTreeRoot().checkedLayers())


def _credit_layers(layout) -> list:

    from qgis.core import QgsLayoutItemMap

    layers = []
    seen = set()
    for item in layout.items():
        if not isinstance(item, QgsLayoutItemMap):
            continue
        for layer in _map_layers(item):
            try:
                layer_id = layer.id()
            except (RuntimeError, AttributeError):
                continue
            if layer_id not in seen:
                seen.add(layer_id)
                layers.append(layer)
    return layers


def _credit_lines(layout) -> list:

    credits = []
    for layer in _credit_layers(layout):
        try:
            text = licence.credit_line(layer)
        except (RuntimeError, AttributeError):
            continue
        if text and text not in credits:
            credits.append(text)
    return credits


def _layout_credits(layout) -> list:






    from qgis.core import QgsLayoutItemLabel

    shown = [str(item.text() or "") for item in layout.items() if isinstance(item, QgsLayoutItemLabel)]
    return [credit for credit in _credit_lines(layout) if not any(credit in label for label in shown)]
