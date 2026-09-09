# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

















































from __future__ import annotations

from qgis.PyQt.QtCore import QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.logger import log_warning
from ..core.plan import memory_allowed
from ..core.profile import profile_context
from ..core.settings import Settings
from .external_links import open_external_url
from .font_scale import apply_font_scale_to_tree, scale_px_length
from .icons import icon_for, logo_pixmap, logo_size
from .learn_page import LearnPage
from .permission_chip import glyph_tile
from .settings_account import AccountPageMixin
from .settings_billing import BillingPageMixin
from .settings_pages import (
    MUTED,
    SAVED_HINT_QSS,
    Page,
    nav_qss,
    set_section_locked,
    sidebar_qss,
)
from .settings_personalisation import PersonalisationPageMixin
from .shared import (
    event_pos,
    get_privacy_url,
    get_product_url,
    get_site_url,
    get_terms_url,
    safe_disconnect,
    size_within_screen,
)
from .siblings_page import SiblingsPage
from .style import ACCENT, ACCENT_DARK, FONT_HINT, ON_ACCENT








_DIALOG_W, _DIALOG_H = 1080, 760
_DIALOG_MIN_W, _DIALOG_MIN_H = 820, 560
_NAV_W = 186
_SAVE_DEBOUNCE_MS = 600
_SAVED_HINT_MS = 1600

_NAV_QSS = nav_qss("settingsNav")
_SIDEBAR_QSS = sidebar_qss("settingsSidebar")





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

_WORDMARK_PX = 22
_WORDMARK_QSS = ("font-size: 13px; font-weight: 700; color: palette(text);"
                 " background: transparent;")
_WORDMARK_NOTE_QSS = f"font-size: {FONT_HINT}px; color: {MUTED}; background: transparent;"
_IDENTITY_QSS = (f"QLabel#settingsIdentity {{ font-size: {FONT_HINT}px; color: {MUTED};"
                 " background: transparent; }"
                 "QLabel#settingsIdentity:hover { color: palette(text); }")


_RAIL_CTA_QSS = (
    f"QPushButton {{ background: {ACCENT}; color: {ON_ACCENT}; border: none;"
    " border-radius: 8px; margin: 4px 12px 6px 14px; padding: 7px 10px;"
    " font-size: 12px; font-weight: 700; }"
    f"QPushButton:hover {{ background: {ACCENT_DARK}; }}"
)


class _LinkTile(QWidget):




    clicked = pyqtSignal()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event_pos(event)):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class SettingsDialog(PersonalisationPageMixin, AccountPageMixin, BillingPageMixin, QDialog):


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









        self._upgrade_pill = QPushButton(self.tr("Do more with Pro"), side)
        self._upgrade_pill.setStyleSheet(_RAIL_CTA_QSS)
        self._upgrade_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self._upgrade_pill.setAutoDefault(False)
        self._upgrade_pill.setToolTip(self.tr("What Pro unlocks, on the TerraLab website."))
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
        legal.setStyleSheet(f"font-size: {FONT_HINT}px;")
        legal.setContentsMargins(16, 0, 0, 0)
        col.addWidget(legal)


        return side

    def _build_wordmark(self, side: QFrame) -> QWidget:












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

        open_external_url(get_product_url(), parent=self)

    def _open_site(self, _link: str = "") -> None:

        open_external_url(get_site_url(), parent=self)

    def _add_page(self, glyph: str, label: str, page: QWidget) -> None:
        tint = _NAV_TINTS.get(glyph)
        icon = icon_for(self, glyph, 16, QColor(tint)) if tint else icon_for(self, glyph, 16)
        item = QListWidgetItem(icon, label)
        item.setSizeHint(QSize(0, scale_px_length(32)))
        self._nav.addItem(item)
        self._pages.addWidget(page)

    def _add_action(self, glyph: str, label: str, kind: str) -> None:







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

        if self._nav.currentRow() != self._page_row:
            self._nav.setCurrentRow(self._page_row)

    def show_page(self, index: int) -> None:
        self._nav.setCurrentRow(max(0, min(int(index), self._nav.count() - 1)))

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
            safe_disconnect(timer, "timeout")
        timer.timeout.connect(save)
        timer.start(_SAVE_DEBOUNCE_MS)

    def _flush_pending(self) -> None:
        for timer in list(self._pending.values()):
            if timer.isActive():
                timer.stop()
                timer.timeout.emit()

    def _memory_locked(self) -> bool:







        return not memory_allowed()



    def _build_tutorials(self) -> LearnPage:







        page = LearnPage(self)
        page.opened.connect(self._on_learn_opened)
        return page

    def _build_siblings(self) -> SiblingsPage:







        page = SiblingsPage(self)
        page.opened.connect(self._on_sibling_opened)
        return page

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

            page.add(build_shortcuts_card(page))
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
