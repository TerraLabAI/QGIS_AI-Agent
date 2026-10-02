# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

import os
import re
from typing import Any

from .provider_uri import _URI_SECRET_KEYS, REDACTED, scrub_uri_secrets




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
    r"(?i)(\b(?:password|passwd|api[-_]?key|activation_?key|client_?secret)['\"]?\s*:\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}\"']+)")

_SECRET_KEY_RE = re.compile(
    r"(?i)^(?:.*(?:password|passwd|secret|token|api[-_]?key|access_?key|private_?key|credential|authorization"
    r"|activation_?key|client_?secret).*|environ|environment|env|pwd|pass|cookie|set-cookie)$"
)









_BASE64_PAYLOAD_RE = re.compile(r"[A-Za-z0-9+/]{2000,}={0,2}")
_BASE64_PAYLOAD_MARK = "\x00\x00b64\x00\x00"
_BASE64_CUT_MIN = 2000


















_SECRET_HINT_WORDS = frozenset({
    "-----begin", "password", "passwd", "pwd", "key", "secret", "tl_", "bearer", "basic", "token",
    "sk-", "ghp_", "github_pat_", "xox", "hf_", "glpat-", "akia", "aiza", "ya29.", "eyj",
    "sig", "session", "auth", "credential", "signature", "jwt",
}) | {key.lower() for key in _URI_SECRET_KEYS}


def _maybe_has_secret(text: str) -> bool:






    if "://" in text and "@" in text:
        return True
    lowered = text.lower()
    return any(word in lowered for word in _SECRET_HINT_WORDS)






_SAFE_KEYS = frozenset({"tokens", "input_tokens", "output_tokens", "layer_key", "primary_key", "key_field",
                        "keys", "key_column", "pk", "field_key", "join_key", "target_key", "join_field",
                        "state_token", "tracked_state_token", "request_token"})
MAX_DEPTH = 40


def _machine_words() -> tuple:

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





    text = text or ""
    for pattern, replacement in _MACHINE_WORDS:
        text = pattern.sub(replacement, text)
    text = _USER_PATH_RE.sub(r"\1***", text)
    text = _ONEDRIVE_RE.sub(r"\1***", text)
    return _UNC_RE.sub(r"\1***", text)


def scrub_sensitive(text: str) -> str:

    cleaned = scrub_user_paths(text)
    marker = "/***"
    if marker in cleaned:
        return cleaned.split(marker, 1)[0] + marker
    marker = "\\***"
    return cleaned.split(marker, 1)[0] + marker if marker in cleaned else cleaned







_SEG = r"(?:[^\\/\r\n\"'<>|:*?]*\\|[^\\/\r\n\t\"'<>|:*?]*/)"
_LAST = r"[^\s\\/\"'<>|,;()\[\]]*"
_URL_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]{1,15})://([^\s/\"'<>|\\]*)([^\s\"'<>|]*)")
_QUOTED_PATH_RE = re.compile(r"([\"'])((?:[A-Za-z]:[\\/]|\\\\|~[\\/]|/[^\s/\"']+/)[^\"'\r\n]*)\1")
_DRIVE_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]+" + _SEG + "*" + _LAST)
_UNC_PATH_RE = re.compile(r"(?<![\w\\])\\{2,4}[^\s\\/\"'<>|]+[\\/]+" + _SEG + "*" + _LAST)
_TILDE_RE = re.compile(r"(?<![\w~])~[\\/]+" + _SEG + "*" + _LAST)
_UNIX_RE = re.compile(r"(?<![\w.:/\\~<>-])/(?:[^\s/\"'<>|]+/)+[^\s/\"'<>|,;()\[\]]*")
_EXT_RE = re.compile(r"\.([A-Za-z0-9]{1,8})$")

DETAIL_CHARS = 2000


def _path_placeholder(path: str) -> str:


    tail = re.split(r"[\\/]+", path.rstrip("\\/. "))[-1] if path else ""
    if tail.lower().endswith(".py"):
        return ".../" + tail
    found = _EXT_RE.search(tail)
    return f"<file.{found.group(1).lower()}>" if found else "<path>"


def _url_placeholder(match) -> str:
    scheme, host, rest = match.group(1), match.group(2), match.group(3)

    trail = rest[len(rest.rstrip(".,;:!?)]")):]
    rest = rest[:len(rest) - len(trail)]
    if scheme.lower() == "file":
        return _path_placeholder(rest or host) + trail
    host = host.rsplit("@", 1)[-1]
    shown = f"{scheme}://{host}/..." if rest.strip("/") else f"{scheme}://{host}"
    return shown + trail


def strip_paths(text: str, limit: int = 0) -> str:


    if not text:
        return ""
    text = _URL_RE.sub(_url_placeholder, str(text))
    text = _QUOTED_PATH_RE.sub(lambda m: m.group(1) + _path_placeholder(m.group(2)) + m.group(1), text)
    for pattern in (_DRIVE_RE, _UNC_PATH_RE, _TILDE_RE, _UNIX_RE):
        text = pattern.sub(lambda m: _path_placeholder(m.group(0)), text)
    return text if not limit or len(text) <= limit else text[:limit] + "..."


def scrub_secrets(text: str) -> str:

    if not text:
        return text or ""
    runs: list[str] = []
    if len(text) >= _BASE64_CUT_MIN:
        def _cut(match: re.Match) -> str:
            runs.append(match.group(0))
            return _BASE64_PAYLOAD_MARK
        text = _BASE64_PAYLOAD_RE.sub(_cut, text)
    if _maybe_has_secret(text):
        text = _PRIVATE_KEY_RE.sub(REDACTED, text)
        text = _COLON_SECRET_RE.sub(lambda m: m.group(1) + REDACTED, text)
        text = _ACTIVATION_KEY_RE.sub(REDACTED, text)
        text = _KNOWN_TOKEN_RE.sub(REDACTED, text)
        text = _BEARER_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
        text = _BASIC_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
        text = scrub_uri_secrets(text)
    for run in runs:
        text = text.replace(_BASE64_PAYLOAD_MARK, run, 1)
    return text


def is_secret_key(key: Any) -> bool:
    name = str(key)
    return name.lower() not in _SAFE_KEYS and bool(_SECRET_KEY_RE.match(name))


def scrub_result(value: Any, _depth: int = 0) -> Any:






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








    try:
        return str(value)
    except Exception as exc:  # noqa: BLE001
        return f"<{type(value).__name__} that cannot be rendered: {type(exc).__name__}>"


def has_secret(value: Any) -> bool:

    return scrub_result(value) != value
