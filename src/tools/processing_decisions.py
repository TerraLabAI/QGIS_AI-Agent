# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which processing runs may leave the main thread, and how a failed one is explained."""
from __future__ import annotations

import re

from qgis.core import QgsApplication

from ..core import tuning






_UNSAFE_PROCESSING_ALGORITHMS = frozenset({
    "algorithms:centerlines",
    "algorithms:sinuosity",
})


def _unsafe_algorithms() -> frozenset:
    """The list above widened by a served `checks.unsafe_threading` row, never narrowed."""
    return tuning.check_algs("unsafe_threading", _UNSAFE_PROCESSING_ALGORITHMS)


def _unsafe_processing_algorithm(algorithm_id: str) -> dict | None:
    folded = str(algorithm_id).casefold()
    if folded not in _unsafe_algorithms():
        return None
    if folded == "algorithms:centerlines":
        suggestion = (
            "Use create_polygon_centerlines instead. It snapshots the GeoPackage and runs the "
            "provider in a separate QGIS process."
        )
    else:
        suggestion = (
            "Do not retry this algorithm. Save the project first, duplicate the input layer, "
            "and use the provider manually only if you accept its source edits."
        )
    return {
        "_error": (
            f"{algorithm_id} is blocked because its installed implementation edits the source "
            "layer from Processing and is unsafe for AI Agent execution."
        ),
        "code": "INVALID_ARGS",
        "suggestion": suggestion,
    }




_NO_THREADING_FLAG = 64


def _threadable(alg) -> bool:
    """Whether this algorithm may run in a QgsProcessingAlgRunnerTask, off the main thread."""












    try:
        if str(alg.id()).casefold() in _unsafe_algorithms():
            return False
        if int(alg.flags()) & _NO_THREADING_FLAG:
            return False
        return _model_steps_may_leave(alg)
    except Exception:  # noqa: BLE001 - flags that cannot be read keep the run on the main thread
        return False


def _model_steps_may_leave(alg) -> bool:
    """Whether a model's steps may run inside the model's own task; True for any other algorithm."""














    children = alg.childAlgorithms() if hasattr(alg, "childAlgorithms") else {}
    for child in children.values():
        step = child.algorithm() or QgsApplication.processingRegistry().algorithmById(child.algorithmId())
        if step is None or str(step.id()).casefold() in _unsafe_algorithms():
            return False
        if not _model_steps_may_leave(step):
            return False
    return True


def _goes_to_task(alg, args: dict, parameters: dict) -> bool:
    """A threadable algorithm always goes to the task manager, whatever its size."""














    del args, parameters
    return _threadable(alg)


_PARAM_ERROR_RE = re.compile(r"Incorrect parameter value for|Missing parameter value for|Invalid value for parameter")





_FIELD_EXISTS_RE = re.compile(r"[Cc]annot create field (?P<field>[^\s.,]+)[^.]*\.\s*"
                              r"A field with the same name already exists", re.DOTALL)
_LAYER_ERROR_RE = re.compile(r"Could not load source layer for \w+: (?P<value>.+?) not found")











_SUBPROCESS_IMPORT_RE = re.compile(
    r"ImportError|ModuleNotFoundError|you should not try to import numpy", re.IGNORECASE)






_SUBPROCESS_TRACEBACK_RE = re.compile(
    r"Traceback \(most recent call last\)[\s\S]{0,4000}?(gdal_[\w.]+\.py|osgeo_utils)", re.IGNORECASE)
_NATIVE_TWIN = {
    "gdal:rastercalculator": "native:rastercalc",
    "gdal:merge": "gdal:buildvirtualraster, then gdal:translate",
    "gdal:polygonize": "native:polygonize",
    "gdal:sieve": "native:sieve",
    "gdal:fillnodata": "native:fillnodata",
    "gdal:aspect": "native:aspect",
    "gdal:slope": "native:slope",
    "gdal:hillshade": "native:hillshade",
    "gdal:gridinversedistance": "qgis:idwinterpolation",
}


def _processing_error(algorithm_id: str, detail: str, alg=None) -> dict:
    result = {"_error": detail}
    if _SUBPROCESS_IMPORT_RE.search(detail):


        twin = _NATIVE_TWIN.get(algorithm_id)
        result["suggestion"] = (
            (f"Run {twin} instead." if twin
             else "Run the native: algorithm for this operation instead of the gdal: one.")
            + f" {algorithm_id} is a Python script GDAL runs in another interpreter, and that "
              "interpreter cannot import numpy or osgeo here. The native ones run inside QGIS."
        )
        return result
    if _SUBPROCESS_TRACEBACK_RE.search(detail):
        twin = _NATIVE_TWIN.get(algorithm_id)
        result["suggestion"] = (
            (f"Run {twin} instead." if twin
             else "Run the native: algorithm for this operation instead of the gdal: one.")
            + f" {algorithm_id} died inside the Python script GDAL runs in a separate interpreter, "
              "so the same call fails the same way. The native ones run inside QGIS."
        )
        return result


    layer_miss = _LAYER_ERROR_RE.search(detail)
    if layer_miss:
        try:
            from ._layers import layer_not_found
            missing = layer_not_found(layer_miss.group("value").strip())
            hint = f"{missing.get('_error') or ''} {missing.get('suggestion') or ''}"
            result["suggestion"] = hint.strip()
            if missing.get("_suggestions"):
                result["closest_layers"] = missing["_suggestions"]
        except Exception:  # nosec B110 - hint only
            pass
        return result


    if alg is not None and _PARAM_ERROR_RE.search(detail):
        try:
            result["parameters"] = [f"{d.name()} ({d.type()})" for d in alg.parameterDefinitions()]
            result["suggestion"] = "Use these parameter names exactly: " + ", ".join(
                d.name() for d in alg.parameterDefinitions()
            )
        except Exception:  # nosec B110 - hint only
            pass
    return result




__all__ = [
    "_FIELD_EXISTS_RE",
    "_LAYER_ERROR_RE",
    "_SUBPROCESS_TRACEBACK_RE",
    "_goes_to_task",
    "_processing_error",
    "_threadable",
    "_unsafe_processing_algorithm",
]
