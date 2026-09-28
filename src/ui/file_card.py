# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




























from __future__ import annotations

import os

from qgis.PyQt.QtCore import QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QCursor, QPixmap
from qgis.PyQt.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .attach_card import HoverReveal
from .icons import pixmap_for
from .style import INK_2, RADIUS_CONTROL, SPACE_CARD, repolish
from .widgets import ElidedLabel, IconButton

CARD_RADIUS = 10
TILE_SIZE = 36
TILE_GLYPH = 20
CARD_PAD_X = 8
CARD_PAD_Y = 8
CARD_GAP = 10
CARD_HEIGHT = TILE_SIZE + 2 * CARD_PAD_Y
ACTION_PX = 26
ACTION_GLYPH = 14


NAME_MIN_WIDTH = 60


FOLD_AT = 4


THUMBNAIL_MAX_BYTES = 12 * 1024 * 1024








_BY_SUFFIX = {
    "pdf": "pdf",
    "html": "html", "htm": "html",
    "png": "image", "jpg": "image", "jpeg": "image", "gif": "image",
    "bmp": "image", "webp": "image", "svg": "image",
    "csv": "table", "tsv": "table", "xlsx": "table", "xls": "table",
    "ods": "table", "dbf": "table", "parquet": "table",
    "gpkg": "vector", "shp": "vector", "geojson": "vector", "json": "vector",
    "kml": "vector", "kmz": "vector", "gml": "vector", "gpx": "vector",
    "dxf": "vector", "fgb": "vector", "tab": "vector", "mif": "vector",
    "tif": "raster", "tiff": "raster", "img": "raster", "jp2": "raster",
    "asc": "raster", "vrt": "raster", "nc": "raster", "hgt": "raster",
    "dem": "raster", "ecw": "raster", "sid": "raster",
}

_GLYPHS = {
    "pdf": "file_pdf",
    "html": "file_html",
    "image": "image",
    "table": "file_table",
    "vector": "file_vector",
    "raster": "file_raster",
    "other": "file",
}


_LOADABLE = frozenset(("vector", "raster", "table"))

_LOADABLE_TABLES = frozenset(("csv", "tsv", "dbf", "parquet"))
_THUMBNAIL_SUFFIXES = frozenset(("png", "jpg", "jpeg", "gif", "bmp", "webp"))


def file_kind(path: str, declared: str = "") -> str:

    if declared in _GLYPHS:
        return declared
    from .file_links import suffix_of

    return _BY_SUFFIX.get(suffix_of(path), "other")


def glyph_for(kind: str) -> str:
    return _GLYPHS.get(kind, "file")


def size_words(size_bytes) -> str:






    try:
        value = float(size_bytes)
    except (TypeError, ValueError):
        return ""
    if value < 0:
        return ""
    if value < 1024:
        return f"{int(value)} B"
    for unit in ("KB", "MB", "GB"):
        value /= 1024.0
        if value < 1024 or unit == "GB":

            number = f"{value:.1f}" if value < 10 else f"{value:.0f}"
            return f"{number} {unit}"
    return ""


def _loadable(path: str, kind: str) -> bool:
    if kind not in _LOADABLE:
        return False
    if kind != "table":
        return True
    from .file_links import suffix_of

    return suffix_of(path) in _LOADABLE_TABLES


def _thumbnail(widget, path: str, kind: str, size_bytes) -> QPixmap | None:






    try:
        if isinstance(size_bytes, int) and size_bytes > THUMBNAIL_MAX_BYTES:
            return None
        from .file_links import suffix_of
        from .font_scale import widget_pixel_ratio

        ratio = widget_pixel_ratio(widget)
        side = max(1, int(round(TILE_SIZE * ratio)))
        suffix = suffix_of(path)
        pixmap = None
        if kind == "image" and suffix in _THUMBNAIL_SUFFIXES:
            pixmap = QPixmap(path)
            if pixmap.isNull():
                return None
        elif kind == "pdf":
            pixmap = _pdf_first_page(path, side)
        if pixmap is None or pixmap.isNull():
            return None
        scaled = pixmap.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(ratio)
        return scaled
    except Exception:  # noqa: BLE001
        return None


def _pdf_first_page(path: str, side: int) -> QPixmap | None:

    try:
        from qgis.PyQt.QtPdf import QPdfDocument
    except Exception:  # noqa: BLE001
        return None
    try:
        document = QPdfDocument(None)
        document.load(path)



        if document.pageCount() < 1:
            return None
        image = document.render(0, QSize(side, side))
        return QPixmap.fromImage(image) if image is not None and not image.isNull() else None
    except Exception:  # noqa: BLE001
        return None


