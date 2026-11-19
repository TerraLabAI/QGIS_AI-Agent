# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later















from __future__ import annotations

import sys

from qgis.core import QgsApplication, QgsTask
from qgis.PyQt.QtCore import QEvent, Qt
from qgis.PyQt.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from ..api.terralab_client import TerraLabClient
from ..core.host_platform import os_info, os_label, plugin_version, prefers_reduced_motion
from ..core.log_scrub import scrub_secrets, scrub_user_paths
from ..core.logger import log_warning, recent_logs
from ..core.served_state import PRODUCT_ID, SUPPORT_EMAIL, _served_text
from .dock.about import CONTACT_QSS
from .font_scale import apply_font_scale_to_tree, scale_px_length, scale_qss_font_px
from .mascot import GAZE, LIVE, Mascot
from .session_export import render_markdown
from .shared import exec_dialog, tr
from .style import (
    _BTN_GHOST,
    _BTN_PRIMARY,
    ACCENT,
    BTN_PILL_PX,
    FONT_BASE,
    FONT_HINT,
    INK,
    LINE,
    RED,
    SURFACE,
    keyboard_ring,
    with_platform_font,
)
from .transcript import redact_signed_urls
from .widgets import placeholder_ink

_BOX_QSS = scale_qss_font_px(
    f"QPlainTextEdit#fbBox {{ font-size: {FONT_BASE}px; color: {INK}; background: {SURFACE};"
    f" border: 1px solid {LINE}; border-radius: 8px; padding: 8px 10px; }}"
    f"QPlainTextEdit#fbBox:focus {{ border-color: {ACCENT}; }}")


def _report_email() -> str:



    return _served_text("support_email", SUPPORT_EMAIL)




_LOG_LINES_MAX = 300
_CONVERSATION_CHARS_MAX = 380_000


def _clean(text: str) -> str:

    return scrub_secrets(scrub_user_paths(text))


