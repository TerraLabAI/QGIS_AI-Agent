# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

import itertools
import os
import re
import time
import uuid

import processing
from qgis.core import (
    Qgis,
    QgsApplication,
    QgsExpression,
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
from qgis.PyQt.QtCore import QPointF, QSizeF
from qgis.PyQt.QtGui import QColor

from ..core import layer_order, limits
from ..core.logger import log, log_warning
from ..core.security import expand_path
from ..core.tool_registry import Tool, ToolRegistry, coded_fact, tool_error
from ._compat import enum_value
from .data_tools import _avoid_reserved_name
from .processing_run import (
    _process_outputs,
    _run_processing,
    offered_inputs,
    prepare_remote_rasters,
    take_prepared_inputs,
)






_BATCH_TIMEOUT = limits.CALL_MAX_SECONDS_MAIN
_INPUT_TYPES = ["vector", "feature_source", "raster", "field", "number", "integer", "distance", "string",
                "boolean", "extent", "crs", "point", "file", "folder", "enum", "multiple_layers"]


def _batch_plan(args: dict) -> tuple[str, list]:
    return str(args.get("algorithm") or ""), args.get("parameters_list") or []


def _model_plan(args: dict) -> tuple[str, list]:
    return str(args.get("model") or ""), [args.get("parameters")]


def _prepare_batch(args: dict):

    try:
        alg = QgsApplication.processingRegistry().algorithmById(_resolve_algorithm_id(args["algorithm"]))
    except (_SpecError, KeyError):
        return None
    return prepare_remote_rasters(alg, list(args.get("parameters_list") or []))


def _prepare_model(args: dict):

    model = str(args.get("model") or "").strip()
    alg = _model_from_file(model)[0] if model.lower().endswith(".model3") else _registered_model(model)[0]
    return prepare_remote_rasters(alg, [args.get("parameters")])


def register_harvest_models_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="get_processing_providers",
        danger="read",
        input_schema={"type": "object", "properties": {"focus": {
            "type": "string", "enum": ["all", "supervised_classification", "kriging",
                                           "land_use_change_prediction"],
        }}, "required": []},
        handler=_get_processing_providers,
    ))

    registry.register(Tool(
        name="execute_processing_batch",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {
                "algorithm": {"type": "string"},
                "parameters_list": {"type": "array", "items": {"type": "object"},
                                    "minItems": 1, "maxItems": 100},
                "timeout": {"type": "number", "minimum": 0.1, "maximum": _BATCH_TIMEOUT},


                "output_name": {"type": "string"},
            },
            "required": ["algorithm", "parameters_list"],
        },
        handler=_execute_processing_batch,

        prepare=_prepare_batch,
        processing=_batch_plan,
    ))

    registry.register(Tool(
        name="create_processing_model",
        danger="destructive",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "object"}, "minItems": 1, "maxItems": 100},
                "inputs": {"type": "array", "items": {"type": "object"}, "maxItems": 100},
                "outputs": {"type": "array", "items": {"type": "object"}, "maxItems": 100},


                "description": {"type": "string"},
                "group": {"type": "string"},
                "open_in_designer": {"type": "boolean"},
            },
            "required": ["name", "steps"],
        },
        handler=_create_processing_model,
    ))

    registry.register(Tool(
        name="list_processing_models",
        danger="read",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_list_processing_models,
    ))

    registry.register(Tool(
        name="run_model",
        danger="write",
        card="model",
        input_schema={
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "parameters": {"type": "object"},
            },
            "required": ["model"],
        },
        handler=_run_model,
        prepare=_prepare_model,
        processing=_model_plan,
    ))






class _SpecError(ValueError):



    def __init__(self, message: str, hint: str = "", **facts):
        super().__init__(message)
        self.hint = hint
        self.facts = facts


def _coded_under(key: str, fact: dict) -> dict:

    rest = dict(fact)
    return {f"{key}_hint": rest.pop("hint"), **rest}


def _hint_rank(algorithm, key: str):

    title = algorithm.displayName().lower()
    bare_id = algorithm.id().split(":", 1)[-1].lower()
    if title == key:
        return 0
    if bare_id == key:
        return 1
    if key in title or key in bare_id:
        return 2
    return None


def _only_choice(matches: list):

    if len(matches) == 1:
        return matches[0]
    native = [algorithm for algorithm in matches if algorithm.provider().id() == "native"]
    return native[0] if len(native) == 1 else None


def _resolve_algorithm_id(hint: str) -> str:






    registry = QgsApplication.processingRegistry()
    if not isinstance(hint, str) or not hint.strip():
        raise _SpecError("Algorithm hint must be a non-empty string")
    wanted = hint.strip()
    registered = registry.algorithmById(wanted)
    if registered is not None:
        return registered.id()

    ranked: tuple = ([], [], [])
    key = wanted.lower()
    for algorithm in registry.algorithms():
        rank = _hint_rank(algorithm, key)
        if rank is not None:
            ranked[rank].append(algorithm)
    for matches in ranked:
        chosen = _only_choice(matches)
        if chosen is not None:
            return chosen.id()

    everything = list(itertools.chain.from_iterable(ranked))
    if not everything:
        raise _SpecError(f"No Processing algorithm matches '{wanted}'.", "algorithm_no_match", wanted=wanted)
    shortlist = sorted(everything, key=lambda a: (a.provider().id() != "native", len(a.id())))[:8]
    listed = ", ".join(f"{a.id()} ({a.displayName()})" for a in shortlist)
    raise _SpecError(f"Algorithm hint '{wanted}' is ambiguous. Candidates: {listed}.")


def _one_source(value, input_names: set, earlier_steps: set):

    kinds = QgsProcessingModelChildParameterSource
    marker = value[:1] if isinstance(value, str) else ""
    body = value[1:] if marker else None
    if marker == "@":
        if body not in input_names:
            raise _SpecError(f"Parameter reference '{value}' points to undefined model input '{body}'")
        return kinds.fromModelParameter(body)
    if marker == "$":
        step_id, dot, output = body.partition(".")
        if not dot:
            raise _SpecError(f"Step output reference '{value}' must be in '$step_id.OUTPUT_NAME' form")
        if step_id not in earlier_steps:
            raise _SpecError(f"Step output reference '{value}' points to undefined step '{step_id}'")
        return kinds.fromChildOutput(step_id, output)
    if marker == "=":
        return kinds.fromExpression(body)
    return kinds.fromStaticValue(value)


