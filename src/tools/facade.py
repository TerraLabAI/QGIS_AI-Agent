# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The three facade tools the model always sees: add_data, ai_edit, ai_segment."""














from __future__ import annotations

import os
import posixpath
import re
import urllib.parse

from qgis.core import QgsProject, QgsRasterLayer, QgsUnitTypes, QgsVectorLayer

from ..core import dataset_docs, ground, net
from ..core.logger import log_warning
from ..core.qt_compat import enum_member
from ..core.tool_registry import VISIBLE_TOOLS, Tool, ToolRegistry, tool_error
from . import aiseg_install_tools as _install
from . import core_tools as _core
from . import data_tools as _data
from . import integration_tools as _integration
from . import stac_tools as _stac
from ._widgets import AI_EDIT_KEYS, AI_SEGMENT_KEYS
from .adapters.ai_edit_access import ACCESS as _AIEDIT
from .data_tools import _run_on_main_thread
from .harvest_remote import _add_arcgis_rest_layer, _arcgis_parse
from .layer_io_tools import point_cloud_provider

AI_EDIT_ACTIONS = ("status", "generate", "generation_status", "select_version", "vectorize", "cancel", "presets")
AI_SEGMENT_ACTIONS = (
    "status", "detect_auto", "auto_status", "set_zone", "presets",
    "install_status", "cancel", "load_model",
)
ADD_DATA_KINDS = ("auto", "vector", "raster", "wms", "wfs", "xyz", "vectortile", "stac", "cog", "pmtiles",
                  "pointcloud", "csv", "geojson", "gpkg")

_VECTOR_EXT = {
    ".shp", ".gpkg", ".geojson", ".json", ".kml", ".kmz", ".gml", ".csv", ".zip", ".fgb", ".sqlite", ".tab",
    ".mif", ".dxf", ".gpx",




    ".parquet", ".geoparquet", ".arrow",
}
_RASTER_EXT = {
    ".tif", ".tiff", ".vrt", ".jp2", ".img", ".asc", ".png", ".jpg", ".jpeg", ".nc", ".hdf", ".ecw", ".sid", ".grd",
}

PLUGIN_MANAGER_SUGGESTION = (
    "Tell the user {plugin} is not installed and offer the QGIS plugin manager "
    "(Plugins > Manage and Install Plugins, search '{plugin}')."
)


