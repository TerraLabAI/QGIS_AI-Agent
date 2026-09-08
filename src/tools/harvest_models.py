# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Processing providers, batches and models."""










from __future__ import annotations

import os
import re
import time

import processing
from qgis.core import (
    Qgis,
    QgsApplication,
    QgsProcessingDestinationParameter,
    QgsProcessingFeedback,
    QgsProcessingModelAlgorithm,
    QgsProcessingModelChildAlgorithm,
    QgsProcessingModelChildParameterSource,
    QgsProcessingModelOutput,
    QgsProcessingModelParameter,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterCrs,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterDistance,
    QgsProcessingParameterEnum,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterFile,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterNumber,
    QgsProcessingParameterPoint,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
    QgsProcessingParameterVectorLayer,
)
from qgis.PyQt.QtCore import QPointF

from ..core import limits
from ..core.logger import log, log_warning
from ..core.security import expand_path
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .core_tools import _process_outputs, _run_processing
from .data_tools import _avoid_reserved_name
from .harvest_project import qgis_enum







_BATCH_TIMEOUT = limits.CALL_MAX_SECONDS_MAIN
_INPUT_TYPES = ["vector", "feature_source", "raster", "field", "number", "integer", "distance", "string",
                "boolean", "extent", "crs", "point", "file", "folder", "enum", "multiple_layers"]


def register_harvest_models_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="get_processing_providers",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_get_processing_providers,
    ))

    registry.register(Tool(
        name="execute_processing_batch",
        input_schema={
            "type": "object",
            "properties": {
                "algorithm": {"type": "string"},
                "parameters_list": {"type": "array", "items": {"type": "object"},
                                    "minItems": 1, "maxItems": 100},
                "timeout": {"type": "number", "minimum": 0.1, "maximum": _BATCH_TIMEOUT},
            },
            "required": ["algorithm", "parameters_list"],
        },
        handler=_execute_processing_batch,
    ))

    registry.register(Tool(
        name="create_processing_model",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "object"}, "minItems": 1, "maxItems": 100},
                "inputs": {"type": "array", "items": {"type": "object"}, "maxItems": 100},
                "outputs": {"type": "array", "items": {"type": "object"}, "maxItems": 100},


                "description": {"type": "string"},
                "group": {"type": "string"},
            },
            "required": ["name", "steps"],
        },
        handler=_create_processing_model,
        destructive=True,
    ))

    registry.register(Tool(
        name="list_processing_models",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_list_processing_models,
    ))

    registry.register(Tool(
        name="run_model",
        input_schema={
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "parameters": {"type": "object"},
            },
            "required": ["model"],
        },
        handler=_run_model,
    ))






class _SpecError(ValueError):
    """A spec problem the caller can fix; surfaces as INVALID_ARGS."""


def _resolve_algorithm_id(hint: str) -> str:
    """Resolve an algorithm hint ('buffer', 'native:buffer') to a full id, or raise _SpecError."""




    registry = QgsApplication.processingRegistry()
    if not isinstance(hint, str) or not hint.strip():
        raise _SpecError("Algorithm hint must be a non-empty string")
    hint_clean = hint.strip()
    alg = registry.algorithmById(hint_clean)
    if alg is not None:
        return alg.id()

    hint_lower = hint_clean.lower()
    exact_name, suffix_id, contains = [], [], []
    for alg in registry.algorithms():
        alg_id = alg.id()
        id_suffix = alg_id.split(":", 1)[-1].lower()
        display = alg.displayName().lower()
        if display == hint_lower:
            exact_name.append(alg)
        elif id_suffix == hint_lower:
            suffix_id.append(alg)
        elif hint_lower in display or hint_lower in id_suffix:
            contains.append(alg)

    for group in (exact_name, suffix_id, contains):
        if len(group) == 1:
            return group[0].id()
        natives = [a for a in group if a.provider().id() == "native"]
        if len(natives) == 1:
            return natives[0].id()

    candidates = exact_name + suffix_id + contains
    if not candidates:
        raise _SpecError(
            f"No Processing algorithm matches '{hint_clean}'. "
            "Pass a keyword found in the algorithm name or its full id (e.g. 'native:buffer')."
        )
    sample = ", ".join(
        f"{a.id()} ({a.displayName()})"
        for a in sorted(candidates, key=lambda a: (a.provider().id() != "native", len(a.id())))[:8]
    )
    raise _SpecError(f"Algorithm hint '{hint_clean}' is ambiguous. Candidates: {sample}. Use the full id.")


