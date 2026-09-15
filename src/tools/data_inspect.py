# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Remote and local data sources: range reads, downloads, extract, routing, inspection and open-data link expansion."""

from __future__ import annotations

import json
import os
import posixpath
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib

from qgis.core import QgsProject, QgsVectorLayer

from ..core import limits, links, net, vsi
from ..core.policy import create_managed_temp_dir
from . import ogc_inspect, volume_guard
from .csv_loader import CSV_EXTENSIONS
from .data_common import (
    _CACHE_CATALOG_S,
    _DOWNLOAD_TOTAL_TIMEOUT,
    _INSPECT_TIMEOUT,
    _MAX_ARCHIVE_ENTRIES,
    _MAX_DOWNLOAD_SIZE,
    _MAX_EXTRACTED_SIZE,
    _PORTAL_TIMEOUT,
    _USER_AGENT,
    _avoid_reserved_name,
    _bbox_km2,
    _download_timeout,
    _http_fetch,
    _layer_from_geojson_str,
    _ogr_feature_count,
    _run_on_main_thread,
    _safe_filename,
    _vector_uri_for,
)
from .data_portals import (
    _build_direct_import_result,
    _inspect_json_payload,
    _inspect_xml_payload,
    _link_file_call,
    _page_distributions,
)




RANGE_READABLE_EXTENSIONS = (".parquet", ".geoparquet", ".fgb", ".gpkg", ".pmtiles")
_VSICURL_TIMEOUT_S = 120




_WARN_REMOTE_BYTES = 200 * 1024 * 1024
_COUNT_CEILING = 200_000



_RANGE_PROBE_BYTES = 1024


_CACHE_PROBE_S = 300.0


def _remote_size(url: str) -> int:
    """Bytes the server says the file is, or 0 when it will not say."""




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
    except Exception:  # noqa: BLE001 - not knowing the size is not a failure
        return 0


def _human_bytes(size: int) -> str:
    step = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if step < 1024 or unit == "TB":
            return f"{step:.0f} {unit}" if unit in ("B", "KB") else f"{step:.1f} {unit}"
        step /= 1024
    return f"{size} B"


def _tune_gdal_for_range_reads() -> None:
    """Apply the process-wide remote-read settings, once."""







    vsi.apply_persistent()


def _range_readable(url: str) -> bool:
    path = urllib.parse.urlparse(url).path.lower()
    return path.endswith(RANGE_READABLE_EXTENSIONS)


def _features_in_box(source: str, sublayer: str | None, bbox) -> int | None:
    """How many features of a remote indexed file fall in *bbox*, read off its index, or None."""
    from osgeo import ogr, osr

    west, south, east, north = bbox
    dataset = layer = None
    try:
        dataset = ogr.Open(source)
        layer = (dataset.GetLayerByName(str(sublayer)) if sublayer else dataset.GetLayer(0)) if dataset else None
        if layer is None:
            return None
        box = (west, south, east, north)
        layer_srs = layer.GetSpatialRef()
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
        return int(layer.GetFeatureCount(1))
    except Exception:  # noqa: BLE001 - a file that cannot say is held to the area ceiling instead
        return None
    finally:
        layer = None
        dataset = None


def _extract_remote_vector(source: str, url: str, name: str, sublayer: str | None, bbox: list,
                           args: dict | None = None) -> dict:
    """Cut the box out of a remote indexed file into a local GeoPackage."""













    from osgeo import gdal

    gdal.UseExceptions()
    west, south, east, north = bbox
    count = _features_in_box(source, sublayer, bbox)
    if count is None:
        area = _bbox_km2(south, west, north, east)
        ceiling = limits.current("MAX_FETCH_KM2")
        if area > ceiling and not volume_guard.lifted(args or {}):
            return {"_error": (f"The box is {area:,.0f} km2 and {posixpath.basename(urllib.parse.urlparse(url).path)} "
                               f"could not say how many features it holds, so the {ceiling:,.0f} km2 limit for one "
                               "extract applies."),
                    "code": limits.CEILING_CODE,
                    "suggestion": f"Cut the box to {ceiling:,.0f} km2 around the area of interest and say which part."}
    else:
        refused = volume_guard.too_many(count, args or {}, f"The box asked of {name}", on_disk=True)
        if refused:
            refused.setdefault("code", limits.CEILING_CODE)
            return refused
    directory = create_managed_temp_dir("extract")
    path = os.path.join(directory, f"{_safe_extract_stem(name)}.gpkg")
    kwargs = {"format": "GPKG", "spatFilter": [west, south, east, north],
              "spatSRS": "EPSG:4326", "dstSRS": "EPSG:4326"}
    if sublayer:
        kwargs["layers"] = [str(sublayer)]
    try:
        written = gdal.VectorTranslate(path, source, **kwargs)
    except RuntimeError as exc:
        return {"_error": f"The extract from {url} failed: {exc}",
                "code": "EXECUTION_FAILED",
                "suggestion": "Try a smaller box, or add the source without bbox and work at its own scale."}
    if written is None:
        return {"_error": f"The extract from {url} produced nothing.",
                "code": "EXECUTION_FAILED",
                "suggestion": "Try a larger box: the file may hold nothing here."}
    out_layer = written.GetLayer(0)
    count = int(out_layer.GetFeatureCount()) if out_layer is not None else 0
    layer_in_file = out_layer.GetName() if out_layer is not None else None
    del out_layer
    del written
    return {"path": path, "feature_count": count, "layer_in_file": layer_in_file}


