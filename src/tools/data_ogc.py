# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


from __future__ import annotations

import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCoordinateTransformContext,
    QgsDataSourceUri,
    QgsExpression,
    QgsOgcUtils,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
)
from qgis.PyQt.QtCore import QCoreApplication, QEvent

from ..core import limits, links, net, tuning
from ..core.background import delete_here, hand_over, on_main_thread, run_on_main_thread, take
from ..core.follow import view_kept
from ..core.logger import log_warning
from ..core.policy import create_managed_temp_dir
from ..core.provider_uri import crs_problem, encode_uri_url
from ..core.qt_compat import enum_member
from ..core.quiet_credentials import hold_from_worker, no_login_prompt, release_from_worker
from ..core.tool_registry import tool_error
from . import elevation_style, ogc_inspect, volume_guard
from .data_basemaps import _stacked
from .data_common import _CACHE_CATALOG_S, _USER_AGENT, _avoid_reserved_name, built_here, worker_options






_WMTS_CAPS_MAX_BYTES = 8 * 1024 * 1024
_WMTS_CAPS_TTL_S = 900
_wmts_capabilities_url = ogc_inspect.wmts_capabilities_url
_wmts_epsg = ogc_inspect.epsg_of


def _wmts_body(url: str) -> bytes:

    try:
        answer = net.fetch(_wmts_capabilities_url(url), timeout=20, max_bytes=_WMTS_CAPS_MAX_BYTES,
                           total_timeout=25, cache_ttl=_WMTS_CAPS_TTL_S)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"WMTS capabilities probe failed for {url}: {exc}")
        return b""
    return answer.body or b""


def _wmts_describe(url: str) -> dict[str, dict]:





    return ogc_inspect.wmts_layers(_wmts_body(url))


def clean_service_url(url: str) -> str:








    text = links.clean(str(url or ""))
    text = re.sub(r"[\r\n\t]+", "", text)
    return re.sub(r"\s*([?&=/])\s*", r"\1", text)


def _canvas_scale() -> float | None:




    try:
        from qgis.utils import iface

        if not QgsProject.instance().mapLayers() or iface is None:
            return None
        scale = float(iface.mapCanvas().scale())
    except Exception:  # noqa: BLE001
        return None
    return scale if scale > 0 and math.isfinite(scale) else None


def _attach_scale_range(out: dict, declared: dict | None, canvas_scale: float | None) -> None:






    if not declared:
        return
    row = dict(declared)
    if canvas_scale:
        low, high = row.get("min_scale_denominator"), row.get("max_scale_denominator")
        row["canvas_scale"] = int(round(canvas_scale))
        row["view_in_range"] = not ((low and canvas_scale < low) or (high and canvas_scale > high))
    out["scale_range"] = row


def _wmts_uri(url: str, layer: str, described: dict) -> tuple[str, dict] | None:

    row = described.get(layer)
    if not row or not row["sets"]:
        return None


    chosen = next((name for name in row["sets"] if _wmts_epsg(row["crs"].get(name, "")) == "EPSG:3857"),
                  row["sets"][0])
    crs = _wmts_epsg(row["crs"].get(chosen, ""))
    if not crs:




        return None, {"unresolved_crs": row["crs"].get(chosen, ""), "tile_matrix_set": chosen}
    img_format = next((f for f in row["formats"] if f.endswith("png")), (row["formats"] or ["image/png"])[0])
    style = (row["styles"] or ["default"])[0]
    uri = (f"url={encode_uri_url(_wmts_capabilities_url(url))}"
           f"&layers={encode_uri_url(layer)}"
           f"&styles={encode_uri_url(style)}"
           f"&format={encode_uri_url(img_format)}"
           f"&tileMatrixSet={encode_uri_url(chosen)}"
           f"&crs={crs}")
    return uri, {"tile_matrix_set": chosen, "crs": crs, "format": img_format, "style": style}


def _is_wmts(url: str) -> bool:

    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parts.query)
    service = next((v[0] for k, v in query.items() if k.lower() == "service"), "")
    return service.upper() == "WMTS" or "wmts" in parts.path.lower()


def _wms_built_here(uri: str, name: str):










    if on_main_thread():
        return None
    try:
        held = hold_from_worker()
    except TimeoutError:
        return None
    if not held:
        return None

    def make():
        layer = QgsRasterLayer(uri, name, "wms", worker_options(QgsRasterLayer))
        if layer.isValid():
            return layer




        delete_here(layer)
        return None

    try:
        return built_here(make)
    finally:
        release_from_worker()

        deferred = enum_member(QEvent, "Type", "DeferredDelete")
        QCoreApplication.sendPostedEvents(None, int(getattr(deferred, "value", deferred)))


def _wms_layer(ready, uri: str, name: str):


    layer = take(ready)
    if layer is not None:
        return layer
    with no_login_prompt():
        return QgsRasterLayer(uri, name, "wms")


def _drop_unused(ready) -> None:


    if ready is not None:
        ready.release()


