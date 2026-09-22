# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



























from __future__ import annotations

import os
from collections import OrderedDict

from qgis.PyQt.QtCore import QObject, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtGui import (
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from qgis.PyQt.QtWidgets import QSizePolicy, QWidget

from ...core.logger import log_warning
from ..font_scale import widget_pixel_ratio


def _thumbs():



    from .. import learn_page

    return learn_page




THUMB_W = 640


_THUMBS_KEPT = 64
_FULL_KEPT = 4


_PARALLEL = 6


def usable(url) -> bool:
    return _thumbs().is_thumbnail_url_usable(url)


def _decode(data: bytes, width: int):





    from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice
    from qgis.PyQt.QtGui import QImageReader

    try:
        buffer = QBuffer()
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer)
        size = reader.size()
        if size.isValid():
            if size.width() * size.height() > 16 * 1024 * 1024:
                return None
            if width and size.width() > width:
                reader.setScaledSize(QSize(width, max(1, round(size.height() * width / size.width()))))
        image = reader.read()
        return None if image.isNull() else image
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def _read_file(url: str):
    thumbs = _thumbs()
    try:
        path = thumbs.thumbnail_cache_path(url)
        if os.path.getsize(path) > thumbs.MAX_THUMBNAIL_BYTES:
            return b""
        with open(path, "rb") as handle:
            return handle.read(thumbs.MAX_THUMBNAIL_BYTES)
    except OSError:
        return b""


def cached_on_disk(url: str) -> bool:
    try:
        return os.path.isfile(_thumbs().thumbnail_cache_path(url))
    except (OSError, TypeError, ValueError):
        return False


class PictureStore(QObject):






    ready = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._thumbs: OrderedDict = OrderedDict()
        self._full: OrderedDict = OrderedDict()
        self._decoding: set = set()
        self._queue: list = []
        self._fetching: dict = {}
        self._waiting: list = []
        self._failed: set = set()
        self._files_only: set = set()
        self._full_wanted: set = set()
        self._inline: list = []
        self._batch_timer = QTimer(self)
        self._batch_timer.setSingleShot(True)
        self._batch_timer.setInterval(0)
        self._batch_timer.timeout.connect(self._start_batch)



    def failed(self, url: str) -> bool:

        return url in self._failed

    def picture(self, url: str, full: bool = False):

        cache = self._full if full else self._thumbs
        image = cache.get(url)
        if image is not None:
            cache.move_to_end(url)
            return image
        if full:


            return self._thumbs.get(url)
        return None

    def want(self, url: str, full: bool = False) -> None:

        if not usable(url) or url in self._failed:
            return
        cache = self._full if full else self._thumbs
        if url in cache or (url, full) in self._decoding:
            return
        if cached_on_disk(url):
            self._decoding.add((url, full))
            self._queue.append((url, full))
            self._batch_timer.start()
            return
        if full:
            self._full_wanted.add(url)
        self._fetch(url)

    def want_file(self, url: str) -> str:

        if not usable(url) or url in self._failed:
            return ""
        if cached_on_disk(url):
            return _thumbs().thumbnail_cache_path(url)
        self._files_only.add(url)
        self._fetch(url)
        return ""



    def _fetch(self, url: str) -> None:
        if url in self._fetching or url in self._waiting:
            return
        if len(self._fetching) >= _PARALLEL:
            self._waiting.append(url)
            return
        thumbs = _thumbs()
        try:
            from qgis.core import QgsNetworkAccessManager
            from qgis.PyQt.QtNetwork import QNetworkRequest

            request = QNetworkRequest(QUrl(url))
            request.setAttribute(thumbs._REDIRECT_POLICY, thumbs._NO_LESS_SAFE)
            reply = QgsNetworkAccessManager.instance().get(request)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Example picture not fetched: {exc}")
            self._failed.add(url)
            self.ready.emit(url)
            return
        try:
            reply.setReadBufferSize(thumbs.MAX_THUMBNAIL_BYTES + 1)
        except (AttributeError, RuntimeError):
            pass
        self._fetching[url] = reply
        reply.downloadProgress.connect(
            lambda received, total, u=url: self._on_progress(u, received, total))
        reply.finished.connect(lambda u=url: self._on_finished(u))

    def _on_progress(self, url: str, received: int, total: int) -> None:
        cap = _thumbs().MAX_THUMBNAIL_BYTES
        if received > cap or total > cap:
            reply = self._fetching.get(url)
            if reply is not None:
                try:
                    reply.abort()
                except RuntimeError:
                    pass

    def _on_finished(self, url: str) -> None:
        thumbs = _thumbs()
        reply = self._fetching.pop(url, None)
        data = b""
        if reply is not None:
            try:
                status = reply.attribute(thumbs._HTTP_STATUS)
                served = int(status) if status is not None else 200
                if reply.error() == thumbs._NETWORK_NO_ERROR and served == 200:
                    data = bytes(reply.readAll())
            except (RuntimeError, TypeError, ValueError):
                data = b""
            try:
                reply.deleteLater()
            except RuntimeError:
                pass
        if data and len(data) <= thumbs.MAX_THUMBNAIL_BYTES:
            thumbs.store_thumbnail(url, data)
        if not cached_on_disk(url):
            self._failed.add(url)
            self.ready.emit(url)
        elif url in self._files_only:
            self.ready.emit(url)
        else:


            for full in (False, True) if url in self._full_wanted else (False,):
                if (url, full) not in self._decoding:
                    self._decoding.add((url, full))
                    self._queue.append((url, full))
            self._batch_timer.start()
        if self._waiting:
            self._fetch(self._waiting.pop(0))



    def _start_batch(self) -> None:
        batch, self._queue = self._queue, []
        if not batch:
            return

        def work():
            out = []
            for url, full in batch:
                data = _read_file(url)
                out.append((url, full, _decode(data, 0 if full else THUMB_W) if data else None))
            return out

        from ...core.background import run_off_thread

        task = run_off_thread("Example pictures", work, self._on_decoded)
        if task is None:


            self._decode_inline(batch)

    def _decode_inline(self, batch: list) -> None:
        idle = not self._inline
        self._inline.extend(batch)
        if idle:
            self._decode_inline_next()

    def _decode_inline_next(self) -> None:

        if not self._inline:
            return
        url, full = self._inline.pop(0)
        data = _read_file(url)
        self._on_decoded([(url, full, _decode(data, 0 if full else THUMB_W) if data else None)], "")
        if self._inline:
            QTimer.singleShot(0, self._decode_inline_next)

    def _on_decoded(self, results, error: str) -> None:
        if error or not isinstance(results, list):
            if error:
                log_warning(f"Example pictures not decoded: {error.splitlines()[-1] if error else ''}")
            self._decoding.clear()
            return
        for url, full, image in results:
            self._decoding.discard((url, full))
            if image is None:
                self._failed.add(url)
                self.ready.emit(url)
                continue
            cache, kept = (self._full, _FULL_KEPT) if full else (self._thumbs, _THUMBS_KEPT)
            cache[url] = image
            cache.move_to_end(url)
            while len(cache) > kept:
                cache.popitem(last=False)
            self.ready.emit(url)


