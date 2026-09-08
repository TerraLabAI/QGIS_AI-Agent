# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generic reset/seed and flow dispatch to per-plugin adapters."""
from __future__ import annotations

from ...core.tool_registry import Tool, ToolRegistry
from ..adapters import get_adapter


def register_fixture_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="reset_plugin_fixture",
        input_schema={
            "type": "object",
            "properties": {
                "plugin_name": {"type": "string"},
                "op": {"type": "string", "enum": ["reset", "seed"]},
                "scenario": {"type": "string"},
            },
            "required": ["plugin_name"],
        },
        handler=_reset_plugin_fixture,
    ))

    registry.register(Tool(
        name="run_plugin_flow",
        input_schema={
            "type": "object",
            "properties": {
                "plugin_name": {"type": "string"},
                "flow": {"type": "string"},
                "params": {"type": "object"},
            },
            "required": ["plugin_name"],
        },
        handler=_run_plugin_flow,
    ))


def _reset_plugin_fixture(args: dict) -> dict:
    adapter = get_adapter(args["plugin_name"])
    if not adapter.is_loaded():
        return {"_error": f"Plugin not loaded: {args['plugin_name']}"}
    op = args.get("op", "reset")
    scenario = args.get("scenario")
    result = adapter.seed(scenario) if op == "seed" else adapter.reset(scenario)
    return {"op": op, "scenario": scenario, "result": result, "state": adapter.state()}


def _run_plugin_flow(args: dict) -> dict:
    adapter = get_adapter(args["plugin_name"])
    if not adapter.is_loaded():
        return {"_error": f"Plugin not loaded: {args['plugin_name']}"}
    flow = args.get("flow")
    if not flow:
        return {"available_flows": adapter.flows()}
    return adapter.run_flow(flow, args.get("params") or {})
