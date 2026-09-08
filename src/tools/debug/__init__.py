# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generic QGIS plugin debugging subsystem."""





from __future__ import annotations


def register_debug_tools(registry):
    """Register all generic plugin-debug tools."""



    from .canvas import register_canvas_tools
    from .context import register_context_tools
    from .errors import install_error_capture, register_error_tools
    from .fixtures import register_fixture_tools
    from .network import install_network_logger, register_network_tools
    from .snapshot import register_snapshot_tools
    from .ui_tools import register_ui_tools

    register_snapshot_tools(registry)
    register_fixture_tools(registry)
    register_context_tools(registry)
    register_canvas_tools(registry)
    register_network_tools(registry)
    register_error_tools(registry)
    register_ui_tools(registry)
    install_network_logger()
    install_error_capture()