def _build_param_source(value, defined_inputs: set, defined_steps: set) -> list:
    """Turn a JSON value into QgsProcessingModelChildParameterSource(s)."""




    src = QgsProcessingModelChildParameterSource
    if isinstance(value, list):
        return [_build_param_source(v, defined_inputs, defined_steps)[0] for v in value]
    if isinstance(value, str):
        if value.startswith("@"):
            ref = value[1:]
            if ref not in defined_inputs:
                raise _SpecError(f"Parameter reference '{value}' points to undefined model input '{ref}'")
            return [src.fromModelParameter(ref)]
        if value.startswith("$"):
            rest = value[1:]
            if "." not in rest:
                raise _SpecError(f"Step output reference '{value}' must be in '$step_id.OUTPUT_NAME' form")
            child_id, output_name = rest.split(".", 1)
            if child_id not in defined_steps:
                raise _SpecError(f"Step output reference '{value}' points to undefined step '{child_id}'")
            return [src.fromChildOutput(child_id, output_name)]
        if value.startswith("="):
            return [src.fromExpression(value[1:])]
    return [src.fromStaticValue(value)]


_INPUT_ALIASES = {
    "vector_layer": "vector", "source": "feature_source", "raster_layer": "raster", "int": "integer",
    "float": "number", "double": "number", "bool": "boolean", "layers": "multiple_layers",
}
_INPUT_CLASSES = {
    "vector": QgsProcessingParameterVectorLayer,
    "feature_source": QgsProcessingParameterFeatureSource,
    "raster": QgsProcessingParameterRasterLayer,
    "number": QgsProcessingParameterNumber,
    "string": QgsProcessingParameterString,
    "extent": QgsProcessingParameterExtent,
    "point": QgsProcessingParameterPoint,
    "file": QgsProcessingParameterFile,
    "multiple_layers": QgsProcessingParameterMultipleLayers,
}


def _input_field(name, description, default, spec):
    parent = spec.get("parent_layer")
    if not parent:
        raise _SpecError(f"Input '{name}' of type 'field' requires 'parent_layer'")
    return QgsProcessingParameterField(name, description, parentLayerParameterName=parent, defaultValue=default)


def _input_integer(name, description, default, spec):
    param = QgsProcessingParameterNumber(name, description, defaultValue=default)
    integer = (qgis_enum(QgsProcessingParameterNumber, "Type.Integer", "Integer")
               or qgis_enum(Qgis, "ProcessingNumberParameterType.Integer"))
    if integer is not None:
        param.setDataType(integer)
    return param


def _input_distance(name, description, default, spec):
    param = QgsProcessingParameterDistance(name, description, defaultValue=default)
    if spec.get("parent_layer"):
        param.setParentParameterName(spec["parent_layer"])
    return param


def _input_boolean(name, description, default, spec):
    value = bool(default) if default is not None else False
    return QgsProcessingParameterBoolean(name, description, defaultValue=value)


def _input_crs(name, description, default, spec):
    return QgsProcessingParameterCrs(name, description, defaultValue=default or "EPSG:4326")


def _input_folder(name, description, default, spec):
    param = QgsProcessingParameterFile(name, description, defaultValue=default)
    folder = (qgis_enum(Qgis, "ProcessingFileParameterBehavior.Folder")
              or qgis_enum(QgsProcessingParameterFile, "Behavior.Folder", "Folder"))
    if folder is not None:
        param.setBehavior(folder)
    return param


