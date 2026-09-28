# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

















from __future__ import annotations

import calendar
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsVectorTileLayer,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP, QCoreApplication

from ..core import dataset_docs, limits, net, tuning, vsi
from ..core.background import on_main_thread, still_awaited
from ..core.crs_ref import crs_ref
from ..core.host_platform import remove_tree
from ..core.logger import log_warning
from ..core.policy import create_managed_temp_dir
from ..core.provider_uri import encode_uri_url
from ..core.tool_registry import Tool, ToolRegistry
from . import volume_guard
from .data_tools import _avoid_reserved_name, _run_on_main_thread





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

_PMTILES_MAX_ZOOM_OFFSET = 101
_PMTILES_PROBE_BYTES = 16 * 1024
_PMTILES_PROBE_TIMEOUT = 20

_PMTILES_BUILD_TIMEOUT = 20





_PMTILES_MAX_AREA_KM2 = 400.0



_PMTILES_EXTRACT_ZOOM = 14


_PMTILES_ADD_TIMEOUT = 30







_PMTILES_BLOCK_TILES = 8
_PMTILES_BLOCK_INSET = 1e-7


_PMTILES_LIFTED_CLOCK_SHARE = 0.75

_PMTILES_UNREAD_SHOWN = 12



_NOT_AWAITED = {"_error": "The call this layer was built for had already ended; the layer was not added.",
                "_code": "TIMEOUT"}


def register_stac_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="add_cog_layer",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Add the raster[ {name}]"),
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
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Add the satellite image[ {name}]"),
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



    ttl = 0.0 if url.startswith(_pc_sign_url().rsplit("/", 1)[0] + "/") else _CACHE_STAC_S
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
    except Exception:  # nosec B110
        pass
    return None




def _has_sas_token(url: str) -> bool:








    query = urllib.parse.urlsplit(url or "").query
    if not query:
        return False
    keys = {k.lower() for k in urllib.parse.parse_qs(query, keep_blank_values=True)}
    return "sig" in keys


_PC_BLOB_HOST = "blob.core.windows.net"




_PUBLIC_BLOB_ACCOUNTS = ("stterralabopendata",)


def _sign_pc_href(url: str) -> tuple[str, str | None]:








    host = (urllib.parse.urlsplit(url or "").hostname or "").lower().rstrip(".")
    if not (host == _PC_BLOB_HOST or host.endswith("." + _PC_BLOB_HOST)) or _has_sas_token(url):
        return url, None
    account = host.split(".", 1)[0]
    if account in _PUBLIC_BLOB_ACCOUNTS:
        return url, None
    container = urllib.parse.urlsplit(url).path.lstrip("/").split("/", 1)[0]
    token = _pc_container_token(account, container) if container else None
    if token:
        return f"{url}{'&' if '?' in url else '?'}{token}", None
    for attempt in range(2):
        try:
            sign_url = f"{_pc_sign_url()}?href={urllib.parse.quote(url, safe='')}"
            signed = _stac_get(sign_url, timeout=_SIGN_TIMEOUT).get("href")
            if signed:
                return signed, None
        except (urllib.error.URLError, OSError, ValueError):
            pass
        if attempt == 0:
            time.sleep(1.0)
    return url, _PC_SIGN_FAILED


_PC_SIGN_FAILED = ("Planetary Computer did not sign this asset, so it was requested unsigned "
                   "and refused (HTTP 409). Signing often works on the next call.")




_PC_TOKENS: dict[tuple[str, str], tuple[str, float]] = {}
_PC_TOKENS_LOCK = threading.Lock()
_PC_TOKEN_MARGIN_S = 300


def _pc_container_token(account: str, container: str) -> str | None:

    sign = _pc_sign_url()
    if not sign.endswith("/sign"):
        return None
    key = (account, container)
    with _PC_TOKENS_LOCK:
        cached = _PC_TOKENS.get(key)
        if cached and cached[1] - _PC_TOKEN_MARGIN_S > time.time():
            return cached[0]
        try:
            payload = _stac_get(f"{sign[:-len('/sign')]}/token/{account}/{container}",
                                timeout=_SIGN_TIMEOUT)
            token = payload.get("token")
            expiry = payload.get("msft:expiry") or ""
            expires = calendar.timegm(time.strptime(expiry[:19], "%Y-%m-%dT%H:%M:%S"))
        except (urllib.error.URLError, OSError, ValueError, TypeError, AttributeError):
            return None
        if not token or "sig=" not in token:
            return None
        _PC_TOKENS[key] = (token, expires)
        return token


_CDSE_S3_PREFIX = "s3://eodata/"
_CDSE_HTTPS_PREFIX = "https://eodata.dataspace.copernicus.eu/"



_EARTHDATA_HOSTS = ("data.lpdaac.earthdatacloud.nasa.gov", "e4ftl01.cr.usgs.gov")
_EARTHDATA_SUFFIXES = (".earthdata.nasa.gov", ".earthdatacloud.nasa.gov")


def _needs_backend_signing(url: str) -> bool:

    lowered = (url or "").strip()
    if lowered.startswith(_CDSE_S3_PREFIX) or lowered.startswith(_cdse_https_prefix()):
        return True
    parts = urllib.parse.urlsplit(lowered)
    if parts.scheme != "https":
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    return host in _earthdata_hosts() or host.endswith(_earthdata_suffixes())


def _backend_sign_url() -> str:

    from ..core.settings import Settings

    parts = urllib.parse.urlsplit(Settings().server_url)
    scheme = "http" if parts.scheme == "ws" else "https"
    return f"{scheme}://{parts.netloc}/eodata/sign"


