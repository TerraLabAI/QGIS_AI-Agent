# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Python exception capture for autonomous debugging."""










from __future__ import annotations

import sys
import traceback as _tb
from collections import deque

from ...core.tool_registry import Tool, ToolRegistry

_BUF = "_aiagent_err_buf"
_PREV = "_aiagent_err_prev"
_INSTALLED = "_aiagent_err_installed"


def _app():
    from qgis.core import QgsApplication

    return QgsApplication.instance()


def _buf(app):
    b = app.property(_BUF)
    if b is None:
        b = deque(maxlen=1000)
        app.setProperty(_BUF, b)
    return b


def _hook(exc_type, exc, tb):
    try:
        app = _app()
        _buf(app).append({
            "type": getattr(exc_type, "__name__", str(exc_type)),
            "message": str(exc),
            "traceback": "".join(_tb.format_exception(exc_type, exc, tb)),
        })
        prev = app.property(_PREV)
    except Exception:
        prev = None
    if callable(prev):
        try:
            prev(exc_type, exc, tb)
        except Exception:  # nosec B110 - the debug report reads what answers and skips what does not
            pass


def install_error_capture() -> bool:
    """Chain sys.excepthook once. Safe to call repeatedly and across reloads."""
    try:
        app = _app()
        if app is None or app.property(_INSTALLED):
            return False
        app.setProperty(_PREV, sys.excepthook)
        sys.excepthook = _hook
        app.setProperty(_INSTALLED, True)
        return True
    except Exception:
        return False


def latest_error():
    """Most recent captured exception (or None), used by the context bundle."""
    try:
        app = _app()
        buf = app.property(_BUF)
        if buf:
            return buf[-1]
    except Exception:  # nosec B110 - the debug report reads what answers and skips what does not
        pass
    return None


def register_error_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="get_python_errors",
        input_schema={
            "type": "object",
            "properties": {
                "contains": {
                    "type": "string",
                },
                "limit": {
                    "type": "integer",
                },
            },
            "required": [],
        },
        handler=_get_python_errors,
    ))
    registry.register(Tool(
        name="clear_python_errors",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_clear_python_errors,
    ))


def _get_python_errors(args: dict) -> dict:
    install_error_capture()
    app = _app()
    errors = list(_buf(app))[::-1]
    contains = (args.get("contains") or "").lower()
    limit = min(max(int(args.get("limit", 10) or 10), 1), 1000)
    if contains:
        errors = [
            e for e in errors
            if contains in (e.get("message", "") + e.get("traceback", "")).lower()
        ]
    return {"errors": errors[:limit], "count": len(errors), "captured_total": len(_buf(app))}


def _clear_python_errors(args: dict) -> dict:
    _buf(_app()).clear()
    return {"cleared": True}
