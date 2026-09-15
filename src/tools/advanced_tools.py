# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
from __future__ import annotations

from ..core.tool_registry import Tool, ToolRegistry
from . import isolated_code
from .advanced_code import (  # noqa: F401 - moved here, re-exported for the callers of advanced_tools
    _API_HELP_CHARS,
    _API_MODULES,
    _ATTR_RE,
    _CALL_RE,
    _ENUM_RE,
    _FAMILY_SHOWN,
    _MAX_OUTPUT,
    _NAME_RE,
    _QT_CORE_NAMES,
    _QT_GUI_NAMES,
    _SIP_LAYER_CONTAINERS_WARM,
    _api_class,
    _api_enum,
    _api_family,
    _api_help,
    _api_module_of,
    _cap,
    _execute_code,
    _put_capped,
    _qt_names,
    _run_code_in_qgis,
)
from .advanced_debug import (  # noqa: F401 - moved here, re-exported for the callers of advanced_tools
    _ENV_HIGHLIGHTS,
    _LOG_BUFFER_SIZE,
    _LOG_RESULT_BUDGET,
    _MSG_BUF,
    _MSG_BUF_CACHE,
    _MSG_CONNECTED,
    _clip_message,
    _connect_message_log,
    _get_debug_info,
    _get_message_log,
    _message_buffer,
    _on_message_received,
    _python_executable,
)
from .advanced_layouts import (  # noqa: F401 - moved here, re-exported for the callers of advanced_tools
    _EXPORT_EXTS,
    _IMAGE_FORMATS,
    _add_credit_label,
    _add_without_undo,
    _apply_scale,
    _crs_wkt,
    _dpi_for_ground,
    _export_format,
    _export_layout,
    _ground_per_pixel,
    _ground_text,
    _image_size,
    _layout_credits,
    _list_layouts,
    _map_layers,
    _metres_per_map_unit,
    _page_text,
    _remove_credit_label,
    _restore_scale_on_refusal,
    _world_file_path,
    _write_georeference,
    dpi_ceiling_advice,
)
from .advanced_render import (  # noqa: F401 - moved here, re-exported for the callers of advanced_tools
    _MAX_IMAGE_BYTES,
    _RENDER_PASS_FLOOR_S,
    _RENDER_PASS_SECONDS,
    _RENDER_TOTAL_SECONDS,
    _UNSAFE_IN_A_FRAME_NAME,
    _VECTOR_TILE_CLASS,
    MAX_FRAME_STEPS,
    _apply_background,
    _apply_quality_flags,
    _empty_memory_provider,
    _encode_jpeg,
    _hash_image,
    _image_is_uniform,
    _is_tiled_raster,
    _jpeg_under_cap,
    _NoLayer,
    _plan_camera_move,
    _plan_detection_reveal,
    _plan_render,
    _prepare_frame_folder,
    _render_camera_move,
    _render_detection_reveal,
    _render_map,
    _render_pass_async,
    _render_stable,
    _render_stable_async,
    _safe_frame_prefix,
    _vector_tile_class,
    _warm_tiles,
)


