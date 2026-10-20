# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


























from __future__ import annotations

from qgis.PyQt.QtCore import QDateTime, QPoint, QRectF, Qt, pyqtSignal
from qgis.PyQt.QtGui import QBrush, QColor, QPainter
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.checkpoints import (
    KIND_AFTER,
    KIND_BEFORE,
    KIND_EDITS,
    NOT_BACKED_COPY_FAILED,
    NOT_BACKED_MEMORY_LOST,
    NOT_BACKED_NEW_FILE,
    NOT_BACKED_NO_BACKUP,
    NOT_BACKED_NOT_A_FILE,
    NOT_BACKED_OLD_PROJECT_FILE,
    NOT_BACKED_OTHER_PROJECT,
    NOT_BACKED_TOO_LARGE,
    NOT_PUT_BACK,
)
from .attach_menu import place_below
from .font_scale import scale_qss_font_px
from .history_popup import relative_time
from .icons import icon_for
from .popover_rows import _POPOVER_QSS, _ROW_PAD_X, _ROW_PAD_Y
from .shared import event_pos, paint_styled_ground, round_popup_corners, screen_area_at
from .style import (
    FONT_BODY,
    FONT_HINT,
    FONT_MICRO,
    HAIRLINE,
    INK,
    INK_2,
    INK_3,
    LINE_STRONG,
    ORANGE,
    RADIUS_CONTROL,
    RADIUS_PANEL,
    SURFACE,
    hover_pill,
)

SHEET_WIDTH = 360
_MIN_WIDTH = 240


_MAX_ROWS = 200
_MIN_HEIGHT = 120


_SCROLL_GUTTER = 12
_WARN_GLYPH = 12

_SHEET_QSS = _POPOVER_QSS + scale_qss_font_px(
    f"QFrame#checkpointSheet {{ background: {SURFACE};"
    f" border: 1px solid {LINE_STRONG}; border-radius: {RADIUS_PANEL}px; }}"
    f"QLabel#checkpointHeading {{ font-size: {FONT_BODY}px; font-weight: 600; color: {INK};"
    " background: transparent; }"
    f"QLabel#versionTitle {{ font-size: {FONT_BODY}px; color: {INK}; background: transparent; }}"

    f'QLabel#versionTitle[undone="true"] {{ color: {INK_2}; }}'
    f"QLabel#versionTitle:disabled {{ color: {INK_3}; }}"
    f"QLabel#versionWhen {{ font-size: {FONT_MICRO}px; color: {INK_3}; background: transparent; }}"
    f"QLabel#versionSummary {{ font-size: {FONT_HINT}px; color: {INK_3}; background: transparent; }}"
    f"QLabel#versionVerb {{ font-size: {FONT_HINT}px; font-weight: 500; color: {INK_3};"
    " background: transparent; }"
    f'QLabel#versionVerb[hot="true"] {{ color: {INK}; }}'
    f"QLabel#versionCurrent {{ font-size: {FONT_MICRO}px; font-weight: 600; color: {INK};"
    f" background: {HAIRLINE}; border-radius: 4px; padding: 1px 6px; }}"
    f"QLabel#versionNow {{ font-size: {FONT_MICRO}px; font-weight: 600; color: {INK};"
    " background: transparent; }"
    f"QFrame#checkpointRule {{ background: {HAIRLINE}; border: none;"
    " min-height: 1px; max-height: 1px; margin: 4px 8px; }"
    f"QFrame#versionNowRule {{ background: {HAIRLINE}; border: none;"
    " min-height: 1px; max-height: 1px; }"
    "QScrollArea#checkpointScroll { background: transparent; border: none; }"
    "QScrollArea#checkpointScroll > QWidget > QWidget { background: transparent; }"
)


def _whole(value, fallback: int = 0) -> int:

    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return int(fallback)


def _one_line(text: str) -> str:

    return " ".join(str(text or "").split())


