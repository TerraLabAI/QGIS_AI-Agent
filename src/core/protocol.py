# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Wire frames, plugin <-> backend, exactly as docs/PROTOCOL.md spells them."""





from __future__ import annotations

import json
import math
from typing import Any

from .log_scrub import scrub_result
from .serialization import dump_json


class FrameType:

    HELLO = "hello"
    USER_MESSAGE = "user_message"
    TOOL_RESULT = "tool_result"
    TOOL_ERROR = "tool_error"
    PERMISSION_RESPONSE = "permission_response"
    CANCEL = "cancel"
    PING = "ping"

    FEEDBACK = "feedback"

    SESSION = "session"
    TOKEN = "token"  # nosec B105 - protocol frame name, not a credential
    PLAN = "plan"
    PLAN_UPDATE = "plan_update"
    TOOL_CALL = "tool_call"
    RUN_END = "run_end"
    ERROR = "error"
    USAGE = "usage"
    PONG = "pong"
    THREAD_TITLE = "thread_title"


    MEMORY_NOTE = "memory_note"
    STATUS_LINE = "status_line"



    POLICY = "policy"





    FOLLOWUPS = "followups"
    SOURCES = "sources"
    COUNTS = "counts"

    CLIENT = frozenset({HELLO, USER_MESSAGE, TOOL_RESULT, TOOL_ERROR, PERMISSION_RESPONSE, CANCEL, PING, FEEDBACK})
    SERVER = frozenset(
        {SESSION, TOKEN, PLAN, PLAN_UPDATE, TOOL_CALL, RUN_END, ERROR, USAGE, PONG, THREAD_TITLE, STATUS_LINE,
         POLICY, MEMORY_NOTE, FOLLOWUPS, SOURCES, COUNTS}
    )


class ClientErrorCode:
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    INVALID_ARGS = "INVALID_ARGS"
    READ_ONLY_MODE = "READ_ONLY_MODE"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    CRS_GUARD = "CRS_GUARD"
    LAYER_NOT_FOUND = "LAYER_NOT_FOUND"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    CANCELLED = "CANCELLED"
    RUN_BUDGET = "RUN_BUDGET"


KNOWN_CLIENT_CODES = frozenset({
    ClientErrorCode.TOOL_NOT_FOUND, ClientErrorCode.INVALID_ARGS, ClientErrorCode.READ_ONLY_MODE,
    ClientErrorCode.PERMISSION_DENIED, ClientErrorCode.CRS_GUARD, ClientErrorCode.LAYER_NOT_FOUND,
    ClientErrorCode.EXECUTION_FAILED, ClientErrorCode.CANCELLED, ClientErrorCode.RUN_BUDGET,
})


class ServerErrorCode:
    AUTH_FAILED = "AUTH_FAILED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    BUSY = "BUSY"
    INTERNAL = "INTERNAL"


class Danger:
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    RANK = {READ: 0, WRITE: 1, DESTRUCTIVE: 2}

    @classmethod
    def normalize(cls, value: Any, default: str = DESTRUCTIVE) -> str:
        return value if isinstance(value, str) and value in cls.RANK else default

    @classmethod
    def most_dangerous(cls, *values: Any) -> str:
        best = cls.READ
        for value in values:
            if isinstance(value, str) and value in cls.RANK and cls.RANK[value] > cls.RANK[best]:
                best = value
        return best


class Decision:
    ALLOW = "allow"
    ALLOW_PROJECT = "allow_project"
    DENY = "deny"
    PENDING = "pending"
    ALL = frozenset({ALLOW, ALLOW_PROJECT, DENY, PENDING})


class Mode:
    ASK = "ask"
    AGENT = "agent"


class Effort:
    """How hard the agent works on a message, picked on the composer's effort slider."""






    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    ALL = (LOW, MEDIUM, HIGH)
    LEGACY = {LOW: "fast", MEDIUM: "pro", HIGH: "pro"}


class Approval:
    CAREFUL = "careful"
    ASK = "ask"
    AUTO = "auto"
    ALL = (CAREFUL, ASK, AUTO)


class StopCode:
    """The server codes an error frame carries when a run ends on a budget."""










    RESUMABLE = frozenset({"STEP_CAP", "TOKEN_CAP", "WALL_CAP"})


class RunStatus:
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"
    QUOTA = "quota"


class PlanState:
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class ProtocolError(ValueError):
    """The text is not one valid frame."""






def hello(activation_key: str, device_hash: str, plugin_version: str, qgis_version: str,
          os_label: str, locale: str, tool_manifest_hash: str,
          tool_manifest: list | None = None, resume_session_id: str | None = None,
          telemetry: bool = True, improve: bool = True) -> dict:
    frame = {
        "type": FrameType.HELLO,
        "activation_key": activation_key,
        "device_hash": device_hash,
        "plugin_version": plugin_version,
        "qgis_version": qgis_version,
        "os": os_label,
        "locale": locale,
        "tool_manifest_hash": tool_manifest_hash,






        "telemetry": bool(telemetry),








        "improve": bool(improve),
    }
    if tool_manifest is not None:
        frame["tool_manifest"] = tool_manifest
    if resume_session_id:
        frame["resume_session_id"] = resume_session_id
    return frame


_ATTACHMENT_EXTRAS = ("kind", "path", "layer_id", "layer_name", "error")
_ATTACHMENT_INT_EXTRAS = ("width", "height")


