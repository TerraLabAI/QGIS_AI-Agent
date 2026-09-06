# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run snapshot: the state before a run modifies the project, and one way back."""
















from __future__ import annotations

import contextlib
import hashlib
import os
import pathlib
import shutil
import sqlite3
import stat
import tempfile
import time

from qgis.core import QgsProject, QgsProviderRegistry, QgsVectorLayer

from .host_platform import retry_file_op
from .logger import log, log_warning
from .settings import account_dir
from .snapshot_features import (  # noqa: F401 - shutdown is re-exported for plugin.py
    MAX_CAPTURE_SECONDS,
    MAX_SIGNATURE_TOTAL,
    _FeaturePass,
    _jobs,
    compare_signatures,
    feature_signatures,
    shutdown,
)
from .snapshot_report import (  # noqa: F401 - re-exported: the panel and the executor import them from here
    changed_layer_items,
    describe_diff,
    diff_changed,
)
from .snapshot_style import _style_digest



MAX_HASH_FILE_BYTES = 200 * 1024 * 1024



INLINE_BACKUP_BYTES = 4 * 1024 * 1024



MAX_DIFF_SECONDS = 0.2
MAX_SQLITE_BACKUP_SECONDS = 60.0
KEEP_SNAPSHOTS = 12
_SHAPEFILE_SIDECARS = (".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx", ".qpj")
_SQLITE_EXTENSIONS = (".gpkg", ".sqlite", ".sqlite3", ".db")
_LAYER_ARG_KEYS = ("layer_name", "layer", "layer_id", "input", "INPUT", "target_layer",
                   "output_path", "path", "file_path")


_SNAPSHOTS_DIR = ""






_HELD: set[str] = set()


def hold_snapshot(path: str) -> None:
    """Keep ``path`` out of the pruner's reach until it is released."""
    if path:
        _HELD.add(os.path.normpath(str(path)))


def release_snapshot(path: str) -> None:
    _HELD.discard(os.path.normpath(str(path or "")))


def held_snapshots() -> set:
    return set(_HELD)


def snapshots_dir() -> str:
    global _SNAPSHOTS_DIR
    path = os.path.join(account_dir(), "snapshots")
    if path != _SNAPSHOTS_DIR:
        os.makedirs(path, exist_ok=True)
        _SNAPSHOTS_DIR = path
    return path


def _map_canvas():
    """The QGIS map canvas, or None outside a running QGIS."""
    try:
        import qgis.utils

        return qgis.utils.iface.mapCanvas() if qgis.utils.iface is not None else None
    except Exception:  # noqa: BLE001 - no iface in tests and in headless runs
        return None


def _refresh_canvas() -> None:
    canvas = _map_canvas()
    if canvas is None:
        return
    try:
        canvas.refresh()
    except Exception:  # nosec B110 - a repaint is best effort
        pass


@contextlib.contextmanager
def _canvas_held():
    """Rendering off and the wait cursor on, for the length of a restore."""






    canvas = _map_canvas()
    rendering = True
    if canvas is not None:
        try:
            rendering = bool(canvas.renderFlag())
            canvas.stopRendering()
            canvas.setRenderFlag(False)
        except Exception:  # noqa: BLE001 - an old canvas without the flag
            canvas = None
    cursor_set = False
    try:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QApplication

        if QApplication.instance() is not None:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            cursor_set = True
            QApplication.processEvents()
    except Exception:  # noqa: BLE001 - no application in the tests
        cursor_set = False
    try:
        yield
    finally:
        if canvas is not None:
            try:
                canvas.setRenderFlag(rendering)
            except Exception:  # nosec B110 - recovery is best effort
                pass
        if cursor_set:
            try:
                from qgis.PyQt.QtWidgets import QApplication

                QApplication.restoreOverrideCursor()
            except Exception:  # nosec B110 - recovery is best effort
                pass


def _stamp(path: str) -> tuple[int, ...] | None:
    """Main ``(size, mtime_ns)`` plus the same pair for a SQLite WAL when present."""






    try:
        st = os.stat(path)
    except OSError:
        return None
    stamp = (int(st.st_size), int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))))
    if os.path.splitext(path)[1].lower() not in _SQLITE_EXTENSIONS:
        return stamp
    try:
        wal = os.stat(path + "-wal")
        wal_stamp = (int(wal.st_size), int(getattr(wal, "st_mtime_ns", int(wal.st_mtime * 1e9))))
    except OSError:
        wal_stamp = (-1, -1)
    return stamp + wal_stamp


