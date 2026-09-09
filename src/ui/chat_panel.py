# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat panel: every slot the controller calls, every signal it listens to."""













from __future__ import annotations

import contextlib
import time

from qgis.PyQt.QtCore import QEasingCurve, Qt, QTimer, QVariantAnimation, pyqtSignal
from qgis.PyQt.QtGui import QKeySequence
from qgis.PyQt.QtWidgets import (
    QFrame,
    QShortcut,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.background import run_sliced
from ..core.layer_mime import mime_has_layers
from ..core.protocol import StopCode
from ..core.telemetry_errors import slot_guard
from .bubbles import AgentBubble, StatusLine, UserBubble
from .card_base import humanise_tool_name
from .cards import (
    ErrorCard,
    PermissionCard,
    QuestionCard,
    QuotaPauseCard,
    RestoreWarningCard,
    RunSummaryCard,
    ToolCard,
)
from .composer import Composer
from .dock.about import UpdateBanner, show_contact_dialog, show_shortcuts_dialog
from .dock.activation_state import ActivationCard
from .drop_overlay import DropOverlay
from .empty_state import EmptyState
from .external_links import open_external_url, open_local_path
from .file_links import is_safe_to_open, path_from_url, reveal_target
from .header import Header
from .layer_links import LayerLinkRow, layer_id_from_url, linkify_layers
from .message_list import MessageList
from .notice_bar import NoticeBar
from .quota_card import QuotaCard
from .shared import (
    format_reset_date,
    get_contact_call_url,
    get_dashboard_url,
    get_free_runs,
    get_support_email,
    get_tutorial_url,
    get_upgrade_url,
    tr,
)
from .style import MIN_PANEL_WIDTH, SPACE_CARD, SPACE_TIGHT, panel_qss
from .thread_replay import replay_steps
from .trace import RunFootnote, RunTrace
from .update_gate import UpdateGate











_NOTHING_CHANGED = "No layer, feature or file changed"


def _is_nothing_changed(line: str) -> bool:
    return line in (_NOTHING_CHANGED, tr(_NOTHING_CHANGED))



_LONG_CHAT_SHARE = 0.7

REPLAY_SLICE_MS = 24


_CENTER_MS = 260













_CENTER_ABOVE = 48
_CENTER_BELOW = 52

_MAX_HEIGHT = 16777215


class _Run:
    """What the panel keeps per run: its bubble and its cards."""

    __slots__ = ("run_id", "bubble", "trace", "tools", "permissions", "started", "segment_break",
                 "waited", "wait_started")

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.bubble: AgentBubble | None = None
        self.trace: RunTrace | None = None
        self.tools: list = []
        self.permissions: list = []

        self.segment_break = False

        self.started = time.monotonic()

        self.waited = 0.0
        self.wait_started = 0.0


def _wrap(widget: QWidget, margins: tuple, parent=None) -> QWidget:
    """A widget with side margins, so bars align with the list's own."""
    host = QWidget(parent)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(*margins)
    lay.setSpacing(0)
    lay.addWidget(widget)
    return host


def _layer_id_named(name: str) -> str:
    """The id of the project layer called ``name``, or "" outside QGIS."""
    try:
        from qgis.core import QgsProject

        layers = QgsProject.instance().mapLayersByName(name)
    except Exception:  # noqa: BLE001 - no project here
        return ""
    return layers[0].id() if layers else ""


def _turn_divider(parent=None) -> QFrame:
    """The hairline between two turns of a thread."""
    line = QFrame(parent)
    line.setObjectName("turnDivider")
    line.setFrameShape(QFrame.Shape.NoFrame)
    line.setFixedHeight(1)
    return line


class ChatPanel(QWidget):
    """Native Qt chat panel, minimum width 320."""


    send_requested = pyqtSignal(str, str, str, object, object)
    stop_requested = pyqtSignal(str)



    permission_decided = pyqtSignal(str, str, object)
    question_answered = pyqtSignal(str, str)
    question_auto_answered = pyqtSignal(str, int)
    retry_requested = pyqtSignal(str)
    continue_requested = pyqtSignal(str)
    feedback = pyqtSignal(str, bool)
    undo_requested = pyqtSignal()
    restore_requested = pyqtSignal(str, bool)
    discard_all_requested = pyqtSignal(bool)
    history_requested = pyqtSignal()
    layer_action_requested = pyqtSignal(str, str)



    cleanup_decided = pyqtSignal(str, str, list)


    plugin_offer_decided = pyqtSignal(str, str)
    new_thread_requested = pyqtSignal()
    thread_selected = pyqtSignal(str)
    suggestion_clicked = pyqtSignal(str)
    chip_removed = pyqtSignal(str, str)
    files_dropped = pyqtSignal(object)
    open_settings_requested = pyqtSignal()
    sign_in_requested = pyqtSignal()

    sign_out_requested = pyqtSignal()
    context_add_requested = pyqtSignal(str)
    mode_changed = pyqtSignal(str, str)
    effort_changed = pyqtSignal(str)
    welcome_dismissed = pyqtSignal()
    pairing_reopen_requested = pyqtSignal()
    pairing_cancel_requested = pyqtSignal()
    dashboard_requested = pyqtSignal()
    upgrade_requested = pyqtSignal()
    help_requested = pyqtSignal(str)
    update_clicked = pyqtSignal(str)
    update_dismissed = pyqtSignal(str)
    low_balance_shown = pyqtSignal(int)
    attachment_added = pyqtSignal(int)
    thread_exported = pyqtSignal(str)
    example_chosen = pyqtSignal(str)
    recommendation_decided = pyqtSignal(str, str)
    diff_applied = pyqtSignal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chatPanel")
        self.setMinimumWidth(MIN_PANEL_WIDTH)
        self.setStyleSheet(panel_qss())
        self.setAcceptDrops(True)
        self._runs: dict[str, _Run] = {}
        self._replay_generation = 0


        self._reopened_tools: dict[str, bool] = {}
        self._long_chat_nudged = False
        self._current_run: str | None = None
        self._explain_runs = True
        self._show_tool_details = True
        self._last_run = ""
        self._last_error = ""
        self._error_said: dict[str, str] = {}


        self._cleanup_cards: dict[str, object] = {}
        self._offer_cards: dict[str, object] = {}
        self._low_balance_visible = False
        self._attachment_count = 0

        self._privacy_dialog = None
        self._privacy_on_accept = None
        self._status: StatusLine | None = None
        self._signed_in = True
        self._connection = "online"
        self._model_label = ""

        self._usage = (0, 0, "", True)

        self._col = QVBoxLayout(self)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(0)

        self.header = Header(self)
        self._col.addWidget(self.header)
        self.update_banner = UpdateBanner(self)
        self._col.addWidget(self.update_banner)

        self.update_gate = UpdateGate(self)
        self._col.addWidget(self.update_gate, 1)
        self.activation = ActivationCard(self)
        self.activation.hide()
        self._col.addWidget(self.activation, 1)
        self.message_list = MessageList(self)
        self._col.addWidget(self.message_list, 1)
        self.empty_state = EmptyState(self)
        self._col.addWidget(self.empty_state, 1)


        self.notice_bar = NoticeBar(self)
        self._col.addWidget(self.notice_bar)

        self.quota_card = QuotaCard(self)
        self._quota_host = _wrap(self.quota_card, (12, SPACE_CARD, 12, 0), self)
        self._quota_host.hide()
        self._col.addWidget(self._quota_host)
        self.composer = Composer(self)
        self._composer_host = _wrap(self.composer, (12, SPACE_CARD, 12, SPACE_TIGHT), self)
        self._col.addWidget(self._composer_host)



        self._empty_rows = _wrap(self.empty_state.rows_widget(), (12, 0, 12, SPACE_CARD), self)
        self._col.addWidget(self._empty_rows)






        self._center_spacer = QWidget(self)
        self._center_spacer.setObjectName("centreSpacer")
        self._center_spacer.setSizePolicy(QSizePolicy.Policy.Ignored,
                                          QSizePolicy.Policy.Ignored)
        self._center_spacer.setFixedHeight(0)
        self._col.addWidget(self._center_spacer)
        self._center_anim = QVariantAnimation(self)
        self._center_anim.setDuration(_CENTER_MS)
        self._center_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._center_anim.valueChanged.connect(
            lambda value: self._center_spacer.setFixedHeight(max(0, int(value))))
        self._drop_overlay = DropOverlay(self)

        self._wire()
        self._show_thread_surface()

    def detach_header(self) -> Header:
        """Hand the header to the dock, which makes it its title bar."""
        self._col.removeWidget(self.header)
        return self.header





    def _wire(self) -> None:
        h = self.header
        h.thread_selected.connect(self.thread_selected.emit)
        h.new_thread_requested.connect(self.new_thread_requested.emit)
        h.account_clicked.connect(self.open_settings_requested.emit)
        h.settings_clicked.connect(self.open_settings_requested.emit)

        self.update_banner.update_clicked.connect(self.update_clicked.emit)
        self.update_gate.update_clicked.connect(self.update_clicked.emit)
        self.update_banner.dismissed.connect(self.update_dismissed.emit)
        a = self.activation
        a.sign_in_requested.connect(self.sign_in_requested.emit)
        a.pairing_reopen_requested.connect(self.pairing_reopen_requested.emit)
        a.pairing_cancel_requested.connect(self.pairing_cancel_requested.emit)
        a.account_clicked.connect(self.open_settings_requested.emit)

        self.empty_state.suggestion_clicked.connect(self.suggestion_clicked.emit)
        self.empty_state.welcome_dismissed.connect(self.welcome_dismissed.emit)
        self.empty_state.examples_requested.connect(self.open_examples)
        self.empty_state.tutorial_requested.connect(lambda: self._on_help("tutorial"))
        h.checkpoints_requested.connect(self.history_requested.emit)
        h.restore_requested.connect(lambda cid: self.restore_requested.emit(cid, False))
        h.discard_all_requested.connect(lambda: self.discard_all_requested.emit(False))
        self._history: list = []
        self._changed_layers: list = []
        self._changed_count = 0
        self._wire_history_keys()
        self.quota_card.upgrade_requested.connect(self._on_upgrade)
        c = self.composer
        c.send_clicked.connect(self._on_send)
        c.stop_clicked.connect(self._on_stop)
        c.files_dropped.connect(lambda paths: self.files_dropped.emit(list(paths)))
        c.attachments_changed.connect(self._on_attachments_changed)
        c.context_add_requested.connect(self.context_add_requested.emit)
        c.chip_removed.connect(self.chip_removed.emit)

        c.layer_card_clicked.connect(lambda layer_id: self.layer_action_requested.emit(layer_id, "show"))

        c.permission_mode_changed.connect(lambda approval: self.mode_changed.emit("", approval))

        c.effort_changed.connect(self.effort_changed.emit)
        c.upgrade_requested.connect(self._on_upgrade)
        c.example_chosen.connect(self.example_chosen.emit)

    def open_examples(self) -> None:
        """The Examples library window."""
        self.composer.open_examples()

    @slot_guard("panel_send")
    def _on_send(self) -> None:
        text = self.composer.text().strip()
        attachments = self.composer.attachments()
        if not text and not attachments:
            return







        if not self._require_privacy_notice(self._on_send):
            return

        chips = self.composer.take_chips()

        self.send_requested.emit(text, "", "", chips, attachments)
        self.composer.clear()
        self.composer.clear_attachments()



    def _require_privacy_notice(self, on_accept) -> bool:
        """Gate the first thing that leaves the machine behind the notice."""





        from ..core.privacy_notice import has_accepted_privacy_notice

        if has_accepted_privacy_notice():
            return True
        self._privacy_on_accept = on_accept
        self._show_privacy_notice()
        return False

    def _show_privacy_notice(self) -> None:
        """The notice, opened at the moment the user asks to send something."""





        from .privacy_notice_dialog import PrivacyNoticeDialog

        if self._privacy_dialog is not None:
            self._privacy_dialog.raise_()
            return
        dialog = PrivacyNoticeDialog(self)
        dialog.accepted.connect(self._on_privacy_accepted)
        dialog.rejected.connect(self._on_privacy_declined)
        dialog.finished.connect(self._on_privacy_closed)
        self._privacy_dialog = dialog
        dialog.open()

    def _on_privacy_accepted(self) -> None:
        from ..core import telemetry
        from ..core.privacy_notice import save_privacy_notice_accepted

        save_privacy_notice_accepted()

        with contextlib.suppress(Exception):
            telemetry.flush()
        queued = self._privacy_on_accept
        self._privacy_on_accept = None
        if queued is not None:
            queued()

    def _on_privacy_declined(self) -> None:
        """Not now: nothing is sent, the typed message stays in the box, and the panel does not move."""

        self._privacy_on_accept = None
        self.composer.show_warning(
            self.tr("Nothing was sent. Send again to read the notice."))

    def _on_privacy_closed(self, _result: int) -> None:
        dialog = self._privacy_dialog
        self._privacy_dialog = None
        if dialog is not None:
            dialog.deleteLater()

    @slot_guard("panel_stop")
    def _on_stop(self) -> None:
        self.stop_requested.emit(self._current_run or self._last_run or "")

    def _on_attachments_changed(self) -> None:
        count = len(self.composer.attachments())
        if count > self._attachment_count:
            self.attachment_added.emit(count - self._attachment_count)
        self._attachment_count = count

    def _on_dashboard(self) -> None:
        self.dashboard_requested.emit()
        open_external_url(get_dashboard_url(), parent=self)

    def _on_upgrade(self) -> None:
        """Every Upgrade in the panel goes to the site, nowhere in between."""






        self.upgrade_requested.emit()
        _used, _limit, _period_end, is_subscriber = self._usage
        open_external_url(get_dashboard_url() if is_subscriber else get_upgrade_url(), parent=self)

    def open_help(self, kind: str) -> None:
        """The Help page of the settings dialog calls this: one entry point for the tutorial, the shortcuts, contact and the problem report."""

        self._on_help(kind)

    @slot_guard("panel_help")
    def _on_help(self, kind: str) -> None:
        self.help_requested.emit(kind)
        if kind == "tutorial":
            open_external_url(get_tutorial_url(), parent=self)
        elif kind == "shortcuts":
            show_shortcuts_dialog(self)
        elif kind == "contact":
            show_contact_dialog(self, get_support_email(), get_contact_call_url())
        elif kind == "report":
            from .error_report_dialog import show_error_report
            show_error_report(self, self._last_error, self._current_run or self._last_run)



    def dragEnterEvent(self, event):  # noqa: N802 - Qt override
        mime = event.mimeData()
        if self._signed_in and self.composer.accepts_mime(mime):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self._drop_overlay.show_over(self, "layers" if mime_has_layers(mime) else "paperclip")
        else:
            event.ignore()

    def dragMoveEvent(self, event):  # noqa: N802 - Qt override
        mime = event.mimeData()
        if self._signed_in and self.composer.accepts_mime(mime):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):  # noqa: N802 - Qt override
        self._drop_overlay.hide()
        event.accept()

    def dropEvent(self, event):  # noqa: N802 - Qt override
        self._drop_overlay.hide()
        if self.composer.take_drop(event.mimeData()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._drop_overlay.sync_geometry(self)








    def _centre_host(self) -> QWidget:
        """The row the question sits in: the thread column once the sidebar has moved the list and the empty state into it, the empty state before."""

        host = getattr(self, "_thread_host", None)
        return host if host is not None else self.empty_state

    def _centre_composer(self, empty: bool, animate: bool = True) -> None:
        """Hold the box in the middle of an empty chat, or let it fall."""













        col = self._col
        spacer = self._center_spacer
        spacer_index = col.indexOf(spacer)
        host_index = col.indexOf(self._centre_host())
        if spacer_index < 0:
            return
        self._center_anim.stop()
        if empty:
            spacer.setMinimumHeight(0)
            spacer.setMaximumHeight(_MAX_HEIGHT)
            if host_index >= 0:
                col.setStretch(host_index, _CENTER_ABOVE)
            col.setStretch(spacer_index, _CENTER_BELOW)
            return
        if host_index >= 0:
            col.setStretch(host_index, 1)
        col.setStretch(spacer_index, 0)
        start = spacer.height()
        if start <= 0 or not animate or self._reduced_motion():
            spacer.setFixedHeight(0)
            return
        spacer.setFixedHeight(start)
        self._center_anim.setStartValue(start)
        self._center_anim.setEndValue(0)
        self._center_anim.start()

    def _reduced_motion(self) -> bool:
        """Qt has no reduced-motion flag; a hidden panel animates nothing."""
        return not self.isVisible()





    def _show_thread_surface(self) -> None:
        """Sign-in card while signed out; else the thread or the empty state."""
        signed_in = self._signed_in


        gated = self.update_gate.isVisibleTo(self)
        self.activation.setVisible(not signed_in and not gated)
        empty = self.message_list.is_empty()
        self.message_list.setVisible(signed_in and not empty and not gated)
        self.empty_state.setVisible(signed_in and empty and not gated)
        self._empty_rows.setVisible(signed_in and empty and not gated)
        self._composer_host.setVisible(signed_in and not gated)
        self.notice_bar.setVisible(not gated and bool(self.notice_bar.notice_id()))
        if gated:
            self._quota_host.hide()
            self.update_banner.hide()
            sidebar = getattr(self, "sidebar", None)
            if sidebar is not None:
                sidebar.sync_visible(False)
            return



        sidebar = getattr(self, "sidebar", None)
        if sidebar is None:
            from .sidebar import install_sidebar

            self.sidebar = sidebar = install_sidebar(self)
            sidebar.new_thread_requested.connect(self.new_thread_requested.emit)
            sidebar.home_requested.connect(self.new_thread_requested.emit)
            sidebar.search_requested.connect(lambda: self.header.history_menu().show_over(self))
            sidebar.thread_selected.connect(self.thread_selected.emit)
            sidebar.upgrade_requested.connect(self._on_upgrade)
            self.header.set_sidebar(sidebar)
        sidebar.sync_visible(signed_in)





        self.composer.set_placeholder(self.tr("Give the AI agent a task in QGIS..."))
        self.composer.set_empty_chat(empty)
        self._apply_quota()

        centred = signed_in and empty
        self.empty_state.set_centred(centred)
        self._centre_composer(centred, animate=signed_in)

    def _apply_quota(self) -> None:
        """The balance decides what the composer may do (AI Segmentation's gate): a spent grant or month blocks it and says why, a free account."""


        used, limit, period_end, is_subscriber = self._usage
        if not self._signed_in or limit <= 0:
            self.quota_card.clear()
            self._quota_host.hide()
            self.composer.set_blocked(False)
            self._low_balance_visible = False
            return
        left = limit - used
        if left <= 0:
            self.quota_card.show_exhausted(
                is_subscriber, limit, format_reset_date(period_end) if is_subscriber else "")
            self.composer.set_blocked(True, self.tr("More runs next month") if is_subscriber
                                      else self.tr("Upgrade to Pro to keep going"))
            self._quota_host.show()
            self._low_balance_visible = False
        else:
            self.quota_card.clear()
            self._quota_host.hide()
            self.composer.set_blocked(False)



            low = not is_subscriber and left <= max(1, int(limit * 0.2))
            if low and not self._low_balance_visible:
                self.low_balance_shown.emit(left)
            self._low_balance_visible = low

    def _run(self, run_id: str) -> _Run:
        run = self._runs.get(run_id)
        if run is None:
            run = _Run(run_id)
            self._runs[run_id] = run
        return run

    def _live_run(self, run_id: str) -> _Run | None:
        """Return the run currently allowed to mutate the visible thread."""







        run_id = str(run_id or "")
        if not run_id or run_id != self._current_run:
            return None
        return self._runs.get(run_id)

    def _wire_history_keys(self) -> None:
        """Panel-scoped keys, never QGIS's Ctrl+Z (that one is the active layer's edit buffer): back, forward, the History sheet, and Escape to stop."""


        context = Qt.ShortcutContext.WidgetWithChildrenShortcut
        for keys, slot in (("Ctrl+Alt+Z", self.step_back), ("Ctrl+Alt+Shift+Z", self.step_forward),
                           ("Ctrl+Alt+H", self.open_checkpoints),
                           ("Esc", self._stop_if_running)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(context)
            shortcut.activated.connect(slot)

    def _stop_if_running(self) -> None:
        """Escape stops the run; with nothing running it does nothing, so the key never steals a plain Escape from a popup or a dialog."""

        if self._current_run:
            self._on_stop()

    def _history_neighbour(self, step: int) -> str:
        """The id of the available entry ``step`` away from the current one."""
        entries = [e for e in self._history if isinstance(e, dict)]
        at = next((i for i, e in enumerate(entries) if e.get("current")), None)
        if at is None:
            at = len(entries) if step < 0 else -1
        candidates = entries[:at][::-1] if step < 0 else entries[at + 1:]
        for entry in candidates:
            if entry.get("available", True):
                return str(entry.get("id") or "")
        return ""

    def step_back(self) -> None:
        if self._current_run is not None:
            return
        cid = self._history_neighbour(-1)
        if cid:
            self.restore_requested.emit(cid, False)

    def step_forward(self) -> None:
        if self._current_run is not None:
            return
        cid = self._history_neighbour(1)
        if cid:
            self.restore_requested.emit(cid, False)

    def focus_composer(self) -> None:
        """Put the caret in the box: opening the panel is asking to type."""
        if self._signed_in and not self.composer.is_blocked():
            self.composer.focus_input()

    def showEvent(self, event):  # noqa: N802 - Qt override
        super().showEvent(event)

        QTimer.singleShot(0, self.focus_composer)




    def _add(self, widget: QWidget, animate: bool = True) -> None:
        """Append to the thread, keeping the status line last."""
        self.message_list.add_widget(widget, before=self._status, animate=animate)
        if self._signed_in:
            self.message_list.setVisible(True)
            self.empty_state.setVisible(False)

    def _add_user(self, bubble: UserBubble) -> None:
        """A user turn, with a hairline above it after the first one."""

        if self.message_list.last_widget(ignore=self._status) is not None:
            self._add(_turn_divider())




        bubble.layer_clicked.connect(self._on_bubble_layer)
        self._add(bubble)

    def _on_bubble_layer(self, layer_id: str) -> None:
        if layer_id:
            self.layer_action_requested.emit(layer_id, "show")

    def _bubble_for(self, run_id: str) -> AgentBubble:
        """The bubble the run's text streams into: the one at the bottom of the list, else a new one."""



        run = self._run(run_id)
        last = self.message_list.last_widget(ignore=self._status)
        if run.bubble is None or last is not run.bubble:
            if run.trace is not None and last is run.trace:
                run.trace.close()
            run.bubble = AgentBubble()
            run.bubble.link_activated.connect(self._on_bubble_link)
            self._add(run.bubble)
        return run.bubble

    def _trace_for(self, run_id: str) -> RunTrace:
        """The activity block the run's next call or plan goes into: the one at the bottom of the list, else a new one under whatever the agent just."""




        run = self._run(run_id)
        last = self.message_list.last_widget(ignore=self._status)
        if run.trace is None or last is not run.trace:
            self._drop_status()
            if run.trace is not None:
                run.trace.close()
            run.trace = RunTrace(run_id, started=time.monotonic(),
                                 reduced_motion=self._reduced_motion)
            self.message_list.register_trace(run_id, run.trace)
            self._add(run.trace)
            if not self._show_tool_details:
                run.trace.hide()
        return run.trace

    def _plan_block(self, run_id: str):
        """The block of the run that holds its plan, or None."""
        for block in self.message_list.traces_of(run_id):
            if block.plan is not None:
                return block
        return None

    def _drop_status(self) -> None:
        if self._status is not None:
            self.message_list.remove_widget(self._status)
            self._status = None





    def set_connection_state(self, state: str, detail: str = "") -> None:
        """What the socket is doing, and what of it reaches the reader: as little as possible."""









        state = str(state or "").strip().lower()
        if state not in {"connecting", "online", "offline", "signed_out", "error"}:
            state = "error"
        detail = str(detail or "")[:500]
        self._connection = state
        if state == "signed_out":
            self._signed_in = False
            self.header.clear_account()


            self.activation.show_signed_out(detail or "")
            self._show_thread_surface()
            return
        if state == "online":
            self._signed_in = True
            self.activation.pairing_finished()
            self.activation.clear_message()
            self.composer.set_offline(False)
            self._show_thread_surface()
            return
        if not self._signed_in:


            if state == "error" and detail:
                self.activation.set_message(detail, "error")
            return
        self.composer.set_offline(True)

    def set_model_label(self, label: str) -> None:
        """Kept for the controller; nothing is shown (owner's decision)."""
        self._model_label = label or ""

    def set_usage(self, runs_used: int, runs_limit: int, period_end_iso: str = "",
                  is_subscriber=None) -> None:
        """The balance."""

        try:
            used = max(0, int(runs_used or 0))
        except (TypeError, ValueError, OverflowError):
            used = 0
        try:
            limit = max(0, int(runs_limit or 0))
        except (TypeError, ValueError, OverflowError):
            limit = 0
        if is_subscriber is None:
            is_subscriber = limit > get_free_runs()
        self._usage = (used, limit, str(period_end_iso or "")[:80], bool(is_subscriber))
        self._apply_quota()
        sidebar = getattr(self, "sidebar", None)
        if sidebar is not None:
            sidebar.set_paid(bool(is_subscriber))

    def set_welcome_visible(self, visible: bool) -> None:
        self.empty_state.set_welcome_visible(visible)

    def set_suggestions(self, items) -> None:
        self.empty_state.set_suggestions([str(item)[:500] for item in list(items or [])[:12]
                                         if isinstance(item, str) and item.strip()])

    def set_threads(self, items, current_project_path: str | None = None) -> None:
        self.header.set_threads([item for item in list(items or [])[:200]
                                 if isinstance(item, dict)], str(current_project_path or ""))

    def set_current_project(self, path: str) -> None:
        self.header.set_current_project(path or "")

    def set_display_options(self, explain_runs: bool, show_tool_details: bool) -> None:
        """Settings > General: the Done card after a run, the activity block."""
        self._explain_runs = bool(explain_runs)
        self._show_tool_details = bool(show_tool_details)

    def set_current_thread(self, thread_id: str | None) -> None:
        self.header.set_current_thread(thread_id or "")





    def begin_run(self, run_id: str) -> None:
        run_id = str(run_id or "").strip()
        if not run_id:
            return
        self._run(run_id).started = time.monotonic()
        self._current_run = run_id
        self._last_run = run_id
        self._changed_layers = []
        self._drop_status()
        self._status = StatusLine(self.tr("Thinking"), started=self._run(run_id).started,
                                  reduced_motion=self._reduced_motion)
        self.message_list.add_widget(self._status)
        self.composer.set_running(True)



        self._changed_count = 0
        self._show_thread_surface()

    def append_user_message(self, run_id: str, text: str, chips, attachments) -> None:
        if self._live_run(run_id) is None:
            return
        self._last_run = run_id or self._last_run
        self._add_user(UserBubble(text, list(chips or []), list(attachments or [])))
        self._show_thread_surface()

        QTimer.singleShot(0, self.message_list.scroll_to_bottom)

    def append_token(self, run_id: str, text: str) -> None:
        if not isinstance(text, str) or not text:
            return
        run = self._live_run(run_id)
        if run is None:
            return
        before = run.bubble
        bubble = self._bubble_for(run_id)
        if bubble is not before:



            self._wire_answer(bubble, run_id)
        if run.segment_break:
            run.segment_break = False
            current = bubble.text()
            if current and not current.endswith("\n"):

                text = "\n\n" + text.lstrip()
        bubble.append_token(text)

    def set_status_line(self, run_id: str, text: str) -> None:
        text = str(text or "").strip()[:300] or self.tr("Thinking...")


        run = self._live_run(run_id)
        if run is None:
            return
        block = run.trace if run is not None else None
        if (block is not None and block.is_live()
                and self.message_list.last_widget(ignore=self._status) is block):
            block.set_activity(text)
            return
        if self._status is None:

            self._status = StatusLine(text, started=self._run(run_id).started,
                                      reduced_motion=self._reduced_motion)
            self.message_list.add_widget(self._status)
        else:
            self._status.set_text(text)

    def set_plan(self, run_id: str, steps) -> None:
        if self._live_run(run_id) is None:
            return


        block = self._plan_block(run_id) or self._trace_for(run_id)
        block.set_plan(list(steps or []))

    def update_plan_step(self, run_id: str, step_id: str, state: str) -> None:
        if self._live_run(run_id) is None:
            return
        block = self._plan_block(run_id)
        if block is not None:
            block.plan.update_step(step_id, state)

    def add_tool_call(self, tool_call_id: str, run_id: str, name: str, args,
                      danger: str, sentence: str) -> None:
        run = self._live_run(run_id)
        if run is None:
            return
        run.segment_break = True
        args = dict(args) if isinstance(args, dict) else {}
        tool_call_id = str(tool_call_id or "")[:256]
        name = str(name or "")[:200]
        sentence = str(sentence or "")[:1000]
        trace = self._trace_for(run_id)


        counts = getattr(self, "_run_counts", None)
        if counts is None:
            counts = self._run_counts = {}
        count = counts.setdefault(run_id, {"tools": 0, "messages": 0})
        if run.bubble is not None and run.bubble.text().strip():
            count["messages"] += 1
        last = trace.last_tool()
        if last is not None and last.same_call(name, args) and last.can_fold():



            last.add_repeat(tool_call_id)
            self.message_list.register_tool_card(tool_call_id, last)
            run.tools.append(last)
            count["tools"] += 1
            self.set_run_counts(run_id, count["tools"], count["messages"])
            return
        card = ToolCard(tool_call_id, name, args, danger, sentence,
                        animate=not self._reduced_motion())
        self.message_list.register_tool_card(tool_call_id, card)
        run.tools.append(card)
        trace.add_tool(card)
        count["tools"] += 1
        self.set_run_counts(run_id, count["tools"], count["messages"])

    def set_run_counts(self, run_id: str, tool_calls: int, messages: int) -> None:
        """The numbers of the group head above a run's tool rows, ``4 tool calls, 2 messages``."""




        trace = self.message_list.trace(run_id)
        if trace is None:
            return
        setter = getattr(trace, "set_run_counts", None)
        if callable(setter):
            setter(int(tool_calls or 0), int(messages or 0))
            return
        group = getattr(trace, "tool_group", None)
        setter = getattr(group, "set_counts", None)
        if callable(setter):
            setter(int(tool_calls or 0), int(messages or 0))

    def finish_tool_call(self, tool_call_id: str, ok: bool, summary: str,
                         duration_s: float, detail: str = "", result=None) -> None:
        """``result`` is the dict the tool handed back, or None."""











        try:
            duration_s = max(0.0, float(duration_s or 0.0))
        except (TypeError, ValueError, OverflowError):
            duration_s = 0.0
        card = self.message_list.tool_card(str(tool_call_id or ""))
        if card is not None:
            card.finish(bool(ok), str(summary or "")[:1000], duration_s, str(detail or "")[:2000])

    def ask_permission(self, tool_call_id: str, run_id: str, sentence: str, args) -> None:
        run = self._live_run(run_id)
        if run is None:







            self.permission_decided.emit(tool_call_id, "deny", None)
            return
        tool = self.message_list.tool_card(tool_call_id)
        name = tool.name if tool is not None else ""





        if tool is not None:
            self._reopened_tools[tool_call_id] = tool.is_expanded()
            tool.set_expanded(True)


            reveal = getattr(tool, "reveal_source", None)
            if callable(reveal):
                reveal(True)
        card = PermissionCard(tool_call_id, sentence, dict(args or {}), name=name)
        card.decided.connect(self.permission_decided.emit)
        self.set_status_line(run_id, self.tr("Waiting for your answer..."))
        run.wait_started = time.monotonic()
        self.message_list.register_permission_card(tool_call_id, card)
        run.permissions.append(card)
        self._add(card)

    def ask_question(self, tool_call_id: str, run_id: str, question: str, options,
                     allow_free_text: bool, recommended: int = -1, why: str = "",
                     timeout_s: int = 0, multiple: bool = False) -> None:
        """``recommended`` is the index of the option the agent suggests (-1 none), ``why`` its one-clause reason, ``timeout_s`` the seconds before."""








        run = self._live_run(run_id)
        if run is None:







            self.question_answered.emit(tool_call_id, "")
            return
        open_card = next((c for c in reversed(run.permissions)
                          if isinstance(c, QuestionCard) and c.is_open()), None)
        if open_card is not None:
            open_card.add_page(tool_call_id, question, list(options or []), allow_free_text,
                               recommended=recommended, why=why, timeout_s=timeout_s,
                               multiple=multiple)
            self.message_list.register_permission_card(tool_call_id, open_card)
            return
        card = QuestionCard(tool_call_id, question, list(options or []), allow_free_text,
                            recommended=recommended, why=why, timeout_s=timeout_s,
                            multiple=multiple)
        card.answered.connect(self.question_answered.emit)
        card.auto_answered.connect(self.question_auto_answered.emit)
        self.set_status_line(run_id, self.tr("Waiting for your answer..."))
        run.wait_started = time.monotonic()
        self.message_list.register_permission_card(tool_call_id, card)
        run.permissions.append(card)
        self._add(card)
        card.focus()

    def offer_cleanup(self, run_id: str, items) -> None:
        """The working layers a finished run left behind, offered back."""





        items = [i for i in list(items or []) if isinstance(i, dict)]
        if not items:
            return
        from .cards_cleanup import CleanupCard
        card = CleanupCard(str(run_id or ""), items, self.message_list)
        card.decided.connect(self.cleanup_decided.emit)
        self._cleanup_cards[str(run_id or "")] = card
        self._add(card)

    def offer_plugin(self, offer_id: str, title: str, body: str, action: str) -> None:
        """A QGIS plugin the finished run wanted and did not find, offered once."""





        offer_id = str(offer_id or "")
        if not offer_id or offer_id in self._offer_cards:
            return
        from .cards_plugin_offer import PluginOfferCard
        card = PluginOfferCard(offer_id, str(title or ""), str(body or ""), str(action or ""),
                               self.message_list)
        card.decided.connect(self.plugin_offer_decided.emit)
        self._offer_cards[offer_id] = card
        self._add(card)

    def finish_plugin_offer(self, offer_id: str, sentence: str = "") -> None:
        """What came of it, folded onto the card."""
        card = self._offer_cards.pop(str(offer_id or ""), None)
        if card is None:
            return
        try:
            card.collapse(str(sentence or ""))
        except RuntimeError:
            pass

    def finish_cleanup(self, run_id: str, sentence: str = "") -> None:
        """What the controller did with them, folded onto the card."""
        card = self._cleanup_cards.pop(str(run_id or ""), None)
        if card is None:
            return
        try:
            card.collapse(str(sentence or ""))
        except RuntimeError:
            pass

    def ask_recommendation(self, tool_call_id: str, run_id: str, title: str, proposal: str,
                           entity: str = "", value: str = "", confidence: str = "high",
                           alternatives=None) -> None:
        """The run proposes a heavy step: the recommendation card, whose Accept, an alternative or the x emit ``recommendation_decided``."""


        from .cards_proposal import RecommendationCard

        run = self._live_run(run_id)
        if run is None:







            self.recommendation_decided.emit(tool_call_id, "dismiss")
            return
        card = RecommendationCard(tool_call_id, run_id, title, proposal, entity, value,
                                  confidence, list(alternatives or []))
        card.decided.connect(self._on_recommendation_decided)
        self.set_status_line(run_id, self.tr("Waiting for your answer..."))
        run.wait_started = time.monotonic()
        self.message_list.register_permission_card(tool_call_id, card)
        run.permissions.append(card)
        self._add(card)

    def _on_recommendation_decided(self, tool_call_id: str, decision: str) -> None:
        self._card_answered(tool_call_id)
        self.recommendation_decided.emit(tool_call_id, decision)

    def _card_answered(self, tool_call_id: str) -> None:
        """The bookkeeping every card the panel answers shares: the time the user spent on it leaves the run's duration, and the status line stops."""

        card = self.message_list.permission_card(tool_call_id)
        for run in self._runs.values():
            if card is not None and card in run.permissions and run.wait_started:
                run.waited += time.monotonic() - run.wait_started
                run.wait_started = 0.0
                break
        if self._status is not None and self._current_run:
            self._status.set_text(self.tr("Thinking..."))

    def show_diff_table(self, run_id: str, title: str, columns, rows) -> None:
        """The attribute edits a run proposes, as the diff table; Apply emits ``diff_applied`` with the ids of the rows still checked."""



        from .cards_proposal import DiffTableCard

        card = DiffTableCard(run_id, title, list(columns or []), list(rows or []))
        card.applied.connect(self.diff_applied.emit)
        self._add(card)

    def _restore_tool_card(self, tool_call_id: str) -> None:
        """Put a call opened for its permission card back as the reader had it."""
        was_open = self._reopened_tools.pop(tool_call_id, None)
        if was_open is None:
            return
        tool = self.message_list.tool_card(tool_call_id)
        if tool is not None:
            reveal = getattr(tool, "reveal_source", None)
            if callable(reveal):
                reveal(False)
            tool.set_expanded(bool(was_open))

    def resolve_question(self, tool_call_id: str, answer: str) -> None:
        card = self.message_list.permission_card(tool_call_id)
        if card is None:
            return
        if hasattr(card, "is_answered"):
            if not card.is_answered(tool_call_id):
                card.collapse(answer, tool_call_id)

            if card.is_open():
                return
        elif getattr(card, "answer", None) is None:
            card.collapse(answer)
        if self._status is not None and self._current_run:
            self._status.set_text(self.tr("Thinking..."))
        for run in self._runs.values():
            if card in run.permissions and run.wait_started:
                run.waited += time.monotonic() - run.wait_started
                run.wait_started = 0.0
                break

    def resolve_permission(self, tool_call_id: str, decision: str) -> None:
        self._restore_tool_card(tool_call_id)
        card = self.message_list.permission_card(tool_call_id)
        if card is None:
            return
        if card.decision is None:
            card.collapse(decision)
        if self._status is not None and self._current_run:
            self._status.set_text(self.tr("Thinking..."))


        run_id = ""
        for run in self._runs.values():
            if card in run.permissions:
                run_id = run.run_id
                if run.wait_started:
                    run.waited += time.monotonic() - run.wait_started
                    run.wait_started = 0.0
                break
        if run_id:


            if decision == "deny":
                self._trace_for(run_id).add_note(
                    f"{card.decision_text()}: {card.sentence}", failed=True)

            if hasattr(card, "cleanup"):
                card.cleanup()
            self.message_list.remove_widget(card)
            for run in self._runs.values():
                if card in run.permissions:
                    run.permissions.remove(card)

    def show_error(self, run_id, code: str, message: str, retryable: bool,
                   details: str = "") -> None:
        run_id = str(run_id or "")[:256]
        if run_id and self._live_run(run_id) is None:
            return
        code = str(code or "UNKNOWN")[:100]
        message = str(message or "")[:2000]
        details = str(details or "")[:4000]
        self._last_error = message or code



        self._error_said[run_id or ""] = (message or "").strip()


        card = ErrorCard(run_id or "", code, message, retryable, details,
                         continuable=str(code or "") in StopCode.RESUMABLE)
        card.retry_requested.connect(self.retry_requested.emit)
        card.continue_requested.connect(self.continue_requested.emit)
        self._add(card)

    def end_run(self, run_id: str, status: str, summary: str, usage, verification) -> None:
        run = self._live_run(run_id)
        if run is None:
            return
        if run is not None and run.bubble is not None:
            run.bubble.flush()
        usage = usage if isinstance(usage, dict) else {}
        seconds = usage.get("duration_s", usage.get("elapsed_s"))
        if seconds is None and run is not None:
            seconds = time.monotonic() - run.started
        try:
            seconds = max(0.0, float(seconds)) if seconds is not None else None
        except (TypeError, ValueError, OverflowError):
            seconds = time.monotonic() - run.started if run is not None else 0.0
        if run is not None and seconds is not None:
            seconds = max(0.0, float(seconds) - run.waited)
        blocks = self.message_list.traces_of(run_id)
        streamed = run.bubble.text().strip() if run is not None and run.bubble is not None else ""
        summary = (summary or "").strip()
        if streamed:

            summary = ""
        elif summary and status == "done":

            self._bubble_for(run_id).set_text(summary)
            summary = ""
        for block in blocks:


            if not block.is_empty():
                block.finish(status, seconds)


        lines = RunSummaryCard._verification_lines(verification, with_changes=self._changed_count <= 0)
        if status == "done":
            lines = [line for line in lines if not _is_nothing_changed(line)]
        said = self._error_said.pop(run_id, "")





        repeat = bool(said) and (said == (summary or "").strip() or not (summary or lines))
        if not repeat and (status != "done" or (self._explain_runs and (summary or lines))):
            self._add(RunSummaryCard(status, summary, usage, {"lines": lines} if lines else None,
                                     duration_s=seconds))
        if run is not None and run.bubble is not None:
            run.bubble.finish_streaming()
        self._add_layer_links(run_id)
        self._nudge_long_chat(usage)
        self._drop_status()
        if self._current_run == run_id or self._current_run is None:
            self._current_run = None
            self.composer.set_running(False)

    def _on_bubble_link(self, href: str) -> None:
        """A link inside an answer: a layer opens in QGIS, a data or document file the run wrote opens with the desktop's own handler, and anything."""








        if href.startswith("source:"):

            return
        layer_id = layer_id_from_url(href)
        if layer_id:
            self.layer_action_requested.emit(layer_id, "show")
            return
        path = path_from_url(href)
        if not path:
            return
        if is_safe_to_open(path):
            open_local_path(path, self)
            return
        open_local_path(reveal_target(path), self)

    def _answer_bubble(self, run_id: str) -> AgentBubble | None:
        """The run's last bubble, for what arrives after the answer."""
        run = self._runs.get(run_id)
        if run is not None and run.bubble is not None:
            return run.bubble
        for widget in reversed(self.message_list.widgets()):
            if isinstance(widget, AgentBubble):
                return widget
        return None

    def _wire_answer(self, bubble: AgentBubble, run_id: str) -> None:
        """The action row's signals, with the run they belong to. Once per bubble."""
        if bubble.property("wired"):
            return
        bubble.setProperty("wired", True)
        bubble.retry_requested.connect(lambda: self.retry_requested.emit(run_id))
        bubble.feedback.connect(lambda up: self.feedback.emit(run_id, bool(up)))


        bubble.message_requested.connect(self.suggestion_clicked.emit)

    def _set_step_line(self, bubble: AgentBubble, run: _Run) -> None:
        """The plan step active as the answer starts: its name, the last tool it reached (a connector or a tool, humanised), and the run's time so far."""


        trace = self._plan_block(run.run_id)
        if trace is None:
            return
        name = trace.plan.active_label()
        if not name:
            return
        last = run.trace.last_tool() if run.trace is not None else None
        source = humanise_tool_name(last.name) if last is not None else ""
        seconds = max(0.0, time.monotonic() - run.started - run.waited)
        bubble.set_step_line(name, source, seconds)

    def set_sources(self, run_id: str, items) -> None:
        """``[{name, url, glyph?}]``: the stacked marks and ``N sources`` on the answer's action row, with the popover that lists them."""

        if run_id not in self._runs:
            return
        bubble = self._answer_bubble(run_id)
        if bubble is None:
            return
        self._wire_answer(bubble, run_id)
        bubble.set_sources([x for x in list(items or [])[:50] if isinstance(x, dict)])

    def _add_layer_links(self, run_id: str = "") -> None:
        """The layers the run changed: links in its prose, chips under it."""
        layers, self._changed_layers = self._changed_layers, []
        run = self._runs.get(run_id)
        bubble = run.bubble if run is not None else None

        count = (getattr(self, "_run_counts", None) or {}).get(run_id)
        if count is not None and bubble is not None and bubble.text().strip():
            count["messages"] += 1
            self.set_run_counts(run_id, count["tools"], count["messages"])
        if not layers:
            return
        if bubble is not None:
            linked = linkify_layers(bubble.text(), layers)
            if linked != bubble.text():
                bubble.set_text(linked)

        group = getattr(run.trace if run is not None else None, "tool_group", None)
        if callable(getattr(group, "set_changes", None)):
            group.set_changes(layers)
        row = LayerLinkRow(layers)
        row.layer_action_requested.connect(self.layer_action_requested.emit)
        self._add(row)

    def _nudge_long_chat(self, usage: dict) -> None:
        """Past 70 % of the history budget, one muted line, once per thread: the server folds old turns from there on, so answers lose detail."""

        try:
            used, budget = float(usage.get("context_tokens") or 0), float(usage.get("context_budget") or 0)
        except (TypeError, ValueError):
            return
        if budget <= 0 or used / budget < _LONG_CHAT_SHARE or self._long_chat_nudged:
            return
        self._long_chat_nudged = True
        note = RunFootnote()
        note.setWordWrap(True)
        note.setText(self.tr("Long chat: older messages get folded from here. "
                             "For a new topic, start a new thread (+ in the header)."))
        note.show()
        self._add(note)

    def set_run_changes(self, changed_layers: int, restore_available: bool,
                        layers: list | None = None) -> None:
        """What the run in progress has changed so far."""








        self._changed_count = int(changed_layers or 0)
        self._changed_layers = [x for x in (layers or []) if isinstance(x, dict)]
        if restore_available:
            self.header.set_restore_available(True)

    def set_history(self, entries: list) -> None:
        """The checkpoints of this chat, oldest first (core/checkpoints.describe)."""



        all_entries = [e for e in list(entries or []) if isinstance(e, dict)]


        self._history = all_entries[-200:]
        self.header.set_checkpoints(self._history)

    def note_project_state(self, text: str) -> None:
        """Where the project stands after a restore ("Back to before run 2")."""





        text = str(text or "").strip()
        if not text:
            return
        note = RunFootnote()
        note.setWordWrap(True)
        note.setText(text)
        note.show()
        self._add(note)
        self.message_list.scroll_to_bottom()

    def note_memory(self, text: str) -> None:
        """What the run just added to the memory, said where it happened."""








        text = str(text or "").strip()
        if not text:
            return
        note = RunFootnote()
        note.setWordWrap(True)


        note.setText(self.tr("Noted for later: {0}. You can remove it in Settings > Memory.")
                     .format(text.rstrip(" .!?;,")))
        note.show()
        self._add(note)
        self.message_list.scroll_to_bottom()

    def open_checkpoints(self) -> None:
        """The restore sheet, under the header's undo button."""






        self.header.open_checkpoints()

    def show_restore_warning(self, checkpoint_id: str, discard: bool = False, edits: bool = False,
                             whole: bool = True) -> None:
        card = RestoreWarningCard(checkpoint_id, discard, edits, whole)
        card.confirmed.connect(self._on_restore_confirmed)
        self._add(card)
        self.message_list.scroll_to_bottom()

    def _on_restore_confirmed(self, checkpoint_id: str, discard: bool) -> None:
        if discard:
            self.discard_all_requested.emit(True)
        else:
            self.restore_requested.emit(checkpoint_id, True)

    def show_quota_pause(self, run_id: str, message: str) -> None:
        card = QuotaPauseCard(run_id, message)
        card.resume_requested.connect(self.retry_requested.emit)
        self._add(card)





    def clear_thread(self) -> None:
        self._replay_generation += 1
        self.message_list.clear()
        self._runs = {}


        self._cleanup_cards.clear()
        self._offer_cards.clear()
        if hasattr(self, "_run_counts"):
            self._run_counts.clear()
        self._reopened_tools = {}
        self._status = None
        self._current_run = None
        self.composer.set_running(False)

        self.composer.clear_chips()
        self._changed_count = 0
        self._changed_layers = []
        self.set_history([])
        self._long_chat_nudged = False
        self._show_thread_surface()

    def load_thread(self, messages) -> None:
        """Replay a stored thread (shape in core/threads.py; see thread_replay)."""







        self.clear_thread()
        steps = replay_steps(self, messages)
        generation = self._replay_generation
        run_sliced(steps, REPLAY_SLICE_MS,
                   still_wanted=lambda: generation == self._replay_generation,
                   around=self._replay_quiet, on_done=self._replay_done)

    @contextlib.contextmanager
    def _replay_quiet(self):
        """One paint per slice, not one per widget, no full garbage collection in the middle of a hundred cards, and the bottom in view after it."""

        from ..core.background import gc_paused

        self.message_list.setUpdatesEnabled(False)
        try:
            with gc_paused():
                yield
        finally:
            self.message_list.setUpdatesEnabled(True)

    def _replay_done(self) -> None:
        """The end of a replay: the thread surface, and the bottom in view."""
        self._show_thread_surface()
        self.message_list.scroll_to_bottom()





    def set_mentions(self, items) -> None:
        """Fill the composer's ``@`` popup: ``[{kind, label, value}]``."""
        self.composer.set_mentions(list(items or []))

    def set_mention_provider(self, provider) -> None:
        """``provider() -> [{kind, label, value}]``, asked on every ``@``."""
        self.composer.set_mention_provider(provider)

    def set_permission_mode(self, approval: str) -> None:
        """Show the settings' approval on the composer chip."""
        self.composer.set_permission_mode(approval)

    def set_effort(self, effort: str) -> None:
        """Show the settings' effort level on the composer chip."""
        self.composer.set_effort(effort)

    def set_paid_plan(self, paid: bool) -> None:
        """Whether the account may pick the paid efforts and Autopilot; a free one sees Upgrade."""
        self.composer.set_paid_plan(paid)

    def add_context_chip(self, chip: dict) -> None:
        """A layer card above the composer, for the next message only."""
        self.composer.add_chip(chip)

    def set_pairing_state(self, waiting: bool, code: str = "", url: str = "") -> None:
        """Browser sign-in in progress (with the code, when there is one)."""
        if waiting:
            self.activation.show_pairing_waiting(code, url)
        else:
            self.activation.show_signed_out()

    def set_pairing_status(self, text: str) -> None:
        """The line beside the sign-in spinner."""
        self.activation.set_pairing_status(text)

    def set_pairing_note(self, text: str, kind: str = "warning") -> None:
        """A line the waiting user must act on, inside the sign-in card: the message list is hidden while signed out, so an error card goes nowhere."""

        self.activation.set_pairing_note(text, kind)

    def set_account(self, email: str, avatar_url: str = "") -> None:
        self.header.set_account(email, avatar_url)
        self.activation.show_activated(email, avatar_url)

    def show_notice(self, notice: dict) -> None:
        """The session notice, or none: the bar hides itself on an empty dict."""
        if notice:
            self.notice_bar.show_notice(notice)
        else:
            self.notice_bar.clear()

    def show_update(self, version: str, note: str = "", required: bool = False,
                    installed: str = "") -> None:
        """Offer an installable version: a banner with Later, or, when ``required``, the card that replaces the chat until it is done."""

        if required:
            self.update_banner.hide()
            self.update_gate.offer(version, note, installed)
        else:
            self.update_gate.hide()
            self.update_banner.offer(version, note)
        self._show_thread_surface()

    def clear_update(self) -> None:
        """No update to offer any more: the chat comes back."""
        was_gated = self.update_gate.isVisibleTo(self)
        self.update_gate.hide()
        self.update_banner.hide()
        if was_gated:
            self._show_thread_surface()

    def apply_server_config(self, payload: dict) -> None:
        """Apply future served values without rebuilding the active thread."""
        self._apply_quota()

    def current_run_id(self) -> str:
        return self._current_run or ""

    def cleanup(self) -> None:


        try:
            self._center_anim.stop()
        except (RuntimeError, AttributeError):
            pass
        self.message_list.stop_animations()
        try:
            self._drop_overlay.stop_animations()
        except (RuntimeError, AttributeError):
            pass
        self.header.clear_account()
        self.composer.set_running(False)
