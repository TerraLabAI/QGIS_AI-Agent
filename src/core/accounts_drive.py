# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

























from __future__ import annotations

import os
import threading
import time
from urllib.parse import urlsplit

from qgis.core import QgsApplication, QgsSettings, QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from . import accounts, accounts_oauth, background, oauth_loopback
from .logger import log_warning
from .settings import GROUP

GOOGLE, MICROSOFT = "googledrive", "onedrive"
BROWSER_VIA = "browser"
SHAREPOINT_SCOPE = "Sites.Read.All"
_ENDPOINTS = {
    GOOGLE: {"auth": "https://accounts.google.com/o/oauth2/v2/auth",
             "token": "https://oauth2.googleapis.com/token",  # nosec B105
             "revoke": "https://oauth2.googleapis.com/revoke",
             "who": "https://www.googleapis.com/drive/v3/about?fields=user(emailAddress)"},
    MICROSOFT: {"auth": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                "token": "https://login.microsoftonline.com/common/oauth2/v2.0/token",  # nosec B105
                "who": "https://graph.microsoft.com/v1.0/me?$select=userPrincipalName,mail"},
}
_SCOPES = {GOOGLE: ["https://www.googleapis.com/auth/drive.file"],
           MICROSOFT: ["offline_access", "User.Read", "Files.Read"]}
ADMIN_CONSENT = "https://login.microsoftonline.com/organizations/adminconsent?client_id={client}"




_ADMIN_CODES = ("AADSTS65001", "AADSTS90094", "AADSTS90095", "AADSTS90008", "AADSTS900941",
                "AADSTS900981", "consent_required")

_SCOPES_KEY = f"{GROUP}/accounts_drive_scopes"
_FAKE_CLIENTS = {GOOGLE: {"client_id": "fake-google-client", "client_secret": "fake-google-secret"},  # nosec B105
                 MICROSOFT: {"client_id": "fake-microsoft-client"}}

_MARGIN_S = 120

_lock = threading.Lock()
_access: dict[str, tuple[str, float]] = {}


def _fake() -> str:
    fake = os.environ.get(accounts_oauth._FAKE_ENV, "").strip().rstrip("/")
    return fake if (urlsplit(fake).hostname or "") in ("127.0.0.1", "localhost") else ""


def client(provider: str) -> dict:


    if _fake():
        return dict(_FAKE_CLIENTS.get(provider) or {})
    from . import tuning
    doc = tuning.service_doc("cloud_clients") or {}
    row = doc.get(provider) if isinstance(doc.get(provider), dict) else {}
    return dict(row) if row.get("client_id") else {}


def provider_of(url: str) -> str:

    text = str(url or "").strip()
    fake = _fake()
    if fake and text.startswith(fake + "/"):
        text = "https://" + text[len(fake) + 1:]
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    if parts.scheme != "https":
        return ""
    host, path = (parts.hostname or "").lower(), parts.path
    if host == "www.googleapis.com" and path.startswith(("/drive/", "/upload/drive/")):
        return GOOGLE
    if host == "graph.microsoft.com" and any(
            f"/{p}" in path for p in ("me/drive", "drives/", "sites/", "shares/", "me/insights")):
        return MICROSOFT
    return ""


def file_id(url: str) -> str:

    path = urlsplit(str(url or "")).path
    marker = "/files/"
    if provider_of(url) != GOOGLE or marker not in path:
        return ""
    rest = path.split(marker, 1)[1].split("/", 1)[0]
    return rest if rest and all(c.isalnum() or c in "-_" for c in rest) else ""


def is_site(url: str) -> bool:

    path = urlsplit(str(url or "")).path
    return provider_of(url) == MICROSOFT and ("/sites/" in path or "/drives/" in path or "/shares/" in path)


def scopes_held(provider: str) -> list[str]:
    raw = QgsSettings().value(f"{_SCOPES_KEY}/{provider}", "", type=str) or ""
    return raw.split() if raw else list(_SCOPES.get(provider, []))


def needs_sites_scope(url: str) -> bool:
    return is_site(url) and SHAREPOINT_SCOPE not in scopes_held(MICROSOFT)


def admin_consent_url() -> str:
    client_id = client(MICROSOFT).get("client_id") or ""
    return ADMIN_CONSENT.format(client=client_id) if client_id else ""




