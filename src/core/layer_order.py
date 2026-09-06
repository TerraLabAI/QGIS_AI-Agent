# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which layer draws over which, and where a new backdrop belongs."""




















from __future__ import annotations





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


def stack_slot(rank: int, area: float, others: list[tuple[int | None, float]]) -> int:
    """The index a new layer takes among children listed top to bottom."""









    index = 0
    for position, (other_rank, other_area) in enumerate(others):
        if other_rank is None:
            continue
        if other_rank > rank:
            index = position + 1
            continue
        if other_rank == rank and area > 0 and other_area > 0 and area >= other_area * STACK_LARGER_RATIO:
            index = position + 1
            continue
        break
    return index





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
    for child in others:
        other = _node_layer(child)
        ranked.append((stack_rank(other), _extent_area(other)) if other is not None else (None, 0.0))
    target = stack_slot(rank, _extent_area(layer), ranked)
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


class Stacker:
    """Places every layer a run adds, once its tree node exists."""








    def __init__(self):
        self._pending: list = []
        self._project = None

    def begin(self) -> None:
        self.end()
        try:
            from qgis.core import QgsProject

            project = QgsProject.instance()
            project.layersAdded.connect(self._on_layers_added)
            self._project = project
        except Exception as exc:  # noqa: BLE001 - the run goes on with QGIS's own order
            from .logger import log_warning

            log_warning(f"Layer order: cannot watch the layers a run adds: {exc}")

    def end(self) -> None:
        if self._project is not None:
            try:
                self._project.layersAdded.disconnect(self._on_layers_added)
            except (TypeError, RuntimeError):
                pass
            self._project = None
        self._pending = []

    def _on_layers_added(self, layers) -> None:
        for layer in layers or ():
            if layer is not None and not is_backdrop(layer):

                self._pending.append(layer)

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
        return out
