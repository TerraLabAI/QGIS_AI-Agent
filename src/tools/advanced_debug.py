# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""get_message_log and get_debug_info: the QGIS message buffer and the machine report."""
from __future__ import annotations

import hashlib
import os

from qgis.core import QgsApplication

from ..core.serialization import size_budget

_LOG_BUFFER_SIZE = 5000
_MSG_BUF = "_aiagent_msglog_buf"

_msg_buf_cache: dict = {"buf": None}
_MSG_CONNECTED = "_aiagent_msglog_connected"


def _message_buffer():
    """The captured-message ring, parked on the QgsApplication singleton."""





    cached = _msg_buf_cache["buf"]
    if cached is not None:
        return cached
    from collections import deque

    app = QgsApplication.instance()
    if app is None:
        return deque(maxlen=_LOG_BUFFER_SIZE)
    buf = app.property(_MSG_BUF)
    if buf is None:
        buf = deque(maxlen=_LOG_BUFFER_SIZE)
        app.setProperty(_MSG_BUF, buf)


    _msg_buf_cache["buf"] = buf
    return buf


def _connect_message_log():
    """Connect to QgsApplication.messageLog() to capture messages."""






    app = QgsApplication.instance()
    if app is not None and app.property(_MSG_CONNECTED):
        return
    try:
        msg_log = QgsApplication.messageLog()
        if hasattr(msg_log, "messageReceived"):
            msg_log.messageReceived.connect(_on_message_received)
            if app is not None:
                app.setProperty(_MSG_CONNECTED, True)
    except Exception:  # nosec B110 - render enhancement is optional
        pass


def _on_message_received(message, tag, level):
    """Capture log messages into our buffer with timestamp."""
    import time
    _LEVEL_MAP = {0: "info", 1: "warning", 2: "critical", 3: "success"}




    try:
        ordinal = int(level)
    except (TypeError, ValueError):
        ordinal = level
    _message_buffer().append({
        "tag": tag,
        "message": message,
        "level": _LEVEL_MAP.get(ordinal, str(level)),
        "timestamp": time.strftime("%H:%M:%S"),
    })


_LOG_RESULT_BUDGET = 4_000


