# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The per-host politeness a shared public service is owed: rate, in-flight cap, refusals, identity."""

from __future__ import annotations

import datetime
import email.utils
import math
import os
import random
import threading
import time
import urllib.error
from collections import namedtuple

from . import tuning
from .net_failure import FetchCancelled, FetchDeadline
from .net_state import _cancelled



























CONTACT = "yvann.barbot@terra-lab.ai"
PROJECT_URL = "https://github.com/TerraLabAI/QGIS_AI-Agent"
_UA_CACHE: str | None = None


def _plugin_version() -> str:
    """The version in metadata.txt, read once."""





    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        with open(os.path.join(root, "metadata.txt"), encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("version="):
                    return line.split("=", 1)[1].strip() or "unknown"
    except OSError:
        pass
    return "unknown"


def user_agent() -> str:
    """The one User-Agent every request in this plugin sends."""
    global _UA_CACHE
    if _UA_CACHE is None:
        _UA_CACHE = f"QGIS-AI-Agent/{_plugin_version()} (+{PROJECT_URL}; contact: {CONTACT})"
    return _UA_CACHE




USER_AGENT = user_agent()

_Policy = namedtuple("_Policy", "rate burst concurrency")













DEFAULT_POLICY = _Policy(rate=2.0, burst=4, concurrency=2)












HOST_POLICIES = {



    "nominatim.openstreetmap.org": _Policy(rate=1.0, burst=1, concurrency=1),



    "routing.openstreetmap.de": _Policy(rate=1.0, burst=1, concurrency=1),







    "overpass-api.de": _Policy(rate=0.5, burst=2, concurrency=2),







    "overpass.private.coffee": _Policy(rate=0.5, burst=2, concurrency=2),
    "maps.mail.ru": _Policy(rate=0.5, burst=2, concurrency=2),
    "overpass.kumi.systems": _Policy(rate=0.5, burst=2, concurrency=2),






    "overpass.terra-lab.ai": _Policy(rate=4.0, burst=8, concurrency=4),
    "geocode.terra-lab.ai": _Policy(rate=10.0, burst=20, concurrency=4),




    "agent.terra-lab.ai": _Policy(rate=10.0, burst=30, concurrency=4),





    "terra-lab.ai": _Policy(rate=1.0, burst=3, concurrency=2),











    "aca-terralab-opendata.proudsky-7d379d48.westeurope.azurecontainerapps.io":
        _Policy(rate=1.5, burst=20, concurrency=6),





    "stterralabopendata.blob.core.windows.net": _Policy(rate=8.0, burst=30, concurrency=6),
}






OWN_HOSTS = frozenset({
    "overpass.terra-lab.ai",
    "geocode.terra-lab.ai",
    "agent.terra-lab.ai",
    "terra-lab.ai",
    "aca-terralab-opendata.proudsky-7d379d48.westeurope.azurecontainerapps.io",
    "stterralabopendata.blob.core.windows.net",
})

RETRY_CODES = (429, 503)
RETRY_MAX = 2
RETRY_BASE_S = 0.5
RETRY_JITTER_S = 0.25
RETRY_MAX_WAIT_S = 30.0
COOLDOWN_MIN_S = 5.0
COOLDOWN_MAX_S = 120.0
_TICK = 0.02


def retry_numbers() -> dict:
    """The refusal retries and the cooldown in force: the constants above under a served `net` row."""





    low = float(tuning.number("net", "cooldown_min_s", COOLDOWN_MIN_S))
    return {
        "retry_max": int(tuning.limit("net", "refusal_retry_max", RETRY_MAX)),
        "base_s": float(tuning.number("net", "refusal_retry_base_s", RETRY_BASE_S)),
        "max_wait_s": float(tuning.number("net", "refusal_max_wait_s", RETRY_MAX_WAIT_S)),
        "cooldown_min_s": low,
        "cooldown_max_s": max(low, float(tuning.number("net", "cooldown_max_s", COOLDOWN_MAX_S))),
    }


class FetchRateLimited(urllib.error.HTTPError):
    """The host kept answering 429 or 503 after the retry budget was spent."""






def _policy_for(host: str) -> _Policy:














    shipped = _shipped_policy(host)
    tuned = tuning.host_policy(host)
    if tuned is not None:
        rate, burst, concurrency = tuned
        if shipped is not None and host not in OWN_HOSTS:
            rate = min(rate, shipped.rate)
            burst = min(burst, shipped.burst)
            concurrency = min(concurrency, shipped.concurrency)
        return _Policy(rate, burst, concurrency)
    return shipped if shipped is not None else DEFAULT_POLICY


def host_is_stated(host: str) -> bool:
    """True when this file states a limit for the host, its parent domain included."""










    return _shipped_policy(str(host or "").strip().lower()) is not None


def _shipped_policy(host: str) -> _Policy | None:
    """The limit this file states for the host, exact then parent domain."""
    policy = HOST_POLICIES.get(host)
    if policy is not None:
        return policy
    parts = host.split(".")






    for cut in range(0, len(parts) - 1):
        policy = HOST_POLICIES.get("." + ".".join(parts[cut:]))
        if policy is not None:
            return policy
    return None


class _HostGate:
    """The rate, the in-flight cap and the counters for one hostname."""

    def __init__(self, policy: _Policy):
        self.policy = policy
        self._lock = threading.Lock()
        self._tokens = float(policy.burst)
        self._stamp = time.monotonic()
        self._cooldown_until = 0.0
        self._strikes = 0
        self._slots = threading.Semaphore(policy.concurrency)
        self._busy = 0
        self._waiting = 0
        self._touched = time.monotonic()
        self._stats = {"requests": 0, "waits": 0, "waited_s": 0.0, "throttled": 0, "retries": 0, "cooldowns": 0}

    def reserve(self) -> float:
        """Take one token and answer how long this caller must wait to use it."""






        with self._lock:
            now = time.monotonic()
            self._tokens = min(float(self.policy.burst), self._tokens + (now - self._stamp) * self.policy.rate)
            self._stamp = now
            self._tokens -= 1.0
            wait = 0.0 if self._tokens >= 0 else -self._tokens / self.policy.rate
            wait = max(wait, self._cooldown_until - now)
            self._stats["requests"] += 1
            if wait > 0:
                self._stats["waits"] += 1
                self._stats["waited_s"] += wait
            return wait

    def refund(self) -> None:
        """Give the token back: the caller was stopped before it used it."""
        with self._lock:
            self._tokens = min(float(self.policy.burst), self._tokens + 1.0)

    def hold(self, cancel, deadline: float | None) -> None:
        """Wait for a free in-flight slot, still answering Stop while waiting."""
        self._waiting += 1
        try:
            self._acquire(cancel, deadline)
        except BaseException:
            self._waiting -= 1
            raise
        self._waiting -= 1
        self._busy += 1

    def _acquire(self, cancel, deadline: float | None) -> None:
        while not self._slots.acquire(timeout=_TICK):
            if _cancelled(cancel):
                raise FetchCancelled("The run was stopped.")
            if deadline is not None and time.monotonic() > deadline:
                raise FetchDeadline("The host was busy for longer than this fetch was allowed to take.")

    def free(self) -> None:
        self._busy = max(0, self._busy - 1)
        self._slots.release()

    @property
    def idle(self) -> bool:
        """Nothing in flight, nobody queued, and not cooling down: safe to forget."""
        return not self._busy and not self._waiting and self._cooldown_until <= time.monotonic()

    def penalise(self, delay: float) -> None:
        """Count a refusal, and from the second one make every caller wait."""







        numbers = retry_numbers()
        with self._lock:
            self._stats["throttled"] += 1
            self._strikes += 1
            if self._strikes >= 2:
                wait = min(max(delay, numbers["cooldown_min_s"]), numbers["cooldown_max_s"])
                self._cooldown_until = max(self._cooldown_until, time.monotonic() + wait)
                self._stats["cooldowns"] += 1

    def retried(self) -> None:
        with self._lock:
            self._stats["retries"] += 1

    def recovered(self) -> None:
        """One success pays off one strike, not all of them."""







        with self._lock:
            if self._strikes:
                self._strikes -= 1

    def snapshot(self) -> dict:
        with self._lock:
            out = dict(self._stats)
            out["waited_s"] = round(out["waited_s"], 3)
            out["rate"] = self.policy.rate
            out["concurrency"] = self.policy.concurrency
            out["cooldown_s"] = round(max(0.0, self._cooldown_until - time.monotonic()), 3)
            return out


_GATES: dict[str, _HostGate] = {}
_GATES_LOCK = threading.Lock()









GATES_KEEP = 512





_GATES_PRUNE_EVERY_S = 5.0
_last_gates_prune = 0.0


def _gate(host: str) -> _HostGate:
    global _last_gates_prune






    policy = _policy_for(host)
    with _GATES_LOCK:
        gate = _GATES.get(host)
        if gate is not None and gate.policy != policy and gate.idle:
            fresh = _HostGate(policy)
            fresh._stats, fresh._strikes = dict(gate._stats), gate._strikes
            gate = _GATES[host] = fresh
        if gate is None:
            gate = _GATES[host] = _HostGate(policy)
            now = time.monotonic()




            if len(_GATES) > GATES_KEEP and now - _last_gates_prune >= _GATES_PRUNE_EVERY_S:
                _last_gates_prune = now
                _prune_gates()
        gate._touched = time.monotonic()
        return gate


def _prune_gates() -> None:
    """Drop the oldest idle gates. Called under _GATES_LOCK."""
    idle = sorted((g._touched, h) for h, g in _GATES.items() if g.idle)
    for _, host in idle[:max(0, len(_GATES) - GATES_KEEP)]:
        _GATES.pop(host, None)


def politeness_stats() -> dict:
    """Per-host counters: why a tool is waiting, and who refused us."""




    with _GATES_LOCK:
        gates = list(_GATES.items())
    return {host: gate.snapshot() for host, gate in gates}


def politeness_reset() -> None:
    """Forget every bucket and counter. Tests, and a new QGIS session."""
    global _last_gates_prune
    with _GATES_LOCK:
        _GATES.clear()
    _last_gates_prune = 0.0


def _pause(seconds: float, cancel, deadline: float | None) -> None:
    """Sleep in small steps so Stop lands inside a rate-limit wait too."""
    if seconds <= 0:
        return
    if deadline is not None and time.monotonic() + seconds > deadline:
        raise FetchDeadline(f"Waiting {seconds:.1f}s for the host's rate limit would pass this fetch's budget.")
    end = time.monotonic() + seconds
    while True:
        left = end - time.monotonic()
        if left <= 0:
            return
        if _cancelled(cancel):
            raise FetchCancelled("The run was stopped.")
        time.sleep(min(left, _TICK))


def retry_after_seconds(value) -> float | None:
    """Seconds from a Retry-After header, given as a count or as an HTTP date."""
    if not value:
        return None
    value = str(value).strip()
    try:



        asked = float(value)
    except ValueError:
        pass
    else:
        return max(0.0, asked) if math.isfinite(asked) else None
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return max(0.0, (when - datetime.datetime.now(datetime.timezone.utc)).total_seconds())


def _refusal_delay(error, attempt: int) -> float:
    """How long to wait after a 429 or a 503: what the host asked, or backoff."""
    headers = getattr(error, "headers", None)
    asked = None
    if headers is not None:
        try:
            asked = retry_after_seconds(headers.get("Retry-After") or headers.get("retry-after"))
        except Exception:  # noqa: BLE001 - a malformed header never breaks a fetch
            asked = None
    if asked is not None:
        return asked
    # nosec B311 - spreads retries so refused clients do not return in lockstep; not a secret
    return retry_numbers()["base_s"] * (2 ** attempt) + random.uniform(0.0, RETRY_JITTER_S)  # nosec B311


def _budget_spent(url: str, host: str, error, tries: int, delay: float) -> FetchRateLimited:
    reason = (f"{host} answered {error.code} on {tries} tries and asked for {delay:.0f}s. "
              "The service is rate limiting us; try again in a moment.")
    return FetchRateLimited(url, error.code, reason, getattr(error, "headers", None), None)
