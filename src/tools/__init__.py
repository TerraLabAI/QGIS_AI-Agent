# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




















from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
from types import ModuleType

from ..core.logger import log, log_warning
from ..core.tool_registry import ToolRegistry

_REGISTER = re.compile(r"register_\w+_tools")
_COMPLETE = re.compile(r"complete_\w+_tools")


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


def _entry_points(module: ModuleType, pattern: re.Pattern) -> list:
    return [fn for name, fn in sorted(vars(module).items())
            if pattern.fullmatch(name) and inspect.isfunction(fn) and fn.__module__ == module.__name__]


def build_registry(include_debug: bool = True) -> ToolRegistry:
    registry = ToolRegistry()
    modules = _tool_modules(include_debug)
    for pattern in (_REGISTER, _COMPLETE):
        for module in modules:
            for step in _entry_points(module, pattern):
                step(registry)
    log(f"Tool catalog ready: {registry.tool_count} tools, {len(registry.visible_names())} visible")
    return registry
