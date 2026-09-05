# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later














from __future__ import annotations

import contextlib
import hashlib
import http.client
import io
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections import OrderedDict

from . import data_access, limits, tuning
from .host_platform import retry_file_op
from .logger import log_warning
from .net_failure import (
    NETWORK_ERROR,
    NETWORK_SUGGESTION,
    FetchCancelled,
    FetchDeadline,
    FetchDropped,
    FetchTooLarge,
    FetchTooSlow,
    FetchTruncated,
    FetchWithdrawn,
    LocalUrlRefused,
    NetworkUnreachable,
    _arrived,
    _dropped,
    _hostname,
    _inflate,
    _is_drop,
    _name_host,
    _transient,
    _warn_if_on_main_thread,
    describe_failure,
)
from .net_hosts import (
    _GATES,
    _TICK,
    DEFAULT_POLICY,
    GATES_KEEP,
    RETRY_CODES,
    RETRY_JITTER_S,
    RETRY_MAX,
    USER_AGENT,
    FetchRateLimited,
    _budget_spent,
    _gate,
    _pause,
    _Policy,
    _policy_for,
    _refusal_delay,
    politeness_reset,
    politeness_stats,
    retry_after_seconds,
    retry_numbers,
    user_agent,
)
from .net_link import (
    LINK_REFERENCE_KBPS,
    OFFLINE_STRIKES,
    _never_reached_a_server,
    _note_reachable,
    _note_unreachable,
    forget_link_speed,
    forget_link_state,
    link_factor,
    link_is_down,
    link_kbps,
    link_report,
    note_transfer,
    seconds_to_transfer,
)
from .net_opener import (
    _CONNECT_LOCAL,
    DEFAULT_CONNECT_TIMEOUT_S,
    WITHDRAWN_HOSTS,
    _explain_url_error,
    _GuardedConnection,
    _NoLocalRedirect,
    _opener,
    _pin_table,
    _pinned_for,
    check_url,
    proxy_in_use,
    set_proxy,
    set_trust,
    withdrawn_reason,
)
from .net_state import (
    current_cancel_check,
    is_cancelled,
    set_cancel_check,
)
from .net_stream import (
    STREAM_KEEP_FREE_BYTES,
    Streamed,
    _StreamInflater,
    free_disk_bytes,
)



__all__ = [
    "CACHE_MAX_BYTES",
    "CACHE_MAX_ENTRIES",
    "CACHE_MAX_TOTAL_BYTES",
    "cache_stats",
    "check_url",
    "CHUNK",
    "clear_cache",
    "current_cancel_check",
    "is_cancelled",
    "DEFAULT_CONNECT_TIMEOUT_S",
    "DEFAULT_POLICY",
    "describe_failure",
    "fetch",
    "fetch_to_file",
    "FetchCancelled",
    "FetchDeadline",
    "FetchDropped",
    "FetchRateLimited",
    "FetchTooLarge",
    "FetchTooSlow",
    "FetchTruncated",
    "FetchWithdrawn",
    "forget_link_speed",
    "forget_link_state",
    "free_disk_bytes",
    "_GATES",
    "GATES_KEEP",
    "_GuardedConnection",
    "HEAD_START_S",
    "link_factor",
    "link_is_down",
    "link_kbps",
    "LINK_REFERENCE_KBPS",
    "link_report",
    "LocalUrlRefused",
    "NETWORK_ERROR",
    "NETWORK_SUGGESTION",
    "NetworkUnreachable",
    "_NoLocalRedirect",
    "note_transfer",
    "OFFLINE_STRIKES",
    "open_url",
    "_pinned_for",
    "_Policy",
    "_policy_for",
    "politeness_reset",
    "politeness_stats",
    "proxy_in_use",
    "race",
    "read_bounded",
    "Response",
    "retry_after_seconds",
    "RETRY_CODES",
    "RETRY_JITTER_S",
    "RETRY_MAX",
    "retry_numbers",
    "seconds_to_transfer",
    "set_cancel_check",
    "set_proxy",
    "set_trust",
    "STREAM_KEEP_FREE_BYTES",
    "Streamed",
    "TRANSPORT_RETRY_BASE_S",
    "TRANSPORT_RETRY_MAX",
    "USER_AGENT",
    "user_agent",
    "WITHDRAWN_HOSTS",
    "withdrawn_reason",
]