def _safe_extract_stem(text: str) -> str:
    """A file stem from a layer name: letters, digits, dash and underscore."""




    stem = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(text or "extract"))
    return _avoid_reserved_name(stem.strip("_")[:60] or "extract")


def _add_vector_over_range_requests(url: str, layer_name: str | None, sublayer: str | None = None,
                                    bbox=None, args: dict | None = None) -> dict:
    """Open a remote cloud-native vector file in place, without downloading it."""












    net.check_url(url)
    _tune_gdal_for_range_reads()
    name = layer_name or os.path.splitext(posixpath.basename(urllib.parse.urlparse(url).path))[0] or "layer"
    size = _remote_size(url)
    source = f"/vsicurl/{url}"
    if sublayer:



        if "|" in str(sublayer):
            return {"_error": f"'{sublayer}' is not a layer name: the | character separates provider options.",
                    "code": "INVALID_ARGS",
                    "suggestion": "Pass the layer name on its own; inspect_data_source lists them."}
        source = f"{source}|layername={sublayer}"

    if bbox:
        cut = _extract_remote_vector(source.split("|", 1)[0], url, name, sublayer, bbox, args)
        if "_error" in cut:
            return cut
        local = cut["path"]
        if cut.get("layer_in_file"):
            local = f"{local}|layername={cut['layer_in_file']}"

        def _create_local():
            layer = QgsVectorLayer(local, name, "ogr")
            if not layer.isValid():
                return {"_error": f"QGIS could not read the extract cut from {url}.",
                        "_code": "EXECUTION_FAILED",
                        "_suggestion": "Add the source without bbox and work at its own scale."}
            QgsProject.instance().addMapLayer(layer)
            return {
                "layer_name": layer.name(),
                "layer_id": layer.id(),
                "url": url,
                "provider": "ogr, local extract",
                "crs": layer.crs().authid(),
                "fields": [f.name() for f in layer.fields()],
                "feature_count": cut["feature_count"],
                "extract_bbox": list(bbox),
                "_note": ("The box was cut out of the remote file through its spatial index and written "
                          "locally, so this layer holds only the area asked for and every read of it is "
                          "local. The whole file was not downloaded."),
            }

        return _run_on_main_thread(_create_local, timeout=_VSICURL_TIMEOUT_S)

    def _create():
        layer = QgsVectorLayer(source, name, "ogr")
        if not layer.isValid():
            return {"_error": f"QGIS could not read {url} over HTTP range requests.",
                    "_code": "INVALID_ARGS",
                    "_suggestion": "The server may not support range requests. Ask for a direct download link "
                                   "to a GeoJSON or GeoPackage extract instead."}
        QgsProject.instance().addMapLayer(layer)
        out = {
            "layer_name": layer.name(),
            "layer_id": layer.id(),
            "url": url,
            "provider": "ogr over /vsicurl/",
            "crs": layer.crs().authid(),
            "fields": [f.name() for f in layer.fields()],
            "_note": "Read in place over HTTP range requests: the whole file is not copied, but the header, "
                     "the spatial index and every block the view touches are fetched, and how much that is "
                     "depends on the format's index, not on the extent alone.",
        }
        if size:
            out["size_bytes"] = size
            out["size"] = _human_bytes(size)
            if size >= _WARN_REMOTE_BYTES:






                out["_note"] += (f" At {_human_bytes(size)} this file is only usable zoomed in. Unless the user "
                                 f"wants it whole, call add_data again with bbox=[west, south, east, north] for "
                                 f"the area of interest: the box is cut out through the file's own index and the "
                                 f"extract is local. Do not filter or list features on this layer as it stands: "
                                 f"without a box, every such read walks the file over the network.")
        if sublayer:
            out["layer"] = sublayer
        else:
            others = _sublayers_of(layer)
            if len(others) > 1:
                out["layers_available"] = others
                out["_note"] += f" This source holds {len(others)} layers; pass layer=<name> to pick another."


        if size and size < _WARN_REMOTE_BYTES:
            count = layer.featureCount()
            if count is not None and 0 <= count <= _COUNT_CEILING:
                out["feature_count"] = int(count)
        return out

    return _run_on_main_thread(_create, timeout=_VSICURL_TIMEOUT_S)







