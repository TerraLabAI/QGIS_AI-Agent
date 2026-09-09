# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat search: the site's Search, a command palette over the panel."""




















from __future__ import annotations

import os
from datetime import datetime

from qgis.PyQt.QtCore import (
    QCoreApplication,
    QDateTime,
    QEasingCurve,
    QEvent,
    QLocale,
    QPoint,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
    pyqtSignal,
)
from qgis.PyQt.QtGui import QBrush, QPainter
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .card_base import reduced_motion
from .font_scale import scale_qss_font_px
from .icons import pixmap_for
from .style import (
    ACCENT_INK,
    FONT_BASE,
    FONT_HINT,
    FONT_MICRO,
    INK,
    INK_3,
    LINE,
    LINE_STRONG,
    MOTION_POP_MS,
    RADIUS_CARD,
    RADIUS_CHIP,
    SURFACE,
    drop_shadow,
    hover_pill,
    qcolor,
)
from .widgets import IconButton



SHEET_WIDTH = 300
SHEET_MIN_WIDTH = 240
SHEET_MARGIN = 24
SHEET_TOP = 32
SEARCH_ROW_PX = 44
ROW_HEIGHT = 33
_ROW_RADIUS = RADIUS_CHIP
_ROW_PAD_X = 8
_LIST_PAD = 4
_SHADOW = 16
_SEARCH_GLYPH = 14
_MAX_ROWS = 60
_POP_RISE = 4


_QUERY_DEBOUNCE_MS = 100

_META_MAX_PX = 96

_SHEET_QSS = scale_qss_font_px(
    f"QFrame#historySheet {{ background: {SURFACE};"
    f" border: 1px solid {LINE_STRONG}; border-radius: {RADIUS_CARD}px; }}"
    f"QLineEdit#historySearch {{ background: transparent; border: none;"
    f" padding: 0; font-size: {FONT_BASE}px; color: {INK};"
    f" selection-background-color: {LINE_STRONG}; }}"
    f"QFrame#historyRule {{ background: {LINE}; border: none; max-height: 1px; min-height: 1px; }}"

    f"QLabel#historySection {{ font-size: {FONT_MICRO}px; font-weight: 500; color: {INK_3};"
    " padding: 5px 8px 6px 8px; background: transparent; }"
    f"QLabel#historyEmpty {{ font-size: {FONT_BASE}px; color: {INK_3};"
    " padding: 22px 8px; background: transparent; }"
    f"QLabel#historyTitle {{ font-size: {FONT_BASE}px; color: {INK}; background: transparent; }}"
    f"QLabel#historyMeta {{ font-size: {FONT_HINT}px; color: {INK_3}; background: transparent; }}"
    "QScrollArea#historyScroll { background: transparent; border: none; }"
    "QScrollArea#historyScroll > QWidget > QWidget { background: transparent; }"
    "QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }"
    f"QScrollBar::handle:vertical {{ background: {LINE_STRONG};"
    " border-radius: 3px; min-height: 24px; }"
    "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
    "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
)


def tr(text: str) -> str:
    return QCoreApplication.translate("HistoryPopup", text)




_CLOCK_SKEW_S = 120


def relative_time(iso: str, now: QDateTime | None = None) -> str:
    """"Just now", "5 min ago", "2 h ago", "Yesterday", "3 days ago", else the short local date."""

    if not iso:
        return ""
    stamp = QDateTime.fromString(iso, Qt.DateFormat.ISODate)
    if not stamp.isValid():
        return iso
    stamp = stamp.toLocalTime()
    now = (now or QDateTime.currentDateTime()).toLocalTime()
    seconds = stamp.secsTo(now)
    if seconds < 0:





        if -seconds <= _CLOCK_SKEW_S:
            return tr("Just now")
        return QLocale().toString(stamp.date(), QLocale.FormatType.ShortFormat)
    if seconds < 60:
        return tr("Just now")
    minutes = seconds // 60
    if minutes < 60:
        return tr("{n} min ago").format(n=minutes)
    hours = minutes // 60
    if hours < 24 and stamp.date() == now.date():
        return tr("{n} h ago").format(n=hours)
    days = stamp.date().daysTo(now.date())
    if days <= 1:
        return tr("Yesterday")
    if days < 7:
        return tr("{n} days ago").format(n=days)
    return QLocale().toString(stamp.date(), QLocale.FormatType.ShortFormat)


