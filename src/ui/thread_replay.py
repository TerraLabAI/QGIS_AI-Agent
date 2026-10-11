# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later










from __future__ import annotations

from ..core.tool_registry import spec
from .bubbles import AgentBubble, UserBubble
from .cards import ErrorCard, PermissionCard, QuestionCard, RunSummaryCard, ToolCard
from .shared import tr



_NOTHING_CHANGED = "No layer, feature or file changed"


def _is_nothing_changed(line: str) -> bool:
    return line in (_NOTHING_CHANGED, tr(_NOTHING_CHANGED))


def replay(panel, messages) -> None:

    for step in replay_steps(panel, messages):
        step()


def replay_steps(panel, messages) -> list:





    steps = []
    owners = {}


    resendable = {str(m.get("run_id") or "") for m in messages or []
                  if isinstance(m, dict) and m.get("role") == "user" and str(m.get("text") or "").strip()}
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        run_id = str(m.get("run_id") or "")
        owner = owners.setdefault(run_id, panel._run(run_id))
        if run_id:

            panel._last_run = run_id
        else:

            owners.setdefault("replay", panel._run("replay"))
        for step in message_steps(panel, m, resendable):
            steps.append(lambda step=step, run_id=run_id, owner=owner:
                         step() if panel._runs.get(run_id) is owner else None)
    steps.append(lambda: finish_replay(panel, owners))
    return steps


def message_steps(panel, m, resendable=None) -> list:


    if not isinstance(m, dict):
        return []
    role = str(m.get("role") or "")
    run_id = str(m.get("run_id") or "")
    steps = []
    if role == "agent":
        steps.extend(_agent_steps(panel, m, run_id))
    elif role in _SIMPLE_ROLES:
        steps.append(lambda: _replay_simple(panel, m, role, run_id, resendable))
    return steps


_SIMPLE_ROLES = ("user", "tool", "plan", "permission", "error", "summary")


def _replay_simple(panel, m: dict, role: str, run_id: str, resendable=None) -> None:
    if role == "user":
        bubble = UserBubble(str(m.get("text") or ""), m.get("chips") or [], m.get("attachments") or [])
        bubble.run_id = run_id
        panel._add_user(bubble)
    elif role == "tool":
        _replay_tool(panel, run_id, m)
    elif role == "plan":
        panel._plan_card_for(run_id).set_steps(m.get("steps") or [])
    elif role == "permission":
        tool_id = str(m.get("tool_call_id") or "")
        card = PermissionCard(tool_id, str(m.get("sentence") or ""), m.get("args") or {})
        card.decided.connect(panel.permission_decided.emit)

        card.collapse(str(m.get("decision") or "deny"))
        if tool_id:
            panel.message_list.register_permission_card(tool_id, card)
        panel._add(card, animate=False)
    elif role == "error":
        retryable = bool(m.get("retryable")) and (resendable is None or run_id in resendable)
        card = ErrorCard(run_id, str(m.get("code") or ""), str(m.get("message") or ""),
                         retryable, str(m.get("details") or ""))
        card.retry_requested.connect(panel.retry_requested.emit)
        if retryable and run_id:
            card.undo_retry_requested.connect(panel.undo_retry_requested.emit)
            panel._error_cards[run_id] = card
        panel._add(card, animate=False)
    elif role == "summary":
        _replay_summary(panel, str(m.get("status") or "done"), str(m.get("summary") or ""),
                        m.get("usage"), m.get("verification"), has_text=True)


def finish_replay(panel, owners) -> None:



    for run_id, run in owners.items():
        if panel._runs.get(run_id) is not run:
            continue
        for trace in panel.message_list.traces_of(run_id):
            if trace.status == "running" and not trace.is_empty():
                for card in trace.tools:
                    card.mark_unfinished()
                trace.finish("interrupted", None, stored=True)
        if run.plan is not None and run.plan.status == "running":
            run.plan.finish("cancelled")


def _split(m: dict) -> tuple[str, str]:



    text = str(m.get("text") or "")
    try:
        start = int(m.get("answer_start") or 0)
    except (TypeError, ValueError):
        start = 0
    if not m.get("tool_calls") or not 0 < start < len(text):
        return "", text.strip()
    commentary, answer = text[:start].strip(), text[start:].strip()
    if not commentary or not answer:
        return "", text.strip()
    return commentary, answer


def _replay_commentary(panel, text: str, run_id: str) -> None:

    bubble = AgentBubble(text)
    bubble.run_id = run_id
    bubble.link_activated.connect(panel._on_bubble_link)
    panel._add(bubble, animate=False)


