# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
from __future__ import annotations

import math

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsField,
    QgsLayerTreeGroup,
    QgsLayerTreeLayer,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsVectorFileWriter,
    QgsVectorLayer,
)
from qgis.utils import iface

from ..core import layer_order
from ..core.context import view_area_km2
from ..core.provider_uri import crs_problem  # noqa: E402
from ..core.qt_compat import enum_member, field_type
from ..core.security import validate_path
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .core_tools import _find_layer, _layer_not_found_error



_WEB_MERCATOR_MAX = 20037508.34


def register_layer_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="set_layers_visibility",
        input_schema={
            "type": "object",
            "properties": {
                "visible": {"type": "boolean"},
                "layers": {"type": "array", "items": {"type": "string"}, "maxItems": 5000},
                "pattern": {"type": "string"},
                "group": {"type": "string"},
            },
            "required": ["visible"],
        },
        handler=_set_layers_visibility,
    ))

    registry.register(Tool(
        name="set_active_layer",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
            },
            "required": ["layer_name"],
        },
        handler=_set_active_layer,
    ))

    registry.register(Tool(
        name="get_active_layer",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_get_active_layer,
    ))

    registry.register(Tool(
        name="set_layer_visibility",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "visible": {"type": "boolean"},
            },
            "required": ["layer_name", "visible"],
        },
        handler=_set_layer_visibility,
    ))

    registry.register(Tool(
        name="create_memory_layer",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "geometry_type": {
                    "type": "string",
                    "enum": ["Point", "LineString", "Polygon", "MultiPoint", "MultiLineString", "MultiPolygon"],
                },
                "crs": {"type": "string"},
                "fields": {
                    "type": "array",
                    "maxItems": 1000,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "type": {"type": "string", "enum": ["string", "integer", "double", "date"]},
                        },
                        "required": ["name", "type"],
                    },
                },
                "permanent": {
                    "type": "boolean",
                },
                "gpkg_path": {
                    "type": "string",
                },
                "overwrite": {"type": "boolean"},
            },
            "required": ["name", "geometry_type"],
        },
        handler=_create_memory_layer,
    ))

    registry.register(Tool(
        name="save_layer_to_gpkg",
        input_schema={
            "type": "object",
            "properties": {
                "layer": {"type": "string"},
                "gpkg_path": {
                    "type": "string",
                },
                "keep_style": {
                    "type": "boolean",
                },
                "overwrite": {"type": "boolean"},
            },
            "required": ["layer"],
        },
        handler=_save_layer_to_gpkg,
    ))

    registry.register(Tool(
        name="get_layer_tree",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_get_layer_tree,
    ))

    registry.register(Tool(
        name="create_layer_group",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "parent_group": {"type": "string"},
            },
            "required": ["name"],
        },
        handler=_create_layer_group,
    ))

    registry.register(Tool(
        name="move_layer_to_group",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "group_name": {"type": "string"},
            },
            "required": ["layer_name", "group_name"],
        },
        handler=_move_layer_to_group,
    ))

    registry.register(Tool(
        name="transform_coordinates",
        input_schema={
            "type": "object",
            "properties": {
                "x": {
                    "type": "number",
                },
                "y": {
                    "type": "number",
                },
                "source_crs": {"type": "string"},
                "target_crs": {"type": "string"},
            },
            "required": ["x", "y", "source_crs", "target_crs"],
        },
        handler=_transform_coordinates,
    ))

    registry.register(Tool(
        name="get_canvas_extent",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_get_canvas_extent,
    ))

    registry.register(Tool(
        name="set_canvas_extent",
        input_schema={
            "type": "object",
            "properties": {
                "xmin": {"type": "number"},
                "ymin": {"type": "number"},
                "xmax": {"type": "number"},
                "ymax": {"type": "number"},
                "crs": {"type": "string"},
            },
            "required": ["xmin", "ymin", "xmax", "ymax"],
        },
        handler=_set_canvas_extent,
    ))

    registry.register(Tool(
        name="set_project_crs",
        input_schema={
            "type": "object",
            "properties": {
                "crs": {"type": "string"},
            },
            "required": ["crs"],
        },
        handler=_set_project_crs,
    ))

    registry.register(Tool(
        name="save_project",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "overwrite": {
                    "type": "boolean",
                },
            },
            "required": [],
        },
        handler=_save_project,
    ))

    registry.register(Tool(
        name="load_project",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
        handler=_load_project,
    ))

    registry.register(Tool(
        name="create_new_project",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "crs": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },
            "required": [],
        },
        handler=_create_new_project,
    ))


