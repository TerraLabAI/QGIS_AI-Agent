# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






































import glob
import math
import os

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDataDefinedSizeLegend,
    QgsGeometry,
    QgsLayoutItem,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemMapGrid,
    QgsLayoutItemPage,
    QgsLayoutItemPicture,
    QgsLayoutItemRegistry,
    QgsLayoutItemScaleBar,
    QgsLayoutItemShape,
    QgsLayoutMeasurementConverter,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsPrintLayout,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsStyle,
    QgsSymbol,
    QgsUnitTypes,
    QgsVectorLayer,
)

try:
    from qgis.core import Qgis
except ImportError:
    Qgis = None
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP
from qgis.PyQt.QtGui import QFont, QFontInfo
from qgis.utils import iface

from ..core.qt_compat import enum_member
from ..core.tool_registry import Tool, ToolRegistry
from ._layers import resolve_layer_note
from .colour_text import hex_from_qcolor, qcolor_from_text
from .layer_lookup import _layer_not_found_error

_PAGE_SIZES = ["A4", "A3", "A2", "A1", "A0", "Letter"]


_KNOWN_SIZES_MM = {
    "A4": (210, 297),
    "A3": (297, 420),
    "A2": (420, 594),
    "A1": (594, 841),
    "A0": (841, 1189),
    "Letter": (216, 279),
}


def _create_layout_label(args: dict) -> str:

    if str(args.get("template_path") or "").strip():
        return QT_TRANSLATE_NOOP("AIAgent", "Create the layout {name} from a template")
    return ""


def _legend_label(args: dict) -> str:

    if str(args.get("legend_id") or "").strip():
        return QT_TRANSLATE_NOOP("AIAgent", "Change the layout legend")
    return ""


def _edit_label(template: str):

    def label_for(args: dict) -> str:
        return template if str(args.get("item_id") or "").strip() else ""
    return label_for


def register_layout_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="create_print_layout",
        danger="write",
        label_for=_create_layout_label,
        builds_layout_at="name",
        label=QT_TRANSLATE_NOOP("AIAgent", "Create the layout {name}"),
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
                "layer": {"type": "string"},
                "extent": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 4,
                    "maxItems": 4,
                },
                "template_path": {"type": "string"},
            },
            "required": ["name"],
        },
        handler=_create_print_layout,
    ))

    registry.register(Tool(
        name="add_layout_map",
        danger="write",
        builds_layout_at="layout_name",
        label_for=_edit_label(QT_TRANSLATE_NOOP("AIAgent", "Change a map of the layout {layout_name}")),
        label=QT_TRANSLATE_NOOP("AIAgent", "Add a map to the layout"),
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
                "layer": {"type": "string"},
                "frame": {"type": "boolean"},
                "overview_of": {
                    "type": "string",
                },
                "lock_item": {"type": "boolean"},
                "lock_layers": {"type": "boolean"},
                "lock_style": {"type": "boolean"},
                "layers": {"type": "array", "items": {"type": "string"}},
                "scale": {"type": "number", "minimum": 1, "maximum": 1000000000},
                "map_theme": {"type": "string"},
                "crs": {"type": "string"},
                "item_id": {"type": "string"},
                "background_color": {"type": "string"},
            },
            "required": ["layout_name"],
        },
        handler=_add_layout_map,
    ))

    registry.register(Tool(
        name="lock_layout_item",
        danger="write",
        builds_layout_at="layout_name",
        label=QT_TRANSLATE_NOOP("AIAgent", "Lock a layout item"),
        input_schema={
            "type": "object",
            "properties": {
                "layout_name": {"type": "string"},
                "item_uuid": {"type": "string"},
                "lock_item": {"type": "boolean"},
                "lock_layers": {"type": "boolean"},
                "lock_style": {"type": "boolean"},
                "layers": {"type": "array", "items": {"type": "string"}},
                "map_theme": {"type": "string"},
            },
            "required": ["layout_name", "item_uuid"],
        },
        handler=_lock_layout_item,
    ))

    registry.register(Tool(
        name="add_layout_label",
        danger="write",
        builds_layout_at="layout_name",
        label_for=_edit_label(QT_TRANSLATE_NOOP("AIAgent", "Change a label of the layout {layout_name}")),
        label=QT_TRANSLATE_NOOP("AIAgent", "Add a label to the layout"),
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
                "item_id": {"type": "string"},
                "font_color": {"type": "string"},
                "font_family": {"type": "string"},
                "frame": {"type": "boolean"},
                "background_color": {"type": "string"},
            },
            "required": ["layout_name"],
        },
        handler=_add_layout_label,
    ))

    registry.register(Tool(
        name="add_layout_legend",
        danger="write",
        label_for=_legend_label,
        builds_layout_at="layout_name",
        label=QT_TRANSLATE_NOOP("AIAgent", "Add a legend to the layout"),
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
                "legend_mode": {
                    "type": "string",
                    "enum": ["automatic", "collapsed", "manual"],
                },
                "manual_classes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "size": {"type": "number", "exclusiveMinimum": 0},
                            "label": {"type": "string"},
                        },
                        "required": ["size", "label"],
                    },
                },
                "patch_shapes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "layer": {"type": "string"},
                            "class": {"type": "string"},
                            "shape": {"type": "string"},
                        },
                        "required": ["layer", "shape"],
                    },
                },
                "legend_id": {"type": "string"},
                "order": {"type": "array", "items": {"type": "string"}},
                "labels": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "layer": {"type": "string"},
                            "class": {"type": "string"},
                            "label": {"type": "string"},
                        },
                        "required": ["layer", "label"],
                    },
                },
                "columns": {"type": "integer", "minimum": 1, "maximum": 20},
                "split_layers": {"type": "boolean"},
                "hide_headers": {"type": "boolean"},
                "map_id": {"type": "string"},
                "frame": {"type": "boolean"},
                "background_color": {"type": "string"},
            },
            "required": ["layout_name"],
        },
        handler=_add_layout_legend,
    ))

    registry.register(Tool(
        name="add_layout_scalebar",
        danger="write",
        builds_layout_at="layout_name",
        label_for=_edit_label(QT_TRANSLATE_NOOP("AIAgent", "Change a scale bar of the layout {layout_name}")),
        label=QT_TRANSLATE_NOOP("AIAgent", "Add a scale bar to the layout"),
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
                "segments": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 2},
                "map_id": {"type": "string"},
                "min_segment_width": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000},
                "max_segment_width": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000},
                "item_id": {"type": "string"},
            },
            "required": ["layout_name"],
        },
        handler=_add_layout_scalebar,
    ))

    registry.register(Tool(
        name="add_layout_north_arrow",
        danger="write",
        builds_layout_at="layout_name",
        label_for=_edit_label(QT_TRANSLATE_NOOP("AIAgent", "Change a north arrow of the layout {layout_name}")),
        label=QT_TRANSLATE_NOOP("AIAgent", "Add a north arrow to the layout"),
        input_schema={
            "type": "object",
            "properties": {
                "layout_name": {"type": "string"},
                "x": {"type": "number", "minimum": -10000, "maximum": 10000},
                "y": {"type": "number", "minimum": -10000, "maximum": 10000},
                "width": {"type": "number", "minimum": 0.001, "maximum": 10000},
                "height": {"type": "number", "minimum": 0.001, "maximum": 10000},
                "map_id": {"type": "string"},
                "north": {"type": "string", "enum": ["true", "grid"]},
                "item_id": {"type": "string"},
            },
            "required": ["layout_name"],
        },
        handler=_add_layout_north_arrow,
    ))

    registry.register(Tool(
        name="add_layout_coordinate_grid",
        danger="write",
        builds_layout_at="layout_name",
        label=QT_TRANSLATE_NOOP("AIAgent", "Add a coordinate grid to the layout"),
        input_schema={
            "type": "object",
            "properties": {
                "layout_name": {"type": "string"},
                "map_id": {"type": "string"},
                "interval_x": {"type": "number", "exclusiveMinimum": 0},
                "interval_y": {"type": "number", "exclusiveMinimum": 0},
                "crs": {"type": "string"},
                "grid_style": {"type": "string", "enum": ["solid", "cross", "markers", "frame_annotations_only"]},
                "frame_style": {"type": "string", "enum": [
                    "no_frame", "zebra", "interior_ticks", "exterior_ticks", "interior_exterior_ticks",
                    "line_border", "line_border_nautical", "zebra_nautical"]},
                "frame_width": {"type": "number", "minimum": 0},
                "frame_sides": {"type": "array",
                                "items": {"type": "string", "enum": ["left", "right", "top", "bottom"]}},
                "annotations": {"type": "string", "enum": ["show_all", "latitude_only", "longitude_only", "hide_all"]},
                "annotation_position": {"type": "string", "enum": ["inside", "outside"]},
                "annotation_format": {"type": "string", "enum": [
                    "decimal", "degree_minute", "degree_minute_second", "decimal_suffix",
                    "degree_minute_no_suffix", "degree_minute_padded", "degree_minute_second_no_suffix",
                    "degree_minute_second_padded"]},
                "annotation_precision": {"type": "integer", "minimum": 0, "maximum": 12},
                "annotation_direction": {"type": "string", "enum": [
                    "horizontal", "vertical", "vertical_descending", "boundary", "above_tick", "on_tick",
                    "under_tick"]},
            },
            "required": ["layout_name", "interval_x", "interval_y"],
        },
        handler=_add_layout_coordinate_grid,
    ))

    registry.register(Tool(
        name="get_layout_info",
        danger="read",
        input_schema={
            "type": "object",
            "properties": {"layout_name": {"type": "string"}},
            "required": ["layout_name"],
        },
        handler=_get_layout_info,
    ))

    registry.register(Tool(
        name="remove_print_layout",
        danger="destructive",
        removes_layout_at="layout_name",
        label_for=_edit_label(QT_TRANSLATE_NOOP("AIAgent", "Remove one item from the layout {layout_name}")),
        input_schema={
            "type": "object",
            "properties": {"layout_name": {"type": "string"}, "item_id": {"type": "string"}},
            "required": ["layout_name"],
        },
        handler=_remove_print_layout,
    ))






def _resolve_layout(name: str, allow_report: bool = False):






    manager = QgsProject.instance().layoutManager()
    layout = manager.layoutByName(name)
    if layout and not allow_report and getattr(layout, "pageCollection", None) is None:
        return None, {"_error": f"'{name}' is a report, not a print layout: its pages are copies of print "
                                "layouts made when it was built.",
                      "code": "INVALID_ARGS",
                      "suggestion": "create_report with replace:true rebuilds it from the print layout its "
                                    "sections came from; get_layout_info reads it."}
    if layout:
        return layout, None
    existing = [lyt.name() for lyt in manager.layouts()]
    return None, {"_error": f"Layout not found: {name}. Existing layouts: {existing}"}


def _mm_point(x, y):
    return QgsLayoutPoint(x, y, enum_member(QgsUnitTypes, "LayoutUnit", "LayoutMillimeters"))


def _mm_size(w, h):
    return QgsLayoutSize(w, h, enum_member(QgsUnitTypes, "LayoutUnit", "LayoutMillimeters"))


def _distance_unit(member: str):

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

    member = _SCALEBAR_UNITS.get(_norm_unit(label))
    return _distance_unit(member) if member else None


def _first_map_item(layout):









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


def _map_for(layout, map_id):






    wanted = str(map_id or "").strip()
    if not wanted:
        return _first_map_item(layout), None
    bare = wanted.strip("{}").casefold()
    maps = [item for item in layout.items() if isinstance(item, QgsLayoutItemMap)]
    for item in maps:
        if item.uuid().strip("{}").casefold() == bare or (item.id() and item.id().casefold() == bare):
            return item, None
    return None, {"_error": f"No map {wanted!r} on layout {layout.name()!r}.", "code": "INVALID_ARGS",
                  "suggestion": (f"Map items there: {[item.uuid() for item in maps]}. Without map_id it "
                                 "links to the main map." if maps else "add_layout_map adds one.")}


def _is_tile_basemap(layer) -> bool:





    if layer is None:
        return False
    try:
        if type(layer).__name__ == "QgsVectorTileLayer":
            return True
        if (layer.providerType() or "").lower() != "wms":
            return False
        source = (layer.source() or "").lower()
    except (AttributeError, RuntimeError):
        return False
    return "type=xyz" in source or "tilematrixset=" in source


