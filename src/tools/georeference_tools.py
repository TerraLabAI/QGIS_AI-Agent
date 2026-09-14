# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

























from __future__ import annotations

import math
import os
import time
import uuid

from qgis.core import Qgis, QgsCoordinateReferenceSystem, QgsProject, QgsRasterLayer, QgsUnitTypes
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core import limits, net, output_paths, security, tuning
from ..core.host_platform import remove_quietly, retry_file_op
from ..core.logger import log_warning
from ..core.qt_compat import enum_member
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .data_tools import _run_on_main_thread
from .layer_lookup import _find_layer, _layer_not_found_error






MIN_POINTS = {"polynomial_1": 3, "polynomial_2": 6, "polynomial_3": 10, "thin_plate_spline": 3,
              "linear": 2, "helmert": 2}


_AFFINE = ("linear", "helmert")



TOLERANCE_M_PER_SCALE = 0.0002


_M_PER_DEGREE = 111_320.0
_ORDER = {"polynomial_1": 1, "polynomial_2": 2, "polynomial_3": 3}
_RESAMPLING = {"nearest": "near", "bilinear": "bilinear", "cubic": "cubic", "cubic_spline": "cubicspline",
               "lanczos": "lanczos"}


_RESIDUALS_SHOWN = 30
_CREATION = ["TILED=YES", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER"]


_LINE_PX = 0.5
_WARP_MEMORY = 256 * 1024 * 1024


def register_georeference_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="georeference_raster",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Georeference {raster}"),
        input_schema={
            "type": "object",
            "properties": {
                "raster": {"type": "string"},
                "points": {
                    "type": "array",
                    "maxItems": 500,
                    "items": {
                        "type": "object",
                        "properties": {
                            "pixel": {"type": "number"},
                            "line": {"type": "number"},
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                        },
                        "required": ["pixel", "line", "x", "y"],
                    },
                },
                "crs": {"type": "string"},
                "transform": {"type": "string",
                              "enum": ["polynomial_1", "polynomial_2", "polynomial_3", "thin_plate_spline",
                                       "linear", "helmert"]},
                "resampling": {"type": "string", "enum": ["nearest", "bilinear", "cubic", "cubic_spline", "lanczos"]},
                "pixel_size": {"type": "number", "exclusiveMinimum": 0},
                "output_path": {"type": "string"},
                "overwrite": {"type": "boolean"},
                "name": {"type": "string"},
                "target_scale": {"type": "number", "minimum": 1},
            },
            "required": ["raster", "points", "crs"],
        },
        handler=_georeference_raster,
        background=True,
    ))




def _parse_crs(text):
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        raw = f"EPSG:{raw}"
    crs = QgsCoordinateReferenceSystem(raw)
    if not crs.isValid():
        crs = QgsCoordinateReferenceSystem()
        crs.createFromUserInput(raw)
    return crs if crs.isValid() else None


def _wkt(crs) -> str:

    variant = enum_member(Qgis, "CrsWktVariant", "Preferred", None)
    if variant is None:
        variant = enum_member(QgsCoordinateReferenceSystem, "WktVariant", "WKT_PREFERRED", None)
    return crs.toWkt(variant) if variant is not None else crs.toWkt()


def _source_facts(raster, crs_text) -> dict:

    crs = _parse_crs(crs_text)
    if crs is None:
        return tool_error(
            f"crs {str(crs_text or '')[:60]!r} is not a CRS QGIS knows.", "INVALID_ARGS",
            "Pass the CRS the points' x and y are in: EPSG:2154 for Lambert 93, EPSG:4326 for degrees, or a WKT.")
    ref = str(raster or "").strip()
    expanded = security.expand_path(ref) if ref else ""
    if expanded and os.path.isfile(expanded):
        error = security.validate_path(expanded)
        if error:
            return tool_error(error, "PERMISSION_DENIED", "Pick an image under the project folder or your home folder.")
        name, source, path, layer_id = os.path.splitext(os.path.basename(expanded))[0], expanded, expanded, None
    else:
        layer = _find_layer(ref)
        if layer is None:
            if "/" in ref or "\\" in ref or os.path.splitext(ref)[1]:
                return tool_error(f"No file at {expanded}.", "INVALID_ARGS",
                                  "Check the path, or pass the loaded raster layer by name.")
            return _layer_not_found_error(ref)
        if not isinstance(layer, QgsRasterLayer):
            return tool_error(f"{layer.name()} is not a raster layer.", "INVALID_ARGS",
                              "Pass the scanned map or image: a raster layer by name, or its file path.")
        if layer.providerType() != "gdal":
            return tool_error(
                f"{layer.name()} uses the {layer.providerType()} provider; only an image file can be georeferenced.",
                "INVALID_ARGS", "Pass the scan's own file (JPEG, PNG, TIFF), by path or loaded with add_data.")
        source = str(layer.source() or "")
        path = source if os.path.isfile(source) else ""
        name, layer_id = layer.name(), layer.id()
    return {
        "name": name, "source": source, "path": path, "layer_id": layer_id,
        "crs": _wkt(crs),
        "crs_authid": str(crs.authid() or ""),
        "geographic": bool(crs.isGeographic()),
        "units": "degrees" if crs.isGeographic() else QgsUnitTypes.toString(crs.mapUnits()),
        "metres_per_unit": None if crs.isGeographic() else _metres_per_unit(crs),
    }