def register_facade_tools(registry: ToolRegistry):
    """Register add_data, ai_edit and ai_segment and flag the visible catalog."""
    registry.register(Tool(
        name="add_data",
        input_schema={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                },
                "kind": {
                    "type": "string",
                    "enum": list(ADD_DATA_KINDS),
                },
                "name": {
                    "type": "string",
                },
                "layer": {
                    "type": "string",
                },
                "crs": {
                    "type": "string",
                },
                "group": {
                    "type": "string",
                },
                "style": {
                    "type": "string",
                },
                "bbox": {
                    "type": "array",
                    "items": {"type": "number"},
                },
                "mode": {
                    "type": "string",
                    "enum": ["extract", "tiles"],
                },
                "zmin": {"type": "integer"},
                "zmax": {"type": "integer"},
            },
            "required": ["source"],
        },
        handler=_add_data,
        background=_add_data_is_remote,
        danger="write",
        visible=True,
    ))

    registry.register(Tool(
        name="ai_edit",
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(AI_EDIT_ACTIONS)},
                "confirm_area_km2": {
                    "type": "number",
                },
                "prompt": {
                    "type": "string",
                },
                "bbox": {
                    "type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4,
                },
                "use_canvas_extent": {
                    "type": "boolean",
                },
                "resolution": {
                    "type": "string",
                    "enum": ["1K", "2K", "4K"],
                },
                "reference_layers": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "template_id": {"type": "string"},
                "index": {"type": "integer"},
                "layer_name": {
                    "type": "string",
                },
                "target_rgb": {
                    "type": "array", "items": {"type": "integer"}, "minItems": 3, "maxItems": 3,
                },
                "tolerance": {"type": "integer"},
                "simplify_factor": {"type": "number"},
                "class_label": {
                    "type": "string",
                },
                "exit": {"type": "boolean"},
            },
            "required": ["action"],
        },
        handler=_ai_edit,
        danger="write",
        visible=True,
    ))

    registry.register(Tool(
        name="ai_segment",
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(AI_SEGMENT_ACTIONS)},
                "confirm_area_km2": {
                    "type": "number",
                },
                "object_class": {
                    "type": "string",
                },
                "zone_wkt": {
                    "type": "string",
                },
                "bbox": {
                    "type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4,
                },
                "use_canvas_extent": {
                    "type": "boolean",
                },
                "layer_name": {
                    "type": "string",
                },
                "detail": {
                    "type": "integer", "minimum": 1, "maximum": 7,
                },
                "exemplars": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
                            "label": {"type": "integer", "enum": [0, 1]},
                        },
                        "required": ["bbox"],
                    },
                },
                "confidence": {
                    "type": "number", "minimum": 0.05, "maximum": 0.95,
                },
                "accept_weak_class": {
                    "type": "boolean",
                },
                "refine": {
                    "type": "object",
                    "properties": {
                        "simplify_px": {"type": "number"},
                        "expand_px": {"type": "integer"},
                        "fill_holes": {"type": "boolean"},
                        "right_angles": {"type": "boolean"},
                        "min_size_m2": {"type": "number"},
                    },
                },
                "include_packages": {
                    "type": "boolean",
                },
                "timeout_s": {
                    "type": "number", "minimum": 1, "maximum": 600,
                },
            },
            "required": ["action"],
        },
        handler=_ai_segment,
        danger="write",
        visible=True,
    ))

    registry.register(Tool(
        name="ask_user",
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "recommended": {
                    "type": ["integer", "string"],
                },
                "why": {
                    "type": "string",
                },
                "allow_free_text": {
                    "type": "boolean",
                },
            },
            "required": ["question"],
        },
        handler=_ask_user_direct,
        danger="read",
    ))
    for name in VISIBLE_TOOLS:
        tool = registry.get_tool(name)
        if tool is not None:
            tool.visible = True

    _wrap_run_processing(registry)





def _ask_user_direct(args: dict) -> dict:
    """The executor answers ask_user through the chat panel; a direct call has no user."""
    return tool_error("ask_user needs the chat panel to show the question.",
                      suggestion="Say the question in your answer and wait for the user's next message.")


def _is_url(source: str) -> bool:
    return source.lower().startswith(("http://", "https://", "ftp://", "/vsicurl/"))


def _extension(source: str) -> str:
    if _is_url(source):
        path = urllib.parse.urlparse(source.replace("/vsicurl/", "", 1)).path
        return posixpath.splitext(path)[1].lower()
    return os.path.splitext(source)[1].lower()


def _pmtiles_url(source: str) -> bool:
    return ".pmtiles" in source.lower()


def deduce_kind(source: str, kind: str | None = None) -> str:
    """The source kind: the explicit one, else from the URL pattern or extension."""
    kind = (kind or "auto").strip().lower()
    if kind in ("csv", "geojson", "gpkg"):
        return "vector"
    if kind != "auto":
        return kind
    text = source.strip()
    lower = text.lower()
    ext = _extension(text)
    if "{z}" in lower and "{x}" in lower and "{y}" in lower:



        return "vectortile" if ext in _data.VECTOR_TILE_EXTENSIONS else "xyz"
    if _is_url(text):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(text).query.lower())
        service = (query.get("service") or [""])[0]
        if service == "wms" or "/wms" in lower or "wmts" in lower:
            return "wms"
        if service == "wfs" or "/wfs" in lower:
            return "wfs"
        if _pmtiles_url(text):
            return "pmtiles"




        if point_cloud_provider(text):
            return "pointcloud"
        if ext in (".tif", ".tiff"):
            return "cog"




        if "/collections/" in lower and _data._oapif_collection(text) is None and "/items/" in lower:
            return "stac"
        if ext == ".json" and "stac" in lower:
            return "stac"
        if ext in _VECTOR_EXT or ext == "":
            return "vector"
        if ext in _RASTER_EXT:
            return "cog"
        return "vector"
    if ext == ".pmtiles":
        return "pmtiles"
    if point_cloud_provider(text):
        return "pointcloud"
    if ext in _RASTER_EXT:
        return "raster"
    if ext in _VECTOR_EXT:
        return "vector"
    if os.path.exists(text):
        return "vector"

    return "xyz"


