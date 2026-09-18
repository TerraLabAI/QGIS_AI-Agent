# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Sending a message and the run it opens: retries, Stop and both watchdogs."""




from __future__ import annotations

import time
import uuid

from qgis.PyQt.QtCore import QCoreApplication

from . import telemetry
from . import telemetry_events as ev
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
from .profile import profile_context
from .protocol import Approval, Mode, RunStatus
from .security import allow_attached_paths, remember_user_text
from .telemetry_errors import report_exception, track_plugin_error
from .threads import user_message_record


def tr(text: str) -> str:
    return QCoreApplication.translate("AgentController", text)


class _ControllerRuns:
    def _on_send(self, text: str, mode: str, approval: str, chips, attachments, reuse_layers: bool = False) -> None:
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
                             tr("Not connected to the agent service, so nothing was sent. Reconnecting now: "
                                "your message is kept, and Retry sends it once the connection is back."),
                             True, "", key)
            return
        mode = mode if mode in (Mode.ASK, Mode.AGENT) else self._settings.mode
        approval = approval if approval in Approval.ALL else self._settings.approval



        approval = self._approval(approval)


        effort = self._effort()
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
        run_id = uuid.uuid4().hex
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
        if user:
            context["user"] = user
        _dump_context(context, run_id)
        record = user_message_record(run_id, text, chips, attachments, mode=mode, approval=approval)
        self._runs.remember(run_id, dict(record, chips=asked_chips, attachments=sent_attachments,
                                         thread_id=self._thread_id))
        self._runs.begin({"run_id": run_id, "thread_id": self._thread_id, "mode": mode, "approval": approval,
                          "effort": effort, "text": text, "started": time.monotonic(), "cancelled": False,


                          "sent": (attachments, context), "resends": 0})
        began = False
        try:
            telemetry.track(ev.AGENT_RUN_STARTED, {
                "run_id": run_id, "mode": mode, "approval": approval, "effort": effort,
                "attachment_count": len(attachments), "chip_count": len(chips), "new_thread": new_thread})
            if telemetry.first_run_recorded():
                telemetry.track(ev.FIRST_RUN_MILESTONE)
            self._last_run = {"text": text, "mode": mode, "approval": approval, "chips": chips,
                              "attachments": attachments}
            self._agent_text[run_id] = ""
            self._remember_run_project(run_id)
            self._session.set_run_open(True)
            self._executor.begin_run(run_id, mode, approval, self._thread_id or "", text)
            self._panel_call("set_display_options", self._settings.explain_runs, self._settings.show_tool_details)
            self._panel_call("begin_run", run_id)
            began = True
            self._panel_call("append_user_message", run_id, text, chips, attachments)
            self._store.append_message(self._thread_id, record)
            self._store.update_agent_message(self._thread_id, run_id, {"status": "running"})
            sent = self._session.send_user_message(
                run_id, self._thread_id, text, attachments, context, mode, approval, effort)
        except Exception as exc:  # noqa: BLE001 - a run that never left must not hold Send
            self._fail_unsent_run(run_id, exc, began, dict(record, chips=asked_chips, attachments=sent_attachments))
            return
        if not sent:



            log_warning(f"Run {run_id} never left the socket ({self._session.state})")
            self._panel_call("show_error", run_id, "OFFLINE",
                             tr("The message could not be sent: the connection to the agent service is down."),
                             True, "")
            self._finish_run(run_id, RunStatus.FAILED,
                             tr("Not connected to the agent service. Retry once the connection is back."), {}, None)
        else:
            self._runs.sent(run_id)
            self._touch_watchdog()
        self._panel_call("set_threads", self._store.list_threads(), _project_path())
        self._panel_call("set_current_thread", self._thread_id)

    def _on_suggestion(self, text: str) -> None:
        self._on_send(text, self._settings.mode, self._settings.approval, [], [])

    def _fail_unsent_run(self, run_id: str, exc: Exception, began: bool, record: dict) -> None:
        """A step of ``_on_send`` raised before the message left: the run closes here."""




        report_exception(exc, "send", module=__name__, run_id=run_id)


        key = run_id if began else "unsent-" + uuid.uuid4().hex
        self._runs.remember(key, dict(record, run_id=run_id if began else ""))
        message = tr("The message could not be sent. Retry, or reload the plugin if it keeps failing.")
        self._panel_call("show_error", run_id if began else None, "SEND_FAILED", message, True, "",
                         "" if began else key)
        self._finish_run(run_id, RunStatus.FAILED, message, {}, None)

    def _retry_input(self, run_id: str) -> dict | None:
        """What Retry on the card of ``run_id`` sends, or None when nothing can be."""




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

    def _on_retry(self, run_id: str) -> None:
        """Retry sends again the message of the card it sits on."""






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
                      [dict(a) for a in attachments if isinstance(a, dict)], reuse_layers=True)

    def _on_continue(self, run_id: str) -> None:
        """"Continue" on a budget stop: one word in the same thread, not the message again."""








        asked = self._retry_input(run_id) or self._last_run or {}
        mode = asked.get("mode") if asked.get("mode") in (Mode.ASK, Mode.AGENT) else self._settings.mode
        approval = self._settings.approval
        if asked.get("approval") in Approval.ALL and approval in Approval.ALL:
            approval = min(asked["approval"], approval, key=Approval.ALL.index)
        self._on_send(CONTINUE_TEXT, mode, approval, [], [])

    def _on_stop(self, run_id: str) -> None:
        run = self._runs.stop(run_id)
        if run is None:
            return

        self._resend_timer.stop()
        self._lost_timer.stop()
        try:
            self._session.send_cancel(run["run_id"])
            self._executor.cancel_run(run["run_id"])
            self._cancel_proposals(run["run_id"])
            self._panel_call("set_status_line", run["run_id"], tr("Stopping..."))
        except Exception as exc:  # noqa: BLE001 - reported; the grace below still closes the run
            report_exception(exc, "stop", module=__name__, run_id=run["run_id"])
        finally:
            self._end_timer.start(CANCEL_GRACE_MS)

    def _end_run_locally(self) -> None:
        run = self._run
        if run is None:
            return
        cancelled = bool(run.get("cancelled"))
        status = RunStatus.CANCELLED if cancelled else RunStatus.FAILED




        summary = str(run.get("error_message") or "")
        if not summary:



            summary = (tr("Stopped before the agent answered.") if cancelled
                       else tr("The run ended without a summary from the agent service."))
        self._finish_run(run["run_id"], status, summary, {}, None)

    def _resend_run(self, grace: int = RUN_SILENCE_S) -> None:
        """Send the open run again, unchanged."""




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
                                                   context, run["mode"], run["approval"], run["effort"])
        except Exception as exc:  # noqa: BLE001 - a resend that raises closes the run, it never holds Send
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
        """A frame for the open run arrived: the server holds the run."""





        if self._runs.heard(run_id):
            self._resend_timer.stop()

    def _pause_watchdog(self) -> None:
        """A tool runs here or a card waits for the user: the server is waiting on us."""
        self._watchdog.stop()

    def _on_resume_outcome_missing(self) -> None:
        """The grace ran out: the server said nothing about the run it does not hold."""
        run = self._run
        if run is None:
            return
        run_id = run["run_id"]
        if self._runs.cancelled():
            self._end_run_locally()
            return
        message = tr("The connection dropped and the agent service no longer has this run. You can retry it.")
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
            message = tr("The agent service stopped answering. The run was ended, you can retry it.")
        else:


            code = "OFFLINE"
            message = tr(
                "The connection to the agent service was lost and did not come back. "
                "The run was ended, you can retry it once you are online."
            )
        try:
            log_warning(f"Run {run['run_id'][:8]}: nothing from the server for too long, ending it locally")
            self._session.send_cancel(run["run_id"])
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
