# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later














from __future__ import annotations

import ipaddress
import re
import socket
import threading
import time
import urllib.parse

from .serialization import CodedText



_NON_HTTP_PORTS = frozenset({
    21, 22, 23, 25, 110, 135, 137, 138, 139, 143, 389, 445, 465, 587, 636, 993, 995, 1433, 1521, 3306, 3389,
    5432, 5900, 6379, 11211, 27017,
})

_user_hosts: set[str] = set()





_vouched_hosts: set[str] = set()
_vouched_thread = None
_VOUCHED_MAX = 4096

_user_text_seen = True


_LOCAL_HOSTS = ("localhost", "localhost.localdomain", "ip6-localhost")
_ALLOWED_SCHEMES = ("http", "https")
_HOST_RE = re.compile(
    r"(?i)(?:[a-z][a-z0-9+.-]*://)?((?:\d{1,3}\.){3}\d{1,3}|\[[0-9a-f:]+\]|[a-z0-9][a-z0-9.-]*\.[a-z]{2,})"
    r"|(?<=://)([a-z0-9][a-z0-9-]*)"
)


def remember_user_hosts(text: str) -> None:

    global _user_text_seen
    _user_text_seen = True
    for match in _HOST_RE.finditer(text or ""):
        _user_hosts.add((match.group(1) or match.group(2) or "").strip("[]").lower().rstrip("."))


_TEMPLATE_LABEL_RE = re.compile(r"^(?:\{[^}]*\}\.)+")


def normal_host(url: str) -> str:





    text = str(url or "").strip()
    match = re.match(r"(?i)^[a-z][a-z0-9+.-]*://([^/?#]*)", text)
    if match and "{" in match.group(1):
        text = text[:match.start(1)] + _TEMPLATE_LABEL_RE.sub("", match.group(1)) + text[match.end(1):]
    try:
        return _host_of(text)
    except ValueError:
        return ""


def _text_leaves(value, depth: int = 0):
    if isinstance(value, str):
        yield value
    elif depth < 12 and isinstance(value, dict):
        for item in value.values():
            yield from _text_leaves(item, depth + 1)
    elif depth < 12 and isinstance(value, (list, tuple)):
        for item in value:
            yield from _text_leaves(item, depth + 1)


def vouch_for_urls(values) -> None:

    for value in _text_leaves(values):
        for match in _URL_IN_TEXT_RE.finditer(value[:20_000]):
            host = normal_host(match.group(0))
            if host:
                if len(_vouched_hosts) >= _VOUCHED_MAX:
                    _vouched_hosts.clear()
                _vouched_hosts.add(host)


def host_is_vouched(host: str) -> bool:

    host = str(host or "").strip().lower().rstrip(".")
    return bool(host) and (host in _user_hosts or host in _vouched_hosts)


def vouched_hosts_for(thread_id) -> bool:


    global _vouched_thread
    other = not thread_id or thread_id != _vouched_thread
    if other:
        _vouched_hosts.clear()
    _vouched_thread = thread_id or None
    return other


_URL_IN_TEXT_RE = re.compile(r"(?i)https?://[^\s\"'<>|\\]+")





































_DNS_TTL_S = 30.0
_DNS_TIMEOUT_S = 2.0
_DNS_CACHE_MAX = 256
_dns_cache: dict[str, tuple[float, tuple[str, ...]]] = {}


_dns_lookups: dict[str, tuple[threading.Event, float]] = {}
_dns_lock = threading.Lock()


def _getaddrinfo(host: str) -> tuple[str, ...]:
    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError, ValueError):
        return ()
    return tuple(sorted({info[4][0].split("%")[0] for info in infos if info[4]}))


def _look_up(host: str, answered: threading.Event) -> None:
    addresses = _getaddrinfo(host)
    with _dns_lock:
        if len(_dns_cache) >= _DNS_CACHE_MAX:
            _dns_cache.clear()
        _dns_cache[host] = (time.monotonic() + _DNS_TTL_S, addresses)
        _dns_lookups.pop(host, None)
    answered.set()


def start_lookup(host: str) -> tuple[threading.Event, float] | None:






    now = time.monotonic()
    with _dns_lock:
        entry = _dns_cache.get(host)
        if entry is not None and entry[0] > now:
            return None
        lookup = _dns_lookups.get(host)
        if lookup is not None:
            return lookup
        lookup = (threading.Event(), now + _DNS_TIMEOUT_S)
        _dns_lookups[host] = lookup
    try:
        threading.Thread(target=_look_up, args=(host, lookup[0]), name="ai-agent-dns", daemon=True).start()
    except RuntimeError:
        with _dns_lock:
            _dns_lookups.pop(host, None)
        lookup[0].set()
    return lookup


