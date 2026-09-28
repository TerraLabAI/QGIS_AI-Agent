# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


from __future__ import annotations

import os
import re

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core.tool_registry import Tool, ToolRegistry


def _source_key(layer) -> str:
    source = str(layer.source() or "").strip()


    source = re.sub(r"[\\/]+", "/", source)
    if source and (os.path.isabs(source) or re.match(r"^[A-Za-z]:/", source)):
        source = os.path.normcase(os.path.normpath(source)).replace("\\", "/")
    return source.casefold()


def _extent_key(layer):
    extent = layer.extent()
    return tuple(round(float(v), 9) for v in (extent.xMinimum(), extent.yMinimum(),
                                               extent.xMaximum(), extent.yMaximum()))


def _layer_signature(layer) -> dict:
    provider = str(layer.providerType() or "").casefold()
    return {
        "name": layer.name(),
        "name_key": " ".join(layer.name().split()).casefold(),
        "source": layer.source(),


        "source_key": "" if provider == "memory" else _source_key(layer),
        "type": int(layer.type()),
        "provider": provider,
        "extent": list(_extent_key(layer)),
    }


def _duplicate_groups(args: dict) -> dict:
    project = QgsProject.instance()
    layers = list(project.mapLayers().values())



    signatures = {layer.id(): _layer_signature(layer) for layer in layers}
    groups: list[dict] = []
    seen: set[str] = set()
    for index, layer in enumerate(layers):
        if layer.id() in seen:
            continue
        sig = signatures[layer.id()]
        matches = []
        for other in layers[index + 1:]:
            other_sig = signatures[other.id()]
            same_source = bool(sig["source_key"]) and sig["source_key"] == other_sig["source_key"]


            same_memory = (
                not sig["source_key"] and not other_sig["source_key"] and sig["name_key"] == other_sig["name_key"]
            )
            if (
                (same_source or same_memory)
                and sig["type"] == other_sig["type"]
                and sig["provider"] == other_sig["provider"]
                and sig["extent"] == other_sig["extent"]
            ):
                matches.append(other)
        if not matches:
            continue
        members = [layer] + matches
        seen.update(item.id() for item in members)
        groups.append({
            "keep_layer_id": layer.id(),
            "layers": [{"id": item.id(), "name": item.name()} for item in members],
            "comparison": {
                "same_source_or_memory_name": True,
                "same_type": True,
                "same_provider": True,
                "same_extent": True,
            },
        })

    remove = bool(args.get("remove", False))
    removed = []
    if remove:
        for group in groups:
            for item in group["layers"][1:]:
                if project.mapLayer(item["id"]) is not None:
                    project.removeMapLayer(item["id"])
                    removed.append(item)
    return {
        "duplicates_found": sum(max(0, len(group["layers"]) - 1) for group in groups),
        "groups": groups,
        "removed": removed,
        "removed_by_request": remove,
        "note": ("No layers were removed. remove=true deletes each group's extra layers."
                  if not remove else "Kept the first layer in each group and removed later duplicates."),
    }


def register_layer_duplicate_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="qgis_find_duplicate_layers",
        danger="destructive",
        label=QT_TRANSLATE_NOOP("AIAgent", "Find duplicate project layers"),
        input_schema={
            "type": "object",
            "properties": {"remove": {"type": "boolean"}},
            "additionalProperties": False,
        },
        handler=_duplicate_groups,
    ))
