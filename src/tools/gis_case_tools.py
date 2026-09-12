# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Focused tools added from the real GIS case review."""





from __future__ import annotations

import os
import uuid
import zipfile

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRaster,
    QgsRectangle,
    QgsSpatialIndex,
    QgsTask,
    QgsVectorLayer,
    QgsWkbTypes,
)

from ..core.geometry_budget import VertexBudget
from ..core.policy import create_managed_temp_dir
from ..core.qt_compat import enum_member, field_type
from ..core.security import validate_path
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .layer_lookup import _find_layer, _layer_not_found_error
from .processing_guards import _utm_authid






MAX_GEOCODE_ADDRESSES = 5000









MAX_TOPOLOGY_FEATURES = 20_000

_REPAIR_MAX_FOLDERS = 20_000
_REPAIR_MAX_DEPTH = 12
_GEOCODE_LAYER_TASKS: dict[str, dict] = {}




_GEOCODE_LAYER_ALIVE: dict[str, object] = {}


def register_gis_case_tools(registry: ToolRegistry):
    _enable_add_data_subdatasets()
    registry.register(
        Tool(
            name="repair_layer_paths",
            input_schema={
                "type": "object",
                "properties": {
                    "layer_name": {"type": "string"},
                    "search_root": {"type": "string"},
                },
                "required": ["layer_name"],
            },
            handler=_repair_layer_paths,
        )
    )
    registry.register(
        Tool(
            name="package_project",
            input_schema={
                "type": "object",
                "properties": {
                    "output_path": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                },
                "required": ["output_path"],
            },
            handler=_package_project,
            destructive=True,
        )
    )
    registry.register(
        Tool(
            name="get_isochrone",
            input_schema={
                "type": "object",
                "properties": {
                    "points": {
                        "type": "array",
                        "maxItems": 25,
                        "items": {
                            "type": "object",
                            "properties": {
                                "lon": {"type": "number", "minimum": -180, "maximum": 180},
                                "lat": {"type": "number", "minimum": -90, "maximum": 90},
                            },
                            "required": ["lon", "lat"],
                        },
                        "minItems": 1,
                    },
                    "minutes": {"anyOf": [
                        {"type": "number", "minimum": 1, "maximum": 1440},
                        {"type": "array", "items": {"type": "number", "minimum": 1, "maximum": 1440},
                         "minItems": 1, "maxItems": 8},
                    ]},
                    "mode": {"type": "string", "enum": ["driving", "walking", "cycling"]},
                    "road_layer": {"type": "string"},
                    "output_name": {"type": "string"},
                },
                "required": ["points", "minutes", "mode"],
            },
            handler=_get_isochrone,
        )
    )
    registry.register(
        Tool(
            name="elevation_profile",
            input_schema={
                "type": "object",
                "properties": {
                    "line_layer": {"type": "string"},
                    "line_wkt": {"type": "string"},
                    "wkt_crs": {"type": "string"},
                    "dem": {"type": "string"},
                    "sample_count": {"type": "integer", "minimum": 2, "maximum": 2000},
                },
                "required": ["dem"],
            },
            handler=_elevation_profile,
        )
    )
    registry.register(
        Tool(
            name="check_topology",
            input_schema={
                "type": "object",
                "properties": {
                    "layer": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["layer"],
            },
            handler=_check_topology,
        )
    )
    registry.register(
        Tool(
            name="geocode_layer",
            input_schema={
                "type": "object",
                "properties": {
                    "layer": {"type": "string"},
                    "address_field": {"type": "string"},
                    "output_name": {"type": "string"},
                },
                "required": ["layer", "address_field"],
            },
            handler=_geocode_layer,
        )
    )
    registry.register(
        Tool(
            name="get_geocode_layer_status",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                },
                "required": ["task_id"],
            },
            handler=_get_geocode_layer_status,
        )
    )