def _dispatch_add(kind: str, args: dict) -> dict:
    source = args["source"]
    name = args.get("name")
    layer = args.get("layer")
    crs = args.get("crs")






    raw_bbox = args.get("bbox")
    bbox = _stac._parse_bbox(raw_bbox) if raw_bbox else None
    if raw_bbox and bbox is None:
        return tool_error(
            "That bbox is not a usable box.",
            "INVALID_ARGS",
            "Pass [west, south, east, north] in EPSG:4326, west below east and south below north.",
        )
    if kind == "vector":
        if _is_url(source):






            if _arcgis_parse(source) is not None:
                return _add_arcgis_rest_layer({"url": source, "name": name, "crs": crs})



            if _data._oapif_collection(source) is not None:
                return _data._add_oapif_layer({"url": source, "name": name})
            return _data._add_vector_from_url({"url": source.replace("/vsicurl/", "", 1),
                                               "layer_name": name, "layer": layer, "bbox": bbox})
        return _core._add_vector_layer({"path": source, "name": name, "crs": crs, "layer": layer})
    if kind == "raster":
        if _is_url(source):
            if _arcgis_parse(source) is not None:
                return _add_arcgis_rest_layer({"url": source, "name": name, "crs": crs, "kind": "map"})
            address = source.replace("/vsicurl/", "", 1)
            streamed = _stac._add_cog_layer({"url": address, "name": name})







            if streamed.get("_error") and streamed.get("_code") != "PERMISSION_DENIED":
                downloaded = _stac.add_raster_downloaded(address, name)
                if not downloaded.get("_error"):
                    return downloaded
            return streamed
        return _core._add_raster_layer({"path": source, "name": name})
    if kind == "cog":
        return _stac._add_cog_layer({"url": source.replace("/vsicurl/", "", 1), "name": name})
    if kind == "stac":
        return _stac._add_stac_layer({"item_url": source, "name": name, "asset": layer})
    if kind == "pmtiles":


        return _stac._add_pmtiles_layer({"url": source, "name": name, "layer": layer,
                                         "bbox": bbox or args.get("bbox"), "mode": args.get("mode")})
    if kind == "pointcloud":
        return _core._add_point_cloud_layer({"path": source, "name": name})
    if kind == "xyz":
        return _data._add_xyz_layer({"source": source, "name": name})
    if kind == "vectortile":
        return _data._add_vector_tile_layer({
            "url": source, "name": name, "style": args.get("style"),
            "zmin": args.get("zmin"), "zmax": args.get("zmax"),
        })
    if kind == "wms":
        if not layer:
            return tool_error(
                "A WMS source needs the layer name to show.",
                "INVALID_ARGS",
                "Pass layer=<WMS layer name>. inspect_data_source lists what the service serves.",
            )
        wms_args = {"url": source, "layers": layer, "name": name}
        if crs:
            wms_args["crs"] = crs
        return _data._add_wms_layer(wms_args)
    if kind == "wfs":
        if not layer:
            return tool_error(
                "A WFS source needs the type name to load.",
                "INVALID_ARGS",
                "Pass layer=<WFS typename>. inspect_data_source lists what the service serves.",
            )
        wfs_args = {"url": source, "typename": layer, "name": name}
        if crs:
            wfs_args["crs"] = crs
        return _data._add_wfs_layer(wfs_args)
    return tool_error(f"Unknown kind: {kind}", "INVALID_ARGS", f"kind must be one of {list(ADD_DATA_KINDS)}.")


def _unit_name(layer) -> str:
    try:
        return QgsUnitTypes.toString(layer.crs().mapUnits())
    except Exception:  # nosec B110 - geometry flag is optional
        return "unknown"



REMOTE_VECTOR_PROVIDERS = ("WFS", "wfs", "arcgisfeatureserver", "oapif")


