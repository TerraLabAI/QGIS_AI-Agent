# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Controller: panel signals in, session and executor calls out, and back."""







from __future__ import annotations

from qgis.core import Qgis
from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal

from ..api.account import Account
from ..api.terralab_client import plugin_version
from . import telemetry
from . import telemetry_events as ev
from .context import mention_candidates
from .controller_account import _ControllerAccount
from .controller_actions import PanelActionsMixin, _project_path
from .controller_frames import _ControllerFrames
from .controller_offers import _ControllerOffers
from .controller_projects import _ControllerProjects
from .controller_runs import _ControllerRuns
from .controller_shared import (
    _DIFF_SETTLE_MS,
    CANCEL_GRACE_MS,
    RESUME_GRACE_S,
    RETRY_MEMORY,
    RUN_SILENCE_S,
    tr,
)
from .executor import ToolExecutor
from .host_platform import os_label
from .logger import log, log_warning
from .plan import autopilot_allowed, effort_allowed
from .protocol import Approval, Effort, RunStatus
from .run_state import RunMachine
from .session import AgentSession
from .settings import DEFAULT_SERVER_URL, Settings
from .telemetry_errors import report_exception
from .threads import ThreadStore


__all__ = ["AgentController", "CANCEL_GRACE_MS", "RESUME_GRACE_S", "RUN_SILENCE_S"]


class AgentController(_ControllerRuns, _ControllerFrames, _ControllerProjects, _ControllerOffers,
                      _ControllerAccount, PanelActionsMixin, QObject):
    layer_action_requested = pyqtSignal(str, str)
    settings_requested = pyqtSignal()
    notice = pyqtSignal(str, str)


    approval_waiting = pyqtSignal(bool)

    def __init__(self, iface, panel, registry, settings: Settings | None = None,
                 account: Account | None = None, parent=None, *, session=None, executor=None, store=None):
        super().__init__(parent)
        self._iface = iface
        self._panel = panel
        self._registry = registry
        self._settings = settings or Settings()
        self._account = account or Account(self._settings, self)
        self._store = store if store is not None else ThreadStore(on_index_ready=self._on_thread_index_ready)
        self._manifest_cache: tuple | None = None
        self._session = session if session is not None else AgentSession(
            self._settings, self._account, self._identity, self._manifest, self)
        self._executor = executor if executor is not None else ToolExecutor(
            registry, self._session, self._settings, self)
        self._thread_id: str | None = None

        self._runs = RunMachine(RETRY_MEMORY)
        self._quota_tracked_run: str | None = None
        self._pairing_started: float | None = None
        self._last_run: dict | None = None
        self._last_verification: dict | None = None
        self._agent_text: dict[str, str] = {}



        self._run_projects: dict[str, str] = {}
        self._text_break: dict[str, bool] = {}


        self._answer_start: dict[str, int] = {}




        self._replay_run: str | None = None
        self._call_runs = self._runs.calls
        self._call_names: dict[str, tuple] = {}
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
        self._lost_timer = QTimer(self)
        self._lost_timer.setSingleShot(True)
        self._lost_timer.timeout.connect(self._on_resume_outcome_missing)
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

    def shutdown(self) -> None:
        self._unwatch_project()





        for timer in (self._end_timer, self._watchdog, self._resend_timer, self._lost_timer, self._diff_timer):
            timer.stop()

        self._executor.cancel_background()


        if getattr(self._account, "state", None) == getattr(Account, "PAIRING", object()):
            try:
                self._account.cancel_pairing()
            except Exception as exc:  # noqa: BLE001 - an unload never stops on the sign-in
                log_warning(f"Sign-in poll not cancelled on unload: {exc}")
        run = self._run
        if run is not None and not run.get("cancelled"):



            try:
                self._session.send_cancel(run["run_id"])
            except Exception as exc:  # noqa: BLE001 - an unload never stops on the cancel
                log_warning(f"Run {run['run_id'][:8]}: no cancel sent on unload: {exc}")




            self._store_ending(run["run_id"], RunStatus.CANCELLED, tr("Stopped: QGIS closed during this run."), {})

        self._session.disconnect_from_server(wait=True)

        self._store.close()
        telemetry.shutdown()

    @property
    def session(self) -> AgentSession:
        return self._session

    @property
    def account(self) -> Account:
        return self._account

    @property
    def _run(self) -> dict | None:
        """The open run's record; RunMachine owns it."""
        return self._runs.current



    def _identity(self) -> dict:
        try:
            qgis_version = str(Qgis.QGIS_VERSION).split("-")[0]
        except Exception:
            qgis_version = ""
        return {"plugin_version": plugin_version(), "qgis_version": qgis_version,
                "os": os_label(), "locale": self._settings.locale}

    def _manifest(self) -> tuple:
        if self._manifest_cache is None:
            manifest = self._registry.manifest()
            self._manifest_cache = (self._registry.manifest_hash(manifest), manifest)
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
            report_exception(exc, "panel_slot", module=type(self._panel).__module__, slot=name)

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
        self._connect_optional("pro_pill_requested", self._on_pro_pill_requested)
        self._connect_optional("reconnect_requested", self._on_reconnect_requested)
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
        if hasattr(s, "server_tool"):
            s.server_tool.connect(self._on_server_tool)
        if hasattr(s, "connection_failed"):
            s.connection_failed.connect(self._on_connection_failed)

    def _wire_executor(self) -> None:
        e = self._executor
        e.tool_started.connect(self._on_tool_started)
        e.tool_finished.connect(self._on_tool_finished)
        e.permission_needed.connect(self._on_permission_needed)
        e.permission_resolved.connect(self._on_permission_resolved)
        e.question_needed.connect(self._on_question_needed)
        e.question_resolved.connect(lambda cid, answer: self._panel_call("resolve_question", cid, answer))
        e.project_changed.connect(self._on_project_changed)
        e.run_blocked.connect(self._on_run_blocked)

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
        self._session.session_started.connect(self._bind_unlocked_account)

    def _approval(self, approval: str = "") -> str:
        """The approval level this account may actually run at."""
        approval = approval if approval in Approval.ALL else self._settings.approval
        return Approval.ASK if (approval == Approval.AUTO and not autopilot_allowed()) else approval

    def _effort(self) -> str:
        """The effort level this account may actually run at: a stored paid level on a free account leaves the plugin as Low, the same clamp Autopilot."""

        effort = self._settings.effort
        return effort if (effort in Effort.ALL and effort_allowed(effort)) else Effort.LOW



    def _on_reconnect_requested(self) -> None:
        """Send pressed while the socket is down: skip the backoff and try now."""



        if self._session.state in ("online", "connecting"):
            return
        url = self._settings.server_url
        if url != DEFAULT_SERVER_URL:
            log_warning(f"Reconnecting to {url}, which is not the default service")
        self._panel_call("set_connection_state", "connecting", "")
        self._session.connect_to_server()
