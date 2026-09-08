# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""One plugin logo, painted as an app icon."""





























from __future__ import annotations

from qgis.PyQt.QtCore import QRectF, Qt
from qgis.PyQt.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap



GROUND = QColor(244, 245, 247)
EDGE = QColor(0, 0, 0, 26)



INSET = 0.12




_INK = 24





_SCAN = 48

_cache: dict = {}


def logo_pixmap(path: str, side: int, ratio: float = 1.0):
    """The logo at ``path`` as a ``side``-wide chip, or None when it will not load."""
    key = (str(path), int(side), round(float(ratio or 1.0), 2))
    if key in _cache:
        return _cache[key]
    source = QPixmap(str(path))
    if source.isNull():
        return None
    chip = _chip(source, int(side), max(1.0, float(ratio or 1.0)))
    _cache[key] = chip
    return chip




_INK_TABLE = bytes(255 if value > _INK else 0 for value in range(256))


def _ink_box(scan: QImage):
    """``(left, top, right, bottom)`` of the pixels that carry ink, or None."""






    width, height = scan.width(), scan.height()
    try:
        scan = scan.convertToFormat(QImage.Format.Format_ARGB32)
        bits = scan.constBits()
        try:
            bits.setsize(scan.sizeInBytes())
        except AttributeError:
            pass
        data = bytes(bits)
        stride = scan.bytesPerLine()
        if len(data) < stride * height:
            raise ValueError("short buffer")
    except Exception:  # noqa: BLE001 - no buffer protocol: read it pixel by pixel
        return _ink_box_slow(scan)
    left, top = width, height
    right = bottom = -1
    for y in range(height):
        row = data[y * stride:y * stride + width * 4][3::4].translate(_INK_TABLE)
        first = row.find(b"\xff")
        if first < 0:
            continue
        left = min(left, first)
        right = max(right, row.rfind(b"\xff"))
        top = min(top, y)
        bottom = y
    return None if right < 0 else (left, top, right, bottom)


def _ink_box_slow(scan: QImage):
    left, top = scan.width(), scan.height()
    right = bottom = -1
    for y in range(scan.height()):
        for x in range(scan.width()):
            if scan.pixelColor(x, y).alpha() > _INK:
                left = min(left, x)
                right = max(right, x)
                top = min(top, y)
                bottom = max(bottom, y)
    return None if right < 0 else (left, top, right, bottom)


def ink_bounds(image: QImage):
    """The box the artwork actually occupies, or None when the image is empty."""
    if image.isNull():
        return None
    if image.width() >= image.height():
        wide, high = _SCAN, max(1, round(_SCAN * image.height() / max(1, image.width())))
    else:
        wide, high = max(1, round(_SCAN * image.width() / max(1, image.height()))), _SCAN
    scan = image.scaled(wide, high, Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
    box = _ink_box(scan)
    if box is None:
        return None
    left, top, right, bottom = box
    kx = image.width() / max(1, scan.width())
    ky = image.height() / max(1, scan.height())


    x0 = max(0, int((left - 1) * kx))
    y0 = max(0, int((top - 1) * ky))
    x1 = min(image.width(), int((right + 2) * kx))
    y1 = min(image.height(), int((bottom + 2) * ky))
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def _chip(source: QPixmap, side: int, ratio: float):
    image = source.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    box = ink_bounds(image)
    if box is not None:
        image = image.copy(*box)

    chip = QPixmap(int(side * ratio), int(side * ratio))
    chip.setDevicePixelRatio(ratio)
    chip.fill(Qt.GlobalColor.transparent)

    radius = side / 3.0 + 2
    rounded = QPainterPath()
    rounded.addRoundedRect(QRectF(0.5, 0.5, side - 1, side - 1), radius, radius)

    painter = QPainter(chip)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.fillPath(rounded, GROUND)
    painter.setClipPath(rounded)
    inner = max(1, int(side * (1 - INSET * 2)))
    art = QPixmap.fromImage(image).scaled(
        int(inner * ratio), int(inner * ratio),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    art.setDevicePixelRatio(ratio)
    painter.drawPixmap(int((side - art.width() / ratio) / 2),
                       int((side - art.height() / ratio) / 2), art)
    painter.setClipping(False)
    painter.setPen(EDGE)
    painter.drawPath(rounded)
    painter.end()
    return chip
