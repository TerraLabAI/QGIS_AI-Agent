# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""RFC 6455 WebSocket client on the standard library, wrapped in a QThread."""












from __future__ import annotations

import base64
import os
import queue
import re
import socket
import ssl
import threading
import time
from typing import Callable
from urllib.parse import urlsplit

from .ws_protocol import (
    MAX_MESSAGE_BYTES,
    OP_BINARY,
    OP_CLOSE,
    OP_CONT,
    OP_PING,
    OP_PONG,
    OP_TEXT,
    SocketReader,
    WsClosing,
    WsConnectionLost,
    WsError,
    WsHandshakeError,
    WsProtocolError,
    accept_key,
    build_ssl_context,
    close_payload,
    decode_text,
    encode_frame,
    env_proxy,
    http_status_of,
    no_proxy_matches,
    parse_close,
    parse_http_head,
    parse_ws_url,
    read_frame,
)

__all__ = ["WsConnection", "WsError", "WsHandshakeError", "WsConnectionLost", "WsClosing", "WsProtocolError",
           "parse_ws_url", "resolve_proxy", "resolve_tls"]

_CONNECT_TIMEOUT_S = 20.0
_CLOSE_WAIT_S = 3.0
_POLL_S = 1.0
_SEND_TIMEOUT_S = 120.0
_SEND_CHUNK = 65536
_MESSAGE_FRAGMENT = 65536
IDLE_PING_S = 45.0
DEAD_AFTER_S = 90.0
MAX_QUEUED_FRAMES = 128
MAX_QUEUED_BYTES = 32 * 1024 * 1024






