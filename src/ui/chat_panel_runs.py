# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





from __future__ import annotations

import time

from qgis.PyQt.QtCore import QTimer, QUrl

from ..core.snapshot_report import run_change_items
from .bubbles import AgentBubble, StatusLine, UserBubble
from .cards import RunSummaryCard, ToolCard
from .chat_panel_shared import _is_nothing_changed, _Run
from .external_links import open_local_path
from .file_card import FileCardStack
from .file_links import gis_data_path, is_safe_to_open, path_from_url, reveal_target
from .layer_links import RunChangesRow, layer_id_from_url, layer_of_file, linkify_layers
from .plan_card import PlanCard
from .trace import RunTrace


class _ChatPanelRuns:

    def _run(self, run_id: str) -> _Run:
        run = self._runs.get(run_id)
        if run is None:
            run = _Run(run_id)
            self._runs[run_id] = run
        return run

    def _live_run(self, run_id: str) -> _Run | None:








        run_id = str(run_id or "")
        if not run_id or run_id != self._current_run:
            return None
        return self._runs.get(run_id)

    def _bubble_for(self, run_id: str) -> AgentBubble:




        run = self._run(run_id)
        last = self.message_list.last_widget(ignore=self._status)
        if run.bubble is None or last is not run.bubble:
            if run.trace is not None and last is run.trace:
                run.trace.close()
            run.bubble = AgentBubble()
            run.bubble.run_id = run_id
            run.bubble.link_activated.connect(self._on_bubble_link)
            self._add(run.bubble)
            if self._status is None and run_id == self._current_run:



                self._status = StatusLine(self.tr("Thinking"), started=run.started,
                                          reduced_motion=self._reduced_motion)
                self.message_list.add_widget(self._status)
        return run.bubble

    def answer_row(self, run_id: str, animate: bool = True) -> AgentBubble:


        bubble = AgentBubble()
        bubble.run_id = run_id
        bubble.link_activated.connect(self._on_bubble_link)
        bubble.set_wordless(True)
        self._wire_answer(bubble, run_id)
        self._add(bubble, animate=animate)
        return bubble

    def _trace_for(self, run_id: str) -> RunTrace:





        run = self._run(run_id)








        waiting = {id(card) for card in run.permissions}
        last = next((w for w in reversed(self.message_list.widgets())
                     if w is not self._status and id(w) not in waiting
                     and (w is run.trace or not w.isHidden())), None)
        if run.trace is None or last is not run.trace:
            self._drop_status()
            if run.trace is not None:
                run.trace.close()
            if run.bubble is not None and last is run.bubble:


                run.bubble.end_text()
            run.trace = RunTrace(run_id, started=time.monotonic(),
                                 reduced_motion=self._reduced_motion)
            self.message_list.register_trace(run_id, run.trace)
            self._add(run.trace)
            if not self._show_tool_details:
                run.trace.hide()
        return run.trace

    def _plan_card_for(self, run_id: str) -> PlanCard:






        run = self._run(run_id)
        if run.plan is None:
            run.plan = PlanCard(run_id, reduced_motion=self._reduced_motion)
            last = self.message_list.last_widget(ignore=self._status)
            if run.trace is not None and last is run.trace and run.trace.is_live():
                self.message_list.add_widget(run.plan, before=run.trace)
            else:
                self._add(run.plan)
        return run.plan

    def _drop_status(self) -> None:
        if self._status is not None:
            self.message_list.remove_widget(self._status)
            self._status = None





    def begin_run(self, run_id: str) -> None:
        run_id = str(run_id or "").strip()
        if not run_id:
            return
        self._run(run_id).started = time.monotonic()

        self.drop_memory_proposal()
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

        self._sync_answer_restores()
        self._show_thread_surface()
        self._sync_queue()

    def append_user_message(self, run_id: str, text: str, chips, attachments) -> None:
        if self._live_run(run_id) is None:
            return
        self._last_run = run_id or self._last_run
        self._run_requests[run_id] = str(text or "")
        bubble = UserBubble(text, list(chips or []), list(attachments or []))
        bubble.run_id = str(run_id or "")
        self._add_user(bubble)
        if run_id == self._current_run:
            self._sync_answer_restores()
        self._show_thread_surface()

        QTimer.singleShot(0, self.message_list.scroll_to_bottom)

    def drop_turn(self, run_id: str) -> None:



        run_id = str(run_id or "")
        widgets = [w for w in self.message_list.widgets() if w is not self._status]
        start = next((i for i, w in enumerate(widgets)
                      if run_id and isinstance(w, UserBubble) and w.run_id == run_id), None)
        if start is None:
            return

        end = next((i for i in range(start + 1, len(widgets))
                    if isinstance(widgets[i], UserBubble) and widgets[i].run_id not in ("", run_id)),
                   len(widgets))
        doomed = widgets[start:end]


        if start > 0 and widgets[start - 1].objectName() == "turnDivider":
            doomed.insert(0, widgets[start - 1])
            if end < len(widgets) and widgets[end - 1].objectName() == "turnDivider":
                doomed.remove(widgets[end - 1])
        for widget in doomed:
            self.message_list.remove_widget(widget)
        for registry in (self._runs, self._run_requests, self._error_cards, self._cleanup_cards):
            registry.pop(run_id, None)

    def reset_answer(self, run_id: str) -> None:






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
        if not text.strip() and (run.bubble is None or self.message_list.last_widget(
                ignore=self._status) is not run.bubble):




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
        if block is not None and block.is_live() and self._block_is_tail(block):
            block.set_activity(text)
            self._drop_status()
            return
        if self._status is None:

            self._status = StatusLine(text, started=self._run(run_id).started,
                                      reduced_motion=self._reduced_motion)
            self.message_list.add_widget(self._status)
        else:
            self._status.set_text(text)

    def set_link_notice(self, run_id: str, text: str) -> None:



        run = self._live_run(run_id)
        if run is None:
            return
        text = str(text or "").strip()[:300]
        held = getattr(self, "_link_prev", None)
        if text:
            if not held or held[0] != run_id:
                block = run.trace
                self._link_prev = (run_id, self._status.text() if self._status is not None
                                   else block.running_title() if block is not None else "")
            self.set_status_line(run_id, text)
        else:
            self._link_prev = None
            if held and held[0] == run_id and held[1]:
                self.set_status_line(run_id, held[1])
        hide = bool(text)
        for widget in (self._status, run.trace):
            if widget is not None:
                widget.hide_clock(hide)

    def _block_is_tail(self, block) -> bool:



        for w in reversed(self.message_list.widgets()):
            if w is block:
                return True
            if w is self._status or w.isHidden() or getattr(w, "decision", None) is not None:
                continue
            return False
        return False

    def set_plan(self, run_id: str, steps) -> None:
        if self._live_run(run_id) is None:
            return
        self._plan_card_for(run_id).set_steps(steps or [])

    def update_plan_step(self, run_id: str, step_id: str, state: str) -> None:
        run = self._live_run(run_id)
        if run is not None and run.plan is not None:
            run.plan.update_step(step_id, state)

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

        self._schedule_queue_after_run(status)
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
        if run.plan is not None:
            run.plan.finish(status)
        if status == "failed":



            for block in reversed(blocks):
                if block.reveal_failure() or block.ended_well():
                    break


        lines = RunSummaryCard._verification_lines(verification, with_changes=self._changed_count <= 0)
        if status == "done":
            lines = [line for line in lines if not _is_nothing_changed(line)]
        said = self._error_said.pop(run_id, "")





        repeat = bool(said) and (said == (summary or "").strip() or not (summary or lines))




        bare = not (summary or lines) and (status == "cancelled" or any(not block.is_empty() for block in blocks))
        if not repeat and not bare and (status != "done" or (self._explain_runs and (summary or lines))):
            self._add(RunSummaryCard(status, summary, usage, {"lines": lines} if lines else None,
                                     duration_s=seconds))
        last = self.message_list.last_widget(ignore=self._status)
        if run.bubble is not None and status != "done" and last is not run.bubble \
                and last in blocks:



            run.bubble.end_text()
            run.bubble = None
        if run.bubble is None and status != "done":




            run.bubble = self.answer_row(run_id)
        if run is not None and run.bubble is not None:
            run.bubble.finish_streaming()
        self._add_layer_links(run_id)
        self._mark_compaction(usage)
        self._drop_status()
        if self._current_run == run_id or self._current_run is None:
            self._current_run = None
            self.composer.set_running(False)

        self._sync_answer_restores()

    def _on_bubble_link(self, href: str) -> None:









        if href.startswith("source:"):

            return
        layer_id = layer_id_from_url(href)
        if layer_id:
            self.layer_action_requested.emit(layer_id, "show")
            return
        path = path_from_url(href)
        if not path:
            return
        layername = ""
        if "layername=" in href:
            layername = QUrl.fromPercentEncoding(
                href.split("layername=", 1)[1].split("&", 1)[0].encode("utf-8"))
            path = path.split("|", 1)[0]
        if self._open_in_qgis(path, layername):
            return
        if is_safe_to_open(path):
            open_local_path(path, self)
            return
        open_local_path(reveal_target(path), self)

    def _answer_bubble(self, run_id: str) -> AgentBubble | None:

        run = self._runs.get(run_id)
        if run is not None and run.bubble is not None:
            return run.bubble
        for widget in reversed(self.message_list.widgets()):
            if isinstance(widget, AgentBubble):
                return widget
        return None

    def _wire_answer(self, bubble: AgentBubble, run_id: str) -> None:

        if bubble.property("wired"):
            return
        bubble.setProperty("wired", True)
        bubble.restore_clicked.connect(lambda: self._on_answer_restore(run_id))

    def set_sources(self, run_id: str, items) -> None:


        if run_id not in self._runs:
            return
        bubble = self._answer_bubble(run_id)
        if bubble is None:
            return
        if self._runs[run_id].bubble is bubble:

            self._wire_answer(bubble, run_id)
        bubble.set_sources([x for x in list(items or [])[:50] if isinstance(x, dict)])

    def _add_layer_links(self, run_id: str = "") -> None:


        layers, self._changed_layers = self._changed_layers, []
        changes, self._run_changes = self._run_changes, None
        self.add_run_changes(run_id, changes if changes is not None else run_change_items(None, layers))

    def add_run_changes(self, run_id: str, changes, animate: bool = True) -> None:








        data = changes if isinstance(changes, dict) else {}
        stack = FileCardStack(data.get("files"))
        row = RunChangesRow(data)
        if row.is_empty() and stack.is_empty():
            row.deleteLater()
            stack.deleteLater()
            return
        run = self._runs.get(run_id)
        bubble = run.bubble if run is not None else None
        if row.layers():


            for said in self.message_list.widgets():
                if isinstance(said, AgentBubble) and (said is bubble or (run_id and said.run_id == run_id)):
                    linked = linkify_layers(said.text(), row.layers())
                    if linked != said.text():
                        said.set_linked_text(linked)
        row.layer_action_requested.connect(self.layer_action_requested.emit)
        stack.action_requested.connect(self._on_file_action)
        for widget, empty in ((stack, stack.is_empty()), (row, row.is_empty())):
            if empty:
                widget.deleteLater()
                continue
            if bubble is not None:

                bubble.set_changes(widget, animate=animate)
            else:
                self._add(widget, animate=animate)

    def _on_file_action(self, path: str, action: str) -> None:








        if not path:
            return
        if action == "add":
            self.file_add_requested.emit(path)
            return
        if action == "open" and self._open_in_qgis(path):
            return
        target = reveal_target(path) if action == "reveal" else path
        open_local_path(target, self)

    def _open_in_qgis(self, path: str, layername: str = "") -> bool:





        data = gis_data_path(path)
        if not data:
            return False
        layer_id = layer_of_file(data, layername)
        if layer_id:
            self.layer_action_requested.emit(layer_id, "show")
        else:


            self.file_add_requested.emit(f"{data}|layername={layername}" if layername else data)
        return True

    def set_run_changes(self, changed_layers: int, restore_available: bool,
                        layers: list | None = None, changes: dict | None = None) -> None:











        self._changed_count = int(changed_layers or 0)
        self._changed_layers = [x for x in (layers or []) if isinstance(x, dict)]
        self._run_changes = changes if isinstance(changes, dict) else None
        if restore_available:
            self.header.set_restore_available(True)
