# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later














from __future__ import annotations

import math

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsProject,
    QgsVectorLayer,
    QgsVertexId,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core.tool_registry import Tool, ToolRegistry, tool_error
from ._compat import enum_value
from .layer_lookup import _find_layer, _layer_not_found_error
from .layout_tools import _mm_point, _mm_size, _resolve_layout, _with_new_item, place_item
from .map_3d import open_3d_views

_PLACEMENT = {
    "layout_name": {"type": "string"},
    "x": {"type": "number", "minimum": -10000, "maximum": 10000},
    "y": {"type": "number", "minimum": -10000, "maximum": 10000},
    "width": {"type": "number", "minimum": 0.001, "maximum": 10000},
    "height": {"type": "number", "minimum": 0.001, "maximum": 10000},
}
_CAMERA = {
    "view_index": {"type": "integer", "minimum": 0, "maximum": 1000},
    "pitch": {"type": "number", "minimum": 0, "maximum": 90},
    "heading": {"type": "number", "minimum": -360, "maximum": 720},
    "distance": {"type": "number", "exclusiveMinimum": 0},
}



_DEFAULT_TOLERANCE_SHARE = 0.01


_POINT_CLOUD_TOLERANCE_CAP = 5.0


_RANGE_DPI = 96.0
_MAX_ERROR_PIXELS = 2.0

_AXIS_TICKS = 5
_LINE = enum_value((Qgis, "GeometryType.Line"), (QgsWkbTypes, "LineGeometry"))


def register_layout_elevation_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="add_layout_3d_map",
        danger="write",
        builds_layout_at="layout_name",
        label=QT_TRANSLATE_NOOP("AIAgent", "Add the 3D view to the layout"),
        input_schema={
            "type": "object",
            "properties": {**_PLACEMENT, **_CAMERA},
            "required": ["layout_name", "x", "y", "width", "height"],
        },
        handler=_add_layout_3d_map,
    ))
    registry.register(Tool(
        name="add_layout_elevation_profile",
        danger="write",
        builds_layout_at="layout_name",
        label=QT_TRANSLATE_NOOP("AIAgent", "Add an elevation profile to the layout"),
        input_schema={
            "type": "object",
            "properties": {
                **_PLACEMENT,
                "line_layer": {"type": "string"},
                "feature_id": {"type": "integer"},
                "line_wkt": {"type": "string"},
                "wkt_crs": {"type": "string"},
                "layers": {"type": "array", "items": {"type": "string"}},
                "tolerance": {"type": "number", "minimum": 0},
            },
            "required": ["layout_name", "x", "y", "width", "height"],
        },
        handler=_add_layout_elevation_profile,
    ))






def snapshot_3d_view(args: dict):







    no_3d_hint = "take_screenshot covers the 2D canvas."
    try:
        from qgis._3d import Qgs3DMapSettings
    except ImportError as exc:
        return None, tool_error(f"This QGIS build has no 3D module ({exc}).", "EXECUTION_FAILED", no_3d_hint)
    views = open_3d_views()
    if views is None:
        return None, tool_error("This QGIS cannot list its 3D views: its 3D support is missing or failed.",
                                "EXECUTION_FAILED", no_3d_hint)
    if not views:
        return None, tool_error("No 3D map view is open, and the scene and camera are copied from one.",
                                "INVALID_ARGS",
                                "configure_3d_map_view opens one on QGIS 3.44 and newer; older QGIS opens "
                                "one at View > 3D Map Views > New 3D Map View.")
    index = int(args.get("view_index") or 0)
    if index not in range(len(views)):
        return None, tool_error(f"view_index {index} names no open 3D view ({len(views)} open).",
                                "INVALID_ARGS", f"view_index is 0 to {len(views) - 1}.")
    source = views[index]
    pose = source.cameraController().cameraPose()
    for key, apply in _CAMERA_OVERRIDES:
        if args.get(key) is not None:
            apply(pose, float(args[key]))
    settings = Qgs3DMapSettings(source.mapSettings())
    _origin_on_ground(settings, pose)
    return {"canvas": source, "index": index, "count": len(views), "settings": settings, "pose": pose}, None


