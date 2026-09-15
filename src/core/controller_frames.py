# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every frame the session sends, and the ending each one drives."""



from __future__ import annotations

import re
import time

from qgis.PyQt.QtCore import QCoreApplication

from ..api.account import usage_to_runs
from . import telemetry
from . import telemetry_events as ev
from .controller_actions import _project_path
from .controller_shared import (
    BUSY_RESEND_MS,
    BUSY_RESENDS,
    CANCEL_GRACE_MS,
    MAX_AGENT_TEXT,
    PROPOSAL_TOOLS,
    RESUME_GRACE_S,
    RESUME_OUTCOME_MS,
    RUN_PROJECTS_KEPT,
    RUN_SILENCE_S,
    strip_followups,
)
from .logger import log, log_warning
from .plan import set_plan_features
from .profile import project_key
from .protocol import Decision, RunStatus, ServerErrorCode
from .run_report import VERIFY_RUN
from .run_state import AWAIT_OUTCOME, AWAIT_RESUME, RESEND
from .snapshot import changed_layer_items, describe_diff
from .snapshot_report import run_change_items
from .telemetry_errors import report_exception, track_plugin_error


def tr(text: str) -> str:
    return QCoreApplication.translate("AgentController", text)


class _ControllerFrames:


    _session_error_shown: tuple | None = None



    def _on_state_changed(self, state: str, detail: str) -> None:
        self._panel_call("set_connection_state", state, detail)
        if state == "offline" and detail and self._run is None:


            telemetry.track(ev.CONNECTION_FAILED, {"stage": "connect", "error_code": "OFFLINE", "duration_ms": None})
        if state == "signed_out" and self._account.has_activation_key:

            self._account.refresh_activation_async(force=True)
        if state in ("offline", "connecting") and self._run is not None:

            self._panel_call("set_status_line", self._run["run_id"], tr("Connection lost. Reconnecting..."))

    def _on_session_started(self, session: dict) -> None:
        self._session_error_shown = None
        resumed = bool(session.get("resumed"))
        action = self._runs.session_started(session.get("resumed"))
        run = self._run
        if action == RESEND:




            log_warning(f"Run {run['run_id'][:8]}: sending the message again on the new session")
            self._resend_run(RESUME_GRACE_S if resumed else RUN_SILENCE_S)
        elif action == AWAIT_OUTCOME:






            run_id = run["run_id"]
            log_warning(f"Run {run_id[:8]}: the server did not resume the session, waiting for its outcome")
            self._panel_call("set_status_line", run_id, tr("Reconnected. Checking how the run ended..."))
            self._replay_run = run_id
            self._watchdog.stop()
            self._lost_timer.start(RESUME_OUTCOME_MS)
        elif action == AWAIT_RESUME:
            self._panel_call("set_status_line", run["run_id"],
                             tr("Reconnected. Waiting for the agent service to resume the run..."))
            self._touch_watchdog(RESUME_GRACE_S)
        self._panel_call("set_model_label", str(session.get("model_label") or ""))



        set_plan_features(session.get("plan_features"))



        self._panel_call("set_paid_plan", not bool(session.get("is_free_tier", True)))





        self._panel_call("set_effort", self._settings.effort)


        self._panel_call("show_notice", session.get("notice") if isinstance(session.get("notice"), dict) else {})
        self._panel_call("set_permission_mode", self._approval())


        rows = session.get("connectors")
        if isinstance(rows, list) and rows:
            try:
                from ..ui.shared import set_connectors

                set_connectors(rows)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Connector list refused: {exc}")


        try:
            from . import catalog

            catalog.apply_session(session)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Catalog lists refused: {exc}")



        group_rows = session.get("use_case_groups")
        if isinstance(group_rows, list) and group_rows:
            try:
                from ..ui.use_cases import set_served_groups

                set_served_groups(group_rows)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Use case group list refused: {exc}")
        case_rows = session.get("use_cases")
        if isinstance(case_rows, list) and case_rows:
            try:
                from ..ui.use_cases import set_served_cases

                set_served_cases(case_rows)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Use case list refused: {exc}")



        plugin_rows = session.get("qgis_plugins")
        if isinstance(plugin_rows, list) and plugin_rows:
            try:
                from ..ui.shared import set_qgis_plugins

                set_qgis_plugins(plugin_rows)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"QGIS plugin list refused: {exc}")
            try:



                from .qgis_plugins import warm_metadata

                warm_metadata()
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Plugin metadata not warmed: {exc}")


        hidden = session.get("qgis_plugins_hidden")
        if isinstance(hidden, list) and hidden:
            try:
                from ..ui.shared import set_hidden_plugins

                set_hidden_plugins(hidden)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Hidden plugin list refused: {exc}")
        quota = session.get("quota") if isinstance(session.get("quota"), dict) else None
        if quota:
            self._on_usage(quota)
        log(f"Session {str(session.get('session_id') or '')[:8]} online, model {session.get('model_label')}")

    def _on_usage(self, usage: dict) -> None:
        if not isinstance(usage, dict):
            return
        try:
            self._panel_call("set_usage", int(usage.get("runs_used") or 0), int(usage.get("runs_limit") or 0),
                             str(usage.get("period_end") or ""), usage.get("is_subscriber"))
        except (TypeError, ValueError):
            pass

    def _on_website_usage(self, payload: object) -> None:
        """Map the website /usage payload (counted in images_* for every product) to runs."""
        if not isinstance(payload, dict):
            return
        self._on_usage(usage_to_runs(payload))

    def _on_token(self, run_id: str, text: str) -> None:
        if run_id not in self._agent_text:

            return
        if self._replay_run == run_id:


            self._replay_run = None
            self._agent_text[run_id] = ""
            self._answer_start[run_id] = 0
            self._text_break.pop(run_id, None)
            self._panel_optional("reset_answer", run_id)
        self._heard(run_id)
        self._touch_watchdog()
        self._panel_call("append_token", run_id, text)
        stored = self._agent_text[run_id]
        if len(stored) < MAX_AGENT_TEXT:

            if self._text_break.pop(run_id, False) and stored and not stored.endswith("\n"):
                text = "\n\n" + text.lstrip()
            self._agent_text[run_id] += text

    def _on_plan(self, run_id: str, steps: list) -> None:
        if run_id not in self._agent_text:
            return
        self._heard(run_id)
        self._touch_watchdog()
        self._panel_call("set_plan", run_id, steps)
        self._answer_start[run_id] = len(self._agent_text.get(run_id, ""))
        if self._thread_id:
            self._store.update_agent_message(self._thread_id, run_id, {"plan": steps})

    def _on_plan_update(self, run_id: str, step_id: str, state: str) -> None:
        if run_id not in self._agent_text:
            return
        self._heard(run_id)
        self._touch_watchdog()
        self._panel_call("update_plan_step", run_id, step_id, state)

    def _on_status_line(self, run_id: str, text: str) -> None:



        if run_id not in self._agent_text:
            return
        self._heard(run_id)
        self._touch_watchdog()
        self._panel_call("set_status_line", run_id, text)

    def _on_tool_call(self, call: dict) -> None:
        tool_call_id = str(call.get("tool_call_id") or "")
        run_id = str(call.get("run_id") or "")
        if run_id and run_id not in self._agent_text:


            log(f"Run {run_id[:8]} tool_call {tool_call_id[:8]} after the run ended, no card")
            self._executor.handle_tool_call(call)
            return
        self._heard(run_id)
        self._touch_watchdog()
        if call.get("name") == VERIFY_RUN:

            self._executor.handle_tool_call(call)
            return
        if not self._runs.open_call(tool_call_id, run_id) and (
                call.get("name") not in PROPOSAL_TOOLS or tool_call_id not in self._proposals):





            self._executor.handle_tool_call(call)
            return
        self._call_runs[tool_call_id] = run_id
        self._call_names[tool_call_id] = (str(call.get("name") or ""), str(call.get("danger") or "read"))
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        if call.get("name") in PROPOSAL_TOOLS:

            self._on_proposal(call)
            return
        if call.get("name") != "ask_user":
            self._panel_call("add_tool_call", tool_call_id, run_id, str(call.get("name") or ""), args,
                             str(call.get("danger") or "read"), str(call.get("sentence") or ""))
        else:
            self._question_args[tool_call_id] = args
        self._text_break[run_id] = True
        self._answer_start[run_id] = len(self._agent_text.get(run_id, ""))
        if self._thread_id:
            self._store.append_tool_call(self._thread_id, run_id, {
                "tool_call_id": tool_call_id, "name": call.get("name"), "args": args,
                "danger": call.get("danger"), "sentence": call.get("sentence"), "ok": None})
        self._executor.handle_tool_call(call)

    def _on_tool_started(self, call: dict) -> None:
        self._pause_watchdog()
        self._panel_call("set_status_line", str(call.get("run_id") or ""),
                         str(call.get("sentence") or call.get("name") or ""))

    def _on_tool_finished(self, tool_call_id: str, ok: bool, summary: str, duration: float, detail: str,
                          result=None) -> None:


        self._panel_call("finish_tool_call", tool_call_id, ok, summary, duration, detail, result)
        run_id = self._runs.close_call(tool_call_id)
        self._call_names.pop(tool_call_id, None)
        self._touch_watchdog()
        if self._thread_id and run_id:
            self._store.append_tool_call(self._thread_id, run_id, {
                "tool_call_id": tool_call_id, "ok": ok, "summary": summary, "duration_s": round(duration, 2)})

    def _on_permission_needed(self, tool_call_id: str, run_id: str, sentence: str, args) -> None:
        name, danger = self._call_names.get(tool_call_id, ("", "read"))
        telemetry.track(ev.AGENT_PERMISSION_ASKED, {"run_id": run_id, "tool": name, "danger": danger})
        self._runs.wait_user(tool_call_id)
        self._pause_watchdog()
        self._panel_call("ask_permission", tool_call_id, run_id, sentence, args)

    def _on_question_needed(self, tool_call_id: str, run_id: str, question: str, options,
                            allow_free_text: bool, recommended: int = -1, why: str = "") -> None:
        telemetry.track(ev.AGENT_QUESTION_ASKED, {"run_id": run_id, "options": len(options or [])})
        self._runs.wait_user(tool_call_id)
        self._pause_watchdog()



        multiple = bool(self._question_args.pop(tool_call_id, {}).get("multiple"))
        extra = (multiple,) if multiple else ()
        self._panel_call("ask_question", tool_call_id, run_id, question, options, allow_free_text,
                         int(recommended), str(why or ""), self._settings.question_timeout_s, *extra)

    def _on_question_answered(self, tool_call_id: str, answer: str) -> None:
        telemetry.track(ev.AGENT_QUESTION_ANSWERED, {"skipped": not answer})
        self._runs.user_answered(tool_call_id)
        self._touch_watchdog()
        self._executor.on_question_answered(tool_call_id, answer)

    def _on_question_auto_answered(self, tool_call_id: str, seconds: int) -> None:

        telemetry.track(ev.AGENT_QUESTION_AUTO_ANSWERED, {
            "run_id": self._call_runs.get(tool_call_id, ""), "seconds": int(seconds)})

    def _on_permission_decided(self, tool_call_id: str, decision: str, edits: object = None) -> None:
        """The user's answer to a permission card, and the values they corrected first."""










        name, danger = self._call_names.get(tool_call_id, ("", "read"))
        edited = bool(isinstance(edits, dict) and edits)
        telemetry.track(ev.AGENT_PERMISSION_DECIDED, {
            "run_id": self._call_runs.get(tool_call_id, ""), "tool": name, "danger": danger,
            "decision": decision, "edited": edited})
        self._runs.user_answered(tool_call_id)
        if decision == Decision.DENY:
            self._close_call(tool_call_id)
        self._touch_watchdog()



        self._panel_call("resolve_permission", tool_call_id, decision)
        self._executor.on_permission_decided(tool_call_id, decision, edits if edited else None)

    def _close_call(self, tool_call_id: str) -> None:
        """Forget a call that ends without a result."""







        self._runs.close_call(tool_call_id)
        self._call_names.pop(tool_call_id, None)

    def _on_permission_resolved(self, tool_call_id: str, decision: str) -> None:
        """The executor denies a card by itself only in ``cancel_run`` (Stop, or the project closed), so the panel reads it as stopped; the user's."""


        self._runs.user_answered(tool_call_id)
        if decision == Decision.DENY:
            self._close_call(tool_call_id)
            self._touch_watchdog()
            self._panel_call("resolve_permission", tool_call_id, decision, "stopped")
        else:
            self._panel_call("resolve_permission", tool_call_id, decision)

    def _on_run_blocked(self, run_id: str, sentence: str) -> None:
        """QGIS froze during the open run: the panel says so too."""





        run = self._run
        if run is None or run["run_id"] != run_id:
            return
        match = re.search(r"(\d+) seconds", str(sentence or ""))
        if match:
            text = tr("QGIS stopped responding for {n} seconds during this run.").format(n=match.group(1))
        else:
            text = tr("QGIS stopped responding for a while during this run.")
        self._panel_optional("note_run_blocked", run_id, text)

    def _on_run_end(self, run_id: str, status: str, summary: str, usage: dict, verification) -> None:
        if run_id not in self._agent_text:


            log(f"Run {run_id[:8]} run_end ignored: already finished")
            return
        if isinstance(usage, dict) and "tool_calls" in usage:

            try:
                self._panel_optional("set_run_counts", run_id, int(usage.get("tool_calls") or 0),
                                     int(usage.get("messages") or 0))
            except (TypeError, ValueError):
                pass
        self._finish_run(run_id, status, summary, usage, verification)

    def _finish_run(self, run_id: str, status: str, summary: str, usage: dict, verification) -> None:
        """Close the run, whatever the steps of its ending do."""






        self._end_timer.stop()
        self._resend_timer.stop()
        self._lost_timer.stop()
        self._replay_run = None
        self._watchdog.stop()
        self._diff_timer.stop()
        closing = self._runs.owns(run_id)
        ended: list = []
        try:
            self._session.set_run_open(False)
            self._wind_up_run(run_id, status, summary, usage, verification, ended)
        except Exception as exc:  # noqa: BLE001 - reported; the run still closes below
            report_exception(exc, "finish_run", module=__name__, run_id=run_id)
        finally:
            if not ended:
                self._panel_call("end_run", run_id, status, summary, usage if isinstance(usage, dict) else {}, None)
            if closing and "stored" not in ended:




                self._store_ending(run_id, status, summary, usage)
            self._agent_text.pop(run_id, None)
            self._answer_start.pop(run_id, None)
            self._text_break.pop(run_id, None)
            if closing:

                for tool_call_id in self._runs.finish(run_id):
                    self._call_names.pop(tool_call_id, None)
                    self._question_args.pop(tool_call_id, None)
                self._release_composer()

    def _release_composer(self) -> None:
        """Send usable again even when the panel's end_run failed half way."""
        composer = getattr(self._panel, "composer", None)
        try:
            if composer is not None and composer.is_running():
                composer.set_running(False)
        except (AttributeError, RuntimeError) as exc:
            log_warning(f"Composer not released: {exc}")

    def _wind_up_run(self, run_id: str, status: str, summary: str, usage: dict, verification,
                     ended: list) -> None:
        """The ending itself: review, history, panel, offers, telemetry, store."""

        self._cancel_proposals(run_id)
        if status == RunStatus.DONE and not self._settings.onboarded:
            self._settings.onboarded = True
        if status != RunStatus.DONE:



            self._executor.cancel_run(run_id, halt=False)
        changed = self._executor.changed_layers(run_id)
        snapshot = self._executor.end_run(run_id)
        diff = None
        if snapshot is not None and snapshot.captured:
            try:
                diff = snapshot.last_diff or snapshot.diff()


                changed = int(diff.get("changed_layers", 0))
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Snapshot diff failed: {exc}")
        lines = []
        touched = changed_layer_items(diff) if diff is not None else []



        run_changes = run_change_items(self._executor.ended_run_report(run_id, snapshot), touched)
        if isinstance(verification, dict):
            for key in ("lines", "checks", "items"):
                if isinstance(verification.get(key), (list, tuple)):
                    lines += [str(v) for v in verification[key] if v]
        elif isinstance(verification, (list, tuple)):
            lines += [str(v) for v in verification if v]
        changes = describe_diff(diff) if diff is not None else []
        if diff is not None and not changes and status == RunStatus.DONE:
            changes = [tr("No layer, feature or file changed")]




        merged = {"lines": lines, "changes": changes} if (lines or changes) else None
        self._last_verification = merged

        self._panel_call("set_run_changes", changed, self._history_available(), touched, run_changes)
        self._send_history()
        self._panel_call("end_run", run_id, status, summary, usage or {}, merged)
        ended.append(True)
        self._offer_cleanup(run_id, status)
        if status == RunStatus.QUOTA:



            if self._quota_tracked_run != run_id:
                self._panel_call("show_quota_pause", run_id, summary or tr("You have used every run of this period."))
                telemetry.track(ev.QUOTA_EXHAUSTED, {"run_id": run_id, "source": "run_end"})
            self._quota_tracked_run = None
        started = (self._run or {}).get("started") if self._run and self._run["run_id"] == run_id else None
        usage = usage if isinstance(usage, dict) else {}
        telemetry.track(ev.AGENT_RUN_ENDED, {
            "run_id": run_id, "status": status, "steps": usage.get("steps"),
            "duration_ms": int((time.monotonic() - started) * 1000) if started else None,
            "layers_changed": changed, "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"), "cached_tokens": usage.get("cached_tokens"),
            "cost_eur": usage.get("cost_eur"),
            "review_lines": len(lines) + len(changes), "snapshot": bool(snapshot and snapshot.captured)})
        thread_id = (self._run or {}).get("thread_id") or self._thread_id
        if thread_id:
            self._store.update_agent_message(thread_id, run_id, {
                "status": status, "summary": summary, "usage": usage or {}, "verification": merged,
                "run_changes": run_changes or None,
                "text": self._take_agent_text(run_id),
                "answer_start": self._answer_start.pop(run_id, 0)})
            ended.append("stored")
            self._panel_call("set_threads", self._store.list_threads(), _project_path())
        log(f"Run {run_id[:8]} {status}: {changed} layers changed")
        try:
            dock_hidden = not self._panel.isVisible()
        except (AttributeError, RuntimeError):
            dock_hidden = False
        if dock_hidden and status == RunStatus.DONE:
            self.notice.emit("info", tr("AI Agent run finished."))
        elif dock_hidden and status not in (RunStatus.DONE, RunStatus.CANCELLED):
            self.notice.emit("warning", tr("AI Agent run failed."))

    def _on_server_error(self, error: dict) -> None:
        run_id = error.get("run_id")
        code = str(error.get("code") or ServerErrorCode.INTERNAL)
        message = str(error.get("message") or tr("The agent service reported an error."))
        retryable = bool(error.get("retryable"))
        run = self._run
        if (code == ServerErrorCode.BUSY and retryable
                and self._runs.busy_refused(run_id if isinstance(run_id, str) else "", BUSY_RESENDS)):
            run = self._run






            log_warning(f"Run {run['run_id'][:8]} refused ({message}); resend {run['resends']} of {BUSY_RESENDS}")
            self._panel_call("set_status_line", run["run_id"], tr("The service is updating. Resuming..."))
            self._watchdog.stop()
            self._resend_timer.start(BUSY_RESEND_MS)
            return
        if not (isinstance(run_id, str) and run_id) and retryable:




            if self._session_error_shown == (code, message):
                return
            self._session_error_shown = (code, message)




        names_run = run is not None and isinstance(run_id, str) and run_id == run["run_id"]
        if names_run:
            run["error_message"] = message

        retryable = retryable and self._retry_input(run_id if isinstance(run_id, str) else "") is not None
        self._panel_call("show_error", run_id if isinstance(run_id, str) else None, code, message, retryable, "")
        track_plugin_error("server", code, run_id if isinstance(run_id, str) else "")
        if code == ServerErrorCode.QUOTA_EXHAUSTED:
            self._quota_tracked_run = run_id if isinstance(run_id, str) else None
            telemetry.track(ev.QUOTA_EXHAUSTED, {"run_id": self._quota_tracked_run, "source": "server_error"})
        if code == ServerErrorCode.QUOTA_EXHAUSTED and self._run:
            self._panel_call("show_quota_pause", self._run["run_id"], message)
        if (self._run and isinstance(run_id, str) and run_id == self._run["run_id"]
                and not self._end_timer.isActive()):
            if (isinstance(run_id, str) and run_id and self._runs.never_started(run_id)
                    and code != ServerErrorCode.QUOTA_EXHAUSTED):


                self._finish_run(run_id, RunStatus.FAILED, message, {}, None)
            else:

                self._end_timer.start(CANCEL_GRACE_MS * 2)

    def _on_thread_title(self, thread_id: str, title: str) -> None:
        self._store.set_title(thread_id, title)
        self._panel_call("set_threads", self._store.list_threads(), _project_path())



    def _remember_run_project(self, run_id: str) -> None:
        """Which project this run is about, kept past the end of the run."""
        try:
            self._run_projects[run_id] = project_key(_project_path())
        except Exception as exc:  # noqa: BLE001 - a note is never worth breaking a run
            log_warning(f"Run project not recorded: {exc}")
            return
        while len(self._run_projects) > RUN_PROJECTS_KEPT:
            self._run_projects.pop(next(iter(self._run_projects)))

    def _store_ending(self, run_id: str, status: str, summary: str, usage) -> None:
        """The open run's status and answer in its thread file, when its wind-up could not write them."""
        thread_id = (self._run or {}).get("thread_id") or self._thread_id
        if not thread_id:
            return
        try:
            self._store.update_agent_message(thread_id, run_id, {
                "status": status, "summary": summary, "usage": usage if isinstance(usage, dict) else {},
                "text": self._take_agent_text(run_id), "answer_start": self._answer_start.get(run_id, 0)})
        except Exception as exc:  # noqa: BLE001 - the run still closes
            log_warning(f"Run {run_id[:8]}: its ending was not stored: {exc}")

    def _take_agent_text(self, run_id: str) -> str:
        """The run's text for the transcript, without a stray Follow-ups line."""
        return strip_followups(self._agent_text.pop(run_id, ""))

    def _on_sources(self, run_id: str, items) -> None:
        rows = []
        for item in items or []:
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                continue
            row = {"name": str(item.get("name")), "url": str(item.get("url") or ""),
                   "glyph": str(item.get("glyph") or "globe")}
            if item.get("id"):
                row["id"] = str(item["id"])
            rows.append(row)
        if rows:
            self._panel_optional("set_sources", run_id, rows)

    def _on_counts(self, run_id: str, tool_calls: int, messages: int) -> None:
        if run_id not in self._agent_text:
            return
        self._heard(run_id)
        self._touch_watchdog()
        self._panel_optional("set_run_counts", run_id, int(tool_calls), int(messages))