def _sign_backend_href(url: str) -> tuple[str, str | None]:







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












    if not url.startswith("s3://") or url.startswith(_CDSE_S3_PREFIX):
        return url
    bucket, _, key = url[len("s3://"):].partition("/")
    if not bucket or not key:
        return url
    return f"https://{bucket}.s3.amazonaws.com/{urllib.parse.quote(key)}"


def _signed_href(url: str) -> tuple[str, str | None]:

    if _needs_backend_signing(url):
        return _sign_backend_href(url)
    return _sign_pc_href(_public_s3_https(url))


def _raster_built_here(source: str, name: str):















    vsi.apply_persistent()
    if on_main_thread():
        return None
    options = QgsRasterLayer.LayerOptions()
    options.skipCrsValidation = True
    layer = QgsRasterLayer(source, name, "gdal", options)
    if layer.isValid() and not layer.crs().isValid():
        return None
    app = QCoreApplication.instance()
    if app is None:
        return None
    layer.moveToThread(app.thread())
    return layer
















_ONE_SHOT_PATH = "/eodata/file"

_ONE_SHOT_MAX_BYTES = 128 * 1024 * 1024
_ONE_SHOT_TIMEOUT = 60


def _is_one_shot_result(url: str) -> bool:

    try:
        return urllib.parse.urlsplit(url).path == _ONE_SHOT_PATH
    except ValueError:
        return False


def _localise_one_shot(url: str, name: str | None) -> dict | None:






    if not _is_one_shot_result(url):
        return None
    return add_raster_downloaded(url, name, polite=False, one_shot=True)


def _declare_nan_nodata(path: str) -> None:








    try:
        from osgeo import gdal
    except ImportError:  # nosec B110
        return
    try:
        dataset = gdal.Open(path, gdal.GA_Update)
        if dataset is None:
            return
        for index in range(1, dataset.RasterCount + 1):
            band = dataset.GetRasterBand(index)
            if band.DataType in (gdal.GDT_Float32, gdal.GDT_Float64) and band.GetNoDataValue() is None:
                band.SetNoDataValue(float("nan"))
        del dataset
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Downloaded raster: nodata not declared: {exc}")


def add_raster_downloaded(url: str, name: str | None, *, polite: bool = True,
                          one_shot: bool = False) -> dict:






    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:

        response = net.fetch(request, timeout=_ONE_SHOT_TIMEOUT, max_bytes=_ONE_SHOT_MAX_BYTES,
                             total_timeout=_ONE_SHOT_TIMEOUT * _TOTAL_TIMEOUT_FACTOR, polite=polite)
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, net.FetchCancelled):
            return {"_error": "The raster download was stopped.", "_code": "CANCELLED",
                    "_suggestion": "The user stopped it; only they restart it.", "url": url}


        if one_shot and isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 404, 410):
            return {"_error": f"That result is no longer on the server: {exc}",
                    "_code": "EXECUTION_FAILED",
                    "_suggestion": "This url is held for a short while only; the tool that produced it "
                                   "gives a new one for the same area.",
                    "url": url}
        if isinstance(exc, net.FetchTooLarge):



            return {"_error": f"That raster cannot be streamed and is too large to download: {exc}",
                    "_code": "EXECUTION_FAILED",
                    "_suggestion": "Take a coarser or hosted Cloud-Optimized version of the same data "
                                   "(find_datasets), or a smaller area's file.", "url": url}
        if net.describe_failure(exc):
            return {"_error": f"The raster download did not finish: {exc}", "_code": "NETWORK_ERROR",
                    "_suggestion": "An interrupted transfer does not mean the source result expired; one "
                                   "retry of this url usually finishes it.", "url": url}
        return {"_error": f"Could not download that raster: {exc}", "_code": "EXECUTION_FAILED",
                "_suggestion": "A working address serves the file itself, not a page about it.", "url": url}
    body = response.body


    headers = response.headers or {}
    credit = {key: urllib.parse.unquote(str(headers.get(header) or "")).strip()[:500]
              for key, header in (("licence", "x-data-licence"), ("attribution", "x-data-attribution"))}
    credit = {key: value for key, value in credit.items() if value}
    if not body:
        return {"_error": "That address returned an empty file.",
                "_code": "EXECUTION_FAILED",
                "_suggestion": "The producing tool gives a fresh url." if one_shot
                               else "A working address serves the file itself.", "url": url}
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", (name or "result").strip()) or "result"

    path = os.path.join(create_managed_temp_dir("eodata"), f"{_avoid_reserved_name(stem[:60])}.tif")
    try:
        with open(path, "wb") as handle:
            handle.write(body)
    except OSError as exc:
        return {"_error": f"Could not write the downloaded result to disk: {exc}",
                "_code": "EXECUTION_FAILED",
                "_suggestion": "The temp folder may be full.", "url": url}
    _declare_nan_nodata(path)
    from .elevation_style import apply_elevation_style, elevation_stretch
    stretch = elevation_stretch(path, name)
    built = _raster_built_here(path, name or "Result")

    def _create():
        layer = built if built is not None else QgsRasterLayer(path, name or "Result", "gdal")
        if not layer.isValid():
            return {"_error": "The downloaded file is not a raster QGIS can read.",
                    "_code": "EXECUTION_FAILED",
                    "_suggestion": "The producing tool gives a fresh url." if one_shot
                                   else "A working address serves a GeoTIFF, not a web page.", "url": url}
        normalised = normalise_crs(layer)
        styled = apply_elevation_style(layer, stretch)
        if not still_awaited():
            return dict(_NOT_AWAITED)
        QgsProject.instance().addMapLayer(layer)
        return {
            "layer_name": layer.name(),
            "layer_id": layer.id(),
            "path": path,
            **({"styled": styled} if styled else {}),
            **({"crs_note": f"The file names no CRS authority; read as {normalised}."} if normalised else {}),
            "width": layer.width(),
            "height": layer.height(),
            "bands": layer.bandCount(),
            "crs": _crs_label(layer.crs()),
            **credit,
            "_note": ("Downloaded to this machine, so the layer keeps working after the server drops the file."
                      if one_shot else "Downloaded to this machine: this address is not streamable."),
        }

    return _run_on_main_thread(_create, timeout=60)


