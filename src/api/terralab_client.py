# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""HTTP client for the TerraLab website, ported from AI Segmentation."""

















from __future__ import annotations

import json
import os
import platform
import random
import re
import time
from urllib.parse import quote, urlencode, urlsplit

from qgis.core import Qgis, QgsBlockingNetworkRequest, QgsMessageLog
from qgis.PyQt.QtCore import QByteArray, QCoreApplication, QUrl
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest

from ..core.log_scrub import scrub_secrets

PRODUCT_ID = "ai-agent"
TERRALAB_BASE_URL_DEFAULT = "https://terra-lab.ai"
_LOG_TAG = "AI Agent"

_TIMEOUT_API = 30_000
_TIMEOUT_INTERACTIVE = 10_000
_TIMEOUT_CHECKOUT_LINK = 4_000



HARD_CONNECTIVITY_CODES = frozenset({"DNS_ERROR", "CONNECTION_REFUSED", "PROXY_ERROR", "NO_INTERNET"})

_NE = getattr(QNetworkReply, "NetworkError", QNetworkReply)
_HostNotFound = getattr(_NE, "HostNotFoundError", None)
_ConnRefused = getattr(_NE, "ConnectionRefusedError", None)
_Timeout = getattr(_NE, "TimeoutError", None)
_OpCanceled = getattr(_NE, "OperationCanceledError", None)
_SslFailed = getattr(_NE, "SslHandshakeFailedError", None)
_ContentDenied = getattr(_NE, "ContentAccessDenied", None)
_AuthRequired = getattr(_NE, "AuthenticationRequiredError", None)
_UnknownNetwork = getattr(_NE, "UnknownNetworkError", None)
_NoError = getattr(_NE, "NoError", 0)
_CONNECT_FAILURE_ERRORS = set(filter(None, [
    getattr(_NE, "TemporaryNetworkFailureError", None),
    getattr(_NE, "NetworkSessionFailedError", None),
]))
_PROXY_ERRORS = set(filter(None, [
    getattr(_NE, name, None) for name in (
        "ProxyConnectionRefusedError", "ProxyConnectionClosedError", "ProxyNotFoundError",
        "ProxyTimeoutError", "ProxyAuthenticationRequiredError", "UnknownProxyError")
]))
_Attr = getattr(QNetworkRequest, "Attribute", QNetworkRequest)
_HTTP_STATUS_ATTR = getattr(_Attr, "HttpStatusCodeAttribute", None)
_REDIRECT_ATTR = getattr(_Attr, "RedirectPolicyAttribute", None)
_RedirectPolicy = getattr(QNetworkRequest, "RedirectPolicy", QNetworkRequest)
_NO_LESS_SAFE_REDIRECT = getattr(_RedirectPolicy, "NoLessSafeRedirectPolicy", None)
_SAME_ORIGIN_REDIRECT = getattr(_RedirectPolicy, "SameOriginRedirectPolicy", None)
_BLOCKING_NO_ERROR = getattr(getattr(QgsBlockingNetworkRequest, "ErrorCode", QgsBlockingNetworkRequest), "NoError", 0)

_WORTH_ASKING_AGAIN_CODES = ("TIMEOUT", "NO_INTERNET")
_RATE_LIMITED_STATUS = 429
_RETRY_AFTER_MAX_S = 10.0
_RETRY_PAUSE_MIN_S = 0.3
_RETRY_PAUSE_MAX_S = 0.8
_SERVER_CONTACT_TTL_S = 180.0
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "")
_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_REQUEST_BYTES = 2 * 1024 * 1024
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def tr(text: str) -> str:
    return QCoreApplication.translate("TerraLabClient", text)


def _log_warning(msg: str):
    QgsMessageLog.logMessage(scrub_secrets(msg), _LOG_TAG, level=Qgis.MessageLevel.Warning)


def _scrub_urls(text: str) -> str:
    return _URL_RE.sub("<url>", text or "")




_plugin_version_cache: str | None = None


def _plugin_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def terralab_base_url() -> str:
    raw = str(os.environ.get("TERRALAB_BASE_URL") or TERRALAB_BASE_URL_DEFAULT).strip()
    if not raw:
        return TERRALAB_BASE_URL_DEFAULT
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").lower()
        local = host in _LOCAL_HOSTS
        valid_scheme = parsed.scheme == "https" or (parsed.scheme == "http" and local)
        if not valid_scheme or not host or parsed.username or parsed.password or parsed.query:
            raise ValueError
        return raw.rstrip("/")
    except (TypeError, ValueError):
        _log_warning("Ignoring an invalid configured service URL")
        return TERRALAB_BASE_URL_DEFAULT


