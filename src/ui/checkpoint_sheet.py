# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later































from __future__ import annotations

import os

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
    f"QLabel#checkpointPromise {{ font-size: {FONT_HINT}px; color: {INK_2};"
    " background: transparent; }"
    f"QLabel#versionTitle {{ font-size: {FONT_BODY}px; color: {INK}; background: transparent; }}"

    f'QLabel#versionTitle[undone="true"] {{ color: {INK_2}; }}'
    f"QLabel#versionTitle:disabled {{ color: {INK_3}; }}"
    f"QLabel#versionWhen {{ font-size: {FONT_MICRO}px; color: {INK_3}; background: transparent; }}"


    f"QLabel#versionVerb {{ font-size: {FONT_HINT}px; font-weight: 600; color: {INK_2};"
    " background: transparent; }"
    f'QLabel#versionVerb[note="true"] {{ font-weight: 400; color: {INK_3}; }}'
    f"QLabel#versionVerb:disabled {{ color: {INK_3}; }}"
    f"QLabel#versionWarning {{ font-size: {FONT_HINT}px; color: {INK}; background: transparent; }}"
    f"QLabel#versionNow {{ font-size: {FONT_HINT}px; font-weight: 600; color: {INK};"
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


def _change_text(row: dict, tr) -> str:

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


def warning_line(items: list, tr) -> str:


    if not items:
        return ""
    if len(items) == 1:
        item = items[0]
        reason = short_reason(str(item.get("reason") or ""), tr)
        names = (tr("{layer} ({reason})").format(layer=str(item.get("name")), reason=reason)
                 if reason else str(item.get("name")))
        return tr("1 layer won't come back: {names}").format(names=names)
    names = ", ".join(str(item.get("name")) for item in items)
    return tr("{n} layers won't come back: {names}").format(n=len(items), names=names)


def warning_tooltip(items: list, tr) -> str:

    if not items:
        return ""
    lines = [tr("Going back brings the project and every backed-up layer back. "
                "These layers keep the data they have now:")]
    for item in items:
        lines.append(tr("• {layer}: {reason}").format(
            layer=str(item.get("name")), reason=_long_reason(str(item.get("reason") or ""), tr)))
    return "\n".join(lines)


def _details(row: dict, tr) -> str:

    if row["type"] == "edits":
        lines = [tr("Changes made in QGIS, outside the agent.")]
    elif row["live"] is None:
        lines = [tr("Restore puts the project back as this request left it.")]
    elif row["live"]:
        lines = [tr("Undo puts the project back as it was before this request.")]
    else:
        lines = [tr("Put back brings the project to where this request left it.")]
    if row.get("request"):
        lines.append(f"“{row['request']}”")
    source = row.get("after") or row.get("before") or {}
    changes = list(dict.fromkeys(_change_text(item, tr) for item in source.get("log") or []
                                 if isinstance(item, dict)))
    if changes:
        lines.append(tr("Changed: {changes}").format(changes=", ".join(changes[:6])))
    return "\n".join(lines)





