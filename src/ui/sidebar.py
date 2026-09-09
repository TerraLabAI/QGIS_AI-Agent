# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat history as a sidebar: the site's Sidebar Nav, docked at the left."""



















from __future__ import annotations

from qgis.PyQt.QtCore import QEasingCurve, QEvent, QRect, Qt, QVariantAnimation, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .font_scale import scale_qss_font_px
from .icons import icon_for, logo_pixmap, pixmap_for
from .style import (
    _BTN_ICON,
    ACCENT_INK,
    BTN_PX,
    FONT_BASE,
    FONT_MICRO,
    HOVER_ON,
    INK,
    INK_2,
    INK_3,
    LINE,
    MOTION_HOVER_MS,
    PAGE,
    RADIUS_CONTROL,
    accent_color,
    qcolor,
    repolish,
)
from .widgets import ElidedLabel

EXPANDED_WIDTH = 240
COLLAPSED_WIDTH = 36


WIDE_PANEL_PX = 640
ROW_PX = 32




MAX_RECENT_ROWS = 24
_TOP_PX = 40
_GLYPH = 18
_GLYPH_SLOT = 20
_MARK = 20
_CHEVRON = 16
_PAD = 8
_GAP = 8
_SLIDE_MS = 200


_COPY_HIDE_PX = 120

_QSS = scale_qss_font_px(
    f"QWidget#sidebar {{ background: {PAGE}; border-right: 1px solid {LINE}; }}"
    f"QLabel#sidebarWorkspace {{ font-size: {FONT_BASE}px; font-weight: 500; color: {INK};"
    " background: transparent; border: none; }"
    f"QLabel#sidebarNav {{ font-size: {FONT_BASE}px; font-weight: 500; color: {INK_2};"
    " background: transparent; border: none; }"
    f"QLabel#sidebarRecent {{ font-size: {FONT_BASE}px; color: {INK_2};"
    " background: transparent; border: none; }"
    f'QLabel#sidebarRecent[on="true"] {{ color: {INK}; }}'
    f"QLabel#sidebarUpgrade {{ font-size: {FONT_BASE}px; font-weight: 500; color: {ACCENT_INK};"
    " background: transparent; border: none; }"
    f"QLabel#sidebarMicro {{ font-size: {FONT_MICRO}px; font-weight: 500; color: {INK_3};"
    " padding: 5px 8px 6px 8px; background: transparent; border: none; }"
    f"QFrame#sidebarGlide {{ background: {HOVER_ON}; border: none;"
    f" border-radius: {RADIUS_CONTROL}px; }}"
    f"QFrame#sidebarRule {{ background: {LINE}; border: none; max-height: 1px; min-height: 1px; }}"
    "QScrollArea#sidebarScroll { background: transparent; border: none; }"
    "QScrollArea#sidebarScroll > QWidget > QWidget { background: transparent; }"
    "QScrollBar:vertical { background: transparent; width: 6px; margin: 2px 1px; }"
    f"QScrollBar::handle:vertical {{ background: {LINE}; border-radius: 3px; min-height: 24px; }}"
    "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
    "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
)


def _project_name(path: str) -> str:
    return str(path or "").replace("\\", "/").rstrip("/").split("/")[-1]


