# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
















from __future__ import annotations

import base64
import errno
import ipaddress
import os
import queue
import re
import selectors
import socket
import ssl
import threading
import time
from typing import Callable

from .ws_protocol import (
    DEFLATE_OFFER,
    MAX_MESSAGE_BYTES,
    OP_BINARY,
    OP_CLOSE,
    OP_CONT,
    OP_PING,
    OP_PONG,
    OP_TEXT,
    Deflate,
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
    http_status_of,
    parse_close,
    parse_http_head,
    parse_ws_url,
    read_frame_ext,
)
from .ws_proxy import resolve_proxy, resolve_tls

__all__ = ["WsConnection", "WsError", "WsHandshakeError", "WsConnectionLost", "WsClosing", "WsProtocolError",
           "parse_ws_url", "resolve_proxy", "resolve_tls"]






_CONNECT_TIMEOUT_S = 10.0




_ATTEMPT_DELAY_S = 0.25




QUIET_DNS_RETRIES_S = (1.0, 2.0)
_LAST_GOOD_BUDGET_S = 3.0
_LOOKUP_POLL_S = 0.02
_CLOSE_WAIT_S = 3.0
_POLL_S = 1.0
_SEND_TIMEOUT_S = 120.0
_SEND_CHUNK = 65536
_MESSAGE_FRAGMENT = 65536
IDLE_PING_S = 45.0
DEAD_AFTER_S = 90.0




LINK_FLOOR_BPS = 50_000
IN_FLIGHT_MAX_S = 40.0
MAX_QUEUED_FRAMES = 128
MAX_QUEUED_BYTES = 32 * 1024 * 1024







_NO_ROUTE_ERRNOS = frozenset(
    {getattr(errno, n) for n in ("ENETDOWN", "ENETUNREACH", "EHOSTUNREACH") if hasattr(errno, n)}
    | {10050, 10051, 10065})


def _tuned(kind: str, key: str, shipped, *bounds):
    try:
        from . import tuning
    except ImportError:
        return shipped
    return getattr(tuning, kind)(key, shipped, *bounds)
def _ms_since(started: float) -> int:
    return int(round((time.monotonic() - started) * 1000))


def _addresses(host: str, port: int, prefer: str = "") -> list:




    seen = set()
    found = []
    for family, _type, _proto, _name, sockaddr in socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM):
        if family in (socket.AF_INET, socket.AF_INET6) and (family, sockaddr[0]) not in seen:
            seen.add((family, sockaddr[0]))
            found.append((family, sockaddr))
    if not found:
        raise socket.gaierror(f"no IPv4 or IPv6 address for {host}")
    if prefer:
        found.sort(key=lambda info: info[1][0] != prefer)
    first = [info for info in found if info[0] == found[0][0]]
    other = [info for info in found if info[0] != found[0][0]]
    ordered = []
    for pair in zip(first, other):
        ordered.extend(pair)
    longer = first if len(first) > len(other) else other
    return ordered + longer[min(len(first), len(other)):]


def _ip_literal(text: str) -> bool:
    try:
        ipaddress.ip_address(str(text or "").split("%", 1)[0])
    except ValueError:
        return False
    return True


class _Lookup:



    def __init__(self, host: str, port: int, prefer: str):
        self._done = threading.Event()
        self._infos: list = []
        self._error: BaseException | None = None
        self._started = time.monotonic()
        self.ms: int | None = None
        threading.Thread(target=self._run, args=(host, port, prefer), name="ai-agent-dns",
                         daemon=True).start()

    def _run(self, host: str, port: int, prefer: str) -> None:
        try:
            self._infos = _addresses(host, port, prefer)
        except BaseException as exc:  # noqa: BLE001
            self._error = exc
        finally:
            self.ms = _ms_since(self._started)
            self._done.set()

    def done(self) -> bool:
        return self._done.is_set()

    def failed(self) -> bool:
        return self._done.is_set() and self._error is not None

    def wait(self, seconds: float) -> bool:
        return self._done.wait(max(0.0, seconds))

    def result(self) -> list:

        self._done.wait()
        if self._error is not None:
            raise self._error
        return list(self._infos)





