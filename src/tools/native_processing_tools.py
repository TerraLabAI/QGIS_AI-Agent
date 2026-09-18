# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""One deferred AI tool per safe, output-producing QGIS Processing algorithm."""



















from __future__ import annotations

import json
import math
import re
from html import unescape
from typing import Any

from qgis.core import Qgis, QgsApplication

from ..core.logger import log_warning
from ..core.tool_registry import Tool as RuntimeTool
from ..core.tool_registry import ToolRegistry, tool_error
from .processing_tools import _run_processing








_PROVIDER_NAMESPACES = {
    "native": "native",
    "qgis": "processing",
    "gdal": "gdal",
    "pdal": "pdal",
    "3d": "3d",
    "grass": "grass",
    "grass7": "grass",
    "saga": "saga",
    "sagang": "saga",
    "otb": "otb",
}




_EXCLUDED = frozenset({
    "native:batchnominatimgeocoder",
    "native:downloadgpsdata",
    "native:downloadvectortiles",
    "native:filedownloader",
    "native:httprequest",
    "native:openurl",
    "native:uploadgpsdata",
})







_CATALOG_BUDGET_BYTES = 600_000
_CORE_NAMESPACES = ("native", "processing", "gdal", "pdal", "3d")

_CONTROL_KEYS = frozenset({"output_name", "async", "confirm_large", "invalid_geometry_filter"})
_ARRAY_TYPES = frozenset({
    "alignrasterlayers",
    "dxflayers",
    "multilayer",
    "tininputlayers",
    "vectortilewriterlayers",
})
_COMPLEX_TYPES = frozenset({"aggregates", "fields_mapping"})
_STRING_TYPES = frozenset({
    "authcfg",
    "color",
    "coordinateoperation",
    "crs",
    "databaseschema",
    "databasetable",
    "expression",
    "extent",
    "file",
    "geometry",
    "layer",
    "layout",
    "layoutitem",
    "maptheme",
    "mesh",
    "point",
    "pointcloud",
    "providerconnection",
    "raster",
    "source",
    "string",
    "vector",
})
_NUMBER_TYPES = frozenset({"area", "distance", "duration", "number", "scale"})
_RAW_EXTERNAL_PARAMETERS = frozenset({"EXTRA"})
_REMOTE_RE = re.compile(r"(?:https?|ftp)://|/vsicurl/", re.IGNORECASE)


def _enum_value(enum_owner: Any, name: str, fallback: int) -> int:
    try:
        return int(getattr(enum_owner, name))
    except (AttributeError, TypeError, ValueError):
        return fallback


_PARAMETER_FLAGS = getattr(Qgis, "ProcessingParameterFlag", object)
_ALGORITHM_FLAGS = getattr(Qgis, "ProcessingAlgorithmFlag", object)
_FLAG_OPTIONAL = _enum_value(_PARAMETER_FLAGS, "FlagOptional", 8)
_FLAG_HIDDEN = _enum_value(_PARAMETER_FLAGS, "FlagHidden", 4)
_ALG_HIDDEN = _enum_value(_ALGORITHM_FLAGS, "HideFromToolbox", 2)
_ALG_DEPRECATED = _enum_value(_ALGORITHM_FLAGS, "Deprecated", 3)
_ALG_SECURITY_RISK = _enum_value(_ALGORITHM_FLAGS, "SecurityRisk", 1 << 15)


