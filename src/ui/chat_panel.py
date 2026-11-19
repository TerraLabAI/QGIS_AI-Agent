# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later












from __future__ import annotations

from qgis.PyQt.QtCore import QEasingCurve, Qt, QVariantAnimation, pyqtSignal
from qgis.PyQt.QtWidgets import QPushButton, QSizePolicy, QVBoxLayout, QWidget

from ..core import quota_view
from ..core.settings import Settings
from .bubbles import AgentBubble, StatusLine
from .chat_panel_layout import _ChatPanelLayout
from .chat_panel_prompts import _ChatPanelPrompts
from .chat_panel_queue import TRAY_TUCK, QueueStrip, _ChatPanelQueue
from .chat_panel_runs import _ChatPanelRuns
from .chat_panel_shared import _Run, _wrap
from .chat_panel_threads import _ChatPanelThreads
from .composer import Composer
from .crash_comment_bar import CrashCommentBar
from .dock.about import UpdateBanner
from .dock.activation_state import ActivationCard
from .drop_overlay import DropOverlay
from .empty_state import EmptyState, feedback_foot
from .header import Header
from .layer_watch import LayerWatch
from .mascot_stage import MascotStage
from .message_list import MessageList
from .notice_bar import NoticeBar
from .quota_card import QuotaCard, RunsLeftLine
from .shared import CHAT_PANEL_OBJECT
from .style import MIN_PANEL_WIDTH, SPACE_CARD, SPACE_TIGHT, panel_qss
from .tasks_panel import TasksPanel
from .toast import Toast
from .update_gate import UpdateGate



_CENTER_MS = 260


