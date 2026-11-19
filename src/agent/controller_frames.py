# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




from __future__ import annotations

import json
import time

from qgis.PyQt.QtCore import QTimer

from ..api.account import Account, site_url, usage_to_runs
from ..core import crash_note, telemetry
from ..core import telemetry_events as ev
from ..core.logger import log, log_warning
from ..core.plan import autopilot_allowed, set_plan_features, stated
from ..core.profile import project_key
from ..core.project_identity import open_project_file
from ..core.protocol import Decision, RunStatus, ServerErrorCode
from ..core.run_state import AWAIT_OUTCOME, AWAIT_RESUME, RESEND
from ..core.security_urls import vouch_for_urls
from ..core.served_state import set_payment_failed, set_student_offer
from ..core.session import HELD_KEY_REFUSED
from ..core.settings import PANEL_RETURN_DAYS, account_tag
from ..core.snapshot import changed_layer_items, describe_diff
from ..core.snapshot_report import run_change_items
from ..core.telemetry_errors import report_exception, track_plugin_error
from ..core.tool_registry import spec
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
    tr,
)
from .executor_calls import _options_joined
from .run_report import VERIFY_RUN


class _ControllerFrames:


    _session_error_shown: tuple | None = None


    def _on_connection_failed(self, facts) -> None:



        props = {"stage": "connect", "duration_ms": None}
        if isinstance(facts, dict):
            props.update(facts)
        props["in_run"] = self._run is not None
        telemetry.track(ev.CONNECTION_FAILED, props)

    def _on_connection_ready(self, facts) -> None:


        if isinstance(facts, dict):
            telemetry.track(ev.CONNECTION_ESTABLISHED, facts)

    def _connection_facts(self) -> dict:

        facts = getattr(self._session, "failure_facts", None)
        if not callable(facts):
            return {}
        try:
            got = facts()
        except Exception:  # noqa: BLE001
            return {}
        return {k: got[k] for k in ("http_status", "route", "server") if k in got}

    def _on_state_changed(self, state: str, detail: str) -> None:
        self._panel_call("set_connection_state", state, detail)
        if state == "online":
            self._refusal_retried = False
        if state == "signed_out" and self._account.has_activation_key:


            self._account.refresh_activation_async(force=True)
        elif state == "offline" and self._account.has_activation_key and not self._session.closed_on_purpose:


            self._account.refresh_activation_async()
        if state in ("offline", "connecting"):
            if self._link_notice:
                self._say_connection_lost()
            elif self._run is not None and not self._conn_lost_timer.isActive():
                self._conn_lost_timer.start()
        else:
            self._conn_lost_timer.stop()
            if self._link_notice:
                self._link_notice = ""
                if self._run is not None:
                    self._panel_call("set_link_notice", self._run["run_id"], "")

    def _on_key_confirmed(self, usage: object) -> None:






        if (self._refusal_retried or getattr(self._session, "held", "") != HELD_KEY_REFUSED
                or not isinstance(usage, dict) or "error" in usage
                or self._account.state != Account.ACTIVATED):
            return
        self._refusal_retried = True
        log("Key confirmed by the website after a refused hello: connecting once more")
        self._panel_call("set_connection_state", "connecting", "")
        self._session.connect_to_server()

    def _say_connection_lost(self) -> None:


        if self._run is None:
            return
        cause = getattr(self._session, "link_cause", None)
        if callable(cause) and cause() == "internet":
            text = tr("No internet connection. Retrying.")
        else:
            text = tr("TerraLab is restarting. Your task will resume.")
        if text != self._link_notice:

            self._link_notice = text
            self._panel_call("set_link_notice", self._run["run_id"], text)

    def _offer_crash_comment(self, reported) -> None:



        key = reported.get("key") if isinstance(reported, dict) else None
        if not (isinstance(key, str) and key):
            return
        if reported.get("ack") is True:

            self._crash_ack_keys.add(key)
        if telemetry.is_telemetry_enabled() and not self._settings.crash_asked(key):
            self._settings.note_crash_asked(key)
            self._panel_call("offer_crash_comment", key)

    def _on_session_started(self, session: dict) -> None:
        self._session_error_shown = None

        self._panel.mascot_stage.apply_served(session.get("mascot"))
        self._note_crashed_chat()
        self._offer_crash_comment(session.get("crash_reported"))
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
            self._replay_run = self._cut_run = run_id
            self._watchdog.stop()
            self._lost_timer.start(RESUME_OUTCOME_MS)
        elif action == AWAIT_RESUME:
            self._panel_call("set_status_line", run["run_id"],
                             tr("Reconnected. Waiting for TerraLab to resume the run..."))
            self._touch_watchdog(RESUME_GRACE_S)
        self._panel_call("set_model_label", str(session.get("model_label") or ""))



        set_plan_features(session.get("plan_features"))


        if stated("yolo") and not autopilot_allowed():
            self._settings.forget_autopilot()


        paid = self._settings.last_paid
        if "is_free_tier" in session:
            paid = not bool(session.get("is_free_tier"))
            self._settings.last_paid = paid
            self._note_plan_seen(paid)
        self._panel_call("set_paid_plan", paid)


        self._panel_call("set_effort", self._landing_effort())


        self._panel_call("show_notice", session.get("notice") if isinstance(session.get("notice"), dict) else {})
        self._panel_call("set_permission_mode", self._approval())


        rows = session.get("connectors")
        if isinstance(rows, list) and rows:
            try:
                from ..core.served_state import set_connectors

                set_connectors(rows)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Connector list refused: {exc}")

        try:
            from ..core import catalog

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
                from ..core.served_state import set_qgis_plugins

                set_qgis_plugins(plugin_rows)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"QGIS plugin list refused: {exc}")
            try:


                from ..core.qgis_plugins import warm_metadata

                warm_metadata()
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Plugin metadata not warmed: {exc}")


        hidden = session.get("qgis_plugins_hidden")
        if isinstance(hidden, list) and hidden:
            try:
                from ..core.served_state import set_hidden_plugins

                set_hidden_plugins(hidden)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Hidden plugin list refused: {exc}")
        quota = session.get("quota") if isinstance(session.get("quota"), dict) else None
        if quota:
            self._on_usage(quota)
            self._account.note_session_accepted()
        log(f"Session {str(session.get('session_id') or '')[:8]} online, model {session.get('model_label')}")

    def _on_usage(self, usage: dict) -> None:
        if not isinstance(usage, dict):
            return

        self._panel_call("set_trial_offer", usage.get("trial_days_available"), usage.get("trial_ends_on"))
        try:
            used, limit = int(usage.get("runs_used") or 0), int(usage.get("runs_limit") or 0)
        except (TypeError, ValueError):
            return
        period_end = str(usage.get("period_end") or "")
        self._panel_call("set_usage", used, limit, period_end, usage.get("is_subscriber"))
        self._account.note_balance(used, limit, period_end)

    def _on_website_usage(self, payload: object) -> None:

        if not isinstance(payload, dict):
            return

        self._panel_call("set_account_cap", payload.get("free_account_cap"))

        from ..api.account import payment_failed

        set_student_offer(payload.get("student_offer"))
        payfail = payment_failed(payload.get("billing_issue"))
        set_payment_failed(payfail)
        self._panel_call("set_payment_failed", payfail)
        usage = usage_to_runs(payload)
        cap = usage.get("trial_cap") or {}
        self._panel_call("set_start_now_url", site_url(cap.get("start_now_url")))
        self._panel_call("set_returning", usage.get("offer_kind") == "return")
        self._panel_call("set_keep_pro", usage.get("keep_url") or "",
                         str(usage.get("plan_trial_ends_on") or usage.get("keep_ends_on") or ""))
        self._on_usage(usage)
        paid = usage.get("is_subscriber")
        if isinstance(paid, bool):

            self._settings.last_paid = paid
            self._panel_call("set_paid_plan", paid)
            self._note_plan_seen(paid, usage)

    def _note_plan_seen(self, paid: bool, usage: dict | None = None) -> None:






        tag = account_tag()
        if tag == "signed-out":
            return
        before = self._settings.plan_seen(tag)
        if usage is None and paid and before == "free":
            self._account.refresh_activation_async(force=True)
            return
        self._settings.set_plan_seen(tag, "pro" if paid else "free")
        if usage is not None and before in ("free", "pro") and (before == "pro") != bool(paid):
            telemetry.track(ev.PLAN_CHANGED, {
                "from_plan": before, "to_plan": "pro" if paid else "free",
                "trial": bool(usage.get("plan_trial_ends_on")), **telemetry.upsell_context()})
        if usage is not None and paid and before == "free":
            self._panel_call("show_pro_welcome", usage.get("runs_limit"), usage.get("plan_trial_ends_on"))


            self._plan_resync = True
            self._resync_sibling_tools_when_idle()

    def _on_token(self, run_id: str, text: str) -> None:
        if not self._runs.owns(run_id):

            return
        if self._replay_run == run_id:


            self._replay_run = None
            self._agent_text[run_id] = ""
            self._answer_start[run_id] = 0
            self._text_break.pop(run_id, None)
            self._panel_call("reset_answer", run_id)
        self._heard(run_id)
        self._touch_watchdog()
        self._panel_call("append_token", run_id, text)
        stored = self._agent_text.get(run_id, "")
        if len(stored) < MAX_AGENT_TEXT:

            if self._text_break.pop(run_id, False) and stored and not stored.endswith("\n"):
                text = "\n\n" + text.lstrip()
            self._agent_text[run_id] = stored + text

    def _on_plan(self, run_id: str, steps: list) -> None:
        if not self._runs.owns(run_id):
            return
        self._heard(run_id)
        self._touch_watchdog()
        self._panel_call("set_plan", run_id, steps)
        self._answer_start[run_id] = len(self._agent_text.get(run_id, ""))
        if self._thread_id:
            self._store.update_agent_message(self._thread_id, run_id, {"plan": steps})

    def _on_plan_update(self, run_id: str, step_id: str, state: str) -> None:
        if not self._runs.owns(run_id):
            return
        self._heard(run_id)
        self._touch_watchdog()
        self._panel_call("update_plan_step", run_id, step_id, state)

    def _on_status_line(self, run_id: str, text: str, progress=None) -> None:


        if not self._runs.owns(run_id):
            return
        self._heard(run_id)
        self._touch_watchdog()
        self._panel_call("set_status_line", run_id, text, progress or {})

    def _on_tool_call(self, call: dict) -> None:
        tool_call_id = str(call.get("tool_call_id") or "")
        run_id = str(call.get("run_id") or "")
        if run_id and not self._runs.owns(run_id):


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



        self._pause_watchdog()
        self._call_runs[tool_call_id] = run_id
        self._call_names[tool_call_id] = (str(call.get("name") or ""), str(call.get("danger") or "read"))
        task_rows = self.__dict__.setdefault("_task_rows", {})
        checked = (call.get("args") or {}).get("task_id") if isinstance(call.get("args"), dict) else None
        if call.get("poll") is True or (call.get("name") == "get_task_status" and checked in task_rows):






            self._poll_calls.add(tool_call_id)
            self._executor.handle_tool_call(call)
            return
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        if call.get("name") in PROPOSAL_TOOLS:

            self._on_proposal(call)
            return
        try:
            if not getattr(spec(str(call.get("name") or "")), "waits_on_user", False):



                self._panel_later("add_tool_call", tool_call_id, run_id, str(call.get("name") or ""), args,
                                  str(call.get("danger") or "read"), str(call.get("sentence") or ""))
            else:
                self._question_args[tool_call_id] = args
            self._text_break[run_id] = True
            self._answer_start[run_id] = len(self._agent_text.get(run_id, ""))
            if self._thread_id:
                self._store.append_tool_call(self._thread_id, run_id, {
                    "tool_call_id": tool_call_id, "name": call.get("name"), "args": args,
                    "danger": call.get("danger"), "sentence": call.get("sentence"), "ok": None})
        except Exception as exc:  # noqa: BLE001


            report_exception(exc, "tool_call_row", module=__name__, run_id=run_id)
        self._executor.handle_tool_call(call)

    def _on_server_tool(self, frame: dict) -> None:






        run_id = str(frame.get("run_id") or "")
        if not self._runs.owns(run_id):
            return
        tool_call_id = str(frame.get("tool_call_id") or "")[:256]
        if self._panel_queue:
            self._drain_panel()
        if self._panel.message_list.tool_card(tool_call_id) is not None:
            return
        self._heard(run_id)
        self._touch_watchdog()
        name = str(frame.get("name") or "")[:200]
        args = frame.get("args") if isinstance(frame.get("args"), dict) else {}
        ok = frame.get("ok") is True
        if ok and frame.get("vouches") is True and isinstance(frame.get("result"), dict):



            rows = frame["result"].get("results")
            vouch_for_urls([row.get("url") for row in rows if isinstance(row, dict)] if isinstance(rows, list) else [])
        try:
            duration = max(0.0, float(frame.get("duration_ms") or 0) / 1000.0)
        except (TypeError, ValueError, OverflowError):
            duration = 0.0
        detail = ""
        if ok:
            result = frame.get("result") if isinstance(frame.get("result"), dict) else {}
            listed = result.get("results") if isinstance(result.get("results"), list) else None

            rows = [{"title": str(r.get("title") or "")[:300], "url": str(r.get("url") or "")[:500],
                     **{k: str(r[k])[:500] for k in ("connector_id", "logo_url") if isinstance(r.get(k), str) and r[k]}}
                    for r in (listed or [])[:20] if isinstance(r, dict)]
            if rows:
                detail = json.dumps({"results": rows}, ensure_ascii=False)
            summary = tr("No results") if listed is not None and not rows else ""
        else:
            code = str(frame.get("code") or "")[:60]
            message = " ".join(str(frame.get("message") or "").split())[:300]
            summary = f"{code}: {message}" if code and message else (message or code)
        self._panel_call("add_tool_call", tool_call_id, run_id, name, args, "read", "")
        self._panel_call("finish_tool_call", tool_call_id, ok, summary, duration, detail)
        self._text_break[run_id] = True
        self._answer_start[run_id] = len(self._agent_text.get(run_id, ""))
        if self._thread_id:
            self._store.append_tool_call(self._thread_id, run_id, {
                "tool_call_id": tool_call_id, "name": name, "args": args, "danger": "read",
                "sentence": "", "ok": ok, "summary": summary, "duration_s": round(duration, 2),
                "detail": detail, "server_side": True})

    def _on_tool_started(self, call: dict) -> None:


        self._pause_watchdog()
        tool_call_id = str(call.get("tool_call_id") or "")
        if tool_call_id in self._poll_calls:
            return
        self._panel_later("show_call_running", str(call.get("run_id") or ""), tool_call_id)

    def _on_task_checked(self, ok: bool, summary: str, detail: str, result) -> None:

        task_rows = self.__dict__.get("_task_rows") or {}
        if not isinstance(result, dict) or str(result.get("task_id") or "") not in task_rows:
            return
        task_id = str(result["task_id"])
        row_id, started = task_rows[task_id]
        if result.get("status") == "running":
            if result.get("received_bytes"):
                self._panel_later("set_call_progress", row_id, result["received_bytes"], result.get("total_bytes"))
            elif result.get("source"):
                self._panel_later("set_call_progress", row_id, 0, None, "wait:" + str(result["source"]))
            return
        task_rows.pop(task_id, None)
        if result.get("status") == "canceled":
            summary = "CANCELLED: " + str(summary or "")
        self._panel_later("finish_tool_call", row_id, ok and result.get("status") == "complete", summary,
                          time.monotonic() - started, detail, result)

    def _on_call_progress(self, tool_call_id: str, done, total, stage: str = "") -> None:
        self._panel_later("set_call_progress", tool_call_id, done, total, stage)

    def _on_tool_finished(self, tool_call_id: str, ok: bool, summary: str, duration: float, detail: str,
                          result=None) -> None:


        if tool_call_id in self._poll_calls:


            self._poll_calls.discard(tool_call_id)
            self._on_task_checked(ok, summary, detail, result)
            self._runs.close_call(tool_call_id)
            self._call_names.pop(tool_call_id, None)
            self._touch_watchdog()
            return
        if isinstance(result, dict) and result.get("status") == "running" and result.get("task_id"):

            started = time.monotonic() - float(duration or 0.0)
            self.__dict__.setdefault("_task_rows", {})[str(result["task_id"])] = (tool_call_id, started)
        elif isinstance(result, dict) and result.get("status") == "canceled":
            self._on_task_checked(ok, summary, detail, result)
        self._panel_later("finish_tool_call", tool_call_id, ok, summary, duration, detail, result)
        run_id = self._runs.close_call(tool_call_id)
        self._call_names.pop(tool_call_id, None)
        self._touch_watchdog()
        if self._thread_id and run_id:
            stored = {"tool_call_id": tool_call_id, "ok": ok, "summary": summary, "duration_s": round(duration, 2)}
            if not ok and isinstance(result, dict) and result.get("hint"):
                stored["hint"] = str(result["hint"])[:80]
            self._store.append_tool_call(self._thread_id, run_id, stored)

    def _on_permission_needed(self, tool_call_id: str, run_id: str, sentence: str, args) -> None:

        name, danger = self._call_names.get(tool_call_id, (self._executor.pending_name(tool_call_id), "read"))
        telemetry.track(ev.AGENT_PERMISSION_ASKED, {"run_id": run_id, "tool": name, "danger": danger})
        self._card_shown_at(tool_call_id)
        self._runs.wait_user(tool_call_id)
        self._pause_watchdog()

        addresses = self._executor.unvouched_for(tool_call_id)


        grant = "answer" if self._executor.covers_answer(tool_call_id) else ""
        folder = self._executor.write_folder_for(tool_call_id)
        if folder:
            grant = "folder\n" + folder

        group = self._executor.card_group(tool_call_id)
        preview = self._executor.code_preview_for(tool_call_id)
        if preview:
            preview["missing"] = _missing_layers(preview.get("layers"))
        extra = (addresses or None, grant, group, preview or None)
        if run_id in self.__dict__.get("_task_states", {}):

            self._panel_call("ask_task_permission", tool_call_id, run_id, sentence, name, args, *extra)
        else:
            self._panel_call("ask_permission", tool_call_id, run_id, sentence, args, *extra)
        self._approval_pending(tool_call_id, True)

    def _on_question_needed(self, tool_call_id: str, run_id: str, question: str, options,
                            allow_free_text: bool, recommended: int = -1, why: str = "") -> None:
        telemetry.track(ev.AGENT_QUESTION_ASKED, {"run_id": run_id, "options": len(options or [])})
        self._card_shown_at(tool_call_id, run_id, [str(o) for o in options or []])
        self._runs.wait_user(tool_call_id)
        self._pause_watchdog()




        args = self._question_args.pop(tool_call_id, {})
        multiple = bool(args.get("multiple"))
        details = [str(d) for d in args.get("details") or []] if isinstance(args.get("details"), list) else []
        header = str(args.get("header") or "")
        extra = ((multiple, details, header) if header
                 else (multiple, details) if any(details) else ((multiple,) if multiple else ()))
        self._panel_call("ask_question", tool_call_id, run_id, question, options, allow_free_text,
                         int(recommended), str(why or ""), *extra)
        self._approval_pending(tool_call_id, True)

    def _on_install_wait_needed(self, tool_call_id: str, run_id: str, name: str) -> None:

        self._runs.wait_user(tool_call_id)
        self._pause_watchdog()
        self._panel_call("show_install_wait", tool_call_id, run_id, name)
        self._approval_pending(tool_call_id, True)

    def _on_account_needed(self, tool_call_id: str, run_id: str, need) -> None:

        self._runs.wait_user(tool_call_id)
        self._pause_watchdog()
        self._panel_call("ask_account", tool_call_id, run_id, need)
        self._approval_pending(tool_call_id, True)

    def _on_question_answered(self, tool_call_id: str, answer: str, attachments=None) -> None:

        run_id, options, waited_ms = self._card_waited(tool_call_id)
        telemetry.track(ev.AGENT_QUESTION_ANSWERED, {
            "skipped": not answer, "run_id": run_id, "waited_ms": waited_ms,
            "answer_kind": "skipped" if not answer else "option" if answer in options else "typed"})
        self._runs.user_answered(tool_call_id)
        self._approval_pending(tool_call_id, False)
        self._touch_watchdog()
        self._folder_card_first(answer or "", None, offered=_options_joined(answer or "", options))
        if self._folder_card_open_for(answer or "", _options_joined(answer or "", options)):


            self._held_answers = [*getattr(self, "_held_answers", []), (tool_call_id, answer, attachments)]
            return
        self._send_answer(tool_call_id, answer, attachments)

    def _send_answer(self, tool_call_id: str, answer: str, attachments=None) -> None:
        if attachments:
            self._executor.on_question_answered(tool_call_id, answer, attachments)
        else:
            self._executor.on_question_answered(tool_call_id, answer)

    def _on_permission_decided(self, tool_call_id: str, decision: str, edits: object = None) -> None:











        name, danger = self._call_names.get(tool_call_id, ("", "read"))
        edited = bool(isinstance(edits, dict) and edits)
        telemetry.track(ev.AGENT_PERMISSION_DECIDED, {
            "run_id": self._call_runs.get(tool_call_id, ""), "tool": name, "danger": danger,
            "decision": decision, "edited": edited, "waited_ms": self._card_waited(tool_call_id)[2]})
        self._runs.user_answered(tool_call_id)
        self._approval_pending(tool_call_id, False)
        if decision == Decision.DENY:
            self._close_call(tool_call_id)
        self._touch_watchdog()



        self._panel_call("resolve_permission", tool_call_id, decision)
        self._executor.on_permission_decided(tool_call_id, decision, edits if edited else None)

    def _card_shown_at(self, tool_call_id: str, run_id: str = "", options=None) -> None:

        self.__dict__.setdefault("_cards_shown", {})[tool_call_id] = (
            run_id, list(options or []), time.monotonic())

    def _card_waited(self, tool_call_id: str):

        run_id, options, since = self.__dict__.get("_cards_shown", {}).pop(
            tool_call_id, ("", [], None))
        return run_id, options, int((time.monotonic() - since) * 1000) if since else 0

    def _approval_pending(self, tool_call_id: str, waiting: bool) -> None:






        pending = self.__dict__.setdefault("_approvals_waiting", set())
        before = bool(pending)
        if waiting:
            pending.add(tool_call_id)
        elif tool_call_id:
            pending.discard(tool_call_id)
        else:
            pending.clear()
        if bool(pending) != before:
            try:
                self.approval_waiting.emit(bool(pending))
            except (AttributeError, RuntimeError):
                pass

    def _close_call(self, tool_call_id: str) -> None:






        self._runs.close_call(tool_call_id)
        self._call_names.pop(tool_call_id, None)
        self._poll_calls.discard(tool_call_id)

    def _on_permission_resolved(self, tool_call_id: str, decision: str) -> None:





        self._runs.user_answered(tool_call_id)
        self._approval_pending(tool_call_id, False)
        if decision == Decision.DENY:
            self._close_call(tool_call_id)
            self._touch_watchdog()
            reason = "stopped" if self._runs.cancelled() else "unanswered"
            self._panel_call("resolve_permission", tool_call_id, decision, reason)
        else:
            self._panel_call("resolve_permission", tool_call_id, decision)

    def _on_run_end(self, run_id: str, status: str, summary: str, usage: dict, verification) -> None:
        if not self._runs.owns(run_id):


            ended = getattr(self, "_ends", {}).get(run_id)
            where = f" here ({ended[1]})" if ended and ended[1] else ""
            log(f"Run {run_id[:8]} run_end ignored: already finished{where}")
            return
        self._finish_run(run_id, status, summary, usage, verification)

    def _finish_run(self, run_id: str, status: str, summary: str, usage: dict, verification) -> None:





        self._end_timer.stop()
        self._resend_timer.stop()
        self._lost_timer.stop()
        self._replay_run = None
        self._watchdog.stop()
        self._diff_timer.stop()
        self._approval_pending("", False)
        closing = self._runs.owns(run_id)
        ended: list = []
        try:
            self._wind_up_run(run_id, status, summary, usage, verification, ended)
        except Exception as exc:  # noqa: BLE001
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
                    self._poll_calls.discard(tool_call_id)
                self._session.refresh_heartbeat()
                self._release_composer()



        QTimer.singleShot(0, lambda: self._save_run_layers(run_id))
        QTimer.singleShot(0, lambda: self._send_project_state(run_id))

        QTimer.singleShot(0, self._resync_sibling_tools_when_idle)
        if closing:

            self._restore_after_run(run_id)
            try:
                self.run_finished.emit(str(status or ""), str(run_id or ""))
            except (AttributeError, RuntimeError):
                pass

            self._flush_steers(run_id)

    def _record_after_point(self, run_id: str = "") -> None:

        try:
            if self._executor.record_pending_after(run_id):
                self._send_history()
        except Exception as exc:  # noqa: BLE001
            report_exception(exc, "record_after_point", module=__name__)

    def _note_project_before(self, run_id: str) -> None:

        if not self._session.wants_project_state:
            return
        try:
            from ..core.project_state import snapshot

            before = getattr(self, "_project_before", None)
            if before is None:
                before = self._project_before = {}
            before.clear()
            before[run_id] = snapshot()
        except Exception as exc:  # noqa: BLE001
            report_exception(exc, "project_before", module=__name__, run_id=run_id)

    def _send_project_state(self, run_id: str) -> None:

        before = (getattr(self, "_project_before", None) or {}).pop(run_id, None)
        if before is None or not self._session.wants_project_state:
            return
        try:
            from ..core.project_state import snapshot

            self._session.send_project_state(run_id, before, snapshot())
        except Exception as exc:  # noqa: BLE001
            report_exception(exc, "project_after", module=__name__, run_id=run_id)

    def _save_run_layers(self, run_id: str = "") -> None:





        layer_ids, self._run_new_layers = getattr(self, "_run_new_layers", None) or [], []
        sent = 0
        if layer_ids:
            try:
                from ..tools.persist_tools import save_run_layers

                history, thread_id = self._executor.history, self._thread_id or ""
                sent = save_run_layers(layer_ids, busy=self._runs.busy,
                                       then=lambda: self._record_after_point(run_id),
                                       before_save=lambda path: history.before_own_save(thread_id, run_id, path))
            except Exception as exc:  # noqa: BLE001
                report_exception(exc, "save_run_layers", module=__name__)
        if not sent:
            self._record_after_point(run_id)

    def _release_composer(self) -> None:

        composer = self._panel.composer
        try:
            if composer.is_running():
                composer.set_running(False)
        except (AttributeError, RuntimeError) as exc:
            log_warning(f"Composer not released: {exc}")

    def _wind_up_run(self, run_id: str, status: str, summary: str, usage: dict, verification,
                     ended: list) -> None:


        self._cancel_proposals(run_id)
        if status == RunStatus.DONE and not self._settings.onboarded:
            self._settings.onboarded = True
        if status != RunStatus.DONE:



            self._executor.cancel_run(run_id, halt=False)
        changed = self._executor.changed_layers(run_id)
        snapshot = self._executor.end_run(run_id)
        if snapshot is not None and snapshot.captured and getattr(self, "_restore_after_stop", "") != run_id:


            from qgis.core import QgsProject

            self._run_new_layers = [layer_id for layer_id in QgsProject.instance().mapLayers()
                                    if layer_id not in snapshot.layer_ids]
            from ..core import layer_removed

            layer_removed.note(run_id, self._run_new_layers)
        diff = None
        if snapshot is not None and snapshot.captured:
            try:
                diff = snapshot.last_diff or snapshot.diff()


                changed = int(diff.get("changed_layers", 0))
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Snapshot diff failed: {exc}")
        lines = []
        touched = changed_layer_items(diff) if diff is not None else []




        answer = self._agent_text.get(run_id, "")[self._answer_start.get(run_id, 0):]
        run_changes = run_change_items(self._executor.ended_run_report(run_id, snapshot), touched, answer)
        if isinstance(verification, dict):
            for key in ("lines", "checks", "items"):
                if isinstance(verification.get(key), (list, tuple)):
                    lines += [str(v) for v in verification[key] if v]
        elif isinstance(verification, (list, tuple)):
            lines += [str(v) for v in verification if v]


        changes = [tr(line) for line in describe_diff(diff)] if diff is not None else []
        if diff is not None and not changes and status == RunStatus.DONE:
            changes = [tr("No layer, feature or file changed")]




        merged = {"lines": lines, "changes": changes} if (lines or changes) else None
        self._last_verification = merged

        self._panel_call("set_run_changes", changed, self._history_available(), touched, run_changes)
        self._send_history()
        if getattr(self._session, "report_offer_decided", False):
            self._panel_call("await_report_decision", run_id)
        self._panel_call("end_run", run_id, status, summary, usage or {}, merged)
        ended.append(True)
        self._offer_cleanup(run_id, status)
        if status == RunStatus.QUOTA:


            if self._quota_tracked_run != run_id:
                self._panel_call("show_quota_pause", run_id, summary or tr("You have used every task of this period."))
                telemetry.track(ev.QUOTA_EXHAUSTED, {"run_id": run_id, "source": "run_end"})
            self._quota_tracked_run = None
        started = (self._run or {}).get("started") if self._run and self._run["run_id"] == run_id else None
        usage = usage if isinstance(usage, dict) else {}
        open_run = self._run if self._run and self._run["run_id"] == run_id else {}
        first_event_ms = open_run.get("first_event_ms")

        drawn = getattr(self._panel, "take_stream_stats", lambda _run: None)(run_id) or None
        sent_at = open_run.get("sent_at")


        stop_code = "" if status == RunStatus.DONE else str(open_run.get("stop_code") or "")
        self._remember_end(run_id, status, stop_code)
        crash_note.record_run_end(run_id, status)
        telemetry.track(ev.AGENT_RUN_ENDED, {
            "run_id": run_id, "status": status, "steps": usage.get("steps"),
            "duration_ms": int((time.monotonic() - started) * 1000) if started else None,
            "first_event_ms": first_event_ms, "had_first_event": first_event_ms is not None,
            "first_text_ms": int((drawn[3] - sent_at) * 1000) if drawn and sent_at is not None else None,
            "stream_renders": drawn[0] if drawn else None,
            "stream_slow_renders": drawn[1] if drawn else None,
            "stream_longest_ms": int(drawn[2]) if drawn else None,
            "resends": open_run.get("resends"), "stop_code": stop_code or None,
            "undo_copy": getattr(snapshot, "copy_kind", "none"),
            "undo_copy_ms": getattr(snapshot, "capture_ms", None),
            "project_layers": len(snapshot.layer_ids) if snapshot and snapshot.captured else None,
            "project_clean": (bool(snapshot.original_file) and not snapshot.was_dirty
                              if snapshot and snapshot.captured else None),
            "layers_changed": changed, "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"), "cached_tokens": usage.get("cached_tokens"),
            "cost_eur": usage.get("cost_eur"),
            "review_lines": len(lines) + len(changes), "snapshot": bool(snapshot and snapshot.captured)})


        if status == RunStatus.DONE and telemetry.first_run_recorded():
            telemetry.track(ev.FIRST_RUN_MILESTONE, {"run_id": run_id})
            self._settings.panel_return_until = time.time() + PANEL_RETURN_DAYS * 86_400
        if status == RunStatus.DONE and self._settings.last_paid and telemetry.first_paid_run_recorded():
            telemetry.track(ev.FIRST_RUN_MILESTONE, {"run_id": run_id, "kind": "first_paid_run",
                                                     **telemetry.upsell_context()})
        thread_id = (self._run or {}).get("thread_id") or self._thread_id
        if thread_id:
            self._store.update_agent_message(thread_id, run_id, {
                "status": status, "summary": summary, "usage": usage or {}, "verification": merged,
                "run_changes": run_changes or None,
                "text": self._agent_text.pop(run_id, ""),
                "answer_start": self._answer_start.pop(run_id, 0)})
            ended.append("stored")
            self._panel_call("set_threads", self._store.list_threads(), open_project_file())
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
        message = str(error.get("message") or tr("TerraLab reported an error."))
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
            run.setdefault("stop_code", code)

        retryable = retryable and self._retry_input(run_id if isinstance(run_id, str) else "") is not None


        on_sign_in_card = (code == ServerErrorCode.AUTH_FAILED and error.get("retryable") is not True
                           and not (isinstance(run_id, str) and run_id))

        if code == ServerErrorCode.PLUGIN_UPDATE_REQUIRED:

            self._panel_call("show_update_required", message)
        elif (code != ServerErrorCode.QUOTA_EXHAUSTED or not self._run) and not on_sign_in_card:
            self._panel_call("show_error", run_id if isinstance(run_id, str) else None, code, message, retryable, "",
                             "", str(error.get("reason") or "")[:64])
        if code != ServerErrorCode.QUOTA_EXHAUSTED and not names_run:




            track_plugin_error("server", code, run_id if isinstance(run_id, str) else "",
                               reason=str(error.get("reason") or ""), retryable=bool(error.get("retryable")),
                               in_run=False)
        if code == ServerErrorCode.QUOTA_EXHAUSTED:


            self._account.refresh_activation_async(force=True)
            self._quota_tracked_run = run_id if isinstance(run_id, str) else None
            telemetry.track(ev.QUOTA_EXHAUSTED, {"run_id": self._quota_tracked_run, "source": "server_error"})
        if code == ServerErrorCode.QUOTA_EXHAUSTED and error.get("reason") == "account_cap":

            self._panel_call("set_account_cap", {"blocked": True})
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
        self._panel_call("set_threads", self._store.list_threads(), open_project_file())


    def _remember_run_project(self, run_id: str) -> None:

        try:
            self._run_projects[run_id] = project_key(open_project_file())
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Run project not recorded: {exc}")
            return
        while len(self._run_projects) > RUN_PROJECTS_KEPT:
            self._run_projects.pop(next(iter(self._run_projects)))

    def _store_ending(self, run_id: str, status: str, summary: str, usage) -> None:

        thread_id = (self._run or {}).get("thread_id") or self._thread_id
        if not thread_id:
            return
        try:
            self._store.update_agent_message(thread_id, run_id, {
                "status": status, "summary": summary, "usage": usage if isinstance(usage, dict) else {},
                "text": self._agent_text.pop(run_id, ""), "answer_start": self._answer_start.get(run_id, 0)})
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Run {run_id[:8]}: its ending was not stored: {exc}")

    def _on_sources(self, run_id: str, items) -> None:
        rows = []
        for item in items or []:
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                continue
            row = {"name": str(item.get("name")), "url": str(item.get("url") or ""),
                   "glyph": str(item.get("glyph") or "globe")}
            if item.get("id"):
                row["id"] = str(item["id"])


            for key in ("product", "data_date", "license", "page_url", "via", "kind", "logo_url", "resolution",
                        "attribution", "address"):
                value = str(item.get(key) or "").strip()
                if value and (key not in ("page_url", "logo_url", "address")
                              or value.startswith(("https://", "http://"))):
                    row[key] = value[:500 if key in ("attribution", "address") else 300]
            if isinstance(item.get("dates"), list):
                row["dates"] = [str(day)[:10] for day in item["dates"][:31]]
            if isinstance(item.get("files"), int) and not isinstance(item.get("files"), bool):
                row["files"] = item["files"]
            rows.append(row)
        if rows:
            self._panel_call("set_sources", run_id, rows)

            if self._thread_id:
                try:
                    self._store.update_agent_message(self._thread_id, run_id, {"sources": rows}, create=False)
                except Exception as exc:  # noqa: BLE001
                    log_warning(f"Run {run_id[:8]}: its sources were not stored: {exc}")

    def _on_counts(self, run_id: str, tool_calls: int, messages: int) -> None:
        if not self._runs.owns(run_id):
            return
        self._heard(run_id)
        self._touch_watchdog()


def _missing_layers(names) -> list[str]:

    from qgis.core import QgsProject
    project = QgsProject.instance()
    return [str(n) for n in (names or []) if isinstance(n, str) and not project.mapLayersByName(n)]