def _add_wmts_layer(url: str, layer: str, name: str) -> dict:







    described = _wmts_describe(url)
    if not described:
        return {"_error": f"No answer from the WMTS at {url} that names any layer.",
                "code": "EXECUTION_FAILED",
                "suggestion": "inspect_data_source checks the URL."}
    built = _wmts_uri(url, layer, described)
    if built is None:
        close = [name for name in described if layer.lower() in name.lower()][:3]
        shown = close or list(described)[:_WFS_NAMES_SHOWN]
        more = "" if len(described) <= len(shown) else f", and {len(described) - len(shown)} more"
        return {"_error": f"The service does not publish a layer {layer!r}. It offers: "
                f"{', '.join(shown)}{more}.",
                "code": "INVALID_ARGS",
                "suggestion": "add_data again with one layer name from the list."}
    uri, chosen = built
    if uri is None:
        return {"_error": (f"The tile matrix set {chosen['tile_matrix_set']!r} of layer {layer!r} is published "
                           f"in {chosen['unresolved_crs']!r}, which this plugin cannot turn into a CRS QGIS "
                           f"knows, so the layer was not added."),
                "code": "EXECUTION_FAILED",
                "suggestion": ("A tile matrix set in an EPSG code works, or the service loads as a "
                               "WMS with add_wms_layer.")}
    ready = _wms_built_here(uri, name)

    def _create():
        made = _wms_layer(ready, uri, name)
        if not made.isValid():
            return {"_invalid": True}
        view = _canvas_scale()
        with view_kept():
            QgsProject.instance().addMapLayer(made)
        out = {"layer_name": made.name(), "layer_id": made.id(), "url": url,
               "wmts_layer": layer, **chosen}
        out.update(_stacked(made))
        if view:
            out["_canvas_scale"] = view
        return out

    try:
        out = run_on_main_thread(_create, timeout=30)
    finally:
        _drop_unused(ready)
    if out.get("_invalid"):
        return {"_error": f"The WMTS layer {layer!r} would not load from {url}.",
                "code": "EXECUTION_FAILED",
                "suggestion": (f"The service publishes it in {', '.join(described[layer]['sets'])}; "
                               "inspect_data_source checks the layer name.")}
    view = out.pop("_canvas_scale", None)
    body = _wmts_body(url)
    _attach_scale_range(out, ogc_inspect.wmts_scale_range(body, layer, chosen["tile_matrix_set"]) if body else None,
                        view)
    return out






_WCS_DESCRIBE_MAX_BYTES = 2 * 1024 * 1024
_WCS_TIMEOUT_S = 30
_TIFF_MAGIC = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")


def _add_wcs_layer(args: dict) -> dict:

    base = ogc_inspect.service_base(str(args.get("url") or ""))
    coverage = str(args.get("coverage") or "").strip()
    name = args.get("name") or coverage
    crs = str(args.get("crs") or "").strip()
    if crs:
        problem = crs_problem(crs)
        if problem:
            return {"_error": problem, "_code": "INVALID_ARGS",
                    "_suggestion": "The CRS goes on its own, without any other provider parameter."}

    net.check_url(base)
    if args.get("bbox"):
        return _wcs_extract(base, coverage, name, crs, args["bbox"])
    def _create():
        errors = []
        for candidate in _wcs_names(coverage):
            uri = (f"url={encode_uri_url(base)}&identifier={encode_uri_url(candidate)}"
                   + (f"&crs={crs}" if crs else ""))
            layer = QgsRasterLayer(uri, name, "wcs")
            if layer.isValid():
                break
            errors.append(layer.error().summary()[:300])
        else:
            return {"_invalid": errors[0] if errors else "no answer"}

        return {"handle": hand_over(layer), "candidate": candidate, "window": _wcs_sample_window(layer)}

    made = run_on_main_thread(_create, timeout=60)
    if "_invalid" in made:
        return {"_error": f"The WCS coverage {coverage!r} would not load from {base}: {made['_invalid']}",
                "_code": "EXECUTION_FAILED",
                "_suggestion": ("inspect_data_source checks the coverage name. QGIS reads WCS 1.0 and 1.1; a "
                                "service that only speaks 2.0 loads through bbox, or not at all.")}
    held = made["handle"]
    try:



        stretch = _wcs_sample_stretch(base, made["candidate"], made["window"])
        elevation = elevation_style.names_elevation(f"{name} {made['candidate']}", None)

        def _add():
            layer = held.take()
            if layer is None:
                return {"_error": "The call ended before the coverage could be added.", "code": "CANCELLED"}
            styled = ""
            if stretch and elevation:
                styled = elevation_style.apply_elevation_style(layer, stretch)
            if stretch and not styled:
                styled = elevation_style.apply_gray_stretch(layer, stretch)
            QgsProject.instance().addMapLayer(layer)
            return {"layer_name": layer.name(), "layer_id": layer.id(), "url": base, "provider": "wcs",
                    "layer": made["candidate"], **({"styled": styled} if styled else {}),
                    "_note": ("Streamed from the service: the values are real (raster_sample reads them), but "
                              "every read is a request. For slope, contours, hillshade or statistics add the "
                              "same coverage again with bbox, which writes that box to a local GeoTIFF.")}

        return run_on_main_thread(_add, timeout=30)
    finally:
        held.release()



_WCS_SAMPLE_PIXELS = 256
_WCS_SAMPLE_NATIVE_CELLS = 2048


def _wcs_sample_window(layer) -> dict | None:

    extent = layer.extent()
    if extent.isEmpty():
        return None
    target = extent
    try:
        from qgis.utils import iface as qgis_iface

        canvas = qgis_iface.mapCanvas() if qgis_iface is not None else None
        if canvas is not None:
            view = QgsCoordinateTransform(canvas.mapSettings().destinationCrs(), layer.crs(),
                                          QgsProject.instance()).transformBoundingBox(canvas.extent())
            if view.intersects(extent):
                target = view.intersect(extent)
    except Exception:  # noqa: BLE001
        target = extent
    half = abs(layer.rasterUnitsPerPixelX() or 0.0) * _WCS_SAMPLE_NATIVE_CELLS / 2.0
    centre = target.center()
    if half and (target.width() > 2 * half or target.height() > 2 * half):
        west, east = max(extent.xMinimum(), centre.x() - half), min(extent.xMaximum(), centre.x() + half)
        south, north = max(extent.yMinimum(), centre.y() - half), min(extent.yMaximum(), centre.y() + half)
    else:
        west, south, east, north = target.xMinimum(), target.yMinimum(), target.xMaximum(), target.yMaximum()
    if not (west < east and south < north):
        return None
    return {"bbox": [west, south, east, north], "crs": layer.crs().authid()}


