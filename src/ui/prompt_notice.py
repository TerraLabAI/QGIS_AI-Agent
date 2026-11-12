# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .font_scale import scale_qss_font_px
from .icons import icon_for
from .style import (
    _BTN_GHOST,
    FONT_BODY,
    FONT_HINT,
    INK,
    INK_2,
    ORANGE,
    ORANGE_TINT,
    RADIUS_CONTROL,
    SPACE_CARD,
    SPACE_OUTER,
    WEIGHT_MEDIUM,
    qcolor,
)

_ICON_PX = 14
_BUTTON_PX = 26


class PromptNotice(QFrame):
    send_anyway = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("promptNotice")
        self.setStyleSheet(f"QFrame#promptNotice {{ background: {ORANGE_TINT}; border: none;"
                           f" border-radius: {RADIUS_CONTROL}px; }}")
        box = QVBoxLayout(self)
        box.setContentsMargins(SPACE_OUTER, SPACE_CARD, SPACE_CARD, SPACE_CARD)
        box.setSpacing(SPACE_CARD)
        row = QHBoxLayout()
        row.setSpacing(SPACE_CARD)
        box.addLayout(row)
        self._icon = QLabel(self)
        self._icon.setFixedSize(_ICON_PX, _ICON_PX)
        row.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(1)
        row.addLayout(words, 1)
        self._title = QLabel(self)
        self._title.setWordWrap(True)
        self._title.setStyleSheet(scale_qss_font_px(
            f"font-size: {FONT_BODY}px; color: {INK}; font-weight: {WEIGHT_MEDIUM};"
            " background: transparent; border: none;"))
        words.addWidget(self._title)
        self._body = QLabel(self)
        self._body.setWordWrap(True)
        self._body.setStyleSheet(scale_qss_font_px(
            f"font-size: {FONT_HINT}px; color: {INK_2}; background: transparent; border: none;"))
        words.addWidget(self._body)
        self._button = QPushButton(self)
        self._button.setStyleSheet(scale_qss_font_px(_BTN_GHOST))
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.setFixedHeight(_BUTTON_PX)
        self._button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._button.clicked.connect(self.send_anyway.emit)

        box.addWidget(self._button, 0, Qt.AlignmentFlag.AlignRight)
        self.hide()

    def ask(self, title: str, body: str, action: str) -> None:
        self._icon.setPixmap(icon_for(self, "warning", _ICON_PX, qcolor(ORANGE)).pixmap(_ICON_PX, _ICON_PX))
        self._title.setText(title)
        self._body.setText(body)
        self._button.setText(action)

        self.setAccessibleName(f"{title} {body}")
        self.show()

    def text(self) -> str:
        return "" if self.isHidden() else self._body.text()
