# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Controller: panel signals in, session and executor calls out, and back."""






from __future__ import annotations

import json
import os
import platform
import re
import time
import uuid

from qgis.core import Qgis, QgsProject
from qgis.PyQt.QtCore import QCoreApplication, QObject, QTimer, pyqtSignal

from ..api.account import Account, usage_to_runs
from ..api.terralab_client import plugin_version
from . import telemetry
from . import telemetry_events as ev
from .context import build_context, build_suggestions, mention_candidates
from .controller_actions import PanelActionsMixin, _project_path
from .executor import ToolExecutor
from .logger import log, log_warning
from .plan import autopilot_allowed, effort_allowed, set_plan_features
from .profile import add_memory_note, profile_context, project_key
from .protocol import Approval, ClientErrorCode, Decision, Effort, Mode, RunStatus, ServerErrorCode
from .scratch import MIN_TO_OFFER
from .scratch import group as group_scratch
from .scratch import remove as remove_scratch
from .security import allow_attached_paths, remember_user_text
from .session import AgentSession
from .settings import Settings
from .snapshot import changed_layer_items, describe_diff
from .telemetry_errors import report_exception, track_plugin_error
from .threads import ThreadStore, user_message_record

CANCEL_GRACE_MS = 5_000
MAX_AGENT_TEXT = 200_000



RUN_PROJECTS_KEPT = 16



RUN_SILENCE_S = 600



BUSY_RESENDS = 4
BUSY_RESEND_MS = 4_000


RESUME_GRACE_S = 120







_DIFF_SETTLE_MS = 250
PROPOSAL_TOOLS = ("propose_action", "propose_edits")



BASEMAP_TOOLS = ("add_xyz_layer", "list_xyz_sources")









_NOT_A_BARE_NAME = ("/", "\\", ".", "://")

QMS_OFFER_ID = "quickmapservices"



CONTINUE_TEXT = "continue"
PROPOSAL_MAX_ROWS = 500



_FOLLOWUPS_RE = re.compile(r"(?:^|\n)[ \t]*(?:[-*]\s*)?Follow-ups?\s*:\s*(?P<items>[^\n]*)\s*$", re.IGNORECASE)


def strip_followups(text: str) -> str:
    """The answer without a trailing Follow-ups line an older server sent."""
    text = text or ""
    match = _FOLLOWUPS_RE.search(text.rstrip())
    if match is None:
        return text
    return text.rstrip()[: match.start()].rstrip()


def _dump_context(context: dict, run_id: str) -> None:
    """The context of this message, written next to the run id."""





    folder = os.environ.get("AI_AGENT_DUMP_CONTEXT") or ""
    if not folder:
        return
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{run_id}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(context, handle, ensure_ascii=False, indent=1, default=str)
        log(f"Context dumped to {path}")
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Context dump failed: {exc}")


def _wants_basemap(name: str, args) -> bool:
    """True when this call is the user asking for a background map."""
    if name in BASEMAP_TOOLS:
        return True
    if name != "add_data":
        return False
    source = str((args or {}).get("source") or "").strip() if isinstance(args, dict) else ""
    return bool(source) and not any(mark in source for mark in _NOT_A_BARE_NAME)


def tr(text: str) -> str:
    return QCoreApplication.translate("AgentController", text)


