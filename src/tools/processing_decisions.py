# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





from __future__ import annotations

import math
import os
import re

from qgis.core import QgsApplication

from ..core import tuning
from ..core.tool_registry import tool_error






_UNSAFE_PROCESSING_ALGORITHMS = frozenset({
    "algorithms:centerlines",
    "algorithms:sinuosity",
})


def _unsafe_algorithms() -> frozenset:

    return tuning.check_algs("unsafe_threading", _UNSAFE_PROCESSING_ALGORITHMS)


def _unsafe_processing_algorithm(algorithm_id: str) -> dict | None:
    folded = str(algorithm_id).casefold()
    if folded not in _unsafe_algorithms():
        return None
    return tool_error(
        f"{algorithm_id} is blocked because its installed implementation edits the source "
        "layer from Processing and is unsafe for AI Agent execution.",
        "INVALID_ARGS", hint="processing_unsafe_algorithm", variant=folded, algorithm=folded)


def _rules(section: str) -> dict:

    value = (tuning.service_doc("processing_rules") or {}).get(section)
    return value if isinstance(value, dict) else {}




_NO_THREADING_FLAG = 64


def _threadable(alg) -> bool:













    try:
        if str(alg.id()).casefold() in _unsafe_algorithms():
            return False
        if int(alg.flags()) & _NO_THREADING_FLAG:
            return False
        return _model_steps_may_leave(alg)
    except Exception:  # noqa: BLE001
        return False


MAIN_THREAD_NOTE = ("This algorithm runs on QGIS's main thread (it declares NoThreading, or its steps "
                    "touch what only the main thread may): QGIS does not respond to the user until the "
                    "whole run ends, however long that is, and run_processing answers only then.")


SEPARATE_QGIS_NOTE = ("This algorithm declares NoThreading. When every layer it reads is a file layer "
                      "(no selection, filter or unsaved edits) and every output is a file, run_processing "
                      "runs it in a separate QGIS process, which takes a few seconds to start, and this "
                      "QGIS stays responsive; otherwise it runs on QGIS's main thread and QGIS does not "
                      "respond to the user until the whole run ends.")


def main_thread_facts(alg) -> dict:







    if _threadable(alg):
        return {}
    from .processing_child import child_capable

    if str(alg.id()).casefold() not in _unsafe_algorithms() and child_capable(alg):
        return {"main_thread": "unless_files", "main_thread_note": SEPARATE_QGIS_NOTE}
    return {"main_thread": True, "main_thread_note": MAIN_THREAD_NOTE}


def _model_steps_may_leave(alg) -> bool:















    children = alg.childAlgorithms() if hasattr(alg, "childAlgorithms") else {}
    for child in children.values():
        step = child.algorithm() or QgsApplication.processingRegistry().algorithmById(child.algorithmId())
        if step is None or str(step.id()).casefold() in _unsafe_algorithms():
            return False
        if not _model_steps_may_leave(step):
            return False
    return True


def _goes_to_task(alg, args: dict, parameters: dict) -> bool:















    del args, parameters
    return _threadable(alg)


def _geometry_word(layer) -> str:

    try:
        from qgis.core import QgsWkbTypes

        return str(QgsWkbTypes.geometryDisplayString(layer.geometryType())).casefold()
    except Exception:  # noqa: BLE001
        return ""


def geometry_defaults(alg, algorithm_id: str, parameters: dict, layer) -> tuple[dict, list[str]]:



    rows = _rules("geometry_defaults").get(str(algorithm_id or "").casefold())
    if not rows or layer is None:
        return parameters, []
    geometry = _geometry_word(layer)
    row = rows.get(geometry) or {}
    out = dict(parameters)
    notes: list[str] = []
    for name, value in (row.get("set") or {}).items():
        if name in parameters and parameters[name] not in (None, ""):
            continue
        try:
            if alg is not None and alg.parameterDefinition(name) is None:
                continue
        except Exception:  # noqa: BLE001  # nosec B112
            continue
        out[name] = value
        undo = str(not value).lower() if isinstance(value, bool) else "another value"
        notes.append(f"{name} {str(value).lower()} by default: '{layer.name()}' is a {geometry} layer and "
                     f"{row['reason']}. Send {name} {undo} {row['undo']}.")
    return out, notes





