# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The guarded opener: address checks, DNS pinning, redirects, TLS, the proxy, and the withdrawn hosts."""

from __future__ import annotations

import contextlib
import http.client
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request

from .logger import log_warning
from .net_failure import LocalUrlRefused, _transient
from .security import _as_address, refused_addresses, validate_url, vetted_addresses








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
    elif host and _as_address(host) is None:
        _pin_table()[host] = _RESOLVE_AT_CONNECT


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











    global _OPENER, _SSL_CONTEXT, _SSL_CA_PEM
    with _PROXY_LOCK:
        if ca_pem == _SSL_CA_PEM:
            return False
        _SSL_CA_PEM = ca_pem
        _SSL_CONTEXT = None




        _OPENER = None
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


def _handlers(address: str | None) -> list:
    """The redirect guard, and the proxy when one is set."""
    handlers = [_NoLocalRedirect()]
    if address:
        handlers.append(urllib.request.ProxyHandler({"http": address, "https": address}))
    return handlers


def _opener():
    """The guarded opener, built once, on the thread that first needs it."""




    global _OPENER
    opener = _OPENER
    if opener is None:
        with _PROXY_LOCK:
            if _OPENER is None:
                _OPENER = _build_opener(*_handlers(_PROXY_URL))
            opener = _OPENER
    return opener


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
        _OPENER = _build_opener(*_handlers(address))
        _PROXY_URL = address
    log_warning(f"Tool fetches now go through {_redact_proxy(address)}." if address
                else "Tool fetches now go out directly (no QGIS proxy).")
    return True


def proxy_in_use() -> str | None:
    """The proxy the guarded opener is using, with the credentials removed."""
    with _PROXY_LOCK:
        return _redact_proxy(_PROXY_URL) if _PROXY_URL else None





















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


def _explain_url_error(exc: urllib.error.URLError) -> urllib.error.URLError:
    """Turn urllib's transport errors into a sentence that names the fix."""






    if _transient(exc):




        return exc
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