def _probe_raster_url(url: str) -> tuple[int, str]:






    request = urllib.request.Request(url, headers={"Range": "bytes=0-3"})
    try:
        response = net.fetch(request, timeout=10, max_bytes=64, total_timeout=15)
    except urllib.error.HTTPError as exc:
        code = int(getattr(exc, "code", 0) or 0)
        causes = {
            401: "the host asks for credentials (HTTP 401)",
            403: "the host refuses access to this file (HTTP 403)",
            404: "the file does not exist at this address (HTTP 404)",
            410: "the file was removed from this address (HTTP 410)",
            429: "the host is rate limiting requests (HTTP 429)",
        }
        if code in causes:
            return code, causes[code] + "."
        if code >= 500:
            return code, f"the host failed to serve the file (HTTP {code})."
        return code, f"the host answered HTTP {code}."
    except net.FetchTooLarge:
        return 200, ("the address answers (HTTP 200) but ignores range requests, so the file cannot "
                     "be streamed; download it instead.")
    except Exception as exc:  # noqa: BLE001
        return 0, f"the host could not be reached ({type(exc).__name__})."
    body = bytes(getattr(response, "body", b"") or b"")[:4]
    if body[:2] not in (b"II", b"MM"):
        return 200, ("the address answers (HTTP 200) but the content is not a GeoTIFF; "
                     "it may be a web page or another format.")
    return 200, ("the file is a TIFF (HTTP 200) but GDAL could not stream it; it may not be "
                 "Cloud-Optimized, or the server ignores range requests.")


def _add_cog(final_url: str, name: str | None, note: str | None = None, extra: dict | None = None) -> dict:






    if final_url.startswith("s3://"):
        return {"_error": note or ("This s3:// address was not opened: it needs a signature this server "
                                   "did not provide, or its bucket is not public."),
                "_code": "PERMISSION_DENIED",
                "_suggestion": "search_stac_items on the Planetary Computer serves the same Sentinel-2 "
                               "scene without a signature.",
                "url": final_url}



    try:
        net.check_url(final_url)
    except Exception as exc:  # noqa: BLE001
        return {"_error": f"This asset address is not fetched: {exc}", "_code": "PERMISSION_DENIED",
                "_suggestion": "A public https address serves the asset.", "url": final_url}
    local = _localise_one_shot(final_url, name)
    if local is not None:
        if note and not local.get("_error"):
            local["_note"] = f"{note} {local.get('_note', '')}".strip()
        return local
    from .elevation_style import apply_elevation_style, elevation_stretch
    stretch = elevation_stretch(f"/vsicurl/{final_url}", name)
    built = _raster_built_here(f"/vsicurl/{final_url}", name or "COG")

    def _create():
        layer = built if built is not None else QgsRasterLayer(f"/vsicurl/{final_url}", name or "COG", "gdal")
        if not layer.isValid():
            return {
                "_error": "Could not open the raster.",
                "_code": "INVALID_ARGS",








                "_suggestion": (
                    'add_data with kind "raster" downloads a plain TIFF, or a server that ignores '
                    "range requests, rather than streaming it."
                ),
                "url": final_url,
            }
        normalised = normalise_crs(layer)
        styled = apply_elevation_style(layer, stretch)
        if not still_awaited():
            return dict(_NOT_AWAITED)
        QgsProject.instance().addMapLayer(layer)
        return {
            "layer_name": layer.name(),
            "layer_id": layer.id(),
            "url": final_url,
            **({"styled": styled} if styled else {}),
            **({"crs_note": f"The file names no CRS authority; read as {normalised}."} if normalised else {}),
            "width": layer.width(),
            "height": layer.height(),
            "bands": layer.bandCount(),
            "crs": _crs_label(layer.crs()),
        }

    result = _run_on_main_thread(_create, timeout=60)
    if result.get("_error"):
        if result.get("_error") == "Could not open the raster.":
            status, cause = _probe_raster_url(final_url)
            result["_error"] = f"Could not open the raster: {cause}"
            if status:
                result["http_status"] = status
            if status in (401, 403):
                result["_code"] = "PERMISSION_DENIED"
                result["_suggestion"] = ("The host refuses this file without credentials. An open source, "
                                         "or a signed address from the catalog, would serve it.")
            elif status and status != 200 and status != 206:
                result["_code"] = "NETWORK_ERROR" if status >= 500 or status == 429 else "INVALID_ARGS"
                result["_suggestion"] = ("The file is not at this address; the catalog holds a current "
                                         "asset URL for it." if status in (404, 410) else
                                         "The host did not serve it; that often clears, or another source has it.")
            if note == _PC_SIGN_FAILED:
                result["_error"] = note
                result["_code"] = "NETWORK_ERROR"
                result["_suggestion"] = "The signing service sometimes answers on a second call."
        return result
    if note:
        result["_note"] = note
    if extra:
        result.update(extra)
    if not result.get("crs"):





        result["warning"] = ("This raster carries no CRS, so QGIS cannot place it on the map. It is a picture "
                             "of the scene (a browse image), not georeferenced data.")
        result["suggestion"] = ("A cloud-optimized GeoTIFF asset of the same item is georeferenced: its key "
                                "as asset to add_stac_layer, or its href to add_cog_layer.")
    return result


def normalise_crs(layer) -> str:









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
    except Exception:  # noqa: BLE001
        return ""


