# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""RFC 6455 byte level, plain Python: frames, masking, the HTTP head, errors."""








from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import struct
import time
from typing import Callable
from urllib.parse import quote, unquote, urlsplit

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME_BYTES = 16 * 1024 * 1024
MAX_MESSAGE_BYTES = 16 * 1024 * 1024

OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class WsError(Exception):
    """Base for every failure the connection raises. `kind` names the cause."""

    def __init__(self, message: str, kind: str = "", status: int = 0):
        super().__init__(message)
        self.kind = kind
        self.status = status


class WsHandshakeError(WsError):
    def __init__(self, message: str, kind: str = "handshake", status: int = 0):
        super().__init__(message, kind, status)


class WsConnectionLost(WsError):
    def __init__(self, message: str, kind: str = "lost", status: int = 0):
        super().__init__(message, kind, status)


class WsClosing(WsConnectionLost):
    """The close handshake has begun: this frame will never be written."""






    def __init__(self, message: str = "the connection is closing"):
        super().__init__(message, "closing")


class WsProtocolError(WsError):
    def __init__(self, message: str, code: int = 1002):
        super().__init__(message, "protocol")
        self.code = code






def parse_ws_url(url: str) -> tuple[bool, str, int, str]:
    """Return (secure, host, port, path_with_query) for a ws:// or wss:// URL."""
    if not isinstance(url, str) or any(ord(c) < 33 or ord(c) == 127 for c in url):
        raise WsError("WebSocket URL contains whitespace or control characters", "url")
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise WsError("WebSocket URL is malformed", "url") from exc
    if parts.scheme not in ("ws", "wss"):
        raise WsError(f"Unsupported URL scheme: {parts.scheme!r}", "url")
    if not parts.hostname:
        raise WsError("WebSocket URL has no host", "url")
    if parts.username is not None or parts.password is not None or parts.fragment:
        raise WsError("WebSocket URL cannot contain credentials or a fragment", "url")
    secure = parts.scheme == "wss"
    try:
        port = parts.port if parts.port is not None else (443 if secure else 80)
        if port == 0:
            raise ValueError("port must be positive")
    except ValueError as exc:
        raise WsError(f"WebSocket URL has a bad port: {exc}", "url") from exc
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if parts.query:
        path += "?" + quote(parts.query, safe="/%?:@!$&'()*+,;=-._~")
    try:
        host = parts.hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise WsError("WebSocket URL has an invalid host", "url") from exc
    return secure, host, port, path


def parse_proxy_url(raw: str) -> dict | None:
    """A proxy URL to the proxy dict, or None."""





    raw = (raw or "").strip()
    if not raw:
        return None
    if any(ord(c) < 32 or ord(c) == 127 for c in raw):
        return None
    try:
        parts = urlsplit(raw if "://" in raw else "http://" + raw)
    except ValueError:
        return None
    if parts.scheme != "http":
        return None
    if not parts.hostname:
        return None
    try:
        port = parts.port if parts.port is not None else 8080
        if port == 0:
            return None
    except ValueError:
        return None
    return {"host": parts.hostname, "port": port,
            "user": unquote(parts.username or ""), "password": unquote(parts.password or "")}


def env_proxy(environ=None) -> dict | None:
    """HTTPS_PROXY / https_proxy / ALL_PROXY, the way curl reads them."""
    env = os.environ if environ is None else environ
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        proxy = parse_proxy_url(env.get(name, ""))
        if proxy:
            return proxy
    return None


def no_proxy_matches(host: str, raw: str) -> bool:
    """True when NO_PROXY (`*`, `example.com`, `.example.com`, `host:port`) names the host."""
    host = (host or "").lower().rstrip(".")
    for entry in (raw or "").split(","):
        entry = entry.strip().lower()
        if not entry:
            continue
        if entry == "*":
            return True
        if entry.startswith("["):
            entry = entry[1:].partition("]")[0]
        elif entry.count(":") == 1:
            entry = entry.split(":", 1)[0]
        entry = entry.lstrip(".").rstrip(".")
        if entry and (host == entry or host.endswith("." + entry)):
            return True
    return False


def build_ssl_context(ca_pem: bytes | None = None, insecure: bool = False) -> ssl.SSLContext:
    """The system trust store, plus the CA list QGIS trusts when one is given."""
    ctx = ssl.create_default_context()
    if ca_pem:
        try:
            ctx.load_verify_locations(cadata=ca_pem.decode("ascii", "ignore"))
        except (ssl.SSLError, ValueError):
            pass
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx






def mask_bytes(data: bytes, mask: bytes) -> bytes:
    n = len(data)
    if n == 0:
        return b""
    key = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(key, "big")).to_bytes(n, "big")


def encode_frame(opcode: int, payload: bytes, mask: bool, fin: bool = True, rsv: int = 0) -> bytes:
    head = bytearray([(0x80 if fin else 0) | ((rsv & 0x7) << 4) | (opcode & 0x0F)])
    n = len(payload)
    mask_bit = 0x80 if mask else 0
    if n < 126:
        head.append(mask_bit | n)
    elif n < 65536:
        head.append(mask_bit | 126)
        head += struct.pack(">H", n)
    else:
        head.append(mask_bit | 127)
        head += struct.pack(">Q", n)
    if mask:
        key = os.urandom(4)
        return bytes(head) + key + mask_bytes(payload, key)
    return bytes(head) + payload


