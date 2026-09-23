# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


















from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time

from .host_platform import remove_quietly, retry_file_op
from .logger import log, log_warning
from .policy import AGENT_HOME, ensure_agent_directories




NOTE_PATH = os.path.join(AGENT_HOME, "last_call.json")



MAX_AGE_S = 7 * 24 * 3600.0


def args_digest(args) -> str:

    try:
        text = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        text = str(args)
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def _qgis_version() -> str:
    try:
        from qgis.core import Qgis

        return str(Qgis.QGIS_VERSION).split("-")[0]
    except Exception:  # noqa: BLE001
        return ""


def _write_note(payload: str) -> None:












    directory = os.path.dirname(NOTE_PATH) or "."
    fd, tmp = tempfile.mkstemp(prefix=".agent-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        retry_file_op(os.replace, tmp, NOTE_PATH)
    finally:
        remove_quietly(tmp)


def write(run_id: str, tool_call_id: str, session_id: str, tool: str, args) -> None:





    try:
        ensure_agent_directories()
        _write_note(json.dumps({
            "run_id": str(run_id or ""),
            "tool_call_id": str(tool_call_id or ""),
            "session_id": str(session_id or ""),
            "tool": str(tool or ""),
            "args_digest": args_digest(args),
            "qgis_version": _qgis_version(),
            "started_at": time.time(),
        }, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Crash note not written: {type(exc).__name__}: {exc}")


def clear() -> None:

    try:
        os.remove(NOTE_PATH)
    except FileNotFoundError:
        return
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Crash note not cleared: {type(exc).__name__}: {exc}")


def take() -> dict | None:





    try:
        with open(NOTE_PATH, encoding="utf-8") as handle:
            raw = handle.read(4096)
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Crash note not read: {type(exc).__name__}: {exc}")
        return None
    clear()
    try:
        note = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(note, dict) or not note.get("run_id"):
        return None
    started = note.get("started_at")
    if not isinstance(started, (int, float)) or abs(time.time() - started) > MAX_AGE_S:
        return None
    log(f"QGIS had died inside {note.get('tool') or 'a tool call'}; reporting it")
    return {
        "run_id": str(note.get("run_id") or "")[:128],
        "tool_call_id": str(note.get("tool_call_id") or "")[:128],
        "session_id": str(note.get("session_id") or "")[:128],
        "tool": str(note.get("tool") or "")[:128],
        "args_digest": str(note.get("args_digest") or "")[:64],
        "qgis_version": str(note.get("qgis_version") or "")[:64],
        "plugin_version": str(note.get("plugin_version") or "")[:64],
        "started_at": float(started),
    }
