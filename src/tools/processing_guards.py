# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later












from __future__ import annotations

import math
import os
import re

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsFeatureRequest,
    QgsProcessing,
    QgsProcessingFeatureSourceDefinition,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
)

from ..core import ground, limits, tuning, vsi
from ..core.logger import log_debug, log_warning
from ..core.policy import create_managed_temp_dir
from ..core.qt_compat import enum_member
from ..core.tool_registry import tool_error
from .layer_lookup import _find_layer
from .project_tools import _is_remote_vector


def _distance_sanity(parameters: dict, confirmed: bool) -> dict | None:


    if confirmed:
        return None
    distance = parameters.get("DISTANCE")
    if isinstance(distance, bool) or not isinstance(distance, (int, float)):
        return None
    source = parameters.get("INPUT")
    layer = _find_layer(source) if isinstance(source, str) else None
    if layer is None or not hasattr(layer, "extent"):
        return None
    extent = layer.extent()
    span = max(extent.width(), extent.height()) if not extent.isEmpty() else 0.0
    if layer.crs().isValid() and layer.crs().isGeographic():
        limit, unit = 5.0, "degrees"
    else:
        limit, unit = max(100_000.0, 50.0 * span), "layer units"
    if abs(float(distance)) <= limit:
        return None




    return tool_error(
        f"DISTANCE_SUSPICIOUS: DISTANCE {distance:g} {unit} is far beyond the input layer, whose "
        f"extent spans {span:g} {unit}.", "INVALID_ARGS",
        hint="distance_suspicious", variant="point" if span == 0 else "", distance=float(distance),
        unit=unit, span=float(span))







_GRID_CELLS_MAX = 2_000_000


def _grid_cells_max() -> int:

    return tuning.ceiling("processing_grid_cells_max", _GRID_CELLS_MAX, 100_000)


_EXTENT_RE = re.compile(r"^\s*(-?[\d.eE+]+)\s*,\s*(-?[\d.eE+]+)\s*,\s*(-?[\d.eE+]+)\s*,\s*(-?[\d.eE+]+)")


def _number(value) -> float | None:

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _extent_numbers(value) -> tuple[float, float, float, float] | None:


    if isinstance(value, (list, tuple)):
        numbers = [_number(v) for v in value]
        if len(numbers) == 4 and all(n is not None for n in numbers):
            return tuple(numbers)  # type: ignore[return-value]
        return None
    if not isinstance(value, str):
        return None
    found = _EXTENT_RE.match(value)
    if not found:
        return None
    try:
        return tuple(float(n) for n in found.groups())  # type: ignore[return-value]
    except ValueError:
        return None


def _cells(width: float, height: float, hspacing: float, vspacing: float) -> float:
    if width <= 0 or height <= 0 or hspacing <= 0 or vspacing <= 0:
        return 0.0
    if not (math.isfinite(width) and math.isfinite(height)):
        return math.inf
    return math.ceil(width / hspacing) * math.ceil(height / vspacing)