def _wcs_sample_stretch(base: str, coverage: str, window: dict | None) -> dict | None:

    if not window or not window.get("crs", "").startswith("EPSG:"):
        return None
    described, coverage, _ = _wcs_describe(base, coverage)
    fmt = ogc_inspect.pick_tiff_format(described.get("formats") or [])
    if not fmt:
        return None
    west, south, east, north = window["bbox"]
    params = {"SERVICE": "WCS", "VERSION": "1.0.0", "REQUEST": "GetCoverage", "COVERAGE": coverage,
              "CRS": window["crs"], "BBOX": f"{west},{south},{east},{north}", "FORMAT": fmt,
              "WIDTH": _WCS_SAMPLE_PIXELS, "HEIGHT": _WCS_SAMPLE_PIXELS}
    request = urllib.request.Request(ogc_inspect.with_query(base, params), headers={"User-Agent": _USER_AGENT})
    cap = min(4 * 1024 * 1024, limits.current("MAX_DOWNLOAD_BYTES"))
    try:
        body = net.fetch(request, timeout=_WCS_TIMEOUT_S, max_bytes=cap, total_timeout=_WCS_TIMEOUT_S).body or b""
    except net.FetchCancelled:
        raise
    except (urllib.error.URLError, OSError) as exc:
        log_warning(f"WCS sample for the stretch of {coverage} failed: {exc}")
        return None
    if not body.startswith(_TIFF_MAGIC):
        return None
    from osgeo import gdal

    path = f"/vsimem/wcs_sample_{abs(hash((base, coverage, west, south)))}.tif"
    gdal.FileFromMemBuffer(path, body)
    try:
        return elevation_style.sample_stretch(path)
    finally:
        gdal.Unlink(path)


def _wcs_names(coverage: str) -> list[str]:







    return [coverage] + ([coverage.replace("__", ":", 1)] if "__" in coverage else [])


def _wcs_describe(base: str, coverage: str) -> tuple[dict, str, Exception | None]:

    described: dict = {}
    failure: Exception | None = None
    for candidate in _wcs_names(coverage):
        describe = ogc_inspect.with_query(base, {"SERVICE": "WCS", "VERSION": "1.0.0",
                                                 "REQUEST": "DescribeCoverage", "COVERAGE": candidate})
        try:
            answer = net.fetch(describe, timeout=_WCS_TIMEOUT_S, max_bytes=_WCS_DESCRIBE_MAX_BYTES,
                               total_timeout=_WCS_TIMEOUT_S * 2, cache_ttl=_CACHE_CATALOG_S)
        except net.FetchCancelled:
            raise
        except (urllib.error.URLError, OSError) as exc:
            failure = exc
            continue
        described = ogc_inspect.wcs10_description(answer.body or b"")
        if described.get("formats"):
            return described, candidate, None
    return described, coverage, failure