def _page_scale(layout) -> float:

    try:
        page = _page_summary(layout)
        width, height = float(page["width_mm"] or 0), float(page["height_mm"] or 0)
        if width <= 0 or height <= 0:
            return 1.0
        return max(0.7, min(2.2, ((width * height) / (210.0 * 297.0)) ** 0.5))
    except Exception:  # noqa: BLE001
        return 1.0


def _legend_font(legend, member: str, size: float, bold: bool) -> None:

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
                except Exception:  # noqa: BLE001
                    text = None
                if text:
                    classes.append(str(text))
            entry = {"layer": name, "classes": classes[:40]}
            printed = _legend_title(node)
            if printed != name:
                entry["label"] = printed
            if _heading_hidden(node):
                entry["heading_hidden"] = True
            entries.append(entry)
    except Exception:  # noqa: BLE001
        return []
    return entries






_TITLE_LABEL = "legend/title-label"


def _legend_title(node) -> str:

    layer = node.layer() if hasattr(node, "layer") else None
    if layer is None:
        return node.name() or ""
    label = node.customProperty(_TITLE_LABEL) if hasattr(node, "customProperty") else None
    return str(label) if label else (layer.name() or "")


def _hidden_style():
    from qgis.core import QgsLegendStyle

    return enum_member(QgsLegendStyle, "Style", "Hidden")


def _heading_hidden(node) -> bool:

    try:
        return str(node.customProperty("legend/title-style") or "") == "hidden"
    except (AttributeError, RuntimeError):
        return False


def _legend_manual(legend) -> None:







    mode = getattr(getattr(Qgis, "LegendSyncMode", None), "Manual", None) if Qgis is not None else None
    if mode is not None and hasattr(legend, "setSyncMode"):
        legend.setSyncMode(mode)
    else:
        legend.setAutoUpdateModel(False)


def _auto_updates(legend) -> bool:

    manual = getattr(getattr(Qgis, "LegendSyncMode", None), "Manual", None) if Qgis is not None else None
    try:
        if manual is not None and hasattr(legend, "syncMode"):
            return legend.syncMode() != manual
        return bool(legend.autoUpdateModel())
    except (AttributeError, RuntimeError):
        return False


def _node_keys(node) -> set:

    keys = {_legend_title(node).strip().casefold()}
    layer = node.layer() if hasattr(node, "layer") else None
    keys.add(((layer.name() if layer is not None else node.name()) or "").strip().casefold())
    keys.discard("")
    return keys


def _legend_nodes(root) -> list:

    from qgis.core import QgsLayerTreeGroup

    found = []
    for child in root.children():
        found.append(child)
        if isinstance(child, QgsLayerTreeGroup):
            found.extend(_legend_nodes(child))
    return found


def _legend_order(root, names) -> list:







    from qgis.core import QgsLayerTreeGroup

    rank = {}
    for index, name in enumerate(names or ()):
        rank.setdefault(str(name).strip().casefold(), index)
    rank.pop("", None)
    seen = set()

    def node_rank(node):
        hits = [rank[key] for key in _node_keys(node) if key in rank]
        seen.update(key for key in _node_keys(node) if key in rank)
        if isinstance(node, QgsLayerTreeGroup):
            hits.extend(r for r in (node_rank(child) for child in node.children()) if r is not None)
        return min(hits) if hits else None

    def walk(group):
        children = list(group.children())
        ranks = [node_rank(child) for child in children]
        ordered = sorted(range(len(children)),
                         key=lambda i: (ranks[i] is None, ranks[i] if ranks[i] is not None else 0, i))
        if ordered != list(range(len(children))):
            clones = [children[i].clone() for i in ordered]
            group.removeAllChildren()
            group.insertChildNodes(0, clones)
            children = clones
        for child in children:
            if isinstance(child, QgsLayerTreeGroup):
                walk(child)

    walk(root)
    return [name for name in names or () if str(name).strip().casefold() not in seen]


def _legend_labels(legend, specs) -> tuple:







    from qgis.core import QgsLayerTreeGroup, QgsLayerTreeLayer, QgsLegendRenderer, QgsMapLayerLegendUtils
    from qgis.PyQt.QtCore import Qt

    display = enum_member(Qt, "ItemDataRole", "DisplayRole")
    model = legend.model()
    applied, warnings = [], []
    for spec in specs or ():
        target = str(spec.get("layer") or "").strip()
        class_name = str(spec.get("class") or "").strip()
        label = str(spec.get("label") if spec.get("label") is not None else "")
        nodes = [node for node in _legend_nodes(model.rootGroup()) if target.casefold() in _node_keys(node)]
        if not nodes:
            warnings.append(f"{target!r} is not in the legend.")
            continue
        done = False
        for node in nodes:
            if not class_name:
                if label.strip() and isinstance(node, QgsLayerTreeGroup):


                    node.setName(label)
                    node.removeCustomProperty(_TITLE_LABEL)
                elif label.strip():
                    node.setCustomProperty(_TITLE_LABEL, label)
                else:
                    QgsLegendRenderer.setNodeLegendStyle(node, _hidden_style())
                if isinstance(node, QgsLayerTreeLayer):
                    model.refreshLayerLegend(node)
                done = True
                continue
            if not isinstance(node, QgsLayerTreeLayer):
                continue
            originals = list(model.layerOriginalLegendNodes(node))
            texts = [str(item.data(display) or "").strip().casefold() for item in originals]
            if class_name.casefold() not in texts:
                continue
            index = texts.index(class_name.casefold())
            if label.strip():
                QgsMapLayerLegendUtils.setLegendNodeUserLabel(node, index, label)
            else:
                order = list(QgsMapLayerLegendUtils.legendNodeOrder(node)) \
                    if QgsMapLayerLegendUtils.hasLegendNodeOrder(node) else list(range(len(originals)))
                QgsMapLayerLegendUtils.setLegendNodeOrder(node, [i for i in order if i != index])
            model.refreshLayerLegend(node)
            done = True
        if done:
            applied.append({key: spec.get(key) for key in ("layer", "class", "label") if spec.get(key) is not None})
        else:
            warnings.append(f"{target!r} has no legend class {class_name!r}.")
    return applied, warnings


def _hide_headings(legend) -> int:




    from qgis.core import QgsLayerTreeGroup, QgsLegendRenderer

    model = legend.model()
    hidden = 0
    for node in _legend_nodes(model.rootGroup()):
        if isinstance(node, QgsLayerTreeGroup) or len(list(model.layerLegendNodes(node))) > 1:
            QgsLegendRenderer.setNodeLegendStyle(node, _hidden_style())
            hidden += 1
    return hidden


def _curate_legend(legend, args: dict) -> dict:

    order, labels, hide = args.get("order"), args.get("labels"), args.get("hide_headers")
    if not (order or labels or hide):
        return {}
    _legend_manual(legend)
    out, warnings = {}, []


    if labels:
        applied, label_warnings = _legend_labels(legend, labels)
        out["labels"] = applied
        warnings.extend(label_warnings)
    if order:
        missing = _legend_order(legend.model().rootGroup(), order)
        out["order"] = [entry for entry in order if entry not in missing]
        warnings.extend(f"{name!r} is not in the legend, so it was not ordered." for name in missing)
    if hide:
        out["headings_hidden"] = _hide_headings(legend)
    if warnings:
        out["warnings"] = warnings
    return out


def _readable_name(name: str) -> tuple[str, bool]:





    text = " ".join(str(name or "").replace("_", " ").split())
    if not text:
        return "", False
    raw = text != name
    if text == text.lower():
        text = text[0].upper() + text[1:]
    return (text, raw) if text != name else ("", False)





_EMPTY_CLASS_MAX_FEATURES = 100_000
_EMPTY_CLASS_MAX_VALUES = 5_000


def _is_null(value) -> bool:
    return value is None or (hasattr(value, "isNull") and value.isNull())


def _empty_catch_all(layer) -> list:






    try:
        renderer = layer.renderer() if hasattr(layer, "renderer") else None
        if renderer is None or type(renderer).__name__ != "QgsCategorizedSymbolRenderer":
            return []
        index = layer.fields().indexFromName(renderer.classAttribute())
        count = layer.featureCount()
        if index < 0 or count < 0 or count > _EMPTY_CLASS_MAX_FEATURES:
            return []
        categories = list(renderer.categories())
        catch_all, named = [], set()
        for position, category in enumerate(categories):
            value = category.value()
            values = value if isinstance(value, list) else [value]
            if all(_is_null(v) or str(v) == "" for v in values):
                catch_all.append(position)
            else:
                named.update(str(v) for v in values if not _is_null(v))
        if not catch_all:
            return []
        present = list(layer.uniqueValues(index, _EMPTY_CLASS_MAX_VALUES + 1))
        if len(present) > _EMPTY_CLASS_MAX_VALUES:
            return []


        if any(_is_null(v) or str(v) not in named for v in present):
            return []
        return catch_all
    except Exception:  # noqa: BLE001
        return []


def _drop_empty_classes(legend, empty: dict) -> list:

    from qgis.core import QgsMapLayerLegendUtils
    from qgis.PyQt.QtCore import Qt

    display = enum_member(Qt, "ItemDataRole", "DisplayRole")
    model = legend.model()
    dropped = []
    for node in list(model.rootGroup().findLayers()):
        layer = node.layer()
        positions = empty.get(node.layerId())
        if not positions or layer is None:
            continue
        categories = list(layer.renderer().categories())
        originals = list(model.layerOriginalLegendNodes(node))
        if len(originals) < len(categories):
            continue
        texts = [str(item.data(display) or "") for item in originals]


        gone = [i for i in positions if texts[i] == str(categories[i].label() or "")]
        if not gone:
            continue
        order = list(QgsMapLayerLegendUtils.legendNodeOrder(node)) \
            if QgsMapLayerLegendUtils.hasLegendNodeOrder(node) else list(range(len(originals)))
        QgsMapLayerLegendUtils.setLegendNodeOrder(node, [i for i in order if i not in gone])
        model.refreshLayerLegend(node)
        dropped.extend({"layer": layer.name(), "class": texts[i] or "(empty)"} for i in gone)
    return dropped


def _band_heading(model, node) -> bool:






    layer = node.layer()
    try:
        if not isinstance(layer, QgsRasterLayer) or layer.bandCount() != 1:
            return False
        originals = list(model.layerOriginalLegendNodes(node))
        if len(originals) < 2 or type(originals[0]).__name__ != "QgsSimpleLegendNode":
            return False
        from qgis.PyQt.QtCore import Qt
        text = str(originals[0].data(enum_member(Qt, "ItemDataRole", "DisplayRole")) or "")
        return bool(layer.bandName(1)) and text.startswith(layer.bandName(1))
    except (AttributeError, RuntimeError):
        return False