class WsConnection:
    """One client connection."""



    def __init__(self, url: str, headers: dict | None = None, proxy: dict | None = None,
                 insecure: bool = False, connect_timeout: float = _CONNECT_TIMEOUT_S,
                 ca_pem: bytes | None = None, idle_ping_s: float = IDLE_PING_S,
                 dead_after_s: float = DEAD_AFTER_S, send_timeout: float = _SEND_TIMEOUT_S):
        self.url = url
        self.headers = dict(headers or {})
        self.proxy = proxy
        self.insecure = insecure
        self.ca_pem = ca_pem
        self.connect_timeout = connect_timeout
        self.idle_ping_s = idle_ping_s
        self.dead_after_s = dead_after_s
        self.send_timeout = send_timeout
        self._sock = None
        self._reader: SocketReader | None = None
        self._stop = threading.Event()
        self._outbox: queue.Queue = queue.Queue(maxsize=MAX_QUEUED_FRAMES)
        self._control_outbox: queue.Queue = queue.Queue(maxsize=16)
        self._writer: threading.Thread | None = None
        self._writing = False
        self._send_error: WsError | None = None
        self._send_state = threading.Condition()
        self._outstanding = 0
        self._queued_bytes = 0
        self._closing_since: float | None = None
        self._close_sent = False
        self._writer_closed = False
        self._last_rx = 0.0
        self._ping_sent_at: float | None = None
        self.open = False



    def connect(self) -> None:
        secure, host, port, path = parse_ws_url(self.url)
        sock = self._open_tcp(host, port)
        self._sock = sock
        try:
            self._check_not_closed()
            if secure:
                sock = self._wrap_tls(sock, host)
                self._sock = sock
                self._check_not_closed()
            sock.settimeout(self._poll_s())
            self._reader = SocketReader(sock, self._stop.is_set, self._on_idle)
            self._handshake(host, port, path)
        except Exception:
            self._close_socket()
            raise
        sock.settimeout(_POLL_S)
        self._last_rx = time.monotonic()
        self.open = True
        self._writer = threading.Thread(target=self._write_loop, name="ai-agent-ws-writer", daemon=True)
        try:
            self._writer.start()
        except RuntimeError as exc:
            self.abort()
            raise WsConnectionLost("could not start the socket writer") from exc

    def _check_not_closed(self) -> None:
        if self._stop.is_set():
            raise WsConnectionLost("closed while connecting")

    def _poll_s(self) -> float:
        """How long a handshake read may block before the stop flag is polled."""




        return min(_POLL_S, self.connect_timeout)

    def _open_tcp(self, host: str, port: int):
        proxy = self.proxy
        if proxy:
            target = f"{proxy['host']}:{proxy['port']}"
            try:
                sock = socket.create_connection((proxy["host"], int(proxy["port"])), self.connect_timeout)
            except socket.gaierror as exc:
                raise WsConnectionLost(f"proxy {target} not found: {exc}", "proxy_unreachable") from exc
            except OSError as exc:
                raise WsConnectionLost(f"cannot reach proxy {target}: {exc}", "proxy_unreachable") from exc
            sock.settimeout(self._poll_s())
            try:
                self._proxy_connect(sock, host, port, proxy)
            except Exception:
                sock.close()
                raise
            sock.settimeout(self.connect_timeout)
            return sock
        try:
            sock = socket.create_connection((host, port), self.connect_timeout)
        except socket.gaierror as exc:
            raise WsConnectionLost(f"cannot resolve {host}: {exc}", "dns") from exc
        except socket.timeout as exc:
            raise WsConnectionLost(f"timed out connecting to {host}:{port}", "timeout") from exc
        except OSError as exc:
            raise WsConnectionLost(f"cannot reach {host}:{port}: {exc}", "unreachable") from exc
        sock.settimeout(self.connect_timeout)
        return sock

    def _proxy_connect(self, sock, host: str, port: int, proxy: dict) -> None:
        authority = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        lines = [f"CONNECT {authority} HTTP/1.1", f"Host: {authority}", "Proxy-Connection: Keep-Alive"]
        if proxy.get("user"):
            cred = f"{proxy['user']}:{proxy.get('password', '')}".encode()
            lines.append("Proxy-Authorization: Basic " + base64.b64encode(cred).decode("ascii"))
        try:
            sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
        except OSError as exc:
            raise WsConnectionLost(f"proxy dropped the connection: {exc}", "proxy_unreachable") from exc
        reader = SocketReader(sock, self._stop.is_set)
        head = reader.read_until(b"\r\n\r\n", deadline=time.monotonic() + self.connect_timeout,
                                 what="waiting for the proxy")
        start, _ = parse_http_head(head)
        status = http_status_of(start)
        if status == 407:
            raise WsHandshakeError("the proxy requires authentication (HTTP 407)", "proxy_auth", 407)
        if not 200 <= status < 300:
            raise WsHandshakeError(f"proxy refused CONNECT: {start}", "proxy_refused", status)

    def _wrap_tls(self, sock, host: str):
        ctx = build_ssl_context(self.ca_pem, self.insecure)
        try:
            return ctx.wrap_socket(sock, server_hostname=host)
        except ssl.SSLCertVerificationError as exc:
            detail = getattr(exc, "verify_message", "") or str(exc)
            raise WsHandshakeError(f"certificate verification failed: {detail}", "tls") from exc
        except ssl.SSLError as exc:
            raise WsHandshakeError(f"TLS handshake failed: {exc}", "tls") from exc
        except socket.timeout as exc:
            raise WsHandshakeError("timed out during the TLS handshake", "timeout") from exc
        except (ConnectionResetError, ConnectionAbortedError) as exc:




            raise WsHandshakeError(
                f"the server closed the connection during the TLS handshake: {exc}", "tls") from exc
        except OSError as exc:
            raise WsConnectionLost(f"socket error during the TLS handshake: {exc}") from exc

    def _handshake(self, host: str, port: int, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        authority = f"[{host}]" if ":" in host else host
        default_port = 443 if self.url.startswith("wss:") else 80
        host_header = authority if port == default_port else f"{authority}:{port}"
        lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {host_header}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
        ]
        for name, value in self.headers.items():
            name, value = str(name), str(value)
            if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) or any(
                    ord(c) < 32 or ord(c) == 127 for c in value):
                raise WsHandshakeError("invalid custom HTTP header")
            if name.lower() in {"host", "upgrade", "connection", "sec-websocket-key", "sec-websocket-version"}:
                raise WsHandshakeError("custom header overrides WebSocket handshake")
            lines.append(f"{name}: {value}")
        try:
            self._sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8"))
        except (OSError, ssl.SSLError) as exc:
            raise WsConnectionLost(f"send failed during the upgrade: {exc}") from exc
        head = self._reader.read_until(b"\r\n\r\n", deadline=time.monotonic() + self.connect_timeout,
                                       what="waiting for the upgrade")
        start, headers = parse_http_head(head)
        status = http_status_of(start)
        if status != 101:
            if 300 <= status < 400:
                raise WsHandshakeError(f"server redirected the upgrade: {start}", "redirect", status)
            raise WsHandshakeError(f"server refused the upgrade: {start}", "http_status", status)
        if headers.get("upgrade", "").lower() != "websocket":
            raise WsHandshakeError("missing Upgrade: websocket")
        if "upgrade" not in {v.strip().lower() for v in headers.get("connection", "").split(",")}:
            raise WsHandshakeError("missing Connection: Upgrade")
        if headers.get("sec-websocket-accept") != accept_key(key):
            raise WsHandshakeError("bad Sec-WebSocket-Accept")


        for name in ("sec-websocket-extensions", "sec-websocket-protocol"):
            if headers.get(name, "").strip():
                raise WsHandshakeError(f"server enabled {name} that was not offered: {headers[name]}")



    def _enqueue(self, opcode: int, payload: bytes) -> None:
        if self._send_error is not None:
            raise self._send_error
        if self._sock is None or self._stop.is_set() or not self.open:
            raise WsConnectionLost("not connected")
        if self._close_sent and opcode not in (OP_PING, OP_PONG, OP_CLOSE):





            raise WsClosing()




        with self._send_state:
            if self._stop.is_set() or self._writer_closed:
                raise WsConnectionLost("not connected")
            size = len(payload) if isinstance(payload, bytes) else 0
            if self._queued_bytes + size > MAX_QUEUED_BYTES:
                raise WsConnectionLost("outgoing byte budget exceeded; reconnect to resume")
            self._outstanding += 1
            try:
                target = self._control_outbox if opcode in (OP_PING, OP_PONG, OP_CLOSE) else self._outbox
                target.put_nowait((opcode, payload))
            except queue.Full as exc:
                self._outstanding -= 1
                raise WsConnectionLost("outgoing queue full; reconnect to resume") from exc
            self._queued_bytes += size

    def _settle_send(self, error: WsError | None = None) -> None:
        with self._send_state:
            if error is not None and self._send_error is None:
                self._send_error = error
            self._outstanding = max(0, self._outstanding - 1)
            self._send_state.notify_all()

    def _write_loop(self) -> None:
        while True:
            try:
                self._write_controls()
                if self._writer_closed:
                    return
                item = self._outbox.get(timeout=0.1)
            except queue.Empty:
                continue
            except WsError:
                self.abort()
                return
            if item is None:
                return
            with self._send_state:
                if isinstance(item[1], bytes):
                    self._queued_bytes = max(0, self._queued_bytes - len(item[1]))
            if self._stop.is_set():


                self._settle_send(WsConnectionLost("send abandoned: the socket closed first"))
                return
            self._writing = True
            payload = item[1]
            if callable(payload):




                try:
                    payload = self._produce(payload)
                except WsError as exc:
                    self._writing = False
                    self._settle_send(exc)
                    continue
            try:
                self._write_message(item[0], payload)
            except WsError as exc:
                self._writing = False
                self._settle_send(exc)
                self.abort()
                return
            self._writing = False
            self._settle_send()
            if self._writer_closed:
                return

    def _write_controls(self, deadline: float | None = None) -> None:
        """Writer thread only: control frames may interrupt a fragmented message."""

        for _ in range(16):
            try:
                opcode, payload = self._control_outbox.get_nowait()
            except queue.Empty:
                return
            with self._send_state:
                self._queued_bytes = max(0, self._queued_bytes - len(payload))
            try:
                self._write_frame(encode_frame(opcode, payload, mask=True), deadline)
            except WsError as exc:
                self._settle_send(exc)
                raise
            self._settle_send()
            if opcode == OP_CLOSE:


                with self._send_state:
                    self._writer_closed = True
                    self._discard_queued()
                    self._send_state.notify_all()
                return

    def _write_message(self, opcode: int, payload: bytes) -> None:
        """Fragment data, with one deadline for the complete message."""






        deadline = time.monotonic() + self.send_timeout
        if opcode not in (OP_TEXT, OP_BINARY) or len(payload) <= _MESSAGE_FRAGMENT:
            self._write_frame(encode_frame(opcode, payload, mask=True), deadline)
            return
        for start in range(0, len(payload), _MESSAGE_FRAGMENT):
            self._write_controls(deadline)
            if self._writer_closed:
                return
            end = min(len(payload), start + _MESSAGE_FRAGMENT)
            fragment = encode_frame(opcode if start == 0 else OP_CONT, payload[start:end],
                                    mask=True, fin=end == len(payload))
            self._write_frame(fragment, deadline)

    @staticmethod
    def _produce(produce) -> bytes:
        try:
            text = produce()
        except Exception as exc:  # noqa: BLE001 - reported as a send error for this frame
            raise WsError(f"frame not encodable: {exc}") from exc
        try:
            data = str(text).encode("utf-8")
        except UnicodeError as exc:
            raise WsError("frame contains invalid Unicode") from exc
        if len(data) > MAX_MESSAGE_BYTES:
            raise WsError(f"message of {len(data)} bytes exceeds the 16 MiB limit")
        return data

    def _write_frame(self, frame: bytes, deadline: float | None = None) -> None:
        """Chunked sends with a total deadline."""

        sock = self._sock
        if sock is None:
            raise WsConnectionLost("not connected")
        view = memoryview(frame)
        if deadline is None:
            deadline = time.monotonic() + self.send_timeout
        while view:
            if self._stop.is_set():
                raise WsConnectionLost("closed while sending")
            if time.monotonic() >= deadline:
                raise WsConnectionLost(f"send timed out after {int(self.send_timeout)} s", "timeout")
            try:
                sent = sock.send(view[:_SEND_CHUNK])
            except (socket.timeout, ssl.SSLWantWriteError, ssl.SSLWantReadError):
                if time.monotonic() > deadline:
                    raise WsConnectionLost(f"send timed out after {int(self.send_timeout)} s", "timeout") from None
                continue
            except (OSError, ssl.SSLError) as exc:
                raise WsConnectionLost(f"send failed: {exc}") from exc
            if sent <= 0:
                raise WsConnectionLost("socket closed while sending")
            view = view[sent:]

    def _drain(self, timeout: float) -> None:
        """Give the writer a moment to flush what is queued (a close frame)."""
        end = time.monotonic() + timeout
        while self._outstanding and time.monotonic() < end:
            if self._send_error is not None or self._stop.is_set():
                return
            time.sleep(0.02)

    def send_text(self, text: str) -> None:
        try:
            data = text.encode("utf-8")
        except UnicodeError as exc:
            raise WsError("frame contains invalid Unicode") from exc
        if len(data) > MAX_MESSAGE_BYTES:
            raise WsError(f"message of {len(data)} bytes exceeds the 16 MiB limit")
        self._enqueue(OP_TEXT, data)

    def send_lazy(self, produce: Callable[[], str]) -> None:
        """Queue a text frame whose text ``produce()`` builds on the writer thread."""
        self._enqueue(OP_TEXT, produce)

    def send_ping(self, payload: bytes = b"") -> None:
        self._enqueue(OP_PING, payload[:125])

    def close(self, code: int = 1000, reason: str = "") -> None:
        """Start the close handshake."""

        if self._close_sent:
            return
        self._close_sent = True
        self._closing_since = time.monotonic()
        try:
            self._enqueue(OP_CLOSE, close_payload(code, reason))
        except WsError:
            self.abort()

    def abort(self) -> None:
        self._stop.set()
        self.open = False

        with self._send_state:
            self._discard_queued()
            self._outbox.put_nowait(None)
            self._queued_bytes = 0
            self._send_state.notify_all()
        self._close_socket()

    def _discard_queued(self) -> None:
        """Called with _send_state held, so no producer races the drain."""
        for outbox in (self._outbox, self._control_outbox):
            while True:
                try:
                    item = outbox.get_nowait()
                except queue.Empty:
                    break
                if item is not None:
                    self._outstanding = max(0, self._outstanding - 1)
        self._queued_bytes = 0

    def _close_socket(self) -> None:
        sock, self._sock = self._sock, None
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass



    def _on_idle(self) -> None:
        """Runs on the reader thread on every 1 s poll without data."""
        now = time.monotonic()
        if self._closing_since is not None and now - self._closing_since > _CLOSE_WAIT_S:
            raise WsConnectionLost("close timeout")
        if self._send_error is not None:
            raise self._send_error
        if not self.open:
            return
        idle = now - self._last_rx
        if self.dead_after_s and idle > self.dead_after_s:
            raise WsConnectionLost(f"no data from the server for {int(idle)} s", "lost")
        if self.idle_ping_s and idle > self.idle_ping_s and self._ping_sent_at is None:
            self._ping_sent_at = now
            try:
                self._enqueue(OP_PING, b"keepalive")
            except WsError:
                pass

    def _settled_send_error(self, grace: float = 5.0) -> WsError | None:
        """The writer's verdict on a send that was in flight, waiting briefly for it."""















        with self._send_state:
            if self._send_error is not None:
                return self._send_error
            if not self._outstanding:
                return None
            self._send_state.wait_for(lambda: self._send_error is not None or not self._outstanding,
                                      timeout=grace)
            return self._send_error

    def run(self, on_text: Callable[[str], None],
            on_pong: Callable[[bytes], None] | None = None) -> tuple[int, str]:
        """Blocking reader. Returns the close (code, reason) once the socket is done."""
        frag_opcode = None
        frag_buf = bytearray()
        code, reason = 1006, "connection lost"
        try:
            while True:
                try:
                    fin, opcode, payload = read_frame(self._reader, require_mask=False)
                except WsConnectionLost as exc:
                    if self._close_sent:
                        code, reason = 1000, ""
                    else:
                        failed_send = self._settled_send_error()
                        reason = str(failed_send) if failed_send is not None else str(exc)
                    break
                self._last_rx = time.monotonic()
                self._ping_sent_at = None
                if opcode == OP_CLOSE:
                    code, reason = parse_close(payload)
                    if not self._close_sent:
                        self._close_sent = True
                        try:
                            self._enqueue(OP_CLOSE, close_payload(code if code != 1005 else 1000, ""))
                            self._drain(1.0)
                        except WsError:
                            pass
                    break
                if opcode == OP_PING:
                    try:
                        self._enqueue(OP_PONG, payload)
                    except WsError:
                        break
                    continue
                if opcode == OP_PONG:
                    if on_pong:
                        on_pong(payload)
                    continue
                if opcode in (OP_TEXT, OP_BINARY):
                    if frag_opcode is not None:
                        raise WsProtocolError("new data frame inside a fragmented message")
                    if fin:
                        if opcode == OP_TEXT:
                            on_text(decode_text(payload))
                        continue
                    frag_opcode, frag_buf = opcode, bytearray(payload)
                    continue
                if frag_opcode is None:
                    raise WsProtocolError("continuation frame without a start")
                frag_buf += payload
                if len(frag_buf) > MAX_MESSAGE_BYTES:
                    raise WsProtocolError("fragmented message exceeds the 16 MiB limit", 1009)
                if fin:
                    if frag_opcode == OP_TEXT:
                        on_text(decode_text(bytes(frag_buf)))
                    frag_opcode, frag_buf = None, bytearray()
        except WsProtocolError as exc:
            code, reason = exc.code, str(exc)
            if not self._close_sent:
                self._close_sent = True
                try:
                    self._enqueue(OP_CLOSE, close_payload(code, reason))
                    self._drain(1.0)
                except WsError:
                    pass
        finally:
            self.abort()
        return code, reason






