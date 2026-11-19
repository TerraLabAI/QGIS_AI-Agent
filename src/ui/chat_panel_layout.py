# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





from __future__ import annotations

import contextlib
import time

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtWidgets import QApplication, QWidget

from ..core import improve, quota_view, telemetry
from ..core import telemetry_events as ev
from ..core.layer_mime import mime_has_layers
from ..core.numbers import as_int
from ..core.privacy_notice import current_notice_version, has_accepted_privacy_notice, save_privacy_notice_accepted
from ..core.served_state import (
    LEGACY_FREE_RUNS_MAX,
    build_utm_url,
    get_contact_call_url,
    get_dashboard_url,
    get_free_runs,
    get_support_email,
    set_pro_offer,
)
from ..core.telemetry_errors import slot_guard
from . import card_base
from .bubbles import UserBubble
from .chat_panel_shared import CompactionDivider, _turn_divider
from .dock.about import show_contact_dialog
from .external_links import open_external_url
from .feedback_dialog import show_feedback
from .privacy_notice_dialog import PrivacyNoticeDialog
from .quota_card import trial_promise
from .selection_line import selection_key
from .shared import format_reset_date









_CENTER_ABOVE = 48
_CENTER_BELOW = 52
_MAX_HEIGHT = 16777215


class _ChatPanelLayout:

    def _wire(self) -> None:
        h = self.header
        h.thread_selected.connect(self.thread_selected.emit)
        h.thread_delete_requested.connect(self.thread_delete_requested.emit)
        h.new_thread_requested.connect(self.new_thread_requested.emit)
        h.settings_clicked.connect(self.open_settings_requested.emit)
        h.feedback_clicked.connect(lambda: self.open_feedback("header"))
        h.pro_pill_clicked.connect(self._on_pro_pill)
        h.site_requested.connect(lambda: open_external_url(build_utm_url("/ai-agent", "header_wordmark"), self))
        h.pro_pill_shown.connect(lambda: self._note_upsell("header_pill"))

        self.update_banner.update_clicked.connect(self._update_now)
        self.update_gate.update_clicked.connect(self._update_now)
        self.update_banner.dismissed.connect(self.update_dismissed.emit)
        self.update_banner.download_clicked.connect(self.update_download_clicked.emit)
        a = self.activation
        a.sign_in_requested.connect(self.sign_in_requested.emit)
        a.pairing_reopen_requested.connect(self.pairing_reopen_requested.emit)
        a.pairing_cancel_requested.connect(self.pairing_cancel_requested.emit)
        a.pairing_code_submitted.connect(self.pairing_code_submitted.emit)

        self.empty_state.examples_requested.connect(self.open_examples)
        self.empty_state.sources_requested.connect(
            lambda: self.composer.open_data_sources("empty_state_sources"))
        self.empty_state.tutorial_requested.connect(lambda: self._on_help("tutorial"))
        h.checkpoints_requested.connect(self.history_requested.emit)
        h.restore_requested.connect(lambda cid: self.restore_requested.emit(cid, False))
        h.discard_all_requested.connect(lambda: self.discard_all_requested.emit(False))
        h.stop_and_restore_requested.connect(self._on_stop_and_restore)
        self._history: list = []
        self._changed_layers: list = []
        self._run_changes: dict | None = None
        self._changed_count = 0
        self._wire_history_keys()

        self.quota_card.plans_requested.connect(self.plans_requested.emit)
        self.quota_card.contact_requested.connect(self.pro_contact_requested.emit)
        self.quota_card.return_closed.connect(self._close_checkout_return)
        self.quota_card.low_closed.connect(self._close_low_card)
        self.quota_card.welcome_closed.connect(self._close_pro_welcome)
        self.quota_card.start_now_requested.connect(
            lambda: self.start_now_requested.emit(getattr(self, "_start_now_url", "")))
        self.quota_card.pay_requested.connect(lambda: self.pay_requested.emit(self._payfail_surface()))
        self.runs_line.requested.connect(self.plans_requested.emit)
        c = self.composer
        c.send_clicked.connect(self._on_send)
        c.stop_clicked.connect(self._on_stop)
        c.files_dropped.connect(lambda paths: self.files_dropped.emit(list(paths)))
        c.attachments_changed.connect(self._on_attachments_changed)
        c.chip_removed.connect(self.chip_removed.emit)
        c.layer_card_clicked.connect(lambda layer_id: self.layer_action_requested.emit(layer_id, "show"))
        c.permission_mode_changed.connect(lambda approval: self.mode_changed.emit("", approval))
        c.folder_removed.connect(self.folder_removed.emit)
        c.effort_changed.connect(self.effort_changed.emit)
        c.upgrade_requested.connect(self._on_upgrade)
        c.upsell_shown.connect(self._note_upsell)
        c.reconnect_requested.connect(self.reconnect_requested.emit)
        c.example_chosen.connect(self.example_chosen.emit)

    def open_examples(self, connector: str = "") -> None:

        self.composer.open_examples(connector)

    @slot_guard("panel_send")
    def _on_send(self) -> None:
        text = self.composer.text().strip()
        attachments = self.composer.attachments()
        if not text and not attachments:
            return
        if not text:

            self.composer.show_warning(self.tr("Add a few words: what should the AI agent do with it?"))
            return







        if not self._require_privacy_notice(self._on_send):
            return


        if text and self._task_cards and self._answer_open_question(text):
            return
        chips = self.composer.peek_chips()
        selection = self.composer.selection_snapshot()






        before = self._last_run
        try:
            self.send_requested.emit(text, "", "", chips, attachments, selection)
        except Exception:  # noqa: BLE001
            self.composer.show_warning(
                self.tr("The message could not be sent. It is still here; try again."), sticky=True)
            raise
        if self._last_run == before:
            return

        self._run_selection = (self._last_run, selection_key(selection))
        self._empty_composer()

    def _empty_composer(self) -> None:
        self.composer.take_chips()
        self.composer.selection_sent()
        self.composer.clear()
        self.composer.clear_attachments()

    def composer_sent(self, text: str) -> None:


        if self.composer.text().strip() == str(text or "").strip():
            self._run_selection = (self._last_run, selection_key(self.composer.selection_snapshot()))
            self._empty_composer()

    def _require_privacy_notice(self, on_accept) -> bool:







        if has_accepted_privacy_notice():
            return True
        self._privacy_on_accept = on_accept
        self._show_privacy_notice()
        return False

    def _show_privacy_notice(self) -> None:







        if self._privacy_dialog is not None:
            self._privacy_dialog.raise_()
            return
        dialog = PrivacyNoticeDialog(self)
        dialog.chosen.connect(self._on_privacy_chosen)
        dialog.accepted.connect(self._on_privacy_accepted)
        dialog.rejected.connect(self._on_privacy_declined)
        dialog.finished.connect(self._on_privacy_closed)
        self._privacy_dialog = dialog
        self._privacy_shown_at = time.monotonic()
        dialog.open()

    def _on_privacy_chosen(self, help_improve: bool) -> None:




        if bool(help_improve) != telemetry.is_telemetry_enabled():
            telemetry.set_telemetry_enabled(bool(help_improve))
        if bool(help_improve) != improve.is_improve_enabled():
            improve.set_improve_enabled(bool(help_improve))

    def _on_privacy_accepted(self) -> None:
        save_privacy_notice_accepted()
        shown_at = getattr(self, "_privacy_shown_at", None)
        telemetry.track(ev.PRIVACY_NOTICE_ACCEPTED, {
            "notice_version": current_notice_version(),
            "duration_ms": int((time.monotonic() - shown_at) * 1000) if shown_at else 0})

        with contextlib.suppress(Exception):
            telemetry.flush()
        queued = self._privacy_on_accept
        self._privacy_on_accept = None
        if queued is not None:
            queued()

    def _on_privacy_declined(self) -> None:


        self._privacy_on_accept = None
        self.composer.show_warning(
            self.tr("Nothing was sent. Send again to read the notice."))

    def _on_privacy_closed(self, _result: int) -> None:
        dialog = self._privacy_dialog
        self._privacy_dialog = None
        if dialog is not None:
            dialog.deleteLater()

    def _on_stop(self) -> None:
        self.stop_requested.emit(self._current_run or self._last_run or "")

    def _on_attachments_changed(self) -> None:
        count = len(self.composer.attachments())
        if count > self._attachment_count:
            self.attachment_added.emit(count - self._attachment_count)
        self._attachment_count = count

    def _on_upgrade(self, where: str) -> None:








        _used, _limit, _period_end, is_subscriber = self._usage
        if is_subscriber:
            self.dashboard_requested.emit()
            open_external_url(get_dashboard_url(), parent=self)
            return
        self.plans_requested.emit(where)

    def _on_pro_pill(self) -> None:


        if self._paid_plan:
            if self._keep_pro:
                self.keep_pro_requested.emit()
            return
        self.pro_pill_requested.emit()

    def _set_plan_paid(self, paid: bool) -> None:
        self._plan_known = True
        self._paid_plan = bool(paid)
        if self._paid_plan and getattr(self, "_checkout_return", False):
            self._checkout_return = False
            self._apply_quota()
        self._sync_pro_pill()

    def _sync_pro_pill(self) -> None:


        offer = self.trial_offer()
        keep = self._keep_pro if self._paid_plan and self._signed_in and self._plan_known else None
        self.header.set_pro_pill_trial(trial_promise(*offer) if offer else "", keep)
        if keep and not self._payfail and getattr(self, "_quota_view", None) != quota_view.EXHAUSTED:
            self.header.set_pro_pill_visible(True)
            return


        view = getattr(self, "_quota_view", None)
        used, limit = self._usage[0], self._usage[1]
        card_offers = view in (quota_view.LOW_CARD, quota_view.EXHAUSTED, quota_view.CHECKOUT_RETURN) or (
            view == quota_view.RUNS_LINE and quota_view.is_low(quota_view.runs_left(used, limit)))
        self.header.set_pro_pill_visible(self._signed_in and self._plan_known and not self._paid_plan
                                         and not self._payfail and not card_offers)

    def open_help(self, kind: str) -> None:



        self._on_help(kind)

    @slot_guard("panel_help")
    def _on_help(self, kind: str) -> None:
        self.help_requested.emit(kind)
        if kind == "tutorial":
            self.open_settings_page_requested.emit("learn")
        elif kind == "contact":
            show_contact_dialog(self, get_support_email(), get_contact_call_url())
        elif kind == "report":
            self._show_feedback("settings")

    def open_feedback(self, entry: str) -> None:

        self.help_requested.emit("report")
        self._show_feedback(entry)

    def _show_feedback(self, entry: str) -> None:
        show_feedback(self, self._last_error, self._current_run or self._last_run, entry=entry)

    def feedback_sent(self) -> None:

        host = self.message_list if self.message_list.isVisible() else self
        toast = self.toast if host is self.message_list else getattr(self, "_home_toast", None)
        if toast is None:
            from .toast import Toast
            toast = self._home_toast = Toast(self)
        toast.show_message(self.tr("Thanks, your feedback was sent."))

    def dragEnterEvent(self, event):  # noqa: N802
        mime = event.mimeData()
        if self._signed_in and self.composer.accepts_mime(mime):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self._drop_overlay.show_over(self, "layers" if mime_has_layers(mime) else "photo_file")
            self.mascot_stage.fire("drop.enter")
        else:
            event.ignore()

    def dragMoveEvent(self, event):  # noqa: N802
        mime = event.mimeData()
        if self._signed_in and self.composer.accepts_mime(mime):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):  # noqa: N802
        self._drop_overlay.hide()
        self.mascot_stage.mascot.hover(None)
        event.accept()

    def dropEvent(self, event):  # noqa: N802
        self._drop_overlay.hide()
        self.mascot_stage.mascot.hover(None)
        if self.composer.take_drop(event.mimeData()):
            self.mascot_stage.fire("drop.done")
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return





        if not self.composer.hint_text():
            self.composer.show_warning(self.tr("Nothing could be added from that drop."))
        event.ignore()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._drop_overlay.sync_geometry(self)





    def _centre_host(self) -> QWidget:


        host = getattr(self, "_thread_host", None)
        return host if host is not None else self.empty_state

    def _centre_composer(self, empty: bool, animate: bool = True) -> None:














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
        if start <= 0 or not animate or card_base.reduced_motion(self):
            spacer.setFixedHeight(0)
            return
        spacer.setFixedHeight(start)
        self._center_anim.setStartValue(start)
        self._center_anim.setEndValue(0)
        self._center_anim.start()

    def _reduced_motion(self) -> bool:



        return not self.isVisible()

    def _apply_quota(self) -> None:
        self._draw_quota()


        self._composer_host.setVisible(self._composer_shown())

    def _composer_shown(self) -> bool:
        return (self._signed_in and not self.update_gate.isVisibleTo(self)
                and not self.composer.is_blocked())

    def _draw_quota(self) -> None:




        used, limit, period_end, is_subscriber = self._usage
        left = quota_view.runs_left(used, limit)
        if not quota_view.is_low(left) or is_subscriber:


            self._low_card_closed = False
        view = self._quota_view = quota_view.choose(
            signed_in=self._signed_in, used=used, limit=limit, is_subscriber=bool(is_subscriber),
            payfail=bool(self._payfail), pro_welcome=self._pro_welcome is not None,
            checkout_return=self._checkout_return, low_closed=self._low_card_closed,
            running=getattr(self, "_current_run", None) is not None)
        self._sync_pro_pill()
        if view == quota_view.HIDDEN:
            self.quota_card.clear()
            self._quota_host.hide()
            self._runs_host.hide()
            self.composer.set_blocked(False)
            self._low_balance_visible = False
            return
        if is_subscriber:
            self._checkout_return = False
        if view == quota_view.PAYMENT_FAILED:


            self._runs_host.hide()
            declined = bool(self._payfail.get("declined"))
            self.quota_card.show_payment_failed(self._payfail["amount"], declined)
            self._quota_host.show()
            blocked = self.tr("Try another card") if declined else self.tr("Pay to get Pro back")
            self.composer.set_blocked(left <= 0, blocked if left <= 0 else "")
            self._low_balance_visible = False
            if left <= 0 and not self._limit_said:
                self._limit_said = True
                self.limit_reached.emit(False, int(limit))
            elif left > 0:
                self._limit_said = False
            self._note_payfail()
            return
        if view == quota_view.PRO_WELCOME:
            self._runs_host.hide()
            self.quota_card.show_pro_welcome(*self._pro_welcome)
            self._quota_host.show()
            self.composer.set_blocked(False)
            self._low_balance_visible = False
            return
        if view == quota_view.CHECKOUT_RETURN:


            self._runs_host.hide()
            self.quota_card.show_checkout_return(self.trial_offer())
            self._quota_host.show()
            self.composer.set_blocked(left <= 0, self._free_wall_text() if left <= 0 else "")
            self._low_balance_visible = False
            return
        if view == quota_view.EXHAUSTED:
            self._runs_host.hide()
            self.quota_card.show_exhausted(is_subscriber, limit, format_reset_date(period_end),
                                           None if is_subscriber else self.trial_offer(),
                                           getattr(self, "_account_cap", None),
                                           bool(is_subscriber and getattr(self, "_start_now_url", "")))
            self.composer.set_blocked(True, self.tr("More tasks next month") if is_subscriber
                                      else self._free_wall_text())
            self._quota_host.show()
            self._low_balance_visible = False
            if not self._limit_said:
                self._limit_said = True
                self.limit_reached.emit(bool(is_subscriber), int(limit))
            if not is_subscriber:
                self._note_upsell("exhausted_card")
            return
        self._limit_said = False
        self.composer.set_blocked(False)
        if view == quota_view.QUIET:
            self.quota_card.clear()
            self._quota_host.hide()
            self._runs_host.hide()
            self._low_balance_visible = False
            return
        low = quota_view.is_low(left)
        if view == quota_view.LOW_CARD:



            self._runs_host.hide()
            self.quota_card.show_low(left, format_reset_date(period_end), self.trial_offer())
            self._quota_host.show()
            self._note_upsell("low_card")
        else:
            self.quota_card.clear()
            self._quota_host.hide()
            self.runs_line.set_balance(left, limit, self.trial_offer())

            self._runs_host.setVisible(self.runs_line.where == "warning")
            if self.runs_line.where == "warning":
                self._note_upsell("warning")
        if low and not self._low_balance_visible:
            self.low_balance_shown.emit(left)
        self._low_balance_visible = low

    def _free_wall_text(self) -> str:

        if getattr(self, "_account_cap", None) is not None:
            return self.tr("No free tasks left on this computer")
        return self.tr("No free tasks left this month")

    def _payfail_surface(self) -> str:
        used, limit, _end, _paid = self._usage
        return "wall" if limit > 0 and used >= limit else "banner"

    def _note_payfail(self) -> None:

        key = "payfail:" + self._payfail_surface()
        if key in self._upsell_seen or not self.isVisible():
            return
        self._upsell_seen.add(key)
        self.payment_card_shown.emit(self._payfail_surface(), bool(self._payfail["amount"]),
                                     bool(self._payfail.get("declined")))

    def set_payment_failed(self, info) -> None:

        if info == self._payfail:
            return
        self._payfail = info
        self._apply_quota()
        self._sync_pro_pill()

    def _note_upsell(self, where: str) -> None:

        if where in self._upsell_seen or not self.isVisible():
            return
        self._upsell_seen.add(where)
        self.upsell_shown.emit(where)

    def show_checkout_return(self) -> None:

        used, limit, _end, is_subscriber = self._usage
        if not self._signed_in or is_subscriber or self._paid_plan or limit <= 0:
            return
        self._checkout_return = True
        self._return_view_pending = True
        self._apply_quota()
        self._note_return_view()

    def _note_return_view(self) -> None:

        if (getattr(self, "_return_view_pending", False) and self._checkout_return
                and self.isVisible() and self.quota_card.state == "checkout_return"):
            self._return_view_pending = False
            self.upsell_shown.emit("checkout_return")

    def show_pro_welcome(self, limit=None, trial_ends_on=None) -> None:


        limit = as_int(limit if limit is not None else self._usage[1], 0)
        self._checkout_return = False
        self._return_view_pending = False
        self._pro_welcome = (limit, format_reset_date(str(trial_ends_on or "")))
        if not self._usage[3]:

            used, old_limit, end, _paid = self._usage
            self._usage = (used, limit or old_limit, end, True)
        self._apply_quota()
        self.mascot_stage.fire("plan.started")

    def _close_low_card(self) -> None:
        self._low_card_closed = True
        self._apply_quota()

    def _close_pro_welcome(self) -> None:
        self._pro_welcome = None
        self._apply_quota()

    def _close_checkout_return(self) -> None:
        self._checkout_return = False
        self._return_view_pending = False
        self._apply_quota()

    def focus_composer(self) -> None:

        if self._signed_in and not self.composer.is_blocked():
            self.composer.focus_input()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._note_return_view()
        if self._payfail:
            self._note_payfail()

        QTimer.singleShot(0, self.focus_composer)





    def _add(self, widget: QWidget, animate: bool = True) -> None:

        self.message_list.add_widget(widget, before=self._status, animate=animate)
        if self._signed_in:
            self.message_list.setVisible(True)
            self.empty_state.setVisible(False)
            self.header.set_history_available(False)
            sidebar = getattr(self, "sidebar", None)
            if sidebar is not None:
                sidebar.sync_visible(False)

    def _add_user(self, bubble: UserBubble) -> None:


        if self.message_list.last_widget(ignore=self._status) is not None:
            self._add(_turn_divider())




        bubble.layer_clicked.connect(self._on_bubble_layer)
        bubble.source_clicked.connect(self._on_bubble_source)
        self._add(bubble)

    def _on_bubble_source(self, connector: str) -> None:



        if not connector:
            return
        self._pending_source = str(connector)
        QTimer.singleShot(0, self._open_pending_source)

    def _open_pending_source(self) -> None:
        connector, self._pending_source = getattr(self, "_pending_source", ""), ""
        if connector:
            self.open_examples(connector)

    def _on_bubble_layer(self, layer_id: str) -> None:
        if layer_id:
            self.layer_action_requested.emit(layer_id, "show")

    def set_connection_state(self, state: str, detail: str = "") -> None:










        state = str(state or "").strip().lower()
        if state not in {"connecting", "online", "offline", "signed_out", "error"}:
            state = "error"
        detail = str(detail or "")[:500]
        self._connection = state
        if state == "signed_out":
            if self._signed_in:
                self.mascot_stage.fire("signout")
            self._signed_in = False
            self._checkout_return = False
            self._return_view_pending = False
            self._low_card_closed = False
            self._pro_welcome = None
            self._payfail = None
            self._start_now_url = ""
            self._plan_known = False
            self._keep_pro = None
            self._returning = False
            set_pro_offer("trial" if self.trial_offer() else "none")
            self._sync_pro_pill()


            self.activation.show_signed_out(detail or "")
            self._show_thread_surface()
            return
        if state == "online":
            if not self._signed_in:
                self.mascot_stage.fire("signin.success")
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






        self.composer.set_offline(True, state, detail if state == "offline" else None)

    def set_model_label(self, label: str) -> None:

        self._model_label = label or ""

    def set_usage(self, runs_used: int, runs_limit: int, period_end_iso: str = "",
                  is_subscriber=None) -> None:


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
            is_subscriber = self._paid_plan if self._plan_known else limit > max(get_free_runs(), LEGACY_FREE_RUNS_MAX)
        self._usage = (used, limit, str(period_end_iso or "")[:80], bool(is_subscriber))
        self._apply_quota()
        stage = getattr(self, "mascot_stage", None)
        if stage is not None:
            stage.rest()
        sidebar = getattr(self, "sidebar", None)
        if sidebar is not None:
            sidebar.set_paid(bool(is_subscriber))
        if stated:
            self._set_plan_paid(bool(is_subscriber))

    def set_trial_offer(self, days, ends_on=None) -> None:



        from ..core.plan_moment import trial_days

        days = trial_days(days)
        offer = (days, str(ends_on)[:10] if isinstance(ends_on, str) and ends_on else None) if days else None
        if offer == self.trial_offer():
            return
        self._trial_days = offer
        self._sync_offer_kind()

    def set_returning(self, returning: bool) -> None:


        if bool(returning) == self._returning:
            return
        self._returning = bool(returning)
        self._sync_offer_kind()

    def _sync_offer_kind(self) -> None:
        set_pro_offer("trial" if self.trial_offer() else "return" if self._returning else "none")
        self._apply_quota()
        self._sync_pro_pill()

    def set_keep_pro(self, url: str, ends_on: str) -> None:


        keep = (url, format_reset_date(ends_on) or ends_on) if url else None
        if keep == self._keep_pro:
            return
        self._keep_pro = keep
        self._sync_pro_pill()

    def keep_pro_url(self) -> str:
        return self._keep_pro[0] if self._keep_pro else ""

    def set_start_now_url(self, url: str) -> None:

        url = str(url or "")
        if url == getattr(self, "_start_now_url", ""):
            return
        self._start_now_url = url
        self._apply_quota()

    def set_account_cap(self, cap) -> None:


        blocked = isinstance(cap, dict) and bool(cap.get("blocked"))
        text = cap.get("message") if blocked else None
        value = (text if isinstance(text, str) and text.strip() else "") if blocked else None
        if value == getattr(self, "_account_cap", None):
            return
        self._account_cap = value
        self._apply_quota()

    def trial_offer(self) -> tuple | None:

        return getattr(self, "_trial_days", None)

    def _composer_is_being_typed_in(self) -> bool:






        composer = getattr(self, "composer", None)
        field = getattr(composer, "_input", None)
        if field is None:
            return False
        focus = QApplication.focusWidget()
        if focus is None or not (focus is field or field.isAncestorOf(focus)):
            return False
        try:
            return bool(field.toPlainText().strip())
        except Exception:  # noqa: BLE001
            return False

    def _mark_compaction(self, usage, animate: bool = True) -> None:






        try:
            folds = int((usage or {}).get("compaction_folds") or 0)
        except (AttributeError, TypeError, ValueError):
            return
        if folds <= 0 or self._compaction_marked:
            return
        self._compaction_marked = True
        divider = CompactionDivider(
            self.tr("Conversation compacted"),
            self.tr("The AI agent now reads the earlier exchanges as a shorter record "
                    "that keeps your requests and what it made."))
        divider.show()
        self._add(divider, animate=animate)

    def set_mentions(self, items) -> None:

        self.composer.set_mentions(list(items or []))

    def set_mention_provider(self, provider) -> None:

        self.composer.set_mention_provider(provider)

    def set_dictation(self, dictation) -> None:
        self.composer.set_dictation(dictation)

    def set_suggester(self, suggester) -> None:
        self.composer.set_suggester(suggester)

    def set_permission_mode(self, approval: str) -> None:

        self.composer.set_permission_mode(approval)

    def set_effort(self, effort: str) -> None:

        self.composer.set_effort(effort)

    def set_paid_plan(self, paid: bool) -> None:

        self.composer.set_paid_plan(paid)
        self._set_plan_paid(paid)

    def add_context_chip(self, chip: dict) -> None:

        self.composer.add_chip(chip)

    def set_pairing_state(self, waiting: bool, code: str = "", url: str = "") -> None:

        if waiting:
            self.activation.show_pairing_waiting(code, url)
        else:
            self.activation.show_signed_out()
        self.mascot_stage.fire("signin.wait" if waiting else "rest")

    def show_pairing_code_input(self) -> None:

        self.activation.show_pairing_code_input()

    def set_pairing_status(self, text: str) -> None:

        self.activation.set_pairing_status(text)

    def set_pairing_note(self, text: str, kind: str = "warning") -> None:



        self.activation.set_pairing_note(text, kind)

    def apply_server_config(self, payload: dict) -> None:

        self._apply_quota()

    def current_run_id(self) -> str:
        return self._current_run or ""
