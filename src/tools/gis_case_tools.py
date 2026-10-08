# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

import contextlib
import io
import math
import os
import sqlite3
import uuid
import zipfile
from urllib.parse import quote, unquote

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsExpression,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRaster,
    QgsRectangle,
    QgsSpatialIndex,
    QgsTask,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsWkbTypes,
)

from ..core import layer_order, limits, net
from ..core.background import heartbeat, run_on_main_thread
from ..core.context import source_kind
from ..core.feature_requests import feature_request, first_feature
from ..core.geometry_budget import VertexBudget
from ..core.host_platform import retry_file_op
from ..core.policy import create_managed_temp_dir
from ..core.qt_compat import enum_member, field_type
from ..core.security import fits_path, validate_path
from ..core.tool_registry import Tool, ToolRegistry, coded_fact, tool_error
from . import csv_loader
from .layer_lookup import _find_layer, _layer_not_found_error
from .processing_guards import _utm_authid






MAX_GEOCODE_ADDRESSES = 5000









MAX_TOPOLOGY_FEATURES = 20_000




TOPOLOGY_CHECKS = ("overlaps", "gaps", "dangles", "duplicates", "multipart", "validity")
_TOPOLOGY_DEFAULTS = {
    "polygon": ("overlaps", "gaps", "duplicates", "multipart", "validity"),
    "line": ("dangles", "duplicates", "multipart", "validity"),
    "point": ("duplicates", "multipart", "validity"),
}

_REPAIR_MAX_FOLDERS = 20_000
_REPAIR_MAX_DEPTH = 12
_GEOCODE_LAYER_TASKS: dict[str, dict] = {}




_GEOCODE_LAYER_ALIVE: dict[str, object] = {}


def register_gis_case_tools(registry: ToolRegistry):
    _enable_add_data_subdatasets()
    registry.register(
        Tool(
            name="repair_layer_paths",
            danger="write",
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
            danger="destructive",
            input_schema={
                "type": "object",
                "properties": {
                    "output_path": {"type": "string"},
                    "overwrite": {"type": "boolean"},

                    "format": {"type": "string", "enum": ["zip", "gpkg"]},
                },
                "required": ["output_path"],


                "x-zip-relinked": True,
            },
            handler=_package_project,
            background=True,
        )
    )
    registry.register(
        Tool(
            name="get_isochrone",
            danger="write",
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


                "x-road-classes": True,
            },
            handler=_get_isochrone,
        )
    )
    registry.register(
        Tool(
            name="elevation_profile",
            danger="read",
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
            danger="read",
            input_schema={
                "type": "object",
                "properties": {
                    "layer": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "checks": {"type": "array", "items": {"type": "string", "enum": list(TOPOLOGY_CHECKS)}},
                    "tolerance": {"type": "number", "minimum": 0},
                },
                "required": ["layer"],
            },
            handler=_check_topology,
        )
    )
    registry.register(
        Tool(
            name="geocode_layer",
            danger="write",
            input_schema={
                "type": "object",
                "properties": {
                    "layer": {"type": "string"},
                    "address_field": {"type": "string"},
                    "output_name": {"type": "string"},
                },
                "required": ["layer", "address_field"],





                "x-geocode-not-found-table": True,
            },
            handler=_geocode_layer,
        )
    )
    registry.register(
        Tool(
            name="get_geocode_layer_status",
            danger="read",
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






    source = layer.source() or ""
    try:
        from qgis.core import QgsProviderRegistry

        decoded = QgsProviderRegistry.instance().decodeUri(layer.providerType(), source)
        path = decoded.get("path") if isinstance(decoded, dict) else ""
    except Exception:  # noqa: BLE001
        path = ""
    return path or source.split("|", 1)[0]


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
        return tool_error("The layer has no local file name.", "INVALID_ARGS", "a local file-backed layer.")
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
                "_suggestion": ("a search_root closer to the file." if exhausted else
                                "The file may not exist, or search_root may help.")}
    if len(matches) > 1:
        return {
            "layer": layer.name(),
            "old_path": old_path,
            "candidates": matches,
            "_error": "More than one matching file exists; a narrower search_root would pick one.",
        }
    provider = layer.providerType()
    layer.setDataSource(matches[0] + source[len(old_path):], layer.name(), provider)
    if not layer.isValid():
        return {"_error": f"QGIS could not load {matches[0]} with provider {provider}."}
    return {"layer": layer.name(), "old_path": old_path, "new_path": matches[0], "repaired": True}


def _arcname(path: str, project_dir: str) -> str:










    try:
        common = os.path.commonpath([project_dir, path])
        if os.path.normcase(os.path.normpath(common)) == os.path.normcase(os.path.normpath(project_dir)):
            return os.path.relpath(path, project_dir).replace(os.sep, "/")
    except ValueError:
        pass
    return ""


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))





_STEM_SIDECARS = (".dbf", ".shx", ".prj", ".cpg", ".qix", ".sbn", ".sbx", ".csvt", ".tfw", ".tifw", ".tiffw",
                  ".wld", ".jgw", ".pgw", ".j2w", ".hdr", ".rrd", ".aux", ".qml", ".qmd")
_NAME_SIDECARS = (".aux.xml", ".ovr", ".msk", ".xml")


def _sidecars(path: str) -> list:
    stem = os.path.splitext(path)[0]
    found = []
    for candidate in [stem + suffix for suffix in _STEM_SIDECARS] + [path + suffix for suffix in _NAME_SIDECARS]:
        if _path_key(candidate) != _path_key(path) and os.path.isfile(candidate) and candidate not in found:
            found.append(candidate)
    return found


def _archive_places(project_path: str, project_dir: str, paths: list) -> list:







    places: list = []
    taken = {os.path.basename(project_path).lower()}
    placed: set = set()
    outside: dict = {}

    def add(path: str, arcname: str) -> None:
        places.append((path, arcname))
        taken.add(arcname.lower())
        placed.add(_path_key(path))

    for path in sorted(paths):
        group = [path] if path.lower().endswith(_SQLITE_FILES) else [path, *_sidecars(path)]
        if _arcname(path, project_dir):
            for member in group:
                if _path_key(member) not in placed:
                    add(member, _arcname(member, project_dir))
        else:
            folder = outside.setdefault(_path_key(os.path.dirname(path)), [])
            folder.extend(member for member in group if member not in folder)
    for files in outside.values():
        files = [member for member in files if _path_key(member) not in placed]
        prefix, number = "", 1
        while any(f"{prefix}{os.path.basename(member)}".lower() in taken for member in files):
            number += 1
            prefix = f"external-{number}/"
        for member in files:
            add(member, prefix + os.path.basename(member))
    return places