def _plain_default(value: Any) -> Any:
    """Return a JSON scalar/list default, or None for a Qt/QGIS object."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple)):
        out = [_plain_default(item) for item in value]
        return out if all(item is not None for item in out) else None
    return None


def _compact_help(algorithm, limit: int = 700) -> str:
    """Plain, bounded official help suitable for manifest metadata."""
    try:
        text = str(algorithm.shortHelpString() or "")
    except Exception:  # noqa: BLE001 - provider help is optional metadata
        return ""
    text = unescape(re.sub(r"<[^>]*>", " ", text))
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("; "))
    if end < limit // 2:
        end = cut.rfind(" ")
    return cut[:end + 1].rstrip()


def _provider(algorithm) -> tuple[str, str]:
    try:
        provider = algorithm.provider()
        provider_id = str(provider.id() or "")
        provider_name = str(provider.name() or provider_id)
    except Exception:  # noqa: BLE001 - the algorithm id still identifies its provider
        provider_id = str(algorithm.id() or "").split(":", 1)[0]
        provider_name = provider_id
    return provider_id, provider_name


def _tool_name(algorithm) -> str:
    provider_id, _provider_name = _provider(algorithm)
    namespace = _PROVIDER_NAMESPACES.get(provider_id.casefold(), "")
    suffix = str(algorithm.id() or "").split(":", 1)[-1]
    suffix = re.sub(r"[^a-z0-9_]+", "_", suffix.lower()).strip("_")
    return f"qgis_{namespace}_{suffix}" if namespace and suffix else ""


def _has_remote_input(value: Any) -> bool:
    if isinstance(value, str):
        return bool(_REMOTE_RE.search(value))
    if isinstance(value, dict):
        return any(_has_remote_input(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_remote_input(item) for item in value)
    return False


def _bounds(schema: dict, metadata: dict) -> None:
    for source, target in (("min", "minimum"), ("max", "maximum")):
        value = metadata.get(source)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):


            if abs(float(value)) < 1e100:
                schema[target] = value


def _parameter_schema(definition) -> dict:
    """Translate a QgsProcessingParameterDefinition to a compact JSON Schema."""
    kind = str(definition.type() or "")
    try:
        metadata = dict(definition.toVariantMap() or {})
    except Exception:  # noqa: BLE001 - a provider parameter may omit serialization
        metadata = {}

    if kind == "boolean":
        schema: dict[str, Any] = {"type": "boolean"}
    elif kind in _NUMBER_TYPES:
        integer = kind == "number" and metadata.get("data_type") == 0
        schema = {"type": "integer" if integer else "number"}
        _bounds(schema, metadata)
    elif kind == "band":
        schema = {"type": "array", "items": {"type": "integer", "minimum": 1}} \
            if metadata.get("allow_multiple") else {"type": "integer", "minimum": 1}
    elif kind == "enum":
        options = [str(item) for item in (metadata.get("options") or [])]
        item = {"type": "integer", "minimum": 0}
        if options:
            item["maximum"] = len(options) - 1
        schema = {"type": "array", "items": item} if metadata.get("allow_multiple") else item
        if options:
            schema["x-qgis-options"] = options
    elif kind in {"attribute", "field"}:
        schema = {"type": "array", "items": {"type": "string"}} \
            if metadata.get("allow_multiple") else {"type": "string"}
    elif kind == "meshdatasetgroups":
        schema = {"type": "array", "items": {"type": "integer", "minimum": 0}}
    elif kind == "meshdatasettime":
        schema = {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": [
                        "current-context-time",
                        "defined-date-time",
                        "dataset-time-step",
                        "static",
                    ],
                },
                "value": {
                    "anyOf": [
                        {"type": "string"},
                        {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 2,
                            "maxItems": 2,
                        },
                    ],
                },
            },
            "required": ["type"],
            "additionalProperties": False,
        }
    elif kind == "relief_colors":
        schema = {
            "type": "array",
            "items": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 5,
                "maxItems": 5,
            },
        }
    elif kind in {"execute_sql", "idw_interpolation_data"}:

        schema = {"type": "string"}
    elif kind in _ARRAY_TYPES:
        schema = {"type": "array", "items": {"type": ["string", "object"]}}
    elif kind in _COMPLEX_TYPES:
        schema = {"type": "array", "items": {"type": "object"}}
    elif kind == "matrix":
        schema = {"type": "array", "items": {}}
        headers = metadata.get("headers")
        if isinstance(headers, list) and headers:
            schema["x-qgis-columns"] = [str(item) for item in headers]
    elif kind in _STRING_TYPES:
        schema = {"type": "string"}
    else:


        schema = {"type": ["string", "number", "boolean", "array", "object"]}

    label = metadata.get("description")
    help_text = metadata.get("help")
    if isinstance(label, str) and label.strip():
        schema["x-qgis-label"] = " ".join(label.split())
    if isinstance(help_text, str) and help_text.strip():
        schema["x-qgis-help"] = " ".join(help_text.split())
    if metadata.get("parent_layer"):
        schema["x-qgis-parent"] = str(metadata["parent_layer"])
    default = _plain_default(metadata.get("default"))
    if default is not None:
        schema["default"] = default
    return schema


def _schema_for(algorithm) -> dict:
    provider_id, provider_name = _provider(algorithm)
    properties: dict[str, dict] = {}
    required: list[str] = []
    for definition in algorithm.parameterDefinitions():
        try:
            flags = int(definition.flags())
        except (TypeError, ValueError):
            flags = 0
        if flags & _FLAG_HIDDEN or getattr(definition, "isDestination", lambda: False)():
            continue
        name = str(definition.name() or "")
        if not name or (provider_id.casefold() != "native" and name.upper() in _RAW_EXTERNAL_PARAMETERS):
            continue
        properties[name] = _parameter_schema(definition)
        try:
            default = _plain_default(definition.defaultValue())
        except Exception:  # noqa: BLE001 - no default means required below
            default = None
        if not flags & _FLAG_OPTIONAL and default is None:
            required.append(name)

    properties.update({
        "output_name": {"type": "string"},
        "async": {"type": "boolean", "default": False},
        "confirm_large": {"type": "boolean", "default": False},
        "invalid_geometry_filter": {
            "type": "string",
            "enum": ["default", "skip", "abort"],
            "default": "default",
        },
    })
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "x-qgis-algorithm-id": str(algorithm.id()),
        "x-qgis-algorithm-name": str(algorithm.displayName()),
        "x-qgis-algorithm-help": _compact_help(algorithm),
        "x-qgis-provider-id": provider_id,
        "x-qgis-provider-name": provider_name,
        "x-qgis-group": str(algorithm.group()),
    }


def _handler(algorithm_id: str):
    def run(args: dict) -> dict:
        controls = {key: args[key] for key in _CONTROL_KEYS if key in args}
        parameters = {key: value for key, value in args.items() if key not in _CONTROL_KEYS}
        if _has_remote_input(parameters):
            return tool_error(
                "A generated Processing tool cannot open a remote URL directly.",
                "INVALID_ARGS",
                "Load the URL with add_data or another guarded data tool, then pass the resulting layer name.",
            )
        return _run_processing({"algorithm_id": algorithm_id, "parameters": parameters, **controls})

    return run


def _eligible(algorithm) -> bool:
    algorithm_id = str(algorithm.id() or "")
    provider_id, _provider_name = _provider(algorithm)
    if provider_id.casefold() not in _PROVIDER_NAMESPACES or algorithm_id in _EXCLUDED:
        return False
    try:
        flags = int(algorithm.flags())
    except (TypeError, ValueError):
        flags = 0
    if flags & (_ALG_HIDDEN | _ALG_DEPRECATED | _ALG_SECURITY_RISK):
        return False
    try:
        group_id = str(algorithm.groupId()).lower()
    except Exception:  # noqa: BLE001 - group metadata is not execution safety
        group_id = ""
    if group_id in {"modelertools", "filetools"}:
        return False



    return any(getattr(item, "isDestination", lambda: False)()
               for item in algorithm.parameterDefinitions())


def register_native_processing_tools(registry: ToolRegistry) -> int:
    """Register one deferred tool per eligible installed Processing algorithm."""
    added = 0
    try:
        algorithms = list(QgsApplication.processingRegistry().algorithms())
    except Exception as exc:  # noqa: BLE001 - keep the rest of the catalog alive
        log_warning(f"QGIS Processing tools unavailable: {exc}")
        return 0

    def rank(algorithm) -> int:
        namespace = _PROVIDER_NAMESPACES.get(_provider(algorithm)[0].casefold(), "")
        return _CORE_NAMESPACES.index(namespace) if namespace in _CORE_NAMESPACES else len(_CORE_NAMESPACES)

    spent = 0
    left_out = 0
    for algorithm in sorted((a for a in algorithms if _eligible(a)), key=rank):
        name = _tool_name(algorithm)
        if not name or len(name) > 128 or registry.has_tool(name):
            continue
        schema = _schema_for(algorithm)
        size = len(name) + len(json.dumps(schema, ensure_ascii=False, separators=(",", ":"), default=str))
        if spent + size > _CATALOG_BUDGET_BYTES:
            left_out += 1
            continue
        spent += size



        registry.register(RuntimeTool(
            name=name,
            input_schema=schema,
            handler=_handler(str(algorithm.id())),
            danger="write",
        ))
        added += 1
    if left_out:
        log_warning(f"{left_out} Processing algorithms left out of the tool catalog to keep it under "
                    f"{_CATALOG_BUDGET_BYTES // 1000} kB; run_processing still runs them")
    return added