def register_advanced_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="execute_code",
        input_schema={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                },
            },
            "required": ["code"],
        },
        handler=_execute_code,
        destructive=True,
        background=isolated_code.runs_isolated,
    ))

    registry.register(Tool(
        name="render_map",
        input_schema={
            "type": "object",
            "properties": {
                "width": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 3840,
                },
                "height": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2160,
                },
                "extent": {
                    "type": "object",
                    "properties": {
                        "xmin": {"type": "number"},
                        "ymin": {"type": "number"},
                        "xmax": {"type": "number"},
                        "ymax": {"type": "number"},
                    },



                    "required": ["xmin", "ymin", "xmax", "ymax"],
                },
                "layer_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 200,
                },
                "crs": {"type": "string"},
                "background": {
                    "type": "string",
                },
                "warmup": {
                    "type": "boolean",
                },
                "save_path": {
                    "type": "string",
                },
                "overwrite": {"type": "boolean"},
            },
            "required": [],
        },
        handler=_render_map,
        background=True,
    ))

    registry.register(Tool(
        name="render_detection_reveal",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {
                    "type": "string",
                },
                "out_dir": {"type": "string"},
                "steps": {"type": "integer", "minimum": 1, "maximum": 120},
                "order": {
                    "type": "string",
                    "enum": ["random", "area_desc", "area_asc", "top_down", "left_right"],
                },
                "extent": {
                    "type": "object",
                    "properties": {
                        "xmin": {"type": "number"},
                        "ymin": {"type": "number"},
                        "xmax": {"type": "number"},
                        "ymax": {"type": "number"},
                    },



                    "required": ["xmin", "ymin", "xmax", "ymax"],
                },
                "base_layer_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 200,
                },
                "width": {"type": "integer", "minimum": 1, "maximum": 3840},
                "height": {"type": "integer", "minimum": 1, "maximum": 2160},
                "prefix": {"type": "string"},
                "random_colors": {
                    "type": "boolean",
                },
                "fill_color": {
                    "type": "string",
                },
                "background": {
                    "type": "string",
                },
                "warmup": {
                    "type": "boolean",
                },
            },
            "required": ["layer_name", "out_dir"],
        },
        handler=_render_detection_reveal,
        background=True,
    ))

    registry.register(Tool(
        name="render_camera_move",
        input_schema={
            "type": "object",
            "properties": {
                "out_dir": {"type": "string"},
                "from_extent": {
                    "type": "object",
                    "properties": {
                        "xmin": {"type": "number"},
                        "ymin": {"type": "number"},
                        "xmax": {"type": "number"},
                        "ymax": {"type": "number"},
                    },



                    "required": ["xmin", "ymin", "xmax", "ymax"],
                },
                "to_extent": {
                    "type": "object",
                    "properties": {
                        "xmin": {"type": "number"},
                        "ymin": {"type": "number"},
                        "xmax": {"type": "number"},
                        "ymax": {"type": "number"},
                    },



                    "required": ["xmin", "ymin", "xmax", "ymax"],
                },
                "to_layer": {
                    "type": "string",
                },
                "to_zoom": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 1000000,
                },
                "steps": {"type": "integer", "minimum": 2, "maximum": 120},
                "layer_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 200,
                },
                "width": {"type": "integer", "minimum": 1, "maximum": 3840},
                "height": {"type": "integer", "minimum": 1, "maximum": 2160},
                "prefix": {"type": "string"},
                "background": {"type": "string"},
                "warmup": {
                    "type": "boolean",
                },
            },
            "required": ["out_dir"],
        },
        handler=_render_camera_move,
        background=True,
    ))

    registry.register(Tool(
        name="list_layouts",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_list_layouts,
    ))

    registry.register(Tool(
        name="export_layout",
        input_schema={
            "type": "object",
            "properties": {
                "layout_name": {"type": "string"},
                "output_path": {"type": "string"},
                "format": {
                    "type": "string",
                    "enum": ["pdf", "png", "jpg", "tif", "svg"],
                },
                "dpi": {"type": "integer", "minimum": 10, "maximum": 600},
                "meters_per_pixel": {"type": "number", "exclusiveMinimum": 0},
                "overwrite": {
                    "type": "boolean",
                },
                "georeference": {
                    "type": "boolean",
                },
                "force_vector": {
                    "type": "boolean",
                },
                "create_folder": {"type": "boolean"},
                "scale": {"type": "number", "minimum": 1, "maximum": 1000000000},
            },
            "required": ["layout_name", "output_path"],
        },
        handler=_export_layout,
    ))

    registry.register(Tool(
        name="get_message_log",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 5000},
                "tag": {
                    "type": "string",
                },
                "level": {
                    "type": "string",
                    "enum": ["info", "warning", "critical"],
                },
                "search": {"type": "string"},
                "max_message_chars": {
                    "type": "integer",
                    "minimum": 40,
                    "maximum": 12000,
                },
            },
            "required": [],
        },
        handler=_get_message_log,
    ))

    registry.register(Tool(
        name="get_debug_info",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_get_debug_info,
    ))

    _connect_message_log()
