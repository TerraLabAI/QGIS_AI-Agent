# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





























from __future__ import annotations

from qgis.PyQt.QtCore import QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont, QFontMetrics
from qgis.PyQt.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from ..core.plan import memory_allowed
from ..core.profile import profile_context
from ..core.qt_compat import disconnect_quietly, one_shot, qt_deleted
from ..core.settings import Settings
from .external_links import open_external_url
from .font_scale import apply_font_scale_to_tree, scale_px_length
from .icons import icon_for
from .learn_page import LearnPage
from .library.dialog import SOURCES_KEY, ExamplesDialog
from .settings_account import AccountPageMixin
from .settings_pages import HAIRLINE, MUTED, SAVED_HINT_QSS, Page
from .settings_personalisation import PersonalisationPageMixin
from .settings_shell import ShellMixin
from .shared import exec_dialog, size_within_screen
from .style import with_platform_font



_DIALOG_W, _DIALOG_H = 900, 640
_DIALOG_MIN_W, _DIALOG_MIN_H = 660, 460
_SAVE_DEBOUNCE_MS = 600
_SAVED_HINT_MS = 1600


class SettingsDialog(ShellMixin, PersonalisationPageMixin, AccountPageMixin, QDialog):


    values_changed = pyqtSignal(dict)
    profile_changed = pyqtSignal(dict)
    sign_out_requested = pyqtSignal()
    sign_in_requested = pyqtSignal()
    delete_account_requested = pyqtSignal(str)
    refresh_requested = pyqtSignal()
    telemetry_toggled = pyqtSignal(bool)
    improve_toggled = pyqtSignal(bool)
    dashboard_opened = pyqtSignal(str)
    upgrade_requested = pyqtSignal()
    help_requested = pyqtSignal(str)
    prompt_chosen = pyqtSignal(str, object)
    example_chosen = pyqtSignal(str)

    def __init__(self, parent=None, account_info: dict | None = None,
                 settings_values: dict | None = None):

        if isinstance(parent, dict):
            parent, settings_values, account_info = (
                account_info if not isinstance(account_info, dict) else None, parent, None)
        super().__init__(parent)
        with_platform_font(self)
        info = dict(account_info or {})
        values = dict(settings_values or {})
        self._values = {
            "mode": str(values.get("mode") or "agent"),
            "approval": str(values.get("approval") or "ask"),
            "server_url": str(values.get("server_url") or ""),
        }
        store = values.get("settings")
        self._store = store if hasattr(store, "memory_notes") else Settings()



        self._values["send_shortcut"] = str(
            values.get("send_shortcut") or self._store.send_shortcut)
        self._telemetry_enabled = bool(info.get("telemetry_enabled", True))
        self._improve_enabled = bool(info.get("improve_enabled", True))
        self._avatar_label: QLabel | None = None
        self._avatar_loader = None
        self._pending: dict[str, QTimer] = {}

        self.setObjectName("AIAgentSettingsDialog")
        self.setWindowTitle(self.tr("AI Agent settings"))
        self.setModal(True)
        size_within_screen(self, scale_px_length(_DIALOG_W), scale_px_length(_DIALOG_H),
                           scale_px_length(_DIALOG_MIN_W), scale_px_length(_DIALOG_MIN_H))

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_shell_sidebar())

        right = QWidget(self)
        right_col = QVBoxLayout(right)
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(0)
        self._pages = QStackedWidget(right)
        right_col.addWidget(self._pages, 1)
        root.addWidget(right, 1)

        self._saved_hint = QLabel(self.tr("Saved"), right)
        self._saved_hint.setStyleSheet(SAVED_HINT_QSS)
        self._saved_hint.adjustSize()
        self._saved_hint.hide()
        self._saved_timer = one_shot(self, self._saved_hint.hide)
        self._right = right
        self._build_shell(info)
        apply_font_scale_to_tree(self)

    def _add_page(self, glyph: str, label: str, page: QWidget) -> None:
        item = QListWidgetItem(icon_for(self, glyph, 16, QColor(MUTED)), label)
        item.setSizeHint(QSize(0, scale_px_length(32)))
        item.setData(Qt.ItemDataRole.UserRole, self._pages.addWidget(page))
        self._nav.addItem(item)

    def _add_separator(self) -> None:

        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setSizeHint(QSize(0, scale_px_length(13)))
        self._nav.addItem(item)
        host = QWidget(self._nav)
        line = QVBoxLayout(host)
        line.setContentsMargins(14, 6, 14, 6)
        rule = QFrame(host)
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {HAIRLINE}; border: none;")
        line.addWidget(rule)
        host.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._nav.setItemWidget(item, host)

    def _add_action(self, glyph: str, label: str, kind: str) -> None:






        item = QListWidgetItem(icon_for(self, glyph, 16, QColor(MUTED)), label)
        item.setSizeHint(QSize(0, scale_px_length(32)))
        item.setData(Qt.ItemDataRole.UserRole, f"action:{kind}")
        item.setToolTip(self.tr("Opens a window"))
        item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, self.tr("Opens a window"))
        self._nav.addItem(item)

        self._nav.ensurePolished()
        font = QFont(self._nav.font())
        font.setPixelSize(scale_px_length(13))




        text_margin = self._nav.style().pixelMetric(
            QStyle.PixelMetric.PM_FocusFrameHMargin, None, self._nav) + 1
        needed = (6 + 8 + 16 + 3 * text_margin + QFontMetrics(font).horizontalAdvance(label)
                  + 8 + 6 + 1)
        side = self._nav.parentWidget()
        if side is not None and needed > side.width():
            side.setFixedWidth(needed)

    def _on_nav_changed(self, row: int) -> None:
        item = self._nav.item(row) if row >= 0 else None
        data = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if isinstance(data, str) and data.startswith("action:"):
            QTimer.singleShot(0, self._restore_page_row)
            kind = data[len("action:"):]
            if kind == "sources":
                QTimer.singleShot(0, self._open_sources)
            else:
                self._on_help(kind)
            return
        if isinstance(data, int):
            self._page_row = row
            self._pages.setCurrentIndex(data)

    def _restore_page_row(self) -> None:

        if self._nav.currentRow() != self._page_row:
            self._nav.setCurrentRow(self._page_row)

    def _show_saved(self) -> None:
        self._show_hint(self.tr("Saved"), _SAVED_HINT_MS)

    def _show_hint(self, text: str, ms: int) -> None:


        hint = self._saved_hint
        room = max(160, self._right.width() - 48)
        shown = hint.fontMetrics().elidedText(str(text), Qt.TextElideMode.ElideRight, room)
        hint.setText(shown)
        hint.setToolTip(str(text) if shown != str(text) else "")
        hint.adjustSize()
        hint.move(self._right.width() - hint.width() - 24, 26)
        hint.raise_()
        hint.show()
        self._saved_timer.start(max(1, int(ms)))

    def _emit_profile(self) -> None:
        self.profile_changed.emit(profile_context(self._store))

    def _save_later(self, key: str, save) -> None:

        timer = self._pending.get(key)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            self._pending[key] = timer
        else:
            disconnect_quietly(timer, "timeout")
        timer.timeout.connect(save)
        timer.start(_SAVE_DEBOUNCE_MS)

    def _flush_pending(self) -> None:
        for timer in list(self._pending.values()):
            if timer.isActive():
                timer.stop()
                timer.timeout.emit()

    def _memory_locked(self) -> bool:


        return not memory_allowed()

    def _build_learn(self) -> LearnPage:







        page = LearnPage(self)
        page.opened.connect(self._on_learn_opened)
        page.siblings.opened.connect(self._on_sibling_opened)
        return page

    def _open_sources(self) -> None:




        library = ExamplesDialog(self, opened_from="settings", place=SOURCES_KEY)
        library.example_chosen.connect(self.example_chosen.emit)
        library.prompt_chosen.connect(self._on_library_prompt)
        try:
            exec_dialog(library)
        finally:

            if not qt_deleted(library):
                library.deleteLater()

    def _on_library_prompt(self, text: str, chip) -> None:
        self.accept()
        self.prompt_chosen.emit(str(text or ""), dict(chip) if isinstance(chip, dict) else {})

    def _on_sibling_opened(self, product_id: str, outcome: str) -> None:
        self.help_requested.emit(f"sibling:{product_id}:{outcome}")

    def _on_learn_opened(self, kind: str, url: str) -> None:
        self.help_requested.emit(f"learn:{kind}")
        open_external_url(str(url), parent=self)

    def _build_shortcuts(self) -> Page:
        from .shortcuts_page import build_shortcuts_page

        return build_shortcuts_page(self)

    def _on_help(self, kind: str) -> None:


        self.help_requested.emit(kind)

    def apply_plan(self) -> None:






        try:
            self._shell_apply_plan(self._memory_locked())
        except RuntimeError:
            pass

    def values(self) -> dict:
        return dict(self._values)

    def done(self, result):  # noqa: N802
        self._flush_pending()
        self._cancel_avatar_load()
        super().done(result)

    def closeEvent(self, event):  # noqa: N802
        self._flush_pending()
        self._cancel_avatar_load()
        super().closeEvent(event)
