# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real mouse interactions on the QGIS map canvas (drag, click)."""






from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, QPoint, QPointF, Qt
from qgis.PyQt.QtGui import QMouseEvent
from qgis.PyQt.QtWidgets import QApplication
from qgis.utils import iface

from ...core.tool_registry import Tool, ToolRegistry


def register_canvas_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="drag_canvas",
        input_schema={
            "type": "object",
            "properties": {
                "x1": {"type": "number"},
                "y1": {"type": "number"},
                "x2": {"type": "number"},
                "y2": {"type": "number"},
                "steps": {"type": "integer"},
            },
            "required": [],
        },
        handler=_drag_canvas,
    ))

    registry.register(Tool(
        name="click_canvas",
        input_schema={
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "button": {"type": "string", "enum": ["left", "right"]},
            },
            "required": [],
        },
        handler=_click_canvas,
    ))


def _viewport():
    canvas = iface.mapCanvas()
    if canvas is None:
        return None, None
    return canvas, canvas.viewport()


def _frac_point(vp, x, y) -> QPoint:
    w, h = vp.width(), vp.height()
    px = min(max(x, 0.0), 1.0) * w
    py = min(max(y, 0.0), 1.0) * h
    return QPoint(int(px), int(py))


def _send(vp, etype, pos, button, buttons):
    """Send a QMouseEvent to the canvas viewport (Qt6/Qt5 constructor tolerant)."""





    glob = QPointF(vp.mapToGlobal(pos))
    try:
        event = QMouseEvent(
            etype, QPointF(pos), glob,
            button, buttons, Qt.KeyboardModifier.NoModifier,
        )
    except Exception:
        event = QMouseEvent(
            etype, QPointF(pos),
            button, buttons, Qt.KeyboardModifier.NoModifier,
        )
    QApplication.instance().sendEvent(vp, event)
    QApplication.instance().processEvents()


def _wait_render(canvas):
    """Block until the canvas finishes its current render (best effort)."""
    try:
        canvas.waitWhileRendering()
    except Exception:  # nosec B110 - the debug report reads what answers and skips what does not
        pass


def _extent_dict(canvas) -> dict:
    extent = canvas.extent()
    return {
        "xmin": extent.xMinimum(), "ymin": extent.yMinimum(),
        "xmax": extent.xMaximum(), "ymax": extent.yMaximum(),
    }


def _drag_canvas(args: dict) -> dict:
    canvas, vp = _viewport()
    if vp is None:
        return {"_error": "Map canvas not available"}
    x1 = float(args.get("x1", 0.35))
    y1 = float(args.get("y1", 0.35))
    x2 = float(args.get("x2", 0.65))
    y2 = float(args.get("y2", 0.65))
    steps = min(max(int(args.get("steps", 14) or 14), 2), 60)

    p1 = _frac_point(vp, x1, y1)
    p2 = _frac_point(vp, x2, y2)

    btn = Qt.MouseButton.LeftButton
    _send(vp, QEvent.Type.MouseButtonPress, p1, btn, btn)
    for i in range(1, steps + 1):
        xi = int(p1.x() + (p2.x() - p1.x()) * i / steps)
        yi = int(p1.y() + (p2.y() - p1.y()) * i / steps)

        _send(vp, QEvent.Type.MouseMove, QPoint(xi, yi), Qt.MouseButton.NoButton, btn)
    _send(vp, QEvent.Type.MouseButtonRelease, p2, btn, Qt.MouseButton.NoButton)

    _wait_render(canvas)
    return {
        "dragged": True,
        "from": {"x": x1, "y": y1},
        "to": {"x": x2, "y": y2},
        "viewport_px": {"width": vp.width(), "height": vp.height()},
        "canvas_extent": _extent_dict(canvas),
    }


def _click_canvas(args: dict) -> dict:
    canvas, vp = _viewport()
    if vp is None:
        return {"_error": "Map canvas not available"}
    x = float(args.get("x", 0.5))
    y = float(args.get("y", 0.5))
    button_name = str(args.get("button", "left")).lower()
    btn = Qt.MouseButton.RightButton if button_name == "right" else Qt.MouseButton.LeftButton
    p = _frac_point(vp, x, y)
    _send(vp, QEvent.Type.MouseButtonPress, p, btn, btn)
    _send(vp, QEvent.Type.MouseButtonRelease, p, btn, Qt.MouseButton.NoButton)
    _wait_render(canvas)
    return {"clicked": True, "at": {"x": x, "y": y}, "button": button_name,
            "canvas_extent": _extent_dict(canvas)}
