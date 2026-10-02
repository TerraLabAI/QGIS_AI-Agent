# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





























from __future__ import annotations

import math
import statistics

from qgis.core import (
    Qgis,
    QgsDoubleRange,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsFeatureRequest,
    QgsMapLayerElevationProperties,
    QgsMeshLayer,
    QgsPointCloudLayer,
    QgsProject,
    QgsProperty,
    QgsPropertyCollection,
    QgsRasterLayer,
    QgsRenderContext,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core import limits, net
from ..core.background import run_on_main_thread
from ..core.qt_compat import enum_member
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .layer_lookup import _field_not_found_error, _find_layer, _is_qgis_null, _layer_not_found_error
from .query_tools import STATS_CHUNK

_RASTER_MODES = {"surface": "RepresentsElevationSurface", "fixed_range": "FixedElevationRange",
                 "range_per_band": "FixedRangePerBand"}
_MESH_MODES = {"from_vertices": "FromVertices", "fixed_range": "FixedElevationRange",
               "range_per_group": "FixedRangePerGroup"}
_MODES = ("surface", "fixed_range", "range_per_band", "from_vertices", "range_per_group", "off")
_CLAMPING = {"terrain": "Terrain", "relative": "Relative", "absolute": "Absolute"}
_BINDING = {"vertex": "Vertex", "centroid": "Centroid"}
_VECTOR_KEYS = ("clamping", "binding", "z_from", "extrusion", "extrusion_height", "height_from", "default_height")
_LEVEL_KEYS = ("mode", "band", "elevation_range", "level_ranges")
_POINTCLOUD_TOOL = "configure_pointcloud_style"
_RANGE = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}


def register_layer_elevation_tools(registry: ToolRegistry):

    registry.register(Tool(
        name="set_layer_elevation",
        danger="write",
        label=QT_TRANSLATE_NOOP(
        "AIAgent", "Set the elevation of {layer_name}[, extruded by {height_from}]"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "mode": {"type": "string", "enum": list(_MODES)},
                "band": {"type": "integer", "minimum": 1},
                "elevation_range": _RANGE,
                "level_ranges": {
                    "type": "array", "minItems": 1, "maxItems": 500,
                    "items": {"type": "object",
                              "properties": {"index": {"type": "integer", "minimum": 0},
                                             "lower": {"type": "number"}, "upper": {"type": "number"}},
                              "required": ["index", "lower", "upper"]},
                },
                "z_offset": {"type": "number"},
                "z_scale": {"type": "number", "exclusiveMinimum": 0},
                "clamping": {"type": "string", "enum": list(_CLAMPING)},
                "binding": {"type": "string", "enum": list(_BINDING)},
                "z_from": {"type": "string", "minLength": 1},
                "extrusion": {"type": "boolean"},
                "extrusion_height": {"type": "number", "exclusiveMinimum": 0},
                "height_from": {"type": "string", "minLength": 1},
                "default_height": {"type": "number", "exclusiveMinimum": 0},
            },
            "required": ["layer_name"],
        },
        handler=_set_layer_elevation,
        background=True,
    ))
    registry.register(Tool(
        name="configure_elevation_controller",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Filter the map by elevation"),
        input_schema={
            "type": "object",
            "properties": {
                "elevation_range": _RANGE,
                "step": {"type": "number", "exclusiveMinimum": 0},
                "invert": {"type": "boolean"},
                "current_range": _RANGE,
                "enabled": {"type": "boolean"},
            },
        },
        handler=_configure_elevation_controller,
    ))




def _member(owner, scope: str, name: str):
    return enum_member(owner, scope, name, None)


def _name_of(value, names: dict, owner, scope: str) -> str | None:

    for word, member in names.items():
        found = _member(owner, scope, member)
        if found is not None and found == value:
            return word
    return None


def _range_list(value: QgsDoubleRange | None) -> list | None:
    if value is None or value.isInfinite():
        return None
    return [_num(value.lower()), _num(value.upper())]


def _num(value: float) -> float:
    return round(float(value), 6)


def _pair(value, label: str) -> tuple:
    try:
        low, high = (float(v) for v in value)
    except (TypeError, ValueError):
        return None, tool_error(f"{label} must be two numbers, lower then upper.", "INVALID_ARGS",
                                f"{label} is [lower, upper], for example [0, 100].")
    if not (math.isfinite(low) and math.isfinite(high)) or low > high:
        return None, tool_error(f"{label} {value!r} is not a range: lower must not be above upper.",
                                "INVALID_ARGS", f"{label} is [lower, upper] with lower <= upper.")
    return (low, high), None


