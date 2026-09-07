# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
from __future__ import annotations

"""Google Earth Engine tools, server-side global EO data and compute.

Conditional: registers nothing unless the ``ee`` (earthengine-api) package is
importable. Kept import-safe by never importing ``ee`` at module top level; every
handler imports it lazily inside the function so the module compiles and loads on a
machine without earthengine-api installed.

The load trick: an ee.Image is turned into an XYZ tile URL via Earth Engine, then
added to QGIS as an XYZ raster layer (same path add_xyz_layer uses). The layer
renders in QGIS and survives in a saved .qgs project, until the EE session token in
the tile URL expires, after which the tiles stop loading and the dataset must be
re-added.

The catalog search (search_gee_catalog) is answered by the server: the community
catalog is tens of megabytes and every session used to download it to match a
keyword. The tools here are the ones that need the ee library on this machine.
"""
from datetime import datetime, timezone  # noqa: E402

from qgis.core import QgsProject, QgsRasterLayer  # noqa: E402

from ..core.policy import ToolPolicyGroup  # noqa: E402
from ..core.provider_uri import encode_uri_url  # noqa: E402
from ..core.tool_registry import Tool, ToolRegistry  # noqa: E402



_EE_LAYERS: dict[str, dict] = {}



_INDEX_BANDS = {
    "NDVI": ("nir_band", "red_band"),
    "NDWI": ("green_band", "nir_band"),
    "NDBI": ("swir_band", "nir_band"),
    "NBR": ("nir_band", "swir2_band"),
}

_EE_NOT_AUTHENTICATED_SUGGESTION = (
    "Run `earthengine authenticate` in a terminal and ensure your Google account is "
    "registered for Earth Engine and (if using a Cloud project) that the Earth Engine "
    "API is enabled. Then call initialize_earth_engine with the project id."
)


def register_earthengine_tools(registry: ToolRegistry):
    import importlib.util
    if importlib.util.find_spec("ee") is None:
        return

    registry.register(Tool(
        name="initialize_earth_engine",
        input_schema={
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                },
            },
            "required": [],
        },
        handler=_initialize_earth_engine,
        policy_group=ToolPolicyGroup.READ,
        background=True,
    ))

    registry.register(Tool(
        name="add_gee_dataset",
        input_schema={
            "type": "object",
            "properties": {
                "ee_id": {
                    "type": "string",
                },
                "name": {"type": "string"},
                "vis_params": {
                    "type": "object",
                },
                "date_start": {"type": "string"},
                "date_end": {"type": "string"},
                "reducer": {
                    "type": "string",
                    "enum": ["median", "mean", "mosaic", "min", "max"],
                },
                "bbox": {
                    "type": "object",
                    "properties": {
                        "xmin": {"type": "number"},
                        "ymin": {"type": "number"},
                        "xmax": {"type": "number"},
                        "ymax": {"type": "number"},
                    },



                    "required": ["xmin", "ymin", "xmax", "ymax"],
                },
            },
            "required": ["ee_id"],
        },
        handler=_add_gee_dataset,
        policy_group=ToolPolicyGroup.NETWORK_IMPORT,



        background=True,
    ))

    registry.register(Tool(
        name="gee_compute_index",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {
                    "type": "string",
                },
                "ee_id": {
                    "type": "string",
                },
                "index": {
                    "type": "string",
                    "enum": ["NDVI", "NDWI", "NDBI", "NBR"],
                },
                "nir_band": {"type": "string"},
                "red_band": {"type": "string"},
                "green_band": {"type": "string"},
                "swir_band": {"type": "string"},
                "swir2_band": {"type": "string"},
                "name": {"type": "string"},
                "vis_params": {
                    "type": "object",
                },
            },
            "required": ["index"],
        },
        handler=_gee_compute_index,
        policy_group=ToolPolicyGroup.NETWORK_IMPORT,

        background=True,
    ))

    registry.register(Tool(
        name="gee_zonal_stats",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {
                    "type": "string",
                },
                "ee_id": {
                    "type": "string",
                },
                "bbox": {
                    "type": "object",
                    "properties": {
                        "xmin": {"type": "number"},
                        "ymin": {"type": "number"},
                        "xmax": {"type": "number"},
                        "ymax": {"type": "number"},
                    },



                    "required": ["xmin", "ymin", "xmax", "ymax"],
                },
                "reducer": {
                    "type": "string",
                    "enum": ["mean", "median", "min", "max", "sum", "stdDev"],
                },
                "scale": {"type": "integer", "minimum": 1, "maximum": 1000000},
                "band": {"type": "string"},
            },
            "required": [],
        },
        handler=_gee_zonal_stats,
        policy_group=ToolPolicyGroup.READ,
        background=True,
    ))