def _crs_label(crs) -> str:





    try:
        if not getattr(crs, "isValid", lambda: True)():
            return ""
        authid = crs.authid()
        if authid:
            return authid


        return getattr(crs, "description", lambda: "")() or getattr(crs, "toProj", lambda: "")()[:80]
    except Exception:  # noqa: BLE001
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

    if not isinstance(asset, dict) or not asset.get("href"):
        return False
    media = (asset.get("type") or "").lower()
    return any(hint in media for hint in _RASTER_MEDIA_HINTS)




_S2_BAND_NAMES = {"B01": "coastal", "B02": "blue", "B03": "green", "B04": "red", "B05": "rededge1",
                  "B06": "rededge2", "B07": "rededge3", "B08": "nir", "B8A": "nir08", "B09": "nir09",
                  "B11": "swir16", "B12": "swir22", "SCL": "scl", "TCI": "visual", "AOT": "aot", "WVP": "wvp"}


def _asset_alias(assets: dict, asset_key: str) -> str | None:

    wanted = str(asset_key or "").strip()
    by_lower = {str(key).lower(): key for key in assets}
    if wanted.lower() in by_lower:
        return by_lower[wanted.lower()]
    band = wanted.upper()
    if len(band) == 2 and band[0] == "B" and band[1].isdigit():
        band = "B0" + band[1]
    candidates = [_S2_BAND_NAMES.get(band)] + [key for key, name in _S2_BAND_NAMES.items() if name == wanted.lower()]
    for candidate in candidates:
        if candidate and candidate.lower() in by_lower:
            return by_lower[candidate.lower()]
    return None




_MASK_ROLES = frozenset({"cloud", "cloud-shadow", "snow-ice", "water-mask", "land-water", "data-mask",
                         "saturation", "metadata", "thumbnail", "overview"})
_TRUE_COLOUR = ("red", "green", "blue")


def _band_colours(asset: dict) -> list:

    bands = asset.get("eo:bands") or asset.get("bands") or []
    if not isinstance(bands, list):
        return []
    return [str(b.get("common_name") or b.get("eo:common_name") or "").lower() for b in bands if isinstance(b, dict)]


def _roles(asset: dict) -> list:
    roles = asset.get("roles")
    return [str(role).lower() for role in roles] if isinstance(roles, list) else []


def _imagery_choice(assets: dict, candidates: list) -> tuple | None:









    for key in candidates:
        if "visual" in _roles(assets[key]):
            return assets[key]["href"], key, None, None
    for key in candidates:
        if all(colour in _band_colours(assets[key]) for colour in _TRUE_COLOUR):
            return assets[key]["href"], key, None, None
    colour_keys = []
    for colour in _TRUE_COLOUR:
        for key in candidates:
            if _band_colours(assets[key]) == [colour] and not _MASK_ROLES.intersection(_roles(assets[key])):
                colour_keys.append(key)
                break
    if colour_keys and _band_colours(assets[colour_keys[0]]) == ["red"]:
        key = colour_keys[0]
        note = f"Added the '{key}' band: this item has no true-colour asset, so one band shows as a grey image."
        if len(colour_keys) == len(_TRUE_COLOUR):
            note += (f" For true colour add '{colour_keys[1]}' and '{colour_keys[2]}' too and stack the three in "
                     f"that order ({', '.join(colour_keys)}) with gdal:buildvirtualraster, SEPARATE true.")
        return assets[key]["href"], key, note, None
    for key in candidates:
        if "reflectance" in _roles(assets[key]):
            return assets[key]["href"], key, f"Added the '{key}' reflectance band.", None
    return None


def _pick_asset_href(assets: dict, asset_key: str | None) -> tuple[str | None, str | None, str | None, str | None]:









    if not assets:
        return None, None, None, "The STAC item has no assets."

    if asset_key:
        asset = assets.get(asset_key)
        if not asset or not asset.get("href"):
            alias = _asset_alias(assets, asset_key)
            aliased = assets.get(alias) if alias else None
            if isinstance(aliased, dict) and aliased.get("href"):
                return aliased["href"], alias, f"Asset '{asset_key}' is named '{alias}' in this catalog.", None
            return None, None, None, f"Asset '{asset_key}' not found. Available: {', '.join(assets.keys())}"
        return asset["href"], asset_key, None, None

    visual = assets.get("visual")
    if _is_raster_asset(visual):
        return visual["href"], "visual", None, None

    rasters = [key for key, asset in assets.items() if _is_raster_asset(asset)]
    cogs = [key for key in rasters if _COG_PROFILE_HINT in (assets[key].get("type") or "").lower()]
    picked = _imagery_choice(assets, cogs or rasters)
    if picked:
        return picked
    ranked = cogs or rasters
    if ranked:
        key, others = ranked[0], ranked[1:]
        note = None
        if not cogs:




            note = (f"Added the '{key}' asset. No asset of this item is tagged cloud-optimized, so it may be "
                    f"read whole rather than by area: expect tens of seconds.")
        elif others:




            note = (f"Added the '{key}' asset. This item has other GeoTIFF assets "
                    f"({', '.join(others[:8])}); asset= picks one of them.")
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
            return {"_error": "item_url, or stac_url + collection + item_id, is needed."}


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


    from ..core.data_date import from_range

    props = item.get("properties") if isinstance(item.get("properties"), dict) else {}
    acquired = from_range(props.get("start_datetime") or props.get("datetime"),
                          props.get("end_datetime") or props.get("datetime"))
    if acquired:
        extra["data_date"] = acquired
    result = _add_cog(final_url, name, note=note, extra=extra)
    dataset_docs.attach(result, item_url)
    return result