def _level_ranges(levels: list) -> dict:



    uppers = {high for _, low, high in levels if high > low}
    return {index: QgsDoubleRange(low, high, not (high > low and low in uppers), True)
            for index, low, high in levels}


def _levels_out(ranges: dict) -> list:
    return [{"index": int(index), "lower": _num(r.lower()), "upper": _num(r.upper())}
            for index, r in sorted(ranges.items())]




def _plan(args: dict) -> dict:

    plan = {"layer_name": str(args.get("layer_name") or "").strip(), "mode": args.get("mode"),
            "given": [key for key, value in args.items() if value is not None]}
    if not plan["layer_name"]:
        return tool_error("layer_name is required.", "INVALID_ARGS", "The layer name or id.")
    if plan["mode"] is not None and plan["mode"] not in _MODES:
        return tool_error(f"mode {plan['mode']!r} is not one of {', '.join(_MODES)}.", "INVALID_ARGS",
                          "Rasters take surface, fixed_range, range_per_band or off; meshes from_vertices, "
                          "fixed_range or range_per_group.")
    for key in ("z_offset", "z_scale", "extrusion_height", "default_height"):
        if args.get(key) is not None:
            try:
                value = float(args[key])
            except (TypeError, ValueError):
                return tool_error(f"{key} must be a number.", "INVALID_ARGS", f"{key} takes a number.")
            if not math.isfinite(value) or (key != "z_offset" and value <= 0):
                return tool_error(f"{key} {args[key]!r} is out of range.", "INVALID_ARGS",
                                  f"{key} is finite" + ("." if key == "z_offset" else ", above zero."))
            plan[key] = value
    if args.get("band") is not None:
        plan["band"] = int(args["band"])
    if args.get("elevation_range") is not None:
        pair, error = _pair(args["elevation_range"], "elevation_range")
        if error:
            return error
        plan["elevation_range"] = pair
    if args.get("level_ranges") is not None:
        levels, seen = [], set()
        for item in args["level_ranges"] or []:
            if not isinstance(item, dict) or item.get("index") is None:
                return tool_error("Each level_ranges entry needs index, lower and upper.", "INVALID_ARGS",
                                  "level_ranges is [{index: 1, lower: 0, upper: 10}, ...].")
            pair, error = _pair([item.get("lower"), item.get("upper")], f"level {item.get('index')}")
            if error:
                return error
            index = int(item["index"])
            if index in seen:
                return tool_error(f"Level index {index} is given twice.", "INVALID_ARGS",
                                  "One range per band or dataset group.")
            seen.add(index)
            levels.append((index, pair[0], pair[1]))
        plan["level_ranges"] = levels
    for key in ("clamping", "binding", "z_from", "height_from"):
        if args.get(key) is not None:
            plan[key] = str(args[key]).strip()
    if plan.get("clamping") is not None and plan["clamping"] not in _CLAMPING:
        return tool_error(f"clamping {plan['clamping']!r} is not terrain, relative or absolute.", "INVALID_ARGS",
                          "terrain, relative or absolute.")
    if plan.get("binding") is not None and plan["binding"] not in _BINDING:
        return tool_error(f"binding {plan['binding']!r} is not vertex or centroid.", "INVALID_ARGS",
                          "vertex or centroid.")
    if plan.get("height_from") and plan.get("extrusion_height") is not None:
        return tool_error("height_from and extrusion_height both give the extrusion height.", "INVALID_ARGS",
                          "height_from is a field or expression (default_height covers missing "
                          "values); extrusion_height is one height per feature.")
    if args.get("extrusion") is not None:
        plan["extrusion"] = bool(args["extrusion"])
    elif plan.get("height_from") or plan.get("extrusion_height") is not None:
        plan["extrusion"] = True
    if plan.get("extrusion") is False and (plan.get("height_from") or plan.get("extrusion_height") is not None):
        return tool_error("extrusion is false but a height was given.", "INVALID_ARGS",
                          "A height turns extrusion on; no height is the only way to turn it off.")
    return plan




