# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat panel's surface: wiring, the composer, drops and window events."""




from __future__ import annotations

import contextlib

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtWidgets import QApplication, QWidget

from ..core.layer_mime import mime_has_layers
from ..core.telemetry_errors import slot_guard
from .bubbles import UserBubble
from .chat_panel_shared import CompactionDivider, _turn_divider
from .dock.about import show_contact_dialog, show_shortcuts_dialog
from .external_links import open_external_url
from .shared import (
    format_reset_date,
    get_contact_call_url,
    get_dashboard_url,
    get_free_runs,
    get_support_email,
    get_tutorial_url,
)














_CENTER_ABOVE = 48
_CENTER_BELOW = 52

_MAX_HEIGHT = 16777215


class _ChatPanelLayout:





    def _wire(self) -> None:
        h = self.header
        h.thread_selected.connect(self.thread_selected.emit)
        h.new_thread_requested.connect(self.new_thread_requested.emit)
        h.account_clicked.connect(self.open_settings_requested.emit)
        h.settings_clicked.connect(self.open_settings_requested.emit)
        h.pro_pill_clicked.connect(self._on_pro_pill)

        self.update_banner.update_clicked.connect(self.update_clicked.emit)
        self.update_gate.update_clicked.connect(self.update_clicked.emit)
        self.update_banner.dismissed.connect(self.update_dismissed.emit)
        a = self.activation
        a.sign_in_requested.connect(self.sign_in_requested.emit)
        a.pairing_reopen_requested.connect(self.pairing_reopen_requested.emit)
        a.pairing_cancel_requested.connect(self.pairing_cancel_requested.emit)
        a.account_clicked.connect(self.open_settings_requested.emit)

        self.empty_state.suggestion_clicked.connect(self.suggestion_clicked.emit)
        self.empty_state.examples_requested.connect(self.open_examples)
        self.empty_state.tutorial_requested.connect(lambda: self._on_help("tutorial"))
        h.checkpoints_requested.connect(self.history_requested.emit)
        h.restore_requested.connect(lambda cid: self.restore_requested.emit(cid, False))
        h.discard_all_requested.connect(lambda: self.discard_all_requested.emit(False))
        self._history: list = []
        self._changed_layers: list = []
        self._run_changes: dict | None = None
        self._changed_count = 0
        self._wire_history_keys()
        self.quota_card.upgrade_requested.connect(self._on_upgrade)
        c = self.composer
        c.send_clicked.connect(self._on_send)
        c.stop_clicked.connect(self._on_stop)
        c.files_dropped.connect(lambda paths: self.files_dropped.emit(list(paths)))
        c.attachments_changed.connect(self._on_attachments_changed)
        c.context_add_requested.connect(self.context_add_requested.emit)
        c.chip_removed.connect(self.chip_removed.emit)

        c.layer_card_clicked.connect(lambda layer_id: self.layer_action_requested.emit(layer_id, "show"))

        c.permission_mode_changed.connect(lambda approval: self.mode_changed.emit("", approval))

        c.effort_changed.connect(self.effort_changed.emit)
        c.upgrade_requested.connect(self._on_upgrade)
        c.reconnect_requested.connect(self.reconnect_requested.emit)
        c.example_chosen.connect(self.example_chosen.emit)

    def open_examples(self) -> None:
        """The Examples library window."""
        self.composer.open_examples()

    @slot_guard("panel_send")
    def _on_send(self) -> None:
        text = self.composer.text().strip()
        attachments = self.composer.attachments()
        if not text and not attachments:
            return
        if not text:



            self.composer.show_warning(self.tr("Add a few words: what should the agent do with it?"))
            return







        if not self._require_privacy_notice(self._on_send):
            return


        chips = self.composer.peek_chips()






        before = self._last_run
        try:
            self.send_requested.emit(text, "", "", chips, attachments)
        except Exception:  # noqa: BLE001 - the box must never empty on a failed send
            self.composer.show_warning(
                self.tr("The message could not be sent. It is still here; try again."), sticky=True)
            raise
        if self._last_run == before:
            return
        self.composer.take_chips()
        self.composer.clear()
        self.composer.clear_attachments()



    def _require_privacy_notice(self, on_accept) -> bool:
        """Gate the first thing that leaves the machine behind the notice."""





        from ..core.privacy_notice import has_accepted_privacy_notice

        if has_accepted_privacy_notice():
            return True
        self._privacy_on_accept = on_accept
        self._show_privacy_notice()
        return False

    def _show_privacy_notice(self) -> None:
        """The notice, opened at the moment the user asks to send something."""





        from .privacy_notice_dialog import PrivacyNoticeDialog

        if self._privacy_dialog is not None:
            self._privacy_dialog.raise_()
            return
        dialog = PrivacyNoticeDialog(self)
        dialog.accepted.connect(self._on_privacy_accepted)
        dialog.rejected.connect(self._on_privacy_declined)
        dialog.finished.connect(self._on_privacy_closed)
        self._privacy_dialog = dialog
        dialog.open()

    def _on_privacy_accepted(self) -> None:
        from ..core import telemetry
        from ..core.privacy_notice import save_privacy_notice_accepted

        save_privacy_notice_accepted()

        with contextlib.suppress(Exception):
            telemetry.flush()
        queued = self._privacy_on_accept
        self._privacy_on_accept = None
        if queued is not None:
            queued()

    def _on_privacy_declined(self) -> None:
        """Not now: nothing is sent, the typed message stays in the box, and the panel does not move."""

        self._privacy_on_accept = None
        self.composer.show_warning(
            self.tr("Nothing was sent. Send again to read the notice."))

    def _on_privacy_closed(self, _result: int) -> None:
        dialog = self._privacy_dialog
        self._privacy_dialog = None
        if dialog is not None:
            dialog.deleteLater()

    @slot_guard("panel_stop")
    def _on_stop(self) -> None:
        self.stop_requested.emit(self._current_run or self._last_run or "")

    def _on_attachments_changed(self) -> None:
        count = len(self.composer.attachments())
        if count > self._attachment_count:
            self.attachment_added.emit(count - self._attachment_count)
        self._attachment_count = count

    def _on_dashboard(self) -> None:
        self.dashboard_requested.emit()
        open_external_url(get_dashboard_url(), parent=self)

    def _on_upgrade(self) -> None:
        """Every Upgrade in the panel goes to the site, nowhere in between."""








        _used, _limit, _period_end, is_subscriber = self._usage
        if is_subscriber:
            self.dashboard_requested.emit()
            open_external_url(get_dashboard_url(), parent=self)
            return
        self.upgrade_requested.emit()

    def _on_pro_pill(self) -> None:
        """The header's "Get Pro": shown to a free account only, so it always means the offer; the controller opens it signed in."""

        if self._paid_plan:
            return
        self.pro_pill_requested.emit()

    def _set_plan_paid(self, paid: bool) -> None:
        self._plan_known = True
        self._paid_plan = bool(paid)
        self._sync_pro_pill()

    def _sync_pro_pill(self) -> None:
        """The pill: a signed-in free account only, never before the plan is known."""
        self.header.set_pro_pill_visible(self._signed_in and self._plan_known and not self._paid_plan)

    def open_help(self, kind: str) -> None:
        """The Help page of the settings dialog calls this: one entry point for the tutorial, the shortcuts, contact and the problem report."""

        self._on_help(kind)

    @slot_guard("panel_help")
    def _on_help(self, kind: str) -> None:
        self.help_requested.emit(kind)
        if kind == "tutorial":
            open_external_url(get_tutorial_url(), parent=self)
        elif kind == "shortcuts":
            show_shortcuts_dialog(self)
        elif kind == "contact":
            show_contact_dialog(self, get_support_email(), get_contact_call_url())
        elif kind == "report":
            from .error_report_dialog import show_error_report
            show_error_report(self, self._last_error, self._current_run or self._last_run)



    def dragEnterEvent(self, event):  # noqa: N802 - Qt override
        mime = event.mimeData()
        if self._signed_in and self.composer.accepts_mime(mime):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self._drop_overlay.show_over(self, "layers" if mime_has_layers(mime) else "paperclip")
        else:
            event.ignore()

    def dragMoveEvent(self, event):  # noqa: N802 - Qt override
        mime = event.mimeData()
        if self._signed_in and self.composer.accepts_mime(mime):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):  # noqa: N802 - Qt override
        self._drop_overlay.hide()
        event.accept()

    def dropEvent(self, event):  # noqa: N802 - Qt override
        self._drop_overlay.hide()
        if self.composer.take_drop(event.mimeData()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return





        if not self.composer.hint_text():
            self.composer.show_warning(self.tr("Nothing could be added from that drop."))
        event.ignore()

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._drop_overlay.sync_geometry(self)









    def _centre_host(self) -> QWidget:
        """The row the question sits in: the thread column once the sidebar has moved the list and the empty state into it, the empty state before."""

        host = getattr(self, "_thread_host", None)
        return host if host is not None else self.empty_state

    def _centre_composer(self, empty: bool, animate: bool = True) -> None:
        """Hold the box in the middle of an empty chat, or let it fall."""













        col = self._col
        spacer = self._center_spacer
        spacer_index = col.indexOf(spacer)
        host_index = col.indexOf(self._centre_host())
        if spacer_index < 0:
            return
        self._center_anim.stop()
        if empty:
            spacer.setMinimumHeight(0)
            spacer.setMaximumHeight(_MAX_HEIGHT)
            if host_index >= 0:
                col.setStretch(host_index, _CENTER_ABOVE)
            col.setStretch(spacer_index, _CENTER_BELOW)
            return
        if host_index >= 0:
            col.setStretch(host_index, 1)
        col.setStretch(spacer_index, 0)
        start = spacer.height()
        if start <= 0 or not animate or self._reduced_motion():
            spacer.setFixedHeight(0)
            return
        spacer.setFixedHeight(start)
        self._center_anim.setStartValue(start)
        self._center_anim.setEndValue(0)
        self._center_anim.start()

    def _reduced_motion(self) -> bool:
        """Qt has no reduced-motion flag; a hidden panel animates nothing."""
        return not self.isVisible()

    def _apply_quota(self) -> None:
        """The balance decides what the composer may do (AI Segmentation's gate): a spent grant or month blocks it and says why, a free account."""


        used, limit, period_end, is_subscriber = self._usage
        if not self._signed_in or limit <= 0:
            self.quota_card.clear()
            self._quota_host.hide()
            self.composer.set_blocked(False)
            self._low_balance_visible = False
            return
        left = limit - used
        if left <= 0:
            self.quota_card.show_exhausted(
                is_subscriber, limit, format_reset_date(period_end) if is_subscriber else "")
            self.composer.set_blocked(True, self.tr("More runs next month") if is_subscriber
                                      else self.tr("Keep working with Pro"))
            self._quota_host.show()
            self._low_balance_visible = False
        else:
            self.quota_card.clear()
            self._quota_host.hide()
            self.composer.set_blocked(False)



            low = not is_subscriber and left <= max(1, int(limit * 0.2))
            if low and not self._low_balance_visible:
                self.low_balance_shown.emit(left)
            self._low_balance_visible = low

    def focus_composer(self) -> None:
        """Put the caret in the box: opening the panel is asking to type."""
        if self._signed_in and not self.composer.is_blocked():
            self.composer.focus_input()

    def showEvent(self, event):  # noqa: N802 - Qt override
        super().showEvent(event)

        QTimer.singleShot(0, self.focus_composer)





    def _add(self, widget: QWidget, animate: bool = True) -> None:
        """Append to the thread, keeping the status line last."""
        self.message_list.add_widget(widget, before=self._status, animate=animate)
        if self._signed_in:
            self.message_list.setVisible(True)
            self.empty_state.setVisible(False)

    def _add_user(self, bubble: UserBubble) -> None:
        """A user turn, with a hairline above it after the first one."""

        if self.message_list.last_widget(ignore=self._status) is not None:
            self._add(_turn_divider())




        bubble.layer_clicked.connect(self._on_bubble_layer)
        self._add(bubble)

    def _on_bubble_layer(self, layer_id: str) -> None:
        if layer_id:
            self.layer_action_requested.emit(layer_id, "show")





    def set_connection_state(self, state: str, detail: str = "") -> None:
        """What the socket is doing, and what of it reaches the reader: as little as possible."""









        state = str(state or "").strip().lower()
        if state not in {"connecting", "online", "offline", "signed_out", "error"}:
            state = "error"
        detail = str(detail or "")[:500]
        self._connection = state
        if state == "signed_out":
            self._signed_in = False
            self.header.clear_account()

            self._plan_known = False
            self._sync_pro_pill()


            self.activation.show_signed_out(detail or "")
            self._show_thread_surface()
            return
        if state == "online":
            self._signed_in = True
            self._sync_pro_pill()
            self.activation.pairing_finished()
            self.activation.clear_message()


            self.composer.set_offline(False, "online")
            self._show_thread_surface()
            return
        if not self._signed_in:


            if state == "error" and detail:
                self.activation.set_message(detail, "error")
            return




        self.composer.set_offline(True, state)

    def set_model_label(self, label: str) -> None:
        """Kept for the controller; nothing is shown (owner's decision)."""
        self._model_label = label or ""

    def set_usage(self, runs_used: int, runs_limit: int, period_end_iso: str = "",
                  is_subscriber=None) -> None:
        """The balance."""

        try:
            used = max(0, int(runs_used or 0))
        except (TypeError, ValueError, OverflowError):
            used = 0
        try:
            limit = max(0, int(runs_limit or 0))
        except (TypeError, ValueError, OverflowError):
            limit = 0



        stated = is_subscriber is not None
        if not stated:
            is_subscriber = self._paid_plan if self._plan_known else limit > get_free_runs()
        self._usage = (used, limit, str(period_end_iso or "")[:80], bool(is_subscriber))
        self._apply_quota()
        sidebar = getattr(self, "sidebar", None)
        if sidebar is not None:
            sidebar.set_paid(bool(is_subscriber))
        if stated:
            self._set_plan_paid(bool(is_subscriber))

    def set_display_options(self, explain_runs: bool, show_tool_details: bool) -> None:
        """Settings > General: the Done card after a run, the activity block."""
        self._explain_runs = bool(explain_runs)
        self._show_tool_details = bool(show_tool_details)

    def _composer_is_being_typed_in(self) -> bool:
        """Whether the keyboard is in the composer with words already in it."""





        composer = getattr(self, "composer", None)
        field = getattr(composer, "_input", None)
        if field is None:
            return False
        focus = QApplication.focusWidget()
        if focus is None or not (focus is field or field.isAncestorOf(focus)):
            return False
        try:
            return bool(field.toPlainText().strip())
        except Exception:  # noqa: BLE001 - a field that cannot say is treated as empty
            return False

    def _mark_compaction(self, usage, animate: bool = True) -> None:
        """The compaction divider, once per thread, after the first run whose history the server compacted: ``usage.compaction_folds`` above 0 means."""




        try:
            folds = int((usage or {}).get("compaction_folds") or 0)
        except (AttributeError, TypeError, ValueError):
            return
        if folds <= 0 or self._compaction_marked:
            return
        self._compaction_marked = True
        divider = CompactionDivider(
            self.tr("Conversation compacted"),
            self.tr("The agent keeps a summary of the earlier exchanges. Your messages stay visible."))
        divider.show()
        self._add(divider, animate=animate)





    def set_mentions(self, items) -> None:
        """Fill the composer's ``@`` popup: ``[{kind, label, value}]``."""
        self.composer.set_mentions(list(items or []))

    def set_mention_provider(self, provider) -> None:
        """``provider() -> [{kind, label, value}]``, asked on every ``@``."""
        self.composer.set_mention_provider(provider)

    def set_permission_mode(self, approval: str) -> None:
        """Show the settings' approval on the composer chip."""
        self.composer.set_permission_mode(approval)

    def set_effort(self, effort: str) -> None:
        """Show the settings' effort level on the composer chip."""
        self.composer.set_effort(effort)

    def set_paid_plan(self, paid: bool) -> None:
        """Whether the account may pick the paid efforts and Autopilot; a free one sees Upgrade."""
        self.composer.set_paid_plan(paid)
        self._set_plan_paid(paid)

    def add_context_chip(self, chip: dict) -> None:
        """A layer card above the composer, for the next message only."""
        self.composer.add_chip(chip)

    def set_pairing_state(self, waiting: bool, code: str = "", url: str = "") -> None:
        """Browser sign-in in progress (with the code, when there is one)."""
        if waiting:
            self.activation.show_pairing_waiting(code, url)
        else:
            self.activation.show_signed_out()

    def set_pairing_status(self, text: str) -> None:
        """The line beside the sign-in spinner."""
        self.activation.set_pairing_status(text)

    def set_pairing_note(self, text: str, kind: str = "warning") -> None:
        """A line the waiting user must act on, inside the sign-in card: the message list is hidden while signed out, so an error card goes nowhere."""

        self.activation.set_pairing_note(text, kind)

    def set_account(self, email: str, avatar_url: str = "") -> None:
        self.header.set_account(email, avatar_url)
        self.activation.show_activated(email, avatar_url)

    def apply_server_config(self, payload: dict) -> None:
        """Apply future served values without rebuilding the active thread."""
        self._apply_quota()

    def current_run_id(self) -> str:
        return self._current_run or ""
