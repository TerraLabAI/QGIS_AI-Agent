# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

































from __future__ import annotations

import json
import time
import urllib.parse

from .logger import log_warning

_PREFIX = "tl_d1"
_KEY_LEN = 63
_MAX_HOSTS = 8
_HOST_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789.-")
_LOOPBACK = frozenset(("127.0.0.1", "localhost"))

_COOKIE = b"tl_data_key"



_EMPTY: tuple[str, frozenset, int] = ("", frozenset(), 0)
_state: tuple[str, frozenset, int] = _EMPTY
_jar = None
_cookies: list = []


def _clean(field) -> tuple[str, frozenset, int]:

    if not isinstance(field, dict):
        return _EMPTY
    key, expiry, hosts = field.get("key"), field.get("expires_at"), field.get("hosts")
    if not (isinstance(key, str) and len(key) == _KEY_LEN and key.startswith(_PREFIX)
            and key[3:].isascii() and key[3:].isalnum()):
        return _EMPTY
    if isinstance(expiry, bool) or not isinstance(expiry, int) or expiry <= time.time():
        return _EMPTY
    if not isinstance(hosts, list):
        return _EMPTY
    clean = frozenset(h for h in hosts[:_MAX_HOSTS]
                      if isinstance(h, str) and 3 < len(h) < 254 and "." in h and set(h) <= _HOST_CHARS)
    if not clean:
        return _EMPTY
    return ("Bearer " + key, clean, expiry)


def _store(field) -> None:
    try:
        from .settings import Settings

        Settings().set_data_access(json.dumps(field) if field else "")
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Data key not saved: {exc}")


def apply(field) -> None:

    global _state
    state = _clean(field)
    if state is _EMPTY:
        return
    had_key = bool(_state[0])
    _state = state
    _store(field)
    _sync_cookies()
    if not had_key:
        _repaint_our_layers(state[1])


def load() -> None:

    global _state
    try:
        from .settings import Settings

        text = Settings().data_access()
        _state = _clean(json.loads(text)) if text else _EMPTY
    except Exception:  # noqa: BLE001
        _state = _EMPTY


def forget() -> None:

    global _state
    _state = _EMPTY
    _store(None)
    _sync_cookies()


def header_for(url: str) -> str:

    value, hosts, expiry = _state
    if not value or expiry <= time.time():
        return ""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    host = parts.hostname or ""
    if host not in hosts:
        return ""

    if parts.scheme != "https" and not (parts.scheme == "http" and host in _LOOPBACK):
        return ""
    return value


def attach(request) -> None:

    try:
        if request.has_header("Authorization"):
            return
        value = header_for(request.full_url)
        if value:
            request.add_unredirected_header("Authorization", value)
    except Exception:  # noqa: BLE001
        return


def _sync_cookies() -> None:




    global _cookies
    jar = _jar
    if jar is None:
        return
    try:
        from qgis.PyQt.QtCore import QByteArray, QDateTime
        from qgis.PyQt.QtNetwork import QNetworkCookie

        for cookie in _cookies:
            jar.deleteCookie(cookie)
        _cookies = []
        value, hosts, expiry = _state
        if not value or expiry <= time.time():
            return
        key = value.split(" ", 1)[1].encode("ascii")
        for host in sorted(hosts):
            cookie = QNetworkCookie(QByteArray(_COOKIE), QByteArray(key))
            cookie.setDomain(host)
            cookie.setPath("/")
            cookie.setSecure(host not in _LOOPBACK)
            cookie.setHttpOnly(True)
            cookie.setExpirationDate(QDateTime.fromSecsSinceEpoch(int(expiry)))
            if jar.insertCookie(cookie):
                _cookies.append(cookie)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Data key not handed to QGIS layers: {exc}")


def install() -> None:

    global _jar
    load()
    try:
        from qgis.core import QgsNetworkAccessManager

        _jar = QgsNetworkAccessManager.instance().cookieJar()
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Data key not handed to QGIS layers: {exc}")
        _jar = None
    _sync_cookies()


def uninstall() -> None:

    global _jar, _state
    _state = _EMPTY
    _sync_cookies()
    _jar = None


def _repaint_our_layers(hosts: frozenset) -> None:

    try:
        from qgis.core import QgsProject

        for layer in QgsProject.instance().mapLayers().values():
            source = layer.source() or ""
            if any(host in source for host in hosts):
                layer.triggerRepaint()
    except Exception:  # nosec B110
        pass
