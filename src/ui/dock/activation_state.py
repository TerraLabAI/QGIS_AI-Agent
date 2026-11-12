# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later












from __future__ import annotations

import os

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QPointF, QRectF, Qt, pyqtSignal
from qgis.PyQt.QtGui import QPainter, QPen, QPixmap
from qgis.PyQt.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..font_scale import scale_qss_font_px, ui_font_scale
from ..style import (
    _BTN_DANGER_GHOST,
    _BTN_GHOST,
    _BTN_PRIMARY_WIDE,
    BTN_PILL_PX,
    BTN_PRIMARY_WIDE_PX,
    FONT_BASE,
    FONT_HINT,
    INK_2,
    INK_3,
    LINE,
    MUTED,
    SPACE_CARD,
    SPACE_OUTER,
    SURFACE,
    keyboard_ring,
    qcolor,
)
from ..styles import PANEL_TEXT, _msg_label_qss
from ..widgets import Spinner, placeholder_ink

_TITLE_QSS = scale_qss_font_px(f"font-weight: 650; font-size: {FONT_BASE + 7}px; color: {PANEL_TEXT};"
                               " background: transparent; border: none;")
_PITCH_QSS = scale_qss_font_px(
    f"font-size: {FONT_BASE}px; color: {INK_2}; background: transparent; border: none;")
_HINT_QSS = scale_qss_font_px(f"font-size: {FONT_HINT}px; color: {MUTED}; background: transparent; border: none;")
_CARD_QSS = f"#signInCard {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 16px; }}"

_SIGN_IN_PX = BTN_PRIMARY_WIDE_PX + 10


_SIGN_IN_SIZES = (FONT_BASE + 2, FONT_BASE, FONT_BASE - 1, FONT_BASE - 2)
_SIGN_IN_PAD = 10


def _sign_in_qss(size: int) -> str:
    return (_BTN_PRIMARY_WIDE.replace(f"font-size: {FONT_BASE}px", f"font-size: {size}px")
            .replace("padding: 0 18px", f"padding: 0 {_SIGN_IN_PAD}px")
            .replace("padding: 0 16px", f"padding: 0 {_SIGN_IN_PAD - 2}px"))


def _qgis_logo() -> QPixmap:

    big = os.path.join(QgsApplication.iconsPath(), "qgis-icon-512x512.png")
    return QPixmap(big if os.path.exists(big) else QgsApplication.appIconPath())


class _Pair(QWidget):



    def __init__(self, parent=None):
        super().__init__(parent)
        self._qgis = _qgis_logo()
        self.setFixedHeight(64)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        cx, cy = self.width() / 2, self.height() / 2
        if not self._qgis.isNull():
            p.drawPixmap(QRectF(cx + 44 - 22, cy - 22, 44, 44), self._qgis, QRectF(self._qgis.rect()))
        p.setPen(QPen(qcolor(INK_3), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx - 6, cy), QPointF(cx + 6, cy))
        p.drawLine(QPointF(cx, cy - 6), QPointF(cx, cy + 6))


