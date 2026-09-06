# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The numbers the server may change without waiting for a plugin release."""
























































from __future__ import annotations

import math
from typing import Any

from . import limits
from .logger import log, log_warning





_INT_LIMITS: dict[str, dict[str, tuple[int, int]]] = {



    "context": {
        "max_layers": (1, 500),
        "max_detailed_layers": (1, 100),
        "max_more_layer_names": (0, 60),
        "max_card_fields": (1, 40),
        "max_history_entries": (0, 20),
        "max_log_lines": (0, 20),
        "max_log_chars": (40, 2_000),
        "max_layout_names": (0, 60),
    },



    "results": {
        "max_result_chars": (10_000, 4_000_000),
        "max_string_chars": (1_000, 1_000_000),
    },



    "follow": {
        "settle_ms": (0, 2_000),
        "flash_ms": (0, 5_000),
    },



    "compose": {
        "enough_chars": (10, 400),
        "min_words": (1, 10),
        "min_chars": (1, 100),
        "min_words_with_context": (1, 10),
        "min_chars_with_context": (1, 100),
        "min_chars_unspaced": (1, 50),
    },





    "net": {
        "overpass_timeout_s": (20, 180),
        "download_timeout_s": (15, 300),




        "hosted_timeout_s": (20, 300),
        "footprints_timeout_s": (20, 300),




        "geocode_timeout_s": (10, 120),
        "route_timeout_s": (10, 180),


        "portal_timeout_s": (5, 60),
    },
}




_FLOAT_LIMITS: dict[str, dict[str, tuple[float, float]]] = {
    "follow": {
        "pad_share": (0.0, 1.0),
        "pan_share": (0.1, 1.0),
        "contained_share": (0.01, 1.0),
        "point_share": (0.01, 0.5),
        "approach_span_m": (50.0, 5_000.0),
        "approach_keep_ratio": (1.0, 100_000.0),
    },
}






_TIGHTEN_ONLY: dict[str, dict[str, tuple[float, float]]] = {
    "limits": {
        "run_max_steps": (5, limits.RUN_MAX_STEPS),
        "run_max_seconds": (60, limits.RUN_MAX_SECONDS),
        "max_layers_per_run": (1, limits.MAX_LAYERS_PER_RUN),
        "max_features_per_call": (100, limits.MAX_FEATURES_PER_CALL),
        "max_features_created": (1_000, limits.MAX_FEATURES_CREATED),
        "max_features_materialised": (10_000, limits.MAX_FEATURES_MATERIALISED),
        "max_fetch_km2": (1.0, limits.MAX_FETCH_KM2),







        "fetch_quiet_km2": (0.5, limits.FETCH_QUIET_KM2),
        "fetch_quiet_sparse_km2": (1.0, limits.FETCH_QUIET_SPARSE_KM2),
        "fetch_dense_max_km2": (5.0, limits.FETCH_DENSE_MAX_KM2),
        "fetch_own_overpass_max_km2": (5.0, limits.FETCH_OWN_OVERPASS_MAX_KM2),
        "fetch_hosted_quiet_km2": (1.0, limits.FETCH_HOSTED_QUIET_KM2),
        "fetch_hosted_quiet_sparse_km2": (1.0, limits.FETCH_HOSTED_QUIET_SPARSE_KM2),
        "fetch_quiet_features": (100, limits.FETCH_QUIET_FEATURES),
        "fetch_hard_max_features": (1_000, limits.FETCH_HARD_MAX_FEATURES),
        "max_download_bytes": (1_048_576, limits.MAX_DOWNLOAD_BYTES),
        "max_render_width_px": (256, limits.MAX_RENDER_WIDTH_PX),
        "max_render_height_px": (256, limits.MAX_RENDER_HEIGHT_PX),
        "max_render_pixels": (65_536, limits.MAX_RENDER_PIXELS),
        "max_render_dpi": (72, limits.MAX_RENDER_DPI),
    },
}

SECTIONS = ("context", "results", "follow", "compose", "net", "limits", "hosts", "services",
            "checks")

_BOOL_KEYS: dict[str, set[str]] = {



    "context": {"always_send_layer_id"},
}





_HOST_RATE = (0.05, 60.0)
_HOST_BURST = (1, 100)
_HOST_CONCURRENCY = (1, 8)










