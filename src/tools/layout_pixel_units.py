# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



















from __future__ import annotations

from qgis.core import (
    Qgis,
    QgsMessageLog,
    QgsPalLayerSettings,
    QgsProject,
    QgsRenderContext,
    QgsUnitTypes,
)
from qgis.PyQt.QtCore import QPointF, QSizeF

from ._compat import enum_value

REFERENCE_DPI = 96.0
_MM_PER_PIXEL = 25.4 / REFERENCE_DPI
_POINTS_PER_PIXEL = 72.0 / REFERENCE_DPI

_PIXELS = enum_value((Qgis, "RenderUnit.Pixels"), (QgsUnitTypes, "RenderPixels"))
_MILLIMETERS = enum_value((Qgis, "RenderUnit.Millimeters"), (QgsUnitTypes, "RenderMillimeters"))
_POINTS = enum_value((Qgis, "RenderUnit.Points"), (QgsUnitTypes, "RenderPoints"))


_VALUE_ALIASES = {
    "customDashPattern": "customDashVector",
    "offset": "offsetDistance",
    "randomDeviationX": "maximumRandomDeviationX",
    "randomDeviationY": "maximumRandomDeviationY",
    "distance": "maxDistance",
    "averageAngle": "averageAngleLength",
}

_SQUARED = {"densityArea"}

_NOT_A_SIZE = {"output", "rotation"}


_PAL_SIZES = (
    (("dist",), "distUnits", "labeldistance"),
    (("xOffset", "yOffset"), "offsetUnits", "offsetxy"),
    (("repeatDistance",), "repeatDistanceUnit", "repeatdistance"),
    (("overrunDistance",), "overrunDistanceUnit", "overrundistance"),
)

_TEXT_PARTS = (("buffer", "setBuffer", "buffer"), ("background", "setBackground", "shape"),
               ("shadow", "setShadow", "shadow"), ("mask", "setMask", "mask"))


def _setter(name: str) -> str:
    return "set" + name[0].upper() + name[1:]


def _property_names(owner) -> dict:

    names = {}
    scope = getattr(type(owner), "Property", None)
    for source, strip in ((scope, ""), (type(owner), "Property")):
        if source is None:
            continue
        for attr in dir(source):
            if attr.startswith("_") or (strip and not attr.startswith(strip)) or attr == "Property":
                continue
            member = getattr(source, attr, None)
            try:
                key = int(getattr(member, "value", member))
            except (TypeError, ValueError):
                continue
            names.setdefault(key, attr[len(strip):].lower())
    return names


def _active(owner) -> set:

    try:
        props = owner.dataDefinedProperties()
        names = _property_names(owner)
        return {names.get(int(key), "") for key in props.propertyKeys() if props.isActive(key)} - {""}
    except (AttributeError, RuntimeError, TypeError):
        return set()


def _data_defined(base: str, active: set) -> bool:
    base = base.lower()
    return any(name and (base in name or name in base) for name in active)


def _scaled(value, factor: float):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value * factor
    if isinstance(value, QPointF):
        return QPointF(value.x() * factor, value.y() * factor)
    if isinstance(value, QSizeF):
        return QSizeF(value.width() * factor, value.height() * factor)
    if isinstance(value, (list, tuple)) and all(isinstance(v, (int, float)) for v in value):
        return [v * factor for v in value]
    return None


def _unit_pairs(obj):

    for name in dir(obj):
        if not name.endswith("Unit") or name.startswith(("set", "_")) or not hasattr(obj, _setter(name)):
            continue
        base = name[:-4]
        if base and base not in _NOT_A_SIZE:
            yield base, name


class _Tally:


    def __init__(self, layer_name: str):
        self.layer = layer_name
        self.labels = 0
        self.symbols = 0
        self.kept = []

    def keep(self, what: str) -> None:
        if what not in self.kept:
            self.kept.append(what)


