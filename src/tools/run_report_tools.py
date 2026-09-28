# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

from ..core.tool_registry import Tool, ToolRegistry, tool_error


def _outside_a_run(args: dict) -> dict:

    return tool_error(
        "verify_run is answered by the executor inside a run.",
        "INVALID_ARGS",
        "The server asks for it itself, before the final answer.",
    )


def register_run_report_tools(registry: ToolRegistry) -> None:




    registry.register(Tool(
        name="verify_run",
        danger="read",
        input_schema={"type": "object", "properties": {}},
        handler=_outside_a_run,
    ))
