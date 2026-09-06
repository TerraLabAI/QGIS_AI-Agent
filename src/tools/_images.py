# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Image encoding helpers shared by the public tools and the debug subsystem."""




from __future__ import annotations

import base64

from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice, Qt

_FMT_ALIASES = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png"}


def normalize_fmt(fmt: str | None, default: str = "png") -> str:
    """Map a caller-supplied format name to 'png' or 'jpeg' (default if unknown)."""
    return _FMT_ALIASES.get((fmt or "").strip().lower(), default)


def image_to_base64(image, fmt: str = "png", quality: int = 95) -> str:
    """Encode a QImage."""





    fmt = normalize_fmt(fmt)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if fmt == "jpeg":
        image.save(buf, "JPEG", quality)
    else:
        image.save(buf, "PNG")
    buf.close()
    return base64.b64encode(bytes(ba)).decode("ascii")


def image_to_base64_jpeg(image, quality: int = 75) -> str:
    return image_to_base64(image, "jpeg", quality)


def scale_image(image, max_width: int):
    if image.width() > max_width:
        return image.scaledToWidth(max_width, Qt.TransformationMode.SmoothTransformation)
    return image
