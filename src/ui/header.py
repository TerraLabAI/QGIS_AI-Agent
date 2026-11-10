# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later













































from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QKeySequence
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .checkpoint_sheet import CheckpointSheet, history_rows, row_target
from .font_scale import scale_px_length
from .history_popup import HistoryPopup
from .icons import logo_pixmap, logo_size
from .quota_card import trial_cta
from .shared import CHECKPOINTS_KEYS
from .style import (
    _BTN_PRO_PILL,
    _HEADER_QSS,
    PRO_PILL_PX,
    RADIUS_CONTROL,
    SPACE_CARD,
    repolish,
)
from .widgets import IconButton

HEADER_HEIGHT = 36



_MARK = 20
_MARK_GAP = 6


class Header(QWidget):


    thread_selected = pyqtSignal(str)
    new_thread_requested = pyqtSignal()
    thread_delete_requested = pyqtSignal(str)
    settings_clicked = pyqtSignal()
    feedback_clicked = pyqtSignal()



    checkpoints_requested = pyqtSignal()
    restore_requested = pyqtSignal(str)
    discard_all_requested = pyqtSignal()
    stop_and_restore_requested = pyqtSignal()
    pro_pill_clicked = pyqtSignal()
    pro_pill_shown = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("hdr")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_HEADER_QSS)
        self.setFixedHeight(HEADER_HEIGHT)
        self._threads: list = []
        self._current_project = ""
        self._dock = None
        self._sidebar = None
        row = QHBoxLayout(self)


        row.setContentsMargins(12, 2, 8, 2)
        row.setSpacing(SPACE_CARD)




        brand_host = QWidget(self)
        brand = QHBoxLayout(brand_host)
        brand.setContentsMargins(0, 0, 0, 0)
        brand.setSpacing(_MARK_GAP)
        self._logo = QLabel(brand_host)
        self._logo.setFixedSize(logo_size(_MARK))
        self._logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._logo.setPixmap(logo_pixmap(self, _MARK))
        brand.addWidget(self._logo, 0, Qt.AlignmentFlag.AlignVCenter)




        wordmark = QWidget(brand_host)
        word_col = QVBoxLayout(wordmark)
        word_col.setContentsMargins(0, 0, 0, 0)
        word_col.setSpacing(0)
        self._title = QLabel(self.tr("AI Agent"), wordmark)
        self._title.setObjectName("hdrTitle")
        word_col.addWidget(self._title)
        self._byline = QLabel(self.tr("by TerraLab"), wordmark)
        self._byline.setObjectName("hdrByline")
        word_col.addWidget(self._byline)
        brand.addWidget(wordmark, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(brand_host, 0)
        self._brand = brand_host
        row.addStretch(1)

        self._pro_pill = QPushButton(self.tr("Get Pro"), self)
        self._pro_pill.setObjectName("hdrProPill")
        self._pro_pill.installEventFilter(self)
        pill_px = scale_px_length(PRO_PILL_PX)
        self._pro_pill.setStyleSheet(
            _BTN_PRO_PILL + f"QPushButton#hdrProPill {{ border-radius: {min(RADIUS_CONTROL, pill_px // 2)}px; }}")
        self._pro_pill.setFixedHeight(pill_px)
        self._pro_pill.setCursor(Qt.CursorShape.PointingHandCursor)

        self._pro_pill.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._pro_pill.setAutoDefault(False)
        self._pro_pill.setToolTip(self.tr("See what Pro unlocks"))
        self._pro_pill.setAccessibleName(self.tr("See what Pro unlocks"))
        self._pro_pill.setProperty("compact", False)
        self._pro_pill.clicked.connect(self.pro_pill_clicked.emit)
        self._pro_pill.hide()
        self._pro_pill_wanted = False
        self._pill_hints = {}
        row.addWidget(self._pro_pill, 0, Qt.AlignmentFlag.AlignVCenter)



        self._restore_btn = IconButton(
            self, "lu.history", 18,
            self.tr("Map history ({key})").format(
                key=QKeySequence(CHECKPOINTS_KEYS).toString(QKeySequence.SequenceFormat.NativeText)))
        self._restore_btn.setFixedSize(26, 26)
        self._sheet = CheckpointSheet(self)
        self._sheet.restore_requested.connect(self.restore_requested.emit)
        self._sheet.discard_all_requested.connect(self.discard_all_requested.emit)
        self._sheet.stop_and_restore_requested.connect(self.stop_and_restore_requested.emit)
        self._sheet.installEventFilter(self)
        self._restore_btn.clicked.connect(self.open_checkpoints)
        self._restore_btn.hide()
        row.addWidget(self._restore_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._history_btn = IconButton(self, "clock", 18, self.tr("Chat history"))
        self._history_btn.setFixedSize(26, 26)
        self._history_popup = HistoryPopup(self)
        self._history_popup.thread_selected.connect(self.thread_selected.emit)
        self._history_popup.new_thread_requested.connect(self.new_thread_requested.emit)
        self._history_popup.thread_delete_requested.connect(self.thread_delete_requested.emit)
        self._history_popup.installEventFilter(self)
        self._history_btn.clicked.connect(self._open_history)
        row.addWidget(self._history_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._new_btn = IconButton(self, "new_chat", 18, self.tr("New chat"))
        self._new_btn.setObjectName("agentNewChat")
        self._new_btn.setProperty("agentAction", "new_chat")
        self._new_btn.setFixedSize(26, 26)
        self._new_btn.clicked.connect(self.new_thread_requested.emit)
        row.addWidget(self._new_btn, 0, Qt.AlignmentFlag.AlignVCenter)


        self._feedback_btn = IconButton(self, "lu.bug", 18, self.tr("Send feedback"))
        self._feedback_btn.setObjectName("agentFeedback")
        self._feedback_btn.setFixedSize(26, 26)
        self._feedback_btn.clicked.connect(self.feedback_clicked.emit)
        row.addWidget(self._feedback_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._settings_btn = IconButton(self, "gear", 18, self.tr("Settings"))
        self._settings_btn.setFixedSize(26, 26)
        self._settings_btn.clicked.connect(self.settings_clicked.emit)
        row.addWidget(self._settings_btn, 0, Qt.AlignmentFlag.AlignVCenter)




        self._float_btn = IconButton(self, "float_window", 18, self.tr("Dock or undock this panel"))
        self._float_btn.setFixedSize(26, 26)
        self._float_btn.clicked.connect(self._toggle_floating)
        self._float_btn.hide()
        row.addWidget(self._float_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self._close_btn = IconButton(self, "close", 18, self.tr("Close this panel"))
        self._close_btn.setFixedSize(26, 26)
        self._close_btn.clicked.connect(self._close_dock)
        self._close_btn.hide()
        row.addWidget(self._close_btn, 0, Qt.AlignmentFlag.AlignVCenter)




    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(super().sizeHint().width(), HEADER_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802


        width = super().minimumSizeHint().width()
        if not self._pro_pill.isHidden():
            width -= self._pro_pill.minimumSizeHint().width() + self.layout().spacing()
        return QSize(width, HEADER_HEIGHT)

    def set_dock(self, dock) -> None:

        self._dock = dock
        self._float_btn.setVisible(dock is not None)
        self._close_btn.setVisible(dock is not None)
        self._fit_pro_pill()

    def set_pro_pill_visible(self, visible: bool) -> None:

        self._pro_pill_wanted = bool(visible)
        self._pro_pill.setVisible(self._pro_pill_wanted)
        self._fit_pro_pill()

    def set_pro_pill_trial(self, promise: str) -> None:


        label = trial_cta() if promise else self.tr("Get Pro")
        tip = promise or self.tr("See what Pro unlocks")
        if self._pro_pill.text() == label and self._pro_pill.toolTip() == tip:
            return
        self._pro_pill.setText(label)
        self._pro_pill.setToolTip(tip)
        self._pro_pill.setAccessibleName(tip)
        self._fit_pro_pill()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._fit_pro_pill()

    def _fit_pro_pill(self) -> None:



        if not self._pro_pill_wanted:
            return
        row = self.layout()
        margins = row.contentsMargins()
        others = [w for w in (self._brand, self._restore_btn, self._new_btn, self._history_btn,
                              self._settings_btn, self._float_btn, self._close_btn)
                  if not w.isHidden()]
        used = margins.left() + margins.right()
        used += sum(w.sizeHint().width() for w in others)
        used += row.spacing() * (len(others) + 1)
        room = self.width() - used


        compact = room < self._pill_hint(False)
        visible = room >= self._pill_hint(compact)
        self._set_pill_compact(compact)
        self._pro_pill.setVisible(visible)

    def _set_pill_compact(self, compact: bool) -> None:
        if bool(self._pro_pill.property("compact")) != compact:
            self._pro_pill.setProperty("compact", compact)
            repolish(self._pro_pill)

    def _pill_hint(self, compact: bool) -> int:





        key = (self._pro_pill.text(), compact)
        if key not in self._pill_hints:
            self._set_pill_compact(compact)
            self._pill_hints[key] = self._pro_pill.sizeHint().width()
        return self._pill_hints[key]

    def _toggle_floating(self) -> None:
        if self._dock is not None:
            self._dock.setFloating(not self._dock.isFloating())

    def _close_dock(self) -> None:
        if self._dock is not None:
            self._dock.close()

    def set_threads(self, items, current_project_path: str | None = None) -> None:


        self._threads = [i for i in (items or []) if isinstance(i, dict) and i.get("id")]
        if current_project_path is not None:
            self._current_project = str(current_project_path or "")
        self._history_popup.set_threads(self._threads, self._current_project)
        if self._sidebar is not None:
            self._sidebar.set_threads(self._threads, self._current_project)

    def set_current_project(self, path: str) -> None:

        self._current_project = str(path or "")
        self._history_popup.set_current_project(self._current_project)
        if self._sidebar is not None:
            self._sidebar.set_project(self._current_project)

    def set_current_thread(self, thread_id: str) -> None:

        self._history_popup.set_current_thread(thread_id or "")
        if self._sidebar is not None:
            self._sidebar.set_current_thread(thread_id or "")

    def set_checkpoints(self, entries: list) -> None:






        entries = [e for e in (entries or []) if isinstance(e, dict)]
        self._sheet.set_entries(entries)
        self.set_restore_available(any(row_target(row) for row in history_rows(entries)))

    def set_restore_available(self, available: bool) -> None:


        self._restore_btn.setVisible(bool(available))
        self._fit_pro_pill()

    def open_checkpoints(self) -> bool:


        if self._restore_btn.isHidden():
            return False
        if self._sheet.isVisible():
            self._sheet.hide()
            return False



        self.checkpoints_requested.emit()
        if self._restore_btn.isHidden():
            return False
        self._restore_btn.set_active(True)
        self._sheet.show_under(self._restore_btn, self.parentWidget() or self)
        return True

    def checkpoint_sheet(self) -> CheckpointSheet:

        return self._sheet

    def set_feedback_available(self, available: bool) -> None:

        self._feedback_btn.setVisible(bool(available))

    def set_history_available(self, available: bool) -> None:

        self._history_btn.setVisible(bool(available))
        if not available and self._history_popup.isVisible():
            self._history_popup.hide()
        self._fit_pro_pill()

    def set_new_chat_available(self, available: bool) -> None:

        self._new_btn.setVisible(bool(available))
        self._fit_pro_pill()

    def history_menu(self) -> HistoryPopup:

        return self._history_popup

    def set_sidebar(self, sidebar) -> None:

        self._sidebar = sidebar
        if sidebar is not None:
            sidebar.set_threads(self._threads, self._current_project)

    def _open_history(self) -> None:
        if self._sidebar is not None and self._sidebar.host_is_wide():
            self._sidebar.toggle()
            return
        if self._history_popup.isVisible():
            self._history_popup.hide()
            return
        self._history_btn.set_active(True)
        self._history_popup.show_over(self.parentWidget() or self)

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is getattr(self, "_pro_pill", None) and event.type() == QEvent.Type.Show:
            self.pro_pill_shown.emit()
        if event.type() == QEvent.Type.Hide:

            for watched_widget, button in ((getattr(self, "_history_popup", None), getattr(self, "_history_btn", None)),
                                           (getattr(self, "_sheet", None), getattr(self, "_restore_btn", None))):
                if watched_widget is not None and button is not None and watched is watched_widget:
                    button.setDown(False)
                    button.set_hovered(False)
                    button.set_active(False)
        return super().eventFilter(watched, event)

    def set_title(self, text: str) -> None:
        self._title.setText(text)