def _moved_source(layer, old_path: str, new_path: str):

    source = layer.source() or ""
    try:
        from qgis.core import QgsProviderRegistry

        registry = QgsProviderRegistry.instance()
        parts = registry.decodeUri(layer.providerType(), source)
        if isinstance(parts, dict) and parts.get("path"):
            parts["path"] = new_path
            encoded = registry.encodeUri(layer.providerType(), parts)
            if encoded:
                return encoded
    except Exception:  # noqa: BLE001
        parts = None
    if old_path and old_path in source:
        return source.replace(old_path, new_path, 1)
    return None


def _not_packed(layer, path: str) -> str:

    provider = layer.providerType()
    if provider == "memory":
        return "a temporary layer with no file: the unzipped project has it without features"
    if path and os.path.isdir(path):
        return f"a folder dataset ({os.path.basename(os.path.normpath(path))}); only files are packed"
    if path and os.path.isabs(path) and "://" not in path:
        return f"its file was not found at {path}"
    return f"not a local file ({provider}): it still reads from its source where the project opens"


def _package_project_snapshot(args: dict) -> dict:







    project = QgsProject.instance()
    project_path = project.fileName()
    if not project_path or not os.path.isfile(project_path):
        return tool_error(
            "Not saved; packaging needs a save.",
            "INVALID_ARGS",
            hint="package_project_not_saved",
        )
    output = os.path.abspath(os.path.expanduser(args["output_path"]))
    if not output.lower().endswith(".zip") and (os.path.isdir(output) or not os.path.splitext(output)[1]):

        stem = os.path.splitext(os.path.basename(project_path))[0] or "project"
        output = os.path.join(output, f"{stem}.zip")
    error = validate_path(output, write=True)
    if error:
        return {"_error": error}
    project_dir = os.path.dirname(project_path)




    local: dict = {}
    layer_files = []
    skipped = []
    for layer in project.mapLayers().values():
        path = _source_path(layer)
        if path and os.path.isfile(path):
            local.setdefault(_path_key(path), path)
            layer_files.append((layer, path))
        else:
            skipped.append({"layer": layer.name(), "reason": _not_packed(layer, path)})
    places = _archive_places(project_path, project_dir, list(local.values()))
    where = {_path_key(path): arcname for path, arcname in places}





    from qgis.core import QgsPathResolver, QgsReadWriteContext
    from qgis.PyQt.QtCore import QFileInfo

    base = QFileInfo(project_path).canonicalPath() or QFileInfo(project_path).absolutePath()
    context = QgsReadWriteContext()
    context.setPathResolver(QgsPathResolver(project_path))
    sources = {}
    for layer, path in layer_files:
        moved = _moved_source(layer, path, f"{base}/{where[_path_key(path)]}")
        if moved is None:
            skipped.append({"layer": layer.name(), "reason": "its file is in the archive, but the zipped project "
                                                             "still names its old place"})
            continue
        sources[layer.id()] = [layer.name(), layer.encodedSource(moved, context)]
    return {
        "output": output,
        "project_path": project_path,
        "places": places,
        "sources": sources,
        "skipped": skipped,
    }






_SQLITE_FILES = (".gpkg", ".sqlite")


def _sqlite_copy(path: str, beside: str) -> str:


    try:
        source = sqlite3.connect(path)
        try:
            target = sqlite3.connect(beside)
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
        return beside
    except sqlite3.Error:
        with contextlib.suppress(OSError):
            os.unlink(beside)
        return path


def _relinked_qgs(data: bytes, sources: dict) -> tuple:

    from qgis.PyQt.QtCore import QByteArray
    from qgis.PyQt.QtXml import QDomDocument

    doc = QDomDocument()
    doc.setContent(QByteArray(data))
    if doc.documentElement().isNull():
        return data, set()
    found = set()
    layers = doc.elementsByTagName("maplayer")
    for index in range(layers.count()):
        element = layers.at(index).toElement()
        entry = sources.get(element.firstChildElement("id").text())
        datasource = element.firstChildElement("datasource")
        if entry is None or datasource.isNull():
            continue
        while datasource.hasChildNodes():
            datasource.removeChild(datasource.firstChild())
        datasource.appendChild(doc.createTextNode(entry[1]))
        found.add(element.firstChildElement("id").text())
    nodes = doc.elementsByTagName("layer-tree-layer")
    for index in range(nodes.count()):
        element = nodes.at(index).toElement()
        entry = sources.get(element.attribute("id"))
        if entry is not None and element.hasAttribute("source"):
            element.setAttribute("source", entry[1])

    absolute = _property(_property(doc.documentElement().firstChildElement("properties"), "Paths"), "Absolute")
    if not absolute.isNull():
        while absolute.hasChildNodes():
            absolute.removeChild(absolute.firstChild())
        absolute.appendChild(doc.createTextNode("false"))
    return bytes(doc.toByteArray(2)), found


def _property(element, name: str):

    child = element.firstChildElement()
    while not child.isNull():
        if child.tagName() == name or (child.tagName() == "properties" and child.attribute("name") == name):
            return child
        child = child.nextSiblingElement()
    return child


def _project_copy(project_path: str, sources: dict) -> tuple:




    if not project_path.lower().endswith(".qgz"):
        with open(project_path, "rb") as handle:
            return _relinked_qgs(handle.read(), sources)
    found: set = set()
    buffer = io.BytesIO()
    with zipfile.ZipFile(project_path) as source, zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename.lower().endswith(".qgs"):
                data, found = _relinked_qgs(data, sources)
            target.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
    return buffer.getvalue(), found


