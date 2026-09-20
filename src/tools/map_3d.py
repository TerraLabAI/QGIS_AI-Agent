# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later













































from __future__ import annotations

import contextlib

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsVector3D,
)
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
                "heading": {"type": "number", "minimum": -360, "maximum": 360},
                "pitch": {"type": "number", "minimum": 0, "maximum": 90},
                "distance": {"type": "number", "exclusiveMinimum": 0, "maximum": 100000000},
                "center": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                "crs": {"type": "string"},
                "background_color": {"type": "string", "minLength": 1, "maxLength": 40},
                "sun_azimuth": {"type": "number", "minimum": 0, "maximum": 360},
                "sun_altitude": {"type": "number", "minimum": 0, "maximum": 90},
                "shadows": {"type": "boolean"},
                "ambient_occlusion": {"type": "boolean"},
                "eye_dome_lighting": {"type": "boolean"},
            },


            "x-view-raised": True,
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
        refused = _opengl_refusal()
        if refused:
            return tool_error(refused, "UNSUPPORTED_QGIS_VERSION",
                              "QGIS draws its 3D views with OpenGL 3.3 shaders; a graphics driver update or "
                              "a session on the machine itself (not a remote desktop) gives them.")
        full = iface.mapCanvas().projectExtent()
        if full.isEmpty() or not full.isFinite():

            return tool_error("The project extent is empty, so QGIS opens no 3D map view.", "EXECUTION_FAILED",
                              "A 3D view frames the project's visible layers; add or show a layer first.")
        title = str(args.get("title") or "AI Agent 3D Map")
        try:
            canvas = create(title)


            for number in range(2, len(views) + 2):
                if canvas is not None:
                    break
                canvas = create(f"{title} {number}")
        except Exception as exc:  # noqa: BLE001
            return tool_error(f"QGIS could not open a 3D map view: {exc}", "EXECUTION_FAILED",
                              "OpenGL support may be missing.")
        if canvas is None:
            return tool_error("QGIS did not create a 3D map view.", "EXECUTION_FAILED",
                              f"{len(views)} 3D views are open; view_index configures one of them."
                              if views else "OpenGL support may be missing.")
    else:
        index = int(args["view_index"])
        if index >= len(views):
            return tool_error(f"view_index {index} out of range (open 3D views: {len(views)}).", "INVALID_ARGS",
                              "A 3D view whose window was closed is deleted; without view_index a new view opens.")
        canvas = views[index]

    settings = canvas.mapSettings()
    if settings is None:
        return tool_error("The 3D map view has no map settings.", "EXECUTION_FAILED")

    kept = None if created else (settings.terrainSettings() if hasattr(settings, "terrainSettings") else None)
    vertical_scale = float(args.get("vertical_exaggeration") or (kept.verticalScale() if kept else 1.0))
    tile_resolution = int(args.get("tile_resolution") or (kept.mapTileResolution() if kept
                                                           else _DEFAULT_TILE_RESOLUTION))
    ground_error = float(args.get("terrain_ground_error") or (kept.maximumGroundError() if kept
                                                              else _DEFAULT_GROUND_ERROR))
    screen_error = float(args.get("terrain_screen_error") or (kept.maximumScreenError() if kept
                                                              else _DEFAULT_SCREEN_ERROR))
    terrain_word = None
    current = settings.terrainSettings() if hasattr(settings, "terrainSettings") else None
    wanted = (vertical_scale, tile_resolution, ground_error, screen_error)
    if dem is not None:
        terrain = terrain_type()
        terrain.setLayer(dem)
        terrain_word = f"DEM {dem.name()}"
    elif created and _project_terrain_is_flat():
        terrain = flat_type()
        terrain_word = "flat"
    elif current is not None and wanted != (current.verticalScale(), current.mapTileResolution(),
                                            current.maximumGroundError(), current.maximumScreenError()):

        terrain = current.clone()
    else:
        terrain = None
    if terrain is not None:
        terrain.setVerticalScale(vertical_scale)
        terrain.setMapTileResolution(tile_resolution)
        terrain.setMaximumGroundError(ground_error)
        terrain.setMaximumScreenError(screen_error)
        if current is not None and hasattr(terrain, "equals") and terrain.equals(current):
            terrain = None

    try:
        camera = _point_camera(canvas, settings, args, created)
    except ValueError as exc:
        return tool_error(str(exc), "INVALID_ARGS",
                          "center is [x, y] in crs (default the project CRS), for example EPSG:4326 lon, lat.")
    ground = None
    if created and camera:
        ground = ground_square(settings, camera["center"][0], camera["center"][1], camera["distance"])
    waits = _rebuild_when_idle(canvas, ground, terrain)

    try:
        scene = _scene_look(settings, args, (camera or {}).get("distance"))
    except ValueError as exc:
        return tool_error(str(exc), "INVALID_ARGS", "background_color is a colour such as #0b1026 or navy.")
    from . import style_3d

    moved = style_3d.refloat_labels(settings.layers()) if _annotations_3d() else []

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
        "view": _view_facts(canvas, current_views),
        "layers_3d": [layer.name() for layer in settings.layers() if _has_3d_renderer(layer)][:20],
        "ground_layers": _ground_layers(settings)[:20],
    }
    flat = [layer.name() for layer in settings.layers() if layer is not None and hasattr(layer, "labelsEnabled")
            and layer.labelsEnabled()]
    if flat:

        result["labels_on_2d_map_only"] = flat[:20]
    in_view = {layer.id() for layer in settings.layers() if layer is not None}
    hidden = [layer.name() for layer in QgsProject.instance().mapLayers().values()
              if _has_3d_renderer(layer) and layer.id() not in in_view]
    if hidden:

        result["hidden_3d_layers"] = hidden[:20]
    if camera:
        result["camera"] = camera
    if ground is not None:

        result["ground_km"] = round(ground.width() / 1000.0, 1)
    if waits:

        result["applied_after_load"] = waits
    if scene:
        result["scene"] = scene
    if moved:

        result["labels_3d_moved"] = moved
    return result


