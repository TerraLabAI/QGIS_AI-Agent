# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

import json
import os
import threading
import urllib.parse
import weakref
from datetime import datetime

from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.PyQt.QtWidgets import QFileDialog, QLineEdit, QPushButton

from ..core import accounts_cloud
from ..core.accounts_cloud import AZURE, GCS, LABELS, S3
from ..core.background import main_thread_invoker
from ..core.qt_compat import qt_deleted
from .account_card import SignInDialog


_KEY_PAGES = {
    S3: ("https://console.aws.amazon.com/iam/home#/security_credentials", "AWS IAM"),
    AZURE: ("https://portal.azure.com/#browse/Microsoft.Storage%2FStorageAccounts", "the Azure portal"),
    GCS: ("https://console.cloud.google.com/iam-admin/serviceaccounts", "Google Cloud console"),
}
_KEY_FILE_MAX_BYTES = 64 * 1024


def tr(text: str) -> str:
    return QCoreApplication.translate("CloudKeysDialog", text)


def card_texts(need: dict) -> tuple[str, str]:

    shown = str(need.get("host") or "")
    store = str(need.get("store") or "")
    if need.get("kind") == "retry" and need.get("expired"):
        return tr("Keys expired"), tr("{store} no longer accepts your saved keys.").format(store=shown)
    if need.get("kind") == "retry":
        return tr("Keys refused"), tr("{store} refused your saved keys.").format(store=shown)
    reason = {S3: tr("This bucket opens only with your access keys."),
              AZURE: tr("This container opens only with your account key or a SAS token."),
              GCS: tr("This bucket opens only with a service account key.")}.get(store, "")
    return tr("Connect {store}").format(store=shown), reason


