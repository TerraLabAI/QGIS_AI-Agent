# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

import os

SETTING = "TerraLab/AIAgent/tasks"


def on() -> bool:
    if os.environ.get("TERRALAB_AGENT_TASKS", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    try:
        from qgis.core import QgsSettings
    except Exception:  # noqa: BLE001
        return False
    return str(QgsSettings().value(SETTING, "false")).strip().lower() in ("1", "true", "yes", "on")
