# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





from __future__ import annotations

from ...core import siblings
from .base import PluginAdapter


class AiEditAdapter(PluginAdapter):
    keys = siblings.AI_EDIT.folders
    aliases = ("ai-edit", "aiedit", "ai edit")

    def display_name(self) -> str:
        return siblings.AI_EDIT.label


_ADAPTER = AiEditAdapter()