def _enable_add_data_subdatasets() -> None:
    """Pass facade ``layer`` through to NetCDF/HDF loading without editing facade.py."""
    from . import facade
    from .layer_io_tools import _add_raster_layer

    original = facade._dispatch_add
    if getattr(original, "_gis_case_subdataset_support", False):
        return

    def dispatch(kind: str, args: dict):






        if kind == "raster" and not facade._is_url(str(args.get("source") or "")):
            return _add_raster_layer({"path": args["source"], "name": args.get("name"), "layer": args.get("layer")})
        return original(kind, args)

    dispatch._gis_case_subdataset_support = True
    facade._dispatch_add = dispatch


def _source_path(layer) -> str:
    return (layer.source() or "").split("|", 1)[0]


def _repair_layer_paths(args: dict) -> dict:
    layer = _find_layer(args["layer_name"])
    if layer is None:
        return _layer_not_found_error(args["layer_name"])
    if layer.isValid():
        return {"layer": layer.name(), "repaired": False, "note": "The layer loads fine; nothing to repair."}
    source = layer.source() or ""
    old_path = _source_path(layer)
    filename = os.path.basename(old_path)
    if not filename:
        return tool_error("The layer has no local file name.", "INVALID_ARGS", "Use a local file-backed layer.")
    project_path = QgsProject.instance().fileName()
    roots = [os.path.dirname(project_path)] if project_path else []
    if args.get("search_root"):
        root = os.path.abspath(os.path.expanduser(args["search_root"]))
        error = validate_path(root, write=False)
        if error:
            return {"_error": error}
        roots.append(root)
    matches = []





    visited = 0
    exhausted = False
    for root in dict.fromkeys(root for root in roots if root and os.path.isdir(root)):
        for folder, folders, files in os.walk(root):
            visited += 1
            if visited > _REPAIR_MAX_FOLDERS:
                exhausted = True
                break
            if folder.count(os.sep) - root.count(os.sep) >= _REPAIR_MAX_DEPTH:
                folders[:] = []
            if filename in files:
                found = os.path.join(folder, filename)
                if found not in matches:
                    matches.append(found)
                if len(matches) > 8:
                    break
        if exhausted:
            break
    if not matches:
        cut = (f" The search stopped after {_REPAIR_MAX_FOLDERS:,} folders." if exhausted else "")
        return {"_error": f"Could not find {filename} under the project folder or search root.{cut}",
                "_suggestion": ("Pass a search_root closer to the file." if exhausted else
                                "Check the file still exists, or pass search_root.")}
    if len(matches) > 1:
        return {
            "layer": layer.name(),
            "old_path": old_path,
            "candidates": matches,
            "_error": "More than one matching file exists. Narrow search_root before changing the source.",
        }
    provider = layer.providerType()
    layer.setDataSource(matches[0] + source[len(old_path):], layer.name(), provider)
    if not layer.isValid():
        return {"_error": f"QGIS could not load {matches[0]} with provider {provider}."}
    return {"layer": layer.name(), "old_path": old_path, "new_path": matches[0], "repaired": True}


def _arcname(path: str, project_dir: str) -> str:
    """Where one file sits inside the package."""







    try:
        if os.path.commonpath([project_dir, path]) == project_dir:
            return os.path.relpath(path, project_dir)
    except ValueError:
        pass
    return os.path.basename(path)


def _package_project(args: dict) -> dict:
    project = QgsProject.instance()
    project_path = project.fileName()
    if not project_path or not os.path.isfile(project_path):
        return tool_error(
            "Save the project before packaging it.",
            "INVALID_ARGS",
            "Call save_project, then package_project.",
        )
    output = os.path.abspath(os.path.expanduser(args["output_path"]))
    error = validate_path(output, write=True)
    if error:
        return {"_error": error}
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    project_dir = os.path.dirname(project_path)




    seen: dict = {os.path.normcase(os.path.abspath(project_path)): project_path}
    skipped = []
    for layer in project.mapLayers().values():
        path = _source_path(layer)
        if path and os.path.isfile(path):
            seen.setdefault(os.path.normcase(os.path.abspath(path)), path)
        else:
            skipped.append(layer.name())
    local = set(seen.values())
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(local):
            archive.write(path, _arcname(path, project_dir))
            if path.lower().endswith(".shp"):
                stem, _ = os.path.splitext(path)
                for suffix in (".dbf", ".shx", ".prj", ".cpg"):
                    if os.path.isfile(stem + suffix):
                        archive.write(stem + suffix, _arcname(stem + suffix, project_dir))
    return {
        "package_path": output,
        "file_count": len(local),
        "skipped_layers": skipped,
        "file_size": os.path.getsize(output),
        "size_units": "bytes",
    }