_SINGLE_LAYER_EXTENSIONS = (".fgb", ".geojson", ".json", ".parquet", ".pmtiles", ".shp", ".csv", ".kml")


def _sublayers_of(layer) -> list:
    """Layer names inside a multi-layer container, cheap and best effort."""
    try:
        source = str(layer.source() or "").split("|", 1)[0]




        path = (urllib.parse.urlparse(source).path or source).lower()
        if path.endswith(_SINGLE_LAYER_EXTENSIONS):
            return []
        try:
            from qgis.core import QgsProviderRegistry



            details = QgsProviderRegistry.instance().querySublayers(source)
            names = [str(d.name()) for d in details]
        except Exception:  # noqa: BLE001 - older QGIS without querySublayers

            names = [(str(entry).split("!!::!!") + ["", ""])[1]
                     for entry in (layer.dataProvider().subLayers() or [])]
        return [n for n in dict.fromkeys(names) if n][:20]
    except Exception:  # noqa: BLE001 - a driver without sublayers is the normal case
        return []


_ARCHIVE_VECTOR_EXTENSIONS = (".shp", ".gpkg", ".geojson")


def _archive_entries(root: str) -> list:
    """Every file name under *root*, at any depth."""
    names = []
    for folder, _dirs, files in os.walk(root):
        names.extend(os.path.join(folder, f) for f in files)
    return names


def _archive_vector_files(root: str) -> dict:
    """The vector files an unpacked archive holds, by extension, deepest last."""







    out = {ext: [] for ext in _ARCHIVE_VECTOR_EXTENSIONS}
    for path in _archive_entries(root):
        ext = os.path.splitext(path)[1].lower()
        if ext == ".json":
            ext = ".geojson"
        if ext in out:
            out[ext].append(path)
    for ext in out:
        out[ext].sort(key=lambda f: (f.count(os.sep), f.lower()))
    return out


def _discard_download(tmp_dir: str, result: dict) -> dict:
    """Drop the temp dir this one call created, then return its error."""




    try:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:  # noqa: BLE001  # nosec B110 - the error being returned is the point
        pass
    return result




_SHAPEFILE_COMPANIONS = (".dbf", ".shx")


def _extract_member(zf, member, tmp_dir: str) -> None:
    """Unpack one archive entry under a name every OS will open."""








    parts = [part for part in member.filename.replace("\\", "/").split("/")
             if part not in ("", ".", "..")]
    if not parts:
        return
    target = os.path.join(tmp_dir, *[_safe_filename(part, "entry") for part in parts])
    if member.is_dir():
        os.makedirs(target, exist_ok=True)
        return
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with zf.open(member) as source, open(target, "wb") as sink:
        shutil.copyfileobj(source, sink)


def _shapefile_is_complete(path: str) -> bool:
    """True when the .dbf and .shx that make a .shp readable sit beside it."""
    stem = os.path.splitext(path)[0]
    siblings = {}
    try:
        directory = os.path.dirname(path) or "."
        for name in os.listdir(directory):
            siblings[name.lower()] = True
    except OSError:
        return False
    base = os.path.basename(stem).lower()
    return all(f"{base}{ext}" in siblings for ext in _SHAPEFILE_COMPANIONS)








_CONTENT_TYPE_EXTS = {"application/zip": ".zip", "application/x-zip-compressed": ".zip",
                      "application/geopackage+sqlite3": ".gpkg", "application/geo+json": ".geojson",
                      "application/json": ".geojson", "application/vnd.google-earth.kml+xml": ".kml",
                      "text/csv": ".csv"}





_ENDPOINT_EXTENSIONS = frozenset({".ashx", ".aspx", ".asp", ".php", ".jsp", ".cgi", ".do", ".action", ".pl"})


def _extension_of_download(path_name: str, headers, body: bytes) -> str:
    """The extension the file really has: the path's, the disposition's, the type's, or the bytes'."""
    ext = os.path.splitext(path_name)[1].lower()
    if ext and ext not in _ENDPOINT_EXTENSIONS:
        return ext
    disposition = headers.get("content-disposition") or ""
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disposition, re.IGNORECASE)
    if match:
        ext = os.path.splitext(match.group(1).strip())[1].lower()
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
        return {"_error": link["note"], "code": "INVALID_ARGS",
                "suggestion": "Ask the user for the link of the file itself, shared with anyone who has the link."}
    if kind == "inline":
        return _add_inline_geojson(link, args.get("layer_name"))
    url = link["url"]
    if kind == "listing":
        data = _link_data_files(link)
        if len(data) != 1:
            return {"_error": f"{link['note']} It holds {len(data)} data files, so none was picked.",
                    "code": "INVALID_ARGS",
                    "files": [{"name": f["name"], "url": f["url"]} for f in data[:15]]
                    or [{"name": f["name"], "url": f["url"]} for f in (link.get("files") or [])[:15]],
                    "suggestion": "Call add_data with the url of the one file wanted, from files."}
        if data[0]["add"]["tool"] != "add_vector_from_url":

            return {"_error": f"{link['note']} It is read with {data[0]['add']['tool']}, not downloaded.",
                    "code": "INVALID_ARGS", "call": data[0]["add"],
                    "suggestion": f"Call {data[0]['add']['tool']} with the arguments in call."}
        url = data[0]["url"]
    out = _download_vector_from_url(args, url, args.get("layer_name"), shared=bool(link.get("resolved_from")))
    if link.get("resolved_from") and isinstance(out, dict):
        out["resolved_from"] = link["resolved_from"]
    return out


