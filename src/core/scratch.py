# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The layers a run made only in order to make another one."""












































from __future__ import annotations

from qgis.core import QgsProject

from .layer_order import is_backdrop
from .logger import log_warning


KEEP_TOOLS = frozenset({
    "set_layer_style", "set_layer_labels", "zoom_to_layer", "export_layer",
    "save_layer_to_gpkg", "add_layout_map", "add_layout_legend", "export_layout",
    "create_print_layout", "set_layer_labels_advanced",
})

VISIBILITY_TOOLS = frozenset({"set_layer_visibility", "set_layers_visibility"})

RENDER_TOOLS = frozenset({"render_map", "render_camera_move"})


MIN_TO_OFFER = 3


MAX_REPORTED = 60



DERIVING_TOOLS = frozenset({
    "run_processing", "execute_processing_batch", "run_model", "duplicate_layer",
    "raster_calculator", "create_hillshade", "create_polygon_centerlines",
    "geocode_layer", "match_lines_to_roads",
})

_TASK_DONE = frozenset({"complete", "completed", "done", "finished", "success"})
_TASK_OVER = frozenset({"error", "failed", "canceled", "cancelled"})

TASK_READS = frozenset({"get_task_status", "list_tasks", "cancel_task"})

_ADDS_NOTHING = KEEP_TOOLS.union(VISIBILITY_TOOLS, RENDER_TOOLS, TASK_READS)
_BATCH_TOOL = "batch_commands"


