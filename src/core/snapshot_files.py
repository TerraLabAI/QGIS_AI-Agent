# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""File copies, SQLite backups and the snapshot pruner."""







from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import time
import urllib.parse

from qgis.core import QgsProject

from .host_platform import retry_file_op
from .logger import log, log_warning
from .snapshot_paths import (
    _SQLITE_EXTENSIONS,
    _file_hash,
    _folder_freshness,
    _stamp,
    folder_bytes,
    held_snapshots,
    inside,
)

MAX_SQLITE_BACKUP_SECONDS = 60.0


MAX_INDEX_BYTES = 32 * 1024 * 1024





ORPHAN_GRACE_SECONDS = 24 * 60 * 60
_SHAPEFILE_SIDECARS = (".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx", ".qpj")
_LAYER_ARG_KEYS = ("layer_name", "layer", "layer_id", "input", "INPUT", "target_layer",
                   "output_path", "path", "file_path")


def sqlite_read_only_uri(path: str) -> str:
    """A read-only SQLite URI for a file, whatever its path looks like."""









    absolute = os.path.abspath(path).replace("\\", "/")
    if not absolute.startswith("/"):
        absolute = "/" + absolute
    return "file://" + urllib.parse.quote(absolute, safe="/:") + "?mode=ro"





_GROUP_SIDECARS = {".shp": _SHAPEFILE_SIDECARS, ".tab": (".dat", ".map", ".id", ".ind"), ".mif": (".mid",)}


def _sidecars(path: str) -> list[str]:
    stem, ext = os.path.splitext(path)
    if ext.lower() not in _GROUP_SIDECARS:
        return [path]
    out = [path]
    for extra in _GROUP_SIDECARS[ext.lower()]:
        for candidate in (stem + extra, stem + extra.upper()):
            if os.path.isfile(candidate):
                out.append(candidate)
                break
    return out




_COPY_SUFFIX_ROOM = len("-journal") + 2


def _backup_copies(folder: str, sources: list) -> list:
    """``(source, destination)`` for one file and its sidecars, short enough to write."""













    from .security import _MAX_PATH, _long_paths_ok

    plain = [(src, os.path.join(folder, os.path.basename(src))) for src in sources]
    if _long_paths_ok() or not plain:
        return plain
    longest = max(len(dst) for _src, dst in plain) + _COPY_SUFFIX_ROOM
    if longest < _MAX_PATH:
        return plain
    stem = os.path.splitext(os.path.basename(sources[0]))[0]
    tag = hashlib.sha1(stem.encode("utf-8", "replace"),
                       usedforsecurity=False).hexdigest()[:8]
    widest = max(len(os.path.splitext(src)[1]) for src in sources)
    room = _MAX_PATH - len(folder) - 1 - widest - len(tag) - 1 - _COPY_SUFFIX_ROOM
    short = (stem[:room] + "-" + tag) if room > 0 else tag
    return [(src, os.path.join(folder, short + os.path.splitext(src)[1])) for src in sources]


def _unchanged(path: str, record: dict) -> bool:
    """The file still is what the snapshot recorded: by stamp, or by hash for a record an older build wrote."""

    stamp = record.get("stamp")
    if stamp:
        return _stamp(path) == tuple(stamp)
    digest = record.get("hash")
    return bool(digest) and _file_hash(path) == digest


def _copies_unchanged(copies) -> bool:
    """Every file of a multi-file group still has its copy's size and mtime (copy2 keeps both)."""
    try:
        for src, dst in copies:
            a, b = os.stat(src), os.stat(dst)
            if a.st_size != b.st_size or a.st_mtime_ns != b.st_mtime_ns:
                return False
    except OSError:
        return False
    return True


def _is_sqlite_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in _SQLITE_EXTENSIONS


def _backup_size(path: str) -> int:
    """Bytes an online backup must read, including a live SQLite WAL."""
    size = os.path.getsize(path)
    if _is_sqlite_file(path):
        try:
            size += os.path.getsize(path + "-wal")
        except OSError:
            pass
    return size


