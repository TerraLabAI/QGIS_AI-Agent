# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The QGIS tool catalog: ``build_registry()`` returns it ready to execute."""















from __future__ import annotations

import importlib

from ..core.logger import log, log_warning
from ..core.tool_registry import ToolRegistry
from .danger import DANGER, DEFAULT_DANGER


def build_registry(include_debug: bool = True, include_dev: bool = False) -> ToolRegistry:
    from .adapters.ai_edit import register_ai_edit_adapter_tools
    from .advanced_tools import register_advanced_tools
    from .aiseg_install_tools import register_aiseg_install_tools
    from .core_tools import register_core_tools
    from .data_tools import register_data_tools
    from .deps_tools import register_deps_tools
    from .earthengine_tools import register_earthengine_tools
    from .edit_tools import register_edit_tools
    from .facade import register_facade_tools
    from .feature_tools import register_feature_tools
    from .gis_case_tools import register_gis_case_tools
    from .grid_tools import register_grid_tools
    from .hydrology_tools import register_hydrology_tools
    from .integration_tools import register_integration_tools
    from .layer_tools import register_layer_tools
    from .layout_tools import register_layout_tools
    from .memory_tools import register_memory_tools
    from .plugin_tools import register_plugin_tools
    from .stac_tools import register_stac_tools
    from .statistics_tools import register_statistics_tools
    from .web_tools import register_web_tools

    registry = ToolRegistry()
    register_core_tools(registry)
    register_feature_tools(registry)
    register_edit_tools(registry)
    register_layer_tools(registry)
    register_layout_tools(registry)
    register_advanced_tools(registry)
    register_integration_tools(registry)
    register_aiseg_install_tools(registry)
    register_data_tools(registry)
    register_web_tools(registry)
    register_stac_tools(registry)
    register_earthengine_tools(registry)
    register_deps_tools(registry)
    register_plugin_tools(registry)
    register_gis_case_tools(registry)
    register_hydrology_tools(registry)
    register_statistics_tools(registry)
    register_grid_tools(registry)
    register_memory_tools(registry)
    register_ai_edit_adapter_tools(registry, include_dev)
    importlib.import_module(".harvest_analysis", __name__).register_harvest_analysis_tools(registry)
    importlib.import_module(".harvest_layers", __name__).register_harvest_layers_tools(registry)
    importlib.import_module(".harvest_project", __name__).register_harvest_project_tools(registry)
    importlib.import_module(".harvest_layout", __name__).register_harvest_layout_tools(registry)
    importlib.import_module(".harvest_models", __name__).register_harvest_models_tools(registry)
    importlib.import_module(".harvest_canvas", __name__).register_harvest_canvas_tools(registry)
    importlib.import_module(".harvest_view", __name__).register_harvest_view_tools(registry)
    importlib.import_module(".harvest_remote", __name__).register_harvest_remote_tools(registry)
    importlib.import_module(".harvest_local", __name__).register_harvest_local_tools(registry)
    importlib.import_module(".diagnostics_tools", __name__).register_diagnostics_tools(registry)

    if include_debug:
        try:
            debug = importlib.import_module(".debug", __name__)
        except ImportError as e:
            log_warning(f"Debug tools not available: {e}")
        else:
            debug.register_debug_tools(registry)


    register_facade_tools(registry)

    apply_danger(registry)
    log(f"Tool catalog ready: {registry.tool_count} tools, {len(registry.visible_names())} visible")
    return registry


def apply_danger(registry: ToolRegistry) -> list[str]:
    """Set every tool's level from DANGER. Returns the names that fell back to the default."""
    fallback = []
    for name in registry.tool_names:
        tool = registry.get_tool(name)
        level = DANGER.get(name)
        if level is None:
            level = DEFAULT_DANGER
            fallback.append(name)
        tool.danger = level
    if fallback:
        log_warning(f"Tools without a danger level, defaulting to {DEFAULT_DANGER}: {', '.join(sorted(fallback))}")
    return sorted(fallback)
