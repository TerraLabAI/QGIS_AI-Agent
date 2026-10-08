# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






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


    BUSY = "busy"



    UPLOAD = "upload"

    SESSION = "session"
    TOKEN = "token"  # nosec B105
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


    SERVER_TOOL = "server_tool"


    STEER = "steer"
    STEER_ACK = "steer_ack"

    UNSTEER = "unsteer"


    EFFORT = "effort"

    CLIENT = frozenset({HELLO, USER_MESSAGE, TOOL_RESULT, TOOL_ERROR, PERMISSION_RESPONSE, CANCEL, PING, FEEDBACK,
                        BUSY, UPLOAD, STEER, UNSTEER, EFFORT})
    SERVER = frozenset(
        {SESSION, TOKEN, PLAN, PLAN_UPDATE, TOOL_CALL, RUN_END, ERROR, USAGE, PONG, THREAD_TITLE, STATUS_LINE,
         POLICY, MEMORY_NOTE, FOLLOWUPS, SOURCES, COUNTS, SERVER_TOOL, STEER_ACK}
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
    TIMEOUT = "TIMEOUT"


    NETWORK_ERROR = "NETWORK_ERROR"








PROTOCOL_VERSION = 2


class ServerErrorCode:
    AUTH_FAILED = "AUTH_FAILED"
    PLUGIN_UPDATE_REQUIRED = "PLUGIN_UPDATE_REQUIRED"
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


    ALLOW_RUN = "allow_run"


class Mode:
    ASK = "ask"
    AGENT = "agent"


class Effort:







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











    RESUMABLE = frozenset({"STEP_CAP", "TOKEN_CAP", "WALL_CAP"})


class RunStatus:
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"
    QUOTA = "quota"


class ProtocolError(ValueError):
    pass






def hello(activation_key: str, device_hash: str, plugin_version: str, qgis_version: str,
          os_label: str, locale: str, tool_manifest_hash: str,
          tool_manifest: list | None = None, resume_session_id: str | None = None,
          telemetry: bool = True, improve: bool = True, last_seq: int | None = None,
          python_version: str = "", libraries: list | None = None,
          crash: dict | None = None, catalog_tag: str = "", last_drop: dict | None = None) -> dict:
    frame = {
        "type": FrameType.HELLO,
        "activation_key": activation_key,
        "device_hash": device_hash,
        "plugin_version": plugin_version,
        "qgis_version": qgis_version,
        "os": os_label,
        "locale": locale,
        "tool_manifest_hash": tool_manifest_hash,
        "protocol_version": PROTOCOL_VERSION,






        "telemetry": bool(telemetry),








        "improve": bool(improve),
    }
    if tool_manifest is not None:
        frame["tool_manifest"] = tool_manifest
    if resume_session_id:
        frame["resume_session_id"] = resume_session_id
        if last_drop:

            frame["last_drop"] = last_drop
    if last_seq is not None:



        frame["last_seq"] = max(0, int(last_seq))





    if python_version:
        frame["python_version"] = str(python_version)
    if libraries:
        frame["libraries"] = [str(name) for name in libraries]
    if crash:



        frame["crash"] = crash
    if catalog_tag:




        frame["catalog_tag"] = str(catalog_tag)
    return frame





CATALOG_FIELDS = ("connectors", "qgis_plugins", "qgis_plugins_hidden", "basemaps", "basemaps_reset",
                  "overpass_mirrors", "connector_shelves", "source_licences", "plugin_roster", "use_cases",
                  "use_case_groups")




_ATTACHMENT_EXTRAS = {"kind": str, "path": str, "layer_id": str, "layer_name": str, "error": str,
                      "mime": str, "upload_id": str, "code_page": str, "width": int, "height": int, "size": int}



UPLOAD_CHUNK_BYTES = 4 * 1024 * 1024 - 1


def normalize_attachment(item: Any) -> dict | None:








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
    for key, kind in _ATTACHMENT_EXTRAS.items():
        value = item.get(key)
        if kind is str:
            if value not in (None, "") and key not in out:
                out[key] = str(value)
        elif (isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
                and (not isinstance(value, float) or math.isfinite(value))):
            out[key] = int(value)
    return out


def user_message(run_id: str, thread_id: str, text: str, attachments: list | None,
                 context: dict, mode: str, approval: str, effort: str = Effort.LOW, example: str = "",
                 replaces_run_id: str = "") -> dict:
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


        "example": str(example or ""),


        "replaces_run_id": str(replaces_run_id or ""),
    }


def tool_result(tool_call_id: str, run_id: str, result: Any, code_class: str = "") -> dict:
    frame = {"type": FrameType.TOOL_RESULT, "tool_call_id": tool_call_id,
             "run_id": run_id, "result": result}
    return _with_code_class(frame, code_class)


