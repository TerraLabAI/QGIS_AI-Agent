# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

























from __future__ import annotations

import re
from contextlib import contextmanager




WEB_SERVICE_PROVIDERS = frozenset({"wfs", "oapif", "arcgisfeatureserver"})






_REMOTE_VECTOR_PROVIDERS = WEB_SERVICE_PROVIDERS | frozenset({"postgres", "postgresraster"})


def is_remote_vector(layer) -> bool:

    from qgis.core import QgsVectorLayer

    if not isinstance(layer, QgsVectorLayer):
        return False
    provider = str(layer.providerType() or "").casefold()
    source = str(layer.source() or "").strip().casefold()
    remote_source = source.startswith(("http://", "https://", "/vsicurl", "/vsis3", "/vsigs", "/vsiaz"))
    remote_source = remote_source or "host=" in source or " dbname=" in f" {source}"
    return provider in _REMOTE_VECTOR_PROVIDERS or remote_source





BACKDROP_PROVIDERS = frozenset({"wms", "xyz", "arcgismapserver", "arcgisrest", "vectortile"})
BACKDROP_CLASSES = ("QgsVectorTileLayer", "QgsTiledSceneLayer")

MAX_COVERING = 5


def is_backdrop(layer) -> bool:

    if layer is None:
        return False
    try:
        provider = (layer.providerType() or "").lower()
    except Exception:  # noqa: BLE001
        provider = ""
    if provider in BACKDROP_PROVIDERS:
        return True
    return type(layer).__name__ in BACKDROP_CLASSES


def basemap_slot(backdrops: list[bool]) -> int:







    index = len(backdrops)
    while index > 0 and backdrops[index - 1]:
        index -= 1
    return index


