# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether the link is down, and how fast it has been measured to be."""

from __future__ import annotations

import os
import socket
import threading
import time
import urllib.error

from .net_failure import _NO_ROUTE_ERRNOS

























OFFLINE_STRIKES = 3
OFFLINE_HOSTS = 2
OFFLINE_HOLD_S = 20.0
MAX_TRACKED_HOSTS = 64
_OFFLINE_LOCK = threading.Lock()
_host_strikes: dict[str, int] = {}
_host_until: dict[str, float] = {}
_offline_until = 0.0


def _never_reached_a_server(exc: BaseException) -> bool:
    """True only for a failure that proves the request did not arrive anywhere."""
    if isinstance(exc, urllib.error.HTTPError):
        return False
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, socket.gaierror):
        return True
    if isinstance(reason, ConnectionRefusedError):
        return True
    if isinstance(reason, OSError) and not isinstance(reason, socket.timeout):
        return reason.errno in _NO_ROUTE_ERRNOS
    return False


def _note_reachable(host: str = "") -> None:
    """A real answer arrived: the link is up, whatever it said."""





    global _offline_until
    with _OFFLINE_LOCK:
        _offline_until = 0.0
        if host:
            _host_strikes.pop(host, None)
            _host_until.pop(host, None)


def _note_unreachable(host: str = "") -> None:
    """A request that never reached a server. Strikes belong to the host it named."""
    global _offline_until
    now = time.monotonic()
    with _OFFLINE_LOCK:
        for name in [n for n, u in _host_until.items() if u <= now]:
            _host_until.pop(name, None)
            _host_strikes.pop(name, None)
        if not host:

            _offline_until = now + OFFLINE_HOLD_S
            return
        strikes = _host_strikes.get(host, 0) + 1
        _host_strikes[host] = strikes
        if strikes >= OFFLINE_STRIKES:
            _host_until[host] = now + OFFLINE_HOLD_S
        if len([u for u in _host_until.values() if u > now]) >= OFFLINE_HOSTS:
            _offline_until = now + OFFLINE_HOLD_S
        while len(_host_strikes) > MAX_TRACKED_HOSTS:
            oldest = next(iter(_host_strikes))
            _host_strikes.pop(oldest, None)
            _host_until.pop(oldest, None)


def link_is_down(host: str = "") -> float:
    """Seconds left of the hold on ``host``, or 0.0 when fetching it is allowed."""




    now = time.monotonic()
    with _OFFLINE_LOCK:
        left = _offline_until - now
        if host:
            left = max(left, _host_until.get(host, 0.0) - now)
        return max(0.0, left)


def forget_link_state() -> None:
    """Tests, a new QGIS session, and the moment the user asks to try again."""
    global _offline_until
    with _OFFLINE_LOCK:
        _offline_until = 0.0
        _host_strikes.clear()
        _host_until.clear()



















LINK_SAMPLE_MIN_BYTES = 64 * 1024



LINK_SMOOTHING = 0.4


LINK_REFERENCE_KBPS = 2000.0



LINK_POOR_KBPS = 250.0

_LINK_LOCK = threading.Lock()
_link_kbps: float | None = None
_link_samples = 0


def note_transfer(byte_count: int, seconds: float) -> None:
    """Record a real transfer. Called by ``fetch``; never raises, never blocks."""
    global _link_kbps, _link_samples
    try:
        if byte_count < LINK_SAMPLE_MIN_BYTES or seconds <= 0.0:
            return
        observed = (byte_count / 1024.0) / seconds
        with _LINK_LOCK:
            if _link_kbps is None:
                _link_kbps = observed
            else:
                _link_kbps = (1.0 - LINK_SMOOTHING) * _link_kbps + LINK_SMOOTHING * observed
            _link_samples += 1
    except Exception:  # nosec B110 - a measurement never breaks the fetch it was measuring; an
        pass


def link_kbps() -> float | None:
    """Kilobytes per second this connection has been delivering, or None if unmeasured."""




    override = os.environ.get("AI_AGENT_LINK_KBPS", "").strip()
    if override:
        try:






            rate = float(override)
            if rate > 0.0:
                return rate
        except ValueError:
            pass
    with _LINK_LOCK:
        return _link_kbps


def link_factor() -> float:
    """The connection as a multiplier on a download ceiling, 0.05 to 1.0."""






    rate = link_kbps()
    if rate is None or rate >= LINK_REFERENCE_KBPS:
        return 1.0
    return round(max(0.05, rate / LINK_REFERENCE_KBPS), 4)


def seconds_to_transfer(byte_count: int) -> float | None:
    """How long *byte_count* takes on this line, or None when it is unmeasured."""
    rate = link_kbps()
    if not rate:
        return None
    return (float(byte_count) / 1024.0) / rate


def link_report() -> dict:
    """What the connection looks like, for the debug menu and the perf matrix."""
    rate = link_kbps()
    with _LINK_LOCK:
        samples = _link_samples
    return {"kbps": round(rate, 1) if rate else None, "samples": samples,
            "factor": link_factor(), "poor": bool(rate and rate < LINK_POOR_KBPS)}


def forget_link_speed() -> None:
    """Tests, and a session that changed network."""
    global _link_kbps, _link_samples
    with _LINK_LOCK:
        _link_kbps, _link_samples = None, 0
