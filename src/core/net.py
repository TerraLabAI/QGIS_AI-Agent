# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""One guarded way to fetch a URL the model chose."""













from __future__ import annotations

import contextlib
import datetime
import email.utils
import errno
import hashlib
import http.client
import math
import os
import random
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections import OrderedDict, namedtuple

from . import limits, tuning
from .logger import log_warning
from .security import validate_url, vetted_addresses


class LocalUrlRefused(urllib.error.URLError):
    """The URL, or a redirect on the way, points somewhere the plugin must not fetch."""









_PINNED = threading.local()


def _pin_table() -> dict:
    table = getattr(_PINNED, "hosts", None)
    if table is None:
        table = _PINNED.hosts = {}
    return table


def _pinned_for(host: str) -> tuple:
    return _pin_table().get(host.strip("[]").lower(), ())


def _pin(url: str) -> None:
    """Record what the guard accepted for *url*'s host, or refuse if it no longer holds."""
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").strip("[]").lower()
    except ValueError:
        return
    addresses = vetted_addresses(url)
    if addresses is None:
        raise LocalUrlRefused(
            f"{host} answered with a local address between the check and the connection.")
    if addresses:
        _pin_table()[host] = addresses


def check_url(url: str) -> None:
    """Raise LocalUrlRefused for a URL the plugin must not fetch."""









    problem = validate_url(url)
    if problem:
        raise LocalUrlRefused(f"{problem} ({url[:120]})")
    _pin(url)




_IDENTITY_HEADERS = frozenset({
    "authorization", "proxy-authorization", "cookie", "authentication",
    "x-api-key", "api-key", "apikey", "x-auth-token", "x-access-token", "x-amz-security-token",
})


def _same_origin(one: str, two: str) -> bool:
    """Scheme, host and port all equal: the definition a browser uses."""
    try:
        a, b = urllib.parse.urlsplit(one), urllib.parse.urlsplit(two)
    except ValueError:
        return False
    return (a.scheme.lower() == b.scheme.lower()
            and (a.hostname or "").lower() == (b.hostname or "").lower()
            and a.port == b.port)


def _identity_names(request) -> list:
    """The identity headers this request carries, by their own spelling."""
    names = []
    for store in (getattr(request, "headers", None), getattr(request, "unredirected_hdrs", None)):
        for key in list(store or ()):
            if str(key).lower() in _IDENTITY_HEADERS:
                names.append(key)
    return names


class _NoLocalRedirect(urllib.request.HTTPRedirectHandler):
    """Every hop is re-validated, and credentials do not travel between origins."""










    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        identity = _identity_names(req)
        if identity and not _same_origin(req.full_url, newurl):
            if urllib.parse.urlsplit(newurl).scheme.lower() != "https" and \
                    urllib.parse.urlsplit(req.full_url).scheme.lower() == "https":
                raise LocalUrlRefused(
                    "the server redirected a signed HTTPS request to plain HTTP, which would put "
                    f"its credentials in the clear ({newurl[:120]})")
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None or _same_origin(req.full_url, newurl):
            return new
        dropped = _identity_names(new)
        for key in dropped:
            new.headers.pop(key, None)
            new.unredirected_hdrs.pop(key, None)
        if dropped:
            log_warning(f"Redirect to a different origin: {len(dropped)} identity header(s) "
                        "were not forwarded.")
        return new


















_PROXY_LOCK = threading.Lock()
_PROXY_URL: str | None = None
_SSL_CONTEXT = None
_SSL_CA_PEM: bytes | None = None


def _ssl_context():
    """One TLS context for every fetch."""



    global _SSL_CONTEXT
    if _SSL_CONTEXT is None:
        import ssl

        _SSL_CONTEXT = ssl.create_default_context()
        if _SSL_CA_PEM:
            try:

                _SSL_CONTEXT.load_verify_locations(
                    cadata=_SSL_CA_PEM.decode("ascii", "ignore"))
            except Exception as exc:  # noqa: BLE001 - system roots alone still work
                log_warning(f"QGIS trusted certificates not added to tool fetches: {exc}")
    return _SSL_CONTEXT


