# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pure layer-of-interest ranking, no PyQGIS import so it stays unit-testable with plain python3."""





from __future__ import annotations

from typing import Any


def _intersects(extent: dict | None, rect: dict | None) -> bool:
    if not extent or not rect:
        return False
    try:
        return not (
            extent["xmax"] < rect["xmin"]
            or extent["xmin"] > rect["xmax"]
            or extent["ymax"] < rect["ymin"]
            or extent["ymin"] > rect["ymax"]
        )
    except (KeyError, TypeError):
        return False


def _rank_key(item: dict[str, Any], active_id: str | None, extent_rect: dict | None) -> int:
    if item.get("selected_count"):
        return 0
    if active_id is not None and item.get("id") == active_id:
        return 1
    visible = bool(item.get("visible"))
    if visible and extent_rect is not None and _intersects(item.get("extent"), extent_rect):
        return 2
    if visible:
        return 3
    return 4


def rank_layers(items: list[dict], active_id: str | None, extent_rect: dict | None) -> list[dict]:
    """Sort plain layer dicts (`id`, `visible`, `selected_count`, `extent`) by interest."""




    return sorted(items, key=lambda item: _rank_key(item, active_id, extent_rect))
