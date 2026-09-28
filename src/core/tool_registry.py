# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
















from __future__ import annotations

import hashlib
import json
import math
import os
import traceback
from dataclasses import dataclass
from typing import Any, Callable

from .host_platform import IS_WINDOWS, expand_leading_env
from .logger import log_warning
from .serialization import reads_the_outside_world

DANGER_LEVELS = ("read", "write", "destructive")

NAMED_LAYER = "@layer"


def _schema_copy(value: Any) -> Any:






    if isinstance(value, dict):
        return {key: _schema_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_schema_copy(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_schema_copy(item) for item in value)
    return value





_SPECS: dict[str, Tool] = {}


def spec(name: str) -> Tool | None:

    return _SPECS.get(name)


def _expand_home(arguments: dict) -> dict:









    def walk(value):
        if isinstance(value, str):
            if value == "~" or value.startswith(("~/", "~\\")):
                return os.path.expanduser(value)
            return expand_leading_env(value)
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        return value
    return walk(arguments)


def tool_error(message: str, code: str = "EXECUTION_FAILED", suggestion: str = "", hint: str = "",
               **facts) -> dict:







    error = {"_error": message, "code": code, "suggestion": suggestion}
    if hint:
        error.update(facts, hint=hint)
    return error


class ServedValueMissing(RuntimeError):







    def __init__(self, key: str):
        super().__init__(f"The server did not send '{key}' for this session.")
        self.key = key


def coded_fact(hint: str, **facts) -> dict:







    return {"hint": hint, **facts}


def _network_failure(exc: BaseException) -> str | None:







    try:
        from . import net
        from .background import MainThreadBusy
    except ImportError:
        return None
    if isinstance(exc, MainThreadBusy):
        return None
    return net.describe_failure(exc)


def _network_suggestion() -> str:
    from . import net

    return net.NETWORK_SUGGESTION


def _refused_file_suggestion(exc: BaseException) -> str:







    if IS_WINDOWS and isinstance(exc, PermissionError):
        return ("The file is open in another program or read-only; closing it or writing to "
                "another file name or folder works.")
    return ""




_PLUGIN_DIR = os.path.normcase(os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")))



_MAX_ARGUMENT_DEPTH = 10


class _ArgumentDepthExceeded(Exception):
    pass


def _short_traceback(error: BaseException, max_frames: int = 6) -> str:

    frames = traceback.extract_tb(error.__traceback__)
    plugin_frames = [f for f in frames if os.path.normcase(os.path.abspath(f.filename)).startswith(_PLUGIN_DIR)]
    plugin_frames.reverse()
    return "\n".join(
        f"{os.path.basename(f.filename)}:{f.lineno}:{f.name}" for f in plugin_frames[:max_frames]
    )


def _title_restates(title, name: str | None) -> bool:

    if not isinstance(title, str):
        return False
    if name is None:
        return True

    def squash(s):
        return "".join(ch for ch in s.lower() if ch.isalnum())
    return squash(title) == squash(name)




_NAMED_CHILDREN = ("properties", "$defs", "definitions")
_SINGLE_CHILD = ("items", "additionalProperties", "not")
_CHILD_LISTS = ("anyOf", "allOf", "oneOf")


def strip_schema_titles(schema, name: str | None = None):





    pending = [(schema, name)]
    while pending:
        node, label = pending.pop()
        if not isinstance(node, dict):
            continue
        if "title" in node and _title_restates(node["title"], label):
            del node["title"]
        for keyword in _NAMED_CHILDREN:
            pending.extend((child, child_key) for child_key, child in (node.get(keyword) or {}).items())
        pending.extend((node[keyword], label) for keyword in _SINGLE_CHILD if isinstance(node.get(keyword), dict))
        for keyword in _CHILD_LISTS:
            pending.extend((child, label) for child in node.get(keyword) or [])
    return schema


@dataclass(frozen=True, eq=False)
class Tool:




















































































    name: str
    input_schema: dict
    handler: Callable[[dict], Any]
    danger: str
    label: str = ""
    description: str = ""
    visible: int | None = None
    hidden: bool = False
    background: bool | Callable[[dict], bool] = False
    idempotent: bool | None = None
    open_world: bool | None = None
    preflight: Callable[[dict], Any] | None = None
    prepare: Callable[[dict], Any] | None = None
    catalog: bool = False



    label_for: Callable[[dict], str] | None = None




    card: str = ""



    provider: str = ""



    xy_crs: tuple[str, ...] = ()


    sets_view: bool = False



    builds_layout_at: str = ""
    removes_layout_at: str = ""
    always_confirm: bool | Callable[[dict], bool] = False
    argument_check: Callable[[dict], dict | None] | None = None
    action_danger: dict | None = None
    reads_when: Callable[[dict], bool] | None = None
    destructive_when: Callable[[dict], bool] | None = None
    replaces_file_at: str = ""
    processing: Callable[[dict], tuple[str, list]] | None = None
    gpkg_table: Callable[[dict, str], str | None] | None = None
    table_at: str = ""
    saves_open_project: bool = False
    removes_layer_at: str = ""
    waits_on_user: bool = False

    def __post_init__(self):
        if self.danger not in DANGER_LEVELS:
            raise ValueError(f"Tool {self.name}: danger must be one of {DANGER_LEVELS}, got {self.danger!r}")
        fixed = {
            "input_schema": self._normalize_schema(self.input_schema),

            "background": self.background if callable(self.background) else bool(self.background),
            "preflight": self.preflight if callable(self.preflight) else None,
            "prepare": self.prepare if callable(self.prepare) else None,
            "catalog": bool(self.catalog),
            "always_confirm": self.always_confirm if callable(self.always_confirm) else bool(self.always_confirm),
            "saves_open_project": bool(self.saves_open_project),
            "waits_on_user": bool(self.waits_on_user),
            "idempotent": (self.danger == "read") if self.idempotent is None else bool(self.idempotent),
            "open_world": reads_the_outside_world(self.name) if self.open_world is None else bool(self.open_world),
        }
        for key, value in fixed.items():
            object.__setattr__(self, key, value)

    @property
    def destructive(self) -> bool:
        return self.danger == "destructive"

    @property
    def annotations(self) -> dict:













        from . import limits, machine

        hints = {
            "readOnlyHint": self.danger == "read",
            "destructiveHint": self.danger == "destructive",
            "idempotentHint": bool(self.idempotent),
            "openWorldHint": bool(self.open_world),

            "executionHint": "dynamic" if callable(self.background) else ("background" if self.background else "main"),
        }
        if self.background:
            hints["executionMaxSeconds"] = limits.CALL_MAX_SECONDS_BACKGROUND * machine.MAX_CLOCK_STRETCH
        return hints

    def to_function_schema(self) -> dict:

        return {
            "name": self.name,
            "description": self.description,
            "parameters": strip_schema_titles(_schema_copy(self.input_schema)),
            "danger": self.danger,
            "annotations": self.annotations,
        }

    @classmethod
    def _normalize_schema(cls, schema: dict) -> dict:
        normalized = _schema_copy(schema)
        cls._normalize_schema_in_place(normalized)
        return normalized

    @classmethod
    def _normalize_schema_in_place(cls, schema: Any):
        if not isinstance(schema, dict):
            return
        schema_type = schema.get("type")
        if (
            schema_type == "object"
            and "additionalProperties" not in schema
            and schema.get("properties")
        ):
            schema["additionalProperties"] = False
        if schema_type == "object":
            for prop in schema.get("properties", {}).values():
                cls._normalize_schema_in_place(prop)
        if schema_type == "array" and "items" in schema:
            cls._normalize_schema_in_place(schema["items"])


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool, replace: bool = False):








        existing = self._tools.get(tool.name)
        if existing is not None and existing is not tool and not replace:




            log_warning(f"Tool '{tool.name}' is already registered; the second registration "
                        "was refused. Pass replace=True to override it deliberately.")
            return
        self._tools[tool.name] = tool
        _SPECS[tool.name] = tool

    def unregister(self, name: str):
        self._tools.pop(name, None)

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def get_tool(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def manifest(self) -> list[dict]:

        return [
            tool.to_function_schema()
            for tool in sorted(self._tools.values(), key=lambda t: t.name)
            if not tool.hidden
        ]

    def visible_names(self) -> list[str]:

        shown = [tool for tool in self._tools.values() if tool.visible is not None]
        return [tool.name for tool in sorted(shown, key=lambda tool: tool.visible)]

    def manifest_hash(self, manifest: list[dict] | None = None) -> str:

        canonical = json.dumps(self.manifest() if manifest is None else manifest,
                               sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def execute(self, name: str, arguments: dict) -> dict:
        tool = self._tools.get(name)
        if not tool:
            return tool_error(
                f"Tool not found: {name}",
                "TOOL_NOT_FOUND",
                "search_tools finds the right name against the manifest.",
            )
        try:
            if isinstance(arguments, dict):
                arguments = _expand_home(self._coerce_arguments(tool.input_schema, arguments))
            validation_error = self._validate_arguments(tool, arguments)
            if validation_error:
                return tool_error(
                    validation_error,
                    "INVALID_ARGS",
                    "The tool's parameters schema states what each argument must be.",
                )
            result = tool.handler(arguments)
        except _ArgumentDepthExceeded:
            return tool_error(
                f"Arguments for tool '{name}' are nested too deeply",
                "INVALID_ARGS",
                "A flatter argument shape matches the tool's parameters schema.",
            )
        except ServedValueMissing as e:
            log_warning(f"Tool '{name}': {e}")
            return tool_error(str(e), "SERVICE_NOT_RECEIVED", "It arrives after reconnect.",
                              hint="served_value_missing", key=e.key)
        except Exception as e:
            network = _network_failure(e)
            if network:


                log_warning(f"Tool '{name}' lost its network: {network}")
                return tool_error(network, "NETWORK_ERROR", _network_suggestion())
            cls = e.__class__.__name__
            detail = str(e).strip()
            message = f"{cls}: {detail}" if detail else f"{cls} (no message)"
            full_traceback = traceback.format_exc()
            log_warning(f"Tool '{name}' raised {message}\n{full_traceback}")
            error = tool_error(
                message,
                "EXECUTION_FAILED",
                _refused_file_suggestion(e)
                or "The traceback names the error; the same call unchanged fails the same way.",
            )
            error["traceback"] = _short_traceback(e)
            return error
        return self._normalize_result(result)

    @staticmethod
    def _normalize_result(result: Any) -> Any:







        if not isinstance(result, dict) or result.get("_error") is None:
            return result
        err = result["_error"]
        if isinstance(err, dict):
            message = err.get("message") or err.get("error") or ""
            result.setdefault("code", err.get("code") or "EXECUTION_FAILED")
            result.setdefault("suggestion", err.get("suggestion") or "")
            result["_error"] = message
        else:
            result.setdefault("code", result.pop("_code", None) or "EXECUTION_FAILED")
            result.setdefault("suggestion", result.pop("_suggestion", None) or "")
        if not str(result["_error"]).strip():
            result["_error"] = "The tool failed but returned no error detail."
            result["code"] = result.get("code") or "EMPTY_ERROR"
        return result

    @classmethod
    def _coerce_arguments(cls, schema: dict, value: Any, _depth: int = 0) -> Any:

        if _depth > _MAX_ARGUMENT_DEPTH:
            raise _ArgumentDepthExceeded()
        if not isinstance(schema, dict):
            return value
        schema_type = schema.get("type")
        if schema_type == "object" and isinstance(value, dict):
            properties = schema.get("properties", {})
            return {
                k: cls._coerce_arguments(properties.get(k, {}), v, _depth + 1) if k in properties else v
                for k, v in value.items()
            }
        if schema_type == "array" and isinstance(value, list):
            item_schema = schema.get("items", {})
            return [cls._coerce_arguments(item_schema, v, _depth + 1) for v in value]
        enum_values = schema.get("enum")
        if isinstance(value, str) and enum_values and value not in enum_values:



            spelled = [e for e in enum_values if isinstance(e, str) and e.lower() == value.strip().lower()]
            if len(spelled) == 1:
                return spelled[0]
        if schema_type == "boolean" and isinstance(value, str):
            low = value.strip().lower()
            if low in ("true", "1", "yes", "y"):
                return True
            if low in ("false", "0", "no", "n"):
                return False
        if schema_type == "integer" and isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                try:

                    as_float = float(value)
                    if as_float.is_integer():
                        return int(as_float)
                except ValueError:
                    pass
                return value
        if schema_type == "number" and isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return value
        return value

    @staticmethod
    def _validate_arguments(tool: Tool, arguments: dict) -> str | None:

        if not isinstance(arguments, dict):
            return f"arguments must be an object for tool '{tool.name}'"
        return ToolRegistry._validate_schema_value(tool.input_schema, arguments, "arguments", tool.name)

    @staticmethod
    def _validate_schema_value(schema: dict, value: Any, path: str, tool_name: str, _depth: int = 0) -> str | None:
        if _depth > _MAX_ARGUMENT_DEPTH:
            return f"{path} for tool '{tool_name}' is nested too deeply"
        expected = schema.get("type")
        if expected is not None:
            type_error = ToolRegistry._validate_type(expected, value, path, tool_name)
            if type_error:
                return type_error

        enum_values = schema.get("enum")
        if enum_values is not None and value not in enum_values:
            return f"Invalid value for {path} in tool '{tool_name}': expected one of {enum_values}"








        if isinstance(value, float) and not math.isfinite(value):





            return f"Value for {path} in tool '{tool_name}' is not a finite number"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            low = schema.get("minimum")
            if isinstance(low, (int, float)) and value < low:
                return f"Value for {path} in tool '{tool_name}' is below the minimum of {low}"
            high = schema.get("maximum")
            if isinstance(high, (int, float)) and value > high:
                return f"Value for {path} in tool '{tool_name}' is above the maximum of {high}"

        schema_type = schema.get("type")
        if isinstance(schema_type, list):
            if isinstance(value, dict):
                schema_type = "object"
            elif isinstance(value, list):
                schema_type = "array"
            elif isinstance(value, bool):
                schema_type = "boolean"
            elif isinstance(value, int) and not isinstance(value, bool):
                schema_type = "integer"
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                schema_type = "number"
            elif isinstance(value, str):
                schema_type = "string"

        if schema_type == "object":
            required = schema.get("required", [])
            properties = schema.get("properties", {})
            for field in required:
                if field not in value:
                    desc = properties.get(field, {}).get("description", "")
                    hint = f" ({desc})" if desc else ""
                    return f"Missing required parameter '{field}'{hint} for tool '{tool_name}'"

            if schema.get("additionalProperties") is False:
                unknown = sorted(set(value.keys()) - set(properties.keys()))
                nested = path.startswith("arguments.")
                if unknown and nested and required and all(f in value for f in required):







                    for key in unknown:
                        del value[key]
                    unknown = []
                if unknown:








                    where = f" in {path[len('arguments.'):]}" if path.startswith("arguments.") else ""
                    wanted = ", ".join(sorted(properties)) if properties else ""
                    tail = f"; it takes {wanted}" if wanted else ""
                    return (f"Unexpected parameter(s) for tool '{tool_name}'{where}: "
                            f"{', '.join(unknown)}{tail}")

            for key, field_value in value.items():
                child_schema = properties.get(key)
                if not child_schema:
                    continue
                error = ToolRegistry._validate_schema_value(
                    child_schema,
                    field_value,
                    f"{path}.{key}",
                    tool_name,
                    _depth + 1,
                )
                if error:
                    return error

        if schema_type == "array":
            min_items = schema.get("minItems")
            if min_items is not None and len(value) < min_items:
                return f"{path} for tool '{tool_name}' must contain at least {min_items} item(s)"
            max_items = schema.get("maxItems")
            if max_items is not None and len(value) > max_items:
                return f"{path} for tool '{tool_name}' must contain at most {max_items} item(s)"
            item_schema = schema.get("items")
            if item_schema:
                for idx, item in enumerate(value):
                    error = ToolRegistry._validate_schema_value(
                        item_schema,
                        item,
                        f"{path}[{idx}]",
                        tool_name,
                        _depth + 1,
                    )
                    if error:
                        return error

        return None

    @staticmethod
    def _validate_type(expected: str | list[str], value: Any, path: str, tool_name: str) -> str | None:
        expected_types = expected if isinstance(expected, list) else [expected]
        for type_name in expected_types:
            if ToolRegistry._matches_type(type_name, value):
                return None
        joined = " or ".join(expected_types)
        return f"Invalid value for {path} in tool '{tool_name}': expected {joined}"

    @staticmethod
    def _matches_type(type_name: str, value: Any) -> bool:
        if type_name == "object":
            return isinstance(value, dict)
        if type_name == "array":
            return isinstance(value, list)
        if type_name == "string":
            return isinstance(value, str)
        if type_name == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if type_name == "number":
            if isinstance(value, float) and not math.isfinite(value):
                return False
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if type_name == "boolean":
            return isinstance(value, bool)
        if type_name == "null":
            return value is None
        return True

    @property
    def tool_count(self) -> int:
        return len(self._tools)

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())
