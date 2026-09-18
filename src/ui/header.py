# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The header row: mark, wordmark, and every control of the panel."""


































from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, QRectF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QIcon, QPainter, QPixmap, QTextOption
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget

from .checkpoint_sheet import CheckpointSheet
from .font_scale import scale_px_length, widget_pixel_ratio
from .history_popup import HistoryPopup
from .icons import icon_for, logo_pixmap, logo_size
from .shared import event_pos, safe_disconnect
from .style import _BTN_AVATAR, _BTN_PRO_PILL, _HEADER_QSS, PRO_PILL_PX, SPACE_CARD, accent_color, repolish
from .styles import BRAND_GREEN
from .widgets import IconButton

HEADER_HEIGHT = 36



_MARK = 20
_MARK_GAP = 6
_AVATAR_PX = 22
_PRO_GLYPH = 14


class _BrandTile(QWidget):
    """The mark and the name as one clickable thing."""






    clicked = pyqtSignal()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event_pos(event)):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def letter_badge(widget, letter: str, diameter: int = _AVATAR_PX) -> QPixmap:
    """A round leaf-green badge with one dark letter, sharp on any screen."""
    ratio = widget_pixel_ratio(widget)
    physical = max(1, int(round(diameter * ratio)))
    pixmap = QPixmap(physical, physical)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(BRAND_GREEN))
        painter.drawEllipse(0, 0, diameter, diameter)
        font = painter.font()
        font.setPixelSize(max(9, int(diameter * 0.55)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#14210A"))
        painter.drawText(QRectF(0, 0, diameter, diameter), letter,
                         QTextOption(Qt.AlignmentFlag.AlignCenter))
    finally:
        painter.end()
    return pixmap


class Header(QWidget):
    """Logo, title, search, new chat, settings, avatar, dock buttons."""

    thread_selected = pyqtSignal(str)
    new_thread_requested = pyqtSignal()
    thread_delete_requested = pyqtSignal(str)
    account_clicked = pyqtSignal()
    settings_clicked = pyqtSignal()


    checkpoints_requested = pyqtSignal()
    restore_requested = pyqtSignal(str)
    discard_all_requested = pyqtSignal()
    pro_pill_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("hdr")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_HEADER_QSS)
        self.setFixedHeight(HEADER_HEIGHT)
        self._avatar_loader = None
        self._threads: list = []
        self._current_project = ""
        self._dock = None
        self._sidebar = None
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 4, 8, 4)
        row.setSpacing(SPACE_CARD)






        brand_host = _BrandTile(self)
        brand_host.setCursor(Qt.CursorShape.PointingHandCursor)
        brand_host.setToolTip(self.tr("Open the AI Agent page"))
        brand_host.clicked.connect(self._open_product_page)
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
        pill_px = scale_px_length(PRO_PILL_PX)
        self._pro_pill.setStyleSheet(
            _BTN_PRO_PILL + f"QPushButton#hdrProPill {{ border-radius: {pill_px // 2}px; }}")
        self._pro_pill.setFixedHeight(pill_px)
        self._pro_pill.setIcon(icon_for(self._pro_pill, "sparkles", _PRO_GLYPH, accent_color()))
        self._pro_pill.setIconSize(QSize(_PRO_GLYPH, _PRO_GLYPH))
        self._pro_pill.setCursor(Qt.CursorShape.PointingHandCursor)

        self._pro_pill.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._pro_pill.setAutoDefault(False)
        self._pro_pill.setToolTip(self.tr("See what Pro unlocks"))
        self._pro_pill.setAccessibleName(self.tr("See what Pro unlocks"))
        self._pro_pill.setProperty("compact", False)
        self._pro_pill.clicked.connect(self.pro_pill_clicked.emit)
        self._pro_pill.hide()
        self._pro_pill_wanted = False
        self._pro_pill_full_width = self._pro_pill.sizeHint().width()
        row.addWidget(self._pro_pill, 0, Qt.AlignmentFlag.AlignVCenter)



        self._restore_btn = IconButton(
            self, "undo", 18,
            self.tr("Undo the agent's changes: back to an earlier state of this project"))
        self._restore_btn.setFixedSize(26, 26)
        self._sheet = CheckpointSheet(self)
        self._sheet.restore_requested.connect(self.restore_requested.emit)
        self._sheet.discard_all_requested.connect(self.discard_all_requested.emit)
        self._sheet.installEventFilter(self)
        self._restore_btn.clicked.connect(self.open_checkpoints)

        self._restore_btn.hide()
        row.addWidget(self._restore_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._new_btn = IconButton(self, "new_chat", 18, self.tr("New chat"))
        self._new_btn.setObjectName("agentNewChat")
        self._new_btn.setProperty("agentAction", "new_chat")
        self._new_btn.setFixedSize(26, 26)
        self._new_btn.clicked.connect(self.new_thread_requested.emit)
        row.addWidget(self._new_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._history_btn = IconButton(self, "clock", 18, self.tr("Chat history"))
        self._history_btn.setFixedSize(26, 26)
        self._history_popup = HistoryPopup(self)
        self._history_popup.thread_selected.connect(self.thread_selected.emit)
        self._history_popup.new_thread_requested.connect(self.new_thread_requested.emit)
        self._history_popup.thread_delete_requested.connect(self.thread_delete_requested.emit)
        self._history_popup.installEventFilter(self)
        self._history_btn.clicked.connect(self._open_history)
        row.addWidget(self._history_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._settings_btn = IconButton(self, "gear", 18, self.tr("Settings"))
        self._settings_btn.setFixedSize(26, 26)
        self._settings_btn.clicked.connect(self.settings_clicked.emit)
        row.addWidget(self._settings_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._avatar_btn = QToolButton(self)
        self._avatar_btn.setObjectName("avatarBtn")
        self._avatar_btn.setStyleSheet(_BTN_AVATAR)
        self._avatar_btn.setFixedSize(26, 26)
        self._avatar_btn.setIconSize(QSize(_AVATAR_PX, _AVATAR_PX))
        self._avatar_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._avatar_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._avatar_btn.setToolTip(self.tr("Account"))
        self._avatar_btn.setAccessibleName(self.tr("Account"))
        self._avatar_btn.clicked.connect(self.account_clicked.emit)
        self._avatar_btn.hide()
        row.addWidget(self._avatar_btn, 0, Qt.AlignmentFlag.AlignVCenter)




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



    def set_dock(self, dock) -> None:
        """Hosted as ``dock``'s title bar: show the float and close buttons."""
        self._dock = dock
        self._float_btn.setVisible(dock is not None)
        self._close_btn.setVisible(dock is not None)
        self._fit_pro_pill()



    def set_pro_pill_visible(self, visible: bool) -> None:
        """Show the pill (a signed-in free account) or hide it (paid, signed out)."""
        self._pro_pill_wanted = bool(visible)
        self._pro_pill.setVisible(self._pro_pill_wanted)
        self._fit_pro_pill()

    def pro_pill(self) -> QPushButton:
        """The pill itself, for the panel's tests and the UI driver."""
        return self._pro_pill

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._fit_pro_pill()

    def _fit_pro_pill(self) -> None:
        """The word when the row has room for it, the sparkle alone otherwise."""





        if not self._pro_pill_wanted:
            return
        row = self.layout()
        margins = row.contentsMargins()
        others = [w for w in (self._brand, self._restore_btn, self._new_btn, self._history_btn,
                              self._settings_btn, self._avatar_btn, self._float_btn, self._close_btn)
                  if not w.isHidden()]
        used = margins.left() + margins.right()
        used += sum(w.sizeHint().width() for w in others)
        used += row.spacing() * len(others)
        compact = used + self._pro_pill_full_width > self.width()
        if bool(self._pro_pill.property("compact")) == compact:
            return
        self._pro_pill.setProperty("compact", compact)
        if compact:
            self._pro_pill.setText("")
            self._pro_pill.setFixedWidth(self._pro_pill.height())
        else:
            self._pro_pill.setText(self.tr("Get Pro"))
            self._pro_pill.setMinimumWidth(0)
            self._pro_pill.setMaximumWidth(16777215)
        repolish(self._pro_pill)

    def _toggle_floating(self) -> None:
        if self._dock is not None:
            self._dock.setFloating(not self._dock.isFloating())

    def _close_dock(self) -> None:
        if self._dock is not None:
            self._dock.close()

    def set_threads(self, items, current_project_path: str | None = None) -> None:
        """Keep the chat list; ``current_project_path`` also moves the current project when given."""

        self._threads = [i for i in (items or []) if isinstance(i, dict) and i.get("id")]
        if current_project_path is not None:
            self._current_project = str(current_project_path or "")
        self._history_popup.set_threads(self._threads, self._current_project)
        if self._sidebar is not None:
            self._sidebar.set_threads(self._threads, self._current_project)

    def set_current_project(self, path: str) -> None:
        """The open project's file path ("" for an unsaved project)."""
        self._current_project = str(path or "")
        self._history_popup.set_current_project(self._current_project)
        if self._sidebar is not None:
            self._sidebar.set_project(self._current_project)

    def set_current_thread(self, thread_id: str) -> None:
        """The open chat."""
        self._history_popup.set_current_thread(thread_id or "")
        if self._sidebar is not None:
            self._sidebar.set_current_thread(thread_id or "")

    def set_thread_text_provider(self, provider) -> None:
        """``provider(thread_id) -> str``, the message text the search sheet looks through once a title does not match."""

        self._history_popup.set_thread_text_provider(provider)



    def set_checkpoints(self, entries: list) -> None:
        """The states of this chat (core/checkpoints.describe), oldest first."""



        entries = [e for e in (entries or []) if isinstance(e, dict)]
        self._sheet.set_entries(entries)
        self.set_restore_available(any(e.get("available", True) for e in entries))

    def set_restore_available(self, available: bool) -> None:
        """Show or hide the undo button."""

        self._restore_btn.setVisible(bool(available))
        self._fit_pro_pill()

    def open_checkpoints(self) -> bool:
        """Drop the sheet under the undo button; False when there is nothing to restore and the button is not there to open it from."""

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
        """The sheet itself, for the panel's tests and its keyboard."""
        return self._sheet

    def history_menu(self) -> HistoryPopup:
        """The chat search sheet (a QMenu before; same role, richer widget)."""
        return self._history_popup

    def set_sidebar(self, sidebar) -> None:
        """The panel's sidebar: the history button toggles it on a wide panel."""
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

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.Hide:
            for watched_widget, button in ((self._history_popup, self._history_btn),
                                           (self._sheet, self._restore_btn)):
                if watched is watched_widget:
                    button.setDown(False)
                    button.set_hovered(False)
                    button.set_active(False)
        return super().eventFilter(watched, event)



    def set_account(self, email: str, avatar_url: str = "") -> None:
        """Show the avatar: the account's picture, else its first letter."""
        letter = (email or "?").strip()[:1].upper() or "?"
        self._avatar_btn.setIcon(QIcon(letter_badge(self, letter)))
        self._avatar_btn.setToolTip(email or self.tr("Account"))
        self._avatar_btn.show()
        self._fit_pro_pill()
        self._cancel_avatar_load()
        if not avatar_url:
            return
        try:
            from .account_avatar import (
                AccountAvatarLoader,
                cached_avatar_pixmap,
                is_avatar_url_usable,
            )
        except ImportError:
            return
        if not is_avatar_url_usable(avatar_url):
            return
        pixmap = cached_avatar_pixmap(avatar_url, _AVATAR_PX)
        if pixmap is not None:
            self._avatar_btn.setIcon(QIcon(pixmap))
            return
        self._avatar_loader = AccountAvatarLoader(self)
        self._avatar_loader.loaded.connect(self._on_avatar_loaded)
        self._avatar_loader.fetch(avatar_url, _AVATAR_PX)

    def _on_avatar_loaded(self, pixmap) -> None:
        if pixmap is not None and not pixmap.isNull():
            self._avatar_btn.setIcon(QIcon(pixmap))

    def _cancel_avatar_load(self) -> None:
        loader, self._avatar_loader = self._avatar_loader, None
        if loader is None:
            return
        safe_disconnect(loader, "loaded")
        try:
            loader.abort()
        except RuntimeError:
            pass

    def clear_account(self) -> None:
        self._cancel_avatar_load()
        self._avatar_btn.hide()
        self._fit_pro_pill()

    def _open_product_page(self) -> None:
        """The mark and the name: the AI Agent page on terra-lab.ai."""
        from .external_links import open_external_url
        from .shared import get_product_url

        open_external_url(get_product_url(), parent=self)

    def set_title(self, text: str) -> None:
        self._title.setText(text)