_ROAD_NAME_HINTS = ("road", "highway", "street", "route", "voirie", "rue", "walk", "network", "réseau")



_ISOCHRONE_FRINGE_M = 60.0
_ISOCHRONE_MAX_STARTS = 25
_ISOCHRONE_SNAP_M = 500.0
_ISOCHRONE_SPEEDS_KMH = {"driving": 50.0, "walking": 5.0, "cycling": 15.0}


def _is_line_layer(layer) -> bool:
    if not isinstance(layer, QgsVectorLayer):
        return False
    try:
        return QgsWkbTypes.geometryType(layer.wkbType()) == enum_member(QgsWkbTypes, "GeometryType", "LineGeometry")
    except AttributeError:
        from qgis.core import Qgis
        return layer.geometryType() == Qgis.GeometryType.Line


def _guess_road_layer():
    """The loaded line layer that looks like a road network, or None."""
    for layer in QgsProject.instance().mapLayers().values():
        if not _is_line_layer(layer):
            continue
        name = layer.name().lower()
        fields = {field.name().lower() for field in layer.fields()}
        if any(hint in name for hint in _ROAD_NAME_HINTS) or "highway" in fields or {"class", "subtype"} <= fields:
            return layer
    return None






_ISOCHRONE_COVER = 0.9


def _covers(extent, window) -> bool:
    """Whether ``extent`` holds enough of ``window`` for the reach to be real."""
    try:
        if window.isEmpty():
            return True
        inside = extent.intersect(window)
        return (inside.width() * inside.height()) >= _ISOCHRONE_COVER * (window.width() * window.height())
    except Exception:  # nosec B110 - an unmeasurable window is not a refusal
        return True