def _set_active_layer(args: dict) -> dict:
    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])
    iface.setActiveLayer(layer)
    return {"active_layer": layer.name()}


def _get_active_layer(args: dict) -> dict:
    layer = iface.activeLayer()
    if not layer:
        return {"active_layer": None}
    return {
        "active_layer": {
            "name": layer.name(),
            "type": "vector" if isinstance(layer, QgsVectorLayer) else "raster",
            "layer_id": layer.id(),
        }
    }


def _set_layers_visibility(args: dict) -> dict:
    visible = bool(args.get("visible"))
    root = QgsProject.instance().layerTreeRoot()
    targets: dict[str, object] = {}
    for name in args.get("layers") or []:
        layer = _find_layer(str(name))
        if layer is not None:
            targets[layer.id()] = layer
    pattern = str(args.get("pattern") or "").strip().lower()
    if pattern:
        for layer in QgsProject.instance().mapLayers().values():
            if pattern in layer.name().lower():
                targets[layer.id()] = layer
    group_name = str(args.get("group") or "").strip()
    if group_name:
        group = root.findGroup(group_name)
        if group is None:
            return _layer_not_found_error(group_name)
        for node in group.findLayers():
            if node.layer() is not None:
                targets[node.layer().id()] = node.layer()
    if not targets:
        return tool_error("No layer matched.", "INVALID_ARGS", "Pass layers, a pattern or a group that exists.")
    changed: list[str] = []
    for layer_id, layer in targets.items():
        node = root.findLayer(layer_id)
        if node is None or node.isVisible() == visible and node.itemVisibilityChecked() == visible:
            continue
        node.setItemVisibilityChecked(visible)
        if visible:
            parent = node.parent()
            while parent is not None and parent is not root:
                if isinstance(parent, QgsLayerTreeGroup) and not parent.itemVisibilityChecked():
                    parent.setItemVisibilityChecked(True)
                parent = parent.parent()
        changed.append(layer.name())
    return {"visible": visible, "matched": len(targets), "changed": changed,
            "unchanged": len(targets) - len(changed)}


def _set_layer_visibility(args: dict) -> dict:
    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])

    root = QgsProject.instance().layerTreeRoot()
    node = root.findLayer(layer.id())
    unhidden_groups: list[str] = []
    if node is not None:
        node.setItemVisibilityChecked(args["visible"])
        if args["visible"]:
            parent = node.parent()
            while parent is not None and parent is not root:
                if isinstance(parent, QgsLayerTreeGroup) and not parent.isVisible():
                    parent.setItemVisibilityChecked(True)
                    unhidden_groups.append(parent.name())
                parent = parent.parent()
    return {
        "layer": args["layer_name"],
        "visible": args["visible"],
        "effective_visible": node.isVisible() if node is not None else args["visible"],
        "unhidden_parent_groups": unhidden_groups,
    }


def _default_gpkg_path() -> str | None:
    """'<project_dir>/<project_stem>_data.gpkg', or None if the project is unsaved."""
    import os
    fname = QgsProject.instance().fileName()
    if not fname:
        return None
    directory = os.path.dirname(fname)
    stem = os.path.splitext(os.path.basename(fname))[0]
    return os.path.join(directory, f"{stem}_data.gpkg")


def _write_layer_to_gpkg(layer, gpkg_path: str, layer_name: str):
    """Write a vector layer to GeoPackage (creating or overwriting the sub-layer) and reopen it as an ogr-backed layer."""







    import os
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = layer_name
    options.fileEncoding = "UTF-8"
    if os.path.exists(gpkg_path):
        options.actionOnExistingFile = enum_member(
            QgsVectorFileWriter, "ActionOnExistingFile", "CreateOrOverwriteLayer")

    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, gpkg_path, QgsProject.instance().transformContext(), options
    )
    error_code = result[0]
    if error_code != enum_member(QgsVectorFileWriter, "WriterError", "NoError"):
        error_msg = result[1] if len(result) > 1 else str(error_code)
        return None, f"GeoPackage write failed: {error_msg}"

    new_layer = QgsVectorLayer(f"{gpkg_path}|layername={layer_name}", layer_name, "ogr")
    if not new_layer.isValid():
        return None, f"Wrote GeoPackage but could not reopen layer '{layer_name}' from {gpkg_path}"
    return new_layer, None


