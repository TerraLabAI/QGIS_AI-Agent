# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




from __future__ import annotations

from qgis.PyQt.QtCore import QCoreApplication

from .controller_shared import PROPOSAL_MAX_ROWS
from .logger import log, log_warning
from .profile import REPLY_LANGUAGES, add_memory_note, load_memory_notes, record_forgotten, remove_memory_note
from .protocol import ClientErrorCode, Decision, RunStatus
from .scratch import MIN_TO_OFFER
from .scratch import group as group_scratch


def tr(text: str) -> str:
    return QCoreApplication.translate("AgentController", text)






_CARD_SLOTS = {"propose_action": "ask_recommendation", "propose_edits": "show_diff_table"}
_DIFF_TABLE = "show_diff_table"


def _setting_label(setting: str) -> str:

    return {"reply_language": tr("reply language"), "reply_style": tr("response style"),
            "question_policy": tr("questions"), "expertise": tr("GIS experience"),
            "units": tr("units"), "layer_naming": tr("layer names")}.get(setting, "")


def _value_label(setting: str, value: str) -> str:

    if setting == "reply_language":
        return next((name for code, name in REPLY_LANGUAGES if code == value), value)
    return {"concise": tr("Concise"), "balanced": tr("Balanced"), "detailed": tr("Detailed"),
            "minimal": tr("Rarely"), "confirm": tr("Before changes"), "beginner": tr("Beginner"),
            "intermediate": tr("Intermediate"), "expert": tr("Expert"), "metric": tr("Metric"),
            "imperial": tr("Imperial"), "human": tr("Readable"), "snake_case": tr("snake_case")}.get(value, value)


def _has_change(rows) -> bool:

    return isinstance(rows, list) and any(
        isinstance(row, dict) and row.get("kind") in ("added", "removed") for row in rows[:PROPOSAL_MAX_ROWS])


class _ControllerOffers:


    def _on_proposal(self, call: dict) -> None:


        tool_call_id = str(call.get("tool_call_id") or "")
        run_id = str(call.get("run_id") or "")
        name = str(call.get("name") or "")
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        if tool_call_id in self._proposals:
            return
        slot = _CARD_SLOTS.get(name, _DIFF_TABLE)
        if not hasattr(self._panel, slot):
            refused = {"code": ClientErrorCode.CANCELLED, "message": tr("This plugin build cannot show that card."),
                       "suggestion": "ask_user reaches the user here."}
            self._session.send_tool_error(tool_call_id, run_id, refused["code"], refused["message"],
                                          refused["suggestion"])
            self._remember_proposal_answer(tool_call_id, None, refused)
            self._close_call(tool_call_id)
            return
        if _CARD_SLOTS.get(name) == _DIFF_TABLE and not _has_change(args.get("rows")):


            refused = {"code": ClientErrorCode.INVALID_ARGS,
                       "message": "propose_edits has no row to apply: every row is unchanged or has no kind.",
                       "suggestion": "A row applies with kind added for a new value (removed for a "
                                     "deletion), and cells as text, one per column."}
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
        if slot != _DIFF_TABLE:
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
                         "suggestion": "The user dismissed it."}
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






        remember = getattr(self._executor, "remember_answer", None)
        if not callable(remember):
            return
        try:
            remember(tool_call_id, result, error)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Proposal answer not kept for a replay: {exc}")

    def _on_recommendation_decided(self, tool_call_id: str, decision: str) -> None:

        decision = str(decision or "").strip()
        if decision == "accept":
            self._answer_proposal(tool_call_id, {"decision": "accept"}, "accept")
        elif decision.startswith("alternative"):
            text = decision.split(":", 1)[1].strip() if ":" in decision else ""
            self._answer_proposal(tool_call_id, {"decision": "alternative", "text": text}, f"alternative: {text}")
        else:
            self._answer_proposal(tool_call_id, None, "dismissed")

    def _on_diff_applied(self, run_id: str, ids) -> None:

        for tool_call_id, call in list(self._proposals.items()):
            if (_CARD_SLOTS.get(str(call.get("name") or "")) == _DIFF_TABLE
                    and str(call.get("run_id") or "") == str(run_id or "")):
                applied = [str(i) for i in (ids or []) if str(i).strip()] if isinstance(ids, (list, tuple)) else []
                self._answer_proposal(tool_call_id, {"applied": applied}, f"applied {len(applied)}")
                return

    def _cancel_proposals(self, run_id: str) -> None:
        for tool_call_id, call in list(self._proposals.items()):
            if str(call.get("run_id") or "") == run_id:
                self._answer_proposal(tool_call_id, None, "cancelled")



    def _offer_cleanup(self, run_id: str, status: str) -> None:













        if status != RunStatus.DONE:
            return
        try:
            items = list(self._executor.scratch.last_report or [])
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Working layers skipped: {exc}")
            return
        if len(items) < MIN_TO_OFFER:
            return
        ids = [str(i.get("layer_id")) for i in items if isinstance(i, dict) and i.get("layer_id")]
        moved = group_scratch(ids, tr("Working layers"))
        if moved:
            log(f"Grouped {moved} working layers of run {str(run_id or '')[:8]}")

    def _on_memory_note(self, text: str, kind: str, scope: str, run_id: str = "", replaces: str = "",
                        setting: str = "", value: str = "") -> None:







        if not str(text or "").strip():
            return
        if not bool(getattr(self._settings, "memory_enabled", True)):
            return
        run_id = str(run_id or "")
        project = self._run_projects.get(run_id)
        if project is None:



            log_warning(f"Memory note dropped: run {run_id[:8] or '?'} is not one of ours")
            return
        undo = getattr(self, "_memory_undo", None)
        if undo is None:
            undo = self._memory_undo = {}
        key = f"{run_id}:{len(undo)}"
        if _setting_label(setting):
            before = str(getattr(self._settings, setting, "") or "")
            try:
                setattr(self._settings, setting, value)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Setting from memory not applied: {exc}")
                return
            after = str(getattr(self._settings, setting, "") or "")
            if after == before:
                return
            undo[key] = {"setting": setting, "before": before}
            self._panel_optional("note_setting", _setting_label(setting), _value_label(setting, after), key)
            return
        old = None
        if replaces:
            old = next((n for n in load_memory_notes(self._settings) if n["id"] == replaces), None)
        try:
            note = add_memory_note(self._settings, text, "ai", kind, scope, project, replaces)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Memory note not stored: {exc}")
            return
        if note is None:
            return
        undo[key] = {"id": note["id"], "text": note["text"], "old": old}
        self._panel_call("note_memory", note["text"], key)

    def _on_memory_undo(self, key: str) -> None:



        entry = (getattr(self, "_memory_undo", None) or {}).pop(str(key or ""), None)
        if not entry:
            return
        try:
            if "setting" in entry:
                setattr(self._settings, entry["setting"], entry["before"])
                return
            old = entry.get("old")
            if old:
                add_memory_note(self._settings, old["text"], old.get("source") or "ai", old["kind"],
                                old["scope"], old.get("project") or "", entry["id"])
                record_forgotten(self._settings, entry["text"])
            else:
                remove_memory_note(self._settings, entry["id"])
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Memory undo failed: {exc}")