def _round_ramp_labels(layer) -> bool:






    import math

    try:
        renderer = layer.renderer() if isinstance(layer, QgsRasterLayer) else None
        if renderer is None or not hasattr(renderer, "shader") or renderer.shader() is None:
            return False
        from qgis.core import QgsColorRampShader
        ramp = renderer.shader().rasterShaderFunction()
        if ramp.colorRampType() != enum_member(QgsColorRampShader, "Type", "Interpolated"):
            return False
        settings = ramp.legendSettings()
        number = settings.numericFormat() if settings is not None else None

        if number is None or number.id() != "basic" or number.numberDecimalPlaces() != 6:
            return False
        span = abs(ramp.maximumValue() - ramp.minimumValue())
        if not math.isfinite(span) or span <= 0:
            return False
        from qgis.core import QgsBasicNumericFormat, QgsColorRampLegendNodeSettings
        number = QgsBasicNumericFormat()
        number.setNumberDecimalPlaces(max(0, min(6, 2 - math.floor(math.log10(span)))))
        number.setShowTrailingZeros(False)
        rounded = QgsColorRampLegendNodeSettings(settings)
        rounded.setNumericFormat(number)
        ramp.setLegendSettings(rounded)
        return True
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def _default_curation(legend, args: dict) -> dict:











    model = legend.model()
    keep = {str(name).strip().casefold() for name in args.get("layers") or () if str(name).strip()}
    named = {str(spec.get("layer") or "").strip().casefold() for spec in args.get("labels") or ()
             if isinstance(spec, dict)}
    basemaps, renames, capitals = [], [], []
    for node in list(model.rootGroup().findLayers()):
        layer = node.layer()
        name = (layer.name() if layer is not None else node.name()) or ""
        if _is_tile_basemap(layer) and name.strip().casefold() not in keep:
            basemaps.append(node)
        elif name.strip().casefold() not in named and not node.customProperty(_TITLE_LABEL):
            readable, raw = _readable_name(name)
            if readable:
                (renames if raw else capitals).append((node, name, readable))
    empty = {}
    band_heads = set()
    rounded = []
    for node in model.rootGroup().findLayers():
        positions = _empty_catch_all(node.layer())
        if positions:
            empty[node.layerId()] = positions
        if _band_heading(model, node):
            band_heads.add(node.layerId())
        if _round_ramp_labels(node.layer()):
            rounded.append(node.layer().name())
            model.refreshLayerLegend(node)


    manual_anyway = not _auto_updates(legend) or any(args.get(key) for key in ("order", "labels", "hide_headers"))
    if not (basemaps or renames or empty or band_heads or (capitals and manual_anyway)):
        return {"ramp_labels_rounded": rounded} if rounded else {}
    renames += capitals
    _legend_manual(legend)

    root = legend.model().rootGroup()
    out = {}
    left_out = []
    base_ids = {node.layerId() for node in basemaps}
    for node in list(root.findLayers()):
        if node.layerId() in base_ids:
            left_out.append(node.name() or "")
            (node.parent() or root).removeChildNode(node)
    if left_out:
        out["basemaps_left_out"] = left_out
    renamed = {}
    rename_ids = {node.layerId(): (old, new) for node, old, new in renames}
    for node in root.findLayers():
        if node.layerId() in rename_ids:
            old, new = rename_ids[node.layerId()]
            node.setCustomProperty(_TITLE_LABEL, new)
            legend.model().refreshLayerLegend(node)
            renamed[old] = new
    if renamed:
        out["renamed"] = renamed
    dropped = _drop_empty_classes(legend, empty)
    if dropped:
        out["empty_classes_dropped"] = dropped
    from qgis.core import QgsMapLayerLegendUtils
    for node in root.findLayers():
        if node.layerId() in band_heads:
            count = len(list(legend.model().layerOriginalLegendNodes(node)))
            order = list(QgsMapLayerLegendUtils.legendNodeOrder(node)) \
                if QgsMapLayerLegendUtils.hasLegendNodeOrder(node) else list(range(count))
            QgsMapLayerLegendUtils.setLegendNodeOrder(node, [i for i in order if i != 0])
            legend.model().refreshLayerLegend(node)
            out.setdefault("band_name_rows_dropped", []).append(node.name() or "")
    if rounded:
        out["ramp_labels_rounded"] = rounded
    if out:
        out["note"] = ("Done on the legend only. layers naming a basemap keeps it; labels sets another name; "
                       "labels with class and an empty label drops a class.")
    return out


def _find_legend(layout, ref: str):

    wanted = str(ref or "").strip()
    bare = wanted.strip("{}").casefold()
    legends = [item for item in layout.items() if isinstance(item, QgsLayoutItemLegend)]
    for legend in legends:
        if legend.uuid().strip("{}").casefold() == bare or (legend.id() and legend.id().casefold() == bare):
            return legend, None
    titled = [legend for legend in legends if (legend.title() or "").strip().casefold() == wanted.casefold()]
    if len(titled) == 1:
        return titled[0], None
    listing = [{"uuid": legend.uuid(), "title": legend.title()} for legend in legends]
    return None, {"_error": f"No legend {wanted!r} on layout {layout.name()!r}. Legends there: {listing}",
                  "code": "INVALID_ARGS",
                  "suggestion": "legend_id takes one of those uuids; left out, a new legend is added."}


def _legend_pick_layers(legend, keep, drop) -> list:

    keep_keys = {str(name).strip().casefold() for name in keep or () if str(name).strip()}
    drop_keys = {str(name).strip().casefold() for name in drop or () if str(name).strip()}
    if not keep_keys and not drop_keys:
        return []
    removed = []
    try:
        _legend_manual(legend)
        root = legend.model().rootGroup()
        for node in list(root.findLayers()):
            layer = node.layer()
            name = (layer.name() if layer is not None else node.name()) or ""
            key = name.strip().casefold()
            if (keep_keys and key not in keep_keys) or key in drop_keys:
                parent = node.parent() or root
                parent.removeChildNode(node)
                removed.append(name)
    except Exception:  # noqa: BLE001
        return removed
    return removed


def _set_page(layout, page_size, orientation) -> bool:





    page = layout.pageCollection().page(0)
    if page_size:
        return bool(page.setPageSize(page_size, _page_orientation(orientation)))
    current = page.pageSize()
    width, height = current.width(), current.height()
    if (orientation == "portrait") == (width > height):
        width, height = height, width
    page.setPageSize(QgsLayoutSize(width, height, current.units()))
    return True


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
    if isinstance(item, QgsLayoutItemMap):
        try:
            if item.followVisibilityPreset():
                summary["map_theme"] = item.followVisibilityPresetName()
        except (AttributeError, RuntimeError):
            pass
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


def _editing(args: dict) -> bool:

    return bool(str(args.get("item_id") or "").strip())


def _missing(args: dict, keys, noun: str):

    missing = [key for key in keys if args.get(key) is None]
    if not missing:
        return None
    return {"_error": f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} needed to add a {noun}.",
            "code": "INVALID_ARGS",
            "suggestion": f"item_id changes a {noun} already on the layout; without it, a new one is added."}


def _find_item(layout, ref, kind, noun: str):




    wanted = str(ref or "").strip()
    bare = wanted.strip("{}").casefold()
    items = [item for item in layout.items()
             if isinstance(item, QgsLayoutItem) and not isinstance(item, QgsLayoutItemPage)]
    found = next((item for item in items if item.uuid().strip("{}").casefold() == bare
                  or (item.id() and item.id().casefold() == bare)), None)
    if found is not None and (kind is None or isinstance(found, kind)):
        return found, None
    same = [{"uuid": item.uuid(), "name": _item_name(item)} for item in items
            if kind is None or isinstance(item, kind)]
    if found is not None:
        message = f"{wanted} on layout {layout.name()!r} is {_item_name(found)}, not a {noun}."
    else:
        message = f"No {noun} {wanted!r} on layout {layout.name()!r}."
    return None, {"_error": f"{message} {noun.capitalize()} items there: {same}",
                  "code": "INVALID_ARGS",
                  "suggestion": "item_id takes one of those uuids; get_layout_info lists every item."}


def _edit_geometry(layout, item, args: dict, resizable: bool = True, inset: bool = False) -> dict:





    keys = ("x", "y", "width", "height") if resizable else ("x", "y")
    given = {key: float(args[key]) for key in keys if args.get(key) is not None}
    if not given:
        return {}
    position, size = item.positionWithUnits(), item.sizeWithUnits()
    if "width" in given or "height" in given:
        item.attemptResize(_mm_size(given.get("width", size.width()), given.get("height", size.height())))
    if "x" in given or "y" in given:
        item.attemptMove(_mm_point(given.get("x", position.x()), given.get("y", position.y())))
    return place_item(layout, item, given, resizable=resizable, inset=inset)


def _edited(layout, item, placement: dict, args: dict, read) -> dict:

    result = _layout_summary(layout)
    result["edited_item_uuid"] = item.uuid()
    result["actual_geometry"] = _item_geometry(item)
    result.update(placement)
    unused = [key for key, value in args.items()
              if key not in read and value is not None and value is not False and value != "" and value != []]
    if unused:
        result["not_applied"] = unused
        result["not_applied_note"] = ("An item changed in place takes these only when it is added: "
                                      "they changed nothing here.")
    return result


def _apply_box_style(item, args: dict, text: bool = False):





    colours = {}
    for key in ("background_color", "font_color") if text else ("background_color",):
        if args.get(key) is not None:
            colours[key] = qcolor_from_text(str(args[key]))
            if not colours[key].isValid():
                return None, {"_error": f"{key} {args[key]!r} is not a colour.", "code": "INVALID_ARGS",
                              "suggestion": "A colour is written #rrggbb."}
    applied = {}
    if args.get("frame") is not None:
        item.setFrameEnabled(bool(args["frame"]))
        applied["frame"] = item.frameEnabled()
    if "background_color" in colours:
        item.setBackgroundEnabled(True)
        item.setBackgroundColor(colours["background_color"])
        applied["background_color"] = hex_from_qcolor(item.backgroundColor())
    if text and args.get("font_family"):
        font = QFont(item.font())
        font.setFamily(str(args["font_family"]))
        item.setFont(font)
        applied["font_family"] = QFontInfo(font).family()
        if applied["font_family"].casefold() != str(args["font_family"]).casefold():
            applied["font_family_note"] = (f"{str(args['font_family'])!r} is not installed here; "
                                           f"QGIS draws the text in {applied['font_family']!r}.")
    if "font_color" in colours:
        if hasattr(item, "textFormat"):
            text_format = item.textFormat()
            text_format.setColor(colours["font_color"])
            item.setTextFormat(text_format)
            applied["font_color"] = hex_from_qcolor(item.textFormat().color())
        else:
            item.setFontColor(colours["font_color"])
            applied["font_color"] = hex_from_qcolor(item.fontColor())
    return applied, None


def _map_layers_for_lock(args):

    names = args.get("layers")
    if names:
        layers, missing = [], []
        for name in names:
            layer, _ = resolve_layer_note(name)
            if layer is None:
                missing.append(str(name))
            else:
                layers.append(layer)
        if missing:
            return None, {"_error": f"Layers not found: {missing}", "code": "LAYER_NOT_FOUND"}
        return layers, None
    try:
        return list(QgsProject.instance().layerTreeRoot().checkedLayers()), None
    except (AttributeError, RuntimeError):
        return [], None


def _lock_layout_item(args) -> dict:
    layout, error = _resolve_layout(args["layout_name"])
    if error:
        return error
    item = next((candidate for candidate in layout.items()
                 if isinstance(candidate, QgsLayoutItem) and candidate.uuid() == args["item_uuid"]), None)
    if item is None:
        return {"_error": f"Layout item not found: {args['item_uuid']}", "code": "INVALID_ARGS",
                "suggestion": "get_layout_info lists each item's uuid."}

    lock_item = bool(args.get("lock_item", True))
    lock_layers = bool(args.get("lock_layers", False))
    lock_style = bool(args.get("lock_style", False))
    map_theme = str(args.get("map_theme") or "").strip()
    if (lock_layers or lock_style or args.get("layers") or map_theme) and not isinstance(item, QgsLayoutItemMap):
        return {"_error": "lock_layers, lock_style and map_theme apply only to a map item.", "code": "INVALID_ARGS"}
    if map_theme and (lock_layers or args.get("layers")):
        return {"_error": "map_theme and layers/lock_layers both set what this map draws: not both.",
                "code": "INVALID_ARGS",
                "suggestion": "map_theme follows a saved theme's layers and styles; layers/lock_layers freeze "
                              "an explicit list instead."}
    if map_theme:
        themes = QgsProject.instance().mapThemeCollection()
        if not themes.hasMapTheme(map_theme):
            return {"_error": f"Map theme not found: {map_theme!r}. Existing themes: {themes.mapThemes()}",
                    "code": "INVALID_ARGS",
                    "suggestion": "get_map_themes gives the exact names; add_map_theme creates one from "
                                  "the current layer visibility."}
    if hasattr(item, "setLocked"):
        item.setLocked(lock_item)
    if isinstance(item, QgsLayoutItemMap):
        if lock_layers or args.get("layers"):
            layers, layer_error = _map_layers_for_lock(args)
            if layer_error:
                return layer_error
            item.setLayers(layers or [])
            item.setKeepLayerSet(True)
        elif args.get("lock_layers") is False:
            item.setKeepLayerSet(False)
        if lock_style:
            item.setKeepLayerStyles(True)
            try:
                item.storeCurrentLayerStyles()
            except (AttributeError, RuntimeError):
                pass
        elif args.get("lock_style") is False:



            item.setKeepLayerStyles(False)
        if map_theme:





            item.setFollowVisibilityPreset(True)
            item.setFollowVisibilityPresetName(map_theme)
    result_theme = None
    if isinstance(item, QgsLayoutItemMap):
        try:
            if item.followVisibilityPreset():
                result_theme = item.followVisibilityPresetName()
        except (AttributeError, RuntimeError):
            pass
    return {
        "layout_name": layout.name(), "item_uuid": item.uuid(),
        "item_type": type(item).__name__, "locked": bool(item.isLocked()) if hasattr(item, "isLocked") else lock_item,
        "layers_locked": bool(item.keepLayerSet()) if isinstance(item, QgsLayoutItemMap) else False,
        "style_locked": bool(item.keepLayerStyles()) if isinstance(item, QgsLayoutItemMap) else False,
        "map_theme": result_theme,
        "layer_names": [layer.name() for layer in item.layers()] if isinstance(item, QgsLayoutItemMap) else [],
    }