def _metres_per_unit(crs) -> float | None:

    metres = enum_member(Qgis, "DistanceUnit", "Meters", None)
    if metres is None:
        metres = enum_member(QgsUnitTypes, "DistanceUnit", "DistanceMeters")
    try:
        factor = float(QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), metres))
    except (TypeError, ValueError):
        return None
    return factor if factor > 0 and math.isfinite(factor) else None


def _add_raster(path: str, name: str) -> dict:
    layer = QgsRasterLayer(path, name, "gdal")
    if not layer.isValid():
        return tool_error(f"The GeoTIFF is written at {path}, but QGIS cannot open it.", "EXECUTION_FAILED",
                          "Load it with add_data; if that fails too, report the file.")
    QgsProject.instance().addMapLayer(layer)
    rect = layer.extent()
    return {"layer_id": layer.id(), "layer_name": layer.name(),
            "extent": [rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum()]}




def _too_few(transform: str, given: int) -> dict:
    needed = MIN_POINTS[transform]
    missing = needed - given
    fallback = ""
    if transform != "polynomial_1" and given >= MIN_POINTS["polynomial_1"]:
        fallback = f", or pass transform polynomial_1, which needs {MIN_POINTS['polynomial_1']}"
    elif transform not in _AFFINE and given >= MIN_POINTS["helmert"]:
        fallback = f", or pass transform helmert (a shift, a scale and a rotation), which needs {MIN_POINTS['helmert']}"
    return tool_error(
        f"{transform} needs at least {needed} control points; {given} given, {missing} missing.", "INVALID_ARGS",
        f"Add {missing} more point{'s' if missing != 1 else ''} spread over the image{fallback}.")


def _parse_points(points: list):

    parsed = []
    for index, raw in enumerate(points, 1):
        values = {}
        for key in ("pixel", "line", "x", "y"):
            try:
                value = float(raw.get(key)) if isinstance(raw, dict) else float("nan")
            except (TypeError, ValueError):
                value = float("nan")
            if not math.isfinite(value):
                return tool_error(f"Point {index} has no number for {key}.", "INVALID_ARGS",
                                  'Pass every point as {"pixel": column, "line": row, "x": map x, "y": map y}.')
            values[key] = value
        parsed.append(values)
    return parsed


def _outside(points: list, width: int, height: int, name: str) -> dict | None:
    out = [index for index, p in enumerate(points, 1)
           if not (0 <= p["pixel"] <= width and 0 <= p["line"] <= height)]
    if not out:
        return None
    first = points[out[0] - 1]
    listed = ", ".join(str(i) for i in out[:8]) + (" and more" if len(out) > 8 else "")
    advice = (f"pixel runs 0 to {width} from the left edge and line 0 to {height} from the top; read the "
              "points on this image.")
    if first["line"] < 0 <= -first["line"] <= height:
        advice = ("Drop the minus sign from line: a QGIS .points file writes the row as a negative sourceY. "
                  + advice)
    return tool_error(
        f"Point{'s' if len(out) > 1 else ''} {listed} lie{'' if len(out) > 1 else 's'} outside {name}, which is "
        f"{width} by {height} pixels (point {out[0]} is at pixel {first['pixel']:g}, line {first['line']:g}).",
        "INVALID_ARGS", advice)


