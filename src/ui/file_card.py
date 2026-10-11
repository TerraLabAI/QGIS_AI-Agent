# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

































from __future__ import annotations

import os

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QCursor, QFont, QFontMetrics
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
from .layer_links import _MoreChip
from .shared import event_pos
from .style import FILE_BADGES, FONT_MICRO, HOVER, INK_2, RADIUS_CHIP, ROW_PX, repolish
from .widgets import ElidedLabel, IconButton

ROW_PAD_X = 8
ROW_GAP = 8
GROUP_PAD = 2
BADGE_W = 30
BADGE_H = 16
BADGE_RADIUS = 4

BADGE_WIDEST = "GEOJSON"
ACTION_PX = 24
ACTION_GLYPH = 14


NAME_MIN_WIDTH = 60

FOLD_AT = 3









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
_KINDS = frozenset(("pdf", "html", "image", "table", "vector", "raster", "other"))

_FAMILY = {
    "pdf": "pdf",
    "csv": "table", "tsv": "table", "xlsx": "table", "xls": "table", "ods": "table", "dbf": "table",
    "json": "text", "txt": "text", "md": "text", "py": "text", "js": "text", "xml": "text",
    "yaml": "text", "yml": "text", "log": "text", "sql": "text", "qml": "text", "sld": "text",
    "gpkg": "vector", "shp": "vector", "geojson": "vector", "kml": "vector", "kmz": "vector",
    "fgb": "vector", "parquet": "vector", "gml": "vector", "gpx": "vector", "dxf": "vector",
    "tif": "raster", "tiff": "raster", "vrt": "raster", "jp2": "raster", "asc": "raster",
    "nc": "raster", "img": "raster", "hgt": "raster", "dem": "raster", "ecw": "raster",
    "png": "image", "jpg": "image", "jpeg": "image", "svg": "image", "webp": "image",
    "gif": "image", "bmp": "image",
    "html": "web", "htm": "web",
}


_LOADABLE = frozenset(("vector", "raster", "table"))

_LOADABLE_TABLES = frozenset(("csv", "tsv", "dbf", "parquet"))


def file_kind(path: str, declared: str = "") -> str:

    if declared in _KINDS:
        return declared
    from .file_links import suffix_of

    return _BY_SUFFIX.get(suffix_of(path), "other")


def file_family(path: str) -> str:

    from .file_links import suffix_of

    return _FAMILY.get(suffix_of(path), "other")