class _ActionReveal(HoverReveal):









    def sync(self) -> None:
        try:
            host, badge = self._host, self._badge
            inside = host.rect().contains(host.mapFromGlobal(QCursor.pos()))
            show = bool(inside) or _focus_inside(host)
            badge.setVisible(show)
            if show:
                host._place_actions()  # noqa: SLF001
        except RuntimeError:
            pass


def _focus_inside(widget: QWidget) -> bool:

    try:
        focused = QApplication.focusWidget()
    except RuntimeError:
        return False
    while focused is not None:
        if focused is widget:
            return True
        focused = focused.parentWidget()
    return False


class FileOutputCard(QWidget):







    action_requested = pyqtSignal(str, str)

    def __init__(self, entry: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("fileCardHost")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        data = entry if isinstance(entry, dict) else {}
        self.path = str(data.get("path") or "")
        self.kind = file_kind(self.path, str(data.get("kind") or ""))
        self._name = os.path.basename(self.path.rstrip("/\\")) or self.path
        self._size = data.get("size_bytes")


        self.on_disk = self._exists()

        self._frame = QFrame(self)
        self._frame.setObjectName("fileCard")
        self._frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._frame.setFixedHeight(CARD_HEIGHT)
        self._frame.setProperty("gone", not self.on_disk)
        row = QHBoxLayout(self._frame)
        row.setContentsMargins(CARD_PAD_X, CARD_PAD_Y, CARD_PAD_X, CARD_PAD_Y)
        row.setSpacing(CARD_GAP)

        self._tile = QLabel(self._frame)
        self._tile.setObjectName("fileTile")
        self._tile.setFixedSize(TILE_SIZE, TILE_SIZE)
        self._tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tile.setProperty("gone", not self.on_disk)
        self._paint_tile()
        row.addWidget(self._tile, 0, Qt.AlignmentFlag.AlignVCenter)

        lines = QVBoxLayout()
        lines.setContentsMargins(0, 0, 0, 0)
        lines.setSpacing(1)
        self._name_label = ElidedLabel(self._name, self._frame, Qt.TextElideMode.ElideMiddle)
        self._name_label.setObjectName("fileName")
        self._name_label.setProperty("gone", not self.on_disk)
        self._name_label.setMinimumWidth(NAME_MIN_WIDTH)
        lines.addWidget(self._name_label)
        self._kind_label = QLabel(self._subtitle(), self._frame)
        self._kind_label.setObjectName("fileKind")
        lines.addWidget(self._kind_label)
        row.addLayout(lines, 1)






        self._actions = QWidget(self._frame)
        self._actions.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        actions = QHBoxLayout(self._actions)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(2)
        self._buttons: list[IconButton] = []
        if _loadable(self.path, self.kind):
            self._add_action(actions, "layers", "add", self.tr("Add to map"))
        self._add_action(actions, "folder", "reveal", self.tr("Show in folder"))
        self._add_action(actions, "external", "open", self._open_words())
        width = len(self._buttons) * ACTION_PX + (len(self._buttons) - 1) * 2
        self._actions.setFixedSize(width, ACTION_PX)
        row.setContentsMargins(CARD_PAD_X, CARD_PAD_Y, CARD_PAD_X + width + CARD_GAP, CARD_PAD_Y)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._frame)
        self.setFixedHeight(CARD_HEIGHT)
        policy = self.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Policy.Expanding)
        policy.setVerticalPolicy(QSizePolicy.Policy.Fixed)
        self.setSizePolicy(policy)

        self._set_tooltip()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(self._name)


        self._reveal = _ActionReveal(self, self._actions)
        self._actions.setEnabled(self.on_disk)



    def _add_action(self, layout, glyph: str, action: str, tooltip: str) -> None:
        button = IconButton(self._actions, None, ACTION_GLYPH, tooltip)
        button.set_icon(glyph, ACTION_GLYPH, QColor(INK_2))
        button.setFixedSize(ACTION_PX, ACTION_PX)


        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.clicked.connect(lambda _=False, a=action: self._fire(a))
        layout.addWidget(button)
        self._buttons.append(button)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._place_actions()

    def _place_actions(self) -> None:








        box = self._actions
        box.move(self.width() - CARD_PAD_X - box.width(),
                 (CARD_HEIGHT - box.height()) // 2)
        box.raise_()

    def _fire(self, action: str) -> None:
        if self.on_disk and self.path:
            self.action_requested.emit(self.path, action)

    def _exists(self) -> bool:
        try:
            return bool(self.path) and os.path.exists(self.path)
        except (OSError, ValueError):
            return False

    def _paint_tile(self) -> None:
        thumbnail = _thumbnail(self, self.path, self.kind, self._size) if self.on_disk else None
        if thumbnail is not None:

            self._tile.setPixmap(_rounded(thumbnail, TILE_SIZE, RADIUS_CONTROL))
            return
        ink = QColor(INK_2) if not self.on_disk else None
        self._tile.setPixmap(pixmap_for(self, glyph_for(self.kind), TILE_GLYPH, ink))

    def _open_words(self) -> str:



        if self.kind == "html":
            return self.tr("Open in the browser")
        return self.tr("Open")

    def _kind_words(self) -> str:
        from .file_links import suffix_of

        suffix = suffix_of(self.path)
        if suffix:
            return suffix.upper()
        return {
            "pdf": self.tr("PDF"), "html": self.tr("Web page"),
            "image": self.tr("Image"), "table": self.tr("Table"),
            "vector": self.tr("Vector data"), "raster": self.tr("Raster data"),
        }.get(self.kind, self.tr("File"))

    def _subtitle(self) -> str:
        if not self.on_disk:
            return self.tr("Not on disk any more")
        size = size_words(self._size if self._size is not None else self._read_size())
        words = self._kind_words()
        return f"{words} · {size}" if size else words

    def _read_size(self):
        try:
            return os.path.getsize(self.path)
        except (OSError, ValueError):
            return None

    def _set_tooltip(self) -> None:


        tip = self.path if self.on_disk else self.tr(
            "{path}\nThis file is no longer where the run wrote it.").format(path=self.path)
        self.setAccessibleDescription(tip)
        for widget in (self._frame, self._name_label, self._kind_label, self._tile):
            widget.setToolTip(tip)



    def follow_disk(self) -> None:


        now = self._exists()
        if now == self.on_disk:
            return
        self.on_disk = now
        for widget in (self._frame, self._tile, self._name_label):
            widget.setProperty("gone", not now)
            repolish(widget)
        self._size = self._read_size()
        self._kind_label.setText(self._subtitle())
        self._paint_tile()
        self._actions.setEnabled(now)
        self._set_tooltip()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._fire("open")
            event.accept()
            return
        super().keyPressEvent(event)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._place_actions()

    def focusInEvent(self, event):  # noqa: N802
        super().focusInEvent(event)


        self._actions.setVisible(True)
        self._place_actions()


def _rounded(pixmap: QPixmap, side: int, radius: int) -> QPixmap:

    from qgis.PyQt.QtCore import QRectF
    from qgis.PyQt.QtGui import QPainter, QPainterPath

    ratio = pixmap.devicePixelRatio() or 1.0
    physical = max(1, int(round(side * ratio)))
    out = QPixmap(physical, physical)
    out.setDevicePixelRatio(ratio)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, side, side), radius, radius)
        painter.setClipPath(path)


        x = (side - pixmap.width() / ratio) / 2.0
        y = (side - pixmap.height() / ratio) / 2.0
        painter.drawPixmap(int(round(x)), int(round(y)), pixmap)
    finally:
        painter.end()
    return out


