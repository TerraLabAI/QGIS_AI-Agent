# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Batched usage telemetry for the AI Agent plugin, the way the sibling plugins do it."""






















from __future__ import annotations

import math
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

from qgis.core import QgsApplication, QgsSettings, QgsTask
from qgis.PyQt.QtCore import QTimer

from ..api.terralab_client import TerraLabClient, plugin_version, qgis_version
from . import telemetry_events as ev
from .host_platform import os_info
from .log_scrub import is_secret_key, scrub_secrets
from .privacy_notice import has_accepted_privacy_notice

PRODUCT_ID = "ai-agent"
SOURCE = "qgis_plugin"
TELEMETRY_ENABLED_KEY = "TerraLab/telemetry_enabled"

BATCH_MAX = 10
POST_MAX = 50
QUEUE_HARD_MAX = 200
PRE_AUTH_MAX = 50
FLUSH_INTERVAL_S = 60
TIMEOUT_S = 5.0
RETRY_BACKOFF_S = 2.0




FINAL_TIMEOUT_MS = 3_000
STRING_MAX = 64
ENABLED_CACHE_S = 5.0
MAX_INFLIGHT = 4


LIFECYCLE = frozenset({
    ev.PLUGIN_FIRST_OPEN, ev.PLUGIN_OPENED, ev.PLUGIN_ACTIVATED,
    ev.TELEMETRY_OPT_CHANGED, ev.TUTORIAL_OPENED,
})

FLUSH_NOW = frozenset({
    ev.AGENT_RUN_ENDED, ev.QUOTA_EXHAUSTED, ev.PLUGIN_ERROR,
    ev.TELEMETRY_OPT_CHANGED, ev.ACCOUNT_SIGNED_OUT,
})

_lock = threading.Lock()
_batch: list = []
_pending_pre_auth: list = []
_session_id = uuid.uuid4().hex

_enabled_cache: dict = {"value": None, "at": 0.0}
_flush_timer: QTimer | None = None
_auth_provider = None
_inflight: list = []
_first_open_key = "AIAgent/telemetry_first_open_sent"
_first_run_key = "AIAgent/telemetry_first_run_sent"





def is_telemetry_enabled() -> bool:
    """The shared opt-out, read at most every few seconds. Unreadable means off."""
    now = time.monotonic()
    cached = _enabled_cache["value"]
    if cached is not None and now - _enabled_cache["at"] < ENABLED_CACHE_S:
        return cached
    try:
        value = bool(QgsSettings().value(TELEMETRY_ENABLED_KEY, True, type=bool))
    except Exception:  # nosec B110 - telemetry must not block QGIS
        value = False
    _enabled_cache.update(value=value, at=now)
    return value


def set_telemetry_enabled(enabled: bool) -> None:
    """Persist the opt-out for every TerraLab plugin."""




    if not enabled:
        track(ev.TELEMETRY_OPT_CHANGED, {"enabled": False}, flush_now=True)
    try:
        QgsSettings().setValue(TELEMETRY_ENABLED_KEY, bool(enabled))
    except Exception:  # nosec B110 - telemetry must not block QGIS
        pass
    _enabled_cache["value"] = None
    if enabled:
        track(ev.TELEMETRY_OPT_CHANGED, {"enabled": True}, flush_now=True)
    else:

        drop_queued_events(include_outbox=False)


def drop_queued_events(include_outbox: bool = True) -> None:
    with _lock:
        _batch.clear()
        _pending_pre_auth.clear()
    if include_outbox:
        for task in list(_inflight):
            try:
                task.cancel()
            except RuntimeError:
                pass





def new_session() -> None:
    """Rotate the session id. Call on dock open so events group by session."""
    global _session_id
    _session_id = uuid.uuid4().hex


def current_session_id() -> str:
    return _session_id


def set_auth_provider(provider) -> None:
    """A main-thread callable returning the bearer headers, or {} when signed out."""
    global _auth_provider
    _auth_provider = provider


def first_open_recorded() -> bool:
    """True the first time this profile opens the plugin; remembered in QgsSettings."""
    try:
        settings = QgsSettings()
        if settings.value(_first_open_key, False, type=bool):
            return False
        settings.setValue(_first_open_key, True)
        return True
    except Exception:  # noqa: BLE001
        return False


