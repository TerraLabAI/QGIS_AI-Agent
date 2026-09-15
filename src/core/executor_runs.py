# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run lifecycle for the tool executor: the clocks, the run state and its end."""




from __future__ import annotations

import time

from qgis.PyQt.QtCore import QCoreApplication

from . import background, licence, limits, machine, stalls
from .checkpoints import KIND_AFTER
from .context import stamp_thread
from .executor_idempotency import IdempotencyTable
from .log_scrub import scrub_result
from .logger import log_warning
from .protocol import ClientErrorCode as Err
from .protocol import Decision
from .run_report import build_report
from .snapshot import RunSnapshot, changed_layer_items, diff_changed

try:
    from ..tools import guards
except ImportError:
    guards = None



CANCELLED_KEEP = 200


def tr(text: str) -> str:
    return QCoreApplication.translate("ToolExecutor", text)


class _ExecutorRuns:
    @staticmethod
    def _refresh_proxy() -> None:
        """Point the guarded opener at whatever proxy QGIS is set to right now."""
        try:
            from .security import apply_qgis_proxy
            apply_qgis_proxy()
        except Exception as exc:  # noqa: BLE001 - a fetch without a proxy beats no plugin
            log_warning(f"QGIS proxy not applied to tool fetches: {exc}")










    def _inflight_description(self) -> str:
        """What was running when the event loop stopped, for the log line."""
        now = time.monotonic()
        parts = [f"{name} ({now - started:.1f}s in flight)"
                 for _, name, started, in_background in self._inflight.values() if not in_background]
        return ", ".join(parts[:3])

    def _on_main_thread_blocked(self, seconds: float, who: str) -> None:
        """The watchdog saw the event loop stop."""
























        if who:



            sentence = (f"QGIS stopped answering for {seconds:.0f} seconds during {who}. The window "
                        "was frozen for that long, so this one call is refused.")
        else:
            sentence = (f"QGIS stopped answering for {seconds:.0f} seconds, with no tool call of this "
                        "run in flight. Nothing is refused; the window was busy with something else.")
        for run_id in list(self._run_mode):
            if who:
                self._blocked[run_id] = sentence
            self.run_blocked.emit(run_id, sentence)

    def _sweep_deadlines(self, now: float | None = None) -> None:
        """One tick of the event loop: cancel any call past its budget."""
        if self._waiting_for_history and self._table.ready and not self._closed:
            waiting, self._waiting_for_history = self._waiting_for_history, {}
            for call in waiting.values():
                self.handle_tool_call(call)
        now = time.monotonic() if now is None else float(now)
        for tool_call_id, (run_id, name, started, in_background) in list(self._inflight.items()):
            spent = now - started





            budget = (limits.current("CALL_MAX_SECONDS_BACKGROUND") if in_background
                      else limits.main_budget(name))
            if spent <= budget:
                continue
            if not in_background:



                continue
            self._inflight.pop(tool_call_id, None)
            entry = self._background.pop(tool_call_id, None)
            if entry is not None:
                background.cancel(entry[1])
            self._answer_once(tool_call_id)



            self._fail({"tool_call_id": tool_call_id, "run_id": run_id, "name": name},
                       Err.TIMEOUT,
                       f"{name} ran for {spent:.0f} seconds without answering, over the "
                       f"{budget:.0f} second budget for one tool call on this computer, and was "
                       "cancelled.",
                       "Ask for less in one call: a smaller area, fewer features, one layer instead of "
                       "the whole catalogue. Do not send the same call again unchanged.",
                       spent)

    def _answer_once(self, tool_call_id: str) -> None:
        """Remember that this call has been answered, so a late result is dropped."""
        self._answered[tool_call_id] = None
        self._answered.move_to_end(tool_call_id)
        while len(self._answered) > CANCELLED_KEEP:
            self._answered.popitem(last=False)

    def _note_slow_main_thread(self, run_id: str, name: str, duration: float) -> None:
        """A main-thread handler that came back late: name it and hold the run."""






        budget = limits.main_budget(name)




        machine.note_slow_call(name, duration, budget)
        sentence = (f"{name} held the QGIS main thread for {duration:.0f} seconds, over the "
                    f"{budget:.0f} second budget for a call that runs there. "
                    "The window was frozen for that long.")
        log_warning(sentence)
        if run_id:
            self._blocked[run_id] = sentence
            self.run_blocked.emit(run_id, sentence)



    def begin_run(self, run_id: str, mode: str, approval: str, thread_id: str = "",
                  prompt: str = "") -> None:





        self._refresh_proxy()
        self._run_mode[run_id] = (mode, approval)
        self._threads[run_id] = thread_id or ""

        self._prompts[run_id] = str(prompt or "")
        self._journal[run_id] = []
        self._touched[run_id] = set()
        self._cancelled.pop(run_id, None)
        self._blocked.pop(run_id, None)
        if guards is not None:
            self._budgets[run_id] = guards.RunBudget()
        self.follower.enabled = bool(getattr(self._settings, "follow_edits", True))
        self.follower.begin()
        self.scratch.begin()
        self.stacker.begin()

    def cancel_run(self, run_id: str, halt: bool = True) -> None:
        """Refuse what is left of the run: its cards, questions and background tasks."""






        self._cancelled[run_id] = None
        self._cancelled.move_to_end(run_id)
        while len(self._cancelled) > CANCELLED_KEEP:
            self._cancelled.popitem(last=False)
        if halt:
            try:
                self.follower.halt()
            except Exception as exc:  # noqa: BLE001 - Stop never fails on the follower
                log_warning(f"Canvas follower not halted on Stop: {exc}")
            self._cancel_tasks(run_id)
        for tool_call_id, (task_run, task) in list(self._background.items()):
            if task_run == run_id:
                self._background.pop(tool_call_id, None)
                background.cancel(task)
        for tool_call_id, call in list(self._pending.items()):
            if call.get("run_id") == run_id:
                self._pending.pop(tool_call_id, None)
                self._session.send_permission_response(tool_call_id, run_id, Decision.DENY)
                self._table.put(tool_call_id, "deny", {})
                self.permission_resolved.emit(tool_call_id, Decision.DENY)
        for tool_call_id, call in list(self._questions.items()):
            if call.get("run_id") == run_id:
                self._questions.pop(tool_call_id, None)
                self._fail(call, Err.CANCELLED, tr("The run was cancelled by the user."),
                           "Stop here and wait for the next user message.")
                self.question_resolved.emit(tool_call_id, "")

    def _cancel_tasks(self, run_id: str) -> None:
        """Stop every task this run's calls started in the task registry, through cancel_task."""





        for task_id in sorted(self._task_ids.pop(run_id, ())):
            try:
                self._registry.execute("cancel_task", {"task_id": task_id})
            except Exception as exc:  # noqa: BLE001 - Stop never fails on a task
                log_warning(f"Task {task_id} not cancelled on Stop: {exc}")

    def main_call_in_flight(self, run_id: str) -> str:
        """The name of this run's tool call holding the main thread right now, or ""."""
        return next((name for rid, name, _started, in_background in self._inflight.values()
                     if rid == run_id and not in_background), "")

    def project_replaced(self, run_id: str) -> None:
        """The project this run worked on was closed by the user."""





        self._replaced.add(run_id)
        self.cancel_run(run_id)
        if run_id in self._touched:
            self._touched[run_id] = set()
        for watcher_end in (self.stacker.end, self.follower.end, self.scratch.end):
            try:
                watcher_end()
            except Exception as exc:  # noqa: BLE001 - a watcher never keeps a closed project
                log_warning(f"Watcher not stopped on project close: {exc}")

    def cancel_background(self) -> None:
        """Plugin unload: every tool task still on the wire is asked to stop, and the idempotency table lands on disk before the thread that writes it."""


        self._closed = True
        self._waiting_for_history.clear()
        self._executing.clear()
        self._background.clear()
        self._inflight.clear()
        self.watchdog.stop()
        background.cancel_all()




        try:
            self.follower.end()
            self.stacker.end()
        except Exception as exc:  # noqa: BLE001 - unload never fails on a follower
            log_warning(f"Canvas follower not stopped at unload: {exc}")
        self._table.close()

    def bind_account(self) -> None:
        """Another account is signed in: its calls' answers go to its own folder."""








        if self._closed:
            return
        old, self._table = self._table, IdempotencyTable()
        try:
            old.close()
        except Exception as exc:  # noqa: BLE001 - a sign-in never fails on the old account's file
            log_warning(f"Tool history of the previous account not closed: {exc}")

    def end_run(self, run_id: str) -> RunSnapshot | None:
        with stalls.probe("executor.end_run"):
            return self._end_run(run_id)

    def _end_run(self, run_id: str) -> RunSnapshot | None:

        if (any(c.get("run_id") == run_id for c in [*self._pending.values(), *self._questions.values()])
                or any(c.get("run_id") == run_id for c in self._waiting_for_history.values())
                or any(task_run == run_id for task_run, _task in self._background.values())):
            self.cancel_run(run_id, halt=False)
        self._run_mode.pop(run_id, None)
        self._touched.pop(run_id, None)
        self._budgets.pop(run_id, None)
        self._refused_costly.pop(run_id, None)
        self._blocked.pop(run_id, None)
        written = self._written.pop(run_id, None)
        warnings = self._call_warnings.pop(run_id, None)
        self._code_allowed.discard(run_id)
        self._task_ids.pop(run_id, None)
        snapshot = self._snapshots.pop(run_id, None)
        thread_id = self._threads.pop(run_id, "")
        run_index = self._run_index.pop(run_id, 0)
        prompt = self._prompts.pop(run_id, "")
        run_log = self._journal.pop(run_id, None)
        if run_log and thread_id:
            try:
                self.history.attach_log(thread_id, run_id, run_log)
            except Exception as exc:  # noqa: BLE001 - a log never breaks the end of a run
                log_warning(f"Run log not kept: {exc}")


        replaced = run_id in self._replaced
        self._replaced.discard(run_id)
        self._ended_run = ("", (), ()) if replaced else (run_id, tuple(written or ()), tuple(warnings or ()))
        if snapshot is not None and snapshot.captured and not replaced:
            self.last_snapshot = snapshot
            self._record_after(run_id, thread_id, run_index, snapshot, prompt)

        if not replaced:



            try:
                added = self.stacker.take_added()
                licence.credit_added(added, None)
                stamp_thread(added, thread_id)
            except Exception as exc:  # noqa: BLE001 - a credit never breaks end_run
                log_warning(f"Layer credit at end of run failed: {exc}")
            self.stacker.place_pending()
        self.stacker.end()

        if not replaced:
            self.follower.now()
        self.follower.end()

        self.scratch.end()
        return None if replaced else snapshot

    def _record_after(self, run_id: str, thread_id: str, run_index: int, snapshot: RunSnapshot,
                      prompt: str = "") -> None:
        """The run's diff, kept on the snapshot for the controller, and a second capture when the run changed something: the state to come back to."""


        try:
            diff = snapshot.diff()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Snapshot diff failed: {exc}")
            return
        snapshot.last_diff = diff
        changed = int(diff.get("changed_layers") or 0)



        if not diff_changed(diff):
            return
        after = RunSnapshot(f"{run_id or 'run'}-after")
        try:
            captured = after.capture()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"After-run snapshot failed: {exc}")
            captured = False
        if captured:
            self.history.add(thread_id, KIND_AFTER, run_id, run_index, after, changed,
                             changed_layer_items(diff), prompt)

    def snapshot_for(self, run_id: str) -> RunSnapshot | None:
        return self._snapshots.get(run_id)

    def changed_layers(self, run_id: str) -> int:
        return len(self._touched.get(run_id, ()))

    def ended_run_report(self, run_id: str, snapshot: RunSnapshot | None) -> dict | None:
        """What the run that just ended left in the project, for the chips under its answer."""






        ended_id, written, warnings = self._ended_run
        if not run_id or run_id != ended_id:
            return None
        self._ended_run = ("", (), ())
        diff = getattr(snapshot, "last_diff", None) if snapshot is not None else None
        with stalls.probe("run_report.build"):

            return scrub_result(build_report(snapshot, written, call_warnings=warnings, diff=diff,
                                             working_copies=self.scratch.working_copies(),
                                             hidden=self.scratch.hidden_ids()))