def _excluded(url: str, host: str, entries) -> bool:
    """QGIS exclude entries are URL prefixes; a bare host name also counts."""
    for entry in entries or []:
        entry = str(entry or "").strip()
        if not entry:
            continue
        if url.startswith(entry.lower()):
            return True
        name = (urlsplit(entry if "://" in entry else "//" + entry).hostname or entry).lower().rstrip(".")
        if name and (host == name or host.endswith("." + name)):
            return True
    return False


def _qt_proxy_dict(proxy) -> dict | None:
    """A QNetworkProxy as the plain dict, None unless it is an HTTP proxy with a host."""
    from qgis.PyQt.QtNetwork import QNetworkProxy
    ptype = getattr(QNetworkProxy, "ProxyType", QNetworkProxy)
    if proxy is None or proxy.type() not in (ptype.HttpProxy, ptype.HttpCachingProxy):
        return None
    if not proxy.hostName():
        return None
    return {"host": proxy.hostName(), "port": int(proxy.port()),
            "user": proxy.user(), "password": proxy.password()}


def _system_proxy(url: str) -> dict | None:
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtNetwork import QNetworkProxyFactory, QNetworkProxyQuery
    for proxy in QNetworkProxyFactory.systemProxyForQuery(QNetworkProxyQuery(QUrl(url))) or []:
        found = _qt_proxy_dict(proxy)
        if found:
            return found
    return None










