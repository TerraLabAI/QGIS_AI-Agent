# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





from __future__ import annotations

import contextlib

from qgis.PyQt.QtCore import QEvent, Qt
from qgis.PyQt.QtGui import QKeyEvent, QKeySequence
from qgis.PyQt.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QShortcut, QTextEdit

from ..core.background import run_sliced
from .checkpoint_sheet import history_rows, row_target, usable
from .thread_replay import replay_steps
from .trace import RunFootnote


REPLAY_SLICE_MS = 24


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
        for keys, slot in (("Ctrl+Alt+Z", self.step_back), ("Ctrl+Alt+Shift+Z", self.step_forward),
                           ("Ctrl+Alt+H", self.open_checkpoints),
                           ("Esc", self._stop_if_running)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(context)
            shortcut.activated.connect(slot)
            if keys == "Esc":
                self._escape_shortcut = shortcut

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

    def set_history(self, entries: list) -> None:




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
            if row["live"]:
                tip = self.tr("Remove what this request changed on the map")
                points[row["run_id"]] = ("undo", row_target(row), tip, later)
                later += 1
            else:
                tip = self.tr("Bring back what this request changed")
                points[row["run_id"]] = ("redo", row_target(row), tip, 0)
        return points

    def _sync_answer_restores(self) -> None:


        running = self._current_run is not None
        try:
            self.header.checkpoint_sheet().set_running(
                running, self._run_requests.get(self._current_run or "", "") if running else "")
        except (AttributeError, RuntimeError):
            pass
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

    def show_restore_notice(self, text: str, ok: bool = True, action: str = "",
                            checkpoint_id: str = "") -> None:




        link = (action, "restore:" + checkpoint_id) if action and checkpoint_id else None
        if ok:
            self.composer.show_hint(text, sticky=link is not None, link=link)
        else:
            self.composer.show_warning(text, sticky=True, focus=False, link=link)

    def _on_notice_link(self, href: str) -> None:
        href = str(href or "")
        if href.startswith("restore:") and self._current_run is None:
            self.restore_requested.emit(href[len("restore:"):], False)

    def note_project_state(self, text: str) -> None:






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









        text = str(text or "").strip()
        if not text:
            return
        note = RunFootnote()
        note.setWordWrap(True)


        note.setText(self.tr("Added to memory: {0}").format(text.rstrip(" .!?;,")))
        note.show()
        self._add(note)
        self.message_list.scroll_to_bottom()





    def clear_draft(self) -> None:

        self.composer.clear()
        self.composer.clear_attachments()


        self.composer.forget_last_sent()

    def clear_thread(self) -> None:
        self._replay_generation += 1
        self.message_list.clear()
        self._runs = {}


        self._cleanup_cards.clear()
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
        steps = replay_steps(self, messages)
        generation = self._replay_generation
        run_sliced(steps, REPLAY_SLICE_MS,
                   still_wanted=lambda: generation == self._replay_generation,
                   around=self._replay_quiet, on_done=self._replay_done)

    @contextlib.contextmanager
    def _replay_quiet(self):


        from ..core.background import gc_paused

        self.message_list.setUpdatesEnabled(False)
        try:
            with gc_paused():
                yield
        finally:
            self.message_list.setUpdatesEnabled(True)

    def _replay_done(self) -> None:

        self._show_thread_surface()
        self._sync_answer_restores()
        self.message_list.scroll_to_bottom()
