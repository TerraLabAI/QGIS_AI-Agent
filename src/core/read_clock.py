# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

import time

from . import limits



SETTLE_S = 15.0



SLACK = 1.1

AFTER_S = 20.0


class ReadClock:


    def __init__(self, share: float = 1.0):
        self.started = time.monotonic()
        self.allowed = max(SETTLE_S * 2, limits.current("CALL_MAX_SECONDS_BACKGROUND") * share - AFTER_S)

    def spent(self) -> float:
        return time.monotonic() - self.started

    def left(self) -> float:
        return max(0.0, self.allowed - self.spent())

    def needs(self, done: float | None) -> float | None:


        spent = self.spent()
        if done is None or done <= 0 or spent < SETTLE_S:
            return None
        return spent / min(float(done), 1.0)

    def over(self, done: float | None = None) -> bool:

        if self.spent() >= self.allowed:
            return True
        needed = self.needs(done)
        return needed is not None and needed > self.allowed * SLACK