def _package_project(args: dict) -> dict:

    wanted = str(args.get("format") or "").lower()
    if wanted == "gpkg" or (not wanted and str(args.get("output_path") or "").lower().endswith(".gpkg")):
        from .project_gpkg import package

        return package(args)
    try:
        snapshot = run_on_main_thread(_package_project_snapshot, args, timeout=20)
    except InterruptedError:
        return tool_error(
            "Project packaging was cancelled.",
            "CANCELLED",
            hint="package_cancelled",
        )
    if "_error" in snapshot:
        return snapshot
    output = snapshot["output"]
    project_path = snapshot["project_path"]
    sources = snapshot["sources"]
    skipped = snapshot["skipped"]
    folder = os.path.dirname(output) or "."
    stage = os.path.join(folder, f".{os.path.basename(output)}.{uuid.uuid4().hex}.part")
    if not fits_path(stage):




        stage = os.path.join(folder, f".package-{uuid.uuid4().hex[:12]}.part")
    if not fits_path(stage):
        return tool_error(
            f"The path is too long for this system to write beside: {output}",
            "INVALID_ARGS",
            hint="package_path_too_long", path=output,
        )
    os.makedirs(folder, exist_ok=True)
    cancelled = net.current_cancel_check()
    completed = False

    def write_file(archive, path: str, arcname: str) -> None:
        info = zipfile.ZipInfo.from_file(path, arcname)
        info.compress_type = zipfile.ZIP_DEFLATED
        with archive.open(info, "w", force_zip64=True) as target, open(path, "rb") as source:
            while True:
                if cancelled is not None and cancelled():
                    raise InterruptedError("Project packaging was cancelled")
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                target.write(chunk)
                heartbeat()

    try:
        with zipfile.ZipFile(stage, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            project_bytes, found = _project_copy(project_path, sources)
            info = zipfile.ZipInfo.from_file(project_path, os.path.basename(project_path))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, project_bytes)
            for path, arcname in snapshot["places"]:
                if path.lower().endswith(_SQLITE_FILES):
                    copy = _sqlite_copy(path, stage + ".sqlite")
                    try:
                        write_file(archive, copy, arcname)
                    finally:
                        if copy != path:
                            os.unlink(copy)
                    continue
                write_file(archive, path, arcname)



        retry_file_op(os.replace, stage, output)
        completed = True
    except InterruptedError:
        return tool_error(
            "Project packaging was cancelled.",
            "CANCELLED",
            hint="package_cancelled",
        )
    finally:
        if not completed:
            try:
                os.unlink(stage)
            except FileNotFoundError:
                pass
    for layer_id, (name, _source) in sources.items():
        if layer_id not in found:
            skipped.append({"layer": name, "reason": "its file is in the archive, but the layer is not in the saved "
                                                     "project file (added or changed since the last save)"})
    return {
        "package_path": output,
        "file_count": len(snapshot["places"]) + 1,
        "skipped_layers": skipped,
        "file_size": os.path.getsize(output),
        "size_units": "bytes",
    }





_ROAD_NAME_HINTS = ("road", "highway", "street", "route", "voirie", "rue", "walk", "network", "réseau")



_ISOCHRONE_FRINGE_M = 60.0
_ISOCHRONE_MAX_STARTS = 25
_ISOCHRONE_SNAP_M = 500.0
_ISOCHRONE_SPEEDS_KMH = {"driving": 50.0, "walking": 5.0, "cycling": 15.0}





_TRAVEL_CLASSES = {
    "driving": ("motorway", "trunk", "primary", "secondary", "tertiary", "residential", "living_street",
                "unclassified", "service"),
    "walking": ("primary", "secondary", "tertiary", "residential", "living_street", "unclassified", "service",
                "pedestrian", "footway", "steps", "path", "track"),
    "cycling": ("primary", "secondary", "tertiary", "residential", "living_street", "unclassified", "service",
                "cycleway", "path", "track"),
}
_LINKED_CLASSES = ("motorway", "trunk", "primary", "secondary", "tertiary")
_TRAVEL_VOCABULARY = ({name for names in _TRAVEL_CLASSES.values() for name in names}
                      | {f"{name}_link" for name in _LINKED_CLASSES} | {"road"})


def _is_line_layer(layer) -> bool:
    if not isinstance(layer, QgsVectorLayer):
        return False
    try:
        return QgsWkbTypes.geometryType(layer.wkbType()) == enum_member(QgsWkbTypes, "GeometryType", "LineGeometry")
    except AttributeError:
        from qgis.core import Qgis
        return layer.geometryType() == Qgis.GeometryType.Line


def _guess_road_layer():

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

    try:
        if window.isEmpty():
            return True
        inside = extent.intersect(window)
        return (inside.width() * inside.height()) >= _ISOCHRONE_COVER * (window.width() * window.height())
    except Exception:  # nosec B110
        return True


def _travel_filter(layer, mode: str) -> tuple:




    names = {field.name().lower(): field.name() for field in layer.fields()}
    classes = _TRAVEL_CLASSES[mode]
    if "class" in names:
        field, values = names["class"], list(classes)
    elif "highway" in names:
        field = names["highway"]
        values = list(classes) + [f"{name}_link" for name in _LINKED_CLASSES if name in classes] + ["road"]
    else:
        return "", "", ()


    try:
        present = {str(value) for value in layer.uniqueValues(layer.fields().indexOf(field), 1000)}
    except Exception:  # nosec B110
        present = set(values)
    if not present & _TRAVEL_VOCABULARY:
        return "", "", ()
    expression = (f"{QgsExpression.quotedColumnRef(field)} IN "
                  f"({', '.join(QgsExpression.quotedString(value) for value in values)})")
    if field == names.get("class") and "subtype" in names:
        expression = f"{QgsExpression.quotedColumnRef(names['subtype'])} = 'road' AND {expression}"
    return expression, field, tuple(values)






_ISOCHRONE_ISLAND_SHARE = 0.05


def _pieces(graph) -> list:

    parent = list(range(graph.vertexCount()))

    def root(vertex: int) -> int:
        while parent[vertex] != vertex:
            parent[vertex] = parent[parent[vertex]]
            vertex = parent[vertex]
        return vertex

    for index in range(graph.edgeCount()):
        edge = graph.edge(index)
        a, b = root(edge.fromVertex()), root(edge.toVertex())
        if a != b:
            parent[a] = b
    return [root(vertex) for vertex in range(len(parent))]


