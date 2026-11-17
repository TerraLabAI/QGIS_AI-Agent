# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




















































from __future__ import annotations

from qgis.PyQt.QtCore import QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont, QFontMetrics
from qgis.PyQt.QtWidgets import (
    QBoxLayout,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from ..core import accounts
from ..core.logger import log_warning
from ..core.plan import memory_allowed
from ..core.profile import profile_context
from ..core.qt_compat import disconnect_quietly, one_shot, qt_deleted
from ..core.served_state import get_privacy_url, get_product_url, get_site_url, get_terms_url
from ..core.settings import Settings
from .cards_click import HIDDEN_FROM_AX, PressSurface, RowButtons
from .external_links import open_external_url
from .font_scale import apply_font_scale_to_tree, scale_px_length
from .icons import icon_for, logo_pixmap, logo_size
from .learn_page import LearnPage
from .library.dialog import SOURCES_KEY, ExamplesDialog
from .permission_chip import glyph_tile
from .settings_account import AccountPageMixin
from .settings_connected import ConnectedAccountsPage
from .settings_pages import (
    HAIRLINE,
    MUTED,
    PROGRESS_QSS,
    SAVED_HINT_QSS,
    Page,
    nav_qss,
    set_section_locked,
    sidebar_qss,
)
from .settings_personalisation import PersonalisationPageMixin
from .shared import exec_dialog, released_on, size_within_screen
from .style import ACCENT, FONT_HINT, WEIGHT_MEDIUM, with_platform_font
from .styles import PANEL_TEXT



_DIALOG_W, _DIALOG_H = 900, 640
_DIALOG_MIN_W, _DIALOG_MIN_H = 660, 460
_NAV_W = 186
_SAVE_DEBOUNCE_MS = 600
_SAVED_HINT_MS = 1600

_NAV_QSS = nav_qss(HIDDEN_FROM_AX)
_SIDEBAR_QSS = sidebar_qss("settingsSidebar")


_WORDMARK_PX = 22
_WORDMARK_QSS = ("font-size: 13px; font-weight: 700; color: " + PANEL_TEXT + ";"
                 " background: transparent;")
_WORDMARK_NOTE_QSS = f"font-size: {FONT_HINT}px; color: {MUTED}; background: transparent;"
_IDENTITY_QSS = (f"QLabel#settingsIdentity {{ font-size: {FONT_HINT}px; color: {MUTED};"
                 " background: transparent; }"
                 "QLabel#settingsIdentity:hover { color: " + PANEL_TEXT + "; }")
class _LinkTile(PressSurface):




    def mouseReleaseEvent(self, event):  # noqa: N802
        if released_on(self, event):
            self.click()
            return
        super().mouseReleaseEvent(event)


class SettingsDialog(PersonalisationPageMixin, AccountPageMixin, QDialog):


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



        self._plan_sections: list = []
        self._pro_notes: list = []

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
        self._saved_timer = one_shot(self, self._saved_hint.hide)
        self._right = right




        self._add_page("lu.pencil", self.tr("Personalisation"), self._build_personalisation())
        self._add_page("lu.bookmark", self.tr("Memory"), self._build_memory_page())
        self._add_page("lu.keyboard", self.tr("Keyboard shortcuts"), self._build_shortcuts())
        self._account_row = self._nav.count()
        self._add_page("lu.user", self.tr("Account"), self._build_account())
        if accounts.any_enabled():
            self._add_page("lu.lock", self.tr("Connected accounts"), ConnectedAccountsPage(self))
        self._add_separator()



        self._add_page("lu.book-open", self.tr("Learn"), self._build_learn())
        self._add_action("lu.globe", self.tr("Data sources"), "sources")
        self._add_separator()




        self._add_action("lu.message-circle", self.tr("Contact us"), "contact")
        self._add_action("lu.bug", self.tr("Send feedback"), "report")
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

        self._nav_buttons = RowButtons(self._nav, side, self._nav.setCurrentIndex)
        self._nav.setStyleSheet(_NAV_QSS)
        self._nav.setIconSize(QSize(16, 16))
        self._nav.setSpacing(0)
        self._nav.setFrameShape(QFrame.Shape.NoFrame)
        self._nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav.setCursor(Qt.CursorShape.PointingHandCursor)
        self._nav.setUniformItemSizes(True)
        self._nav.currentRowChanged.connect(self._on_nav_changed)
        self._page_row = 0
        col.addWidget(self._nav, 1)





        self._rail_plan = _LinkTile(side)
        self._rail_plan.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rail_plan.setToolTip(self.tr("See usage"))
        self._rail_plan.setAccessibleName(self.tr("See usage"))
        self._rail_plan.clicked.connect(lambda: self._nav.setCurrentRow(self._account_row))
        plan_col = QVBoxLayout(self._rail_plan)
        plan_col.setContentsMargins(16, 10, 14, 8)
        plan_col.setSpacing(6)
        rule = QFrame(self._rail_plan)
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {HAIRLINE}; border: none;")
        plan_col.addWidget(rule)
        plan_col.addSpacing(4)
        plan_line = self._rail_plan_line = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        plan_line.setContentsMargins(0, 0, 0, 0)
        plan_line.setSpacing(8)
        self._rail_plan_name = QLabel(self._rail_plan)
        self._rail_plan_name.setStyleSheet(
            f"font-size: {FONT_HINT + 1}px; font-weight: {WEIGHT_MEDIUM};"
            f" color: {PANEL_TEXT}; background: transparent;")
        plan_line.addWidget(self._rail_plan_name)
        plan_line.addStretch(1)
        self._rail_plan_runs = QLabel(self._rail_plan)
        self._rail_plan_runs.setStyleSheet(_IDENTITY_QSS.replace("QLabel#settingsIdentity", "QLabel"))
        plan_line.addWidget(self._rail_plan_runs)
        plan_col.addLayout(plan_line)
        self._rail_plan_bar = QProgressBar(self._rail_plan)
        self._rail_plan_bar.setTextVisible(False)
        self._rail_plan_bar.setFixedHeight(3)
        self._rail_plan_bar.setStyleSheet(PROGRESS_QSS)
        plan_col.addWidget(self._rail_plan_bar)
        self._rail_plan.hide()
        col.addWidget(self._rail_plan)




        dot = ' <span style="color: rgba(128,128,128,0.5);">·</span> '
        legal = QLabel(
            f'<a href="site" style="color: {MUTED}; text-decoration: none;">{self.tr("TerraLab")}</a>'
            f'{dot}<a href="{get_terms_url()}" style="color: {MUTED}; text-decoration: none;">'
            f'{self.tr("Terms")}</a>{dot}'
            f'<a href="{get_privacy_url()}" style="color: {MUTED}; text-decoration: none;">'
            f'{self.tr("Privacy")}</a>', side)
        legal.setObjectName("settingsIdentity")
        legal.setStyleSheet(_IDENTITY_QSS)

        legal.setWordWrap(True)
        legal.setOpenExternalLinks(False)
        legal.linkActivated.connect(
            lambda href: self._open_site() if href == "site" else open_external_url(href, parent=self))
        legal.setContentsMargins(16, 0, 12, 0)
        col.addWidget(legal)


        return side

    def _build_wordmark(self, side: QFrame) -> QWidget:





        host = _LinkTile(side)
        host.clicked.connect(self._open_product)
        host.setCursor(Qt.CursorShape.PointingHandCursor)
        host.setToolTip(self.tr("Open the AI Agent page"))
        host.setAccessibleName(self.tr("Open the AI Agent page"))
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

        open_external_url(get_product_url(), parent=self)

    def _open_site(self, _link: str = "") -> None:

        open_external_url(get_site_url(), parent=self)

    def _add_page(self, glyph: str, label: str, page: QWidget) -> None:
        item = QListWidgetItem(icon_for(self, glyph, 16, QColor(MUTED)), label)
        item.setSizeHint(QSize(0, scale_px_length(32)))
        item.setData(Qt.ItemDataRole.UserRole, self._pages.addWidget(page))
        self._nav.addItem(item)

    def set_rail_plan(self, name: str, runs: str, left: int | None, limit: int | None) -> None:

        self._rail_plan_name.setText(name)
        self._rail_plan_runs.setText(runs)


        for label in (self._rail_plan_name, self._rail_plan_runs):
            label.ensurePolished()
        margins = self._rail_plan.layout().contentsMargins()
        room = self._rail_plan.parentWidget().width() - margins.left() - margins.right()
        need = (self._rail_plan_name.sizeHint().width() + self._rail_plan_line.spacing()
                + self._rail_plan_runs.sizeHint().width())
        stacked = need > room
        self._rail_plan_line.setDirection(QBoxLayout.Direction.TopToBottom if stacked
                                          else QBoxLayout.Direction.LeftToRight)
        self._rail_plan_line.setSpacing(2 if stacked else 8)
        has_bar = limit is not None and left is not None
        if has_bar:
            self._rail_plan_bar.setRange(0, max(int(limit), 1))
            self._rail_plan_bar.setValue(max(0, min(int(left), int(limit))))
        self._rail_plan_bar.setVisible(has_bar)
        self._rail_plan.setVisible(bool(name))

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



        mark = QWidget(self._nav)
        line = QHBoxLayout(mark)


        line.setContentsMargins(0, 0, 2, 0)
        line.addStretch(1)
        glyph_label = QLabel(mark)
        glyph_label.setPixmap(icon_for(self, "lu.external-link", 12, QColor(MUTED)).pixmap(12, 12))
        glyph_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        line.addWidget(glyph_label, 0, Qt.AlignmentFlag.AlignVCenter)
        mark.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        mark.setStyleSheet("background: transparent;")
        self._nav.setItemWidget(item, mark)


        self._nav.ensurePolished()
        font = QFont(self._nav.font())
        font.setPixelSize(scale_px_length(13))




        text_margin = self._nav.style().pixelMetric(
            QStyle.PixelMetric.PM_FocusFrameHMargin, None, self._nav) + 1
        needed = (6 + 8 + 16 + 3 * text_margin + QFontMetrics(font).horizontalAdvance(label)
                  + 8 + 12 + 10 + 6 + 1)
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





        page = Page(self.tr("Keyboard shortcuts"),
                    self.tr("Everything the panel does without leaving the keyboard."), self)
        try:
            from .dock.about import build_shortcuts_card

            card = build_shortcuts_card(page)


            card.setStyleSheet(card.styleSheet()
                               + "QFrame#shCard { background: transparent; border: none; }")
            card.layout().setContentsMargins(14, 0, 14, 0)
            page.add(card)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Keyboard shortcuts not shown: {exc}")
            page.add(self._muted(self.tr("The shortcuts could not be listed.")))
        return page

    def _on_help(self, kind: str) -> None:


        self.help_requested.emit(kind)

    def apply_plan(self) -> None:






        locked = self._memory_locked()
        for card, widgets in list(self._plan_sections):
            try:
                if card is not None:
                    card.setVisible(locked)
                set_section_locked(*widgets, locked=locked)
            except RuntimeError:
                continue

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
