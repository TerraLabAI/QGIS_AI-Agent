# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pure account helpers the settings dialog renders its plan line from."""





from __future__ import annotations

from typing import NamedTuple

from qgis.PyQt.QtCore import QLocale

_ACCOUNT_OFFLINE_CODES = frozenset({
    "NO_INTERNET", "DNS_ERROR", "CONNECTION_REFUSED", "PROXY_ERROR",
    "TIMEOUT", "SSL_ERROR",
})


class PlanRuns(NamedTuple):
    """What the plan card draws: who the user is and what is spent."""

    is_subscriber: bool
    used: int | None
    limit: int | None
    reset_iso: str | None


def _as_int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def resolve_plan_runs(usage: dict, sub: dict) -> PlanRuns:
    """Read the newer usage names first, the account row second."""
    usage = usage if isinstance(usage, dict) else {}
    sub = sub if isinstance(sub, dict) else {}
    if "is_subscriber" in usage:
        is_subscriber = bool(usage["is_subscriber"])
    elif "is_free_tier" in usage:
        is_subscriber = not bool(usage["is_free_tier"])
    else:
        is_subscriber = str(sub.get("plan", "")).lower() == "pro"
    used = _as_int(usage.get("runs_used"))
    if used is None:
        used = _as_int(sub.get("usage_this_month"))
    limit = _as_int(usage.get("runs_limit"))
    if limit is None:
        limit = _as_int(sub.get("quota_limit"))
    reset = (usage.get("period_end") or usage.get("reset_date")
             or sub.get("current_period_end"))
    return PlanRuns(is_subscriber, used, limit, str(reset) if reset else None)


def format_count(value) -> str:
    try:
        return QLocale().toString(int(value or 0))
    except (TypeError, ValueError):
        return "0"
