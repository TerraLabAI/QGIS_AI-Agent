# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






























































from __future__ import annotations

import math
import re
from typing import Any

from . import limits
from .logger import log, log_warning
from .tuning_tables import _clamp_float, _clamp_int, _read_checks, _read_names, _read_services





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


        "max_rich_layers": (0, 25),
        "min_rich_layers": (0, 25),
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



        "own_geocode_timeout_s": (2, 60),




        "osrm_match_max_coordinates": (5, 100),
        "osrm_match_max_radius_m": (5, 100),




        "retry_max": (0, 5),
        "refusal_retry_max": (0, 5),








        "ws_heartbeat_s": (10, 120),
        "ws_run_heartbeat_s": (2, 15),
        "ws_hello_timeout_s": (5, 120),
        "ws_backoff_min_s": (1, 30),
        "ws_backoff_max_s": (5, 600),
        "ws_connect_timeout_s": (5, 120),
        "ws_send_timeout_s": (30, 600),
        "ws_idle_ping_s": (15, 300),
        "ws_dead_after_s": (30, 900),
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




    "net": {
        "retry_base_s": (0.5, 10.0),
        "refusal_retry_base_s": (0.1, 10.0),
        "refusal_max_wait_s": (5.0, 120.0),
        "cooldown_min_s": (1.0, 60.0),
        "cooldown_max_s": (10.0, 600.0),
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
        "fetch_hosted_quiet_km2": (1.0, limits.FETCH_HOSTED_QUIET_KM2),
        "fetch_hosted_quiet_sparse_km2": (1.0, limits.FETCH_HOSTED_QUIET_SPARSE_KM2),
        "fetch_hard_max_features": (1_000, limits.FETCH_HARD_MAX_FEATURES),
        "max_download_bytes": (1_048_576, limits.MAX_DOWNLOAD_BYTES),
        "max_render_width_px": (256, limits.MAX_RENDER_WIDTH_PX),
        "max_render_height_px": (256, limits.MAX_RENDER_HEIGHT_PX),
        "max_render_pixels": (65_536, limits.MAX_RENDER_PIXELS),
        "max_render_dpi": (72, limits.MAX_RENDER_DPI),


        "fetch_own_overpass_region_km2": (limits.MAX_FETCH_KM2, limits.FETCH_OWN_OVERPASS_REGION_KM2),
        "fetch_own_overpass_split_km2": (limits.MAX_FETCH_KM2, limits.FETCH_OWN_OVERPASS_SPLIT_KM2),




        "map_match_max_requests": (50, limits.MAP_MATCH_MAX_REQUESTS),


        "isochrone_max_roads": (10_000, limits.ISOCHRONE_MAX_ROADS),
        "sync_feature_loop_max": (250, limits.SYNC_FEATURE_LOOP_MAX),
        "geometry_check_max_vertices": (50_000, limits.GEOMETRY_CHECK_MAX_VERTICES),
        "geometry_check_max_total_vertices": (500_000, limits.GEOMETRY_CHECK_MAX_TOTAL_VERTICES),

        "geometry_check_seconds": (2.0, limits.GEOMETRY_CHECK_SECONDS),

        "overture_lifted_read_seconds": (5.0, limits.OVERTURE_LIFTED_READ_SECONDS),
        "terrain_max_cells": (16_000_000, limits.TERRAIN_MAX_CELLS),
        "hydrology_max_cells": (1_000_000, limits.HYDROLOGY_MAX_CELLS),
        "georeference_max_pixels": (40_000_000, limits.GEOREFERENCE_MAX_PIXELS),
        "chart_max_features": (100_000, limits.CHART_MAX_FEATURES),
        "chart_max_bins": (10, limits.CHART_MAX_BINS),
        "chart_max_categories": (5, limits.CHART_MAX_CATEGORIES),
        "chart_max_points": (1_000, limits.CHART_MAX_POINTS),
        "animation_max_frames": (250, limits.ANIMATION_MAX_FRAMES),
        "processing_batch_parallel": (1, limits.PROCESSING_BATCH_PARALLEL),

        "max_stream_bytes": (limits.MAX_DOWNLOAD_BYTES, limits.MAX_STREAM_BYTES),







        "call_max_seconds_main": (8.0, limits.CALL_MAX_SECONDS_MAIN),
        "call_max_seconds_main_long": (30.0, limits.CALL_MAX_SECONDS_MAIN_LONG),
        "call_max_seconds_background": (60.0, limits.CALL_MAX_SECONDS_BACKGROUND),
        "watchdog_tick_s": (0.5, limits.WATCHDOG_TICK_S),
        "watchdog_blocked_s": (3.0, limits.WATCHDOG_BLOCKED_S),


        "execute_code_max_seconds": (20.0, limits.EXECUTE_CODE_MAX_SECONDS),


        "dataset_docs_total_seconds": (1.0, limits.DATASET_DOCS_TOTAL_SECONDS),
        "dataset_docs_probe_seconds": (1.0, limits.DATASET_DOCS_PROBE_SECONDS),
        "dataset_docs_connect_seconds": (0.5, limits.DATASET_DOCS_CONNECT_SECONDS),



        "prompt_max_chars": (10_000, limits.PROMPT_MAX_CHARS),
        "attachments_total_bytes": (1_048_576, limits.ATTACHMENTS_TOTAL_BYTES),




    },
}

