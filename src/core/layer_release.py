# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

import os
import time
import uuid

from qgis.core import (
    QgsApplication,
    QgsMapLayer,
    QgsProcessingUtils,
    QgsProject,
    QgsProperty,
    QgsTask,
    QgsTaskManager,
)

from . import background
from .host_platform import IS_WINDOWS, release_pooled_handles, remove_quietly, remove_tree
from .logger import log_warning
from .policy import AGENT_TMP_DIR
from .qt_compat import disconnect_quietly, enum_member, qt_deleted

PROCESSING_TASKS: dict[str, dict] = {}

CANCELED_AT_UNLOAD: dict[str, dict] = {}


def sweep_consumed_tasks():



    now = time.time()
    stale = [
        tid for tid, e in PROCESSING_TASKS.items()
        if e.get("_consumed") and (now - e.get("_consumed_at", now)) > 600 and not e.get("held_layers")
    ]
    for tid in stale:
        PROCESSING_TASKS.pop(tid, None)


def register_task(task, label: str, connect=None, after=None) -> tuple:














    sweep_consumed_tasks()
    task_id = "task-" + uuid.uuid4().hex[:12]
    entry = {
        "status": "running",
        "progress": 0,
        "algorithm": label,
        "started_at": time.strftime("%H:%M:%S"),
        "task": task,
    }
    PROCESSING_TASKS[task_id] = entry
    try:
        task.progressChanged.connect(lambda value, tid=task_id: on_proc_progress(tid, value))
    except (AttributeError, TypeError):
        pass
    try:
        task.taskTerminated.connect(lambda tid=task_id: on_proc_terminated(tid))
    except (AttributeError, TypeError):
        pass
    if connect is not None:
        connect(task_id, entry)
    if after is not None:
        QgsApplication.taskManager().addTask(QgsTaskManager.TaskDefinition(task, [after]))
    else:
        QgsApplication.taskManager().addTask(task)
    return task_id, entry


def adopt_running_call(task, label: str) -> tuple:




    sweep_consumed_tasks()
    task_id = "task-" + uuid.uuid4().hex[:12]
    entry = {"status": "running", "progress": 0, "algorithm": label,
             "started_at": time.strftime("%H:%M:%S"), "task": task}
    PROCESSING_TASKS[task_id] = entry
    return task_id, entry


def note_adopted_bytes(task_id: str, done, total) -> None:

    entry = PROCESSING_TASKS.get(task_id)
    if entry is not None and entry.get("status") == "running":
        entry["received_bytes"] = int(done or 0)
        if total:
            entry["total_bytes"] = int(total)


def settle_adopted(task_id: str, result, error_text: str) -> None:

    entry = PROCESSING_TASKS.get(task_id)
    if entry is None or entry.get("status") == "canceled":
        return
    entry["task"] = None
    if error_text or not isinstance(result, dict):
        entry.update(status="error", error=(error_text or "No result").split("\n", 1)[0])
    elif result.get("_error"):
        entry.update(status="error", error=result.get("_error"), code=result.get("_code") or result.get("code"),
                     hint=result.get("hint"))
    else:
        entry.update(status="complete", progress=100, outputs=result)


def cleanup_canceled_destination(entry: dict) -> None:














    for path in entry.get("destination_paths") or ():


        stem, extension = os.path.splitext(path)
        sidecars = [path + suffix for suffix in ("-wal", "-shm", "-journal")]
        if extension.lower() == ".shp":
            sidecars += [stem + suffix for suffix in (".shx", ".dbf", ".prj", ".cpg", ".qix")]
        for candidate in [path] + sidecars:
            if os.path.isfile(candidate) and not remove_quietly(candidate):
                log_warning(f"Partial output {candidate} not removed after cancel: another program holds it.")
    folder = entry.get("temporary_dir")
    if not isinstance(folder, str) or not folder:
        return
    try:
        real = os.path.realpath(folder)

        if (os.path.dirname(real) == os.path.realpath(AGENT_TMP_DIR)
                and os.path.basename(real).startswith("processing-") and os.path.isdir(real)):
            if remove_tree(real):
                log_warning(f"Partial temporary output {folder} not fully removed after cancel.")
    except OSError as exc:
        log_warning(f"Partial temporary output {folder} not removed after cancel: {exc}")


