# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
from __future__ import annotations

"""STAC, Cloud-Optimized GeoTIFF, and PMTiles tools.

Add a STAC item or a Cloud-Optimized GeoTIFF straight from a URL via GDAL's
/vsicurl/ driver, and add a PMTiles archive as an extract or a vector tile
layer. Zero external dependencies, plain urllib for HTTP, json for parsing,
and PyQGIS for the layers. Searching a catalog (list_stac_collections,
search_stac_items) is answered by the server; what stays here is what needs
GDAL, the project and the signing of an asset address.

Three archives want something before they hand over a byte. Planetary Computer
signs a blob URL from an open endpoint, so the plugin asks for that itself.
Copernicus and the protected NASA Earthdata hosts want a credential, and a
credential is what a plugin on a user's machine must never hold: for those the
plugin asks its own backend for a short-lived address of ours and hands that to
GDAL. No key of either provider is ever in the plugin, in a QGIS project file,
or on this machine.
"""
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
import urllib.error  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402

from qgis.core import (  # noqa: E402
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsVectorTileLayer,
)

from ..core import dataset_docs, limits, net, tuning, vsi  # noqa: E402
from ..core.logger import log_warning  # noqa: E402
from ..core.policy import create_managed_temp_dir  # noqa: E402
from ..core.provider_uri import encode_uri_url  # noqa: E402
from ..core.tool_registry import Tool, ToolRegistry  # noqa: E402
from .data_tools import _avoid_reserved_name, _run_on_main_thread  # noqa: E402





_USER_AGENT = net.user_agent()

_DEFAULT_STAC_ROOT = "https://planetarycomputer.microsoft.com/api/stac/v1"
_PC_SIGN_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/sign"

_STAC_GET_TIMEOUT = 20
_SIGN_TIMEOUT = 15
_MAX_RESPONSE_SIZE = 8 * 1024 * 1024
_TOTAL_TIMEOUT_FACTOR = 3
_CACHE_STAC_S = 600


_RASTER_MEDIA_HINTS = ("geotiff", "image/tiff")






_BROWSE_ASSET_KEYS = ("rendered_preview", "preview", "thumbnail", "overview")


_COG_PROFILE_HINT = "cloud-optimized"





_PMTILES_MAGIC = b"PMTiles"
_PMTILES_VERSION = 3
_PMTILES_PROBE_BYTES = 16 * 1024
_PMTILES_PROBE_TIMEOUT = 20

_PMTILES_BUILD_TIMEOUT = 20





_PMTILES_MAX_AREA_KM2 = 400.0



_PMTILES_EXTRACT_ZOOM = 14


_PMTILES_ADD_TIMEOUT = 30


def register_stac_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="add_cog_layer",
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "name": {"type": "string"},
            },
            "required": ["url"],
        },
        handler=_add_cog_layer,
        background=True,
    ))

    registry.register(Tool(
        name="add_stac_layer",
        input_schema={
            "type": "object",
            "properties": {
                "item_url": {"type": "string"},
                "stac_url": {"type": "string"},
                "collection": {"type": "string"},
                "item_id": {"type": "string"},
                "asset": {
                    "type": "string",
                },
                "name": {"type": "string"},
            },
            "required": [],
        },
        handler=_add_stac_layer,
        background=True,
    ))

    registry.register(Tool(
        name="add_pmtiles_layer",
        input_schema={
            "type": "object",
            "properties": {
                "confirm_area_km2": {"type": "number", "minimum": 0},
                "confirm_large": {"type": "boolean"},
                "url": {"type": "string"},
                "name": {"type": "string"},
                "layer": {"type": "string"},
                "bbox": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 4,
                    "maxItems": 4,
                },
                "mode": {
                    "type": "string",
                    "enum": ["extract", "tiles"],
                },
            },
            "required": ["url"],
        },
        handler=_add_pmtiles_layer,
        background=True,
    ))




def _stac_get(url: str, timeout: int = _STAC_GET_TIMEOUT):
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise ValueError("STAC URL must use http or https")
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )



    ttl = 0.0 if _pc_sign_url() in url else _CACHE_STAC_S
    raw = net.fetch(req, timeout=timeout, max_bytes=_MAX_RESPONSE_SIZE,
                    total_timeout=timeout * _TOTAL_TIMEOUT_FACTOR, cache_ttl=ttl).body
    return json.loads(raw)









