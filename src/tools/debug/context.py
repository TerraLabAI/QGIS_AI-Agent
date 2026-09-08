# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shot situational context bundle for autonomous plugin debugging."""
from __future__ import annotations

import time

from ...core.tool_registry import Tool, ToolRegistry
from ..adapters import get_adapter


def register_context_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="wait_for_log",
        input_schema={
            "type": "object",
            "properties": {
                "contains": {
                    "type": "string",
                },
                "tag": {"type": "string"},
                "level": {
                    "type": "string",
                    "enum": ["info", "warning", "critical"],
                },
                "timeout_ms": {"type": "integer"},
            },
            "required": [],
        },
        handler=_wait_for_log,
    ))

    registry.register(Tool(
        name="get_plugin_debug_context",
        input_schema={
            "type": "object",
            "properties": {
                "plugin_name": {"type": "string"},
                "include_snapshot": {
                    "type": "boolean",
                },
                "log_limit": {"type": "integer"},
                "max_nodes": {"type": "integer"},
            },
            "required": ["plugin_name"],
        },
        handler=_get_plugin_debug_context,
    ))


def _get_plugin_debug_context(args: dict) -> dict:
    from qgis.utils import active_plugins

    name = args["plugin_name"]
    adapter = get_adapter(name)
    log_limit = min(max(int(args.get("log_limit", 30) or 30), 1), 200)

    bundle = {
        "plugin": adapter.display_name(),
        "loaded": adapter.is_loaded(),
        "state": adapter.state(),
        "flows": adapter.flows(),
        "logs": _recent_logs(adapter, log_limit),
        "last_error": _last_error(),
        "network_errors": _recent_network_errors(),
        "active_plugins": list(active_plugins or []),
    }

    if not adapter.is_loaded():
        bundle["hint"] = "Plugin is not loaded. Use list_plugins to see available plugins."
        return bundle

    if args.get("include_snapshot", True):
        from .snapshot import _accessibility_snapshot

        max_nodes = min(max(int(args.get("max_nodes", 120) or 120), 1), 400)
        snap = _accessibility_snapshot({
            "plugin_name": name,
            "interactive_only": True,
            "max_nodes": max_nodes,
        })
        bundle["snapshot"] = snap.get("snapshot", [])
        bundle["snapshot_truncated"] = snap.get("truncated", False)
    return bundle


def _wait_for_log(args: dict) -> dict:
    try:
        from ..advanced_tools import _get_message_log
    except Exception:
        return {"_error": "message log unavailable"}

    contains = (args.get("contains") or "").lower()
    tag = (args.get("tag") or "").lower()
    level = args.get("level")
    timeout_ms = min(max(int(args.get("timeout_ms", 20000) or 20000), 200), 120000)
    deadline = time.monotonic() + timeout_ms / 1000.0

    severity = {"info": 0, "success": 0, "warning": 1, "critical": 2}
    min_sev = severity.get(level, 0)




    baseline = _get_message_log({"limit": 1}).get("buffer_size", 0)




    from qgis.PyQt.QtWidgets import QApplication
    app = QApplication.instance()

    while time.monotonic() < deadline:

        snap = _get_message_log({"limit": 1000, "max_message_chars": 0})
        buffer_size = snap.get("buffer_size", 0)
        new_count = max(0, buffer_size - baseline)

        for m in snap.get("messages", [])[:new_count]:
            if tag and str(m.get("tag", "")).lower() != tag:
                continue
            if severity.get(m.get("level", "info"), 0) < min_sev:
                continue
            if contains and contains not in str(m.get("message", "")).lower():
                continue
            elapsed = int((timeout_ms / 1000.0 - (deadline - time.monotonic())) * 1000)
            return {"matched": True, "message": m, "elapsed_ms": max(0, elapsed)}
        if app is not None:
            app.processEvents()
        time.sleep(0.2)
    return {"matched": False, "timeout_ms": timeout_ms}


def _last_error():
    try:
        from .errors import latest_error
        return latest_error()
    except Exception:
        return None


def _recent_network_errors(limit: int = 5) -> list[dict]:
    try:
        from .network import _get_network_log



        return _get_network_log({
            "only_errors": True,
            "limit": limit,
            "include_headers": False,
            "max_body_chars": 1500,
        }).get("requests", [])
    except Exception:
        return []


def _recent_logs(adapter, limit: int) -> list[dict]:
    """Recent QgsMessageLog entries, filtered to the adapter's tags when known."""
    try:
        from ..advanced_tools import _get_message_log
    except Exception:
        return []
    tags = [t.lower() for t in adapter.log_tags()]
    raw = _get_message_log({"limit": max(limit * 3, limit)})
    messages = raw.get("messages", []) if isinstance(raw, dict) else []
    if tags:
        messages = [m for m in messages if str(m.get("tag", "")).lower() in tags]
    return messages[:limit]
