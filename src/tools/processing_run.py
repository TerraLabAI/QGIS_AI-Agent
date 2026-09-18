# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""_run_processing and the QgsTask registry its async runs and batch runs live in."""
from __future__ import annotations

import os
import re
import time
import uuid
from collections import OrderedDict

import processing
from qgis.core import (
    Qgis,
    QgsApplication,
    QgsMapLayer,
    QgsProcessingAlgRunnerTask,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingUtils,
    QgsProject,
    QgsRasterLayer,
    QgsTask,
    QgsVectorLayer,
    QgsWkbTypes,
)

from ..core import layer_order
from ..core.host_platform import remove_quietly, remove_tree
from ..core.logger import log_warning
from ..core.policy import AGENT_TMP_DIR, create_managed_temp_dir
from ..core.qt_compat import enum_member
from ..core.security import validate_path
from ..core.serialization import cut_string
from .layer_io_tools import _release_layers_at_path, _same_file
from .layer_lookup import _find_layer
from .postconditions import report_checks
from .processing_decisions import (
    _FIELD_EXISTS_RE,
    _goes_to_task,
    _processing_error,
    _unsafe_processing_algorithm,
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
    _gdal_format_options,
    _geographic_distance_check,
    _grid_sanity,
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
)
from .stac_tools import normalise_crs



_PROCESSING_TASKS: dict[str, dict] = {}

_CANCELED_AT_UNLOAD: dict[str, dict] = {}








_FAILED_CALLS: OrderedDict[str, dict] = OrderedDict()
_FAILED_CALLS_MAX = 64


_TRANSIENT_CODES = frozenset({"RUN_BUDGET", "PERMISSION_DENIED", "TIMEOUT", "CANCELLED",
                              "NETWORK_ERROR", "REPEATED_FAILURE"})
_TRANSIENT_MARKS = ("still held by another process", "is not answering", "was stopped")
_EXTERNAL_PREFIXES = ("saga:", "sagang:", "grass:", "grass7:", "otb:", "r:")
_REPEAT_SUGGESTION = ("Do not send it again. Change the algorithm (find_processing_algorithm for the same "
                      "operation), the input, or the output path, or tell the user what is in the way.")
_REPEAT_EXTERNAL_SUGGESTION = ("This provider runs outside QGIS and fails the same way every time. Use the "
                               "native: or gdal: algorithm for the same operation instead.")


def _call_signature(algorithm_id, parameters) -> str:
    """The call as a string, scoped to the run that makes it, or "" when it cannot be written."""
    import json

    try:
        return json.dumps({"run": str(layer_order.current_run() or ""), "alg": str(algorithm_id),
                           "params": parameters}, sort_keys=True, default=str)[:4000]
    except Exception:  # noqa: BLE001 - a call we cannot write down is a call we do not remember
        return ""


def _repeat_refusal(signature: str, algorithm_id: str) -> dict | None:
    """The first failure of this exact call, with something else to do, or None."""
    first = _FAILED_CALLS.get(signature)
    if first is None:
        return None
    _FAILED_CALLS.move_to_end(signature)
    first["attempts"] = first.get("attempts", 1) + 1
    alternative = (_REPEAT_EXTERNAL_SUGGESTION
                   if str(algorithm_id).casefold().startswith(_EXTERNAL_PREFIXES)
                   else _REPEAT_SUGGESTION)
    return {
        "_error": (f"This {algorithm_id} call, with these exact parameters, already failed in this answer: "
                   f"{first['error']} Nothing was run, because nothing about it changed."),
        "code": "REPEATED_FAILURE",
        "attempts": first["attempts"],
        "first_failure": first["error"],
        "first_suggestion": first.get("suggestion") or "",
        "suggestion": alternative,
    }


