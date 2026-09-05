# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later















































from __future__ import annotations

from qgis.core import QgsLayerTree, QgsProject

from .layer_order import is_backdrop, reading_back
from .logger import log_warning


KEEP_TOOLS = frozenset({
    "set_layer_style", "set_layer_symbology", "set_layer_labels", "zoom_to_layer", "export_layer",
    "export_3d_model",
    "save_layer_to_gpkg", "add_layout_map", "add_layout_legend", "export_layout",
    "create_print_layout", "set_layer_labels_advanced",


    "set_raster_style", "set_raster_class_style", "apply_style_qml",
})

VISIBILITY_TOOLS = frozenset({"set_layer_visibility", "set_layers_visibility"})

RENDER_TOOLS = frozenset({"render_map"})


MIN_TO_OFFER = 3


MAX_REPORTED = 60



DERIVING_TOOLS = frozenset({
    "run_processing", "execute_processing_batch", "run_model", "duplicate_layer",
    "raster_calculator", "create_hillshade",
    "geocode_layer",
})

_TASK_DONE = frozenset({"complete", "completed", "done", "finished", "success"})
_TASK_OVER = frozenset({"error", "failed", "canceled", "cancelled"})

TASK_READS = frozenset({"get_task_status", "list_tasks", "cancel_task"})


