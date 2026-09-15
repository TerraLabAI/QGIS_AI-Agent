# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The checkpoint history of a chat: every state the project can go back to."""















from __future__ import annotations

import json
import os
import time
import uuid

from qgis.core import QgsProject

from .host_platform import retry_file_op
from .logger import log_warning
from .snapshot import (
    MAX_HASH_FILE_BYTES,
    MAX_INDEX_BYTES,
    REASON_MEMORY_LOST,
    RunSnapshot,
    checkpoints_dir,
    folder_bytes,
    hold_snapshot,
    release_snapshot,
    snapshots_dir,
)




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


def _count(value) -> int:
    """A run index or a layer count from a stored history, which is not always a number."""
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _open_project_file() -> str:
    """The file of the project open now, "" while it was never saved."""
    try:
        return QgsProject.instance().fileName() or ""
    except Exception as exc:  # noqa: BLE001 - no project reads as unsaved
        log_warning(f"Open project not read: {exc}")
        return ""


def _resolved(path: str) -> str:
    """``path`` resolved and case-folded, or only normalised when Windows cannot resolve it."""







    try:
        return os.path.normcase(os.path.realpath(path))
    except (OSError, ValueError):
        return os.path.normcase(os.path.normpath(path))


def _same_file(a: str, b: str) -> bool:
    """Whether two paths name the same project file: empty never matches."""
    if not a or not b:
        return False
    return _resolved(a) == _resolved(b)


def _in_snapshots(path: str) -> bool:
    """Whether ``path`` is inside the snapshot folders: our own copy, never the user's project."""





    try:
        root = os.path.normcase(os.path.realpath(snapshots_dir()))
        target = os.path.normcase(os.path.realpath(path))
    except OSError:
        return True
    if os.path.splitdrive(root)[0] != os.path.splitdrive(target)[0]:
        return False
    try:
        return os.path.commonpath([root, target]) == root
    except ValueError:
        return False


KIND_BEFORE = "before"
KIND_AFTER = "after"
KIND_EDITS = "edits"



NOT_BACKED_TOO_LARGE = "too_large"
NOT_BACKED_NOT_A_FILE = "not_a_file"
NOT_BACKED_COPY_FAILED = "copy_failed"
NOT_BACKED_UNKNOWN = "unknown"


NOT_BACKED_MEMORY_LOST = REASON_MEMORY_LOST


NOT_BACKED_NO_BACKUP = "no_backup"
NOT_BACKED_NEW_FILE = "new_file"
NOT_BACKED_OTHER_PROJECT = "other_project"
NOT_BACKED_OLD_PROJECT_FILE = "old_project_file"


def _clean_log(value) -> list:
    """A run log as it may be kept and read back: call records of known keys, bounded."""
    from . import limits
    from .journal import MAX_FIELDS, MAX_ITEMS, MAX_TEXT

    try:
        cap = max(1, int(limits.current("CHECKPOINT_LOG_CALLS")))
    except Exception as exc:  # noqa: BLE001 - an unread cap keeps the shipped one
        log_warning(f"Run log cap not read: {exc}")
        cap = 200
    calls = []
    for call in value[:cap] if isinstance(value, list) else []:
        if not isinstance(call, dict):
            continue
        layers = []
        for item in (call.get("layers") if isinstance(call.get("layers"), list) else [])[:MAX_ITEMS]:
            if not isinstance(item, dict):
                continue
            clean = {key: str(item.get(key) or "")[:MAX_TEXT] for key in ("id", "name", "what", "provider")}
            clean["path"] = str(item.get("path") or "")
            for key in ("before", "after"):
                if isinstance(item.get(key), int) and not isinstance(item.get(key), bool):
                    clean[key] = item[key]
            if isinstance(item.get("fields"), list):
                clean["fields"] = [str(name)[:MAX_TEXT] for name in item["fields"][:MAX_FIELDS]]
            for key in ("file_changed", "backup"):
                if key in item:
                    clean[key] = bool(item[key])
            layers.append(clean)
        files = [{"path": str(item["path"]), "existed": bool(item.get("existed")), "backup": bool(item.get("backup"))}
                 for item in (call.get("files") if isinstance(call.get("files"), list) else [])[:MAX_ITEMS]
                 if isinstance(item, dict) and isinstance(item.get("path"), str) and item["path"]]
        calls.append({"tool": str(call.get("tool") or "")[:MAX_TEXT], "ok": bool(call.get("ok", True)),
                      "at": _count(call.get("at")), "project_file": str(call.get("project_file") or ""),
                      "layers": layers, "files": files})
    return calls


