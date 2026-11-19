# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later















from __future__ import annotations

import os
import time
import traceback
from collections import OrderedDict, deque

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal

from ..core import (
    background,
    datum_choice,
    follow,
    layer_order,
    layer_removed,
    restore_guard,
    scratch,
    stalls,
    telemetry,
)
from ..core import telemetry_events as ev
from ..core.checkpoints import KIND_BEFORE, CheckpointHistory
from ..core.logger import log, log_warning
from ..core.protocol import ClientErrorCode as Err
from ..core.snapshot import RunSnapshot, diff_changed, unsaved_edit_layers
from ..core.tool_registry import tool_error
from ..core.watchdog import MainThreadWatchdog
from .executor_accounts import _ExecutorAccounts
from .executor_calls import _ExecutorCalls
from .executor_code import _ExecutorCode, tr
from .executor_deliver import _ExecutorDeliver
from .executor_execute import _ExecutorExecute
from .executor_guards import _ExecutorGuards
from .executor_idempotency import IdempotencyTable
from .executor_install_wait import _ExecutorInstallWait
from .executor_runs import _ExecutorRuns


__all__ = ["ToolExecutor", "background"]


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


class ToolExecutor(_ExecutorAccounts, _ExecutorInstallWait, _ExecutorRuns, _ExecutorCalls, _ExecutorCode,
                   _ExecutorExecute, _ExecutorDeliver, _ExecutorGuards, QObject):
    tool_started = pyqtSignal(object)



    tool_finished = pyqtSignal(str, bool, str, float, str, object)
    permission_needed = pyqtSignal(str, str, str, object)
    permission_resolved = pyqtSignal(str, str)

    question_needed = pyqtSignal(str, str, str, object, bool, int, str)
    question_resolved = pyqtSignal(str, str)


    question_answered_elsewhere = pyqtSignal(str, str)
    install_wait_needed = pyqtSignal(str, str, str)
    account_needed = pyqtSignal(str, str, object)
    project_changed = pyqtSignal(int)


    stop_requested = pyqtSignal(str)

    call_progress = pyqtSignal(str, object, object, str)

    def __init__(self, registry, session, settings, parent=None):
        super().__init__(parent)
        self._registry = registry
        self._session = session
        self._settings = settings
        self._closed = False
        self._executing: set[str] = set()
        self._table = IdempotencyTable()
        self._waiting_for_history: dict[str, dict] = {}

        self._resolving: dict[str, dict] = {}



        self._queued: dict[str, dict[str, tuple[dict, float]]] = {}




        self._main_depth = 0
        self._main_line: deque = deque()
        self._main_line_due = False
        self._pending: dict[str, dict] = {}
        self._questions: dict[str, dict] = {}


        self._refused_costly: dict[str, set[str]] = {}
        self._run_mode: dict[str, tuple[str, str]] = {}



        self._cancelled: OrderedDict[str, None] = OrderedDict()
        self._touched: dict[str, set] = {}
        self._journal: dict[str, list[dict]] = {}
        self._snapshots: dict[str, RunSnapshot] = {}
        self._written: dict[str, list[str]] = {}
        self._own_save_done: dict = {}

        self._own_files: dict[str, set[str]] = {}


        self._chat_files: OrderedDict[str, set[str]] = OrderedDict()



        self._output_files: dict[str, list[dict]] = {}


        self._objects: dict[str, list[dict]] = {}
        self._ended_objects: tuple = ()


        self._transformations: dict[str, list[dict]] = {}
        self._ended_transformations: tuple = ()
        self._call_warnings: dict[str, list[dict]] = {}

        self._ended_run: tuple = ("", (), ())
        self._budgets: dict[str, object] = {}
        self._background: dict[str, tuple] = {}
        self._task_ids: dict[str, set] = {}

        self._heard: dict[str, tuple] = {}
        self._handed_back: dict[str, str] = {}
        self._threads: dict[str, str] = {}
        self._run_index: dict[str, int] = {}
        self._prompts: dict[str, str] = {}




        self._inflight: dict[str, tuple[str, str, float, bool]] = {}


        self._dialog_waits: dict[str, tuple[float, float, str]] = {}
        self._dialog_tick: float | None = None


        self._answered: OrderedDict[str, None] = OrderedDict()


        self._unsent: OrderedDict[str, str] = OrderedDict()


        self._sent_recently: OrderedDict[str, tuple[str, float]] = OrderedDict()


        self._runs_begun: OrderedDict[str, None] = OrderedDict()

        self._blocked: dict[str, str] = {}

        self._replaced: set[str] = set()





        self._run_allowed: dict[str, dict[str, str]] = {}


        self._run_denied: dict[str, set[str]] = {}


        self._run_code_granted: dict[str, str] = {}
        self._run_capture_s: dict[str, float] = {}


        self._code_escalated: dict[str, str] = {}


        self._held: dict[str, tuple] = {}
        self._hold_listening = False


        self._after_pending: tuple | None = None
        self.history = CheckpointHistory()



        self.watchdog = MainThreadWatchdog(describe=self._inflight_description,
                                           on_blocked=self._on_main_thread_blocked,
                                           on_tick=self._sweep_deadlines,
                                           on_stall=self._on_main_thread_stalled,
                                           on_thaw=self._on_main_thread_thawed)
        self.watchdog.start()
        self.tool_finished.connect(self._turn_over)
        resumed = getattr(session, "session_started", None)
        if resumed is not None:
            resumed.connect(self._resend_unsent)
        state = getattr(session, "state_changed", None)
        if state is not None:
            state.connect(self._socket_state)
        stalls.add_context_provider(self._inflight_description)



        self.follower = follow.Follower()




        self.scratch = scratch.ScratchLedger()



        self.stacker = layer_order.Stacker()




        self.stacker.in_call = lambda: bool(self._inflight) or bool(self._handed_back)
        self._refresh_proxy()











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





        if not self._closed:
            self._when_main_free("held", None, self._read_held_now)

    def _read_held_now(self) -> None:
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
                self._deliver(call, result, started, held=True)
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



        background.listen_finished(self._on_task_status, on)
        try:
            if on:
                manager.statusChanged.connect(self._on_task_status)
            else:
                manager.statusChanged.disconnect(self._on_task_status)
            self._hold_listening = on
        except (TypeError, RuntimeError) as exc:
            log_warning(f"Task status signal not {'connected' if on else 'disconnected'}: {exc}")












    def _undo_target(self, thread_id: str):












        history = self.history
        current = history.current(thread_id)
        own = current if current is not None and current.kind == KIND_BEFORE \
            and current.run_id in getattr(self, "_threads", {}) else None
        same = frozenset()
        if own is not None:
            entries = history.entries(thread_id)
            index = next((i for i, e in enumerate(entries) if e.id == own.id), 0)
            behind = entries[index - 1] if index > 0 else None
            try:
                if diff_changed(own.snapshot.diff()):
                    return (own, True) if own.available else (None, False)
                if behind is not None and behind.snapshot is not None and not diff_changed(behind.snapshot.diff()):
                    same = frozenset({own.id})
            except Exception as exc:  # noqa: BLE001
                log(f"qgis_undo refused: the run's own before point could not be compared: {exc}")
                return None, False
        entry = history.previous(thread_id, same)
        if entry is not None and history.gap_between(thread_id, entry):
            log(f"qgis_undo refused: the points between before request {entry.run_index} and the "
                "project's state were not kept")
            return None, False
        return entry, False

    def _own_writes(self, run_id: str) -> set:

        _fold = restore_guard._fold
        out = set()
        for item in self._journal.get(run_id) or []:
            for layer in item.get("layers") or []:
                if layer.get("path") and layer.get("file_changed"):
                    out.add(_fold(layer["path"]))
            out.update(_fold(f["path"]) for f in item.get("files") or [] if f.get("path"))
        return out

    def restore_previous(self, thread_id: str) -> dict:

        history = self.history
        entry, own = self._undo_target(thread_id) if thread_id else (None, False)
        if entry is None:
            return tool_error("This chat has no earlier state to go back to, or the one before the last "
                              "request was not kept. Nothing was restored.", hint="undo_no_earlier_state")
        if not history.belongs_here(entry):
            return tool_error("The earlier state belongs to another project.", hint="undo_other_project")


        current = history.current(thread_id)
        reference = current or next(iter(reversed(history.entries(thread_id))), None)
        try:
            changed = restore_guard.maybe_changed(history, thread_id, entry, reference)
        except Exception as exc:  # noqa: BLE001
            log(f"Hand-edit check for qgis_undo failed: {exc}")
            return tool_error("Whether a file changed by hand since the AI agent's last step could not be "
                              "read, so nothing was restored. The chat's Undo button asks the user first.",
                              hint="undo_hand_changed")
        if own:


            mine = self._own_writes(entry.run_id)
            exempt = set()
            for group in entry.snapshot.data_groups():
                members = {restore_guard._fold(original) for original, _copy in group}
                if members & mine:
                    exempt |= members
            changed = [path for path in changed if restore_guard._fold(path) not in exempt]
        if changed:
            names = ", ".join(os.path.basename(path) for path in changed[:5])
            return tool_error(f"{names} changed since the AI agent's last step, possibly by the user's hand; "
                              "going back would write an older copy over it. Nothing was restored. The chat's "
                              "Undo button asks the user and keeps their version first.",
                              hint="undo_hand_changed")


        editing = [str(layer.name()) for layer in unsaved_edit_layers()]
        if editing:
            return tool_error(f"{', '.join(editing[:5])} has unsaved edits in QGIS; going back would discard "
                              "them. Nothing was restored. The user can save or discard them, or use the "
                              "chat's Undo button, which saves them in the history first.",
                              hint="undo_unsaved_edits")
        started = time.monotonic()
        history.restore_begins(entry)
        try:
            with layer_removed.undoing(), datum_choice.held(choose=False):
                result = entry.snapshot.restore(file_name=entry.file_to_keep(),
                                                extra_copies=history.file_versions(thread_id, entry),
                                                own_saves=history.own_saves_after(thread_id, entry))
        finally:
            history.restore_ended()
        history.project_restored()

        telemetry.track(ev.AGENT_UNDO_USED, {
            "ok": bool(result.get("ok")), "kind": "typed", "steps_back": 1, "run_id": entry.run_id,
            "duration_ms": int((time.monotonic() - started) * 1000), "refused": bool(result.get("refused")),
            "files_not_restored": len(result.get("file_restore_errors") or []),
            "memory_incomplete": len(result.get("memory_layers_incomplete") or [])})
        log(f"qgis_undo restored {entry.kind} run {entry.run_index}: {result.get('message') or result}")
        if result.get("project_read"):

            history.mark_current(entry)
        if not result.get("ok"):
            if not result.get("refused") or result.get("files_blocked"):
                from ..core.telemetry_errors import track_restore_failure
                track_restore_failure(result, "typed")
            return tool_error(str(result.get("message") or "The earlier state could not be fully restored."),
                              hint="" if result.get("refused") else "undo_partly_restored")
        if own:
            note = ("The edit was already saved, so the whole project went back to before this request's "
                    "changes. Nothing holds those changes now: no Redo brings them back.")
        else:
            note = ("The edit was already saved, so the whole project went back to before that request, "
                    "as the chat's Undo arrow does; Redo brings it back.")
        return {"undone": True, "restored_to": f"before request {entry.run_index}", "this_request": own,
                "request": str(entry.prompt or "")[:200],
                "files_put_back": list(result.get("files_put_back") or result.get("files_restored") or [])[:20],
                "note": note}