def _remember_failure(signature: str, failure: dict) -> None:
    if not signature or not isinstance(failure, dict):
        return
    message = str(failure.get("_error") or failure.get("error") or "")
    if not message:
        return
    code = str(failure.get("code") or failure.get("_code") or "")
    if code in _TRANSIENT_CODES or any(mark in message for mark in _TRANSIENT_MARKS):
        return
    _FAILED_CALLS[signature] = {"error": cut_string(message, 400),
                                "suggestion": cut_string(str(failure.get("suggestion") or ""), 300),
                                "attempts": 1}
    _FAILED_CALLS.move_to_end(signature)
    while len(_FAILED_CALLS) > _FAILED_CALLS_MAX:
        _FAILED_CALLS.popitem(last=False)

class _CapturingFeedback(QgsProcessingFeedback):
    """Feedback that records error/warning text so a failed async task can report the real reason instead of pointing the agent at the message log."""


    def __init__(self):
        super().__init__()
        self.errors: list[str] = []
        self.console: list[str] = []

    def reportError(self, error, fatalError=False):  # noqa: N802 (Qt signature)
        self.errors.append(str(error))
        super().reportError(error, fatalError)

    def pushWarning(self, warning):  # noqa: N802 (Qt signature)
        self.errors.append(str(warning))
        super().pushWarning(warning)



    def pushConsoleInfo(self, info):  # noqa: N802 (Qt signature)
        lines = [line.strip() for line in str(info).splitlines() if line.strip()]
        self.console = (self.console + lines)[-40:]
        super().pushConsoleInfo(info)

    def console_tail(self, count: int = 4) -> list[str]:
        """The last console lines that read like a failure, else the last lines."""
        flagged = [line for line in self.console
                   if any(word in line.lower() for word in ("error", "fail", "could not", "cannot", "invalid"))]
        return (flagged or self.console)[-count:]


def _inject_gdal_crs(alg, params: dict) -> None:
    """Fill SOURCE_CRS for GDAL algorithms from the input layer, to avoid a WKT mismatch."""







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
    """True when the algorithm has a boolean OVERWRITE this call leaves false, so it adds to its output file."""
    try:
        definition = alg.parameterDefinition("OVERWRITE")
    except Exception:  # noqa: BLE001 - an algorithm without the lookup overwrites as before
        return False
    if definition is None or definition.type() != "boolean":
        return False
    value = parameters.get("OVERWRITE", definition.defaultValue())
    if isinstance(value, str):
        return value.strip().lower() in ("false", "0", "no")
    return value is not None and not bool(value)


def _vector_destination(definition) -> bool:
    """A feature sink or vector destination: what QGIS writes through an OGR driver."""
    try:
        return definition.type() in ("sink", "vectorDestination")
    except Exception:  # noqa: BLE001 - a definition that cannot say is left as given
        return False


_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]+:")


def _ogr_driver_for(path: str) -> str:
    """The OGR driver the path's extension selects, "" when none does; a URI or an unanswered lookup is kept."""
    if _URI_SCHEME.match(path):
        return "uri"
    ext = os.path.splitext(path)[1].lstrip(".")
    if not ext:
        return ""
    try:
        from qgis.core import QgsVectorFileWriter

        return str(QgsVectorFileWriter.driverForExtension(ext) or "")
    except Exception:  # noqa: BLE001 - no answer keeps the path as given
        return "unknown"


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


def _run_processing(args: dict, algorithm=None) -> dict:
    """run_processing, with the same call's earlier failure answered from memory."""





    signature = _call_signature(args.get("algorithm_id"), args.get("parameters", {}))
    repeat = _repeat_refusal(signature, args.get("algorithm_id")) if signature else None
    if repeat is not None:
        return repeat
    out = _run_processing_once(args, algorithm)
    if isinstance(out, dict) and out.get("_error"):
        _remember_failure(signature, out)
    elif isinstance(out, dict) and out.get("task_id") and out.get("status") == "running":


        entry = _PROCESSING_TASKS.get(out["task_id"])
        if entry is not None:
            entry["signature"] = signature
    return out


