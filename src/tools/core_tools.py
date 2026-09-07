# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Registers the core tool set: project/layer inspection, data loading and export, processing, read-only queries, symbology and screenshots."""








from __future__ import annotations

from ..core.tool_registry import Tool, ToolRegistry
from .layer_io_tools import (  # noqa: F401 - _sublayer_names/describe_sublayers re-exported
    _add_field,
    _add_point_cloud_layer,
    _add_raster_layer,
    _add_vector_layer,
    _add_vector_layer_is_remote,
    _export_layer,
    _sublayer_names,
    describe_sublayers,
)
from .layer_lookup import (  # noqa: F401 - re-exported for other tool modules
    _duplicate_layer_names,
    _field_not_found_error,
    _find_layer,
    _geometry_type_name,
    _is_qgis_null,
    _jsonable_value,
    _layer_not_found_error,
)
from .processing_tools import (  # noqa: F401 - _PROCESSING_TASKS/_sweep_consumed_tasks re-exported
    _PROCESSING_TASKS,
    _cancel_task,
    _get_algorithm_help,
    _get_task_status,
    _list_algorithms,
    _list_tasks,
    _process_outputs,
    _run_processing,
    _sweep_consumed_tasks,
)
from .project_tools import (
    _get_layer_info,
    _get_project_context,
    _get_project_info,
    _list_layers,
    _remove_layer,
    _zoom_to_layer,
)
from .query_tools import (
    _check_geometry_validity,
    _evaluate_expression,
    _get_features,
    _get_field_statistics,
    _get_provider_capabilities,
    _get_raster_band_stats,
    _get_renderer_info,
    _raster_sample,
)
from .style_tools import _make_batch_handler, _set_layer_labels, _set_layer_style, _take_screenshot


