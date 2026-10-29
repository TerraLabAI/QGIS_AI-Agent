# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later










from __future__ import annotations

import math
import os
import re

from qgis.core import (
    Qgis,
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
    QgsVectorLayer,
)

from ..core import ground, limits, tuning, vsi
from ..core.logger import log_debug
from ..core.policy import create_managed_temp_dir
from ..core.qt_compat import enum_member
from ..core.tool_registry import tool_error
from .layer_lookup import _find_layer
from .processing_crs_checks import _TERRAIN_BY_CELL
from .project_tools import _is_remote_vector





_DISTANCE_DEGREES_MAX = 10.0
_DISTANCE_SPAN_RATIO = 100.0
_DISTANCE_FLOOR = 100_000.0


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
        limit, unit = tuning.threshold("distance_degrees_max", _DISTANCE_DEGREES_MAX, 1.0, 90.0), "degrees"
    else:
        ratio = tuning.threshold("distance_span_ratio", _DISTANCE_SPAN_RATIO, 5.0, 1000.0)
        floor = tuning.threshold("distance_floor", _DISTANCE_FLOOR, 1_000.0, 10_000_000.0)
        limit, unit = max(floor, ratio * span), "layer units"
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
        return tool_error(f"{key} is {layer.name()}, streamed over HTTP, and {provider} only reads files; a local copy "
                          f"would be {raw / 1e6:,.0f} MB, over the {cap / 1e6:,.0f} MB copied before a call.",
                          "INVALID_ARGS")
    try:
        from osgeo import gdal
    except ImportError:
        return tool_error("GDAL is missing, so the streamed raster cannot be copied.")
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
        return tool_error(f"{layer.name()} could not be copied from where it is streamed. {reason}".strip())
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


def _window_readings(value, layer):

    numbers = _extent_numbers(value)
    if numbers is None:
        return None
    a, b, c, d = numbers
    rect = QgsRectangle(min(a, b), min(c, d), max(a, b), max(c, d))
    tag = _WINDOW_CRS_RE.search(value) if isinstance(value, str) else None
    if tag and tag.group(1).upper() != layer.crs().authid().upper():
        try:
            transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem(tag.group(1).upper()),
                                               layer.crs(), QgsProject.instance())
            rect = transform.transformBoundingBox(rect)
        except Exception:  # noqa: BLE001
            return None
    extent = layer.extent()
    ratio_max = tuning.threshold("processing_window_ratio_max", _WINDOW_RATIO_MAX, 1.5, 10.0)
    return rect, rect.intersects(extent) and rect.area() <= ratio_max * extent.area()








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
        rect, fits = readings
        if fits:
            return None
        extent = layer.extent()
        problem = ("does not overlap" if not rect.intersects(extent)
                   else f"is {rect.area() / extent.area():,.0f} times the size of")
        return tool_error(
            f"WINDOW_OFF_RASTER: {key} spans {rect.width():,.6g} by {rect.height():,.6g} in the "
            f"raster's CRS and {problem} {layer.name()} ({layer.crs().authid()}, extent {_plain(extent.xMinimum())}, "
            f"{_plain(extent.yMinimum())} to {_plain(extent.xMaximum())}, {_plain(extent.yMaximum())}).",
            "INVALID_ARGS", hint="window_off_raster", key=key)
    return None


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
            return plain, selected, tool_error(
                f"{key} of {algorithm} is an object, and the only form it reads is "
                f'{{"source": "<layer name or id>", "selected_only": true, '
                f'"expression": "<QGIS expression>"}}, both options optional. '
                + (f"It does not read {', '.join(unknown)}. " if unknown else "")
                + "Nothing was run.", "INVALID_ARGS")
        plain[key] = source.strip()
        expression = (expression or "").strip()
        only_selected = _truthy(value.get("selected_only"))
        if definition is None or not (only_selected or expression):

            continue
        if kind != "source":
            return plain, selected, tool_error(
                f"{key} of {algorithm} is a {kind or 'non-vector'} input, and only a vector feature "
                f"source reads a selection or an expression (QGIS's 'Selected features only' box). "
                f"Nothing was run.", "INVALID_ARGS", hint="processing_subset_not_vector", parameter=key)
        layer = None if any(mark in source for mark in _NOT_A_LAYER_NAME) else _find_layer(source.strip())
        if layer is None or not hasattr(layer, "selectedFeatureCount"):
            if layer is None and not any(mark in source for mark in _NOT_A_LAYER_NAME):
                return plain, selected, _no_such_layers(alg, key, [source.strip()])
            return plain, selected, tool_error(
                f"{key} names {source.strip()!r}, which is not a vector layer of this project, and "
                f"only a project layer holds a selection or takes an expression here. Nothing was run.",
                "INVALID_ARGS", hint="processing_subset_not_project_layer")
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
        return 0, tool_error(
            f"Layer '{layer.name()}' {EMPTY_SELECTION_MARK}, so {key} with selected_only would "
            f"read nothing. Nothing was run.", "INVALID_ARGS", hint="processing_selection_empty")
    if not expression:
        return selection, None
    parsed = QgsExpression(expression)
    if parsed.hasParserError():
        return 0, tool_error(
            f"The expression of {key} does not parse: "
            f"{' '.join(parsed.parserErrorString().split())}. Nothing was run.",
            "INVALID_ARGS", hint="processing_expression_quotes")
    fields = [field.name() for field in layer.fields()]
    missing = sorted(set(parsed.referencedColumns()) - set(fields) - {QgsFeatureRequest.ALL_ATTRIBUTES})
    if missing:

        return 0, tool_error(
            f"The expression of {key} names {', '.join(missing)}, which '{layer.name()}' does not "
            f"have. Nothing was run.", "INVALID_ARGS",
            f"The fields of '{layer.name()}' are {', '.join(fields[:30])}.")
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
        return 0, tool_error(
            f"No feature of '{layer.name()}'" + (" selected" if only_selected else "")
            + f" matches {expression}, so {key} would read nothing. Nothing was run.",
            "INVALID_ARGS", hint="processing_subset_no_match", layer=layer.name())
    return -1, None


