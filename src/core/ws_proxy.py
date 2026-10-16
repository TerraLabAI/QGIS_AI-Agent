# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

import os
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


def resolve_proxy(host: str, port: int = 443, secure: bool = True) -> dict | None:









    host = (host or "").lower()
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
        fallback = _fallback_proxy(nam)
        ptype = getattr(QNetworkProxy, "ProxyType", QNetworkProxy)
        use_system = excluded or bool(getattr(nam, "useSystemProxy", lambda: False)()) \
            or fallback.type() == ptype.DefaultProxy
        if not excluded:
            found = _qt_proxy_dict(fallback)
            if found:
                return found
        if use_system:
            found = _resolved(("system", url), lambda: _system_proxy(url))
            if found:
                return found
        return env_proxy()
    except Exception:
        return env_proxy()


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