def _pc_sign_url() -> str:
    return tuning.service_url("pc_sign", _PC_SIGN_URL)


def _earthdata_hosts() -> tuple:
    return tuple(tuning.service_list("earthdata_hosts", _EARTHDATA_HOSTS))


def _earthdata_suffixes() -> tuple:
    return tuple(tuning.service_list("earthdata_suffixes", _EARTHDATA_SUFFIXES))


def _cdse_https_prefix() -> str:
    return tuning.service_url("cdse_https_prefix", _CDSE_HTTPS_PREFIX)


def _stac_root(args: dict) -> str:
    return (args.get("stac_url") or tuning.service_url("stac_root", _DEFAULT_STAC_ROOT)).rstrip("/")


def _canvas_bbox_4326() -> list | None:
    """Current canvas extent as [west, south, east, north] in EPSG:4326."""




    try:
        from qgis.utils import iface as qgis_iface
        canvas = qgis_iface.mapCanvas()
        extent = canvas.extent()
        canvas_crs = canvas.mapSettings().destinationCrs()
        if canvas_crs.authid() != "EPSG:4326":
            xform = QgsCoordinateTransform(
                canvas_crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance()
            )
            extent = xform.transformBoundingBox(extent)
        w, e = extent.xMinimum(), extent.xMaximum()
        s, n = extent.yMinimum(), extent.yMaximum()
        if abs(e - w) > 0.0001 and abs(n - s) > 0.0001:
            return [round(w, 6), round(s, 6), round(e, 6), round(n, 6)]
    except Exception:  # nosec B110 - canvas transform is optional
        pass
    return None




def _has_sas_token(url: str) -> bool:
    """True when the query really carries a shared access signature."""







    query = urllib.parse.urlsplit(url or "").query
    if not query:
        return False
    keys = {k.lower() for k in urllib.parse.parse_qs(query, keep_blank_values=True)}
    return "sig" in keys


_PC_BLOB_HOST = "blob.core.windows.net"




_PUBLIC_BLOB_ACCOUNTS = ("stterralabopendata",)


def _sign_pc_href(url: str) -> tuple[str, str | None]:
    """Sign a Planetary Computer blob URL."""







    host = (urllib.parse.urlsplit(url or "").hostname or "").lower().rstrip(".")
    if not (host == _PC_BLOB_HOST or host.endswith("." + _PC_BLOB_HOST)) or _has_sas_token(url):
        return url, None
    if host.split(".", 1)[0] in _PUBLIC_BLOB_ACCOUNTS:
        return url, None
    try:
        sign_url = f"{_pc_sign_url()}?href={urllib.parse.quote(url, safe='')}"
        payload = _stac_get(sign_url, timeout=_SIGN_TIMEOUT)
        signed = payload.get("href")
        if signed:
            return signed, None
        return url, "SAS signing failed, layer may not load"
    except (urllib.error.URLError, OSError, ValueError):
        return url, "SAS signing failed, layer may not load"


_CDSE_S3_PREFIX = "s3://eodata/"
_CDSE_HTTPS_PREFIX = "https://eodata.dataspace.copernicus.eu/"



_EARTHDATA_HOSTS = ("data.lpdaac.earthdatacloud.nasa.gov", "e4ftl01.cr.usgs.gov")
_EARTHDATA_SUFFIXES = (".earthdata.nasa.gov", ".earthdatacloud.nasa.gov")


def _needs_backend_signing(url: str) -> bool:
    """True for an address only our server holds the credential for."""
    lowered = (url or "").strip()
    if lowered.startswith(_CDSE_S3_PREFIX) or lowered.startswith(_cdse_https_prefix()):
        return True
    parts = urllib.parse.urlsplit(lowered)
    if parts.scheme != "https":
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    return host in _earthdata_hosts() or host.endswith(_earthdata_suffixes())


def _backend_sign_url() -> str:
    """The /eodata/sign address of whichever backend this plugin is paired with."""
    from ..core.settings import Settings

    parts = urllib.parse.urlsplit(Settings().server_url)
    scheme = "http" if parts.scheme == "ws" else "https"
    return f"{scheme}://{parts.netloc}/eodata/sign"