def call_timing(arrived: float, started: float, answered: float, sent: float, phases: dict | None = None) -> dict:









    def ms(at: float) -> int:
        return max(0, int(round((at - arrived) * 1000)))

    timing = {"started_ms": ms(started), "answered_ms": ms(answered), "sent_ms": ms(sent)}
    kept = {key: int(value) for key, value in (phases or {}).items()
            if key in CALL_PHASES and isinstance(value, int) and 0 <= value <= _PHASE_MAX_MS}
    if kept:
        timing["phases"] = kept
    return timing


CALL_PHASES = ("backup_ms", "prepare_ms", "handler_ms")
_PHASE_MAX_MS = 24 * 3600 * 1000

_PHASES: dict = {}
_PHASES_KEPT = 64


def note_phases(tool_call_id: str, phases: dict) -> None:

    if not phases:
        return
    _PHASES[str(tool_call_id)] = dict(phases)
    while len(_PHASES) > _PHASES_KEPT:
        _PHASES.pop(next(iter(_PHASES)), None)


def take_phases(tool_call_id: str) -> dict | None:

    return _PHASES.pop(str(tool_call_id or ""), None)


def _with_code_class(frame: dict, code_class: str) -> dict:


    if code_class:
        frame["code_class"] = code_class
    return frame














RETRYABLE_CODES = frozenset({
    ClientErrorCode.EXECUTION_FAILED, ClientErrorCode.NETWORK_ERROR,
})
FIXABLE_CODES = frozenset({
    ClientErrorCode.INVALID_ARGS, ClientErrorCode.LAYER_NOT_FOUND, ClientErrorCode.CRS_GUARD,
    ClientErrorCode.TOOL_NOT_FOUND,
})


def error_disposition(code: str) -> str:

    if code in RETRYABLE_CODES:
        return "retry"
    if code in FIXABLE_CODES:
        return "fix"
    return "stop"


def tool_error(tool_call_id: str, run_id: str, code: str, message: str, suggestion: str = "",
               code_class: str = "", detail: str = "") -> dict:


    frame = _with_code_class({"type": FrameType.TOOL_ERROR, "tool_call_id": tool_call_id, "run_id": run_id,
                              "code": code, "message": message, "suggestion": suggestion or "",
                              "retryable": error_disposition(code) == "retry",
                              "disposition": error_disposition(code)}, code_class)
    if detail:
        frame["detail"] = str(detail)
    return frame


def permission_response(tool_call_id: str, run_id: str, decision: str) -> dict:
    if decision not in Decision.ALL:
        raise ValueError(f"unknown decision {decision!r}")
    return {"type": FrameType.PERMISSION_RESPONSE, "tool_call_id": tool_call_id,
            "run_id": run_id, "decision": decision}



CANCEL_STOP = "stop"
CANCEL_PROJECT_CLOSED = "project_closed"
CANCEL_SIGNED_OUT = "signed_out"
CANCEL_QGIS_CLOSED = "qgis_closed"
CANCEL_SERVER_SILENT = "server_silent"


def cancel(run_id: str, reason: str = "") -> dict:
    frame = {"type": FrameType.CANCEL, "run_id": run_id}
    if reason:
        frame["reason"] = str(reason)
    return frame


def unsteer(run_id: str, steer_id: str) -> dict:

    return {"type": FrameType.UNSTEER, "run_id": run_id, "steer_id": steer_id}


def steer(run_id: str, steer_id: str, text: str) -> dict:

    return {"type": FrameType.STEER, "run_id": run_id, "steer_id": steer_id, "text": text}


def effort_switch(run_id: str, effort: str) -> dict:


    return {"type": FrameType.EFFORT, "run_id": run_id, "effort": effort}


def ping() -> dict:
    return {"type": FrameType.PING}


def upload(upload_id: str, name: str, seq: int, total: int, data_base64: str) -> dict:



    return {"type": FrameType.UPLOAD, "upload_id": str(upload_id), "name": str(name)[:512],
            "seq": int(seq), "total": int(total), "data_base64": data_base64}


def busy(seconds: float, where: str, calls: str, ended: bool = False) -> dict:




    frame = {"type": FrameType.BUSY, "seconds": round(float(seconds), 1),
             "where": str(where)[:300], "calls": str(calls)[:300]}
    if ended:
        frame["ended"] = True
    return frame






def encode(frame: dict) -> str:
    if not isinstance(frame, dict) or not isinstance(frame.get("type"), str):
        raise ProtocolError("a frame is a dict with a string 'type'")
    return dump_json(frame)


def _reject_constant(name: str):







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

    kind = frame.get("type", "?")
    ids = [f"{k}={frame[k]}" for k in ("run_id", "tool_call_id", "session_id") if frame.get(k)]
    return f"{kind} {' '.join(ids)}".strip()


def recommended_index(value, options: list) -> int:





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