def _sqlite_copy(source: str, destination: str) -> None:
    """Copy one committed SQLite state, including transactions still in WAL."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            retry_file_op(os.remove, destination + suffix)
        except OSError:





            pass
    source_db = sqlite3.connect(sqlite_read_only_uri(source), timeout=5.0, uri=True)
    try:
        destination_db = sqlite3.connect(destination, timeout=5.0)
        try:
            _sqlite_backup(source_db, destination_db)
        finally:
            destination_db.close()
    finally:
        source_db.close()
    try:
        shutil.copystat(source, destination)
    except OSError:
        pass


def _sqlite_backup(source_db, destination_db) -> None:
    """Bounded, cancellable wrapper around SQLite's retrying backup loop."""
    deadline = time.monotonic() + MAX_SQLITE_BACKUP_SECONDS

    def progress(_status, _remaining, _total):
        if time.monotonic() >= deadline:
            raise TimeoutError(f"SQLite backup exceeded {MAX_SQLITE_BACKUP_SECONDS:.0f} seconds")
        try:
            from . import net
            cancelled = net.current_cancel_check()
        except (ImportError, AttributeError):
            cancelled = None
        try:
            stopped = callable(cancelled) and bool(cancelled())
        except Exception:  # noqa: BLE001 - a broken optional callback must not corrupt a backup
            stopped = False
        if stopped:
            raise InterruptedError("SQLite backup was stopped")


    source_db.backup(destination_db, pages=256, progress=progress, sleep=0.05)


def _restore_sqlite(backup: str, target: str) -> None:
    """Restore live SQLite in place, or replace a corrupt closed destination."""
    source_db = sqlite3.connect(sqlite_read_only_uri(backup), timeout=5.0, uri=True)
    try:
        target_is_sqlite = True
        if os.path.isfile(target):
            try:
                target_db = sqlite3.connect(sqlite_read_only_uri(target), timeout=5.0, uri=True)
                try:
                    target_db.execute("PRAGMA schema_version").fetchone()
                finally:
                    target_db.close()
            except sqlite3.DatabaseError as exc:
                code = getattr(exc, "sqlite_errorcode", None)
                corrupt = ((isinstance(code, int) and code & 0xFF in (11, 26))
                           or str(exc).lower() in (
                               "database disk image is malformed", "file is not a database"))
                if not corrupt:
                    raise
                target_is_sqlite = False
        if not target_is_sqlite:
            _replace_corrupt_sqlite(backup, target)
            return
        destination_db = sqlite3.connect(target, timeout=5.0)
        try:
            _sqlite_backup(source_db, destination_db)
        finally:
            destination_db.close()
    finally:
        source_db.close()


def _replace_corrupt_sqlite(backup: str, target: str) -> None:
    """Atomically replace a corrupt database only when no live sidecars exist."""
    sidecars = ("-wal", "-shm", "-journal")
    if any(os.path.exists(target + suffix) for suffix in sidecars):
        raise sqlite3.DatabaseError(
            "refusing to replace a corrupt SQLite file while transaction sidecars exist"
        )
    folder = os.path.dirname(os.path.abspath(target))
    fd, temporary = tempfile.mkstemp(prefix=".qgis-restore-", dir=folder)
    os.close(fd)
    os.remove(temporary)
    try:
        _sqlite_copy(backup, temporary)

        if any(os.path.exists(target + suffix) for suffix in sidecars):
            raise sqlite3.DatabaseError(
                "refusing to replace a corrupt SQLite file while transaction sidecars exist"
            )


        retry_file_op(os.replace, temporary, target)
    finally:
        for suffix in ("",) + sidecars:
            try:
                os.remove(temporary + suffix)
            except FileNotFoundError:
                pass


def _restore_file(backup: str, target: str) -> None:
    """Put the saved copy back, all of it or none of it."""








    if _is_sqlite_file(backup):
        _restore_sqlite(backup, target)
        return
    folder = os.path.dirname(os.path.abspath(target)) or "."
    try:
        fd, temporary = tempfile.mkstemp(prefix=".qgis-restore-", dir=folder)
        os.close(fd)
    except OSError:


        shutil.copy2(backup, target)
        return
    try:
        shutil.copy2(backup, temporary)



        retry_file_op(os.replace, temporary, target)
    except OSError:
        try:
            os.remove(temporary)
        except OSError:  # nosec B110 - the temporary is already gone
            pass
        raise


def _copy_files(folder: str, copies: list, label: str) -> bool:
    try:
        os.makedirs(folder, exist_ok=True)
        for src, dst in copies:
            if _is_sqlite_file(src):
                _sqlite_copy(src, dst)
            else:
                shutil.copy2(src, dst)
    except (OSError, sqlite3.Error) as exc:
        log_warning(f"Backup of {label} failed: {exc}")


        for _src, dst in copies:
            for suffix in ("", "-wal", "-shm", "-journal"):
                try:
                    os.remove(dst + suffix)
                except OSError:
                    pass
        return False
    return True


