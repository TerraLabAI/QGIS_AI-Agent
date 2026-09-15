# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat panel: every slot the controller calls, every signal it listens to."""













from __future__ import annotations

from qgis.PyQt.QtCore import QEasingCurve, QVariantAnimation, pyqtSignal
from qgis.PyQt.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from .bubbles import StatusLine
from .chat_panel_layout import _ChatPanelLayout
from .chat_panel_prompts import _ChatPanelPrompts
from .chat_panel_runs import _ChatPanelRuns




from .chat_panel_shared import (
    _is_nothing_changed,  # noqa: F401 - re-exported
    _layer_id_named,  # noqa: F401 - re-exported
    _Run,
    _turn_divider,  # noqa: F401 - re-exported
    _wrap,
)
from .chat_panel_threads import _ChatPanelThreads
from .composer import Composer
from .dock.about import UpdateBanner
from .dock.activation_state import ActivationCard
from .drop_overlay import DropOverlay
from .empty_state import EmptyState
from .header import Header
from .layer_watch import LayerWatch
from .message_list import MessageList
from .notice_bar import NoticeBar
from .quota_card import QuotaCard
from .style import MIN_PANEL_WIDTH, SPACE_CARD, SPACE_TIGHT, panel_qss
from .update_gate import UpdateGate



_CENTER_MS = 260


class ChatPanel(_ChatPanelRuns, _ChatPanelPrompts, _ChatPanelThreads, _ChatPanelLayout, QWidget):
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
    reconnect_requested = pyqtSignal()
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



        self._layer_watch = LayerWatch(self, self)
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

        self._layer_watch.close()
        self.header.clear_account()
        self.composer.set_running(False)