def _get_isochrone(args: dict) -> dict:








    import processing

    road_layer_name = args.get("road_layer")
    roads = _find_layer(road_layer_name) if road_layer_name else _guess_road_layer()
    if roads is None:
        if road_layer_name:
            return _layer_not_found_error(road_layer_name)
        return tool_error(
            "No road line layer is loaded for a service area.", "EXECUTION_FAILED",
            hint="isochrone_no_roads")
    if not _is_line_layer(roads):
        return tool_error(f"{roads.name()} is not a line layer.", "INVALID_ARGS",
                          hint="isochrone_not_line_layer", layer=roads.name())




    kind = source_kind(roads)
    if kind not in ("file", "memory"):
        return tool_error(
            f"{roads.name()} is a remote or database-backed road layer; the road graph needs a local layer.",
            "INVALID_ARGS",
            hint="isochrone_remote_roads", layer=roads.name(), source_kind=kind)


    asked = args["minutes"]
    rings = sorted({float(m) for m in (asked if isinstance(asked, list) else [asked])})
    minutes = rings[-1]
    mode = str(args["mode"])
    speed = _ISOCHRONE_SPEEDS_KMH[mode]
    reach_m = speed * 1000.0 * minutes / 60.0
    points = list(args["points"])
    if len(points) > _ISOCHRONE_MAX_STARTS:
        return tool_error(
            f"{len(points)} start points; get_isochrone takes {_ISOCHRONE_MAX_STARTS} per call.", "INVALID_ARGS",
            hint="isochrone_too_many_starts", points=len(points), max_starts=_ISOCHRONE_MAX_STARTS)
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
        except Exception:  # nosec B110
            window = None




    if window is not None and not _covers(roads.extent(), window):
        return tool_error(
            f"{roads.name()} does not reach {minutes:.0f} minutes from every start, so the polygon would be "
            "the shape of the download box.", "INVALID_ARGS",
            hint="isochrone_roads_short", crs="EPSG:4326",
            bbox=f"{needed.xMinimum():.4f},{needed.yMinimum():.4f},{needed.xMaximum():.4f},{needed.yMaximum():.4f}")
    network = roads
    if window is not None and not window.contains(roads.extent()):
        try:
            network = processing.run("native:extractbyextent", {"INPUT": roads, "EXTENT": window, "CLIP": False,
                                                                "OUTPUT": "memory:"})["OUTPUT"]
        except Exception:  # nosec B110
            network = roads


    travel, class_field, kept = _travel_filter(network, mode)
    lines_in_window = network.featureCount()
    if travel:
        try:
            network = processing.run("native:extractbyexpression", {"INPUT": network, "EXPRESSION": travel,
                                                                    "OUTPUT": "memory:"})["OUTPUT"]
        except Exception as exc:
            return tool_error(f"Keeping the {mode} road classes of {roads.name()} failed: {exc}",
                              "EXECUTION_FAILED", f"The layer's {class_field} field holds the road class.")
        if network.featureCount() == 0:
            return tool_error(
                f"{roads.name()} holds no {mode} road near the starts: none of its {lines_in_window:,} lines "
                f"there has {class_field} {', '.join(kept)}.", "INVALID_ARGS",
                hint="isochrone_no_mode_roads", layer=roads.name(), mode=mode)
    road_count = network.featureCount()
    max_roads = int(limits.current("ISOCHRONE_MAX_ROADS"))
    if road_count > max_roads:
        return tool_error(
            f"{roads.name()} has {road_count:,} road features, over the {max_roads:,} graph limit.",
            "INVALID_ARGS",
            hint="isochrone_graph_too_big", crs="EPSG:4326", road_count=road_count, max_roads=max_roads,
            window=f"{needed.xMinimum():.4f},{needed.yMinimum():.4f},{needed.xMaximum():.4f},{needed.yMaximum():.4f}")






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
                          hint="isochrone_graph_failed", layer=roads.name())
    if graph.vertexCount() == 0:
        return tool_error("The road layer built an empty graph.", "EXECUTION_FAILED",
                          hint="isochrone_empty_graph", layer=roads.name())
    areas = QgsVectorLayer(
        f"Polygon?crs={metric.authid()}&field=start:integer&field=minutes:double&field=mode:string"
        "&field=reached_vertices:integer", args.get("output_name") or f"{minutes:g} min {mode} area", "memory")
    polygons = []
    vertices_total = 0
    starts_reached = 0
    starts_moved = []
    labels = _pieces(graph)
    sizes: dict = {}
    for label in labels:
        sizes[label] = sizes.get(label, 0) + 1
    largest = max(sizes, key=sizes.get)
    for index, origin in enumerate(origins):
        start_vertex = graph.findVertex(tied[index])
        if start_vertex < 0 or origin.distance(tied[index]) > _ISOCHRONE_SNAP_M:
            continue
        if sizes[labels[start_vertex]] < _ISOCHRONE_ISLAND_SHARE * sizes[largest]:
            near = [(origin.distance(graph.vertex(v).point()), v) for v in range(len(labels)) if labels[v] == largest]
            gap, vertex = min(near)
            if gap <= _ISOCHRONE_SNAP_M:
                start_vertex = vertex
                starts_moved.append({"start": index + 1, "to_m": round(gap)})
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
                          hint="isochrone_unreachable", layer=roads.name())
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
        **({"starts_moved": starts_moved, "starts_moved_why": (
            "the nearest road was a piece of the network not linked to the rest for this mode (a ramp, a "
            "service road), so the start was tied to the nearest point of the main network, to_m metres away")}
           if starts_moved else {}),
        "minutes": rings if len(rings) > 1 else minutes,
        "rings": len(rings),
        "mode": mode,
        "speed_kmh": speed,
        "reach_m": round(reach_m),
        "road_layer": roads.name(),
        "roads_used": road_count,
        "classes_kept": (f"{class_field} {', '.join(kept)}; {lines_in_window - road_count:,} other lines "
                         f"near the starts left out" if travel
                         else "every line: the layer has no class or highway field"),
        "reached_vertices": vertices_total,
        "method": (f"roads reachable within {', '.join(f'{r:g}' for r in rings)} min at {speed:g} km/h along "
                   f"{roads.name()}; the reached vertices hulled (concave) and buffered {_ISOCHRONE_FRINGE_M:g} m; "
                   "one polygon per start point and per travel time (field minutes), overlapping where they meet"),
    }


def _reach_hull(cloud):



    hull = None
    if hasattr(cloud, "concaveHull"):
        try:
            hull = cloud.concaveHull(0.3, False)
        except Exception:  # nosec B110
            hull = None
    if hull is None or hull.isNull() or hull.isEmpty():
        hull = cloud.convexHull()
    return hull


