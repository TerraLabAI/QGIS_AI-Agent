# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later















from __future__ import annotations

import dataclasses
import os
import posixpath
import re
import urllib.parse

from qgis.core import QgsProject, QgsRasterLayer, QgsUnitTypes, QgsVectorLayer
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core import dataset_docs, ground, links, net
from ..core.crs_ref import crs_ref
from ..core.layer_order import feature_count_of, is_remote_vector
from ..core.logger import log_warning
from ..core.qt_compat import enum_member
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from . import core_tools as _core
from . import data_tools as _data
from . import integration_tools as _integration
from . import ogc_inspect
from . import sibling_setup as _setup
from . import stac_tools as _stac
from ._widgets import AI_EDIT_KEYS
from .adapters.ai_edit_access import ACCESS as _AIEDIT
from .crs_at_load import check_loaded
from .csv_loader import CSV_EXTENSIONS
from .data_ogc import _wfs_typenames
from .data_tools import _run_on_main_thread
from .harvest_remote import _add_arcgis_rest_layer, _arcgis_parse
from .layer_io_tools import NO_CRS_WARNING, point_cloud_provider




AI_EDIT_ACTIONS = ("status", "generate", "generation_status", "select_version", "vectorize", "cancel", "presets",
                   "setup", "credits")
AI_SEGMENT_ACTIONS = (
    "status", "detect_auto", "auto_status", "set_zone", "presets", "cancel", "setup",
    "detect_points",
)



AI_EDIT_ACTION_DANGER = {
    "status": "read",
    "generation_status": "read",
    "presets": "read",
    "select_version": "write",
    "cancel": "write",
    "generate": "destructive",
    "vectorize": "destructive",

    "setup": "write",
}
AI_SEGMENT_ACTION_DANGER = {
    "status": "read",
    "auto_status": "read",
    "presets": "read",
    "install_status": "read",
    "set_zone": "write",
    "cancel": "write",
    "detect_auto": "destructive",
    "setup": "write",
}

_CRS_CHECKED_KINDS = frozenset({"vector", "raster", "pointcloud", "geojson", "gpkg", "cog"})
ADD_DATA_KINDS = ("auto", "vector", "raster", "wms", "wfs", "xyz", "vectortile", "stac", "cog", "pmtiles",
                  "pointcloud", "csv", "geojson", "gpkg", "wcs")

_VECTOR_EXT = {
    ".shp", ".gpkg", ".geojson", ".json", ".kml", ".kmz", ".gml", ".csv", ".zip", ".fgb", ".sqlite", ".tab",
    ".mif", ".dxf", ".dwg", ".gpx",

    ".gdb",




    ".parquet", ".geoparquet", ".arrow",
}
_RASTER_EXT = {
    ".tif", ".tiff", ".vrt", ".jp2", ".img", ".asc", ".png", ".jpg", ".jpeg", ".nc", ".hdf", ".ecw", ".sid", ".grd",
}

def register_facade_tools(registry: ToolRegistry):

    registry.register(Tool(
        name="add_data",
        danger="write",
        visible=5,
        label=QT_TRANSLATE_NOOP("AIAgent", "Add {source}[ as {name}]"),
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

                "max_features": {"type": "integer", "minimum": 1},
                "full_extent": {
                    "type": "object",
                    "properties": {"quote": {"type": "string"}, "place": {"type": "string"}},
                    "required": ["quote", "place"],
                },
            },
            "required": ["source"],


            "x-folder-merge": True,



            "x-zip-listing": True,
        },
        handler=_add_data,
        background=_add_data_is_remote,
    ))

    registry.register(Tool(
        name="ai_edit",
        danger="write",
        visible=11,
        label=QT_TRANSLATE_NOOP("AIAgent", "AI Edit: {action}"),
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
                "use_zone": {
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
        action_danger=AI_EDIT_ACTION_DANGER,
    ))

    registry.register(Tool(
        name="ai_segment",
        danger="write",
        visible=12,
        label=QT_TRANSLATE_NOOP("AIAgent", "AI Segmentation[: detect {object_class}][ ({action})]"),
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
                "positive_points": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                    "minItems": 1,
                },
                "negative_points": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                },
                "zone_wkt": {
                    "type": "string",
                },
                "use_zone": {
                    "type": "boolean",
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
            },
            "required": ["action"],
        },
        handler=_ai_segment,
        action_danger=AI_SEGMENT_ACTION_DANGER,
        argument_check=_integration.aiseg_imagery_refusal,
    ))

    registry.register(Tool(
        name="ask_user",
        danger="read",
        visible=13,
        waits_on_user=True,
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
    ))