def _grid_sanity(parameters: dict, confirmed: bool) -> dict | None:








    if confirmed:
        return None
    numbers = _extent_numbers(parameters.get("EXTENT"))
    if numbers is None:
        return None
    spacing = _number(parameters.get("SPACING"))
    hspacing = _number(parameters.get("HSPACING", parameters.get("SPACING")))
    vspacing = _number(parameters.get("VSPACING", parameters.get("SPACING")))
    hspacing = hspacing if hspacing is not None else spacing
    vspacing = vspacing if vspacing is not None else spacing
    if hspacing is None or vspacing is None:
        return None
    a, b, c, d = numbers
    value = parameters.get("EXTENT")
    tag = _WINDOW_CRS_RE.search(value) if isinstance(value, str) else None
    target = parameters.get("CRS") if isinstance(parameters.get("CRS"), str) else ""

    def spans(x0: float, x1: float, y0: float, y1: float) -> tuple[float, float]:




        width, height = abs(x1 - x0), abs(y1 - y0)
        if not tag or not target or tag.group(1).upper() == target.strip().upper():
            return width, height
        try:
            box = QgsCoordinateTransform(QgsCoordinateReferenceSystem(tag.group(1).upper()),
                                         QgsCoordinateReferenceSystem(target.strip()),
                                         QgsProject.instance()).transformBoundingBox(
                QgsRectangle(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
            width, height = box.width(), box.height()
        except Exception:  # noqa: BLE001
            return math.inf, math.inf
        return (width, height) if math.isfinite(width) and math.isfinite(height) else (math.inf, math.inf)


    width, height = spans(a, b, c, d)
    asked = _cells(width, height, hspacing, vspacing)
    most = _grid_cells_max()
    if asked <= most:
        return None

    alt_width, alt_height = spans(a, c, b, d)
    meant = _cells(alt_width, alt_height, hspacing, vspacing)
    swapped = 0 < meant <= most
    return tool_error(
        f"GRID_TOO_LARGE: this EXTENT and spacing build {asked:,.0f} cells, over a box spanning "
        f"{width:,.0f} by {height:,.0f}.", "INVALID_ARGS",
        hint="grid_too_large", variant="swapped" if swapped else "", cells=f"{asked:,.0f}",
        **({"alt_width": f"{alt_width:,.0f}", "alt_height": f"{alt_height:,.0f}", "meant": f"{meant:,.0f}"}
           if swapped else {}))






_REMOTE_RASTER_PIXELS_MAX = 60_000_000


def _remote_raster_pixels_max() -> int:

    return tuning.ceiling("processing_remote_raster_pixels_max", _REMOTE_RASTER_PIXELS_MAX, 1_000_000)
_RASTER_INPUT_KEYS = ("INPUT", "INPUT_RASTER", "INPUT_A", "INPUT_B", "RASTER", "GRID", "ELEVATION", "DEM")


_RASTER_LIST_KEYS = ("LAYERS", "INPUT_LAYERS", "INPUTS")




_WINDOW_KEYS = ("PROJWIN", "EXTENT", "TARGET_EXTENT", "MASK", "MASK_LAYER")






_WINDOW_ALGORITHMS = ("gdal:cliprasterbyextent", "gdal:cliprasterbymasklayer", "gdal:translate",
                      "gdal:warpreproject", "gdal:rearrange_bands", "native:cliprasterbyextent")


def _raster_ids(parameters: dict):

    for key in _RASTER_INPUT_KEYS:
        value = parameters.get(key)
        if isinstance(value, str) and value:
            yield key, value
    for key in _RASTER_LIST_KEYS:
        values = parameters.get(key)
        if isinstance(values, (list, tuple)):
            for value in values:
                if isinstance(value, str) and value:
                    yield key, value


def nice_pixel_size(value: float) -> float:

    if not math.isfinite(value) or value <= 0:
        return 0.0
    power = 10 ** math.floor(math.log10(value))
    for step in (1.0, 2.0, 2.5, 5.0, 10.0):
        if value <= step * power * (1 + 1e-9):
            return float(f"{step * power:.6g}")
    return float(f"{10 * power:.6g}")


def fitting_pixel_size(width: float, height: float, budget: float) -> float:

    if width <= 0 or height <= 0 or budget <= 0:
        return 0.0
    size = nice_pixel_size(math.sqrt(width * height / budget))

    while size > 0 and math.ceil(width / size) * math.ceil(height / size) > budget:
        size = nice_pixel_size(size * 1.01)
    return size


def _units_word(crs) -> str:
    try:
        return "degrees" if crs.isGeographic() else "metres" if _metric(crs) else "map units"
    except Exception:  # noqa: BLE001
        return "map units"


def _metric(crs) -> bool:
    try:
        from qgis.core import Qgis

        return crs.mapUnits() == Qgis.DistanceUnit.Meters
    except Exception:  # noqa: BLE001
        return False






_RESAMPLE_ALGORITHMS = ("gdal:translate", "gdal:warpreproject")
_EXTRA_SIZE_RE = re.compile(r"-(tr|ts|outsize)\s+([-+\d.eE]+%?)\s+([-+\d.eE]+%?)")


def _target_extent(layer, parameters: dict):

    extent = layer.extent()
    target = parameters.get("TARGET_CRS")
    target_id = target if isinstance(target, str) else getattr(target, "authid", lambda: "")()
    if not target_id or str(target_id).strip().upper() == layer.crs().authid().upper():
        return extent.width(), extent.height()
    box = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem(str(target_id).strip()),
                                 QgsProject.instance()).transformBoundingBox(extent)
    return box.width(), box.height()


def _resampled_pixels(layer, algorithm_id: str, parameters: dict) -> float | None:

    if algorithm_id not in _RESAMPLE_ALGORITHMS:
        return None
    try:
        width, height = (_target_extent(layer, parameters) if algorithm_id == "gdal:warpreproject"
                         else (layer.extent().width(), layer.extent().height()))
    except Exception:  # noqa: BLE001
        return None
    resolution = parameters.get("TARGET_RESOLUTION") if algorithm_id == "gdal:warpreproject" else None
    if isinstance(resolution, (int, float)) and not isinstance(resolution, bool) and resolution > 0:
        return math.ceil(width / resolution) * math.ceil(height / resolution)
    extra = parameters.get("EXTRA") if isinstance(parameters.get("EXTRA"), str) else ""
    found = _EXTRA_SIZE_RE.search(extra or "")
    if not found:
        return None
    flag, first, second = found.groups()
    try:
        if flag == "tr":
            xres, yres = abs(float(first)), abs(float(second))
            return math.ceil(width / xres) * math.ceil(height / yres) if xres and yres else None
        cols, rows = int(layer.width()), int(layer.height())

        def size(text: str, whole: int) -> float:
            return whole * float(text[:-1]) / 100.0 if text.endswith("%") else float(text)

        out_x, out_y = size(first, cols), size(second, rows)

        if out_x <= 0 < out_y:
            out_x = out_y * cols / max(rows, 1)
        if out_y <= 0 < out_x:
            out_y = out_x * rows / max(cols, 1)
        return out_x * out_y if out_x > 0 and out_y > 0 else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _has_overviews(layer) -> bool:
    try:
        return bool(layer.dataProvider().hasPyramids())
    except Exception:  # noqa: BLE001
        return True


def _zone_of(layer, parameters: dict):





    extent = layer.extent()
    for key in ("EXTENT", "PROJWIN", "TARGET_EXTENT"):
        value = parameters.get(key)
        if not isinstance(value, str):
            continue
        tag = _WINDOW_CRS_RE.search(value)
        if tag and tag.group(1).upper() != layer.crs().authid().upper():
            continue
        numbers = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", value.split("[")[0])]
        if len(numbers) == 4:
            width, height = abs(numbers[1] - numbers[0]), abs(numbers[3] - numbers[2])
            if width > 0 and height > 0:
                return width, height, f"the {key} window"
    return extent.width(), extent.height(), "the whole raster"


def _resample_facts(layer, parameters: dict, budget: float, algorithm_id: str = "") -> tuple[str, dict]:



    try:
        width, height, words = _zone_of(layer, parameters)
        size = fitting_pixel_size(width, height, budget)
        native = float(layer.rasterUnitsPerPixelX())
    except Exception:  # noqa: BLE001
        return "", {}
    if size <= 0:
        return "", {}
    source = f" (the source is {_plain(native) if native >= 1e-6 else f'{native:g}'})" if native > 0 else ""
    fits = (f" {words[:1].upper() + words[1:]} fits at a pixel size of {size:g} {_units_word(layer.crs())}"
            f"{source}: {math.ceil(width / size):,} by {math.ceil(height / size):,} pixels, under the "
            f"{budget / 1e6:,.0f} million a streamed call reads.")
    if not _has_overviews(layer):
        return fits + " This file has no overviews.", {"variant": "no_overviews", "size": float(f"{size:g}")}
    variant = {"gdal:warpreproject": "warp", "gdal:translate": "translate"}.get(
        algorithm_id, "resample" if words == "the whole raster" else "resample_window")
    return fits, {"variant": variant, "size": float(f"{size:g}")}


def _raster_size_sanity(parameters: dict, confirmed: bool, algorithm_id: str = "") -> dict | None:











    windowed = any(parameters.get(key) for key in _WINDOW_KEYS)


    if confirmed or (windowed and (not algorithm_id or algorithm_id in _WINDOW_ALGORITHMS)):
        return None
    project = QgsProject.instance()
    for key, value in _raster_ids(parameters):
        layer = project.mapLayer(value)
        if layer is None or not isinstance(layer, QgsRasterLayer):
            continue
        source = str(layer.source() or "")
        if "/vsicurl" not in source and not source.startswith(("http://", "https://")):
            continue
        pixels = int(layer.width()) * int(layer.height())
        most = _remote_raster_pixels_max()
        if pixels <= most:
            continue
        written = _resampled_pixels(layer, algorithm_id, parameters)
        if written is not None and written <= most and _has_overviews(layer):
            continue
        coarser = ""
        if written is not None and written > most:
            coarser = f" This call would write {written / 1e6:,.0f} million."
        fits, facts = _resample_facts(layer, parameters, most, algorithm_id)
        return tool_error(
            f"RASTER_TOO_LARGE: {key} is a remote raster of {pixels / 1e6:,.0f} million pixels "
            f"({layer.width():,} by {layer.height():,}); every derivative streams the whole file and "
            f"takes minutes.{coarser}{fits}", "INVALID_ARGS", hint="raster_too_large", key=key, **facts)
    return None










_EXTERNAL_PROVIDERS = frozenset({"grass", "grass7", "saga", "sagang", "otb"})



_STREAMED_COPY_MAX_BYTES = 32 * 1024 * 1024
_STREAMED_COPIES: dict[str, str] = {}


def _is_streamed(layer) -> bool:
    source = str(layer.source() or "")
    return any(prefix in source for prefix in vsi.STREAMED_PREFIXES) or source.startswith(("http://", "https://"))


def _sample_bytes(layer) -> int:
    try:
        from qgis.core import QgsRasterBlock

        size = int(QgsRasterBlock.typeSize(layer.dataProvider().dataType(1)))
        return size if size > 0 else 8
    except Exception:  # noqa: BLE001
        return 8


def _local_copy(layer, algorithm_id: str, key: str) -> dict:

    source = str(layer.source() or "")
    cached = _STREAMED_COPIES.get(source)
    if cached and os.path.exists(cached):
        return {"path": cached, "megabytes": os.path.getsize(cached) / 1e6}
    raw = int(layer.width()) * int(layer.height()) * max(1, int(layer.bandCount())) * _sample_bytes(layer)
    cap = min(
        tuning.ceiling("processing_streamed_copy_max_bytes", _STREAMED_COPY_MAX_BYTES, 1024 * 1024),
        int(limits.current("MAX_DOWNLOAD_BYTES")),
    )
    provider = algorithm_id.split(":", 1)[0]


    if raw > cap:
        return {
            "_error": (f"{key} is {layer.name()}, streamed over HTTP, and {provider} only reads files; a local copy "
                       f"would be {raw / 1e6:,.0f} MB, over the {cap / 1e6:,.0f} MB copied before a call."),
            "code": "INVALID_ARGS",
        }
    try:
        from osgeo import gdal
    except ImportError:
        return {"_error": "GDAL is missing, so the streamed raster cannot be copied.", "code": "EXECUTION_FAILED"}
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", layer.name())[:60] or "raster"
    path = os.path.join(create_managed_temp_dir("streamed_inputs"), f"{stem}.tif")
    reason = ""
    try:
        with vsi.scoped_read(gdal):
            dataset = gdal.Translate(path, source, format="GTiff",
                                     creationOptions=["COMPRESS=DEFLATE", "TILED=YES", "BIGTIFF=IF_SAFER"])
            copied = dataset is not None
            del dataset
    except Exception as exc:  # noqa: BLE001
        copied, reason = False, " ".join(str(exc).split())[:200]
    if not copied or not os.path.exists(path):
        return {"_error": f"{layer.name()} could not be copied from where it is streamed. {reason}".strip(),
                "code": "EXECUTION_FAILED"}
    _STREAMED_COPIES[source] = path
    return {"path": path, "megabytes": os.path.getsize(path) / 1e6}


def localise_streamed_rasters(algorithm_id: str, parameters: dict) -> tuple[dict, list[str], dict | None]:





    provider = str(algorithm_id or "").split(":", 1)[0].lower()
    if provider not in _EXTERNAL_PROVIDERS:
        return parameters, [], None
    project = QgsProject.instance()
    out, notes = dict(parameters), []
    for key, value in parameters.items():
        items = list(value) if isinstance(value, (list, tuple)) else [value]
        changed = False
        for index, item in enumerate(items):
            layer = item if isinstance(item, QgsRasterLayer) else (
                project.mapLayer(item) if isinstance(item, str) and item else None)
            if not isinstance(layer, QgsRasterLayer) or not _is_streamed(layer):
                continue
            copied = _local_copy(layer, algorithm_id, key)
            if "_error" in copied:
                return parameters, [], copied
            items[index] = copied["path"]
            changed = True
            notes.append(f"{key}: {layer.name()} is streamed over HTTP and {provider} reads files, so it ran on a "
                         f"local copy ({copied['megabytes']:.1f} MB)")
        if changed:
            out[key] = items if isinstance(value, (list, tuple)) else items[0]
    return out, notes, None








_WINDOW_RATIO_MAX = 4.0
_WINDOW_CRS_RE = re.compile(r"\[\s*(EPSG:\d+)\s*\]", re.IGNORECASE)


def _plain(value: float) -> str:

    return f"{value:.6f}".rstrip("0").rstrip(".")


def _raster_input(parameters: dict):
    project = QgsProject.instance()
    for _key, value in _raster_ids(parameters):
        layer = project.mapLayer(value)
        if isinstance(layer, QgsRasterLayer):
            return layer
    return None


def _window_degrees_repair(parameters: dict) -> list[str]:









    notes: list[str] = []
    for key in ("PROJWIN", "EXTENT", "TARGET_EXTENT"):
        value = parameters.get(key)
        numbers = _extent_numbers(value)
        if numbers is None or (isinstance(value, str) and _WINDOW_CRS_RE.search(value)):
            continue
        if not all(abs(n) <= 180 for n in numbers):
            continue
        layer = _raster_input(parameters)
        if layer is None or layer.extent().isEmpty():
            continue
        crs = layer.crs()
        if not crs.isValid() or crs.isGeographic():
            continue
        try:
            transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), crs,
                                               QgsProject.instance())
        except Exception:  # nosec B112
            continue
        a, b, c, d = numbers
        extent = layer.extent()
        readings = ((min(a, b), max(a, b), min(c, d), max(c, d)),
                    (min(a, c), max(a, c), min(b, d), max(b, d)))
        for xmin, xmax, ymin, ymax in readings:
            if abs(ymin) > 90 or abs(ymax) > 90:
                continue
            try:
                rect = transform.transformBoundingBox(QgsRectangle(xmin, ymin, xmax, ymax))
            except Exception:  # nosec B112
                continue
            ratio_max = tuning.threshold("processing_window_ratio_max", _WINDOW_RATIO_MAX, 1.5, 10.0)
            if rect.intersects(extent) and rect.area() <= ratio_max * extent.area():
                parameters[key] = f"{_plain(xmin)},{_plain(xmax)},{_plain(ymin)},{_plain(ymax)} [EPSG:4326]"
                notes.append(f"{key} was four degrees with no CRS: written as xmin,xmax,ymin,ymax [EPSG:4326] "
                             f"for a raster in {crs.authid()}")
                break
    return notes


