# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""OGC services: WMTS, WCS, WMS and WFS layers, with their describe and volume guards."""

from __future__ import annotations

import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from qgis.core import QgsCoordinateTransform, QgsDataSourceUri, QgsProject, QgsRasterLayer, QgsVectorLayer

from ..core import limits, links, net, tuning
from ..core.follow import view_kept
from ..core.layer_order import mark_truncated_count
from ..core.logger import log_warning
from ..core.policy import create_managed_temp_dir
from ..core.provider_uri import crs_problem, encode_uri_url
from . import elevation_style, ogc_inspect
from .data_basemaps import _stacked
from .data_common import _CACHE_CATALOG_S, _USER_AGENT, _run_on_main_thread






_WMTS_CAPS_MAX_BYTES = 8 * 1024 * 1024
_WMTS_CAPS_TTL_S = 900
_wmts_capabilities_url = ogc_inspect.wmts_capabilities_url
_wmts_epsg = ogc_inspect.epsg_of


def _wmts_describe(url: str) -> dict[str, dict]:
    """``{layer id: {sets, formats, styles}}`` for a WMTS, empty when it will not describe itself."""




    try:
        answer = net.fetch(_wmts_capabilities_url(url), timeout=20, max_bytes=_WMTS_CAPS_MAX_BYTES,
                           total_timeout=25, cache_ttl=_WMTS_CAPS_TTL_S)
    except Exception as exc:  # noqa: BLE001 - a service that will not describe itself still reports
        log_warning(f"WMTS capabilities probe failed for {url}: {exc}")
        return {}
    return ogc_inspect.wmts_layers(answer.body or b"")


def _wmts_uri(url: str, layer: str, described: dict) -> tuple[str, dict] | None:
    """The provider URI for one WMTS layer, and what was chosen, or None when it is not there."""
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
           f"&layers={urllib.parse.quote(layer)}"
           f"&styles={urllib.parse.quote(style)}"
           f"&format={urllib.parse.quote(img_format)}"
           f"&tileMatrixSet={urllib.parse.quote(chosen)}"
           f"&crs={crs}")
    return uri, {"tile_matrix_set": chosen, "crs": crs, "format": img_format, "style": style}


def _is_wmts(url: str) -> bool:
    """Whether this address is a WMTS rather than a WMS."""
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parts.query)
    service = next((v[0] for k, v in query.items() if k.lower() == "service"), "")
    return service.upper() == "WMTS" or "wmts" in parts.path.lower()


def _add_wmts_layer(url: str, layer: str, name: str) -> dict:
    """Add one layer of a WMTS, configured from what the service says about it."""






    described = _wmts_describe(url)
    if not described:
        return {"_error": f"No answer from the WMTS at {url} that names any layer.",
                "code": "EXECUTION_FAILED",
                "suggestion": "Check the URL with inspect_data_source."}
    built = _wmts_uri(url, layer, described)
    if built is None:
        close = [name for name in described if layer.lower() in name.lower()][:3]
        shown = close or list(described)[:_WFS_NAMES_SHOWN]
        more = "" if len(described) <= len(shown) else f", and {len(described) - len(shown)} more"
        return {"_error": f"The service does not publish a layer {layer!r}. It offers: "
                f"{', '.join(shown)}{more}.",
                "code": "INVALID_ARGS",
                "suggestion": "Call add_data again with one of those layer names."}
    uri, chosen = built
    if uri is None:
        return {"_error": (f"The tile matrix set {chosen['tile_matrix_set']!r} of layer {layer!r} is published "
                           f"in {chosen['unresolved_crs']!r}, which this plugin cannot turn into a CRS QGIS "
                           f"knows, so the layer was not added."),
                "code": "EXECUTION_FAILED",
                "suggestion": ("Pick a tile matrix set the service publishes in an EPSG code, or load the "
                               "service as a WMS with add_wms_layer.")}

    def _create():
        made = QgsRasterLayer(uri, name, "wms")
        if not made.isValid():
            return {"_invalid": True}
        with view_kept():
            QgsProject.instance().addMapLayer(made)
        out = {"layer_name": made.name(), "layer_id": made.id(), "url": url,
               "wmts_layer": layer, **chosen}
        out.update(_stacked(made))
        return out

    out = _run_on_main_thread(_create, timeout=30)
    if out.get("_invalid"):
        return {"_error": f"The WMTS layer {layer!r} would not load from {url}.",
                "code": "EXECUTION_FAILED",
                "suggestion": (f"The service publishes it in {', '.join(described[layer]['sets'])}. "
                               "Check the layer name with inspect_data_source.")}
    return out