_SERVICE_URLS = frozenset({

    "overture_api",
    "overture_tiles",
    "osm_tiles",
    "footprints",
    "photon",

    "ban", "cartociudad", "pelias", "nominatim",
    "osrm_driving", "osrm_walking", "osrm_cycling",
    "stac_root", "pc_sign", "cdse_https_prefix",
})
_MAX_URL_CHARS = 300




_SERVICE_TOKENS = frozenset({
    "osm_themes", "overture_themes", "division_subtypes", "footprint_sources",
})
_MAX_TOKENS = 60
_MAX_TOKEN_CHARS = 40




_SERVICE_HOSTS = frozenset({"earthdata_hosts", "earthdata_suffixes"})
_MAX_HOSTS = 40



_CAP_KM2 = (1.0, 5_000.0)
_CAP_SPAN_DEG = (0.05, 10.0)
_MAX_CAP_ROWS = 60



_SILENT_PAGE = (1, 1_000_000)
_MAX_SILENT_PAGES = 32








_MAX_PORTAL_ROWS = 20
_MAX_SEARCH_URL_CHARS = 400
_MAX_PORTAL_NAME_CHARS = 60


def _clean_service_url(value: Any, where: str) -> str | None:
    """One base URL a row may point a tool at, or None with a line in the log."""
    if not isinstance(value, str):
        log_warning(f"Server policy {where} is not a string, ignored")
        return None
    text = value.strip()
    if not text or len(text) > _MAX_URL_CHARS:
        log_warning(f"Server policy {where} is empty or over {_MAX_URL_CHARS} characters, ignored")
        return None
    import urllib.parse

    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        log_warning(f"Server policy {where} cannot be parsed as a URL, ignored")
        return None
    if parts.scheme.lower() != "https" or not parts.hostname:
        log_warning(f"Server policy {where} is not an https URL, ignored")
        return None
    if parts.username or parts.password or "@" in parts.netloc:
        log_warning(f"Server policy {where} carries credentials, ignored")
        return None
    if parts.fragment:
        log_warning(f"Server policy {where} carries a fragment, ignored")
        return None



    try:
        from . import security

        if security.is_local_url(text, resolve=False) or security.is_private_url(text, resolve=False):
            log_warning(f"Server policy {where} names a local or private address, ignored")
            return None
    except Exception as exc:  # noqa: BLE001 - no security module is not a reason to obey
        log_warning(f"Server policy {where} could not be checked ({exc}), ignored")
        return None



    return text if where.endswith("_prefix") else text.rstrip("/")


def _clean_tokens(value: Any, where: str) -> list | None:
    """A list of short lowercase identifiers, deduplicated, order kept."""
    if not isinstance(value, list):
        log_warning(f"Server policy {where} is not a list, ignored")
        return None
    out: list = []
    for item in value[:_MAX_TOKENS]:
        if not isinstance(item, str):
            continue
        token = item.strip().lower()
        if not token or len(token) > _MAX_TOKEN_CHARS or not token.replace("_", "").isalnum():
            continue
        if token not in out:
            out.append(token)
    if not out:
        log_warning(f"Server policy {where} held no usable value, ignored")
        return None
    return out


def _clean_hosts(value: Any, where: str) -> list | None:
    """Hostnames or host suffixes, lowercased. A bare suffix keeps its dot."""
    if not isinstance(value, list):
        log_warning(f"Server policy {where} is not a list, ignored")
        return None
    out: list = []
    for item in value[:_MAX_HOSTS]:
        if not isinstance(item, str):
            continue
        host = item.strip().lower().strip("/")
        if not host or len(host) > 253 or "/" in host or ":" in host or " " in host:
            continue
        if host not in out:
            out.append(host)
    if not out:
        log_warning(f"Server policy {where} held no usable host, ignored")
        return None
    return out


def _clean_caps(value: Any, where: str) -> dict | None:
    """`{theme: (km2, degrees)}`: what one call may ask a hosted service for."""
    if not isinstance(value, dict):
        log_warning(f"Server policy {where} is not an object, ignored")
        return None
    out: dict = {}
    for theme, pair in list(value.items())[:_MAX_CAP_ROWS]:
        if not isinstance(theme, str) or not theme.strip():
            continue
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            log_warning(f"Server policy {where}.{theme} is not a [km2, degrees] pair, ignored")
            continue
        km2 = _clamp_float(pair[0], *_CAP_KM2, where=f"{where}.{theme}.km2")
        span = _clamp_float(pair[1], *_CAP_SPAN_DEG, where=f"{where}.{theme}.degrees")
        if km2 is None or span is None:
            continue
        out[theme.strip().lower()] = (km2, span)
    return out or None


