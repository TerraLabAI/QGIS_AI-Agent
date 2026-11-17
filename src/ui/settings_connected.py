# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

import html
import time

from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QDialog, QHBoxLayout, QLabel, QMenu, QToolButton, QWidget

from ..core import accounts, accounts_oauth, telemetry
from ..core import telemetry_events as ev
from ..core.logger import log_warning
from .account_card import AccountMark, KeysDialog, SignInDialog, TokenDialog, provider_tag
from .card_base import msg_kind_colour
from .confirm_dialog import ask_confirm
from .font_scale import scale_px_length, scale_qss_font_px
from .icons import icon_for, pixmap_for
from .settings_pages import GHOST_BTN_QSS, MUTED, ROW_NOTE_QSS, Page, SettingGroup, SettingRow, Switch
from .shared import exec_dialog, resolve_qt_enum
from .style import _MENU_QSS, INK_2, INK_3
from .widgets import IconButton


def _day(epoch: float) -> str:

    from qgis.PyQt.QtCore import QDateTime, QLocale
    short = resolve_qt_enum(QLocale, "FormatType", "ShortFormat")
    return QLocale().toString(QDateTime.fromSecsSinceEpoch(int(epoch)).date(), short)


def dialog_for(key: str, entry: dict | None, parent=None):

    entry = entry or {}
    if key == "earthengine":
        from .earthengine_connect import ConnectDialog
        return ConnectDialog(parent, sign_in=True)
    info = accounts.PROVIDERS.get(key)
    if info is not None and info.get("own"):
        from .account_drive import ConnectDialog as DriveDialog
        return DriveDialog(key, parent)
    if info is not None:
        return KeysDialog(key, parent) if info.get("keys") == accounts.S3 else TokenDialog(key, parent=parent)
    if entry.get("kind") == accounts.CLOUD:
        from .account_cloud import CloudKeysDialog
        kept = ("store", "bucket", "account", "endpoint", "region", "https", "vendor", "address")
        need = {name: entry.get(name) for name in kept}
        need.update(key=key, located=bool(entry.get("endpoint")) or entry.get("store") != "s3")
        return CloudKeysDialog(need, parent)
    host = str(entry.get("host") or key)
    if entry.get("kind") == accounts.HEADER:
        return TokenDialog("", host, str(entry.get("header") or "Authorization"), "", parent, origin=key)
    return SignInDialog(host, parent, origin=key)


