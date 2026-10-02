# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




















































from __future__ import annotations

import hashlib
import math
import os
import posixpath
import struct
import urllib.error
import urllib.request
import uuid
import zipfile
from urllib.parse import unquote, urlsplit

from qgis.core import QgsRasterLayer
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP, QLineF, QRectF, Qt
from qgis.PyQt.QtGui import QColor, QFont, QImage, QPainter, QPen

from ..core import limits, net, output_paths, security, tuning
from ..core.background import run_on_main_thread
from ..core.host_platform import remove_quietly, retry_file_op
from ..core.logger import log_warning
from ..core.tool_registry import Tool, ToolRegistry, coded_fact, tool_error
from ._images import image_to_base64
from .data_common import _DOWNLOAD_TOTAL_TIMEOUT, _MAX_DOWNLOAD_SIZE, _download_timeout, _safe_filename
from .layer_lookup import _find_layer, _layer_not_found_error

PREVIEW_LONG_SIDE = 1200


MATCH_LONG_SIDE = 4800
MATCH_JPEG_QUALITY = 85


MATCH_MAX_CHARS = 10 * 1024 * 1024
MATCH_FALLBACK_SIDE = 2400
PREVIEW_JPEG_QUALITY = 80
PDF_DPI = 200
GRID_STEPS = 10

KML_MAX_BYTES = 20 * 1024 * 1024

PHOTO_HEAD_BYTES = 4 * 1024 * 1024

_R = 6378137.0
_WORLD_FILE_EXTENSIONS = frozenset({".wld", ".tfw", ".tifw", ".tiffw", ".jgw", ".jpgw", ".jpegw", ".pgw", ".pngw",
                                    ".gfw", ".gifw", ".bpw", ".bmpw", ".j2w", ".jp2w", ".sdw"})

_FRAME_35MM_DIAGONAL = math.hypot(36.0, 24.0)

_UNIT_MM = {2: 25.4, 3: 10.0, 4: 1.0, 5: 0.001}

_NADIR_PITCH = -70.0

_SIGNATURES = ((b"\xff\xd8\xff", ".jpg"), (b"\x89PNG\r\n\x1a\n", ".png"), (b"II*\x00", ".tif"),
               (b"MM\x00*", ".tif"), (b"II+\x00", ".tif"), (b"MM\x00+", ".tif"), (b"%PDF-", ".pdf"),
               (b"GIF87a", ".gif"), (b"GIF89a", ".gif"), (b"\x00\x00\x00\x0cjP  \r\n", ".jp2"),
               (b"\xffO\xffQ", ".j2k"), (b"BM", ".bmp"), (b"PK\x03\x04", ".kmz"))
_DOWNLOADED_EXTENSIONS = frozenset(ext for _, ext in _SIGNATURES) | {".webp", ".kml"}

_DOWNLOAD_SHARE = 0.6


def register_georeference_inspect_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="inspect_georeference",
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Inspect {raster} for georeferencing"),
        input_schema={
            "type": "object",
            "properties": {
                "raster": {"type": "string"},
                "page": {"type": "integer", "minimum": 1},
            },
            "required": ["raster"],


            "x-url-source": True,
        },
        handler=_inspect_georeference,
        background=True,
    ))




def _remote_url(text: str) -> str:

    kind, rest = security.unwrap_vsi(text)
    if kind == "remote" and text.lower().startswith(("/vsicurl/", "/vsicurl_streaming/", "/vsicurl?",
                                                     "/vsicurl_streaming?")):
        text = rest
    elif kind != "local":
        return ""
    return text if text.lower().startswith(("https://", "http://")) else ""


def _resolve(ref) -> dict:





    text = str(ref or "").strip()
    if not text:
        return tool_error("raster is empty.", "INVALID_ARGS",
                          "the image, PDF or KMZ by path, or a loaded layer by name.")
    url = _remote_url(text)
    if url:
        return {"url": url, "layer": "", "page": 0}
    expanded = security.expand_path(text)
    if os.path.isfile(expanded):
        error = security.validate_path(expanded)
        if error:
            return tool_error(error, "PERMISSION_DENIED", "Allowed: the project folder or your home folder.")
        return {"path": expanded, "layer": "", "page": 0}
    layer = _find_layer(text)
    if layer is None:
        if "/" in text or "\\" in text or os.path.splitext(text)[1]:
            return tool_error(f"No file at {expanded}.", "INVALID_ARGS",
                              "The path or the loaded layer name may be wrong.")
        return _layer_not_found_error(text)
    source = str(layer.source() or "").split("|", 1)[0]
    page = 0

    parts = source.split(":", 2)
    if len(parts) == 3 and parts[0].upper() == "PDF" and parts[1].isdigit() and parts[2]:
        page, source = int(parts[1]), parts[2]
    url = _remote_url(source)
    extension = os.path.splitext(urlsplit(url).path if url else source)[1].lower()
    if not url and not os.path.isfile(source):
        return tool_error(f"{layer.name()} is not read from a file on this computer.", "INVALID_ARGS",
                          "the scan, photo, PDF or KMZ file itself.")
    if not isinstance(layer, QgsRasterLayer) and extension not in (".kml", ".kmz"):
        return tool_error(f"{layer.name()} is a vector layer.", "INVALID_ARGS",
                          "the image to georeference: a raster layer by name, or its file path.")
    if url:
        return {"url": url, "layer": layer.name(), "page": page}
    return {"path": source, "layer": layer.name(), "page": page}




def _to_mercator(lon: float, lat: float) -> tuple:
    lat = max(-85.0, min(85.0, lat))
    return math.radians(lon) * _R, math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * _R


def _to_lonlat(x: float, y: float) -> tuple:
    return math.degrees(x / _R), math.degrees(2 * math.atan(math.exp(y / _R)) - math.pi / 2)


