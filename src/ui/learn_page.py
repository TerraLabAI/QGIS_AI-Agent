# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later























from __future__ import annotations

from qgis.PyQt.QtCore import QCoreApplication, QObject, QRectF, QSize, Qt, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.PyQt.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.qgis_plugins import plugin_logo
from ..core.siblings import is_sibling_installed, sibling_info
from .cards_click import PressSurface
from .cross_plugin_discovery import open_sibling
from .font_scale import scale_px_length, scale_qss_font_px
from .icons import pixmap_for
from .image_guard import read_bounded_image
from .logo_tile import logo_pixmap
from .settings_pages import (
    GHOST_BTN_QSS,
    GROUP_TITLE_QSS,
    MUTED,
    PRIMARY_BTN_QSS,
    ROW_NOTE_QSS,
    ROW_TITLE_QSS,
    TINT_HOVER,
    Page,
    SettingGroup,
)
from .shared import get_learn_items, released_on
from .style import FONT_BASE, FONT_HINT, GREEN, RADIUS_CARD
from .styles import PANEL_TEXT
from .thumbnail_cache import (
    HTTP_STATUS,
    MAX_THUMBNAIL_BYTES,
    NETWORK_NO_ERROR,
    NO_LESS_SAFE,
    REDIRECT_POLICY,
    cached_thumbnail,
    drop_reply,
    is_picture_url_usable,
    store_thumbnail,
)



_CARD_W = 300
_CARD_H = 169




_KIND_ACCENTS = {"video": "#D0533C", "article": "#3E86D6"}
_DEFAULT_ACCENT = "#3E86D6"
_KIND_GLYPHS = {"video": "play", "article": "book"}

_CARD_QSS = (

    scale_qss_font_px(f"#learnCard {{ background: transparent; border: none;"
    f" border-radius: {RADIUS_CARD}px; }}"
    f"#learnCard:hover {{ background: {TINT_HOVER}; }}"
    f"QLabel#learnTitle {{ font-size: {FONT_BASE}px; font-weight: 600; color: {PANEL_TEXT};"
    " background: transparent; }"
    f"QLabel#learnNote {{ font-size: {FONT_HINT}px; color: {MUTED}; background: transparent; }}")
)




class ThumbnailLoader(QObject):






    loaded = pyqtSignal(QImage)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._reply = None
        self._url = ""

    def fetch(self, url: str) -> None:
        from qgis.core import QgsNetworkAccessManager

        if self._reply is not None or not is_picture_url_usable(url):
            return
        self._url = url
        request = QNetworkRequest(QUrl(url))
        request.setAttribute(REDIRECT_POLICY, NO_LESS_SAFE)
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
        drop_reply(reply)

    def _on_progress(self, received: int, total: int) -> None:
        if self._reply is not None and (received > MAX_THUMBNAIL_BYTES or total > MAX_THUMBNAIL_BYTES):
            self.abort()

    def _on_finished(self) -> None:
        reply, self._reply = self._reply, None
        if reply is None:
            return
        data = b""
        try:
            status = reply.attribute(HTTP_STATUS)
            served = int(status) if status is not None else 200
            if reply.error() == NETWORK_NO_ERROR and served == 200:
                data = bytes(reply.readAll())
        except (RuntimeError, TypeError, ValueError):
            data = b""
        try:
            reply.deleteLater()
        except RuntimeError:
            pass
        if not data or len(data) > MAX_THUMBNAIL_BYTES:
            return
        image = read_bounded_image(data, 16 * 1024 * 1024)
        if image is None:
            return
        store_thumbnail(self._url, data)
        self.loaded.emit(image)


