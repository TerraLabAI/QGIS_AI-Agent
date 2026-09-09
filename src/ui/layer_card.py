# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The layer card above the composer and in a sent bubble."""













from __future__ import annotations

from qgis.PyQt.QtCore import pyqtSignal

from ..core.context import layer_geometry_label
from ..core.layer_mime import card_kind_line, cheap_feature_count
from .attach_card import TILE_GLYPH, AttachCard
from .layer_icons import layer_icon, resolve_layer

_KIND_GLYPHS = {
    "layer": "layers",
    "selection": "pin",
    "extent": "expand",
    "field": "checklist",
    "layout": "image",
    "file": "file",
    "source": "globe",
}


def chip_display(chip: dict) -> str:
    """The label as given; no ``@`` in front of a layer any more."""
    return str(chip.get("label") or chip.get("value") or "")


def layer_crs_label(layer) -> str:
    try:
        crs = layer.crs()
        authid = str(crs.authid() or "")
        description = str(crs.description() or "")
    except (AttributeError, RuntimeError, TypeError):
        return ""
    if authid and description:
        return f"{authid} {description}"
    return authid or description


def layer_kind_line(layer) -> str:
    """``Polygon layer, 1 204 features`` or ``Raster layer``."""
    if layer is None:
        return card_kind_line("")
    return card_kind_line(layer_geometry_label(layer), cheap_feature_count(layer))


class LayerCard(AttachCard):
    """One chip as a card. ``removed(kind, value)`` from the x, ``layer_clicked(id)`` on a click."""

    chip_removed = pyqtSignal(str, str)
    layer_clicked = pyqtSignal(str)

    def __init__(self, chip: dict, parent=None, closable: bool = True):
        chip = dict(chip)
        kind = str(chip.get("kind") or "")
        value = str(chip.get("value") or "")
        layer = resolve_layer(value) if kind == "layer" else None
        pixmap = None
        if layer is not None:
            icon = layer_icon(layer)
            if not icon.isNull():
                pixmap = icon.pixmap(TILE_GLYPH, TILE_GLYPH)
        super().__init__(chip_display(chip), "", pixmap, parent, closable=closable,
                         clickable=(kind == "layer"), glyph=_KIND_GLYPHS.get(kind, "pin"))
        self.chip = chip
        self.kind = kind
        self.value = value
        self.set_kind(layer_kind_line(layer) if kind == "layer" else self._kind_word())
        self.set_tooltip(self._tooltip(layer))
        self.removed.connect(lambda: self.chip_removed.emit(self.kind, self.value))
        self.clicked.connect(lambda: self.layer_clicked.emit(self.value))

    def _kind_word(self) -> str:
        return {
            "selection": self.tr("Selection"),
            "extent": self.tr("Map extent"),
            "field": self.tr("Field"),
            "layout": self.tr("Layout"),
            "file": self.tr("File"),
            "source": self.tr("Data source"),
        }.get(self.kind, self.kind)

    def _tooltip(self, layer) -> str:
        name = chip_display(self.chip)
        if self.kind != "layer":
            return f"{self._kind_word()}: {name}" if name else self._kind_word()
        if layer is None:
            return self.tr("{name}. This layer is no longer in the project.").format(name=name)
        crs = layer_crs_label(layer)
        if crs:
            return self.tr("{name}\nCRS: {crs}\nClick to show it in the Layers panel.").format(
                name=name, crs=crs)
        return self.tr("{name}\nClick to show it in the Layers panel.").format(name=name)