def _ensure_initialized(project: str | None = None):
    """Import ee and make sure a session is live."""





    try:
        import ee
    except Exception as exc:
        return None, {
            "_error": f"earthengine-api is not importable: {exc}",
            "_code": "PERMISSION_DENIED",
            "_suggestion": _EE_NOT_AUTHENTICATED_SUGGESTION,
        }
    try:
        if project:
            ee.Initialize(project=project)
        else:
            ee.Initialize()
    except Exception as exc:
        return None, {
            "_error": f"Earth Engine could not initialize: {exc}",
            "_code": "PERMISSION_DENIED",
            "_suggestion": _EE_NOT_AUTHENTICATED_SUGGESTION,
        }
    return ee, None


def _initialize_earth_engine(args: dict) -> dict:
    project = args.get("project")
    ee, err = _ensure_initialized(project)
    if err:
        return err
    return {"initialized": True, "project": project}


def _today_iso() -> str:
    """Today in UTC as YYYY-MM-DD, the open end of a half-given date range."""
    return datetime.now(timezone.utc).date().isoformat()





def _add_ee_image_layer(image, vis_params: dict, name: str, ee_id: str) -> dict:
    """Turn an ee.Image into an XYZ tile URL and add it as a QGIS raster layer."""






    try:
        mapid = image.getMapId(vis_params or {})
        tile_url = mapid["tile_fetcher"].url_format
    except Exception as exc:
        return {
            "_error": f"Earth Engine could not build map tiles for '{ee_id}': {exc}",
            "_code": "EE_MAPID_FAILED",
            "_suggestion": (
                "Check the vis_params (min/max/bands) match the image's bands, and that "
                "the asset id is correct."
            ),
        }

    uri = f"type=xyz&url={encode_uri_url(tile_url)}&zmax=24&zmin=0"

    def _create():
        layer = QgsRasterLayer(uri, name, "wms")
        if not layer.isValid():
            return None
        QgsProject.instance().addMapLayer(layer)
        return {"layer_name": layer.name(), "layer_id": layer.id()}

    from .data_tools import _run_on_main_thread

    created = _run_on_main_thread(_create, timeout=30)
    if created is None:
        return {"_error": f"Failed to create XYZ layer from Earth Engine tiles for '{ee_id}'", "_code": "LAYER_INVALID"}

    _EE_LAYERS[created["layer_id"]] = {"image": image, "vis": vis_params or {}, "ee_id": ee_id}
    return created