def _describe_layer(layer, kind: str, known_count=None) -> dict:
    extent = layer.extent()
    out = {
        "layer_id": layer.id(),
        "name": layer.name(),
        "kind": kind,
        "crs": layer.crs().authid(),
        "extent": {
            "xmin": extent.xMinimum(),
            "ymin": extent.yMinimum(),
            "xmax": extent.xMaximum(),
            "ymax": extent.yMaximum(),
        },
        "units": _unit_name(layer),
    }
    if isinstance(layer, QgsVectorLayer):


        out["feature_count"] = known_count if known_count is not None else layer.featureCount()
        out["count_units"] = "features"
        try:
            from qgis.core import QgsWkbTypes

            out["geometry_type"] = QgsWkbTypes.geometryDisplayString(layer.geometryType())
        except Exception:
            out["geometry_type"] = str(layer.geometryType())
    elif isinstance(layer, QgsRasterLayer):
        out["band_count"] = layer.bandCount()
        out["width"] = layer.width()
        out["height"] = layer.height()
        out["size_units"] = "pixels"
        out.update(ground.pixel_facts(layer))
    else:
        out["layer_type"] = type(layer).__name__
    return out


def _move_to_group(layer, group_name: str):
    root = QgsProject.instance().layerTreeRoot()
    group = root.findGroup(group_name)
    if group is None:
        group = root.addGroup(group_name)
    node = root.findLayer(layer.id())
    if node is None:
        return
    clone = node.clone()
    group.addChildNode(clone)
    parent = node.parent()
    if parent is not None:
        parent.removeChildNode(node)






_REMOTE_KINDS = frozenset({"cog", "stac", "pmtiles", "wms", "wfs", "vectortile"})


def _add_data_is_remote(args: dict) -> bool:
    """Whether this add_data call can run off the main thread."""
    source = str(args.get("source") or "").strip()
    if not source:
        return False
    kind = deduce_kind(source, args.get("kind"))
    if kind in _REMOTE_KINDS:
        return True
    return kind in ("vector", "raster") and _is_url(source)


_TEMPLATE_FIELD = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")


def _add_data(args: dict) -> dict:
    source = _data.hosted_department_url(str(args.get("source") or "").strip())
    if not source:
        return tool_error("source is empty.", "INVALID_ARGS", "Pass a path, URL, service endpoint or basemap name.")
    args = dict(args, source=source)





    unfilled = _TEMPLATE_FIELD.findall(source)
    if unfilled:
        names = ", ".join(sorted(set(unfilled)))
        return tool_error(
            f"The URL still carries the template field {{{names}}}: it stands for several files.",
            "INVALID_ARGS",
            f"Replace {{{names}}} with one of the values the dataset's notes list (find_datasets shows them), "
            "one call per file.")



    refusal = net.withdrawn_reason(urllib.parse.urlsplit(source).hostname or "")
    if refusal:
        return tool_error(refusal, "INVALID_ARGS",
                          "Call find_datasets for the same theme and add a row it returns.")
    kind = deduce_kind(source, args.get("kind"))
    result = _dispatch_add(kind, args)
    if not isinstance(result, dict) or result.get("_error") is not None:
        return result


    if _add_data_is_remote(args):
        dataset_docs.attach(result, source)

    def _finish():
        layer_id = result.get("layer_id")
        layer = QgsProject.instance().mapLayer(layer_id) if layer_id else None
        if layer is None:
            result.setdefault("kind", kind)
            return result
        group = args.get("group")
        if group:
            try:
                _move_to_group(layer, group)
                result["group"] = group
            except Exception as e:
                result["group_error"] = str(e)
        described = _describe_layer(layer, kind, known_count=result.get("feature_count"))
        for key in (
            "url", "source", "provider", "item_id", "collection", "asset_key", "_note",
            "wms_layers", "typename", "group", "group_error",


            "warning", "suggestion", "size", "size_bytes", "layers_available", "styled", "zmin", "zmax",


            "bbox", "area_km2", "path", "layer", "seconds",


            "dataset_notes",
        ):
            if key in result:
                described[key] = result[key]
        return described



    return _run_on_main_thread(_finish, timeout=120)





def _plugin_missing(label: str) -> dict:
    return tool_error(
        f"{label} is not installed in this QGIS.",
        "PERMISSION_DENIED",
        PLUGIN_MANAGER_SUGGESTION.format(plugin=label),
    )


def _loaded(keys) -> bool:
    import qgis.utils

    return any(qgis.utils.plugins.get(key) is not None for key in keys)