def accept_key(client_key: str) -> str:
    digest = hashlib.sha1(  # nosec B324 - required by the WebSocket protocol
        (client_key + _GUID).encode("ascii"), usedforsecurity=False
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def parse_http_head(head: bytes) -> tuple[str, dict]:
    """Split an HTTP head into (start line, lowercase header dict)."""
    lines = head.decode("iso-8859-1").split("\r\n")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            name, value = name.strip().lower(), value.strip()
            headers[name] = headers[name] + ", " + value if name in headers else value
    return lines[0], headers


def http_status_of(start_line: str) -> int:
    parts = start_line.split()
    if len(parts) < 2 or not parts[0].startswith("HTTP/"):
        return 0
    try:
        return int(parts[1])
    except ValueError:
        return 0


def close_payload(code: int, reason: str) -> bytes:

    reason_bytes = reason.encode("utf-8", errors="replace")[:123].decode("utf-8", errors="ignore").encode("utf-8")
    return struct.pack(">H", code) + reason_bytes


def parse_close(payload: bytes) -> tuple[int, str]:
    if len(payload) == 1:
        raise WsProtocolError("close payload has no complete status code")
    if len(payload) >= 2:
        code = struct.unpack(">H", payload[:2])[0]
        if not (1000 <= code <= 1014 or 3000 <= code <= 4999) or code in (1004, 1005, 1006):
            raise WsProtocolError("invalid close status code")
        return code, decode_text(payload[2:])
    return 1005, ""


class SocketReader:
    """Buffered reads."""


    def __init__(self, sock, should_stop: Callable[[], bool], on_idle: Callable[[], None] | None = None):
        self.sock = sock
        self.buf = bytearray()
        self._should_stop = should_stop
        self._on_idle = on_idle

    def _recv(self, size: int, deadline: float | None, what: str) -> bytes:
        while True:
            if self._should_stop():
                raise WsConnectionLost(f"closed while {what}")
            if deadline is not None and time.monotonic() >= deadline:
                raise WsHandshakeError(f"timed out while {what}", "timeout")
            try:
                chunk = self.sock.recv(size)
            except (socket.timeout, ssl.SSLWantReadError, ssl.SSLWantWriteError):



                if self._should_stop():
                    raise WsConnectionLost(f"closed while {what}") from None
                if deadline is not None and time.monotonic() > deadline:
                    raise WsHandshakeError(f"timed out while {what}", "timeout") from None
                if self._on_idle is not None:
                    self._on_idle()
                continue
            except (OSError, ssl.SSLError) as exc:
                raise WsConnectionLost(f"socket error while {what}: {exc}") from exc
            if not chunk:
                raise WsConnectionLost(f"peer closed the socket while {what}")
            return chunk

    def read_exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            self.buf += self._recv(min(65536, max(4096, n - len(self.buf))), None, "reading")
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def read_until(self, marker: bytes, limit: int = 65536, deadline: float | None = None,
                   what: str = "waiting for the handshake") -> bytes:
        while marker not in self.buf:
            if len(self.buf) > limit:
                raise WsHandshakeError("HTTP head too long")
            self.buf += self._recv(4096, deadline, what)
        idx = self.buf.index(marker) + len(marker)
        if idx > limit:
            raise WsHandshakeError("HTTP head too long")
        out = bytes(self.buf[:idx])
        del self.buf[:idx]
        return out


def read_frame(reader: SocketReader, require_mask: bool) -> tuple[bool, int, bytes]:
    """Read one frame, return (fin, opcode, payload). Raises WsProtocolError."""
    b1, b2 = reader.read_exact(2)
    fin = bool(b1 & 0x80)
    if b1 & 0x70:
        raise WsProtocolError("reserved bits set (no extension was negotiated)")
    opcode = b1 & 0x0F
    if opcode not in (OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG):
        raise WsProtocolError(f"unknown opcode {opcode}")
    masked = bool(b2 & 0x80)
    if masked != require_mask:
        raise WsProtocolError("client frame is not masked" if require_mask else "server frame is masked")
    length = b2 & 0x7F
    if opcode >= 0x8 and (not fin or length > 125):
        raise WsProtocolError("malformed control frame")
    if length == 126:
        length = struct.unpack(">H", reader.read_exact(2))[0]
        if length < 126:
            raise WsProtocolError("non-minimal frame length")
    elif length == 127:
        length = struct.unpack(">Q", reader.read_exact(8))[0]
        if length < 65536 or length & (1 << 63):
            raise WsProtocolError("invalid 64-bit frame length")
    if length > MAX_FRAME_BYTES:
        raise WsProtocolError(f"frame of {length} bytes exceeds the 16 MiB limit", 1009)
    if require_mask and not masked:
        raise WsProtocolError("client frame is not masked")
    key = reader.read_exact(4) if masked else None
    payload = reader.read_exact(length)
    if key:
        payload = mask_bytes(payload, key)
    if opcode >= 0x8:
        if not fin or length > 125:
            raise WsProtocolError("malformed control frame")
    elif opcode not in (OP_CONT, OP_TEXT, OP_BINARY):
        raise WsProtocolError(f"unknown opcode {opcode}")
    return fin, opcode, payload


def decode_text(payload: bytes) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WsProtocolError(f"invalid UTF-8 in text frame: {exc}", 1007) from exc
