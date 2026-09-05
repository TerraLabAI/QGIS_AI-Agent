# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The checkpoint history of a chat: every state the project can go back to."""















from __future__ import annotations

import os
import time
import uuid

from .logger import log_warning
from .snapshot import MAX_HASH_FILE_BYTES, RunSnapshot, hold_snapshot, release_snapshot




PROMPT_CHARS = 120


def _opening(text: str) -> str:
    """The first line of what was asked, whitespace flattened, cut to length."""
    words = str(text or "").split()
    if not words:
        return ""
    opening = " ".join(words)
    if len(opening) <= PROMPT_CHARS:
        return opening
    return opening[:PROMPT_CHARS].rstrip() + "\u2026"


KIND_BEFORE = "before"
KIND_AFTER = "after"
KIND_EDITS = "edits"



NOT_BACKED_TOO_LARGE = "too_large"
NOT_BACKED_NOT_A_FILE = "not_a_file"
NOT_BACKED_COPY_FAILED = "copy_failed"
NOT_BACKED_UNKNOWN = "unknown"


def _unbacked_reason(record, layer_id: str) -> str:
    """Why ``layer_id`` was never copied, read from the snapshot's own record."""







    if str(layer_id).startswith("copy:"):
        return NOT_BACKED_COPY_FAILED
    if not isinstance(record, dict):
        return NOT_BACKED_UNKNOWN
    path = str(record.get("path") or "")
    if not path:
        return NOT_BACKED_NOT_A_FILE
    size = record.get("size")
    if size is None:

        try:
            size = os.path.getsize(path)
        except OSError:
            return NOT_BACKED_UNKNOWN
    try:
        too_large = int(size) > MAX_HASH_FILE_BYTES
    except (TypeError, ValueError):
        return NOT_BACKED_UNKNOWN
    return NOT_BACKED_TOO_LARGE if too_large else NOT_BACKED_UNKNOWN


class Checkpoint:
    __slots__ = ("id", "kind", "run_id", "run_index", "thread_id", "snapshot",
                 "changed_layers", "layers", "created_at", "prompt")

    def __init__(self, kind: str, run_id: str, run_index: int, thread_id: str,
                 snapshot: RunSnapshot, changed_layers: int = 0, layers: list | None = None,
                 prompt: str = ""):
        self.id = uuid.uuid4().hex
        self.kind = kind
        self.run_id = run_id
        self.run_index = int(run_index)
        self.thread_id = thread_id
        self.snapshot = snapshot
        self.changed_layers = int(changed_layers or 0)
        self.layers = list(layers or [])




        self.prompt = _opening(prompt)
        self.created_at = time.time()

    @property
    def available(self) -> bool:
        """The snapshot folder still holds the project (pruning drops old ones)."""
        snapshot = self.snapshot
        return bool(snapshot is not None and snapshot.captured and os.path.isfile(snapshot.project_path))

    @property
    def not_restored(self) -> list:
        """Layers this run edited whose data was never copied, with the reason."""






        snapshot = self.snapshot
        names = getattr(snapshot, "unbacked", None) if snapshot is not None else None
        if not names:
            return []
        records = getattr(snapshot, "layers", None) or {}
        items = [{"name": str(name), "reason": _unbacked_reason(records.get(lid), lid)}
                 for lid, name in names.items() if name]
        return sorted(items, key=lambda item: item["name"])

    @property
    def unbacked_layers(self) -> list:
        """The names alone, for a caller that only counts them."""
        return [item["name"] for item in self.not_restored]

    def describe(self, current: bool, start: bool = False) -> dict:







        unbacked = self.not_restored
        return {"id": self.id, "kind": self.kind, "run_id": self.run_id, "run_index": self.run_index,
                "changed_layers": self.changed_layers, "layers": list(self.layers),
                "prompt": self.prompt,
                "available": self.available, "current": bool(current),



                "created_at": float(self.created_at or 0.0), "start": bool(start),
                "data_restored": not unbacked, "not_backed_up": unbacked}