def _sign_backend_href(url: str) -> tuple[str, str | None]:
    """Ask the backend for a short-lived address for a Copernicus or Earthdata asset."""






    from ..core.settings import Settings

    key = Settings().activation_key
    if not key:
        return url, "This asset needs a signed address; sign in to AI Agent first"
    try:
        request = urllib.request.Request(
            f"{_backend_sign_url()}?href={urllib.parse.quote(url, safe='')}",
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json",
                     "Authorization": f"Bearer {key}"},
        )
        raw = net.fetch(request, timeout=_SIGN_TIMEOUT, max_bytes=_MAX_RESPONSE_SIZE,
                        total_timeout=_SIGN_TIMEOUT * _TOTAL_TIMEOUT_FACTOR).body
        signed = (json.loads(raw) or {}).get("href")
        if signed:
            return signed, None
        return url, "Signing this asset address failed, the layer may not load"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log_warning(f"eodata signing failed: {exc}")
        return url, "Signing this asset address failed, the layer may not load"


def _public_s3_https(url: str) -> str:
    """A public AWS bucket's s3:// href as the https address GDAL can open."""











    if not url.startswith("s3://") or url.startswith(_CDSE_S3_PREFIX):
        return url
    bucket, _, key = url[len("s3://"):].partition("/")
    if not bucket or not key:
        return url
    return f"https://{bucket}.s3.amazonaws.com/{urllib.parse.quote(key)}"


def _signed_href(url: str) -> tuple[str, str | None]:
    """The address GDAL should be given, and a note when signing did not work."""
    if _needs_backend_signing(url):
        return _sign_backend_href(url)
    return _sign_pc_href(_public_s3_https(url))


def _prefetch_vsicurl(url: str) -> None:
    """Read the remote header through GDAL before the layer is built."""








    try:
        from osgeo import gdal
    except Exception:  # noqa: BLE001 - no osgeo, let the layer do the fetch
        return



    vsi.apply_persistent()





    try:
        net.check_url(url)
    except Exception as exc:  # noqa: BLE001 - refused, not an error worth a traceback
        log_warning(f"COG prefetch refused: {exc}")
        return
    try:
        dataset = gdal.Open(f"/vsicurl/{url}")
        del dataset
    except Exception:  # nosec B110 - a prefetch that fails costs nothing
        pass


def _add_cog(final_url: str, name: str | None, note: str | None = None, extra: dict | None = None) -> dict:






    if final_url.startswith("s3://"):
        return {"_error": note or ("This s3:// address was not opened: it needs a signature this server "
                                   "did not provide, or its bucket is not public."),
                "_code": "PERMISSION_DENIED",
                "_suggestion": "Take the same scene from an open catalog instead: search_stac_items on "
                               "the Planetary Computer serves Sentinel-2 without a signature.",
                "url": final_url}



    try:
        net.check_url(final_url)
    except Exception as exc:  # noqa: BLE001 - the reason is the answer
        return {"_error": f"This asset address is not fetched: {exc}", "_code": "PERMISSION_DENIED",
                "_suggestion": "Use a public https address for the asset.", "url": final_url}
    _prefetch_vsicurl(final_url)

    def _create():
        layer = QgsRasterLayer(f"/vsicurl/{final_url}", name or "COG", "gdal")
        if not layer.isValid():
            return {
                "_error": (
                    "Could not open the raster. The URL may not be a valid Cloud-Optimized GeoTIFF, "
                    "or it may be private (needs a SAS token / auth) or unreachable."
                ),
                "_code": "INVALID_ARGS",









                "_suggestion": (
                    'Call add_data again with kind "raster": a plain TIFF, or a server that ignores '
                    "range requests, has to be downloaded rather than streamed."
                ),
                "url": final_url,
            }
        normalised = normalise_crs(layer)
        QgsProject.instance().addMapLayer(layer)
        return {
            "layer_name": layer.name(),
            "layer_id": layer.id(),
            "url": final_url,
            **({"crs_note": f"The file names no CRS authority; read as {normalised}."} if normalised else {}),
            "width": layer.width(),
            "height": layer.height(),
            "bands": layer.bandCount(),
            "crs": _crs_label(layer.crs()),
        }

    result = _run_on_main_thread(_create, timeout=60)
    if result.get("_error"):
        return result
    if note:
        result["_note"] = note
    if extra:
        result.update(extra)
    if not result.get("crs"):





        result["warning"] = ("This raster carries no CRS, so QGIS cannot place it on the map. It is a picture "
                             "of the scene (a browse image), not georeferenced data.")
        result["suggestion"] = ("Add a cloud-optimized GeoTIFF asset of the same item instead: pass its key as "
                                "asset to add_stac_layer, or its href to add_cog_layer.")
    return result