def _profile_geometry(args: dict, dem):
    if args.get("line_wkt"):
        geometry = QgsGeometry.fromWkt(args["line_wkt"])
        if geometry.isNull():
            return None, tool_error("line_wkt is not a valid LINESTRING.", "INVALID_ARGS",
                                    "WKT such as LINESTRING(x1 y1, x2 y2).")

        source = QgsCoordinateReferenceSystem(str(args.get("wkt_crs") or "EPSG:4326"))
        if source.isValid() and source != dem.crs():
            geometry.transform(QgsCoordinateTransform(source, dem.crs(), QgsProject.instance()))
        return geometry, None
    if args.get("line_layer"):
        layer = _find_layer(args["line_layer"])
        if layer is None:
            return None, _layer_not_found_error(args["line_layer"])
        feature = first_feature(layer, feature_request(attributes=[], limit=1))
        geometry = feature.geometry() if feature is not None else None
        if geometry is not None and layer.crs() != dem.crs():
            transform = QgsCoordinateTransform(layer.crs(), dem.crs(), QgsProject.instance())
            geometry.transform(transform)
        return geometry, None
    return None, tool_error("line_layer or line_wkt.", "INVALID_ARGS", "a line layer or LINESTRING WKT.")


def _metric_sampling_crs(geometry, dem_crs):











    if not dem_crs.isValid():
        return dem_crs, None, None
    metres = enum_member(QgsUnitTypes, "DistanceUnit", "DistanceMeters")
    if not dem_crs.isGeographic() and dem_crs.mapUnits() == metres:
        return dem_crs, None, None
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    centre = geometry.centroid().asPoint()
    if dem_crs != wgs84:
        centre = QgsCoordinateTransform(dem_crs, wgs84, QgsProject.instance()).transform(centre)
    metric_crs = QgsCoordinateReferenceSystem(_utm_authid(centre.x(), centre.y()))
    to_metric = QgsCoordinateTransform(dem_crs, metric_crs, QgsProject.instance())
    to_dem = QgsCoordinateTransform(metric_crs, dem_crs, QgsProject.instance())
    return metric_crs, to_metric, to_dem


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
    dem_crs = dem.crs()
    _metric_crs, to_metric, to_dem = _metric_sampling_crs(geometry, dem_crs)
    if to_metric is not None:
        geometry = QgsGeometry(geometry)
        geometry.transform(to_metric)
    total = geometry.length()
    if total <= 0:
        return {"_error": "The profile line is empty or has zero length."}
    series = []
    for index in range(count):
        distance = total * index / (count - 1)
        point = geometry.interpolate(distance).asPoint()
        if to_dem is not None:
            point = to_dem.transform(point)
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
        "distance_units": "meters",
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


class _TopologyStop(Exception):


    def __init__(self, rule: str, why: str):
        super().__init__(rule)
        self.rule = rule
        self.why = why


def _tick(budget, rule: str) -> None:
    why = budget.exhausted()
    if why:
        raise _TopologyStop(rule, why)


def _topology_kind(layer) -> str | None:
    for kind, member in (("point", "PointGeometry"), ("line", "LineGeometry"), ("polygon", "PolygonGeometry")):
        if layer.geometryType() == enum_member(QgsWkbTypes, "GeometryType", member):
            return kind
    return None


def _topology_checks(requested, kind: str, name: str):

    applies = _TOPOLOGY_DEFAULTS[kind]
    if requested in (None, "", []):

        return list(applies), None
    if isinstance(requested, str):
        requested = [requested]
    if not isinstance(requested, (list, tuple)):
        return None, tool_error("checks must be a list of rule names. Nothing was checked.", "INVALID_ARGS",
                                f"Valid: {', '.join(TOPOLOGY_CHECKS)}; empty runs all.")
    unknown = [str(c) for c in requested if c not in TOPOLOGY_CHECKS]
    if unknown:
        return None, tool_error(
            f"Unknown check {', '.join(repr(c) for c in unknown)}. Nothing was checked.", "INVALID_ARGS",
            f"The rules are {', '.join(TOPOLOGY_CHECKS)}; for {kind}s: {', '.join(applies)}.")
    wrong = [c for c in requested if c not in applies]
    if wrong:
        return None, tool_error(
            f"{' and '.join(wrong)} mean nothing on {name!r}, which holds {kind}s. Nothing was checked.",
            "INVALID_ARGS",
            hint="topology_check_not_for_kind", wrong=list(wrong), kind=kind, applies=list(applies))
    return [c for c in TOPOLOGY_CHECKS if c in requested], None