_WAKE = ("wake", b"")
_WRITER_IDLE_S = 5.0







_TLS_LOCK = threading.Lock()
_TLS_CONTEXTS: dict = {}


def _tls_context(ca_pem: bytes | None, insecure: bool) -> ssl.SSLContext:
    key = (ca_pem or b"", bool(insecure))
    with _TLS_LOCK:
        ctx = _TLS_CONTEXTS.get(key)
        if ctx is None:
            if len(_TLS_CONTEXTS) >= 4:
                _TLS_CONTEXTS.clear()
            ctx = _TLS_CONTEXTS[key] = build_ssl_context(ca_pem, insecure)
        return ctx


def prewarm_tls(url: str) -> None:







    try:
        secure, host, port, _ = parse_ws_url(url)
        if not secure:
            return
        ca_pem, insecure = resolve_tls(host, port)
    except Exception:  # noqa: BLE001
        return

    def build() -> None:
        try:
            _tls_context(ca_pem, insecure)
        except Exception:  # nosec B110
            pass

    threading.Thread(target=build, name="ai-agent-tls-prewarm", daemon=True).start()


class _SharedTls:




















    def __init__(self, sock: ssl.SSLSocket, timeout: float):
        sock.setblocking(False)
        self._sock = sock
        self._timeout = timeout
        self._lock = threading.Lock()

    def _call(self, op, arg):
        deadline = time.monotonic() + self._timeout
        while True:
            with self._lock:
                try:
                    return op(arg)
                except ssl.SSLWantReadError:
                    event = selectors.EVENT_READ
                except ssl.SSLWantWriteError:
                    event = selectors.EVENT_WRITE
            left = deadline - time.monotonic()
            if left <= 0:
                raise socket.timeout("timed out")
            try:
                with selectors.DefaultSelector() as waiter:
                    waiter.register(self._sock, event)
                    waiter.select(left)
            except (ValueError, OSError) as exc:
                raise OSError(f"socket closed: {exc}") from exc

    def recv(self, size: int) -> bytes:
        return self._call(self._sock.recv, size)

    def send(self, data) -> int:
        return self._call(self._sock.send, data)

    def shutdown(self, how: int) -> None:
        with self._lock:
            self._sock.shutdown(how)

    def close(self) -> None:
        with self._lock:
            self._sock.close()







def _drop_kind(exc: BaseException) -> str:






    kind = str(getattr(exc, "kind", "") or "")
    if kind and kind != "lost":
        return kind
    cause = exc.__cause__
    if cause is None:
        return "eof"
    if isinstance(cause, ssl.SSLEOFError):
        return "ssl_eof"
    if isinstance(cause, ssl.SSLError):
        return "ssl"
    if isinstance(cause, ConnectionResetError):
        return "reset"
    if isinstance(cause, ConnectionAbortedError):
        return "aborted"
    if isinstance(cause, (socket.timeout, TimeoutError)):
        return "timeout"
    return "socket"