def _on_one_line(points: list) -> bool:






    distinct = list({(p["pixel"], p["line"]) for p in points})
    if len(distinct) < 3:
        return True

    ax, ay = distinct[0]
    bx, by = max(distinct, key=lambda q: (q[0] - ax) ** 2 + (q[1] - ay) ** 2)
    ax, ay = max(distinct, key=lambda q: (q[0] - bx) ** 2 + (q[1] - by) ** 2)
    length = math.hypot(bx - ax, by - ay)
    return all(abs((bx - ax) * (qy - ay) - (by - ay) * (qx - ax)) <= _LINE_PX * length for qx, qy in distinct)


def _same_file(path: str, source: str) -> bool:


    if not path or not source or not os.path.exists(path):
        return False
    try:
        return os.path.samefile(path, source)
    except OSError:
        return os.path.realpath(path) == os.path.realpath(source)


def _output_target(args: dict, facts: dict):

    asked = str(args.get("output_path") or "").strip()
    expanded = security.expand_path(asked) if asked else ""
    if expanded and not os.path.isdir(expanded):
        target = expanded
        extension = os.path.splitext(target)[1].lower()
        if not extension:
            target += ".tif"
        elif extension not in (".tif", ".tiff"):
            return None, tool_error(f"{os.path.basename(target)} is not a GeoTIFF name.", "INVALID_ARGS",
                                    "End output_path in .tif: the tool writes a GeoTIFF.")
        if _same_file(target, facts["path"]):
            return None, tool_error(
                "output_path is the image being georeferenced, and the tool never writes over it.", "INVALID_ARGS",
                "Leave output_path out to write <name>_georeferenced.tif next to the image, or name another file.")
        error = security.validate_path(target, write=True)
        if error:
            return None, tool_error(error, "PERMISSION_DENIED",
                                    "Pick a path under the project folder, your home folder or the temp folder.")
        if os.path.exists(target) and args.get("overwrite") is not True:
            return None, tool_error(f"{target} already exists.", "INVALID_ARGS",
                                    "Tell the user the file exists and ask whether to replace it (overwrite true) "
                                    "or name a new file.")
        return target, None

    stem = output_paths.safe_file_name(f"{facts['name']}_georeferenced", "georeferenced")
    if expanded:
        folders = [expanded]
    else:
        folders = ([os.path.dirname(facts["path"])] if facts["path"] else []) + [output_paths.default_folder()]
    for folder in folders:
        target = ""
        for index in range(1, 1000):
            candidate = os.path.join(folder, f"{stem}_{index}.tif" if index > 1 else f"{stem}.tif")
            if not os.path.exists(candidate):
                target = candidate
                break
        writable = os.access(folder, os.W_OK) if os.path.isdir(folder) else True
        if target and writable and not security.validate_path(target, write=True):
            return target, None
    return None, tool_error("No folder next to the image or in the exports folder can take the GeoTIFF.",
                            "PERMISSION_DENIED", "Pass output_path under your home folder.")


def _round_up(value: float) -> float:

    if not value > 0:
        return value
    factor = 10 ** (2 - int(math.floor(math.log10(value))))
    return math.ceil(value * factor) / factor


def _size_refusal(cols: int, rows: int, size: float, units: str) -> dict | None:
    allowed = int(limits.current("GEOREFERENCE_MAX_PIXELS"))
    pixels = cols * rows
    if pixels <= allowed:
        return None
    fits = _round_up(size * math.sqrt(pixels / float(allowed)))
    return limits.refusal(
        "The georeferenced image", f"{cols:,} by {rows:,} pixels", f"{allowed:,} pixels",
        f"Pass pixel_size {fits:g} or more (in {units}), or crop the scan to the part that matters first.")


def _pixel_size(geotransform) -> float:
    return math.sqrt(abs(geotransform[1] * geotransform[5] - geotransform[2] * geotransform[4]))


def _unsolvable(transform: str) -> dict:
    return tool_error(
        f"The control points fix no {transform} transform.", "INVALID_ARGS",
        "Spread the points over the image: three on one line, or two at the same place, fix nothing.")