def _run_processing_once(args: dict, algorithm=None) -> dict:
    """run_processing."""


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




    parameters, _resolved_inputs = _resolve_layer_inputs(parameters, alg)



    wrong_parameters = _parameter_sanity(alg, parameters)
    if wrong_parameters:
        return wrong_parameters
    dropped = ignored_parameters(alg, parameters)

    in_degrees = _geographic_distance_check(alg, parameters, bool(args.get("confirm_large")))
    if in_degrees:
        return in_degrees
    terrain_in_degrees = _terrain_on_degrees_check(algorithm_id, parameters)
    if terrain_in_degrees:
        return terrain_in_degrees
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
    window_repairs = _window_degrees_repair(parameters) + _window_order_repair(parameters)
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
                    if _vector_destination(definition) and not _ogr_driver_for(expanded):



                        expanded += ".gpkg"
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
                    "suggestion": "Write the output to a new file or a new table name, then use that layer."}





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
                    "suggestion": f"Pass the layer under its exact input name: {', '.join(missing)}."}






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

    feedback = _CapturingFeedback()
    context = QgsProcessingContext()
    context.setProject(QgsProject.instance())
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



    repairs: list[str] = renamed_outputs + list(window_repairs) + streamed_copies
    if algorithm_id.startswith("gdal:"):
        _ensure_proj_env()
        _inject_gdal_crs(alg, sanitized_parameters)
        repairs += _gdal_format_options(algorithm_id, sanitized_parameters)

    try:
        valid, detail = alg.checkParameterValues(sanitized_parameters, context)
    except Exception as exc:  # noqa: BLE001 - a broken provider preflight must not clear a destination
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
        except Exception as exc:  # noqa: BLE001 - the native validation message still stands alone
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
                "Save or roll back those edits, then run the algorithm again; nothing was run."
            )
        }




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












    if _goes_to_task(alg, args, parameters):
        started = _start_async_processing(
            alg,
            algorithm_id,
            sanitized_parameters,
            args.get("output_name"),
            invalid_geometry_filter,
            destination_paths=file_destination_paths,
            temporary_dir=managed_output_dir,
        )
        if dropped:
            repairs += dropped
        if repairs:
            started["repairs"] = repairs
        return started

    target = algorithm if algorithm is not None else algorithm_id
    try:
        result = processing.run(target, sanitized_parameters, feedback=feedback, context=context)
    except Exception as e:


        by_object = _inputs_as_layers(alg, sanitized_parameters, str(e))
        if by_object is not None:
            try:
                result = processing.run(target, by_object, feedback=feedback, context=context)
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
                        released.extend(_release_layers_at_path(item, skip_ids=input_ids,
                                                                delete_existing=not appends))
                try:
                    result = processing.run(target, sanitized_parameters, feedback=feedback, context=context)
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
            failed["suggestion"] = ("This external provider wrote nothing; its log is in the message. Look for "
                                    "the same operation among the native: or gdal: algorithms (list_algorithms), "
                                    "which run inside QGIS, before retrying this one.")
        return failed

    provenance = _record_history(alg, sanitized_parameters, context)
    out = {"algorithm": algorithm_id,
           "outputs": _process_outputs(result, output_name=args.get("output_name"), provenance=provenance,
                                       destination_parameters=sanitized_parameters)}
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







