# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""QGIS Processing handlers: run/list/describe algorithms, plus the async task registry (QgsTask-backed) that run_processing(async=true)."""




from __future__ import annotations

import os
import re
import time
import uuid

import processing
from qgis.core import (
    Qgis,
    QgsApplication,
    QgsMapLayer,
    QgsProcessingAlgRunnerTask,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProject,
    QgsRasterLayer,
    QgsTask,
    QgsVectorLayer,
)

from ..core.policy import MAX_LIST_CHARS, create_managed_temp_dir
from ..core.qt_compat import enum_member
from ..core.security import validate_path
from ..core.serialization import cut_string
from .layer_io_tools import _release_layers_at_path, _same_file
from .layer_lookup import _find_layer
from .postconditions import report_checks
from .processing_guards import (
    _distance_sanity,
    _empty_input_check,
    _gdal_format_options,
    _geographic_distance_check,
    _grid_sanity,
    _raster_size_sanity,
    _resolve_layer_inputs,
    _stamp_provenance,
    _unresolved_input_check,
    _window_degrees_repair,
    _window_sanity,
)
from .stac_tools import normalise_crs



_PROCESSING_TASKS: dict[str, dict] = {}

_PARAM_ERROR_RE = re.compile(r"Incorrect parameter value for|Missing parameter value for|Invalid value for parameter")






_FIELD_EXISTS_RE = re.compile(r"[Cc]annot create field (?P<field>[^\s.,]+)[^.]*\.\s*"
                              r"A field with the same name already exists", re.DOTALL)
_LAYER_ERROR_RE = re.compile(r"Could not load source layer for \w+: (?P<value>.+?) not found")











_SUBPROCESS_IMPORT_RE = re.compile(
    r"ImportError|ModuleNotFoundError|you should not try to import numpy", re.IGNORECASE)
