# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

import os

from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core.tool_registry import NAMED_LAYER, Tool, ToolRegistry
from . import guards
from .layer_io_tools import (
    _add_field,
    _add_vector_layer,
    _export_layer,
)
from .layer_lookup import _find_layer
from .model_export import FORMATS as MODEL_FORMATS
from .model_export import MAX_CELLS_CEILING as MODEL_MAX_CELLS
from .model_export import _export_3d_model
from .processing_help import _get_algorithm_help, _list_algorithms
from .processing_run import _cancel_task, _get_task_status, _list_tasks, _run_processing, prepare_inputs
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
    _get_features_is_remote,
    _get_field_statistics,
    _get_provider_capabilities,
    _get_raster_band_stats,
    _get_renderer_info,
    _raster_sample,
)
from .style_tools import (
    _flash_features,
    _set_layer_labels,
    _set_layer_legend_image,
    _set_layer_style,
    _style_reads_rows,
)


def _runs_a_script(args: dict) -> bool:





    return str(args.get("algorithm_id") or "").strip().lower().startswith(("script:", "model:"))


def _processing_plan(args: dict) -> tuple[str, list]:
    return str(args.get("algorithm_id") or ""), [args.get("parameters")]


def _export_gpkg_table(args: dict, path: str) -> str | None:

    if os.path.splitext(path)[1].lower() != ".gpkg":
        return None
    if args.get("layer_name_in_file"):
        return str(args["layer_name_in_file"])
    return guards.layer_table_name(args.get("layer_name"), strip=False)


