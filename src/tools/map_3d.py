# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
















































from __future__ import annotations

import contextlib
import os
import uuid

from qgis.core import (
    Qgis,
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
from ._compat import enum_value
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
                "pitch": {"type": "number", "minimum": 0, "maximum": 180},
                "distance": {"type": "number", "exclusiveMinimum": 0, "maximum": 100000000},
                "center": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                "crs": {"type": "string"},
                "background_color": {"type": "string", "minLength": 1, "maxLength": 40},
                "sun_azimuth": {"type": "number", "minimum": 0, "maximum": 360},
                "sun_altitude": {"type": "number", "minimum": 0, "maximum": 90},
                "shadows": {"type": "boolean"},
                "ambient_occlusion": {"type": "boolean"},
                "eye_dome_lighting": {"type": "boolean"},
                "show_terrain": {"type": "boolean"},
            },


            "x-view-raised": True,


            "x-terrain-project-crs": True,
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
        return create, lambda: _XmlTerrain("dem"), lambda: _XmlTerrain("flat")
    return create, QgsDemTerrainSettings, QgsFlatTerrainSettings


def _configure_3d_map_view(args: dict) -> dict:

    api = _public_3d_api()
    if api is None:
        return tool_error(
            "This QGIS version does not expose the public 3D map-view API (QGIS 3.36 or newer is required).",
            "UNSUPPORTED_QGIS_VERSION",
            "An older QGIS version has no automatic 3D Map View; 3.36 and newer open one here.",
        )
    create, terrain_type, flat_type = api
    dem = None
    if args.get("dem_layer"):
        dem = _find_layer(str(args["dem_layer"]))
        if dem is None:
            return tool_error(f"DEM layer {args.get('dem_layer')!r} was not found.", "LAYER_NOT_FOUND",
                              hint="map3d_dem_not_found", dem_layer=str(args["dem_layer"]))
        if not isinstance(dem, QgsRasterLayer):
            return tool_error(f"{dem.name()!r} is not a raster DEM layer.", "INVALID_ARGS",
                              hint="map3d_dem_not_raster", dem_layer=dem.name())

    views = open_3d_views()
    if views is None:
        return tool_error("QGIS could not enumerate its 3D map views.", "EXECUTION_FAILED",
                          hint="map3d_views_unavailable")
    created = "view_index" not in args
    if created:
        refused = _opengl_refusal()
        if refused:
            return tool_error(refused, "UNSUPPORTED_QGIS_VERSION", hint="map3d_opengl_refused")
        full = iface.mapCanvas().projectExtent()
        if full.isEmpty() or not full.isFinite():

            return tool_error("The project extent is empty, so QGIS opens no 3D map view.", "EXECUTION_FAILED",
                              hint="map3d_extent_empty")
        title = str(args.get("title") or "AI Agent 3D Map")
        try:
            canvas = create(title)


            for number in range(2, len(views) + 2):
                if canvas is not None:
                    break
                canvas = create(f"{title} {number}")
        except Exception as exc:  # noqa: BLE001
            return tool_error(f"QGIS could not open a 3D map view: {exc}", "EXECUTION_FAILED",
                              hint="map3d_open_failed")
        if canvas is None:
            if views:
                return tool_error("QGIS did not create a 3D map view.", "EXECUTION_FAILED",
                                  hint="map3d_views_open", open_views=len(views))
            return tool_error("QGIS did not create a 3D map view.", "EXECUTION_FAILED",
                              hint="map3d_open_failed")
    else:
        index = int(args["view_index"])
        if index >= len(views):
            return tool_error(f"view_index {index} out of range (open 3D views: {len(views)}).", "INVALID_ARGS",
                              hint="map3d_view_index_out_of_range", view_index=index, open_views=len(views))
        canvas = views[index]

    settings = canvas.mapSettings()
    if settings is None:
        return tool_error("The 3D map view has no map settings.", "EXECUTION_FAILED")

    kept = None if created else _terrain_of(settings)
    vertical_scale = float(args.get("vertical_exaggeration") or (kept.verticalScale() if kept else 1.0))
    tile_resolution = int(args.get("tile_resolution") or (kept.mapTileResolution() if kept
                                                           else _DEFAULT_TILE_RESOLUTION))
    ground_error = float(args.get("terrain_ground_error") or (kept.maximumGroundError() if kept
                                                              else _DEFAULT_GROUND_ERROR))
    screen_error = float(args.get("terrain_screen_error") or (kept.maximumScreenError() if kept
                                                              else _DEFAULT_SCREEN_ERROR))
    terrain_word = None
    current = _terrain_of(settings)
    wanted = (vertical_scale, tile_resolution, ground_error, screen_error)
    reprojected = None
    if dem is not None:
        terrain = terrain_type()
        surface, reprojected = _terrain_in_project_crs(dem)
        terrain.setLayer(surface)
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
    show = args.get("show_terrain")
    if (show is not None and hasattr(settings, "terrainRenderingEnabled")
            and bool(show) == settings.terrainRenderingEnabled()):
        show = None
    try:
        _scene_setters(settings, args)
    except ValueError as exc:
        return tool_error(str(exc), "INVALID_ARGS", "background_color is a colour such as #0b1026 or navy.")


    edit = _scene_xml(args, (camera or {}).get("distance"))
    legacy = not hasattr(settings, "setTerrainSettings")
    waits = _rebuild_when_idle(canvas, ground, terrain, None if show is None else bool(show),
                               edit if legacy else None)
    if edit is not None and not legacy:
        _through_xml(settings, edit)
    scene = _scene_facts(settings) if any(args.get(key) is not None for key in _SCENE_KEYS) else None
    if scene and "scene" in waits:
        scene.update({key: bool(args[key]) for key in ("shadows", "ambient_occlusion") if args.get(key) is not None})
    from . import style_3d

    moved = style_3d.refloat_labels(settings.layers()) if _annotations_3d() else []

    current_views = open_3d_views()
    current = _terrain_of(settings)
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
    if hasattr(settings, "terrainRenderingEnabled"):
        result["terrain_shown"] = settings.terrainRenderingEnabled() if show is None else bool(show)
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

        result["ground_km"] = round(ground.width() / 1000.0, 1 if ground.width() >= 1000.0 else 2)
    if reprojected:

        result["terrain_reprojected"] = reprojected
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


