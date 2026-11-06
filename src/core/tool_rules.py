# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

from . import tuning


def section(name: str) -> dict:

    value = (tuning.service_doc("tool_rules") or {}).get(name)
    return value if isinstance(value, dict) else {}


def names(name: str, key: str) -> tuple:

    value = section(name).get(key)
    return tuple(value) if isinstance(value, list) else ()


def number(name: str, key: str):

    value = section(name).get(key)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