def _input_enum(name, description, default, spec):
    return QgsProcessingParameterEnum(name, description, options=spec.get("options") or [], defaultValue=default)


_INPUT_BUILDERS = {
    "field": _input_field, "integer": _input_integer, "distance": _input_distance, "boolean": _input_boolean,
    "crs": _input_crs, "folder": _input_folder, "enum": _input_enum,
}


def _make_input_definition(spec: dict):
    raw_type = str(spec.get("type") or "string").lower()
    type_name = _INPUT_ALIASES.get(raw_type, raw_type)
    name = spec["name"]
    description = spec.get("description", name)
    default = spec.get("default")
    builder = _INPUT_BUILDERS.get(type_name)
    param_class = _INPUT_CLASSES.get(type_name)
    if builder is not None:
        param = builder(name, description, default, spec)
    elif param_class is not None:
        param = param_class(name, description, defaultValue=default)
    else:
        raise _SpecError(f"Unsupported input type '{raw_type}' for input '{name}'. Types: {', '.join(_INPUT_TYPES)}")
    if spec.get("optional"):
        optional = (qgis_enum(Qgis, "ProcessingParameterFlag.Optional")
                    or qgis_enum(QgsProcessingParameterDefinition, "FlagOptional"))
        if optional is not None:
            param.setFlags(param.flags() | optional)
    return param


def _models_folder():
    """The model provider's folder and the provider itself (None when Processing lacks it)."""
    provider = QgsApplication.processingRegistry().providerById("model")
    folder = None
    if provider is not None and hasattr(provider, "modelsFolder"):
        try:
            folder = provider.modelsFolder()
        except Exception:  # nosec B110 - provider metadata is optional
            folder = None
    if not folder:
        folder = os.path.join(QgsApplication.qgisSettingsDirPath(), "processing", "models")
    return folder, provider


_UNSAFE_IN_A_FILENAME = re.compile(r"[^A-Za-z0-9 _.-]")


def safe_model_filename(name: str) -> str:
    """The model's name reduced to something that can only be a file in one folder."""






    cleaned = _UNSAFE_IN_A_FILENAME.sub("_", str(name or "")).strip(" .")
    while ".." in cleaned:
        cleaned = cleaned.replace("..", ".")
    return _avoid_reserved_name(cleaned.strip(" .") or "model")


def _unique_model_path(folder: str, name: str):
    """(final_name, path): the requested name, or name_2, name_3, ... when the file exists."""
    name = safe_model_filename(name)
    path = os.path.join(folder, f"{name}.model3")
    if not os.path.exists(path):
        return name, path
    for suffix in range(2, 1001):
        candidate = f"{name}_{suffix}"
        candidate_path = os.path.join(folder, f"{candidate}.model3")
        if not os.path.exists(candidate_path):
            return candidate, candidate_path
    raise _SpecError(f"Could not find a unique name for '{name}' in {folder} (tried up to _1000)")


