# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later













from __future__ import annotations

import base64
import time

from qgis.PyQt.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QColor, QIcon
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import accounts, accounts_oauth, quiet_credentials, telemetry
from ..core import telemetry_events as ev
from . import style as S
from .account_browser import BrowserWait, failure_text
from .card_base import _Card, msg_kind_colour, on_screen
from .card_controls import (
    _ASK_CARD_QSS,
    _ASK_DONE_LINE_QSS,
    _ASK_MARGINS,
    _BTN_GHOST_PILL,
    _BTN_PRIMARY_PILL,
    _BTN_QUIET_LINK,
    AnswerFooter,
    FoldMixin,
    _pill,
)
from .confirm_dialog import SIDE_PAD
from .external_links import open_external_url
from .font_scale import scale_px_length, scale_qss_font_px, widget_pixel_ratio
from .icons import icon_for, pixmap_for
from .settings_pages import GHOST_BTN_QSS, INPUT_QSS, PRIMARY_BTN_QSS
from .shared import (
    AGENT_CARD_KIND,
    AGENT_REQUEST_ID,
    available_screen_rect,
    exec_dialog,
    popup_menu,
    resolve_qt_enum,
)
from .style import _BTN_PRIMARY, _MENU_QSS, FONT_BASE, INK, INK_2, INK_3, MOTION_POP_MS, SPACE_CARD
from .styles import PANEL_BASE, PANEL_TEXT
from .widgets import ChatLabel, placeholder_ink

_SPLIT_LEFT = (_BTN_PRIMARY.replace("padding: 0 12px", "padding: 0 12px 0 14px")
               + "QPushButton { border-top-right-radius: 0px; border-bottom-right-radius: 0px; }")
_SPLIT_RIGHT = (_BTN_PRIMARY.replace("padding: 0 12px", "padding: 0px")
                + "QPushButton { border-top-left-radius: 0px; border-bottom-left-radius: 0px; }")
_TITLE_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BASE}px; font-weight: 600; color: {INK}; background: transparent; border: none; }}")
_SUB_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: 12px; color: {INK_2}; background: transparent; border: none; }}")

_SAVE_QSS = PRIMARY_BTN_QSS + f"QPushButton:disabled {{ background: {S.HOVER}; color: {INK_3}; }}"
_SAVED_QSS = PRIMARY_BTN_QSS.replace("background: " + PANEL_TEXT, f"background: {S.GREEN}").replace(
    "color: " + PANEL_BASE, "color: #ffffff") + f"QPushButton:disabled {{ background: {S.GREEN}; color: #ffffff; }}"
_PRIVACY_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: 11px; color: {INK_3}; background: transparent; border: none; }}")
_STATUS_ATTR = resolve_qt_enum(QNetworkRequest, "Attribute", "HttpStatusCodeAttribute")
_REDIRECT_ATTR = resolve_qt_enum(QNetworkRequest, "Attribute", "RedirectPolicyAttribute")
_MANUAL_REDIRECT = resolve_qt_enum(QNetworkRequest, "RedirectPolicy", "ManualRedirectPolicy")

_CHECK_MS = 8000


def provider_tag(provider: str, connector: str = "", store: str = "") -> str:


    if provider in accounts.PROVIDERS:
        return provider
    if store:
        return store
    return "esri" if connector == "esri" else "server"


def _connector_id(provider: str, connector: str = "") -> str:
    return str(connector or (accounts.PROVIDERS.get(provider) or {}).get("connector") or "")


def _connector_row(cid: str, host: str) -> dict:
    from .source_marks import connector_row
    try:
        return connector_row(cid, host)
    except Exception:  # noqa: BLE001
        return {}


class AccountMark(QLabel):



    def __init__(self, parent: QWidget, cid: str, host: str, side: int, glyph: str = "server"):
        super().__init__(parent)
        self._side, self._glyph = scale_px_length(side), glyph
        self._row = _connector_row(cid, host)
        self._url = str(self._row.get("logo_url") or "")
        self.setFixedSize(self._side, self._side)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if self._url:
            from .library.pictures import store
            store().ready.connect(self._on_ready)
        self._paint()

    def _paint(self) -> None:
        radius = max(6, int(self._side * 0.28))
        if self._url:
            from .source_marks import family_pixmap
            self.setStyleSheet("QLabel { background: transparent; border: none; }")
            self.setPixmap(family_pixmap(self._row, self._side, widget_pixel_ratio(self), round_=False))
            return
        self.setStyleSheet(f"QLabel {{ background: {S.HOVER}; border: none; border-radius: {radius}px; }}")
        self.setPixmap(pixmap_for(self, self._glyph, int(self._side * 0.5), QColor(INK_2)))

    def _on_ready(self, url: str) -> None:
        if url == self._url:
            self._paint()