def _convert_pairs(obj, target, factor: float, active: set, tally: _Tally, kind: str, where: str) -> int:

    count = 0
    for base, unit_name in _unit_pairs(obj):
        try:
            if getattr(obj, unit_name)() != _PIXELS:
                continue
        except (TypeError, RuntimeError):
            continue
        value_name = base if hasattr(obj, base) and hasattr(obj, _setter(base)) else _VALUE_ALIASES.get(base, "")
        if _data_defined(base, active):
            tally.keep(f"data-defined {where} {base}")
            continue
        if not value_name or not hasattr(obj, value_name) or not hasattr(obj, _setter(value_name)):
            tally.keep(f"{where} {base}")
            continue
        try:
            scaled = _scaled(getattr(obj, value_name)(), factor * factor if base in _SQUARED else factor)
            if scaled is None:
                tally.keep(f"{where} {base}")
                continue
            getattr(obj, _setter(value_name))(scaled)
            getattr(obj, _setter(unit_name))(target)
        except (TypeError, RuntimeError, AttributeError):
            tally.keep(f"{where} {base}")
            continue
        count += 1
    if kind == "labels":
        tally.labels += count
    else:
        tally.symbols += count
    return count


def _convert_symbol(symbol, tally: _Tally) -> None:
    if symbol is None:
        return
    for layer in symbol.symbolLayers():
        _convert_pairs(layer, _MILLIMETERS, _MM_PER_PIXEL, _active(layer), tally, "symbols", "symbol")
        _convert_symbol(layer.subSymbol(), tally)


def _convert_renderer(renderer, tally: _Tally) -> None:

    _convert_pairs(renderer, _MILLIMETERS, _MM_PER_PIXEL, set(), tally, "symbols", "renderer")
    try:
        symbols = renderer.symbols(QgsRenderContext())
    except (AttributeError, RuntimeError, TypeError):
        symbols = []
    for symbol in symbols:
        _convert_symbol(symbol, tally)
    for extra in ("centerSymbol", "clusterSymbol"):
        getter = getattr(renderer, extra, None)
        if callable(getter):
            _convert_symbol(getter(), tally)


def _convert_text_format(text_format, active: set, tally: _Tally):

    fmt = type(text_format)(text_format)
    own = {name for name in active if not name.startswith(("buffer", "shape", "shadow", "mask", "callout"))}
    _convert_pairs(fmt, _POINTS, _POINTS_PER_PIXEL, own, tally, "labels", "label")
    for getter, setter, prefix in _TEXT_PARTS:
        if not hasattr(fmt, getter):
            continue
        part = getattr(fmt, getter)()
        part_active = {name[len(prefix):] for name in active if name.startswith(prefix)}
        _convert_pairs(part, _POINTS, _POINTS_PER_PIXEL, part_active, tally, "labels", f"label {getter}")

        for symbol_getter in ("markerSymbol", "fillSymbol"):
            symbol = getattr(part, symbol_getter, None)
            symbol = symbol() if callable(symbol) else None
            if symbol is not None:
                copy = symbol.clone()
                _convert_symbol(copy, tally)
                getattr(part, _setter(symbol_getter))(copy)
        getattr(fmt, setter)(part)
    return fmt


def _convert_pal(settings, tally: _Tally):

    pal = QgsPalLayerSettings(settings)
    active = _active(pal)
    pal.setFormat(_convert_text_format(pal.format(), active, tally))
    for values, unit_attr, dd_name in _PAL_SIZES:
        if not hasattr(pal, unit_attr) or getattr(pal, unit_attr) != _PIXELS:
            continue
        if _data_defined(dd_name, active):
            tally.keep(f"data-defined label {values[0]}")
            continue
        for value in values:
            setattr(pal, value, getattr(pal, value) * _POINTS_PER_PIXEL)
        setattr(pal, unit_attr, _POINTS)
        tally.labels += len(values)
    callout = pal.callout() if hasattr(pal, "callout") else None
    if callout is not None:
        copy = callout.clone()
        before = (tally.labels, tally.symbols)
        _convert_pairs(copy, _MILLIMETERS, _MM_PER_PIXEL, _active(copy), tally, "labels", "callout")
        for getter in ("lineSymbol", "markerSymbol", "fillSymbol"):
            symbol = getattr(copy, getter, None)
            if callable(symbol):
                _convert_symbol(symbol(), tally)
        if (tally.labels, tally.symbols) != before:
            pal.setCallout(copy)
    return pal