def _annotations_3d() -> bool:

    try:
        from qgis._3d import QgsAnnotationLayer3DRenderer  # noqa: F401
    except ImportError:
        return False
    return True


def _opengl_refusal() -> str | None:

    from qgis.PyQt.QtGui import QOpenGLContext, QSurfaceFormat

    context = QOpenGLContext()
    context.setFormat(QSurfaceFormat.defaultFormat())
    if not context.create():
        return "This machine gives QGIS no OpenGL context, and a 3D view draws through one."
    found = context.format()
    version = (found.majorVersion(), found.minorVersion())
    if version < (3, 3):
        return (f"This machine's OpenGL is {version[0]}.{version[1]}; QGIS's 3D views need 3.3 or newer, and a "
                "view opened on less crashes QGIS.")
    return None


def _project_terrain_is_flat() -> bool:

    provider = QgsProject.instance().elevationProperties().terrainProvider()
    return provider is None or provider.type() == "flat"


def _ground_layers(settings) -> list:



    layers = settings.layers()
    themes = settings.mapThemeCollection() if hasattr(settings, "mapThemeCollection") else None
    theme = settings.terrainMapTheme() if hasattr(settings, "terrainMapTheme") else ""
    if theme and themes is not None and themes.hasMapTheme(theme):
        layers = themes.mapThemeVisibleLayers(theme)
    notes = QgsProject.instance().mainAnnotationLayer()
    return [layer.name() for layer in layers
            if layer is not None and layer is not notes and not _has_3d_renderer(layer)]


def _has_3d_renderer(layer) -> bool:
    return layer is not None and hasattr(layer, "renderer3D") and layer.renderer3D() is not None


def _main_map_view(settings):

    main = iface.mapCanvas() if iface is not None else None
    if main is None:
        return None
    extent = main.extent()
    try:
        transform = QgsCoordinateTransform(main.mapSettings().destinationCrs(), settings.crs(), QgsProject.instance())
        extent = transform.transformBoundingBox(extent)
    except Exception:  # noqa: BLE001
        return None
    if extent.isEmpty() or not extent.isFinite():
        return None
    return extent.center().x(), extent.center().y(), max(extent.width(), extent.height()) * 1.2


