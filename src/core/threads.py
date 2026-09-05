# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Thread storage: one JSON file per conversation under the state folder."""

























from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone

from .host_platform import retry_file_op
from .logger import log_warning
from .settings import account_dir
from .writeback import WriteBehind


MAX_LISTED = 60
MAX_KEPT_FILES = 200
MAX_THREAD_BYTES = 32 * 1024 * 1024


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex}"


def _serialise(thread: dict) -> str:
    """The file for ``thread``, always small enough for ``ThreadStore._read``."""












    text = json.dumps(thread, ensure_ascii=False, default=str)
    size = len(text.encode("utf-8"))
    if size <= MAX_THREAD_BYTES:
        return text
    messages = list(thread.get("messages") or [])
    total = len(messages)

    target = int(MAX_THREAD_BYTES * 0.9)
    while messages and size > target:
        cut = max(1, int(len(messages) * (1.0 - target / float(size))))
        messages = messages[cut:]
        text = json.dumps(dict(thread, messages=messages, dropped_messages=total - len(messages)),
                          ensure_ascii=False, default=str)
        size = len(text.encode("utf-8"))
    log_warning(f"Conversation {thread.get('id')} is larger than the {MAX_THREAD_BYTES // (1024 * 1024)} MB "
                f"a stored chat may be: its {total - len(messages)} oldest messages were not written. "
                "They are still on screen until QGIS is closed.")
    return text