def _affine(points: list, transform: str):






    n = float(len(points))
    mp = sum(p["pixel"] for p in points) / n
    ml = sum(p["line"] for p in points) / n
    mx = sum(p["x"] for p in points) / n
    my = sum(p["y"] for p in points) / n
    rows = [(p["pixel"] - mp, p["line"] - ml, p["x"] - mx, p["y"] - my) for p in points]
    if transform == "linear":
        spread_p = sum(u * u for u, _l, _x, _y in rows)
        spread_l = sum(v * v for _u, v, _x, _y in rows)
        if spread_p <= 0 or spread_l <= 0:
            return None
        sx = sum(u * x for u, _v, x, _y in rows) / spread_p
        sy = sum(v * y for _u, v, _x, y in rows) / spread_l
        if sx == 0 or sy == 0:
            return None
        return (mx - sx * mp, sx, 0.0, my - sy * ml, 0.0, sy)
    spread = sum(u * u + v * v for u, v, _x, _y in rows)
    if spread <= 0:
        return None
    a = sum(u * x - v * y for u, v, x, y in rows) / spread
    b = sum(u * y + v * x for u, v, x, y in rows) / spread
    if a == 0 and b == 0:
        return None
    return (mx - a * mp - b * ml, a, b, my - b * mp + a * ml, b, -a)


def _affine_refusal(points: list, transform: str) -> dict | None:


    distinct = {(p["pixel"], p["line"]) for p in points}
    if len(distinct) < 2:
        return tool_error(
            f"The control points sit at one place on the image, so they fix no {transform} transform.",
            "INVALID_ARGS", "Pass at least two points far apart, for example two opposite corners of the image.")
    if transform == "linear":
        columns = [p["pixel"] for p in points]
        lines = [p["line"] for p in points]
        if max(columns) - min(columns) <= _LINE_PX or max(lines) - min(lines) <= _LINE_PX:
            return tool_error(
                "linear scales the columns and the rows on their own, and these points all share one "
                f"{'column' if max(columns) - min(columns) <= _LINE_PX else 'row'}.", "INVALID_ARGS",
                "Add a point in another column and another row, or pass transform helmert.")
    return None


def _residuals(gdal, dataset, points: list, transform: str, crs: str):





    if transform in _AFFINE:
        gt = _affine(points, transform)
        if gt is None:
            return _unsolvable(transform)
        return [(gt[0] + p["pixel"] * gt[1] + p["line"] * gt[2] - p["x"],
                 gt[3] + p["pixel"] * gt[4] + p["line"] * gt[5] - p["y"]) for p in points]
    vrt = f"/vsimem/georef_{uuid.uuid4().hex[:16]}_fit.vrt"
    try:
        gdal.Translate(vrt, dataset, format="VRT", outputSRS=crs,
                       GCPs=[gdal.GCP(p["x"], p["y"], 0.0, p["pixel"], p["line"]) for p in points])
        return _fitted_errors(gdal, vrt, points, transform)
    finally:
        _unlink(gdal, vrt)


def _exact(transform: str, count: int) -> bool:

    return transform == "thin_plate_spline" or count == MIN_POINTS[transform]


def _metre_lengths(errors: list, points: list, facts: dict) -> list | None:

    if facts["geographic"]:
        latitude = sum(p["y"] for p in points) / len(points)
        fx, fy = _M_PER_DEGREE * math.cos(math.radians(latitude)), _M_PER_DEGREE
    else:
        fx = fy = facts.get("metres_per_unit")
        if not fx:
            return None
    return [math.hypot(dx * fx, dy * fy) for dx, dy in errors]


def _rms(values: list) -> float:
    return math.sqrt(sum(v * v for v in values) / len(values)) if values else 0.0


