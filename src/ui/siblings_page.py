# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Settings > More plugins: AI Edit and AI Segmentation, installable from here."""
































from __future__ import annotations

from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.qgis_plugins import plugin_logo
from .cross_plugin_discovery import (
    SIBLINGS,
    is_sibling_installed,
    open_sibling,
    open_sibling_tutorial,
)
from .font_scale import scale_px_length, widget_pixel_ratio
from .learn_page import Thumbnail, ThumbnailLoader, cached_thumbnail, is_thumbnail_url_usable
from .logo_tile import logo_pixmap
from .settings_pages import (
    ACCENT_BORDER,
    GHOST_BTN_QSS,
    HAIRLINE,
    LINK_BTN_QSS,
    MUTED,
    PRIMARY_BTN_QSS,
    Page,
)
from .style import ACCENT, FONT_BASE, FONT_BODY, FONT_HINT, RADIUS_CARD




_SHOT_RATIO = 630 / 1200
_LOGO_PX = 24



_CARD_MAX_W = 420
_CARD_PADDING = 12




_CARD_MIN_W = 300


_BODY_MARGIN = 24
_SCROLLBAR_W = 16

_CARD_QSS = (
    f"QFrame#siblingCard {{ background: transparent; border: 1px solid {HAIRLINE};"
    f" border-radius: {RADIUS_CARD}px; }}"
    f"QFrame#siblingCard:hover {{ border-color: {ACCENT_BORDER}; }}"
    f"QLabel#siblingName {{ font-size: {FONT_BASE + 1}px; font-weight: 600;"
    " color: palette(text); background: transparent; }"
    f"QLabel#siblingNote {{ font-size: {FONT_BODY}px; color: palette(text);"
    " background: transparent; }"
    f"QLabel#siblingState {{ font-size: {FONT_HINT}px; color: {MUTED};"
    " background: transparent; }"
)


class SiblingCard(QFrame):
    """One TerraLab plugin: its still, its logo, what it does, and the way in."""

    install_requested = pyqtSignal(str)
    tutorial_requested = pyqtSignal(str)

    def __init__(self, product_id: str, title: str, note: str, parent=None):
        super().__init__(parent)
        self.product_id = str(product_id)
        sibling = SIBLINGS.get(self.product_id, {})
        self.setObjectName("siblingCard")
        self.setStyleSheet(_CARD_QSS)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMaximumWidth(scale_px_length(_CARD_MAX_W))

        col = QVBoxLayout(self)
        col.setContentsMargins(_CARD_PADDING, _CARD_PADDING, _CARD_PADDING, _CARD_PADDING)
        col.setSpacing(9)

        logo_path = plugin_logo(str(sibling.get("folder") or ""))
        self._shot = Thumbnail("sparkles", ACCENT, self)
        self._shot_width = 0
        self._shot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        col.addWidget(self._shot)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)



        chip = logo_pixmap(logo_path, scale_px_length(_LOGO_PX),
                           self.devicePixelRatioF()) if logo_path else None
        if chip is not None:
            mark = QLabel(self)
            mark.setPixmap(chip)
            head.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
        name = QLabel(title, self)
        name.setObjectName("siblingName")
        head.addWidget(name, 0, Qt.AlignmentFlag.AlignVCenter)
        self._state = QLabel("", self)
        self._state.setObjectName("siblingState")
        head.addWidget(self._state, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        col.addLayout(head)

        body = QLabel(note, self)
        body.setObjectName("siblingNote")
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        col.addWidget(body, 1)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self._action = QPushButton(self)
        self._action.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action.setAutoDefault(False)
        self._action.clicked.connect(lambda: self.install_requested.emit(self.product_id))
        actions.addWidget(self._action, 0)



        guide = QPushButton(self.tr("Read the guide"), self)
        guide.setStyleSheet(LINK_BTN_QSS)
        guide.setCursor(Qt.CursorShape.PointingHandCursor)
        guide.setAutoDefault(False)
        guide.setToolTip(self.tr("The written tutorial, on the TerraLab blog."))
        guide.clicked.connect(lambda: self.tutorial_requested.emit(self.product_id))
        actions.addWidget(guide, 0)
        actions.addStretch(1)
        col.addLayout(actions)

        self._loader: ThumbnailLoader | None = None
        self._load_shot(str(sibling.get("thumbnail_url") or ""), logo_path)
        self.refresh()

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)





        width = event.size().width()
        if width == self._shot_width:
            return
        self._shot_width = width
        inner = max(1, width - 2 * _CARD_PADDING)
        self._shot.setFixedHeight(int(round(inner * _SHOT_RATIO)))

    def _load_shot(self, url: str, logo_path: str) -> None:
        """The still, from the cache when it is there and the network once when it is not."""


        if logo_path:
            chip = logo_pixmap(logo_path, scale_px_length(44), widget_pixel_ratio(self))
            if chip is not None:
                self._shot.set_placeholder(chip)
        if not is_thumbnail_url_usable(url):
            return
        cached = cached_thumbnail(url)
        if cached is not None:
            self._shot.set_image(cached)
            return
        self._loader = ThumbnailLoader(self)
        self._loader.loaded.connect(self._shot.set_image)
        self._loader.fetch(url)

    def refresh(self) -> None:
        """Say what the button does now: QGIS may have gained the plugin since."""
        installed = is_sibling_installed(self.product_id)
        self._action.setText(
            self.tr("Open in QGIS") if installed else self.tr("Install in QGIS"))
        self._action.setStyleSheet(GHOST_BTN_QSS if installed else PRIMARY_BTN_QSS)
        self._action.setToolTip(
            self.tr("Show the plugin's panel.") if installed
            else self.tr("Opens the QGIS plugin manager on this plugin."))
        self._state.setText(self.tr("Installed") if installed else "")