class ChatPanel(_ChatPanelRuns, _ChatPanelQueue, _ChatPanelPrompts, _ChatPanelThreads, _ChatPanelLayout, QWidget):



    send_requested = pyqtSignal(str, str, str, object, object, object)
    stop_requested = pyqtSignal(str)
    task_stop_requested = pyqtSignal(str)
    crash_comment_sent = pyqtSignal(str, str)
    steer_requested = pyqtSignal(str, str, str)
    unsteer_requested = pyqtSignal(str, str)

    queue_send_requested = pyqtSignal(str, object, object, object)
    undo_retry_requested = pyqtSignal(str)



    permission_decided = pyqtSignal(str, str, object)
    folder_decided = pyqtSignal(str, str)
    folder_removed = pyqtSignal(str)
    question_answered = pyqtSignal(str, str)


    question_reply = pyqtSignal(str, str, object)
    retry_requested = pyqtSignal(str)
    continue_requested = pyqtSignal(str)
    answer_rated = pyqtSignal(str, str)
    answer_reason = pyqtSignal(str, str, str, str)
    feedback_ask_state = pyqtSignal(str, str, str)
    undo_requested = pyqtSignal()
    restore_requested = pyqtSignal(str, bool)


    restore_after_stop_requested = pyqtSignal(str)
    discard_all_requested = pyqtSignal(bool)
    history_requested = pyqtSignal()
    layer_action_requested = pyqtSignal(str, str)


    file_add_requested = pyqtSignal(str)
    new_thread_requested = pyqtSignal()
    thread_selected = pyqtSignal(str)
    thread_delete_requested = pyqtSignal(str)
    chip_removed = pyqtSignal(str, str)
    files_dropped = pyqtSignal(object)
    open_settings_requested = pyqtSignal()

    open_settings_page_requested = pyqtSignal(str)
    sign_in_requested = pyqtSignal()
    sign_out_requested = pyqtSignal()
    mode_changed = pyqtSignal(str, str)
    effort_changed = pyqtSignal(str)
    pairing_reopen_requested = pyqtSignal()
    pairing_cancel_requested = pyqtSignal()
    pairing_code_submitted = pyqtSignal(str)
    dashboard_requested = pyqtSignal()




    plans_requested = pyqtSignal(str)
    pro_contact_requested = pyqtSignal(str)


    upsell_shown = pyqtSignal(str)

    payment_card_shown = pyqtSignal(str, bool, bool)
    pay_requested = pyqtSignal(str)
    limit_reached = pyqtSignal(bool, int)
    pro_pill_requested = pyqtSignal()

    keep_pro_requested = pyqtSignal()
    start_now_requested = pyqtSignal(str)
    reconnect_requested = pyqtSignal()
    memory_undo_requested = pyqtSignal(str)
    help_requested = pyqtSignal(str)
    update_clicked = pyqtSignal(str)
    update_dismissed = pyqtSignal(str)
    update_download_clicked = pyqtSignal(str)
    low_balance_shown = pyqtSignal(int)
    attachment_added = pyqtSignal(int)
    example_chosen = pyqtSignal(str)
    recommendation_decided = pyqtSignal(str, str)
    diff_applied = pyqtSignal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName(CHAT_PANEL_OBJECT)



        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(MIN_PANEL_WIDTH)
        self.setStyleSheet(panel_qss())
        self.setAcceptDrops(True)
        self._runs: dict[str, _Run] = {}
        self._asked_run = ""
        self._replay_generation = 0


        self._reopened_tools: dict[str, bool] = {}
        self._compaction_marked = False
        self._current_run: str | None = None
        self._error_cards: dict = {}
        self._update_refused = False
        self._last_run = ""
        self._last_error = ""
        self._error_said: dict[str, str] = {}
        self._low_balance_visible = False
        self._upsell_seen: set = set()
        self._checkout_return = False
        self._return_view_pending = False

        self._low_card_closed = False
        self._quota_view = "hidden"

        self._pro_welcome = None

        self._payfail = None

        self._keep_pro = None

        self._returning = False
        self._limit_said = False
        self._attachment_count = 0
        self._privacy_dialog = None
        self._privacy_on_accept = None
        self._status: StatusLine | None = None
        self._signed_in = True
        self._connection = "online"
        self._model_label = ""

        self._usage = (0, 0, "", True)


        self._plan_known = False
        self._paid_plan = False

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
        self.toast = Toast(self.message_list)



        self._layer_watch = LayerWatch(self, self)
        self.empty_state = EmptyState(self)
        self._col.addWidget(self.empty_state, 1)

        self.notice_bar = NoticeBar(self)
        self._col.addWidget(self.notice_bar)
        self.crash_comment_bar = CrashCommentBar(self)
        self.crash_comment_bar.sent.connect(self.crash_comment_sent)
        self._col.addWidget(self.crash_comment_bar)

        self.quota_card = QuotaCard(self)
        self._quota_host = _wrap(self.quota_card, (12, SPACE_CARD, 12, 0), self)
        self._quota_host.hide()
        self._col.addWidget(self._quota_host)

        self.tasks_panel = TasksPanel(self)
        self._task_cards = {}
        self.tasks_panel.stop_requested.connect(self.task_stop_requested)
        self.tasks_panel.permission_decided.connect(self.permission_decided)
        self._tasks_host = _wrap(self.tasks_panel, (12, SPACE_CARD, 12, 0), self)
        self._col.addWidget(self._tasks_host)


        self.composer = Composer(self)
        self._composer_host = _wrap(self.composer, (12, SPACE_CARD, 12, SPACE_TIGHT), self)
        self.queue_strip = QueueStrip(self._composer_host)
        host = self._composer_host.layout()
        host.insertWidget(0, self.queue_strip)


        host.setSpacing(-TRAY_TUCK)
        self.composer.raise_()
        self._col.addWidget(self._composer_host)
        self.runs_line = RunsLeftLine(self)
        self._runs_host = _wrap(self.runs_line, (12, 0, 12, SPACE_TIGHT), self)
        self._runs_host.hide()
        self._col.addWidget(self._runs_host)



        self._empty_rows = _wrap(self.empty_state.rows_widget(), (12, 0, 12, SPACE_CARD), self)
        self._col.addWidget(self._empty_rows)






        self._center_spacer = QWidget(self)
        self._center_spacer.setObjectName("centreSpacer")
        self._center_spacer.setSizePolicy(QSizePolicy.Policy.Ignored,
                                          QSizePolicy.Policy.Ignored)
        self._center_spacer.setFixedHeight(0)
        self._col.addWidget(self._center_spacer)
        self._feedback_foot = _wrap(feedback_foot(self), (8, 0, 12, 6), self)
        self._feedback_foot.findChild(QPushButton, "feedbackFoot").clicked.connect(
            lambda: self.open_feedback("home"))
        self._col.addWidget(self._feedback_foot)
        self._center_anim = QVariantAnimation(self)
        self._center_anim.setDuration(_CENTER_MS)
        self._center_anim.setEasingCurve(QEasingCurve.Type.OutQuint)
        self._center_anim.valueChanged.connect(
            lambda value: self._center_spacer.setFixedHeight(max(0, int(value))))
        self._drop_overlay = DropOverlay(self)

        self.mascot_stage = stage = MascotStage(self, Settings())
        self.header.mark_pressed.connect(lambda: stage.fire("head.click"))
        self.composer._input.textChanged.connect(stage.rest)
        self.permission_decided.connect(self._mascot_decided)
        self.question_answered.connect(self._mascot_answered)
        self.message_list.verticalScrollBar().valueChanged.connect(stage.sync)
        stage.watch(self.composer._send_btn, "compose.send.hover")
        stage.watch(self.activation.sign_in_button(), "signin.hover")
        for row in self.empty_state.hover_rows():
            stage.watch(row, "home.row.hover")

        h, c, e = self.header, self.composer, self.empty_state


        for widget, name in ((c, "composer"), (c._send_btn, "send"), (c._attach_btn, "attach"),
                             (c._permission_chip, "permission"), (c._effort_chip, "effort"),
                             (c._dictation_ui.button, "mic"), (e._examples_row, "examples"),
                             (e._tutorial_row, "tutorial"), (e._sources_row, "data")):
            stage.solid(widget, name, perch=c)
        for widget, name in ((h._history_btn, "history"), (h._new_btn, "new"), (h._feedback_btn, "feedback"),
                             (h._settings_btn, "settings"), (h._pro_pill, "plan"),
                             (self.activation.sign_in_button(), "signin")):
            stage.solid(widget, name)
        stage.keep_clear(e.question())





        for widget in (self.header._pro_pill, self.runs_line._label):
            stage.watch(widget, "plan.hover")
        stage.watch(self.quota_card._button, "plan.hover", when=self.quota_card.offers)
        self.quota_card.moment_changed.connect(stage.offer_moment)
        self.quota_card.low_closed.connect(stage.offer_closed)
        self.quota_card.return_closed.connect(stage.offer_closed)
        self.plans_requested.connect(lambda where: where != "checkout_return" and stage.fire("plan.open"))
        self.pro_pill_requested.connect(lambda: stage.fire("plan.open"))
        stage.watch(self.header._history_btn, "history.hover")
        stage.watch(self.header._feedback_btn, "feedback.hover")
        for widget in (self.composer._attach_btn, self.composer._permission_chip, self.composer._effort_chip):
            stage.watch(widget, "chip.hover")
        self.mode_changed.connect(lambda _run, _mode: stage.fire("mode.changed"))
        self.effort_changed.connect(lambda _level: stage.fire("effort.changed"))
        self.composer.files_attached.connect(lambda _files: stage.fire("attach.done"))
        self.composer.example_chosen.connect(lambda _prompt: stage.fire("example.picked"))
        self.composer._dictation_ui.button.clicked.connect(lambda: stage.fire("dictate.press"))
        self.composer._dictation_ui.attached.connect(stage.listen_to)
        self.answer_rated.connect(lambda _run, rating: stage.fire(f"rate.{rating}") if rating else None)
        self.answer_reason.connect(lambda *_args: stage.fire("feedback.sent"))
        self.restore_requested.connect(lambda _cid, _confirmed: stage.fire("undo"))

        self._wire()
        self._init_queue()
        self._show_thread_surface()

    def task_update(self, frame: dict) -> None:
        self.tasks_panel.task_update(frame)
        task_id = str(frame.get("task_id") or "")
        if task_id in self._task_cards and not self.tasks_panel.is_live(task_id):

            for card in self._task_cards.pop(task_id):
                for _page in range(32):
                    if not card.is_open():
                        break
                    card.collapse(self.tr("Task ended"))
            self.composer.set_reply_placeholder("")

    def ask_task_permission(self, tool_call_id: str, task_id: str, sentence: str, name: str = "", args=None,
                            addresses=None, grant: str = "", group: str = "", preview=None) -> None:
        if not self.tasks_panel.ask_permission(task_id, tool_call_id, sentence, name, args, addresses, grant,
                                               preview):
            self.permission_decided.emit(tool_call_id, "deny", None)

    def task_result(self, frame: dict) -> None:

        self.tasks_panel.task_result(frame)
        summary = str(frame.get("summary") or "").strip()
        if summary:
            bubble = AgentBubble()
            bubble.run_id = str(frame.get("task_id") or "")
            bubble.set_text(summary)
            self._add(bubble)

    def _mascot_decided(self, _tool_call_id: str, decision: str, _values=None) -> None:
        self.mascot_stage.fire("permission.allow" if str(decision).startswith("allow") else "permission.deny")

    def _mascot_answered(self, _tool_call_id: str, answer: str) -> None:
        if answer:
            self.mascot_stage.fire("question.answer")

    def quota_walled(self) -> bool:

        return self._quota_view in (quota_view.EXHAUSTED, quota_view.PAYMENT_FAILED)

    def detach_header(self) -> Header:

        self._col.removeWidget(self.header)
        return self.header

    def cleanup(self) -> None:


        try:
            self._center_anim.stop()
        except (RuntimeError, AttributeError):
            pass
        self._unwire_history_keys()
        self.composer.unwire_selection()
        self.message_list.stop_animations()
        try:
            self._drop_overlay.stop_animations()
        except (RuntimeError, AttributeError):
            pass
        try:
            self.queue_strip.stop_animations()
        except (RuntimeError, AttributeError):
            pass

        self._layer_watch.close()
        self.composer.set_running(False)
