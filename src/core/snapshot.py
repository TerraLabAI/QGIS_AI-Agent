# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






















from __future__ import annotations

import contextlib
import hashlib
import os
import sqlite3
import time
import uuid

from qgis.core import QgsProject, QgsVectorLayer

from . import designer_guard, tuning
from .host_platform import release_pooled_handles
from .layer_order import is_web_service, read_back
from .logger import log, log_warning
from .snapshot_features import (
    MAX_CAPTURE_SECONDS,
    MAX_SIGNATURE_TOTAL,
    _FeaturePass,
    _jobs,
    capture_seconds,
    compare_signatures,
    feature_signatures,
    forget_copies,
    held_features,
    is_copy_table,
    refill_from_copy,
    shutdown,
    signature_total,
)
from .snapshot_files import (
    MAX_INDEX_BYTES,
    ORPHAN_GRACE_SECONDS,
    _backup_copies,
    _backup_size,
    _copies_unchanged,
    _copy_files,
    _remove_tree,
    _same_content,
    _sidecars,
    _unchanged,
    prune_snapshots,
    resolve_layers,
    restore_group,
)
from .snapshot_paths import (
    _canvas_held,
    _ordered_layers,
    _project_state,
    _refresh_canvas,
    _stamp,
    checkpoints_dir,
    folder_bytes,
    hold_snapshot,
    inside,
    layer_file_path,
    release_snapshot,
    snapshots_dir,
)
from .snapshot_project import (
    LAYER_SECTIONS_FILE,
    RESTORE_FILE,
    SECTIONS_FILE,
    _core_sections,
    _handler_sections,
    _layer_handler_sections,
    _merged_copy,
    _signals_blocked,
)
from .snapshot_report import (
    changed_layer_items,
    describe_diff,
    diff_changed,
)
from .snapshot_style import _style_digest



__all__ = [
    "changed_layer_items",
    "checkpoints_dir",
    "compare_signatures",
    "describe_diff",
    "diff_changed",
    "feature_signatures",
    "folder_bytes",
    "forget_styles",
    "held_features",
    "hold_snapshot",
    "INLINE_BACKUP_BYTES",
    "inside",
    "is_copy_table",
    "layer_file_path",
    "LAYER_SECTIONS_FILE",
    "MAX_CAPTURE_SECONDS",
    "MAX_DIFF_SECONDS",
    "MAX_HASH_FILE_BYTES",
    "MAX_INDEX_BYTES",
    "MAX_SIGNATURE_TOTAL",
    "ORPHAN_GRACE_SECONDS",
    "prune_snapshots",
    "REASON_MEMORY_LOST",
    "REASON_UNSAVED_EDITS",
    "RESTORE_COPY_WAIT_S",
    "refill_from_copy",
    "release_snapshot",
    "remember_style",
    "remembered_style",
    "resolve_layers",
    "RESTORE_FILE",
    "RunSnapshot",
    "SECTIONS_FILE",
    "shutdown",
    "snapshots_dir",
    "style_memory_dir",
    "style_xml",
    "unsaved_edit_layers",
    "STYLE_MEMORY_HOURS",
    "STYLE_MEMORY_LAYERS",
]



MAX_HASH_FILE_BYTES = 200 * 1024 * 1024



INLINE_BACKUP_BYTES = 4 * 1024 * 1024




MAX_DIFF_SECONDS = 0.2



REASON_MEMORY_LOST = "memory_lost"



REASON_UNSAVED_EDITS = "unsaved_edits"



RESTORE_COPY_WAIT_S = 60.0


_DEFINITION_ONLY_PROVIDERS = frozenset({"wms", "xyz", "wcs", "arcgismapserver", "vectortile", "mbtilesvectortiles"})


def _is_project_file(path: str) -> bool:

    return os.path.splitext(str(path or ""))[1].lower() in (".qgs", ".qgz")


def unsaved_edit_layers(project=None) -> list:

    project = project or QgsProject.instance()
    held = []
    for layer in list(project.mapLayers().values()):
        try:
            if isinstance(layer, QgsVectorLayer) and layer.isEditable() and layer.isModified():
                held.append(layer)
        except RuntimeError:
            continue
    return held



















