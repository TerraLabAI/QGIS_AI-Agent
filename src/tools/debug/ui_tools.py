# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native Qt UI tools for visual QGIS plugin debugging."""




from __future__ import annotations

from qgis.utils import active_plugins

from ...core.tool_registry import Tool, ToolRegistry
from .snapshot import _accessibility_snapshot
from .widgets import (  # noqa: F401 - kept for backward-compatible imports
    AI_EDIT_KEYS,
    AI_SEGMENT_KEYS,
)
from .widgets import (
    find_plugin as _find_plugin,
)
from .widgets import (
    grab_widget as _grab_widget,
)
from .widgets import (
    plugin_widgets as _plugin_widgets,
)
from .widgets import (
    process_events as _process_events,
)
from .widgets import (
    resolve_widget as _resolve_widget,
)
from .widgets import (
    widget_ident as _widget_ident,
)
from .widgets import (
    widget_summary as _widget_summary,
)


def register_ui_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="take_qgis_window_screenshot",
        input_schema={
            "type": "object",
            "properties": {
                "max_width": {
                    "type": "integer",
                },
                "format": {
                    "type": "string",
                    "enum": ["png", "jpeg"],
                },
                "quality": {"type": "integer"},
                "save_path": {
                    "type": "string",
                },
                "overwrite": {"type": "boolean"},
            },
            "required": [],
        },
        handler=_take_qgis_window_screenshot,
    ))

    registry.register(Tool(
        name="take_widget_screenshot",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "occurrence": {
                    "type": "integer",
                },
                "max_width": {
                    "type": "integer",
                },
                "format": {
                    "type": "string",
                    "enum": ["png", "jpeg"],
                },
                "quality": {"type": "integer"},
                "save_path": {
                    "type": "string",
                },
                "verbose": {
                    "type": "boolean",
                },
                "overwrite": {"type": "boolean"},
            },
            "required": ["query"],
        },
        handler=_take_widget_screenshot,
    ))

    registry.register(Tool(
        name="open_plugin_panel",
        input_schema={
            "type": "object",
            "properties": {
                "plugin_name": {"type": "string"},
                "mode": {"type": "string", "enum": ["show", "launch"]},
            },
            "required": ["plugin_name"],
        },
        handler=_open_plugin_panel,
    ))


def _capture_format(args: dict) -> tuple[str, int]:
    return args.get("format", "png"), min(max(int(args.get("quality", 95) or 95), 10), 100)


def _take_qgis_window_screenshot(args: dict) -> dict:
    from qgis.utils import iface

    from ...core import limits






    max_width = min(max(int(args.get("max_width", 1600) or 1600), 200), limits.MAX_RENDER_WIDTH_PX)
    main_window = iface.mainWindow()
    if main_window is None:
        return {"_error": "QGIS main window not available"}
    fmt, quality = _capture_format(args)



    canvas = iface.mapCanvas()
    if canvas is not None and hasattr(canvas, "waitWhileRendering"):
        canvas.waitWhileRendering()
    result = _grab_widget(main_window, max_width, args.get("save_path", ""), fmt, quality)
    if "_error" not in result:
        result["widget"] = _widget_ident(main_window)
        result["capture"] = "qgis_main_window"
    return result


def _take_widget_screenshot(args: dict) -> dict:
    max_width = min(max(int(args.get("max_width", 1600) or 1600), 100), 4000)
    widget, error = _resolve_widget(args)
    if error:
        return error
    fmt, quality = _capture_format(args)
    result = _grab_widget(widget, max_width, args.get("save_path", ""), fmt, quality)
    if "_error" not in result:
        result["widget"] = _widget_summary(widget) if args.get("verbose") else _widget_ident(widget)
        result["capture"] = "widget"
    return result


def _open_plugin_panel(args: dict) -> dict:
    key, plugin = _find_plugin(args["plugin_name"])
    if plugin is None:
        return {
            "_error": f"Plugin not loaded: {args['plugin_name']}",
            "active_plugins": list(active_plugins or []),
        }

    mode = args.get("mode", "show")
    opened = []
    for widget in _plugin_widgets(plugin):
        widget.show()
        widget.raise_()
        opened.append(_widget_ident(widget))
    if mode == "launch":
        launch = getattr(plugin, "_on_launch_clicked", None)
        if callable(launch):
            launch()
        elif hasattr(plugin, "_action") and plugin._action is not None:
            plugin._action.trigger()

    _process_events()
    return {
        "plugin": key,
        "mode": mode,
        "opened_widgets": opened,




        "state": _accessibility_snapshot({"plugin_name": key}),
    }
