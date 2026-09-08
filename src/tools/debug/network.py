# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Network activity logger for the QGIS network stack."""


































from __future__ import annotations

import time
from collections import deque

from ...core.tool_registry import Tool, ToolRegistry
from .widgets import redact_secrets







_BUF = "_aiagent_netlog2_buf"
_PENDING = "_aiagent_netlog2_pending"
_INSTALLED = "_aiagent_netlog2_installed"
_RAW_INSTALLED = "_aiagent_netlog2_raw_installed"
_RAW_KEEP = "_aiagent_netlog2_raw_keep"



_RING = 5000
_PENDING_MAX = 1000

_MAX_BODY_CHARS = 50_000
_DEFAULT_BODY_CHARS = 4000

_OPS = {1: "HEAD", 2: "GET", 3: "PUT", 4: "POST", 5: "DELETE", 6: "CUSTOM"}

_TEXTUAL = (
    "json", "text", "xml", "javascript", "html", "csv", "yaml",
    "x-www-form-urlencoded", "plain",
)


def _nam():
    from qgis.core import QgsNetworkAccessManager

    return QgsNetworkAccessManager.instance()


def _buf(nam):
    b = nam.property(_BUF)
    if not isinstance(b, deque):
        b = deque(maxlen=_RING)
        nam.setProperty(_BUF, b)
    elif b.maxlen != _RING:

        b = deque(b, maxlen=_RING)
        nam.setProperty(_BUF, b)
    return b


def _pending(nam):
    """requestId -> the live entry dict, so both reply hooks can fill it in."""




    holder = nam.property(_PENDING)
    if not isinstance(holder, deque):
        holder = deque([{}], maxlen=1)
        nam.setProperty(_PENDING, holder)
    return holder[0]


def _op_name(op) -> str:
    try:
        return _OPS.get(int(op), str(int(op)))
    except Exception:
        return "?"


def _headers(source) -> dict:
    """Raw headers of a QNetworkRequest, QNetworkReply or QgsNetworkReplyContent."""
    out = {}
    try:
        for name in source.rawHeaderList():
            key = bytes(name).decode("latin-1", "replace")
            value = bytes(source.rawHeader(name)).decode("latin-1", "replace")
            out[key] = redact_secrets(value)
    except Exception:  # nosec B110 - the debug report reads what answers and skips what does not
        pass
    return out


def _header(headers: dict, name: str) -> str:
    lower = name.lower()
    for key, value in (headers or {}).items():
        if key.lower() == lower:
            return value
    return ""


def _is_textual(content_type: str) -> bool:
    ct = (content_type or "").lower()
    return any(token in ct for token in _TEXTUAL)