def source_definition(layer_id: str, only_selected: bool, expression: str):







    definition = QgsProcessingFeatureSourceDefinition(layer_id, bool(only_selected))
    if not expression:
        return definition
    if hasattr(definition, "filterExpression"):
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
        "hint": "processing_layer_not_found",
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
    try:
        static = bool(definition.usesStaticStrings())
    except (AttributeError, TypeError, RuntimeError):
        static = False
    if static:



        listed = ", ".join(options)
        for item in (value if isinstance(value, (list, tuple)) else [value]):
            if item is None or str(item) in options:
                continue
            text = str(item).strip()
            if text.lstrip("-").isdigit() and 0 <= int(text) < len(options):
                return f"is {text}, and this enum takes the option's text, not its index: send '{options[int(text)]}'."
            match = next((option for option in options if option.casefold() == text.casefold()), None)
            if match is not None:
                return f"is '{item}', and this enum takes the text exactly as QGIS spells it: send '{match}'."
            return f"is '{item}', which is not one of its choices. Choices: {listed}"
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


_FIELDS_LISTED = 60


def _field_parent_layer(value):

    if isinstance(value, QgsVectorLayer):
        return value
    source = getattr(value, "source", None)
    if source is not None:
        try:
            value = source.staticValue()
        except (AttributeError, RuntimeError):
            return None
    if not _names_a_layer(value):
        return None
    layer = QgsProject.instance().mapLayer(value.strip()) or _find_layer(value.strip())
    return layer if isinstance(layer, QgsVectorLayer) else None


