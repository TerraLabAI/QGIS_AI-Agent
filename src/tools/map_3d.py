# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


















from __future__ import annotations

from qgis.core import QgsCoordinateTransform, QgsProject, QgsRasterLayer, QgsVector3D
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP
from qgis.utils import iface

from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .layer_lookup import _find_layer

TOOL = "configure_3d_map_view"
_DEFAULT_TILE_RESOLUTION = 256
_DEFAULT_GROUND_ERROR = 1.0
_DEFAULT_SCREEN_ERROR = 3.0


def register_map_3d_tools(registry: ToolRegistry):

    registry.register(Tool(
        name="configure_3d_map_view",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Open or configure a 3D map view[ with {dem_layer}]"),
        input_schema={
            "type": "object",
            "properties": {
                "dem_layer": {"type": "string"},
                "view_index": {"type": "integer", "minimum": 0, "maximum": 1000},
                "title": {"type": "string", "minLength": 1, "maxLength": 120},
                "vertical_exaggeration": {"type": "number", "exclusiveMinimum": 0, "maximum": 100},
                "tile_resolution": {"type": "integer", "minimum": 16, "maximum": 1024},
                "terrain_ground_error": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000},
                "terrain_screen_error": {"type": "number", "minimum": 0.5, "maximum": 64},
            },
        },
        handler=_configure_3d_map_view,
    ))


def open_3d_views():






    views = getattr(iface, "mapCanvases3D", None)
    if callable(views):
        try:
            return list(views())
        except Exception:  # noqa: BLE001
            return None
    try:
        from qgis._3d import Qgs3DMapCanvas
        return list(iface.mainWindow().findChildren(Qgs3DMapCanvas))
    except Exception:  # noqa: BLE001
        return None


def _public_3d_api():

    create = getattr(iface, "createNewMapCanvas3D", None)
    if not callable(create) or not callable(getattr(iface, "mapCanvases3D", None)):
        return None
    try:
        from qgis._3d import QgsDemTerrainSettings, QgsFlatTerrainSettings
    except ImportError:
        return None
    return create, QgsDemTerrainSettings, QgsFlatTerrainSettings