def _wcs_extract(base: str, coverage: str, name: str, crs: str, bbox) -> dict:







    try:
        west, south, east, north = (float(value) for value in bbox)
    except (TypeError, ValueError):
        return {"_error": "That bbox is not a usable box.", "_code": "INVALID_ARGS",
                "_suggestion": "west, south, east, north in EPSG:4326."}
    described, coverage, failure = _wcs_describe(base, coverage)
    if failure is not None and not described:
        return {"_error": f"The WCS at {base} did not describe {coverage!r}: {failure}", "_code": "EXECUTION_FAILED",
                "_suggestion": ("The coverage without bbox streams it; inspect_data_source checks the name.")}
    fmt = ogc_inspect.pick_tiff_format(described.get("formats") or [])
    if described.get("exception") or not fmt:
        reason = described.get("exception") or "it offers no GeoTIFF format"
        return {"_error": f"The WCS at {base} cannot hand {coverage!r} over as a GeoTIFF: {reason}",
                "_code": "EXECUTION_FAILED",
                "_suggestion": ("inspect_data_source checks the coverage name. The download speaks WCS 1.0.0; "
                                "without bbox the coverage is streamed instead.")}
    native = described.get("crs") or ""
    response_crs = crs or native or "EPSG:4326"
    resolution = described.get("resolution") or 0.0 if response_crs == native else 0.0
    params = {"SERVICE": "WCS", "VERSION": "1.0.0", "REQUEST": "GetCoverage", "COVERAGE": coverage,
              "CRS": "EPSG:4326", "BBOX": f"{west},{south},{east},{north}", "RESPONSE_CRS": response_crs,
              "FORMAT": fmt}
    width_m = abs(east - west) * 111_320 * max(math.cos(math.radians((north + south) / 2)), 0.01)
    height_m = abs(north - south) * 110_574
    if resolution:

        cells_x = abs(east - west) / resolution if resolution < 0.1 else width_m / resolution
        cells_y = abs(north - south) / resolution if resolution < 0.1 else height_m / resolution
        params.update({"RESX": resolution, "RESY": resolution})
    else:
        cells_x = 2048.0
        cells_y = max(1.0, round(2048.0 * height_m / max(width_m, 1.0)))
        params.update({"WIDTH": int(cells_x), "HEIGHT": int(cells_y)})
    ceiling = limits.current("MAX_DOWNLOAD_BYTES")
    if cells_x * cells_y * 4 > ceiling:
        return {"_error": (f"{coverage!r} over that box is about {int(cells_x)} x {int(cells_y)} cells, more than "
                           f"one download may read ({ceiling // (1024 * 1024)} MB)."),
                "_code": limits.CEILING_CODE,
                "_suggestion": limits._with_machine("A smaller box around the area of interest, or a coarser "
                                                    "coverage of the service, may fit.")}
    attempts = [params]
    if "RESX" in params:


        sized = {key: value for key, value in params.items() if key not in ("RESX", "RESY")}
        sized.update({"WIDTH": max(1, round(cells_x)), "HEIGHT": max(1, round(cells_y))})
        attempts.append(sized)
    body = b""
    for attempt in attempts:
        request = urllib.request.Request(ogc_inspect.with_query(base, attempt), headers={"User-Agent": _USER_AGENT})
        try:
            answer = net.fetch(request, timeout=_WCS_TIMEOUT_S * 2, max_bytes=ceiling,
                               total_timeout=_WCS_TIMEOUT_S * 6)
        except net.FetchCancelled:
            raise
        except (urllib.error.URLError, OSError) as exc:
            return {"_error": f"The GetCoverage of {coverage!r} from {base} failed: {exc}",
                    "_code": "EXECUTION_FAILED",
                    "_suggestion": "A smaller box, or the coverage without bbox, may stream it."}
        body = answer.body or b""
        if body.startswith(_TIFF_MAGIC):
            break
    if not body.startswith(_TIFF_MAGIC):


        reason = ogc_inspect.wcs10_description(body).get("exception")
        said = reason or body[:200].decode("utf-8", "replace")
        return {"_error": (f"The service did not return a GeoTIFF for {coverage!r} over a box of about "
                           f"{int(cells_x)} x {int(cells_y)} cells: {said}"),
                "_code": "EXECUTION_FAILED",
                "_suggestion": None if reason else "The box may not overlap it (wgs84_bbox in inspect_data_source)."}
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name or coverage)).strip("_")[:60] or "coverage"

    stem = _avoid_reserved_name(stem)
    path = os.path.join(create_managed_temp_dir("wcs"), f"{stem}.tif")
    try:
        with open(path, "wb") as handle:
            handle.write(body)
    except OSError as exc:
        return {"_error": f"Could not write the coverage to disk: {exc}", "_code": "EXECUTION_FAILED",
                "_suggestion": "Freeing space in the temp folder may help."}

    def _create():
        layer = QgsRasterLayer(path, name, "gdal")
        if not layer.isValid():
            return {"_error": "The GeoTIFF the service returned is not readable.", "_code": "EXECUTION_FAILED",
                    "_suggestion": "Another format or box may fit."}
        QgsProject.instance().addMapLayer(layer)
        return {"layer_name": layer.name(), "layer_id": layer.id(), "url": base, "provider": "gdal, local extract",
                "layer": coverage, "path": path, "bbox": [west, south, east, north], "size_bytes": len(body),
                "_note": ("The box was downloaded from the WCS as a GeoTIFF of the real values and is read from "
                          "this machine: slope, contours, hillshade and zonal statistics run on it directly.")}

    return run_on_main_thread(_create, timeout=60)











_WMS_CAPS_MAX_BYTES = 8 * 1024 * 1024
_WMS_CAPS_TTL_S = 900
_WMS_NAMES_SHOWN = 8
_WMS_SERVER_TEXT_CHARS = 240


_WMS_BLOCK_RE = re.compile(rb"<(?:\w+:)?(Style|Service)\b.*?</(?:\w+:)?\1>", re.DOTALL | re.IGNORECASE)
_WMS_NAME_RE = re.compile(rb"<(?:\w+:)?Name>\s*([^<]+?)\s*</(?:\w+:)?Name>")
_WMS_EXCEPTION_RE = re.compile(rb"<(?:\w+:)?(?:ServiceException|ExceptionText)[^>]*>\s*(.*?)\s*</", re.DOTALL)
_MARKUP_RE = re.compile(rb"<[^>]*>")


def _server_words(body: bytes) -> str:

    text = _MARKUP_RE.sub(b" ", body[:4000]).decode("utf-8", "replace")
    return " ".join(text.split())[:_WMS_SERVER_TEXT_CHARS]


def _wms_capabilities(url: str) -> tuple[bytes, str, bool]:








    query = {"SERVICE": "WMS", "REQUEST": "GetCapabilities", "VERSION": "1.3.0"}
    probe = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
    try:
        answer = net.fetch(probe, timeout=20, max_bytes=_WMS_CAPS_MAX_BYTES,
                           total_timeout=25, cache_ttl=_WMS_CAPS_TTL_S)
    except urllib.error.HTTPError as exc:
        return b"", f"it answered HTTP {exc.code} to a GetCapabilities request", True
    except Exception as exc:  # noqa: BLE001
        log_warning(f"WMS capabilities probe failed for {url}: {exc}")
        return b"", f"it could not be reached: {exc}", False
    return answer.body or b"", "", True


def _wms_layer_names(body: bytes) -> list[str]:

    names: list[str] = []
    for found in _WMS_NAME_RE.finditer(_WMS_BLOCK_RE.sub(b"", body)):
        name = found.group(1).decode("utf-8", "replace").strip()
        if name and name not in names:
            names.append(name)
    return names


