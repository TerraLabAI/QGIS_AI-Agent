# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Typed confirmation before an account erasure is scheduled."""






from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from .settings_pages import DANGER_GHOST_BTN_QSS, GHOST_BTN_QSS, INPUT_QSS, MUTED
from .shared import exec_dialog, tr


class DeleteAccountDialog(QDialog):
    """State what is lost, then ask for the address before enabling the button."""

    def __init__(self, email: str, parent=None):
        super().__init__(parent)
        self._email = str(email or "").strip()
        self.setWindowTitle(tr("Delete my account"))
        self.setModal(True)
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel(tr("Delete your TerraLab account"), self)
        title.setStyleSheet("font-size: 14px; font-weight: 600;")
        title.setWordWrap(True)
        layout.addWidget(title)

        body = QLabel(tr(
            "Your account and the data attached to it are erased. Every TerraLab plugin "
            "stops working right away, on this computer and on any other, and a paid "
            "subscription stops renewing.\n\n"
            "The erasure is final once the grace period ends. Until then you can cancel it "
            "by signing in on terra-lab.ai."), self)
        body.setWordWrap(True)
        layout.addWidget(body)

        who = QLabel(tr("Signed in as {email}").format(email=self._email), self)
        who.setWordWrap(True)
        who.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        who.setStyleSheet("font-weight: 600;")
        layout.addWidget(who)

        ask = QLabel(tr("Type that address to confirm."), self)
        ask.setWordWrap(True)
        ask.setStyleSheet(f"font-size: 11px; color: {MUTED};")
        layout.addWidget(ask)

        self._line = QLineEdit(self)
        self._line.setStyleSheet(INPUT_QSS)
        self._line.setPlaceholderText(self._email)
        self._line.textChanged.connect(self._on_typed)
        layout.addWidget(self._line)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        cancel = QPushButton(tr("Cancel"), self)
        cancel.setStyleSheet(GHOST_BTN_QSS)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self._confirm = QPushButton(tr("Delete my account"), self)
        self._confirm.setStyleSheet(DANGER_GHOST_BTN_QSS)
        self._confirm.setCursor(Qt.CursorShape.PointingHandCursor)
        self._confirm.setAutoDefault(False)
        self._confirm.setEnabled(False)
        self._confirm.clicked.connect(self.accept)
        buttons.addWidget(self._confirm)
        layout.addLayout(buttons)

    def _on_typed(self, text: str) -> None:
        self._confirm.setEnabled(text.strip().lower() == self._email.strip().lower())

    def confirmation(self) -> str:
        """What the user typed, sent to the server as it was typed."""
        return self._line.text().strip()


def ask_delete_account(email: str, parent=None) -> str:
    """The typed address when it matches and the user confirmed, otherwise ""."""
    email = str(email or "").strip()
    if not email:
        return ""
    dialog = DeleteAccountDialog(email, parent)
    try:
        if exec_dialog(dialog) != QDialog.DialogCode.Accepted:
            return ""
        typed = dialog.confirmation()
        if typed.lower() != email.lower():
            return ""
        return typed
    finally:



        dialog.deleteLater()
