# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





from __future__ import annotations

from .._widgets import AI_EDIT_KEYS
from .base import PluginAdapter


class AiEditAdapter(PluginAdapter):
    keys = AI_EDIT_KEYS
    aliases = ("ai-edit", "aiedit", "ai edit")

    def display_name(self) -> str:
        return "AI Edit"


_ADAPTER = AiEditAdapter()