def _ai_edit(args: dict) -> dict:
    action = args.get("action")
    if action == "status":
        return _integration._aiedit_status(args)
    if not _loaded(AI_EDIT_KEYS):
        return _plugin_missing("AI Edit by TerraLab")
    if action == "generate":
        if not str(args.get("prompt") or "").strip():
            return tool_error(
                "generate needs a prompt.", "INVALID_ARGS",
                "Pass prompt, the edit to apply, plus bbox or use_canvas_extent.",
            )
        return _integration._aiedit_generate(args)
    if action == "generation_status":
        return _AIEDIT.generation_status()
    if action == "select_version":
        if args.get("index") is None:
            return tool_error(
                "select_version needs index.", "INVALID_ARGS",
                "Pass index: 0 = Original, 1 = V1, 2 = V2.",
            )
        return _integration._aiedit_select_version(args)
    if action == "vectorize":
        if not args.get("target_rgb"):
            return tool_error(
                "vectorize needs target_rgb.", "INVALID_ARGS",
                "Pass target_rgb as [r, g, b] 0-255, the color to trace.",
            )
        return _integration._aiedit_vectorize(args)
    if action == "cancel":
        return _integration._aiedit_cancel(args)
    if action == "presets":
        return _integration._aiedit_get_presets(args)
    return tool_error(f"Unknown action: {action}", "INVALID_ARGS", f"action must be one of {list(AI_EDIT_ACTIONS)}.")





def _ai_segment(args: dict) -> dict:
    action = args.get("action")
    if action == "status":
        return _integration._aiseg_status(args)
    if action == "install_status":



        if not _loaded(AI_SEGMENT_KEYS) and _install._seg_package() is None:
            return _plugin_missing("AI Segmentation by TerraLab")
        return _install._install_status(args)
    if not _loaded(AI_SEGMENT_KEYS):
        return _plugin_missing("AI Segmentation by TerraLab")
    if action == "detect_auto":
        return _integration._aiseg_detect_auto(args)
    if action == "auto_status":
        return _integration._aiseg_auto_status(args)
    if action == "set_zone":
        return _integration._aiseg_set_zone(args)
    if action == "presets":
        return _integration._aiseg_get_presets(args)
    if action == "cancel":
        return _integration._aiseg_auto_cancel(args)
    if action == "load_model":
        return _integration._aiseg_load_model(args)
    return tool_error(f"Unknown action: {action}", "INVALID_ARGS", f"action must be one of {list(AI_SEGMENT_ACTIONS)}.")




_LAYER_EXT = _VECTOR_EXT | _RASTER_EXT


def _wrap_run_processing(registry: ToolRegistry):
    """Route run_processing through the geometry guard and the output check."""




    tool = registry.get_tool("run_processing")
    if tool is not None and not getattr(tool.handler, "_facade_wrapped", False):
        inner = tool.handler

        def run_processing(args: dict) -> dict:
            return _run_processing_guarded(args, inner)

        run_processing._facade_wrapped = True
        tool.handler = run_processing

    status_tool = registry.get_tool("get_task_status")
    if status_tool is not None and not getattr(status_tool.handler, "_facade_wrapped", False):
        inner_status = status_tool.handler

        def get_task_status(args: dict) -> dict:
            result = inner_status(args)
            if (
                isinstance(result, dict)
                and result.get("status") == "complete"
                and isinstance(result.get("outputs"), dict)
            ):
                status, message = annotate_outputs(result["outputs"])
                result["output_status"] = status
                if message:
                    result["message"] = message
            return result

        get_task_status._facade_wrapped = True
        status_tool.handler = get_task_status


def _processing_config():
    try:
        from processing.core.ProcessingConfig import ProcessingConfig
    except Exception:
        return None
    return ProcessingConfig


def _invalid_geometry_indexes(config) -> tuple:
    """(index of the skip option, index of the default option) of FILTER_INVALID_GEOMETRIES."""
    setting = config.settings.get(config.FILTER_INVALID_GEOMETRIES)
    options = list(getattr(setting, "options", None) or [])
    if not options:
        return None, None
    skip = next((i for i, label in enumerate(options) if "skip" in str(label).lower()), None)
    if skip is None and len(options) > 1:
        skip = 1
    default = getattr(setting, "default", None)
    default_index = options.index(default) if default in options else None
    return skip, default_index