def _create_memory_layer(args: dict) -> dict:
    geom_type = args["geometry_type"]
    crs = args.get("crs", "EPSG:4326")
    name = str(args["name"]).strip()
    if not name:
        return {"_error": "Layer name cannot be empty.", "_code": "INVALID_ARGS"}
    fields_def = args.get("fields", [])
    permanent = args.get("permanent", False)

    problem = crs_problem(crs)
    if problem:
        return {"_error": problem, "_code": "INVALID_ARGS",
                "_suggestion": "Pass the CRS on its own, for example EPSG:4326."}
    uri = f"{geom_type}?crs={crs}"
    field_specs = []
    seen_fields = set()
    for field in fields_def:
        field_name = str(field["name"]).strip()
        folded = field_name.casefold()
        if not field_name:
            return {"_error": "Field names cannot be empty.", "_code": "INVALID_ARGS"}
        if folded in seen_fields:
            return {"_error": f"Duplicate field name: {field_name}", "_code": "INVALID_ARGS"}
        seen_fields.add(folded)
        type_map = {"string": "String", "integer": "LongLong", "double": "Double", "date": "Date"}
        field_specs.append(QgsField(field_name, field_type(type_map[field["type"]])))

    layer = QgsVectorLayer(uri, name, "memory")
    if not layer.isValid():
        return {"_error": "Failed to create memory layer"}
    if field_specs:
        provider = layer.dataProvider()
        if not provider.addAttributes(field_specs):
            return {"_error": "Failed to add fields to the memory layer"}
        layer.updateFields()

    if not permanent:
        QgsProject.instance().addMapLayer(layer)
        return {
            "name": layer.name(), "layer_id": layer.id(), "crs": crs,
            "geometry_type": geom_type, "storage": "memory",
        }

    gpkg_path = args.get("gpkg_path") or _default_gpkg_path()
    if not gpkg_path:
        return {
            "_error": "Project is unsaved, pass gpkg_path explicitly for a permanent layer, or save the project first."
        }

    path_error = validate_path(gpkg_path, write=True)
    if path_error:
        return {"_error": path_error}

    new_layer, error = _write_layer_to_gpkg(layer, gpkg_path, name)
    if error:
        return {"_error": error}

    QgsProject.instance().addMapLayer(new_layer)
    return {
        "name": new_layer.name(), "layer_id": new_layer.id(), "crs": crs,
        "geometry_type": geom_type, "storage": "gpkg", "gpkg_path": gpkg_path,
    }


def _save_layer_to_gpkg(args: dict) -> dict:
    layer = _find_layer(args["layer"])
    if not layer:
        return _layer_not_found_error(args["layer"])
    if not isinstance(layer, QgsVectorLayer):
        return {"_error": f"Layer '{args['layer']}' is not a vector layer"}

    keep_style = args.get("keep_style", True)
    name = layer.name()
    source = str(layer.source() or "")
    if "/vsicurl/" in source and any(ext in source.lower() for ext in (".fgb", ".parquet", ".gpkg")):




        return tool_error(
            f"'{name}' streams a remote file in place; saving it copies the whole tile, not the area on the map, "
            "and holds QGIS for minutes.",
            "INVALID_ARGS",
            'Fetch a local copy of the area first, with the same theme and bbox and mode "clip" (fetch_overture) '
            "or a bbox on add_data, then save that layer.")
    gpkg_path = args.get("gpkg_path") or _default_gpkg_path()
    if not gpkg_path:
        return {"_error": "Project is unsaved, pass gpkg_path explicitly, or save the project first."}

    path_error = validate_path(gpkg_path, write=True)
    if path_error:
        return {"_error": path_error}

    new_layer, error = _write_layer_to_gpkg(layer, gpkg_path, name)
    if error:
        return {"_error": error}



    style_ok = False
    style_error = ""
    if keep_style:
        try:
            new_layer.setRenderer(layer.renderer().clone())
            style_ok = True
        except Exception as exc:  # noqa: BLE001 - the data is saved either way
            style_error = str(exc)



    project = QgsProject.instance()
    root = project.layerTreeRoot()
    old_node = root.findLayer(layer.id())
    parent = old_node.parent() if old_node else root
    index = parent.children().index(old_node) if old_node else -1


    was_checked = old_node.itemVisibilityChecked() if old_node else True
    try:
        was_active = iface.activeLayer() is layer
    except Exception:  # noqa: BLE001 - no interface in a headless run
        was_active = False



    layer_order.keep_place(new_layer)
    project.addMapLayer(new_layer, False)
    if index >= 0:
        parent.insertLayer(index, new_layer)
    else:
        root.addLayer(new_layer)
    new_node = root.findLayer(new_layer.id())
    if new_node is not None:
        new_node.setItemVisibilityChecked(was_checked)

    old_id = layer.id()
    project.removeMapLayer(old_id)
    if was_active:
        try:
            iface.setActiveLayer(new_layer)
        except Exception:  # nosec B110 - the layer is replaced either way
            pass

    out = {
        "name": new_layer.name(),
        "layer_id": new_layer.id(),
        "replaced_layer_id": old_id,
        "storage": "gpkg",
        "gpkg_path": gpkg_path,
        "kept_style": style_ok,
    }
    if keep_style and not style_ok:
        out["style_warning"] = (f"The data was saved but the style could not be copied: "
                                f"{style_error or 'the layer has no renderer to clone'}. "
                                f"Restyle the saved layer if it matters.")
    return out


