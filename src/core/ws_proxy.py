# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later









from __future__ import annotations

import os
import sys
import threading
import time
from urllib.parse import urlsplit

from .ws_protocol import env_proxy, no_proxy_matches


def _entry_host(entry: str) -> str:

    return (urlsplit(entry if "://" in entry else "//" + entry).hostname or entry).lower().rstrip(".")


def _excluded(url: str, host: str, entries) -> bool:

    for entry in entries or []:
        entry = str(entry or "").strip()
        if not entry:
            continue
        if url.startswith(entry.lower()):
            return True
        name = _entry_host(entry)
        if name and (host == name or host.endswith("." + name)):
            return True
    return False


def _qt_proxy_dict(proxy) -> dict | None:

    from qgis.PyQt.QtNetwork import QNetworkProxy
    ptype = getattr(QNetworkProxy, "ProxyType", QNetworkProxy)
    if proxy is None or proxy.type() not in (ptype.HttpProxy, ptype.HttpCachingProxy):
        return None
    if not proxy.hostName():
        return None
    return {"host": proxy.hostName(), "port": int(proxy.port()),
            "user": proxy.user(), "password": proxy.password()}


def _fallback_proxy(nam):
    return nam.fallbackProxy() if hasattr(nam, "fallbackProxy") else nam.proxy()


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

    now = time.monotonic()
    hit = _resolve_cache.get(key)
    if hit is None or now - hit[0] >= _RESOLVE_TTL_S:
        hit = (now, produce())
        _resolve_cache[key] = hit
    value = hit[1]
    return dict(value) if isinstance(value, dict) else value


def _warm_system_lookup(url: str) -> None:








    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    class IeConfig(ctypes.Structure):
        _fields_ = [("fAutoDetect", wintypes.BOOL), ("lpszAutoConfigUrl", ctypes.c_void_p),
                    ("lpszProxy", ctypes.c_void_p), ("lpszProxyBypass", ctypes.c_void_p)]

    class Options(ctypes.Structure):
        _fields_ = [("dwFlags", wintypes.DWORD), ("dwAutoDetectFlags", wintypes.DWORD),
                    ("lpszAutoConfigUrl", wintypes.LPCWSTR), ("lpvReserved", ctypes.c_void_p),
                    ("dwReserved", wintypes.DWORD), ("fAutoLogonIfChallenged", wintypes.BOOL)]

    class Info(ctypes.Structure):
        _fields_ = [("dwAccessType", wintypes.DWORD), ("lpszProxy", ctypes.c_void_p),
                    ("lpszProxyBypass", ctypes.c_void_p)]

    winhttp = ctypes.WinDLL("winhttp")
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.restype = ctypes.c_void_p
    winhttp.WinHttpGetIEProxyConfigForCurrentUser.argtypes = [ctypes.POINTER(IeConfig)]
    winhttp.WinHttpGetIEProxyConfigForCurrentUser.restype = wintypes.BOOL
    winhttp.WinHttpOpen.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                    wintypes.DWORD]
    winhttp.WinHttpOpen.restype = ctypes.c_void_p
    winhttp.WinHttpGetProxyForUrl.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(Options),
                                              ctypes.POINTER(Info)]
    winhttp.WinHttpGetProxyForUrl.restype = wintypes.BOOL
    winhttp.WinHttpCloseHandle.argtypes = [ctypes.c_void_p]

    config = IeConfig()
    if not winhttp.WinHttpGetIEProxyConfigForCurrentUser(ctypes.byref(config)):
        return
    pac = ctypes.wstring_at(config.lpszAutoConfigUrl) if config.lpszAutoConfigUrl else ""
    for held in (config.lpszAutoConfigUrl, config.lpszProxy, config.lpszProxyBypass):
        if held:
            kernel32.GlobalFree(held)
    if not pac and not config.fAutoDetect:
        return
    session = winhttp.WinHttpOpen("TerraLab", 1, None, None, 0)
    if not session:
        return
    options = Options()
    options.dwFlags = (0x2 if pac else 0) | (0x1 if config.fAutoDetect else 0)
    options.dwAutoDetectFlags = 0x3 if config.fAutoDetect else 0
    options.lpszAutoConfigUrl = pac or None
    options.fAutoLogonIfChallenged = True
    info = Info()
    if winhttp.WinHttpGetProxyForUrl(session, url, ctypes.byref(options), ctypes.byref(info)):
        for held in (info.lpszProxy, info.lpszProxyBypass):
            if held:
                kernel32.GlobalFree(held)
    winhttp.WinHttpCloseHandle(session)


def system_lookup(url: str) -> dict | None:





    _warm_system_lookup(url)
    found = _system_proxy(url)
    _resolve_cache[("system", url)] = (time.monotonic(), found)
    return dict(found) if found else None


