# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The illustration above an example: one bundled SVG, tinted to its group."""




























from __future__ import annotations

import os

from qgis.PyQt.QtCore import QRectF, Qt
from qgis.PyQt.QtGui import QColor, QPainter, QPixmap

from ..font_scale import widget_pixel_ratio
from ..icons import ink_of, paper_of


ACCENT = "#2f6fed"
ACCENT_MID = "#7ea6f5"
ACCENT_WASH = "#d6e2fb"
INK = "#48536a"
INK_WASH = "#dfe4ec"
PAPER = "#ffffff"


SCENE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "icons", "examples")

_PIXMAPS: dict = {}
_SOURCES: dict = {}


def _renderer():
    """QSvgRenderer, or None on a build that ships no QtSvg."""
    try:
        from qgis.PyQt.QtSvg import QSvgRenderer
    except ImportError:
        return None
    return QSvgRenderer


def scene_path(name: str) -> str:
    """The file one scene name asks for. Not a promise that it exists."""
    clean = str(name or "").strip()


    if not clean or not clean.replace("-", "").replace("_", "").isalnum():
        return ""
    return os.path.join(SCENE_DIR, f"{clean}.svg")


def scene_exists(name: str) -> bool:
    path = scene_path(name)
    return bool(path) and os.path.isfile(path)


def _source(name: str) -> str:
    """The file's text, read once per session."""
    if name in _SOURCES:
        return _SOURCES[name]
    path = scene_path(name)
    text = ""
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            text = ""
    _SOURCES[name] = text
    return text


def _mix(colour: QColor, other: QColor, weight: float) -> QColor:
    """``colour`` moved ``weight`` of the way towards ``other``."""
    weight = max(0.0, min(1.0, weight))
    return QColor(
        round(colour.red() * (1 - weight) + other.red() * weight),
        round(colour.green() * (1 - weight) + other.green() * weight),
        round(colour.blue() * (1 - weight) + other.blue() * weight),
    )


def _palette(accent: str, ink: QColor, paper: QColor) -> list:
    """What each of the six tokens becomes, longest token first."""





    hue = QColor(accent)
    if not hue.isValid():
        hue = QColor(ACCENT)
    return [
        (ACCENT, hue.name()),
        (ACCENT_MID, _mix(hue, paper, 0.45).name()),
        (ACCENT_WASH, _mix(hue, paper, 0.82).name()),
        (INK, _mix(ink, paper, 0.25).name()),
        (INK_WASH, _mix(ink, paper, 0.86).name()),
        (PAPER, paper.name()),
    ]


def scene_svg(name: str, accent: str, ink: QColor, paper: QColor) -> bytes:
    """The file's bytes with the palette swapped, or b"" when there is none."""
    text = _source(name)
    if not text:
        return b""
    for token, replacement in _palette(accent, ink, paper):
        text = text.replace(token, replacement)
    return text.encode("utf-8")


def scene_pixmap(widget, name: str, accent: str, width: int, height: int):
    """The scene at ``width`` by ``height`` device-independent pixels, or None."""




    renderer = _renderer()
    if renderer is None or width <= 0 or height <= 0:
        return None
    ink, paper = ink_of(widget), paper_of(widget)




    ratio = widget_pixel_ratio(widget)
    key = (name, str(accent), ink.rgb(), paper.rgb(), int(width), int(height),
           round(float(ratio), 2))
    if key in _PIXMAPS:
        return _PIXMAPS[key]
    data = scene_svg(name, accent, ink, paper)
    if not data:
        _PIXMAPS[key] = None
        return None
    engine = renderer(data)
    if not engine.isValid():
        _PIXMAPS[key] = None
        return None
    pixmap = QPixmap(int(width * ratio), int(height * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    engine.render(painter, QRectF(0, 0, width, height))
    painter.end()
    _PIXMAPS[key] = pixmap
    return pixmap