_WCS_DESCRIBE_MAX_BYTES = 2 * 1024 * 1024
_WCS_TIMEOUT_S = 30
_TIFF_MAGIC = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")


def _add_wcs_layer(args: dict) -> dict:
    """One coverage of a WCS: streamed without a bbox, a local GeoTIFF of the box with one."""
    base = ogc_inspect.service_base(str(args.get("url") or ""))
    coverage = str(args.get("coverage") or "").strip()
    name = args.get("name") or coverage
    crs = str(args.get("crs") or "").strip()
    if crs:
        problem = crs_problem(crs)
        if problem:
            return {"_error": problem, "_code": "INVALID_ARGS",
                    "_suggestion": "Pass the CRS on its own, without any other provider parameter."}

    net.check_url(base)
    if args.get("bbox"):
        return _wcs_extract(base, coverage, name, crs, args["bbox"])
    def _create():
        errors = []
        for candidate in _wcs_names(coverage):
            uri = (f"url={encode_uri_url(base)}&identifier={urllib.parse.quote(candidate)}"
                   + (f"&crs={crs}" if crs else ""))
            layer = QgsRasterLayer(uri, name, "wcs")
            if layer.isValid():
                break
            errors.append(layer.error().summary()[:300])
        else:
            return {"_invalid": errors[0] if errors else "no answer"}
        return {"layer": layer, "candidate": candidate, "window": _wcs_sample_window(layer)}

    made = _run_on_main_thread(_create, timeout=60)
    if "_invalid" in made:
        return {"_error": f"The WCS coverage {coverage!r} would not load from {base}: {made['_invalid']}",
                "_code": "EXECUTION_FAILED",
                "_suggestion": ("Check the coverage name with inspect_data_source. QGIS reads WCS 1.0 and 1.1: a "
                                "service that only speaks 2.0 loads through bbox, or not at all.")}



    stretch = _wcs_sample_stretch(base, made["candidate"], made["window"])
    layer = made["layer"]
    elevation = elevation_style.names_elevation(f"{name} {made['candidate']}", None)

    def _add():
        styled = ""
        if stretch and elevation:
            styled = elevation_style.apply_elevation_style(layer, stretch)
        if stretch and not styled:
            styled = elevation_style.apply_gray_stretch(layer, stretch)
        QgsProject.instance().addMapLayer(layer)
        return {"layer_name": layer.name(), "layer_id": layer.id(), "url": base, "provider": "wcs",
                "layer": made["candidate"], **({"styled": styled} if styled else {}),
                "_note": ("Streamed from the service: the values are real (raster_sample reads them), but every "
                          "read is a request. For slope, contours, hillshade or statistics add the same coverage "
                          "again with bbox, which writes that box to a local GeoTIFF.")}

    return _run_on_main_thread(_add, timeout=30)



_WCS_SAMPLE_PIXELS = 256
_WCS_SAMPLE_NATIVE_CELLS = 2048


def _wcs_sample_window(layer) -> dict | None:
    """The part of a streamed coverage worth sampling, in the layer CRS: the canvas, else the centre. Main thread."""
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
    except Exception:  # noqa: BLE001 - no canvas, or one that will not transform: the centre will do
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
    """The 2-98 % cut of a small GetCoverage over *window*, or None. Off the main thread."""
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
    """The coverage as named, then as WCS 1.0 names it on GeoServer."""






    return [coverage] + ([coverage.replace("__", ":", 1)] if "__" in coverage else [])