def _clean_silent_pages(value: Any, where: str) -> list | None:
    if not isinstance(value, list):
        log_warning(f"Server policy {where} is not a list, ignored")
        return None
    out: list = []
    for item in value[:_MAX_SILENT_PAGES]:
        page = _clamp_int(item, *_SILENT_PAGE, where=where)
        if page is not None and page not in out:
            out.append(page)
    return sorted(out) or None


def _clean_portals(value: Any, where: str) -> dict | None:
    """The open data portals a row moved. Only keys the plugin already parses."""
    if not isinstance(value, dict):
        log_warning(f"Server policy {where} is not an object, ignored")
        return None
    out: dict = {}
    for key, row in list(value.items())[:_MAX_PORTAL_ROWS]:
        if not isinstance(key, str) or not isinstance(row, dict):
            continue
        spot = f"{where}.{key}"
        clean: dict = {}
        base = _clean_service_url(row.get("base_url"), f"{spot}.base_url") if row.get("base_url") else None
        if base:
            clean["base_url"] = base
        search = row.get("search_url")
        if isinstance(search, str) and search.strip():
            text = search.strip()




            if len(text) > _MAX_SEARCH_URL_CHARS:
                log_warning(f"Server policy {spot}.search_url is over {_MAX_SEARCH_URL_CHARS} characters, ignored")
            elif text.count("{query}") != 1 or set("{}") & set(text.replace("{query}", "")):
                log_warning(f"Server policy {spot}.search_url does not carry exactly one {{query}}, ignored")
            else:
                probe = _clean_service_url(text.replace("{query}", "q"), f"{spot}.search_url")
                if probe:
                    clean["search_url"] = text
        name = row.get("name")
        if isinstance(name, str) and name.strip():
            clean["name"] = name.strip()[:_MAX_PORTAL_NAME_CHARS]
        if clean:
            out[key] = clean
    return out or None


def _read_services(raw: Any) -> dict:
    """The `services` section, key by key. An unknown key is dropped."""
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for key, value in list(raw.items())[:100]:
        if not isinstance(key, str):
            continue
        where = f"services.{key}"
        if key in _SERVICE_URLS:
            clean = _clean_service_url(value, where)
        elif key in _SERVICE_TOKENS:
            clean = _clean_tokens(value, where)
        elif key in _SERVICE_HOSTS:
            clean = _clean_hosts(value, where)
        elif key == "hosted_caps":
            clean = _clean_caps(value, where)
        elif key == "wfs_silent_pages":
            clean = _clean_silent_pages(value, where)
        elif key == "open_data_portals":
            clean = _clean_portals(value, where)
        else:
            continue
        if clean is not None:
            out[key] = clean
    return out














_CHECK_TABLES = frozenset({
    "metric_algs",
    "join_algorithms",
    "overlay_algorithms",
    "reduces_rows",
    "preserves_rows",
    "field_to_a_copy",
    "raster_preserves",
    "raster_masked",
})


_ALG_ID_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789_:"
_MAX_ALG_NAMES = 200
_MAX_ALG_CHARS = 80


def _clean_alg_names(value: Any, where: str) -> list | None:
    """The algorithm ids a row adds to one table, lowercased and deduplicated."""
    if not isinstance(value, list):
        log_warning(f"Server policy {where} is not a list, ignored")
        return None
    out: list = []
    for item in value[:_MAX_ALG_NAMES]:
        name = str(item or "").strip().lower()
        if not name or len(name) > _MAX_ALG_CHARS or name.count(":") != 1:
            continue
        provider, _, alg = name.partition(":")
        if not provider or not alg:
            continue
        if set(name) - set(_ALG_ID_CHARS):
            continue
        if name not in out:
            out.append(name)
    return out or None


def _read_checks(raw: Any) -> dict:
    """The `checks` section. An unknown table name is dropped."""
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for key, value in list(raw.items())[:100]:
        if not isinstance(key, str) or key not in _CHECK_TABLES:
            continue
        clean = _clean_alg_names(value, f"checks.{key}")
        if clean is not None:
            out[key] = clean
    return out


