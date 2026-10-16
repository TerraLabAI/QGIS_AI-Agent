# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later












from __future__ import annotations

from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .layout_tools import _mm_point, _mm_size, _resolve_layout, _with_new_item, place_item
from .map_3d import ground_square, open_3d_views

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
        return None, tool_error("No 3D map view is open (closing a view's window deletes the view), and the "
                                "scene and camera are copied from one.",
                                "INVALID_ARGS",
                                "configure_3d_map_view opens one on QGIS 3.36 and newer; older QGIS opens "
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
    x, y = origin.x() + centre.x(), origin.y() + centre.y()
    ground = ground_square(settings, x, y, pose.distanceFromCenterPoint())
    if ground is not None:
        settings.setExtent(ground)
    settings.setOrigin(QgsVector3D(x, y, 0.0))
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







