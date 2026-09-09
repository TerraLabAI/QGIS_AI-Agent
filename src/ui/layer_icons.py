# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The icon QGIS itself shows for a layer, so our lists read like the Layers panel."""







from __future__ import annotations

from qgis.core import QgsProject
from qgis.PyQt.QtGui import QIcon


def resolve_layer(layer_or_id):
    """The layer object for an id (or the object itself); None when gone."""
    if layer_or_id is None:
        return None
    if isinstance(layer_or_id, str):
        try:
            return QgsProject.instance().mapLayer(layer_or_id)
        except Exception:  # nosec B110 - optional QGIS context
            return None
    return layer_or_id


def layer_icon(layer_or_id) -> QIcon:
    layer = resolve_layer(layer_or_id)
    if layer is None:
        return QIcon()
    try:
        from qgis.core import QgsIconUtils
        icon = QgsIconUtils.iconForLayer(layer)
        if icon is not None and not icon.isNull():
            return icon
    except Exception:  # nosec B110 - older QGIS, fall through
        pass
    try:
        from qgis.core import QgsLayerItem, QgsRasterLayer, QgsVectorLayer, QgsWkbTypes
        if isinstance(layer, QgsRasterLayer):
            return QgsLayerItem.iconRaster()
        if isinstance(layer, QgsVectorLayer):
            geometry = layer.geometryType()
            if geometry == QgsWkbTypes.GeometryType.PointGeometry:
                return QgsLayerItem.iconPoint()
            if geometry == QgsWkbTypes.GeometryType.LineGeometry:
                return QgsLayerItem.iconLine()
            if geometry == QgsWkbTypes.GeometryType.PolygonGeometry:
                return QgsLayerItem.iconPolygon()
            return QgsLayerItem.iconTable()
        return QgsLayerItem.iconDefault()
    except Exception:  # nosec B110 - no icon beats a crash in a popup
        return QIcon()


def layer_depth(layer_or_id) -> int:
    """How many groups deep the layer sits in the Layers panel (0 at the root)."""
    layer = resolve_layer(layer_or_id)
    if layer is None:
        return 0
    try:
        node = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
    except Exception:  # nosec B110 - optional QGIS context
        return 0
    depth = 0
    parent = node.parent() if node is not None else None
    while parent is not None and parent.parent() is not None:
        depth += 1
        parent = parent.parent()
    return depth