def _accuracy(gdal, dataset, points: list, errors: list, transform: str, facts: dict, scale: float) -> dict:






    tolerance = scale * tuning.threshold("georeference_tolerance_m_per_scale", TOLERANCE_M_PER_SCALE,
                                         0.0001, 0.0005)
    out = {"target_scale": f"1:{scale:,.0f}", "tolerance_m": round(tolerance, 3)}
    lengths = _metre_lengths(errors, points, facts)
    if lengths is None:
        out.update(verdict="unknown", reason=f"The CRS's unit ({facts['units']}) has no length in metres.")
        return out
    out.update(rmse_m=round(_rms(lengths), 3), max_error_m=round(max(lengths), 3))
    if _exact(transform, len(points)):
        out.update(verdict="not_measurable",
                   reason="The transform passes through every control point, so no residual can fail.")
        return out
    failed = [i for i, length in enumerate(lengths, 1) if length > tolerance]
    out["point_verdicts"] = ["fail" if length > tolerance else "pass" for length in lengths]
    out["verdict"] = "fail" if failed or _rms(lengths) > tolerance else "pass"
    if not failed:
        return out
    out["failed_points"] = failed
    rest = [p for i, p in enumerate(points, 1) if i not in failed]
    if len(rest) < MIN_POINTS[transform] or (
            _affine_refusal(rest, transform) if transform in _AFFINE else _on_one_line(rest)):
        out["without_failed"] = {"points": len(rest),
                                 "reason": f"Too few points are left to fit {transform} without the failed ones."}
        return out
    refit = _residuals(gdal, dataset, rest, transform, facts["crs"])
    if isinstance(refit, dict):
        out["without_failed"] = {"points": len(rest), "reason": refit.get("_error", "No fit.")}
        return out
    rest_lengths = _metre_lengths(refit, rest, facts) or []
    again = {"points": len(rest), "rmse_m": round(_rms(rest_lengths), 3),
             "max_error_m": round(max(rest_lengths), 3) if rest_lengths else 0.0}
    if _exact(transform, len(rest)):
        again.update(verdict="not_measurable",
                     reason="Without them the transform passes through every point left.")
    else:
        again["verdict"] = "pass" if all(length <= tolerance for length in rest_lengths) else "fail"
    out["without_failed"] = again
    return out




_WRITTEN_POINTS: dict[str, str] = {}


def _points_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _digest(path: str) -> str:
    import hashlib

    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return ""


