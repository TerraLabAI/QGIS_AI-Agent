# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Guards run before a Processing algorithm and the stamp written after it."""
















from __future__ import annotations

import math
import os
import re
import time

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
)

from ..core import ground, limits, tuning, vsi
from ..core.logger import log_debug
from ..core.policy import create_managed_temp_dir
from .layer_lookup import _find_layer


def _distance_sanity(parameters: dict, confirmed: bool) -> dict | None:
    """Refuse a DISTANCE that dwarfs the input layer (5000 km around city points, 500 "metres" on a degree layer) until the model confirms it with."""

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
    return {







        "_error": (
            f"DISTANCE_SUSPICIOUS: DISTANCE {distance:g} {unit} is far beyond the input layer, whose "
            f"extent spans {span:g} {unit}. This looks like a unit or CRS mistake."
        ),
        "code": "INVALID_ARGS",
    }







_GRID_CELLS_MAX = 2_000_000


_EXTENT_RE = re.compile(r"^\s*(-?[\d.eE+]+)\s*,\s*(-?[\d.eE+]+)\s*,\s*(-?[\d.eE+]+)\s*,\s*(-?[\d.eE+]+)")


def _number(value) -> float | None:
    """A parameter as a number, including the string a model often sends instead."""
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
    """The four numbers of an EXTENT, whether it arrives as Processing's string or as the list of four a model writes when it is thinking of a."""

    if isinstance(value, (list, tuple)):
        numbers = [_number(v) for v in value]
        if len(numbers) == 4 and all(n is not None for n in numbers):
            return tuple(numbers)
        return None
    if not isinstance(value, str):
        return None
    found = _EXTENT_RE.match(value)
    if not found:
        return None
    try:
        return tuple(float(n) for n in found.groups())
    except ValueError:
        return None


def _cells(width: float, height: float, hspacing: float, vspacing: float) -> float:
    if width <= 0 or height <= 0 or hspacing <= 0 or vspacing <= 0:
        return 0.0
    if not (math.isfinite(width) and math.isfinite(height)):
        return math.inf
    return math.ceil(width / hspacing) * math.ceil(height / vspacing)


def _grid_sanity(parameters: dict, confirmed: bool) -> dict | None:
    """Refuse a grid whose cell count is beyond anything a person asked for."""







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
        except Exception:  # noqa: BLE001 - a box that will not transform is past any grid
            return math.inf, math.inf
        return (width, height) if math.isfinite(width) and math.isfinite(height) else (math.inf, math.inf)


    width, height = spans(a, b, c, d)
    asked = _cells(width, height, hspacing, vspacing)
    if asked <= _GRID_CELLS_MAX:
        return None

    alt_width, alt_height = spans(a, c, b, d)
    meant = _cells(alt_width, alt_height, hspacing, vspacing)
    hint = ""
    if 0 < meant <= _GRID_CELLS_MAX:
        hint = (f" Read as xmin,ymin,xmax,ymax, the order every other tool here takes, the same four "
                f"numbers span {alt_width:,.0f} by {alt_height:,.0f} and give {meant:,.0f} "
                f"cell{'' if meant == 1 else 's'}.")
    return {
        "_error": (
            f"GRID_TOO_LARGE: this EXTENT and spacing build {asked:,.0f} cells, over a box spanning "
            f"{width:,.0f} by {height:,.0f}. A Processing EXTENT is xmin,xmax,ymin,ymax, not "
            f"xmin,ymin,xmax,ymax.{hint}"
        ),
        "code": "INVALID_ARGS",
    }





_REMOTE_RASTER_PIXELS_MAX = 60_000_000
_RASTER_INPUT_KEYS = ("INPUT", "INPUT_RASTER", "INPUT_A", "INPUT_B", "RASTER", "GRID", "ELEVATION", "DEM")


_RASTER_LIST_KEYS = ("LAYERS", "INPUT_LAYERS", "INPUTS")




_WINDOW_KEYS = ("PROJWIN", "EXTENT", "TARGET_EXTENT", "MASK", "MASK_LAYER")






_WINDOW_ALGORITHMS = ("gdal:cliprasterbyextent", "gdal:cliprasterbymasklayer", "gdal:translate",
                      "gdal:warpreproject", "gdal:rearrange_bands", "native:cliprasterbyextent")


def _raster_ids(parameters: dict):
    """Every parameter value that may name a raster layer, scalar keys and lists alike."""
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


