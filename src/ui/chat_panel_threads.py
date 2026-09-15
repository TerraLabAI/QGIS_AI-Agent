# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





from __future__ import annotations

import contextlib

from qgis.PyQt.QtCore import QEvent, Qt
from qgis.PyQt.QtGui import QKeyEvent, QKeySequence
from qgis.PyQt.QtWidgets import (
    QApplication,
    QGraphicsOpacityEffect,
    QLineEdit,
    QPlainTextEdit,
    QShortcut,
    QTextEdit,
    QWidget,
)

from ..core.background import run_sliced
from .bubbles import UserBubble
from .chat_panel_shared import RestoreDivider
from .checkpoint_sheet import affected_layers, history_rows, long_reason, not_restored_line, row_target, usable
from .shared import CHECKPOINTS_KEYS, STEP_BACK_KEYS, STEP_FORWARD_KEYS
from .thread_replay import replay_steps
from .trace import RunFootnote


REPLAY_SLICE_MS = 24

UNDONE_OPACITY = 0.45


class _ChatPanelThreads:





    def _show_thread_surface(self) -> None:

        signed_in = self._signed_in


        gated = self.update_gate.isVisibleTo(self)
        self.activation.setVisible(not signed_in and not gated)
        empty = self.message_list.is_empty()
        self.message_list.setVisible(signed_in and not empty and not gated)
        self.empty_state.setVisible(signed_in and empty and not gated)
        self._empty_rows.setVisible(signed_in and empty and not gated)
        self._composer_host.setVisible(signed_in and not gated)
        self.notice_bar.setVisible(not gated and bool(self.notice_bar.notice_id()))


        self.header.set_history_available(signed_in and empty and not gated)

        self.header.set_new_chat_available(signed_in and not gated)
        if gated:
            self._quota_host.hide()
            self._runs_host.hide()
            self.update_banner.hide()
            sidebar = getattr(self, "sidebar", None)
            if sidebar is not None:
                sidebar.sync_visible(False)
            return



        sidebar = getattr(self, "sidebar", None)
        if sidebar is None:
            from .sidebar import install_sidebar

            self.sidebar = sidebar = install_sidebar(self)
            sidebar.new_thread_requested.connect(self.new_thread_requested.emit)
            sidebar.search_requested.connect(lambda: self.header.history_menu().show_over(self))
            sidebar.thread_selected.connect(self.thread_selected.emit)
            sidebar.upgrade_requested.connect(lambda: self._on_upgrade("sidebar"))
            sidebar.upgrade_shown.connect(lambda: self._note_upsell("sidebar"))
            self.header.set_sidebar(sidebar)
        sidebar.sync_visible(signed_in and empty)





        self.composer.set_placeholder(self.tr("Give the AI agent a task in QGIS..."))
        self.composer.set_empty_chat(empty)
        self._apply_quota()

        centred = signed_in and empty
        self.empty_state.set_centred(centred)
        self._centre_composer(centred, animate=signed_in)

    def _wire_history_keys(self) -> None:








        context = Qt.ShortcutContext.WidgetWithChildrenShortcut
        self._history_shortcuts = []
        for keys, slot in ((STEP_BACK_KEYS, self.step_back), (STEP_FORWARD_KEYS, self.step_forward),
                           (CHECKPOINTS_KEYS, self.open_checkpoints),
                           ("Esc", self._stop_if_running)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(context)
            shortcut.activated.connect(slot)
            if keys == "Esc":
                self._escape_shortcut = shortcut
            else:
                self._history_shortcuts.append(shortcut)



        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._history_keys_follow_focus)
            self._history_keys_follow_focus(None, app.focusWidget())

    def _history_keys_follow_focus(self, _old, new) -> None:
        typing = isinstance(new, QLineEdit) or (
            isinstance(new, (QTextEdit, QPlainTextEdit)) and not new.isReadOnly())
        for shortcut in getattr(self, "_history_shortcuts", ()):
            with contextlib.suppress(RuntimeError):
                shortcut.setEnabled(not typing)

    def _unwire_history_keys(self) -> None:

        app = QApplication.instance()
        if app is not None:
            with contextlib.suppress(TypeError, RuntimeError):
                app.focusChanged.disconnect(self._history_keys_follow_focus)

    def _stop_if_running(self) -> None:



        popup = QApplication.activePopupWidget()
        if popup is not None:
            self._hand_escape_to(popup)
            return
        focus = QApplication.focusWidget()
        composer = getattr(self, "composer", None)
        field = getattr(composer, "_input", None)
        in_composer = (focus is not None and field is not None
                       and (focus is field or field.isAncestorOf(focus)))
        if (focus is not None and self.isAncestorOf(focus) and not in_composer
                and isinstance(focus, (QLineEdit, QPlainTextEdit, QTextEdit))
                and not focus.isReadOnly()):
            return
        if self._current_run:
            self._on_stop()

    def _hand_escape_to(self, popup) -> None:









        focus = QApplication.focusWidget()
        inside = focus is not None and (focus is popup or popup.isAncestorOf(focus))
        target = focus if inside else popup
        shortcut = getattr(self, "_escape_shortcut", None)
        try:
            if shortcut is not None:
                shortcut.setEnabled(False)
            key = Qt.Key.Key_Escape
            event = QKeyEvent(QEvent.Type.KeyPress, int(getattr(key, "value", key)),
                              Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(target, event)
        except Exception:  # noqa: BLE001
            popup.close()
        finally:
            if shortcut is not None:
                shortcut.setEnabled(True)

    def _history_neighbour(self, step: int) -> str:






        rows = history_rows(self._history)
        candidates = [row for row in rows if row["live"] is True] if step < 0 else \
            [row for row in reversed(rows) if row["live"] is False]
        for row in candidates:
            cid = row_target(row)
            if cid:
                return cid
        return ""

    def step_back(self) -> None:
        if self._current_run is not None:
            return
        cid = self._history_neighbour(-1)
        if cid:
            self.restore_requested.emit(cid, False)

    def step_forward(self) -> None:
        if self._current_run is not None:
            return
        cid = self._history_neighbour(1)
        if cid:
            self.restore_requested.emit(cid, False)

    def set_threads(self, items, current_project_path: str | None = None) -> None:
        self.header.set_threads([item for item in list(items or [])[:200]
                                 if isinstance(item, dict)], str(current_project_path or ""))

    def set_current_project(self, path: str) -> None:
        self.header.set_current_project(path or "")

    def set_current_thread(self, thread_id: str | None) -> None:
        self.header.set_current_thread(thread_id or "")
        self.set_queue_thread(thread_id or "")

    def set_history(self, entries: list, undone_runs=None) -> None:






        self._undone_runs = [r for r in list(undone_runs or []) if isinstance(r, str) and r]
        all_entries = [e for e in list(entries or []) if isinstance(e, dict)]


        self._history = all_entries[-200:]
        self.header.set_checkpoints(self._history)
        self._sync_answer_restores()

    def _answer_points(self) -> dict:










        points: dict = {}
        later = 0
        for row in history_rows(self._history):
            live_request = row["type"] == "request" and row["live"]

            if (row["type"] != "request" or row["live"] is None
                    or not (usable(row["before"]) and usable(row["after"]))):
                later += 1 if live_request else 0
                continue
            layers = affected_layers([row], self.tr)
            if row["live"]:
                tip = (self.tr("Removes: {layers}").format(layers=layers) if layers
                       else self.tr("Removes this request's changes from the map"))
                lost = not_restored_line(row["target"], self.tr)
                if lost:
                    tip += "\n" + lost
                points[row["run_id"]] = ("undo", row_target(row), tip, later)
                later += 1
            else:
                tip = (self.tr("Brings back: {layers}").format(layers=layers) if layers
                       else self.tr("Brings this request's changes back"))
                points[row["run_id"]] = ("redo", row_target(row), tip, 0)
        return points

    def _sync_answer_restores(self) -> None:


        running = self._current_run is not None
        try:
            self.header.checkpoint_sheet().set_running(
                running, self._run_requests.get(self._current_run or "", "") if running else "")
        except (AttributeError, RuntimeError):
            pass
        self._sync_undone_turns()
        with contextlib.suppress(AttributeError, RuntimeError):
            self.toast.set_link_enabled(not running)
        points = self._answer_points() if not running else {}
        for run_id, card in list(self._error_cards.items()):
            try:
                card.set_undo_retry(points.get(run_id, ("",))[0] == "undo")
            except RuntimeError:
                self._error_cards.pop(run_id, None)
        for run_id, run in list(self._runs.items()):
            bubble = getattr(run, "bubble", None)
            if bubble is None or not bubble.is_finished():
                continue
            mode, _cid, tip, later = points.get(run_id, ("", "", "", 0))
            try:
                bubble.set_restore(mode, tip, later)
            except RuntimeError:
                continue

    def _undone_turns(self) -> tuple:



        redo = [row for row in history_rows(self._history)
                if row["type"] == "request" and row["live"] is False]
        greyed = {row["run_id"] for row in redo if row["run_id"]}
        self._redo_runs = set(greyed)
        greyed.update(getattr(self, "_undone_runs", None) or [])

        target = next((cid for cid in (row_target(row) for row in redo) if cid), "")
        return greyed, target

    def _sync_undone_turns(self) -> None:

        greyed, target = self._undone_turns()
        divider = getattr(self, "_restore_divider", None)
        hidden_rule = getattr(self, "_restore_hidden_rule", None)
        widgets = [w for w in self.message_list.widgets() if w is not self._status and w is not divider]
        first = None
        first_turn = True
        dim = False
        for index, widget in enumerate(widgets):
            if isinstance(widget, UserBubble) and widget.run_id:
                dim = widget.run_id in greyed


                if dim and first is None and widget.run_id in self._redo_runs:
                    first = (index, first_turn)
                first_turn = False
            elif widget.objectName() == "turnDivider":
                continue
            try:
                self._dim(widget, dim)
            except RuntimeError:
                continue
        if hidden_rule is not None:
            with contextlib.suppress(RuntimeError):
                hidden_rule.show()
            self._restore_hidden_rule = None



        if first is None or not target:
            if divider is not None:
                self.message_list.remove_widget(divider)
                self._restore_divider = None
            return
        index, at_start = first
        text = (self.tr("Restored to start of chat") if at_start
                else self.tr("Restored to here"))
        link = self.tr("Redo")
        if divider is None:
            divider = RestoreDivider(text, link)
            divider.redo_requested.connect(self._on_divider_redo)
            self._restore_divider = divider
        else:
            divider.set_text(text, link)
        divider.target = target
        divider.set_link_enabled(self._current_run is None)
        bubble = widgets[index]

        above = widgets[index - 1] if index > 0 else None
        if above is not None and above.objectName() == "turnDivider":
            above.hide()
            self._restore_hidden_rule = above
        layout = self.message_list._layout
        if layout.indexOf(divider) >= 0:
            layout.removeWidget(divider)
        layout.insertWidget(layout.indexOf(bubble), divider)
        divider.show()

    def _dim(self, widget, dim: bool) -> None:


        effect = widget.graphicsEffect()
        mine = effect is not None and getattr(widget, "_undone_dim", False)
        if effect is not None and not mine:
            if not dim:
                return
            self.message_list._stop_fade(widget)
        if dim and not mine:
            effect = QGraphicsOpacityEffect(widget)
            effect.setOpacity(UNDONE_OPACITY)
            widget.setGraphicsEffect(effect)
            widget._undone_dim = True
        elif not dim and mine:
            widget.setGraphicsEffect(None)
            widget._undone_dim = False

    def _on_divider_redo(self) -> None:
        divider = getattr(self, "_restore_divider", None)
        cid = getattr(divider, "target", "") if divider is not None else ""
        if cid and self._current_run is None:
            self.restore_requested.emit(cid, False)

    def _on_answer_restore(self, run_id: str) -> None:
        if self._current_run is not None:
            return
        point = self._answer_points().get(run_id)
        if point and point[1]:
            self.restore_requested.emit(point[1], False)

    def _on_stop_and_restore(self) -> None:


        run_id = self._current_run
        if not run_id:
            return
        self.restore_after_stop_requested.emit(run_id)
        self._on_stop()

    def show_restore_result(self, ok: bool, back: bool = True, revert_id: str = "",
                            missing=None) -> None:






        items = [item for item in (missing or []) if isinstance(item, dict) and item.get("name")]
        if not ok:
            self.toast.show_message(self.tr("Couldn't restore. See the log."), warn=True)
            return
        if items:
            text = (self.tr("1 layer couldn't be restored") if len(items) == 1
                    else self.tr("{n} layers couldn't be restored").format(n=len(items)))
            tip = "\n".join(
                self.tr("{layer}: {reason}").format(layer=str(item["name"]),
                                                    reason=long_reason(str(item.get("reason") or ""), self.tr))
                for item in items)
        else:
            text = self.tr("Changes undone") if back else self.tr("Changes restored")
            tip = ""
        link = (self.tr("Redo") if back else self.tr("Undo")) if revert_id else ""
        self.toast.show_message(text, link=link, on_link=lambda: self._on_toast_revert(revert_id),
                                warn=bool(items), tooltip=tip)
        self.toast.set_link_enabled(self._current_run is None)

    def _on_toast_revert(self, checkpoint_id: str) -> None:
        if checkpoint_id and self._current_run is None:
            self.restore_requested.emit(checkpoint_id, False)

    def note_memory(self, text: str) -> None:









        text = str(text or "").strip()
        if not text:
            return
        note = RunFootnote()
        note.setWordWrap(True)


        note.setText(self.tr("Added to memory: {0}").format(text.rstrip(" .!?;,")))
        note.show()


        follow = self.message_list.at_bottom()
        self._add(note)
        if follow:
            self.message_list.scroll_to_bottom()

    def propose_memory(self, key: str, text: str, replaces_text: str = "") -> None:



        text = str(text or "").strip()
        if not text:
            return
        self.drop_memory_proposal()
        from .cards_memory import MemoryCard
        card = MemoryCard(str(key or ""), text.rstrip(" ;,"), str(replaces_text or ""), self.message_list)
        card.decided.connect(self._on_memory_decided)
        card.settings_requested.connect(self.open_settings_requested.emit)
        self._memory_card = card
        follow = self.message_list.at_bottom()
        self._add(card)
        if follow:
            self.message_list.scroll_to_bottom()

    def _on_memory_decided(self, key: str, add: bool) -> None:
        if not add:
            self.drop_memory_proposal()
        self.memory_decided.emit(key, add)

    def confirm_memory(self, key: str) -> None:

        card = getattr(self, "_memory_card", None)
        if card is None or card.key != str(key or ""):
            return
        self._memory_card = None
        with contextlib.suppress(RuntimeError):
            card.confirm()

    def drop_memory_proposal(self) -> None:

        card = getattr(self, "_memory_card", None)
        self._memory_card = None
        if card is None:
            return
        with contextlib.suppress(RuntimeError):
            if card.decision is not True:
                self.message_list.remove_widget(card)





    def clear_draft(self) -> None:

        self.composer.clear()
        self.composer.clear_attachments()


        self.composer.forget_last_sent()

    def clear_thread(self) -> None:
        self._replay_generation += 1
        self.message_list.clear()
        self._replay_boundary = None

        self._restore_divider = None
        self._restore_hidden_rule = None
        self._runs = {}


        self._memory_card = None
        self._reopened_tools = {}
        self._status = None
        self._current_run = None
        self._run_requests = {}

        self.set_queue_thread(None)
        self._error_cards = {}
        self.composer.set_running(False)

        self.composer.clear_chips()
        self._changed_count = 0
        self._changed_layers = []
        self._run_changes = None
        self.set_history([])
        self._compaction_marked = False
        self._show_thread_surface()

    def load_thread(self, messages) -> None:








        self.clear_thread()

        self._replay_boundary = QWidget()
        self._replay_boundary.hide()
        self.message_list.add_widget(self._replay_boundary, animate=False)
        steps = replay_steps(self, messages)
        generation = self._replay_generation
        run_sliced(steps, REPLAY_SLICE_MS,
                   still_wanted=lambda: generation == self._replay_generation,
                   around=self._replay_quiet, on_done=self._replay_done)

    @contextlib.contextmanager
    def _replay_quiet(self):


        from ..core.background import gc_paused

        self.message_list.setUpdatesEnabled(False)

        status = self._status
        self._status = None
        try:
            with self.message_list.inserting_before(self._replay_boundary), gc_paused():
                yield
        finally:
            self._status = status
            self.message_list.setUpdatesEnabled(True)

    def _replay_done(self) -> None:

        self.message_list.remove_widget(self._replay_boundary)
        self._replay_boundary = None
        self._show_thread_surface()
        self._sync_answer_restores()
        self.message_list.scroll_to_bottom()
