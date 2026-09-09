# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

from qgis.core import QgsProject
from qgis.PyQt.QtGui import QIcon


def resolve_layer(layer_or_id):

    if layer_or_id is None:
        return None
    if isinstance(layer_or_id, str):
        try:
            return QgsProject.instance().mapLayer(layer_or_id)
        except Exception:  # nosec B110
            return None
    return layer_or_id


def layer_name(layer_id) -> str | None:









    layer = resolve_layer(str(layer_id or ""))
    if layer is None:
        return None
    try:
        return layer.name()
    except RuntimeError:

        return None


def layer_icon(layer_or_id) -> QIcon:
    layer = resolve_layer(layer_or_id)
    if layer is None:
        return QIcon()
    try:
        from qgis.core import QgsIconUtils
        icon = QgsIconUtils.iconForLayer(layer)
        if icon is not None and not icon.isNull():
            return icon
    except Exception:  # nosec B110
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
    except Exception:  # nosec B110
        return QIcon()