def _get_isochrone(args: dict) -> dict:
    """A service area per start point, as polygons."""








    import processing

    road_layer_name = args.get("road_layer")
    roads = _find_layer(road_layer_name) if road_layer_name else _guess_road_layer()
    if roads is None:
        if road_layer_name:
            return _layer_not_found_error(road_layer_name)
        return tool_error(
            "No road line layer is loaded for a service area.", "EXECUTION_FAILED",
            "Load the roads first: fetch_overture theme roads (or fetch_osm_data way[highway]) over a box a "
            "little larger than the reach, then call get_isochrone again, naming it in road_layer if several "
            "line layers are loaded.")
    if not _is_line_layer(roads):
        return tool_error(f"{roads.name()} is not a line layer.", "INVALID_ARGS",
                          "road_layer is the road network: a line layer such as fetch_overture theme roads.")




    if "/vsicurl/" in str(roads.source()):
        return tool_error(
            f"{roads.name()} is a streamed remote tile; the road graph needs a local layer.", "INVALID_ARGS",
            "Fetch the roads again with fetch_overture theme roads, mode clip, over a bbox a little larger "
            "than the reach (confirm_large true if it says so), then call get_isochrone with that layer.")





    asked = args["minutes"]
    rings = sorted({float(m) for m in (asked if isinstance(asked, list) else [asked])})
    minutes = rings[-1]
    mode = str(args["mode"])
    speed = _ISOCHRONE_SPEEDS_KMH[mode]
    reach_m = speed * 1000.0 * minutes / 60.0
    points = list(args["points"])
    if len(points) > _ISOCHRONE_MAX_STARTS:
        return tool_error(
            f"{len(points)} start points; get_isochrone takes {_ISOCHRONE_MAX_STARTS} per call, because every "
            "start is a full search of the road graph on the main thread.", "INVALID_ARGS",
            f"Keep the {_ISOCHRONE_MAX_STARTS} that matter, or split the list across calls.")
    starts = QgsVectorLayer("Point?crs=EPSG:4326&field=start:integer", "isochrone_starts", "memory")
    features = []
    for index, point in enumerate(points):
        feature = QgsFeature(starts.fields())
        feature.setAttribute("start", index + 1)
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(float(point["lon"]), float(point["lat"]))))
        features.append(feature)
    starts.dataProvider().addFeatures(features)
    starts.updateExtents()





    margin_deg = (reach_m + _ISOCHRONE_FRINGE_M) / 111_320.0 * 1.2
    lons = [float(p["lon"]) for p in points]
    lats = [float(p["lat"]) for p in points]
    needed = QgsRectangle(min(lons) - margin_deg, min(lats) - margin_deg,
                          max(lons) + margin_deg, max(lats) + margin_deg)
    window = QgsRectangle(needed)
    if roads.crs().isValid() and roads.crs().authid() != "EPSG:4326":
        try:
            window = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), roads.crs(),
                                            QgsProject.instance()).transformBoundingBox(window)
        except Exception:  # nosec B110 - an untransformable window keeps the whole layer
            window = None







    if window is not None and not _covers(roads.extent(), window):
        return tool_error(
            f"{roads.name()} does not reach {minutes:.0f} minutes from every start, so the polygon would be "
            "the shape of the download box.", "INVALID_ARGS",
            f"Fetch roads over bbox {needed.xMinimum():.4f},{needed.yMinimum():.4f},"
            f"{needed.xMaximum():.4f},{needed.yMaximum():.4f} (EPSG:4326), then call get_isochrone again.")
    network = roads
    if window is not None and not window.contains(roads.extent()):
        try:
            network = processing.run("native:extractbyextent", {"INPUT": roads, "EXTENT": window, "CLIP": False,
                                                                "OUTPUT": "memory:"})["OUTPUT"]
        except Exception:  # nosec B110 - the whole layer still works, only slower
            network = roads






    from qgis.analysis import QgsGraphAnalyzer, QgsGraphBuilder, QgsNetworkDistanceStrategy, QgsVectorLayerDirector

    metric = QgsCoordinateReferenceSystem(_utm_authid(float(points[0]["lon"]), float(points[0]["lat"])))
    to_metric = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), metric, QgsProject.instance())
    origins = [to_metric.transform(QgsPointXY(float(p["lon"]), float(p["lat"]))) for p in points]
    try:
        director = QgsVectorLayerDirector(network, -1, "", "", "", QgsVectorLayerDirector.Direction.DirectionBoth)
        director.addStrategy(QgsNetworkDistanceStrategy())
        builder = QgsGraphBuilder(metric, True, 0.0)
        tied = director.makeGraph(builder, origins)
        graph = builder.graph()
    except Exception as exc:
        return tool_error(f"Building the road graph failed: {exc}", "EXECUTION_FAILED",
                          "The roads must be one line layer that the start points lie within a few hundred metres "
                          "of; fetch_overture theme roads over a box a little larger than the reach.")
    if graph.vertexCount() == 0:
        return tool_error("The road layer built an empty graph.", "EXECUTION_FAILED",
                          "Check that the layer holds lines near the start points (get_layer_info extent).")
    areas = QgsVectorLayer(
        f"Polygon?crs={metric.authid()}&field=start:integer&field=minutes:double&field=mode:string"
        "&field=reached_vertices:integer", args.get("output_name") or f"{minutes:g} min {mode} area", "memory")
    polygons = []
    vertices_total = 0
    starts_reached = 0
    for index, origin in enumerate(origins):
        start_vertex = graph.findVertex(tied[index])
        if start_vertex < 0 or origin.distance(tied[index]) > _ISOCHRONE_SNAP_M:
            continue
        _tree, cost = QgsGraphAnalyzer.dijkstra(graph, start_vertex, 0)
        reached_any = False

        for ring in reversed(rings):
            ring_m = speed * 1000.0 * ring / 60.0
            reached = [graph.vertex(i).point() for i, c in enumerate(cost) if 0 <= c <= ring_m]
            if len(reached) < 3:
                continue
            hull = _reach_hull(QgsGeometry.fromMultiPointXY(reached)).buffer(_ISOCHRONE_FRINGE_M, 5)
            if hull.isNull() or hull.isEmpty():
                continue
            polygon = QgsFeature(areas.fields())
            polygon.setAttributes([index + 1, ring, mode, len(reached)])
            polygon.setGeometry(hull)
            polygons.append(polygon)
            vertices_total += len(reached)
            reached_any = True
        starts_reached += int(reached_any)
    if not polygons:
        return tool_error("No road is reachable from the start points within the travel time.", "EXECUTION_FAILED",
                          "The starts are probably off the network: compare them with the road layer's extent, "
                          "or fetch roads over a larger box.")
    areas.dataProvider().addFeatures(polygons)
    areas.updateExtents()
    QgsProject.instance().addMapLayer(areas)
    return {
        "layer_name": areas.name(),
        "layer_id": areas.id(),
        "crs": metric.authid(),
        "polygons": len(polygons),
        "starts": len(points),
        "starts_unreached": max(0, len(points) - starts_reached),
        "starts_unreached_why": ("farther than 500 m from any road, or fewer than 3 vertices reached"
                                 if starts_reached < len(points) else ""),
        "minutes": rings if len(rings) > 1 else minutes,
        "rings": len(rings),
        "mode": mode,
        "speed_kmh": speed,
        "reach_m": round(reach_m),
        "road_layer": roads.name(),
        "roads_used": network.featureCount(),
        "reached_vertices": vertices_total,
        "method": (f"roads reachable within {', '.join(f'{r:g}' for r in rings)} min at {speed:g} km/h along "
                   f"{roads.name()}; the reached vertices hulled (concave) and buffered {_ISOCHRONE_FRINGE_M:g} m; "
                   "one polygon per start point and per travel time (field minutes), overlapping where they meet"),
    }