def raster_file_problem(path: str) -> str:
    """Why GDAL cannot read a GeoTIFF back to its last row, or "" when it can."""
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
                     destination_parameters=None) -> dict:
    """Add output layers to the project and return a JSON-safe outputs summary."""







    output = {}
    added = []
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
            QgsProject.instance().addMapLayer(value)
            added.append((key, value))
            output[key] = _layer_summary(value)
        elif isinstance(value, str) and "|layername=" in value and os.path.exists(value.split("|", 1)[0]):

            file_part = value.split("|", 1)[0]
            path_error = validate_path(file_part, write=False)
            if path_error:
                output[key] = {"path": value, "readable": False, "warning": path_error}
                continue
            layer = _native_output_layer(value, context)
            if layer is not None and layer.isValid():
                QgsProject.instance().addMapLayer(layer)
                added.append((key, layer))
                output[key] = _layer_summary(layer, value)
            else:
                output[key] = {"path": value, "readable": False}
        elif isinstance(value, str) and os.path.exists(value):
            path_error = validate_path(value, write=False)
            if path_error:
                output[key] = {"path": value, "readable": False, "warning": path_error}
                continue
            layer = _native_output_layer(value, context)
            problem = raster_file_problem(value) if isinstance(layer, QgsRasterLayer) else ""
            if problem:
                output[key] = {"path": value, "readable": False, "unreadable": problem[:300],
                               "warning": "GDAL cannot read this file to its last row: it was written "
                                          "incompletely and was not loaded. Do not use it; write it again "
                                          "to another folder."}
            elif layer is not None and layer.isValid():
                _declare_raster_crs(layer)
                QgsProject.instance().addMapLayer(layer)
                added.append((key, layer))
                output[key] = _layer_summary(layer, value)
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
                QgsProject.instance().addMapLayer(layer)
                added.append((key, layer))
                output[key] = _layer_summary(layer)
            elif isinstance(value, str) and _looks_like_file(value) and not os.path.exists(os.path.expanduser(value)):



                output[key] = {"path": value, "readable": False, "missing": True,
                               "warning": "the algorithm finished without writing this file; read log"}
            else:



                output[key] = _plain_output(value)
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
    if provenance and added:
        for _key, layer in added:
            _stamp_provenance(layer, provenance)
    return output


def _native_output_layer(value: str, context=None):
    """Load a Processing output with QGIS' resolver, including non-vector layer types."""
    lookup = context or QgsProcessingContext()
    if context is None:
        lookup.setProject(QgsProject.instance())
    try:
        layer = QgsProcessingUtils.mapLayerFromString(value, lookup, True)
    except Exception:  # noqa: BLE001 - an unreadable output is reported by the caller
        return None
    if layer is None or not layer.isValid():
        return None
    if QgsProject.instance().mapLayer(layer.id()) is None:
        try:
            lookup.temporaryLayerStore().takeMapLayer(layer)
        except Exception as exc:  # noqa: BLE001 - older QGIS may not expose ownership transfer
            log_warning(f"takeMapLayer ownership transfer failed: {exc}")
    return layer





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
           "readable": True, "added_to_project": True}
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
    destination_paths=None,
    temporary_dir=None,
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
        "run_token": layer_order.current_run(),
        "destination_paths": list(destination_paths or []),

        "temporary_dir": temporary_dir,
    }
    task.progressChanged.connect(lambda p, tid=task_id: _on_proc_progress(tid, p))






    task.executed.connect(
        lambda ok, results, tid=task_id, ctx=context, fb=feedback: _on_proc_done(tid, ok, results, ctx))






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


def _cleanup_canceled_destination(entry: dict) -> None:
    """Remove whatever a cancelled task had already started writing."""













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
    """Drop *paths* from the destinations of every cancelled task, whose cleanup would remove them."""
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
    """Whether ``path`` is a file a cancelled task was writing, which its cleanup removes."""




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
    """A task that failed or was killed without ever emitting ``executed``."""





    entry = _PROCESSING_TASKS.get(task_id) or _CANCELED_AT_UNLOAD.pop(task_id, None)
    if entry is None:
        return
    if entry.get("status") == "canceled":




        _cleanup_canceled_destination(entry)
        for heavy in ("task", "context", "feedback", "alg", "parameters"):
            entry.pop(heavy, None)
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
    for heavy in ("task", "context", "feedback", "alg", "parameters"):
        entry.pop(heavy, None)


