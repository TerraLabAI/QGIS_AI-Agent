# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a run changed, as chips under its answer, inside the chat itself."""































from __future__ import annotations

import re

from qgis.PyQt.QtCore import QPoint, QRectF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QPainter, QPainterPath
from qgis.PyQt.QtWidgets import QLabel, QMenu, QSizePolicy, QWidget

from ..core.qt_compat import enum_member
from .card_base import mono_font
from .file_links import escape_markdown_label
from .font_scale import scale_point_size, scale_px_length
from .icons import icon_for, pixmap_for
from .layer_icons import layer_name, resolve_layer
from .markdown_view import GONE_SCHEME
from .run_bar import ACTION_SHOW, ACTION_ZOOM
from .shared import event_pos, exec_menu, tr
from .style import (
    CHIP_PX,
    FIELD,
    FONT_HINT,
    GREEN,
    HOVER_ON,
    INK,
    INK_2,
    INK_3,
    ORANGE,
    RADIUS_CHIP,
    RED,
    SPACE_CARD,
    SPACE_TIGHT,
    qcolor,
)


_CHIP_PX = scale_px_length(CHIP_PX)
_CHIP_PAD_PX = 6

_PART_GAP_PX = SPACE_TIGHT
_GLYPH_PX = 12
_TRAILING_PX = 10

_ROW_GAP_PX = SPACE_CARD
_LINE_GAP_PX = SPACE_TIGHT


_FOLDED_LINES = 3



