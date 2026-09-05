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


class ScratchLedger:
    """What this run added, and which of it was only a step on the way."""







    def __init__(self):
        self._project = None
        self._order: list[str] = []
        self._names: dict[str, str] = {}
        self._consumed: set[str] = set()
        self._kept: set[str] = set()
        self._hidden: set[str] = set()
        self._last_render: set[str] = set()
        self.last_report: list[dict] = []



    def begin(self) -> None:
        self.end()
        self._order, self._names = [], {}
        self._consumed, self._kept, self._hidden, self._last_render = set(), set(), set(), set()
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



    def note_call(self, name: str, args, mark) -> None:
        """One finished tool call: what it consumed, and what it kept."""





        if not self._names:
            return
        try:
            self._note_call(str(name or ""), args, mark)
        except Exception as exc:  # noqa: BLE001 - a ledger never fails a tool call
            log_warning(f"Scratch: {name} not accounted for: {exc}")

    def _note_call(self, name: str, args, mark) -> None:
        try:
            before = self._order[:int(mark)] if mark is not None else list(self._order)
        except (TypeError, ValueError):
            before = list(self._order)
        created = len(self._order) > len(before)
        referenced = self._referenced(args, before)
        if name in RENDER_TOOLS:


            drawn = self._referenced(args, list(self._order))
            self._last_render = drawn or self._last_render
            return
        if name in VISIBILITY_TOOLS:
            visible = args.get("visible") if isinstance(args, dict) else None
            target = self._kept if visible is not False else self._hidden
            target.update(self._referenced(args, list(self._order)))
            return
        if name in KEEP_TOOLS:
            self._kept.update(self._referenced(args, list(self._order)))
            return
        if created and referenced:
            self._consumed.update(referenced)

    def _referenced(self, args, pool: list) -> set:
        """The layers of ``pool`` this call's arguments name, by id or by name."""







        if not pool:
            return set()
        texts = []
        _strings(args, texts)
        wanted = {t.strip() for t in texts if t and len(t.strip()) >= 3}
        if not wanted:
            return set()
        found = set()
        for layer_id in pool:
            name = self._names.get(layer_id, "")
            if layer_id in wanted or (name and name in wanted):
                found.add(layer_id)
        return found



    def report(self) -> list[dict]:
        """The run's scratch layers, oldest first, each with what to show."""





        project = QgsProject.instance()
        if project is None or len(self._order) <= 1:
            return []
        try:
            live = project.mapLayers()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Scratch: no project to report on: {exc}")
            return []
        out = []


        for layer_id in self._order[:-1]:
            if layer_id in self._kept or layer_id in self._last_render:
                continue
            if layer_id not in self._consumed and layer_id not in self._hidden:
                continue
            layer = live.get(layer_id)
            if layer is None:
                continue
            pair = _layer_of(layer)
            if pair is None:
                continue
            out.append({"layer_id": pair[0], "name": pair[1]})
            if len(out) >= MAX_REPORTED:
                break
        return out


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
