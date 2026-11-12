# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

from qgis.core import QgsProcessingUtils, QgsProject, QgsVectorLayer

from ..core import policy
from ._layers import _source_key




WRITING: dict[str, tuple[str, str]] = {}


def writing() -> dict:

    return dict(WRITING)


def scratch_layers(project: QgsProject) -> tuple[list, list]:








    prefixes = tuple(_source_key(folder).rstrip("/") + "/"
                     for folder in (policy.AGENT_TMP_DIR, QgsProcessingUtils.tempFolder()) if folder)
    being_written = writing()
    memory_layers: list = []
    temporary_layers: list = []
    for layer in project.mapLayers().values():
        if layer.id() in being_written:
            continue
        if isinstance(layer, QgsVectorLayer) and layer.providerType() == "memory":
            memory_layers.append(layer)
        elif _source_key(str(layer.source() or "")).startswith(prefixes):
            temporary_layers.append(layer)
    return memory_layers, temporary_layers