_refreshing: set = set()


def _refresh(url: str) -> None:

    if url in _refreshing:
        return
    _refreshing.add(url)

    def work():
        try:
            system_lookup(url)
        except Exception:  # noqa: BLE001
            _resolve_cache[("system", url)] = (time.monotonic(), None)
        finally:
            _refreshing.discard(url)

    try:
        threading.Thread(target=work, name="terralab-proxy-lookup", daemon=True).start()
    except RuntimeError:
        _refreshing.discard(url)


def _plan(host: str, port: int, secure: bool) -> tuple[dict | None, str]:


    host = (host or "").lower()
    url = f"{'https' if secure else 'http'}://{host}:{port}/"
    if no_proxy_matches(host, os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""):
        return None, ""
    try:
        from qgis.core import QgsNetworkAccessManager
        from qgis.PyQt.QtNetwork import QNetworkProxy
    except ImportError:
        return env_proxy(), ""
    try:
        nam = QgsNetworkAccessManager.instance()
        if _excluded(url, host, getattr(nam, "noProxyList", list)()):
            return None, ""
        excluded = _excluded(url, host, getattr(nam, "excludeList", list)())
        fallback = _fallback_proxy(nam)
        ptype = getattr(QNetworkProxy, "ProxyType", QNetworkProxy)
        use_system = excluded or bool(getattr(nam, "useSystemProxy", lambda: False)()) \
            or fallback.type() == ptype.DefaultProxy
        if not excluded:
            found = _qt_proxy_dict(fallback)
            if found:
                return found, ""
        return (None, url) if use_system else (env_proxy(), "")
    except Exception:
        return env_proxy(), ""


def resolve_proxy(host: str, port: int = 443, secure: bool = True) -> dict | None:














    found, url = _plan(host, port, secure)
    if not url:
        return found
    try:
        hit = _resolve_cache.get(("system", url))
        if hit is None:
            return env_proxy() if url in _refreshing else system_lookup(url) or env_proxy()
        if time.monotonic() - hit[0] >= _RESOLVE_TTL_S:
            _refresh(url)
        return (dict(hit[1]) if hit[1] else None) or env_proxy()
    except Exception:
        return env_proxy()


def deferred_proxy(host: str, port: int = 443, secure: bool = True):






    generic = _plan("example.com", 443, True)[1]
    if generic:
        hit = _resolve_cache.get(("system", generic))
        if hit is None or time.monotonic() - hit[0] >= _RESOLVE_TTL_S:
            _refresh(generic)
    found, url = _plan(host, port, secure)
    if not url:
        return found, None
    hit = _resolve_cache.get(("system", url))
    if hit is not None and time.monotonic() - hit[0] < _RESOLVE_TTL_S:
        return (dict(hit[1]) if hit[1] else None) or env_proxy(), None

    def ask() -> dict | None:
        try:
            return system_lookup(url) or env_proxy()
        except Exception:  # noqa: BLE001
            return env_proxy()

    return None, ask


def typed_proxy() -> dict | None:


    try:
        from qgis.core import QgsNetworkAccessManager

        return _qt_proxy_dict(_fallback_proxy(QgsNetworkAccessManager.instance())) or None
    except Exception:  # noqa: BLE001
        return None


def qgis_exclusions() -> list[str]:







    try:
        from qgis.core import QgsNetworkAccessManager

        nam = QgsNetworkAccessManager.instance()
        if not _qt_proxy_dict(_fallback_proxy(nam)):
            return []

        return [_entry_host(part.strip()) for x in getattr(nam, "excludeList", list)()
                for part in str(x or "").split("|") if part.strip()]
    except Exception:  # noqa: BLE001
        return []


def _qgis_ignores_cert_errors(manager, host: str, port: int) -> bool:

    hostport = f"{host}:{port}"
    try:
        config = manager.sslCertCustomConfigByHost(hostport)
        if config is not None and not config.isNull() and config.sslIgnoredErrorEnums():
            return True
    except Exception:  # nosec B110
        pass
    try:
        cache = manager.ignoredSslErrorCache()
        return bool(cache and cache.get(hostport))
    except Exception:
        return False


def resolve_tls(host: str, port: int = 443) -> tuple[bytes | None, bool]:






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
    except Exception:  # nosec B110
        pass
    if ca_pem is None:
        try:
            from qgis.PyQt.QtNetwork import QSslConfiguration
            certs = QSslConfiguration.defaultConfiguration().caCertificates()
            ca_pem = b"".join(bytes(cert.toPem()) for cert in certs) or None
        except Exception:
            ca_pem = None
    return ca_pem, insecure
