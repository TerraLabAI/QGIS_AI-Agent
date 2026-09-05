# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Putting a URL inside a QGIS provider URI without losing half of it."""














from __future__ import annotations

import re as _re
import urllib.parse
















_URI_VALUE_SAFE = "/:?"


def encode_uri_url(url: str) -> str:
    """Escape ``url`` for use as the ``url=`` value of a provider URI."""
    return urllib.parse.quote(url, safe=_URI_VALUE_SAFE)








_CRS_RE = _re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,15}:[A-Za-z0-9_.\-]{1,40}$")


def crs_problem(crs: str) -> str | None:
    """``None`` when ``crs`` is safe to put in a provider URI, else why not."""
    text = str(crs or "").strip()
    if not text:
        return "A CRS is required, for example EPSG:4326."
    if not _CRS_RE.match(text):
        return (f"{crs!r} is not a CRS name. Pass an authority and a code, "
                f"for example EPSG:4326 or IGNF:LAMB93.")
    return None









_URI_SECRET_KEYS = (
    "password", "passwd", "pwd", "token", "access_token", "refresh_token", "api_key", "apikey",
    "api-key", "secret", "client_secret", "signature", "authorization", "auth_token", "activation_key",
)  # pragma: allowlist secret - the names of the keys to redact, not a key




_URI_KV_RE = _re.compile(
    r"(?i)\b(" + "|".join(_re.escape(k) for k in _URI_SECRET_KEYS)
    + r")\s*=\s*('(?:[^'\\]|\\.)*'|\"[^\"]*\"|[^\s&;,'\"]+)"
)

_URL_QUERY_KEY_RE = _re.compile(r"(?i)([?&](?:key|sig|session|sessionid|auth|credentials?)=)[^&\s'\"]{12,}")




_URL_USERINFO_RE = _re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]+):([^@/\s]+)@")
REDACTED = "***"


def scrub_uri_secrets(text: str) -> str:
    """Drop password, token and key values from a provider URI, a URL or free text."""
    if not text:
        return text or ""
    text = _URL_USERINFO_RE.sub(r"\1:" + REDACTED + "@", text)
    text = _URL_QUERY_KEY_RE.sub(r"\1" + REDACTED, text)
    return _URI_KV_RE.sub(lambda m: f"{m.group(1)}={REDACTED}", text)


def uri_carries_secret(text: str) -> bool:
    """True when scrubbing would change the text."""
    return bool(text) and scrub_uri_secrets(text) != text