class LoopbackSignIn(QgsTask):





    code_ready = pyqtSignal(str, str)
    confirmed = pyqtSignal()
    done = pyqtSignal(bool, str)

    def __init__(self, provider: str, scopes: list, extra: dict, label: str):
        flags = QgsTask.Flag.CanCancel
        for name in ("Hidden", "Silent"):
            more = getattr(QgsTask.Flag, name, None)
            if more is not None:
                flags = flags | more
        super().__init__(label, flags)
        self.provider, self.scopes, self.extra = provider, list(scopes), dict(extra)
        self._result: dict = {}
        self.reason = ""

    def take(self) -> dict:
        result, self._result = self._result, {}
        return result

    def run(self) -> bool:
        settings = client(self.provider)
        ends = _ENDPOINTS[self.provider]
        if not settings.get("client_id"):
            self.reason = "refused"
            return False
        verifier, challenge = oauth_loopback.pkce_pair()
        try:
            loop = oauth_loopback.Loopback("Signed in. You can close this tab and go back to QGIS.",
                                           "Sign-in did not finish. Go back to QGIS.")
        except OSError:
            self.reason = "network"
            return False
        try:
            page = oauth_loopback.authorization_url(ends["auth"], settings["client_id"], loop.redirect_uri,
                                                    self.scopes, challenge, loop.state, self.extra)
            self.code_ready.emit("", accounts_oauth.routed(page))
            deadline = time.monotonic() + accounts_oauth.WAIT_S
            while not loop.done() and time.monotonic() < deadline:
                if self.isCanceled():
                    self.reason = "cancelled"
                    return False
                time.sleep(0.25)
            answered = loop.done()
            code, error = loop.result()
            params = dict(loop.params)
        finally:
            loop.close()
        if not code:
            self.reason = _sign_in_refusal(error, params) if answered else "expired"
            return False
        self.confirmed.emit()
        form = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                "client_id": settings["client_id"], "redirect_uri": loop.redirect_uri}
        if settings.get("client_secret"):
            form["client_secret"] = settings["client_secret"]
        if self.provider == MICROSOFT:
            form["scope"] = " ".join(self.scopes)
        status, body = accounts_oauth.http(ends["token"], form)
        refresh_token = str(body.get("refresh_token") or "")
        access = str(body.get("access_token") or "")
        if status != 200 or not refresh_token or not access:
            error = str(body.get("error_description") or body.get("error") or "")
            self.reason = "network" if status is None or status >= 500 else _sign_in_refusal(error, {})
            return False
        if self.isCanceled():
            revoke = _ENDPOINTS[self.provider].get("revoke")
            if revoke and not accounts.entries().get(self.provider):
                accounts_oauth.http(revoke, {"token": refresh_token})
            self.reason = "cancelled"
            return False
        account = _who(self.provider, access)
        granted = str(body.get("scope") or " ".join(self.scopes))
        self._result = {"refresh_token": refresh_token, "access_token": access, "account": account,
                        "expires_in": int(body.get("expires_in") or 3600), "scope": granted,
                        "picked": [p for p in str(params.get("picked_file_ids") or "").split(",") if p]}
        return True

    def finished(self, ok: bool) -> None:  # noqa: D401
        if not ok:
            self._result = {}
        self.done.emit(bool(ok), "" if ok else (self.reason or "cancelled"))


def _sign_in_refusal(error: str, params: dict) -> str:
    said = f"{error} {params.get('error_description') or ''} {params.get('error_subcode') or ''}"
    if any(code in said for code in _ADMIN_CODES):
        return "admin"
    if "access_denied" in said or "cancel" in said:
        return "denied"
    return "refused"


def _who(provider: str, access: str) -> str:

    status, body = accounts_oauth.http(_ENDPOINTS[provider]["who"], None, {"Authorization": f"Bearer {access}"})
    if status != 200:
        return ""
    if provider == GOOGLE:
        user = body.get("user") if isinstance(body.get("user"), dict) else {}
        return str(user.get("emailAddress") or "")[:64]
    return str(body.get("userPrincipalName") or body.get("mail") or "")[:64]