def _get_layer_tree(args: dict) -> dict:
    root = QgsProject.instance().layerTreeRoot()

    def _tree_node(node):
        if isinstance(node, QgsLayerTreeGroup):
            children = [_tree_node(child) for child in node.children()]
            return {"type": "group", "name": node.name(), "visible": node.isVisible(), "children": children}
        if isinstance(node, QgsLayerTreeLayer):
            layer = node.layer()
            return {
                "type": "layer",
                "name": layer.name() if layer else "(invalid)",
                "visible": node.isVisible(),
                "layer_type": "vector" if isinstance(layer, QgsVectorLayer) else "raster" if layer else "unknown",
            }
        return {"type": "unknown"}

    children = [_tree_node(child) for child in root.children()]
    return {"tree": children}


def _create_layer_group(args: dict) -> dict:
    name = args["name"]
    parent_name = args.get("parent_group")
    root = QgsProject.instance().layerTreeRoot()

    if parent_name:
        container = root.findGroup(parent_name)
        if container is None:
            return {"_error": f"Parent group not found: {parent_name}"}
    else:
        container = root


    for child in container.children():
        if isinstance(child, QgsLayerTreeGroup) and child.name() == name:
            return {"created": name, "existed": True}

    container.addGroup(name)
    return {"created": name, "existed": False}


def _move_layer_to_group(args: dict) -> dict:
    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])

    root = QgsProject.instance().layerTreeRoot()
    group = root.findGroup(args["group_name"])
    if group is None:
        return {"_error": f"Group not found: {args['group_name']}"}

    node = root.findLayer(layer.id())
    if node is None:
        return {"_error": "Layer not found in tree"}

    clone = node.clone()
    group.insertChildNode(0, clone)
    node.parent().removeChildNode(node)

    return {"moved": args["layer_name"], "to_group": args["group_name"]}


def _transform_coordinates(args: dict) -> dict:
    source = QgsCoordinateReferenceSystem(args["source_crs"])
    target = QgsCoordinateReferenceSystem(args["target_crs"])

    if not source.isValid():
        return {"_error": f"Invalid source CRS: {args['source_crs']}"}
    if not target.isValid():
        return {"_error": f"Invalid target CRS: {args['target_crs']}"}

    try:
        transform = QgsCoordinateTransform(source, target, QgsProject.instance())
        point = transform.transform(QgsPointXY(args["x"], args["y"]))
    except Exception as e:
        return {"_error": f"Coordinate transform failed: {e}"}

    x, y = point.x(), point.y()



    if not (math.isfinite(x) and math.isfinite(y)):
        return {"_error": (f"The transform from {args['source_crs']} to {args['target_crs']} produced no "
                           f"usable coordinate for ({args['x']}, {args['y']}): the point is outside the "
                           f"area that operation covers."),
                "_code": "INVALID_ARGS"}
    return {"x": x, "y": y, "crs": args["target_crs"]}