class Thumbnail(QLabel):







    def __init__(self, glyph: str, accent: str, parent=None):
        super().__init__(parent)
        self._glyph = glyph
        self._accent = QColor(accent)
        self._image: QImage | None = None
        self._placeholder: QPixmap | None = None

        self.play_mark = False
        self.setMinimumHeight(scale_px_length(_CARD_H))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_image(self, image: QImage) -> None:
        self._image = image if image is not None and not image.isNull() else None
        self.update()

    def _paint_play(self, painter: QPainter, rect) -> None:
        side = float(scale_px_length(52))
        disc = QRectF(rect.center().x() - side / 2, rect.center().y() - side / 2, side, side)
        painter.setClipping(False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 235))
        painter.drawEllipse(disc)
        tri = QPainterPath()
        cx, cy, half = disc.center().x() + side * 0.05, disc.center().y(), side * 0.2
        tri.moveTo(cx - half * 0.8, cy - half)
        tri.lineTo(cx + half, cy)
        tri.lineTo(cx - half * 0.8, cy + half)
        tri.closeSubpath()
        painter.setBrush(QColor(20, 20, 20))
        painter.drawPath(tri)

    def set_placeholder(self, pixmap: QPixmap) -> None:






        self._placeholder = pixmap if pixmap is not None and not pixmap.isNull() else None
        self.update()

    def paintEvent(self, _event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        radius = scale_px_length(RADIUS_CARD)
        rect = self.rect()
        path_rect = rect.adjusted(0, 0, -1, -1)
        if self._image is not None:
            ratio = float(self.devicePixelRatioF())
            target = QSize(max(1, int(rect.width() * ratio)), max(1, int(rect.height() * ratio)))
            scaled = self._image.scaled(target, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                        Qt.TransformationMode.SmoothTransformation)
            pixmap = QPixmap.fromImage(scaled)
            pixmap.setDevicePixelRatio(ratio)


            clip = QPainterPath()
            clip.addRoundedRect(QRectF(path_rect), radius, radius)
            painter.setClipPath(clip)
            painter.drawPixmap(
                rect.x() - max(0, (int(scaled.width() / ratio) - rect.width())) // 2,
                rect.y() - max(0, (int(scaled.height() / ratio) - rect.height())) // 2,
                pixmap)
            if self.play_mark:
                self._paint_play(painter, rect)
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


class LearnCard(PressSurface):


    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("learnCard")

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_CARD_QSS)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        kind = str(item.get("kind") or "article")
        accent = _KIND_ACCENTS.get(kind, _DEFAULT_ACCENT)
        self._url = str(item.get("url") or "")
        self.kind = kind

        col = QVBoxLayout(self)
        col.setContentsMargins(10, 10, 10, 12)
        col.setSpacing(0)
        self._thumb = Thumbnail(_KIND_GLYPHS.get(kind, "book"), accent, self)
        self._thumb.play_mark = kind == "video"
        col.addWidget(self._thumb)
        col.addSpacing(10)

        title = QLabel(str(item.get("title") or ""), self)
        title.setObjectName("learnTitle")
        self.setAccessibleName(title.text())
        title.setWordWrap(True)
        col.addWidget(title)
        col.addSpacing(4)
        kind_word = self.tr("Video") if kind == "video" else self.tr("Guide")
        line = str(item.get("note") or "")
        note = QLabel(f"{kind_word} · {line}" if line else kind_word, self)
        note.setObjectName("learnNote")
        self.setAccessibleDescription(note.text())
        note.setWordWrap(True)
        col.addWidget(note)


        col.addStretch(1)

        self._loader: ThumbnailLoader | None = None
        thumbnail_url = str(item.get("thumbnail_url") or "")
        if is_picture_url_usable(thumbnail_url):
            cached = cached_thumbnail(thumbnail_url)
            if cached is not None:
                self._thumb.set_image(cached)
            else:
                self._loader = ThumbnailLoader(self)
                self._loader.loaded.connect(self._thumb.set_image)
                self._loader.fetch(thumbnail_url)

    def url(self) -> str:
        return self._url

    def mouseReleaseEvent(self, event):  # noqa: N802
        if released_on(self, event):
            self.click()
            return
        super().mouseReleaseEvent(event)


_LOGO_PX = 32