def diagnostic_text(error_message: str = "", run_id: str = "") -> str:

    lines = ["=== AI Agent error report ===", ""]
    if error_message:
        lines.extend(["--- Error ---", _clean(error_message), ""])
    if run_id:
        lines.extend(["--- Run ---", f"Run ID: {run_id}", ""])
    lines.extend([
        "--- Plugin ---",
        f"Version: {plugin_version() or 'unknown'}",
        "",
        "--- System ---",
        f"OS: {os_label()}",
        f"Architecture: {os_info()[2]}",
        f"Python: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    ])
    try:
        from qgis.core import Qgis
        lines.append(f"QGIS: {Qgis.QGIS_VERSION}")
    except Exception:  # noqa: BLE001
        lines.append("QGIS: unknown")
    lines.extend(["", "--- Server policy ---", _policy_line()])
    lines.extend(["", "--- Recent logs ---", recent_logs(), "", "=== End of report ==="])
    return _clean("\n".join(lines))


def _policy_line() -> str:







    try:
        from ..core import tuning

        snap = tuning.snapshot()
    except Exception:  # noqa: BLE001
        return "unavailable"
    tuned = {key: value for key, value in snap.items() if key != "version" and value}
    if not tuned:
        return "none in force (the values this build shipped with)"
    parts = [f"v{snap.get('version', 0)}"]
    parts += [f"{section}: {values}" for section, values in sorted(tuned.items())]
    return "; ".join(parts)


def feedback_payload(message: str, conversation: str, run_id: str, entry: str, rating: str = "") -> dict:

    lines = [_clean(line) for line in recent_logs().splitlines()][-_LOG_LINES_MAX:]
    system, release, machine = os_info()
    try:
        from qgis.core import Qgis
        qgis_version = Qgis.QGIS_VERSION
    except Exception:  # noqa: BLE001
        qgis_version = ""
    try:
        from ..core import telemetry
        session = telemetry.session_id()
    except Exception:  # noqa: BLE001
        session = ""
    context = {
        "plugin_version": plugin_version() or "", "qgis_version": qgis_version, "os": system,
        "os_version": release, "arch": machine,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "session_id": session, "run_id": run_id, "entry": entry,
    }
    body = {
        "product_id": PRODUCT_ID, "kind": "feedback",
        "message": redact_signed_urls((message or "").strip())[:1500],
        "conversation": conversation,
        "context": {key: value for key, value in context.items() if value},
        "log_lines": lines, "line_count": len(lines), "truncated": False,
    }
    if rating in ("up", "down"):
        body["rating"] = rating
    return body


class _SendTask(QgsTask):


    def __init__(self, report: dict, auth: dict, done):
        holder = getattr(QgsTask, "Flag", QgsTask)
        flags = holder.CanCancel
        for name in ("Hidden", "Silent"):
            extra = getattr(holder, name, None)
            if extra is not None:
                flags = flags | extra
        super().__init__("AI Agent feedback", flags)
        self._report, self._auth, self._done = report, dict(auth), done
        self.answer: dict = {}

    def run(self) -> bool:

        try:
            self.answer = TerraLabClient().send_feedback(self._report, self._auth) or {}
        except Exception as exc:  # noqa: BLE001
            self.answer = {"error": str(exc), "code": "CLIENT_ERROR"}
        return not self.answer.get("error")

    def finished(self, ok: bool) -> None:
        self._auth.clear()
        done, self._done = self._done, None
        if done is not None:
            done(bool(ok), self.answer)


class FeedbackDialog(QDialog):


    def __init__(self, error_message: str = "", run_id: str = "", parent=None, report_provider=None,
                 auth_provider=None, entry: str = "panel"):
        super().__init__(parent)
        with_platform_font(self)
        self.setWindowTitle(tr("Send feedback"))
        self.setModal(True)
        self.setMinimumWidth(scale_px_length(440))
        self._run_id = str(run_id or "")
        self._error = str(error_message or "")
        self._entry = entry


        self._provider = report_provider
        self._auth_provider = auth_provider
        self._export: dict | None = None
        self._task = None
        self.sent = False

        self.setObjectName("feedbackDialog")
        self.setStyleSheet(CONTACT_QSS + _BOX_QSS)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)
        head = QHBoxLayout()
        head.setSpacing(14)
        self._seat = QWidget(self)
        self._seat.setFixedSize(scale_px_length(46), scale_px_length(54))
        self._mascot = Mascot(self._seat)
        self._mascot.set_mode(GAZE if prefers_reduced_motion() else LIVE)
        self._mascot.set_mood("listen")
        head.addWidget(self._seat, 0, Qt.AlignmentFlag.AlignVCenter)
        words = QVBoxLayout()
        words.setSpacing(2)
        title = QLabel(tr("Send feedback"), self)
        title.setObjectName("ctTitle")
        words.addWidget(title)
        subtitle = QLabel(tr("We read every message and use it to improve AI Agent."), self)
        subtitle.setObjectName("ctSub")
        subtitle.setWordWrap(True)
        words.addWidget(subtitle)
        head.addLayout(words, 1)
        layout.addLayout(head)

        card = QFrame(self)
        card.setObjectName("ctCard")
        inner = QVBoxLayout(card)
        inner.setContentsMargins(16, 16, 16, 14)
        inner.setSpacing(10)
        self._text = QPlainTextEdit(card)
        self._text.setObjectName("fbBox")
        self._text.setPlaceholderText(tr("What happened, and what did you expect? For example: the buffer came out "
                                         "in degrees instead of metres."))
        placeholder_ink(self._text)
        self._text.setTabChangesFocus(True)
        self._text.installEventFilter(self)
        self._text.setFixedHeight(scale_px_length(132))
        self._text.textChanged.connect(self._sync_send)
        inner.addWidget(self._text)
        self._note = QLabel(self._note_text(), card)
        self._note.setObjectName("ctNote")
        self._note.setWordWrap(True)
        self._note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        inner.addWidget(self._note)
        self._failure = QLabel(card)
        self._failure.setWordWrap(True)
        self._failure.setStyleSheet(
            scale_qss_font_px(f"color: {RED}; font-size: {FONT_HINT}px; background: transparent;"))
        self._failure.hide()
        inner.addWidget(self._failure)
        layout.addWidget(card)


        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        self._cancel = QPushButton(tr("Cancel"), card)
        self._cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel.setStyleSheet(_BTN_GHOST)
        keyboard_ring(self._cancel)
        self._cancel.setFixedHeight(scale_px_length(BTN_PILL_PX))
        self._cancel.setAutoDefault(False)
        self._cancel.clicked.connect(self.reject)
        row.addWidget(self._cancel)
        self._send = QPushButton(tr("Send"), card)
        self._send.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send.setStyleSheet(_BTN_PRIMARY)
        keyboard_ring(self._send)
        self._send.setFixedHeight(scale_px_length(BTN_PILL_PX))

        self._send.ensurePolished()
        metrics = self._send.fontMetrics()
        self._send.setMinimumWidth(max(scale_px_length(84), 32 + max(
            metrics.horizontalAdvance(tr("Send")), metrics.horizontalAdvance(tr("Sending...")))))
        self._send.clicked.connect(self._on_send)
        row.addWidget(self._send)
        inner.addSpacing(2)
        inner.addLayout(row)
        apply_font_scale_to_tree(self)
        self.adjustSize()
        self.resize(max(self.width(), scale_px_length(520)), self.height())
        self._mascot.put(self._seat.width() / 2.0, self._seat.height() - 2.0, scale_px_length(44))
        self._sync_send()
        self._text.setFocus()

    def _auth(self) -> dict:
        try:
            headers = self._auth_provider() if callable(self._auth_provider) else {}
        except Exception:  # noqa: BLE001
            return {}
        return headers if isinstance(headers, dict) and headers.get("Authorization") else {}

    def _note_text(self) -> str:
        if not self._auth():
            return tr("Sign in to send feedback from here, or write to us at {email}.").format(
                email=_report_email())
        return tr("Sent with this conversation and the plugin's recent log.")

    def eventFilter(self, obj, event):  # noqa: N802

        if obj is self._text and event.type() == QEvent.Type.KeyPress \
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                and event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            self._on_send()
            return True
        return super().eventFilter(obj, event)

    def _sync_send(self) -> None:
        self._send.setEnabled(bool(self._text.toPlainText().strip()) and self._task is None
                              and bool(self._auth()))

    def report(self) -> dict | None:

        if self._export is None and self._provider is not None:
            try:
                self._export = self._provider(self._run_id, self._error)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"The session report could not be built: {exc}")
                self._provider = None
        return self._export

    def conversation(self) -> str:

        export = self.report()
        if export is None:
            return diagnostic_text(self._error, self._run_id)

        return _clean(render_markdown(export))[-_CONVERSATION_CHARS_MAX:]

    def payload(self) -> dict:

        return feedback_payload(self._text.toPlainText(), self.conversation(), self._run_id, self._entry)

    def _on_send(self) -> None:
        auth = self._auth()
        if not auth or not self._text.toPlainText().strip() or self._task is not None:
            return
        self._failure.hide()
        self._send.setText(tr("Sending..."))
        self._text.setReadOnly(True)
        self._task = _SendTask(self.payload(), auth, self._on_answer)
        self._sync_send()
        if not QgsApplication.taskManager().addTask(self._task):
            self._on_answer(False, {"code": "CLIENT_ERROR"})

    def _on_answer(self, ok: bool, answer: dict) -> None:
        self._task = None
        try:
            self._send.setText(tr("Send"))
            self._text.setReadOnly(False)
        except RuntimeError:
            return
        if ok:
            self.sent = True
            self.accept()
            return
        if answer.get("code") == "RATE_LIMITED":
            self._failure.setText(tr("You sent several reports in a short time. Try again in a few minutes."))
        else:
            self._failure.setText(tr("Could not send. Check your connection and try again, or write to us at {email}.")
                                  .format(email=_report_email()))
        self._failure.show()
        self._sync_send()

    def reject(self) -> None:

        super().reject()


