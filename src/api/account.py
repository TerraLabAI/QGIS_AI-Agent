# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Account facade: sign in by browser pairing, revalidate, sign out, delete."""













from __future__ import annotations

import secrets
import time
from typing import Any, Callable

from qgis.core import QgsApplication, QgsTask
from qgis.PyQt.QtCore import (
    QCoreApplication,
    QDateTime,
    QLocale,
    QObject,
    QSysInfo,
    Qt,
    QUrl,
    pyqtSignal,
)
from qgis.PyQt.QtGui import QDesktopServices

from ..core.logger import log, log_warning
from ..core.settings import KEY_RE, Settings, key_prefix
from .pairing_poll_task import PairingPollTask
from .terralab_client import HARD_CONNECTIVITY_CODES, PRODUCT_ID, TerraLabClient

_UTM = f"utm_source=qgis&utm_medium=plugin&utm_campaign={PRODUCT_ID}"
DASHBOARD_URL = f"https://terra-lab.ai/dashboard/{PRODUCT_ID}?{_UTM}&utm_content=dashboard"
PLANS_URL_FALLBACK = "https://terra-lab.ai/pricing"
PRO_CHECKOUT_URL_BASE = f"https://terra-lab.ai/dashboard?action=checkout&product={PRODUCT_ID}"
_REVALIDATE_EVERY_S = 900.0
_PLATFORM_MAX_LEN = 48
_cached_platform: str | None = None


def tr(text: str) -> str:
    return QCoreApplication.translate("Account", text)


def _openable_https(url: object) -> bool:
    """True for a URL this plugin may hand to the user's browser."""






    if not isinstance(url, str) or not url:
        return False
    try:
        from urllib.parse import urlsplit

        parsed = urlsplit(url)
    except ValueError:
        return False
    return (parsed.scheme == "https" and bool(parsed.hostname)
            and not parsed.username and not parsed.password
            and not any(ord(char) < 32 or ord(char) == 127 for char in url))


def is_rejection_code(code: str) -> bool:
    """True when a /usage error code means the stored key is no longer usable."""
    return (code or "").strip().upper() in ("INVALID_KEY", "SUBSCRIPTION_INACTIVE")


def usage_to_runs(payload: dict) -> dict:
    """The website /usage payload (images_* for every product) as runs."""
    payload = payload if isinstance(payload, dict) else {}
    return {"runs_used": payload.get("images_used"), "runs_limit": payload.get("images_limit"),
            "period_end": payload.get("period_end") or payload.get("reset_date") or "",
            "is_subscriber": payload.get("is_subscriber")}


def get_dashboard_url() -> str:
    from ..ui.shared import get_dashboard_url as served_dashboard_url
    return served_dashboard_url()


def get_pro_checkout_url(cta_source: str) -> str:
    from ..ui.shared import get_upgrade_url
    source = "".join(ch for ch in str(cta_source or "") if ch.isalnum() or ch == "_") or "plugin"
    served = get_upgrade_url()
    if served:
        join = "&" if "?" in served else "?"
        return f"{served}{join}cta_source={source}"
    return f"{PRO_CHECKOUT_URL_BASE}&cta_source={source}&{_UTM}"


def get_plans_page_url(cta_source: str = "plugin") -> str:
    from ..ui.shared import get_upgrade_url
    source = "".join(ch for ch in str(cta_source or "") if ch.isalnum() or ch == "_") or "plugin"
    return get_upgrade_url() or f"{PLANS_URL_FALLBACK}?{_UTM}&utm_content={source}"


def get_device_platform() -> str:
    """OS label such as "macOS 15.0", ASCII only, for the X-Device-Platform header."""
    global _cached_platform
    if _cached_platform is None:
        try:
            name = QSysInfo.prettyProductName() or ""
        except Exception:  # nosec B110 - device header is optional
            name = ""
        name = " ".join(name.split()).encode("ascii", "ignore").decode("ascii")
        _cached_platform = name.strip()[:_PLATFORM_MAX_LEN]
    return _cached_platform


