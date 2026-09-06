# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the session frame says about the catalog beyond the connector rows."""















from __future__ import annotations

import contextlib
import urllib.parse

from . import net, security, tuning
from .logger import log_warning


FALLBACK_BASEMAPS: tuple = (
    {
        "id": "openstreetmap",
        "name": "OpenStreetMap",
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attribution": "© OpenStreetMap contributors",
        "max_zoom": 19,
    },
)

FALLBACK_OVERPASS = "https://overpass-api.de/api/interpreter"

FALLBACK_SHELF_CAP = 6


_SHELF_CAP_MIN, _SHELF_CAP_MAX = 3, 24
_MAX_BASEMAPS = 40
_MAX_MIRRORS = 8

_basemaps: list = []
_mirrors: list = []
_shelves: dict = {}


def _looks_like_xyz(url: str) -> bool:
    return "{z}" in url and "{x}" in url and "{y}" in url


def _fetchable(url: str) -> bool:
    """A public http(s) URL, judged from its text alone."""






    try:
        scheme = urllib.parse.urlsplit(url).scheme.lower()
    except ValueError:
        return False
    if scheme not in ("http", "https"):
        return False
    return not security.is_local_url(url, resolve=False) and not security.is_private_url(url, resolve=False)


def _clean_basemap(row) -> dict | None:
    if not isinstance(row, dict):
        return None
    key = str(row.get("id") or "").strip().lower()
    url = str(row.get("url") or "").strip()
    if not key or not _looks_like_xyz(url) or not _fetchable(url):
        return None
    try:
        zoom = int(row.get("max_zoom") or 19)
    except (TypeError, ValueError):
        zoom = 19
    return {
        "id": key,
        "name": str(row.get("name") or key),
        "url": url,
        "attribution": str(row.get("attribution") or ""),
        "max_zoom": max(1, min(zoom, 24)),
    }


def set_basemaps(rows) -> None:
    """Keep the presets the server named, and remember them for the next start."""
    global _basemaps
    cleaned = []
    seen = set()
    for row in (rows or [])[:_MAX_BASEMAPS]:
        clean = _clean_basemap(row)
        if clean is not None and clean["id"] not in seen:
            seen.add(clean["id"])
            cleaned.append(clean)
    if not cleaned or cleaned == _basemaps:
        return
    _basemaps = cleaned

    with contextlib.suppress(Exception):
        from .settings import Settings

        Settings().known_basemaps = cleaned


def basemaps() -> dict:
    """``{id: {name, url, attribution, max_zoom}}``: served, else cached, else shipped."""
    global _basemaps
    if not _basemaps:
        try:
            from .settings import Settings

            _basemaps = [c for c in (_clean_basemap(r) for r in Settings().known_basemaps) if c]
        except Exception:  # noqa: BLE001 - no settings backend is the shipped list
            _basemaps = []
    rows = _basemaps or [dict(r) for r in FALLBACK_BASEMAPS]
    return {row["id"]: dict(row) for row in rows}


def _mirror_allowed(url: str) -> bool:
    """An https URL the plugin may fetch, on a host someone has stated a rate for."""














    try:
        parts = urllib.parse.urlsplit(str(url or "").strip())
    except ValueError:
        return False
    if parts.scheme.lower() != "https" or not parts.hostname or not _fetchable(url):
        return False
    return net.host_is_stated(parts.hostname) or tuning.host_policy(parts.hostname) is not None


def _mirror_dedup_key(url: str) -> str:
    """Same host and path read as the same mirror regardless of case or a trailing slash: the server can otherwise send both."""


    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return url.strip()
    path = parts.path.rstrip("/")
    return urllib.parse.urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, parts.query, parts.fragment)
    )





_dropped_said: set = set()


def _log_dropped_mirror(url) -> None:
    """Say once which served mirror this build cannot reach, and why."""
    try:
        host = urllib.parse.urlsplit(str(url or "").strip()).hostname
    except ValueError:
        host = None
    host = (host or str(url or "").strip() or "?").lower()
    if host in _dropped_said:
        return
    _dropped_said.add(host)
    log_warning(
        f"Overpass mirror {host} served by the backend is not used: neither this build "
        "(core/net.HOST_POLICIES) nor the policy document states a rate for that host. "
        "Add a `hosts` row for it beside the mirror list."
    )


def set_overpass_mirrors(urls) -> None:
    """Keep the served order of the mirrors this build already reaches."""















    global _mirrors
    kept = []
    seen = set()
    for url in (urls or [])[:_MAX_MIRRORS]:
        if not isinstance(url, str) or not _mirror_allowed(url):
            _log_dropped_mirror(url)
            continue
        clean = url.strip()
        key = _mirror_dedup_key(clean)
        if key in seen:
            continue
        seen.add(key)
        kept.append(clean)
    _mirrors = kept


def overpass_mirrors() -> list:
    """Best first. The shipped instance alone until a server has sent a list."""
    return list(_mirrors) or [FALLBACK_OVERPASS]


def set_connector_shelves(payload) -> None:
    global _shelves
    if not isinstance(payload, dict):
        return
    out: dict = {}
    try:
        cap = int(payload.get("cap"))
    except (TypeError, ValueError):
        cap = 0
    if cap:
        out["cap"] = max(_SHELF_CAP_MIN, min(cap, _SHELF_CAP_MAX))
    order = payload.get("order")
    if isinstance(order, list):
        out["order"] = [str(k) for k in order if isinstance(k, str) and k]
    _shelves = out


def shelf_cap() -> int:
    return int(_shelves.get("cap") or FALLBACK_SHELF_CAP)


def shelf_order() -> list:
    """The served shelf keys top to bottom, or an empty list for the page's own rule."""
    return list(_shelves.get("order") or [])


def apply_session(session: dict) -> None:
    """Read the three lists out of a session frame. Absent fields change nothing."""
    if not isinstance(session, dict):
        return
    rows = session.get("basemaps")
    if isinstance(rows, list) and rows:
        set_basemaps(rows)
    mirrors = session.get("overpass_mirrors")
    if isinstance(mirrors, list) and mirrors:
        set_overpass_mirrors(mirrors)
    shelves = session.get("connector_shelves")
    if isinstance(shelves, dict) and shelves:
        set_connector_shelves(shelves)
