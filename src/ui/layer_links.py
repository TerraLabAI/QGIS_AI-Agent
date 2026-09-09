# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a run changed, as layers you can open, inside the chat itself."""
























from __future__ import annotations

import re

from qgis.PyQt.QtCore import QPoint, QRectF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QPainter, QPainterPath
from qgis.PyQt.QtWidgets import QLabel, QMenu, QWidget

from ..core.qt_compat import enum_member
from .card_base import mono_font
from .file_links import escape_markdown_label
from .icons import icon_for, pixmap_for
from .layer_icons import resolve_layer
from .run_bar import ACTION_SHOW, ACTION_ZOOM
from .shared import event_pos, exec_menu
from .style import (
    FONT_HINT,
    GREEN,
    HOVER,
    INK,
    INK_2,
    INK_3,
    LINE,
    LINE_STRONG,
    MONO_FAMILY,
    RADIUS_CONTROL,
    RED,
    SPACE_CARD,
    SURFACE,
    qcolor,
)

_CHIP_PX = 28
_CHIP_PAD_PX = 10
_CHIP_GAP_PX = 6

_GLYPH_PX = 14

_ROW_GAP_PX = SPACE_CARD



_CHIP_NAME_CHARS = 28
_MORE_QSS = (
    f"QLabel {{ font-family: {MONO_FAMILY}; font-size: {FONT_HINT}px; color: {INK_3};"
    " background: transparent; border: none; padding: 0 6px; }"
)