def _ask_user_direct(args: dict) -> dict:

    return tool_error("ask_user needs the chat panel to show the question.",
                      suggestion="A question written in the answer reaches the user; their next message replies.")


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

        if (service == "wcs" or "/wcs" in lower) and (query.get("request") or [""])[0] != "getcoverage":
            return "wcs"
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







def _service_layer(kind: str, source: str) -> str | dict:

    request = ogc_inspect.ogc_request(source)
    if request and request["service"].lower() == kind and len(request["layers"]) == 1:
        return request["layers"][0]
    if kind != "wfs":
        return ""
    names = _wfs_typenames(ogc_inspect.service_base(links.clean(source)))
    if len(names) == 1:
        return names[0]
    if not names:
        return ""
    shown = names[:_SERVICE_NAMES_SHOWN]
    return {"loaded": False, "service": "WFS", "type_names": shown, "type_name_count": len(names),
            "note": (f"This WFS publishes {len(names)} type names and the call named none. "
                     "layer=<one of them> on add_data loads one"
                     + (", or inspect_data_source lists them all." if len(names) > len(shown) else "."))}


_SERVICE_NAMES_SHOWN = 40


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
            "[west, south, east, north] in EPSG:4326, west below east and south below north.",
        )
    if kind == "vector":
        if _is_url(source):









            if _arcgis_parse(source) is not None:



                return _add_arcgis_rest_layer({"url": source, "name": name, "crs": crs, "bbox": bbox,
                                               "full_extent": args.get("full_extent")})



            if _data._oapif_collection(source) is not None:
                return _data._add_oapif_layer({"url": source, "name": name})
            return _data._add_vector_from_url({"url": source.replace("/vsicurl/", "", 1),
                                               "layer_name": name, "layer": layer, "bbox": bbox,
                                               "full_extent": args.get("full_extent")})
        return _core._add_vector_layer({"path": source, "name": name, "crs": crs, "layer": layer})
    if kind == "raster":
        if _is_url(source):
            if _arcgis_parse(source) is not None:
                return _add_arcgis_rest_layer({"url": source, "name": name, "crs": crs, "kind": "map",
                                               "full_extent": args.get("full_extent")})
            address = source.replace("/vsicurl/", "", 1)
            streamed = _stac._add_cog_layer({"url": address, "name": name})





            if streamed.get("_error") and streamed.get("_code") != "PERMISSION_DENIED":



                return _stac.add_raster_downloaded(address, name)
            return streamed
        return _core._add_raster_layer({"path": source, "name": name, "layer": layer})
    if kind == "cog":
        return _stac._add_cog_layer({"url": source.replace("/vsicurl/", "", 1), "name": name})
    if kind == "stac":
        return _stac._add_stac_layer({"item_url": source, "name": name, "asset": layer})
    if kind == "pmtiles":


        return _stac._add_pmtiles_layer({"url": source, "name": name, "layer": layer,
                                         "bbox": bbox or args.get("bbox"), "mode": args.get("mode"),
                                         "full_extent": args.get("full_extent")})
    if kind == "pointcloud":
        return _core._add_point_cloud_layer({"path": source, "name": name})
    if kind == "xyz":

        return _data._add_xyz_layer({"source": source, "name": name, "bbox": bbox or args.get("bbox"),
                                     "zmin": args.get("zmin"), "zmax": args.get("zmax")})
    if kind == "vectortile":
        return _data._add_vector_tile_layer({
            "url": source, "name": name, "style": args.get("style"),
            "zmin": args.get("zmin"), "zmax": args.get("zmax"),
        })
    if kind in ("wms", "wfs") and not layer:
        found = _service_layer(kind, source)
        if isinstance(found, dict):
            return found
        layer = found
    if kind == "wms":
        if not layer:
            return tool_error(
                "A WMS source needs the layer name to show.",
                "INVALID_ARGS",
                "layer=<WMS layer name>. inspect_data_source lists what the service serves.",
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
                "layer=<WFS typename>. inspect_data_source lists what the service serves.",
            )


        wfs_args = {"url": source, "typename": layer, "name": name, "bbox": bbox,
                    "max_features": args.get("max_features"), "full_extent": args.get("full_extent")}
        if crs:
            wfs_args["crs"] = crs
        return _data._add_wfs_layer(wfs_args)
    if kind == "wcs":
        if not layer:
            return tool_error(
                "A WCS source needs the coverage to load.",
                "INVALID_ARGS",
                "layer=<coverage id>. inspect_data_source lists what the service serves.",
            )
        return _data._add_wcs_layer({"url": source, "coverage": layer, "name": name, "crs": crs, "bbox": bbox})
    return tool_error(f"Unknown kind: {kind}", "INVALID_ARGS", f"kind must be one of {list(ADD_DATA_KINDS)}.")