def _near(widget, name: str):

    node = widget
    for _ in range(12):
        if node is None:
            return None
        found = getattr(node, name, None)
        if callable(found):
            return found
        node = node.parent() if hasattr(node, "parent") else None
    return None


def _owner_window(widget):
    try:
        from qgis.utils import iface
        main = iface.mainWindow() if iface is not None else None
    except Exception:  # noqa: BLE001
        main = None
    if main is not None:
        return main
    return widget.window() if widget is not None else None


def show_feedback(parent=None, error_message: str = "", run_id: str = "", entry: str = "panel") -> bool:







    dialog = FeedbackDialog(error_message, run_id, _owner_window(parent), _near(parent, "diagnostics_provider"),
                            _near(parent, "auth_header_provider"), entry)
    _IN_FLIGHT.add(dialog)
    try:
        exec_dialog(dialog)
    finally:
        _IN_FLIGHT.discard(dialog)
        dialog.deleteLater()
    if dialog.sent:
        host = _near(parent, "feedback_sent")
        if host is not None:
            host()
    return dialog.sent


def send_answer_words(widget, run_id: str, rating: str, text: str, done) -> bool:




    auth_provider = _near(widget, "auth_header_provider")
    try:
        auth = auth_provider() if auth_provider is not None else {}
    except Exception:  # noqa: BLE001
        auth = {}
    if not text.strip() or not isinstance(auth, dict) or not auth.get("Authorization"):
        return False
    conversation = ""
    provider = _near(widget, "diagnostics_provider")
    if provider is not None:
        try:
            from .session_export import render_markdown

            conversation = _clean(render_markdown(provider(run_id, "")))[-_CONVERSATION_CHARS_MAX:]
        except Exception as exc:  # noqa: BLE001
            log_warning(f"The session report could not be built: {exc}")
    task = _SendTask(feedback_payload(text, conversation, run_id, "answer", rating), auth,
                     lambda ok, _answer: done(ok))
    _IN_FLIGHT.add(task)
    task.taskCompleted.connect(lambda: _IN_FLIGHT.discard(task))
    task.taskTerminated.connect(lambda: _IN_FLIGHT.discard(task))
    if not QgsApplication.taskManager().addTask(task):
        _IN_FLIGHT.discard(task)
        return False
    return True



_IN_FLIGHT: set = set()
