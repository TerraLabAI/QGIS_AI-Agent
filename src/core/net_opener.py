# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


from __future__ import annotations

import contextlib
import http.client
import ipaddress
import select
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .logger import log_warning
from .net_failure import LocalUrlRefused, _transient
from .security import _as_address, refused_addresses, validate_url, vetted_addresses
from .serialization import coded_like








_PINNED = threading.local()



_RESOLVE_AT_CONNECT = "resolve-at-connect"


def _pin_table() -> dict:
    table = getattr(_PINNED, "hosts", None)
    if table is None:
        table = _PINNED.hosts = {}
    return table


def _pinned_for(host: str) -> tuple:
    return _pin_table().get(host.strip("[]").lower(), ())


def _pin(url: str) -> None:

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
    elif host and _as_address(host) is None:
        _pin_table()[host] = _RESOLVE_AT_CONNECT


def check_url(url: str) -> None:










    problem = validate_url(url)
    if problem:
        raise LocalUrlRefused(coded_like(f"{problem} ({url[:120]})", problem))
    _pin(url)




_IDENTITY_HEADERS = frozenset({
    "authorization", "proxy-authorization", "cookie", "authentication",
    "x-api-key", "api-key", "apikey", "x-auth-token", "x-access-token", "x-amz-security-token",
})


def _same_origin(one: str, two: str) -> bool:

    try:
        a, b = urllib.parse.urlsplit(one), urllib.parse.urlsplit(two)
    except ValueError:
        return False
    return (a.scheme.lower() == b.scheme.lower()
            and (a.hostname or "").lower() == (b.hostname or "").lower()
            and a.port == b.port)


def _identity_names(request) -> list:

    names = []
    for store in (getattr(request, "headers", None), getattr(request, "unredirected_hdrs", None)):
        for key in list(store or ()):
            if str(key).lower() in _IDENTITY_HEADERS:
                names.append(key)
    return names


class _NoLocalRedirect(urllib.request.HTTPRedirectHandler):











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
        if new is not None and req.get_method() == "HEAD":


            new.method = "HEAD"
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




    global _SSL_CONTEXT
    if _SSL_CONTEXT is None:
        _SSL_CONTEXT = ssl.create_default_context()
        if _SSL_CA_PEM:
            try:

                _SSL_CONTEXT.load_verify_locations(
                    cadata=_SSL_CA_PEM.decode("ascii", "ignore"))
            except Exception as exc:  # noqa: BLE001
                log_warning(f"QGIS trusted certificates not added to tool fetches: {exc}")
    return _SSL_CONTEXT


def set_trust(ca_pem: bytes | None) -> bool:












    global _OPENER, _SSL_CONTEXT, _SSL_CA_PEM
    with _PROXY_LOCK:
        if ca_pem == _SSL_CA_PEM:
            return False
        _SSL_CA_PEM = ca_pem
        _SSL_CONTEXT = None




        old, _OPENER = _OPENER, None
    _drop_pool(old)
    return True
































DEFAULT_CONNECT_TIMEOUT_S = 10.0
_CONNECT_LOCAL = threading.local()


class _GuardedConnection:


    def _pinned_addresses(self) -> tuple:
        if getattr(self, "_tunnel_host", None):
            return ()
        return _pinned_for(str(self.host or ""))

    def connect(self):  # noqa: D102







        budget = getattr(_CONNECT_LOCAL, "connect_timeout", None) or DEFAULT_CONNECT_TIMEOUT_S
        full = self.timeout
        if budget:
            self.timeout = budget if not full else min(budget, full)
        try:
            pinned = self._pinned_addresses()
            if pinned == _RESOLVE_AT_CONNECT:
                pinned = self._resolve_and_vet()
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

    def _resolve_and_vet(self) -> tuple:
        host = str(self.host or "")
        infos = socket.getaddrinfo(host, self.port, 0, socket.SOCK_STREAM)
        addresses = tuple(dict.fromkeys(info[4][0] for info in infos if info[4]))
        reason = refused_addresses(f"http://{host}:{self.port}/", addresses)
        if reason:
            raise LocalUrlRefused(reason)
        return addresses

    def _connect_pinned(self, addresses: tuple) -> None:








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

























POOL_IDLE_S = 60.0
POOL_PER_HOST = 4
POOL_TOTAL = 32




POOL_UNSAFE_IDLE_S = 2.0
_RETRIED_METHODS = frozenset({"GET", "HEAD"})



_STALE = (http.client.BadStatusLine, http.client.ImproperConnectionState, ConnectionError,
          ssl.SSLEOFError, ssl.SSLZeroReturnError)


def _ip(text):
    try:
        return ipaddress.ip_address(str(text or "").split("%", 1)[0])
    except ValueError:
        return None


class _PooledResponse(http.client.HTTPResponse):


    _on_done = None
    _trailer_read = False

    def _read_and_discard_trailer(self):
        super()._read_and_discard_trailer()
        self._trailer_read = True

    def _finished(self) -> bool:
        if self.will_close:
            return False
        if self._method == "HEAD":
            return True
        if self.chunked:
            return self._trailer_read
        return self.length == 0

    def _close_conn(self):
        done, self._on_done = self._on_done, None
        super()._close_conn()
        if done is not None:
            done(self._finished())