def _when(stamp) -> str:

    try:
        seconds = float(stamp or 0.0)
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    moment = QDateTime.fromSecsSinceEpoch(int(seconds))
    return relative_time(moment.toString(Qt.DateFormat.ISODate)) if moment.isValid() else ""





def history_rows(entries) -> list[dict]:















    entries = [e for e in (entries or []) if isinstance(e, dict)]
    now = next((i for i, e in enumerate(entries) if e.get("current")), None)
    known = now is not None
    befores: dict = {}
    for entry in entries:
        run_id = str(entry.get("run_id") or "")
        if entry.get("kind") == KIND_BEFORE and run_id and run_id not in befores:
            befores[run_id] = entry
    rows: list[dict] = []
    for index, entry in enumerate(entries):
        kind = entry.get("kind")
        run_id = str(entry.get("run_id") or "")
        if kind == KIND_AFTER:
            before = befores.get(run_id)
            live = index <= now if known else None
            rows.append({"type": "request", "anchor": index, "live": live,
                         "target": before if live else entry, "before": before, "after": entry,
                         "request": _one_line(entry.get("prompt") or (before or {}).get("prompt")),
                         "created_at": entry.get("created_at"), "run_id": run_id})
            continue
        own = kind == KIND_EDITS or (
            kind == KIND_BEFORE and index > 0 and entries[index - 1].get("kind") == KIND_AFTER
            and not entry.get("same_as_previous"))
        if own and index != now:
            rows.append({"type": "edits", "anchor": index, "live": index < now if known else None,
                         "target": entry,
                         "before": None, "after": None, "request": "",
                         "created_at": entry.get("created_at"), "run_id": run_id})
    rows.sort(key=lambda row: row["anchor"], reverse=True)
    return rows[:_MAX_ROWS]


def usable(entry) -> bool:

    return isinstance(entry, dict) and bool(entry.get("available", True)) and not entry.get("other_project")


def row_target(row: dict) -> str:

    target = row.get("target")
    return str(target.get("id") or "") if usable(target) else ""


def not_restored_items(entry) -> list:

    if not usable(entry):
        return []
    items = entry.get("not_backed_up")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("name")]


def short_reason(reason: str, tr) -> str:

    if reason == NOT_BACKED_TOO_LARGE:
        return tr("too large")
    if reason == NOT_BACKED_NOT_A_FILE:
        return tr("not a file")
    if reason == NOT_BACKED_MEMORY_LOST:
        return tr("temporary layer")
    if reason == NOT_BACKED_NEW_FILE:
        return tr("file the run wrote")
    if reason == NOT_BACKED_OTHER_PROJECT:
        return tr("other project")
    return tr("no backup")


def not_restored_line(entry, tr) -> str:



    return _items_line(not_restored_items(entry), tr)


def _items_line(items: list, tr) -> str:



    lost = losses(items)
    kept = [item for item in items or [] if item not in lost]
    parts = []
    if len(lost) == 1:
        parts.append(tr("{layer} can't be restored ({reason})").format(
            layer=str(lost[0]["name"]), reason=short_reason(str(lost[0].get("reason") or ""), tr)))
    elif lost:
        parts.append(tr("{n} layers can't be restored").format(n=len(lost)))
    if len(kept) == 1:
        parts.append(tr("{file} stays on disk").format(file=str(kept[0]["name"])))
    elif kept:
        parts.append(tr("{n} files the request wrote stay on disk").format(n=len(kept)))
    return " \u00b7 ".join(parts)


def losses(items) -> list:

    return [item for item in items or [] if isinstance(item, dict) and item.get("reason") != NOT_BACKED_NEW_FILE]


def long_reason(reason: str, tr) -> str:

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
    if reason == NOT_PUT_BACK:
        return tr("its file could not be written back (another program may hold it), so it was left as it was")
    return tr("no backup was made")