def _reach_hull(cloud):
    """The outline of a reached-vertex cloud: a concave hull where GEOS has one (QGIS 3.28+), the convex hull before that."""


    hull = None
    if hasattr(cloud, "concaveHull"):
        try:
            hull = cloud.concaveHull(0.3, False)
        except Exception:  # nosec B110 - an older GEOS falls back to the convex hull
            hull = None
    if hull is None or hull.isNull() or hull.isEmpty():
        hull = cloud.convexHull()
    return hull


def _profile_geometry(args: dict, dem):
    if args.get("line_wkt"):
        geometry = QgsGeometry.fromWkt(args["line_wkt"])
        if geometry.isNull():
            return None, tool_error("line_wkt is not a valid LINESTRING.", "INVALID_ARGS",
                                    "Pass WKT such as LINESTRING(x1 y1, x2 y2).")

        source = QgsCoordinateReferenceSystem(str(args.get("wkt_crs") or "EPSG:4326"))
        if source.isValid() and source != dem.crs():
            geometry.transform(QgsCoordinateTransform(source, dem.crs(), QgsProject.instance()))
        return geometry, None
    if args.get("line_layer"):
        layer = _find_layer(args["line_layer"])
        if layer is None:
            return None, _layer_not_found_error(args["line_layer"])
        feature = next(layer.getFeatures(), None)
        geometry = feature.geometry() if feature is not None else None
        if geometry is not None and layer.crs() != dem.crs():
            transform = QgsCoordinateTransform(layer.crs(), dem.crs(), QgsProject.instance())
            geometry.transform(transform)
        return geometry, None
    return None, tool_error("Pass line_layer or line_wkt.", "INVALID_ARGS", "Use a line layer or LINESTRING WKT.")