def _facts(values: dict, meta: dict) -> dict:


    facts = {"account": str(values.get("username") or "")[:64]} if meta.get("secret") == "service_account" else {}
    if meta.get("secret") == "sas":
        end = urllib.parse.parse_qs(str(values.get("password") or "")).get("se", [""])[0]
        try:
            facts["expires"] = datetime.fromisoformat(end.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return facts


def _test(address: str, values: dict, meta: dict) -> str:



    ref = accounts_cloud.parse(address)
    if ref is None:
        return ""
    if ref.store == AZURE and not ref.account and meta.get("account"):
        ref = accounts_cloud.Ref(AZURE, ref.bucket, ref.key, account=meta["account"], located=True,
                                 address=ref.address)
    if accounts_cloud.probe(ref, values, meta) != "refused":
        return ""
    return tr("{where} refused these keys.").format(where=str(meta.get("endpoint") or "") or ref.shown)


class CloudKeysDialog(SignInDialog):


    method = "cloud_keys"

    def __init__(self, need: dict, parent=None):
        self.need = dict(need or {})
        self.store = str(self.need.get("store") or S3)
        self.cloud_key = str(self.need.get("key") or "")
        label = str(self.need.get("vendor") or LABELS.get(self.store, self.store))
        super().__init__(label, parent)
        self._sas = False
        self.setWindowTitle(tr("Connect {label}").format(label=label))
        self._heading.setText(tr("Connect {label}").format(label=label))
        where = str(self.need.get("address") or self.need.get("bucket") or "")
        self._set_where(tr("For {address}. A read-only key is enough: the AI agent only reads from it.")
                        .format(address=where))
        page = _KEY_PAGES.get(self.store) if not self.need.get("endpoint") else None
        if page:
            self._set_page(*page)
        self._endpoint = self._region = self._account = self._key_file = None
        self._hmac = False
        self._service_account: dict = {}
        self._checking = False
        if self.store == AZURE:
            self._account = self._field(tr("Storage account name"), self._fields,
                                        text=str(self.need.get("account") or ""))
            self._fields.removeWidget(self._account)
            self._fields.insertWidget(0, self._account)
            self._account.setVisible(not self.need.get("account"))
            self._only(self._password)
            self._password.setPlaceholderText(tr("Account key"))
            self._password.setAccessibleName(tr("Account key"))
            self._value.setPlaceholderText(tr("SAS token"))
            self._value.setAccessibleName(tr("SAS token"))
            self._toggle.clicked.disconnect()
            self._toggle.clicked.connect(self._flip_sas)
            self._toggle.setText(tr("Use a SAS token instead"))
            self._toggle.show()
        else:
            self._only(self._user, self._password)
            gcs = self.store == GCS
            for edit, text in ((self._user, tr("HMAC access key") if gcs else tr("Access key ID")),
                               (self._password, tr("HMAC secret") if gcs else tr("Secret access key"))):
                edit.setPlaceholderText(text)
                edit.setAccessibleName(text)
        if self.store == GCS:
            self._key_file = QPushButton(tr("Service account key (JSON file)"), self)
            self._key_file.setCursor(Qt.CursorShape.PointingHandCursor)
            self._key_file.setObjectName("accountKeyFile")
            self._key_file.setAutoDefault(False)
            self._key_file.setStyleSheet(self._toggle.styleSheet())
            self._key_file.clicked.connect(self._pick_key_file)
            self._fields.insertWidget(0, self._key_file)
            self._toggle.clicked.disconnect()
            self._toggle.clicked.connect(self._flip_hmac)
            self._toggle.show()
            self._show_gcs()
        if self.store == S3:
            if not self.need.get("located"):
                self._endpoint = self._field(tr("Endpoint, empty for Amazon S3"), self._fields)
            self._region = self._field(tr("Region (optional)"), self._fields, text=str(self.need.get("region") or ""))
        for edit in (self._account, self._endpoint, self._region):
            if edit is not None:
                edit.textChanged.connect(self._sync_save)
        self._sync_save()
        self.adjustSize()
        (self._account if self._account is not None and self._account.isVisible() else
         self._key_file if self._key_file is not None else
         self._password if self.store == AZURE else self._user).setFocus()

    def _show_gcs(self) -> None:
        self._key_file.setVisible(not self._hmac)
        self._user.setVisible(self._hmac)
        self._password.setVisible(self._hmac)
        self._toggle.setText(tr("Use a service account key instead") if self._hmac else tr("Use an HMAC key instead"))
        self._error.hide()
        self._sync_save()
        self.adjustSize()

    def _flip_hmac(self) -> None:
        self._hmac = not self._hmac
        self._show_gcs()

    def _pick_key_file(self) -> None:


        path, _ = QFileDialog.getOpenFileName(self, tr("Service account key (JSON file)"), "",
                                              tr("JSON key (*.json)"))
        if not path:
            return
        self._service_account = {}
        try:
            if os.path.getsize(path) > _KEY_FILE_MAX_BYTES:
                raise ValueError
            with open(path, encoding="utf-8-sig") as handle:
                data = json.load(handle)
            if data.get("type") != "service_account" or not data.get("client_email") or not data.get("private_key"):
                raise ValueError
        except (OSError, ValueError, AttributeError):
            self._key_file.setText(tr("Service account key (JSON file)"))
            self._sync_save()
            self._error.setText(tr("This file is not a service account key. In the Google Cloud console, "
                                   "open the service account, then Keys, Add key, JSON."))
            self._error.show()
            return
        self._service_account = {"username": str(data["client_email"]), "password": str(data["private_key"])}
        self._key_file.setText(str(data["client_email"]))
        self._sync_save()

    def _flip_sas(self) -> None:
        self._sas = not self._sas
        self._password.setVisible(not self._sas)
        self._value.setVisible(self._sas)
        self._toggle.setText(tr("Use the account key instead") if self._sas else tr("Use a SAS token instead"))
        self._error.hide()
        self._sync_save()
        self.adjustSize()

    def _sync_save(self) -> None:
        shown = [e for e in (self._user, self._password, self._value, getattr(self, "_account", None))
                 if isinstance(e, QLineEdit) and not e.isHidden()]
        key_file = getattr(self, "_key_file", None)
        picked = key_file is None or key_file.isHidden() or bool(getattr(self, "_service_account", None))
        self._save.setEnabled(all(e.text().strip() for e in shown) and picked
                              and not getattr(self, "_checking", False))
        self._error.hide()

    def done(self, result) -> None:  # noqa: D401
        self._service_account = {}
        super().done(result)

    def _typed(self) -> tuple[dict, dict]:

        meta = {"bucket": str(self.need.get("bucket") or ""), "address": str(self.need.get("address") or "")}
        if self.store == AZURE:
            account = str(self.need.get("account") or self._account.text()).strip().lower()
            secret = (self._value.text() if self._sas else self._password.text()).strip()
            meta.update(account=account,
                        secret="sas" if self._sas else "account_key")  # pragma: allowlist secret
            return {"username": account, "password": secret.lstrip("?") if self._sas else secret}, meta
        if self.store == GCS and not self._hmac:
            meta.update(secret="service_account")  # nosec B106  # pragma: allowlist secret
            return dict(self._service_account), meta
        values = {"username": self._user.text().strip(), "password": self._password.text().strip()}
        if self.store == GCS:
            meta.update(secret="hmac")  # nosec B106  # pragma: allowlist secret
        if self.store == S3:
            endpoint, https = str(self.need.get("endpoint") or ""), self.need.get("https") is not False
            if self._endpoint is not None and self._endpoint.text().strip():
                text = self._endpoint.text().strip().rstrip("/")
                https = not text.lower().startswith("http://")
                endpoint = text.split("://", 1)[-1].lower()
            meta.update(endpoint="" if endpoint == "s3.amazonaws.com" else endpoint, https=https,
                        region=self._region.text().strip() if self._region is not None else "",
                        vendor=str(self.need.get("vendor") or ""))
        return values, meta

    def _test(self, values: dict, meta: dict) -> str:
        return _test(str(self.need.get("address") or ""), values, meta)

    def _store(self) -> None:

        if self._checking:
            return
        values, meta = self._typed()
        self._checking = True
        self._set_checking(True)
        address, window = str(self.need.get("address") or ""), weakref.ref(self)

        def answer(error: str) -> None:
            dialog = window()
            if not qt_deleted(dialog):
                dialog._on_checked(error)

        def check() -> None:
            try:
                error = _test(address, values, meta)
            except Exception as exc:  # noqa: BLE001
                error = tr("The keys could not be checked: {error}").format(error=str(exc)[:200])
            main_thread_invoker().invoke(lambda: answer(error))
        self._pending = (values, meta)
        threading.Thread(target=check, name="cloud-keys-check", daemon=True).start()

    def _set_checking(self, on: bool) -> None:
        super()._set_checking(on)
        for widget in (self._account, self._endpoint, self._region, self._key_file):
            if widget is not None:
                widget.setEnabled(not on)

    def _on_checked(self, error: str) -> None:
        self._checking = False
        self._set_checking(False)
        if self.result() or not self.isVisible():
            self._pending = ({}, {})
            return
        values, meta = getattr(self, "_pending", ({}, {}))
        self._pending = ({}, {})
        if error:
            for edit in (self._password, self._value):
                edit.clear()
            if self.store == GCS and not self._hmac:
                self._service_account = {}
                self._key_file.setText(tr("Service account key (JSON file)"))
            self._sync_save()
            self._error.setText(error)
            self._error.show()
            self.adjustSize()
            return
        key = accounts_cloud.save(self.cloud_key, self.store, values, meta, _facts(values, meta))
        typed = [str(values.get("username") or ""), str(values.get("password") or "")]
        for edit in (self._password, self._value):
            edit.clear()
        self._service_account = {}
        if not key:
            self._sync_save()
            self._error.setText(tr("Not saved. Fill in the fields, and unlock QGIS's vault "
                                   "with its master password when QGIS asks."))
            self._error.show()
            return
        self._succeed(typed)