class CheckpointHistory:
    """The per-thread lists and the ``current`` marker of each."""

    def __init__(self):
        self._entries: dict[str, list[Checkpoint]] = {}
        self._current: dict[str, str] = {}
        self._run_index: dict[str, int] = {}



    def next_run_index(self, thread_id: str) -> int:
        self._run_index[thread_id] = self._run_index.get(thread_id, 0) + 1
        return self._run_index[thread_id]

    def add(self, thread_id: str, kind: str, run_id: str, run_index: int, snapshot: RunSnapshot,
            changed_layers: int = 0, layers: list | None = None, prompt: str = "",
            fork: bool = True) -> Checkpoint:
        """Append after the current entry."""










        entries = self._entries.setdefault(thread_id, [])
        current = self.current_index(thread_id)
        if fork and current is not None and current < len(entries) - 1:
            for stale in entries[current + 1:]:
                if stale.snapshot is not None:
                    try:
                        release_snapshot(stale.snapshot.dir)
                        stale.snapshot.discard()
                    except Exception as e:  # nosec B110 - a locked temp file must not block history
                        log_warning(f"Checkpoint snapshot discard failed: {e}")
            del entries[current + 1:]
        entry = Checkpoint(kind, run_id, run_index, thread_id, snapshot, changed_layers, layers, prompt)
        if not entries:



            try:
                hold_snapshot(snapshot.dir)
            except Exception as e:  # nosec B110 - a hold that fails must not block a run
                log_warning(f"Checkpoint hold failed: {e}")
        if fork or current is None or current >= len(entries) - 1:
            entries.append(entry)
        else:
            entries.insert(current + 1, entry)
        self._current[thread_id] = entry.id
        return entry

    def mark_current(self, entry: Checkpoint) -> None:
        self._current[entry.thread_id] = entry.id



    def entries(self, thread_id: str) -> list[Checkpoint]:
        return list(self._entries.get(thread_id, []))

    def find(self, checkpoint_id: str) -> Checkpoint | None:
        for entries in self._entries.values():
            for entry in entries:
                if entry.id == checkpoint_id:
                    return entry
        return None

    def current(self, thread_id: str) -> Checkpoint | None:
        wanted = self._current.get(thread_id)
        return next((e for e in self._entries.get(thread_id, []) if e.id == wanted), None)

    def current_index(self, thread_id: str) -> int | None:
        wanted = self._current.get(thread_id)
        for i, entry in enumerate(self._entries.get(thread_id, [])):
            if entry.id == wanted:
                return i
        return None

    def previous(self, thread_id: str) -> Checkpoint | None:
        """The nearest available entry before the current one."""
        index = self.current_index(thread_id)
        entries = self._entries.get(thread_id, [])
        if index is None:
            index = len(entries)
        for entry in reversed(entries[:index]):
            if entry.available:
                return entry
        return None

    def next(self, thread_id: str) -> Checkpoint | None:
        """The nearest available entry after the current one."""
        index = self.current_index(thread_id)
        if index is None:
            return None
        for entry in self._entries.get(thread_id, [])[index + 1:]:
            if entry.available:
                return entry
        return None

    def first(self, thread_id: str) -> Checkpoint | None:
        """The start of the conversation: the state before its first run."""
        for entry in self._entries.get(thread_id, []):
            if entry.available:
                return entry
        return None

    def steps_between(self, thread_id: str, entry: Checkpoint) -> int:
        """How many entries the restore jumps over (negative = forward)."""
        index = self.current_index(thread_id)
        entries = self._entries.get(thread_id, [])
        target = next((i for i, e in enumerate(entries) if e.id == entry.id), None)
        if index is None or target is None:
            return 0
        return index - target

    def start_reachable(self, thread_id: str) -> bool:
        """True when the state this chat opened on can still be restored."""






        entries = self._entries.get(thread_id, [])
        return bool(entries and entries[0].available)

    def describe(self, thread_id: str) -> list[dict]:
        """The rows the panel shows, oldest first."""
        current_id = self._current.get(thread_id)
        entries = self._entries.get(thread_id, [])
        return [entry.describe(entry.id == current_id, start=(i == 0))
                for i, entry in enumerate(entries)]

    def clear(self, thread_id: str) -> None:
        for entry in self._entries.get(thread_id, []):
            if entry.snapshot is not None:
                release_snapshot(entry.snapshot.dir)
        self._entries.pop(thread_id, None)
        self._current.pop(thread_id, None)
        self._run_index.pop(thread_id, None)
