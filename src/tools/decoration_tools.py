# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


















from __future__ import annotations

import math
import os

from qgis.core import (
    QgsProject,
    QgsReadWriteContext,
    QgsSymbolLayerUtils,
    QgsTextFormat,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP, QMetaObject
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtXml import QDomDocument

from ..core.logger import log_warning
from ..core.security import validate_path
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .style_tools import _color_error

TOOL = "set_canvas_decoration"


_ITEMS = {
    "title": ("TitleLabel", "QgsDecorationTitle"),
    "copyright": ("CopyrightLabel", "QgsDecorationCopyright"),
    "north_arrow": ("NorthArrow", "QgsDecorationNorthArrow"),
    "scale_bar": ("ScaleBar", "QgsDecorationScaleBar"),
    "grid": ("Grid", "QgsDecorationGrid"),
    "image": ("Image", "QgsDecorationImage"),
    "layout_extent": ("LayoutExtent", "QgsDecorationLayoutExtent"),
}
DECORATIONS = tuple(_ITEMS)


PLACEMENTS = ("bottom_left", "top_left", "top_right", "bottom_right", "top_center", "bottom_center")

_DEFAULT_PLACEMENT = {"title": "top_center", "copyright": "bottom_right", "north_arrow": "bottom_left",
                      "scale_bar": "top_left", "image": "bottom_left", "layout_extent": "bottom_right"}

_MARGIN_UNITS = {"mm": "MM", "pixels": "Pixel", "percent": "Percentage"}

SCALE_BAR_STYLES = ("ticks_down", "ticks_up", "bar", "box")

GRID_STYLES = ("lines", "markers")

_MARGINS = {"placement", "margin_x", "margin_y", "margin_unit"}


_APPLIES = {
    "title": _MARGINS | {"text", "color", "background_color", "font_size"},
    "copyright": _MARGINS | {"text", "color", "font_size"},
    "north_arrow": _MARGINS | {"color", "outline_color", "size", "rotation", "path"},
    "scale_bar": _MARGINS | {"color", "outline_color", "size", "style", "snap", "font_size"},
    "grid": {"style", "interval", "interval_y", "color", "show_labels", "font_size"},
    "image": _MARGINS | {"path", "size"},
    "layout_extent": {"show_labels"},
}
_SETTINGS = sorted(set().union(*_APPLIES.values()))


def register_decoration_tools(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="set_canvas_decoration",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Map decoration[ {decoration}]"),
        input_schema={
            "type": "object",
            "properties": {
                "decoration": {"type": "string", "enum": list(DECORATIONS)},
                "enabled": {"type": "boolean"},
                "read": {"type": "boolean"},
                "placement": {"type": "string", "enum": list(PLACEMENTS)},
                "text": {"type": "string"},
                "color": {"type": "string"},
                "background_color": {"type": "string"},
                "outline_color": {"type": "string"},
                "font_size": {"type": "number", "minimum": 1, "maximum": 200},
                "size": {"type": "number", "minimum": 1, "maximum": 500},
                "style": {"type": "string", "enum": list(SCALE_BAR_STYLES + GRID_STYLES)},
                "snap": {"type": "boolean"},
                "rotation": {"type": "number", "minimum": 0, "maximum": 360},
                "path": {"type": "string"},
                "interval": {"type": "number", "exclusiveMinimum": 0},
                "interval_y": {"type": "number", "exclusiveMinimum": 0},
                "show_labels": {"type": "boolean"},
                "margin_x": {"type": "integer", "minimum": 0, "maximum": 1000},
                "margin_y": {"type": "integer", "minimum": 0, "maximum": 1000},
                "margin_unit": {"type": "string", "enum": list(_MARGIN_UNITS)},
            },
            "additionalProperties": False,
        },
        handler=_set_canvas_decoration,

        reads_when=lambda args: bool(args.get("read") or not args.get("decoration")),
    ))






def _project():
    return QgsProject.instance()


def _read_str(scope: str, key: str, default: str = "") -> str:
    value = _project().readEntry(scope, key, default)
    return value[0] if isinstance(value, tuple) else value


def _read_int(scope: str, key: str, default: int) -> int:
    value = _project().readNumEntry(scope, key, default)
    return int(value[0] if isinstance(value, tuple) else value)