def _get_canvas_extent(args: dict) -> dict:
    canvas = iface.mapCanvas()
    extent = canvas.extent()
    return {
        "xmin": extent.xMinimum(),
        "ymin": extent.yMinimum(),
        "xmax": extent.xMaximum(),
        "ymax": extent.yMaximum(),
        "scale": canvas.scale(),
        "crs": canvas.mapSettings().destinationCrs().authid(),

        "area_km2": view_area_km2(extent, canvas.mapSettings().destinationCrs(), QgsProject.instance()),
    }


def _set_canvas_extent(args: dict) -> dict:
    canvas = iface.mapCanvas()
    canvas_crs = canvas.mapSettings().destinationCrs()
    xmin, ymin, xmax, ymax = args["xmin"], args["ymin"], args["xmax"], args["ymax"]










    source = args.get("crs")
    if source:
        source_crs = QgsCoordinateReferenceSystem(str(source))
        if not source_crs.isValid():
            return {"_error": f"Invalid CRS: {source}"}
        if source_crs != canvas_crs:
            try:
                transform = QgsCoordinateTransform(source_crs, canvas_crs, QgsProject.instance())
                box = transform.transformBoundingBox(QgsRectangle(xmin, ymin, xmax, ymax))
            except Exception as exc:  # noqa: BLE001 - an unprojectable box is the caller's error, not a crash
                return {"_error": f"Cannot transform the extent from {source_crs.authid()} to "
                                  f"{canvas_crs.authid()}: {exc}"}
            if box.isEmpty():
                return {"_error": f"The extent is empty once transformed from {source_crs.authid()} "
                                  f"to {canvas_crs.authid()}. Check the coordinate order (x is "
                                  f"longitude or easting)."}
            xmin, ymin, xmax, ymax = box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()


    assumed = None
    assumed_note = None
    if not source and canvas_crs.isGeographic():
        if abs(xmin) > 180 or abs(xmax) > 180 or abs(ymin) > 90 or abs(ymax) > 90:








            if max(abs(xmin), abs(xmax), abs(ymin), abs(ymax)) > _WEB_MERCATOR_MAX:
                return {
                    "_error": f"Coordinates look like meters but canvas CRS is {canvas_crs.authid()} (degrees), "
                    f"and they fall outside the Web Mercator world too. Pass crs= with the CRS they "
                    f"are measured in, or transform them first."
                }
            assumed_source = QgsCoordinateReferenceSystem("EPSG:3857")
            try:
                transform = QgsCoordinateTransform(assumed_source, canvas_crs, QgsProject.instance())
                box = transform.transformBoundingBox(QgsRectangle(xmin, ymin, xmax, ymax))
            except Exception as exc:  # noqa: BLE001 - an unprojectable box is the caller's error, not a crash
                return {"_error": f"Coordinates look like meters but canvas CRS is {canvas_crs.authid()} "
                                  f"(degrees), and reading them as EPSG:3857 failed: {exc}"}
            if box.isEmpty():
                return {"_error": f"Coordinates look like meters but canvas CRS is {canvas_crs.authid()} "
                                  f"(degrees), and read as EPSG:3857 they give an empty extent. Check "
                                  f"the coordinate order (x is easting)."}
            xmin, ymin, xmax, ymax = box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()
            assumed = "EPSG:3857"
            assumed_note = (f"No crs was given and the numbers cannot be degrees, so they were read as "
                            f"EPSG:3857 metres and transformed to {canvas_crs.authid()}. Pass crs= to say "
                            f"so outright.")
    elif not source and not canvas_crs.isGeographic():
        if abs(xmax) < 180 and abs(ymax) < 90 and abs(xmin) < 180 and abs(ymin) < 90:
            return {
                "_error": f"Coordinates look like degrees (lat/lon) but canvas CRS is {canvas_crs.authid()} (meters). "
                f"Pass crs='EPSG:4326' and these numbers again, and they will be transformed for you."
            }

    extent = QgsRectangle(xmin, ymin, xmax, ymax)
    canvas.setExtent(extent)
    canvas.refresh()
    out = {"extent_set": True, "canvas_crs": canvas_crs.authid(), "scale": canvas.scale()}
    if assumed:
        out["assumed_crs"] = assumed
        out["note"] = assumed_note





    shown = extent
    try:
        shown = canvas.extent()
    except Exception:  # noqa: BLE001 - a canvas that cannot report keeps the asked-for box
        pass  # nosec B110 - the asked-for box is already in `shown`
    out.update(_extent_span(shown, canvas_crs))
    return out


