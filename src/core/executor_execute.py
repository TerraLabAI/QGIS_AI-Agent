# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




from __future__ import annotations

import time
import traceback

from qgis.PyQt.QtCore import QCoreApplication

from . import background, crash_note, stalls
from .executor_code import CODE_TOOL
from .logger import log, log_warning
from .protocol import ClientErrorCode as Err
from .protocol import Danger

try:
    from ..tools._layers import pinned_layer_gone
except ImportError:
    def pinned_layer_gone(_args, _pins):
        return None


def tr(text: str) -> str:
    return QCoreApplication.translate("ToolExecutor", text)


_BACKUP_FACT = "Nothing was changed. The next call that changes data tries the copy again."


class _ExecutorExecute:


    def _execute(self, call: dict) -> None:
        tool_call_id = str(call.get("tool_call_id"))
        if tool_call_id in self._executing or self._closed:
            return


        self._executing.add(tool_call_id)
        try:
            self._prepare_and_execute(call)
        except Exception as exc:  # noqa: BLE001
            self._fail_unanswered(call, exc)

    def _fail_unanswered(self, call: dict, exc: Exception) -> None:










        tool_call_id = str(call.get("tool_call_id"))
        if self._table.get(tool_call_id) is not None or tool_call_id in self._answered:
            self._executing.discard(tool_call_id)
            log_warning(f"{call.get('name')}: {type(exc).__name__} after its answer was sent: {exc}")
            return
        self._fail(call, Err.EXECUTION_FAILED, f"{type(exc).__name__}: {exc}",
                   "The project may be partly changed.")

    def _prepare_and_execute(self, call: dict) -> None:
        run_id = str(call.get("run_id") or "")
        name, args, danger = str(call.get("name")), call.get("args") or {}, call.get("danger", Danger.READ)
        guard = self._crs_guard(name, args)
        if guard is not None:
            self._fail(call, Err.CRS_GUARD, guard[0], guard[1])
            return

        fresh = not getattr(self._snapshots.get(run_id), "captured", False)
        if name == CODE_TOOL:
            self._code_note_edit_sessions(call)
        if danger != Danger.READ:
            try:
                snapshot = self._prepare_snapshot(run_id, name, args, danger, call.get("overwrites") or [])
            except OSError as exc:


                self._fail(call, Err.EXECUTION_FAILED, f"The backup failed; the operation was not started. {exc}",
                           _BACKUP_FACT)
                return
            if name == CODE_TOOL and not self._code_restore_point(call, fresh):
                self._code_no_point(call)
                return
            if snapshot is not None and snapshot.has_pending_copies() and self._copy_then_run(call, snapshot):
                return
        self._run_prepared(call)

    def _copy_then_run(self, call: dict, snapshot) -> bool:


        name = str(call.get("name") or "")
        tool_call_id = str(call.get("tool_call_id"))
        run_id = str(call.get("run_id") or "")

        def done(_result, error_text: str):
            self._background.pop(tool_call_id, None)
            self._inflight.pop(tool_call_id, None)
            if self._closed or tool_call_id in self._answered:
                return
            if error_text:
                cause = error_text.split(chr(10), 1)[0]
                log_warning(f"Backup before {name} failed: {cause}")
                if not self._closed:
                    self._fail(call, Err.EXECUTION_FAILED,
                               f"The backup failed; the operation was not started. {cause}", _BACKUP_FACT)
                return
            try:
                self._run_prepared(call)
            except Exception as exc:  # noqa: BLE001
                self._fail_unanswered(call, exc)

        task = background.run_off_thread(f"AI Agent: backup before {name}", snapshot.run_pending_copies, done)
        if task is None:
            snapshot.run_pending_copies()
            return False
        self._background[tool_call_id] = (run_id, task, call)
        self._inflight[tool_call_id] = (run_id, name, time.monotonic(), True)
        stalls.mark(f"backup off-thread before {name}")
        return True

    def _run_prepared(self, call: dict) -> None:








        name = str(call.get("name") or "")
        tool = self._registry.get_tool(name)
        prepare = getattr(tool, "prepare", None)
        work = None
        if callable(prepare) and str(call.get("run_id") or "") not in self._cancelled:
            try:
                with stalls.probe(f"prepare.{name}"):
                    work = prepare(call.get("args") or {})
            except Exception as exc:  # noqa: BLE001
                log_warning(f"{name}: preparation skipped: {exc}")
        if not callable(work):
            self._execute_now(call)
            return
        tool_call_id = str(call.get("tool_call_id"))
        run_id = str(call.get("run_id") or "")

        def done(finish, error_text: str):
            self._background.pop(tool_call_id, None)
            self._inflight.pop(tool_call_id, None)
            try:
                if self._closed or tool_call_id in self._answered:
                    return
                if error_text:
                    log_warning(f"{name}: preparation failed, running without it: {error_text.splitlines()[0]}")
                elif callable(finish):
                    try:
                        note = finish()
                        if note:
                            call["prepared"] = note
                    except Exception as exc:  # noqa: BLE001
                        log_warning(f"{name}: preparation not applied: {exc}")
                try:
                    self._execute_now(call)
                except Exception as exc:  # noqa: BLE001
                    self._fail_unanswered(call, exc)
            finally:


                release = getattr(finish, "release", None)
                if callable(release):
                    try:
                        release()
                    except Exception as exc:  # noqa: BLE001
                        log_warning(f"{name}: preparation not released: {exc}")

        task = background.run_off_thread(f"AI Agent: prepare {name}", work, done)
        if task is None:
            self._execute_now(call)
            return
        self._background[tool_call_id] = (run_id, task, call)
        self._inflight[tool_call_id] = (run_id, name, time.monotonic(), True)
        stalls.mark(f"prepare off-thread before {name}")

    def _execute_now(self, call: dict) -> None:
        if self._closed or str(call.get("tool_call_id")) in self._answered:
            return
        run_id = str(call.get("run_id") or "")
        name, args, danger = str(call.get("name")), call.get("args") or {}, call.get("danger", Danger.READ)
        if run_id in self._cancelled:
            self._fail(call, Err.CANCELLED, tr("The run was cancelled by the user."),
                       "The user stopped the run.")
            return


        gone = pinned_layer_gone(args, call.get("layer_pins") or {})
        if gone:
            self._fail(call, Err.LAYER_NOT_FOUND, gone["_error"], gone["suggestion"])
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





        crash_note.write(run_id, tool_call_id, getattr(self._session, "session_id", "") or "", name, args)
        try:
            with stalls.probe(f"tool.{name}"), self._code_context(call), background.calling(tool_call_id):
                result = self._registry.execute(name, args)
        except Exception as exc:  # noqa: BLE001
            result = {"_error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()[-2000:]}
        finally:
            crash_note.clear()
        if self._hold(call, result, started):
            return
        self._deliver(call, result, started)

    def _start_background(self, call: dict, name: str, args: dict, started: float) -> bool:







        if not self._background_ok(name, args):
            return False
        if call.get("overwrites"):



            return False
        tool_call_id = str(call.get("tool_call_id"))


        context = self._code_context(call)

        def work():
            with context, background.calling(tool_call_id):
                return self._registry.execute(name, args)

        def done(result, error_text: str):
            self._background.pop(tool_call_id, None)
            if error_text:
                result = {"_error": error_text.split("\n", 1)[0], "traceback": error_text}
            try:
                self._deliver(call, result, started)
            except Exception as exc:  # noqa: BLE001
                self._inflight.pop(tool_call_id, None)
                self._fail_unanswered(call, exc)


        task = background.run_off_thread(f"AI Agent: {name}", work, done)
        if task is None:
            return False
        self._background[tool_call_id] = (str(call.get("run_id") or ""), task, call)


        self._inflight[tool_call_id] = (str(call.get("run_id") or ""), name, started, True)
        return True

    def _background_ok(self, name: str, args: dict) -> bool:



        tool = self._registry.get_tool(name)
        flag = getattr(tool, "background", False)
        if callable(flag):
            try:
                return bool(flag(args))
            except Exception as exc:  # noqa: BLE001
                log_warning(f"background predicate for {name} failed: {exc}")
                return False
        return bool(flag)