def _wms_failure(url: str, wanted: list[str], qgis_message: str = "") -> dict:

    detail = f" QGIS said: {qgis_message.strip()}" if (qgis_message or "").strip() else ""
    body, why, _answered = _wms_capabilities(url)
    if why:
        return tool_error(f"The WMS at {url} did not load: {why}.{detail}",
                          code="EXECUTION_FAILED", hint="wms_unreachable")
    exception = _WMS_EXCEPTION_RE.search(body)
    if exception:
        return tool_error(
            f"The WMS at {url} answered with a service exception: {_server_words(exception.group(1))}",
            code="EXECUTION_FAILED", hint="wms_service_exception")
    names = _wms_layer_names(body)
    if not names:
        return tool_error(
            f"{url} answered, but not with WMS capabilities. The server said: {_server_words(body)}",
            code="EXECUTION_FAILED", hint="wms_not_capabilities")
    missing = [one for one in wanted if one not in names]
    if missing:
        shown = ", ".join(names[:_WMS_NAMES_SHOWN])
        more = "" if len(names) <= _WMS_NAMES_SHOWN else f", and {len(names) - _WMS_NAMES_SHOWN} more"
        return tool_error(
            f"The WMS at {url} does not publish {', '.join(repr(one) for one in missing)}. "
            f"It offers: {shown}{more}.",
            code="EXECUTION_FAILED", hint="wms_layer_missing")
    return tool_error(
        f"{url} publishes {', '.join(wanted)}, but QGIS could not build the layer from it.{detail}",
        code="EXECUTION_FAILED", hint="wms_build_failed")


def _add_wms_layer(args: dict) -> dict:
    url = clean_service_url(args["url"])
    layers = args["layers"]
    name = args.get("name") or f"WMS - {layers}"
    crs = args.get("crs", "EPSG:4326")
    img_format = args.get("format", "image/png")
    problem = crs_problem(crs)
    if problem:
        return {"_error": problem, "_code": "INVALID_ARGS",
                "_suggestion": "The CRS goes on its own, without any other provider parameter."}
    if _is_wmts(url):
        return _add_wmts_layer(ogc_inspect.service_base(links.clean(url)), layers,
                               args.get("name") or f"WMTS - {layers}")



    url = ogc_inspect.service_base(links.clean(url))








    names = [part.strip() for part in str(layers).split(",") if part.strip()] or [str(layers)]
    uri = (
        f"url={encode_uri_url(url)}"
        + "".join(f"&layers={encode_uri_url(part)}&styles=" for part in names)
        + f"&crs={crs}"
        f"&format={encode_uri_url(img_format)}"
    )








    body, silent_why, answered = _wms_capabilities(url)
    if not answered:
        return tool_error(f"The WMS at {url} did not load: {silent_why}.",
                          code=net.NETWORK_ERROR, suggestion=net.NETWORK_SUGGESTION)
    ready = _wms_built_here(uri, name)

    def _create():
        layer = _wms_layer(ready, uri, name)
        if not layer.isValid():


            try:
                said = str(layer.error().summary() or "")
            except Exception:  # noqa: BLE001
                said = ""
            return {"_wms_invalid": True, "qgis_message": said}
        view = _canvas_scale()
        with view_kept():
            QgsProject.instance().addMapLayer(layer)
        out = {"layer_name": layer.name(), "layer_id": layer.id(), "url": url, "wms_layers": layers}
        out.update(_stacked(layer))
        if view:
            out["_canvas_scale"] = view
        return out

    try:
        made = run_on_main_thread(_create, timeout=30)
    finally:
        _drop_unused(ready)
    if isinstance(made, dict) and made.get("_wms_invalid"):
        return _wms_failure(url, names, made.get("qgis_message") or "")
    if isinstance(made, dict) and not made.get("_error"):
        view = made.pop("_canvas_scale", None)



        _attach_scale_range(made, ogc_inspect.wms_scale_range(body, names) if body else None, view)
    return made



















WFS_WARN_FEATURES = 50_000
WFS_WIRE_BYTES_PER_FEATURE = 150





WFS_SILENT_PAGES = (100, 500, 1_000, 2_000, 5_000, 10_000, 25_000)


def _wfs_silent_pages() -> tuple:







    return tuple(tuning.service_list("wfs_silent_pages", WFS_SILENT_PAGES))


_WFS_HITS_MAX_BYTES = 64 * 1024
_WFS_HITS_TTL_S = 300
_WFS_MATCHED_RE = re.compile(rb'numberMatched\s*=\s*"(\d+)"')


_WFS_TYPENAME_RE = re.compile(rb"<(?:\w+:)?Name>\s*([^<\s][^<]*?)\s*</(?:\w+:)?Name>")



_WFS_CAPS_MAX_BYTES = 12 * 1024 * 1024
_WFS_CAPS_TTL_S = 900
_WFS_NAMES_SHOWN = 12





_WFS_CAPS_QUERIES = (
    {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetCapabilities"},
    {"SERVICE": "WFS", "ACCEPTVERSIONS": "2.0.0,1.1.0,1.0.0", "REQUEST": "GetCapabilities"},
    {"SERVICE": "WFS", "VERSION": "1.1.0", "REQUEST": "GetCapabilities"},
)
_WFS_VERSION_RE = re.compile(rb"<(?:\w+:)?WFS_Capabilities\b[^>]*?\sversion\s*=\s*[\"']([\d.]+)[\"']")


def _wfs_capabilities(url: str) -> tuple[bytes, str]:





    for query in _WFS_CAPS_QUERIES:
        probe = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
        try:
            answer = net.fetch(probe, timeout=20, max_bytes=_WFS_CAPS_MAX_BYTES,
                               total_timeout=25, cache_ttl=_WFS_CAPS_TTL_S)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"WFS capabilities probe failed for {probe}: {exc}")
            continue
        body = answer.body or b""
        if _WFS_TYPE_BLOCK_RE.search(body):
            found = _WFS_VERSION_RE.search(body)
            return body, found.group(1).decode("ascii", "replace") if found else query.get("VERSION", "")
    return b"", ""


