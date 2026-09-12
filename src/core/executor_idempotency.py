# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict

from . import tuning
from .logger import log_warning
from .settings import account_dir
from .writeback import WriteBehind





IDEMPOTENCY_KEEP = 500





DISK_STRING_MAX = 64 * 1024
FULL_ANSWERS_KEPT = 8


class IdempotencyTable:












    def __init__(self, path: str | None = None, keep: int | None = None, writer=None):
        self._path = path or os.path.join(account_dir(), "idempotency.json")

        self._keep_given = None if keep is None else max(1, int(keep))
        self._texts: OrderedDict[str, str] = OrderedDict()

        self._full: OrderedDict[str, object] = OrderedDict()
        self._lock = threading.RLock()
        self._loaded = threading.Event()
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






            log_warning(f"The saved tool history could not be read ({exc}); it is kept as it is "
                        "and no earlier call is replayed from it this session.")
        if isinstance(data, dict) and isinstance(data.get("entries"), list):
            for entry in data["entries"][-self._keep_now():]:
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
            while len(self._texts) > self._keep_now():
                self._texts.popitem(last=False)
            self._loaded.set()

    def _keep_now(self) -> int:
        return self._keep_given or tuning.ceiling("idempotency_keep", IDEMPOTENCY_KEEP, 100)

    @property
    def ready(self) -> bool:
        return self._loaded.is_set()

    def _wait(self) -> None:
        if not self._loaded.is_set():
            self._loaded.wait(timeout=10.0)

    def _save(self) -> None:
        self._writer.schedule(self._path, self._text)

    def _text(self) -> list[str]:




        with self._lock:
            texts = list(self._texts.values())
        pieces = ['{"entries":[']
        for index, text in enumerate(texts):
            if index:
                pieces.append(",")
            pieces.append(text)
        pieces.append("]}")
        return pieces

    def flush(self, wait: bool = False) -> None:
        self._writer.flush(wait=wait)

    def close(self) -> None:
        self._writer.close()

    def get(self, tool_call_id: str) -> dict | None:
        self._wait()


        with self._lock:
            text = self._texts.get(tool_call_id)
            full = self._full.get(tool_call_id, _NONE)
        if text is None:
            return None
        try:
            entry = json.loads(text)
        except ValueError:
            return None
        if full is not _NONE and isinstance(entry, dict):
            entry["payload"] = full
        return entry

    def put(self, tool_call_id: str, kind: str, payload, payload_json: str | None = None) -> None:


        self._wait()
        head = json.dumps({"id": tool_call_id, "kind": kind}, separators=(",", ":"))[:-1]
        shortened = False
        if payload_json is None:


            disk, shortened = _for_disk(payload)
            try:
                payload_json = json.dumps(disk, default=str, separators=(",", ":"))
            except (TypeError, ValueError) as exc:
                log_warning(f"Idempotency entry not stored: {exc}")
                return
        text = f'{head},"payload":{payload_json},"ts":{time.time():.3f}}}'
        with self._lock:
            self._texts.pop(tool_call_id, None)
            self._texts[tool_call_id] = text
            while len(self._texts) > self._keep_now():
                gone, _ = self._texts.popitem(last=False)
                self._full.pop(gone, None)
            self._full.pop(tool_call_id, None)
            if shortened:
                self._full[tool_call_id] = payload
                while len(self._full) > tuning.ceiling("idempotency_full_answers_kept", FULL_ANSWERS_KEPT, 1):
                    self._full.popitem(last=False)
        self._save()


_NONE = object()


def _for_disk(payload) -> tuple[object, bool]:



    if not isinstance(payload, dict):
        return payload, False
    cut = tuning.ceiling("idempotency_disk_string_max", DISK_STRING_MAX, 4096)
    long_keys = [key for key, value in payload.items() if isinstance(value, str) and len(value) > cut]
    if not long_keys:
        return payload, False
    disk = dict(payload)
    for key in long_keys:
        disk[key] = (f"[{len(payload[key]):,} characters sent with the call and not kept after it; "
                     "call the tool again to see them]")
    return disk, True