def _unit_name(layer) -> str:
    try:
        return QgsUnitTypes.toString(layer.crs().mapUnits())
    except Exception:  # nosec B110
        return "unknown"


def _describe_layer(layer, kind: str, known_count=None) -> dict:
    extent = layer.extent()
    out = {
        "layer_id": layer.id(),
        "name": layer.name(),
        "kind": kind,
        "crs": crs_ref(layer.crs()),
        "extent": {
            "xmin": extent.xMinimum(),
            "ymin": extent.yMinimum(),
            "xmax": extent.xMaximum(),
            "ymax": extent.yMaximum(),
        },
        "units": _unit_name(layer),
    }
    if not out["crs"]:


        out["crs_name"] = layer.crs().description() if layer.crs().isValid() else "none declared"
    if isinstance(layer, QgsVectorLayer):





        out["feature_count"] = known_count if known_count is not None else feature_count_of(layer)
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






_REMOTE_KINDS = frozenset({"cog", "stac", "pmtiles", "wms", "wfs", "vectortile", "wcs"})


def _add_data_is_remote(args: dict) -> bool:

    source = str(args.get("source") or "").strip()
    if not source:
        return False
    kind = deduce_kind(source, args.get("kind"))
    if kind in _REMOTE_KINDS:
        return True

    return kind in ("vector", "raster", "xyz") and _is_url(source)


_TEMPLATE_FIELD = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")


def _merge_crs_check(described: dict, found: dict) -> None:






    described["crs_check"] = found["crs_check"]
    earlier = described.get("warning") or ""
    if earlier == NO_CRS_WARNING:
        earlier = ""
    described["warning"] = " ".join(filter(None, (earlier, found["warning"])))
    advice = described.get("suggestion") or ""
    described["suggestion"] = " ".join(filter(None, (advice if advice != found["suggestion"] else "",
                                                     found["suggestion"])))