class ThreadStore:
    def __init__(self, base_dir: str | None = None, writer: WriteBehind | None = None,
                 on_index_ready=None):
        self._dir = base_dir or os.path.join(account_dir(), "threads")
        try:
            os.makedirs(self._dir, exist_ok=True)
        except OSError as exc:
            log_warning(f"Thread store folder not writable: {exc}")
        self._writer = writer or WriteBehind(name="ai-agent-threads")


        self._cache: dict[str, dict] = {}

        self._index: dict[str, dict] = {}
        self._index_lock = threading.RLock()
        self._deleted: set[str] = set()
        self._closed = False
        self._index_ready = threading.Event()
        self._index_thread: threading.Thread | None = None


        self._on_index_ready = on_index_ready
        self._invoker = None
        if on_index_ready is not None:
            try:
                from .background import main_thread_invoker

                self._invoker = main_thread_invoker()
            except Exception:  # noqa: BLE001 - no Qt: the listing is synchronous anyway
                self._invoker = None
        self._start_index()



    def _path(self, thread_id: str) -> str:
        if (not isinstance(thread_id, str) or not thread_id or len(thread_id) > 160
                or not all(c.isascii() and (c.isalnum() or c in "-_") for c in thread_id)):
            raise ValueError("Invalid thread identifier")
        return os.path.join(self._dir, f"{thread_id}.json")

    def _save(self, thread: dict) -> None:
        """Queue the write. The producer reads the live dict when the timer fires."""
        thread_id = str(thread.get("id") or "")
        if not thread_id or thread_id in self._deleted or self._closed:
            return
        try:
            path = self._path(thread_id)
        except ValueError:
            return
        self._cache[thread_id] = thread
        self._index_row(thread)
        self._writer.schedule(path, lambda: _serialise(thread))

    def flush(self, wait: bool = False) -> None:
        """Write everything pending now. ``wait`` is for plugin unload."""
        self._writer.flush(wait=wait)

    def close(self) -> None:
        self._closed = True
        self._on_index_ready = None
        self._writer.close()

    def load(self, thread_id: str) -> dict | None:
        cached = self._cache.get(thread_id)
        if cached is not None:
            return cached
        if thread_id in self._deleted:
            return None
        try:
            path = self._path(thread_id)
        except ValueError:
            return None
        data = self._read(path)
        if data is None:
            return None
        if data["id"] != thread_id:
            return None
        self._cache[thread_id] = data
        return data

    @staticmethod
    def _read(path: str) -> dict | None:
        try:
            if os.path.islink(path) or os.path.getsize(path) > MAX_THREAD_BYTES:
                return None
            with open(path, encoding="utf-8") as fh:
                raw = fh.read(MAX_THREAD_BYTES + 1)
            if len(raw) > MAX_THREAD_BYTES:
                return None
            data = json.loads(raw)
        except (OSError, ValueError, RecursionError):
            return None
        if (not isinstance(data, dict) or not isinstance(data.get("id"), str)
                or not data["id"] or not isinstance(data.get("messages"), list)):
            return None
        if (data["id"] + ".json" != os.path.basename(path) or len(data["id"]) > 160
                or not all(c.isascii() and (c.isalnum() or c in "-_") for c in data["id"])):
            return None
        data["messages"] = [msg for msg in data["messages"] if isinstance(msg, dict)]
        for key in ("title", "project_path", "updated_at", "created_at"):
            if not isinstance(data.get(key, ""), str):
                data[key] = ""
        return data

    def _files_newest_first(self) -> list[tuple[float, str]]:
        entries = []
        try:
            for name in os.listdir(self._dir):
                if not name.endswith(".json"):
                    continue
                path = os.path.join(self._dir, name)
                try:
                    entries.append((os.path.getmtime(path), path))
                except OSError:
                    continue
        except OSError:
            return []
        entries.sort(reverse=True)
        return entries



    def _start_index(self) -> None:
        """Parse the files once, off the main thread; the callback brings the list to whoever asked before it was ready."""

        thread = threading.Thread(target=self._build_index, name="ai-agent-thread-index", daemon=True)
        self._index_thread = thread
        try:
            thread.start()
        except RuntimeError:
            self._index_thread = None
            self._build_index()

    def _build_index(self) -> None:
        rows: dict[str, dict] = {}
        try:
            for mtime, path in self._files_newest_first()[:MAX_KEPT_FILES]:
                if self._closed:
                    break
                data = self._read(path)
                if data is None or not data.get("id") or not data.get("messages"):
                    continue
                rows[data["id"]] = self._row_of(data, mtime)
        finally:

            with self._index_lock:
                for thread_id, row in rows.items():
                    if thread_id not in self._deleted:
                        self._index.setdefault(thread_id, row)
            self._index_ready.set()
            if self._on_index_ready is not None and self._invoker is not None and self._index_thread is not None:
                try:
                    self._invoker.invoke(lambda: self._on_index_ready() if self._on_index_ready else None)
                except Exception as exc:  # noqa: BLE001
                    log_warning(f"Thread index callback not delivered: {exc}")

    def _row_of(self, thread: dict, mtime: float) -> dict:
        return {
            "id": thread["id"],
            "title": thread.get("title") or self._fallback_title(thread),
            "project_path": thread.get("project_path") or "",
            "updated_at_iso": thread.get("updated_at") or thread.get("created_at") or "",
            "mtime": mtime,
            "_empty": not thread.get("messages"),
        }

    def _index_row(self, thread: dict) -> None:
        with self._index_lock:
            self._index[thread["id"]] = self._row_of(thread, time.time())

    @property
    def index_ready(self) -> bool:
        return self._index_ready.is_set()



    def list_threads(self, limit: int = MAX_LISTED) -> list[dict]:
        """[{id, title, project_path, updated_at_iso}], newest first."""






        with self._index_lock:
            snapshot = list(self._index.values())
        rows = sorted((row for row in snapshot if not row.get("_empty")),
                      key=lambda row: row["mtime"], reverse=True)
        return [{"id": row["id"], "title": row["title"], "project_path": row["project_path"],
                 "updated_at_iso": row["updated_at_iso"]} for row in rows[:limit]]

    @staticmethod
    def _fallback_title(thread: dict) -> str:
        for msg in thread.get("messages", []):
            if msg.get("role") == "user" and msg.get("text"):
                text = " ".join(str(msg["text"]).split())
                return text[:60] + ("..." if len(text) > 60 else "")
        return "New chat"

    def create(self, title: str = "", project_path: str = "") -> dict:
        thread = {"id": new_id("t_"), "title": title, "project_path": project_path or "",
                  "created_at": now_iso(), "updated_at": now_iso(), "messages": []}
        self._save(thread)
        self._writer.schedule_job(self._prune)
        return thread

    def messages(self, thread_id: str) -> list:
        thread = self.load(thread_id)
        return list(thread.get("messages", [])) if thread else []

    def append_message(self, thread_id: str, message: dict) -> None:
        thread = self.load(thread_id)
        if thread is None:
            thread = {"id": thread_id, "title": "", "created_at": now_iso(), "messages": []}
        message = dict(message)
        message.setdefault("ts", now_iso())
        thread["messages"].append(message)
        thread["updated_at"] = now_iso()
        self._save(thread)

    def update_agent_message(self, thread_id: str, run_id: str, patch: dict, create: bool = True) -> None:
        """Merge `patch` into the agent message of `run_id`, creating it if needed."""
        thread = self.load(thread_id)
        if thread is None:
            return
        for msg in reversed(thread["messages"]):
            if msg.get("role") == "agent" and msg.get("run_id") == run_id:
                msg.update(patch)
                break
        else:
            if not create:
                return
            msg = {"role": "agent", "run_id": run_id, "text": "", "ts": now_iso(),
                   "tool_calls": [], "plan": [], "status": "running", "summary": "",
                   "usage": {}, "verification": None}
            msg.update(patch)
            thread["messages"].append(msg)
        thread["updated_at"] = now_iso()
        self._save(thread)

    def append_tool_call(self, thread_id: str, run_id: str, call: dict) -> None:
        thread = self.load(thread_id)
        if thread is None:
            return
        for msg in reversed(thread["messages"]):
            if msg.get("role") == "agent" and msg.get("run_id") == run_id:
                calls = msg.setdefault("tool_calls", [])
                for existing in calls:
                    if existing.get("tool_call_id") == call.get("tool_call_id"):
                        existing.update(call)
                        break
                else:
                    calls.append(dict(call))
                self._save(thread)
                return
        self.update_agent_message(thread_id, run_id, {"tool_calls": [dict(call)]})

    def set_title(self, thread_id: str, title: str) -> None:
        thread = self.load(thread_id)
        if thread is None:
            return
        thread["title"] = (title or "").strip()[:120]
        thread["updated_at"] = now_iso()
        self._save(thread)

    def delete(self, thread_id: str) -> None:
        try:
            path = self._path(thread_id)
        except ValueError:
            return
        self._cache.pop(thread_id, None)
        with self._index_lock:
            self._deleted.add(thread_id)
            self._index.pop(thread_id, None)
        self._writer.delete(path)
        try:
            from . import telemetry
            from . import telemetry_events as ev
            telemetry.track(ev.THREAD_DELETED)
        except Exception:  # nosec B110 - telemetry must not block deletion
            pass

    def _prune(self) -> None:
        """Writer thread: drop the oldest files past the cap."""

        for _, path in self._files_newest_first()[MAX_KEPT_FILES:]:
            try:



                retry_file_op(os.remove, path)
            except OSError:
                continue



            self._cache.pop(os.path.splitext(os.path.basename(path))[0], None)


def user_message_record(run_id: str, text: str, chips: list, attachments: list) -> dict:
    return {"role": "user", "run_id": run_id, "text": text, "ts": now_iso(),
            "chips": [c for c in chips if isinstance(c, dict)],
            "attachments": [{"name": a.get("name", ""), "path": a.get("path", "")}
                            for a in attachments if isinstance(a, dict)]}


def elapsed_label(started: float) -> str:
    seconds = max(0.0, time.monotonic() - started)
    return f"{seconds:.1f} s" if seconds < 60 else f"{int(seconds // 60)} min {int(seconds % 60)} s"