BAND_TODAY = "today"
BAND_YESTERDAY = "yesterday"
BAND_WEEK = "week"
BAND_MONTH = "month"
BAND_OLDER = "older"


def _local_date(iso: str):
    """The local calendar date of an ISO stamp, or None when unreadable."""




    text = str(iso or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return stamp.date()
    return stamp.astimezone().date()


def date_band(iso: str, now=None) -> str:
    """Which band of the list a chat falls in, ChatGPT's grouping."""
    day = _local_date(iso)
    if day is None:
        return BAND_OLDER
    today = _local_date(now) if isinstance(now, str) else (now or datetime.now().astimezone())
    today = today if not hasattr(today, "date") else today.date()
    days = (today - day).days
    if days <= 0:
        return BAND_TODAY
    if days == 1:
        return BAND_YESTERDAY
    if days < 7:
        return BAND_WEEK
    if days < 30:
        return BAND_MONTH
    return BAND_OLDER


def band_label(band: str) -> str:
    return {
        BAND_TODAY: tr("Today"),
        BAND_YESTERDAY: tr("Yesterday"),
        BAND_WEEK: tr("Previous 7 days"),
        BAND_MONTH: tr("Previous 30 days"),
    }.get(band, tr("Older"))


def _same_project(left: str, right: str) -> bool:
    """Whether two project paths name one file."""






    left, right = str(left or ""), str(right or "")
    if not left or not right:
        return left == right
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _project_name(path: str) -> str:
    return str(path or "").replace("\\", "/").rstrip("/").split("/")[-1]


def filter_threads(items: list, query: str, text_of=None) -> list:
    """The chats matching ``query``: a title match first, then a match in the message text ``text_of(thread_id)`` returns (only asked for the."""


    query = (query or "").strip().lower()
    if not query:
        return list(items)
    by_title, by_text = [], []
    for item in items:
        if query in str(item.get("title") or "").lower():
            by_title.append(item)
        elif text_of is not None:
            try:
                text = text_of(str(item.get("id") or ""))
            except Exception:  # noqa: BLE001 - a broken provider must not break the search
                text = ""
            if query in str(text or "").lower():
                by_text.append(item)
    return by_title + by_text


def group_threads(items: list, current_project: str, now=None) -> list:
    """The sheet's sections: the open project first, cut in date bands, then one "Other projects" section."""



    mine, others = [], []
    for item in items:
        path = str(item.get("project_path") or "")
        (mine if _same_project(path, current_project) else others).append(item)
    sections = []
    for band in (BAND_TODAY, BAND_YESTERDAY, BAND_WEEK, BAND_MONTH, BAND_OLDER):
        rows = [i for i in mine
                if date_band(str(i.get("updated_at") or i.get("updated_at_iso") or ""), now) == band]
        if rows:
            sections.append((band_label(band), rows))
    if others:
        sections.append((tr("Other projects"), others))
    return sections


class _Row(QWidget):
    """One 33 px line: the title, the meta at the right, a bin on hover or when selected."""


    clicked = pyqtSignal()
    delete_clicked = pyqtSignal()

    def __init__(self, title: str, deletable: bool = False, parent=None, meta: str = ""):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(ROW_HEIGHT)
        self._hover = False
        self._selected = False



        self._current = False
        self._full_title = title
        row = QHBoxLayout(self)
        row.setContentsMargins(_ROW_PAD_X, 0, _ROW_PAD_X - 4, 0)
        row.setSpacing(8)
        self._title = QLabel(self)
        self._title.setObjectName("historyTitle")
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        self._title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self._title, 1, Qt.AlignmentFlag.AlignVCenter)
        self._full_meta = meta
        self._meta = QLabel(self)
        self._meta.setObjectName("historyMeta")
        self._meta.setTextFormat(Qt.TextFormat.PlainText)
        self._meta.setVisible(bool(meta))
        row.addWidget(self._meta, 0, Qt.AlignmentFlag.AlignVCenter)
        self._delete = None
        if deletable:
            self._delete = IconButton(self, "trash", 14, tr("Delete chat"))
            self._delete.setFixedSize(24, 24)
            self._delete.clicked.connect(self.delete_clicked.emit)
            self._delete.hide()
            row.addWidget(self._delete, 0, Qt.AlignmentFlag.AlignVCenter)
        self._elide()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._sync_delete()
        self.update()

    def set_current(self, current: bool) -> None:
        """Whether this row is the conversation open in the panel."""
        self._current = bool(current)
        self.update()

    def _sync_delete(self) -> None:
        if self._delete is None:
            return
        showing = self._hover or self._selected or self._current
        self._delete.setVisible(showing)

        self._meta.setVisible(bool(self._full_meta) and not showing)

    def _elide(self) -> None:
        meta_width = 0
        if self._full_meta:
            meta_width = min(_META_MAX_PX, self._meta.fontMetrics().horizontalAdvance(self._full_meta) + 4)
            self._meta.setText(self._meta.fontMetrics().elidedText(
                self._full_meta, Qt.TextElideMode.ElideRight, max(24, meta_width)))
        width = self.width() - 2 * _ROW_PAD_X - 32 - meta_width
        metrics = self._title.fontMetrics()
        self._title.setText(metrics.elidedText(self._full_title, Qt.TextElideMode.ElideRight, max(40, width)))

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._elide()

    def enterEvent(self, event):  # noqa: N802 - Qt override
        self._hover = True
        self._sync_delete()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        self._hover = False
        self._sync_delete()
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):  # noqa: N802 - Qt override



        if self._hover or self._selected or self._current:
            painter = QPainter(self)
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                if self._hover or self._selected:
                    painter.setBrush(QBrush(hover_pill()))
                    painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                                            _ROW_RADIUS, _ROW_RADIUS)
                if self._current:
                    painter.setBrush(QBrush(qcolor(ACCENT_INK)))
                    painter.drawRoundedRect(QRectF(1.0, 8.0, 2.0, max(4.0, self.height() - 16.0)),
                                            1.0, 1.0)
            finally:
                painter.end()
        super().paintEvent(event)