def _window_readings(value, layer):




    numbers = _extent_numbers(value)
    if numbers is None:
        return None
    a, b, c, d = numbers
    rect = QgsRectangle(min(a, b), min(c, d), max(a, b), max(c, d))
    alt = QgsRectangle(min(a, c), min(b, d), max(a, c), max(b, d))
    tag = _WINDOW_CRS_RE.search(value) if isinstance(value, str) else None
    crs_text = f" [{tag.group(1).upper()}]" if tag else ""
    if tag and tag.group(1).upper() != layer.crs().authid().upper():
        try:
            transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem(tag.group(1).upper()),
                                               layer.crs(), QgsProject.instance())
            rect = transform.transformBoundingBox(rect)
            alt = transform.transformBoundingBox(alt)
        except Exception:  # noqa: BLE001
            return None
    extent = layer.extent()

    def fits(box):
        ratio_max = tuning.threshold("processing_window_ratio_max", _WINDOW_RATIO_MAX, 1.5, 10.0)
        return box.intersects(extent) and box.area() <= ratio_max * extent.area()

    return rect, alt, crs_text, fits(rect), fits(alt)








_GROUND_METHOD_ALGORITHMS = {"exportaddgeometrycolumns": ("METHOD", "CALC_METHOD")}
_ELLIPSOIDAL_METHOD = 2


def _ground_measure_repair(alg, parameters: dict) -> list[str]:

    algorithm_id = alg.id() if alg is not None else ""
    names = _GROUND_METHOD_ALGORITHMS.get(algorithm_id.split(":")[-1].lower())
    if not names:
        return []
    declared = {definition.name() for definition in alg.parameterDefinitions()}
    key = next((name for name in names if name in declared), None)
    if key is None or parameters.get(key, 0) not in (0, "0"):
        return []
    source = parameters.get("INPUT")
    layer = _find_layer(source) if isinstance(source, str) else source
    if layer is None or not hasattr(layer, "crs") or not layer.crs().isValid():
        return []
    crs = layer.crs()
    if crs.isGeographic():
        reason = f"is in {crs.authid()}, whose units are degrees"
    else:
        scale = ground.layer_metres_per_unit(layer)
        if not ground.distorted(scale):
            return []
        reason = f"is in {crs.authid()}, where one unit is {scale:.3g} m of ground"
    parameters[key] = _ELLIPSOIDAL_METHOD
    return [f"{key} set to {_ELLIPSOIDAL_METHOD} (ellipsoidal): layer '{layer.name()}' {reason}, so its "
            f"area and length fields are in ground square metres and metres"]


def _window_order_repair(parameters: dict) -> list[str]:





    notes: list[str] = []
    for key in ("PROJWIN", "EXTENT", "TARGET_EXTENT"):
        value = parameters.get(key)
        layer = _raster_input(parameters)
        if layer is None or layer.extent().isEmpty():
            return notes
        readings = _window_readings(value, layer)
        if readings is None:
            continue
        _rect, _alt, crs_text, fits, alt_fits = readings
        if fits or not alt_fits:
            continue
        a, b, c, d = _extent_numbers(value)
        parameters[key] = f"{_plain(a)},{_plain(c)},{_plain(b)},{_plain(d)}{crs_text}"
        notes.append(f"{key} was xmin,ymin,xmax,ymax: rewritten as xmin,xmax,ymin,ymax, the order Processing reads")
    return notes


def _window_sanity(parameters: dict) -> dict | None:






    for key in ("PROJWIN", "EXTENT", "TARGET_EXTENT"):
        value = parameters.get(key)
        if _extent_numbers(value) is None:
            continue
        layer = _raster_input(parameters)
        if layer is None or layer.extent().isEmpty():
            return None
        readings = _window_readings(value, layer)
        if readings is None:
            return None
        rect, _alt, _crs_text, fits, _alt_fits = readings
        if fits:
            return None
        extent = layer.extent()
        problem = ("does not overlap" if not rect.intersects(extent)
                   else f"is {rect.area() / extent.area():,.0f} times the size of")
        return tool_error(
            f"WINDOW_OFF_RASTER: {key} spans {rect.width():,.6g} by {rect.height():,.6g} in the "
            f"raster's CRS and {problem} {layer.name()} (extent {extent.xMinimum():,.6g}, "
            f"{extent.yMinimum():,.6g} to {extent.xMaximum():,.6g}, {extent.yMaximum():,.6g}).",
            "INVALID_ARGS", hint="window_off_raster", key=key)
    return None









_FORMAT_FLAGS = ("-f", "-of")
_GDAL_OPTION_KEYS = ("OPTIONS", "EXTRA")


_OPTION_TOKEN_RE = re.compile(r"""(?:[^\s"']+|"[^"]*"|'[^']*')+""")


def _strip_format_flags(value: str) -> tuple[str, list[str]]:

    tokens = _OPTION_TOKEN_RE.findall(value)
    kept: list[str] = []
    removed: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            removed[-1] += f" {token}"
            skip_next = False
            continue
        if token in _FORMAT_FLAGS:
            removed.append(token)
            skip_next = True
            continue
        kept.append(token)
    return " ".join(kept), removed


def _gdal_format_options(algorithm_id: str, parameters: dict) -> list[str]:





    if not str(algorithm_id or "").startswith("gdal:"):
        return []
    repairs: list[str] = []
    for key in _GDAL_OPTION_KEYS:
        value = parameters.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        cleaned, removed = _strip_format_flags(value)
        if not removed:
            continue
        parameters[key] = cleaned
        repairs.append(
            f"{key}: dropped {', '.join(repr(r) for r in removed)}. Processing writes the format "
            f"itself from the OUTPUT extension, and a second one fails the run with "
            '"Duplicate argument". The OUTPUT extension sets the format.'
        )
    return repairs


_INPUT_KEYS = ("INPUT", "INPUT_LAYER", "LAYER", "SOURCE", "INPUT_VECTOR")



_NOT_A_LAYER_NAME = ("|", "://", "/", "\\", "memory:")
_OUTPUT_SENTINELS = ("TEMPORARY_OUTPUT", "memory:")


def _resolve_layer_inputs(parameters: dict, alg=None) -> tuple[dict, list[str]]:









    resolved = dict(parameters)
    rewritten: list[str] = []
    for key in _INPUT_KEYS:
        source = parameters.get(key)
        if not isinstance(source, str) or not source.strip() or not _takes_a_layer(alg, key):
            continue
        value = source.strip()
        if value in _OUTPUT_SENTINELS or any(mark in value for mark in _NOT_A_LAYER_NAME):
            continue
        if os.path.exists(value):
            continue
        layer = _find_layer(value)
        if layer is None:
            continue
        try:
            identifier = layer.id()
        except (AttributeError, RuntimeError):
            continue
        if identifier and identifier != value:
            resolved[key] = identifier
            rewritten.append(key)
    for key in sorted(_layer_list_keys(alg)):
        items = parameters.get(key)
        if not isinstance(items, list):
            continue
        swapped = [_layer_id_for(item) for item in items]
        if swapped != items:
            resolved[key] = swapped
            rewritten.append(key)
    return resolved, rewritten