def _source(layer, text: str, context) -> dict:

    fields = layer.fields()
    index = fields.lookupField(text)
    if index >= 0:
        name = fields.at(index).name()
        return {"label": name, "text": QgsExpression.quotedColumnRef(name), "index": index}
    expression = QgsExpression(text)
    if expression.hasParserError():
        known = ", ".join(fields.names()[:12])
        return tool_error(f"{text[:120]!r} is neither a field of {layer.name()} nor an expression that parses: "
                          f"{' '.join(expression.parserErrorString().split())[:160]}", "INVALID_ARGS",
                          f"{known} are its fields, or a QGIS expression; field names go in double quotes.")
    for column in expression.referencedColumns():
        everything = getattr(QgsFeatureRequest, "ALL_ATTRIBUTES", "#!allattributes!#")
        if column != everything and fields.lookupField(column) < 0:
            return _field_not_found_error(layer, column)
    expression.prepare(context)
    return {"label": text, "text": f"({text})", "expression": expression}


def _kind(layer) -> str:
    if isinstance(layer, QgsVectorLayer):
        return "vector"
    if isinstance(layer, QgsRasterLayer):
        return "raster"
    if isinstance(layer, QgsMeshLayer):
        return "mesh"
    if isinstance(layer, QgsPointCloudLayer):
        return "pointcloud"
    return "other"


def _open(plan: dict) -> dict:

    layer = _find_layer(plan["layer_name"])
    if layer is None:
        return _layer_not_found_error(plan["layer_name"])
    kind = _kind(layer)
    if kind == "pointcloud":
        return tool_error(f"{layer.name()} is a point cloud: its elevation is set with {_POINTCLOUD_TOOL}.",
                          "INVALID_ARGS", f"{_POINTCLOUD_TOOL} takes z_scale or z_offset.")
    if kind == "other" or layer.elevationProperties() is None:
        return tool_error(f"{layer.name()} has no elevation properties in QGIS.", "INVALID_ARGS",
                          "A vector, raster or mesh layer.")
    state = {"layer_id": layer.id(), "layer_name": layer.name(), "kind": kind, "plan": plan, "sources": []}
    given = [key for key in (_LEVEL_KEYS if kind == "vector" else _VECTOR_KEYS) if key in plan["given"]]
    if given:
        what = "raster or mesh" if kind == "vector" else "vector"
        verb = "applies" if len(given) == 1 else "apply"
        return tool_error(f"{', '.join(given)} only {verb} to a {what} layer; {layer.name()} is a {kind} layer.",
                          "INVALID_ARGS", "They have no effect on this layer.")
    props = layer.elevationProperties()
    if kind == "raster":
        return _open_raster(layer, props, plan, state)
    if kind == "mesh":
        return _open_mesh(layer, props, plan, state)
    polygon = _member(Qgis, "GeometryType", "Polygon")
    if polygon is None:
        polygon = enum_member(QgsWkbTypes, "GeometryType", "PolygonGeometry")
    state["polygon"] = layer.geometryType() == polygon
    state["has_z"] = QgsWkbTypes.hasZ(layer.wkbType())
    context = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    for key, positive in (("height_from", True), ("z_from", False)):
        if not plan.get(key):
            continue
        source = _source(layer, plan[key], context)
        if "_error" in source:
            return source
        source.update(key=key, positive=positive, valid=[], missing=0, not_positive=0)
        state["sources"].append(source)
    if not state["sources"]:
        return state
    request = QgsFeatureRequest()
    expressions = [s["expression"] for s in state["sources"] if "expression" in s]
    if not any(e.needsGeometry() for e in expressions):
        request.setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry"))
    columns = set()
    for source in state["sources"]:
        columns |= ({layer.fields().at(source["index"]).name()} if "index" in source
                    else set(source["expression"].referencedColumns()))
    if columns and all(layer.fields().lookupField(c) >= 0 for c in columns):
        request.setSubsetOfAttributes(sorted(columns), layer.fields())
    state.update(context=context, features=layer.getFeatures(request), total=layer.featureCount(),
                 read=0, cap=int(limits.current("MAX_FEATURES_MATERIALISED")))
    return state


def _version_error(what: str) -> dict:
    return tool_error(f"{what} needs QGIS 3.38 or newer.", "UNSUPPORTED_QGIS_VERSION",
                      "z_offset, z_scale and band work here without an upgrade.")


