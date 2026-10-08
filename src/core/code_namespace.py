# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



















from __future__ import annotations

import json
import math
import os
import sys

WKT_CHARS = 2_000
_LIST_ITEMS = 200







PRELUDE = {"math": "math", "json": "json", "statistics": "statistics", "np": "numpy"}


def prelude(names: frozenset, importer) -> dict:

    out = {}
    for alias, module in PRELUDE.items():
        if alias not in names:
            continue
        try:
            out[alias] = importer(module)
        except Exception:  # noqa: BLE001  # nosec B112
            continue
    return out


def _owned_by_qgis(value) -> bool:










    if "qgis" not in sys.modules:
        return False
    try:
        from qgis.PyQt import sip
        from qgis.PyQt.QtCore import QObject
    except ImportError:
        return False
    if not isinstance(value, sip.simplewrapper):
        return False
    try:
        return isinstance(value, QObject) or sip.isdeleted(value) or not sip.ispyowned(value)
    except Exception:  # noqa: BLE001
        return True


def keepable(value, depth: int = 0) -> bool:

    if _owned_by_qgis(value):
        return False
    if isinstance(value, (list, tuple, set, frozenset)) and depth < 2:
        return all(keepable(item, depth + 1) for item in list(value)[:1000])
    if isinstance(value, dict) and depth < 2:
        return all(keepable(item, depth + 1) for item in list(value.values())[:1000])
    return True


def ordered_layers(project) -> list:
    try:
        order = [node.layer() for node in project.layerTreeRoot().findLayers()]
        found = [layer for layer in order if layer is not None]
        rest = [layer for lid, layer in project.mapLayers().items() if layer not in found]
        return found + rest
    except Exception:  # noqa: BLE001
        return list(project.mapLayers().values())


def layer_lookup(project):
    def layer(name_or_id):

        key = str(name_or_id or "")
        found = project.mapLayer(key)
        if found is not None:
            return found
        exact = project.mapLayersByName(key)
        if len(exact) == 1:
            return exact[0]
        names = [lyr.name() for lyr in ordered_layers(project)]
        if len(exact) > 1:
            raise LookupError(f"{len(exact)} layers are called {key!r}; pass one of their ids: "
                              + ", ".join(lyr.id() for lyr in exact[:10]))
        folded = [lyr for lyr in project.mapLayers().values() if lyr.name().casefold() == key.casefold()]
        if len(folded) == 1:
            return folded[0]
        shown = ", ".join(repr(n) for n in names[:20]) or "none"
        raise LookupError(f"No layer is called {key!r}. The project's layers: {shown}.")
    return layer


def encode(value, depth: int = 0):

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    try:
        from qgis.core import (
            QgsCoordinateReferenceSystem,
            QgsFeature,
            QgsGeometry,
            QgsMapLayer,
            QgsPoint,
            QgsPointXY,
            QgsRectangle,
        )
        from qgis.PyQt.QtCore import QDate, QDateTime, QTime, QVariant
        from qgis.PyQt.QtGui import QColor
    except ImportError:
        return _plain(value, depth)
    if isinstance(value, QVariant):
        return None if value.isNull() else encode(value.value(), depth)
    if isinstance(value, QgsMapLayer):
        return {"id": value.id(), "name": value.name(), "type": _layer_type(value),
                "crs": value.crs().authid() or None}
    if isinstance(value, QgsGeometry):
        return _cut_wkt(value.asWkt()) if not value.isNull() else None
    if isinstance(value, QgsFeature):
        out = {"id": value.id(),
               "attributes": {f.name(): encode(value.attribute(f.name()), depth + 1) for f in value.fields()}}
        if value.hasGeometry():
            out["geometry"] = _cut_wkt(value.geometry().asWkt())
        return out
    if isinstance(value, (QgsPointXY, QgsPoint)):
        return [value.x(), value.y()]
    if isinstance(value, QgsRectangle):
        return [value.xMinimum(), value.yMinimum(), value.xMaximum(), value.yMaximum()]
    if isinstance(value, QgsCoordinateReferenceSystem):
        return value.authid() or value.toWkt()[:200]
    if isinstance(value, QColor):
        return value.name()
    if isinstance(value, (QDate, QDateTime, QTime)):
        return value.toString("yyyy-MM-ddTHH:mm:ss" if isinstance(value, QDateTime) else
                              "yyyy-MM-dd" if isinstance(value, QDate) else "HH:mm:ss")
    return _plain(value, depth)


def _plain(value, depth: int):
    if depth > 6:
        return repr(value)[:200]
    if isinstance(value, dict):
        return {str(k): encode(v, depth + 1) for k, v in list(value.items())[:_LIST_ITEMS]}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        out = [encode(v, depth + 1) for v in items[:_LIST_ITEMS]]
        if len(items) > _LIST_ITEMS:
            out.append(f"... {len(items) - _LIST_ITEMS} more")
        return out
    if hasattr(value, "isoformat"):
        return value.isoformat()
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)[:500]


