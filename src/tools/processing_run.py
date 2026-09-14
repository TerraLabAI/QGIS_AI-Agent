# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import contextlib
import os
import re
import time
import uuid

import processing
from qgis.core import (
    Qgis,
    QgsApplication,
    QgsDataSourceUri,
    QgsEditorWidgetSetup,
    QgsMapLayer,
    QgsProcessingAlgRunnerTask,
    QgsProcessingContext,
    QgsProcessingFeatureSourceDefinition,
    QgsProcessingFeedback,
    QgsProcessingUtils,
    QgsProject,
    QgsProperty,
    QgsRasterLayer,
    QgsTask,
    QgsVectorLayer,
    QgsVectorLayerFeatureSource,
    QgsWkbTypes,
)
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QFileInfo

from ..core import background, ground, invalid_geometry, layer_order
from ..core.crs_ref import crs_ref
from ..core.host_platform import remove_quietly, remove_tree
from ..core.logger import log_warning
from ..core.policy import AGENT_TMP_DIR, create_managed_temp_dir
from ..core.qt_compat import enum_member
from ..core.security import validate_path
from ..core.serialization import cut_string
from . import processing_child
from .layer_io_tools import _release_layers_at_path, _same_file
from .layer_lookup import _find_layer
from .postconditions import joined_input_fields, read_ahead_memory, report_checks
from .postconditions import read_ahead as read_checks_ahead
from .processing_decisions import (
    _FIELD_EXISTS_RE,
    _goes_to_task,
    _processing_error,
    _threadable,
    _unsafe_processing_algorithm,
    align_raster_grids,
    geometry_defaults,
    raster_nodata_defaults,
)
from .processing_destinations import (
    _destination_names,
    _ensure_proj_env,
    _gpkg_sink_uri,
    _gpkg_table_target,
    _input_file_paths,
    _inputs_as_layers,
    _missing_destinations,
    _missing_inputs,
    _output_names,
    _provider_hint,
    _release_table_layers,
    _temporary_suffix,
    destination_report,
    modified_destination_layers,
    output_evidence_problem,
)
from .processing_guards import (
    _distance_sanity,
    _empty_input_check,
    _flow_in_degrees_check,
    _gdal_format_options,
    _geographic_distance_check,
    _grid_sanity,
    _ground_measure_repair,
    _hillshade_input_check,
    _metric_grid_on_degrees_check,
    _parameter_sanity,
    _raster_size_sanity,
    _resolve_layer_inputs,
    _stamp_provenance,
    _terrain_on_degrees_check,
    _undeclared_nodata_check,
    _unresolved_input_check,
    _window_degrees_repair,
    _window_order_repair,
    _window_sanity,
    ignored_parameters,
    localise_streamed_rasters,
    selected_sources,
    source_definition,
    streamed_raster_addresses,
    unfilled_dem_warning,
)
from .processing_paths import alias_fragile_paths, safe_output_name
from .stac_tools import normalise_crs



_PROCESSING_TASKS: dict[str, dict] = {}

_CANCELED_AT_UNLOAD: dict[str, dict] = {}

class _CapturingFeedback(QgsProcessingFeedback):



    def __init__(self):
        super().__init__()
        self.errors: list[str] = []
        self.console: list[str] = []

    def reportError(self, error, fatalError=False):  # noqa: N802
        self.errors.append(str(error))
        super().reportError(error, fatalError)

    def pushWarning(self, warning):  # noqa: N802
        self.errors.append(str(warning))
        super().pushWarning(warning)



    def pushConsoleInfo(self, info):  # noqa: N802
        lines = [line.strip() for line in str(info).splitlines() if line.strip()]
        self.console = (self.console + lines)[-40:]
        super().pushConsoleInfo(info)

    def console_tail(self, count: int = 4) -> list[str]:

        flagged = [line for line in self.console
                   if any(word in line.lower() for word in ("error", "fail", "could not", "cannot", "invalid"))]
        return (flagged or self.console)[-count:]


def _inject_gdal_crs(alg, params: dict) -> None:








    crs_param_names = {d.name() for d in alg.parameterDefinitions()
                       if d.name().upper().endswith("_CRS") and d.name().upper().startswith("SOURCE")}
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


    if collected_authid:
        for name in crs_param_names:
            if not params.get(name):
                params[name] = collected_authid


def _appends_to_destination(alg, parameters: dict) -> bool:

    try:
        definition = alg.parameterDefinition("OVERWRITE")
    except Exception:  # noqa: BLE001
        return False
    if definition is None or definition.type() != "boolean":
        return False
    value = parameters.get("OVERWRITE", definition.defaultValue())
    if isinstance(value, str):
        return value.strip().lower() in ("false", "0", "no")
    return value is not None and not bool(value)


def _vector_destination(definition) -> bool:

    try:
        return definition.type() in ("sink", "vectorDestination")
    except Exception:  # noqa: BLE001
        return False


_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]+:")


def _ogr_driver_for(path: str) -> str:

    if _URI_SCHEME.match(path):
        return "uri"
    ext = os.path.splitext(path)[1].lstrip(".")
    if not ext:
        return ""
    try:
        from qgis.core import QgsVectorFileWriter

        return str(QgsVectorFileWriter.driverForExtension(ext) or "")
    except Exception:  # noqa: BLE001
        return "unknown"


def _record_history(alg, parameters: dict, context) -> dict:







    from ..core import provenance as origin

    provenance = {"algorithm": alg.id(), "command": ""}
    try:


        provenance.update(origin.inputs_of(parameters, alg))
        provenance["command"] = origin.command_text(alg.id(), parameters)
    except Exception:  # noqa: BLE001
        provenance = {"algorithm": alg.id(), "command": ""}
    try:
        python_command = str(alg.asPythonCommand(parameters, context))
    except Exception:  # noqa: BLE001
        python_command = ""
    try:
        from qgis.gui import QgsGui, QgsHistoryEntry
        from qgis.PyQt.QtCore import QDateTime

        entry = QgsHistoryEntry("processing", QDateTime.currentDateTime(), {
            "python_command": python_command,
            "algorithm_id": alg.id(),
            "parameters": alg.asMap(parameters, context),
        })
        QgsGui.historyProviderRegistry().addEntry(entry)
    except Exception:  # nosec B110
        pass
    return provenance





_PREPARED_INPUTS: dict = {}

_PREPARED_AT_ONCE = 4


def _remote_raster_strings(alg, parameters: dict) -> list:

    from ..core.vsi import STREAMED_PREFIXES
    from .processing_guards import _takes_rasters

    found = []
    for definition in alg.parameterDefinitions():
        value = parameters.get(definition.name())
        if definition.type() == "raster":
            items = [value]
        elif definition.type() == "multilayer" and isinstance(value, list) and _takes_rasters(definition):
            items = value
        else:
            continue
        for item in items:
            text = item.strip() if isinstance(item, str) else ""
            if (text.lower().startswith(("http://", "https://", "ftp://"))
                    or any(prefix in text for prefix in STREAMED_PREFIXES)) and text not in found:
                found.append(text)
    return found


def prepare_inputs(args: dict):

    algorithm_id = str(args.get("algorithm_id") or "")
    alg = QgsApplication.processingRegistry().algorithmById(algorithm_id) if algorithm_id else None
    return prepare_remote_rasters(alg, [args.get("parameters")])