def _raster_size_sanity(parameters: dict, confirmed: bool, algorithm_id: str = "") -> dict | None:
    """Refuse a derivative of a huge remote raster until it is clipped or confirmed."""







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
        if pixels <= _REMOTE_RASTER_PIXELS_MAX:
            continue
        return {
            "_error": (
                f"RASTER_TOO_LARGE: {key} is a remote raster of {pixels / 1e6:,.0f} million pixels "
                f"({layer.width():,} by {layer.height():,}); every derivative streams the whole file and "
                f"takes minutes."
            ),
            "code": "INVALID_ARGS",
        }
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
    except Exception:  # noqa: BLE001 - an unknown type is sized as the largest
        return 8


def _local_copy(layer, algorithm_id: str, key: str) -> dict:
    """A GeoTIFF on this disk holding what the layer streams, or the refusal."""
    source = str(layer.source() or "")
    cached = _STREAMED_COPIES.get(source)
    if cached and os.path.exists(cached):
        return {"path": cached, "megabytes": os.path.getsize(cached) / 1e6}
    raw = int(layer.width()) * int(layer.height()) * max(1, int(layer.bandCount())) * _sample_bytes(layer)
    cap = min(_STREAMED_COPY_MAX_BYTES, int(limits.current("MAX_DOWNLOAD_BYTES")))
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
    except Exception as exc:  # noqa: BLE001 - GDAL raises once UseExceptions is on
        copied, reason = False, " ".join(str(exc).split())[:200]
    if not copied or not os.path.exists(path):
        return {"_error": f"{layer.name()} could not be copied from where it is streamed. {reason}".strip(),
                "code": "EXECUTION_FAILED"}
    _STREAMED_COPIES[source] = path
    return {"path": path, "megabytes": os.path.getsize(path) / 1e6}


def localise_streamed_rasters(algorithm_id: str, parameters: dict) -> tuple[dict, list[str], dict | None]:
    """Hand a GRASS, SAGA or OTB algorithm a local copy of every raster it would otherwise stream."""




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
    """A coordinate without exponent: 6862000, not 6.862e+06."""
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _raster_input(parameters: dict):
    project = QgsProject.instance()
    for _key, value in _raster_ids(parameters):
        layer = project.mapLayer(value)
        if isinstance(layer, QgsRasterLayer):
            return layer
    return None


def _window_degrees_repair(parameters: dict) -> list[str]:
    """A window written in degrees for a projected raster, rewritten in place."""








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
        except Exception:  # nosec B112 - a layer whose CRS refuses a transform cannot answer this reading
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
            except Exception:  # nosec B112 - a box that will not transform is simply not this reading
                continue
            if rect.intersects(extent) and rect.area() <= _WINDOW_RATIO_MAX * extent.area():
                parameters[key] = f"{_plain(xmin)},{_plain(xmax)},{_plain(ymin)},{_plain(ymax)} [EPSG:4326]"
                notes.append(f"{key} was four degrees with no CRS: written as xmin,xmax,ymin,ymax [EPSG:4326] "
                             f"for a raster in {crs.authid()}")
                break
    return notes


def _window_readings(value, layer):
    """(rect, alt, crs_text, fits, alt_fits) for a window, both readings in the raster's CRS, or None."""



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
        except Exception:  # noqa: BLE001 - a window that will not transform is not judged
            return None
    extent = layer.extent()

    def fits(box):
        return box.intersects(extent) and box.area() <= _WINDOW_RATIO_MAX * extent.area()

    return rect, alt, crs_text, fits(rect), fits(alt)


def _window_order_repair(parameters: dict) -> list[str]:
    """A window in the xmin,ymin,xmax,ymax habit, rewritten in Processing's order when only that reading fits."""




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
    """Refuse a raster window that misses the input raster or dwarfs it."""





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
        return {
            "_error": (
                f"WINDOW_OFF_RASTER: {key} spans {rect.width():,.6g} by {rect.height():,.6g} in the "
                f"raster's CRS and {problem} {layer.name()} (extent {extent.xMinimum():,.6g}, "
                f"{extent.yMinimum():,.6g} to {extent.xMaximum():,.6g}, {extent.yMaximum():,.6g}). "
                f"Processing reads {key} as xmin,xmax,ymin,ymax [EPSG:n], not xmin,ymin,xmax,ymax."
            ),
            "code": "INVALID_ARGS",
        }
    return None









_FORMAT_FLAGS = ("-f", "-of")
_GDAL_OPTION_KEYS = ("OPTIONS", "EXTRA")


_OPTION_TOKEN_RE = re.compile(r"""(?:[^\s"']+|"[^"]*"|'[^']*')+""")