def set_trust(ca_pem: bytes | None) -> bool:
    """Trust the CAs QGIS trusts, on top of the ones Python ships with."""











    global _SSL_CONTEXT, _SSL_CA_PEM
    if ca_pem == _SSL_CA_PEM:
        return False
    _SSL_CA_PEM = ca_pem
    _SSL_CONTEXT = None
    return True
































DEFAULT_CONNECT_TIMEOUT_S = 5.0
_CONNECT_LOCAL = threading.local()


class _GuardedConnection:
    """Connect to what the guard vetted, under the short budget when one is set."""

    def _pinned_addresses(self) -> tuple:
        if getattr(self, "_tunnel_host", None):
            return ()
        return _pinned_for(str(self.host or ""))

    def connect(self):  # noqa: D102 - http.client's own contract







        budget = getattr(_CONNECT_LOCAL, "connect_timeout", None) or DEFAULT_CONNECT_TIMEOUT_S
        full = self.timeout
        if budget:
            self.timeout = budget if not full else min(budget, full)
        try:
            pinned = self._pinned_addresses()
            if pinned:
                self._connect_pinned(pinned)
            else:
                super().connect()
        finally:
            self.timeout = full
            sock = getattr(self, "sock", None)
            if sock is not None and isinstance(full, (int, float)):
                try:
                    sock.settimeout(full)
                except OSError:
                    pass

    def _connect_pinned(self, addresses: tuple) -> None:
        """Open the socket to one of *addresses*, trying each as urllib would."""







        original = self._create_connection
        failure = None
        try:
            for address in addresses:
                def to_pinned(target, *args, _address=address, **kwargs):
                    return original((_address, target[1]), *args, **kwargs)

                self._create_connection = to_pinned
                try:
                    super().connect()
                    return
                except OSError as exc:
                    failure = exc
                    sock = getattr(self, "sock", None)
                    if sock is not None:
                        with contextlib.suppress(OSError):
                            sock.close()
                        self.sock = None
        finally:
            self._create_connection = original
        raise failure if failure is not None else OSError("the host resolved to no usable address")


class _GuardedHTTP(_GuardedConnection, http.client.HTTPConnection):
    pass


class _GuardedHTTPS(_GuardedConnection, http.client.HTTPSConnection):
    pass


class _GuardedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_GuardedHTTP, req)


class _GuardedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_GuardedHTTPS, req, context=self._context)


def _build_opener(*handlers):


    return urllib.request.build_opener(_GuardedHTTPHandler(),
                                       _GuardedHTTPSHandler(context=_ssl_context()), *handlers)






_OPENER = None


def _opener():
    """The guarded opener, built once, on the thread that first needs it."""
    global _OPENER
    if _OPENER is None:
        with _PROXY_LOCK:
            if _OPENER is None:
                _OPENER = _build_opener(_NoLocalRedirect())
    return _OPENER


def _redact_proxy(address: str) -> str:
    """A proxy URL fit for a log line: the credentials in it are the user's."""
    try:
        parts = urllib.parse.urlsplit(address)
    except ValueError:
        return "(proxy)"
    if not parts.hostname:
        return "(proxy)"
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{'***@' if parts.username else ''}{parts.hostname}{port}"


def set_proxy(address: str | None) -> bool:
    """Send every guarded fetch through *address*, or straight out when falsy."""




    global _OPENER, _PROXY_URL
    address = (address or "").strip() or None
    with _PROXY_LOCK:
        if address == _PROXY_URL:
            return False
        handlers = [_NoLocalRedirect()]
        if address:
            handlers.append(urllib.request.ProxyHandler({"http": address, "https": address}))
        _OPENER = _build_opener(*handlers)
        _PROXY_URL = address
    log_warning(f"Tool fetches now go through {_redact_proxy(address)}." if address
                else "Tool fetches now go out directly (no QGIS proxy).")
    return True