def _short_name(name: str) -> str:
    """A layer name at chip length, cut in the middle so the tail survives."""
    if len(name) <= _CHIP_NAME_CHARS:
        return name
    keep = _CHIP_NAME_CHARS - 1
    head = max(6, keep // 2 - 1)
    return name[:head] + "…" + name[-(keep - head):]


def _counts(item: dict) -> tuple[int, int, int]:
    """``(added, removed, changed)`` of a layer dict: the explicit counts when present, else derived from ``delta``."""

    def whole(value) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    added, removed, changed = whole(item.get("added")), whole(item.get("removed")), whole(item.get("changed"))
    if not added and not removed and "delta" in item:
        delta = whole(item.get("delta"))
        if delta > 0:
            added = delta
        elif delta < 0:
            removed = -delta
    return added, removed, changed





_GEOMETRY_GLYPHS = {
    "point": "points", "points": "points", "multipoint": "points",
    "line": "polyline", "linestring": "polyline", "polyline": "polyline",
    "polygon": "polygon", "area": "polygon", "surface": "polygon",
    "raster": "raster", "table": "table", "nogeometry": "table",
    "none": "table", "null": "table", "vector": "layers", "mesh": "layers",
}
_FALLBACK_GLYPH = "layers"


def _glyph_from_project(layer_id: str) -> str:
    """The glyph for a live layer, or the generic one when it is gone."""
    layer = None
    try:
        layer = resolve_layer(layer_id) if layer_id else None
    except Exception:  # noqa: BLE001 - no QGIS project is not a crash here
        layer = None
    if layer is None:
        return _FALLBACK_GLYPH
    try:
        from qgis.core import QgsRasterLayer, QgsVectorLayer, QgsWkbTypes

        if isinstance(layer, QgsRasterLayer):
            return "raster"
        if isinstance(layer, QgsVectorLayer):
            return {
                QgsWkbTypes.GeometryType.PointGeometry: "points",
                QgsWkbTypes.GeometryType.LineGeometry: "polyline",
                QgsWkbTypes.GeometryType.PolygonGeometry: "polygon",
            }.get(layer.geometryType(), "table")
    except Exception:  # noqa: BLE001 - an older QGIS keeps the generic glyph
        return _FALLBACK_GLYPH
    return _FALLBACK_GLYPH


def _glyph_name(item: dict) -> str:
    """The glyph of one layer item: its own ``kind``/``geometry`` first, the live layer next, the generic layer glyph last."""

    kind = str(item.get("kind") or "").strip().lower()
    geometry = str(item.get("geometry") or "").strip().lower()
    if kind == "raster":
        return "raster"
    for key in (geometry, kind):
        glyph = _GEOMETRY_GLYPHS.get(key)
        if glyph and glyph != "layers":
            return glyph
    return _glyph_from_project(str(item.get("id") or ""))


def _name_font(px: int = FONT_HINT, weight: int = 500):
    """The panel's own face at chip size: a layer name is prose, not a token."""
    from qgis.PyQt.QtGui import QFont, QGuiApplication

    font = QFont(QGuiApplication.font())
    font.setPixelSize(px)
    try:
        font.setWeight(QFont.Weight(weight))
    except Exception:  # noqa: BLE001 - Qt5 takes the 0-99 scale
        font.setWeight(enum_member(QFont, "Weight", "Medium"))
    return font


def visible_count(widths, more_width: int, available: int, gap: int = _ROW_GAP_PX) -> int:
    """How many chips of ``widths`` fit on one row ``available`` pixels wide."""





    widths = [int(w) for w in widths]
    if not widths:
        return 0
    total = sum(widths) + gap * (len(widths) - 1)
    if total <= available:
        return len(widths)
    used = 0
    fits = 0
    for index, width in enumerate(widths):
        used += width if index == 0 else gap + width
        if used + gap + more_width <= available:
            fits = index + 1
        else:
            break
    return max(1, fits)


class _LayerChip(QWidget):
    """One changed layer: its kind as a glyph, the name, the counts after it."""


    action_requested = pyqtSignal(str, str)

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.layer_id = str(item.get("id") or "")
        self.layer_name = str(item.get("name") or self.layer_id or "?")
        self.what = str(item.get("what") or "")
        self.item_visible = bool(item.get("visible", True))
        self.added, self.removed, self.changed = _counts(item)
        self.delta = self.added - self.removed
        self._hover = False
        self._font = _name_font()
        self._count_font = mono_font(FONT_HINT, 500)
        self.glyph = _glyph_name(item)
        self.setFixedHeight(_CHIP_PX)
        self.setEnabled(bool(self.layer_id) and self.what != "removed")
        self.setCursor(Qt.CursorShape.PointingHandCursor if self.isEnabled()
                       else Qt.CursorShape.ArrowCursor)
        self.setToolTip(self._tooltip_with_name())
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._parts = self._build_parts()



    def _build_parts(self) -> list:
        """``[(text, colour, font)]`` left to right."""
        parts = [(_short_name(self.layer_name), INK, self._font)]
        if self.added:
            parts.append((f"+{self.added}", GREEN, self._count_font))
        if self.removed:
            parts.append((f"-{self.removed}", RED, self._count_font))
        if self.changed and not self.added and not self.removed:
            parts.append((f"~{self.changed}", INK_2, self._count_font))
        if len(parts) == 1:
            word = self._what_word()
            if word:
                parts.append((word, INK_3, self._font))
        return parts

    def _what_word(self) -> str:
        return {
            "added": self.tr("new"),
            "removed": self.tr("removed"),
            "crs": self.tr("CRS"),
            "file": self.tr("saved"),
            "style": self.tr("styled"),
            "visibility": self.tr("shown") if self.item_visible else self.tr("hidden"),
        }.get(self.what, "")

    def _tooltip_with_name(self) -> str:
        """The chip's own sentence, under the layer's whole name."""






        line = self._tooltip()
        name = str(self.layer_name or "").strip()
        if not name:
            return line
        text = f"{name}\n{line}" if line else name
        try:
            self.setAccessibleName(name)
            self.setAccessibleDescription(line)
        except (RuntimeError, AttributeError):
            pass
        return text

    def _tooltip(self) -> str:
        if self.what == "added":
            return self.tr("New layer. Click to open it in QGIS.")
        if self.what == "removed":
            return self.tr("This layer was removed from the project.")
        if self.added or self.removed:
            if abs(self.delta) == 1:
                return self.tr("{n:+d} feature. Click to open it in QGIS.").format(n=self.delta)
            return self.tr("{n:+d} features. Click to open it in QGIS.").format(n=self.delta)
        if self.what == "crs":
            return self.tr("Its CRS changed. Click to open it in QGIS.")
        if self.what == "file":
            return self.tr("Its file was written on disk. Click to open it in QGIS.")
        return self.tr("Click to open it in QGIS.")



    def _widths(self) -> list:
        from qgis.PyQt.QtGui import QFontMetrics

        return [QFontMetrics(font).horizontalAdvance(text) for text, _colour, font in self._parts]

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        widths = self._widths()
        width = (2 * _CHIP_PAD_PX + _GLYPH_PX + _CHIP_GAP_PX + sum(widths)
                 + _CHIP_GAP_PX * (len(widths) - 1) + 2)
        return QSize(width, _CHIP_PX)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return self.sizeHint()

    def enterEvent(self, event):  # noqa: N802 - Qt override
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):  # noqa: N802 - Qt override
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            path = QPainterPath()
            path.addRoundedRect(rect, RADIUS_CONTROL, RADIUS_CONTROL)
            painter.fillPath(path, qcolor(HOVER if self._hover and self.isEnabled() else SURFACE))
            painter.setPen(qcolor(LINE_STRONG if self._hover and self.isEnabled() else LINE))
            painter.drawPath(path)
            x = _CHIP_PAD_PX
            glyph_ink = INK_2 if self.isEnabled() else INK_3
            pixmap = pixmap_for(self, self.glyph, _GLYPH_PX, qcolor(glyph_ink))
            painter.drawPixmap(QPoint(int(x), (self.height() - _GLYPH_PX) // 2), pixmap)
            x += _GLYPH_PX + _CHIP_GAP_PX
            for (text, colour, font), width in zip(self._parts, self._widths()):
                painter.setFont(font)
                painter.setPen(qcolor(colour if self.isEnabled() or colour != INK else INK_2))
                painter.drawText(QRectF(x, 0, width + 2, self.height()),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
                x += width + _CHIP_GAP_PX
            painter.end()
        except Exception:  # noqa: BLE001 - paint must never raise
            return



    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if (self.isEnabled() and event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event_pos(event))):
            self._open_menu()
        super().mouseReleaseEvent(event)

    def _open_menu(self) -> None:
        menu = QMenu(self)



        entries = (
            (ACTION_SHOW, "layers", self.tr("Show in Layers panel")),
            (ACTION_ZOOM, "expand", self.tr("Zoom to layer")),
        )
        for action, glyph, label in entries:
            act = menu.addAction(icon_for(menu, glyph, 16), label)
            act.triggered.connect(lambda _=False, a=action: self.action_requested.emit(self.layer_id, a))
        exec_menu(menu, self.mapToGlobal(QPoint(0, self.height() + 2)))
        menu.deleteLater()


class _MoreLabel(QLabel):
    """``+2 more`` in the third ink; a click opens the rest."""

    clicked = pyqtSignal()

    def __init__(self, count: int, parent=None):
        super().__init__(parent)
        self.setStyleSheet(_MORE_QSS)
        self.set_count(count)
        self.setFixedHeight(_CHIP_PX)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_count(self, count: int) -> None:
        """How many chips it is holding; the row changes this on every resize."""
        self.count = int(count)
        self.setText(self.tr("+{n} more").format(n=self.count))
        self.adjustSize()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event_pos(event)):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class LayerLinkRow(QWidget):
    """The changed layers of one run, on one line, with ``+N more`` for the rest."""








    layer_action_requested = pyqtSignal(str, str)

    def __init__(self, layers, parent=None):
        super().__init__(parent)
        self.setObjectName("layerLinks")
        items = [x for x in (layers or []) if isinstance(x, dict)]
        self.count = len(items)
        self._expanded = False
        self._chips: list[_LayerChip] = []
        for item in items:
            chip = _LayerChip(item, self)
            chip.action_requested.connect(self.layer_action_requested.emit)
            self._chips.append(chip)
        self._more = _MoreLabel(0, self) if len(items) > 1 else None
        if self._more is not None:
            self._more.clicked.connect(self.show_all)
        self.setFixedHeight(_CHIP_PX if items else 0)
        self.setVisible(bool(items))
        self._relayout()



    def _available(self) -> int:
        return max(0, self.width() - 4)

    def _relayout(self) -> None:
        """Place the chips for the width the row has now."""
        if not self._chips:
            return
        widths = [chip.sizeHint().width() for chip in self._chips]
        if self._expanded:
            self._lay_out_wrapped(widths)
            return
        hidden = len(self._chips)
        more_width = 0
        if self._more is not None:
            self._more.set_count(max(1, hidden - 1))
            more_width = self._more.sizeHint().width()
        shown = visible_count(widths, more_width, self._available())
        x = 2
        for index, chip in enumerate(self._chips):
            if index < shown:
                chip.setGeometry(x, 0, widths[index], _CHIP_PX)
                chip.setVisible(True)
                x += widths[index] + _ROW_GAP_PX
            else:
                chip.setVisible(False)
        rest = len(self._chips) - shown
        if self._more is not None:
            self._more.set_count(rest)
            self._more.setVisible(bool(rest))
            if rest:
                self._more.setGeometry(x, 0, self._more.sizeHint().width(), _CHIP_PX)
        self.setFixedHeight(_CHIP_PX)

    def _lay_out_wrapped(self, widths) -> None:
        """Every chip, wrapped onto as many lines as the width needs."""
        if self._more is not None:
            self._more.setVisible(False)
        available = max(1, self._available())
        x, y = 2, 0
        for chip, width in zip(self._chips, widths):
            if x > 2 and x + width > available:
                x, y = 2, y + _CHIP_PX + _ROW_GAP_PX
            chip.setGeometry(x, y, width, _CHIP_PX)
            chip.setVisible(True)
            x += width + _ROW_GAP_PX
        self.setFixedHeight(y + _CHIP_PX)

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._relayout()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(0, self.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(0, self.height())

    def show_all(self) -> None:
        """Open the chips ``+N more`` was holding."""
        self._expanded = True
        self._relayout()
        self.updateGeometry()

    def chips(self) -> list:
        return list(self._chips)

    def to_markdown(self) -> str:
        return ""


LAYER_URL = "qgis-layer:"
_FENCE = re.compile(r"```.*?```|~~~.*?~~~|`[^`\n]*`|\[[^\]]*\]\([^)]*\)", re.DOTALL)


def linkify_layers(text: str, layers) -> str:
    """The answer with every mention of a changed layer turned into a link."""









    text = (text or "").replace("\x00", "")
    named = [(str(i.get("name") or "").strip(), str(i.get("id") or ""))
             for i in (layers or []) if isinstance(i, dict)]
    named = [(name, lid) for name, lid in named if name and lid and len(name) > 2]
    if not named or not text:
        return text
    named.sort(key=lambda pair: len(pair[0]), reverse=True)


    target = {}
    for name, lid in named:
        target.setdefault(name, lid)


    guarded = []

    def keep(match):
        guarded.append(match.group(0))
        return f"\x00{len(guarded) - 1}\x00"

    def restore(match):
        index = int(match.group(1))
        return guarded[index] if 0 <= index < len(guarded) else match.group(0)

    masked = _FENCE.sub(keep, text)










    pattern = re.compile("|".join(rf"(?<![\w`]){re.escape(name)}(?![\w`])"
                                  for name in target))
    masked = pattern.sub(
        lambda m: f"[{escape_markdown_label(m.group(0))}]({LAYER_URL}{target[m.group(0)]})", masked)
    return re.sub(r"\x00(\d+)\x00", restore, masked)


def layer_id_from_url(url: str) -> str:
    """The layer id of a ``qgis-layer:`` link, else ""."""
    url = str(url or "")
    return url[len(LAYER_URL):] if url.startswith(LAYER_URL) else ""


def layer_names(layers) -> list[str]:
    """The names a run touched, in order, for the answer's own wording."""
    out = []
    for item in layers or []:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if name and name not in out:
                out.append(name)
    return out
