# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


from __future__ import annotations

import threading




_LOCAL = threading.local()


def set_cancel_check(check) -> None:

    _LOCAL.cancel = check


def current_cancel_check():
    return getattr(_LOCAL, "cancel", None)


def is_cancelled(cancel=None) -> bool:

    check = cancel or current_cancel_check()
    if check is None:
        return False
    try:
        return bool(check())
    except Exception:  # noqa: BLE001
        return False


def set_progress_sink(sink) -> None:




    _LOCAL.progress = sink


def current_progress_sink():
    return getattr(_LOCAL, "progress", None)


def report_stage(stage: str, done=None, total=None) -> None:



    sink = current_progress_sink()
    if sink is None:
        return
    try:
        sink(done, total, stage)
    except Exception:  # noqa: BLE001  # nosec B110
        pass




_TIMING_SINK = None


def set_timing_sink(sink) -> None:
    global _TIMING_SINK
    _TIMING_SINK = sink


def current_timing_sink():
    return _TIMING_SINK
