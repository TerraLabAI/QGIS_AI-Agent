# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Safe plugin-error telemetry at Qt slot boundaries."""
from __future__ import annotations

import functools
import hashlib
import os
import traceback

from . import telemetry_events as ev
from .log_scrub import scrub_secrets, scrub_user_paths
from .logger import log_warning


def short_traceback_hash(error: BaseException) -> str:
    """Return a stable path-free fingerprint for one exception traceback."""
    try:
        frames = traceback.extract_tb(error.__traceback__)
        parts = [f"{os.path.basename(frame.filename)}:{frame.lineno}:{frame.name}" for frame in frames]
        parts.append(error.__class__.__name__)
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]
    except Exception:  # nosec B110 - error tracking must not recurse
        return ""


def track_plugin_error(stage: str, error_code: str, run_id: str = "", module: str = "",
                       traceback_hash: str = "") -> None:
    """Record structured, path-free failure data. User text never enters telemetry."""
    props = {"stage": stage or "other", "error_code": error_code or "UNKNOWN"}
    if run_id:
        props["run_id"] = run_id
    if module:
        props["module"] = module.rsplit(".", 1)[-1]
    if traceback_hash:
        props["traceback_hash"] = traceback_hash
    try:
        from . import telemetry
        telemetry.track(ev.PLUGIN_ERROR, props)
    except Exception:  # nosec B110 - error tracking must not recurse
        pass


def report_exception(error: BaseException, stage: str, module: str = "", run_id: str = "") -> None:
    """Log and track an exception without leaking its message or filesystem path."""
    code = error.__class__.__name__ or "Exception"
    fingerprint = short_traceback_hash(error)
    track_plugin_error(stage, code, run_id, module, fingerprint)
    detail = scrub_secrets(scrub_user_paths(str(error).splitlines()[0] if str(error) else ""))
    log_warning(f"Unhandled {code} in {module or 'unknown'} ({stage}) [{fingerprint or '-'}]: {detail[:160]}")


def slot_guard(stage: str):
    """Catch a top-level Qt slot failure so QGIS keeps running and support sees it."""
    def decorator(function):
        @functools.wraps(function)
        def wrapped(self, *args, **kwargs):
            try:
                return function(self, *args, **kwargs)
            except Exception as error:  # noqa: BLE001
                run_id = ""
                try:
                    run_id = self.current_run_id()
                except Exception:  # nosec B110 - error tracking must not recurse
                    pass
                report_exception(error, stage, function.__module__, run_id)
                return None
        return wrapped
    return decorator