def _param_sources(value, input_names: set, earlier_steps: set) -> list:





    if not isinstance(value, list):
        return [_one_source(value, input_names, earlier_steps)]
    if any(isinstance(item, list) for item in value):
        raise _SpecError("Nested parameter source lists are not supported; pass a flat list of sources")
    return [_one_source(item, input_names, earlier_steps) for item in value]



_TYPE_SPELLINGS = {
    "vector": ("vector_layer",), "feature_source": ("source",), "raster": ("raster_layer",), "integer": ("int",),
    "number": ("float", "double"), "boolean": ("bool",), "multiple_layers": ("layers",),
}
_CANONICAL_TYPE = {spelling: kind for kind, spellings in _TYPE_SPELLINGS.items() for spelling in spellings}

_PLAIN_PARAMETERS = {
    "vector": QgsProcessingParameterVectorLayer, "feature_source": QgsProcessingParameterFeatureSource,
    "raster": QgsProcessingParameterRasterLayer, "string": QgsProcessingParameterString,
    "extent": QgsProcessingParameterExtent, "point": QgsProcessingParameterPoint,
    "multiple_layers": QgsProcessingParameterMultipleLayers,
}

_PROJECT_CRS_WORDS = {"project", "project crs", "projectcrs"}


def _canonical_type(raw):
    return _CANONICAL_TYPE.get(raw, raw)


def _new_parameter(kind: str, name, label, default, spec: dict):

    if kind in _PLAIN_PARAMETERS:
        return _PLAIN_PARAMETERS[kind](name, label, defaultValue=default)
    if kind in ("number", "integer"):
        number = QgsProcessingParameterNumber(name, label, defaultValue=default)
        whole = None
        if kind == "integer":
            whole = (enum_value((QgsProcessingParameterNumber, "Type.Integer"),
                                (QgsProcessingParameterNumber, "Integer"))
                     or enum_value((Qgis, "ProcessingNumberParameterType.Integer")))
        if whole is not None:
            number.setDataType(whole)
        return number
    if kind in ("file", "folder"):
        path = QgsProcessingParameterFile(name, label, defaultValue=default)
        folder = None
        if kind == "folder":
            folder = (enum_value((Qgis, "ProcessingFileParameterBehavior.Folder"))
                      or enum_value((QgsProcessingParameterFile, "Behavior.Folder"),
                                    (QgsProcessingParameterFile, "Folder")))
        if folder is not None:
            path.setBehavior(folder)
        return path
    if kind == "field":
        if not spec.get("parent_layer"):
            raise _SpecError(f"Input '{name}' of type 'field' requires 'parent_layer'")
        return QgsProcessingParameterField(name, label, parentLayerParameterName=spec.get("parent_layer"),
                                           defaultValue=default)
    if kind == "distance":
        distance = QgsProcessingParameterDistance(name, label, defaultValue=default)
        if spec.get("parent_layer"):
            distance.setParentParameterName(spec["parent_layer"])
        return distance
    if kind == "boolean":
        return QgsProcessingParameterBoolean(name, label, defaultValue=False if default is None else bool(default))
    if kind == "crs":
        if isinstance(default, str) and default.strip().lower() in _PROJECT_CRS_WORDS:
            default = "ProjectCrs"
        return QgsProcessingParameterCrs(name, label, defaultValue=default or "EPSG:4326")
    if kind == "enum":
        return QgsProcessingParameterEnum(name, label, options=spec.get("options") or [], defaultValue=default)
    return None




_SPEC_KEYS = {
    "input": ("name", "type", "description", "default", "optional", "parent_layer", "options", "help", "min", "max"),
    "step": ("id", "algorithm", "description", "parameters", "section"),
    "output": ("name", "from_step", "from_output", "description"),
}


def _refuse_unknown_keys(kind: str, label: str, spec: dict) -> None:
    unknown = sorted(str(key) for key in spec if key not in _SPEC_KEYS[kind])
    if unknown:
        raise _SpecError(f"{kind.capitalize()} {label}: unknown key(s) {unknown}. "
                         f"Valid keys: {list(_SPEC_KEYS[kind])}")


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _input_parameter(spec: dict):

    name = spec["name"]
    raw = str(spec.get("type") or "string").lower()
    parameter = _new_parameter(_canonical_type(raw), name, spec.get("description", name), spec.get("default"), spec)
    if parameter is None:
        raise _SpecError(f"Unsupported input type '{raw}' for input '{name}'. Types: {', '.join(_INPUT_TYPES)}")
    if isinstance(parameter, QgsProcessingParameterNumber):
        if _is_number(spec.get("min")):
            parameter.setMinimum(spec["min"])
        if _is_number(spec.get("max")):
            parameter.setMaximum(spec["max"])
    if spec.get("optional"):
        optional = (enum_value((Qgis, "ProcessingParameterFlag.Optional"))
                    or enum_value((QgsProcessingParameterDefinition, "FlagOptional")))
        if optional is not None:
            parameter.setFlags(parameter.flags() | optional)
    return parameter


def _models_folder():

    provider = QgsApplication.processingRegistry().providerById("model")
    folder = None
    try:
        if provider is not None and hasattr(provider, "modelsFolder"):
            folder = provider.modelsFolder()
    except Exception:  # nosec B110
        folder = None
    return folder or os.path.join(QgsApplication.qgisSettingsDirPath(), "processing", "models"), provider


_UNSAFE_IN_A_FILENAME = re.compile(r"[^A-Za-z0-9 _.-]")


def safe_model_filename(name: str) -> str:







    cleaned = _UNSAFE_IN_A_FILENAME.sub("_", str(name or "")).strip(" .")
    while ".." in cleaned:
        cleaned = cleaned.replace("..", ".")
    return _avoid_reserved_name(cleaned.strip(" .") or "model")