_ITEM_GAP_MM = 3.0
_PAGE_EDGE_MM = 5.0


_OVERLAP_MM2 = 0.5

_SHRINK_STEPS = (0.85, 0.7, 0.55)


_INSET_AREA_RATIO = 0.5


def _draws_overview(item) -> bool:
    try:
        return any(overview.linkedMap() is not None for overview in item.overviews().asList())
    except (AttributeError, RuntimeError):
        return False


def _larger_map_under(item):

    try:
        layout = item.layout()
        rect, z = item.sceneBoundingRect(), item.zValue()
    except (AttributeError, RuntimeError):
        return None
    if layout is None:
        return None
    area = rect.width() * rect.height()
    for other in layout.items():
        if other is item or not isinstance(other, QgsLayoutItemMap) or _draws_overview(other):
            continue
        try:
            if other.uuid() == item.uuid() or not other.isVisible() or other.zValue() >= z:
                continue
            other_rect = other.sceneBoundingRect()
        except (AttributeError, RuntimeError):
            continue
        if _overlap(rect, other_rect) and area < other_rect.width() * other_rect.height() * _INSET_AREA_RATIO:
            return other
    return None


def _is_inset(item) -> bool:





    if not isinstance(item, QgsLayoutItemMap):
        return False
    return _draws_overview(item) or _larger_map_under(item) is not None



_PANEL_TYPES = frozenset(int(getattr(value, "value", value)) for value in (
    getattr(QgsLayoutItemRegistry, "Layout3DMap", None),
    getattr(QgsLayoutItemRegistry, "LayoutElevationProfile", None)) if value is not None)


def _is_panel(item) -> bool:

    try:
        return int(item.type()) in _PANEL_TYPES
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def _is_surface(item, inset: bool = False) -> bool:

    return (isinstance(item, QgsLayoutItemMap) and not inset and not _is_inset(item)) or _is_panel(item)


def _item_name(item) -> str:
    if isinstance(item, QgsLayoutItemLabel):
        text = " ".join(str(item.text() or "").split())
        return f"label {text[:40]!r}" if text else "a label"
    try:
        return str(item.displayName())
    except (AttributeError, RuntimeError):
        return type(item).__name__


def _overlap(a, b) -> bool:
    shared = a.intersected(b)
    return shared.width() * shared.height() > _OVERLAP_MM2


def _obstacles(layout, item, surface: bool, page_rect, item_rect=None) -> list:








    own = item.uuid()
    found = []
    for other in layout.items():
        if not isinstance(other, QgsLayoutItem) or isinstance(other, QgsLayoutItemPage):
            continue
        try:
            if other.uuid() == own or not other.isVisible() or other.parentGroup() is not None:
                continue
            if type(other).__name__ == "QgsLayoutItemGroup":
                continue
            if _is_surface(other) != surface:
                continue
            rect = other.sceneBoundingRect()
        except (AttributeError, RuntimeError):
            continue
        if rect.width() <= 0 or rect.height() <= 0 or not rect.intersects(page_rect):
            continue
        if (not surface and item_rect is not None and isinstance(other, QgsLayoutItemShape)
                and rect.adjusted(-0.01, -0.01, 0.01, 0.01).contains(item_rect)):
            continue
        found.append((rect, other))
    return found


def _free_spot(rect, obstacles, page_rect):

    from qgis.PyQt.QtCore import QRectF

    width, height = rect.width(), rect.height()
    left, top, right, bottom = page_rect.left(), page_rect.top(), page_rect.right(), page_rect.bottom()
    xs = {rect.x(), left + _PAGE_EDGE_MM, right - _PAGE_EDGE_MM - width}
    ys = {rect.y(), top + _PAGE_EDGE_MM, bottom - _PAGE_EDGE_MM - height}
    for other, _item in obstacles:
        xs.update((other.right() + _ITEM_GAP_MM, other.left() - _ITEM_GAP_MM - width, other.left()))
        ys.update((other.bottom() + _ITEM_GAP_MM, other.top() - _ITEM_GAP_MM - height, other.top()))
    best = None
    for x in xs:
        if x < left - 0.01 or x + width > right + 0.01:
            continue
        for y in ys:
            if y < top - 0.01 or y + height > bottom + 0.01:
                continue
            candidate = QRectF(x, y, width, height)
            if any(_overlap(candidate, other) for other, _item in obstacles):
                continue
            distance = (x - rect.x()) ** 2 + (y - rect.y()) ** 2
            if best is None or distance < best[0]:
                best = (distance, x, y)
    return None if best is None else (best[1], best[2])


def _settle(layout, item, force_content_size: bool):




    for target in (layout, item):
        try:
            target.refresh()
        except (AttributeError, RuntimeError):
            pass
    if force_content_size and isinstance(item, QgsLayoutItemLegend):



        try:
            from qgis.core import QgsLegendRenderer
            size = QgsLegendRenderer(item.model(), item.legendSettings()).minimumSize()
            if size.width() > 0 and size.height() > 0:
                item.attemptResize(_mm_size(size.width(), size.height()))
        except (AttributeError, RuntimeError, TypeError):
            pass
    return item.sceneBoundingRect()


def _containing_page_rect(layout, item_rect):
    pages = layout.pageCollection()
    for candidate in (pages.page(i) for i in range(pages.pageCount())):
        page_rect = candidate.mapRectToScene(candidate.rect())
        if page_rect.contains(item_rect.center()) or page_rect.intersects(item_rect):
            return page_rect
    page = pages.page(0)
    return page.mapRectToScene(page.rect())


def place_item(layout, item, requested=None, resizable: bool = False, inset: bool = False,
               force_content_size: bool = False) -> dict:















    warnings, reasons = [], []
    try:
        rect = _settle(layout, item, force_content_size)
        page_rect = _containing_page_rect(layout, rect)
        width, height = rect.width(), rect.height()




        if resizable and (width > page_rect.width() or height > page_rect.height()):
            if width > page_rect.width():
                width = page_rect.width()
            if height > page_rect.height():
                height = page_rect.height()
            rect.moveLeft(min(max(rect.x(), page_rect.left()), page_rect.right() - width))
            rect.moveTop(min(max(rect.y(), page_rect.top()), page_rect.bottom() - height))
            item.attemptResize(_mm_size(width, height))
            item.attemptMove(_mm_point(rect.x(), rect.y()))
            reasons.append("shrunk to fit the page")
        for size, page_size, axis in ((width, page_rect.width(), "wide"), (height, page_rect.height(), "high")):
            if size > page_size:
                warnings.append(f"{_item_name(item)} is {size:.1f} mm {axis} but the page is "
                                f"{page_size:.1f} mm {axis}; it was kept at that size and may clip.")
        x = rect.x() if width > page_rect.width() else min(max(rect.x(), page_rect.left()), page_rect.right() - width)
        y = rect.y() if height > page_rect.height() else min(max(rect.y(), page_rect.top()),
                                                                 page_rect.bottom() - height)
        if abs(x - rect.x()) > 0.001 or abs(y - rect.y()) > 0.001:
            item.attemptMove(_mm_point(x, y))
            reasons.append("moved back onto the page")

        from qgis.PyQt.QtCore import QRectF

        surface = _is_surface(item, inset)
        base = _larger_map_under(item) if isinstance(item, QgsLayoutItemMap) and not (surface or inset) else None
        if base is not None:
            reasons.append(f"kept on top of {_item_name(base)} as an inset")
        here = QRectF(x, y, width, height)
        obstacles = _obstacles(layout, item, surface, page_rect, here)
        if surface and not _is_panel(item):



            obstacles = [(rect_other, other) for rect_other, other in obstacles
                         if _is_panel(other) or not (_overlap(here, rect_other) and rect_other.width()
                                                     * rect_other.height() < width * height * _INSET_AREA_RATIO)]
        covered = [other for rect_other, other in obstacles if _overlap(here, rect_other)]
        if covered:
            names = ", ".join(_item_name(other) for other in covered[:4])
            spot = _free_spot(here, obstacles, page_rect)
            scale = 1.0
            if spot is None and resizable:
                for scale in _SHRINK_STEPS:
                    spot = _free_spot(QRectF(x, y, width * scale, height * scale), obstacles, page_rect)
                    if spot is not None:
                        item.attemptResize(_mm_size(width * scale, height * scale))
                        break
            if spot is None:
                warnings.append(f"{_item_name(item)} covers {names}: no free place on the page holds it. "
                                "Smaller items fit; execute_code moves one.")
            else:
                item.attemptMove(_mm_point(spot[0], spot[1]))
                reasons.append(f"moved clear of {names}" if scale == 1.0
                               else f"shrunk to {scale * 100:.0f}% and moved clear of {names}")
        if surface and len(layout.items()) > 1:



            try:
                layout.moveItemToBottom(item)
            except (AttributeError, RuntimeError, TypeError):
                pass
    except Exception as exc:  # nosec B110
        warnings.append(f"Could not verify {type(item).__name__} placement on the page: {exc}")

    fields = {}
    if reasons and requested:
        fields["placement_adjustment"] = {
            "requested": {key: float(value) for key, value in requested.items() if value is not None},
            "actual": _item_geometry(item),
            "reason": "; ".join(reasons),
        }
    if warnings:
        fields["placement_warnings"] = warnings
    return fields


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

    wanted = "Landscape" if str(orientation or "").lower() == "landscape" else "Portrait"
    return enum_member(QgsLayoutItemPage, "Orientation", wanted)




_SUBJECT_MARGIN = 0.04


def _canvas_extent():

    canvas = iface.mapCanvas()
    rect = canvas.extent()




    settings = canvas.mapSettings() if hasattr(canvas, "mapSettings") else None
    canvas_crs = settings.destinationCrs() if settings is not None else None
    project = QgsProject.instance()
    project_crs = project.crs()
    if (canvas_crs is not None and canvas_crs.isValid() and project_crs.isValid()
            and canvas_crs != project_crs):
        try:
            transform = QgsCoordinateTransform(canvas_crs, project_crs, project.transformContext())
            rect = transform.transformBoundingBox(rect)
        except Exception as exc:
            return None, {
                "_error": f"Could not transform the canvas extent into {project_crs.authid()}: {exc}",
                "code": "INVALID_EXTENT",
            }
    return rect, None


def _subject(args: dict):










    extent = args.get("extent")
    layer_name = None if extent else args.get("layer")
    if layer_name:
        layer, _note = resolve_layer_note(layer_name)
        if layer is None:
            return None, "", _layer_not_found_error(layer_name)
        project = QgsProject.instance()
        rect = QgsRectangle(layer.extent())
        if layer.crs().isValid() and project.crs().isValid() and layer.crs() != project.crs():
            try:
                rect = QgsCoordinateTransform(layer.crs(), project.crs(), project.transformContext()) \
                    .transformBoundingBox(rect)
            except Exception as exc:
                return None, "", {"_error": f"Could not bring {layer.name()!r} into {project.crs().authid()}: {exc}",
                                  "code": "INVALID_EXTENT"}
        if rect.isNull() or not (rect.width() >= 0 and rect.height() >= 0):
            return None, "", {"_error": f"Layer {layer.name()!r} has no extent to show (it may be empty).",
                              "code": "INVALID_EXTENT"}
        source = f"layer {layer.name()!r}"
    elif extent:
        if len(extent) != 4:
            return None, "", {"_error": "extent must be [xmin, ymin, xmax, ymax]"}
        rect = QgsRectangle(extent[0], extent[1], extent[2], extent[3])
        source = "the extent given"
    else:
        rect, error = _canvas_extent()
        return rect, "", error
    if rect.width() <= 0 or rect.height() <= 0:

        side = max(rect.width(), rect.height()) or (0.01 if QgsProject.instance().crs().isGeographic() else 500.0)
        rect.grow(side / 2)
    if layer_name:




        rect.grow(_SUBJECT_MARGIN * max(rect.width(), rect.height()))
    return rect, source, None