def _strings(value, out: list) -> None:

    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            _strings(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _strings(item, out)


def _layer_of(layer):

    try:
        return str(layer.id()), str(layer.name())
    except (RuntimeError, AttributeError):
        return None


def _named_ids(value, out: set, depth: int = 0) -> None:

    if depth > 6:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "layer_id" and isinstance(item, str) and item:
                out.add(item)
            elif key == "layer_ids" and isinstance(item, (list, tuple)):
                out.update(lid for lid in item if isinstance(lid, str) and lid)
            else:
                _named_ids(item, out, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _named_ids(item, out, depth + 1)


def _started_task(result) -> bool:

    return (isinstance(result, dict) and bool(result.get("task_id"))
            and str(result.get("status") or "").lower() == "running")


class ScratchLedger:








    def __init__(self):
        self._project = None
        self._order: list[str] = []
        self._names: dict[str, str] = {}
        self._kept: set[str] = set()
        self._last_render: set[str] = set()
        self._made_by: dict[str, str] = {}

        self._pending: dict[str, tuple[str, set, int]] = {}

        self._windows: dict[str, list] = {}

        self._fed: dict[str, list[tuple[str, ...]]] = {}

        self._seen_drawn: dict[str, bool] = {}
        self._switched_off: set[str] = set()
        self.last_report: list[dict] = []



    def begin(self) -> None:
        self.end()
        self._order, self._names = [], {}
        self._kept, self._last_render = set(), set()
        self._made_by, self._pending, self._windows = {}, {}, {}
        self._fed, self._seen_drawn, self._switched_off = {}, {}, set()
        self.last_report = []
        project = QgsProject.instance()
        if project is None:
            return
        try:
            project.layersAdded.connect(self._on_layers_added)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Scratch: cannot watch new layers: {exc}")
            return
        self._project = project

    def end(self) -> list[dict]:


        if self._project is not None:
            try:
                self._project.layersAdded.disconnect(self._on_layers_added)
            except (TypeError, RuntimeError):
                pass
            self._project = None
        self.last_report = self.report()
        return self.last_report

    @property
    def watching(self) -> bool:
        return self._project is not None

    def mark(self) -> int:

        return len(self._order)



    def _on_layers_added(self, layers) -> None:
        if reading_back():


            return
        for layer in layers or ():
            if is_backdrop(layer):



                continue
            pair = _layer_of(layer)
            if pair is None:
                continue
            layer_id, name = pair
            if layer_id in self._names:
                continue
            self._order.append(layer_id)
            self._names[layer_id] = name



    def note_call(self, name: str, args, mark, result=None) -> None:








        if not self._names and not _started_task(result):
            return
        try:
            self._note_call(str(name or ""), args, mark, result)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Scratch: {name} not accounted for: {exc}")

    def _note_call(self, name: str, args, mark, result=None) -> None:
        try:
            before = self._order[:int(mark)] if mark is not None else list(self._order)
        except (TypeError, ValueError):
            before = list(self._order)
        self._observe_tree()
        added = self._order[len(before):]
        for layer_id in added:
            self._made_by.setdefault(layer_id, name)
        self._settle_task(result)
        self._note_one(name, args, before, added, result)

    def _note_one(self, name: str, args, pool: list, made: list, result) -> None:




        if name in RENDER_TOOLS:




            drawn = self._referenced(args, list(self._order))
            if not drawn and isinstance(result, dict) and result.get("layers_rendered"):
                drawn = self._referenced({"layers_rendered": result["layers_rendered"]}, list(self._order))
            self._last_render = drawn or self._last_render
            return
        if name in VISIBILITY_TOOLS:

            visible = args.get("visible") if isinstance(args, dict) else None
            if visible is not False:
                self._kept.update(self._referenced(args, list(self._order)))
            return
        if name in KEEP_TOOLS:
            self._kept.update(self._referenced(args, list(self._order)))
            return
        referenced = self._referenced(args, pool)
        if made and referenced:
            self._consume(referenced, made)
        elif _started_task(result) and name not in TASK_READS:




            task_id = str(result["task_id"])
            self._pending.setdefault(task_id, (name, referenced, len(self._order)))
            self._windows.setdefault(task_id, [len(self._order), None])

    def _consume(self, inputs, outputs) -> None:

        if not inputs:
            return
        for layer_id in inputs:
            self._fed.setdefault(layer_id, []).append(tuple(outputs))

    def _settle_task(self, result) -> None:

        if not self._pending or not isinstance(result, dict):
            return
        task_id = str(result.get("task_id") or "")
        if task_id not in self._pending:
            return
        status = str(result.get("status") or "").lower()
        if status in _TASK_DONE:
            producer, inputs, start = self._pending.pop(task_id)
            made = self._task_made(task_id, start, result)
            for layer_id in made:
                self._made_by[layer_id] = producer
            self._consume(inputs, made)
        if status in _TASK_DONE or status in _TASK_OVER:
            self._pending.pop(task_id, None)
            if task_id in self._windows:
                self._windows[task_id][1] = len(self._order)

    def _task_made(self, task_id: str, start: int, result) -> list:









        since = self._order[start:]
        named: set = set()
        _named_ids(result, named)
        own = [layer_id for layer_id in since if layer_id in named]
        if own:
            return own
        others = [window for other, window in self._windows.items() if other != task_id]
        return [layer_id for index, layer_id in enumerate(since, start)
                if layer_id not in self._made_by
                and not any(begun <= index and (ended is None or index < ended) for begun, ended in others)]

    def _observe_tree(self) -> None:








        project = QgsProject.instance()
        try:
            root = project.layerTreeRoot() if project is not None else None
        except Exception:  # noqa: BLE001
            return
        if root is None:
            return
        try:
            added_checked = bool(project.layerTreeRegistryBridge().newLayersVisible())
        except Exception:  # noqa: BLE001
            added_checked = True
        for layer_id in self._order:
            try:
                node = root.findLayer(layer_id)
                if node is None:
                    continue
                drawn, checked = bool(node.isVisible()), bool(node.itemVisibilityChecked())
            except Exception:  # noqa: BLE001  # nosec B112
                continue
            seen = self._seen_drawn.get(layer_id)
            if drawn:
                self._switched_off.discard(layer_id)
            elif seen or (seen is None and not checked and added_checked):
                self._switched_off.add(layer_id)
            self._seen_drawn[layer_id] = drawn

    def _referenced(self, args, pool: list) -> set:











        if not pool:
            return set()
        texts = []
        _strings(args, texts)
        wanted = {t.strip() for t in texts if t and len(t.strip()) >= 3}
        if not wanted:
            return set()
        project = QgsProject.instance()
        live = project.mapLayers() if project is not None else {}
        found = set()
        for layer_id in pool:
            names = {self._names.get(layer_id, "")}
            pair = _layer_of(live[layer_id]) if layer_id in live else None
            if pair is not None:
                names.add(pair[1])
            if layer_id in wanted or any(name and name in wanted for name in names):
                found.add(layer_id)
        return found



    def report(self) -> list[dict]:










        project = QgsProject.instance()
        if project is None or len(self._order) <= 1:
            return []
        try:
            live = project.mapLayers()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Scratch: no project to report on: {exc}")
            return []
        present = [layer_id for layer_id in self._order if layer_id in live]
        out = []


        for layer_id in present[:-1]:
            if layer_id in self._kept or layer_id in self._last_render:
                continue
            if layer_id not in self._switched_off and not self._feeds_a_live_layer(layer_id, live, set()):
                continue
            pair = _layer_of(live[layer_id])
            if pair is None:
                continue
            out.append({"layer_id": pair[0], "name": pair[1]})
            if len(out) >= MAX_REPORTED:
                break
        return out

    def working_copies(self) -> list[dict]:









        project = QgsProject.instance()
        if project is None or len(self._order) <= 1:
            return []
        try:
            live = project.mapLayers()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Scratch: no project to report on: {exc}")
            return []
        present = [layer_id for layer_id in self._order if layer_id in live]
        out = []
        for layer_id in present[:-1]:
            if self._made_by.get(layer_id) not in DERIVING_TOOLS:
                continue
            if layer_id in self._kept or layer_id in self._last_render:
                continue
            if not self._feeds_a_live_layer(layer_id, live, set()):
                continue
            pair = _layer_of(live[layer_id])
            if pair is None:
                continue
            out.append({"layer_id": pair[0], "name": pair[1]})
            if len(out) >= MAX_REPORTED:
                break
        return out

    def _feeds_a_live_layer(self, layer_id: str, live, seen: set) -> bool:

        if layer_id in seen:
            return False
        seen.add(layer_id)
        return any(output in live or self._feeds_a_live_layer(output, live, seen)
                   for outputs in self._fed.get(layer_id, ()) for output in outputs)

    def hidden_ids(self) -> set[str]:

        return set(self._switched_off)


def remove(layer_ids) -> list:





    from ..tools.processing_run import remove_layers

    return remove_layers([str(layer_id) for layer_id in layer_ids or ()])


def group(layer_ids, title: str) -> int:







    project = QgsProject.instance()
    if project is None:
        return 0
    try:
        root = project.layerTreeRoot()
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Scratch: no layer tree to group in: {exc}")
        return 0
    if root is None:
        return 0
    moved = 0
    holder = None
    for layer_id in list(layer_ids or ()):
        node = root.findLayer(str(layer_id))
        if node is None:
            continue
        if holder is None:


            holder = next((child for child in root.children()
                           if QgsLayerTree.isGroup(child) and child.name() == title), None)
            holder = holder or root.addGroup(title)
            if holder is None:
                return 0
        try:
            clone = node.clone()
            holder.addChildNode(clone)
            parent = node.parent()
            if parent is not None:
                parent.removeChildNode(node)
            clone.setItemVisibilityChecked(False)
            moved += 1
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Scratch: {layer_id} not grouped: {exc}")
    if holder is not None:
        holder.setExpanded(False)
        holder.setItemVisibilityChecked(False)
    return moved