class _PooledHTTPS(_GuardedHTTPS):
    response_class = _PooledResponse


class _Pool:




    def __init__(self):
        self._lock = threading.RLock()
        self._idle: dict = {}
        self._closed = False

    def _expired(self, now: float) -> list:
        gone = []
        for key in list(self._idle):
            keep = []
            for since, conn in self._idle[key]:
                (keep if now - since < POOL_IDLE_S else gone).append((since, conn))
            if keep:
                self._idle[key] = keep
            else:
                del self._idle[key]
        return [conn for _, conn in gone]

    def take(self, key: tuple, method: str):

        now = time.monotonic()
        limit = POOL_IDLE_S if method in _RETRIED_METHODS else POOL_UNSAFE_IDLE_S
        found = None
        with self._lock:
            gone = self._expired(now)
            entries = self._idle.get(key) or []
            while entries and found is None:
                since, conn = entries.pop()
                if now - since < limit and _peer_allowed(conn, key[0]):
                    found = conn
                else:
                    gone.append(conn)
            if not entries:
                self._idle.pop(key, None)
        for conn in gone:
            _close_quietly(conn)
        if found is not None and not _still_quiet(found):
            _close_quietly(found)
            found = None
        return found

    def give(self, key: tuple, conn) -> None:
        now = time.monotonic()
        with self._lock:
            if self._closed or conn.sock is None:
                gone = [conn]
            else:
                gone = self._expired(now)
                entries = self._idle.setdefault(key, [])
                entries.append((now, conn))
                while len(entries) > POOL_PER_HOST:
                    gone.append(entries.pop(0)[1])
                total = sum(len(v) for v in self._idle.values())
                while total > POOL_TOTAL:
                    oldest = min(self._idle, key=lambda k: self._idle[k][0][0])
                    gone.append(self._idle[oldest].pop(0)[1])
                    if not self._idle[oldest]:
                        del self._idle[oldest]
                    total -= 1
        for each in gone:
            _close_quietly(each)

    def close(self, final: bool = False) -> None:
        with self._lock:
            self._closed = self._closed or final
            gone = [conn for entries in self._idle.values() for _, conn in entries]
            self._idle.clear()
        for conn in gone:
            _close_quietly(conn)


def _close_quietly(conn) -> None:
    with contextlib.suppress(Exception):
        conn.close()


def _still_quiet(conn) -> bool:


    sock = conn.sock
    if sock is None:
        return False
    try:
        if getattr(sock, "pending", None) and sock.pending():
            return False
        readable, _, _ = select.select([sock], [], [], 0)
    except (OSError, ValueError):
        return False
    return not readable


def _peer_allowed(conn, host: str) -> bool:

    pinned = _pinned_for(host)
    if pinned == _RESOLVE_AT_CONNECT:
        return False
    if not pinned:
        return _ip(host) is not None and _ip(host) == getattr(conn, "_peer_ip", None)
    return getattr(conn, "_peer_ip", None) in {_ip(address) for address in pinned}


class _GuardedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pool = _Pool()

    def https_open(self, req):
        if getattr(req, "_tunnel_host", None):
            return self.do_open(_GuardedHTTPS, req, context=self._context)
        return self._pooled_open(req)

    def _pooled_open(self, req):

        if not req.host:
            raise urllib.error.URLError("no host given")
        method = req.get_method().upper()
        headers = dict(req.unredirected_hdrs)
        headers.update({k: v for k, v in req.headers.items() if k not in headers})
        headers = {name.title(): value for name, value in headers.items()}
        fresh = _PooledHTTPS(req.host, timeout=req.timeout, context=self._context)
        fresh.set_debuglevel(self._debuglevel)
        key = (str(fresh.host or "").strip("[]").lower(), fresh.port)
        reused = self.pool.take(key, method)
        while True:
            conn = reused or fresh
            if reused is not None:
                conn.timeout = req.timeout
                with contextlib.suppress(OSError):
                    conn.sock.settimeout(req.timeout)
            try:
                try:
                    conn.request(method, req.selector, req.data, headers,
                                 encode_chunked=req.has_header("Transfer-encoding"))
                except OSError as err:
                    if reused is not None and method in _RETRIED_METHODS and isinstance(err, _STALE):
                        raise
                    raise urllib.error.URLError(err) from err
                response = conn.getresponse()
            except _STALE:
                _close_quietly(conn)
                if reused is None or method not in _RETRIED_METHODS:
                    raise
                reused = None
                continue
            except BaseException:
                _close_quietly(conn)
                raise
            break
        if conn.sock is not None and getattr(conn, "_peer_ip", None) is None:
            with contextlib.suppress(OSError, IndexError, TypeError):
                conn._peer_ip = _ip(conn.sock.getpeername()[0])
        pool = self.pool

        def done(finished: bool, _conn=conn, _key=key) -> None:
            if finished and _conn.sock is not None:
                pool.give(_key, _conn)
            else:
                _close_quietly(_conn)

        response._on_done = done
        response.url = req.get_full_url()
        response.msg = response.reason
        return response