def _strip_format_flags(value: str) -> tuple[str, list[str]]:
    """The option string without its -f/-of pairs, and the pairs removed."""
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
    """Remove the format flags from a GDAL call's OPTIONS/EXTRA, in place."""




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
            '"Duplicate argument". Give the OUTPUT the extension you want instead.'
        )
    return repairs


_INPUT_KEYS = ("INPUT", "INPUT_LAYER", "LAYER", "SOURCE", "INPUT_VECTOR")



_NOT_A_LAYER_NAME = ("|", "://", "/", "\\", "memory:")
_OUTPUT_SENTINELS = ("TEMPORARY_OUTPUT", "memory:")


def _resolve_layer_inputs(parameters: dict, alg=None) -> tuple[dict, list[str]]:
    """Replace a named input with the id of the layer that name points at."""








    resolved = dict(parameters)
    rewritten: list[str] = []
    for key in _INPUT_KEYS:
        source = parameters.get(key)
        if not isinstance(source, str) or not source.strip():
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
    """Whether a string argument is meant as a project layer's name or id."""
    if not isinstance(value, str) or not value.strip():
        return False
    value = value.strip()
    if value in _OUTPUT_SENTINELS or any(mark in value for mark in _NOT_A_LAYER_NAME):
        return False
    return not os.path.exists(value)


def _layer_id_for(item):
    """A list entry as the id of the layer it names, or unchanged."""
    if not _names_a_layer(item):
        return item
    layer = _find_layer(item.strip())
    try:
        return layer.id() if layer is not None and layer.id() else item
    except (AttributeError, RuntimeError):
        return item


def _layer_list_keys(alg) -> set:
    """The parameters of ``alg`` that take several layers (LAYERS of mergevectorlayers)."""
    keys = set()
    try:
        definitions = alg.parameterDefinitions() if alg is not None else []
    except Exception:  # noqa: BLE001 - no definitions, no list inputs to check
        return keys
    for definition in definitions:
        try:
            if definition.type() == "multilayer":
                keys.add(definition.name())
        except Exception:  # nosec B112 - a definition that cannot say its type is skipped
            continue
    return keys


def _unresolved_input_check(alg, parameters: dict) -> dict | None:
    """Refuse an INPUT that names no layer, before anything is started."""









    for key in _INPUT_KEYS:
        source = parameters.get(key)
        if not isinstance(source, str) or not source.strip():
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
    """The refusal for input names that match no layer of the project."""
    names = [layer.name() for layer in QgsProject.instance().mapLayers().values()][:20]
    quoted = ", ".join(repr(value) for value in missing)
    return {
        "_error": (f"No layer in this project is called {quoted} ({key}), so "
                   f"{alg.id() if alg is not None else 'this algorithm'} has nothing to read."),
        "code": "INVALID_ARGS",
        "layers": names,
        "suggestion": "Pass one of the names above, or the layer_id the tool that made the layer "
                      "returned. list_layers gives both.",
    }










PARAMETER_MARK = "PARAMETER_INVALID:"


_NUMERIC_TYPES = frozenset({"number", "distance", "area", "volume", "duration", "scale"})
_SKIPPED_TYPES = frozenset({"matrix", "range", "aggregates", "fieldmapping", "tininputlayers",
                            "vectortilewriterlayers", "dxflayers", "meshdatasetgroups",
                            "meshdatasettime", "alignrasterlayers", "rasterdemparameters"})


def _definitions(alg):
    try:
        return list(alg.parameterDefinitions()) if alg is not None else []
    except Exception:  # noqa: BLE001 - an algorithm that cannot list its parameters is not checked
        return []


def _type_of(definition) -> str:
    try:
        return str(definition.type() or "").casefold()
    except Exception:  # noqa: BLE001 - a definition without a type is not checked
        return ""


def _enum_problem(definition, value) -> str:
    """Why this value cannot be an enum choice, or ""."""
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
    """Refuse a call whose parameters QGIS would reject, naming each one and its type."""
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
                "suggestion": f"Send '{near[0]}' instead of '{key}'. The full parameter list is above.",
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
        except Exception as exc:  # noqa: BLE001 - a definition that cannot say is left alone
            log_debug(f"required parameter check: {name!r} could not say if it is optional: {exc}")
            continue
        absent.append(f"{name} ({_type_of(definition) or 'value'})")
    if absent:
        return {
            "_error": (f"{PARAMETER_MARK} {algorithm} needs {', '.join(absent)}, and the call gives "
                       f"no value for {'them' if len(absent) > 1 else 'it'}. Nothing was run."),
            "code": "INVALID_ARGS",
            "parameters": [f"{d.name()} ({_type_of(d) or 'value'})" for d in definitions],
            "suggestion": "Add " + ", ".join(absent) + " to parameters, with the type named in brackets.",
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
                    "suggestion": f"Send the integer for '{name}' from the choices above.",
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
                    "suggestion": f"Send '{name}' as a number, in the units of the input layer's CRS.",
                }
    return None