def _corner_points(cx: float, cy: float, half_w: float, half_h: float, clockwise_deg: float,
                   width: int, height: int) -> list:






    theta = math.radians(clockwise_deg)
    top = (math.sin(theta), math.cos(theta))
    right = (math.cos(theta), -math.sin(theta))
    points = []
    for pixel, line, sx, sy in ((0, 0, -1, 1), (width, 0, 1, 1), (width, height, 1, -1), (0, height, -1, -1)):
        x = cx + sx * half_w * right[0] + sy * half_h * top[0]
        y = cy + sx * half_w * right[1] + sy * half_h * top[1]
        lon, lat = _to_lonlat(x, y)
        points.append({"pixel": pixel, "line": line, "x": round(lon, 9), "y": round(lat, 9)})
    return points


def _bbox(points: list) -> list:
    xs = [p["x"] for p in points]
    ys = [p["y"] for p in points]
    return [round(min(xs), 7), round(min(ys), 7), round(max(xs), 7), round(max(ys), 7)]




def _stopped() -> dict:
    return tool_error("Stopped before the file was read.", "CANCELLED", "Nothing was written.")


def _writable_target(beside: str, file_name: str) -> str:

    for folder in (os.path.dirname(beside), output_paths.exports_folder()):
        target = os.path.join(folder, file_name)
        writable = os.access(folder, os.W_OK) if os.path.isdir(folder) else folder != os.path.dirname(beside)
        if writable and security.fits_path(target, 48) and not security.validate_path(target, write=True):
            return target
    return ""


def _read_vsimem(gdal, name: str) -> bytes:
    handle = gdal.VSIFOpenL(name, "rb")
    if handle is None:
        return b""
    try:
        gdal.VSIFSeekL(handle, 0, 2)
        length = gdal.VSIFTellL(handle)
        gdal.VSIFSeekL(handle, 0, 0)
        return bytes(gdal.VSIFReadL(1, length, handle) or b"")
    finally:
        gdal.VSIFCloseL(handle)


def _unlink(gdal, name: str) -> None:
    try:
        gdal.Unlink(name)
    except RuntimeError:
        pass


def _progress(cancelled):
    def progress(_complete, _message, _data):
        return 0 if (cancelled is not None and cancelled()) else 1
    return progress




def _signature_extension(body: bytes) -> str:

    for magic, extension in _SIGNATURES:
        if body.startswith(magic):
            return extension
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return ".webp"
    head = body[:4096].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head.startswith(b"<") and b"<kml" in head:
        return ".kml"
    return ""


def _kept_copy(folder: str, key: str) -> str:

    try:
        names = os.listdir(folder)
    except OSError:
        return ""
    for name in names:
        stem, extension = os.path.splitext(name)
        if extension.lower() in _DOWNLOADED_EXTENSIONS and (stem == key or stem.endswith("-" + key)):
            path = os.path.join(folder, name)
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                return path
    return ""


def _download(url: str, cancelled) -> dict:

    folder = output_paths.exports_folder()
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    kept = _kept_copy(folder, key)
    if kept:
        return {"path": kept, "downloaded": False}
    host = urlsplit(url).hostname or url[:80]
    total = min(_DOWNLOAD_TOTAL_TIMEOUT, limits.current("CALL_MAX_SECONDS_BACKGROUND") * _DOWNLOAD_SHARE)
    try:
        net.check_url(url)
        answer = net.fetch(urllib.request.Request(url), timeout=_download_timeout(),
                           max_bytes=_MAX_DOWNLOAD_SIZE, total_timeout=total, cancel=cancelled)
    except urllib.error.HTTPError as exc:
        missing = exc.code in (404, 410)
        return tool_error(f"HTTP {exc.code} from {host}: "
                          + ("there is no file at this address." if missing else f"{exc.reason}."),
                          "INVALID_ARGS" if 400 <= exc.code < 500 else "EXECUTION_FAILED",
                          "The address may be wrong; the file itself is another route.")
    except (net.LocalUrlRefused, net.FetchWithdrawn) as exc:
        return tool_error(f"This address is not fetched: {exc.reason}", "PERMISSION_DENIED",
                          "The file itself, with a local path, is needed.")
    except net.FetchTooLarge:
        return tool_error(f"The file at {host} is over the {_MAX_DOWNLOAD_SIZE // (1024 * 1024)} MB this tool "
                          "downloads.", "INVALID_ARGS",
                          "A downloaded file, with its local path, is needed.")
    except net.FetchCancelled:
        return _stopped()
    except net.FetchDeadline as exc:
        return tool_error(f"The download from {host} did not finish in time: {exc.reason}", "TIMEOUT",
                          "A downloaded file, with its local path, is needed.")
    except (urllib.error.URLError, OSError) as exc:
        described = net.describe_failure(exc)
        if described:
            return tool_error(described, net.NETWORK_ERROR, net.NETWORK_SUGGESTION)
        return tool_error(f"The download from {host} failed: {str(exc)[:160]}", "EXECUTION_FAILED",
                          "The address may be wrong; the file itself is another route.")
    body = answer.body
    extension = _signature_extension(body)
    if not extension:
        return tool_error(f"{host} did not answer with an image, a PDF, a KML or a KMZ.", "INVALID_ARGS",
                          "the address of the file itself, not of a page that shows it.")
    stem = os.path.splitext(_safe_filename(posixpath.basename(unquote(urlsplit(url).path)), "image"))[0]
    target = os.path.join(folder, f"{stem[:60]}-{key}{extension}")
    if not security.fits_path(target, 48):
        target = os.path.join(folder, f"{key}{extension}")
    refused = security.validate_path(target, write=True)
    if refused:
        return tool_error(refused, "PERMISSION_DENIED", "The file itself, with a local path, is needed.")
    part = f"{target}.{uuid.uuid4().hex[:8]}.part"
    try:
        os.makedirs(folder, exist_ok=True)
        with open(part, "wb") as handle:
            handle.write(body)
        retry_file_op(os.replace, part, target)
    except OSError as exc:
        remove_quietly(part)
        return tool_error(f"The download could not be written to {folder}: {exc}", "EXECUTION_FAILED",
                          "Disk space, or the file itself directly, may help.")
    return {"path": target, "downloaded": True}




def _crs_label(srs) -> str:
    if srs is None:
        return ""
    try:
        srs.AutoIdentifyEPSG()
    except RuntimeError:
        pass
    authority, code = srs.GetAuthorityName(None), srs.GetAuthorityCode(None)
    return f"{authority}:{code}" if authority and code else str(srs.GetName() or "a CRS with no code")


