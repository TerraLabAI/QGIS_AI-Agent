# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat panel's cards and dialogs: what a run asks and what it reports."""





from __future__ import annotations

import time

from ..core.protocol import StopCode
from .cards import (
    ErrorCard,
    PermissionCard,
    QuestionCard,
    QuotaPauseCard,
    RestoreWarningCard,
)


class _ChatPanelPrompts:

    def ask_permission(self, tool_call_id: str, run_id: str, sentence: str, args) -> None:
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
        card = PermissionCard(tool_call_id, sentence, dict(args or {}), name=name)
        card.decided.connect(self.permission_decided.emit)
        self.set_status_line(run_id, self.tr("Waiting for your answer..."))
        run.wait_started = time.monotonic()
        self.message_list.register_permission_card(tool_call_id, card)
        run.permissions.append(card)
        self._add(card)

    def ask_question(self, tool_call_id: str, run_id: str, question: str, options,
                     allow_free_text: bool, recommended: int = -1, why: str = "",
                     timeout_s: int = 0, multiple: bool = False) -> None:
        """``recommended`` is the index of the option the agent suggests (-1 none), ``why`` its one-clause reason, ``timeout_s`` the seconds before."""








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
        """The working layers a finished run left behind, offered back."""





        items = [i for i in list(items or []) if isinstance(i, dict)]
        if not items:
            return
        from .cards_cleanup import CleanupCard
        card = CleanupCard(str(run_id or ""), items, self.message_list)
        card.decided.connect(self.cleanup_decided.emit)
        self._cleanup_cards[str(run_id or "")] = card
        self._add(card)

    def finish_cleanup(self, run_id: str, sentence: str = "") -> None:
        """What the controller did with them, folded onto the card."""
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
        """The run proposes a heavy step: the recommendation card, whose Accept, an alternative or the x emit ``recommendation_decided``."""


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
        """The bookkeeping every card the panel answers shares: the time the user spent on it leaves the run's duration, and the status line stops."""

        card = self.message_list.permission_card(tool_call_id)
        for run in self._runs.values():
            if card is not None and card in run.permissions and run.wait_started:
                run.waited += time.monotonic() - run.wait_started
                run.wait_started = 0.0
                break
        if self._status is not None and self._current_run:
            self._status.set_text(self.tr("Thinking..."))

    def show_diff_table(self, run_id: str, title: str, columns, rows) -> None:
        """The attribute edits a run proposes, as the diff table; Apply emits ``diff_applied`` with the ids of the rows still checked."""



        from .cards_proposal import DiffTableCard

        card = DiffTableCard(run_id, title, list(columns or []), list(rows or []))
        card.applied.connect(self.diff_applied.emit)
        self._add(card)

    def _restore_tool_card(self, tool_call_id: str) -> None:
        """Put a call opened for its permission card back as the reader had it."""
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

    def resolve_permission(self, tool_call_id: str, decision: str, reason: str = "") -> None:
        self._restore_tool_card(tool_call_id)
        card = self.message_list.permission_card(tool_call_id)
        if card is None:
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
            stopped = decision == "deny" and reason == "stopped"
            if decision == "deny":


                tool = self.message_list.tool_card(tool_call_id)
                if tool is not None and tool.ok is None:
                    tool.mark_unfinished("stopped" if stopped else "denied")
                else:
                    self._trace_for(run_id).add_note(
                        f"{self.tr('Stopped')}: {card.sentence}" if stopped
                        else f"{card.decision_text()}: {card.sentence}",
                        failed=not stopped)

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
        self._add(card)

    def open_checkpoints(self) -> None:
        """The restore sheet, under the header's undo button."""






        self.header.open_checkpoints()

    def show_restore_warning(self, checkpoint_id: str, discard: bool = False, edits: bool = False,
                             whole: bool = True) -> None:
        card = RestoreWarningCard(checkpoint_id, discard, edits, whole)
        card.confirmed.connect(self._on_restore_confirmed)
        self._add(card)
        self.message_list.scroll_to_bottom()

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
        """The session notice, or none: the bar hides itself on an empty dict."""
        if notice:
            self.notice_bar.show_notice(notice)
        else:
            self.notice_bar.clear()

    def show_update(self, version: str, note: str = "", required: bool = False,
                    installed: str = "") -> None:
        """Offer an installable version: a banner with Later, or, when ``required``, the card that replaces the chat until it is done."""

        if required:
            self.update_banner.hide()
            self.update_gate.offer(version, note, installed)
        else:
            self.update_gate.hide()
            self.update_banner.offer(version, note)
        self._show_thread_surface()

    def clear_update(self) -> None:
        """No update to offer any more: the chat comes back."""
        was_gated = self.update_gate.isVisibleTo(self)
        self.update_gate.hide()
        self.update_banner.hide()
        if was_gated:
            self._show_thread_surface()
