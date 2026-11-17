# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later














from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import threading
import urllib.parse

from qgis.core import QgsSettings

from . import accounts, oauth_loopback
from .logger import log_warning
from .settings import GROUP
from .tool_registry import tool_error

KEY = "earthengine"



SCOPES = ("https://www.googleapis.com/auth/earthengine",
          "https://www.googleapis.com/auth/cloud-platform")
ENDPOINTS = {
    "auth": "https://accounts.google.com/o/oauth2/auth",
    "token": "https://oauth2.googleapis.com/token",  # nosec B105
    "revoke": "https://oauth2.googleapis.com/revoke",
    "earthengine": "https://earthengine.googleapis.com",
}
REGISTER_URL = "https://code.earthengine.google.com/register"
ENABLE_API_URL = "https://console.cloud.google.com/apis/library/earthengine.googleapis.com?project={project}"
_EE_DEFAULT = ENDPOINTS["earthengine"]
_PROJECT_KEY = f"{GROUP}/earthengine_project"
_lock = threading.Lock()
_live: tuple[str, str] | None = None
_refusal = ""


def _open(request, timeout):
    from . import net
    return net.open_url(request, timeout)


OPENER = _open


def client() -> tuple[str, str]:


    from ee import oauth
    return oauth.CLIENT_ID, oauth.CLIENT_SECRET


def ee_installed() -> bool:
    return importlib.util.find_spec("ee") is not None


def signed_in() -> bool:
    return KEY in accounts.entries()


def project() -> str:
    return str(QgsSettings().value(_PROJECT_KEY, "", type=str) or "")


def set_project(project_id: str) -> None:
    global _refusal
    QgsSettings().setValue(_PROJECT_KEY, str(project_id or "").strip())
    with _lock:
        _refusal = ""


def legacy_credentials() -> bool:

    home = os.path.expanduser("~")
    paths = [os.path.join(home, ".config", "earthengine", "credentials"),
             os.path.join(home, ".config", "gcloud", "application_default_credentials.json"),
             os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or ""]
    if os.environ.get("APPDATA"):
        paths.append(os.path.join(os.environ["APPDATA"], "gcloud", "application_default_credentials.json"))
    return any(p and os.path.isfile(p) for p in paths)


def connected() -> bool:
    return (signed_in() and bool(project())) or legacy_credentials()




def _label() -> str:
    return accounts.PROVIDERS[KEY]["label"]


def _failure(message: str) -> dict:
    return tool_error(message, "PERMISSION_DENIED", hint="gee_not_connected")


def need(kind: str, what: str = "", reason: str = "") -> dict:


    card = {"kind": kind, "key": KEY, "provider": KEY, "host": _label(), "label": _label(),
            "connector": accounts.PROVIDERS[KEY]["connector"], "reason": reason, "what": str(what or "")[:200]}
    if kind == "retry" and reason == "project":
        card["project"] = project()
    message = {"consent": "The user did not allow their saved Earth Engine access.",
               "retry": "Earth Engine refused the user's saved sign-in." if reason != "project"
               else f"Earth Engine refused the user's project {project()}."}.get(
        kind, "Earth Engine is not connected on this computer.")
    return {accounts.MARK: card, "failure": _failure(message)}




def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def credentials(token: str):
    from google.oauth2.credentials import Credentials
    client_id, secret = client()
    return Credentials(None, refresh_token=token, token_uri=ENDPOINTS["token"], client_id=client_id,
                       client_secret=secret, scopes=list(SCOPES))


def _initialize(ee, token: str, project_id: str) -> None:
    global _live
    kwargs = {"credentials": credentials(token), "project": project_id}
    if ENDPOINTS["earthengine"] != _EE_DEFAULT:
        kwargs["url"] = ENDPOINTS["earthengine"]
    ee.Initialize(**kwargs)
    with _lock:
        _live = (_digest(token), project_id)


def _refused_reason(exc: Exception) -> str:


    global _refusal
    with _lock:
        _refusal = ""
    try:
        from google.auth.exceptions import RefreshError
        if isinstance(exc, RefreshError):
            return "sign_in"
    except ImportError:
        pass
    text = str(exc)
    if "invalid_grant" in text:
        return "sign_in"
    cause = _cause(text)
    if cause is None:
        return ""
    with _lock:
        _refusal = cause
    return "project"


def _cause(text: str) -> str | None:

    causes = (("register", ("not registered to use Earth Engine", "Not signed up for Earth Engine",
                            "project is not registered")),
              ("api", ("has not been used in project", "is disabled", "SERVICE_DISABLED")),
              ("", ("does not have required permission", "PERMISSION_DENIED")))
    for cause, markers in causes:
        if any(m in text for m in markers):
            return cause
    return None


