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


def module_names(include_debug: bool = True) -> list[str]:

    packages = [__name__, f"{__name__}.adapters"] + ([f"{__name__}.debug"] if include_debug else [])
    names = []
    for package in packages:
        try:
            path = importlib.import_module(package).__path__
        except ImportError as e:
            log_warning(f"Tools not available from {package}: {e}")
            continue
        names.extend(f"{package}.{name}" for name in sorted(info.name for info in pkgutil.iter_modules(path)
                                                         if not info.ispkg))
    return names


def _tool_modules(include_debug: bool) -> list[ModuleType]:
    return [importlib.import_module(name) for name in module_names(include_debug)]


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
