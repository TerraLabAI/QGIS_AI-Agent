# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

















from __future__ import annotations

from datetime import datetime, timedelta, timezone

from qgis.core import QgsProject, QgsRasterLayer
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core import limits
from ..core.provider_uri import encode_uri_url
from ..core.tool_registry import Tool, ToolRegistry



_EE_LAYERS: dict[str, dict] = {}



_INDEX_BANDS = {
    "NDVI": ("nir_band", "red_band"),
    "NDWI": ("green_band", "nir_band"),
    "NDBI": ("swir_band", "nir_band"),
    "NBR": ("nir_band", "swir2_band"),
}

_EE_NOT_AUTHENTICATED_SUGGESTION = (
    "`earthengine authenticate` in a terminal, a Google account registered for Earth "
    "Engine, and (for a Cloud project) the Earth Engine API enabled are needed; "
    "initialize_earth_engine with the project id follows."
)


def register_earthengine_tools(registry: ToolRegistry):
    import importlib.util
    if importlib.util.find_spec("ee") is None:
        return

    registry.register(Tool(
        name="initialize_earth_engine",
        danger="read",
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
        background=True,
    ))

    registry.register(Tool(
        name="add_gee_dataset",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Add the Earth Engine dataset[ {name}]"),
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



        background=True,
    ))

    registry.register(Tool(
        name="gee_compute_index",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Compute an index with Earth Engine"),
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

        background=True,
    ))

    registry.register(Tool(
        name="gee_zonal_stats",
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Zonal statistics with Earth Engine"),
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
        background=True,
    ))





def _ensure_initialized(project: str | None = None):






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

    return datetime.now(timezone.utc).date().isoformat()





def _add_ee_image_layer(image, vis_params: dict, name: str, ee_id: str) -> dict:







    try:
        mapid = image.getMapId(vis_params or {})
        tile_url = mapid["tile_fetcher"].url_format
    except Exception as exc:
        return {
            "_error": f"Earth Engine could not build map tiles for '{ee_id}': {exc}",
            "_code": "EE_MAPID_FAILED",
            "_suggestion": (
                "vis_params (min/max/bands) may not match the image's bands, or the asset "
                "id is wrong."
            ),
        }

    uri = f"type=xyz&url={encode_uri_url(tile_url)}&zmax=24&zmin=0"

    def _create():
        layer = QgsRasterLayer(uri, name, "wms")
        if not layer.isValid():
            return None
        QgsProject.instance().addMapLayer(layer)
        return {"layer_name": layer.name(), "layer_id": layer.id()}

    from ..core.background import run_on_main_thread

    created = run_on_main_thread(_create, timeout=30)
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
                "_suggestion": "The date range, bbox or a single-Image asset id may be why.",
            }
    else:
        try:
            image = ee.Image(ee_id)
        except Exception as exc:
            return {
                "_error": f"'{ee_id}' is neither a usable ImageCollection nor Image: {exc}",
                "_code": "INVALID_ARGS",
                "_suggestion": "search_gee_catalog lists valid asset ids.",
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
    acquired = _acquired(ee, image, collection is not None, date_start, date_end)
    if acquired:

        result["data_date"] = acquired
    return result


def _acquired(ee, image, reduced: bool, date_start, date_end) -> str:







    from ..core.data_date import from_range

    try:
        if reduced:
            if not date_start:
                return ""
            end = str(date_end or _today_iso())[:10]
            try:
                last = (datetime.fromisoformat(end) - timedelta(days=1)).date().isoformat()
            except ValueError:
                last = end
            return from_range(date_start, max(last, str(date_start)[:10]))
        return from_range(image.date().format("YYYY-MM-dd").getInfo())
    except Exception:  # noqa: BLE001
        return ""





def _resolve_source_image(args: dict, ee):




    layer_name = args.get("layer_name")
    ee_id = args.get("ee_id")

    if layer_name:
        from ..core.background import run_on_main_thread
        from .layer_lookup import _find_layer


        layer = run_on_main_thread(_find_layer, layer_name)
        if layer is None:
            return None, {"_error": f"Layer '{layer_name}' not found or ambiguous", "_code": "LAYER_NOT_FOUND"}
        cached = _EE_LAYERS.get(run_on_main_thread(layer.id))
        if cached is None:
            return None, {
                "_error": f"Layer '{layer_name}' is not an Earth Engine layer added in this session",
                "_code": "INVALID_ARGS",
                "_suggestion": "ee_id loads the asset directly; add_gee_dataset adds it as a layer first.",
            }
        return cached["image"], None

    if ee_id:
        try:
            return ee.Image(ee_id), None
        except Exception as exc:
            return None, {"_error": f"Could not load Image '{ee_id}': {exc}", "_code": "INVALID_ARGS"}

    return None, {"_error": "layer_name or ee_id is required", "_code": "INVALID_ARGS"}


def _gee_compute_index(args: dict) -> dict:
    index = (args.get("index") or "").strip().upper()
    if index not in _INDEX_BANDS:
        return {"_error": f"Unknown index '{index}'. Valid: NDVI, NDWI, NDBI, NBR.", "_code": "INVALID_ARGS"}

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
                "_suggestion": "nir_band/red_band/green_band/swir_band/swir2_band set the matching bands.",
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
    pass


def _bbox_to_wsen(args: dict) -> tuple | None:






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
    from ..core.background import run_on_main_thread
    from .data_tools import _canvas_viewbox_4326

    viewbox = run_on_main_thread(_canvas_viewbox_4326)
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
            "_suggestion": "bbox is {xmin, ymin, xmax, ymax} in EPSG:4326 degrees; omitted, "
                           "the canvas extent is used.",
        }
    if wsen is None:
        return {
            "_error": "No region given and the current canvas extent could not be read",
            "_code": "INVALID_ARGS",
            "_suggestion": "bbox is {xmin,ymin,xmax,ymax} in EPSG:4326.",
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
            maxPixels=int(limits.current("GEE_MAX_PIXELS")),
        ).getInfo()
    except Exception as exc:
        return {
            "_error": f"Earth Engine reduceRegion failed: {exc}",
            "_code": "EE_STATS_FAILED",
            "_suggestion": "A smaller bbox or larger scale (meters per pixel) avoids timeout on large regions.",
        }

    return {
        "stats": stats,
        "reducer": reducer_name,
        "scale": scale,
        "band": band,
        "region_4326": {"xmin": w, "ymin": s, "xmax": e, "ymax": n},
    }
