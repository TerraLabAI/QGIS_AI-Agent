# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Print layout authoring tools."""







import glob
import os

from qgis.core import (
    QgsApplication,
    QgsLayoutItem,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemPage,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutMeasurementConverter,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsPrintLayout,
    QgsProject,
    QgsRectangle,
    QgsUnitTypes,
)
from qgis.PyQt.QtGui import QFont
from qgis.utils import iface

from ..core.qt_compat import enum_member
from ..core.tool_registry import Tool, ToolRegistry

_PAGE_SIZES = ["A4", "A3", "A2", "A1", "A0", "Letter"]


_KNOWN_SIZES_MM = {
    "A4": (210, 297),
    "A3": (297, 420),
    "A2": (420, 594),
    "A1": (594, 841),
    "A0": (841, 1189),
    "Letter": (216, 279),
}


def register_layout_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="create_print_layout",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "page_size": {"type": "string", "enum": _PAGE_SIZES},
                "orientation": {
                    "type": "string",
                    "enum": ["portrait", "landscape"],
                },
                "set_page": {
                    "type": "boolean",
                },
            },
            "required": ["name"],
        },
        handler=_create_print_layout,
    ))

    registry.register(Tool(
        name="add_layout_map",
        input_schema={
            "type": "object",
            "properties": {
                "layout_name": {"type": "string"},
                "x": {"type": "number", "minimum": -10000, "maximum": 10000},
                "y": {"type": "number", "minimum": -10000, "maximum": 10000},
                "width": {"type": "number", "minimum": 0.001, "maximum": 10000},
                "height": {"type": "number", "minimum": 0.001, "maximum": 10000},
                "extent": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 4,
                    "maxItems": 4,
                },
                "frame": {"type": "boolean"},
                "overview_of": {
                    "type": "string",
                },
            },
            "required": ["layout_name", "x", "y", "width", "height"],
        },
        handler=_add_layout_map,
    ))

    registry.register(Tool(
        name="add_layout_label",
        input_schema={
            "type": "object",
            "properties": {
                "layout_name": {"type": "string"},
                "text": {"type": "string"},
                "x": {"type": "number", "minimum": -10000, "maximum": 10000},
                "y": {"type": "number", "minimum": -10000, "maximum": 10000},
                "width": {"type": "number", "minimum": 0.001, "maximum": 10000},
                "height": {"type": "number", "minimum": 0.001, "maximum": 10000},
                "font_size": {"type": "number", "minimum": 0.1, "maximum": 1000},
                "bold": {"type": "boolean"},
                "halign": {
                    "type": "string",
                    "enum": ["left", "center", "right"],
                },
            },
            "required": ["layout_name", "text", "x", "y", "width", "height"],
        },
        handler=_add_layout_label,
    ))

    registry.register(Tool(
        name="add_layout_legend",
        input_schema={
            "type": "object",
            "properties": {
                "layout_name": {"type": "string"},
                "x": {"type": "number", "minimum": -10000, "maximum": 10000},
                "y": {"type": "number", "minimum": -10000, "maximum": 10000},
                "width": {"type": "number", "minimum": 0.001, "maximum": 10000},
                "height": {"type": "number", "minimum": 0.001, "maximum": 10000},
                "title": {"type": "string"},
                "linked_to_map": {
                    "type": "boolean",
                },
                "continuous_ramp": {
                    "type": "boolean",
                },
            },
            "required": ["layout_name", "x", "y"],
        },
        handler=_add_layout_legend,
    ))

    registry.register(Tool(
        name="add_layout_scalebar",
        input_schema={
            "type": "object",
            "properties": {
                "layout_name": {"type": "string"},
                "x": {"type": "number", "minimum": -10000, "maximum": 10000},
                "y": {"type": "number", "minimum": -10000, "maximum": 10000},
                "style": {
                    "type": "string",
                },
                "units_label": {"type": "string"},
                "segments": {"type": "integer", "minimum": 1, "maximum": 1000},
            },
            "required": ["layout_name", "x", "y"],
        },
        handler=_add_layout_scalebar,
    ))

    registry.register(Tool(
        name="add_layout_north_arrow",
        input_schema={
            "type": "object",
            "properties": {
                "layout_name": {"type": "string"},
                "x": {"type": "number", "minimum": -10000, "maximum": 10000},
                "y": {"type": "number", "minimum": -10000, "maximum": 10000},
                "width": {"type": "number", "minimum": 0.001, "maximum": 10000},
                "height": {"type": "number", "minimum": 0.001, "maximum": 10000},
            },
            "required": ["layout_name", "x", "y"],
        },
        handler=_add_layout_north_arrow,
    ))

    registry.register(Tool(
        name="get_layout_info",
        input_schema={
            "type": "object",
            "properties": {"layout_name": {"type": "string"}},
            "required": ["layout_name"],
        },
        handler=_get_layout_info,
    ))

    registry.register(Tool(
        name="remove_print_layout",
        input_schema={
            "type": "object",
            "properties": {"layout_name": {"type": "string"}},
            "required": ["layout_name"],
        },
        handler=_remove_print_layout,
        destructive=True,
    ))






