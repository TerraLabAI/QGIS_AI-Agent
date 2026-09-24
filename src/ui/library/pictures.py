# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later































from __future__ import annotations

import os
from collections import OrderedDict

from qgis.PyQt.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QUrl,
    QVariantAnimation,
    pyqtSignal,
)
from qgis.PyQt.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from qgis.PyQt.QtWidgets import QAbstractScrollArea, QSizePolicy, QWidget

from ...core.logger import log_warning
from ..font_scale import widget_pixel_ratio
from ..image_guard import read_bounded_image
from ..shared import tr


def _thumbs():



    from .. import learn_page

    return learn_page




THUMB_W = 640


_MAX_BYTES = 8 * 1024 * 1024



_THUMBS_KEPT = 128
_FULL_KEPT = 6


_PARALLEL = 6

_PREFETCH = 1000


_DECODE_CHUNK = 6


def usable(url) -> bool:
    return _thumbs().is_thumbnail_url_usable(url)


def _decode(data: bytes, width: int):





    def fit_width(size: QSize) -> QSize | None:
        if width and size.width() > width:
            return QSize(width, max(1, round(size.height() * width / size.width())))
        return None

    return read_bounded_image(data, 16 * 1024 * 1024, fit_width)


def _read_file(url: str):
    thumbs = _thumbs()
    try:
        path = thumbs.thumbnail_cache_path(url)
        if os.path.getsize(path) > _MAX_BYTES:
            return b""
        with open(path, "rb") as handle:
            return handle.read(_MAX_BYTES)
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
        self._waiting: dict = {}
        self._prefetch: set = set()
        self._failed: set = set()
        self._files_only: set = set()
        self._full_wanted: set = set()
        self._inline: list = []
        self._batch_timer = QTimer(self)
        self._batch_timer.setSingleShot(True)
        self._batch_timer.setInterval(0)
        self._batch_timer.timeout.connect(self._start_batch)

        self._pump_timer = QTimer(self)
        self._pump_timer.setSingleShot(True)
        self._pump_timer.setInterval(0)
        self._pump_timer.timeout.connect(self._pump)



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

    def want(self, url: str, full: bool = False, priority: int = 0) -> None:


        if not usable(url) or url in self._failed:
            return
        self._prefetch.discard(url)
        cache = self._full if full else self._thumbs
        if url in cache or (url, full) in self._decoding:
            return
        if cached_on_disk(url):
            self._decoding.add((url, full))
            self._queue.append((int(priority), url, full))
            self._batch_timer.start()
            return
        if full:
            self._full_wanted.add(url)
        self._fetch(url, priority)

    def prefetch(self, urls) -> None:

        for url in urls:
            if (usable(url) and url not in self._failed and url not in self._fetching
                    and url not in self._waiting and not cached_on_disk(url)):
                self._prefetch.add(url)
                self._fetch(url, _PREFETCH)

    def want_file(self, url: str) -> str:

        if not usable(url) or url in self._failed:
            return ""
        if cached_on_disk(url):
            return _thumbs().thumbnail_cache_path(url)
        self._files_only.add(url)
        self._fetch(url, 0)
        return ""



    def _fetch(self, url: str, priority: int = 0) -> None:
        if url in self._fetching:
            return
        self._waiting[url] = int(priority)
        self._pump_timer.start()

    def _pump(self) -> None:

        while self._waiting and len(self._fetching) < _PARALLEL:
            url = min(self._waiting, key=self._waiting.get)
            del self._waiting[url]
            self._start_fetch(url)

    def _start_fetch(self, url: str) -> None:
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
            reply.setReadBufferSize(_MAX_BYTES + 1)
        except (AttributeError, RuntimeError):
            pass
        self._fetching[url] = reply
        reply.downloadProgress.connect(
            lambda received, total, u=url: self._on_progress(u, received, total))
        reply.finished.connect(lambda u=url: self._on_finished(u))

    def _on_progress(self, url: str, received: int, total: int) -> None:
        cap = _MAX_BYTES
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
        if data and len(data) <= _MAX_BYTES:
            thumbs.store_thumbnail(url, data)
        if not cached_on_disk(url):
            self._failed.add(url)
            self.ready.emit(url)
        elif url in self._files_only or url in self._prefetch:
            self._prefetch.discard(url)
            self.ready.emit(url)
        else:


            for full in (False, True) if url in self._full_wanted else (False,):
                if (url, full) not in self._decoding:
                    self._decoding.add((url, full))
                    self._queue.append((0, url, full))
            self._batch_timer.start()
        self._pump_timer.start()



    def _start_batch(self) -> None:
        queued, self._queue = sorted(self._queue, key=lambda item: item[0]), []
        for start in range(0, len(queued), _DECODE_CHUNK):
            self._start_chunk([(url, full) for _p, url, full in queued[start:start + _DECODE_CHUNK]])

    def _start_chunk(self, batch: list) -> None:
        if not batch:
            return

        def work():
            out = []
            for url, full in batch:
                data = _read_file(url)
                out.append((url, full, _decode(data, 0 if full else THUMB_W) if data else None))
            return out

        from ...core.background import run_off_thread

        task = run_off_thread("Example pictures", work,
                              lambda results, error, b=tuple(batch): self._on_decoded(results, error, b))
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

    def _on_decoded(self, results, error: str, batch: tuple = ()) -> None:
        if error or not isinstance(results, list):
            if error:
                log_warning(f"Example pictures not decoded: {error.splitlines()[-1] if error else ''}")
            for item in batch:
                self._decoding.discard(item)
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