def _add_data(args: dict) -> dict:
    source = _data.hosted_department_url(str(args.get("source") or "").strip())
    if not source:
        return tool_error("source is empty.", "INVALID_ARGS", "a path, URL, service endpoint or basemap name.")
    args = dict(args, source=source)





    unfilled = _TEMPLATE_FIELD.findall(source)
    if unfilled:
        names = ", ".join(sorted(set(unfilled)))
        return tool_error(
            f"The URL still carries the template field {{{names}}}: it stands for several files.",
            "INVALID_ARGS",
            f"{{{names}}} takes a value the dataset's notes list (find_datasets shows them), "
            "one call per file.")



    refusal = net.withdrawn_reason(urllib.parse.urlsplit(source).hostname or "")
    if refusal:
        return tool_error(refusal, "INVALID_ARGS",
                          "find_datasets lists rows for the same theme to add instead.")




    resolved_from = ""
    if _is_url(source) and not source.startswith("/vsicurl/"):
        link = links.resolve(source)
        if link.kind in ("listing", "inline", "unreachable"):
            args = dict(args, kind="vector")
        elif link.kind != "unchanged":
            resolved_from, source = source, link.url
            args = dict(args, source=source)
    kind = deduce_kind(source, args.get("kind"))
    result = _dispatch_add(kind, args)
    if not isinstance(result, dict) or result.get("_error") is not None:
        return result
    if resolved_from:
        result["resolved_from"] = resolved_from


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


            "bbox", "box_km2", "path", "gpkg_path", "source_format", "layer", "seconds",


            "dataset_notes",

            "resolved_from",


            "licence", "attribution",

            "data_date",

            "crs_assigned",

            "description_fields",

            "scale_range",
        ):
            if key in result:
                described[key] = result[key]



        if kind in _CRS_CHECKED_KINDS and not source.lower().split("?", 1)[0].endswith(CSV_EXTENSIONS):
            found = check_loaded(layer, crs_assigned=bool(result.get("crs_assigned")),
                                 cad=source.lower().endswith((".dxf", ".dwg")))
            if found:
                _merge_crs_check(described, found)
        return described



    return _run_on_main_thread(_finish, timeout=120)





def _plugin_missing(label: str, tool: str) -> dict:
    return tool_error(
        f"{label} is not installed in this QGIS.",
        "PERMISSION_DENIED",
        f"{tool} action setup opens the Plugin Manager on {label} (its card is the yes).",
    )


def _signed_out(tool: str, label: str) -> dict:

    return tool_error(
        f"{label} is not connected to a TerraLab account.",
        "PERMISSION_DENIED",
        f"{tool} action setup opens the one-click sign-in page (its card is the yes).",
    )


def _loaded(keys) -> bool:
    import qgis.utils

    return any(qgis.utils.plugins.get(key) is not None for key in keys)


def _aiseg_not_running(presence: dict) -> dict:





    if presence.get("state") == "absent":
        return _plugin_missing("AI Segmentation by TerraLab", "ai_segment")
    status = _integration.aiseg_not_running_status(presence)
    return tool_error(
        f"AI Segmentation by TerraLab is installed but not running ({status['state']}).",
        "PERMISSION_DENIED",
        ("ai_segment action setup switches the plugin on." if status["state"] == "PLUGIN_DISABLED"
         else status["action_required"]),
    )


def _ai_edit(args: dict) -> dict:
    action = args.get("action")
    if action == "status":
        return _integration._aiedit_status(args)
    if action == "setup":
        return _setup.setup("ai_edit")
    if not _loaded(AI_EDIT_KEYS):
        found = _setup.presence(AI_EDIT_KEYS)
        if found["state"] == "absent":
            return _plugin_missing("AI Edit by TerraLab", "ai_edit")
        return tool_error(
            "AI Edit by TerraLab is installed but not running.", "PERMISSION_DENIED",
            ("ai_edit action setup switches the plugin on." if found["state"] == "disabled"
             else _setup.not_running("ai_edit", found)["action_required"]),
        )
    if action == "generate":
        if not str(args.get("prompt") or "").strip():
            return tool_error(
                "generate needs a prompt.", "INVALID_ARGS",
                "prompt, the edit to apply, plus bbox or use_canvas_extent.",
            )
        if _setup.signed_in("ai_edit", _setup.presence(AI_EDIT_KEYS)["plugin"]) is False:
            return _signed_out("ai_edit", "AI Edit")
        return _integration._aiedit_generate(args)
    if action == "generation_status":
        return _AIEDIT.generation_status()
    if action == "select_version":
        if args.get("index") is None:
            return tool_error(
                "select_version needs index.", "INVALID_ARGS",
                "index: 0 = Original, 1 = V1, 2 = V2.",
            )
        return _integration._aiedit_select_version(args)
    if action == "vectorize":
        if not args.get("target_rgb"):
            return tool_error(
                "vectorize needs target_rgb.", "INVALID_ARGS",
                "target_rgb is [r, g, b] 0-255, the color to trace.",
            )
        return _integration._aiedit_vectorize(args)
    if action == "cancel":
        return _integration._aiedit_cancel(args)
    if action == "presets":
        return _integration._aiedit_get_presets(args)
    if action == "credits":
        return _integration._aiedit_get_credits(args)
    return tool_error(f"Unknown action: {action}", "INVALID_ARGS", f"action must be one of {list(AI_EDIT_ACTIONS)}.")