def _wfs_typenames(url: str) -> list[str]:





    names: list[str] = []
    for block in _WFS_TYPE_BLOCK_RE.finditer(_wfs_capabilities(url)[0]):
        found = _WFS_TYPENAME_RE.search(block.group(1))
        name = found.group(1).decode("utf-8", "replace").strip() if found else ""
        if name and name not in names:
            names.append(name)
    return names


_WFS_TYPE_BLOCK_RE = re.compile(rb"<(?:\w+:)?FeatureType\b[^>]*>(.*?)</(?:\w+:)?FeatureType>", re.DOTALL)

_WFS_CRS_RE = re.compile(rb"<(?:\w+:)?(?:Default|Other)?(?:CRS|SRS)>\s*([^<\s][^<]*?)\s*</")
_WFS_CRS_SHOWN = 6


def _wfs_type_crs(url: str, typename: str) -> list[str]:





    wanted = typename.strip()
    for block in _WFS_TYPE_BLOCK_RE.finditer(_wfs_capabilities(url)[0]):
        body = block.group(1)
        found = _WFS_TYPENAME_RE.search(body)
        if not found:
            continue
        if found.group(1).decode("utf-8", "replace").strip() != wanted:
            continue
        seen: list[str] = []
        for one in _WFS_CRS_RE.finditer(body):

            text = one.group(1).decode("utf-8", "replace").strip()
            code = ogc_inspect.epsg_of(text) or text
            if code and code not in seen:
                seen.append(code)
        return seen
    return []


def _wfs_failure(url: str, typename: str, crs: str, qgis_message: str,
                 hits: int | None = None) -> str:












    detail = (qgis_message or "").strip()
    names = _wfs_typenames(url)
    if names and typename not in names:


        close = [n for n in names if typename.split(":")[-1].lower() in n.lower()][:_WFS_NAMES_SHOWN]
        shown = close or names[:_WFS_NAMES_SHOWN]
        more = "" if len(names) <= len(shown) else f", and {len(names) - len(shown)} more"
        return (f"The service does not publish a type name {typename!r}. It offers: "
                f"{', '.join(shown)}{more}. add_wfs_layer with one of those loads it."
                + (f" QGIS said: {detail}" if detail else ""))
    if names:
        tail = detail if detail.endswith((".", "!", "?")) else detail + "."
        head = (f"{typename} exists on this service but the layer would not load"
                + (f": {tail}" if detail else "."))





        offered = _wfs_type_crs(url, typename)
        if offered and crs not in offered:
            shown = ", ".join(offered[:_WFS_CRS_SHOWN])
            more = "" if len(offered) <= _WFS_CRS_SHOWN else f", and {len(offered) - _WFS_CRS_SHOWN} more"
            return (f"{head} It does not publish {crs}. Its CRS are: {shown}{more}. "
                    f"add_wfs_layer with crs={offered[0]!r} loads it.")
        if hits is not None and hits > WFS_WARN_FEATURES:
            return (f"{head} The type holds {hits:,} features and the whole of it was asked for. "
                    f"The canvas zoomed to the area of interest, called again, or "
                    f"max_features up to {limits.current('MAX_FEATURES_PER_CALL'):,}, gives a sample."
                    + (f" {crs} is offered, so the CRS is not the problem." if offered else ""))





        return (f"{head} A smaller max_features may fit"
                + (f"; {crs} is offered, so the CRS is not the problem." if offered else
                   f", or a srsname other than {crs}."))
    return (f"No answer from the WFS at {url} that names any type. "
            + (f"QGIS said: {detail}. " if detail else "")
            + "inspect_data_source checks the URL.")


def _wfs_hits(url: str, typename: str, crs: str) -> int | None:







    query = {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
             "TYPENAMES": typename, "RESULTTYPE": "hits", "SRSNAME": crs}
    probe = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
    try:
        answer = net.fetch(probe, timeout=20, max_bytes=_WFS_HITS_MAX_BYTES,
                           total_timeout=25, cache_ttl=_WFS_HITS_TTL_S)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"WFS hits probe failed for {typename}: {exc}")
        return None
    found = _WFS_MATCHED_RE.search(answer.body or b"")
    if not found:
        return None
    try:
        return int(found.group(1))
    except ValueError:
        return None


def _wfs_restrict_to_view(hits: int, max_features: int | None = None) -> bool:













    return hits > WFS_WARN_FEATURES or (max_features is not None and hits > max_features)


def _bbox_ring_filter(west: float, south: float, east: float, north: float) -> str:











    ring = f"{west} {south},{east} {south},{east} {north},{west} {north},{west} {south}"
    return f"intersects_bbox($geometry, geom_from_wkt('POLYGON(({ring}))'))"


def _wfs_area(bbox, crs="EPSG:4326"):







    if bbox in (None, "", [], {}):
        return ""
    try:
        if isinstance(bbox, dict):
            west, south, east, north = (float(bbox[k]) for k in ("xmin", "ymin", "xmax", "ymax"))
        else:
            west, south, east, north = (float(v) for v in bbox)
    except (KeyError, TypeError, ValueError):
        return {"_error": f"bbox must be [west, south, east, north] in EPSG:4326, got {bbox!r}.",
                "code": "INVALID_ARGS", "suggestion": "bbox is four numbers in degrees, or left out."}
    wrong = volume_guard.not_degrees(west, south, east, north)
    if wrong:
        return {"_error": wrong, "code": "INVALID_ARGS",
                "suggestion": "The box is in EPSG:4326 degrees: west, south, east, north."}
    if not (west < east and south < north):
        return {"_error": "bbox must have west < east and south < north.", "code": "INVALID_ARGS",
                "suggestion": "bbox: west, south, east, north, in degrees."}
    dst = QgsCoordinateReferenceSystem(crs or "EPSG:4326")
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    if dst.isValid() and dst != wgs84:
        try:


            box = QgsCoordinateTransform(wgs84, dst, QgsCoordinateTransformContext()).transformBoundingBox(
                QgsRectangle(west, south, east, north))
        except Exception as exc:  # noqa: BLE001
            return {"_error": f"bbox cannot be expressed in {crs}: {exc}", "code": "INVALID_ARGS",
                    "suggestion": "A box inside the area the crs covers, or crs left out."}
        west, south, east, north = box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()
    return _bbox_ring_filter(west, south, east, north)