def normalize_attachment(item: Any) -> dict | None:
    """Keep the two shapes the server accepts, plus the keys the model needs."""







    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or "")
    if item.get("data_base64"):
        out = {"name": name, "mime": str(item.get("mime") or "application/octet-stream"),
               "data_base64": str(item["data_base64"])}
    elif item.get("path"):
        out = {"name": name or str(item["path"]).replace("\\", "/").split("/")[-1],
               "path": str(item["path"])}
    else:
        return None
    for key in _ATTACHMENT_EXTRAS:
        value = item.get(key)
        if value not in (None, "") and key not in out:
            out[key] = str(value)
    for key in _ATTACHMENT_INT_EXTRAS:
        value = item.get(key)
        if (isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
                and (not isinstance(value, float) or math.isfinite(value))):
            out[key] = int(value)
    return out


def user_message(run_id: str, thread_id: str, text: str, attachments: list | None,
                 context: dict, mode: str, approval: str, effort: str = Effort.LOW) -> dict:
    clean = [a for a in (normalize_attachment(x) for x in (attachments or [])) if a]
    effort = effort if effort in Effort.ALL else Effort.LOW
    return {
        "type": FrameType.USER_MESSAGE,
        "run_id": run_id,
        "thread_id": thread_id,
        "text": text,
        "attachments": clean,







        "context": scrub_result(context),
        "mode": mode if mode in (Mode.ASK, Mode.AGENT) else Mode.AGENT,
        "approval": approval if approval in (Approval.ASK, Approval.AUTO) else Approval.ASK,
        "effort": effort,

        "model_mode": Effort.LEGACY[effort],
    }


def tool_result(tool_call_id: str, run_id: str, result: Any) -> dict:
    return {"type": FrameType.TOOL_RESULT, "tool_call_id": tool_call_id,
            "run_id": run_id, "result": result}














RETRYABLE_CODES = frozenset({
    ClientErrorCode.EXECUTION_FAILED,
})
FIXABLE_CODES = frozenset({
    ClientErrorCode.INVALID_ARGS, ClientErrorCode.LAYER_NOT_FOUND, ClientErrorCode.CRS_GUARD,
    ClientErrorCode.TOOL_NOT_FOUND,
})


def error_disposition(code: str) -> str:
    """"retry", "fix" or "stop" for a client error code."""
    if code in RETRYABLE_CODES:
        return "retry"
    if code in FIXABLE_CODES:
        return "fix"
    return "stop"


def tool_error(tool_call_id: str, run_id: str, code: str, message: str, suggestion: str = "") -> dict:
    return {"type": FrameType.TOOL_ERROR, "tool_call_id": tool_call_id, "run_id": run_id,
            "code": code, "message": message, "suggestion": suggestion or "",
            "retryable": error_disposition(code) == "retry",
            "disposition": error_disposition(code)}


def permission_response(tool_call_id: str, run_id: str, decision: str) -> dict:
    if decision not in Decision.ALL:
        raise ValueError(f"unknown decision {decision!r}")
    return {"type": FrameType.PERMISSION_RESPONSE, "tool_call_id": tool_call_id,
            "run_id": run_id, "decision": decision}


def cancel(run_id: str) -> dict:
    return {"type": FrameType.CANCEL, "run_id": run_id}


def feedback(run_id: str, up: bool) -> dict:
    """The thumbs under an answer: up or down, on one finished run."""
    return {"type": FrameType.FEEDBACK, "run_id": run_id, "up": bool(up)}


def ping() -> dict:
    return {"type": FrameType.PING}






def encode(frame: dict) -> str:
    if not isinstance(frame, dict) or not isinstance(frame.get("type"), str):
        raise ProtocolError("a frame is a dict with a string 'type'")
    return dump_json(frame)


def _reject_constant(name: str):
    """Python's json accepts the bare NaN/Infinity tokens; JSON does not."""






    raise ProtocolError(f"frame contains the non-JSON number {name}")


def decode(text: str) -> dict:
    try:
        frame = json.loads(text, parse_constant=_reject_constant)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ProtocolError(f"frame is not JSON: {exc}") from exc
    if not isinstance(frame, dict):
        raise ProtocolError("frame is not a JSON object")
    kind = frame.get("type")
    if not isinstance(kind, str) or not kind:
        raise ProtocolError("frame has no string 'type'")
    return frame


def is_server_type(kind: str) -> bool:
    return kind in FrameType.SERVER


def describe(frame: dict) -> str:
    """One short line for the log, never the payload."""
    kind = frame.get("type", "?")
    ids = [f"{k}={frame[k]}" for k in ("run_id", "tool_call_id", "session_id") if frame.get(k)]
    return f"{kind} {' '.join(ids)}".strip()


def recommended_index(value, options: list) -> int:
    """The option the model recommends, as an index into ``options``; -1 when none."""




    if not options or value is None or isinstance(value, bool):
        return -1
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
            return -1
        index = int(value)
        return index if 0 <= index < len(options) else -1
    text = str(value).strip()
    if not text:
        return -1
    if text.lstrip("-").isdigit():
        try:
            index = int(text)
        except ValueError:
            return -1
        return index if 0 <= index < len(options) else -1
    lowered = [str(o).strip().lower() for o in options]
    return lowered.index(text.lower()) if text.lower() in lowered else -1