_MAX_NAME_SUFFIX = 1000


def _unique_model_path(folder: str, name: str):

    base = safe_model_filename(name)
    names = itertools.chain([base], (f"{base}_{n}" for n in range(2, _MAX_NAME_SUFFIX + 1)))
    for candidate in names:
        path = os.path.join(folder, candidate + ".model3")
        if not os.path.exists(path):
            return candidate, path
    raise _SpecError(f"Could not find a unique name for '{base}' in {folder} (tried up to _{_MAX_NAME_SUFFIX})")


_LAYER_INPUT_KINDS = {"vector", "feature_source", "raster", "multiple_layers"}


class _ModelBuilder:







    def __init__(self, name: str, description: str, group: str):
        self.registry = QgsApplication.processingRegistry()
        self.model = QgsProcessingModelAlgorithm()
        self.model.setName(name)
        if group:
            self.model.setGroup(group)
        self.description = description
        self.input_names: set = set()
        self.referenced_inputs: set = set()
        self.algorithm_of: dict = {}

    def outputs_of(self, algorithm_id: str) -> set:
        return {output.name() for output in self.registry.algorithmById(algorithm_id).outputDefinitions()}



    def set_help(self, inputs: list) -> None:

        lines = {"ALG_DESC": self.description} if self.description else {}
        for spec in inputs:
            if isinstance(spec, dict) and isinstance(spec.get("name"), str) \
                    and isinstance(spec.get("help"), str) and spec["help"].strip():
                lines[spec["name"]] = spec["help"]
        if not lines:
            return
        try:
            self.model.setHelpContent(lines)
        except Exception:  # nosec B110
            pass

    @staticmethod
    def check_inputs(inputs: list) -> None:
        by_name: dict = {}
        for position, spec in enumerate(inputs):
            if not isinstance(spec, dict) or not isinstance(spec.get("name"), str) or not spec["name"].strip():
                raise _SpecError(f"Input #{position} requires a non-empty string name")
            if spec["name"] in by_name:
                raise _SpecError(f"Duplicate input name '{spec['name']}'")
            _refuse_unknown_keys("input", f"'{spec['name']}'", spec)
            by_name[spec["name"]] = spec
        for spec in inputs:
            parent = spec.get("parent_layer")
            if not parent:
                continue
            label = f"Input '{spec['name']}'"
            if not isinstance(parent, str):
                raise _SpecError(f"{label}: parent_layer must be a model input name")
            if parent not in by_name:
                raise _SpecError(f"{label}: parent_layer '{parent}' is not a model input")
            parent_kind = _canonical_type(by_name[parent].get("type"))
            if parent_kind not in _LAYER_INPUT_KINDS:
                raise _SpecError(f"{label}: parent_layer '{parent}' is not a layer input")
            if spec.get("type") == "field" and parent_kind not in {"vector", "feature_source"}:
                raise _SpecError(f"Field input '{spec['name']}' requires a vector parent_layer")

    def add_inputs(self, inputs: list) -> None:
        for column, spec in enumerate(inputs):
            placed = QgsProcessingModelParameter(spec["name"])
            placed.setPosition(QPointF(_LAYOUT_X0 + column * _LAYOUT_DX, _LAYOUT_Y0))
            self.model.addModelParameter(_input_parameter(spec), placed)
            self.input_names.add(spec["name"])



    def resolve_steps(self, steps: list) -> list:

        resolved = []
        for position, step in enumerate(steps):
            if not isinstance(step, dict):
                raise _SpecError(f"Step #{position} must be a dict")
            _refuse_unknown_keys("step", f"#{position}", step)
            absent = next((key for key in ("id", "algorithm") if key not in step), None)
            if absent:
                raise _SpecError(f"Step #{position} missing required key '{absent}'")
            step_id = step["id"]
            if not isinstance(step_id, str) or not step_id.strip():
                raise _SpecError(f"Step #{position} requires a non-empty string id")
            if step.get("parameters") is not None and not isinstance(step["parameters"], dict):
                raise _SpecError(f"Step '{step_id}': parameters must be an object")
            if step_id in self.algorithm_of:
                raise _SpecError(f"Duplicate step id '{step_id}'")
            try:
                algorithm_id = _resolve_algorithm_id(step["algorithm"])
            except _SpecError as e:
                raise _SpecError(f"Step '{step_id}': {e}", e.hint, **e.facts) from e
            self.algorithm_of[step_id] = algorithm_id
            accepted = {p.name() for p in self.registry.algorithmById(algorithm_id).parameterDefinitions()}
            stray = next((key for key in step.get("parameters") or {} if key not in accepted), None)
            if stray is not None:
                raise _SpecError(f"Step '{step_id}' (algorithm '{algorithm_id}'): unknown parameter "
                                 f"'{stray}'. Valid parameters: {sorted(accepted)}")
            resolved.append((step, algorithm_id))
        return resolved

    def group_outputs(self, outputs: list) -> dict:

        grouped: dict = {}
        taken: set = set()
        for position, out in enumerate(outputs):
            if not isinstance(out, dict):
                raise _SpecError(f"Output #{position} must be a dict")
            _refuse_unknown_keys("output", f"#{position}", out)
            for key in ("name", "from_step", "from_output"):
                if not isinstance(out.get(key), str) or not out[key].strip():
                    raise _SpecError(f"Output #{position} requires a non-empty string '{key}'")
            name, step_id = out["name"], out["from_step"]
            if name in taken:
                raise _SpecError(f"Duplicate output name '{name}'")
            taken.add(name)
            if step_id not in self.algorithm_of:
                raise _SpecError(f"Output '{name}': from_step '{step_id}' is not a defined step")
            offered = self.outputs_of(self.algorithm_of[step_id])
            if out["from_output"] not in offered:
                raise _SpecError(f"Output '{name}': '{out['from_output']}' is not an output of step "
                                 f"'{step_id}' (algorithm '{self.algorithm_of[step_id]}'). "
                                 f"Valid outputs: {sorted(offered)}")
            grouped.setdefault(step_id, {})[name] = out
        return grouped



    def note_source(self, value, step_id: str, parameter: str) -> None:

        if isinstance(value, list):
            for item in value:
                self.note_source(item, step_id, parameter)
            return
        if not isinstance(value, str):
            return
        where = f"Step '{step_id}' parameter '{parameter}'"
        if value.startswith("@"):
            self.referenced_inputs.add(value[1:])
        elif value.startswith("="):
            expression = QgsExpression(value[1:])
            if expression.hasParserError():
                raise _SpecError(f"{where}: invalid source expression: {expression.parserErrorString()}")
            self.referenced_inputs.update(expression.referencedVariables() & self.input_names)
        elif value.startswith("$") and "." in value:
            source_step, _dot, output = value[1:].partition(".")
            if source_step in self.algorithm_of:
                offered = self.outputs_of(self.algorithm_of[source_step])
                if output not in offered:
                    raise _SpecError(f"{where}: '{value}' names an unknown child output. "
                                     f"Valid outputs: {sorted(offered)}")

    @staticmethod
    def output_box(name: str, step_id: str, child_output: str, description: str, at: QPointF):
        box = QgsProcessingModelOutput(name)
        box.setChildId(step_id)
        box.setChildOutputName(child_output)
        box.setDescription(description)
        box.setPosition(at)
        return box

    def add_steps(self, resolved: list, grouped: dict, positions: dict) -> list:

        added: list = []
        for step, algorithm_id in resolved:
            step_id = step["id"]
            child = QgsProcessingModelChildAlgorithm(algorithm_id)
            child.setChildId(step_id)
            child.setDescription(step.get("description") or self.registry.algorithmById(algorithm_id).displayName())
            child.setPosition(positions[step_id])
            for parameter, value in (step.get("parameters") or {}).items():
                self.note_source(value, step_id, parameter)
                child.addParameterSources(parameter, _param_sources(value, self.input_names, set(added)))
            boxes = {}
            for row, (name, out) in enumerate(grouped.get(step_id, {}).items(), start=1):
                at = positions[step_id] + QPointF(_OUTPUT_DX, _OUTPUT_DY * row)
                boxes[name] = self.output_box(name, step_id, out["from_output"], out.get("description") or name, at)
            if boxes:
                child.setModelOutputs(boxes)
            self.model.addChildAlgorithm(child)
            added.append(step_id)
        return added

    def expose_last_step(self, step_id: str, positions: dict) -> None:

        algorithm = self.registry.algorithmById(self.algorithm_of[step_id])
        offered = [o.name() for o in algorithm.outputDefinitions()] if algorithm else []
        chosen = "OUTPUT" if "OUTPUT" in offered else next(iter(offered), None)
        if not chosen:
            return
        at = positions[step_id] + QPointF(_OUTPUT_DX, _OUTPUT_DY)
        self.model.childAlgorithm(step_id).setModelOutputs(
            {"Result": self.output_box("Result", step_id, chosen, "Result", at)})