def _origin_on_ground(settings, pose) -> None:









    from qgis._3d import Qgs3DMapScene
    from qgis.core import QgsVector3D

    if not hasattr(Qgs3DMapScene, "hasSceneOriginShiftEnabled"):
        return
    origin, centre = settings.origin(), pose.centerPoint()
    settings.setOrigin(QgsVector3D(origin.x() + centre.x(), origin.y() + centre.y(), 0.0))
    pose.setCenterPoint(QgsVector3D(0.0, 0.0, origin.z() + centre.z()))


def _set_distance(pose, metres: float) -> None:
    if metres > 0:
        pose.setDistanceFromCenterPoint(metres)




_CAMERA_OVERRIDES = (
    ("pitch", lambda pose, degrees: pose.setPitchAngle(min(90.0, max(0.0, degrees)))),
    ("heading", lambda pose, degrees: pose.setHeadingAngle(degrees % 360.0)),
    ("distance", _set_distance),
)


def camera_facts(pose) -> dict:
    return {"pitch": round(pose.pitchAngle(), 2), "heading": round(pose.headingAngle(), 2),
            "distance": round(pose.distanceFromCenterPoint(), 2)}


def _add_layout_3d_map(args: dict) -> dict:
    layout, error = _resolve_layout(args["layout_name"])
    if error:
        return error
    view, error = snapshot_3d_view(args)
    if error:
        return error
    from qgis._3d import QgsLayoutItem3DMap

    item = QgsLayoutItem3DMap(layout)
    item.attemptMove(_mm_point(args["x"], args["y"]))
    item.attemptResize(_mm_size(args["width"], args["height"]))
    item.setMapSettings(view["settings"])
    item.setCameraPose(view["pose"])
    layout.addLayoutItem(item)
    placement = place_item(layout, item, {key: args[key] for key in ("x", "y", "width", "height")},
                           resizable=True)
    result = _with_new_item(layout, item)
    result.update(placement)
    result.update({"view_index": view["index"], "open_3d_views": view["count"],
                   "camera": camera_facts(view["pose"])})
    return result






def _has_elevation(layer) -> bool:






    props = layer.elevationProperties() if hasattr(layer, "elevationProperties") else None
    if props is None:
        return False
    try:
        return bool(props.showByDefaultInElevationProfilePlots())
    except (AttributeError, RuntimeError, TypeError):
        return isinstance(layer, QgsVectorLayer) and QgsWkbTypes.hasZ(layer.wkbType())


def _range_context(length: float, width_mm: float):

    from qgis.core import QgsProfileGenerationContext

    pixels = max(width_mm / 25.4 * _RANGE_DPI, 1.0)
    context = QgsProfileGenerationContext()
    context.setDpi(_RANGE_DPI)
    context.setMapUnitsPerDistancePixel(length / pixels)
    context.setMaximumErrorMapUnits(_MAX_ERROR_PIXELS * length / pixels)
    return context