def _check_topology(args: dict) -> dict:
    layer = _find_layer(args["layer"])
    if layer is None:
        return _layer_not_found_error(args["layer"])
    kind = _topology_kind(layer) if isinstance(layer, QgsVectorLayer) else None
    if kind is None:
        return tool_error(f"{layer.name()!r} has no point, line or polygon geometry, so there is no topology "
                          "to check.", "INVALID_ARGS",
                          "a vector layer with geometries; list_layers shows each layer's type.")
    checks, error = _topology_checks(args.get("checks"), kind, layer.name())
    if error:
        return error
    try:
        tolerance = float(args.get("tolerance") or 0)
    except (TypeError, ValueError):
        tolerance = -1.0
    if not math.isfinite(tolerance) or tolerance < 0:
        return tool_error(f"tolerance {args.get('tolerance')!r} is not a distance. Nothing was checked.",
                          "INVALID_ARGS", "0 or a positive distance in the layer's units.")


    count = layer.featureCount()
    if isinstance(count, int) and count > MAX_TOPOLOGY_FEATURES:
        return tool_error(
            f"{layer.name()!r} holds {count:,} {kind}s, past the {MAX_TOPOLOGY_FEATURES:,} this check "
            "compares in one call. Nothing was checked.",
            "INVALID_ARGS",
            hint="topology_too_many_features", count=count, cap=MAX_TOPOLOGY_FEATURES, kind=kind)
    limit = max(1, min(int(args.get("limit", 20) or 20), 100))


    budget = VertexBudget()
    features = []
    for feature in layer.getFeatures(feature_request(attributes=[])):
        if feature.geometry().isEmpty():
            continue
        too_big = budget.oversize(feature.geometry())
        if too_big or budget.exhausted():
            return tool_error(
                f"{layer.name()!r} is too detailed for this check on this machine: "
                + (f"one feature of {too_big:,} vertices" if too_big
                   else f"more than {budget.total:,} vertices in total")
                + ". Nothing was checked.",
                "INVALID_ARGS",
                hint="topology_too_detailed", vertices=too_big or budget.total)
        features.append(feature)
    out = {"layer": layer.name(), "checked_features": len(features)}
    try:
        index = None
        if "overlaps" in checks or "dangles" in checks:
            index = QgsSpatialIndex()
            for feature in features:
                _tick(budget, "indexing")
                index.addFeature(feature)
        floor = _sliver_area(tolerance, features) if ("overlaps" in checks or "gaps" in checks) else 0.0
        if "overlaps" in checks:
            overlaps = _topology_overlaps(features, index, budget)
            out["overlap_count"] = len(overlaps)
            out["overlaps"] = overlaps[:limit]
            _split_slivers(out, "overlaps", overlaps, len(overlaps), floor)
        if "gaps" in checks:
            union = QgsGeometry.unaryUnion(_valid_geometries(features, budget))
            out["gap_count"], out["gaps"] = _interior_holes(union, limit)
            out["gap_note"] = "Gaps are enclosed holes in the combined polygon coverage."
            _split_slivers(out, "gaps", out["gaps"], len(out["gaps"]), floor)
        if "dangles" in checks:
            out["dangle_count"], out["dangles"] = _topology_dangles(features, index, tolerance, limit, budget)
            out["tolerance"] = tolerance
            out["dangle_note"] = (
                "A dangle is a line end that touches no other line within the tolerance. Ends of the network "
                "(a cul-de-sac, a river source, the edge of the extract) are normal: they are candidates to "
                "review, not errors.")
        if "duplicates" in checks:
            groups = _topology_duplicates(features, budget)
            out["duplicate_count"] = len(groups)
            out["duplicates"] = [{"feature_ids": ids} for ids in groups[:limit]]
        if "multipart" in checks:
            multipart = []
            for feature in features:
                _tick(budget, "multipart")
                parts = _part_count(feature.geometry())
                if parts > 1:
                    multipart.append({"fid": feature.id(), "parts": parts})
            out["multipart_count"] = len(multipart)
            out["multipart"] = multipart[:limit]
        if "validity" in checks:
            out["validity"] = _topology_validity(features, limit, budget)
    except _TopologyStop as stop:
        return tool_error(
            f"check_topology stopped during {stop.rule} to keep QGIS responsive "
            f"({budget.stop_reason(stop.why)} reached). Nothing is reported.",
            "INVALID_ARGS",
            hint="topology_stopped", rule=stop.rule, reason=budget.stop_reason(stop.why))
    out["checks_run"] = checks
    return out


def _valid_geometries(features, budget) -> list:






    geometries = []
    for feature in features:
        _tick(budget, "gaps")
        geometry = feature.geometry()
        try:
            valid = geometry.isGeosValid()
        except Exception:  # noqa: BLE001
            valid = False
        geometries.append(geometry if valid else geometry.makeValid())
    return geometries


def _topology_overlaps(features, index, budget) -> list[dict]:
    by_id = {feature.id(): feature for feature in features}
    overlaps = []
    for feature in features:
        for other_id in index.intersects(feature.geometry().boundingBox()):
            if other_id <= feature.id():
                continue
            other = by_id.get(other_id)
            if other is None:
                continue
            _tick(budget, "overlaps")
            intersection = feature.geometry().intersection(other.geometry())
            area = intersection.area() if not intersection.isEmpty() else 0.0
            if area > 0:
                overlaps.append({"feature_ids": [feature.id(), other.id()], "area": round(area, 6),
                                 "wkt": intersection.asWkt(6)})
    return overlaps