def _open_raster(layer, props, plan: dict, state: dict) -> dict:
    mode = plan.get("mode") or ("range_per_band" if plan.get("level_ranges") else
                                "fixed_range" if plan.get("elevation_range") else "surface")
    if mode not in ("surface", "fixed_range", "range_per_band", "off"):
        return tool_error(f"mode {mode} does not apply to a raster.", "INVALID_ARGS",
                          "Rasters take surface, fixed_range, range_per_band or off.")
    count = layer.bandCount()
    if plan.get("band") is not None and not 1 <= plan["band"] <= count:
        return tool_error(f"band {plan['band']} is not a band of {layer.name()} (it has {count}).", "INVALID_ARGS",
                          f"Bands run 1 to {count}.")
    if mode != "surface" and mode != "off" and not hasattr(props, "setMode"):
        return _version_error(f"A raster {mode.replace('_', ' ')}")
    if mode == "fixed_range" and not plan.get("elevation_range"):
        return tool_error("mode fixed_range needs elevation_range.", "INVALID_ARGS",
                          "elevation_range [lower, upper] is the heights the whole raster stands for.")
    if mode == "range_per_band":
        levels = plan.get("level_ranges")
        if not levels:
            names = ", ".join(layer.bandName(b) for b in range(1, min(count, 12) + 1))
            return tool_error("mode range_per_band needs level_ranges, one [lower, upper] per band.", "INVALID_ARGS",
                              f"The bands of {layer.name()} are {names}. level_ranges is "
                              "[{index: band, lower, upper}, ...], the height each band stands for.")
        wrong = [index for index, _, _ in levels if not 1 <= index <= count]
        if wrong:
            return tool_error(f"Band {wrong[0]} is not a band of {layer.name()} (bands 1 to {count}).",
                              "INVALID_ARGS", f"Raster bands are numbered 1 to {count}.")
    state["mode"] = mode
    return state


def _open_mesh(layer, props, plan: dict, state: dict) -> dict:
    mode = plan.get("mode") or ("range_per_group" if plan.get("level_ranges") else
                                "fixed_range" if plan.get("elevation_range") else None)
    if mode is not None and mode not in _MESH_MODES:
        return tool_error(f"mode {mode} does not apply to a mesh.", "INVALID_ARGS",
                          "Meshes take from_vertices, fixed_range or range_per_group.")
    if plan.get("band") is not None:
        return tool_error("band applies to a raster; a mesh has dataset groups.", "INVALID_ARGS",
                          "level_ranges takes the dataset group indices configure_mesh_layer lists.")
    if mode is not None and not hasattr(props, "setMode"):
        return _version_error(f"A mesh {mode.replace('_', ' ')}")
    if mode == "fixed_range" and not plan.get("elevation_range"):
        return tool_error("mode fixed_range needs elevation_range.", "INVALID_ARGS",
                          "elevation_range [lower, upper] is the heights the whole mesh stands for.")
    if mode == "range_per_group":
        count = layer.datasetGroupCount()
        levels = plan.get("level_ranges")
        if not levels:
            names = ", ".join(f"{g}: {layer.datasetGroupMetadata(g).name()}" for g in range(min(count, 12)))
            return tool_error("mode range_per_group needs level_ranges, one [lower, upper] per dataset group.",
                              "INVALID_ARGS", f"The dataset groups of {layer.name()} are {names}. level_ranges is "
                              "[{index: group, lower, upper}, ...], the height each group stands for.")
        wrong = [index for index, _, _ in levels if not 0 <= index < count]
        if wrong:
            return tool_error(f"Dataset group {wrong[0]} is not a group of {layer.name()} (0 to {count - 1}).",
                              "INVALID_ARGS", "configure_mesh_layer action inspect lists the group indices.")
    state["mode"] = mode
    return state


def _read(state: dict) -> bool:

    context = state["context"]
    for count, feature in enumerate(state["features"], 1):
        state["read"] += 1
        context.setFeature(feature)
        for source in state["sources"]:
            if "index" in source:
                value = feature[source["index"]]
            else:
                value = source["expression"].evaluate(context)
                if source["expression"].hasEvalError():
                    value = None
            try:
                number = None if _is_qgis_null(value) else float(value)
            except (TypeError, ValueError):
                number = None
            if number is None or not math.isfinite(number):
                source["missing"] += 1
            elif source["positive"] and number <= 0:
                source["not_positive"] += 1
            else:
                source["valid"].append(number)
        if state["read"] >= state["cap"]:
            return True
        if count >= STATS_CHUNK:
            return False
    return True


