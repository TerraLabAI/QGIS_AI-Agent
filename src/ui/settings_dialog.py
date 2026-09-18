# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Settings dialog behind the footer gear: tabs at the left, one page at a time at the right, every control saved the moment it changes."""














































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
from .connector_page import ConnectorPage
from .connectors_page import ConnectorsPage
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
    PLUGIN_FOLDERS,
    event_pos,
    get_connectors,
    get_hidden_plugins,
    get_privacy_url,
    get_product_url,
    get_qgis_plugins,
    get_site_url,
    get_terms_url,
    get_tool_names,
    known_plugins_by_folder,
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


_FAULT_HINT_MS = 6000

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
    """A widget that reports a click."""



    clicked = pyqtSignal()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event_pos(event)):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class SettingsDialog(PersonalisationPageMixin, AccountPageMixin, BillingPageMixin, QDialog):
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

    def _memory_locked(self) -> bool:
        """Whether Personalisation and Memory are locked: the plan does not include them."""






        return not memory_allowed()



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

    def apply_plan(self) -> None:
        """Lock or unlock the plan's sections from what the server last said."""








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

    def done(self, result):  # noqa: N802 - Qt signature
        self._flush_pending()
        self._cancel_avatar_load()
        super().done(result)

    def closeEvent(self, event):  # noqa: N802 - Qt signature
        self._flush_pending()
        self._cancel_avatar_load()
        super().closeEvent(event)