def _build_model(name: str, steps: list, inputs: list, outputs: list, description: str, group: str):
    """The QgsProcessingModelAlgorithm for a validated spec, plus the resolved step list."""
    registry = QgsApplication.processingRegistry()
    model = QgsProcessingModelAlgorithm()
    model.setName(name)
    if group:
        model.setGroup(group)
    if description:
        try:
            model.setHelpContent({"ALG_DESC": description})
        except Exception:  # nosec B110 - provider metadata is optional
            pass

    defined_inputs = set()
    for idx, spec in enumerate(inputs):
        if not isinstance(spec, dict) or "name" not in spec:
            raise _SpecError(f"Input #{idx} must be a dict with at least 'name'")
        param_def = _make_input_definition(spec)
        model_param = QgsProcessingModelParameter(spec["name"])
        model_param.setPosition(QPointF(50.0, 50.0 + idx * 100.0))
        model.addModelParameter(param_def, model_param)
        defined_inputs.add(spec["name"])



    resolved = []
    seen = set()
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            raise _SpecError(f"Step #{idx} must be a dict")
        for required in ("id", "algorithm"):
            if required not in step:
                raise _SpecError(f"Step #{idx} missing required key '{required}'")
        if step["id"] in seen:
            raise _SpecError(f"Duplicate step id '{step['id']}'")
        seen.add(step["id"])
        try:
            alg_id = _resolve_algorithm_id(step["algorithm"])
        except _SpecError as e:
            raise _SpecError(f"Step '{step['id']}': {e}") from e
        valid_params = {p.name() for p in registry.algorithmById(alg_id).parameterDefinitions()}
        for pname in step.get("parameters") or {}:
            if pname not in valid_params:
                raise _SpecError(f"Step '{step['id']}' (algorithm '{alg_id}'): unknown parameter "
                                 f"'{pname}'. Valid parameters: {sorted(valid_params)}")
        resolved.append((step, alg_id))

    step_to_alg = {step["id"]: alg_id for step, alg_id in resolved}
    outputs_by_step: dict = {}
    for idx, out in enumerate(outputs):
        if not isinstance(out, dict):
            raise _SpecError(f"Output #{idx} must be a dict")
        for required in ("name", "from_step", "from_output"):
            if required not in out:
                raise _SpecError(f"Output #{idx} missing required key '{required}'")
        if out["from_step"] not in step_to_alg:
            raise _SpecError(f"Output '{out['name']}': from_step '{out['from_step']}' is not a defined step")
        valid_outputs = {o.name() for o in registry.algorithmById(step_to_alg[out["from_step"]]).outputDefinitions()}
        if out["from_output"] not in valid_outputs:
            raise _SpecError(f"Output '{out['name']}': '{out['from_output']}' is not an output of step "
                             f"'{out['from_step']}' (algorithm '{step_to_alg[out['from_step']]}'). "
                             f"Valid outputs: {sorted(valid_outputs)}")
        outputs_by_step.setdefault(out["from_step"], {})[out["name"]] = out

    defined_steps: list = []
    for step_idx, (step, alg_id) in enumerate(resolved):
        child = QgsProcessingModelChildAlgorithm(alg_id)
        child.setChildId(step["id"])
        child.setDescription(step.get("description") or registry.algorithmById(alg_id).displayName())
        child.setPosition(QPointF(300.0 + step_idx * 250.0, 50.0))
        for pname, pvalue in (step.get("parameters") or {}).items():
            child.addParameterSources(pname, _build_param_source(pvalue, defined_inputs, set(defined_steps)))
        step_outputs = outputs_by_step.get(step["id"], {})
        if step_outputs:
            model_outputs = {}
            for out_name, out in step_outputs.items():
                model_output = QgsProcessingModelOutput(out_name)
                model_output.setChildId(step["id"])
                model_output.setChildOutputName(out["from_output"])
                model_output.setDescription(out.get("description") or out_name)
                model_outputs[out_name] = model_output
            child.setModelOutputs(model_outputs)
        model.addChildAlgorithm(child)
        defined_steps.append(step["id"])


    if not outputs and defined_steps:
        last_id = defined_steps[-1]
        last_alg = registry.algorithmById(step_to_alg[last_id])
        names = [o.name() for o in last_alg.outputDefinitions()] if last_alg else []
        preferred = "OUTPUT" if "OUTPUT" in names else (names[0] if names else None)
        if preferred:
            model_output = QgsProcessingModelOutput("Result")
            model_output.setChildId(last_id)
            model_output.setChildOutputName(preferred)
            model_output.setDescription("Result")
            model.childAlgorithm(last_id).setModelOutputs({"Result": model_output})

    output_count = sum(len(v) for v in outputs_by_step.values()) or (1 if defined_steps else 0)
    return model, resolved, len(defined_inputs), output_count