def _prj_beside(osr, path: str):

    prj = os.path.splitext(path)[0] + ".prj"
    if not os.path.isfile(prj):
        return None
    try:
        with open(prj, encoding="utf-8", errors="replace") as handle:
            text = handle.read(65536).strip()
        srs = osr.SpatialReference()
        srs.SetFromUserInput(text)
        return srs
    except (OSError, RuntimeError):
        return None


def _georeferencing(gdal, osr, dataset, path: str) -> dict | None:

    transform = dataset.GetGeoTransform(can_return_null=True)
    has_transform = transform is not None and tuple(transform) != (0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    gcps = dataset.GetGCPCount() if not has_transform else 0
    if not has_transform and not gcps:
        return None
    driver = dataset.GetDriver().ShortName if dataset.GetDriver() else ""
    files = [f for f in (dataset.GetFileList() or []) if isinstance(f, str)]
    world = next((f for f in files if os.path.splitext(f)[1].lower() in _WORLD_FILE_EXTENSIONS), "")
    aux = next((f for f in files if f.lower().endswith(".aux.xml")), "")
    aux_places = False
    if aux and not world:
        try:
            with open(aux, encoding="utf-8", errors="replace") as handle:
                text = handle.read(1_000_000)
            aux_places = "<GeoTransform" in text or "<GCPList" in text
        except OSError:
            aux_places = False
    if world:
        why = f"world file {os.path.basename(world)}"
    elif aux_places:
        why = f"{os.path.basename(aux)}"
    elif driver == "PDF":
        why = "GeoPDF"
    elif driver == "GTiff":
        why = "control points in the GeoTIFF" if gcps else "GeoTIFF tags"
    else:
        why = f"{driver} header" + (" control points" if gcps else "")
    srs = dataset.GetSpatialRef() if has_transform else dataset.GetGCPSpatialRef()
    if srs is None:
        srs = _prj_beside(osr, path)
    width, height = dataset.RasterXSize, dataset.RasterYSize
    corners = [(0, 0), (width, 0), (width, height), (0, height), (width / 2, height / 2)]
    placed = None
    try:
        transformer = gdal.Transformer(dataset, None, [] if has_transform else ["METHOD=GCP_POLYNOMIAL"])
        placed, ok = transformer.TransformPoints(0, [(float(x), float(y), 0.0) for x, y in corners])
        if not all(ok):
            placed = None
    except (RuntimeError, TypeError, ValueError):
        placed = None
    if placed is None and gcps:
        gcp_list = dataset.GetGCPs()
        xs, ys = [g.GCPX for g in gcp_list], [g.GCPY for g in gcp_list]
        placed = [(min(xs), max(ys)), (max(xs), max(ys)), (max(xs), min(ys)), (min(xs), min(ys)),
                  ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)]
    facts = {"georeferenced": True, "georeferenced_by": why, "crs": _crs_label(srs) or None}
    if placed:
        xs, ys = [p[0] for p in placed[:4]], [p[1] for p in placed[:4]]
        facts["extent"] = [min(xs), min(ys), max(xs), max(ys)]
        if srs is not None:
            try:
                wgs84 = osr.SpatialReference()
                wgs84.ImportFromEPSG(4326)
                wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
                srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
                change = osr.CoordinateTransformation(srs, wgs84)
                lonlat = [change.TransformPoint(float(p[0]), float(p[1])) for p in placed]
                lons, lats = [p[0] for p in lonlat[:4]], [p[1] for p in lonlat[:4]]
                facts["extent_4326"] = [round(min(lons), 7), round(min(lats), 7),
                                        round(max(lons), 7), round(max(lats), 7)]
                facts["center_4326"] = [round(lonlat[4][0], 7), round(lonlat[4][1], 7)]
            except (RuntimeError, TypeError, ValueError) as exc:
                log_warning(f"inspect_georeference: extent in EPSG:4326 not computed: {exc}")
    return facts




def _local(tag) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _child(element, name: str):
    return next((c for c in list(element) if _local(c.tag) == name), None)


def _number(element, name: str):
    child = _child(element, name) if element is not None else None
    try:
        value = float(str(child.text).strip()) if child is not None and child.text else None
    except ValueError:
        return None
    return value if value is not None and math.isfinite(value) else None


def _kml_document(path: str):

    archive = None
    if path.lower().endswith(".kmz"):
        try:
            archive = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile) as exc:
            return tool_error(f"{os.path.basename(path)} is not a readable KMZ: {exc}", "INVALID_ARGS",
                              "a KMZ saved by Google Earth or QGIS, or the image inside it.")
        entries = [i for i in archive.infolist() if i.filename.lower().endswith(".kml")]
        if not entries:
            archive.close()
            return tool_error(f"{os.path.basename(path)} holds no KML document.", "INVALID_ARGS",
                              "the image inside it instead.")
        entry = next((i for i in entries if i.filename.lower() == "doc.kml"),
                     min(entries, key=lambda i: i.filename.count("/")))
        if entry.file_size > KML_MAX_BYTES:
            archive.close()
            return tool_error(f"The KML in {os.path.basename(path)} is {entry.file_size:,} bytes.", "INVALID_ARGS",
                              "That is vector data, not an image overlay; add_data adds it.")
        return archive.read(entry), archive
    if os.path.getsize(path) > KML_MAX_BYTES:
        return tool_error(f"{os.path.basename(path)} is {os.path.getsize(path):,} bytes.", "INVALID_ARGS",
                          "That is vector data, not an image overlay; add_data adds it.")
    with open(path, "rb") as handle:
        return handle.read(), None


