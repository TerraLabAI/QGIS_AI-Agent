# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later














from __future__ import annotations

import json
import urllib.parse

from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest

OTHER = "__other__"
_MAX_BYTES = 8 * 1024 * 1024

_flag: dict | None = None
_tag = ""
_cache: dict = {}


def user_place() -> tuple:

    try:
        from .connector_locale import system_locale

        language, region = system_locale()
    except Exception:  # noqa: BLE001
        language, region = "", ""
    return str(region or "").upper(), str(language or "").lower()


def relevance(row: dict, country: str = "", language: str = "") -> tuple:

    covers = [str(c).upper() for c in (row.get("countries") or [])]
    languages = [str(x).lower().split("_")[0] for x in (row.get("languages") or [])]
    if country and str(row.get("country") or "").upper() == country:
        step = 0
    elif country and country in covers:
        step = 1
    elif language and language in languages:
        step = 2
    elif "GLOBAL" in covers:
        step = 3
    else:
        step = 4
    try:
        rank = int(row.get("rank") or 0)
    except (TypeError, ValueError):
        rank = 0
    try:
        datasets = int(row.get("datasets") or 0)
    except (TypeError, ValueError):
        datasets = 0
    return (step, -rank, -datasets, str(row.get("name") or row.get("title") or "").lower())


def apply_session(session: dict) -> None:

    global _flag, _tag
    value = session.get("sources_rows") if isinstance(session, dict) else None
    _flag = dict(value) if isinstance(value, dict) else None
    tag = str(session.get("catalog_tag") or "") if isinstance(session, dict) else ""
    if tag != _tag:
        _cache.clear()
    _tag = tag


def available() -> bool:
    return _flag is not None


def other_count() -> int:
    try:
        return int((_flag or {}).get("other") or 0)
    except (TypeError, ValueError):
        return 0


def _base() -> str:
    from .settings import Settings

    parts = urllib.parse.urlsplit(Settings().server_url)
    scheme = "http" if parts.scheme == "ws" else "https"
    return f"{scheme}://{parts.netloc}/sources/rows"


def _locale() -> str:
    from .i18n import current_locale

    return str(current_locale() or "").replace("-", "_").split("_")[0][:8]


def _disk_path(family: str, locale: str) -> str:
    import hashlib
    import os

    from ..ui.shared import PLUGIN_CACHE_DIR

    digest = hashlib.sha256(f"{family}|{locale}|{_tag}".encode()).hexdigest()[:32]
    return os.path.join(PLUGIN_CACHE_DIR, "source_rows", f"{digest}.json")


def _read_disk(family: str, locale: str) -> list | None:
    if not _tag:
        return None
    try:
        with open(_disk_path(family, locale), encoding="utf-8") as handle:
            rows = json.load(handle)
    except (OSError, ValueError):
        return None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else None


def _write_disk(family: str, locale: str, rows: list) -> None:
    if not _tag:
        return
    try:
        import os

        from .writeback import write_atomic

        path = _disk_path(family, locale)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_atomic(path, json.dumps(rows, ensure_ascii=False))
    except Exception:  # nosec B110
        pass


def cached(family: str) -> list | None:

    locale = _locale()
    hit = _cache.get((family, locale, _tag))
    if hit is None and family:
        hit = _read_disk(family, locale)
        if hit is not None:
            _cache[(family, locale, _tag)] = hit
    return hit


def fetch(on_done, family: str = "", query: str = "", country: str = ""):




    if not available():
        return None
    locale = _locale()
    if family:
        hit = cached(family)
        if hit is not None:
            on_done(list(hit), "")
            return None
    from qgis.core import QgsNetworkAccessManager

    from .settings import Settings

    key = Settings().activation_key
    if not key:
        on_done([], "no key")
        return None
    params = {"locale": locale}
    if family:
        params["family"] = family
    else:
        params["q"] = query
        if country:
            params["country"] = country
    request = QNetworkRequest(QUrl(_base() + "?" + urllib.parse.urlencode(params)))
    request.setRawHeader(b"Accept", b"application/json")
    request.setRawHeader(b"Authorization", f"Bearer {key}".encode())
    reply = QgsNetworkAccessManager.instance().get(request)

    def finished() -> None:
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                on_done([], reply.errorString() or "network error")
                return
            raw = bytes(reply.read(_MAX_BYTES))
            try:
                doc = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                on_done([], "unreadable answer")
                return
            rows = [r for r in (doc or {}).get("rows") or [] if isinstance(r, dict)]
            if family:
                _cache[(family, locale, _tag)] = rows
                _write_disk(family, locale, rows)
            on_done(list(rows), "")
        except RuntimeError:
            pass
        finally:
            reply.deleteLater()

    reply.finished.connect(finished)
    return reply


def abort(reply) -> None:
    if reply is None:
        return
    try:
        reply.finished.disconnect()
    except (RuntimeError, TypeError):
        pass
    try:
        reply.abort()
        reply.deleteLater()
    except RuntimeError:
        pass