def prepare_remote_rasters(alg, parameter_sets: list):














    if alg is None:
        return None
    from .processing_guards import streamable_path

    remote = []
    for parameters in parameter_sets:
        if isinstance(parameters, dict):
            remote += [text for text in _remote_raster_strings(alg, parameters) if text not in remote]
    if not remote:
        return None

    def work():
        from .data_common import built_here, worker_options

        built = {}

        def build(text):



            streamed = streamable_path(text)
            for source in ([streamed] if streamed else []) + [text]:
                opened = {}

                def make(source=source, opened=opened):
                    layer = QgsRasterLayer(source, os.path.basename(text.split("?", 1)[0]), "gdal",
                                           worker_options(QgsRasterLayer))
                    opened["valid"] = layer.isValid()
                    return layer

                handle = built_here(make)
                if handle is None:
                    return
                built[source] = handle
                if opened.get("valid"):
                    return


        with background.KeptThreadPool(min(len(remote), _PREPARED_AT_ONCE), "run_processing prepare") as pool:
            for future in [pool.submit(build, text) for text in remote]:
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001
                    log_warning(f"Remote raster not prepared: {exc}")

        def finish():
            _PREPARED_INPUTS.clear()
            for text, handle in built.items():
                layer = handle.take()
                if layer is not None and layer.isValid():
                    _PREPARED_INPUTS[text] = layer
            built.clear()



        finish.release = _PREPARED_INPUTS.clear
        return finish

    return work


def take_prepared_inputs() -> dict:

    prepared = dict(_PREPARED_INPUTS)
    _PREPARED_INPUTS.clear()
    return prepared


@contextlib.contextmanager
def offered_inputs(prepared: dict):



    _PREPARED_INPUTS.update(prepared)
    try:
        yield
    finally:
        _PREPARED_INPUTS.clear()


def _with_prepared_inputs(parameters: dict) -> dict:



    prepared = take_prepared_inputs()
    if not prepared:
        return parameters
    out = dict(parameters)
    for key, value in parameters.items():
        if isinstance(value, str) and value.strip() in prepared:
            out[key] = prepared[value.strip()]
        elif isinstance(value, list):
            out[key] = [prepared.get(item.strip(), item) if isinstance(item, str) else item for item in value]
    return out