_RESOLVE_TTL_S = 60.0
_resolve_cache: dict = {}


def _resolved(key: tuple, produce):
    """``produce()``, at most once per _RESOLVE_TTL_S for this key."""
    now = time.monotonic()
    hit = _resolve_cache.get(key)
    if hit is None or now - hit[0] >= _RESOLVE_TTL_S:
        hit = (now, produce())
        _resolve_cache[key] = hit
    value = hit[1]
    return dict(value) if isinstance(value, dict) else value


def forget_resolved_network() -> None:
    """Drop the cached proxy and CA answers. Called when the settings change."""
    _resolve_cache.clear()


def resolve_proxy(host: str, port: int = 443, secure: bool = True) -> dict | None:
    """The HTTP proxy QGIS would use for this host, as a plain dict, or None."""




    host = (host or "").lower()
    return _resolved(("proxy", host, port, secure), lambda: _resolve_proxy_now(host, port, secure))


def _resolve_proxy_now(host: str, port: int, secure: bool) -> dict | None:
    url = f"{'https' if secure else 'http'}://{host}:{port}/"
    if no_proxy_matches(host, os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""):
        return None
    try:
        from qgis.core import QgsNetworkAccessManager
        from qgis.PyQt.QtNetwork import QNetworkProxy
    except ImportError:
        return env_proxy()
    try:
        nam = QgsNetworkAccessManager.instance()
        if _excluded(url, host, getattr(nam, "noProxyList", list)()):
            return None
        excluded = _excluded(url, host, getattr(nam, "excludeList", list)())
        fallback = nam.fallbackProxy() if hasattr(nam, "fallbackProxy") else nam.proxy()
        ptype = getattr(QNetworkProxy, "ProxyType", QNetworkProxy)
        use_system = excluded or bool(getattr(nam, "useSystemProxy", lambda: False)()) \
            or fallback.type() == ptype.DefaultProxy
        if not excluded:
            found = _qt_proxy_dict(fallback)
            if found:
                return found
        if use_system:
            found = _system_proxy(url)
            if found:
                return found
        return env_proxy()
    except Exception:
        return env_proxy()