def _terrain_in_project_crs(dem):










    target = QgsProject.instance().crs()
    if not target.isValid() or dem.crs() == target:
        return dem, None
    for layer in QgsProject.instance().mapLayers().values():
        if (layer.customProperty("terralab/terrain_of") == dem.id() and layer.isValid()
                and isinstance(layer, QgsRasterLayer) and layer.crs() == target):
            return layer, {"from": dem.crs().authid() or dem.crs().description(), "to": target.authid(),
                           "file": layer.source(), "reused": True}
    from osgeo import gdal

    from ..core import output_paths, security

    folder = output_paths.default_folder()
    os.makedirs(folder, exist_ok=True)
    tag = (target.authid() or "project CRS").replace(":", "-")
    path = os.path.join(folder, output_paths.safe_file_name(f"{dem.name()} 3D terrain {tag}", "terrain") + ".vrt")
    if not security.fits_path(path):

        folder = output_paths.exports_folder()
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"terrain-{uuid.uuid4().hex[:8]}.vrt")
    source = dem.dataProvider().dataSourceUri() if dem.dataProvider() is not None else dem.source()
    wkt = target.toWkt(enum_value((Qgis, "CrsWktVariant.PreferredGdal"),
                                  (QgsCoordinateReferenceSystem, "WKT_PREFERRED_GDAL")))
    read = _with_virtual_overviews(gdal, source, path[:-4] + ".base.vrt")
    warped = gdal.Warp(path, read, format="VRT", dstSRS=wkt, resampleAlg="bilinear")
    if warped is None:
        return dem, {"from": dem.crs().authid(), "to": target.authid(), "failed": gdal.GetLastErrorMsg()[:200]}
    warped = None
    layer = QgsRasterLayer(path, f"{dem.name()} (3D terrain, {target.authid() or 'project CRS'})", "gdal")
    if not layer.isValid():
        return dem, {"from": dem.crs().authid(), "to": target.authid(), "failed": "the warped VRT did not open"}
    layer.setCustomProperty("terralab/terrain_of", dem.id())
    QgsProject.instance().addMapLayer(layer, False)
    return layer, {"from": dem.crs().authid() or dem.crs().description(), "to": target.authid(), "file": path}


