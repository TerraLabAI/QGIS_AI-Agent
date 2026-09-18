# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat panel's threads: switching, the history keys and a stored replay."""




from __future__ import annotations

import contextlib

from qgis.PyQt.QtCore import QEvent, Qt
from qgis.PyQt.QtGui import QKeyEvent, QKeySequence
from qgis.PyQt.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QShortcut, QTextEdit

from ..core.background import run_sliced
from .checkpoint_sheet import point_name, visible_entries
from .thread_replay import replay_steps
from .trace import RunFootnote


REPLAY_SLICE_MS = 24


class _ChatPanelThreads:





    def _show_thread_surface(self) -> None:
        """Sign-in card while signed out; else the thread or the empty state."""
        signed_in = self._signed_in


        gated = self.update_gate.isVisibleTo(self)
        self.activation.setVisible(not signed_in and not gated)
        empty = self.message_list.is_empty()
        self.message_list.setVisible(signed_in and not empty and not gated)
        self.empty_state.setVisible(signed_in and empty and not gated)
        self._empty_rows.setVisible(signed_in and empty and not gated)
        self._composer_host.setVisible(signed_in and not gated)
        self.notice_bar.setVisible(not gated and bool(self.notice_bar.notice_id()))
        if gated:
            self._quota_host.hide()
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
            sidebar.home_requested.connect(self.new_thread_requested.emit)
            sidebar.search_requested.connect(lambda: self.header.history_menu().show_over(self))
            sidebar.thread_selected.connect(self.thread_selected.emit)
            sidebar.upgrade_requested.connect(self._on_upgrade)
            self.header.set_sidebar(sidebar)
        sidebar.sync_visible(signed_in)





        self.composer.set_placeholder(self.tr("Give the AI agent a task in QGIS..."))
        self.composer.set_empty_chat(empty)
        self._apply_quota()

        centred = signed_in and empty
        self.empty_state.set_centred(centred)
        self._centre_composer(centred, animate=signed_in)

    def _wire_history_keys(self) -> None:
        """Panel-scoped keys, never QGIS's Ctrl+Z (that one is the active layer's edit buffer): back, forward, the History sheet, and Escape to stop."""


        context = Qt.ShortcutContext.WidgetWithChildrenShortcut
        for keys, slot in (("Ctrl+Alt+Z", self.step_back), ("Ctrl+Alt+Shift+Z", self.step_forward),
                           ("Ctrl+Alt+H", self.open_checkpoints),
                           ("Esc", self._stop_if_running)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(context)
            shortcut.activated.connect(slot)
            if keys == "Esc":
                self._escape_shortcut = shortcut

    def _stop_if_running(self) -> None:
        """Escape stops the run only when it is not meant for something else."""


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
        """Deliver this Escape to the open popover, so its own handling runs."""








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
        except Exception:  # noqa: BLE001 - a shortcut slot never raises; closing is the fallback
            popup.close()
        finally:
            if shortcut is not None:
                shortcut.setEnabled(True)

    def _history_neighbour(self, step: int) -> str:
        """The id of the available entry ``step`` away from the current one."""




        entries = visible_entries(self._history)
        at = next((i for i, e in enumerate(entries) if e.get("current")), None)
        if at is None:
            at = len(entries) if step < 0 else -1
        candidates = entries[:at][::-1] if step < 0 else entries[at + 1:]
        for entry in candidates:
            if entry.get("available", True):
                return str(entry.get("id") or "")
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

    def set_history(self, entries: list) -> None:
        """The checkpoints of this chat, oldest first (core/checkpoints.describe)."""



        all_entries = [e for e in list(entries or []) if isinstance(e, dict)]


        self._history = all_entries[-200:]
        self.header.set_checkpoints(self._history)
        self._sync_answer_restores()

    def _answer_points(self) -> dict:
        """run id -> (mode, checkpoint id, tooltip) for each answer's undo glyph."""






        shown = visible_entries(self._history)
        now = next((i for i, e in enumerate(shown) if e.get("current")), None)

        order = {str(e.get("id") or ""): i for i, e in enumerate(shown)}
        points: dict = {}
        for entry in self._history:
            if not isinstance(entry, dict) or entry.get("kind") != "after":
                continue
            run_id = str(entry.get("run_id") or "")
            before = next((e for e in self._history if isinstance(e, dict) and e.get("kind") == "before"
                           and str(e.get("run_id") or "") == run_id), None)
            if not run_id or before is None or now is None:
                continue
            usable = [e for e in (entry, before) if e.get("available", True) and not e.get("other_project")]
            if len(usable) < 2:
                continue
            after_at = order.get(str(entry.get("id") or ""))
            if after_at is None:
                continue
            if now >= after_at:
                points[run_id] = ("undo", str(before.get("id") or ""),
                                  self.tr("Undo: back to {point}").format(point=point_name(before, self.tr)))
            else:
                points[run_id] = ("redo", str(entry.get("id") or ""),
                                  self.tr("Redo: forward to {point}").format(point=point_name(entry, self.tr)))
        return points

    def _sync_answer_restores(self) -> None:
        """Every answer's undo glyph matches where the project stands now."""
        points = self._answer_points() if self._current_run is None else {}
        for run_id, run in list(self._runs.items()):
            bubble = getattr(run, "bubble", None)
            if bubble is None or not bubble.is_finished():
                continue
            mode, _cid, tip = points.get(run_id, ("", "", ""))
            try:
                bubble.set_restore(mode, tip)
            except RuntimeError:
                continue

    def _on_answer_restore(self, run_id: str) -> None:
        if self._current_run is not None:
            return
        point = self._answer_points().get(run_id)
        if point and point[1]:
            self.restore_requested.emit(point[1], False)

    def note_project_state(self, text: str) -> None:
        """Where the project stands after a restore ("Back to before run 2")."""





        text = str(text or "").strip()
        if not text:
            return
        note = RunFootnote()
        note.setWordWrap(True)
        note.setText(text)
        note.show()
        self._add(note)
        self.message_list.scroll_to_bottom()

    def note_memory(self, text: str) -> None:
        """What the run just added to the memory, said where it happened."""








        text = str(text or "").strip()
        if not text:
            return
        note = RunFootnote()
        note.setWordWrap(True)


        note.setText(self.tr("Noted for later: {0}. You can remove it in Settings > Memory.")
                     .format(text.rstrip(" .!?;,")))
        note.show()
        self._add(note)
        self.message_list.scroll_to_bottom()

    def note_run_blocked(self, run_id: str, text: str) -> None:
        """QGIS froze during the run: one muted line in its activity block, once per run, so the user sees why the next edit may be refused."""

        run = self._live_run(str(run_id or ""))
        text = str(text or "").strip()
        if run is None or not text or run.blocked_noted:
            return
        run.blocked_noted = True
        block = run.trace if run.trace is not None else self._trace_for(run.run_id)
        block.add_note(text)





    def clear_draft(self) -> None:
        """The unsent message and its attachments: an account that leaves takes them with it."""
        self.composer.clear()
        self.composer.clear_attachments()


        self.composer._last_sent = ""

    def clear_thread(self) -> None:
        self._replay_generation += 1
        self.message_list.clear()
        self._runs = {}


        self._cleanup_cards.clear()
        self._reopened_tools = {}
        self._status = None
        self._current_run = None
        self.composer.set_running(False)

        self.composer.clear_chips()
        self._changed_count = 0
        self._changed_layers = []
        self._run_changes = None
        self.set_history([])
        self._compaction_marked = False
        self._show_thread_surface()

    def load_thread(self, messages) -> None:
        """Replay a stored thread (shape in core/threads.py; see thread_replay)."""







        self.clear_thread()
        steps = replay_steps(self, messages)
        generation = self._replay_generation
        run_sliced(steps, REPLAY_SLICE_MS,
                   still_wanted=lambda: generation == self._replay_generation,
                   around=self._replay_quiet, on_done=self._replay_done)

    @contextlib.contextmanager
    def _replay_quiet(self):
        """One paint per slice, not one per widget, no full garbage collection in the middle of a hundred cards, and the bottom in view after it."""

        from ..core.background import gc_paused

        self.message_list.setUpdatesEnabled(False)
        try:
            with gc_paused():
                yield
        finally:
            self.message_list.setUpdatesEnabled(True)

    def _replay_done(self) -> None:
        """The end of a replay: the thread surface, and the bottom in view."""
        self._show_thread_surface()
        self._sync_answer_restores()
        self.message_list.scroll_to_bottom()
