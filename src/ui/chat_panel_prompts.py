# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

import contextlib
import os
import time

from qgis.PyQt.QtCore import QTimer

from ..core.protocol import StopCode
from .cards import (
    ErrorCard,
    PermissionCard,
    QuestionCard,
    QuotaPauseCard,
    RestoreWarningCard,
)


class _ChatPanelPrompts:

    def ask_permission(self, tool_call_id: str, run_id: str, sentence: str, args, addresses=None,
                       grant: str = "", group: str = "") -> None:
        run = self._live_run(run_id)
        if run is None:






            self.permission_decided.emit(tool_call_id, "deny", None)
            return
        tool = self.message_list.tool_card(tool_call_id)
        name = tool.name if tool is not None else ""





        if tool is not None:
            self._reopened_tools[tool_call_id] = tool.is_expanded()
            tool.set_expanded(True)


            reveal = getattr(tool, "reveal_source", None)
            if callable(reveal):
                reveal(True)



        joined = next((c for c in reversed(run.permissions)
                       if isinstance(c, PermissionCard) and group and c.add_call(
                           tool_call_id, sentence, dict(args or {}), name=name, addresses=addresses,
                           group=group)), None)
        if joined is not None:
            self.message_list.register_permission_card(tool_call_id, joined)
            QTimer.singleShot(0, self._show_pending_card)
            return
        card = PermissionCard(tool_call_id, sentence, dict(args or {}), name=name, addresses=addresses,
                              grant=grant, group=group)
        card.decided.connect(self.permission_decided.emit)
        self.set_status_line(run_id, self.tr("Waiting for your approval"))
        run.wait_started = time.monotonic()
        self.message_list.register_permission_card(tool_call_id, card)
        run.permissions.append(card)
        self._add(card)



        QTimer.singleShot(0, self._show_pending_card)

    def _show_pending_card(self) -> None:
        try:
            self.message_list.scroll_to_bottom()
        except (AttributeError, RuntimeError):
            pass

    def ask_question(self, tool_call_id: str, run_id: str, question: str, options,
                     allow_free_text: bool, recommended: int = -1, why: str = "",
                     timeout_s: int = 0, multiple: bool = False) -> None:









        run = self._live_run(run_id)
        if run is None:






            self.question_answered.emit(tool_call_id, "")
            return
        open_card = next((c for c in reversed(run.permissions)
                          if isinstance(c, QuestionCard) and c.is_open()), None)
        if open_card is not None:
            open_card.add_page(tool_call_id, question, list(options or []), allow_free_text,
                               recommended=recommended, why=why, timeout_s=timeout_s,
                               multiple=multiple)
            self.message_list.register_permission_card(tool_call_id, open_card)
            return
        card = QuestionCard(tool_call_id, question, list(options or []), allow_free_text,
                            recommended=recommended, why=why, timeout_s=timeout_s,
                            multiple=multiple)
        card.answered.connect(self.question_answered.emit)
        card.auto_answered.connect(self.question_auto_answered.emit)
        self.set_status_line(run_id, self.tr("Waiting for your answer..."))
        run.wait_started = time.monotonic()
        self.message_list.register_permission_card(tool_call_id, card)
        run.permissions.append(card)
        self._add(card)
        if not self._composer_is_being_typed_in():
            card.focus()

    def offer_cleanup(self, run_id: str, items) -> None:






        items = [i for i in list(items or []) if isinstance(i, dict)]
        if not items:
            return
        from .cards_cleanup import CleanupCard
        card = CleanupCard(str(run_id or ""), items, self.message_list)
        card.decided.connect(self.cleanup_decided.emit)
        self._cleanup_cards[str(run_id or "")] = card
        self._add(card)

    def finish_cleanup(self, run_id: str, sentence: str = "") -> None:

        card = self._cleanup_cards.pop(str(run_id or ""), None)
        if card is None:
            return
        try:
            card.collapse(str(sentence or ""))
        except RuntimeError:
            pass

    def ask_recommendation(self, tool_call_id: str, run_id: str, title: str, proposal: str,
                           entity: str = "", value: str = "", confidence: str = "high",
                           alternatives=None) -> None:



        from .cards_proposal import RecommendationCard

        run = self._live_run(run_id)
        if run is None:






            self.recommendation_decided.emit(tool_call_id, "dismiss")
            return
        card = RecommendationCard(tool_call_id, run_id, title, proposal, entity, value,
                                  confidence, list(alternatives or []))
        card.decided.connect(self._on_recommendation_decided)
        self.set_status_line(run_id, self.tr("Waiting for your answer..."))
        run.wait_started = time.monotonic()
        self.message_list.register_permission_card(tool_call_id, card)
        run.permissions.append(card)
        self._add(card)

    def _on_recommendation_decided(self, tool_call_id: str, decision: str) -> None:
        self._card_answered(tool_call_id)
        self.recommendation_decided.emit(tool_call_id, decision)

    def _card_answered(self, tool_call_id: str) -> None:


        card = self.message_list.permission_card(tool_call_id)
        for run in self._runs.values():
            if card is not None and card in run.permissions and run.wait_started:
                run.waited += time.monotonic() - run.wait_started
                run.wait_started = 0.0
                break
        if self._status is not None and self._current_run:
            self._status.set_text(self.tr("Thinking..."))

    def show_diff_table(self, run_id: str, title: str, columns, rows) -> None:




        from .cards_proposal import DiffTableCard

        card = DiffTableCard(run_id, title, list(columns or []), list(rows or []))
        card.applied.connect(self.diff_applied.emit)
        self._add(card)

    def _restore_tool_card(self, tool_call_id: str) -> None:

        was_open = self._reopened_tools.pop(tool_call_id, None)
        if was_open is None:
            return
        tool = self.message_list.tool_card(tool_call_id)
        if tool is not None:
            reveal = getattr(tool, "reveal_source", None)
            if callable(reveal):
                reveal(False)
            tool.set_expanded(bool(was_open))

    def resolve_question(self, tool_call_id: str, answer: str) -> None:
        card = self.message_list.permission_card(tool_call_id)
        if card is None:
            return
        if hasattr(card, "is_answered"):
            if not card.is_answered(tool_call_id):
                card.collapse(answer, tool_call_id)

            if card.is_open():
                return
        elif getattr(card, "answer", None) is None:
            card.collapse(answer)
        if self._status is not None and self._current_run:
            self._status.set_text(self.tr("Thinking..."))
        for run in self._runs.values():
            if card in run.permissions and run.wait_started:
                run.waited += time.monotonic() - run.wait_started
                run.wait_started = 0.0
                break

    def resolve_permission(self, tool_call_id: str, decision: str, reason: str = "denied") -> None:
        self._restore_tool_card(tool_call_id)
        card = self.message_list.permission_card(tool_call_id)
        if card is None:
            return

        said = card.sentence_for(tool_call_id) if hasattr(card, "sentence_for") else card.sentence
        if hasattr(card, "resolve") and not card.resolve(tool_call_id):

            self.message_list.permission_cards.pop(tool_call_id, None)
            self._resolved_one_of_many(card, tool_call_id, decision, reason, said)
            return
        if card.decision is None:
            card.collapse(decision)
        if self._status is not None and self._current_run:
            self._status.set_text(self.tr("Thinking..."))


        run_id = ""
        for run in self._runs.values():
            if card in run.permissions:
                run_id = run.run_id
                if run.wait_started:
                    run.waited += time.monotonic() - run.wait_started
                    run.wait_started = 0.0
                break
        if run_id:



            if hasattr(card, "cleanup"):
                card.cleanup()
            self.message_list.remove_widget(card)
            for run in self._runs.values():
                if card in run.permissions:
                    run.permissions.remove(card)
            self._mark_denied(run_id, tool_call_id, decision, reason, said, card)

    def _resolved_one_of_many(self, card, tool_call_id: str, decision: str, reason: str,
                              said: str) -> None:

        run_id = next((r.run_id for r in self._runs.values() if card in r.permissions), "")
        if run_id:
            self._mark_denied(run_id, tool_call_id, decision, reason, said, card)

    def _mark_denied(self, run_id: str, tool_call_id: str, decision: str, reason: str, said: str,
                     card) -> None:




        if decision != "deny":
            return
        tool = self.message_list.tool_card(tool_call_id)
        if tool is not None and tool.ok is None:
            tool.mark_unfinished(reason)
        elif reason == "stopped":
            self._trace_for(run_id).add_note(f"{self.tr('Stopped')}: {said}")
        elif reason == "unanswered":
            self._trace_for(run_id).add_note(f"{self.tr('Not answered')}: {said}")
        else:
            self._trace_for(run_id).add_note(f"{card.decision_text('deny')}: {said}", failed=True)

    def show_error(self, run_id, code: str, message: str, retryable: bool,
                   details: str = "", retry_key: str = "") -> None:
        run_id = str(run_id or "")[:256]
        if run_id and self._live_run(run_id) is None:
            return
        code = str(code or "UNKNOWN")[:100]
        message = str(message or "")[:2000]
        details = str(details or "")[:4000]
        self._last_error = message or code



        self._error_said[run_id or ""] = (message or "").strip()


        card = ErrorCard(run_id or "", code, message, retryable, details,
                         continuable=str(code or "") in StopCode.RESUMABLE)
        key = str(retry_key or "")[:256]
        if key and not run_id:

            card.retry_requested.connect(lambda _run_id, key=key: self.retry_requested.emit(key))
        else:
            card.retry_requested.connect(self.retry_requested.emit)
        card.continue_requested.connect(self.continue_requested.emit)
        if run_id and retryable:
            card.undo_retry_requested.connect(self.undo_retry_requested.emit)
            self._error_cards[run_id] = card
            if self._current_run is None:
                card.set_undo_retry(self._answer_points().get(run_id, ("",))[0] == "undo")
        self._add(card)

    def open_checkpoints(self) -> None:







        self.header.open_checkpoints()

    def show_restore_warning(self, checkpoint_id: str, discard: bool = False, edits: bool = False,
                             whole: bool = True, point: str = "") -> None:



        old = getattr(self, "_restore_card", None)
        if old is not None:
            try:
                old.hide()
                old.deleteLater()
            except RuntimeError:
                pass
        card = RestoreWarningCard(checkpoint_id, discard, edits, whole, point=point)
        card.confirmed.connect(self._on_restore_confirmed)
        card_id = id(card)
        card.destroyed.connect(lambda *_a: self._forget_restore_card(card_id))
        self._restore_card = card
        self._add(card)
        self.message_list.scroll_to_bottom()

    def _forget_restore_card(self, card_id: int) -> None:
        card = getattr(self, "_restore_card", None)
        if card is not None and id(card) == card_id:
            self._restore_card = None

    def _on_restore_confirmed(self, checkpoint_id: str, discard: bool) -> None:
        if discard:
            self.discard_all_requested.emit(True)
        else:
            self.restore_requested.emit(checkpoint_id, True)

    def show_quota_pause(self, run_id: str, message: str) -> None:
        card = QuotaPauseCard(run_id, message)
        card.resume_requested.connect(self.retry_requested.emit)
        self._add(card)

    def show_notice(self, notice: dict) -> None:

        if notice:
            self.notice_bar.show_notice(notice)
        else:
            self.notice_bar.clear()

    def show_update(self, version: str, note: str = "", required: bool = False,
                    installed: str = "") -> None:


        if required:
            self.update_banner.hide()
            self.update_gate.offer(version, note, installed)
        else:
            self.update_gate.hide()
            self.update_banner.offer(version, note)
        self._show_thread_surface()

    def _update_now(self, version: str) -> None:





        from .plugin_self_update import one_click_result_tracker, start_plugin_self_update
        from .shared import PLUGIN_DIR
        from .terralab_menu import open_plugin_manager_updates

        self.update_clicked.emit(version)
        self.update_banner.set_busy(True)
        self.update_gate.set_busy(True)

        def fallback() -> None:
            for card in (self.update_banner, self.update_gate):
                with contextlib.suppress(RuntimeError):
                    card.set_busy(False)
            open_plugin_manager_updates()

        start_plugin_self_update(os.path.basename(PLUGIN_DIR), version, fallback,
                                 on_result=one_click_result_tracker(version),
                                 is_busy=self.composer.is_running)

    def clear_update(self) -> None:

        was_gated = self.update_gate.isVisibleTo(self)
        self.update_gate.hide()
        self.update_banner.hide()
        if was_gated:
            self._show_thread_surface()