def refusal() -> tuple[str, str]:


    with _lock:
        cause = _refusal
    if cause == "api":
        return cause, ENABLE_API_URL.format(project=urllib.parse.quote(project(), safe=""))
    return (cause, REGISTER_URL) if cause == "register" else ("", "")


def session(what: str = "", project_arg: str | None = None):


    global _refusal
    if signed_in():
        if not accounts.granted(KEY):
            return None, need("consent", what)
        if not ee_installed():
            return None, need("provider", what, "install")
        chosen = project()
        if not chosen:
            return None, need("provider", what, "project")
        token = accounts.refresh_token(KEY)
        if not token:
            return None, need("consent", what)
        import ee
        with _lock:
            live = _live == (_digest(token), chosen)
        if live:
            return ee, None
        try:
            _initialize(ee, token, chosen)
        except Exception as exc:  # noqa: BLE001
            reason = _refused_reason(exc)
            if reason == "sign_in":
                accounts._note(KEY, state="refused")
            if reason:
                return None, need("retry", what, reason)
            return None, tool_error(f"Earth Engine could not initialize: {exc}", "PERMISSION_DENIED",
                                    hint="gee_not_connected")
        accounts.worked(KEY)
        with _lock:
            _refusal = ""
        return ee, None
    if ee_installed() and legacy_credentials():
        try:
            import ee
            ee.Initialize(project=project_arg) if project_arg else ee.Initialize()
        except Exception as exc:  # noqa: BLE001
            if _refused_reason(exc):

                return None, need("provider", what)
            return None, tool_error(f"Earth Engine could not initialize: {exc}", "PERMISSION_DENIED",
                                    hint="gee_not_connected")
        return ee, None
    return None, need("provider", what, "" if ee_installed() else "install")


def forget_session() -> None:

    global _live
    with _lock:
        was, _live = _live, None
    if was is None:
        return
    try:
        import ee
        ee.Reset()
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Earth Engine session not reset: {exc}")


def remove() -> None:


    token = accounts.refresh_token(KEY, acting=True)
    accounts.remove(KEY)
    set_project("")
    forget_session()
    if token:
        threading.Thread(target=_revoke, args=(token,), name="earthengine-revoke", daemon=True).start()


def _revoke(token: str) -> None:
    try:
        oauth_loopback.post_form(ENDPOINTS["revoke"], {"token": token}, OPENER, timeout=15)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Earth Engine sign-in not revoked at Google: {type(exc).__name__}")




def exchange(code: str, verifier: str, redirect_uri: str) -> dict:

    client_id, secret = client()
    try:
        status, body = oauth_loopback.exchange(ENDPOINTS["token"], client_id, secret, code, verifier,
                                               redirect_uri, OPENER)
    except OSError as exc:
        return {"error": type(exc).__name__}
    if status != 200 or not body.get("refresh_token"):
        return {"error": str(body.get("error") or f"HTTP {status}")}
    return {"refresh_token": str(body["refresh_token"]), "access_token": str(body.get("access_token") or "")}


def access_token(refresh_token: str) -> dict:
    client_id, secret = client()
    try:
        status, body = oauth_loopback.refresh(ENDPOINTS["token"], client_id, secret, refresh_token, OPENER)
    except OSError as exc:
        return {"error": type(exc).__name__, "cause": "network"}
    if status != 200 or not body.get("access_token"):
        return {"error": str(body.get("error") or f"HTTP {status}")}
    return {"access_token": str(body["access_token"])}


def check_project(token: str, project_id: str) -> dict:









    pid = urllib.parse.quote(project_id, safe="")
    try:
        status, body = oauth_loopback.get_json(f"{ENDPOINTS['earthengine']}/v1/projects/{pid}/config", token,
                                               OPENER, timeout=15, headers={"x-goog-user-project": project_id})
    except OSError as exc:
        return {"cause": "network", "url": "", "error": type(exc).__name__}
    if status == 200:
        return {}
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    text = f"{error.get('message') or ''} {error.get('status') or ''} {json.dumps(error.get('details') or [])}"
    cause = _cause(text)
    url = ENABLE_API_URL.format(project=pid) if cause == "api" else REGISTER_URL if cause == "register" else ""
    return {"cause": cause or "", "url": url, "error": str(error.get("status") or f"HTTP {status}")}


def authorization(loopback, challenge: str) -> str:
    client_id, _ = client()
    return oauth_loopback.authorization_url(ENDPOINTS["auth"], client_id, loopback.redirect_uri, SCOPES,
                                            challenge, loopback.state,
                                            {"access_type": "offline", "prompt": "consent"})


def save_sign_in(refresh_token: str) -> bool:

    forget_session()
    return bool(accounts.store_oauth(KEY, refresh_token))