def _file_hash(path: str) -> str | None:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def layer_file_path(layer) -> str | None:
    """The local file behind a layer, or None for remote and memory sources."""
    provider = layer.providerType() or ""
    source = layer.source() or ""
    if provider in ("memory", "wms", "wfs", "oapif", "postgres", "arcgisfeatureserver"):
        return None
    path = None
    try:
        parts = QgsProviderRegistry.instance().decodeUri(provider, source)
        path = parts.get("path") if isinstance(parts, dict) else None
    except Exception:
        path = None
    if not path:
        path = source.split("|", 1)[0]
    if path.startswith(("/vsi", "http://", "https://")):
        return None
    return path if os.path.isfile(path) else None


def _ordered_layers(project) -> list:
    """The layers a run is most likely to touch first: the active one, then the ones drawn on the map, then the rest."""


    layers = dict(project.mapLayers())
    order: list = []
    seen: set[str] = set()

    def push(layer_id: str) -> None:
        layer = layers.get(layer_id)
        if layer is not None and layer_id not in seen:
            seen.add(layer_id)
            order.append((layer_id, layer))

    try:
        from qgis.utils import iface
        active = iface.activeLayer() if iface is not None else None
        if active is not None:
            push(active.id())
    except Exception:  # nosec B110 - no iface in a headless run
        pass
    try:
        for node in project.layerTreeRoot().findLayers():
            if node.isVisible():
                push(node.layerId())
    except Exception:  # nosec B110 - a tree that will not walk falls back to the map order
        pass
    for layer_id in layers:
        push(layer_id)
    return order


def _project_state(project) -> dict:
    """The project-level facts the layer records leave out: the project CRS and the layer tree (order, groups, visibility)."""



    state: dict = {"crs": "", "tree": []}
    try:
        state["crs"] = project.crs().authid() or ""
    except Exception:  # nosec B110 - a project without a CRS is recorded blank
        pass
    try:
        root = project.layerTreeRoot()
        for node in getattr(root, "findLayers", lambda: [])():
            groups = []
            parent = node.parent()
            while parent is not None and parent is not root:
                groups.append(str(parent.name() or ""))
                parent = parent.parent()
            state["tree"].append([str(node.layerId()), "/".join(reversed(groups)), bool(node.isVisible())])
    except Exception:  # nosec B110 - a tree that will not walk is recorded empty
        pass
    return state


def _sidecars(path: str) -> list[str]:
    stem, ext = os.path.splitext(path)
    if ext.lower() != ".shp":
        return [path]
    out = [path]
    for extra in _SHAPEFILE_SIDECARS:
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
    source_uri = pathlib.Path(os.path.abspath(source)).as_uri() + "?mode=ro"
    source_db = sqlite3.connect(source_uri, timeout=5.0, uri=True)
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
    source_uri = pathlib.Path(os.path.abspath(backup)).as_uri() + "?mode=ro"
    source_db = sqlite3.connect(source_uri, timeout=5.0, uri=True)
    try:
        target_is_sqlite = True
        if os.path.isfile(target):
            target_uri = pathlib.Path(os.path.abspath(target)).as_uri() + "?mode=ro"
            try:
                target_db = sqlite3.connect(target_uri, timeout=5.0, uri=True)
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


