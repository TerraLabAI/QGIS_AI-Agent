# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





















from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
import secrets
import time
from urllib.parse import urlencode, urlsplit

from qgis.core import QgsNetworkAccessManager, QgsTask
from qgis.PyQt.QtCore import QByteArray, QUrl, pyqtSignal
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest

from .logger import log_warning
from .qt_compat import task_flags


BROWSER = {"copernicus": "accounts_cdse", "googledrive": "accounts_drive:google",
           "onedrive": "accounts_drive:microsoft"}
TIMEOUT_MS = 30000

WAIT_S = 300
_FAKE_ENV = "TERRALAB_AGENT_FAKE_OAUTH"


class Client:


    def __init__(self, issuer: str, client_id: str, scope: str):
        base = issuer.rstrip("/") + "/protocol/openid-connect"
        self.client_id, self.scope = client_id, scope
        self.device_url = base + "/auth/device"
        self.token_url = base + "/token"
        self.logout_url = base + "/logout"


def provider_module(provider: str):

    name, _, attr = BROWSER.get(str(provider or ""), "").partition(":")
    if not name:
        return None
    module = importlib.import_module(f"{__package__}.{name}")
    return getattr(module, attr) if attr else module


def browser_name(provider: str, fallback: str) -> str:

    module = provider_module(provider)
    return str(getattr(module, "NAME", "") or fallback)


def browser_ready(provider: str) -> bool:

    module = provider_module(provider)
    try:
        return bool(module and module.available())
    except Exception:  # noqa: BLE001
        return False


def forget(provider: str, values: dict) -> None:

    module = provider_module(provider)
    if module is not None:
        try:
            module.disconnect(values)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"{provider}: the provider was not told about the removal ({type(exc).__name__})")


def pkce_pair() -> tuple[str, str]:

    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def routed(url: str) -> str:

    fake = os.environ.get(_FAKE_ENV, "").strip().rstrip("/")
    if fake and (urlsplit(fake).hostname or "") in ("127.0.0.1", "localhost"):
        parts = urlsplit(url)
        return f"{fake}/{parts.hostname}{parts.path}" + (f"?{parts.query}" if parts.query else "")
    return url


def _enum(owner, group: str, name: str):

    return getattr(getattr(owner, group, owner), name)


_STATUS = _enum(QNetworkRequest, "Attribute", "HttpStatusCodeAttribute")


def _request(url: str, headers: dict | None, form: bool) -> QNetworkRequest:
    request = QNetworkRequest(QUrl(routed(url)))
    request.setRawHeader(b"Accept", b"application/json")
    if form:
        request.setRawHeader(b"Content-Type", b"application/x-www-form-urlencoded")
    for name, value in (headers or {}).items():
        request.setRawHeader(name.encode("ascii"), str(value).encode("utf-8"))
    request.setAttribute(_enum(QNetworkRequest, "Attribute", "CacheSaveControlAttribute"), False)
    request.setAttribute(_enum(QNetworkRequest, "Attribute", "CacheLoadControlAttribute"),
                         _enum(QNetworkRequest, "CacheLoadControl", "AlwaysNetwork"))
    try:
        request.setTransferTimeout(TIMEOUT_MS)
    except AttributeError:
        pass  # nosec B110
    return request


def _parsed(status, raw: bytes) -> tuple[int | None, dict]:
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeDecodeError):
        body = {}
    return status, body if isinstance(body, dict) else {}


def http(url: str, form: dict | None = None, headers: dict | None = None) -> tuple[int | None, dict]:




    request = _request(url, headers, bool(form))
    if form is not None:
        reply = QgsNetworkAccessManager.blockingPost(request, QByteArray(urlencode(form).encode("utf-8")))
    else:
        reply = QgsNetworkAccessManager.blockingGet(request, "", True)
    return _parsed(reply.attribute(_STATUS), bytes(reply.content()))


