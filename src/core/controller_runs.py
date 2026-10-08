# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





from __future__ import annotations

import json
import time
import uuid

from qgis.PyQt.QtCore import QCoreApplication, QTimer

from . import telemetry
from . import telemetry_events as ev
from .checkpoints import KIND_BEFORE
from .context import build_context
from .controller_actions import _project_path
from .controller_shared import (
    CANCEL_GRACE_MS,
    CONTINUE_TEXT,
    RUN_SILENCE_S,
    SENDING_RECHECK_MS,
    _dump_context,
)
from .logger import log_warning
from .profile import driven_session, profile_context
from .protocol import CANCEL_SERVER_SILENT, CANCEL_STOP, Approval, Mode, RunStatus
from .security import allow_attached_paths, remember_user_text, vouched_for_thread
from .telemetry_errors import report_exception, track_plugin_error
from .threads import user_message_record


def tr(text: str) -> str:
    return QCoreApplication.translate("AgentController", text)


def _context_bytes(context: dict) -> int | None:





    if not telemetry.is_telemetry_enabled():
        return None
    try:
        return len(json.dumps(context, default=str, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError, RecursionError):
        return None


class _ControllerRuns:
    def _on_send(self, text: str, mode: str, approval: str, chips, attachments, reuse_layers: bool = False,
                 replaces: str | None = None) -> None:

        text = (text or "").strip()
        if not text:
            return
        if self._run is not None:
            self.notice.emit("info", tr("A run is in progress. Stop it or wait for it to finish."))
            return
        if not self._session.is_online:




            if self._session.state != "connecting":
                self._panel_call("set_connection_state", "connecting", "")
                self._session.connect_to_server()
            log_warning(f"Send refused, socket is {self._session.state}; the message is kept for Retry")


            key = "unsent-" + uuid.uuid4().hex
            self._runs.remember(key, {
                "run_id": "", "text": text, "mode": mode, "approval": approval,
                "chips": [c for c in (chips or []) if isinstance(c, dict)],
                "attachments": [a for a in (attachments or []) if isinstance(a, dict)]})
            self._panel_call("show_error", None, "OFFLINE",
                             tr("Not connected to TerraLab, so nothing was sent. Reconnecting now: "
                                "your message is kept, and Retry sends it once the connection is back."),
                             True, "", key)
            return
        mode = mode if mode in (Mode.ASK, Mode.AGENT) else self._settings.mode
        approval = approval if approval in Approval.ALL else self._settings.approval



        approval = self._approval(approval)


        effort = self._effort()
        example = self._example_sent(text)
        self._retry_after_restore = ("", "")
        replaces = str(replaces or "")

        reuse_layers = reuse_layers or bool(replaces)
        chips = [c for c in (chips or []) if isinstance(c, dict)]
        attachments = [a for a in (attachments or []) if isinstance(a, dict)]
        sent_attachments = [dict(a) for a in attachments]


        remember_user_text(text)
        allow_attached_paths([a.get("path") for a in attachments if a.get("path")])



        asked_chips = [dict(c) for c in chips]
        chips += (self._load_file_attachments(attachments, reuse=True) if reuse_layers
                  else self._load_file_attachments(attachments))
        new_thread = not self._thread_id
        if not self._thread_id:
            self._thread_id = self._store.create(project_path=_project_path())["id"]
            telemetry.track(ev.THREAD_CREATED)


        thread_id = self._thread_id
        vouched_for_thread(thread_id, self._store.layer_roots(thread_id),
                           lambda roots: self._store.set_layer_roots(thread_id, roots))
        run_id = uuid.uuid4().hex


        prep_began = time.perf_counter()
        try:
            context = build_context(chips, text=text, thread_id=self._thread_id)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Context build failed: {exc}")
            context = {"error": str(exc)[:200], "layers": [], "chips": chips}
        try:
            user = profile_context(self._settings, _project_path())
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Profile context failed: {exc}")
            user = {}
        if "memory_notes" in user:



            try:
                recent = self._store.recent_prompts(self._thread_id)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Recent prompts not read: {exc}")
                recent = []
            if recent:
                user["recent_prompts"] = recent
            if user.get("declined"):


                try:
                    since = self._store.conversations_since
                    user["declined"] = [{"text": d["text"], "later_conversations": since(d["at"]) if d["at"] else 0}
                                        for d in user["declined"]]
                except Exception as exc:  # noqa: BLE001
                    log_warning(f"Declined notes not counted: {exc}")
                    user.pop("declined", None)
            if driven_session():
                user["driven"] = True
        if user:
            context["user"] = user
        try:


            undone = self._executor.history.undone_requests(self._thread_id)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Undone requests not read: {exc}")
            undone = []
        if undone:
            context["undone_requests"] = undone
        prep_ms = int((time.perf_counter() - prep_began) * 1000)
        _dump_context(context, run_id)
        record = user_message_record(run_id, text, chips, attachments, mode=mode, approval=approval)
        self._runs.remember(run_id, dict(record, chips=asked_chips, attachments=sent_attachments,
                                         thread_id=self._thread_id))
        self._runs.begin({"run_id": run_id, "thread_id": self._thread_id, "mode": mode, "approval": approval,
                          "effort": effort, "text": text, "started": time.monotonic(), "cancelled": False,
                          "example": example, "replaces": replaces if self._session.edit_last_available else "",


                          "sent": (attachments, context), "resends": 0})
        self._sync_effort_live()
        began = False
        try:
            telemetry.track(ev.AGENT_RUN_STARTED, {
                "run_id": run_id, "mode": mode, "approval": approval, "effort": effort,
                "attachment_count": len(attachments), "chip_count": len(chips), "new_thread": new_thread,
                "prep_ms": prep_ms, "context_bytes": _context_bytes(context),


                **(getattr(self._session, "connect_facts", None) or {})})
            self._last_run = {"text": text, "mode": mode, "approval": approval, "chips": chips,
                              "attachments": attachments}
            self._agent_text[run_id] = ""
            self._remember_run_project(run_id)
            self._session.set_run_open(True)
            self._executor.begin_run(run_id, mode, approval, self._thread_id or "", text)
            self._panel_call("set_display_options", self._settings.explain_runs, self._settings.show_tool_details)
            self._panel_call("begin_run", run_id)

            self._panel_call("set_steer_available",
                             self._session.steer_available and self._session.unsteer_available)
            began = True
            self._panel_call("append_user_message", run_id, text, chips, attachments)
            self._store.append_message(self._thread_id, record)
            self._store.update_agent_message(self._thread_id, run_id, {"status": "running"})
            sent = self._session.send_user_message(
                run_id, self._thread_id, text, attachments, context, mode, approval, effort, example, replaces)
        except Exception as exc:  # noqa: BLE001
            self._fail_unsent_run(run_id, exc, began, dict(record, chips=asked_chips, attachments=sent_attachments))
            return
        if not sent:



            log_warning(f"Run {run_id} never left the socket ({self._session.state})")
            self._panel_call("show_error", run_id, "OFFLINE",
                             tr("The message could not be sent: the connection to TerraLab is down."),
                             True, "")
            self._finish_run(run_id, RunStatus.FAILED,
                             tr("Not connected to TerraLab. Retry once the connection is back."), {}, None)
        else:
            self._runs.sent(run_id)

            if self._run is not None and self._run["run_id"] == run_id:
                self._run.setdefault("sent_at", time.perf_counter())
            self._touch_watchdog()
        self._panel_call("set_threads", self._store.list_threads(), _project_path())
        self._panel_call("set_current_thread", self._thread_id)

    def _example_sent(self, text: str) -> str:





        slug, self._example_pick = getattr(self, "_example_pick", ""), ""
        if not slug:
            return ""
        from ..ui.use_cases import use_cases
        prompt = next((case.prompt for case in use_cases() if case.slug == slug), "")
        return slug if prompt and prompt in text else ""

    def _fail_unsent_run(self, run_id: str, exc: Exception, began: bool, record: dict) -> None:





        report_exception(exc, "send", module=__name__, run_id=run_id)


        key = run_id if began else "unsent-" + uuid.uuid4().hex
        self._runs.remember(key, dict(record, run_id=run_id if began else ""))
        message = tr("The message could not be sent. Retry, or reload the plugin if it keeps failing.")
        self._panel_call("show_error", run_id if began else None, "SEND_FAILED", message, True, "",
                         "" if began else key)
        self._finish_run(run_id, RunStatus.FAILED, message, {}, None)

    def _retry_input(self, run_id: str) -> dict | None:





        run_id = str(run_id or "")
        remembered = self._runs.record(run_id)
        if remembered is not None and remembered.get("thread_id", "") in ("", self._thread_id):
            return remembered
        if not run_id or not self._thread_id:
            return None
        thread = self._store.load(self._thread_id) or {}
        for message in reversed(thread.get("messages") or []):
            if (isinstance(message, dict) and message.get("role") == "user"
                    and message.get("run_id") == run_id and str(message.get("text") or "").strip()):
                return message
        return None

    def _on_retry(self, run_id: str, replaces: str = "") -> None:







        asked = self._retry_input(run_id)
        if asked is None:
            self.notice.emit("info", tr("This message can no longer be sent again."))
            return
        mode = asked.get("mode") if asked.get("mode") in (Mode.ASK, Mode.AGENT) else self._settings.mode
        approval = self._settings.approval
        if asked.get("approval") in Approval.ALL and approval in Approval.ALL:

            approval = min(asked["approval"], approval, key=Approval.ALL.index)
        attachments = asked.get("attachments") or []
        self._on_send(str(asked.get("text") or ""), mode, approval,
                      [dict(c) for c in asked.get("chips") or [] if isinstance(c, dict)],
                      [dict(a) for a in attachments if isinstance(a, dict)], reuse_layers=True,
                      replaces=str(replaces or ""))

    def _on_undo_retry(self, run_id: str) -> None:



        run_id = str(run_id or "")
        if self._run is not None or self._retry_input(run_id) is None:
            return
        history = self._executor.history
        thread_id = self._thread_id or ""
        before = next((e for e in reversed(history.entries(thread_id))
                       if e.run_id == run_id and e.kind == KIND_BEFORE), None)
        if before is None or not before.available:
            self._retry_replacing(run_id)
            return
        self._retry_after_restore = (before.id, run_id)
        self._on_restore(before.id, False)
        current = history.current(thread_id)
        if self._retry_after_restore[0] and current is not None and current.id == before.id:
            self._restored_for_retry(before.id)

    def _restored_for_retry(self, checkpoint_id: str) -> None:

        pending, run_id = self._retry_after_restore
        if not pending or pending != checkpoint_id:
            return
        self._retry_after_restore = ("", "")
        QTimer.singleShot(0, lambda: self._retry_replacing(run_id))

    def _retry_replacing(self, run_id: str) -> None:
        self._on_retry(run_id, replaces=run_id)

    def _on_continue(self, run_id: str) -> None:









        asked = self._retry_input(run_id) or self._last_run or {}
        mode = asked.get("mode") if asked.get("mode") in (Mode.ASK, Mode.AGENT) else self._settings.mode
        approval = self._settings.approval
        if asked.get("approval") in Approval.ALL and approval in Approval.ALL:
            approval = min(asked["approval"], approval, key=Approval.ALL.index)
        self._on_send(CONTINUE_TEXT, mode, approval, [], [], replaces="")

    def _on_steer(self, run_id: str, steer_id: str, text: str) -> None:


        text = (text or "").strip()
        run = self._run
        if not text or run is None or run.get("run_id") != run_id or not self._session.steer_available:
            self._panel_call("steer_refused", steer_id)
            return
        remember_user_text(text)
        thread_id = str(run.get("thread_id") or self._thread_id or "")
        self._steers.setdefault(run_id, {})[steer_id] = (text, thread_id)
        if not self._session.send_steer(run_id, steer_id, text):
            log_warning(f"Steer for run {run_id} did not leave; it waits in the queue for the run's end")

    def _on_unsteer(self, run_id: str, steer_id: str) -> None:


        if (self._steers.get(run_id) or {}).get(steer_id) is not None:


            self._session.send_unsteer(run_id, steer_id)

    def _on_queue_send(self, text: str, chips, attachments) -> None:


        if not self._session.is_online:
            return
        self._on_send(text, "", "", chips, attachments, replaces="")

    def _on_steer_ack(self, run_id: str, steer_id: str, taken: bool) -> None:
        steer = (self._steers.get(run_id) or {}).pop(steer_id, None)
        if not taken:
            self._panel_call("steer_refused", steer_id)
            return
        text, thread_id = steer if steer is not None else ("", "")
        if not text:
            return
        self._panel_call("append_steer", run_id, steer_id, text)

        self._executor.drop_run_grant(run_id)

        thread_id = thread_id or str(self._thread_id or "")
        if thread_id:
            self._store.append_steer(thread_id, run_id, text)

    def _flush_steers(self, run_id: str) -> None:

        for steer_id in getattr(self, "_steers", {}).pop(run_id, None) or {}:
            self._panel_call("steer_refused", steer_id)

    def _on_stop(self, run_id: str) -> None:
        again = self._runs.cancelled()
        run = self._runs.stop(run_id)
        if run is None:
            return
        if again:


            self._end_run_locally()
            return

        self._resend_timer.stop()
        self._lost_timer.stop()
        heard = False
        try:



            heard = self._session.send_cancel(run["run_id"], CANCEL_STOP) and self._session.is_online
            self._executor.cancel_run(run["run_id"])
            self._cancel_proposals(run["run_id"])
            self._panel_call("set_status_line", run["run_id"], tr("Stopping..."))
        except Exception as exc:  # noqa: BLE001
            report_exception(exc, "stop", module=__name__, run_id=run["run_id"])
        finally:
            if heard:
                self._end_timer.start(CANCEL_GRACE_MS)
            else:
                self._end_run_locally()

    def _end_run_locally(self) -> None:
        run = self._run
        if run is None:
            return
        cancelled = bool(run.get("cancelled"))
        status = RunStatus.CANCELLED if cancelled else RunStatus.FAILED




        summary = str(run.get("error_message") or "")
        if not summary:



            summary = (tr("Stopped before the agent answered.") if cancelled
                       else tr("The run ended without a summary from TerraLab."))
        self._finish_run(run["run_id"], status, summary, {}, None)

    def _resend_run(self, grace: int = RUN_SILENCE_S) -> None:





        run = self._runs.take_resend()
        if run is None:
            return
        self._resend_timer.stop()
        if not self._session.is_online:

            self._runs.resend_later()



            self._touch_watchdog()
            return
        attachments, context = run.get("sent") or ([], {})
        try:
            sent = self._session.send_user_message(run["run_id"], run["thread_id"], run["text"], attachments,
                                                   context, run["mode"], run["approval"], run["effort"],
                                                   run.get("example", ""), run.get("replaces", ""))
        except Exception as exc:  # noqa: BLE001
            self._fail_unsent_run(run["run_id"], exc, True, self._runs.record(run["run_id"]) or dict(  # noqa: C408
                text=run["text"], mode=run["mode"], approval=run["approval"], chips=[], attachments=[]))
            return
        if sent:
            self._panel_call("set_status_line", run["run_id"], tr("Thinking..."))
            self._touch_watchdog(grace)
        else:
            self._runs.resend_later()



    def _touch_watchdog(self, seconds: int = RUN_SILENCE_S) -> None:
        if not self._runs.server_owes_frame():
            return
        self._run.pop("silence_ms", None)
        self._watchdog.start(int(seconds) * 1000)

    def _heard(self, run_id: str) -> None:






        run = self._run
        first = run is not None and run["run_id"] == run_id and not run.get("heard")
        if self._runs.heard(run_id):
            self._resend_timer.stop()
        if first and run.get("replaces"):



            self._panel_call("drop_turn", run["replaces"])
            self._store.drop_run(run["thread_id"], run["replaces"])
        if first and run.get("sent_at") is not None:

            run.setdefault("first_event_ms", int((time.perf_counter() - run["sent_at"]) * 1000))

    def _pause_watchdog(self) -> None:

        self._watchdog.stop()

    def _on_resume_outcome_missing(self) -> None:

        run = self._run
        if run is None:
            return
        run_id = run["run_id"]
        if self._runs.cancelled():
            self._end_run_locally()
            return
        message = tr("The connection dropped and TerraLab no longer has this run. You can retry it.")
        try:
            log_warning(f"Run {run_id[:8]}: no outcome after the refused resume, ending the run locally")
            self._panel_call("show_error", run_id, "LOST", message, True, "")
            track_plugin_error("resume", "LOST", run_id)
            telemetry.track(ev.CONNECTION_FAILED, {"stage": "resume", "error_code": "LOST",
                                                   "duration_ms": self._run_age_ms(run),
                                                   **self._connection_facts()})
        finally:
            self._finish_run(run_id, RunStatus.FAILED, message, {}, None)

    def _on_server_silent(self) -> None:
        run = self._run
        if run is None:
            return
        if self._session.is_online and self._session.still_sending():



            run.setdefault("silence_ms", self._watchdog.interval())
            self._watchdog.start(SENDING_RECHECK_MS)
            return



        held = run.pop("silence_ms", 0)
        if held and self._session.is_online:
            self._watchdog.start(held)
            return
        if self._session.is_online:
            code = "TIMEOUT"
            message = tr("TerraLab stopped answering. The run was ended, you can retry it.")
        else:


            code = "OFFLINE"
            message = tr(
                "The connection to TerraLab was lost and did not come back. "
                "The run was ended, you can retry it once you are online."
            )
        try:
            log_warning(f"Run {run['run_id'][:8]}: nothing from the server for too long, ending it locally")
            self._session.send_cancel(run["run_id"], CANCEL_SERVER_SILENT)
            self._panel_call("show_error", run["run_id"], code, message, True, "")
            track_plugin_error("watchdog", code, run["run_id"])
            telemetry.track(
                ev.CONNECTION_FAILED, {"stage": "run", "error_code": code, "duration_ms": self._run_age_ms(run),
                                       **self._connection_facts()}
            )
        finally:
            self._finish_run(run["run_id"], RunStatus.FAILED, message, {}, None)

    @staticmethod
    def _run_age_ms(run: dict | None) -> int | None:
        started = (run or {}).get("started")
        return int((time.monotonic() - started) * 1000) if started else None
