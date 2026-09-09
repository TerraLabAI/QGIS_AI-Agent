# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Settings > Tutorials: the video and the guide, with their own thumbnails."""

























from __future__ import annotations

import hashlib
import os
import tempfile

from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice, QObject, QSize, Qt, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QColor, QImage, QImageReader, QPainter, QPixmap
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.host_platform import retry_file_op
from .font_scale import scale_px_length
from .icons import pixmap_for
from .settings_pages import HAIRLINE, MUTED, ROW_NOTE_QSS, Page
from .shared import PLUGIN_CACHE_DIR, get_learn_items, resolve_qt_enum
from .style import ACCENT_BORDER, ACCENT_TINT, FONT_BASE, FONT_HINT, RADIUS_CARD




MAX_THUMBNAIL_BYTES = 900 * 1024

THUMBNAIL_CACHE_DIR = os.path.join(PLUGIN_CACHE_DIR, "thumbnails")

_NETWORK_NO_ERROR = resolve_qt_enum(QNetworkReply, "NetworkError", "NoError")
_HTTP_STATUS = resolve_qt_enum(QNetworkRequest, "Attribute", "HttpStatusCodeAttribute")
_REDIRECT_POLICY = resolve_qt_enum(QNetworkRequest, "Attribute", "RedirectPolicyAttribute")
_NO_LESS_SAFE = resolve_qt_enum(QNetworkRequest, "RedirectPolicy", "NoLessSafeRedirectPolicy")



_CARD_W = 300
_CARD_H = 169
_BADGE_PX = 34
_BADGE_GLYPH_PX = 16




_KIND_ACCENTS = {"video": "#D0533C", "article": "#3E86D6"}
_DEFAULT_ACCENT = "#3E86D6"
_KIND_GLYPHS = {"video": "play", "article": "book"}

_CARD_QSS = (
    f"QFrame#learnCard {{ background: transparent; border: 1px solid {HAIRLINE};"
    f" border-radius: {RADIUS_CARD}px; }}"
    f"QFrame#learnCard:hover {{ background: {ACCENT_TINT}; border-color: {ACCENT_BORDER}; }}"
    f"QLabel#learnTitle {{ font-size: {FONT_BASE}px; font-weight: 600; color: palette(text);"
    " background: transparent; }"
    f"QLabel#learnNote {{ font-size: {FONT_HINT}px; color: {MUTED}; background: transparent; }}"
    f"QLabel#learnKind {{ font-size: {FONT_HINT}px; font-weight: 600; color: {MUTED};"
    " background: transparent; }"
)


def thumbnail_cache_path(url: str) -> str:
    """The URL is the identity of the picture: a new still comes with a new URL."""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    return os.path.join(THUMBNAIL_CACHE_DIR, f"{digest}.img")


def is_thumbnail_url_usable(url: object) -> bool:
    """https only. A file: or data: URL from a server would read something local."""
    if not isinstance(url, str) or not url.lower().startswith("https://"):
        return False
    try:
        from urllib.parse import urlsplit
        parsed = urlsplit(url)
        return bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def cached_thumbnail(url: str) -> QImage | None:
    try:
        if os.path.getsize(thumbnail_cache_path(url)) > MAX_THUMBNAIL_BYTES:
            return None
        with open(thumbnail_cache_path(url), "rb") as handle:
            data = handle.read(MAX_THUMBNAIL_BYTES)
    except OSError:
        return None
    return _read_bounded_image(data)


def _read_bounded_image(data: bytes) -> QImage | None:
    """Inspect image dimensions before decoding a cached remote thumbnail."""
    try:
        buffer = QBuffer()
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer)
        size = reader.size()
        if size.isValid() and size.width() * size.height() > 16 * 1024 * 1024:
            return None
        image = reader.read()
        return None if image.isNull() else image
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def store_thumbnail(url: str, data: bytes) -> None:
    path = thumbnail_cache_path(url)
    try:
        os.makedirs(THUMBNAIL_CACHE_DIR, exist_ok=True)




        handle_fd, part = tempfile.mkstemp(prefix=".thumb-", dir=THUMBNAIL_CACHE_DIR)
        with os.fdopen(handle_fd, "wb") as handle:
            handle.write(data)



        retry_file_op(os.replace, part, path)
    except OSError:
        try:
            os.remove(part)
        except OSError:
            pass


