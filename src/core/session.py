# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later















from __future__ import annotations

import base64
import json
import math
import os
import random
import tempfile
import threading
import time
import uuid
import zlib
from typing import Callable
from urllib.parse import urlsplit

from qgis.PyQt.QtCore import QCoreApplication, QObject, QTimer, pyqtSignal

from . import crash_note, data_access, protocol, tuning
from .logger import log, log_warning
from .protocol import ProtocolError, ServerErrorCode
from .ws_client import _CONNECT_TIMEOUT_S, _SEND_TIMEOUT_S, DEAD_AFTER_S, IDLE_PING_S, WsClient





HEARTBEAT_MS = 30_000















RUN_HEARTBEAT_MS = 3_000




HELLO_TIMEOUT_MS = 20_000










HELLO_UPLOAD_CAP_S = 120.0
HELLO_UPLOAD_RECHECK_MS = 5_000



_CALL_CLOCKS_KEPT = 256
MAX_MISSED_PONGS = 2





KNOWN_MANIFESTS_KEPT = 8





LINK_SILENT_S = 15.0
BACKOFF_S = (1, 2, 4, 8, 16, 30)




RESTART_CODES = frozenset({1012, 1013})







CAUSE_AFTER_S = 30.0




USER_SIDE_FAILURES = frozenset({"dns", "proxy_auth", "proxy_refused", "proxy_unreachable", "tls",
                                "redirect", "url"})



INTERNET_DOWN_FAILURES = frozenset({"dns", "no_route"})
RESTART_QUICK_TRIES = 3
RESTART_JITTER_S = 0.3



SAVED_SESSION_MAX_AGE_S = 24 * 3600



CANCEL_RESENDS = 3

MAX_PENDING_CANCELS = 16




QUIT_FLUSH_MS = 1_000


_BAD_FRAME_MILESTONES = frozenset({10, 100, 1_000, 10_000})







_CATALOG_FILE = "session-catalogs.z"
_CATALOG_MAGIC = b"TLCAT1 "


def _catalog_path() -> str:
    from .policy import state_dir

    return os.path.join(state_dir(), _CATALOG_FILE)


def _held_catalog_tag() -> str:

    try:
        with open(_catalog_path(), "rb") as handle:
            head = handle.readline(128)
    except OSError:
        return ""
    if not head.startswith(_CATALOG_MAGIC):
        return ""
    tag = head[len(_CATALOG_MAGIC):].strip().decode("ascii", "replace")
    return tag if tag.isalnum() and len(tag) <= 64 else ""


def _held_catalogs(tag: str) -> dict | None:

    try:
        with open(_catalog_path(), "rb") as handle:
            data = handle.read()
        head, _, body = data.partition(b"\n")
        if head != _CATALOG_MAGIC + tag.encode("ascii"):
            return None
        fields = json.loads(zlib.decompress(body).decode("utf-8"))
    except (OSError, ValueError, UnicodeError, zlib.error) as exc:
        log_warning(f"Catalogs on disk not read: {exc}")
        return None
    if not isinstance(fields, dict):
        return None
    return {key: fields[key] for key in protocol.CATALOG_FIELDS if key in fields}


def _store_catalogs(tag: str, fields: dict) -> threading.Thread | None:






    try:
        text = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        log_warning(f"Catalogs not stored: {exc}")
        return None

    def write() -> None:
        from .host_platform import remove_quietly, retry_file_op

        tmp = ""
        try:
            path = _catalog_path()
            data = _CATALOG_MAGIC + tag.encode("ascii") + b"\n" + zlib.compress(text.encode("utf-8"), 6)
            fd, tmp = tempfile.mkstemp(prefix=".agent-", suffix=".tmp", dir=os.path.dirname(path))
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            retry_file_op(os.replace, tmp, path)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Catalogs not stored: {exc}")
        finally:
            if tmp:
                remove_quietly(tmp)

    writer = threading.Thread(target=write, name="ai-agent-catalogs", daemon=True)
    writer.start()
    return writer


def _host_of(url: str) -> str:

    try:
        return urlsplit(str(url or "")).hostname or "?"
    except ValueError:
        return "?"


def socket_clocks() -> dict:





    return tuning.socket_clocks({
        "heartbeat_s": HEARTBEAT_MS / 1000,
        "run_heartbeat_s": RUN_HEARTBEAT_MS / 1000,
        "hello_timeout_s": HELLO_TIMEOUT_MS / 1000,
        "backoff_min_s": float(BACKOFF_S[0]),
        "backoff_max_s": float(BACKOFF_S[-1]),
        "connect_timeout_s": _CONNECT_TIMEOUT_S,
        "send_timeout_s": _SEND_TIMEOUT_S,
        "idle_ping_s": IDLE_PING_S,
        "dead_after_s": DEAD_AFTER_S,
    })


def tr(text: str) -> str:
    return QCoreApplication.translate("AgentSession", text)