def _read_float(scope: str, key: str, default: float) -> float:
    value = _project().readDoubleEntry(scope, key, default)
    return float(value[0] if isinstance(value, tuple) else value)


def _read_bool(scope: str, key: str, default: bool) -> bool:
    value = _project().readBoolEntry(scope, key, default)
    return bool(value[0] if isinstance(value, tuple) else value)


def _write(scope: str, key: str, value) -> None:

    project = _project()
    if isinstance(value, bool):
        project.writeEntryBool(scope, key, value)
    elif isinstance(value, float):
        project.writeEntryDouble(scope, key, value)
    elif isinstance(value, int):
        project.writeEntry(scope, key, int(value))
    else:
        project.writeEntry(scope, key, str(value))


def _color_to_string(color: QColor) -> str:
    utils = _color_utils()
    if utils is not None and hasattr(utils, "colorToString"):
        return utils.colorToString(color)
    return QgsSymbolLayerUtils.encodeColor(color)


def _color_from_string(text: str) -> QColor:
    utils = _color_utils()
    if utils is not None and hasattr(utils, "colorFromString"):
        return utils.colorFromString(text)
    return QgsSymbolLayerUtils.decodeColor(text)


def _color_utils():
    try:
        from qgis.core import QgsColorUtils
        return QgsColorUtils
    except ImportError:
        return None


def _hex(color: QColor) -> str:

    rgb = f"{color.red():02x}{color.green():02x}{color.blue():02x}"
    return f"#{rgb}" if color.alpha() == 255 else f"#{color.alpha():02x}{rgb}"


def _context() -> QgsReadWriteContext:
    context = QgsReadWriteContext()
    context.setPathResolver(_project().pathResolver())
    return context


def _read_text_format(scope: str, key: str) -> tuple[QgsTextFormat, bool]:

    fmt = QgsTextFormat()
    xml = _read_str(scope, key)
    if not xml:
        return fmt, False
    doc = QDomDocument()
    doc.setContent(xml)
    element = doc.documentElement()
    if element.isNull():
        return fmt, False
    fmt.readXml(element, _context())
    return fmt, True


def _write_text_format(scope: str, key: str, fmt: QgsTextFormat) -> None:
    doc = QDomDocument()
    doc.appendChild(fmt.writeXml(doc, _context()))
    _write(scope, key, doc.toString())


def _write_symbol(scope: str, key: str, name: str, symbol) -> None:
    doc = QDomDocument()
    doc.appendChild(QgsSymbolLayerUtils.saveSymbol(name, symbol, doc, _context()))
    _write(scope, key, doc.toString())


def _read_symbol(scope: str, key: str, loader):
    xml = _read_str(scope, key)
    if not xml:
        return None
    doc = QDomDocument()
    doc.setContent(xml)
    element = doc.documentElement()
    if element.isNull():
        return None
    try:
        return loader(element, _context())
    except Exception:  # noqa: BLE001
        return None


def _enum_int(value) -> int:
    return int(getattr(value, "value", value))






def _margins(scope: str, decoration: str) -> dict:
    placement = _read_int(scope, "/Placement", PLACEMENTS.index(_DEFAULT_PLACEMENT[decoration]))
    unit = _read_str(scope, "/MarginUnit", "MM")
    return {
        "placement": PLACEMENTS[placement] if 0 <= placement < len(PLACEMENTS) else _DEFAULT_PLACEMENT[decoration],
        "margin_x": _read_int(scope, "/MarginH", 0),
        "margin_y": _read_int(scope, "/MarginV", 0),
        "margin_unit": next((name for name, code in _MARGIN_UNITS.items() if code == unit), "mm"),
    }