def _run_processing(args: dict, algorithm=None) -> dict:



    algorithm_id = args["algorithm_id"]
    parameters = args.get("parameters", {})

    unsafe = _unsafe_processing_algorithm(algorithm_id)
    if unsafe:
        return unsafe

    alg = algorithm if algorithm is not None else QgsApplication.processingRegistry().algorithmById(algorithm_id)
    if not alg:



        return {"_error": f"Algorithm not found: {algorithm_id}", "code": "INVALID_ARGS",
                **_provider_hint(algorithm_id)}


    parameters, renamed_outputs, misnamed = _output_names(alg, parameters, algorithm_id)
    if misnamed:
        return misnamed





    parameters, selected, selection_refused = selected_sources(alg, parameters)
    if selection_refused:
        return selection_refused



    parameters, _resolved_inputs = _resolve_layer_inputs(parameters, alg)

    parameters, streamed_addresses = streamed_raster_addresses(alg, parameters, _PREPARED_INPUTS)



    wrong_parameters = _parameter_sanity(alg, parameters)
    if wrong_parameters:
        return wrong_parameters



    source = parameters.get("INPUT")
    parameters, defaults = geometry_defaults(
        alg, algorithm_id, parameters, QgsProject.instance().mapLayer(source) if isinstance(source, str) else None)

    parameters, nodata_defaults = raster_nodata_defaults(alg, algorithm_id, parameters)

    parameters, grid_defaults, grid_refused = align_raster_grids(alg, algorithm_id, parameters)
    if grid_refused:
        return grid_refused
    defaults = defaults + nodata_defaults + grid_defaults
    selection = [f"{key} read the " + (f"{count} " if count >= 0 else "") + ("selected " if only_selected else "")
                 + f"features of '{name}'"
                 + (f" matching {expression}" if expression else "")
                 + (f" (of {total})" if total >= 0 else "")
                 for key, (_layer_id, count, total, name, only_selected, expression) in selected.items()]
    dropped = ignored_parameters(alg, parameters)

    in_degrees = _geographic_distance_check(alg, parameters, bool(args.get("confirm_large")))
    if in_degrees:
        return in_degrees

    shaded_twice = _hillshade_input_check(algorithm_id, parameters)
    if shaded_twice:
        return shaded_twice
    terrain_in_degrees = _terrain_on_degrees_check(algorithm_id, parameters)
    if terrain_in_degrees:
        return terrain_in_degrees


    grid_in_degrees = (_metric_grid_on_degrees_check(algorithm_id, parameters, bool(args.get("confirm_large")))
                       or _flow_in_degrees_check(alg, algorithm_id, parameters))
    if grid_in_degrees:
        return grid_in_degrees


    fill_warning = unfilled_dem_warning(alg, algorithm_id, parameters, bool(args.get("confirm_large")))
    undeclared_nodata = _undeclared_nodata_check(algorithm_id, parameters, bool(args.get("confirm_large")))
    if undeclared_nodata:
        return undeclared_nodata
    suspicious = _distance_sanity(parameters, bool(args.get("confirm_large")))
    if suspicious:
        return suspicious

    too_many = _grid_sanity(parameters, bool(args.get("confirm_large")))
    if too_many:
        return too_many
    too_big = _raster_size_sanity(parameters, bool(args.get("confirm_large")), algorithm_id)
    if too_big:
        return too_big

    parameters, streamed_copies, not_copied = localise_streamed_rasters(algorithm_id, parameters)
    if not_copied:
        return not_copied
    window_repairs = (_window_degrees_repair(parameters) + _window_order_repair(parameters)
                      + _ground_measure_repair(alg, parameters))
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


                is_path = any(mark in item for mark in ("|", "/", "\\"))
                found = QgsProject.instance().mapLayer(item) if is_path else _find_layer(item)
                if found is not None:
                    input_ids.add(found.id())


    input_paths = _input_file_paths(parameters, destination_names)

    def _protects_input(path: str) -> bool:
        return any(_same_file(path, other) for other in input_paths)




    plans: list[tuple] = []

    renamed_for_provider: list[str] = []


    in_project = args.get("add_to_project") is not False
    try:
        for definition in alg.parameterDefinitions():
            name = definition.name()
            if not getattr(definition, "isDestination", lambda: False)():
                continue

            current_value = sanitized_parameters.get(name)
            if current_value in (None, "", "TEMPORARY_OUTPUT") or (
                    not in_project and isinstance(current_value, str) and current_value.startswith("memory:")):
                plans.append(("temp", name, definition))
                continue

            if isinstance(current_value, str) and current_value != "memory:":
                table_target = _gpkg_table_target(current_value)
                if table_target is not None:
                    gpkg_path, table_name = table_target
                    if not os.path.exists(gpkg_path):
                        gpkg_path, renamed = safe_output_name(algorithm_id, name, gpkg_path)
                        renamed_for_provider += [renamed] if renamed else []
                    path_error = validate_path(gpkg_path, write=True)
                    if path_error:
                        return {"_error": path_error}
                    sink_uri = _gpkg_sink_uri(gpkg_path, table_name)
                    if sink_uri is None:
                        return {"_error": (
                            f"{name}: a GeoPackage path cannot contain a single quote and a table name "
                            f"cannot contain a double quote, because the output URI uses them as "
                            f"delimiters. A different folder or table name has none."),
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
                    if _vector_destination(definition) and not _ogr_driver_for(expanded):



                        expanded += ".gpkg"
                    expanded, renamed = safe_output_name(algorithm_id, name, expanded)
                    renamed_for_provider += [renamed] if renamed else []
                    path_error = validate_path(expanded, write=True)
                    if path_error:
                        return {"_error": path_error}
                    plans.append(("file", name, expanded))
    except Exception as e:
        return {"_error": f"Failed to validate processing outputs: {e}"}





    input_tables = set()
    input_sources = []
    for key, value in parameters.items():
        if key in destination_names:
            continue
        for item in (value if isinstance(value, (list, tuple)) else [value]):
            if not isinstance(item, str):
                continue
            layer = QgsProject.instance().mapLayer(item)
            source = layer.source() if layer is not None else item
            table = _gpkg_table_target(source)
            if table is not None:
                input_tables.add((os.path.normcase(os.path.abspath(table[0])), table[1].casefold()))
            if layer is not None:


                path = source.split("|", 1)[0]
                if path and os.path.isfile(path):
                    input_sources.append(path)
    for kind, name, payload in plans:
        overwritten = ""
        if kind == "file" and (_protects_input(payload) or any(_same_file(payload, other) for other in input_sources)):
            overwritten = payload
        elif kind == "table" and (os.path.normcase(os.path.abspath(payload[0])), payload[1].casefold()) in input_tables:
            overwritten = f"{payload[0]}|layername={payload[1]}"
        if overwritten:
            return {"_error": (f"{name} is {overwritten}, which {algorithm_id} also reads: writing there replaces "
                               f"the input before it is read, and its data would be lost. Nothing was run."),
                    "code": "INVALID_ARGS",
                    "suggestion": "A new file or a new table name avoids overwriting the input."}





    appends = _appends_to_destination(alg, sanitized_parameters)
    file_destination_paths = [payload for kind, _name, payload in plans
                              if kind == "file" and not (appends and os.path.exists(payload))]




    replaced = [payload if kind == "file" else payload[0] for kind, _name, payload in plans if kind != "temp"]
    if any(os.path.exists(path) and not _protects_input(path) for path in replaced):
        missing = _missing_inputs(alg, parameters, layers_only=True)
        if missing:
            return {"_error": (f"{algorithm_id} needs {', '.join(missing)}, which the call does not give; "
                               f"nothing was run and no file was replaced."),
                    "code": "INVALID_ARGS",
                    "suggestion": f"Input name(s): {', '.join(missing)}."}






    try:
        for kind, name, payload in plans:
            if kind == "temp":
                if managed_output_dir is None:
                    managed_output_dir = create_managed_temp_dir("processing")
                suffix = _temporary_suffix(payload, input_ids)
                sanitized_parameters[name] = os.path.join(
                    managed_output_dir,
                    f"{name.lower()}{suffix}",
                )
            elif kind == "table":
                sanitized_parameters[name] = payload[2]
            else:
                sanitized_parameters[name] = payload
    except Exception as e:
        return {"_error": f"Failed to prepare processing output names: {e}"}
    for key, (layer_id, _count, _total, _name, only_selected, expression) in selected.items():
        sanitized_parameters[key] = source_definition(layer_id, only_selected, expression)

    feedback = _CapturingFeedback()
    context = QgsProcessingContext()
    context.setProject(QgsProject.instance())
    measured_on = ground.measure_processing_on_ellipsoid(context)

    measurement = ground.processing_measurement(context, sanitized_parameters, measured_on)
    invalid_geometry_filter = args.get("invalid_geometry_filter", "default")
    if invalid_geometry_filter in ("skip", "abort"):
        from qgis.core import QgsFeatureRequest




        holder = getattr(Qgis, "InvalidGeometryCheck", None) or QgsFeatureRequest
        member = "GeometrySkipInvalid" if invalid_geometry_filter == "skip" else "GeometryAbortOnInvalid"
        check = getattr(holder, member, None)
        if check is None and invalid_geometry_filter == "skip":
            if managed_output_dir is not None:
                remove_tree(managed_output_dir)
            return {"_error": "This QGIS version cannot skip invalid geometries through Processing."}
        if check is not None:
            context.setInvalidGeometryCheck(check)



    repairs: list[str] = (renamed_outputs + list(window_repairs) + streamed_copies + renamed_for_provider
                          + streamed_addresses)
    if algorithm_id.startswith("gdal:"):
        _ensure_proj_env()
        _inject_gdal_crs(alg, sanitized_parameters)
        repairs += _gdal_format_options(algorithm_id, sanitized_parameters)

    try:
        valid, detail = alg.checkParameterValues(_with_prepared_inputs(sanitized_parameters), context)
    except Exception as exc:  # noqa: BLE001
        if managed_output_dir is not None:
            remove_tree(managed_output_dir)
        return _processing_error(
            algorithm_id,
            f"Processing parameter validation failed before the run: {exc}. "
            "Nothing was run and no destination was replaced.",
            alg,
        )
    if not valid:
        if managed_output_dir is not None:
            remove_tree(managed_output_dir)
        native_detail = " ".join(str(detail).split()) or "provider validation failed"
        failure = _processing_error(
            algorithm_id,
            f"Processing parameters are invalid: {native_detail}. "
            "Nothing was run and no destination was replaced.",
            alg,
        )
        failure["code"] = "INVALID_ARGS"
        try:
            failure.setdefault("parameters", [f"{d.name()} ({d.type()})" for d in alg.parameterDefinitions()])
        except Exception as exc:  # noqa: BLE001
            log_warning(f"{algorithm_id}: listing parameter definitions failed: {exc}")
        return failure

    modified_outputs: list[str] = []
    for kind, _name, payload in plans:
        if kind == "table":
            gpkg_path, table_name, _sink_uri = payload
            if not _protects_input(gpkg_path):
                modified_outputs.extend(
                    modified_destination_layers(gpkg_path, table_name, skip_ids=input_ids))
        elif kind == "file" and not _protects_input(payload):
            modified_outputs.extend(modified_destination_layers(payload, skip_ids=input_ids))
    if modified_outputs:
        if managed_output_dir is not None:
            remove_tree(managed_output_dir)
        names = ", ".join(dict.fromkeys(modified_outputs))
        return {
            "_error": (
                f"Cannot replace the Processing destination while {names} has unsaved edits. "
                "Nothing was run; saving or rolling back those edits frees it."
            )
        }




    run_parameters, alias_notes, path_aliases, not_aliased = alias_fragile_paths(
        algorithm_id, sanitized_parameters, destination_names)
    if not_aliased:
        if managed_output_dir is not None:
            remove_tree(managed_output_dir)
        return not_aliased
    repairs += alias_notes




    _forget_canceled_destinations(file_destination_paths)

    try:
        for kind, _name, payload in plans:
            if kind == "table":
                gpkg_path, table_name, sink_uri = payload
                if not _protects_input(gpkg_path):
                    released.extend(_release_table_layers(gpkg_path, table_name, skip_ids=input_ids))
            elif kind == "file":
                parent = os.path.dirname(payload)
                if parent and not os.path.isdir(parent):
                    os.makedirs(parent, exist_ok=True)
                if not _protects_input(payload):
                    released.extend(_release_layers_at_path(payload, skip_ids=input_ids, delete_existing=not appends))
    except Exception as e:
        return {"_error": f"Failed to prepare processing outputs: {e}"}













    from ..core.provenance import existing_files

    try:
        existed = existing_files(sanitized_parameters)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Files before the run not read: {exc}")
        existed = None


    child_job = None if _goes_to_task(alg, args, parameters) else processing_child.plan(
        alg, algorithm_id, run_parameters, context)
    if child_job is not None or _goes_to_task(alg, args, parameters):
        started = _start_async_processing(
            alg,
            algorithm_id,
            sanitized_parameters,
            args.get("output_name"),
            invalid_geometry_filter,
            destination_paths=file_destination_paths,
            add_to_project=in_project,
            temporary_dir=managed_output_dir,
            existed=existed,
            run_parameters=run_parameters,
            path_aliases=path_aliases,
            child_job=child_job,
        )
        if dropped:
            repairs += dropped


        entry = _PROCESSING_TASKS.get(started.get("task_id"))
        for key, value in (("repairs", repairs), ("selection", selection), ("defaults", defaults),
                           ("measurement", measurement.get("measurement")), ("warning", fill_warning)):
            if value:
                started[key] = value
                if entry is not None:
                    entry[key] = value
        return started

    target = algorithm if algorithm is not None else algorithm_id
    held_since = time.monotonic()
    try:
        result = processing.run(target, run_parameters, feedback=feedback, context=context)
    except Exception as e:


        by_object = _inputs_as_layers(alg, run_parameters, str(e))
        if by_object is not None:
            try:
                result = processing.run(target, by_object, feedback=feedback, context=context)
                if path_aliases is None:
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
            failure["suggestion"] = (f"Another field name avoids it; native:fieldcalculator on '{clash}' "
                                     f"overwrites it in place.")
            return failure
        elif "already exists" not in str(e):
            failure = _processing_error(algorithm_id, f"Processing failed: {str(e)}", alg)
            failure.update(invalid_geometry.facts(run_parameters, context))
            return failure
        else:



            result = None
            last_error = e
            for _attempt in range(2):


                for dest_name in destination_names:
                    value = sanitized_parameters.get(dest_name)
                    for item in (value if isinstance(value, (list, tuple)) else [value]):
                        if not isinstance(item, str) or not os.path.isfile(item) or _protects_input(item):
                            continue
                        released.extend(_release_layers_at_path(item, skip_ids=input_ids,
                                                                delete_existing=not appends))
                try:
                    result = processing.run(target, run_parameters, feedback=feedback, context=context)
                    break
                except Exception as retry_error:
                    last_error = retry_error
            if result is None:
                return _processing_error(
                    algorithm_id,
                    f"The output file is still held by another process (on Windows, a "
                    f"GeoPackage open in QGIS or another program). Last error: {last_error}. "
                    f"A new output file name is free of that lock.",
                    alg,
                )

    if path_aliases is not None:

        result = path_aliases.restore(result)
        path_aliases.remove()



    missing, written = destination_report(alg, sanitized_parameters)
    if missing:
        detail = f"Processing finished without writing {', '.join(missing)}."
        if written:
            detail += " It did write " + ", ".join(f"{key} at {path}" for key, path in written.items()) + "."
        log_lines = feedback.errors[:3] or feedback.console_tail()
        if log_lines:
            detail += " Log: " + " | ".join(" ".join(e.split())[:300] for e in log_lines)
        failed = _processing_error(algorithm_id, detail, alg)
        if written:
            failed["files_written"] = written
        external = algorithm_id.split(":", 1)[0] in ("saga", "sagang", "grass", "grass7", "otb")
        if external and not failed.get("suggestion"):
            failed["suggestion"] = ("This external provider wrote nothing; its log is in the message. The "
                                    "native: or gdal: algorithm for the same operation (list_algorithms finds "
                                    "it) runs inside QGIS.")
        return failed

    provenance = _record_history(alg, sanitized_parameters, context)
    provenance["existed"] = existed
    out = {"algorithm": algorithm_id,
           "outputs": _process_outputs(result, output_name=args.get("output_name"), provenance=provenance,
                                       destination_parameters=sanitized_parameters, algorithm_id=algorithm_id,
                                       add_to_project=in_project)}
    unreadable = output_evidence_problem(alg, sanitized_parameters, out["outputs"])
    if unreadable:
        failed = _processing_error(algorithm_id, f"Processing finished, but {unreadable}", alg)
        if written:
            failed["files_written"] = written
        return failed




    if written:
        out["files_written"] = written



        repairs += [f"{key} was written as {path}" for key, path in written.items()
                    if isinstance(sanitized_parameters.get(key), str)
                    and "|layername=" not in sanitized_parameters[key]
                    and not sanitized_parameters[key].startswith("ogr:")
                    and path != sanitized_parameters[key]]
    if dropped:
        repairs += dropped
    if repairs:
        out["repairs"] = repairs
    if selection:
        out["selection"] = selection
    if defaults:
        out["defaults"] = defaults
    if fill_warning:
        out["warning"] = fill_warning
    out.update(measurement)
    if managed_output_dir is not None:
        out["outputs_note"] = f"unspecified outputs were written to a managed temp dir: {managed_output_dir}"
    if released:
        out["replaced_layers"] = released
    _report_empty_outputs(out)
    _report_log(out, feedback)
    report_checks(out, algorithm_id, sanitized_parameters)
    if not _threadable(alg):

        out["main_thread_s"] = round(time.monotonic() - held_since, 1)
    return out








def _report_empty_outputs(out: dict) -> None:

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



    if isinstance(layer, QgsRasterLayer):
        try:
            normalise_crs(layer)
        except Exception:  # nosec B110
            pass







def raster_file_problem(path: str) -> str:

    try:
        from osgeo import gdal
    except ImportError:
        return ""
    gdal.PushErrorHandler("CPLQuietErrorHandler")
    dataset = None
    try:
        gdal.ErrorReset()
        dataset = gdal.Open(path)
        if dataset is None:
            return gdal.GetLastErrorMsg() or "GDAL cannot open the file"


        if dataset.GetDriver().ShortName != "GTiff":
            return ""
        rows, cols, bands = dataset.RasterYSize, dataset.RasterXSize, dataset.RasterCount
        if not (rows and cols and bands):
            return ""
        for band_number, row in ((1, 0), (bands, rows - 1)):
            gdal.ErrorReset()
            data = dataset.GetRasterBand(band_number).ReadRaster(0, row, cols, 1)
            if data is None or gdal.GetLastErrorType() >= gdal.CE_Failure:
                return gdal.GetLastErrorMsg() or f"band {band_number} row {row} cannot be read"
        return ""
    except Exception as exc:
        return " ".join(str(exc).split()) or type(exc).__name__
    finally:
        del dataset
        gdal.PopErrorHandler()


def _process_outputs(result_map: dict, context=None, output_name=None, provenance=None,
                     destination_parameters=None, algorithm_id=None, add_to_project=True, built=None,
                     algorithm=None) -> dict:














    output = {}
    added = []
    pending = []
    kept_out = []

    def place(key, layer, path=None):
        source = path or _file_of(layer)
        if not add_to_project and source:
            output[key] = _layer_summary(layer, source, in_project=False)
            kept_out.append(layer)
            return
        pending.append(layer)
        added.append((key, layer))
        output[key] = _layer_summary(layer, path)

    for key, value in (result_map or {}).items():
        declared = (destination_parameters or {}).get(key)



        table_target = _gpkg_table_target(declared) if isinstance(declared, str) else None
        if table_target:
            value = f"{table_target[0]}|layername={table_target[1]}"
        if isinstance(value, QgsMapLayer):
            if not value.isValid():
                output[key] = {"layer_name": value.name(), "readable": False,
                               "warning": "the algorithm returned an invalid QGIS layer"}
                continue
            _declare_raster_crs(value)
            place(key, value)
        elif isinstance(value, str) and "|layername=" in value and os.path.exists(value.split("|", 1)[0]):

            file_part = value.split("|", 1)[0]
            path_error = validate_path(file_part, write=False)
            if path_error:
                output[key] = {"path": value, "readable": False, "warning": path_error}
                continue
            layer = _output_layer(value, context, "Vector", built)[0]
            if layer is not None and layer.isValid():


                table = value.split("|layername=", 1)[1].split("|", 1)[0]
                if table and layer.name() == os.path.splitext(os.path.basename(file_part))[0]:
                    layer.setName(table)
                place(key, layer, value)
            else:
                output[key] = {"path": value, "readable": False}
        elif isinstance(value, str) and os.path.exists(value):
            path_error = validate_path(value, write=False)
            if path_error:
                output[key] = {"path": value, "readable": False, "warning": path_error}
                continue
            layer, problem = _output_layer(value, context, _output_hint(algorithm_id, key, algorithm), built)
            if problem:
                output[key] = {"path": value, "readable": False, "unreadable": problem[:300],
                               "warning": "GDAL cannot read this file to its last row: it was written "
                                          "incompletely and was not loaded; another folder "
                                          "may take a whole copy."}
            elif layer is not None and layer.isValid():
                _declare_raster_crs(layer)
                place(key, layer, value)
            else:
                output[key] = {"path": value, "readable": False}
        else:
            layer = None
            if context is not None and isinstance(value, str):
                try:
                    layer = context.takeResultLayer(value)
                except Exception:
                    layer = None
            if layer is not None and layer.isValid():
                _declare_raster_crs(layer)
                place(key, layer)
            elif isinstance(value, str) and _looks_like_file(value) and not os.path.exists(os.path.expanduser(value)):



                output[key] = {"path": value, "readable": False, "missing": True,
                               "warning": "the algorithm finished without writing this file; read log"}
            else:



                output[key] = _plain_output(value)
    if pending:
        QgsProject.instance().addMapLayers(pending, True)
    if provenance:




        stamped = {id(made[0]) for made in (built or {}).values() if made[0] is not None}
        for layer in [layer for _key, layer in added] + kept_out:
            if id(layer) not in stamped:
                _stamp_provenance(layer, provenance)
    added = _drop_leftovers(added, output)
    name = str(output_name or "").strip()
    if name and added:
        from ._layers import take_layer_name

        for key, layer in added:
            new_name = name if (len(added) == 1 or key == "OUTPUT") else f"{name} {key.lower()}"



            new_name, renamed = take_layer_name(new_name, keep_id=layer.id())
            layer.setName(new_name)
            output[key]["layer_name"] = new_name
            if renamed:
                output[key]["renamed_intermediates"] = renamed
    if provenance:
        from ..core.provenance import remember_output

        for key, value in (result_map or {}).items():


            shown = output.get(key, {}).get("path") if isinstance(output.get(key), dict) else None
            remember_output(shown if isinstance(shown, str) and shown else value if isinstance(value, str) else None,
                            provenance)
    if algorithm_id in _FIELD_SETUP_CARRY_ALGORITHMS and added:
        source_layer = _field_rebuild_input_layer(destination_parameters)
        if source_layer is not None:
            for key, layer in added:
                carried = _carried_field_setup(source_layer, layer)
                if carried:
                    output[key]["field_setup_carried"] = carried
    if added and algorithm_id:
        from .style_defaults import carry_style

        source_layer = _style_input_layer(destination_parameters)
        if source_layer is not None:
            for key, layer in added:
                carried = carry_style(source_layer, layer, algorithm_id)
                if carried:
                    output[key]["style_carried"] = carried
    if added:
        from .style_defaults import style_computed_hillshade

        for key, layer in added:
            if "style_carried" not in output[key]:
                styled = style_computed_hillshade(layer)
                if styled:
                    output[key]["styled"] = styled
    return output


def _style_input_layer(destination_parameters):

    vector = _field_rebuild_input_layer(destination_parameters)
    if vector is not None:
        return vector
    value = (destination_parameters or {}).get("INPUT")
    if isinstance(value, QgsRasterLayer):
        return value
    if isinstance(value, str) and value.strip():
        layer = QgsProject.instance().mapLayer(value) or _find_layer(value)
        return layer if isinstance(layer, QgsRasterLayer) else None
    return None








_FIELD_SETUP_CARRY_ALGORITHMS = frozenset({"native:refactorfields", "qgis:refactorfields"})


def _field_rebuild_input_layer(destination_parameters):







    value = (destination_parameters or {}).get("INPUT")
    if isinstance(value, QgsProcessingFeatureSourceDefinition):
        try:
            static_value = value.source.staticValue()
        except AttributeError:
            static_value = None
        value = static_value if static_value is not None else value
    if isinstance(value, QgsVectorLayer):
        return value
    if isinstance(value, str) and value.strip():
        layer = QgsProject.instance().mapLayer(value) or _find_layer(value)
        return layer if isinstance(layer, QgsVectorLayer) else None
    return None


def _carried_field_setup(source_layer, target_layer) -> list:


    if not isinstance(source_layer, QgsVectorLayer) or not isinstance(target_layer, QgsVectorLayer):
        return []
    if source_layer.id() == target_layer.id():
        return []
    carried = []
    target_fields = target_layer.fields()
    for src_index, field in enumerate(source_layer.fields()):
        dst_index = target_fields.indexOf(field.name())
        if dst_index < 0:
            continue
        entry = {"name": field.name()}
        setup = source_layer.editorWidgetSetup(src_index)
        if setup is not None and setup.type():
            target_layer.setEditorWidgetSetup(dst_index, QgsEditorWidgetSetup(setup.type(), setup.config()))
            entry["widget_type"] = setup.type()
        alias = source_layer.attributeAlias(src_index)
        if alias:
            target_layer.setFieldAlias(dst_index, alias)
            entry["alias"] = alias
        default = source_layer.defaultValueDefinition(src_index)
        if default is not None and default.expression():
            target_layer.setDefaultValueDefinition(dst_index, default)
            entry["default_expression"] = default.expression()
        if len(entry) > 1:
            carried.append(entry)
    return carried


def _output_hint(algorithm_id, key, algorithm=None) -> str:


    try:
        algorithm = algorithm or QgsApplication.processingRegistry().algorithmById(str(algorithm_id or ""))
        definition = algorithm.outputDefinition(key) if algorithm is not None else None
        kind = definition.type() if definition is not None else ""
    except Exception:  # noqa: BLE001
        return ""
    return {"outputRaster": "Raster", "outputVector": "Vector"}.get(kind, "")


def _layer_from_string(value: str, lookup, hint: str = "", load: bool = True):


    type_hint = getattr(getattr(QgsProcessingUtils, "LayerHint", None), hint, None) if hint else None
    if type_hint is not None:
        return QgsProcessingUtils.mapLayerFromString(value, lookup, load, type_hint)
    return QgsProcessingUtils.mapLayerFromString(value, lookup, load)


def _output_layer(value: str, context, hint: str, built=None) -> tuple:


    made = (built or {}).get(value)
    if made is not None and made[0] is not None:
        return made
    layer = _native_output_layer(value, context, hint)
    return layer, (raster_file_problem(value) if isinstance(layer, QgsRasterLayer) else "")


def _native_output_layer(value: str, context=None, hint: str = ""):






    lookup = context or QgsProcessingContext()
    if context is None:
        lookup.setProject(QgsProject.instance())
    try:
        layer = _layer_from_string(value, lookup, hint)
    except Exception:  # noqa: BLE001
        return None
    if layer is None or not layer.isValid():
        return None
    if QgsProject.instance().mapLayer(layer.id()) is None:
        try:
            lookup.temporaryLayerStore().takeMapLayer(layer)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"takeMapLayer ownership transfer failed: {exc}")
    return layer





