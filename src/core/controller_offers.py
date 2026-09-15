# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The cards a run leaves behind: proposals, offers, cleanup and memory notes."""



from __future__ import annotations

from qgis.PyQt.QtCore import QCoreApplication

from .controller_shared import PROPOSAL_MAX_ROWS
from .logger import log, log_warning
from .profile import add_memory_note
from .protocol import ClientErrorCode, Decision, RunStatus
from .scratch import MIN_TO_OFFER
from .scratch import group as group_scratch
from .scratch import remove as remove_scratch


def tr(text: str) -> str:
    return QCoreApplication.translate("AgentController", text)


class _ControllerOffers:


    def _on_proposal(self, call: dict) -> None:
        """propose_action and propose_edits never run a handler: the panel shows a card and the user's decision is the tool result, exactly like."""

        tool_call_id = str(call.get("tool_call_id") or "")
        run_id = str(call.get("run_id") or "")
        name = str(call.get("name") or "")
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        if tool_call_id in self._proposals:
            return
        slot = "ask_recommendation" if name == "propose_action" else "show_diff_table"
        if not hasattr(self._panel, slot):
            refused = {"code": ClientErrorCode.CANCELLED, "message": tr("This plugin build cannot show that card."),
                       "suggestion": "Ask the user with ask_user instead."}
            self._session.send_tool_error(tool_call_id, run_id, refused["code"], refused["message"],
                                          refused["suggestion"])
            self._remember_proposal_answer(tool_call_id, None, refused)
            self._close_call(tool_call_id)
            return
        self._proposals[tool_call_id] = call
        self._runs.wait_user(tool_call_id)
        self._pause_watchdog()

        self._session.send_permission_response(tool_call_id, run_id, Decision.PENDING)
        title = " ".join(str(args.get("title") or "").split())
        if self._thread_id:
            self._store.append_tool_call(self._thread_id, run_id, {
                "tool_call_id": tool_call_id, "name": name, "args": args,
                "danger": "read", "sentence": title, "ok": None})
        if name == "propose_action":
            raw = args.get("alternatives") if isinstance(args.get("alternatives"), list) else []
            alternatives = [str(a).strip() for a in raw if str(a or "").strip()][:3]
            confidence = args.get("confidence") if args.get("confidence") in ("high", "medium", "low") else "medium"
            self._panel_call("ask_recommendation", tool_call_id, run_id, title,
                             str(args.get("proposal") or ""), str(args.get("entity") or ""),
                             str(args.get("value") or ""), confidence, alternatives)
            return
        raw_columns = args.get("columns") if isinstance(args.get("columns"), list) else []
        columns = [str(c) for c in raw_columns]
        rows = []
        for row in (args.get("rows") if isinstance(args.get("rows"), list) else [])[:PROPOSAL_MAX_ROWS]:
            if not isinstance(row, dict):
                continue
            kind = row.get("kind") if row.get("kind") in ("added", "removed", "unchanged") else "unchanged"
            cells = row.get("cells") if isinstance(row.get("cells"), list) else []
            rows.append({"id": str(row.get("id") or ""), "kind": kind, "cells": [str(c) for c in cells]})
        self._panel_call("show_diff_table", run_id, title, columns, rows)

    def _answer_proposal(self, tool_call_id: str, result: dict | None, summary: str) -> None:
        call = self._proposals.pop(tool_call_id, None)
        if call is None:
            return
        run_id = str(call.get("run_id") or "")
        dismissed = None
        if result is None:
            dismissed = {"code": ClientErrorCode.CANCELLED, "message": tr("The user dismissed the proposal."),
                         "suggestion": "Stop here and wait for the next user message."}
            self._session.send_tool_error(tool_call_id, run_id, dismissed["code"], dismissed["message"],
                                          dismissed["suggestion"])
        else:
            self._session.send_tool_result(tool_call_id, run_id, result)
        self._remember_proposal_answer(tool_call_id, result, dismissed)
        self._close_call(tool_call_id)
        self._touch_watchdog()
        log(f"PROPOSAL {tool_call_id}: {summary[:80]}")
        if self._thread_id and run_id:
            self._store.append_tool_call(self._thread_id, run_id, {
                "tool_call_id": tool_call_id, "ok": result is not None, "summary": summary, "duration_s": 0.0})

    def _remember_proposal_answer(self, tool_call_id: str, result: dict | None, error: dict | None) -> None:
        """The card's answer in the executor's replay table, so a replay of the call answers it."""





        remember = getattr(self._executor, "remember_answer", None)
        if not callable(remember):
            return
        try:
            remember(tool_call_id, result, error)
        except Exception as exc:  # noqa: BLE001 - the card is answered whatever the table says
            log_warning(f"Proposal answer not kept for a replay: {exc}")

    def _on_recommendation_decided(self, tool_call_id: str, decision: str) -> None:
        """accept, alternative:<text> or dismiss, from the recommendation card."""
        decision = str(decision or "").strip()
        if decision == "accept":
            self._answer_proposal(tool_call_id, {"decision": "accept"}, "accept")
        elif decision.startswith("alternative"):
            text = decision.split(":", 1)[1].strip() if ":" in decision else ""
            self._answer_proposal(tool_call_id, {"decision": "alternative", "text": text}, f"alternative: {text}")
        else:
            self._answer_proposal(tool_call_id, None, "dismissed")

    def _on_diff_applied(self, run_id: str, ids) -> None:
        """The rows the user kept in the diff table; the open propose_edits of that run answers."""
        for tool_call_id, call in list(self._proposals.items()):
            if call.get("name") == "propose_edits" and str(call.get("run_id") or "") == str(run_id or ""):
                applied = [str(i) for i in (ids or []) if str(i).strip()] if isinstance(ids, (list, tuple)) else []
                self._answer_proposal(tool_call_id, {"applied": applied}, f"applied {len(applied)}")
                return

    def _cancel_proposals(self, run_id: str) -> None:
        for tool_call_id, call in list(self._proposals.items()):
            if str(call.get("run_id") or "") == run_id:
                self._answer_proposal(tool_call_id, None, "cancelled")



    def _offer_cleanup(self, run_id: str, status: str) -> None:
        """Offer back the layers this run made only to make another one."""









        if status != RunStatus.DONE:
            return
        try:
            items = list(self._executor.scratch.last_report or [])
        except Exception as exc:  # noqa: BLE001 - a ledger never breaks the end of a run
            log_warning(f"Cleanup offer skipped: {exc}")
            return
        if len(items) < MIN_TO_OFFER:
            return
        self._panel_optional("offer_cleanup", run_id, items)

    def _on_cleanup_decided(self, run_id: str, decision: str, ids) -> None:
        """Delete them, tidy them into one collapsed group, or keep them."""





        run_id, decision = str(run_id or ""), str(decision or "")
        ids = [str(i) for i in (ids or []) if str(i).strip()]
        sentence = ""
        if decision == "delete":
            gone = remove_scratch(ids)
            sentence = (tr("Deleted 1 working layer") if len(gone) == 1
                        else tr("Deleted {n} working layers").format(n=len(gone)))
        elif decision == "group":
            moved = group_scratch(ids, tr("Working layers"))
            sentence = (tr("Tidied 1 working layer away") if moved == 1
                        else tr("Tidied {n} working layers away").format(n=moved))
        else:
            sentence = (tr("Kept 1 working layer") if len(ids) == 1
                        else tr("Kept {n} working layers").format(n=len(ids)))
        log(f"Cleanup {decision} on {len(ids)} layers of run {run_id[:8]}")
        self._panel_optional("finish_cleanup", run_id, sentence)

    def _on_feedback(self, run_id: str, up: bool) -> None:
        """The thumbs under an answer: sent to the server, which keeps it on the run row."""
        run_id = str(run_id or "")
        if not run_id:
            return
        self._session.send_feedback(run_id, bool(up))
        log(f"Feedback {run_id[:8]}: {'up' if up else 'down'}")

    def _on_memory_note(self, text: str, kind: str, scope: str, run_id: str = "") -> None:
        """The note the finished run left behind: store it, then say so."""







        if not str(text or "").strip():
            return
        if not bool(getattr(self._settings, "memory_enabled", True)):
            return
        project = self._run_projects.get(str(run_id or ""))
        if project is None:



            log_warning(f"Memory note dropped: run {str(run_id)[:8] or '?'} is not one of ours")
            return
        try:
            note = add_memory_note(self._settings, text, "ai", kind, scope, project)
        except Exception as exc:  # noqa: BLE001 - a note is never worth breaking the panel
            log_warning(f"Memory note not stored: {exc}")
            return
        if note is not None:
            self._panel_call("note_memory", note["text"])