def _elevation_profile(args: dict) -> dict:
    dem = _find_layer(args["dem"])
    if dem is None:
        return _layer_not_found_error(args["dem"])
    geometry, error = _profile_geometry(args, dem)
    if error:
        return error
    if geometry is None or geometry.isEmpty() or geometry.length() == 0:
        return {"_error": "The profile line is empty or has zero length."}
    count = max(2, min(int(args.get("sample_count", 100) or 100), 2000))
    provider = dem.dataProvider()
    total = geometry.length()
    series = []
    for index in range(count):
        distance = total * index / (count - 1)
        point = geometry.interpolate(distance).asPoint()
        identified = provider.identify(point, enum_member(QgsRaster, "IdentifyFormat", "IdentifyFormatValue"))
        values = identified.results() if identified.isValid() else {}
        value = values.get(1)
        series.append({"distance": round(distance, 3), "elevation": None if value is None else round(float(value), 3)})
    path = _write_profile_png(series)
    return {
        "dem": dem.name(),
        "samples": series,
        "sample_count": count,
        "png_path": path,
        "distance_units": dem.crs().mapUnits().name if hasattr(dem.crs().mapUnits(), "name") else "layer units",
    }


def _write_profile_png(series: list[dict]) -> str:
    from qgis.PyQt.QtCore import QPointF
    from qgis.PyQt.QtGui import QColor, QImage, QPainter, QPen

    path = os.path.join(create_managed_temp_dir("elevation-profile"), "profile.png")
    image = QImage(1000, 360, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setPen(QPen(QColor("#345a8a"), 2))
    values = [row["elevation"] for row in series if row["elevation"] is not None]
    if values:
        lo, hi = min(values), max(values)
        span = hi - lo or 1.0
        previous = None
        for index, row in enumerate(series):
            value = row["elevation"]
            if value is None:
                previous = None
                continue
            x = 40 + index * 920 / max(1, len(series) - 1)
            y = 320 - (value - lo) * 280 / span
            if previous is not None:
                painter.drawLine(QPointF(previous[0], previous[1]), QPointF(x, y))
            previous = (x, y)
    painter.setPen(QPen(QColor("#222"), 1))
    painter.drawLine(40, 320, 960, 320)
    painter.drawLine(40, 40, 40, 320)
    painter.end()
    image.save(path, "PNG")
    return path


def _check_topology(args: dict) -> dict:
    layer = _find_layer(args["layer"])
    if layer is None:
        return _layer_not_found_error(args["layer"])
    if not isinstance(layer, QgsVectorLayer):
        return {"_error": "Topology checks need a polygon vector layer."}
    polygon_type = enum_member(QgsWkbTypes, "GeometryType", "PolygonGeometry")
    if layer.geometryType() != polygon_type:
        drawn = {0: "points", 1: "lines"}.get(int(layer.geometryType()), "no geometry")
        return {"_error": f"{layer.name()!r} holds {drawn}, and overlaps and gaps only mean something between "
                "polygons.",
                "suggestion": "Pass a polygon layer, or run check_geometry_validity on this one."}


    count = layer.featureCount()
    if isinstance(count, int) and count > MAX_TOPOLOGY_FEATURES:
        return tool_error(
            f"{layer.name()!r} holds {count:,} polygons, past the {MAX_TOPOLOGY_FEATURES:,} this check "
            "compares in one call. Nothing was checked.",
            "INVALID_ARGS",
            "Extract the part that matters first (a filter, a clip to the area of interest, "
            "native:extractbyexpression) and run check_topology on the extract. Every polygon is "
            "intersected with its neighbours here, so a layer this size freezes QGIS for minutes.")
    limit = max(1, min(int(args.get("limit", 20) or 20), 100))


    budget = VertexBudget()
    features = []
    for feature in layer.getFeatures():
        if feature.geometry().isEmpty():
            continue
        too_big = budget.oversize(feature.geometry())
        if too_big or budget.exhausted():
            return tool_error(
                f"{layer.name()!r} is too detailed for this check on this machine: "
                + (f"one polygon of {too_big:,} vertices" if too_big
                   else f"more than {budget.total:,} vertices in total")
                + ". Nothing was checked.",
                "INVALID_ARGS",
                "Simplify it first (native:simplifygeometries through run_processing with async true), or "
                "run native:checkvalidity and native:dissolve in the background and read their outputs.")
        features.append(feature)
    by_id = {feature.id(): feature for feature in features}
    index = QgsSpatialIndex()
    for feature in features:
        index.addFeature(feature)
    overlaps, checked = [], set()
    for feature in features:
        for other_id in index.intersects(feature.geometry().boundingBox()):
            if other_id <= feature.id() or (feature.id(), other_id) in checked:
                continue
            checked.add((feature.id(), other_id))
            other = by_id.get(other_id)
            if other is None:
                continue
            if budget.exhausted():
                return tool_error(
                    f"check_topology stopped after {len(checked)} pairs to keep QGIS responsive "
                    f"({budget.stop_reason(budget.exhausted())} reached). Nothing is reported.",
                    "INVALID_ARGS",
                    "Run it on a smaller extract, or use native:checkvalidity and native:union through "
                    "run_processing with async true.")
            intersection = feature.geometry().intersection(other.geometry())
            if not intersection.isEmpty() and intersection.area() > 0:
                overlaps.append({"feature_ids": [feature.id(), other.id()], "wkt": intersection.asWkt(6)})
    union = QgsGeometry.unaryUnion([feature.geometry() for feature in features])
    gaps = _interior_holes(union, limit)
    return {
        "layer": layer.name(),
        "checked_features": len(features),
        "overlap_count": len(overlaps),
        "overlaps": overlaps[:limit],
        "gap_count": len(gaps),
        "gaps": gaps[:limit],
        "gap_note": "Gaps are enclosed holes in the combined polygon coverage.",
    }


def _interior_holes(geometry, limit: int) -> list[dict]:
    """The enclosed holes of a union, whatever shape the union came back as."""







    polygon_type = enum_member(QgsWkbTypes, "GeometryType", "PolygonGeometry")
    gaps = []
    for part in geometry.asGeometryCollection() or [geometry]:
        if part.isEmpty() or part.type() != polygon_type:
            continue
        try:
            rings = part.asPolygon()
        except (TypeError, ValueError):
            continue
        for ring in rings[1:]:
            hole = QgsGeometry.fromPolygonXY([ring])
            gaps.append({"wkt": hole.asWkt(6), "area": round(hole.area(), 6)})
            if len(gaps) >= limit:
                return gaps
    return gaps


class _GeocodeLayerTask(QgsTask):
    def __init__(self, task_id: str, addresses: list[str]):
        super().__init__("Geocode layer", enum_member(QgsTask, "Flag", "CanCancel"))
        self.task_id = task_id
        self.addresses = addresses
        self.results: list[dict] = []
        self.skipped = 0
        self.error = None

    def run(self):












        from .data_tools import _BACKEND_GEOCODE_BATCH_MAX, _geocode_one_address

        own_host, through_backend = True, 0
        for number, address in enumerate(self.addresses, 1):
            if self.isCanceled():
                return False
            if not own_host and through_backend >= _BACKEND_GEOCODE_BATCH_MAX:



                self.skipped += 1
                reason = (
                    "not geocoded: the geocoding service was unreachable and the fallback is "
                    f"limited to {_BACKEND_GEOCODE_BATCH_MAX} addresses per run"
                )
                self.results.append({"address": address, "error": reason})
                continue
            try:
                hit, own_failed = _geocode_one_address(address, own_host)
                if own_failed:
                    own_host = False
                if not own_host:
                    through_backend += 1
                if hit:
                    self.results.append({"address": address, "lat": hit["lat"], "lon": hit["lon"],
                                         "display_name": hit.get("display_name")})
                else:
                    self.results.append({"address": address, "error": "not found"})
            except Exception as exc:
                self.results.append({"address": address, "error": str(exc)})
            self.setProgress(number * 100 / max(1, len(self.addresses)))
        return True

    def finished(self, result):
        _GEOCODE_LAYER_ALIVE.pop(self.task_id, None)
        state = _GEOCODE_LAYER_TASKS.get(self.task_id)
        if state is None:
            return
        if not result:
            state.update({"status": "canceled" if self.isCanceled() else "error", "results": self.results})
            return
        try:
            self._publish(state)
        except Exception as exc:  # noqa: BLE001 - a poll must never wait on a raised finished()
            state.update({"status": "error", "error": str(exc), "results": self.results})

    def _publish(self, state: dict) -> None:
        layer = QgsVectorLayer("Point?crs=EPSG:4326", state["output_name"], "memory")
        provider = layer.dataProvider()
        provider.addAttributes(
            [
                QgsField("address", field_type("String")),
                QgsField("latitude", field_type("Double")),
                QgsField("longitude", field_type("Double")),
            ]
        )
        layer.updateFields()
        rows = []
        for item in self.results:
            if "lat" not in item:
                continue
            feature = QgsFeature(layer.fields())
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(item["lon"], item["lat"])))
            feature.setAttributes([item["address"], item["lat"], item["lon"]])
            rows.append(feature)
        provider.addFeatures(rows)
        QgsProject.instance().addMapLayer(layer)
        state.update({"status": "complete", "layer_name": layer.name(), "matched": len(rows), "results": self.results})
        if self.skipped:



            state["skipped"] = self.skipped
            state["note"] = (f"{len(rows)} of {len(self.results)} addresses were geocoded: the geocoding "
                             f"service was unreachable and the fallback carried {self.skipped} fewer rows. "
                             "Call geocode_layer again on the rows with no coordinates in a few minutes.")


