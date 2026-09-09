# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Settings dialog behind the footer gear: tabs at the left, one page at a time at the right, every control saved the moment it changes."""
















































from __future__ import annotations

from qgis.PyQt.QtCore import QDateTime, QLocale, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.logger import log_warning
from ..core.plan import memory_allowed
from ..core.profile import (
    MEMORY_NOTE_MAX_CHARS,
    PROFILE_LINE_MAX_CHARS,
    PROFILE_MAX_CHARS,
    REPLY_LANGUAGES,
    add_memory_note,
    load_memory_notes,
    normalize_expertise,
    normalize_layer_naming,
    normalize_question_policy,
    normalize_reply_language,
    normalize_reply_style,
    normalize_units,
    profile_context,
    project_key,
    remove_memory_note,
)
from ..core.settings import Settings
from .account_settings_dialog import _ACCOUNT_OFFLINE_CODES, format_count, resolve_plan_runs
from .connector_page import ConnectorPage
from .connectors_page import ConnectorsPage
from .delete_account_dialog import ask_delete_account
from .external_links import open_external_url
from .font_scale import apply_font_scale_to_tree, scale_px_length
from .icons import icon_for, logo_pixmap, logo_size
from .learn_page import LearnPage
from .permission_chip import glyph_tile
from .settings_pages import (
    DANGER_GHOST_BTN_QSS,
    GHOST_BTN_QSS,
    HAIRLINE,
    INPUT_QSS,
    MUTED,
    PRIMARY_BTN_QSS,
    ROW_NOTE_QSS,
    SAVED_HINT_QSS,
    TEXTAREA_QSS,
    BillingCard,
    BillingCardRow,
    NoteRow,
    Page,
    ProCard,
    SectionCard,
    Segmented,
    SettingGroup,
    SettingRow,
    Switch,
    combo_qss,
    nav_qss,
    set_section_locked,
    sidebar_qss,
)
from .shared import (
    PLUGIN_FOLDERS,
    PRODUCT_ID,
    event_pos,
    exec_dialog,
    format_reset_date,
    get_connectors,
    get_dashboard_url,
    get_hidden_plugins,
    get_plan_name,
    get_pricing_url,
    get_privacy_url,
    get_pro_runs_per_month,
    get_product_url,
    get_qgis_plugins,
    get_site_url,
    get_support_email,
    get_terms_url,
    get_tool_names,
    known_plugins_by_folder,
    safe_disconnect,
    size_within_screen,
)
from .siblings_page import SiblingsPage
from .style import ACCENT, ACCENT_DARK, RED





_USAGE_KEYS = ("runs_used", "runs_limit", "period_end", "reset_date", "is_subscriber", "is_free_tier")










_DIALOG_W, _DIALOG_H = 1080, 760
_DIALOG_MIN_W, _DIALOG_MIN_H = 820, 560
_NAV_W = 186
_SAVE_DEBOUNCE_MS = 600
_SAVED_HINT_MS = 1600


_FAULT_HINT_MS = 6000
_TEXTAREA_H = 118

_NAV_QSS = nav_qss("settingsNav")
_SIDEBAR_QSS = sidebar_qss("settingsSidebar")



_INPUT_ERROR_QSS = INPUT_QSS.replace(HAIRLINE, RED)
_PROGRESS_QSS = (
    "QProgressBar { background: rgba(128,128,128,0.18); border: none; border-radius: 3px;"
    " max-height: 6px; min-height: 6px; }"
    "QProgressBar::chunk { background: palette(text); border-radius: 3px; }"
)





_NAV_TINTS = {
    "gear": "#6B7A8F",
    "shield": "#C98A2E",
    "pencil": "#7C6CD0",
    "spark": "#1F9E96",
    "globe": "#3E86D6",



    "person": ACCENT,
    "book": "#C96A8C",
    "gem": "#C98A2E",
    "play": "#D0533C",

    "chat_bubble": "#3E86D6",
    "warning": "#D0533C",
}
_AVATAR_D = 38

_WORDMARK_PX = 22
_WORDMARK_QSS = ("font-size: 13px; font-weight: 700; color: palette(text);"
                 " background: transparent;")
_WORDMARK_NOTE_QSS = f"font-size: 10px; color: {MUTED}; background: transparent;"
_IDENTITY_QSS = (f"QLabel#settingsIdentity {{ font-size: 10px; color: {MUTED};"
                 " background: transparent; }"
                 "QLabel#settingsIdentity:hover { color: palette(text); }")


_RAIL_CTA_QSS = (
    f"QPushButton {{ background: {ACCENT}; color: #12240a; border: none;"
    " border-radius: 8px; margin: 4px 12px 6px 14px; padding: 7px 10px;"
    " font-size: 12px; font-weight: 700; }"
    f"QPushButton:hover {{ background: {ACCENT_DARK}; }}"
)