def _ground_overlays(body: bytes) -> list:
    import xml.etree.ElementTree as ET  # nosec B405

    head = body[:4096].upper()
    if b"<!ENTITY" in body.upper() or b"<!DOCTYPE" in head:
        return []
    try:
        root = ET.fromstring(body)  # nosec B314
    except ET.ParseError:
        return []
    overlays = []
    for element in root.iter():
        if _local(element.tag) != "GroundOverlay":
            continue
        icon = _child(element, "Icon")
        href = _child(icon, "href") if icon is not None else None
        name = _child(element, "name")
        box = _child(element, "LatLonBox")
        quad = _child(element, "LatLonQuad")
        overlay = {"name": (name.text or "").strip() if name is not None else "",
                   "href": (href.text or "").strip() if href is not None else ""}
        if quad is not None:
            coords = _child(quad, "coordinates")
            values = []
            for token in str(coords.text if coords is not None else "").split():
                parts = token.split(",")
                try:
                    values.append((float(parts[0]), float(parts[1])))
                except (ValueError, IndexError):
                    values = []
                    break
            if len(values) == 4:
                overlay["quad"] = values
        if box is not None and "quad" not in overlay:
            sides = {k: _number(box, k) for k in ("north", "south", "east", "west")}
            if all(v is not None for v in sides.values()):
                overlay["box"] = sides
                overlay["rotation"] = _number(box, "rotation") or 0.0
        if overlay["href"] and ("quad" in overlay or "box" in overlay):
            overlays.append(overlay)
    return overlays


def _overlay_image(path: str, archive, href: str, cancelled) -> str | dict:


    if len(urlsplit(href).scheme) > 1 and "://" in href:
        return tool_error(f"The overlay's image is on the web ({href[:120]}), not in the file.", "INVALID_ARGS",
                          "The image needs to sit next to the KML, or be given directly.")
    member = unquote(href).replace("\\", "/").lstrip("./")
    if archive is None:
        image = os.path.normpath(os.path.join(os.path.dirname(path), unquote(href)))
        if not os.path.isfile(image):
            return tool_error(f"The overlay's image {href} is not next to the KML.", "INVALID_ARGS",
                              "The image belongs where the KML says, or is given directly.")
        return image
    names = {n.lower(): n for n in archive.namelist()}
    stored = names.get(member.lower())
    if stored is None:
        return tool_error(f"The KMZ names the image {href}, which it does not contain.", "INVALID_ARGS",
                          "the image itself, with points read on it.")
    stem = os.path.splitext(os.path.basename(path))[0]
    base, extension = os.path.splitext(os.path.basename(stored))
    file_name = output_paths.safe_file_name(f"{stem}_{base}", "overlay") + extension.lower()
    target = _writable_target(path, file_name)
    if not target:
        return tool_error("No folder next to the KMZ or in the exports folder can take its image.",
                          "PERMISSION_DENIED", "An unzipped image, with its points, works.")
    size = archive.getinfo(stored).file_size
    if os.path.isfile(target) and os.path.getsize(target) == size:
        return target
    part = os.path.join(os.path.dirname(target), f".{uuid.uuid4().hex[:12]}.part{extension.lower()}")
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with archive.open(stored) as source, open(part, "wb") as sink:
            while True:
                if cancelled is not None and cancelled():
                    raise InterruptedError
                block = source.read(1024 * 1024)
                if not block:
                    break
                sink.write(block)
        retry_file_op(os.replace, part, target)
        part = ""
    except InterruptedError:
        return _stopped()
    except OSError as exc:
        return tool_error(f"The KMZ's image could not be written to {target}: {exc}", "EXECUTION_FAILED",
                          "An unzipped image, with its points, works.")
    finally:
        if part:
            remove_quietly(part)
    return target


def _from_kml(gdal, found: dict, cancelled) -> dict:
    path = found["path"]
    document = _kml_document(path)
    if isinstance(document, dict):
        return document
    body, archive = document
    try:
        overlays = _ground_overlays(body)
        if not overlays:
            return tool_error(f"{os.path.basename(path)} has no GroundOverlay with a LatLonBox or gx:LatLonQuad.",
                              "INVALID_ARGS",
                              "It holds vector features; add_data adds it. Georeferencing needs the image "
                              "itself.")
        overlay = overlays[0]
        image = _overlay_image(path, archive, overlay["href"], cancelled)
    finally:
        if archive is not None:
            archive.close()
    if isinstance(image, dict):
        return image
    try:
        dataset = gdal.Open(image, gdal.GA_ReadOnly)
        width, height = dataset.RasterXSize, dataset.RasterYSize
        dataset = None
    except RuntimeError as exc:
        return tool_error(f"The overlay's image {os.path.basename(image)} does not open: {str(exc)[:160]}",
                          "EXECUTION_FAILED", "another copy of the image with points read on it.")
    if "quad" in overlay:

        order = ((0, height), (width, height), (width, 0), (0, 0))
        points = [{"pixel": px, "line": ln, "x": round(lon, 9), "y": round(lat, 9)}
                  for (px, ln), (lon, lat) in zip(order, overlay["quad"])]
        points = [points[3], points[2], points[1], points[0]]
        placed_by, transform = "KML GroundOverlay gx:LatLonQuad", "thin_plate_spline"
    else:
        box = overlay["box"]
        east = box["east"] + 360.0 if box["east"] < box["west"] else box["east"]
        west_x, south_y = _to_mercator(box["west"], box["south"])
        east_x, north_y = _to_mercator(east, box["north"])

        points = _corner_points((west_x + east_x) / 2, (south_y + north_y) / 2, (east_x - west_x) / 2,
                                (north_y - south_y) / 2, -overlay["rotation"], width, height)
        placed_by = "KML GroundOverlay LatLonBox" + (f", rotation {overlay['rotation']:g}"
                                                     if overlay["rotation"] else "")
        transform = "polynomial_1"
    result = {
        "source": path,
        "raster": image,
        "width": width,
        "height": height,
        "georeferenced": False,
        "placement": "exact",
        "placed_by": placed_by,
        "points": points,
        "crs": "EPSG:4326",
        "transform": transform,
        "extent_4326": _bbox(points),
        "suggest": (f"The KML gives the image's four corners exactly: call georeference_raster with raster "
                    f"{image}, these points, crs EPSG:4326 and transform {transform}; no matching is needed."),
    }
    if overlay["name"]:
        result["overlay_name"] = overlay["name"]
    if len(overlays) > 1:
        result["overlays"] = len(overlays)
        result["overlay_note"] = f"The file has {len(overlays)} image overlays; these points are the first one's."
    if found.get("layer"):
        result["layer"] = found["layer"]
    return result




_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
_TYPE_FORMATS = {1: "B", 3: "H", 4: "I", 6: "b", 8: "h", 9: "i", 11: "f", 12: "d"}