_NODATA_DEFAULT = -9999.0
_SIGNED_TYPES = ("Int8", "Int16", "Int32", "Float32", "Float64")
_INT_RANGES = {"Int8": (-128.0, 127.0), "Int16": (-32768.0, 32767.0), "Int32": (-2147483648.0, 2147483647.0)}


def _raster_of(value):

    from qgis.core import QgsProject, QgsRasterLayer

    if isinstance(value, QgsRasterLayer):
        return value
    if not isinstance(value, str) or not value:
        return None
    layer = QgsProject.instance().mapLayer(value)
    if layer is None:
        found = QgsProject.instance().mapLayersByName(value)
        layer = found[0] if found else None
    if layer is None and os.path.isfile(value.split("|", 1)[0]):
        layer = QgsRasterLayer(value, "nodata probe", "gdal")
    return layer if isinstance(layer, QgsRasterLayer) and layer.isValid() else None


def _declared_nodata(layer, band: int = 1):

    try:
        provider = layer.dataProvider()
        if provider.sourceHasNoDataValue(band) and provider.useSourceNoDataValue(band):
            return float(provider.sourceNoDataValue(band))
        user = provider.userNoDataValues(band)
        if user:
            return float(user[0].min())
    except Exception:  # noqa: BLE001
        return None
    return None


def _type_name(layer) -> str:

    try:
        from qgis.core import Qgis

        from ..core.qt_compat import enum_member

        kind = layer.dataProvider().dataType(1)
        for name in ("Byte", "Int8", "UInt16", "Int16", "UInt32", "Int32", "Float32", "Float64"):
            member = enum_member(Qgis, "DataType", name, None)
            if member is not None and kind == member:
                return name
    except Exception:  # noqa: BLE001
        return ""
    return ""



_GDAL_TYPES = ("Byte", "Int8", "UInt16", "Int16", "UInt32", "Int32", "UInt64", "Int64", "Float32", "Float64",
               "CInt16", "CInt32", "CFloat32", "CFloat64")


def _output_type(alg, parameters: dict, layer) -> str:





    try:
        definition = alg.parameterDefinition("DATA_TYPE") if alg is not None else None
        value = parameters.get("DATA_TYPE")
        if definition is not None:
            if value in (None, ""):
                value = definition.defaultValue()
            index = int(value) if value not in (None, "") else 0
            options = list(definition.options())
            if 0 <= index < len(options) and str(options[index]).strip() in _GDAL_TYPES:
                return str(options[index]).strip()
    except Exception:  # noqa: BLE001  # nosec B110
        pass
    return _type_name(layer) if layer is not None else ""


def _default_nodata_for(type_name: str, layer):


    if type_name not in _SIGNED_TYPES:
        return None
    low, high = _INT_RANGES.get(type_name, (-math.inf, math.inf))
    if layer is not None:
        from .processing_guards import _nodata_sentinel

        sentinel = _nodata_sentinel(layer, 1)
        if sentinel is not None and low <= sentinel <= high:
            return sentinel
    return _NODATA_DEFAULT if low <= _NODATA_DEFAULT <= high else low


def _plain_number(value: float) -> str:
    return f"{value:g}"


def _defines(alg, name: str) -> bool:
    try:
        return alg is None or alg.parameterDefinition(name) is not None
    except Exception:  # noqa: BLE001
        return False


def _takes_several(alg, name: str) -> bool:

    try:
        definition = alg.parameterDefinition(name) if alg is not None else None
        return definition is not None and str(definition.type()) == "multilayer"
    except Exception:  # noqa: BLE001
        return False


