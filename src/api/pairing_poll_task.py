# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""QgsTask that polls the website until a browser pairing code is bound to a key."""







from __future__ import annotations

import time

from qgis.core import QgsTask
from qgis.PyQt.QtCore import QCoreApplication, pyqtSignal

from ..core.logger import log
from ..core.settings import KEY_RE as ACTIVATION_KEY_RE
from .terralab_client import HARD_CONNECTIVITY_CODES


def tr(text: str) -> str:
    return QCoreApplication.translate("PairingPollTask", text)


class PairingPollTask(QgsTask):
    pairing_succeeded = pyqtSignal(str)
    pairing_failed = pyqtSignal(str, str)
    pairing_timeout = pyqtSignal()


    pairing_browser_seen = pyqtSignal()

    pairing_stalled = pyqtSignal(str)

    STALL_BROWSER_NOT_SEEN = "browser_not_seen"
    STALL_CODE_EXPIRED = "code_expired"
    STALL_AFTER_S = 45.0

    CODE_TTL_S = 600.0
    EXPIRY_HINT_LEAD_S = 30.0






    DEFAULT_TIMEOUT_S = CODE_TTL_S

    OFFLINE_STREAK = 4

    def __init__(self, client, code: str, interval_s: float = 2.0, total_timeout_s: float | None = None):
        super().__init__(tr("Connecting AI Agent"), QgsTask.Flag.CanCancel)
        self._client = client
        self._code = code
        self._interval_s = interval_s
        self._total_timeout_s = self.DEFAULT_TIMEOUT_S if total_timeout_s is None else total_timeout_s
        self._key: str | None = None
        self._failure: tuple[str, str] | None = None
        self._timed_out = False

    @property
    def pairing_code(self) -> str:
        return self._code

    def is_active(self) -> bool:
        try:
            return self.status() in (QgsTask.TaskStatus.Running, QgsTask.TaskStatus.Queued,
                                     QgsTask.TaskStatus.OnHold)
        except Exception:
            return False

    @staticmethod
    def _unexpected_failure() -> tuple[str, str]:
        return (tr("Sign-in failed unexpectedly. Click Sign in to try again."), "POLL_ERROR")

    def run(self) -> bool:
        try:
            return self._run_poll()
        except Exception as err:  # noqa: BLE001
            if self.isCanceled():
                return False
            log(f"Pairing poll failed unexpectedly: {err}")
            self._failure = self._unexpected_failure()
            return False

    def _run_poll(self) -> bool:
        started = time.monotonic()
        deadline = started + self._total_timeout_s
        browser_seen = False
        stall_hinted = False
        expiry_hinted = False
        last_logged_detail = ""
        offline_streak = 0
        while not self.isCanceled() and time.monotonic() < deadline:
            try:
                result = self._client.poll_pairing(self._code)
            except Exception:
                result = {"error": "poll failed", "code": "NO_INTERNET"}
            if self.isCanceled():
                return False

            status = result.get("status") if isinstance(result, dict) else None
            error_code = ""
            if status is None and isinstance(result, dict):
                error_code = str(result.get("code") or "").strip().upper()
            if error_code in HARD_CONNECTIVITY_CODES:
                offline_streak += 1
                if offline_streak >= self.OFFLINE_STREAK:
                    self._failure = (
                        tr("No connection to the sign-in service. Check your internet "
                           "connection, then click Sign in to try again."),
                        "NO_INTERNET")
                    return False
            else:
                offline_streak = 0

            if status == "ready":
                raw_key = result.get("activation_key")
                key = raw_key.strip() if isinstance(raw_key, str) else ""
                if ACTIVATION_KEY_RE.match(key):
                    self._key = key
                    return True
                self._failure = (tr("Unexpected response from the server. Please try again."), "BAD_KEY")
                return False
            if status == "no_plan":
                self._failure = (
                    tr("This account has no active AI Agent plan. "
                       "Activate it on terra-lab.ai, then click Sign in again."),
                    "NO_PLAN")
                return False
            if status == "cancelled":
                self._failure = (tr("Sign-in was cancelled in the browser. Click Sign in to try again."),
                                 "CANCELLED")
                return False


            waited_s = time.monotonic() - started
            if status == "pending" and not browser_seen:
                browser_seen = True
                self.pairing_browser_seen.emit()
            elif not browser_seen and not stall_hinted and waited_s >= self.STALL_AFTER_S:
                stall_hinted = True
                self.pairing_stalled.emit(self.STALL_BROWSER_NOT_SEEN)
            elif browser_seen and not expiry_hinted and waited_s >= self.CODE_TTL_S - self.EXPIRY_HINT_LEAD_S:
                expiry_hinted = True
                self.pairing_stalled.emit(self.STALL_CODE_EXPIRED)

            sleep_s = self._interval_s
            hint = result.get("retry_after") if isinstance(result, dict) else None
            if hint is not None:
                try:
                    sleep_s = min(max(float(hint), 1.0), 15.0)
                except (TypeError, ValueError):
                    pass
            detail = status or (result.get("code") if isinstance(result, dict) else None) or "unknown"
            if detail != last_logged_detail:
                last_logged_detail = detail
                log(f"Pairing poll: waiting ({detail})")
            self._sleep_cancellable(sleep_s)

        if self.isCanceled():
            return False
        self._timed_out = True
        return False

    def _sleep_cancellable(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.isCanceled():
                return
            time.sleep(0.25)

    def finished(self, result: bool) -> None:
        """Emit the outcome once, then drop what this task was holding."""







        try:
            if self.isCanceled() and not self._key:
                return
            if result and self._key:
                self.pairing_succeeded.emit(self._key)
            elif self._timed_out:
                self.pairing_timeout.emit()
            elif self._failure is not None:
                self.pairing_failed.emit(*self._failure)
            else:
                self.pairing_failed.emit(*self._unexpected_failure())
        finally:
            self._key = None
            self._code = ""
            self._client = None
