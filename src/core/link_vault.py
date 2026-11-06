# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later














from __future__ import annotations

import calendar
import hashlib
import re
import secrets
import threading
import time
from urllib.parse import parse_qsl, unquote, urlsplit


SECRET_QUERY_KEYS = frozenset({
    "x-amz-signature", "x-amz-credential", "x-amz-security-token", "sig", "x-goog-signature",
    "signature", "token", "access_token", "rlkey", "resourcekey",
})

SECRET_HOSTS = ("sharepoint.com", "1drv.ms", "onedrive.live.com")

SHARE_HOSTS = SECRET_HOSTS + ("dropbox.com", "drive.google.com", "docs.google.com",
                              "drive.usercontent.google.com")

HANDLE_RE = re.compile(r"\blink:[0-9a-f]{8}\b")


_HANDLE_VALUE_RE = re.compile(r"(?:/vsi[a-z0-9_]+/)*(link:[0-9a-f]{8})|(?:.*&)?url=(link:[0-9a-f]{8})(?:&.*)?",
                              re.DOTALL)

_URL_RE = re.compile(r"(?:https?://|(?<![\w.@/-])(?:www\.|(?:[\w-]+\.)+sharepoint\.com/|1drv\.ms/|"
                     r"onedrive\.live\.com/))[^\s<>\"'`]+", re.IGNORECASE)

_WRAPPED_RE = re.compile(r"(https?://[^\s<>\"'`]+)[ \t]*\r?\n[ \t]*(?=[?&][\w%.-]+=)", re.IGNORECASE)
_TRAILING = ".,;:!?)]}'\""

_MIN_PART = 8

_STRAY_RE = re.compile(r"(?i)((?:^|(?<=[?&;#\s])|(?<=&amp;))(?:" + "|".join(
    re.escape(k) for k in sorted(SECRET_QUERY_KEYS, key=len, reverse=True)) + r")=)([^\s&#\"'<>]{8,})")
STRAY_MASK = "[hidden]"

_TOKEN_PIECE = 16

_lock = threading.Lock()
_by_thread: dict[str, dict[str, str]] = {}
_parts: dict[str, list[str]] = {}
_run_thread: dict[str, str] = {}


def _host(parts) -> str:
    return (parts.hostname or "").lower().rstrip(".")


def _under(host: str, names) -> bool:
    return any(host == name or host.endswith("." + name) for name in names)


def _pairs(parts) -> list[tuple[str, str]]:


    text = f"{parts.query}&{parts.fragment.replace('?', '&')}".replace("&amp;", "&").replace(";", "&")
    return [(key, value) for key, _, value in (item.partition("=") for item in text.split("&") if item)]


def carries_secret(url: str) -> bool:

    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return False
    if parts.scheme.lower() not in ("http", "https") or not _host(parts):
        return False
    if _under(_host(parts), SECRET_HOSTS):
        return True

    return any(value and unquote(key).strip().lower() in SECRET_QUERY_KEYS for key, value in _pairs(parts))


def _epoch(text: str, layout: str) -> float | None:
    try:
        return float(calendar.timegm(time.strptime(text, layout)))
    except (ValueError, OverflowError):
        return None


def expires_at(url: str) -> float | None:






    try:
        query = {k.lower(): v for k, v in parse_qsl(urlsplit(str(url or "")).query, keep_blank_values=True)}
    except ValueError:
        return None
    for vendor in ("amz", "goog"):
        start, ttl = query.get(f"x-{vendor}-date", ""), query.get(f"x-{vendor}-expires", "")
        if start and ttl.isdigit():
            began = _epoch(start, "%Y%m%dT%H%M%SZ")
            if began is not None:
                return began + int(ttl)
    if "signature" in query and query.get("expires", "").isdigit():
        return float(query["expires"])
    if "sig" in query:
        ends = [_epoch(v.rstrip("Z")[:19], "%Y-%m-%dT%H:%M:%S") if "T" in v else _epoch(v[:10], "%Y-%m-%d")
                for v in (query.get("se", ""), query.get("ske", "")) if v]
        ends = [e for e in ends if e is not None]
        return min(ends) if ends else None
    return None


def _when(epoch: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(epoch))


def _hint(url: str) -> str:
    parts = urlsplit(url)
    host = _host(parts)
    name = "" if _under(host, SHARE_HOSTS) or not parts.path.strip("/") \
        else unquote(parts.path.rstrip("/").rsplit("/", 1)[-1])
    hint = f"{host}, {name}" if name else host
    ends = expires_at(url)
    if ends is None:
        return hint
    return f"{hint}, {'expired' if ends <= time.time() else 'expires'} {_when(ends)}"


def _secret_parts(url: str) -> list[str]:
    parts = urlsplit(url)
    found = {url, unquote(url)}
    for key, value in _pairs(parts):
        if unquote(key).strip().lower() in SECRET_QUERY_KEYS:
            found.update((value, unquote(value)))
    if _under(_host(parts), SECRET_HOSTS):
        found.update((parts.path, unquote(parts.path)))
    return sorted((p for p in found if len(p) >= _MIN_PART), key=len, reverse=True)


def _handle_for(thread_id: str, url: str) -> str:



    links = _by_thread.setdefault(thread_id, {})
    for handle, known in links.items():
        if known == url:
            return handle
    handle = "link:" + hashlib.sha256(f"{thread_id}\n{url}".encode()).hexdigest()[:8]
    while handle in _parts:
        handle = f"link:{secrets.token_hex(4)}"
    links[handle] = url
    _parts[handle] = _secret_parts(url)
    return handle