def _build_model(name: str, steps: list, inputs: list, outputs: list, description: str, group: str):

    builder = _ModelBuilder(name, description, group)
    builder.set_help(inputs)
    builder.check_inputs(inputs)
    builder.add_inputs(inputs)
    resolved = builder.resolve_steps(steps)
    grouped = builder.group_outputs(outputs)
    ordered_steps = [step for step, _algorithm in resolved]
    positions = _step_positions(ordered_steps)
    added = builder.add_steps(resolved, grouped, positions)

    unused = sorted(builder.input_names - builder.referenced_inputs)
    if unused:
        raise _SpecError(f"Unused model inputs: {', '.join(unused)}.", "model_inputs_unused", inputs=unused)
    if not outputs and added:
        builder.expose_last_step(added[-1], positions)

    model = builder.model
    _add_section_boxes(model, ordered_steps, positions)
    if hasattr(model, "setParameterOrder"):
        model.setParameterOrder([spec["name"] for spec in inputs])
    valid, issues = model.validate()
    if not valid:
        raise _SpecError("Model validation failed: " + "; ".join(str(issue) for issue in issues))
    output_count = sum(len(model.childAlgorithm(step_id).modelOutputs()) for step_id in added)
    return model, resolved, len(builder.input_names), output_count






def _get_processing_providers(args: dict) -> dict:
    focus = str(args.get("focus") or "all")
    if focus != "all":
        if focus == "land_use_change_prediction":
            return _land_use_change_audit()
        return _processing_capability_audit(focus)
    rows = sorted((_provider_row(provider) for provider in QgsApplication.processingRegistry().providers()),
                  key=lambda row: row["id"])
    return {"providers": rows, "count": len(rows)}


def _provider_row(provider) -> dict:
    row = {"id": provider.id(), "name": provider.name(), "algorithm_count": len(provider.algorithms())}
    try:
        row["active"] = bool(provider.isActive())
    except Exception:  # nosec B110
        pass
    return row


def _land_use_change_audit() -> dict:

    registry = QgsApplication.processingRegistry()
    algorithms = list(registry.algorithms())
    terms = ("cellular automata", "cellular-automata", "transition potential",
             "land use change", "land-use change", "land cover change", "molusce",
             "markov", "neural network", "artificial neural")
    models = []
    for algorithm in algorithms:
        label = " ".join((str(algorithm.id() or ""), str(algorithm.displayName() or ""),
                           str(algorithm.shortDescription() or ""))).casefold()
        if any(term in label for term in terms):
            models.append({"id": str(algorithm.id()), "name": str(algorithm.displayName() or algorithm.id()),
                           "provider": str(algorithm.id()).split(":", 1)[0]})
    kappa = [aid for aid in ("grass:r.kappa",) if registry.algorithmById(aid) is not None]
    return {
        "focus": "land_use_change_prediction",
        "status": "partial" if kappa and not models else ("available" if models else "unavailable"),
        "algorithms": models,
        "validation_algorithms": kappa,
        "diagnosis": (
            "A registered land-use-change model is available; its parameters are listed with it."
            if models
            else "No registered Processing algorithm exposes cellular automata, ANN/neural transition "
                 "modeling, MOLUSCE, or a land-use-change predictor."
            + (" GRASS r.kappa is available for validation only." if kappa else "")
        ),
        "safe_composition": {
            "status": "validation_only" if kappa and not models else ("available" if models else "unavailable"),
            "algorithms": kappa,
            "steps": [
                "the two classified rasters need alignment and matching class codes.",
                "This installation has no change model.",
                "r.kappa validates an observed classification or prediction; it does not train or "
                "predict transitions.",
            ],
        },
    }