LEFTOVER_OUTPUTS = frozenset({
    "FAIL_OUTPUT", "NON_MATCHING", "OUTPUT_NON_MATCHING", "NON_MATCHING_OUTPUT",
    "UNMATCHED", "DUPLICATES", "ERROR_OUTPUT", "INVALID_OUTPUT", "INVALID_FEATURES",
})


def _drop_leftovers(added: list, output: dict) -> list:










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
        if not remove_layers([layer]):
            kept.append((key, layer))
            continue
        if isinstance(summary, dict):
            summary["added_to_project"] = False
            summary.pop("layer_id", None)
            summary.pop("layer_name", None)
            summary["note"] = "Not added to the project: these are the features the algorithm did not keep."
    return kept


def _file_of(layer) -> str:

    try:
        if layer.providerType() not in ("ogr", "gdal"):
            return ""
        source = str(layer.source() or "")
    except Exception:  # noqa: BLE001
        return ""
    return source if os.path.isfile(source.split("|", 1)[0]) else ""


def _layer_summary(layer, path: str | None = None, in_project: bool = True) -> dict:




    out = {"crs": crs_ref(layer.crs()), "readable": True, "added_to_project": in_project}
    if in_project:
        out = {"layer_id": layer.id(), "layer_name": layer.name(), **out}
    else:
        out["note"] = "Not added to the project: this path is the next step's input."
    if isinstance(layer, QgsVectorLayer):
        out["feature_count"] = layer.featureCount()
        out["geometry_type"] = QgsWkbTypes.displayString(layer.wkbType())
    elif isinstance(layer, QgsRasterLayer):
        out["raster_size"] = [int(layer.width()), int(layer.height())]
        out["band_count"] = int(layer.bandCount())
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



    now = time.time()
    stale = [
        tid for tid, e in _PROCESSING_TASKS.items()
        if e.get("_consumed") and (now - e.get("_consumed_at", now)) > 600 and not e.get("held_layers")
    ]
    for tid in stale:
        _PROCESSING_TASKS.pop(tid, None)










