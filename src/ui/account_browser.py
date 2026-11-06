# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QCoreApplication, pyqtSignal
from qgis.PyQt.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ..core import accounts, accounts_oauth
from .card_controls import _BTN_GHOST_PILL, _BTN_QUIET_LINK, AnswerFooter, _pill
from .external_links import open_external_url
from .font_scale import scale_px_length, scale_qss_font_px
from .style import INK, INK_2
from .widgets import ChatLabel

_STEP_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: 13px; font-weight: 600; color: {INK}; background: transparent; border: none; }}")
_HINT_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: 12px; color: {INK_2}; background: transparent; border: none; }}")


class BrowserWait(QWidget):
    finished = pyqtSignal(bool, str)

    def __init__(self, provider: str, name: str, parent=None, need: dict | None = None):
        super().__init__(parent)
        self.setObjectName("accountBrowserWait")
        self.provider, self.name = provider, name
        self.need = dict(need or {})
        self._task = None
        self._page = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(scale_px_length(6))
        self._step = ChatLabel(self.tr("Opening your browser…"), self, wrap=True, selectable=False)
        self._step.setObjectName("accountBrowserStep")
        self._step.setStyleSheet(_STEP_QSS)
        lay.addWidget(self._step)
        self._hint = ChatLabel("", self, wrap=True, selectable=True)
        self._hint.setObjectName("accountBrowserCode")
        self._hint.setStyleSheet(_HINT_QSS)
        self._hint.hide()
        lay.addWidget(self._hint)
        footer = AnswerFooter(self)
        self._again = self._button(self.tr("Open the page again"), _BTN_QUIET_LINK, self._open_page)
        self._again.setObjectName("accountBrowserAgain")
        self._again.hide()
        self._cancel = _pill(self._button(self.tr("Cancel"), _BTN_GHOST_PILL, self.cancel))
        self._cancel.setObjectName("accountBrowserCancel")
        footer.set_widgets(self._again, [self._cancel])
        row = QHBoxLayout()
        row.setContentsMargins(0, scale_px_length(6), 0, 0)
        row.addWidget(footer)
        lay.addLayout(row)

    def _button(self, text: str, qss: str, slot):
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QPushButton
        button = QPushButton(text, self)
        button.setStyleSheet(qss)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    def start(self) -> None:

        module = accounts_oauth.provider_module(self.provider)
        if module is None or not accounts.vault_ready(prompt=True):
            self.finished.emit(False, "vault")
            return

        task = module.sign_in_for(self.need) if hasattr(module, "sign_in_for") else module.sign_in()
        task.code_ready.connect(self._on_code)
        task.confirmed.connect(self._on_confirmed)
        task.done.connect(self._on_done)
        self._task = task
        QgsApplication.taskManager().addTask(task)

    def cancel(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        try:

            task.done.connect(lambda ok, _reason, t=task, p=self.provider: _discard(p, t, ok))
            task.cancel()
        except RuntimeError:
            pass  # nosec B110
        self.finished.emit(False, "cancelled")

    def _open_page(self) -> None:
        if self._page:
            open_external_url(self._page, self)

    def _on_code(self, user_code: str, page: str) -> None:
        if self._task is None:
            return
        self._page = page
        self._step.setText(self.tr("Finish signing in to {name} in your browser.").format(name=self.name))
        if user_code:
            self._hint.setText(
                self.tr("Check that the page shows the code {code}, then sign in.").format(code=user_code))
            self._hint.show()
        self._again.show()
        self._open_page()

    def _on_confirmed(self) -> None:
        if self._task is None:
            return
        self._step.setText(self.tr("Connecting…"))
        self._hint.hide()
        self._again.hide()

    def _on_done(self, ok: bool, reason: str) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        result = task.take() if ok else {}
        if not ok:
            self.finished.emit(False, reason)
            return
        module = accounts_oauth.provider_module(self.provider)
        saved = module.save(result) if module is not None else ""
        result.clear()
        self.finished.emit(bool(saved), "" if saved else "vault")


def _discard(provider: str, task, ok: bool) -> None:

    result = task.take() if ok else {}
    module = accounts_oauth.provider_module(provider)
    if result and module is not None:
        module.discard(result)
    result.clear()


def failure_text(reason: str, label: str) -> str:

    if reason == "cancelled":
        return ""
    if reason == "denied":
        return QCoreApplication.translate("BrowserWait", "The sign-in was declined in the browser.")
    if reason == "expired":
        return QCoreApplication.translate("BrowserWait", "Sign-in didn't finish.")
    if reason == "network":
        return QCoreApplication.translate(
            "BrowserWait", "{label} did not answer. Check your connection.").format(label=label)
    if reason == "keys":
        return QCoreApplication.translate("BrowserWait", "{label} did not create the S3 keys.").format(label=label)
    if reason == "admin":
        from ..core import accounts_drive
        link = accounts_drive.admin_consent_url()
        said = QCoreApplication.translate(
            "BrowserWait",
            "Your organization lets only an admin approve TerraLab AI Agent for {label}.").format(label=label)
        return f"{said} " + (QCoreApplication.translate(
            "BrowserWait", "Send this approval link to your IT admin: {link}").format(link=link) if link else "")
    if reason == "vault":
        return QCoreApplication.translate(
            "BrowserWait", "Not saved. Unlock QGIS's vault with its master password when QGIS asks.")
    return QCoreApplication.translate("BrowserWait", "{label} refused the sign-in.").format(label=label)