def _geocode_layer(args: dict) -> dict:
    layer = _find_layer(args["layer"])
    if layer is None:
        return _layer_not_found_error(args["layer"])
    field = args["address_field"]
    index = layer.fields().indexOf(field)
    if index < 0:
        return tool_error(f"Field not found: {field}", "INVALID_ARGS", "Call get_layer_info to inspect fields.")



    request = QgsFeatureRequest().setSubsetOfAttributes([index])
    try:
        request.setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry"))
    except Exception:  # nosec B110 - a hint
        pass
    seen: dict = {}
    for feature in layer.getFeatures(request):
        value = str(feature[index] or "").strip()
        if value:
            seen[value] = None
            if len(seen) > MAX_GEOCODE_ADDRESSES:
                break
    addresses = list(seen)
    if not addresses:
        return {"_error": "The address field has no non-empty values."}
    if len(addresses) > MAX_GEOCODE_ADDRESSES:

        return tool_error(
            f"{len(addresses)} distinct addresses is past the {MAX_GEOCODE_ADDRESSES} this tool geocodes "
            "in one call.",
            "INVALID_ARGS",
            "Geocode a filtered selection, or split the layer and call geocode_layer once per part.")
    task_id = "geocode-" + uuid.uuid4().hex[:12]
    _GEOCODE_LAYER_TASKS[task_id] = {
        "status": "running",
        "output_name": args.get("output_name") or "Geocoded addresses",
        "total": len(addresses),
        "matched": 0,
    }
    task = _GeocodeLayerTask(task_id, addresses)
    _GEOCODE_LAYER_ALIVE[task_id] = task
    if not QgsApplication.taskManager().addTask(task):
        _GEOCODE_LAYER_ALIVE.pop(task_id, None)
        _GEOCODE_LAYER_TASKS.pop(task_id, None)
        return tool_error("The task manager refused the geocoding task.", "QGIS_ERROR",
                          "Try again once the other background tasks have finished.")
    return {
        "status": "running",
        "task_id": task_id,
        "total": len(addresses),
        "rate_limit": "10 requests/s",
        "poll": {
            "tool": "get_geocode_layer_status",
            "args": {"task_id": task_id},
            "interval_s": 2,
            "timeout_s": max(600, len(addresses) // 4),
            "label": "Geocoding addresses",
        },
    }


def _get_geocode_layer_status(args: dict) -> dict:
    state = _GEOCODE_LAYER_TASKS.get(args["task_id"])
    if state is None:
        return {"_error": f"Geocode task not found: {args['task_id']}"}
    result = dict(state)
    if result["status"] == "running":
        result["progress"] = None
    return result
