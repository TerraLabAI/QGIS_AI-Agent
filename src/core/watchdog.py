# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

































from __future__ import annotations

import os
import sys
import threading
import time

from . import limits, tuning
from .freeze_stack import FreezeStack, main_thread_frames_in
from .logger import log_warning








STALL_REPORT_S = 8.0

STALL_REPEAT_S = 30.0
STALL_FRAMES = 3


def main_thread_frames(ident: int | None, limit: int | None = None) -> str:





    if ident is None:
        return ""
    try:
        frame = sys._current_frames().get(ident)
    except Exception:  # noqa: BLE001
        return ""
    if limit is None:
        limit = tuning.threshold("watchdog_stall_frames", STALL_FRAMES, 1, 10)
    names = []
    while frame is not None and len(names) < limit:
        code = frame.f_code
        names.append(f"{os.path.basename(code.co_filename)}:{code.co_name}")
        frame = frame.f_back
    return " < ".join(names)


class MainThreadWatchdog:








    def __init__(self, describe=None, on_blocked=None, on_tick=None, tick_s: float | None = None,
                 blocked_s: float | None = None, on_stall=None, on_thaw=None):
        self._describe = describe
        self._on_blocked = on_blocked


        self._on_stall = on_stall



        self._on_thaw = on_thaw


        self._reported_for: float | None = None
        self._reported_where = ""
        self._main_ident: int | None = None
        self._helper: threading.Thread | None = None
        self._helper_stop = threading.Event()




        self._on_tick = on_tick



        self._tick_given = tick_s
        self._blocked_given = blocked_s
        self._timer = None
        self._last = time.monotonic()


        self._stack = FreezeStack() if on_stall is not None else None

        self.blocked_for: float = 0.0
        self.blocked_by: str = ""

    def _seconds(self, given, name: str) -> float:
        if given is not None:
            return float(given)
        try:
            return float(limits.current(name))
        except Exception:  # noqa: BLE001
            return float(getattr(limits, name))

    def tick_s(self) -> float:

        return self._seconds(self._tick_given, "WATCHDOG_TICK_S")

    def blocked_s(self) -> float:

        return self._seconds(self._blocked_given, "WATCHDOG_BLOCKED_S")



    def start(self) -> bool:

        if self._timer is not None:
            return True
        try:
            from qgis.PyQt.QtCore import QTimer

            timer = QTimer()
            timer.setInterval(int(self.tick_s() * 1000))
            timer.timeout.connect(self.tick)
            timer.start()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Main-thread watchdog not started: {exc}")
            return False
        self._timer = timer
        self._last = time.monotonic()
        if self._on_stall is not None:

            self._main_ident = threading.get_ident()

            self._helper_stop = threading.Event()
            self._helper = threading.Thread(target=self._watch, args=(self._helper_stop,),
                                            name="ai-agent-stall-watch", daemon=True)
            self._helper.start()
        return True

    @staticmethod
    def _stall_report_s() -> float:
        return tuning.ceiling("watchdog_stall_report_s", STALL_REPORT_S, 6.0)

    def _held_in(self, gap: float) -> tuple[bool, str]:


        if self._stack is None:
            return False, ""
        dump = self._stack.take()
        if not dump or gap < self._stall_report_s():
            return False, ""
        limit = tuning.threshold("watchdog_stall_frames", STALL_FRAMES, 1, 10)
        return True, main_thread_frames_in(dump, self._main_ident, limit)

    def _watch(self, stop: threading.Event) -> None:

        reported_for = None
        reported_at = 0.0
        while not stop.wait(1.0):
            last = self._last
            held = time.monotonic() - last
            if held < self._stall_report_s():
                continue
            if reported_for == last and held - reported_at < tuning.ceiling(
                    "watchdog_stall_repeat_s", STALL_REPEAT_S, 10.0):
                continue
            reported_for, reported_at = last, held
            where = main_thread_frames(self._main_ident)
            self._reported_where = where
            self._reported_for = last
            try:
                self._on_stall(held, where)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Stall report failed: {exc}")

    def stop(self) -> None:
        self._helper_stop.set()
        self._helper = None
        if self._stack is not None:

            self._stack.close()
        timer, self._timer = self._timer, None
        if timer is None:
            return
        try:
            timer.stop()
            timer.timeout.disconnect(self.tick)
            timer.deleteLater()
        except Exception:  # nosec B110
            pass



    def tick(self, now: float | None = None) -> float:






        now = time.monotonic() if now is None else float(now)
        previous = self._last
        gap = now - previous
        self._last = now


        dumped, held_in = self._held_in(gap)
        if self._stack is not None and self._timer is not None:
            self._stack.arm(self._stall_report_s())
        reported = self._reported_for is not None and self._reported_for == previous
        if dumped and (not reported or (held_in and held_in != self._reported_where)) and self._on_stall is not None:




            try:
                self._on_stall(gap, held_in)
                reported = True
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Stall report failed: {exc}")
        if reported:

            self._reported_for = None
            if self._on_thaw is not None:
                try:
                    self._on_thaw(gap)
                except Exception as exc:  # noqa: BLE001
                    log_warning(f"Thaw report failed: {exc}")
        timer = self._timer
        if timer is not None:


            try:
                interval = int(self.tick_s() * 1000)
                if timer.interval() != interval:
                    timer.setInterval(interval)
            except Exception:  # nosec B110
                pass
        if self._on_tick is not None:
            try:
                self._on_tick(now)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Watchdog tick failed: {exc}")
        if gap <= self.blocked_s():
            return gap
        try:
            who = str(self._describe() or "") if self._describe is not None else ""
        except Exception:  # noqa: BLE001
            who = ""
        self.blocked_for = max(self.blocked_for, gap)
        self.blocked_by = who or self.blocked_by


        log_warning(f"Main thread blocked for {gap:.1f}s"
                    + (f" in {held_in}" if held_in else (" in native code" if dumped else ""))
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

        self.blocked_for = 0.0
        self.blocked_by = ""
        self._last = time.monotonic()
