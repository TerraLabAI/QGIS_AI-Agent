# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later














from __future__ import annotations

import itertools
import uuid

from qgis.PyQt.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QFontMetrics
from qgis.PyQt.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QVBoxLayout

from ..core.layer_mime import live_layer_chips
from .bubbles import UserBubble
from .icons import pixmap_for
from .layer_icons import layer_name
from .style import (
    CANVAS,
    FONT_BODY,
    FONT_HINT,
    HOVER_ON,
    INK,
    INK_2,
    INK_3,
    LINE,
    RADIUS_COMPOSER,
    RADIUS_CONTROL,
    qcolor,
    repolish,
)
from .widgets import IconButton

QUEUE_MAX = 5

_FOLDED = 3
_MOTION_MS = 150
_QWIDGETSIZE_MAX = 16777215
_ids = itertools.count(1)

TRAY_TUCK = 10

TRAY_INSET = 6



_TRAY_QSS = (
    f"QFrame#queueTray {{ background: {CANVAS}; border: 1px solid {LINE}; border-bottom: none;"
    f" border-top-left-radius: {RADIUS_COMPOSER}px; border-top-right-radius: {RADIUS_COMPOSER}px; }}"
    f"QFrame#queueRow {{ background: transparent; border: none; border-radius: {RADIUS_CONTROL}px; }}"
    f'QFrame#queueRow[hover="true"] {{ background: {HOVER_ON}; }}'
    f"QLabel#queueText {{ font-size: {FONT_BODY}px; color: {INK}; background: transparent; border: none; }}"
    f"QLabel#queueHeader, QLabel#queueExtra {{ font-size: {FONT_HINT}px; color: {INK_3};"
    " background: transparent; border: none; }"
    f"QLabel#queueMore {{ font-size: {FONT_HINT}px; color: {INK_2}; background: transparent; border: none; }}"
    f"QLabel#queueMore:hover {{ color: {INK}; }}"
)


def new_item(text: str, chips=(), attachments=()) -> dict:

    return {"id": next(_ids), "text": str(text or ""), "chips": list(chips or []),
            "attachments": list(attachments or [])}


def extras_of(item: dict) -> int:
    return len(item.get("chips") or []) + len(item.get("attachments") or [])


def _extras_tip(item: dict) -> str:
    names = []
    for extra in list(item.get("chips") or []) + list(item.get("attachments") or []):
        if isinstance(extra, dict):
            names.append(str(extra.get("name") or extra.get("label") or extra.get("path") or ""))
    return "\n".join(n for n in names if n)


class _RowText(QLabel):


    clicked = pyqtSignal()

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("queueText")
        self._full = " ".join(str(text or "").split())

        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(self._full)
        self.setAccessibleName(self._full)
        self.setMinimumWidth(40)
        self._elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        self.ensurePolished()
        shown = QFontMetrics(self.font()).elidedText(self._full, Qt.TextElideMode.ElideRight,
                                                     max(40, self.width() - 2))
        if shown != self.text():
            self.setText(shown)

    def mousePressEvent(self, event):  # noqa: N802

        event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()


