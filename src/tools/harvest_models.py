# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Processing providers, batches and models."""










from __future__ import annotations

import os
import re
import time
import uuid

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

from ..core import layer_order, limits
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
    from .processing_tools import _threadable

    alg = QgsApplication.processingRegistry().algorithmById(algorithm_id)
    if _threadable(alg):
        return _BatchRun(alg, algorithm_id, parameters_list, args.get("timeout")).start()


    ceiling = limits.current("CALL_MAX_SECONDS_MAIN")
    budget = min(float(args.get("timeout") or ceiling), ceiling)
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


class _BatchRun:
    """execute_processing_batch over an algorithm that may leave the main thread."""
















    def __init__(self, alg, algorithm_id: str, parameters_list: list, timeout=None):
        from .processing_tools import _destination_names

        self.alg = alg
        self.outputs = _destination_names(alg)
        self.algorithm_id = algorithm_id
        self.pending = list(enumerate(parameters_list))
        self.count = len(self.pending)
        self.results: list[dict] = []

        self.running: dict[str, tuple] = {}
        self.width = max(1, int(limits.current("PROCESSING_BATCH_PARALLEL")))
        self._advancing = False
        self._again = False
        self.timed_out = False


        self.budget = float(timeout) if timeout else None
        self.deadline = time.monotonic() + self.budget if self.budget else None


        self.run_token = layer_order.current_run()
        self.task_id = "batch-" + uuid.uuid4().hex[:12]
        self.entry = {"status": "running", "progress": 0, "algorithm": algorithm_id,
                      "started_at": time.strftime("%H:%M:%S"), "sequence": self}

    def start(self) -> dict:
        from .processing_tools import _POLL_INTERVAL_S, _PROCESSING_TASKS, _sweep_consumed_tasks

        _sweep_consumed_tasks()
        _PROCESSING_TASKS[self.task_id] = self.entry
        self.advance()
        if self.entry["status"] != "running":

            _PROCESSING_TASKS.pop(self.task_id, None)
            return self.report()
        return {**self.report(), "task_id": self.task_id, "status": "running",
                "note": f"The runs go in the background, up to {self.width} at a time, and QGIS stays "
                        "responsive. Poll get_task_status(task_id).",
                "poll": {"tool": "get_task_status", "args": {"task_id": self.task_id},
                         "interval_s": _POLL_INTERVAL_S,
                         "label": f"Running {self.algorithm_id}, {self.count} runs"}}

    def report(self) -> dict:
        """The batch as execute_processing_batch has always answered it, for the runs settled so far."""
        results = sorted(self.results, key=lambda r: r["index"])
        out = {"algorithm": self.algorithm_id, "results": results, "count": self.count,
               "succeeded": sum(1 for r in results if r["status"] == "success")}
        if self.timed_out:
            out["timed_out"] = True
            out["suggestion"] = "Raise timeout or split parameters_list; the completed runs are kept."
        return out

    def advance(self) -> None:
        """Settle the entries whose task is over, then start every entry there is room for."""
        from .processing_tools import _PROCESSING_TASKS

        if self._advancing:


            self._again = True
            return
        self._advancing = True
        try:

            while self.entry["status"] == "running" and _PROCESSING_TASKS.get(self.task_id) is self.entry:
                self._again = False
                moved = self._settle() + self._fill()
                if not self.pending and not self.running:
                    self.entry.update(status="complete", progress=100)
                    return
                if not moved and not self._again:
                    settled = len(self.results) + sum(
                        (_PROCESSING_TASKS.get(tid) or {}).get("progress", 0) / 100 for tid in self.running)
                    self.entry["progress"] = int(100 * settled / self.count)
                    return
        finally:
            self._advancing = False

    def _settle(self) -> int:
        """Record every running entry whose task is over; returns how many."""
        from .processing_tools import _PROCESSING_TASKS, _sync_with_qgis

        settled = 0
        for task_id in list(self.running):
            _sync_with_qgis(task_id)
            child = _PROCESSING_TASKS.get(task_id) or {}
            if child.get("status") == "running" or task_id not in self.running:
                continue
            index, started, _writes, _reads = self.running.pop(task_id)
            child.update(_consumed=True, _consumed_at=time.time())
            self.results.append(self._line(index, started, child))
            settled += 1
        return settled

    def _fill(self) -> int:
        """Start waiting entries in list order while fewer than ``width`` run; returns how many left the queue."""




        writes_busy, reads_busy = set(), set()
        for _index, _started, writes, reads in self.running.values():
            writes_busy |= writes
            reads_busy |= reads
        left = position = 0
        while position < len(self.pending) and len(self.running) < self.width and self.entry["status"] == "running":
            index, parameters = self.pending[position]
            writes, reads = self._files(parameters)
            if writes & (writes_busy | reads_busy) or reads & writes_busy:
                writes_busy |= writes
                reads_busy |= reads
                position += 1
                continue
            del self.pending[position]
            left += 1
            if self._start(index, parameters, writes, reads):
                writes_busy |= writes
                reads_busy |= reads
        return left

    def _files(self, parameters) -> tuple[set, set]:
        """(files the entry writes, files it reads), each a normalised path, a GeoPackage table as its file."""
        from .processing_tools import _find_layer, _gpkg_table_target, _output_names

        writes: set = set()
        reads: set = set()
        if not isinstance(parameters, dict):
            return writes, reads
        named, _renamed, _refusal = _output_names(self.alg, parameters, self.algorithm_id)
        for key, value in named.items():
            for item in (value if isinstance(value, (list, tuple)) else [value]):
                if not isinstance(item, str) or not item.strip() or item == "TEMPORARY_OUTPUT" \
                        or item.startswith("memory:"):
                    continue
                target = item
                if key not in self.outputs:


                    is_path = "/" in item or "\\" in item or "|" in item or item.startswith("~")
                    layer = None if is_path else _find_layer(item)
                    if layer is not None:
                        target = layer.source() or ""
                    elif not is_path:
                        continue
                table = _gpkg_table_target(target)
                path = (table[0] if table else target.split("|", 1)[0]).strip()
                if path:
                    (writes if key in self.outputs else reads).add(
                        os.path.normcase(os.path.abspath(os.path.expanduser(path))))
        return writes, reads

    def cancel(self) -> None:
        """Stop: every running entry is cancelled, and the entries not started never start."""
        from .processing_tools import _cancel_task

        running, self.running = self.running, {}
        for task_id, (index, started, _writes, _reads) in running.items():
            _cancel_task({"task_id": task_id})
            self.results.append({"index": index, "seconds": round(time.monotonic() - started, 2),
                                 "status": "canceled", "message": "Stopped while running; nothing was added."})
        pending, self.pending = self.pending, []
        self.results.extend({"index": index, "status": "skipped", "message": "Stopped before this run started."}
                            for index, _parameters in pending)

    def _start(self, index: int, parameters, writes=frozenset(), reads=frozenset()) -> bool:
        """Start one entry; True when its task runs, False when the entry was settled at once."""
        from .processing_tools import _PROCESSING_TASKS, _cancel_task

        if not isinstance(parameters, dict):
            self.results.append({"index": index, "status": "error",
                                 "message": "each parameters_list entry must be an object"})
            return False
        if self.deadline is not None and time.monotonic() >= self.deadline:
            self.timed_out = True
            self.results.append({"index": index, "status": "skipped",
                                 "message": f"Batch budget of {self.budget:g}s exhausted before this run started"})
            return False
        started = time.monotonic()
        try:
            outcome = _run_processing({"algorithm_id": self.algorithm_id, "parameters": parameters})
        except Exception as exc:  # noqa: BLE001 - it also runs from a task's signal, where nothing may raise
            log_warning(f"execute_processing_batch: run {index} raised {exc}")
            outcome = {"_error": f"{exc.__class__.__name__}: {exc}"}
        if not isinstance(outcome, dict):
            outcome = {"_error": "The run returned no result."}
        child = _PROCESSING_TASKS.get(outcome.get("task_id") or "")
        if child is None:
            self.results.append(self._line(index, started, outcome))
            return False
        child["run_token"] = self.run_token
        task_id = outcome["task_id"]
        if self.entry["status"] != "running":

            _cancel_task({"task_id": task_id})
            self.results.append({"index": index, "seconds": round(time.monotonic() - started, 2),
                                 "status": "canceled", "message": "Stopped while running; nothing was added."})
            return False
        self.running[task_id] = (index, started, frozenset(writes), frozenset(reads))
        task = child.get("task")
        for name in ("executed", "taskTerminated"):
            signal = getattr(task, name, None)
            if signal is not None:
                signal.connect(lambda *_args, tid=task_id: self._ended(tid))
        return True

    def _ended(self, task_id: str) -> None:
        """A task signal: the entry that task ran is over, unless the batch already settled it."""
        if task_id not in self.running:
            return
        try:
            self.advance()
        except Exception as exc:  # noqa: BLE001 - a Qt slot must not raise
            log_warning(f"execute_processing_batch: could not go on after {task_id}: {exc}")

    @staticmethod
    def _line(index: int, started: float, outcome: dict) -> dict:
        """One run's line, in the shape the loop in _execute_processing_batch writes."""
        line = {"index": index, "seconds": round(time.monotonic() - started, 2)}
        status = str(outcome.get("status") or "")
        if outcome.get("_error") or status == "error":
            line.update(status="error",
                        message=str(outcome.get("_error") or outcome.get("error") or "The run failed."))
        elif status == "canceled":
            line.update(status="canceled", message="Cancelled before it finished; nothing was added.")
        else:
            line.update(status="success", outputs=outcome.get("outputs"))
            if outcome.get("outputs_note"):
                line["outputs_note"] = outcome["outputs_note"]
        return line


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





    from .processing_tools import _destination_names, _output_names

    parameters, renamed, misnamed = _output_names(alg, parameters, model)
    if misnamed:
        return misnamed

    def _named(outcome):
        if isinstance(outcome, dict) and not outcome.get("_error"):
            outcome["model"] = model
            if renamed:
                outcome["repairs"] = renamed + list(outcome.get("repairs") or [])
        return outcome



    for param in alg.parameterDefinitions():
        if isinstance(param, QgsProcessingDestinationParameter):
            parameters.setdefault(param.name(), "TEMPORARY_OUTPUT")

    if file_alg is None:
        return _named(_run_processing({"algorithm_id": model, "parameters": parameters}))






    from ..core.security import validate_path

    outputs = _destination_names(alg)
    for key, value in list(parameters.items()):
        if not isinstance(value, str) or value in ("TEMPORARY_OUTPUT", "memory:"):
            continue
        upper = key.upper()
        if key not in outputs and "OUTPUT" not in upper and "DEST" not in upper:
            continue
        if not (os.path.isabs(value) or value.startswith(("~", ".")) or "/" in value or "\\" in value):
            continue
        problem = validate_path(value.split("|", 1)[0], write=True)
        if problem:
            return tool_error(problem, "PERMISSION_DENIED",
                              "Write the model's output under the project folder, the user's home folder "
                              "or the temp folder, or pass TEMPORARY_OUTPUT.")








    from .processing_tools import _heavy_inputs, _threadable

    if _threadable(file_alg):
        outcome = _named(_run_processing({"algorithm_id": model, "parameters": parameters}, algorithm=file_alg))
        if isinstance(outcome, dict) and isinstance(outcome.get("poll"), dict):
            outcome["poll"]["label"] = f"Running {os.path.basename(model)}"
        return outcome

    if _heavy_inputs(parameters):
        return tool_error(
            "A step of this model cannot run in the background, and the inputs are too large to run it on "
            "the main thread; nothing was run.",
            "INVALID_ARGS",
            "Clip the inputs first.")

    feedback = QgsProcessingFeedback()
    try:
        result = processing.run(file_alg, parameters, feedback=feedback)
    except Exception as e:
        return tool_error(f"Model run failed: {e}", "EXECUTION_FAILED",
                          "Check the parameter names against the model's inputs "
                          "(get_algorithm_help on a registered model).")
    return _named({"model": model, "outputs": _process_outputs(result)})