SECTIONS = ("context", "results", "follow", "compose", "net", "limits", "hosts", "services",
            "checks", "execute_code", "files", "names", "ceilings", "thresholds")

_BOOL_KEYS: dict[str, set[str]] = {



    "context": {"always_send_layer_id"},








    "results": {"match_image", "match_image_url", "layer_facts"},




}








_OFF_ONLY: dict[str, set[str]] = {
    "execute_code": {"isolated_enabled", "tripwire_enabled"},


    "files": {"read_scope_enabled"},
}





_HOST_RATE = (0.05, 60.0)
_HOST_BURST = (1, 100)
_HOST_CONCURRENCY = (1, 8)














_NUMBER_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}")
_MAX_NUMBER_KEYS = 200
_SAID: set = set()


def _read_numbers(raw: Any, section: str) -> dict:
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for key, value in list(raw.items())[:_MAX_NUMBER_KEYS]:
        if not isinstance(key, str) or not _NUMBER_KEY.fullmatch(key):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            log_warning(f"Server policy {section}.{key} is not a number, ignored")
            continue
        if isinstance(value, float) and not math.isfinite(value):
            log_warning(f"Server policy {section}.{key} is not finite, ignored")
            continue
        out[key] = value
    return out


def _say_once(text: str) -> None:
    if text not in _SAID:
        _SAID.add(text)
        log_warning(text)


def ceiling(key: str, shipped, floor):




    value = (_DOC.get("ceilings") or {}).get(key)
    if value is None:
        return shipped
    if isinstance(shipped, int) and not isinstance(value, int):
        _say_once(f"Server policy ceilings.{key}={value} is not an integer, ignored")
        return shipped
    if value > shipped:
        _say_once(f"Server policy ceilings.{key}={value} is above the shipped {shipped}, "
                  "a ceiling only comes down, kept at the shipped value")
        return shipped
    if value < floor:
        _say_once(f"Server policy ceilings.{key}={value} is under the floor {floor}, clamped")
        return type(shipped)(floor)
    return type(shipped)(value)


def threshold(key: str, shipped, low, high):

    value = (_DOC.get("thresholds") or {}).get(key)
    if value is None:
        return shipped
    if isinstance(shipped, int) and not isinstance(value, int):
        _say_once(f"Server policy thresholds.{key}={value} is not an integer, ignored")
        return shipped
    if value < low or value > high:
        _say_once(f"Server policy thresholds.{key}={value} is outside {low}..{high}, clamped")
    return type(shipped)(max(low, min(high, value)))


def _blank() -> dict[str, Any]:
    doc: dict[str, Any] = {"version": 0}
    doc.update({section: {} for section in SECTIONS})
    return doc


_EMPTY: dict[str, Any] = _blank()


_DOC: dict[str, Any] = _EMPTY




_LISTENERS: list[Any] = []


