# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the session frame says about the catalog beyond the connector rows."""


















from __future__ import annotations

import contextlib
import urllib.parse

from . import data_date, net, security, tuning
from .logger import log_warning











FALLBACK_BASEMAPS: tuple = (
    {
        "id": "terralab_basemap",
        "name": "TerraLab basemap",
        "kind": "vectortile",
        "url": "https://aca-terralab-tiles.proudsky-7d379d48.westeurope.azurecontainerapps.io/planet.json",
        "style": "https://stterralabopendata.blob.core.windows.net/osm/basemap/style/light.json",
        "attribution": "© OpenStreetMap contributors, Protomaps",
        "max_zoom": 15,
        "fallback": "openfreemap_liberty",
    },
    {
        "id": "terralab_satellite",
        "name": "TerraLab satellite",
        "url": "https://aca-terralab-tiles.proudsky-7d379d48.westeurope.azurecontainerapps.io"
               "/satellite-s2-2021/{z}/{x}/{y}.jpg",
        "attribution": "© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) "
                       "processed by ESA WorldCover consortium",
        "max_zoom": 14,
        "fallback": "nasa_blue_marble",
    },
    {
        "id": "openfreemap_liberty",
        "name": "OpenFreeMap Liberty",
        "kind": "vectortile",
        "url": "https://tiles.openfreemap.org/planet",
        "style": "https://tiles.openfreemap.org/styles/liberty",
        "attribution": "OpenFreeMap © OpenMapTiles Data from OpenStreetMap",
        "max_zoom": 14,
        "fallback": "openstreetmap",
    },
    {
        "id": "openstreetmap",
        "name": "OpenStreetMap",
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attribution": "© OpenStreetMap contributors",
        "max_zoom": 19,
    },
    {
        "id": "nasa_blue_marble",
        "name": "NASA Blue Marble",
        "url": "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_NextGeneration/default/default"
               "/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg",
        "attribution": "NASA Blue Marble, imagery from NASA's Global Imagery Browse Services (GIBS)",
        "max_zoom": 8,
    },
)

FALLBACK_OVERPASS = "https://overpass-api.de/api/interpreter"



FALLBACK_OWN_OVERPASS = "https://overpass.terra-lab.ai/api/interpreter"
FALLBACK_MIRRORS = (FALLBACK_OWN_OVERPASS, FALLBACK_OVERPASS)

FALLBACK_SHELF_CAP = 6


_SHELF_CAP_MIN, _SHELF_CAP_MAX = 3, 24
_MAX_BASEMAPS = 40
_MAX_MIRRORS = 8
_MAX_SOURCE_LICENCES = 2000

_basemaps: list = []
_mirrors: list = []
_shelves: dict = {}
_source_licences: list = []


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
    kind = str(row.get("kind") or "xyz").strip().lower()
    if not key or kind not in ("xyz", "vectortile") or not _fetchable(url):
        return None



    if kind == "xyz" and not _looks_like_xyz(url):
        return None
    try:
        zoom = int(row.get("max_zoom") or 19)
    except (TypeError, ValueError):
        zoom = 19
    clean = {
        "id": key,
        "name": str(row.get("name") or key),
        "url": url,
        "attribution": str(row.get("attribution") or ""),
        "max_zoom": max(1, min(zoom, 24)),
    }
    if kind == "vectortile":
        style = str(row.get("style") or "").strip()
        clean["kind"] = kind
        clean["style"] = style if style and _fetchable(style) else ""
    fallback = str(row.get("fallback") or "").strip().lower()
    if fallback and fallback != key:
        clean["fallback"] = fallback
    return clean