_CLASSIFIER_NAMES = frozenset({"i.gensig", "i.maxlik", "i.smap", "i.gensigset"})


def _processing_capability_audit(focus: str) -> dict:

    registry = QgsApplication.processingRegistry()
    algorithms = list(registry.algorithms())
    rows = []
    for algorithm in algorithms:
        aid = str(algorithm.id() or "")
        label = " ".join((aid, str(algorithm.displayName() or ""),
                          str(algorithm.shortDescription() or ""))).casefold()
        if focus == "supervised_classification":
            terms = ("supervised", "maximum likelihood", "minimum distance",
                     "training samples", "training polygons")
            matches = any(term in label for term in terms if term != "supervised")
            matches = matches or bool(re.search(r"(?<!un)supervised", label))



            matches = matches or aid.split(":", 1)[-1] in _CLASSIFIER_NAMES or aid.startswith("dzetsaka:")
        else:
            terms = ("kriging", "variogram", "ordinary kriging", "universal kriging")
            matches = any(term in label for term in terms)
        if not matches:
            continue
        params = []
        for param in algorithm.parameterDefinitions():
            item = {"name": str(param.name()), "type": str(param.type()),
                    "description": str(param.description() or "")}
            if getattr(param, "isDestination", lambda: False)():
                item["destination"] = True
            try:
                options = list(param.options())
            except (AttributeError, TypeError, RuntimeError):
                options = []
            if options:
                item["options"] = [str(option) for option in options]
            params.append(item)
        rows.append({"id": aid, "name": str(algorithm.displayName() or aid),
                     "provider": aid.split(":", 1)[0], "parameters": params})

    if focus == "supervised_classification":
        available = {str(a.id()) for a in algorithms}
        recipe_ids = [
            aid
            for aid in (
                "gdal:rasterize",
                "gdal:rastercalculator",
                "native:reclassifybytable",
                "native:reclassifybylayer",
                "native:zonalstatisticsfb",
            )
            if aid in available
        ]
        return {
            "focus": focus,
            "status": "available" if rows else "unavailable",
            "algorithms": rows,
            "diagnosis": (
                "A registered provider exposes a supervised classifier; its parameters are listed with it."
                if rows
                else "No registered Processing algorithm exposes supervised classification, ROI training, "
                     "or a spectral classifier."
            ),
            "safe_composition": {
                "status": "partial" if recipe_ids else "unavailable",
                "algorithms": recipe_ids,
                "steps": [
                    "a multiband raster and ROI polygons with an explicit class field are needed.",
                    "a classifier exists only when this audit returns one.",
                    "Otherwise the listed rasterize/reclassify/zonal algorithms serve validation or "
                    "post-processing, not supervised classification.",
                ],
                "required_inputs": ["raster(s)", "ROI vector layer", "class field", "output raster"],
            },
        }

    return {
        "focus": focus,
        "status": "available" if rows else "unavailable",
        "algorithms": rows,
        "diagnosis": (
            "A registered provider exposes kriging; its returned parameter schema includes "
            "variogram and diagnostics when present."
            if rows
            else "No registered Processing provider exposes kriging. IDW remains available through "
                 "qgis:idwinterpolation; no scikit-learn or plugin was installed."
        ),
        "safe_composition": {
            "status": "use_idw_fallback" if not rows else "not_needed",
            "algorithms": ["qgis:idwinterpolation"]
            if not rows and any(str(a.id()) == "qgis:idwinterpolation" for a in algorithms)
            else [],
            "steps": [
                "Needs a point value field and projected CRS.",
                "Kriging here is the audited provider's, with its diagnostics.",
                "Without it, the surface is IDW, not kriging.",
            ],
        },
    }


def _main_thread_entry(algorithm_id: str, index: int, parameters, layer_name, ends_at: float, budget: float,
                       prepared=None) -> dict:

    if not isinstance(parameters, dict):
        return {"index": index, "status": "error", "message": "each parameters_list entry must be an object"}
    if time.monotonic() >= ends_at:
        return {"index": index, "status": "skipped",
                "message": f"Batch budget of {budget:g}s exhausted before this run started"}
    began = time.monotonic()
    with offered_inputs(prepared or {}):
        outcome = _run_processing({"algorithm_id": algorithm_id, "parameters": parameters, "output_name": layer_name})
    line = {"index": index, "seconds": round(time.monotonic() - began, 2)}
    failure = outcome.get("_error") if isinstance(outcome, dict) else None
    background = outcome.get("task_id") if isinstance(outcome, dict) else None
    if failure:
        line.update(status="error", message=str(failure))
    elif background:



        line.update(status="running", task_id=background,
                    message="Started in the background.")
    else:
        line.update(status="success", outputs=outcome.get("outputs"))
        if outcome.get("outputs_note"):
            line["outputs_note"] = outcome["outputs_note"]
    return line


