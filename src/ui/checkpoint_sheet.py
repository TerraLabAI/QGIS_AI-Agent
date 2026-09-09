# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The restore sheet: every state of this chat, as a timeline you can walk."""












































from __future__ import annotations

from qgis.PyQt.QtCore import QDateTime, QPoint, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QPainter, QPen
from qgis.PyQt.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.checkpoints import (
    KIND_AFTER,
    KIND_EDITS,
    NOT_BACKED_COPY_FAILED,
    NOT_BACKED_NOT_A_FILE,
    NOT_BACKED_TOO_LARGE,
)
from .attach_menu import place_below
from .font_scale import scale_qss_font_px
from .history_popup import relative_time
from .popover_rows import _POPOVER_QSS, _ROW_PAD_X, _ROW_PAD_Y, _TILE, _Row
from .style import (
    FONT_BODY,
    FONT_HINT,
    FONT_MICRO,
    HAIRLINE,
    INK,
    INK_2,
    INK_3,
    LINE,
    LINE_STRONG,
    RADIUS_PANEL,
    SURFACE,
)
from .styles import ERROR_TEXT

SHEET_WIDTH = 360
_MIN_WIDTH = 240


_MAX_ROWS = 200
_MIN_HEIGHT = 120



_INK_GREY = INK_3


_SPINE_X = _ROW_PAD_X + _TILE // 2



_SCROLL_GUTTER = 12

_SHEET_QSS = _POPOVER_QSS + scale_qss_font_px(



    f"QFrame#checkpointSheet {{ background: {SURFACE};"
    f" border: 1px solid {LINE_STRONG}; border-radius: {RADIUS_PANEL}px; }}"
    "QFrame#checkpointSheet QLabel#exampleTitle { font-weight: 600; }"

    f"QLabel#checkpointHeading {{ font-size: {FONT_BODY}px; font-weight: 600; color: {INK};"
    " background: transparent; }"
    f"QLabel#checkpointPromise {{ font-size: {FONT_HINT}px; color: {INK_3};"
    " background: transparent; }"

    f"QLabel#checkpointRun {{ font-size: {FONT_MICRO}px; font-weight: 600; color: {INK_3};"
    " background: transparent; }"
    f"QLabel#checkpointAsked {{ font-size: {FONT_HINT}px; color: {INK_2};"
    " background: transparent; }"


    f"QLabel#checkpointHere {{ font-size: {FONT_MICRO}px; font-weight: 600; color: {INK};"
    " background: transparent; }"




    f"QFrame#checkpointSheet QLabel#checkpointWarning {{ font-size: {FONT_HINT}px;"
    f" color: {INK}; background: transparent; }}"
    f"QFrame#checkpointRule {{ background: {HAIRLINE}; border: none;"
    " min-height: 1px; max-height: 1px; margin: 4px 8px; }"
    "QScrollArea#checkpointScroll { background: transparent; border: none; }"
    "QScrollArea#checkpointScroll > QWidget > QWidget { background: transparent; }"
)


def _whole(value, fallback: int = 0) -> int:
    """A count from a stored history, which is not always a number."""






    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return int(fallback)


def _one_line(text: str) -> str:
    """What a run was asked, on one line: every run of whitespace closed up."""





    return " ".join(str(text or "").split())


def _when(entry: dict) -> str:
    """"3 min ago" for the run this state belongs to, "" when it has no stamp."""
    stamp = entry.get("created_at")
    try:
        seconds = float(stamp or 0.0)
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    moment = QDateTime.fromSecsSinceEpoch(int(seconds))
    return relative_time(moment.toString(Qt.DateFormat.ISODate)) if moment.isValid() else ""


def group_caption(entry: dict, tr) -> str:
    """The first line of a group: which run these states belong to, and when."""





    if entry.get("kind") == KIND_EDITS:
        head = tr("Your own changes")
    else:
        head = tr("Run {n}").format(n=_whole(entry.get("run_index")))
    when = _when(entry)
    return f"{head} · {when}" if when else head


def group_prompt(entry: dict, tr) -> str:
    """The second line of a group: what that run was asked, or where it sits."""
    if entry.get("kind") == KIND_EDITS:
        return tr("Made in QGIS, outside the agent")
    asked = str(entry.get("prompt") or "").strip()
    if asked:
        return asked
    if entry.get("start") or _whole(entry.get("run_index")) <= 1:
        return tr("The first thing this chat did")
    return ""