def _ifd(data: bytes, offset: int, endian: str) -> dict:

    tags: dict = {}
    if offset <= 0 or offset + 2 > len(data):
        return tags
    (count,) = struct.unpack_from(endian + "H", data, offset)
    for index in range(min(count, 512)):
        entry = offset + 2 + 12 * index
        if entry + 12 > len(data):
            break
        tag, kind, number = struct.unpack_from(endian + "HHI", data, entry)
        size = _TYPE_SIZES.get(kind)
        if not size or number > 65536:
            continue
        total = size * number
        where = entry + 8 if total <= 4 else struct.unpack_from(endian + "I", data, entry + 8)[0]
        if where + total > len(data):
            continue
        raw = data[where:where + total]
        if kind == 2:
            tags[tag] = raw.split(b"\0", 1)[0].decode("latin-1").strip()
        elif kind == 7:
            tags[tag] = raw
        elif kind in (5, 10):
            pairs = struct.unpack_from(f"{endian}{2 * number}{'I' if kind == 5 else 'i'}", raw)
            tags[tag] = [a / b if b else None for a, b in zip(pairs[0::2], pairs[1::2])]
        else:
            tags[tag] = list(struct.unpack_from(f"{endian}{number}{_TYPE_FORMATS[kind]}", raw))
    return tags


def _tiff_tags(block: bytes) -> dict:

    if len(block) < 8 or block[:2] not in (b"II", b"MM"):
        return {}
    endian = "<" if block[:2] == b"II" else ">"
    try:
        tags = _ifd(block, struct.unpack_from(endian + "I", block, 4)[0], endian)
        for pointer in (0x8769, 0x8825):
            value = tags.get(pointer)
            if isinstance(value, list) and value:
                tags[pointer] = _ifd(block, int(value[0]), endian)
    except struct.error:
        return {}
    return tags


def _photo_metadata(path: str) -> tuple:

    try:
        with open(path, "rb") as handle:
            head = handle.read(PHOTO_HEAD_BYTES)
    except OSError:
        return {}, ""
    exif, xmp = {}, ""
    if head[:2] == b"\xff\xd8":
        index = 2
        while index + 4 <= len(head) and head[index] == 0xFF:
            marker = head[index + 1]
            if marker == 0xFF:
                index += 1
                continue
            if marker in (0xD9, 0xDA):
                break
            (length,) = struct.unpack_from(">H", head, index + 2)
            segment = head[index + 4:index + 2 + length]
            if marker == 0xE1 and segment.startswith(b"Exif\0\0") and not exif:
                exif = _tiff_tags(segment[6:])
            elif marker == 0xE1 and segment.startswith(b"http://ns.adobe.com/xap/1.0/\0") and not xmp:
                xmp = segment[29:].decode("utf-8", "replace")
            index += 2 + length
    elif head[:2] in (b"II", b"MM"):
        exif = _tiff_tags(head)
        packet = exif.get(700)
        if isinstance(packet, (bytes, list)):
            xmp = (bytes(packet) if isinstance(packet, list) else packet).decode("utf-8", "replace")
    if not xmp:
        start = head.find(b"<x:xmpmeta")
        end = head.find(b"</x:xmpmeta>", start)
        if start >= 0 and end > start:
            xmp = head[start:end + 12].decode("utf-8", "replace")
    return exif, xmp


def _xmp_number(xmp: str, *names: str):
    for name in names:
        text = _xmp_text(xmp, name)
        if text is None:
            continue
        try:
            value = float(text.strip())
        except ValueError:
            continue
        if math.isfinite(value):
            return value
    return None


def _xmp_text(xmp: str, name: str):


    at = 0
    key = ":" + name
    while True:
        at = xmp.find(key, at)
        if at < 0:
            return None
        end = at + len(key)
        prefix_ok = at > 0 and (xmp[at - 1].isalnum() or xmp[at - 1] in "-_")
        rest = xmp[end:end + 64].lstrip()
        if prefix_ok and rest.startswith("="):
            value = rest[1:].lstrip()
            if value.startswith('"'):
                close = value.find('"', 1)
                if close > 0:
                    return value[1:close]
        elif prefix_ok and xmp[end:end + 1] == ">" and xmp.rfind("<", 0, at) > xmp.rfind(">", 0, at):
            close = xmp.find("<", end + 1)
            if close > 0:
                return xmp[end + 1:close]
        at = end


def _first(value):
    if isinstance(value, (list, tuple)) and value:
        return value[0]
    if isinstance(value, (bytes, bytearray)) and value:
        return value[0]
    return value if isinstance(value, (int, float)) else None


def _degrees(value, reference) -> float | None:
    if not isinstance(value, list) or len(value) != 3 or any(v is None for v in value):
        return None
    degrees = value[0] + value[1] / 60 + value[2] / 3600
    return -degrees if str(reference or "").upper()[:1] in ("S", "W") else degrees


