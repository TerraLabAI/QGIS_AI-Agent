# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


























from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import time
import uuid

from qgis.PyQt.QtCore import QCoreApplication, QTimer

from .host_platform import remove_quietly, remove_tree, retry_file_op
from .logger import log_warning

DEBOUNCE_MS = 2000
KEEP_DAYS = 7
MANIFEST = "manifest.json"
GROUP_NAME = "Recovered after QGIS closed"
_VERSION = 1
_FIELD = re.compile(r"^a(\d+)$")


def tr(text: str) -> str:
    return QCoreApplication.translate("AIAgentRecovery", text)


def _root() -> str:
    from .policy import AGENT_HOME

    return os.path.join(AGENT_HOME, "recovery")


def _own_folder() -> str:
    return os.path.join(_root(), str(os.getpid()))


class _State:
    def __init__(self):
        self.timer = None
        self.writing = False
        self.dirty = False
        self.run_id = ""
        self.thread_id = ""
        self.stopped = False
        self.generation = 0


_STATE = _State()
_WATCHED: set = set()


def _editing_stopped() -> None:
    note_change()


def note_change(run_id: str = "", thread_id: str = "") -> None:

    state = _STATE
    if state.stopped:
        return
    if run_id:
        state.run_id = run_id
    if thread_id:
        state.thread_id = thread_id
    if state.timer is None:
        state.timer = QTimer()
        state.timer.setSingleShot(True)
        state.timer.timeout.connect(_pass)
    state.timer.start(DEBOUNCE_MS)


def _ceiling():
    try:
        from . import limits

        return int(limits.current("MAX_FEATURES_MATERIALISED"))
    except Exception:  # noqa: BLE001
        return None


def _plan(project) -> list:

    from qgis.core import QgsVectorLayer, QgsVectorLayerFeatureSource, QgsWkbTypes

    from .layer_order import is_remote_vector

    ceiling = _ceiling()
    budget = ceiling
    jobs = []
    for lid, layer in project.mapLayers().items():
        try:
            if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
                continue
            memory = layer.providerType() == "memory"
            buffered = layer.isEditable() and layer.isModified()
            if not memory and not buffered:
                continue
            if not memory and is_remote_vector(layer):
                continue
            if buffered and lid not in _WATCHED:

                layer.editingStopped.connect(_editing_stopped)
                _WATCHED.add(lid)
            count = int(layer.featureCount())
            if count <= 0 and not buffered:
                continue
            if budget is not None and count > budget:
                log_warning(f"Recovery copy skips {layer.name()}: {count} features")
                continue
            if budget is not None:
                budget -= max(count, 0)
            fields = layer.fields()
            jobs.append({
                "id": lid, "name": layer.name(), "memory": memory, "buffered": buffered,
                "provider": layer.providerType(), "count": count,
                "wkb": QgsWkbTypes.displayString(layer.wkbType()),
                "crs": layer.crs().toWkt(), "fields": [fields.at(i).name() for i in range(fields.count())],
                "table": "m_" + uuid.uuid4().hex[:20],
                "_source": QgsVectorLayerFeatureSource(layer), "_fields": fields,
            })
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Recovery copy cannot plan a layer: {exc}")
    return jobs


def _pass() -> None:

    state = _STATE
    if state.stopped:
        return
    if state.writing:
        state.dirty = True
        return
    from . import project_read

    if project_read.reading():
        project_read.when_done(lambda: note_change())
        return
    try:
        from qgis.core import QgsProject

        project = QgsProject.instance()
        jobs = _plan(project)
        project_file = project.fileName() or ""
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Recovery copy not planned: {exc}")
        return
    folder = _own_folder()
    if not jobs:
        _drop_own()
        return
    from . import background

    state.writing = True
    generation = state.generation
    meta = {"version": _VERSION, "pid": os.getpid(), "project_file": project_file,
            "run_id": state.run_id, "thread_id": state.thread_id, "time": time.time()}
    background.start_kept_thread(_write, folder, jobs, meta, generation, name="ai-agent-recovery")