def proxy_in_use() -> str | None:
    """The proxy the guarded opener is using, with the credentials removed."""
    with _PROXY_LOCK:
        return _redact_proxy(_PROXY_URL) if _PROXY_URL else None


def open_url(request, timeout: float, connect_timeout: float | None = None):
    """``urlopen`` with the local-address guard on the URL and on every redirect."""







    url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
    _pin_table().clear()
    check_url(url)
    opener = _opener()
    _CONNECT_LOCAL.connect_timeout = connect_timeout or None
    try:
        return opener.open(request, timeout=timeout)  # nosec B310 - scheme, address and redirects checked above
    finally:
        _CONNECT_LOCAL.connect_timeout = None


        _pin_table().clear()














CHUNK = 64 * 1024
CACHE_MAX_ENTRIES = 64
CACHE_MAX_BYTES = 4 * 1024 * 1024
CACHE_MAX_TOTAL_BYTES = 24 * 1024 * 1024
_SHARE_WAIT_S = 30.0


class FetchCancelled(urllib.error.URLError):
    """The run was stopped while the body was still arriving."""


class FetchTooLarge(urllib.error.URLError):
    """The body went past the caller's cap."""


class FetchDeadline(urllib.error.URLError):
    """The whole fetch took longer than the caller allowed."""


class FetchTooSlow(urllib.error.URLError):
    """The body fits the size cap, and this connection cannot deliver it in time."""








class FetchTruncated(urllib.error.URLError):
    """The connection ended before the server sent the length it declared."""


class FetchWithdrawn(urllib.error.URLError):
    """The host serves data this product may not offer, so nothing was sent."""





















WITHDRAWN_HOSTS = {
    "gisco-services.ec.europa.eu":
        "Eurostat GISCO grants its boundary files on condition that the data is not used for "
        "commercial purposes, so this product does not serve them. Use osm_planet_boundaries, "
        "overture_divisions or the country's own national row instead.",
    ".open-meteo.com":
        "Open-Meteo's free tier is for non-commercial use only and commercial use needs a paid "
        "key. Use metno_forecast for the forecast and nasa_power_daily for the daily record.",
    "basemaps.cartocdn.com":
        "CARTO has no keyless basemap left: the free tiles carry an API key watermark. Use "
        "openfreemap, osm_standard or a national basemap.",
    ".basemaps.cartocdn.com":
        "CARTO has no keyless basemap left: the free tiles carry an API key watermark. Use "
        "openfreemap, osm_standard or a national basemap.",
    "cartodb-basemaps-a.global.ssl.fastly.net":
        "CARTO has no keyless basemap left. Use openfreemap, osm_standard or a national basemap.",
    "cartodb-basemaps-b.global.ssl.fastly.net":
        "CARTO has no keyless basemap left. Use openfreemap, osm_standard or a national basemap.",
    "cartodb-basemaps-c.global.ssl.fastly.net":
        "CARTO has no keyless basemap left. Use openfreemap, osm_standard or a national basemap.",
    "cartodb-basemaps-d.global.ssl.fastly.net":
        "CARTO has no keyless basemap left. Use openfreemap, osm_standard or a national basemap.",
    "api.inaturalist.org":
        "iNaturalist observations are CC BY-NC per record. Use the gbif row, which asks for the "
        "CC0 and CC BY records only.",
    ".opensky-network.org":
        "OpenSky's terms are non-commercial. There is no keyless commercial replacement for live "
        "flight positions in this catalog.",
    ".nrsc.gov.in":
        "Bhuvan is free to view and not to reuse. For India use the global rows, OpenStreetMap "
        "and the Overture themes.",
    "cartoweb.wms.ngi.be":
        "The Belgian NGI's CartoWeb is CC BY-NC and commercial use needs a paid subscription. "
        "Use the regional orthophotos and OpenStreetMap for Belgium.",
}


