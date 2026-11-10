# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

from . import tuning


def section(name: str) -> dict:

    return (tuning.service_doc("tool_rules") or {}).get(name) or {}


def names(name: str, key: str) -> tuple:

    return tuple(section(name).get(key) or ())


def number(name: str, key: str):

    return section(name).get(key)
