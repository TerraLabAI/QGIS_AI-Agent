# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

import contextlib
import os

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..core.protocol import StopCode
from .cards import (
    ErrorCard,
    PermissionCard,
    QuestionCard,
    QuotaPauseCard,
)
from .confirm_dialog import ConfirmDialog
from .loader import ElapsedClock, ShimmerLabel, user_waiting
from .widgets import Spinner


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
            joined.quiet = joined.quiet and tool is not None
            self.message_list.register_permission_card(tool_call_id, joined)

            self._wait_on_user(run_id, self.tr("Waiting for your approval"))
            QTimer.singleShot(0, self._show_pending_card)
            return
        card = PermissionCard(tool_call_id, sentence, dict(args or {}), name=name, addresses=addresses,
                              grant=grant, group=group)

        card.quiet = tool is not None
        card.decided.connect(self.permission_decided.emit)
        self._wait_on_user(run_id, self.tr("Waiting for your approval"))
        self.message_list.register_permission_card(tool_call_id, card)
        run.permissions.append(card)
        self._add(card)



        QTimer.singleShot(0, self._show_pending_card)

    def show_call_running(self, run_id: str, tool_call_id: str) -> None:



        tool = self.message_list.tool_card(tool_call_id)
        if tool is not None and self._live_run(run_id) is not None:
            self.set_status_line(run_id, tool.line())

    def _wait_on_user(self, run_id: str, text: str) -> None:








        user_waiting(True)
        self.set_status_line(run_id, text)
        if self._status is not None:
            self._status.stop()
            for clock in self._status.findChildren(ElapsedClock):
                clock.hide()
        run = self._live_run(run_id)
        if run is not None and run.trace is not None and run.trace.is_live():
            self._hold_motion(run.trace)

    def _hold_motion(self, block) -> None:




        held = self.__dict__.setdefault("_held_motion", [])
        for kind in (Spinner, ShimmerLabel):
            for part in block.findChildren(kind):

                if not part.isHidden() and not any(part is h for h in held):
                    part.stop()
                    held.append(part)
        for clock in block.findChildren(ElapsedClock):
            if not clock.isHidden():
                clock.stop()
                clock.hide()
                held.append(clock)

    def _release_motion(self) -> None:
        user_waiting(False)
        for part in self.__dict__.pop("_held_motion", []):
            try:
                if isinstance(part, ElapsedClock):
                    part.show()
                elif not part.isHidden():
                    part.start()
            except RuntimeError:
                pass

    def _resume_line(self, run_id: str, tool_call_id: str = "") -> None:







        run = self._live_run(run_id) if run_id else None
        if run is None or run_id != self._current_run:
            self._release_motion()
            return
        if any(callable(getattr(c, "is_open", None)) and c.is_open() for c in run.permissions):
            return
        self._release_motion()
        tool = self.message_list.tool_card(tool_call_id) if tool_call_id else None
        line = ""
        if tool is not None and getattr(tool, "ok", True) is None:
            line = tool.line()
        text = line or self.tr("Thinking...")
        self._drop_status()
        if run.trace is not None and run.trace.is_live():


            run.trace.set_activity(text)
            if not self._block_is_tail(run.trace):
                return
        self.set_status_line(run_id, text)

    def _show_pending_card(self) -> None:
        try:
            self.message_list.scroll_to_bottom()
        except (AttributeError, RuntimeError):
            pass

    def ask_question(self, tool_call_id: str, run_id: str, question: str, options,
                     allow_free_text: bool, recommended: int = -1, why: str = "",
                     multiple: bool = False, details=None,
                     header: str = "") -> None:










        run = self._live_run(run_id)
        if run is None:






            self.question_answered.emit(tool_call_id, "")
            return
        open_card = next((c for c in reversed(run.permissions)
                          if isinstance(c, QuestionCard) and c.is_open()), None)
        if open_card is not None:
            open_card.add_page(tool_call_id, question, list(options or []), allow_free_text,
                               recommended=recommended, why=why,
                               multiple=multiple, details=details, header=header)
            self.message_list.register_permission_card(tool_call_id, open_card)
            return
        card = QuestionCard(tool_call_id, question, list(options or []), allow_free_text,
                            recommended=recommended, why=why,
                            multiple=multiple, details=details, header=header)
        card.answered.connect(self.question_answered.emit)
        self._wait_on_user(run_id, self.tr("Waiting for your answer"))
        self.message_list.register_permission_card(tool_call_id, card)
        run.permissions.append(card)
        self._add(card)
        if not self._composer_is_being_typed_in():
            card.focus()

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
        self._wait_on_user(run_id, self.tr("Waiting for your answer"))
        self.message_list.register_permission_card(tool_call_id, card)
        run.permissions.append(card)
        self._add(card)

    def _on_recommendation_decided(self, tool_call_id: str, decision: str) -> None:
        self._card_answered(tool_call_id)
        self.recommendation_decided.emit(tool_call_id, decision)

    def _card_answered(self, tool_call_id: str) -> None:


        card = self.message_list.permission_card(tool_call_id)
        run_id = ""
        for run in self._runs.values():
            if card is not None and card in run.permissions:
                run_id = run.run_id
                break
        self._resume_line(run_id or self._current_run)

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
        run_id = ""
        for run in self._runs.values():
            if card in run.permissions:
                run_id = run.run_id
                break
        self._resume_line(run_id or self._current_run)

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
        run_id = ""
        for run in self._runs.values():
            if card in run.permissions:
                run_id = run.run_id
                break
        if reason == "denied":



            if card.decision is None:
                card.collapse(decision)
            if run_id:
                for run in self._runs.values():
                    if card in run.permissions:
                        run.permissions.remove(card)
                self._mark_denied(run_id, tool_call_id, decision, reason, said, card)
                self._resume_line(run_id, tool_call_id if decision != "deny" else "")
            return


        if card.decision is None and not run_id:
            card.collapse(decision)
        self._release_motion()
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
                             whole: bool = True, requests: int = 0, layers=None) -> None:








        old = getattr(self, "_restore_dialog", None)
        if old is not None:
            try:
                old.reject()
            except RuntimeError:
                pass
        requests = max(0, int(requests or 0))

        if discard:
            title = self.tr("Go back to the start of the chat?")
        elif requests > 1:
            title = self.tr("Undo {n} requests?").format(n=requests)
        else:
            title = self.tr("Undo changes?")
        points = []
        if discard:
            points.append(("lu.history", self.tr("Back to the project as this chat found it.") if whole
                           else self.tr("Back to the oldest version still kept.")))
        elif requests > 1:
            later = requests - 1
            points.append(("lu.undo-2", self.tr("This also removes the changes from 1 later request.")
                           if later == 1 else
                           self.tr("This also removes the changes from {n} later requests.").format(n=later)))
        if edits:
            points.append(("pencil", self.tr("Your manual edits are saved in the history first.")))
        dialog = ConfirmDialog(self.window(), title=title, confirm_text=self.tr("Undo"), points=points,
                               object_name="restoreConfirm")
        items = [item for item in (layers or []) if isinstance(item, dict) and item.get("name")]
        if items:

            dialog.layout().insertWidget(2, _LayerList(items, dialog))
        dialog.checkpoint_id, dialog.discard = checkpoint_id, bool(discard)
        dialog.accepted.connect(lambda: self._on_restore_confirmed(checkpoint_id, discard))
        dialog.finished.connect(lambda _code: dialog.deleteLater())
        self._restore_dialog = dialog
        dialog_id = id(dialog)
        dialog.destroyed.connect(lambda *_a: self._forget_restore_dialog(dialog_id))
        dialog.open()

    def _forget_restore_dialog(self, dialog_id: int) -> None:
        dialog = getattr(self, "_restore_dialog", None)
        if dialog is not None and id(dialog) == dialog_id:
            self._restore_dialog = None

    def _on_restore_confirmed(self, checkpoint_id: str, discard: bool) -> None:
        if discard:
            self.discard_all_requested.emit(True)
        else:
            self.restore_requested.emit(checkpoint_id, True)

    def show_quota_pause(self, run_id: str, message: str) -> None:

        paid = bool(self._paid_plan if self._plan_known else (self._usage[1] > 0 and self._usage[3]))
        card = QuotaPauseCard(run_id, message, offer_pro=not paid)
        card.upgrade_requested.connect(lambda: self._on_upgrade("quota_card"))

        self._error_said[run_id or ""] = card.message.strip()
        self._add(card)
        if not paid:
            self._note_upsell("quota_card")

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