def raster_nodata_defaults(alg, algorithm_id: str, parameters: dict) -> tuple[dict, list[str]]:



    row = _rules("nodata_outputs").get(str(algorithm_id or "").casefold())
    if not row:
        return parameters, []
    key, cells = row["parameter"], row["cells"]
    if not _defines(alg, key):
        return parameters, []
    out = dict(parameters)
    notes: list[str] = []
    if _takes_several(alg, "INPUT"):
        inputs = parameters.get("INPUT")
        inputs = inputs if isinstance(inputs, (list, tuple)) else [inputs]
        layers = [_raster_of(item) for item in inputs[:50]]
        if not layers or any(layer is None for layer in layers):
            return parameters, []
        declared = {_declared_nodata(layer) for layer in layers}
        reads_nodata = _defines(alg, "NODATA_INPUT")
        if reads_nodata and parameters.get("NODATA_INPUT") in (None, "") and len(declared) == 1 \
                and None not in declared:
            value = declared.pop()
            out["NODATA_INPUT"] = value
            notes.append(f"NODATA_INPUT {_plain_number(value)} by default: every tile declares NoData "
                         f"{_plain_number(value)}. NODATA_INPUT with another value changes it.")
            if parameters.get(key) in (None, ""):
                out[key] = value
                notes.append(f"{key} {_plain_number(value)} by default, the tiles' own NoData, so "
                             f"{cells} stay NoData. {key} with another value changes it.")
            return out, notes
        if declared != {None} or parameters.get(key) not in (None, ""):
            return out, notes
        sample = layers[0]
        from .processing_guards import _nodata_sentinel

        markers = {_nodata_sentinel(layer, 1) for layer in layers}
        if reads_nodata and parameters.get("NODATA_INPUT") in (None, "") and len(markers) == 1 \
                and None not in markers:
            marker = markers.pop()
            out["NODATA_INPUT"] = marker
            notes.append(f"NODATA_INPUT {_plain_number(marker)} by default: every tile's minimum is "
                         f"{_plain_number(marker)}, declared by none. NODATA_INPUT with another value keeps "
                         f"{_plain_number(marker)} as a real measurement.")
        who = "the tiles declare"
    else:
        if parameters.get(key) not in (None, ""):
            return parameters, []
        sample = _raster_of(parameters.get("INPUT"))
        if sample is None or _declared_nodata(sample) is not None:

            return parameters, []
        who = f"'{sample.name()}' declares"
    type_name = _output_type(alg, parameters, sample)
    value = _default_nodata_for(type_name, sample)
    if value is None:
        return out, notes
    out[key] = value
    notes.append(f"{key} {_plain_number(value)} by default: {who} no NoData, so "
                 f"{cells} would be stored as 0 and read as real values. {key} with another value "
                 f"changes it.")
    return out, notes









_GRID_PARAMETERS = ("EXTENT", "CELL_SIZE", "CRS")
_GDAL_CALC_INPUTS = ("INPUT_A", "INPUT_B", "INPUT_C", "INPUT_D", "INPUT_E", "INPUT_F")


def _cell(layer) -> tuple[float, float]:
    return abs(float(layer.rasterUnitsPerPixelX())), abs(float(layer.rasterUnitsPerPixelY()))


def _same_grid(layer, reference, crs_only: bool = False) -> bool:
    if layer.crs() != reference.crs():
        return False
    if crs_only:
        return True
    (cx, cy), (rx, ry) = _cell(layer), _cell(reference)
    if abs(cx - rx) > rx * 1e-6 or abs(cy - ry) > ry * 1e-6:
        return False
    a, b = layer.extent(), reference.extent()
    tolerance = min(rx, ry) / 100.0
    return all(abs(p - q) <= tolerance for p, q in (
        (a.xMinimum(), b.xMinimum()), (a.xMaximum(), b.xMaximum()),
        (a.yMinimum(), b.yMinimum()), (a.yMaximum(), b.yMaximum())))


def _grid_words(layer) -> str:

    crs = layer.crs()
    unit = ""
    try:
        from qgis.core import QgsUnitTypes

        unit = str(QgsUnitTypes.toAbbreviatedString(crs.mapUnits()))
    except Exception:  # noqa: BLE001
        unit = ""
    size = _cell(layer)[0]
    return (f"'{layer.name()}' ({crs.authid() or crs.description() or 'custom CRS'}, "
            f"{size:.4g}{(' ' + unit) if unit else ''} cells)")