def withdrawn_reason(host: str) -> str:
    """Why this product will not reach that host, or "" when it will."""





    host = str(host or "").strip().lower()
    reason = WITHDRAWN_HOSTS.get(host)
    if reason:
        return reason
    parts = host.split(".")
    for cut in range(0, len(parts) - 1):
        reason = WITHDRAWN_HOSTS.get("." + ".".join(parts[cut:]))
        if reason:
            return reason
    return ""


class Response:
    """What a tool needs from a fetch, with the connection already closed."""

    __slots__ = ("body", "headers", "url", "cached")

    def __init__(self, body: bytes, headers: dict, url: str, cached: bool = False):
        self.body = body
        self.headers = headers
        self.url = url
        self.cached = cached

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding, errors="replace")





_LOCAL = threading.local()


def set_cancel_check(check) -> None:
    """Install (or clear, with None) the cancel check for this thread."""
    _LOCAL.cancel = check


def current_cancel_check():
    return getattr(_LOCAL, "cancel", None)


def _cancelled(cancel) -> bool:
    check = cancel or current_cancel_check()
    if check is None:
        return False
    try:
        return bool(check())
    except Exception:  # noqa: BLE001 - a broken check never stops a fetch
        return False


def read_bounded(response, max_bytes: int, deadline: float | None = None, cancel=None,
                 budget: float | None = None) -> bytes:
    """Read at most *max_bytes*, giving up at *deadline* or on cancel."""















    parts: list[bytes] = []
    total = 0
    reader = getattr(response, "read1", None) or response.read
    while True:
        if _cancelled(cancel):
            raise FetchCancelled("The run was stopped.")
        if deadline is not None and time.monotonic() > deadline:
            allowed = f"{budget:g}" if budget else "allotted"
            raise FetchDeadline(f"The download took longer than the {allowed}s allowed.")
        block = reader(min(CHUNK, max_bytes + 1 - total))
        if not block:
            break
        parts.append(block)
        total += len(block)
        if total > max_bytes:
            raise FetchTooLarge(f"The answer is larger than the {max_bytes} bytes this tool reads.")
    return b"".join(parts)


def _declared_length(headers) -> int | None:
    """The body length the server promised, when it promised one."""
    try:
        raw = headers.get("Content-Length") or headers.get("content-length")
    except Exception:  # noqa: BLE001 - a header mapping that cannot answer is no promise
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _span(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())




_CACHE: OrderedDict[str, tuple[float, Response]] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_INFLIGHT: dict[str, threading.Event] = {}


def _cache_get(key: str) -> Response | None:
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        expires, response = entry
        if expires < time.monotonic():
            _CACHE.pop(key, None)
            return None
        _CACHE.move_to_end(key)
        return Response(response.body, dict(response.headers), response.url, cached=True)


def _cache_put(key: str, response: Response, ttl: float) -> None:
    if ttl <= 0 or len(response.body) > CACHE_MAX_BYTES:
        return
    with _CACHE_LOCK:



        _CACHE[key] = (time.monotonic() + ttl,
                       Response(response.body, dict(response.headers), response.url))
        _CACHE.move_to_end(key)
        total = sum(len(entry.body) for _, entry in _CACHE.values())


        while _CACHE and (len(_CACHE) > CACHE_MAX_ENTRIES or total > CACHE_MAX_TOTAL_BYTES):
            _, (_, dropped) = _CACHE.popitem(last=False)
            total -= len(dropped.body)


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def cache_stats() -> dict:
    with _CACHE_LOCK:
        return {"entries": len(_CACHE), "bytes": sum(len(r.body) for _, r in _CACHE.values())}




_CREDENTIAL_HEADERS = ("authorization", "cookie", "proxy-authorization", "x-api-key")