def _run_processing_guarded(args: dict, inner) -> dict:
    """Run with invalid geometries skipped, restore the setting, then check the outputs."""







    config = _processing_config()
    restore = None
    if config is not None:
        try:
            skip, default_index = _invalid_geometry_indexes(config)
            previous = config.getSetting(config.FILTER_INVALID_GEOMETRIES)
            if skip is not None and previous != skip:
                config.setSettingValue(config.FILTER_INVALID_GEOMETRIES, skip)
                restore = previous if isinstance(previous, int) else default_index
        except Exception as e:
            log_warning(f"Could not set FILTER_INVALID_GEOMETRIES to skip: {e}")
            restore = None
    try:
        result = inner(args)
    finally:
        if restore is not None:
            try:
                config.setSettingValue(config.FILTER_INVALID_GEOMETRIES, restore)
            except Exception as e:
                log_warning(f"Could not restore FILTER_INVALID_GEOMETRIES: {e}")
    return check_processing_result(result)


def check_processing_result(result):
    """feature_count on every vector output, and status ok or ambiguous, on a finished run."""
    if not isinstance(result, dict) or result.get("_error") is not None:
        return result
    if result.get("task_id") and result.get("status") == "running":
        return result
    outputs = result.get("outputs")
    if not isinstance(outputs, dict):
        return result
    status, message = annotate_outputs(outputs)
    result["status"] = status
    if message:
        result["message"] = message
    return result


def _output_layer(value: dict):
    """The project layer an output entry points at, or None when unknown or ambiguous."""
    project = QgsProject.instance()
    layer_id = value.get("layer_id")
    if layer_id:
        layer = project.mapLayer(str(layer_id))
        if layer is not None:
            return layer
    name = value.get("layer_name")
    if not name:
        return None
    candidates = project.mapLayersByName(str(name))
    path = value.get("path")
    if path and len(candidates) > 1:
        candidates = [c for c in candidates if str(c.source() or "").split("|")[0] == str(path)]
    if len(candidates) != 1:
        return None
    return candidates[0]


_VECTOR_COUNT_CAP = 10_000


def _vector_count(layer) -> tuple:
    """Feature count and whether it was capped rather than an exact scan."""
    from qgis.core import QgsFeatureRequest

    count = layer.featureCount()
    if count is not None and count >= 0:
        return int(count), False
    if str(layer.providerType() or "") in REMOTE_VECTOR_PROVIDERS:


        return -1, False
    request = QgsFeatureRequest()
    request.setNoAttributes()
    request.setLimit(_VECTOR_COUNT_CAP + 1)
    try:
        request.setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry"))
    except Exception:  # nosec B110 - geometry flag is optional
        pass
    scanned = sum(1 for _ in layer.getFeatures(request))
    if scanned > _VECTOR_COUNT_CAP:
        return _VECTOR_COUNT_CAP, True
    return scanned, False


def annotate_outputs(outputs: dict) -> tuple:
    """Add layer_id, feature_count and valid to the output entries. Returns (status, message)."""
    problems = []
    for key, value in outputs.items():
        if not isinstance(value, dict):
            continue
        layer = _output_layer(value)
        if layer is None:
            path = value.get("path")
            if path and not value.get("layer_name") and os.path.splitext(str(path))[1].lower() in _LAYER_EXT:
                problems.append(f"{key} ({os.path.basename(str(path))}) could not be opened as a layer")
            continue
        value.setdefault("layer_id", layer.id())
        if not layer.isValid():
            value["valid"] = False
            problems.append(f"{key} ({layer.name()}) is not a valid layer")
            continue
        if isinstance(layer, QgsVectorLayer):
            count, approximate = _vector_count(layer)
            value["feature_count"] = count
            if approximate:
                value["feature_count_approximate"] = True
            if count == 0:
                problems.append(f"{key} ({layer.name()}) has no features")
    if problems:
        return "ambiguous", (
            "The algorithm finished but " + "; ".join(problems) + ". Check the inputs (CRS, extent, "
            "filters, selection) before using this output."
        )
    return "ok", ""
