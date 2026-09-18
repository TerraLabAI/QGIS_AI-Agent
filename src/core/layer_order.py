# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which layer draws over which, and where a new backdrop belongs."""




















from __future__ import annotations

import re
from contextlib import contextmanager





BACKDROP_PROVIDERS = frozenset({"wms", "xyz", "arcgismapserver", "arcgisrest", "vectortile"})
BACKDROP_CLASSES = ("QgsVectorTileLayer", "QgsTiledSceneLayer")

MAX_COVERING = 5


def is_backdrop(layer) -> bool:
    """Whether the layer is a tile or map service rather than data of its own."""
    if layer is None:
        return False
    try:
        provider = (layer.providerType() or "").lower()
    except Exception:  # noqa: BLE001 - a layer that cannot name its provider is not a backdrop
        provider = ""
    if provider in BACKDROP_PROVIDERS:
        return True
    return type(layer).__name__ in BACKDROP_CLASSES


def basemap_slot(backdrops: list[bool]) -> int:
    """The index a new basemap takes among children listed top to bottom."""






    index = len(backdrops)
    while index > 0 and backdrops[index - 1]:
        index -= 1
    return index


def _node_layer(node):
    """The layer of a tree node, or None for a group."""
    getter = getattr(node, "layer", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:  # noqa: BLE001 - a node whose layer is gone is not a layer
        return None


def _node_is_backdrop(node) -> bool:
    return is_backdrop(_node_layer(node))


def _group_path(node) -> str:
    """The names of the groups above a node, outermost first, "" at the root."""
    names = []
    parent = node.parent() if node is not None else None
    while parent is not None:
        name = ""
        try:
            name = parent.name() or ""
        except Exception:  # noqa: BLE001 - the root has no usable name
            name = ""
        if not name:
            break
        names.append(name)
        parent = parent.parent()
    return " / ".join(reversed(names))


def _ancestors_visible(node) -> bool:
    """Whether every group above the node is ticked. An unticked group draws nothing."""
    parent = node.parent() if node is not None else None
    while parent is not None:
        checker = getattr(parent, "isVisible", None)
        if callable(checker):
            try:
                if not parent.isVisible():
                    return False
            except Exception:  # noqa: BLE001 - the root answers nothing on some builds
                return True
        step = getattr(parent, "parent", None)
        parent = step() if callable(step) else None
    return True


def positions(root) -> dict:
    """``{layer_id: {"index", "group", "group_visible"}}`` in drawing order, top first."""




    out: dict = {}
    if root is None:
        return out
    try:
        nodes = list(root.findLayers())
    except Exception:  # noqa: BLE001 - a project without a tree is not an error here
        return out
    for index, node in enumerate(nodes, start=1):
        try:
            layer_id = node.layerId()
        except Exception:  # nosec B112 - noqa: BLE001 - a node with no id says nothing about order
            continue
        if not layer_id:
            continue
        out[layer_id] = {
            "index": index,
            "group": _group_path(node),
            "group_visible": _ancestors_visible(node),
        }
    return out


def move_to_index(node, parent, target: int) -> bool:
    """Put ``node`` at ``target`` among ``parent``'s other children, top counted first."""







    old = node.parent()
    if old is None:
        return False
    if old is parent:
        children = list(parent.children())
        try:
            here = children.index(node)
        except ValueError:
            return False
        if target == here:
            return False
        insert_at = target if target <= here else target + 1
    else:
        insert_at = target
    try:
        clone = node.clone()
        parent.insertChildNode(insert_at, clone)
        old.removeChildNode(node)
    except Exception:  # noqa: BLE001 - a tree that refuses the move keeps the layer where it is
        return False
    return True


def place_basemap(layer, root=None) -> dict:
    """Move a freshly added tile layer to the top of the basemap block."""










    if root is None:
        root = _root()
    if root is None or layer is None:
        return {}
    try:
        node = root.findLayer(layer.id())
    except Exception:  # noqa: BLE001 - a layer that is not in the tree cannot be placed
        return {}
    if node is None:
        return {}
    parent = node.parent() or root
    others = [child for child in parent.children() if child is not node]
    target = basemap_slot([_node_is_backdrop(child) for child in others])
    return {"moved": move_to_index(node, parent, target)}


def cover_report(layer, root=None) -> dict:
    """Where the layer landed and what draws over it, for the tool result."""






    if root is None:
        root = _root()
    if root is None or layer is None:
        return {}
    try:
        nodes = list(root.findLayers())
        layer_id = layer.id()
    except Exception:  # noqa: BLE001 - no tree, nothing to report
        return {}
    index = None
    for position, node in enumerate(nodes):
        try:
            if node.layerId() == layer_id:
                index = position
                break
        except Exception:  # nosec B112 - noqa: BLE001 - a node with no id is not the one we placed
            continue
    if index is None:
        return {}
    out = {"position": index + 1, "of": len(nodes)}
    above = []
    hidden_by = ""
    for node in reversed(nodes[:index]):
        other = _node_layer(node)
        if other is None:
            continue
        try:
            if not node.isVisible() or not _ancestors_visible(node):
                continue
            name = other.name()
        except Exception:  # nosec B112 - noqa: BLE001 - a layer that cannot answer is not counted
            continue
        above.append(name)
        if not hidden_by and is_backdrop(other) and _opacity(other) >= 1.0:
            hidden_by = name
    if above:
        out["covered_by"] = above[:MAX_COVERING]
    if hidden_by:
        out["hidden_by"] = hidden_by
    return out


def _opacity(layer) -> float:
    try:
        return float(layer.opacity())
    except Exception:  # noqa: BLE001 - a layer with no opacity is a full one
        return 1.0


def _paints_fill(layer) -> bool:
    """Whether a polygon layer paints its inside, so a raster under it is hidden. True when unsure."""
    try:
        from qgis.core import QgsRenderContext
        renderer = layer.renderer()
        symbols = list(renderer.symbols(QgsRenderContext())) if renderer is not None else []
    except Exception:  # noqa: BLE001 - a renderer that cannot be read is treated as a fill
        return True
    if not symbols:
        return True
    for symbol in symbols:
        try:
            if float(symbol.opacity()) <= 0.0:
                continue
            for symbol_layer in symbol.symbolLayers():
                if hasattr(symbol_layer, "enabled") and not symbol_layer.enabled():
                    continue
                kind = type(symbol_layer).__name__
                if "Line" in kind and "Fill" not in kind:
                    continue
                if kind == "QgsSimpleFillSymbolLayer":
                    from qgis.PyQt.QtCore import Qt
                    if symbol_layer.brushStyle() == Qt.BrushStyle.NoBrush or symbol_layer.fillColor().alpha() == 0:
                        continue
                return True
        except Exception:  # noqa: BLE001 - a symbol that cannot answer is treated as a fill
            return True
    return False


def _root():
    try:
        from qgis.core import QgsProject

        project = QgsProject.instance()
        return project.layerTreeRoot() if project is not None else None
    except Exception:  # noqa: BLE001 - outside QGIS there is no tree
        return None












STACK_BACKDROP, STACK_RASTER, STACK_POLYGON, STACK_LINE, STACK_POINT = 0, 1, 2, 3, 4



STACK_LARGER_RATIO = 4.0


def stack_rank(layer) -> int | None:
    """The stacking class of a layer, ``None`` for one that draws nothing."""
    if layer is None:
        return None
    if is_backdrop(layer):
        return STACK_BACKDROP
    try:
        from qgis.core import QgsRasterLayer, QgsVectorLayer
    except ImportError:
        return None
    if isinstance(layer, QgsRasterLayer):
        return STACK_RASTER
    if not isinstance(layer, QgsVectorLayer):

        return STACK_RASTER if hasattr(layer, "extent") else None
    try:
        name = str(layer.geometryType())
        try:
            from qgis.core import QgsWkbTypes

            name = QgsWkbTypes.geometryDisplayString(layer.geometryType())
        except Exception:  # noqa: BLE001  # nosec B110 - the enum name is enough without the helper
            pass
    except Exception:  # noqa: BLE001 - a layer that cannot say its geometry is not stacked
        return None
    lowered = name.lower()
    if "polygon" in lowered:
        return STACK_POLYGON
    if "line" in lowered:
        return STACK_LINE
    if "point" in lowered:
        return STACK_POINT
    return None


def stack_slot(rank: int, area: float, others: list[tuple]) -> int:
    """The index a new layer takes among children listed top to bottom."""












    index = 0
    for position, other in enumerate(others):
        other_rank, other_area = other[0], other[1]
        other_fills = other[2] if len(other) > 2 else True
        if other_rank is None:
            continue
        if other_rank > rank:
            if rank == STACK_RASTER and other_rank == STACK_POLYGON and other_fills:
                break
            index = position + 1
            continue
        if other_rank == rank and area > 0 and other_area > 0 and area >= other_area * STACK_LARGER_RATIO:
            index = position + 1
            continue
        break
    return index








_SERIES_NUMBER = re.compile(r"(?<![0-9A-Za-z.])(19\d\d|20\d\d|2100)(?![0-9A-Za-z.])")

SERIES_GROUP_MIN = 3


def series_key(name: str) -> tuple[str, float] | None:
    """``("ndvi brut", 2021.0)`` for "NDVI 2021 brut", ``None`` without a year."""








    text = str(name or "")
    matches = list(_SERIES_NUMBER.finditer(text))
    if not matches:
        return None
    last = matches[-1]
    rest = (text[:last.start()] + " " + text[last.end():]).lower()


    stem = " ".join(part for part in re.split(r"[\W_]+", rest) if part)
    return (stem, float(last.group(1))) if stem else None


def series_label(name: str) -> str:
    """The family's name as a person wrote it: "NDVI 2021 brut" gives "NDVI brut"."""
    text = str(name or "")
    matches = list(_SERIES_NUMBER.finditer(text))
    if not matches:
        return text.strip()
    last = matches[-1]
    return " ".join((text[:last.start()] + " " + text[last.end():]).split()).strip(" -_,")


def series_slot(rank: int, key: tuple[str, float] | None,
                others: list[tuple[int | None, tuple[str, float] | None]], default_index: int) -> int:
    """Where a numbered layer goes so its family stays together, highest number on top."""







    if key is None:
        return default_index
    stem, number = key
    family = [(index, other[1][1]) for index, other in enumerate(others)
              if other[0] == rank and other[1] is not None and other[1][0] == stem]
    if not family:
        return default_index
    for index, other_number in family:
        if other_number < number:
            return index
    return family[-1][0] + 1





KEEP_PLACE_PROPERTY = "ai_agent/keep_place"


def keep_place(layer) -> None:
    """Mark a layer as placed by the tool that added it: the stacker leaves it alone."""
    try:
        layer.setCustomProperty(KEEP_PLACE_PROPERTY, True)
    except Exception:  # nosec B110 - noqa: BLE001 - a layer without properties is stacked like any other
        pass


def keeps_place(layer) -> bool:
    try:
        return bool(layer.customProperty(KEEP_PLACE_PROPERTY, False))
    except Exception:  # noqa: BLE001
        return False








TRUNCATED_COUNT_PROPERTY = "ai_agent/truncated_feature_count"


def mark_truncated_count(layer, count: int) -> None:
    """Record the features a capped remote load actually holds, read by feature_count_of()."""
    try:
        layer.setCustomProperty(TRUNCATED_COUNT_PROPERTY, int(count))
    except Exception:  # nosec B110 - noqa: BLE001 - a layer without properties reports its own count
        pass


def feature_count_of(layer) -> int | None:
    """The count to report for *layer*: the stamped true count when one was capped, else the provider's own ``featureCount()``, None when that is."""



    try:
        stamped = layer.customProperty(TRUNCATED_COUNT_PROPERTY, None)
    except Exception:  # noqa: BLE001
        stamped = None
    if stamped is not None:
        try:
            return int(stamped)
        except (TypeError, ValueError):
            pass
    try:
        count = int(layer.featureCount())
    except Exception:  # noqa: BLE001
        return None
    return count if count >= 0 else None


def _extent_area(layer) -> float:
    """The layer's extent as an area in the project CRS, 0 when it says nothing."""






    try:
        from .vsi import streamed_in_place

        if streamed_in_place(layer):
            return 0.0
        rect = layer.extent()
        if rect is None or rect.isNull():
            return 0.0
        from qgis.core import QgsCoordinateTransform, QgsProject

        project = QgsProject.instance()
        source, target = layer.crs(), project.crs()
        if source.isValid() and target.isValid() and source != target:
            rect = QgsCoordinateTransform(source, target, project).transformBoundingBox(rect)
        return max(0.0, float(rect.width()) * float(rect.height()))
    except Exception:  # noqa: BLE001 - a layer with no extent is stacked by rank alone
        return 0.0


def place_new(layer, root=None) -> dict:
    """Move a freshly added layer to where its kind belongs among its siblings."""






    if root is None:
        root = _root()
    if root is None or layer is None:
        return {}
    rank = stack_rank(layer)
    if rank is None or keeps_place(layer):
        return {}
    try:
        node = root.findLayer(layer.id())
    except Exception:  # noqa: BLE001 - a layer that is not in the tree cannot be placed
        return {}
    if node is None:
        return {}
    parent = node.parent() or root
    others = [child for child in parent.children() if child is not node]
    ranked = []
    keyed = []
    for child in others:
        other = _node_layer(child)
        if other is None:
            ranked.append((None, 0.0))
            keyed.append((None, None))
            continue
        other_rank = stack_rank(other)
        if other_rank == STACK_POLYGON:
            ranked.append((other_rank, _extent_area(other), _paints_fill(other)))
        else:
            ranked.append((other_rank, _extent_area(other)))
        keyed.append((other_rank, series_key(other.name())))
    target = stack_slot(rank, _extent_area(layer), ranked)
    target = series_slot(rank, series_key(layer.name()), keyed, target)
    moved = move_to_index(node, parent, target)
    if parent is not root and _lift_over_backdrops(parent, root):
        moved = True
    under = []
    for child in others[:target]:
        other = _node_layer(child)
        if other is None:
            continue
        try:
            if child.isVisible():
                under.append(other.name())
        except Exception:  # nosec B112 - noqa: BLE001 - a sibling that cannot answer is not named
            continue
    return {"moved": moved, "under": under[:MAX_COVERING]}


def _lift_over_backdrops(group, root) -> bool:
    """A group that holds data is never under a basemap: lift it over the top one."""





    lifted = False
    node = group
    while node is not None and node is not root:
        parent = node.parent()
        if parent is None:
            break
        siblings = list(parent.children())
        try:
            here = siblings.index(node)
        except ValueError:
            break
        above = [position for position, other in enumerate(siblings[:here]) if _node_is_backdrop(other)]
        if above and move_to_index(node, parent, above[0]):
            lifted = True
        node = parent
    return lifted


def reset_insertion_point(root=None) -> None:
    """Put QGIS's insertion point back at the root of the layer tree."""







    if root is None:
        root = _root()
    if root is None:
        return
    try:
        from qgis.core import QgsLayerTreeRegistryBridge, QgsProject

        bridge = QgsProject.instance().layerTreeRegistryBridge()
        if bridge is None:
            return
        point = getattr(QgsLayerTreeRegistryBridge, "InsertionPoint", None)
        if point is not None:
            bridge.setLayerInsertionPoint(point(root, 0))
        else:
            bridge.setLayerInsertionPoint(root, 0)
    except Exception:  # nosec B110 - noqa: BLE001 - an insertion point we cannot set stays QGIS's to choose
        pass


def _group_alive(group) -> bool:
    """Whether a group node is still in the tree rather than deleted under us."""
    try:
        group.name()
        return True
    except RuntimeError:
        return False


def _move_into_group(group, node, number: float) -> None:
    """Move a layer node into *group*, keeping the group ordered by number, highest first."""
    index = 0
    for child in group.children():
        other = _node_layer(child)
        key = series_key(other.name()) if other is not None else None
        if key is not None and key[1] < number:
            break
        index += 1
    clone = node.clone()
    group.insertChildNode(index, clone)
    parent = node.parent()
    if parent is not None:
        parent.removeChildNode(node)




_RUN_TOKEN = None
_RUN_SERIAL = 0

_ADOPTING: list = []





_RECENT_IDS: list = []
MAX_RECENT_IDS = 40


def note_added(layer_id: str) -> None:
    """Remember that an agent call added this layer, newest last."""
    text = str(layer_id or "")
    if not text:
        return
    if text in _RECENT_IDS:
        _RECENT_IDS.remove(text)
    _RECENT_IDS.append(text)
    del _RECENT_IDS[:-MAX_RECENT_IDS]


def recent_layer_ids(project=None) -> list:
    """The ids of the layers the agent added lately that the project still holds, newest first."""
    try:
        if project is None:
            from qgis.core import QgsProject

            project = QgsProject.instance()
        present = project.mapLayers()
    except Exception:  # noqa: BLE001 - no project, nothing to point at
        return []
    return [layer_id for layer_id in reversed(_RECENT_IDS) if layer_id in present]


def forget_recent() -> None:
    """Drop the remembered ids. A new project shares none of them."""
    del _RECENT_IDS[:]


def current_run():
    """The token of the run whose layers are watched now, or None outside a run."""




    return _RUN_TOKEN


@contextmanager
def adopted(token):
    """Layers added inside belong to the run ``token`` names, if it is still the watched run."""
    _ADOPTING.append(token)
    try:
        yield
    finally:
        _ADOPTING.pop()


class Stacker:
    """Places every layer a run adds, once its tree node exists."""











    def __init__(self):
        self._pending: list = []
        self._project = None
        self._placed: list = []
        self._series_groups: dict = {}
        self._grouped: dict = {}
        self._added: list = []
        self.in_call = None
        self._added_ids: list = []
        self._token = None

    def begin(self) -> None:
        self.end()
        global _RUN_TOKEN, _RUN_SERIAL
        _RUN_SERIAL += 1
        self._token = _RUN_SERIAL
        _RUN_TOKEN = self._token
        try:
            from qgis.core import QgsProject

            project = QgsProject.instance()
            project.layersAdded.connect(self._on_layers_added)
            self._project = project
            reset_insertion_point()
        except Exception as exc:  # noqa: BLE001 - the run goes on with QGIS's own order
            from .logger import log_warning

            log_warning(f"Layer order: cannot watch the layers a run adds: {exc}")

    def end(self) -> None:
        global _RUN_TOKEN
        if self._token is not None and self._token == _RUN_TOKEN:
            _RUN_TOKEN = None
        self._token = None
        if self._project is not None:
            try:
                self._project.layersAdded.disconnect(self._on_layers_added)
            except (TypeError, RuntimeError):
                pass
            self._project = None
        self._pending = []
        self._added = []
        self._placed = []
        self._series_groups = {}
        self._grouped = {}
        self._added_ids = []

    def _on_layers_added(self, layers) -> None:


        deferred = self._token is not None and self._token in _ADOPTING
        if not deferred and _ADOPTING and _ADOPTING[-1] != self._token:



            return
        if not deferred and self.in_call is not None:
            try:
                if not self.in_call():
                    return
            except Exception:  # noqa: BLE001 - a check that cannot answer means not in a call
                return
        for layer in layers or ():
            if layer is None:
                continue

            self._added.append(layer)
            self._added_ids.append(layer.id())
            note_added(layer.id())
            if not is_backdrop(layer):

                self._pending.append(layer)

    def added_count(self) -> int:
        """How many layers added by this run's tool calls are still in the project."""
        try:
            from qgis.core import QgsProject
            present = QgsProject.instance().mapLayers()
        except Exception:  # noqa: BLE001 - no project, nothing counted
            return 0
        return sum(1 for layer_id in dict.fromkeys(self._added_ids) if layer_id in present)

    def place_pending(self) -> dict:
        """Place what the last call added. ``{name: under}`` for each layer moved under something."""
        pending, self._pending = self._pending, []
        out: dict = {}
        for layer in pending:
            try:
                placed = place_new(layer)
                if placed.get("moved") and placed.get("under"):
                    out[layer.name()] = placed["under"]
            except Exception as exc:  # noqa: BLE001 - a layer that is added is added, placed or not
                from .logger import log_warning

                log_warning(f"Layer order: new layer not placed: {exc}")
        self._placed.extend(pending)
        try:
            self._file_series()
        except Exception as exc:  # noqa: BLE001 - a layer outside a group is still a layer
            from .logger import log_warning

            log_warning(f"Layer order: series not grouped: {exc}")
        reset_insertion_point()
        return out

    def take_grouped(self) -> dict:
        """``{layer name: group name}`` for what was filed since the last read."""
        grouped, self._grouped = self._grouped, {}
        return grouped

    def take_added(self) -> list:
        """Every layer added since the last read, basemaps included, for core/licence.credit_added."""
        added, self._added = self._added, []
        return added

    def _file_series(self) -> None:
        """Put a numbered family this run added into a group of its own."""







        root = _root()
        if root is None:
            return
        alive = []
        for layer in self._placed:
            try:
                node = root.findLayer(layer.id())
            except Exception:  # noqa: BLE001 - a layer removed mid-run is not filed
                node = None
            if node is not None:
                alive.append((layer, node))
        self._placed = [layer for layer, _ in alive]
        families: dict = {}
        for layer, node in alive:
            if keeps_place(layer):
                continue
            key = series_key(layer.name())
            if key is None:
                continue
            parent = node.parent()
            if parent is not None and parent is not root and parent is not self._series_groups.get(key[0]):
                continue
            families.setdefault(key[0], []).append((key[1], layer, node))
        for stem, members in families.items():
            members.sort(key=lambda item: -item[0])
            group = self._series_groups.get(stem)
            if group is not None and not _group_alive(group):
                group = None
                self._series_groups.pop(stem, None)
            if group is None:
                if len(members) < SERIES_GROUP_MIN:
                    continue
                group = root.addGroup(series_label(members[0][1].name()) or stem)
                self._series_groups[stem] = group
            for number, layer, node in members:
                if node.parent() is group:
                    continue
                _move_into_group(group, node, number)
                self._grouped[layer.name()] = group.name()
            _lift_over_backdrops(group, root)