def _create_print_layout(args: dict) -> dict:
    name = args["name"]
    page_size = args.get("page_size")
    asked = args.get("orientation")

    manager = QgsProject.instance().layoutManager()
    existing = manager.layoutByName(name)
    if existing and getattr(existing, "pageCollection", None) is None:
        return {"_error": f"A report is already called '{name}', and a print layout needs a name of its own.",
                "code": "INVALID_ARGS", "suggestion": "Another name is needed."}
    if existing and not args.get("set_page"):
        return {"_error": f"A layout named '{name}' already exists. set_page=true updates its page."}
    if str(args.get("template_path") or "").strip():
        if args.get("set_page"):
            return {"_error": "template_path creates a new layout; set_page changes an existing one.",
                    "code": "INVALID_ARGS", "suggestion": "Only one at a time."}
        return _layout_from_template(name, args)




    try:
        rect, source, error = _subject(args)
    except Exception:  # noqa: BLE001
        rect, source, error = None, "", None
    if error and (args.get("layer") or args.get("extent")):
        return error
    ratio = rect.width() / rect.height() if rect is not None and rect.width() > 0 and rect.height() > 0 else None
    orientation = asked or ("portrait" if ratio is not None and ratio < 1.0 else "landscape")

    if existing:
        layout = existing
        if not _set_page(layout, page_size, orientation):
            return {"_error": f"Failed to set page size '{page_size}'."}
    else:
        layout = QgsPrintLayout(QgsProject.instance())
        layout.initializeDefaults()
        layout.setName(name)
        page_size = page_size or "A4"
        if not _set_page(layout, page_size, orientation):
            return {"_error": f"Failed to set page size '{page_size}'. One of: {_PAGE_SIZES}"}
        manager.addLayout(layout)

    result = _layout_summary(layout)
    if ratio is not None:
        shown = source or "the map view"
        result["map_subject"] = {"source": shown, "width_to_height": round(ratio, 3)}
        shape = (f"{1 / ratio:.1f} times taller than wide" if ratio < 1.0
                 else f"{ratio:.1f} times wider than tall")
        if not asked:
            result["orientation_note"] = f"{orientation.capitalize()}: {shown} is {shape}."



        elif (asked == "portrait") != (ratio < 1.0) and abs(ratio - 1.0) > 0.1:
            result["orientation_note"] = (f"{orientation.capitalize()} as asked, though {shown} is {shape}: "
                                          "the map fills less of the sheet than it would turned the other way.")
    return result


def _layout_from_template(name: str, args: dict) -> dict:








    from qgis.core import QgsReadWriteContext
    from qgis.PyQt.QtXml import QDomDocument

    from ..core import security
    from ..ui.file_links import path_from_url

    path = str(args["template_path"]).strip()


    path = path_from_url(path) or path
    if not os.path.isabs(path) and security.project_dir():
        path = os.path.join(security.project_dir(), path)
    path = os.path.normpath(path)
    path_error = security.validate_path(path)
    if path_error:
        return {"_error": path_error}
    if not os.path.isfile(path):
        return {"_error": f"Template not found: {path}", "code": "INVALID_ARGS",
                "suggestion": "template_path needs a .qpt file's full path, from export_layout(format='qpt') or QGIS."}
    rect = None
    if args.get("layer") or args.get("extent"):
        rect, _source, error = _subject(args)
        if error:
            return error
    with open(path, "rb") as handle:
        data = handle.read()
    document = QDomDocument()
    parsed = document.setContent(data)
    ok = parsed[0] if isinstance(parsed, tuple) else bool(parsed)
    if not ok:
        detail = parsed[1] if isinstance(parsed, tuple) and len(parsed) > 1 else getattr(parsed, "errorMessage", "")
        return {"_error": f"{os.path.basename(path)} is not a readable layout template: {detail}",
                "code": "INVALID_ARGS"}
    project = QgsProject.instance()
    layout = QgsPrintLayout(project)
    context = QgsReadWriteContext()
    context.setPathResolver(project.pathResolver())
    loaded = layout.loadFromTemplate(document, context)
    items, ok = loaded if isinstance(loaded, tuple) else (loaded, True)
    if not ok or layout.pageCollection().pageCount() == 0:
        return {"_error": f"{os.path.basename(path)} holds no print layout QGIS can read.", "code": "INVALID_ARGS",
                "suggestion": "The file may not be a print layout template (.qpt)."}
    layout.setName(name)
    if args.get("page_size") or args.get("orientation"):
        orientation = args.get("orientation") or _page_summary(layout)["orientation"] or "landscape"
        if not _set_page(layout, args.get("page_size"), orientation):
            return {"_error": f"Failed to set page size '{args.get('page_size')}'."}
    zoomed = None
    if rect is not None:
        map_item = _first_map_item(layout)
        if map_item is not None:

            project, map_crs = QgsProject.instance(), map_item.crs()
            if map_crs.isValid() and project.crs().isValid() and map_crs != project.crs():
                try:
                    rect = QgsCoordinateTransform(project.crs(), map_crs, project.transformContext()) \
                        .transformBoundingBox(rect)
                except Exception as exc:
                    return {"_error": f"Could not bring the area into the template map's CRS "
                                      f"{map_crs.authid()}: {exc}", "code": "INVALID_EXTENT"}
            map_item.zoomToExtent(rect)
            zoomed = map_item.uuid()
    QgsProject.instance().layoutManager().addLayout(layout)
    result = _layout_summary(layout)
    result["template"] = path
    result["items_loaded"] = len([item for item in items or () if isinstance(item, QgsLayoutItem)
                                  and not isinstance(item, QgsLayoutItemPage)])
    if zoomed:
        result["map_zoomed"] = zoomed
    elif any(isinstance(item, QgsLayoutItemMap) for item in layout.items()):
        result["map_note"] = ("Maps show the extent saved in the template; layer or extent passed with "
                              "template_path zooms the main map instead, keeping its frame.")
    return result


def _add_layout_map(args: dict) -> dict:
    layout, error = _resolve_layout(args["layout_name"])
    if error:
        return error
    if _editing(args):
        return _edit_layout_map(layout, args)
    error = _missing(args, ("x", "y", "width", "height"), "map")
    if error:
        return error

    rect, source, error = _subject(args)
    if error:
        return error




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
                               if known else "This layout has no map yet; add_layout_map without "
                                             "overview_of makes one. Nothing was added."),
            }



    map_crs = None
    crs_text = str(args.get("crs") or "").strip()
    if crs_text:
        map_crs = QgsCoordinateReferenceSystem(crs_text)
        if not map_crs.isValid():
            return {"_error": f"Invalid map CRS: {crs_text}. Nothing was added.", "code": "INVALID_ARGS",
                    "suggestion": "An authority id, e.g. EPSG:2154 or EPSG:32631."}
        project = QgsProject.instance()
        if project.crs().isValid() and map_crs != project.crs():
            try:
                rect = QgsCoordinateTransform(project.crs(), map_crs, project.transformContext()) \
                    .transformBoundingBox(rect)
            except Exception as exc:
                return {"_error": f"Could not bring the map's area into {map_crs.authid()}: {exc}. Nothing was added.",
                        "code": "INVALID_EXTENT"}

    map_item = QgsLayoutItemMap(layout)
    map_item.attemptMove(_mm_point(args["x"], args["y"]))
    map_item.attemptResize(_mm_size(args["width"], args["height"]))
    map_item.setFrameEnabled(bool(args.get("frame", True)))
    style, error = _apply_box_style(map_item, args)
    if error:
        return error
    if map_crs is not None:
        map_item.setCrs(map_crs)
    layout.addLayoutItem(map_item)

    placement = place_item(layout, map_item,
                           {"x": args["x"], "y": args["y"], "width": args["width"], "height": args["height"]},
                           resizable=True, inset=main_map is not None)







    if args.get("layer") and not args.get("extent") and main_map is None and args.get("scale") is None:
        fitted = _fit_frame_to(map_item, rect)
        if fitted:
            adjustment = placement.setdefault("placement_adjustment", {
                "requested": {key: float(args[key]) for key in ("x", "y", "width", "height")},
                "reason": "",
            })
            adjustment["reason"] = "; ".join(part for part in (adjustment["reason"], fitted) if part)
            adjustment["actual"] = _item_geometry(map_item)
    map_item.zoomToExtent(rect)

    scale_note = ""
    wanted_scale = args.get("scale")
    if wanted_scale is not None:
        from .advanced_tools import _apply_scale

        applied = _apply_scale(map_item, wanted_scale, args["layout_name"])
        if isinstance(applied, dict):
            layout.removeLayoutItem(map_item)
            return applied
        scale_note = applied

    if main_map is not None:
        try:
            from qgis.core import QgsLayoutItemMapOverview


            overview = QgsLayoutItemMapOverview("Overview", map_item)
            overview.setLinkedMap(main_map)
            map_item.overviews().addOverview(overview)
        except Exception as exc:
            layout.removeLayoutItem(map_item)
            return {"_error": f"Could not add overview frame: {exc}. Nothing was added."}
    lock_args = {key: args[key] for key in ("lock_item", "lock_layers", "lock_style", "layers", "map_theme")
                if key in args}
    locked = None
    if lock_args:


        lock_args.setdefault("lock_item", False)
        lock_args.update({"layout_name": args["layout_name"], "item_uuid": map_item.uuid()})
        locked = _lock_layout_item(lock_args)
        if locked.get("_error"):
            layout.removeLayoutItem(map_item)
            return locked
    result = _with_new_item(layout, map_item)
    result.update(placement)
    result.update(style)
    if source:
        result["fitted_to"] = source
    if args.get("layer") and args.get("extent"):
        result["layer_note"] = "extent and layer were both given: the map shows the extent, layer was not used."
    result.update(_frame_crs_facts(map_item))
    if wanted_scale is not None:
        result["scale"] = int(round(map_item.scale()))
        if scale_note:
            result["scale_note"] = scale_note
    shown = map_item.extent()
    result["extent_shown"] = [round(shown.xMinimum(), 6), round(shown.yMinimum(), 6),
                              round(shown.xMaximum(), 6), round(shown.yMaximum(), 6)]
    if locked is not None:
        result["locked"] = locked
    else:
        drawn = _visible_layer_names()
        if drawn:




            result["draws"] = drawn[:12] + ([f"{len(drawn) - 12} more"] if len(drawn) > 12 else [])
            covered = _covered_layers()
            if covered:
                result["covered_note"] = (
                    "; ".join(f"'{low}' is drawn under the raster '{high}', which covers {percent}% of it"
                              for low, high, percent in covered[:3])
                    + ": it will hardly show on this map. set_layer_order layer_name that layer, position top, "
                    "before exporting.")
    return result


def _frame_crs_facts(map_item) -> dict:






    frame_crs = map_item.crs() if map_item.crs().isValid() else QgsProject.instance().crs()
    facts = {"map_crs": frame_crs.authid()}
    if frame_crs.isGeographic():
        centre = map_item.extent().center().y()
        stretch = 1 / max(0.05, abs(math.cos(math.radians(centre))))
        facts["map_crs_note"] = (
            f"{frame_crs.authid()} draws in degrees: at {abs(centre):.0f} degrees of latitude shapes print "
            f"{stretch:.2f} times wider east-west than north-south, and a scale bar holds near the frame's "
            "centre only; the crs argument draws the frame in a projected CRS without changing the project's")


    area = frame_crs.bounds()
    project = QgsProject.instance()
    if not frame_crs.isGeographic() and area is not None and not area.isEmpty():
        try:
            seen = QgsCoordinateTransform(frame_crs, QgsCoordinateReferenceSystem("EPSG:4326"),
                                          project.transformContext()).transformBoundingBox(map_item.extent())
        except Exception:  # noqa: BLE001
            seen = None
        if seen is None or not seen.intersects(area):
            where = (f" It lies near longitude {seen.center().x():.1f}, latitude {seen.center().y():.1f}."
                     if seen is not None and not seen.isEmpty() else "")
            facts["extent_note"] = (
                f"The frame's extent is outside the area {frame_crs.authid()} is defined for (longitude "
                f"{area.xMinimum():.1f} to {area.xMaximum():.1f}, latitude {area.yMinimum():.1f} to "
                f"{area.yMaximum():.1f}).{where} extent is read in the project's CRS "
                f"({project.crs().authid()}) and shown in the frame's.")
    return facts


_MAP_EDIT_READS = frozenset({"layout_name", "item_id", "x", "y", "width", "height", "extent", "layer", "frame",
                             "background_color", "scale", "lock_item", "lock_layers", "lock_style", "layers",
                             "map_theme"})