STYLE_MEMORY_LAYERS = 8
STYLE_MEMORY_HOURS = 24

_style_memory: dict = {}


def style_memory_dir() -> str:

    from .settings import account_dir

    path = os.path.join(account_dir(), "style_undo")
    os.makedirs(path, exist_ok=True)
    return path


def style_xml(layer) -> str:









    try:
        from qgis.PyQt.QtXml import QDomDocument

        document = QDomDocument()
        layer.exportNamedStyle(document)
        text = document.toString(2)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Style not kept: {exc}")
        return ""
    return text if text and text.strip() else ""


def remember_style(layer, xml: str) -> str:

    if not xml:
        return ""
    try:
        layer_id, name = str(layer.id()), str(layer.name())
    except Exception:  # noqa: BLE001
        return ""
    try:
        tag = hashlib.sha1(layer_id.encode("utf-8", "replace"), usedforsecurity=False).hexdigest()[:16]
        path = os.path.join(style_memory_dir(), f"{tag}.qml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(xml)
    except OSError as exc:
        log_warning(f"Previous style of {name!r} not kept: {exc}")
        return ""
    _style_memory.pop(layer_id, None)
    _style_memory[layer_id] = {"path": path, "layer_name": name, "at": time.time()}
    _prune_style_memory()
    return path


def remembered_style(layer_id: str) -> dict | None:

    entry = _style_memory.get(str(layer_id or ""))
    if entry and os.path.isfile(entry["path"]):
        return dict(entry)
    return None


def _prune_style_memory() -> None:





    while len(_style_memory) > STYLE_MEMORY_LAYERS:
        entry = _style_memory.pop(next(iter(_style_memory)))
        with contextlib.suppress(OSError):
            os.remove(entry["path"])
    kept = {entry["path"] for entry in _style_memory.values()}
    cutoff = time.time() - STYLE_MEMORY_HOURS * 3600
    try:
        folder = style_memory_dir()
        stale = [os.path.join(folder, name) for name in os.listdir(folder) if name.endswith(".qml")]
    except OSError:
        return
    for path in stale:
        if path in kept:
            continue
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:  # nosec B112
            continue


def forget_styles() -> None:

    for entry in list(_style_memory.values()):
        with contextlib.suppress(OSError):
            os.remove(entry["path"])
    _style_memory.clear()



def _layout_digests(rows) -> dict[str, str]:

    return {str(row[0]): str(row[1] or "") for row in rows or []
            if isinstance(row, (list, tuple)) and len(row) == 2}


def _remove_soon(path: str, what: str) -> bool:











    if not os.path.exists(path):
        return True
    gone = os.path.join(os.path.dirname(path), f".gone-{uuid.uuid4().hex[:12]}")
    try:
        os.rename(path, gone)
    except OSError:
        return _remove_tree(path, what)
    _jobs().schedule_job(lambda: _remove_tree(gone, what))
    return True


class RunSnapshot:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.captured = False
        self.captured_at = 0.0
        self.dir = os.path.join(snapshots_dir(), "".join(c for c in run_id if c.isalnum() or c in "-_")[:64])
        self.project_path = os.path.join(self.dir, "project.qgz")
        self.original_file = ""


        self.original_stamp: tuple | None = None
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


        self._sweep_jobs: dict[int, str] = {}
        self._not_copyable: set[str] = set()


        self._settled_source: dict[str, str] = {}

        self._real_paths: dict[str, str] = {}
        self.memory_features: dict[str, list] = {}


        self.memory_files: dict[str, tuple[str, int]] = {}



        self.unbacked_reasons: dict[str, str] = {}

        self.feature_signatures: dict[str, dict] = {}
        self._pass: _FeaturePass | None = None





        self._ready_callbacks: list = []



        self.quiet = False



    def to_record(self) -> dict:






        return {
            "run_id": self.run_id,
            "dir": self.dir,
            "captured": self.captured,
            "captured_at": self.captured_at,
            "quiet": self.quiet,
            "original_file": self.original_file,
            "original_stamp": list(self.original_stamp) if self.original_stamp else None,
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
        try:
            stamp = record.get("original_stamp")
            snap.original_stamp = tuple(int(x) for x in stamp) if isinstance(stamp, (list, tuple)) else None
        except (TypeError, ValueError):
            snap.original_stamp = None
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
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Snapshot features-ready callback failed: {exc}")

    def _note_uncopied(self, feature_pass) -> None:

        for lid, (name, reason) in list(feature_pass.unbacked.items()):
            self.unbacked[lid] = name
            self.unbacked_reasons[lid] = reason

    def wait_for_features(self, timeout: float = 2.0) -> bool:

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



                if not is_web_service(layer):
                    n = layer.featureCount()
                    record["feature_count"] = int(n) if n is not None and n >= 0 else None
            except Exception:  # nosec B110
                pass
            try:
                record["subset"] = str(layer.subsetString() or "")
            except Exception:  # nosec B110
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



        hold_snapshot(self.dir)
        self.original_file = project.fileName() or ""
        self.was_dirty = project.isDirty()
        ok = False



        self.quiet = False
        try:
            sections = _core_sections(project)
            with open(os.path.join(self.dir, SECTIONS_FILE), "wb") as fh:
                fh.write(sections)
            self.quiet = True
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Snapshot relations not kept apart, the copy is written with the project's signals: {exc}")
        with _signals_blocked(project) if self.quiet else contextlib.nullcontext():
            try:
                ok = bool(project.write(self.project_path))
                if not ok:


                    log_warning(f"Snapshot write failed: {project.error() or 'QGIS gave no reason'}")
            except Exception as exc:
                log_warning(f"Snapshot write failed: {exc}")
            finally:
                try:
                    project.setFileName(self.original_file)
                    project.setDirty(self.was_dirty)
                except Exception:  # nosec B110
                    pass
        if self.quiet:


            try:
                layer_sections = _layer_handler_sections(project)
                if layer_sections:
                    with open(os.path.join(self.dir, LAYER_SECTIONS_FILE), "wb") as fh:
                        fh.write(layer_sections)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Snapshot per-layer plugin sections not kept: {exc}")
        self.layers = {}
        self.layer_ids = set(project.mapLayers().keys())
        self.memory_features = {}
        self.feature_signatures = {}

        self.original_stamp = _stamp(self.original_file) if self.original_file else None




        budget_s = capture_seconds()
        deadline = time.monotonic() + budget_s
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
            log(f"Snapshot over {budget_s:.1f} s: {light} layers recorded without their style.")

        for layer in unsaved_edit_layers(project):
            key = "edits:" + layer.id()
            self.unbacked[key] = str(layer.name() or layer.id())
            self.unbacked_reasons[key] = REASON_UNSAVED_EDITS
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
        except Exception:  # noqa: BLE001
            disk_mb = 0.0
        _jobs().schedule_job(lambda: prune_snapshots(protect=self.dir, base=base,
                                                     index_dir=index_dir, disk_mb=disk_mb))
        return ok



    def backup_layer_files(self, layer) -> bool:











        lid = layer.id()
        if lid in self.backups:
            return True
        name = ""
        try:
            name = str(layer.name() or "")
        except Exception:  # noqa: BLE001  # nosec B110
            pass
        path = layer_file_path(layer)
        if not path:
            try:
                memory = layer.providerType() == "memory"
            except Exception:  # noqa: BLE001
                memory = False
            if memory:




                return lid not in self.unbacked
            try:
                definition_only = str(layer.providerType() or "").lower() in _DEFINITION_ONLY_PROVIDERS
            except Exception:  # noqa: BLE001
                definition_only = False
            if definition_only:



                return True

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




        tag = hashlib.sha1(lid.encode("utf-8", "replace"), usedforsecurity=False).hexdigest()[:8]
        folder = os.path.join(self.dir, "backup", f"{lid[:31]}_{tag}")
        copies = _backup_copies(folder, _sidecars(path))
        self.backups[lid] = copies
        self.path_backups[canonical] = copies
        self._plan_copies(folder, copies, name or os.path.basename(path))

        self.unbacked.pop(lid, None)
        return True

    def backup_targets(self, args: dict) -> int:
        count = 0
        for layer in resolve_layers(args if isinstance(args, dict) else {}):
            if self.backup_layer_files(layer):
                count += 1
        return count

    def backup_files(self, paths) -> int:

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


        total = 0
        for src, _dst in copies:
            try:
                total += _backup_size(src)
            except OSError:
                continue
        if total <= INLINE_BACKUP_BYTES:
            reason = _copy_files(folder, copies, label)
            if reason:
                raise OSError(self._copy_failed(copies, label, reason))
            return
        self.pending_copies.append((folder, copies, label))

    def _copy_failed(self, copies: list, label: str, reason: str) -> str:






        for table in (self.backups, self.path_backups):
            for key in [key for key, value in table.items() if value is copies]:
                del table[key]
        for key in [key for key, record in self.file_backups.items() if record.get("copies") is copies]:
            del self.file_backups[key]
        return f"Could not back up {label}: {reason}"

    def has_pending_copies(self) -> bool:
        return bool(self.pending_copies)

    def run_pending_copies(self) -> int:

        jobs, self.pending_copies = self.pending_copies, []
        done, refusals = 0, []
        for folder, copies, label in jobs:
            reason = _copy_files(folder, copies, label)
            swept = self._sweep_jobs.pop(id(copies), None)
            if reason and swept is not None:
                self._copy_failed(copies, label, reason)
                self._not_copied(swept, label, reason)
            elif reason:
                refusals.append(self._copy_failed(copies, label, reason))
            else:
                done += 1
        if refusals:
            raise OSError("; ".join(refusals))
        return done

    def backup_project_files(self) -> int:













        count = 0
        for layer in list(QgsProject.instance().mapLayers().values()):
            try:
                lid = layer.id()
                source = layer.source()
                if lid in self._not_copyable and lid not in self.backups:
                    if self._settled_source.get(lid) == source:
                        continue
                    self._not_copyable.discard(lid)
                self._settled_source[lid] = source
                if not layer_file_path(layer):
                    continue
            except Exception:  # noqa: BLE001  # nosec B112
                continue
            queued = len(self.pending_copies)
            try:
                backed = self.backup_layer_files(layer)
            except OSError as exc:

                self._not_copied(lid, str(layer.name() or lid), str(exc).split(": ", 1)[-1])
                continue
            for _folder, copies, _label in self.pending_copies[queued:]:
                self._sweep_jobs[id(copies)] = lid
            if backed:
                count += 1
            elif lid in self.unbacked:



                self._not_copyable.add(lid)
        return count

    def real_path(self, path: str) -> str:






        real = self._real_paths.get(path)
        if real is None:
            real = self._real_paths[path] = os.path.realpath(path)
        return real

    def copied_files(self) -> set[str]:

        return {*self.file_backups,
                *(self.real_path(src) for copies in self.backups.values() for src, _dst in copies)}

    def _not_copied(self, lid: str, name: str, reason: str) -> None:
        self.backups.pop(lid, None)
        self._not_copyable.add(lid)
        self.unbacked[lid] = name
        self.unbacked_reasons[lid] = f"its file could not be copied ({reason})"



    def diff(self) -> dict:
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        started = time.monotonic()
        after: dict[str, dict] = {}
        diff_s = tuning.ceiling("snapshot_max_diff_seconds", MAX_DIFF_SECONDS, 0.05)
        deadline = started + diff_s
        for lid, layer in project.mapLayers().items():
            try:
                after[lid] = self._record(layer, with_hash=True)
                after[lid]["in_tree"] = root.findLayer(lid) is not None
                after[lid]["memory"] = layer.providerType() == "memory"
            except Exception:  # nosec B112
                continue
        before = self.layers



        known = self.layer_ids or set(before)
        added = [{"name": r["name"], "id": lid} for lid, r in after.items() if lid not in known]
        removed = [{"name": (before.get(lid) or {}).get("name") or lid, "id": lid}
                   for lid in known if lid not in after]
        counts, crs_changes, files_changed, style_changes, renames = [], [], [], [], []
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


                now = feature_signatures(layers_now.get(lid), signature_total(), deadline)
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



            if b.get("name") != a.get("name"):
                renames.append({"name": a["name"], "id": lid, "before": b.get("name")})
                changed.add(lid)
        orphans = [{"name": r["name"], "id": lid} for lid, r in after.items()
                   if lid not in before and r.get("memory") and not r.get("in_tree")]
        visibility_changes, project_changes = self._project_diff(_project_state(project), after)
        for entry in visibility_changes:
            changed.add(entry["id"])
        spent = time.monotonic() - started
        if spent > diff_s:
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
            "name_changes": renames,
            "visibility_changes": visibility_changes,
            "project_changes": project_changes,
            "orphan_temporary_layers": orphans,
            "changed_layers": len(changed),
            "snapshot_available": self.captured,
        }
        self.last_diff = result
        return result

    def _project_diff(self, now: dict, after: dict) -> tuple[list, list]:



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

        if "layouts" in then and "layouts" in now:
            marks_then, marks_now = _layout_digests(then["layouts"]), _layout_digests(now["layouts"])
            added = [name for name in marks_now if name not in marks_then]
            removed = [name for name in marks_then if name not in marks_now]
            changed = [name for name, digest in marks_now.items()
                       if digest and marks_then.get(name) and marks_then[name] != digest]
            if added or removed or changed:
                project.append({"what": "layouts", "added": added, "removed": removed, "changed": changed})
        return visibility, project



    def restore(self, file_name: str | None = None, extra_copies=()) -> dict:































        if not self.captured or not os.path.isfile(self.project_path):
            return {"ok": False, "project_read": False, "message": "No snapshot to restore."}
        with _canvas_held():
            writing = self._copies_still_writing(RESTORE_COPY_WAIT_S)
            if writing:
                named = ", ".join(writing[:3]) + (f" and {len(writing) - 3} more" if len(writing) > 3 else "")
                log_warning(f"Restore of run {self.run_id[:8]} refused: still copying {named}")
                return {"ok": False, "refused": True, "project_read": False, "still_saving": writing,
                        "message": f"Still saving {named}; it finishes within seconds."}
            result = self._restore_now(file_name, extra_copies)
        _refresh_canvas()
        return result

    def _copies_still_writing(self, timeout: float) -> list:






        feature_pass = self._pass
        if feature_pass is None or feature_pass.done:
            return []
        if not feature_pass.wait_copies(timeout):
            names = list(dict(feature_pass.memory_planned).values())
            return [str(name) for name in names] or ["a temporary layer"]
        self.wait_for_features(0.0)
        return []

    @staticmethod
    def _path_key(path: str) -> str:






        try:
            return os.path.normcase(os.path.realpath(path))
        except (OSError, ValueError):
            return os.path.normcase(os.path.normpath(path))

    def _same_as_saved(self, file_name: str) -> bool:

        if self.was_dirty or not file_name or not self.original_file or not self.original_stamp:
            return False
        if self._path_key(file_name) != self._path_key(self.original_file):
            return False
        return _stamp(self.original_file) == tuple(self.original_stamp)

    def data_groups(self, extra_copies=()) -> list:





        groups, seen = [], set()
        sources = [*(record.get("copies") or [] for record in self.file_backups.values()), *self.backups.values(),
                   *([pair] for pair in (extra_copies or ()))]
        for copies in sources:
            pairs = [(src, dst) for src, dst in copies or [] if not _is_project_file(src)]
            if not pairs or self._path_key(pairs[0][0]) in seen:
                continue
            seen.add(self._path_key(pairs[0][0]))
            groups.append(pairs)
        return groups

    def keep_files_restored_by(self, other: RunSnapshot, extra_copies=()) -> int:









        paths = []
        for pairs in other.data_groups(extra_copies):
            if any(os.path.isfile(src) and not _same_content(dst, src) for src, dst in pairs):
                paths.append(pairs[0][0])
        if not paths:
            return 0
        count = self.backup_files(paths)
        self.run_pending_copies()
        return count

    def _groups_to_put_back(self, extra_copies=()) -> list[list]:







        groups: list[list] = []
        for path, record in self.file_backups.items():
            if _is_project_file(path):

                continue
            copies = record["copies"]
            if os.path.isfile(path) and _unchanged(path, record) and (
                    len(copies) < 2 or _copies_unchanged(copies)):
                continue
            groups.append(list(copies))
        for lid, copies in self.backups.items():
            record = self.layers.get(lid) or {}
            path = record.get("path")
            if path and os.path.isfile(path) and _unchanged(path, record) and (
                    len(copies) < 2 or _copies_unchanged(copies)):
                continue
            groups.append(list(copies))


        by_stem: dict[str, list] = {}
        for src, dst in extra_copies or ():
            by_stem.setdefault(os.path.splitext(self._path_key(src))[0], []).append((src, dst))
        groups.extend(by_stem.values())
        return groups

    def unchanged_since_capture(self) -> bool:










        if not self.captured or self.was_dirty:
            return False
        project = QgsProject.instance()
        try:
            if project.isDirty():
                return False
            layers = project.mapLayers()
            if set(layers) != self.layer_ids or set(self.layers) != self.layer_ids:
                return False
            for lid, layer in layers.items():
                if layer.providerType() == "memory" or (isinstance(layer, QgsVectorLayer) and layer.isEditable()):
                    return False
                before, now = self.layers[lid], self._record(layer, with_hash=True)
                if before.get("style") is None or any(before.get(key) != now.get(key) for key in (
                        "name", "source", "provider", "crs", "feature_count", "stamp", "style", "subset")):
                    return False
            if _project_state(project) != self.project_state:
                return False
            return not self._groups_to_put_back()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Snapshot cannot tell whether the project changed: {exc}")
            return False

    def _restore_now(self, file_name: str | None = None, extra_copies=()) -> dict:
        project = QgsProject.instance()
        for layer in list(project.mapLayers().values()):
            try:
                if isinstance(layer, QgsVectorLayer) and layer.isEditable():
                    layer.rollBack()
            except Exception:  # nosec B110
                pass

        source = self._source_to_read(project)
        kept = self.original_file if file_name is None else file_name

        saved = self._same_as_saved(kept)




        restoring = {self._path_key(src) for record in self.file_backups.values() for src, _dst in record["copies"]}
        restoring.update(self._path_key(src) for copies in self.backups.values() for src, _dst in copies)
        restoring.update(self._path_key(src) for src, _dst in (extra_copies or ()))
        for layer in list(project.mapLayers().values()):
            file_part = (layer.source() or "").split("|", 1)[0]
            if file_part and self._path_key(file_part) in restoring:
                release_pooled_handles(layer)


        designers = designer_guard.open_layouts()
        project.clear()
        files_restored = []


        files_put_back = []
        file_restore_errors = []
        groups = self._groups_to_put_back(extra_copies)
        names = {self._path_key(record["path"]): str(record.get("name") or "")
                 for record in self.layers.values() if isinstance(record, dict) and record.get("path")}
        tried: set[str] = set()
        for group in groups:


            pairs = [(src, dst) for src, dst in group if src not in tried and not _is_project_file(src)]
            if not pairs:
                continue
            tried.update(src for src, _dst in pairs)
            try:
                same = [_same_content(dst, src) for src, dst in pairs]
                restore_group(pairs)
            except (OSError, sqlite3.Error) as exc:
                main = pairs[0][0]
                layer = names.get(self._path_key(main)) or os.path.basename(main)
                log_warning(f"Could not restore {layer} ({len(pairs)} file(s), left as they were): {exc}")
                file_restore_errors.extend({"path": src, "layer": layer, "error": str(exc)} for src, _dst in pairs)
                continue
            for (src, _dst), unchanged in zip(pairs, same):
                if not unchanged:
                    files_put_back.append(src)
                files_restored.append(src)
        project_ok = False
        try:
            with read_back():
                project_ok = bool(project.read(source))
        except Exception as exc:
            log_warning(f"Snapshot read failed: {exc}")
        finally:
            if source != self.project_path:
                try:
                    os.remove(source)
                except OSError as exc:
                    log_warning(f"Restore copy not removed ({source}): {exc}")
        if project_ok:
            designer_guard.reopen(designers)
        try:
            project.setFileName(kept)

            project.setDirty(not (project_ok and saved))
        except Exception:  # nosec B110
            pass
        refilled = 0
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
        if project_ok:
            self._restamp()
        log(f"Snapshot restored for run {self.run_id[:8]}: project={'ok' if project_ok else 'failed'}, "
            f"files={len(files_restored)}, file errors={len(file_restore_errors)}, "
            f"memory layers refilled={refilled}, memory layers incomplete={len(refill_errors)}")
        if not project_ok and file_restore_errors:
            message = (f"The snapshot project and {len(file_restore_errors)} file(s) "
                       "could not be restored.")
        elif not project_ok:
            message = "The snapshot project could not be read."
        else:


            problems = []
            if file_restore_errors:

                layers = list(dict.fromkeys(str(e.get("layer") or os.path.basename(e["path"]))
                                            for e in file_restore_errors))
                named = ", ".join(layers[:3])
                if len(layers) > 3:
                    named += f" and {len(layers) - 3} more"
                problems.append(f"the files of {named} could not be put back and were left as they were")
            if refill_errors:
                named = ", ".join(refill_errors[:3])
                if len(refill_errors) > 3:
                    named += f" and {len(refill_errors) - 3} more"
                problems.append(f"{named} did not take all their features back")
            message = ("Project restored, but " + "; ".join(problems) + ".") if problems else "Project restored."
        layer_count = len(project.mapLayers()) if project_ok else 0
        return {"ok": ok, "project_read": project_ok, "files_restored": files_restored,
                "files_put_back": files_put_back, "layers": layer_count,
                "file_restore_errors": file_restore_errors,
                "memory_layers_refilled": refilled,
                "memory_layers_incomplete": refill_errors, "message": message}

    def _restamp(self) -> None:















        for lid, record in self.layers.items():
            path = record.get("path") if isinstance(record, dict) else None
            if path and record.get("stamp"):
                stamp = _stamp(path)
                if stamp != tuple(record.get("stamp") or ()):
                    self.feature_signatures.pop(lid, None)
                record["stamp"] = stamp
                record["size"] = stamp[0] if stamp else None
        for path, record in self.file_backups.items():
            if record.get("stamp"):
                record["stamp"] = _stamp(path)

    def file_groups(self) -> dict[str, list[tuple[str, str]]]:





        pending = {dst for _folder, copies, _label in self.pending_copies for _src, dst in copies}
        groups: dict[str, list[tuple[str, str]]] = {}
        sources = [*self.backups.values(), *(record.get("copies") or [] for record in self.file_backups.values())]
        for copies in sources:
            pairs = [(src, dst) for src, dst in copies or []]
            if not pairs or any(dst in pending or not os.path.isfile(dst) for _src, dst in pairs):
                continue
            groups.setdefault(self._path_key(pairs[0][0]), pairs)
        return groups

    def keep_files_of(self, other: RunSnapshot) -> int:







        paths = [pairs[0][0] for pairs in other.file_groups().values()]
        paths = [path for path in paths if os.path.splitext(path)[1].lower() not in (".qgs", ".qgz")]
        if not paths:
            return 0
        try:
            count = self.backup_files(paths)
            self.run_pending_copies()
        except OSError as exc:
            log_warning(f"After-run copy of the run's files failed: {exc}")
            return 0
        return count

    def _source_to_read(self, project) -> str:









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
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Snapshot sections not put back, the copy is read as it is: {exc}")
        return self.project_path

    def _name_unrefilled(self, project, refill_errors: list, handled: set) -> None:






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
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Could not set the filter of memory layer {layer.name()} again: {exc}")
                accepted = False
            if accepted is False:
                log_warning(f"QGIS refused the filter of memory layer {layer.name()}: {subset}")
                refill_errors.append(f"{layer.name()} (filter not restored)")

    def _refill_from_copies(self, project, refill_errors: list, handled: set) -> int:








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
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Could not refill memory layer {name}: {exc}")
                refill_errors.append(name)
                continue
            if got < count:
                refill_errors.append(f"{name} ({max(got, 0)}/{count})")
            else:
                refilled += 1
        return refilled

    def discard(self) -> bool:











        forget_copies(self.dir)
        release_snapshot(self.dir)
        feature_pass = self._pass
        if feature_pass is not None and not feature_pass.done:
            def remove_now() -> None:
                forget_copies(self.dir)
                _remove_soon(self.dir, "Snapshot")

            self._ready_callbacks.append(remove_now)
            return False
        return _remove_soon(self.dir, "Snapshot")