def open_url(request, timeout: float, connect_timeout: float | None = None):








    url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
    _pin_table().clear()
    check_url(url)
    if isinstance(request, urllib.request.Request):

        data_access.attach(request)
    opener = _opener()
    _CONNECT_LOCAL.connect_timeout = connect_timeout or None
    try:
        return opener.open(request, timeout=timeout)  # nosec B310
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), LocalUrlRefused):
            raise exc.reason from None
        raise
    finally:
        _CONNECT_LOCAL.connect_timeout = None


        _pin_table().clear()














CHUNK = 64 * 1024
CACHE_MAX_ENTRIES = 64
CACHE_MAX_BYTES = 4 * 1024 * 1024
CACHE_MAX_TOTAL_BYTES = 24 * 1024 * 1024
_SHARE_WAIT_S = 30.0


class Response:


    __slots__ = ("body", "headers", "url", "cached")

    def __init__(self, body: bytes, headers: dict, url: str, cached: bool = False):
        self.body = body
        self.headers = headers
        self.url = url
        self.cached = cached

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding, errors="replace")


def read_bounded(response, max_bytes: int, deadline: float | None = None, cancel=None,
                 budget: float | None = None) -> bytes:
















    parts: list[bytes] = []
    total = 0
    reader = getattr(response, "read1", None) or response.read
    reading_from = time.monotonic()
    try:
        while True:
            if is_cancelled(cancel):
                raise FetchCancelled("The run was stopped.")
            if deadline is not None and time.monotonic() > deadline:
                allowed = f"{budget:g}" if budget else "allotted"
                raise FetchDeadline(f"The download took longer than the {allowed}s allowed: "
                                    f"{_arrived(total, time.monotonic() - reading_from)}.")
            block = reader(min(CHUNK, max_bytes + 1 - total))
            if not block:
                break
            parts.append(block)
            total += len(block)
            if total > max_bytes:
                raise FetchTooLarge(f"The answer is larger than the {max_bytes} bytes this tool reads.")
    except (http.client.HTTPException, OSError) as exc:

        with contextlib.suppress(AttributeError, TypeError):
            exc.received = b"".join(parts)
        raise
    return b"".join(parts)


def _declared_length(headers) -> int | None:

    try:
        raw = headers.get("Content-Length") or headers.get("content-length")
    except Exception:  # noqa: BLE001
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _span(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())




_CACHE: OrderedDict[str, tuple[float, Response]] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_INFLIGHT: dict[str, threading.Event] = {}


def _cache_get(key: str) -> Response | None:
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        expires, response = entry
        if expires < time.monotonic():
            _CACHE.pop(key, None)
            return None
        _CACHE.move_to_end(key)
        return Response(response.body, dict(response.headers), response.url, cached=True)


def _cache_put(key: str, response: Response, ttl: float) -> None:
    if ttl <= 0 or len(response.body) > CACHE_MAX_BYTES:
        return
    with _CACHE_LOCK:



        _CACHE[key] = (time.monotonic() + ttl,
                       Response(response.body, dict(response.headers), response.url))
        _CACHE.move_to_end(key)
        total = sum(len(entry.body) for _, entry in _CACHE.values())


        while _CACHE and (len(_CACHE) > CACHE_MAX_ENTRIES or total > CACHE_MAX_TOTAL_BYTES):
            _, (_, dropped) = _CACHE.popitem(last=False)
            total -= len(dropped.body)


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def cache_stats() -> dict:
    with _CACHE_LOCK:
        return {"entries": len(_CACHE), "bytes": sum(len(r.body) for _, r in _CACHE.values())}




_CREDENTIAL_HEADERS = ("authorization", "cookie", "proxy-authorization", "x-api-key")