def _view_point(center, crs_text, view_crs):

    try:
        x, y = float(center[0]), float(center[1])
    except (TypeError, ValueError, IndexError):
        raise ValueError(f"center {center!r} is not [x, y].") from None
    source = QgsCoordinateReferenceSystem(str(crs_text)) if crs_text else QgsProject.instance().crs()
    if not source.isValid():
        raise ValueError(f"crs {crs_text!r} is not a CRS QGIS knows.")
    if source == view_crs:
        return x, y
    try:
        point = QgsCoordinateTransform(source, view_crs, QgsProject.instance()).transform(QgsPointXY(x, y))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"center {center!r} does not transform from {source.authid()} to the view's "
                         f"{view_crs.authid()}: {exc}") from None
    return point.x(), point.y()


def _ground_height(settings, x, y) -> float:

    terrain = settings.terrainSettings() if hasattr(settings, "terrainSettings") else None
    offset = float(terrain.elevationOffset()) if hasattr(terrain, "elevationOffset") else 0.0
    layer = terrain.layer() if hasattr(terrain, "layer") else None
    if not isinstance(layer, QgsRasterLayer):
        return offset
    try:
        point = QgsCoordinateTransform(settings.crs(), layer.crs(), QgsProject.instance()).transform(QgsPointXY(x, y))
        value, found = layer.dataProvider().sample(point, 1)
    except Exception:  # noqa: BLE001
        return offset
    return float(value) * float(terrain.verticalScale()) + offset if found else offset


def _looked_at(controller, settings):


    if hasattr(controller, "lookingAtMapPoint"):
        return controller.lookingAtMapPoint()
    return settings.worldToMapCoordinates(controller.lookingAtPoint())


def _point_camera(canvas, settings, args: dict, created: bool) -> dict | None:


    controller = canvas.cameraController() if hasattr(canvas, "cameraController") else None
    if controller is None:
        return None
    pose = controller.cameraPose()
    at = _looked_at(controller, settings)
    x, y, z = at.x(), at.y(), at.z()
    distance, pitch, heading = pose.distanceFromCenterPoint(), pose.pitchAngle(), pose.headingAngle()
    moved = False
    framed = _main_map_view(settings) if created else None
    if framed is not None:
        (x, y, distance), pitch, heading, z, moved = framed, 45.0, 0.0, None, True
    if args.get("center") is not None:
        (x, y), z, moved = _view_point(args["center"], args.get("crs"), settings.crs()), None, True
    for key in ("distance", "pitch", "heading"):
        if args.get(key) is not None:
            moved = True
    if moved:
        distance = float(args["distance"]) if args.get("distance") is not None else distance
        pitch = float(args["pitch"]) if args.get("pitch") is not None else pitch
        heading = float(args["heading"]) % 360.0 if args.get("heading") is not None else heading
        if z is None:
            z = _ground_height(settings, x, y)
    if moved:
        look = getattr(controller, "setLookingAtMapPoint", None)
        if look is not None:
            look(QgsVector3D(x, y, z), float(distance), float(pitch), float(heading))
        else:
            controller.setLookingAtPoint(settings.mapToWorldCoordinates(QgsVector3D(x, y, z)),
                                         float(distance), float(pitch), float(heading))
    pose, at = controller.cameraPose(), _looked_at(controller, settings)
    return {"heading": round(pose.headingAngle() % 360.0, 1), "pitch": round(pose.pitchAngle(), 1),
            "distance": round(pose.distanceFromCenterPoint(), 1),
            "center": [round(at.x(), 2), round(at.y(), 2)], "crs": settings.crs().authid()}




_GROUND_DISTANCES = 5.0


def _content_reach(settings, x: float, y: float) -> float:

    terrain = settings.terrainSettings() if hasattr(settings, "terrainSettings") else None
    layers = [layer for layer in settings.layers() if _has_3d_renderer(layer)]
    dem = terrain.layer() if hasattr(terrain, "layer") else None
    if dem is not None:
        layers.append(dem)
    reach = 0.0
    for layer in layers:
        box = None
        with contextlib.suppress(Exception):
            box = QgsCoordinateTransform(layer.crs(), settings.crs(), QgsProject.instance()).transformBoundingBox(
                layer.extent())
        if box is not None and box.isFinite() and not box.isEmpty():
            reach = max(reach, abs(box.xMinimum() - x), abs(box.xMaximum() - x),
                        abs(box.yMinimum() - y), abs(box.yMaximum() - y))
    return reach