def _credential_tag(request: urllib.request.Request) -> str:
    """A short, non-reversible stand-in for whoever a signed request is."""











    parts = [f"{name.lower()}={value}"
             for name, value in sorted(request.header_items())
             if name.lower() in _CREDENTIAL_HEADERS and value]
    if not parts:
        return ""
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]




























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

RETRY_CODES = (429, 503)
RETRY_MAX = 2
RETRY_BASE_S = 0.5
RETRY_JITTER_S = 0.25
RETRY_MAX_WAIT_S = 30.0
COOLDOWN_MIN_S = 5.0
COOLDOWN_MAX_S = 120.0
_TICK = 0.02


class FetchRateLimited(urllib.error.HTTPError):
    """The host kept answering 429 or 503 after the retry budget was spent."""






def _policy_for(host: str) -> _Policy:













    shipped = _shipped_policy(host)
    tuned = tuning.host_policy(host)
    if tuned is not None:
        rate, burst, concurrency = tuned
        if shipped is not None:
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







        with self._lock:
            self._stats["throttled"] += 1
            self._strikes += 1
            if self._strikes >= 2:
                wait = min(max(delay, COOLDOWN_MIN_S), COOLDOWN_MAX_S)
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
    with _GATES_LOCK:
        gate = _GATES.get(host)
        if gate is None:
            gate = _GATES[host] = _HostGate(_policy_for(host))
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
    return RETRY_BASE_S * (2 ** attempt) + random.uniform(0.0, RETRY_JITTER_S)  # nosec B311


def _budget_spent(url: str, host: str, error, tries: int, delay: float) -> FetchRateLimited:
    reason = (f"{host} answered {error.code} on {tries} tries and asked for {delay:.0f}s. "
              "The service is rate limiting us; try again in a moment.")
    return FetchRateLimited(url, error.code, reason, getattr(error, "headers", None), None)


























OFFLINE_STRIKES = 3
OFFLINE_HOSTS = 2
OFFLINE_HOLD_S = 20.0
MAX_TRACKED_HOSTS = 64
_OFFLINE_LOCK = threading.Lock()
_host_strikes: dict[str, int] = {}
_host_until: dict[str, float] = {}
_offline_until = 0.0


class NetworkUnreachable(urllib.error.URLError):
    """Several requests in a row never reached a server, so this one was not sent."""


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


_NO_ROUTE_ERRNOS = frozenset(
    code for code in (getattr(errno, name, None)
                      for name in ("ENETUNREACH", "EHOSTUNREACH", "ENETDOWN", "EHOSTDOWN", "ECONNREFUSED"))
    if code is not None
)


def _hostname(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(str(url)).hostname or "").strip("[]").lower()
    except ValueError:
        return ""


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


def _warn_if_on_main_thread() -> None:
    """A fetch from the Qt main thread freezes QGIS: say so in the log."""








    try:
        from .background import on_main_thread

        if not on_main_thread():
            return
        from qgis.core import QgsApplication

        if QgsApplication.instance() is None:
            return
    except Exception:  # noqa: BLE001 - a diagnostic never breaks a fetch
        return
    log_warning("net.fetch called on the Qt main thread: QGIS is blocked for the "
                "length of this request. The tool should declare background=True.")