def _drop_pool(opener) -> None:

    for handler in getattr(opener, "handlers", None) or ():
        if isinstance(handler, _GuardedHTTPSHandler):
            handler.pool.close(final=True)


def close_idle_connections() -> None:

    for handler in getattr(_OPENER, "handlers", None) or ():
        if isinstance(handler, _GuardedHTTPSHandler):
            handler.pool.close()


def _build_opener(*handlers):


    return urllib.request.build_opener(_GuardedHTTPHandler(),
                                       _GuardedHTTPSHandler(context=_ssl_context()), *handlers)






_OPENER = None


def _handlers(address: str | None) -> list:

    handlers = [_NoLocalRedirect()]
    if address:
        handlers.append(urllib.request.ProxyHandler({"http": address, "https": address}))
    return handlers


def _opener():





    global _OPENER
    opener = _OPENER
    if opener is None:
        with _PROXY_LOCK:
            if _OPENER is None:
                _OPENER = _build_opener(*_handlers(_PROXY_URL))
            opener = _OPENER
    return opener


def _redact_proxy(address: str) -> str:

    try:
        parts = urllib.parse.urlsplit(address)
    except ValueError:
        return "(proxy)"
    if not parts.hostname:
        return "(proxy)"
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{'***@' if parts.username else ''}{parts.hostname}{port}"


def set_proxy(address: str | None) -> bool:





    global _OPENER, _PROXY_URL
    address = (address or "").strip() or None
    with _PROXY_LOCK:
        if address == _PROXY_URL:
            return False
        old, _OPENER = _OPENER, _build_opener(*_handlers(address))
        _PROXY_URL = address
    _drop_pool(old)
    log_warning(f"Tool fetches now go through {_redact_proxy(address)}." if address
                else "Tool fetches now go out directly (no QGIS proxy).")
    return True


def proxy_in_use() -> str | None:

    with _PROXY_LOCK:
        return _redact_proxy(_PROXY_URL) if _PROXY_URL else None

























WITHDRAWN_HOSTS = {
    "gisco-services.ec.europa.eu":
        "Eurostat GISCO grants its boundary files on condition that the data is not used for "
        "commercial purposes, so this product does not serve them.",
    ".open-meteo.com":
        "Open-Meteo's free tier is for non-commercial use only and commercial use needs a paid key.",
    "basemaps.cartocdn.com": "CARTO has no keyless basemap left: the free tiles carry a watermark.",
    ".basemaps.cartocdn.com": "CARTO has no keyless basemap left: the free tiles carry a watermark.",
    "cartodb-basemaps-a.global.ssl.fastly.net": "CARTO has no keyless basemap left: the free tiles carry a watermark.",
    "cartodb-basemaps-b.global.ssl.fastly.net": "CARTO has no keyless basemap left: the free tiles carry a watermark.",
    "cartodb-basemaps-c.global.ssl.fastly.net": "CARTO has no keyless basemap left: the free tiles carry a watermark.",
    "cartodb-basemaps-d.global.ssl.fastly.net": "CARTO has no keyless basemap left: the free tiles carry a watermark.",
    "api.inaturalist.org": "iNaturalist observations are CC BY-NC per record.",
    ".opensky-network.org": "OpenSky's terms are non-commercial.",
    ".nrsc.gov.in": "Bhuvan is free to view and not to reuse.",
    "cartoweb.wms.ngi.be":
        "The Belgian NGI's CartoWeb is CC BY-NC and commercial use needs a paid subscription.",
}


def withdrawn_reason(host: str) -> str:












    host = str(host or "").strip().lower()
    keys = [host] + ["." + ".".join(host.split(".")[cut:]) for cut in range(0, len(host.split(".")) - 1)]
    try:
        from . import tuning

        served = tuning.withdrawn_hosts()
    except Exception:  # noqa: BLE001
        served = {}
    for table in (served, WITHDRAWN_HOSTS) if served else (WITHDRAWN_HOSTS,):
        for key in keys:
            reason = table.get(key)
            if reason:
                return reason
    return ""


def _explain_url_error(exc: urllib.error.URLError) -> urllib.error.URLError:







    if _transient(exc):




        return exc
    reason = str(getattr(exc, "reason", exc) or exc)
    lowered = reason.lower()


    timed_out = "timed out" in lowered or isinstance(getattr(exc, "reason", None), TimeoutError)
    if not timed_out and ("certificate" in lowered or "ssl" in lowered or "tls" in lowered):
        return urllib.error.URLError(
            f"{reason}. The server's TLS certificate was not accepted. If this host uses a private "
            "or company certificate authority, add it in QGIS Settings > Options > Authentication.")
    if proxy_in_use() and ("proxy" in lowered or "refused" in lowered or "timed out" in lowered):
        return urllib.error.URLError(
            f"{reason}. Requests go through the proxy set in QGIS Settings > Options > Network "
            f"({proxy_in_use()}); check that it is reachable and allows this host.")
    return exc
