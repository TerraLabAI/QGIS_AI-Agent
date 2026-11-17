# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

import os
import threading
import time

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QCoreApplication, Qt, QTimer
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import accounts, oauth_loopback
from ..core import earthengine_account as account
from . import style as S
from .account_card import _SAVE_QSS, _SAVED_QSS, AccountMark, _privacy_row
from .card_base import msg_kind_colour
from .card_controls import _BTN_QUIET_LINK
from .confirm_dialog import SIDE_PAD
from .external_links import open_external_url
from .font_scale import scale_px_length, scale_qss_font_px
from .icons import icon_for
from .settings_pages import GHOST_BTN_QSS
from .shared import available_screen_rect
from .style import INK, INK_2
from .styles import PANEL_TEXT

_SIGN_IN_WAIT_S = 300
_CONSOLE_URL = "https://console.cloud.google.com/"
_POLL_MS = 150


def tr(text: str) -> str:
    return QCoreApplication.translate("EarthEngineConnect", text)


def open_browser(url: str, parent=None) -> bool:
    return open_external_url(url, parent)


def card_texts(kind: str, reason: str, project: str = "") -> tuple[str, str, str]:


    terms = tr("A non-commercial Earth Engine project is not for commercial work.")
    if kind == "retry" and reason == "project":
        return tr("Project refused"), _refusal_text(project), tr("Change project")
    if kind == "retry":
        return tr("Sign-in expired"), tr("Your Earth Engine sign-in expired."), tr("Sign in again")
    if reason == "project":
        return (tr("Choose your Earth Engine project"),
                f"{tr('Earth Engine runs on one of your Google Cloud projects.')} {terms}", tr("Choose project"))
    return (tr("Connect Earth Engine"),
            f"{tr('Sign in with Google and pick your own Cloud project.')} {terms}", tr("Connect"))


def _refusal_text(project: str) -> str:
    cause = account.refusal()[0]
    if cause == "register":
        return tr("Your project {project} is not registered for Earth Engine.").format(project=project)
    if cause == "api":
        return tr("The Earth Engine API is off in your project {project}.").format(project=project)
    return tr("Earth Engine refused your project {project}.").format(project=project)


class _Job:


    def __init__(self, work, *args):
        self.answer = None
        self._thread = threading.Thread(target=self._run, args=(work, *args), name="earthengine-connect", daemon=True)
        self._thread.start()

    def _run(self, work, *args):
        try:
            self.answer = work(*args)
        except Exception as exc:  # noqa: BLE001
            self.answer = {"error": type(exc).__name__}