def _strings(value, out: list) -> None:
    """Every string anywhere in a tool's arguments, however nested."""
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            _strings(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _strings(item, out)


def _layer_of(layer):
    """The (id, name) of a layer, or None when its C++ side is already gone."""
    try:
        return str(layer.id()), str(layer.name())
    except (RuntimeError, AttributeError):
        return None


def _named_ids(value, out: set, depth: int = 0) -> None:
    """Every layer id a result names under ``layer_id`` or ``layer_ids``, however nested (bounded)."""
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
    """A result that started a task: its layers arrive after the answer (run_processing in the background)."""
    return (isinstance(result, dict) and bool(result.get("task_id"))
            and str(result.get("status") or "").lower() == "running")


def _batch_commands(args, result) -> list:
    """A batch's commands that ran, as ``(tool, arguments, its own result)`` in order."""
    commands = args.get("commands") if isinstance(args, dict) else None
    results = result.get("results") if isinstance(result, dict) else None
    if not isinstance(commands, list) or not isinstance(results, list):
        return []
    out = []
    for command, item in zip(commands, results):
        if not isinstance(command, dict) or not isinstance(item, dict):
            continue
        arguments = command.get("arguments") if isinstance(command.get("arguments"), dict) else {}
        out.append((str(item.get("name") or command.get("name") or ""), arguments, item.get("result")))
    return out


def _batch_credit(commands: list, added: list) -> dict:
    """Which command of a batch made each layer the batch added, by index."""







    pool = set(added)
    credit: dict = {}
    naming: set = set()
    for index, (tool, _arguments, result) in enumerate(commands):
        if tool in _ADDS_NOTHING:
            continue
        ids: set = set()
        _named_ids(result, ids)
        for layer_id in [lid for lid in added if lid in ids & pool]:
            credit.setdefault(layer_id, index)
            naming.add(index)
    rest = [lid for lid in added if lid not in credit]
    silent = [index for index, (tool, _arguments, result) in enumerate(commands)
              if index not in naming and tool not in _ADDS_NOTHING and not _started_task(result)]
    if rest and len(silent) == 1:
        credit.update(dict.fromkeys(rest, silent[0]))
    return credit


class ScratchLedger:
    """What this run added, and which of it was only a step on the way."""







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
        except Exception as exc:  # noqa: BLE001 - an old build without the signal keeps no ledger
            log_warning(f"Scratch: cannot watch new layers: {exc}")
            return
        self._project = project

    def end(self) -> list[dict]:
        """Stop watching and answer with the report, which is also kept on ``last_report`` for the controller to read after the run is closed."""

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
        """How many layers the run had added before the call about to run."""
        return len(self._order)



    def _on_layers_added(self, layers) -> None:
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
        """One finished tool call: what it consumed, and what it kept."""







        starts = _started_task(result) or (name == _BATCH_TOOL and any(
            _started_task(inner) for _tool, _arguments, inner in _batch_commands(args, result)))
        if not self._names and not starts:
            return
        try:
            self._note_call(str(name or ""), args, mark, result)
        except Exception as exc:  # noqa: BLE001 - a ledger never fails a tool call
            log_warning(f"Scratch: {name} not accounted for: {exc}")

    def _note_call(self, name: str, args, mark, result=None) -> None:
        try:
            before = self._order[:int(mark)] if mark is not None else list(self._order)
        except (TypeError, ValueError):
            before = list(self._order)
        self._observe_tree()
        added = self._order[len(before):]
        if name == _BATCH_TOOL:
            self._note_batch(args, result, before, added)
            return
        for layer_id in added:
            self._made_by.setdefault(layer_id, name)
        self._settle_task(result)
        self._note_one(name, args, before, added, result)

    def _note_batch(self, args, result, before: list, added: list) -> None:
        """A batch, command by command: each is credited with the layers it made (``_batch_credit``)."""
        commands = _batch_commands(args, result)
        credit = _batch_credit(commands, added)
        for layer_id in added:
            index = credit.get(layer_id)
            self._made_by.setdefault(layer_id, commands[index][0] if index is not None else _BATCH_TOOL)
        for index, (tool, arguments, inner) in enumerate(commands):
            pool = before + [lid for lid in added if credit.get(lid, index) < index]
            self._settle_task(inner)
            self._note_one(tool, arguments, pool, [lid for lid in added if credit.get(lid) == index], inner)
        uncredited = [lid for lid in added if lid not in credit]
        if uncredited:


            named = self._referenced([arguments for tool, arguments, _inner in commands
                                      if tool not in _ADDS_NOTHING], before)
            self._consume(named, uncredited)

    def _note_one(self, name: str, args, pool: list, made: list, result) -> None:
        """What one call, or one command of a batch, says about the run's layers."""



        if name in RENDER_TOOLS:


            drawn = self._referenced(args, list(self._order))
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
        """These layers fed a call that made ``outputs``."""
        if not inputs:
            return
        for layer_id in inputs:
            self._fed.setdefault(layer_id, []).append(tuple(outputs))

    def _settle_task(self, result) -> None:
        """A result that closes a task this ledger saw start: complete consumes its inputs."""
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
        """The layers a finished task made: the ones its result names, else the ones no other task could have."""








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
        """Which of the run's layers a call switched off, read from the layer tree after it."""







        project = QgsProject.instance()
        try:
            root = project.layerTreeRoot() if project is not None else None
        except Exception:  # noqa: BLE001 - no tree, nothing to read
            return
        if root is None:
            return
        try:
            added_checked = bool(project.layerTreeRegistryBridge().newLayersVisible())
        except Exception:  # noqa: BLE001 - QGIS's default: new layers are checked
            added_checked = True
        for layer_id in self._order:
            try:
                node = root.findLayer(layer_id)
                if node is None:
                    continue
                drawn, checked = bool(node.isVisible()), bool(node.itemVisibilityChecked())
            except Exception:  # noqa: BLE001  # nosec B112 - a node that cannot say is left as it was
                continue
            seen = self._seen_drawn.get(layer_id)
            if drawn:
                self._switched_off.discard(layer_id)
            elif seen or (seen is None and not checked and added_checked):
                self._switched_off.add(layer_id)
            self._seen_drawn[layer_id] = drawn

    def _referenced(self, args, pool: list) -> set:
        """The layers of ``pool`` this call's arguments name, by id or by name."""










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
        """The run's scratch layers still in the project, oldest first, each with what to show."""









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
        """The run's working copies still in the project, oldest first, for the run report."""








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
        """Whether a call consumed this layer and made a layer that, or one made from it, is still in the project."""
        if layer_id in seen:
            return False
        seen.add(layer_id)
        return any(output in live or self._feeds_a_live_layer(output, live, seen)
                   for outputs in self._fed.get(layer_id, ()) for output in outputs)

    def hidden_ids(self) -> set[str]:
        """The layers a call of the run switched off itself (``_observe_tree``): not a fault of the deliverable."""
        return set(self._switched_off)


def remove(layer_ids) -> list:
    """Take these layers out of the project. Answers with the names removed."""
    project = QgsProject.instance()
    if project is None:
        return []
    gone = []
    for layer_id in list(layer_ids or ()):
        layer = project.mapLayer(str(layer_id))
        if layer is None:
            continue
        pair = _layer_of(layer)
        try:
            project.removeMapLayer(str(layer_id))
        except Exception as exc:  # noqa: BLE001 - one stubborn layer never stops the others
            log_warning(f"Scratch: {layer_id} not removed: {exc}")
            continue
        gone.append(pair[1] if pair else str(layer_id))
    return gone


def group(layer_ids, title: str) -> int:
    """Move these layers into one collapsed, unchecked group at the bottom."""






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
            holder = root.addGroup(title)
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