def _clip_message(text: str, max_chars: int) -> str:
    """Cut a long log line, saying how much was dropped so nothing looks complete."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}… [+{len(text) - max_chars} chars]"


def _get_message_log(args: dict) -> dict:
    _connect_message_log()
    limit = args.get("limit", 25)
    tag = args.get("tag")
    min_level = args.get("level")
    search = (args.get("search") or "").lower()
    try:
        max_message_chars = int(args.get("max_message_chars", 500))
    except (TypeError, ValueError):
        max_message_chars = 500

    _LEVEL_SEVERITY = {"info": 0, "success": 0, "warning": 1, "critical": 2}

    captured = list(_message_buffer())
    messages = list(captured)
    messages.reverse()

    if tag:
        messages = [m for m in messages if m["tag"] == tag]
    if min_level:
        threshold = _LEVEL_SEVERITY.get(min_level, 0)
        messages = [m for m in messages if _LEVEL_SEVERITY.get(m["level"], 0) >= threshold]
    if search:
        messages = [m for m in messages if search in m["message"].lower() or search in m["tag"].lower()]

    matched = len(messages)
    messages = [
        dict(m, message=_clip_message(m["message"], max_message_chars))
        for m in messages[:limit]
    ]


    messages, _dropped = size_budget(messages, _LOG_RESULT_BUDGET)

    out = {
        "messages": messages,
        "count": len(messages),
        "buffer_size": len(captured),
        "buffer_capacity": _LOG_BUFFER_SIZE,
    }
    if matched > len(messages):
        out["messages_omitted"] = matched - len(messages)
    if not tag:

        tags = sorted({m["tag"] for m in captured})
        out["available_tags"] = tags[:40]
        if len(tags) > 40:
            out["tags_omitted"] = len(tags) - 40
    return out




_ENV_HIGHLIGHTS = (
    "PROJ_LIB", "PROJ_DATA", "GDAL_DATA", "GDAL_DRIVER_PATH", "GDAL_PAM_ENABLED",
    "QT_SCALE_FACTOR", "QT_AUTO_SCREEN_SCALE_FACTOR", "QT_QPA_PLATFORM",
    "PYTHONPATH", "PYTHONHOME", "QGIS_PREFIX_PATH", "QGIS_PLUGINPATH", "PATH",
)


def _python_executable() -> str:
    """The interpreter a user could actually type, not the program we run in."""








    import os
    import sys

    current = sys.executable or ""
    if os.name != "nt" or os.path.basename(current).lower().startswith("python"):
        return current
    sibling = os.path.join(os.path.dirname(current), "python.exe")
    return sibling if os.path.exists(sibling) else current


def _get_debug_info(args: dict) -> dict:
    import platform
    import sys

    from qgis.core import Qgis, QgsProviderRegistry

    from ..core.host_platform import os_info, os_label

    result = {
        "qgis_version": Qgis.version(),
        "python_version": platform.python_version(),
        "python_executable": _python_executable(),
        "host_executable": sys.executable,
        "os": f"{os_label()} ({os_info()[2]})",
        "profile_path": QgsApplication.qgisSettingsDirPath(),
        "prefix_path": QgsApplication.prefixPath(),
        "locale": QgsApplication.locale(),
    }






    try:
        from ..core import machine, net

        result["machine"] = machine.report()
        result["connection"] = net.link_report()
    except Exception as err:
        result["machine_error"] = f"{err.__class__.__name__}: {err}"

    try:
        result["data_providers"] = sorted(QgsProviderRegistry.instance().providerList())
    except Exception as err:
        result["data_providers_error"] = f"{err.__class__.__name__}: {err}"

    try:
        proc_reg = QgsApplication.processingRegistry()
        result["processing_providers"] = [
            {"id": p.id(), "name": p.name(), "algorithms": len(p.algorithms()), "active": p.isActive()}
            for p in proc_reg.providers()
        ]
    except Exception as err:
        result["processing_providers_error"] = f"{err.__class__.__name__}: {err}"

    try:
        import qgis.utils
        result["active_plugins"] = {
            name: qgis.utils.pluginMetadata(name, "version") or "?"
            for name in sorted(qgis.utils.plugins)
        }
    except Exception as err:
        result["active_plugins_error"] = f"{err.__class__.__name__}: {err}"

    try:
        srs_db = QgsApplication.srsDatabaseFilePath()
        crs_db = {"path": srs_db, "exists": os.path.exists(srs_db)}
        if crs_db["exists"]:
            crs_db["size_bytes"] = os.path.getsize(srs_db)
        aux = QgsApplication.qgisUserDatabaseFilePath()
        crs_db["user_database"] = {"path": aux, "exists": os.path.exists(aux)}
        result["crs_database"] = crs_db
    except Exception as err:
        result["crs_database_error"] = f"{err.__class__.__name__}: {err}"

    try:
        svg = list(QgsApplication.svgPaths())
        result["svg_paths"] = [{"path": p, "exists": os.path.isdir(p)} for p in svg]
    except Exception as err:
        result["svg_paths_error"] = f"{err.__class__.__name__}: {err}"

    try:
        env = dict(os.environ)

        result["environment_highlights"] = {k: env[k] for k in _ENV_HIGHLIGHTS if k in env}
    except Exception as err:
        result["environment_error"] = f"{err.__class__.__name__}: {err}"

    from ..core.host_platform import peak_memory_mb

    peak = peak_memory_mb()
    if peak is None:
        result["memory_peak_mb_error"] = "peak memory unavailable on this platform"
    else:
        result["memory_peak_mb"] = peak

    _connect_message_log()



    seen = set()
    recent_errors = []
    for m in reversed(list(_message_buffer())):
        if m["level"] not in ("warning", "critical"):
            continue
        key = hashlib.sha256(f"{m['tag']}|{m['message']}".encode("utf-8", "replace")).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        recent_errors.append(dict(m, message=_clip_message(m["message"], 500)))
        if len(recent_errors) >= 10:
            break
    if recent_errors:
        result["recent_errors"] = recent_errors

    return result

