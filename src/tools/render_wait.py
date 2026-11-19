# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



from __future__ import annotations

import time

from qgis.PyQt.QtCore import QEventLoop, QTimer

from ..core.net_state import current_cancel_check
from ..core.qt_compat import disconnect_quietly, enum_member


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


def finish_on_main(job, budget_s: float) -> bool:










    if not job.isActive():
        return True
    loop = QEventLoop()
    flags = enum_member(QEventLoop, "ProcessEventsFlag", "ExcludeUserInputEvents")
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    job.finished.connect(loop.quit)
    try:
        timer.start(int(max(0.0, budget_s) * 1000))
        QEventLoop.exec(loop, flags)
        if not job.isActive():
            return True
        job.cancelWithoutBlocking()
        while job.isActive():
            QEventLoop.exec(loop, flags)
        return False
    finally:
        timer.stop()
        disconnect_quietly(job, "finished", loop.quit)