def _close(state: dict) -> None:
    state["features"] = None
    state.pop("context", None)
    for source in state["sources"]:
        source.pop("expression", None)




def _decide(state: dict) -> dict:

    plan, decided = state["plan"], {"notes": []}
    if state["sources"] and state["read"] < state["total"]:
        decided["notes"].append(f"Counted the first {state['read']:,} of {state['total']:,} features.")
    for source in state["sources"]:
        valid = source.pop("valid")
        counted = {"read": state["read"], "real": len(valid), "missing": source["missing"]}
        if valid:
            counted.update(min=_num(min(valid)), max=_num(max(valid)))
        if source["key"] == "height_from":
            counted["not_positive"] = source["not_positive"]
            default = plan.get("default_height")
            if default is None:
                if not valid:
                    return tool_error(f"{source['label']} holds no height above zero in the {state['read']:,} "
                                      f"features read of {state['layer_name']}.", "INVALID_ARGS",
                                      "extrusion_height gives one height for every feature; another field "
                                      "also works.")
                default = round(statistics.median(valid), 1) or 1.0
                decided["default_from"] = "median of the real heights"
            else:
                decided["default_from"] = "default_height"
            decided["default_height"] = default
            number = f"to_real({source['text']})"
            decided["height_expression"] = f"if(coalesce({number}, 0) > 0, {number}, {_literal(default)})"
            decided["heights"] = counted
        else:
            decided["z_expression"] = f"coalesce(to_real({source['text']}), 0)"
            decided["z_values"] = counted
    return decided


def _literal(value: float) -> str:
    return repr(float(value)) if not float(value).is_integer() else str(int(value))




def _apply(state: dict, decided: dict) -> dict:
    layer = QgsProject.instance().mapLayer(state["layer_id"])
    if layer is None:
        return tool_error(f"{state['layer_name']} was removed while it was read.", "LAYER_NOT_FOUND",
                          "Reloading it lets set_layer_elevation run again.")
    plan, props = state["plan"], layer.elevationProperties()
    if plan.get("z_offset") is not None:
        props.setZOffset(plan["z_offset"])
    if plan.get("z_scale") is not None:
        props.setZScale(plan["z_scale"])
    if state["kind"] == "raster":
        _apply_raster(layer, props, plan, state["mode"])
    elif state["kind"] == "mesh":
        _apply_mesh(props, plan, state["mode"])
    else:
        _apply_vector(layer, props, plan, state, decided)
    layer.triggerRepaint()
    result = {"layer_name": layer.name(), "layer_type": state["kind"], "elevation": _read_back(layer),
              "has_elevation": bool(props.hasElevation())}
    for key in ("heights", "default_height", "default_from", "z_values", "renderer_3d"):
        if decided.get(key) is not None:
            result[key] = decided[key]
    if decided.get("notes"):
        result["note"] = " ".join(decided["notes"])
    return result


def _apply_raster(layer, props, plan: dict, mode: str) -> None:
    if mode == "off":
        props.setEnabled(False)
        return
    props.setEnabled(True)
    if hasattr(props, "setMode"):
        props.setMode(_member(Qgis, "RasterElevationMode", _RASTER_MODES[mode]))
    if mode == "surface":
        props.setBandNumber(plan.get("band") or props.bandNumber() or 1)
    elif mode == "fixed_range":
        props.setFixedRange(QgsDoubleRange(*plan["elevation_range"]))
    else:
        props.setFixedRangePerBand(_level_ranges(plan["level_ranges"]))


def _apply_mesh(props, plan: dict, mode: str | None) -> None:
    if mode is None:
        return
    props.setMode(_member(Qgis, "MeshElevationMode", _MESH_MODES[mode]))
    if mode == "fixed_range":
        props.setFixedRange(QgsDoubleRange(*plan["elevation_range"]))
    elif mode == "range_per_group":
        props.setFixedRangePerGroup(_level_ranges(plan["level_ranges"]))


def _property_key(name: str):
    return _member(QgsMapLayerElevationProperties, "Property", name)


