# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


from __future__ import annotations

from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.PyQt.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout

from ..core import accounts, accounts_oauth
from ..core.accounts_drive import GOOGLE
from . import style as S
from .account_browser import BrowserWait, failure_text
from .account_card import _SAVE_QSS, AccountMark, _privacy_row
from .card_base import msg_kind_colour
from .confirm_dialog import SIDE_PAD
from .font_scale import scale_px_length, scale_qss_font_px
from .settings_pages import GHOST_BTN_QSS
from .shared import available_screen_rect
from .style import INK, INK_2
from .widgets import ChatLabel


def tr(text: str) -> str:
    return QCoreApplication.translate("AccountDrive", text)


def _intro(provider: str, sharepoint: bool = False) -> tuple[str, str]:
    if provider == GOOGLE:
        return (tr("Connect Google Drive"),
                tr("You pick the files the AI agent may open. It sees nothing else in your Drive."))
    if sharepoint:
        return (tr("Connect SharePoint"),
                tr("Reading SharePoint sites needs one more permission. "
                   "Your organization's admin may have to approve it."))
    return (tr("Connect OneDrive and SharePoint"),
            tr("Read-only: the AI agent can open your files, never change or delete them."))


def card_texts(kind: str, provider: str, need: dict) -> tuple[str, str]:

    label = (accounts.PROVIDERS.get(provider) or {}).get("label") or provider
    sharepoint = need.get("reason") == "sharepoint"
    if kind == "consent":
        what = str(need.get("what") or "").strip()
        return (tr("Use your {label} access?").format(label=label),
                f"{what} {tr('Read with your account, only for this request.')}".strip())
    if kind == "pick":
        return (tr("Choose this file in Google Drive"),
                tr("The AI agent sees only the files you pick. Pick this one to let it open it."))
    if kind == "retry" and sharepoint:
        return tr("Allow SharePoint sites"), _intro(provider, True)[1]
    if kind == "retry":
        name = accounts_oauth.browser_name(provider, label)
        return (tr("Sign in again"), tr("{name} no longer accepts your saved sign-in.").format(name=name))
    return _intro(provider, sharepoint)


class ConnectDialog(QDialog):



    method = "oauth"

    def __init__(self, provider: str, parent=None):
        super().__init__(parent)
        S.with_platform_font(self)
        self.setObjectName("driveConnect")
        self.provider = provider
        self.saved = False
        self._wait: BrowserWait | None = None
        info = accounts.PROVIDERS.get(provider) or {}
        self._label_text = str(info.get("label") or provider)
        self._name = accounts_oauth.browser_name(provider, self._label_text)
        title, text = _intro(provider)
        self.setWindowTitle(title)
        self.setModal(True)
        available = available_screen_rect(self)
        cap_w = (available.width() - 48) if available is not None else scale_px_length(420)
        self.setFixedWidth(max(300, min(scale_px_length(420), cap_w)))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SIDE_PAD, scale_px_length(24), SIDE_PAD, scale_px_length(18))
        layout.setSpacing(0)
        layout.addWidget(AccountMark(self, str(info.get("connector") or ""), str(info.get("host") or ""), 44),
                         0, Qt.AlignmentFlag.AlignLeft)
        layout.addSpacing(scale_px_length(14))
        heading = ChatLabel(title, self, wrap=True, selectable=False)
        heading.setObjectName("driveTitle")
        heading.setStyleSheet(scale_qss_font_px(
            f"QLabel {{ font-size: 17px; font-weight: 600; color: {INK}; background: transparent; }}"))
        layout.addWidget(heading)
        layout.addSpacing(scale_px_length(4))
        line = ChatLabel(text, self, wrap=True, selectable=False)
        line.setObjectName("driveText")
        line.setStyleSheet(scale_qss_font_px(
            f"QLabel {{ font-size: 12px; color: {INK_2}; background: transparent; }}"))
        layout.addWidget(line)
        layout.addSpacing(scale_px_length(12))
        self._slot = QVBoxLayout()
        self._slot.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._slot)
        self._error = ChatLabel("", self, wrap=True, selectable=True)
        self._error.setObjectName("driveError")
        self._error.setStyleSheet(scale_qss_font_px(
            f"QLabel {{ font-size: 12px; color: {msg_kind_colour('error')}; background: transparent; }}"))
        self._error.hide()
        layout.addWidget(self._error)
        layout.addSpacing(scale_px_length(14))
        layout.addWidget(_privacy_row(self, tr(
            "Your sign-in stays in QGIS's encrypted vault on this computer. "
            "Never sent to TerraLab or the AI agent.")))
        layout.addSpacing(scale_px_length(20))
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(scale_px_length(8))
        footer.addStretch(1)
        self._cancel = QPushButton(tr("Cancel"), self)
        self._cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel.setObjectName("driveCancel")
        self._cancel.setStyleSheet(GHOST_BTN_QSS)
        self._cancel.clicked.connect(self.reject)
        footer.addWidget(self._cancel)
        self._primary = QPushButton(tr("Sign in with {name}").format(name=self._name), self)
        self._primary.setCursor(Qt.CursorShape.PointingHandCursor)
        self._primary.setObjectName("drivePrimary")
        self._primary.setStyleSheet(_SAVE_QSS)
        self._primary.setDefault(True)
        self._primary.clicked.connect(self._start)
        footer.addWidget(self._primary)
        self._footer = footer
        layout.addLayout(footer)

    def _start(self) -> None:
        if self._wait is not None:
            return
        self._error.hide()
        self._primary.hide()
        self._cancel.hide()
        self._wait = BrowserWait(self.provider, self._name, self)
        self._wait.finished.connect(self._done)
        self._slot.addWidget(self._wait)
        self._wait.start()

    def _done(self, ok: bool, reason: str) -> None:
        wait, self._wait = self._wait, None
        if wait is not None:
            wait.hide()
            wait.deleteLater()
        if ok:
            self.saved = True
            self.accept()
            return
        said = failure_text(reason, self._label_text)
        self._error.setText(said)
        self._error.setVisible(bool(said))
        self._primary.setText(tr("Try again") if said else tr("Sign in with {name}").format(name=self._name))
        self._primary.show()
        self._cancel.show()

    def done(self, result) -> None:  # noqa: D401
        if self._wait is not None:
            self._wait.cancel()
        super().done(result)