def _get_processing_providers(args: dict) -> dict:
    providers = []
    for provider in QgsApplication.processingRegistry().providers():
        info = {"id": provider.id(), "name": provider.name(), "algorithm_count": len(provider.algorithms())}
        try:
            info["active"] = bool(provider.isActive())
        except Exception:  # nosec B110 - provider metadata is optional
            pass
        providers.append(info)
    providers.sort(key=lambda p: p["id"])
    return {"providers": providers, "count": len(providers)}


def _execute_processing_batch(args: dict) -> dict:
    try:
        algorithm_id = _resolve_algorithm_id(args["algorithm"])
    except _SpecError as e:
        return tool_error(str(e), "INVALID_ARGS", "list_algorithms finds the exact id.")
    parameters_list = args["parameters_list"]


    budget = min(float(args.get("timeout") or _BATCH_TIMEOUT), limits.CALL_MAX_SECONDS_MAIN)
    deadline = time.monotonic() + budget

    results = []
    for index, parameters in enumerate(parameters_list):
        if not isinstance(parameters, dict):
            results.append({"index": index, "status": "error",
                            "message": "each parameters_list entry must be an object"})
            continue
        if time.monotonic() >= deadline:
            results.append({"index": index, "status": "skipped",
                            "message": f"Batch budget of {budget:g}s exhausted before this run started"})
            continue
        started = time.monotonic()
        outcome = _run_processing({"algorithm_id": algorithm_id, "parameters": parameters})
        entry = {"index": index, "seconds": round(time.monotonic() - started, 2)}
        if isinstance(outcome, dict) and outcome.get("_error"):
            entry.update(status="error", message=str(outcome["_error"]))
        elif isinstance(outcome, dict) and outcome.get("task_id"):





            entry.update(status="running", task_id=outcome["task_id"],
                         message="Started in the background; poll get_task_status(task_id).")
        else:
            entry.update(status="success", outputs=outcome.get("outputs"))
            if outcome.get("outputs_note"):
                entry["outputs_note"] = outcome["outputs_note"]
        results.append(entry)

    running = [r["task_id"] for r in results if r["status"] == "running"]
    response = {"algorithm": algorithm_id, "results": results, "count": len(results),
                "succeeded": sum(1 for r in results if r["status"] == "success")}
    advice = []
    if running:
        response["running"] = running
        response["poll"] = {"tool": "get_task_status", "args": {"task_id": running[0]},
                            "label": f"Running {algorithm_id}"}
        advice.append(
            f"{len(running)} run(s) were too heavy to run inline and went to the background. "
            "They have produced nothing yet: poll get_task_status on each task_id before "
            "reporting the batch as done."
        )
    if any(r["status"] == "skipped" for r in results):
        response["timed_out"] = True
        advice.append("Raise timeout or split parameters_list; the completed runs are kept.")
    if advice:
        response["suggestion"] = " ".join(advice)
    return response


def _create_processing_model(args: dict) -> dict:
    name = str(args["name"]).strip()
    if not name:
        return tool_error("Model 'name' is required", "INVALID_ARGS", "Pass a short name such as 'buffer_and_clip'.")
    folder, provider = _models_folder()
    os.makedirs(folder, exist_ok=True)
    try:
        final_name, target_path = _unique_model_path(folder, name)
        model, resolved, input_count, output_count = _build_model(
            final_name, args["steps"], args.get("inputs") or [], args.get("outputs") or [],
            args.get("description") or "", args.get("group") or "Models",
        )
    except _SpecError as e:
        return tool_error(str(e), "INVALID_ARGS", "Fix the spec as described and call again; nothing was written.")

    if not model.toFile(target_path):
        return tool_error(f"Failed to write model to {target_path}", "EXECUTION_FAILED",
                          "Check the models folder is writable.")
    registered = False
    if provider is not None:
        try:
            provider.refreshAlgorithms()
            registered = True
        except Exception as e:
            log_warning(f"Model saved but provider refresh failed: {e}")
    log(f"Processing model '{final_name}' saved to {target_path}")
    return {
        "name": final_name,
        "requested_name": name,
        "id": f"model:{final_name}",
        "path": target_path,
        "registered": registered,
        "input_count": input_count,
        "step_count": len(resolved),
        "output_count": output_count,
        "resolved_steps": [{"id": step["id"], "algorithm": alg_id, "hint": step["algorithm"]}
                           for step, alg_id in resolved],
    }