def _blank() -> dict[str, Any]:
    doc: dict[str, Any] = {"version": 0}
    doc.update({section: {} for section in SECTIONS})
    return doc


_EMPTY: dict[str, Any] = _blank()


_DOC: dict[str, Any] = _EMPTY




_LISTENERS: list[Any] = []


def _clamp_int(value: Any, low: int, high: int, where: str) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        log_warning(f"Server policy {where} is not an integer, ignored")
        return None
    if value < low or value > high:
        log_warning(f"Server policy {where}={value} is outside {low}..{high}, clamped")
    return max(low, min(high, value))


def _clamp_float(value: Any, low: float, high: float, where: str) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        log_warning(f"Server policy {where} is not a number, ignored")
        return None
    if isinstance(value, float) and not math.isfinite(value):
        log_warning(f"Server policy {where} is not finite, ignored")
        return None
    if value < low or value > high:
        log_warning(f"Server policy {where}={value} is outside {low}..{high}, clamped")
    return max(low, min(high, float(value)))


def _tighten(value: Any, low: float, shipped: float, where: str) -> int | float | None:
    """A ceiling the server may only lower. Above the shipped value is the shipped value."""
    if isinstance(shipped, int):
        if isinstance(value, bool) or not isinstance(value, int):
            log_warning(f"Server policy {where} is not an integer, ignored")
            return None
    elif isinstance(value, bool) or not isinstance(value, (int, float)):
        log_warning(f"Server policy {where} is not a number, ignored")
        return None
    if isinstance(value, float) and not math.isfinite(value):
        log_warning(f"Server policy {where} is not finite, ignored")
        return None
    if value > shipped:
        log_warning(f"Server policy {where}={value} is above the shipped {shipped}, "
                    "a ceiling only comes down, kept at the shipped value")
        return shipped
    if value < low:
        log_warning(f"Server policy {where}={value} is under the floor {low}, clamped")
        return type(shipped)(low)
    return value if isinstance(shipped, int) else float(value)


def _read_hosts(raw: Any) -> dict[str, tuple[float, int, int]]:
    """Per-host limits, keyed exactly as `net.HOST_POLICIES` keys them."""






    out: dict[str, tuple[float, int, int]] = {}
    if not isinstance(raw, dict):
        return out
    for host, spec in list(raw.items())[:200]:
        if not isinstance(host, str) or not host or not isinstance(spec, dict):
            continue
        rate = _clamp_float(spec.get("rate"), *_HOST_RATE, where=f"hosts.{host}.rate")
        burst = _clamp_int(spec.get("burst"), *_HOST_BURST, where=f"hosts.{host}.burst")
        concurrency = _clamp_int(spec.get("concurrency"), *_HOST_CONCURRENCY,
                                 where=f"hosts.{host}.concurrency")
        if rate is None or burst is None or concurrency is None:
            continue
        out[host.strip().lower()[:253]] = (rate, burst, concurrency)
    return out


def apply(policy: Any) -> bool:
    """Take the `policy` object out of a session frame."""





    global _DOC
    if not isinstance(policy, dict) or not policy:
        if _DOC is not _EMPTY:
            _DOC = _EMPTY
            log("Server policy cleared, shipped defaults are back")
            _notify()
            return True
        return False

    doc = _blank()
    version = policy.get("version")
    doc["version"] = version if isinstance(version, int) and not isinstance(version, bool) else 0

    for section, keys in _INT_LIMITS.items():
        raw = policy.get(section)
        if not isinstance(raw, dict):
            continue
        for name, (low, high) in keys.items():
            if name not in raw:
                continue
            value = _clamp_int(raw[name], low, high, where=f"{section}.{name}")
            if value is not None:
                doc[section][name] = value

    for section, keys in _FLOAT_LIMITS.items():
        raw = policy.get(section)
        if not isinstance(raw, dict):
            continue
        for name, (low, high) in keys.items():
            if name not in raw:
                continue
            number = _clamp_float(raw[name], low, high, where=f"{section}.{name}")
            if number is not None:
                doc[section][name] = number

    for section, keys in _TIGHTEN_ONLY.items():
        raw = policy.get(section)
        if not isinstance(raw, dict):
            continue
        for name, (low, shipped) in keys.items():
            if name not in raw:
                continue
            number = _tighten(raw[name], low, shipped, where=f"{section}.{name}")
            if number is not None:
                doc[section][name] = number

    for section, names in _BOOL_KEYS.items():
        raw = policy.get(section)
        if not isinstance(raw, dict):
            continue
        for name in names:
            if isinstance(raw.get(name), bool):
                doc[section][name] = raw[name]

    doc["hosts"] = _read_hosts(policy.get("hosts"))
    doc["services"] = _read_services(policy.get("services"))
    doc["checks"] = _read_checks(policy.get("checks"))

    if doc == _DOC:
        return False
    _DOC = doc
    tuned = sum(len(v) for k, v in doc.items() if isinstance(v, dict))
    log(f"Server policy v{doc['version']} applied, {tuned} values tuned")
    _notify()
    return True


