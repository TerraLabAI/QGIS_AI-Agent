# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The executor's replay table: tool_call_id -> stored answer, persisted as JSON."""





from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict

from .logger import log_warning
from .settings import account_dir
from .writeback import WriteBehind

IDEMPOTENCY_KEEP = 500


class IdempotencyTable:
    """tool_call_id -> stored answer, persisted as JSON, last 500 kept."""










    def __init__(self, path: str | None = None, keep: int = IDEMPOTENCY_KEEP, writer=None):
        self._path = path or os.path.join(account_dir(), "idempotency.json")
        self._keep = max(1, int(keep))
        self._texts: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.RLock()
        self._loaded = threading.Event()
        self._unreadable = False
        self._writer = writer or WriteBehind(name="ai-agent-idempotency")
        try:
            threading.Thread(target=self._load, name="ai-agent-idempotency-load", daemon=True).start()
        except RuntimeError:
            self._load()

    def _load(self) -> None:
        texts: OrderedDict[str, str] = OrderedDict()
        data = None
        try:
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, RecursionError) as exc:






            self._unreadable = True
            log_warning(f"The saved tool history could not be read ({exc}); it is kept as it is "
                        "and no earlier call is replayed from it this session.")
        if isinstance(data, dict) and isinstance(data.get("entries"), list):
            for entry in data["entries"][-self._keep:]:
                if isinstance(entry, dict) and entry.get("id"):
                    try:
                        texts[str(entry["id"])] = json.dumps(entry, default=str, separators=(",", ":"))
                    except (TypeError, ValueError, RecursionError):
                        continue

        with self._lock:
            for key, text in self._texts.items():
                texts.pop(key, None)
                texts[key] = text
            self._texts = texts
            while len(self._texts) > self._keep:
                self._texts.popitem(last=False)
            self._loaded.set()

    @property
    def ready(self) -> bool:
        return self._loaded.is_set()

    def _wait(self) -> None:
        if not self._loaded.is_set():
            self._loaded.wait(timeout=10.0)

    def _save(self) -> None:
        self._writer.schedule(self._path, self._text)

    def _text(self) -> str:
        with self._lock:
            return '{"entries":[' + ",".join(self._texts.values()) + "]}"

    def flush(self, wait: bool = False) -> None:
        self._writer.flush(wait=wait)

    def close(self) -> None:
        self._writer.close()

    @property
    def unreadable(self) -> bool:
        """True when the file on disk exists but could not be read."""
        self._wait()
        return self._unreadable

    def get(self, tool_call_id: str) -> dict | None:
        self._wait()
        if self._unreadable:



            with self._lock:
                text = self._texts.get(tool_call_id) if tool_call_id in self._texts else None
            return json.loads(text) if text else None
        with self._lock:
            text = self._texts.get(tool_call_id)
        if text is None:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None

    def put(self, tool_call_id: str, kind: str, payload, payload_json: str | None = None) -> None:
        """Store an answer."""

        self._wait()
        head = json.dumps({"id": tool_call_id, "kind": kind}, separators=(",", ":"))[:-1]
        if payload_json is None:
            try:
                payload_json = json.dumps(payload, default=str, separators=(",", ":"))
            except (TypeError, ValueError) as exc:
                log_warning(f"Idempotency entry not stored: {exc}")
                return
        text = f'{head},"payload":{payload_json},"ts":{time.time():.3f}}}'
        with self._lock:
            self._texts.pop(tool_call_id, None)
            self._texts[tool_call_id] = text
            while len(self._texts) > self._keep:
                self._texts.popitem(last=False)
        self._save()
