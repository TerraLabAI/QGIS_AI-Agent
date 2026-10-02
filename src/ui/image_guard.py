# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

from typing import Callable

from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice, QSize
from qgis.PyQt.QtGui import QImage, QImageReader


def read_bounded_image(source, max_pixels: int,
                       scaled: Callable[[QSize], QSize | None] | None = None) -> QImage | None:





    try:
        if isinstance(source, (bytes, bytearray)):
            buffer = QBuffer()
            buffer.setData(QByteArray(bytes(source)))
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            reader = QImageReader(buffer)
        else:
            reader = QImageReader(str(source))
        size = reader.size()
        if size.isValid():
            if size.width() * size.height() > max_pixels:
                return None
            target = scaled(size) if scaled is not None else None
            if target is not None:
                reader.setScaledSize(target)
        image = reader.read()
        return None if image.isNull() else image
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None