def look_up_ahead(host: str) -> bool:

    lookup = start_lookup(host)
    return lookup is not None and not lookup[0].is_set() and lookup[1] > time.monotonic()


def resolve_host(host: str) -> tuple[str, ...]:














    lookup = start_lookup(host)
    if lookup is not None:
        lookup[0].wait(max(0.0, lookup[1] - time.monotonic()))
    with _dns_lock:
        entry = _dns_cache.get(host)
    if entry is None or entry[0] <= time.monotonic():
        return ()
    return entry[1]


def forget_resolved_hosts() -> None:

    with _dns_lock:
        _dns_cache.clear()
        _dns_lookups.clear()


_LEGACY_IPV4_PART_RE = re.compile(r"^(?:0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)$")


def _legacy_ipv4(text: str):






    parts = text.split(".")
    if not 1 <= len(parts) <= 4 or not all(_LEGACY_IPV4_PART_RE.match(p) for p in parts):
        return None
    values = [int(p, 16) if p[:2].lower() == "0x" else int(p, 8) if p.startswith("0") else int(p)
              for p in parts]
    tail_bits = 8 * (5 - len(parts))
    if any(v > 255 for v in values[:-1]) or values[-1] >= 1 << tail_bits:
        return None
    packed = 0
    for v in values[:-1]:
        packed = (packed << 8) | v
    packed = (packed << tail_bits) | values[-1]
    return ipaddress.IPv4Address(packed)


_NAT64_PREFIXES = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


_METADATA_ADDRESSES = frozenset(ipaddress.ip_address(a) for a in (
    "168.63.129.16", "100.100.100.200", "fd00:ec2::254",
))
_METADATA_NAMES = ("metadata.google.internal", "metadata.goog", "instance-data.ec2.internal")


def _host_of(url: str) -> str:






    host = (urllib.parse.urlsplit(url).hostname or "").strip("[]").lower()
    return host.split("%", 1)[0].rstrip(".")


def host_name(url) -> str:





    try:
        return _host_of(str(url or ""))
    except ValueError:
        return ""


def _as_address(text: str):







    text = (text or "").split("%", 1)[0].rstrip(".")
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return _legacy_ipv4(text)
    if address.version != 6 or address in _METADATA_ADDRESSES:
        return address
    for inner in (address.ipv4_mapped, address.sixtofour, (address.teredo or (None, None))[1]):
        if inner is not None:
            return inner
    if any(address in net for net in _NAT64_PREFIXES) or (
            address in _IPV4_COMPATIBLE and int(address) >> 24):
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return address


def _address_is_local(address) -> bool:

    return bool(address.is_loopback or address.is_link_local or address.is_unspecified
                or address.is_multicast or address.is_reserved or address in _METADATA_ADDRESSES)


def _address_is_private(address) -> bool:

    if _address_is_local(address):
        return False
    return bool(address.is_private or (address.version == 4 and address in _CGNAT))


def name_to_resolve(url: str) -> str:





    try:
        host = _host_of(url)
    except ValueError:
        return ""
    if (not host or host in _LOCAL_HOSTS or host.endswith(".localhost") or host in _METADATA_NAMES
            or _as_address(host) is not None):
        return ""
    return host


def is_local_url(url: str, resolve: bool = True) -> bool:






    try:
        host = _host_of(url)
    except ValueError:
        return True
    if not host:
        return True
    if host in _LOCAL_HOSTS or host.endswith(".localhost") or host in _METADATA_NAMES:
        return True
    literal = _as_address(host)
    if literal is not None:
        return _address_is_local(literal)
    if not resolve:
        return False
    for text in resolve_host(host):
        address = _as_address(text)
        if address is not None and _address_is_local(address):
            return True
    return False