def _add_inline_geojson(link: dict, layer_name) -> dict:
    """The GeoJSON a geojson.io link carries, as a layer, with no download."""
    text = link.get("inline") or ""
    try:
        json.loads(text)
    except ValueError:
        return {"_error": "The link carries data inside it, and it is not valid GeoJSON.", "code": "INVALID_ARGS",
                "suggestion": "Ask the user to export the data from geojson.io as a file."}
    name = layer_name or "geojson.io"

    def _create():
        layer = _layer_from_geojson_str(text, name, "geojson")
        if not layer.isValid():
            return {"_error": "QGIS could not read the GeoJSON the link carries.", "code": "INVALID_ARGS"}
        QgsProject.instance().addMapLayer(layer)
        return {"layer_name": layer.name(), "layer_id": layer.id(), "feature_count": layer.featureCount(),
                "resolved_from": link["resolved_from"]}

    return _run_on_main_thread(_create, timeout=30)


_DISPOSITION_STAR_RE = re.compile(r"filename\*\s*=\s*(?:[\w-]+)'[^']*'([^;]+)", re.IGNORECASE)
_DISPOSITION_RE = re.compile(r'filename\s*=\s*"?([^";]+)"?', re.IGNORECASE)


def _disposition_filename(disposition: str) -> str:
    """The file name a Content-Disposition header gives, filename* first, else ""."""
    found = _DISPOSITION_STAR_RE.search(disposition or "")
    if found:
        return posixpath.basename(urllib.parse.unquote(found.group(1).strip().strip('"')))
    found = _DISPOSITION_RE.search(disposition or "")
    return posixpath.basename(found.group(1).strip()) if found else ""


def _gunzip_download(filepath: str):
    """The gzipped download unpacked beside it, capped like an archive; an error result on failure."""






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
                if written > _MAX_EXTRACTED_SIZE:
                    return {"_error": f"The gzipped file unpacks to more than {_human_bytes(_MAX_EXTRACTED_SIZE)}.",
                            "suggestion": "Ask the provider for a smaller extract of the area."}
                sink.write(chunk)
    except (OSError, EOFError, zlib.error) as exc:

        return {"_error": f"The .gz download could not be unpacked: {exc}", "code": "INVALID_ARGS",
                "suggestion": "inspect_data_source says what the URL really serves."}
    return target