def _probe_pmtiles(url: str) -> dict:




















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
            "suggestion": "A host that answers range requests, or an extract, would serve this.",
        }
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {
            "_error": f"The PMTiles archive at {url} could not be read: {e}",
            "code": "EXECUTION_FAILED",
            "suggestion": "A working URL, or another source, serves the same data.",
        }

    if not head.startswith(_PMTILES_MAGIC):
        opening = head[:48].decode("utf-8", errors="replace").replace("\n", " ").strip()
        return {
            "_error": (f"{url} is not a PMTiles archive: it begins with '{opening}' instead of the PMTiles "
                       "magic bytes. A URL that answers with an error page reads exactly like this."),
            "code": "INVALID_ARGS",
            "suggestion": "A working URL points at the .pmtiles file itself; another source has it.",
        }
    version = head[len(_PMTILES_MAGIC)] if len(head) > len(_PMTILES_MAGIC) else 0
    if version != _PMTILES_VERSION:
        return {
            "_error": (f"This archive is PMTiles version {version}; only version {_PMTILES_VERSION} can be read "
                       "in place."),
            "code": "INVALID_ARGS",
            "suggestion": "A version 3 archive, or another cloud-native format, would read.",
        }



    if len(head) > _PMTILES_MAX_ZOOM_OFFSET:
        return {"max_zoom": head[_PMTILES_MAX_ZOOM_OFFSET]}
    return {}


def _prefetch_pmtiles(url: str) -> None:








    try:
        from osgeo import gdal
    except Exception:  # noqa: BLE001
        return
    vsi.apply_persistent()
    try:


        net.check_url(url)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"PMTiles prefetch refused: {exc}")
        return
    try:
        dataset = gdal.OpenEx(f"/vsicurl/{url}", gdal.OF_VECTOR)
        del dataset
    except Exception:  # nosec B110
        pass


def _parse_bbox(raw) -> list | None:

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
    except Exception:  # noqa: BLE001
        canvas = None
    if canvas:
        return canvas, "the current canvas extent"
    return None, "no bbox was given and the canvas extent could not be read"


def _bbox_area_km2(bbox: list) -> float:







    west, south, east, north = bbox
    return limits.bbox_km2(south, west, north, east)


def _pmtiles_cancelled() -> bool:

    check = net.current_cancel_check()
    try:
        return bool(check and check())
    except Exception:  # noqa: BLE001
        return False


def _pmtiles_stopped(url: str) -> dict:
    return {
        "_error": "Stopped before the extract finished.",
        "code": "CANCELLED",
        "suggestion": "The user stopped it; nothing was added.",
        "url": url,
    }


def _safe_file_stem(text: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text)).strip("_.")


    return _avoid_reserved_name(stem[:60] or "pmtiles")


def _pmtiles_blocks(bbox: list, zoom: int) -> list:





    import math

    west, south, east, north = bbox
    n = 2 ** zoom
    x0, y0 = _tiles_in_bbox([west, north, west, north], zoom)[0]
    x1, y1 = _tiles_in_bbox([east, south, east, south], zoom)[0]

    def lon(x):
        return x / n * 360.0 - 180.0

    def lat(y):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))

    step, eps, blocks = _PMTILES_BLOCK_TILES, _PMTILES_BLOCK_INSET, []
    for bx in range(x0, x1 + 1, step):
        left = west if bx == x0 else lon(bx) + eps
        last_x = min(bx + step - 1, x1)
        right = east if last_x == x1 else lon(last_x + 1) - eps
        for by in range(y0, y1 + 1, step):
            top = north if by == y0 else lat(by) - eps
            last_y = min(by + step - 1, y1)
            bottom = south if last_y == y1 else lat(last_y + 1) + eps
            if left < right and bottom < top:
                blocks.append([left, bottom, right, top])
    return blocks


def _pmtiles_read_blocks(gdal, dataset, url: str, bbox: list, chosen: str, zoom: int, path: str,
                         deadline: float) -> dict:





    blocks = _pmtiles_blocks(bbox, zoom)
    read, rescued, unread_tiles, stopped = 0, 0, [], False

    def keep_going(_complete, _message, _data):

        return 0 if (_pmtiles_cancelled() or time.monotonic() > deadline) else 1

    for index, block in enumerate(blocks):
        if _pmtiles_cancelled():
            stopped = True
            break
        if time.monotonic() > deadline:
            break
        options = {"format": "GPKG", "spatFilter": block, "spatSRS": "EPSG:4326", "dstSRS": "EPSG:4326",
                   "layers": [chosen], "layerName": chosen, "callback": keep_going}
        if index:
            options.update(accessMode="append")
        try:
            written = gdal.VectorTranslate(path, dataset, **options)
        except RuntimeError as exc:
            if _pmtiles_cancelled() or time.monotonic() > deadline:
                stopped = _pmtiles_cancelled()
                break
            if not index:
                return {"_error": f"The extract from {url} failed: {exc}", "code": "EXECUTION_FAILED",
                        "suggestion": "A smaller area, or another source, fits this data.", "url": url}
            break
        if written is None:
            if not index:
                return {"_error": f"The extract from {url} produced nothing.", "code": "EXECUTION_FAILED",
                        "suggestion": "A smaller area, or another source, fits this data.", "url": url}
            break
        del written
        rescue = _rescue_oversized_tiles(gdal, url, block, chosen, zoom, path)
        rescued += rescue["tiles_rescued"]
        unread_tiles += rescue["tiles_unread"]
        read += 1
    if stopped:
        return _pmtiles_stopped(url)
    return {"blocks_total": len(blocks), "blocks_read": read,
            "blocks_unread": [[round(v, 5) for v in b] for b in blocks[read:]],
            "tiles_rescued": rescued, "tiles_unread": unread_tiles}


