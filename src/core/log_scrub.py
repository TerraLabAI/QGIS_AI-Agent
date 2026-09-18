# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scrub local paths and secrets before text leaves the QGIS session."""







from __future__ import annotations

import os
import re
from typing import Any

from .provider_uri import REDACTED, scrub_uri_secrets




_SEP = r"(?:/|\\{1,2}|%5[cC]|%2[fF])"
_NAME_CHAR = r"[^/\\\s\"'\[\]:;|=,+*?<>%]"
_ACCOUNT = r"(?:" + _NAME_CHAR + r"+(?: " + _NAME_CHAR + r"+)+(?=" + _SEP + r")|[^/\\\s\"'%]+)"
_USER_PATH_RE = re.compile(r"(?i)(" + _SEP + r"(?:Users|home|Documents and Settings)" + _SEP + r")" + _ACCOUNT)

_ONEDRIVE_RE = re.compile(r"(?i)(\bOneDrive - )[^/\\\"'\r\n]+")


_UNC_RE = re.compile(
    r"(?im)(\\{1,2}[?.]\\{1,2}UNC\\{1,2}|(?:^|(?<=[\s\"'=(\[,;<>|]))(?:\\{2,4}|//)(?![?.][\\/]))"
    r"([^\\/\s\"'|<>?]+)")

_ACTIVATION_KEY_RE = re.compile(r"(?i)\btl_[0-9a-f]{32}\b")
_BEARER_RE = re.compile(r"(?i)\b(bearer|token)\s+[A-Za-z0-9\-._~+/]{16,}=*")

_BASIC_RE = re.compile(r"(?i)\b(basic)\s+[A-Za-z0-9+/]{4,}=*")
_KNOWN_TOKEN_RE = re.compile(
    r"\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"
    r"|xox[abpr]-[A-Za-z0-9-]{20,}|hf_[A-Za-z0-9]{30,}|glpat-[A-Za-z0-9_-]{20,}"
    r"|(?:AccountKey|SharedAccessKey)=[A-Za-z0-9+/]{20,}={0,2}"
    r"|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z\-_]{35}|ya29\.[0-9A-Za-z\-_]+"
    r"|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,})"
)

_PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?"
                             r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
_COLON_SECRET_RE = re.compile(
    r"(?i)(\b(?:password|passwd|api[-_]?key|activation_?key|client_?secret)\s*:\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}\"']+)")

_SECRET_KEY_RE = re.compile(
    r"(?i)^(?:.*(?:password|passwd|secret|token|api[-_]?key|access_?key|private_?key|credential|authorization"
    r"|activation_?key|client_?secret).*|environ|environment|env|pwd|pass|cookie|set-cookie)$"
)




_SAFE_KEYS = frozenset({"tokens", "input_tokens", "output_tokens", "layer_key", "primary_key", "key_field",
                        "keys", "key_column", "pk", "field_key", "join_key", "target_key", "join_field",
                        "state_token", "tracked_state_token", "request_token"})
MAX_DEPTH = 40


def _machine_words() -> tuple:
    """This computer's name and a home folder outside Users, as (pattern, replacement)."""
    words = []
    for name in ("COMPUTERNAME", "HOSTNAME"):
        value = (os.environ.get(name) or "").strip()
        if len(value) >= 4:
            words.append((re.compile(r"(?i)(?<![\w-])" + re.escape(value) + r"(?![\w-])"), REDACTED))
    home = os.path.expanduser("~")
    if len(home) > 3 and home != "~":
        for spelling in {home, home.replace("\\", "/"), home.replace("\\", "\\\\")}:
            words.append((re.compile(r"(?i)" + re.escape(spelling) + r"(?=[/\\\s\"']|$)"), "~"))
    return tuple(words)


_MACHINE_WORDS = _machine_words()


def scrub_user_paths(text: str) -> str:
    """Replace a local account name in macOS, Linux, and Windows home paths."""




    text = text or ""
    for pattern, replacement in _MACHINE_WORDS:
        text = pattern.sub(replacement, text)
    text = _USER_PATH_RE.sub(r"\1***", text)
    text = _ONEDRIVE_RE.sub(r"\1***", text)
    return _UNC_RE.sub(r"\1***", text)


def scrub_sensitive(text: str) -> str:
    """Keep the useful path prefix while removing the local username and tail."""
    cleaned = scrub_user_paths(text)
    marker = "/***"
    if marker in cleaned:
        return cleaned.split(marker, 1)[0] + marker
    marker = "\\***"
    return cleaned.split(marker, 1)[0] + marker if marker in cleaned else cleaned


def scrub_secrets(text: str) -> str:
    """Remove keys, tokens and URI passwords from free text; paths stay readable."""
    if not text:
        return text or ""
    text = _PRIVATE_KEY_RE.sub(REDACTED, text)
    text = _COLON_SECRET_RE.sub(lambda m: m.group(1) + REDACTED, text)
    text = _ACTIVATION_KEY_RE.sub(REDACTED, text)
    text = _KNOWN_TOKEN_RE.sub(REDACTED, text)
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    text = _BASIC_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    return scrub_uri_secrets(text)


def is_secret_key(key: Any) -> bool:
    name = str(key)
    return name.lower() not in _SAFE_KEYS and bool(_SECRET_KEY_RE.match(name))


def scrub_result(value: Any, _depth: int = 0) -> Any:
    """Scrub every string in a tool result, and blank the value of a secret-named key."""





    if isinstance(value, str):
        return scrub_secrets(value)
    if _depth > MAX_DEPTH:
        return REDACTED
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            name = scrub_secrets(_as_text(key))
            if is_secret_key(key) and item not in (None, "", [], {}, False):
                out[name] = REDACTED
            else:
                out[name] = scrub_result(item, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub_result(item, _depth + 1) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value


    return scrub_secrets(_as_text(value))


def _as_text(value: Any) -> str:
    """``str(value)`` that cannot fail."""







    try:
        return str(value)
    except Exception as exc:  # noqa: BLE001 - the leaf is unrepresentable, not the result
        return f"<{type(value).__name__} that cannot be rendered: {type(exc).__name__}>"


def has_secret(value: Any) -> bool:
    """True when ``scrub_result`` would change something. Cheap enough for tests and asserts."""
    return scrub_result(value) != value