def _on_proc_progress(task_id: str, progress: float):
    entry = _PROCESSING_TASKS.get(task_id)
    if entry is not None and entry.get("status") == "running":
        entry["progress"] = int(progress)


def _on_proc_done(task_id: str, ok: bool, results, context):

    entry = _PROCESSING_TASKS.get(task_id) or _CANCELED_AT_UNLOAD.pop(task_id, None)
    if entry is None:
        return
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
        try:
            provenance = None
            if entry.get("alg") is not None:
                provenance = _record_history(entry["alg"], entry.get("parameters") or {}, context)
            with layer_order.adopted(entry.get("run_token")):
                entry["outputs"] = _process_outputs(
                    results,
                    context,
                    output_name=entry.get("output_name"),
                    provenance=provenance,
                    destination_parameters=entry.get("parameters") or {},
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
    else:
        entry["status"] = "error"
        feedback = entry.get("feedback")
        captured = "; ".join(getattr(feedback, "errors", [])[-5:]) if feedback else ""
        entry["error"] = (
            f"Algorithm reported failure: {captured}"
            if captured
            else "Algorithm reported failure, check get_message_log for details."
        )


    if entry.get("status") == "error" and entry.get("signature"):
        _remember_failure(entry["signature"],
                          {"_error": entry.get("error"), "suggestion": entry.get("suggestion")})

    for heavy in ("task", "context", "feedback", "alg", "parameters"):
        entry.pop(heavy, None)


def _sync_with_qgis(task_id: str) -> None:
    """Close an entry whose QgsTask is already over, however it ended."""













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
            "isolated_process",
            "output_crs",
            "empty_count",
            "invalid_count",


            "checks",


            "files_written",
        ):
            if key in entry and entry[key] is not None:
                out[key] = entry[key]
    elif entry["status"] == "error":
        out["error"] = entry.get("error")
        for key in ("code", "suggestion", "files_written"):
            if entry.get(key):
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
        "_hint": "Poll get_task_status(task_id) for progress/outputs; cancel_task(task_id) stops a running one.",
    }


def _cancel_task(args: dict) -> dict:
    task_id = args.get("task_id")
    entry = _PROCESSING_TASKS.get(task_id)
    if entry is None:
        return {"_error": f"Unknown task_id: {task_id}", "_code": "INVALID_ARGS"}
    if entry.get("sequence") is not None and entry.get("status") == "running":

        entry["status"] = "canceled"
        entry["sequence"].cancel()
        return {"task_id": task_id, "status": "canceled"}
    task = entry.get("task")
    if task is not None and entry.get("status") == "running":


        entry["status"] = "canceled"
        try:
            task.cancel()
        except Exception:  # nosec B110 - task may already be gone
            pass
        return {"task_id": task_id, "status": "canceled"}
    return {"task_id": task_id, "status": entry["status"], "note": "Task already finished."}


def shutdown() -> int:
    """Cancel every task this module still has running, and forget them."""











    asked = 0
    for entry in list(_PROCESSING_TASKS.values()):
        if entry.get("sequence") is not None and entry.get("status") == "running":

            entry["status"] = "canceled"
            try:
                entry["sequence"].cancel()
                asked += 1
            except Exception:  # nosec B110 - a sequence whose tasks are already gone needs nothing
                pass
            continue
        task = entry.get("task")
        if task is None or entry.get("status") != "running":
            continue
        entry["status"] = "canceled"
        try:
            task.cancel()
            asked += 1
        except Exception:  # nosec B110 - a task already gone needs nothing
            pass

    for task_id, entry in _PROCESSING_TASKS.items():
        if entry.get("status") == "canceled" and entry.get("sequence") is None and "task" in entry:

            _CANCELED_AT_UNLOAD[task_id] = {"status": "canceled", "temporary_dir": entry.get("temporary_dir"),
                                            "destination_paths": list(entry.get("destination_paths") or [])}
    _PROCESSING_TASKS.clear()
    return asked