def _extent_span(box, crs) -> dict:
    """Width, height and area of *box*, in metres when the CRS is metric."""
    try:
        width, height = box.width(), box.height()
    except Exception:  # noqa: BLE001 - a canvas without an extent reports nothing
        return {}
    if crs is not None and crs.isGeographic():


        import math
        middle = math.radians((box.yMinimum() + box.yMaximum()) / 2.0)
        width = width * 111320.0 * max(0.0, math.cos(middle))
        height = height * 111320.0
    return {"width_m": round(width), "height_m": round(height),
            "area_km2": round(width * height / 1e6, 2)}


def _set_project_crs(args: dict) -> dict:
    crs = QgsCoordinateReferenceSystem(args["crs"])
    if not crs.isValid():
        return {"_error": f"Invalid CRS: {args['crs']}"}
    QgsProject.instance().setCrs(crs)
    return {"project_crs": args["crs"]}


def _write_failure(project, path: str) -> str:
    """Why ``QgsProject.write`` said no."""






    import os
    import tempfile

    try:
        reason = (project.error() or "").strip()
    except Exception:  # noqa: BLE001 - the reason never breaks the report
        reason = ""
    if not reason:
        folder = os.path.dirname(path) or "."
        if not os.path.isdir(folder):
            reason = f"no such folder: {folder}"
        else:




            try:
                handle, probe = tempfile.mkstemp(prefix=".qgis-write-", dir=folder)
            except OSError as exc:
                reason = f"folder is not writable: {folder} ({exc.strerror or exc})"
            else:
                os.close(handle)
                try:
                    os.remove(probe)
                except OSError:  # nosec B110 - a leftover probe is not the user's problem
                    pass
                if os.path.exists(path) and not os.access(path, os.W_OK):
                    reason = "the file exists and is not writable"
    return f"Failed to save project to {path}" + (f": {reason}" if reason else "")


def _save_project(args: dict) -> dict:
    from ..core.security import validate_path
    project = QgsProject.instance()
    path = args.get("path")
    if path:
        path_error = validate_path(path, write=True)
        if path_error:
            return {"_error": path_error}
        ok = project.write(path)
    else:
        if not project.fileName():
            return {"_error": "Project has never been saved. Provide a path."}
        path_error = validate_path(project.fileName(), write=True)
        if path_error:
            return {"_error": path_error}
        ok = project.write()

    if not ok:
        return {"_error": _write_failure(project, path or project.fileName())}
    return {"saved": project.fileName()}


def _load_project(args: dict) -> dict:
    import os

    from ..core.security import validate_path
    path = args["path"]
    path_error = validate_path(path, write=False)
    if path_error:
        return {"_error": path_error}
    if not os.path.exists(path):
        return {"_error": f"File not found: {path}"}

    project = QgsProject.instance()



    from qgis.core import QgsSettings
    settings = QgsSettings()
    previous = settings.value("qgis/enableMacros", None)
    settings.setValue("qgis/enableMacros", 0)
    try:
        ok = project.read(path)
    finally:
        if previous is None:
            settings.remove("qgis/enableMacros")
        else:
            settings.setValue("qgis/enableMacros", previous)
    if not ok:
        return {"_error": f"Failed to load project: {path}"}
    result = {"loaded": path, "layer_count": len(project.mapLayers())}
    macro, has_macro = project.readEntry("Macros", "/pythonCode", "")
    if has_macro and str(macro).strip():
        result["note"] = "This project carries Python macros. They did not run; the user can enable them in QGIS."
    return result


def _create_new_project(args: dict) -> dict:
    from ..core.security import validate_path
    project = QgsProject.instance()



    crs = args.get("crs", "EPSG:4326")
    crs_obj = QgsCoordinateReferenceSystem(crs)
    if not crs_obj.isValid():
        return {"_error": f"Invalid CRS: {crs}"}

    path = args.get("path")
    if path:
        path_error = validate_path(path, write=True)
        if path_error:
            return {"_error": path_error}

    project.clear()
    project.setCrs(crs_obj)

    if path:
        if not project.write(path):
            return {"_error": _write_failure(project, path)}
        return {"created": path, "crs": crs}
    return {"created": "(unsaved)", "crs": crs}
