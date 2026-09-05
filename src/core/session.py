# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Agent session: one WebSocket, hello, heartbeat, reconnection, typed frames."""












from __future__ import annotations

import random
from typing import Callable

from qgis.PyQt.QtCore import QCoreApplication, QObject, QTimer, pyqtSignal

from . import protocol, tuning
from .logger import log, log_warning
from .protocol import ProtocolError, ServerErrorCode
from .ws_client import WsClient

HEARTBEAT_MS = 30_000








RUN_HEARTBEAT_MS = 3_000




HELLO_TIMEOUT_MS = 20_000
MAX_MISSED_PONGS = 2
BACKOFF_S = (1, 2, 4, 8, 16, 30)



CANCEL_RESENDS = 3

MAX_PENDING_CANCELS = 16


_BAD_FRAME_MILESTONES = frozenset({10, 100, 1_000, 10_000})


def tr(text: str) -> str:
    return QCoreApplication.translate("AgentSession", text)


def _improve_enabled() -> bool:
    """The user's "help improve AI Agent" switch, read at connect time."""




    try:
        from .improve import is_improve_enabled

        return bool(is_improve_enabled())
    except Exception:  # noqa: BLE001 - a settings read must never stop a connection
        return False


def _telemetry_enabled() -> bool:
    """The user's "share usage statistics" switch, read at connect time."""






    try:
        from .telemetry import is_telemetry_enabled

        return bool(is_telemetry_enabled())
    except Exception:  # noqa: BLE001 - a settings read must never stop a connection
        return False