def _photo_facts(path: str, width: int, height: int) -> dict | None:

    if os.path.splitext(path)[1].lower() not in (".jpg", ".jpeg", ".tif", ".tiff"):
        return None
    tags, xmp = _photo_metadata(path)
    exif = tags.get(0x8769) if isinstance(tags.get(0x8769), dict) else {}
    gps = tags.get(0x8825) if isinstance(tags.get(0x8825), dict) else {}
    lat = _degrees(gps.get(2), gps.get(1))
    lon = _degrees(gps.get(4), gps.get(3))
    if lat is None or lon is None:
        lat = _xmp_number(xmp, "GpsLatitude", "Latitude")
        lon = _xmp_number(xmp, "GpsLongitude", "GpsLongtitude", "Longitude")
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    photo: dict = {"lat": round(lat, 8), "lon": round(lon, 8)}
    for tag, key in ((0x010F, "make"), (0x0110, "model")):
        if isinstance(tags.get(tag), str) and tags[tag]:
            photo[key] = tags[tag][:60]
    relative = _xmp_number(xmp, "RelativeAltitude")
    sea_level = _first(gps.get(6))
    if sea_level is not None and _first(gps.get(5)) == 1:
        sea_level = -sea_level
    if sea_level is None:
        sea_level = _xmp_number(xmp, "AbsoluteAltitude")
    focal = _first(exif.get(0x920A))
    focal_35 = _first(exif.get(0xA405))
    resolution = _first(exif.get(0xA20E))
    unit_mm = _UNIT_MM.get(_first(exif.get(0xA210)) or 2)
    across = _first(exif.get(0xA002)) or width
    sensor = across / resolution * unit_mm if resolution and unit_mm and across else None
    if sensor is not None and not 1.0 <= sensor <= 70.0:
        sensor = None
    yaw = _xmp_number(xmp, "GimbalYawDegree", "CameraYawDegree")
    flight_yaw = _xmp_number(xmp, "FlightYawDegree")
    pitch = _xmp_number(xmp, "GimbalPitchDegree", "CameraPitchDegree")
    for key, value in (("height_above_takeoff_m", relative), ("altitude_above_sea_m", sea_level),
                       ("focal_mm", focal), ("focal_35mm_equivalent", focal_35), ("sensor_width_mm", sensor),
                       ("gimbal_yaw_deg", yaw), ("flight_yaw_deg", flight_yaw), ("gimbal_pitch_deg", pitch)):
        if isinstance(value, (int, float)) and math.isfinite(value) and value:
            photo[key] = round(float(value), 3)
    facts: dict = {"photo": photo, "center": {"lon": photo["lon"], "lat": photo["lat"]}}
    ground_width = None
    if relative and relative > 0 and focal and sensor:
        ground_width, how = relative * sensor / focal, "height above take-off x sensor width / focal length"
    elif relative and relative > 0 and focal_35:
        ground_width = relative * _FRAME_35MM_DIAGONAL / focal_35 * width / math.hypot(width, height)
        how = "height above take-off and the 35 mm equivalent focal length"
    if ground_width:
        facts["width_m"] = round(ground_width, 1)
        facts["width_from"] = how
    heading = yaw if yaw is not None else flight_yaw
    if heading is not None:
        facts["rotation_deg"] = round(((heading + 180.0) % 360.0) - 180.0, 2)
    notes = []
    if not ground_width:
        notes.append("The photo gives no height above the ground, so its ground width is unknown: estimate it.")
    if heading is None:
        notes.append("No heading in the photo: leave rotation_deg out.")
    if pitch is not None and pitch > _NADIR_PITCH:
        notes.append(f"The camera looked {abs(pitch):g} degrees down, not straight down: the footprint is rough.")
    if notes:
        facts["photo_note"] = " ".join(notes)
    if ground_width and heading is not None:
        cx, cy = _to_mercator(photo["lon"], photo["lat"])

        half_w = ground_width / 2 / math.cos(math.radians(photo["lat"]))
        facts["points"] = _corner_points(cx, cy, half_w, half_w * height / width, facts["rotation_deg"],
                                         width, height)
        facts["crs"] = "EPSG:4326"
    return facts




def _pdf_page_count(gdal, path: str) -> int:
    dataset = gdal.Open(path, gdal.GA_ReadOnly)
    pages = len(dataset.GetSubDatasets() or [])
    dataset = None
    return pages or 1


def _open_pdf_page(gdal, path: str, page: int, pages: int, dpi: float):
    name = f"PDF:{page}:{path}" if pages > 1 else path
    return gdal.OpenEx(name, gdal.OF_RASTER | gdal.OF_READONLY, open_options=[f"DPI={dpi:g}"])


def _pdf_with_qt(path: str, page: int, dpi: float, target: str) -> tuple:

    document_class = None
    for module in ("qgis.PyQt.QtPdf", "PyQt6.QtPdf", "PyQt5.QtPdf"):
        try:
            document_class = __import__(module, fromlist=["QPdfDocument"]).QPdfDocument
            break
        except (ImportError, AttributeError):
            continue
    if document_class is None:
        return 0, "this QGIS reads no PDF (neither GDAL's PDF driver nor Qt's PDF module)"
    from qgis.PyQt.QtCore import QSize

    document = document_class()
    document.load(path)
    pages = int(document.pageCount() or 0)
    if pages <= 0:
        return 0, "the PDF has no page this QGIS can read"
    if page > pages:
        return pages, f"the PDF has {pages} page(s)"
    size = document.pagePointSize(page - 1)
    width, height = max(1, round(size.width() * dpi / 72)), max(1, round(size.height() * dpi / 72))
    image = document.render(page - 1, QSize(width, height))
    if image is None or image.isNull() or not image.save(target, "PNG"):
        return pages, "Qt could not draw that page"
    return pages, None


