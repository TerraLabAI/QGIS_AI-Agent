# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Notice that the Qt main thread stopped answering, and name what was holding it."""






















from __future__ import annotations

import time

from . import limits
from .logger import log_warning


class MainThreadWatchdog:
    """Ticks on the event loop; reports the gaps where it did not."""







    def __init__(self, describe=None, on_blocked=None, on_tick=None, tick_s: float | None = None,
                 blocked_s: float | None = None):
        self._describe = describe
        self._on_blocked = on_blocked




        self._on_tick = on_tick



        self._tick_given = tick_s
        self._blocked_given = blocked_s
        self._timer = None
        self._last = time.monotonic()

        self.blocked_for: float = 0.0
        self.blocked_by: str = ""

    def _seconds(self, given, name: str) -> float:
        if given is not None:
            return float(given)
        try:
            return float(limits.current(name))
        except Exception:  # noqa: BLE001 - a slot never throws into Qt; the shipped number stands
            return float(getattr(limits, name))

    def tick_s(self) -> float:
        """How often the timer fires, in seconds: the row's lower number or the shipped one."""
        return self._seconds(self._tick_given, "WATCHDOG_TICK_S")

    def blocked_s(self) -> float:
        """The gap that counts as a freeze, in seconds, read the same way."""
        return self._seconds(self._blocked_given, "WATCHDOG_BLOCKED_S")



    def start(self) -> bool:
        """Arm the timer. False when there is no Qt to arm it with (unit tests)."""
        if self._timer is not None:
            return True
        try:
            from qgis.PyQt.QtCore import QTimer

            timer = QTimer()
            timer.setInterval(int(self.tick_s() * 1000))
            timer.timeout.connect(self.tick)
            timer.start()
        except Exception as exc:  # noqa: BLE001 - no timer means no watchdog, never no plugin
            log_warning(f"Main-thread watchdog not started: {exc}")
            return False
        self._timer = timer
        self._last = time.monotonic()
        return True

    def stop(self) -> None:
        timer, self._timer = self._timer, None
        if timer is None:
            return
        try:
            timer.stop()
            timer.timeout.disconnect(self.tick)
            timer.deleteLater()
        except Exception:  # nosec B110 - a timer already torn down by Qt is not an error
            pass



    def tick(self, now: float | None = None) -> float:
        """One tick."""





        now = time.monotonic() if now is None else float(now)
        gap = now - self._last
        self._last = now
        timer = self._timer
        if timer is not None:


            try:
                interval = int(self.tick_s() * 1000)
                if timer.interval() != interval:
                    timer.setInterval(interval)
            except Exception:  # nosec B110 - a timer Qt already tore down keeps its interval
                pass
        if self._on_tick is not None:
            try:
                self._on_tick(now)
            except Exception as exc:  # noqa: BLE001 - a slot never throws into Qt
                log_warning(f"Watchdog tick failed: {exc}")
        if gap <= self.blocked_s():
            return gap
        try:
            who = str(self._describe() or "") if self._describe is not None else ""
        except Exception:  # noqa: BLE001 - a broken describe must not silence the report
            who = ""
        self.blocked_for = max(self.blocked_for, gap)
        self.blocked_by = who or self.blocked_by


        log_warning(f"Main thread blocked for {gap:.1f}s"
                    + (f" during {who}. QGIS answered nothing for that long; the next tool call of "
                       "this run is refused." if who
                       else " with no tool call in flight. QGIS answered nothing for that long; "
                            "nothing is refused."))
        if self._on_blocked is not None:
            try:
                self._on_blocked(gap, who)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Watchdog report failed: {exc}")
        return gap



    def is_blocked(self) -> bool:
        return self.blocked_for > 0.0

    def clear(self) -> None:
        """Forget the freeze. Called once the model has been told about it."""
        self.blocked_for = 0.0
        self.blocked_by = ""
        self._last = time.monotonic()