_ASYNC_FEATURES = 1_000






_ASYNC_PIXELS = 16_000_000



_POLL_INTERVAL_S = 1.0







_ASYNC_FILE_BYTES = 50 * 1024 * 1024


def _size_of(layer) -> tuple[int, int]:





    features = pixels = 0
    try:
        if hasattr(layer, "width") and hasattr(layer, "height") and not hasattr(layer, "featureCount"):
            pixels = int(layer.width()) * int(layer.height())
        elif hasattr(layer, "featureCount"):
            features = int(layer.featureCount())
    except Exception:  # noqa: BLE001
        return 0, 0
    return max(features, 0), max(pixels, 0)


def _file_is_big(value: str) -> bool:






    path = value.split("|", 1)[0].strip()
    if not path or path.startswith(("/vsi", "memory:")) or "://" in path:
        return False
    try:
        return os.path.getsize(os.path.expanduser(path)) > _ASYNC_FILE_BYTES
    except OSError:
        return False


def _heavy_inputs(parameters: dict) -> bool:












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
    destination_paths=None,
    temporary_dir=None,
    run_parameters=None,
    path_aliases=None,
    add_to_project=True,
    existed=None,
    child_job=None,
) -> dict:
    _sweep_consumed_tasks()
    task_id = "proc-" + uuid.uuid4().hex[:12]
    context = QgsProcessingContext()



    context.setProject(QgsProject.instance())





    from qgis.core import QgsExpressionContext, QgsExpressionContextUtils

    context.setExpressionContext(QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(None)))
    ground.measure_processing_on_ellipsoid(context)
    if invalid_geometry_filter == "skip":
        check = getattr(getattr(Qgis, "InvalidGeometryCheck", object), "GeometrySkipInvalid", None)
        if check is not None:
            context.setInvalidGeometryCheck(check)
    elif invalid_geometry_filter == "abort":
        check = getattr(getattr(Qgis, "InvalidGeometryCheck", object), "GeometryAbortOnInvalid", None)
        if check is not None:
            context.setInvalidGeometryCheck(check)
    feedback = _CapturingFeedback()


    if child_job is not None:
        task = processing_child.ChildRun(
            f"Running {algorithm_id}", child_job,
            lambda ok, report, tid=task_id, ctx=context, fb=feedback: _on_child_done(tid, ok, report, ctx, fb))
    else:
        task = QgsProcessingAlgRunnerTask(alg, run_parameters if run_parameters is not None else parameters,
                                          context, feedback)
    _PROCESSING_TASKS[task_id] = {
        "status": "running",
        "progress": 0,
        "algorithm": algorithm_id,
        "started_at": time.strftime("%H:%M:%S"),
        "task": task,
        "context": context,
        "feedback": feedback,
        "output_name": output_name,
        "add_to_project": add_to_project,
        "alg": alg,
        "parameters": parameters,
        "run_token": layer_order.current_run(),
        "tool_call_id": background.current_call(),
        "existed": existed,
        "destination_paths": list(destination_paths or []),

        "temporary_dir": temporary_dir,
    }
    if path_aliases is not None:
        path_aliases.owner = task_id
        _PROCESSING_TASKS[task_id]["path_aliases"] = path_aliases
    if child_job is not None:
        _PROCESSING_TASKS[task_id]["separate_qgis"] = True
    task.progressChanged.connect(lambda p, tid=task_id: _on_proc_progress(tid, p))






    if child_job is None:
        task.executed.connect(
            lambda ok, results, tid=task_id, ctx=context, fb=feedback: _on_proc_done(tid, ok, results, ctx))






    task.taskTerminated.connect(lambda tid=task_id: _on_proc_terminated(tid))
    QgsApplication.taskManager().addTask(task)
    return {
        "task_id": task_id,
        "status": "running",
        "algorithm": algorithm_id,
        "note": ("Running in a separate QGIS process (the algorithm declares NoThreading), "
                 "this QGIS stays responsive." if child_job is not None
                 else "Running in the background, QGIS stays responsive."),
        "poll": {"tool": "get_task_status", "args": {"task_id": task_id},
                 "interval_s": _POLL_INTERVAL_S, "label": _shown_name(alg, algorithm_id)},
    }