def normalise_crs(layer) -> str:
    """Give a raster whose CRS is valid but names no authority the matching known CRS, so every later tool sees an authid instead of ""."""








    try:
        from qgis.core import QgsCoordinateReferenceSystem
        crs = layer.crs()
        if not crs.isValid() or crs.authid():
            return ""
        candidate = None
        name = (crs.description() or "").strip()
        if re.fullmatch(r"(?i)EPSG:\d{4,6}", name):
            candidate = QgsCoordinateReferenceSystem(name.upper())
        if candidate is None or not candidate.isValid():
            srsid = int(crs.findMatchingProj() or 0)
            candidate = QgsCoordinateReferenceSystem.fromSrsId(srsid) if srsid else None
        if candidate is None or not candidate.isValid() or not candidate.authid():
            return ""
        layer.setCrs(candidate)
        return candidate.authid()
    except Exception:  # noqa: BLE001 - a normalisation never fails the load
        return ""


def _crs_label(crs) -> str:
    """The layer's CRS as the model should read it: the authority id when there is one, otherwise the projection's own name."""




    try:
        if not getattr(crs, "isValid", lambda: True)():
            return ""
        authid = crs.authid()
        if authid:
            return authid


        return getattr(crs, "description", lambda: "")() or getattr(crs, "toProj", lambda: "")()[:80]
    except Exception:  # noqa: BLE001 - a label never fails the load
        return ""


def _add_cog_layer(args: dict) -> dict:
    url = args.get("url")
    if not url:
        return {"_error": "url is required"}
    name = args.get("name")
    final_url, note = _signed_href(url)
    result = _add_cog(final_url, name, note=note)


    dataset_docs.attach(result, url)
    return result


def _is_raster_asset(asset: object) -> bool:
    """True when the asset declares a GeoTIFF media type, so it carries a CRS."""
    if not isinstance(asset, dict) or not asset.get("href"):
        return False
    media = (asset.get("type") or "").lower()
    return any(hint in media for hint in _RASTER_MEDIA_HINTS)


def _pick_asset_href(assets: dict, asset_key: str | None) -> tuple[str | None, str | None, str | None, str | None]:
    """Return (href, chosen_key, note, error): the data first, a picture of it last."""








    if not assets:
        return None, None, None, "The STAC item has no assets."

    if asset_key:
        asset = assets.get(asset_key)
        if not asset or not asset.get("href"):
            return None, None, None, f"Asset '{asset_key}' not found. Available: {', '.join(assets.keys())}"
        return asset["href"], asset_key, None, None

    visual = assets.get("visual")
    if _is_raster_asset(visual):
        return visual["href"], "visual", None, None

    rasters = [key for key, asset in assets.items() if _is_raster_asset(asset)]
    cogs = [key for key in rasters if _COG_PROFILE_HINT in (assets[key].get("type") or "").lower()]
    ranked = cogs or rasters
    if ranked:
        key, others = ranked[0], ranked[1:]
        note = None
        if not cogs:




            note = (f"Added the '{key}' asset. No asset of this item is tagged cloud-optimized, so it may be "
                    f"read whole rather than by area: expect tens of seconds, and say so before waiting.")
        elif others:




            note = (f"Added the '{key}' asset. This item has other GeoTIFF assets "
                    f"({', '.join(others[:8])}); pass one as asset= to add it instead.")
        return assets[key]["href"], key, note, None

    if isinstance(visual, dict) and visual.get("href"):
        return visual["href"], "visual", None, None

    for key in _BROWSE_ASSET_KEYS:
        asset = assets.get(key)
        if isinstance(asset, dict) and asset.get("href"):
            note = (f"This item has no GeoTIFF asset, so '{key}' was added: it is a browse image of the "
                    "scene, not georeferenced data.")
            return asset["href"], key, note, None

    return None, None, None, f"No display or GeoTIFF asset found. Available: {', '.join(assets.keys())}"