def plugin_version() -> str:
    global _plugin_version_cache
    if _plugin_version_cache is None:
        version = ""
        try:
            with open(os.path.join(_plugin_dir(), "metadata.txt"), encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("version="):
                        version = line.strip().split("=", 1)[1].strip()
                        break
        except Exception:
            version = ""
        _plugin_version_cache = version or "unknown"
    return _plugin_version_cache


def qgis_version() -> str:
    try:
        return str(Qgis.QGIS_VERSION).split("-")[0]
    except Exception:  # nosec B110 - OS hint is optional
        return ""


def config_query(product: str, lang: str = "") -> str:
    params = {"product": product}
    if lang:
        params["lang"] = lang[:5]
    params["v"] = plugin_version()
    if qgis_version():
        params["qgis"] = qgis_version()
    try:
        params["os"] = platform.system()
    except Exception:  # nosec B110 - OS hint is optional
        pass
    return f"/api/plugin/config?{urlencode(params)}"




_last_server_contact_monotonic: float | None = None


def note_server_contact() -> None:
    global _last_server_contact_monotonic
    _last_server_contact_monotonic = time.monotonic()


def server_reached_recently() -> bool:
    stamp = _last_server_contact_monotonic
    if stamp is None:
        return False
    age = time.monotonic() - stamp
    return 0.0 <= age <= _SERVER_CONTACT_TTL_S


def _apply_redirect_policy(req: QNetworkRequest, has_auth: bool) -> None:
    """Authenticated calls follow same-origin redirects only, so the key never travels to another host."""

    if _REDIRECT_ATTR is None:
        return
    policy = _SAME_ORIGIN_REDIRECT if has_auth else _NO_LESS_SAFE_REDIRECT
    if policy is not None:
        req.setAttribute(_REDIRECT_ATTR, policy)


def _parse_json_body(raw_body: str, allow_list: bool = False):
    parsed = json.loads(raw_body)
    if isinstance(parsed, dict):
        return parsed
    if allow_list and isinstance(parsed, list):
        return parsed
    return None


def _named_error_text(body: dict) -> str:
    for key in ("error", "message", "detail"):
        value = body.get(key)
        if isinstance(value, str):
            if value.strip():
                return value.strip()[:400]
            continue
        if value:
            return str(value)[:400]
    return ""


def _error_shaped(body: dict, fallback_code: str, fallback_msg: str) -> dict:



    answer = {}
    for key in ("code", "error", "message", "detail", "retry_after", "purge_after"):
        value = body.get(key)
        if isinstance(value, str):
            answer[key] = value[:400]
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            answer[key] = value
    if "error" not in answer:
        answer["error"] = _named_error_text(body) or fallback_msg
    if not answer.get("code"):
        answer["code"] = fallback_code
    return answer


def _unreadable_answer() -> dict:
    return {
        "error": tr("The reply did not come from the service. If this network shows a "
                    "sign-in page, open it in your browser first, then try again."),
        "code": "UNREADABLE_RESPONSE",
    }


def _http_status_of(reply) -> int | None:
    if reply is None or _HTTP_STATUS_ATTR is None:
        return None
    try:
        attr = reply.attribute(_HTTP_STATUS_ATTR)
        return None if attr is None else int(attr)
    except (TypeError, ValueError, RuntimeError):
        return None


def _parse_retry_after_header(raw: str) -> float | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _retry_after_s(reply) -> float:
    try:
        raw = bytes(reply.rawHeader(b"Retry-After")).decode("ascii", "replace")
    except Exception:
        raw = ""
    value = _parse_retry_after_header(raw)
    if value is None:
        return 0.0
    return min(value, _RETRY_AFTER_MAX_S)


def _retry_pause_s() -> float:
    return random.uniform(_RETRY_PAUSE_MIN_S, _RETRY_PAUSE_MAX_S)  # nosec B311 - retry jitter is not security relevant


def _worth_asking_again(answer, http_status: int | None) -> bool:
    if http_status == _RATE_LIMITED_STATUS:
        return True
    if not isinstance(answer, dict) or "error" not in answer:
        return False
    return str(answer.get("code") or "").upper() in _WORTH_ASKING_AGAIN_CODES


def _classify_qt_error(qt_error, error_string: str, http_status: int | None,
                       service_reachable: bool = False) -> tuple[str, str]:
    """Map a Qt NetworkError to (code, user message)."""

    qt_error_num = getattr(qt_error, "value", qt_error)
    _log_warning(f"Network error: qt_error={qt_error_num}, http_status={http_status}, "
                 f"detail={_scrub_urls(error_string or '')[:300]}")
    if qt_error == _HostNotFound:
        return "DNS_ERROR", tr("Cannot reach the server. Check your internet connection.")
    if qt_error == _ConnRefused:
        return "CONNECTION_REFUSED", tr("Server refused the connection.")
    if qt_error == _Timeout or (_OpCanceled is not None and qt_error == _OpCanceled):
        if http_status is None:
            if service_reachable:
                return "TIMEOUT", tr("Request timed out. Check your connection or try again.")
            return "NO_INTERNET", tr("Network error. Check your internet connection.")
        return "TIMEOUT", tr("Request timed out. Check your connection or try again.")
    if qt_error == _SslFailed:
        return "SSL_ERROR", tr("SSL certificate error. Your network may be blocking secure connections.")
    if qt_error in _PROXY_ERRORS:
        return "PROXY_ERROR", tr("Proxy connection failed. Check QGIS proxy settings (Settings > Options > Network).")
    if qt_error in (_ContentDenied, _AuthRequired) and http_status == 401:
        return "AUTH_ERROR", tr("Authentication failed. Please sign in again.")
    if http_status is not None and http_status >= 500:
        return "SERVER_ERROR", tr("The service is temporarily unavailable (server error). "
                                  "Your connection is fine, please try again in a few minutes.")
    if http_status is not None:
        return "SERVER_ERROR", tr("The server returned an unexpected response. Please try again.")
    if qt_error in _CONNECT_FAILURE_ERRORS:
        return "NO_INTERNET", tr("Network error. Check your internet connection.")
    return "SERVER_ERROR", tr("The connection to the server was interrupted. Please try again.")


def _classify_network_error(blocker: QgsBlockingNetworkRequest) -> tuple[str, str]:
    reply = blocker.reply()
    qt_error = reply.error() if reply else _UnknownNetwork
    return _classify_qt_error(qt_error, blocker.errorMessage(), _http_status_of(reply),
                              service_reachable=server_reached_recently())




class TerraLabClient:
    """Blocking client for the website. Call it off the GUI thread (QgsTask)."""

    def __init__(self, base_url: str | None = None):
        if base_url is None:
            base_url = self._read_base_url()
        self.base_url = base_url.rstrip("/")
        self._pending_retry_after_s = 0.0

    @staticmethod
    def _read_base_url() -> str:
        return terralab_base_url()

    def _resolve_url(self, path_or_url: str) -> str:
        path_or_url = str(path_or_url or "")
        url = path_or_url if path_or_url.startswith(("http://", "https://")) else f"{self.base_url}{path_or_url}"
        self._reject_cleartext_remote(url)
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("Invalid service URL")
        return url

    @staticmethod
    def _reject_cleartext_remote(url: str) -> None:
        """Never send the key over plain HTTP to a remote host."""
        if not url.startswith("http://"):
            return
        try:
            host = (urlsplit(url).hostname or "").lower()
        except Exception:
            host = "?"
        if host not in _LOCAL_HOSTS:
            raise ValueError("Refusing to send an authenticated request over plain HTTP to a "
                             "remote host (the key would travel in cleartext). Use HTTPS.")



    def get_usage(self, auth: dict) -> dict:
        return self._request("GET", "/api/plugin/usage", auth=auth,
                             timeout_ms=_TIMEOUT_INTERACTIVE, require_body=True)

    def get_account(self, auth: dict) -> dict:
        return self._request("GET", "/api/plugin/account", auth=auth,
                             timeout_ms=_TIMEOUT_INTERACTIVE, require_body=True)

    def get_config(self, product: str = PRODUCT_ID, lang: str = "") -> dict:
        return self._request("GET", config_query(product, lang),
                             timeout_ms=_TIMEOUT_INTERACTIVE, require_body=True)

    def send_telemetry_batch(self, events: list, auth: dict) -> dict:
        """Send telemetry through QGIS's network stack and proxy settings."""
        if not isinstance(events, list):
            return {"error": "Invalid telemetry batch", "code": "CLIENT_ERROR"}
        safe_events = [event for event in events[:50] if isinstance(event, dict)]
        body = json.dumps({"events": safe_events}, separators=(",", ":")).encode("utf-8")
        return self._request("POST", "/api/plugin/track", auth=auth, body=body,
                             timeout_ms=_TIMEOUT_INTERACTIVE, require_body=True)

    def get_plugin_login_link(self, target: str, cta_source: str, auth: dict | None = None,
                              locale: str | None = None) -> dict:
        """A one-time signed-in link to `target` on the website, or {"error", "code"}."""
        target = str(target or "")
        if (not target.startswith("/") or target.startswith("//") or "\\" in target
                or any(ord(char) < 32 or ord(char) == 127 for char in target)):
            return {"error": "Invalid login target", "code": "CLIENT_ERROR"}
        payload: dict = {"target": target, "cta_source": cta_source, "plugin_version": plugin_version()}
        if locale:
            payload["locale"] = locale
        return self._request("POST", "/api/plugin/login-link", auth=auth,
                             body=json.dumps(payload).encode("utf-8"),
                             timeout_ms=_TIMEOUT_CHECKOUT_LINK, require_body=True)

    def get_pro_checkout_link(self, product: str, cta_source: str, auth: dict | None = None,
                              interval: str | None = None, locale: str | None = None) -> dict:
        payload: dict = {"product": product, "cta_source": cta_source, "plugin_version": plugin_version()}
        if interval:
            payload["interval"] = interval
        if locale:
            payload["locale"] = locale
        return self._request("POST", "/api/plugin/checkout-session", auth=auth,
                             body=json.dumps(payload).encode("utf-8"),
                             timeout_ms=_TIMEOUT_CHECKOUT_LINK, require_body=True)

    def delete_account(self, auth: dict, confirm: str) -> dict:
        """Schedule the erasure of the signed-in account, or {"error", "code"}."""





        payload = {"confirm": confirm}
        return self._request("POST", "/api/plugin/account/delete", auth=auth,
                             body=json.dumps(payload).encode("utf-8"),
                             timeout_ms=_TIMEOUT_INTERACTIVE, require_body=True)

    def poll_pairing(self, code: str, timeout_ms: int = 10_000) -> dict:
        """Unauthenticated: the code is the bearer of trust."""

        code = str(code or "").strip()
        if not code or len(code) > 256:
            return {"error": "Invalid pairing code", "code": "CLIENT_ERROR"}
        return self._request("GET", f"/api/plugin/pair/poll?code={quote(code, safe='')}",
                             timeout_ms=timeout_ms, require_body=True)

    def cancel_pairing(self, code: str) -> dict:
        code = str(code or "").strip()
        if not code or len(code) > 256:
            return {"error": "Invalid pairing code", "code": "CLIENT_ERROR"}
        body = json.dumps({"code": code, "product": PRODUCT_ID}).encode("utf-8")
        return self._request("POST", "/api/plugin/pair/cancel", body=body, timeout_ms=5_000)



    def _make_qnetwork_request(self, auth: dict | None, timeout_ms: int, path: str) -> QNetworkRequest:
        req = QNetworkRequest(QUrl(self._resolve_url(path)))
        req.setRawHeader(b"Content-Type", b"application/json")
        if hasattr(req, "setTransferTimeout"):
            req.setTransferTimeout(max(1_000, min(int(timeout_ms), 120_000)))
        _apply_redirect_policy(req, bool(auth))
        if auth:
            for key, value in auth.items():
                if not _HEADER_NAME_RE.fullmatch(str(key)):
                    raise ValueError("Invalid request header")
                value = str(value)
                if (len(value) > 4096 or any(ord(char) < 32 or ord(char) == 127
                                             for char in value)):
                    raise ValueError("Invalid request header value")
                req.setRawHeader(str(key).encode("ascii"), value.encode("utf-8"))
        return req

    def _request(self, method: str, path: str, auth: dict | None = None, body: bytes | None = None,
                 timeout_ms: int | None = None, allow_list: bool = False,
                 require_body: bool = False):
        """One blocking round trip: the parsed body or {"error", "code"}."""

        if timeout_ms is None:
            timeout_ms = _TIMEOUT_API




        state: dict = {}
        answer, http_status, _ = self._request_once(
            method, path, auth, body, timeout_ms, allow_list, require_body, state)
        if method == "GET" and _worth_asking_again(answer, http_status):
            time.sleep(state.get("retry_after_s") or _retry_pause_s())
            answer, _, _ = self._request_once(
                method, path, auth, body, timeout_ms, allow_list, require_body, state)
        return answer

    def _request_once(self, method: str, path: str, auth: dict | None, body: bytes | None,
                      timeout_ms: int, allow_list: bool, require_body: bool, state: dict | None = None):
        state = {} if state is None else state
        state["retry_after_s"] = 0.0
        self._pending_retry_after_s = 0.0
        method = str(method or "").upper()
        if body is not None and len(body) > _MAX_REQUEST_BYTES:
            return ({"error": "Request is too large", "code": "CLIENT_ERROR"}, None, False)
        try:
            req = self._make_qnetwork_request(auth, timeout_ms, path)
        except ValueError as err:
            return ({"error": str(err), "code": "CLIENT_ERROR"}, None, False)

        blocker = QgsBlockingNetworkRequest()





        if method == "GET":
            err = blocker.get(req, forceRefresh=True)
        elif method == "POST":
            err = blocker.post(req, QByteArray(body) if body else QByteArray())
        else:
            return ({"error": f"Unsupported method: {method}", "code": "CLIENT_ERROR"}, None, False)

        if err != _BLOCKING_NO_ERROR:
            reply = blocker.reply()
            http_status = _http_status_of(reply)
            if http_status is not None:
                note_server_contact()
            if http_status == _RATE_LIMITED_STATUS and reply is not None:
                state["retry_after_s"] = self._pending_retry_after_s = _retry_after_s(reply)
            if reply is not None and http_status is not None and http_status >= 400:
                raw = self._read_reply_body(reply)
                if raw:
                    try:
                        parsed = _parse_json_body(raw)
                    except Exception:
                        parsed = None
                    if parsed is not None:
                        code, msg = _classify_network_error(blocker)
                        return (_error_shaped(parsed, code, msg), http_status, True)
            code, msg = _classify_network_error(blocker)
            return ({"error": msg, "code": code}, http_status, False)

        reply = blocker.reply()
        http_status = _http_status_of(reply)
        raw_body, why = self._read_body(reply)
        if raw_body is None:
            if why == "RESPONSE_TOO_LARGE":
                _log_warning("Response exceeded the client size limit")
                return ({"error": "Server response is too large", "code": "RESPONSE_TOO_LARGE"},
                        http_status, False)
            return ({"error": "The server's answer was not valid UTF-8", "code": "SERVER_ERROR"},
                    http_status, False)
        note_server_contact()
        if http_status == _RATE_LIMITED_STATUS:
            state["retry_after_s"] = self._pending_retry_after_s = _retry_after_s(reply)

        if http_status is not None and http_status >= 400:
            _log_warning(f"HTTP {http_status} error response")
            try:
                error_body = _parse_json_body(raw_body)
            except Exception:
                error_body = None
            if error_body is None:
                return ({"error": f"Server error (HTTP {http_status})", "code": "SERVER_ERROR"}, http_status, False)
            return (_error_shaped(error_body, "SERVER_ERROR", f"Server error (HTTP {http_status})"), http_status, True)

        if not raw_body:
            if require_body:
                _log_warning("Empty body on a route that must carry one")
                return (_unreadable_answer(), http_status, False)
            return ({}, http_status, False)
        try:
            parsed = _parse_json_body(raw_body, allow_list=allow_list)
        except json.JSONDecodeError:
            parsed = None
        if parsed is None:
            _log_warning(f"Invalid JSON response ({len(raw_body)} bytes)")
            if require_body:
                return (_unreadable_answer(), http_status, False)
            return ({"error": "Invalid server response", "code": "SERVER_ERROR"}, http_status, False)
        return (parsed, http_status, True)

    @staticmethod
    def _read_reply_body(reply) -> str | None:
        """The text of the body, or None when it is unusable. See ``_read_body``."""
        return TerraLabClient._read_body(reply)[0]

    @staticmethod
    def _read_body(reply) -> tuple[str | None, str]:
        """Bound the second copy and parsing of a body already buffered by Qt."""







        try:
            data = bytes(reply.read(_MAX_RESPONSE_BYTES + 1))
        except (AttributeError, RuntimeError, TypeError):
            try:
                data = bytes(reply.content())
            except (AttributeError, RuntimeError, TypeError):
                return ("", "")
        if len(data) > _MAX_RESPONSE_BYTES:
            return (None, "RESPONSE_TOO_LARGE")
        try:
            return (data.decode("utf-8"), "")
        except UnicodeDecodeError:
            _log_warning(f"Response was not valid UTF-8 ({len(data)} bytes)")
            return (None, "SERVER_ERROR")
