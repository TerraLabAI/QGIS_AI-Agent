# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later















from __future__ import annotations

import os
import re
import threading
import urllib.parse

from . import net_state
from .net_hosts import OWN_HOSTS, VOLUNTEER_CEILINGS

SAMPLE_EVERY = 20
SESSION_MAX = 60
_KINDS = {
    "terra-lab.ai": "web",
    "agent.terra-lab.ai": "agent",
    "tiles.terra-lab.ai": "tiles",
    "data.terra-lab.ai": "data",
    "overpass.terra-lab.ai": "data",
    "geocode.terra-lab.ai": "geocode",
}
_GEOCODERS = frozenset({"nominatim.openstreetmap.org", "photon.komoot.io", "api-adresse.data.gouv.fr",
                        "data.geopf.fr", "www.cartociudad.es"})
_PUBLIC_SERVICES = frozenset(VOLUNTEER_CEILINGS) | _GEOCODERS
_FIELDS = ("dns_ms", "tcp_ms", "tls_ms", "ttfb_ms", "total_ms", "resp_bytes", "status_code", "method", "x_cache")
_SERVER_TOTAL = re.compile(r"(?:^|,)\s*total;[^,]*?dur=([0-9.]+)")

_LOCK = threading.Lock()
_seen: dict = {}
_sent = 0


def host_kind(host: str) -> str:
    host = (host or "").lower().rstrip(".")
    if host in _KINDS:
        return _KINDS[host]
    if host in _GEOCODERS:
        return "geocode"
    if host.startswith("stterralab") or host in OWN_HOSTS or host.endswith(".terra-lab.ai"):
        return "data"
    return "other"


def _named(host: str) -> bool:
    return host in _KINDS or host in OWN_HOSTS or host.endswith(".terra-lab.ai") \
        or host.startswith("stterralab") or host in _PUBLIC_SERVICES


def _route(path: str) -> str:

    path = re.sub(r"/\d+(?=/|$)", "/{n}", path or "/")
    path = re.sub(r"/[0-9a-f]{16,}(?=/|$)", "/{id}", path)
    return re.sub(r"/[^/]+\.(fgb|pmtiles|parquet|tif|json|geojson|png|jpg|pbf|mvt)$", r"/{file}.\1", path)[:120]


def _sample_rate(host: str) -> int:

    global _sent
    with _LOCK:
        count = _seen.get(host, 0)
        _seen[host] = count + 1
        if _sent >= SESSION_MAX:
            return 0
        rate = 1 if count == 0 else (SAMPLE_EVERY if count % SAMPLE_EVERY == 0 else 0)
        if rate:
            _sent += 1
        return rate


def record(url: str, facts: dict) -> None:

    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return
    host = (parts.hostname or "").lower()
    props = {"host_kind": host_kind(host), "reused": bool(facts.get("reused")), "journey": "run",
             "route": _route(parts.path) if _named(host) else "-"}
    if _named(host):
        props["host"] = host
    props.update({k: facts[k] for k in _FIELDS if facts.get(k) not in (None, "")})
    server = _SERVER_TOTAL.search(str(facts.get("server_timing") or ""))
    if server:
        props["server_ms"] = round(float(server.group(1)))
    if os.environ.get("AI_AGENT_PERF") == "1":
        from .logger import log

        log("perf fetch " + " ".join(f"{k}={v}" for k, v in sorted(props.items())))
    rate = _sample_rate(host)
    if not rate:
        return
    props["sample_rate"] = rate
    from . import telemetry
    from . import telemetry_events as ev

    telemetry.track(ev.PLUGIN_HTTP_TIMED, props)


def install() -> None:
    net_state.set_timing_sink(record)


def uninstall() -> None:
    net_state.set_timing_sink(None)
