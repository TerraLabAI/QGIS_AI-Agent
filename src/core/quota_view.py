# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





















from __future__ import annotations

HIDDEN = "hidden"
PAYMENT_FAILED = "payment_failed"
PRO_WELCOME = "pro_welcome"
CHECKOUT_RETURN = "checkout_return"
EXHAUSTED = "exhausted"
QUIET = "quiet"
LOW_CARD = "low_card"
RUNS_LINE = "runs_line"



WARN_AT_RUNS_LEFT = 3


def runs_left(used: int, limit: int) -> int:
    return max(0, limit - used)


def is_low(left: int) -> bool:
    return left <= WARN_AT_RUNS_LEFT


def choose(*, signed_in: bool, used: int, limit: int, is_subscriber: bool, payfail: bool,
           pro_welcome: bool, checkout_return: bool, low_closed: bool, running: bool = False) -> str:

    if not signed_in or limit <= 0:
        return HIDDEN
    left = runs_left(used, limit)
    if running and left <= 0:
        return QUIET
    if not is_subscriber and payfail:
        return PAYMENT_FAILED
    if pro_welcome and is_subscriber and left > 0:
        return PRO_WELCOME
    if checkout_return and not is_subscriber:
        return CHECKOUT_RETURN
    if left <= 0:
        return EXHAUSTED
    if is_subscriber:
        return QUIET
    if is_low(left) and not low_closed:
        return LOW_CARD
    return RUNS_LINE