_NATIVE_TWIN = {
    "gdal:rastercalculator": "native:rastercalc",
    "gdal:merge": "gdal:buildvirtualraster, then gdal:translate",
    "gdal:polygonize": "native:polygonize",
    "gdal:sieve": "native:sieve",
    "gdal:fillnodata": "native:fillnodata",
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




_GPKG_TABLE_RE = re.compile(r"^(?P<path>.+\.gpkg)\|layername=(?P<table>[^|]+)$", re.IGNORECASE)
_OGR_DBNAME_RE = re.compile(r"^ogr:dbname='(?P<path>[^']+)'\s+table=\"(?P<table>[^\"]+)\"", re.IGNORECASE)


def _gpkg_table_target(value: str):
    """(gpkg_path, table) when ``value`` names a table inside a GeoPackage, else None."""
    text = (value or "").strip()
    match = _GPKG_TABLE_RE.match(text) or _OGR_DBNAME_RE.match(text)
    if not match:
        return None
    return match.group("path"), match.group("table").strip()


def _gpkg_sink_uri(path: str, table: str) -> str | None:
    """``ogr:`` connection string naming this table, or None when it cannot be written."""









    if "'" in (path or "") or '"' in (table or ""):
        return None
    return f"ogr:dbname='{path}' table=\"{table}\" (geom)"


def _destination_names(alg) -> set:
    """Names of the parameters this algorithm writes to."""
    try:
        return {d.name() for d in alg.parameterDefinitions()
                if getattr(d, "isDestination", lambda: False)()}
    except Exception:  # noqa: BLE001 - an algorithm that cannot list its parameters protects everything
        return set()


def _input_file_paths(parameters: dict, destination_names: set) -> set:
    """Existing files the run reads from, whatever the project has open."""






    paths = set()
    for key, value in (parameters or {}).items():
        if key in destination_names:
            continue
        for item in (value if isinstance(value, (list, tuple)) else [value]):
            if not isinstance(item, str) or not item:
                continue
            candidate = _gpkg_table_target(item)
            candidate = candidate[0] if candidate else item.split("|", 1)[0]
            try:
                if candidate and os.path.isfile(candidate):
                    paths.add(candidate)
            except (OSError, ValueError):
                continue
    return paths


def _release_table_layers(path: str, table: str, skip_ids=None) -> list:
    """Remove the project layers reading exactly this table, and only those, so the sink can overwrite it while the other tables of the GeoPackage."""

    removed = []
    skip_ids = set(skip_ids or ())
    project = QgsProject.instance()
    wanted = f"layername={table}".lower()
    for layer_id, layer in list(project.mapLayers().items()):
        if layer_id in skip_ids:
            continue
        parts = (layer.source() or "").split("|")
        if not parts[0] or not os.path.exists(path) or not _same_file(parts[0], path):
            continue
        if any(p.strip().lower() == wanted for p in parts[1:]):
            removed.append(layer.name())
            project.removeMapLayer(layer_id)
    return removed


def _has_proj_db(folder: str) -> bool:
    """Whether ``folder`` is a PROJ data folder, rather than only named one."""
    try:
        return bool(folder) and os.path.isfile(os.path.join(folder, "proj.db"))
    except (OSError, ValueError):
        return False


def _ensure_proj_env() -> None:
    """GDAL algorithms run gdalwarp and friends as child processes, which need PROJ_LIB to find proj.db; the QGIS process itself knows the path."""










    if _has_proj_db(os.environ.get("PROJ_DATA", "")) or _has_proj_db(os.environ.get("PROJ_LIB", "")):
        return
    try:
        from osgeo import osr
        paths = list(osr.GetPROJSearchPaths())
    except Exception:  # nosec B110 - environment hint only
        return
    for candidate in paths:
        if os.path.isfile(os.path.join(candidate, "proj.db")):
            os.environ["PROJ_LIB"] = candidate
            return




_OPTIONAL_PROVIDERS = {
    "grass": "GRASS", "grass7": "GRASS", "saga": "SAGA", "sagang": "SAGA",
    "pdal": "PDAL", "otb": "Orfeo Toolbox", "r": "R",
}


def _provider_hint(algorithm_id: str) -> dict:
    """Why this id resolves to nothing on this machine, when the reason is local."""









    text = str(algorithm_id or "").strip()
    registry = QgsApplication.processingRegistry()
    try:
        counts: dict = {}
        for alg in registry.algorithms():
            counts[alg.id().split(":", 1)[0]] = counts.get(alg.id().split(":", 1)[0], 0) + 1
        registered = {p.id() for p in registry.providers()}
    except Exception:  # noqa: BLE001 - a registry that will not answer gives no hint
        return {}
    if ":" not in text:
        return {"suggestion": "An algorithm id is provider:name, for example native:buffer. "
                              "Providers here: " + ", ".join(sorted(counts)) + "."}
    prefix, name = text.split(":", 1)
    if prefix in registered and not counts.get(prefix):
        product = _OPTIONAL_PROVIDERS.get(prefix, prefix)
        return {"suggestion": f"The {prefix} provider is loaded but offers no algorithms in this "
                              f"QGIS, which means {product} was not installed with it. Use an "
                              f"algorithm from: " + ", ".join(sorted(counts)) + "."}
    if prefix not in registered:
        product = _OPTIONAL_PROVIDERS.get(prefix)
        if product:
            return {"suggestion": f"There is no {prefix} provider in this QGIS. {product} is "
                                  "optional: it has to be installed, and its provider plugin "
                                  "enabled in the Plugin Manager. Providers here: "
                                  + ", ".join(sorted(counts)) + "."}
        return {"suggestion": "No provider is called " + prefix + " here. Providers: "
                              + ", ".join(sorted(counts)) + "."}

    import difflib
    pool = [a.id() for a in registry.algorithms() if a.id().startswith(prefix + ":")]
    close = difflib.get_close_matches(text, pool, n=3, cutoff=0.6) or \
        difflib.get_close_matches(name, [p.split(":", 1)[1] for p in pool], n=3, cutoff=0.6)
    if close:
        close = [c if ":" in c else f"{prefix}:{c}" for c in close]
        return {"suggestion": "Closest ids in " + prefix + ": " + ", ".join(close) + ".",
                "closest_algorithms": close}
    return {}


def _missing_destinations(alg, parameters: dict) -> list:
    """Destination parameters that name a file which does not exist after the run."""
    missing = []
    for definition in alg.parameterDefinitions():
        if not getattr(definition, "isDestination", lambda: False)():
            continue
        value = parameters.get(definition.name())
        if not isinstance(value, str) or value in ("", "memory:", "TEMPORARY_OUTPUT"):
            continue
        target = _gpkg_table_target(value)
        path = target[0] if target else value.split("|", 1)[0]
        if not os.path.isabs(path):
            continue
        if not os.path.exists(path):
            missing.append(f"{definition.name()} ({os.path.basename(path)})")
    return missing


class _CapturingFeedback(QgsProcessingFeedback):
    """Feedback that records error/warning text so a failed async task can report the real reason instead of pointing the agent at the message log."""


    def __init__(self):
        super().__init__()
        self.errors: list[str] = []

    def reportError(self, error, fatalError=False):  # noqa: N802 (Qt signature)
        self.errors.append(str(error))
        super().reportError(error, fatalError)

    def pushWarning(self, warning):  # noqa: N802 (Qt signature)
        self.errors.append(str(warning))
        super().pushWarning(warning)


def _inject_gdal_crs(alg, params: dict) -> None:
    """Fill SOURCE_CRS / TARGET_CRS for GDAL algorithms to avoid WKT mismatch."""
    crs_param_names = {d.name() for d in alg.parameterDefinitions() if d.name().upper().endswith("_CRS")}
    if not crs_param_names:
        return

    already_set = {k for k in crs_param_names if params.get(k)}
    if already_set == crs_param_names:
        return

    collected_authid = None
    project = QgsProject.instance()
    for definition in alg.parameterDefinitions():
        if getattr(definition, "isDestination", lambda: False)():
            continue
        val = params.get(definition.name())
        if not isinstance(val, str) or not val:
            continue
        layer = project.mapLayer(val) or project.mapLayersByName(val)
        if isinstance(layer, list):
            layer = layer[0] if layer else None
        if layer and hasattr(layer, "crs") and layer.crs().isValid():
            collected_authid = layer.crs().authid()
            break

    if not collected_authid:
        collected_authid = project.crs().authid() if project.crs().isValid() else None

    if collected_authid:
        for name in crs_param_names:
            if not params.get(name):
                params[name] = collected_authid


def _record_history(alg, parameters: dict, context) -> dict:
    """Write the run to the QGIS Processing history, as the toolbox does."""






    provenance = {"algorithm": alg.id(), "command": ""}
    try:
        provenance["command"] = str(alg.asPythonCommand(parameters, context))
    except Exception:  # nosec B110 - the stamp then names the algorithm only
        pass
    try:
        from qgis.gui import QgsGui, QgsHistoryEntry
        from qgis.PyQt.QtCore import QDateTime

        entry = QgsHistoryEntry("processing", QDateTime.currentDateTime(), {
            "python_command": provenance["command"],
            "algorithm_id": alg.id(),
            "parameters": alg.asMap(parameters, context),
        })
        QgsGui.historyProviderRegistry().addEntry(entry)
    except Exception:  # nosec B110 - history is a convenience, the run already succeeded
        pass
    return provenance


def _inputs_as_layers(alg, parameters: dict, message: str) -> dict | None:
    """``parameters`` with every input string that names an open layer replaced by the layer."""













    if not _LAYER_ERROR_RE.search(message or ""):
        return None
    try:
        destinations = {d.name() for d in alg.parameterDefinitions()
                        if getattr(d, "isDestination", lambda: False)()}
    except Exception:  # noqa: BLE001 - an algorithm that cannot list its parameters gets no retry
        return None
    out = dict(parameters)
    changed = False
    for key, value in parameters.items():
        if key in destinations or not isinstance(value, str):
            continue
        layer = _find_layer(value)
        if layer is None:
            continue
        out[key] = layer
        changed = True
    return out if changed else None


def _run_processing(args: dict) -> dict:
    algorithm_id = args["algorithm_id"]
    parameters = args.get("parameters", {})

    alg = QgsApplication.processingRegistry().algorithmById(algorithm_id)
    if not alg:



        return {"_error": f"Algorithm not found: {algorithm_id}", "code": "INVALID_ARGS",
                **_provider_hint(algorithm_id)}




    parameters, _resolved_inputs = _resolve_layer_inputs(parameters)

    in_degrees = _geographic_distance_check(alg, parameters, bool(args.get("confirm_large")))
    if in_degrees:
        return in_degrees
    suspicious = _distance_sanity(parameters, bool(args.get("confirm_large")))
    if suspicious:
        return suspicious

    too_many = _grid_sanity(parameters, bool(args.get("confirm_large")))
    if too_many:
        return too_many
    too_big = _raster_size_sanity(parameters, bool(args.get("confirm_large")), algorithm_id)
    if too_big:
        return too_big
    window_repairs = _window_degrees_repair(parameters)
    off_raster = _window_sanity(parameters)
    if off_raster:
        return off_raster



    unresolved = _unresolved_input_check(alg, parameters)
    if unresolved:
        return unresolved


    empty = _empty_input_check(alg, parameters)
    if empty:
        return empty

    sanitized_parameters = dict(parameters)


    managed_output_dir = None
    released = []
    destination_names = _destination_names(alg)


    input_ids = set()
    for key, value in parameters.items():
        if key in destination_names:
            continue
        for item in (value if isinstance(value, (list, tuple)) else [value]):
            if isinstance(item, str):
                found = _find_layer(item)
                if found is not None:
                    input_ids.add(found.id())


    input_paths = _input_file_paths(parameters, destination_names)

    def _protects_input(path: str) -> bool:
        return any(_same_file(path, other) for other in input_paths)




    plans: list[tuple] = []
    try:
        for definition in alg.parameterDefinitions():
            name = definition.name()
            if not getattr(definition, "isDestination", lambda: False)():
                continue

            current_value = sanitized_parameters.get(name)
            if current_value in (None, "", "TEMPORARY_OUTPUT"):
                plans.append(("temp", name, definition))
                continue

            if isinstance(current_value, str) and current_value != "memory:":
                table_target = _gpkg_table_target(current_value)
                if table_target is not None:
                    gpkg_path, table_name = table_target
                    path_error = validate_path(gpkg_path, write=True)
                    if path_error:
                        return {"_error": path_error}
                    sink_uri = _gpkg_sink_uri(gpkg_path, table_name)
                    if sink_uri is None:
                        return {"_error": (
                            f"{name}: a GeoPackage path cannot contain a single quote and a table name "
                            f"cannot contain a double quote, because the output URI uses them as "
                            f"delimiters. Write to a different folder or table name."),
                            "code": "INVALID_ARGS"}
                    plans.append(("table", name, (gpkg_path, table_name, sink_uri)))
                    continue
                looks_like_path = (
                    os.path.isabs(current_value)
                    or current_value.startswith(("~", ".", ".."))
                    or "/" in current_value
                    or "\\" in current_value
                    or bool(os.path.splitext(current_value)[1])
                )
                if looks_like_path:


                    expanded = os.path.expanduser(current_value)
                    path_error = validate_path(expanded, write=True)
                    if path_error:
                        return {"_error": path_error}
                    plans.append(("file", name, expanded))
    except Exception as e:
        return {"_error": f"Failed to validate processing outputs: {e}"}

    try:
        for kind, name, payload in plans:
            if kind == "temp":
                if managed_output_dir is None:
                    managed_output_dir = create_managed_temp_dir("processing")
                ext = ""
                if hasattr(payload, "defaultFileExtension"):
                    try:
                        ext = payload.defaultFileExtension() or ""
                    except Exception:
                        ext = ""
                suffix = f".{ext}" if ext and not str(ext).startswith(".") else str(ext)
                sanitized_parameters[name] = os.path.join(
                    managed_output_dir,
                    f"{name.lower()}{suffix}",
                )
            elif kind == "table":
                gpkg_path, table_name, sink_uri = payload
                if not _protects_input(gpkg_path):
                    released.extend(_release_table_layers(gpkg_path, table_name, skip_ids=input_ids))
                sanitized_parameters[name] = sink_uri
            else:
                sanitized_parameters[name] = payload
                parent = os.path.dirname(payload)
                if parent and not os.path.isdir(parent):
                    os.makedirs(parent, exist_ok=True)
                if not _protects_input(payload):
                    released.extend(_release_layers_at_path(payload, skip_ids=input_ids, delete_existing=True))
    except Exception as e:
        return {"_error": f"Failed to prepare processing outputs: {e}"}

    feedback = _CapturingFeedback()
    context = QgsProcessingContext()
    context.setProject(QgsProject.instance())
    invalid_geometry_filter = args.get("invalid_geometry_filter", "default")
    if invalid_geometry_filter == "skip":
        check = getattr(getattr(Qgis, "InvalidGeometryCheck", object), "GeometrySkipInvalid", None)
        if check is None:
            return {"_error": "This QGIS version cannot skip invalid geometries through Processing."}
        context.setInvalidGeometryCheck(check)
    elif invalid_geometry_filter == "abort":
        check = getattr(getattr(Qgis, "InvalidGeometryCheck", object), "GeometryAbortOnInvalid", None)
        if check is not None:
            context.setInvalidGeometryCheck(check)




    repairs: list[str] = list(window_repairs)
    if algorithm_id.startswith("gdal:"):
        _ensure_proj_env()
        _inject_gdal_crs(alg, sanitized_parameters)


        repairs += _gdal_format_options(algorithm_id, sanitized_parameters)








    if bool(args.get("async")) or _heavy_inputs(parameters):
        started = _start_async_processing(
            alg,
            algorithm_id,
            sanitized_parameters,
            args.get("output_name"),
            invalid_geometry_filter,
        )
        if repairs:
            started["repairs"] = repairs
        return started

    try:
        result = processing.run(algorithm_id, sanitized_parameters, feedback=feedback, context=context)
    except Exception as e:


        by_object = _inputs_as_layers(alg, sanitized_parameters, str(e))
        if by_object is not None:
            try:
                result = processing.run(algorithm_id, by_object, feedback=feedback, context=context)
                sanitized_parameters = by_object
            except Exception as retry_error:
                return _processing_error(algorithm_id, f"Processing failed: {retry_error}", alg)





        elif _FIELD_EXISTS_RE.search(str(e)):
            clash = _FIELD_EXISTS_RE.search(str(e)).group("field")
            failure = _processing_error(
                algorithm_id,
                f"The output already carries a field named '{clash}', so it cannot be created again. "
                f"A GeoPackage compares field names without case, which makes 'Population' and "
                f"'population' the same field.", alg)
            failure["suggestion"] = (f"Give the new field another name, or overwrite '{clash}' in place "
                                     f"with native:fieldcalculator on that same field name.")
            return failure
        elif "already exists" not in str(e):
            return _processing_error(algorithm_id, f"Processing failed: {str(e)}", alg)
        else:



            result = None
            last_error = e
            for _attempt in range(2):



                for dest_name in destination_names:
                    value = sanitized_parameters.get(dest_name)
                    for item in (value if isinstance(value, (list, tuple)) else [value]):
                        if not isinstance(item, str) or not os.path.isfile(item) or _protects_input(item):
                            continue
                        released.extend(_release_layers_at_path(item, skip_ids=input_ids, delete_existing=True))
                try:
                    result = processing.run(algorithm_id, sanitized_parameters, feedback=feedback, context=context)
                    break
                except Exception as retry_error:
                    last_error = retry_error
            if result is None:
                return _processing_error(
                    algorithm_id,
                    f"The output file is still held by another process (on Windows, a "
                    f"GeoPackage open in QGIS or another program). Last error: {last_error}. "
                    f"Run again, or pass a new output file name.",
                    alg,
                )



    missing = _missing_destinations(alg, sanitized_parameters)
    if missing:
        detail = f"Processing finished without writing {', '.join(missing)}."
        if feedback.errors:
            detail += " Log: " + " | ".join(" ".join(e.split())[:300] for e in feedback.errors[:3])
        return _processing_error(algorithm_id, detail, alg)

    provenance = _record_history(alg, sanitized_parameters, context)
    out = {"algorithm": algorithm_id,
           "outputs": _process_outputs(result, output_name=args.get("output_name"), provenance=provenance)}
    if repairs:
        out["repairs"] = repairs
    if managed_output_dir is not None:
        out["outputs_note"] = f"unspecified outputs were written to a managed temp dir: {managed_output_dir}"
    if released:
        out["replaced_layers"] = released
    _report_empty_outputs(out)
    _report_log(out, feedback)
    report_checks(out, algorithm_id, sanitized_parameters)
    return out








def _report_empty_outputs(out: dict) -> None:
    """Name the vector outputs that came back with no features."""
    empty = [
        key
        for key, value in (out.get("outputs") or {}).items()
        if isinstance(value, dict) and value.get("feature_count") == 0
    ]
    if empty and len(empty) == len([
        v for v in (out.get("outputs") or {}).values()
        if isinstance(v, dict) and "feature_count" in v
    ]):
        out["empty_outputs"] = empty






_LOG_LINES = 5
_LOG_LINE_CHARS = 300


def _report_log(out: dict, feedback) -> None:
    lines = getattr(feedback, "errors", None) or []
    if not lines:
        return
    out["log"] = [" ".join(line.split())[:_LOG_LINE_CHARS] for line in lines[-_LOG_LINES:]]


_FILE_EXTENSIONS = (".tif", ".tiff", ".gpkg", ".shp", ".geojson", ".json", ".csv", ".kml", ".sqlite", ".vrt",
                    ".asc", ".img", ".nc", ".gml", ".fgb", ".parquet", ".xlsx", ".dbf", ".txt", ".pdf", ".png")


def _looks_like_file(value: str) -> bool:
    return (os.path.splitext(value)[1].lower() in _FILE_EXTENSIONS
            and ("/" in value or "\\" in value or value.startswith("~")))


def _declare_raster_crs(layer) -> None:
    """A derivative of a raster whose WKT names no authority (the hosted LiDAR HD tiles) inherits that WKT, so QGIS reported crs "" on every."""


    if isinstance(layer, QgsRasterLayer):
        try:
            normalise_crs(layer)
        except Exception:  # nosec B110 - a declaration is a courtesy, never a failure
            pass


def _process_outputs(result_map: dict, context=None, output_name=None, provenance=None) -> dict:
    """Add output layers to the project and return a JSON-safe outputs summary."""







    output = {}
    added = []
    for key, value in (result_map or {}).items():
        if isinstance(value, QgsMapLayer):
            _declare_raster_crs(value)
            QgsProject.instance().addMapLayer(value)
            added.append((key, value))
            output[key] = _layer_summary(value)
        elif isinstance(value, str) and "|layername=" in value and os.path.exists(value.split("|", 1)[0]):

            file_part = value.split("|", 1)[0]
            path_error = validate_path(file_part, write=False)
            if path_error:
                output[key] = {"path": value, "warning": path_error}
                continue
            table = value.split("|layername=", 1)[1].split("|", 1)[0]
            layer = QgsVectorLayer(value, table, "ogr")
            if layer.isValid():
                QgsProject.instance().addMapLayer(layer)
                added.append((key, layer))
                output[key] = _layer_summary(layer, value)
            else:
                output[key] = {"path": value}
        elif isinstance(value, str) and os.path.exists(value):
            path_error = validate_path(value, write=False)
            if path_error:
                output[key] = {"path": value, "warning": path_error}
                continue
            layer = QgsVectorLayer(value, os.path.basename(value), "ogr")
            if layer.isValid():
                QgsProject.instance().addMapLayer(layer)
                added.append((key, layer))
                output[key] = _layer_summary(layer, value)
            else:
                rlayer = QgsRasterLayer(value, os.path.basename(value))
                if rlayer.isValid():
                    _declare_raster_crs(rlayer)
                    QgsProject.instance().addMapLayer(rlayer)
                    added.append((key, rlayer))
                    output[key] = _layer_summary(rlayer, value)
                else:
                    output[key] = {"path": value}
        else:
            layer = None
            if context is not None and isinstance(value, str):
                try:
                    layer = context.takeResultLayer(value)
                except Exception:
                    layer = None
            if layer is not None and layer.isValid():
                _declare_raster_crs(layer)
                QgsProject.instance().addMapLayer(layer)
                added.append((key, layer))
                output[key] = _layer_summary(layer)
            elif isinstance(value, str) and _looks_like_file(value) and not os.path.exists(os.path.expanduser(value)):



                output[key] = {"path": value, "missing": True,
                               "warning": "the algorithm finished without writing this file; read log"}
            else:



                output[key] = _plain_output(value)
    added = _drop_leftovers(added, output)
    name = str(output_name or "").strip()
    if name and added:
        from ._layers import unique_layer_name

        for key, layer in added:
            new_name = name if (len(added) == 1 or key == "OUTPUT") else f"{name} {key.lower()}"


            new_name = unique_layer_name(new_name, keep_id=layer.id())
            layer.setName(new_name)
            output[key]["layer_name"] = new_name
    if provenance and added:
        for _key, layer in added:
            _stamp_provenance(layer, provenance)
    return output





LEFTOVER_OUTPUTS = frozenset({
    "FAIL_OUTPUT", "NON_MATCHING", "OUTPUT_NON_MATCHING", "NON_MATCHING_OUTPUT",
    "UNMATCHED", "DUPLICATES", "ERROR_OUTPUT", "INVALID_OUTPUT", "INVALID_FEATURES",
})


def _drop_leftovers(added: list, output: dict) -> list:
    """Keep the leftovers out of the layer tree, and report them as counts."""












    if len(added) < 2:
        return added
    primary = [key for key, _layer in added if key not in LEFTOVER_OUTPUTS]
    if not primary:
        return added
    kept = []
    for key, layer in added:
        if key not in LEFTOVER_OUTPUTS:
            kept.append((key, layer))
            continue
        summary = output.get(key)
        try:
            QgsProject.instance().removeMapLayer(layer.id())
        except Exception:  # noqa: BLE001 - a layer that will not leave stays, it is only clutter
            kept.append((key, layer))
            continue
        if isinstance(summary, dict):
            summary["added_to_project"] = False
            summary.pop("layer_id", None)
            summary["note"] = "Not added to the project: these are the features the algorithm did not keep."
    return kept


def _layer_summary(layer, path: str | None = None) -> dict:
    """What the model needs about an output layer: id, name, crs, count, path."""
    out = {"layer_id": layer.id(), "layer_name": layer.name(), "crs": layer.crs().authid(),
           "added_to_project": True}
    if isinstance(layer, QgsVectorLayer):
        out["feature_count"] = layer.featureCount()
    if path:
        out["path"] = path
    return out


def _plain_output(value):
    if isinstance(value, str):
        return cut_string(value)
    if isinstance(value, dict):
        return cut_string(str(value)) if len(str(value)) > 2_000 else value
    return value


def _sweep_consumed_tasks():
    """Drop terminal task entries that were already read via get_task_status more than 10 minutes ago, so _PROCESSING_TASKS does not grow."""

    now = time.time()
    stale = [
        tid for tid, e in _PROCESSING_TASKS.items() if e.get("_consumed") and (now - e.get("_consumed_at", now)) > 600
    ]
    for tid in stale:
        _PROCESSING_TASKS.pop(tid, None)







_ASYNC_FEATURES = 1_000







_ASYNC_PIXELS = 16_000_000




_POLL_INTERVAL_S = 1.0







_ASYNC_FILE_BYTES = 50 * 1024 * 1024


def _size_of(layer) -> tuple[int, int]:
    """(features, pixels) a layer reports without being read: one of the two is zero."""




    features = pixels = 0
    try:
        if hasattr(layer, "width") and hasattr(layer, "height") and not hasattr(layer, "featureCount"):
            pixels = int(layer.width()) * int(layer.height())
        elif hasattr(layer, "featureCount"):
            features = int(layer.featureCount())
    except Exception:  # noqa: BLE001 - a layer that cannot measure itself is not the reason to wait
        return 0, 0
    return max(features, 0), max(pixels, 0)


def _file_is_big(value: str) -> bool:
    """True when this parameter names a file on disk over ``_ASYNC_FILE_BYTES``."""





    path = value.split("|", 1)[0].strip()
    if not path or path.startswith(("/vsi", "memory:")) or "://" in path:
        return False
    try:
        return os.path.getsize(os.path.expanduser(path)) > _ASYNC_FILE_BYTES
    except OSError:
        return False


def _heavy_inputs(parameters: dict) -> bool:
    """True when the inputs are big enough that running here would freeze QGIS."""











    features = pixels = 0
    for value in (parameters or {}).values():
        for item in (value if isinstance(value, (list, tuple)) else [value]):
            layer = item if hasattr(item, "featureCount") or hasattr(item, "width") else (
                _find_layer(item) if isinstance(item, str) else None)
            if layer is None:
                if isinstance(item, str) and _file_is_big(item):
                    return True
                continue
            found_features, found_pixels = _size_of(layer)
            features += found_features
            pixels += found_pixels
            if features > _ASYNC_FEATURES or pixels > _ASYNC_PIXELS:
                return True
    return False


def _start_async_processing(
    alg,
    algorithm_id: str,
    parameters: dict,
    output_name=None,
    invalid_geometry_filter="default",
) -> dict:
    _sweep_consumed_tasks()
    task_id = "proc-" + uuid.uuid4().hex[:12]
    context = QgsProcessingContext()



    context.setProject(QgsProject.instance())
    if invalid_geometry_filter == "skip":
        check = getattr(getattr(Qgis, "InvalidGeometryCheck", object), "GeometrySkipInvalid", None)
        if check is not None:
            context.setInvalidGeometryCheck(check)
    elif invalid_geometry_filter == "abort":
        check = getattr(getattr(Qgis, "InvalidGeometryCheck", object), "GeometryAbortOnInvalid", None)
        if check is not None:
            context.setInvalidGeometryCheck(check)
    feedback = _CapturingFeedback()
    task = QgsProcessingAlgRunnerTask(alg, parameters, context, feedback)
    _PROCESSING_TASKS[task_id] = {
        "status": "running",
        "progress": 0,
        "algorithm": algorithm_id,
        "started_at": time.strftime("%H:%M:%S"),
        "task": task,
        "context": context,
        "feedback": feedback,
        "output_name": output_name,
        "alg": alg,
        "parameters": parameters,
    }
    task.progressChanged.connect(lambda p, tid=task_id: _on_proc_progress(tid, p))
    task.executed.connect(lambda ok, results, tid=task_id, ctx=context: _on_proc_done(tid, ok, results, ctx))






    task.taskTerminated.connect(lambda tid=task_id: _on_proc_terminated(tid))
    QgsApplication.taskManager().addTask(task)
    return {
        "task_id": task_id,
        "status": "running",
        "algorithm": algorithm_id,
        "note": "Running in the background, QGIS stays responsive. Poll get_task_status(task_id).",
        "poll": {"tool": "get_task_status", "args": {"task_id": task_id},
                 "interval_s": _POLL_INTERVAL_S, "label": f"Running {algorithm_id}"},
    }


def register_task(task, label: str, connect=None) -> tuple:
    """Put an already-built ``QgsTask`` under the registry get_task_status reads."""











    _sweep_consumed_tasks()
    task_id = "task-" + uuid.uuid4().hex[:12]
    entry = {
        "status": "running",
        "progress": 0,
        "algorithm": label,
        "started_at": time.strftime("%H:%M:%S"),
        "task": task,
    }
    _PROCESSING_TASKS[task_id] = entry
    try:
        task.progressChanged.connect(lambda value, tid=task_id: _on_proc_progress(tid, value))
    except (AttributeError, TypeError):
        pass
    try:
        task.taskTerminated.connect(lambda tid=task_id: _on_proc_terminated(tid))
    except (AttributeError, TypeError):
        pass
    if connect is not None:
        connect(task_id, entry)
    QgsApplication.taskManager().addTask(task)
    return task_id, entry


def _on_proc_terminated(task_id: str):
    """A task that failed or was killed without ever emitting ``executed``."""





    entry = _PROCESSING_TASKS.get(task_id)
    if entry is None or entry.get("status") != "running":
        return
    feedback = entry.get("feedback")
    captured = "; ".join(getattr(feedback, "errors", [])[-5:]) if feedback else ""
    entry["status"] = "error"
    entry["error"] = (
        f"The algorithm stopped before it produced anything: {captured}"
        if captured
        else ("The algorithm stopped before it produced anything, most often an input that names no "
              "layer. list_layers gives the names the project actually holds.")
    )
    for heavy in ("task", "context", "feedback", "alg", "parameters"):
        entry.pop(heavy, None)


def _on_proc_progress(task_id: str, progress: float):
    entry = _PROCESSING_TASKS.get(task_id)
    if entry is not None and entry.get("status") == "running":
        entry["progress"] = int(progress)


def _on_proc_done(task_id: str, ok: bool, results, context):

    entry = _PROCESSING_TASKS.get(task_id)
    if entry is None:
        return
    if entry.get("status") == "canceled":
        pass
    elif ok and entry.get("alg") is not None and _missing_destinations(entry["alg"], entry.get("parameters") or {}):



        missing = _missing_destinations(entry["alg"], entry.get("parameters") or {})
        feedback = entry.get("feedback")
        errors = getattr(feedback, "errors", [])[-3:] if feedback else []
        detail = f"Processing finished without writing {', '.join(missing)}."
        if errors:
            detail += " Log: " + " | ".join(" ".join(e.split())[:300] for e in errors)
        entry["status"] = "error"
        entry["error"] = detail
    elif ok:
        try:
            provenance = None
            if entry.get("alg") is not None:
                provenance = _record_history(entry["alg"], entry.get("parameters") or {}, context)
            entry["outputs"] = _process_outputs(results, context, output_name=entry.get("output_name"),
                                                provenance=provenance)
            entry["status"] = "complete"
            entry["progress"] = 100
            _report_empty_outputs(entry)
            _report_log(entry, entry.get("feedback"))
            report_checks(entry, entry.get("algorithm") or entry.get("algorithm_id") or "",
                          entry.get("parameters") or {})
        except Exception as e:
            entry["status"] = "error"
            entry["error"] = f"Output handling failed: {e}"
    else:
        entry["status"] = "error"
        feedback = entry.get("feedback")
        captured = "; ".join(getattr(feedback, "errors", [])[-5:]) if feedback else ""
        entry["error"] = (
            f"Algorithm reported failure: {captured}"
            if captured
            else "Algorithm reported failure, check get_message_log for details."
        )

    for heavy in ("task", "context", "feedback", "alg", "parameters"):
        entry.pop(heavy, None)


def _sync_with_qgis(task_id: str) -> None:
    """Close an entry whose QgsTask is already over, however it ended."""















    entry = _PROCESSING_TASKS.get(task_id)
    if entry is None or entry.get("status") != "running":
        return
    task = entry.get("task")
    if task is None:
        return
    terminated = enum_member(QgsTask, "TaskStatus", "Terminated", None)
    try:
        over = task.status() == terminated and not task.isActive()
    except (AttributeError, RuntimeError):
        over = True
    if over:
        _on_proc_terminated(task_id)


def _get_task_status(args: dict) -> dict:
    task_id = args.get("task_id")
    _sync_with_qgis(task_id)
    entry = _PROCESSING_TASKS.get(task_id)
    if entry is None:
        return {
            "_error": f"Unknown task_id: {task_id}",
            "_code": "INVALID_ARGS",
            "_suggestion": "Start one with run_processing(async=true), which returns a task_id.",
        }
    out = {
        "task_id": task_id,
        "status": entry["status"],
        "progress": entry.get("progress", 0),
        "algorithm": entry.get("algorithm"),
    }
    if entry["status"] == "complete":
        out["outputs"] = entry.get("outputs", {})

        for key in ("empty_outputs", "log"):
            if entry.get(key):
                out[key] = entry[key]
    elif entry["status"] == "error":
        out["error"] = entry.get("error")
    elif entry["status"] == "canceled":



        if entry.get("staged_for"):
            out["destination_unchanged"] = entry["staged_for"]
        if entry.get("partial_file"):
            out["partial_file"] = entry["partial_file"]
            out["note"] = (f"The write was stopped. {entry['partial_file']} is the incomplete file it was "
                           f"writing, not the export; it can be deleted. The destination was not touched.")
        elif entry.get("note"):
            out["note"] = entry["note"]


    if entry["status"] in ("complete", "error", "canceled") and not entry.get("_consumed"):
        entry["_consumed"] = True
        entry["_consumed_at"] = time.time()
    return out


def _list_tasks(args: dict) -> dict:
    tasks = []
    for tid in list(_PROCESSING_TASKS):
        _sync_with_qgis(tid)
    for tid, entry in _PROCESSING_TASKS.items():
        item = {
            "task_id": tid,
            "algorithm": entry.get("algorithm"),
            "status": entry.get("status"),
        }
        if entry.get("started_at"):
            item["started_at"] = entry["started_at"]
        tasks.append(item)
    return {
        "tasks": tasks,
        "count": len(tasks),
        "_hint": "Poll get_task_status(task_id) for progress/outputs; cancel_task(task_id) stops a running one.",
    }


def _cancel_task(args: dict) -> dict:
    task_id = args.get("task_id")
    entry = _PROCESSING_TASKS.get(task_id)
    if entry is None:
        return {"_error": f"Unknown task_id: {task_id}", "_code": "INVALID_ARGS"}
    task = entry.get("task")
    if task is not None and entry.get("status") == "running":
        try:
            task.cancel()
        except Exception:  # nosec B110 - task may already be gone
            pass
        entry["status"] = "canceled"
        return {"task_id": task_id, "status": "canceled"}
    return {"task_id": task_id, "status": entry["status"], "note": "Task already finished."}


def shutdown() -> int:
    """Cancel every task this module still has running, and forget them."""











    asked = 0
    for entry in list(_PROCESSING_TASKS.values()):
        task = entry.get("task")
        if task is None or entry.get("status") != "running":
            continue
        try:
            task.cancel()
            asked += 1
        except Exception:  # nosec B110 - a task already gone needs nothing
            pass
        entry["status"] = "canceled"
    _PROCESSING_TASKS.clear()
    return asked


_ALGORITHMS_CACHE: list[tuple] = []
_ALGORITHMS_CACHE_COUNT: int = -1


def _list_algorithms(args: dict) -> dict:
    global _ALGORITHMS_CACHE, _ALGORITHMS_CACHE_COUNT
    registry = QgsApplication.processingRegistry()
    algs = registry.algorithms()


    if len(algs) != _ALGORITHMS_CACHE_COUNT:
        _ALGORITHMS_CACHE = [(a.id(), a.displayName(), a.group()) for a in algs]
        _ALGORITHMS_CACHE_COUNT = len(algs)

    search = (args.get("search") or "").lower()
    limit = max(int(args.get("limit", 0) or 0), 0)

    results = []
    matched = 0
    size = 0
    over_budget = False
    for alg_id, name, group in _ALGORITHMS_CACHE:
        if search and search not in alg_id.lower() and search not in name.lower():
            continue
        matched += 1
        if over_budget or (limit and len(results) >= limit):
            continue
        entry = {"id": alg_id, "name": name, "group": group}


        size += len(alg_id) + len(name) + len(group) + 40
        if results and size > MAX_LIST_CHARS:
            over_budget = True
            continue
        results.append(entry)

    out = {
        "total": len(_ALGORITHMS_CACHE),
        "matched": matched,
        "count": len(results),
        "algorithms": results,
    }
    if matched > len(results):
        out["omitted"] = matched - len(results)
    return out


def _is_optional(param) -> bool:
    """Whether a Processing parameter may be left out, on QGIS 3 and on QGIS 4."""






    flag = enum_member(type(param), "Flag", "Optional", None)
    if flag is None:
        flag = getattr(param, "FlagOptional", None)
    if flag is None:
        return False
    try:
        return bool(param.flags() & flag)
    except (TypeError, ValueError, AttributeError):
        return False


def _get_algorithm_help(args: dict) -> dict:
    algorithm_id = args["algorithm_id"]
    alg = QgsApplication.processingRegistry().algorithmById(algorithm_id)
    if not alg:
        return {"_error": f"Algorithm not found: {algorithm_id}", **_provider_hint(algorithm_id)}

    params = []
    for param in alg.parameterDefinitions():
        params.append(
            {
                "name": param.name(),
                "description": param.description(),
                "type": param.type(),
                "required": not _is_optional(param),
                "default": param.defaultValue(),
            }
        )

    out = {
        "id": alg.id(),
        "name": alg.displayName(),
        "description": alg.shortDescription() or alg.shortHelpString(),
        "parameters": params,
    }



    from .plugin_tools import skill_for_algorithm

    folder, skill = skill_for_algorithm(alg.id())
    if skill:
        out["plugin"] = folder
        out["how_to_use"] = skill
    return out
