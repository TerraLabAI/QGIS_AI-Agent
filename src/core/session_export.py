# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The whole session as one object, and as one page somebody can paste."""




























from __future__ import annotations

import json
import platform
import sys
import time
from datetime import datetime, timezone

from .log_scrub import scrub_result, scrub_secrets, scrub_user_paths

SCHEMA = "terralab.agent.session/1"



RESULT_CHARS = 6000
HEAD_SHARE = 0.6




BLOB_CHARS = 800


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clean(value):
    """A username, a key or a signed URL never travels with a report."""
    from ..ui.transcript import redact_signed_urls

    if isinstance(value, str):
        return redact_signed_urls(scrub_secrets(scrub_user_paths(value)))
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def build(*, thread=None, identity=None, session=None, account=None, policy=None,
          logs="", error="", run_id="", verification=None) -> dict:
    """Everything this session knows about itself, in one object."""






    identity = dict(identity or {})
    export = {
        "schema": SCHEMA,
        "exported_at": _now_iso(),
        "run_id": str(run_id or ""),
        "plugin": {
            "version": identity.get("plugin_version", ""),
            "qgis": identity.get("qgis_version", ""),
            "os": identity.get("os", ""),
            "arch": platform.machine(),
            "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "locale": identity.get("locale", ""),
        },
        "session": dict(session or {}),
        "account": dict(account or {}),
        "policy": policy if policy is not None else {},
        "error": str(error or ""),
        "runs": [],
        "logs": [line for line in str(logs or "").splitlines() if line.strip()],
    }
    if thread:
        export["thread"] = {
            "id": thread.get("id", ""),
            "title": thread.get("title", ""),
            "created_at": thread.get("created_at", ""),
            "updated_at": thread.get("updated_at", ""),
        }
        export["runs"] = _runs_of(thread.get("messages") or [], verification)
    export["counts"] = {
        "runs": len(export["runs"]),
        "tool_calls": sum(len(r.get("tool_calls") or []) for r in export["runs"]),
        "failed_tool_calls": sum(
            1 for r in export["runs"] for c in (r.get("tool_calls") or []) if c.get("ok") is False),
    }
    return _clean(scrub_result(export))


def _runs_of(messages, verification=None) -> list:
    """The stored thread, regrouped by run."""






    runs: list = []
    index: dict = {}

    def run_for(run_id: str) -> dict:
        key = run_id or f"_{len(runs)}"
        if key not in index:
            index[key] = {"run_id": run_id, "user": None, "answer": "", "plan": [],
                          "tool_calls": [], "status": "", "summary": "", "usage": {},
                          "verification": None}
            runs.append(index[key])
        return index[key]

    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        run = run_for(str(message.get("run_id") or ""))
        if role == "user":
            run["user"] = {"text": message.get("text", ""), "ts": message.get("ts", ""),
                           "chips": message.get("chips") or [],
                           "attachments": message.get("attachments") or []}
        elif role == "agent":
            run["answer"] = message.get("text", "")
            run["answer_start"] = message.get("answer_start")
            run["plan"] = message.get("plan") or []
            run["status"] = message.get("status", "")
            run["summary"] = message.get("summary", "")
            run["usage"] = message.get("usage") or {}
            run["verification"] = message.get("verification")
            run["ts"] = message.get("ts", "")
            for call in message.get("tool_calls") or []:
                if isinstance(call, dict):
                    run["tool_calls"].append(_call_of(call))
    if runs and verification and not runs[-1].get("verification"):



        runs[-1]["verification"] = verification
    return runs


def _call_of(call: dict) -> dict:
    """One tool call, named the way a GenAI trace names one."""
    out = {
        "tool.call.id": call.get("tool_call_id", ""),
        "tool.name": call.get("name", ""),
        "danger": call.get("danger", ""),
        "sentence": call.get("sentence", ""),
        "arguments": call.get("args"),
        "ok": call.get("ok"),
        "summary": call.get("summary", ""),
        "duration_s": call.get("duration_s"),
    }



    if "result" in call:
        out["result"] = call.get("result")
    if "error" in call:
        out["error"] = call.get("error")
    return out





def _cut(text: str, limit: int = RESULT_CHARS) -> str:
    """Head and tail, with the count of what went, the way the wire copy is cut."""
    text = text or ""
    if len(text) <= limit:
        return text
    head = int(limit * HEAD_SHARE)
    tail = limit - head
    return f"{text[:head]}\n... {len(text) - limit} characters cut ...\n{text[-tail:]}"