_CHIP_NAME_CHARS = 28
_NOTE_QSS = (
    f"QLabel {{ color: {INK_2}; font-size: {FONT_HINT}px; background: transparent;"
    " border: none; padding: 0; }"
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
    font.setPixelSize(scale_point_size(px))




    font.setWeight(enum_member(QFont, "Weight", "Medium" if weight >= 500 else "Normal"))
    return font


def wrap_lines(widths, available: int, gap: int = _ROW_GAP_PX) -> list[tuple[int, int]]:
    """Where chips of ``widths`` land when they wrap like words: ``(x, line)`` each."""




    placed: list[tuple[int, int]] = []
    x = line = 0
    for width in widths:
        width = int(width)
        if x > 0 and x + width > available:
            line += 1
            x = 0
        placed.append((x, line))
        x += width + gap
    return placed


def folded_count(widths, tail, more_width: int, available: int, lines: int = _FOLDED_LINES,
                 gap: int = _ROW_GAP_PX) -> int:
    """How many chips of ``widths`` stay on a folded row."""






    widths, tail = [int(w) for w in widths], [int(w) for w in tail]

    def fits(row: list) -> bool:
        placed = wrap_lines(row, available, gap)
        return not placed or placed[-1][1] < lines

    if fits(widths + tail):
        return len(widths)
    for keep in range(len(widths) - 1, 0, -1):
        if fits(widths[:keep] + [int(more_width)] + tail):
            return keep
    return min(1, len(widths))


class _Chip(QWidget):
    """One chip of the row: a glyph and words painted on the field, a click emits ``clicked``."""

    clicked = pyqtSignal()

    def __init__(self, glyph: str = "", glyph_ink: str = INK_2, parent=None):
        super().__init__(parent)
        self.glyph = glyph
        self._glyph_ink = glyph_ink
        self._trailing = ""
        self._parts: list = []
        self._hover = False
        self.setFixedHeight(_CHIP_PX)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def set_parts(self, parts) -> None:
        """``[(text, ink, font)]`` left to right."""
        self._parts = list(parts)
        self.update()

    def _refresh_cursor(self) -> None:
        self.setCursor(Qt.CursorShape.PointingHandCursor if self.isEnabled() else Qt.CursorShape.ArrowCursor)

    def _widths(self) -> list:
        from qgis.PyQt.QtGui import QFontMetrics

        return [QFontMetrics(font).horizontalAdvance(text) for text, _ink, font in self._parts]

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        width = 2 * _CHIP_PAD_PX + sum(self._widths()) + _PART_GAP_PX * max(0, len(self._parts) - 1)
        if self.glyph:
            width += _GLYPH_PX + _PART_GAP_PX
        if self._trailing:
            width += _PART_GAP_PX + _TRAILING_PX
        return QSize(width + 2, _CHIP_PX)

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
            path = QPainterPath()
            path.addRoundedRect(QRectF(self.rect()), RADIUS_CHIP, RADIUS_CHIP)
            painter.fillPath(path, qcolor(HOVER_ON if self._hover and self.isEnabled() else FIELD))
            x = _CHIP_PAD_PX
            if self.glyph:
                ink = self._glyph_ink if self.isEnabled() else INK_3
                pixmap = pixmap_for(self, self.glyph, _GLYPH_PX, qcolor(ink))
                painter.drawPixmap(QPoint(int(x), (self.height() - _GLYPH_PX) // 2), pixmap)
                x += _GLYPH_PX + _PART_GAP_PX
            for (text, ink, font), width in zip(self._parts, self._widths()):
                painter.setFont(font)



                painter.setPen(qcolor(ink if self.isEnabled() else (INK_2 if ink == INK else INK_3)))
                painter.drawText(QRectF(x, 0, width + 2, self.height()),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
                x += width + _PART_GAP_PX
            if self._trailing:
                pixmap = pixmap_for(self, self._trailing, _TRAILING_PX, qcolor(INK_3))
                painter.drawPixmap(QPoint(int(x), (self.height() - _TRAILING_PX) // 2), pixmap)
            painter.end()
        except Exception:  # noqa: BLE001 - paint must never raise
            return

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if (self.isEnabled() and event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event_pos(event))):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _LayerChip(_Chip):
    """One changed layer: its kind as a glyph, the name, the counts after it."""


    action_requested = pyqtSignal(str, str)

    def __init__(self, item: dict, parent=None):
        super().__init__(_glyph_name(item), INK_2, parent)
        self.layer_id = str(item.get("id") or "")
        self.layer_name = str(item.get("name") or self.layer_id or "?")
        self.what = str(item.get("what") or "")
        self.item_visible = bool(item.get("visible", True))
        self.added, self.removed, self.changed = _counts(item)
        self.delta = self.added - self.removed
        self.in_project = False
        self._font = _name_font()
        self._count_font = mono_font(FONT_HINT, 500)
        self.follow_project()
        self.clicked.connect(lambda: self.action_requested.emit(self.layer_id, ACTION_SHOW))

    def follow_project(self, gone=frozenset()) -> None:
        """Match the layer as the project holds it now: the name it has, or a grey chip that does nothing once it left the project."""



        name = None if self.layer_id in gone else layer_name(self.layer_id)
        self.in_project = name is not None
        if name:
            self.layer_name = name
        self.setEnabled(self.in_project and self.what != "removed")
        self._refresh_cursor()
        self.setToolTip(self._tooltip_with_name())
        self.set_parts(self._build_parts())



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
        credit = self._credit_line()
        name = str(self.layer_name or "").strip()
        if not name:
            return f"{line}\n{credit}" if credit and line else (line or credit)
        text = f"{name}\n{line}" if line else name
        if credit:
            text = f"{text}\n{credit}"
        try:
            self.setAccessibleName(name)
            self.setAccessibleDescription(line)
        except (RuntimeError, AttributeError):
            pass
        return text

    def _tooltip(self) -> str:
        if self.what == "removed":
            return self.tr("This layer was removed from the project.")
        if not self.in_project:
            return self.tr("This layer is no longer in the project.")
        if self.what == "added":
            return self.tr("New layer. Click to select it in the Layers panel.")
        if self.added or self.removed:
            if abs(self.delta) == 1:
                return self.tr("{n:+d} feature. Click to select it in the Layers panel.").format(n=self.delta)
            return self.tr("{n:+d} features. Click to select it in the Layers panel.").format(n=self.delta)
        if self.what == "crs":
            return self.tr("Its CRS changed. Click to select it in the Layers panel.")
        if self.what == "file":
            return self.tr("Its file was written on disk. Click to select it in the Layers panel.")
        return self.tr("Click to select it in the Layers panel.")

    def _credit_line(self) -> str:
        """The licence and the credit the layer carries, one line each, or ""."""





        if self.what == "removed" or not self.layer_id or not self.in_project:
            return ""
        layer = resolve_layer(self.layer_id)
        if layer is None:
            return ""
        try:
            metadata = layer.metadata()
            licence = ""
            for entry in metadata.licenses():
                value = str(entry or "").strip()
                if value:
                    licence = value
                    break
            credit = str(layer.attribution() or "").strip()
            if not credit:
                for entry in metadata.rights():
                    value = str(entry or "").strip()
                    if value:
                        credit = value
                        break
        except (RuntimeError, AttributeError, TypeError):
            return ""
        if len(licence) > 120:
            licence = licence[:117].rstrip() + "..."
        if len(credit) > 160:
            credit = credit[:157].rstrip() + "..."
        lines = []
        if licence:
            lines.append(self.tr("Licence: {licence}").format(licence=licence))
        if credit:
            lines.append(self.tr("Credit: {credit}").format(credit=credit))
        return "\n".join(lines)



    def contextMenuEvent(self, event):  # noqa: N802 - Qt override
        if self.isEnabled():
            self._open_menu()
        event.accept()

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


class _WarningsChip(_Chip):
    """The run's warnings as one muted chip; a click opens their sentences under the row."""

    def __init__(self, sentences, parent=None):
        super().__init__("warning", ORANGE, parent)
        self.sentences = [str(text) for text in sentences]
        count = len(self.sentences)
        label = self.tr("1 warning") if count == 1 else self.tr("%n warnings", "", count)
        self.set_parts([(label, INK_2, _name_font())])
        self.setToolTip(self.tr("What QGIS found when it read the result back. Click to read it."))
        self.setAccessibleName(label)
        self.set_open(False)
        self._refresh_cursor()

    def set_open(self, opened: bool) -> None:
        self._trailing = "chevron_up" if opened else "chevron_down"
        self.update()


class _MoreChip(_Chip):
    """``+2 more`` in the third ink; a click opens the chips it holds."""

    def __init__(self, parent=None):
        super().__init__("", INK_3, parent)
        self._font = _name_font()
        self.count = -1
        self.set_count(0)
        self._refresh_cursor()

    def _label(self, count: int) -> str:
        return self.tr("+{n} more").format(n=int(count))

    def set_count(self, count: int) -> None:
        if int(count) != self.count:
            self.count = int(count)
            self.set_parts([(self._label(self.count), INK_3, self._font)])

    def width_for(self, count: int) -> int:
        """The width the chip takes holding ``count``, measured without changing it."""
        from qgis.PyQt.QtGui import QFontMetrics

        return 2 * _CHIP_PAD_PX + QFontMetrics(self._font).horizontalAdvance(self._label(count)) + 2


class RunChangesRow(QWidget):
    """What one run changed, as chips under its answer."""










    layer_action_requested = pyqtSignal(str, str)

    def __init__(self, changes, parent=None):
        super().__init__(parent)
        self.setObjectName("runChanges")
        data = changes if isinstance(changes, dict) else {}

        def listed(key: str) -> list:
            value = data.get(key)
            return list(value) if isinstance(value, (list, tuple)) else []

        self._layers = [item for item in listed("layers") if isinstance(item, dict)]
        self._chips: list[_Chip] = []
        for item in self._layers:
            chip = _LayerChip(item, self)
            chip.action_requested.connect(self.layer_action_requested.emit)
            self._chips.append(chip)
        sentences = [text for text in listed("warnings") if isinstance(text, str) and text.strip()]
        self._warnings = _WarningsChip(sentences, self) if sentences else None
        self._notes: list[QLabel] = []
        if self._warnings is not None:
            self._warnings.clicked.connect(self.toggle_warnings)
            for text in sentences:
                note = QLabel(self)
                note.setTextFormat(Qt.TextFormat.PlainText)
                note.setWordWrap(True)
                note.setStyleSheet(_NOTE_QSS)
                note.setText(text)
                note.hide()
                self._notes.append(note)
        self._more = _MoreChip(self) if len(self._chips) > 1 else None
        if self._more is not None:
            self._more.hide()
            self._more.clicked.connect(self.show_all)
        self._all = False
        self._open = False
        policy = self.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Policy.Expanding)
        policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setVisible(not self.is_empty())



    def is_empty(self) -> bool:
        return not self._chips and self._warnings is None

    def layers(self) -> list:
        return list(self._layers)

    def chips(self) -> list:
        return [*self._chips, *([self._warnings] if self._warnings is not None else [])]

    def warnings_open(self) -> bool:
        return self._open

    def toggle_warnings(self) -> None:
        if self._warnings is None:
            return
        self._open = not self._open
        self._warnings.set_open(self._open)
        self._relayout()

    def show_all(self) -> None:
        """Open the chips ``+N more`` was holding."""
        self._all = True
        self._relayout()

    def follow_project(self, gone=frozenset()) -> None:
        """Every layer chip matches its layer as the project holds it now (``layer_watch``); the row flows again when a new name changed a width."""

        widths = [chip.sizeHint().width() for chip in self._chips]
        for chip in self._chips:
            chip.follow_project(gone)
        if widths != [chip.sizeHint().width() for chip in self._chips]:
            self._relayout()



    def _relayout(self) -> None:
        self._arrange(self.width(), place=True)
        self.updateGeometry()

    def _arrange(self, width: int, place: bool) -> int:
        """The height the row needs at ``width``; with ``place``, put every child there."""
        available = max(1, int(width) - 2)
        chips = self._chips
        tail = [self._warnings] if self._warnings is not None else []
        widths = [min(available, chip.sizeHint().width()) for chip in chips]
        tail_widths = [min(available, chip.sizeHint().width()) for chip in tail]
        shown = len(chips)
        if not self._all and self._more is not None:
            shown = folded_count(widths, tail_widths, min(available, self._more.width_for(len(chips))), available)
        hidden = len(chips) - shown
        items = list(chips[:shown])
        item_widths = list(widths[:shown])
        if hidden and self._more is not None:
            items.append(self._more)
            item_widths.append(min(available, self._more.width_for(hidden)))
        items += tail
        item_widths += tail_widths
        placed = wrap_lines(item_widths, available)
        lines = placed[-1][1] + 1 if placed else 0
        height = lines * _CHIP_PX + max(0, lines - 1) * _LINE_GAP_PX
        if place:
            for chip in chips[shown:]:
                chip.setVisible(False)
            if self._more is not None:
                self._more.set_count(hidden)
                self._more.setVisible(bool(hidden))
            for item, (x, line), item_width in zip(items, placed, item_widths):
                item.setGeometry(1 + x, line * (_CHIP_PX + _LINE_GAP_PX), item_width, _CHIP_PX)
                item.setVisible(True)
        note_x = 1 + _CHIP_PAD_PX
        note_width = max(1, available - _CHIP_PAD_PX)
        if self._open and self._notes:
            y = height + _LINE_GAP_PX
            for note in self._notes:
                note_height = note.heightForWidth(note_width)
                if note_height <= 0:
                    note_height = note.sizeHint().height()
                if place:
                    note.setGeometry(note_x, y, note_width, note_height)
                    note.setVisible(True)
                y += note_height + 2
            height = y
        elif place:
            for note in self._notes:
                note.setVisible(False)
        return height

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt override
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt override
        return self._arrange(width, place=False)

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._arrange(self.width(), place=True)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override


        return QSize(0, _CHIP_PX)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(0, _CHIP_PX)

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


_CODE = re.compile(r"```.*?```|~~~.*?~~~|`[^`\n]*`", re.DOTALL)
_LAYER_LINK = re.compile(r"\[((?:\\.|[^\\\]])*)\]\(" + re.escape(LAYER_URL) + r"([^)\s]+)\)")


def follow_layer_links(text: str, gone=frozenset()) -> str:
    """The answer with its layer links as the project holds those layers now."""









    text = (text or "").replace("\x00", "")
    if LAYER_URL not in text:
        return text
    guarded = []

    def keep(match):
        guarded.append(match.group(0))
        return f"\x00{len(guarded) - 1}\x00"

    def restore(match):
        index = int(match.group(1))
        return guarded[index] if 0 <= index < len(guarded) else match.group(0)

    def relink(match):
        layer_id = match.group(2)
        name = None if layer_id in gone else layer_name(layer_id)
        if name is None:

            reason = tr("This layer is no longer in the project.").replace("\\", "").replace('"', "'")
            return f'[{match.group(1)}]({GONE_SCHEME}{LAYER_URL}{layer_id} "{reason}")'
        label = escape_markdown_label(name) if name else match.group(1)
        return f"[{label}]({LAYER_URL}{layer_id})"

    masked = _LAYER_LINK.sub(relink, _CODE.sub(keep, text))
    return re.sub(r"\x00(\d+)\x00", restore, masked)


def layer_names(layers) -> list[str]:
    """The names a run touched, in order, for the answer's own wording."""
    out = []
    for item in layers or []:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if name and name not in out:
                out.append(name)
    return out
