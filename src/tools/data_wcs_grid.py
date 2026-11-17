# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

import math
import os
import urllib.error
import urllib.request

from ..core import limits, net, net_state
from ..core.host_platform import remove_quietly, remove_tree
from ..core.logger import log_warning
from ..core.policy import create_managed_temp_dir
from ..core.tool_registry import tool_error
from . import ogc_inspect

TIFF_MAGIC = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")



_PIECE_SIDE = 4096
_PIECE_BYTES = 32 * 1024 * 1024



_TRIES = 3


def is_grid(body: bytes, ext: str) -> bool:

    if ext == ".asc":
        return body[:64].lstrip().lower().startswith(b"ncols")
    return body.startswith(TIFF_MAGIC)


def ascii_grid_to_tiff(source: str, target: str, crs: str) -> str:


    from osgeo import gdal

    reason = ""
    try:
        written = gdal.Translate(target, source, format="GTiff", outputSRS=crs or None,
                                 creationOptions=["COMPRESS=DEFLATE", "TILED=YES", "BIGTIFF=IF_SAFER"])
        if written is None:
            reason = " ".join((gdal.GetLastErrorMsg() or "no dataset").split())[:200]
        del written
    except Exception as exc:  # noqa: BLE001
        reason = " ".join(str(exc).split())[:200]
    remove_quietly(source)
    return reason


def _native_box(bbox, crs: str):

    from osgeo import osr

    west, south, east, north = bbox
    source, target = osr.SpatialReference(), osr.SpatialReference()
    source.ImportFromEPSG(4326)
    if target.SetFromUserInput(crs) != 0:
        return None
    for ref in (source, target):
        ref.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(source, target)
    steps = [i / 20 for i in range(21)]
    edge = ([(west + (east - west) * t, south) for t in steps] + [(west + (east - west) * t, north) for t in steps]
            + [(west, south + (north - south) * t) for t in steps]
            + [(east, south + (north - south) * t) for t in steps])
    xs, ys = [], []
    for lon, lat in edge:
        x, y, _ = transform.TransformPoint(lon, lat)
        if math.isfinite(x) and math.isfinite(y):
            xs.append(x)
            ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _box_4326(box, crs: str) -> list:
    from osgeo import osr

    source, target = osr.SpatialReference(), osr.SpatialReference()
    source.SetFromUserInput(crs)
    target.ImportFromEPSG(4326)
    for ref in (source, target):
        ref.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(source, target)
    corners = [transform.TransformPoint(x, y)[:2] for x in (box[0], box[2]) for y in (box[1], box[3])]
    return [round(min(c[0] for c in corners), 6), round(min(c[1] for c in corners), 6),
            round(max(c[0] for c in corners), 6), round(max(c[1] for c in corners), 6)]