class WsConnection:




    def __init__(self, url: str, headers: dict | None = None, proxy: dict | None = None,
                 insecure: bool = False, connect_timeout: float = _CONNECT_TIMEOUT_S,
                 ca_pem: bytes | None = None, idle_ping_s: float = IDLE_PING_S,
                 dead_after_s: float = DEAD_AFTER_S, send_timeout: float = _SEND_TIMEOUT_S,
                 compress: bool = True, last_good: str = "", dns_retries: tuple = ()):
        self.url = url


        self.last_good = str(last_good or "")
        self.dns_retries = tuple(float(w) for w in dns_retries or ())




        self.report: dict = {}
        self.compress = compress
        self._deflate: Deflate | None = None
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
        self._outbox: queue.Queue = queue.Queue(
            maxsize=_tuned("ceiling", "ws_max_queued_frames", MAX_QUEUED_FRAMES, 16))
        self._control_outbox: queue.Queue = queue.Queue(maxsize=16)
        self._writer: threading.Thread | None = None
        self._send_error: WsError | None = None
        self._send_state = threading.Condition()
        self._outstanding = 0
        self._queued_bytes = 0
        self._closing_since: float | None = None
        self._close_sent = False


        self.closed_by = "none"
        self.drop_kind = ""
        self._writer_closed = False
        self._last_rx = 0.0
        self._last_tx = 0.0
        self._in_flight_until = 0.0
        self._ping_sent_at: float | None = None
        self.open = False



    def connect(self) -> None:
        secure, host, port, path = parse_ws_url(self.url)
        self.report = {"used_last_good": False, "dns_retries": 0}
        sock = self._open_tcp(host, port)
        self._sock = sock
        try:
            self._check_not_closed()
            if secure:
                started = time.monotonic()
                sock = self._wrap_tls(sock, host)
                self.report["tls_ms"] = _ms_since(started)
                self._sock = sock
                self._check_not_closed()
            sock.settimeout(self._poll_s())
            self._reader = SocketReader(sock, self._stop.is_set, self._on_idle)
            started = time.monotonic()
            self._handshake(host, port, path)
            self.report["upgrade_ms"] = _ms_since(started)
        except WsError as exc:
            self._close_socket()
            if self.report.get("dialed_early") and not self._stop.is_set():


                self.last_good = ""
                return self.connect()
            if self.report.get("used_last_good") and not self._stop.is_set():



                raise WsConnectionLost(f"cannot resolve {host}, and its last address failed: {exc}",
                                       "dns") from exc
            raise
        except Exception:
            self._close_socket()
            raise
        if secure:

            self._sock = self._reader.sock = _SharedTls(sock, _POLL_S)
        else:
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





        return min(_POLL_S, self.connect_timeout)

    @staticmethod
    def _no_delay(sock) -> None:









        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except (OSError, AttributeError):
            pass

    def _open_tcp(self, host: str, port: int):
        proxy = self.proxy
        if proxy:
            self.report["route"] = "proxy"
            target = f"{proxy['host']}:{proxy['port']}"
            try:
                started = time.monotonic()
                infos = _addresses(proxy["host"], int(proxy["port"]))
                self.report["dns_ms"] = _ms_since(started)
                started = time.monotonic()
                sock = self._race(infos)
            except socket.gaierror as exc:
                raise WsConnectionLost(f"proxy {target} not found: {exc}", "proxy_unreachable") from exc
            except OSError as exc:
                raise WsConnectionLost(f"cannot reach proxy {target}: {exc}", "proxy_unreachable") from exc
            self._no_delay(sock)
            sock.settimeout(self._poll_s())
            try:
                self._proxy_connect(sock, host, port, proxy)
            except Exception:
                sock.close()
                raise
            self.report["tcp_ms"] = _ms_since(started)
            sock.settimeout(self.connect_timeout)
            return sock
        self.report["route"] = "direct"
        try:
            sock = self._dial(host, port)
        except socket.timeout as exc:
            raise WsConnectionLost(f"timed out connecting to {host}:{port}", "timeout") from exc
        except OSError as exc:


            kind = "no_route" if getattr(exc, "errno", None) in _NO_ROUTE_ERRNOS else "unreachable"
            raise WsConnectionLost(f"cannot reach {host}:{port}: {exc}", kind) from exc
        self._no_delay(sock)
        sock.settimeout(self.connect_timeout)
        return sock

    def _dial(self, host: str, port: int):


        waits = (0.0,) + self.dns_retries
        last_good = self.last_good
        for tried, wait in enumerate(waits):
            if wait and self._stop.wait(wait):
                raise WsConnectionLost("closed while connecting")
            self.report["dns_retries"] = tried
            started = time.monotonic()
            if not tried and last_good and _ip_literal(last_good):



                lookup = _Lookup(host, port, last_good)
                try:
                    sock = self._race(_addresses(last_good, port), later=lookup)
                except socket.gaierror:
                    raise
                except OSError as exc:
                    try:
                        lookup.result()
                    except socket.gaierror as dns_exc:
                        failure = dns_exc
                        continue
                    raise exc
                self.report["tcp_ms"] = _ms_since(started)
                if lookup.failed():
                    self.report["used_last_good"] = True
                elif lookup.ms is not None:
                    self.report["dns_ms"] = lookup.ms
                return sock
            try:
                infos = _addresses(host, port, self.last_good)
            except socket.gaierror as exc:
                failure = exc
                if not last_good:
                    continue
                try:
                    infos = _addresses(last_good, port)
                    started = time.monotonic()


                    sock = self._race(infos, budget=min(self.connect_timeout, _LAST_GOOD_BUDGET_S))
                except socket.timeout:
                    last_good = ""
                    continue
                except OSError:
                    continue
                self.report.update(used_last_good=True, tcp_ms=_ms_since(started))
                return sock
            self.report["dns_ms"] = _ms_since(started)
            started = time.monotonic()
            sock = self._race(infos)
            self.report["tcp_ms"] = _ms_since(started)
            return sock
        raise WsConnectionLost(f"cannot resolve {host}: {failure}", "dns") from failure

    def _race(self, infos: list, budget: float | None = None, later: _Lookup | None = None):











        infos = list(infos)
        deadline = time.monotonic() + (self.connect_timeout if budget is None else budget)
        pending: dict = {}
        error: OSError | None = None
        winner = None
        next_at = 0.0
        index = 0
        selector = selectors.DefaultSelector()
        try:
            while winner is None:
                self._check_not_closed()
                now = time.monotonic()
                if later is not None and later.done():
                    try:
                        known = {info[1][0] for info in infos}
                        infos.extend(info for info in later.result() if info[1][0] not in known)
                    except socket.gaierror:
                        pass
                    later = None
                if index < len(infos) and (now >= next_at or not pending):
                    family, sockaddr = infos[index]
                    index += 1
                    try:
                        sock = socket.socket(family, socket.SOCK_STREAM)
                    except OSError as exc:
                        error = exc
                        continue
                    try:
                        sock.setblocking(False)
                        sock.connect(sockaddr)
                    except BlockingIOError:
                        selector.register(sock, selectors.EVENT_WRITE, family)
                        pending[sock] = family
                        next_at = now + _ATTEMPT_DELAY_S
                    except OSError as exc:
                        sock.close()
                        error = exc
                    else:
                        winner = sock, family
                    continue
                if not pending:
                    if later is not None and now < deadline:
                        later.wait(min(deadline - now, _POLL_S))
                        continue
                    raise error or OSError("no address to connect to")
                if now >= deadline:
                    raise socket.timeout("timed out")
                wake = min(deadline, now + _POLL_S, next_at if index < len(infos) else deadline)
                if later is not None:
                    wake = min(wake, now + _LOOKUP_POLL_S)
                for key, _ in selector.select(max(0.0, wake - now)):
                    sock = key.fileobj
                    selector.unregister(sock)
                    family = pending.pop(sock)
                    code = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                    if code == 0:
                        winner = sock, family
                        break
                    sock.close()
                    error = OSError(code, f"{os.strerror(code)} ({code})")
        finally:
            for sock in pending:
                sock.close()
            selector.close()
        sock, family = winner
        if later is not None:
            self.report["dialed_early"] = True
        sock.setblocking(True)
        self.report["ip_family"] = "ipv6" if family == socket.AF_INET6 else "ipv4"
        try:
            self.report["peer"] = str(sock.getpeername()[0])
        except OSError:
            pass
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
        ctx = _tls_context(self.ca_pem, self.insecure)
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
        if self.compress:
            lines.append(f"Sec-WebSocket-Extensions: {DEFLATE_OFFER}")
        for name, value in self.headers.items():
            name, value = str(name), str(value)
            if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) or any(
                    ord(c) < 32 or ord(c) == 127 for c in value):
                raise WsHandshakeError("invalid custom HTTP header")
            if name.lower() in {"host", "upgrade", "connection", "sec-websocket-key", "sec-websocket-version",
                                "sec-websocket-extensions"}:
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



        answer = headers.get("sec-websocket-extensions", "").strip()
        if answer:
            if not self.compress:
                raise WsHandshakeError(f"server enabled sec-websocket-extensions that was not offered: {answer}")
            self._deflate = Deflate.from_answer(answer)
        if headers.get("sec-websocket-protocol", "").strip():
            raise WsHandshakeError(
                f"server enabled sec-websocket-protocol that was not offered: {headers['sec-websocket-protocol']}")



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
            if self._queued_bytes + size > _tuned("ceiling", "ws_max_queued_bytes", MAX_QUEUED_BYTES,
                                                  16 * 1024 * 1024):
                raise WsConnectionLost("outgoing byte budget exceeded; reconnect to resume")
            self._outstanding += 1
            try:
                control = opcode in (OP_PING, OP_PONG, OP_CLOSE)
                target = self._control_outbox if control else self._outbox
                target.put_nowait((opcode, payload))
                if control and self._outbox.empty():
                    try:
                        self._outbox.put_nowait(_WAKE)
                    except queue.Full:
                        pass
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
                item = self._outbox.get(timeout=_WRITER_IDLE_S)
            except queue.Empty:
                continue
            except WsError:
                self.abort()
                return
            if item is None:
                return
            if item is _WAKE:
                continue
            with self._send_state:
                if isinstance(item[1], bytes):
                    self._queued_bytes = max(0, self._queued_bytes - len(item[1]))
            if self._stop.is_set():


                self._settle_send(WsConnectionLost("send abandoned: the socket closed first"))
                return
            payload = item[1]
            if callable(payload):




                try:
                    payload = self._produce(payload)
                except WsError as exc:
                    self._settle_send(exc)
                    continue
            try:
                self._write_message(item[0], payload)
            except WsError as exc:
                self._settle_send(exc)
                self.abort()
                return
            self._settle_send()
            if self._writer_closed:
                return

    def _write_controls(self) -> None:


        for _ in range(16):
            try:
                opcode, payload = self._control_outbox.get_nowait()
            except queue.Empty:
                return
            with self._send_state:
                self._queued_bytes = max(0, self._queued_bytes - len(payload))
            try:
                self._write_frame(encode_frame(opcode, payload, mask=True))
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








        rsv = 0
        deflate = self._deflate
        if deflate is not None and deflate.sends_compressed and opcode in (OP_TEXT, OP_BINARY):
            payload, rsv = deflate.compress(payload), 4
        if opcode not in (OP_TEXT, OP_BINARY) or len(payload) <= _MESSAGE_FRAGMENT:
            self._write_frame(encode_frame(opcode, payload, mask=True, rsv=rsv))
            return
        for start in range(0, len(payload), _MESSAGE_FRAGMENT):
            self._write_controls()
            if self._writer_closed:
                return
            end = min(len(payload), start + _MESSAGE_FRAGMENT)
            fragment = encode_frame(opcode if start == 0 else OP_CONT, payload[start:end],
                                    mask=True, fin=end == len(payload), rsv=rsv if start == 0 else 0)
            self._write_frame(fragment)

    @staticmethod
    def _produce(produce) -> bytes:
        try:
            text = produce()
        except Exception as exc:  # noqa: BLE001
            raise WsError(f"frame not encodable: {exc}") from exc
        try:
            data = str(text).encode("utf-8")
        except UnicodeError as exc:
            raise WsError("frame contains invalid Unicode") from exc
        if len(data) > MAX_MESSAGE_BYTES:
            raise WsError(f"message of {len(data)} bytes exceeds the 16 MiB limit")
        return data

    def _write_frame(self, frame: bytes) -> None:








        sock = self._sock
        if sock is None:
            raise WsConnectionLost("not connected")
        view = memoryview(frame)
        moved_at = time.monotonic()
        while view:
            if self._stop.is_set():
                raise WsConnectionLost("closed while sending")
            if time.monotonic() - moved_at >= self.send_timeout:
                raise WsConnectionLost(f"send timed out: nothing moved for {int(self.send_timeout)} s", "timeout")
            try:
                sent = sock.send(view[:_SEND_CHUNK])
            except (socket.timeout, ssl.SSLWantWriteError, ssl.SSLWantReadError):
                continue
            except (OSError, ssl.SSLError) as exc:
                raise WsConnectionLost(f"send failed: {exc}") from exc
            if sent <= 0:
                raise WsConnectionLost("socket closed while sending")
            now = time.monotonic()
            moved_at = now
            self._last_tx = now
            floor_bps = _tuned("threshold", "ws_link_floor_bps", LINK_FLOOR_BPS, 10_000, 1_000_000)
            self._in_flight_until = min(max(self._in_flight_until, now) + sent / floor_bps,
                                        now + _tuned("ceiling", "ws_in_flight_max_s", IN_FLIGHT_MAX_S, 5.0))
            view = view[sent:]

    def _drain(self, timeout: float) -> None:





        with self._send_state:
            self._send_state.wait_for(
                lambda: not self._outstanding or self._send_error is not None or self._stop.is_set(),
                timeout=max(0.0, timeout))

    def send_text(self, text: str) -> None:
        try:
            data = text.encode("utf-8")
        except UnicodeError as exc:
            raise WsError("frame contains invalid Unicode") from exc
        if len(data) > MAX_MESSAGE_BYTES:
            raise WsError(f"message of {len(data)} bytes exceeds the 16 MiB limit")
        self._enqueue(OP_TEXT, data)

    def send_lazy(self, produce: Callable[[], str]) -> None:

        self._enqueue(OP_TEXT, produce)

    def link_moved_since(self, since: float) -> tuple[bool, bool]:














        writing = (self._outstanding > 0 and self._last_tx >= since) or self._in_flight_until > time.monotonic()
        return self._last_byte_at() >= since, writing

    def _last_byte_at(self) -> float:

        reader = self._reader
        return max(self._last_rx, reader.last_rx if reader is not None else 0.0)

    def flush(self, timeout: float) -> bool:





        self._drain(timeout)
        return not self._outstanding

    def close(self, code: int = 1000, reason: str = "") -> None:


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

        for outbox in (self._outbox, self._control_outbox):
            while True:
                try:
                    item = outbox.get_nowait()
                except queue.Empty:
                    break
                if item is not None and item is not _WAKE:
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

        now = time.monotonic()
        if self._closing_since is not None and now - self._closing_since > _CLOSE_WAIT_S:
            raise WsConnectionLost("close timeout")
        if self._send_error is not None:
            raise self._send_error
        if not self.open:
            return
        idle = now - self._last_byte_at()
        if self.dead_after_s and idle > self.dead_after_s:
            raise WsConnectionLost(f"no data from the server for {int(idle)} s", "no_data")
        if self.idle_ping_s and idle > self.idle_ping_s and self._ping_sent_at is None:
            self._ping_sent_at = now
            try:
                self._enqueue(OP_PING, b"keepalive")
            except WsError:
                pass

    def _settled_send_error(self, grace: float = 5.0) -> WsError | None:
















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

        frag_opcode = None
        frag_compressed = False
        frag_buf = bytearray()
        code, reason = 1006, "connection lost"
        deflate = self._deflate
        try:
            while True:
                try:
                    fin, compressed, opcode, payload = read_frame_ext(self._reader, False, deflate is not None)
                except WsConnectionLost as exc:
                    if self._close_sent:
                        code, reason = 1000, ""
                        self.closed_by = "plugin"
                    else:
                        failed_send = self._settled_send_error()
                        reason = str(failed_send) if failed_send is not None else str(exc)
                        self.drop_kind = _drop_kind(failed_send or exc)
                    break
                self._last_rx = time.monotonic()
                self._ping_sent_at = None
                if opcode == OP_CLOSE:
                    code, reason = parse_close(payload)
                    self.closed_by = "plugin" if self._close_sent else "server"
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
                        if compressed:
                            payload = deflate.decompress(payload)
                        if opcode == OP_TEXT:
                            on_text(decode_text(payload))
                        continue
                    frag_opcode, frag_compressed, frag_buf = opcode, compressed, bytearray(payload)
                    continue
                if frag_opcode is None:
                    raise WsProtocolError("continuation frame without a start")
                frag_buf += payload
                if len(frag_buf) > MAX_MESSAGE_BYTES:
                    raise WsProtocolError("fragmented message exceeds the 16 MiB limit", 1009)
                if fin:
                    message = bytes(frag_buf)
                    if frag_compressed:
                        message = deflate.decompress(message)
                    if frag_opcode == OP_TEXT:
                        on_text(decode_text(message))
                    frag_opcode, frag_compressed, frag_buf = None, False, bytearray()
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