def _apply_vector(layer, props, plan: dict, state: dict, decided: dict) -> None:
    clamping = plan.get("clamping")
    if clamping is None and decided.get("z_expression") and not state["has_z"]:

        clamping = "absolute"
        decided["notes"].append("Clamping set to absolute: the z comes from the field on a layer without z.")
    if clamping is not None:
        props.setClamping(_member(Qgis, "AltitudeClamping", _CLAMPING[clamping]))
    if plan.get("binding") is not None:
        props.setBinding(_member(Qgis, "AltitudeBinding", _BINDING[plan["binding"]]))
    collection = QgsPropertyCollection(props.dataDefinedProperties())
    if decided.get("z_expression"):
        collection.setProperty(_property_key("ZOffset"), QgsProperty.fromExpression(decided["z_expression"]))
    extrusion = plan.get("extrusion")
    if extrusion is not None:
        props.setExtrusionEnabled(extrusion)
        if extrusion:
            height = decided.get("default_height") or plan.get("extrusion_height") or props.extrusionHeight() or 10.0
            props.setExtrusionHeight(float(height))
        if decided.get("height_expression"):
            collection.setProperty(_property_key("ExtrusionHeight"),
                                   QgsProperty.fromExpression(decided["height_expression"]))
        elif plan.get("extrusion_height") is not None or not extrusion:
            collection.setProperty(_property_key("ExtrusionHeight"), QgsProperty())
    props.setDataDefinedProperties(collection)
    if state["polygon"] and (extrusion or _has_polygon_3d(layer)):
        decided["renderer_3d"] = _set_renderer_3d(layer, props, extrusion is not None)
    elif extrusion and not state["polygon"]:
        decided["notes"].append("The 3D view extrudes polygons only; the elevation profile uses the extrusion.")


def _has_polygon_3d(layer) -> bool:
    renderer = layer.renderer3D()
    symbol = getattr(renderer, "symbol", lambda: None)() if renderer is not None else None

    return symbol is not None and symbol.type() == "polygon"


def _fill_color(layer):
    from qgis.PyQt.QtGui import QColor

    renderer, symbol = layer.renderer(), None
    try:
        symbol = renderer.symbol() if hasattr(renderer, "symbol") else None
        if symbol is None:
            symbols = renderer.symbols(QgsRenderContext())
            symbol = symbols[0] if symbols else None
    except Exception:  # noqa: BLE001
        symbol = None
    return QColor(symbol.color()) if symbol is not None else QColor("#b4b4b4")


def _set_renderer_3d(layer, props, extrusion_asked: bool) -> dict | str:







    try:
        from qgis._3d import QgsPhongMaterialSettings, QgsPolygon3DSymbol, QgsVectorLayer3DRenderer
    except ImportError:
        return "unavailable: this QGIS build has no 3D support"
    from qgis.core import QgsAbstract3DSymbol

    height_key = _member(QgsAbstract3DSymbol, "Property", "ExtrusionHeight")
    old = layer.renderer3D().clone() if _has_polygon_3d(layer) else None
    if old is not None:
        from qgis.PyQt import sip


        symbol = sip.cast(old.symbol(), QgsPolygon3DSymbol)
        kept_height = symbol.extrusionHeight()
        kept_expression = QgsProperty(symbol.dataDefinedProperties().property(height_key))
    else:
        symbol = QgsPolygon3DSymbol()
    symbol.setDefaultPropertiesFromLayer(layer)
    collection = QgsPropertyCollection(symbol.dataDefinedProperties())
    if old is not None and not extrusion_asked:
        symbol.setExtrusionHeight(kept_height)
        collection.setProperty(height_key, kept_expression)
    elif not props.extrusionEnabled():

        collection.setProperty(height_key, QgsProperty())
    symbol.setDataDefinedProperties(collection)
    if old is not None:
        color = symbol.materialSettings().diffuse() if hasattr(symbol.materialSettings(), "diffuse") else None
    else:
        color = _fill_color(layer)
        material = QgsPhongMaterialSettings()
        material.setDiffuse(color)
        material.setAmbient(color.darker(200))
        symbol.setMaterialSettings(material)
        if hasattr(symbol, "setEdgesEnabled"):
            symbol.setEdgesEnabled(True)
            symbol.setEdgeColor(color.darker(170))
    if old is not None:
        layer.setRenderer3D(old)
    else:
        layer.setRenderer3D(QgsVectorLayer3DRenderer(symbol))
    dd = symbol.dataDefinedProperties()
    extrusion = ("data-defined" if dd.isActive(height_key) else _num(symbol.extrusionHeight()))
    out = {"symbol": "polygon", "extrusion": extrusion, "clamping": _clamping_word(symbol.altitudeClamping()),
           "kept_style": old is not None}
    if dd.isActive(_member(QgsAbstract3DSymbol, "Property", "Height")):
        out["z"] = "data-defined"
    if color is not None:
        out["color"] = color.name()
    return out