def _field_problem(definition, value, parameters: dict) -> str:






    try:
        parent = definition.parentLayerParameterName()
    except (AttributeError, RuntimeError):
        return ""
    layer = _field_parent_layer(parameters.get(parent)) if parent else None
    if layer is None:
        return ""
    try:
        fields = layer.fields()
        names = list(fields.names())
    except RuntimeError:
        return ""
    listed = ", ".join(names[:_FIELDS_LISTED]) + (f" and {len(names) - _FIELDS_LISTED} more"
                                                   if len(names) > _FIELDS_LISTED else "")
    items = value if isinstance(value, (list, tuple)) else [value]
    for item in items:
        if item is None or isinstance(item, bool):
            continue
        text = str(item).strip()
        if not text or fields.lookupField(text) >= 0:
            continue
        if ";" in text and all(fields.lookupField(part.strip()) >= 0 for part in text.split(";") if part.strip()):
            continue
        if isinstance(item, (int, float)) or text.lstrip("-").isdigit():
            return (f"is {text}, a position, and a field parameter takes the field's name: QGIS finds no field "
                    f"named {text} in '{layer.name()}' ({parent}). Its fields: {listed}")
        return f"is '{text}', and '{layer.name()}' ({parent}) has no field of that name. Its fields: {listed}"
    return ""


def field_parameter_problem(alg, parameters: dict) -> str:




    algorithm = alg.id() if alg is not None else "this algorithm"
    for definition in _definitions(alg):
        value = parameters.get(definition.name())
        if value is None or _type_of(definition) != "field":
            continue
        problem = _field_problem(definition, value, parameters)
        if problem:
            return f"{PARAMETER_MARK} {algorithm} parameter '{definition.name()}' {problem}. Nothing was run."
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
                **tool_error(f"{PARAMETER_MARK} {algorithm} has no parameter '{key}'. Its parameter "
                             f"'{near[0]}' ({_type_of(definition) or 'value'}) is the one this spelling means. "
                             f"Nothing was run.", "INVALID_ARGS"),
                "parameters": [f"{d.name()} ({_type_of(d) or 'value'})" for d in definitions],
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
            **tool_error(f"{PARAMETER_MARK} {algorithm} needs {', '.join(absent)}, and the call gives "
                         f"no value for {'them' if len(absent) > 1 else 'it'}. Nothing was run.", "INVALID_ARGS"),
            "parameters": [f"{d.name()} ({_type_of(d) or 'value'})" for d in definitions],
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
                return tool_error(f"{PARAMETER_MARK} {algorithm} parameter '{name}' {problem}. Nothing was run.",
                                  "INVALID_ARGS")
            continue
        if kind == "field":
            problem = _field_problem(definition, value, parameters)
            if problem:
                return tool_error(f"{PARAMETER_MARK} {algorithm} parameter '{name}' {problem}. Nothing was run.",
                                  "INVALID_ARGS")
            continue
        if kind in _NUMERIC_TYPES and isinstance(value, str) and value.strip():
            try:
                float(value.strip())
            except ValueError:
                return tool_error(f"{PARAMETER_MARK} {algorithm} parameter '{name}' is a {kind} and the call "
                                  f"sends the text '{value}'. Nothing was run.",
                                  "INVALID_ARGS", hint="processing_number_units", parameter=str(name))
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
        return tool_error(
            f"Layer '{layer.name()}' has no features, so {alg.id() if alg is not None else 'this algorithm'} "
            f"would produce an empty layer.", "INVALID_ARGS", hint="processing_input_empty")
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


_ZONAL_ALGORITHMS = frozenset()


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
    return tool_error(
        f"UNDECLARED_NODATA: '{layer.name()}' band {band} declares no NoData, and its minimum is {sentinel:g}, "
        f"the usual missing-data marker: every statistic would count those cells as {sentinel:g}.",
        "INVALID_ARGS", hint="processing_undeclared_nodata", sentinel=sentinel)







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
    return tool_error(
        f"HILLSHADE_OF_HILLSHADE: '{layer.name()}' is already a hillshade ({found['how']}): its values "
        f"are light, not heights, so {what} on it would shade the shading. Nothing was changed.",
        "INVALID_ARGS", hint="processing_hillshade_of_hillshade", dem=dem)


def _hillshade_input_check(algorithm_id: str, parameters: dict) -> dict | None:

    if algorithm_id not in tuning.check_algs("terrain_by_cell", _TERRAIN_BY_CELL):
        return None
    source = parameters.get("INPUT")
    layer = source if hasattr(source, "crs") else (_find_layer(source) if isinstance(source, str) else None)
    return hillshade_of_hillshade(layer, algorithm_id)