def ground_square(settings, x: float, y: float, distance: float):





    from qgis.core import QgsRectangle

    extent = settings.extent()
    half = _content_reach(settings, x, y) or _GROUND_DISTANCES * distance
    if extent.isFinite() and not extent.isEmpty():
        half = min(half, max(abs(extent.xMinimum() - x), abs(extent.xMaximum() - x),
                             abs(extent.yMinimum() - y), abs(extent.yMaximum() - y)))
    if not half > 0:
        return None
    return QgsRectangle(x - half, y - half, x + half, y + half)


def _rebuild_when_idle(canvas, ground, terrain) -> list:







    if ground is None and terrain is None:
        return []
    settings = canvas.mapSettings()
    scene = canvas.scene() if hasattr(canvas, "scene") else None
    pending = getattr(scene, "totalPendingJobsCount", None)

    def apply() -> None:
        if ground is not None:
            settings.setExtent(ground)
        if terrain is not None:
            settings.setTerrainSettings(terrain)


            layers = settings.layers()
            settings.setLayers([layer for layer in layers if not _has_3d_renderer(layer)])
            settings.setLayers(layers)

    if scene is None or not callable(pending) or pending() == 0:
        apply()
        return []
    from qgis.PyQt import sip
    from qgis.PyQt.QtCore import QTimer





    done: list = []

    def settle() -> None:

        if done or sip.isdeleted(scene) or scene.totalPendingJobsCount() != 0:
            return
        done.append(True)
        scene.totalPendingJobsCountChanged.disconnect(check)
        apply()

    def check(*_args) -> None:
        if not done and scene.totalPendingJobsCount() == 0:
            QTimer.singleShot(0, settle)

    canvas.setProperty("terralabWaiting3D", [*(canvas.property("terralabWaiting3D") or []), check])
    scene.totalPendingJobsCountChanged.connect(check)
    return [name for name, value in (("ground", ground), ("terrain", terrain)) if value is not None]


def _view_facts(canvas, views) -> dict:




    facts = {"index": next((i for i, view in enumerate(views or []) if view is canvas), None)}
    holder = canvas.parent() if hasattr(canvas, "parent") else None
    main = iface.mainWindow() if iface is not None else None
    docked = holder is not None and main is not None and holder is main.windowHandle()
    facts["docked"] = docked
    if holder is None:
        return facts
    if not docked:
        facts["name"] = holder.title()
        from qgis.PyQt.QtGui import QWindow

        if not holder.isVisible() or holder.visibility() == QWindow.Visibility.Minimized:
            holder.showNormal()
        holder.raise_()
        holder.requestActivate()
        facts["window"] = {"shown": bool(holder.isVisible()) and holder.visibility() != QWindow.Visibility.Minimized,
                           "raised": True}
    return facts


_SCENE_KEYS = ("background_color", "sun_azimuth", "sun_altitude", "shadows", "ambient_occlusion", "eye_dome_lighting")

_SUN = (315.0, 45.0)


def _directional(light) -> bool:
    from qgis.core import Qgis

    return light.type() == Qgis.LightSourceType.Directional


def _sun_angles(light) -> tuple[float, float]:

    import math

    from qgis._3d import QgsDirectionalLightSettings
    from qgis.PyQt import sip


    d = sip.cast(light, QgsDirectionalLightSettings).direction()
    flat = math.hypot(d.x(), d.y())
    azimuth = 0.0 if flat == 0 else math.degrees(math.atan2(-d.x(), -d.y())) % 360.0
    return round(azimuth, 1), round(math.degrees(math.atan2(-d.z(), flat)), 1)