def _names_a_layer(value) -> bool:

    if not isinstance(value, str) or not value.strip():
        return False
    value = value.strip()
    if value in _OUTPUT_SENTINELS or any(mark in value for mark in _NOT_A_LAYER_NAME):
        return False
    return not os.path.exists(value)


def _layer_id_for(item):

    if not _names_a_layer(item):
        return item
    layer = _find_layer(item.strip())
    try:
        return layer.id() if layer is not None and layer.id() else item
    except (AttributeError, RuntimeError):
        return item


def _layer_list_keys(alg) -> set:

    keys = set()
    try:
        definitions = alg.parameterDefinitions() if alg is not None else []
    except Exception:  # noqa: BLE001
        return keys
    for definition in definitions:
        try:
            if definition.type() == "multilayer":
                keys.add(definition.name())
        except Exception:  # nosec B112
            continue
    return keys


def _takes_rasters(definition) -> bool:

    try:
        wanted = (enum_member(Qgis, "ProcessingSourceType", "Raster", None)
                  or enum_member(QgsProcessing, "SourceType", "TypeRaster", None))
        return wanted is not None and definition.layerType() == wanted
    except Exception:  # noqa: BLE001
        return False


def streamable_path(text) -> str | None:









    text = text.strip() if isinstance(text, str) else ""
    if not text.lower().startswith(("http://", "https://")):
        return None
    listed = tuple(ext for ext in vsi.VSICURL_ALLOWED_EXTENSIONS if ext != "{noext}")
    head = text.split("?", 1)[0]
    opens = head.lower().endswith(listed) or (
        "{noext}" in vsi.VSICURL_ALLOWED_EXTENSIONS and "." not in head.rsplit("/", 1)[-1])
    return "/vsicurl/" + text if opens else None


def streamed_raster_addresses(alg, parameters: dict, opened=()) -> tuple[dict, list[str]]:




















    out = dict(parameters)
    repairs: list[str] = []
    try:
        definitions = list(alg.parameterDefinitions()) if alg is not None else []
    except Exception:  # noqa: BLE001
        return out, repairs

    def streamed(item):
        path = streamable_path(item)
        return path if path is not None and path in opened else item

    for definition in definitions:
        try:
            kind, name = definition.type(), definition.name()
        except Exception:  # nosec B112
            continue
        value = parameters.get(name)
        if kind == "raster" and isinstance(value, str):
            swapped = streamed(value)
        elif kind == "multilayer" and isinstance(value, list) and _takes_rasters(definition):
            swapped = [streamed(item) for item in value]
        else:
            continue
        if swapped != value:
            out[name] = swapped
            repairs.append(f"{name}: a web address read as a /vsicurl/ path. Handed to Processing as it was, an "
                           "address is downloaded whole on QGIS's main thread before the run; /vsicurl/ reads only "
                           "the parts the algorithm asks for.")
    return out, repairs





_LAYER_PARAMETER_TYPES = frozenset({"source", "vector", "raster", "layer", "mesh", "pointcloud", "annotation"})


def _takes_a_layer(alg, key: str) -> bool:

    try:
        definition = alg.parameterDefinition(key) if alg is not None else None
        return definition is None or definition.type() in _LAYER_PARAMETER_TYPES
    except Exception:  # noqa: BLE001
        return True







_SOURCE_KEYS = ("source", "layer", "layer_name")
_SOURCE_OPTIONS = ("selected_only", "expression")


EMPTY_SELECTION_MARK = "has no selected features"


def selected_sources(alg, parameters: dict) -> tuple[dict, dict, dict | None]:












    plain = dict(parameters)
    selected: dict = {}
    algorithm = alg.id() if alg is not None else "this algorithm"
    for key, value in parameters.items():
        if not isinstance(value, dict) or not any(name in value for name in _SOURCE_KEYS):
            continue
        source = next((value[name] for name in _SOURCE_KEYS if name in value), None)
        unknown = sorted(set(value) - set(_SOURCE_KEYS) - set(_SOURCE_OPTIONS))
        expression = value.get("expression")
        definition = alg.parameterDefinition(key) if alg is not None else None
        kind = _type_of(definition) if definition is not None else ""
        if (unknown or not isinstance(source, str) or not source.strip()
                or not (expression is None or isinstance(expression, str))):
            return plain, selected, {
                "_error": (f"{key} of {algorithm} is an object, and the only form it reads is "
                           f'{{"source": "<layer name or id>", "selected_only": true, '
                           f'"expression": "<QGIS expression>"}}, both options optional. '
                           + (f"It does not read {', '.join(unknown)}. " if unknown else "")
                           + "Nothing was run."),
                "code": "INVALID_ARGS",
                "suggestion": f"{key} takes the layer name, or source, selected_only and expression only.",
            }
        plain[key] = source.strip()
        expression = (expression or "").strip()
        only_selected = _truthy(value.get("selected_only"))
        if definition is None or not (only_selected or expression):

            continue
        if kind != "source":
            return plain, selected, {
                "_error": (f"{key} of {algorithm} is a {kind or 'non-vector'} input, and only a vector feature "
                           f"source reads a selection or an expression (QGIS's 'Selected features only' box). "
                           f"Nothing was run."),
                "code": "INVALID_ARGS",
                "suggestion": ("export_layer with selected_only true, or "
                               f"native:extractbyexpression, writes those features to a file usable as {key}."),
            }
        layer = None if any(mark in source for mark in _NOT_A_LAYER_NAME) else _find_layer(source.strip())
        if layer is None or not hasattr(layer, "selectedFeatureCount"):
            if layer is None and not any(mark in source for mark in _NOT_A_LAYER_NAME):
                return plain, selected, _no_such_layers(alg, key, [source.strip()])
            return plain, selected, {
                "_error": (f"{key} names {source.strip()!r}, which is not a vector layer of this project, and "
                           f"only a project layer holds a selection or takes an expression here. Nothing was run."),
                "code": "INVALID_ARGS",
                "suggestion": "add_data loads it as a project layer this call can then name.",
            }
        try:
            total = -1 if _is_remote_vector(layer) else int(layer.featureCount())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            total = -1
        count, refused = _subset_count(layer, key, only_selected, expression)
        if refused:
            return plain, selected, refused
        plain[key] = layer.id()
        selected[key] = (layer.id(), count, total, layer.name(), only_selected, expression)
    return plain, selected, None


def _subset_count(layer, key: str, only_selected: bool, expression: str) -> tuple[int, dict | None]:

    try:
        selection = int(layer.selectedFeatureCount()) if only_selected else -1
    except (AttributeError, RuntimeError, TypeError, ValueError):
        selection = 0
    if only_selected and selection <= 0:
        return 0, {
            "_error": (f"Layer '{layer.name()}' {EMPTY_SELECTION_MARK}, so {key} with selected_only would "
                       f"read nothing. Nothing was run."),
            "code": "INVALID_ARGS",
            "suggestion": ("select_by_attribute, select_features or "
                           "select_by_geometry make a selection; selected_only off uses "
                           "the whole layer instead."),
        }
    if not expression:
        return selection, None
    parsed = QgsExpression(expression)
    if parsed.hasParserError():
        return 0, {
            "_error": (f"The expression of {key} does not parse: "
                       f"{' '.join(parsed.parserErrorString().split())}. Nothing was run."),
            "code": "INVALID_ARGS",
            "suggestion": "Field names take double quotes, text takes single quotes: \"name\" = 'value'.",
        }
    fields = [field.name() for field in layer.fields()]
    missing = sorted(set(parsed.referencedColumns()) - set(fields) - {QgsFeatureRequest.ALL_ATTRIBUTES})
    if missing:
        return 0, {
            "_error": (f"The expression of {key} names {', '.join(missing)}, which '{layer.name()}' does not "
                       f"have. Nothing was run."),
            "code": "INVALID_ARGS",
            "suggestion": f"The fields of '{layer.name()}' are {', '.join(fields[:30])}.",
        }
    if _is_remote_vector(layer):

        return -1, None
    context = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    request = QgsFeatureRequest(parsed, context)
    if not parsed.needsGeometry():
        request.setFlags(QgsFeatureRequest.Flag.NoGeometry)
    if only_selected:
        request.setFilterFids(layer.selectedFeatureIds())


    request.setLimit(1)
    if next(iter(layer.getFeatures(request)), None) is None:
        return 0, {
            "_error": (f"No feature of '{layer.name()}'" + (" selected" if only_selected else "")
                       + f" matches {expression}, so {key} would read nothing. Nothing was run."),
            "code": "INVALID_ARGS",
            "suggestion": (f"get_features or get_field_statistics give the values on '{layer.name()}'."),
        }
    return -1, None