def _convert_rule(rule, tally: _Tally) -> None:
    if rule.settings() is not None:
        rule.setSettings(_convert_pal(rule.settings(), tally))
    for child in rule.children():
        _convert_rule(child, tally)


def _convert_labeling(labeling, tally: _Tally):

    if labeling is None:
        return None
    before = tally.labels
    copy = labeling.clone()
    symbols = tally.symbols
    if hasattr(copy, "rootRule"):
        _convert_rule(copy.rootRule(), tally)
    elif hasattr(copy, "styles") and hasattr(copy, "setStyles"):
        styles = copy.styles()
        for style in styles:
            style.setLabelSettings(_convert_pal(style.labelSettings(), tally))
        copy.setStyles(styles)
    elif hasattr(copy, "settings") and hasattr(copy, "setSettings"):
        copy.setSettings(_convert_pal(copy.settings(), tally))
    elif hasattr(copy, "textFormat") and hasattr(copy, "setTextFormat"):
        copy.setTextFormat(_convert_text_format(copy.textFormat(), set(), tally))
    else:
        return None
    return copy if (tally.labels, tally.symbols) != (before, symbols) else None


def _convert_layer_renderer(layer, tally: _Tally):

    renderer = layer.renderer() if hasattr(layer, "renderer") else None
    if renderer is None:
        return None
    before = tally.symbols
    copy = renderer.clone()
    if hasattr(copy, "styles") and hasattr(copy, "setStyles"):
        styles = copy.styles()
        for style in styles:
            symbol = style.symbol()
            if symbol is not None:
                symbol = symbol.clone()
                _convert_symbol(symbol, tally)
                style.setSymbol(symbol)
        copy.setStyles(styles)
    elif hasattr(copy, "symbols"):
        _convert_renderer(copy, tally)
    else:
        _convert_pairs(copy, _MILLIMETERS, _MM_PER_PIXEL, set(), tally, "symbols", "renderer")
    return copy if tally.symbols != before else None


def _locked_pixel_styles(item, layer_ids: set) -> list:






    try:
        if item.followVisibilityPreset():
            return _theme_pixel_styles(item.followVisibilityPresetName(), layer_ids)
        if not item.keepLayerStyles():
            return []
        overrides = item.layerStyleOverrides()
    except (AttributeError, RuntimeError):
        return []
    return [layer_id for layer_id, xml in overrides.items()
            if layer_id in layer_ids and '"Pixel"' in str(xml)]


def _theme_pixel_styles(theme: str, layer_ids: set) -> list:
    themes = QgsProject.instance().mapThemeCollection()
    if not themes.hasMapTheme(theme):
        return []
    out = []
    for record in themes.mapThemeState(theme).layerRecords():
        layer = record.layer()
        if layer is None or layer.id() not in layer_ids or not record.usingCurrentStyle:
            continue
        manager = layer.styleManager()
        if record.currentStyle == manager.currentStyle():
            continue
        if '"Pixel"' in manager.style(record.currentStyle).xmlData():
            out.append(layer.id())
    return out


def _pixel_diagrams(layer) -> bool:

    renderer = layer.diagramRenderer() if hasattr(layer, "diagramRenderer") else None
    if renderer is None:
        return False
    try:
        return any(_PIXELS in (settings.sizeType, settings.lineSizeUnit) for settings in renderer.diagramSettings())
    except (AttributeError, RuntimeError, TypeError):
        return False


def _with_group_children(layers) -> list:

    out, seen = [], set()
    pending = list(layers)
    while pending:
        layer = pending.pop(0)
        if layer is None or layer.id() in seen:
            continue
        seen.add(layer.id())
        out.append(layer)
        children = getattr(layer, "childLayers", None)
        if callable(children):
            pending.extend(children())
    return out