def _resolve_layout(name: str):
    """Return (layout, None) or (None, error_dict)."""
    manager = QgsProject.instance().layoutManager()
    layout = manager.layoutByName(name)
    if layout:
        return layout, None
    existing = [lyt.name() for lyt in manager.layouts()]
    return None, {"_error": f"Layout not found: {name}. Existing layouts: {existing}"}


def _mm_point(x, y):
    return QgsLayoutPoint(x, y, enum_member(QgsUnitTypes, "LayoutUnit", "LayoutMillimeters"))


def _mm_size(w, h):
    return QgsLayoutSize(w, h, enum_member(QgsUnitTypes, "LayoutUnit", "LayoutMillimeters"))


def _distance_unit(member: str):
    """``QgsUnitTypes.DistanceUnit.Kilometers``, or the flat ``DistanceKilometers``."""
    scope = getattr(QgsUnitTypes, "DistanceUnit", None)
    if scope is not None:
        found = getattr(scope, member, None)
        if found is not None:
            return found
    return getattr(QgsUnitTypes, "Distance" + member, None)






_SCALEBAR_UNITS = {
    "m": "Meters", "meter": "Meters", "metre": "Meters", "metres": "Meters",
    "meters": "Meters",
    "km": "Kilometers", "kilometer": "Kilometers", "kilometers": "Kilometers",
    "kilometre": "Kilometers", "kilometres": "Kilometers",
    "cm": "Centimeters", "centimeter": "Centimeters", "centimeters": "Centimeters",
    "centimetre": "Centimeters", "centimetres": "Centimeters",
    "mm": "Millimeters", "millimeter": "Millimeters", "millimeters": "Millimeters",
    "millimetre": "Millimeters", "millimetres": "Millimeters",
    "ft": "Feet", "foot": "Feet", "feet": "Feet", "pied": "Feet", "pieds": "Feet",
    "yd": "Yards", "yard": "Yards", "yards": "Yards",
    "mi": "Miles", "mile": "Miles", "miles": "Miles",
    "nm": "NauticalMiles", "nauticalmile": "NauticalMiles",
    "nauticalmiles": "NauticalMiles", "millemarin": "NauticalMiles",
    "millesmarins": "NauticalMiles",
    "deg": "Degrees", "degree": "Degrees", "degrees": "Degrees",
    "degre": "Degrees", "degres": "Degrees",
}

_UNIT_ACCENTS = str.maketrans("àâäéèêëîïôöùûüç", "aaaeeeeiioouuuc")


def _norm_unit(label: str) -> str:
    return "".join(ch for ch in label.strip().lower().translate(_UNIT_ACCENTS) if ch.isalnum())


def _scalebar_unit(label: str):
    """The distance unit ``label`` names, or None when it is only free text."""
    member = _SCALEBAR_UNITS.get(_norm_unit(label))
    return _distance_unit(member) if member else None


def _first_map_item(layout):
    for item in layout.items():
        if isinstance(item, QgsLayoutItemMap):
            return item
    return None


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


def _item_summary(item) -> dict:
    rect = item.sceneBoundingRect()
    summary = {
        "type": type(item).__name__,
        "display_name": item.displayName() if hasattr(item, "displayName") else type(item).__name__,
        "uuid": item.uuid(),
        "x": round(rect.x(), 2),
        "y": round(rect.y(), 2),
        "width": round(rect.width(), 2),
        "height": round(rect.height(), 2),
    }
    if isinstance(item, QgsLayoutItemLabel):
        summary["text"] = item.text()
    return summary


def _layout_summary(layout) -> dict:
    items = [
        _item_summary(item)
        for item in layout.items()
        if isinstance(item, QgsLayoutItem) and not isinstance(item, QgsLayoutItemPage)
    ]
    page = _page_summary(layout)
    return {
        "name": layout.name(),
        "page_size": page["page_size"],
        "orientation": page["orientation"],
        "width_mm": page["width_mm"],
        "height_mm": page["height_mm"],
        "item_count": len(items),
        "items": items,
    }