try:
    from qgis.PyQt.QtCore import QObject, QThread, pyqtSignal
    _QT_AVAILABLE = True
except ImportError:
    _QT_AVAILABLE = False

if _QT_AVAILABLE:

    class _WsThread(QThread):
        connected = pyqtSignal()
        disconnected = pyqtSignal(int, str)


        frame_received = pyqtSignal(str, float)
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
                code, reason = self.conn.run(self._received)
            except Exception as exc:
                self.conn.abort()
                code, reason = 1006, f"reader crashed: {exc}"
                self.failed.emit("lost", reason, 0)
            self.disconnected.emit(code, reason)

        def _received(self, text: str) -> None:

            self.frame_received.emit(text, time.perf_counter())

    class WsClient(QObject):



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
            self.closed_by = "none"
            self.drop_kind = ""


            self.arrived_at = 0.0

            self.connect_report: dict = {}

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

        def open(self, url: str, headers: dict | None = None, clocks: dict | None = None,
                 dial: dict | None = None) -> bool:




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
            clocks = clocks or {}
            dial = dial or {}
            self.connect_report = {}
            thread = _WsThread(WsConnection(
                url, headers or {}, proxy, insecure, ca_pem=ca_pem,
                connect_timeout=float(clocks.get("connect_timeout_s", _CONNECT_TIMEOUT_S)),
                idle_ping_s=float(clocks.get("idle_ping_s", IDLE_PING_S)),
                dead_after_s=float(clocks.get("dead_after_s", DEAD_AFTER_S)),
                send_timeout=float(clocks.get("send_timeout_s", _SEND_TIMEOUT_S)),
                last_good="" if proxy else str(dial.get("last_good") or ""),
                dns_retries=tuple(dial.get("dns_retries") or ())))
            thread.connected.connect(lambda t=thread: self._on_connected(t))
            thread.disconnected.connect(lambda code, reason, t=thread: self._on_disconnected(t, code, reason))
            thread.frame_received.connect(lambda text, at, t=thread: self._on_frame(t, text, at))
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

        def link_moved_since(self, since: float) -> tuple[bool, bool]:

            thread = self._thread
            if thread is None:
                return False, False
            return thread.conn.link_moved_since(since)

        def flush(self, wait_ms: int) -> bool:

            thread = self._thread
            if thread is None:
                return True
            return thread.conn.flush(max(0, int(wait_ms)) / 1000.0)

        def close(self, code: int = 1000, reason: str = "", wait_ms: int = 0) -> None:


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
            self.connect_report = dict(thread.conn.report)
            self._set_state("open")
            self.connected.emit()

        def _on_frame(self, thread, text: str, at: float = 0.0):
            if thread is self._thread:
                self.arrived_at = at
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
            self.closed_by = thread.conn.closed_by
            self.drop_kind = thread.conn.drop_kind
            self._set_state("closed")
            self.disconnected.emit(code, reason)