def fetch(request, *, timeout: float, max_bytes: int, total_timeout: float | None = None,
          cache_ttl: float = 0.0, cancel=None, polite: bool = True,
          connect_timeout: float | None = None) -> Response:
    """Fetch a URL once, bounded, interruptible and polite, sharing when asked."""











    _warn_if_on_main_thread()





    ceiling = limits.current("MAX_DOWNLOAD_BYTES")
    max_bytes = int(max_bytes) if max_bytes and max_bytes > 0 else ceiling
    max_bytes = min(max_bytes, ceiling)
    if not isinstance(request, urllib.request.Request):
        request = urllib.request.Request(str(request))
    url = request.full_url
    method = request.get_method()



    refusal = withdrawn_reason(urllib.parse.urlsplit(url).hostname or "")
    if refusal:
        raise FetchWithdrawn(refusal)
    if not request.has_header("User-agent"):


        request.add_header("User-agent", USER_AGENT)
    if not request.has_header("Accept-encoding"):





        request.add_header("Accept-encoding", "gzip, deflate")
    shareable = cache_ttl > 0 and method == "GET"



    key = (f"{url}|{request.get_header('Range', '')}|{request.get_header('Accept', '')}"
           f"|{_credential_tag(request)}" if shareable else "")




    deadline = time.monotonic() + total_timeout if total_timeout else None
    owns_key = False
    if shareable:



        if _cancelled(cancel):
            raise FetchCancelled("The run was stopped.")
        hit = _cache_get(key)




        if hit is not None and len(hit.body) <= max_bytes:
            return hit
        waiter = _join_or_own(key)
        owns_key = waiter is None
        if waiter is not None:
            share_wait = _SHARE_WAIT_S if deadline is None else min(_SHARE_WAIT_S, _span(deadline))
            _wait_for_owner(waiter, share_wait, cancel)


            if _cancelled(cancel):
                raise FetchCancelled("The run was stopped.")
            hit = _cache_get(key)
            if hit is not None and len(hit.body) <= max_bytes:
                return hit




    try:
        if _cancelled(cancel):
            raise FetchCancelled("The run was stopped.")
        host = _hostname(url)
        held = link_is_down(host)
        if held:
            where = f"to {host}" if host else ""
            raise NetworkUnreachable(
                f"The last {OFFLINE_STRIKES} requests {where} did not reach a server, so this one was "
                f"not sent. Check the connection; the agent will try again in {held:.0f}s.")
        try:
            if polite:
                out = _send_politely(request, url, method, timeout, max_bytes, deadline, cancel,
                                     total_timeout, connect_timeout)
            else:
                out = _send_once(request, timeout, max_bytes, deadline, cancel, total_timeout,
                                 connect_timeout)
        except urllib.error.HTTPError:
            _note_reachable(host)
            raise
        except Exception as exc:  # noqa: BLE001 - classified, then re-raised untouched
            if _never_reached_a_server(exc):
                _note_unreachable(host)
            raise
        _note_reachable(host)
        partial = any(str(k).lower() == "content-range" for k in (out.headers or {}))
        if owns_key and (not partial or request.get_header("Range")):
















            _cache_put(key, out, cache_ttl)
    finally:
        if owns_key:
            _release(key)
    return out


def _send_once(request, timeout: float, max_bytes: int, deadline: float | None, cancel,
               budget: float | None = None, connect_timeout: float | None = None) -> Response:



    extra = {"connect_timeout": connect_timeout} if connect_timeout else {}
    try:
        with open_url(request, timeout=timeout, **extra) as response:
            promised = _declared_length(response.headers)
            if (promised is not None and promised > max_bytes
                    and request.get_method() != "HEAD"):





                raise FetchTooLarge(
                    f"The server says the answer is {promised} bytes, over the {max_bytes} bytes "
                    "this tool reads. Ask for less of it, or a narrower area.")







            if promised is not None and budget and request.get_method() != "HEAD":
                needed = seconds_to_transfer(promised)
                if needed is not None and needed > budget:
                    rate = link_kbps() or 0.0
                    raise FetchTooSlow(
                        f"The answer is {promised / (1024 * 1024):.0f} MB and this connection has been "
                        f"delivering {rate:.0f} kB/s, so it needs about {needed / 60:.0f} minutes, over "
                        f"the {budget:.0f} seconds this call has. Ask for a smaller area, a filtered "
                        "extract, or a lower resolution.")






            reading_from = time.perf_counter()
            body = read_bounded(response, max_bytes, deadline, cancel, budget)
























            note_transfer(len(body), time.perf_counter() - reading_from)
            headers = {k.lower(): v for k, v in response.headers.items()}








            if promised is not None and len(body) < promised and request.get_method() != "HEAD":
                raise FetchTruncated(
                    f"The download stopped after {len(body)} of the {promised} bytes the server "
                    "said it was sending. The connection dropped; try again.")
            inflated = _inflate(body, headers.get("content-encoding", ""), max_bytes)
            if inflated is not body:





                headers.pop("content-encoding", None)
                headers["content-length"] = str(len(inflated))
            body = inflated
            return Response(body, headers, response.geturl())
    except urllib.error.HTTPError as exc:
        if exc.code == 407:


            raise urllib.error.HTTPError(
                exc.url, exc.code,
                "The network proxy asked for credentials that were not accepted. "
                "Check the user name and password in QGIS Settings > Options > Network.",
                exc.headers, None) from exc
        raise
    except urllib.error.URLError as exc:
        raise _explain_url_error(exc) from exc