def _with_new_item(layout, item) -> dict:
    summary = _layout_summary(layout)
    summary["new_item_uuid"] = item.uuid()
    summary["new_item_type"] = type(item).__name__
    return summary


def _find_north_arrow_svg():
    prefer = []
    pkg = QgsApplication.pkgDataPath()
    if pkg:
        prefer.append(os.path.join(pkg, "svg", "arrows", "NorthArrow_02.svg"))
    for d in QgsApplication.svgPaths():
        prefer.append(os.path.join(d, "arrows", "NorthArrow_02.svg"))
        prefer.append(os.path.join(d, "arrows", "NorthArrow_01.svg"))
    for p in prefer:
        if os.path.exists(p):
            return p

    search_dirs = list(QgsApplication.svgPaths())
    if pkg:
        search_dirs.append(os.path.join(pkg, "svg"))
    for d in search_dirs:
        for pat in ("arrows/*.svg", "**/NorthArrow*.svg", "**/arrows/*.svg"):
            hits = glob.glob(os.path.join(d, pat), recursive=True)
            if hits:
                return hits[0]
    return None






def _page_orientation(orientation: str):
    """The layout page orientation enum, named the way both Qt generations answer to."""
    wanted = "Landscape" if str(orientation or "").lower() == "landscape" else "Portrait"
    return enum_member(QgsLayoutItemPage, "Orientation", wanted)


def _create_print_layout(args: dict) -> dict:
    name = args["name"]
    page_size = args.get("page_size", "A4")
    orientation = args.get("orientation", "landscape")

    manager = QgsProject.instance().layoutManager()
    existing = manager.layoutByName(name)
    if existing and not args.get("set_page"):
        return {"_error": f"A layout named '{name}' already exists. Pass set_page=true to update its page."}

    if existing:
        page = existing.pageCollection().page(0)
        orientation = args.get("orientation", "landscape")
        page_orientation = _page_orientation(orientation)
        if not page.setPageSize(args.get("page_size", "A4"), page_orientation):
            return {"_error": f"Failed to set page size '{args.get('page_size', 'A4')}'."}
        return _layout_summary(existing)

    layout = QgsPrintLayout(QgsProject.instance())
    layout.initializeDefaults()
    layout.setName(name)

    page = layout.pageCollection().page(0)
    page_orientation = _page_orientation(orientation)
    if not page.setPageSize(page_size, page_orientation):
        return {"_error": f"Failed to set page size '{page_size}'. Use one of: {_PAGE_SIZES}"}

    manager.addLayout(layout)
    return _layout_summary(layout)


def _add_layout_map(args: dict) -> dict:
    layout, error = _resolve_layout(args["layout_name"])
    if error:
        return error

    map_item = QgsLayoutItemMap(layout)
    map_item.attemptMove(_mm_point(args["x"], args["y"]))
    map_item.attemptResize(_mm_size(args["width"], args["height"]))

    extent = args.get("extent")
    if extent:
        if len(extent) != 4:
            return {"_error": "extent must be [xmin, ymin, xmax, ymax]"}
        rect = QgsRectangle(extent[0], extent[1], extent[2], extent[3])
    else:
        rect = iface.mapCanvas().extent()



    map_item.zoomToExtent(rect)

    map_item.setFrameEnabled(bool(args.get("frame", True)))



    overview_of = args.get("overview_of")
    main_map = None
    if overview_of:
        main_map = next(
            (item for item in layout.items() if isinstance(item, QgsLayoutItemMap) and item.uuid() == overview_of),
            None,
        )
        if main_map is None:
            known = [item.uuid() for item in layout.items() if isinstance(item, QgsLayoutItemMap)]
            return {
                "_error": f"Map item not found for overview_of: {overview_of}",
                "code": "INVALID_ARGS",
                "suggestion": (f"Map items on this layout: {known}. Nothing was added."
                               if known else "This layout has no map yet; call add_layout_map without "
                                             "overview_of first. Nothing was added."),
            }
    layout.addLayoutItem(map_item)
    if main_map is not None:
        try:
            from qgis.core import QgsLayoutItemMapOverview
            overview = QgsLayoutItemMapOverview(map_item)
            overview.setLinkedMap(main_map)
            map_item.overviews().addOverview(overview)
        except Exception as exc:
            return {"_error": f"Could not add overview frame: {exc}"}
    return _with_new_item(layout, map_item)