def _download_vector_from_url(args: dict, url: str, layer_name, shared: bool = False) -> dict:
    if _range_readable(url):
        return _add_vector_over_range_requests(url, layer_name, args.get("layer"), args.get("bbox"), args=args)




    size = _remote_size(url)
    if size and size > _MAX_DOWNLOAD_SIZE:
        return {"_error": f"That file is {_human_bytes(size)} and a download stops at "
                f"{_human_bytes(_MAX_DOWNLOAD_SIZE)}.",
                "code": "EXECUTION_FAILED",

                "suggestion": "Call find_datasets for the same theme and take a row that streams or is "
                              "served per area, or ask the service's own WFS or API for this area only."}

    try:



        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        answer = net.fetch(req, timeout=_download_timeout(), max_bytes=_MAX_DOWNLOAD_SIZE,
                           total_timeout=_DOWNLOAD_TOTAL_TIMEOUT)
    except net.FetchTooLarge:


        return {"_error": "File too large (max 100 MB)",
                "suggestion": "Take the service's own subset instead (its WFS or API, filtered to "
                              "the area), or the lighter row the catalog names beside this one."}
    except net.FetchDeadline as e:
        return {"_error": f"The download did not finish in time: {e}",
                "suggestion": "Ask the user for a smaller extract, or a direct link to a lighter format."}
    except net.FetchCancelled:
        return {"_error": "The run was stopped.", "code": "CANCELLED",
                "suggestion": "Stop here and wait for the next user message."}
    except (urllib.error.URLError, OSError) as e:
        return {"_error": f"Failed to download: {e}"}
    content = answer.body
    content_type = answer.headers.get("content-type", "")
    if shared and content[:512].lstrip()[:15].lower().startswith((b"<!doctype html", b"<html")):


        return {"_error": "The share link answered a web page, not the file: it is not shared with anyone "
                          "who has the link, or the link is incomplete.", "code": "INVALID_ARGS",
                "suggestion": "Ask the user to share the file with anyone who has the link and paste that link."}

    parsed = urllib.parse.urlparse(url)



    filename = _safe_filename(posixpath.basename(parsed.path))


    named = _disposition_filename(answer.headers.get("content-disposition") or "")
    if named and (not os.path.splitext(filename)[1]
                  or os.path.splitext(filename)[1].lower() in _ENDPOINT_EXTENSIONS):
        filename = _safe_filename(named)
    ext = _extension_of_download(filename, answer.headers, content)
    if ext and not filename.lower().endswith(ext):
        filename += ext
    if not ext and "json" in content_type:
        ext = ".geojson"
        filename += ext

    tmp_dir = create_managed_temp_dir("download")
    filepath = os.path.join(tmp_dir, filename)

    with open(filepath, "wb") as f:
        f.write(content)

    others: list = []
    if ext == ".zip":
        try:
            with zipfile.ZipFile(filepath, "r") as zf:
                members = zf.infolist()
                if len(members) > _MAX_ARCHIVE_ENTRIES:
                    return _discard_download(tmp_dir, {
                        "_error": f"The archive holds {len(members)} entries, over the "
                        f"{_MAX_ARCHIVE_ENTRIES} this tool unpacks in one call.",
                        "code": "INVALID_ARGS",
                        "suggestion": "Ask the provider for the layer you need rather than a bulk archive, "
                                      "or name one file inside it with a /vsizip/ path.",
                    })
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






                if extracted > _MAX_EXTRACTED_SIZE:
                    return _discard_download(tmp_dir, {
                        "_error": f"The archive unpacks to {_human_bytes(extracted)}, over the "
                        f"{_human_bytes(_MAX_EXTRACTED_SIZE)} limit for one download.",
                        "suggestion": "Ask the provider's service for the area of interest instead of "
                                      "the whole archive: a WFS with a bbox, or a regional extract.",
                    })



                cancel = net.current_cancel_check()
                for member in members:
                    if cancel is not None and cancel():
                        return _discard_download(tmp_dir, {
                            "_error": "The run was stopped while the archive was being unpacked.",
                            "code": "CANCELLED",
                        })
                    _extract_member(zf, member, tmp_dir)





            found = _archive_vector_files(tmp_dir)
            shp_files, gpkg_files, geojson_files = found[".shp"], found[".gpkg"], found[".geojson"]
            others = [os.path.relpath(f, tmp_dir)
                      for f in (shp_files + gpkg_files + geojson_files)][:10]



            shp_files = [f for f in shp_files if _shapefile_is_complete(f)]
            if shp_files:
                filepath = shp_files[0]
            elif gpkg_files:
                filepath = gpkg_files[0]
            elif geojson_files:
                filepath = geojson_files[0]
            else:
                inside = sorted({os.path.splitext(n)[1].lower() for n in _archive_entries(tmp_dir)} - {""})
                return _discard_download(tmp_dir, {
                    "_error": "The ZIP archive holds no .shp, .gpkg or .geojson file"
                              + (f"; it holds {', '.join(inside[:8])}." if inside else "."),
                    "code": "INVALID_ARGS",
                    "suggestion": "Ask the provider for a shapefile, GeoPackage or GeoJSON export, or name "
                                  "the file inside the archive with a /vsizip/ path.",
                })
        except zipfile.BadZipFile:
            return _discard_download(tmp_dir, {
                "_error": f"{url} did not download a valid ZIP archive.", "code": "INVALID_ARGS",
                "suggestion": "Check the URL: a download page or an error page often arrives instead of "
                              "the file. inspect_data_source says what the URL really serves."})
        except (NotImplementedError, RuntimeError, OSError, EOFError, zlib.error) as exc:



            return _discard_download(tmp_dir, {
                "_error": f"The ZIP archive could not be unpacked: {type(exc).__name__}: {exc}",
                "code": "INVALID_ARGS",
                "suggestion": "Ask the provider for a plain ZIP (Deflate, no password) or another format."})

    if filepath.lower().endswith(".gz") and ext != ".zip":

        unpacked = _gunzip_download(filepath)
        if isinstance(unpacked, dict):
            return _discard_download(tmp_dir, unpacked)
        filepath = unpacked
    if not layer_name:
        layer_name = os.path.splitext(os.path.basename(filepath))[0]

    final_layer_name = layer_name


    counted = _ogr_feature_count(filepath)
    if counted is not None:
        refused = volume_guard.too_many(counted, args, os.path.basename(filepath), on_disk=True)
        if refused:
            return _discard_download(tmp_dir, refused)
    final_filepath = _vector_uri_for(filepath)

    def _create():
        layer = QgsVectorLayer(final_filepath, final_layer_name, "ogr")
        if layer.isValid() and final_filepath.lower().endswith(".gpx") and layer.featureCount() == 0:

            for sublayer in ("tracks", "routes", "track_points"):
                candidate = QgsVectorLayer(f"{final_filepath}|layername={sublayer}", final_layer_name, "ogr")
                if candidate.isValid() and candidate.featureCount() > 0:
                    layer = candidate
                    break
        if not layer.isValid():
            return {"_error": f"QGIS could not open {os.path.basename(final_filepath)} as a vector layer: "
                    f"{layer.error().summary() or 'the format is unsupported or the file is empty'}.",
                    "code": "INVALID_ARGS",
                    "suggestion": "inspect_data_source says what the URL really serves; add_data with "
                                  "kind='raster' loads an image instead."}
        if counted is None:
            refused = volume_guard.too_many_features(layer, args, os.path.basename(final_filepath), on_disk=True)
            if refused:
                return refused
        QgsProject.instance().addMapLayer(layer)
        result = {
            "layer_name": layer.name(),
            "layer_id": layer.id(),
            "feature_count": layer.featureCount(),
            "geometry_type": (
                layer.geometryType().name
                if hasattr(layer.geometryType(), "name")
                else str(layer.geometryType())
            ),
            "crs": layer.crs().authid(),
            "fields": [f.name() for f in layer.fields()],
        }
        if layer.featureCount() == 0:








            result["warning"] = ("The layer loaded but holds no features: the download opened correctly, "
                                 "so the source itself is empty, already filtered down to nothing, or this "
                                 "read the wrong sublayer. inspect_data_source shows what else is in it.")
        return result

    out = _run_on_main_thread(_create, timeout=30)






    if isinstance(out, dict) and out.get("_error") is not None:
        return _discard_download(tmp_dir, out)


    if isinstance(out, dict) and len(others) > 1:
        out["file_opened"] = os.path.relpath(filepath, tmp_dir)
        out["files_available"] = others
    return out