def source_definition(layer_id: str, only_selected: bool, expression: str):





    definition = QgsProcessingFeatureSourceDefinition(layer_id, bool(only_selected))
    if not expression:
        return definition
    flag = getattr(getattr(Qgis, "ProcessingFeatureSourceDefinitionFlag", None), "FilterExpression", None)
    if flag is not None and hasattr(definition, "filterExpression"):
        definition.flags = definition.flags | flag
        definition.filterExpression = expression
        return definition
    layer = QgsProject.instance().mapLayer(layer_id)
    request = QgsFeatureRequest().setFilterExpression(expression)
    if only_selected:
        request.setFilterFids(layer.selectedFeatureIds())
    return layer.materialize(request)


def _truthy(value) -> bool:
    return value is True or str(value).strip().casefold() in ("true", "1", "yes")


def _unresolved_input_check(alg, parameters: dict) -> dict | None:










    for key in _INPUT_KEYS:
        source = parameters.get(key)
        if not isinstance(source, str) or not source.strip() or not _takes_a_layer(alg, key):
            continue
        value = source.strip()
        if value in _OUTPUT_SENTINELS or any(mark in value for mark in _NOT_A_LAYER_NAME):
            continue
        if os.path.exists(value) or _find_layer(value) is not None:
            continue
        return _no_such_layers(alg, key, [value])



    for key in sorted(_layer_list_keys(alg)):
        items = parameters.get(key)
        if not isinstance(items, list):
            continue
        missing = [item.strip() for item in items if _names_a_layer(item) and _find_layer(item.strip()) is None]
        if missing:
            return _no_such_layers(alg, key, missing)
    return None


def _no_such_layers(alg, key: str, missing: list) -> dict:

    names = [layer.name() for layer in QgsProject.instance().mapLayers().values()][:20]
    quoted = ", ".join(repr(value) for value in missing)
    return {
        "_error": (f"No layer in this project is called {quoted} ({key}), so "
                   f"{alg.id() if alg is not None else 'this algorithm'} has nothing to read."),
        "code": "INVALID_ARGS",
        "layers": names,
        "suggestion": "A name above, or the layer_id the tool that made the layer returned, "
                      "names it. list_layers gives both.",
    }










PARAMETER_MARK = "PARAMETER_INVALID:"


_NUMERIC_TYPES = frozenset({"number", "distance", "area", "volume", "duration", "scale"})
_SKIPPED_TYPES = frozenset({"matrix", "range", "aggregates", "fieldmapping", "tininputlayers",
                            "vectortilewriterlayers", "dxflayers", "meshdatasetgroups",
                            "meshdatasettime", "alignrasterlayers", "rasterdemparameters"})


def _definitions(alg):
    try:
        return list(alg.parameterDefinitions()) if alg is not None else []
    except Exception:  # noqa: BLE001
        return []


def _type_of(definition) -> str:
    try:
        return str(definition.type() or "").casefold()
    except Exception:  # noqa: BLE001
        return ""


def _enum_problem(definition, value) -> str:

    try:
        options = [str(option) for option in definition.options()]
    except (AttributeError, TypeError, RuntimeError):
        return ""
    if not options:
        return ""
    listed = ", ".join(f"{index}={option}" for index, option in enumerate(options))
    for item in (value if isinstance(value, (list, tuple)) else [value]):
        if isinstance(item, bool) or item is None:
            continue
        if isinstance(item, str) and not item.strip().lstrip("-").isdigit():
            wanted = item.strip().casefold()
            match = next((index for index, option in enumerate(options)
                          if str(option).casefold() == wanted), None)
            if match is not None:
                return (f"is the label '{item}', and an enum takes the integer beside it: send {match}. "
                        f"Choices: {listed}")
            return f"is '{item}', which is not one of its choices. Choices: {listed}"
        try:
            index = int(item)
        except (TypeError, ValueError):
            continue
        if index < 0 or index >= len(options):
            return f"is {index}, outside its choices. Choices: {listed}"
    return ""


def _parameter_sanity(alg, parameters: dict) -> dict | None:

    definitions = _definitions(alg)
    if not definitions:
        return None
    by_name = {definition.name(): definition for definition in definitions}
    algorithm = alg.id() if alg is not None else "this algorithm"




    import difflib
    for key in parameters:
        if key in by_name:
            continue
        near = difflib.get_close_matches(str(key), list(by_name), n=1, cutoff=0.75)
        if not near:




            folded = str(key).casefold()
            holders = [name for name in by_name
                       if folded and folded != name.casefold() and folded in name.casefold()]
            near = holders if len(holders) == 1 else []
        if near:
            definition = by_name[near[0]]
            return {
                "_error": (f"{PARAMETER_MARK} {algorithm} has no parameter '{key}'. Its parameter "
                           f"'{near[0]}' ({_type_of(definition) or 'value'}) is the one this spelling means. "
                           f"Nothing was run."),
                "code": "INVALID_ARGS",
                "parameters": [f"{d.name()} ({_type_of(d) or 'value'})" for d in definitions],
                "suggestion": f"'{near[0]}' is the parameter; '{key}' is not. Full list above.",
            }



    from .processing_destinations import _is_optional

    absent = []
    for definition in definitions:
        name = definition.name()
        if name in parameters and parameters[name] not in (None, ""):
            continue
        if getattr(definition, "isDestination", lambda: False)():
            continue
        try:
            if _is_optional(definition) or definition.defaultValue() is not None:
                continue
        except Exception as exc:  # noqa: BLE001
            log_debug(f"required parameter check: {name!r} could not say if it is optional: {exc}")
            continue
        absent.append(f"{name} ({_type_of(definition) or 'value'})")
    if absent:
        return {
            "_error": (f"{PARAMETER_MARK} {algorithm} needs {', '.join(absent)}, and the call gives "
                       f"no value for {'them' if len(absent) > 1 else 'it'}. Nothing was run."),
            "code": "INVALID_ARGS",
            "parameters": [f"{d.name()} ({_type_of(d) or 'value'})" for d in definitions],
            "suggestion": ", ".join(absent) + " needs a value; brackets above name the type.",
        }



    for name, value in parameters.items():
        definition = by_name.get(name)
        if definition is None or value is None:
            continue
        kind = _type_of(definition)
        if kind in _SKIPPED_TYPES:
            continue
        if kind == "enum":
            problem = _enum_problem(definition, value)
            if problem:
                return {
                    "_error": f"{PARAMETER_MARK} {algorithm} parameter '{name}' {problem}. Nothing was run.",
                    "code": "INVALID_ARGS",
                    "suggestion": f"'{name}' takes the integer from the choices above.",
                }
            continue
        if kind in _NUMERIC_TYPES and isinstance(value, str) and value.strip():
            try:
                float(value.strip())
            except ValueError:
                return {
                    "_error": (f"{PARAMETER_MARK} {algorithm} parameter '{name}' is a {kind} and the call "
                               f"sends the text '{value}'. Nothing was run."),
                    "code": "INVALID_ARGS",
                    "suggestion": f"'{name}' is a number, in the units of the input layer's CRS.",
                }
    return None


def ignored_parameters(alg, parameters: dict) -> list:






    definitions = _definitions(alg)
    if not definitions:
        return []
    names = {definition.name() for definition in definitions}
    return [f"{key} is not a parameter of {alg.id()} and was ignored"
            for key in parameters if key not in names]


def _empty_input_check(alg, parameters: dict) -> dict | None:









    for key in ("INPUT", "INPUT_LAYER", "LAYER", "SOURCE", "INPUT_VECTOR"):
        source = parameters.get(key)
        if isinstance(source, str) and any(mark in source for mark in _NOT_A_LAYER_NAME):


            layer = QgsProject.instance().mapLayer(source)
        else:
            layer = _find_layer(source) if isinstance(source, str) else source
        if layer is None or not hasattr(layer, "featureCount"):
            continue
        if _is_remote_vector(layer):


            continue
        try:
            count = int(layer.featureCount())
        except Exception:  # nosec B112
            continue
        if count != 0:
            return None
        return {
            "_error": (
                f"Layer '{layer.name()}' has no features, so {alg.id() if alg is not None else 'this algorithm'} "
                f"would produce an empty layer."
            ),
            "code": "INVALID_ARGS",
            "suggestion": (
                "A filter or selection on it may be hiding the features, or it is the wrong layer."
            ),
        }
    return None


_DISTANCE_NAMES = ("DISTANCE", "RADIUS", "TOLERANCE", "BUFFER", "MAX_DISTANCE", "SEARCH_DISTANCE",
                   "SNAP_TOLERANCE", "SPACING", "INTERVAL", "CELLSIZE", "PIXEL_SIZE")




_PERCENTAGE_PARAMETERS = frozenset({("native:voronoipolygons", "BUFFER")})

_DEGREES_ALLOWED = 0.01