class _QueueRow(QFrame):



    send_now = pyqtSignal(int)
    remove = pyqtSignal(int)
    edit = pyqtSignal(int)

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.item_id = int(item["id"])
        self.leaving = False
        self.fresh = True
        self.setObjectName("queueRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("hover", False)

        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._pointer = False
        line = QHBoxLayout(self)
        line.setContentsMargins(8, 3, 4, 3)
        line.setSpacing(8)
        glyph = QLabel(self)
        glyph.setPixmap(pixmap_for(glyph, "lu.corner-down-left", 12, qcolor(INK_3)))
        glyph.setFixedSize(14, 18)
        glyph.setStyleSheet("background: transparent; border: none;")
        line.addWidget(glyph, 0, Qt.AlignmentFlag.AlignVCenter)
        self.text = _RowText(item["text"], self)
        self.text.clicked.connect(lambda: self.edit.emit(self.item_id))
        line.addWidget(self.text, 1)
        count = extras_of(item)
        self.extra = QLabel(f"+{count}", self)
        self.extra.setObjectName("queueExtra")
        self.extra.setToolTip(_extras_tip(item))
        self.extra.setVisible(count > 0)
        line.addWidget(self.extra, 0, Qt.AlignmentFlag.AlignVCenter)
        self.pencil = IconButton(self, None, 14, self.tr("Edit this message"))
        self.pencil.setObjectName("queueEdit")
        self.pencil.clicked.connect(lambda: self.edit.emit(self.item_id))
        self.up = IconButton(self, None, 14, self.tr("Send this message now"))
        self.up.setObjectName("queueSendNow")
        self.up.clicked.connect(lambda: self.send_now.emit(self.item_id))
        self.gone = IconButton(self, None, 14, self.tr("Remove from the queue"))
        self.gone.setObjectName("queueRemove")
        self.gone.clicked.connect(lambda: self.remove.emit(self.item_id))
        self._arrow_shown = True
        for button in (self.pencil, self.up, self.gone):
            button.setFixedSize(22, 22)

            policy = button.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            button.setSizePolicy(policy)
            line.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        self._paint(False)

        self._fade = QGraphicsOpacityEffect(self)
        self._fade.setOpacity(1.0)
        self._fade.setEnabled(False)
        self.setGraphicsEffect(self._fade)
        self._grow = QPropertyAnimation(self, b"maximumHeight", self)
        self._grow.setDuration(_MOTION_MS)
        self._grow.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._grow.finished.connect(self._settled)
        self._alpha = QPropertyAnimation(self._fade, b"opacity", self)
        self._alpha.setDuration(_MOTION_MS)
        self._on_gone = None

    def _paint(self, active: bool) -> None:

        ink = qcolor(INK_2)
        self.pencil.set_icon("lu.pencil", 14, ink)
        self.up.set_icon("lu.arrow-up", 14, ink)
        self.gone.set_icon("lu.x", 14, ink)
        self.pencil.setVisible(active)
        self.up.setVisible(active and self._arrow_shown)
        self.gone.setVisible(active)
        if bool(self.property("hover")) != active:
            self.setProperty("hover", active)
            repolish(self)

    def _sync_active(self) -> None:
        self._paint(self._pointer or self.hasFocus())

    def set_arrow(self, shown: bool, enabled: bool, tip: str) -> None:
        self._arrow_shown = bool(shown)
        self.up.setEnabled(bool(enabled))
        self.up.setToolTip(tip)
        self._sync_active()

    def enterEvent(self, event):  # noqa: N802
        super().enterEvent(event)
        self._pointer = True
        self._sync_active()

    def leaveEvent(self, event):  # noqa: N802
        super().leaveEvent(event)
        self._pointer = False
        self._sync_active()

    def focusInEvent(self, event):  # noqa: N802
        super().focusInEvent(event)
        self._sync_active()

    def focusOutEvent(self, event):  # noqa: N802
        super().focusOutEvent(event)
        self._sync_active()

    def keyPressEvent(self, event):  # noqa: N802

        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.edit.emit(self.item_id)
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.remove.emit(self.item_id)
            return
        super().keyPressEvent(event)

    def appear(self) -> None:

        target = max(1, self.sizeHint().height())
        self._run(0, target, 0.0, 1.0)

    def leave(self, animate: bool, done) -> None:

        self.leaving = True
        self._on_gone = done
        self.setEnabled(False)
        if not animate or not self.isVisible():
            self._grow.stop()
            self._alpha.stop()
            done(self)
            return
        self._run(self.height(), 0, 1.0, 0.0)

    def _run(self, start: int, end: int, alpha_from: float, alpha_to: float) -> None:
        self._grow.stop()
        self._alpha.stop()
        self.setMaximumHeight(start)
        self._fade.setEnabled(True)
        self._fade.setOpacity(alpha_from)
        self._grow.setStartValue(start)
        self._grow.setEndValue(end)
        self._alpha.setStartValue(alpha_from)
        self._alpha.setEndValue(alpha_to)
        self._grow.start()
        self._alpha.start()

    def _settled(self) -> None:
        if self.leaving:
            if self._on_gone is not None:
                self._on_gone(self)
            return
        self.setMaximumHeight(_QWIDGETSIZE_MAX)
        self._fade.setEnabled(False)

    def stop_animations(self) -> None:
        self._grow.stop()
        self._alpha.stop()


class _MoreLine(QLabel):
    clicked = pyqtSignal()

    def mousePressEvent(self, event):  # noqa: N802
        event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class QueueStrip(QFrame):






    send_now = pyqtSignal(int)
    remove = pyqtSignal(int)
    edit = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("queueTrayHost")
        self.setStyleSheet(_TRAY_QSS)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(TRAY_INSET, 0, TRAY_INSET, 0)
        tray = QFrame(self)
        tray.setObjectName("queueTray")
        tray.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer.addWidget(tray)
        self.reduced_motion = False
        self._expanded = False
        self._rows: dict = {}
        self._order: list = []
        self._leaving: list = []
        col = QVBoxLayout(tray)

        col.setContentsMargins(6, 8, 6, 6 + TRAY_TUCK)
        col.setSpacing(4)
        self._header = QLabel(self)
        self._header.setObjectName("queueHeader")
        self._header.setContentsMargins(8, 0, 8, 0)
        col.addWidget(self._header)
        self._list = QVBoxLayout()
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(2)
        col.addLayout(self._list)
        self._more = _MoreLine(self)
        self._more.setObjectName("queueMore")
        self._more.setCursor(Qt.CursorShape.PointingHandCursor)
        self._more.setContentsMargins(30, 2, 0, 2)
        self._more.clicked.connect(self._expand)
        self._more.hide()
        col.addWidget(self._more)
        self.hide()

    def header_text(self) -> str:
        return self._header.text()

    def more_text(self) -> str:
        return self._more.text() if self._more.isVisibleTo(self) else ""

    def _expand(self) -> None:
        self._expanded = True
        self._layout_rows()

    def set_items(self, items, *, header: str, arrow) -> None:

        items = list(items or [])
        count = len(items)
        self._header.setText(header)
        if count <= _FOLDED:
            self._expanded = False
        wanted = [int(item["id"]) for item in items]
        animate = not self.reduced_motion and self.isVisible()
        for item_id in [i for i in self._order if i not in wanted]:
            row = self._rows.pop(item_id)
            self._leaving.append(row)
            row.leave(animate, self._drop)
        for item in items:
            row = self._rows.get(int(item["id"]))
            if row is None:
                row = _QueueRow(item, self)
                row.send_now.connect(self.send_now.emit)
                row.remove.connect(self.remove.emit)
                row.edit.connect(self.edit.emit)
                self._rows[row.item_id] = row
            row.set_arrow(*arrow(item))
        self._order = wanted
        if items:
            self.setVisible(True)
        self._layout_rows()
        for item_id in wanted:
            row = self._rows[item_id]
            if row.fresh:
                row.fresh = False
                if animate and not row.isHidden():
                    row.appear()
        self._hide_if_empty()

    def _layout_rows(self) -> None:

        for index in reversed(range(self._list.count())):
            entry = self._list.itemAt(index)
            widget = entry.widget() if entry is not None else None
            if widget is not None:
                self._list.removeWidget(widget)
        folded = not self._expanded and len(self._order) > _FOLDED
        for index, item_id in enumerate(self._order):
            row = self._rows[item_id]
            hidden = folded and index >= _FOLDED
            self._list.addWidget(row)
            row.setVisible(not hidden)
        for row in self._leaving:
            self._list.addWidget(row)
        more = len(self._order) - _FOLDED if folded else 0
        self._more.setText(self.tr("Show {n} more").format(n=more))
        self._more.setVisible(more > 0)

    def _drop(self, row) -> None:
        if row in self._leaving:
            self._leaving.remove(row)
        self._list.removeWidget(row)
        row.hide()
        row.deleteLater()
        self._hide_if_empty()

    def _hide_if_empty(self) -> None:
        if not self._order and not self._leaving:
            self.setVisible(False)

    def stop_animations(self) -> None:
        for row in list(self._rows.values()) + list(self._leaving):
            row.stop_animations()


class _ChatPanelQueue:











    def _init_queue(self) -> None:


        self._queues: dict = {}
        self._queue_key = None
        self._steer_ok = False
        strip = self.queue_strip
        strip.send_now.connect(self._queue_send_now)
        strip.remove.connect(self._queue_remove)
        strip.edit.connect(self._queue_edit)
        self.composer.queue_clicked.connect(self._on_queue)
        self.composer.queue_recall_requested.connect(self._queue_edit_last)
        self._sync_queue()

    def _queue(self) -> dict:
        key = "" if self._queue_key is None else self._queue_key
        return self._queues.setdefault(key, {"items": [], "paused": False})

    def queue_items(self) -> list:

        if self._queue_key is None:
            return []
        return [{"text": i["text"], "chips": len(i["chips"]), "attachments": len(i["attachments"]),
                 "sent": bool(i.get("steer_id"))} for i in self._queue()["items"]]

    def queue_paused(self) -> bool:
        return self._queue_key is not None and bool(self._queue()["paused"])



    def set_queue_thread(self, thread_id) -> None:


        key = None if thread_id is None else str(thread_id)
        if key == self._queue_key:
            return
        self._park_queue()
        self._queue_key = key
        self._sync_queue()

    def set_steer_available(self, available: bool) -> None:

        self._steer_ok = bool(available)
        self._sync_queue()

    def append_steer(self, run_id: str, steer_id: str, text: str) -> None:


        for entry in self._queues.values():
            for item in list(entry["items"]):
                if item.get("steer_id") == steer_id:
                    entry["items"].remove(item)
        if self._live_run(run_id) is not None:
            self._add(UserBubble(text, [], []), animate=False)
            QTimer.singleShot(0, self.message_list.scroll_to_bottom)
        self.composer.remember_sent(text, [])
        self._sync_queue()

    def steer_refused(self, steer_id: str) -> None:


        for entry in self._queues.values():
            for item in entry["items"]:
                if item.get("steer_id") == steer_id:
                    item["held"] = item.get("run_id", "")
                    item["steer_id"] = ""
        self._sync_queue()



    def _park_queue(self) -> None:
        if self._queue_key is not None:
            entry = self._queues.get(self._queue_key)
            if entry and entry["items"]:
                entry["paused"] = True

    def _arrow(self, item: dict) -> tuple:

        if self._current_run is None:
            return True, True, self.tr("Send this message now")
        return False, False, ""

    def _joins_run(self, item: dict) -> bool:


        return (self._current_run is not None and self._steer_ok and not extras_of(item)
                and item.get("held") != self._current_run)

    def _queue_header(self, entry: dict) -> str:
        items = entry["items"]
        count = len(items)
        if entry["paused"]:
            return self.tr("Paused · nothing is sent until you choose")
        lead = self.tr("Queued") if count == 1 else self.tr("{n} queued").format(n=count)
        if any(self._joins_run(i) for i in items):
            return self.tr("{lead} · the agent reads it at its next step").format(lead=lead)
        return self.tr("{lead} · sent when the agent finishes").format(lead=lead)

    def _push_rows(self, entry: dict) -> None:

        run_id = self._current_run
        if not run_id or entry["paused"]:
            return
        for item in entry["items"]:
            if not item.get("steer_id") and self._joins_run(item):
                item["steer_id"], item["run_id"] = uuid.uuid4().hex[:16], run_id
                self.steer_requested.emit(run_id, item["steer_id"], item["text"])

    def _withdraw(self, item: dict) -> None:

        if item.get("steer_id"):
            self.unsteer_requested.emit(item.get("run_id", ""), item["steer_id"])
            item["steer_id"] = ""

    def _sync_queue(self) -> None:
        shown = self._queue_key is not None
        entry = self._queue() if shown else {"items": [], "paused": False}
        if shown:
            self._push_rows(entry)
        items = entry["items"]
        self.queue_strip.reduced_motion = bool(self._reduced_motion())
        self.queue_strip.set_items(items, header=self._queue_header(entry), arrow=self._arrow)
        self.composer.set_queue_count(len(items), QUEUE_MAX)

    def _find(self, item_id: int):
        items = self._queue()["items"]
        return next((i for i in items if int(i["id"]) == int(item_id)), None)

    def _on_queue(self) -> None:

        text = self.composer.text().strip()
        entry = self._queue()
        if not text or len(entry["items"]) >= QUEUE_MAX:
            return
        entry["items"].append(new_item(text, self.composer.peek_chips(), self.composer.attachments()))
        self.composer.take_chips()
        self.composer.clear()
        self.composer.clear_attachments()
        self._sync_queue()

    def _queue_remove(self, item_id: int) -> None:
        item = self._find(item_id)
        if item is not None:
            self._withdraw(item)
            self._queue()["items"].remove(item)
        self._sync_queue()

    def _queue_edit_last(self) -> None:
        items = self._queue()["items"] if self._queue_key is not None else []
        if items:
            self._queue_edit(items[-1]["id"])

    def _queue_edit(self, item_id: int) -> None:

        item = self._find(item_id)
        if item is None:
            return
        self._withdraw(item)
        self._queue()["items"].remove(item)
        c = self.composer
        typed = c.text().strip()
        c.set_text(typed + "\n\n" + item["text"] if typed else item["text"])
        for chip in item["chips"]:
            if isinstance(chip, dict):
                c.add_chip(dict(chip))
        for attachment in item["attachments"]:
            if isinstance(attachment, dict):
                c.add_attachment(dict(attachment))
        c.focus_input()
        self._sync_queue()

    def _queue_send_now(self, item_id: int) -> None:
        entry = self._queue()
        item = self._find(item_id)
        if item is None or self._current_run:

            return
        if self._send_item(item):
            entry["items"].remove(item)
            entry["paused"] = False
        self._sync_queue()

    def _send_item(self, item: dict) -> bool:



        if self.composer.connection_state() != "online":
            self.composer.show_warning(self.tr("Not connected to the agent service. The message stays queued."))
            return False
        before = self._last_run
        chips = live_layer_chips(item["chips"], layer_name)
        self.queue_send_requested.emit(item["text"], chips, list(item["attachments"]))
        if self._last_run == before:
            return False
        self.composer.remember_sent(item["text"], item["attachments"])
        return True

    def _queue_after_run(self, status: str) -> None:



        if self._queue_key is None:
            return
        entry = self._queue()
        if entry["items"]:
            if status != "done":
                entry["paused"] = True
            elif not entry["paused"] and self._current_run is None:
                item = entry["items"].pop(0)
                if not self._send_item(item):
                    entry["items"].insert(0, item)
                    entry["paused"] = True

        self._sync_queue()

    def _schedule_queue_after_run(self, status: str) -> None:
        QTimer.singleShot(0, lambda s=str(status or ""): self._queue_after_run(s))