def _edit_layout_map(layout, args: dict) -> dict:





    map_item, error = _find_item(layout, args["item_id"], QgsLayoutItemMap, "map")
    if error:
        return error
    subject = None
    if args.get("layer") or args.get("extent"):
        rect, _source, error = _subject(args)
        if error:
            return error
        project = QgsProject.instance()
        crs = map_item.crs()
        if crs.isValid() and project.crs().isValid() and crs != project.crs():
            try:
                rect = QgsCoordinateTransform(project.crs(), crs, project.transformContext()).transformBoundingBox(rect)
            except Exception as exc:
                return {"_error": f"Could not bring the map's area into {crs.authid()}: {exc}. Nothing was changed.",
                        "code": "INVALID_EXTENT"}
        subject = rect
    shown = QgsRectangle(map_item.extent())
    style, error = _apply_box_style(map_item, args)
    if error:
        return error
    placement = _edit_geometry(layout, map_item, args, resizable=True, inset=_is_inset(map_item))
    if subject is not None:
        map_item.zoomToExtent(subject)
    elif placement or any(args.get(key) is not None for key in ("width", "height")):
        map_item.zoomToExtent(shown)
    scale_note = ""
    if args.get("scale") is not None:
        from .advanced_tools import _apply_scale

        applied = _apply_scale(map_item, args["scale"], layout.name())
        if isinstance(applied, dict):
            return applied
        scale_note = applied
    lock_args = {key: args[key] for key in ("lock_item", "lock_layers", "lock_style", "layers", "map_theme")
                if key in args}
    locked = None
    if lock_args:
        lock_args.setdefault("lock_item", bool(map_item.isLocked()))
        lock_args.update({"layout_name": layout.name(), "item_uuid": map_item.uuid()})
        locked = _lock_layout_item(lock_args)
        if locked.get("_error"):
            return locked
    result = _edited(layout, map_item, placement, args, _MAP_EDIT_READS)
    result.update(style)
    shown = map_item.extent()
    result["extent_shown"] = [round(shown.xMinimum(), 6), round(shown.yMinimum(), 6),
                              round(shown.xMaximum(), 6), round(shown.yMaximum(), 6)]
    result.update(_frame_crs_facts(map_item))
    result["scale"] = int(round(map_item.scale()))
    if scale_note:
        result["scale_note"] = scale_note
    if locked is not None:
        result["locked"] = locked
    return result




_COVERED_SHARE = 0.8


def _covered_layers() -> list:






    project = QgsProject.instance()
    root = project.layerTreeRoot()
    rasters = []
    pairs = []
    for layer in root.layerOrder():
        node = root.findLayer(layer.id())
        if node is None or not node.isVisible():
            continue
        try:
            if isinstance(layer, QgsRasterLayer):
                if layer.opacity() >= 0.9:
                    rasters.append(layer)
                continue
            if not isinstance(layer, QgsVectorLayer):
                continue
            for raster in rasters:
                extent = layer.extent()
                if raster.crs() != layer.crs():
                    extent = QgsCoordinateTransform(layer.crs(), raster.crs(), project).transformBoundingBox(extent)
                area = extent.width() * extent.height()
                shared = extent.intersect(raster.extent())
                share = shared.width() * shared.height() / area if area > 0 else 0.0
                if share >= _COVERED_SHARE:
                    pairs.append((layer.name(), raster.name(), round(share * 100)))
                    break
        except Exception:  # noqa: BLE001  # nosec B112
            continue
    return pairs


def _visible_layer_names() -> list:

    root = QgsProject.instance().layerTreeRoot()
    names = []
    for layer in root.layerOrder():
        node = root.findLayer(layer.id())
        if node is not None and node.isVisible():
            names.append(layer.name())
    return names




_MIN_FILL = 0.8


def _fit_frame_to(map_item, rect) -> str:








    try:
        size = map_item.sizeWithUnits()
        pos = map_item.positionWithUnits()
        width, height = float(size.width()), float(size.height())
        if not (rect.width() > 0 and rect.height() > 0 and width > 0 and height > 0):
            return ""
        subject = rect.width() / rect.height()
        frame = width / height
        if min(subject / frame, frame / subject) >= _MIN_FILL:
            return ""
        x, y = float(pos.x()), float(pos.y())
        if subject < frame:
            new_w, new_h = height * subject, height
            x += (width - new_w) / 2
        else:
            new_w, new_h = width, width / subject
            y += (height - new_h) / 2
        map_item.attemptResize(_mm_size(new_w, new_h))
        map_item.attemptMove(_mm_point(x, y))
        return (f"frame narrowed from {width:.0f} x {height:.0f} to {new_w:.0f} x {new_h:.0f} mm, centred in "
                "the same box, so the subject fills it instead of a strip of it")
    except Exception:  # noqa: BLE001
        return ""




_ASKED_AT = "terralab_agent/asked_at"


def _same_label(item, text: str, x, y) -> bool:

    if not isinstance(item, QgsLayoutItemLabel):
        return False
    if str(item.text() or "") != text:
        return False
    rect = item.sceneBoundingRect()
    spots = [(rect.x(), rect.y())]
    try:
        asked = str(item.customProperty(_ASKED_AT, "") or "").split(",")
        if len(asked) == 2:
            spots.append((float(asked[0]), float(asked[1])))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass
    return any(abs(sx - float(x)) <= 1.0 and abs(sy - float(y)) <= 1.0 for sx, sy in spots)


def _add_layout_label(args: dict) -> dict:







    layout, error = _resolve_layout(args["layout_name"])
    if error:
        return error
    if _editing(args):
        return _edit_layout_label(layout, args)
    error = _missing(args, ("text", "x", "y", "width", "height"), "label")
    if error:
        return error

    text = args["text"]
    existing = next((item for item in layout.items() if _same_label(item, text, args["x"], args["y"])), None)
    if existing is not None:


        result = _edit_layout_label(layout, {**args, "item_id": existing.uuid()})
        if result.get("_error"):
            return result
        result["new_item_uuid"] = existing.uuid()
        result["reused_existing_label"] = True
        result["note"] = ("This label was already on the layout at this spot with this text, so it was "
                          "changed in place rather than drawn a second time over itself.")
        return result

    label = QgsLayoutItemLabel(layout)
    label.setText(text)

    font = QFont()
    font.setPointSizeF(float(args.get("font_size", 14)))
    if args.get("bold"):
        font.setBold(True)
    label.setFont(font)

    _label_halign(label, args.get("halign"))
    style, error = _apply_box_style(label, args, text=True)
    if error:
        return error

    label.attemptMove(_mm_point(args["x"], args["y"]))
    label.attemptResize(_mm_size(args["width"], args["height"]))
    label.setCustomProperty(_ASKED_AT, f"{float(args['x'])},{float(args['y'])}")
    layout.addLayoutItem(label)
    placement = place_item(layout, label,
                           {"x": args["x"], "y": args["y"], "width": args["width"], "height": args["height"]},
                           resizable=True)
    result = _with_new_item(layout, label)
    result.update(placement)
    result.update(style)
    return result


def _label_halign(label, halign) -> None:
    if not halign:
        return
    from qgis.PyQt.QtCore import Qt
    flags = Qt.AlignmentFlag
    align_map = {"left": flags.AlignLeft, "center": flags.AlignHCenter, "right": flags.AlignRight}
    try:
        label.setHAlign(align_map.get(halign, flags.AlignLeft))
    except Exception:  # nosec B110
        pass


_LABEL_EDIT_READS = frozenset({"layout_name", "item_id", "text", "x", "y", "width", "height", "font_size", "bold",
                               "halign", "font_color", "font_family", "frame", "background_color"})


def _edit_layout_label(layout, args: dict) -> dict:

    label, error = _find_item(layout, args["item_id"], QgsLayoutItemLabel, "label")
    if error:
        return error
    style, error = _apply_box_style(label, args, text=True)
    if error:
        return error
    if args.get("text") is not None:
        label.setText(str(args["text"]))
    if args.get("font_size") is not None or args.get("bold") is not None:
        try:
            font = QFont(label.font())
        except (AttributeError, RuntimeError):
            font = QFont()
        if args.get("font_size") is not None:
            font.setPointSizeF(float(args["font_size"]))
        if args.get("bold") is not None:
            font.setBold(bool(args["bold"]))
        label.setFont(font)
    _label_halign(label, args.get("halign"))
    placement = _edit_geometry(layout, label, args, resizable=True)
    if placement or args.get("x") is not None or args.get("y") is not None:
        here = label.positionWithUnits()
        label.setCustomProperty(_ASKED_AT, f"{here.x()},{here.y()}")
    result = _edited(layout, label, placement, args, _LABEL_EDIT_READS)
    result.update(style)
    return result


def _legend_patch_shape(value: str):

    from qgis.core import QgsLegendPatchShape
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        style = QgsStyle.defaultStyle()
        names = list(style.legendPatchShapeNames()) if hasattr(style, "legendPatchShapeNames") else []
        if raw in names:
            shape = style.legendPatchShape(raw)
            return shape if not shape.isNull() else None
    except (AttributeError, RuntimeError):
        pass
    symbol_type = {"marker": "Marker", "point": "Marker", "line": "Line", "polygon": "Fill",
                   "fill": "Fill"}.get(raw.casefold())
    if symbol_type:
        enum_scope = getattr(Qgis, "SymbolType", None) if Qgis is not None else None
        member = (getattr(enum_scope, symbol_type, None) if enum_scope is not None
                  else getattr(QgsSymbol, symbol_type, None))
        if member is not None:
            try:
                return QgsLegendPatchShape(member, QgsGeometry(), True)
            except (TypeError, RuntimeError):
                return None
    if raw.upper().startswith(("POINT", "LINESTRING", "POLYGON")):
        try:
            geom = QgsGeometry.fromWkt(raw)
            if not geom.isNull():
                enum_scope = getattr(Qgis, "SymbolType", None) if Qgis is not None else None
                member = getattr(enum_scope, "Fill", None) if raw.upper().startswith("POLYGON") else (
                    getattr(enum_scope, "Line", None) if raw.upper().startswith("LINE")
                    else getattr(enum_scope, "Marker", None))
                if member is not None:
                    return QgsLegendPatchShape(member, geom, True)
        except (TypeError, RuntimeError, ValueError):
            pass
    return None


def _apply_legend_patch_shapes(legend, specs) -> list:

    if not specs:
        return []
    applied, warnings = [], []
    try:
        model = legend.model()
        for spec in specs:
            layer_key = str(spec.get("layer", "")).strip().casefold()
            class_key = str(spec.get("class", "")).strip().casefold()
            shape = _legend_patch_shape(spec.get("shape"))
            if shape is None:
                warnings.append(f"Unknown legend patch shape: {spec.get('shape')!r}")
                continue
            matched = False
            for layer_node in model.rootGroup().findLayers():
                layer = layer_node.layer()
                layer_name = (layer.name() if layer is not None else layer_node.name()) or ""
                if layer_name.casefold() != layer_key:
                    continue
                from qgis.PyQt.QtCore import Qt
                for node in model.layerLegendNodes(layer_node):
                    label = str(node.data(enum_member(Qt, "ItemDataRole", "DisplayRole")) or "")
                    if class_key and label.casefold() != class_key:
                        continue
                    if hasattr(node, "setPatchShape"):
                        node.setPatchShape(shape)
                        matched = True
            if matched:
                applied.append({"layer": spec.get("layer"), "class": spec.get("class"), "shape": spec.get("shape")})
            else:
                warnings.append(f"Legend entry not found for layer {spec.get('layer')!r}")
    except (AttributeError, RuntimeError) as exc:
        warnings.append(f"Per-entry patch shapes unavailable: {exc}")
    return [applied, warnings]


def _apply_size_legend_mode(legend, mode: str, classes) -> dict:

    if mode not in ("collapsed", "manual"):
        return {"mode": "automatic", "layers": []}
    changed, warnings = [], []
    enum_type = getattr(QgsDataDefinedSizeLegend, "LegendType", None)
    legend_type = getattr(enum_type, "LegendCollapsed" if mode == "collapsed" else "LegendSeparated", None)
    if legend_type is None:
        return {"mode": mode, "layers": [], "warning": "This QGIS version has no proportional legend mode API."}
    for node in legend.model().rootGroup().findLayers():
        layer = node.layer()
        renderer = layer.renderer() if layer is not None and hasattr(layer, "renderer") else None
        if renderer is None or not hasattr(renderer, "dataDefinedSizeLegend"):
            continue
        try:
            settings = renderer.dataDefinedSizeLegend()
            settings.setLegendType(legend_type)
            if mode == "manual" and classes:
                settings.setClasses([QgsDataDefinedSizeLegend.SizeClass(float(item["size"]), str(item["label"]))
                                     for item in classes])
            renderer.setDataDefinedSizeLegend(settings)
            layer.triggerRepaint()
            changed.append(layer.name())
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            warnings.append(f"{layer.name()}: {exc}")
    return {"mode": mode, "layers": changed, "warnings": warnings}


