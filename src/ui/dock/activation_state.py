# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later













from __future__ import annotations

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..font_scale import scale_qss_font_px
from ..style import (
    _BTN_DANGER_GHOST,
    _BTN_GHOST,
    _BTN_PRIMARY_WIDE,
    BTN_PILL_PX,
    BTN_PRIMARY_WIDE_PX,
    FONT_BASE,
    FONT_HINT,
    INK_2,
    MUTED,
    SPACE_CARD,
    SPACE_OUTER,
    keyboard_ring,
)
from ..styles import PANEL_TEXT, _msg_label_qss
from ..widgets import Spinner, placeholder_ink


_TITLE_QSS = (scale_qss_font_px(f"font-weight: 600; font-size: {FONT_BASE + 3}px; color: {PANEL_TEXT};"
              " background: transparent; border: none;"))
_PITCH_QSS = scale_qss_font_px(
    f"font-size: {FONT_BASE}px; color: {INK_2}; background: transparent; border: none;")


_TOP_MARGIN = 18
_TITLE_GAP = 14
_HINT_QSS = scale_qss_font_px(f"font-size: {FONT_HINT}px; color: {MUTED}; background: transparent; border: none;")


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
        outer.setContentsMargins(12, _TOP_MARGIN, 12, SPACE_OUTER)
        outer.setSpacing(0)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(SPACE_OUTER)


        outer.addStretch(1)
        outer.addLayout(col)
        outer.addStretch(2)

        title = QLabel(self.tr("Your AI agent inside QGIS"), self)
        title.setStyleSheet(_TITLE_QSS)
        title.setWordWrap(True)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(title)
        self._pitch = QLabel(
            self.tr("Ask it to load data, style layers, run analyses or edit features."), self)
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
        col.addSpacing(_TITLE_GAP)

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
        self._connect_btn = QPushButton(self.tr("Sign in or create an account"), self._connect_section)
        self._connect_btn.setToolTip(self.tr("Sign in via your browser to start using AI Agent"))
        self._connect_btn.setFixedHeight(BTN_PRIMARY_WIDE_PX)
        self._connect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._connect_btn.setStyleSheet(_BTN_PRIMARY_WIDE)
        keyboard_ring(self._connect_btn)
        self._connect_btn.setAutoDefault(False)
        self._connect_btn.clicked.connect(self.sign_in_requested.emit)
        connect_col.addWidget(self._connect_btn)

        self._hint = QLabel(self.tr("Free. Opens your browser, takes about 15 seconds."),
                            self._connect_section)
        self._hint.setStyleSheet(_HINT_QSS)
        self._hint.setWordWrap(True)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        connect_col.addSpacing(4)
        connect_col.addWidget(self._hint)
        col.addWidget(self._connect_section)

        self._wait_section = QWidget(self)
        wait_col = QVBoxLayout(self._wait_section)
        wait_col.setContentsMargins(0, 0, 0, 0)
        wait_col.setSpacing(SPACE_OUTER)
        status_row = QHBoxLayout()
        status_row.setSpacing(SPACE_OUTER)
        status_row.addStretch(1)
        self._spinner = Spinner(16, parent=self._wait_section)
        status_row.addWidget(self._spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        self._wait_status = QLabel(self.tr("Waiting for your browser sign-in..."), self._wait_section)
        self._wait_status.setStyleSheet(scale_qss_font_px("font-size: 12px; color: " + PANEL_TEXT + ";"))

        self._wait_status.setWordWrap(True)
        status_row.addWidget(self._wait_status, 0)
        status_row.addStretch(1)
        wait_col.addLayout(status_row)
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

        outer.addStretch(1)

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
        self._hint.setVisible(not message)
        self._pitch.setVisible(not message)
        self._connect_btn.setText(self.tr("Sign in again") if message
                                  else self.tr("Sign in or create an account"))

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
