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

from ..core import ground
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
        "suggestion": (
            "Check the layer CRS (reproject a geographic layer to metres first) or confirm the distance "
            "with ask_user, then call run_processing again with confirm_large true."
        ),
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

    width, height = b - a, d - c
    asked = _cells(width, height, hspacing, vspacing)
    if asked <= _GRID_CELLS_MAX:
        return None

    alt_width, alt_height = c - a, d - b
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
        "suggestion": (
            "Rewrite EXTENT as xmin,xmax,ymin,ymax for the box you mean, or widen the spacing. "
            "Pass confirm_large true only if a grid this size is really wanted."
        ),
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
            "suggestion": (
                f"Clip it to the study area first: run_processing gdal:cliprasterbyextent with INPUT "
                f"'{value}' and PROJWIN set to the canvas extent (get_canvas_extent, in the layer's CRS), "
                f"then run this algorithm on the clip. Pass confirm_large true only if the whole tile is wanted."
            ),
        }
    return None








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


def _window_sanity(parameters: dict) -> dict | None:
    """Refuse a raster window that misses the input raster or dwarfs it."""






    for key in ("PROJWIN", "EXTENT", "TARGET_EXTENT"):
        value = parameters.get(key)
        numbers = _extent_numbers(value)
        if numbers is None:
            continue
        layer = _raster_input(parameters)
        if layer is None or layer.extent().isEmpty():
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
            except Exception:
                return None
        extent = layer.extent()
        if rect.intersects(extent) and rect.area() <= _WINDOW_RATIO_MAX * extent.area():
            return None
        problem = ("does not overlap" if not rect.intersects(extent)
                   else f"is {rect.area() / extent.area():,.0f} times the size of")
        hint = ""
        if alt.intersects(extent) and alt.area() <= _WINDOW_RATIO_MAX * extent.area():
            hint = (f" The same numbers read as xmin,ymin,xmax,ymax lie on the raster: "
                    f"pass '{_plain(a)},{_plain(c)},{_plain(b)},{_plain(d)}{crs_text}'.")
        return {
            "_error": (
                f"WINDOW_OFF_RASTER: {key} spans {rect.width():,.0f} by {rect.height():,.0f} in the "
                f"raster's CRS and {problem} {layer.name()} (extent {extent.xMinimum():,.0f}, "
                f"{extent.yMinimum():,.0f} to {extent.xMaximum():,.0f}, {extent.yMaximum():,.0f}). "
                f"Processing reads {key} as xmin,xmax,ymin,ymax [EPSG:n], not xmin,ymin,xmax,ymax.{hint}"
            ),
            "code": "INVALID_ARGS",
            "suggestion": (
                f"Rewrite {key} as xmin,xmax,ymin,ymax in the raster's CRS; get_layer_info gives its "
                f"extent and crs. Never a box larger than the raster."
            ),
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


def _resolve_layer_inputs(parameters: dict) -> tuple[dict, list[str]]:
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
    return resolved, rewritten


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
        names = [layer.name() for layer in QgsProject.instance().mapLayers().values()][:20]
        return {
            "_error": f"No layer in this project is called {value!r}, so "
                      f"{alg.id() if alg is not None else 'this algorithm'} has nothing to read.",
            "code": "INVALID_ARGS",
            "layers": names,
            "suggestion": "Pass one of the names above, or the layer_id the tool that made the layer "
                          "returned. list_layers gives both.",
        }
    return None


def _empty_input_check(alg, parameters: dict) -> dict | None:
    """Refuse an algorithm whose input layer holds no features."""








    for key in ("INPUT", "INPUT_LAYER", "LAYER", "SOURCE", "INPUT_VECTOR"):
        source = parameters.get(key)
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
    return None


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