class ConnectedAccountsPage(Page):
    def __init__(self, parent=None):
        super().__init__(QCoreApplication.translate("ConnectedAccountsPage", "Connected accounts"),
                         QCoreApplication.translate("ConnectedAccountsPage",
                                                    "The sign-ins the AI agent may use, each only after you allow it."),
                         parent)
        self._group = SettingGroup(self)
        self.add(self._group)
        self._empty = QLabel(self.tr("No account yet. When a source asks you to sign in, the AI agent "
                                     "shows a card, and the access you save appears here."), self)
        self._empty.setWordWrap(True)
        self._empty.setStyleSheet(ROW_NOTE_QSS)
        self._empty.setContentsMargins(14, 4, 14, 0)
        self.add(self._empty)
        self.add(self._add_row())
        self.add(self._privacy())
        self.refresh()

    def _add_row(self) -> QWidget:
        row = QWidget(self)
        line = QHBoxLayout(row)
        line.setContentsMargins(14, 10, 14, 0)
        add = QToolButton(row)
        add.setObjectName("accountAdd")
        add.setText(self.tr("Add account"))
        add.setIcon(icon_for(add, "plus", scale_px_length(12), QColor(INK_2)))
        add.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        add.setStyleSheet(GHOST_BTN_QSS.replace("QPushButton", "QToolButton")
                          + "QToolButton::menu-indicator { image: none; width: 0px; }")
        add.setCursor(Qt.CursorShape.PointingHandCursor)
        add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(add)
        menu.setStyleSheet(scale_qss_font_px(_MENU_QSS))
        for key, info in sorted(accounts.PROVIDERS.items(), key=lambda kv: kv[1]["label"].lower()):
            if (info.get("own") and not accounts_oauth.browser_ready(key)) or not accounts.enabled(key):
                continue
            what = (self.tr("S3 keys") if info.get("keys") == accounts.S3
                    else self.tr("{name} sign-in").format(name=accounts_oauth.browser_name(key, "Google"))
                    if info.get("keys") == accounts.OAUTH else self.tr("Token"))
            action = menu.addAction(f"{info['label']} · {what}")
            action.setObjectName(f"accountAdd_{key}")
            action.triggered.connect(lambda _=False, k=key: self._connect(k))
        add.setMenu(menu)
        line.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)
        line.addStretch(1)
        return row

    def _privacy(self) -> QWidget:
        row = QWidget(self)
        line = QHBoxLayout(row)
        line.setContentsMargins(14, 12, 14, 0)
        line.setSpacing(6)
        lock = QLabel(row)
        lock.setPixmap(pixmap_for(row, "lock", scale_px_length(12), QColor(INK_3)))
        lock.setAlignment(Qt.AlignmentFlag.AlignTop)
        line.addWidget(lock, 0, Qt.AlignmentFlag.AlignTop)
        text = QLabel(self.tr("Kept in QGIS's encrypted vault, on this computer. Neither TerraLab "
                              "nor the AI agent sees them."), row)
        text.setWordWrap(True)
        text.setStyleSheet(f"font-size: 11px; color: {MUTED}; background: transparent;")
        line.addWidget(text, 1)
        return row

    def showEvent(self, event):  # noqa: N802
        self.refresh()
        super().showEvent(event)

    def _note(self, key: str, entry: dict) -> str:

        kind = (self.tr("Signed in with the browser") if entry.get("via") == "browser"
                else self.tr("Username and password") if entry.get("kind") == accounts.BASIC
                else self.tr("S3 keys") if entry.get("kind") == accounts.S3
                else self.tr("Google account") if entry.get("kind") == accounts.OAUTH
                else self.tr("Storage keys") if entry.get("kind") == accounts.CLOUD
                else self.tr("Token") if key in accounts.PROVIDERS else self.tr("API key"))
        host = str(entry.get("host") or key)
        parts = [kind]
        if key == "earthengine":
            from ..core import earthengine_account
            project = earthengine_account.project()
            parts.append(self.tr("Project {project}").format(project=project) if project
                         else self.tr("No project picked"))
        if entry.get("account"):
            parts.append(str(entry["account"]))
        if accounts.label_of(key, entry) != host:
            parts.append(host)
        note = html.escape(" · ".join(parts))
        when = entry.get("expires")
        colour = msg_kind_colour("error")

        def red(said: str) -> str:
            return f'{note} · <span style="color: {colour};">{html.escape(said)}</span>'
        if entry.get("state") == "refused":
            return red(self.tr("Expired") if accounts.expired(entry) else self.tr("Refused at its last use"))
        if isinstance(when, (int, float)):
            if when < time.time():
                return red(self.tr("Expired"))
            return f"{note} · {html.escape(self.tr('Expires on {day}').format(day=_day(when)))}"
        return note

    def refresh(self) -> None:
        self._group.clear()
        rows = sorted(accounts.entries().items(), key=lambda kv: str(kv[1].get("label") or kv[0]).lower())
        for key, entry in rows:

            held = not accounts.enabled(key)
            host = str(entry.get("host") or key)
            label = accounts.label_of(key, entry)
            more = IconButton(self._group, "more", 16, self.tr("More actions for {label}").format(label=label))
            more.setObjectName("accountMore")
            more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            more.setStyleSheet(more.styleSheet() + "QToolButton::menu-indicator { image: none; width: 0px; }")
            menu = QMenu(more)
            menu.setStyleSheet(scale_qss_font_px(_MENU_QSS))
            if key == "earthengine" and not held:
                change = menu.addAction(self.tr("Change project…"))
                change.setObjectName("earthEngineChangeProject")
                change.triggered.connect(self._change_project)

            if not held:
                replace = menu.addAction(self.tr("Choose files…") if key == "googledrive" else self.tr("Replace…"))
                replace.setObjectName("accountReplace")
                replace.triggered.connect(lambda _=False, k=key: self._connect(k))
            remove = menu.addAction(self.tr("Remove…"))
            remove.setObjectName("accountRemove")
            remove.triggered.connect(lambda _=False, k=key: self._remove(k))
            more.setMenu(menu)
            control = QWidget(self._group)
            pair = QHBoxLayout(control)
            pair.setContentsMargins(0, 0, 0, 0)
            pair.setSpacing(8)
            if not held:
                said = QLabel(self.tr("Always allow"), control)
                said.setStyleSheet(ROW_NOTE_QSS)
                flip = Switch(control, accounts.always(key))
                flip.setObjectName("accountAlways")
                flip.setAccessibleName(self.tr("Always allow {label}").format(label=label))
                flip.setToolTip(self.tr("Used with no card each time. Off: the AI agent asks first."))
                flip.toggled.connect(lambda on, k=key: accounts.set_always(k, on))
                pair.addWidget(said)
                pair.addWidget(flip)
            pair.addWidget(more)
            row = SettingRow(label, "", control, self._group)
            row.note_label.setTextFormat(Qt.TextFormat.RichText)
            row.note_label.setText(self._note(key, entry))
            row.note_label.show()
            cid = str((accounts.PROVIDERS.get(key) or {}).get("connector") or "")
            row.layout().insertWidget(0, AccountMark(row, cid, host, 32), 0, Qt.AlignmentFlag.AlignVCenter)
            self._group.add_row(row)
        self._group.setVisible(bool(rows))
        self._empty.setVisible(not rows)

    def _connect(self, key: str) -> None:

        entry = accounts.entries().get(key) or {}
        dialog = dialog_for(key, entry, self.window())
        try:
            if exec_dialog(dialog) == QDialog.DialogCode.Accepted and dialog.saved:
                tag = provider_tag(key, store=str(entry.get("store") or ""))
                telemetry.track(ev.ACCOUNT_CONNECTED, {"provider": tag, "method": dialog.method})
        finally:
            dialog.deleteLater()
        self.refresh()

    def _remove(self, key: str) -> None:
        entry = accounts.entries().get(key) or {}
        host = str(entry.get("host") or key)
        label = accounts.label_of(key, entry)
        if not ask_confirm(self, title=self.tr("Remove your {label} access?").format(label=label),
                           note=self.tr("QGIS deletes it from its vault. Layers loaded with it stop loading, "
                                        "and the AI agent asks you to sign in again the next time {host} "
                                        "needs it.").format(host=host),
                           confirm_text=self.tr("Remove"), glyph="trash", object_name="accountRemoveConfirm"):
            return
        tag = provider_tag(key, store=str(entry.get("store") or ""))
        try:
            if key == "earthengine":
                from ..core import earthengine_account
                earthengine_account.remove()
            else:
                accounts.remove(key)
            telemetry.track(ev.ACCOUNT_REMOVED, {"provider": tag})
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Access for {key} not removed: {exc}")
        self.refresh()

    def _change_project(self) -> None:
        from .earthengine_connect import ConnectDialog
        dialog = ConnectDialog(self.window())
        try:
            exec_dialog(dialog)
        finally:
            dialog.deleteLater()
        self.refresh()
