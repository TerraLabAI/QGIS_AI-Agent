# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The restore sheet: every state of this chat, one line each, newest first."""


































from __future__ import annotations

import os

from qgis.PyQt.QtCore import QDateTime, QPoint, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QPainter, QPen
from qgis.PyQt.QtWidgets import (
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
    NOT_BACKED_MEMORY_LOST,
    NOT_BACKED_NEW_FILE,
    NOT_BACKED_NO_BACKUP,
    NOT_BACKED_NOT_A_FILE,
    NOT_BACKED_OLD_PROJECT_FILE,
    NOT_BACKED_OTHER_PROJECT,
    NOT_BACKED_TOO_LARGE,
)
from .attach_menu import place_below
from .font_scale import scale_qss_font_px
from .history_popup import relative_time
from .popover_rows import _POPOVER_QSS, _ROW_PAD_X, _ROW_PAD_Y, _TILE, _Row
from .shared import paint_styled_ground, round_popup_corners, screen_area_at
from .style import (
    FONT_BODY,
    FONT_HINT,
    FONT_MICRO,
    HAIRLINE,
    INK,
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

    "QFrame#checkpointSheet QLabel#exampleTitle { font-weight: 400; }"

    f"QLabel#checkpointHeading {{ font-size: {FONT_BODY}px; font-weight: 600; color: {INK};"
    " background: transparent; }"
    f"QLabel#checkpointPromise {{ font-size: {FONT_HINT}px; color: {INK_3};"
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


def _change_text(row: dict, tr) -> str:
    """One change of a run's log, in the fewest words that say it."""
    name = str(row.get("layer") or os.path.basename(str(row.get("file") or "")) or "?")
    what = row.get("what")
    if what == "added":
        return tr("{name} added").format(name=name)
    if what == "removed":
        return tr("{name} removed").format(name=name)
    if what == "features":
        return tr("{name}: {before} to {after} features").format(
            name=name, before=_whole(row.get("before")), after=_whole(row.get("after")))
    if what == "fields":
        return tr("{name}: {fields}").format(name=name, fields=", ".join(str(f) for f in row.get("fields") or []))
    if what == "file":
        return tr("{name} written").format(name=name)
    return tr("{name} changed").format(name=name)


_SHORT_REQUEST = 48


def _request(entry: dict, limit: int = 0) -> str:
    """What the entry's run was asked, on one line, cut to ``limit`` characters when given."""
    text = _one_line(entry.get("prompt"))
    if limit and len(text) > limit:
        text = text[:limit].rstrip() + "\u2026"
    return text


def _is_start(entry: dict) -> bool:
    return entry.get("kind") not in (KIND_AFTER, KIND_EDITS) and (
        bool(entry.get("start")) or _whole(entry.get("run_index")) <= 1)


def visible_entries(entries) -> list[dict]:
    """The states worth a row, oldest first: a before entry that holds the same state as the after entry just before it is folded into that row."""


    shown: list[dict] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("same_as_previous") and shown:
            if entry.get("current"):
                shown[-1] = {**shown[-1], "current": True}
            continue
        shown.append(entry)
    return shown


def checkpoint_title(entry: dict, tr) -> str:
    """The row's one line: the state named by what produced it, in the user's words."""
    kind = entry.get("kind")
    if kind == KIND_EDITS:
        return tr("Your own changes")
    asked = _request(entry)
    if kind == KIND_AFTER:
        return asked or tr("Request {n}").format(n=_whole(entry.get("run_index")))
    if _is_start(entry):
        return tr("Start of this chat")
    return tr("Before: {request}").format(request=asked or tr("request {n}").format(n=_whole(entry.get("run_index"))))


def point_name(entry: dict, tr) -> str:
    """The state as a sentence names it: "before “Remove the roads layer”"."""
    kind = entry.get("kind")
    if kind == KIND_EDITS:
        return tr("your own changes")
    asked = _request(entry, _SHORT_REQUEST)
    if kind == KIND_AFTER:
        return tr("after “{request}”").format(request=asked) if asked else tr("after request {n}").format(
            n=_whole(entry.get("run_index")))
    if _is_start(entry):
        return tr("the start of this chat")
    return tr("before “{request}”").format(request=asked) if asked else tr("before request {n}").format(
        n=_whole(entry.get("run_index")))


def checkpoint_note(entry: dict, tr) -> str:
    """A second line only when the row cannot be restored here, saying why."""
    if not entry.get("available", True):
        return tr("No longer available")
    if entry.get("other_project"):
        name = str(entry.get("project") or "")
        return tr("Belongs to {name}").format(name=name) if name else tr("Belongs to a closed project")
    return ""


def checkpoint_details(entry: dict, tr) -> str:
    """The tooltip of a row: when, the whole request, and what it changed."""
    lines = []
    when = _when(entry)
    kind = entry.get("kind")
    if kind == KIND_EDITS:
        head = tr("Made in QGIS, outside the agent")
    elif kind == KIND_AFTER:
        head = tr("The project after this request")
    elif _is_start(entry):
        head = tr("The project before the first request")
    else:
        head = tr("The project before this request")
    lines.append(f"{head} · {when}" if when else head)
    asked = _request(entry)
    if asked and kind != KIND_EDITS:
        lines.append(f"“{asked}”")
    if kind == KIND_AFTER:
        changes = list(dict.fromkeys(_change_text(row, tr) for row in entry.get("log") or []
                                     if isinstance(row, dict)))
        if changes:
            lines.append(tr("Changed: {changes}").format(changes=", ".join(changes[:6])))
    return "\n".join(lines)


def checkpoint_tag(entry: dict, tr) -> str:
    """The verb on the right: what a click on this row does, in one word."""
    if entry.get("other_project"):
        return ""
    if entry.get("current"):
        return tr("Now")
    if not entry.get("available", True):
        return ""
    return tr("Restore")


def not_restored_items(entry: dict) -> list:
    """The layers a restore of ``entry`` cannot bring back, as the core describes them: ``[{"name", "reason"}]``."""

    if not entry.get("available", True) or entry.get("other_project"):
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
    if reason == NOT_BACKED_MEMORY_LOST:
        return tr("emptied by a restart")
    if reason == NOT_BACKED_NO_BACKUP:
        return tr("changed without a backup")
    if reason == NOT_BACKED_NEW_FILE:
        return tr("new file")
    if reason == NOT_BACKED_OTHER_PROJECT:
        return tr("another project")
    if reason == NOT_BACKED_OLD_PROJECT_FILE:
        return tr("earlier project file")
    return ""


def _long_reason(reason: str, tr) -> str:
    """The whole sentence, for the tooltip, where there is room for it."""
    if reason == NOT_BACKED_TOO_LARGE:
        return tr("its file was over the backup limit")
    if reason == NOT_BACKED_NOT_A_FILE:
        return tr("it lives in a database or a service, not in a file")
    if reason == NOT_BACKED_COPY_FAILED:
        return tr("its backup could not be written")
    if reason == NOT_BACKED_MEMORY_LOST:
        return tr("it was a temporary layer held in memory, and QGIS has restarted since")
    if reason == NOT_BACKED_NO_BACKUP:
        return tr("it was changed in place and no copy was made first")
    if reason == NOT_BACKED_NEW_FILE:
        return tr("the run wrote this file, and a restore deletes no file")
    if reason == NOT_BACKED_OTHER_PROJECT:
        return tr("it was changed while another project was open")
    if reason == NOT_BACKED_OLD_PROJECT_FILE:
        return tr("the project is saved under another file now, and a restore writes only that one")
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
    """The last row: one line under a red glyph, what it reaches in its tooltip."""

    def __init__(self, title: str, note: str, parent=None):



        super().__init__("trash", ERROR_TEXT, title, "", "", parent)
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
        round_popup_corners(self)
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
        shown = list(reversed(visible_entries(entries)))[:_MAX_ROWS]
        for position, entry in enumerate(shown):
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
                              self.tr("Back to the project as it was before the first request") if whole
                              else self.tr("Back to the oldest state still kept"), self)
        discard.setEnabled(any(e.get("available", True) and not e.get("other_project") for e in shown))
        discard.clicked.connect(lambda: self._choose(self.discard_all_requested))
        self._outer.addWidget(discard)
        self._discard = discard
        self._rows.append(discard)

    def _row_for(self, entry: dict, first: bool, last: bool) -> _CheckpointRow:
        other = bool(entry.get("other_project"))
        current = bool(entry.get("current")) and not other
        available = bool(entry.get("available", True)) and not other
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
        tip = checkpoint_details(entry, self.tr)
        if warning:
            tip = tip + "\n\n" + checkpoint_tooltip(entry, self.tr)
        row.setToolTip(tip)
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


        return max(_MIN_HEIGHT, height + listed + 2 - self._scroll.sizeHint().height())

    def _room_for(self, anchor: QWidget) -> int:
        """What the screen leaves for the sheet on the better side of ``anchor``: ``place_below`` opens below when it fits and above when it does not."""


        top_left = anchor.mapToGlobal(QPoint(0, 0))
        area = screen_area_at(top_left, anchor)
        if area is None:
            return 1 << 20
        below = area.bottom() - (top_left.y() + anchor.height() + 6) - 8
        above = (top_left.y() - 6) - area.top() - 8
        return max(_MIN_HEIGHT, below, above)

    def paintEvent(self, event):  # noqa: N802 - Qt override
        paint_styled_ground(self)
        super().paintEvent(event)

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