def _points_text(facts: dict, points: list, errors: list, source_gt, pixel: float) -> str:




    identity = source_gt is None or tuple(source_gt) == (0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    rows = [f"#CRS: {facts['crs']}", "mapX,mapY,sourceX,sourceY,enable,dX,dY,residual"]
    for p, (dx, dy) in zip(points, errors):
        if identity:
            sx, sy = p["pixel"], -p["line"] or 0.0
        else:
            sx = source_gt[0] + p["pixel"] * source_gt[1] + p["line"] * source_gt[2]
            sy = source_gt[3] + p["pixel"] * source_gt[4] + p["line"] * source_gt[5]
        rx, ry = (dx / pixel, dy / pixel) if pixel else (0.0, 0.0)
        values = (p["x"], p["y"], sx, sy)
        rows.append(",".join(repr(float(v)) for v in values) + ",1,"
                    + ",".join(repr(round(float(v), 6)) for v in (rx, ry, math.hypot(rx, ry))))
    return "\n".join(rows) + "\n"


def _write_points(facts: dict, target: str, text: str) -> tuple[str, str]:


    from ..core.writeback import write_atomic

    candidates = []
    if facts["path"]:
        candidates.append(facts["path"] + ".points")
        candidates.append(os.path.join(os.path.dirname(target), os.path.basename(facts["path"]) + ".points"))
    candidates.append(os.path.splitext(target)[0] + ".points")
    seen, reason = set(), "no folder could take it"
    for path in candidates:
        key = _points_key(path)
        if key in seen:
            continue
        seen.add(key)
        if os.path.exists(path) and _WRITTEN_POINTS.get(key) != _digest(path):
            reason = f"{path} already holds points this tool did not write"
            continue
        if security.validate_path(path, write=True) or not security.fits_path(path, len(".agent-.tmp") + 8):
            continue
        try:
            write_atomic(path, text)
        except OSError as exc:
            reason = str(exc.strerror or exc)
            continue
        _WRITTEN_POINTS[key] = _digest(path)
        return path, ""
    return "", reason


def _fitted_errors(gdal, gcp_vrt: str, points: list, transform: str):

    order = _ORDER.get(transform)
    options = ["SRC_METHOD=GCP_TPS"] if order is None else ["SRC_METHOD=GCP_POLYNOMIAL", f"MAX_GCP_ORDER={order}"]
    unsolvable = _unsolvable(transform)
    try:
        fitted = gdal.Transformer(gdal.Open(gcp_vrt), None, options)
    except RuntimeError:
        return unsolvable
    if fitted is None:
        return unsolvable
    errors = []
    for p in points:
        ok, where = fitted.TransformPoint(0, p["pixel"], p["line"])
        if not ok or not all(math.isfinite(v) for v in where[:2]):
            return unsolvable
        errors.append((where[0] - p["x"], where[1] - p["y"]))
    return errors


def _unlink(gdal, path: str) -> None:
    if path and gdal.VSIStatL(path) is not None:
        gdal.Unlink(path)


def _remove(path: str) -> None:
    if path:
        remove_quietly(path)


def _build_overviews(gdal, dataset, method: str, levels: list, progress) -> None:






    try:
        dataset.BuildOverviews(method, levels, progress, options=["COMPRESS_OVERVIEW=DEFLATE"])
        return
    except TypeError:
        log_warning("This GDAL's BuildOverviews takes no options: COMPRESS_OVERVIEW is set as a config option.")
    set_local = getattr(gdal, "SetThreadLocalConfigOption", None)
    get_local = getattr(gdal, "GetThreadLocalConfigOption", None)
    if callable(set_local) and callable(get_local):
        setter, previous = set_local, get_local("COMPRESS_OVERVIEW", None)
    else:
        setter, previous = gdal.SetConfigOption, gdal.GetConfigOption("COMPRESS_OVERVIEW", None)
    setter("COMPRESS_OVERVIEW", "DEFLATE")
    try:
        dataset.BuildOverviews(method, levels, progress)
    finally:
        setter("COMPRESS_OVERVIEW", previous)


def _overviews(gdal, path: str, method: str, progress) -> None:





    dataset = gdal.Open(path, gdal.GA_Update)
    failure = None
    try:
        size = max(dataset.RasterXSize, dataset.RasterYSize)
        levels, level = [], 2
        while size / level >= 256:
            levels.append(level)
            level *= 2
        if levels:
            _build_overviews(gdal, dataset, method, levels, progress)
    except Exception as exc:  # noqa: BLE001
        failure = exc.with_traceback(None)
    del dataset
    if failure is not None:
        raise failure


def _stopped() -> dict:
    return tool_error("Stopped before the georeferenced image was written.", "CANCELLED",
                      "Nothing was added to the project.")


def _georeference_raster(args: dict) -> dict:
    try:
        from osgeo import gdal
    except ImportError as exc:  # pragma: no cover
        return tool_error(f"GDAL is missing from this QGIS: {exc}", "EXECUTION_FAILED",
                          "Use QGIS's Georeferencer (Layer > Georeferencer) instead.")
    gdal.UseExceptions()
    transform = str(args.get("transform") or "polynomial_1")
    if transform not in MIN_POINTS:
        return tool_error(f"transform {transform!r} is not one this tool knows.", "INVALID_ARGS",
                          "Pass helmert (2 points or more), linear (2), polynomial_1 (3), polynomial_2 (6), "
                          "polynomial_3 (10) or thin_plate_spline (3).")
    scale = args.get("target_scale")
    if scale is not None:
        try:
            scale = float(scale)
        except (TypeError, ValueError):
            scale = 0.0
        if not math.isfinite(scale) or scale < 1:
            return tool_error("target_scale must be the scale's denominator, 1 or more.", "INVALID_ARGS",
                              "Pass 25000 for a map at 1:25,000, or leave it out.")
    resampling = str(args.get("resampling") or "nearest")
    if resampling not in _RESAMPLING:
        return tool_error(f"resampling {resampling!r} is not one this tool knows.", "INVALID_ARGS",
                          f"Pass one of {', '.join(_RESAMPLING)}.")
    points = args.get("points") if isinstance(args.get("points"), list) else []
    if len(points) < MIN_POINTS[transform]:
        return _too_few(transform, len(points))
    parsed = _parse_points(points)
    if isinstance(parsed, dict):
        return parsed
    if transform in _AFFINE:
        refused = _affine_refusal(parsed, transform)
        if refused:
            return refused
    elif _on_one_line(parsed):
        return tool_error(
            f"The control points' pixel positions all lie on one line, so they fix no {transform} transform: "
            "nothing places the image away from that line.", "INVALID_ARGS",
            "Add a point well off that line, for example three corners of the image; two points at the same "
            "pixel count as one.")
    pixel_size = args.get("pixel_size")
    if pixel_size is not None:
        try:
            pixel_size = float(pixel_size)
        except (TypeError, ValueError):
            pixel_size = 0.0
        if not pixel_size > 0 or not math.isfinite(pixel_size):
            return tool_error("pixel_size must be a positive number.", "INVALID_ARGS",
                              "Pass the ground size of one output pixel in the CRS units, or leave it out.")
    started = time.monotonic()
    cancelled = net.current_cancel_check()

    facts = _run_on_main_thread(_source_facts, args.get("raster"), args.get("crs"))
    if "_error" in facts:
        return facts
    if facts["geographic"]:
        far = next((i for i, p in enumerate(parsed, 1) if abs(p["x"]) > 180 or abs(p["y"]) > 90), None)
        if far is not None:
            p = parsed[far - 1]
            return tool_error(
                f"crs {facts['crs_authid'] or 'given'} is in degrees, and point {far} has x {p['x']:g}, "
                f"y {p['y']:g}, which are not degrees.", "INVALID_ARGS",
                "Pass the projected CRS these coordinates are in (EPSG:2154 for metres in France), or the points "
                "as longitude and latitude.")
    try:
        dataset = gdal.Open(facts["source"], gdal.GA_ReadOnly)
    except RuntimeError as exc:
        return tool_error(f"GDAL could not open {facts['name']}: {str(exc)[:160]}", "EXECUTION_FAILED",
                          "Pass the image file itself (JPEG, PNG, TIFF).")
    width, height = dataset.RasterXSize, dataset.RasterYSize
    refused = _outside(parsed, width, height, facts["name"])
    if refused:
        return refused
    target, refused = _output_target(args, facts)
    if refused:
        return refused
    if os.path.exists(target):



        def _readers() -> dict:
            names = []
            for layer in QgsProject.instance().mapLayers().values():
                source = str(layer.source() or "").split("|", 1)[0]
                if _same_file(target, source):
                    names.append(layer.name())
            return {"names": names}

        readers = _run_on_main_thread(_readers)
        if readers.get("names"):
            listed = ", ".join(repr(name) for name in readers["names"][:5])
            return tool_error(
                f"{target} is the file the layer {listed} reads in this project, so it is not replaced.",
                "INVALID_ARGS",
                "Remove that layer with remove_layer and call again, or leave output_path out to write a new file.")



    token = uuid.uuid4().hex[:16]
    gcp_vrt = f"/vsimem/georef_{token}.vrt"
    plan_vrt = f"/vsimem/georef_{token}_plan.vrt"
    part = ""
    failed = None
    order = _ORDER.get(transform)

    method = {} if transform in _AFFINE else {"tps": True} if order is None else {"polynomialOrder": order}
    paletted = dataset.RasterCount == 1 and dataset.GetRasterBand(1).GetColorTable() is not None
    has_nodata = any(dataset.GetRasterBand(b).GetNoDataValue() is not None
                     for b in range(1, dataset.RasterCount + 1))
    source_gt = dataset.GetGeoTransform(can_return_null=True)
    accuracy = None

    def progress(_complete, _message, _data):
        return 0 if (cancelled is not None and cancelled()) else 1

    try:
        if transform in _AFFINE:
            fitted_gt = _affine(parsed, transform)
            if fitted_gt is None:
                return _unsolvable(transform)
            vrt = gdal.Translate(gcp_vrt, dataset, format="VRT", outputSRS=facts["crs"])
            vrt.SetGeoTransform(fitted_gt)
            del vrt
        else:
            gcps = [gdal.GCP(p["x"], p["y"], 0.0, p["pixel"], p["line"]) for p in parsed]
            gdal.Translate(gcp_vrt, dataset, format="VRT", GCPs=gcps, outputSRS=facts["crs"])
        errors = (_residuals(gdal, dataset, parsed, transform, facts["crs"]) if transform in _AFFINE
                  else _fitted_errors(gdal, gcp_vrt, parsed, transform))
        if isinstance(errors, dict):
            return errors
        if scale:
            accuracy = _accuracy(gdal, dataset, parsed, errors, transform, facts, scale)


        plan = gdal.Warp(plan_vrt, gcp_vrt, format="VRT", dstSRS=facts["crs"], **method)
        natural = _pixel_size(plan.GetGeoTransform())
        cols, rows = plan.RasterXSize, plan.RasterYSize
        del plan
        if pixel_size:
            _unlink(gdal, plan_vrt)
            plan = gdal.Warp(plan_vrt, gcp_vrt, format="VRT", dstSRS=facts["crs"], xRes=pixel_size,
                             yRes=pixel_size, **method)
            cols, rows = plan.RasterXSize, plan.RasterYSize
            del plan
        refused = _size_refusal(cols, rows, pixel_size or natural, facts["units"])
        if refused:
            return refused

        folder = os.path.dirname(target)
        os.makedirs(folder, exist_ok=True)
        part = os.path.join(folder, f".{os.path.basename(target)}.{token}.part.tif")
        if not security.fits_path(part, len(".aux.xml")):


            part = os.path.join(folder, f".georef-{token}.part.tif")
        options = gdal.WarpOptions(
            format="GTiff", dstSRS=facts["crs"], xRes=pixel_size, yRes=pixel_size,
            resampleAlg="near" if paletted else _RESAMPLING[resampling],

            dstAlpha=not has_nodata, multithread=True, warpMemoryLimit=_WARP_MEMORY, creationOptions=_CREATION,
            callback=progress, **method)

        gdal.Warp(part, gcp_vrt, options=options)
        _overviews(gdal, part, "NEAREST" if paletted else "AVERAGE", progress)
        try:
            retry_file_op(os.replace, part, target)
        except PermissionError as exc:



            failed = tool_error(
                f"The georeferenced image was computed but could not replace {target}: the file is open, in this "
                f"project or another program ({exc.strerror or exc}).", "EXECUTION_FAILED",
                "Remove the layer that reads that file with remove_layer and call again, or leave output_path out.")
        else:
            part = ""
    except InterruptedError:
        failed = _stopped()
    except (RuntimeError, OSError) as exc:
        failed = _stopped() if (cancelled is not None and cancelled()) else tool_error(
            f"Georeferencing {facts['name']} failed: {str(exc)[:200]}", "EXECUTION_FAILED",
            "Check the image opens and the folder has room, then retry once.")
    finally:
        _unlink(gdal, gcp_vrt)
        _unlink(gdal, plan_vrt)
        dataset = None



        _remove(part)
    if failed is not None:
        return failed

    name = str(args.get("name") or f"{facts['name']} georeferenced")
    added = _run_on_main_thread(_add_raster, target, name, timeout=60)
    if "_error" in added:
        return added

    places = 8 if facts["geographic"] else 3
    lengths = [math.hypot(dx, dy) for dx, dy in errors]
    rmse = math.sqrt(sum(e * e for e in lengths) / len(lengths))
    residuals = [{"point": i, "dx": round(dx, places), "dy": round(dy, places), "error": round(e, places),
                  "error_px": round(e / natural, 2)}
                 for i, ((dx, dy), e) in enumerate(zip(errors, lengths), 1)]
    if accuracy is not None:
        verdicts = accuracy.pop("point_verdicts", None)
        in_metres = _metre_lengths(errors, parsed, facts) if facts.get("metres_per_unit") != 1.0 else None
        for index, row in enumerate(residuals):
            if in_metres:
                row["error_m"] = round(in_metres[index], 3)
            if verdicts:
                row["verdict"] = verdicts[index]
    shown = tuning.ceiling("georeference_residuals_shown", _RESIDUALS_SHOWN, 5)
    result = {
        "layer_name": added["layer_name"],
        "layer_id": added["layer_id"],
        "output_path": target,
        "source": facts["name"],
        "crs": facts["crs_authid"] or "the WKT given",
        "transform": transform,
        "resampling": "nearest" if paletted else resampling,
        "points": len(parsed),
        "rmse": round(rmse, places),
        "rmse_px": round(rmse / natural, 2),
        "units": facts["units"],
        "residuals": residuals if len(residuals) <= shown
        else sorted(residuals, key=lambda r: -r["error"])[:shown],
        "size_px": [cols, rows],
        "pixel_size": round(pixel_size or natural, places + 3),
        "extent": [round(v, places) for v in added["extent"]],
        "seconds": round(time.monotonic() - started, 1),
    }
    if len(residuals) > shown:
        result["residuals_note"] = f"The {shown} largest of {len(residuals)} residuals."
    if _exact(transform, len(parsed)):
        result["note"] = ("The transform passes through every control point, so the residuals are 0 by "
                          "construction and say nothing about accuracy: compare the result with a basemap, "
                          "or add points.")
    if accuracy is not None:
        result["accuracy"] = accuracy
    points_file, why_not = _write_points(facts, target, _points_text(facts, parsed, errors, source_gt, natural))
    if points_file:
        result["points_file"] = points_file
    else:
        result["points_file_note"] = f"The control points were not saved as a .points file: {why_not}."
    if paletted and resampling != "nearest":
        result["resampling_note"] = "The image is paletted, so it was resampled nearest: another method mixes colours."
    return result


__all__ = ["register_georeference_tools", "MIN_POINTS"]