def ignored_parameters(alg, parameters: dict) -> list:
    """Parameter names the algorithm does not have and no real name resembles."""





    definitions = _definitions(alg)
    if not definitions:
        return []
    names = {definition.name() for definition in definitions}
    return [f"{key} is not a parameter of {alg.id()} and was ignored"
            for key in parameters if key not in names]


def _empty_input_check(alg, parameters: dict) -> dict | None:
    """Refuse an algorithm whose input layer holds no features."""








    for key in ("INPUT", "INPUT_LAYER", "LAYER", "SOURCE", "INPUT_VECTOR"):
        source = parameters.get(key)
        if isinstance(source, str) and any(mark in source for mark in _NOT_A_LAYER_NAME):


            layer = QgsProject.instance().mapLayer(source)
        else:
            layer = _find_layer(source) if isinstance(source, str) else source
        if layer is None or not hasattr(layer, "featureCount"):
            continue
        try:
            count = int(layer.featureCount())
        except Exception:  # nosec B112 - a source that cannot count is not one to refuse over
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
                "Check the layer is the one you meant and that any filter or selection on it is not hiding "
                "everything, or work on the layer that does hold the features."
            ),
        }
    return None


_DISTANCE_NAMES = ("DISTANCE", "RADIUS", "TOLERANCE", "BUFFER", "MAX_DISTANCE", "SEARCH_DISTANCE",
                   "SNAP_TOLERANCE", "SPACING", "INTERVAL", "CELLSIZE", "PIXEL_SIZE")

_DEGREES_ALLOWED = 0.01









def _utm_authid(lon: float, lat: float) -> str:
    """The UTM zone under a lon/lat point, as an EPSG authid (326xx north, 327xx south)."""
    lon = max(-180.0, min(180.0, float(lon)))
    zone = min(60, int((lon + 180.0) // 6.0) + 1)
    return f"EPSG:{32600 + zone}" if float(lat) >= 0 else f"EPSG:{32700 + zone}"


def _centre_lonlat(layer) -> tuple[float, float] | None:
    """The centre of the layer's extent as longitude and latitude, whatever its CRS."""
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
    except Exception:  # noqa: BLE001 - no centre means no suggestion, not a failed call
        return None


def _suggest_metric_crs(layer) -> str:
    """A CRS whose metres are ground metres over this layer."""









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
    """The layer's ground, read as if it were already in the project's CRS."""






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
    """Refuse a distance, radius or tolerance whose units are not ground metres."""







    if confirmed:
        return None
    for definition in alg.parameterDefinitions():
        name = definition.name()
        try:
            typed_distance = definition.type() == "distance"
        except Exception:  # nosec B110 - a definition without type()
            typed_distance = False
        if not typed_distance and name.upper() not in _DISTANCE_NAMES:
            continue
        value = parameters.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or abs(float(value)) < _DEGREES_ALLOWED:
            continue
        parent = ""
        if hasattr(definition, "parentParameterName"):
            try:
                parent = definition.parentParameterName() or ""
            except Exception:  # nosec B110 - no parent parameter
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
                    f"Reproject the layer first (native:reprojectlayer with TARGET_CRS {suggested}) and run "
                    f"the algorithm on the result; tell the user which CRS you used. Pass confirm_large true "
                    f"only when {value:g} {crs.authid()} units is really what was meant."
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
                f"Reproject the layer first (native:reprojectlayer with TARGET_CRS {suggested}) and run "
                "the algorithm on the result; tell the user which CRS you used. Pass confirm_large true only "
                "when a value in degrees is really intended."
            ),
        }
    return None





_NODATA_SENTINELS = (-9999.0, -99999.0, -32768.0, -32767.0, -3.4028234663852886e38)


def _nodata_sentinel(rast, band: int) -> float | None:
    """The sentinel this band's minimum sits on; None when there is none or it is declared NoData."""
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
    except Exception:  # noqa: BLE001 - no statistics, no claim about the band
        return None
    for sentinel in _NODATA_SENTINELS:
        if abs(minimum - sentinel) <= abs(sentinel) * 1e-9:
            return sentinel
    return None


_ZONAL_ALGORITHMS = frozenset({"native:zonalstatisticsfb", "native:zonalstatistics", "qgis:zonalstatistics"})


