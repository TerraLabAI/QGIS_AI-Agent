# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Running a checked call: CRS guard, backups, background task, then the handler."""



from __future__ import annotations

import time
import traceback

from qgis.PyQt.QtCore import QCoreApplication

from . import background, stalls
from .logger import log, log_warning
from .protocol import ClientErrorCode as Err
from .protocol import Danger


def tr(text: str) -> str:
    return QCoreApplication.translate("ToolExecutor", text)


class _ExecutorExecute:


    def _execute(self, call: dict) -> None:
        tool_call_id = str(call.get("tool_call_id"))
        if tool_call_id in self._executing or self._closed:
            return


        self._executing.add(tool_call_id)
        try:
            self._prepare_and_execute(call)
        except Exception as exc:  # noqa: BLE001 - preparation must also answer the call
            self._fail_unanswered(call, exc)

    def _fail_unanswered(self, call: dict, exc: Exception) -> None:
        """Answer a call whose preparation or delivery raised, unless it was answered already."""









        tool_call_id = str(call.get("tool_call_id"))
        if self._table.get(tool_call_id) is not None or tool_call_id in self._answered:
            self._executing.discard(tool_call_id)
            log_warning(f"{call.get('name')}: {type(exc).__name__} after its answer was sent: {exc}")
            return
        self._fail(call, Err.EXECUTION_FAILED, f"{type(exc).__name__}: {exc}",
                   "Inspect the project before retrying the operation.")

    def _prepare_and_execute(self, call: dict) -> None:
        run_id = str(call.get("run_id") or "")
        name, args, danger = str(call.get("name")), call.get("args") or {}, call.get("danger", Danger.READ)
        guard = self._crs_guard(name, args)
        if guard is not None:
            self._fail(call, Err.CRS_GUARD, guard[0], guard[1])
            return
        if danger != Danger.READ:
            snapshot = self._prepare_snapshot(run_id, name, args, danger, call.get("overwrites") or [])
            if snapshot is not None and getattr(snapshot, "copy_errors", None):
                self._fail(call, Err.EXECUTION_FAILED, "A backup failed; this run cannot make further changes.",
                           "Check free disk space and folder access, then start a new run.")
                return
            if snapshot is not None and snapshot.has_pending_copies() and self._copy_then_run(call, snapshot):
                return
        self._execute_now(call)

    def _copy_then_run(self, call: dict, snapshot) -> bool:
        """The backups this call needs are large: copy them off the main thread, then run the call."""

        name = str(call.get("name") or "")
        tool_call_id = str(call.get("tool_call_id"))
        run_id = str(call.get("run_id") or "")

        def done(_result, error_text: str):
            self._background.pop(tool_call_id, None)
            self._inflight.pop(tool_call_id, None)
            if self._closed or tool_call_id in self._answered:
                return
            if error_text:
                log_warning(f"Backup before {name} failed: {error_text.split(chr(10), 1)[0]}")
                if not self._closed:
                    self._fail(call, Err.EXECUTION_FAILED, "The backup failed; the operation was not started.",
                               "Check free disk space and folder access before trying again.")
                return
            try:
                self._execute_now(call)
            except Exception as exc:  # noqa: BLE001 - the task only logs what its callback raises
                self._fail_unanswered(call, exc)

        task = background.run_off_thread(f"AI Agent: backup before {name}", snapshot.run_pending_copies, done)
        if task is None:
            snapshot.run_pending_copies()
            return False
        self._background[tool_call_id] = (run_id, task)
        self._inflight[tool_call_id] = (run_id, name, time.monotonic(), True)
        stalls.mark(f"backup off-thread before {name}")
        return True

    def _execute_now(self, call: dict) -> None:
        if self._closed or str(call.get("tool_call_id")) in self._answered:
            return
        run_id = str(call.get("run_id") or "")
        name, args, danger = str(call.get("name")), call.get("args") or {}, call.get("danger", Danger.READ)
        if run_id in self._cancelled:
            self._fail(call, Err.CANCELLED, tr("The run was cancelled by the user."),
                       "Stop here and wait for the next user message.")
            return
        self.tool_started.emit(call)
        log(f"RUN {name} danger={danger} args={self._args_digest(args)}")






        if danger != Danger.READ:
            with stalls.probe("follow.approach"):
                self.follower.approach(name, args)
        started = time.monotonic()
        tool_call_id = str(call.get("tool_call_id"))


        call["scratch_mark"] = self.scratch.mark()
        if danger != Danger.READ:

            call["journal_before"] = self._journal_before(run_id, name, args)
        if self._start_background(call, name, args, started):
            return



        self._inflight[tool_call_id] = (run_id, name, started, False)
        try:
            with stalls.probe(f"tool.{name}"):
                result = self._registry.execute(name, args)
        except Exception as exc:  # noqa: BLE001
            result = {"_error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()[-2000:]}
        self._deliver(call, result, started)

    def _start_background(self, call: dict, name: str, args: dict, started: float) -> bool:
        """Run a handler that declared itself off-thread safe in a QgsTask."""






        if not self._background_ok(name, args):
            return False
        if call.get("overwrites"):



            return False
        tool_call_id = str(call.get("tool_call_id"))

        def work():
            return self._registry.execute(name, args)

        def done(result, error_text: str):
            self._background.pop(tool_call_id, None)
            if error_text:
                result = {"_error": error_text.split("\n", 1)[0], "traceback": error_text}
            try:
                self._deliver(call, result, started)
            except Exception as exc:  # noqa: BLE001 - the task only logs what its callback raises
                self._inflight.pop(tool_call_id, None)
                self._fail_unanswered(call, exc)


        task = background.run_off_thread(f"AI Agent: {name}", work, done)
        if task is None:
            return False
        self._background[tool_call_id] = (str(call.get("run_id") or ""), task)


        self._inflight[tool_call_id] = (str(call.get("run_id") or ""), name, started, True)
        return True

    def _background_ok(self, name: str, args: dict) -> bool:
        """The tool's own answer: a bool, or a predicate on the arguments for a tool like add_data whose remote branches are safe and whose local-file."""


        tool = self._registry.get_tool(name)
        flag = getattr(tool, "background", False)
        if callable(flag):
            try:
                return bool(flag(args))
            except Exception as exc:  # noqa: BLE001
                log_warning(f"background predicate for {name} failed: {exc}")
                return False
        return bool(flag)
