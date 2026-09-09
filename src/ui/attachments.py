# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a message carries besides text: images and data files."""













from __future__ import annotations

import base64
import binascii
import os
import uuid

from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice, QRectF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QImage, QImageReader, QPainter, QPainterPath, QPixmap
from qgis.PyQt.QtWidgets import QFrame, QVBoxLayout

from .attach_card import (
    CARD_HEIGHT,
    CARD_RADIUS,
    CLOSE_OVERHANG,
    CLOSE_SIZE,
    TILE_GLYPH,
    AttachCard,
    HoverReveal,
)
from .font_scale import widget_pixel_ratio
from .icons import ink_of, paper_of, theme_pixmap
from .image_preview import ClickableThumb, open_image_preview
from .style import _BTN_THUMB_CLOSE
from .widgets import IconButton

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
DATA_EXTS = (".gpkg", ".geojson", ".json", ".shp", ".csv", ".tif", ".tiff",
             ".kml", ".kmz", ".zip", ".gml", ".fgb", ".parquet")


def _patterns(exts) -> str:
    """The ``*.ext`` list a QFileDialog filter takes, from the same tuple ``classify`` reads."""



    return " ".join("*" + ext for ext in exts)


IMAGE_PATTERNS = _patterns(IMAGE_EXTS)
DATA_PATTERNS = _patterns(DATA_EXTS)
ALL_PATTERNS = _patterns(IMAGE_EXTS + DATA_EXTS)


def image_filter() -> str:
    return f"{tr('Images')} ({IMAGE_PATTERNS})"


def data_filter() -> str:
    return f"{tr('Data files')} ({DATA_PATTERNS})"


def any_filter() -> str:
    """One picker for both, the way ChatGPT and Claude offer one "Add files"."""





    return (f"{tr('Files and photos')} ({ALL_PATTERNS});;"
            f"{image_filter()};;{data_filter()}")





MAX_IMAGE_SIDE = 2048
MAX_IMAGE_PIXELS = MAX_IMAGE_SIDE * MAX_IMAGE_SIDE * 4
MAX_ATTACHMENTS = 24




ATTACHMENT_BUDGET_BYTES = 12 * 1024 * 1024
MAX_IMAGE_FILE_BYTES = 64 * 1024 * 1024



MAX_IMAGE_BASE64 = 16 * 1024 * 1024

THUMB_HEIGHT = 36
THUMB_MAX_WIDTH = 64




TILE_HEIGHT = CARD_HEIGHT

TILE_MAX_ASPECT = 2.0
TILE_RADIUS = CARD_RADIUS
TAG_HEIGHT = 22


FILE_TYPES = {
    ".gpkg": "GeoPackage", ".geojson": "GeoJSON", ".json": "JSON",
    ".shp": "Shapefile", ".csv": "CSV", ".tif": "GeoTIFF", ".tiff": "GeoTIFF",
    ".kml": "KML", ".kmz": "KMZ", ".zip": "ZIP", ".gml": "GML",
    ".fgb": "FlatGeobuf", ".parquet": "Parquet",
}





FILE_ICONS = {
    ".gpkg": ("/mGeoPackage.svg", "layers"),
    ".geojson": ("/mIconVector.svg", "layers"),
    ".shp": ("/mIconVector.svg", "layers"),
    ".gml": ("/mIconVector.svg", "layers"),
    ".fgb": ("/mIconVector.svg", "layers"),
    ".kml": ("/mIconVector.svg", "layers"),
    ".kmz": ("/mIconVector.svg", "layers"),
    ".parquet": ("/mIconTableLayer.svg", "layers"),
    ".csv": ("/mIconDelimitedText.svg", "file"),
    ".json": ("/mIconFieldJson.svg", "file"),
    ".tif": ("/mIconRaster.svg", "image"),
    ".tiff": ("/mIconRaster.svg", "image"),
    ".zip": ("/mIconZip.svg", "file"),
}
_DEFAULT_FILE_ICON = ("/mIconFile.svg", "file")


def classify(path: str) -> str | None:
    """``image``, ``file`` or None for a path the composer does not take."""
    ext = os.path.splitext(str(path))[1].lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in DATA_EXTS:
        return "file"
    return None