def _node_layer(node):

    getter = getattr(node, "layer", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:  # noqa: BLE001
        return None


def _node_is_backdrop(node) -> bool:
    return is_backdrop(_node_layer(node))


def _group_path(node) -> str:

    names = []
    parent = node.parent() if node is not None else None
    while parent is not None:
        name = ""
        try:
            name = parent.name() or ""
        except Exception:  # noqa: BLE001
            name = ""
        if not name:
            break
        names.append(name)
        parent = parent.parent()
    return " / ".join(reversed(names))


def _ancestors_visible(node) -> bool:

    parent = node.parent() if node is not None else None
    while parent is not None:
        checker = getattr(parent, "isVisible", None)
        if callable(checker):
            try:
                if not parent.isVisible():
                    return False
            except Exception:  # noqa: BLE001
                return True
        step = getattr(parent, "parent", None)
        parent = step() if callable(step) else None
    return True


def drawn_nodes(root) -> list:







    nodes = list(root.findLayers())
    if not root.hasCustomLayerOrder():
        return nodes
    by_id = {node.layerId(): node for node in nodes}
    drawn = [by_id.pop(layer.id()) for layer in root.customLayerOrder()
             if layer is not None and layer.id() in by_id]
    return drawn + list(by_id.values())


def follow_tree(layer, root=None) -> None:






    if root is None:
        root = _root()
    try:
        if root is None or layer is None or not root.hasCustomLayerOrder():
            return
        tree = [node.layerId() for node in root.findLayers()]
        if layer.id() not in tree:
            return
        below = set(tree[tree.index(layer.id()) + 1:])
        order = [other for other in root.customLayerOrder() if other is not None and other.id() != layer.id()]
        at = next((index for index, other in enumerate(order) if other.id() in below), len(order))
        root.setCustomLayerOrder(order[:at] + [layer] + order[at:])
    except Exception:  # nosec B110
        pass


def positions(root) -> dict:




    out: dict = {}
    if root is None:
        return out
    try:
        nodes = drawn_nodes(root)
    except Exception:  # noqa: BLE001
        return out
    for index, node in enumerate(nodes, start=1):
        try:
            layer_id = node.layerId()
        except Exception:  # nosec B112
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
    except Exception:  # noqa: BLE001
        return False
    moved = [clone] if _node_layer(clone) is not None else list(clone.findLayers())
    for each in moved:
        follow_tree(_node_layer(each))
    return True


def place_basemap(layer, root=None) -> dict:











    if root is None:
        root = _root()
    if root is None or layer is None:
        return {}
    try:
        node = root.findLayer(layer.id())
    except Exception:  # noqa: BLE001
        return {}
    if node is None:
        return {}
    parent = node.parent() or root
    others = [child for child in parent.children() if child is not node]
    target = basemap_slot([_node_is_backdrop(child) for child in others])
    moved = move_to_index(node, parent, target)
    follow_tree(layer, root)
    return {"moved": moved}


def cover_report(layer, root=None) -> dict:







    if root is None:
        root = _root()
    if root is None or layer is None:
        return {}
    try:
        nodes = drawn_nodes(root)
        layer_id = layer.id()
    except Exception:  # noqa: BLE001
        return {}
    index = None
    for position, node in enumerate(nodes):
        try:
            if node.layerId() == layer_id:
                index = position
                break
        except Exception:  # nosec B112
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
        except Exception:  # nosec B112
            continue
        above.append(name)
        if not hidden_by and is_backdrop(other) and _opacity(other) >= 1.0 and _backdrop_covers(other, layer):
            hidden_by = name
    if above:
        out["covered_by"] = above[:MAX_COVERING]
    if hidden_by:
        out["hidden_by"] = hidden_by
    return out





COVERAGE_PROPERTY = "terralab/coverage_4326"


def coverage(layer):

    try:
        text = str(layer.customProperty(COVERAGE_PROPERTY, "") or "")
        west, south, east, north = (float(part) for part in text.split(","))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None
    return (west, south, east, north) if west < east and south < north else None


def _backdrop_covers(backdrop, layer) -> bool:







    box = coverage(backdrop)
    if box is None:
        return True
    try:
        from qgis.core import QgsCoordinateReferenceSystem

        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        if is_backdrop(layer):
            below = coverage(layer)
            if below is None:
                return False
        else:
            rect = drawn_extent(layer, wgs84)
            if rect is None:
                return True
            below = (rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum())
    except Exception:  # noqa: BLE001
        return True
    return box[0] <= below[0] and box[1] <= below[1] and box[2] >= below[2] and box[3] >= below[3]


def _opacity(layer) -> float:
    try:
        return float(layer.opacity())
    except Exception:  # noqa: BLE001
        return 1.0


def _layer_id(layer) -> str:
    try:
        return layer.id()
    except RuntimeError:
        return ""


def _paints_fill(layer) -> bool:

    try:
        from qgis.core import QgsRenderContext
        renderer = layer.renderer()
        symbols = list(renderer.symbols(QgsRenderContext())) if renderer is not None else []
    except Exception:  # noqa: BLE001
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
        except Exception:  # noqa: BLE001
            return True
    return False


def _root():
    try:
        from qgis.core import QgsProject

        project = QgsProject.instance()
        return project.layerTreeRoot() if project is not None else None
    except Exception:  # noqa: BLE001
        return None












STACK_BACKDROP, STACK_RASTER, STACK_POLYGON, STACK_LINE, STACK_POINT = 0, 1, 2, 3, 4



STACK_LARGER_RATIO = 4.0


def stack_rank(layer) -> int | None:

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
        except Exception:  # noqa: BLE001  # nosec B110
            pass
    except Exception:  # noqa: BLE001
        return None
    lowered = name.lower()
    if "polygon" in lowered:
        return STACK_POLYGON
    if "line" in lowered:
        return STACK_LINE
    if "point" in lowered:
        return STACK_POINT
    return None


def stack_slot(rank: int, area: float | None, others: list[tuple]) -> int:
















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
        if (other_rank == rank and area and other_area and area > 0 and other_area > 0
                and area >= other_area * STACK_LARGER_RATIO):
            index = position + 1
            continue
        break
    return index








_SERIES_NUMBER = re.compile(r"(?<![0-9A-Za-z.])(19\d\d|20\d\d|2100)(?![0-9A-Za-z.])")

SERIES_GROUP_MIN = 3


def series_key(name: str) -> tuple[str, float] | None:









    text = str(name or "")
    matches = list(_SERIES_NUMBER.finditer(text))
    if not matches:
        return None
    last = matches[-1]
    rest = (text[:last.start()] + " " + text[last.end():]).lower()


    stem = " ".join(part for part in re.split(r"[\W_]+", rest) if part)
    return (stem, float(last.group(1))) if stem else None


def series_label(name: str) -> str:

    text = str(name or "")
    matches = list(_SERIES_NUMBER.finditer(text))
    if not matches:
        return text.strip()
    last = matches[-1]
    return " ".join((text[:last.start()] + " " + text[last.end():]).split()).strip(" -_,")


def series_slot(rank: int, key: tuple[str, float] | None,
                others: list[tuple[int | None, tuple[str, float] | None]], default_index: int) -> int:








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

    try:
        layer.setCustomProperty(KEEP_PLACE_PROPERTY, True)
    except Exception:  # nosec B110
        pass


def keeps_place(layer) -> bool:
    try:
        return bool(layer.customProperty(KEEP_PLACE_PROPERTY, False))
    except Exception:  # noqa: BLE001
        return False








TRUNCATED_COUNT_PROPERTY = "ai_agent/truncated_feature_count"


def mark_truncated_count(layer, count: int) -> None:

    try:
        layer.setCustomProperty(TRUNCATED_COUNT_PROPERTY, int(count))
    except Exception:  # nosec B110
        pass


def feature_count_of(layer) -> int | None:







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
        if str(layer.providerType() or "").lower() in WEB_SERVICE_PROVIDERS:


            return None
        count = int(layer.featureCount())
    except Exception:  # noqa: BLE001
        return None
    return count if count >= 0 else None


def drawn_extent(layer, crs=None):



















    import math

    if layer is None or is_backdrop(layer):
        return None
    try:
        from qgis.core import QgsCoordinateTransform, QgsProject, QgsRasterLayer, QgsRectangle

        from .vsi import streamed_in_place

        if not isinstance(layer, QgsRasterLayer) and streamed_in_place(layer):
            return None
        rect = layer.extent()
        if rect is None or rect.isNull() or rect.isEmpty():
            return None
        rect = QgsRectangle(rect)
        project = QgsProject.instance()
        target = crs if crs is not None else project.crs()
        source = layer.crs()
        if source.isValid() and target.isValid() and source != target:
            rect = QgsCoordinateTransform(source, target, project).transformBoundingBox(rect)
        values = (rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum())
        if not all(math.isfinite(float(value)) for value in values) or rect.isEmpty():
            return None
        return rect
    except Exception:  # noqa: BLE001
        return None


def drawn_area(layer) -> float | None:

    rect = drawn_extent(layer)
    if rect is None:
        return None
    area = float(rect.width()) * float(rect.height())
    return area if area > 0 else None


def place_new(layer, root=None) -> dict:







    if root is None:
        root = _root()
    if root is None or layer is None:
        return {}
    rank = stack_rank(layer)
    if rank is None or keeps_place(layer):
        follow_tree(layer, root)
        return {}
    try:
        node = root.findLayer(layer.id())
    except Exception:  # noqa: BLE001
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
            ranked.append((None, None))
            keyed.append((None, None))
            continue
        other_rank = stack_rank(other)
        if other_rank == STACK_POLYGON:
            ranked.append((other_rank, drawn_area(other), _paints_fill(other)))
        else:
            ranked.append((other_rank, drawn_area(other)))
        keyed.append((other_rank, series_key(other.name())))
    target = stack_slot(rank, drawn_area(layer), ranked)
    target = series_slot(rank, series_key(layer.name()), keyed, target)
    moved = move_to_index(node, parent, target)
    follow_tree(layer, root)
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
        except Exception:  # nosec B112
            continue
    return {"moved": moved, "under": under[:MAX_COVERING]}


def _lift_over_backdrops(group, root) -> bool:






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
    except Exception:  # nosec B110
        pass


def _group_alive(group) -> bool:

    try:
        group.name()
        return True
    except RuntimeError:
        return False


def _move_into_group(group, node, number: float) -> None:

    index = 0
    for child in group.children():
        other = _node_layer(child)
        key = series_key(other.name()) if other is not None else None
        if key is not None and key[1] < number:
            break
        index += 1
    move_to_index(node, group, index)




_RUN_TOKEN = None
_RUN_SERIAL = 0

_ADOPTING: list = []





_RECENT_IDS: list = []
MAX_RECENT_IDS = 40


def note_added(layer_id: str) -> None:

    text = str(layer_id or "")
    if not text:
        return
    if text in _RECENT_IDS:
        _RECENT_IDS.remove(text)
    _RECENT_IDS.append(text)
    del _RECENT_IDS[:-MAX_RECENT_IDS]


def recent_layer_ids(project=None) -> list:

    try:
        if project is None:
            from qgis.core import QgsProject

            project = QgsProject.instance()
        present = project.mapLayers()
    except Exception:  # noqa: BLE001
        return []
    return [layer_id for layer_id in reversed(_RECENT_IDS) if layer_id in present]


def forget_recent() -> None:

    del _RECENT_IDS[:]


def current_run():





    return _RUN_TOKEN


@contextmanager
def read_back():









    _ADOPTING.append(_READ_BACK)
    try:
        yield
    finally:
        _ADOPTING.pop()


_READ_BACK = object()


@contextmanager
def adopted(token):

    _ADOPTING.append(token)
    try:
        yield
    finally:
        _ADOPTING.pop()


class Stacker:












    def __init__(self):
        self._pending: list = []
        self._project = None
        self._placed: list = []
        self._series_groups: dict = {}
        self._grouped: dict = {}
        self._added: list = []
        self.in_call = None
        self._added_ids: list = []

        self.from_task: set = set()

        self._call_of: dict = {}
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
            project.readProject.connect(self._on_project_read)
            self._project = project
            reset_insertion_point()
        except Exception as exc:  # noqa: BLE001
            from .logger import log_warning

            log_warning(f"Layer order: cannot watch the layers a run adds: {exc}")

    def end(self) -> None:
        global _RUN_TOKEN
        if self._token is not None and self._token == _RUN_TOKEN:
            _RUN_TOKEN = None
        self._token = None
        if self._project is not None:
            for signal, slot in ((self._project.layersAdded, self._on_layers_added),
                                 (self._project.readProject, self._on_project_read)):
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
            self._project = None
        self._pending = []
        self._added = []
        self._placed = []
        self._series_groups = {}
        self._grouped = {}
        self._added_ids = []
        self.from_task = set()
        self._call_of = {}

    def _on_layers_added(self, layers) -> None:


        deferred = self._token is not None and self._token in _ADOPTING
        if not deferred and _ADOPTING and _ADOPTING[-1] != self._token:



            return
        if not deferred and self.in_call is not None:
            try:
                if not self.in_call():
                    return
            except Exception:  # noqa: BLE001
                return
        from .background import current_call

        caller = current_call()
        for layer in layers or ():
            if layer is None:
                continue

            self._added.append(layer)
            self._call_of[layer.id()] = caller
            self._added_ids.append(layer.id())
            if deferred:
                self.from_task.add(layer.id())
            note_added(layer.id())
            if not is_backdrop(layer):

                self._pending.append(layer)

    def _on_project_read(self, *_document) -> None:






        if _READ_BACK in _ADOPTING:
            return
        forgotten = set(self._added_ids)
        if forgotten:
            _RECENT_IDS[:] = [layer_id for layer_id in _RECENT_IDS if layer_id not in forgotten]
        self._added = []
        self._added_ids = []
        self._pending = []
        self._placed = []

    def added_count(self) -> int:

        try:
            from qgis.core import QgsProject
            present = QgsProject.instance().mapLayers()
        except Exception:  # noqa: BLE001
            return 0
        return sum(1 for layer_id in dict.fromkeys(self._added_ids) if layer_id in present)

    def added_total(self) -> int:

        return len(dict.fromkeys(self._added_ids))

    def fresh(self) -> list:






        try:
            from qgis.core import QgsProject
            present = QgsProject.instance().mapLayers()
        except Exception:  # noqa: BLE001
            return []
        out = []
        for layer in self._added:
            try:
                if layer.id() in present:
                    out.append(layer)
            except RuntimeError:  # nosec B112
                continue
        return out

    def place_pending(self) -> dict:

        pending, self._pending = self._pending, []
        out: dict = {}
        for layer in pending:
            try:
                placed = place_new(layer)
                if placed.get("moved") and placed.get("under"):
                    out[layer.name()] = placed["under"]
            except Exception as exc:  # noqa: BLE001
                from .logger import log_warning

                log_warning(f"Layer order: new layer not placed: {exc}")
        self._placed.extend(pending)
        try:
            self._file_series()
        except Exception as exc:  # noqa: BLE001
            from .logger import log_warning

            log_warning(f"Layer order: series not grouped: {exc}")
        reset_insertion_point()
        return out

    def take_grouped(self) -> dict:

        grouped, self._grouped = self._grouped, {}
        return grouped

    def take_added(self, tool_call_id: str | None = None, in_flight=()) -> list:





        if tool_call_id is None:
            added, self._added = self._added, []
            return added
        waiting = set(in_flight) - {tool_call_id}
        mine, kept = [], []
        for layer in self._added:
            try:
                owner = self._call_of.get(layer.id(), "")
            except RuntimeError:  # nosec B112
                continue
            (kept if owner in waiting else mine).append(layer)
        self._added = kept
        return mine

    def added_by(self, layer) -> str:

        try:
            return self._call_of.get(layer.id(), "")
        except RuntimeError:
            return ""

    def _file_series(self) -> None:








        root = _root()
        if root is None:
            return
        alive = []
        for layer in self._placed:
            try:
                node = root.findLayer(layer.id())
            except Exception:  # noqa: BLE001
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