_LOCAL_VECTOR = {".geojson", ".json", ".gpkg", ".kml", ".kmz", ".shp", ".zip", ".gml", ".fgb", ".sqlite", ".parquet"}
_LOCAL_RASTER = {".tif", ".tiff", ".geotiff", ".jp2", ".img", ".vrt", ".asc", ".nc", ".png", ".jpg"}


def _inspect_local_file(path: str) -> dict:
    """A file on disk: what it is and the one call that loads it."""
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
            if info.get("metric"):
                out["message"] += " Coordinates look projected: pass crs=<EPSG code> if you know it."
        else:
            out["message"] = "No coordinate column: loads as an attribute table. Join it or geocode an address field."
        return out
    if ext in _LOCAL_VECTOR:
        out.update({"import_method": "add_data", "import_arguments": {"source": path, "name": name},
                    "message": "Direct vector import is available."})
        if ext in (".gpkg", ".sqlite", ".gdb", ".kml", ".kmz", ".gml"):
            from .core_tools import _sublayer_names, describe_sublayers
            names = _sublayer_names(path)
            if len(names) > 1:
                out["layers"] = describe_sublayers(path, names)
                out["message"] = (f"Holds {len(names)} layers. Call add_data with layer=<name> for each one "
                                  "wanted; the list above already gives geometry, count and CRS.")
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
                    "message": ("A point cloud. It loads as a point cloud layer, not a vector one, and the "
                                "pdal: algorithms build a DEM or a canopy height model from it.")})
        return out
    if ext in {".txt", ".md", ".pdf", ".docx"}:
        out.update({"message": "A document, not a dataset. Read it with read_text if the user wants its content."})
        return out
    out["message"] = "Unknown extension. Try add_vector_layer, then add_raster_layer."
    return out


def _inspect_is_remote(args: dict) -> bool:
    """inspect_data_source runs off-thread only for a URL."""

    url = str(args.get("url") or "").strip()
    return url.lower().startswith(("http://", "https://"))


_DIRECT_IMPORT_EXTS = frozenset({".geojson", ".json", ".gpkg", ".kml", ".csv", ".zip", ".shp"})








_HOSTED_DEPARTMENT_PATHS = (
    (re.compile(r"(/ign/rgealti/5m/rgealti-5m-D)(\d{1,2}|2[ab])(\.tif)$", re.IGNORECASE), 3),
    (re.compile(r"(/reference/ign/lidarhd-mnt1m/)(\d)(\.tif)$", re.IGNORECASE), 2),
)


def hosted_department_url(url: str) -> str:
    """The same hosted tile with its department code at the width the store uses."""
    if "stterralabopendata.blob.core.windows.net" not in (url or ""):
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
    """The extension a format-in-the-path export URL stands for, else None."""





    parts = urllib.parse.urlparse(url)
    last = parts.path.rstrip("/").rsplit("/", 1)[-1].lower()
    if last in _FORMAT_SEGMENTS:
        return _FORMAT_SEGMENTS[last]
    for value in urllib.parse.parse_qs(parts.query).get("format", []):
        if value.lower() in _FORMAT_SEGMENTS:
            return _FORMAT_SEGMENTS[value.lower()]
    return None