def _state(decoration: str) -> dict:

    scope = _ITEMS[decoration][0]
    state = {"decoration": decoration, "enabled": _read_bool(scope, "/Enabled", False)}
    if decoration in ("title", "copyright"):
        fmt, _stored = _read_text_format(scope, "/Font")
        state.update(_margins(scope, decoration))
        state.update(text=_read_str(scope, "/Label"), color=_hex(fmt.color()), font_size=round(fmt.size(), 2))
        if decoration == "title":
            state["background_color"] = _hex(_color_from_string(_read_str(scope, "/BackgroundColor", "0,0,0,99")))
    elif decoration == "north_arrow":
        state.update(_margins(scope, decoration))
        automatic = _read_bool(scope, "/Automatic", True)
        state.update(size=_read_float(scope, "/Size", 16.0),
                     color=_hex(_color_from_string(_read_str(scope, "/Color", "#000000"))),
                     outline_color=_hex(_color_from_string(_read_str(scope, "/OutlineColor", "#FFFFFF"))),
                     rotation="automatic" if automatic else _read_int(scope, "/Rotation", 0),
                     path=_read_str(scope, "/SvgPath") or "(QGIS default arrow)")
    elif decoration == "scale_bar":
        state.update(_margins(scope, decoration))
        style = _read_int(scope, "/Style", 0)
        fmt, _stored = _read_text_format(scope, "/TextFormat")
        state.update(style=SCALE_BAR_STYLES[style] if 0 <= style < len(SCALE_BAR_STYLES) else "ticks_down",
                     size=_read_int(scope, "/PreferredSize", 30),
                     snap=_read_bool(scope, "/Snapping", True),
                     color=_hex(_color_from_string(_read_str(scope, "/Color", "#000000"))),
                     outline_color=_hex(_color_from_string(_read_str(scope, "/OutlineColor", "#FFFFFF"))),
                     font_size=round(fmt.size(), 2))
    elif decoration == "grid":
        style = _read_int(scope, "/Style", 0)
        state.update(style=GRID_STYLES[style] if 0 <= style < len(GRID_STYLES) else "lines",
                     interval=_read_float(scope, "/IntervalX", 10.0),
                     interval_y=_read_float(scope, "/IntervalY", 10.0),
                     show_labels=_read_bool(scope, "/ShowAnnotation", False))
    elif decoration == "image":
        state.update(_margins(scope, decoration))
        state.update(path=_read_str(scope, "/ImagePath"), size=_read_float(scope, "/Size", 16.0))
    elif decoration == "layout_extent":
        state["show_labels"] = _read_bool(scope, "/Labels", True)
    return state


def _active() -> list:
    return [name for name in DECORATIONS if _read_bool(_ITEMS[name][0], "/Enabled", False)]






def _main_window():
    try:
        from qgis.utils import iface
    except ImportError:
        return None, None
    if iface is None:
        return None, None
    return iface, iface.mainWindow()


def _item(decoration: str):

    _iface, window = _main_window()
    if window is None:
        return None
    wanted = _ITEMS[decoration][1]
    for child in window.children():
        try:
            if child.metaObject().className() == wanted:
                return child
        except (AttributeError, RuntimeError):
            continue
    return None


def _apply(decoration: str, item) -> bool:


    if QMetaObject.invokeMethod(item, "projectRead") is False:
        return False
    iface, _window = _main_window()
    canvas = iface.mapCanvas() if iface is not None else None
    if canvas is not None:
        canvas.refresh()
    return True






def _invalid(message: str, suggestion: str) -> dict:
    return tool_error(message, "INVALID_ARGS", suggestion)


def _nice_interval(span: float) -> float:

    raw = span / 5.0
    if raw <= 0 or not math.isfinite(raw):
        return 0.0
    factor = 10 ** math.floor(math.log10(raw))
    return round(raw / factor) * factor


def _check_args(decoration: str, args: dict) -> dict | None:
    given = [key for key in _SETTINGS if args.get(key) is not None]
    foreign = [key for key in given if key not in _APPLIES[decoration]]
    if foreign:
        return _invalid(
            f"The {decoration} decoration has no {', '.join(foreign)}.",
            f"Settings of {decoration}: {', '.join(sorted(_APPLIES[decoration]))}.")
    style = args.get("style")
    if style is not None:
        allowed = SCALE_BAR_STYLES if decoration == "scale_bar" else GRID_STYLES
        if style not in allowed:
            return _invalid(f"style {style!r} is not a {decoration} style.", f"Valid: {', '.join(allowed)}.")
    for key in ("color", "background_color", "outline_color"):
        bad = _color_error(args.get(key), key)
        if bad:
            return bad
    path = args.get("path")
    if path is not None:
        why = validate_path(str(path))
        if why:
            return _invalid(why, "The full path of an image file on this computer.")
        if not os.path.isfile(str(path)):
            return _invalid(f"No file at {path}.", "The full path of an existing SVG, PNG or JPEG file.")
    return None