def _from_pdf(gdal, osr, found: dict, page: int, cancelled) -> dict:

    path = found["path"]
    try:
        pages = _pdf_page_count(gdal, path)
        gdal_reads = True
    except RuntimeError:
        pages, gdal_reads = 0, False
    if gdal_reads and page > pages:
        return tool_error(f"{os.path.basename(path)} has {pages} page(s); page {page} does not exist.",
                          "INVALID_ARGS", f"page is 1 to {pages}.")
    dpi = float(PDF_DPI)
    stem = output_paths.safe_file_name(os.path.splitext(os.path.basename(path))[0], "plan")
    facts = {"page": page, "pages": pages or None, "dpi": PDF_DPI}
    if gdal_reads:
        try:
            dataset = _open_pdf_page(gdal, path, page, pages, dpi)
        except RuntimeError as exc:
            return tool_error(f"GDAL could not read page {page} of {os.path.basename(path)}: {str(exc)[:160]}",
                              "EXECUTION_FAILED", "An exported PNG or JPEG of the page works.")
        placed = _georeferencing(gdal, osr, dataset, path)
        if placed is not None:
            dataset = None
            return {"georeferenced_pdf": placed, "pdf": facts}
        cap = limits.current("GEOREFERENCE_MAX_PIXELS")
        pixels = dataset.RasterXSize * dataset.RasterYSize
        if pixels > cap:
            dpi = max(36.0, math.floor(dpi * math.sqrt(cap / pixels)))
            facts["dpi"] = dpi
            facts["dpi_note"] = f"At {PDF_DPI} DPI the page is {pixels:,} pixels; drawn at {dpi:g} DPI to fit."
            dataset = _open_pdf_page(gdal, path, page, pages, dpi)
    file_name = f"{stem}_page{page}_{int(dpi)}dpi.png"
    target = _writable_target(path, file_name)
    if not target:
        return tool_error("No folder next to the PDF or in the exports folder can take the page image.",
                          "PERMISSION_DENIED", "An exported image of the page works.")
    facts["rasterized_to"] = target
    if os.path.isfile(target) and os.path.getmtime(target) >= os.path.getmtime(path):
        facts["reused"] = True
        return {"image": target, "pdf": facts}
    part = os.path.join(os.path.dirname(target), f".{uuid.uuid4().hex[:12]}.part.png")
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if gdal_reads:
            gdal.Translate(part, dataset, format="PNG", creationOptions=["ZLEVEL=3"], callback=_progress(cancelled))
            dataset = None
        else:
            counted, why = _pdf_with_qt(path, page, dpi, part)
            if why:
                return tool_error(f"{os.path.basename(path)} cannot be drawn here: {why}.", "EXECUTION_FAILED",
                                  "An exported PNG or JPEG of the page works.")
            facts["pages"] = counted
        if cancelled is not None and cancelled():
            return _stopped()
        retry_file_op(os.replace, part, target)
        part = ""
    except RuntimeError as exc:
        if cancelled is not None and cancelled():
            return _stopped()
        return tool_error(f"Page {page} of {os.path.basename(path)} could not be drawn: {str(exc)[:160]}",
                          "EXECUTION_FAILED", "An exported PNG or JPEG of the page works.")
    except OSError as exc:
        return tool_error(f"The page image could not be written to {target}: {exc}", "EXECUTION_FAILED",
                          "An exported PNG or JPEG of the page works.")
    finally:
        dataset = None
        if part:
            remove_quietly(part)
    return {"image": target, "pdf": facts}




def _base_image(gdal, dataset, long_side: int, cancelled):

    width, height = dataset.RasterXSize, dataset.RasterYSize
    factor = min(1.0, long_side / max(width, height))
    options = {"format": "PNG", "width": max(1, round(width * factor)), "height": max(1, round(height * factor)),
               "resampleAlg": "average" if factor < 1.0 else "nearest", "callback": _progress(cancelled)}
    first = dataset.GetRasterBand(1)
    if dataset.RasterCount == 1 and first.GetColorTable() is not None:
        options["rgbExpand"] = "rgb"
    else:
        options["bandList"] = [1, 2, 3] if dataset.RasterCount >= 3 else [1]
    if first.DataType != gdal.GDT_Byte:
        options["outputType"] = gdal.GDT_Byte
        options["scaleParams"] = [[]]
    memory = f"/vsimem/georef_inspect_{uuid.uuid4().hex}.png"

    previous = gdal.GetThreadLocalConfigOption("GDAL_PAM_ENABLED", None)
    gdal.SetThreadLocalConfigOption("GDAL_PAM_ENABLED", "NO")
    try:
        gdal.Translate(memory, dataset, **options)
        raw = _read_vsimem(gdal, memory)
    finally:
        gdal.SetThreadLocalConfigOption("GDAL_PAM_ENABLED", previous)
        _unlink(gdal, memory)
    image = QImage()
    if not raw or not image.loadFromData(raw):
        return None
    return image.convertToFormat(QImage.Format.Format_RGB32)


def _grid_preview(base, width: int, height: int):

    image = base
    if max(base.width(), base.height()) > PREVIEW_LONG_SIDE:
        if base.width() >= base.height():
            image = base.scaledToWidth(PREVIEW_LONG_SIDE, Qt.TransformationMode.SmoothTransformation)
        else:
            image = base.scaledToHeight(PREVIEW_LONG_SIDE, Qt.TransformationMode.SmoothTransformation)
    image = image.convertToFormat(QImage.Format.Format_RGB32)
    w, h = image.width(), image.height()
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = QFont()
        font.setPixelSize(max(11, round(min(w, h) / 55)))
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()

        line_pen = QPen(QColor(230, 0, 90, 170))
        line_pen.setWidth(1)
        line_pen.setStyle(Qt.PenStyle.DashLine)
        box_fill = QColor(255, 255, 255, 215)
        text_pen = QPen(QColor(120, 0, 80))

        def label(text: str, cx: float, cy: float) -> None:
            tw, th = metrics.horizontalAdvance(text) + 6, metrics.height() + 2
            left = min(max(cx - tw / 2, 0), w - tw)
            top = min(max(cy - th / 2, 0), h - th)
            rect = QRectF(left, top, tw, th)
            painter.fillRect(rect, box_fill)
            painter.setPen(text_pen)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

        painter.setPen(line_pen)
        for step in range(1, GRID_STEPS):
            x = w * step / GRID_STEPS
            y = h * step / GRID_STEPS
            painter.drawLine(QLineF(x, 0, x, h))
            painter.drawLine(QLineF(0, y, w, y))
        half = metrics.height() / 2 + 2
        for step in range(1, GRID_STEPS):
            label(str(round(width * step / GRID_STEPS)), w * step / GRID_STEPS, half)
            label(str(round(height * step / GRID_STEPS)), metrics.horizontalAdvance("00000") / 2 + 4,
                  h * step / GRID_STEPS)
            painter.setPen(line_pen)
        label(f"pixel (top) and line (left) of the original, {width} x {height}", w / 2, h - half)
    finally:
        painter.end()
    return image