def _cut_wkt(text: str) -> str:
    return text if len(text) <= WKT_CHARS else text[:WKT_CHARS] + f"... ({len(text)} characters)"


def _layer_type(layer) -> str:
    kind = str(getattr(layer.type(), "name", layer.type()))
    return kind.replace("Layer", "").lower() or "layer"


def result_json(value) -> tuple[object, str]:

    return encode(value), type(value).__name__


def shown_files(namespace: dict) -> list[str]:







    value = namespace.get("show_files") if isinstance(namespace, dict) else None
    if isinstance(value, (str, os.PathLike)):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for entry in value[:_LIST_ITEMS]:
        try:
            path = os.path.abspath(os.path.expanduser(os.fspath(entry)))
            if isinstance(path, str) and os.path.isfile(path) and path not in out:
                out.append(path)
        except (TypeError, ValueError, OSError):
            continue
    return out




def _pointer(obj) -> int:
    if obj is None:
        return 0
    try:
        from qgis.PyQt import sip

        return int(sip.unwrapinstance(obj))
    except Exception:  # noqa: BLE001
        return 0


def _signature(layer) -> tuple:
    look, data = (), ()
    try:
        look = (_pointer(layer.renderer()), layer.opacity(), getattr(layer, "blendMode", lambda: 0)())
        if hasattr(layer, "labeling"):
            look += (_pointer(layer.labeling()), layer.labelsEnabled())
        if hasattr(layer, "featureCount"):
            data = (layer.featureCount(), layer.isModified(), layer.subsetString())
    except Exception:  # noqa: BLE001  # nosec B110
        pass
    return layer.name(), look, data


class _State(dict):


    facts: dict


def _quiet(read, default=None):
    try:
        return read()
    except Exception:  # noqa: BLE001
        return default


def _layer_facts(layer) -> dict:
    out = {}
    fields = _quiet(lambda: tuple(f.name() for f in layer.fields())) if hasattr(layer, "fields") else None
    if fields is not None:
        out["fields"] = fields
    crs = _quiet(lambda: layer.crs().authid())
    if crs is not None:
        out["crs"] = crs
    return out


def _tree_visibility(project) -> dict:
    nodes = _quiet(lambda: project.layerTreeRoot().findLayers(), [])
    out = {}
    for node in nodes:
        lid = _quiet(node.layerId)
        shown = _quiet(node.isVisible)
        if lid is not None and shown is not None:
            out[lid] = bool(shown)
    return out


def _item_types(layout) -> dict:


    counts: dict[str, int] = {}
    found = list(_quiet(layout.items, [])) + list(_quiet(layout.multiFrames, []))
    for item in found:
        kind = type(item).__name__
        if not kind.startswith("QgsLayoutItem") or kind == "QgsLayoutItemPage":
            continue
        kind = kind[len("QgsLayoutItem"):] or "Item"
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _layouts(project) -> dict:
    found = _quiet(lambda: project.layoutManager().printLayouts(), [])
    out = {}
    for layout in found:
        name = _quiet(layout.name)
        if name is not None:
            out[name] = _item_types(layout)
    return out


def project_state(project) -> dict:
    state = _State({lid: _signature(layer) for lid, layer in project.mapLayers().items()})
    state.facts = {
        "layers": {lid: _layer_facts(layer) for lid, layer in project.mapLayers().items()},
        "visible": _tree_visibility(project),
        "layouts": _layouts(project),
    }
    return state


class EditWatch:









    def __init__(self, project):
        self.values: dict[str, dict] = {}
        self._project = project


        self._links: list = []
        for lid, layer in _quiet(project.mapLayers, {}).items():
            if not (hasattr(layer, "committedAttributeValuesChanges")
                    and hasattr(layer, "committedGeometriesChanges")):
                continue
            for signal, slot in (("committedAttributeValuesChanges", self._on_values(lid)),
                                 ("committedGeometriesChanges", self._on_geoms(lid))):
                try:
                    getattr(layer, signal).connect(slot)
                    self._links.append((lid, signal, slot))
                except Exception:  # noqa: BLE001  # nosec B110
                    pass

    def _entry(self, lid) -> dict:
        return self.values.setdefault(lid, {"features": set(), "fields": set(), "geometries": set()})

    def _on_values(self, lid):
        def note(_layer_id, changes):
            entry = self._entry(lid)
            names = _quiet(lambda: self._project.mapLayer(lid).fields().names(), [])
            for fid, attrs in dict(changes).items():
                entry["features"].add(fid)
                for idx in dict(attrs):
                    entry["fields"].add(names[idx] if 0 <= idx < len(names) else str(idx))
        return note

    def _on_geoms(self, lid):
        def note(_layer_id, changes):
            self._entry(lid)["geometries"].update(dict(changes))
        return note

    def close(self) -> None:

        try:
            from qgis.PyQt import sip
        except Exception:  # noqa: BLE001
            sip = None
        for lid, signal, slot in self._links:
            layer = _quiet(lambda lid=lid: self._project.mapLayer(lid))
            if layer is None or sip is None or sip.isdeleted(layer):
                continue
            try:
                getattr(layer, signal).disconnect(slot)
            except Exception:  # noqa: BLE001  # nosec B110
                pass
        self._links = []