def _split_url(match: re.Match) -> tuple[str, str]:
    url = match.group(0)
    tail = ""
    while url and url[-1] in _TRAILING:
        tail = url[-1] + tail
        url = url[:-1]
    if "://" not in url[:8]:
        url = "https://" + url
    return url, tail


def _each_secret_url(text: str, swap) -> str:


    if not text or ("." not in text and "=" not in text):
        return text or ""
    text = _WRAPPED_RE.sub(r"\1", text)

    def one(match: re.Match) -> str:
        url, tail = _split_url(match)
        return swap(url) + tail if carries_secret(url) else match.group(0)
    text = _URL_RE.sub(one, text)
    return _STRAY_RE.sub(lambda m: m.group(1) + STRAY_MASK, text) if "=" in text else text


def hide_links(thread_id: str, text: str) -> str:

    thread_id = str(thread_id or "")

    def swap(url: str) -> str:
        with _lock:
            handle = _handle_for(thread_id, url)
        return f"{handle} ({_hint(url)})"
    return _each_secret_url(text, swap)


def redact(text: str, mark: str) -> str:


    return _each_secret_url(hide_text(text or ""), lambda url: mark)


def follow(url: str, target: str) -> None:


    if not _parts or not target or target == url:
        return
    try:
        hop = urlsplit(target)
    except ValueError:
        return
    found = {target, unquote(target)}
    found.update(v for _k, v in _pairs(hop))
    found.update(unquote(v) for _k, v in _pairs(hop))
    found.update(piece for piece in hop.path.split("/") if len(piece) >= _TOKEN_PIECE)
    with _lock:
        handle = next((h for h, parts in _parts.items() if any(part in url for part in parts)), None)
        if handle is not None:
            merged = set(_parts[handle]) | {p for p in found if len(p) >= _MIN_PART}
            _parts[handle] = sorted(merged, key=len, reverse=True)


def secret_links(text: str) -> list[tuple[int, int, str]]:


    found = []
    for match in _URL_RE.finditer(text or ""):
        url, tail = _split_url(match)
        if carries_secret(url):
            found.append((match.start(), match.end() - len(tail), url))
    return found


def file_name(url: str) -> str:

    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if _under(_host(parts), SHARE_HOSTS):
        return ""
    name = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1])
    return name if "." in name.strip(".") else ""


def remember(thread_id: str, messages) -> None:


    for msg in messages or []:
        if isinstance(msg, dict) and msg.get("role") == "user" and "://" in str(msg.get("text") or ""):
            hide_links(thread_id, str(msg["text"]))


def shown(text: str) -> str:


    if not text or "link:" not in text:
        return text
    with _lock:
        urls = {handle: url for links in _by_thread.values() for handle, url in links.items()}

    def one(match: re.Match) -> str:
        url = urls.get(match.group(0))
        if url is None:
            return match.group(0)
        return file_name(url) or _host(urlsplit(url))
    return HANDLE_RE.sub(one, text)


def note_run(run_id: str, thread_id: str) -> None:
    if run_id:
        with _lock:
            _run_thread[str(run_id)] = str(thread_id or "")


def thread_of_run(run_id: str) -> str:
    with _lock:
        return _run_thread.get(str(run_id or ""), "")


def forget(thread_id: str) -> None:

    with _lock:
        for handle in _by_thread.pop(str(thread_id or ""), {}):
            _parts.pop(handle, None)


def resolve(thread_id: str, value):



    with _lock:
        links = dict(_by_thread.get(str(thread_id or ""), {}))
    unknown: list[str] = []
    expired: dict[str, str] = {}
    embedded: list[str] = []

    def walk(item):
        if isinstance(item, str):
            if "link:" not in item:
                return item
            if HANDLE_RE.search(item) and not _HANDLE_VALUE_RE.fullmatch(item.strip()):
                embedded.extend(HANDLE_RE.findall(item))
                return item

            def swap(match: re.Match) -> str:
                url = links.get(match.group(0))
                if url is None:
                    unknown.append(match.group(0))
                    return match.group(0)
                ends = expires_at(url)
                if ends is not None and ends <= time.time():
                    expired[match.group(0)] = _when(ends)
                return url
            return HANDLE_RE.sub(swap, item)
        if isinstance(item, dict):
            return {k: walk(v) for k, v in item.items()}
        if isinstance(item, list):
            return [walk(v) for v in item]
        return item
    return walk(value), sorted(set(unknown)), expired, sorted(set(embedded))


def _known_parts() -> list[tuple[str, str]]:
    with _lock:
        if not _parts:
            return []
        pairs = [(part, handle) for handle, parts in _parts.items() for part in parts]
    return sorted(pairs, key=lambda pair: len(pair[0]), reverse=True)


def hide_text(text: str) -> str:

    if not text or not _parts:
        return text
    for part, handle in _known_parts():
        if part in text:
            text = text.replace(part, handle)
    return text


def hide(value):

    if not _parts:
        return value
    if isinstance(value, str):
        return hide_text(value)
    if isinstance(value, dict):
        return {hide(k) if isinstance(k, str) else k: hide(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [hide(v) for v in value]
    return value


def from_user_link(url: str) -> bool:


    text = str(url or "")
    return bool(text) and any(part in text for part, _ in _known_parts())
