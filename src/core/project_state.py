# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later













from __future__ import annotations

import hashlib
import re
import time

from qgis.core import QgsProject, QgsVectorLayer, QgsWkbTypes

from .context_layers import feature_count, layer_kind, source_kind
from .crs_ref import crs_ref
from .logger import log_debug
from .snapshot_style import _style_digest

MAX_LAYERS = 400

STYLE_BUDGET_S = 0.15


def _short(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def _crs(crs) -> str:
    ref = crs_ref(crs)

    return ref if not ref or re.fullmatch(r"[A-Z]{2,8}:[A-Za-z0-9_.]{1,24}", ref) else "custom"


def _geometry(layer) -> str | None:

    if not isinstance(layer, QgsVectorLayer):
        return None
    return QgsWkbTypes.displayString(layer.wkbType()) or None


def snapshot() -> dict:
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    layers = list(project.mapLayers().values())
    try:

        nodes = {node.layerId(): node for node in root.findLayers()}
    except Exception:  # noqa: BLE001
        nodes = {}
    out = []
    deadline = time.perf_counter() + STYLE_BUDGET_S
    for layer in layers[:MAX_LAYERS]:
        try:
            node = nodes.get(layer.id())
            style = _style_digest(layer) if time.perf_counter() < deadline else None
            out.append({
                "k": _short(layer.id()),
                "type": layer_kind(layer),
                "geometry": _geometry(layer),
                "crs": _crs(layer.crs()),
                "features": feature_count(layer, source_kind(layer)),
                "style": _short(style) if style else None,
                "visible": bool(node.isVisible()) if node is not None else True,
            })
        except Exception as exc:  # noqa: BLE001
            log_debug(f"Project state left a layer out: {exc}")
    return {"crs": _crs(project.crs()), "count": len(layers), "layers": out}