class SiblingRow(QFrame):


    install_requested = pyqtSignal(str)

    def __init__(self, product_id: str, title: str, note: str, parent=None):
        super().__init__(parent)
        self.product_id = str(product_id)
        self._title = title
        sibling = sibling_info(self.product_id) or {}
        self.setObjectName("settingsRow")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        line = QHBoxLayout(self)
        line.setContentsMargins(14, 10, 14, 10)
        line.setSpacing(12)


        logo_path = plugin_logo(str(sibling.get("folder") or ""))
        chip = logo_pixmap(logo_path, scale_px_length(_LOGO_PX),
                           self.devicePixelRatioF()) if logo_path else None
        if chip is not None:
            mark = QLabel(self)
            mark.setPixmap(chip)
            line.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(2)
        name = QLabel(title, self)
        name.setStyleSheet(ROW_TITLE_QSS)
        words.addWidget(name)
        body = QLabel(note, self)
        body.setStyleSheet(ROW_NOTE_QSS)
        body.setWordWrap(True)
        words.addWidget(body)
        line.addLayout(words, 1)
        self._check = QLabel(self)
        self._check.setPixmap(pixmap_for(self, "check", 12, QColor(GREEN)))
        line.addWidget(self._check, 0, Qt.AlignmentFlag.AlignVCenter)
        self._state = QLabel(self.tr("Installed"), self)
        self._state.setStyleSheet(ROW_NOTE_QSS)
        line.addWidget(self._state, 0, Qt.AlignmentFlag.AlignVCenter)
        line.addSpacing(4)
        self._action = QPushButton(self)
        self._action.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action.setAutoDefault(False)
        self._action.clicked.connect(lambda: self.install_requested.emit(self.product_id))
        line.addWidget(self._action, 0, Qt.AlignmentFlag.AlignVCenter)
        self.refresh()

    def refresh(self) -> None:

        installed = is_sibling_installed(self.product_id)
        self._action.setText(self.tr("Open") if installed else self.tr("Install"))

        self._action.setAccessibleName((self.tr("Open {plugin}") if installed
                                        else self.tr("Install {plugin}")).format(plugin=self._title))
        self._action.setStyleSheet(GHOST_BTN_QSS if installed else PRIMARY_BTN_QSS)
        self._action.setToolTip(
            self.tr("Show the plugin's panel.") if installed
            else self.tr("Opens the QGIS plugin manager on this plugin."))
        self._check.setVisible(installed)
        self._state.setVisible(installed)


class SiblingsSection(QWidget):


    opened = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 12, 0, 0)
        col.setSpacing(8)
        head = QLabel(self.tr("Other TerraLab plugins"), self)
        head.setStyleSheet(GROUP_TITLE_QSS)
        head.setContentsMargins(14, 0, 0, 0)
        col.addWidget(head)
        group = SettingGroup(self)
        self._rows = [
            SiblingRow("ai-edit", "AI Edit",
                       self.tr("Edit your imagery with a single sentence."), group),
            SiblingRow("ai-segmentation", "AI Segmentation",
                       self.tr("Turn buildings, trees or water into polygons."), group),
        ]
        for row in self._rows:
            row.install_requested.connect(self._on_install)
            group.add_row(row)
        col.addWidget(group)

    def refresh(self) -> None:
        for row in self._rows:
            row.refresh()

    def _on_install(self, product_id: str) -> None:
        outcome = open_sibling(product_id)
        self.opened.emit(product_id, outcome)


        self.refresh()


class _CardRow(QWidget):






    _STACK_BELOW_PX = 560

    def __init__(self, parent=None):
        super().__init__(parent)
        self.row = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)

    def resizeEvent(self, event):  # noqa: N802
        stacked = event.size().width() < scale_px_length(self._STACK_BELOW_PX)
        direction = QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight
        if self.row.direction() != direction:
            self.row.setDirection(direction)
        super().resizeEvent(event)


class LearnPage(Page):


    opened = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(
            QCoreApplication.translate("LearnPage", "Learn"),
            QCoreApplication.translate(
                "LearnPage", "Videos, guides and the other TerraLab plugins. Links open in your browser."),
            parent)

        self.siblings = SiblingsSection(self)
        items = [i for i in get_learn_items() if str(i.get("url") or "").startswith("https://")]
        if not items:
            empty = QLabel(self.tr("The tutorials arrive when the panel connects."), self)
            empty.setStyleSheet(ROW_NOTE_QSS)
            empty.setWordWrap(True)
            empty.setContentsMargins(14, 8, 14, 4)
            self.add(empty)
            self.add(self.siblings)
            return
        row_host = _CardRow(self)
        row = row_host.row

        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(12)
        for index, item in enumerate(items[:2]):
            card = LearnCard(item, row_host)
            kind, url = card.kind, card.url()
            card.clicked.connect(lambda _c=False, k=kind, u=url: self.opened.emit(k, u))

            row.addWidget(card, 3 if index == 0 else 2)
        self.add(row_host)


        for item in items[2:]:
            more = QLabel(
                f'<a href="{item.get("url")}" style="color: {PANEL_TEXT};">'
                f'{item.get("title")}</a>', self)
            more.setOpenExternalLinks(True)
            more.setStyleSheet(scale_qss_font_px(f"font-size: {FONT_BASE}px; background: transparent;"))
            more.setContentsMargins(14, 2, 14, 0)
            self.add(more)
        self.add(self.siblings)