def _write_margins(scope: str, decoration: str, args: dict, current: dict) -> None:
    placement = args.get("placement") or current["placement"]
    _write(scope, "/Placement", PLACEMENTS.index(placement))
    _write(scope, "/MarginH", int(args["margin_x"] if args.get("margin_x") is not None else current["margin_x"]))
    _write(scope, "/MarginV", int(args["margin_y"] if args.get("margin_y") is not None else current["margin_y"]))
    _write(scope, "/MarginUnit", _MARGIN_UNITS[args.get("margin_unit") or current["margin_unit"]])


def _label_format(scope: str, key: str, args: dict, default_size: float | None = None) -> QgsTextFormat:
    fmt, stored = _read_text_format(scope, key)
    if not stored and default_size:
        fmt.setSize(default_size)
    if args.get("font_size") is not None:
        fmt.setSize(float(args["font_size"]))
    if args.get("color"):
        fmt.setColor(QColor(str(args["color"])))
    return fmt


def _write_settings(decoration: str, args: dict, current: dict) -> dict | None:

    scope = _ITEMS[decoration][0]
    if decoration in ("title", "copyright"):
        text = args.get("text") if args.get("text") is not None else current["text"]
        if not str(text or "").strip():
            return _invalid(f"A {decoration} decoration needs its text.",
                            "For example the map title or 'Data: OpenStreetMap contributors'. "
                            "QGIS expressions in [% %] work, such as [% @project_title %].")
        _write_margins(scope, decoration, args, current)
        _write(scope, "/Label", str(text))

        _write_text_format(scope, "/Font", _label_format(scope, "/Font", args, 16.0 if decoration == "title" else None))
        if decoration == "title":
            background = args.get("background_color")
            _write(scope, "/BackgroundColor", _color_to_string(QColor(str(background))) if background
                   else _read_str(scope, "/BackgroundColor", "0,0,0,99"))
    elif decoration == "north_arrow":
        _write_margins(scope, decoration, args, current)
        _write(scope, "/Size", float(args["size"] if args.get("size") is not None else current["size"]))
        for key, entry in (("color", "/Color"), ("outline_color", "/OutlineColor")):
            _write(scope, entry, _color_to_string(QColor(str(args.get(key) or current[key]))))
        if args.get("rotation") is not None:
            _write(scope, "/Rotation", int(round(float(args["rotation"]))) % 360)
            _write(scope, "/Automatic", False)
        if args.get("path"):
            _write(scope, "/SvgPath", os.path.abspath(str(args["path"])))
    elif decoration == "scale_bar":
        _write_margins(scope, decoration, args, current)
        _write(scope, "/Style", SCALE_BAR_STYLES.index(args.get("style") or current["style"]))
        _write(scope, "/PreferredSize", int(round(float(args["size"] if args.get("size") is not None
                                                          else current["size"]))))
        _write(scope, "/Snapping", bool(args["snap"]) if args.get("snap") is not None else current["snap"])
        for key, entry in (("color", "/Color"), ("outline_color", "/OutlineColor")):
            _write(scope, entry, _color_to_string(QColor(str(args.get(key) or current[key]))))
        _write_text_format(scope, "/TextFormat", _label_format(scope, "/TextFormat", args))
    elif decoration == "grid":
        return _write_grid(scope, args, current)
    elif decoration == "image":
        path = args.get("path") or current["path"]
        if not path:
            return _invalid("An image decoration needs the image file.", "path takes an SVG, PNG or JPEG file.")
        _write_margins(scope, decoration, args, current)
        _write(scope, "/ImagePath", os.path.abspath(str(path)))
        _write(scope, "/Size", float(args["size"] if args.get("size") is not None else current["size"]))
    elif decoration == "layout_extent":
        _write(scope, "/Labels", bool(args["show_labels"]) if args.get("show_labels") is not None
               else current["show_labels"])
    return None