class _Row(QWidget):
    """One 32 px row: a glyph in its 20 px slot, the copy, an optional meta."""






    clicked = pyqtSignal()
    hovered = pyqtSignal(object)

    def __init__(self, glyph: str, text: str, role: str = "sidebarNav", parent=None, color=None):
        super().__init__(parent)
        self._glyph_name = glyph
        self._color = color
        self._on = False
        self.setFixedHeight(ROW_PX)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)



        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(text)
        row = QHBoxLayout(self)
        row.setContentsMargins(_PAD, 0, _PAD, 0)
        row.setSpacing(_GAP)
        self._glyph = QLabel(self)
        self._glyph.setFixedSize(_GLYPH_SLOT, _GLYPH_SLOT)
        self._glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self._glyph, 0, Qt.AlignmentFlag.AlignVCenter)




        self._text = ElidedLabel(text, self)
        self._text.setObjectName(role)
        self._text.setTextFormat(Qt.TextFormat.PlainText)
        self._text.setProperty("on", False)
        self._text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self._text, 1, Qt.AlignmentFlag.AlignVCenter)
        self.setToolTip(text)
        self._paint_glyph()

    def _paint_glyph(self) -> None:
        if not self._glyph_name:
            self._glyph.clear()
            return
        color = self._color if self._color is not None else qcolor(INK_2)
        self._glyph.setPixmap(pixmap_for(self, self._glyph_name, _GLYPH, color))

    def set_copy_visible(self, visible: bool) -> None:
        self._text.setVisible(visible)

    def set_on(self, on: bool) -> None:
        """The open chat: ink on the pressed step, whatever the pointer does."""
        self._on = bool(on)
        self._text.setProperty("on", self._on)
        repolish(self._text)
        self.update()

    def set_text(self, text: str) -> None:
        self._text.setText(text)
        self.setToolTip(text)

    def enterEvent(self, event):  # noqa: N802 - Qt override
        super().enterEvent(event)
        self.hovered.emit(self)
        if self._text.objectName() == "sidebarRecent" and not self._on:
            self._text.setProperty("on", True)
            repolish(self._text)

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        super().leaveEvent(event)
        if self._text.objectName() == "sidebarRecent" and not self._on:
            self._text.setProperty("on", False)
            repolish(self._text)

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event):  # noqa: N802 - Qt override
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):  # noqa: N802 - Qt override
        super().focusOutEvent(event)
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt override


        if self._on or self.hasFocus():
            from qgis.PyQt.QtCore import QRectF
            from qgis.PyQt.QtGui import QPainter

            painter = QPainter(self)
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(qcolor(HOVER_ON))
                painter.drawRoundedRect(QRectF(self.rect()), RADIUS_CONTROL, RADIUS_CONTROL)
            finally:
                painter.end()
        super().paintEvent(event)

    def changeEvent(self, event):  # noqa: N802 - Qt override
        super().changeEvent(event)
        try:
            if event.type() == QEvent.Type.PaletteChange:
                self._paint_glyph()
        except (RuntimeError, AttributeError):
            pass