def _ai_segment(args: dict) -> dict:
    action = args.get("action")
    if action == "status":
        return _integration._aiseg_status(args)
    if action == "install_status":


        return _integration._aiseg_status(args)
    if action == "setup":
        return _setup.setup("ai_segment")
    presence = _integration.aiseg_presence()
    if presence["plugin"] is None:
        return _aiseg_not_running(presence)
    if action == "detect_auto":
        if _setup.signed_in("ai_segment", presence["plugin"]) is False:
            return _signed_out("ai_segment", "AI Segmentation")
        return _integration._aiseg_detect_auto(args)
    if action == "detect_points":
        return _integration._aiseg_detect_points(args)
    if action == "auto_status":
        return _integration._aiseg_auto_status(args)
    if action == "set_zone":
        return _integration._aiseg_set_zone(args)
    if action == "presets":
        return _integration._aiseg_get_presets(args)
    if action == "cancel":
        return _integration._aiseg_auto_cancel(args)
    return tool_error(f"Unknown action: {action}", "INVALID_ARGS", f"action must be one of {list(AI_SEGMENT_ACTIONS)}.")




_LAYER_EXT = _VECTOR_EXT | _RASTER_EXT


def wrap_run_processing(registry: ToolRegistry):






    tool = registry.get_tool("run_processing")
    if tool is not None and not getattr(tool.handler, "_facade_wrapped", False):
        inner = tool.handler

        def run_processing(args: dict) -> dict:
            return _run_processing_guarded(args, inner)

        run_processing._facade_wrapped = True
        registry.register(dataclasses.replace(tool, handler=run_processing), replace=True)

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
        registry.register(dataclasses.replace(status_tool, handler=get_task_status), replace=True)


def _processing_config():
    try:
        from processing.core.ProcessingConfig import ProcessingConfig
    except Exception:
        return None
    return ProcessingConfig


def _invalid_geometry_indexes(config) -> tuple:

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

        from ._layers import _source_key

        wanted = _source_key(str(path).split("|")[0])
        candidates = [c for c in candidates if _source_key(str(c.source() or "").split("|")[0]) == wanted]
    if len(candidates) != 1:
        return None
    return candidates[0]


_VECTOR_COUNT_CAP = 10_000


def _vector_count(layer) -> tuple:

    from qgis.core import QgsFeatureRequest

    if is_remote_vector(layer):



        return -1, False
    count = layer.featureCount()
    if count is not None and count >= 0:
        return int(count), False
    request = QgsFeatureRequest()
    request.setNoAttributes()
    request.setLimit(_VECTOR_COUNT_CAP + 1)
    try:
        request.setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry"))
    except Exception:  # nosec B110
        pass
    scanned = sum(1 for _ in layer.getFeatures(request))
    if scanned > _VECTOR_COUNT_CAP:
        return _VECTOR_COUNT_CAP, True
    return scanned, False


def annotate_outputs(outputs: dict) -> tuple:

    problems = []
    for key, value in outputs.items():
        if not isinstance(value, dict):
            continue
        if value.get("added_to_project") is False:



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
            "The algorithm finished but " + "; ".join(problems) + ". The inputs (CRS, extent, "
            "filters, selection) may explain this output."
        )
    return "ok", ""