def _explain_url_error(exc: urllib.error.URLError) -> urllib.error.URLError:
    """Turn urllib's transport errors into a sentence that names the fix."""






    reason = str(getattr(exc, "reason", exc) or exc)
    lowered = reason.lower()
    if "certificate" in lowered or "ssl" in lowered or "tls" in lowered:
        return urllib.error.URLError(
            f"{reason}. The server's TLS certificate was not accepted. If this host uses a private "
            "or company certificate authority, add it in QGIS Settings > Options > Authentication.")
    if proxy_in_use() and ("proxy" in lowered or "refused" in lowered or "timed out" in lowered):
        return urllib.error.URLError(
            f"{reason}. Requests go through the proxy set in QGIS Settings > Options > Network "
            f"({proxy_in_use()}); check that it is reachable and allows this host.")
    return exc


def _inflate(body: bytes, encoding: str, max_bytes: int) -> bytes:
    """Undo the server's compression, refusing anything that opens up too far."""






    kind = (encoding or "").strip().lower().split(",")[0].strip()
    if not body or kind not in ("gzip", "x-gzip", "deflate"):
        return body
    gzip_stream = "gzip" in kind
    for wbits in ((16 + zlib.MAX_WBITS,) if gzip_stream else (zlib.MAX_WBITS, -zlib.MAX_WBITS)):
        try:
            worker = zlib.decompressobj(wbits)
            out = worker.decompress(body, max_bytes + 1)
        except zlib.error:
            continue
        if len(out) > max_bytes or worker.unconsumed_tail:
            raise FetchTooLarge(f"The answer expands to more than {max_bytes} bytes.")
        if not gzip_stream:
            return out






        if not worker.eof:
            raise FetchTruncated(
                "The compressed answer ended before its own end marker, so the file is "
                "incomplete. The connection dropped; try again.")
        rest = worker.unused_data
        while rest:
            worker = zlib.decompressobj(wbits)
            try:
                out += worker.decompress(rest, max_bytes + 1 - len(out))
            except zlib.error:
                log_warning("Bytes after a complete gzip stream were not a further member; "
                            "they are ignored.")
                break
            if len(out) > max_bytes or worker.unconsumed_tail:
                raise FetchTooLarge(f"The answer expands to more than {max_bytes} bytes.")
            if not worker.eof:
                raise FetchTruncated(
                    "The compressed answer ended before its own end marker, so the file is "
                    "incomplete. The connection dropped; try again.")
            rest = worker.unused_data
        return out
    log_warning(f"Body announced as {kind} did not decompress; passing it through as it came.")
    return body


