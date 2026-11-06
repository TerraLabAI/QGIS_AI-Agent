# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





from __future__ import annotations

import ctypes
import threading
import time
from typing import Any, Callable


class CodeTimeout(BaseException):
    pass






WAITING_ON_USER = threading.Event()


TASK_WAITS: list = []




CLOCK_OUT: list = []
CLOCK_LOCK = threading.Lock()


def clock_ran_out() -> bool:

    return bool(CLOCK_OUT) and CLOCK_OUT[-1].is_set()


def run_with_timeout(fn: Callable[[], Any], seconds: float) -> Any:





    thread_id = threading.get_ident()
    done = threading.Event()
    expired = threading.Event()

    def _watch() -> None:
        remaining = seconds
        while remaining > 0:
            started = time.monotonic()
            if done.wait(min(remaining, 0.5)):
                return
            if not WAITING_ON_USER.is_set():
                remaining -= time.monotonic() - started
        expired.set()



        while True:
            with CLOCK_LOCK:
                if done.is_set():
                    return
                if not TASK_WAITS:


                    armed = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread_id),
                                                                       ctypes.py_object(CodeTimeout))
                    if armed > 1:
                        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread_id), None)
                    return
                TASK_WAITS[-1]()
            if done.wait(0.5):
                return

    with CLOCK_LOCK:
        CLOCK_OUT.append(expired)
    watchdog = threading.Thread(target=_watch, name="execute_code-watchdog", daemon=True)
    watchdog.start()
    try:
        return fn()
    finally:
        with CLOCK_LOCK:
            done.set()
            if expired in CLOCK_OUT:
                CLOCK_OUT.remove(expired)