def _list_processing_models(args: dict) -> dict:
    models = []
    for alg in QgsApplication.processingRegistry().algorithms():
        if alg.provider() is None or alg.provider().id() != "model":
            continue
        entry = {"id": alg.id(), "name": alg.displayName(), "group": alg.group()}
        try:
            entry["path"] = alg.sourceFilePath()
        except Exception:  # nosec B110 - model path is optional
            pass
        models.append(entry)
    models.sort(key=lambda m: m["id"])
    return {"models": models, "count": len(models)}


def _run_model(args: dict) -> dict:
    model = str(args["model"]).strip()
    parameters = dict(args.get("parameters") or {})
    registry = QgsApplication.processingRegistry()
    file_alg = None
    if model.lower().endswith(".model3"):
        path = expand_path(model)
        if not os.path.isfile(path):
            return tool_error(f"Model file not found: {path}", "INVALID_ARGS",
                              "Pass an existing .model3 path, or a registered id from list_processing_models.")
        file_alg = QgsProcessingModelAlgorithm()
        if not file_alg.fromFile(path):
            return tool_error(f"Failed to load model file: {path}", "EXECUTION_FAILED",
                              "The file is not a valid .model3; open it in the Model Designer to check.")
        file_alg.initAlgorithm()
        alg = file_alg
        model = path
    else:
        alg = registry.algorithmById(model)

        if alg is None and not model.startswith("model:"):
            alg = registry.algorithmById(f"model:{model}")
            if alg is not None:
                model = f"model:{model}"
        if alg is None:
            for candidate in registry.algorithms():
                if candidate.provider() and candidate.provider().id() == "model" and candidate.displayName() == model:
                    alg, model = candidate, candidate.id()
                    break
        if alg is None:
            return tool_error(f"Model not found: {model!r}", "INVALID_ARGS",
                              "Call list_processing_models for the registered ids, or pass a .model3 file path.")



    for param in alg.parameterDefinitions():
        if isinstance(param, QgsProcessingDestinationParameter):
            parameters.setdefault(param.name(), "TEMPORARY_OUTPUT")

    if file_alg is None:
        outcome = _run_processing({"algorithm_id": model, "parameters": parameters})
        if isinstance(outcome, dict) and not outcome.get("_error"):
            outcome["model"] = model
        return outcome






    from ..core.security import validate_path

    for key, value in list(parameters.items()):
        if not isinstance(value, str) or value in ("TEMPORARY_OUTPUT", "memory:"):
            continue
        upper = key.upper()
        if "OUTPUT" not in upper and "DEST" not in upper:
            continue
        if not (os.path.isabs(value) or value.startswith(("~", ".")) or "/" in value or "\\" in value):
            continue
        problem = validate_path(value.split("|", 1)[0], write=True)
        if problem:
            return tool_error(problem, "PERMISSION_DENIED",
                              "Write the model's output under the project folder, the user's home folder "
                              "or the temp folder, or pass TEMPORARY_OUTPUT.")




    from .processing_tools import _heavy_inputs

    if _heavy_inputs(parameters):
        return tool_error(
            "The inputs are too large to run a .model3 file on the main thread; nothing was run.",
            "INVALID_ARGS",
            "Add the model to the Processing toolbox (Models > Add Model to Toolbox) and call run_model "
            "with its registered id, which runs heavy inputs in the background, or clip the inputs first.")

    feedback = QgsProcessingFeedback()
    try:
        result = processing.run(file_alg, parameters, feedback=feedback)
    except Exception as e:
        return tool_error(f"Model run failed: {e}", "EXECUTION_FAILED",
                          "Check the parameter names against the model's inputs "
                          "(get_algorithm_help on a registered model).")
    return {"model": model, "outputs": _process_outputs(result)}