def _send_politely(request, url: str, method: str, timeout: float, max_bytes: int,
                   deadline: float | None, cancel, budget: float | None = None,
                   connect_timeout: float | None = None) -> Response:
    """One request through the host's gate, retried when the host says wait."""




    host = urllib.parse.urlparse(url).hostname or ""
    gate = _gate(host)
    retries = RETRY_MAX if method in ("GET", "HEAD") else 0
    attempt = 0
    while True:
        gate.hold(cancel, deadline)
        try:
            wait = gate.reserve()
            try:
                _pause(wait, cancel, deadline)
            except BaseException:
                gate.refund()
                raise
            try:
                out = _send_once(request, timeout, max_bytes, deadline, cancel, budget, connect_timeout)
            except urllib.error.HTTPError as exc:
                if exc.code not in RETRY_CODES:
                    raise
                delay = _refusal_delay(exc, attempt)
                gate.penalise(delay)
                if attempt >= retries or delay > RETRY_MAX_WAIT_S:
                    raise _budget_spent(url, host, exc, attempt + 1, delay) from exc



                with contextlib.suppress(Exception):
                    exc.close()
                gate.retried()
                attempt += 1
                _pause(delay, cancel, deadline)
                continue
            gate.recovered()
            return out
        finally:
            gate.free()


def _wait_for_owner(event: threading.Event, seconds: float, cancel) -> None:
    """Wait for the in-flight owner in short ticks, like ``_pause``."""





    end = time.monotonic() + seconds
    while True:
        left = end - time.monotonic()
        if left <= 0:
            return
        if _cancelled(cancel):
            return
        if event.wait(min(left, _TICK)):
            return


def _join_or_own(key: str):
    """None when this thread owns the request, else the event to wait on."""
    with _CACHE_LOCK:
        event = _INFLIGHT.get(key)
        if event is None:
            _INFLIGHT[key] = threading.Event()
            return None
        return event


def _release(key: str) -> None:
    with _CACHE_LOCK:
        event = _INFLIGHT.pop(key, None)
    if event is not None:
        event.set()





HEAD_START_S = 2.5


class _NotNeeded(Exception):
    """A staggered attempt that was released before it ever made a request."""


def race(attempts, *, workers: int | None = None, stagger: float = HEAD_START_S):
    """Try *attempts* with a head start each, answer with the first that succeeds."""

























    import concurrent.futures

    attempts = list(attempts)
    if not attempts:
        return None, None, []
    if len(attempts) == 1:
        label, call = attempts[0]
        try:
            return label, call(), []
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            return None, None, [f"{label}: {exc}"]

    state = {"failed": 0, "won": False}
    ready = threading.Condition()





    parent_cancel = current_cancel_check()

    def staggered(index: int, call):
        set_cancel_check(parent_cancel)
        try:
            return _staggered(index, call)
        finally:
            set_cancel_check(None)

    def _staggered(index: int, call):






        if index and stagger > 0:
            end = time.monotonic() + stagger * index
            with ready:
                while not state["won"] and state["failed"] < index:
                    left = end - time.monotonic()
                    if left <= 0:
                        break
                    ready.wait(left)
                if state["won"]:
                    raise _NotNeeded(f"a mirror ahead of {index} answered first")
            if _cancelled(parent_cancel):
                raise _NotNeeded("the run was stopped before this mirror was asked")
        try:
            value = call()
        except BaseException:
            with ready:
                state["failed"] += 1
                ready.notify_all()
            raise
        with ready:


            state["won"] = True
            ready.notify_all()
        return value

    errors: list[str] = []
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers or len(attempts))
    futures = {pool.submit(staggered, i, call): label for i, (label, call) in enumerate(attempts)}
    winner_label, winner = None, None
    try:
        for future in concurrent.futures.as_completed(futures):
            label = futures[future]
            try:
                value = future.result()
            except _NotNeeded:
                continue
            except Exception as exc:  # noqa: BLE001 - a mirror being down is normal
                errors.append(f"{label}: {exc}")
                continue
            winner_label, winner = label, value
            break
    finally:
        with ready:
            state["won"] = True
            ready.notify_all()
        for future in futures:
            future.cancel()


        pool.shutdown(wait=False)
    return winner_label, winner, errors