def _wcs_describe(base: str, coverage: str) -> tuple[dict, str, Exception | None]:
    """The 1.0.0 description of the first spelling of *coverage* the service knows, and that spelling."""
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
    """The box of one coverage as a local GeoTIFF, from one WCS 1.0.0 GetCoverage."""






    try:
        west, south, east, north = (float(value) for value in bbox)
    except (TypeError, ValueError):
        return {"_error": "That bbox is not a usable box.", "_code": "INVALID_ARGS",
                "_suggestion": "Pass [west, south, east, north] in EPSG:4326."}
    described, coverage, failure = _wcs_describe(base, coverage)
    if failure is not None and not described:
        return {"_error": f"The WCS at {base} did not describe {coverage!r}: {failure}", "_code": "EXECUTION_FAILED",
                "_suggestion": ("Add the coverage without bbox to stream it, or check the name with "
                                "inspect_data_source.")}
    fmt = ogc_inspect.pick_tiff_format(described.get("formats") or [])
    if described.get("exception") or not fmt:
        reason = described.get("exception") or "it offers no GeoTIFF format"
        return {"_error": f"The WCS at {base} cannot hand {coverage!r} over as a GeoTIFF: {reason}",
                "_code": "EXECUTION_FAILED",
                "_suggestion": ("Check the coverage name with inspect_data_source. The download speaks WCS 1.0.0; "
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
                "_suggestion": limits._with_machine("Ask for a smaller box around the area of interest, or a "
                                                    "coarser coverage of the same service.")}
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
                    "_suggestion": "Try a smaller box, or add the coverage without bbox to stream it."}
        body = answer.body or b""
        if body.startswith(_TIFF_MAGIC):
            break
    if not body.startswith(_TIFF_MAGIC):
        said = ogc_inspect.wcs10_description(body).get("exception") or body[:200].decode("utf-8", "replace")
        return {"_error": f"The service did not return a GeoTIFF for {coverage!r}: {said}",
                "_code": "EXECUTION_FAILED",
                "_suggestion": "Check the box overlaps the coverage (wgs84_bbox in inspect_data_source)."}
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name or coverage)).strip("_")[:60] or "coverage"
    path = os.path.join(create_managed_temp_dir("wcs"), f"{stem}.tif")
    try:
        with open(path, "wb") as handle:
            handle.write(body)
    except OSError as exc:
        return {"_error": f"Could not write the coverage to disk: {exc}", "_code": "EXECUTION_FAILED",
                "_suggestion": "Free some space in the temp folder and try again."}

    def _create():
        layer = QgsRasterLayer(path, name, "gdal")
        if not layer.isValid():
            return {"_error": "The GeoTIFF the service returned is not readable.", "_code": "EXECUTION_FAILED",
                    "_suggestion": "Try another format or a smaller box."}
        QgsProject.instance().addMapLayer(layer)
        return {"layer_name": layer.name(), "layer_id": layer.id(), "url": base, "provider": "gdal, local extract",
                "layer": coverage, "path": path, "bbox": [west, south, east, north], "size_bytes": len(body),
                "_note": ("The box was downloaded from the WCS as a GeoTIFF of the real values and is read from "
                          "this machine: slope, contours, hillshade and zonal statistics run on it directly.")}

    return _run_on_main_thread(_create, timeout=60)


def _add_wms_layer(args: dict) -> dict:
    url = args["url"]
    layers = args["layers"]
    name = args.get("name") or f"WMS - {layers}"
    crs = args.get("crs", "EPSG:4326")
    img_format = args.get("format", "image/png")
    problem = crs_problem(crs)
    if problem:
        return {"_error": problem, "_code": "INVALID_ARGS",
                "_suggestion": "Pass the CRS on its own, without any other provider parameter."}
    if _is_wmts(url):
        return _add_wmts_layer(ogc_inspect.service_base(links.clean(url)), layers,
                               args.get("name") or f"WMTS - {layers}")



    url = ogc_inspect.service_base(links.clean(url))




    names = [part.strip() for part in str(layers).split(",") if part.strip()] or [str(layers)]
    uri = (
        f"url={encode_uri_url(url)}"
        + "".join(f"&layers={urllib.parse.quote(part)}&styles=" for part in names)
        + f"&crs={crs}"
        f"&format={urllib.parse.quote(img_format)}"
    )

    def _create():
        layer = QgsRasterLayer(uri, name, "wms")
        if not layer.isValid():



            return {"_error": f"Failed to connect to WMS: {url}", "code": "EXECUTION_FAILED",
                    "suggestion": "Keep every parameter the user's link carried (authkey, token, map=) in url, "
                                  "then check the layer name with inspect_data_source on that url."}
        with view_kept():
            QgsProject.instance().addMapLayer(layer)
        out = {"layer_name": layer.name(), "layer_id": layer.id(), "url": url, "wms_layers": layers}
        out.update(_stacked(layer))
        return out

    return _run_on_main_thread(_create, timeout=30)



















