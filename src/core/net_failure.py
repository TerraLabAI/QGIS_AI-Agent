# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""A failed fetch: its exception classes, what it says, and how its body is decoded."""

from __future__ import annotations

import contextlib
import errno
import http.client
import socket
import urllib.error
import urllib.parse
import zlib

from .logger import log_warning


class LocalUrlRefused(urllib.error.URLError):
    """The URL, or a redirect on the way, points somewhere the plugin must not fetch."""


class FetchCancelled(urllib.error.URLError):
    """The run was stopped while the body was still arriving."""


class FetchTooLarge(urllib.error.URLError):
    """The body went past the caller's cap."""


class FetchDeadline(urllib.error.URLError):
    """The whole fetch took longer than the caller allowed."""


class FetchTooSlow(urllib.error.URLError):
    """The body fits the size cap, and this connection cannot deliver it in time."""








class FetchDropped(urllib.error.URLError):
    """The connection broke before the answer arrived whole: cut, reset or closed on the way."""








    def __init__(self, reason, host: str = ""):
        super().__init__(reason)
        self.host = host

    def __str__(self) -> str:
        return str(self.reason)


class FetchTruncated(FetchDropped):
    """The connection ended before the server sent the length it declared."""


def _is_drop(exc: BaseException) -> bool:
    """A connection that broke under an answer, as the socket or http.client raises it."""
    import ssl

    return isinstance(exc, (http.client.IncompleteRead, ConnectionResetError, ConnectionAbortedError,
                            BrokenPipeError, ssl.SSLEOFError))


def _dropped(url: str, exc: BaseException) -> FetchDropped:
    host = _hostname(url)
    if isinstance(exc, http.client.RemoteDisconnected):
        how = "was closed before any answer came back"
    elif isinstance(exc, http.client.IncompleteRead):
        how = "was cut before the answer was complete"
    elif isinstance(exc, (ConnectionResetError, ConnectionAbortedError)):
        how = "was reset while the answer was on its way"
    else:
        how = "broke while the answer was on its way"
    return FetchDropped(f"The connection to {host or 'the host'} {how} ({type(exc).__name__}).", host)


def _name_host(exc: BaseException, host: str) -> None:
    """Put the host on a failure that left ``fetch`` without one, for ``describe_failure``."""
    if host and not getattr(exc, "host", ""):
        with contextlib.suppress(AttributeError, TypeError):
            exc.host = host


def _arrived(byte_count: int, seconds: float) -> str:
    """How much of an answer came in, and how fast: what tells a slow link from a silent host."""
    if byte_count <= 0:
        return "no byte of the answer had arrived"
    size = (f"{byte_count / (1024 * 1024):.1f} MB" if byte_count >= 1024 * 1024
            else f"{max(1, round(byte_count / 1024))} kB")
    rate = byte_count / 1024 / seconds if seconds > 0 else 0.0
    return f"{size} had arrived, at {rate:.0f} kB/s"


def _transient(exc: BaseException) -> bool:
    """A failure an unstable link produces and a second try a few seconds later can get past."""






    if isinstance(exc, (FetchCancelled, FetchTooLarge, FetchDeadline, FetchTooSlow, FetchWithdrawn,
                        NetworkUnreachable, LocalUrlRefused)):
        return False
    if isinstance(exc, FetchDropped) or _is_drop(exc):
        return True
    reason = getattr(exc, "reason", None)
    if isinstance(reason, socket.gaierror) or (reason is not None and _is_drop(reason)):
        return True
    return (isinstance(reason, OSError) and not isinstance(reason, (socket.timeout, ConnectionRefusedError))
            and reason.errno in _NO_ROUTE_ERRNOS and reason.errno != errno.ECONNREFUSED)




NETWORK_ERROR = "NETWORK_ERROR"
NETWORK_SUGGESTION = ("Nothing from this call was used. Tell the user what broke in the message's own terms "
                      "(their connection, not the data service, unless the message says a service "
                      "answered). Try the same call once more; if it fails again, ask the user to check "
                      "their connection and say when to retry.")


def describe_failure(exc: BaseException) -> str | None:
    """A plain sentence for a request the network failed, or None for any other failure."""







    host = str(getattr(exc, "host", "") or "")
    where = host or "the host"
    if isinstance(exc, urllib.error.HTTPError):
        return None
    if isinstance(exc, NetworkUnreachable):
        return str(exc.reason)
    if isinstance(exc, FetchDropped):
        return (f"{exc.reason} The connection between this computer and {where} dropped, as an unstable "
                "connection does; no error came from the service.")
    if _is_drop(exc):
        return describe_failure(_dropped(f"http://{host}/" if host else "", exc))
    reason = getattr(exc, "reason", None) if isinstance(exc, urllib.error.URLError) else exc
    if reason is not exc and reason is not None and _is_drop(reason):
        return describe_failure(_dropped(f"http://{host}/" if host else "", reason))
    if isinstance(reason, socket.gaierror):
        return f"This computer could not look up {where}: its internet connection or its DNS is not working."
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return f"{where} sent nothing for too long: the connection, or the service, is too slow to answer."
    if (isinstance(reason, OSError) and reason.errno in _NO_ROUTE_ERRNOS
            and reason.errno != errno.ECONNREFUSED):
        return f"This computer has no route to {where}: its network is down."
    return None


class FetchWithdrawn(urllib.error.URLError):
    """The host serves data this product may not offer, so nothing was sent."""


class NetworkUnreachable(urllib.error.URLError):
    """Several requests in a row never reached a server, so this one was not sent."""


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