def _sliver_area(tolerance: float, features) -> float:







    areas = sorted(feature.geometry().area() for feature in features)
    median = areas[len(areas) // 2] if areas else 0.0
    return max(tolerance * tolerance, median * 1e-6)


def _split_slivers(out: dict, key: str, items: list, count: int, floor: float) -> None:

    slivers = sum(1 for item in items if float(item.get("area") or 0.0) <= floor)
    if not slivers:
        return
    out[f"{key}_slivers"] = slivers
    out[f"{key}_sliver_note"] = (
        f"{slivers} of the {count} {key} measured have an area of {floor:.6g} square layer units or less: "
        "slivers where two edges were digitised apart or touch, not real " + key + "; "
        "native:snapgeometries with a small tolerance removes them.")


def _topology_duplicates(features, budget) -> list[list[int]]:








    buckets: dict[tuple, list] = {}
    for feature in features:
        _tick(budget, "duplicates")
        geometry = feature.geometry()
        box = geometry.boundingBox()
        clusters = buckets.setdefault((box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()), [])
        for first, ids in clusters:
            _tick(budget, "duplicates")
            if first.isGeosEqual(geometry):
                ids.append(feature.id())
                break
        else:
            clusters.append((geometry, [feature.id()]))
    return [ids for clusters in buckets.values() for _first, ids in clusters if len(ids) > 1]


def _part_count(geometry) -> int:
    inner = geometry.constGet()
    try:
        return int(inner.partCount()) if inner is not None else 0
    except Exception:  # noqa: BLE001
        return 1


def _curve_parts(geometry) -> list:

    inner = geometry.constGet()
    if inner is None:
        return []
    if QgsWkbTypes.isMultiType(inner.wkbType()):
        return [inner.geometryN(i) for i in range(inner.numGeometries())]
    return [inner]


def _topology_dangles(features, index, tolerance: float, limit: int, budget) -> tuple[int, list[dict]]:








    ends: dict[tuple[float, float], set] = {}
    open_ends = []
    parts_of: dict[int, list] = {}
    for feature in features:
        _tick(budget, "dangles")
        parts = [part for part in _curve_parts(feature.geometry()) if part is not None and not part.isEmpty()]
        if len(parts) > 1:
            parts_of[feature.id()] = [QgsGeometry(part.clone()) for part in parts]
        for position, part in enumerate(parts):
            start, end = part.startPoint(), part.endPoint()
            if part.isClosed() or (start.distance(end) <= tolerance and part.length() > 2 * tolerance):
                continue
            for point in (start, end):
                key = (point.x(), point.y())
                ends.setdefault(key, set()).add((feature.id(), position))
                open_ends.append((feature.id(), position, key))
    geometry_of = {feature.id(): feature.geometry() for feature in features}
    dangles = []
    count = 0
    for fid, position, key in open_ends:
        _tick(budget, "dangles")
        if any(owner != (fid, position) for owner in ends[key]):
            continue
        x, y = key
        reach = tolerance if tolerance > 0 else 1e-10 * max(1.0, abs(x), abs(y))
        point = QgsGeometry.fromPointXY(QgsPointXY(x, y))
        near = [part for i, part in enumerate(parts_of.get(fid, ())) if i != position]
        for other_id in index.intersects(QgsRectangle(x - reach, y - reach, x + reach, y + reach)):
            if other_id != fid and other_id in geometry_of:
                near.append(geometry_of[other_id])
        connected = False
        for geometry in near:
            _tick(budget, "dangles")
            distance = geometry.distance(point)
            if 0 <= distance <= reach:
                connected = True
                break
        if connected:
            continue
        count += 1
        if len(dangles) < limit:
            dangles.append({"fid": fid, "x": round(x, 6), "y": round(y, 6)})
    return count, dangles


def _topology_validity(features, limit: int, budget) -> dict:





    from .query_tools import _invalid_reasons

    invalid = []
    count = 0
    for feature in features:
        _tick(budget, "validity")
        geometry = feature.geometry()
        try:
            if geometry.isGeosValid():
                continue
        except Exception:  # noqa: BLE001  # nosec B110
            pass
        count += 1
        if len(invalid) < limit:
            reasons = _invalid_reasons(geometry)
            invalid.append({"fid": feature.id(), "reason": "; ".join(reasons) or "invalid geometry"})
    return {
        "invalid_count": count,
        "invalid": invalid,
        "note_hint": coded_fact(hint="topology_validity_note")["hint"],
    }


def _interior_holes(geometry, limit: int) -> tuple[int, list[dict]]:










    polygon_type = enum_member(QgsWkbTypes, "GeometryType", "PolygonGeometry")
    gaps = []
    count = 0
    for part in geometry.asGeometryCollection() or [geometry]:
        if part.isEmpty() or part.type() != polygon_type:
            continue
        try:
            rings = part.asPolygon()
        except (TypeError, ValueError):
            continue
        count += max(0, len(rings) - 1)
        for ring in rings[1:limit - len(gaps) + 1]:
            hole = QgsGeometry.fromPolygonXY([ring])
            gaps.append({"wkt": hole.asWkt(6), "area": round(hole.area(), 6)})
    return count, gaps


def _garbled_addresses(values) -> dict | None:











    replacement, mojibake = [], []
    for value in values:
        if "�" in value:
            replacement.append(value)
            continue
        try:
            back = value.encode("cp1252").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue
        if back != value:
            mojibake.append(value)
    if replacement:
        return {"kind": "replacement", "count": len(replacement), "samples": replacement[:3]}
    if mojibake:
        return {"kind": "mojibake", "count": len(mojibake), "samples": mojibake[:3]}
    return None


def _read_geocode_rows(layer, index: int):








    request = QgsFeatureRequest()
    try:
        request.setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry"))
    except Exception:  # nosec B110
        pass
    seen: dict = {}
    rows_by_address: dict = {}
    over_cap = False
    for feature in layer.getFeatures(request):
        value = str(feature[index] or "").strip()
        if not value:
            continue
        if value not in seen:
            seen[value] = None
            if len(seen) > MAX_GEOCODE_ADDRESSES:
                over_cap = True
                break
        rows_by_address.setdefault(value, []).append(feature.attributes())
    return seen, rows_by_address, over_cap


def _uri_query_value(uri: str, key: str) -> str | None:
    query = uri.partition("?")[2]
    for part in query.split("&"):
        name, _, value = part.partition("=")
        if name == key:
            return unquote(value)
    return None


def _with_encoding(uri: str, encoding: str) -> str:

    base, _, query = uri.partition("?")
    parts = [p for p in query.split("&") if p and not p.startswith("encoding=")]
    parts.append("encoding=" + quote(encoding))
    return base + "?" + "&".join(parts)


def _try_fix_encoding(layer, field_name: str, garbled: dict):












    provider_type = layer.providerType()
    candidate = "UTF-8" if garbled["kind"] == "mojibake" else csv_loader._ansi_encoding()

    if provider_type == "ogr":
        old = layer.dataProvider().encoding()
        if old == candidate:
            return None
        layer.setProviderEncoding(candidate)
        index = layer.fields().indexOf(field_name)
        if index < 0:
            layer.setProviderEncoding(old)
            return None
        seen, rows_by_address, over_cap = _read_geocode_rows(layer, index)
        if _garbled_addresses(list(seen)):
            layer.setProviderEncoding(old)
            return None
        return old, candidate, seen, rows_by_address, over_cap

    if provider_type == "delimitedtext":
        old_uri = layer.source()
        new_uri = _with_encoding(old_uri, candidate)
        if new_uri == old_uri:
            return None
        old_encoding = _uri_query_value(old_uri, "encoding") or "UTF-8"
        layer.setDataSource(new_uri, layer.name(), "delimitedtext")
        index = layer.fields().indexOf(field_name)
        if not layer.isValid() or index < 0:
            layer.setDataSource(old_uri, layer.name(), "delimitedtext")
            return None
        seen, rows_by_address, over_cap = _read_geocode_rows(layer, index)
        if _garbled_addresses(list(seen)):
            layer.setDataSource(old_uri, layer.name(), "delimitedtext")
            return None
        return old_encoding, candidate, seen, rows_by_address, over_cap

    return None


def _suffixed_field(existing_lower: set, base: str, qtype) -> QgsField:

    name = base
    number = 2
    while name.lower() in existing_lower:
        name = f"{base}_{number}"
        number += 1
    existing_lower.add(name.lower())
    return QgsField(name, qtype)


class _GeocodeLayerTask(QgsTask):
    def __init__(self, task_id: str, addresses: list[str], source_fields: QgsFields,
                 rows_by_address: dict, encoding_fixed: dict | None = None):
        super().__init__("Geocode layer", enum_member(QgsTask, "Flag", "CanCancel"))
        self.task_id = task_id
        self.addresses = addresses
        self.source_fields = source_fields
        self.rows_by_address = rows_by_address
        self.encoding_fixed = encoding_fixed
        self.results: list[dict] = []
        self.skipped = 0
        self.error = None
        self.run_token = layer_order.current_run()

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
            matched = sum(1 for r in self.results if "lat" in r)
            state.update({
                "status": "canceled" if self.isCanceled() else "error",
                "matched": matched,
                "not_found_count": len(self.results) - matched,
            })
            return
        try:
            with layer_order.adopted(self.run_token):
                self._publish(state)
        except Exception as exc:  # noqa: BLE001
            state.update({"status": "error", "error": str(exc)})

    def _publish(self, state: dict) -> None:
        output_name = state["output_name"]
        layer = QgsVectorLayer("Point?crs=EPSG:4326", output_name, "memory")
        provider = layer.dataProvider()
        provider.addAttributes(list(self.source_fields))
        existing_lower = {f.name().lower() for f in self.source_fields}
        provider.addAttributes([
            _suffixed_field(existing_lower, "latitude", field_type("Double")),
            _suffixed_field(existing_lower, "longitude", field_type("Double")),
            _suffixed_field(existing_lower, "geocode_label", field_type("String")),
        ])
        layer.updateFields()

        not_found_layer = QgsVectorLayer("None", f"{output_name} (not found)", "memory")
        nf_provider = not_found_layer.dataProvider()
        nf_provider.addAttributes(list(self.source_fields))
        nf_existing_lower = {f.name().lower() for f in self.source_fields}
        nf_provider.addAttributes([_suffixed_field(nf_existing_lower, "geocode_error", field_type("String"))])
        not_found_layer.updateFields()

        found_features, not_found_features, not_found_samples = [], [], []
        matched_addresses = 0
        found_fields = layer.fields()
        not_found_fields = not_found_layer.fields()
        for item in self.results:
            address = item["address"]
            rows = self.rows_by_address.get(address, [])
            if "lat" in item:
                matched_addresses += 1
                for attrs in rows:
                    feature = QgsFeature(found_fields)
                    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(item["lon"], item["lat"])))
                    feature.setAttributes(list(attrs) + [item["lat"], item["lon"], item.get("display_name")])
                    found_features.append(feature)
            else:
                error_text = item.get("error", "not found")
                if len(not_found_samples) < 20:
                    not_found_samples.append({"address": address, "error": error_text})
                for attrs in rows:
                    feature = QgsFeature(not_found_fields)
                    feature.setAttributes(list(attrs) + [error_text])
                    not_found_features.append(feature)

        provider.addFeatures(found_features)
        QgsProject.instance().addMapLayer(layer)
        state.update({"status": "complete", "layer_name": layer.name(), "matched": len(found_features)})
        if self.encoding_fixed:
            state["encoding_fixed"] = self.encoding_fixed

        if not_found_features:
            nf_provider.addFeatures(not_found_features)
            QgsProject.instance().addMapLayer(not_found_layer)
            state.update({
                "not_found_count": len(not_found_features),
                "not_found": not_found_samples,
                "not_found_layer": not_found_layer.name(),
            })

        if self.skipped:



            state["skipped"] = self.skipped
            state["note"] = (f"{matched_addresses} of {len(self.results)} addresses were geocoded: the geocoding "
                             f"service was unreachable and the fallback carried {self.skipped} fewer rows.")
            state["addresses_matched"] = matched_addresses
            state["addresses_total"] = len(self.results)
            state["note_hint"] = coded_fact(hint="geocode_partial")["hint"]