class AgentSession(QObject):
    session_started = pyqtSignal(object)
    token = pyqtSignal(str, str)
    plan = pyqtSignal(str, object)
    plan_update = pyqtSignal(str, str, str)
    tool_call = pyqtSignal(object)
    run_end = pyqtSignal(str, str, str, object, object)
    server_error = pyqtSignal(object)
    usage = pyqtSignal(object)
    thread_title = pyqtSignal(str, str)



    memory_note = pyqtSignal(str, str, str, str)
    status_line = pyqtSignal(str, str)
    state_changed = pyqtSignal(str, str)


    sources = pyqtSignal(str, object)
    counts = pyqtSignal(str, int, int)

    def __init__(self, settings, account, identity_provider: Callable[[], dict],
                 manifest_provider: Callable[[], tuple], parent=None):
        super().__init__(parent)
        self._settings = settings
        self._account = account
        self._identity_provider = identity_provider
        self._manifest_provider = manifest_provider
        self._ws = WsClient(self)
        self._ws.connected.connect(self._on_ws_connected)
        self._ws.disconnected.connect(self._on_ws_disconnected)
        self._ws.frame_received.connect(self._on_frame)
        self._ws.error_occurred.connect(self._on_ws_error)
        self._ws.connect_failed.connect(self._on_ws_failed)
        self._heartbeat = QTimer(self)
        self._heartbeat.setInterval(HEARTBEAT_MS)
        self._heartbeat.timeout.connect(self._on_heartbeat)
        self._reconnect = QTimer(self)
        self._reconnect.setSingleShot(True)
        self._reconnect.timeout.connect(self._reconnect_now)
        self._hello_deadline = QTimer(self)
        self._hello_deadline.setSingleShot(True)
        self._hello_deadline.setInterval(HELLO_TIMEOUT_MS)
        self._hello_deadline.timeout.connect(self._on_hello_timeout)
        self._state = "offline"
        self._session_id: str | None = None
        self._attempt = 0
        self._awaiting_pongs = 0
        self._user_closed = True
        self._auth_failed = False
        self._auth_message = ""
        self._manifest_included = False
        self._manifest_retry = False
        self._resume_asked = False
        self._resume_refused = False
        self._last_manifest_hash = ""
        self._model_label = ""
        self._last_failure: tuple[str, str, int] | None = None


        self._pending_cancels: dict[str, int] = {}
        self._bad_frames: dict[str, int] = {}



    @property
    def state(self) -> str:
        return self._state

    @property
    def is_online(self) -> bool:
        return self._state == "online"

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def model_label(self) -> str:
        """What the server said it would answer with, for a report to name."""
        return self._model_label

    def connect_to_server(self) -> None:
        self._user_closed = False
        self._auth_failed = False
        self._auth_message = ""
        self._attempt = 0
        self._last_failure = None
        self._reconnect.stop()
        self._open()

    def disconnect_from_server(self, wait: bool = False) -> None:
        """Non-blocking."""

        self._user_closed = True
        self._heartbeat.stop()
        self._hello_deadline.stop()
        self._reconnect.stop()
        self._pending_cancels.clear()
        self._ws.close(1000, "client closing", wait_ms=1500 if wait else 0)
        self._set_state("offline", "")

    def forget_session(self) -> None:
        self._session_id = None

    def set_run_open(self, open_: bool) -> None:
        """A run is open: ping every 3 s and give the link up after 9 s of silence."""

        interval = RUN_HEARTBEAT_MS if open_ else HEARTBEAT_MS
        if interval == self._heartbeat.interval():
            return
        self._heartbeat.setInterval(interval)
        if self._heartbeat.isActive():
            self._heartbeat.start()

    def send_user_message(self, run_id: str, thread_id: str, text: str, attachments: list,
                          context: dict, mode: str, approval: str, effort: str = "low") -> bool:
        return self._send(protocol.user_message(
            run_id, thread_id, text, attachments, context, mode, approval, effort))

    def send_tool_result(self, tool_call_id: str, run_id: str, result) -> bool:
        return self._send(protocol.tool_result(tool_call_id, run_id, result))

    def send_tool_error(self, tool_call_id: str, run_id: str, code: str, message: str, suggestion: str = "") -> bool:
        return self._send(protocol.tool_error(tool_call_id, run_id, code, message, suggestion))

    def send_permission_response(self, tool_call_id: str, run_id: str, decision: str) -> bool:
        return self._send(protocol.permission_response(tool_call_id, run_id, decision))

    def send_feedback(self, run_id: str, up: bool) -> bool:
        """The thumbs under an answer. Best effort: a thumb lost offline is not resent."""
        return self._send(protocol.feedback(run_id, bool(up)))

    def send_cancel(self, run_id: str) -> bool:
        """A cancel that cannot leave now (socket down) is sent on the next session, so a run stopped while offline does not keep running server side."""








        if run_id:
            self._pending_cancels[run_id] = CANCEL_RESENDS
            while len(self._pending_cancels) > MAX_PENDING_CANCELS:
                self._pending_cancels.pop(next(iter(self._pending_cancels)))
        return self._send(protocol.cancel(run_id))



    def _set_state(self, state: str, detail: str = "") -> None:
        if state != self._state or detail:
            self._state = state
            self.state_changed.emit(state, detail)

    def _open(self) -> None:
        if not self._account.has_activation_key:
            self._set_state("signed_out", "")
            return
        url = self._settings.server_url
        self._set_state("connecting", "")
        identity = self._identity_provider()
        headers = {"User-Agent": f"QGIS-AI-Agent/{identity.get('plugin_version', '?')}"}
        if not self._ws.open(url, headers):
            log_warning("WebSocket open refused: a connection is already in progress")
        elif self._ws.proxy_label:
            log(f"Connecting through the HTTP proxy {self._ws.proxy_label}")

    def _send(self, frame: dict) -> bool:
        """Queue a frame."""


        if not isinstance(frame, dict) or not isinstance(frame.get("type"), str):
            log_warning(f"Frame not encodable ({protocol.describe(frame) if isinstance(frame, dict) else frame})")
            return False
        sender = getattr(self._ws, "send_lazy", None)
        if sender is None:

            try:
                text = protocol.encode(frame)
            except (ProtocolError, TypeError, ValueError) as exc:
                log_warning(f"Frame not encodable ({protocol.describe(frame)}): {exc}")
                return False
            sent = self._ws.send_text(text)
        else:
            sent = sender(lambda: protocol.encode(frame))
        if not sent:
            log_warning(f"Frame dropped, socket not open: {protocol.describe(frame)}")
            return False
        return True

    def _on_ws_connected(self) -> None:
        """Send hello, then wait for `session` under a deadline of its own."""







        try:
            identity = self._identity_provider()
            manifest_hash, manifest = self._manifest_provider()
            include = self._manifest_retry or self._settings.known_manifest_hash != manifest_hash
            frame = protocol.hello(
                activation_key=self._account.activation_key,
                device_hash=self._account.device_hash,
                plugin_version=str(identity.get("plugin_version", "")),
                qgis_version=str(identity.get("qgis_version", "")),
                os_label=str(identity.get("os", "")),
                locale=str(identity.get("locale", self._settings.locale)),
                tool_manifest_hash=manifest_hash,
                tool_manifest=manifest if include else None,
                resume_session_id=self._session_id,
                telemetry=_telemetry_enabled(),
                improve=_improve_enabled(),
            )
        except Exception as exc:  # noqa: BLE001 - a half-open socket is worse than a retry
            log_warning(f"hello could not be built: {exc}")
            self._ws.close(1000, "hello failed")
            return
        self._last_manifest_hash = manifest_hash
        self._manifest_included = include
        self._resume_asked = bool(self._session_id)
        log(f"hello sent (manifest {'included' if include else 'by hash'}, "
            f"{'resume' if self._session_id else 'new'} session)")
        self._send(frame)
        self._hello_deadline.start()

    def _on_hello_timeout(self) -> None:
        """The server took the upgrade and never answered hello."""






        if self._state == "online" or self._user_closed:
            return
        log_warning(f"No session frame within {HELLO_TIMEOUT_MS // 1000}s of hello, reconnecting")
        self._ws.close(1000, "no session frame")

    def _on_ws_disconnected(self, code: int, reason: str) -> None:
        self._heartbeat.stop()
        self._hello_deadline.stop()
        self._awaiting_pongs = 0
        if self._user_closed:
            self._set_state("offline", "")
            return
        if self._auth_failed:



            self._set_state("signed_out", self._auth_message
                            or tr("Session expired. Sign in again to continue."))
            return
        log(f"WebSocket closed ({code}): {reason}")
        self._schedule_reconnect(code, reason)

    def _on_ws_error(self, message: str) -> None:
        """Raw transport text is for the log, never for the panel."""
        log_warning(f"WebSocket: {message}")

    def _on_ws_failed(self, kind: str, message: str, status: int) -> None:
        self._last_failure = (kind, message, int(status or 0))
        if kind == "http_status" and int(status or 0) in (401, 403):



            self._auth_failed = True
            self._auth_message = tr("Session expired. Sign in again to continue.")

    def _schedule_reconnect(self, code: int = 1006, reason: str = "") -> None:
        """Reconnect quietly."""






        base = BACKOFF_S[min(self._attempt, len(BACKOFF_S) - 1)]
        delay = base + random.uniform(0.0, 1.0)  # nosec B311 - reconnect jitter is not security relevant
        self._attempt += 1
        self._log_failure(code, reason)
        self._set_state("offline", "")
        self._reconnect.start(int(delay * 1000))

    def _log_failure(self, code: int = 1006, reason: str = "") -> None:
        """The cause of the drop, once, in the log only."""

        url = str(self._settings.server_url or "")
        cause = self._failure_text(code, reason)
        self._last_failure = None
        if "127.0.0.1" in url or "localhost" in url:
            log_warning(f"Local server {url} is not running, reconnecting")
        elif cause:
            log_warning(f"Reconnecting: {cause}")

    def _failure_text(self, code: int, reason: str) -> str:
        """The cause of the last drop as one translated sentence, or ""."""





        kind, _message, status = self._last_failure or ("", "", 0)
        if kind == "dns":
            return tr("The server name could not be resolved. Check your internet connection.")
        if kind == "timeout":
            return tr("The server did not answer in time.")
        if kind == "proxy_auth":
            return tr("The proxy asks for a login. Set the proxy user and password in QGIS "
                      "(Settings > Options > Network).")
        if kind == "proxy_refused":
            return tr("The proxy refused the connection to the agent service.")
        if kind == "proxy_unreachable":
            return tr("The proxy set in QGIS (Settings > Options > Network) cannot be reached.")
        if kind == "tls":
            return tr("The server certificate could not be verified. If your network inspects secure "
                      "traffic, add its certificate in QGIS (Settings > Options > Authentication).")
        if kind == "redirect":
            return tr("The server redirected the connection (HTTP {code}). Check the server URL in the "
                      "plugin settings.").format(code=status)
        if kind == "url":
            return tr("The server URL in the plugin settings is not valid.")
        if kind == "http_status":
            if status in (502, 503, 504):
                return tr("The agent service is unavailable right now (HTTP {code}).").format(code=status)
            return tr("A gateway blocked the connection (HTTP {code}). If this network shows a sign-in "
                      "page, open it in your browser first.").format(code=status)
        if code in (1008, 1011) or 4000 <= code < 5000:
            return tr("The server closed the connection ({code}).").format(code=code)
        return ""

    def _reconnect_now(self) -> None:
        if not self._user_closed:
            self._open()

    def _on_heartbeat(self) -> None:
        if not self._ws.is_open:
            return
        if self._awaiting_pongs >= MAX_MISSED_PONGS:
            log_warning("Two pongs missed, closing the socket to reconnect")
            self._heartbeat.stop()


            self._set_state("offline", "")
            self._ws.close(1001, "heartbeat lost")
            return
        self._awaiting_pongs += 1
        self._send(protocol.ping())



    def _on_frame(self, text: str) -> None:
        try:
            frame = protocol.decode(text)
        except ProtocolError as exc:
            self._note_bad_frame("invalid", str(exc))
            return
        kind = frame.get("type")
        handler = getattr(self, f"_on_{kind}", None) if protocol.is_server_type(kind) else None
        if handler is None:
            self._note_bad_frame("unknown type", str(kind)[:60])
            return

        self._awaiting_pongs = 0
        try:
            handler(frame)
        except Exception as exc:  # noqa: BLE001
            self._note_bad_frame(f"handler {kind}", str(exc))

    def _note_bad_frame(self, category: str, detail: str) -> None:
        """One line for the first of a kind, then a count."""






        seen = self._bad_frames.get(category)
        if seen is None:
            self._bad_frames[category] = 1
            log_warning(f"Frame dropped ({category}): {detail[:200]}")
            return
        self._bad_frames[category] = seen + 1
        if seen + 1 in _BAD_FRAME_MILESTONES:
            log_warning(f"Frame dropped ({category}): {seen + 1} so far this session")

    def _on_session(self, frame: dict) -> None:
        self._hello_deadline.stop()




        tuning.apply(frame.get("policy"))
        self._session_id = str(frame.get("session_id") or self._session_id or "") or None
        self._attempt = 0
        if self._resume_asked and frame.get("resumed") is False:




            self._resume_refused = True
        known = bool(frame.get("manifest_known"))
        if known:
            self._settings.known_manifest_hash = self._last_manifest_hash
        elif not self._manifest_included:
            log("Server does not hold the tool manifest, reconnecting with the full catalog")
            self._settings.known_manifest_hash = ""
            self._manifest_retry = True
            self._ws.close(1000, "resend manifest")
            return
        self._manifest_retry = False
        self._awaiting_pongs = 0
        self._last_failure = None
        self._heartbeat.start()
        self._model_label = str(frame.get("model_label") or "")
        self._set_state("online", self._model_label)
        for run_id in sorted(self._pending_cancels):
            self._send(protocol.cancel(run_id))
            left = self._pending_cancels.get(run_id, 0) - 1
            if left > 0:
                self._pending_cancels[run_id] = left
            else:
                self._pending_cancels.pop(run_id, None)
        if self._resume_refused:
            frame = dict(frame, resumed=False)
            self._resume_refused = False
        self.session_started.emit(frame)

    def _on_policy(self, frame: dict) -> None:
        """The server changed the numbers, without waiting for a reconnect."""





        tuning.apply(frame.get("policy"))

    def _on_token(self, frame: dict) -> None:
        self.token.emit(str(frame.get("run_id") or ""), str(frame.get("text") or ""))

    def _on_plan(self, frame: dict) -> None:
        steps = frame.get("steps") if isinstance(frame.get("steps"), list) else []
        self.plan.emit(str(frame.get("run_id") or ""), steps)

    def _on_plan_update(self, frame: dict) -> None:
        self.plan_update.emit(str(frame.get("run_id") or ""), str(frame.get("step_id") or ""),
                              str(frame.get("state") or ""))

    def _on_tool_call(self, frame: dict) -> None:
        self.tool_call.emit(frame)

    def _on_run_end(self, frame: dict) -> None:

        self._pending_cancels.pop(str(frame.get("run_id") or ""), None)
        usage = frame.get("usage") if isinstance(frame.get("usage"), dict) else {}
        verification = frame.get("verification") if isinstance(frame.get("verification"), dict) else None
        self.run_end.emit(str(frame.get("run_id") or ""), str(frame.get("status") or "done"),
                          str(frame.get("summary") or ""), usage, verification)

    def _on_status_line(self, frame: dict) -> None:
        self.status_line.emit(str(frame.get("run_id") or ""), str(frame.get("text") or ""))

    def _on_error(self, frame: dict) -> None:
        code = str(frame.get("code") or ServerErrorCode.INTERNAL)
        if code == ServerErrorCode.AUTH_FAILED:
            self._auth_failed = True
            self._auth_message = str(frame.get("message") or "")
            self._heartbeat.stop()
            self._set_state("signed_out", str(frame.get("message") or ""))
            self._ws.close(1000, "auth failed")
        self.server_error.emit(frame)

    def _on_usage(self, frame: dict) -> None:
        self.usage.emit(frame)

    def _on_pong(self, frame: dict) -> None:
        self._awaiting_pongs = 0

    def _on_thread_title(self, frame: dict) -> None:
        self.thread_title.emit(str(frame.get("thread_id") or ""), str(frame.get("title") or ""))

    def _on_memory_note(self, frame: dict) -> None:
        self.memory_note.emit(str(frame.get("text") or ""), str(frame.get("kind") or ""),
                              str(frame.get("scope") or ""), str(frame.get("run_id") or ""))

    def _on_sources(self, frame: dict) -> None:
        raw = frame.get("items") if isinstance(frame.get("items"), list) else []
        items = [i for i in raw if isinstance(i, dict) and i.get("name")]
        self.sources.emit(str(frame.get("run_id") or ""), items)

    def _on_counts(self, frame: dict) -> None:
        try:
            self.counts.emit(str(frame.get("run_id") or ""), int(frame.get("tool_calls") or 0),
                             int(frame.get("messages") or 0))
        except (TypeError, ValueError):
            pass