def _shown_name(alg, algorithm_id: str) -> str:

    try:
        return str(alg.displayName() or "") or algorithm_id
    except Exception:  # noqa: BLE001
        return algorithm_id


def _on_child_done(task_id: str, ok: bool, report: dict, context, feedback) -> None:


    for line in report.get("errors") or []:
        feedback.reportError(str(line))
    if not ok and report.get("error") and not report.get("unavailable"):
        feedback.reportError(str(report["error"]))
    for line in report.get("warnings") or []:
        feedback.pushWarning(str(line))
    for line in report.get("console") or []:
        feedback.pushConsoleInfo(str(line))
    entry = _PROCESSING_TASKS.get(task_id)
    if entry is not None:

        entry["separate_qgis"] = {key: report[key] for key in ("init_s", "run_s", "wall_s") if key in report}
    results = report.get("results") or {}
    if report.get("unavailable") and entry is not None and entry.get("status") == "running":



        held_since = time.monotonic()
        try:
            results = processing.run(entry["alg"], report.get("parameters") or entry.get("parameters") or {},
                                     feedback=feedback, context=context)
            ok = True
        except Exception as exc:  # noqa: BLE001
            feedback.reportError(str(exc))
            ok = False
        entry["main_thread_s"] = round(time.monotonic() - held_since, 1)
        entry.pop("separate_qgis", None)
    _on_proc_done(task_id, ok, results, context)
    if report.get("work_dir"):
        remove_tree(report["work_dir"])


def register_task(task, label: str, connect=None) -> tuple:












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


def _cleanup_canceled_destination(entry: dict) -> None:














    for path in entry.get("destination_paths") or ():


        stem, extension = os.path.splitext(path)
        sidecars = [path + suffix for suffix in ("-wal", "-shm", "-journal")]
        if extension.lower() == ".shp":
            sidecars += [stem + suffix for suffix in (".shx", ".dbf", ".prj", ".cpg", ".qix")]
        for candidate in [path] + sidecars:
            if os.path.isfile(candidate) and not remove_quietly(candidate):
                log_warning(f"Partial output {candidate} not removed after cancel: another program holds it.")
    folder = entry.get("temporary_dir")
    if not isinstance(folder, str) or not folder:
        return
    try:
        real = os.path.realpath(folder)

        if (os.path.dirname(real) == os.path.realpath(AGENT_TMP_DIR)
                and os.path.basename(real).startswith("processing-") and os.path.isdir(real)):
            if remove_tree(real):
                log_warning(f"Partial temporary output {folder} not fully removed after cancel.")
    except OSError as exc:
        log_warning(f"Partial temporary output {folder} not removed after cancel: {exc}")


def _forget_canceled_destinations(paths) -> None:

    def key(value: str) -> str:
        return os.path.normcase(os.path.realpath(value))

    try:
        wanted = {key(path) for path in paths or ()}
    except (OSError, TypeError, ValueError):
        return
    if not wanted:
        return
    for entry in list(_PROCESSING_TASKS.values()) + list(_CANCELED_AT_UNLOAD.values()):
        if entry.get("status") != "canceled" or not entry.get("destination_paths"):
            continue
        entry["destination_paths"] = [p for p in entry["destination_paths"] if key(p) not in wanted]