def _qgis_ignores_cert_errors(manager, host: str, port: int) -> bool:
    """True only when the user told QGIS to ignore SSL errors for this host."""
    hostport = f"{host}:{port}"
    try:
        config = manager.sslCertCustomConfigByHost(hostport)
        if config is not None and not config.isNull() and config.sslIgnoredErrorEnums():
            return True
    except Exception:  # nosec B110 - no custom SSL config is the normal case, not an error
        pass
    try:
        cache = manager.ignoredSslErrorCache()
        return bool(cache and cache.get(hostport))
    except Exception:
        return False


def resolve_tls(host: str, port: int = 443) -> tuple[bytes | None, bool]:
    """(PEM bundle of the CAs QGIS trusts or None, verification off)."""





    return _resolved(("tls", host, port), lambda: _resolve_tls_now(host, port))


def _resolve_tls_now(host: str, port: int) -> tuple[bytes | None, bool]:
    insecure = False
    ca_pem: bytes | None = None
    try:
        from qgis.core import QgsApplication
        manager = QgsApplication.authManager()
        if manager is not None and hasattr(manager, "trustedCaCertsPemText"):
            ca_pem = bytes(manager.trustedCaCertsPemText()) or None
        if manager is not None and not insecure:
            insecure = _qgis_ignores_cert_errors(manager, host, port)
    except Exception:  # nosec B110 - falls back to the system CA list, which is the safe direction
        pass
    if ca_pem is None:
        try:
            from qgis.PyQt.QtNetwork import QSslConfiguration
            certs = QSslConfiguration.defaultConfiguration().caCertificates()
            ca_pem = b"".join(bytes(cert.toPem()) for cert in certs) or None
        except Exception:
            ca_pem = None
    return ca_pem, insecure