class RunSnapshot:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.captured = False
        self.captured_at = 0.0
        self.dir = os.path.join(snapshots_dir(), "".join(c for c in run_id if c.isalnum() or c in "-_")[:64])
        self.project_path = os.path.join(self.dir, "project.qgz")
        self.original_file = ""
        self.was_dirty = False
        self.layers: dict[str, dict] = {}





        self.layer_ids: set[str] = set()

        self.project_state: dict = {"crs": "", "tree": []}
        self.last_diff: dict | None = None
        self.last_diff_at = 0.0
        self.backups: dict[str, list[tuple[str, str]]] = {}


        self.path_backups: dict[str, list[tuple[str, str]]] = {}





        self.unbacked: dict[str, str] = {}
        self.file_backups: dict[str, dict] = {}


        self.pending_copies: list[tuple] = []
        self.copy_errors: list[str] = []
        self.memory_features: dict[str, list] = {}

        self.feature_signatures: dict[str, dict] = {}
        self._pass: _FeaturePass | None = None



    def _adopt_pass(self) -> None:
        """Main thread, once the feature pass is over: its results become the snapshot's."""
        feature_pass = self._pass
        if feature_pass is None:
            return
        self.feature_signatures = feature_pass.signatures
        self.memory_features = feature_pass.memory_features

    def wait_for_features(self, timeout: float = 2.0) -> bool:
        """True once the feature pass of the capture has landed."""
        feature_pass = self._pass
        if feature_pass is None:
            return True
        ok = feature_pass.wait(timeout)
        if ok:
            self._adopt_pass()
        return ok

    def _record(self, layer, with_hash: bool, with_style: bool = True) -> dict:
        path = layer_file_path(layer)
        record = {
            "id": layer.id(), "name": layer.name(), "source": layer.source() or "",
            "provider": layer.providerType() or "", "path": path,
            "crs": layer.crs().authid() or "",
            "feature_count": None, "hash": None, "size": None, "stamp": None,
            "style": _style_digest(layer) if with_style else None,
        }
        if isinstance(layer, QgsVectorLayer):
            try:
                n = layer.featureCount()
                record["feature_count"] = int(n) if n is not None and n >= 0 else None
            except Exception:  # nosec B110 - snapshot recovery is best effort
                pass
        if path and with_hash:
            stamp = _stamp(path)
            record["stamp"] = stamp
            record["size"] = stamp[0] if stamp else None
        return record

    def capture(self) -> bool:
        if self.captured:
            return True
        project = QgsProject.instance()
        started = time.monotonic()
        try:
            os.makedirs(self.dir, exist_ok=True)
        except OSError as exc:
            log_warning(f"Snapshot folder not writable: {exc}")
            return False
        self.original_file = project.fileName() or ""
        self.was_dirty = project.isDirty()
        ok = False
        try:
            ok = bool(project.write(self.project_path))
        except Exception as exc:
            log_warning(f"Snapshot write failed: {exc}")
        finally:
            try:
                project.setFileName(self.original_file)
                project.setDirty(self.was_dirty)
            except Exception:  # nosec B110 - snapshot recovery is best effort
                pass
        self.layers = {}
        self.layer_ids = set(project.mapLayers().keys())
        self.memory_features = {}
        self.feature_signatures = {}
        if self.original_file and os.path.isfile(self.original_file):
            self.backup_files([self.original_file])




        deadline = time.monotonic() + MAX_CAPTURE_SECONDS
        light = 0
        for lid, layer in _ordered_layers(project):





            full = time.monotonic() <= deadline
            try:
                self.layers[lid] = self._record(layer, with_hash=full, with_style=full)
            except Exception as exc:
                log_warning(f"Snapshot skipped layer {lid}: {exc}")
                continue
            if not full:
                light += 1
        if light:
            log(f"Snapshot over {MAX_CAPTURE_SECONDS:.1f} s: {light} layers recorded without their style.")
        self.project_state = _project_state(project)
        self._pass = _FeaturePass(project)
        self._pass.start(self._adopt_pass)
        self.captured = ok
        self.captured_at = time.time()
        log(f"Snapshot {'captured' if ok else 'FAILED'} for run {self.run_id[:8]} in "
            f"{time.monotonic() - started:.2f} s ({len(self.layers)} layers)")
        _jobs().schedule_job(lambda: prune_snapshots(keep=KEEP_SNAPSHOTS, protect=self.dir))
        return ok



    def backup_layer_files(self, layer) -> bool:
        """Copy the files behind ``layer`` into the snapshot."""










        lid = layer.id()
        if lid in self.backups:
            return True
        name = ""
        try:
            name = str(layer.name() or "")
        except Exception:  # noqa: BLE001  # nosec B110 - a layer that cannot name itself is still recorded
            pass
        path = layer_file_path(layer)
        if not path:

            self.unbacked[lid] = name
            return False
        try:
            canonical = os.path.realpath(path)
        except (OSError, ValueError):
            canonical = path
        known = self.path_backups.get(canonical)
        if known is not None:

            self.backups[lid] = known
            self.unbacked.pop(lid, None)
            return True
        try:
            if _backup_size(path) > MAX_HASH_FILE_BYTES:
                self.unbacked[lid] = name
                return False
        except OSError:
            self.unbacked[lid] = name
            return False
        folder = os.path.join(self.dir, "backup", lid[:40])
        copies = _backup_copies(folder, _sidecars(path))
        self.backups[lid] = copies
        self.path_backups[canonical] = copies
        self.unbacked.pop(lid, None)
        self._plan_copies(folder, copies, name or os.path.basename(path))
        return True

    def backup_targets(self, args: dict) -> int:
        count = 0
        for layer in resolve_layers(args if isinstance(args, dict) else {}):
            if self.backup_layer_files(layer):
                count += 1
        return count

    def backup_files(self, paths) -> int:
        """Copy existing files (with shapefile sidecars) a call is about to replace. Idempotent per path."""
        count = 0
        for raw in paths or []:
            path = os.path.realpath(str(raw))
            if path in self.file_backups or not os.path.isfile(path):
                continue





            try:
                if _backup_size(path) > MAX_HASH_FILE_BYTES:
                    self.unbacked["file:" + path] = os.path.basename(path)
                    continue
            except OSError:
                self.unbacked["file:" + path] = os.path.basename(path)
                continue
            folder = os.path.join(self.dir, "files", f"{len(self.file_backups):03d}")
            copies = _backup_copies(folder, _sidecars(path))
            self.file_backups[path] = {"copies": copies, "stamp": _stamp(path), "hash": None}
            self._plan_copies(folder, copies, os.path.basename(path))
            count += 1
        return count



    def _plan_copies(self, folder: str, copies: list, label: str) -> None:
        """Small copies happen now; large ones are queued for ``run_pending_copies``, which the executor runs off the main thread before the call."""

        total = 0
        for src, _dst in copies:
            try:
                total += _backup_size(src)
            except OSError:
                continue
        if total <= INLINE_BACKUP_BYTES:
            if not _copy_files(folder, copies, label):
                self._copy_failed(folder, label)
            return
        self.pending_copies.append((folder, copies, label))

    def _copy_failed(self, folder: str, label: str) -> None:
        self.copy_errors.append(label)
        self.unbacked["copy:" + folder] = label
        raise OSError(f"Could not back up {label}; the operation was not started.")

    def has_pending_copies(self) -> bool:
        return bool(self.pending_copies)

    def run_pending_copies(self) -> int:
        """Perform the queued copies. Safe on any thread: files only."""
        jobs, self.pending_copies = self.pending_copies, []
        done = 0
        for folder, copies, label in jobs:
            if _copy_files(folder, copies, label):
                done += 1
            else:
                self._copy_failed(folder, label)
        return done

    def backup_mentioned(self, text: str) -> int:
        """Back up every file-backed project layer whose name or id appears in ``text`` (execute_code)."""
        if not text:
            return 0
        count = 0
        for lid, layer in QgsProject.instance().mapLayers().items():
            try:
                named = layer.name() and layer.name() in text
            except Exception:
                named = False
            if (named or lid in text) and self.backup_layer_files(layer):
                count += 1
        return count



    def diff(self) -> dict:
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        started = time.monotonic()
        after: dict[str, dict] = {}
        deadline = started + MAX_DIFF_SECONDS
        for lid, layer in project.mapLayers().items():
            try:
                after[lid] = self._record(layer, with_hash=True)
                after[lid]["in_tree"] = root.findLayer(lid) is not None
                after[lid]["memory"] = layer.providerType() == "memory"
            except Exception:  # nosec B112 - unreadable layer is omitted
                continue
        before = self.layers



        known = self.layer_ids or set(before)
        added = [{"name": r["name"], "id": lid} for lid, r in after.items() if lid not in known]
        removed = [{"name": (before.get(lid) or {}).get("name") or lid, "id": lid}
                   for lid in known if lid not in after]
        counts, crs_changes, files_changed, style_changes = [], [], [], []
        changed: set[str] = {x["id"] for x in added} | {x["id"] for x in removed}
        layers_now = project.mapLayers()
        for lid in before.keys() & after.keys():
            b, a = before[lid], after[lid]
            counted = b["feature_count"] is not None and a["feature_count"] is not None
            entry = None
            if counted and b["feature_count"] != a["feature_count"]:
                entry = {"name": a["name"], "id": lid, "before": b["feature_count"],
                         "after": a["feature_count"], "delta": a["feature_count"] - b["feature_count"]}
            over_budget = time.monotonic() > deadline
            signed = self.feature_signatures.get(lid)
            if signed is not None and counted and not over_budget:


                now = feature_signatures(layers_now.get(lid), MAX_SIGNATURE_TOTAL, deadline)
                if now is not None:
                    fine = compare_signatures(signed, now)
                    if entry is None and fine["changed"]:
                        entry = {"name": a["name"], "id": lid, "before": b["feature_count"],
                                 "after": a["feature_count"], "delta": 0}
                    if entry is not None:
                        entry.update(fine)
            if entry is not None:
                counts.append(entry)
                changed.add(lid)
            if b["crs"] != a["crs"]:
                crs_changes.append({"name": a["name"], "id": lid, "before": b["crs"], "after": a["crs"]})
                changed.add(lid)
            if b.get("stamp") and b.get("path") and a.get("stamp") and a["stamp"] != b["stamp"]:
                files_changed.append({"name": a["name"], "id": lid, "path": b["path"]})
                changed.add(lid)
            if b.get("style") and a.get("style") and b["style"] != a["style"]:
                style_changes.append({"name": a["name"], "id": lid})
                changed.add(lid)
        orphans = [{"name": r["name"], "id": lid} for lid, r in after.items()
                   if lid not in before and r.get("memory") and not r.get("in_tree")]
        visibility_changes, project_changes = self._project_diff(_project_state(project), after)
        for entry in visibility_changes:
            changed.add(entry["id"])
        spent = time.monotonic() - started
        if spent > MAX_DIFF_SECONDS:
            log_warning(f"Snapshot diff took {spent:.1f} s: the layers after the budget were "
                        "compared on their counts only.")
        self.last_diff_at = time.monotonic()
        result = {
            "layers_added": added,
            "layers_removed": removed,
            "feature_count_changes": counts,
            "crs_changes": crs_changes,
            "files_changed": files_changed,
            "style_changes": style_changes,
            "visibility_changes": visibility_changes,
            "project_changes": project_changes,
            "orphan_temporary_layers": orphans,
            "changed_layers": len(changed),
            "snapshot_available": self.captured,
        }
        self.last_diff = result
        return result

    def _project_diff(self, now: dict, after: dict) -> tuple[list, list]:
        """Visibility flips per layer, and the project-level moves (CRS, layer order or grouping) that belong to no layer."""


        then = self.project_state or {}
        visibility: list = []
        project: list = []
        if then.get("crs") and now.get("crs") and then["crs"] != now["crs"]:
            project.append({"what": "crs", "before": then["crs"], "after": now["crs"]})
        before_tree = {row[0]: row for row in then.get("tree") or []}
        after_tree = {row[0]: row for row in now.get("tree") or []}
        common = [lid for lid in after_tree if lid in before_tree]
        for lid in common:
            if before_tree[lid][2] != after_tree[lid][2]:
                name = (after.get(lid) or {}).get("name") or lid
                visibility.append({"name": name, "id": lid, "visible": bool(after_tree[lid][2])})
        order_then = [lid for lid in before_tree if lid in after_tree]
        if order_then != common or any(before_tree[lid][1] != after_tree[lid][1] for lid in common):
            project.append({"what": "layer_tree"})
        return visibility, project



    def restore(self) -> dict:
        """Put the project back, with the canvas held still while it happens."""











        if not self.captured or not os.path.isfile(self.project_path):
            return {"ok": False, "message": "No snapshot to restore."}
        with _canvas_held():
            result = self._restore_now()
        _refresh_canvas()
        return result

    def _restore_now(self) -> dict:
        project = QgsProject.instance()
        for layer in list(project.mapLayers().values()):
            try:
                if isinstance(layer, QgsVectorLayer) and layer.isEditable():
                    layer.rollBack()
            except Exception:  # nosec B110 - snapshot recovery is best effort
                pass
        project.clear()
        files_restored = []
        file_restore_errors = []
        for path, record in self.file_backups.items():
            if os.path.isfile(path) and _unchanged(path, record):
                continue
            for src, dst in record["copies"]:
                try:
                    _restore_file(dst, src)
                    files_restored.append(src)
                except (OSError, sqlite3.Error) as exc:
                    log_warning(f"Could not restore {os.path.basename(src)}: {exc}")
                    file_restore_errors.append({"path": src, "error": str(exc)})
        for lid, copies in self.backups.items():
            record = self.layers.get(lid) or {}
            path = record.get("path")
            if path and os.path.isfile(path) and _unchanged(path, record):
                continue
            for src, dst in copies:


                if src in files_restored:
                    continue
                try:
                    _restore_file(dst, src)
                    files_restored.append(src)
                except (OSError, sqlite3.Error) as exc:
                    log_warning(f"Could not restore {os.path.basename(src)}: {exc}")
                    file_restore_errors.append({"path": src, "error": str(exc)})
        project_ok = False
        try:
            project_ok = bool(project.read(self.project_path))
        except Exception as exc:
            log_warning(f"Snapshot read failed: {exc}")
        try:
            project.setFileName(self.original_file)
            project.setDirty(True)
        except Exception:  # nosec B110 - snapshot recovery is best effort
            pass
        refilled = 0
        self.wait_for_features(2.0)
        refill_errors: list[str] = []
        for lid, features in self.memory_features.items():
            layer = project.mapLayer(lid)
            if isinstance(layer, QgsVectorLayer) and layer.providerType() == "memory" and layer.featureCount() == 0:
                try:




                    accepted = layer.dataProvider().addFeatures(features)
                    layer.updateExtents()
                    layer.triggerRepaint()
                    got = layer.featureCount()
                    if accepted is False or (got >= 0 and got < len(features)):
                        refill_errors.append(f"{layer.name()} ({max(got, 0)}/{len(features)})")
                    else:
                        refilled += 1
                except Exception as exc:
                    log_warning(f"Could not refill memory layer {layer.name()}: {exc}")
                    refill_errors.append(str(layer.name()))
        ok = project_ok and not file_restore_errors and not refill_errors
        log(f"Snapshot restored for run {self.run_id[:8]}: project={'ok' if project_ok else 'failed'}, "
            f"files={len(files_restored)}, file errors={len(file_restore_errors)}, "
            f"memory layers refilled={refilled}, memory layers incomplete={len(refill_errors)}")
        if file_restore_errors and project_ok:
            message = f"Project restored, but {len(file_restore_errors)} file(s) could not be restored."
        elif file_restore_errors:
            message = (f"The snapshot project and {len(file_restore_errors)} file(s) "
                       "could not be restored.")
        elif project_ok and refill_errors:
            message = ("Project restored, but " + ", ".join(refill_errors[:3])
                       + " did not take all their features back.")
        elif project_ok:
            message = "Project restored."
        else:
            message = "The snapshot project could not be read."
        return {"ok": ok, "files_restored": files_restored,
                "file_restore_errors": file_restore_errors,
                "memory_layers_refilled": refilled,
                "memory_layers_incomplete": refill_errors, "message": message}

    def discard(self) -> bool:
        """True when the folder is gone. False, and logged, when it is held."""
        return _remove_tree(self.dir, "Snapshot")


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


def prune_snapshots(keep: int = KEEP_SNAPSHOTS, protect: str = "") -> None:
    """Drop the oldest snapshot folders past ``keep``, never a held one."""





    base = snapshots_dir()
    held = held_snapshots()
    try:
        entries = [(os.path.getmtime(os.path.join(base, n)), os.path.join(base, n))
                   for n in os.listdir(base) if os.path.isdir(os.path.join(base, n))]
    except OSError:
        return
    entries.sort(reverse=True)
    for _, path in entries[keep:]:
        if path != protect and os.path.normpath(path) not in held:
            _remove_tree(path, "Old snapshot")