def _extract_pmtiles(url: str, bbox: list, sublayer: str | None, name: str | None,
                     max_zoom: int | None = None, deadline: float | None = None) -> dict:













    try:
        from osgeo import gdal
    except ImportError:
        return {
            "_error": "GDAL's Python bindings are not available, so this archive cannot be extracted.",
            "code": "EXECUTION_FAILED",
            "suggestion": "mode='tiles' reads it as vector tiles, which can block QGIS on a large archive.",
            "url": url,
        }


    net.check_url(url)





    zoom = _PMTILES_EXTRACT_ZOOM if max_zoom is None else max(0, min(_PMTILES_EXTRACT_ZOOM, int(max_zoom)))
    with vsi.scoped_read(gdal):
        try:
            dataset = gdal.OpenEx(f"/vsicurl/{url}", gdal.OF_VECTOR,
                                  open_options=[f"ZOOM_LEVEL={zoom}"])
        except RuntimeError as exc:
            return {"_error": f"GDAL could not open the PMTiles archive at {url}: {exc}",
                    "code": "EXECUTION_FAILED",
                    "suggestion": "A working URL, or another source, serves the same data.",
                    "url": url}
        if dataset is None:
            return {"_error": f"GDAL could not open the PMTiles archive at {url}.",
                    "code": "EXECUTION_FAILED",
                    "suggestion": "A working URL, or another source, serves the same data.",
                    "url": url}

        available = []
        for index in range(dataset.GetLayerCount()):
            layer = dataset.GetLayer(index)
            if layer is not None:
                available.append(layer.GetName())
        if not available:
            return {"_error": f"The archive at {url} carries no vector layer at zoom {zoom}.",
                    "code": "EXECUTION_FAILED",
                    "suggestion": "Another source may hold this data.",
                    "url": url}
        chosen = str(sublayer) if sublayer else available[0]
        if chosen not in available:
            return {"_error": f"'{chosen}' is not a layer of this archive.",
                    "code": "INVALID_ARGS",
                    "suggestion": f"layer is one of: {', '.join(available)}.",
                    "layers_available": available,
                    "url": url}
        if _pmtiles_cancelled():
            return _pmtiles_stopped(url)

        directory = create_managed_temp_dir("pmtiles")
        path = os.path.join(directory, f"{_safe_file_stem(name or chosen)}.gpkg")
        if deadline is not None:

            blocks = _pmtiles_read_blocks(gdal, dataset, url, bbox, chosen, zoom, path, deadline)
            del dataset
            if blocks.get("_error"):
                remove_tree(directory)
                return blocks
            reopened = gdal.OpenEx(path, gdal.OF_VECTOR)
            out_layer = reopened.GetLayerByName(chosen) if reopened is not None else None
            count = int(out_layer.GetFeatureCount()) if out_layer is not None else 0
            del out_layer
            del reopened
            return {"path": path, "layer": chosen, "layers_available": available, "feature_count": count,
                    **blocks}
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
                    "suggestion": "A smaller area, or another source, fits this data.",
                    "url": url}
        if written is None:
            return {"_error": f"The extract from {url} produced nothing.",
                    "code": "EXECUTION_FAILED",
                    "suggestion": "A smaller area, or another source, fits this data.",
                    "url": url}
        out_layer = written.GetLayer(0)
        count = int(out_layer.GetFeatureCount()) if out_layer is not None else 0
        del out_layer
        del written
        del dataset

        rescue = _rescue_oversized_tiles(gdal, url, bbox, chosen, zoom, path)
        if rescue["tiles_rescued"]:
            reopened = gdal.OpenEx(path, gdal.OF_VECTOR)
            out_layer = reopened.GetLayerByName(chosen) if reopened is not None else None
            if out_layer is not None:
                count = int(out_layer.GetFeatureCount())
            del out_layer
            del reopened

    return {"path": path, "layer": chosen, "layers_available": available, "feature_count": count, **rescue}









_MVT_GDAL_MAX_BYTES = 10 * 1024 * 1024
_MVT_PIECE_BUDGET = 8 * 1024 * 1024


_MVT_RESCUE_MIN_STORED = 512 * 1024

_MVT_RESCUE_MAX_TILES = 2048