def _profile_crs(crs, center):

    if crs.isValid() and not crs.isGeographic():
        return crs
    project_crs = QgsProject.instance().crs()
    if project_crs.isValid() and not project_crs.isGeographic():
        return project_crs
    lonlat = center
    if crs.isValid() and crs.authid() != "EPSG:4326":
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        lonlat = QgsCoordinateTransform(crs, wgs84, QgsProject.instance()).transform(center)
    zone = min(60, max(1, int((lonlat.x() + 180.0) // 6.0) + 1))
    return QgsCoordinateReferenceSystem(f"EPSG:{(32600 if lonlat.y() >= 0 else 32700) + zone}")


def _longest_line(geometry):

    if geometry is None or geometry.isEmpty() or geometry.type() != _LINE:
        return None
    parts = [QgsGeometry(part.clone()) for part in geometry.constParts()]
    parts = [part for part in parts if part.length() > 0]
    return max(parts, key=lambda part: part.length()) if parts else None


def _curve(args: dict):

    if args.get("line_wkt") and args.get("line_layer"):
        return None, None, None, tool_error("line_layer with feature_id, or line_wkt: not both.",
                                            "INVALID_ARGS", "Only one holds the profile line.")
    if args.get("line_wkt"):
        geometry = QgsGeometry.fromWkt(str(args["line_wkt"]))

        crs = QgsCoordinateReferenceSystem(args.get("wkt_crs") or "EPSG:4326")
        if not crs.isValid():
            return None, None, None, tool_error(f"Unknown wkt_crs {args['wkt_crs']!r}.", "INVALID_ARGS",
                                                "wkt_crs needs an id such as EPSG:2154.")
        source = "line_wkt"
    elif args.get("line_layer"):
        layer = _find_layer(args["line_layer"])
        if layer is None:
            return None, None, None, _layer_not_found_error(args["line_layer"])
        if not isinstance(layer, QgsVectorLayer):
            return None, None, None, tool_error(f"{layer.name()} is not a vector layer.", "INVALID_ARGS",
                                                "line_layer needs a line layer, or line_wkt.")
        if args.get("feature_id") is None:
            if layer.featureCount() != 1:
                return None, None, None, tool_error(
                    f"{layer.name()} has {layer.featureCount()} features; feature_id says which line.",
                    "INVALID_ARGS", "get_features lists the ids.")
            feature = next(layer.getFeatures(), None)
        else:
            feature = layer.getFeature(int(args["feature_id"]))
        if feature is None or not feature.isValid():
            return None, None, None, tool_error(f"Feature {args.get('feature_id')} not found in {layer.name()}.",
                                                "INVALID_ARGS", "get_features lists the ids.")
        geometry, crs, source = feature.geometry(), layer.crs(), f"{layer.name()} feature {feature.id()}"
    else:
        return None, None, None, tool_error("No profile line: line_layer+feature_id, or line_wkt works.",
                                            "INVALID_ARGS", "A drawn line layer or a WKT LINESTRING both work.")
    line = _longest_line(geometry)
    if line is None:
        return None, None, None, tool_error(f"The profile line from {source} is not a line with a length.",
                                            "INVALID_ARGS", "A LINESTRING or line feature only.")
    return line, crs, source, None


def _nudged(line):



    box = line.boundingBox()
    if box.width() > 0 and box.height() > 0:
        return line
    step = line.length() * 1e-6
    curve = line.constGet().clone()
    last = curve.numPoints() - 1
    point = curve.pointN(last)
    if box.height() == 0:
        point.setY(point.y() + step)
    else:
        point.setX(point.x() + step)
    curve.moveVertex(QgsVertexId(0, 0, last), point)
    return QgsGeometry(curve)


def _nice_step(span: float) -> float:

    raw = span / _AXIS_TICKS if span > 0 else 1.0
    magnitude = 10 ** math.floor(math.log10(raw))
    return min((factor * magnitude for factor in (1, 2, 2.5, 5, 10)),
               key=lambda step: abs(math.log(step / raw)))


def _set_axis(axis, span: float) -> float:
    step = _nice_step(span)
    for setter, value in (("setGridIntervalMajor", step), ("setGridIntervalMinor", step / 5.0),
                          ("setLabelInterval", step)):
        try:
            getattr(axis, setter)(value)
        except (AttributeError, TypeError):
            pass
    return step


def _profile_layers(args: dict):

    names = args.get("layers")
    advice = ("A raster DEM draws once marked as an elevation surface: set_layer_elevation does it, or "
              "Layer Properties > Elevation > Represents Elevation Surface.")
    if names:
        layers, lacking = [], []
        for name in names:
            layer = _find_layer(name)
            if layer is None:
                return None, _layer_not_found_error(name)
            (layers if _has_elevation(layer) else lacking).append(layer)
        if lacking:
            return None, tool_error(f"No elevation is set on {', '.join(layer.name() for layer in lacking)}, so "
                                    "the profile cannot draw it.", "INVALID_ARGS", advice)
        return layers, None
    layers = [layer for layer in QgsProject.instance().mapLayers().values() if _has_elevation(layer)]
    if not layers:
        return None, tool_error("No loaded layer has elevation enabled, so the profile would be empty.",
                                "INVALID_ARGS", advice)
    return layers, None


def _add_layout_elevation_profile(args: dict) -> dict:
    try:
        from qgis.core import QgsLayoutItemElevationProfile, QgsProfilePlotRenderer, QgsProfileRequest
    except ImportError:
        return tool_error("A layout elevation profile needs QGIS 3.30 or newer.", "UNSUPPORTED_QGIS_VERSION",
                          "elevation_profile gives the numbers along the line on older QGIS.")
    layout, error = _resolve_layout(args["layout_name"])
    if error:
        return error
    layers, error = _profile_layers(args)
    if error:
        return error
    line, crs, source, error = _curve(args)
    if error:
        return error
    project = QgsProject.instance()
    profile_crs = _profile_crs(crs, line.centroid().asPoint())
    if profile_crs != crs:
        line.transform(QgsCoordinateTransform(crs, profile_crs, project))
    line = _nudged(line)
    length = line.length()
    clouds = [layer for layer in layers if type(layer).__name__ == "QgsPointCloudLayer"]
    tolerance = args.get("tolerance")
    if tolerance is None and (clouds or any(isinstance(layer, QgsVectorLayer) for layer in layers)):
        tolerance = length * _DEFAULT_TOLERANCE_SHARE
        if clouds:
            tolerance = min(tolerance, _POINT_CLOUD_TOLERANCE_CAP)

    request = QgsProfileRequest(line.constGet().clone())
    request.setCrs(profile_crs)
    request.setTransformContext(project.transformContext())
    if tolerance is not None:
        request.setTolerance(float(tolerance))
    try:
        terrain = project.elevationProperties().terrainProvider()
        if terrain is not None:
            request.setTerrainProvider(terrain.clone())
    except (AttributeError, RuntimeError):
        pass
    renderer = QgsProfilePlotRenderer(layers, request)
    renderer.setContext(_range_context(length, float(args["width"])))
    renderer.generateSynchronously()
    z_range = renderer.zRange()
    if z_range.isEmpty() if hasattr(z_range, "isEmpty") else z_range.lower() > z_range.upper():
        return tool_error(f"Nothing along the profile line from {source} has an elevation: the line misses "
                          f"{', '.join(layer.name() for layer in layers[:5])}.", "INVALID_ARGS",
                          "The line misses the DEM and the elevation layers; line_wkt is lon lat unless "
                          "wkt_crs names its CRS.")
    low, high = z_range.lower(), z_range.upper()
    margin = max((high - low) * 0.05, 1.0)
    low, high = low - margin, high + margin

    item = QgsLayoutItemElevationProfile(layout)
    item.attemptMove(_mm_point(args["x"], args["y"]))
    item.attemptResize(_mm_size(args["width"], args["height"]))
    item.setCrs(profile_crs)
    item.setProfileCurve(line.constGet().clone())
    item.setLayers(layers)
    if tolerance is not None:
        item.setTolerance(float(tolerance))
    plot = item.plot()
    plot.setXMinimum(0.0)
    plot.setXMaximum(length)
    plot.setYMinimum(low)
    plot.setYMaximum(high)
    _set_axis(plot.xAxis(), length)
    _set_axis(plot.yAxis(), high - low)
    layout.addLayoutItem(item)
    placement = place_item(layout, item, {key: args[key] for key in ("x", "y", "width", "height")},
                           resizable=True)
    result = _with_new_item(layout, item)
    result.update(placement)
    profile = {
        "line": source,
        "crs": profile_crs.authid(),
        "length": round(length, 2),
        "distance_range": [0, round(length, 2)],
        "elevation_range": [round(low, 2), round(high, 2)],
        "layers": [layer.name() for layer in layers],
    }
    if tolerance is not None:
        profile["tolerance"] = round(float(tolerance), 4)
    if profile_crs != crs:
        profile["reprojected_from"] = crs.authid()
    result["profile"] = profile
    return result
