# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
























from __future__ import annotations

from qgis.PyQt.QtCore import pyqtSignal

from ..core.context import layer_geometry_label
from ..core.layer_mime import card_kind_line, cheap_feature_count
from .attach_card import TILE_GLYPH, AttachCard
from .font_scale import widget_pixel_ratio
from .layer_icons import follows_layers, layer_icon, layer_name, resolve_layer

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

    if layer is None:
        return card_kind_line("")
    return card_kind_line(layer_geometry_label(layer), cheap_feature_count(layer))


class LayerCard(AttachCard):



    chip_removed = pyqtSignal(str, str)
    layer_clicked = pyqtSignal(str)
    source_clicked = pyqtSignal(str)

    def __init__(self, chip: dict, parent=None, closable: bool = True):
        chip = dict(chip)
        kind = str(chip.get("kind") or "")
        value = str(chip.get("value") or "")
        layer = resolve_layer(value) if kind == "layer" else None
        super().__init__(chip_display(chip), "", None, parent, closable=closable,
                         clickable=(kind == "layer" or (kind == "source" and bool(value))),
                         glyph=_KIND_GLYPHS.get(kind, "pin"))
        if layer is not None:
            self._set_layer_icon(layer_icon(layer))
        self.chip = chip
        self.kind = kind
        self.value = value
        self.in_project = layer is not None
        self.removed.connect(lambda: self.chip_removed.emit(self.kind, self.value))
        self.clicked.connect(self._on_click)
        if kind == "layer":
            self.follow_project()
            follows_layers(self)
        else:
            self.set_kind(self._kind_word())
            self.set_tooltip(self._tooltip(None))

    def _on_click(self) -> None:
        if self.kind == "source":
            self.source_clicked.emit(self.value)
        else:
            self.layer_clicked.emit(self.value)

    def _set_layer_icon(self, icon) -> None:








        if icon is None or icon.isNull():
            return
        side = max(1, int(round(TILE_GLYPH * widget_pixel_ratio(self))))
        pixmap = icon.pixmap(side, side)
        if pixmap.isNull():
            return
        pixmap.setDevicePixelRatio(max(1.0, max(pixmap.width(), pixmap.height()) / float(TILE_GLYPH)))
        self._tile.setPixmap(pixmap)

    def follow_project(self, gone=frozenset()) -> None:



        if self.kind != "layer":
            return
        name = None if self.value in gone else layer_name(self.value)
        layer = resolve_layer(self.value) if name is not None else None
        self.in_project = layer is not None
        if name:
            self.set_name(name)
        self.set_clickable(self.in_project)
        self.setEnabled(self.in_project or self._closable)
        self.set_kind(layer_kind_line(layer) if self.in_project else self.tr("Not in the project"))
        self.set_tooltip(self._tooltip(layer))

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
        if self.kind == "source" and self.value:
            return self.tr("{name}\nClick to open its page.").format(name=chip_display(self.chip))
        if self.kind != "layer":
            name = chip_display(self.chip)
            return f"{self._kind_word()}: {name}" if name else self._kind_word()
        name = self.name() or chip_display(self.chip)
        if layer is None:
            return self.tr("{name}. This layer is no longer in the project.").format(name=name)
        crs = layer_crs_label(layer)
        if crs:
            return self.tr("{name}\nCRS: {crs}\nClick to show it in the Layers panel.").format(
                name=name, crs=crs)
        return self.tr("{name}\nClick to show it in the Layers panel.").format(name=name)