def _needs_points_suggestion(matching: bool, photo: dict | None) -> tuple[str, dict]:



    variant = "match" if matching else "no_match"
    if photo and photo.get("width_m") and "rotation_deg" in photo:
        text = (("The photo's GPS and camera give a rough footprint; match_georeference with center, width_m "
                "and rotation_deg (kind photo) places it precisely. Points are that footprint's corners, "
                "tens of metres off, usable with georeference_raster if matching fails.") if matching else
                ("The photo's GPS and camera give a rough footprint (tens of metres off); georeference_raster "
                "with these points and crs EPSG:4326 gives an approximate result."))
        return text, coded_fact(hint="georef_photo_footprint", variant=variant)
    if photo:
        text = ("The photo gives its position only; the preview and an estimated ground width feed "
                "match_georeference with center and width_m (kind photo)." if matching
                else "The photo gives its position only; the preview and 3 or more places read on it "
                "feed georeference_raster.")
        return text, coded_fact(hint="georef_photo_position", variant=variant)
    if matching:
        text = ("No coordinates in the file. match_georeference takes 2 to 4 recognisable places far apart (names, "
                "crossroads, bridges, coastline, grid ticks) as rough points, each with its pixel, line, place "
                "name and town (it looks them up; kind map or photo).")
    else:
        text = ("No coordinates in the file. georeference_raster takes 3 or more recognisable places far apart "
                "(names, crossroads, bridges, grid ticks), each geocoded with its pixel and line on the grid.")
    return text, coded_fact(hint="georef_no_coords", variant=variant)


def _inspect_georeference(args: dict) -> dict:
    try:
        from osgeo import gdal, osr
    except ImportError as exc:  # pragma: no cover
        return tool_error(f"GDAL is missing from this QGIS: {exc}", "EXECUTION_FAILED",
                          "QGIS's Georeferencer (Layer > Georeferencer) still works.")
    gdal.UseExceptions()
    page = args.get("page")
    if page is not None and (not isinstance(page, int) or isinstance(page, bool) or page < 1):
        return tool_error("page must be a page number, 1 or more.", "INVALID_ARGS", "page 1 is the first page.")
    cancelled = net.current_cancel_check()
    found = run_on_main_thread(_resolve, args.get("raster"))
    if "_error" in found:
        return found
    url = found.get("url")
    if not url:
        return _inspect_file(gdal, osr, args, found, page, cancelled)
    copy = _download(url, cancelled)
    if "_error" in copy:
        return copy
    result = _inspect_file(gdal, osr, args, {**found, "path": copy["path"]}, page, cancelled)
    if "_error" not in result:
        result.update({"url": url, "downloaded": copy["downloaded"]})
    elif copy["downloaded"] and result.get("code") == "EXECUTION_FAILED":

        remove_quietly(copy["path"])
    return result


def _inspect_file(gdal, osr, args: dict, found: dict, page, cancelled) -> dict:
    path = found["path"]
    extension = os.path.splitext(path)[1].lower()
    if extension in (".kml", ".kmz"):
        return _from_kml(gdal, found, cancelled)

    result: dict = {"source": path}
    if found.get("layer"):
        result["layer"] = found["layer"]
    image = path
    lead = ""
    if extension == ".pdf":
        pdf = _from_pdf(gdal, osr, found, page or found.get("page") or 1, cancelled)
        if "_error" in pdf:
            return pdf
        result["pdf"] = pdf["pdf"]
        if "georeferenced_pdf" in pdf:
            placed = pdf["georeferenced_pdf"]
            result.update({"raster": path, **placed})
            result["suggest"] = ("The PDF is a GeoPDF: add it with add_raster_layer, no control points are needed"
                                 + ("." if placed.get("crs") else ", then set its CRS."))
            return result
        image = pdf["image"]
        lead = (f"Page {result['pdf']['page']} was rasterized to {image} at {result['pdf']['dpi']:g} DPI: "
                "that file is now the raster. ")
    result["raster"] = image
    try:
        dataset = gdal.Open(image, gdal.GA_ReadOnly)
    except RuntimeError as exc:
        return tool_error(f"GDAL could not open {os.path.basename(image)}: {str(exc)[:160]}", "EXECUTION_FAILED",
                          "an image file (JPEG, PNG, TIFF), a PDF or a KMZ.")
    try:
        width, height = dataset.RasterXSize, dataset.RasterYSize
        result["width"], result["height"] = width, height
        placed = _georeferencing(gdal, osr, dataset, image)
        if placed is not None:
            result.update(placed)
            if placed.get("crs"):
                result["suggest"] = (f"Already georeferenced ({placed['georeferenced_by']}, {placed['crs']}); "
                                     "add_raster_layer adds it, no control points needed.")
            else:
                result["suggest"] = (f"It has coordinates ({placed['georeferenced_by']}) but no CRS; "
                                     "add_raster_layer adds it, and the CRS its extent is in still needs setting.")
            return result
        result["georeferenced"] = False
        photo = _photo_facts(image, width, height)
        if photo:
            result.update(photo)
        if cancelled is not None and cancelled():
            return _stopped()
        base = _base_image(gdal, dataset, MATCH_LONG_SIDE, cancelled)
    except RuntimeError as exc:
        if cancelled is not None and cancelled():
            return _stopped()
        return tool_error(f"{os.path.basename(image)} could not be read: {str(exc)[:160]}", "EXECUTION_FAILED",
                          "another copy of the image (JPEG, PNG or TIFF).")
    finally:
        dataset = None
    if base is None:
        return tool_error(f"{os.path.basename(image)} could not be decoded for a preview.", "EXECUTION_FAILED",
                          "another copy of the image (JPEG, PNG or TIFF).")
    preview = _grid_preview(base, width, height)
    result["image_base64"] = image_to_base64(preview, "jpeg", PREVIEW_JPEG_QUALITY)
    result["preview"] = {"width": preview.width(), "height": preview.height(),
                         "grid": "every 10 %; labels are pixel (top) and line (left) of the original image"}
    matching = tuning.flag("results", "match_image", False)
    if matching:
        copy = base
        encoded = image_to_base64(copy, "jpeg", MATCH_JPEG_QUALITY)
        if len(encoded) > MATCH_MAX_CHARS:
            copy = base.scaled(MATCH_FALLBACK_SIDE, MATCH_FALLBACK_SIDE, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            encoded = image_to_base64(copy, "jpeg", MATCH_JPEG_QUALITY)
        result["_match_image_base64"] = encoded
        result["_match_image"] = {"scale": round(copy.width() / width, 6), "width": copy.width(),
                                  "height": copy.height(), "raster": str(args.get("raster") or ""), "path": image,
                                  "layer": found.get("layer") or ""}
    text, fact = _needs_points_suggestion(matching, photo)
    result["suggest"] = lead + text
    if not lead:



        result.update(fact)
    return result


__all__ = ["register_georeference_inspect_tools"]