def resolve_layers(args: dict) -> list:
    """Project layers named or identified by the string values of a tool call."""
    project = QgsProject.instance()
    found, seen = [], set()
    values = []
    for key in _LAYER_ARG_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value:
            values.append(value)
    params = args.get("parameters")
    if isinstance(params, dict):
        for value in params.values():
            if isinstance(value, str) and value:
                values.append(value)
    for value in values:
        layer = project.mapLayer(value)
        candidates = [layer] if layer is not None else project.mapLayersByName(value)
        for candidate in candidates:
            if candidate is not None and candidate.id() not in seen:
                seen.add(candidate.id())
                found.append(candidate)
    return found


def _remove_tree(path: str, what: str) -> bool:
    """Delete a snapshot folder, and say so in the log when Windows refuses."""








    if not os.path.exists(path):
        return True
    failed = []

    def note(func, target, _exc):




        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
            return
        except OSError:
            pass
        failed.append(target)

    try:
        shutil.rmtree(path, onexc=lambda f, t, e: note(f, t, e))
    except TypeError:
        shutil.rmtree(path, onerror=note)
    except OSError as exc:
        log_warning(f"{what} not removed ({path}): {exc}")
        return False
    if failed:
        log_warning(f"{what} partly left behind: {len(failed)} entries under {path} are held open. "
                    "Closing the layers that read them, or restarting QGIS, frees it.")
        return False
    return True


def prune_snapshots(protect: str = "", base: str = "", index_dir: str = "", disk_mb: float = 0) -> None:
    """Drop the folders no chat names, then the oldest histories over the disk cap."""














    if not base:
        return
    try:
        entries = [(_folder_freshness(os.path.join(base, n)), os.path.join(base, n))
                   for n in os.listdir(base) if os.path.isdir(os.path.join(base, n))]
    except OSError:
        return
    held = {os.path.normpath(os.path.realpath(path)) for path in held_snapshots() if path}
    referenced = set(held)
    protected = os.path.normpath(os.path.realpath(protect)) if protect else ""
    if protected:
        referenced.add(protected)
    indexes: list[tuple[float, str, set]] = []
    try:
        names = os.listdir(index_dir)
    except OSError:
        names = []
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(index_dir, name)
        try:
            if os.path.islink(path) or os.path.getsize(path) > MAX_INDEX_BYTES:
                continue
            with open(path, encoding="utf-8") as fh:
                raw = fh.read(MAX_INDEX_BYTES + 1)
            if len(raw) > MAX_INDEX_BYTES:
                continue
            data = json.loads(raw)
        except (OSError, ValueError, RecursionError):
            continue
        records = data.get("entries") if isinstance(data, dict) else None
        dirs: set[str] = set()
        if isinstance(records, list):
            for record in records:
                if not isinstance(record, dict):
                    continue
                snapshot = record.get("snapshot")
                folder = snapshot.get("dir") if isinstance(snapshot, dict) else None
                if isinstance(folder, str) and folder:
                    dirs.add(os.path.normpath(os.path.realpath(folder)))
        referenced.update(dirs)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0.0
        indexes.append((mtime, path, dirs))
    now = time.time()
    for mtime, path in entries:
        if os.path.normpath(os.path.realpath(path)) in referenced:
            continue
        if now - mtime <= ORPHAN_GRACE_SECONDS:
            continue
        _remove_tree(path, "Old snapshot")
    try:
        cap = float(disk_mb) * 1024 * 1024
    except (TypeError, ValueError):
        return
    if cap <= 0:
        return
    total = float(folder_bytes(base))
    if total <= cap:
        return




    sweep: list[tuple[float, str, set | None]] = [
        (mtime, path, None) for mtime, path in entries
        if os.path.normpath(os.path.realpath(path)) not in referenced
    ]
    sweep.extend((mtime, path, dirs) for mtime, path, dirs in indexes)
    sweep.sort(key=lambda row: row[0])
    dropped = 0
    for _mtime, path, dirs in sweep:
        if total <= cap:
            break
        if dirs is None:
            if not os.path.exists(path):

                continue
            total -= folder_bytes(path)
            _remove_tree(path, "Old snapshot")
            dropped += 1
            continue
        if protected and protected in dirs:
            continue



        if dirs & held:
            continue
        freed = 0
        for folder in dirs:



            if folder in held or not inside(base, folder):
                continue
            freed += folder_bytes(folder)
            _remove_tree(folder, "Old snapshot")
        try:
            retry_file_op(os.remove, path)
        except OSError:
            pass
        total -= freed
        dropped += 1
    if dropped:
        log(f"{dropped} chats or unindexed snapshots lost their Undo history to stay under the snapshot disk cap")
