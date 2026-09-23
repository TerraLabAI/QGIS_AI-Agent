# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


























from __future__ import annotations

import hmac
import json
import os
import secrets
from urllib.parse import parse_qs, urlsplit

from qgis.PyQt.QtCore import QObject
from qgis.PyQt.QtNetwork import QHostAddress, QTcpServer

from .logger import log, log_warning
from .policy import AGENT_HOME, ensure_agent_directories
from .qt_compat import enum_member

PORTS = tuple(range(47821, 47831))
MAX_REQUEST_BYTES = 4096
_TOKEN_FILE = os.path.join(AGENT_HOME, "report-bridge-token")
_HEADERS = ("HTTP/1.1 {status}\r\nContent-Type: application/json; charset=utf-8\r\n"
            "Access-Control-Allow-Origin: *\r\nCache-Control: no-store\r\nConnection: close\r\n"
            "Content-Length: {length}\r\n\r\n")
_STATUS = {200: "200 OK", 400: "400 Bad Request", 403: "403 Forbidden", 404: "404 Not Found",
           405: "405 Method Not Allowed", 413: "413 Payload Too Large"}


def token() -> str:

    try:
        with open(_TOKEN_FILE, encoding="utf-8") as stream:
            value = stream.read().strip()
        if len(value) >= 32:
            return value
    except OSError:
        pass
    value = secrets.token_hex(24)
    try:
        ensure_agent_directories()
        fd = os.open(_TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value)
    except OSError as exc:
        log_warning(f"Report bridge: the token could not be kept ({exc}); pages of this session only")
    return value


class ReportBridge(QObject):


    def __init__(self, parent=None):
        super().__init__(parent)
        self._server: QTcpServer | None = None
        self._token: str | None = None
        self.port = 0



    def start(self) -> bool:
        if self._server is not None:
            return True
        self._token = token()
        server = QTcpServer(self)
        for port in PORTS:
            if server.listen(QHostAddress(enum_member(QHostAddress, "SpecialAddress", "LocalHost")), port):
                server.newConnection.connect(self._on_connection)
                self._server = server
                self.port = port
                log(f"Report bridge listening on 127.0.0.1:{port}")
                return True
        log_warning(f"Report bridge: no free port in {PORTS[0]}-{PORTS[-1]}; report pages will not link back")
        server.deleteLater()
        return False

    def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None
        self.port = 0

    @property
    def token(self) -> str:
        return self._token or ""



    def _on_connection(self) -> None:
        server = self._server
        if server is None:
            return
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            if socket is None:
                break
            socket.readyRead.connect(lambda s=socket: self._on_ready(s))
            socket.disconnected.connect(socket.deleteLater)

    def _on_ready(self, socket) -> None:
        try:
            if socket.bytesAvailable() > MAX_REQUEST_BYTES:
                self._reply(socket, 413, {"ok": False, "error": "request too large"})
                return
            data = bytes(socket.peek(MAX_REQUEST_BYTES))
            if b"\r\n" not in data and b"\n" not in data:
                return
            socket.readAll()
            line = data.split(b"\n", 1)[0].strip().decode("latin-1", "replace")
            parts = line.split(" ")
            if len(parts) < 2:
                self._reply(socket, 400, {"ok": False, "error": "not an HTTP request"})
                return
            method, target = parts[0].upper(), parts[1]
            if method != "GET":
                self._reply(socket, 405, {"ok": False, "error": "GET only"})
                return
            url = urlsplit(target)
            if url.path != "/qgis":
                self._reply(socket, 404, {"ok": False, "error": "unknown path"})
                return
            query = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
            given = str(query.get("token") or "")
            if not self._token or not hmac.compare_digest(given, self._token):
                self._reply(socket, 403, {"ok": False, "error": "bad token"})
                return
            status, body = self._act(query)
            self._reply(socket, status, body)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Report bridge: {exc}")
            try:
                self._reply(socket, 400, {"ok": False, "error": "request failed"})
            except Exception:  # nosec B110
                pass

    @staticmethod
    def _reply(socket, status: int, body: dict) -> None:
        payload = json.dumps(body, ensure_ascii=True).encode("utf-8")
        head = _HEADERS.format(status=_STATUS.get(status, "200 OK"), length=len(payload)).encode("ascii")
        socket.write(head + payload)
        socket.flush()
        socket.disconnectFromHost()



    def _act(self, query: dict) -> tuple[int, dict]:
        verb = str(query.get("do") or "").strip().lower()
        if verb == "ping":
            return 200, {"ok": True, "pong": True}
        if verb not in ("zoom", "show"):
            return 400, {"ok": False, "error": f"unknown action {verb!r}"}
        layer = _find_layer(str(query.get("layer") or ""))
        if layer is None:
            return 404, {"ok": False, "error": "no such layer"}
        fids = _fids(str(query.get("fids") or ""))
        _reveal(layer, fids, zoom=verb == "zoom")
        return 200, {"ok": True, "layer": layer.name(), "features": len(fids)}


_BRIDGE: ReportBridge | None = None


def ensure_started() -> tuple[int, str] | None:

    global _BRIDGE
    if _BRIDGE is None:
        _BRIDGE = ReportBridge()
    if not _BRIDGE.start():
        return None
    return _BRIDGE.port, _BRIDGE.token


def shutdown() -> None:
    global _BRIDGE
    if _BRIDGE is not None:
        _BRIDGE.stop()
        _BRIDGE.deleteLater()
        _BRIDGE = None


def _find_layer(name_or_id: str):
    from qgis.core import QgsProject

    text = name_or_id.strip()
    if not text:
        return None
    project = QgsProject.instance()
    layer = project.mapLayer(text)
    if layer is not None:
        return layer
    matches = project.mapLayersByName(text)
    return matches[0] if matches else None


def _fids(text: str) -> list[int]:
    out: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.append(int(part))
        if len(out) >= 5000:
            break
    return out


def _reveal(layer, fids: list[int], zoom: bool) -> None:

    from qgis.core import QgsCoordinateTransform, QgsProject
    from qgis.utils import iface

    project = QgsProject.instance()
    node = project.layerTreeRoot().findLayer(layer.id())
    if node is not None:
        node.setItemVisibilityCheckedParentRecursive(True)
    canvas = iface.mapCanvas() if iface is not None else None
    if canvas is None:
        return
    is_vector = hasattr(layer, "selectByIds")
    if is_vector and fids:
        layer.selectByIds(fids)
        if zoom:
            canvas.zoomToSelected(layer)
        try:
            canvas.flashFeatureIds(layer, fids)
        except Exception:  # nosec B110
            pass
    elif zoom:
        extent = layer.extent()
        if extent is not None and not extent.isEmpty():
            try:
                transform = QgsCoordinateTransform(layer.crs(), canvas.mapSettings().destinationCrs(), project)
                extent = transform.transformBoundingBox(extent)
            except Exception:  # nosec B110
                pass
            canvas.setExtent(extent)
            canvas.zoomByFactor(1.05)
    if iface is not None:
        try:
            iface.setActiveLayer(layer)
            window = iface.mainWindow()
            window.raise_()
            window.activateWindow()
        except Exception:  # nosec B110
            pass
    canvas.refresh()