class _GlideHost(QWidget):
    """The column the rows sit in, with the one highlight that glides."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._glide = QFrame(self)
        self._glide.setObjectName("sidebarGlide")
        self._glide.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._glide.hide()
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(MOTION_HOVER_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_frame)
        self._target = None
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def glide_to(self, row: QWidget) -> None:
        """Move the highlight under ``row``: a jump the first time, a glide after."""
        target = QRect(row.geometry())
        if not self._glide.isVisible() or not self.isVisible():
            self._anim.stop()
            self._glide.setGeometry(target)
            self._glide.show()
            self._glide.lower()
            return
        self._anim.stop()
        self._anim.setStartValue(self._glide.geometry())
        self._anim.setEndValue(target)
        self._anim.start()

    def _on_frame(self, value) -> None:
        try:
            self._glide.setGeometry(value)
        except TypeError:
            pass

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        super().leaveEvent(event)
        self._anim.stop()
        self._glide.hide()


class ChatSidebar(QWidget):
    """The sidebar."""



    new_thread_requested = pyqtSignal()
    home_requested = pyqtSignal()
    search_requested = pyqtSignal()
    thread_selected = pyqtSignal(str)
    upgrade_requested = pyqtSignal()
    collapsed_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_QSS)
        self._collapsed = False
        self._open = False
        self._threads: list = []
        self._current_thread = ""
        self._recent_rows: list = []
        self._shown_threads: list = []
        self._copy: list = []
        self.setFixedWidth(EXPANDED_WIDTH)
        self._slide = QVariantAnimation(self)
        self._slide.setDuration(_SLIDE_MS)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._slide.valueChanged.connect(self._on_slide)

        col = QVBoxLayout(self)
        col.setContentsMargins(_PAD, _PAD, _PAD, _PAD)
        col.setSpacing(0)
        self._col = col


        top = QWidget(self)
        top.setFixedHeight(_TOP_PX)
        head = QHBoxLayout(top)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(0)
        self._mark = QLabel(top)
        self._mark.setFixedSize(_MARK + 2 * _PAD, ROW_PX)
        self._mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._mark.setPixmap(logo_pixmap(self._mark, _MARK))
        head.addWidget(self._mark, 0, Qt.AlignmentFlag.AlignVCenter)
        self._workspace = QLabel(self.tr("AI Agent"), top)
        self._workspace.setObjectName("sidebarWorkspace")
        self._workspace.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        head.addWidget(self._workspace, 1, Qt.AlignmentFlag.AlignVCenter)
        self._chevron = QLabel(top)
        self._chevron.setFixedSize(_CHEVRON, _CHEVRON)
        self._chevron.setPixmap(pixmap_for(top, "chevron_down", _CHEVRON, qcolor(INK_3)))
        head.addWidget(self._chevron, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addSpacing(_GAP)
        self._fold_btn = self._icon_button(top, "sidebar", self.tr("Collapse the sidebar"))
        self._fold_btn.clicked.connect(self.collapse)
        head.addWidget(self._fold_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self._expand_btn = self._icon_button(top, "sidebar", self.tr("Expand the sidebar"))
        self._expand_btn.setFixedSize(COLLAPSED_WIDTH, COLLAPSED_WIDTH)
        self._expand_btn.clicked.connect(self.expand)
        self._expand_btn.hide()
        head.addWidget(self._expand_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addWidget(top)
        col.addSpacing(_PAD)


        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("sidebarScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._host = _GlideHost(self._scroll)
        self._rows = QVBoxLayout(self._host)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(1)
        self._new_row = self._nav_row("new_chat", self.tr("New chat"), self.new_thread_requested)
        self._home_row = self._nav_row("home", self.tr("Home"), self.home_requested)
        self._search_row = self._nav_row("search", self.tr("Search chats"), self.search_requested)
        self._rows.addSpacing(_PAD)
        self._recents_label = QLabel(self.tr("Recents").upper(), self._host)
        self._recents_label.setObjectName("sidebarMicro")
        self._recents_label.setFont(self._micro_font())
        self._rows.addWidget(self._recents_label)
        self._copy.append(self._recents_label)
        self._recents_at = self._rows.count()
        self._rows.addStretch(1)
        self._scroll.setWidget(self._host)
        col.addWidget(self._scroll, 1)


        self._rule = QFrame(self)
        self._rule.setObjectName("sidebarRule")
        self._rule.setFrameShape(QFrame.Shape.NoFrame)
        col.addWidget(self._rule)
        col.addSpacing(_PAD)
        self._upgrade_row = _Row("sparkles", self.tr("Upgrade"), "sidebarUpgrade", self, accent_color())
        self._upgrade_row.clicked.connect(self.upgrade_requested.emit)
        self._copy.append(self._upgrade_row)
        col.addWidget(self._upgrade_row)
        self.set_paid(True)



    def _icon_button(self, parent, glyph: str, tooltip: str) -> QToolButton:
        button = QToolButton(parent)
        button.setStyleSheet(_BTN_ICON)
        button.setFixedSize(BTN_PX, BTN_PX)
        button.setIcon(icon_for(button, glyph, _GLYPH, qcolor(INK_3)))
        button.setIconSize(self._glyph_size())
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setAutoRaise(True)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        return button

    @staticmethod
    def _glyph_size():
        from qgis.PyQt.QtCore import QSize

        return QSize(_GLYPH, _GLYPH)

    def _micro_font(self):
        from qgis.PyQt.QtGui import QFont

        font = QFont(self.font())
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 104)
        return font

    def _nav_row(self, glyph: str, text: str, signal) -> _Row:
        row = _Row(glyph, text, "sidebarNav", self._host)
        row.clicked.connect(signal.emit)
        row.hovered.connect(self._host.glide_to)
        self._rows.addWidget(row)
        self._copy.append(row)
        return row



    def set_project(self, path: str) -> None:
        """The workspace row names the open project, or says it is unsaved."""
        name = _project_name(path)
        self._workspace.setText(name or self.tr("Unsaved project"))
        self._workspace.setToolTip(str(path or ""))

    def set_threads(self, items, current_project_path: str | None = None) -> None:
        threads = [i for i in (items or []) if isinstance(i, dict) and i.get("id")]
        if current_project_path is not None:
            self.set_project(current_project_path)



        threads = threads[:MAX_RECENT_ROWS]
        shown = [(str(i.get("id") or ""), str(i.get("title") or "")) for i in threads]
        if shown == self._shown_threads and len(self._recent_rows) == len(shown):
            self._threads = threads
            self.set_current_thread(self._current_thread)
            return
        self._threads = threads
        same_rows = ([i for i, _ in shown] == [i for i, _ in self._shown_threads]
                     and len(self._recent_rows) == len(shown))
        self._shown_threads = shown
        if same_rows:


            for row, item in zip(self._recent_rows, threads):
                row.set_text(str(item.get("title") or self.tr("Untitled chat")).strip())
            self.set_current_thread(self._current_thread)
            return
        self._rebuild()

    def set_current_thread(self, thread_id: str) -> None:
        self._current_thread = str(thread_id or "")
        for row, item in zip(self._recent_rows, self._threads):
            row.set_on(str(item.get("id") or "") == self._current_thread)

    def set_paid(self, paid: bool) -> None:
        """Upgrade shows for a free account only."""
        self._rule.setVisible(not paid)
        self._upgrade_row.setVisible(not paid)

    def threads(self) -> list:
        return list(self._threads)

    def _rebuild(self) -> None:
        for row in self._recent_rows:
            self._rows.removeWidget(row)
            row.hide()
            row.setParent(None)
            row.deleteLater()
        self._recent_rows = []
        at = self._recents_at
        for item in self._threads:
            thread_id = str(item.get("id") or "")
            title = str(item.get("title") or self.tr("Untitled chat")).strip()
            row = _Row("", title, "sidebarRecent", self._host)
            row.clicked.connect(lambda t=thread_id: self.thread_selected.emit(t))
            row.hovered.connect(self._host.glide_to)
            row.set_on(thread_id == self._current_thread)
            row.set_copy_visible(not self._collapsed)
            self._rows.insertWidget(at, row)
            self._recent_rows.append(row)
            at += 1
        self._recents_label.setVisible(bool(self._threads) and not self._collapsed)



    def host_is_wide(self) -> bool:
        """Whether the panel has room for two columns (``WIDE_PANEL_PX``)."""
        host = self.parentWidget()
        while host is not None and host.objectName() != "chatPanel":
            host = host.parentWidget()
        width = host.width() if host is not None else 0
        return width > WIDE_PANEL_PX

    def is_open(self) -> bool:
        """Whether the user has the sidebar out (a signed-out panel still hides it)."""
        return self._open

    def toggle(self) -> None:
        self._open = not self._open
        self.setVisible(self._open)

    def sync_visible(self, allowed: bool) -> None:
        """Shown when opened and the panel may show a thread at all."""
        self.setVisible(self._open and bool(allowed))

    def is_collapsed(self) -> bool:
        return self._collapsed

    def collapse(self) -> None:
        self._set_collapsed(True)

    def expand(self) -> None:
        self._set_collapsed(False)

    def _set_collapsed(self, collapsed: bool) -> None:
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        target = COLLAPSED_WIDTH if collapsed else EXPANDED_WIDTH
        self._slide.stop()
        if not self.isVisible():
            self._apply_width(target)
        else:
            self._slide.setStartValue(self.width())
            self._slide.setEndValue(target)
            self._slide.start()
        self.collapsed_changed.emit(collapsed)

    def _on_slide(self, value) -> None:
        try:
            self._apply_width(int(value))
        except (TypeError, ValueError):
            pass

    def _apply_width(self, width: int) -> None:
        """One frame of the slide: the width, and the copy in or out of it."""
        self.setFixedWidth(width)
        show_copy = width >= _COPY_HIDE_PX
        margin = _PAD if show_copy else 0
        self._col.setContentsMargins(margin, _PAD, margin, _PAD)
        for widget in self._copy:
            if isinstance(widget, _Row):
                widget.set_copy_visible(show_copy)
            else:
                widget.setVisible(show_copy and bool(self._threads))

        for row in self._recent_rows:
            row.set_copy_visible(show_copy)
            row.setVisible(show_copy)
        self._workspace.setVisible(show_copy)
        self._chevron.setVisible(show_copy)
        self._mark.setVisible(show_copy)
        self._fold_btn.setVisible(show_copy)
        self._expand_btn.setVisible(not show_copy)


def install_sidebar(panel) -> ChatSidebar:
    """Put a sidebar at the left of ``panel``'s thread and empty state."""






    sidebar = ChatSidebar(panel)
    column = panel._col
    index = column.indexOf(panel.message_list)
    thread = QWidget(panel)
    thread_col = QVBoxLayout(thread)
    thread_col.setContentsMargins(0, 0, 0, 0)
    thread_col.setSpacing(0)
    column.removeWidget(panel.message_list)
    column.removeWidget(panel.empty_state)
    thread_col.addWidget(panel.message_list, 1)
    thread_col.addWidget(panel.empty_state, 1)
    host = QWidget(panel)
    host.setObjectName("threadHost")
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(0)
    row.addWidget(sidebar, 0)
    row.addWidget(thread, 1)
    column.insertWidget(max(0, index), host, 1)



    panel._thread_host = host
    sidebar.hide()
    return sidebar