def _inspect_by_head(url: str, ext: str) -> dict | None:
    """The direct-import answer for a file URL, from a HEAD instead of the body."""




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








_INSPECT_HTTP_ADVICE: dict[int, tuple[str, str, str]] = {
    400: ("INVALID_ARGS", "the server rejected the request itself",
          "A query string this service does not accept. Ask for its capabilities document, "
          "or drop the parameters and probe the base URL."),
    401: ("EXECUTION_FAILED", "the service wants credentials",
          "This source is not open. Say so and look for an open mirror with find_datasets "
          "rather than retrying."),
    403: ("EXECUTION_FAILED", "the service refused us",
          "Either the source needs an account or the host blocks unknown clients. Do not retry the "
          "same URL; find another route to the same data."),
    404: ("INVALID_ARGS", "there is nothing at this address",
          "The path is wrong, not the service. Probe the parent directory or the service root, "
          "and read the link out of what it lists."),
    405: ("INVALID_ARGS", "the service refuses this method",
          "Usually a service endpoint asked for as a file. Add the service's own query "
          "(WFS GetCapabilities, an API's collections path) instead of the bare URL."),
    409: ("INVALID_ARGS", "the request conflicts with what the service holds",
          "Two parameters disagree, most often a format or a version the collection does not "
          "publish. Ask the service what it offers before asking again."),
    410: ("INVALID_ARGS", "this address is gone for good",
          "The dataset moved or was withdrawn. Search for its current home; retrying cannot work."),
    429: ("EXECUTION_FAILED", "the host is rate limiting us",
          "Wait before asking this host again, and do not open more requests to it in the meantime."),
}
_INSPECT_SERVER_ERROR = ("EXECUTION_FAILED", "the service failed on its side",
                         "The address looks right and the server broke. This is the one case worth "
                         "one retry; if it fails again, use another source.")


def _inspect_failure(url: str, exc: Exception) -> dict:
    """The refusal, with the status named and the next move stated."""
    status = getattr(exc, "code", None)
    if isinstance(status, int):
        code, what, suggestion = _INSPECT_HTTP_ADVICE.get(
            status, _INSPECT_SERVER_ERROR if status >= 500 else
            ("EXECUTION_FAILED", "the service refused the request", "Read the status and change the address."))
        return {"_error": f"HTTP {status} from {url}: {what}.", "code": code,
                "status": status, "suggestion": suggestion}
    if isinstance(exc, (net.FetchDeadline, TimeoutError)):
        return {"_error": f"The probe of {url} ran past its deadline.", "code": "EXECUTION_FAILED",
                "suggestion": "The host is slow rather than wrong. Ask for a smaller extract, "
                              "or use a source that answers."}
    if isinstance(exc, net.FetchTooLarge):
        return {"_error": f"{url} serves more than this probe reads.", "code": "EXECUTION_FAILED",
                "import_method": "add_data", "import_arguments": {"source": url},
                "suggestion": "It is a bulk file, not a service description. Pass it straight to "
                              "add_data instead of inspecting it."}
    return {"_error": f"Failed to inspect source: {exc}", "code": "EXECUTION_FAILED",
            "suggestion": "The host did not answer at all. Check the domain, or use another source."}


def _inspect_data_source(args: dict) -> dict:
    raw = str(args["url"]).strip()


    if raw.startswith("<"):
        return _inspect_xml_payload("", raw)
    url = hosted_department_url(links.clean(raw))
    if url.startswith("file://"):



        parts = urllib.parse.urlsplit(url)
        if parts.netloc in ("", "localhost"):
            local = urllib.request.url2pathname(parts.path)
        elif os.name == "nt" and re.fullmatch(r"[\w.-]+", parts.netloc):

            local = urllib.request.url2pathname("//" + parts.netloc + parts.path)
        else:
            local = urllib.parse.unquote(url[7:])
    else:
        local = url
    local = os.path.expanduser(local)
    if os.path.isfile(local):
        return _inspect_local_file(os.path.abspath(local))
    if not url.lower().startswith(("http://", "https://")):
        return {"_error": f"Not a URL and no file at this path: {url}",
                "suggestion": "Check the path with the user, or give a direct download URL."}


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
    """A pasted link resolved: ``{url, kind, note, resolved_from?, files?, inline?, error?}``."""





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
        out["error"] = {"_error": f"Could not list the files behind {pasted}: {exc}", "code": "EXECUTION_FAILED",
                        "suggestion": ("The listing service refused or the address is private. Ask the user for "
                                       "the direct link of the one file wanted." if status != 403 else
                                       "GitHub allows 60 listings an hour without an account. Ask the user for "
                                       "the file's own link, or wait an hour.")}
        return out
    files = sorted(links.listing_files(resolved.listing, resolved.url, payload), key=links.rank)
    out["files"] = [dict(entry, add=_link_file_call(entry)) for entry in files]
    return out