def _credential_tag(request: urllib.request.Request) -> str:












    parts = [f"{name.lower()}={value}"
             for name, value in sorted(request.header_items())
             if name.lower() in _CREDENTIAL_HEADERS and value]
    if not parts:
        return ""
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def fetch(request, *, timeout: float, max_bytes: int, total_timeout: float | None = None,
          cache_ttl: float = 0.0, cancel=None, polite: bool = True,
          connect_timeout: float | None = None) -> Response:












    _warn_if_on_main_thread()





    ceiling = limits.current("MAX_DOWNLOAD_BYTES")
    max_bytes = int(max_bytes) if max_bytes and max_bytes > 0 else ceiling
    max_bytes = min(max_bytes, ceiling)
    if not isinstance(request, urllib.request.Request):
        request = urllib.request.Request(str(request))
    url = request.full_url
    method = request.get_method()



    refusal = withdrawn_reason(urllib.parse.urlsplit(url).hostname or "")
    if refusal:
        raise FetchWithdrawn(refusal)
    if not request.has_header("User-agent"):


        request.add_header("User-agent", USER_AGENT)
    if not request.has_header("Accept-encoding"):





        request.add_header("Accept-encoding", "gzip, deflate")
    shareable = cache_ttl > 0 and method == "GET"



    key = (f"{url}|{request.get_header('Range', '')}|{request.get_header('Accept', '')}"
           f"|{_credential_tag(request)}" if shareable else "")




    deadline = time.monotonic() + total_timeout if total_timeout else None
    owns_key = False
    if shareable:



        if is_cancelled(cancel):
            raise FetchCancelled("The run was stopped.")
        hit = _cache_get(key)




        if hit is not None and len(hit.body) <= max_bytes:
            return hit
        waiter = _join_or_own(key)
        owns_key = waiter is None
        if waiter is not None:
            share_wait = _SHARE_WAIT_S if deadline is None else min(_SHARE_WAIT_S, _span(deadline))
            _wait_for_owner(waiter, share_wait, cancel)


            if is_cancelled(cancel):
                raise FetchCancelled("The run was stopped.")
            hit = _cache_get(key)
            if hit is not None and len(hit.body) <= max_bytes:
                return hit




    try:
        if is_cancelled(cancel):
            raise FetchCancelled("The run was stopped.")
        host = _hostname(url)
        held = link_is_down(host)
        if held:
            where = f"to {host}" if host else ""
            raise NetworkUnreachable(
                f"The last {OFFLINE_STRIKES} requests {where} did not reach a server, so this one was "
                f"not sent. The agent retries in {held:.0f}s.")
        try:
            if polite:
                out = _send_politely(request, url, method, timeout, max_bytes, deadline, cancel,
                                     total_timeout, connect_timeout)
            else:
                out = _send_once(request, timeout, max_bytes, deadline, cancel, total_timeout,
                                 connect_timeout)
        except urllib.error.HTTPError:
            _note_reachable(host)
            raise
        except Exception as exc:  # noqa: BLE001
            if _never_reached_a_server(exc):
                _note_unreachable(host)
            _name_host(exc, host)
            raise
        _note_reachable(host)
        partial = any(str(k).lower() == "content-range" for k in (out.headers or {}))
        if owns_key and (not partial or request.get_header("Range")):
















            _cache_put(key, out, cache_ttl)
    finally:
        if owns_key:
            _release(key)
    return out




_REFUSAL_WIRE_BYTES = 64 * 1024
_REFUSAL_TEXT_BYTES = 256 * 1024


def _refusal_readable(exc: urllib.error.HTTPError) -> urllib.error.HTTPError:









    headers = getattr(exc, "headers", None)
    try:
        encoding = str(headers.get("Content-Encoding") or "") if headers is not None else ""
    except Exception:  # noqa: BLE001
        return exc
    kind = encoding.strip().lower().split(",")[0].strip()
    if kind not in ("gzip", "x-gzip", "deflate"):
        return exc
    try:
        raw = exc.read(_REFUSAL_WIRE_BYTES) or b""
    except Exception:  # noqa: BLE001
        raw = b""
    with contextlib.suppress(Exception):
        exc.close()
    text = raw
    for wbits in ((16 + zlib.MAX_WBITS,) if "gzip" in kind else (zlib.MAX_WBITS, -zlib.MAX_WBITS)):
        try:

            text = zlib.decompressobj(wbits).decompress(raw, _REFUSAL_TEXT_BYTES)
            break
        except zlib.error:
            continue
    plain = http.client.HTTPMessage()
    for key, value in headers.items():
        if key.lower() not in ("content-encoding", "content-length"):
            plain[key] = value
    return urllib.error.HTTPError(exc.url, exc.code, exc.msg, plain, io.BytesIO(text))