def format_local_date(stamp: str) -> str:
    """An ISO instant from the server as the date this user reads, or ""."""





    raw = str(stamp or "").strip()
    if not raw:
        return ""
    for form in (Qt.DateFormat.ISODateWithMs, Qt.DateFormat.ISODate):
        when = QDateTime.fromString(raw, form)
        if when.isValid():
            return QLocale().toString(when.toLocalTime().date(), QLocale.FormatType.ShortFormat)
    return ""


class GenericRequestTask(QgsTask):
    """Run a no-args client call off the main thread. Raises or {"error"} -> failed."""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str, str)

    def __init__(self, description: str, request_fn: Callable[[], Any], hidden: bool = True):
        flags = QgsTask.Flag.CanCancel
        if hidden:
            for name in ("Hidden", "Silent"):
                extra = getattr(QgsTask.Flag, name, None)
                if extra is not None:
                    flags = flags | extra
        super().__init__(description, flags)
        self._request_fn = request_fn
        self._result: Any = None
        self._failure: tuple[str, str] | None = None

    def is_active(self) -> bool:
        try:
            return self.status() in (QgsTask.TaskStatus.Running, QgsTask.TaskStatus.Queued,
                                     QgsTask.TaskStatus.OnHold)
        except Exception:
            return False

    def run(self) -> bool:
        if self.isCanceled():
            return False
        try:
            result = self._request_fn()
        except Exception as exc:  # noqa: BLE001
            self._failure = (str(exc)[:300], "CLIENT_ERROR")
            return False
        if isinstance(result, dict) and "error" in result:
            self._failure = (str(result.get("error") or "")[:300], str(result.get("code") or "UNKNOWN"))
            return False
        self._result = result
        return True

    def finished(self, result: bool) -> None:
        if self.isCanceled():
            self._request_fn = lambda: None
            self._result = None
            return
        try:
            if result:
                self.succeeded.emit(self._result)
            else:
                message, code = self._failure or ("", "UNKNOWN")
                self.failed.emit(message, code)
        finally:


            self._request_fn = lambda: None
            self._result = None


