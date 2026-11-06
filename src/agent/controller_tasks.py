# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

FINAL = ("done", "failed", "stopped")


class _ControllerTasks:
    def _on_task_update(self, frame: dict) -> None:
        task_id = str(frame.get("task_id") or "")
        status = str(frame.get("status") or "")
        seen = self.__dict__.setdefault("_task_states", {})
        before = seen.get(task_id)
        if before is None and status not in FINAL:
            last = getattr(self, "_last_run", None) or {}
            self._executor.begin_run(task_id, str(last.get("mode") or "agent"), str(last.get("approval") or "ask"),
                                     self._thread_id or "", "")
        elif before is not None and before not in FINAL and status in FINAL:
            self._executor.end_run(task_id)
        seen[task_id] = status
        if status not in FINAL:
            keep = getattr(self._session, "keep_for_reopen", None)
            if keep is not None:
                keep()
        self._panel_call("task_update", frame)

    def _on_task_result(self, frame: dict) -> None:
        self._panel_call("task_result", frame)

    def _on_task_stop(self, task_id: str) -> None:
        self._session.send_task_stop(task_id)