def read_in_pieces(base: str, coverage: str, fmt: str, ext: str, cell_bytes: int, crs: str,
                   resolution: float, bbox, stem: str) -> dict:








    from osgeo import gdal

    native = _native_box(bbox, crs)
    if native is None:
        return tool_error(f"The box could not be placed in {crs}, the coverage's own CRS.", "INVALID_ARGS",
                          hint="bbox_not_usable")
    x0 = math.floor(native[0] / resolution) * resolution
    y0 = math.floor(native[1] / resolution) * resolution
    nx = max(1, math.ceil((native[2] - x0) / resolution - 1e-9))
    ny = max(1, math.ceil((native[3] - y0) / resolution - 1e-9))
    x1, y1 = x0 + nx * resolution, y0 + ny * resolution

    grid_bytes = nx * ny * cell_bytes
    folder = create_managed_temp_dir("wcs")
    free = net.free_disk_bytes(folder)
    room = min(limits.current("MAX_STREAM_BYTES"),
               (free - net.STREAM_KEEP_FREE_BYTES) // 2 if free is not None else limits.current("MAX_STREAM_BYTES"))
    if grid_bytes > room:
        remove_tree(folder)
        return tool_error(f"{coverage!r} over that box is {nx:,} x {ny:,} cells, about {grid_bytes // 2**20:,} MB "
                          f"once written; this disk takes at most {max(room, 0) // 2**20:,} MB for one load.",
                          limits.CEILING_CODE, hint="ogc_wcs_box_cells")
    ceiling = limits.current("MAX_DOWNLOAD_BYTES")
    side = int(min(_PIECE_SIDE, math.sqrt(min(_PIECE_BYTES, ceiling) / cell_bytes)))
    cols, rows = math.ceil(nx / side), math.ceil(ny / side)
    width, height = math.ceil(nx / cols), math.ceil(ny / rows)
    pieces = [(c * width, r * height, min(width, nx - c * width), min(height, ny - r * height))
              for r in range(rows) for c in range(cols)]
    expected = nx * ny * cell_bytes
    sink = net_state.current_progress_sink()
    heard = [0]

    def progress(done, _total=None):
        if sink is not None:
            try:
                sink(heard[0] + int(done or 0), expected)
            except Exception:  # noqa: BLE001  # nosec B110
                pass

    written, unread, reason = [], [], ""
    try:
        for index, (cx, cy, cw, ch) in enumerate(pieces):
            box = (x0 + cx * resolution, y1 - (cy + ch) * resolution,
                   x0 + (cx + cw) * resolution, y1 - cy * resolution)
            params = {"SERVICE": "WCS", "VERSION": "1.0.0", "REQUEST": "GetCoverage", "COVERAGE": coverage,
                      "CRS": crs, "RESPONSE_CRS": crs, "BBOX": ",".join(repr(v) for v in box),
                      "WIDTH": cw, "HEIGHT": ch, "FORMAT": fmt}
            body = b""
            for _attempt in range(_TRIES):
                try:
                    answer = net.fetch(urllib.request.Request(ogc_inspect.with_query(base, params)),
                                       timeout=30, max_bytes=ceiling, total_timeout=180, progress=progress)
                except net.FetchCancelled:
                    raise
                except (urllib.error.URLError, OSError) as exc:
                    reason = str(exc)[:300]
                    if isinstance(exc, urllib.error.HTTPError):
                        break
                    continue
                body = answer.body or b""
                if is_grid(body, ext):
                    break
                reason = (ogc_inspect.wcs10_description(body).get("exception")
                          or body[:200].decode("utf-8", "replace"))
                body = b""
            if not body:
                unread.append(_box_4326(box, crs))
                log_warning(f"WCS piece {index + 1} of {len(pieces)} of {coverage} not read: {reason}")
                if not written and len(unread) >= 2:
                    break
                continue
            heard[0] += len(body)
            path = os.path.join(folder, f"p{index}.tif")
            try:
                with open(path if ext != ".asc" else path[:-4] + ".asc", "wb") as handle:
                    handle.write(body)
            except OSError as exc:
                remove_tree(folder)
                return tool_error(f"Could not write the coverage to disk: {exc}", hint="ogc_wcs_write_failed")
            del body
            if ext == ".asc":
                failure = ascii_grid_to_tiff(path[:-4] + ".asc", path, crs)
                if failure:
                    remove_quietly(path)
                    unread.append(_box_4326(box, crs))
                    reason = failure
                    continue
            written.append(path)
        if not written:
            remove_tree(folder)
            return tool_error(f"No piece of {coverage!r} came back from {base} ({len(pieces)} asked): {reason}",
                              hint="ogc_wcs_getcoverage_failed")
        out = os.path.join(folder, f"{stem}.tif")
        failure = _mosaic(gdal, written, out, (x0, y0, x1, y1), resolution, crs, bool(unread))
        for path in written:
            remove_quietly(path)
        if failure:
            remove_tree(folder)
            return tool_error(f"The {len(written)} pieces of {coverage!r} could not be put together: {failure}",
                              hint="ogc_wcs_geotiff_unreadable")
    except BaseException:
        remove_tree(folder)
        raise
    result = {"path": out, "size_bytes": os.path.getsize(out), "download_bytes": heard[0], "cells": [nx, ny],
              "pieces": {"read": len(written), "asked": len(pieces)}}
    if unread:
        result.update(coverage="partial", pieces_unread=unread)
    return result


def _mosaic(gdal, pieces: list, out: str, bounds, resolution: float, crs: str, holes: bool) -> str:

    vrt_path = out[:-4] + ".vrt"
    first = gdal.Open(pieces[0])
    if first is None:
        return " ".join((gdal.GetLastErrorMsg() or "the first piece is not readable").split())[:200]
    band = first.GetRasterBand(1)
    nodata, data_type = band.GetNoDataValue(), band.DataType
    first = band = None
    options = {"outputBounds": bounds, "xRes": resolution, "yRes": resolution, "resampleAlg": "nearest"}
    if holes and nodata is None:

        options["VRTNodata"] = 0 if data_type in (gdal.GDT_Byte, gdal.GDT_UInt16, gdal.GDT_UInt32) else -9999
    float_band = data_type in (gdal.GDT_Float32, gdal.GDT_Float64)

    def keep_going(_share, _message, _data):
        return 0 if net.is_cancelled() else 1

    reason, vrt, written = "", None, None
    try:
        vrt = gdal.BuildVRT(vrt_path, pieces, **options)
        if vrt is None:
            reason = " ".join((gdal.GetLastErrorMsg() or "no mosaic").split())[:200]
        else:
            written = gdal.Translate(out, vrt, format="GTiff", outputSRS=crs, callback=keep_going,
                                     creationOptions=["COMPRESS=DEFLATE", "TILED=YES", "BIGTIFF=IF_SAFER",
                                                      f"PREDICTOR={3 if float_band else 2}"])
            if written is None:
                reason = " ".join((gdal.GetLastErrorMsg() or "no GeoTIFF").split())[:200]
    except Exception as exc:  # noqa: BLE001
        reason = " ".join(str(exc).split())[:200]
    finally:
        written = vrt = None
    remove_quietly(vrt_path)
    if net.is_cancelled():
        raise net.FetchCancelled("The run was stopped.")
    return reason