def reduced_image(image: QImage) -> QImage:
    """``image`` at the size it will be encoded at, MAX_IMAGE_SIDE at most."""
    if image is None or image.isNull() or image.width() <= 0 or image.height() <= 0:
        return image
    if image.width() * image.height() > MAX_IMAGE_PIXELS * 16:
        image = image.scaled(MAX_IMAGE_SIDE * 2, MAX_IMAGE_SIDE * 2,
                             Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    if max(image.width(), image.height()) > MAX_IMAGE_SIDE:
        image = image.scaled(MAX_IMAGE_SIDE, MAX_IMAGE_SIDE,
                             Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    return image


def image_to_png_base64(image: QImage) -> str:
    """PNG bytes of ``image``, base64, downscaled past MAX_IMAGE_SIDE."""
    if image is None or image.isNull() or image.width() <= 0 or image.height() <= 0:
        return ""
    image = reduced_image(image)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return base64.b64encode(bytes(data)).decode("ascii")


def image_attachment(image: QImage, name: str, path: str = "") -> dict | None:



    image = reduced_image(image) if image is not None else image
    encoded = image_to_png_base64(image)
    if not encoded:
        return None
    if not name:
        name = "image.png"
    if not name.lower().endswith(".png"):
        name = os.path.splitext(name)[0] + ".png"
    item = {"id": uuid.uuid4().hex, "kind": "image", "name": name,
            "mime": "image/png", "data_base64": encoded,
            "width": image.width(), "height": image.height()}
    if path:
        item["path"] = path
    return item


def file_attachment(path: str) -> dict:
    return {"id": uuid.uuid4().hex, "kind": "file",
            "name": os.path.basename(path) or path, "path": path}


def attachment_from_path(path: str) -> dict | None:
    """An image file becomes an image attachment, a data file a file one."""
    path = str(path or "")
    if len(path) > 4096:
        return None
    kind = classify(path)
    if kind == "image":
        try:
            if not os.path.isfile(path) or os.path.getsize(path) > MAX_IMAGE_FILE_BYTES:
                return None
        except OSError:
            return None
        reader = QImageReader(path)



        try:
            reader.setAutoTransform(True)
        except AttributeError:
            pass
        size = reader.size()
        if size.isValid():
            if size.width() * size.height() > MAX_IMAGE_PIXELS * 16:
                return None
            if max(size.width(), size.height()) > MAX_IMAGE_SIDE:
                scale = min(MAX_IMAGE_SIDE / size.width(), MAX_IMAGE_SIDE / size.height())
                reader.setScaledSize(QSize(max(1, int(size.width() * scale)),
                                           max(1, int(size.height() * scale))))
        image = reader.read()
        return image_attachment(image, os.path.basename(path), path)
    if kind == "file":
        try:
            if not os.path.isfile(path):
                return None
        except OSError:
            return None
        return file_attachment(path)
    return None


def wire_shape(item: dict) -> dict:
    """The attachment as ``send_requested`` carries it: no widget-only keys."""
    keys = ("kind", "name", "mime", "data_base64", "path")
    return {k: item[k] for k in keys if k in item and item[k] not in (None, "")}


def file_type(item: dict) -> str:
    ext = os.path.splitext(str(item.get("name") or item.get("path") or ""))[1].lower()
    return FILE_TYPES.get(ext, ext.lstrip(".").upper() or "File")


def attachment_kind(item: dict) -> str:
    """The card's second line: ``Image`` for a picture, ``GeoPackage file`` for data."""
    if item.get("kind") == "image":
        return tr("Image")
    ext = os.path.splitext(str(item.get("name") or item.get("path") or ""))[1].lower()
    name = FILE_TYPES.get(ext)
    if name:
        return tr("{type} file").format(type=name)
    return tr("File")


def tr(text: str) -> str:




    from qgis.PyQt.QtCore import QCoreApplication

    return QCoreApplication.translate("AttachmentTag", text)


def _decode(item: dict) -> QImage | None:
    encoded = item.get("data_base64")
    if not isinstance(encoded, str) or not encoded or len(encoded) > MAX_IMAGE_BASE64:
        return None
    try:
        data = base64.b64decode(encoded, validate=True)
        buffer = QBuffer()
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer)
        size = reader.size()
        if size.isValid() and size.width() * size.height() > MAX_IMAGE_PIXELS:
            return None
        image = reader.read()
    except (ValueError, TypeError, binascii.Error):
        return None
    if image is None or image.isNull():
        return None


    if image.width() * image.height() > MAX_IMAGE_PIXELS:
        return None
    return image


def _canvas(width: int, height: int, ratio: float, radius: float, ground: QColor | None):
    """A rounded pixmap and a painter clipped to it, ground already laid."""
    pixmap = QPixmap(int(width * ratio), int(height * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, width, height), radius, radius)
    if ground is not None:
        painter.fillPath(path, ground)
    painter.setClipPath(path)
    return pixmap, painter


def _rounded(image: QImage, width: int, height: int, ratio: float, radius: float) -> QPixmap:
    """``image`` cropped to cover ``width x height`` with rounded corners."""




    scaled = image.scaled(int(width * ratio), int(height * ratio),
                          Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                          Qt.TransformationMode.SmoothTransformation)
    pixmap, painter = _canvas(width, height, ratio, radius, None)
    try:

        dx = int((scaled.width() / ratio - width) / 2)
        dy = int((scaled.height() / ratio - height) / 2)
        painter.drawImage(-dx, -dy, scaled)
    finally:
        painter.end()
    return pixmap


def _contained(image: QImage, width: int, height: int, ratio: float, radius: float,
               ground: QColor) -> QPixmap:
    """The WHOLE ``image``, centred inside ``width x height``, on ``ground``."""





    scaled = image.scaled(int(width * ratio), int(height * ratio),
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
    pixmap, painter = _canvas(width, height, ratio, radius, ground)
    try:
        dx = (width - scaled.width() / ratio) / 2
        dy = (height - scaled.height() / ratio) / 2
        painter.drawImage(QRectF(dx, dy, scaled.width() / ratio, scaled.height() / ratio),
                          scaled, QRectF(scaled.rect()))
    finally:
        painter.end()
    return pixmap


def letterbox_ground(widget) -> QColor:
    """The neutral behind a contained picture: the widget's own paper, one step toward its ink."""





    paper, ink = paper_of(widget), ink_of(widget)
    mix = 0.12
    return QColor(int(round(paper.red() * (1 - mix) + ink.red() * mix)),
                  int(round(paper.green() * (1 - mix) + ink.green() * mix)),
                  int(round(paper.blue() * (1 - mix) + ink.blue() * mix)))


def thumbnail_pixmap(item: dict, widget, height: int = THUMB_HEIGHT) -> QPixmap | None:
    """A rounded thumbnail of an image attachment, sharp on the screen."""
    image = _decode(item)
    if image is None:
        return None
    ratio = widget_pixel_ratio(widget)
    width = max(height, min(THUMB_MAX_WIDTH, int(round(height * image.width() / max(1, image.height())))))
    return _rounded(image, width, height, ratio, 4)


def tile_width(image: QImage, height: int = TILE_HEIGHT) -> int:
    """How wide the picture tile is for ``image`` at ``height``: the image's own aspect, between a square and ``TILE_MAX_ASPECT``."""

    aspect = image.width() / max(1, image.height())
    return max(height, min(int(round(height * TILE_MAX_ASPECT)), int(round(height * aspect))))


def tile_pixmap(item: dict, widget, height: int = TILE_HEIGHT) -> QPixmap | None:
    """The picture tile of the composer and the user bubble: the whole image, letterboxed on the tile's ground, at the file card's height."""

    image = _decode(item)
    if image is None:
        return None
    return _contained(image, tile_width(image, height), height,
                      widget_pixel_ratio(widget), TILE_RADIUS, letterbox_ground(widget))


def file_art(widget, item: dict, size: int = TILE_GLYPH):
    """``(pixmap, glyph)`` for a data file's card tile."""





    ext = os.path.splitext(str(item.get("name") or item.get("path") or ""))[1].lower()
    theme_name, glyph = FILE_ICONS.get(ext, _DEFAULT_FILE_ICON)
    return theme_pixmap(widget, theme_name, size), glyph


def attachment_caption(item: dict) -> str:
    if item.get("kind") == "image":
        size = ""
        if item.get("width") and item.get("height"):
            size = f" ({item['width']} x {item['height']})"
        return str(item.get("name") or "") + size
    return str(item.get("path") or item.get("name") or "")


class AttachmentTag(QFrame):
    """One attachment in the composer."""







    removed = pyqtSignal(str)

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.item = item
        self.key = str(item.get("id") or item.get("path") or item.get("name"))
        self._reveal = None
        tile = tile_pixmap(item, self) if item.get("kind") == "image" else None
        if tile is not None:
            self._build_tile(tile)
        else:
            self._build_card()

    def _build_tile(self, tile: QPixmap) -> None:
        self.setObjectName("attachTile")
        ratio = tile.devicePixelRatio() or 1.0
        width = int(round(tile.width() / ratio))
        height = int(round(tile.height() / ratio))


        self.setFixedSize(width + CLOSE_OVERHANG, height + CLOSE_OVERHANG)
        picture = ClickableThumb(self)
        picture.setPixmap(tile)
        picture.setGeometry(0, CLOSE_OVERHANG, width, height)
        picture.setToolTip(self.tr("{name}. Click to open.").format(name=attachment_caption(self.item)))
        picture.clicked.connect(self._open)
        close = IconButton(self, None, 10, self.tr("Remove"), _BTN_THUMB_CLOSE)
        close.set_icon("close", 10, QColor(255, 255, 255))
        close.setFixedSize(CLOSE_SIZE, CLOSE_SIZE)
        close.move(self.width() - CLOSE_SIZE, 0)
        close.clicked.connect(lambda: self.removed.emit(self.key))
        close.raise_()
        self._reveal = HoverReveal(self, close)

    def _build_card(self) -> None:
        self.setObjectName("attachCardTag")
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        art, glyph = file_art(self, self.item)
        card = AttachCard(str(self.item.get("name") or ""), attachment_kind(self.item), art, self,
                          closable=True, tooltip=attachment_caption(self.item), glyph=glyph)
        card.removed.connect(lambda: self.removed.emit(self.key))
        col.addWidget(card)

    def _open(self) -> None:
        """The picture full size, or a word saying why it cannot be shown."""






        if open_image_preview(self.item, self) is None:
            tip = tr("This picture cannot be opened any more.")
            self.setToolTip(tip)
            try:
                from qgis.PyQt.QtWidgets import QToolTip

                QToolTip.showText(self.mapToGlobal(self.rect().center()), tip, self)
            except (RuntimeError, AttributeError, ImportError):
                pass