def _agent_steps(panel, m: dict, run_id: str) -> list:

    steps = []
    plan = m.get("plan") or []
    if plan:
        steps.append(lambda: panel._plan_card_for(run_id).set_steps(plan))
    commentary, _answer = _split(m)
    calls = m.get("tool_calls") or []



    asked = [i for i, c in enumerate(calls) if isinstance(c, dict)
             and getattr(spec(str(c.get("name") or "")), "waits_on_user", False)]
    after_call = asked[-1] if commentary and asked else -1
    if commentary and after_call < 0:
        steps.append(lambda: _replay_commentary(panel, commentary, run_id))


    steers = [s for s in m.get("steers") or [] if isinstance(s, dict) and str(s.get("text") or "").strip()]

    steps.append(lambda: setattr(panel, "_replay_round", None))
    for index, call in enumerate(calls):
        steps.extend(lambda s=s: _replay_steer(panel, s) for s in steers if _steer_after(s) == index)
        if isinstance(call, dict):
            steps.append(lambda call=call: _replay_tool(panel, run_id, call))
        if index == after_call:
            steps.append(lambda: _replay_commentary(panel, commentary, run_id))
    count = len(calls)
    steps.extend(lambda s=s: _replay_steer(panel, s) for s in steers if _steer_after(s) >= count)
    steps.append(lambda: _agent_answer(panel, m, run_id))
    return steps


def _steer_after(steer: dict) -> int:
    try:
        return max(0, int(steer.get("after") or 0))
    except (TypeError, ValueError):
        return 0


def _replay_steer(panel, steer: dict) -> None:
    panel._add(UserBubble(str(steer.get("text") or ""), [], []), animate=False)


def _agent_answer(panel, m: dict, run_id: str) -> None:
    text = _split(m)[1]
    status = str(m.get("status") or "")
    summary = str(m.get("summary") or "").strip()
    if not text and summary and status == "done":
        text, summary = summary, ""
    usage = m.get("usage") if isinstance(m.get("usage"), dict) else {}
    seconds = usage.get("duration_s", usage.get("elapsed_s"))

    blocks = panel.message_list.traces_of(run_id) if run_id else []
    for block in blocks:
        if not block.is_empty():
            block.finish(status or "done", seconds, stored=True)
    plan = panel._run(run_id).plan if run_id else None
    if plan is not None and status and status != "running":
        plan.finish(status)
    if text:
        bubble = AgentBubble(text)
        bubble.run_id = run_id

        bubble.link_activated.connect(panel._on_bubble_link)
        if run_id:
            panel._run(run_id).bubble = bubble

            panel._wire_answer(bubble, run_id)
            bubble.finish_streaming()

        sources = m.get("sources")
        if isinstance(sources, list):
            bubble.set_sources([x for x in sources[:50] if isinstance(x, dict)])
        panel._add(bubble, animate=False)


    changes = m.get("run_changes") if isinstance(m.get("run_changes"), dict) else None
    if status and status != "running":
        _replay_summary(panel, status, "" if text else summary, usage, m.get("verification"),
                        has_text=bool(text), with_changes=not changes,
                        has_block=any(not block.is_empty() for block in blocks))
        if not text and run_id and status != "done":


            bubble = panel.answer_row(run_id, animate=False)
            panel._run(run_id).bubble = bubble
            bubble.finish_streaming()
        if changes and run_id:
            panel.add_run_changes(run_id, changes, animate=False)

        panel._mark_compaction(usage, animate=False)


def _replay_summary(panel, status: str, summary: str, usage, verification, has_text: bool,
                    with_changes: bool = True, has_block: bool = False) -> None:


    lines = RunSummaryCard._verification_lines(verification, with_changes=with_changes)
    if status == "done":
        lines = [line for line in lines if not _is_nothing_changed(line)]
    if has_text:
        summary = ""
    if summary or lines or (status not in ("done", "cancelled") and not has_block):
        panel._add(RunSummaryCard(status, summary, usage, {"lines": lines} if lines else None),
                   animate=False)


def _replay_tool(panel, run_id: str, call: dict) -> None:
    tool_id = str(call.get("tool_call_id") or call.get("id") or "")
    if getattr(spec(str(call.get("name") or "")), "waits_on_user", False):
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        question = str(args.get("question") or call.get("sentence") or "")
        answer = str(call.get("summary") or "") if call.get("ok") else ""
        header = str(args.get("header") or "")


        last = getattr(panel, "_replay_round", None)
        if last is not None and last[0] == run_id:
            try:
                last[1].add_answered(question, answer, header)
                return
            except RuntimeError:
                pass
        card = QuestionCard(tool_id, question, args.get("options") or [], True, header=header)
        card.collapse(answer)
        panel._add(card, animate=False)
        panel._replay_round = (run_id, card)
        return
    panel._replay_round = None
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
