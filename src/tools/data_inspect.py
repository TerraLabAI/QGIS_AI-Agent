# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib

from qgis.core import QgsCoordinateReferenceSystem, QgsProject, QgsVectorLayer, QgsWkbTypes

from ..core import http_headers, limits, links, net, security, tuning, vsi
from ..core.background import delete_here, run_on_main_thread, take
from ..core.crs_ref import crs_ref
from ..core.host_platform import IS_WINDOWS, remove_tree
from ..core.policy import create_managed_temp_dir
from ..core.tool_registry import coded_fact, tool_error
from . import ogc_inspect, volume_guard
from .csv_loader import CSV_EXTENSIONS
from .data_common import (
    _CACHE_CATALOG_S,
    _DOWNLOAD_TOTAL_TIMEOUT,
    _INSPECT_TIMEOUT,
    _MAX_ARCHIVE_ENTRIES,
    _MAX_EXTRACTED_SIZE,
    _PORTAL_TIMEOUT,
    _USER_AGENT,
    _avoid_reserved_name,
    _bbox_km2,
    _download_timeout,
    _http_fetch,
    _layer_from_geojson_str,
    _safe_filename,
    _vector_uri_for,
    built_here,
    worker_options,
)
from .data_portals import (
    _MAX_DISTRIBUTIONS,
    _build_direct_import_result,
    _downloads_as_vector,
    _inspect_json_payload,
    _inspect_xml_payload,
    _link_file_call,
    _page_distributions,
)
from .missing_file import missing_file




RANGE_READABLE_EXTENSIONS = (".parquet", ".geoparquet", ".fgb", ".gpkg", ".pmtiles")
_VSICURL_TIMEOUT_S = 120




_WARN_REMOTE_BYTES = 200 * 1024 * 1024
_COUNT_CEILING = 200_000



_RANGE_PROBE_BYTES = 1024


_CACHE_PROBE_S = 300.0


def _remote_size(url: str) -> int:





    try:
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Range": "bytes=0-0"})






        answer = net.fetch(request, timeout=15, max_bytes=_RANGE_PROBE_BYTES, total_timeout=20,
                           cache_ttl=_CACHE_PROBE_S)




        content_range = str(answer.headers.get("content-range") or "")
        total = content_range.rsplit("/", 1)[-1]
        if total.isdigit():
            return int(total)
        if content_range:


            return 0
        length = answer.headers.get("content-length")
        return int(length) if str(length).isdigit() else 0
    except Exception:  # noqa: BLE001
        return 0


def _human_bytes(size: int) -> str:
    step = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if step < 1024 or unit == "TB":
            return f"{step:.0f} {unit}" if unit in ("B", "KB") else f"{step:.1f} {unit}"
        step /= 1024
    return f"{size} B"


def _tune_gdal_for_range_reads() -> None:








    vsi.apply_persistent()


def _range_readable(url: str) -> bool:
    path = urllib.parse.urlparse(url).path.lower()
    return path.endswith(RANGE_READABLE_EXTENSIONS)


class _NoSuchLayer(Exception):
    pass


def _given_srs(crs):

    from osgeo import osr

    if not crs:
        return None
    srs = osr.SpatialReference()
    try:
        if srs.SetFromUserInput(str(crs)) != 0:
            return None
    except RuntimeError:
        return None
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return srs


def _features_in_box(source: str, sublayer: str | None, bbox, given=None) -> tuple[int | None, object]:





    from osgeo import ogr, osr

    west, south, east, north = bbox
    dataset = layer = None
    declared = None
    try:
        try:
            dataset = ogr.Open(source)
        except RuntimeError as exc:
            raise OSError(str(exc)) from exc
        if dataset is None:
            raise OSError("GDAL could not open it")
        layer = dataset.GetLayerByName(str(sublayer)) if sublayer else dataset.GetLayer(0)
        if layer is None:
            raise _NoSuchLayer([dataset.GetLayer(i).GetName() for i in range(dataset.GetLayerCount())])
        box = (west, south, east, north)
        declared = layer.GetSpatialRef()
        if declared is not None:
            declared = declared.Clone()
        layer_srs = declared or given
        if layer_srs is not None:
            wgs84 = osr.SpatialReference()
            wgs84.ImportFromEPSG(4326)
            wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            layer_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            if not layer_srs.IsSame(wgs84):
                transform = osr.CoordinateTransformation(wgs84, layer_srs)
                corners = [transform.TransformPoint(x, y)[:2] for x in (west, east) for y in (south, north)]
                box = (min(c[0] for c in corners), min(c[1] for c in corners),
                       max(c[0] for c in corners), max(c[1] for c in corners))
        layer.SetSpatialFilterRect(*box)
        return int(layer.GetFeatureCount(1)), declared
    except (OSError, _NoSuchLayer):
        raise
    except Exception:  # noqa: BLE001
        return None, declared
    finally:
        del layer
        del dataset







_EXTRACT_FEATURES_PER_S = 1_000


def _extract_remote_vector(source: str, url: str, name: str, sublayer: str | None, bbox: list,
                           args: dict | None = None) -> dict:














    from osgeo import gdal

    gdal.UseExceptions()
    west, south, east, north = bbox
    file_name = posixpath.basename(urllib.parse.urlparse(url).path)
    started = time.monotonic()



    given_crs = (args or {}).get("crs")
    given = _given_srs(given_crs)
    try:
        count, declared = _features_in_box(source, sublayer, bbox, given)
    except _NoSuchLayer as exc:
        layers = list(exc.args[0])
        return tool_error(f"{file_name} holds no layer named {sublayer!r}; its layers: {', '.join(layers)}.",
                          "INVALID_ARGS", hint="layer_not_in_file", layers=layers)
    except OSError as exc:


        try:
            net.fetch(urllib.request.Request(url, headers={"Range": "bytes=0-0"}), timeout=15,
                      max_bytes=_RANGE_PROBE_BYTES, total_timeout=20)
        except Exception as refusal:  # noqa: BLE001
            refused = _http_refusal(url, refusal)
            if refused:
                return refused
        return tool_error(f"GDAL could not open {file_name} over HTTP: {exc}", "EXECUTION_FAILED",
                          hint="gdal_open_failed")
    if count is None:
        area = _bbox_km2(south, west, north, east)
        ceiling = limits.current("MAX_FETCH_KM2")
        if area > ceiling and not volume_guard.lifted(args or {}):
            return {"_error": (f"The box is {area:,.0f} km2 and {file_name} "
                               f"could not say how many features it holds, so the {ceiling:,.0f} km2 limit for one "
                               "extract applies."),
                    "code": limits.CEILING_CODE,
                    "suggestion": f"{ceiling:,.0f} km2 around the area of interest fits the limit."}
    elif count == 0:

        return {"loaded": False, "feature_count": 0,
                "note": f"{file_name} holds no feature in this box."}
    elif not volume_guard.lifted(args or {}):


        left = max(0.0, limits.current("CALL_MAX_SECONDS_BACKGROUND") - (time.monotonic() - started))
        fits = int(left * tuning.threshold("extract_features_per_s", _EXTRACT_FEATURES_PER_S, 200, 20_000))
        if count > fits:
            area = _bbox_km2(south, west, north, east)
            smaller = limits.shrink_bbox((south, west, north, east), area * fits / count) if fits else None
            out = {"_error": (f"The box holds {count:,} features of {file_name}; the {left:.0f} s this call has "
                              f"left copies about {fits:,}, so nothing was loaded."),
                   **volume_guard.coded(hint="feature_ceiling", variant="cap"),
                   "code": limits.CEILING_CODE, "feature_count": count, "suggestion": ""}
            if smaller:
                out["bbox_that_fits"] = [smaller[1], smaller[0], smaller[3], smaller[2]]
                out["variant"] = "cap_box"
            return out
    directory = create_managed_temp_dir("extract")
    path = os.path.join(directory, f"{_safe_extract_stem(name)}.gpkg")
    kwargs = {"format": "GPKG", "spatFilter": [west, south, east, north],
              "spatSRS": "EPSG:4326", "dstSRS": "EPSG:4326"}
    crs_note = ""
    if declared is None and given is not None:
        kwargs["srcSRS"] = str(given_crs)
        crs_note = f"{file_name} declares no CRS; it was read as {given_crs}, the crs of this call."
    elif declared is not None and given is not None and not declared.IsSame(given):
        name_of = declared.GetAuthorityCode(None)
        declared_name = (f"{declared.GetAuthorityName(None)}:{name_of}" if name_of
                         else (declared.GetName() or "its own CRS"))
        crs_note = (f"{file_name} declares {declared_name}, which was kept; the crs of this call, "
                    f"{given_crs}, was not applied.")
    if sublayer:
        kwargs["layers"] = [str(sublayer)]




    local = callable(getattr(gdal, "GetThreadLocalConfigOption", None))
    getter = gdal.GetThreadLocalConfigOption if local else gdal.GetConfigOption
    setter = gdal.SetThreadLocalConfigOption if local else gdal.SetConfigOption
    previous = getter("OGR2OGR_USE_ARROW_API", None)
    setter("OGR2OGR_USE_ARROW_API", "NO")
    try:
        written = gdal.VectorTranslate(path, source, **kwargs)
    except RuntimeError as exc:
        return tool_error(f"The extract from {url} failed: {exc}", "EXECUTION_FAILED", hint="extract_failed")
    finally:
        setter("OGR2OGR_USE_ARROW_API", previous)
    if written is None:
        return tool_error(f"The extract from {url} produced nothing.", "EXECUTION_FAILED", hint="extract_empty")
    out_layer = written.GetLayer(0)
    count = int(out_layer.GetFeatureCount()) if out_layer is not None else 0
    layer_in_file = out_layer.GetName() if out_layer is not None else None
    del out_layer
    del written
    out = {"path": path, "feature_count": count, "layer_in_file": layer_in_file}
    if crs_note:
        out["crs_note"] = crs_note
    return out