def _scene_look(settings, args: dict, camera_distance=None) -> dict | None:





    import math

    from qgis.core import QgsReadWriteContext
    from qgis.PyQt.QtGui import QColor
    from qgis.PyQt.QtXml import QDomDocument

    if all(args.get(key) is None for key in _SCENE_KEYS):
        return None
    if args.get("background_color") is not None:
        colour = QColor(str(args["background_color"]))
        if not colour.isValid():
            raise ValueError(f"background_color {args['background_color']!r} is not a colour.")
        settings.setBackgroundColor(colour)
    if args.get("eye_dome_lighting") is not None:
        settings.setEyeDomeLightingEnabled(bool(args["eye_dome_lighting"]))
    lights = list(settings.lightSources())
    suns = [light for light in lights if _directional(light)]
    placed = args.get("sun_azimuth") is not None or args.get("sun_altitude") is not None
    if placed or (args.get("shadows") and not suns):
        from qgis._3d import QgsDirectionalLightSettings

        old = _sun_angles(suns[0]) if suns else _SUN
        azimuth = float(args["sun_azimuth"]) if args.get("sun_azimuth") is not None else old[0]
        altitude = float(args["sun_altitude"]) if args.get("sun_altitude") is not None else old[1]
        flat = math.cos(math.radians(altitude))
        sun = QgsDirectionalLightSettings()
        sun.setDirection(QgsVector3D(-flat * math.sin(math.radians(azimuth)), -flat * math.cos(math.radians(azimuth)),
                                     -math.sin(math.radians(altitude))))

        settings.setLightSources([light.clone() for light in lights if not _directional(light)] + [sun])
    if args.get("shadows") is not None or args.get("ambient_occlusion") is not None:
        context = QgsReadWriteContext()
        context.setPathResolver(QgsProject.instance().pathResolver())
        document = QDomDocument()
        element = settings.writeXml(document, context)
        if args.get("shadows") is not None:
            shadows = element.firstChildElement("shadow-rendering")
            shadows.setAttribute("shadow-rendering-enabled", "1" if args["shadows"] else "0")
            directional = [light for light in settings.lightSources() if _directional(light)]
            if directional:
                shadows.setAttribute("selected-directional-light", "0")
                if hasattr(directional[0], "id"):
                    shadows.setAttribute("light-source", directional[0].id())
            extent = settings.extent()


            size = min(max(extent.width(), extent.height()), 10000.0) if not extent.isEmpty() else 0.0
            reach = int(max(1500.0, float(camera_distance) * 2.0 if camera_distance else size * 2.0))
            shadows.setAttribute("max-shadow-rendering-distance", str(reach))
        if args.get("ambient_occlusion") is not None:
            element.firstChildElement("screen-space-ambient-occlusion").setAttribute(
                "enabled", "1" if args["ambient_occlusion"] else "0")


        origin = settings.origin()
        node = element.firstChildElement("origin")
        for axis, value in (("x", origin.x()), ("y", origin.y()), ("z", origin.z())):
            node.setAttribute(axis, repr(float(value)))



        terrain = element.firstChildElement("terrain")
        generator = terrain.firstChildElement("generator")
        if not generator.isNull():
            terrain.removeChild(generator)
        layers = settings.layers()
        settings.readXml(element, context)




        settings.setLayers(layers)
        settings.shadowSettingsChanged.emit()
        settings.ambientOcclusionSettingsChanged.emit()
        settings.lightSourcesChanged.emit()
    return _scene_facts(settings)


def _scene_facts(settings) -> dict:
    from qgis.core import QgsReadWriteContext
    from qgis.PyQt.QtXml import QDomDocument

    element = settings.writeXml(QDomDocument(), QgsReadWriteContext())
    suns = [light for light in settings.lightSources() if _directional(light)]
    facts = {"background_color": settings.backgroundColor().name(),
             "shadows": element.firstChildElement("shadow-rendering").attribute("shadow-rendering-enabled") == "1",
             "ambient_occlusion":
                 element.firstChildElement("screen-space-ambient-occlusion").attribute("enabled") == "1",
             "eye_dome_lighting": bool(settings.eyeDomeLightingEnabled())}
    if facts["shadows"]:
        facts["shadow_reach_m"] = int(element.firstChildElement("shadow-rendering").attribute(
            "max-shadow-rendering-distance") or 0)
    if suns:
        facts["sun"] = dict(zip(("azimuth", "altitude"), _sun_angles(suns[0])))
    return facts
