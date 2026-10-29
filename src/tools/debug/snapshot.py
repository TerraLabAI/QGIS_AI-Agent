# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

from qgis.PyQt.QtWidgets import QWidget

from ...core.serialization import dump_json
from ..adapters import get_adapter
from .refs import get_ref_store
from .widgets import (
    descendants,
    interactive_kind,
    process_events,
    top_level_roots,
    widget_node,
)


def _scope_roots(plugin_name: str):
    adapter = get_adapter(plugin_name)
    roots = adapter.snapshot_roots()
    if roots:
        return roots, adapter.display_name()
    return top_level_roots(), adapter.display_name()


def _collect(roots, interactive_only: bool, max_nodes: int):
    seen = set()
    widgets = []
    truncated = False
    for root in roots:
        if not isinstance(root, QWidget):
            continue
        for w in descendants(root):
            key = id(w)
            if key in seen:
                continue
            seen.add(key)
            if not w.isVisible():
                continue
            if interactive_only and interactive_kind(w) is None:
                continue
            widgets.append(w)
            if len(widgets) >= max_nodes:
                truncated = True
                return widgets, truncated
    return widgets, truncated




_MAX_TEXT = 120
_MAX_NODES = 200
_MAX_ROWS = 50
_SNAPSHOT_BUDGET = 24_000


def _accessibility_snapshot(plugin_name: str) -> dict:

    process_events()
    roots, root_label = _scope_roots(plugin_name)
    widgets, truncated = _collect(roots, True, _MAX_NODES)
    ref_map = get_ref_store().assign(widgets)
    refs = list(ref_map.keys())


    nodes = []
    size = 0
    omitted = 0
    for i, w in enumerate(widgets):
        node = widget_node(w, refs[i], False, _MAX_TEXT, _MAX_ROWS)
        size += len(dump_json(node)) + 1
        if nodes and size > _SNAPSHOT_BUDGET:
            omitted = len(widgets) - i
            break
        nodes.append(node)

    result = {
        "root": root_label,
        "count": len(nodes),
    }
    if omitted:
        result["more"] = True
        result["nodes_omitted"] = omitted
    if truncated:
        result["truncated"] = True
    result["snapshot"] = nodes
    return result