class ThumbnailLoader(QObject):
    """One picture read, off the UI thread, reporting back on ``loaded``."""





    loaded = pyqtSignal(QImage)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._reply = None
        self._url = ""

    def fetch(self, url: str) -> None:
        from qgis.core import QgsNetworkAccessManager

        if self._reply is not None or not is_thumbnail_url_usable(url):
            return
        self._url = url
        request = QNetworkRequest(QUrl(url))
        request.setAttribute(_REDIRECT_POLICY, _NO_LESS_SAFE)
        try:
            reply = QgsNetworkAccessManager.instance().get(request)
        except Exception:  # noqa: BLE001
            return
        try:
            reply.setReadBufferSize(MAX_THUMBNAIL_BYTES + 1)
        except (AttributeError, RuntimeError):
            pass
        self._reply = reply
        reply.downloadProgress.connect(self._on_progress)
        reply.finished.connect(self._on_finished)



        reply.finished.connect(reply.deleteLater)

    def abort(self) -> None:
        reply, self._reply = self._reply, None
        if reply is None:
            return
        for signal in ("finished", "downloadProgress"):
            try:
                getattr(reply, signal).disconnect()
            except (AttributeError, RuntimeError, TypeError):
                pass
        try:
            reply.abort()
            reply.deleteLater()
        except RuntimeError:
            pass

    def _on_progress(self, received: int, total: int) -> None:
        if self._reply is not None and (received > MAX_THUMBNAIL_BYTES or total > MAX_THUMBNAIL_BYTES):
            self.abort()

    def _on_finished(self) -> None:
        reply, self._reply = self._reply, None
        if reply is None:
            return
        data = b""
        try:
            status = reply.attribute(_HTTP_STATUS)
            served = int(status) if status is not None else 200
            if reply.error() == _NETWORK_NO_ERROR and served == 200:
                data = bytes(reply.readAll())
        except (RuntimeError, TypeError, ValueError):
            data = b""
        try:
            reply.deleteLater()
        except RuntimeError:
            pass
        if not data or len(data) > MAX_THUMBNAIL_BYTES:
            return
        image = _read_bounded_image(data)
        if image is None:
            return
        store_thumbnail(self._url, data)
        self.loaded.emit(image)


class Thumbnail(QLabel):
    """The picture, or the panel that stands in for it."""










    def __init__(self, glyph: str, accent: str, parent=None):
        super().__init__(parent)
        self._glyph = glyph
        self._accent = QColor(accent)
        self._image: QImage | None = None
        self._placeholder: QPixmap | None = None
        self.setMinimumHeight(scale_px_length(_CARD_H))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_image(self, image: QImage) -> None:
        self._image = image if image is not None and not image.isNull() else None
        self.update()

    def set_placeholder(self, pixmap: QPixmap) -> None:
        """Paint this in the tinted panel instead of the kind's glyph."""





        self._placeholder = pixmap if pixmap is not None and not pixmap.isNull() else None
        self.update()

    def paintEvent(self, _event):  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        radius = scale_px_length(RADIUS_CARD)
        rect = self.rect()
        path_rect = rect.adjusted(0, 0, -1, -1)
        if self._image is not None:
            ratio = float(self.devicePixelRatioF()) if hasattr(self, "devicePixelRatioF") else 1.0
            target = QSize(max(1, int(rect.width() * ratio)), max(1, int(rect.height() * ratio)))
            scaled = self._image.scaled(target, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                        Qt.TransformationMode.SmoothTransformation)
            pixmap = QPixmap.fromImage(scaled)
            pixmap.setDevicePixelRatio(ratio)
            painter.setClipRect(path_rect)
            painter.drawPixmap(
                rect.x() - max(0, (int(scaled.width() / ratio) - rect.width())) // 2,
                rect.y() - max(0, (int(scaled.height() / ratio) - rect.height())) // 2,
                pixmap)
            painter.end()
            return
        tint = QColor(self._accent)
        tint.setAlphaF(0.16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tint)
        painter.drawRoundedRect(path_rect, radius, radius)
        glyph = self._placeholder if self._placeholder is not None else pixmap_for(
            self, self._glyph, scale_px_length(30), self._accent)
        painter.drawPixmap(int((rect.width() - glyph.width() / glyph.devicePixelRatio()) / 2),
                           int((rect.height() - glyph.height() / glyph.devicePixelRatio()) / 2),
                           glyph)
        painter.end()