def _add_layout_legend(args: dict) -> dict:













    layout, error = _resolve_layout(args["layout_name"])
    if error:
        return error

    has_position = args.get("x") is not None and args.get("y") is not None
    if args.get("background_color") is not None and not qcolor_from_text(str(args["background_color"])).isValid():
        return {"_error": f"background_color {args['background_color']!r} is not a colour.", "code": "INVALID_ARGS",
                "suggestion": "A colour is written #rrggbb."}
    editing = bool(str(args.get("legend_id") or "").strip())
    if editing:
        legend, error = _find_legend(layout, args["legend_id"])
        if error:
            return error
        if args.get("title") is not None:
            legend.setTitle(args["title"])
        if args.get("linked_to_map") is False:
            legend.setLinkedMap(None)
        elif args.get("map_id") or (args.get("linked_to_map") and legend.linkedMap() is None):
            target, error = _map_for(layout, args.get("map_id"))
            if error:
                return error
            legend.setLinkedMap(target)
        map_item = legend.linkedMap()
        if has_position:
            legend.attemptMove(_mm_point(args["x"], args["y"]))
    else:
        if not has_position:
            return {"_error": "x and y are needed to add a legend.", "code": "INVALID_ARGS",
                    "suggestion": "x and y in mm add one; legend_id changes one already on the layout."}
        linked = args.get("linked_to_map", True)
        map_item = None
        if linked:
            map_item, error = _map_for(layout, args.get("map_id"))
            if error:
                return error
        legend = QgsLayoutItemLegend(layout)
        if args.get("title") is not None:
            legend.setTitle(args["title"])
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
            except Exception:  # nosec B110
                pass

        legend.attemptMove(_mm_point(args["x"], args["y"]))


        layout.addLayoutItem(legend)
    style, _ = _apply_box_style(legend, args)
    removed = _legend_pick_layers(legend, args.get("layers"), args.get("hide_layers"))
    defaults = {} if editing else _default_curation(legend, args)
    curation = _curate_legend(legend, args)
    columns = args.get("columns")
    if columns is not None:
        legend.setColumnCount(max(1, int(columns)))
    if args.get("split_layers") is not None:
        legend.setSplitLayer(bool(args["split_layers"]))
    try:
        legend.updateLegend()
    except (AttributeError, RuntimeError):
        pass

    sized = args.get("width") is not None and args.get("height") is not None
    if sized:
        legend.attemptResize(_mm_size(args["width"], args["height"]))
    elif not editing or legend.resizeToContents():



        try:
            legend.setResizeToContents(True)
            legend.adjustBoxSize()
        except (AttributeError, RuntimeError):
            pass
    size_legend = _apply_size_legend_mode(legend, args.get("legend_mode", "automatic"), args.get("manual_classes"))
    patch_result = _apply_legend_patch_shapes(legend, args.get("patch_shapes"))
    entries = _legend_entries(legend)
    rows = sum(1 + len(entry["classes"]) for entry in entries)
    if rows > 14 and not sized and not editing and columns is None:

        try:
            legend.setColumnCount(2 if rows <= 30 else 3)
            legend.adjustBoxSize()
        except (AttributeError, RuntimeError):
            pass

    if has_position:
        requested = {"x": args["x"], "y": args["y"]}
    else:
        here = legend.positionWithUnits()
        requested = {"x": here.x(), "y": here.y()}
    if sized:
        requested.update(width=args["width"], height=args["height"])
    placement = place_item(layout, legend, requested, resizable=sized, force_content_size=not sized)
    result = _with_new_item(layout, legend)
    if editing:
        result.pop("new_item_uuid", None)
        result.pop("new_item_type", None)
        result["edited_item_uuid"] = legend.uuid()
    result["actual_geometry"] = _item_geometry(legend)
    result.update(placement)
    result["entries"] = entries
    result["linked_map"] = map_item.uuid() if map_item else None
    result["columns"] = legend.columnCount()
    result.update(style)
    if curation:
        result["curation"] = curation
    if defaults:
        result["default_curation"] = defaults
    result["auto_update"] = _auto_updates(legend)
    if removed:
        result["removed_from_legend"] = removed
    if args.get("legend_mode"):
        result["legend_mode"] = size_legend
    if args.get("patch_shapes"):
        result["patch_shapes"] = {"applied": patch_result[0], "warnings": patch_result[1]}
    if not entries:
        result["empty_note"] = ("The legend has no entries: the map it follows draws nothing, or every "
                                "layer is switched off. get_layout_info shows it.")
    return result


_SCALEBAR_READS = frozenset({"layout_name", "item_id", "x", "y", "style", "units_label", "segments", "map_id",
                             "min_segment_width", "max_segment_width"})


def _add_layout_scalebar(args: dict) -> dict:
    layout, error = _resolve_layout(args["layout_name"])
    if error:
        return error

    editing = _editing(args)
    if editing:
        scalebar, error = _find_item(layout, args["item_id"], QgsLayoutItemScaleBar, "scale bar")
        if error:
            return error
        map_item = scalebar.linkedMap()
        if args.get("map_id"):
            map_item, error = _map_for(layout, args["map_id"])
            if error:
                return error
            scalebar.setLinkedMap(map_item)
        if args.get("style"):
            scalebar.setStyle(args["style"])
    else:
        error = _missing(args, ("x", "y"), "scale bar")
        if error:
            return error
        map_item, error = _map_for(layout, args.get("map_id"))
        if error:
            return error
        if not map_item:
            return {"_error": "A scale bar must be linked to a map; add_layout_map adds one."}
        scalebar = QgsLayoutItemScaleBar(layout)
        scalebar.setStyle(args.get("style", "Single Box"))
        scalebar.setLinkedMap(map_item)

    resize = not editing or bool(args.get("units_label") or args.get("map_id"))
    segments_before = scalebar.numberOfSegments() if editing else 2





    label = args.get("units_label")
    unit = _scalebar_unit(label) if label else None
    units_note = ""
    if unit is not None and unit == _distance_unit("Degrees"):


        unit, label = None, None
        units_note = ("A scale bar in degrees measures nothing on the ground (a degree of longitude "
                      "shrinks toward the poles), so it is in metres or kilometres instead.")
    sized = not resize
    if resize and unit is not None:
        try:
            scalebar.setUnits(unit)
            scalebar.applyDefaultSize(unit)
            sized = True
        except Exception:  # nosec B110
            pass
    if not sized:



        metres, kilometres = _distance_unit("Meters"), _distance_unit("Kilometers")
        if metres is None or kilometres is None:
            scalebar.applyDefaultSize()
        else:
            scalebar.applyDefaultSize(metres)
            per_segment = scalebar.unitsPerSegment()
            if per_segment >= 1000:



                scalebar.setUnits(kilometres)
                scalebar.setUnitsPerSegment(per_segment / 1000.0)
                scalebar.setUnitLabel("km")



    segments = args.get("segments", segments_before)
    try:
        scalebar.setNumberOfSegments(int(segments))
    except Exception:  # nosec B110
        pass
    if label:
        try:
            scalebar.setUnitLabel(label)
        except Exception:  # nosec B110
            pass







    min_width, max_width = args.get("min_segment_width"), args.get("max_segment_width")
    segment_width = {}
    if min_width is not None or max_width is not None:
        from qgis.core import QgsScaleBarSettings

        from ._compat import enum_value

        lo = float(min_width) if min_width is not None else scalebar.minimumBarWidth()
        hi = float(max_width) if max_width is not None else scalebar.maximumBarWidth()
        if lo > hi:
            lo, hi = hi, lo
        fit_mode = enum_value((QgsScaleBarSettings, "SegmentSizeFitWidth"),
                              (QgsScaleBarSettings, "SegmentSizeMode.FitWidth"))
        try:
            if fit_mode is not None:
                scalebar.setSegmentSizeMode(fit_mode)
            scalebar.setMinimumBarWidth(lo)
            scalebar.setMaximumBarWidth(hi)
            segment_width = {"min_mm": round(lo, 2), "max_mm": round(hi, 2), "mode": "fit_width"}
        except Exception:  # nosec B110
            segment_width = {}

    if editing:
        result = _edited(layout, scalebar, _edit_geometry(layout, scalebar, args, resizable=False), args,
                         _SCALEBAR_READS)
    else:
        scalebar.attemptMove(_mm_point(args["x"], args["y"]))
        layout.addLayoutItem(scalebar)
        placement = place_item(layout, scalebar, {"x": args["x"], "y": args["y"]})
        result = _with_new_item(layout, scalebar)
        result["actual_geometry"] = _item_geometry(scalebar)
        result.update(placement)
    if map_item is None:
        return result
    result["linked_map"] = map_item.uuid()
    result["units"] = scalebar.unitLabel() or label or ""
    if segment_width:
        result["segment_width"] = segment_width
    if units_note:
        result["units_note"] = units_note
    try:
        crs = map_item.crs()
        if crs.isValid() and crs.isGeographic():
            result["crs_note"] = (f"The map is drawn in {crs.authid()}, a geographic CRS: the bar is measured "
                                  "on the ellipsoid and reads true, but the map is stretched east to west "
                                  "away from the equator. A projected CRS (the UTM zone or the national grid) "
                                  "prints true shapes.")
    except (AttributeError, RuntimeError):
        pass
    if label:
        result["units_label"] = label
        if unit is None:
            result["units_note"] = (
                f"{label!r} is not a distance unit I know, so it is printed as a free label "
                "and the bar keeps map units. m, km, ft, yd, mi or nm size the bar."
            )
    return result


_ARROW_READS = frozenset({"layout_name", "item_id", "x", "y", "width", "height", "map_id", "north"})


def _set_north(picture, map_item, north: str) -> None:
    try:
        picture.setLinkedMap(map_item)
        picture.setNorthMode(enum_member(QgsLayoutItemPicture, "NorthMode", north))
    except Exception:  # nosec B110
        pass


def _edit_layout_north_arrow(layout, args: dict) -> dict:

    picture, error = _find_item(layout, args["item_id"], QgsLayoutItemPicture, "north arrow")
    if error:
        return error
    map_item = picture.linkedMap()
    if args.get("map_id"):
        map_item, error = _map_for(layout, args["map_id"])
        if error:
            return error
    if map_item is not None and (args.get("map_id") or args.get("north")):
        _set_north(picture, map_item, "TrueNorth" if args.get("north") == "true" else "GridNorth")
    result = _edited(layout, picture, _edit_geometry(layout, picture, args, resizable=True), args, _ARROW_READS)
    if map_item is not None:
        result["linked_map"] = map_item.uuid()
    return result


def _add_layout_north_arrow(args: dict) -> dict:
    layout, error = _resolve_layout(args["layout_name"])
    if error:
        return error
    if _editing(args):
        return _edit_layout_north_arrow(layout, args)
    error = _missing(args, ("x", "y"), "north arrow")
    if error:
        return error

    map_item, error = _map_for(layout, args.get("map_id"))
    if error:
        return error
    svg_path = _find_north_arrow_svg()
    if not svg_path:
        return {"_error": "Could not find a bundled north-arrow SVG in the QGIS svg paths."}

    picture = QgsLayoutItemPicture(layout)
    picture.setPicturePath(svg_path)
    picture.attemptMove(_mm_point(args["x"], args["y"]))
    picture.attemptResize(_mm_size(args.get("width", 15), args.get("height", 15)))




    north = "TrueNorth" if args.get("north") == "true" else "GridNorth"
    if map_item:
        _set_north(picture, map_item, north)

    layout.addLayoutItem(picture)
    placement = place_item(layout, picture, {"x": args["x"], "y": args["y"], "width": args.get("width", 15),
                                             "height": args.get("height", 15)}, resizable=True)
    result = _with_new_item(layout, picture)
    result.update(placement)
    result["svg_path"] = svg_path
    if map_item:
        result["linked_map"] = map_item.uuid()
        result["north"] = "true" if north == "TrueNorth" else "grid"
    return result