class _Provider:


    def __init__(self, provider: str, name: str):
        self.provider, self.NAME = provider, name

    def available(self) -> bool:
        try:
            manager = QgsApplication.authManager()
            vault = manager is not None and not manager.isDisabled() and "Basic" in manager.authMethodsKeys()
        except Exception:  # noqa: BLE001
            return False
        return bool(vault and client(self.provider).get("client_id"))

    def sign_in(self) -> LoopbackSignIn:
        return self.sign_in_for({})

    def sign_in_for(self, need: dict) -> LoopbackSignIn:


        scopes = list(_SCOPES[self.provider])
        extra: dict = {"prompt": "consent"} if self.provider == GOOGLE else {"prompt": "select_account"}
        if self.provider == GOOGLE:
            extra.update(access_type="offline", trigger_onepick="true", allow_multiple="true")
            asked = str((need or {}).get("file_id") or file_id(str((need or {}).get("url") or "")))
            if asked:
                extra["file_ids"] = asked
        elif (need or {}).get("reason") == "sharepoint" or SHAREPOINT_SCOPE in scopes_held(MICROSOFT):
            scopes.append(SHAREPOINT_SCOPE)
        return LoopbackSignIn(self.provider, scopes, extra, f"{self.NAME} sign-in")

    def save(self, result: dict) -> str:



        result = dict(result or {})
        info = accounts.PROVIDERS[self.provider]
        account = str(result.get("account") or "")
        key = accounts.store(f"https://{self.provider}/", accounts.OAUTH,
                             {"username": account or self.provider, "password": result.get("refresh_token", "")},
                             info["label"], key=self.provider, via=BROWSER_VIA,
                             facts={"account": account} if account else None)
        if key:
            QgsSettings().setValue(f"{_SCOPES_KEY}/{self.provider}", str(result.get("scope") or ""))
            _keep_access(self.provider, str(result.get("access_token") or ""), int(result.get("expires_in") or 0))
        result.clear()
        return key

    def discard(self, result: dict) -> None:


        if not accounts.entries().get(self.provider):
            _revoke(self.provider, str((result or {}).get("refresh_token") or ""))

    def disconnect(self, values: dict) -> None:


        with _lock:
            _access.pop(self.provider, None)
        QgsSettings().remove(f"{_SCOPES_KEY}/{self.provider}")
        _revoke(self.provider, str((values or {}).get("password") or ""))


google = _Provider(GOOGLE, "Google")
microsoft = _Provider(MICROSOFT, "Microsoft")


def _revoke(provider: str, refresh_token: str) -> None:
    url = _ENDPOINTS.get(provider, {}).get("revoke")
    if url and refresh_token:
        accounts_oauth.send("POST", url, {"token": refresh_token})




def _keep_access(provider: str, token: str, expires_in: int) -> None:
    if not token:
        return
    accounts.hide_also(token)
    with _lock:
        _access[provider] = (token, time.time() + max(60, expires_in or 3600))


def headers(provider: str) -> dict:



    with _lock:
        token, ends = _access.get(provider, ("", 0.0))
    if token and ends - _MARGIN_S > time.time():
        return {"Authorization": f"Bearer {token}"} if accounts.granted(provider) else {}
    if background.on_main_thread():
        return {}
    refresh_token = accounts.refresh_token(provider)
    if not refresh_token:
        return {}
    settings = client(provider)
    form = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": settings.get("client_id", "")}
    if settings.get("client_secret"):
        form["client_secret"] = settings["client_secret"]
    if provider == MICROSOFT:
        form["scope"] = " ".join(scopes_held(MICROSOFT))
    status, body = accounts_oauth.http(_ENDPOINTS[provider]["token"], form)
    access = str(body.get("access_token") or "")
    if status != 200 or not access:
        log_warning(f"{accounts.PROVIDERS[provider]['label']}: the saved sign-in was not renewed "
                    f"({'network' if status is None or status >= 500 else 'refused'})")
        return {}
    _keep_access(provider, access, int(body.get("expires_in") or 0))
    rotated = str(body.get("refresh_token") or "")
    if rotated and rotated != refresh_token:

        try:
            background.run_on_main_thread(_keep_refresh, provider, rotated, timeout=20)
        except Exception:  # nosec B110
            pass
    return {"Authorization": f"Bearer {access}"}


def _keep_refresh(provider: str, refresh_token: str) -> None:
    entry = accounts.entries().get(provider) or {}
    if entry.get("via") != BROWSER_VIA:
        return
    account = str(entry.get("account") or "")
    accounts.store(f"https://{provider}/", accounts.OAUTH,
                   {"username": account or provider, "password": refresh_token},
                   accounts.PROVIDERS[provider]["label"], key=provider, via=BROWSER_VIA,
                   facts={"account": account} if account else None)