def _warped_input(layer, reference, key: str, whole_grid: bool, stem: str = "") -> str:


    if layer.providerType() != "gdal":
        return ""
    try:
        from osgeo import gdal
    except ImportError:
        return ""
    from ..core import vsi
    from ..core.policy import create_managed_temp_dir

    ext = reference.extent()
    cx, cy = _cell(reference)
    options = {"format": "VRT", "dstSRS": reference.crs().toWkt(), "resampleAlg": "near"}
    if whole_grid:
        options.update(outputBounds=(ext.xMinimum(), ext.yMinimum(), ext.xMaximum(), ext.yMaximum()),
                       width=int(reference.width()), height=int(reference.height()))
    else:
        options.update(xRes=cx, yRes=cy)
    declared = _declared_nodata(layer)
    fill = declared if declared is not None else _default_nodata_for(_type_name(layer), layer)
    if fill is not None:
        options["dstNodata"] = fill
    elif whole_grid:


        options.update(outputType=gdal.GDT_Float32, dstNodata=_NODATA_DEFAULT)
    if not stem:
        stem = key.lower() + "_" + (re.sub(r"[^A-Za-z0-9_-]+", "_", layer.name())[:40].strip("_") or "raster")
    path = os.path.join(create_managed_temp_dir("aligned_inputs"), f"{stem}.vrt")
    try:
        with vsi.scoped_read(gdal):
            dataset = gdal.Warp(path, str(layer.source()), options=gdal.WarpOptions(**options))
            made = dataset is not None
            del dataset
    except Exception:  # noqa: BLE001
        return ""
    return path if made and os.path.isfile(path) else ""


def calculator_inputs_on_grid(layers, reference) -> dict:









    from ..core.output_paths import safe_file_name

    out = {}
    for layer in layers:
        name = str(layer.name())
        if (layer.id() in out or _same_grid(layer, reference) or _declared_nodata(layer) is not None
                or len(name) > 80 or safe_file_name(name, "") != name):
            continue
        path = _warped_input(layer, reference, "LAYERS", whole_grid=True, stem=name)
        if path:
            out[layer.id()] = path
    return out