def canceled_output(path: str) -> bool:





    def key(value: str) -> str:
        return os.path.normcase(os.path.realpath(value))

    try:
        wanted = key(path)
        for entry in list(PROCESSING_TASKS.values()):
            if entry.get("status") != "canceled":
                continue
            if any(key(other) == wanted for other in entry.get("destination_paths") or ()):
                return True
            folder = entry.get("temporary_dir")
            if isinstance(folder, str) and folder and wanted.startswith(key(folder) + os.sep):
                return True
    except (OSError, TypeError, ValueError) as exc:
        log_warning(f"Cancelled output check failed for {path}: {exc}")
    return False


def on_proc_terminated(task_id: str):






    entry = PROCESSING_TASKS.get(task_id) or CANCELED_AT_UNLOAD.pop(task_id, None)
    if entry is None:
        return
    if entry.get("path_aliases") is not None:
        entry.pop("path_aliases").remove()
    if entry.get("status") == "canceled":




        cleanup_canceled_destination(entry)
        let_go(entry)
        return
    if entry.get("status") != "running":
        return
    feedback = entry.get("feedback")
    captured = "; ".join(getattr(feedback, "errors", [])[-5:]) if feedback else ""
    entry["status"] = "error"
    if captured:
        entry["error"] = f"The algorithm stopped before it produced anything: {captured}"
    else:
        entry["error"] = "The algorithm stopped before it produced anything."
        entry["hint"] = "async_run_stopped_silently"
    let_go(entry)


def on_proc_progress(task_id: str, progress: float):
    entry = PROCESSING_TASKS.get(task_id)
    if entry is not None and entry.get("status") == "running":
        entry["progress"] = int(progress)


def sync_with_qgis(task_id: str) -> None:














    entry = PROCESSING_TASKS.get(task_id)
    if entry is None or entry.get("status") != "running":
        return
    if entry.get("sequence") is not None:


        entry["sequence"].advance()
        return
    task = entry.get("task")
    if task is None:
        return
    terminated = enum_member(QgsTask, "TaskStatus", "Terminated", None)
    try:
        over = task.status() == terminated and not task.isActive()
    except (AttributeError, RuntimeError):
        over = True
    if over:
        on_proc_terminated(task_id)


def task_state(task_id: str):




    if task_id not in PROCESSING_TASKS:
        return None
    sync_with_qgis(task_id)
    entry = PROCESSING_TASKS.get(task_id)
    if entry is None:
        return None
    try:
        percent = int(round(float(entry.get("progress") or 0)))
    except (TypeError, ValueError):
        percent = None
    return str(entry.get("status") or ""), percent


def remove_layers(layers) -> list:











    project = QgsProject.instance()
    gone = []
    taken = False
    for item in layers or ():
        try:
            layer = project.mapLayer(item if isinstance(item, str) else item.id())
            if layer is None:
                continue
            name = layer.name()
            _unhook_layouts(project, layer)
            readers = readers_of(layer)
            if not readers:
                project.removeMapLayer(layer.id())
            else:
                held = project.takeMapLayer(layer)
                if held is None:
                    continue
                taken = True
                for entry in readers:
                    if not any(other is held for other in entry.get("held_layers") or ()):
                        entry.setdefault("held_layers", []).append(held)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"A layer was not removed from the project: {exc}")
            continue
        gone.append(name)
    if taken:
        _canvas_follows_tree()
    if gone:
        from .scratch import drop_emptied_group

        try:
            drop_emptied_group()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"The emptied Working layers group stayed: {exc}")
    return gone


def _unhook_layouts(project, layer) -> None:






    try:
        layouts = project.layoutManager().printLayouts()
    except Exception:  # noqa: BLE001
        return
    for layout in layouts:
        try:
            atlas = layout.atlas()
            if atlas is not None and atlas.coverageLayer() is layer:
                atlas.setEnabled(False)
                atlas.setCoverageLayer(None)
            for item in layout.items():
                if type(item).__name__ != "QgsLayoutItemElevationProfile":
                    continue
                listed = item.layers()
                kept = [other for other in listed if other is not None and other is not layer]
                if len(kept) != len(listed):
                    item.setLayers(kept)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"A layout kept a reference to a removed layer: {exc}")


def hold_on_removal(removed) -> None:






    project = QgsProject.instance()
    for item in removed or ():
        try:
            layer = project.mapLayer(item) if isinstance(item, str) else item
            if qt_deleted(layer):
                continue
            readers = readers_of(layer)
            if not readers:
                continue
            layer.setParent(None)
            for entry in readers:
                if not any(held is layer for held in entry.get("held_layers") or ()):
                    entry.setdefault("held_layers", []).append(layer)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"A removed layer was not kept for its task: {exc}")


_WATCHING = False


