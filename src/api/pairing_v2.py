# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




















from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any, Callable

from qgis.core import QgsApplication, QgsTask
from qgis.PyQt.QtCore import QCoreApplication, QObject, QTimer, pyqtSignal

from ..core.logger import log, log_warning
from ..core.settings import KEY_RE
from .terralab_client import PRODUCT_ID

CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # pragma: allowlist secret
FALLBACK_AFTER_MS = 15_000
CLAIM_TIMEOUT_MS = 10_000
START_FAILURES_FOR_LEGACY = 3
TTL_S = 1800
_RETRY_CAP_S = 10.0
_NO_STORE = [("Cache-Control", "no-store"), ("Referrer-Policy", "no-referrer")]
_GO_BACK_PAGE = (
    b'<!doctype html><html><head><meta charset="utf-8"><title>TerraLab</title></head>'
    b'<body style="font-family:sans-serif;text-align:center;padding-top:60px">'
    b"<h1>Go back to QGIS</h1></body></html>")


def tr(text: str) -> str:
    return QCoreApplication.translate("PairingV2", text)


def normalize_user_code(text: object) -> str | None:


    raw = "".join(str(text or "").split()).replace("-", "").upper()
    if len(raw) != 6 or any(char not in CODE_ALPHABET for char in raw):
        return None
    return raw


def _pause(seconds: float, is_canceled: Callable[[], bool]) -> None:
    end = time.monotonic() + max(0.0, min(seconds, _RETRY_CAP_S))
    while not is_canceled() and time.monotonic() < end:
        time.sleep(0.1)


def _retry_after(answer: dict, default: float) -> float:
    try:
        return float(answer.get("retry_after"))
    except (TypeError, ValueError):
        return default


def request_start(client, secret_hash: str, port: int | None, is_canceled) -> dict:


    failures = 0
    rate_retried = False
    while not is_canceled():
        answer, status = client.start_pairing_v2(secret_hash, port, TTL_S)
        if status == 200:
            code = str(answer.get("code") or "")
            url = str(answer.get("connect_url") or "")
            base = client.base_url.rstrip("/") + "/connect?"
            if 20 <= len(code) <= 64 and url.startswith(base):
                try:
                    expires = int(answer.get("expires_in") or TTL_S)
                except (TypeError, ValueError):
                    expires = TTL_S
                return {"code": code, "connect_url": url, "expires_in": max(60, min(expires, TTL_S))}
            failures += 1
        elif status == 400:
            return {"legacy": "start refused the request (400)"}
        elif status == 429 and not rate_retried:
            rate_retried = True
            _pause(_retry_after(answer, 5.0), is_canceled)
            continue
        else:
            failures += 1
        if failures >= START_FAILURES_FOR_LEGACY:
            return {"legacy": f"start failed {failures} times in a row (last HTTP {status})"}
        _pause(_retry_after(answer, 1.0 * failures) if status in (429, 503) else 1.0 * failures,
               is_canceled)
    return {"cancelled": True}


def request_claim(client, code: str, secret: str, is_canceled, grant: str = "",
                  user_code: str = "", attempts: int = 2) -> dict:


    status_text = "error"
    for attempt in range(max(1, attempts)):
        if is_canceled():
            return {"status": "cancelled_here"}
        answer, http = client.claim_pairing(code, secret, grant=grant, user_code=user_code,
                                            timeout_ms=CLAIM_TIMEOUT_MS)
        status_text = str(answer.get("status") or "")
        if http == 200 and status_text == "ready":
            key = str(answer.get("activation_key") or "").strip()
            return {"status": "ready", "key": key} if KEY_RE.match(key) else {"status": "error"}
        if http == 200 and status_text in ("pending", "no_plan", "cancelled"):
            return {"status": status_text}
        if http == 403 and status_text in ("invalid", "locked"):
            return {"status": status_text}
        if http == 404:
            return {"status": "not_found"}
        if http == 400:
            return {"status": "invalid_request"}

        if attempt + 1 < attempts:
            _pause(_retry_after(answer, 1.0), is_canceled)
    return {"status": "error"}