def _elide_blobs(value, limit: int = BLOB_CHARS):
    """A long opaque string becomes its own length, everything else stays."""



    if isinstance(value, str):
        return value if len(value) <= limit else f"<{len(value)} characters, not shown>"
    if isinstance(value, dict):
        return {k: _elide_blobs(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [_elide_blobs(v, limit) for v in value]
    return value


def _json(value, limit: int = RESULT_CHARS) -> str:
    value = _elide_blobs(value)
    try:
        text = json.dumps(value, ensure_ascii=False, indent=1, default=str, sort_keys=True)
    except (TypeError, ValueError, RecursionError):
        text = str(value)
    return _cut(text, limit)


def _fence(text: str, language: str = "json") -> str:
    from ..ui.transcript import fence

    return fence(text, language)


def render_markdown(export: dict) -> str:
    """The page. Ordered, numbered, and readable without the plugin open."""
    plugin = export.get("plugin") or {}
    session = export.get("session") or {}
    thread = export.get("thread") or {}
    counts = export.get("counts") or {}
    lines = ["# AI Agent session report", ""]
    lines += [
        f"Exported {export.get('exported_at', '')} - schema `{export.get('schema', '')}`", "",
        "## Environment", "",
        f"- Plugin {plugin.get('version') or 'unknown'}, QGIS {plugin.get('qgis') or 'unknown'}, "
        f"{plugin.get('os') or 'unknown'} {plugin.get('arch') or ''}, Python {plugin.get('python') or ''}",
        f"- Interface language: {plugin.get('locale') or 'unknown'}",
    ]
    if session:
        lines.append(
            f"- Session {session.get('session_id') or 'none'}, state {session.get('state') or 'unknown'}, "
            f"model {session.get('model') or 'unknown'}, effort {session.get('effort') or 'unknown'}, "
            f"mode {session.get('mode') or 'unknown'}, approval {session.get('approval') or 'unknown'}")
        if session.get("tool_count"):
            lines.append(f"- Tools offered: {session.get('tool_count')} "
                         f"(manifest {session.get('manifest_hash') or 'unknown'})")
    account = export.get("account") or {}
    if account:
        row = f"- Account {account.get('state') or 'unknown'}"
        if account.get("plan"):
            row += f", plan {account['plan']}"
        row += f", device {account.get('device_hash') or 'unknown'}"
        lines.append(row)
    if thread:
        lines.append(f"- Thread `{thread.get('id') or ''}` \"{thread.get('title') or ''}\", "
                     f"{counts.get('runs', 0)} runs, {counts.get('tool_calls', 0)} tool calls, "
                     f"{counts.get('failed_tool_calls', 0)} of them failed")
    if export.get("run_id"):
        lines.append(f"- Reported run: `{export['run_id']}`")
    lines.append("")
    if export.get("error"):
        lines += ["## The error that was on screen", "", _fence(_cut(export["error"], 4000), ""), ""]
    if export.get("policy"):
        lines += ["## Server policy in force", "", _fence(_json(export["policy"], 4000)), ""]

    runs = export.get("runs") or []
    for number, run in enumerate(runs, 1):
        lines += _render_run(number, len(runs), run)

    logs = export.get("logs") or []
    if logs:
        lines += ["## Recent log lines", "",
                  _fence(_cut("\n".join(logs), 20000), ""), ""]
    return "\n".join(lines).rstrip() + "\n"


def _render_run(number: int, total: int, run: dict) -> list:
    head = f"## Run {number} of {total}"
    if run.get("run_id"):
        head += f" - `{run['run_id']}`"
    if run.get("status"):
        head += f" - {run['status']}"
    lines = [head, ""]
    user = run.get("user") or {}
    if user.get("text"):
        lines += ["**What the user asked**", "", _fence(_cut(user["text"], 8000), ""), ""]
    if user.get("chips"):
        lines.append(f"Pinned with @: {', '.join(str(c) for c in user['chips'])}")
        lines.append("")
    if user.get("attachments"):
        names = ", ".join(str(a.get("name") if isinstance(a, dict) else a) for a in user["attachments"])
        lines += [f"Attachments: {names}", ""]
    if run.get("plan"):
        lines.append("**Plan**")
        lines.append("")
        for step in run["plan"]:
            if isinstance(step, dict):
                lines.append(f"- [{step.get('state', '')}] {step.get('label', '')}")
        lines.append("")
    calls = run.get("tool_calls") or []
    if calls:
        lines += [f"**Tool calls ({len(calls)})**", ""]
        for index, call in enumerate(calls, 1):
            lines += _render_call(index, call)
    if run.get("answer"):
        lines += ["**What the agent answered**", "", _cut(run["answer"], 12000), ""]
    usage = run.get("usage") or {}
    if usage:
        lines += ["**Usage**", "", _fence(_json(usage, 2000)), ""]
    verification = run.get("verification")
    if verification:
        lines += ["**What changed in the project**", "", _fence(_json(verification, 6000)), ""]
    if run.get("summary"):
        lines += [f"**Summary**: {run['summary']}", ""]
    return lines


def _render_call(index: int, call: dict) -> list:
    ok = call.get("ok")
    verdict = "ok" if ok else ("FAILED" if ok is False else "no answer recorded")
    duration = call.get("duration_s")
    timing = f", {duration} s" if duration is not None else ""
    lines = [f"### {index}. `{call.get('tool.name', '')}` ({call.get('danger', '')}{timing}) - {verdict}", ""]
    if call.get("summary"):
        lines += [call["summary"], ""]
    if call.get("arguments") is not None:
        lines += ["Arguments:", "", _fence(_json(call["arguments"], 3000)), ""]
    if "result" in call:
        lines += ["What the model read back:", "", _fence(_json(call["result"])), ""]
    if "error" in call:
        lines += ["The error the model read back:", "", _fence(_json(call["error"], 3000)), ""]
    return lines


def write_json(path: str, export: dict) -> None:
    """The structured object, nothing cut, for a script or an attachment."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(export, handle, ensure_ascii=False, indent=1, default=str)


def default_name(export: dict) -> str:
    thread = (export.get("thread") or {}).get("id") or "session"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"ai-agent-{thread}-{stamp}.json"