def send(method: str, url: str, form: dict | None = None, headers: dict | None = None, on_done=None):

    request = _request(url, headers, bool(form))
    manager = QgsNetworkAccessManager.instance()
    data = QByteArray(urlencode(form or {}).encode("utf-8"))
    if method == "DELETE":
        reply = manager.deleteResource(request)
    elif method == "POST":
        reply = manager.post(request, data)
    else:
        reply = manager.get(request)

    def finished() -> None:
        try:
            status = reply.attribute(_STATUS)
            if status is None and reply.error() != QNetworkReply.NetworkError.NoError:
                result = (None, {})
            else:
                result = _parsed(status, bytes(reply.readAll()))
            if on_done is not None:
                on_done(*result)
        finally:
            reply.deleteLater()

    reply.finished.connect(finished)
    return reply


def refresh(client: Client, refresh_token: str) -> tuple[dict, str]:


    status, body = http(client.token_url, {"grant_type": "refresh_token", "client_id": client.client_id,
                                           "refresh_token": refresh_token})
    if status == 200 and body.get("access_token"):
        return body, ""
    return {}, "network" if status is None or status >= 500 else "refused"


def _logout_form(client: Client, refresh_token: str) -> dict:
    return {"client_id": client.client_id, "refresh_token": refresh_token}


def logout(client: Client, refresh_token: str, on_done=None) -> None:

    send("POST", client.logout_url, _logout_form(client, refresh_token), on_done=on_done)


class DeviceSignIn(QgsTask):






    code_ready = pyqtSignal(str, str)
    confirmed = pyqtSignal()
    done = pyqtSignal(bool, str)

    def __init__(self, client: Client, after=None, label: str = "Sign in"):
        super().__init__(label, task_flags(QgsTask, hidden=True))
        self.client, self.after = client, after
        self._result: dict = {}
        self.reason = ""

    def take(self) -> dict:

        result, self._result = self._result, {}
        return result

    def _wait(self, seconds: float) -> bool:

        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if self.isCanceled():
                return False
            time.sleep(0.25)
        return not self.isCanceled()

    def run(self) -> bool:  # noqa: C901
        verifier, challenge = pkce_pair()
        status, start = http(self.client.device_url, {"client_id": self.client.client_id, "scope": self.client.scope,
                                                      "code_challenge": challenge, "code_challenge_method": "S256"})
        device_code = str(start.get("device_code") or "")
        page = str(start.get("verification_uri_complete") or start.get("verification_uri") or "")
        if status != 200 or not device_code or not page.startswith(("https://", "http://127.0.0.1")):
            self.reason = "network" if status is None or status >= 500 else "unavailable"
            return False
        self.code_ready.emit(str(start.get("user_code") or ""), page)
        interval = max(2, int(start.get("interval") or 5))
        deadline = time.monotonic() + max(60, min(int(start.get("expires_in") or WAIT_S), WAIT_S))
        while time.monotonic() < deadline:
            if not self._wait(interval):
                self.reason = "cancelled"
                return False
            status, body = http(self.client.token_url, {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code", "client_id": self.client.client_id,
                "device_code": device_code, "code_verifier": verifier})
            error = str(body.get("error") or "")
            if status == 200 and body.get("access_token"):
                if self.isCanceled():
                    http(self.client.logout_url, _logout_form(self.client, str(body.get("refresh_token") or "")))
                    self.reason = "cancelled"
                    return False
                self.confirmed.emit()
                if self.after is None:
                    self._result = body
                    return True
                self._result, self.reason = self.after(body)
                return bool(self._result)
            if error == "slow_down":
                interval += 5
            elif error == "access_denied":
                self.reason = "denied"
                return False
            elif error == "expired_token":
                break
            elif status is not None and error != "authorization_pending":
                self.reason = "refused"
                return False
        self.reason = "expired"
        return False

    def finished(self, ok: bool) -> None:  # noqa: D401
        if not ok:
            self._result = {}
        self.done.emit(bool(ok), "" if ok else (self.reason or "cancelled"))