def _wfs_where(where) -> str | dict:










    from qgis.PyQt.QtXml import QDomDocument

    text = str(where or "").strip()
    if not text:
        return ""
    expression = QgsExpression(text)
    if expression.hasParserError():
        return tool_error(f"where is not an expression QGIS can read: {expression.parserErrorString().strip()}",
                          "INVALID_ARGS",
                          "where is a QGIS expression on the type's own fields, field names in double quotes "
                          "and text in single quotes: \"height\" > 30 AND \"use\" = 'industrial'.")
    if QgsOgcUtils.expressionToOgcFilter(expression, QDomDocument()).isNull():
        return tool_error("QGIS has no OGC filter for this where, so the service cannot be asked it; nothing was "
                          "loaded.", "INVALID_ARGS",
                          "=, <>, <, >, AND, OR, NOT, IN, LIKE, ILIKE, IS NULL and + - * / on the type's fields "
                          "have an OGC form, and other functions go to the service by name; BETWEEN, NOT LIKE, "
                          "CASE, $area, $length and the other $ values, and the %, ^, //, || and ~ operators "
                          "have none.")
    return text


def _canvas_extent_in(crs: str) -> list[float] | None:










    try:
        from qgis.utils import iface as qgis_iface

        canvas = qgis_iface.mapCanvas() if qgis_iface is not None else None
        if canvas is None:
            return None
        extent = canvas.extent()
        if extent.isEmpty():
            return None
        src = canvas.mapSettings().destinationCrs()
        dst = QgsCoordinateReferenceSystem(crs)
        if not dst.isValid():
            return None
        if src.isValid() and src != dst:
            extent = QgsCoordinateTransform(src, dst, QgsProject.instance()).transformBoundingBox(extent)
        box = [extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()]
        if any(not math.isfinite(v) for v in box):
            return None
        return box
    except Exception:  # noqa: BLE001
        return None


def _add_wfs_layer(args: dict) -> dict:

    url = ogc_inspect.service_base(links.clean(args["url"]))
    typename = args["typename"]
    name = args.get("name") or f"WFS - {typename}"
    crs = args.get("crs", "EPSG:4326")
    ceiling = limits.current("MAX_FEATURES_PER_CALL")
    area = _wfs_area(args.get("bbox"), crs)
    if isinstance(area, dict):
        return area
    where = _wfs_where(args.get("where"))
    if isinstance(where, dict):
        return where


    lifted = volume_guard.lifted(args)
    default_cap = 1000
    try:





        max_features = max(1, min(int(args.get("max_features") or default_cap), ceiling))
    except (TypeError, ValueError):
        return {"_error": f"max_features must be a whole number from 1 to "
                f"{ceiling}, got {args.get('max_features')!r}.",
                "code": "INVALID_ARGS",
                "suggestion": "max_features is a number; left out, it defaults to 1000."}

    problem = crs_problem(crs)
    if problem:
        return {"_error": problem, "_code": "INVALID_ARGS",
                "_suggestion": "The CRS goes on its own, without any other provider parameter."}


    net.check_url(url)
    hits = _wfs_hits(url, typename, crs)
    whole = hits
    if area or where:


        hits = None








    view_filter = ""
    if not lifted and not area and (where or (whole is not None and _wfs_restrict_to_view(whole, max_features))):
        canvas_box = run_on_main_thread(_canvas_extent_in, crs, timeout=10)
        if isinstance(canvas_box, list):
            west, south, east, north = canvas_box
            if west < east and south < north:
                view_filter = _bbox_ring_filter(west, south, east, north)







    source = QgsDataSourceUri()
    source.setParam("url", url)
    source.setParam("typename", typename)
    source.setParam("srsname", crs)
    source.setParam("version", "2.0.0")
    if not lifted:
        source.setParam("maxNumFeatures", str(max_features))



    narrowing = area or ("" if where else view_filter)
    parts = [part for part in (narrowing, where) if part]
    if parts:
        source.setParam("filter", parts[0] if len(parts) == 1 else " AND ".join(f"({part})" for part in parts))



    from .data_wfs_extract import extract

    def _copy() -> dict:
        return extract(url, typename, name, source.uri(False),
                       count_at=max_features if hits is None and not lifted else None,
                       view=(view_filter, min(WFS_WARN_FEATURES, max_features)) if where and view_filter else None)

    out = _copy()
    if out.get("_invalid"):


        spoken = _wfs_capabilities(url)[1]
        if spoken and spoken != "2.0.0":
            source.removeParam("version")
            source.setParam("version", spoken)
            out = _copy()
    if out.get("_invalid"):
        return {"_error": _wfs_failure(url, typename, crs, out.get("_qgis_message") or "", hits)}
    fields = out.pop("_fields", None)
    if out.get("_error"):
        if where and fields:

            out["suggestion"] = f"The request's where was: {where}. {typename}'s fields: {', '.join(fields)}."
        return out
    out.update({"url": url, "typename": typename, "provider": "WFS written to a GeoPackage"})
    if where:
        out["where"] = where
    if area:
        out["bbox"] = [round(v, 6) for v in args["bbox"]] if isinstance(args.get("bbox"), (list, tuple)) \
            else args.get("bbox")
    if (area or where) and whole is not None:
        out["features_in_type"] = whole
    count = out.get("feature_count")
    matched = out.pop("_matched", None)
    ended_by = out.pop("_ended_by", "")
    broken = out.pop("_service_error", "")
    counted, in_view = out.pop("_where_count", None), out.pop("_in_view", False)
    if where and view_filter:


        view_filter = view_filter if in_view else ""
        hits = counted if isinstance(counted, int) and counted > 0 else None
    warning = suggestion = ""
    if not lifted and hits is not None:
        out["features_available"] = hits
        out["estimated_bytes"] = hits * WFS_WIRE_BYTES_PER_FEATURE
    if not lifted and (hits is not None or view_filter):
        out["restricted_to_view"] = bool(view_filter)
    if hits is None and not view_filter and isinstance(matched, int) and isinstance(count, int) and matched > count:



        hits = out["features_available"] = matched
    if ended_by:
        out["coverage"] = "partial"
        why = ("the clock ran out" if ended_by == "clock"
               else "the disk cap or the free space was reached")
        warning = (f"Only {count:,} features were written before {why}, "
                   "the first ones in the service's own order, which can all lie in one part of the area.")
        suggestion = ("A smaller bbox per call, with the same full_extent, reads the rest." if lifted
                      else "A smaller bbox per call reads the rest.")
    elif broken:
        out["coverage"] = "partial"
        warning = f"The service broke the download off after {count:,} features. QGIS said: {broken}"
    elif lifted:
        if isinstance(hits, int) and hits > 0 and isinstance(count, int) and count < hits:
            out["features_available"] = hits
            out["coverage"] = "partial"
            warning = (f"{count:,} of the {hits:,} features the service counts came back: "
                       "it stops a request there and does not page past it.")
            suggestion = "Smaller boxes with bbox read the rest of the type."
    else:
        warning, suggestion = _capped_words(out, url, typename, crs, count, hits, max_features, ceiling,
                                            view_filter, where)
    if where and count == 0 and not warning:
        place = " inside the box" if area else (" under the map view" if view_filter else "")
        warning = f"The service returned no feature of {typename} matching the where{place}; the layer is empty."
    if warning:
        out["warning"] = warning
    if suggestion:
        out["suggestion"] = suggestion
    return out