def register_core_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="get_project_context",
        input_schema={
            "type": "object",
            "properties": {
                "verbose": {"type": "boolean"},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5000,
                },
            },
            "required": [],
        },
        handler=_get_project_context,
    ))

    registry.register(Tool(
        name="get_project_info",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_get_project_info,
    ))

    registry.register(Tool(
        name="list_layers",
        input_schema={
            "type": "object",
            "properties": {
                "verbose": {
                    "type": "boolean",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5000,
                },
            },
            "required": [],
        },
        handler=_list_layers,
    ))

    registry.register(Tool(
        name="get_layer_info",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
            },
            "required": ["layer_name"],
        },
        handler=_get_layer_info,
    ))

    registry.register(Tool(
        name="add_vector_layer",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                },
                "name": {"type": "string"},
                "layer": {"type": "string"},




                "crs": {"type": "string"},
            },
            "required": ["path"],
        },
        handler=_add_vector_layer,



        background=_add_vector_layer_is_remote,
    ))

    registry.register(Tool(
        name="add_raster_layer",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "name": {"type": "string"},



                "layer": {"type": "string"},
            },
            "required": ["path"],
        },
        handler=_add_raster_layer,
    ))

    registry.register(Tool(
        name="remove_layer",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
            },
            "required": ["layer_name"],
        },
        handler=_remove_layer,
        destructive=True,
    ))

    registry.register(Tool(
        name="zoom_to_layer",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
            },
            "required": ["layer_name"],
        },
        handler=_zoom_to_layer,
    ))

    registry.register(Tool(
        name="get_features",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "expression": {"type": "string"},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5000,
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 1000000000,
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 1000,
                },
                "include_geometry": {
                    "type": "boolean",
                },
            },
            "required": ["layer_name"],
        },
        handler=_get_features,
    ))

    registry.register(Tool(
        name="run_processing",
        input_schema={
            "type": "object",
            "properties": {
                "algorithm_id": {
                    "type": "string",
                },
                "parameters": {"type": "object", "maxProperties": 500},
                "output_name": {
                    "type": "string",
                },
                "async": {
                    "type": "boolean",
                },
                "confirm_large": {
                    "type": "boolean",
                },
                "invalid_geometry_filter": {
                    "type": "string",
                    "enum": ["default", "skip", "abort"],
                },
            },
            "required": ["algorithm_id", "parameters"],
        },
        handler=_run_processing,
    ))

    registry.register(Tool(
        name="get_task_status",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
        handler=_get_task_status,
    ))

    registry.register(Tool(
        name="cancel_task",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
        handler=_cancel_task,
    ))

    registry.register(Tool(
        name="list_tasks",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_list_tasks,
    ))

    registry.register(Tool(
        name="evaluate_expression",
        input_schema={
            "type": "object",
            "properties": {
                "expression": {"type": "string"},
                "layer_name": {
                    "type": "string",
                },
                "feature_id": {
                    "type": "integer",
                    "minimum": -1,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                },
            },
            "required": ["expression"],
        },
        handler=_evaluate_expression,
    ))

    registry.register(Tool(
        name="get_field_statistics",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "field": {"type": "string"},
            },
            "required": ["layer_name", "field"],
        },
        handler=_get_field_statistics,



        background=True,
    ))

    registry.register(Tool(
        name="get_renderer_info",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
            },
            "required": ["layer_name"],
        },
        handler=_get_renderer_info,
    ))

    registry.register(Tool(
        name="raster_sample",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "x": {"type": "number"},
                "y": {"type": "number"},
                "band": {"type": "integer", "minimum": 1, "maximum": 65535},
                "crs": {"type": "string"},
            },
            "required": ["layer_name", "x", "y"],
        },
        handler=_raster_sample,
    ))

    registry.register(Tool(
        name="get_raster_band_stats",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "band": {"type": "integer", "minimum": 1, "maximum": 65535},
                "class_counts": {"type": "boolean"},
            },
            "required": ["layer_name"],
        },
        handler=_get_raster_band_stats,
    ))

    registry.register(Tool(
        name="get_provider_capabilities",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
            },
            "required": ["layer_name"],
        },
        handler=_get_provider_capabilities,
    ))

    registry.register(Tool(
        name="check_geometry_validity",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 5000},
            },
        },
        handler=_check_geometry_validity,
    ))

    registry.register(Tool(
        name="batch_commands",
        input_schema={
            "type": "object",
            "properties": {
                "commands": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 100,
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}, "arguments": {"type": "object"}},
                        "required": ["name"],
                    },
                },
                "stop_on_error": {"type": "boolean"},
            },
            "required": ["commands"],
        },
        handler=_make_batch_handler(registry),
    ))

    registry.register(Tool(
        name="set_layer_style",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "style_type": {
                    "type": "string",
                    "enum": ["single", "categorized", "graduated", "cluster"],
                },
                "field": {
                    "type": "string",
                },
                "color": {
                    "type": "string",
                },
                "color_ramp": {
                    "type": "string",
                },
                "classification_mode": {
                    "type": "string",
                    "enum": ["equal_interval", "quantile", "jenks", "pretty"],
                },
                "classes": {"type": "integer", "minimum": 1, "maximum": 100},
                "max_classes": {"type": "integer", "minimum": 1, "maximum": 500},
                "null_class_color": {
                    "type": "string",
                },
                "size_expression": {
                    "type": "string",
                },
                "fill": {
                    "type": "string",
                    "enum": ["solid", "none"],
                },
                "opacity": {"type": "number", "minimum": 0, "maximum": 1},
                "stroke_color": {"type": "string"},
                "stroke_width": {"type": "number", "minimum": 0, "maximum": 1000},
                "size": {"type": "number", "minimum": 0, "maximum": 1000},
                "cluster_distance": {"type": "number", "minimum": 0, "maximum": 10000},
                "min_size": {"type": "number", "minimum": 0, "maximum": 1000},
                "max_size": {"type": "number", "minimum": 0, "maximum": 1000},
                "label_color": {"type": "string"},
            },
            "required": ["layer_name", "style_type"],
        },
        handler=_set_layer_style,
    ))

    registry.register(Tool(
        name="take_screenshot",
        input_schema={
            "type": "object",
            "properties": {
                "max_width": {"type": "integer", "minimum": 1, "maximum": 3840},
                "save_path": {
                    "type": "string",
                },
                "layers_only": {
                    "type": "boolean",
                },
                "format": {
                    "type": "string",
                    "enum": ["png", "jpeg"],
                },
                "quality": {
                    "type": "integer",
                    "minimum": 10,
                    "maximum": 100,
                },
                "overwrite": {"type": "boolean"},
            },
            "required": [],
        },
        handler=_take_screenshot,
    ))

    registry.register(Tool(
        name="list_algorithms",
        input_schema={
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                },
                "limit": {"type": "integer", "minimum": 0, "maximum": 5000},
            },
            "required": [],
        },
        handler=_list_algorithms,
    ))

    registry.register(Tool(
        name="get_algorithm_help",
        input_schema={
            "type": "object",
            "properties": {
                "algorithm_id": {"type": "string"},
            },
            "required": ["algorithm_id"],
        },
        handler=_get_algorithm_help,
    ))

    registry.register(Tool(
        name="set_layer_labels",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "field": {"type": "string"},
                "size": {"type": "number", "minimum": 0.1, "maximum": 1000},
                "color": {"type": "string"},
                "enabled": {"type": "boolean"},
                "avoid_overlaps": {
                    "type": "boolean",
                },
                "buffer_size": {"type": "number", "minimum": 0, "maximum": 1000},
                "buffer_color": {"type": "string"},
                "min_scale": {"type": "number", "minimum": 0},
                "max_scale": {"type": "number", "minimum": 0},
            },








            "required": ["layer_name"],
        },
        handler=_set_layer_labels,
    ))

    registry.register(Tool(
        name="export_layer",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "path": {
                    "type": "string",
                },
                "crs": {"type": "string"},
                "selected_only": {"type": "boolean"},
                "geometryless": {
                    "type": "boolean",
                },
                "overwrite": {
                    "type": "boolean",
                },
            },
            "required": ["layer_name", "path"],
        },
        handler=_export_layer,
    ))

    registry.register(Tool(
        name="add_field",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "field_name": {"type": "string"},
                "field_type": {
                    "type": "string",



                    "enum": ["string", "text", "int", "integer", "long", "double", "float", "real",
                             "bool", "boolean", "date", "datetime"],
                },
                "expression": {
                    "type": "string",
                },
            },
            "required": ["layer_name", "field_name"],
        },
        handler=_add_field,
    ))
