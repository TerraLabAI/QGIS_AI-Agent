# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


































from __future__ import annotations

from typing import Any

from .logger import log, log_warning

SECTIONS = ("context", "results", "follow", "compose", "net", "limits", "hosts", "services",
            "checks", "execute_code", "names", "ceilings", "thresholds")


_SAID: set = set()


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


def blank() -> dict[str, Any]:

    doc: dict[str, Any] = {"version": 0}
    doc.update({section: {} for section in SECTIONS})
    return doc


_EMPTY: dict[str, Any] = blank()


_DOC: dict[str, Any] = _EMPTY




_LISTENERS: list[Any] = []


def install(doc: dict[str, Any] | None) -> bool:


    global _DOC
    if doc is None:
        if _DOC is _EMPTY:
            return False
        _DOC = _EMPTY
        _SAID.clear()
        log("Server policy cleared, shipped defaults are back")
        _notify()
        return True
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
    return default if value is None else value


def number(section: str, name: str, default: float) -> float:

    value = _DOC.get(section, {}).get(name)
    return default if value is None else value


def flag(section: str, name: str, default: bool) -> bool:
    value = _DOC.get(section, {}).get(name)
    return default if value is None else value


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
    if not added:
        return frozenset(shipped)
    return frozenset(shipped) | frozenset(added)


def names(key: str, shipped):






    added = (_DOC.get("names") or {}).get(key)
    if not added:
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
