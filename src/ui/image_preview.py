# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""A picture opened full size over the QGIS window, the Claude lightbox."""







from __future__ import annotations

import base64
import binascii

from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice, QRectF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QImage, QImageReader, QPainter, QPainterPath, QPixmap
from qgis.PyQt.QtWidgets import QDialog, QLabel, QVBoxLayout, QWidget

from .font_scale import scale_qss_font_px, widget_pixel_ratio
from .shared import event_pos
from .style import FONT_HINT
from .widgets import IconButton

_MAX_IMAGE_BASE64 = 16 * 1024 * 1024
_MAX_IMAGE_SIDE = 4096
_MAX_IMAGE_PIXELS = _MAX_IMAGE_SIDE * _MAX_IMAGE_SIDE


_FILL = 0.80
_CLOSE_SIZE = 32
_CAPTION_GAP = 10

_BTN_LIGHTBOX_CLOSE = (
    "QToolButton { background: rgba(255, 255, 255, 0.12); border: none;"
    " border-radius: 16px; }"
    "QToolButton:hover { background: rgba(255, 255, 255, 0.28); }"
)
_CAPTION_QSS = scale_qss_font_px(
    f"QLabel {{ color: rgba(255, 255, 255, 0.92); font-size: {FONT_HINT + 1}px;"
    " background: transparent; border: none; }"
)


def image_of(item: dict) -> QImage | None:
    """The attachment's picture, or None when the bytes are not one."""
    encoded = item.get("data_base64") if isinstance(item, dict) else None
    if isinstance(encoded, str) and encoded and len(encoded) <= _MAX_IMAGE_BASE64:
        try:
            image = _read_bounded_image(base64.b64decode(encoded, validate=True))
        except (ValueError, TypeError, binascii.Error):
            return None
        return image
    path = item.get("path") if isinstance(item, dict) else None
    if path:
        return _read_bounded_image(str(path))
    return None


def _read_bounded_image(source) -> QImage | None:
    """Inspect dimensions before decoding potentially hostile image pixels."""
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
            if size.width() * size.height() > _MAX_IMAGE_PIXELS * 4:
                return None
            if max(size.width(), size.height()) > _MAX_IMAGE_SIDE:
                scale = min(_MAX_IMAGE_SIDE / size.width(), _MAX_IMAGE_SIDE / size.height())
                reader.setScaledSize(QSize(max(1, int(size.width() * scale)),
                                           max(1, int(size.height() * scale))))
        image = reader.read()
        return None if image.isNull() else image
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


class ClickableThumb(QLabel):
    """A thumbnail that opens its picture on a click."""

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if (event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event_pos(event))):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _Picture(QWidget):
    """The scaled picture with rounded corners; a click on it does nothing."""

    def __init__(self, image: QImage, parent=None):
        super().__init__(parent)
        self._image = image
        self._pixmap: QPixmap | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def fit(self, box: QSize) -> None:
        image = self._image
        if image.isNull() or box.width() <= 0 or box.height() <= 0:
            return
        ratio = widget_pixel_ratio(self)

        box = QSize(int(min(box.width() * ratio, image.width())), int(min(box.height() * ratio, image.height())))
        scaled = image.scaled(box.width(), box.height(),
                              Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
        pixmap = QPixmap.fromImage(scaled)
        pixmap.setDevicePixelRatio(ratio)
        self._pixmap = pixmap
        self.setFixedSize(int(scaled.width() / ratio), int(scaled.height() / ratio))
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt override
        if self._pixmap is None:
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            path = QPainterPath()
            path.addRoundedRect(QRectF(self.rect()), 8, 8)
            painter.setClipPath(path)
            painter.drawPixmap(0, 0, self._pixmap)
        finally:
            painter.end()

    def mousePressEvent(self, event):  # noqa: N802 - Qt override
        event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        event.accept()


class ImagePreview(QDialog):
    """The lightbox: dark ground, the picture, its name, one x."""

    def __init__(self, image: QImage, caption: str, anchor: QWidget | None = None):
        window = anchor.window() if anchor is not None else None
        super().__init__(window)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setModal(False)
        self._window = window

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(_CAPTION_GAP)
        col.addStretch(1)
        self._picture = _Picture(image, self)
        col.addWidget(self._picture, 0, Qt.AlignmentFlag.AlignHCenter)
        self._caption = QLabel(caption, self)
        self._caption.setStyleSheet(_CAPTION_QSS)
        self._caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(self._caption, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addStretch(1)

        self._close = IconButton(self, None, 16, self.tr("Close"), _BTN_LIGHTBOX_CLOSE)
        self._close.set_icon("close", 16, QColor(255, 255, 255))
        self._close.setFixedSize(_CLOSE_SIZE, _CLOSE_SIZE)
        self._close.clicked.connect(self.close)

    def open_over_window(self) -> None:


        geometry = self._window.frameGeometry() if self._window is not None else None
        screen = self._window.screen() if self._window is not None else None
        usable = screen.availableGeometry() if screen is not None else None
        if geometry is not None and usable is not None and not usable.isEmpty():
            geometry = geometry.intersected(usable)
        if (geometry is None or geometry.isEmpty()) and usable is not None:
            geometry = usable
        if geometry is not None and not geometry.isEmpty():
            self.setGeometry(geometry)
        self._layout_picture()
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _layout_picture(self) -> None:
        box = QSize(int(self.width() * _FILL), int(self.height() * _FILL) - 48)
        self._picture.fit(box)
        self._close.move(self.width() - _CLOSE_SIZE - 16, 16)
        self._close.raise_()

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._layout_picture()

    def paintEvent(self, event):  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), QColor(0, 0, 0, 200))
        finally:
            painter.end()

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.close()
            return
        super().keyPressEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override


        self.close()


def open_image_preview(item: dict, anchor: QWidget | None = None) -> ImagePreview | None:
    """Show ``item``'s picture full size; None when it carries none."""
    image = image_of(item)
    if image is None:
        return None
    name = str(item.get("name") or "")[:160]
    size = f"{image.width()} x {image.height()}"
    caption = f"{name}  ·  {size}" if name else size
    preview = ImagePreview(image, caption, anchor)
    preview.open_over_window()
    return preview