class ConnectDialog(QDialog):



    method = "oauth"

    def __init__(self, parent=None, sign_in: bool = False):
        super().__init__(parent)
        S.with_platform_font(self)
        self.setObjectName("earthEngineConnect")
        self.setWindowTitle(tr("Connect Earth Engine"))
        self.setModal(True)
        self.saved = False
        self._step = ""
        self._loop = None
        self._verifier = ""
        self._challenge = ""
        self._job: _Job | None = None
        self._job_kind = ""
        self._install_id = ""
        self._chosen = ""
        self._started = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._poll)
        available = available_screen_rect(self)
        cap_w = (available.width() - 48) if available is not None else scale_px_length(420)
        self.setFixedWidth(max(300, min(scale_px_length(420), cap_w)))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SIDE_PAD, scale_px_length(24), SIDE_PAD, scale_px_length(18))
        layout.setSpacing(0)
        layout.addWidget(AccountMark(self, account.KEY, "", 44, "globe"), 0, Qt.AlignmentFlag.AlignLeft)
        layout.addSpacing(scale_px_length(14))
        self._heading = self._label("earthEngineTitle", f"font-size: 17px; font-weight: 600; color: {INK};")
        layout.addWidget(self._heading)
        layout.addSpacing(scale_px_length(4))
        self._text = self._label("earthEngineText", f"font-size: 12px; color: {INK_2};")
        layout.addWidget(self._text)
        layout.addSpacing(scale_px_length(12))
        self._project_box = QWidget(self)
        box = QVBoxLayout(self._project_box)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(scale_px_length(6))
        name = self._label("earthEngineProjectLabel", f"font-size: 12px; font-weight: 600; color: {INK};")
        name.setText(tr("Cloud project ID"))
        box.addWidget(name)
        self._project = QLineEdit(self._project_box)
        self._project.setObjectName("earthEngineProjectId")
        self._project.setPlaceholderText("my-project-123")
        self._project.setAccessibleName(tr("Cloud project ID"))
        self._project.setStyleSheet(scale_qss_font_px(
            f"QLineEdit {{ border: 1px solid {S.LINE}; border-radius: 8px; padding: 7px 10px; font-size: 13px;"
            f" background: transparent; color: {PANEL_TEXT}; }} QLineEdit:focus {{ border-color: {S.ACCENT}; }}"))
        self._project.textChanged.connect(self._sync)
        self._project.returnPressed.connect(self._primary_clicked)
        box.addWidget(self._project)
        hint = self._label("earthEngineProjectHint", f"font-size: 11px; color: {INK_2};")
        hint.setText(tr("Shown next to the project name at the top of console.cloud.google.com."))
        box.addWidget(hint)
        layout.addWidget(self._project_box)
        layout.addSpacing(scale_px_length(6))
        self._link_url = ""
        self._link = QPushButton(self)
        self._link.setObjectName("earthEngineLink")
        self._link.setStyleSheet(_BTN_QUIET_LINK + "QPushButton { padding-left: 0px; }")
        self._link.setCursor(Qt.CursorShape.PointingHandCursor)
        self._link.setIcon(icon_for(self._link, "external", scale_px_length(12), QColor(INK_2)))
        self._link.clicked.connect(self._link_clicked)
        layout.addWidget(self._link, 0, Qt.AlignmentFlag.AlignLeft)
        self._error = self._label("earthEngineError", f"font-size: 12px; color: {msg_kind_colour('error')};")
        layout.addWidget(self._error)
        layout.addSpacing(scale_px_length(14))
        layout.addWidget(_privacy_row(self, tr(
            "Your Google sign-in stays in QGIS's encrypted vault on this computer. "
            "Never sent to TerraLab or the AI agent.")))
        layout.addSpacing(scale_px_length(20))
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(scale_px_length(8))
        footer.addStretch(1)
        cancel = QPushButton(tr("Cancel"), self)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setObjectName("earthEngineCancel")
        cancel.setStyleSheet(GHOST_BTN_QSS)
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self._primary = QPushButton(self)
        self._primary.setCursor(Qt.CursorShape.PointingHandCursor)
        self._primary.setObjectName("earthEnginePrimary")
        self._primary.setStyleSheet(_SAVE_QSS)
        self._primary.setDefault(True)
        self._primary.clicked.connect(self._primary_clicked)
        footer.addWidget(self._primary)
        layout.addLayout(footer)
        if not account.ee_installed():
            self._show_install()
        elif account.signed_in() and not sign_in:
            self._show_project()
        else:
            self._show_sign_in()

    def _label(self, name: str, qss: str) -> QLabel:
        label = QLabel(self)
        label.setObjectName(name)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setStyleSheet(scale_qss_font_px(f"{qss} background: transparent;"))
        return label



    def _show(self, step: str, heading: str, text: str, button: str, link: str = "") -> None:
        self._step = step
        self._heading.setText(heading)
        self._text.setText(text)
        self._primary.setText(button)
        self._primary.setVisible(bool(button))
        self._project_box.setVisible(step == "project")
        self._link.setText(link)
        self._link.setVisible(bool(link))
        self._error.hide()
        self._sync()
        self._fit()

    def _fit(self) -> None:


        self.layout().activate()
        height = max(self.height(), self.sizeHint().height())
        available = available_screen_rect(self)
        if available is not None:
            height = min(height, available.height() - 48)
        self.resize(self.width(), height)

    def _fail(self, text: str) -> None:
        self._error.setText(text)
        self._error.show()
        self._primary.setEnabled(True)
        self._fit()

    def _show_install(self) -> None:
        self._show("install", tr("Connect Earth Engine"),
                   tr("Earth Engine needs Google's earthengine-api package. It installs into QGIS in about "
                      "a minute, then you sign in with Google."), tr("Install"))

    def _show_sign_in(self) -> None:
        text = tr("Your browser opens Google's sign-in. Earth Engine then runs on your own Google Cloud "
                  "project, under that project's terms.")
        if not accounts.vault_has_master():
            text += "\n\n" + tr("QGIS will ask you to choose a master password. It locks every sign-in saved "
                                 "in QGIS and cannot be recovered, so keep it somewhere safe.")
        self._show("sign_in", tr("Sign in with Google"), text, tr("Sign in with Google"))

    def _sign_in_failed(self, text: str) -> None:
        self._show_sign_in()
        self._fail(text)
        self._primary.setText(tr("Try again"))

    def _show_project(self) -> None:
        cause = account.refusal()[0]
        text = tr("Type the ID of the Google Cloud project Earth Engine runs on.")
        if cause:
            text = f"{_refusal_text(account.project())} {text}"
        terms = tr("A non-commercial Earth Engine project is not for commercial work.")
        self._show("project", tr("Choose your Earth Engine project"), f"{text} {terms}", tr("Use this project"),
                   tr("Open the Cloud console"))
        self._link_url = _CONSOLE_URL
        if not self._project.text():
            self._project.setText(account.project())
        self._project.setFocus()

    def _sync(self) -> None:
        if self._step == "project":
            self._primary.setEnabled(bool(self._project.text().strip()) and not self._timer.isActive())
        elif not self._timer.isActive():
            self._primary.setEnabled(True)



    def _primary_clicked(self) -> None:
        if self._timer.isActive():
            return
        if self._step == "install":
            self._start_install()
        elif self._step == "sign_in":
            self._start_sign_in()
        elif self._step == "project":
            self._start_check()

    def _link_clicked(self) -> None:
        if self._step == "project":
            open_browser(self._link_url or _CONSOLE_URL, self)
        elif self._step == "sign_in" and self._loop is not None:
            open_browser(account.authorization(self._loop, self._challenge), self)

    def _start_install(self) -> None:
        from ..tools import deps_tools
        started = deps_tools._install({"feature": "earthengine"})
        if started.get("already_installed"):
            self._show_sign_in()
            return
        if started.get("_error"):
            self._fail(tr("earthengine-api did not install: {error}").format(error=started["_error"]))
            return
        self._install_id = str(started.get("task_id") or "")
        self._primary.setEnabled(False)
        self._text.setText(tr("Installing earthengine-api..."))
        self._begin("install")

    def _start_sign_in(self) -> None:
        self._close_loop()
        self._verifier, self._challenge = oauth_loopback.pkce_pair()
        self._loop = oauth_loopback.Loopback(
            (tr("You're signed in"), tr("You can close this tab and return to QGIS."),
             tr("In QGIS, pick the Cloud project Earth Engine runs on.")),
            (tr("Sign-in didn't finish"), tr("Close this tab and try again from QGIS.")),
            os.path.join(QgsApplication.iconsPath(), "qgis-icon-512x512.png"))
        open_browser(account.authorization(self._loop, self._challenge), self)
        self._text.setText(tr("Finish signing in to Google in your browser."))
        self._link.setText(tr("Open the sign-in page again"))
        self._link.show()
        self._primary.setEnabled(False)
        self._begin("browser")

    def _start_check(self) -> None:

        chosen = self._project.text().strip()
        if not chosen:
            return
        token = accounts.refresh_token(account.KEY, acting=True)
        if not token:
            self._show_sign_in()
            return
        self._chosen = chosen
        self._text.setText(tr("Checking {project} with Earth Engine...").format(project=chosen))
        self._primary.setEnabled(False)
        self._job = _Job(_check_with, token, chosen)
        self._begin("check")

    def _begin(self, kind: str) -> None:
        self._job_kind = kind
        self._started = time.monotonic()
        self._error.hide()
        self._timer.start()

    def _poll(self) -> None:
        kind = self._job_kind
        if kind == "install":
            from ..tools import deps_tools
            state = deps_tools._install_status({"task_id": self._install_id})
            if state.get("status") == "running":
                return
            self._timer.stop()
            if state.get("status") == "complete" and account.ee_installed():
                self._show_sign_in()
            else:
                self._fail(tr("earthengine-api did not install: {error}").format(
                    error=str(state.get("error") or state.get("status") or "")[:300]))
            return
        if kind == "browser":
            if not self._loop.done():
                if time.monotonic() - self._started > _SIGN_IN_WAIT_S:
                    self._timer.stop()
                    self._close_loop()
                    self._sign_in_failed(tr("Sign-in didn't finish within 5 minutes."))
                return
            code, error = self._loop.result()
            redirect = self._loop.redirect_uri
            self._close_loop()
            if error or not code:
                self._timer.stop()
                self._sign_in_failed(tr("Sign-in didn't finish ({error}).").format(error=error or "no code"))
                return
            self._job = _Job(account.exchange, code, self._verifier, redirect)
            self._verifier = ""
            self._job_kind = "exchange"
            return
        if self._job is None or self._job.answer is None:
            return
        answer, self._job = self._job.answer, None
        self._timer.stop()
        if kind == "exchange":
            if answer.get("error"):
                self._sign_in_failed(tr("Google refused the sign-in ({error}).").format(error=answer["error"]))
                return
            if not account.save_sign_in(answer.pop("refresh_token")):
                self._sign_in_failed(tr("Not saved: QGIS's vault stayed locked. Connect again and set the master "
                                        "password when QGIS asks."))
                return
            self._show_project()
        elif kind == "check":
            chosen = self._chosen
            if answer.get("error") in ("invalid_grant", "unauthorized_client"):
                self._sign_in_failed(tr("Google refused your saved sign-in."))
                return
            if not answer:
                account.set_project(chosen)
                account.forget_session()
                self._succeed()
                return
            self._show_project()
            cause = answer.get("cause")
            if cause == "register":
                text = tr("Your project {project} is not registered for Earth Engine.").format(project=chosen)
                self._link.setText(tr("Register a project"))
            elif cause == "api":
                text = tr("The Earth Engine API is off in your project {project}.").format(project=chosen)
                self._link.setText(tr("Turn on the Earth Engine API"))
            elif cause == "network":
                text = tr("Earth Engine could not be reached ({error}). Check your connection or the proxy "
                          "in QGIS (Settings > Options > Network).").format(error=answer.get("error") or "")
            else:
                text = tr("Earth Engine refused the project {project} ({error}). Check the ID and your role "
                          "on the project.").format(project=chosen, error=answer.get("error") or "")
            self._link_url = answer.get("url") or _CONSOLE_URL
            self._fail(text)

    def _succeed(self) -> None:
        self.saved = True
        self._primary.setEnabled(False)
        self._primary.setStyleSheet(_SAVED_QSS)
        self._primary.setText(tr("Connected"))
        self._primary.setIcon(icon_for(self._primary, "check", scale_px_length(14), QColor("#ffffff")))
        QTimer.singleShot(450, self.accept)

    def _close_loop(self) -> None:
        if self._loop is not None:
            self._loop.close()
            self._loop = None

    def done(self, result) -> None:  # noqa: D401
        self._timer.stop()
        self._close_loop()
        self._verifier = ""
        super().done(result)


def _check_with(refresh_token: str, project_id: str) -> dict:
    tokens = account.access_token(refresh_token)
    if tokens.get("error"):
        return tokens
    return account.check_project(str(tokens.get("access_token") or ""), project_id)
