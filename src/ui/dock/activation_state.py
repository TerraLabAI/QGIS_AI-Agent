# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The sign-in card, in its three states."""











from __future__ import annotations

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..font_scale import scale_px_length
from ..header import letter_badge
from ..icons import logo_pixmap, logo_size, pixmap_for
from ..style import (
    _BTN_AVATAR,
    _BTN_DANGER_GHOST,
    _BTN_GHOST,
    _BTN_PRIMARY_WIDE,
    _BTN_QUIET,
    BTN_PILL_PX,
    BTN_PRIMARY_WIDE_PX,
    FONT_BASE,
    FONT_HINT,
    HAIRLINE,
    MONO_FAMILY,
    MUTED,
    RADIUS_CARD,
    SPACE_CARD,
    SPACE_OUTER,
)
from ..styles import _CARD_MARGINS, _msg_label_qss
from ..widgets import Spinner


_TITLE_QSS = (f"font-weight: 600; font-size: {FONT_BASE + 3}px; color: palette(text);"
              " background: transparent; border: none;")
_HINT_CARD_QSS = (
    f"QFrame#signinHintCard {{ background: palette(base); border: 1px solid {HAIRLINE};"
    f" border-radius: {RADIUS_CARD}px; }}"
    "QLabel { background: transparent; border: none; }"
)


_TOP_MARGIN = 18
_TITLE_GAP = 14
_HINT_QSS = f"font-size: {FONT_HINT}px; color: {MUTED}; background: transparent; border: none;"
_CODE_QSS = (
    f"font-family: {MONO_FAMILY}; font-size: 20px; font-weight: 700;"
    " letter-spacing: 2px; color: palette(text); background: rgba(128, 128, 128, 0.12);"
    f" border: 1px solid rgba(128, 128, 128, 0.25); border-radius: {RADIUS_CARD}px; padding: 6px 12px;"
)