def _clamping_word(value) -> str | None:
    return _name_of(value, _CLAMPING, Qgis, "AltitudeClamping")


def _expression_of(props, name: str) -> str | None:
    prop = props.dataDefinedProperties().property(_property_key(name))
    if prop is None or not prop.isActive():
        return None
    return prop.expressionString() or prop.field() or None


def _read_back(layer) -> dict:

    props, kind = layer.elevationProperties(), _kind(layer)
    out = {"z_offset": _num(props.zOffset()), "z_scale": _num(props.zScale())}
    if kind == "vector":
        out.update(clamping=_clamping_word(props.clamping()),
                   binding=_name_of(props.binding(), _BINDING, Qgis, "AltitudeBinding"),
                   extrusion=bool(props.extrusionEnabled()))
        if props.extrusionEnabled():
            out["extrusion_height"] = _num(props.extrusionHeight())
        for key, name in (("z_expression", "ZOffset"), ("extrusion_expression", "ExtrusionHeight")):
            text = _expression_of(props, name)
            if text:
                out[key] = text
        return out
    if kind == "raster":
        out["represents_elevation"] = bool(props.isEnabled())
        mode = props.mode() if hasattr(props, "mode") else None
        word = _name_of(mode, _RASTER_MODES, Qgis, "RasterElevationMode") if mode is not None else "surface"
        out["mode"] = word or "dynamic_range_per_band"
        if word == "surface":
            out["band"] = props.bandNumber()
        elif word == "fixed_range":
            out["elevation_range"] = _range_list(props.fixedRange())
        elif word == "range_per_band":
            out["level_ranges"] = _levels_out(props.fixedRangePerBand())
        return out
    if hasattr(props, "mode"):
        word = _name_of(props.mode(), _MESH_MODES, Qgis, "MeshElevationMode")
        out["mode"] = word
        if word == "fixed_range":
            out["elevation_range"] = _range_list(props.fixedRange())
        elif word == "range_per_group":
            out["level_ranges"] = _levels_out(props.fixedRangePerGroup())
    return out




def _stopped() -> dict:
    return tool_error("Stopped while the layer's heights were read.", "CANCELLED", "The layer was left as it was.")


def _set_layer_elevation(args: dict) -> dict:
    plan = _plan(args)
    if "_error" in plan:
        return plan
    cancelled = net.current_cancel_check()
    try:
        state = run_on_main_thread(_open, plan, timeout=60)
        if "_error" in state:
            return state
        if state["sources"]:
            try:
                while True:
                    if cancelled is not None and cancelled():
                        return _stopped()
                    if run_on_main_thread(_read, state, timeout=120):
                        break
            finally:
                run_on_main_thread(_close, state, timeout=30)
        decided = _decide(state)
        if "_error" in decided:
            return decided
        return run_on_main_thread(_apply, state, decided, timeout=60)
    except InterruptedError:
        return _stopped()




def _layer_levels(layer) -> list:



    props = layer.elevationProperties()
    if props is None or not hasattr(props, "mode") or not props.hasElevation():
        return []
    kind, mode = _kind(layer), props.mode()
    if kind == "raster":
        if mode == _member(Qgis, "RasterElevationMode", "FixedElevationRange"):
            ranges = [props.fixedRange()]
        elif mode == _member(Qgis, "RasterElevationMode", "FixedRangePerBand"):
            ranges = list(props.fixedRangePerBand().values())
        else:
            return []
    elif kind == "mesh":
        if mode == _member(Qgis, "MeshElevationMode", "FixedElevationRange"):
            ranges = [props.fixedRange()]
        elif mode == _member(Qgis, "MeshElevationMode", "FixedRangePerGroup"):
            ranges = list(props.fixedRangePerGroup().values())
        else:
            return []
    else:
        return []
    return [(r.lower(), r.upper(), r.includeLower(), r.includeUpper()) for r in ranges if not r.isInfinite()]


def _derived_step(levels: list) -> float | None:


    widths = [level[1] - level[0] for level in levels if level[1] > level[0]]
    if widths:
        return statistics.median(widths)
    heights = sorted({level[0] for level in levels})
    gaps = [b - a for a, b in zip(heights, heights[1:]) if b > a]
    return min(gaps) if gaps else None


