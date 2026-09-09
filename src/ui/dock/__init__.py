# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The dock package: ``AIAgentDock`` plus the TerraLab surfaces the dock shares with its sibling plugins (sign-in card, update card, footer."""








from __future__ import annotations

__all__ = ["AIAgentDock", "DOCK_OBJECT_NAME"]


def __getattr__(name: str):
    if name in __all__:
        from . import widget

        return getattr(widget, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