def _utm_authid(lon: float, lat: float) -> str:

    lon = max(-180.0, min(180.0, float(lon)))
    zone = min(60, int((lon + 180.0) // 6.0) + 1)
    return f"EPSG:{32600 + zone}" if float(lat) >= 0 else f"EPSG:{32700 + zone}"


def _centre_lonlat(layer) -> tuple[float, float] | None:

    try:
        extent = layer.extent()
        if extent.isNull():
            return None
        centre = extent.center()
        crs = layer.crs()
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        if not crs.isValid() or crs == wgs84:
            return centre.x(), centre.y()
        moved = QgsCoordinateTransform(crs, wgs84, QgsProject.instance()).transform(centre)
        return moved.x(), moved.y()
    except Exception:  # noqa: BLE001
        return None


def _suggest_metric_crs(layer) -> str:










    project_crs = QgsProject.instance().crs()
    if project_crs.isValid() and not project_crs.isGeographic():
        scale = ground.layer_metres_per_unit(_ProjectCrsProbe(project_crs, layer))
        if scale is None or abs(scale - 1.0) <= ground.TOLERANCE:
            return project_crs.authid()
    centre = _centre_lonlat(layer)
    if centre is None:
        return "EPSG:3857"
    return _utm_authid(centre[0], centre[1])


class _ProjectCrsProbe:







    def __init__(self, crs, layer):
        self._crs = crs
        self._layer = layer

    def crs(self):
        return self._crs

    def extent(self):
        source = self._layer.crs()
        extent = self._layer.extent()
        if not source.isValid() or source == self._crs:
            return extent
        return QgsCoordinateTransform(source, self._crs, QgsProject.instance()).transformBoundingBox(extent)


def _geographic_distance_check(alg, parameters: dict, confirmed: bool) -> dict | None:








    if confirmed:
        return None
    for definition in alg.parameterDefinitions():
        name = definition.name()
        if (str(alg.id() or "").casefold(), name.upper()) in _PERCENTAGE_PARAMETERS:
            continue
        try:
            typed_distance = definition.type() == "distance"
        except Exception:  # nosec B110
            typed_distance = False
        if not typed_distance and name.upper() not in _DISTANCE_NAMES:
            continue
        value = parameters.get(name)
        degrees_allowed = tuning.threshold("processing_degrees_allowed", _DEGREES_ALLOWED, 0.001, 0.1)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or abs(float(value)) < degrees_allowed:
            continue
        parent = ""
        if hasattr(definition, "parentParameterName"):
            try:
                parent = definition.parentParameterName() or ""
            except Exception:  # nosec B110
                parent = ""
        source = parameters.get(parent or "INPUT")
        layer = _find_layer(source) if isinstance(source, str) else None
        if layer is None or not hasattr(layer, "crs"):
            continue
        if name.upper() == "INTERVAL" and not typed_distance and isinstance(layer, QgsRasterLayer):



            continue
        crs = layer.crs()
        if not crs.isValid():
            continue
        if not crs.isGeographic():



            scale = ground.layer_metres_per_unit(layer)
            if scale is None or abs(scale - 1.0) <= ground.TOLERANCE:
                continue
            real = float(value) * scale
            suggested = _suggest_metric_crs(layer)
            return {
                "_error": (
                    f"GEOGRAPHIC_DISTANCE: {name} {value:g} on layer '{layer.name()}' in {crs.authid()} covers "
                    f"{real:.4g} m on the ground, not {value:g} m: one {crs.authid()} unit is {scale:.4g} m "
                    f"where this layer sits."
                ),
                "code": "CRS_GUARD",
                "layer_crs": crs.authid(),
                "ground_metres_per_unit": round(scale, 5),
                "suggested_crs": suggested,






                "suggestion": (
                    f"Changing {name} cannot clear this: the refusal is about the CRS, so every value in "
                    f"{crs.authid()} is refused the same way while one of its units is {scale:.4g} m here. "
                    f"Two ways out: native:reprojectlayer with TARGET_CRS {suggested} runs the algorithm in "
                    f"metres, or, when the layer spans too much of the world for any projection to fit it, "
                    f"confirm_large true keeps {crs.authid()} with the distance off by "
                    f"{abs(scale - 1.0) * 100:.0f} % where the layer sits."
                ),
            }
        suggested = _suggest_metric_crs(layer)
        return {
            "_error": (
                f"GEOGRAPHIC_DISTANCE: {name} {value:g} on layer '{layer.name()}' in {crs.authid()} means "
                f"{value:g} degrees, about {float(value) * 111:g} km, not metres."
            ),



            "code": "CRS_GUARD",
            "layer_crs": crs.authid(),
            "suggested_crs": suggested,
            "suggestion": (
                f"native:reprojectlayer with TARGET_CRS {suggested} runs the algorithm in metres. "
                "confirm_large true keeps degrees when a value in degrees is really intended."
            ),
        }
    return None





_NODATA_SENTINELS = (-9999.0, -99999.0, -32768.0, -32767.0, -3.4028234663852886e38)


def _nodata_sentinel(rast, band: int) -> float | None:

    try:
        import qgis.core as _core

        provider = rast.dataProvider()
        if (provider.sourceHasNoDataValue(band) and provider.useSourceNoDataValue(band)) \
                or provider.userNoDataValues(band):
            return None

        wanted = getattr(getattr(getattr(_core, "Qgis", None), "RasterBandStatistic", None), "Min", None)
        if wanted is None:
            wanted = getattr(_core.QgsRasterBandStats, "Min", None)
        minimum = float(provider.bandStatistics(band, wanted, rast.extent(), 250000).minimumValue)
    except Exception:  # noqa: BLE001
        return None
    for sentinel in _NODATA_SENTINELS:
        if abs(minimum - sentinel) <= abs(sentinel) * 1e-9:
            return sentinel
    return None


_ZONAL_ALGORITHMS = frozenset({"native:zonalstatisticsfb", "native:zonalstatistics", "qgis:zonalstatistics"})


def _undeclared_nodata_check(algorithm_id: str, parameters: dict, confirmed: bool) -> dict | None:






    if confirmed or algorithm_id not in tuning.check_algs("zonal_algorithms", _ZONAL_ALGORITHMS):
        return None
    source = parameters.get("INPUT_RASTER")
    layer = source if hasattr(source, "dataProvider") else (_find_layer(source) if isinstance(source, str) else None)
    if not isinstance(layer, QgsRasterLayer):
        return None
    try:
        band = int(parameters.get("RASTER_BAND") or 1)
    except (TypeError, ValueError):
        band = 1
    sentinel = _nodata_sentinel(layer, band)
    if sentinel is None:
        return None
    return {
        "_error": (
            f"UNDECLARED_NODATA: '{layer.name()}' band {band} declares no NoData, and its minimum is {sentinel:g}, "
            f"the usual missing-data marker: every statistic would count those cells as {sentinel:g}."
        ),
        "code": "INVALID_ARGS",
        "suggestion": (
            f"zonal_statistics with nodata={sentinel:g} leaves those cells out without touching the "
            f"source. confirm_large true keeps {sentinel:g} as a real measurement here."
        ),
    }




_TERRAIN_BY_CELL = frozenset({
    "gdal:slope", "gdal:aspect", "gdal:hillshade", "gdal:roughness", "gdal:triterrainruggednessindex",
    "gdal:tpitopographicpositionindex", "native:slope", "native:aspect", "native:hillshade",
    "native:ruggednessindex", "qgis:slope", "qgis:aspect", "qgis:hillshade", "qgis:ruggednessindex",
})


def _terrain_on_degrees_check(algorithm_id: str, parameters: dict) -> dict | None:









    if algorithm_id not in tuning.check_algs("terrain_by_cell", _TERRAIN_BY_CELL):
        return None
    source = parameters.get("INPUT")
    layer = source if hasattr(source, "crs") else (_find_layer(source) if isinstance(source, str) else None)
    if layer is None or not hasattr(layer, "crs"):
        return None
    crs = layer.crs()
    if not crs.isValid() or not crs.isGeographic():
        return None
    suggested = _suggest_metric_crs(layer)
    return {
        "_error": (
            f"TERRAIN_ON_DEGREES: '{layer.name()}' is in {crs.authid()}, so its cells are degrees while its "
            f"heights are not; {algorithm_id} on this grid is wrong on at least one axis, whatever SCALE is."
        ),
        "code": "CRS_GUARD",
        "layer_crs": crs.authid(),
        "suggested_crs": suggested,
        "suggestion": (
            f"gdal:warpreproject with TARGET_CRS {suggested} lets {algorithm_id} "
            "run on the result with SCALE 1."
        ),
    }







HILLSHADE_PROPERTY = "ai_agent/hillshade_of"
_HILLSHADE_ALGORITHM = re.compile(
    r"TerraLab AI Agent with ([\w.:-]*(?:hillshad|r\.relief|analyticalhillshading)[\w.:-]*)", re.IGNORECASE)
_HILLSHADE_NAME = re.compile(r"hillshad|shaded[ _-]?relief|ombrage|schummerung|sombreado", re.IGNORECASE)
_COMMAND_INPUT = re.compile(r"""['"]INPUT['"]\s*:\s*['"]([^'"]+)['"]""")


def _dem_named(value: str, shade) -> str:

    if not value:
        return ""
    project = QgsProject.instance()
    layer = project.mapLayer(value)
    if layer is None:

        layer = next((other for other in project.mapLayersByName(value)
                      if isinstance(other, QgsRasterLayer) and other.id() != shade.id()), None)
    if layer is None:
        wanted = os.path.normcase(os.path.normpath(value.split("|", 1)[0]))
        layer = next((other for other in project.mapLayers().values()
                      if isinstance(other, QgsRasterLayer) and other.id() != shade.id()
                      and os.path.normcase(os.path.normpath(other.source().split("|", 1)[0])) == wanted), None)
    return layer.name() if layer is not None and layer.id() != shade.id() else ""


def computed_hillshade(layer) -> dict:







    if not isinstance(layer, QgsRasterLayer):
        return {}
    try:
        from .elevation_style import RELIEF_PROPERTY

        if layer.customProperty(RELIEF_PROPERTY, ""):
            return {}
        dem_id = str(layer.customProperty(HILLSHADE_PROPERTY, "") or "")
        if dem_id:
            return {"how": "made by create_hillshade", "dem": _dem_named(dem_id, layer)}
        history = " ".join(str(item) for item in layer.metadata().history())
        found = _HILLSHADE_ALGORITHM.search(history)
        if found:
            source = _COMMAND_INPUT.search(history, found.end())
            return {"how": f"made by {found.group(1).rstrip('.')}",
                    "dem": _dem_named(source.group(1), layer) if source else ""}
        if _HILLSHADE_NAME.search(layer.name()) and layer.bandCount() == 1:
            from .processing_decisions import _type_name

            if _type_name(layer) == "Byte":
                return {"how": "named a hillshade, one band of 0-255 light values", "dem": ""}
    except Exception:  # noqa: BLE001
        return {}
    return {}


def hillshade_of_hillshade(layer, what: str) -> dict | None:

    found = computed_hillshade(layer)
    if not found:
        return None
    dem = f"'{found['dem']}'" if found["dem"] else "the elevation raster (DEM) it was computed from"
    return {
        "_error": (f"HILLSHADE_OF_HILLSHADE: '{layer.name()}' is already a hillshade ({found['how']}): its values "
                   f"are light, not heights, so {what} on it would shade the shading. Nothing was changed."),
        "code": "INVALID_ARGS",
        "suggestion": (f"{dem} is the DEM: set_raster_style on it with its colour ramp "
                       "and hillshade_overlay true adds the relief blended with multiply, or style_type hillshade "
                       "does the same. This computed hillshade over the coloured DEM needs "
                       "singleband_gray and set_layer_symbology blend_mode multiply."),
    }


def _hillshade_input_check(algorithm_id: str, parameters: dict) -> dict | None:

    if algorithm_id not in tuning.check_algs("terrain_by_cell", _TERRAIN_BY_CELL):
        return None
    source = parameters.get("INPUT")
    layer = source if hasattr(source, "crs") else (_find_layer(source) if isinstance(source, str) else None)
    return hillshade_of_hillshade(layer, algorithm_id)









_ON_DEGREES = {
    "gdal:viewshed": (True, "every sight line mixes a horizontal distance in degrees with heights in metres, "
                            "and MAX_DISTANCE (100 when left out) reads as degrees", "INPUT"),
    "native:rastersurfacevolume": (True, "each cell's area is in square degrees, so the volume comes out in "
                                         "degrees squared times metres", "INPUT"),
    "qgis:idwinterpolation": (False, "the distances IDW weights by are degrees, and a degree of longitude is "
                                     "shorter than a degree of latitude away from the equator; PIXEL_SIZE (0.1 "
                                     "when left out) is in degrees too", ""),
    "qgis:tininterpolation": (False, "PIXEL_SIZE (0.1 when left out, about 11 km) is in degrees, and the "
                                     "cells are not square on the ground", ""),
    "qgis:heatmapkerneldensityestimation": (False, "RADIUS (100 when left out) and PIXEL_SIZE are in degrees",
                                            "INPUT"),
    "gdal:gridinversedistance": (False, "the search radii and the distance weights are in degrees", "INPUT"),
    "gdal:gridinversedistancenearestneighbor": (False, "the search radius and the distance weights are in "
                                                       "degrees", "INPUT"),
    "gdal:gridaverage": (False, "the search radii are in degrees", "INPUT"),
    "gdal:griddatametrics": (False, "the search radii are in degrees", "INPUT"),
}


def _interpolation_source(value):

    if not isinstance(value, str) or not value:
        return None
    first = value.split("::|::", 1)[0].split("::~::", 1)[0]
    return _find_layer(first) if first else None


def _metric_grid_on_degrees_check(algorithm_id: str, parameters: dict, confirmed: bool) -> dict | None:

    row = _ON_DEGREES.get(str(algorithm_id or "").casefold())
    if row is None:
        return None
    always, why, key = row
    if confirmed and not always:
        return None
    layer = _find_layer(parameters.get(key)) if key and isinstance(parameters.get(key), str) else (
        _interpolation_source(parameters.get("INTERPOLATION_DATA")) if not key else None)
    if layer is None or not hasattr(layer, "crs"):
        return None
    crs = layer.crs()
    if not crs.isValid() or not crs.isGeographic():
        return None
    suggested = _suggest_metric_crs(layer)
    reproject = ("gdal:warpreproject" if isinstance(layer, QgsRasterLayer) else "native:reprojectlayer")
    return {
        "_error": (f"CRS_ON_DEGREES: '{layer.name()}' is in {crs.authid()}, a CRS in degrees: {algorithm_id} "
                   f"on it is wrong, because {why}."),
        "code": "CRS_GUARD",
        "layer_crs": crs.authid(),
        "suggested_crs": suggested,
        "suggestion": (f"{reproject} with TARGET_CRS {suggested} runs "
                       f"{algorithm_id} on the result in metres."
                       + ("" if always else " confirm_large true keeps values really meant in degrees.")),
    }






_FLOW_FROM_DEM = frozenset({
    "grass:r.flow", "grass7:r.flow",
    "saga:flowaccumulationtopdown", "sagang:flowaccumulationtopdown",
    "saga:flowaccumulationrecursive", "sagang:flowaccumulationrecursive",
    "sagang:flowaccumulationparallelizable", "saga:flowdirection", "sagang:flowdirection",
    "wbt:d8pointer", "wbt:dinfpointer", "wbt:fd8pointer", "wbt:rho8pointer",
})

_FILL_MARKS = ("fillsink", "fill.dir", "filldepression", "breachdepression", "pitremove", "sinkremoval",
               "fillsinkswangliu")

_FILL_ALGORITHMS = ("native:fillsinkswangliu", "grass:r.fill.dir", "grass7:r.fill.dir", "sagang:fillsinkswangliu",
                    "wbt:filldepressions", "wbt:breachdepressions")


def _was_filled(layer) -> bool:

    try:
        history = " ".join(str(item) for item in layer.metadata().history()).casefold()
    except Exception:  # noqa: BLE001
        return False
    return any(mark in history for mark in _FILL_MARKS)





_fill_step_cache: dict = {"value": "", "count": -1}


def _fill_step_available() -> str:

    try:
        algs = QgsApplication.processingRegistry().algorithms()
    except Exception:  # noqa: BLE001
        return ""
    if len(algs) != _fill_step_cache["count"]:
        ids = {a.id().casefold(): a.id() for a in algs}
        _fill_step_cache["value"] = next((ids[a] for a in _FILL_ALGORITHMS if a in ids), "")
        _fill_step_cache["count"] = len(algs)
    return _fill_step_cache["value"]


def _flow_dem(alg, algorithm_id: str, parameters: dict):

    if str(algorithm_id or "").casefold() not in _FLOW_FROM_DEM:
        return None
    layer = None
    for definition in alg.parameterDefinitions():
        try:
            if definition.type() == "raster" and not definition.isDestination():
                layer = _find_layer(parameters.get(definition.name())) \
                    if isinstance(parameters.get(definition.name()), str) else None
                break
        except Exception:  # noqa: BLE001  # nosec B112
            continue
    return layer if isinstance(layer, QgsRasterLayer) else None


def _flow_in_degrees_check(alg, algorithm_id: str, parameters: dict) -> dict | None:

    layer = _flow_dem(alg, algorithm_id, parameters)
    if layer is None:
        return None
    crs = layer.crs()
    if crs.isValid() and crs.isGeographic():
        suggested = _suggest_metric_crs(layer)
        return {
            "_error": (f"CRS_ON_DEGREES: '{layer.name()}' is in {crs.authid()}, so its cells are degrees while "
                       f"its heights are metres: {algorithm_id} routes water on slopes it cannot measure."),
            "code": "CRS_GUARD",
            "layer_crs": crs.authid(),
            "suggested_crs": suggested,
            "suggestion": (f"map_drainage reprojects and fills the DEM itself. Or gdal:warpreproject TARGET_CRS "
                           f"{suggested} then a sink fill prepares it for {algorithm_id}."),
        }
    return None


def unfilled_dem_warning(alg, algorithm_id: str, parameters: dict, confirmed: bool) -> str:







    if confirmed:
        return ""
    layer = _flow_dem(alg, algorithm_id, parameters)
    if layer is None or _was_filled(layer):
        return ""
    fill = _fill_step_available()
    step = f"{fill} on '{layer.name()}'" if fill else "a sink fill on the DEM"
    return (f"UNFILLED_DEM: the history of '{layer.name()}' names no sink fill. If it is a raw DEM, "
            f"{algorithm_id} stopped the water at every pit, so the streams break and the catchments fragment: "
            f"run {step} and route its output again, or call map_drainage, which fills and routes in one call. "
            f"A DEM already filled or breached elsewhere is fine as it is.")








_DEGREE_MIN_X = -180.0
_DEGREE_MAX_X = 360.0
_DEGREE_BOUND_Y = 90.0


_HALF_CELL_MAX = 2.5


def degree_slack(layer) -> tuple[float, float]:

    try:
        if isinstance(layer, QgsRasterLayer):
            half_x = abs(float(layer.rasterUnitsPerPixelX())) / 2.0
            half_y = abs(float(layer.rasterUnitsPerPixelY())) / 2.0
            half_max = tuning.threshold("processing_half_cell_max", _HALF_CELL_MAX, 1.0, 5.0)
            if half_x <= half_max and half_y <= half_max:
                return half_x, half_y
    except Exception:  # noqa: BLE001  # nosec B110
        pass
    return 0.0, 0.0


def fits_degrees(xmin: float, xmax: float, ymin: float, ymax: float,
                 slack_x: float = 0.0, slack_y: float = 0.0) -> bool:

    return (xmin >= _DEGREE_MIN_X - slack_x and xmax <= _DEGREE_MAX_X + slack_x
            and xmax - xmin <= 360.0 + 2 * slack_x
            and ymin >= -_DEGREE_BOUND_Y - slack_y and ymax <= _DEGREE_BOUND_Y + slack_y)


def _looks_like_degrees(extent, layer=None) -> bool:
    slack_x, slack_y = degree_slack(layer)
    return fits_degrees(extent.xMinimum(), extent.xMaximum(), extent.yMinimum(), extent.yMaximum(),
                        slack_x, slack_y)


def crs_plausibility(layer, new_crs) -> dict | None:










    try:
        if layer is None or not hasattr(layer, "extent") or not new_crs.isValid():
            return None
        extent = layer.extent()
        if extent is None or extent.isEmpty():
            return None
        degrees = _looks_like_degrees(extent, layer)
    except Exception:  # nosec B110
        return None

    name = layer.name()
    if new_crs.isGeographic() and not degrees:
        return {
            "_error": (
                f"The coordinates in '{name}' run to {extent.xMaximum():.0f}, {extent.yMaximum():.0f}, which "
                f"cannot be degrees, so declaring {new_crs.authid()} would put the layer in the wrong place."
            ),
            "code": "CRS_GUARD",
            "suggestion": (
                "native:reprojectlayer TARGET_CRS EPSG:4326 moves an already correctly declared layer into "
                "degrees. Setting the CRS relabels the coordinates, it does not move them."
            ),
        }
    if not new_crs.isGeographic() and degrees:
        return {
            "_error": (
                f"The coordinates in '{name}' all sit inside 180 by 90, so they are degrees, and "
                f"{new_crs.authid()} is in metres. Declaring it would not reproject the layer."
            ),
            "code": "CRS_GUARD",
            "suggestion": (
                f"The coordinates are usually EPSG:4326; from there, native:reprojectlayer TARGET_CRS "
                f"{new_crs.authid()} gives metres."
            ),
        }
    return _outside_area_of_use(layer, new_crs, extent)




_AREA_OF_USE_MARGIN = 2.0


def _centre_in_area_of_use(crs, point) -> bool | None:

    try:
        bounds = crs.bounds()
        if bounds is None or bounds.isEmpty():
            return None
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        lonlat = point if crs.isGeographic() else \
            QgsCoordinateTransform(crs, wgs84, QgsProject.instance()).transform(point)
        x, y = lonlat.x(), lonlat.y()
        if math.isnan(x) or math.isnan(y) or math.isinf(x) or math.isinf(y):
            return False
        if crs.isGeographic() and x > 180.0:

            x -= 360.0
        m = tuning.threshold("processing_area_of_use_margin", _AREA_OF_USE_MARGIN, 0.5, 10.0)
        return (bounds.xMinimum() - m <= x <= bounds.xMaximum() + m
                and bounds.yMinimum() - m <= y <= bounds.yMaximum() + m)
    except Exception:  # noqa: BLE001
        return False


def _outside_area_of_use(layer, new_crs, extent) -> dict | None:







    try:
        current = layer.crs()
        if not current.isValid() or current == new_crs:
            return None
        centre = extent.center()
    except Exception:  # nosec B110
        return None
    if _centre_in_area_of_use(current, centre) is not True or _centre_in_area_of_use(new_crs, centre) is not False:
        return None
    return {
        "_error": (
            f"'{layer.name()}' is declared {current.authid()} and its coordinates fit that CRS; read as "
            f"{new_crs.authid()} they fall outside the area that CRS covers, so declaring it would move the layer."
        ),
        "code": "CRS_GUARD",
        "suggestion": (
            f"native:reprojectlayer (gdal:warpreproject for a raster) TARGET_CRS "
            f"{new_crs.authid()} moves it there. Setting the CRS relabels the coordinates, it does not move them."
        ),
    }


_PROVENANCE_COMMAND_MAX = 2000


def _stamp_provenance(layer, provenance: dict) -> None:











    try:
        from datetime import datetime, timezone

        from ..core.licence import ORIGIN_PROPERTY, credit_layer

        when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        algorithm = provenance.get("algorithm") or "a Processing algorithm"
        command = str(provenance.get("command") or "")
        if len(command) > _PROVENANCE_COMMAND_MAX:
            command = command[:_PROVENANCE_COMMAND_MAX] + " ..."
        md = layer.metadata()


        own = any(str(text).strip() for text in md.licenses())
        inputs = ", ".join(str(name) for name in provenance.get("inputs") or [])
        md.addHistoryItem(f"{when}: produced by the TerraLab AI Agent with {algorithm}"
                          + (f". Inputs: {inputs}" if inputs else "")
                          + (f". Command: {command}" if command else ""))
        if not md.abstract():
            md.setAbstract(f"Output of {algorithm}, produced by the TerraLab AI Agent on {when}. "
                           "The exact command is in the metadata history.")
        layer.setMetadata(md)
        layer.setCustomProperty(ORIGIN_PROPERTY, True)
        if own:
            credit_layer(layer)
        else:
            credit_layer(layer, list(provenance.get("licences") or []), list(provenance.get("rights") or []))
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Provenance not written on {layer.name()}: {exc}")
        return
    write_metadata_to_file(layer, provenance.get("existed"))


def _gpkg_table(path: str) -> str:

    from osgeo import ogr

    dataset = ogr.Open(path)
    try:
        return dataset.GetLayer(0).GetName() if dataset is not None and dataset.GetLayerCount() else ""
    finally:
        dataset = None


def write_metadata_to_file(layer, existed=None) -> None:











    from qgis.core import QgsProviderRegistry

    from ..core.provenance import was_there

    try:
        registry = QgsProviderRegistry.instance()
        parts = registry.decodeUri(layer.providerType(), layer.source()) or {}
        path = str(parts.get("path") or "")
        if not path or not os.path.isfile(path):
            return
        if layer.providerType() == "ogr" and path.lower().endswith(".gpkg"):
            table = str(parts.get("layerName") or "") or _gpkg_table(path)
            if not table:
                log_warning(f"Provenance not saved in {os.path.basename(path)}: no table found in it")
                return
            if was_there(path, table, existed):
                return
            parts["layerName"] = table
            parts.pop("layerId", None)
            saved = registry.saveLayerMetadata("ogr", registry.encodeUri("ogr", parts), layer.metadata())
            ok, error = saved if isinstance(saved, tuple) else (bool(saved), "")
        else:
            if was_there(path, "", existed) or os.path.exists(layer.metadataUri()):
                return
            error, ok = layer.saveDefaultMetadata()
        if not ok:
            log_warning(f"Provenance not saved with {os.path.basename(path)}: {error}")
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Provenance not saved with the layer's file: {exc}")