def _add_stac_layer(args: dict) -> dict:
    item_url = args.get("item_url")
    if not item_url:
        stac_url = _stac_root(args)
        collection = args.get("collection")
        item_id = args.get("item_id")
        if not (collection and item_id):
            return {"_error": "Provide item_url, or stac_url + collection + item_id."}


        item_url = (f"{stac_url}/collections/{urllib.parse.quote(collection, safe='')}"
                    f"/items/{urllib.parse.quote(item_id, safe='')}")

    try:
        item = _stac_get(item_url)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"_error": f"Failed to fetch STAC item: {e}", "item_url": item_url}

    assets = item.get("assets", {}) or {}
    href, chosen_key, choice_note, err = _pick_asset_href(assets, args.get("asset"))
    if err:
        return {"_error": err, "item_url": item_url}




    href = urllib.parse.urljoin(item_url, href)
    final_url, sign_note = _signed_href(href)
    note = " ".join(part for part in (choice_note, sign_note) if part) or None
    name = args.get("name") or item.get("id")
    extra = {
        "item_id": item.get("id"),
        "collection": item.get("collection"),
        "asset_key": chosen_key,
    }
    result = _add_cog(final_url, name, note=note, extra=extra)
    dataset_docs.attach(result, item_url)
    return result


def _probe_pmtiles(url: str) -> dict:
    """Learn everything a worker thread can learn about a remote archive."""
















    request = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Range": f"bytes=0-{_PMTILES_PROBE_BYTES - 1}"},
    )
    try:


        head = net.fetch(request, timeout=_PMTILES_PROBE_TIMEOUT, max_bytes=_PMTILES_PROBE_BYTES,
                         total_timeout=_PMTILES_PROBE_TIMEOUT * _TOTAL_TIMEOUT_FACTOR).body
    except net.FetchTooLarge:
        return {
            "_error": (f"The server ignored the byte range and began sending the whole archive at {url}. "
                       "A PMTiles archive is read in place over range requests, so this host cannot serve it."),
            "code": "INVALID_ARGS",
            "suggestion": "Ask for the same data from a host that answers range requests, or for an extract.",
        }
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {
            "_error": f"The PMTiles archive at {url} could not be read: {e}",
            "code": "EXECUTION_FAILED",
            "suggestion": "Check the URL, or use another source for the same data.",
        }

    if not head.startswith(_PMTILES_MAGIC):
        opening = head[:48].decode("utf-8", errors="replace").replace("\n", " ").strip()
        return {
            "_error": (f"{url} is not a PMTiles archive: it begins with '{opening}' instead of the PMTiles "
                       "magic bytes. A URL that answers with an error page reads exactly like this."),
            "code": "INVALID_ARGS",
            "suggestion": "Check the URL points at the .pmtiles file itself, or use another source.",
        }
    version = head[len(_PMTILES_MAGIC)] if len(head) > len(_PMTILES_MAGIC) else 0
    if version != _PMTILES_VERSION:
        return {
            "_error": (f"This archive is PMTiles version {version}; only version {_PMTILES_VERSION} can be read "
                       "in place."),
            "code": "INVALID_ARGS",
            "suggestion": "Ask for a version 3 archive, or for the data in another cloud-native format.",
        }
    return {}


def _prefetch_pmtiles(url: str) -> None:
    """Open the archive with GDAL from this thread so the build finds it cached."""








    try:
        from osgeo import gdal
    except Exception:  # noqa: BLE001 - no osgeo, let the layer do the fetch
        return
    vsi.apply_persistent()
    try:


        net.check_url(url)
    except Exception as exc:  # noqa: BLE001 - refused, not an error worth a traceback
        log_warning(f"PMTiles prefetch refused: {exc}")
        return
    try:
        dataset = gdal.OpenEx(f"/vsicurl/{url}", gdal.OF_VECTOR)
        del dataset
    except Exception:  # nosec B110 - a prefetch that fails costs nothing
        pass