def local_url_refusal(url: str) -> str:







    base = "URLs on this machine or on link-local addresses are not fetched."
    try:
        host = _host_of(url)
    except ValueError:
        return base
    if (not host or _as_address(host) is not None or host in _LOCAL_HOSTS or host.endswith(".localhost")
            or host in _METADATA_NAMES):
        return base
    local = []
    for text in resolve_host(host):
        address = _as_address(text)
        if address is not None and _address_is_local(address):
            local.append(text)
    if not local:
        return base
    return CodedText(f"{host} resolves to {', '.join(local)} on this computer, a local address, so it is not "
                     "fetched.", "host_resolves_local", host=host, addresses=local)


def is_private_url(url: str, resolve: bool = True) -> bool:

    try:
        host = _host_of(url)
    except ValueError:
        return False
    if not host:
        return False
    literal = _as_address(host)
    if literal is not None:
        return _address_is_private(literal)
    if "." not in host:
        return True
    if not resolve:
        return False


    for text in resolve_host(host):
        address = _as_address(text)
        if address is not None and _address_is_private(address):
            return True
    return False


def vetted_addresses(url: str):





















    try:
        host = _host_of(url)
    except ValueError:
        return None
    if not host or _as_address(host) is not None:
        return ()
    addresses = resolve_host(host)
    for text in addresses:
        address = _as_address(text)
        if address is not None and _address_is_local(address):
            return None
    return addresses







PUBLIC_SECTOR_SUFFIXES = (
    ".gov", ".mil", ".int", ".europa.eu", ".gouv.fr", ".gc.ca", ".admin.ch", ".gv.at", ".bund.de",
    ".gov.uk", ".govt.nz", ".gov.au", ".gov.ie", ".gouv.qc.ca", ".gov.br", ".gov.in", ".go.jp",
    ".go.kr", ".go.id", ".go.th", ".go.ke", ".go.tz", ".go.ug",
)
PUBLIC_SECTOR_LABELS = ("gov", "gob", "gouv", "govt")


def public_sector_host(host: str) -> bool:


    host = str(host or "").strip().lower().rstrip(".")
    if any(host.endswith(suffix) or host == suffix[1:] for suffix in PUBLIC_SECTOR_SUFFIXES):
        return True
    parts = host.split(".")
    return len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in PUBLIC_SECTOR_LABELS


def _routed_public_host(host: str) -> bool:









    from . import catalog, tuning
    from .net_hosts import OWN_HOSTS

    host = str(host or "").strip().lower().rstrip(".")
    if not host:
        return False
    if host in OWN_HOSTS:
        return True
    try:
        return tuning.host_row_named(host) or catalog.names_host(host)
    except Exception:  # noqa: BLE001
        return False


def refused_addresses(url: str, addresses) -> str | None:





    host = _host_of(url)
    for text in addresses:
        address = _as_address(text)
        if address is None:
            continue
        if _address_is_local(address) and not is_paired_backend_url(url):
            return f"{host} resolved to a local address at connection time."
        if (_address_is_private(address) and _user_text_seen and host not in _user_hosts
                and not _routed_public_host(host) and not is_paired_backend_url(url)):
            return f"{host} resolved to a private network address the user did not name."
    return None


def is_paired_backend_url(url: str) -> bool:





    try:
        from .settings import Settings

        parts = urllib.parse.urlsplit(str(url or ""))
        server = urllib.parse.urlsplit(str(Settings().server_url or ""))
    except Exception:  # noqa: BLE001
        return False
    try:
        host = (parts.hostname or "").strip("[]").lower().rstrip(".")
        theirs = (server.hostname or "").strip("[]").lower().rstrip(".")
        return bool(host) and host == theirs and parts.port == server.port
    except ValueError:
        return False


def validate_url(url: str) -> str | None:

    text = str(url or "").strip()
    try:
        scheme = urllib.parse.urlsplit(text).scheme.lower()
    except ValueError:
        return "The URL cannot be parsed."
    if scheme not in _ALLOWED_SCHEMES:
        return f"Only http and https URLs are fetched, not {scheme or 'a bare path'}://."
    try:
        port = urllib.parse.urlsplit(text).port
    except ValueError:
        return "The URL cannot be parsed."
    if port in _NON_HTTP_PORTS:
        return f"Port {port} answers a protocol other than HTTP and is not fetched."






    if is_local_url(text) and not is_paired_backend_url(text):
        return local_url_refusal(text)
    if is_private_url(text) and _user_text_seen and not is_paired_backend_url(text):
        host = _host_of(text)


        if host not in _user_hosts and not _routed_public_host(host):
            return CodedText(f"{host} is a private network address the user did not name.",
                             "private_host_unnamed", host=host)
    return None
