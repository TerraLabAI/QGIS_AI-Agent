# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import functools
import hashlib
import os
import sys
import threading
import traceback

from . import telemetry_events as ev
from .log_scrub import scrub_secrets, scrub_user_paths
from .logger import log_warning


def short_traceback_hash(error: BaseException) -> str:

    try:
        frames = traceback.extract_tb(error.__traceback__)
        parts = [f"{os.path.basename(frame.filename)}:{frame.lineno}:{frame.name}" for frame in frames]
        parts.append(error.__class__.__name__)
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]
    except Exception:  # nosec B110
        return ""


def track_plugin_error(stage: str, error_code: str, run_id: str = "", module: str = "",
                       traceback_hash: str = "", slot: str = "", where: str = "", reason: str = "",
                       retryable: bool | None = None, in_run: bool | None = None,
                       os_error: int | None = None, synced: bool | None = None) -> None:

    props = {"stage": stage or "other", "error_code": error_code or "UNKNOWN"}
    if run_id:
        props["run_id"] = run_id
    if module:
        props["module"] = module.rsplit(".", 1)[-1]
    if traceback_hash:
        props["traceback_hash"] = traceback_hash



    if slot:
        props["slot"] = slot[:60]
    if where:
        props["where"] = where[:64]

    if reason:
        props["reason"] = reason[:64]
    if retryable is not None:
        props["retryable"] = retryable
    if in_run is not None:
        props["in_run"] = in_run


    if isinstance(os_error, int):
        props["os_error"] = os_error
    if synced is not None:
        props["synced"] = synced
    try:
        from . import telemetry
        telemetry.track(ev.PLUGIN_ERROR, props)
    except Exception:  # nosec B110
        pass


def innermost_frame(error: BaseException) -> str:

    try:
        frames = traceback.extract_tb(error.__traceback__)
        if not frames:
            return ""
        frame = frames[-1]
        return f"{os.path.basename(frame.filename)}:{frame.lineno}:{frame.name}"
    except Exception:  # nosec B110
        return ""


def report_exception(error: BaseException, stage: str, module: str = "", run_id: str = "",
                     slot: str = "") -> None:

    try:
        if getattr(error, "_ai_agent_reported", False):
            return
        error._ai_agent_reported = True
    except Exception:  # nosec B110
        pass
    code = error.__class__.__name__ or "Exception"
    fingerprint = short_traceback_hash(error)
    where = innermost_frame(error)
    track_plugin_error(stage, code, run_id, module, fingerprint, slot=slot, where=where)
    detail = scrub_secrets(scrub_user_paths(str(error).splitlines()[0] if str(error) else ""))
    log_warning(f"Unhandled {code} in {module or 'unknown'}{'.' + slot if slot else ''} ({stage}) at {where or '?'} "
                f"[{fingerprint or '-'}]: {detail[:160]}")


def slot_guard(stage: str):

    def decorator(function):
        @functools.wraps(function)
        def wrapped(self, *args, **kwargs):
            try:
                return function(self, *args, **kwargs)
            except Exception as error:  # noqa: BLE001
                run_id = ""
                try:
                    run_id = self.current_run_id()
                except Exception:  # nosec B110
                    pass
                report_exception(error, stage, function.__module__, run_id)
                return None
        return wrapped
    return decorator


_PLUGIN_DIR = os.path.normcase(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + os.sep)
_hooks: dict = {}
_reporting = threading.local()


def _from_this_plugin(error: BaseException) -> bool:

    try:
        frames = traceback.extract_tb(error.__traceback__)
        return any(os.path.normcase(os.path.abspath(f.filename)).startswith(_PLUGIN_DIR) for f in frames)
    except Exception:  # nosec B110
        return False


def _report_uncaught(error: BaseException | None) -> None:
    if error is None or getattr(_reporting, "busy", False) or not _from_this_plugin(error):
        return
    _reporting.busy = True
    try:
        report_exception(error, "uncaught")
    except Exception:  # nosec B110
        pass
    finally:
        _reporting.busy = False


def install_excepthooks() -> None:

    if _hooks:
        return
    previous_sys, previous_thread = sys.excepthook, threading.excepthook

    def sys_hook(exc_type, exc, tb):
        try:
            previous_sys(exc_type, exc, tb)
        finally:
            if _hooks:
                _report_uncaught(exc)

    def thread_hook(args):
        try:
            previous_thread(args)
        finally:
            if _hooks:
                _report_uncaught(args.exc_value)

    _hooks.update(sys=(sys_hook, previous_sys), thread=(thread_hook, previous_thread))
    sys.excepthook = sys_hook
    threading.excepthook = thread_hook


def restore_excepthooks() -> None:

    if not _hooks:
        return
    mine_sys, previous_sys = _hooks["sys"]
    mine_thread, previous_thread = _hooks["thread"]
    _hooks.clear()
    if sys.excepthook is mine_sys:
        sys.excepthook = previous_sys
    if threading.excepthook is mine_thread:
        threading.excepthook = previous_thread


def _restore_error_code(result: dict) -> str:

    message = str(result.get("message") or "")
    if result.get("unreadable_copy"):
        return "COPY_UNREADABLE"
    if message.startswith("No snapshot"):
        return "NO_SNAPSHOT"
    if not result.get("layers") and "project" in message:
        return "PROJECT_UNREADABLE"
    if result.get("file_restore_errors"):
        return "FILES_NOT_RESTORED"
    if result.get("memory_layers_incomplete"):
        return "MEMORY_INCOMPLETE"
    return "INCOMPLETE"


def track_restore_failure(result: dict, where: str) -> None:


    errors = [e for e in result.get("file_restore_errors") or [] if isinstance(e, dict)]
    first = errors[0] if errors else {}
    kinds = "+".join(dict.fromkeys(str(e.get("kind")) for e in errors if e.get("kind")))
    track_plugin_error("restore", _restore_error_code(result), where=where, reason=kinds,
                       os_error=first.get("os_error"), synced=first.get("synced") if first else None)