def _add_layout_label(args: dict) -> dict:
    layout, error = _resolve_layout(args["layout_name"])
    if error:
        return error

    label = QgsLayoutItemLabel(layout)
    label.setText(args["text"])

    font = QFont()
    font.setPointSizeF(float(args.get("font_size", 14)))
    if args.get("bold"):
        font.setBold(True)
    label.setFont(font)

    halign = args.get("halign")
    if halign:
        from qgis.PyQt.QtCore import Qt
        flags = Qt.AlignmentFlag
        align_map = {"left": flags.AlignLeft, "center": flags.AlignHCenter, "right": flags.AlignRight}
        try:
            label.setHAlign(align_map.get(halign, flags.AlignLeft))
        except Exception:  # nosec B110 - layout styling is optional
            pass

    label.attemptMove(_mm_point(args["x"], args["y"]))
    label.attemptResize(_mm_size(args["width"], args["height"]))
    layout.addLayoutItem(label)
    return _with_new_item(layout, label)


def _add_layout_legend(args: dict) -> dict:
    layout, error = _resolve_layout(args["layout_name"])
    if error:
        return error

    legend = QgsLayoutItemLegend(layout)
    if args.get("title") is not None:
        legend.setTitle(args["title"])

    linked = args.get("linked_to_map", True)
    if linked:
        map_item = _first_map_item(layout)
        if map_item:
            legend.setLinkedMap(map_item)
    legend.setAutoUpdateModel(True)
    if args.get("continuous_ramp"):
        legend.setAutoUpdateModel(True)

    legend.attemptMove(_mm_point(args["x"], args["y"]))
    if args.get("width") is not None and args.get("height") is not None:
        legend.attemptResize(_mm_size(args["width"], args["height"]))
    layout.addLayoutItem(legend)
    return _with_new_item(layout, legend)


def _add_layout_scalebar(args: dict) -> dict:
    layout, error = _resolve_layout(args["layout_name"])
    if error:
        return error

    map_item = _first_map_item(layout)
    if not map_item:
        return {"_error": "Add a map item first (add_layout_map), a scale bar must be linked to a map."}

    scalebar = QgsLayoutItemScaleBar(layout)
    scalebar.setStyle(args.get("style", "Single Box"))
    scalebar.setLinkedMap(map_item)





    label = args.get("units_label")
    unit = _scalebar_unit(label) if label else None
    sized = False
    if unit is not None:
        try:
            scalebar.setUnits(unit)
            scalebar.applyDefaultSize(unit)
            sized = True
        except Exception:  # nosec B110 - fall back to the default sizing below
            pass
    if not sized:
        scalebar.applyDefaultSize()

    segments = args.get("segments", 4)
    try:
        scalebar.setNumberOfSegments(int(segments))
    except Exception:  # nosec B110 - layout styling is optional
        pass
    if label:
        try:
            scalebar.setUnitLabel(label)
        except Exception:  # nosec B110 - layout styling is optional
            pass

    scalebar.attemptMove(_mm_point(args["x"], args["y"]))
    layout.addLayoutItem(scalebar)
    result = _with_new_item(layout, scalebar)
    if label:
        result["units_label"] = label
        if unit is None:
            result["units_note"] = (
                f"{label!r} is not a distance unit I know, so it is printed as a free label "
                "and the bar keeps map units. Use m, km, ft, yd, mi or nm to size the bar."
            )
    return result


def _add_layout_north_arrow(args: dict) -> dict:
    layout, error = _resolve_layout(args["layout_name"])
    if error:
        return error

    svg_path = _find_north_arrow_svg()
    if not svg_path:
        return {"_error": "Could not find a bundled north-arrow SVG in the QGIS svg paths."}

    picture = QgsLayoutItemPicture(layout)
    picture.setPicturePath(svg_path)
    picture.attemptMove(_mm_point(args["x"], args["y"]))
    picture.attemptResize(_mm_size(args.get("width", 15), args.get("height", 15)))

    map_item = _first_map_item(layout)
    if map_item:
        try:
            picture.setLinkedMap(map_item)
            picture.setNorthMode(enum_member(QgsLayoutItemPicture, "NorthMode", "GridNorth"))
        except Exception:  # nosec B110 - layout styling is optional
            pass

    layout.addLayoutItem(picture)
    result = _with_new_item(layout, picture)
    result["svg_path"] = svg_path
    return result


def _get_layout_info(args: dict) -> dict:
    layout, error = _resolve_layout(args["layout_name"])
    if error:
        return error
    return _layout_summary(layout)


def _remove_print_layout(args: dict) -> dict:
    layout, error = _resolve_layout(args["layout_name"])
    if error:
        return error
    name = layout.name()
    QgsProject.instance().layoutManager().removeLayout(layout)
    return {"removed": name}