def _with_virtual_overviews(gdal, source: str, base: str) -> str:




    opened = gdal.Open(source)
    if opened is None or opened.GetRasterBand(1).GetOverviewCount() > 0:
        return source
    side = max(opened.RasterXSize, opened.RasterYSize)
    opened = None
    factors, factor = [], 2
    while side // factor >= 256:
        factors.append(str(factor))
        factor *= 2
    if not factors or gdal.BuildVRT(base, [source]) is None:
        return source
    with open(base, encoding="utf-8") as handle:
        text = handle.read()
    listed = " ".join(factors)
    text = text.replace("</VRTDataset>", f'  <OverviewList resampling="nearest">{listed}</OverviewList>\n</VRTDataset>')
    with open(base, "w", encoding="utf-8") as handle:
        handle.write(text)
    return base


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

    terrain = _terrain_of(settings)
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



_HEIGHT_SAMPLE = 200


def _content_height(settings, x, y, distance) -> float | None:




    from qgis.core import QgsRectangle, QgsWkbTypes

    lows, highs = [], []
    around = QgsRectangle(x - distance, y - distance, x + distance, y + distance)
    for layer in settings.layers():
        if not _has_3d_renderer(layer) or not hasattr(layer, "wkbType") or not QgsWkbTypes.hasZ(layer.wkbType()):
            continue
        box = None
        with contextlib.suppress(Exception):
            box = QgsCoordinateTransform(layer.crs(), settings.crs(), QgsProject.instance()).transformBoundingBox(
                layer.extent())
        if box is None or not box.intersects(around):
            continue
        span = _z_span(layer)
        if span is not None:
            lows.append(span[0])
            highs.append(span[1])
    return (min(lows) + max(highs)) / 2.0 if lows else None


def _z_span(layer):

    import math

    from qgis.core import QgsFeatureRequest

    box = layer.extent3D() if hasattr(layer, "extent3D") else None
    if box is not None and math.isfinite(box.zMinimum()) and math.isfinite(box.zMaximum()):
        return box.zMinimum(), box.zMaximum()
    if layer.providerType() not in ("memory", "ogr", "spatialite"):
        return None
    zs = []
    request = QgsFeatureRequest().setNoAttributes().setLimit(_HEIGHT_SAMPLE)
    for feature in layer.getFeatures(request):
        geometry = feature.geometry()
        if geometry.isNull():
            continue
        zs.extend(vertex.z() for vertex in geometry.vertices() if math.isfinite(vertex.z()))
    return (min(zs), max(zs)) if zs else None


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
            z = _content_height(settings, x, y, distance)
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

    terrain = _terrain_of(settings)
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
    reach = _content_reach(settings, x, y)
    half = max(reach, distance) if reach else _GROUND_DISTANCES * distance
    if extent.isFinite() and not extent.isEmpty():
        half = min(half, max(abs(extent.xMinimum() - x), abs(extent.xMaximum() - x),
                             abs(extent.yMinimum() - y), abs(extent.yMaximum() - y)))


    half = max(half, distance)
    if not half > 0:
        return None
    return QgsRectangle(x - half, y - half, x + half, y + half)


def _rebuild_when_idle(canvas, ground, terrain, show_terrain=None, scene_xml=None) -> list:










    if ground is None and terrain is None and show_terrain is None and scene_xml is None:
        return []
    settings = canvas.mapSettings()
    scene = canvas.scene() if hasattr(canvas, "scene") else None
    pending = getattr(scene, "totalPendingJobsCount", None)

    def apply() -> None:
        if ground is not None:
            settings.setExtent(ground)
        if terrain is not None or scene_xml is not None:
            if hasattr(settings, "setTerrainSettings"):
                settings.setTerrainSettings(terrain)
            else:
                (terrain or _XmlTerrain.of(settings)).write(settings, scene_xml)


            layers = settings.layers()
            settings.setLayers([layer for layer in layers if not _has_3d_renderer(layer)])
            settings.setLayers(layers)
        if show_terrain is not None:
            settings.setTerrainRenderingEnabled(show_terrain)

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
    return [name for name, value in (("ground", ground), ("terrain", terrain), ("show_terrain", show_terrain),
                                     ("scene", scene_xml)) if value is not None]


def _terrain_of(settings):

    if hasattr(settings, "terrainSettings"):
        return settings.terrainSettings()
    return _XmlTerrain.of(settings)


def _xml_context():
    from qgis.core import QgsReadWriteContext

    context = QgsReadWriteContext()
    context.setPathResolver(QgsProject.instance().pathResolver())
    return context


