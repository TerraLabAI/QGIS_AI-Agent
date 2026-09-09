# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The card that replaces the chat while an update is required."""















from __future__ import annotations

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .font_scale import scale_px_length, scale_qss_font_px, widget_pixel_ratio
from .shared import PLUGIN_ICON_PATH, PRODUCT_NAME
from .style import (
    _BTN_PRIMARY_WIDE,
    ACCENT,
    ACCENT_BORDER_SOFT,
    ACCENT_TINT,
    BTN_PRIMARY_WIDE_PX,
    FONT_BODY,
    FONT_HINT,
    INK,
    INK_2,
    INK_3,
    LINE,
    RADIUS_CARD,
    SURFACE,
)
from .terralab_menu import open_plugin_manager_updates

_CARD_MAX_W = 380
_ICON_PX = 44

_QSS = scale_qss_font_px(
    f"QFrame#updateGateCard {{ background: {SURFACE}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_CARD + 4}px; }}"
    f"QWidget#updateGateBadge {{ background: {ACCENT_TINT}; border: 1px solid {ACCENT_BORDER_SOFT};"
    f" border-radius: 11px; }}"
    f"QLabel#updateGateBadgeText {{ font-size: {FONT_HINT}px; font-weight: 600; color: {ACCENT};"
    " background: transparent; border: none; letter-spacing: 0.4px; }"
    f"QLabel#updateGateTitle {{ font-size: 16px; font-weight: 600; color: {INK};"
    " background: transparent; border: none; }"
    f"QLabel#updateGateBody {{ font-size: {FONT_BODY}px; color: {INK_2};"
    " background: transparent; border: none; }"
    f"QLabel#updateGateNote {{ font-size: {FONT_BODY}px; color: {INK};"
    " background: transparent; border: none; }"
    f"QLabel#updateGateHint {{ font-size: {FONT_HINT}px; color: {INK_3};"
    " background: transparent; border: none; }"
)


class UpdateGate(QWidget):
    """The required-update card, centred in the panel in place of the chat."""

    update_clicked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.version = ""
        self.setStyleSheet(_QSS)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 24, 16, 24)
        outer.setSpacing(0)
        outer.addStretch(1)

        self._card = QFrame(self)
        self._card.setObjectName("updateGateCard")



        self._card.setFixedWidth(_CARD_MAX_W)
        col = QVBoxLayout(self._card)
        col.setContentsMargins(22, 22, 22, 22)
        col.setSpacing(10)

        icon_row = QHBoxLayout()
        icon_row.setContentsMargins(0, 0, 0, 0)
        icon_row.setSpacing(12)
        icon = QLabel(self._card)
        icon.setFixedSize(_ICON_PX, _ICON_PX)




        ratio = widget_pixel_ratio(icon)
        pixmap = QPixmap(PLUGIN_ICON_PATH)
        if not pixmap.isNull():
            pixmap = pixmap.scaled(
                max(1, int(round(_ICON_PX * ratio))), max(1, int(round(_ICON_PX * ratio))),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            pixmap.setDevicePixelRatio(ratio)
            icon.setPixmap(pixmap)
        icon_row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        badge = QWidget(self._card)
        badge.setObjectName("updateGateBadge")
        badge.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        badge_row = QHBoxLayout(badge)
        badge_row.setContentsMargins(9, 3, 9, 3)
        badge_row.setSpacing(0)
        badge_text = QLabel(self.tr("Update required"), badge)
        badge_text.setObjectName("updateGateBadgeText")
        badge_row.addWidget(badge_text)
        icon_row.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        icon_row.addStretch(1)
        col.addLayout(icon_row)

        self._title = QLabel("", self._card)
        self._title.setObjectName("updateGateTitle")
        self._title.setWordWrap(True)
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        col.addWidget(self._title)



        self._note = QLabel("", self._card)
        self._note.setObjectName("updateGateNote")
        self._note.setWordWrap(True)
        self._note.setTextFormat(Qt.TextFormat.PlainText)
        self._note.hide()
        col.addWidget(self._note)

        body = QLabel(self.tr("Update to keep using {product}. It takes one click in the "
                              "QGIS Plugin Manager; the plugin reloads on its own.")
                      .format(product=PRODUCT_NAME), self._card)
        body.setObjectName("updateGateBody")
        body.setWordWrap(True)
        col.addWidget(body)

        col.addSpacing(4)
        self._button = QPushButton(self.tr("Update now"), self._card)
        self._button.setObjectName("updateGateButton")
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.setStyleSheet(_BTN_PRIMARY_WIDE)
        self._button.setFixedHeight(scale_px_length(BTN_PRIMARY_WIDE_PX))
        self._button.setAutoDefault(False)
        self._button.clicked.connect(self._on_update)
        col.addWidget(self._button)

        hint = QLabel("", self._card)
        hint.setObjectName("updateGateHint")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._hint = hint
        col.addWidget(hint)

        outer.addWidget(self._card, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(2)
        self.hide()

    def offer(self, version: str, note: str = "", installed: str = "") -> None:
        """Require one version, with the served line about what it brings."""
        self.version = str(version or "")
        self._title.setText(self.tr("{product} {version} is out").format(
            product=PRODUCT_NAME, version=self.version))
        self._note.setText(str(note or ""))
        self._note.setVisible(bool(note))
        if installed:
            self._hint.setText(self.tr("You have {installed}.").format(installed=installed))
        self._hint.setVisible(bool(installed))
        self.setVisible(bool(self.version))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        margins = self.layout().contentsMargins()
        room = max(200, self.width() - margins.left() - margins.right())
        self._card.setFixedWidth(min(_CARD_MAX_W, room))

    def _on_update(self) -> None:
        self.update_clicked.emit(self.version)
        open_plugin_manager_updates()
