# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

import os
import uuid

from qgis.PyQt.QtCore import QCoreApplication, QTimer

from . import telemetry
from . import telemetry_events as ev
from .checkpoints import KIND_BEFORE, KIND_EDITS
from .context import mention_candidates
from .logger import log, log_warning
from .protocol import Approval, Effort, Mode
from .snapshot import diff_changed
from .telemetry_errors import track_plugin_error


def tr(text: str) -> str:
    return QCoreApplication.translate("AgentController", text)


def _restore_layers(rows) -> list:





    order: list = []
    groups: dict = {}
    for row in rows or []:
        source = (row.get("after") or row.get("before") or {}) if isinstance(row, dict) else {}
        for item in source.get("log") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("layer") or "")
            if not name:
                continue
            what = str(item.get("what") or "")
            group = "removed" if what == "added" else "restored" if what else ""
            if name not in groups:
                order.append(name)
                groups[name] = group
            elif group == "removed" or not groups[name]:

                groups[name] = group or groups[name]
    return [{"name": name, "group": groups[name]} for name in order]


def _restore_error_code(result: dict) -> str:

    message = str(result.get("message") or "")
    if result.get("refused"):
        return "STILL_SAVING"
    if message.startswith("No snapshot"):
        return "NO_SNAPSHOT"
    if not result.get("layers") and "project" in message:
        return "PROJECT_UNREADABLE"
    if result.get("file_restore_errors"):
        return "FILES_NOT_RESTORED"
    if result.get("memory_layers_incomplete"):
        return "MEMORY_INCOMPLETE"
    return "INCOMPLETE"