class LearnCard(QFrame):
    """One tutorial: the thumbnail, what kind it is, its title and one line."""

    clicked = pyqtSignal()

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("learnCard")
        self.setStyleSheet(_CARD_QSS)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        kind = str(item.get("kind") or "article")
        accent = _KIND_ACCENTS.get(kind, _DEFAULT_ACCENT)
        self._url = str(item.get("url") or "")
        self.kind = kind

        col = QVBoxLayout(self)
        col.setContentsMargins(10, 10, 10, 12)
        col.setSpacing(8)
        self._thumb = Thumbnail(_KIND_GLYPHS.get(kind, "book"), accent, self)
        col.addWidget(self._thumb)

        label = QLabel(self.tr("Video") if kind == "video" else self.tr("Guide"), self)
        label.setObjectName("learnKind")
        col.addWidget(label)
        title = QLabel(str(item.get("title") or ""), self)
        title.setObjectName("learnTitle")
        title.setWordWrap(True)
        col.addWidget(title)
        note = QLabel(str(item.get("note") or ""), self)
        note.setObjectName("learnNote")
        note.setWordWrap(True)
        col.addWidget(note)

        self._loader: ThumbnailLoader | None = None
        thumbnail_url = str(item.get("thumbnail_url") or "")
        if is_thumbnail_url_usable(thumbnail_url):
            cached = cached_thumbnail(thumbnail_url)
            if cached is not None:
                self._thumb.set_image(cached)
            else:
                self._loader = ThumbnailLoader(self)
                self._loader.loaded.connect(self._thumb.set_image)
                self._loader.fetch(thumbnail_url)

    def url(self) -> str:
        return self._url

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class LearnPage(Page):
    """The two cards side by side, and what to do when there is nothing to show."""

    opened = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(
            parent.tr("Tutorials") if parent is not None else "Tutorials",
            parent.tr("Twelve minutes of video and a written guide. Both open in your browser.")
            if parent is not None else "",
            parent)
        items = [i for i in get_learn_items() if str(i.get("url") or "").startswith("https://")]
        if not items:
            empty = QLabel(self.tr("The tutorials arrive when the panel connects."), self)
            empty.setStyleSheet(ROW_NOTE_QSS)
            empty.setWordWrap(True)
            empty.setContentsMargins(4, 8, 4, 4)
            self.add(empty)
            return
        row_host = QWidget(self)
        row = QHBoxLayout(row_host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        for item in items[:2]:
            card = LearnCard(item, row_host)
            kind, url = card.kind, card.url()
            card.clicked.connect(lambda k=kind, u=url: self.opened.emit(k, u))
            card.setMinimumWidth(scale_px_length(_CARD_W // 2))
            row.addWidget(card, 1)
        self.add(row_host)


        for item in items[2:]:
            more = QLabel(
                f'<a href="{item.get("url")}" style="color: palette(text);">'
                f'{item.get("title")}</a>', self)
            more.setOpenExternalLinks(True)
            more.setStyleSheet(f"font-size: {FONT_BASE}px; background: transparent;")
            more.setContentsMargins(4, 2, 4, 0)
            self.add(more)