def _geocode_layer(args: dict) -> dict:
    layer = _find_layer(args["layer"])
    if layer is None:
        return _layer_not_found_error(args["layer"])
    field = args["address_field"]
    index = layer.fields().indexOf(field)
    if index < 0:
        return tool_error(f"Field not found: {field}", "INVALID_ARGS", hint="field_not_found", field=field,
                          layer=layer.name())

    seen, rows_by_address, over_cap = _read_geocode_rows(layer, index)
    encoding_fixed = None
    encoding_warning = None
    garbled = _garbled_addresses(list(seen))
    if garbled:
        fixed = _try_fix_encoding(layer, field, garbled)
        if fixed is not None:
            old, new, seen, rows_by_address, over_cap = fixed
            encoding_fixed = {"from": old, "to": new}
        else:












            total = len(seen)
            significant = total > 0 and garbled["count"] >= 3 and (garbled["count"] / total) >= 0.05
            fixable_provider = layer.providerType() in ("ogr", "delimitedtext")
            if significant and fixable_provider:
                kind = "replacement characters" if garbled["kind"] == "replacement" else "mojibake"
                samples = ", ".join(repr(s) for s in garbled["samples"])
                return tool_error(
                    f"{garbled['count']} of the values in {field} look garbled ({kind}), for example {samples}. "
                    f"{layer.name()} looks like it was read in the wrong encoding.",
                    "INVALID_ARGS",
                    hint="geocode_garbled_encoding", layer=layer.name(), field=field, count=garbled["count"],
                    kind=garbled["kind"])
            encoding_warning = {"kind": garbled["kind"], "count": garbled["count"], "samples": garbled["samples"]}

    addresses = list(seen)
    if not addresses:
        return {"_error": "The address field has no non-empty values."}
    if over_cap:

        return tool_error(
            f"{len(addresses)} distinct addresses is past the {MAX_GEOCODE_ADDRESSES} this tool geocodes "
            "in one call.",
            "INVALID_ARGS",
            hint="geocode_too_many_addresses", addresses=len(addresses), cap=MAX_GEOCODE_ADDRESSES)
    task_id = "geocode-" + uuid.uuid4().hex[:12]
    _GEOCODE_LAYER_TASKS[task_id] = {
        "status": "running",
        "output_name": args.get("output_name") or "Geocoded addresses",
        "total": len(addresses),
        "matched": 0,
    }
    if encoding_fixed:
        _GEOCODE_LAYER_TASKS[task_id]["encoding_fixed"] = encoding_fixed
    if encoding_warning:
        _GEOCODE_LAYER_TASKS[task_id]["encoding_warning"] = encoding_warning
    task = _GeocodeLayerTask(task_id, addresses, QgsFields(layer.fields()), rows_by_address, encoding_fixed)
    _GEOCODE_LAYER_ALIVE[task_id] = task
    if not QgsApplication.taskManager().addTask(task):
        _GEOCODE_LAYER_ALIVE.pop(task_id, None)
        _GEOCODE_LAYER_TASKS.pop(task_id, None)
        return tool_error("The task manager refused the geocoding task.", "QGIS_ERROR",
                          hint="task_manager_refused")
    running = {
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
    if encoding_fixed:
        running["encoding_fixed"] = encoding_fixed
    if encoding_warning:
        running["encoding_warning"] = encoding_warning
    return running


def _get_geocode_layer_status(args: dict) -> dict:
    state = _GEOCODE_LAYER_TASKS.get(args["task_id"])
    if state is None:
        return {"_error": f"Geocode task not found: {args['task_id']}"}
    result = dict(state)
    if result["status"] == "running":
        result["progress"] = None
    return result