def watch_removals() -> None:

    global _WATCHING
    if _WATCHING:
        return
    QgsProject.instance().layersWillBeRemoved.connect(hold_on_removal)
    _WATCHING = True


def unwatch_if_idle() -> None:

    global _WATCHING
    if not _WATCHING or any(entry.get("task") is not None for entry in PROCESSING_TASKS.values()):
        return
    disconnect_quietly(QgsProject.instance(), "layersWillBeRemoved", hold_on_removal)
    _WATCHING = False


def _canvas_follows_tree() -> None:




    try:
        from qgis.utils import iface

        bridge = iface.layerTreeCanvasBridge() if iface is not None else None
        if bridge is not None:
            bridge.setCanvasLayers()
    except Exception as exc:  # noqa: BLE001
        log_warning(f"The map canvas was not updated after a layer left the project: {exc}")


def readers_of(layer) -> list:




    runs = [entry for entry in PROCESSING_TASKS.values()
            if entry.get("task") is not None and entry.get("parameters") and not entry.get("separate_qgis")]
    if not runs:
        return []
    names = {layer.id(), layer.name()}
    source = QgsProcessingUtils.normalizeLayerSource(layer.source())

    def names_it(value) -> bool:
        if isinstance(value, QgsMapLayer):
            return value is layer
        definition = getattr(value, "source", None)
        if isinstance(definition, QgsProperty):
            value = definition.staticValue()
        return isinstance(value, str) and bool(value) and (
            value in names or QgsProcessingUtils.normalizeLayerSource(value) == source)

    return [entry for entry in runs
            if any(names_it(item) for value in entry["parameters"].values()
                   for item in (value if isinstance(value, (list, tuple)) else [value]))]


def release_held(entry: dict) -> None:



    for layer in entry.pop("held_layers", None) or ():
        holders = list(PROCESSING_TASKS.values()) + list(CANCELED_AT_UNLOAD.values())
        if any(other is layer for holder in holders for other in holder.get("held_layers") or ()):
            continue
        try:
            if not qt_deleted(layer) and QgsProject.instance().mapLayer(layer.id()) is not layer:
                background.delete_here(layer)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"A removed layer was not deleted after its task: {exc}")


def let_go(entry: dict) -> None:


    for heavy in ("task", "context", "feedback", "alg", "parameters"):
        entry.pop(heavy, None)
    release_held(entry)
    unwatch_if_idle()


_CLEARING = False


_POOL_WATCHING = False


def _release_leaving(removed) -> None:















    if not IS_WINDOWS or _CLEARING:
        return
    try:
        from qgis.core import QgsProject

        project = QgsProject.instance()
        done: set = set()
        for item in removed or ():
            layer = project.mapLayer(item) if isinstance(item, str) else item
            if layer is None or layer.providerType() != "ogr" or not layer.isValid():
                continue
            path = (layer.source() or "").split("|", 1)[0]
            key = os.path.normcase(os.path.normpath(path)) if path else ""
            if (not key or key in done or path.startswith(("\\\\", "//", "/vsi")) or "://" in path
                    or not os.path.isabs(path) or layer.isEditable() or readers_of(layer)):
                continue
            done.add(key)
            release_pooled_handles(layer)
    except Exception:  # nosec B110
        pass


def _clearing(flag: bool):
    def set_flag(*_args) -> None:
        global _CLEARING
        _CLEARING = flag
    return set_flag


_CLEAR_START = _clearing(True)


_CLEAR_END = _clearing(False)


def watch_layer_removals() -> None:

    global _POOL_WATCHING
    if not IS_WINDOWS or _POOL_WATCHING:
        return
    try:
        from qgis.core import QgsProject

        project = QgsProject.instance()
        project.layersWillBeRemoved.connect(_release_leaving)
        if hasattr(project, "aboutToBeCleared"):
            project.aboutToBeCleared.connect(_CLEAR_START)
            project.cleared.connect(_CLEAR_END)
        _POOL_WATCHING = True
    except Exception:  # nosec B110
        pass


def stop_watching_layer_removals() -> None:

    global _POOL_WATCHING, _CLEARING
    if not _POOL_WATCHING:
        return
    try:
        from qgis.core import QgsProject

        project = QgsProject.instance()
        project.layersWillBeRemoved.disconnect(_release_leaving)
        if hasattr(project, "aboutToBeCleared"):
            project.aboutToBeCleared.disconnect(_CLEAR_START)
            project.cleared.disconnect(_CLEAR_END)
    except Exception:  # nosec B110
        pass
    _POOL_WATCHING = False
    _CLEARING = False
