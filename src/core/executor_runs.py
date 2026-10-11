# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





from __future__ import annotations

import time

from qgis.PyQt.QtCore import QCoreApplication

from . import background, code_guard, layer_egress, layout_show, licence, limits, machine, stalls
from .checkpoints import KIND_AFTER
from .context import stamp_thread
from .executor_idempotency import IdempotencyTable
from .log_scrub import scrub_result
from .logger import log_warning
from .protocol import ClientErrorCode as Err
from .protocol import Decision
from .run_report import build_report
from .serialization import error_details
from .snapshot import RunSnapshot, changed_layer_items, diff_changed
from .tool_registry import coded_fact

try:
    from ..tools import guards
except ImportError:
    guards = None



CANCELLED_KEEP = 200


def tr(text: str) -> str:
    return QCoreApplication.translate("ToolExecutor", text)



def _open_dialog() -> str | None:





    try:
        from qgis.PyQt.QtWidgets import QApplication

        from .code_processing import WINDOW_NAME

        dialog = QApplication.activeModalWidget()
    except Exception:  # noqa: BLE001
        return None
    if dialog is None or dialog.objectName() == WINDOW_NAME:
        return None
    return str(dialog.windowTitle())


def _stop_snippet_algorithm(run_id: str) -> None:

    try:
        from .code_processing import stop
    except ImportError:
        return
    stop(run_id)


def _snippet_waiting() -> bool:

    try:
        from .code_processing import _WAITS
    except ImportError:
        return False
    return bool(_WAITS)