def _sheets(target) -> list:

    if hasattr(target, "items"):
        return [target]
    sheets = []
    for getter in ("header", "body", "footer"):
        sheet = getattr(target, getter, None)
        sheet = sheet() if callable(sheet) else None
        if sheet is not None:
            sheets.append(sheet)
    children = getattr(target, "childSections", None)
    for child in (children() if callable(children) else []):
        sheets.extend(_sheets(child))
    return sheets


class PixelSizeHold:


    def __init__(self, layout, dpi: float):
        self._swapped = []
        self._tallies = []
        self._locked = []
        self._dirty = None
        if layout is None or abs(float(dpi) - REFERENCE_DPI) < 0.5:
            return
        try:
            self._apply(layout)
        except Exception as exc:  # nosec B110
            QgsMessageLog.logMessage(f"Pixel sizes were not held for this export: {exc}", "AI Agent",
                                     level=Qgis.MessageLevel.Warning)
            self.restore()

    def _apply(self, layout) -> None:
        from qgis.core import QgsLayoutItemMap

        from .advanced_layouts import _map_layers

        maps = [item for sheet in _sheets(layout) for item in sheet.items() if isinstance(item, QgsLayoutItemMap)]
        layers = _with_group_children(layer for item in maps for layer in _map_layers(item))
        seen = {layer.id() for layer in layers}
        for item in maps:
            for layer_id in _locked_pixel_styles(item, seen):
                self._locked.append(f"{item.displayName()}: {QgsProject.instance().mapLayer(layer_id).name()}")
        project = QgsProject.instance()
        self._dirty = project.isDirty()
        for layer in layers:
            tally = _Tally(layer.name())
            renderer = _convert_layer_renderer(layer, tally)
            labeling = _convert_labeling(layer.labeling(), tally) if hasattr(layer, "labeling") else None
            if _pixel_diagrams(layer):
                tally.keep("diagram sizes")
            if renderer is None and labeling is None:
                if tally.kept:
                    self._tallies.append(tally)
                continue
            original_renderer = layer.renderer().clone() if renderer is not None else None
            original_labeling = layer.labeling().clone() if labeling is not None else None
            self._swapped.append((layer, original_renderer, original_labeling))
            if renderer is not None:
                layer.setRenderer(renderer)
            if labeling is not None:
                layer.setLabeling(labeling)
            self._tallies.append(tally)

    def restore(self) -> None:

        swapped, self._swapped = self._swapped, []
        for layer, renderer, labeling in swapped:
            try:
                if renderer is not None:
                    layer.setRenderer(renderer)
                if labeling is not None:
                    layer.setLabeling(labeling)
            except RuntimeError:
                continue
        if self._dirty is not None:
            project = QgsProject.instance()
            if project.isDirty() != self._dirty:
                project.setDirty(self._dirty)
            self._dirty = None

    def report(self) -> dict | None:

        converted, kept = [], []
        for tally in self._tallies:
            parts = []
            if tally.labels:
                parts.append(f"{tally.labels} label size{'s' if tally.labels != 1 else ''} as points")
            if tally.symbols:
                parts.append(f"{tally.symbols} symbol size{'s' if tally.symbols != 1 else ''} as millimetres")
            if parts:
                converted.append({"layer": tally.layer, "converted": ", ".join(parts)})
            kept.extend(f"{tally.layer}: {what}" for what in tally.kept)
        kept.extend(f"{where} (the map draws its own saved copy of this layer's style)" for where in self._locked)
        if not converted and not kept:
            return None
        out = {"reference_dpi": int(REFERENCE_DPI)}
        if converted:
            out["layers"] = converted
            out["note"] = ("Sizes set in pixels were drawn at their 96 dpi size for this export only, so "
                           "they read on paper as they do on screen; the layers' styles are unchanged.")
        if kept:
            out["left_in_pixels"] = kept[:10]
        return out