def change_lines(row: dict, tr) -> list[str]:






    source = row.get("after") or row.get("before") or row.get("target") or {}
    changes = source.get("changes") if isinstance(source, dict) else None
    lines = []
    for item in changes or []:
        if not isinstance(item, dict):
            continue
        what = [str(w) for w in item.get("what") or []]
        name = str(item.get("layer") or "")
        if not name:
            if "crs" in what:
                lines.append(tr("Project CRS changed"))
            if "layouts" in what:
                lines.append(tr("Print layouts changed"))
            continue
        if what == ["added"]:
            lines.append(tr("Added {layer}").format(layer=name))
            continue
        if what == ["removed"]:
            lines.append(tr("Removed {layer}").format(layer=name))
            continue
        parts = []
        if "features" in what:
            delta = _whole(item.get("delta"))
            parts.append(tr("{n} features deleted").format(n=-delta) if delta < 0 else
                         tr("{n} features added").format(n=delta) if delta > 0 else tr("features edited"))
        if "data" in what:
            parts.append(tr("data edited"))
        if "style" in what:
            parts.append(tr("restyled"))
        if "renamed" in what:
            parts.append(tr("renamed"))
        if what == ["changed"]:
            parts.append(tr("changed"))
        if parts:
            lines.append(tr("{layer}: {changes}").format(layer=name, changes=", ".join(parts)))
    return lines


def summary(row: dict, tr) -> str:

    if row["type"] == "edits":
        return tr("Changes made in QGIS")
    lines = change_lines(row, tr)
    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0]
    return tr("{first} and {n} more").format(first=lines[0], n=len(lines) - 1)


def changes_tooltip(row: dict, tr, limit: int = 8) -> str:

    lines = change_lines(row, tr)
    shown = ["\u2022 " + line for line in lines[:limit]]
    if len(lines) > limit:
        shown.append(tr("and {n} more").format(n=len(lines) - limit))
    return "\n".join(shown)