def _add_layout_coordinate_grid(args: dict) -> dict:

    layout, error = _resolve_layout(args["layout_name"])
    if error:
        return error
    maps = [item for item in layout.items() if isinstance(item, QgsLayoutItemMap)]
    map_id = args.get("map_id")
    if map_id:
        map_item = next((item for item in maps if item.uuid() == map_id), None)
        if map_item is None:
            return {"_error": f"Map item not found: {map_id}", "code": "INVALID_ARGS",
                    "suggestion": f"Map items on this layout: {[item.uuid() for item in maps]}"}
    elif maps:
        map_item = max(maps, key=lambda item: item.rect().width() * item.rect().height())
    else:
        return {"_error": "This layout has no map item; add_layout_map adds one.", "code": "INVALID_ARGS"}

    stack = map_item.grids()
    grid = QgsLayoutItemMapGrid("Coordinate grid", map_item)
    grid.setEnabled(True)
    grid.setAnnotationEnabled(True)
    grid.setIntervalX(float(args["interval_x"]))
    grid.setIntervalY(float(args["interval_y"]))

    crs_text = args.get("crs")
    if crs_text:
        crs = QgsCoordinateReferenceSystem(crs_text)
        if not crs.isValid():
            return {"_error": f"Invalid grid CRS: {crs_text}", "code": "INVALID_ARGS",
                    "suggestion": "An authority id, e.g. EPSG:4326 or EPSG:2154."}
        grid.setCrs(crs)
    else:
        grid.setCrs(map_item.crs())

    grid_style = {
        "solid": "Solid", "cross": "Cross", "markers": "Markers",
        "frame_annotations_only": "FrameAnnotationsOnly",
    }
    frame_style = {
        "no_frame": "NoFrame", "zebra": "Zebra", "interior_ticks": "InteriorTicks",
        "exterior_ticks": "ExteriorTicks", "interior_exterior_ticks": "InteriorExteriorTicks",
        "line_border": "LineBorder", "line_border_nautical": "LineBorderNautical",
        "zebra_nautical": "ZebraNautical",
    }
    if args.get("grid_style"):
        grid.setStyle(enum_member(QgsLayoutItemMapGrid, "GridStyle", grid_style[args["grid_style"]]))
    if args.get("frame_style"):
        grid.setFrameStyle(enum_member(QgsLayoutItemMapGrid, "FrameStyle", frame_style[args["frame_style"]]))
    if "frame_width" in args:
        grid.setFrameWidth(float(args["frame_width"]))
    side_names = {"left": "Left", "right": "Right", "top": "Top", "bottom": "Bottom"}
    if "frame_sides" in args:


        for side, member in side_names.items():
            grid.setFrameSideFlag(enum_member(QgsLayoutItemMapGrid, "FrameSideFlag", "Frame" + member),
                                  side in args["frame_sides"])

    display_names = {"show_all": "ShowAll", "latitude_only": "LatitudeOnly",
                     "longitude_only": "LongitudeOnly", "hide_all": "HideAll"}
    if args.get("annotations"):
        display = enum_member(QgsLayoutItemMapGrid, "DisplayMode", display_names[args["annotations"]])
        for member in side_names.values():
            grid.setAnnotationDisplay(display, enum_member(QgsLayoutItemMapGrid, "BorderSide", member))
    if "annotation_position" in args:
        inside = args["annotation_position"] == "inside"
        position = enum_member(QgsLayoutItemMapGrid, "AnnotationPosition",
                               "InsideMapFrame" if inside else "OutsideMapFrame")
        for member in side_names.values():
            grid.setAnnotationPosition(position, enum_member(QgsLayoutItemMapGrid, "BorderSide", member))
    if "annotation_precision" in args:
        grid.setAnnotationPrecision(int(args["annotation_precision"]))
    if args.get("annotation_format"):
        format_names = {
            "decimal": "Decimal", "degree_minute": "DegreeMinute", "degree_minute_second": "DegreeMinuteSecond",
            "decimal_suffix": "DecimalWithSuffix", "degree_minute_no_suffix": "DegreeMinuteNoSuffix",
            "degree_minute_padded": "DegreeMinutePadded",
            "degree_minute_second_no_suffix": "DegreeMinuteSecondNoSuffix",
            "degree_minute_second_padded": "DegreeMinuteSecondPadded",
        }
        grid.setAnnotationFormat(
            enum_member(QgsLayoutItemMapGrid, "AnnotationFormat", format_names[args["annotation_format"]]))
    if args.get("annotation_direction"):
        direction_names = {"horizontal": "Horizontal", "vertical": "Vertical",
                           "vertical_descending": "VerticalDescending", "boundary": "BoundaryDirection",
                           "above_tick": "AboveTick", "on_tick": "OnTick", "under_tick": "UnderTick"}
        direction = enum_member(QgsLayoutItemMapGrid, "AnnotationDirection",
                                direction_names[args["annotation_direction"]])
        for member in side_names.values():
            border = enum_member(QgsLayoutItemMapGrid, "BorderSide", member)
            try:
                grid.setAnnotationDirection(direction, border)
            except TypeError:
                grid.setAnnotationDirection(direction)




    for existing in list(stack.asList()):
        if existing.name() == "Coordinate grid":
            stack.removeGrid(existing.id())
    stack.addGrid(grid)
    return {
        "layout_name": layout.name(), "map_id": map_item.uuid(), "grid_id": grid.id(),
        "grid_name": grid.name(), "interval_x": grid.intervalX(), "interval_y": grid.intervalY(),
        "crs": grid.crs().authid(), "frame_style": args.get("frame_style", "no_frame"),
        "annotations": args.get("annotations", "show_all"), "layout": _layout_summary(layout),
    }


def _label_points(label) -> float:

    try:
        text_format = label.textFormat()
        units = text_format.sizeUnit()
        points = enum_member(Qgis, "RenderUnit", "Points", None) if Qgis is not None else None
        if points is None:
            points = enum_member(QgsUnitTypes, "RenderUnit", "RenderPoints", None)
        if points is None or units == points:
            return float(text_format.size())
    except (AttributeError, RuntimeError, TypeError):
        pass
    try:
        return float(label.font().pointSizeF())
    except (AttributeError, RuntimeError, TypeError):
        return 0.0




_TITLE_POINTS = 13.0


def _exported(item) -> bool:
    try:
        return bool(item.isVisible()) and not bool(item.excludeFromExports())
    except (AttributeError, RuntimeError):
        return False


def _is_north_arrow(item) -> bool:
    if not isinstance(item, QgsLayoutItemPicture):
        return False
    try:
        path = os.path.basename(str(item.picturePath() or "")).lower()
        return item.linkedMap() is not None or "north" in path or "arrow" in path
    except (AttributeError, RuntimeError):
        return False


def _hand_drawn(items) -> list:









    drawn = []
    for item in items:
        if isinstance(item, (QgsLayoutItemMap, QgsLayoutItemLegend, QgsLayoutItemScaleBar)):
            continue
        if _is_north_arrow(item):
            continue
        if isinstance(item, QgsLayoutItemLabel) and _label_points(item) >= _TITLE_POINTS:
            continue
        if type(item).__name__ == "QgsLayoutItemGroup":
            continue
        drawn.append(item)
    return drawn


def furniture_check(layout):












    try:
        items = [item for item in layout.items()
                 if isinstance(item, QgsLayoutItem) and not isinstance(item, QgsLayoutItemPage) and _exported(item)]
        maps = [item for item in items if isinstance(item, QgsLayoutItemMap)]
        if not maps:
            return None
        main = _first_map_item(layout)
        labels = [item for item in items if isinstance(item, QgsLayoutItemLabel) and str(item.text() or "").strip()]
        legends = [item for item in items if isinstance(item, QgsLayoutItemLegend)]
        bars = [item for item in items if isinstance(item, QgsLayoutItemScaleBar)]
        arrows = [item for item in items if _is_north_arrow(item)]
        has = {"title": any(_label_points(label) >= _TITLE_POINTS for label in labels),
               "legend": bool(legends), "scale bar": bool(bars), "north arrow": bool(arrows)}



        drawn = _hand_drawn(items)
        absent = [element for element, ok in has.items() if not ok]
        unknown = [element for element in absent if element != "title"] if drawn else []
        out = {"present": [element for element, ok in has.items() if ok],
               "missing": [element for element in absent if element not in unknown]}
        if unknown:
            out["unknown"] = unknown
            out["hand_drawn_items"] = [_item_name(item) for item in drawn[:10]]
            out["unknown_note"] = (
                "This page carries " + ", ".join(out["hand_drawn_items"][:4])
                + ", any of which may be a " + " or a ".join(unknown)
                + " drawn by hand. QGIS has no such item here, so this check cannot tell: render_map (target "
                  "layout, or target file on the export) shows the page as printed; a hand-drawn one already "
                  "there means adding another duplicates it.")

        links = []
        for kind, group in (("scale bar", bars), ("north arrow", arrows), ("legend", legends)):
            for item in group:
                linked = item.linkedMap()
                if linked is None and kind == "scale bar":
                    links.append(f"The scale bar {item.uuid()} follows no map.")
                elif linked is not None and main is not None and len(maps) > 1 and linked.uuid() != main.uuid():
                    links.append(f"The {kind} {item.uuid()} follows {_item_name(linked)} {linked.uuid()}, "
                                 f"not the main map {main.uuid()}.")
        if links:
            out["links"] = links

        overlays = [item for item in items if not _is_surface(item)
                    and type(item).__name__ != "QgsLayoutItemGroup"]
        overlaps = []
        for first_index, first in enumerate(overlays):
            first_rect = first.sceneBoundingRect()
            for second in overlays[first_index + 1:]:
                second_rect = second.sceneBoundingRect()
                if not _overlap(first_rect, second_rect):
                    continue

                framed = any(isinstance(shape, QgsLayoutItemShape) and rect.adjusted(-0.01, -0.01, 0.01, 0.01)
                             .contains(inner) for shape, rect, inner in ((first, first_rect, second_rect),
                                                                         (second, second_rect, first_rect)))
                if not framed:
                    overlaps.append(f"{_item_name(first)} and {_item_name(second)}")
        if overlaps:
            out["overlaps"] = overlaps[:10]

        basemaps = []
        for legend in legends:
            for node in legend.model().rootGroup().findLayers():
                if _is_tile_basemap(node.layer()) and not _heading_hidden(node):
                    basemaps.append(node.layer().name())
        if basemaps:
            out["legend_basemaps"] = sorted(set(basemaps))
        if out["missing"] or links or overlaps or basemaps:
            out["status"] = "incomplete"
        else:


            out["status"] = "unsure" if unknown else "complete"
        return out
    except Exception as exc:  # noqa: BLE001
        return {"status": "unknown", "warning": f"Could not check the sheet's furniture: {exc}"}


def _get_layout_info(args: dict) -> dict:
    layout, error = _resolve_layout(args["layout_name"], allow_report=True)
    if error:
        return error
    if getattr(layout, "pageCollection", None) is None:
        from .report_tools import report_summary

        return report_summary(layout)
    result = _layout_summary(layout)
    result["edit_in_place"] = ("A uuid as item_id on add_layout_map, add_layout_label, add_layout_scalebar or "
                               "add_layout_north_arrow, or as legend_id on add_layout_legend, changes that item.")
    from ..core.layout_quality import assess_layout
    result["layout_checks"] = assess_layout(layout)
    furniture = furniture_check(layout)
    if furniture is not None:
        result["furniture_check"] = furniture
    return result


def _follows(item, map_uuid: str) -> bool:

    try:
        linked = item.linkedMap() if hasattr(item, "linkedMap") else None
        return linked is not None and linked.uuid() == map_uuid
    except (AttributeError, RuntimeError, TypeError):
        return False


def _remove_print_layout(args: dict) -> dict:
    layout, error = _resolve_layout(args["layout_name"], allow_report=not _editing(args))
    if error:
        return error
    if _editing(args):
        item, error = _find_item(layout, args["item_id"], None, "item")
        if error:
            return error
        gone = {"uuid": item.uuid(), "type": type(item).__name__, "name": _item_name(item)}
        followers = [_item_name(other) for other in layout.items()
                     if other is not item and _follows(other, gone["uuid"])]
        layout.removeLayoutItem(item)
        result = _layout_summary(layout)
        result["removed_item"] = gone
        if followers:
            result["unlinked"] = followers
            result["unlinked_note"] = "These followed the map removed and now follow none."
        return result
    name = layout.name()
    QgsProject.instance().layoutManager().removeLayout(layout)
    return {"removed": name}
