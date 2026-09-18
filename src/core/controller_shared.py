# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Constants and module-level helpers shared by the controller and its mixins."""





from __future__ import annotations

import json
import os
import re

from qgis.PyQt.QtCore import QCoreApplication

from .logger import log, log_warning

CANCEL_GRACE_MS = 5_000
MAX_AGENT_TEXT = 200_000



RUN_PROJECTS_KEPT = 16



RUN_SILENCE_S = 600



BUSY_RESENDS = 4
BUSY_RESEND_MS = 4_000


RESUME_GRACE_S = 120




SENDING_RECHECK_MS = 5_000


RETRY_MEMORY = 8




RESUME_OUTCOME_MS = 5_000





_DIFF_SETTLE_MS = 250
PROPOSAL_TOOLS = ("propose_action", "propose_edits")






CONTINUE_TEXT = "continue"
PROPOSAL_MAX_ROWS = 500



_FOLLOWUPS_RE = re.compile(r"(?:^|\n)[ \t]*(?:[-*]\s*)?Follow-ups?\s*:\s*(?P<items>[^\n]*)\s*$", re.IGNORECASE)


def strip_followups(text: str) -> str:
    """The answer without a trailing Follow-ups line an older server sent."""
    text = text or ""
    match = _FOLLOWUPS_RE.search(text.rstrip())
    if match is None:
        return text
    return text.rstrip()[: match.start()].rstrip()


def _dump_context(context: dict, run_id: str) -> None:
    """The context of this message, written next to the run id."""





    folder = os.environ.get("AI_AGENT_DUMP_CONTEXT") or ""
    if not folder:
        return
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{run_id}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(context, handle, ensure_ascii=False, indent=1, default=str)
        log(f"Context dumped to {path}")
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Context dump failed: {exc}")


def tr(text: str) -> str:
    return QCoreApplication.translate("AgentController", text)


__all__ = [
    "BUSY_RESENDS",
    "BUSY_RESEND_MS",
    "CANCEL_GRACE_MS",
    "CONTINUE_TEXT",
    "MAX_AGENT_TEXT",
    "PROPOSAL_MAX_ROWS",
    "PROPOSAL_TOOLS",
    "RESUME_GRACE_S",
    "RESUME_OUTCOME_MS",
    "RETRY_MEMORY",
    "RUN_PROJECTS_KEPT",
    "RUN_SILENCE_S",
    "SENDING_RECHECK_MS",
    "_DIFF_SETTLE_MS",
    "_dump_context",
    "strip_followups",
    "tr",
]