_LIST_SHOWN = 4


def _layer_glyph(name: str) -> str:

    try:
        from qgis.core import QgsProject

        from .layer_links import _glyph_from_project

        found = QgsProject.instance().mapLayersByName(name)
        if not found:
            return "layers"
        layer = found[0]
        provider = str(layer.providerType() or "").lower()
        if provider in ("wms", "xyz", "arcgismapserver") or "vectortile" in provider:
            return "globe"
        return _glyph_from_project(layer.id())
    except Exception:  # noqa: BLE001
        return "layers"


class _LayerList(QWidget):




    def __init__(self, items: list, parent=None):
        super().__init__(parent)
        from . import style as S
        from .confirm_dialog import BADGE_PX, ROW_SPACING, SIDE_PAD
        from .font_scale import scale_px_length, scale_qss_font_px

        self._qss_label = scale_qss_font_px(
            f"font-size: 11px; color: {S.INK_3}; background: transparent;")
        self._qss_name = scale_qss_font_px(f"font-size: 13px; color: {S.INK}; background: transparent;")
        self._ink = S.INK_2
        col = QVBoxLayout(self)
        indent = scale_px_length(BADGE_PX) + ROW_SPACING
        col.setContentsMargins(SIDE_PAD + indent, 0, SIDE_PAD, 16)
        col.setSpacing(3)
        known = all(item.get("group") in ("removed", "restored") for item in items)
        if known:
            groups = [(self.tr("Removed"), [i for i in items if i["group"] == "removed"]),
                      (self.tr("Restored"), [i for i in items if i["group"] == "restored"])]
        else:
            groups = [(self.tr("Layers affected"), list(items))]
        self._hidden: list = []
        shown = 0
        for label, members in groups:
            if not members:
                continue
            head = QLabel(label, self)
            head.setStyleSheet(self._qss_label)
            col.addSpacing(2 if col.count() == 0 else 6)
            col.addWidget(head)
            hidden_head = shown >= _LIST_SHOWN
            if hidden_head:
                head.hide()
                self._hidden.append(head)
            for item in members:
                row = self._row(str(item["name"]))
                col.addWidget(row)
                if shown >= _LIST_SHOWN:
                    row.hide()
                    self._hidden.append(row)
                shown += 1
        rest = shown - _LIST_SHOWN
        if rest > 0:
            self._more = QPushButton(self.tr("+{n} more").format(n=rest), self)
            self._more.setObjectName("restoreLayersMore")
            self._more.setCursor(Qt.CursorShape.PointingHandCursor)
            self._more.setStyleSheet(scale_qss_font_px(
                "QPushButton { background: transparent; border: none; padding: 2px 0; text-align: left;"
                f" font-size: 12px; color: {S.INK_2}; }}"
                f"QPushButton:hover {{ color: {S.INK}; }}"))
            self._more.clicked.connect(self._expand)
            col.addWidget(self._more, 0, Qt.AlignmentFlag.AlignLeft)

    def _row(self, name: str) -> QWidget:
        from qgis.PyQt.QtGui import QColor

        from .icons import pixmap_for
        from .widgets import ElidedLabel

        row = QWidget(self)
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        glyph = QLabel(row)
        glyph.setFixedSize(14, 14)
        glyph.setPixmap(pixmap_for(row, _layer_glyph(name), 14, QColor(self._ink)))
        line.addWidget(glyph, 0, Qt.AlignmentFlag.AlignVCenter)
        label = ElidedLabel(name, row, mode=Qt.TextElideMode.ElideMiddle)
        label.setStyleSheet(self._qss_name)
        line.addWidget(label, 1)
        return row

    def _expand(self) -> None:
        for widget in self._hidden:
            widget.show()
        self._hidden = []
        self._more.hide()
        dialog = self.window()
        if dialog is not None:
            dialog.adjustSize()