WFS_WARN_FEATURES = 50_000
WFS_WIRE_BYTES_PER_FEATURE = 150





WFS_SILENT_PAGES = (100, 500, 1_000, 2_000, 5_000, 10_000, 25_000)


def _wfs_silent_pages() -> tuple:
    """The page sizes to test a silent truncation against, served or shipped."""






    return tuple(tuning.service_list("wfs_silent_pages", WFS_SILENT_PAGES))


_WFS_HITS_MAX_BYTES = 64 * 1024
_WFS_HITS_TTL_S = 300
_WFS_MATCHED_RE = re.compile(rb'numberMatched\s*=\s*"(\d+)"')


_WFS_TYPENAME_RE = re.compile(rb"<(?:\w+:)?Name>\s*([^<\s][^<]*?)\s*</(?:\w+:)?Name>")



_WFS_CAPS_MAX_BYTES = 12 * 1024 * 1024
_WFS_CAPS_TTL_S = 900
_WFS_NAMES_SHOWN = 12


def _wfs_typenames(url: str) -> list[str]:
    """The type names the service advertises, for a request that named one it does not have."""




    query = {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetCapabilities"}
    probe = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
    try:
        answer = net.fetch(probe, timeout=20, max_bytes=_WFS_CAPS_MAX_BYTES,
                           total_timeout=25, cache_ttl=_WFS_CAPS_TTL_S)
    except Exception as exc:  # noqa: BLE001 - a service that will not describe itself is still reported
        log_warning(f"WFS capabilities probe failed for {url}: {exc}")
        return []
    names: list[str] = []
    for found in _WFS_TYPENAME_RE.finditer(answer.body or b""):
        name = found.group(1).decode("utf-8", "replace").strip()
        if name and ":" in name and name not in names:
            names.append(name)
    return names


_WFS_TYPE_BLOCK_RE = re.compile(rb"<(?:\w+:)?FeatureType\b[^>]*>(.*?)</(?:\w+:)?FeatureType>", re.DOTALL)
_WFS_CRS_RE = re.compile(rb"<(?:\w+:)?(?:Default|Other)CRS>\s*([^<\s][^<]*?)\s*</")
_WFS_CRS_SHOWN = 6


def _wfs_epsg(text: str) -> str:
    """"urn:ogc:def:crs:EPSG::4326" and "EPSG:4326" are the same CRS, said twice."""
    parts = [p for p in text.replace("::", ":").split(":") if p]
    if len(parts) >= 2 and parts[-2].upper() == "EPSG" and parts[-1].isdigit():
        return f"EPSG:{parts[-1]}"
    return text.strip()


def _wfs_type_crs(url: str, typename: str) -> list[str]:
    """The CRS this type name advertises, default first, or []."""




    query = {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetCapabilities"}
    probe = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
    try:
        answer = net.fetch(probe, timeout=20, max_bytes=_WFS_CAPS_MAX_BYTES,
                           total_timeout=25, cache_ttl=_WFS_CAPS_TTL_S)
    except Exception as exc:  # noqa: BLE001 - a service that will not describe itself is still reported
        log_warning(f"WFS capabilities probe failed for {url}: {exc}")
        return []
    wanted = typename.strip()
    for block in _WFS_TYPE_BLOCK_RE.finditer(answer.body or b""):
        body = block.group(1)
        found = _WFS_TYPENAME_RE.search(body)
        if not found:
            continue
        if found.group(1).decode("utf-8", "replace").strip() != wanted:
            continue
        seen: list[str] = []
        for one in _WFS_CRS_RE.finditer(body):
            code = _wfs_epsg(one.group(1).decode("utf-8", "replace"))
            if code and code not in seen:
                seen.append(code)
        return seen
    return []


def _wfs_failure(url: str, typename: str, crs: str, qgis_message: str,
                 hits: int | None = None) -> str:
    """Why the WFS layer is invalid, in the service's own terms rather than ours."""











    detail = (qgis_message or "").strip()
    names = _wfs_typenames(url)
    if names and typename not in names:
        close = [n for n in names if typename.split(":")[-1].lower() in n.lower()][:3]
        shown = close or names[:_WFS_NAMES_SHOWN]
        more = "" if len(names) <= len(shown) else f", and {len(names) - len(shown)} more"
        return (f"The service does not publish a type name {typename!r}. It offers: "
                f"{', '.join(shown)}{more}. Call add_wfs_layer again with one of those."
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
                    f"Call add_wfs_layer again with crs={offered[0]!r}.")
        if hits is not None and hits > WFS_WARN_FEATURES:
            return (f"{head} The type holds {hits:,} features and the whole of it was asked for. "
                    f"Zoom the canvas to the area of interest and call again, or pass "
                    f"max_features up to {limits.current('MAX_FEATURES_PER_CALL'):,} for a sample."
                    + (f" {crs} is offered, so the CRS is not the problem." if offered else ""))





        return (f"{head} Try a smaller max_features"
                + (f"; {crs} is offered, so the CRS is not the problem." if offered else
                   f", or a srsname other than {crs}."))
    return (f"No answer from the WFS at {url} that names any type. "
            + (f"QGIS said: {detail}. " if detail else "")
            + "Check the URL with inspect_data_source.")


def _wfs_hits(url: str, typename: str, crs: str) -> int | None:
    """How many features the service holds for this type name, or None."""






    query = {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
             "TYPENAMES": typename, "RESULTTYPE": "hits", "SRSNAME": crs}
    probe = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
    try:
        answer = net.fetch(probe, timeout=20, max_bytes=_WFS_HITS_MAX_BYTES,
                           total_timeout=25, cache_ttl=_WFS_HITS_TTL_S)
    except Exception as exc:  # noqa: BLE001 - a service that will not be probed still loads
        log_warning(f"WFS hits probe failed for {typename}: {exc}")
        return None
    found = _WFS_MATCHED_RE.search(answer.body or b"")
    if not found:
        return None
    try:
        return int(found.group(1))
    except ValueError:
        return None


def _wfs_restrict_to_view(hits: int) -> bool:
    """Whether a type name of *hits* features is loaded for the map view only."""






    return hits > WFS_WARN_FEATURES


def _misses_the_view(layer) -> str:
    """Say so when a layer's own extent does not reach the map, else ""."""








    try:
        from qgis.utils import iface as qgis_iface

        canvas = qgis_iface.mapCanvas() if qgis_iface is not None else None
        if canvas is None:
            return ""
        view = canvas.extent()
        if view.isEmpty():
            return ""
        extent = layer.extent()
        if extent.isEmpty():
            return "The layer reports an empty extent, so nothing will draw."
        target = canvas.mapSettings().destinationCrs()
        if layer.crs().isValid() and target.isValid() and layer.crs() != target:
            extent = QgsCoordinateTransform(layer.crs(), target,
                                            QgsProject.instance()).transformBoundingBox(extent)
        if extent.intersects(view):
            return ""
        return ("The layer's own extent does not reach the current view, so the map will look "
                "empty. Either the service published a wrong bounding box, or the data is "
                "somewhere else: zoom_to_layer shows where it says it is.")
    except Exception:  # noqa: BLE001 - a note is never worth an exception
        return ""


def _mark_wfs_truncated(layer_id: str, count) -> None:
    """Stamp the layer *layer_id* with the count it really holds. Runs on the main thread."""
    if not isinstance(count, int):
        return
    layer = QgsProject.instance().mapLayer(layer_id)
    if layer is not None:
        mark_truncated_count(layer, count)


def _add_wfs_layer(args: dict) -> dict:

    url = ogc_inspect.service_base(links.clean(args["url"]))
    typename = args["typename"]
    name = args.get("name") or f"WFS - {typename}"
    crs = args.get("crs", "EPSG:4326")
    ceiling = limits.current("MAX_FEATURES_PER_CALL")
    try:





        max_features = max(1, min(int(args.get("max_features") or 1000), ceiling))
    except (TypeError, ValueError):
        return {"_error": f"max_features must be a whole number from 1 to "
                f"{ceiling}, got {args.get('max_features')!r}.",
                "code": "INVALID_ARGS",
                "suggestion": "Pass a whole number, or leave max_features out for 1000."}

    problem = crs_problem(crs)
    if problem:
        return {"_error": problem, "_code": "INVALID_ARGS",
                "_suggestion": "Pass the CRS on its own, without any other provider parameter."}

    hits = _wfs_hits(url, typename, crs)
    restrict = _wfs_restrict_to_view(hits) if hits is not None else False
    warning = suggestion = ""







    source = QgsDataSourceUri()
    source.setParam("url", url)
    source.setParam("typename", typename)
    source.setParam("srsname", crs)
    source.setParam("version", "2.0.0")
    source.setParam("maxNumFeatures", str(max_features))
    if restrict:
        source.setParam("restrictToRequestBBOX", "1")
    uri = source.uri(False)

    def _create():
        layer = QgsVectorLayer(uri, name, "WFS")
        if not layer.isValid():


            try:
                said = layer.error().summary()
            except Exception:  # noqa: BLE001 - an invalid layer may have no error object
                said = ""
            return {"_invalid": True, "_qgis_message": said}
        QgsProject.instance().addMapLayer(layer)
        out = {
            "layer_name": layer.name(),
            "layer_id": layer.id(),
            "feature_count": layer.featureCount(),
            "url": url,
            "typename": typename,
        }
        missed = _misses_the_view(layer)
        if missed:
            out["_note"] = missed
        return out

    out = _run_on_main_thread(_create, timeout=30)
    if out.get("_invalid"):
        return {"_error": _wfs_failure(url, typename, crs, out.get("_qgis_message") or "", hits)}
    if out.get("_error"):
        return out
    if hits is not None:
        out["features_available"] = hits
        out["estimated_bytes"] = hits * WFS_WIRE_BYTES_PER_FEATURE
        out["restricted_to_view"] = restrict
    if restrict:






        warning = (f"Only the features under the map view are fetched: this type name has "
                   f"{hits:,} of them. An expression filter, get_features or a Processing run "
                   "sees nothing outside the current view, however right the field name is.")
        suggestion = ("Narrow at the source: set_layer_filter sends the expression to the "
                      "service, so the layer holds that subset wherever the map is. Zoom to the "
                      "area first if what you want is the view's features.")
    count = out.get("feature_count")
    if isinstance(count, int) and count > max_features:



        if hits is None:
            hits = out["features_available"] = count
        count = out["feature_count"] = max_features




    silent_page = (hits is None and isinstance(count, int)
                   and count in _wfs_silent_pages() and count < max_features)


    if not restrict and isinstance(count, int) and count > 0 and (
            (count == max_features and (hits is None or hits > count))
            or silent_page or (hits is not None and count < hits)):




        out["truncated"] = True






        layer_id = out.get("layer_id")
        if layer_id:
            _run_on_main_thread(_mark_wfs_truncated, layer_id, count)
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
        suggestion = (f"Narrow at the source: set_layer_filter sends the expression to the service, which "
                      f"then answers the matching features, up to {ceiling:,} for one layer. More than that "
                      f"needs a narrower filter, not a larger number.")
    if restrict and count == 0 and hits:











        offered = [code for code in _wfs_type_crs(url, typename) if code != crs]
        out["empty_in_view"] = True
        warning = (f"The layer loaded and holds nothing: the service returned no feature under the "
                   f"map view, although this type name has {hits:,} in total.")
        suggestion = (f"Either the view is over ground this type does not cover, or the service "
                      f"disagrees about a bbox in {crs}. Move the view to an area it covers, or "
                      f"call again in the CRS the service uses natively"
                      + (f": it also offers {', '.join(offered[:_WFS_CRS_SHOWN])}." if offered else "."))
    if warning:
        out["warning"] = warning
    if suggestion:
        out["suggestion"] = suggestion
    return out