def _link_data_files(link: dict) -> list:
    return [f for f in link.get("files") or [] if not f.get("folder") and (
        f.get("service") or (links.extension(f.get("name", "")) or links.extension(f["url"])) in links.DATA_EXTENSIONS)]


def _link_inspect_answer(link: dict) -> dict | None:
    """inspect_data_source's whole answer for a link that is not one fetchable address, else None."""
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
                "message": link["note"] + " add_vector_from_url on the same link builds the layer from it."}
    if kind != "listing":
        return None
    files = link.get("files") or []
    out = {"source_family": "file_listing", "resolved_from": link["resolved_from"], "listing_url": link["url"],
           "file_count": len(files), "layers": files,
           "message": (link["note"] + " Each entry in layers carries the call that opens it, the files that "
                       "draw on a map first." if files else
                       link["note"] + " The listing is empty: nothing public is attached there.")}
    data = _link_data_files(link)
    if len(data) == 1:
        out["import_method"] = data[0]["add"]["tool"]
        out["import_arguments"] = data[0]["add"]["args"]
    return out


def _inspect_remote_url(url: str) -> dict:
    path = urllib.parse.urlparse(url).path
    streamed = os.path.splitext(path.lower())[1]
    name = os.path.splitext(os.path.basename(path))[0] or "dataset"
    lowered = url.lower()
    if "{z}" in lowered and "{x}" in lowered and "{y}" in lowered:

        vector = streamed in (".pbf", ".mvt")
        return {"source_family": "tile_template", "final_url": url,
                "import_method": "add_data", "import_arguments": {"source": url, "kind": "vectortile" if vector
                                                                  else "xyz"},
                "message": ("A tile URL template: it loads as a " + ("vector tile" if vector else "tile")
                            + " layer as it is, {z}/{x}/{y} in any order.")}
    if streamed in _STREAMED_EXTS:
        kind = _STREAMED_EXTS[streamed]
        return {"source_family": "remote_file", "final_url": url, "format": streamed.lstrip("."),
                "import_method": "add_data", "import_arguments": {"source": url, "name": name, "kind": kind},
                "message": ("A file add_data reads in place over range requests: nothing to download first."
                            if kind != "raster" else "A NetCDF file: add_data reads it as a raster; a global "
                            "one is large, so name the variable and the area.")}
    if lowered.split("?", 1)[0].endswith(_DOWNLOAD_EXTS):
        result = _build_direct_import_result(url, os.path.basename(path) or "dataset", "vector_file",
                                             os.path.basename(path).split(".", 1)[-1])
        result["message"] = "A file add_vector_from_url downloads and opens: KMZ, GPX, XLSX and gzipped files included."
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
    """Whether the host answered, but not with a description: then it is asked for one."""
    if result.get("_error"):
        return result.get("status") in _PROBE_STATUSES
    return result.get("source_family") in _PROBE_FAMILIES


def _inspect_follow(url: str, result: dict) -> dict:
    """An OGC API landing page read through to its collections, once."""
    follow = result.pop("follow", None) if isinstance(result, dict) else None
    if not follow:
        return result
    followed, _ = _inspect_fetched(follow, _INSPECT_TIMEOUT)
    if followed.get("_error"):
        return {"source_family": "ogc_api_landing", "final_url": url, "collections_url": follow,
                "message": "An OGC API landing page. Its collections did not answer: inspect collections_url."}
    return followed


def _response_charset(content_type: str) -> str:
    """The encoding a Content-Type header declares, else UTF-8."""







    found = re.search(r"charset\s*=\s*[\"']?([A-Za-z0-9_.:-]+)", content_type or "", re.IGNORECASE)
    if not found:
        return "utf-8"
    import codecs

    try:
        return codecs.lookup(found.group(1)).name
    except LookupError:
        return "utf-8"


def _inspect_fetched(url: str, timeout: int) -> tuple[dict, bool]:
    """One GET of *url*, classified; and whether the answer was an HTML page."""
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
                   "message": ("A web page that links to data files: distributions lists up to 10, what draws on "
                               "a map first, each with the call that loads it.")}
            if len(found) == 1 or links.rank(found[0]) < links.rank(found[1]):
                out["import_method"] = found[0]["add"]["tool"]
                out["import_arguments"] = found[0]["add"]["args"]
            return out, True
    return {
        "source_family": "unknown",
        "final_url": final_url,
        "content_type": content_type or "unknown",
        "message": "Could not classify this source. Try a direct download URL or a service capabilities endpoint.",
    }, "html" in content_type