def _decimal_places(value: float, max_places: int = 6) -> int:









    value = abs(float(value))
    for places in range(max_places + 1):
        if abs(round(value, places) - value) < 1e-9:
            return places
    return max_places


def _write_grid(scope: str, args: dict, current: dict) -> dict | None:
    from qgis.core import Qgis, QgsLineSymbol, QgsMarkerSymbol, QgsSimpleMarkerSymbolLayer

    from ..core.qt_compat import enum_member

    iface, _window = _main_window()
    canvas = iface.mapCanvas() if iface is not None else None
    if canvas is None:
        return tool_error("The map canvas is not available.", "EXECUTION_FAILED", "")
    units = _enum_int(canvas.mapSettings().mapUnits())
    extent = canvas.extent()



    same_units = _read_int(scope, "/MapUnits", -1) == units
    interval_x = args.get("interval") or (current["interval"] if same_units else _nice_interval(extent.width()))
    interval_y = args.get("interval_y") or args.get("interval") or (
        current["interval_y"] if same_units else _nice_interval(extent.height()))
    if not interval_x or not interval_y:
        return _invalid("The grid spacing could not be read from the view.", "interval is in map units.")
    _write(scope, "/MapUnits", units)
    _write(scope, "/Style", GRID_STYLES.index(args.get("style") or current["style"]))
    _write(scope, "/IntervalX", float(interval_x))
    _write(scope, "/IntervalY", float(interval_y))
    show = bool(args["show_labels"]) if args.get("show_labels") is not None else current["show_labels"]
    _write(scope, "/ShowAnnotation", show)

    precision = max(_decimal_places(interval_x), _decimal_places(interval_y))
    _write(scope, "/AnnotationPrecision", precision)
    if args.get("font_size") is not None:
        fmt, _stored = _read_text_format(scope, "/Font")
        fmt.setSize(float(args["font_size"]))
        _write_text_format(scope, "/Font", fmt)
    color = args.get("color")
    if color:
        line = _read_symbol(scope, "/LineSymbol", QgsSymbolLayerUtils.loadSymbol) or QgsLineSymbol()
        line.setColor(QColor(str(color)))
        _write_symbol(scope, "/LineSymbol", "line symbol", line)
        marker = _read_symbol(scope, "/MarkerSymbol", QgsSymbolLayerUtils.loadSymbol) or QgsMarkerSymbol(


            [QgsSimpleMarkerSymbolLayer(enum_member(Qgis, "MarkerShape", "Cross", 9), 3, 0)])
        marker.setColor(QColor(str(color)))
        _write_symbol(scope, "/MarkerSymbol", "marker symbol", marker)
    return None


_NOTE = ("Canvas decorations (View > Decorations) are drawn on the map canvas, saved with the project, "
         "and drawn by QGIS's Export Map to Image and by animation exports. They are not part of a print "
         "layout: a layout has its own title, scale bar and north arrow items.")


def _set_canvas_decoration(args: dict) -> dict:
    decoration = args.get("decoration")
    if args.get("read") or not decoration:
        names = [decoration] if decoration else list(DECORATIONS)
        return {"decorations": [_state(name) for name in names], "active": _active(), "note": _NOTE}

    error = _check_args(decoration, args)
    if error:
        return error
    item = _item(decoration)
    if item is None:
        return tool_error(
            "Canvas decorations need the QGIS desktop window, and its decoration items were not found.",
            "EXECUTION_FAILED", "create_print_layout gives a title, scale bar or north arrow off the canvas.")

    scope = _ITEMS[decoration][0]
    enabled = args.get("enabled") is not False
    current = _state(decoration)
    if enabled:
        error = _write_settings(decoration, args, current)
        if error:
            return error
    elif any(args.get(key) is not None for key in _SETTINGS):

        error = _write_settings(decoration, args, current)
        if error:
            return error
    _write(scope, "/Enabled", enabled)
    try:
        applied = _apply(decoration, item)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"{TOOL}: {decoration} not re-read: {exc}")
        applied = False
    result = {"decoration": decoration, "enabled": enabled, "settings": _state(decoration),
              "active": _active(), "note": _NOTE}
    if not applied:
        result["warning"] = ("The settings are saved in the project, but QGIS did not redraw the decoration; "
                             "it shows after the project is reopened.")
    return result
