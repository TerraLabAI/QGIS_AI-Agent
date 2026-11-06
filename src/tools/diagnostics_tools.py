# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

from qgis.core import Qgis, QgsProject, QgsRasterLayer, QgsVectorLayer

from ..core.background import running_tasks
from ..core.layer_order import is_remote_vector
from ..core.tool_registry import Tool, ToolRegistry
from ._layers import layer_not_found, resolve_layer

_MAX_LAYERS = 200
_REMOTE_RASTER_PROVIDERS = ("wms", "wcs", "arcgismapserver")


def register_diagnostics_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="diagnose_project",
        danger="read",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
            },
        },
        handler=_diagnose_project,
    ))


def _vector_facts(layer: QgsVectorLayer, visible: bool) -> dict:
    provider = layer.providerType()
    lower = layer.source().lower()
    remote = is_remote_vector(layer)
    try:
        no_index = layer.hasSpatialIndex() == Qgis.SpatialIndexPresence.NotPresent
    except Exception:  # nosec B110
        no_index = False
    return {"layer": layer.name(), "layer_id": layer.id(), "kind": "vector", "visible": visible,
            "provider": provider, "remote": remote, "features": -1 if remote else int(layer.featureCount()),
            "no_index": no_index, "shapefile": ".shp" in lower, "geopackage": ".gpkg" in lower,
            "memory": lower.startswith("memory"), "scale_limited": bool(layer.hasScaleBasedVisibility()),
            "crs": layer.crs().authid() if layer.crs().isValid() else ""}


def _raster_facts(layer: QgsRasterLayer, visible: bool) -> dict:
    facts = {"layer": layer.name(), "layer_id": layer.id(), "kind": "raster", "visible": visible,
             "provider": layer.providerType(), "remote": layer.providerType() in _REMOTE_RASTER_PROVIDERS}
    if facts["remote"]:
        return facts
    try:
        pyramids = bool(layer.dataProvider().hasPyramids())
    except Exception:  # nosec B110
        pyramids = True
    return {**facts, "pixels": int(layer.width()) * int(layer.height()), "pyramids": pyramids,
            "scale_limited": bool(layer.hasScaleBasedVisibility())}


def _diagnose_project(args: dict) -> dict:
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    wanted = str(args.get("layer_name") or "").strip()
    layers = list(project.mapLayers().values())
    if wanted:


        found = resolve_layer(wanted)
        if found is None:
            return layer_not_found(wanted)
        layers = [found]
    facts: list[dict] = []
    visible_count = 0
    remote_count = 0
    for layer in layers[:_MAX_LAYERS]:
        node = root.findLayer(layer.id())
        visible = bool(node.isVisible()) if node is not None else True
        visible_count += int(visible)
        try:
            if isinstance(layer, QgsVectorLayer):
                entry = _vector_facts(layer, visible)
            elif isinstance(layer, QgsRasterLayer):
                entry = _raster_facts(layer, visible)
            else:
                continue
            remote_count += int(entry["remote"])
            facts.append(entry)
        except Exception as exc:  # noqa: BLE001
            facts.append({"layer": layer.name(), "layer_id": layer.id(), "error": str(exc)})
    try:
        tasks = running_tasks()
    except Exception:  # noqa: BLE001
        tasks = []
    return {
        "layers_checked": min(len(layers), _MAX_LAYERS),
        "layers_visible": visible_count,
        "remote_layers": remote_count,
        "project_crs": project.crs().authid(),

        **({"scoped": True} if wanted else {}),



        **({"background_tasks": tasks} if tasks else {}),
        "layer_facts": facts,
    }