def _execute_processing_batch(args: dict) -> dict:

    prepared = take_prepared_inputs()
    try:
        algorithm_id = _resolve_algorithm_id(args["algorithm"])
    except _SpecError as e:
        return tool_error(str(e), "INVALID_ARGS", hint=e.hint or "algorithm_hint_unresolved", **e.facts)
    parameters_list = args["parameters_list"]
    from .processing_decisions import _threadable

    alg = QgsApplication.processingRegistry().algorithmById(algorithm_id)
    names = _output_names_for(args.get("output_name"), algorithm_id, parameters_list)
    if _threadable(alg):
        return _BatchRun(alg, algorithm_id, parameters_list, args.get("timeout"), names, prepared).start()


    ceiling = limits.current("CALL_MAX_SECONDS_MAIN")
    budget = min(float(args.get("timeout") or ceiling), ceiling)
    ends_at = time.monotonic() + budget
    results = [_main_thread_entry(algorithm_id, index, parameters, names[index], ends_at, budget, prepared)
               for index, parameters in enumerate(parameters_list)]

    running = [r["task_id"] for r in results if r["status"] == "running"]
    response = {"algorithm": algorithm_id, "results": results, "count": len(results),
                "succeeded": sum(1 for r in results if r["status"] == "success")}
    if running:
        response["running"] = running
        response["poll"] = {"tool": "get_task_status", "args": {"task_id": running[0]},
                            "label": f"Running {algorithm_id}"}
        response["note"] = (f"{len(running)} run(s) were too heavy to run inline and went to the background; "
                            "they have produced nothing yet.")
        response.update(_coded_under("running", coded_fact(hint="batch_runs_backgrounded",
                                                           backgrounded=len(running))))
    if any(r["status"] == "skipped" for r in results):
        response["timed_out"] = True
        response.update(_coded_under("timed_out", coded_fact(hint="batch_timed_out")))
    return response






_REPROJECTIONS = frozenset({"native:reprojectlayer", "gdal:warpreproject"})
_DEFAULT_REPROJECT_NAME = "{input}_{epsg}"
_CRS_KEYS = ("TARGET_CRS", "CRS", "T_SRS", "OUTPUT_CRS")
_NAME_FIELD = re.compile(r"\{(input|epsg|n)\}")


def _input_name(parameters: dict) -> str:

    value = parameters.get("INPUT")
    if isinstance(value, dict):
        value = value.get("source")
    if not isinstance(value, str) or not value.strip():
        return "layer"
    from .layer_lookup import _find_layer

    is_path = "/" in value or "\\" in value or "|" in value
    layer = None if is_path else _find_layer(value)
    if layer is not None:
        return layer.name()
    path = value.split("|", 1)[0]
    table = re.search(r"layername=([^|]+)", value)
    return table.group(1) if table else (os.path.splitext(os.path.basename(path))[0] or "layer")


def _crs_code(parameters: dict) -> str:

    from qgis.core import QgsCoordinateReferenceSystem

    for key in _CRS_KEYS:
        value = parameters.get(key)
        if value in (None, ""):
            continue
        crs = value if isinstance(value, QgsCoordinateReferenceSystem) else QgsCoordinateReferenceSystem(str(value))
        authid = crs.authid() if crs.isValid() else str(value)
        authority, _, code = authid.partition(":")
        if authority.upper() == "EPSG" and code:
            return code
        return re.sub(r"[^0-9A-Za-z]+", "_", authid).strip("_") or "crs"
    return "crs"


def _names_its_file(output) -> bool:

    if not isinstance(output, str):
        return False
    text = output.strip()
    if not text or text == "TEMPORARY_OUTPUT" or text.startswith(("memory:", "ogr:")) or "layername=" in text:
        return False
    return bool(os.path.splitext(text)[1])


def _output_names_for(pattern, algorithm_id: str, parameters_list: list) -> list:






    count = len(parameters_list)
    text = str(pattern or "").strip()
    default = not text and algorithm_id in _REPROJECTIONS
    if default:
        text = _DEFAULT_REPROJECT_NAME
    if not text:
        return [None] * count
    names = []
    for index, parameters in enumerate(parameters_list):
        if not isinstance(parameters, dict) or (default and _names_its_file(parameters.get("OUTPUT"))):

            names.append(None)
            continue
        values = {"input": lambda p=parameters: _input_name(p), "epsg": lambda p=parameters: _crs_code(p),
                  "n": lambda i=index: str(i + 1)}
        name = _NAME_FIELD.sub(lambda m, fields=values: fields[m.group(1)](), text)
        if not _NAME_FIELD.search(text) and count > 1 and index:
            name = f"{name}_{index + 1}"
        names.append(name)
    return names