def _safe_extract_stem(text: str) -> str:





    stem = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(text or "extract"))
    return _avoid_reserved_name(stem.strip("_")[:60] or "extract")


def _add_vector_over_range_requests(url: str, layer_name: str | None, sublayer: str | None = None,
                                    bbox=None, args: dict | None = None) -> dict:













    net.check_url(url)
    _tune_gdal_for_range_reads()
    name = layer_name or os.path.splitext(posixpath.basename(urllib.parse.urlparse(url).path))[0] or "layer"
    size = _remote_size(url)
    source = f"/vsicurl/{url}"
    if sublayer:



        if "|" in str(sublayer):
            return tool_error(f"'{sublayer}' is not a layer name: the | character separates provider options.",
                              "INVALID_ARGS", hint="layer_name_has_pipe")
        source = f"{source}|layername={sublayer}"

    if bbox:
        cut = _extract_remote_vector(source.split("|", 1)[0], url, name, sublayer, bbox, args)
        if "_error" in cut or "path" not in cut:
            return cut
        local = cut["path"]
        if cut.get("layer_in_file"):
            local = f"{local}|layername={cut['layer_in_file']}"

        built_local = built_here(lambda: QgsVectorLayer(local, name, "ogr", worker_options()))

        def _create_local():
            layer = take(built_local)
            if layer is None:
                layer = QgsVectorLayer(local, name, "ogr")
            if not layer.isValid():
                return {"_error": f"QGIS could not read the extract cut from {url}.",
                        "_code": "EXECUTION_FAILED",
                        **coded_fact(hint="extract_unreadable")}
            QgsProject.instance().addMapLayer(layer)
            return {
                "layer_name": layer.name(),
                "layer_id": layer.id(),
                "url": url,
                "provider": "ogr, local extract",
                "crs": crs_ref(layer.crs()),
                "fields": [f.name() for f in layer.fields()],
                "feature_count": cut["feature_count"],
                "extract_bbox": list(bbox),
                "_note": ("The box was cut out of the remote file through its spatial index and written "
                          "locally, so this layer holds only the area asked for and every read of it is "
                          "local. The whole file was not downloaded."),
                **({"crs_note": cut["crs_note"]} if cut.get("crs_note") else {}),
            }

        return run_on_main_thread(_create_local, timeout=_VSICURL_TIMEOUT_S)



    facts: dict = {}

    def _make():
        layer = QgsVectorLayer(source, name, "ogr", worker_options())
        if layer.isValid():
            if not sublayer:
                facts["others"] = _sublayers_of(layer)
            if size and size < _WARN_REMOTE_BYTES:
                facts["count"] = layer.featureCount()
        return layer

    built = built_here(_make)

    def _create():
        layer = take(built)
        if layer is None:
            layer = QgsVectorLayer(source, name, "ogr")
        if not layer.isValid():
            return {"_error": f"QGIS could not read {url} over HTTP range requests.",
                    "_code": "INVALID_ARGS",
                    **coded_fact(hint="range_requests_unsupported")}
        given_crs = (args or {}).get("crs")
        if given_crs and not layer.crs().isValid():

            assumed = QgsCoordinateReferenceSystem(str(given_crs))
            if assumed.isValid():
                layer.setCrs(assumed)
        QgsProject.instance().addMapLayer(layer)
        out = {
            "layer_name": layer.name(),
            "layer_id": layer.id(),
            "url": url,
            "provider": "ogr over /vsicurl/",
            "crs": crs_ref(layer.crs()),
            "fields": [f.name() for f in layer.fields()],
            "_note": "Read in place over HTTP range requests: the whole file is not copied.",
            "read_note": coded_fact(hint="vsicurl_read_in_place"),
        }
        if size:
            out["size_bytes"] = size
            out["size"] = _human_bytes(size)
            if size >= _WARN_REMOTE_BYTES:






                out["large_note"] = coded_fact(hint="vsicurl_large_file", size=_human_bytes(size))
        if sublayer:
            out["layer"] = sublayer
        else:
            others = facts["others"] if "others" in facts else _sublayers_of(layer)
            if len(others) > 1:
                out["layers_available"] = others
                out["_note"] += f" This source holds {len(others)} layers."
                out["layers_note"] = coded_fact(hint="source_layers_pick", count=len(others))


        if size and size < _WARN_REMOTE_BYTES:
            count = facts["count"] if "count" in facts else layer.featureCount()
            if count is not None and 0 <= count <= _COUNT_CEILING:
                out["feature_count"] = int(count)
        return out

    return run_on_main_thread(_create, timeout=_VSICURL_TIMEOUT_S)







_SINGLE_LAYER_EXTENSIONS = (".fgb", ".geojson", ".json", ".parquet", ".pmtiles", ".shp", ".csv", ".kml")


def _sublayers_of(layer) -> list:

    try:
        source = str(layer.source() or "").split("|", 1)[0]




        path = (urllib.parse.urlparse(source).path or source).lower()
        if path.endswith(_SINGLE_LAYER_EXTENSIONS):
            return []
        try:
            from qgis.core import QgsProviderRegistry



            details = QgsProviderRegistry.instance().querySublayers(source)
            names = [str(d.name()) for d in details]
        except Exception:  # noqa: BLE001

            names = [(str(entry).split("!!::!!") + ["", ""])[1]
                     for entry in (layer.dataProvider().subLayers() or [])]
        return [n for n in dict.fromkeys(names) if n][:20]
    except Exception:  # noqa: BLE001
        return []



_ARCHIVE_RASTER_EXTENSIONS = (".tif", ".tiff", ".img", ".jp2", ".asc", ".nc")


def _archive_entries(root: str) -> list:

    names = []
    for folder, _dirs, files in os.walk(root):
        names.extend(os.path.join(folder, f) for f in files)
    return names



_ARCHIVE_LIST_MAX = 50


def _archive_names(paths: list, root: str) -> list:

    return [os.path.relpath(f, root).replace(os.sep, "/") for f in paths[:_ARCHIVE_LIST_MAX]]


def _discard_download(tmp_dir: str, result: dict) -> dict:





    remove_tree(tmp_dir)
    return result


