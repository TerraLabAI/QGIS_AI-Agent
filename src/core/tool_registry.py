# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tool catalog: one Tool per QGIS capability, one registry the executor calls into."""






















from __future__ import annotations

import hashlib
import json
import math
import os
import traceback
from copy import deepcopy
from typing import Any, Callable

from .logger import log_warning
from .serialization import reads_the_outside_world

DANGER_LEVELS = ("read", "write", "destructive")



VISIBLE_TOOLS = (
    "get_project_context",
    "list_layers",
    "get_layer_info",
    "get_features",
    "add_data",
    "run_processing",
    "execute_code",
    "render_map",
    "set_layer_style",
    "save_project",
    "ai_edit",
    "ai_segment",
    "ask_user",
)


def _expand_home(arguments: dict) -> dict:
    """Every "~/..." string argument as an absolute path, at any depth."""







    def walk(value):
        if isinstance(value, str):
            if value == "~" or value.startswith(("~/", "~\\")):
                return os.path.expanduser(value)
            return value
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        return value
    return walk(arguments)


def tool_error(message: str, code: str = "EXECUTION_FAILED", suggestion: str = "") -> dict:
    """The error result every tool returns on failure."""
    return {"_error": message, "code": code, "suggestion": suggestion}




_PLUGIN_DIR = os.path.normcase(os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")))



_MAX_ARGUMENT_DEPTH = 10


class _ArgumentDepthExceeded(Exception):
    """Raised internally when arguments nest deeper than is ever legitimate."""


def _short_traceback(error: BaseException, max_frames: int = 6) -> str:
    """``basename:lineno:function`` for plugin frames only, newest first."""
    frames = traceback.extract_tb(error.__traceback__)
    plugin_frames = [f for f in frames if os.path.normcase(os.path.abspath(f.filename)).startswith(_PLUGIN_DIR)]
    plugin_frames.reverse()
    return "\n".join(
        f"{os.path.basename(f.filename)}:{f.lineno}:{f.name}" for f in plugin_frames[:max_frames]
    )


def _title_restates(title, name: str | None) -> bool:
    """True when a schema title only repeats the property name (Pydantic noise)."""
    if not isinstance(title, str):
        return False
    if name is None:
        return True

    def squash(s):
        return "".join(ch for ch in s.lower() if ch.isalnum())
    return squash(title) == squash(name)


def strip_schema_titles(schema, name: str | None = None):
    """Drop the titles that carry no information, recursively, in place."""






    if not isinstance(schema, dict):
        return schema
    if "title" in schema and _title_restates(schema["title"], name):
        schema.pop("title")
    for key, sub in (schema.get("properties") or {}).items():
        strip_schema_titles(sub, key)
    for key in ("$defs", "definitions"):
        for sub_name, sub in (schema.get(key) or {}).items():
            strip_schema_titles(sub, sub_name)
    for key in ("items", "additionalProperties", "not"):
        if isinstance(schema.get(key), dict):
            strip_schema_titles(schema[key], name)
    for key in ("anyOf", "allOf", "oneOf"):
        for sub in schema.get(key) or []:
            strip_schema_titles(sub, name)
    return schema


class Tool:
    def __init__(
        self,
        name: str,
        input_schema: dict,
        handler: Callable[[dict], Any],
        description: str = "",
        danger: str | None = None,
        destructive: bool | None = None,
        policy_group: Any = None,
        output_schema: dict | None = None,
        visible: bool = False,
        hidden: bool = False,
        background: bool | Callable[[dict], bool] = False,
        idempotent: bool | None = None,
        open_world: bool | None = None,
    ):










        if danger is None:
            danger = "destructive" if destructive else "write"
        if danger not in DANGER_LEVELS:
            raise ValueError(f"Tool {name}: danger must be one of {DANGER_LEVELS}, got {danger!r}")
        self.name = name
        self.description = description
        self.input_schema = self._normalize_schema(input_schema)
        self.handler = handler
        self.danger = danger
        self.visible = visible
        self.hidden = hidden

        self.background = background if callable(background) else bool(background)








        self._idempotent = None if idempotent is None else bool(idempotent)



        self.open_world = reads_the_outside_world(name) if open_world is None else bool(open_world)

    @property
    def idempotent(self) -> bool:
        """Whether calling it twice gives the same answer."""




        return (self.danger == "read") if self._idempotent is None else self._idempotent

    @property
    def destructive(self) -> bool:
        return self.danger == "destructive"

    @property
    def annotations(self) -> dict:
        """The MCP tool annotations, derived from what the plugin already knows."""












        return {
            "readOnlyHint": self.danger == "read",
            "destructiveHint": self.danger == "destructive",
            "idempotentHint": bool(self.idempotent),
            "openWorldHint": bool(self.open_world),
        }

    def to_function_schema(self) -> dict:
        """OpenAI function-calling shape, plus the danger level the plugin enforces."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": strip_schema_titles(deepcopy(self.input_schema)),
            "danger": self.danger,
            "annotations": self.annotations,
        }

    @classmethod
    def _normalize_schema(cls, schema: dict) -> dict:
        normalized = deepcopy(schema)
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
        """Add a tool."""







        existing = self._tools.get(tool.name)
        if existing is not None and existing is not tool and not replace:




            log_warning(f"Tool '{tool.name}' is already registered; the second registration "
                        "was refused. Pass replace=True to override it deliberately.")
            return
        self._tools[tool.name] = tool

    def unregister(self, name: str):
        self._tools.pop(name, None)

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def get_tool(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def manifest(self) -> list[dict]:
        """Every non-hidden tool, sorted by name, in function-calling shape."""
        return [
            tool.to_function_schema()
            for tool in sorted(self._tools.values(), key=lambda t: t.name)
            if not tool.hidden
        ]

    def visible_names(self) -> list[str]:
        """The facade tools in their fixed order, restricted to what is registered."""
        return [name for name in VISIBLE_TOOLS if name in self._tools]

    def manifest_hash(self) -> str:
        canonical = json.dumps(self.manifest(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def execute(self, name: str, arguments: dict) -> dict:
        tool = self._tools.get(name)
        if not tool:
            return tool_error(
                f"Tool not found: {name}",
                "TOOL_NOT_FOUND",
                "Check the name against the manifest; search_tools finds the right one.",
            )
        try:
            if isinstance(arguments, dict):
                arguments = _expand_home(self._coerce_arguments(tool.input_schema, arguments))
            validation_error = self._validate_arguments(tool, arguments)
            if validation_error:
                return tool_error(
                    validation_error,
                    "INVALID_ARGS",
                    "Fix the arguments to match the tool's parameters schema and call again.",
                )
            result = tool.handler(arguments)
        except _ArgumentDepthExceeded:
            return tool_error(
                f"Arguments for tool '{name}' are nested too deeply",
                "INVALID_ARGS",
                "Flatten the arguments to match the tool's parameters schema and call again.",
            )
        except Exception as e:
            cls = e.__class__.__name__
            detail = str(e).strip()
            message = f"{cls}: {detail}" if detail else f"{cls} (no message)"
            full_traceback = traceback.format_exc()
            log_warning(f"Tool '{name}' raised {message}\n{full_traceback}")
            error = tool_error(
                message,
                "EXECUTION_FAILED",
                "Read the traceback, change the approach, and do not retry the same call unchanged.",
            )
            error["traceback"] = _short_traceback(e)
            return error
        return self._normalize_result(result)

    @staticmethod
    def _normalize_result(result: Any) -> Any:
        """Give legacy error results the ``code`` and ``suggestion`` keys."""






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
        """Best-effort coercion of common LLM string encodings to their schema type."""
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
        """Validate arguments against the supported JSON Schema subset."""
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
