# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






















from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
import sys
from types import ModuleType

from ..core.logger import log, log_warning
from ..core.tool_registry import ToolRegistry

_ENTRY_POINT = re.compile(r"register_\w+_tools")


def _tool_modules(include_debug: bool) -> list[ModuleType]:
    packages = [__name__, f"{__name__}.adapters"] + ([f"{__name__}.debug"] if include_debug else [])
    modules = []
    for package in packages:
        try:
            path = importlib.import_module(package).__path__
        except ImportError as e:
            log_warning(f"Tools not available from {package}: {e}")
            continue
        names = sorted(info.name for info in pkgutil.iter_modules(path) if not info.ispkg)
        modules.extend(importlib.import_module(f"{package}.{name}") for name in names)
    return modules


def _entry_points(module: ModuleType) -> list:
    return [fn for name, fn in sorted(vars(module).items())
            if _ENTRY_POINT.fullmatch(name) and inspect.isfunction(fn) and fn.__module__ == module.__name__]


def build_registry(include_debug: bool = True, include_dev: bool = False) -> ToolRegistry:
    from .facade import wrap_run_processing
    from .native_processing_tools import add_native_processing_tools

    registry = ToolRegistry()
    for module in _tool_modules(include_debug):
        for register in _entry_points(module):
            if "include_dev" in inspect.signature(register).parameters:
                register(registry, include_dev=include_dev)
            else:
                register(registry)


    add_native_processing_tools(registry)
    wrap_run_processing(registry)
    if include_debug and f"{__name__}.debug" in sys.modules:
        sys.modules[f"{__name__}.debug"].install_capture()
    log(f"Tool catalog ready: {registry.tool_count} tools, {len(registry.visible_names())} visible")
    return registry