class HistoryPopup(QWidget):
    """The palette."""



    thread_selected = pyqtSignal(str)
    new_thread_requested = pyqtSignal()
    thread_delete_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("historyPopupHost")
        self._threads: list = []
        self._current_project = ""
        self._current_thread = ""
        self._text_provider = None
        self._text_cache: dict = {}
        self._rows: list = []
        self._built_key = None
        self._selected = -1



        self._limit = _MAX_ROWS
        self._final = QPoint()
        self._query_timer = QTimer(self)
        self._query_timer.setSingleShot(True)
        self._query_timer.setInterval(_QUERY_DEBOUNCE_MS)
        self._query_timer.timeout.connect(self._rebuild)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(_SHADOW, _SHADOW, _SHADOW, _SHADOW)
        outer.setSpacing(0)
        self._frame = QFrame(self)
        self._frame.setObjectName("historySheet")
        self._frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._frame.setStyleSheet(_SHEET_QSS)
        drop_shadow(self._frame, "overlay")
        outer.addWidget(self._frame)
        col = QVBoxLayout(self._frame)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)


        head_host = QWidget(self._frame)
        head_host.setFixedHeight(SEARCH_ROW_PX)
        head = QHBoxLayout(head_host)
        head.setContentsMargins(12, 0, 12, 0)
        head.setSpacing(8)
        self._search_glyph = QLabel(head_host)
        self._search_glyph.setFixedSize(_SEARCH_GLYPH, _SEARCH_GLYPH)
        self._search_glyph.setPixmap(pixmap_for(head_host, "search", _SEARCH_GLYPH, qcolor(INK_3)))
        head.addWidget(self._search_glyph, 0, Qt.AlignmentFlag.AlignVCenter)
        self._search = QLineEdit(head_host)
        self._search.setObjectName("historySearch")
        self._search.setPlaceholderText(self.tr("Search chats..."))
        self._search.setClearButtonEnabled(False)
        self._search.textChanged.connect(self._on_query)
        self._search.installEventFilter(self)
        head.addWidget(self._search, 1, Qt.AlignmentFlag.AlignVCenter)
        col.addWidget(head_host)
        rule = QFrame(self._frame)
        rule.setObjectName("historyRule")
        rule.setFrameShape(QFrame.Shape.NoFrame)
        col.addWidget(rule)

        self._scroll = QScrollArea(self._frame)
        self._scroll.setObjectName("historyScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._content = QWidget(self._scroll)
        self._list = QVBoxLayout(self._content)
        self._list.setContentsMargins(_LIST_PAD, _LIST_PAD, _LIST_PAD, _LIST_PAD)
        self._list.setSpacing(0)
        self._scroll.setWidget(self._content)
        col.addWidget(self._scroll, 1)

        self._pop = QVariantAnimation(self)
        self._pop.setDuration(MOTION_POP_MS)
        self._pop.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._pop.valueChanged.connect(self._on_pop)
        self._rebuild()



    def set_threads(self, items, current_project_path: str | None = None) -> None:
        self._threads = [i for i in (items or []) if isinstance(i, dict) and i.get("id")]
        if current_project_path is not None:
            self._current_project = str(current_project_path or "")
        self._text_cache.clear()



        if self.isVisible():
            self._rebuild()

    def set_current_project(self, path: str) -> None:
        self._current_project = str(path or "")
        if self.isVisible():
            self._rebuild()

    def set_current_thread(self, thread_id: str) -> None:
        self._current_thread = str(thread_id or "")
        self._rebuild()

    def set_thread_text_provider(self, provider) -> None:
        """``provider(thread_id) -> str``: the text of a chat's messages, asked only for the chats whose title does not match the search."""

        self._text_provider = provider
        self._text_cache.clear()

    def threads(self) -> list:
        return list(self._threads)



    def show_over(self, panel: QWidget) -> None:
        """Open over ``panel``, centred, a palette's distance from the top."""
        width = max(SHEET_MIN_WIDTH, min(SHEET_WIDTH, panel.width() - SHEET_MARGIN))
        self._search.clear()
        self._text_cache.clear()
        self._limit = _MAX_ROWS
        self._rebuild()
        self.setFixedWidth(width + 2 * _SHADOW)
        self._fit_height(max(160, panel.height() - SHEET_TOP - SHEET_MARGIN))
        origin = panel.mapToGlobal(QPoint(0, 0))
        x = origin.x() + (panel.width() - self.width()) // 2
        y = origin.y() + SHEET_TOP - _SHADOW
        self._final = QPoint(x, y)
        self.move(self._final)
        self.show()
        self._pop_in(panel)
        self._search.setFocus(Qt.FocusReason.PopupFocusReason)

    def show_under(self, button: QWidget, panel: QWidget | None = None) -> None:
        """Kept for callers of the old anchored popover: the sheet is centred."""
        self.show_over(panel or button.window())

    def _fit_height(self, cap: int) -> None:
        """Sized on open to its rows, up to ``cap``; a search never resizes it, so the list does not jump under the pointer."""

        self._list.activate()
        rows = self._list.sizeHint().height()
        chrome = SEARCH_ROW_PX + 1 + 2
        self.setFixedHeight(min(cap, rows + chrome) + 2 * _SHADOW)

    def _pop_in(self, panel: QWidget) -> None:
        """The site's pop-in: opacity from 0 and a short rise, 160 ms."""
        self._pop.stop()
        if not panel.isVisible() or reduced_motion(panel):
            self.setWindowOpacity(1.0)
            return
        self.setWindowOpacity(0.0)
        self._pop.setStartValue(0.0)
        self._pop.setEndValue(1.0)
        self._pop.start()

    def _on_pop(self, value) -> None:
        try:
            progress = float(value)
        except (TypeError, ValueError):
            progress = 1.0
        self.setWindowOpacity(progress)
        self.move(QPoint(self._final.x(), self._final.y() + int(_POP_RISE * (1.0 - progress))))

    def hideEvent(self, event):  # noqa: N802 - Qt override
        self._pop.stop()



        self._query_timer.stop()
        super().hideEvent(event)



    def _clear_rows(self) -> None:
        self._rows = []
        self._selected = -1
        self._built_key = None
        while self._list.count():
            item = self._list.takeAt(0)
            widget = item.widget()
            if widget is not None:


                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _text_of(self, thread_id: str) -> str:
        if self._text_provider is None:
            return ""
        if thread_id not in self._text_cache:
            self._text_cache[thread_id] = str(self._text_provider(thread_id) or "")
        return self._text_cache[thread_id]

    def _on_query(self, _text: str) -> None:


        self._limit = _MAX_ROWS
        self._query_timer.start()

    def _rebuild(self) -> None:
        query = self._search.text().strip()







        key = (tuple((str(t.get("id")), str(t.get("title") or ""),
                     str(t.get("project_path") or ""),
                     str(t.get("updated_at") or t.get("updated_at_iso") or ""))
                     for t in self._threads),
               self._current_project, self._current_thread, query, self._limit)
        if key == self._built_key and self._list.count():
            self._select(0 if query and self._rows else -1)
            return
        self._clear_rows()
        self._built_key = key
        threads = filter_threads(self._threads, query, self._text_of)

        if not self._threads:
            self._list.addWidget(self._empty(self.tr("No chats yet")))
        elif not threads:
            self._list.addWidget(self._empty(self.tr("No chats match")))
        elif query:

            for item in threads[:self._limit]:
                self._add_row(self._thread_row(item))
            self._add_more_row(len(threads))
        else:
            shown = 0
            for label, items in group_threads(threads, self._current_project):
                items = items[:max(0, self._limit - shown)]
                if not items:
                    break
                self._list.addWidget(self._section(label))
                for item in items:
                    self._add_row(self._thread_row(item))
                shown += len(items)
            self._add_more_row(len(threads))
        self._list.addStretch(1)
        self._select(0 if query and self._rows else -1)

    def _add_row(self, row: _Row) -> None:
        self._rows.append(row)
        self._list.addWidget(row)

    def _add_more_row(self, total: int) -> None:
        """The way past the page that is drawn."""







        if total <= self._limit:
            return
        row = _Row(self.tr("Show older chats"), False, self._content,
                   meta=str(total - self._limit))
        row.clicked.connect(self._show_more)
        self._add_row(row)

    def _show_more(self) -> None:
        at = self._selected
        self._limit += _MAX_ROWS
        self._rebuild()
        if 0 <= at < len(self._rows):
            self._select(at)

    def _section(self, text: str) -> QLabel:
        """A band's micro label: uppercase with 0.04 em of tracking."""

        from qgis.PyQt.QtGui import QFont

        label = QLabel(text.upper(), self._content)
        label.setObjectName("historySection")
        font = QFont(label.font())
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 104)
        label.setFont(font)
        return label

    def _empty(self, text: str) -> QLabel:
        label = QLabel(text, self._content)
        label.setObjectName("historyEmpty")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        return label

    def _thread_row(self, item: dict) -> _Row:
        thread_id = str(item.get("id") or "")
        title = str(item.get("title") or self.tr("Untitled chat")).strip()
        when = relative_time(str(item.get("updated_at") or item.get("updated_at_iso") or ""))
        path = str(item.get("project_path") or "")
        project = _project_name(path) or self.tr("Unsaved project")


        mine = _same_project(path, self._current_project)
        row = _Row(title, True, self._content, meta=when if mine else project)
        row.setToolTip("\n".join(p for p in (title, "  ·  ".join(q for q in (when, project) if q)) if p))
        row.set_current(thread_id == self._current_thread)
        row.clicked.connect(lambda t=thread_id: self._on_pick(t))
        row.delete_clicked.connect(lambda t=thread_id: self._on_delete(t))
        return row



    def _select(self, index: int) -> None:
        if not self._rows:
            self._selected = -1
            return
        index = max(-1, min(index, len(self._rows) - 1))
        for i, row in enumerate(self._rows):
            row.set_selected(i == index)
        self._selected = index
        if index >= 0:
            self._scroll.ensureWidgetVisible(self._rows[index], 0, 4)

    def _move(self, step: int) -> None:
        if not self._rows:
            return
        if self._selected < 0:
            self._select(0 if step > 0 else len(self._rows) - 1)
        else:
            self._select((self._selected + step) % len(self._rows))

    def _activate(self) -> None:
        if 0 <= self._selected < len(self._rows):
            self._rows[self._selected].clicked.emit()

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if watched is self._search and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Down:
                self._move(1)
                return True
            if key == Qt.Key.Key_Up:
                self._move(-1)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):




                if self._query_timer.isActive():
                    self._query_timer.stop()
                    self._rebuild()
                self._activate()
                return True
            if key == Qt.Key.Key_Escape:
                if self._search.text():
                    self._search.clear()
                else:
                    self.hide()
                return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)



    def _on_pick(self, thread_id: str) -> None:
        self.hide()
        self.thread_selected.emit(thread_id)

    def _on_delete(self, thread_id: str) -> None:
        self._threads = [t for t in self._threads if str(t.get("id") or "") != thread_id]
        self._text_cache.pop(thread_id, None)
        self._rebuild()
        self.thread_delete_requested.emit(thread_id)

    def _on_new_thread(self) -> None:
        self.hide()
        self.new_thread_requested.emit()