def _send_once(request, timeout: float, max_bytes: int, deadline: float | None, cancel,
               budget: float | None = None, connect_timeout: float | None = None) -> Response:



    extra = {"connect_timeout": connect_timeout} if connect_timeout else {}
    plain = False
    try:
        with open_url(request, timeout=timeout, **extra) as response:


            plain = str(response.headers.get("Content-Encoding") or "identity").strip().lower() == "identity"
            promised = _declared_length(response.headers)
            if (promised is not None and promised > max_bytes
                    and request.get_method() != "HEAD"):





                raise FetchTooLarge(
                    f"The server says the answer is {promised} bytes, over the {max_bytes} bytes "
                    "this tool reads. Less of it, or a narrower area, fits.")







            if promised is not None and budget and request.get_method() != "HEAD":
                needed = seconds_to_transfer(promised)
                if needed is not None and needed > budget:
                    rate = link_kbps() or 0.0
                    raise FetchTooSlow(
                        f"The answer is {promised / (1024 * 1024):.0f} MB and this connection has been "
                        f"delivering {rate:.0f} kB/s, so it needs about {needed / 60:.0f} minutes, over "
                        f"the {budget:.0f} seconds this call has. A smaller area, a filtered "
                        "extract, or a lower resolution fits.")






            reading_from = time.perf_counter()
            body = read_bounded(response, max_bytes, deadline, cancel, budget)
























            note_transfer(len(body), time.perf_counter() - reading_from)
            headers = {k.lower(): v for k, v in response.headers.items()}








            if promised is not None and len(body) < promised and request.get_method() != "HEAD":
                cut = FetchTruncated(
                    f"The download stopped after {len(body)} of the {promised} bytes the server "
                    "said it was sending. The connection dropped.")
                cut.received = body if plain else b""
                raise cut
            inflated = _inflate(body, headers.get("content-encoding", ""), max_bytes)
            if inflated is not body:





                headers.pop("content-encoding", None)
                headers["content-length"] = str(len(inflated))
            body = inflated
            return Response(body, headers, response.geturl())
    except urllib.error.HTTPError as exc:
        exc = _refusal_readable(exc)
        if exc.code == 407:


            raise urllib.error.HTTPError(
                exc.url, exc.code,
                "The network proxy asked for credentials that were not accepted. "
                "QGIS Settings > Options > Network holds the user name and password.",
                exc.headers, None) from exc
        raise exc
    except urllib.error.URLError as exc:
        with contextlib.suppress(AttributeError, TypeError):
            exc.received = getattr(exc, "received", b"") if plain else b""
        raise _explain_url_error(exc) from exc
    except (http.client.HTTPException, OSError) as exc:


        if _is_drop(exc):
            dropped = _dropped(request.full_url, exc)
            dropped.received = getattr(exc, "received", b"") if plain else b""
            raise dropped from exc
        raise









TRANSPORT_RETRY_MAX = 2
TRANSPORT_RETRY_BASE_S = 2.0


def _send_politely(request, url: str, method: str, timeout: float, max_bytes: int,
                   deadline: float | None, cancel, budget: float | None = None,
                   connect_timeout: float | None = None, send=None) -> Response:








    host = urllib.parse.urlparse(url).hostname or ""
    gate = _gate(host)
    idempotent = method in ("GET", "HEAD")

    refusals = retry_numbers()
    retries = refusals["retry_max"] if idempotent else 0
    transport_max = int(tuning.limit("net", "retry_max", TRANSPORT_RETRY_MAX))
    transport_base = float(tuning.number("net", "retry_base_s", TRANSPORT_RETRY_BASE_S))
    attempt = dropped = 0
    backoff = 0.0
    while True:





        _pause(backoff, cancel, deadline)
        backoff = 0.0
        gate.hold(cancel, deadline)
        try:
            wait = gate.reserve()
            try:
                _pause(wait, cancel, deadline)
            except BaseException:
                gate.refund()
                raise
            try:
                out = (send or _send_once)(request, timeout, max_bytes, deadline, cancel, budget, connect_timeout)
            except urllib.error.HTTPError as exc:
                if exc.code not in RETRY_CODES:
                    raise
                delay = _refusal_delay(exc, attempt)
                gate.penalise(delay)
                if attempt >= retries or delay > refusals["max_wait_s"]:
                    raise _budget_spent(url, host, exc, attempt + 1, delay) from exc



                with contextlib.suppress(Exception):
                    exc.close()
                gate.retried()
                attempt += 1
                _pause(delay, cancel, deadline)
                continue
            except urllib.error.URLError as exc:
                if not (idempotent and dropped < transport_max and _transient(exc)):
                    raise
# nosec B311
                delay = transport_base * (2 ** dropped) + random.uniform(0.0, RETRY_JITTER_S)  # nosec B311
                if deadline is not None and time.monotonic() + delay >= deadline:
                    raise
                dropped += 1
                log_warning(f"{host}: {exc.reason}; asking again in {delay:.1f}s ({dropped}/{transport_max})")
                backoff = delay
                continue
            gate.recovered()
            return out
        finally:
            gate.free()