class _VersionRow(QWidget):



    clicked = pyqtSignal()

    def __init__(self, title: str, when: str = "", summary: str = "", parent=None,
                 undone: bool = False, current: bool = False, warn: bool = False, verb: str = ""):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._hover = False
        self._focused = False


        self._width_hint = 0
        self._full_title = str(title or "")
        self._full_summary = str(summary or "")
        self.current = bool(current)
        self.restorable = not current
        self.setCursor(Qt.CursorShape.PointingHandCursor if self.restorable else Qt.CursorShape.ArrowCursor)
        column = QVBoxLayout(self)
        column.setContentsMargins(_ROW_PAD_X, _ROW_PAD_Y + 1, _ROW_PAD_X, _ROW_PAD_Y + 1)
        column.setSpacing(1)
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        self._title = QLabel(self)
        self._title.setObjectName("versionTitle")
        self._title.setProperty("undone", bool(undone))
        self._title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        line.addWidget(self._title, 1)
        self._when = QLabel(str(when or ""), self)
        self._when.setObjectName("versionWhen")
        self._when.setVisible(bool(when))
        line.addWidget(self._when, 0, Qt.AlignmentFlag.AlignVCenter)
        self._verb = QLabel(self.tr("Current") if current else (verb or self.tr("Restore")), self)
        self._verb.setObjectName("versionCurrent" if current else "versionVerb")
        line.addWidget(self._verb, 0, Qt.AlignmentFlag.AlignVCenter)
        column.addLayout(line)
        self._summary_row = QWidget(self)
        under = QHBoxLayout(self._summary_row)
        under.setContentsMargins(0, 0, 0, 0)
        under.setSpacing(4)
        self._warn_glyph = QLabel(self._summary_row)
        self._warn_glyph.setFixedSize(_WARN_GLYPH, _WARN_GLYPH)
        self._warn_glyph.setPixmap(icon_for(self, "warning", _WARN_GLYPH, QColor(ORANGE)).pixmap(
            _WARN_GLYPH, _WARN_GLYPH))
        self._warn_glyph.setVisible(bool(warn))
        under.addWidget(self._warn_glyph, 0, Qt.AlignmentFlag.AlignVCenter)
        self._summary = QLabel(self._summary_row)
        self._summary.setObjectName("versionSummary")
        self._summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        under.addWidget(self._summary, 1)
        self._summary_row.setVisible(bool(self._full_summary) or bool(warn))
        column.addWidget(self._summary_row)
        self._elide()

    def verb(self) -> str:
        return self._verb.text() if not self._verb.isHidden() else ""

    def title(self) -> str:
        return self._full_title

    def summary(self) -> str:
        return self._full_summary

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        super().setEnabled(enabled)

        if not self.current:
            self._verb.setVisible(bool(enabled))

    def set_focused(self, focused: bool) -> None:
        self._focused = bool(focused)
        self._sync_hot()

    def set_width_hint(self, width: int) -> None:
        self._width_hint = max(0, int(width or 0))

    def _sync_hot(self) -> None:
        hot = (self._hover or self._focused) and self.isEnabled() and self.restorable
        if bool(self._verb.property("hot")) != hot:
            self._verb.setProperty("hot", hot)
            self._verb.style().unpolish(self._verb)
            self._verb.style().polish(self._verb)
        self.update()

    def _elide(self) -> None:
        base = self._width_hint or self.width()
        side = sum(label.sizeHint().width() + 8 for label in (self._when, self._verb) if not label.isHidden())
        width = max(40, base - 2 * _ROW_PAD_X - side)
        self._title.setText(self._title.fontMetrics().elidedText(
            self._full_title, Qt.TextElideMode.ElideRight, width))
        if self._full_summary:
            glyph = _WARN_GLYPH + 4 if not self._warn_glyph.isHidden() else 0
            room = max(40, base - 2 * _ROW_PAD_X - glyph)
            self._summary.setText(self._summary.fontMetrics().elidedText(
                self._full_summary, Qt.TextElideMode.ElideRight, room))

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def enterEvent(self, event):  # noqa: N802
        self._hover = True
        self._sync_hot()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._hover = False
        self._sync_hot()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if (event.button() == Qt.MouseButton.LeftButton and self.restorable
                and self.rect().contains(event_pos(event))):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):  # noqa: N802
        if (self._hover or self._focused) and self.isEnabled() and self.restorable:
            painter = QPainter(self)
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(hover_pill()))
                painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                                        RADIUS_CONTROL, RADIUS_CONTROL)
            finally:
                painter.end()
        super().paintEvent(event)


class _NowLine(QWidget):


    def __init__(self, parent=None):
        super().__init__(parent)
        line = QHBoxLayout(self)
        line.setContentsMargins(_ROW_PAD_X, 3, _ROW_PAD_X, 3)
        line.setSpacing(8)
        label = QLabel(self.tr("Current"), self)
        label.setObjectName("versionNow")
        line.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)
        rule = QFrame(self)
        rule.setObjectName("versionNowRule")
        rule.setFrameShape(QFrame.Shape.NoFrame)
        line.addWidget(rule, 1, Qt.AlignmentFlag.AlignVCenter)


