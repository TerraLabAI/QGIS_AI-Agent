# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Print layout authoring tools."""







import glob
import os

from qgis.core import (
    QgsApplication,
    QgsCoordinateTransform,
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
                "scale": {"type": "number", "minimum": 1, "maximum": 1000000000},
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
                "layers": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "hide_layers": {
                    "type": "array",
                    "items": {"type": "string"},
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
    """The map a legend, a scale bar or a north arrow should follow."""








    try:
        reference = layout.referenceMap()
    except (AttributeError, RuntimeError):
        reference = None
    if isinstance(reference, QgsLayoutItemMap):
        return reference
    maps = [item for item in layout.items() if isinstance(item, QgsLayoutItemMap)]
    if not maps:
        return None
    return max(maps, key=lambda item: item.sceneBoundingRect().width() * item.sceneBoundingRect().height())


def _page_scale(layout) -> float:
    """How much bigger this page is than A4, on the diagonal. 1.0 when unreadable."""
    try:
        page = _page_summary(layout)
        width, height = float(page["width_mm"] or 0), float(page["height_mm"] or 0)
        if width <= 0 or height <= 0:
            return 1.0
        return max(0.7, min(2.2, ((width * height) / (210.0 * 297.0)) ** 0.5))
    except Exception:  # noqa: BLE001 - a page that will not measure keeps A4 sizes
        return 1.0


def _legend_font(legend, member: str, size: float, bold: bool) -> None:
    """Set one component of a legend's text, on both QGIS font APIs."""
    from qgis.core import QgsLegendStyle
    from qgis.PyQt.QtGui import QFont

    component = enum_member(QgsLegendStyle, "Style", member, None)
    if component is None:
        return
    font = QFont()
    font.setPointSizeF(size)
    font.setBold(bold)
    if hasattr(legend, "setStyleFont"):
        legend.setStyleFont(component, font)
        return
    style = legend.rstyle(component)
    text_format = style.textFormat()
    text_format.setFont(font)
    text_format.setSize(size)
    style.setTextFormat(text_format)


def _legend_entries(legend) -> list:
    """What the legend prints, layer by layer: ``[{"layer": name, "classes": [...]}]``."""





    entries = []
    try:
        model = legend.model()
        for node in model.rootGroup().findLayers():
            layer = node.layer()
            name = (layer.name() if layer is not None else node.name()) or ""
            classes = []
            for legend_node in model.layerLegendNodes(node):
                try:
                    from qgis.PyQt.QtCore import Qt

                    text = legend_node.data(enum_member(Qt, "ItemDataRole", "DisplayRole"))
                except Exception:  # noqa: BLE001 - a node with no label prints none
                    text = None
                if text:
                    classes.append(str(text))
            entries.append({"layer": name, "classes": classes[:40]})
    except Exception:  # noqa: BLE001 - a legend we cannot read still exported fine
        return []
    return entries


def _legend_pick_layers(legend, keep, drop) -> list:
    """Keep only *keep* and remove *drop* from the legend; the names taken out."""





    keep_keys = {str(name).strip().casefold() for name in keep or () if str(name).strip()}
    drop_keys = {str(name).strip().casefold() for name in drop or () if str(name).strip()}
    if not keep_keys and not drop_keys:
        return []
    removed = []
    try:
        legend.setAutoUpdateModel(False)
        root = legend.model().rootGroup()
        for node in list(root.findLayers()):
            layer = node.layer()
            name = (layer.name() if layer is not None else node.name()) or ""
            key = name.strip().casefold()
            if (keep_keys and key not in keep_keys) or key in drop_keys:
                parent = node.parent() or root
                parent.removeChildNode(node)
                removed.append(name)
    except Exception:  # noqa: BLE001 - a legend that will not be edited keeps every layer
        return removed
    return removed


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


def _keep_item_inside_page(layout, item, force_content_size: bool = False) -> list[str]:
    """Keep an item on its containing page after QGIS determines its size."""






    warnings = []
    try:



        try:
            layout.refresh()
        except (AttributeError, RuntimeError):
            pass
        try:
            item.refresh()
        except (AttributeError, RuntimeError):
            pass
        item_rect = item.sceneBoundingRect()
        if force_content_size and isinstance(item, QgsLayoutItemLegend):



            try:
                from qgis.core import QgsLegendRenderer
                size = QgsLegendRenderer(item.model(), item.legendSettings()).minimumSize()
                if size.width() > 0 and size.height() > 0:
                    item.attemptResize(_mm_size(size.width(), size.height()))
                    item_rect = item.sceneBoundingRect()
            except (AttributeError, RuntimeError, TypeError):
                pass
        pages = layout.pageCollection()
        page = None
        for candidate in (pages.page(i) for i in range(pages.pageCount())):
            page_rect = candidate.mapRectToScene(candidate.rect())
            if page_rect.contains(item_rect.center()) or page_rect.intersects(item_rect):
                page = candidate
                break
        if page is None:
            page = pages.page(0)
        page_rect = page.mapRectToScene(page.rect())
        width, height = item_rect.width(), item_rect.height()
        page_width, page_height = page_rect.width(), page_rect.height()
        x, y = item_rect.x(), item_rect.y()

        if width > page_width:
            warnings.append(
                f"{type(item).__name__} is {width:.1f} mm wide but the containing page is "
                f"{page_width:.1f} mm wide; it was kept at that size and may clip."
            )
        else:
            x = min(max(x, page_rect.left()), page_rect.right() - width)

        if height > page_height:
            warnings.append(
                f"{type(item).__name__} is {height:.1f} mm high but the containing page is "
                f"{page_height:.1f} mm high; it was kept at that size and may clip."
            )
        else:
            y = min(max(y, page_rect.top()), page_rect.bottom() - height)

        if abs(x - item_rect.x()) > 0.001 or abs(y - item_rect.y()) > 0.001:
            item.attemptMove(_mm_point(x, y))
    except Exception as exc:  # nosec B110 - placement must not block layout creation
        warnings.append(f"Could not verify {type(item).__name__} placement on the page: {exc}")
    return warnings


def _item_geometry(item) -> dict:
    rect = item.sceneBoundingRect()
    return {"x": round(rect.x(), 2), "y": round(rect.y(), 2),
            "width": round(rect.width(), 2), "height": round(rect.height(), 2)}


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
        canvas = iface.mapCanvas()
        rect = canvas.extent()




        settings = canvas.mapSettings() if hasattr(canvas, "mapSettings") else None
        canvas_crs = settings.destinationCrs() if settings is not None else None
        project = QgsProject.instance()
        project_crs = project.crs()
        if (canvas_crs is not None and canvas_crs.isValid() and project_crs.isValid()
                and canvas_crs != project_crs):
            try:
                rect = QgsCoordinateTransform(
                    canvas_crs, project_crs, project.transformContext()
                ).transformBoundingBox(rect)
            except Exception as exc:
                return {
                    "_error": f"Could not transform the canvas extent into {project_crs.authid()}: {exc}",
                    "code": "INVALID_EXTENT",
                }



    map_item.zoomToExtent(rect)

    scale_note = ""
    wanted_scale = args.get("scale")
    if wanted_scale is not None:
        from .advanced_tools import _apply_scale

        applied = _apply_scale(map_item, wanted_scale, args["layout_name"])
        if isinstance(applied, dict):
            return applied
        scale_note = applied

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
    result = _with_new_item(layout, map_item)
    if wanted_scale is not None:
        result["scale"] = int(round(map_item.scale()))
        if scale_note:
            result["scale_note"] = scale_note

    frame_note = _framing_note(map_item, rect, args["width"], args["height"])
    if frame_note:
        result["framing_note"] = frame_note
    shown = map_item.extent()
    result["extent_shown"] = [round(shown.xMinimum(), 6), round(shown.yMinimum(), 6),
                              round(shown.xMaximum(), 6), round(shown.yMaximum(), 6)]
    return result


def _framing_note(map_item, asked, width_mm, height_mm) -> str:
    """What to say when the frame shows much more ground than was asked for."""







    try:
        shown = map_item.extent()
        asked_w, asked_h = float(asked.width()), float(asked.height())
        if not (asked_w > 0 and asked_h > 0 and shown.width() > 0 and shown.height() > 0):
            return ""
        fill = (asked_w * asked_h) / (shown.width() * shown.height())
        if fill >= 0.72:
            return ""
        ratio = asked_w / asked_h
        fitted_h = float(width_mm) / ratio
        fitted_w = float(height_mm) * ratio
        better = (f"width {float(width_mm):.0f} by height {fitted_h:.0f} mm"
                  if fitted_h <= float(height_mm) * 1.6
                  else f"width {fitted_w:.0f} by height {float(height_mm):.0f} mm")
        return (f"The extent asked for fills only {fill * 100:.0f}% of this frame: the frame keeps its own "
                f"shape, so the rest is empty ground on two sides. A frame of {better} matches the extent. "
                "The map itself is correct; only the empty margin is not.")
    except Exception:  # noqa: BLE001 - a frame we cannot measure says nothing
        return ""


def _same_label(item, text: str, x, y) -> bool:
    """An existing label with this text within a millimetre of this spot."""
    if not isinstance(item, QgsLayoutItemLabel):
        return False
    if str(item.text() or "") != text:
        return False
    rect = item.sceneBoundingRect()
    return abs(rect.x() - float(x)) <= 1.0 and abs(rect.y() - float(y)) <= 1.0


def _add_layout_label(args: dict) -> dict:
    """Add a label, or move the one already saying this rather than stack a second on it."""






    layout, error = _resolve_layout(args["layout_name"])
    if error:
        return error

    text = args["text"]
    existing = next((item for item in layout.items() if _same_label(item, text, args["x"], args["y"])), None)
    if existing is not None:
        existing.attemptResize(_mm_size(args["width"], args["height"]))
        result = _with_new_item(layout, existing)
        result["reused_existing_label"] = True
        result["note"] = ("This label was already on the layout at this spot with this text, so it was "
                          "resized rather than drawn a second time over itself. The sheet is as you "
                          "wanted it; move on.")
        return result

    label = QgsLayoutItemLabel(layout)
    label.setText(text)

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
    """A legend a reader can actually use, on the first call."""






    layout, error = _resolve_layout(args["layout_name"])
    if error:
        return error

    legend = QgsLayoutItemLegend(layout)
    if args.get("title") is not None:
        legend.setTitle(args["title"])

    linked = args.get("linked_to_map", True)
    map_item = _first_map_item(layout) if linked else None
    if map_item:
        legend.setLinkedMap(map_item)



        try:
            legend.setLegendFilterByMapEnabled(True)
        except (AttributeError, RuntimeError):
            pass
    legend.setAutoUpdateModel(True)




    scale = _page_scale(layout)
    body = max(7.0, min(14.0, 8.5 * scale))
    for member, size, bold in (("Title", body * 1.3, True), ("Group", body * 1.05, True),
                               ("Subgroup", body, True), ("SymbolLabel", body, False)):
        try:
            _legend_font(legend, member, size, bold)
        except Exception:  # nosec B110 - legend styling is optional
            pass

    legend.attemptMove(_mm_point(args["x"], args["y"]))


    layout.addLayoutItem(legend)
    removed = _legend_pick_layers(legend, args.get("layers"), args.get("hide_layers"))
    try:
        legend.updateLegend()
    except (AttributeError, RuntimeError):
        pass

    sized = args.get("width") is not None and args.get("height") is not None
    if sized:
        legend.attemptResize(_mm_size(args["width"], args["height"]))
    else:


        try:
            legend.setResizeToContents(True)
            legend.adjustBoxSize()
        except (AttributeError, RuntimeError):
            pass
    entries = _legend_entries(legend)
    rows = sum(1 + len(entry["classes"]) for entry in entries)
    if rows > 14 and not sized:

        try:
            legend.setColumnCount(2 if rows <= 30 else 3)
            legend.adjustBoxSize()
        except (AttributeError, RuntimeError):
            pass

    placement_warnings = _keep_item_inside_page(layout, legend, force_content_size=not sized)
    result = _with_new_item(layout, legend)
    actual_geometry = _item_geometry(legend)
    result["actual_geometry"] = actual_geometry
    if abs(actual_geometry["x"] - float(args["x"])) > 0.01 or abs(actual_geometry["y"] - float(args["y"])) > 0.01:
        result["placement_adjustment"] = {
            "requested": {"x": float(args["x"]), "y": float(args["y"])},
            "actual": actual_geometry,
        }
    result["entries"] = entries
    result["linked_map"] = map_item.uuid() if map_item else None
    if placement_warnings:
        result["placement_warnings"] = placement_warnings
    if removed:
        result["removed_from_legend"] = removed
    if not entries:
        result["empty_note"] = ("The legend has no entries: the map it follows draws nothing, or every "
                                "layer is switched off. Check the map with get_layout_info first.")
    return result


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
    placement_warnings = _keep_item_inside_page(layout, scalebar)
    result = _with_new_item(layout, scalebar)
    actual_geometry = _item_geometry(scalebar)
    result["actual_geometry"] = actual_geometry
    if abs(actual_geometry["x"] - float(args["x"])) > 0.01 or abs(actual_geometry["y"] - float(args["y"])) > 0.01:
        result["placement_adjustment"] = {
            "requested": {"x": float(args["x"]), "y": float(args["y"])},
            "actual": actual_geometry,
        }
    if placement_warnings:
        result["placement_warnings"] = placement_warnings
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
    result = _layout_summary(layout)
    from ..core.layout_quality import assess_layout
    result["layout_checks"] = assess_layout(layout)
    return result


def _remove_print_layout(args: dict) -> dict:
    layout, error = _resolve_layout(args["layout_name"])
    if error:
        return error
    name = layout.name()
    QgsProject.instance().layoutManager().removeLayout(layout)
    return {"removed": name}
