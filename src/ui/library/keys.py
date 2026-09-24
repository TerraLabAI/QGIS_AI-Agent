# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later









from __future__ import annotations

from qgis.PyQt.QtCore import QPoint, Qt
from qgis.PyQt.QtWidgets import QScrollArea

LEFT, RIGHT, UP, DOWN = Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down
ARROWS = (LEFT, RIGHT, UP, DOWN)


def _centre(tile):
    window = tile.window()
    corner = tile.mapTo(window, QPoint(0, 0))
    return corner.x() + tile.width() / 2.0, corner.y() + tile.height() / 2.0


def step(tiles: list, index: int, key) -> int:



    if not tiles:
        return -1
    if index < 0 or index >= len(tiles):
        return 0
    try:
        cx, cy = _centre(tiles[index])
        half = tiles[index].height() / 2.0
        best, best_score = index, None
        for other, tile in enumerate(tiles):
            if other == index:
                continue
            x, y = _centre(tile)
            dx, dy = x - cx, y - cy
            if key in (LEFT, RIGHT):
                if abs(dy) > half or (dx >= 0 if key == LEFT else dx <= 0):
                    continue
                score = abs(dx)
            else:
                if (dy >= -half if key == UP else dy <= half):
                    continue
                score = abs(dy) * 4 + abs(dx)
            if best_score is None or score < best_score:
                best, best_score = other, score
        if best_score is None and key == UP:
            return -1
        return best
    except RuntimeError:
        return index


def reveal(tile) -> None:

    try:
        parent = tile.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                parent.ensureWidgetVisible(tile, 0, 24)
            parent = parent.parentWidget()
    except RuntimeError:
        pass