class _BatchRun:

















    def __init__(self, alg, algorithm_id: str, parameters_list: list, timeout=None, names=None, prepared=None):
        from .processing_destinations import _destination_names

        self.alg = alg

        self.prepared = prepared or {}

        self.names = list(names) if names else [None] * len(parameters_list)
        self.outputs = _destination_names(alg)
        self.algorithm_id = algorithm_id
        self.pending = list(enumerate(parameters_list))
        self.count = len(self.pending)
        self.results: list[dict] = []

        self.running: dict[str, tuple] = {}
        self.width = max(1, int(limits.current("PROCESSING_BATCH_PARALLEL")))
        self._advancing = False
        self._again = False
        self.stopped = False
        self.timed_out = False


        self.budget = float(timeout) if timeout else None
        self.deadline = time.monotonic() + self.budget if self.budget else None


        self.run_token = layer_order.current_run()
        self.task_id = "batch-" + uuid.uuid4().hex[:12]
        self.entry = {"status": "running", "progress": 0, "algorithm": algorithm_id,
                      "started_at": time.strftime("%H:%M:%S"), "sequence": self}

    def start(self) -> dict:
        from .processing_run import _POLL_INTERVAL_S, _PROCESSING_TASKS, _sweep_consumed_tasks

        _sweep_consumed_tasks()
        _PROCESSING_TASKS[self.task_id] = self.entry
        self.advance()
        if self.entry["status"] != "running":

            _PROCESSING_TASKS.pop(self.task_id, None)
            return self.report()
        return {**self.report(), "task_id": self.task_id, "status": "running",
                **coded_fact(hint="batch_started", width=self.width, task_id=self.task_id),
                "poll": {"tool": "get_task_status", "args": {"task_id": self.task_id},
                         "interval_s": _POLL_INTERVAL_S,
                         "label": f"Running {self.algorithm_id}, {self.count} runs"}}

    def report(self) -> dict:

        results = sorted(self.results, key=lambda r: r["index"])
        out = {"algorithm": self.algorithm_id, "results": results, "count": self.count,
               "succeeded": sum(1 for r in results if r["status"] == "success")}
        if self.timed_out:
            out["timed_out"] = True
            out.update(_coded_under("timed_out", coded_fact(hint="batch_timed_out")))
        return out

    def advance(self) -> None:

        from .processing_run import _PROCESSING_TASKS

        if self._advancing:


            self._again = True
            return
        self._advancing = True
        try:

            while self.entry["status"] == "running" and _PROCESSING_TASKS.get(self.task_id) is self.entry:
                self._again = False
                moved = self._settle() + self._fill()
                if not self.pending and not self.running:
                    self.entry.update(status="canceled" if self.stopped else "complete", progress=100)
                    self.prepared.clear()
                    return
                if not moved and not self._again:
                    settled = len(self.results) + sum(
                        (_PROCESSING_TASKS.get(tid) or {}).get("progress", 0) / 100 for tid in self.running)
                    self.entry["progress"] = int(100 * settled / self.count)
                    return
        finally:
            self._advancing = False

    def _settle(self) -> int:

        from .processing_run import _PROCESSING_TASKS, _sync_with_qgis

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

        from .layer_lookup import _find_layer
        from .processing_destinations import _gpkg_table_target, _output_names

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






        from .processing_run import _PROCESSING_TASKS, _cancel_task

        self.stopped = True
        running, self.running = self.running, {}
        for task_id, (index, started, writes, reads) in running.items():
            answer = _cancel_task({"task_id": task_id})
            child = _PROCESSING_TASKS.get(task_id) or {}
            seconds = round(time.monotonic() - started, 2)
            if answer.get("status") == "canceled":
                self.results.append({"index": index, "seconds": seconds, "status": "canceled",
                                     "message": "Stopped while running; nothing was added."})
            elif "_error" in answer:
                self.results.append({"index": index, "seconds": seconds, "status": "error",
                                     "message": str(answer["_error"])})
            elif child.get("status") == "running":
                self.running[task_id] = (index, started, writes, reads)
            else:
                child.update(_consumed=True, _consumed_at=time.time())
                self.results.append(self._line(index, started, child))
        self.prepared.clear()
        pending, self.pending = self.pending, []
        self.results.extend({"index": index, "status": "skipped", "message": "Stopped before this run started."}
                            for index, _parameters in pending)
        if self.running and _PROCESSING_TASKS.get(self.task_id) is self.entry:
            self.entry["status"] = "running"

    def _start(self, index: int, parameters, writes=frozenset(), reads=frozenset()) -> bool:

        from .processing_run import _PROCESSING_TASKS, _cancel_task

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
            with offered_inputs(self.prepared):
                outcome = _run_processing({"algorithm_id": self.algorithm_id, "parameters": parameters,
                                           "output_name": self.names[index] if index < len(self.names) else None})
        except Exception as exc:  # noqa: BLE001
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

        if task_id not in self.running:
            return
        try:
            self.advance()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"execute_processing_batch: could not go on after {task_id}: {exc}")

    @staticmethod
    def _line(index: int, started: float, outcome: dict) -> dict:

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






_LAYOUT_X0, _LAYOUT_Y0, _LAYOUT_DX, _LAYOUT_DY = 120.0, 60.0, 240.0, 150.0
_OUTPUT_DX, _OUTPUT_DY = 170.0, 45.0

_COLUMN_DX = 560.0
_STEP_REF = re.compile(r"^\$([^.]+)\.")


def _step_refs(value) -> set:

    if isinstance(value, str):
        match = _STEP_REF.match(value)
        return {match.group(1)} if match else set()
    if isinstance(value, dict):
        return set().union(*(_step_refs(v) for v in value.values())) if value else set()
    if isinstance(value, (list, tuple)):
        return set().union(*(_step_refs(v) for v in value)) if value else set()
    return set()


def _step_positions(steps: list) -> dict:
    depth: dict = {}
    for step in steps:
        parents = [depth[ref] for ref in _step_refs(step.get("parameters") or {}) if ref in depth]
        depth[step["id"]] = 1 + max(parents, default=0)
    column: dict = {}
    positions = {}
    for step in steps:
        level = depth[step["id"]]
        slot = column.get(level, 0)
        column[level] = slot + 1
        positions[step["id"]] = QPointF(_LAYOUT_X0 + slot * _COLUMN_DX, _LAYOUT_Y0 + level * _LAYOUT_DY)
    return positions


_SECTION_COLORS = ("#cfe8ff", "#d5f5d0", "#ffe9c2", "#f3d6f5", "#e2e2e2")
_BOX_W, _BOX_H, _BOX_PAD = 200.0, 30.0, 30.0


def _add_section_boxes(model, steps: list, positions: dict):


    if not hasattr(model, "addGroupBox"):
        return
    try:
        from qgis.core import QgsProcessingModelGroupBox
    except ImportError:
        return
    sections: dict = {}
    for step in steps:
        section = step.get("section")
        if isinstance(section, str) and section.strip():
            sections.setdefault(section.strip(), []).append(step["id"])
    for index, (title, ids) in enumerate(sections.items()):
        corners = []
        for step_id in ids:
            centre = positions[step_id]
            corners.append(centre)
            for output in model.childAlgorithm(step_id).modelOutputs().values():
                corners.append(output.position())
        left = min(p.x() for p in corners) - _BOX_W / 2 - _BOX_PAD
        right = max(p.x() for p in corners) + _BOX_W / 2 + _BOX_PAD
        top = min(p.y() for p in corners) - _BOX_H / 2 - _BOX_PAD
        bottom = max(p.y() for p in corners) + _BOX_H / 2 + _BOX_PAD
        box = QgsProcessingModelGroupBox(title)
        box.setPosition(QPointF((left + right) / 2, (top + bottom) / 2))
        box.setSize(QSizeF(right - left, bottom - top))
        box.setColor(QColor(_SECTION_COLORS[index % len(_SECTION_COLORS)]))
        model.addGroupBox(box)




_OPEN_DESIGNERS: list = []