def checkpoint_title(entry: dict, tr) -> str:
    """The row's own line: which side of its run this state is."""






    kind = entry.get("kind")
    if kind == KIND_AFTER:
        return tr("After this run")
    if kind == KIND_EDITS:
        return tr("Before you continued")
    if entry.get("start") or _whole(entry.get("run_index")) <= 1:
        return tr("Start of this chat")
    return tr("Before this run")


def checkpoint_note(entry: dict, tr) -> str:
    """The row's second line: what going there does, in as few words as fit."""






    if not entry.get("available", True):
        return tr("No longer available")
    kind = entry.get("kind")
    if kind == KIND_AFTER:
        changed = _whole(entry.get("changed_layers"))
        if changed == 0:

            return tr("Project changed")
        if changed == 1:
            return tr("1 layer changed")
        return tr("%n layers changed", "", changed)
    if kind == KIND_EDITS:
        return tr("Your own edits")
    if entry.get("start") or _whole(entry.get("run_index")) <= 1:
        return tr("Nothing had been changed yet")


    return ""


def checkpoint_tag(entry: dict, tr) -> str:
    """The verb on the right: what a click on this row does, in one word."""
    if entry.get("current"):
        return tr("You are here")
    if not entry.get("available", True):
        return ""
    return tr("Restore")


def not_restored_items(entry: dict) -> list:
    """The layers a restore of ``entry`` cannot bring back, as the core describes them: ``[{"name", "reason"}]``."""

    if not entry.get("available", True):
        return []
    items = entry.get("not_backed_up")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("name")]


def _short_reason(reason: str, tr) -> str:
    """Two or three words, for the line inside the row."""
    if reason == NOT_BACKED_TOO_LARGE:
        return tr("too large")
    if reason == NOT_BACKED_NOT_A_FILE:
        return tr("not a file")
    if reason == NOT_BACKED_COPY_FAILED:
        return tr("backup failed")
    return ""


def _long_reason(reason: str, tr) -> str:
    """The whole sentence, for the tooltip, where there is room for it."""
    if reason == NOT_BACKED_TOO_LARGE:
        return tr("its file was over the backup limit")
    if reason == NOT_BACKED_NOT_A_FILE:
        return tr("it lives in a database or a service, not in a file")
    if reason == NOT_BACKED_COPY_FAILED:
        return tr("its backup could not be written")
    return tr("no backup was made")


def checkpoint_warning(entry: dict, tr) -> str:
    """The third line: the layers this restore will not bring back, and why."""






    items = not_restored_items(entry)
    if not items:
        return ""
    named = []
    for item in items:
        reason = _short_reason(str(item.get("reason") or ""), tr)
        name = str(item.get("name"))
        named.append(f"{name} ({reason})" if reason else name)
    if len(named) == 1:
        line = tr("{layer} keeps the data it has now").format(layer=str(items[0].get("name")))
        reason = _short_reason(str(items[0].get("reason") or ""), tr)
        return f"{line} ({reason})" if reason else line
    head = tr("%n layers keep the data they have now", "", len(named))
    return f"{head}: {', '.join(named)}"


def checkpoint_tooltip(entry: dict, tr) -> str:
    """What the row says, plus the whole reason for every layer it names."""
    items = not_restored_items(entry)
    if not items:
        return ""
    lines = [tr("Restoring brings the project and every backed-up layer back."),
             tr("These layers keep the data they have now:")]
    for item in items:
        lines.append("• {name}: {why}".format(
            name=str(item.get("name")), why=_long_reason(str(item.get("reason") or ""), tr)))
    return "\n".join(lines)


def _paint_spine(widget, up: bool, down: bool) -> None:
    """The hairline that threads the tile column, above and below the glyph."""





    if not up and not down:
        return
    painter = QPainter(widget)
    try:
        pen = QPen(QColor(LINE))
        pen.setWidth(1)
        painter.setPen(pen)
        x = _SPINE_X
        height = widget.height()
        middle = height // 2
        gap = _TILE // 2 + 3
        if up:
            painter.drawLine(x, 0, x, max(0, middle - gap))
        if down:
            painter.drawLine(x, min(height, middle + gap), x, height)
    finally:
        painter.end()