def _extract_member(zf, member, tmp_dir: str) -> None:









    parts = [part for part in member.filename.replace("\\", "/").split("/")
             if part not in ("", ".", "..")]
    if not parts:
        return
    target = os.path.join(tmp_dir, *[_safe_filename(part, "entry") for part in parts])
    if len(target) >= security._MAX_PATH and not security._long_paths_ok():




        if member.is_dir():
            return
        folder = hashlib.sha1("/".join(parts[:-1]).encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
        target = os.path.join(tmp_dir, folder, _safe_filename(parts[-1], "entry"))
    if member.is_dir():
        os.makedirs(target, exist_ok=True)
        return
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with zf.open(member) as source, open(target, "wb") as sink:
        shutil.copyfileobj(source, sink)








_CONTENT_TYPE_EXTS = {"application/zip": ".zip", "application/x-zip-compressed": ".zip",
                      "application/geopackage+sqlite3": ".gpkg", "application/geo+json": ".geojson",
                      "application/json": ".geojson", "application/vnd.google-earth.kml+xml": ".kml",
                      "text/csv": ".csv"}





_ENDPOINT_EXTENSIONS = frozenset({".ashx", ".aspx", ".asp", ".php", ".jsp", ".cgi", ".do", ".action", ".pl"})


def _extension_of_download(path_name: str, headers, body: bytes) -> str:

    ext = os.path.splitext(path_name)[1].lower()
    if ext and ext not in _ENDPOINT_EXTENSIONS:
        return ext
    ext = os.path.splitext(_disposition_filename(headers.get("content-disposition") or ""))[1].lower()
    if ext:
        return ext
    content_type = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if content_type in _CONTENT_TYPE_EXTS:
        return _CONTENT_TYPE_EXTS[content_type]
    if body[:4] == b"PK\x03\x04":
        return ".zip"
    if body[:15] == b"SQLite format 3":
        return ".gpkg"
    return ""


def _add_vector_from_url(args: dict) -> dict:
    link = expand_link(str(args["url"]))
    if link.get("error"):
        return link["error"]
    kind = link["kind"]
    if kind == "unreachable":
        return tool_error(link["note"], "INVALID_ARGS", hint="link_unreachable")
    if kind == "inline":
        return _add_inline_geojson(link, args.get("layer_name"))
    url = link["url"]
    if kind == "listing":
        data = _link_data_files(link)
        if len(data) != 1:
            return tool_error(f"{link['note']} It holds {len(data)} data files, so none was picked.",
                              "INVALID_ARGS", hint="link_listing_pick",
                              files=[{"name": f["name"], "url": f["url"]} for f in data[:15]]
                              or [{"name": f["name"], "url": f["url"]} for f in (link.get("files") or [])[:15]])
        if not _downloads_as_vector(data[0]):

            return {"_error": f"{link['note']} It is read with {data[0]['add']['tool']}, not downloaded.",
                    "code": "INVALID_ARGS", "call": data[0]["add"],
                    "suggestion": f"{data[0]['add']['tool']}, with call arguments, reads it."}
        url = data[0]["url"]
    out = _download_vector_from_url(args, url, args.get("layer_name"), shared=bool(link.get("resolved_from")))
    if link.get("resolved_from") and isinstance(out, dict):
        out["resolved_from"] = link["resolved_from"]
    return out


def _add_inline_geojson(link: dict, layer_name) -> dict:

    text = link.get("inline") or ""
    try:
        json.loads(text)
    except ValueError:
        return tool_error("The link carries data inside it, and it is not valid GeoJSON.", "INVALID_ARGS",
                          hint="geojson_io_link_invalid")
    name = layer_name or "geojson.io"

    def _create():
        layer = _layer_from_geojson_str(text, name, "geojson")
        if not layer.isValid():
            return {"_error": "QGIS could not read the GeoJSON the link carries.", "code": "INVALID_ARGS"}
        QgsProject.instance().addMapLayer(layer)
        return {"layer_name": layer.name(), "layer_id": layer.id(), "feature_count": layer.featureCount(),
                "resolved_from": link["resolved_from"]}

    return run_on_main_thread(_create, timeout=30)


def _disposition_filename(disposition: str) -> str:

    return posixpath.basename(http_headers.disposition_filename(disposition))


def _gunzip_download(filepath: str, cap: int = _MAX_EXTRACTED_SIZE):







    import gzip

    target = filepath[:-3]
    cancel = net.current_cancel_check()
    try:
        with gzip.open(filepath, "rb") as source, open(target, "wb") as sink:
            written = 0
            while True:
                if cancel is not None and cancel():
                    return {"_error": "The run was stopped while the download was being unpacked.",
                            "code": "CANCELLED"}
                chunk = source.read(1 << 20)
                if not chunk:
                    break
                written += len(chunk)
                if written > cap:
                    return tool_error(f"The gzipped file unpacks to more than {_human_bytes(cap)}.",
                                      hint="gzip_too_large", limit=_human_bytes(cap))
                sink.write(chunk)
    except (OSError, EOFError, zlib.error) as exc:

        return tool_error(f"The .gz download could not be unpacked: {exc}", "INVALID_ARGS", hint="gz_unpack_failed")
    return target


def _download_vector_from_url(args: dict, url: str, layer_name, shared: bool = False) -> dict:
    if _range_readable(url):
        ranged = _add_vector_over_range_requests(url, layer_name, args.get("layer"), args.get("bbox"), args=args)
        if not ranged.get("_error"):
            return ranged







        range_failure = (
            ranged.get("_code") == "INVALID_ARGS"
            and "over HTTP range requests" in str(ranged.get("_error") or "")
        )
        if args.get("bbox") or not range_failure:
            return ranged





    return _download_to_disk(args, url, layer_name, shared)



_STREAM_DOWNLOAD_SHARE = 0.6


def _share_page(shared: bool, head: bytes) -> dict | None:
    if shared and head[:512].lstrip()[:15].lower().startswith((b"<!doctype html", b"<html")):


        return tool_error("The share link answered a web page, not the file: it is not shared with anyone "
                          "who has the link, or the link is incomplete.", "INVALID_ARGS",
                          hint="share_page_not_file")
    return None


def _download_to_disk(args: dict, url: str, layer_name, shared: bool) -> dict:

    tmp_dir = create_managed_temp_dir("download")
    body = os.path.join(tmp_dir, "download.bin")
    clock = limits.current("CALL_MAX_SECONDS_BACKGROUND") * _STREAM_DOWNLOAD_SHARE
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        streamed = net.fetch_to_file(req, body, timeout=_download_timeout(),
                                     max_bytes=limits.current("MAX_STREAM_BYTES"),
                                     total_timeout=min(_DOWNLOAD_TOTAL_TIMEOUT, clock))
    except net.FetchTooLarge as e:
        return _discard_download(tmp_dir, tool_error(
            f"The file is larger than this disk can take for one load: {e}", limits.CEILING_CODE,
            hint="download_too_large"))
    except net.FetchDeadline as e:
        return _discard_download(tmp_dir, tool_error(f"The download did not finish in time: {e}",
                                                     hint="download_late"))
    except net.FetchCancelled:
        return _discard_download(tmp_dir, {"_error": "The run was stopped.", "code": "CANCELLED",
                                           "suggestion": "The user stopped the run."})
    except (urllib.error.URLError, OSError) as e:
        return _discard_download(tmp_dir, _http_refusal(url, e) or {"_error": f"Failed to download: {e}"})
    with open(streamed.path, "rb") as handle:
        head = handle.read(512)
    refused = _share_page(shared, head)
    if refused:
        return _discard_download(tmp_dir, refused)
    filename, ext = _download_name(url, streamed.headers, head)
    filepath = os.path.join(tmp_dir, filename)
    os.replace(streamed.path, filepath)
    out = _load_download(args, url, layer_name, tmp_dir, filepath, ext, limits.current("MAX_STREAM_BYTES"))
    if isinstance(out, dict) and not out.get("_error"):
        out["download_bytes"] = streamed.size
    return out


def _download_name(url: str, headers, head: bytes) -> tuple[str, str]:

    content_type = headers.get("content-type", "")
    parsed = urllib.parse.urlparse(url)



    filename = _safe_filename(posixpath.basename(parsed.path))


    named = _disposition_filename(headers.get("content-disposition") or "")
    if named and (not os.path.splitext(filename)[1]
                  or os.path.splitext(filename)[1].lower() in _ENDPOINT_EXTENSIONS):
        filename = _safe_filename(named)
    ext = _extension_of_download(filename, headers, head)
    if ext and not filename.lower().endswith(ext):
        filename += ext
    if not ext and "json" in content_type:
        ext = ".geojson"
        filename += ext
    return filename, ext


def _load_unpacked_raster(tmp_dir: str, url: str, paths: list, layer_name) -> dict:


    if len(paths) > 1:
        return {"url": url, "layers": _archive_names(paths, tmp_dir), "local_paths": paths[:_ARCHIVE_LIST_MAX],
                "folder": tmp_dir,
                "_note": f"The archive holds {len(paths)} rasters, none added yet.",
                "load_note": coded_fact(hint="archive_rasters_load")}
    from .layer_io_tools import _add_raster_layer

    loaded = _add_raster_layer({"path": paths[0], "name": layer_name or None})
    if isinstance(loaded, dict) and loaded.get("_error") is not None:
        return _discard_download(tmp_dir, loaded)
    if isinstance(loaded, dict):
        loaded["url"] = url
    return loaded


def _load_download(args: dict, url: str, layer_name, tmp_dir: str, filepath: str, ext: str,
                   extracted_cap: int) -> dict:

    if ext == ".zip":
        try:
            with zipfile.ZipFile(filepath, "r") as zf:
                members = zf.infolist()
                if len(members) > _MAX_ARCHIVE_ENTRIES:
                    return _discard_download(tmp_dir, tool_error(
                        f"The archive holds {len(members)} entries, over the "
                        f"{_MAX_ARCHIVE_ENTRIES} this tool unpacks in one call.",
                        "INVALID_ARGS", hint="archive_too_many_entries", entries=len(members),
                        max_entries=_MAX_ARCHIVE_ENTRIES))
                extracted = 0
                for member in members:
                    target_path = os.path.realpath(os.path.join(tmp_dir, member.filename))
                    if (
                        not target_path.startswith(os.path.realpath(tmp_dir) + os.sep)
                        and target_path != os.path.realpath(tmp_dir)
                    ):
                        return _discard_download(
                            tmp_dir, {"_error": f"ZIP archive contains an unsafe path: {member.filename}"})
                    extracted += member.file_size






                if extracted > extracted_cap:
                    return _discard_download(tmp_dir, tool_error(
                        f"The archive unpacks to {_human_bytes(extracted)}, over the "
                        f"{_human_bytes(extracted_cap)} limit for one download.",
                        hint="archive_too_big", unpacked=_human_bytes(extracted),
                        limit=_human_bytes(extracted_cap)))



                cancel = net.current_cancel_check()
                for member in members:
                    if cancel is not None and cancel():
                        return _discard_download(tmp_dir, {
                            "_error": "The run was stopped while the archive was being unpacked.",
                            "code": "CANCELLED",
                        })
                    _extract_member(zf, member, tmp_dir)


            from .layer_io_tools import (
                _add_vector_layer,
                archive_datasets,
                container_layers,
                gdb_folders,
                members_matching,
            )



            entries = [os.path.relpath(f, tmp_dir).replace(os.sep, "/") for f in _archive_entries(tmp_dir)
                       if f != filepath]

            def unpacked(name: str) -> str:
                return os.path.join(tmp_dir, *name.split("/"))



            vectors, tables = archive_datasets(entries, unpacked)
            readable = [unpacked(name) for name in vectors]
            gdbs = [unpacked(folder) for folder in gdb_folders(entries)]


            tables = [unpacked(name) for name in tables]
            if not readable and not gdbs:
                readable, tables = tables, []

            candidates = {os.path.relpath(f, tmp_dir).replace(os.sep, "/"): f
                          for f in readable + gdbs + (tables if args.get("layer") else [])}
            wanted = str(args.get("layer") or "").strip()




            rasters = {os.path.relpath(f, tmp_dir).replace(os.sep, "/"): f
                       for f in sorted(_archive_entries(tmp_dir), key=lambda f: (f.count(os.sep), f.lower()))
                       if f != filepath and f.lower().endswith(_ARCHIVE_RASTER_EXTENSIONS)}
            named_raster = members_matching(list(rasters), wanted) if wanted and rasters else []
            if named_raster and not members_matching(list(candidates), wanted):
                return _load_unpacked_raster(tmp_dir, url, [rasters[name] for name in named_raster], layer_name)
            if rasters and not readable and not gdbs and not wanted:
                return _load_unpacked_raster(tmp_dir, url, list(rasters.values()), layer_name)
            if gdbs and not readable and len(gdbs) == 1:


                loaded = _add_vector_layer({"path": gdbs[0], "layer": wanted or None, "name": layer_name})
                if isinstance(loaded, dict) and loaded.get("_error") is not None:
                    return _discard_download(tmp_dir, loaded)
                if isinstance(loaded, dict) and loaded.get("layers") and not loaded.get("layer_id"):
                    loaded["url"] = url
                return loaded
            if wanted and candidates:



                picked = members_matching(list(candidates), wanted)
                if not picked:
                    return _discard_download(tmp_dir, tool_error(
                        f"No vector file named {wanted!r} in the archive.", "INVALID_ARGS",
                        hint="archive_member_not_found", layers=list(candidates)[:_ARCHIVE_LIST_MAX]))
                if len(picked) > 1:


                    from .vector_merge import start_folder_merge

                    sources = [candidates[name] for name in picked]
                    merged_name = layer_name or os.path.splitext(os.path.basename(filepath))[0]
                    return run_on_main_thread(lambda: start_folder_merge(tmp_dir, sources, merged_name),
                                               timeout=30)
                if candidates[picked[0]] in gdbs:
                    gdb = candidates[picked[0]]
                    return _add_vector_layer({"path": gdb, "name": layer_name})
                readable = [candidates[picked[0]]]
            elif len(candidates) > 1:




                listed = list(candidates.values())
                return {
                    "url": url,
                    "layers": _archive_names(listed, tmp_dir),
                    "local_paths": listed[:_ARCHIVE_LIST_MAX],
                    "folder": tmp_dir,
                    "_note": f"The archive holds {len(listed)} vector sources, none added yet.",
                    "load_note": coded_fact(hint="archive_vectors_load", count=len(listed)),
                }
            if readable and len(container_layers(readable[0]) or []) > 1:


                loaded = _add_vector_layer({"path": readable[0], "layer": None, "name": layer_name})
                if isinstance(loaded, dict) and loaded.get("_error") is not None:
                    return _discard_download(tmp_dir, loaded)
                if isinstance(loaded, dict) and loaded.get("layers") and not loaded.get("layer_id"):
                    loaded["url"] = url
                return loaded
            if readable:
                filepath = readable[0]
            else:
                return _discard_download(tmp_dir, tool_error(
                    "The ZIP archive holds no file GDAL reads as vector data or a table.", "INVALID_ARGS",
                    hint="archive_no_vector", files=entries[:_ARCHIVE_LIST_MAX], file_count=len(entries)))
        except zipfile.BadZipFile:
            return _discard_download(tmp_dir, tool_error(
                f"{url} did not download a valid ZIP archive.", "INVALID_ARGS", hint="zip_invalid"))
        except (NotImplementedError, RuntimeError, OSError, EOFError, zlib.error) as exc:



            return _discard_download(tmp_dir, tool_error(
                f"The ZIP archive could not be unpacked: {type(exc).__name__}: {exc}", "INVALID_ARGS",
                hint="zip_unpack_failed"))

    if filepath.lower().endswith(".gz") and ext != ".zip":

        unpacked = _gunzip_download(filepath, extracted_cap)
        if isinstance(unpacked, dict):
            return _discard_download(tmp_dir, unpacked)
        filepath = unpacked
    if not layer_name:
        layer_name = os.path.splitext(os.path.basename(filepath))[0]

    final_layer_name = layer_name
    if filepath.lower().endswith((".dxf", ".dwg")):



        from .layer_io_tools import _add_cad_to_gpkg


        loaded = _add_cad_to_gpkg(filepath, layer_name, args.get("crs"))
        if isinstance(loaded, dict) and loaded.get("_error") is not None:
            return _discard_download(tmp_dir, loaded)
        return loaded
    if filepath.lower().endswith((".csv", ".tsv")):



        from .csv_loader import add_csv

        stem = layer_name or os.path.splitext(os.path.basename(filepath))[0]
        loaded = add_csv(filepath, stem, args.get("crs"), timeout=30)
        if isinstance(loaded, dict) and loaded.get("_error") is not None:
            return _discard_download(tmp_dir, loaded)
        return loaded
    final_filepath = _vector_uri_for(filepath)
    split = None
    if filepath.lower().endswith((".kml", ".kmz")):

        from .vector_merge import split_description, split_note

        try:
            split = split_description(filepath, None, final_layer_name)
        except InterruptedError:
            _discard_download(tmp_dir, {})
            raise
        if split is not None:
            final_filepath = split["uri"]

    def _open(options):
        layer = QgsVectorLayer(final_filepath, final_layer_name, "ogr", options)
        if layer.isValid() and final_filepath.lower().endswith(".gpx") and layer.featureCount() == 0:

            for sublayer in ("tracks", "routes", "track_points"):
                candidate = QgsVectorLayer(f"{final_filepath}|layername={sublayer}", final_layer_name, "ogr", options)
                if candidate.isValid() and candidate.featureCount() > 0:
                    delete_here(layer)
                    layer = candidate
                    break
                delete_here(candidate)
        return layer



    from .layer_io_tools import geometry_split, split_alongside, split_left_out_note

    more: list = []
    left_out = {"n": 0}

    def _open_split(options, on_worker: bool):
        pending = split_alongside(final_filepath) if on_worker else None
        layer = _open(options)
        if not layer.isValid():
            return layer
        source = layer.source()
        parts, left_out["n"] = (pending() if pending is not None and source == final_filepath
                                else geometry_split(source))
        if not parts:
            return layer
        delete_here(layer)

        def label(word):
            return f"{final_layer_name} ({word})" if len(parts) > 1 else final_layer_name

        for uri, word in parts[1:]:
            name = label(word)
            handle = (built_here(lambda u=uri, n=name: QgsVectorLayer(u, n, "ogr", worker_options()))
                      if on_worker else None)
            more.append((handle, uri, name))
        return QgsVectorLayer(parts[0][0], label(parts[0][1]), "ogr", options)


    built = built_here(lambda: _open_split(worker_options(), True))

    def _create():
        layer = take(built)
        if layer is None:
            more.clear()
            layer = _open_split(QgsVectorLayer.LayerOptions(), False)
        siblings = []
        for handle, uri, label in more:
            sibling = take(handle) if handle is not None else None
            siblings.append(sibling if sibling is not None else QgsVectorLayer(uri, label, "ogr"))
        if not layer.isValid():
            return tool_error(f"QGIS could not open {os.path.basename(final_filepath)} as a vector layer: "
                              f"{layer.error().summary() or 'the format is unsupported or the file is empty'}.",
                              "INVALID_ARGS", hint="vector_open_failed")
        QgsProject.instance().addMapLayer(layer)
        added = [layer]
        for sibling in siblings:
            if sibling.isValid():
                QgsProject.instance().addMapLayer(sibling)
                added.append(sibling)
            else:
                delete_here(sibling)
        result = {
            "layer_name": layer.name(),
            "layer_id": layer.id(),
            "feature_count": layer.featureCount(),
            "geometry_type": (
                layer.geometryType().name
                if hasattr(layer.geometryType(), "name")
                else str(layer.geometryType())
            ),
            "crs": crs_ref(layer.crs()),
            "fields": [f.name() for f in layer.fields()],
        }
        if left_out["n"]:
            result["not_loaded"] = split_left_out_note(left_out["n"])
        if split is not None:
            result.update({"description_fields": split["description_fields"], "_note": split_note(split)})
        if len(added) > 1:
            result["layers"] = [{"name": each.name(), "layer_id": each.id(),
                                 "geometry": QgsWkbTypes.geometryDisplayString(each.geometryType()),
                                 "feature_count": each.featureCount()} for each in added]
        if layer.featureCount() == 0:








            result["warning"] = "The layer loaded but holds no features."
            result["empty_note"] = coded_fact(hint="download_layer_empty")
        return result

    out = run_on_main_thread(_create, timeout=30)






    if isinstance(out, dict) and out.get("_error") is not None:
        return _discard_download(tmp_dir, out)
    return out


_LOCAL_VECTOR = {".geojson", ".json", ".gpkg", ".kml", ".kmz", ".shp", ".zip", ".gml", ".fgb", ".sqlite", ".parquet",
                 ".gdb"}
_LOCAL_RASTER = {".tif", ".tiff", ".geotiff", ".jp2", ".img", ".vrt", ".asc", ".nc", ".png", ".jpg"}


_FOLDER_DATA = (_LOCAL_VECTOR | _LOCAL_RASTER | {".csv", ".tsv", ".xlsx", ".xls", ".ods", ".las", ".laz",
                                                 ".vpc"}) - {".png", ".jpg"}
_FOLDER_SCANNED = 500
_FOLDER_SHOWN = 40
_FOLDER_OTHERS_SHOWN = 20


def _csv_crs_candidates(box) -> dict:




    if not box:
        return {}
    from qgis.core import QgsRectangle

    from . import crs_landing

    extent = QgsRectangle(*box)
    out = {"coordinate_range": {"x": [box[0], box[2]], "y": [box[1], box[3]]}}
    found = crs_landing.candidates(extent, crs_landing.anchors())
    if found:
        out["crs_candidates"] = found[:3]
        out["crs_sentence"] = f" Read against the project's layers, {crs_landing.sentence(found)}."
        out["crs_note"] = coded_fact(hint="csv_crs_read_against_layers", best=found[0]["crs"])
    return out


def _inspect_local_file(path: str) -> dict:

    ext = os.path.splitext(path)[1].lower()
    name = os.path.splitext(os.path.basename(path))[0] or "dataset"
    out = {"source_family": "local_file", "path": path, "format": ext.lstrip("."),
           "size_bytes": os.path.getsize(path)}
    if ext in CSV_EXTENSIONS:
        from .csv_loader import sniff
        try:
            info = sniff(path)
        except OSError as exc:
            return {"_error": f"Failed to read {path}: {exc}"}
        out.update({
            "delimiter": info["delimiter"], "decimal_separator": info["decimal"],
            "columns": info["header"][:40], "rows_sampled": info["rows"],
            "x_column": info["lon"], "y_column": info["lat"], "wkt_column": info["wkt"],
        })
        out["import_method"] = "add_vector_layer"
        out["import_arguments"] = {"path": path, "name": name}
        if info["lat"] and info["lon"] or info["wkt"]:
            out["message"] = ("Loads as points through the delimited text provider; the delimiter, "
                              "decimal comma and coordinate columns are handled for you.")
            if info.get("projected"):
                out["projected_coordinates"] = True
                out["message"] += " The coordinates are projected (past the longitude/latitude range)."
                out["projected_note"] = coded_fact(hint="csv_projected_crs")
                found = _csv_crs_candidates(info.get("box"))
                out["message"] += found.pop("crs_sentence", "")
                out.update(found)
        else:
            out["message"] = "No coordinate column."
            out["table_note"] = coded_fact(hint="csv_no_coordinates")
        return out
    if ext in _LOCAL_VECTOR:
        out.update({"import_method": "add_data", "import_arguments": {"source": path, "name": name},
                    "message": "Direct vector import is available."})
        if ext in (".gpkg", ".sqlite", ".gdb", ".kml", ".kmz", ".gml"):
            from .layer_io_tools import _sublayer_names, describe_sublayers
            names = _sublayer_names(path)
            if len(names) > 1:
                out["layers"] = describe_sublayers(path, names)
                out["message"] = f"Holds {len(names)} layers."
                out["layers_note"] = coded_fact(hint="local_file_layers_pick", count=len(names))
        return out
    if ext in _LOCAL_RASTER:
        out.update({"import_method": "add_raster_layer", "import_arguments": {"path": path, "name": name},
                    "message": "Direct raster import is available."})
        return out
    from .layer_io_tools import point_cloud_provider
    provider = point_cloud_provider(path)
    if provider:
        out.update({"import_method": "add_data",
                    "import_arguments": {"source": path, "name": name, "kind": "pointcloud"},
                    "provider": provider,
                    "message": "A point cloud.", "load_note": coded_fact(hint="point_cloud_file")})
        return out
    georeferenced = _geopdf(path) if ext == ".pdf" else None
    if georeferenced:


        out.update(georeferenced)
        out.update({"import_method": "add_raster_layer", "import_arguments": {"path": path, "name": name},
                    "message": "A georeferenced PDF.", "load_note": coded_fact(hint="georeferenced_pdf_file")})
        return out
    if ext in {".txt", ".md", ".pdf", ".docx"}:
        out.update({"message": "A document, not a dataset.", "load_note": coded_fact(hint="document_file")})
        return out
    if ext in {".qgs", ".qgz"}:


        out.update({"import_method": "load_project", "import_arguments": {"path": path},
                    "message": "A QGIS project, not a dataset.", "load_note": coded_fact(hint="qgis_project_file")})
        return out
    out["message"] = "Unknown extension."
    out["load_note"] = coded_fact(hint="unknown_extension_file")
    return out


def _geopdf(path: str) -> dict | None:

    try:
        from osgeo import gdal
        dataset = gdal.OpenEx(path, gdal.OF_RASTER | gdal.OF_READONLY)
    except Exception:  # noqa: BLE001
        return None
    if dataset is None:
        return None
    wkt = dataset.GetProjection() or dataset.GetGCPProjection()
    if not wkt or (dataset.GetGeoTransform(can_return_null=True) is None and not dataset.GetGCPCount()):
        return None
    return {"crs": crs_ref(QgsCoordinateReferenceSystem.fromWkt(wkt)), "width": dataset.RasterXSize,
            "height": dataset.RasterYSize, "bands": dataset.RasterCount}


def _inspect_local_folder(folder: str) -> dict:







    data, folders, others = [], [], []
    scanned = 0
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if scanned >= _FOLDER_SCANNED:
                    break
                scanned += 1
                if entry.name.startswith((".", "~$", "__MACOSX")):
                    continue
                ext = os.path.splitext(entry.name)[1].lower()
                try:
                    is_dir = entry.is_dir()
                    size = None if is_dir else entry.stat().st_size
                except OSError:
                    continue
                if ext in _FOLDER_DATA and (not is_dir or ext == ".gdb"):
                    data.append({"name": entry.name, "path": entry.path, "size_bytes": size})
                elif is_dir:
                    folders.append({"name": entry.name, "path": entry.path})
                else:
                    others.append(entry.name)
    except OSError as exc:
        return tool_error(f"Could not list the folder {folder}: {exc}", "EXECUTION_FAILED", hint="folder_list_failed")

    data = [item for item in data if security.validate_read(item["path"]) is None]
    folders = [dict(item, add={"tool": "inspect_data_source", "args": {"url": item["path"]}})
               for item in sorted(folders, key=lambda item: item["name"].casefold())
               if security.validate_read(item["path"]) is None]

    stems = {item["name"].split(".", 1)[0].casefold() for item in data}
    others = sorted((name for name in others if name.split(".", 1)[0].casefold() not in stems), key=str.casefold)
    shown = []
    for name in others:
        if len(shown) >= _FOLDER_OTHERS_SHOWN:
            break
        if security.validate_read(os.path.join(folder, name)) is None:
            shown.append(name)
    around = {}
    if folders:
        around["folders"] = folders[:_FOLDER_SHOWN]
    if shown:
        around["other_files"] = shown
        around["other_file_count"] = len(others)
    if len(data) == 1:
        out = _inspect_local_file(data[0]["path"])
        out["folder"] = folder
        out.update(around)
        return out
    data.sort(key=lambda item: (links.rank(item), item["name"].casefold()))
    out = {"source_family": "local_folder", "path": folder, "file_count": len(data),
           "layers": [dict(item, add={"tool": "inspect_data_source", "args": {"url": item["path"]}})
                      for item in data[:_FOLDER_SHOWN]], **around}
    out["message"] = "A folder, not a file." if data else "A folder with no data file QGIS opens in it."
    out["folder_note"] = coded_fact(hint="folder_layers" if data else "folder_no_data")
    if scanned >= _FOLDER_SCANNED:
        out["message"] += f" Only its first {_FOLDER_SCANNED} entries were read."
    return out


def _inspect_is_remote(args: dict) -> bool:


    url = str(args.get("url") or "").strip()
    return url.lower().startswith(("http://", "https://"))


_DIRECT_IMPORT_EXTS = frozenset({".geojson", ".json", ".gpkg", ".kml", ".csv", ".zip", ".shp"})








_HOSTED_DEPARTMENT_PATHS = (
    (re.compile(r"(/ign/rgealti/5m/rgealti-5m-D)(\d{1,2}|2[ab])(\.tif)$", re.IGNORECASE), 3),
    (re.compile(r"(/reference/ign/lidarhd-mnt1m/)(\d)(\.tif)$", re.IGNORECASE), 2),
)


def hosted_department_url(url: str) -> str:

    if not links.open_data_label(url or ""):
        return url
    parts = urllib.parse.urlsplit(url)
    for pattern, width in _HOSTED_DEPARTMENT_PATHS:
        match = pattern.search(parts.path)
        if match:
            head, code, tail = match.groups()
            code = code.upper().zfill(width)
            path = parts.path[:match.start()] + head + code + tail
            return urllib.parse.urlunsplit(parts._replace(path=path))
    return url


_FORMAT_SEGMENTS = {"geojson": ".geojson", "gpkg": ".gpkg", "kml": ".kml",
                    "csv": ".csv", "shp": ".shp", "shapefile": ".zip", "zip": ".zip"}


def _format_named_by_url(url: str) -> str | None:






    parts = urllib.parse.urlparse(url)
    last = parts.path.rstrip("/").rsplit("/", 1)[-1].lower()
    if last in _FORMAT_SEGMENTS:
        return _FORMAT_SEGMENTS[last]
    for value in urllib.parse.parse_qs(parts.query).get("format", []):
        if value.lower() in _FORMAT_SEGMENTS:
            return _FORMAT_SEGMENTS[value.lower()]
    return None


def _inspect_by_head(url: str, ext: str) -> dict | None:





    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": _USER_AGENT})
    try:


        answer = net.fetch(req, timeout=_PORTAL_TIMEOUT, max_bytes=1024,
                           total_timeout=_PORTAL_TIMEOUT)
    except (urllib.error.URLError, OSError):
        return None
    final_url = answer.url or url
    path = urllib.parse.urlparse(final_url).path.lower()
    final_ext = os.path.splitext(path)[1] or ext
    if final_ext not in _DIRECT_IMPORT_EXTS:
        return None
    result = _build_direct_import_result(
        final_url, os.path.basename(path) or "dataset", "vector_file", final_ext.lstrip("."))
    length = next((str(v) for k, v in (answer.headers or {}).items()
                   if str(k).lower() == "content-length"), "").strip()
    if length.isdigit():
        result["size_bytes"] = int(length)
    return result


def _landing_url(url: str) -> str:






    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": _USER_AGENT})
    try:
        answer = net.fetch(req, timeout=_PORTAL_TIMEOUT, max_bytes=1024, total_timeout=_PORTAL_TIMEOUT)
    except (urllib.error.URLError, OSError):
        return ""
    return answer.url or ""









_INSPECT_HTTP_ADVICE: dict[int, tuple[str, str, dict]] = {
    400: ("INVALID_ARGS", "the server rejected the request itself", coded_fact(hint="inspect_http_400")),
    401: ("EXECUTION_FAILED", "the service wants credentials", coded_fact(hint="inspect_http_401")),
    403: ("EXECUTION_FAILED", "the service refused us", coded_fact(hint="inspect_http_403")),
    404: ("INVALID_ARGS", "there is nothing at this address", coded_fact(hint="inspect_http_404")),
    405: ("INVALID_ARGS", "the service refuses this method", coded_fact(hint="inspect_http_405")),
    409: ("INVALID_ARGS", "the request conflicts with what the service holds",
          coded_fact(hint="inspect_http_409")),
    410: ("INVALID_ARGS", "this address is gone for good", coded_fact(hint="inspect_http_410")),
    429: ("EXECUTION_FAILED", "the host is rate limiting us", coded_fact(hint="inspect_http_429")),
}
_INSPECT_SERVER_ERROR = ("EXECUTION_FAILED", "the service failed on its side", coded_fact(hint="inspect_http_5xx"))









_REASON_BYTES = 4096
_REASON_CHARS = 300
_OWS_EXCEPTION_RE = re.compile(rb"<(?:\w+:)?(?:ExceptionText|ServiceException)[^>]*>\s*(.*?)\s*</", re.DOTALL)
_JSON_REASON_KEYS = ("message", "error", "detail", "description", "reason", "title")


def _json_reason(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in _JSON_REASON_KEYS:
            found = _json_reason(value.get(key))
            if found:
                return found
    if isinstance(value, list) and value:
        return _json_reason(value[0])
    return ""


def _service_reason(exc: Exception) -> str:

    if not isinstance(exc, urllib.error.HTTPError):
        return ""
    try:
        body = exc.read(_REASON_BYTES) or b""
    except Exception:  # noqa: BLE001
        return ""
    if body[:2] == b"\x1f\x8b":

        try:
            body = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(body, _REASON_BYTES * 4)
        except zlib.error:
            return ""
    head = body.lstrip()[:15].lower()
    if head.startswith((b"<!doctype html", b"<html")):
        return ""
    found = _OWS_EXCEPTION_RE.search(body)
    if found:
        text = found.group(1).decode("utf-8", errors="replace")
    elif head[:1] in (b"{", b"["):
        try:
            text = _json_reason(json.loads(body.decode("utf-8", errors="replace")))
        except ValueError:
            text = body.decode("utf-8", errors="replace")
    else:
        text = body.decode("utf-8", errors="replace")
    return " ".join(text.split())[:_REASON_CHARS]


def _http_refusal(url: str, exc: Exception) -> dict | None:


    status = getattr(exc, "code", None)
    if not isinstance(status, int):
        return None
    code, what, fact = _INSPECT_HTTP_ADVICE.get(
        status, _INSPECT_SERVER_ERROR if status >= 500 else
        ("EXECUTION_FAILED", "the service refused the request", {}))
    message = f"HTTP {status} from {url}: {what}."
    reason = _service_reason(exc)
    if reason:
        message += f" The service says: {reason}"
    out = tool_error(message, code, suggestion="" if fact else "The status says why.", **fact)
    out["status"] = status
    if reason:
        out["service_says"] = reason
    return out


def _inspect_failure(url: str, exc: Exception) -> dict:

    refused = _http_refusal(url, exc)
    if refused:
        return refused
    if isinstance(exc, (net.FetchDeadline, TimeoutError)):
        return tool_error(f"The probe of {url} ran past its deadline.", "EXECUTION_FAILED", hint="probe_late")
    if isinstance(exc, net.FetchTooLarge):


        size = _remote_size(url)
        return {"source_family": "remote_file", "final_url": url,
                "import_method": "add_data", "import_arguments": {"source": url},
                **({"size_bytes": size} if size else {}),
                "message": ("A bulk file" + (f" of {_human_bytes(size)}" if size else "")
                            + ", not a service description: add_data downloads it.")}
    return tool_error(f"Failed to inspect source: {exc}", "EXECUTION_FAILED", hint="probe_no_answer")


def _unquoted_path(raw: str) -> str:





    text = raw.strip()
    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'`" or text[:1] + text[-1:] == "<>":
        text = text[1:-1].strip()
    drive = len(text) > 2 and text[0].isalpha() and text[1] == ":" and text[2] in "\\/"
    if drive or text.startswith(("/", "~", "\\\\", "file://")):
        return text
    return ""


def _inspect_data_source(args: dict) -> dict:
    raw = str(args["url"]).strip()



    if raw.startswith("<") and not _unquoted_path(raw):
        return _inspect_xml_payload("", raw)
    url = _unquoted_path(raw) or hosted_department_url(links.clean(raw))
    if url.startswith("file://"):



        parts = urllib.parse.urlsplit(url)
        if parts.netloc in ("", "localhost"):
            local = urllib.request.url2pathname(parts.path)
        elif IS_WINDOWS and re.fullmatch(r"[\w.-]+", parts.netloc):

            local = urllib.request.url2pathname("//" + parts.netloc + parts.path)
        else:
            local = urllib.parse.unquote(url[7:])
    else:
        local = url
    local = os.path.expanduser(local)
    if os.path.isfile(local) or (os.path.isdir(local) and local.rstrip("/\\").lower().endswith(".gdb")):

        return _inspect_local_file(os.path.abspath(local))
    if os.path.isdir(local):
        return _inspect_local_folder(os.path.abspath(local))
    if not url.lower().startswith(("http://", "https://")):
        return missing_file(local, f"Not a URL and no file at this path: {url}")


    link = expand_link(url)
    answer = _link_inspect_answer(link)
    if answer is not None:
        return answer
    result = _inspect_remote_url(link["url"])
    if link.get("resolved_from") and isinstance(result, dict):
        result["resolved_from"] = link["resolved_from"]
        result.setdefault("resolved_note", link["note"])


        arguments = result.get("import_arguments")
        if isinstance(arguments, dict) and arguments.get("url") and arguments["url"] != link["url"]:
            arguments["url"] = link["url"]
    return result




_STREAMED_EXTS = {".pmtiles": "pmtiles", ".tif": "cog", ".tiff": "cog", ".fgb": "vector", ".parquet": "vector",
                  ".geoparquet": "vector", ".laz": "pointcloud", ".copc": "pointcloud", ".nc": "raster"}

_DOWNLOAD_EXTS = (".kmz", ".gpx", ".xlsx", ".ods", ".gml", ".json.gz", ".geojson.gz", ".csv.gz", ".gpkg.gz")




_LINK_LISTING_TTL = 600
_LINK_LISTING_BYTES = 5 * 1024 * 1024


def expand_link(url: str) -> dict:






    pasted = links.clean(url)
    resolved = links.resolve(pasted)
    out: dict = {"url": resolved.url, "kind": resolved.kind, "note": resolved.note}
    if resolved.kind != "unchanged":
        out["resolved_from"] = pasted
    if resolved.kind == "inline":
        out["inline"] = resolved.inline
    if resolved.kind != "listing":
        return out
    request = urllib.request.Request(resolved.url, headers={"User-Agent": _USER_AGENT,
                                                            "Accept": "application/json"})
    try:
        answer = net.fetch(request, timeout=_PORTAL_TIMEOUT, max_bytes=_LINK_LISTING_BYTES,
                           total_timeout=_PORTAL_TIMEOUT * 2, cache_ttl=_LINK_LISTING_TTL)
        payload = json.loads(answer.body.decode("utf-8", "replace"))
    except net.FetchCancelled:
        raise
    except (urllib.error.URLError, OSError, ValueError) as exc:
        if resolved.optional:

            return {"url": pasted, "kind": "unchanged", "note": ""}
        status = getattr(exc, "code", None)
        out["error"] = tool_error(f"Could not list the files behind {pasted}: {exc}", "EXECUTION_FAILED",
                                  hint="link_listing_refused", **({"variant": "rate_limit"} if status == 403 else {}))
        return out
    files = sorted(links.listing_files(resolved.listing, resolved.url, payload), key=links.rank)
    out["files"] = [dict(entry, add=_link_file_call(entry)) for entry in files]
    out.update(links.listing_terms(resolved.listing, payload))
    return out


def _link_data_files(link: dict) -> list:
    return [f for f in link.get("files") or [] if not f.get("folder") and (
        f.get("service") or (links.extension(f.get("name", "")) or links.extension(f["url"])) in links.DATA_EXTENSIONS)]


def _link_inspect_answer(link: dict) -> dict | None:

    if link.get("error"):
        return link["error"]
    kind = link["kind"]
    if kind == "unreachable":
        return {"source_family": "share_link_unreachable", "resolved_from": link["resolved_from"],
                "message": link["note"]}
    if kind == "inline":
        try:
            payload = json.loads(link["inline"])
        except ValueError:
            return {"source_family": "unknown", "resolved_from": link["resolved_from"],
                    "message": "The link carries data inside it, and it is not valid JSON."}
        count = len(payload.get("features") or []) if isinstance(payload, dict) else 0
        return {"source_family": "inline_geojson", "resolved_from": link["resolved_from"],
                **({"feature_count": count} if count else {}), "geojson": link["inline"][:2000],
                "import_method": "add_vector_from_url", "import_arguments": {"url": link["resolved_from"]},
                "message": link["note"], "load_note": coded_fact(hint="inline_geojson_load")}
    if kind != "listing":
        return None
    files = link.get("files") or []
    out = {"source_family": "file_listing", "resolved_from": link["resolved_from"], "listing_url": link["url"],
           "file_count": len(files), "layers": files,
           **{k: link[k] for k in ("licence", "publisher") if link.get(k)},
           "message": link["note"] + ("" if files else " The listing is empty: nothing public is attached there.")}
    if files:
        out["layers_note"] = coded_fact(hint="file_listing_layers")
    data = _link_data_files(link)
    if len(data) == 1:
        out["import_method"] = data[0]["add"]["tool"]
        out["import_arguments"] = data[0]["add"]["args"]
    return out


def _inspect_remote_url(url: str, landed: bool = False) -> dict:
    path = urllib.parse.urlparse(url).path
    streamed = os.path.splitext(path.lower())[1]
    name = os.path.splitext(os.path.basename(path))[0] or "dataset"
    lowered = url.lower()
    if "{z}" in lowered and "{x}" in lowered and "{y}" in lowered:

        vector = streamed in (".pbf", ".mvt")
        return {"source_family": "tile_template", "final_url": url,
                "import_method": "add_data", "import_arguments": {"source": url, "kind": "vectortile" if vector
                                                                  else "xyz"},
                "message": "A tile URL template.",
                "load_note": coded_fact(hint="tile_template_load", tile_kind="vector tile" if vector else "tile")}
    if streamed in _STREAMED_EXTS:
        kind = _STREAMED_EXTS[streamed]
        return {"source_family": "remote_file", "final_url": url, "format": streamed.lstrip("."),
                "import_method": "add_data", "import_arguments": {"source": url, "name": name, "kind": kind},
                "message": "A remote file." if kind != "raster" else "A NetCDF file.",
                "load_note": (coded_fact(hint="remote_file_range_read") if kind != "raster"
                              else coded_fact(hint="remote_netcdf_file"))}
    if lowered.split("?", 1)[0].endswith(_DOWNLOAD_EXTS):
        result = _build_direct_import_result(url, os.path.basename(path) or "dataset", "vector_file",
                                             os.path.basename(path).split(".", 1)[-1])
        result["message"] = "A downloadable file."
        result["load_note"] = coded_fact(hint="remote_download_file")
        return result





    head_ext = os.path.splitext(urllib.parse.urlparse(url).path.lower())[1]
    if head_ext in _DIRECT_IMPORT_EXTS and head_ext != ".json":
        probed = _inspect_by_head(url, head_ext)
        if probed is not None:
            return probed





    named = _format_named_by_url(url)
    if named:
        return _inspect_by_head(url, named) or _build_direct_import_result(
            url, os.path.basename(urllib.parse.urlparse(url).path.rstrip("/")) or "dataset",
            "vector_file", named.lstrip("."))





    request = ogc_inspect.ogc_request(url)
    if request is not None:
        probe = request["capabilities"]
        answer, _ = _inspect_fetched(probe, _INSPECT_TIMEOUT)
        answer = _inspect_follow(probe, answer)
        answer["asked_for_capabilities"] = probe
        return ogc_inspect.attach_layer_in_link(answer, url, request)






    if not landed:
        landing = _landing_url(url)
        if landing and landing != url:
            result = _inspect_remote_url(landing, landed=True)
            result.setdefault("resolved_from", url)
            return result


    url = ogc_inspect.arcgis_json_url(url)
    result, html_answer = _inspect_fetched(url, _INSPECT_TIMEOUT)
    result = _inspect_follow(url, result)
    if result.get("source_family") in ogc_inspect.RECOGNIZED_FAMILIES or not _worth_probing(result):
        return result




    for probe in ogc_inspect.capabilities_probes(url, html_answer):
        answer, _ = _inspect_fetched(probe, _INSPECT_PROBE_TIMEOUT)
        answer = _inspect_follow(probe, answer)
        if answer.get("source_family") in ogc_inspect.RECOGNIZED_FAMILIES:
            answer["asked_for_capabilities"] = probe
            return answer
    return result





_INSPECT_PROBE_TIMEOUT = 10
_PROBE_STATUSES = frozenset({400, 404, 405, 500, 501})
_PROBE_FAMILIES = frozenset({"unknown", "xml", "ogc_exception", "json_api", "ogc_api"})


def _worth_probing(result: dict) -> bool:

    if result.get("_error"):
        return result.get("status") in _PROBE_STATUSES
    return result.get("source_family") in _PROBE_FAMILIES


def _inspect_follow(url: str, result: dict) -> dict:

    follow = result.pop("follow", None) if isinstance(result, dict) else None
    if not follow:
        return result
    followed, _ = _inspect_fetched(follow, _INSPECT_TIMEOUT)
    if followed.get("_error"):
        return {"source_family": "ogc_api_landing", "final_url": url, "collections_url": follow,
                "message": "An OGC API landing page. Its collections did not answer.",
                "follow_note": coded_fact(hint="ogc_api_collections_unanswered")}
    return followed


def _response_charset(content_type: str) -> str:








    declared = http_headers.declared_charset(content_type)
    if not declared:
        return "utf-8"
    import codecs

    try:
        return codecs.lookup(declared).name
    except LookupError:
        return "utf-8"


def _inspect_fetched(url: str, timeout: int) -> tuple[dict, bool]:

    try:
        body, headers, final_url = _http_fetch(url, timeout=timeout, cache_ttl=_CACHE_CATALOG_S)
    except net.FetchCancelled:
        raise
    except (urllib.error.URLError, OSError) as e:
        return _inspect_failure(url, e), False

    raw_content_type = headers.get("content-type") or ""
    content_type = raw_content_type.split(";", 1)[0].strip().lower()
    path = urllib.parse.urlparse(final_url).path.lower()
    ext = _extension_of_download(path, headers, body)
    if "json" in content_type and ext == ".geojson" and not path.endswith((".json", ".geojson")):
        ext = ""
    text = body.decode(_response_charset(raw_content_type), "replace")

    if ext in _DIRECT_IMPORT_EXTS:
        return _build_direct_import_result(
            final_url,
            os.path.basename(path) or "dataset",
            "vector_file",
            ext.lstrip(".") or content_type,
        ), False

    if "xml" in content_type or text.lstrip().startswith("<?xml"):
        return _inspect_xml_payload(final_url, ogc_inspect.decode_xml(body)), False

    if "json" in content_type or text.lstrip().startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {
                "source_family": "unknown",
                "final_url": final_url,
                "content_type": content_type,
                "message": "Response looked like JSON but could not be parsed.",
            }, False
        result = _inspect_json_payload(final_url, payload)
        if "follow" not in result:
            result["content_type"] = content_type
        return result, False

    if "html" in content_type or text.lstrip()[:15].lower().startswith(("<!doctype html", "<html")):
        found = _page_distributions(final_url, text)
        if found:
            out = {"source_family": "dataset_page", "final_url": final_url, "content_type": content_type or "text/html",
                   "distributions": found, "layers": found,
                   "message": "A web page that links to data files.",
                   "distributions_note": coded_fact(hint="dataset_page_distributions", limit=_MAX_DISTRIBUTIONS)}
            if len(found) == 1 or links.rank(found[0]) < links.rank(found[1]):
                out["import_method"] = found[0]["add"]["tool"]
                out["import_arguments"] = found[0]["add"]["args"]
            return out, True
    return {
        "source_family": "unknown",
        "final_url": final_url,
        "content_type": content_type or "unknown",
        "message": "Could not classify this source.",
        "classify_note": coded_fact(hint="source_unclassified"),
    }, "html" in content_type