def _parse_bbox(raw) -> list | None:
    """[west, south, east, north] in EPSG:4326 from a dict, a list or a comma string."""
    values = None
    if isinstance(raw, dict):
        try:
            values = [raw["xmin"], raw["ymin"], raw["xmax"], raw["ymax"]]
        except KeyError:
            keys = ("west", "south", "east", "north")
            if all(k in raw for k in keys):
                values = [raw[k] for k in keys]
    elif isinstance(raw, (list, tuple)) and len(raw) == 4:
        values = list(raw)
    elif isinstance(raw, str) and raw.count(",") == 3:
        values = raw.split(",")
    if values is None:
        return None
    try:
        west, south, east, north = (float(v) for v in values)
    except (TypeError, ValueError):
        return None
    if west > east:






        if (east + 360.0) - west < west - east:
            return None
        west, east = east, west
    if south > north:
        south, north = north, south
    if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0
            and -90.0 <= south <= 90.0 and -90.0 <= north <= 90.0):
        return None
    if east - west <= 0 or north - south <= 0:
        return None
    return [west, south, east, north]


def _bbox_4326(args: dict) -> tuple[list | None, str]:
    """The area to extract and where it came from, or (None, why not)."""





    for key in ("bbox", "extent", "area_of_interest", "aoi"):
        raw = args.get(key)
        if raw in (None, "", {}, []):
            continue
        parsed = _parse_bbox(raw)
        if parsed:
            return parsed, f"the {key} argument"
        return None, f"{key} was given but is not a bbox in EPSG:4326"
    try:
        canvas = _run_on_main_thread(_canvas_bbox_4326, timeout=10)
    except Exception:  # noqa: BLE001 - no canvas is a missing bbox, not a failure
        canvas = None
    if canvas:
        return canvas, "the current canvas extent"
    return None, "no bbox was given and the canvas extent could not be read"


def _bbox_area_km2(bbox: list) -> float:
    """Area of a lon/lat box in square kilometres, near enough to decide with."""






    west, south, east, north = bbox
    return limits.bbox_km2(south, west, north, east)


def _pmtiles_cancelled() -> bool:
    """Whether the Stop button was pressed on the task running this extract."""
    check = net.current_cancel_check()
    try:
        return bool(check and check())
    except Exception:  # noqa: BLE001 - a broken check is not a stop
        return False


def _pmtiles_stopped(url: str) -> dict:
    return {
        "_error": "Stopped before the extract finished.",
        "code": "CANCELLED",
        "suggestion": "Ask again for the same area when you want it.",
        "url": url,
    }


def _safe_file_stem(text: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text)).strip("_.")


    return _avoid_reserved_name(stem[:60] or "pmtiles")