def _privacy_row(parent: QWidget, text: str, glyph: str = "lock") -> QWidget:

    row = QWidget(parent)
    line = QHBoxLayout(row)
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(6)
    lock = QLabel(row)
    lock.setPixmap(pixmap_for(row, glyph, scale_px_length(13), QColor(INK_3)))
    lock.setFixedWidth(scale_px_length(14))

    lock.setContentsMargins(0, scale_px_length(2), 0, 0)
    line.addWidget(lock, 0, Qt.AlignmentFlag.AlignTop)
    label = ChatLabel(text, row, wrap=True, selectable=False)
    label.setObjectName("accountPrivacy")
    label.setStyleSheet(_PRIVACY_QSS)
    line.addWidget(label, 1)
    return row


def _uses_keys(provider: str) -> bool:
    return (accounts.PROVIDERS.get(provider) or {}).get("keys") == accounts.S3


class AccountCard(_Card, FoldMixin):
    answered = pyqtSignal(str, str)

    def __init__(self, tool_call_id: str, need: dict, parent=None):
        super().__init__(None, parent, frame_qss=_ASK_CARD_QSS)
        self.set_margins(*_ASK_MARGINS)
        self._col.setSpacing(SPACE_CARD)
        self.tool_call_id = tool_call_id
        self.need = dict(need or {})
        self.kind = str(self.need.get("kind") or "sign_in")
        self.host = str(self.need.get("host") or self.need.get("key") or "")
        self.provider = str(self.need.get("provider") or "")
        self.label = str(self.need.get("label") or self.host)
        self.answer: str | None = None
        self._wait: BrowserWait | None = None
        self.setToolTip(self.tr("Needs your answer"))
        self._body = self._build_body()
        self._col.addWidget(self._body)
        self._done = self._build_done()
        self._done.hide()
        self._col.addWidget(self._done)
        self._tag = provider_tag(self.provider, str(self.need.get("connector") or ""),
                                 str(self.need.get("store") or ""))
        self._shown_at = time.monotonic()
        telemetry.track(ev.ACCOUNT_CARD_SHOWN, {"kind": self.kind, "provider": self._tag})

    def _texts(self) -> tuple[str, str]:

        host, label = self.host, self.label
        if self.kind == "consent":
            what = str(self.need.get("what") or "").strip()
            if what and what[-1] not in ".!?":
                what += "."
            sent = self.tr("Sent to {host} only.").format(host=host)
            return self.tr("Use your {label} access?").format(label=label), f"{what} {sent}".strip()
        if self.provider == "earthengine":
            return self._earthengine()[:2]
        if (accounts.PROVIDERS.get(self.provider) or {}).get("own"):
            from .account_drive import card_texts
            return card_texts(self.kind, self.provider, self.need)
        if self.need.get("store"):
            from .account_cloud import card_texts
            return card_texts(self.need)
        keys = _uses_keys(self.provider)
        token = bool(self.need.get("token_header"))
        browser = accounts_oauth.browser_ready(self.provider)
        if self.kind == "provider" and browser:
            return (self.tr("Continue with your {label} account").format(label=label)
                    if self.need.get("reason") == "shared_spent" else
                    self.tr("Connect your {label} account").format(label=label),
                    self.tr("TerraLab's shared access is used up. Your free account keeps you going.")
                    if self.need.get("reason") == "shared_spent" else
                    self.tr("{label} answers only a signed-in user.").format(label=label))
        if self.kind == "provider":
            if self.need.get("reason") != "shared_spent":
                return (self.tr("Connect your {label} account").format(label=label),
                        self.tr("{label} answers only a signed-in user.").format(label=label))
            return (self.tr("Continue with your {label} account").format(label=label),
                    self.tr("TerraLab's shared access is used up. Free S3 keys keep you going.") if keys
                    else self.tr("TerraLab's shared access is used up. A free token keeps you going."))
        if self.kind == "retry" and browser and (accounts.entries().get(self.provider) or {}).get("via") == "browser":
            name = accounts_oauth.browser_name(self.provider, label)
            said = (self.tr("Your {name} sign-in expired.") if self.need.get("expired")
                    else self.tr("{name} no longer accepts your saved sign-in."))
            return self.tr("Sign in again"), said.format(name=name)
        if self.kind == "retry" and self.need.get("expired"):
            if keys:
                return self.tr("Keys expired"), self.tr("{label} no longer accepts your saved S3 keys.").format(
                    label=label)
            if self.provider or token:
                return self.tr("Token expired"), self.tr("{label} no longer accepts your saved token.").format(
                    label=label)
            return self.tr("Sign-in expired"), self.tr("{host} no longer accepts your saved access.").format(host=host)
        if self.kind == "retry":
            if keys:
                return self.tr("Keys refused"), self.tr("{label} refused your saved S3 keys.").format(label=label)
            if self.provider or token:
                return self.tr("Token refused"), self.tr("{label} refused your saved token.").format(label=label)
            user = str(self.need.get("username") or "").strip()
            return (self.tr("Sign-in refused"),
                    self.tr("{host} refused your saved access ({user}).").format(host=host, user=user) if user
                    else self.tr("{host} refused your saved access.").format(host=host))
        if token:
            service = str(self.need.get("service") or "").strip() or host
            return (self.tr("{service} needs a token").format(service=service),
                    self.tr("{host} opens with a token or an API key from your account.").format(host=host))
        return self.tr("Sign in to {host}").format(host=host), self.tr("This service answers only a signed-in user.")

    def _earthengine(self) -> tuple[str, str, str]:
        from .earthengine_connect import card_texts
        return card_texts(self.kind, str(self.need.get("reason") or ""), str(self.need.get("project") or ""))

    def _build_body(self) -> QWidget:
        body_widget = QWidget(self)
        body = QVBoxLayout(body_widget)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(scale_px_length(12))
        title, sub = self._texts()
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(scale_px_length(12))
        glyph = "lock" if self.kind == "consent" else "server"
        cid = _connector_id(self.provider, str(self.need.get("connector") or ""))
        head.addWidget(AccountMark(body_widget, cid, self.host, 32, glyph), 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(2)
        heading = ChatLabel(title, body_widget, wrap=True, selectable=True)
        heading.setObjectName("askText")
        heading.setStyleSheet(_TITLE_QSS)
        texts.addWidget(heading)
        if sub:
            line = ChatLabel(sub, body_widget, wrap=True, selectable=True)
            line.setObjectName("accountReason")
            line.setStyleSheet(_SUB_QSS)
            texts.addWidget(line)
        head.addLayout(texts, 1)
        body.addLayout(head)
        facts = self.need.get("details") if isinstance(self.need.get("details"), dict) else {}
        details = [said.format(value=str(facts[name])[:300]) for name, said in (
            ("file", self.tr("File: {value}")), ("sent_to", self.tr("Sent to: {value}")),
            ("access", self.tr("Saved access: {value}"))) if facts.get(name)]
        if facts.get("read_only"):
            details.append(self.tr("Only this file is read with it; nothing is written."))
        if self.kind == "consent" and details:
            body.addWidget(self._details(body_widget, details))
        footer = AnswerFooter(body_widget)
        if self.kind == "consent":
            no = _pill(self._button(self.tr("Deny"), _BTN_GHOST_PILL, lambda: self._answer("")))
            no.setObjectName("accountDeny")
            footer.set_widgets(*self._allow_widgets(body_widget, no))
            yes = self._allow_yes
        else:
            body.addWidget(_privacy_row(body_widget, self.tr(
                "Stored encrypted in QGIS on this computer. Never sent to TerraLab or the AI agent.")))
            no = _pill(self._button(self.tr("Skip"), _BTN_GHOST_PILL, lambda: self._answer("")))
            no.setObjectName("accountSkip")
            retry = self.kind == "retry"
            browser = accounts_oauth.browser_ready(self.provider)
            if self.provider == "earthengine":
                text = self._earthengine()[2]
            elif browser:
                link = None
                if _uses_keys(self.provider):
                    link = self._button(self.tr("Use S3 keys instead"), _BTN_QUIET_LINK, self._sign_in_keys)
                    link.setObjectName("accountUseKeys")
                name = accounts_oauth.browser_name(self.provider, self.label)
                said = (self.tr("Choose in Google Drive") if self.kind == "pick"
                        else self.tr("Sign in with {name}").format(name=name))
                yes = _pill(self._button(said, _BTN_PRIMARY_PILL, self._browser_sign_in))
                yes.setObjectName("accountBrowser")
                footer.set_widgets(link, [no, yes])
            elif _uses_keys(self.provider) or self.need.get("store"):
                text = self.tr("Replace keys") if retry else self.tr("Add keys")
            elif self.provider or self.need.get("token_header"):
                text = self.tr("Replace token") if retry else self.tr("Add token")
            else:
                text = self.tr("Sign in again") if retry else self.tr("Sign in")
            if not browser:
                yes = _pill(self._button(text, _BTN_PRIMARY_PILL, self._sign_in))
                yes.setObjectName("accountSignIn")
                footer.set_widgets(None, [no, yes])
        more = getattr(self, "_allow_more", None) if self.kind == "consent" else None
        for button in (no, yes, *([more] if more is not None else [])):
            button.setProperty(AGENT_REQUEST_ID, self.tool_call_id)
            button.setProperty(AGENT_CARD_KIND, "account")
        self._error = ChatLabel("", body_widget, wrap=True, selectable=True)
        self._error.setObjectName("accountBrowserError")
        self._error.setStyleSheet(_SUB_QSS.replace(INK_2, msg_kind_colour("error")))
        self._error.hide()
        body.addWidget(self._error)
        self._footer, self._yes = footer, yes
        body.addWidget(footer)
        return body_widget

    def _allow_widgets(self, parent: QWidget, no: QPushButton):


        yes = _pill(self._button(self.tr("Allow"), _SPLIT_LEFT, lambda: self._answer(accounts.ANSWER + "once")))
        yes.setObjectName("accountAllow")
        more = _pill(self._button("", _SPLIT_RIGHT, lambda: self._allow_menu(more)))
        more.setObjectName("accountAllowMore")
        more.setIcon(icon_for(more, "chevron_down", scale_px_length(12), QColor(S.ON_ACCENT)))
        more.setFixedWidth(scale_px_length(26))
        more.setToolTip(self.tr("More ways to allow"))
        more.setAccessibleName(self.tr("More ways to allow"))
        pair = QWidget(parent)
        line = QHBoxLayout(pair)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(1)
        line.addWidget(yes)
        line.addWidget(more)
        self._allow_yes, self._allow_more = yes, more
        return None, [no, pair]

    def _allow_menu(self, anchor: QWidget) -> None:
        menu = QMenu(anchor)
        menu.setStyleSheet(scale_qss_font_px(_MENU_QSS))
        thread = menu.addAction(self.tr("Allow for this conversation"))
        thread.setObjectName("accountAllowThread")
        thread.triggered.connect(self._allow_thread)
        always = menu.addAction(self.tr("Always allow {label}").format(label=self.label))
        always.setObjectName("accountAllowAlways")
        always.triggered.connect(self._allow_always)
        popup_menu(menu, anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _allow_thread(self) -> None:
        self._answer(accounts.ANSWER + "thread")

    def _allow_always(self) -> None:
        self._answer(accounts.ANSWER + "always")

    def _details(self, parent: QWidget, lines: list) -> QWidget:

        box = QWidget(parent)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(scale_px_length(4))
        toggle = QPushButton(self.tr("Details"), box)
        toggle.setObjectName("accountDetails")
        toggle.setStyleSheet(_BTN_QUIET_LINK + "QPushButton { padding-left: 0px; }")
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle.setIcon(icon_for(toggle, "chevron_right", scale_px_length(12), QColor(INK_2)))
        text = ChatLabel("\n".join(lines), box, wrap=True, selectable=True)
        text.setObjectName("accountDetailsText")
        text.setStyleSheet(_SUB_QSS)
        text.hide()

        def flip() -> None:
            text.setVisible(text.isHidden())
            toggle.setIcon(icon_for(toggle, "chevron_down" if text.isVisible() else "chevron_right",
                                    scale_px_length(12), QColor(INK_2)))
        toggle.clicked.connect(flip)
        lay.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(text)
        return box

    def _build_done(self) -> QWidget:
        row = QWidget(self)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(scale_px_length(8))
        self._done_icon = QLabel(row)
        self._done_icon.setFixedSize(scale_px_length(14), scale_px_length(14))
        lay.addWidget(self._done_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self._done_text = ChatLabel("", row, wrap=True, selectable=True)
        self._done_text.setObjectName("accountDecision")
        self._done_text.setStyleSheet(_ASK_DONE_LINE_QSS)
        lay.addWidget(self._done_text, 1)
        return row

    def _sign_in_keys(self) -> None:
        self._sign_in(keys=True)

    def _browser_sign_in(self) -> None:


        if self.answer is not None or self._wait is not None:
            return
        self._error.hide()
        self._footer.hide()
        self._wait = BrowserWait(self.provider, accounts_oauth.browser_name(self.provider, self.label), self._body,
                                 self.need)
        self._wait.finished.connect(self._on_browser_done)
        self._body.layout().addWidget(self._wait)
        self._wait.start()

    def _on_browser_done(self, ok: bool, reason: str) -> None:
        wait, self._wait = self._wait, None
        if wait is not None:
            wait.hide()
            wait.deleteLater()
        if self.answer is not None:
            return
        if ok:
            self._answer(accounts.ANSWER + "signed_in")
            return
        said = failure_text(reason, self.label)
        self._error.setText(said)
        self._error.setVisible(bool(said))
        if said:
            self._yes.setText(self.tr("Try again"))
        self._footer.show()

    def _sign_in(self, keys: bool = False) -> None:
        if self.answer is not None:
            return
        header = str(self.need.get("token_header") or "")
        origin = str(self.need.get("key") or "")
        url = str(self.need.get("url") or "")
        if self.provider == "earthengine":
            from .earthengine_connect import ConnectDialog
            dialog = ConnectDialog(self.window(), sign_in=self.kind == "retry" and self.need.get("reason") != "project")
        elif self.need.get("store"):
            from .account_cloud import CloudKeysDialog
            dialog = CloudKeysDialog(self.need, self.window())
        elif keys or _uses_keys(self.provider):
            dialog = KeysDialog(self.provider, parent=self.window())
        elif self.provider in accounts.PROVIDERS:
            dialog = TokenDialog(self.provider, parent=self.window(), check_url=url)
        elif header:
            dialog = TokenDialog("", self.host, header, str(self.need.get("service") or ""), self.window(),
                                 connector=str(self.need.get("connector") or ""), origin=origin, check_url=url)
        else:
            dialog = SignInDialog(self.host, self.window(), origin=origin, check_url=url)
        try:
            if exec_dialog(dialog) == QDialog.DialogCode.Accepted and dialog.saved:
                telemetry.track(ev.ACCOUNT_CONNECTED, {"provider": self._tag, "method": dialog.method})
                self._answer(accounts.ANSWER + "signed_in")
        finally:
            dialog.deleteLater()

    def _answer(self, choice: str) -> None:
        if self.answer is None:
            said = choice[len(accounts.ANSWER):] if choice.startswith(accounts.ANSWER) else ""
            telemetry.track(ev.ACCOUNT_CARD_ANSWERED, {
                "kind": self.kind, "provider": self._tag,
                "decision": said or ("denied" if self.kind == "consent" else "skipped"),
                "waited_ms": int((time.monotonic() - self._shown_at) * 1000)})
            self.answered.emit(self.tool_call_id, choice)
            if self.answer is None:
                self.collapse(choice)

    def is_open(self) -> bool:
        return self.answer is None

    def open_page_id(self) -> str:
        return self.tool_call_id if self.answer is None else ""

    def collapse(self, answer: str = "", _tool_call_id: str = "") -> None:

        self.answer = answer or ""
        choice = self.answer[len(accounts.ANSWER):] if self.answer.startswith(accounts.ANSWER) else None
        said = {"signed_in": self.tr("Connected"), "once": self.tr("Allowed once"),
                "thread": self.tr("Allowed for this conversation"),
                "always": self.tr("Always allowed")}.get(choice or "")
        if said:
            glyph, colour = "check", QColor(S.GREEN)
        else:
            said = self.answer or (self.tr("Not allowed") if self.kind == "consent" else self.tr("Skipped"))
            glyph, colour = "dash", QColor(INK_3)
        self._done_icon.setPixmap(pixmap_for(self, glyph, scale_px_length(12), colour))
        self._done_text.setText(f"{said} · {self.label}")
        self.fold_body(self._body, self._done.show)

    def cleanup(self) -> None:
        if self._wait is not None:
            self._wait.cancel()
        self.stop_fold()

    def to_markdown(self) -> str:
        return f"> {self._done_text.text()}" if self.answer is not None else ""


class SignInDialog(QDialog):





    def __init__(self, host: str, parent=None, cid: str = "", origin: str = "", check_url: str = ""):
        super().__init__(parent)
        S.with_platform_font(self)
        self.setObjectName("accountSignInDialog")
        self.setWindowTitle(self.tr("Sign in"))
        self.setModal(True)
        self.host = host

        self.origin = origin if "://" in origin else f"https://{host}"
        self.saved = False
        self._key_mode = False

        self.check_url = check_url
        self._reply = None
        available = available_screen_rect(self)
        cap_w = (available.width() - 48) if available is not None else scale_px_length(400)
        self.setFixedWidth(max(300, min(scale_px_length(400), cap_w)))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SIDE_PAD, scale_px_length(24), SIDE_PAD, scale_px_length(18))
        layout.setSpacing(0)
        layout.addWidget(AccountMark(self, cid, host, 44), 0, Qt.AlignmentFlag.AlignLeft)
        layout.addSpacing(scale_px_length(14))
        self._heading = QLabel(self.tr("Sign in to {host}").format(host=host), self)
        self._heading.setObjectName("accountSignInTitle")
        self._heading.setTextFormat(Qt.TextFormat.PlainText)
        self._heading.setWordWrap(True)
        self._heading.setStyleSheet(scale_qss_font_px(
            f"font-size: 17px; font-weight: 600; color: {INK}; background: transparent;"))
        layout.addWidget(self._heading)
        layout.addSpacing(scale_px_length(4))
        self._where = QLabel(self)
        self._where.setObjectName("accountTokenWhere")
        self._where.setWordWrap(True)
        self._where.setTextFormat(Qt.TextFormat.PlainText)
        self._where.setStyleSheet(scale_qss_font_px(f"font-size: 12px; color: {INK_2}; background: transparent;"))
        self._where.hide()
        layout.addWidget(self._where)
        self._open = QPushButton(self)
        self._open.setObjectName("accountOpenPage")
        self._open.setStyleSheet(_BTN_QUIET_LINK + "QPushButton { padding-left: 0px; }")
        self._open.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open.hide()
        layout.addWidget(self._open, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addSpacing(scale_px_length(14))

        fields = QVBoxLayout()
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setSpacing(scale_px_length(8))
        self._fields = fields
        self._user = self._field(self.tr("Username"), fields)
        self._password = self._field(self.tr("Password"), fields, secret=True)
        self._header = self._field(self.tr("Header name"), fields, text="X-API-Key")
        self._value = self._field(self.tr("Key"), fields, secret=True)
        for edit in (self._user, self._password, self._header, self._value):
            edit.textChanged.connect(self._sync_save)
        layout.addLayout(fields)
        self._toggle = QPushButton(self.tr("Use an API key instead"), self)
        self._toggle.setObjectName("accountSignInToggle")
        self._toggle.setStyleSheet(_BTN_QUIET_LINK)
        self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle.clicked.connect(self._switch)
        layout.addSpacing(scale_px_length(6))
        layout.addWidget(self._toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self._error = QLabel("", self)
        self._error.setObjectName("accountSignInError")
        self._error.setWordWrap(True)
        self._error.setStyleSheet(scale_qss_font_px(
            f"font-size: 12px; color: {msg_kind_colour('error')}; background: transparent;"))
        self._error.hide()
        layout.addWidget(self._error)
        layout.addSpacing(scale_px_length(14))
        layout.addWidget(_privacy_row(self, self.tr(
            "Stored encrypted in QGIS on this computer, behind your QGIS master password. "
            "Never sent to TerraLab or the AI agent.")))
        if not accounts.vault_has_master():
            layout.addSpacing(scale_px_length(6))
            first = _privacy_row(self, self.tr(
                "QGIS will ask you to choose a master password. It locks every sign-in saved in QGIS "
                "and cannot be recovered, so keep it somewhere safe."), "shield")
            first.setObjectName("accountMasterNote")
            layout.addWidget(first)
        layout.addSpacing(scale_px_length(20))

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(scale_px_length(8))
        footer.addStretch(1)
        cancel = QPushButton(self.tr("Cancel"), self)
        cancel.setObjectName("accountSignInCancel")
        cancel.setStyleSheet(GHOST_BTN_QSS)
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self._save = QPushButton(self.tr("Connect"), self)
        self._save.setObjectName("accountSignInSave")
        self._save.setStyleSheet(_SAVE_QSS)
        self._save.setDefault(True)
        self._save.clicked.connect(self._store)

        self._save.setText(self.tr("Checking…"))
        widest = self._save.sizeHint().width()
        self._save.setText(self.tr("Connected"))
        self._save.setIcon(icon_for(self._save, "check", scale_px_length(14), QColor("#ffffff")))
        self._save.setMinimumWidth(max(widest, self._save.sizeHint().width()))
        self._save.setIcon(QIcon())
        self._save.setText(self.tr("Connect"))
        footer.addWidget(self._save)
        layout.addLayout(footer)
        self._show_mode()
        self._user.setFocus()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if getattr(self, "_faded", False) or not on_screen(self):
            return
        self._faded = True
        self.setWindowOpacity(0.0)
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(MOTION_POP_MS)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutQuint)
        fade.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def done(self, result) -> None:  # noqa: D401
        self._stop_check()
        for edit in (self._user, self._password, self._value):
            edit.clear()
        super().done(result)

    def _check_key(self) -> str:

        return accounts.key_for(self.origin)

    def _check(self, headers: dict, save) -> None:




        url = self.check_url
        if not url or self._reply is not None or accounts.key_for(url) != self._check_key():
            save()
            return
        from qgis.core import QgsNetworkAccessManager
        request = QNetworkRequest(QUrl(url))
        request.setAttribute(_REDIRECT_ATTR, _MANUAL_REDIRECT)
        for name, value in headers.items():
            request.setRawHeader(name.encode("latin-1", "replace"), value.encode("utf-8"))
        self._set_checking(True)
        quiet_credentials.hold()
        try:
            reply = QgsNetworkAccessManager.instance().get(request)
        except Exception:  # noqa: BLE001
            quiet_credentials.release()
            self._set_checking(False)
            save()
            return
        self._reply, self._save_after = reply, save
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._checked(None))
        timer.start(_CHECK_MS)
        self._check_timer = timer
        reply.metaDataChanged.connect(lambda: self._checked(reply.attribute(_STATUS_ATTR)))
        reply.finished.connect(lambda: self._checked(reply.attribute(_STATUS_ATTR)))

    def _stop_check(self):

        reply, save = self._reply, getattr(self, "_save_after", None)
        if reply is None:
            return None
        self._reply, self._save_after = None, None
        self._check_timer.stop()
        quiet_credentials.release()
        try:
            reply.abort()
            reply.deleteLater()
        except RuntimeError:
            pass
        return save

    def _checked(self, status) -> None:
        if status is None and self._reply is not None and not self._reply.isFinished() \
                and self._check_timer.isActive():
            return
        save = self._stop_check()
        if save is None:
            return
        self._set_checking(False)
        if isinstance(status, int) and status in (401, 403):
            self._password.clear()
            self._value.clear()
            self._error.setText(self.tr("{host} refused these details. Check them and try again.").format(
                host=self.host))
            self._error.show()
            self.adjustSize()
            return
        save()

    def _set_checking(self, on: bool) -> None:
        for edit in (self._user, self._password, self._header, self._value, self._toggle):
            edit.setEnabled(not on)
        self._save.setEnabled(not on)
        self._save.setText(self.tr("Checking…") if on else self.tr("Connect"))
        if not on:
            self._sync_save()

    def _sync_save(self) -> None:
        shown = [e for e in (self._user, self._password, self._header, self._value) if not e.isHidden()]
        self._save.setEnabled(all(e.text().strip() for e in shown))
        self._error.hide()

    def _set_page(self, url: str, host: str) -> None:

        self._open.setText(self.tr("Open {host}").format(host=host))
        self._open.setIcon(icon_for(self._open, "external", scale_px_length(12), QColor(INK_2)))
        self._open.clicked.connect(lambda: open_external_url(url, self))
        self._open.show()

    def _add_eye(self, edit: QLineEdit) -> None:

        eye = edit.addAction(icon_for(edit, "eye", scale_px_length(14), QColor(INK_3)),
                             QLineEdit.ActionPosition.TrailingPosition)
        eye.setToolTip(self.tr("Show"))

        def flip() -> None:
            hidden = edit.echoMode() == QLineEdit.EchoMode.Password
            edit.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
            eye.setToolTip(self.tr("Hide") if hidden else self.tr("Show"))
        eye.triggered.connect(flip)

    def _succeed(self, typed: list) -> None:


        clipboard = QApplication.clipboard()
        try:
            if clipboard is not None and clipboard.text() and clipboard.text().strip() in typed:
                clipboard.clear()
        except RuntimeError:
            pass
        for edit in (self._user, self._password, self._header, self._value):
            edit.clear()
            edit.setEnabled(False)
        self.saved = True
        self._save.setEnabled(False)
        self._save.setStyleSheet(_SAVED_QSS)
        self._save.setText(self.tr("Connected"))
        self._save.setIcon(icon_for(self._save, "check", scale_px_length(14), QColor("#ffffff")))


        if on_screen(self):
            QTimer.singleShot(450, self.accept)
        else:
            self.accept()

    def _field(self, label: str, into: QVBoxLayout, secret: bool = False, text: str = "") -> QLineEdit:
        edit = QLineEdit(text, self)
        edit.setStyleSheet(INPUT_QSS)
        edit.setPlaceholderText(label)
        placeholder_ink(edit)
        edit.setAccessibleName(label)
        if secret:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            self._add_eye(edit)
        into.addWidget(edit)
        return edit

    def _only(self, *shown: QLineEdit) -> None:

        for edit in (self._user, self._password, self._header, self._value):
            edit.setVisible(edit in shown)
        self._toggle.hide()
        self._sync_save()

    def _set_where(self, text: str) -> None:
        self._where.setText(text)
        self._where.show()

    def _show_mode(self) -> None:
        for edit, shown in ((self._user, not self._key_mode), (self._password, not self._key_mode),
                            (self._header, self._key_mode), (self._value, self._key_mode)):
            edit.setVisible(shown)
        self._toggle.setText(self.tr("Use a username and password instead") if self._key_mode
                             else self.tr("Use an API key instead"))
        self._sync_save()
        self.adjustSize()

    def _switch(self) -> None:
        self._key_mode = not self._key_mode
        self._error.hide()
        self._show_mode()

    @property
    def method(self) -> str:

        return "api_key" if self._key_mode else "password"

    def _store(self) -> None:
        if self._key_mode:
            headers = {self._header.text().strip(): self._value.text().strip()}
        else:
            pair = f"{self._user.text()}:{self._password.text()}".encode()
            headers = {"Authorization": "Basic " + base64.b64encode(pair).decode("ascii")}
        self._check(headers, self._save_typed)

    def _save_typed(self) -> None:
        url = self.origin
        typed = [self._password.text().strip(), self._value.text().strip()]
        if self._key_mode:
            key = accounts.store(url, accounts.HEADER, {"header": self._header.text(), "value": self._value.text()})
        else:
            key = accounts.store(url, accounts.BASIC, {"username": self._user.text(),
                                                       "password": self._password.text()})

        self._password.clear()
        self._value.clear()
        if not key:
            self._error.setText(self.tr("Not saved. Fill in the fields, and unlock QGIS's vault "
                                        "with its master password when QGIS asks."))
            self._error.show()
            self.adjustSize()
            return
        self._succeed(typed)


class TokenDialog(SignInDialog):



    method = "token"

    def __init__(self, provider: str = "", host: str = "", header: str = "", service: str = "", parent=None,
                 connector: str = "", origin: str = "", check_url: str = ""):
        self.provider = provider
        self.token_host, self.token_header = host, header or "Authorization"
        info = accounts.PROVIDERS.get(provider) or {"label": host, "host": host, "token_page": ""}  # nosec B105
        super().__init__(info["label"] if provider else host, parent, _connector_id(provider, connector), origin,
                         check_url)
        label = info["label"]
        self.setWindowTitle(self.tr("Connect {label}").format(label=label))
        self._heading.setText(self.tr("Connect {label}").format(label=label) if provider
                              else self.tr("Connect {service}").format(service=service) if service
                              else self.tr("Token for {host}").format(host=host))
        self._only(self._value)
        self._value.setPlaceholderText(self.tr("Paste your token"))
        placeholder_ink(self._value)
        self._value.setAccessibleName(self.tr("Token"))
        page = info["token_page"]
        if page:
            self._set_where(self.tr("Sign in with a free account, choose Generate Token, copy it and paste it here."))
            self._set_page(page, info["host"])
        else:
            self._set_where(self.tr("A token or an API key from your {service} account. Sent to {host} only.")
                            .format(service=service or host, host=host))
        self.adjustSize()
        self._value.setFocus()

    def _check_key(self) -> str:
        return self.provider or accounts.key_for(self.origin)

    def _store(self) -> None:
        token = self._value.text().strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        self._check({self.token_header: f"Bearer {token}"}, self._save_typed)

    def _save_typed(self) -> None:
        typed = [self._value.text().strip()]
        key = accounts.store_token(self.provider, self._value.text(), self.origin, self.token_header)
        if not key:
            self._value.clear()
            self._error.setText(self.tr("Not saved. Paste the token, and unlock QGIS's vault "
                                        "with its master password when QGIS asks."))
            self._error.show()
            self.adjustSize()
            return
        self._succeed(typed)


class KeysDialog(SignInDialog):


    method = "s3_keys"

    def __init__(self, provider: str, parent=None):
        self.provider = provider
        info = accounts.PROVIDERS[provider]
        super().__init__(info["label"], parent, _connector_id(provider))
        self.setWindowTitle(self.tr("Connect {label}").format(label=info["label"]))
        self._heading.setText(self.tr("Connect {label}").format(label=info["label"]))
        self._only(self._user, self._password)
        self._user.setPlaceholderText(self.tr("Access key"))
        placeholder_ink(self._user)
        self._user.setAccessibleName(self.tr("Access key"))
        self._password.setPlaceholderText(self.tr("Secret key"))
        placeholder_ink(self._password)
        self._password.setAccessibleName(self.tr("Secret key"))
        self._set_where(self.tr("Sign in with a free account, add a credential and paste both keys here."))
        self._set_page(info["token_page"], self.tr("the S3 keys manager"))
        self.adjustSize()
        self._user.setFocus()

    def _store(self) -> None:
        self._save_typed()

    def _save_typed(self) -> None:
        typed = [self._user.text().strip(), self._password.text().strip()]
        key = accounts.store_s3_keys(self.provider, self._user.text(), self._password.text())
        if not key:
            self._password.clear()
            self._error.setText(self.tr("Not saved. Paste both keys, and unlock QGIS's vault "
                                        "with its master password when QGIS asks."))
            self._error.show()
            self.adjustSize()
            return
        self._succeed(typed)