def _body_fields(prefix: str, raw, content_type: str) -> dict:
    """Body keys for one side of the exchange."""




    try:
        data = bytes(raw)
    except Exception:
        return {}
    if not data:
        return {}
    out = {f"{prefix}_body_bytes": len(data)}
    if content_type and not _is_textual(content_type):
        out[f"{prefix}_body_omitted"] = f"binary content-type: {content_type}"
        return out
    text = data[: _MAX_BODY_CHARS * 4].decode("utf-8", "replace")
    if not content_type and text.count("\ufffd") > max(8, len(text) // 20):
        out[f"{prefix}_body_omitted"] = "looks binary, no content-type header"
        return out
    if len(text) > _MAX_BODY_CHARS:
        text = text[:_MAX_BODY_CHARS]
        out[f"{prefix}_body_truncated_at_capture"] = _MAX_BODY_CHARS
    body = redact_secrets(text)
    out[f"{prefix}_body"] = body
    out[f"{prefix}_body_chars"] = len(body)
    return out


def _new_entry(op: str, url: str) -> dict:
    return {
        "time": time.strftime("%H:%M:%S"),
        "op": op,
        "url": url,
        "status": None,
        "error_code": 0,
        "error": None,
        "duration_ms": None,
        "finished": False,
        "_start": time.monotonic(),
    }


def _remember(nam, rid, entry):
    pending = _pending(nam)
    pending[rid] = entry
    while len(pending) > _PENDING_MAX:
        pending.pop(next(iter(pending)), None)


def _on_about(params):
    """Request created: this is the only place the method and the request body exist."""
    try:
        nam = _nam()
        request = params.request()
        entry = _new_entry(
            _op_name(params.operation()),
            redact_secrets(request.url().toString()),
        )
        headers = _headers(request)
        if headers:
            entry["request_headers"] = headers
        entry.update(_body_fields("request", params.content(), _header(headers, "content-type")))


        _buf(nam).append(entry)
        _remember(nam, params.requestId(), entry)
    except Exception:  # nosec B110 - the debug report reads what answers and skips what does not
        pass


def _on_finished(reply):
    """Reply finished (QgsNetworkReplyContent): status, timing, response headers."""
    try:
        nam = _nam()
        rid = reply.requestId()
        entry = _pending(nam).get(rid)
        if entry is None:
            url = "?"
            try:
                url = redact_secrets(reply.request().url().toString())
            except Exception:  # nosec B110 - the debug report reads what answers and skips what does not
                pass
            entry = _new_entry("?", url)
            entry["_start"] = None
            _buf(nam).append(entry)
            _remember(nam, rid, entry)
        entry["finished"] = True
        try:
            from qgis.PyQt.QtNetwork import QNetworkRequest

            entry["status"] = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        except Exception:  # nosec B110 - the debug report reads what answers and skips what does not
            pass
        try:
            code = int(reply.error())
            entry["error_code"] = code
            entry["error"] = reply.errorString() if code else None
        except Exception:  # nosec B110 - the debug report reads what answers and skips what does not
            pass
        start = entry.get("_start")
        if start:
            entry["duration_ms"] = int((time.monotonic() - start) * 1000)
        headers = _headers(reply)
        if headers:
            entry["response_headers"] = headers
        if "response_body" not in entry:
            entry.update(_body_fields("response", reply.content(), _header(headers, "content-type")))
    except Exception:  # nosec B110 - the debug report reads what answers and skips what does not
        pass


def _on_raw_finished(reply):
    """Same reply, seen as a real QNetworkReply: the only place the body is readable."""




    try:
        nam = _nam()
        rid = reply.property("requestId")
        entry = _pending(nam).get(rid) if rid is not None else None
        if entry is None:
            return
        content_type = ""
        try:
            content_type = bytes(reply.rawHeader(b"Content-Type")).decode("latin-1", "replace")
        except Exception:  # nosec B110 - the debug report reads what answers and skips what does not
            pass
        available = int(reply.bytesAvailable())
        if available <= 0:
            return
        if content_type and not _is_textual(content_type):
            entry["response_body_bytes"] = available
            entry["response_body_omitted"] = f"binary content-type: {content_type}"
            return
        fields = _body_fields("response", reply.peek(_MAX_BODY_CHARS * 4), content_type)
        entry.update(fields)
        entry["response_body_bytes"] = available
    except Exception:  # nosec B110 - the debug report reads what answers and skips what does not
        pass


def install_network_logger() -> bool:
    """Connect the hooks once. Safe to call repeatedly and across reloads."""
    installed = False
    try:
        nam = _nam()
    except Exception:
        return False
    try:
        if not nam.property(_INSTALLED):
            from qgis.core import QgsNetworkReplyContent, QgsNetworkRequestParameters

            nam.requestAboutToBeCreated[QgsNetworkRequestParameters].connect(_on_about)
            nam.finished[QgsNetworkReplyContent].connect(_on_finished)
            nam.setProperty(_INSTALLED, True)
            installed = True
    except Exception:  # nosec B110 - the debug report reads what answers and skips what does not
        pass
    try:
        if not nam.property(_RAW_INSTALLED):
            from qgis.PyQt import sip
            from qgis.PyQt.QtNetwork import QNetworkAccessManager

            base = sip.cast(nam, QNetworkAccessManager)
            base.finished.connect(_on_raw_finished)


            nam.setProperty(_RAW_KEEP, deque([(base, _on_raw_finished)], maxlen=1))
            nam.setProperty(_RAW_INSTALLED, True)
            installed = True
    except Exception:  # nosec B110 - the debug report reads what answers and skips what does not
        pass
    return installed


def register_network_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="get_network_log",
        input_schema={
            "type": "object",
            "properties": {
                "url_contains": {
                    "type": "string",
                },
                "only_errors": {
                    "type": "boolean",
                },
                "unfinished_only": {
                    "type": "boolean",
                },
                "limit": {
                    "type": "integer",
                },
                "include_headers": {
                    "type": "boolean",
                },
                "include_bodies": {
                    "type": "boolean",
                },
                "max_body_chars": {
                    "type": "integer",
                },
            },
            "required": [],
        },
        handler=_get_network_log,
    ))
    registry.register(Tool(
        name="clear_network_log",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_clear_network_log,
    ))


def _shape(entry: dict, include_headers: bool, include_bodies: bool, max_body_chars: int) -> dict:
    out = {key: value for key, value in entry.items() if not key.startswith("_")}
    if not include_headers:
        out.pop("request_headers", None)
        out.pop("response_headers", None)
    for side in ("request", "response"):
        key = f"{side}_body"
        if not include_bodies:
            out.pop(key, None)
            continue
        body = out.get(key)
        if isinstance(body, str) and max_body_chars and len(body) > max_body_chars:
            out[key] = body[:max_body_chars]
            out[f"{side}_body_shown_chars"] = max_body_chars
            out[f"{side}_body_more"] = "raise max_body_chars (0 = all) to see the rest"
    return out


def _get_network_log(args: dict) -> dict:
    install_network_logger()
    nam = _nam()
    events = list(_buf(nam))[::-1]
    url_contains = (args.get("url_contains") or "").lower()
    only_errors = args.get("only_errors", False)
    unfinished_only = args.get("unfinished_only", False)
    limit = min(max(int(args.get("limit", 50) or 50), 1), _RING)
    include_headers = args.get("include_headers", True)
    include_bodies = args.get("include_bodies", True)
    max_body_chars = max(int(args.get("max_body_chars", _DEFAULT_BODY_CHARS) or 0), 0)

    def is_error(e):
        return bool(e.get("error")) or (isinstance(e.get("status"), int) and e["status"] >= 400)

    filtered = [
        e for e in events
        if (not url_contains or url_contains in (e.get("url") or "").lower())
        and (not only_errors or is_error(e))
        and (not unfinished_only or not e.get("finished"))
    ]
    return {
        "requests": [_shape(e, include_headers, include_bodies, max_body_chars) for e in filtered[:limit]],
        "count": len(filtered),
        "captured_total": len(events),
        "ring_size": _RING,
    }


def _clear_network_log(args: dict) -> dict:
    nam = _nam()
    _buf(nam).clear()
    _pending(nam).clear()
    return {"cleared": True}