class CheckpointSheet(QFrame):


    restore_requested = pyqtSignal(str)
    discard_all_requested = pyqtSignal()

    stop_and_restore_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        round_popup_corners(self)
        self.setObjectName("checkpointSheet")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_SHEET_QSS)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._rows: list[_VersionRow] = []
        self._focus = -1
        self._running = False

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


        self._scroll.setMinimumHeight(1)
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
        self._everything: _VersionRow | None = None


        self._entries: list = []
        self._stale = True
        self._rebuild()

    def _build_head(self) -> QWidget:


        head = QWidget(self)
        column = QVBoxLayout(head)
        column.setContentsMargins(_ROW_PAD_X, _ROW_PAD_Y + 2, _ROW_PAD_X, 2)
        column.setSpacing(2)
        text = self.tr("Map history")
        label = QLabel(text, head)
        label.setObjectName("checkpointHeading")
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        label.setProperty("fullText", text)
        column.addWidget(label)
        return head



    def set_entries(self, entries: list) -> None:

        self._entries = list(entries or [])
        self._stale = True
        if self.isVisible():
            self._rebuild()

    def set_running(self, running: bool) -> None:

        self._running = bool(running)
        self._stale = True
        if self.isVisible():
            self._rebuild()

    def rows(self) -> list:

        if self._stale:
            self._rebuild()
        return self._rows

    def _clear_list(self) -> None:
        while self._col.count():
            item = self._col.takeAt(0)
            widget = item.widget()
            if widget is not None:


                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        if self._everything is not None:
            self._outer.removeWidget(self._everything)
            self._everything.hide()
            self._everything.setParent(None)
            self._everything.deleteLater()
            self._everything = None

    def _rebuild(self) -> None:
        self._stale = False
        self._clear_list()
        self._rows = []
        self._focus = -1
        rows = history_rows(self._entries)
        entries = [e for e in self._entries if isinstance(e, dict)]
        now = next((i for i, e in enumerate(entries) if e.get("current")), None)
        if self._running:
            stop = _VersionRow(self.tr("Stop and undo this request"), parent=self._host, verb=self.tr("Stop"))
            stop.setToolTip(self.tr("Stops the run, then puts the map back"))
            stop.clicked.connect(lambda: self._choose(self.stop_and_restore_requested))
            self._col.addWidget(stop)
            self._rows.append(stop)







        here = now
        while here is not None and here > 0 and entries[here].get("same_as_previous"):
            here -= 1
        owner = next((row for row in rows if here is not None and row["anchor"] in (now, here)), None)
        line_wanted = now is not None and owner is None and not self._start_is_current(entries, now)
        line_placed = False
        for row in rows:
            if line_wanted and not line_placed and row["anchor"] < now:
                self._col.addWidget(_NowLine(self._host))
                line_placed = True
            self._col.addWidget(self._row_for(row, row is owner))
        if line_wanted and not line_placed:
            self._col.addWidget(_NowLine(self._host))
        self._col.addStretch(1)
        self._rule.setVisible(bool(rows) or self._running)



        first = entries[0] if entries else None
        whole = bool(first is not None and first.get("available", True))
        start_current = now is not None and self._start_is_current(entries, now)
        everything = _VersionRow(self.tr("Start of chat"), parent=self, current=start_current)
        everything.setToolTip(self.tr("The map as it was before the first request") if whole
                              else self.tr("The oldest version still kept"))
        everything.setEnabled(start_current or (not self._running and any(usable(e) for e in entries)))
        everything.clicked.connect(lambda: self._choose(self.discard_all_requested))
        self._outer.addWidget(everything)
        self._everything = everything
        self._rows.append(everything)

    @staticmethod
    def _start_is_current(entries: list, now: int) -> bool:


        if now is None or not entries:
            return False
        return all(e.get("kind") == KIND_BEFORE and (i == 0 or e.get("same_as_previous"))
                   for i, e in enumerate(entries[:now + 1]))

    def _row_for(self, row: dict, current: bool = False) -> _VersionRow:


        target = row.get("after") if row["type"] == "request" else row.get("target")
        cid = str(target.get("id") or "") if usable(target) else ""
        if row["type"] == "edits":
            title = self.tr("Your edits")
        else:
            title = row["request"] or self.tr("Request {n}").format(
                n=_whole((row.get("after") or {}).get("run_index")))


        items = not_restored_items(target) or (
            not_restored_items(row.get("before")) if row["type"] == "request" else [])
        changed = summary(row, self.tr)
        warning = _items_line(items, self.tr)
        widget = _VersionRow(title, _when(row.get("created_at")), warning or changed, self._host,
                             undone=row["type"] == "request" and row["live"] is False,
                             current=current, warn=bool(losses(items)))
        reason = ""
        if isinstance(target, dict) and target.get("other_project"):
            name = str(target.get("project") or "")
            reason = (self.tr("Belongs to the project {project}").format(project=name) if name
                      else self.tr("Belongs to a closed project"))
        elif not cid and not current:
            reason = self.tr("No longer kept")

        details = "\n".join(part for part in (
            row["request"] if row["type"] == "request" else "",
            changes_tooltip(row, self.tr) if row["type"] == "request" else "",
            warning) if part)
        widget.setToolTip(reason or details)
        if current:
            return widget

        widget.setEnabled(bool(cid) and not self._running)
        if cid:
            widget.clicked.connect(lambda c=cid: self._choose(self.restore_requested, c))
        self._rows.append(widget)
        return widget

    def _choose(self, signal, *args) -> None:
        self.hide()
        signal.emit(*args)



    def _relayout(self) -> None:

        margins = self._outer.contentsMargins()
        hint = max(_MIN_WIDTH, self.width() - margins.left() - margins.right() - _SCROLL_GUTTER)
        for label in self._head.findChildren(QLabel):
            label.ensurePolished()
            full = str(label.property("fullText") or "")
            label.setText(label.fontMetrics().elidedText(
                full, Qt.TextElideMode.ElideRight, hint + _SCROLL_GUTTER - 2 * _ROW_PAD_X))
        widgets = [self._col.itemAt(i).widget() for i in range(self._col.count())] + [self._everything]
        for widget in widgets:
            if widget is None:
                continue


            widget.ensurePolished()
            for child in widget.findChildren(QLabel):
                child.ensurePolished()
            if isinstance(widget, _VersionRow):


                widget.set_width_hint(hint if widget is not self._everything else hint + _SCROLL_GUTTER)
                widget._elide()
                widget.updateGeometry()
        self._col.activate()
        self._outer.activate()

    def show_under(self, anchor: QWidget, panel: QWidget | None = None) -> None:

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
            self._outer.activate()



        short = self._host.sizeHint().height() - self._scroll.viewport().height()
        if short > 0 and self.height() + short <= room:
            self.setFixedHeight(self.height() + short)
            self._outer.activate()
        place_below(self, anchor, panel, width)
        self._show_bar()
        self.setFocus(Qt.FocusReason.PopupFocusReason)

    def _show_bar(self) -> None:


        self._outer.activate()
        listed = self._host.sizeHint().height()
        viewport = self._scroll.viewport().height()
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn if listed > viewport
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def _natural_height(self) -> int:












        margins = self._outer.contentsMargins()
        items = [self._outer.itemAt(i) for i in range(self._outer.count())]
        items = [item for item in items if item.widget() is None or not item.widget().isHidden()]
        fixed = sum(max(0, item.sizeHint().height()) for item in items if item.widget() is not self._scroll)
        listed = self._host.sizeHint().height()
        if listed <= 0:
            listed = self._col.sizeHint().height()
        spacing = self._outer.spacing() * max(0, len(items) - 1)
        return max(_MIN_HEIGHT, margins.top() + margins.bottom() + fixed + max(0, listed) + spacing)

    def _room_for(self, anchor: QWidget) -> int:

        top_left = anchor.mapToGlobal(QPoint(0, 0))
        area = screen_area_at(top_left, anchor)
        if area is None:
            return 1 << 20
        below = area.bottom() - (top_left.y() + anchor.height() + 6) - 8
        above = (top_left.y() - 6) - area.top() - 8
        return max(_MIN_HEIGHT, below, above)

    def paintEvent(self, event):  # noqa: N802
        paint_styled_ground(self)
        super().paintEvent(event)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        for row in self._rows:
            row._elide()

    def showEvent(self, event):  # noqa: N802
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
            if row is not self._everything:
                self._scroll.ensureWidgetVisible(row, 0, 4)

    def keyPressEvent(self, event):  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.hide()
            return
        enabled = [i for i, row in enumerate(self._rows) if row.isEnabled() and row.restorable]
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and enabled:
            step = 1 if key == Qt.Key.Key_Down else -1
            at = enabled.index(self._focus) if self._focus in enabled else -1
            self._set_focus(enabled[(at + step) % len(enabled)])
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._focus in enabled:
            self._rows[self._focus].clicked.emit()
            return
        super().keyPressEvent(event)