def _undeclared_nodata_check(algorithm_id: str, parameters: dict, confirmed: bool) -> dict | None:
    """Refuse zonal statistics over a band whose minimum is an undeclared missing-data marker."""





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
            f"Call zonal_statistics with nodata={sentinel:g}, which leaves those cells out without touching the "
            f"source. Pass confirm_large true only if {sentinel:g} is a real measurement here."
        ),
    }




_TERRAIN_BY_CELL = frozenset({
    "gdal:slope", "gdal:aspect", "gdal:hillshade", "gdal:roughness", "gdal:triterrainruggednessindex",
    "gdal:tpitopographicpositionindex", "native:slope", "native:aspect", "native:hillshade",
    "native:ruggednessindex", "qgis:slope", "qgis:aspect", "qgis:hillshade", "qgis:ruggednessindex",
})


def _terrain_on_degrees_check(algorithm_id: str, parameters: dict) -> dict | None:
    """Refuse a slope, aspect or hillshade computed on a DEM whose cells are degrees."""








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
            f"Reproject the DEM first (gdal:warpreproject with TARGET_CRS {suggested}), then run {algorithm_id} "
            "on the result with SCALE 1."
        ),
    }





_DEGREE_BOUND_X = 180.0
_DEGREE_BOUND_Y = 90.0


def _looks_like_degrees(extent) -> bool:
    return (abs(extent.xMinimum()) <= _DEGREE_BOUND_X and abs(extent.xMaximum()) <= _DEGREE_BOUND_X
            and abs(extent.yMinimum()) <= _DEGREE_BOUND_Y and abs(extent.yMaximum()) <= _DEGREE_BOUND_Y)


def crs_plausibility(layer, new_crs) -> dict | None:
    """Refuse a CRS the coordinates in the layer contradict."""









    try:
        if layer is None or not hasattr(layer, "extent") or not new_crs.isValid():
            return None
        extent = layer.extent()
        if extent is None or extent.isEmpty():
            return None
        degrees = _looks_like_degrees(extent)
    except Exception:  # nosec B110 - a layer that cannot answer is not a layer to refuse over
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
                "If the layer already has the right CRS and you want it in degrees, reproject it with "
                "native:reprojectlayer TARGET_CRS EPSG:4326. Setting the CRS relabels the coordinates, it "
                "does not move them."
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
                f"Declare the geographic CRS the coordinates are actually in (usually EPSG:4326), then "
                f"reproject with native:reprojectlayer TARGET_CRS {new_crs.authid()} if you want metres."
            ),
        }
    return _outside_area_of_use(layer, new_crs, extent)




_AREA_OF_USE_MARGIN = 2.0


def _centre_in_area_of_use(crs, point) -> bool | None:
    """Whether `point`, read in `crs`, falls in that CRS's area of use; None when unknown."""
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
        m = _AREA_OF_USE_MARGIN
        return (bounds.xMinimum() - m <= x <= bounds.xMaximum() + m
                and bounds.yMinimum() - m <= y <= bounds.yMaximum() + m)
    except Exception:  # noqa: BLE001 - a point a CRS cannot transform is outside it
        return False


def _outside_area_of_use(layer, new_crs, extent) -> dict | None:
    """Refuse relabelling a layer whose declared CRS fits it with one that puts it off its grid."""






    try:
        current = layer.crs()
        if not current.isValid() or current == new_crs:
            return None
        centre = extent.center()
    except Exception:  # nosec B110 - a layer that cannot answer is not a layer to refuse over
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
            f"Reproject with native:reprojectlayer (gdal:warpreproject for a raster) TARGET_CRS "
            f"{new_crs.authid()}. Setting the CRS relabels the coordinates, it does not move them."
        ),
    }


_PROVENANCE_COMMAND_MAX = 2000


def _stamp_provenance(layer, provenance: dict) -> None:
    """Write how the layer was made into its metadata (Layer Properties > Metadata)."""




    try:
        when = time.strftime("%Y-%m-%d %H:%M")
        algorithm = provenance.get("algorithm") or "a Processing algorithm"
        command = str(provenance.get("command") or "")
        if len(command) > _PROVENANCE_COMMAND_MAX:
            command = command[:_PROVENANCE_COMMAND_MAX] + " ..."
        md = layer.metadata()
        md.addHistoryItem(f"{when}: produced by the TerraLab AI Agent with {algorithm}"
                          + (f". Command: {command}" if command else ""))
        if not md.abstract():
            md.setAbstract(f"Output of {algorithm}, produced by the TerraLab AI Agent on {when}. "
                           "The exact command is in the metadata history.")
        layer.setMetadata(md)
    except Exception:  # nosec B110 - provenance is best effort, the layer is already in the project
        pass