def _exact_place(settings, element) -> None:


    origin = settings.origin()
    node = element.firstChildElement("origin")
    for axis, value in (("x", origin.x()), ("y", origin.y()), ("z", origin.z())):
        node.setAttribute(axis, repr(float(value)))
    extent = settings.extent()
    node = element.firstChildElement("extent")
    if not node.isNull():
        for key, value in (("xMin", extent.xMinimum()), ("yMin", extent.yMinimum()), ("xMax", extent.xMaximum()),
                           ("yMax", extent.yMaximum())):
            node.setAttribute(key, repr(float(value)))


class _XmlTerrain:







    _VALUES = (("exaggeration", "_scale"), ("texture-size", "_tiles"), ("max-ground-error", "_ground"),
               ("max-terrain-error", "_screen"))

    def __init__(self, kind: str, layer=None, scale=1.0, tiles=_DEFAULT_TILE_RESOLUTION,
                 ground=_DEFAULT_GROUND_ERROR, screen=_DEFAULT_SCREEN_ERROR, offset=0.0):
        self._kind, self._layer, self._offset = kind, layer, float(offset)
        self._scale, self._tiles, self._ground, self._screen = float(scale), int(tiles), float(ground), float(screen)

    @classmethod
    def of(cls, settings) -> _XmlTerrain:
        from qgis.PyQt.QtXml import QDomDocument

        node = settings.writeXml(QDomDocument(), _xml_context()).firstChildElement("terrain")
        generator = node.firstChildElement("generator")
        kind = generator.attribute("type") or "flat"
        layer = QgsProject.instance().mapLayer(generator.attribute("layer")) if kind == "dem" else None
        return cls(kind, layer, settings.terrainVerticalScale(), settings.mapTileResolution(),
                   settings.maxTerrainGroundError(), settings.maxTerrainScreenError(),
                   settings.terrainElevationOffset())

    def type(self) -> str:
        return self._kind

    def layer(self):
        return self._layer

    def setLayer(self, layer) -> None:
        self._layer = layer

    def verticalScale(self) -> float:
        return self._scale

    def setVerticalScale(self, value) -> None:
        self._scale = float(value)

    def mapTileResolution(self) -> int:
        return self._tiles

    def setMapTileResolution(self, value) -> None:
        self._tiles = int(value)

    def maximumGroundError(self) -> float:
        return self._ground

    def setMaximumGroundError(self, value) -> None:
        self._ground = float(value)

    def maximumScreenError(self) -> float:
        return self._screen

    def setMaximumScreenError(self, value) -> None:
        self._screen = float(value)

    def elevationOffset(self) -> float:
        return self._offset

    def clone(self) -> _XmlTerrain:
        return _XmlTerrain(self._kind, self._layer, self._scale, self._tiles, self._ground, self._screen, self._offset)

    def equals(self, other) -> bool:
        def key(terrain):
            layer = terrain.layer()
            return (terrain.type(), layer.id() if layer is not None else "", terrain.verticalScale(),
                    terrain.mapTileResolution(), terrain.maximumGroundError(), terrain.maximumScreenError())
        return isinstance(other, _XmlTerrain) and key(self) == key(other)

    def write(self, settings, edit=None) -> None:

        from qgis.PyQt.QtXml import QDomDocument

        context = _xml_context()
        element = settings.writeXml(QDomDocument(), context)
        node = element.firstChildElement("terrain")
        for name, attribute in self._VALUES:
            value = getattr(self, attribute)
            node.setAttribute(name, str(value) if isinstance(value, int) else repr(value))
        generator = node.firstChildElement("generator")
        generator.setAttribute("type", self._kind)
        if self._kind == "dem" and self._layer is not None:
            generator.setAttribute("layer", self._layer.id())

            for name, default in (("resolution", "16"), ("skirt-height", "10")):
                if not generator.attribute(name):
                    generator.setAttribute(name, default)
        if edit is not None:
            edit(settings, element)
        _exact_place(settings, element)
        settings.readXml(element, context)
        settings.resolveReferences(QgsProject.instance())


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


def _scene_setters(settings, args: dict) -> None:

    import math

    from qgis.PyQt.QtGui import QColor

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


def _scene_xml(args: dict, camera_distance=None):






    if args.get("shadows") is None and args.get("ambient_occlusion") is None:
        return None

    def edit(settings, element) -> None:
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

    return edit


def _through_xml(settings, edit) -> None:

    from qgis.PyQt.QtXml import QDomDocument

    context = _xml_context()
    element = settings.writeXml(QDomDocument(), context)
    edit(settings, element)
    _exact_place(settings, element)



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