def fetch_to_file(request, path: str, *, timeout: float, max_bytes: int, total_timeout: float | None = None,
                  cancel=None, polite: bool = True, connect_timeout: float | None = None) -> Streamed:













    _warn_if_on_main_thread()
    ceiling = limits.current("MAX_STREAM_BYTES")
    max_bytes = int(max_bytes) if max_bytes and max_bytes > 0 else ceiling
    max_bytes = min(max_bytes, ceiling)
    if not isinstance(request, urllib.request.Request):
        request = urllib.request.Request(str(request))
    url = request.full_url
    method = request.get_method()
    refusal = withdrawn_reason(urllib.parse.urlsplit(url).hostname or "")
    if refusal:
        raise FetchWithdrawn(refusal)
    if not request.has_header("User-agent"):
        request.add_header("User-agent", USER_AGENT)
    if not request.has_header("Accept-encoding"):
        request.add_header("Accept-encoding", "gzip, deflate")
    deadline = time.monotonic() + total_timeout if total_timeout else None
    if is_cancelled(cancel):
        raise FetchCancelled("The run was stopped.")
    host = _hostname(url)
    held = link_is_down(host)
    if held:
        where = f"to {host}" if host else ""
        raise NetworkUnreachable(
            f"The last {OFFLINE_STRIKES} requests {where} did not reach a server, so this one was "
            f"not sent. The agent retries in {held:.0f}s.")

    def send(req, timeout_s, cap, until, stop, budget=None, connect_s=None):
        return _send_to_file(req, path, timeout_s, cap, until, stop, budget, connect_s)

    try:
        if polite:
            out = _send_politely(request, url, method, timeout, max_bytes, deadline, cancel,
                                 total_timeout, connect_timeout, send=send)
        else:
            out = send(request, timeout, max_bytes, deadline, cancel, total_timeout, connect_timeout)
    except urllib.error.HTTPError:
        _note_reachable(host)
        raise
    except Exception as exc:  # noqa: BLE001
        if _never_reached_a_server(exc):
            _note_unreachable(host)


        _name_host(exc, host)
        raise
    _note_reachable(host)
    return out


def _send_to_file(request, path: str, timeout: float, max_bytes: int, deadline: float | None, cancel,
                  budget: float | None = None, connect_timeout: float | None = None) -> Streamed:

    part = f"{path}.part"
    try:
        return _stream_once(request, path, part, timeout, max_bytes, deadline, cancel, budget, connect_timeout)
    except BaseException:


        with contextlib.suppress(OSError):
            os.remove(part)
        raise


def _stream_once(request, path: str, part: str, timeout: float, max_bytes: int, deadline: float | None, cancel,
                 budget: float | None, connect_timeout: float | None) -> Streamed:
    extra = {"connect_timeout": connect_timeout} if connect_timeout else {}
    try:
        with open_url(request, timeout=timeout, **extra) as response:
            promised = _declared_length(response.headers)
            headers = {k.lower(): v for k, v in response.headers.items()}
            free = free_disk_bytes(os.path.dirname(os.path.abspath(path)))
            cap = max_bytes if free is None else min(max_bytes, free - STREAM_KEEP_FREE_BYTES)
            if cap <= 0:
                raise FetchTooLarge(
                    f"The disk has {max(free or 0, 0) / (1024 ** 3):.1f} GB free and "
                    f"{STREAM_KEEP_FREE_BYTES / (1024 ** 3):.0f} GB of it is kept free, so nothing was "
                    "written. A smaller area or more space allows it.")
            if promised is not None and promised > cap:
                raise FetchTooLarge(
                    f"The server says the answer is {promised} bytes, over the {cap} bytes this load "
                    "may write. Less of it, or a narrower area, fits.")
            if promised is not None and budget:
                needed = seconds_to_transfer(promised)
                if needed is not None and needed > budget:
                    raise FetchTooSlow(
                        f"The answer is {promised / (1024 * 1024):.0f} MB and this connection has been "
                        f"delivering {link_kbps() or 0.0:.0f} kB/s, so it needs about {needed / 60:.0f} "
                        f"minutes, over the {budget:.0f} seconds this call has. A smaller area fits.")
            inflater = _StreamInflater(headers.get("content-encoding", ""), cap)
            reader = getattr(response, "read1", None) or response.read
            wire = 0
            reading_from = time.perf_counter()
            with open(part, "wb") as handle:
                while True:
                    if is_cancelled(cancel):
                        raise FetchCancelled("The run was stopped.")
                    if deadline is not None and time.monotonic() > deadline:
                        allowed = f"{budget:g}" if budget else "allotted"
                        raise FetchDeadline(f"The download took longer than the {allowed}s allowed: "
                                            f"{_arrived(wire, time.perf_counter() - reading_from)}.")
                    block = reader(CHUNK)
                    if not block:
                        break
                    wire += len(block)
                    inflater.write(block, handle)
                inflater.close(handle)
            note_transfer(wire, time.perf_counter() - reading_from)
            if promised is not None and wire < promised:
                raise FetchTruncated(
                    f"The download stopped after {wire} of the {promised} bytes the server "
                    "said it was sending. The connection dropped.")
            if inflater.inflated:
                headers.pop("content-encoding", None)
            headers["content-length"] = str(inflater.written)
            try:


                retry_file_op(os.replace, part, path)
            except PermissionError as exc:
                raise PermissionError(
                    exc.errno, f"The download finished but could not replace {path}: another program holds "
                    f"that file open ({exc.strerror or exc}); another name avoids it.") from exc
            return Streamed(path, inflater.written, wire, headers, response.geturl())
    except urllib.error.HTTPError as exc:
        exc = _refusal_readable(exc)
        if exc.code == 407:
            raise urllib.error.HTTPError(
                exc.url, exc.code,
                "The network proxy asked for credentials that were not accepted. "
                "QGIS Settings > Options > Network holds the user name and password.",
                exc.headers, None) from exc
        raise exc
    except urllib.error.URLError as exc:
        raise _explain_url_error(exc) from exc
    except (http.client.HTTPException, OSError) as exc:
        if _is_drop(exc):
            raise _dropped(request.full_url, exc) from exc
        raise