def _layer_put_back(snapshot, item: dict, same_project: bool) -> tuple[bool, str]:
    """Whether restoring the state before the run brings this layer change back, and why not."""
    if not same_project:
        return False, NOT_BACKED_OTHER_PROJECT
    what, lid = item.get("what"), str(item.get("id") or "")
    if item.get("provider") == "memory":

        reason = (getattr(snapshot, "unbacked_reasons", None) or {}).get(lid) if what != "added" else ""
        return (False, reason) if reason else (True, "")
    if what in ("added", "removed"):
        return True, ""
    if item.get("path"):
        if not item.get("file_changed") or item.get("backup"):
            return True, ""
        reason = _unbacked_reason((getattr(snapshot, "layers", None) or {}).get(lid), lid) \
            if lid in (getattr(snapshot, "unbacked", None) or {}) else NOT_BACKED_NO_BACKUP
        return False, NOT_BACKED_NO_BACKUP if reason == NOT_BACKED_UNKNOWN else reason
    if what in ("features", "fields"):
        return False, NOT_BACKED_NOT_A_FILE
    return True, ""


def _log_rows(before) -> list[dict]:
    """The run log of ``before``'s run as the sheet reads it: each change, and whether Undo puts it back."""
    snapshot = before.snapshot
    captured_in = str(getattr(snapshot, "original_file", "") or "")
    files = [name for name in [*(before.project_files or []), before.project_file, captured_in] if name]

    kept = before.file_to_keep() or captured_in
    rows: list[dict] = []
    for call in before.log:
        called_in = call.get("project_file") or ""
        same = (not called_in and not captured_in) or any(_same_file(called_in, name) for name in files)
        for item in call.get("layers") or []:
            restored, reason = _layer_put_back(snapshot, item, same)
            row = {"tool": call.get("tool", ""), "what": item.get("what", ""), "layer": item.get("name", ""),
                   "restored": restored, "reason": reason}
            row.update({key: item[key] for key in ("before", "after", "fields") if key in item})
            rows.append(row)
        for item in call.get("files") or []:
            path = str(item.get("path") or "")
            if not same:
                restored, reason = False, NOT_BACKED_OTHER_PROJECT
            elif any(_same_file(path, name) for name in files) and not _same_file(path, kept):
                restored, reason = False, NOT_BACKED_OLD_PROJECT_FILE
            elif item.get("backup"):
                restored, reason = True, ""
            else:
                restored, reason = False, NOT_BACKED_NO_BACKUP if item.get("existed") else NOT_BACKED_NEW_FILE
            rows.append({"tool": call.get("tool", ""), "what": "file", "file": item.get("path", ""),
                         "restored": restored, "reason": reason})
    return rows


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
                 "changed_layers", "layers", "created_at", "prompt",
                 "project_file", "project_files", "project_generation", "log")

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

        self.project_file = str(getattr(snapshot, "original_file", "") or "")

        self.project_files = [self.project_file] if self.project_file else []
        self.project_generation = 0

        self.log: list = []

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


        reasons = getattr(snapshot, "unbacked_reasons", None) or {}
        items = [{"name": str(name), "reason": reasons.get(lid) or _unbacked_reason(records.get(lid), lid)}
                 for lid, name in names.items() if name]
        return sorted(items, key=lambda item: item["name"])

    @property
    def unbacked_layers(self) -> list:
        """The names alone, for a caller that only counts them."""
        return [item["name"] for item in self.not_restored]

    def file_to_keep(self) -> str | None:
        """The file name a restore of this entry leaves on the project."""







        open_file = _open_project_file()
        if open_file and any(_same_file(name, open_file) for name in (self.project_files or [self.project_file])):
            return open_file
        return None

    @property
    def project_name(self) -> str:
        """The project file's own name, without its folder or extension."""
        return os.path.splitext(os.path.basename(self.project_file))[0] if self.project_file else ""

    def describe(self, current: bool, start: bool = False, other_project: bool = False) -> dict:







        unbacked = self.not_restored
        return {"id": self.id, "kind": self.kind, "run_id": self.run_id, "run_index": self.run_index,
                "changed_layers": self.changed_layers, "layers": list(self.layers),
                "prompt": self.prompt,
                "available": self.available, "current": bool(current),



                "created_at": float(self.created_at or 0.0), "start": bool(start),
                "data_restored": not unbacked, "not_backed_up": unbacked,
                "project": self.project_name, "other_project": bool(other_project)}

    def to_record(self) -> dict:
        """The JSON-safe state of this entry, for the chat's file on disk."""
        return {"id": self.id, "kind": self.kind, "run_id": self.run_id,
                "run_index": self.run_index, "changed_layers": self.changed_layers,
                "layers": list(self.layers), "prompt": self.prompt,
                "created_at": self.created_at, "project_file": self.project_file,
                "project_files": list(self.project_files), "log": self.log,
                "snapshot": self.snapshot.to_record() if self.snapshot is not None else None}

    @classmethod
    def from_record(cls, record, thread_id: str) -> Checkpoint | None:
        """An entry read back from a chat's file, or None when it is malformed."""
        if not isinstance(record, dict):
            return None
        kind = record.get("kind")
        if kind not in (KIND_BEFORE, KIND_AFTER, KIND_EDITS):
            return None
        checkpoint_id = record.get("id")
        if not isinstance(checkpoint_id, str) or not checkpoint_id:
            return None
        snapshot = RunSnapshot.from_record(record.get("snapshot"))
        if snapshot is None:
            return None
        run_id = record.get("run_id")
        layers = record.get("layers")
        if not isinstance(layers, list):
            layers = []
        entry = cls(kind, run_id if isinstance(run_id, str) else "", _count(record.get("run_index")),
                    thread_id, snapshot, _count(record.get("changed_layers")), layers, "")
        entry.id = checkpoint_id
        prompt = record.get("prompt")

        entry.prompt = prompt if isinstance(prompt, str) else ""
        try:
            entry.created_at = float(record.get("created_at") or 0.0)
        except (TypeError, ValueError):
            entry.created_at = 0.0
        project_file = record.get("project_file")
        entry.project_file = project_file if isinstance(project_file, str) else ""
        files = record.get("project_files")
        entry.project_files = ([name for name in files if isinstance(name, str)]
                               if isinstance(files, list)
                               else ([entry.project_file] if entry.project_file else []))



        entry.project_generation = None
        entry.log = _clean_log(record.get("log"))
        return entry


