# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Panel actions that touch no run state: threads, history, files, trace, telemetry clicks."""





from __future__ import annotations

import os
import time

from qgis.PyQt.QtCore import QCoreApplication

from . import telemetry
from . import telemetry_events as ev
from .checkpoints import KIND_AFTER, KIND_BEFORE, KIND_EDITS
from .context import build_suggestions, mention_candidates
from .logger import log, log_warning
from .protocol import Approval, Effort, Mode
from .snapshot import diff_changed


def tr(text: str) -> str:
    return QCoreApplication.translate("AgentController", text)


class PanelActionsMixin:
    """Slots for the panel signals that manage threads, history, files and the trace."""








    _restoring = False



    def _on_project_opened(self, *_args) -> None:
        if self._restoring:

            return
        if self._run is None and self._thread_id is not None:
            self._on_new_thread()
        else:
            self._panel_call("set_suggestions", build_suggestions())

    def _on_new_thread(self) -> None:
        if self._run is not None:
            self.notice.emit("info", tr("Stop the current run before starting a new chat."))
            return
        self._thread_id = None

        if self._settings.reset_autopilot():
            self._sync_permission_mode()
        self._panel_call("clear_thread")
        self._panel_call("set_current_thread", "")
        self._panel_call("set_run_changes", 0, False)

        self._send_history()
        self._panel_call("set_suggestions", build_suggestions())

    def _on_thread_selected(self, thread_id: str) -> None:
        if self._run is not None:
            self.notice.emit("info", tr("Stop the current run before switching chats."))
            return
        thread = self._store.load(thread_id)
        if thread is None:
            self.notice.emit("warning", tr("This chat is gone."))
            self._panel_call("set_threads", self._store.list_threads(), _project_path())
            return
        self._thread_id = thread_id
        self._panel_call("load_thread", thread.get("messages", []))
        self._panel_call("set_current_thread", thread_id)
        self._panel_call("set_run_changes", 0, False)


        self._send_history()



    def _history_available(self) -> bool:
        thread_id = self._thread_id or ""
        history = self._executor.history
        return bool(history.previous(thread_id) or history.next(thread_id))

    def _send_history(self) -> None:
        self._panel_call("set_history", self._executor.history.describe(self._thread_id or ""))

    def _on_undo(self) -> None:
        """The last run, undone: the checkpoint before the current one."""
        entry = self._executor.history.previous(self._thread_id or "")
        if entry is None:
            self.notice.emit("info", tr("Nothing to undo."))
            return
        self._on_restore(entry.id, False)

    def _on_discard_all(self, confirmed: bool = False) -> None:
        entry = self._executor.history.first(self._thread_id or "")
        if entry is None:
            self.notice.emit("info", tr("Nothing to discard: this chat changed nothing yet."))
            return
        self._on_restore(entry.id, confirmed, discard=True)

    def _on_restore(self, checkpoint_id: str, confirmed: bool = False, discard: bool = False) -> None:
        """Put the project back at a checkpoint of this chat."""






        if self._restoring:



            return
        history = self._executor.history
        thread_id = self._thread_id or ""
        entry = history.find(checkpoint_id)
        if entry is None or not entry.available:
            self.notice.emit("info", tr("This state is no longer available."))
            self._send_history()
            return
        if self._run is not None:
            self.notice.emit("info", tr("Stop the current run before going back."))
            return
        current = history.current(thread_id)
        if current is not None and current.id == entry.id and not discard:
            return
        edits = self._manual_changes_since(current)
        if (edits or discard) and not confirmed:



            whole = not discard or history.start_reachable(thread_id)
            self._panel_call("show_restore_warning", checkpoint_id, discard, edits, whole)
            return
        if edits and current is not None and not self._capture_edits(thread_id, current):



            self.notice.emit("warning", tr(
                "Your changes since this point could not be saved, so nothing was restored. "
                "Save the project and try again."))
            return
        self._restoring = True
        try:
            result = entry.snapshot.restore()
        finally:
            self._restoring = False
        log(f"Restore to {entry.kind} run {entry.run_index}: {result.get('message') or result}")
        steps = history.steps_between(thread_id, entry)
        telemetry.track(ev.AGENT_UNDO_USED, {
            "ok": bool(result.get("ok")), "kind": "discard" if discard else entry.kind, "steps_back": steps})
        if result.get("ok"):
            history.mark_current(entry)
        self._note_restored_state(entry if result.get("ok") else None)
        self._send_history()
        self.notice.emit("info" if result.get("ok") else "warning", str(result.get("message") or ""))

    def _manual_changes_since(self, current) -> bool:
        """True when the project moved since the current checkpoint outside a run."""
        if current is None or not current.available:
            return False
        try:
            return diff_changed(current.snapshot.diff())
        except Exception as exc:  # noqa: BLE001
            log(f"Checkpoint diff failed: {exc}")
            return False

    def _capture_edits(self, thread_id: str, current) -> bool:
        """Keep the state being left, so the restore about to run can be undone."""





        from .snapshot import RunSnapshot

        snapshot = RunSnapshot(f"{current.run_id or 'run'}-edits-{int(time.time())}")
        try:
            captured = snapshot.capture()
        except Exception as exc:  # noqa: BLE001
            log(f"Edits snapshot failed: {exc}")
            captured = False
        if captured:
            self._executor.history.add(thread_id, KIND_EDITS, current.run_id, current.run_index,
                                       snapshot, fork=False)
        return bool(captured)

    def _note_restored_state(self, entry) -> None:
        """After a restore, one quiet line in the chat says where the project stands."""


        if entry is None:
            return
        if entry.kind == KIND_AFTER:
            note = tr("Back to after run {n}").format(n=entry.run_index)
        elif entry.kind == KIND_BEFORE:
            note = tr("Back to before run {n}").format(n=entry.run_index)
        else:
            note = tr("Back to your edits after run {n}").format(n=entry.run_index)
        self._panel_call("note_project_state", note)

    def _on_layer_action(self, layer_id: str, action: str) -> None:
        telemetry.track(ev.REVIEW_OPENED, {"action": action})
        self.layer_action_requested.emit(layer_id, action)

    def _on_sign_out(self) -> None:
        telemetry.track(ev.ACCOUNT_SIGNED_OUT, {"source": "panel"})
        telemetry.flush()
        self._account.sign_out()

    def _on_welcome_dismissed(self) -> None:
        self._settings.onboarded = True
        telemetry.track(ev.WELCOME_DISMISSED)

    def _on_example_chosen(self, slug: str) -> None:
        telemetry.track(ev.EXAMPLE_CHOSEN, {"slug": str(slug or "")})

    def _on_update_clicked(self, version: str) -> None:
        telemetry.track(ev.PLUGIN_UPDATE_PROMPT_CLICKED, {
            "offered_version": version, "action": "plugin_manager"})

    def _on_update_dismissed(self, version: str) -> None:
        telemetry.track(ev.PLUGIN_UPDATE_PROMPT_CLICKED, {
            "offered_version": version, "action": "dismissed"})

    def _on_help_requested(self, kind: str) -> None:
        if kind == "report":


            has_error = bool(getattr(self._panel, "_last_error", ""))
            telemetry.track(ev.ERROR_REPORT_OPENED, {"has_error": has_error})
        elif kind == "contact":
            telemetry.track(ev.CONTACT_OPENED)
        elif kind == "tutorial":
            telemetry.track(ev.TUTORIAL_OPENED)
        log(f"Help opened: {kind}")

    def _on_upgrade_requested(self) -> None:
        telemetry.track(ev.SUBSCRIBE_LINK_CLICKED, {"source": "quota_card"})
        log("Upgrade opened from the panel")

    def _on_low_balance_shown(self, remaining: int) -> None:
        telemetry.track(ev.LOW_BALANCE_CARD_SHOWN, {"remaining": remaining})

    def _on_attachment_added(self, count: int) -> None:
        telemetry.track(ev.ATTACHMENT_ADDED, {"source": "composer", "count": count})

    def _on_mode_changed(self, mode: str, approval: str) -> None:
        if mode in (Mode.ASK, Mode.AGENT):
            self._settings.mode = mode
        if approval in Approval.ALL:
            previous = self._settings.approval
            self._settings.approval = approval
            if approval != previous:
                telemetry.track(ev.PERMISSION_LEVEL_CHANGED, {"level": approval, "previous": previous})

                self._executor.on_approval_changed(approval)
        self._sync_permission_mode()

    def _sync_permission_mode(self) -> None:
        self._panel_call("set_permission_mode", self._settings.approval)

    def _on_effort_changed(self, effort: str) -> None:
        """The composer's effort slider."""

        if effort not in Effort.ALL:
            return
        previous = self._settings.effort
        self._settings.effort = effort
        if effort != previous:
            telemetry.track(ev.EFFORT_CHANGED, {"effort": effort})
        self._panel_call("set_effort", self._settings.effort)

    def _on_context_add(self, kind: str) -> None:
        """The + button of the context bar: turn a kind into a chip (or a file attachment)."""
        from qgis.PyQt.QtWidgets import QFileDialog

        parent = self._panel if hasattr(self._panel, "window") else None
        if kind == "file":
            path, _filter = QFileDialog.getOpenFileName(
                parent, tr("Add a data file"), "",
                tr("Data files (*.gpkg *.geojson *.json *.shp *.csv *.tif *.tiff *.kml *.kmz *.zip);;All files (*)"))
            if path and hasattr(self._panel, "composer"):
                self._panel.composer.add_paths([path])
            return

        if not mention_candidates():
            self.notice.emit("info", tr("Nothing to add: open a layer first."))
            return
        composer = getattr(self._panel, "composer", None)
        begin = getattr(getattr(composer, "_input", None), "begin_mention", None)
        if callable(begin):
            begin()

    def _on_files_dropped(self, paths) -> None:
        """Informational: the panel already carries dropped files in send_requested."""
        count = len([p for p in (paths or []) if os.path.isfile(str(p))])
        if count:
            log(f"{count} file(s) attached to the composer")

    def _load_file_attachments(self, attachments: list) -> list:
        """Load every data-file attachment into the project through add_data."""





        chips = []
        for att in attachments:
            if att.get("kind") != "file" or not att.get("path"):
                continue
            path = str(att["path"])
            if not os.path.isfile(path):
                att["error"] = "file not found"
                continue
            result = self._registry.execute("add_data", {"source": path})
            if not isinstance(result, dict) or result.get("_error") or not result.get("layer_id"):
                att["error"] = str((result or {}).get("_error") or "not loaded")[:200]
                name = att.get("name") or os.path.basename(path)
                log_warning(f"Could not load {name}: {att['error']}")
                self.notice.emit("warning", tr("{name} could not be loaded. Check the file and try again.").format(
                    name=name))
                continue
            att["layer_id"] = str(result["layer_id"])
            att["layer_name"] = str(result.get("name") or result.get("layer_name") or att.get("name") or "")
            chips.append({"kind": "layer", "label": att["layer_name"], "value": att["layer_id"]})
        return chips

    def session_report(self, run_id: str = "", error_message: str = "") -> dict:
        """Everything this session knows about itself, as one object."""







        from . import session_export
        from .logger import recent_logs

        thread = None
        try:
            thread = self._store.load(self._thread_id) if self._thread_id else None
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Session report: the thread could not be read ({exc})")
        try:
            from . import tuning
            policy = tuning.snapshot()
        except Exception:  # noqa: BLE001
            policy = {}
        try:
            manifest_hash, manifest = self._manifest()
        except Exception:  # noqa: BLE001
            manifest_hash, manifest = "", []
        session = {
            "session_id": self._session.session_id or "",
            "state": self._session.state,
            "model": getattr(self._session, "model_label", ""),
            "effort": self._settings.effort,
            "mode": self._settings.mode,
            "approval": self._settings.approval,
            "server": _host_of(self._settings.server_url),
            "manifest_hash": manifest_hash,
            "tool_count": len(manifest or []),
        }
        account = {"state": self._account.state, "device_hash": self._account.device_hash}
        return session_export.build(
            thread=thread, identity=self._identity(), session=session, account=account,
            policy=policy, logs=recent_logs(), error=error_message, run_id=run_id,
            verification=self._last_verification)


def _host_of(url: str) -> str:
    """The server a report was talking to, without its path or credentials."""
    from urllib.parse import urlsplit

    try:
        return (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def _project_path() -> str:
    """Path of the open project, "" when unsaved; keys the history menu."""
    try:
        from qgis.core import QgsProject
        return QgsProject.instance().fileName() or ""
    except Exception:  # noqa: BLE001
        return ""