def prefetch_popular(cases, groups) -> None:




    try:
        from qgis.utils import iface

        panel = iface.mainWindow().findChild(QWidget, "chatPanel") if iface is not None else None
        if panel is None or not panel.isVisible():
            return
        first = str(groups[0][0]) if groups else ""
        urls = []
        for case in cases:
            if case.group != first or not case.listed:
                continue
            before, after = case.pair(small=True)
            urls.extend([before, after] if before else [case.image_small or case.image])
        store().prefetch([u for u in urls if u])
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Example pictures not prefetched: {exc}")





def screen_priority(widget: QWidget) -> int:


    try:
        if not widget.isVisible():
            return _PREFETCH - 1
        if not widget.visibleRegion().isEmpty():
            return 0
        window = widget.window()
        clip = QRect(QPoint(0, 0), window.size())
        parent = widget.parentWidget()
        while parent is not None and parent is not window:
            area = parent.parentWidget()
            if isinstance(area, QAbstractScrollArea) and area.viewport() is parent:
                clip = clip.intersected(QRect(parent.mapTo(window, QPoint(0, 0)), parent.size()))
            parent = area
        rect = QRect(widget.mapTo(window, QPoint(0, 0)), widget.size())
        if clip.isEmpty():
            return _PREFETCH - 1
        dx = max(clip.left() - rect.right(), rect.left() - clip.right(), 0)
        dy = max(clip.top() - rect.bottom(), rect.top() - clip.bottom(), 0)
        return 1 + int(max(dx / max(1, clip.width()), dy / max(1, clip.height())))
    except RuntimeError:
        return _PREFETCH - 1


def reprioritise(root: QWidget) -> None:

    try:
        for view in root.findChildren(PictureView):
            view.request()
    except RuntimeError:
        pass


def watch_scroll(owner: QWidget, *bars) -> None:


    timer = QTimer(owner)
    timer.setSingleShot(True)
    timer.setInterval(80)
    timer.timeout.connect(lambda o=owner: reprioritise(o))
    for bar in bars:
        bar.valueChanged.connect(timer.start)





class Tween:




    def __init__(self, widget: QWidget, ms: int, apply,
                 curve=QEasingCurve.Type.OutCubic):
        self._anim = QVariantAnimation(widget)
        self._anim.setDuration(int(ms))
        self._anim.setEasingCurve(QEasingCurve(curve))
        self._anim.valueChanged.connect(apply)

    def run(self, start: float, end: float, keys: tuple = ()) -> None:
        self._anim.stop()
        self._anim.setStartValue(float(start))
        for at, value in keys:
            self._anim.setKeyValueAt(float(at), float(value))
        self._anim.setEndValue(float(end))
        self._anim.start()

    def stop(self) -> None:
        self._anim.stop()

    def running(self) -> bool:
        return self._anim.state() == QAbstractAnimation.State.Running





def rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path



_ZOOM = 0.03
_FADE_MS, _ZOOM_MS, _REST_MS = 150, 150, 250
_DIVIDER = QColor(255, 255, 255)
_DIVIDER_SHADOW = QColor(0, 0, 0, 70)
_CHIP_BG = QColor(0, 0, 0, 140)


class PictureView(QWidget):

























    def __init__(self, url: str, owner=None, parent=None, full: bool = False,
                 radius: int = 12, label: str = "", label_px: int = 15,
                 pair: tuple = ("", ""), split: float = 0.5, fallback=None):
        super().__init__(parent)
        before, after = (tuple(pair) + ("", ""))[:2]
        self._pair = (before, after) if usable(before) and usable(after) else None
        self._url = "" if self._pair else (url if usable(url) else "")
        fallback = fallback if isinstance(fallback, (tuple, list)) else (fallback or "",)
        self._fallback = tuple(u if usable(u) else "" for u in fallback)
        self._owner = owner if owner is not None else self
        self._full = bool(full)
        self._radius = radius
        self._label = str(label or "")
        self._label_px = int(label_px)
        self._scaled: dict = {}
        self._shade = 0.0
        self._focused = False
        self.split = float(split) if 0.0 < float(split) < 1.0 else 0.5
        self.pos = self.split
        self._zoom = 0.0
        self._fade = 1.0
        self._seen_empty = False
        self._handle = 0
        self._before_text = tr("Before")
        self._after_text = tr("After")
        self._fade_tween = Tween(self, _FADE_MS, self._set_fade)
        self._zoom_tween = Tween(self, _ZOOM_MS, self._set_zoom)
        self._rest_tween = Tween(self, _REST_MS, self._set_pos, QEasingCurve.Type.InOutCubic)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        if self._urls():
            store().ready.connect(self._on_ready)



    def _urls(self) -> list:
        own = list(self._pair) if self._pair else [self._url]
        return [u for u in own + list(self._fallback) if u]

    def is_pair(self) -> bool:
        return self._pair is not None

    def _image(self, index: int):

        url = self._pair[index] if self._pair else self._url
        if not url:
            return None
        image = store().picture(url, self._full)
        if image is None and self._full:
            fallback = self._fallback[index] if index < len(self._fallback) else ""
            image = store().picture(fallback) if fallback else None
        return image

    def _failed(self, index: int) -> bool:
        url = self._pair[index] if self._pair else self._url
        return not url or store().failed(url)

    def _content(self):

        if self._pair:
            before, after = self._image(0), self._image(1)
            if before is not None and after is not None:
                return ("pair", before, after)
            if before is not None and self._failed(1):
                return ("one", before)
            if after is not None and self._failed(0):
                return ("one", after)
            return None
        image = self._image(0)
        return ("one", image) if image is not None else None

    def has_picture(self) -> bool:
        return self._content() is not None

    def has_pair(self) -> bool:
        content = self._content()
        return content is not None and content[0] == "pair"

    def loading(self) -> bool:
        if self._pair:
            return not self.has_picture() and not (self._failed(0) and self._failed(1))
        return bool(self._url) and not store().failed(self._url) and not self.has_picture()



    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return max(1, round(width * 9 / 16))

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(320, 180)



    def set_shade(self, amount: float) -> None:
        self._shade = max(0.0, min(1.0, float(amount)))
        self.update()

    def set_focused(self, focused: bool) -> None:
        self._focused = bool(focused)
        self.update()

    def set_handle(self, radius: int) -> None:

        self._handle = int(radius)
        self.update()

    def set_hover(self, hovered: bool) -> None:

        if not self.isVisible():
            self._zoom_tween.stop()
            self._set_zoom(0.0)
            return
        self._zoom_tween.run(self._zoom, 1.0 if hovered else 0.0)

    def follow(self, x: float) -> None:

        if not self._pair:
            return
        self._rest_tween.stop()
        self._set_pos(x / max(1, self.width()))

    def rest(self) -> None:

        if not self._pair:
            return
        if not self.isVisible() or abs(self.pos - self.split) < 0.002:
            self._rest_tween.stop()
            self._set_pos(self.split)
            return
        self._rest_tween.run(self.pos, self.split)

    def _set_pos(self, value) -> None:
        self.pos = max(0.0, min(1.0, float(value)))
        self.update()

    def _set_zoom(self, value) -> None:
        self._zoom = max(0.0, min(1.0, float(value)))
        self.update()

    def _set_fade(self, value) -> None:
        self._fade = max(0.0, min(1.0, float(value)))
        self.update()



    def request(self) -> None:

        if not self.isVisible():
            return
        priority = screen_priority(self)
        own = list(self._pair) if self._pair else [self._url]
        for url in own:
            if url:
                store().want(url, self._full, priority)

    def _on_ready(self, url: str) -> None:
        if url not in self._urls():
            return
        self._scaled = {}
        if self._seen_empty and self.isVisible() and self.has_picture():
            self._seen_empty = False
            self._fade_tween.run(0.0, 1.0)
        self.update()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if self._urls():

            QTimer.singleShot(0, self.request)

    def hideEvent(self, event):  # noqa: N802

        for tween in (self._fade_tween, self._zoom_tween, self._rest_tween):
            tween.stop()
        self._fade, self._zoom, self.pos = 1.0, 0.0, self.split
        super().hideEvent(event)

    def resizeEvent(self, event):  # noqa: N802
        self._scaled = {}
        super().resizeEvent(event)



    def paintEvent(self, _event):  # noqa: N802
        from . import common

        palette = common.T
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            rect = QRectF(self.rect())
            painter.setClipPath(rounded_path(rect, self._radius))
            content = self._content()
            if content is None or self._fade < 1.0:
                painter.fillRect(rect, QColor(palette.tile))
            if content is None:
                self._seen_empty = True
                if not self.loading():
                    self._paint_label(painter, rect, palette)
            else:
                painter.setOpacity(self._fade)
                self._paint_content(painter, rect, content)
                painter.setOpacity(1.0)
            if self._shade > 0.0:
                shade = QColor(0, 0, 0)
                shade.setAlphaF(palette.shade * self._shade)
                painter.fillRect(rect, shade)
            if content is not None and content[0] == "pair":
                painter.setOpacity(self._fade)
                self._paint_divider(painter, rect)
                painter.setOpacity(1.0)
            if self._focused:
                painter.setClipping(False)
                ring = QColor(palette.text)
                painter.setPen(QPen(ring, 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), self._radius - 1, self._radius - 1)
        except Exception:  # noqa: BLE001
            pass  # nosec B110
        finally:
            painter.end()

    def _paint_content(self, painter: QPainter, rect: QRectF, content) -> None:
        painter.save()
        if self._zoom > 0.0:
            scale = 1.0 + _ZOOM * self._zoom
            centre = rect.center()
            painter.translate(centre)
            painter.scale(scale, scale)
            painter.translate(-centre)
        if content[0] == "one":
            self._paint_image(painter, content[1], "one")
        else:

            split_x = rect.width() * self.pos
            if self._zoom > 0.0:
                scale = 1.0 + _ZOOM * self._zoom
                split_x = rect.center().x() + (split_x - rect.center().x()) / scale
            painter.save()
            painter.setClipRect(QRectF(-rect.width(), -rect.height(), split_x + rect.width(),
                                       3 * rect.height()), Qt.ClipOperation.IntersectClip)
            self._paint_image(painter, content[1], "before")
            painter.restore()
            painter.save()
            painter.setClipRect(QRectF(split_x, -rect.height(), 2 * rect.width(), 3 * rect.height()),
                                Qt.ClipOperation.IntersectClip)
            self._paint_image(painter, content[2], "after")
            painter.restore()
        painter.restore()

    def _paint_divider(self, painter: QPainter, rect: QRectF) -> None:
        x = rect.width() * self.pos
        painter.setPen(QPen(_DIVIDER_SHADOW, 4))
        painter.drawLine(QPointF(x, 0), QPointF(x, rect.height()))
        painter.setPen(QPen(_DIVIDER, 2))
        painter.drawLine(QPointF(x, 0), QPointF(x, rect.height()))
        if self._handle > 0:
            y = rect.height() / 2.0
            r = float(self._handle)
            painter.setPen(QPen(_DIVIDER_SHADOW, 1))
            painter.setBrush(_DIVIDER)
            painter.drawEllipse(QPointF(x, y), r, r)
            painter.setPen(QPen(QColor(32, 32, 32), 2))
            a = r * 0.3
            for sign in (-1, 1):
                tip = x + sign * a * 1.6
                painter.drawLine(QPointF(tip, y), QPointF(tip - sign * a, y - a))
                painter.drawLine(QPointF(tip, y), QPointF(tip - sign * a, y + a))
        self._paint_chips(painter, rect, x)

    def _paint_chips(self, painter: QPainter, rect: QRectF, x: float) -> None:





        from . import common as C

        if rect.width() < C.px(260):
            return
        font = QFont(self.font())
        font.setPixelSize(max(10, C.px(12 if rect.width() >= 300 else 11)))
        font.setWeight(QFont.Weight.DemiBold)
        metrics = QFontMetrics(font)
        painter.setFont(font)
        margin = float(C.px(8))
        pad = float(C.px(8))
        height = float(metrics.height() + C.px(4))
        for text, left in ((self._before_text, True), (self._after_text, False)):
            width = float(metrics.horizontalAdvance(text)) + 2 * pad
            box_x = margin if left else rect.width() - margin - width
            if (left and x < box_x + width + 4) or (not left and x > box_x - 4):
                continue
            box = QRectF(box_x, margin, width, height)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_CHIP_BG)
            painter.drawRoundedRect(box, height / 2.0, height / 2.0)
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), text)

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

    def _paint_image(self, painter: QPainter, image: QImage, slot: str) -> None:


        ratio = widget_pixel_ratio(self)
        key = (self.width(), self.height(), round(ratio, 2), image.cacheKey())
        cached = self._scaled.get(slot)
        if cached is None or cached[0] != key:
            target = QSize(max(1, int(self.width() * ratio)), max(1, int(self.height() * ratio)))
            scaled = image.scaled(target, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                  Qt.TransformationMode.SmoothTransformation)
            pixmap = QPixmap.fromImage(scaled)
            pixmap.setDevicePixelRatio(ratio)
            cached = (key, pixmap)
            self._scaled[slot] = cached
        pixmap = cached[1]
        width = pixmap.width() / pixmap.devicePixelRatio()
        height = pixmap.height() / pixmap.devicePixelRatio()
        painter.drawPixmap(QPointF((self.width() - width) / 2, (self.height() - height) / 2), pixmap)





_SMALL_WORDS = {"of", "the", "and", "de", "du", "des", "la", "le", "les", "del", "der", "für",
                "for", "van", "von", "y", "et", "&"}


def monogram(name: str, code: str = "", room: int = 2) -> str:









    if code:
        return str(code)[:2].upper()
    words = [w for w in str(name or "").replace("-", " ").replace("/", " ").split() if w]
    if not words:
        return "?"
    first = "".join(ch for ch in words[0] if ch.isalnum())
    if 2 <= len(first) <= 5 and first.isupper():
        return first if len(first) <= max(2, int(room)) else first[:2]
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

        share = 0.4 if len(letters) <= 2 else (0.3 if len(letters) == 3 else 0.25)
        font.setPixelSize(max(7, int(side * share)))
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

    room = 4 if side >= 32 else 2
    return monogram_pixmap(monogram(str((row or {}).get("name") or ""), code, room), accent, side, ratio)


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