_STORE: PictureStore | None = None


def store() -> PictureStore:
    global _STORE
    if _STORE is None:
        _STORE = PictureStore()
    return _STORE





def rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


class PictureView(QWidget):


















    def __init__(self, url: str, owner=None, parent=None, full: bool = False,
                 radius: int = 12, label: str = "", label_px: int = 15):
        super().__init__(parent)
        self._url = url if usable(url) else ""
        self._owner = owner if owner is not None else self
        self._full = bool(full)
        self._radius = radius
        self._label = str(label or "")
        self._label_px = int(label_px)
        self._scaled_key = None
        self._scaled = None
        self._shade = 0.0
        self._focused = False
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        if self._url:
            store().ready.connect(self._on_ready)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return max(1, round(width * 9 / 16))

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(320, 180)

    def has_picture(self) -> bool:
        return bool(self._url) and store().picture(self._url, self._full) is not None

    def loading(self) -> bool:
        return bool(self._url) and not store().failed(self._url) and not self.has_picture()

    def set_shade(self, amount: float) -> None:
        self._shade = max(0.0, min(1.0, float(amount)))
        self.update()

    def set_focused(self, focused: bool) -> None:
        self._focused = bool(focused)
        self.update()

    def _on_ready(self, url: str) -> None:
        if url != self._url:
            return
        self._scaled_key = None
        self.update()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if self._url:
            store().want(self._url, self._full)

    def paintEvent(self, _event):  # noqa: N802
        from . import common

        palette = common.T
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            rect = QRectF(self.rect())
            painter.setClipPath(rounded_path(rect, self._radius))
            image = store().picture(self._url, self._full) if self._url else None
            if image is not None:
                self._paint_image(painter, image)
            else:
                painter.fillRect(rect, QColor(palette.tile))
                if not self.loading():
                    self._paint_label(painter, rect, palette)
            if self._shade > 0.0:
                shade = QColor(0, 0, 0)
                shade.setAlphaF(palette.shade * self._shade)
                painter.fillRect(rect, shade)
            if self._focused:
                painter.setClipping(False)
                ring = QColor(palette.text)
                painter.setPen(QPen(ring, 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), self._radius - 1, self._radius - 1)
        finally:
            painter.end()

    def _paint_label(self, painter: QPainter, rect: QRectF, palette) -> None:

        if not self._label or rect.width() < 60:
            return
        font = QFont(self.font())
        font.setPixelSize(max(11, round(self._label_px * min(1.6, max(0.8, rect.width() / 260)))))
        from .common import MEDIUM

        font.setWeight(QFont.Weight.DemiBold if MEDIUM >= 600 else QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor(palette.text_2))
        pad = max(12.0, rect.width() * 0.06)
        box = rect.adjusted(pad, pad, -pad, -pad)
        painter.drawText(box, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom
                                  | Qt.TextFlag.TextWordWrap), self._label)

    def _paint_image(self, painter: QPainter, image: QImage) -> None:
        ratio = widget_pixel_ratio(self)
        key = (self.width(), self.height(), round(ratio, 2), image.cacheKey())
        if key != self._scaled_key:
            target = QSize(max(1, int(self.width() * ratio)), max(1, int(self.height() * ratio)))
            scaled = image.scaled(target, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                  Qt.TransformationMode.SmoothTransformation)
            pixmap = QPixmap.fromImage(scaled)
            pixmap.setDevicePixelRatio(ratio)
            self._scaled, self._scaled_key = pixmap, key
        pixmap = self._scaled
        width = pixmap.width() / pixmap.devicePixelRatio()
        height = pixmap.height() / pixmap.devicePixelRatio()
        painter.drawPixmap(int((self.width() - width) / 2), int((self.height() - height) / 2), pixmap)