def first_run_recorded() -> bool:
    """True once, when this QGIS profile starts its first agent run."""
    try:
        settings = QgsSettings()
        if settings.value(_first_run_key, False, type=bool):
            return False
        settings.setValue(_first_run_key, True)
        return True
    except Exception:  # noqa: BLE001
        return False





def clean(properties: dict | None) -> dict:
    """Keep numbers, booleans, None and short strings."""




    out = {}
    if not isinstance(properties, dict):
        return out
    for key, value in properties.items():
        if (not isinstance(key, str) or len(key) > STRING_MAX or is_secret_key(key)
                or scrub_secrets(key) != key):
            continue
        if value is None or isinstance(value, (bool, int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                continue
            out[key] = value
        elif isinstance(value, str):
            text = value.strip()
            if (len(text) <= STRING_MAX and "/" not in text and "\\" not in text and "," not in text
                    and "@" not in text and scrub_secrets(text) == text
                    and not any(ord(c) < 32 for c in text)):
                out[key] = text
    return out


def _base_properties() -> dict:
    system, release, machine = os_info()
    props = {
        "product_id": PRODUCT_ID,
        "plugin_version": plugin_version(),
        "os": system,
        "os_version": release,
        "arch": machine,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "qgis_version": qgis_version() or "unknown",
        "session_id": _session_id,
        "registry_version": ev.REGISTRY_VERSION,
    }
    try:
        from .settings import Settings
        props["device_hash"] = Settings().device_hash
    except Exception:  # nosec B110 - telemetry must not block QGIS
        pass
    return props


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")





def track(event: str, properties: dict | None = None, flush_now: bool = False) -> None:
    """Queue one event. Safe from any thread. Nothing is queued when opted out."""
    if not _on_main_thread():


        try:
            from qgis.PyQt.QtCore import QCoreApplication

            from .background import main_thread_invoker
            if QCoreApplication.instance() is not None:
                props = clean(properties)
                main_thread_invoker().invoke(lambda: track(event, props, flush_now))
        except Exception:  # nosec B110 - analytics never interrupts a tool
            pass
        return
    if not event or not is_telemetry_enabled():
        return
    try:
        record = {"event": event, "timestamp": _now_iso(), "properties": {**_base_properties(), **clean(properties)}}
        record["properties"]["product_id"] = PRODUCT_ID
    except Exception:  # noqa: BLE001
        return




    if not has_accepted_privacy_notice():
        if event in LIFECYCLE:
            with _lock:
                if len(_pending_pre_auth) < PRE_AUTH_MAX:
                    _pending_pre_auth.append(record)
            _arm_flush_timer()
        return
    with _lock:
        _batch.append(record)
        _trim_locked()
        urgent = flush_now or event in FLUSH_NOW or len(_batch) >= BATCH_MAX
    if urgent:
        flush()
    _arm_flush_timer()


def _trim_locked() -> None:
    drop = len(_batch) - QUEUE_HARD_MAX
    if drop <= 0:
        return
    kept = []
    for record in _batch:
        if drop > 0 and record["event"] not in FLUSH_NOW:
            drop -= 1
            continue
        kept.append(record)
    _batch[:] = kept[drop:] if drop > 0 else kept


def _on_main_thread() -> bool:
    try:
        from qgis.PyQt.QtCore import QCoreApplication, QThread
        app = QCoreApplication.instance()
        return app is not None and QThread.currentThread() == app.thread()
    except Exception:  # noqa: BLE001
        return False


def flush(final: bool = False) -> None:
    """Hand the queued batch to the sender."""






    if not _on_main_thread():
        return
    if not is_telemetry_enabled():
        drop_queued_events()
        return



    if not has_accepted_privacy_notice():
        return
    capacity = max(0, MAX_INFLIGHT - len(_inflight)) * POST_MAX
    if not capacity:
        return
    with _lock:
        if not _batch and not _pending_pre_auth:
            return
    headers = _auth_headers()
    with _lock:
        if not headers:
            for record in _batch:
                if record["event"] in LIFECYCLE and len(_pending_pre_auth) < PRE_AUTH_MAX:
                    _pending_pre_auth.append(record)
            _batch.clear()
            return
        events = list(_pending_pre_auth) + list(_batch)
        _batch[:] = events[capacity:]
        events = events[:capacity]
        _pending_pre_auth.clear()
    if not events:
        return
    for start in range(0, len(events), POST_MAX):
        _start_flush_task(events[start:start + POST_MAX], dict(headers), final)


def _auth_headers() -> dict:
    try:
        headers = _auth_provider() if _auth_provider is not None else {}
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(headers, dict) or not headers.get("Authorization"):
        return {}
    return headers


class _TelemetryFlushTask(QgsTask):
    """A silent task that uses TerraLabClient and QGIS proxy settings."""

    def __init__(self, events: list, auth: dict, final: bool = False):
        holder = getattr(QgsTask, "Flag", QgsTask)
        flags = holder.CanCancel
        for name in ("Hidden", "Silent"):
            extra = getattr(holder, name, None)
            if extra is not None:
                flags = flags | extra
        super().__init__("AI Agent telemetry", flags)
        self._events = list(events)


        self._auth = dict(auth)
        self._final = bool(final)

    def run(self) -> bool:
        attempts = 1 if self._final else 2
        for attempt in range(attempts):
            if self.isCanceled():
                return False
            try:
                result = TerraLabClient().send_telemetry_batch(
                    self._events, self._auth, timeout_ms=FINAL_TIMEOUT_MS if self._final else None)
                if not isinstance(result, dict) or not result.get("error"):
                    return True
            except Exception:  # nosec B110 - telemetry must not block QGIS
                pass
            if attempt + 1 < attempts:

                end = time.monotonic() + RETRY_BACKOFF_S
                while time.monotonic() < end:
                    if self.isCanceled():
                        return False
                    time.sleep(0.1)
        return False

    def finished(self, _result: bool) -> None:
        self._auth.clear()
        self._events.clear()
        try:
            _inflight.remove(self)
        except ValueError:
            pass


def _start_flush_task(events: list, auth: dict, final: bool = False) -> None:
    task = None
    try:
        task = _TelemetryFlushTask(events, auth, final)
        _inflight.append(task)
        if not QgsApplication.taskManager().addTask(task):
            _inflight.remove(task)
    except Exception:  # nosec B110 - telemetry must not block QGIS
        if task in _inflight:
            _inflight.remove(task)





def start_flush_timer() -> None:
    """Main thread. Ships what worker threads queued, once a minute."""
    global _flush_timer
    if _flush_timer is not None or not _on_main_thread():
        return
    try:
        timer = QTimer()
        timer.setInterval(FLUSH_INTERVAL_S * 1000)
        timer.timeout.connect(_on_timer)
        timer.start()
        _flush_timer = timer
    except Exception:  # nosec B110 - telemetry must not block QGIS
        _flush_timer = None


def _arm_flush_timer() -> None:
    """Main thread. Wake the minute timer when something waits to be shipped."""
    timer = _flush_timer
    if timer is None:
        return
    try:
        if not timer.isActive():
            timer.start()
    except Exception:  # nosec B110 - telemetry must not block QGIS
        pass


def _on_timer() -> None:
    try:
        flush()
    except Exception:  # nosec B110 - telemetry must not block QGIS
        pass


    with _lock:
        idle = not _batch and not _pending_pre_auth
    timer = _flush_timer
    if idle and timer is not None:
        try:
            timer.stop()
        except Exception:  # nosec B110 - telemetry must not block QGIS
            pass


def stop_flush_timer() -> None:
    global _flush_timer
    timer, _flush_timer = _flush_timer, None
    if timer is None:
        return
    try:
        timer.stop()
        timer.timeout.disconnect(_on_timer)
        timer.deleteLater()
    except Exception:  # nosec B110 - telemetry must not block QGIS
        pass


def shutdown() -> None:
    """Last flush at unload, then stop the timer."""








    global _auth_provider

    try:
        flush(final=True)
    except Exception:  # nosec B110 - telemetry must not block QGIS
        pass
    stop_flush_timer()
    _auth_provider = None