def _add_gee_dataset(args: dict) -> dict:
    ee_id = (args.get("ee_id") or "").strip()
    if not ee_id:
        return {"_error": "ee_id is required", "_code": "MISSING_EE_ID"}

    ee, err = _ensure_initialized()
    if err:
        return err

    name = args.get("name") or ee_id
    vis_params = args.get("vis_params") or {}
    date_start = args.get("date_start")
    date_end = args.get("date_end")
    reducer = args.get("reducer", "median")
    bbox = args.get("bbox")

    reducer_used = None
    image = None


    collection = None
    try:
        collection = ee.ImageCollection(ee_id)


        collection.size().getInfo()
    except Exception:
        collection = None

    if collection is not None:
        try:




            if date_start or date_end:
                collection = collection.filterDate(date_start or "1970-01-01",
                                                   date_end or _today_iso())
            if bbox:
                region = ee.Geometry.Rectangle(
                    [bbox["xmin"], bbox["ymin"], bbox["xmax"], bbox["ymax"]]
                )
                collection = collection.filterBounds(region)
            reducer_map = {
                "median": collection.median,
                "mean": collection.mean,
                "mosaic": collection.mosaic,
                "min": collection.min,
                "max": collection.max,
            }
            reduce_fn = reducer_map.get(reducer, collection.median)
            image = reduce_fn()
            reducer_used = reducer if reducer in reducer_map else "median"
        except Exception as exc:
            return {
                "_error": f"Failed to reduce ImageCollection '{ee_id}': {exc}",
                "_code": "EE_COLLECTION_FAILED",
                "_suggestion": "Check the date range and bbox, or pass a single-Image asset id.",
            }
    else:
        try:
            image = ee.Image(ee_id)
        except Exception as exc:
            return {
                "_error": f"'{ee_id}' is neither a usable ImageCollection nor Image: {exc}",
                "_code": "INVALID_ARGS",
                "_suggestion": "Verify the asset id with search_gee_catalog.",
            }

    added = _add_ee_image_layer(image, vis_params, name, ee_id)
    if "_error" in added:
        return added

    result = {
        "layer_name": added["layer_name"],
        "layer_id": added["layer_id"],
        "ee_id": ee_id,
        "vis_params_used": vis_params,
    }
    if reducer_used is not None:
        result["reducer_used"] = reducer_used
    return result





def _resolve_source_image(args: dict, ee):
    """Resolve the source ee.Image from a cached layer or a fresh asset id."""



    layer_name = args.get("layer_name")
    ee_id = args.get("ee_id")

    if layer_name:
        from .core_tools import _find_layer
        from .data_tools import _run_on_main_thread


        layer = _run_on_main_thread(_find_layer, layer_name)
        if layer is None:
            return None, {"_error": f"Layer '{layer_name}' not found or ambiguous", "_code": "LAYER_NOT_FOUND"}
        cached = _EE_LAYERS.get(_run_on_main_thread(layer.id))
        if cached is None:
            return None, {
                "_error": f"Layer '{layer_name}' is not an Earth Engine layer added in this session",
                "_code": "INVALID_ARGS",
                "_suggestion": "Pass ee_id to load the asset directly, or add it first with add_gee_dataset.",
            }
        return cached["image"], None

    if ee_id:
        try:
            return ee.Image(ee_id), None
        except Exception as exc:
            return None, {"_error": f"Could not load Image '{ee_id}': {exc}", "_code": "INVALID_ARGS"}

    return None, {"_error": "Provide either layer_name or ee_id", "_code": "INVALID_ARGS"}


def _gee_compute_index(args: dict) -> dict:
    index = (args.get("index") or "").strip().upper()
    if index not in _INDEX_BANDS:
        return {"_error": f"Unknown index '{index}'. Use one of NDVI, NDWI, NDBI, NBR.", "_code": "INVALID_ARGS"}

    ee, err = _ensure_initialized()
    if err:
        return err

    image, src_err = _resolve_source_image(args, ee)
    if src_err:
        return src_err


    defaults = {
        "nir_band": "B8",
        "red_band": "B4",
        "green_band": "B3",
        "swir_band": "B11",
        "swir2_band": "B12",
    }
    band_a_key, band_b_key = _INDEX_BANDS[index]
    band_a = args.get(band_a_key) or defaults[band_a_key]
    band_b = args.get(band_b_key) or defaults[band_b_key]

    try:
        available = image.bandNames().getInfo()
    except Exception:
        available = None

    if available is not None:
        missing = [b for b in (band_a, band_b) if b not in available]
        if missing:
            return {
                "_error": f"Bands {missing} not found for {index}. Available bands: {available}",
                "_code": "INVALID_ARGS",
                "_suggestion": "Pass the matching band names via nir_band/red_band/green_band/swir_band/swir2_band.",
            }

    try:
        index_image = image.normalizedDifference([band_a, band_b]).rename(index)
    except Exception as exc:
        return {"_error": f"Failed to compute {index}: {exc}", "_code": "EE_INDEX_FAILED"}

    vis_params = args.get("vis_params") or {
        "min": -1,
        "max": 1,
        "palette": ["blue", "white", "green"],
    }
    name = args.get("name") or index

    added = _add_ee_image_layer(index_image, vis_params, name, f"{index} (computed)")
    if "_error" in added:
        return added

    return {
        "layer_name": added["layer_name"],
        "layer_id": added["layer_id"],
        "index": index,
        "bands_used": [band_a, band_b],
        "vis_params_used": vis_params,
    }