try:
    from qgis.PyQt.QtCore import QObject, QThread, pyqtSignal
    _QT_AVAILABLE = True
except ImportError:
    _QT_AVAILABLE = False

if _QT_AVAILABLE:

    class _WsThread(QThread):
        connected = pyqtSignal()
        disconnected = pyqtSignal(int, str)
        frame_received = pyqtSignal(str)
        failed = pyqtSignal(str, str, int)

        def __init__(self, conn: WsConnection):
            super().__init__()
            self.conn = conn

        def run(self):
            try:
                self.conn.connect()
            except WsError as exc:
                self.failed.emit(exc.kind or "lost", str(exc), int(exc.status or 0))
                self.disconnected.emit(1006, str(exc))
                return
            except Exception as exc:
                self.failed.emit("lost", f"connect failed: {exc}", 0)
                self.disconnected.emit(1006, str(exc))
                return
            if self.conn._stop.is_set():
                self.conn.abort()
                self.disconnected.emit(1000, "closed before open")
                return
            self.connected.emit()
            try:
                code, reason = self.conn.run(self.frame_received.emit)
            except Exception as exc:
                self.conn.abort()
                code, reason = 1006, f"reader crashed: {exc}"
                self.failed.emit("lost", reason, 0)
            self.disconnected.emit(code, reason)

    class WsClient(QObject):
        """Main-thread handle."""


        connected = pyqtSignal()
        disconnected = pyqtSignal(int, str)
        frame_received = pyqtSignal(str)
        error_occurred = pyqtSignal(str)
        connect_failed = pyqtSignal(str, str, int)
        state_changed = pyqtSignal(str)

        def __init__(self, parent=None):
            super().__init__(parent)
            self._thread: _WsThread | None = None
            self._retired: list = []
            self._state = "closed"
            self.proxy_label = ""

        @property
        def state(self) -> str:
            return self._state

        @property
        def is_open(self) -> bool:
            thread = self._thread
            return bool(thread and thread.conn.open and self._state == "open")

        def _set_state(self, state: str):
            if state != self._state:
                self._state = state
                self.state_changed.emit(state)

        def open(self, url: str, headers: dict | None = None) -> bool:
            thread = self._thread
            if thread is not None and thread.isRunning():
                if self._state != "closing":
                    return False
                self._retire(thread)
            try:
                secure, host, port, _ = parse_ws_url(url)
            except WsError as exc:
                self._set_state("closed")
                self.error_occurred.emit(str(exc))
                self.connect_failed.emit("url", str(exc), 0)
                self.disconnected.emit(1006, str(exc))
                return True
            proxy = resolve_proxy(host, port, secure)
            self.proxy_label = f"{proxy['host']}:{proxy['port']}" if proxy else ""
            ca_pem, insecure = resolve_tls(host, port) if secure else (None, False)
            thread = _WsThread(WsConnection(url, headers or {}, proxy, insecure, ca_pem=ca_pem))
            thread.connected.connect(lambda t=thread: self._on_connected(t))
            thread.disconnected.connect(lambda code, reason, t=thread: self._on_disconnected(t, code, reason))
            thread.frame_received.connect(lambda text, t=thread: self._on_frame(t, text))
            thread.failed.connect(lambda kind, message, status, t=thread: self._on_failed(t, kind, message, status))
            self._thread = thread
            self._set_state("connecting")
            thread.start()
            return True

        def send_text(self, text: str) -> bool:
            thread = self._thread
            if thread is None or not thread.conn.open or self._state != "open":
                return False
            try:
                thread.conn.send_text(text)
                return True
            except WsClosing:
                return False
            except WsError as exc:
                self.error_occurred.emit(str(exc))
                thread.conn.abort()
                return False

        def send_lazy(self, produce) -> bool:
            """Queue a frame built on the writer thread. False when the socket is not open."""
            thread = self._thread
            if thread is None or not thread.conn.open or self._state != "open":
                return False
            try:
                thread.conn.send_lazy(produce)
                return True
            except WsClosing:
                return False
            except WsError as exc:
                self.error_occurred.emit(str(exc))
                thread.conn.abort()
                return False

        def close(self, code: int = 1000, reason: str = "", wait_ms: int = 0) -> None:
            """Non-blocking by default."""

            thread = self._thread
            if thread is None:
                return
            self._set_state("closing")
            thread.conn.close(code, reason)
            if wait_ms > 0 and thread.isRunning() and not thread.wait(int(wait_ms)):
                thread.conn.abort()
                thread.wait(1000)

        def _retire(self, thread) -> None:
            thread.conn.abort()
            self._retired.append(thread)
            self._thread = None

        def _on_connected(self, thread):
            if thread is not self._thread:
                return
            self._set_state("open")
            self.connected.emit()

        def _on_frame(self, thread, text: str):
            if thread is self._thread:
                self.frame_received.emit(text)

        def _on_failed(self, thread, kind: str, message: str, status: int):
            if thread is not self._thread:
                return
            self.error_occurred.emit(message)
            self.connect_failed.emit(kind, message, status)

        def _on_disconnected(self, thread, code: int, reason: str):
            thread.wait(1000)
            thread.deleteLater()
            if thread is not self._thread:
                if thread in self._retired:
                    self._retired.remove(thread)
                return
            self._thread = None
            self._set_state("closed")
            self.disconnected.emit(code, reason)