class ActivationCard(QWidget):


    sign_in_requested = pyqtSignal()
    pairing_reopen_requested = pyqtSignal()
    pairing_cancel_requested = pyqtSignal()

    pairing_code_submitted = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "signed_out"
        self.pairing_code = ""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 16, 12, 16)
        outer.setSpacing(0)

        card = QWidget(self)
        card.setObjectName("signInCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setStyleSheet(_CARD_QSS)
        col = QVBoxLayout(card)
        col.setContentsMargins(16, 26, 16, 18)
        col.setSpacing(SPACE_OUTER)
        outer.addStretch(1)
        outer.addWidget(card)
        outer.addStretch(1)

        self._pair = _Pair(self)
        col.addWidget(self._pair)
        col.addSpacing(10)
        title = QLabel(self.tr("Your AI agent inside QGIS"), self)
        title.setStyleSheet(_TITLE_QSS)
        title.setWordWrap(True)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(title)
        self._pitch = QLabel(
            self.tr("The AI agent loads data, styles layers, runs analyses and edits features for you."), self)
        self._pitch.setStyleSheet(_PITCH_QSS)
        self._pitch.setWordWrap(True)
        self._pitch.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self._pitch)
        self._subtitle = QLabel(self)
        self._subtitle.setStyleSheet(_HINT_QSS)
        self._subtitle.setWordWrap(True)
        self._subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._subtitle.hide()
        col.addWidget(self._subtitle)
        col.addSpacing(14)

        self._connect_section = QWidget(self)
        connect_col = QVBoxLayout(self._connect_section)
        connect_col.setContentsMargins(0, 0, 0, 0)
        connect_col.setSpacing(6)


        self._reason = QLabel(self._connect_section)
        self._reason.setWordWrap(True)
        self._reason.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._reason.setStyleSheet(_msg_label_qss("warning"))
        self._reason.hide()
        connect_col.addWidget(self._reason)
        self._connect_btn = QPushButton(self.tr("Sign in or create a free account"), self._connect_section)
        self._connect_btn.setToolTip(self.tr("Opens your browser, takes about 15 seconds"))
        self._connect_btn.setFixedHeight(_SIGN_IN_PX)
        self._connect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sign_in_size = 0
        self._fit_sign_in()
        keyboard_ring(self._connect_btn)
        self._connect_btn.setAutoDefault(False)
        self._connect_btn.clicked.connect(self.sign_in_requested.emit)
        connect_col.addWidget(self._connect_btn)
        col.addWidget(self._connect_section)

        self._wait_section = QWidget(self)
        wait_col = QVBoxLayout(self._wait_section)
        wait_col.setContentsMargins(0, 0, 0, 0)
        wait_col.setSpacing(SPACE_OUTER)
        self._spinner = Spinner(16, parent=self._wait_section)
        wait_col.addWidget(self._spinner, 0, Qt.AlignmentFlag.AlignHCenter)
        self._wait_status = QLabel(self.tr("Waiting for your browser sign-in..."), self._wait_section)
        self._wait_status.setStyleSheet(scale_qss_font_px("font-size: 12px; color: " + PANEL_TEXT + ";"))
        self._wait_status.setWordWrap(True)
        self._wait_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wait_col.addWidget(self._wait_status)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(SPACE_CARD)
        self._reopen_btn = QPushButton(self.tr("Open browser"), self._wait_section)
        self._reopen_btn.setToolTip(self.tr("Did nothing open? Open the sign-in page again"))
        self._reopen_btn.setFixedHeight(BTN_PILL_PX)
        self._reopen_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reopen_btn.setStyleSheet(_BTN_GHOST)
        keyboard_ring(self._reopen_btn)
        self._reopen_btn.setAutoDefault(False)
        self._reopen_btn.clicked.connect(self.pairing_reopen_requested.emit)
        btn_row.addWidget(self._reopen_btn)
        self._cancel_btn = QPushButton(self.tr("Cancel"), self._wait_section)
        self._cancel_btn.setFixedHeight(BTN_PILL_PX)
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.setStyleSheet(_BTN_DANGER_GHOST)
        keyboard_ring(self._cancel_btn)
        self._cancel_btn.setAutoDefault(False)
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._cancel_btn)
        wait_col.addLayout(btn_row)


        self._code_section = QWidget(self._wait_section)
        code_col = QVBoxLayout(self._code_section)
        code_col.setContentsMargins(0, 0, 0, 0)
        code_col.setSpacing(6)
        code_label = QLabel(self.tr("Type the code shown in your browser:"), self._code_section)
        code_label.setWordWrap(True)
        code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        code_label.setStyleSheet(scale_qss_font_px("font-size: 12px; color: " + PANEL_TEXT + ";"))
        code_col.addWidget(code_label)
        code_row = QHBoxLayout()
        code_row.setSpacing(SPACE_CARD)
        self._code_input = QLineEdit(self._code_section)
        self._code_input.setMaxLength(9)
        self._code_input.setPlaceholderText("K7P-4QX")
        placeholder_ink(self._code_input)
        self._code_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._code_input.setFixedHeight(BTN_PILL_PX)
        self._code_input.returnPressed.connect(self._submit_code)
        code_row.addWidget(self._code_input, 1)
        self._code_btn = QPushButton(self.tr("Sign in"), self._code_section)
        self._code_btn.setFixedHeight(BTN_PILL_PX)
        self._code_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._code_btn.setStyleSheet(_BTN_GHOST)
        keyboard_ring(self._code_btn)
        self._code_btn.setAutoDefault(False)
        self._code_btn.clicked.connect(self._submit_code)
        code_row.addWidget(self._code_btn)
        code_col.addLayout(code_row)
        code_hint = QLabel(self.tr("Only type a code you see on terra-lab.ai, on a page you opened "
                                   "from this QGIS."), self._code_section)
        code_hint.setWordWrap(True)
        code_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        code_hint.setStyleSheet(_HINT_QSS)
        code_col.addWidget(code_hint)
        self._code_section.hide()
        wait_col.addWidget(self._code_section)
        self._wait_section.hide()
        col.addWidget(self._wait_section)

        self._message = QLabel(self)
        self._message.setWordWrap(True)
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.hide()
        col.addWidget(self._message)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_sign_in()

    def _fit_sign_in(self) -> None:

        from qgis.PyQt.QtGui import QFont, QFontMetrics

        room = self._connect_btn.width() - 2 * _SIGN_IN_PAD - 4
        if room <= 0:
            room = 10_000
        text = self._connect_btn.text()
        size = _SIGN_IN_SIZES[-1]
        for candidate in _SIGN_IN_SIZES:
            font = QFont(self._connect_btn.font())
            font.setPixelSize(round(candidate * max(1.0, ui_font_scale())))
            font.setWeight(QFont.Weight.DemiBold)
            if QFontMetrics(font).horizontalAdvance(text) <= room:
                size = candidate
                break
        if size != self._sign_in_size:
            self._sign_in_size = size
            self._connect_btn.setStyleSheet(scale_qss_font_px(_sign_in_qss(size)))

    def show_signed_out(self, message: str = "") -> None:





        self.state = "signed_out"
        self.pairing_code = ""
        self._spinner.stop()
        self._wait_section.hide()
        self._hide_code_input()
        self._connect_section.show()
        self._set_subtitle("")
        self.clear_message()
        self._reason.setText(message or "")
        self._reason.setVisible(bool(message))
        self._pitch.setVisible(not message)
        self._connect_btn.setText(self.tr("Sign in again") if message
                                  else self.tr("Sign in or create a free account"))
        self._fit_sign_in()

    def mascot_seat(self) -> tuple:

        return self._pair, QPointF(self._pair.width() / 2.0 - 44, self._pair.height() / 2.0 + 26)

    def sign_in_button(self) -> QPushButton:
        return self._connect_btn

    def show_pairing_waiting(self, code: str = "", url: str = "") -> None:
        self.pairing_code = code or ""
        if self.state != "pairing" or not url:
            self._hide_code_input()
        self.state = "pairing"
        self._connect_section.hide()
        self._pitch.hide()
        self._message.hide()
        self._wait_section.show()
        self._spinner.start()
        self._set_subtitle(self.tr("Finish the sign-in in your browser, then come back here."))

    def show_pairing_code_input(self) -> None:

        if self.state != "pairing":
            return
        self._code_input.clear()
        self._code_section.show()
        self._code_input.setFocus()

    def _hide_code_input(self) -> None:
        self._code_input.clear()
        self._code_section.hide()

    def _submit_code(self) -> None:
        text = self._code_input.text().strip()
        if text:
            self.pairing_code_submitted.emit(text)

    def set_pairing_status(self, text: str) -> None:

        if self.state == "pairing" and text:
            self._wait_status.setText(text)

    def set_pairing_note(self, text: str, kind: str = "warning") -> None:



        if self.state == "pairing":
            self.set_message(text, kind)

    def pairing_finished(self) -> None:


        if self.state == "pairing":
            self.show_signed_out()

    def _set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))

    def set_message(self, text: str, kind: str = "info") -> None:

        if not text:
            self.clear_message()
            return
        self._message.setText(text)
        self._message.setStyleSheet(_msg_label_qss(kind))
        self._message.show()

    def clear_message(self) -> None:
        self._message.hide()

    def _on_cancel(self) -> None:
        self.pairing_cancel_requested.emit()
        self.show_signed_out()