def _mvt_varint(buf, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def _mvt_varint_bytes(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _mvt_fields(buf):

    pos, end = 0, len(buf)
    while pos < end:
        start = pos
        key, pos = _mvt_varint(buf, pos)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _mvt_varint(buf, pos)
        elif wire == 2:
            length, pos = _mvt_varint(buf, pos)
            value = buf[pos:pos + length]
            pos += length
        elif wire == 5:
            value, pos = buf[pos:pos + 4], pos + 4
        elif wire == 1:
            value, pos = buf[pos:pos + 8], pos + 8
        else:
            raise ValueError(f"protobuf wire type {wire}")
        if pos > end:
            raise ValueError("protobuf field runs past the message")
        yield number, wire, value, buf[start:pos]


def _mvt_message(number: int, payload: bytes) -> bytes:
    return _mvt_varint_bytes((number << 3) | 2) + _mvt_varint_bytes(len(payload)) + payload


def _mvt_piece(chunk: list, name_raw: bytes, keys: list, values: list, tail: list) -> bytes:

    key_index, value_index, chunk_keys, chunk_values, encoded = {}, {}, [], [], []
    for feature in chunk:
        record = bytearray()
        for field, _wire, value, raw in _mvt_fields(feature):
            if field != 2:
                record += raw
                continue
            tags, pos = bytearray(), 0
            while pos < len(value):
                key, pos = _mvt_varint(value, pos)
                val, pos = _mvt_varint(value, pos)
                if key not in key_index:
                    key_index[key] = len(chunk_keys)
                    chunk_keys.append(keys[key])
                if val not in value_index:
                    value_index[val] = len(chunk_values)
                    chunk_values.append(values[val])
                tags += _mvt_varint_bytes(key_index[key]) + _mvt_varint_bytes(value_index[val])
            record += _mvt_message(2, bytes(tags))
        encoded.append(_mvt_message(2, bytes(record)))
    body = (name_raw + b"".join(encoded) + b"".join(_mvt_message(3, k) for k in chunk_keys)
            + b"".join(_mvt_message(4, v) for v in chunk_values) + b"".join(tail))
    return _mvt_message(3, body)


def _mvt_pieces_under_limit(chunk: list, parts: tuple):
    piece = _mvt_piece(chunk, *parts)
    if len(piece) <= _MVT_GDAL_MAX_BYTES or len(chunk) == 1:
        yield piece
        return
    half = len(chunk) // 2
    yield from _mvt_pieces_under_limit(chunk[:half], parts)
    yield from _mvt_pieces_under_limit(chunk[half:], parts)


def _mvt_layer_pieces(tile: bytes, layer_name: str):

    tile = memoryview(tile)
    for number, wire, layer, _raw in _mvt_fields(tile):
        if number != 3 or wire != 2:
            continue
        name, name_raw, tail, features, keys, values = None, b"", [], [], [], []
        for field, _wire, value, raw in _mvt_fields(layer):
            if field == 1:
                name, name_raw = bytes(value).decode("utf-8", "replace"), bytes(raw)
            elif field == 2:
                features.append(value)
            elif field == 3:
                keys.append(bytes(value))
            elif field == 4:
                values.append(bytes(value))
            else:
                tail.append(bytes(raw))
        if name != layer_name:
            continue
        parts = (name_raw, keys, values, tail)
        chunk, size = [], 0
        for feature in features:
            if chunk and size + len(feature) > _MVT_PIECE_BUDGET:
                yield from _mvt_pieces_under_limit(chunk, parts)
                chunk, size = [], 0
            chunk.append(feature)
            size += len(feature)
        if chunk:
            yield from _mvt_pieces_under_limit(chunk, parts)
        return


def _tiles_in_bbox(bbox: list, zoom: int) -> list:
    import math

    west, south, east, north = bbox
    n = 2 ** zoom

    def column(lon):
        return min(n - 1, max(0, int((lon + 180.0) / 360.0 * n)))

    def row(lat):
        lat = math.radians(max(-85.0511, min(85.0511, lat)))
        return min(n - 1, max(0, int((1.0 - math.asinh(math.tan(lat)) / math.pi) / 2.0 * n)))

    return [(x, y) for x in range(column(west), column(east) + 1) for y in range(row(north), row(south) + 1)]


def _rescue_oversized_tiles(gdal, url: str, bbox: list, layer: str, zoom: int, path: str) -> dict:

    import gzip

    rescued, unread = 0, []
    tiles = _tiles_in_bbox(bbox, zoom)
    if len(tiles) > _MVT_RESCUE_MAX_TILES:
        return {"tiles_rescued": 0, "tiles_unread": []}
    for x, y in tiles:
        if _pmtiles_cancelled():
            break
        address = f"/vsipmtiles//vsicurl/{url}/{zoom}/{x}/{y}.mvt"
        stat = gdal.VSIStatL(address)
        if stat is None or stat.size < _MVT_RESCUE_MIN_STORED:
            continue
        handle = gdal.VSIFOpenL(address, "rb")
        if handle is None:
            unread.append(f"{zoom}/{x}/{y}")
            continue
        try:
            stored = bytes(gdal.VSIFReadL(1, stat.size, handle) or b"")
        finally:
            gdal.VSIFCloseL(handle)
        try:
            raw = gzip.decompress(stored) if stored[:2] == b"\x1f\x8b" else stored
        except (OSError, EOFError):
            unread.append(f"{zoom}/{x}/{y}")
            continue
        if len(raw) <= _MVT_GDAL_MAX_BYTES:
            continue
        try:
            for index, piece in enumerate(_mvt_layer_pieces(raw, layer)):
                memory = f"/vsimem/pmtiles_piece_{zoom}_{x}_{y}_{index}.pbf"
                gdal.FileFromMemBuffer(memory, piece)
                try:
                    source = gdal.OpenEx("MVT:" + memory, gdal.OF_VECTOR, allowed_drivers=["MVT"],
                                         open_options=[f"X={x}", f"Y={y}", f"Z={zoom}"])
                    if source is None or source.GetLayerByName(layer) is None:
                        raise RuntimeError("piece not readable")
                    appended = gdal.VectorTranslate(
                        path, source, accessMode="append", addFields=True, layerName=layer,
                        spatFilter=list(bbox), spatSRS="EPSG:4326", dstSRS="EPSG:4326", layers=[layer],
                    )
                    if appended is None:
                        raise RuntimeError("piece not appended")
                    del appended
                    del source
                finally:
                    gdal.Unlink(memory)
            rescued += 1
        except (RuntimeError, ValueError, IndexError):
            unread.append(f"{zoom}/{x}/{y}")
    return {"tiles_rescued": rescued, "tiles_unread": unread}


def _add_pmtiles_extract(url: str, name: str | None, args: dict, max_zoom: int | None = None) -> dict:

    bbox, origin = _bbox_4326(args)
    if bbox is None:
        return {
            "_error": f"A PMTiles archive is loaded by extracting an area, and {origin}.",
            "code": "INVALID_ARGS",
            "suggestion": ("The map view zoomed to the area, or bbox as "
                           "[west, south, east, north] in EPSG:4326, gives the area."),
            "url": url,
        }
    area = _bbox_area_km2(bbox)


    lifted = volume_guard.lifted(args) and area > _PMTILES_MAX_AREA_KM2
    if area > _PMTILES_MAX_AREA_KM2 and not lifted:
        return {
            "_error": (f"The area asked for is {area:,.0f} km2, over the {_PMTILES_MAX_AREA_KM2:,.0f} km2 "
                       "one extract may cover." + volume_guard.LIFT_HINT),
            "code": "INVALID_ARGS",
            "suggestion": ("A smaller area or bbox would fit. mode='tiles' reads the whole "
                           "archive in place, but it can block QGIS for minutes."),
            "bbox": bbox,
            "box_km2": round(area, 1),
            "url": url,
        }

    started = time.monotonic()
    deadline = (started + limits.current("CALL_MAX_SECONDS_BACKGROUND") * _PMTILES_LIFTED_CLOCK_SHARE
                if lifted else None)
    extract = _extract_pmtiles(url, bbox, args.get("layer"), name, max_zoom, deadline)
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
                    "suggestion": "A smaller area, or another source, fits this data."}
        if not still_awaited():
            return dict(_NOT_AWAITED)
        QgsProject.instance().addMapLayer(layer)
        return {"layer_name": layer.name(), "layer_id": layer.id(), "crs": crs_ref(layer.crs())}



    result = _run_on_main_thread(_create, timeout=_PMTILES_ADD_TIMEOUT)
    if result.get("_error"):
        return result

    count = extract["feature_count"]
    result.update({
        "url": url,
        "path": path,
        "bbox": bbox,
        "box_km2": round(area, 1),
        "feature_count": count,
        "layer": chosen,
        "layers_available": extract["layers_available"],
        "provider": "GDAL extract from PMTiles",
        "seconds": round(time.monotonic() - started, 1),
        "_note": (f"{count:,} features of '{chosen}' cut out of the archive for {origin} "
                  f"({bbox[0]:.4f}, {bbox[1]:.4f}, {bbox[2]:.4f}, {bbox[3]:.4f}) and written to a local "
                  "GeoPackage. The archive itself was never downloaded."),
    })
    if extract.get("tiles_rescued"):
        result["tiles_rescued"] = extract["tiles_rescued"]
    unread = extract.get("tiles_unread") or []
    if lifted:
        result["size_bytes"] = os.path.getsize(path) if os.path.exists(path) else None
        result["wall_s"] = result["seconds"]
        result["blocks_total"] = extract.get("blocks_total")
        result["blocks_read"] = extract.get("blocks_read")
    left = extract.get("blocks_unread") or []
    if left:
        result["coverage"] = "partial"
        result["blocks_unread"] = left[:_PMTILES_UNREAD_SHOWN]
        result["warning"] = (f"The clock ran out after {extract.get('blocks_read')} of {extract.get('blocks_total')} "
                             f"blocks of this box: {count:,} features were written, and the {len(left)} blocks "
                             "listed in blocks_unread (west, south, east, north) are missing.")
        result["suggestion"] = ("The same call with bbox set to the missing blocks, and the same "
                                "full_extent, reads the rest into another layer.")
    elif unread:
        result["tiles_unread"] = unread
        result["coverage"] = "partial"
        result["warning"] = (f"{len(unread)} tile(s) of this box could not be read, so the layer lacks their "
                             "features.")
        result["suggestion"] = "The result is partial; a smaller box around what is missing would cover it."
    elif count == 0:
        result["warning"] = "The archive has no feature of this layer inside that box."
        result["suggestion"] = "The area, or another archive layer, may hold it."
    return result


def _add_pmtiles_as_tiles(url: str, name: str | None, args: dict) -> dict:







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
        if not still_awaited():
            return dict(_NOT_AWAITED)
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
            "suggestion": "Once QGIS answers again, the archive loads without mode='tiles'.",
        }
    if result.get("_error"):



        from .data_tools import _add_vector_over_range_requests

        try:
            fallback = _add_vector_over_range_requests(url, name, args.get("layer"))
        except Exception:  # noqa: BLE001
            return result
        if not fallback.get("_error"):
            fallback["_note"] = ("Read with GDAL over HTTP range requests: this QGIS could not open the "
                                 "archive as a vector tile layer. " + str(fallback.get("_note") or ""))
            return fallback
    return result