def _improve_enabled() -> bool:





    try:
        from .improve import is_improve_enabled

        return bool(is_improve_enabled())
    except Exception:  # noqa: BLE001
        return False


def _telemetry_enabled() -> bool:







    try:
        from .telemetry import is_telemetry_enabled

        return bool(is_telemetry_enabled())
    except Exception:  # noqa: BLE001
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



    memory_note = pyqtSignal(str, str, str, str, str, str, str)
    status_line = pyqtSignal(str, str)
    state_changed = pyqtSignal(str, str)


    sources = pyqtSignal(str, object)

    server_tool = pyqtSignal(object)
    counts = pyqtSignal(str, int, int)
    steer_ack = pyqtSignal(str, str, bool)


    connection_failed = pyqtSignal(object)

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
        self._run_open = False
        self._heartbeat = QTimer(self)
        self._heartbeat.setInterval(int(socket_clocks()["heartbeat_s"] * 1000))
        self._heartbeat.timeout.connect(self._on_heartbeat)
        self._reconnect = QTimer(self)
        self._reconnect.setSingleShot(True)
        self._reconnect.timeout.connect(self._reconnect_now)
        self._hello_deadline = QTimer(self)
        self._hello_deadline.setSingleShot(True)
        self._hello_deadline.setInterval(int(socket_clocks()["hello_timeout_s"] * 1000))
        self._hello_deadline.timeout.connect(self._on_hello_timeout)
        self._state = "offline"
        self._session_id: str | None = None


        self._call_clocks: dict[str, tuple[float, float]] = {}



        self._crash_note: dict | None = None


        self._threads_told: set = set()
        self._hello_sent_at = 0.0
        self._hello_checked_at = 0.0
        self._attempt = 0
        self._awaiting_pongs = 0
        self._beat_at = 0.0
        self._sent_at = 0.0
        self._user_closed = True
        self._auth_failed = False
        self._auth_message = ""
        self._update_required = ""
        self._manifest_included = False
        self._manifest_retry = False
        self._resume_asked = False
        self._resume_refused = False
        self._last_manifest_hash = ""


        self._catalog_tag = ""
        self._disk_tag: str | None = None
        self._applied_tag = ""
        self._catalog_writer: threading.Thread | None = None
        self._catalogs_came = False
        self._connect_facts: dict = {}
        self._model_label = ""
        self._last_failure: tuple[str, str, int] | None = None
        self._reached_session = False


        self._last_drop: dict | None = None
        self._dropped_at = 0.0
        self._failed_streak = 0
        self._failing_since = 0.0
        self._url_failures = 0


        self._link_cause = "server"


        self._pending_cancels: dict[str, int] = {}

        self._cancel_reasons: dict[str, str] = {}
        self._bad_frames: dict[str, int] = {}



        self._last_seq = 0
        self._replays_dropped = 0



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
    def connect_facts(self) -> dict:


        return dict(self._connect_facts)

    @property
    def model_label(self) -> str:

        return self._model_label

    @property
    def closed_on_purpose(self) -> bool:

        return self._user_closed

    def connect_to_server(self) -> None:
        if not self._session_id:
            self._claim_saved_session()
        self._user_closed = False
        self._auth_failed = False
        self._auth_message = ""
        self._update_required = ""
        self._attempt = 0
        self._last_failure = None
        self._reconnect.stop()
        self._open()

    def disconnect_from_server(self, wait: bool = False) -> None:



        self._user_closed = True
        self._heartbeat.stop()
        self._hello_deadline.stop()
        self._reconnect.stop()
        if wait and self._pending_cancels:
            self._flush_before_close()
        if wait:
            self._save_session()
        self._pending_cancels.clear()
        self._cancel_reasons.clear()
        self._ws.close(1000, "client closing", wait_ms=1500 if wait else 0)
        self._set_state("offline", "")

    def _flush_before_close(self) -> None:

        flush = getattr(self._ws, "flush", None)
        if flush is None or not self._ws.is_open:
            return
        started = time.monotonic()
        try:
            written = flush(QUIT_FLUSH_MS)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Cancel not written before closing: {exc}")
            return
        took = int((time.monotonic() - started) * 1000)
        if written:
            log(f"Cancel written before closing ({took} ms)")
        else:
            log_warning(f"Closing with a cancel still queued after {took} ms; the server may finish the run")

    def forget_session(self) -> None:
        self._session_id = None
        self._last_seq = 0
        self._write_saved("")

















    def _key_tag(self) -> str:
        import hashlib

        key = str(getattr(self._account, "activation_key", "") or "")
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12] if key else ""

    @staticmethod
    def _profile_tag() -> str:

        import hashlib
        import os

        try:
            from qgis.core import QgsApplication

            folder = str(QgsApplication.qgisSettingsDirPath() or "")
        except Exception:  # noqa: BLE001
            folder = ""
        if not folder:
            return ""
        folder = os.path.normcase(os.path.normpath(os.path.abspath(folder)))
        return hashlib.sha256(folder.encode("utf-8")).hexdigest()[:12]

    def _write_saved(self, value: str) -> None:
        try:
            if hasattr(type(self._settings), "saved_session"):
                self._settings.saved_session = value
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Session id not stored: {exc}")

    def _save_session(self) -> None:
        tag = self._key_tag()
        if not (self._session_id and tag):
            return
        self._write_saved(
            f"{tag}|{self._session_id}|{int(self._last_seq)}|{int(time.time())}|{self._profile_tag()}")

    def _claim_saved_session(self) -> None:
        try:
            saved = str(getattr(self._settings, "saved_session", "") or "") \
                if hasattr(type(self._settings), "saved_session") else ""
        except Exception:  # noqa: BLE001
            saved = ""
        if not saved:
            return
        self._write_saved("")
        parts = saved.split("|")
        if len(parts) != 5 or not parts[0] or parts[0] != self._key_tag():
            return
        if parts[4] != self._profile_tag():
            log("Saved session left by another QGIS profile; opening a new one")
            return
        try:
            last_seq, saved_at = int(parts[2]), int(parts[3])
        except ValueError:
            return
        if not (0 <= time.time() - saved_at <= SAVED_SESSION_MAX_AGE_S):
            return
        session_id = parts[1]
        if not session_id or len(session_id) > 128 or not all(c.isalnum() or c in "._-" for c in session_id):
            return
        self._session_id = session_id
        self._last_seq = max(0, last_seq)
        log(f"Resuming the session this profile left ({session_id[:8]})")

    def set_run_open(self, open_: bool) -> None:


        self._run_open = bool(open_)
        self._apply_heartbeat()

    def _apply_heartbeat(self) -> None:

        clocks = socket_clocks()
        interval = int((clocks["run_heartbeat_s"] if self._run_open else clocks["heartbeat_s"]) * 1000)
        if interval == self._heartbeat.interval():
            return
        self._heartbeat.setInterval(interval)
        if self._heartbeat.isActive():
            self._heartbeat.start()

    def still_sending(self) -> bool:






        probe = getattr(self._ws, "link_moved_since", None)
        if probe is None or not self._ws.is_open:
            return False
        try:
            return bool(probe(time.monotonic() - socket_clocks()["run_heartbeat_s"])[1])
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Socket activity unreadable: {exc}")
            return False

    def send_user_message(self, run_id: str, thread_id: str, text: str, attachments: list,
                          context: dict, mode: str, approval: str, effort: str = "low",
                          example: str = "", replaces_run_id: str = "",
                          earlier_turns: Callable[[], list] | None = None) -> bool:













        attachments = [self._upload_pieces(att) for att in (attachments or [])]

        crash_note.record_run(run_id, self._session_id or "")
        turns = earlier_turns() if earlier_turns is not None and thread_id not in self._threads_told else None
        sent = self._send(protocol.user_message(
            run_id, thread_id, text, attachments, context, mode, approval, effort, example,
            replaces_run_id if self.edit_last_available else "", turns))
        if sent:
            self._threads_told.add(thread_id)
        return sent

    def _upload_pieces(self, att) -> object:
        if not isinstance(att, dict) or att.get("kind") != "document" or att.get("data_base64"):
            return att
        path = str(att.get("path") or "")
        try:
            size = os.path.getsize(path)
        except OSError:
            return att
        chunk = protocol.UPLOAD_CHUNK_BYTES
        total = max(1, (size + chunk - 1) // chunk)
        upload_id = uuid.uuid4().hex
        name = str(att.get("name") or os.path.basename(path))
        for seq in range(total):
            self._send_produced(_piece_producer(upload_id, name, seq, total, path, seq * chunk, chunk))
        out = dict(att)
        out["upload_id"] = upload_id
        out["size"] = size



        from ..tools.csv_loader import _ansi_encoding

        out["code_page"] = _ansi_encoding()
        return out

    def send_tool_result(self, tool_call_id: str, run_id: str, result, code_class: str = "") -> bool:
        return self._send(protocol.tool_result(tool_call_id, run_id, result, code_class),
                          self._answer_clock(tool_call_id))

    def send_tool_error(self, tool_call_id: str, run_id: str, code: str, message: str, suggestion: str = "",
                        code_class: str = "", detail: str = "") -> bool:
        return self._send(protocol.tool_error(tool_call_id, run_id, code, message, suggestion, code_class, detail),
                          self._answer_clock(tool_call_id))

    def _answer_clock(self, tool_call_id: str) -> tuple | None:

        clock = self._call_clocks.pop(str(tool_call_id or ""), None)
        return (*clock, time.perf_counter()) if clock is not None else None

    def send_permission_response(self, tool_call_id: str, run_id: str, decision: str) -> bool:
        return self._send(protocol.permission_response(tool_call_id, run_id, decision))

    def send_busy(self, seconds: float, where: str, calls: str, ended: bool = False) -> bool:


        return self._send(protocol.busy(seconds, where, calls, ended))

    @property
    def steer_available(self) -> bool:

        return bool(getattr(self, "_steer", False)) and self.is_online

    @property
    def edit_last_available(self) -> bool:

        return bool(getattr(self, "_edit_last", False)) and self.is_online

    @property
    def unsteer_available(self) -> bool:

        return bool(getattr(self, "_unsteer", False)) and self.is_online

    def effort_group(self, effort: str) -> tuple:


        if not self.is_online:
            return ()
        return next((g for g in getattr(self, "_effort_switch", ()) if effort in g), ())

    def send_effort(self, run_id: str, effort: str) -> bool:
        return self._send(protocol.effort_switch(run_id, effort))

    def send_unsteer(self, run_id: str, steer_id: str) -> bool:
        return self._send(protocol.unsteer(run_id, steer_id))

    def send_steer(self, run_id: str, steer_id: str, text: str) -> bool:
        return self._send(protocol.steer(run_id, steer_id, text))

    def send_cancel(self, run_id: str, reason: str = "") -> bool:











        if run_id:
            self._pending_cancels[run_id] = CANCEL_RESENDS
            if reason:
                self._cancel_reasons[run_id] = reason
            while len(self._pending_cancels) > MAX_PENDING_CANCELS:
                oldest = next(iter(self._pending_cancels))
                self._pending_cancels.pop(oldest)
                self._cancel_reasons.pop(oldest, None)
        return self._send(protocol.cancel(run_id, reason))



    def _set_state(self, state: str, detail: str = "") -> None:
        if state != self._state or detail:
            self._state = state
            self.state_changed.emit(state, detail)

    def _open(self) -> None:
        if not self._account.has_activation_key:
            self._set_state("signed_out", "")
            return
        url = self._pick_server_url()
        self._set_state("connecting", "")
        self._reached_session = False
        identity = self._identity_provider()
        headers = {"User-Agent": f"QGIS-AI-Agent/{identity.get('plugin_version', '?')}"}
        clocks = socket_clocks()
        transport = {name: clocks[name] for name in ("connect_timeout_s", "send_timeout_s", "idle_ping_s",
                                                     "dead_after_s")}
        if not self._ws.open(url, headers, clocks=transport):
            log_warning("WebSocket open refused: a connection is already in progress")
        elif self._ws.proxy_label:
            log(f"Connecting through the HTTP proxy {self._ws.proxy_label}")

    def _pick_server_url(self) -> str:








        try:
            from .settings import (
                ALTERNATE_AFTER_FAILURES,
                DEFAULT_SERVER_URL,
                alternate_server_url,
                server_url_choices,
                use_server_url,
            )
        except Exception:  # noqa: BLE001
            return self._settings.server_url
        configured = getattr(self._settings, "configured_server_url", None) or self._settings.server_url
        if configured != DEFAULT_SERVER_URL:
            return configured
        current = alternate_server_url() or DEFAULT_SERVER_URL
        if self._url_failures >= ALTERNATE_AFTER_FAILURES:
            self._url_failures = 0
            choices = server_url_choices()
            following = choices[(choices.index(current) + 1) % len(choices)]
            if following != current:
                log_warning(f"{ALTERNATE_AFTER_FAILURES} connections to {_host_of(current)} failed, "
                            f"trying {_host_of(following)}")
                use_server_url(following)
                current = following
        return current

    def _send_produced(self, produce: Callable[[], str]) -> bool:


        sender = getattr(self._ws, "send_lazy", None)
        if sender is None:
            try:
                sent = self._ws.send_text(produce())
            except (ProtocolError, TypeError, ValueError, OSError) as exc:
                log_warning(f"Frame not encodable (upload): {exc}")
                return False
        else:
            sent = sender(produce)
        if not sent:
            log_warning("Frame dropped, socket not open: upload")
        else:
            self._sent_at = time.monotonic()
        return bool(sent)

    def _send(self, frame: dict, clock: tuple | None = None) -> bool:





        if not isinstance(frame, dict) or not isinstance(frame.get("type"), str):
            log_warning(f"Frame not encodable ({protocol.describe(frame) if isinstance(frame, dict) else frame})")
            return False
        if clock is not None:
            arrived, started, answered = clock


            phases = protocol.take_phases(frame.get("tool_call_id"))

            def produce() -> str:
                return protocol.encode(dict(frame, timing=protocol.call_timing(
                    arrived, started, answered, time.perf_counter(), phases)))
        else:
            def produce() -> str:
                return protocol.encode(frame)
        sender = getattr(self._ws, "send_lazy", None)
        if sender is None:

            try:
                text = produce()
            except (ProtocolError, TypeError, ValueError) as exc:
                log_warning(f"Frame not encodable ({protocol.describe(frame)}): {exc}")
                return False
            sent = self._ws.send_text(text)
        else:
            sent = sender(produce)
        if not sent:
            log_warning(f"Frame dropped, socket not open: {protocol.describe(frame)}")
            return False
        self._sent_at = time.monotonic()
        return True

    def _on_ws_connected(self) -> None:








        if self._user_closed:


            self._ws.close(1000, "client closing")
            return
        try:
            identity = self._identity_provider()
            manifest_hash, manifest = self._manifest_provider()
            include = self._manifest_retry or manifest_hash not in self._known_manifests()
            last_seq = self._last_seq if self._session_id else 0


            if self._crash_note is None:
                self._crash_note = crash_note.take(str(identity.get("plugin_version", ""))) or {}
            if self._disk_tag is None:
                self._disk_tag = _held_catalog_tag()
                self._catalog_tag = self._catalog_tag or self._disk_tag
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
                last_seq=last_seq,
                python_version=str(identity.get("python_version", "")),
                libraries=list(identity.get("libraries") or ()),
                crash=self._crash_note or None,
                catalog_tag=self._catalog_tag,
                last_drop=self._drop_report() if self._session_id else None,
            )
        except Exception as exc:  # noqa: BLE001
            log_warning(f"hello could not be built: {exc}")
            self._ws.close(1000, "hello failed")
            return
        self._last_manifest_hash = manifest_hash
        self._manifest_included = include
        self._resume_asked = bool(self._session_id)
        what = f"resume after seq {last_seq}" if self._session_id else "new"
        log(f"hello sent (manifest {'included' if include else 'by hash'}, {what} session)")
        self._send(frame)
        self._hello_sent_at = self._hello_checked_at = time.monotonic()
        self._hello_deadline.setInterval(int(socket_clocks()["hello_timeout_s"] * 1000))
        self._hello_deadline.start()

    def _known_manifests(self) -> list:





        try:
            return str(self._settings.known_manifest_hash or "").split()
        except Exception:  # noqa: BLE001
            return []

    def _remember_manifest(self, manifest_hash: str, known: bool) -> None:

        kept = [h for h in self._known_manifests() if h != manifest_hash]
        if known and manifest_hash:
            kept.insert(0, manifest_hash)
        try:
            self._settings.known_manifest_hash = " ".join(kept[:KNOWN_MANIFESTS_KEPT])
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Manifest memory not stored: {exc}")

    def _on_hello_timeout(self) -> None:







        if self._state == "online" or self._user_closed:
            return
        if self._hello_still_leaving():
            self._hello_deadline.start(HELLO_UPLOAD_RECHECK_MS)
            return
        log_warning(f"No session frame within {self._hello_deadline.interval() // 1000}s of hello, reconnecting")
        self._last_failure = ("hello_timeout", "", 0)
        self._ws.close(1000, "no session frame")

    def _hello_still_leaving(self) -> bool:







        probe = getattr(self._ws, "link_moved_since", None)
        if probe is None or not self._hello_sent_at:
            return False
        now = time.monotonic()
        if now - self._hello_sent_at >= HELLO_UPLOAD_CAP_S:
            return False
        since, self._hello_checked_at = self._hello_checked_at, now
        try:
            received, writing = probe(since)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Socket activity unreadable: {exc}")
            return False
        if writing or received:
            log(f"Hello still {'uploading' if writing else 'answered'} after {now - self._hello_sent_at:.0f} s, "
                "waiting for it")
        return bool(writing or received)

    def _on_ws_disconnected(self, code: int, reason: str) -> None:
        self._heartbeat.stop()
        self._hello_deadline.stop()
        self._awaiting_pongs = 0
        if self._user_closed:
            self._set_state("offline", "")
            return
        if self._update_required:


            self._set_state("offline", self._update_required)
            return
        if self._auth_failed:



            self._set_state("signed_out", self._auth_message
                            or tr("Session expired. Sign in again to continue."))
            return
        log(f"WebSocket closed ({code}): {reason}")
        self._note_drop(code, reason)
        self._schedule_reconnect(code, reason)

    def _note_drop(self, code: int, reason: str) -> None:

        if not self._reached_session or self._last_drop is not None:
            return
        by = str(getattr(self._ws, "closed_by", "none") or "none")
        kind = str(getattr(self._ws, "drop_kind", "") or "")
        if reason == "heartbeat lost":
            kind = "heartbeat_lost"
        self._last_drop = {"code": int(code) if by != "none" else None, "by": by, "kind": kind[:32]}
        self._dropped_at = time.monotonic()

    def _drop_report(self) -> dict | None:
        if self._last_drop is None:
            return None
        return {**self._last_drop, "offline_s": round(max(0.0, time.monotonic() - self._dropped_at), 1)}

    def _on_ws_error(self, message: str) -> None:

        log_warning(f"WebSocket: {message}")

    def _on_ws_failed(self, kind: str, message: str, status: int) -> None:
        self._last_failure = (kind, message, int(status or 0))
        if kind == "http_status" and int(status or 0) in (401, 403):



            self._auth_failed = True
            self._auth_message = tr("Session expired. Sign in again to continue.")

    def _schedule_reconnect(self, code: int = 1006, reason: str = "") -> None:













        clocks = socket_clocks()
        base = min(clocks["backoff_max_s"], clocks["backoff_min_s"] * (2 ** min(self._attempt, 16)))
        delay = base * random.uniform(0.5, 1.0)  # nosec B311
        restart = code in RESTART_CODES and self._attempt < RESTART_QUICK_TRIES
        if restart:



            delay = random.uniform(0.0, RESTART_JITTER_S)  # nosec B311
        if not self._reached_session and not restart:



            self._failed_streak += 1
            self._url_failures += 1
            if self._failed_streak == 1:
                self._failing_since = time.monotonic()
            if self._failed_streak in (1, 5):
                self._report_failure(code)
        self._attempt += 1
        failing_for = time.monotonic() - self._failing_since if self._failed_streak else 0.0
        said = self._panel_cause(code, reason) if failing_for >= CAUSE_AFTER_S else ""
        kind = (self._last_failure or ("",))[0]
        self._link_cause = "internet" if kind in INTERNET_DOWN_FAILURES else "server"
        self._log_failure(code, reason)
        self._set_state("offline", said)
        self._reconnect.start(int(delay * 1000))

    def link_cause(self) -> str:


        return self._link_cause

    def _panel_cause(self, code: int, reason: str) -> str:




        kind, _message, status = self._last_failure or ("", "", 0)
        user_side = kind in USER_SIDE_FAILURES or (kind == "http_status" and int(status or 0) not in (502, 503, 504))
        cause = self._failure_text(code, reason) if user_side else ""
        return cause or tr("Can't reach TerraLab. Retrying.")

    def failure_facts(self, code: int = 0) -> dict:





        kind, _message, status = self._last_failure or ("", "", 0)
        try:
            from .settings import DEFAULT_SERVER_URL, alternate_server_url
            url = str(self._settings.server_url or "")
            server = ("default" if url == DEFAULT_SERVER_URL
                      else "alternate" if url == alternate_server_url() else "custom")
        except Exception:  # noqa: BLE001
            server = "default"
        return {
            "error_code": (kind or (f"CLOSE_{int(code)}" if code else "OFFLINE")).upper(),
            "http_status": int(status or 0),
            "route": "proxy" if getattr(self._ws, "proxy_label", "") else "direct",
            "server": server,
            "ws_close_code": int(code or 0),
        }

    def _report_failure(self, code: int) -> None:
        facts = self.failure_facts(code)
        facts["attempt"] = int(self._failed_streak)
        facts["resuming"] = bool(self._session_id)
        try:
            self.connection_failed.emit(facts)
        except (AttributeError, RuntimeError):
            pass

    def _log_failure(self, code: int = 1006, reason: str = "") -> None:


        url = str(self._settings.server_url or "")
        cause = self._failure_text(code, reason)
        self._last_failure = None
        if "127.0.0.1" in url or "localhost" in url:
            log_warning(f"Local server {url} is not running, reconnecting")
        elif cause:
            log_warning(f"Reconnecting: {cause}")

    def _failure_text(self, code: int, reason: str) -> str:






        kind, _message, status = self._last_failure or ("", "", 0)
        if kind == "dns":
            return tr("The server name could not be resolved. Check your internet connection.")
        if kind == "timeout":
            return tr("The server did not answer in time.")
        if kind == "proxy_auth":
            return tr("The proxy asks for a login. Set the proxy user and password in QGIS "
                      "(Settings > Options > Network).")
        if kind == "proxy_refused":
            return tr("The proxy refused the connection to TerraLab.")
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
                return tr("TerraLab is unavailable right now (HTTP {code}).").format(code=status)
            return tr("A gateway blocked the connection (HTTP {code}). If this network shows a sign-in "
                      "page, open it in your browser first.").format(code=status)
        if code in (1008, 1011) or 4000 <= code < 5000:
            return tr("The server closed the connection ({code}).").format(code=code)
        return ""

    def _reconnect_now(self) -> None:
        if not self._user_closed:
            self._open()

    def _link_moved(self) -> tuple[bool, bool]:

        since, self._beat_at = self._beat_at, time.monotonic()
        probe = getattr(self._ws, "link_moved_since", None)
        if probe is None:
            return False, False
        try:
            received, writing = probe(since)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Socket activity unreadable: {exc}")
            return False, False
        return bool(received), bool(writing)

    def _on_heartbeat(self) -> None:
        if not self._ws.is_open:
            return
        received, writing = self._link_moved()
        tick_s = max(0.001, self._heartbeat.interval() / 1000.0)
        if received or writing:


            self._awaiting_pongs = 0
            if writing:
                return
            if time.monotonic() - self._sent_at < 2 * tick_s:
                return
        if self._awaiting_pongs >= max(MAX_MISSED_PONGS, math.ceil(LINK_SILENT_S / tick_s)):
            log_warning(f"{self._awaiting_pongs} pongs missed, closing the socket to reconnect")
            self._heartbeat.stop()


            self._set_state("offline", "")
            self._ws.close(1001, "heartbeat lost")
            return
        self._awaiting_pongs += 1
        self._send(protocol.ping())



    def _on_frame(self, text: str) -> None:
        if self._user_closed:



            return
        try:
            frame = protocol.decode(text)
        except ProtocolError as exc:
            self._note_bad_frame("invalid", str(exc))
            return
        if self._already_received(frame):
            self._awaiting_pongs = 0
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







        seen = self._bad_frames.get(category)
        if seen is None:
            self._bad_frames[category] = 1
            log_warning(f"Frame dropped ({category}): {detail[:200]}")
            return
        self._bad_frames[category] = seen + 1
        if seen + 1 in _BAD_FRAME_MILESTONES:
            log_warning(f"Frame dropped ({category}): {seen + 1} so far this session")

    def _already_received(self, frame: dict) -> bool:










        seq = frame.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq <= 0:
            return False
        if seq <= self._last_seq:
            self._replays_dropped += 1
            if self._replays_dropped == 1:
                log(f"Frame seq {seq} ({frame.get('type')}) already received, dropped")
            return True
        if self._replays_dropped:
            log(f"{self._replays_dropped} frame(s) already received were dropped")
            self._replays_dropped = 0
        if seq > self._last_seq + 1:
            log_warning(f"Frames seq {self._last_seq + 1} to {seq - 1} never reached this session")
        self._last_seq = seq
        return False

    def _on_session(self, frame: dict) -> None:
        self._hello_deadline.stop()
        if self._crash_note is not None:


            crash_note.acknowledge()
            self._crash_note = {}
        if frame.get("resumed") is not True:


            self._last_seq = 0
            self._replays_dropped = 0
            self._threads_told.clear()




        tuning.apply(frame.get("policy"))


        data_access.apply(frame.get("data_access"))
        self._session_id = str(frame.get("session_id") or self._session_id or "") or None
        self._attempt = 0
        self._reached_session = True
        self._failed_streak = 0
        self._url_failures = 0
        if self._resume_asked and frame.get("resumed") is False:




            self._resume_refused = True
        known = bool(frame.get("manifest_known"))
        if known:
            self._remember_manifest(self._last_manifest_hash, True)
        elif not self._manifest_included:
            log("Server does not hold the tool manifest, reconnecting with the full catalog")
            self._remember_manifest(self._last_manifest_hash, False)
            self._manifest_retry = True
            self._ws.close(1000, "resend manifest")
            return
        if not self._take_catalogs(frame):
            return


        self._connect_facts = {
            "connect_ms": int((time.monotonic() - self._hello_sent_at) * 1000) if self._hello_sent_at else None,
            "connect_manifest": bool(self._manifest_included),
            "connect_catalogs": self._catalogs_came,
        }
        self._manifest_retry = False
        self._last_drop = None
        self._awaiting_pongs = 0
        self._last_failure = None
        self._beat_at = time.monotonic()
        self._apply_heartbeat()
        self._heartbeat.start()
        self._model_label = str(frame.get("model_label") or "")


        self._steer = frame.get("steer") is True
        self._unsteer = frame.get("unsteer") is True
        self._edit_last = frame.get("edit_last") is True

        groups = frame.get("effort_switch")
        self._effort_switch = tuple(tuple(str(e) for e in g) for g in groups
                                    if isinstance(g, list)) if isinstance(groups, list) else ()
        self._set_state("online", self._model_label)
        for run_id in sorted(self._pending_cancels):
            self._send(protocol.cancel(run_id, self._cancel_reasons.get(run_id, "")))
            left = self._pending_cancels.get(run_id, 0) - 1
            if left > 0:
                self._pending_cancels[run_id] = left
            else:
                self._pending_cancels.pop(run_id, None)
                self._cancel_reasons.pop(run_id, None)
        if self._resume_refused:
            frame = dict(frame, resumed=False)
            self._resume_refused = False
        self.session_started.emit(frame)

    def _take_catalogs(self, frame: dict) -> bool:







        tag = str(frame.get("catalog_tag") or "")
        carried = {key: frame[key] for key in protocol.CATALOG_FIELDS if key in frame}
        self._catalogs_came = bool(carried)
        if carried:
            if tag and tag != self._disk_tag:
                self._catalog_writer = _store_catalogs(tag, carried)
                self._disk_tag = tag
            self._applied_tag = tag
        elif tag and tag != self._applied_tag:


            held = _held_catalogs(tag)
            if held is None:
                log_warning("Catalogs left out by the server are not on disk, asking for them whole")
                self._catalog_tag = self._disk_tag = ""
                self._ws.close(1000, "resend catalogs")
                return False
            frame.update(held)
            self._applied_tag = tag
        self._catalog_tag = tag
        return True

    def _on_policy(self, frame: dict) -> None:






        tuning.apply(frame.get("policy"))
        self._apply_heartbeat()

    def _on_token(self, frame: dict) -> None:
        self.token.emit(str(frame.get("run_id") or ""), str(frame.get("text") or ""))

    def _on_plan(self, frame: dict) -> None:
        steps = frame.get("steps") if isinstance(frame.get("steps"), list) else []
        self.plan.emit(str(frame.get("run_id") or ""), steps)

    def _on_plan_update(self, frame: dict) -> None:
        self.plan_update.emit(str(frame.get("run_id") or ""), str(frame.get("step_id") or ""),
                              str(frame.get("state") or ""))

    def _on_tool_call(self, frame: dict) -> None:
        tool_call_id = str(frame.get("tool_call_id") or "")
        if tool_call_id:
            started = time.perf_counter()


            arrived = getattr(self._ws, "arrived_at", 0.0) or started
            self._call_clocks.pop(tool_call_id, None)
            self._call_clocks[tool_call_id] = (min(arrived, started), started)
            while len(self._call_clocks) > _CALL_CLOCKS_KEPT:
                self._call_clocks.pop(next(iter(self._call_clocks)))
        self.tool_call.emit(frame)

    def _on_run_end(self, frame: dict) -> None:

        self._pending_cancels.pop(str(frame.get("run_id") or ""), None)
        self._cancel_reasons.pop(str(frame.get("run_id") or ""), None)
        usage = frame.get("usage") if isinstance(frame.get("usage"), dict) else {}
        verification = frame.get("verification") if isinstance(frame.get("verification"), dict) else None
        self.run_end.emit(str(frame.get("run_id") or ""), str(frame.get("status") or "done"),
                          str(frame.get("summary") or ""), usage, verification)

    def _on_status_line(self, frame: dict) -> None:
        self.status_line.emit(str(frame.get("run_id") or ""), str(frame.get("text") or ""))

    def _on_error(self, frame: dict) -> None:
        code = str(frame.get("code") or ServerErrorCode.INTERNAL)




        if code == ServerErrorCode.AUTH_FAILED and frame.get("retryable") is not True:
            self._auth_failed = True
            self._auth_message = str(frame.get("message") or "")
            self._heartbeat.stop()
            self._set_state("signed_out", str(frame.get("message") or ""))
            self._ws.close(1000, "auth failed")
        if code == ServerErrorCode.PLUGIN_UPDATE_REQUIRED:
            self._update_required = str(frame.get("message") or "") or tr(
                "This version of AI Agent is no longer supported. Update the plugin to continue.")
            self._heartbeat.stop()
            self._ws.close(1000, "plugin update required")
        self.server_error.emit(frame)

    def _on_usage(self, frame: dict) -> None:
        self.usage.emit(frame)

    def _on_pong(self, frame: dict) -> None:
        self._awaiting_pongs = 0

    def _on_thread_title(self, frame: dict) -> None:
        self.thread_title.emit(str(frame.get("thread_id") or ""), str(frame.get("title") or ""))

    def _on_memory_note(self, frame: dict) -> None:
        self.memory_note.emit(str(frame.get("text") or ""), str(frame.get("kind") or ""),
                              str(frame.get("scope") or ""), str(frame.get("run_id") or ""),
                              str(frame.get("replaces") or ""), str(frame.get("setting") or ""),
                              str(frame.get("value") or ""))

    def _on_sources(self, frame: dict) -> None:
        raw = frame.get("items") if isinstance(frame.get("items"), list) else []
        items = [i for i in raw if isinstance(i, dict) and i.get("name")]
        self.sources.emit(str(frame.get("run_id") or ""), items)

    def _on_server_tool(self, frame: dict) -> None:
        if not frame.get("tool_call_id") or not frame.get("name") or not isinstance(frame.get("ok"), bool):
            self._note_bad_frame("server_tool", "missing tool_call_id, name or ok")
            return
        self.server_tool.emit(frame)

    def _on_steer_ack(self, frame: dict) -> None:
        run_id, steer_id = str(frame.get("run_id") or ""), str(frame.get("steer_id") or "")
        if run_id and steer_id:
            self.steer_ack.emit(run_id, steer_id, frame.get("taken") is True)

    def _on_counts(self, frame: dict) -> None:
        try:
            self.counts.emit(str(frame.get("run_id") or ""), int(frame.get("tool_calls") or 0),
                             int(frame.get("messages") or 0))
        except (TypeError, ValueError):
            pass



def _piece_producer(upload_id: str, name: str, seq: int, total: int, path: str, offset: int,
                    size: int) -> Callable[[], str]:




    def produce() -> str:
        data = ""
        try:
            with open(path, "rb") as handle:
                handle.seek(offset)
                data = base64.b64encode(handle.read(size)).decode("ascii")
        except OSError as exc:
            log_warning(f"Upload piece {seq} of {name!r} not read: {exc}")
        return protocol.encode(protocol.upload(upload_id, name, seq, total, data))

    return produce