def set_basemaps(rows, authoritative: bool = False) -> None:
    """Keep the presets the server named, and remember them for the next start."""






    global _basemaps
    cleaned = []
    seen = set()
    for row in (rows or [])[:_MAX_BASEMAPS]:
        clean = _clean_basemap(row)
        if clean is not None and clean["id"] not in seen:
            seen.add(clean["id"])
            cleaned.append(clean)
    if not cleaned and authoritative:

        _basemaps = []
        with contextlib.suppress(Exception):
            from .settings import Settings

            Settings().known_basemaps = []
        return
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
    """Best first. Our own instance, then the reference one, until a server has sent a list."""
    return list(_mirrors) or list(FALLBACK_MIRRORS)


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


def _row_extras(row: dict) -> dict:
    """The source sheet facts a newer server adds to a row, each optional: ``{title, data_date, page_url}``."""




    page_url = str(row.get("page_url") or "").strip()
    if not page_url.startswith(("https://", "http://")) or len(page_url) > 500:
        page_url = ""
    return {"title": str(row.get("title") or "").strip()[:200],
            "data_date": data_date.clean(row.get("data_date")),
            "page_url": page_url}


def set_source_licences(rows) -> None:
    """Keep the licence rows the server named: ``[{prefix, layer, licence, attribution}]``."""




    global _source_licences
    if not isinstance(rows, list):
        return
    cleaned = []
    for row in rows[:_MAX_SOURCE_LICENCES]:
        if not isinstance(row, dict):
            continue
        prefix = str(row.get("prefix") or "").strip()
        if prefix.startswith(("http://", "https://")):


            if len(prefix) < 10:
                continue
        elif not any(prefix.startswith(kind) and len(prefix) > len(kind) for kind in ("stac:", "overture:")):


            continue
        layer = str(row.get("layer") or "").strip()
        licence = str(row.get("licence") or "").strip()[:300]
        attribution = str(row.get("attribution") or "").strip()[:500]
        extras = _row_extras(row)
        if not licence and not attribution and not any(extras.values()):
            continue
        cleaned.append({"prefix": prefix, "layer": layer, "licence": licence, "attribution": attribution,
                        **extras})



    _source_licences = cleaned


def theme_licence(key: str) -> dict | None:
    """The row served under exactly *key* (``overture:<theme>``): ``{licence, attribution, title, data_date, page_url}`` or None, the last three."""





    for row in _source_licences:
        if row["prefix"] == key:
            return _answer(row)
    return None


def source_licence(url: str, layer: str = "") -> dict | None:
    """The row for a source address: ``{licence, attribution, title, data_date, page_url}`` or None."""
    url = str(url or "").strip()
    if url.startswith("/vsicurl/"):
        url = url[len("/vsicurl/"):]
    if not url:
        return None
    names = {part.strip().lower() for part in str(layer or "").split(",") if part.strip()}
    best = None
    best_key = (-1, -1)
    for row in _source_licences:
        prefix = row["prefix"]
        if not url.startswith(prefix):
            continue
        row_layer = row["layer"].strip().lower()
        if row_layer and row_layer not in names:
            continue


        key = (1 if row_layer else 0, len(prefix))
        if key > best_key:
            best_key, best = key, row
    if best is None:
        return None
    return _answer(best)


def _answer(row: dict) -> dict:
    """A kept row as its lookups hand it out, every key present."""
    return {"licence": row["licence"], "attribution": row["attribution"], "title": row.get("title", ""),
            "data_date": row.get("data_date", ""), "page_url": row.get("page_url", "")}


def apply_session(session: dict) -> None:
    """Read the served lists out of a session frame."""







    if not isinstance(session, dict):
        return
    rows = session.get("basemaps")
    reset = session.get("basemaps_reset") is True
    if isinstance(rows, list) and (rows or reset):
        set_basemaps(rows, authoritative=reset)
    mirrors = session.get("overpass_mirrors")
    if isinstance(mirrors, list) and mirrors:
        set_overpass_mirrors(mirrors)
    shelves = session.get("connector_shelves")
    if isinstance(shelves, dict) and shelves:
        set_connector_shelves(shelves)
    licences = session.get("source_licences")
    if isinstance(licences, list):
        set_source_licences(licences)
