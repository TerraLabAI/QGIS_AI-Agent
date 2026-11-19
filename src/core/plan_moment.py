# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations


MOMENTS = ("payment_failed", "trial_running", "pro", "out", "trial", "return", "none")


def trial_days(raw) -> int | None:

    if raw is None or isinstance(raw, bool):
        return None
    try:
        days = int(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return days if 0 < days <= 31 else None


def offer_kind(usage: dict, subscriber: bool) -> str:


    usage = usage if isinstance(usage, dict) else {}
    if subscriber:
        return "none"
    if trial_days(usage.get("trial_days_available")):
        return "trial"
    return "return" if usage.get("offer_kind") == "return" else "none"


def is_low(left: int | None, limit: int | None) -> bool:

    if left is None or not limit:
        return False
    return left <= 1 or left * 10 <= limit


def moment(offer: str, subscriber: bool, left: int | None, limit: int | None,
           payment_failed: bool = False, trial_ends_on: str | None = None) -> str:

    if payment_failed:
        return "payment_failed"
    if subscriber:
        return "trial_running" if trial_ends_on else "pro"
    if is_low(left, limit):
        return "out"
    return offer if offer in ("trial", "return") else "none"
