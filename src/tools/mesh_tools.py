# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

import os
from typing import Any

from qgis.core import QgsApplication, QgsMeshLayer, QgsProject
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core import security
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .layer_lookup import _find_layer
from .processing_tools import _run_processing


def register_mesh_tools(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="configure_mesh_layer",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Inspect or configure a mesh layer"),
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["inspect", "load", "configure", "rasterize", "calculate"]},
                "layer_name": {"type": "string"},
                "source": {"type": "string"},
                "name": {"type": "string"},
                "scalar_group": {"type": "integer", "minimum": 0},
                "vector_group": {"type": "integer", "minimum": 0},
                "dataset_time": {"type": "number"},
                "vertical_level": {"type": "number"},
                "parameters": {"type": "object", "maxProperties": 100},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        handler=_configure_mesh_layer,
    ))


def _mesh_layer(args: dict[str, Any]):
    layer_name = str(args.get("layer_name") or "")
    layer = _find_layer(layer_name) if layer_name else None
    if not isinstance(layer, QgsMeshLayer):
        return None, tool_error(
            f"Mesh layer {layer_name!r} was not found.", "INVALID_ARGS",
            "Pass layer_name for a loaded mesh layer, or use action load with source.")
    return layer, None


def _call(obj: Any, name: str, *args: Any, default: Any = None) -> Any:
    value = getattr(obj, name, None)
    if not callable(value):
        return default
    try:
        return value(*args)
    except Exception:
        return default


def _groups(layer: QgsMeshLayer) -> list[dict[str, Any]]:
    count = int(_call(layer, "datasetGroupCount", default=0) or 0)
    result = []
    for index in range(max(0, count)):
        metadata = _call(layer, "datasetGroupMetadata", index)
        item: dict[str, Any] = {"index": index}
        if metadata is not None:
            for key in ("name", "isScalar", "isVector", "dataType", "minimum", "maximum"):
                value = _call(metadata, key)
                if value is not None and isinstance(value, (str, int, float, bool)):
                    item[key] = value
        result.append(item)
    return result


def _times(layer: QgsMeshLayer) -> list[Any]:


    values = _call(layer, "datasetTimes", default=None)
    if isinstance(values, (list, tuple)):
        return [str(value) for value in values[:100]]
    return []


def _inspect(layer: QgsMeshLayer) -> dict[str, Any]:
    settings = _call(layer, "rendererSettings")
    output: dict[str, Any] = {
        "layer_name": layer.name(), "source": layer.source(),
        "provider": layer.providerType(), "valid": layer.isValid(),
        "groups": _groups(layer), "dataset_times": _times(layer),
        "extent": [layer.extent().xMinimum(), layer.extent().yMinimum(),
                   layer.extent().xMaximum(), layer.extent().yMaximum()],
        "capabilities": {"rasterize": _algorithm_available("native:meshrasterize"),
                         "calculate": _algorithm_available("native:meshcalculator")},
        "available_ranges": _available_ranges(layer),
    }
    if settings is not None:
        scalar = _call(settings, "scalarSettings")
        vector = _call(settings, "vectorSettings")
        output["renderer"] = {
            "active_scalar_group": _call(settings, "activeScalarDatasetGroup"),
            "active_vector_group": _call(settings, "activeVectorDatasetGroup"),
            "scalar": {"color_ramp": str(_call(scalar, "colorRampShader"))} if scalar else {},
            "vector": {"on_user_grid": _call(vector, "onUserDefinedGrid")} if vector else {},
        }
    temporal = _call(layer, "temporalProperties")
    if temporal is not None:
        output["temporal"] = {"active": bool(_call(temporal, "isActive", default=False)),
                              "range": str(_call(temporal, "fixedTemporalRange"))}
    return output


def _available_ranges(layer: QgsMeshLayer) -> dict[str, Any]:

    result: dict[str, Any] = {}
    for label, names in (("time", ("datasetTimeRange", "timeRange")),
                         ("vertical", ("datasetVerticalRange", "verticalRange", "verticalLevelRange"))):
        for name in names:
            value = _call(layer, name)
            if value is not None:
                result[label] = str(value)
                break
    return result


def _algorithm_available(algorithm_id: str) -> bool:
    try:
        return QgsApplication.processingRegistry().algorithmById(algorithm_id) is not None
    except Exception:
        return False


def _configure_mesh_layer(args: dict[str, Any]) -> dict[str, Any]:
    action = str(args.get("action") or "inspect")
    if action == "load":
        source = security.expand_path(str(args.get("source") or ""))
        if not source or not os.path.exists(source):
            return tool_error("source must be an existing GRIB or NetCDF mesh file.", "INVALID_ARGS",
                              "Pass a local .grib, .grb or .nc file.")
        path_error = security.validate_path(source)
        if path_error:
            return tool_error(path_error, "PERMISSION_DENIED")
        name = str(args.get("name") or os.path.basename(source))
        layer = QgsMeshLayer(source, name, "mdal")
        if not layer.isValid():
            return tool_error("QGIS MDAL could not open this mesh source.", "CAPABILITY_UNAVAILABLE",
                              "Check that this QGIS build has MDAL support and that the file is a mesh dataset.")
        QgsProject.instance().addMapLayer(layer)
        return _inspect(layer)
    layer, error = _mesh_layer(args)
    if error or layer is None:
        return error or tool_error("No mesh layer to work on.", "INVALID_ARGUMENT",
                                   "Name a mesh layer with layer_name, or load one first.")
    if action == "inspect":
        return _inspect(layer)
    if action == "calculate":
        if not _algorithm_available("native:meshcalculator"):
            return tool_error("This QGIS build has no native mesh calculator.", "CAPABILITY_UNAVAILABLE",
                              "Use a QGIS build or provider that exposes native:meshcalculator.")
        parameters = dict(args.get("parameters") or {})
        parameters["INPUT"] = layer
        return _run_processing({"algorithm_id": "native:meshcalculator", "parameters": parameters})
    if action == "rasterize":
        if not _algorithm_available("native:meshrasterize"):
            return tool_error("This QGIS build has no native mesh rasterizer.", "CAPABILITY_UNAVAILABLE",
                              "Use run_processing with an installed mesh rasterization algorithm.")
        parameters = dict(args.get("parameters") or {})
        parameters["INPUT"] = layer
        return _run_processing({"algorithm_id": "native:meshrasterize", "parameters": parameters})
    if action != "configure":
        return tool_error(
            f"Unknown mesh action {action!r}.", "INVALID_ARGS", "Use inspect, load, configure, rasterize or calculate."
        )
    settings = _call(layer, "rendererSettings")
    if settings is None:
        return tool_error("Mesh renderer settings are unavailable in this QGIS build.", "CAPABILITY_UNAVAILABLE",
                          "Inspect the layer or use a QGIS build with mesh rendering support.")
    changed = {}
    for key, method in (
        ("scalar_group", "setActiveScalarDatasetGroup"),
        ("vector_group", "setActiveVectorDatasetGroup"),
    ):
        if args.get(key) is not None and callable(getattr(settings, method, None)):
            getattr(settings, method)(int(args[key]))
            changed[key] = int(args[key])
    layer.setRendererSettings(settings)
    layer.triggerRepaint()
    result = _inspect(layer)
    result["changed"] = changed
    if args.get("dataset_time") is not None:
        result["dataset_time_requested"] = float(args["dataset_time"])
    if args.get("vertical_level") is not None:
        result["vertical_level_requested"] = float(args["vertical_level"])
    return result