class SiblingsPage(Page):
    """The two cards, side by side, or stacked when the window is too narrow."""

    opened = pyqtSignal(str, str)

    def __init__(self, parent=None):
        def translate(text: str) -> str:
            return parent.tr(text) if parent is not None else text



        super().__init__(
            translate(QT_TRANSLATE_NOOP("SiblingsPage", "More plugins")),
            translate(QT_TRANSLATE_NOOP(
                "SiblingsPage",
                "The two other TerraLab plugins for QGIS. They install from here.")),
            parent)
        self._host = QWidget(self)
        self._grid = QGridLayout(self._host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(12)




        self._cards = [
            SiblingCard(
                "ai-edit", "AI Edit",
                self.tr("Repaint the imagery you already have open, from a sentence: a "
                        "redevelopment before it is built, a street planted with trees, "
                        "a flood, an orthophoto redrawn as a clean site plan. The result "
                        "comes back georeferenced, on the source's own extent and CRS."),
                self._host),
            SiblingCard(
                "ai-segmentation", "AI Segmentation",
                self.tr("Detect objects in a raster and get them back as real polygons: "
                        "building footprints, trees, water, solar panels, anything you can "
                        "point at. Runs on your machine or on our servers, and exports to "
                        "GeoPackage, Shapefile or GeoJSON."),
                self._host),
        ]
        for card in self._cards:
            card.install_requested.connect(self._on_install)
            card.tutorial_requested.connect(self._on_tutorial)
        self._columns = 0
        self._reflow(2)
        self.add(self._host)

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)




        room = event.size().width() - 2 * _BODY_MARGIN - _SCROLLBAR_W
        self._reflow(2 if room >= 2 * _CARD_MIN_W else 1)

    def _reflow(self, columns: int) -> None:
        """Two cards across, or one above the other when there is no room."""





        if columns == self._columns:
            return
        self._columns = columns
        for index, card in enumerate(self._cards):




            self._grid.addWidget(card, index // columns, index % columns,
                                 Qt.AlignmentFlag.AlignLeft)
        for column in range(2):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)


        self._grid.setColumnStretch(2, 2)

    def refresh(self) -> None:
        for card in self._cards:
            card.refresh()

    def _on_install(self, product_id: str) -> None:
        outcome = open_sibling(product_id)
        self.opened.emit(product_id, outcome)


        self.refresh()

    def _on_tutorial(self, product_id: str) -> None:
        open_sibling_tutorial(product_id)
        self.opened.emit(product_id, "guide")