def _open_in_designer(model_id: str, path: str) -> str | None:

    try:
        from processing.modeler.ModelerDialog import ModelerDialog
    except ImportError:
        return "the Processing plugin is not loaded"
    alg = QgsApplication.processingRegistry().algorithmById(model_id)
    if alg is None:


        alg = QgsProcessingModelAlgorithm()
        if not alg.fromFile(path):
            return "the saved file could not be read back"
        alg.setSourceFilePath(path)
    dialog = ModelerDialog.create(alg)
    provider = QgsApplication.processingRegistry().providerById("model")
    if provider is not None:
        dialog.update_model.connect(provider.refreshAlgorithms)
    _OPEN_DESIGNERS[:] = [d for d in _OPEN_DESIGNERS if d.isVisible()] + [dialog]
    dialog.show()
    dialog.activate()
    return None


def _create_processing_model(args: dict) -> dict:
    name = str(args["name"]).strip()
    if not name:
        return tool_error("Model 'name' is required", "INVALID_ARGS", "name is short, e.g. 'buffer_and_clip'.")
    folder, provider = _models_folder()
    os.makedirs(folder, exist_ok=True)
    try:
        final_name, target_path = _unique_model_path(folder, name)
        model, resolved, input_count, output_count = _build_model(
            final_name, args["steps"], args.get("inputs") or [], args.get("outputs") or [],
            args.get("description") or "", args.get("group") or "Models",
        )
    except _SpecError as e:
        return tool_error(str(e), "INVALID_ARGS", "" if e.hint else "Nothing was written; the error above "
                          "names the spec problem.", hint=e.hint, **e.facts)

    if not model.toFile(target_path):
        return tool_error(f"Failed to write model to {target_path}", "EXECUTION_FAILED",
                          "the models folder must be writable.")
    registered = _refresh_models(provider)
    log(f"Processing model '{final_name}' saved to {target_path}")
    model_id = f"model:{final_name}"
    answer = {
        "name": final_name, "requested_name": name, "id": model_id, "path": target_path,
        "registered": registered, "input_count": input_count, "step_count": len(resolved),
        "output_count": output_count,
        "resolved_steps": [{"id": step["id"], "algorithm": algorithm_id, "hint": step["algorithm"]}
                           for step, algorithm_id in resolved],
    }
    if args.get("open_in_designer"):
        try:
            why = _open_in_designer(model_id, target_path)
        except Exception as e:
            why = str(e)
        answer["opened_in_designer"] = True if why is None else f"not opened: {why}"
    return answer


def _refresh_models(provider) -> bool:

    if provider is None:
        return False
    try:
        provider.refreshAlgorithms()
    except Exception as e:
        log_warning(f"Model saved but provider refresh failed: {e}")
        return False
    return True


def _is_model(algorithm) -> bool:
    provider = algorithm.provider()
    return provider is not None and provider.id() == "model"


def _list_processing_models(args: dict) -> dict:
    rows = []
    for algorithm in filter(_is_model, QgsApplication.processingRegistry().algorithms()):
        row = {"id": algorithm.id(), "name": algorithm.displayName(), "group": algorithm.group()}
        try:
            row["path"] = algorithm.sourceFilePath()
        except Exception:  # nosec B110
            pass
        rows.append(row)
    rows.sort(key=lambda row: row["id"])
    return {"models": rows, "count": len(rows)}


def _model_from_file(given: str):

    path = expand_path(given)
    if not os.path.isfile(path):
        return None, path, tool_error(f"Model file not found: {path}", "INVALID_ARGS", hint="model_file_not_found")
    loaded = QgsProcessingModelAlgorithm()
    if not loaded.fromFile(path):
        return None, path, tool_error(f"Failed to load model file: {path}", "EXECUTION_FAILED",
                                      "The file is not a valid .model3; the Model Designer reads .model3 files.")
    loaded.initAlgorithm()
    return loaded, path, None


def _registered_model(given: str):

    registry = QgsApplication.processingRegistry()

    for model_id in (given,) if given.startswith("model:") else (given, f"model:{given}"):
        found = registry.algorithmById(model_id)
        if found is not None:
            return found, model_id
    titled = next((a for a in registry.algorithms() if a.provider() and a.provider().id() == "model"
                   and a.displayName() == given), None)
    return (titled, titled.id()) if titled is not None else (None, given)


def _run_model(args: dict) -> dict:
    model = str(args["model"]).strip()
    parameters = dict(args.get("parameters") or {})
    file_alg = None
    if model.lower().endswith(".model3"):
        file_alg, model, refusal = _model_from_file(model)
        if refusal:
            return refusal
        alg = file_alg
    else:
        alg, model = _registered_model(model)
        if alg is None:
            return tool_error(f"Model not found: {model!r}", "INVALID_ARGS", hint="model_not_registered")





    from .processing_destinations import _destination_names, _output_names

    parameters, renamed, misnamed = _output_names(alg, parameters, model)
    if misnamed:
        return misnamed

    def _named(outcome):
        if isinstance(outcome, dict) and not outcome.get("_error"):
            outcome["model"] = model
            if renamed:
                outcome["repairs"] = renamed + list(outcome.get("repairs") or [])
        return outcome



    unset = [definition.name() for definition in alg.parameterDefinitions()
             if isinstance(definition, QgsProcessingDestinationParameter) and definition.name() not in parameters]
    parameters.update(dict.fromkeys(unset, "TEMPORARY_OUTPUT"))

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
            return tool_error(problem, "PERMISSION_DENIED", hint="model_output_path_refused")








    from .processing_decisions import _threadable
    from .processing_run import _heavy_inputs

    if _threadable(file_alg):
        outcome = _named(_run_processing({"algorithm_id": model, "parameters": parameters}, algorithm=file_alg))
        if isinstance(outcome, dict) and isinstance(outcome.get("poll"), dict):
            outcome["poll"]["label"] = f"Running {os.path.basename(model)}"
        return outcome

    if _heavy_inputs(parameters):
        return tool_error(
            "A step of this model cannot run in the background, and the inputs are too large to run it on "
            "the main thread; nothing was run.",
            "INVALID_ARGS", hint="model_inputs_too_large")

    feedback = QgsProcessingFeedback()
    try:
        result = processing.run(file_alg, parameters, feedback=feedback)
    except Exception as e:
        return tool_error(f"Model run failed: {e}", "EXECUTION_FAILED", hint="model_run_failed")
    return _named({"model": model, "outputs": _process_outputs(result)})
