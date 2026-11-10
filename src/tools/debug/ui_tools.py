# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





from __future__ import annotations

from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP
from qgis.utils import active_plugins

from ...core import limits
from ...core.qt_compat import one_shot
from ...core.tool_registry import Tool, ToolRegistry, tool_error
from .._widgets import find_plugin as _find_plugin
from .._widgets import plugin_widgets as _plugin_widgets
from .._widgets import process_events as _process_events
from .._widgets import widget_ident as _widget_ident
from ..adapters import get_adapter
from ..map_3d import open_3d_views
from .snapshot import _accessibility_snapshot
from .widgets import (
    grab_widget as _grab_widget,
)


def register_ui_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="take_qgis_window_screenshot",
        danger="read",
        render_size="held",
        writes_at=("save_path",),
        label=QT_TRANSLATE_NOOP("AIAgent", "Look at the QGIS window"),
        input_schema={
            "type": "object",


            "x-3d-views-blank": True,
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
        name="open_plugin_panel",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Open the {plugin_name} panel"),
        input_schema={
            "type": "object",
            "properties": {
                "plugin_name": {"type": "string"},
                "mode": {"type": "string", "enum": ["show", "launch"]},
            },
            "required": ["plugin_name"],
        },
        handler=_open_plugin_panel,

        always_confirm=lambda args: args.get("mode") == "launch",
    ))


def _capture_format(args: dict) -> tuple[str, int]:
    return args.get("format", "png"), min(max(int(args.get("quality", 95) or 95), 10), 100)


def _take_qgis_window_screenshot(args: dict) -> dict:
    from qgis.utils import iface








    max_width = min(max(int(args.get("max_width", 1280) or 1280), 200), limits.current("MAX_RENDER_WIDTH_PX"))
    main_window = iface.mainWindow()
    if main_window is None:
        return tool_error("QGIS main window not available")
    fmt, quality = _capture_format(args)







    canvas = iface.mapCanvas()
    still_drawing = canvas is not None and _wait_for_canvas(canvas)
    views_3d = {"not_drawn": []}
    picture, overlays = _window_picture(main_window, views_3d)
    result = _grab_widget(main_window, max_width, args.get("save_path", ""), fmt, quality, pixmap=picture)
    if "_error" not in result:
        result["widget"] = _widget_ident(main_window)
        result["capture"] = "qgis_main_window"
        if overlays:
            result["windows_on_top"] = overlays
        if views_3d["not_drawn"]:

            result["3d_views_not_in_picture"] = views_3d["not_drawn"]
        if still_drawing:
            result["canvas_still_drawing"] = True
    return result


_CANVAS_WAIT_MS = 5000


def _wait_for_canvas(canvas) -> bool:

    if not canvas.isDrawing():
        return False
    from qgis.PyQt.QtCore import QEventLoop

    loop = QEventLoop()
    timer = one_shot(None, loop.quit)
    canvas.mapCanvasRefreshed.connect(loop.quit)
    timer.start(_CANVAS_WAIT_MS)
    try:
        if canvas.isDrawing():
            QEventLoop.exec(loop)
    finally:
        timer.stop()
        canvas.mapCanvasRefreshed.disconnect(loop.quit)
    return canvas.isDrawing()


def _window_picture(main_window, views_3d):
















    from qgis.PyQt.QtCore import QPoint, QRect, Qt
    from qgis.PyQt.QtGui import QPainter
    from qgis.PyQt.QtWidgets import QApplication, QDialog

    pixmap = main_window.grab()
    origin = main_window.mapToGlobal(QPoint(0, 0))
    frame = QRect(origin, main_window.size())
    skipped = (Qt.WindowType.ToolTip, Qt.WindowType.Desktop, Qt.WindowType.SplashScreen)
    above = []
    for window in QApplication.topLevelWidgets():
        if (window is main_window or not window.isVisible() or window.isMinimized()
                or window.windowType() in skipped or window.width() <= 0 or window.height() <= 0):
            continue
        top_left = window.mapToGlobal(QPoint(0, 0))
        if not frame.intersects(QRect(top_left, window.size())):
            continue

        rank = (window.isActiveWindow(), window.isModal(), isinstance(window, QDialog))
        above.append((rank, window, top_left - origin))
    above.sort(key=lambda item: item[0])
    overlays = []
    views = _exposed_3d_views()
    _name_3d_views(main_window, views, views_3d)
    if above:
        painter = QPainter(pixmap)
        try:
            for _rank, window, offset in above:
                painter.drawPixmap(offset, window.grab())
                _name_3d_views(window, views, views_3d)
                overlays.append(window.windowTitle() or window.objectName() or window.metaObject().className())
        finally:
            painter.end()
    return pixmap, overlays


def _exposed_3d_views() -> list:


    return [(index, canvas) for index, canvas in enumerate(open_3d_views() or []) if canvas.isExposed()]


def _name_3d_views(host, views, views_3d) -> None:


    from qgis.utils import iface

    holder = host.windowHandle()
    for index, canvas in views:
        if holder is None or canvas.parent() is not holder:
            continue
        title = "" if host is iface.mainWindow() else host.windowTitle()
        views_3d["not_drawn"].append(f"{title} (view_index {index})" if title else f"view_index {index}")


def _open_plugin_panel(args: dict) -> dict:
    key, plugin = _find_plugin(args["plugin_name"])
    if plugin is None:
        return {
            **tool_error(f"Plugin not loaded: {args['plugin_name']}", "INVALID_ARGS"),
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
    out = {"plugin": key, "mode": mode, "opened_widgets": opened}

    if not opened and not get_adapter(key).snapshot_roots():



        out["panel"] = None
        out["note"] = ("This plugin has no panel or window QGIS can show from here: nothing opened. It works "
                       "through its menu, its toolbar buttons or its Processing algorithms.")
        return out




    out["state"] = _accessibility_snapshot(key)
    return out