class _BadBbox(ValueError):
    """A bbox the caller wrote that cannot be read; never silently replaced."""


def _bbox_to_wsen(args: dict) -> tuple | None:
    """Return (w, s, e, n) in EPSG:4326 from the bbox arg or the canvas extent."""





    bbox = args.get("bbox")
    if bbox not in (None, {}, ""):
        if not isinstance(bbox, dict):
            raise _BadBbox("bbox must be an object {xmin, ymin, xmax, ymax} in EPSG:4326")
        corners = ("xmin", "ymin", "xmax", "ymax")
        missing = [k for k in corners if k not in bbox]
        if missing:
            raise _BadBbox(f"bbox is missing {missing}; it needs all four of {list(corners)}")
        try:
            w, s, e, n = (float(bbox[k]) for k in corners)
        except (TypeError, ValueError):
            raise _BadBbox("bbox corners must be numbers in EPSG:4326 degrees") from None
        if w >= e or s >= n:
            raise _BadBbox("bbox is empty: xmin must be < xmax and ymin < ymax")
        return w, s, e, n
    from .data_tools import _canvas_viewbox_4326, _run_on_main_thread

    viewbox = _run_on_main_thread(_canvas_viewbox_4326)
    if not viewbox:
        return None
    try:
        w, s, e, n = (float(v) for v in viewbox.split(","))
        return w, s, e, n
    except (ValueError, TypeError):
        return None


def _gee_zonal_stats(args: dict) -> dict:
    ee, err = _ensure_initialized()
    if err:
        return err

    image, src_err = _resolve_source_image(args, ee)
    if src_err:
        return src_err

    try:
        wsen = _bbox_to_wsen(args)
    except _BadBbox as bad:
        return {
            "_error": str(bad),
            "_code": "INVALID_ARGS",
            "_suggestion": "Pass bbox as {xmin, ymin, xmax, ymax} in EPSG:4326 degrees, "
                           "or omit it entirely to use the current canvas extent.",
        }
    if wsen is None:
        return {
            "_error": "No region given and the current canvas extent could not be read",
            "_code": "INVALID_ARGS",
            "_suggestion": "Pass a bbox {xmin,ymin,xmax,ymax} in EPSG:4326.",
        }
    w, s, e, n = wsen

    reducer_name = args.get("reducer", "mean")
    scale = int(args.get("scale", 30))
    band = args.get("band")

    reducer_map = {
        "mean": ee.Reducer.mean,
        "median": ee.Reducer.median,
        "min": ee.Reducer.min,
        "max": ee.Reducer.max,
        "sum": ee.Reducer.sum,
        "stdDev": ee.Reducer.stdDev,
    }
    if reducer_name not in reducer_map:
        return {"_error": f"Unknown reducer '{reducer_name}'", "_code": "INVALID_ARGS"}

    try:
        region = ee.Geometry.Rectangle([w, s, e, n])
        target = image.select(band) if band else image
        stats = target.reduceRegion(
            reducer=reducer_map[reducer_name](),
            geometry=region,
            scale=scale,
            maxPixels=1e9,
        ).getInfo()
    except Exception as exc:
        return {
            "_error": f"Earth Engine reduceRegion failed: {exc}",
            "_code": "EE_STATS_FAILED",
            "_suggestion": "Shrink the bbox or raise the scale (meters per pixel); large regions can time out.",
        }

    return {
        "stats": stats,
        "reducer": reducer_name,
        "scale": scale,
        "band": band,
        "region_4326": {"xmin": w, "ymin": s, "xmax": e, "ymax": n},
    }