def _loadable(path: str, kind: str) -> bool:
    if kind not in _LOADABLE:
        return False
    if kind != "table":
        return True
    from .file_links import suffix_of

    return suffix_of(path) in _LOADABLE_TABLES


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
        self.setObjectName("fileRowHost")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        data = entry if isinstance(entry, dict) else {}
        self.path = str(data.get("path") or "")
        self.kind = file_kind(self.path, str(data.get("kind") or ""))
        self.family = file_family(self.path)
        self._name = os.path.basename(self.path.rstrip("/\\")) or self.path


        self.on_disk = self._exists()

        self._frame = QFrame(self)
        self._frame.setObjectName("fileRow")
        self._frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._frame.setFixedHeight(ROW_PX)
        self._frame.setProperty("gone", not self.on_disk)
        row = QHBoxLayout(self._frame)
        row.setContentsMargins(ROW_PAD_X, 0, ROW_PAD_X, 0)
        row.setSpacing(ROW_GAP)

        self._badge = QLabel(self._badge_text(), self._frame)
        self._badge.setObjectName("fileBadge")
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont(self._badge.font())
        font.setPixelSize(FONT_MICRO)
        font.setWeight(QFont.Weight.DemiBold)
        self._badge.setFont(font)



        metrics = QFontMetrics(font)
        width = max(BADGE_W, metrics.horizontalAdvance(BADGE_WIDEST) + 8)
        full = self._badge.text()
        shown = metrics.elidedText(full, Qt.TextElideMode.ElideRight, width - 6)
        if shown != full:
            self._badge.setText(shown)
        self._badge.setFixedSize(width, BADGE_H)
        self._paint_badge()
        slot = QWidget(self._frame)
        slot.setFixedSize(width, BADGE_H)
        slot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._badge.setParent(slot)
        self._badge.move(0, 0)
        row.addWidget(slot, 0, Qt.AlignmentFlag.AlignVCenter)

        self._name_label = ElidedLabel(self._name, self._frame, Qt.TextElideMode.ElideMiddle)
        self._name_label.setObjectName("fileName")
        self._name_label.setProperty("gone", not self.on_disk)
        self._name_label.setMinimumWidth(NAME_MIN_WIDTH)
        row.addWidget(self._name_label, 1)


        for label in (self._badge, self._name_label):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)





        self._actions = QWidget(self._frame)
        self._actions.setObjectName("fileActions")
        self._actions.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._actions.setStyleSheet(
            f"QWidget#fileActions {{ background: {HOVER}; border: none;"
            f" border-radius: {RADIUS_CHIP}px; }}")
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

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._frame)
        self.setFixedHeight(ROW_PX)
        policy = self.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Policy.Expanding)
        policy.setVerticalPolicy(QSizePolicy.Policy.Fixed)
        self.setSizePolicy(policy)

        self._set_tooltip()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(self._name)
        if self.on_disk:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
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

    def _badge_text(self) -> str:
        from .file_links import suffix_of

        return (suffix_of(self.path) or self.tr("File")).upper()

    def _paint_badge(self) -> None:
        background, ink = FILE_BADGES.get(self.family if self.on_disk else "other", FILE_BADGES["other"])
        self._badge.setStyleSheet(
            f"QLabel#fileBadge {{ background: {background}; color: {ink}; border: none;"
            f" border-radius: {BADGE_RADIUS}px; padding: 0px; }}")

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._place_actions()

    def _place_actions(self) -> None:


        box = self._actions
        box.move(self.width() - 2 - box.width(), (ROW_PX - box.height()) // 2)
        box.raise_()

    def _fire(self, action: str) -> None:
        if self.on_disk and self.path:
            self.action_requested.emit(self.path, action)

    def _exists(self) -> bool:
        try:
            return bool(self.path) and os.path.exists(self.path)
        except (OSError, ValueError):
            return False

    def _open_words(self) -> str:

        if self.kind == "html":
            return self.tr("Open in the browser")
        return self.tr("Open")

    def _set_tooltip(self) -> None:

        tip = self.path if self.on_disk else self.tr(
            "{path}\nThis file is no longer where the run wrote it.").format(path=self.path)
        self.setAccessibleDescription(tip)
        for widget in (self, self._frame, self._name_label, self._badge):
            widget.setToolTip(tip)



    def follow_disk(self) -> None:


        now = self._exists()
        if now == self.on_disk:
            return
        self.on_disk = now
        for widget in (self._frame, self._name_label):
            widget.setProperty("gone", not now)
            repolish(widget)
        self._paint_badge()
        self._actions.setEnabled(now)
        if now:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()
        self._set_tooltip()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._fire("open")
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):  # noqa: N802

        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802

        if event.button() == Qt.MouseButton.LeftButton:
            if self.rect().contains(event_pos(event)):
                self._fire("open")
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._place_actions()

    def focusInEvent(self, event):  # noqa: N802
        super().focusInEvent(event)


        self._actions.setVisible(True)
        self._place_actions()


class _FilesMore(_MoreChip):






    def set_count(self, count: int) -> None:
        super().set_count(count)
        self.setToolTip("")

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

        def entries(value) -> list:
            return [x for x in (value or ()) if isinstance(x, dict) and x.get("path")]

        listed = entries(files)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        self._group = QFrame(self)
        self._group.setObjectName("fileGroup")
        self._group.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        rows = QVBoxLayout(self._group)
        rows.setContentsMargins(GROUP_PAD, GROUP_PAD, GROUP_PAD, GROUP_PAD)
        rows.setSpacing(0)
        column.addWidget(self._group)
        self._cards: list[FileOutputCard] = []
        for entry in listed:
            self._add_card(rows, entry)

        self._more = None
        self._all = False
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(2)
        if len(self._cards) > FOLD_AT:
            self._more = _FilesMore(self)
            self._more.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self._more.clicked.connect(self._toggle)
            footer.addWidget(self._more)
        if footer.count():
            footer.addStretch(1)
            column.addLayout(footer)
        self._fold()
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

        self._all = True
        self._fold()

    def _add_card(self, rows, entry: dict) -> None:
        card = FileOutputCard(entry, self._group)
        card.action_requested.connect(self.action_requested.emit)
        self._cards.append(card)
        rows.addWidget(card)

    def _toggle(self) -> None:
        self._all = not self._all
        self._fold()

    def _fold(self) -> None:
        for index, card in enumerate(self._cards):
            card.setVisible(self._all or index < FOLD_AT)
        if self._more is not None:
            self._more.set_count(0 if self._all else len(self._cards) - FOLD_AT)
            self._more.updateGeometry()
        self.updateGeometry()

    def follow_disk(self) -> None:

        for card in self._cards:
            card.follow_disk()