class _LinkTile(QWidget):
    """A widget that reports a click."""



    clicked = pyqtSignal()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event_pos(event)):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class SettingsDialog(QDialog):
    """Tabs at the left, a page at the right, immediate apply with a Saved hint."""

    values_changed = pyqtSignal(dict)
    profile_changed = pyqtSignal(dict)
    sign_out_requested = pyqtSignal()
    delete_account_requested = pyqtSignal(str)
    refresh_requested = pyqtSignal()
    telemetry_toggled = pyqtSignal(bool)
    improve_toggled = pyqtSignal(bool)
    dashboard_opened = pyqtSignal(str)
    upgrade_requested = pyqtSignal()
    help_requested = pyqtSignal(str)


    prompt_chosen = pyqtSignal(str, object)

    def __init__(self, parent=None, account_info: dict | None = None,
                 settings_values: dict | None = None):

        if isinstance(parent, dict):
            parent, settings_values, account_info = (
                account_info if not isinstance(account_info, dict) else None, parent, None)
        super().__init__(parent)
        info = dict(account_info or {})
        values = dict(settings_values or {})
        self._values = {
            "mode": str(values.get("mode") or "agent"),
            "approval": str(values.get("approval") or "ask"),
            "server_url": str(values.get("server_url") or ""),
            "tool_names": list(values.get("tool_names") or []),
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



        self._plan_sections: list = []

        self.setObjectName("AIAgentSettingsDialog")
        self.setWindowTitle(self.tr("AI Agent settings"))
        self.setModal(True)
        size_within_screen(self, scale_px_length(_DIALOG_W), scale_px_length(_DIALOG_H),
                           scale_px_length(_DIALOG_MIN_W), scale_px_length(_DIALOG_MIN_H))

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())

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
        self._saved_timer = QTimer(self)
        self._saved_timer.setSingleShot(True)
        self._saved_timer.timeout.connect(self._saved_hint.hide)
        self._right = right





































        self._add_page("globe", self.tr("Connectors"), self._build_connectors())
        self._add_page("pencil", self.tr("Personalisation"), self._build_personalisation())
        self._add_page("terminal", self.tr("Keyboard shortcuts"), self._build_shortcuts())
        self._add_page("play", self.tr("Tutorials"), self._build_tutorials())





        self._add_page("puzzle", self.tr("More plugins"), self._build_siblings())
        self._add_page("person", self.tr("Account"), self._build_account())
        self._billing_index = self._nav.count()
        self._add_page("gem", self.tr("Billing"), self._build_billing())





        self._page_row = 0
        self._add_action("chat_bubble", self.tr("Contact us"), "contact")
        self._add_action("warning", self.tr("Report a problem"), "report")



        self._connector_detail = ConnectorPage(self)
        self._connector_detail.back_requested.connect(self._close_connector)
        self._connector_detail.prompt_chosen.connect(self._on_connector_prompt)
        self._connector_detail.install_requested.connect(self._on_plugin_install)
        self._connector_detail.enable_requested.connect(self._on_detail_enable)




        self._detail_kind = ""
        self._connector_index = self._pages.count()
        self._pages.addWidget(self._connector_detail)
        self._nav.setCurrentRow(0)
        self.set_account_info(info)
        apply_font_scale_to_tree(self)



    def _build_sidebar(self) -> QFrame:
        side = QFrame(self)
        side.setObjectName("settingsSidebar")
        side.setStyleSheet(_SIDEBAR_QSS)
        side.setFixedWidth(scale_px_length(_NAV_W))
        col = QVBoxLayout(side)
        col.setContentsMargins(0, 14, 0, 12)
        col.setSpacing(6)
        col.addWidget(self._build_wordmark(side))
        self._nav = QListWidget(side)
        self._nav.setObjectName("settingsNav")
        self._nav.setStyleSheet(_NAV_QSS)
        self._nav.setIconSize(QSize(16, 16))
        self._nav.setSpacing(0)
        self._nav.setFrameShape(QFrame.Shape.NoFrame)
        self._nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav.setCursor(Qt.CursorShape.PointingHandCursor)
        self._nav.setUniformItemSizes(True)
        self._nav.currentRowChanged.connect(self._on_nav_changed)
        col.addWidget(self._nav, 1)








        self._upgrade_pill = QPushButton(self.tr("Upgrade to Pro"), side)
        self._upgrade_pill.setStyleSheet(_RAIL_CTA_QSS)
        self._upgrade_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self._upgrade_pill.setAutoDefault(False)
        self._upgrade_pill.setToolTip(self.tr("Plans and prices, on the TerraLab website."))
        self._upgrade_pill.clicked.connect(self._on_upgrade)
        self._upgrade_pill.hide()
        col.addWidget(self._upgrade_pill)









        self._identity = QLabel(
            f'<a href="{get_site_url()}" style="color: {MUTED}; text-decoration: none;">'
            f'{self.tr("TerraLab")}</a>', side)
        self._identity.setObjectName("settingsIdentity")
        self._identity.setStyleSheet(_IDENTITY_QSS)
        self._identity.setContentsMargins(16, 0, 12, 2)
        self._identity.setCursor(Qt.CursorShape.PointingHandCursor)
        self._identity.setToolTip(self.tr("Open terra-lab.ai"))
        self._identity.setOpenExternalLinks(False)
        self._identity.linkActivated.connect(self._open_site)
        col.addWidget(self._identity)

        legal = QLabel(
            f'<a href="{get_terms_url()}" style="color: {MUTED}; text-decoration: none;">{self.tr("Terms")}</a>'
            f' <span style="color: rgba(128,128,128,0.5);">·</span> '
            f'<a href="{get_privacy_url()}" style="color: {MUTED}; text-decoration: none;">'
            f'{self.tr("Privacy")}</a>', side)
        legal.setOpenExternalLinks(True)
        legal.setStyleSheet("font-size: 10px;")
        legal.setContentsMargins(16, 0, 0, 0)
        col.addWidget(legal)



        return side

    def _build_wordmark(self, side: QFrame) -> QWidget:
        """The mark and "AI Agent", the way the site's nav bar carries them."""











        host = _LinkTile(side)
        host.clicked.connect(self._open_product)
        host.setCursor(Qt.CursorShape.PointingHandCursor)
        host.setToolTip(self.tr("Open the AI Agent page"))
        row = QHBoxLayout(host)
        row.setContentsMargins(14, 0, 12, 6)
        row.setSpacing(8)
        mark = QLabel(host)
        side_px = scale_px_length(_WORDMARK_PX)


        pixmap = logo_pixmap(mark, side_px)
        if pixmap.isNull():

            mark = glyph_tile(host, "spark", ACCENT, side_px, scale_px_length(12))
        else:
            mark.setFixedSize(logo_size(side_px))
            mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
            mark.setPixmap(pixmap)
        row.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
        names = QVBoxLayout()
        names.setContentsMargins(0, 0, 0, 0)
        names.setSpacing(0)
        title = QLabel(self.tr("AI Agent"), host)
        title.setStyleSheet(_WORDMARK_QSS)
        names.addWidget(title)
        maker = QLabel(self.tr("by TerraLab"), host)
        maker.setStyleSheet(_WORDMARK_NOTE_QSS)
        names.addWidget(maker)
        row.addLayout(names, 1)
        return host

    def _open_product(self) -> None:
        """The wordmark: the product's page on the site."""
        open_external_url(get_product_url(), parent=self)

    def _open_site(self, _link: str = "") -> None:
        """The foot of the rail: terra-lab.ai itself."""
        open_external_url(get_site_url(), parent=self)

    def _add_page(self, glyph: str, label: str, page: QWidget) -> None:
        tint = _NAV_TINTS.get(glyph)
        icon = icon_for(self, glyph, 16, QColor(tint)) if tint else icon_for(self, glyph, 16)
        item = QListWidgetItem(icon, label)
        item.setSizeHint(QSize(0, scale_px_length(32)))
        self._nav.addItem(item)
        self._pages.addWidget(page)

    def _add_action(self, glyph: str, label: str, kind: str) -> None:
        """A rail row that opens a dialog instead of a page."""






        tint = _NAV_TINTS.get(glyph)
        icon = icon_for(self, glyph, 16, QColor(tint)) if tint else icon_for(self, glyph, 16)
        item = QListWidgetItem(icon, label)
        item.setSizeHint(QSize(0, scale_px_length(32)))
        item.setData(Qt.ItemDataRole.UserRole, f"action:{kind}")
        self._nav.addItem(item)

    def _on_nav_changed(self, row: int) -> None:
        item = self._nav.item(row) if row >= 0 else None
        data = str(item.data(Qt.ItemDataRole.UserRole) or "") if item is not None else ""
        if data.startswith("action:"):
            QTimer.singleShot(0, self._restore_page_row)
            self._on_help(data[len("action:"):])
            return
        if 0 <= row < self._pages.count():
            self._page_row = row
            self._pages.setCurrentIndex(row)

    def _restore_page_row(self) -> None:
        """Hand the rail's highlight back to the page that is showing."""
        if self._nav.currentRow() != self._page_row:
            self._nav.setCurrentRow(self._page_row)

    def show_page(self, index: int) -> None:
        self._nav.setCurrentRow(max(0, min(int(index), self._nav.count() - 1)))

    def _show_saved(self) -> None:
        self._show_hint(self.tr("Saved"), _SAVED_HINT_MS)

    def _show_hint(self, text: str, ms: int = _FAULT_HINT_MS) -> None:
        """The floating note in the corner, for "Saved" and for what went wrong."""





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
        """Debounce a text field: write once typing pauses."""
        timer = self._pending.get(key)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            self._pending[key] = timer
        else:
            safe_disconnect(timer, "timeout")
        timer.timeout.connect(save)
        timer.start(_SAVE_DEBOUNCE_MS)

    def _flush_pending(self) -> None:
        for timer in list(self._pending.values()):
            if timer.isActive():
                timer.stop()
                timer.timeout.emit()



    def _build_answers(self, page: Page) -> SettingGroup:
        """How the AI writes back: the language, the length, how often it asks."""







        group = SettingGroup(page)

        self._language_combo = QComboBox(group)
        self._language_combo.setStyleSheet(combo_qss(self))
        self._language_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._language_combo.addItem(self.tr("Same as QGIS"), "")
        for code, native in REPLY_LANGUAGES:
            self._language_combo.addItem(native, code)
        self._language_combo.setMinimumWidth(scale_px_length(180))
        self._language_combo.currentIndexChanged.connect(self._on_language_changed)
        group.add_row(SettingRow(self.tr("Response language"),
                                 self.tr("The language the AI writes its answers in."),
                                 self._language_combo, group))

        self._style_segments = Segmented(
            [("concise", self.tr("Concise")), ("balanced", self.tr("Balanced")),
             ("detailed", self.tr("Detailed"))], self._store.reply_style, group)
        self._style_segments.changed.connect(self._on_style_changed)
        group.add_row(SettingRow(self.tr("Response style"),
                                 self.tr("Short answers, a balance, or the full reasoning."),
                                 self._style_segments, group))





        self._questions_segments = Segmented(
            [("minimal", self.tr("Rarely")), ("balanced", self.tr("When needed")),
             ("confirm", self.tr("Often"))], self._store.question_policy, group)
        self._questions_segments.changed.connect(self._on_questions_changed)
        group.add_row(SettingRow(self.tr("Questions"),
                                 self.tr("How often the AI asks before acting."),
                                 self._questions_segments, group))

        self._timeout_combo = QComboBox(group)
        self._timeout_combo.setStyleSheet(combo_qss(self))
        self._timeout_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        for seconds, name in self._timeout_texts():
            self._timeout_combo.addItem(name, seconds)
        self._timeout_combo.setMinimumWidth(scale_px_length(180))
        self._timeout_combo.currentIndexChanged.connect(self._on_timeout_picked)
        group.add_row(SettingRow(
            self.tr("Answer a question for me after"),
            self.tr("The AI takes the option it recommended and carries on."),
            self._timeout_combo, group))

        self._explain_switch = Switch(group, self._store.explain_runs)
        self._explain_switch.toggled.connect(self._on_explain_toggled)
        group.add_row(SettingRow(self.tr("Explain what it did after each run"),
                                 self.tr("A short summary of the changes once a task is done."),
                                 self._explain_switch, group))

        self._tools_switch = Switch(group, self._store.show_tool_details)
        self._tools_switch.toggled.connect(self._on_tools_toggled)
        group.add_row(SettingRow(self.tr("Show tool details in the trace"),
                                 self.tr("Each step with its inputs and results, under the answer."),
                                 self._tools_switch, group))






        self._follow_switch = Switch(group, self._store.follow_edits)
        self._follow_switch.toggled.connect(self._on_follow_toggled)
        group.add_row(SettingRow(
            self.tr("Move the map to what it changes"),
            self.tr("The view goes to each edit as it happens. Off keeps your view where you put it."),
            self._follow_switch, group))
        return group

    def _timeout_texts(self) -> tuple:
        """``(seconds, label)`` for the delay before a question answers itself."""
        return (
            (0, self.tr("Always wait for me")),
            (30, self.tr("30 seconds")),
            (60, self.tr("1 minute")),
            (120, self.tr("2 minutes")),
        )

    def _sync_timeout_combo(self) -> None:
        current = self._store.question_timeout_s
        index = self._timeout_combo.findData(current)
        if index < 0:
            index = max(0, self._timeout_combo.findData(60))
        self._timeout_combo.blockSignals(True)
        self._timeout_combo.setCurrentIndex(index)
        self._timeout_combo.blockSignals(False)

    def _on_timeout_picked(self, index: int) -> None:
        seconds = self._timeout_combo.itemData(index)
        if not isinstance(seconds, int) or seconds == self._store.question_timeout_s:
            return
        self._store.question_timeout_s = seconds
        self._show_saved()

    def _sync_language_combo(self) -> None:
        code = normalize_reply_language(self._store.reply_language)
        index = max(0, self._language_combo.findData(code))
        self._language_combo.blockSignals(True)
        self._language_combo.setCurrentIndex(index)
        self._language_combo.blockSignals(False)

    def _on_language_changed(self, index: int) -> None:
        code = str(self._language_combo.itemData(index) or "")
        if code == self._store.reply_language:
            return
        self._store.reply_language = code
        self._show_saved()
        self._emit_profile()

    def _on_style_changed(self, value: str) -> None:
        value = normalize_reply_style(value)
        if value == self._store.reply_style:
            return
        self._store.reply_style = value
        self._show_saved()
        self._emit_profile()

    def _on_questions_changed(self, value: str) -> None:
        value = normalize_question_policy(value)
        if value == self._store.question_policy:
            return
        self._store.question_policy = value
        self._show_saved()
        self._emit_profile()

    def _on_explain_toggled(self, on: bool) -> None:
        self._store.explain_runs = bool(on)
        self._show_saved()
        self._emit_profile()

    def _on_tools_toggled(self, on: bool) -> None:
        self._store.show_tool_details = bool(on)
        self._show_saved()
        self._emit_profile()

    def _on_follow_toggled(self, on: bool) -> None:
        self._store.follow_edits = bool(on)
        self._show_saved()
        self._emit_profile()



    def _build_personalisation(self) -> Page:
        """Everything the AI knows about you before you type: the profile, how you work, and the notes it keeps between conversations."""







        page = Page(self.tr("Personalisation"),
                    self.tr("Context the AI reads at the start of every conversation, "
                            "and the notes it keeps between them."), self)





        page.add_group_title(self.tr("Who you are"))
        identity = SettingGroup(page)
        self._name_edit = QLineEdit(self._store.profile_name, identity)
        self._name_edit.setStyleSheet(INPUT_QSS)
        self._name_edit.setPlaceholderText(self.tr("Your first name"))
        self._name_edit.setMaxLength(PROFILE_LINE_MAX_CHARS)
        self._name_edit.setFixedWidth(scale_px_length(200))
        self._name_edit.editingFinished.connect(self._on_name_changed)
        identity.add_row(SettingRow(self.tr("What the AI should call you"),
                                    self.tr("Used in its answers. Empty: it uses no name."),
                                    self._name_edit, identity))
        self._role_edit = QLineEdit(self._store.profile_role, identity)
        self._role_edit.setStyleSheet(INPUT_QSS)
        self._role_edit.setPlaceholderText(self.tr("Urban planner"))
        self._role_edit.setMaxLength(PROFILE_LINE_MAX_CHARS)
        self._role_edit.setFixedWidth(scale_px_length(200))
        self._role_edit.editingFinished.connect(self._on_role_changed)
        identity.add_row(SettingRow(self.tr("What you do"),
                                    self.tr("Your job in a few words. It changes which data and "
                                            "which method it reaches for first."),
                                    self._role_edit, identity))
        page.add(identity)
        locked = not memory_allowed()
        self._about_edit, self._about_count = self._text_block(
            page, self.tr("About you"),
            self.tr("Who you are and what you work on. Example: urban planner at the city of Lyon, "
                    "I mostly work with cadastre and PLU layers in EPSG:2154."),
            self._store.profile_about, "about", pro_only=locked,
            pro_note=self.tr("Pro reads this before every run, so your job, your city and your usual "
                             "CRS do not have to be typed into each prompt."))
        self._instructions_edit, self._instructions_count = self._text_block(
            page, self.tr("Instructions for the AI"),
            self.tr("How it should work. Example: always answer in French, name new layers in "
                    "snake_case, never delete a layer without asking."),
            self._store.profile_instructions, "instructions", pro_only=locked,
            pro_note=self.tr("Pro follows your standing rules in every run: the language it answers in, "
                             "how it names layers, and what it must never do without asking."))
        page.add_group_title(self.tr("How you work"))
        page.add(self._build_work_preferences(page))




        page.add_group_title(self.tr("Answers"))
        page.add(self._build_answers(page))
        self._sync_language_combo()
        self._sync_timeout_combo()
        self._build_memory(page)
        return page

    def _build_work_preferences(self, page: Page) -> SettingGroup:
        group = SettingGroup(page)
        self._expertise_segments = Segmented(
            [("", self.tr("Auto")), ("beginner", self.tr("Beginner")),
             ("intermediate", self.tr("Regular")), ("expert", self.tr("Expert"))],
            self._store.expertise, group)
        self._expertise_segments.changed.connect(self._on_expertise_changed)
        group.add_row(SettingRow(self.tr("GIS experience"),
                                 self.tr("How much the AI explains."),
                                 self._expertise_segments, group))








        self._units_segments = Segmented(
            [("metric", self.tr("Metric")), ("imperial", self.tr("Imperial"))], self._store.units, group)
        self._units_segments.changed.connect(self._on_units_changed)
        group.add_row(SettingRow(self.tr("Units"),
                                 self.tr("Metres and hectares, or feet, miles and acres."),
                                 self._units_segments, group))

        self._naming_segments = Segmented(
            [("human", self.tr("Plain words")), ("snake_case", self.tr("Computer-style names"))],
            self._store.layer_naming, group)
        self._naming_segments.changed.connect(self._on_naming_changed)
        group.add_row(SettingRow(self.tr("Layer names"),
                                 self.tr("How the AI names the layers it creates."),
                                 self._naming_segments, group))
        return group

    def _on_name_changed(self) -> None:
        value = " ".join(self._name_edit.text().split())[:PROFILE_LINE_MAX_CHARS]
        if self._name_edit.text() != value:
            self._name_edit.setText(value)
        if value == self._store.profile_name:
            return
        self._store.profile_name = value
        self._show_saved()
        self._emit_profile()

    def _on_role_changed(self) -> None:
        value = " ".join(self._role_edit.text().split())[:PROFILE_LINE_MAX_CHARS]
        if self._role_edit.text() != value:
            self._role_edit.setText(value)
        if value == self._store.profile_role:
            return
        self._store.profile_role = value
        self._show_saved()
        self._emit_profile()

    def _on_expertise_changed(self, value: str) -> None:
        value = normalize_expertise(value)
        if value == self._store.expertise:
            return
        self._store.expertise = value
        self._show_saved()
        self._emit_profile()

    def _on_units_changed(self, value: str) -> None:
        value = normalize_units(value)
        if value == self._store.units:
            return
        self._store.units = value
        self._show_saved()
        self._emit_profile()

    def _on_naming_changed(self, value: str) -> None:
        value = normalize_layer_naming(value)
        if value == self._store.layer_naming:
            return
        self._store.layer_naming = value
        self._show_saved()
        self._emit_profile()

    def _pro_card(self, host, note: str) -> ProCard:
        """The card over a section this plan does not include."""










        card = ProCard(self.tr("Included with Pro"), note, self.tr("See pricing"), host)
        card.upgrade_requested.connect(self._on_upgrade)
        host.add(card)
        return card

    def _text_block(self, page: Page, title: str, placeholder: str, text: str, key: str,
                    pro_only: bool = False, pro_note: str = ""):
        page.add_group_title(title)


        card = SectionCard(page)
        page.add(card)



        pro = self._pro_card(card, pro_note) if pro_note else None
        if pro is not None:
            pro.setVisible(bool(pro_only))
        edit = QPlainTextEdit(card)
        edit.setStyleSheet(TEXTAREA_QSS)
        edit.setPlaceholderText(placeholder)
        edit.setPlainText(text or "")
        edit.setFixedHeight(scale_px_length(_TEXTAREA_H))
        edit.setTabChangesFocus(True)
        card.add(edit)
        count = QLabel(card)
        count.setStyleSheet(ROW_NOTE_QSS)
        count.setAlignment(Qt.AlignmentFlag.AlignRight)
        count.setContentsMargins(0, 0, 4, 0)
        card.add(count)
        self._update_count(edit, count)
        edit.textChanged.connect(lambda: self._on_profile_text_changed(edit, count, key))
        self._plan_sections.append((pro, (edit, count)))
        set_section_locked(edit, count, locked=bool(pro_only))
        return edit, count

    def _update_count(self, edit: QPlainTextEdit, count: QLabel) -> None:
        count.setText(self.tr("{count} / {limit}").format(
            count=len(edit.toPlainText()), limit=PROFILE_MAX_CHARS))

    def _on_profile_text_changed(self, edit: QPlainTextEdit, count: QLabel, key: str) -> None:
        text = edit.toPlainText()
        if len(text) > PROFILE_MAX_CHARS:
            edit.blockSignals(True)
            edit.setPlainText(text[:PROFILE_MAX_CHARS])
            cursor = edit.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            edit.setTextCursor(cursor)
            edit.blockSignals(False)
        self._update_count(edit, count)
        self._save_later(key, lambda: self._save_profile_text(edit, key))

    def _save_profile_text(self, edit: QPlainTextEdit, key: str) -> None:
        try:
            text = edit.toPlainText()[:PROFILE_MAX_CHARS]
        except RuntimeError:
            return
        if key == "about":
            if text == self._store.profile_about:
                return
            self._store.profile_about = text
        else:
            if text == self._store.profile_instructions:
                return
            self._store.profile_instructions = text
        self._show_saved()
        self._emit_profile()



    def _build_memory(self, page: Page) -> None:
        """The notes, the field that adds one, and the switch that lets the AI add its own."""









        page.add_group_title(self.tr("Memory"))
        locked = not memory_allowed()
        card = SectionCard(page)
        page.add(card)
        pro = self._pro_card(
            card, self.tr("Pro reads your notes at the start of every conversation."))
        pro.setVisible(locked)
        intro = QLabel(self.tr("Short reminders it keeps between conversations."), card)
        intro.setStyleSheet(ROW_NOTE_QSS)
        intro.setWordWrap(True)
        intro.setContentsMargins(2, 0, 2, 0)
        card.add(intro)
        self._notes_group = SettingGroup(card, flat=True)
        card.add(self._notes_group)

        add_row = QWidget(card)
        add_lay = QHBoxLayout(add_row)
        add_lay.setContentsMargins(0, 0, 0, 0)
        add_lay.setSpacing(8)
        self._note_edit = QLineEdit(add_row)
        self._note_edit.setStyleSheet(INPUT_QSS)
        self._note_edit.setPlaceholderText(self.tr("I work in EPSG:2154"))
        self._note_edit.setMaxLength(MEMORY_NOTE_MAX_CHARS)
        self._note_edit.returnPressed.connect(self._on_add_note)
        add_lay.addWidget(self._note_edit, 1)
        add_btn = QPushButton(self.tr("Add"), add_row)
        add_btn.setStyleSheet(GHOST_BTN_QSS)
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setAutoDefault(False)
        add_btn.clicked.connect(self._on_add_note)
        add_lay.addWidget(add_btn, 0)
        card.add(add_row)

        group = SettingGroup(card, flat=True)
        self._memory_switch = Switch(group, self._store.memory_enabled)
        self._memory_switch.toggled.connect(self._on_memory_toggled)
        group.add_row(SettingRow(self.tr("Let the AI add its own notes"), "",
                                 self._memory_switch, group))
        card.add(group)
        self._fill_notes()


        self._plan_sections.append((pro, (self._notes_group, add_row, group, intro)))
        set_section_locked(self._notes_group, add_row, group, intro, locked=locked)

    def _fill_notes(self) -> None:
        self._notes_group.clear()
        notes = load_memory_notes(self._store)
        if not notes:
            empty = QLabel(self.tr("No notes yet."), self._notes_group)
            empty.setStyleSheet(ROW_NOTE_QSS)
            empty.setWordWrap(True)
            empty.setContentsMargins(14, 12, 14, 12)
            self._notes_group.add_row(empty)
            return
        for note in reversed(notes):
            row = NoteRow(note["text"], self._note_caption(note), self._notes_group)
            row.removed.connect(self._on_remove_note)
            self._notes_group.add_row(row)
        apply_font_scale_to_tree(self._notes_group)

    def _open_project_key(self) -> str:
        """The key of the project open right now, or "" outside QGIS."""
        try:
            from qgis.core import QgsProject
            return project_key(QgsProject.instance().fileName())
        except Exception:  # noqa: BLE001 - no project is not an error here
            return ""

    def _note_caption(self, note: dict) -> str:
        """Who noted it, when, and whether it belongs to this project."""










        who = self.tr("Noted by the AI") if note.get("source") == "ai" else self.tr("Added by you")
        parts = [who]
        if note.get("scope") == "project":
            key = str(note.get("project") or "")
            here = self._open_project_key()
            parts.append(self.tr("This project") if key and key == here
                         else self.tr("Another project"))
        stamp = str(note.get("updated_at") or note.get("created_at") or "")
        when = QDateTime.fromString(stamp, Qt.DateFormat.ISODate)
        if when.isValid():
            parts.append(QLocale().toString(when.toLocalTime().date(), QLocale.FormatType.ShortFormat))
        return " · ".join(parts)

    def _on_add_note(self) -> None:
        text = self._note_edit.text().strip()
        if not text:
            return
        if add_memory_note(self._store, text, "user") is None:

            self._note_edit.selectAll()
            return
        self._note_edit.clear()
        self._fill_notes()
        self._show_saved()
        self._emit_profile()

    def _on_remove_note(self, text: str) -> None:
        if remove_memory_note(self._store, text):
            self._fill_notes()
            self._show_saved()
            self._emit_profile()

    def _on_memory_toggled(self, on: bool) -> None:
        self._store.memory_enabled = bool(on)
        self._show_saved()
        self._emit_profile()



    def _build_connectors(self) -> QWidget:
        """The directory, built in ui/connectors_page.py; the dialog only feeds it."""

















        page = ConnectorsPage(self)
        page.plugin_install_requested.connect(self._on_plugin_install)
        page.plugin_enable_requested.connect(lambda folder: self._on_plugin_enable(folder, True))
        page.connector_opened.connect(self.open_connector)
        page.plugin_opened.connect(self.open_plugin)
        self._connector_page = page
        self.set_connectors(get_connectors())
        return page

    def set_connectors(self, rows) -> None:
        """A new list from the server. Repaints the whole directory."""
        rows = [r for r in (rows or []) if isinstance(r, dict) and r.get("id")]
        if rows:
            self._connector_rows = {str(row["id"]): row for row in rows}
        self._paint_connectors()

    def _paint_connectors(self) -> None:
        page = getattr(self, "_connector_page", None)
        if page is None:
            return
        page.set_data(list(getattr(self, "_connector_rows", {}).values()),
                      self._plugin_rows(), self._capability_groups())

    def _plugin_rows(self) -> list:
        """The merged list: what is installed here, enriched by what the server knows."""






        try:
            from ..core.qgis_plugins import installed_plugins, plugin_logo
        except Exception:  # noqa: BLE001
            return []
        try:
            local = installed_plugins()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"QGIS plugins not listed: {exc}")
            return []
        known = known_plugins_by_folder()
        self._known_plugins = list(get_qgis_plugins() or [])





        skip = set(PLUGIN_FOLDERS) | get_hidden_plugins()
        rows = []
        for folder, row in local.items():
            if folder in skip:
                continue
            merged = dict(known.get(folder) or {})
            merged.update(row)
            merged["installed"] = True


            if not str(merged.get("icon") or "").strip():
                merged["icon"] = plugin_logo(folder)
            rows.append(merged)
        rows.sort(key=lambda r: (not r.get("loaded"), str(r.get("name") or "").lower()))
        placed = {str(r.get("folder") or "") for r in rows}
        for row in self._known_plugins:
            folder = str(row.get("folder") or "")
            names = [folder] + [str(n) for n in (row.get("folders") or ()) if n]
            if not folder or folder in skip or any(n in local or n in placed for n in names):
                continue
            rows.append({**row, "folder": folder, "installed": False, "loaded": False,
                         "icon": plugin_logo(folder)})
        return rows

    def _capability_groups(self) -> list:
        """What the agent reaches inside QGIS."""









        try:
            from ..core.capabilities import capability_groups
        except Exception:  # noqa: BLE001
            return []
        names = self._values.get("tool_names") or get_tool_names()
        if not names:
            return []
        try:
            return capability_groups(names)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Capabilities not listed: {exc}")
            return []



    def open_connector(self, key: str) -> None:
        """Show one connector's detail page. Unknown id: stay where we are."""
        row = getattr(self, "_connector_rows", {}).get(str(key))
        if not row:
            return
        self._detail_kind = "connector"
        self._connector_detail.set_connector(row)
        self._pages.setCurrentIndex(self._connector_index)

    def open_plugin(self, folder: str) -> None:
        """Show one QGIS plugin's detail page, with its live menu commands."""
        row = next((r for r in self._plugin_rows() if str(r.get("folder")) == str(folder)), None)
        if not row:
            return
        row = dict(row)
        if row.get("loaded"):
            try:
                from ..core.qgis_plugins import plugin_actions

                row["actions"] = plugin_actions(str(folder))
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Plugin actions not read: {exc}")
        self._detail_kind = "plugin"
        self._connector_detail.set_plugin(row)
        self._pages.setCurrentIndex(self._connector_index)

    def _close_connector(self) -> None:
        """Back to the list, exactly where it was left."""







        self._detail_kind = ""
        page = getattr(self, "_connector_page", None)
        if page is not None and hasattr(page, "remember_scroll"):
            page.remember_scroll()
        self._paint_connectors()
        self._pages.setCurrentIndex(self._nav.currentRow())

    def _on_connector_prompt(self, text: str, chip=None) -> None:
        """A prompt from a connector card: the panel takes it, the dialog closes."""
        self.prompt_chosen.emit(str(text or ""), chip if isinstance(chip, dict) else {})
        self.accept()

    def _on_detail_enable(self, folder: str) -> None:
        """The Enable button on a plugin's card: start it, then repaint the card."""
        self._on_plugin_enable(str(folder), True)
        self.open_plugin(str(folder))

    def _on_plugin_enable(self, folder: str, on: bool) -> None:
        """Start a plugin QGIS has switched off, then repaint the row it moved."""







        if not on:
            return
        reason = ""
        try:
            from ..core.qgis_plugins import enable_plugin

            started, reason = enable_plugin(str(folder))
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Plugin not enabled: {exc}")
            started = False
        if started:
            self._show_saved()
        else:
            log_warning(f"Plugin {folder} did not start: {reason or 'no reason recorded'}")
            self._show_hint(reason.strip() or self.tr("This plugin would not start."))
        self._paint_connectors()

    def _on_plugin_install(self, folder: str) -> None:
        """Hand the user to the QGIS plugin manager, filtered to this plugin."""









        row = next((r for r in getattr(self, "_known_plugins", []) if str(r.get("folder")) == str(folder)), None)
        name = str((row or {}).get("name") or folder)
        url = str((row or {}).get("url") or "https://plugins.qgis.org/plugins/")
        self.accept()
        try:
            from .cross_plugin_discovery import open_plugin_manager

            open_plugin_manager(name, url)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Plugin manager not opened: {exc}")



    def _build_billing(self) -> Page:
        """Which plan is in force, what Pro adds, and the way out to the site."""










        page = Page(self.tr("Billing"),
                    self.tr("The plan this copy of QGIS is signed in on."), self)
        self._billing_box = QWidget(page)
        self._billing_col = QVBoxLayout(self._billing_box)
        self._billing_col.setContentsMargins(0, 0, 0, 0)
        self._billing_col.setSpacing(14)
        page.add(self._billing_box)
        self._paint_billing({})
        return page

    def _paint_billing(self, info: dict) -> None:
        """Repaint the Billing page from the same dict the Account page gets."""












        col = getattr(self, "_billing_col", None)
        if col is None:
            return
        while col.count():
            item = col.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        box = self._billing_box
        info = dict(info or {})
        if info.get("loading"):
            col.addWidget(self._muted(self.tr("Loading your plan..."), box))
            return
        usage = dict(info.get("usage") or {})
        for key in _USAGE_KEYS:
            if key in info and key not in usage:
                usage[key] = info[key]
        plan = resolve_plan_runs(usage, self._find_subscription(info) or {})

        cards = BillingCardRow(box)
        cards.add_card(self._billing_plan_card(cards, plan))
        cards.add_card(self._billing_dashboard_card(cards, plan.is_subscriber))
        col.addWidget(cards)
        apply_font_scale_to_tree(box)

    def _billing_plan_card(self, parent: QWidget, plan) -> BillingCard:
        """The plan in force: what is left of it, and who to write to for more."""
        card = BillingCard(
            get_plan_name("pro", self.tr("Pro plan")) if plan.is_subscriber
            else get_plan_name("free", self.tr("Free plan")), parent)
        if plan.limit is not None and plan.used is not None:
            left = max(0, int(plan.limit) - int(plan.used))
            card.add_stat(format_count(left), self.tr("runs left of {limit} this month").format(
                limit=format_count(plan.limit)))
            bar = QProgressBar(card)
            bar.setRange(0, max(int(plan.limit), 1))
            bar.setValue(min(left, int(plan.limit)))
            bar.setTextVisible(False)
            bar.setFixedHeight(6)
            bar.setStyleSheet(_PROGRESS_QSS)
            card.add(bar)
        elif plan.used is not None:
            card.add_stat(format_count(plan.used), self.tr("runs this month"))
        else:


            card.add_status(self.tr("No runs counted yet this month."))
        reset_str = format_reset_date(plan.reset_iso or "")
        if reset_str:
            card.add_note(self.tr("Resets {date}").format(date=reset_str))
        if plan.is_subscriber:
            card.add_note(self.tr("Need more than {n} runs a month? Write to {email}.").format(
                n=get_pro_runs_per_month(), email=get_support_email()))
        return card

    def _billing_dashboard_card(self, parent: QWidget, is_subscriber: bool) -> BillingCard:
        """The way out to the site, and nothing priced."""







        card = BillingCard(self.tr("Manage"), parent)
        card.add_subtitle(self.tr("Your plan, payment method and invoices are on the "
                                  "TerraLab dashboard."))
        if not is_subscriber:

            pricing = QPushButton(self.tr("See pricing"), card)
            pricing.clicked.connect(self._on_upgrade)
            card.add_action(pricing)
        manage = QPushButton(self.tr("Open the dashboard"), card)
        manage.clicked.connect(lambda: self._open_dashboard("billing_page"))
        card.add_action(manage)
        if not is_subscriber:
            manage.setStyleSheet(GHOST_BTN_QSS)
        card.add_note(self.tr("Payment happens on the TerraLab website, never inside QGIS. "
                              "Your plan here updates on its own."))
        return card



    def _build_tutorials(self) -> LearnPage:
        """The video and the guide as thumbnails, built in ui/learn_page.py."""






        page = LearnPage(self)
        page.opened.connect(self._on_learn_opened)
        return page

    def _build_siblings(self) -> SiblingsPage:
        """AI Edit and AI Segmentation, in ui/siblings_page.py."""






        page = SiblingsPage(self)
        page.opened.connect(self._on_sibling_opened)
        return page

    def _on_sibling_opened(self, product_id: str, outcome: str) -> None:
        self.help_requested.emit(f"sibling:{product_id}:{outcome}")

    def _on_learn_opened(self, kind: str, url: str) -> None:
        self.help_requested.emit(f"learn:{kind}")
        open_external_url(str(url), parent=self)



    def _build_shortcuts(self) -> Page:
        """The panel's whole keyboard map, on the page rather than behind a button."""






        page = Page(self.tr("Keyboard shortcuts"),
                    self.tr("Everything the panel does without leaving the keyboard."), self)
        try:
            from .dock.about import build_shortcuts_card

            page.add(build_shortcuts_card(page))
        except Exception as exc:  # noqa: BLE001 - a broken sheet must not break the window
            log_warning(f"Keyboard shortcuts not shown: {exc}")
            page.add(self._muted(self.tr("The shortcuts could not be listed.")))
        return page



    def _on_help(self, kind: str) -> None:
        """The panel owns the contact dialog and the report dialog: it answers this, over the settings window."""

        self.help_requested.emit(kind)



    def _build_account(self) -> Page:
        """Who is signed in, then the settings that belong to the account."""








        page = Page(self.tr("Account"), self.tr("Who is signed in, and how this copy of the plugin "
                                                "behaves."), self)
        self._account_box = QWidget(page)
        self._account_col = QVBoxLayout(self._account_box)
        self._account_col.setContentsMargins(0, 0, 0, 0)
        self._account_col.setSpacing(14)



        page.add(self._account_box)
        self._add_advanced(page)
        return page

    def set_account_info(self, info: dict) -> None:
        """Repaint the Account page from one dict: loading, error, or the account."""
        info = dict(info or {})
        if "telemetry_enabled" in info:
            self.set_telemetry_enabled(bool(info["telemetry_enabled"]))
        if "improve_enabled" in info:
            self.set_improve_enabled(bool(info["improve_enabled"]))
        self._sync_improve_plan(info)


        try:
            self._paint_billing(info)
        except RuntimeError:
            pass
        self._sync_upgrade_pill(info)
        self._clear_account()


        self._account_email = ""
        if info.get("loading"):
            self._account_col.addWidget(self._muted(self.tr("Loading account info...")))
        elif info.get("error") or info.get("error_code"):
            self._show_account_error(str(info.get("error") or ""), str(info.get("error_code") or ""))
        else:
            usage = dict(info.get("usage") or {})
            for key in _USAGE_KEYS:
                if key in info and key not in usage:
                    usage[key] = info[key]
            self._show_account(info, usage)
        self._sync_delete_account()
        apply_font_scale_to_tree(self._account_box)

    def _sync_upgrade_pill(self, info: dict) -> None:
        """Show the rail's upgrade only to an account that can take it up."""




        pill = getattr(self, "_upgrade_pill", None)
        if pill is None:
            return
        if info.get("loading") or info.get("error") or info.get("error_code"):
            pill.hide()
            return
        pill.setVisible(not self._is_subscriber(info))

    def _is_subscriber(self, info: dict) -> bool:
        """Does this account pay? Read from whichever of the three fields arrived."""
        usage = dict(info.get("usage") or {})
        for key in ("is_subscriber", "is_free_tier"):
            if key in info and key not in usage:
                usage[key] = info[key]
        if "is_subscriber" in usage:
            return bool(usage.get("is_subscriber"))
        if "is_free_tier" in usage:
            return not bool(usage.get("is_free_tier"))
        return bool(self._find_subscription(info))

    def _clear_account(self) -> None:
        self._cancel_avatar_load()
        self._avatar_label = None
        while self._account_col.count():
            item = self._account_col.takeAt(0)
            widget = item.widget()
            if widget is not None:



                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _muted(self, text: str, parent: QWidget | None = None) -> QLabel:


        label = QLabel(text, parent or getattr(self, "_account_box", None) or self)
        label.setStyleSheet(ROW_NOTE_QSS)
        label.setWordWrap(True)
        label.setContentsMargins(4, 4, 4, 4)
        return label

    def _show_account_error(self, message: str, code: str) -> None:
        code = (code or "").strip().upper()
        retry = False
        manage = False
        if code == "SIGNED_OUT":
            text = message or self.tr("Sign in to see your account.")
        elif code == "SUBSCRIPTION_INACTIVE":
            text = self.tr("There's a problem with your subscription: your last payment may have "
                           "failed. Update your payment method to fix it.")
            manage = True
        elif code == "INVALID_KEY":
            text = self.tr("This computer is no longer signed in. Sign out, then sign in again to "
                           "reconnect it.")
        elif code in _ACCOUNT_OFFLINE_CODES:
            text = self.tr("Could not reach TerraLab. Check your internet connection, then try again.")
            retry = True
        else:
            text = message or self.tr("Could not load your account. Try again in a moment.")
            retry = True
        group = SettingGroup(self._account_box)
        group.add_row(SettingRow(text, "", None, group))
        self._account_col.addWidget(group)
        if code == "SIGNED_OUT":
            return
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        if manage:
            btn = self._button(self.tr("Update payment method"), PRIMARY_BTN_QSS)
            btn.clicked.connect(lambda: self._open_dashboard("error_card"))
            buttons.addWidget(btn)
        if retry:
            btn = self._button(self.tr("Retry"), GHOST_BTN_QSS)
            btn.clicked.connect(self._on_retry)
            buttons.addWidget(btn)
        buttons.addStretch(1)
        out = self._button(self.tr("Sign out"), GHOST_BTN_QSS)
        out.clicked.connect(self._on_sign_out)
        buttons.addWidget(out)
        holder = QWidget(self._account_box)
        holder.setLayout(buttons)
        self._account_col.addWidget(holder)

    def _show_account(self, account: dict, usage: dict) -> None:
        email = str(account.get("email") or "-")
        self._account_email = "" if email == "-" else email
        group = SettingGroup(self._account_box)

        chip = QFrame(group)
        chip.setObjectName("settingsRow")
        chip_row = QHBoxLayout(chip)
        chip_row.setContentsMargins(14, 12, 14, 12)
        chip_row.setSpacing(12)
        diameter = scale_px_length(_AVATAR_D)
        avatar = QLabel(email[:1].upper() if email and email != "-" else "?", chip)
        avatar.setFixedSize(diameter, diameter)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(
            "background: rgba(128,128,128,0.2); color: palette(text);"
            f" border-radius: {diameter // 2}px; font-size: 16px; font-weight: 700;")
        chip_row.addWidget(avatar)
        self._avatar_label = avatar
        self._show_account_picture(account.get("avatar_url"), diameter)
        id_col = QVBoxLayout()
        id_col.setSpacing(2)
        email_lbl = QLabel(email, chip)
        email_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        email_lbl.setStyleSheet("font-size: 13px; font-weight: 600; color: palette(text); background: transparent;")
        id_col.addWidget(email_lbl)
        status = QLabel(self.tr("Signed in"), chip)
        status.setStyleSheet(ROW_NOTE_QSS)
        id_col.addWidget(status)
        chip_row.addLayout(id_col, 1)
        group.add_row(chip)

        sub = self._find_subscription(account)
        plan = resolve_plan_runs(usage, sub or {})
        plan_name = (get_plan_name("pro", self.tr("Pro plan")) if plan.is_subscriber
                     else get_plan_name("free", self.tr("Free plan")))
        bits = []
        if plan.limit is not None and plan.used is not None:
            left = max(0, int(plan.limit) - int(plan.used))
            bits.append(self.tr("{left} runs left of {limit} this month").format(
                left=format_count(left), limit=format_count(plan.limit)))
        elif plan.used is not None:
            bits.append(self.tr("{used} runs this month").format(used=format_count(plan.used)))
        reset_str = format_reset_date(plan.reset_iso or "")
        if reset_str:
            bits.append(self.tr("Resets {date}").format(date=reset_str))
        if not plan.is_subscriber:
            bits.append(self.tr("Personal, non-commercial use."))
        bar = None
        if plan.limit is not None and plan.used is not None:
            bar = QProgressBar(group)
            bar.setRange(0, max(int(plan.limit), 1))
            bar.setValue(max(0, min(int(plan.limit - plan.used), int(plan.limit))))
            bar.setTextVisible(False)
            bar.setFixedHeight(6)
            bar.setStyleSheet(_PROGRESS_QSS)
        group.add_row(SettingRow(plan_name, " · ".join(bits), bar, group, control_below=bar is not None))
        self._account_col.addWidget(group)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        if plan.is_subscriber:
            manage = self._button(self.tr("Manage account in browser"), PRIMARY_BTN_QSS)
            manage.clicked.connect(lambda: self._open_dashboard("account_card"))
            buttons.addWidget(manage)
        else:
            upgrade = self._button(self.tr("Compare plans"), PRIMARY_BTN_QSS)
            upgrade.setToolTip(self.tr("Opens the Billing page."))
            upgrade.clicked.connect(self._on_upgrade)
            buttons.addWidget(upgrade)
            manage = self._button(self.tr("Manage account"), GHOST_BTN_QSS)
            manage.clicked.connect(lambda: self._open_dashboard("account_card"))
            buttons.addWidget(manage)
        buttons.addStretch(1)
        out = self._button(self.tr("Sign out"), GHOST_BTN_QSS)
        out.clicked.connect(self._on_sign_out)
        buttons.addWidget(out)
        holder = QWidget(self._account_box)
        holder.setLayout(buttons)
        self._account_col.addWidget(holder)

    def _button(self, text: str, qss: str) -> QPushButton:
        btn = QPushButton(text, self._account_box)
        btn.setStyleSheet(qss)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setAutoDefault(False)
        btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        return btn

    @staticmethod
    def _find_subscription(data: dict) -> dict | None:
        subs = data.get("subscriptions") or []
        for pid in (f"{PRODUCT_ID}-pro", PRODUCT_ID):
            for s in subs:
                if isinstance(s, dict) and s.get("product_id") == pid:
                    return s
        return None

    def _show_account_picture(self, url, diameter: int) -> None:
        from .account_avatar import AccountAvatarLoader, cached_avatar_pixmap, is_avatar_url_usable

        if not is_avatar_url_usable(url):
            return
        pixmap = cached_avatar_pixmap(url, diameter)
        if pixmap is not None:
            self._paint_account_picture(pixmap)
            return
        self._avatar_loader = AccountAvatarLoader(self)
        self._avatar_loader.loaded.connect(self._paint_account_picture)
        self._avatar_loader.fetch(url, diameter)

    def _paint_account_picture(self, pixmap) -> None:
        label = self._avatar_label
        if label is None or pixmap is None or pixmap.isNull():
            return
        try:
            label.setText("")
            label.setStyleSheet("background: transparent; border: none;")
            label.setPixmap(pixmap)
        except RuntimeError:
            self._avatar_label = None

    def _cancel_avatar_load(self) -> None:
        loader, self._avatar_loader = self._avatar_loader, None
        if loader is None:
            return
        safe_disconnect(loader, "loaded")
        try:
            loader.abort()
        except RuntimeError:
            pass

    def _open_dashboard(self, source: str = "account_card") -> None:
        self.dashboard_opened.emit(source)
        open_external_url(get_dashboard_url(), parent=self)

    def _on_upgrade(self) -> None:
        """The plans, on the site."""






        self.upgrade_requested.emit()
        open_external_url(get_pricing_url(), parent=self)

    def _on_retry(self) -> None:
        self.set_account_info({"loading": True})
        self.refresh_requested.emit()

    def _on_sign_out(self) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(self.tr("Sign out"))
        box.setText(self.tr("Sign out of AI Agent?"))
        box.setInformativeText(self.tr("You can sign back in anytime from QGIS."))
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if exec_dialog(box) != QMessageBox.StandardButton.Yes:
            return
        self.sign_out_requested.emit()
        self.accept()



    def _add_advanced(self, page: Page) -> None:
        """Usage statistics and reset."""






        page.add_group_title(self.tr("Advanced"))




        group = SettingGroup(page)
        self._telemetry_switch = Switch(group, self._telemetry_enabled)
        self._telemetry_switch.toggled.connect(self._on_telemetry_toggled)
        group.add_row(SettingRow(self.tr("Share usage statistics with TerraLab"),
                                 self.tr("Errors, versions and which features you use, linked to your "
                                         "account. Never your prompts, your layers, your coordinates or "
                                         "your files. On Pro, only that you used the app and when, "
                                         "never what you did in it."),
                                 self._telemetry_switch, group))





        self._improve_switch = Switch(group, self._improve_enabled)
        self._improve_switch.toggled.connect(self._on_improve_toggled)
        self._improve_row = SettingRow(self.tr("Help improve AI Agent"),
                                       self._improve_note(False), self._improve_switch, group)
        group.add_row(self._improve_row)
        reset = QPushButton(self.tr("Reset all settings"), group)
        reset.setStyleSheet(DANGER_GHOST_BTN_QSS)
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.setAutoDefault(False)
        reset.clicked.connect(self._on_reset)
        group.add_row(SettingRow(self.tr("Reset all settings"),
                                 self.tr("Language, style, permissions, your profile and your memory "
                                         "notes go back to their defaults. You stay signed in."),
                                 reset, group))
        page.add(group)
        self._add_danger_zone(page)

    def _add_danger_zone(self, page: Page) -> None:
        """Its own heading and its own group: this one leaves the plugin."""




        page.add_group_title(self.tr("Danger zone"))
        group = SettingGroup(page)
        button = QPushButton(self.tr("Delete my account"), group)
        button.setStyleSheet(DANGER_GHOST_BTN_QSS)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAutoDefault(False)
        button.clicked.connect(self._on_delete_account)
        self._delete_account_btn = button


        self._delete_account_row = SettingRow(self.tr("Delete my account"), "", button, group)
        group.add_row(self._delete_account_row)
        page.add(group)
        self._sync_delete_account()

    def _sync_delete_account(self) -> None:
        """Nothing to delete until the account has been loaded and named."""
        button = getattr(self, "_delete_account_btn", None)
        row = getattr(self, "_delete_account_row", None)
        if button is None or row is None:
            return
        email = str(getattr(self, "_account_email", "") or "").strip()
        try:
            button.setEnabled(bool(email))
            if email:
                row.set_note(self.tr("Your TerraLab account and its data are erased, and every "
                                     "TerraLab plugin stops working. To confirm, type your email "
                                     "address again."))
            else:
                row.set_note(self.tr("Sign in first: deleting an account needs the address it was "
                                     "opened with."))
        except RuntimeError:
            pass

    def _on_delete_account(self) -> None:
        email = str(getattr(self, "_account_email", "") or "").strip()
        if not email:
            return
        typed = ask_delete_account(email, self)
        if not typed:
            return
        self.delete_account_requested.emit(typed)
        self.accept()

    def _improve_note(self, subscriber: bool) -> str:
        """What the row says. A paying account is told the answer, not asked it."""
        if subscriber:
            return self.tr("Your plan already turns this off. What you write is never read to "
                           "improve the product, no matter how this switch is set.")
        return self.tr("Lets us read a conversation only when we are fixing a wrong answer or "
                       "something that broke. Turn it off any time, with no other effect.")

    def _sync_improve_plan(self, info: dict) -> None:
        """A paid account cannot be asked this: the plan has already refused for it."""
        row = getattr(self, "_improve_row", None)
        switch = getattr(self, "_improve_switch", None)
        if row is None or switch is None:
            return
        if info.get("loading") or info.get("error") or info.get("error_code"):
            return
        subscriber = self._is_subscriber(info)
        try:
            row.set_note(self._improve_note(subscriber))
            if subscriber:



                switch.blockSignals(True)
                switch.setChecked(False)
                switch.blockSignals(False)
                set_section_locked(switch)
        except RuntimeError:
            pass

    def apply_plan(self) -> None:
        """Lock or unlock the plan's sections from what the server last said."""








        locked = not memory_allowed()
        for card, widgets in list(self._plan_sections):
            try:
                if card is not None:
                    card.setVisible(locked)
                set_section_locked(*widgets, locked=locked)
            except RuntimeError:
                continue

    def set_improve_enabled(self, enabled: bool) -> None:
        self._improve_enabled = bool(enabled)
        switch = getattr(self, "_improve_switch", None)
        if switch is not None:
            switch.blockSignals(True)
            switch.setChecked(self._improve_enabled)
            switch.blockSignals(False)

    def _on_improve_toggled(self, on: bool) -> None:
        self._improve_enabled = bool(on)
        self._show_saved()
        self.improve_toggled.emit(self._improve_enabled)

    def set_telemetry_enabled(self, enabled: bool) -> None:
        self._telemetry_enabled = bool(enabled)
        switch = getattr(self, "_telemetry_switch", None)
        if switch is not None:
            switch.blockSignals(True)
            switch.setChecked(self._telemetry_enabled)
            switch.blockSignals(False)

    def _on_telemetry_toggled(self, on: bool) -> None:
        self._telemetry_enabled = bool(on)
        self._show_saved()
        self.telemetry_toggled.emit(self._telemetry_enabled)

    def _on_reset(self) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(self.tr("Reset all settings"))
        box.setText(self.tr("Reset all settings?"))
        box.setInformativeText(self.tr("Language, style, permissions, your profile and your memory "
                                       "notes go back to their defaults. You stay signed in."))
        reset = box.addButton(self.tr("Reset"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(self.tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        exec_dialog(box)
        if box.clickedButton() is not reset:
            return
        for timer in self._pending.values():
            timer.stop()
        self._store.reset_preferences()
        self._values["mode"], self._values["approval"] = self._store.mode, self._store.approval
        self._sync_from_store()
        self._show_saved()
        self.values_changed.emit(self.values())
        self._emit_profile()

    def _sync_from_store(self) -> None:
        """Repaint every control from the store, without re-saving."""
        self._sync_language_combo()
        self._style_segments.set_value(self._store.reply_style)
        self._questions_segments.set_value(self._store.question_policy)
        self._expertise_segments.set_value(self._store.expertise)
        self._units_segments.set_value(self._store.units)
        self._naming_segments.set_value(self._store.layer_naming)
        self._sync_timeout_combo()
        self._values["send_shortcut"] = self._store.send_shortcut
        for edit, value in ((self._name_edit, self._store.profile_name),
                            (self._role_edit, self._store.profile_role)):
            edit.blockSignals(True)
            edit.setText(value)
            edit.blockSignals(False)
        for switch, value in ((self._explain_switch, self._store.explain_runs),
                              (self._tools_switch, self._store.show_tool_details),
                              (self._follow_switch, self._store.follow_edits),
                              (self._memory_switch, self._store.memory_enabled)):
            switch.blockSignals(True)
            switch.setChecked(value)
            switch.blockSignals(False)
        for edit, text in ((self._about_edit, self._store.profile_about),
                           (self._instructions_edit, self._store.profile_instructions)):
            edit.blockSignals(True)
            edit.setPlainText(text)
            edit.blockSignals(False)
        self._update_count(self._about_edit, self._about_count)
        self._update_count(self._instructions_edit, self._instructions_count)
        self._fill_notes()



    def values(self) -> dict:
        return dict(self._values)

    def done(self, result):  # noqa: N802 - Qt signature
        self._flush_pending()
        self._cancel_avatar_load()
        super().done(result)

    def closeEvent(self, event):  # noqa: N802 - Qt signature
        self._flush_pending()
        self._cancel_avatar_load()
        super().closeEvent(event)
