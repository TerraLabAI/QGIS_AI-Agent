# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later









from __future__ import annotations

from qgis.core import QgsLayerTreeGroup, QgsLayerTreeLayer, QgsLayoutItemLegend, QgsLayoutItemMap


_LEGEND_ENTRIES = 60

_MAP_LAYERS = 60


def _number(value: float):

    return round(float(value), 6)


def _map_facts(item) -> dict:
    extent = item.extent()
    crs = item.crs()
    facts = {"uuid": item.uuid(), "id": item.displayName(),
             "extent": [_number(extent.xMinimum()), _number(extent.yMinimum()),
                        _number(extent.xMaximum()), _number(extent.yMaximum())],
             "crs": crs.authid() or crs.description(), "scale": round(item.scale(), 2)}
    if item.mapRotation():
        facts["rotation"] = _number(item.mapRotation())
    if item.followVisibilityPreset():
        facts["map_theme"] = item.followVisibilityPresetName()
    elif item.keepLayerSet():
        layers = [layer.name() for layer in item.layers() if layer is not None]
        facts["layers"] = layers[:_MAP_LAYERS]
        if len(layers) > _MAP_LAYERS:
            facts["layer_count"] = len(layers)
    else:
        facts["layers"] = "the project's visible layers, as the layer tree shows them"
    if item.atlasDriven():

        facts["atlas_driven"] = {0: "fixed scale", 1: "predefined scales",
                                 2: "margin around the feature"}.get(int(item.atlasScalingMode()), "")
        if int(item.atlasScalingMode()) == 2:
            facts["atlas_margin"] = _number(item.atlasMargin())
    grids = item.grids().size()
    if grids:
        facts["grids"] = grids
    overviews = item.overviews().size()
    if overviews:
        facts["overviews"] = overviews
    return facts


def _entries(group, out: list, total: list) -> None:
    for node in group.children():
        if isinstance(node, QgsLayerTreeLayer):
            total[0] += 1
            if len(out) < _LEGEND_ENTRIES:
                out.append(node.name())
        elif isinstance(node, QgsLayerTreeGroup):
            inner: list = []
            _entries(node, inner, total)
            if len(out) < _LEGEND_ENTRIES:
                out.append({"group": node.name(), "entries": inner})


def _legend_facts(item) -> dict:
    linked = item.linkedMap()
    facts = {"uuid": item.uuid(), "id": item.displayName(), "title": item.title(),
             "linked_map": linked.uuid() if linked is not None else None,
             "auto_update": item.autoUpdateModel(), "filtered_by_map": item.legendFilterByMapEnabled()}
    root = item.model().rootGroup() if item.model() is not None else None
    if root is not None:
        entries: list = []
        total = [0]
        _entries(root, entries, total)
        facts["entries"] = entries
        if total[0] > _LEGEND_ENTRIES:
            facts["entry_count"] = total[0]
    return facts


def _atlas_facts(layout) -> dict:
    atlas = layout.atlas()
    if atlas is None or not atlas.enabled():
        return {"enabled": False}
    coverage = atlas.coverageLayer()
    facts = {"enabled": True, "coverage_layer": coverage.name() if coverage is not None else None,
             "page_name": atlas.pageNameExpression(), "hide_coverage": atlas.hideCoverage(),
             "file_name": atlas.filenameExpression()}
    if atlas.filterFeatures():
        facts["filter"] = atlas.filterExpression()
    if atlas.sortFeatures():
        facts["sort_by"] = atlas.sortExpression()
        facts["sort_ascending"] = atlas.sortAscending()
    return facts


def layout_reads(layout) -> dict:

    maps, legends = [], []
    for item in layout.items():
        try:
            if isinstance(item, QgsLayoutItemMap):
                maps.append(_map_facts(item))
            elif isinstance(item, QgsLayoutItemLegend):
                legends.append(_legend_facts(item))
        except (AttributeError, RuntimeError, TypeError):
            continue
    out: dict = {"maps": maps, "legends": legends}
    try:
        out["atlas"] = _atlas_facts(layout)
    except (AttributeError, RuntimeError, TypeError):
        pass
    return out