class _ExecutorRuns:
    @staticmethod
    def _refresh_proxy() -> None:

        try:
            from .security import apply_qgis_proxy
            apply_qgis_proxy()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"QGIS proxy not applied to tool fetches: {exc}")










    def _inflight_description(self) -> str:

        now = time.monotonic()
        parts = [f"{name} ({now - started:.1f}s in flight)"
                 for _, name, started, in_background in self._inflight.values() if not in_background]
        return ", ".join(parts[:3])

    def _on_main_thread_blocked(self, seconds: float, who: str) -> None:
























        if not who:
            return



        sentence = (f"QGIS stopped answering for {seconds:.0f} seconds during {who}. The window "
                    "was frozen for that long, so this one call is refused.")
        for run_id in list(self._run_mode):
            self._blocked[run_id] = sentence

    def _on_main_thread_stalled(self, seconds: float, where: str) -> None:






        if not self._run_mode:
            return
        try:
            calls = self._inflight_description()
        except Exception:  # noqa: BLE001
            calls = ""
        send = getattr(self._session, "send_busy", None)
        if send is not None:
            send(seconds, where, calls)

    def _on_main_thread_thawed(self, seconds: float) -> None:


        if not self._run_mode:
            return
        send = getattr(self._session, "send_busy", None)
        if send is not None:
            send(seconds, "", "", True)

    def _sweep_deadlines(self, now: float | None = None) -> None:

        if self._waiting_for_history and self._table.ready and not self._closed:
            self._when_main_free("history", None, self._take_history)
        if self._held:
            self._read_held()
        if self._queued:
            self._take_turns()
        if not (self._run_mode or self._inflight or self._background or self._waiting_for_history or self._held
                or self._queued):



            self.watchdog.stop()
            code_guard.WAITING_ON_USER.clear()
            return
        now = time.monotonic() if now is None else float(now)
        self._note_dialog_waits(now)
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
                       "",
                       spent,
                       details=error_details(coded_fact(hint="tool_call_timeout", tool=name, spent_s=round(spent),
                                                        budget_s=round(budget))))

    def _note_dialog_waits(self, now: float) -> None:












        last, self._dialog_tick = self._dialog_tick, now
        title = _open_dialog() if self._inflight else None

        if title is None:
            code_guard.WAITING_ON_USER.clear()
        else:
            code_guard.WAITING_ON_USER.set()
        if last is None:
            return
        step = min(now - last, 2.0)
        if title is None and not _snippet_waiting():
            return
        for tool_call_id, (_run_id, _name, _started, in_background) in self._inflight.items():
            if not in_background:
                looped, on_dialog, seen = self._dialog_waits.get(tool_call_id, (0.0, 0.0, ""))
                self._dialog_waits[tool_call_id] = (looped + step, on_dialog + (step if title is not None else 0.0),
                                                    title if title is not None else seen)

    def _answer_once(self, tool_call_id: str) -> None:

        self._answered[tool_call_id] = None
        self._answered.move_to_end(tool_call_id)
        while len(self._answered) > CANCELLED_KEEP:
            self._answered.popitem(last=False)

    def _note_slow_main_thread(self, run_id: str, name: str, duration: float) -> None:







        budget = limits.main_budget(name)




        machine.note_slow_call(name, duration, budget)
        sentence = (f"{name} held the QGIS main thread for {duration:.0f} seconds, over the "
                    f"{budget:.0f} second budget for a call that runs there. "
                    "The window was frozen for that long.")
        log_warning(sentence)
        if run_id:
            self._blocked[run_id] = sentence



    def _wake_watchdog(self) -> None:

        if not self._closed:
            self.watchdog.start()

    def begin_run(self, run_id: str, mode: str, approval: str, thread_id: str = "",
                  prompt: str = "") -> None:
        self._wake_watchdog()





        self._refresh_proxy()

        self.record_pending_after()
        self._run_mode[run_id] = (mode, approval)
        self._threads[run_id] = thread_id or ""

        self._prompts[run_id] = str(prompt or "")
        self._journal[run_id] = []
        self._touched[run_id] = set()
        self._cancelled.pop(run_id, None)
        self._blocked.pop(run_id, None)
        layer_egress.begin_run(run_id)
        if guards is not None:
            self._budgets[run_id] = guards.RunBudget()
        self.follower.enabled = bool(getattr(self._settings, "follow_edits", True))
        self.follower.begin()
        self.scratch.begin()
        self.stacker.begin()

    def cancel_run(self, run_id: str, halt: bool = True) -> None:







        self._cancelled[run_id] = None
        self._cancelled.move_to_end(run_id)
        _stop_snippet_algorithm(run_id)
        self._code_run_grants.discard(run_id)
        self._code_run_unknown.pop(run_id, None)
        while len(self._cancelled) > CANCELLED_KEEP:
            self._cancelled.popitem(last=False)
        if halt:

            self._layouts.pop(run_id, None)
            try:
                self.follower.halt()
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Canvas follower not halted on Stop: {exc}")
            self._cancel_tasks(run_id)
        self._drop_held(run_id)
        self._drop_queued(run_id)
        self._drop_main_line(run_id)
        for tool_call_id, (task_run, task, _call) in list(self._background.items()):
            if task_run == run_id:
                self._background.pop(tool_call_id, None)
                background.cancel(task)
        for tool_call_id, call in list(self._pending.items()):
            if call.get("run_id") == run_id:
                self._pending.pop(tool_call_id, None)
                self._card_closed_unallowed(call)
                self._session.send_permission_response(tool_call_id, run_id, Decision.DENY)
                self._table.put(tool_call_id, "deny", {})
                self.permission_resolved.emit(tool_call_id, Decision.DENY)
        for tool_call_id, call in list(self._questions.items()):
            if call.get("run_id") == run_id:
                self._questions.pop(tool_call_id, None)
                self._fail(call, Err.CANCELLED, tr("The run was cancelled by the user."),
                           "The user stopped the run.")
                self.question_resolved.emit(tool_call_id, "")

    def _cancel_tasks(self, run_id: str) -> None:






        for task_id in sorted(self._task_ids.pop(run_id, ())):
            try:
                self._registry.execute("cancel_task", {"task_id": task_id})
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Task {task_id} not cancelled on Stop: {exc}")

    def main_call_in_flight(self, run_id: str) -> str:

        return next((name for rid, name, _started, in_background in self._inflight.values()
                     if rid == run_id and not in_background), "")

    def project_replaced(self, run_id: str) -> None:






        self._replaced.add(run_id)
        for call in self._pending.values():
            if call.get("run_id") == run_id:

                call.pop("_prior_zone", None)
                call.pop("_zone_proposed", None)
        self.cancel_run(run_id)
        if run_id in self._touched:
            self._touched[run_id] = set()
        for watcher_end in (self.stacker.end, self.follower.end, self.scratch.end):
            try:
                watcher_end()
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Watcher not stopped on project close: {exc}")

    def cancel_background(self) -> None:



        self._closed = True

        _stop_snippet_algorithm("")
        self._waiting_for_history.clear()
        self._executing.clear()
        self._background.clear()
        self._inflight.clear()
        self._held.clear()
        self._queued.clear()
        self._main_line.clear()
        self._listen_tasks(False)
        self.watchdog.stop()
        background.cancel_all()








        for watcher_end in (self.follower.end, self.stacker.end, self.scratch.end):
            try:
                watcher_end()
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Run watcher not stopped at unload: {exc}")
        self._table.close()

    def bind_account(self) -> None:









        if self._closed:
            return
        old, self._table = self._table, IdempotencyTable()
        try:
            old.close()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Tool history of the previous account not closed: {exc}")

    def end_run(self, run_id: str) -> RunSnapshot | None:
        with stalls.probe("executor.end_run"):
            return self._end_run(run_id)

    def _end_run(self, run_id: str) -> RunSnapshot | None:
        _stop_snippet_algorithm(run_id)

        if (any(c.get("run_id") == run_id for c in [*self._pending.values(), *self._questions.values()])
                or any(c.get("run_id") == run_id for c in self._waiting_for_history.values())
                or any(task_run == run_id for task_run, _task, _call in self._background.values())
                or any(entry[0].get("run_id") == run_id for entry in self._held.values())
                or run_id in self._queued
                or any((call or {}).get("run_id") == run_id for _kind, call, _start in self._main_line)):
            self.cancel_run(run_id, halt=False)
        layer_egress.end_run(run_id)
        self._run_mode.pop(run_id, None)
        self._touched.pop(run_id, None)
        self._budgets.pop(run_id, None)
        self._refused_costly.pop(run_id, None)
        self._blocked.pop(run_id, None)
        written = self._written.pop(run_id, None)
        self._own_files.pop(run_id, None)
        warnings = self._call_warnings.pop(run_id, None)
        outputs = self._output_files.pop(run_id, None)
        self._run_allowed.pop(run_id, None)
        self._code_run_grants.discard(run_id)
        self._code_run_unknown.pop(run_id, None)
        self._run_denied.pop(run_id, None)
        self._task_ids.pop(run_id, None)
        snapshot = self._snapshots.pop(run_id, None)
        thread_id = self._threads.pop(run_id, "")
        run_index = self._run_index.pop(run_id, 0)
        prompt = self._prompts.pop(run_id, "")
        run_log = self._journal.pop(run_id, None)
        if run_log and thread_id:
            try:
                self.history.attach_log(thread_id, run_id, run_log)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Run log not kept: {exc}")


        replaced = run_id in self._replaced
        self._replaced.discard(run_id)
        self._ended_run = (("", (), (), ()) if replaced
                           else (run_id, tuple(written or ()), tuple(warnings or ()), tuple(outputs or ())))
        if snapshot is not None and snapshot.captured and not replaced:
            self.last_snapshot = snapshot
            self._record_after(run_id, thread_id, run_index, snapshot, prompt)

        if not replaced:



            try:
                added = self.stacker.take_added()
                licence.credit_added(added, None)
                stamp_thread(added, thread_id)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Layer credit at end of run failed: {exc}")
            self.stacker.place_pending()
        self.stacker.end()

        layout = self._layouts.pop(run_id, "")
        if not replaced:
            self.follower.now()
            if layout and self.follower.enabled:
                layout_show.show(layout)
        self.follower.end()

        self.scratch.end()
        return None if replaced else snapshot

    def _record_after(self, run_id: str, thread_id: str, run_index: int, snapshot: RunSnapshot,
                      prompt: str = "") -> None:





        try:
            diff = snapshot.diff()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Snapshot diff failed: {exc}")
            return
        snapshot.last_diff = diff
        changed = int(diff.get("changed_layers") or 0)



        if not diff_changed(diff):
            return
        self._after_pending = (run_id, thread_id, run_index, snapshot, changed, changed_layer_items(diff), prompt)

    def record_pending_after(self) -> bool:







        pending, self._after_pending = self._after_pending, None
        if pending is None:
            return False
        run_id, thread_id, run_index, snapshot, changed, items, prompt = pending
        with stalls.probe("executor.record_after"):
            return self._capture_after(run_id, thread_id, run_index, snapshot, changed, items, prompt)

    def drop_pending_after(self) -> None:

        self._after_pending = None

    def _capture_after(self, run_id: str, thread_id: str, run_index: int, snapshot: RunSnapshot,
                       changed: int, items: list, prompt: str) -> bool:






        current = self.history.current(thread_id)
        moved = current is not None and current.run_id != run_id
        if moved:
            try:
                if not diff_changed(current.snapshot.diff()):
                    return False
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Snapshot diff against the restored point failed: {exc}")
        after = RunSnapshot(f"{run_id or 'run'}-after")
        try:
            captured = after.capture()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"After-run snapshot failed: {exc}")
            captured = False
        if captured:



            try:
                after.keep_files_of(snapshot)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"After-run file copies failed: {exc}")
            self.history.add(thread_id, KIND_AFTER, run_id, run_index, after, changed,
                             items, prompt, fork=not moved)
        return captured

    def snapshot_for(self, run_id: str) -> RunSnapshot | None:
        return self._snapshots.get(run_id)

    def changed_layers(self, run_id: str) -> int:
        return len(self._touched.get(run_id, ()))

    def ended_run_report(self, run_id: str, snapshot: RunSnapshot | None) -> dict | None:







        ended_id, written, warnings, outputs = self._ended_run
        if not run_id or run_id != ended_id:
            return None
        self._ended_run = ("", (), (), ())
        diff = getattr(snapshot, "last_diff", None) if snapshot is not None else None
        with stalls.probe("run_report.build"):

            return scrub_result(build_report(snapshot, written, call_warnings=warnings, diff=diff,
                                             working_copies=self.scratch.working_copies(),
                                             hidden=self.scratch.hidden_ids(), declared=outputs))