class _MoreRow(QLabel):






    clicked = pyqtSignal()

    def __init__(self, hidden: int, parent=None):
        super().__init__(parent)
        self.setObjectName("fileMore")
        self.setText(self.tr("+%n more", "", int(hidden)))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(self.text())

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class FileCardStack(QWidget):







    action_requested = pyqtSignal(str, str)

    def __init__(self, files, parent=None):
        super().__init__(parent)
        self.setObjectName("fileCardStack")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        listed = [x for x in (files or ()) if isinstance(x, dict) and x.get("path")]
        self._column = QVBoxLayout(self)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(SPACE_CARD)
        self._cards: list[FileOutputCard] = []
        for entry in listed:
            card = FileOutputCard(entry, self)
            card.action_requested.connect(self.action_requested.emit)
            self._cards.append(card)
            self._column.addWidget(card)
        self._more = None
        hidden = len(self._cards) - FOLD_AT
        if hidden > 0:
            for card in self._cards[FOLD_AT:]:
                card.hide()
            self._more = _MoreRow(hidden, self)
            self._more.clicked.connect(self.show_all)
            self._column.addWidget(self._more)
        policy = self.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Policy.Expanding)
        policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
        self.setSizePolicy(policy)
        self.setVisible(not self.is_empty())

    def is_empty(self) -> bool:
        return not self._cards

    def cards(self) -> list:
        return list(self._cards)

    def show_all(self) -> None:
        for card in self._cards:
            card.show()
        if self._more is not None:
            self._more.hide()
        self.updateGeometry()

    def follow_disk(self) -> None:

        for card in self._cards:
            card.follow_disk()