class _RunCaption(QWidget):
    """A group head: ``Run 2 · 3 min ago`` over what that run was asked."""

    def __init__(self, caption: str, prompt: str, spine: bool = True, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._full_prompt = _one_line(prompt)
        self._spine = bool(spine)
        self._width_hint = 0
        column = QVBoxLayout(self)
        column.setContentsMargins(_ROW_PAD_X + _TILE + 8, 6, _ROW_PAD_X, 2)
        column.setSpacing(1)
        self._full_caption = str(caption or "")
        self._caption = QLabel(caption, self)
        self._caption.setObjectName("checkpointRun")
        self._caption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        column.addWidget(self._caption)
        self._prompt = QLabel(self)
        self._prompt.setObjectName("checkpointAsked")
        self._prompt.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._prompt.setVisible(bool(self._full_prompt))
        column.addWidget(self._prompt)
        self._elide()

    def set_width_hint(self, width: int) -> None:
        self._width_hint = max(0, int(width or 0))

    def _elide(self) -> None:
        width = max(40, (self._width_hint or self.width()) - (_ROW_PAD_X + _TILE + 8) - _ROW_PAD_X)
        self._caption.setText(self._caption.fontMetrics().elidedText(
            self._full_caption, Qt.TextElideMode.ElideRight, width))
        if self._full_prompt:
            self._prompt.setText(self._prompt.fontMetrics().elidedText(
                self._full_prompt, Qt.TextElideMode.ElideRight, width))
            self._prompt.setToolTip(self._full_prompt)

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._elide()

    def paintEvent(self, event):  # noqa: N802 - Qt override
        super().paintEvent(event)
        if self._spine:
            painter = QPainter(self)
            try:
                pen = QPen(QColor(LINE))
                pen.setWidth(1)
                painter.setPen(pen)
                painter.drawLine(_SPINE_X, 0, _SPINE_X, self.height())
            finally:
                painter.end()


class _CheckpointRow(_Row):
    """A checkpoint row, with the third line when a restore leaves data behind."""






    def __init__(self, glyph: str, accent: str, title: str, note: str, tag: str = "",
                 warning: str = "", parent=None, here: bool = False,
                 spine_up: bool = True, spine_down: bool = True):
        super().__init__(glyph, accent, title, note, tag, parent)
        self._full_warning = str(warning or "")
        self._spine_up = bool(spine_up)
        self._spine_down = bool(spine_down)


        self.restorable = not here
        if here:

            self._tag.setObjectName("checkpointHere")
            style = self._tag.style()
            if style is not None:
                style.unpolish(self._tag)
                style.polish(self._tag)
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self._warning = QLabel(self)
        self._warning.setObjectName("checkpointWarning")
        self._warning.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._warning.setVisible(bool(self._full_warning))
        column = self.layout().itemAt(1)
        column = column.layout() if column is not None else None
        if column is not None:
            column.addWidget(self._warning)
        self._elide()

    def _elide(self) -> None:
        super()._elide()
        label = getattr(self, "_warning", None)
        if label is None:
            return
        width = self._title.width()
        if width < 30 or self._width_hint:
            width = self.text_width()
        label.setText(label.fontMetrics().elidedText(
            self._full_warning, Qt.TextElideMode.ElideRight, max(40, width)))

    def paintEvent(self, event):  # noqa: N802 - Qt override
        super().paintEvent(event)
        _paint_spine(self, self._spine_up, self._spine_down)


class _DiscardRow(_Row):
    """The last row: red title, and the full sentence as its tooltip when the panel is too narrow to show it whole."""


    def __init__(self, title: str, note: str, parent=None):



        super().__init__("trash", ERROR_TEXT, title, note, "", parent)
        self.setToolTip(f"{title}\n{note}")

    def _elide(self) -> None:
        width = max(40, (self._width_hint or self.width()) - 2 * _ROW_PAD_X - _TILE - 8)
        self._title.setText(self._title.fontMetrics().elidedText(
            self._full_title, Qt.TextElideMode.ElideRight, width))
        self._note.setText(self._note.fontMetrics().elidedText(
            self._full_note, Qt.TextElideMode.ElideRight, width))


class CheckpointSheet(QFrame):
    """The sheet. ``set_entries`` fills it; the signals carry the choice."""

    restore_requested = pyqtSignal(str)
    discard_all_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("checkpointSheet")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_SHEET_QSS)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._rows: list[_Row] = []
        self._focus = -1





        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(6, 6, 6, 6)
        self._outer.setSpacing(2)
        self._head = self._build_head()
        self._outer.addWidget(self._head)
        self._head_rule = QFrame(self)
        self._head_rule.setObjectName("checkpointRule")
        self._head_rule.setFrameShape(QFrame.Shape.NoFrame)
        self._outer.addWidget(self._head_rule)
        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("checkpointScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)


        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._host = QWidget(self._scroll)
        self._col = QVBoxLayout(self._host)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(2)
        self._scroll.setWidget(self._host)
        self._outer.addWidget(self._scroll, 1)
        self._rule = QFrame(self)
        self._rule.setObjectName("checkpointRule")
        self._rule.setFrameShape(QFrame.Shape.NoFrame)
        self._outer.addWidget(self._rule)
        self._discard: _DiscardRow | None = None




        self._entries: list = []
        self._stale = True
        self._rebuild()

    def _build_head(self) -> QWidget:
        """What this sheet is, and the fact that makes it safe to try."""
        head = QWidget(self)
        column = QVBoxLayout(head)
        column.setContentsMargins(_ROW_PAD_X, _ROW_PAD_Y + 2, _ROW_PAD_X, 2)
        column.setSpacing(2)
        title = QLabel(self.tr("Go back in this chat"), head)
        title.setObjectName("checkpointHeading")
        column.addWidget(title)
        promise = QLabel(self.tr("Nothing is deleted. You can always come back."), head)
        promise.setObjectName("checkpointPromise")
        promise.setWordWrap(True)
        column.addWidget(promise)
        return head



    def set_entries(self, entries: list) -> None:
        """``entries`` oldest first, as the controller describes them; the sheet shows them newest first."""

        self._entries = list(entries or [])
        self._stale = True
        if self.isVisible():
            self._rebuild()

    def rows(self) -> list:
        """The rows as they would open, for the keyboard and the tests."""
        if self._stale:
            self._rebuild()
        return self._rows

    def _relayout(self) -> None:
        """Fit every line to the width the sheet actually has, then measure."""










        margins = self._outer.contentsMargins()
        hint = max(_MIN_WIDTH, self.width() - margins.left() - margins.right() - _SCROLL_GUTTER)
        for widget in [self._col.itemAt(i).widget() for i in range(self._col.count())] + [self._discard]:
            if widget is None:
                continue



            widget.ensurePolished()
            for child in widget.findChildren(QLabel):
                child.ensurePolished()
            if hasattr(widget, "set_width_hint"):


                widget.set_width_hint(hint if widget is not self._discard
                                      else hint + _SCROLL_GUTTER)
            if hasattr(widget, "_elide"):
                widget._elide()
                widget.updateGeometry()
        self._col.activate()
        self._outer.activate()

    def _rebuild(self) -> None:
        entries = self._entries
        self._stale = False
        while self._col.count():
            item = self._col.takeAt(0)
            widget = item.widget()
            if widget is not None:


                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._rows = []
        self._focus = -1
        shown = [e for e in reversed(entries or []) if isinstance(e, dict)][:_MAX_ROWS]
        group = object()
        for position, entry in enumerate(shown):
            key = (entry.get("kind") == KIND_EDITS, _whole(entry.get("run_index")),
                   str(entry.get("run_id") or ""))
            if key != group:
                group = key
                self._col.addWidget(_RunCaption(group_caption(entry, self.tr),
                                                group_prompt(entry, self.tr),
                                                spine=position > 0, parent=self._host))
            self._col.addWidget(self._row_for(entry, first=position == 0,
                                              last=position == len(shown) - 1))
        self._col.addStretch(1)
        self._rule.setVisible(bool(shown))
        if self._discard is not None:
            self._outer.removeWidget(self._discard)
            self._discard.hide()
            self._discard.setParent(None)
            self._discard.deleteLater()




        start = next((e for e in entries if isinstance(e, dict) and e.get("start")), None)
        whole = bool(start is not None and start.get("available", True))
        discard = _DiscardRow(self.tr("Discard everything from this chat"),
                              self.tr("Back to the project as it was before the first run") if whole
                              else self.tr("Back to the oldest state still kept"), self)
        discard.setEnabled(any(e.get("available", True) for e in shown))
        discard.clicked.connect(lambda: self._choose(self.discard_all_requested))
        self._outer.addWidget(discard)
        self._discard = discard
        self._rows.append(discard)

    def _row_for(self, entry: dict, first: bool, last: bool) -> _CheckpointRow:
        current = bool(entry.get("current"))
        available = bool(entry.get("available", True))
        warning = checkpoint_warning(entry, self.tr)




        if current:
            glyph = "check"
        elif not available:
            glyph = "dash"
        else:
            glyph = "warning" if warning else "circle"
        row = _CheckpointRow(glyph, _INK_GREY, checkpoint_title(entry, self.tr),
                             checkpoint_note(entry, self.tr), checkpoint_tag(entry, self.tr),
                             warning, self._host, here=current,
                             spine_up=not first, spine_down=not last)
        if warning:
            row.setToolTip(checkpoint_tooltip(entry, self.tr))
        row.setEnabled(available)


        if available and not current:
            cid = str(entry.get("id") or "")
            row.clicked.connect(lambda c=cid: self._choose(self.restore_requested, c))
        self._rows.append(row)
        return row

    def _choose(self, signal, *args) -> None:
        self.hide()
        signal.emit(*args)



    def show_under(self, anchor: QWidget, panel: QWidget | None = None) -> None:
        """Open under ``anchor``, left edges aligned, kept inside ``panel``."""
        panel = panel or anchor.window()
        width = SHEET_WIDTH
        if panel is not None:
            width = max(_MIN_WIDTH, min(SHEET_WIDTH, panel.width() - 8))
        if self._stale:
            self._rebuild()
        self._set_focus(-1)
        self.setFixedWidth(width)
        self.ensurePolished()
        self._relayout()
        room = self._room_for(anchor)
        self.setFixedHeight(min(self._natural_height(), room))
        place_below(self, anchor, panel, width)
        self.show()




        self._relayout()
        settled = min(self._natural_height(), room)
        if settled != self.height():
            self.setFixedHeight(settled)
            place_below(self, anchor, panel, width)
        self._show_bar()
        self.setFocus(Qt.FocusReason.PopupFocusReason)

    def _show_bar(self) -> None:
        """The scrollbar is shown whenever the list runs past the sheet."""








        self._outer.activate()
        listed = self._host.sizeHint().height()
        viewport = self._scroll.viewport().height()
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn if listed > viewport
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def _natural_height(self) -> int:
        """The height the sheet would take with nothing scrolled."""







        height = self._outer.sizeHint().height()
        if height <= 0:
            margins = self._outer.contentsMargins()
            return margins.top() + margins.bottom() + self._col.sizeHint().height()





        listed = self._host.sizeHint().height()
        if listed <= 0:
            listed = self._col.sizeHint().height()
        return height + max(0, listed + 2 - self._scroll.sizeHint().height())

    def _room_for(self, anchor: QWidget) -> int:
        """What the screen leaves for the sheet on the better side of ``anchor``: ``place_below`` opens below when it fits and above when it does not."""


        top_left = anchor.mapToGlobal(QPoint(0, 0))
        screen = QApplication.screenAt(top_left) if hasattr(QApplication, "screenAt") else None
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return 1 << 20
        area = screen.availableGeometry()
        below = area.bottom() - (top_left.y() + anchor.height() + 6) - 8
        above = (top_left.y() - 6) - area.top() - 8
        return max(_MIN_HEIGHT, below, above)

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        for row in self._rows:
            row._elide()

    def showEvent(self, event):  # noqa: N802 - Qt override
        super().showEvent(event)
        if self._stale:
            self._rebuild()
        self._relayout()



    def _set_focus(self, index: int) -> None:
        for i, row in enumerate(self._rows):
            row.set_focused(i == index)
        self._focus = index


        if 0 <= index < len(self._rows):
            row = self._rows[index]
            if row is not self._discard:
                self._scroll.ensureWidgetVisible(row, 0, 4)

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.hide()
            return
        enabled = [i for i, row in enumerate(self._rows)
                   if row.isEnabled() and getattr(row, "restorable", True)]
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and enabled:
            step = 1 if key == Qt.Key.Key_Down else -1
            at = enabled.index(self._focus) if self._focus in enabled else -1
            self._set_focus(enabled[(at + step) % len(enabled)])
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._focus in enabled:
            self._rows[self._focus].clicked.emit()
            return
        super().keyPressEvent(event)