class ActivationCard(QWidget):
    """Signed out, pairing in progress, or activated."""

    sign_in_requested = pyqtSignal()
    pairing_reopen_requested = pyqtSignal()
    pairing_cancel_requested = pyqtSignal()
    account_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "signed_out"
        self.pairing_code = ""
        self.pairing_url = ""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, _TOP_MARGIN, 12, SPACE_OUTER)
        outer.setSpacing(0)


        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(SPACE_OUTER)


        outer.addStretch(1)
        outer.addLayout(col)
        outer.addStretch(2)



        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(7)
        head.addStretch(1)
        title = QLabel(self.tr("Drive QGIS in plain language"), self)
        title.setStyleSheet(_TITLE_QSS)
        head.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        mark = QLabel(self)
        mark.setFixedSize(logo_size(18))
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setPixmap(logo_pixmap(self, 18))
        mark.setStyleSheet("background: transparent; border: none;")
        head.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        col.addLayout(head)


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
        self._connect_btn = QPushButton(self.tr("Sign in / Sign up to start"), self._connect_section)
        self._connect_btn.setToolTip(self.tr("Sign in via your browser to start using AI Agent"))
        self._connect_btn.setFixedHeight(BTN_PRIMARY_WIDE_PX)
        self._connect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._connect_btn.setStyleSheet(_BTN_PRIMARY_WIDE)
        self._connect_btn.setAutoDefault(False)
        self._connect_btn.clicked.connect(self.sign_in_requested.emit)
        connect_col.addWidget(self._connect_btn)

        hint_card = QFrame(self._connect_section)
        hint_card.setObjectName("signinHintCard")
        hint_card.setStyleSheet(_HINT_CARD_QSS)
        hint_col = QVBoxLayout(hint_card)
        hint_col.setContentsMargins(*_CARD_MARGINS)
        hint_col.setSpacing(5)
        for text in (
            self.tr("Signing up is free and takes 15 seconds, in your browser."),
            self.tr("Then ask the AI for anything: load data, style layers, run analyses, edit features."),
        ):
            row = QHBoxLayout()
            row.setSpacing(7)
            check = QLabel(hint_card)
            check.setFixedSize(14, 16)
            check.setAlignment(Qt.AlignmentFlag.AlignCenter)
            check.setPixmap(pixmap_for(hint_card, "check", 12))
            row.addWidget(check, 0, Qt.AlignmentFlag.AlignTop)
            label = QLabel(text, hint_card)
            label.setWordWrap(True)
            label.setStyleSheet("font-size: 11px; color: palette(text); border: none; background: transparent;")
            row.addWidget(label, 1)
            hint_col.addLayout(row)
        connect_col.addWidget(hint_card)
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
        self._wait_status.setStyleSheet("font-size: 12px; color: palette(text);")
        status_row.addWidget(self._wait_status, 0, Qt.AlignmentFlag.AlignVCenter)
        status_row.addStretch(1)
        wait_col.addLayout(status_row)
        self._code_label = QLabel(self._wait_section)
        self._code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._code_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._code_label.setStyleSheet(_CODE_QSS)
        self._code_label.setToolTip(self.tr("Type this code in the browser if it asks for one"))
        self._code_label.hide()
        wait_col.addWidget(self._code_label)
        self._code_hint = QLabel(self.tr("Your browser asks for this code."), self._wait_section)
        self._code_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._code_hint.setStyleSheet(_HINT_QSS)
        self._code_hint.hide()
        wait_col.addWidget(self._code_hint)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(SPACE_CARD)
        self._reopen_btn = QPushButton(self.tr("Open browser"), self._wait_section)
        self._reopen_btn.setToolTip(self.tr("Did nothing open? Open the sign-in page again"))
        self._reopen_btn.setFixedHeight(BTN_PILL_PX)
        self._reopen_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reopen_btn.setStyleSheet(_BTN_GHOST)
        self._reopen_btn.setAutoDefault(False)
        self._reopen_btn.clicked.connect(self.pairing_reopen_requested.emit)
        btn_row.addWidget(self._reopen_btn)
        self._cancel_btn = QPushButton(self.tr("Cancel"), self._wait_section)
        self._cancel_btn.setFixedHeight(BTN_PILL_PX)
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.setStyleSheet(_BTN_DANGER_GHOST)
        self._cancel_btn.setAutoDefault(False)
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._cancel_btn)
        wait_col.addLayout(btn_row)
        self._wait_section.hide()
        col.addWidget(self._wait_section)


        self._account_section = QFrame(self)
        self._account_section.setObjectName("accountChip")
        self._account_section.setStyleSheet(
            f"QFrame#accountChip {{ background: palette(base);"
            f" border: 1px solid {HAIRLINE}; border-radius: {RADIUS_CARD}px; }}"
            "QLabel { background: transparent; border: none; }")
        chip_row = QHBoxLayout(self._account_section)
        chip_row.setContentsMargins(12, 10, 12, 10)
        chip_row.setSpacing(11)
        self._avatar_btn = QToolButton(self._account_section)
        self._avatar_btn.setStyleSheet(_BTN_AVATAR)
        diameter = scale_px_length(32)
        self._avatar_diameter = diameter
        self._avatar_btn.setFixedSize(diameter + 4, diameter + 4)
        self._avatar_btn.setIconSize(self._avatar_btn.size())
        self._avatar_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._avatar_btn.setToolTip(self.tr("Account"))
        self._avatar_btn.setAccessibleName(self.tr("Account"))
        self._avatar_btn.clicked.connect(self.account_clicked.emit)
        chip_row.addWidget(self._avatar_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        id_col = QVBoxLayout()
        id_col.setSpacing(2)
        self._email_label = QLabel(self._account_section)
        self._email_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._email_label.setStyleSheet("font-size: 13px; font-weight: 600; color: palette(text);")
        id_col.addWidget(self._email_label)
        connected = QLabel("✓ " + self.tr("Connected"), self._account_section)
        connected.setStyleSheet(f"font-size: 11px; color: {MUTED};")
        id_col.addWidget(connected)
        chip_row.addLayout(id_col, 1)
        account_btn = QPushButton(self.tr("Account"), self._account_section)
        account_btn.setStyleSheet(_BTN_QUIET)
        account_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        account_btn.setAutoDefault(False)
        account_btn.clicked.connect(self.account_clicked.emit)
        chip_row.addWidget(account_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self._account_section.hide()
        col.addWidget(self._account_section)


        self._message = QLabel(self)
        self._message.setWordWrap(True)
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.hide()
        col.addWidget(self._message)

        outer.addStretch(1)



    def show_signed_out(self, message: str = "") -> None:
        """Signed out."""


        self.state = "signed_out"
        self.pairing_code = ""
        self._spinner.stop()
        self._wait_section.hide()
        self._account_section.hide()
        self._connect_section.show()
        if message:
            self._set_subtitle(self.tr("Your sign-in is no longer valid on this computer."))
            self._connect_btn.setText(self.tr("Sign in again"))
            self.set_message(message, "warning")
        else:
            self._set_subtitle("")
            self._connect_btn.setText(self.tr("Sign in / Sign up to start"))
            self.clear_message()

    def show_pairing_waiting(self, code: str = "", url: str = "") -> None:
        self.state = "pairing"
        self.pairing_code = code or ""
        self.pairing_url = url or ""
        self._connect_section.hide()
        self._account_section.hide()
        self._message.hide()
        self._code_label.setText(self.pairing_code)
        self._code_label.setVisible(bool(self.pairing_code))
        self._code_hint.setVisible(bool(self.pairing_code))
        self._wait_section.show()
        self._spinner.start()
        self._set_subtitle(self.tr("Finish the sign-in in your browser, then come back here."))

    def set_pairing_status(self, text: str) -> None:
        """The line beside the spinner, while the browser sign-in waits."""
        if self.state == "pairing" and text:
            self._wait_status.setText(text)

    def set_pairing_note(self, text: str, kind: str = "warning") -> None:
        """Something the waiting user has to act on: the browser did not open, or the page has not been reached yet."""


        if self.state == "pairing":
            self.set_message(text, kind)

    def pairing_finished(self) -> None:
        """Signed in: the card goes behind the thread from here on, so the spinner has to stop rather than animate where nobody can see it."""

        if self.state == "pairing":
            self.show_signed_out()

    def show_activated(self, email: str, avatar_url: str = "") -> None:
        self.state = "activated"
        self._spinner.stop()
        self._connect_section.hide()
        self._wait_section.hide()
        self._message.hide()
        self._email_label.setText(email or "")
        letter = (email or "?").strip()[:1].upper() or "?"
        self._avatar_btn.setIcon(QIcon(letter_badge(self, letter, self._avatar_diameter)))
        if avatar_url:
            try:
                from ..account_avatar import cached_avatar_pixmap, is_avatar_url_usable

                if is_avatar_url_usable(avatar_url):
                    pixmap = cached_avatar_pixmap(avatar_url, self._avatar_diameter)
                    if pixmap is not None:
                        self._avatar_btn.setIcon(QIcon(pixmap))
            except ImportError:
                pass
        self._account_section.show()
        self._set_subtitle(self.tr("You are signed in."))

    def _set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))

    def set_message(self, text: str, kind: str = "info") -> None:
        """A framed line under the buttons, per the message taxonomy."""
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
