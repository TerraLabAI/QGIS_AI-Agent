# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later













from __future__ import annotations

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from .card_controls import _ASK_CARD_QSS, _ASK_INPUT_QSS, _ASK_TEXT_QSS, _BTN_PRIMARY_PILL, _PILL_PX
from .font_scale import scale_px_length, scale_qss_font_px
from .icons import pixmap_for
from .style import _BTN_CHIP_CLOSE, FONT_BODY, HIT_PX, HOVER_ON, INK_2, INK_3, SPACE_CARD, keyboard_ring
from .styles import WARNING_TEXT
from .widgets import IconButton, placeholder_ink

MAX_CHARS = 500
_CLOSE_PX = 14
_GLYPH_PX = 14
_NAME = "crashCommentCard"
_BODY_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BODY}px; color: {INK_2}; background: transparent; border: none; }}"
)
_SEND_DISABLED_QSS = f"QPushButton:disabled {{ background: {HOVER_ON}; color: {INK_3}; }}"
_ERROR_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BODY}px; color: {WARNING_TEXT}; background: transparent; border: none; }}"
)


class CrashCommentBar(QWidget):


    sent = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._key = ""
        self._sending = False
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, SPACE_CARD, 12, 0)
        outer.setSpacing(0)
        card = QWidget(self)
        card.setObjectName(_NAME)
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setStyleSheet(_ASK_CARD_QSS.replace("{name}", _NAME))
        column = QVBoxLayout(card)


        column.setContentsMargins(16, 10, 10, 14)
        column.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(8)
        glyph = QLabel(card)
        glyph.setPixmap(pixmap_for(glyph, "warning", scale_px_length(_GLYPH_PX), QColor(WARNING_TEXT)))
        glyph.setStyleSheet("background: transparent; border: none;")
        head.addWidget(glyph, 0, Qt.AlignmentFlag.AlignVCenter)
        title = QLabel(self.tr("QGIS closed unexpectedly"), card)
        title.setStyleSheet(_ASK_TEXT_QSS)
        head.addWidget(title, 1, Qt.AlignmentFlag.AlignVCenter)
        close = IconButton(card, None, _CLOSE_PX, self.tr("Dismiss"), _BTN_CHIP_CLOSE)
        close.set_icon("close", _CLOSE_PX)
        close.setFixedSize(HIT_PX, HIT_PX)
        close.clicked.connect(self.dismiss)
        head.addWidget(close, 0, Qt.AlignmentFlag.AlignVCenter)
        column.addLayout(head)

        body = QLabel(self.tr("The crash report was sent. What were you doing just before?"), card)
        body.setWordWrap(True)
        body.setStyleSheet(_BODY_QSS)
        column.addWidget(body)
        column.addSpacing(8)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 6, 0)
        row.setSpacing(8)
        self._edit = QLineEdit(card)
        self._edit.setMaxLength(MAX_CHARS)
        self._edit.setPlaceholderText(self.tr("Example: I had the attribute table open and pressed Save"))
        placeholder_ink(self._edit)
        self._edit.setStyleSheet(_ASK_INPUT_QSS)
        self._edit.setFixedHeight(scale_px_length(_PILL_PX))
        self._edit.textChanged.connect(self._sync_send)
        self._edit.returnPressed.connect(self._on_send)
        row.addWidget(self._edit, 1)
        self._send = QPushButton(self.tr("Send"), card)
        self._send.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send.setStyleSheet(_BTN_PRIMARY_PILL + _SEND_DISABLED_QSS)
        keyboard_ring(self._send)
        self._send.setFixedHeight(scale_px_length(_PILL_PX))
        self._send.clicked.connect(self._on_send)
        row.addWidget(self._send)
        column.addLayout(row)

        self._error = QLabel(self.tr("Not sent. Press Send to try again."), card)
        self._error.setWordWrap(True)
        self._error.setStyleSheet(_ERROR_QSS)
        self._error.hide()
        column.addSpacing(4)
        column.addWidget(self._error)

        outer.addWidget(card)
        self._sync_send()
        self.hide()

    def offer(self, key: str) -> None:

        self._key = key
        self._sending = False
        self._edit.clear()
        self._edit.setReadOnly(False)
        self._error.hide()
        self._sync_send()
        self.show()

    def dismiss(self) -> None:
        self._key = ""
        self._sending = False
        self._edit.clear()
        self._edit.setReadOnly(False)
        self._error.hide()
        self.hide()

    def confirm(self, key: str) -> None:

        if key == self._key:
            self.dismiss()

    def failed(self, key: str) -> None:

        if key != self._key:
            return
        self._sending = False
        self._edit.setReadOnly(False)
        self._error.show()
        self._sync_send()

    def _sync_send(self) -> None:
        self._send.setEnabled(bool(self._edit.text().strip()) and not self._sending)

    def _on_send(self) -> None:
        text = self._edit.text().strip()
        if not text or not self._key or self._sending:
            return
        self._sending = True
        self._edit.setReadOnly(True)
        self._error.hide()
        self._sync_send()
        self.sent.emit(self._key, text[:MAX_CHARS])