def canceled_output(path: str) -> bool:





    def key(value: str) -> str:
        return os.path.normcase(os.path.realpath(value))

    try:
        wanted = key(path)
        for entry in list(_PROCESSING_TASKS.values()):
            if entry.get("status") != "canceled":
                continue
            if any(key(other) == wanted for other in entry.get("destination_paths") or ()):
                return True
            folder = entry.get("temporary_dir")
            if isinstance(folder, str) and folder and wanted.startswith(key(folder) + os.sep):
                return True
    except (OSError, TypeError, ValueError) as exc:
        log_warning(f"Cancelled output check failed for {path}: {exc}")
    return False


def _on_proc_terminated(task_id: str):






    entry = _PROCESSING_TASKS.get(task_id) or _CANCELED_AT_UNLOAD.pop(task_id, None)
    if entry is None:
        return
    if entry.get("path_aliases") is not None:
        entry.pop("path_aliases").remove()
    if entry.get("status") == "canceled":




        _cleanup_canceled_destination(entry)
        _let_go(entry)
        return
    if entry.get("status") != "running":
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
    _let_go(entry)


def _on_proc_progress(task_id: str, progress: float):
    entry = _PROCESSING_TASKS.get(task_id)
    if entry is not None and entry.get("status") == "running":
        entry["progress"] = int(progress)


def _on_proc_done(task_id: str, ok: bool, results, context):

    entry = _PROCESSING_TASKS.get(task_id) or _CANCELED_AT_UNLOAD.pop(task_id, None)
    if entry is None:
        return
    path_aliases = entry.pop("path_aliases", None)
    if path_aliases is not None:

        results = path_aliases.restore(results)
        path_aliases.remove()
    if entry.get("status") == "canceled":
        _cleanup_canceled_destination(entry)
    elif ok and entry.get("alg") is not None and _missing_destinations(entry["alg"], entry.get("parameters") or {}):



        missing, written = destination_report(entry["alg"], entry.get("parameters") or {})
        feedback = entry.get("feedback")
        errors = getattr(feedback, "errors", [])[-3:] if feedback else []
        console = feedback.console_tail() if hasattr(feedback, "console_tail") else []
        detail = f"Processing finished without writing {', '.join(missing)}."
        if written:
            detail += " It did write " + ", ".join(f"{key} at {path}" for key, path in written.items()) + "."
        for lines in (errors, console):
            if lines:
                detail += " Log: " + " | ".join(" ".join(e.split())[:300] for e in lines)
                break
        entry["status"] = "error"



        explained = _processing_error(entry.get("algorithm") or "", detail, entry.get("alg"))
        entry["error"] = explained.get("_error") or detail
        if explained.get("suggestion"):
            entry["suggestion"] = explained["suggestion"]
        if written:
            entry["files_written"] = written
    elif ok:


        entry["finished_writing"] = True
        if _outputs_built_off_main(task_id, entry, results, context):
            return
        _finish_outputs(entry, results, context)
    else:
        entry["status"] = "error"
        feedback = entry.get("feedback")
        captured = "; ".join(getattr(feedback, "errors", [])[-5:]) if feedback else ""
        entry["error"] = (
            f"Algorithm reported failure: {captured}"
            if captured
            else "Algorithm reported failure, check get_message_log for details."
        )
        entry.update(invalid_geometry.facts(entry.get("parameters"), context))
    _let_go(entry)


def _let_go(entry: dict) -> None:


    for heavy in ("task", "context", "feedback", "alg", "parameters"):
        entry.pop(heavy, None)
    _release_held(entry)




def remove_layers(layers) -> list:











    project = QgsProject.instance()
    gone = []
    taken = False
    for item in layers or ():
        try:
            layer = project.mapLayer(item if isinstance(item, str) else item.id())
            if layer is None:
                continue
            name = layer.name()
            readers = _readers_of(layer)
            if not readers:
                project.removeMapLayer(layer.id())
            else:
                held = project.takeMapLayer(layer)
                if held is None:
                    continue
                taken = True
                for entry in readers:
                    entry.setdefault("held_layers", []).append(held)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"A layer was not removed from the project: {exc}")
            continue
        gone.append(name)
    if taken:
        _canvas_follows_tree()
    return gone


def _canvas_follows_tree() -> None:




    try:
        from qgis.utils import iface

        bridge = iface.layerTreeCanvasBridge() if iface is not None else None
        if bridge is not None:
            bridge.setCanvasLayers()
    except Exception as exc:  # noqa: BLE001
        log_warning(f"The map canvas was not updated after a layer left the project: {exc}")


def _readers_of(layer) -> list:




    runs = [entry for entry in _PROCESSING_TASKS.values()
            if entry.get("task") is not None and entry.get("parameters") and not entry.get("separate_qgis")]
    if not runs:
        return []
    names = {layer.id(), layer.name()}
    source = QgsProcessingUtils.normalizeLayerSource(layer.source())

    def names_it(value) -> bool:
        if isinstance(value, QgsMapLayer):
            return value is layer
        definition = getattr(value, "source", None)
        if isinstance(definition, QgsProperty):
            value = definition.staticValue()
        return isinstance(value, str) and bool(value) and (
            value in names or QgsProcessingUtils.normalizeLayerSource(value) == source)

    return [entry for entry in runs
            if any(names_it(item) for value in entry["parameters"].values()
                   for item in (value if isinstance(value, (list, tuple)) else [value]))]


def _release_held(entry: dict) -> None:



    for layer in entry.pop("held_layers", None) or ():
        holders = list(_PROCESSING_TASKS.values()) + list(_CANCELED_AT_UNLOAD.values())
        if any(other is layer for holder in holders for other in holder.get("held_layers") or ()):
            continue
        try:
            if not sip.isdeleted(layer) and QgsProject.instance().mapLayer(layer.id()) is not layer:
                background.delete_here(layer)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"A removed layer was not deleted after its task: {exc}")


def _finish_outputs(entry: dict, results, context, built=None) -> None:




    try:

        provenance = entry.pop("provenance", None) if "provenance" in entry else _provenance_of(entry, context)


        with layer_order.adopted(entry.get("run_token")), background.calling(entry.get("tool_call_id", "")):
            entry["outputs"] = _process_outputs(
                results,
                context,
                output_name=entry.get("output_name"),
                provenance=provenance,
                destination_parameters=entry.get("parameters") or {},
                algorithm_id=entry.get("algorithm"),
                add_to_project=entry.get("add_to_project", True),
                built=built,
                algorithm=entry.get("alg"),
            )
        written = destination_report(entry["alg"], entry.get("parameters") or {})[1]
        if written:
            entry["files_written"] = written
        unreadable = output_evidence_problem(
            entry["alg"], entry.get("parameters") or {}, entry.get("outputs") or {})
        if unreadable:
            entry["status"] = "error"
            explained = _processing_error(
                entry.get("algorithm") or "", f"Processing finished, but {unreadable}", entry.get("alg"))
            entry["error"] = explained.get("_error") or unreadable
            if explained.get("suggestion"):
                entry["suggestion"] = explained["suggestion"]
        else:
            entry["status"] = "complete"
            entry["progress"] = 100
            _report_empty_outputs(entry)
            _report_log(entry, entry.get("feedback"))
            report_checks(entry, entry.get("algorithm") or entry.get("algorithm_id") or "",
                          entry.get("parameters") or {})
    except Exception as e:
        entry["status"] = "error"
        entry["error"] = f"Output handling failed: {e}"


def _provenance_of(entry: dict, context) -> dict | None:

    if entry.get("alg") is None:
        return None
    provenance = _record_history(entry["alg"], entry.get("parameters") or {}, context)
    provenance["existed"] = entry.get("existed")
    return provenance