def _extract_pmtiles(url: str, bbox: list, sublayer: str | None, name: str | None) -> dict:
    """Cut *bbox* out of the remote archive into a local GeoPackage."""












    try:
        from osgeo import gdal
    except ImportError:
        return {
            "_error": "GDAL's Python bindings are not available, so this archive cannot be extracted.",
            "code": "EXECUTION_FAILED",
            "suggestion": "Read it as vector tiles with mode='tiles', which can block QGIS on a large archive.",
            "url": url,
        }


    net.check_url(url)




    with vsi.scoped_read(gdal):
        try:
            dataset = gdal.OpenEx(f"/vsicurl/{url}", gdal.OF_VECTOR,
                                  open_options=[f"ZOOM_LEVEL={_PMTILES_EXTRACT_ZOOM}"])
        except RuntimeError as exc:
            return {"_error": f"GDAL could not open the PMTiles archive at {url}: {exc}",
                    "code": "EXECUTION_FAILED",
                    "suggestion": "Check the URL, or ask for the same data from another source.",
                    "url": url}
        if dataset is None:
            return {"_error": f"GDAL could not open the PMTiles archive at {url}.",
                    "code": "EXECUTION_FAILED",
                    "suggestion": "Check the URL, or ask for the same data from another source.",
                    "url": url}

        available = []
        for index in range(dataset.GetLayerCount()):
            layer = dataset.GetLayer(index)
            if layer is not None:
                available.append(layer.GetName())
        if not available:
            return {"_error": f"The archive at {url} carries no vector layer at zoom {_PMTILES_EXTRACT_ZOOM}.",
                    "code": "EXECUTION_FAILED",
                    "suggestion": "Ask for another source for this data.",
                    "url": url}
        chosen = str(sublayer) if sublayer else available[0]
        if chosen not in available:
            return {"_error": f"'{chosen}' is not a layer of this archive.",
                    "code": "INVALID_ARGS",
                    "suggestion": f"Pass layer as one of: {', '.join(available)}.",
                    "layers_available": available,
                    "url": url}
        if _pmtiles_cancelled():
            return _pmtiles_stopped(url)

        directory = create_managed_temp_dir("pmtiles")
        path = os.path.join(directory, f"{_safe_file_stem(name or chosen)}.gpkg")
        west, south, east, north = bbox
        try:
            written = gdal.VectorTranslate(
                path, dataset, format="GPKG",
                spatFilter=[west, south, east, north],
                spatSRS="EPSG:4326", dstSRS="EPSG:4326",
                layers=[chosen],
            )
        except RuntimeError as exc:
            return {"_error": f"The extract from {url} failed: {exc}",
                    "code": "EXECUTION_FAILED",
                    "suggestion": "Try a smaller area, or another source for this data.",
                    "url": url}
        if written is None:
            return {"_error": f"The extract from {url} produced nothing.",
                    "code": "EXECUTION_FAILED",
                    "suggestion": "Try a smaller area, or another source for this data.",
                    "url": url}
        out_layer = written.GetLayer(0)
        count = int(out_layer.GetFeatureCount()) if out_layer is not None else 0
        del out_layer
        del written
        del dataset

    return {"path": path, "layer": chosen, "layers_available": available, "feature_count": count}


def _add_pmtiles_extract(url: str, name: str | None, args: dict) -> dict:
    """The default route: extract the area of interest, then add a local file."""
    bbox, origin = _bbox_4326(args)
    if bbox is None:
        return {
            "_error": f"A PMTiles archive is loaded by extracting an area, and {origin}.",
            "code": "INVALID_ARGS",
            "suggestion": ("Zoom the map to the area first, or pass bbox as "
                           "[west, south, east, north] in EPSG:4326."),
            "url": url,
        }
    area = _bbox_area_km2(bbox)
    if area > _PMTILES_MAX_AREA_KM2:
        return {
            "_error": (f"The area asked for is {area:,.0f} km2, over the {_PMTILES_MAX_AREA_KM2:,.0f} km2 "
                       "one extract may cover."),
            "code": "INVALID_ARGS",
            "suggestion": ("Zoom to a smaller area or pass a smaller bbox. mode='tiles' reads the whole "
                           "archive in place, but it can block QGIS for minutes."),
            "bbox": bbox,
            "area_km2": round(area, 1),
            "url": url,
        }

    started = time.monotonic()
    extract = _extract_pmtiles(url, bbox, args.get("layer"), name)
    if extract.get("_error"):
        return extract
    if _pmtiles_cancelled():
        return _pmtiles_stopped(url)

    path = extract["path"]
    chosen = extract["layer"]
    label = name or chosen
    source = f"{path}|layername={chosen}"

    def _create():
        layer = QgsVectorLayer(source, label, "ogr")
        if not layer.isValid():
            return {"_error": f"QGIS could not read the extract written to {path}.",
                    "code": "EXECUTION_FAILED",
                    "suggestion": "Try a smaller area, or another source for this data."}
        QgsProject.instance().addMapLayer(layer)
        return {"layer_name": layer.name(), "layer_id": layer.id(), "crs": layer.crs().authid()}



    result = _run_on_main_thread(_create, timeout=_PMTILES_ADD_TIMEOUT)
    if result.get("_error"):
        return result

    count = extract["feature_count"]
    result.update({
        "url": url,
        "path": path,
        "bbox": bbox,
        "area_km2": round(area, 1),
        "feature_count": count,
        "layer": chosen,
        "layers_available": extract["layers_available"],
        "provider": "GDAL extract from PMTiles",
        "seconds": round(time.monotonic() - started, 1),
        "_note": (f"{count:,} features of '{chosen}' cut out of the archive for {origin} "
                  f"({bbox[0]:.4f}, {bbox[1]:.4f}, {bbox[2]:.4f}, {bbox[3]:.4f}) and written to a local "
                  "GeoPackage. The archive itself was never downloaded."),
    })
    if count == 0:
        result["warning"] = "The archive has no feature of this layer inside that box."
        result["suggestion"] = "Check the area, or try another layer of the archive."
    return result