class AgentController(PanelActionsMixin, QObject):
    layer_action_requested = pyqtSignal(str, str)
    settings_requested = pyqtSignal()
    notice = pyqtSignal(str, str)

    def __init__(self, iface, panel, registry, settings: Settings | None = None,
                 account: Account | None = None, parent=None):
        super().__init__(parent)
        self._iface = iface
        self._panel = panel
        self._registry = registry
        self._settings = settings or Settings()
        self._account = account or Account(self._settings, self)
        self._store = ThreadStore(on_index_ready=self._on_thread_index_ready)
        self._manifest_cache: tuple | None = None
        self._session = AgentSession(self._settings, self._account, self._identity, self._manifest, self)
        self._executor = ToolExecutor(registry, self._session, self._settings, self)
        self._thread_id: str | None = None
        self._run: dict | None = None
        self._quota_tracked_run: str | None = None
        self._pairing_started: float | None = None
        self._last_run: dict | None = None
        self._last_verification: dict | None = None
        self._agent_text: dict[str, str] = {}



        self._run_projects: dict[str, str] = {}
        self._text_break: dict[str, bool] = {}


        self._answer_start: dict[str, int] = {}
        self._call_runs: dict[str, str] = {}
        self._call_names: dict[str, tuple] = {}


        self._wanted_basemap: set = set()
        self._missing_slots: set[str] = set()
        self._failed_slots: set[str] = set()

        self._proposals: dict[str, dict] = {}

        self._question_args: dict[str, dict] = {}




        self._project_connections: list[tuple] = []
        self._end_timer = QTimer(self)
        self._end_timer.setSingleShot(True)
        self._end_timer.timeout.connect(self._end_run_locally)
        self._resend_timer = QTimer(self)
        self._resend_timer.setSingleShot(True)
        self._resend_timer.timeout.connect(self._resend_run)
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_server_silent)
        self._diff_timer = QTimer(self)
        self._diff_timer.setSingleShot(True)
        self._diff_timer.setInterval(_DIFF_SETTLE_MS)
        self._diff_timer.timeout.connect(self._refresh_run_changes)
        self._pending_changed = 0
        self._wire_panel()
        self._wire_session()
        self._wire_executor()
        self._wire_account()



    def start(self) -> None:
        self._panel_call("set_mention_provider", mention_candidates)
        self._panel_call("set_permission_mode", self._approval())
        self._panel_call("set_effort", self._settings.effort)
        self._panel_call("set_suggestions", build_suggestions())
        self._panel_call("set_welcome_visible", not self._settings.onboarded)
        self._panel_call("set_threads", self._store.list_threads(), _project_path())
        self._watch_project()
        telemetry.set_auth_provider(self._account.get_auth_header)
        telemetry.new_session()
        telemetry.start_flush_timer()
        if telemetry.first_open_recorded():
            telemetry.track(ev.PLUGIN_FIRST_OPEN)
        telemetry.track(ev.PLUGIN_OPENED, {"signed_in": self._account.has_activation_key})
        if self._account.state in (Account.ACTIVATED, Account.LOCKED):
            self._panel_call("set_connection_state", "connecting", "")
            self._session.connect_to_server()
            self._account.refresh_activation_async()
        else:
            self._panel_call("set_connection_state", "signed_out", "")

    def _on_thread_index_ready(self) -> None:
        """The history index finished off-thread: the menu gets the full list."""
        self._panel_call("set_threads", self._store.list_threads(), _project_path())

    def _watch_project(self) -> None:
        """Re-key the history menu when the user opens or saves a project."""
        try:
            from qgis.core import QgsProject
            project = QgsProject.instance()
            refresh = lambda *_: self._panel_call(  # noqa: E731
                "set_threads", self._store.list_threads(), _project_path())


            self._project_connections = [
                (project.readProject, refresh),
                (project.projectSaved, refresh),
                (project.cleared, refresh),
                (project.readProject, self._on_project_opened),
            ]
            for signal, slot in self._project_connections:
                signal.connect(slot)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Project watch failed: {exc}")

    def _unwatch_project(self) -> None:
        """Leave QgsProject as we found it: a kept connection fires into a dead panel on the next project open, once per plugin reload of the session."""

        for signal, slot in self._project_connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._project_connections = []

    def shutdown(self) -> None:
        self._unwatch_project()
        self._end_timer.stop()
        self._watchdog.stop()

        self._executor.cancel_background()

        self._session.disconnect_from_server(wait=True)

        self._store.close()
        telemetry.shutdown()

    @property
    def session(self) -> AgentSession:
        return self._session

    @property
    def account(self) -> Account:
        return self._account



    def _identity(self) -> dict:
        try:
            qgis_version = str(Qgis.QGIS_VERSION).split("-")[0]
        except Exception:
            qgis_version = ""
        return {"plugin_version": plugin_version(), "qgis_version": qgis_version,
                "os": f"{platform.system()} {platform.release()}".strip(), "locale": self._settings.locale}

    def _manifest(self) -> tuple:
        if self._manifest_cache is None:
            self._manifest_cache = (self._registry.manifest_hash(), self._registry.manifest())
        return self._manifest_cache



    def _connect(self, name: str, slot) -> None:
        signal = getattr(self._panel, name, None)
        if signal is None or not hasattr(signal, "connect"):
            log_warning(f"Panel has no signal {name}")
            return
        signal.connect(slot)

    def _panel_call(self, name: str, *args) -> None:
        method = getattr(self._panel, name, None)
        if method is None:
            if name not in self._missing_slots:
                self._missing_slots.add(name)
                log_warning(f"Panel has no slot {name}")
            return
        try:
            method(*args)
        except Exception as exc:  # noqa: BLE001


            if name in self._failed_slots:
                return
            self._failed_slots.add(name)
            report_exception(exc, "panel_slot", module=type(self._panel).__module__)

    def _connect_optional(self, name: str, slot) -> None:
        """A signal a newer panel has and an older one may not: no warning when absent."""
        if hasattr(self._panel, name):
            self._connect(name, slot)

    def _panel_optional(self, name: str, *args) -> None:
        """A slot a newer panel has and an older one may not: skipped silently when absent."""
        if hasattr(self._panel, name):
            self._panel_call(name, *args)

    def _wire_panel(self) -> None:
        self._connect("send_requested", self._on_send)
        self._connect("stop_requested", self._on_stop)
        self._connect("permission_decided", self._on_permission_decided)
        self._connect("question_answered", self._on_question_answered)
        self._connect("question_auto_answered", self._on_question_auto_answered)
        self._connect("retry_requested", self._on_retry)
        self._connect("continue_requested", self._on_continue)
        self._connect("undo_requested", self._on_undo)
        self._connect("restore_requested", self._on_restore)
        self._connect("discard_all_requested", self._on_discard_all)
        self._connect("history_requested", self._send_history)
        self._connect("layer_action_requested", self._on_layer_action)
        self._connect_optional("cleanup_decided", self._on_cleanup_decided)
        self._connect_optional("plugin_offer_decided", self._on_plugin_offer_decided)
        self._connect("new_thread_requested", self._on_new_thread)
        self._connect("thread_selected", self._on_thread_selected)
        self._connect("suggestion_clicked", self._on_suggestion)
        self._connect("chip_removed", lambda kind, value: log(f"chip removed: {kind}"))
        self._connect("files_dropped", self._on_files_dropped)
        self._connect("sign_out_requested", self._on_sign_out)
        self._connect("context_add_requested", self._on_context_add)
        self._connect("mode_changed", self._on_mode_changed)
        self._connect("effort_changed", self._on_effort_changed)
        self._connect("welcome_dismissed", self._on_welcome_dismissed)
        self._connect("pairing_reopen_requested", self._account.reopen_pairing_page)
        self._connect("pairing_cancel_requested", self._account.cancel_pairing)
        self._connect("thread_exported", lambda path: log(f"Thread exported to {path}"))
        self._connect("update_clicked", self._on_update_clicked)
        self._connect("update_dismissed", self._on_update_dismissed)
        self._connect("low_balance_shown", self._on_low_balance_shown)
        self._connect("example_chosen", self._on_example_chosen)
        self._connect("attachment_added", self._on_attachment_added)
        self._connect("dashboard_requested", lambda: log("Dashboard opened from the panel"))
        self._connect("upgrade_requested", self._on_upgrade_requested)
        self._connect("help_requested", self._on_help_requested)
        self._connect("open_settings_requested", self.settings_requested)


        self._connect("open_settings_requested", self._sync_permission_mode)
        self._connect("sign_in_requested", self._on_sign_in)

        self._connect_optional("recommendation_decided", self._on_recommendation_decided)
        self._connect_optional("diff_applied", self._on_diff_applied)
        self._connect_optional("feedback", self._on_feedback)

    def _wire_session(self) -> None:
        s = self._session
        s.state_changed.connect(self._on_state_changed)
        s.session_started.connect(self._on_session_started)
        s.token.connect(self._on_token)
        s.plan.connect(self._on_plan)
        s.plan_update.connect(self._on_plan_update)
        s.tool_call.connect(self._on_tool_call)
        s.run_end.connect(self._on_run_end)
        s.server_error.connect(self._on_server_error)
        s.usage.connect(self._on_usage)
        s.thread_title.connect(self._on_thread_title)
        s.memory_note.connect(self._on_memory_note)
        s.status_line.connect(self._on_status_line)
        s.sources.connect(self._on_sources)
        s.counts.connect(self._on_counts)

    def _wire_executor(self) -> None:
        e = self._executor
        e.tool_started.connect(self._on_tool_started)
        e.tool_finished.connect(self._on_tool_finished)
        e.permission_needed.connect(self._on_permission_needed)
        e.permission_resolved.connect(self._on_permission_resolved)
        e.question_needed.connect(self._on_question_needed)
        e.question_resolved.connect(lambda cid, answer: self._panel_call("resolve_question", cid, answer))
        e.project_changed.connect(self._on_project_changed)

    def _wire_account(self) -> None:
        a = self._account
        a.paired.connect(self._on_paired)
        a.signed_out.connect(self._on_signed_out)
        a.session_expired.connect(self._on_session_expired)



        a.pairing_started.connect(lambda url: self._panel_call("set_pairing_state", True, "", url))
        a.pairing_browser_seen.connect(lambda: self._panel_call(
            "set_pairing_status", tr("Sign-in page open, waiting for you...")))
        a.pairing_address.connect(lambda message: self._panel_call("set_pairing_note", message, "warning"))
        a.pairing_stalled.connect(
            lambda _reason, message: self._panel_call("set_pairing_note", message, "info"))
        a.pairing_failed.connect(self._on_pairing_failed)
        a.pairing_timeout.connect(lambda: self._on_pairing_failed(tr("Sign-in timed out. Try again."), "TIMEOUT"))
        a.notice.connect(self.notice)
        a.state_changed.connect(self._on_account_state)
        a.usage_refreshed.connect(self._on_website_usage)

    def _approval(self, approval: str = "") -> str:
        """The approval level this account may actually run at."""
        approval = approval if approval in Approval.ALL else self._settings.approval
        return Approval.ASK if (approval == Approval.AUTO and not autopilot_allowed()) else approval

    def _effort(self) -> str:
        """The effort level this account may actually run at: a stored paid level on a free account leaves the plugin as Low, the same clamp Autopilot."""

        effort = self._settings.effort
        return effort if (effort in Effort.ALL and effort_allowed(effort)) else Effort.LOW



    def _on_send(self, text: str, mode: str, approval: str, chips, attachments) -> None:
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
            self._panel_call("show_error", None, "OFFLINE",
                             tr("Not connected to the agent service yet. Reconnecting, retry in a moment."), True, "")
            return
        mode = mode if mode in (Mode.ASK, Mode.AGENT) else self._settings.mode
        approval = approval if approval in Approval.ALL else self._settings.approval



        approval = self._approval(approval)


        effort = self._effort()
        chips = [c for c in (chips or []) if isinstance(c, dict)]
        attachments = [a for a in (attachments or []) if isinstance(a, dict)]


        remember_user_text(text)
        allow_attached_paths([a.get("path") for a in attachments if a.get("path")])
        chips += self._load_file_attachments(attachments)
        new_thread = not self._thread_id
        if not self._thread_id:
            self._thread_id = self._store.create(project_path=_project_path())["id"]
            telemetry.track(ev.THREAD_CREATED)
        run_id = uuid.uuid4().hex
        try:
            context = build_context(chips)
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
        self._run = {"run_id": run_id, "thread_id": self._thread_id, "mode": mode, "approval": approval,
                     "effort": effort, "text": text, "started": time.monotonic(), "cancelled": False,


                     "sent": (attachments, context), "resends": 0}
        telemetry.track(ev.AGENT_RUN_STARTED, {
            "run_id": run_id, "mode": mode, "approval": approval, "effort": effort,
            "attachment_count": len(attachments), "chip_count": len(chips), "new_thread": new_thread})
        if telemetry.first_run_recorded():
            telemetry.track(ev.FIRST_RUN_MILESTONE)
        self._last_run = {"text": text, "mode": mode, "approval": approval, "chips": chips, "attachments": attachments}
        self._agent_text[run_id] = ""
        self._remember_run_project(run_id)
        self._session.set_run_open(True)
        self._executor.begin_run(run_id, mode, approval, self._thread_id or "", text)
        self._panel_call("set_display_options", self._settings.explain_runs, self._settings.show_tool_details)
        self._panel_call("begin_run", run_id)
        self._panel_call("append_user_message", run_id, text, chips, attachments)
        self._store.append_message(self._thread_id, user_message_record(run_id, text, chips, attachments))
        self._store.update_agent_message(self._thread_id, run_id, {"status": "running"})
        if not self._session.send_user_message(
                run_id, self._thread_id, text, attachments, context, mode, approval, effort):
            self._panel_call("show_error", run_id, "OFFLINE", tr("The message could not be sent."), True, "")
            self._finish_run(run_id, RunStatus.FAILED,
                             tr("Not connected to the agent service. Retry once the connection is back."), {}, None)
        else:
            self._touch_watchdog()
        self._panel_call("set_threads", self._store.list_threads(), _project_path())
        self._panel_call("set_current_thread", self._thread_id)

    def _on_suggestion(self, text: str) -> None:
        self._on_send(text, self._settings.mode, self._settings.approval, [], [])

    def _on_retry(self, run_id: str) -> None:
        last = self._last_run
        if last is None:
            return
        self._on_send(last["text"], last["mode"], last["approval"], last["chips"], last["attachments"])

    def _on_continue(self, run_id: str) -> None:
        """"Continue" on a budget stop: one word in the same thread, not the message again."""







        last = self._last_run
        mode = last["mode"] if last else self._settings.mode
        approval = last["approval"] if last else self._settings.approval
        self._on_send(CONTINUE_TEXT, mode, approval, [], [])

    def _on_stop(self, run_id: str) -> None:
        run = self._run
        if run is None or (run_id and run["run_id"] != run_id):
            return
        run["cancelled"] = True
        self._session.send_cancel(run["run_id"])
        self._executor.cancel_run(run["run_id"])
        self._cancel_proposals(run["run_id"])
        self._panel_call("set_status_line", run["run_id"], tr("Stopping..."))
        self._end_timer.start(CANCEL_GRACE_MS)

    def _end_run_locally(self) -> None:
        run = self._run
        if run is None:
            return
        status = RunStatus.CANCELLED if run.get("cancelled") else RunStatus.FAILED




        summary = str(run.get("error_message") or "") or tr("The run ended without a summary from the agent service.")
        self._finish_run(run["run_id"], status, summary, {}, None)

    def _resend_run(self) -> None:
        """Send the open run again, unchanged."""

        run = self._run
        if run is None or not run.get("resend_due"):
            return
        run["resend_due"] = False
        self._resend_timer.stop()
        if not self._session.is_online:


            run["resend_due"] = True
            return
        attachments, context = run.get("sent") or ([], {})
        if self._session.send_user_message(run["run_id"], run["thread_id"], run["text"], attachments, context,
                                           run["mode"], run["approval"], run["effort"]):
            self._panel_call("set_status_line", run["run_id"], tr("Thinking..."))
            self._touch_watchdog()
        else:
            run["resend_due"] = True



    def _touch_watchdog(self, seconds: int = RUN_SILENCE_S) -> None:
        run = self._run
        if run is None:
            return
        if run["run_id"] in self._call_runs.values():
            return
        self._watchdog.start(int(seconds) * 1000)

    def _pause_watchdog(self) -> None:
        """A tool runs here or a card waits for the user: the server is waiting on us."""
        self._watchdog.stop()

    def _on_server_silent(self) -> None:
        run = self._run
        if run is None:
            return
        log_warning(f"Run {run['run_id'][:8]}: nothing from the server for too long, ending it locally")
        self._session.send_cancel(run["run_id"])
        message = tr("The agent service stopped answering. The run was ended, you can retry it.")
        self._panel_call("show_error", run["run_id"], "TIMEOUT", message, True, "")
        track_plugin_error("watchdog", "TIMEOUT", run["run_id"])
        telemetry.track(ev.CONNECTION_FAILED, {"stage": "run", "error_code": "TIMEOUT",
                                               "duration_ms": self._run_age_ms(run)})
        self._finish_run(run["run_id"], RunStatus.FAILED, message, {}, None)



    @staticmethod
    def _run_age_ms(run: dict | None) -> int | None:
        started = (run or {}).get("started")
        return int((time.monotonic() - started) * 1000) if started else None

    def _on_state_changed(self, state: str, detail: str) -> None:
        self._panel_call("set_connection_state", state, detail)
        if state == "offline" and detail and self._run is None:


            telemetry.track(ev.CONNECTION_FAILED, {"stage": "connect", "error_code": "OFFLINE", "duration_ms": None})
        if state == "signed_out" and self._account.has_activation_key:

            self._account.refresh_activation_async(force=True)
        if state in ("offline", "connecting") and self._run is not None:

            self._panel_call("set_status_line", self._run["run_id"], tr("Connection lost. Reconnecting..."))

    def _on_session_started(self, session: dict) -> None:
        if self._run is not None and self._run.get("resend_due"):



            self._resend_run()
        elif self._run is not None and session.get("resumed") is False:



            run_id = self._run["run_id"]
            log_warning(f"Run {run_id[:8]}: the server could not resume the session, ending the run locally")
            message = tr("The connection dropped and the agent service no longer has this run. You can retry it.")
            self._panel_call("show_error", run_id, "LOST", message, True, "")
            track_plugin_error("resume", "LOST", run_id)
            telemetry.track(ev.CONNECTION_FAILED, {"stage": "resume", "error_code": "LOST",
                                                   "duration_ms": self._run_age_ms(self._run)})
            self._finish_run(run_id, RunStatus.FAILED, message, {}, None)
        elif self._run is not None:
            self._panel_call("set_status_line", self._run["run_id"],
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


        suggestions = session.get("suggestions")
        local = build_suggestions()
        if QgsProject.instance().mapLayers() or not (
                isinstance(suggestions, list) and len([s for s in suggestions if isinstance(s, str) and s]) >= 3):
            self._panel_call("set_suggestions", local)
        else:
            self._panel_call("set_suggestions", [str(s) for s in suggestions[:3]])
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
        self._touch_watchdog()
        self._panel_call("set_plan", run_id, steps)
        self._answer_start[run_id] = len(self._agent_text.get(run_id, ""))
        if self._thread_id:
            self._store.update_agent_message(self._thread_id, run_id, {"plan": steps})

    def _on_plan_update(self, run_id: str, step_id: str, state: str) -> None:
        if run_id not in self._agent_text:
            return
        self._touch_watchdog()
        self._panel_call("update_plan_step", run_id, step_id, state)

    def _on_status_line(self, run_id: str, text: str) -> None:



        if run_id not in self._agent_text:
            return
        self._touch_watchdog()
        self._panel_call("set_status_line", run_id, text)

    def _on_tool_call(self, call: dict) -> None:
        tool_call_id = str(call.get("tool_call_id") or "")
        run_id = str(call.get("run_id") or "")
        if run_id and run_id not in self._agent_text:


            log(f"Run {run_id[:8]} tool_call {tool_call_id[:8]} after the run ended, no card")
            self._executor.handle_tool_call(call)
            return
        self._touch_watchdog()
        self._call_runs[tool_call_id] = run_id
        self._call_names[tool_call_id] = (str(call.get("name") or ""), str(call.get("danger") or "read"))
        if run_id and _wants_basemap(str(call.get("name") or ""), call.get("args")):
            self._wanted_basemap.add(run_id)
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
        run_id = self._call_runs.pop(tool_call_id, "")
        self._call_names.pop(tool_call_id, None)
        self._touch_watchdog()
        if self._thread_id and run_id:
            self._store.append_tool_call(self._thread_id, run_id, {
                "tool_call_id": tool_call_id, "ok": ok, "summary": summary, "duration_s": round(duration, 2)})

    def _on_permission_needed(self, tool_call_id: str, run_id: str, sentence: str, args) -> None:
        name, danger = self._call_names.get(tool_call_id, ("", "read"))
        telemetry.track(ev.AGENT_PERMISSION_ASKED, {"run_id": run_id, "tool": name, "danger": danger})
        self._pause_watchdog()
        self._panel_call("ask_permission", tool_call_id, run_id, sentence, args)

    def _on_question_needed(self, tool_call_id: str, run_id: str, question: str, options,
                            allow_free_text: bool, recommended: int = -1, why: str = "") -> None:
        telemetry.track(ev.AGENT_QUESTION_ASKED, {"run_id": run_id, "options": len(options or [])})
        self._pause_watchdog()



        multiple = bool(self._question_args.pop(tool_call_id, {}).get("multiple"))
        extra = (multiple,) if multiple else ()
        self._panel_call("ask_question", tool_call_id, run_id, question, options, allow_free_text,
                         int(recommended), str(why or ""), self._settings.question_timeout_s, *extra)

    def _on_question_answered(self, tool_call_id: str, answer: str) -> None:
        telemetry.track(ev.AGENT_QUESTION_ANSWERED, {"skipped": not answer})
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
        if decision == Decision.DENY:
            self._close_call(tool_call_id)
        self._touch_watchdog()
        self._executor.on_permission_decided(tool_call_id, decision, edits if edited else None)
        self._panel_call("resolve_permission", tool_call_id, decision)

    def _close_call(self, tool_call_id: str) -> None:
        """Forget a call that ends without a result."""







        self._call_runs.pop(tool_call_id, None)
        self._call_names.pop(tool_call_id, None)

    def _on_permission_resolved(self, tool_call_id: str, decision: str) -> None:
        """The executor decided by itself: a cancelled run denies its open cards."""
        if decision == Decision.DENY:
            self._close_call(tool_call_id)
            self._touch_watchdog()
        self._panel_call("resolve_permission", tool_call_id, decision)

    def _on_project_changed(self, changed: int) -> None:
        """A layer changed: the chips refresh once the burst has settled."""







        self._pending_changed = int(changed)
        self._diff_timer.start()

    def _refresh_run_changes(self) -> None:
        run = self._run
        changed = self._pending_changed
        snapshot = self._executor.snapshot_for(run["run_id"]) if run else None
        touched: list = []
        if snapshot is not None and snapshot.captured:



            try:
                diff = snapshot.diff()
                touched = changed_layer_items(diff)
                changed = int(diff.get("changed_layers", changed))
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Snapshot diff failed: {exc}")
        self._panel_call("set_run_changes", changed, bool(snapshot and snapshot.captured), touched)

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
        self._end_timer.stop()
        self._resend_timer.stop()
        self._watchdog.stop()
        self._diff_timer.stop()
        self._session.set_run_open(False)

        self._cancel_proposals(run_id)
        if status == RunStatus.DONE and not self._settings.onboarded:
            self._settings.onboarded = True
            self._panel_call("set_welcome_visible", False)
        if status != RunStatus.DONE:

            self._executor.cancel_run(run_id)
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

        self._panel_call("set_run_changes", changed, self._history_available(), touched)
        self._send_history()
        self._panel_call("end_run", run_id, status, summary, usage or {}, merged)
        self._offer_cleanup(run_id, status)
        self._offer_quickmapservices(run_id)
        if status == RunStatus.QUOTA:
            self._panel_call("show_quota_pause", run_id, summary or tr("You have used every run of this period."))


            if self._quota_tracked_run != run_id:
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
                "text": self._take_agent_text(run_id),
                "answer_start": self._answer_start.pop(run_id, 0)})
            self._panel_call("set_threads", self._store.list_threads(), _project_path())
        if self._run and self._run["run_id"] == run_id:
            self._run = None
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
        if (code == ServerErrorCode.BUSY and retryable and run is not None
                and (not run_id or run_id == run["run_id"]) and run.get("resends", 0) < BUSY_RESENDS):






            run["resends"] = run.get("resends", 0) + 1
            run["resend_due"] = True
            log_warning(f"Run {run['run_id'][:8]} refused ({message}); resend {run['resends']} of {BUSY_RESENDS}")
            self._panel_call("set_status_line", run["run_id"], tr("The service is updating. Resuming..."))
            self._watchdog.stop()
            self._resend_timer.start(BUSY_RESEND_MS)
            return
        if run is not None and (not run_id or run_id == run["run_id"]):
            run["error_message"] = message
        self._panel_call("show_error", run_id if isinstance(run_id, str) else None, code, message, retryable, "")
        track_plugin_error("server", code, run_id if isinstance(run_id, str) else "")
        if code == ServerErrorCode.QUOTA_EXHAUSTED:
            self._quota_tracked_run = run_id if isinstance(run_id, str) else None
            telemetry.track(ev.QUOTA_EXHAUSTED, {"run_id": self._quota_tracked_run, "source": "server_error"})
        if code == ServerErrorCode.QUOTA_EXHAUSTED and self._run:
            self._panel_call("show_quota_pause", self._run["run_id"], message)
        if self._run and (not run_id or run_id == self._run["run_id"]) and not self._end_timer.isActive():

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
        self._touch_watchdog()
        self._panel_optional("set_run_counts", run_id, int(tool_calls), int(messages))



    def _on_proposal(self, call: dict) -> None:
        """propose_action and propose_edits never run a handler: the panel shows a card and the user's decision is the tool result, exactly like."""

        tool_call_id = str(call.get("tool_call_id") or "")
        run_id = str(call.get("run_id") or "")
        name = str(call.get("name") or "")
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        if tool_call_id in self._proposals:
            return
        slot = "ask_recommendation" if name == "propose_action" else "show_diff_table"
        if not hasattr(self._panel, slot):
            self._session.send_tool_error(tool_call_id, run_id, ClientErrorCode.CANCELLED,
                                          tr("This plugin build cannot show that card."),
                                          "Ask the user with ask_user instead.")
            self._close_call(tool_call_id)
            return
        self._proposals[tool_call_id] = call
        self._pause_watchdog()

        self._session.send_permission_response(tool_call_id, run_id, Decision.PENDING)
        title = " ".join(str(args.get("title") or "").split())
        if self._thread_id:
            self._store.append_tool_call(self._thread_id, run_id, {
                "tool_call_id": tool_call_id, "name": name, "args": args,
                "danger": "read", "sentence": title, "ok": None})
        if name == "propose_action":
            raw = args.get("alternatives") if isinstance(args.get("alternatives"), list) else []
            alternatives = [str(a).strip() for a in raw if str(a or "").strip()][:3]
            confidence = args.get("confidence") if args.get("confidence") in ("high", "medium", "low") else "medium"
            self._panel_call("ask_recommendation", tool_call_id, run_id, title,
                             str(args.get("proposal") or ""), str(args.get("entity") or ""),
                             str(args.get("value") or ""), confidence, alternatives)
            return
        raw_columns = args.get("columns") if isinstance(args.get("columns"), list) else []
        columns = [str(c) for c in raw_columns]
        rows = []
        for row in (args.get("rows") if isinstance(args.get("rows"), list) else [])[:PROPOSAL_MAX_ROWS]:
            if not isinstance(row, dict):
                continue
            kind = row.get("kind") if row.get("kind") in ("added", "removed", "unchanged") else "unchanged"
            cells = row.get("cells") if isinstance(row.get("cells"), list) else []
            rows.append({"id": str(row.get("id") or ""), "kind": kind, "cells": [str(c) for c in cells]})
        self._panel_call("show_diff_table", run_id, title, columns, rows)

    def _answer_proposal(self, tool_call_id: str, result: dict | None, summary: str) -> None:
        call = self._proposals.pop(tool_call_id, None)
        if call is None:
            return
        run_id = str(call.get("run_id") or "")
        if result is None:
            self._session.send_tool_error(tool_call_id, run_id, ClientErrorCode.CANCELLED,
                                          tr("The user dismissed the proposal."),
                                          "Stop here and wait for the next user message.")
        else:
            self._session.send_tool_result(tool_call_id, run_id, result)
        self._close_call(tool_call_id)
        self._touch_watchdog()
        log(f"PROPOSAL {tool_call_id}: {summary[:80]}")
        if self._thread_id and run_id:
            self._store.append_tool_call(self._thread_id, run_id, {
                "tool_call_id": tool_call_id, "ok": result is not None, "summary": summary, "duration_s": 0.0})

    def _on_recommendation_decided(self, tool_call_id: str, decision: str) -> None:
        """accept, alternative:<text> or dismiss, from the recommendation card."""
        decision = str(decision or "").strip()
        if decision == "accept":
            self._answer_proposal(tool_call_id, {"decision": "accept"}, "accept")
        elif decision.startswith("alternative"):
            text = decision.split(":", 1)[1].strip() if ":" in decision else ""
            self._answer_proposal(tool_call_id, {"decision": "alternative", "text": text}, f"alternative: {text}")
        else:
            self._answer_proposal(tool_call_id, None, "dismissed")

    def _on_diff_applied(self, run_id: str, ids) -> None:
        """The rows the user kept in the diff table; the open propose_edits of that run answers."""
        for tool_call_id, call in list(self._proposals.items()):
            if call.get("name") == "propose_edits" and str(call.get("run_id") or "") == str(run_id or ""):
                applied = [str(i) for i in (ids or []) if str(i).strip()] if isinstance(ids, (list, tuple)) else []
                self._answer_proposal(tool_call_id, {"applied": applied}, f"applied {len(applied)}")
                return

    def _cancel_proposals(self, run_id: str) -> None:
        for tool_call_id, call in list(self._proposals.items()):
            if str(call.get("run_id") or "") == run_id:
                self._answer_proposal(tool_call_id, None, "cancelled")



    def _offer_cleanup(self, run_id: str, status: str) -> None:
        """Offer back the layers this run made only to make another one."""











        if status != RunStatus.DONE:
            return
        try:
            items = list(self._executor.scratch.last_report or [])
        except Exception as exc:  # noqa: BLE001 - a ledger never breaks the end of a run
            log_warning(f"Cleanup offer skipped: {exc}")
            return
        if len(items) < MIN_TO_OFFER:
            return
        self._panel_optional("offer_cleanup", run_id, items)

    def _offer_quickmapservices(self, run_id: str) -> None:
        """A run that wanted a basemap on a QGIS without QuickMapServices."""












        if run_id not in self._wanted_basemap:
            return
        self._wanted_basemap.discard(run_id)
        try:
            from ..ui.cards_plugin_offer import answered_offers
            from ..ui.cross_plugin_discovery import is_quickmapservices_installed

            if QMS_OFFER_ID in answered_offers() or is_quickmapservices_installed():
                return
        except Exception as exc:  # noqa: BLE001 - an offer never breaks the end of a run
            log_warning(f"QuickMapServices offer skipped: {exc}")
            return
        self._panel_optional(
            "offer_plugin", QMS_OFFER_ID,
            tr("Basemaps here are limited without QuickMapServices."),
            tr("It is the free QGIS plugin that carries the basemaps people mean by name, "
               "Google, Bing, Esri, CartoDB and about two hundred more. Installed, I use it "
               "on my own."),
            tr("Install it"))

    def _on_plugin_offer_decided(self, offer_id: str, decision: str) -> None:
        """Install opens the QGIS plugin manager on that plugin's own card."""





        offer_id, decision = str(offer_id or ""), str(decision or "")
        if offer_id != QMS_OFFER_ID:
            return
        if decision != "install":
            self._panel_optional("finish_plugin_offer", offer_id, tr("Not now"))
            return
        sentence = tr("Opened the plugin manager")
        try:
            from ..ui.cross_plugin_discovery import install_quickmapservices

            if install_quickmapservices() == "website":
                sentence = tr("Opened the plugin's page")
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Plugin manager not opened: {exc}")
            sentence = tr("The plugin manager would not open")
        self._panel_optional("finish_plugin_offer", offer_id, sentence)

    def _on_cleanup_decided(self, run_id: str, decision: str, ids) -> None:
        """Delete them, tidy them into one collapsed group, or keep them."""





        run_id, decision = str(run_id or ""), str(decision or "")
        ids = [str(i) for i in (ids or []) if str(i).strip()]
        sentence = ""
        if decision == "delete":
            gone = remove_scratch(ids)
            sentence = (tr("Deleted 1 working layer") if len(gone) == 1
                        else tr("Deleted {n} working layers").format(n=len(gone)))
        elif decision == "group":
            moved = group_scratch(ids, tr("Working layers"))
            sentence = (tr("Tidied 1 working layer away") if moved == 1
                        else tr("Tidied {n} working layers away").format(n=moved))
        else:
            sentence = (tr("Kept 1 working layer") if len(ids) == 1
                        else tr("Kept {n} working layers").format(n=len(ids)))
        log(f"Cleanup {decision} on {len(ids)} layers of run {run_id[:8]}")
        self._panel_optional("finish_cleanup", run_id, sentence)

    def _on_feedback(self, run_id: str, up: bool) -> None:
        """The thumbs under an answer: sent to the server, which keeps it on the run row."""
        run_id = str(run_id or "")
        if not run_id:
            return
        self._session.send_feedback(run_id, bool(up))
        log(f"Feedback {run_id[:8]}: {'up' if up else 'down'}")

    def _on_memory_note(self, text: str, kind: str, scope: str, run_id: str = "") -> None:
        """The note the finished run left behind: store it, then say so."""







        if not str(text or "").strip():
            return
        if not bool(getattr(self._settings, "memory_enabled", True)):
            return
        project = self._run_projects.get(str(run_id or ""))
        if project is None:



            log_warning(f"Memory note dropped: run {str(run_id)[:8] or '?'} is not one of ours")
            return
        try:
            note = add_memory_note(self._settings, text, "ai", kind, scope, project)
        except Exception as exc:  # noqa: BLE001 - a note is never worth breaking the panel
            log_warning(f"Memory note not stored: {exc}")
            return
        if note is not None:
            self._panel_call("note_memory", note["text"])



    def _on_sign_in(self) -> None:
        if self._account.state == Account.PAIRING:
            self._account.reopen_pairing_page()
            return
        self._pairing_started = time.monotonic()
        telemetry.track(ev.PAIRING_STARTED)
        self._account.start_pairing()

    def _on_paired(self, prefix: str) -> None:
        log(f"Signed in with key {prefix}...")



        self._raise_qgis()
        telemetry.track(ev.PLUGIN_ACTIVATED, {"activation_method": "pairing"})
        telemetry.flush()
        self._session.forget_session()
        self._panel_call("set_connection_state", "connecting", "")
        self._session.connect_to_server()
        self.notice.emit("info", tr("Signed in to TerraLab."))

    def _on_signed_out(self) -> None:
        self._session.disconnect_from_server()
        self._session.forget_session()
        self._panel_call("set_connection_state", "signed_out", "")

    def _on_session_expired(self, message: str) -> None:
        self._session.disconnect_from_server()
        self._session.forget_session()
        self._panel_call("set_connection_state", "signed_out", message)
        self.notice.emit("warning", message)

    def _on_pairing_failed(self, message: str, code: str) -> None:
        telemetry.track(ev.PAIRING_FAILED, {"error_code": code, "duration_ms": self._since_pairing_ms()})
        self._panel_call("set_connection_state", "signed_out", message)
        self._panel_call("show_error", None, code, message, False, "")

    def _raise_qgis(self) -> None:
        """Bring QGIS back to the front after the browser round trip."""
        try:
            from .window_focus import bring_qgis_window_to_front

            window = self._iface.mainWindow() if self._iface is not None else None
            dock = getattr(self._panel, "window", None)
            bring_qgis_window_to_front(window, dock() if callable(dock) else None)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"QGIS could not be raised after the sign-in: {exc}")

    def _since_pairing_ms(self) -> int | None:
        started = self._pairing_started
        self._pairing_started = None
        return int((time.monotonic() - started) * 1000) if started else None

    def _on_account_state(self, state: str, message: str) -> None:
        if state == Account.LOCKED:
            self._panel_call("set_connection_state", "offline", message)
