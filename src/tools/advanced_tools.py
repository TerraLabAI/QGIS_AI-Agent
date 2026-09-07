# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
from __future__ import annotations

from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core import code_effects
from ..core.tool_registry import Tool, ToolRegistry
from . import isolated_code, raster_overviews
from .advanced_code import (  # noqa: F401
    _api_help,
    _execute_code,
)
from .advanced_debug import (  # noqa: F401
    _connect_message_log,
    _get_debug_info,
    _get_message_log,
)
from .advanced_layouts import (  # noqa: F401
    _IMAGE_FORMATS,
    _apply_scale,
    _dpi_for_ground,
    _export_layout,
    _ground_per_pixel,
    _list_layouts,
)
from .advanced_render import (  # noqa: F401
    _apply_quality_flags,
    _prepare_frame_folder,
    _render_map,
    _safe_frame_prefix,
)


def _code_only_reads(args: dict) -> bool:



    return code_effects.classify(args.get("code")).cls == code_effects.R


def _export_layout_label(args: dict) -> str:

    if (str(args.get("format") or "").strip().lower() == "qpt"
            or str(args.get("output_path") or "").strip().lower().endswith(".qpt")):
        return QT_TRANSLATE_NOOP("AIAgent", "Save the layout as a template")
    return ""


def register_advanced_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="execute_code",
        danger="destructive",
        visible=7,
        label=QT_TRANSLATE_NOOP("AIAgent", "Run Python code[: {description}]"),
        input_schema={
            "type": "object",


            "x-file-modules": True,


            "x-code-classes": True,



            "x-workspace": True,



            "x-read-scope": True,
            "properties": {
                "code": {
                    "type": "string",
                },
            },
            "required": ["code"],
        },
        handler=_execute_code,
        background=isolated_code.runs_in_task,
        reads_when=_code_only_reads,
    ))

    registry.register(Tool(
        name="render_map",
        danger="read",
        visible=8,
        label=QT_TRANSLATE_NOOP("AIAgent", "Render the map"),
        input_schema={
            "type": "object",



            "x-view-size": True,
            "properties": {



                "target": {
                    "type": "string",
                    "enum": ["canvas", "layout", "file"],
                },
                "layout_name": {"type": "string"},
                "path": {"type": "string"},
                "page": {"type": "integer", "minimum": 1, "maximum": 999},



                "crop": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number", "minimum": 0, "maximum": 1},
                        "y": {"type": "number", "minimum": 0, "maximum": 1},
                        "width": {"type": "number", "minimum": 0, "maximum": 1},
                        "height": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["x", "y", "width", "height"],
                },
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


        prepare=raster_overviews.prepare_render,
    ))






    registry.register(Tool(
        name="list_layouts",
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "List the layouts"),
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_list_layouts,
    ))

    registry.register(Tool(
        name="export_layout",



        danger="write",
        label_for=_export_layout_label,
        builds_layout_at="layout_name",
        label=QT_TRANSLATE_NOOP("AIAgent", "Export the layout"),
        input_schema={
            "type": "object",


            "x-dpi-at-ceiling": True,
            "properties": {
                "layout_name": {"type": "string"},
                "output_path": {"type": "string"},
                "format": {
                    "type": "string",
                    "enum": ["pdf", "png", "jpg", "tif", "svg", "qpt"],
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



            "x-builds-overviews": True,
        },
        handler=_export_layout,
        replaces_file_at="output_path",
        prepare=raster_overviews.prepare_layout,
    ))

    registry.register(Tool(
        name="get_message_log",
        danger="read",
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
        danger="read",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_get_debug_info,
    ))

    _connect_message_log()




__all__ = [
    "register_advanced_tools",
    "_IMAGE_FORMATS",
    "_api_help",
    "_apply_quality_flags",
    "_apply_scale",
    "_dpi_for_ground",
    "_ground_per_pixel",
    "_prepare_frame_folder",
    "_safe_frame_prefix",
]