class Account(QObject):
    state_changed = pyqtSignal(str, str)
    paired = pyqtSignal(str)
    signed_out = pyqtSignal()
    session_expired = pyqtSignal(str)
    pairing_started = pyqtSignal(str)
    pairing_address = pyqtSignal(str)
    pairing_browser_seen = pyqtSignal()
    pairing_stalled = pyqtSignal(str, str)
    pairing_failed = pyqtSignal(str, str)
    pairing_timeout = pyqtSignal()
    usage_refreshed = pyqtSignal(object)
    account_loaded = pyqtSignal(object)
    account_failed = pyqtSignal(str, str)
    notice = pyqtSignal(str, str)

    SIGNED_OUT, PAIRING, ACTIVATED, LOCKED, EXPIRED = "signed_out", "pairing", "activated", "locked", "expired"

    def __init__(self, settings: Settings | None = None, parent=None):
        super().__init__(parent)
        self._settings = settings or Settings()
        self._client = TerraLabClient()
        self._pairing_worker: PairingPollTask | None = None
        self._pairing_cancel_task = None
        self._revalidate_task: GenericRequestTask | None = None
        self._account_task: GenericRequestTask | None = None
        self._delete_task: GenericRequestTask | None = None
        self._login_link_task: GenericRequestTask | None = None
        self._pending_code = ""
        self._pairing_url = ""
        self._pairing_t0 = 0.0
        self._last_key_validation_unix = 0.0
        self._last_key_validation_at = float("-inf")
        self._billing_warning_shown = False
        self._state = self._stored_state()



    def _stored_state(self) -> str:
        if self._settings.activation_key_is_locked():
            return self.LOCKED
        return self.ACTIVATED if self._settings.has_activation_key else self.SIGNED_OUT

    @property
    def state(self) -> str:
        return self._state

    def _set_state(self, state: str, message: str = "") -> None:
        self._state = state
        self.state_changed.emit(state, message)

    @property
    def activation_key(self) -> str:
        return self._settings.activation_key

    @property
    def has_activation_key(self) -> bool:
        return self._settings.has_activation_key

    @property
    def key_prefix(self) -> str:
        return key_prefix(self.activation_key)

    @property
    def device_hash(self) -> str:
        return self._settings.device_hash

    @property
    def base_url(self) -> str:
        return self._client.base_url

    def get_auth_header(self) -> dict:
        """Same headers as the siblings: bearer key, product, device hash and platform."""
        token = self.activation_key
        if not token:
            return {}
        headers = {"Authorization": f"Bearer {token}", "X-Product-ID": PRODUCT_ID}
        try:
            headers["X-Device-Hash"] = self.device_hash
            platform_label = get_device_platform()
            if platform_label:
                headers["X-Device-Platform"] = platform_label
        except Exception:  # nosec B110 - device header is optional
            pass
        return headers



    def connect_url(self, code: str) -> str:
        from urllib.parse import quote
        safe_code = quote(str(code or ""), safe="")
        return (f"{self._client.base_url}/connect?code={safe_code}&product={PRODUCT_ID}"
                f"&{_UTM}&utm_content=connect")

    def start_pairing(self) -> None:
        """Mint a code, start the poll first, then open the browser."""
        self.cancel_pairing()
        self._pending_code = secrets.token_urlsafe(32)
        self._open_pairing_page(self._pending_code)

    def reopen_pairing_page(self) -> None:
        if self._pending_code:
            self._open_pairing_page(self._pending_code)

    def _open_pairing_page(self, code: str) -> None:
        self._start_pairing_poll(code)
        url = self.connect_url(code)
        self._pairing_url = url
        self._set_state(self.PAIRING, tr("Waiting for the sign-in page in your browser..."))
        self.pairing_started.emit(url)
        if not QDesktopServices.openUrl(QUrl(url)):
            self._show_pairing_address(url)

    def _start_pairing_poll(self, code: str) -> None:
        worker = self._pairing_worker
        if worker is not None and worker.is_active():
            if worker.pairing_code == code:
                return
            for signal_name in ("pairing_succeeded", "pairing_failed", "pairing_timeout",
                                "pairing_stalled", "pairing_browser_seen"):
                try:
                    getattr(worker, signal_name).disconnect()
                except (TypeError, RuntimeError):
                    pass
            try:
                worker.cancel()
            except RuntimeError:
                pass
        worker = PairingPollTask(self._client, code)
        worker.pairing_succeeded.connect(self._on_pairing_succeeded)
        worker.pairing_failed.connect(self._on_pairing_failed)
        worker.pairing_timeout.connect(self._on_pairing_timeout)
        worker.pairing_stalled.connect(self._on_pairing_stalled)
        worker.pairing_browser_seen.connect(self.pairing_browser_seen)
        self._pairing_worker = worker
        self._pairing_t0 = time.monotonic()
        QgsApplication.taskManager().addTask(worker)
        log("Pairing started")

    def _show_pairing_address(self, url: str) -> None:
        copied = False
        try:
            from qgis.PyQt.QtWidgets import QApplication

            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(url)
                copied = True
        except Exception:
            copied = False
        if copied:
            message = tr("QGIS could not open a browser. The sign-in address is copied to your "
                         "clipboard: paste it into a browser to finish, then come back here. It works once.")
        else:
            message = tr("QGIS could not open a browser. Open this address to finish signing in, "
                         "then come back here. It works once:\n{}").format(url)
        self.pairing_address.emit(message)

    def _on_pairing_succeeded(self, key: str) -> None:
        if not self.is_valid_key(key):
            self._on_pairing_failed(tr("Unexpected response from the server. Please try again."),
                                    "BAD_KEY")
            return
        self._settings.set_activation_key(key)
        self._pending_code = ""
        self._pairing_url = ""
        self._last_key_validation_unix = 0.0
        self._last_key_validation_at = float("-inf")
        self._billing_warning_shown = False
        self._set_state(self.ACTIVATED, tr("Signed in."))
        self.paired.emit(key_prefix(key))
        log("Pairing successful")

    def _on_pairing_failed(self, message: str, code: str) -> None:
        self._pending_code = ""
        self._set_state(self.SIGNED_OUT, message)
        self.pairing_failed.emit(message, code)
        log_warning(f"Pairing failed ({code})")

    def _on_pairing_timeout(self) -> None:
        self._pending_code = ""
        message = tr("Sign-in timed out. Click Sign in to try again.")
        self._set_state(self.SIGNED_OUT, message)
        self.pairing_timeout.emit()
        log("Pairing timed out")

    def _on_pairing_stalled(self, reason: str = "") -> None:
        if reason == PairingPollTask.STALL_CODE_EXPIRED:
            message = tr("This sign-in code has expired. Click Cancel, then Sign in to get a new one.")
        else:
            message = tr("Still waiting for the sign-in page. If no browser opened, or the page "
                         "shows an error, click Cancel and try again.")
            if self._pairing_url:
                message += "\n\n" + tr("You can also open this address by hand:\n{}").format(self._pairing_url)
        self.pairing_stalled.emit(reason, message)

    def cancel_pairing(self) -> None:
        code, self._pending_code = self._pending_code, ""
        self._pairing_url = ""
        worker = self._pairing_worker
        if worker is not None and worker.is_active():
            try:
                worker.cancel()
            except RuntimeError:
                pass
        if code:
            client = self._client
            self._pairing_cancel_task = QgsTask.fromFunction(
                tr("Cancelling sign-in"), lambda task, c=code: client.cancel_pairing(c))
            QgsApplication.taskManager().addTask(self._pairing_cancel_task)
        if self._state == self.PAIRING:
            self._set_state(self._stored_state(), "")
        log("Pairing cancelled")



    def refresh_activation_async(self, force: bool = False) -> None:
        """Re-check the stored key against /usage in a hidden task, once per 15 min."""

        if self._settings.activation_key_is_locked():
            self._set_state(self.LOCKED, tr("You are signed in on this computer, but QGIS cannot "
                                            "read your sign-in until you enter its master password."))
            return
        if not self.has_activation_key:
            if self._state not in (self.PAIRING, self.SIGNED_OUT):
                self._set_state(self.SIGNED_OUT, "")
            return





        if not force and (time.monotonic() - self._last_key_validation_at) < _REVALIDATE_EVERY_S:
            return
        if self._revalidate_task is not None and self._revalidate_task.is_active():
            return
        auth = self.get_auth_header()
        client = self._client
        task = GenericRequestTask(tr("Checking your AI Agent subscription"),
                                  lambda: client.get_usage(auth=auth), hidden=True)
        task.succeeded.connect(self._on_key_revalidate_ok)
        task.failed.connect(self._on_key_revalidate_failed)
        self._revalidate_task = task
        QgsApplication.taskManager().addTask(task)

    def fetch_account_async(self) -> None:
        """Account and usage off the main thread, for the settings dialog."""



        if not self.has_activation_key:
            self.account_failed.emit(tr("Sign in to see your account."), "SIGNED_OUT")
            return
        task = self._account_task
        if task is not None and task.is_active():
            return
        auth = self.get_auth_header()
        client = self._client

        def _fetch():
            account = client.get_account(auth=auth)
            if not isinstance(account, dict) or "error" in account:
                return account
            account = dict(account)
            usage = client.get_usage(auth=auth)
            if not isinstance(usage, dict) or "error" in usage:
                return usage if isinstance(usage, dict) else {
                    "error": tr("Could not load account usage."), "code": "USAGE_ERROR"}
            account["usage"] = usage_to_runs(usage)
            account["_usage_payload"] = usage
            return account

        task = GenericRequestTask(tr("Loading the TerraLab account"), _fetch)
        task.succeeded.connect(self._on_account_ok)
        task.failed.connect(self._on_account_failed)
        self._account_task = task
        QgsApplication.taskManager().addTask(task)

    def _on_account_ok(self, account: object) -> None:
        self._account_task = None
        account = dict(account) if isinstance(account, dict) else {}
        payload = account.pop("_usage_payload", None)
        if isinstance(payload, dict):
            self._last_key_validation_unix = time.time()
            self._last_key_validation_at = time.monotonic()
            self.usage_refreshed.emit(payload)
        self.account_loaded.emit(account)

    def _on_account_failed(self, message: str, code: str) -> None:
        self._account_task = None
        if (code or "").strip().upper() == "INVALID_KEY":
            self._settings.clear_activation_key()
            self._set_state(self.SIGNED_OUT, "")
            self.signed_out.emit()
        self.account_failed.emit(message, code)



    def delete_account_async(self, confirm: str) -> None:
        """Ask the server to schedule the erasure of the signed-in account."""







        confirm = str(confirm or "").strip()
        if not confirm:
            return
        if not self.has_activation_key:
            self.notice.emit("warning", tr("Sign in first, then you can delete your account."))
            return
        task = self._delete_task
        if task is not None and task.is_active():
            return
        auth = self.get_auth_header()
        client = self._client
        answer: dict = {}

        def _call():
            result = client.delete_account(auth=auth, confirm=confirm)
            if isinstance(result, dict):
                answer.clear()
                answer.update(result)
            return result

        task = GenericRequestTask(tr("Deleting your TerraLab account"), _call)
        task.succeeded.connect(self._on_delete_ok)
        task.failed.connect(lambda message, code: self._on_delete_failed(message, code, dict(answer)))
        self._delete_task = task
        QgsApplication.taskManager().addTask(task)

    def _on_delete_ok(self, payload: object) -> None:
        self._delete_task = None
        payload = payload if isinstance(payload, dict) else {}
        self._clear_local_session()
        log("Account deletion scheduled")
        date = format_local_date(str(payload.get("purge_after") or ""))
        if date:
            self.notice.emit("info", tr("Your account is scheduled for deletion. Everything is erased "
                                        "for good on {date}. Until then, sign in on terra-lab.ai to "
                                        "cancel it.").format(date=date))
        else:
            self.notice.emit("info", tr("Your account is scheduled for deletion. Until the grace "
                                        "period ends, sign in on terra-lab.ai to cancel it."))

    def _on_delete_failed(self, message: str, code: str, payload: dict) -> None:
        self._delete_task = None
        normalized = (code or "").strip().upper()
        payload = payload if isinstance(payload, dict) else {}
        log_warning(f"Account deletion refused ({normalized or 'unknown'})")
        if normalized == "CONFIRM_MISMATCH":
            self.notice.emit("warning", tr("That address does not match the account. Nothing was "
                                           "deleted."))
            return
        if normalized in ("ALREADY_SCHEDULED", "ACCOUNT_DELETION_SCHEDULED"):
            date = format_local_date(str(payload.get("purge_after") or ""))
            if date:
                self.notice.emit("warning", tr("This account is already scheduled for deletion. "
                                               "Everything is erased for good on {date}. Sign in on "
                                               "terra-lab.ai to cancel it.").format(date=date))
            else:
                self.notice.emit("warning", tr("This account is already scheduled for deletion. Sign "
                                               "in on terra-lab.ai to cancel it."))
            return
        if normalized == "RATE_LIMITED":
            seconds = 0
            try:
                seconds = int(float(payload.get("retry_after") or 0))
            except (TypeError, ValueError):
                seconds = 0
            if seconds > 0:
                self.notice.emit("warning", tr("Too many attempts. Wait {n} seconds, then try "
                                               "again.").format(n=seconds))
            else:
                self.notice.emit("warning", tr("Too many attempts. Wait a moment, then try again."))
            return
        if normalized in ("NO_AUTH", "INVALID_KEY", "NO_ACCOUNT"):
            self.notice.emit("warning", tr("This computer is not signed in to that account. Sign in "
                                           "again, then delete it."))
            return
        if normalized == "SUBSCRIPTION_INACTIVE":
            self.notice.emit("warning", tr("Your subscription is not active. Open your TerraLab "
                                           "dashboard, then try again."))
            return
        self.notice.emit("warning", message or tr("Could not delete your account. Try again, or "
                                                  "delete it from terra-lab.ai."))

    def _on_key_revalidate_ok(self, usage: object) -> None:
        self._revalidate_task = None
        self._last_key_validation_unix = time.time()
        self._last_key_validation_at = time.monotonic()
        if self._state != self.ACTIVATED:
            self._set_state(self.ACTIVATED, "")
        self.usage_refreshed.emit(usage if isinstance(usage, dict) else {})

    def _on_key_revalidate_failed(self, message: str, code: str) -> None:
        self._revalidate_task = None
        normalized = (code or "").strip().upper()
        if normalized == "INVALID_KEY":
            self._settings.clear_activation_key()
            self._last_key_validation_unix = 0.0
            self._last_key_validation_at = float("-inf")
            expired = tr("Session expired. Sign in again to continue.")
            self._set_state(self.EXPIRED, expired)
            self.session_expired.emit(expired)
            log_warning("Stored key rejected on revalidation")
            return
        if normalized == "SUBSCRIPTION_INACTIVE":
            if not self._billing_warning_shown:
                self._billing_warning_shown = True
                self.notice.emit("warning", tr("There's a problem with your subscription. Open your "
                                               "TerraLab dashboard to update your payment method."))
            return
        if normalized in ("DEVICE_LIMIT_EXCEEDED", "DEVICE_LIMIT"):
            self.notice.emit("warning", tr("Your plan is already running on its maximum number of "
                                           "computers. Close AI Agent on one of them, then try again."))
            return
        if normalized in HARD_CONNECTIVITY_CODES:
            log_warning(f"Key revalidation skipped, no connection ({normalized})")
            return
        log_warning(f"Key revalidation failed ({normalized or 'unknown'})")
        self.notice.emit("warning", tr("Could not check your AI Agent account. If this lasts, "
                                       "sign out and sign in again."))



    def _clear_local_session(self) -> None:
        """Forget the key and every timer hanging off it, and say so."""





        self.cancel_pairing()
        task = self._revalidate_task
        if task is not None and task.is_active():
            try:
                task.succeeded.disconnect()
                task.failed.disconnect()
                task.cancel()
            except (TypeError, RuntimeError):
                pass
        self._revalidate_task = None
        for attr in ("_account_task", "_delete_task", "_login_link_task"):
            task = getattr(self, attr, None)
            if task is not None:
                try:
                    task.succeeded.disconnect()
                    task.failed.disconnect()
                except (TypeError, RuntimeError, AttributeError):
                    pass
                if getattr(task, "is_active", lambda: False)():
                    try:
                        task.cancel()
                    except (RuntimeError, AttributeError):
                        pass
            setattr(self, attr, None)
        self._settings.clear_activation_key()
        self._last_key_validation_unix = 0.0
        self._last_key_validation_at = float("-inf")
        self._billing_warning_shown = False
        self._set_state(self.SIGNED_OUT, "")
        self.signed_out.emit()

    def sign_out(self) -> None:
        self._clear_local_session()
        log("Signed out")

    def open_website(self, target: str, cta_source: str, fallback_url: str) -> None:
        """Open `target` signed in through a one-time login link, else the plain URL."""
        auth = self.get_auth_header()
        fallback_url = str(fallback_url or "")
        if not _openable_https(fallback_url):
            return
        if not auth:
            QDesktopServices.openUrl(QUrl(fallback_url))
            return
        if self._login_link_task is not None and self._login_link_task.is_active():
            return
        client = self._client
        locale = self._settings.locale

        def _open(result):




            url = result.get("url") if isinstance(result, dict) else None
            QDesktopServices.openUrl(QUrl(url if _openable_https(url) else fallback_url))

        def _done(*_args):
            self._login_link_task = None

        task = GenericRequestTask(tr("Opening your TerraLab account"),
                                  lambda: client.get_plugin_login_link(target, cta_source, auth=auth, locale=locale))
        task.succeeded.connect(_open)
        task.failed.connect(lambda _m, _c: QDesktopServices.openUrl(QUrl(fallback_url)))
        task.succeeded.connect(_done)
        task.failed.connect(_done)
        self._login_link_task = task
        QgsApplication.taskManager().addTask(task)

    def open_dashboard(self) -> None:
        self.open_website(f"/dashboard/{PRODUCT_ID}", "plugin_menu", get_dashboard_url())

    def open_plans(self, cta_source: str = "plugin_quota") -> None:
        self.open_website("/pricing", cta_source, get_plans_page_url(cta_source))

    def is_valid_key(self, key: str) -> bool:
        return bool(KEY_RE.match((key or "").strip()))