def _add_pmtiles_as_tiles(url: str, name: str | None, args: dict) -> dict:
    """The opt-in route: the whole archive as a remote vector tile layer."""






    _prefetch_pmtiles(url)

    uri = f"type=xyz&url={encode_uri_url(url)}"

    def _create():
        layer = QgsVectorTileLayer(uri, name or "PMTiles")
        if not layer.isValid():
            return {
                "_error": (
                    "This QGIS has no PMTiles vector tile provider, so the archive could not be opened as "
                    "vector tiles. It is reachable and valid: the probe read its header first."
                ),
                "_code": "INVALID_ARGS",
                "url": url,
            }
        QgsProject.instance().addMapLayer(layer)
        return {"layer_name": layer.name(), "layer_id": layer.id(), "url": url,
                "provider": "vector tiles",
                "_note": ("The whole archive is read in place as vector tiles, which QGIS builds on the main "
                          "thread: the window can stay blocked while it opens.")}

    try:



        result = _run_on_main_thread(_create, timeout=_PMTILES_BUILD_TIMEOUT)
    except TimeoutError:



        return {
            "_error": (f"QGIS was still opening {url} after {_PMTILES_BUILD_TIMEOUT} seconds and the window is "
                       "blocked until it finishes."),
            "code": "EXECUTION_FAILED",
            "suggestion": "Wait for QGIS to answer again, then load this archive without mode='tiles'.",
        }
    if result.get("_error"):



        from .data_tools import _add_vector_over_range_requests

        try:
            fallback = _add_vector_over_range_requests(url, name, args.get("layer"))
        except Exception:  # noqa: BLE001 - keep the first, clearer error
            return result
        if not fallback.get("_error"):
            fallback["_note"] = ("Read with GDAL over HTTP range requests: this QGIS could not open the "
                                 "archive as a vector tile layer. " + str(fallback.get("_note") or ""))
            return fallback
    return result


def _pmtiles_unreadable_here() -> dict | None:
    """The refusal to make when nothing in this install can read a PMTiles archive."""








    try:
        from osgeo import ogr
    except ImportError:
        return None
    try:
        if ogr.GetDriverByName("PMTiles") is not None:
            return None
    except Exception:  # noqa: BLE001 - a GDAL that cannot answer is not proof it lacks the driver
        return None
    return {
        "_error": "This QGIS build has no PMTiles driver, so a PMTiles archive cannot be read here.",
        "code": "EXECUTION_FAILED",
        "suggestion": ("Ask the source for GeoParquet, FlatGeobuf or a tile service instead, and tell "
                       "the user their QGIS ships a GDAL older than 3.8, or one without the driver."),
    }


def _add_pmtiles_layer(args: dict) -> dict:
    """Load a PMTiles archive, by extracting an area of interest by default."""






    url = args.get("url")
    if not url:
        return {"_error": "url is required"}
    name = args.get("name")
    mode = str(args.get("mode") or "extract").strip().lower()
    if mode not in ("extract", "auto", "tiles"):
        return {"_error": f"'{mode}' is not a way to load a PMTiles archive.",
                "code": "INVALID_ARGS",
                "suggestion": "Use mode='extract' (the default) or mode='tiles'."}

    refusal = _pmtiles_unreadable_here() or _probe_pmtiles(url)
    if refusal:
        return refusal
    if _pmtiles_cancelled():
        return _pmtiles_stopped(url)

    result = (_add_pmtiles_as_tiles(url, name, args) if mode == "tiles"
              else _add_pmtiles_extract(url, name, args))
    dataset_docs.attach(result, url)
    return result
