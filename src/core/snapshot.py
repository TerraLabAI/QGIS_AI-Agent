# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run snapshot: the state before a run modifies the project, and one way back."""


















from __future__ import annotations

import contextlib
import os
import sqlite3
import time

from qgis.core import QgsProject, QgsVectorLayer

from .logger import log, log_warning
from .snapshot_features import (  # noqa: F401 - shutdown is re-exported for plugin.py
    MAX_CAPTURE_SECONDS,
    MAX_SIGNATURE_TOTAL,
    _FeaturePass,
    _jobs,
    compare_signatures,
    feature_signatures,
    held_features,
    is_copy_table,
    refill_from_copy,
    shutdown,
)
from .snapshot_files import (  # noqa: F401 - the names this module moved out are re-exported here
    MAX_INDEX_BYTES,
    MAX_SQLITE_BACKUP_SECONDS,
    ORPHAN_GRACE_SECONDS,
    _backup_copies,
    _backup_size,
    _copies_unchanged,
    _copy_files,
    _is_sqlite_file,
    _remove_tree,
    _replace_corrupt_sqlite,
    _restore_file,
    _restore_sqlite,
    _sidecars,
    _sqlite_backup,
    _sqlite_copy,
    _unchanged,
    prune_snapshots,
    resolve_layers,
)
from .snapshot_paths import (  # noqa: F401 - the names this module moved out are re-exported here
    _canvas_held,
    _file_hash,
    _folder_freshness,
    _map_canvas,
    _ordered_layers,
    _project_state,
    _refresh_canvas,
    _stamp,
    checkpoints_dir,
    folder_bytes,
    held_snapshots,
    hold_snapshot,
    inside,
    layer_file_path,
    release_snapshot,
    snapshots_dir,
)
from .snapshot_project import (  # noqa: F401 - the names this module moved out are re-exported here
    _CORE_SECTIONS,
    LAYER_SECTIONS_FILE,
    RESTORE_FILE,
    SECTIONS_FILE,
    _children,
    _core_sections,
    _dom,
    _enum_int,
    _handler_sections,
    _layer_handler_sections,
    _layer_sections_by_id,
    _merged_copy,
    _put_layer_sections,
    _signals_blocked,
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



REASON_MEMORY_LOST = "memory_lost"


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


        self.memory_files: dict[str, tuple[str, int]] = {}



        self.unbacked_reasons: dict[str, str] = {}

        self.feature_signatures: dict[str, dict] = {}
        self._pass: _FeaturePass | None = None





        self._ready_callbacks: list = []



        self.quiet = False



    def to_record(self) -> dict:
        """The JSON-safe state a restore after a restart needs."""





        return {
            "run_id": self.run_id,
            "dir": self.dir,
            "captured": self.captured,
            "captured_at": self.captured_at,
            "quiet": self.quiet,
            "original_file": self.original_file,
            "was_dirty": self.was_dirty,
            "layers": self.layers,
            "layer_ids": sorted(self.layer_ids),
            "project_state": self.project_state,
            "backups": {str(lid): [[str(src), str(dst)] for src, dst in copies]
                        for lid, copies in self.backups.items()},
            "unbacked": dict(self.unbacked),
            "unbacked_reasons": dict(self.unbacked_reasons),
            "file_backups": {str(path): {
                "copies": [[str(src), str(dst)] for src, dst in (record.get("copies") or [])],
                "stamp": list(record.get("stamp")) if record.get("stamp") else None,
                "hash": record.get("hash")}
                for path, record in self.file_backups.items()},

            "memory_files": {str(lid): [str(copy[0]), int(copy[1]), *[str(table) for table in copy[2:3]]]
                             for lid, copy in dict(self.memory_files).items()},
        }

    @classmethod
    def from_record(cls, record) -> RunSnapshot | None:
        """The snapshot a chat's index describes, or None when it is malformed."""







        if not isinstance(record, dict):
            return None
        run_id = record.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            return None
        folder = record.get("dir")
        if not isinstance(folder, str) or not inside(snapshots_dir(), folder):
            return None
        snap = cls(run_id)
        snap.dir = os.path.realpath(folder)
        snap.project_path = os.path.join(snap.dir, "project.qgz")
        snap.captured = bool(record.get("captured"))
        snap.quiet = bool(record.get("quiet"))
        try:
            snap.captured_at = float(record.get("captured_at") or 0.0)
        except (TypeError, ValueError):
            snap.captured_at = 0.0
        original = record.get("original_file")
        snap.original_file = original if isinstance(original, str) else ""
        snap.was_dirty = bool(record.get("was_dirty"))
        layers = record.get("layers")
        if isinstance(layers, dict):
            snap.layers = {}
            for lid, rec in layers.items():
                if not isinstance(rec, dict):
                    continue
                layer = dict(rec)



                stamp = layer.get("stamp")
                try:
                    layer["stamp"] = (tuple(int(x) for x in stamp)
                                      if isinstance(stamp, (list, tuple)) else None)
                except (TypeError, ValueError):
                    layer["stamp"] = None
                snap.layers[str(lid)] = layer
        ids = record.get("layer_ids")
        if isinstance(ids, (list, tuple, set, frozenset)):
            snap.layer_ids = {str(lid) for lid in ids}
        state = record.get("project_state")
        if isinstance(state, dict):
            snap.project_state = state
        backups = record.get("backups")
        if isinstance(backups, dict):
            for lid, copies in backups.items():
                if not isinstance(copies, list):
                    continue
                kept = [(pair[0], pair[1]) for pair in copies
                        if isinstance(pair, (list, tuple)) and len(pair) == 2
                        and isinstance(pair[0], str) and isinstance(pair[1], str)
                        and inside(snap.dir, pair[1])]
                if kept:
                    snap.backups[str(lid)] = kept
        for key in ("unbacked", "unbacked_reasons"):
            values = record.get(key)
            if not isinstance(values, dict):
                continue
            target = getattr(snap, key)
            for lid, value in values.items():
                if isinstance(value, str):
                    target[str(lid)] = value
        files = record.get("file_backups")
        if isinstance(files, dict):
            for path, entry in files.items():
                if not isinstance(path, str) or not isinstance(entry, dict):
                    continue
                copies = [(pair[0], pair[1]) for pair in (entry.get("copies") or [])
                          if isinstance(pair, (list, tuple)) and len(pair) == 2
                          and isinstance(pair[0], str) and isinstance(pair[1], str)
                          and inside(snap.dir, pair[1])]
                stamp = entry.get("stamp")
                snap.file_backups[path] = {
                    "copies": copies,
                    "stamp": tuple(stamp) if isinstance(stamp, (list, tuple)) else None,
                    "hash": entry.get("hash")}
        memory = record.get("memory_files")
        if isinstance(memory, dict):
            for lid, pair in memory.items():
                if not isinstance(pair, (list, tuple)) or len(pair) not in (2, 3):
                    continue
                path, count, table = pair[0], pair[1], tuple(pair[2:3])
                if not isinstance(path, str) or not inside(snap.dir, path):
                    continue


                if table and not is_copy_table(table[0]):
                    continue
                try:
                    count = int(count or 0)
                except (TypeError, ValueError):
                    continue
                snap.memory_files[str(lid)] = (path, count, *table)


        for lid, rec in snap.layers.items():
            if not isinstance(rec, dict) or rec.get("provider") != "memory":
                continue
            try:
                count = int(rec.get("feature_count") or 0)
            except (TypeError, ValueError):
                count = 0
            if count > 0 and lid not in snap.memory_files and lid not in snap.unbacked:
                snap.unbacked[lid] = str(rec.get("name") or lid)
                snap.unbacked_reasons[lid] = REASON_MEMORY_LOST
        return snap



    def _adopt_pass(self) -> None:
        """Main thread, once the feature pass is over: its results become the snapshot's."""
        feature_pass = self._pass
        if feature_pass is None:
            return
        self.feature_signatures = feature_pass.signatures
        self.memory_features = feature_pass.memory_features
        self.memory_files = feature_pass.memory_files
        self._note_uncopied(feature_pass)
        callbacks, self._ready_callbacks = self._ready_callbacks, []
        for callback in callbacks:
            try:
                callback()
            except Exception as exc:  # noqa: BLE001 - a caller's callback must not break capture
                log_warning(f"Snapshot features-ready callback failed: {exc}")

    def _note_uncopied(self, feature_pass) -> None:
        """The memory layers the pass could not copy join ``unbacked``, with their reason."""
        for lid, (name, reason) in list(feature_pass.unbacked.items()):
            self.unbacked[lid] = name
            self.unbacked_reasons[lid] = reason

    def wait_for_features(self, timeout: float = 2.0) -> bool:
        """True once the signatures and the small memory layers of the capture are in."""
        feature_pass = self._pass
        if feature_pass is None:
            return True
        if feature_pass.done:
            self._adopt_pass()
            return True
        ok = feature_pass.wait_features(timeout)
        if ok:



            self.feature_signatures = feature_pass.signatures
            self.memory_features = feature_pass.memory_features
        return ok

    def on_features_ready(self, callback) -> None:
        """Call ``callback`` (main thread) once the capture's feature pass, memory-layer copies included, is done."""






        feature_pass = self._pass
        if feature_pass is None or feature_pass.done:
            callback()
            return
        self._ready_callbacks.append(callback)

    def _record(self, layer, with_hash: bool, with_style: bool = True) -> dict:
        path = layer_file_path(layer)
        record = {
            "id": layer.id(), "name": layer.name(), "source": layer.source() or "",
            "provider": layer.providerType() or "", "path": path,
            "crs": layer.crs().authid() or "",
            "feature_count": None, "hash": None, "size": None, "stamp": None,
            "style": _style_digest(layer) if with_style else None, "subset": "",
        }
        if isinstance(layer, QgsVectorLayer):
            try:
                n = layer.featureCount()
                record["feature_count"] = int(n) if n is not None and n >= 0 else None
            except Exception:  # nosec B110 - snapshot recovery is best effort
                pass
            try:
                record["subset"] = str(layer.subsetString() or "")
            except Exception:  # nosec B110 - a provider without a subset string
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



        self.quiet = False
        try:
            sections = _core_sections(project)
            with open(os.path.join(self.dir, SECTIONS_FILE), "wb") as fh:
                fh.write(sections)
            self.quiet = True
        except Exception as exc:  # noqa: BLE001 - an older QGIS, a stub: the copy keeps them itself
            log_warning(f"Snapshot relations not kept apart, the copy is written with the project's signals: {exc}")
        with _signals_blocked(project) if self.quiet else contextlib.nullcontext():
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
        if self.quiet:


            try:
                layer_sections = _layer_handler_sections(project)
                if layer_sections:
                    with open(os.path.join(self.dir, LAYER_SECTIONS_FILE), "wb") as fh:
                        fh.write(layer_sections)
            except Exception as exc:  # noqa: BLE001 - a plugin's per-layer XML never fails a capture
                log_warning(f"Snapshot per-layer plugin sections not kept: {exc}")
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
        self._pass = _FeaturePass(project, os.path.join(self.dir, "memory"))


        self.memory_files = self._pass.memory_files

        self._note_uncopied(self._pass)
        self._pass.start(self._adopt_pass)
        self.captured = ok
        self.captured_at = time.time()
        log(f"Snapshot {'captured' if ok else 'FAILED'} for run {self.run_id[:8]} in "
            f"{time.monotonic() - started:.2f} s ({len(self.layers)} layers)")


        base = snapshots_dir()
        index_dir = checkpoints_dir()
        try:
            from . import limits

            disk_mb = float(limits.current("CHECKPOINT_DISK_MB"))
        except Exception:  # noqa: BLE001 - an unread cap only means no disk ceiling
            disk_mb = 0.0
        _jobs().schedule_job(lambda: prune_snapshots(protect=self.dir, base=base,
                                                     index_dir=index_dir, disk_mb=disk_mb))
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
            try:
                memory = layer.providerType() == "memory"
            except Exception:  # noqa: BLE001 - a stub without a provider
                memory = False
            if memory:




                return lid not in self.unbacked

            self.unbacked[lid] = name
            return False
        try:
            canonical = os.path.realpath(path)
        except (OSError, ValueError):
            canonical = path
        known = self.path_backups.get(canonical)
        if known is None and canonical in self.file_backups:


            known = self.path_backups[canonical] = self.file_backups[canonical]["copies"]
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
            try:
                path = os.path.realpath(str(raw))
            except (OSError, ValueError):


                path = os.path.normpath(os.path.abspath(str(raw)))
            if path in self.file_backups or not os.path.isfile(path):
                continue
            if path in self.path_backups:



                self.file_backups[path] = {"copies": self.path_backups[path], "stamp": None, "hash": None}
                count += 1
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



    def restore(self, file_name: str | None = None, project_files=()) -> dict:
        """Put the project back, with the canvas held still while it happens."""


















        if not self.captured or not os.path.isfile(self.project_path):
            return {"ok": False, "message": "No snapshot to restore."}
        with _canvas_held():
            result = self._restore_now(file_name, project_files)
        _refresh_canvas()
        return result

    @staticmethod
    def _path_key(path: str) -> str:
        """``path`` resolved and case-folded, or only normalised when it cannot be resolved."""





        try:
            return os.path.normcase(os.path.realpath(path))
        except (OSError, ValueError):
            return os.path.normcase(os.path.normpath(path))

    def _project_files_left(self, file_name: str | None, project_files) -> set:
        """The project files a restore never writes: every one it knows but the file the project keeps."""
        key = self._path_key
        kept = self.original_file if file_name is None else file_name
        names = {key(name) for name in (self.original_file, *(project_files or ())) if isinstance(name, str) and name}
        if kept:
            names.discard(key(kept))
        return names

    def _restore_now(self, file_name: str | None = None, project_files=()) -> dict:
        project = QgsProject.instance()
        for layer in list(project.mapLayers().values()):
            try:
                if isinstance(layer, QgsVectorLayer) and layer.isEditable():
                    layer.rollBack()
            except Exception:  # nosec B110 - snapshot recovery is best effort
                pass

        source = self._source_to_read(project)



        left = self._project_files_left(file_name, project_files)
        project.clear()
        files_restored = []
        file_restore_errors = []
        files_left = []
        for path, record in self.file_backups.items():




            copies = record["copies"]
            if os.path.isfile(path) and _unchanged(path, record) and (
                    len(copies) < 2 or _copies_unchanged(copies)):
                continue
            if self._path_key(path) in left:
                files_left.append(path)
                log(f"Restore leaves {os.path.basename(path)} as it is on disk: the project is not that file now.")
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
            if path and os.path.isfile(path) and _unchanged(path, record) and (
                    len(copies) < 2 or _copies_unchanged(copies)):
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
            project_ok = bool(project.read(source))
        except Exception as exc:
            log_warning(f"Snapshot read failed: {exc}")
        finally:
            if source != self.project_path:
                try:
                    os.remove(source)
                except OSError as exc:
                    log_warning(f"Restore copy not removed ({source}): {exc}")
        try:
            project.setFileName(self.original_file if file_name is None else file_name)
            project.setDirty(True)
        except Exception:  # nosec B110 - snapshot recovery is best effort
            pass
        refilled = 0
        self.wait_for_features(2.0)
        refill_errors: list[str] = []
        handled: set[str] = set()
        for lid, features in self.memory_features.items():
            layer = project.mapLayer(lid)
            if isinstance(layer, QgsVectorLayer) and layer.providerType() == "memory" and layer.featureCount() == 0:
                handled.add(lid)
                try:




                    accepted = layer.dataProvider().addFeatures(features)
                    layer.updateExtents()
                    layer.triggerRepaint()
                    got = held_features(layer)
                    if accepted is False or (got >= 0 and got < len(features)):
                        refill_errors.append(f"{layer.name()} ({max(got, 0)}/{len(features)})")
                    else:
                        refilled += 1
                except Exception as exc:
                    log_warning(f"Could not refill memory layer {layer.name()}: {exc}")
                    refill_errors.append(str(layer.name()))
        refilled += self._refill_from_copies(project, refill_errors, handled)
        self._restore_subsets(project, refill_errors)
        self._name_unrefilled(project, refill_errors, handled)
        ok = project_ok and not file_restore_errors and not refill_errors
        log(f"Snapshot restored for run {self.run_id[:8]}: project={'ok' if project_ok else 'failed'}, "
            f"files={len(files_restored)}, file errors={len(file_restore_errors)}, files left={len(files_left)}, "
            f"memory layers refilled={refilled}, memory layers incomplete={len(refill_errors)}")
        if not project_ok and file_restore_errors:
            message = (f"The snapshot project and {len(file_restore_errors)} file(s) "
                       "could not be restored.")
        elif not project_ok:
            message = "The snapshot project could not be read."
        else:


            problems = []
            if file_restore_errors:
                problems.append(f"{len(file_restore_errors)} file(s) could not be restored")
            if refill_errors:
                named = ", ".join(refill_errors[:3])
                if len(refill_errors) > 3:
                    named += f" and {len(refill_errors) - 3} more"
                problems.append(f"{named} did not take all their features back")
            message = ("Project restored, but " + "; ".join(problems) + ".") if problems else "Project restored."
        return {"ok": ok, "files_restored": files_restored, "files_left": files_left,
                "file_restore_errors": file_restore_errors,
                "memory_layers_refilled": refilled,
                "memory_layers_incomplete": refill_errors, "message": message}

    def _source_to_read(self, project) -> str:
        """The file a restore reads: the copy, or the copy with what a quiet capture left out."""








        if not self.quiet:
            return self.project_path
        target = os.path.join(self.dir, RESTORE_FILE)
        try:
            captured = None
            path = os.path.join(self.dir, SECTIONS_FILE)
            if os.path.isfile(path):
                with open(path, "rb") as fh:
                    captured = fh.read()
            layers = None
            path = os.path.join(self.dir, LAYER_SECTIONS_FILE)
            if os.path.isfile(path):
                with open(path, "rb") as fh:
                    layers = fh.read()
            if _merged_copy(self.project_path, target, captured, _handler_sections(project), layers):
                return target
        except Exception as exc:  # noqa: BLE001 - the copy alone is still a restore
            log_warning(f"Snapshot sections not put back, the copy is read as it is: {exc}")
        return self.project_path

    def _name_unrefilled(self, project, refill_errors: list, handled: set) -> None:
        """Every memory layer the capture saw with features, and that came back short, is named."""





        for lid, record in self.layers.items():
            if lid in handled or not isinstance(record, dict) or record.get("provider") != "memory":
                continue
            expected = record.get("feature_count") or 0
            if expected <= 0:
                continue
            layer = project.mapLayer(lid)
            if not (isinstance(layer, QgsVectorLayer) and layer.providerType() == "memory"):
                continue
            got = layer.featureCount()
            if got is not None and got < expected:
                refill_errors.append(f"{layer.name()} ({max(got, 0)}/{expected})")

    def _restore_subsets(self, project, refill_errors: list) -> None:
        """Every memory layer the capture saw filtered gets its subset string back."""





        for lid, record in self.layers.items():
            if not isinstance(record, dict) or record.get("provider") != "memory":
                continue
            subset = record.get("subset") or ""
            if not subset:
                continue
            layer = project.mapLayer(lid)
            if not (isinstance(layer, QgsVectorLayer) and layer.providerType() == "memory"):
                continue
            try:
                if layer.subsetString() == subset:
                    continue
                accepted = layer.setSubsetString(subset)
            except Exception as exc:  # noqa: BLE001 - a provider that raises on the expression
                log_warning(f"Could not set the filter of memory layer {layer.name()} again: {exc}")
                accepted = False
            if accepted is False:
                log_warning(f"QGIS refused the filter of memory layer {layer.name()}: {subset}")
                refill_errors.append(f"{layer.name()} (filter not restored)")

    def _refill_from_copies(self, project, refill_errors: list, handled: set) -> int:
        """Every memory layer with a copy, back from its GeoPackage; used after a restart, when ``memory_features`` (the in-process copy) is empty or."""







        feature_pass = self._pass
        pending = dict(getattr(feature_pass, "memory_planned", None) or {})
        uncopied = {lid: name for lid, (name, _reason)
                    in (getattr(feature_pass, "unbacked", None) or {}).items()}
        refilled = 0
        for lid in dict.fromkeys(list(self.memory_files) + list(pending) + list(uncopied)):
            layer = project.mapLayer(lid)
            if not (isinstance(layer, QgsVectorLayer) and layer.providerType() == "memory"
                    and layer.featureCount() == 0):
                continue
            handled.add(lid)
            name = str(layer.name())
            copy = self.memory_files.get(lid)
            if copy is None:
                refill_errors.append(f"{name} (no copy)" if lid in uncopied else f"{name} (copy not finished)")
                continue
            path, count = copy[0], copy[1]
            try:
                got = refill_from_copy(layer, path, *copy[2:3])
                layer.triggerRepaint()
            except Exception as exc:  # noqa: BLE001 - a copy deleted or unreadable
                log_warning(f"Could not refill memory layer {name}: {exc}")
                refill_errors.append(name)
                continue
            if got < count:
                refill_errors.append(f"{name} ({max(got, 0)}/{count})")
            else:
                refilled += 1
        return refilled

    def discard(self) -> bool:
        """True when the folder is gone. False, and logged, when it is held."""
        return _remove_tree(self.dir, "Snapshot")