def _wait_for_owner(event: threading.Event, seconds: float, cancel) -> None:






    end = time.monotonic() + seconds
    while True:
        left = end - time.monotonic()
        if left <= 0:
            return
        if is_cancelled(cancel):
            return
        if event.wait(min(left, _TICK)):
            return


def _join_or_own(key: str):

    with _CACHE_LOCK:
        event = _INFLIGHT.get(key)
        if event is None:
            _INFLIGHT[key] = threading.Event()
            return None
        return event


def _release(key: str) -> None:
    with _CACHE_LOCK:
        event = _INFLIGHT.pop(key, None)
    if event is not None:
        event.set()








HEAD_START_S = 2.5


class _NotNeeded(Exception):
    pass


def race(attempts, *, workers: int | None = None, stagger: float | None = None):


























    import concurrent.futures

    attempts = list(attempts)
    if not attempts:
        return None, None, []
    if len(attempts) == 1:
        label, call = attempts[0]
        try:
            return label, call(), []
        except Exception as exc:  # noqa: BLE001
            return None, None, [f"{label}: {exc}"]

    state = {"failed": 0, "won": False}
    ready = threading.Condition()





    parent_cancel = current_cancel_check()

    if stagger is None:
        stagger = tuning.threshold("net_head_start_s", HEAD_START_S, 0.5, 10.0)

    def staggered(index: int, call):
        set_cancel_check(parent_cancel)
        try:
            return _staggered(index, call)
        finally:
            set_cancel_check(None)

    def _staggered(index: int, call):






        if index and stagger > 0:
            end = time.monotonic() + stagger * index
            with ready:
                while not state["won"] and state["failed"] < index:
                    left = end - time.monotonic()
                    if left <= 0:
                        break
                    ready.wait(left)
                if state["won"]:
                    raise _NotNeeded(f"a mirror ahead of {index} answered first")
            if is_cancelled(parent_cancel):
                raise _NotNeeded("the run was stopped before this mirror was asked")
        try:
            value = call()
        except BaseException:
            with ready:
                state["failed"] += 1
                ready.notify_all()
            raise
        with ready:


            state["won"] = True
            ready.notify_all()
        return value

    errors: list[str] = []
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers or len(attempts))
    futures = {pool.submit(staggered, i, call): label for i, (label, call) in enumerate(attempts)}
    winner_label, winner = None, None
    try:
        for future in concurrent.futures.as_completed(futures):
            label = futures[future]
            try:
                value = future.result()
            except _NotNeeded:
                continue
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{label}: {exc}")
                continue
            winner_label, winner = label, value
            break
    finally:
        with ready:
            state["won"] = True
            ready.notify_all()
        for future in futures:
            future.cancel()


        pool.shutdown(wait=False)
    return winner_label, winner, errors