class _Task(QgsTask):


    done = pyqtSignal(object)

    def __init__(self, description: str, fn: Callable[[Any], dict]):
        flags = QgsTask.Flag.CanCancel
        for name in ("Hidden", "Silent"):
            extra = getattr(QgsTask.Flag, name, None)
            if extra is not None:
                flags = flags | extra
        super().__init__(description, flags)
        self._fn = fn
        self._result: dict = {}

    def run(self) -> bool:
        try:
            self._result = self._fn(self) or {}
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Sign-in request failed: {type(exc).__name__}")
            self._result = {"status": "error"}
        return True

    def finished(self, result: bool) -> None:
        try:
            if not self.isCanceled():
                self.done.emit(self._result)
        finally:

            self._fn = None
            self._result = {}


class PairingV2(QObject):


    started = pyqtSignal(str, int)
    legacy = pyqtSignal(str)
    code_needed = pyqtSignal()
    code_refused = pyqtSignal(str)
    claimed = pyqtSignal(str)
    failed = pyqtSignal(str, str)

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self._client = client
        self._secret = secrets.token_urlsafe(32)
        self._code = ""
        self._port: int | None = None
        self._live = True
        self._fallback_shown = False
        self._typed_busy = False
        self._tasks: list[_Task] = []



        self._claims_in_flight = 0

        self._waiting_sockets: list = []
        self._succeeded = False
        self._not_found_held = False
        self._fallback_timer = QTimer(self)
        self._fallback_timer.setSingleShot(True)
        self._fallback_timer.setInterval(FALLBACK_AFTER_MS)
        self._fallback_timer.timeout.connect(self._show_fallback)

    @property
    def code(self) -> str:
        return self._code

    @property
    def port(self) -> int | None:
        return self._port

    @property
    def fallback_shown(self) -> bool:
        return self._fallback_shown



    def begin(self) -> None:
        from ..core import report_bridge

        self._port = report_bridge.open_pairing_route(self._on_signed_in)
        secret_hash = hashlib.sha256(self._secret.encode("utf-8")).hexdigest()
        client, port = self._client, self._port
        log(f"Sign-in v2 starting ({'door open' if port else 'no door, code only'})")
        self._run(_Task(tr("Connecting AI Agent"),
                        lambda task: request_start(client, secret_hash, port, task.isCanceled)),
                  self._on_started)

    def _on_started(self, result: dict) -> None:
        if not self._live:
            return
        if result.get("legacy"):
            log_warning(f"Sign-in v2 unavailable, legacy sign-in: {result['legacy']}")
            self.end()
            self.legacy.emit(str(result["legacy"]))
            return
        if not result.get("code"):

            log_warning("Sign-in v2 start failed unexpectedly, legacy sign-in")
            self.end()
            self.legacy.emit("start failed unexpectedly")
            return
        self._code = str(result["code"])
        self.started.emit(str(result["connect_url"]), int(result["expires_in"]))
        if self._port is None:
            self._show_fallback()



    def on_confirmed(self) -> None:

        if self._live and not self._fallback_shown and not self._fallback_timer.isActive():
            self._fallback_timer.start()

    def _show_fallback(self) -> None:
        if self._live and not self._fallback_shown:
            self._fallback_shown = True
            log("Sign-in v2: asking for the code shown in the browser")
            self.code_needed.emit()



    def _on_signed_in(self, socket, query: dict) -> None:
        from ..core import report_bridge

        given = str(query.get("code") or "")
        grant = str(query.get("grant") or "")
        if (not self._live or not self._code
                or not hmac.compare_digest(given.encode("utf-8"), self._code.encode("utf-8"))):

            report_bridge.write_http(socket, "404 Not Found", _NO_STORE, b"")
            return
        if not grant or len(grant) > 512:
            self._go_back(socket)
            return
        client, code, secret = self._client, self._code, self._secret
        self._claims_in_flight += 1
        self._waiting_sockets.append(socket)
        self._run(_Task(tr("Connecting AI Agent"),
                        lambda task: request_claim(client, code, secret, task.isCanceled,
                                                   grant=grant, attempts=3)),
                  lambda result, s=socket: self._on_grant_claim(s, result))

    def _on_grant_claim(self, socket, result: dict) -> None:
        from ..core import report_bridge

        self._claims_in_flight -= 1
        if socket in self._waiting_sockets:
            self._waiting_sockets.remove(socket)
        status = result.get("status")
        log(f"Sign-in v2: browser claim answered {status}")
        if status == "ready" and self._live:
            done = f"{self._client.base_url.rstrip('/')}/connect/done?product={PRODUCT_ID}"
            report_bridge.write_http(socket, "302 Found", [("Location", done)] + _NO_STORE, b"")
            self._succeed(str(result.get("key") or ""))
            return
        self._go_back(socket)
        if self._live:
            self._settle(status)
            self._settle_held()

    @staticmethod
    def _go_back(socket) -> None:
        from ..core import report_bridge

        report_bridge.write_http(socket, "200 OK",
                                 [("Content-Type", "text/html; charset=utf-8")] + _NO_STORE, _GO_BACK_PAGE)



    def submit_code(self, text: str) -> None:
        if not self._live or not self._code or self._typed_busy:
            return
        user_code = normalize_user_code(text)
        if user_code is None:
            self.code_refused.emit(tr("Type the 6 characters shown in your browser, like K7P-4QX."))
            return
        self._typed_busy = True
        self._claims_in_flight += 1
        client, code, secret = self._client, self._code, self._secret
        self._run(_Task(tr("Connecting AI Agent"),
                        lambda task: request_claim(client, code, secret, task.isCanceled,
                                                   user_code=user_code, attempts=3)),
                  self._on_typed_claim)

    def _on_typed_claim(self, result: dict) -> None:
        self._typed_busy = False
        self._claims_in_flight -= 1
        if not self._live:
            return
        status = result.get("status")
        log(f"Sign-in v2: typed code answered {status}")
        if status == "ready":
            self._succeed(str(result.get("key") or ""))
        elif status == "invalid":
            self.code_refused.emit(tr("Wrong code. Check it and try again."))
        elif status == "pending":
            self.code_refused.emit(tr("Click Connect in your browser first, then type the code."))
        elif status in ("error", "invalid_request"):
            self.code_refused.emit(tr("Can't reach TerraLab. Try again."))
        else:
            self._settle(status)
        self._settle_held()



    def _settle(self, status: object) -> None:

        if status == "no_plan":
            self._fail(tr("This account has no active AI Agent plan. "
                          "Activate it on terra-lab.ai, then click Sign in again."), "NO_PLAN")
        elif status == "cancelled":
            self._fail(tr("Sign-in was cancelled in the browser. Click Sign in to try again."), "CANCELLED")
        elif status == "locked":
            self._fail(tr("Too many wrong codes: this sign-in is closed. Click Sign in to start again."),
                       "LOCKED")
        elif status == "not_found" and self._claims_in_flight > 0:
            self._not_found_held = True
        elif status == "not_found":
            self._fail(tr("This sign-in has ended. Click Sign in to start again."), "NOT_FOUND")

    def _settle_held(self) -> None:

        if self._live and self._not_found_held and self._claims_in_flight <= 0:
            self._settle("not_found")

    def _succeed(self, key: str) -> None:
        self._succeeded = True
        self.end()
        self.claimed.emit(key)

    def _fail(self, message: str, code: str) -> None:
        self.end()
        self.failed.emit(message, code)

    def end(self) -> None:


        if not self._live:
            return
        self._live = False
        self._fallback_timer.stop()
        from ..core import report_bridge


        sockets, self._waiting_sockets = self._waiting_sockets, []
        for socket in sockets:
            if self._succeeded:
                done = f"{self._client.base_url.rstrip('/')}/connect/done?product={PRODUCT_ID}"
                report_bridge.write_http(socket, "302 Found", [("Location", done)] + _NO_STORE, b"")
            else:
                self._go_back(socket)

        report_bridge.close_pairing_route(self._on_signed_in)
        for task in self._tasks:
            try:
                task.cancel()
            except RuntimeError:
                pass
        self._secret = ""  # nosec B105

    def _run(self, task: _Task, slot) -> None:
        self._tasks = [t for t in self._tasks if _alive(t)]
        self._tasks.append(task)
        task.done.connect(slot)
        QgsApplication.taskManager().addTask(task)


def _alive(task: _Task) -> bool:
    try:
        return task.status() not in (QgsTask.TaskStatus.Complete, QgsTask.TaskStatus.Terminated)
    except RuntimeError:
        return False