def _pmtiles_unreadable_here() -> dict | None:









    try:
        from osgeo import ogr
    except ImportError:
        return None
    try:
        if ogr.GetDriverByName("PMTiles") is not None:
            return None
    except Exception:  # noqa: BLE001
        return None
    return {
        "_error": "This QGIS build has no PMTiles driver, so a PMTiles archive cannot be read here.",
        "code": "EXECUTION_FAILED",
        "suggestion": ("GeoParquet, FlatGeobuf or a tile service work here; this QGIS ships a GDAL "
                       "older than 3.8, or one without the driver."),
    }


def _add_pmtiles_layer(args: dict) -> dict:







    url = args.get("url")
    if not url:
        return {"_error": "url is required"}
    name = args.get("name")
    mode = str(args.get("mode") or "extract").strip().lower()
    if mode not in ("extract", "auto", "tiles"):
        return {"_error": f"'{mode}' is not a way to load a PMTiles archive.",
                "code": "INVALID_ARGS",
                "suggestion": "mode is 'extract' (the default) or 'tiles'."}

    refusal = _pmtiles_unreadable_here()
    if refusal:
        return refusal
    header = _probe_pmtiles(url)
    if header.get("_error"):
        return header
    if _pmtiles_cancelled():
        return _pmtiles_stopped(url)

    result = (_add_pmtiles_as_tiles(url, name, args) if mode == "tiles"
              else _add_pmtiles_extract(url, name, args, header.get("max_zoom")))
    dataset_docs.attach(result, url)
    return result
