# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later













from __future__ import annotations

import json
import os
import time

from .logger import log_warning
from .policy import state_dir
from .writeback import WriteBehind

NEW_CHAT = "_new"
MAX_DRAFTS = 50
MAX_CHARS = 30000
MAX_AGE_DAYS = 30

_FILE = "drafts.json"
_writer: WriteBehind | None = None

_drafts: dict[str, dict] = {}



_changed: dict[str, dict] = {}


def _path(tag: str) -> str:
    return os.path.join(state_dir(), "accounts", tag, _FILE)


def _load(tag: str) -> dict:
    drafts = _drafts.get(tag)
    if drafts is None:
        drafts = _drafts[tag] = _read(tag)
    return drafts


def _read(tag: str) -> dict:
    drafts = {}
    try:
        with open(_path(tag), encoding="utf-8") as fh:
            raw = json.load(fh).get("drafts", {})
        horizon = time.time() - MAX_AGE_DAYS * 86400
        for key, entry in raw.items():
            if (isinstance(entry, dict) and isinstance(entry.get("text"), str) and entry["text"].strip()
                    and float(entry.get("t", 0)) >= horizon):
                drafts[str(key)] = {"text": entry["text"], "t": float(entry.get("t", 0))}
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return drafts


def get(tag: str, key: str) -> str:

    entry = _load(tag).get(key)
    return entry["text"] if entry else ""


def put(tag: str, key: str, text: str) -> None:

    drafts = _load(tag)
    text = str(text or "")[:MAX_CHARS]
    if not text.strip():
        if drafts.pop(key, None) is None:
            return
        _changed.setdefault(tag, {})[key] = None
    else:
        entry = drafts.get(key)
        if entry is not None and entry["text"] == text:
            return
        drafts[key] = _changed.setdefault(tag, {})[key] = {"text": text, "t": time.time()}
    _schedule(tag)


def _schedule(tag: str) -> None:
    global _writer
    if _writer is None:
        _writer = WriteBehind(name="ai-agent-drafts")
    path = _path(tag)

    def produce() -> str | None:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except OSError as exc:
            log_warning(f"Draft folder not writable: {exc}")
            return None
        drafts = _read(tag)
        for key, entry in _changed.pop(tag, {}).items():
            if entry is None:
                drafts.pop(key, None)
            else:
                drafts[key] = entry
        while len(drafts) > MAX_DRAFTS:
            drafts.pop(min(drafts, key=lambda k: drafts[k]["t"]))
        _drafts[tag] = drafts
        return json.dumps({"v": 1, "drafts": drafts}, ensure_ascii=False)

    _writer.schedule(path, produce)


def flush() -> None:

    if _writer is not None:
        _writer.flush(wait=True)