def _notify() -> None:
    for listener in list(_LISTENERS):
        try:
            listener()
        except Exception as exc:  # noqa: BLE001 - one reader's mistake must not lose the policy
            log_warning(f"Server policy listener failed: {exc}")


def subscribe(listener: Any) -> None:
    """Call `listener()` after every change, including a reset. Socket thread."""
    if listener not in _LISTENERS:
        _LISTENERS.append(listener)


def limit(section: str, name: str, default: int) -> int:
    """The server's number for this knob, or the one the plugin shipped with."""
    value = _DOC.get(section, {}).get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def number(section: str, name: str, default: float) -> float:
    """Same as `limit`, for a knob that may be a float."""
    value = _DOC.get(section, {}).get(name)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def flag(section: str, name: str, default: bool) -> bool:
    value = _DOC.get(section, {}).get(name)
    return value if isinstance(value, bool) else default


def host_policy(host: str) -> tuple[float, int, int] | None:
    """The server's rate for one host, exact match then parent domain."""
    hosts: dict[str, tuple[float, int, int]] = _DOC.get("hosts") or {}
    if not hosts:
        return None
    host = (host or "").strip().lower()
    found = hosts.get(host)
    if found is not None:
        return found



    parts = host.split(".")
    for cut in range(0, len(parts) - 1):
        found = hosts.get("." + ".".join(parts[cut:]))
        if found is not None:
            return found
    return None


def service_url(key: str, default: str) -> str:
    """The base URL a row gave this service, or the one the plugin shipped with."""




    value = (_DOC.get("services") or {}).get(key)
    return value if isinstance(value, str) and value else default


def service_list(key: str, default):
    """The themes, sources, subtypes or hosts a row named, else the shipped tuple."""
    value = (_DOC.get("services") or {}).get(key)
    return tuple(value) if isinstance(value, list) and value else default


def service_caps(key: str, default: dict) -> dict:
    """`{theme: (km2, degrees)}` as a row gave it, else the shipped table."""
    value = (_DOC.get("services") or {}).get(key)
    return dict(value) if isinstance(value, dict) and value else default


def service_rows(key: str, default: dict) -> dict:
    """`{row_key: {field: value}}` merged over the shipped table, field by field."""






    served = (_DOC.get("services") or {}).get(key)
    if not isinstance(served, dict) or not served:
        return default
    out = {name: dict(row) for name, row in default.items()}
    for name, row in served.items():
        if name in out and isinstance(row, dict):
            out[name].update(row)
    return out


def check_algs(key: str, shipped) -> frozenset:
    """The shipped algorithm table widened by whatever a row added to it."""





    added = (_DOC.get("checks") or {}).get(key)
    if not isinstance(added, list) or not added:
        return frozenset(shipped)
    return frozenset(shipped) | frozenset(added)


def snapshot() -> dict[str, Any]:
    """What is in force, for the diagnostic report and for the tests."""
    snap: dict[str, Any] = {"version": _DOC.get("version", 0)}
    for section in SECTIONS:
        if section != "hosts":
            snap[section] = dict(_DOC.get(section) or {})
    snap["hosts"] = {h: {"rate": r, "burst": b, "concurrency": c}
                     for h, (r, b, c) in (_DOC.get("hosts") or {}).items()}

    services = dict(snap.get("services") or {})
    caps = services.get("hosted_caps")
    if isinstance(caps, dict):
        services["hosted_caps"] = {theme: list(pair) for theme, pair in caps.items()}
    snap["services"] = services
    return snap


def reset() -> None:
    """Back to the shipped defaults. For the tests and for a lost session."""
    global _DOC
    changed = _DOC is not _EMPTY
    _DOC = _EMPTY
    if changed:
        _notify()
