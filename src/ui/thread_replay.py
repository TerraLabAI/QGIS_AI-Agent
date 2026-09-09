# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Replay a stored thread into the panel."""







from __future__ import annotations

from .bubbles import AgentBubble, UserBubble
from .cards import ErrorCard, PermissionCard, QuestionCard, RunSummaryCard, ToolCard
from .shared import tr



_NOTHING_CHANGED = "No layer, feature or file changed"


def _is_nothing_changed(line: str) -> bool:
    return line in (_NOTHING_CHANGED, tr(_NOTHING_CHANGED))


def replay(panel, messages) -> None:
    """Rebuild ``panel``'s thread from ``messages``, in order, in one go."""
    for step in replay_steps(panel, messages):
        step()


def replay_steps(panel, messages) -> list:
    """The replay as a list of small callables, in order: one per user turn, per tool call, per answer."""




    steps = []
    for m in messages or []:
        steps.extend(message_steps(panel, m))
    steps.append(lambda: finish_replay(panel))
    return steps


def message_steps(panel, m) -> list:
    """The steps of one stored message; [] for anything that is not one."""
    if not isinstance(m, dict):
        return []
    role = str(m.get("role") or "")
    run_id = str(m.get("run_id") or "")
    steps = []
    if run_id:
        steps.append(lambda: setattr(panel, "_last_run", run_id))
    if role == "agent":
        steps.extend(_agent_steps(panel, m, run_id))
    elif role in _SIMPLE_ROLES:
        steps.append(lambda: _replay_simple(panel, m, role, run_id))
    return steps


_SIMPLE_ROLES = ("user", "tool", "plan", "permission", "error", "summary")


def _replay_simple(panel, m: dict, role: str, run_id: str) -> None:
    if role == "user":
        panel._add_user(UserBubble(str(m.get("text") or ""), m.get("chips") or [],
                                   m.get("attachments") or []))
    elif role == "tool":
        _replay_tool(panel, run_id, m)
    elif role == "plan":
        panel._trace_for(run_id).set_plan(m.get("steps") or [])
    elif role == "permission":
        tool_id = str(m.get("tool_call_id") or "")
        card = PermissionCard(tool_id, str(m.get("sentence") or ""), m.get("args") or {})
        card.decided.connect(panel.permission_decided.emit)

        card.collapse(str(m.get("decision") or "deny"))
        if tool_id:
            panel.message_list.register_permission_card(tool_id, card)
        panel._add(card, animate=False)
    elif role == "error":
        card = ErrorCard(run_id, str(m.get("code") or ""), str(m.get("message") or ""),
                         bool(m.get("retryable")), str(m.get("details") or ""))
        card.retry_requested.connect(panel.retry_requested.emit)
        panel._add(card, animate=False)
    elif role == "summary":
        _replay_summary(panel, str(m.get("status") or "done"), str(m.get("summary") or ""),
                        m.get("usage"), m.get("verification"), has_text=True)


def finish_replay(panel) -> None:
    """A run that was still running when the chat was saved is shown cancelled."""
    for blocks in panel.message_list.trace_blocks.values():
        for trace in blocks:
            if trace.status == "running" and not trace.is_empty():
                for card in trace.tools:
                    card.mark_unfinished()
                trace.finish("cancelled", None)


def _agent_steps(panel, m: dict, run_id: str) -> list:
    """The plan, then one step per tool call, then the answer and its summary."""
    steps = []
    plan = m.get("plan") or []
    if plan:
        steps.append(lambda: panel._trace_for(run_id).set_plan(plan))
    for call in m.get("tool_calls") or []:
        if isinstance(call, dict):
            steps.append(lambda call=call: _replay_tool(panel, run_id, call))
    steps.append(lambda: _agent_answer(panel, m, run_id))
    return steps


def _agent_answer(panel, m: dict, run_id: str) -> None:
    text = str(m.get("text") or "").strip()
    status = str(m.get("status") or "")
    summary = str(m.get("summary") or "").strip()
    if not text and summary and status == "done":
        text, summary = summary, ""
    usage = m.get("usage") if isinstance(m.get("usage"), dict) else {}
    seconds = usage.get("duration_s", usage.get("elapsed_s"))


    for block in panel.message_list.traces_of(run_id) if run_id else ():
        if not block.is_empty():
            block.finish(status or "done", seconds)
    if text:
        bubble = AgentBubble(text)
        if run_id:
            panel._run(run_id).bubble = bubble
        panel._add(bubble, animate=False)
    if status and status != "running":
        _replay_summary(panel, status, "" if text else summary, usage, m.get("verification"),
                        has_text=bool(text))


def _replay_summary(panel, status: str, summary: str, usage, verification, has_text: bool) -> None:
    lines = RunSummaryCard._verification_lines(verification)
    if status == "done":
        lines = [line for line in lines if not _is_nothing_changed(line)]
    if has_text:
        summary = ""
    if summary or lines or status != "done":
        panel._add(RunSummaryCard(status, summary, usage, {"lines": lines} if lines else None),
                   animate=False)


def _replay_tool(panel, run_id: str, call: dict) -> None:
    tool_id = str(call.get("tool_call_id") or call.get("id") or "")
    if call.get("name") == "ask_user":
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        card = QuestionCard(tool_id, str(args.get("question") or call.get("sentence") or ""),
                            args.get("options") or [], True)
        card.collapse(str(call.get("summary") or "") if call.get("ok") else "")
        panel._add(card, animate=False)
        return
    card = ToolCard(tool_id, str(call.get("name") or ""), call.get("args") or {},
                    str(call.get("danger") or "read"), str(call.get("sentence") or ""))
    if call.get("ok") is not None:
        try:
            duration = float(call.get("duration_s") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        card.finish(bool(call.get("ok")), str(call.get("summary") or ""), duration,
                    str(call.get("detail") or ""))
    else:
        card.mark_unfinished()
    if tool_id:
        panel.message_list.register_tool_card(tool_id, card)
    panel._trace_for(run_id or "replay").add_tool(card)
