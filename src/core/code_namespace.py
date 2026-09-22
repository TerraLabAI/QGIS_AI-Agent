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


def project_state(project) -> dict:
    return {lid: _signature(layer) for lid, layer in project.mapLayers().items()}


def _settle_tree_removals(project) -> None:







    try:
        from qgis.PyQt.QtCore import QCoreApplication, QEvent

        bridge = project.layerTreeRegistryBridge()
        if bridge is not None:
            QCoreApplication.sendPostedEvents(bridge, QEvent.Type.MetaCall)
    except Exception:  # noqa: BLE001  # nosec B110
        pass


def changed(before: dict, project) -> dict:

    _settle_tree_removals(project)
    after = project_state(project)
    out: dict[str, list] = {}
    added = [lid for lid in after if lid not in before]
    removed = [lid for lid in before if lid not in after]
    if added:
        out["added"] = [{"id": lid, "name": after[lid][0]} for lid in added]
    if removed:
        out["removed"] = [before[lid][0] for lid in removed]
    for lid in after:
        if lid not in before:
            continue
        (name0, look0, data0), (name1, look1, data1) = before[lid], after[lid]
        if name0 != name1:
            out.setdefault("renamed", []).append({"from": name0, "to": name1})
        if look0 != look1:
            out.setdefault("restyled", []).append(name1)
        if data0 != data1:
            out.setdefault("edited", []).append(name1)
    return out
