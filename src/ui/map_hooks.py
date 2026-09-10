# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reach the agent from where a GIS user already is: the layer tree, the map, the table."""











from __future__ import annotations

from qgis.core import QgsLayerTree, QgsMapLayer, QgsVectorLayer
from qgis.PyQt.QtCore import QCoreApplication

from ..core.logger import log_warning
from ..core.qt_compat import enum_member
from .shared import QAction


def tr(text: str) -> str:
    return QCoreApplication.translate("MapHooks", text)


MAX_TREE_CHIPS = 8


def _enum(owner, scope: str, member: str):
    """Qt6 scopes the enum (``Target.SingleFeature``); Qt5 exposes the member on the class."""
    scoped = getattr(owner, scope, None)
    value = getattr(scoped, member, None) if scoped is not None else None
    return value if value is not None else getattr(owner, member)


class MapHooks:
    """Owns the actions; ``install`` and ``remove`` bracket the plugin's life."""

    def __init__(self, iface, open_panel, add_chip):
        self._iface = iface
        self._open_panel = open_panel
        self._add_chip = add_chip
        self._layer_action = None
        self._canvas_connected = False
        self._tree_connected = False
        self._feature_action = None

    def install(self) -> None:
        if self._layer_action is not None or self._canvas_connected:
            return
        self._layer_action = QAction(tr("Ask AI Agent about this layer"), self._iface.mainWindow())
        self._layer_action.triggered.connect(self._on_layer_action)
        vector = enum_member(QgsMapLayer, "LayerType", "VectorLayer")
        raster = enum_member(QgsMapLayer, "LayerType", "RasterLayer")
        for kind in (vector, raster):
            try:
                self._iface.addCustomActionForLayerType(self._layer_action, None, kind, True)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Layer menu action unavailable: {exc}")
        canvas = self._iface.mapCanvas()
        try:
            canvas.contextMenuAboutToShow.connect(self._on_canvas_menu)
            self._canvas_connected = True
        except Exception as exc:  # noqa: BLE001 - QGIS before 3.30 has no such signal
            log_warning(f"Canvas menu hook unavailable: {exc}")
        try:
            self._iface.layerTreeView().contextMenuAboutToShow.connect(self._on_tree_menu)
            self._tree_connected = True
        except Exception as exc:  # noqa: BLE001 - QGIS before 3.32 has no such signal
            log_warning(f"Layer tree menu hook unavailable: {exc}")
        self._install_feature_action()

    def _install_feature_action(self) -> None:
        """"Ask AI Agent about this feature" in the attribute table and the identify results."""
        try:
            from qgis.gui import QgsGui, QgsMapLayerAction

            single = _enum(QgsMapLayerAction, "Target", "SingleFeature")
            try:
                targets = QgsMapLayerAction.Targets(single | _enum(QgsMapLayerAction, "Target", "MultipleFeatures"))
            except Exception:  # noqa: BLE001 - older QGIS without the flags wrapper
                targets = single
            action = QgsMapLayerAction(tr("Ask AI Agent about this feature"), self._iface.mainWindow(), targets)
            action.triggeredForFeature.connect(self._on_feature)
            action.triggeredForFeatures.connect(self._on_features)
            QgsGui.mapLayerActionRegistry().addMapLayerAction(action)
            self._feature_action = action
        except Exception as exc:  # noqa: BLE001 - the action is a convenience
            log_warning(f"Feature action unavailable: {exc}")

    def remove(self) -> None:
        if self._feature_action is not None:
            try:
                from qgis.gui import QgsGui

                QgsGui.mapLayerActionRegistry().removeMapLayerAction(self._feature_action)
            except Exception:  # nosec B110 - registry may be gone at shutdown
                pass



            for signal, slot in ((self._feature_action.triggeredForFeature, self._on_feature),
                                 (self._feature_action.triggeredForFeatures, self._on_features)):
                try:
                    signal.disconnect(slot)
                except Exception:  # nosec B110 - Qt connection may be gone
                    pass
            self._feature_action.deleteLater()
            self._feature_action = None
        if self._tree_connected:
            try:
                self._iface.layerTreeView().contextMenuAboutToShow.disconnect(self._on_tree_menu)
            except Exception:  # nosec B110 - Qt connection may be gone
                pass
            self._tree_connected = False
        if self._layer_action is not None:









            for _ in range(8):
                try:
                    if not self._iface.removeCustomActionForLayerType(self._layer_action):
                        break
                except Exception:  # nosec B110 - Qt connection may be gone
                    break
            try:
                self._layer_action.triggered.disconnect(self._on_layer_action)
            except Exception:  # nosec B110 - Qt connection may be gone
                pass
            self._layer_action.setVisible(False)
            self._layer_action = None
        if self._canvas_connected:
            try:
                self._iface.mapCanvas().contextMenuAboutToShow.disconnect(self._on_canvas_menu)
            except Exception:  # nosec B110 - Qt connection may be gone
                pass
            self._canvas_connected = False



    def _on_layer_action(self) -> None:
        layer = self._iface.layerTreeView().currentLayer() or self._iface.activeLayer()
        if layer is None:
            return
        self._open_panel()
        self._add_chip({"kind": "layer", "label": layer.name(), "value": layer.id()})

    def _on_canvas_menu(self, menu, _event) -> None:
        layer = self._iface.activeLayer()
        count = layer.selectedFeatureCount() if isinstance(layer, QgsVectorLayer) else 0
        menu.addSeparator()
        if count:
            label = tr("Ask AI Agent about the {n} selected features").format(n=count) if count > 1 \
                else tr("Ask AI Agent about the selected feature")
            action = menu.addAction(label)
            action.triggered.connect(lambda _checked=False, lyr=layer, n=count: self._ask_selection(lyr, n))
        else:
            action = menu.addAction(tr("Ask AI Agent about this view"))
            action.triggered.connect(lambda _checked=False: self._ask_view())

    def _ask_selection(self, layer, count: int) -> None:
        self._open_panel()
        label = tr("selection ({n} features)").format(n=count) if count != 1 \
            else tr("selection (1 feature)")
        self._add_chip({"kind": "selection", "label": label, "value": layer.id()})

    def _ask_view(self) -> None:
        self._open_panel()
        self._add_chip({"kind": "extent", "label": tr("current extent"), "value": "canvas"})

    def _on_tree_menu(self, menu) -> None:
        """Several layers or a group selected: one entry that pins them all."""
        try:
            nodes = self._iface.layerTreeView().selectedNodes(True)
        except Exception:  # nosec B110 - older signature
            return
        layers = []
        group_name = ""
        for node in nodes:
            if QgsLayerTree.isGroup(node):
                group_name = group_name or node.name()
                layers.extend(n.layer() for n in node.findLayers() if n.layer() is not None)
            elif QgsLayerTree.isLayer(node) and node.layer() is not None:
                layers.append(node.layer())
        unique = list({layer.id(): layer for layer in layers}.values())
        if len(unique) < 2 and not group_name:
            return
        if group_name and len(nodes) == 1:
            label = tr("Ask AI Agent about group {name} ({n} layers)").format(name=group_name, n=len(unique)) \
                if len(unique) != 1 \
                else tr("Ask AI Agent about group {name} (1 layer)").format(name=group_name)
        else:
            label = tr("Ask AI Agent about these {n} layers").format(n=len(unique))
        menu.addSeparator()
        action = menu.addAction(label)
        action.triggered.connect(lambda _checked=False, lyrs=unique: self._ask_layers(lyrs))

    def _ask_layers(self, layers) -> None:
        self._open_panel()
        for layer in layers[:MAX_TREE_CHIPS]:
            self._add_chip({"kind": "layer", "label": layer.name(), "value": layer.id()})

    def _on_feature(self, layer, feature) -> None:
        self._on_features(layer, [feature])

    def _on_features(self, layer, features) -> None:
        """Select the features on their layer and pin the selection: the context carries their attributes."""
        if not isinstance(layer, QgsVectorLayer):
            return
        ids = [f.id() for f in features if f is not None]
        if not ids:
            return
        try:
            layer.selectByIds(ids)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Feature selection failed: {exc}")
            return
        self._ask_selection(layer, len(ids))