def _configure_3d_map_view(args: dict) -> dict:

    api = _public_3d_api()
    if api is None:
        return tool_error(
            "This QGIS version does not expose the public 3D map-view API (QGIS 3.44 or newer is required).",
            "UNSUPPORTED_QGIS_VERSION",
            "An older QGIS version has no automatic 3D Map View; 3.44 and newer open one here.",
        )
    create, terrain_type, flat_type = api
    dem = None
    if args.get("dem_layer"):
        dem = _find_layer(str(args["dem_layer"]))
        if dem is None:
            return tool_error(f"DEM layer {args.get('dem_layer')!r} was not found.", "LAYER_NOT_FOUND",
                              "dem_layer names a loaded raster DEM; unset, the terrain is flat.")
        if not isinstance(dem, QgsRasterLayer):
            return tool_error(f"{dem.name()!r} is not a raster DEM layer.", "INVALID_ARGS",
                              "dem_layer names a raster elevation layer (DEM, DTM, LiDAR); unset, the terrain is flat.")

    views = open_3d_views()
    if views is None:
        return tool_error("QGIS could not enumerate its 3D map views.", "EXECUTION_FAILED",
                          "This QGIS build may not have 3D support enabled.")
    created = "view_index" not in args
    if created:
        try:
            canvas = create(str(args.get("title") or "AI Agent 3D Map"))
        except Exception as exc:  # noqa: BLE001
            return tool_error(f"QGIS could not open a 3D map view: {exc}", "EXECUTION_FAILED",
                              "OpenGL support may be missing.")
        if canvas is None:
            return tool_error("QGIS did not create a 3D map view.", "EXECUTION_FAILED",
                              "OpenGL support may be missing.")
    else:
        index = int(args["view_index"])
        if index >= len(views):
            return tool_error(f"view_index {index} out of range (open 3D views: {len(views)}).", "INVALID_ARGS",
                              "Omit view_index to create a view, or use an open view index.")
        canvas = views[index]

    settings = canvas.mapSettings()
    if settings is None:
        return tool_error("The 3D map view has no map settings.", "EXECUTION_FAILED")
    vertical_scale = float(args.get("vertical_exaggeration") or 1.0)
    tile_resolution = int(args.get("tile_resolution") or _DEFAULT_TILE_RESOLUTION)
    ground_error = float(args.get("terrain_ground_error") or _DEFAULT_GROUND_ERROR)
    screen_error = float(args.get("terrain_screen_error") or _DEFAULT_SCREEN_ERROR)
    terrain_word = None
    if dem is not None:
        terrain = terrain_type()
        terrain.setLayer(dem)
        terrain_word = f"DEM {dem.name()}"
    elif created and _project_terrain_is_flat():
        terrain = flat_type()
        terrain_word = "flat"
    else:
        terrain = None
    if terrain is not None:
        terrain.setVerticalScale(vertical_scale)
        terrain.setMapTileResolution(tile_resolution)
        terrain.setMaximumGroundError(ground_error)
        terrain.setMaximumScreenError(screen_error)
        settings.setTerrainSettings(terrain)
    settings.setTerrainVerticalScale(vertical_scale)
    settings.setMapTileResolution(tile_resolution)
    settings.setMaxTerrainGroundError(ground_error)
    settings.setMaxTerrainScreenError(screen_error)

    camera = _look_at_main_map(canvas, settings) if created else None

    current_views = open_3d_views()
    current = settings.terrainSettings() if hasattr(settings, "terrainSettings") else None
    result = {
        "created": created,
        "terrain": terrain_word or (f"kept ({current.type()})" if current is not None else "kept"),
        "vertical_exaggeration": vertical_scale,
        "tile_resolution": tile_resolution,
        "terrain_ground_error": ground_error,
        "terrain_screen_error": screen_error,
        "open_3d_views": len(current_views) if current_views is not None else None,
        "layers_3d": [layer.name() for layer in settings.layers() if _has_3d_renderer(layer)][:20],
    }
    in_view = {layer.id() for layer in settings.layers() if layer is not None}
    hidden = [layer.name() for layer in QgsProject.instance().mapLayers().values()
              if _has_3d_renderer(layer) and layer.id() not in in_view]
    if hidden:

        result["hidden_3d_layers"] = hidden[:20]
    if camera:
        result["camera"] = camera
    return result


def _project_terrain_is_flat() -> bool:

    provider = QgsProject.instance().elevationProperties().terrainProvider()
    return provider is None or provider.type() == "flat"


def _has_3d_renderer(layer) -> bool:
    return layer is not None and hasattr(layer, "renderer3D") and layer.renderer3D() is not None


def _look_at_main_map(canvas, settings) -> dict | None:

    controller = canvas.cameraController() if hasattr(canvas, "cameraController") else None
    look = getattr(controller, "setLookingAtMapPoint", None)
    main = iface.mapCanvas() if iface is not None else None
    if not callable(look) or main is None:
        return None
    extent = main.extent()
    try:
        transform = QgsCoordinateTransform(main.mapSettings().destinationCrs(), settings.crs(), QgsProject.instance())
        extent = transform.transformBoundingBox(extent)
    except Exception:  # noqa: BLE001
        return None
    if extent.isEmpty() or not extent.isFinite():
        return None
    centre = extent.center()
    distance = max(extent.width(), extent.height()) * 1.2
    look(QgsVector3D(centre.x(), centre.y(), 0.0), float(distance), 45.0, 0.0)
    return {"centre": [round(centre.x(), 2), round(centre.y(), 2)], "distance": round(distance, 1), "pitch": 45.0}
