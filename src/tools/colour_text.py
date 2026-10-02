# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later









from __future__ import annotations

import re

from qgis.PyQt.QtGui import QColor

_HEX8 = re.compile(r"#([0-9a-fA-F]{8})")


def _hex8_channels(value):

    match = _HEX8.fullmatch(value) if isinstance(value, str) else None
    if not match:
        return None
    digits = match.group(1)
    return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4, 6))


def qcolor_from_text(value) -> QColor:





    channels = _hex8_channels(value)
    return QColor(*channels) if channels else QColor(value)


def qgis_colour_text(value):





    channels = _hex8_channels(value)
    return ",".join(str(c) for c in channels) if channels else value


def hex_from_qcolor(colour: QColor) -> str:

    rgb = f"{colour.red():02x}{colour.green():02x}{colour.blue():02x}"
    return f"#{rgb}" if colour.alpha() == 255 else f"#{rgb}{colour.alpha():02x}"
