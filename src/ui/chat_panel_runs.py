# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat panel's run lifecycle: the status line, the trace and the answer."""




from __future__ import annotations

import time

from qgis.PyQt.QtCore import QTimer

from ..core.snapshot_report import run_change_items
from .bubbles import AgentBubble, StatusLine, UserBubble
from .card_base import format_duration
from .cards import RunSummaryCard, ToolCard
from .chat_panel_shared import _is_nothing_changed, _Run
from .external_links import open_local_path
from .file_links import is_safe_to_open, path_from_url, reveal_local_file, reveal_target
from .layer_links import RunChangesRow, layer_id_from_url, linkify_layers
from .trace import RunFootnote, RunTrace


class _ChatPanelRuns:

    def _run(self, run_id: str) -> _Run:
        run = self._runs.get(run_id)
        if run is None:
            run = _Run(run_id)
            self._runs[run_id] = run
        return run

    def _live_run(self, run_id: str) -> _Run | None:
        """Return the run currently allowed to mutate the visible thread."""







        run_id = str(run_id or "")
        if not run_id or run_id != self._current_run:
            return None
        return self._runs.get(run_id)

    def _bubble_for(self, run_id: str) -> AgentBubble:
        """The bubble the run's text streams into: the one at the bottom of the list, else a new one."""



        run = self._run(run_id)
        last = self.message_list.last_widget(ignore=self._status)
        if run.bubble is None or last is not run.bubble:
            if run.trace is not None and last is run.trace:
                run.trace.close()
            run.bubble = AgentBubble()
            run.bubble.link_activated.connect(self._on_bubble_link)
            self._add(run.bubble)
        return run.bubble

    def _trace_for(self, run_id: str) -> RunTrace:
        """The activity block the run's next call or plan goes into: the one at the bottom of the list, else a new one under whatever the agent just."""




        run = self._run(run_id)
        last = self.message_list.last_widget(ignore=self._status)
        if run.trace is None or last is not run.trace:
            self._drop_status()
            if run.trace is not None:
                run.trace.close()
            run.trace = RunTrace(run_id, started=time.monotonic(),
                                 reduced_motion=self._reduced_motion)
            self.message_list.register_trace(run_id, run.trace)
            self._add(run.trace)
            if not self._show_tool_details:
                run.trace.hide()
        return run.trace

    def _plan_block(self, run_id: str):
        """The block of the run that holds its plan, or None."""
        for block in self.message_list.traces_of(run_id):
            if block.plan is not None:
                return block
        return None

    def _drop_status(self) -> None:
        if self._status is not None:
            self.message_list.remove_widget(self._status)
            self._status = None





    def begin_run(self, run_id: str) -> None:
        run_id = str(run_id or "").strip()
        if not run_id:
            return
        self._run(run_id).started = time.monotonic()
        self._current_run = run_id
        self._last_run = run_id
        self._changed_layers = []
        self._run_changes = None
        self._drop_status()
        self._status = StatusLine(self.tr("Thinking"), started=self._run(run_id).started,
                                  reduced_motion=self._reduced_motion)
        self.message_list.add_widget(self._status)
        self.composer.set_running(True)



        self._changed_count = 0
        self._show_thread_surface()

    def append_user_message(self, run_id: str, text: str, chips, attachments) -> None:
        if self._live_run(run_id) is None:
            return
        self._last_run = run_id or self._last_run
        self._add_user(UserBubble(text, list(chips or []), list(attachments or [])))
        self._show_thread_surface()

        QTimer.singleShot(0, self.message_list.scroll_to_bottom)

    def reset_answer(self, run_id: str) -> None:
        """Empty the bubble the run is streaming into, before a replayed answer."""





        run = self._live_run(run_id)
        if run is None or run.bubble is None:
            return
        if self.message_list.last_widget(ignore=self._status) is not run.bubble:




            return
        run.bubble.set_text("")
        run.segment_break = False

    def append_token(self, run_id: str, text: str) -> None:
        if not isinstance(text, str) or not text:
            return
        run = self._live_run(run_id)
        if run is None:
            return
        before = run.bubble
        bubble = self._bubble_for(run_id)
        if bubble is not before:



            self._wire_answer(bubble, run_id)
        if run.segment_break:
            run.segment_break = False
            current = bubble.text()
            if current and not current.endswith("\n"):

                text = "\n\n" + text.lstrip()
        bubble.append_token(text)

    def set_status_line(self, run_id: str, text: str) -> None:
        text = str(text or "").strip()[:300] or self.tr("Thinking...")


        run = self._live_run(run_id)
        if run is None:
            return
        block = run.trace if run is not None else None
        if (block is not None and block.is_live()
                and self.message_list.last_widget(ignore=self._status) is block):
            block.set_activity(text)
            return
        if self._status is None:

            self._status = StatusLine(text, started=self._run(run_id).started,
                                      reduced_motion=self._reduced_motion)
            self.message_list.add_widget(self._status)
        else:
            self._status.set_text(text)

    def set_plan(self, run_id: str, steps) -> None:
        if self._live_run(run_id) is None:
            return


        block = self._plan_block(run_id) or self._trace_for(run_id)
        block.set_plan(list(steps or []))

    def update_plan_step(self, run_id: str, step_id: str, state: str) -> None:
        if self._live_run(run_id) is None:
            return
        block = self._plan_block(run_id)
        if block is not None:
            block.plan.update_step(step_id, state)

    def add_tool_call(self, tool_call_id: str, run_id: str, name: str, args,
                      danger: str, sentence: str) -> None:
        run = self._live_run(run_id)
        if run is None:
            return
        run.segment_break = True
        args = dict(args) if isinstance(args, dict) else {}
        tool_call_id = str(tool_call_id or "")[:256]
        name = str(name or "")[:200]
        sentence = str(sentence or "")[:1000]
        trace = self._trace_for(run_id)
        last = trace.last_tool()
        if last is not None and last.same_call(name, args) and last.can_fold():



            last.add_repeat(tool_call_id)
            self.message_list.register_tool_card(tool_call_id, last)
            run.tools.append(last)
            return
        card = ToolCard(tool_call_id, name, args, danger, sentence,
                        animate=not self._reduced_motion())
        self.message_list.register_tool_card(tool_call_id, card)
        run.tools.append(card)
        trace.add_tool(card)

    def finish_tool_call(self, tool_call_id: str, ok: bool, summary: str,
                         duration_s: float, detail: str = "", result=None) -> None:
        """``result`` is the dict the tool handed back, or None."""











        try:
            duration_s = max(0.0, float(duration_s or 0.0))
        except (TypeError, ValueError, OverflowError):
            duration_s = 0.0
        card = self.message_list.tool_card(str(tool_call_id or ""))
        if card is not None:
            card.finish(bool(ok), str(summary or "")[:1000], duration_s, str(detail or "")[:2000])

    def end_run(self, run_id: str, status: str, summary: str, usage, verification) -> None:
        run = self._live_run(run_id)
        if run is None:
            return
        if run is not None and run.bubble is not None:
            run.bubble.flush()
        usage = usage if isinstance(usage, dict) else {}
        seconds = usage.get("duration_s", usage.get("elapsed_s"))
        if seconds is None and run is not None:
            seconds = time.monotonic() - run.started
        try:
            seconds = max(0.0, float(seconds)) if seconds is not None else None
        except (TypeError, ValueError, OverflowError):
            seconds = time.monotonic() - run.started if run is not None else 0.0
        if run is not None and seconds is not None:
            seconds = max(0.0, float(seconds) - run.waited)
        blocks = self.message_list.traces_of(run_id)
        streamed = run.bubble.text().strip() if run is not None and run.bubble is not None else ""
        summary = (summary or "").strip()
        if streamed:

            summary = ""
        elif summary and status == "done":

            bubble = self._bubble_for(run_id)
            self._wire_answer(bubble, run_id)
            bubble.set_text(summary)
            summary = ""


        reason = "" if status == "done" else "stopped"
        for block in blocks:
            for card in block.tools:
                if card.ok is None and not getattr(card, "ended", ""):
                    card.mark_unfinished(reason)
        for block in blocks:


            if not block.is_empty():
                block.finish(status, seconds)
        if status == "failed":

            for block in reversed(blocks):
                if block.reveal_failure():
                    break


        lines = RunSummaryCard._verification_lines(verification, with_changes=self._changed_count <= 0)
        if status == "done":
            lines = [line for line in lines if not _is_nothing_changed(line)]
        said = self._error_said.pop(run_id, "")





        repeat = bool(said) and (said == (summary or "").strip() or not (summary or lines))
        if not repeat and (status != "done" or (self._explain_runs and (summary or lines))):
            self._add(RunSummaryCard(status, summary, usage, {"lines": lines} if lines else None,
                                     duration_s=seconds))
        if run is not None and run.bubble is not None:
            run.bubble.finish_streaming()
        self._add_layer_links(run_id)
        footnote = self._run_footnote(blocks, usage, seconds)
        if footnote:
            note = RunFootnote()
            note.setText(footnote)
            note.show()
            self._add(note)
        self._nudge_long_chat(usage)
        self._drop_status()
        if self._current_run == run_id or self._current_run is None:
            self._current_run = None
            self.composer.set_running(False)

    def _on_bubble_link(self, href: str) -> None:
        """A link inside an answer: a layer opens in QGIS, a data or document file the run wrote opens with the desktop's own handler, and anything."""








        if href.startswith("source:"):

            return
        layer_id = layer_id_from_url(href)
        if layer_id:
            self.layer_action_requested.emit(layer_id, "show")
            return
        path = path_from_url(href)
        if not path:
            return
        if is_safe_to_open(path):
            open_local_path(path, self)
            return
        open_local_path(reveal_target(path), self)

    def _answer_bubble(self, run_id: str) -> AgentBubble | None:
        """The run's last bubble, for what arrives after the answer."""
        run = self._runs.get(run_id)
        if run is not None and run.bubble is not None:
            return run.bubble
        for widget in reversed(self.message_list.widgets()):
            if isinstance(widget, AgentBubble):
                return widget
        return None

    def _wire_answer(self, bubble: AgentBubble, run_id: str) -> None:
        """The action row's signals, with the run they belong to. Once per bubble."""
        if bubble.property("wired"):
            return
        bubble.setProperty("wired", True)
        bubble.feedback.connect(lambda up: self.feedback.emit(run_id, bool(up)))

    def set_sources(self, run_id: str, items) -> None:
        """``[{name, url, glyph?}]``: the stacked marks and ``N sources`` on the answer's action row, with the popover that lists them."""

        if run_id not in self._runs:
            return
        bubble = self._answer_bubble(run_id)
        if bubble is None:
            return
        if self._runs[run_id].bubble is bubble:

            self._wire_answer(bubble, run_id)
        bubble.set_sources([x for x in list(items or [])[:50] if isinstance(x, dict)])

    def _add_layer_links(self, run_id: str = "") -> None:
        """What the run changed, under its answer: the chips the controller built from the run report at run end, else the layers the diff saw while."""

        layers, self._changed_layers = self._changed_layers, []
        changes, self._run_changes = self._run_changes, None
        self.add_run_changes(run_id, changes if changes is not None else run_change_items(None, layers))

    def add_run_changes(self, run_id: str, changes, animate: bool = True) -> None:
        """``changes`` from core/snapshot_report.run_change_items as chips under the run's answer, and its layer names as links in the answer's prose."""


        row = RunChangesRow(changes)
        if row.is_empty():
            row.deleteLater()
            return
        run = self._runs.get(run_id)
        bubble = run.bubble if run is not None else None
        if bubble is not None and row.layers():
            linked = linkify_layers(bubble.text(), row.layers())
            if linked != bubble.text():
                bubble.set_text(linked)
        row.layer_action_requested.connect(self.layer_action_requested.emit)
        row.file_reveal_requested.connect(self._on_reveal_file)
        self._add(row, animate=animate)

    def _on_reveal_file(self, path: str) -> None:
        """A file chip: the file shown in the system file browser, never opened."""
        reveal_local_file(path, self)

    def _run_footnote(self, blocks, usage: dict, seconds) -> str:
        """``4 steps · 12 s · 37 runs left``: what the run took, in one muted line under its answer."""


        parts = []
        try:
            steps = int(usage.get("steps")) if usage.get("steps") is not None else None
        except (TypeError, ValueError):
            steps = None
        if steps is None:
            steps = sum(block.step_count() for block in blocks)
        if steps > 0:
            parts.append(self.tr("%n steps", "", steps) if steps != 1 else self.tr("1 step"))
        if seconds is not None:
            parts.append(format_duration(seconds))
        used, limit = self._usage[0], self._usage[1]
        if self._signed_in and limit > 0:
            left = max(0, limit - used)
            parts.append(self.tr("%n runs left", "", left) if left != 1 else self.tr("1 run left"))
        return " · ".join(part for part in parts if part)

    def set_run_changes(self, changed_layers: int, restore_available: bool,
                        layers: list | None = None, changes: dict | None = None) -> None:
        """What the run in progress has changed so far."""










        self._changed_count = int(changed_layers or 0)
        self._changed_layers = [x for x in (layers or []) if isinstance(x, dict)]
        self._run_changes = changes if isinstance(changes, dict) else None
        if restore_available:
            self.header.set_restore_available(True)