class _VersionRow(QWidget):



    clicked = pyqtSignal()

    def __init__(self, title: str, when: str = "", verb: str = "", warning: str = "",
                 parent=None, undone: bool = False, note: bool = False):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._hover = False
        self._focused = False


        self._width_hint = 0
        self._full_title = str(title or "")
        self._full_warning = str(warning or "")
        self.restorable = True
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
        self._verb = QLabel(str(verb or ""), self)
        self._verb.setObjectName("versionVerb")
        self._verb.setProperty("note", bool(note))
        self._verb.setVisible(bool(verb))
        line.addWidget(self._verb, 0, Qt.AlignmentFlag.AlignVCenter)
        column.addLayout(line)
        self._warn_row = QWidget(self)
        warn = QHBoxLayout(self._warn_row)
        warn.setContentsMargins(0, 0, 0, 0)
        warn.setSpacing(4)
        self._warn_glyph = QLabel(self._warn_row)
        self._warn_glyph.setFixedSize(_WARN_GLYPH, _WARN_GLYPH)
        self._warn_glyph.setPixmap(icon_for(self, "warning", _WARN_GLYPH, QColor(INK_2)).pixmap(
            _WARN_GLYPH, _WARN_GLYPH))
        warn.addWidget(self._warn_glyph, 0, Qt.AlignmentFlag.AlignVCenter)
        self._warning = QLabel(self._warn_row)
        self._warning.setObjectName("versionWarning")
        self._warning.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        warn.addWidget(self._warning, 1)
        self._warn_row.setVisible(bool(self._full_warning))
        column.addWidget(self._warn_row)
        self._elide()

    def verb(self) -> str:
        return self._verb.text() if not self._verb.isHidden() else ""

    def title(self) -> str:
        return self._full_title

    def warning(self) -> str:
        return self._full_warning

    def set_focused(self, focused: bool) -> None:
        self._focused = bool(focused)
        self.update()

    def set_width_hint(self, width: int) -> None:
        self._width_hint = max(0, int(width or 0))

    def _elide(self) -> None:
        base = self._width_hint or self.width()
        side = sum(label.sizeHint().width() + 8 for label in (self._when, self._verb) if not label.isHidden())
        width = max(40, base - 2 * _ROW_PAD_X - side)
        self._title.setText(self._title.fontMetrics().elidedText(
            self._full_title, Qt.TextElideMode.ElideRight, width))
        if self._full_warning:
            room = max(40, base - 2 * _ROW_PAD_X - _WARN_GLYPH - 4)
            self._warning.setText(self._warning.fontMetrics().elidedText(
                self._full_warning, Qt.TextElideMode.ElideRight, room))

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def enterEvent(self, event):  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._hover = False
        self.update()
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
        label = QLabel(self.tr("Now"), self)
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
        self._running_request = ""

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
        for name, text in (("checkpointHeading", self.tr("Versions of this project")),
                           ("checkpointPromise", self.tr("Going back never deletes anything."))):
            label = QLabel(text, head)
            label.setObjectName(name)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            label.setProperty("fullText", text)
            label.setToolTip(text)
            column.addWidget(label)
        return head



    def set_entries(self, entries: list) -> None:

        self._entries = list(entries or [])
        self._stale = True
        if self.isVisible():
            self._rebuild()

    def set_running(self, running: bool, request: str = "") -> None:

        self._running = bool(running)
        self._running_request = _one_line(request) if running else ""
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
        if self._running:
            request = self._running_request or next(
                (_one_line(e.get("prompt")) for e in reversed(self._entries)
                 if isinstance(e, dict) and e.get("current") and e.get("kind") == KIND_BEFORE), "")
            title = (self.tr("Stop and go back to before “{request}”").format(request=request) if request
                     else self.tr("Stop and go back to before this request"))
            stop = _VersionRow(title, parent=self._host)
            stop.setToolTip(self.tr("Stops the run, then puts the project back as it was before this request."))
            stop.clicked.connect(lambda: self._choose(self.stop_and_restore_requested))
            self._col.addWidget(stop)
            self._rows.append(stop)


        undone = any(row["live"] is False for row in rows)
        now_placed = False
        for row in rows:
            if undone and row["live"] and not now_placed:
                self._col.addWidget(_NowLine(self._host))
                now_placed = True
            self._col.addWidget(self._row_for(row))
        if undone and not now_placed:
            self._col.addWidget(_NowLine(self._host))
        self._col.addStretch(1)
        self._rule.setVisible(bool(rows) or self._running)



        entries = [e for e in self._entries if isinstance(e, dict)]
        first = entries[0] if entries else None
        whole = bool(first is not None and first.get("available", True))
        everything = _VersionRow(self.tr("Undo everything in this chat"), parent=self)
        everything.setToolTip(self.tr("Back to the project as it was before the first request") if whole
                              else self.tr("Back to the oldest version still kept"))
        everything.setEnabled(not self._running and any(usable(e) for e in entries))
        everything.clicked.connect(lambda: self._choose(self.discard_all_requested))
        self._outer.addWidget(everything)
        self._everything = everything
        self._rows.append(everything)

    def _row_for(self, row: dict) -> _VersionRow:
        target = row.get("target")
        cid = row_target(row)
        if row["type"] == "edits":
            title, verb = self.tr("Your own changes"), self.tr("Restore")
        else:
            title = row["request"] or self.tr("Request {n}").format(
                n=_whole((row.get("after") or {}).get("run_index")))
            verb = (self.tr("Restore") if row["live"] is None
                    else self.tr("Undo") if row["live"] else self.tr("Put back"))
        note = False
        if isinstance(target, dict) and target.get("other_project"):
            name = str(target.get("project") or "")
            verb = self.tr("In {project}").format(project=name) if name else self.tr("In a closed project")
            note = True
        elif not cid:
            verb, note = self.tr("No longer kept"), True
        items = not_restored_items(target)
        widget = _VersionRow(title, _when(row.get("created_at")), verb, warning_line(items, self.tr),
                             self._host, undone=row["type"] == "request" and row["live"] is False, note=note)
        tip = _details(row, self.tr)
        if items:
            tip += "\n\n" + warning_tooltip(items, self.tr)
        widget.setToolTip(tip)

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