class PanelActionsMixin:









    _restoring = False


    _restore_after_stop = ""



    def _on_project_opened(self, *_args) -> None:
        if self._restoring:

            return
        if self._run is None and self._thread_id is not None:
            self._on_new_thread()

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

    def _on_thread_delete(self, thread_id: str) -> None:




        thread_id = str(thread_id or "")
        if not thread_id:
            return
        if self._run is not None and thread_id == self._thread_id:
            self.notice.emit("info", tr("Stop the current run before deleting this chat."))
            self._panel_call("set_threads", self._store.list_threads(), _project_path())
            return
        history = self._executor.history
        snapshots = [entry.snapshot for entry in history.entries(thread_id) if entry.snapshot is not None]

        history.clear(thread_id)
        for snapshot in snapshots:
            snapshot.discard()
        self._store.delete(thread_id)
        if thread_id == self._thread_id:
            self._on_new_thread()
        self._panel_call("set_threads", self._store.list_threads(), _project_path())



    def _history_available(self) -> bool:
        thread_id = self._thread_id or ""
        history = self._executor.history
        return bool(history.previous(thread_id) or history.next(thread_id))

    def _send_history(self) -> None:
        thread_id = self._thread_id or ""
        history = self._executor.history
        self._panel_call("set_history", history.describe(thread_id), history.undone_runs(thread_id))

    def _on_undo(self) -> None:

        self._executor.record_pending_after()
        entry = self._executor.history.previous(self._thread_id or "")
        if entry is None:
            self.notice.emit("info", tr("Nothing to undo."))
            return
        self._on_restore(entry.id, False)

    def _on_discard_all(self, confirmed: bool = False) -> None:
        self._executor.record_pending_after()
        entry = self._executor.history.first(self._thread_id or "")
        if entry is None:
            self.notice.emit("info", tr("Nothing to discard: this chat changed nothing yet."))
            return
        self._on_restore(entry.id, confirmed, discard=True)

    def _on_restore(self, checkpoint_id: str, confirmed: bool = False, discard: bool = False) -> None:










        if self._restoring:



            return

        self._executor.record_pending_after()
        history = self._executor.history
        thread_id = self._thread_id or ""
        entry = history.find(checkpoint_id)
        if entry is None or not entry.available:
            self.notice.emit("info", tr("This version is no longer kept."))
            self._send_history()
            return
        if self._run is not None:
            self.notice.emit("info", tr("Stop the current run before going back."))
            return
        if not history.belongs_here(entry):


            name = entry.project_name
            if name:
                message = tr("This point belongs to the project {name}. Open that project to go back to it.").format(
                    name=name)
            else:
                message = tr("This point belongs to an unsaved project that was closed, so it cannot be restored here.")
            self.notice.emit("warning", message)
            self._send_history()
            return
        current = history.current(thread_id)
        if current is not None and current.id == entry.id and not discard:
            return
        edits = self._manual_changes_since(current)
        if not discard and not edits and history.same_state(thread_id, entry):


            self._send_history()
            return
        removed = self._requests_removed(entry)
        if (edits or discard or len(removed) > 1) and not confirmed:



            whole = not discard or history.start_reachable(thread_id)
            self._panel_call("show_restore_warning", checkpoint_id, discard, edits, whole,
                             len(removed), _restore_layers(removed))
            return
        refusal = self._capture_edits(thread_id, current, entry) if edits else ""
        if refusal:



            track_plugin_error("restore", "EDITS_NOT_CAPTURED")
            self.notice.emit("warning", refusal)
            return


        left = history.current(thread_id)
        steps = history.steps_between(thread_id, entry)
        self._restoring = True
        try:



            result = entry.snapshot.restore(file_name=entry.file_to_keep(),
                                            extra_copies=history.file_versions(thread_id, entry))
        finally:
            self._restoring = False

            history.project_restored()
        log(f"Restore to {entry.kind} run {entry.run_index}: {result.get('message') or result}")
        ok = bool(result.get("ok"))
        telemetry.track(ev.AGENT_UNDO_USED, {
            "ok": ok, "kind": "discard" if discard else entry.kind, "steps_back": steps})
        if not ok:

            track_plugin_error("restore", _restore_error_code(result), where=str(entry.kind))
        if result.get("project_read"):


            history.mark_current(entry)

            self._restored_for_retry(entry.id)
        self._send_history()
        back = steps >= 0

        revert = left.id if (ok and left is not None and left.id != entry.id and left.available) else ""
        self._panel_call("show_restore_result", ok, back, revert, self._missing_layers(entry, result))

    def _on_restore_after_stop(self, run_id: str) -> None:





        run_id = str(run_id or "")
        run = self._run
        if run is not None and (not run_id or run.get("run_id") == run_id):
            self._restore_after_stop = run["run_id"]
            return
        self._go_back_before(run_id or str(getattr(self._runs, "last_run_id", "") or ""))

    def _restore_after_run(self, run_id: str) -> None:

        if not run_id or self._restore_after_stop != run_id:
            return
        self._restore_after_stop = ""


        QTimer.singleShot(0, lambda: self._go_back_before(run_id))

    def _go_back_before(self, run_id: str) -> None:

        self._executor.record_pending_after()
        history = self._executor.history
        thread_id = self._thread_id or ""
        before = next((e for e in reversed(history.entries(thread_id))
                       if e.run_id == run_id and e.kind == KIND_BEFORE), None) if run_id else None
        current = history.current(thread_id)
        if before is None or (current is not None and current.id == before.id
                              and not self._manual_changes_since(current)):
            self.notice.emit("info", tr("Stopped. The run had not changed the project."))
            return
        if not before.available:
            self.notice.emit("warning", tr("Stopped. The version before this request is no longer kept, "
                                           "so nothing was undone."))
            return
        self._on_restore(before.id, False)

    def _manual_changes_since(self, current) -> bool:





        from .snapshot import unsaved_edit_layers

        if unsaved_edit_layers():
            return True
        if current is None or not current.available:
            return False
        try:
            return diff_changed(current.snapshot.diff())
        except Exception as exc:  # noqa: BLE001
            log(f"Checkpoint diff failed: {exc}")
            return False

    def _capture_edits(self, thread_id: str, current, entry) -> str:













        from .snapshot import RunSnapshot, unsaved_edit_layers

        for layer in unsaved_edit_layers():
            name = str(layer.name())
            try:
                saved = bool(layer.commitChanges())
                errors = [] if saved else [str(text) for text in layer.commitErrors()]
            except Exception as exc:  # noqa: BLE001
                saved, errors = False, [str(exc)]
            if not saved:
                log(f"Unsaved edits on {name} not saved before a restore: {'; '.join(errors[:3])}")
                return tr("Your unsaved edits on {layer} could not be saved, so nothing was restored. "
                          "Save or discard them in QGIS, then try again.").format(layer=name)
        history = self._executor.history
        run_id = current.run_id if current is not None else ""


        snapshot = RunSnapshot(f"{run_id or 'run'}-edits-{uuid.uuid4().hex[:12]}")
        try:
            captured = snapshot.capture()
            if captured:
                snapshot.keep_files_restored_by(entry.snapshot, history.file_versions(thread_id, entry))
        except Exception as exc:  # noqa: BLE001
            log(f"Edits snapshot failed: {exc}")
            captured = False
        if not captured:
            return tr("Your changes since this point could not be saved, so nothing was restored. "
                      "Save the project and try again.")
        history.add(thread_id, KIND_EDITS, run_id, current.run_index if current is not None else 0,
                    snapshot, fork=False)
        return ""

    def _requests_removed(self, entry) -> list:

        from ..ui.checkpoint_sheet import history_rows

        entries = self._executor.history.describe(self._thread_id or "")
        index = next((i for i, e in enumerate(entries) if e.get("id") == entry.id), None)
        if index is None:
            return []
        return [row for row in history_rows(entries)
                if row["type"] == "request" and row["live"] and row["anchor"] > index]

    def _missing_layers(self, entry, result: dict) -> list:


        row = next((r for r in self._executor.history.describe(self._thread_id or "")
                    if r.get("id") == entry.id), {})
        items = [item for item in row.get("not_backed_up") or [] if isinstance(item, dict) and item.get("name")]
        known = {str(item["name"]) for item in items}
        for name in result.get("memory_layers_incomplete") or []:
            name = str(name).split(" (")[0]
            if name and name not in known:
                known.add(name)
                items.append({"name": name, "reason": ""})
        return items

    def _on_layer_action(self, layer_id: str, action: str) -> None:
        telemetry.track(ev.REVIEW_OPENED, {"action": action})
        self.layer_action_requested.emit(layer_id, action)

    def _on_file_add(self, path: str) -> None:










        path, _, layername = str(path or "").partition("|layername=")
        if not path or not os.path.isfile(path):
            self.notice.emit("warning", tr("This file is no longer where the run wrote it."))
            return
        if not layername:
            kept = self._layer_of_file(path)
            if kept is not None:
                self.layer_action_requested.emit(str(kept.id()), "show")
                return
        args = {"source": path, "layer": layername} if layername else {"source": path}
        result = self._registry.execute("add_data", args)
        name = os.path.basename(path)
        if isinstance(result, dict) and not result.get("_error") and not result.get("layer_id") \
                and isinstance(result.get("layers"), list) and result["layers"]:
            first = ""
            for item in result["layers"]:
                sub = str((item or {}).get("name") or "") if isinstance(item, dict) else str(item or "")
                if not sub:
                    continue
                one = self._registry.execute("add_data", {"source": path, "layer": sub})
                if isinstance(one, dict) and one.get("layer_id") and not one.get("_error"):
                    first = first or str(one["layer_id"])
                else:
                    log_warning(f"Could not add {sub} of {name} to the map: {(one or {}).get('_error')}")
            if first:
                self.layer_action_requested.emit(first, "show")
                return
        if not isinstance(result, dict) or result.get("_error") or not result.get("layer_id"):
            log_warning(f"Could not add {name} to the map: {(result or {}).get('_error')}")
            self.notice.emit("warning", tr("{name} could not be loaded. Check the file and try again.").format(
                name=name))
            return
        self.layer_action_requested.emit(str(result["layer_id"]), "show")

    def _on_sign_out(self) -> None:
        telemetry.track(ev.ACCOUNT_SIGNED_OUT, {"source": "panel"})
        telemetry.flush()
        self._account.sign_out()

    def _on_welcome_dismissed(self) -> None:
        self._settings.onboarded = True
        telemetry.track(ev.WELCOME_DISMISSED)

    def _on_example_chosen(self, slug: str) -> None:
        self._example_pick = str(slug or "")
        telemetry.track(ev.EXAMPLE_CHOSEN, {"slug": self._example_pick})

    def _on_update_dismissed(self, version: str) -> None:
        from .plugin_release import put_off_update

        put_off_update(version)
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

    def _on_pro_pill_requested(self) -> None:





        self._account.open_plans(
            "plugin_header_pill",
            on_outcome=lambda link: telemetry.track(
                ev.SUBSCRIBE_LINK_CLICKED, {"source": "header_pill", "where": "header_pill", "checkout_link": link}))
        log("Upgrade opened from the header pill")

    def _on_checkout_requested(self, where: str) -> None:


        self._account.open_checkout(
            f"plugin_{where}",
            on_outcome=lambda link: telemetry.track(
                ev.SUBSCRIBE_LINK_CLICKED, {"source": where, "where": where, "checkout_link": link}))
        log(f"Checkout opened from the panel ({where})")

    def _on_plans_requested(self, where: str) -> None:



        self._account.open_plans(
            f"plugin_{where}",
            on_outcome=lambda link: telemetry.track(
                ev.SUBSCRIBE_LINK_CLICKED, {"source": where, "where": where, "checkout_link": link}))
        log(f"Upgrade opened from the panel ({where})")

    def _on_invoice_requested(self, where: str) -> None:

        from ..ui.external_links import open_external_url
        from ..ui.shared import get_invoice_url

        telemetry.track(ev.SUBSCRIBE_LINK_CLICKED, {"source": "invoice", "where": where, "checkout_link": "invoice"})
        open_external_url(get_invoice_url(), parent=self._panel)

    def _on_upsell_shown(self, where: str) -> None:
        telemetry.track(ev.PRO_UPSELL_VIEWED, {"where": where, "cta_source": where})

    def _on_limit_reached(self, is_subscriber: bool, limit: int) -> None:
        telemetry.track(ev.RUN_LIMIT_REACHED, {"is_free_tier": not is_subscriber, "quota_limit": limit})

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




                if self._run is not None:
                    if self._executor.open_cards():
                        self.notice.emit("info", tr("Permission mode changed. It applies from the next "
                                                    "action; the open card still needs your answer."))
                    else:
                        self.notice.emit("info", tr("Permission mode changed. It applies from the next action."))
        self._sync_permission_mode()

    def _sync_permission_mode(self) -> None:

        self._panel_call("set_permission_mode", self._approval())

    def _on_effort_changed(self, effort: str) -> None:


        if effort not in Effort.ALL:
            return
        previous = self._settings.effort
        self._settings.effort = effort
        if effort != previous:
            telemetry.track(ev.EFFORT_CHANGED, {"effort": effort})
        self._panel_call("set_effort", self._settings.effort)

    def _on_context_add(self, kind: str) -> None:

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

        count = len([p for p in (paths or []) if os.path.isfile(str(p))])
        if count:
            log(f"{count} file(s) attached to the composer")

    @staticmethod
    def _layer_of_file(path: str):

        from qgis.core import QgsProject

        from .snapshot import layer_file_path

        def key(value: str) -> str:
            try:
                return os.path.normcase(os.path.realpath(value))
            except (OSError, ValueError):
                return os.path.normcase(os.path.normpath(os.path.abspath(value)))

        wanted = key(path)
        for layer in QgsProject.instance().mapLayers().values():
            try:
                source = layer_file_path(layer)
            except Exception:  # noqa: BLE001  # nosec B112
                continue
            if source and key(source) == wanted:
                return layer
        return None

    def _load_file_attachments(self, attachments: list, reuse: bool = False) -> list:








        chips = []
        for att in attachments:
            if att.get("kind") != "file" or not att.get("path"):
                continue
            path = str(att["path"])
            if not os.path.isfile(path):
                att["error"] = "file not found"
                continue
            kept = self._layer_of_file(path) if reuse else None
            if kept is not None:
                att["layer_id"] = str(kept.id())
                att["layer_name"] = str(kept.name() or att.get("name") or "")
                chips.append({"kind": "layer", "label": att["layer_name"], "value": att["layer_id"]})
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

    from urllib.parse import urlsplit

    try:
        return (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def _project_path() -> str:

    try:
        from qgis.core import QgsProject
        return QgsProject.instance().fileName() or ""
    except Exception:  # noqa: BLE001
        return ""