class _Sink:


    def __init__(self):
        self.unbacked = {}
        self.memory_files = {}
        self.memory_planned = {}


def _write(folder: str, jobs: list, meta: dict, generation: int) -> None:

    from .security import fits_path
    from .snapshot_features import _FeaturePass

    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"copy-{uuid.uuid4().hex}.gpkg")
        if not fits_path(path, 10):
            log_warning("Recovery copy not written: the folder path is too long")
            return
        sink = _Sink()
        kept = []
        for job in jobs:
            if _STATE.stopped:
                return
            _FeaturePass._write_copy(sink, job["id"], job["name"], job["_source"], job["_fields"],
                                     job["count"], job["table"], path)
            if job["id"] in sink.memory_files:
                kept.append({k: v for k, v in job.items() if not k.startswith("_")})
        old = _read_manifest(folder)
        if not kept:
            return
        meta = dict(meta, file=os.path.basename(path), layers=kept)
        staged = os.path.join(folder, f".{MANIFEST}.{uuid.uuid4().hex}.tmp")
        with open(staged, "w", encoding="utf-8") as handle:
            json.dump(meta, handle)
        retry_file_op(os.replace, staged, os.path.join(folder, MANIFEST))
        if old and old.get("file") and old["file"] != meta["file"]:
            for suffix in ("", "-wal", "-shm", "-journal"):
                remove_quietly(os.path.join(folder, old["file"] + suffix))
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Recovery copy not written: {exc}")
    finally:
        jobs.clear()
        from . import background

        with contextlib.suppress(Exception):
            background.main_thread_invoker().invoke(lambda: _written(generation))


def _written(generation: int) -> None:
    state = _STATE
    state.writing = False
    if generation != state.generation:
        _drop_own()
    if state.dirty and not state.stopped:
        state.dirty = False
        _pass()


def _read_manifest(folder: str):
    try:
        with open(os.path.join(folder, MANIFEST), encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) and data.get("version") == _VERSION else None
    except (OSError, ValueError):
        return None


def _drop_own() -> None:
    folder = _own_folder()
    if os.path.isdir(folder) and not _STATE.writing:
        remove_tree(folder)


def project_cleared() -> None:

    _STATE.generation += 1
    _STATE.dirty = False
    if _STATE.timer is not None:
        _STATE.timer.stop()
    _drop_own()


def shutdown() -> None:

    state = _STATE
    state.stopped = True
    if state.timer is not None:
        with contextlib.suppress(RuntimeError):
            state.timer.stop()
        state.timer = None
    folder = _own_folder()
    if os.path.isdir(folder):
        remove_tree(folder)





def _dead_folders() -> list:
    from .crash_note import pid_alive

    root = _root()
    try:
        names = os.listdir(root)
    except OSError:
        return []
    found = []
    for name in names:
        if not name.isdigit() or int(name) == os.getpid():
            continue
        folder = os.path.join(root, name)
        manifest = _read_manifest(folder)
        try:
            age = time.time() - (manifest or {}).get("time", os.path.getmtime(folder))
        except OSError:
            continue
        if age > KEEP_DAYS * 86400:
            remove_tree(folder)
            continue
        if pid_alive(int(name)):
            continue
        if manifest is None:
            remove_tree(folder)
            continue
        found.append((folder, manifest))
    return found