class CheckpointHistory:
    """The per-thread lists and the ``current`` marker of each."""

    def __init__(self, folder: str = ""):
        self._entries: dict[str, list[Checkpoint]] = {}
        self._current: dict[str, str] = {}
        self._run_index: dict[str, int] = {}



        self._folder = folder

        self._loaded: set[str] = set()



        self._unread: set[str] = set()


        self._folders: dict[str, str] = {}


        self._generation = 0







        self._lineage: list[str] = []
        self._remember(_open_project_file())



    def _index_path(self, thread_id: str) -> str:
        """The chat's file in the checkpoints folder, "" for an id we may not write."""
        if (not isinstance(thread_id, str) or not thread_id or len(thread_id) > 160
                or not all(c.isascii() and (c.isalnum() or c in "-_") for c in thread_id)):
            return ""
        try:
            folder = self._folders.setdefault(thread_id, self._folder or checkpoints_dir())
        except Exception as exc:  # noqa: BLE001 - a disk that will not answer is no history
            log_warning(f"Checkpoint folder not read: {exc}")
            return ""
        return os.path.join(folder, thread_id + ".json")

    def _ensure(self, thread_id: str) -> None:
        """Read the chat's file once, before this session touches its entries."""
        if thread_id in self._loaded:
            return
        self._loaded.add(thread_id)
        path = self._index_path(thread_id)
        if not path:
            return
        try:
            if os.path.islink(path):
                log_warning(f"Checkpoint history not read ({path}): it is a symlink")
                return
            if not os.path.isfile(path):
                if os.path.lexists(path):
                    log_warning(f"Checkpoint history not read ({path}): it is not a regular file")
                return
            size = os.path.getsize(path)
            if size > MAX_INDEX_BYTES:
                log_warning(f"Checkpoint history not read ({path}): {size} bytes is past the "
                            f"{MAX_INDEX_BYTES}-byte cap, so it is left as it is")
                self._unread.add(thread_id)
                return
            with open(path, encoding="utf-8") as fh:
                raw = fh.read(MAX_INDEX_BYTES + 1)
            if len(raw) > MAX_INDEX_BYTES:
                log_warning(f"Checkpoint history not read ({path}): past the "
                            f"{MAX_INDEX_BYTES}-byte cap, so it is left as it is")
                self._unread.add(thread_id)
                return
            data = json.loads(raw)
        except OSError as exc:


            log_warning(f"Checkpoint history not read ({path}), it will be read again: {exc}")
            self._loaded.discard(thread_id)
            self._unread.add(thread_id)
            return
        except (ValueError, RecursionError) as exc:
            log_warning(f"Checkpoint history not read ({path}): {exc}")
            self._unread.add(thread_id)
            return
        records = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(records, list):
            log_warning(f"Checkpoint history not read ({path}): it holds no entries list")
            self._unread.add(thread_id)
            return
        self._unread.discard(thread_id)
        read_entries = []
        for record in records:
            entry = Checkpoint.from_record(record, thread_id)
            if entry is None or not entry.available:
                continue
            read_entries.append(entry)
        existing = self._entries.get(thread_id, [])
        if existing:
            known = {entry.id for entry in existing}
            entries = [entry for entry in read_entries if entry.id not in known] + existing
        else:
            entries = read_entries
        self._entries[thread_id] = entries
        if entries and thread_id not in self._current:
            stored = data.get("current")
            self._current[thread_id] = (stored if any(entry.id == stored for entry in entries)
                                        else entries[-1].id)
        run_index = _count(data.get("run_index"))
        for entry in entries:
            run_index = max(run_index, _count(entry.run_index))
        self._run_index[thread_id] = max(run_index, self._run_index.get(thread_id, 0))
        if entries and entries[0].snapshot is not None:
            try:
                hold_snapshot(entries[0].snapshot.dir)
            except Exception as exc:  # noqa: BLE001 - a hold that fails must not block a read
                log_warning(f"Checkpoint hold failed: {exc}")


        self._adopt(_open_project_file())

    def _save(self, thread_id: str) -> None:
        """Write the chat's file now, on the calling thread."""










        path = self._index_path(thread_id)
        if not path:
            return
        entries = self._entries.get(thread_id, [])
        if not entries:
            try:
                if os.path.exists(path):
                    retry_file_op(os.remove, path)
            except OSError as exc:
                log_warning(f"Checkpoint history not removed ({path}): {exc}")
            return
        if thread_id in self._unread:
            if thread_id not in self._loaded:

                self._ensure(thread_id)
                entries = self._entries.get(thread_id, [])
            if thread_id in self._unread and os.path.lexists(path):
                if thread_id not in self._loaded:
                    log_warning(f"Checkpoint history not written ({path}): the file there could not be read yet")
                    return
                aside = path + ".unreadable"
                try:
                    retry_file_op(os.replace, path, aside)
                except OSError as exc:
                    log_warning(f"Checkpoint history not written ({path}): the file there could not be read "
                                f"and was not set aside: {exc}")
                    return
                log_warning(f"Checkpoint history {path} could not be read: it is kept as {aside}")
            self._unread.discard(thread_id)
        record = {"version": 1, "thread_id": thread_id,
                  "current": self._current.get(thread_id, ""),
                  "run_index": self._run_index.get(thread_id, 0),
                  "entries": [entry.to_record() for entry in entries]}
        dropped: list[Checkpoint] = []
        kept = None
        try:
            text = json.dumps(record, ensure_ascii=False, default=str)
            size = len(text.encode("utf-8"))
            if size > MAX_INDEX_BYTES:
                kept = list(entries)
                protected = self._protected_ids(thread_id)
                while size > MAX_INDEX_BYTES:
                    oldest = next((i for i, entry in enumerate(kept) if entry.id not in protected), None)
                    if oldest is None:
                        break
                    dropped.append(kept.pop(oldest))
                    record["entries"] = [entry.to_record() for entry in kept]
                    text = json.dumps(record, ensure_ascii=False, default=str)
                    size = len(text.encode("utf-8"))
                if size > MAX_INDEX_BYTES:
                    log_warning(f"Checkpoint history not written ({path}): past {MAX_INDEX_BYTES} "
                                "bytes with only its protected entries left")
                    return
                if dropped:
                    log_warning(f"Checkpoint history for {thread_id} dropped {len(dropped)} oldest entries "
                                "to stay under the index size cap")
            temporary = path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as fh:
                fh.write(text)
            retry_file_op(os.replace, temporary, path)
        except Exception as exc:  # noqa: BLE001 - a history write never breaks a run
            log_warning(f"Checkpoint history not written ({path}): {exc}")
            return
        if kept is not None:
            self._entries[thread_id] = kept
        for stale in dropped:
            if stale.snapshot is None:
                continue
            try:
                release_snapshot(stale.snapshot.dir)
                stale.snapshot.discard()
            except Exception as e:  # nosec B110 - a locked temp file must not block history
                log_warning(f"Checkpoint snapshot discard failed: {e}")

    def _protected_ids(self, thread_id: str, keep_id: str = "") -> set[str]:
        """The ids a retention step may never drop."""







        entries = self._entries.get(thread_id, [])
        if not entries:
            return set()
        protected = {entries[0].id}
        if keep_id:
            protected.add(keep_id)
        current = self._current.get(thread_id)
        if current:
            protected.add(current)
        anchor = next((e for e in entries if e.id == keep_id), None)
        if anchor is None and current:
            anchor = next((e for e in entries if e.id == current), None)
        if anchor is not None and anchor.run_id:
            protected.update(e.id for e in entries if e.run_id == anchor.run_id)
        return protected

    def _trim(self, thread_id: str, keep_id: str) -> None:
        """Drop the oldest states past the chat's count and size caps."""





        from . import limits

        entries = self._entries.get(thread_id, [])
        if not entries:
            return
        try:
            cap = max(2, int(limits.current("CHECKPOINTS_PER_CHAT")))
            budget = float(limits.current("CHECKPOINT_CHAT_MB")) * 1024 * 1024
        except Exception as exc:  # noqa: BLE001 - an unread cap only means no trim
            log_warning(f"Checkpoint caps not read: {exc}")
            return
        protected = self._protected_ids(thread_id, keep_id)



        kept_ids: set[str] = {e.id for e in entries if e.id in protected}
        bytes_kept = 0.0
        for entry in entries:
            if entry.id in kept_ids:
                bytes_kept += folder_bytes(entry.snapshot.dir) if entry.snapshot is not None else 0
        for entry in reversed(entries):
            if entry.id in protected:
                continue
            size = folder_bytes(entry.snapshot.dir) if entry.snapshot is not None else 0
            if len(kept_ids) >= cap or bytes_kept + size > budget:
                continue
            kept_ids.add(entry.id)
            bytes_kept += size
        if len(kept_ids) == len(entries):
            return
        kept = [entry for entry in entries if entry.id in kept_ids]
        for entry in entries:
            if entry.id in kept_ids or entry.snapshot is None:
                continue
            try:
                release_snapshot(entry.snapshot.dir)
                entry.snapshot.discard()
            except Exception as e:  # nosec B110 - a locked temp file must not block history
                log_warning(f"Checkpoint snapshot discard failed: {e}")
        self._entries[thread_id] = kept



    def next_run_index(self, thread_id: str) -> int:
        self._ensure(thread_id)
        self._run_index[thread_id] = self._run_index.get(thread_id, 0) + 1
        return self._run_index[thread_id]

    def project_replaced(self) -> None:
        """The open project was closed: what came before no longer belongs here."""
        self._generation += 1
        self._lineage = []

    def _remember(self, path: str) -> None:
        """``path`` is a file the project in memory was read from or saved under."""




        if not path or any(_same_file(path, known) for known in self._lineage) or _in_snapshots(path):
            return
        self._lineage.append(path)

    def _known_files(self, open_file: str) -> list[str]:
        """The lineage, then the open file when it is not in it: never a snapshot-folder path."""
        known = [name for name in self._lineage if name]
        if open_file and not any(_same_file(open_file, name) for name in known) and not _in_snapshots(open_file):
            known.append(open_file)
        return known

    def _adopt(self, open_file: str) -> None:
        """Take back the entries that belong to the project file open now."""












        if not open_file or _in_snapshots(open_file):
            return
        known = self._known_files(open_file)
        changed: set[str] = set()
        for thread_id, entries in self._entries.items():
            for entry in entries:
                if entry.project_generation == self._generation:
                    continue
                files = entry.project_files or ([entry.project_file] if entry.project_file else [])
                matched = next((i for i, other in enumerate(known)
                                if any(_same_file(name, other) for name in files)), None)
                if matched is None:
                    continue
                entry.project_generation = self._generation
                missing = [name for name in known[matched + 1:] if not any(_same_file(name, have) for have in files)]
                if missing:
                    entry.project_files = [*files, *missing]


                    entry.project_file = (open_file if any(_same_file(open_file, name) for name in entry.project_files)
                                          else missing[-1])
                    changed.add(thread_id)
        for thread_id in changed:
            self._save(thread_id)

    def project_opened(self) -> None:
        """A project file was read: checkpoints of that file belong here again."""





        open_file = _open_project_file()
        if open_file and _in_snapshots(open_file):
            return
        self._lineage = []
        self._remember(open_file)
        self._adopt(open_file)

    def project_restored(self) -> None:
        """A restore finished and named the project: the lineage follows that name."""







        open_file = _open_project_file()
        if open_file and any(_same_file(open_file, name) for name in self._lineage):
            return
        self._lineage = []
        self._remember(open_file)
        self._adopt(open_file)

    def project_saved(self) -> None:
        """The open project was written to its file: File > Save or Save As."""






        path = _open_project_file()
        if not path or _in_snapshots(path):
            return





        lineage = list(self._lineage)
        self._remember(path)
        changed: set[str] = set()
        for thread_id, entries in self._entries.items():
            for entry in entries:
                if entry.project_generation != self._generation:
                    continue
                files = [name for name in (entry.project_files or [entry.project_file]) if name]
                if not (any(_same_file(name, known) for name in files for known in lineage)
                        or (not files and not lineage)):
                    continue
                if not any(_same_file(name, path) for name in files):
                    entry.project_files.append(path)
                    changed.add(thread_id)
                if entry.project_file != path:
                    entry.project_file = path
                    changed.add(thread_id)
        for thread_id in changed:
            self._save(thread_id)

    def add(self, thread_id: str, kind: str, run_id: str, run_index: int, snapshot: RunSnapshot,
            changed_layers: int = 0, layers: list | None = None, prompt: str = "",
            fork: bool = True) -> Checkpoint:
        """Append after the current entry."""










        self._ensure(thread_id)
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
        entry.project_generation = self._generation
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
        self._trim(thread_id, entry.id)
        self._save(thread_id)
        if snapshot is not None:
            snapshot.on_features_ready(lambda: self._save(thread_id))
        return entry

    def mark_current(self, entry: Checkpoint) -> None:
        self._ensure(entry.thread_id)
        self._current[entry.thread_id] = entry.id
        self._save(entry.thread_id)

    def attach_log(self, thread_id: str, run_id: str, log: list) -> None:
        """A run's log (core/journal.py), kept on its before entry and written with the chat."""
        self._ensure(thread_id)
        entry = next((e for e in reversed(self._entries.get(thread_id, []))
                      if e.run_id == run_id and e.kind == KIND_BEFORE), None)
        if entry is None or not log:
            return
        entry.log = _clean_log(log)
        self._save(thread_id)



    def entries(self, thread_id: str) -> list[Checkpoint]:
        self._ensure(thread_id)
        return list(self._entries.get(thread_id, []))

    def find(self, checkpoint_id: str) -> Checkpoint | None:
        for entries in self._entries.values():
            for entry in entries:
                if entry.id == checkpoint_id:
                    return entry
        return None

    def belongs_here(self, entry: Checkpoint) -> bool:
        """Whether ``entry`` was taken in the project open now."""








        if entry.project_generation == self._generation:
            return True
        open_file = _open_project_file()
        files = entry.project_files or [entry.project_file]
        if any(_same_file(name, other) for name in files for other in self._known_files(open_file)):
            self._adopt(open_file)
            return True
        return False

    def current(self, thread_id: str) -> Checkpoint | None:
        self._ensure(thread_id)
        wanted = self._current.get(thread_id)
        return next((e for e in self._entries.get(thread_id, []) if e.id == wanted), None)

    def current_index(self, thread_id: str) -> int | None:
        self._ensure(thread_id)
        wanted = self._current.get(thread_id)
        for i, entry in enumerate(self._entries.get(thread_id, [])):
            if entry.id == wanted:
                return i
        return None

    def previous(self, thread_id: str) -> Checkpoint | None:
        """The nearest available entry before the current one."""
        self._ensure(thread_id)
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
        self._ensure(thread_id)
        index = self.current_index(thread_id)
        if index is None:
            return None
        for entry in self._entries.get(thread_id, [])[index + 1:]:
            if entry.available:
                return entry
        return None

    def first(self, thread_id: str) -> Checkpoint | None:
        """The start of the conversation: the state before its first run."""
        self._ensure(thread_id)
        for entry in self._entries.get(thread_id, []):
            if entry.available:
                return entry
        return None

    def steps_between(self, thread_id: str, entry: Checkpoint) -> int:
        """How many entries the restore jumps over (negative = forward)."""
        self._ensure(thread_id)
        index = self.current_index(thread_id)
        entries = self._entries.get(thread_id, [])
        target = next((i for i, e in enumerate(entries) if e.id == entry.id), None)
        if index is None or target is None:
            return 0
        return index - target

    def start_reachable(self, thread_id: str) -> bool:
        """True when the state this chat opened on can still be restored."""






        self._ensure(thread_id)
        entries = self._entries.get(thread_id, [])
        return bool(entries and entries[0].available)

    def describe(self, thread_id: str) -> list[dict]:
        """The rows the panel shows, oldest first."""
        self._ensure(thread_id)
        current_id = self._current.get(thread_id)
        entries = self._entries.get(thread_id, [])


        logs = {entry.run_id: _log_rows(entry) for entry in entries
                if entry.kind == KIND_BEFORE and entry.run_id and entry.log}
        rows = []
        for i, entry in enumerate(entries):
            row = entry.describe(entry.id == current_id, start=(i == 0),
                                 other_project=not self.belongs_here(entry))
            log = logs.get(entry.run_id) if entry.kind != KIND_EDITS else None
            if log:
                row["log"] = log
            if log and entry.kind == KIND_BEFORE:
                named = {item["name"] for item in row["not_backed_up"]}
                for item in log:
                    name = item.get("layer") or os.path.basename(str(item.get("file") or ""))
                    if item.get("restored") or not name or name in named:
                        continue
                    named.add(name)
                    row["not_backed_up"].append({"name": name, "reason": item.get("reason") or NOT_BACKED_UNKNOWN})
                row["not_backed_up"].sort(key=lambda item: item["name"])
                row["data_restored"] = not row["not_backed_up"]
            rows.append(row)
        return rows

    def clear(self, thread_id: str) -> None:
        self._ensure(thread_id)
        for entry in self._entries.get(thread_id, []):
            if entry.snapshot is not None:
                release_snapshot(entry.snapshot.dir)
        self._entries.pop(thread_id, None)
        self._current.pop(thread_id, None)
        self._run_index.pop(thread_id, None)
        self._save(thread_id)
        self._loaded.discard(thread_id)