def _tighten(value: Any, low: float, shipped: float, where: str) -> int | float | None:

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






    out: dict[str, tuple[float, int, int]] = {}
    if not isinstance(raw, dict):
        return out


    for host, spec in list(raw.items())[:1000]:
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






    global _DOC
    if not isinstance(policy, dict) or not policy:
        if _DOC is not _EMPTY:
            _DOC = _EMPTY
            _SAID.clear()
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

    for section, names in _OFF_ONLY.items():
        raw = policy.get(section)
        if not isinstance(raw, dict):
            continue
        for name in names:
            if raw.get(name) is False:
                doc[section][name] = False
            elif name in raw:
                log_warning(f"Server policy {section}.{name} may only switch it off, ignored")

    doc["hosts"] = _read_hosts(policy.get("hosts"))
    doc["services"] = _read_services(policy.get("services"))
    doc["checks"] = _read_checks(policy.get("checks"))
    doc["names"] = _read_names(policy.get("names"))
    doc["ceilings"] = _read_numbers(policy.get("ceilings"), "ceilings")
    doc["thresholds"] = _read_numbers(policy.get("thresholds"), "thresholds")

    if doc == _DOC:
        return False
    _DOC = doc
    _SAID.clear()
    tuned = sum(len(v) for k, v in doc.items() if isinstance(v, dict))
    log(f"Server policy v{doc['version']} applied, {tuned} values tuned")
    _notify()
    return True


def _notify() -> None:
    for listener in list(_LISTENERS):
        try:
            listener()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Server policy listener failed: {exc}")


def subscribe(listener: Any) -> None:

    if listener not in _LISTENERS:
        _LISTENERS.append(listener)


def limit(section: str, name: str, default: int) -> int:

    value = _DOC.get(section, {}).get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def number(section: str, name: str, default: float) -> float:

    value = _DOC.get(section, {}).get(name)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def flag(section: str, name: str, default: bool) -> bool:
    value = _DOC.get(section, {}).get(name)
    return value if isinstance(value, bool) else default


def socket_clocks(shipped: dict) -> dict:









    out = {name: float(number("net", f"ws_{name}", value)) for name, value in shipped.items()}
    if "heartbeat_s" in out and "run_heartbeat_s" in out:
        out["run_heartbeat_s"] = min(out["run_heartbeat_s"], out["heartbeat_s"])
    if "backoff_min_s" in out and "backoff_max_s" in out:
        out["backoff_max_s"] = max(out["backoff_max_s"], out["backoff_min_s"])
    if "dead_after_s" in out:
        out["dead_after_s"] = max(out["dead_after_s"], 2 * out.get("idle_ping_s", 0.0),
                                  2 * out.get("heartbeat_s", 0.0))
    return out


def host_policy(host: str) -> tuple[float, int, int] | None:

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


def host_row_named(host: str) -> bool:


    hosts = _DOC.get("hosts") or {}
    host = (host or "").strip().lower()
    return bool(host) and (host in hosts or "." + host in hosts)


def service_url(key: str, default: str) -> str:





    value = (_DOC.get("services") or {}).get(key)
    return value if isinstance(value, str) and value else default


def service_list(key: str, default):

    value = (_DOC.get("services") or {}).get(key)
    return tuple(value) if isinstance(value, list) and value else default


def service_caps(key: str, default: dict) -> dict:

    value = (_DOC.get("services") or {}).get(key)
    return dict(value) if isinstance(value, dict) and value else default


def service_doc(key: str) -> dict | None:





    value = (_DOC.get("services") or {}).get(key)
    return value if isinstance(value, dict) else None


def check_algs(key: str, shipped) -> frozenset:






    added = (_DOC.get("checks") or {}).get(key)
    if not isinstance(added, list) or not added:
        return frozenset(shipped)
    return frozenset(shipped) | frozenset(added)


def names(key: str, shipped):






    added = (_DOC.get("names") or {}).get(key)
    if not isinstance(added, list) or not added:
        return shipped
    if isinstance(shipped, tuple):
        return shipped + tuple(name for name in added if name not in shipped)
    return frozenset(shipped) | frozenset(added)


def withdrawn_hosts() -> dict:

    value = (_DOC.get("services") or {}).get("withdrawn_hosts")
    return value if isinstance(value, dict) else {}


def snapshot() -> dict[str, Any]:

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

    global _DOC
    changed = _DOC is not _EMPTY
    _DOC = _EMPTY
    _SAID.clear()
    if changed:
        _notify()