def _outputs_built_off_main(task_id: str, entry: dict, results, context) -> bool:










    builds = _output_builds(results, context, entry.get("parameters") or {}, entry.get("algorithm"),
                            entry.get("alg"))
    reads = _output_reads(results, context)
    if not builds and not reads:
        return False
    try:
        joined_from = joined_input_fields(entry.get("algorithm"), entry.get("parameters") or {})
    except Exception:  # noqa: BLE001
        joined_from = None
    transform_context = context.transformContext()


    entry["provenance"] = provenance = _provenance_of(entry, context)
    task = background.run_off_thread(
        f"Loading the outputs of {entry.get('algorithm') or 'a Processing run'}",
        lambda: _build_outputs(builds, transform_context, entry, reads, joined_from, provenance),
        lambda built, error: _outputs_built(task_id, results, context, built, error))
    if task is None:
        return False


    entry["task"] = task
    return True


def _output_builds(result_map: dict, context, destination_parameters: dict, algorithm_id, algorithm=None) -> dict:



    builds = {}
    for key, value in (result_map or {}).items():
        declared = destination_parameters.get(key)
        table_target = _gpkg_table_target(declared) if isinstance(declared, str) else None
        if table_target:
            value = f"{table_target[0]}|layername={table_target[1]}"
        if not isinstance(value, str):
            continue
        if "|layername=" in value and os.path.exists(value.split("|", 1)[0]):
            path, hint = value.split("|", 1)[0], "Vector"
        elif os.path.exists(value):
            path, hint = value, _output_hint(algorithm_id, key, algorithm)
        else:
            continue
        if hint not in ("Raster", "Vector") or validate_path(path, write=False):
            continue
        try:
            known = _layer_from_string(value, context, hint, load=False)
        except Exception:  # noqa: BLE001
            known = False
        if known is None:
            builds[value] = hint
    return builds


def _output_reads(result_map: dict, context) -> dict:






    reads = {}
    for value in (result_map or {}).values():
        if not isinstance(value, str) or not value:
            continue
        try:
            layer = context.temporaryLayerStore().mapLayer(value)
            if isinstance(layer, QgsVectorLayer) and layer.isValid() and layer.providerType() == "memory":
                reads[layer.id()] = QgsVectorLayerFeatureSource(layer)
        except Exception:  # noqa: BLE001
            layer = None
    return reads


def _build_outputs(builds: dict, transform_context, entry: dict, reads: dict | None = None,
                   joined_from: list | None = None, provenance: dict | None = None) -> dict:
















    from ..core.postcondition import read_ahead
    from .data_common import built_here

    built = {}
    for value, hint in builds.items():
        if entry.get("status") != "running":
            break
        found = {"problem": ""}

        def make(value=value, hint=hint, found=found):
            first = QFileInfo(value.split("|", 1)[0])
            name = first.baseName() if first.isFile() else QFileInfo(value).baseName()
            name = name or QgsDataSourceUri(value).table() or value
            if hint == "Vector":
                options = QgsVectorLayer.LayerOptions(transform_context)
                options.loadDefaultStyle = False
                options.skipCrsValidation = True
                layer = QgsVectorLayer(value, name, "ogr", options)
                if layer.isValid():
                    layer.featureCount()
                    read_checks_ahead(layer, layer.id(), joined_from)
            else:
                options = QgsRasterLayer.LayerOptions()
                options.loadDefaultStyle = False
                options.skipCrsValidation = True
                layer = QgsRasterLayer(value, name, "gdal", options)
                if layer.isValid():
                    found["problem"] = raster_file_problem(value)
                    _declare_raster_crs(layer)
                    read_ahead(layer)
            if not layer.isValid():
                background.delete_here(layer)
                return None
            if provenance and not (layer.isSpatial() and not layer.crs().isValid()):

                _stamp_provenance(layer, provenance)
            return layer

        built[value] = (built_here(make), found["problem"])
    for layer_id, source in (reads or {}).items():
        if entry.get("status") != "running":
            break
        read_ahead_memory(source, layer_id)
    return built


def _outputs_built(task_id: str, results, context, built, error: str) -> None:


    entry = _PROCESSING_TASKS.get(task_id) or _CANCELED_AT_UNLOAD.pop(task_id, None)

    layers = ({value: (background.take(handle), problem) for value, (handle, problem) in built.items()}
              if isinstance(built, dict) else {})
    if entry is None or entry.get("status") != "running":
        layers.clear()
        if entry is not None and entry.get("status") == "canceled":
            _cleanup_canceled_destination(entry)
            _let_go(entry)
        return
    if error:
        log_warning(f"Outputs of {entry.get('algorithm')} not built off the main thread: {error[:300]}")
    _finish_outputs(entry, results, context, built=layers)
    layers.clear()
    _let_go(entry)


    for other in list(_PROCESSING_TASKS.values()):
        if other.get("sequence") is not None and other.get("status") == "running":
            other["sequence"].advance()


def _sync_with_qgis(task_id: str) -> None:














    entry = _PROCESSING_TASKS.get(task_id)
    if entry is None or entry.get("status") != "running":
        return
    if entry.get("sequence") is not None:


        entry["sequence"].advance()
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
    if entry.get("sequence") is not None:

        out.update(entry["sequence"].report())
    elif entry["status"] == "complete":
        out["outputs"] = entry.get("outputs", {})

        for key in (
            "empty_outputs",
            "log",
            "feature_count",
            "source_unchanged",


            "separate_qgis",
            "main_thread_s",
            "output_crs",
            "empty_count",
            "invalid_count",


            "checks",


            "files_written",


            "repairs",
            "selection",
            "defaults",

            "measurement",

            "warning",


            "note",
        ):
            if key in entry and entry[key] is not None:
                out[key] = entry[key]
    elif entry["status"] == "error":
        out["error"] = entry.get("error")

        for key in ("code", "suggestion", "files_written", "hint", "layer", "fid"):
            if entry.get(key) is not None and entry.get(key) != "":
                out[key] = entry[key]
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
        "_hint": "get_task_status(task_id) gives progress/outputs; cancel_task(task_id) stops a running one.",
    }


def _cancel_task(args: dict) -> dict:
    task_id = args.get("task_id")
    entry = _PROCESSING_TASKS.get(task_id)
    if entry is None:
        return {"_error": f"Unknown task_id: {task_id}", "_code": "INVALID_ARGS"}
    if entry.get("sequence") is not None and entry.get("status") == "running":

        entry["status"] = "canceled"
        entry["sequence"].cancel()

        return {"task_id": task_id, "status": entry["status"]}
    if entry.get("finished_writing"):

        return {"task_id": task_id, "status": entry["status"], "note": "Task already finished."}
    task = entry.get("task")
    if task is not None and entry.get("status") == "running":


        entry["status"] = "canceled"
        try:
            task.cancel()
        except Exception:  # nosec B110
            pass
        return {"task_id": task_id, "status": "canceled"}
    return {"task_id": task_id, "status": entry["status"], "note": "Task already finished."}


def shutdown() -> int:












    asked = 0
    for entry in list(_PROCESSING_TASKS.values()):
        if entry.get("sequence") is not None and entry.get("status") == "running":

            entry["status"] = "canceled"
            try:
                entry["sequence"].cancel()
                asked += 1
            except Exception:  # nosec B110
                pass
            continue
        task = entry.get("task")
        if task is None or entry.get("status") != "running" or entry.get("finished_writing"):
            continue
        entry["status"] = "canceled"
        try:
            task.cancel()
            asked += 1
        except Exception:  # nosec B110
            pass

    for task_id, entry in _PROCESSING_TASKS.items():
        if entry.get("status") == "canceled" and entry.get("sequence") is None and "task" in entry:


            _CANCELED_AT_UNLOAD[task_id] = {"status": "canceled", "temporary_dir": entry.get("temporary_dir"),
                                            "destination_paths": list(entry.get("destination_paths") or []),
                                            "held_layers": entry.pop("held_layers", None) or []}
    for entry in _PROCESSING_TASKS.values():
        _release_held(entry)
    _PROCESSING_TASKS.clear()
    return asked