def align_raster_grids(alg, algorithm_id: str, parameters: dict) -> tuple[dict, list[str], dict | None]:




    family = _rules("grids").get(str(algorithm_id or "").casefold())
    why = str(_rules("grid_reasons").get(family) or "")
    if family in ("grid_parameters", "grid_parameters_in_place"):
        try:
            if alg is not None and any(alg.parameterDefinition(k) is None for k in _GRID_PARAMETERS):
                return parameters, [], None
        except Exception:  # noqa: BLE001
            return parameters, [], None
        if any(parameters.get(k) not in (None, "", 0, 0.0) for k in _GRID_PARAMETERS):
            return parameters, [], None
        value = parameters.get("LAYERS")
        layers = [_raster_of(item) for item in (value if isinstance(value, (list, tuple)) else [value])]
        if len(layers) < 2 or any(layer is None for layer in layers):
            return parameters, [], None
        reference, others = layers[0], [layer for layer in layers[1:] if not _same_grid(layer, layers[0])]
        if not others:
            return parameters, [], None
        ext, authid = reference.extent(), reference.crs().authid()
        out = dict(parameters)
        items = list(value) if isinstance(value, (list, tuple)) else [value]


        warped = {} if family == "grid_parameters_in_place" else calculator_inputs_on_grid(others, reference)
        for index, layer in enumerate(layers[1:], start=1):
            if layer.id() in warped:
                items[index] = warped[layer.id()]
        out["LAYERS"] = items
        out["EXTENT"] = (f"{ext.xMinimum()},{ext.xMaximum()},{ext.yMinimum()},{ext.yMaximum()}"
                         + (f" [{authid}]" if authid else ""))
        out["CELL_SIZE"] = _cell(reference)[0]
        if authid:
            out["CRS"] = authid
        return out, [(f"EXTENT, CELL_SIZE and CRS set to the grid of {_grid_words(reference)} by default: "
                      f"{', '.join(_grid_words(layer) for layer in others)} "
                      f"{'is' if len(others) == 1 else 'are'} read onto it, nearest neighbour. {why} "
                      f"Send EXTENT, CELL_SIZE and CRS to use another grid.").replace("  ", " ")], None
    if family == "warp_inputs":
        keys = [k for k in _GDAL_CALC_INPUTS if parameters.get(k) not in (None, "")]
        layers = {k: _raster_of(parameters.get(k)) for k in keys}
        if len(keys) < 2 or layers.get(keys[0]) is None:
            return parameters, [], None
        reference, out, notes = layers[keys[0]], dict(parameters), []
        for key in keys[1:]:
            layer = layers[key]
            if layer is None or _same_grid(layer, reference):
                continue
            path = _warped_input(layer, reference, key, whole_grid=True)
            if not path:
                continue
            out[key] = path
            notes.append(f"{key} {_grid_words(layer)} read on the grid of {keys[0]} {_grid_words(reference)}, "
                         f"{reference.width()} by {reference.height()} cells, through a warped virtual raster "
                         f"(nearest neighbour). {why} Send inputs already on one grid to choose another."
                         .replace("  ", " "))
        return out, notes, None
    if family in ("reproject_inputs", "refuse_mixed_crs"):
        value = parameters.get("INPUT")
        items = list(value) if isinstance(value, (list, tuple)) else [value]
        layers = [_raster_of(item) for item in items[:200]]
        if len(layers) < 2 or layers[0] is None:
            return parameters, [], None
        reference = layers[0]
        foreign = [i for i, layer in enumerate(layers) if layer is not None and not _same_grid(
            layer, reference, crs_only=True)]
        if not foreign:
            return parameters, [], None
        if family == "refuse_mixed_crs":
            names = ", ".join(_grid_words(layers[i]) for i in foreign)
            return parameters, [], tool_error(
                f"{names} {'is' if len(foreign) == 1 else 'are'} not in the CRS of the first input, "
                f"{_grid_words(reference)}.", "INVALID_ARGS",
                hint="grid_mixed_crs", algorithm=str(algorithm_id), inputs=names,
                first=_grid_words(reference), crs=reference.crs().authid() or "the first input CRS",
                count=len(foreign))
        out_items, notes = list(items), []
        for i in foreign:
            path = _warped_input(layers[i], reference, f"input{i + 1}", whole_grid=False)
            if not path:
                continue
            out_items[i] = path
            notes.append(f"INPUT {_grid_words(layers[i])} reprojected into the CRS of {_grid_words(reference)} "
                         f"through a warped virtual raster (nearest neighbour). {why}".strip())
        out = dict(parameters)
        out["INPUT"] = out_items if isinstance(value, (list, tuple)) else out_items[0]
        return out, notes, None
    return parameters, [], None


_PARAM_ERROR_RE = re.compile(r"Incorrect parameter value for|Missing parameter value for|Invalid value for parameter")





_FIELD_EXISTS_RE = re.compile(r"[Cc]annot create field (?P<field>[^\s.,]+)[^.]*\.\s*"
                              r"A field with the same name already exists", re.DOTALL)
_LAYER_ERROR_RE = re.compile(r"Could not load source layer for \w+: (?P<value>.+?) not found")


def _processing_error(algorithm_id: str, detail: str, alg=None) -> dict:




    result = {"_error": detail}


    layer_miss = _LAYER_ERROR_RE.search(detail)
    if layer_miss:
        try:
            from ._layers import layer_not_found
            missing = layer_not_found(layer_miss.group("value").strip())
            hint = f"{missing.get('_error') or ''} {missing.get('suggestion') or ''}"
            result["suggestion"] = hint.strip()
            if missing.get("_suggestions"):
                result["closest_layers"] = missing["_suggestions"]
        except Exception:  # nosec B110
            pass
        return result
    if alg is not None and _PARAM_ERROR_RE.search(detail):
        try:
            definitions = list(alg.parameterDefinitions())
            result["parameters"] = [f"{d.name()} ({d.type()})" for d in definitions]
            result.update(hint="processing_parameter_names", names=", ".join(d.name() for d in definitions))
        except Exception:  # nosec B110
            pass
    return result




__all__ = [
    "_FIELD_EXISTS_RE",
    "_LAYER_ERROR_RE",
    "_goes_to_task",
    "_processing_error",
    "geometry_defaults",
    "raster_nodata_defaults",
    "_threadable",
    "_unsafe_processing_algorithm",
    "main_thread_facts",
]