def _same_file(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _matches(manifest: dict, project) -> bool:
    wanted = manifest.get("project_file") or ""
    if wanted:
        return _same_file(wanted, project.fileName())

    return not project.fileName() and len(project.mapLayers()) <= 1


def offer(iface=None) -> int:

    if _STATE.stopped:
        return 0
    from . import project_read

    if project_read.reading():
        project_read.when_done(lambda: offer(iface))
        return 0
    try:
        from qgis.core import QgsProject

        project = QgsProject.instance()
        found = [(f, m) for f, m in _dead_folders() if _matches(m, project)]
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Recovery not read: {exc}")
        return 0
    total = expected = 0
    kept_in = ""
    for folder, manifest in found:
        claimed = _claim(folder)
        if not claimed:
            continue
        expected += len(manifest.get("layers") or [])
        given = _give_back(project, claimed, manifest)
        total += given
        if given < len(manifest.get("layers") or []):
            kept_in = claimed
    if iface is not None and expected:
        with contextlib.suppress(Exception):
            if total:
                iface.messageBar().pushInfo(
                    "AI Agent",
                    tr("QGIS closed during a run. {n} layer(s) of unsaved work came back "
                       'in the group "{group}". Save them to keep them.').format(n=total, group=tr(GROUP_NAME)))
            if kept_in:
                iface.messageBar().pushWarning(
                    "AI Agent", tr("Some unsaved work could not be put back. Its copy is kept in {folder}.")
                    .format(folder=kept_in))
    return total


def _claim(folder: str) -> str:

    from .security import fits_path

    base = _root()
    _prune_claimed(base)
    target = os.path.join(base, f"recovered-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}")
    if not fits_path(os.path.join(target, "copy-" + "0" * 32 + ".gpkg"), 10):
        return folder
    try:
        retry_file_op(os.replace, folder, target)
    except OSError:
        try:
            shutil.copytree(folder, target)
            remove_tree(folder)
        except OSError as exc:
            log_warning(f"Recovery folder not claimed: {exc}")
            return ""
    return target


def _prune_claimed(base: str) -> None:

    try:
        names = os.listdir(base)
    except OSError:
        return
    for name in names:
        path = os.path.join(base, name)
        with contextlib.suppress(OSError):
            if name.startswith("recovered-") and time.time() - os.path.getmtime(path) > KEEP_DAYS * 86400:
                remove_tree(path)


def _give_back(project, folder: str, manifest: dict) -> int:
    from qgis.core import (
        QgsCoordinateReferenceSystem,
        QgsFields,
        QgsMemoryProviderUtils,
        QgsVectorLayer,
        QgsWkbTypes,
    )

    from .snapshot_features import refill_from_copy

    path = os.path.join(folder, str(manifest.get("file") or ""))
    group = None
    given = 0
    for entry in manifest.get("layers") or []:
        name = str(entry.get("name") or "layer")
        table = str(entry.get("table") or "")
        try:
            same = project.mapLayer(str(entry.get("id") or ""))
            if (entry.get("memory") and isinstance(same, QgsVectorLayer) and same.providerType() == "memory"
                    and same.featureCount() == 0 and same.fields().names() == list(entry.get("fields") or [])):


                refill_from_copy(same, path, table)
                given += 1
                continue
            copy = QgsVectorLayer(f"{path}|layername={table}", "recovery copy", "ogr")
            if not copy.isValid():
                raise OSError("copy not readable")
            fields = QgsFields()
            names = list(entry.get("fields") or [])
            for field in copy.fields():
                hit = _FIELD.match(field.name())
                if hit and int(hit.group(1)) < len(names):
                    field.setName(names[int(hit.group(1))])
                    fields.append(field)
            del copy
            crs = QgsCoordinateReferenceSystem.fromWkt(str(entry.get("crs") or ""))
            label = tr("{name} (unsaved edits kept)").format(name=name)
            layer = QgsMemoryProviderUtils.createMemoryLayer(
                label, fields, QgsWkbTypes.parseType(str(entry.get("wkb") or "Unknown")), crs)
            refill_from_copy(layer, path, table)
            if group is None:
                group = project.layerTreeRoot().insertGroup(0, tr(GROUP_NAME))
            project.addMapLayer(layer, False)
            group.addLayer(layer)
            given += 1
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Recovery of {name} failed: {exc}")
    log_warning(f"Recovery: {given} layer(s) given back from {folder}")
    return given
