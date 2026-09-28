# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later













from __future__ import annotations

import time
import traceback

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QTimer

from .logger import log_warning
from .protocol import ClientErrorCode as Err


MAX_HOLD_S = 60.0


def _differs(result: dict, seen) -> bool:

    if not isinstance(seen, dict):
        return False
    for key, value in seen.items():
        now = result.get(key)
        if (now if isinstance(now, (str, int, float, bool)) else None) != value:
            return True
    return False


def _moved(result, seen: dict, held_s: float, move_s: float) -> bool:

    if not isinstance(result, dict) or "_error" in result:
        return True
    if _differs(result, seen.get("state")):
        return True
    return held_s >= move_s and _differs(result, seen.get("progress"))


def tr(text: str) -> str:
    from qgis.PyQt.QtCore import QCoreApplication
    return QCoreApplication.translate("ToolExecutor", text)


class _ExecutorHold:
    def _hold(self, call: dict, result, started: float) -> bool:

        wait = call.get("wait")
        if call.get("poll") is not True or not isinstance(wait, dict):
            return False
        try:
            max_s = min(MAX_HOLD_S, float(wait.get("max_s") or 0.0))
            move_s = float(wait.get("move_s") or 0.0)
        except (TypeError, ValueError):
            return False
        seen = wait.get("seen") if isinstance(wait.get("seen"), dict) else {}
        if max_s <= 0.0 or _moved(result, seen, 0.0, move_s):
            return False
        tool_call_id = str(call.get("tool_call_id"))

        self._inflight.pop(tool_call_id, None)
        self._held[tool_call_id] = (call, started, max_s, move_s, seen)
        self._listen_tasks(True)
        return True

    def _read_held(self) -> None:

        if self._closed:
            return
        for tool_call_id, (call, started, max_s, move_s, seen) in list(self._held.items()):
            if tool_call_id not in self._held:
                continue
            try:
                result = self._registry.execute(str(call.get("name")), call.get("args") or {})
            except Exception as exc:  # noqa: BLE001
                result = {"_error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()[-2000:]}
            held_s = time.monotonic() - started
            if held_s >= max_s or _moved(result, seen, held_s, move_s):
                self._held.pop(tool_call_id, None)
                self._deliver(call, result, started)
        if not self._held:
            self._listen_tasks(False)

    def _drop_held(self, run_id: str = "") -> None:

        for tool_call_id, entry in list(self._held.items()):
            call = entry[0]
            if run_id and str(call.get("run_id") or "") != run_id:
                continue
            self._held.pop(tool_call_id, None)
            self._fail(call, Err.CANCELLED, tr("The run was cancelled by the user."), "The user stopped the run.")
        if not self._held:
            self._listen_tasks(False)

    def _on_task_status(self, *_args) -> None:

        QTimer.singleShot(0, self._read_held)

    def _listen_tasks(self, on: bool) -> None:
        if on == self._hold_listening:
            return
        manager = QgsApplication.taskManager()
        try:
            if on:
                manager.statusChanged.connect(self._on_task_status)
            else:
                manager.statusChanged.disconnect(self._on_task_status)
            self._hold_listening = on
        except (TypeError, RuntimeError) as exc:
            log_warning(f"Task status signal not {'connected' if on else 'disconnected'}: {exc}")