def _controller_widget(canvas):
    try:
        from qgis.gui import QgsElevationControllerWidget
    except ImportError:
        return None
    return canvas.findChild(QgsElevationControllerWidget) if canvas is not None else None


def _shows(layer, current: QgsDoubleRange) -> str:
    props = layer.elevationProperties()
    if _kind(layer) == "raster" and hasattr(props, "bandForElevationRange") and \
            props.mode() == _member(Qgis, "RasterElevationMode", "FixedRangePerBand"):
        band = props.bandForElevationRange(layer, current)
        return f"band {band}" if band > 0 else "nothing"
    return "shown" if props.isVisibleInZRange(current, layer) else "hidden"


def _temporal_state(canvas) -> str | None:
    controller = canvas.temporalController() if canvas is not None else None
    mode = getattr(controller, "navigationMode", None)
    if mode is None:
        return None
    for word, member in (("animated", "Animated"), ("fixed range", "FixedRange"), ("movie", "Movie")):
        found = _member(Qgis, "TemporalNavigationMode", member)
        if found is not None and mode() == found:
            return word
    return "off"


def _configure_elevation_controller(args: dict) -> dict:
    from qgis.utils import iface

    project = QgsProject.instance()
    props = project.elevationProperties()
    if not hasattr(props, "setElevationRange"):
        return _version_error("The elevation controller")
    canvas = iface.mapCanvas() if iface is not None else None
    if canvas is None:
        return tool_error("There is no map canvas to filter.", "EXECUTION_FAILED", "Needs the main window.")
    widget = _controller_widget(canvas)
    if args.get("enabled") is False:
        canvas.setZRange(QgsDoubleRange())
        return {"enabled": False, "canvas_z_range": None,
                "controller": "open" if widget is not None else "closed",
                "note": "The map shows every height again. The slider, when open, filters again once moved; "
                        "View > Elevation Controller hides it."}
    explicit = {}
    for key in ("elevation_range", "current_range"):
        if args.get(key) is not None:
            pair, error = _pair(args[key], key)
            if error:
                return error
            explicit[key] = pair
    layers, levels = [], []
    for layer in project.mapLayers().values():
        own = _layer_levels(layer)
        if own:
            layers.append(layer)
            levels.extend(own)
    levels.sort()
    if "elevation_range" in explicit:
        low, high = explicit["elevation_range"]
        range_from = "elevation_range"
    elif levels:
        low, high = min(level[0] for level in levels), max(level[1] for level in levels)
        range_from = "the layers' levels"
    else:
        return tool_error("No layer of the project stands for fixed heights, so there is no range to derive.",
                          "INVALID_ARGS", "set_layer_elevation sets a range per band or dataset group, or "
                          "elevation_range [lower, upper] here.")
    step = float(args["step"]) if args.get("step") is not None else _derived_step(levels)
    step_from = "step" if args.get("step") is not None else ("the layers' levels" if step else None)
    if "current_range" in explicit:
        current = QgsDoubleRange(*explicit["current_range"])
    elif levels:
        current = QgsDoubleRange(*levels[0])
    else:
        current = QgsDoubleRange(low, min(high, low + step) if step else high)
    props.setElevationRange(QgsDoubleRange(low, high))
    props.setElevationFilterRangeSize(step or -1.0)
    if args.get("invert") is not None:
        props.setInvertElevationFilter(bool(args["invert"]))
    if widget is not None:

        widget.setFixedRangeSize(step or -1.0)
        widget.setInverted(bool(props.invertElevationFilter()))
        widget.setRange(current)

        current = widget.range()
    canvas.setZRange(current)
    canvas.refresh()
    result = {"elevation_range": [_num(low), _num(high)], "range_from": range_from,
              "step": _num(step) if step else None, "step_from": step_from,
              "inverted": bool(props.invertElevationFilter()), "canvas_z_range": _range_list(current),
              "levels": len(levels),
              "layers": [{"layer_name": layer.name(), "now": _shows(layer, current)} for layer in layers[:20]],
              "controller": "open" if widget is not None else "closed"}
    if widget is None:
        result["note"] = ("The map is filtered to canvas_z_range now. The slider to move through the heights is "
                          "under View > Elevation Controller; it opens with this range, step and direction.")
    temporal = _temporal_state(canvas)
    if temporal and temporal != "off":
        result["temporal_controller"] = temporal
    return result


__all__ = ["register_layer_elevation_tools"]