def register_core_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="get_project_context",
        danger="read",
        visible=1,
        label=QT_TRANSLATE_NOOP("AIAgent", "Read the project"),
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
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Read the project details"),
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_get_project_info,
    ))

    registry.register(Tool(
        name="list_layers",
        danger="read",
        visible=2,
        label=QT_TRANSLATE_NOOP("AIAgent", "List the layers"),
        input_schema={
            "type": "object",


            "x-verbose-extent": True,
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
        danger="read",
        visible=3,
        label=QT_TRANSLATE_NOOP("AIAgent", "Inspect {layer_name}"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "detail": {"type": "string", "enum": ["summary", "full"]},
            },
            "required": ["layer_name"],
        },
        handler=_get_layer_info,
    ))

    registry.register(Tool(
        name="add_vector_layer",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Add {path}[ as {name}]"),
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


        background=True,
    ))






    registry.register(Tool(
        name="remove_layer",
        danger="destructive",
        removes_layer_at="layer_name",
        label=QT_TRANSLATE_NOOP("AIAgent", "Remove {layer_name}"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
            },
            "required": ["layer_name"],
        },
        handler=_remove_layer,
    ))

    registry.register(Tool(
        name="zoom_to_layer",
        sets_view=True,
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Zoom to {layer_name}"),
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
        danger="read",
        visible=4,
        label=QT_TRANSLATE_NOOP("AIAgent", "Read features of {layer_name}"),
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


        background=_get_features_is_remote,
    ))

    registry.register(Tool(
        name="run_processing",
        danger="write",
        visible=6,
        card="algorithm",
        input_schema={
            "type": "object",


            "x-source-expression": True,
            "properties": {
                "algorithm_id": {
                    "type": "string",
                },
                "parameters": {"type": "object", "maxProperties": 500},
                "output_name": {
                    "type": "string",
                },


                "add_to_project": {
                    "type": "boolean",
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


        prepare=prepare_inputs,
        always_confirm=_runs_a_script,
        argument_check=guards.paid_algorithm_refusal,
        processing=_processing_plan,
    ))

    registry.register(Tool(
        name="get_task_status",
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Check the running task"),
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
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Cancel the running task"),
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
        danger="read",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_list_tasks,
    ))

    registry.register(Tool(
        name="evaluate_expression",
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Evaluate an expression"),
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
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Statistics of {field} in {layer_name}"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "field": {"type": "string"},
                "group_by": {"type": "string"},
                "filter": {"type": "string"},
            },
            "required": ["layer_name", "field"],


            "x-field-expression": True,
        },
        handler=_get_field_statistics,



        background=True,
    ))

    registry.register(Tool(
        name="get_renderer_info",
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Read the style of {layer_name}"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "filter": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 5000},
            },
            "required": ["layer_name"],
        },
        handler=_get_renderer_info,
    ))

    registry.register(Tool(
        name="raster_sample",
        danger="read",
        xy_crs=("crs", NAMED_LAYER),
        label=QT_TRANSLATE_NOOP("AIAgent", "Sample {layer_name} at a point"),
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
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Statistics of {layer_name}[, band {band}]"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "band": {"type": "integer", "minimum": 1, "maximum": 65535},
                "class_counts": {"type": "boolean"},
                "ranges": {"type": "array", "items": {"type": "array", "items": {"type": ["number", "null"]},
                                                      "minItems": 2, "maxItems": 2}, "maxItems": 50},
            },
            "required": ["layer_name"],
        },
        handler=_get_raster_band_stats,





        background=True,
    ))

    registry.register(Tool(
        name="get_provider_capabilities",
        danger="read",
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
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Check the geometries of {layer_name}"),
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
        name="set_layer_style",
        danger="write",
        visible=9,
        label=QT_TRANSLATE_NOOP("AIAgent", "Style {layer_name}"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "style_type": {
                    "type": "string",
                    "enum": ["single", "categorized", "graduated", "cluster", "pie", "bar", "diagram"],
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
                "fields": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 12},
                "diagram_fields": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 12},
                "diagram_type": {"type": "string", "enum": ["pie", "bar", "stacked_bar"]},
                "diagram_scale": {"type": "string", "enum": ["total", "fixed"]},
                "size_field": {"type": "string"},
                "colors": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
                "diagram_size": {"type": "number", "minimum": 1, "maximum": 1000},
                "multi_channel": {"type": "boolean"},
                "multi_channel_min_size": {"type": "number", "minimum": 0.1, "maximum": 1000},
                "multi_channel_max_size": {"type": "number", "minimum": 0.1, "maximum": 1000},
                "legend_image": {"type": "string"},
                "color_field": {"type": "string"},
                "keep_colors": {"type": "boolean"},
                "stroke_width_unit": {"type": "string", "enum": ["mm", "map_units"]},
                "label_field": {"type": "string"},


                "breaks": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 101},
                "invert_ramp": {"type": "boolean"},
                "color_stops": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 8},
                "null_class_label": {"type": "string"},
                "value_expression": {"type": "string"},
                "categories": {"type": "array", "items": {
                    "type": "object", "required": ["value"], "properties": {
                        "value": {"type": ["string", "number", "boolean", "null"]}, "label": {"type": "string"},
                        "color": {"type": "string"}, "size": {"type": "number"}, "width": {"type": "number"}}}},
            },
            "required": ["layer_name", "style_type"],
        },
        handler=_set_layer_style,



        background=_style_reads_rows,
    ))

    registry.register(Tool(
        name="set_layer_legend_image",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Set legend image for {layer_name}"),
        input_schema={"type": "object", "properties": {
            "layer_name": {"type": "string"}, "legend_image": {"type": "string"}},
            "required": ["layer_name", "legend_image"], "additionalProperties": False},
        handler=lambda args: _set_layer_legend_image(_find_layer(args.get("layer_name")), args),
    ))






    registry.register(Tool(
        name="list_algorithms",
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Search the processing tools[ for {search}]"),
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
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Read the help of {algorithm_id}"),
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
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Label {layer_name}[ by {field}]"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "field": {"type": "string"},
                "expression": {"type": "string"},
                "rules": {"type": "array", "items": {"type": "object"}},
                "placement": {"type": "string", "enum": ["curved", "parallel", "line", "around_point", "horizontal",
                                                         "cartographic", "over_point", "perimeter",
                                                         "perimeter_curved", "free"]},
                "format_numbers": {"type": "boolean"},
                "decimals": {"type": "integer", "minimum": 0, "maximum": 10},
                "suppress_edge": {"type": "boolean"},
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
                "segment_lengths": {"type": "boolean"},
                "output_name": {"type": "string"},
                "line_position": {"type": "string", "enum": ["on", "above", "below", "above_below", "on_above_below"]},
                "merge_lines": {"type": "boolean"},
                "distance": {"type": "number", "minimum": 0, "maximum": 1000},
                "offset": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                "data_defined": {"type": "object"},
                "font": {"type": "string"},
                "italic": {"type": "boolean"},
                "bold": {"type": "boolean"},
                "unit": {"type": "string", "enum": ["m", "km", "ft"]},
                "ellipsoid": {"type": "string"},
                "priority": {"type": "integer", "minimum": 0, "maximum": 10},
                "z_index": {"type": "number"},
                "obstacle": {"type": "boolean"},
                "repeat_distance": {"type": "number", "minimum": 0, "maximum": 1000},
                "letter_spacing": {"type": "number", "minimum": -10, "maximum": 50},
                "capitalization": {"type": "string", "enum": ["upper", "lower", "title", "small_caps", "none"]},
                "opacity": {"type": "number", "minimum": 0, "maximum": 1},
                "reset": {"type": "boolean"},
            },








            "required": ["layer_name"],
        },
        handler=_set_layer_labels,
        argument_check=guards.labels_field_refusal,
    ))

    registry.register(Tool(
        name="export_layer",
        danger="destructive",
        label=QT_TRANSLATE_NOOP("AIAgent", "Export {layer_name} to {path}"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "path": {
                    "type": "string",
                },
                "layer_name_in_file": {"type": "string", "minLength": 1},
                "crs": {"type": "string"},
                "selected_only": {"type": "boolean"},
                "geometryless": {
                    "type": "boolean",
                },
                "overwrite": {
                    "type": "boolean",
                },


                "encoding": {"type": "string"},
                "polygons_as_lines": {"type": "boolean"},
                "cad_layer_field": {"type": "string"},
            },
            "required": ["layer_name", "path"],
        },
        handler=_export_layer,
        replaces_file_at="path",
        gpkg_table=_export_gpkg_table,
        table_at="layer_name_in_file",
    ))

    registry.register(Tool(
        name="export_3d_model",
        danger="destructive",
        label=QT_TRANSLATE_NOOP("AIAgent", "Export {layer_name} as a 3D model to {path}"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "path": {"type": "string"},



                "format": {"type": "string", "enum": list(MODEL_FORMATS)},
                "extent": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 4,
                    "maxItems": 4,
                },
                "z_factor": {"type": "number", "minimum": 1e-9, "maximum": 1e9},
                "base_thickness": {"type": "number", "minimum": 1e-9, "maximum": 1e12},
                "max_cells": {"type": "integer", "minimum": 9, "maximum": MODEL_MAX_CELLS},
                "band": {"type": "integer", "minimum": 1},
                "overwrite": {"type": "boolean"},
            },
            "required": ["layer_name", "path"],
        },


        background=True,
        handler=_export_3d_model,
        replaces_file_at="path",
    ))

    registry.register(Tool(
        name="add_field",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Add the field {field_name} to {layer_name}"),
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
                "measurement_mode": {
                    "type": "string",
                    "enum": ["ground", "planar_project"],
                    "default": "ground",
                },
                "expression": {
                    "type": "string",
                },
            },
            "required": ["layer_name", "field_name"],
        },
        handler=_add_field,
    ))




    registry.register(Tool(
        name="flash_features",
        danger="read",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "fids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 1,
                    "maxItems": 200,
                },
                "expression": {"type": "string"},
                "flashes": {"type": "integer", "minimum": 1, "maximum": 10},
                "duration": {"type": "integer", "minimum": 50, "maximum": 5000},
            },
            "required": ["layer_name"],
        },
        handler=_flash_features,
    ))