_WHERE_WORDS = (" add_data with where, an attribute filter the service applies, reads only the matching "
                "features, and max_features counts those.")


def _capped_words(out: dict, url: str, typename: str, crs: str, count, hits, max_features: int,
                  ceiling: int, view_filter: str, where: str = "") -> tuple[str, str]:




    warning = suggestion = ""
    if view_filter:



        if where:
            matches = (f"{hits:,} features of this type name match it" if hits
                       else "the service gave no count of its matches")
            warning = (f"Only the features matching the where under the map view are fetched, up to "
                       f"{max_features:,}: {matches}. An expression filter, get_features or a Processing run "
                       "sees nothing outside the current view.")
        else:
            warning = (f"Only the features under the map view are fetched, up to {max_features:,}: this "
                       f"type name has {hits:,} of them. An expression filter, get_features or a Processing "
                       "run sees nothing outside the current view, however right the field name is.")
        suggestion = ("The layer is a local copy of that view: a zoom or a pan fetches nothing more, and "
                      "set_layer_filter filters the features it holds and asks the service nothing. "
                      "add_data with bbox [west, south, east, north] reads another area from the service.")
        suggestion += "" if where else _WHERE_WORDS




    silent_page = (hits is None and isinstance(count, int)
                   and count in _wfs_silent_pages() and count < max_features)


    if not view_filter and isinstance(count, int) and count > 0 and (
            (count == max_features and (hits is None or hits > count))
            or silent_page or (hits is not None and count < hits)):




        out["truncated"] = True
        if hits is not None:
            warning = (f"Only {count:,} of the {hits:,} features were loaded, the first {count:,} in the "
                       "service's own order, which can all lie in one part of its coverage: a filter, a "
                       "selection or a Processing run on this layer sees those alone.")
        elif silent_page:
            warning = (f"Exactly {count:,} features came back for a request that asked for "
                       f"{max_features:,}, and the service published no total: {count:,} is this "
                       "service's own page, so the layer is probably a fraction of the answer.")
        else:
            warning = (f"Exactly {count:,} features came back, which is the request cap: the layer "
                       "is probably truncated.")
        suggestion = (f"add_data with bbox [west, south, east, north] around the area needed reads that box "
                      f"from the service, which answers it alone, up to {ceiling:,} features for one layer; "
                      "more than that needs a smaller box, not a larger number. The layer is a local copy: "
                      "set_layer_filter filters the features it holds and asks the service nothing.")
        suggestion += "" if where else _WHERE_WORDS
    if view_filter and count == 0 and hits:















        offered = [code for code in _wfs_type_crs(url, typename) if code != crs]
        out["empty_in_view"] = True
        warning = (f"The layer loaded and holds nothing: the service returned no feature under the "
                   f"map view, although this type name has {hits:,} in total.")
        if where:
            warning = (f"The layer loaded and holds nothing: the service returned no feature matching the "
                       f"where under the map view, although {hits:,} features of this type name match it.")
        suggestion = (f"Either the view is over ground this type does not cover, or the service "
                      f"disagrees about a bbox in {crs}. A view over an area it covers, or "
                      f"the CRS the service uses natively, answers"
                      + (f": it also offers {', '.join(offered[:_WFS_CRS_SHOWN])}." if offered else "."))
    return warning, suggestion
