# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



from __future__ import annotations

import time

from ..core.net_state import current_cancel_check


_STOP_POLL_S = 0.25


def stop_requested() -> bool:


    check = current_cancel_check()
    try:
        return bool(check()) if check is not None else False
    except Exception:  # noqa: BLE001
        return False


def wait_or_stop(done, budget: float) -> bool:






    deadline = time.monotonic() + budget
    while not done.is_set():
        if stop_requested():
            return True
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        done.wait(min(_STOP_POLL_S, left))
    return False
