# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Logging through QgsMessageLog, never the ``logging`` module."""





from __future__ import annotations

import sys
import threading
from collections import deque
from datetime import datetime, timezone

from .log_scrub import scrub_secrets, scrub_user_paths

TAG = "AI Agent"



MAX_LOG_LINE_CHARS = 2_000
_recent_lines = deque(maxlen=100)
_recent_lines_lock = threading.Lock()
_capture_connected = False


def _qgis():
    try:
        from qgis.core import Qgis, QgsMessageLog

        return Qgis, QgsMessageLog
    except Exception:
        return None, None


def log(message, level=None):
    """One line in the QGIS message log, with the secrets and the account name out."""







    text = _rendered(message)
    qgis, message_log = _qgis()
    if message_log is None:




        if sys.stderr is not None:
            try:
                print(f"[{TAG}] {text}", file=sys.stderr)  # noqa: T201 - no QGIS, no message log
            except Exception:  # nosec B110 - a log line must not raise
                pass
        return
    if level is None:
        level = qgis.MessageLevel.Info
    try:
        message_log.logMessage(text, TAG, level=level)
    except Exception:  # noqa: BLE001 - a log line must never replace the error it describes



        if sys.stderr is not None:
            try:
                print(f"[{TAG}] {text}", file=sys.stderr)  # noqa: T201 - message log unusable
            except Exception:  # nosec B110 - a log line must not raise
                pass


def _rendered(message) -> str:
    """The scrubbed, bounded text of one log line, whatever ``message`` is."""






    try:
        text = str(message)
    except Exception as exc:  # noqa: BLE001 - the value is unrenderable, not the log
        return f"<{type(message).__name__} that cannot be rendered: {type(exc).__name__}>"
    if len(text) > MAX_LOG_LINE_CHARS:
        cut = len(text) - MAX_LOG_LINE_CHARS
        head = int(MAX_LOG_LINE_CHARS * 0.75)
        text = f"{text[:head]} [... {cut:,} chars cut ...] {text[-(MAX_LOG_LINE_CHARS - head):]}"
    try:
        return scrub_secrets(scrub_user_paths(text))
    except Exception:  # noqa: BLE001 - never publish a line the scrubber could not clean
        return f"<a {len(text)}-char message the scrubber could not clean>"


def _capture_message(message, tag, _level) -> None:
    if tag != TAG:
        return
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")




    line = f"[{stamp}] {_rendered(message)}"
    with _recent_lines_lock:
        _recent_lines.append(line)


def start_log_capture() -> None:
    """Keep this plugin's recent QGIS log lines for a user-approved report."""
    global _capture_connected
    if _capture_connected:
        return
    try:
        from qgis.core import QgsApplication

        QgsApplication.messageLog().messageReceived.connect(_capture_message)
        _capture_connected = True
    except Exception:  # nosec B110 - logging must not recurse
        pass


def stop_log_capture() -> None:
    global _capture_connected
    if not _capture_connected:
        return
    try:
        from qgis.core import QgsApplication

        QgsApplication.messageLog().messageReceived.disconnect(_capture_message)
    except (TypeError, RuntimeError, AttributeError):
        pass
    _capture_connected = False


def recent_logs() -> str:
    return "\n".join(_recent_lines) or "(No AI Agent logs captured this session)"


def log_warning(message):
    qgis, _ = _qgis()
    log(message, level=qgis.MessageLevel.Warning if qgis else None)


def log_error(message):
    qgis, _ = _qgis()
    log(message, level=qgis.MessageLevel.Critical if qgis else None)


def log_debug(message):
    log(message)