def _settle_tree_removals(project, before) -> None:










    try:
        from qgis.PyQt.QtCore import QCoreApplication, QEvent

        bridge = project.layerTreeRegistryBridge()
        if bridge is None:
            return
        QCoreApplication.removePostedEvents(bridge, QEvent.Type.MetaCall)
        root = project.layerTreeRoot()
        held = (getattr(before, "facts", None) or {}).get("visible") or {}
        gone = [lid for lid in project.mapLayers() if lid in held and root.findLayer(lid) is None]
        if gone:
            project.removeMapLayers(gone)
    except Exception:  # noqa: BLE001  # nosec B110
        pass


def changed(before: dict, project, edits: EditWatch | None = None) -> dict:



    _settle_tree_removals(project, before)
    after = project_state(project)
    out: dict = {}
    added = [lid for lid in after if lid not in before]
    removed = [lid for lid in before if lid not in after]
    if added:
        out["added"] = [{"id": lid, "name": after[lid][0]} for lid in added]
    if removed:
        out["removed"] = [before[lid][0] for lid in removed]
    facts0 = getattr(before, "facts", None) or {}
    facts1 = after.facts
    layers0, layers1 = facts0.get("layers", {}), facts1.get("layers", {})



    values = {}
    for lid, entry in ((edits.values if edits is not None else {}) or {}).items():
        if lid not in after or not (entry["features"] or entry["geometries"]):
            continue
        fact = {}
        if entry["features"]:
            fact["features"] = len(entry["features"])
            fact["fields"] = sorted(entry["fields"])
        if entry["geometries"]:
            fact["geometries"] = len(entry["geometries"])
        values[lid] = fact
    for lid in after:
        if lid not in before:
            continue
        (name0, look0, data0), (name1, look1, data1) = before[lid], after[lid]
        if name0 != name1:
            out.setdefault("renamed", []).append({"from": name0, "to": name1})
        if look0 != look1:
            out.setdefault("restyled", []).append(name1)


        refiltered = bool(data0 and data1) and data0[2:] != data1[2:]
        if refiltered:
            out.setdefault("filtered", {})[name1] = [data0[0], data1[0]]
        moved = bool(data0 and data1) and data0[0] != data1[0] and -1 not in (data0[0], data1[0])
        if (data0[1:2] != data1[1:2]) or (moved and not refiltered) or lid in values:
            out.setdefault("edited", []).append(name1)
        if moved and not refiltered:
            out.setdefault("features", {})[name1] = [data0[0], data1[0]]
        f0, f1 = layers0.get(lid, {}), layers1.get(lid, {})
        if "fields" in f0 and "fields" in f1:
            gained = [n for n in f1["fields"] if n not in f0["fields"]]
            lost = [n for n in f0["fields"] if n not in f1["fields"]]
            if gained:
                out.setdefault("fields_added", {})[name1] = gained
            if lost:
                out.setdefault("fields_removed", {})[name1] = lost
        if f0.get("crs") is not None and f1.get("crs") is not None and f0["crs"] != f1["crs"]:
            out.setdefault("crs", {})[name1] = [f0["crs"], f1["crs"]]
    if values:
        out["values"] = {after[lid][0]: fact for lid, fact in values.items()}
    vis0, vis1 = facts0.get("visible", {}), facts1.get("visible", {})
    for lid, shown in vis1.items():
        if lid in before and lid in vis0 and vis0[lid] != shown:
            out.setdefault("shown" if shown else "hidden", []).append(after[lid][0])
    if "layouts" in facts0:
        lay0, lay1 = facts0["layouts"], facts1.get("layouts", {})
        made = {n: kinds for n, kinds in lay1.items() if n not in lay0}
        gone = [n for n in lay0 if n not in lay1]
        moved = {}
        for name, kinds in lay1.items():
            if name not in lay0:
                continue
            old = lay0[name]
            plus = {k: kinds.get(k, 0) - old.get(k, 0) for k in kinds if kinds.get(k, 0) > old.get(k, 0)}
            minus = {k: old.get(k, 0) - kinds.get(k, 0) for k in old if old.get(k, 0) > kinds.get(k, 0)}
            if plus or minus:
                moved[name] = {k: v for k, v in (("added", plus), ("removed", minus)) if v}
        if made:
            out["layouts_added"] = made
        if gone:
            out["layouts_removed"] = gone
        if moved:
            out["layouts_changed"] = moved
    return out