_SMALL_WORDS = {"of", "the", "and", "de", "du", "des", "la", "le", "les", "del", "der", "für",
                "for", "van", "von", "y", "et", "&"}


def monogram(name: str, code: str = "") -> str:







    if code:
        return str(code)[:2].upper()
    words = [w for w in str(name or "").replace("-", " ").replace("/", " ").split() if w]
    if not words:
        return "?"
    first = "".join(ch for ch in words[0] if ch.isalnum())
    if 2 <= len(first) <= 5 and first.isupper():
        return first[:2]
    big = [w for w in words if w.lower() not in _SMALL_WORDS and w[:1].isalnum()]
    if len(big) >= 2:
        return (big[0][0] + big[1][0]).upper()
    capitals = [ch for ch in first if ch.isupper()]
    if len(capitals) >= 2:
        return "".join(capitals[:2])
    return first[:2].capitalize() if first else "?"


_MARKS: dict = {}


def monogram_pixmap(letters: str, accent: str, side: int, ratio: float = 1.0) -> QPixmap:



    ratio = max(1.0, float(ratio or 1.0))
    key = (letters, accent, int(side), round(ratio, 2))
    if key in _MARKS:
        return _MARKS[key]
    color = QColor(accent)
    pixmap = QPixmap(int(side * ratio), int(side * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        radius = side / 3.0 + 2
        box = QRectF(0, 0, side, side)
        fill = QLinearGradient(0, 0, 0, side)
        fill.setColorAt(0.0, color.lighter(108))
        fill.setColorAt(1.0, color.darker(112))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(box, radius, radius)
        font = QFont(painter.font())
        font.setPixelSize(max(7, int(side * 0.4)))
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), letters)
    finally:
        painter.end()
    _MARKS[key] = pixmap
    return pixmap


def source_pixmap(row: dict, accent: str, side: int, ratio: float = 1.0):


    url = str((row or {}).get("logo_url") or "")
    if url:
        path = store().want_file(url)
        if path:
            from ..logo_tile import logo_pixmap

            chip = logo_pixmap(path, side, ratio)
            if chip is not None:
                return chip
    from ...core.connector_locale import country_of

    try:
        code = country_of(row)
    except Exception:  # noqa: BLE001
        code = ""
    return monogram_pixmap(monogram(str((row or {}).get("name") or ""), code), accent, side, ratio)


class _MarkWatcher(QObject):






    def __init__(self, label, row: dict, accent: str, side: int):
        super().__init__(label)
        self._label, self._row, self._accent, self._side = label, dict(row), accent, side
        self._url = str(row.get("logo_url") or "")
        store().ready.connect(self._on_ready)

    def _on_ready(self, url: str) -> None:
        if url != self._url:
            return
        try:
            self._label.setPixmap(source_pixmap(self._row, self._accent, self._side,
                                                widget_pixel_ratio(self._label)))
        except RuntimeError:
            pass


def wear_source_mark(label, row: dict, accent: str, side: int) -> None:

    label.setStyleSheet("QLabel { background: transparent; border: none; }")
    label.setFixedSize(side, side)
    label.setPixmap(source_pixmap(row, accent, side, widget_pixel_ratio(label)))
    url = str((row or {}).get("logo_url") or "")
    if usable(url) and not cached_on_disk(url):
        _MarkWatcher(label, row, accent, side)
