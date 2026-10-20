# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



































from __future__ import annotations

import hashlib
import os
import sqlite3
import time

from .logger import log_warning


BUDGET_S = 20.0


INLINE_ROWS_BYTES = 16 * 1024 * 1024
INLINE_ROWS_S = 1.0
_SQLITE_EXTENSIONS = (".gpkg", ".sqlite", ".db", ".spatialite", ".gpkx")

_DERIVED_PREFIXES = ("sqlite_", "rtree_")


_DERIVED_TABLES = frozenset({"gpkg_ogr_contents"})
_DERIVED_COLUMNS = frozenset({("gpkg_contents", "last_change")})


def _fold(path: str) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def expected_copies(history, thread_id: str, current) -> dict[str, str]:




    if current is None or getattr(current, "snapshot", None) is None:
        return {}
    pairs = current.snapshot.data_groups(history.file_versions(thread_id, current))
    return {_fold(src): dst for group in pairs for src, dst in group}


def files_to_check(history, thread_id: str, entry, current) -> list[tuple[str, str]]:

    if entry is None or getattr(entry, "snapshot", None) is None:
        return []
    expected = expected_copies(history, thread_id, current)
    if not expected:
        return []
    out, seen = [], set()
    for group in entry.snapshot.data_groups(history.file_versions(thread_id, entry)):
        for src, _dst in group:
            key = _fold(src)
            if key in expected and key not in seen:
                seen.add(key)
                out.append((src, expected[key]))
    return out


def hand_changed_files(pairs, budget_s: float = BUDGET_S) -> list[str]:

    deadline = time.monotonic() + max(0.1, budget_s)
    changed = []
    for original, copy in pairs:
        if not os.path.exists(original):
            continue
        if time.monotonic() > deadline or not _same(original, copy, deadline):
            changed.append(original)
    return changed


def _same(original: str, copy: str, deadline: float) -> bool:
    try:
        a, b = os.stat(original), os.stat(copy)
        sqlite = os.path.splitext(original)[1].lower() in _SQLITE_EXTENSIONS
        if a.st_size == b.st_size and a.st_mtime_ns == b.st_mtime_ns and not (sqlite and _pending_wal(original)):
            return True
        if sqlite:
            return _sqlite_rows(original, live=True, deadline=deadline) == \
                _sqlite_rows(copy, live=False, deadline=deadline)
        if a.st_size != b.st_size:
            return False
        return _same_bytes(original, copy, deadline)
    except (OSError, sqlite3.Error, ValueError) as exc:
        log_warning(f"Hand-edit check could not read {os.path.basename(original)}: {exc}")
        return False


def _pending_wal(path: str) -> bool:
    try:
        return os.path.getsize(path + "-wal") > 0
    except OSError:
        return False


def _uri(path: str, live: bool) -> str:







    from .snapshot_files import sqlite_read_only_uri

    uri = sqlite_read_only_uri(path)
    if live and os.path.exists(path + "-wal"):
        return uri
    return uri + "&immutable=1"


def _same_bytes(a: str, b: str, deadline: float) -> bool:
    with open(a, "rb") as left, open(b, "rb") as right:
        while True:
            if time.monotonic() > deadline:
                return False
            x, y = left.read(1 << 20), right.read(1 << 20)
            if x != y:
                return False
            if not x:
                return True


def _sqlite_rows(path: str, live: bool, deadline: float) -> str:




    con = sqlite3.connect(_uri(path, live), uri=True, timeout=2.0)
    try:
        digest = hashlib.sha256()
        tables = [(row[0], str(row[1] or "")) for row in con.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        for name, sql in tables:
            if name.startswith(_DERIVED_PREFIXES) or name in _DERIVED_TABLES \
                    or sql.lstrip().upper().startswith("CREATE VIRTUAL TABLE"):


                continue
            digest.update(name.encode("utf-8", "replace") + b"\0")
            quoted = '"' + name.replace('"', '""') + '"'
            info = [(row[1], str(row[2] or "")) for row in con.execute(f"PRAGMA table_info({quoted})")]  # nosec B608
            columns = [column for column, _type in info]
            kept = [c for c in columns if (name, c) not in _DERIVED_COLUMNS] or columns

            digest.update(repr([pair for pair in info if pair[0] in kept]).encode("utf-8", "replace"))
            listed = ", ".join('"' + c.replace('"', '""') + '"' for c in kept)
            try:
                rows = con.execute(f"SELECT {listed} FROM {quoted} ORDER BY rowid")  # nosec B608
            except sqlite3.OperationalError:
                rows = con.execute(f"SELECT {listed} FROM {quoted}")  # nosec B608
            for count, row in enumerate(rows):
                if count % 2000 == 0 and time.monotonic() > deadline:
                    raise ValueError("over the check's budget")
                digest.update(repr(row).encode("utf-8", "replace"))
        return digest.hexdigest()
    finally:
        con.close()


def _recorded_stamps(entries) -> dict[str, tuple]:





    stamps: dict[str, tuple] = {}
    for entry in entries or []:
        snapshot = getattr(entry, "snapshot", None)
        if snapshot is None:
            continue
        for path, record in (getattr(snapshot, "file_backups", None) or {}).items():
            stamp = record.get("stamp") if isinstance(record, dict) else None
            for src, dst in (record.get("copies") or []) if isinstance(record, dict) else []:
                if stamp and _fold(src) == _fold(path):
                    stamps[_fold(dst)] = tuple(stamp)
        layers = getattr(snapshot, "layers", None) or {}
        for lid, copies in (getattr(snapshot, "backups", None) or {}).items():
            record = layers.get(lid) or {}
            stamp, main = record.get("stamp"), str(record.get("path") or "")
            for src, dst in copies or []:
                if stamp and main and _fold(src) == _fold(main):
                    stamps[_fold(dst)] = tuple(stamp)
    return stamps


def maybe_changed(history, thread_id: str, entry, current) -> list[str]:








    pairs = files_to_check(history, thread_id, entry, current)
    if not pairs:
        return []
    from .snapshot_paths import _stamp

    stamps = _recorded_stamps(history.entries(thread_id))
    deadline = time.monotonic() + INLINE_ROWS_S
    changed = []
    for original, copy in pairs:
        if not os.path.exists(original):
            continue
        recorded = stamps.get(_fold(copy))
        if recorded is not None and _stamp(original) == recorded:
            continue
        sqlite = os.path.splitext(original)[1].lower() in _SQLITE_EXTENSIONS
        try:
            if not sqlite and recorded is None:
                a, b = os.stat(original), os.stat(copy)
                if a.st_size == b.st_size and a.st_mtime_ns == b.st_mtime_ns:
                    continue
            elif sqlite and os.path.getsize(original) <= INLINE_ROWS_BYTES \
                    and os.path.getsize(copy) <= INLINE_ROWS_BYTES \
                    and deadline - time.monotonic() > 0.05 \
                    and not hand_changed_files([(original, copy)], budget_s=deadline - time.monotonic()):
                continue
        except OSError:
            pass
        changed.append(original)
    return changed

