# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later














from __future__ import annotations

import json
import time
import weakref

from qgis.PyQt.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPointF, Qt, QTimer
from qgis.PyQt.QtWidgets import QApplication

from ..core import mascot as table_of
from ..core.host_platform import prefers_reduced_motion
from ..core.qt_compat import qt_deleted
from .font_scale import scale_px_length
from .mascot import BOX_H, BOX_W, GAZE, LIVE, STILL, Mascot

HEAD, HOME, SIGNIN, CARD, QUESTION, OFFER = "head", "home", "signin", "card", "question", "offer"
_PEEK = 0.27
_PEEK_INSET = 48
_STAGES = weakref.WeakSet()


def refresh_mascots() -> None:

    for stage in list(_STAGES):
        stage.refresh_mode()
        stage.sync()


class _Watch(QObject):


    def __init__(self, stage):
        super().__init__(stage)
        self._stage = stage
        self._events = {}

    def watch(self, widget, event: str, when=None) -> None:
        if widget in self._events:
            return
        self._events[widget] = (event, when)
        widget.installEventFilter(self)
        widget.destroyed.connect(self._forget)

    def _forget(self, widget=None) -> None:
        self._events = {w: e for w, e in self._events.items() if not qt_deleted(w) and w is not widget}

    def eventFilter(self, watched, event):  # noqa: N802
        kind = event.type()
        if kind == QEvent.Type.Enter and watched in self._events:
            name, when = self._events[watched]
            if when is None or when():
                self._stage.mascot.fire(name, watched)
        elif kind in (QEvent.Type.Leave, QEvent.Type.Hide):
            self._stage.mascot.hover(None)
        return False


class MascotStage(QObject):


    def __init__(self, panel, settings=None):
        super().__init__(panel)
        self._panel = panel
        self._settings = settings
        self._host = panel
        self._served = None
        table = self._stored_table() or table_of.BUILT_IN
        self.mascot = Mascot(panel, table)
        self.mascot.on_tick = self._place
        self._watch = _Watch(self)
        self._where = None
        self._seat_held = False
        self._from = None
        self._t0 = 0.0
        self._drawn_at = None
        self._from_card = self._peeked = None
        self._relay = False
        self._solid = {}
        self._perches = {}
        self._clear = []
        self._zone = None
        self._last_drop = None
        self._lit = None
        self._perched = None
        self.mascot.on_carry = self._carried
        self.mascot.on_drop = self._dropped
        self.mascot.on_home = self._leave_perch
        self._perch_over = QTimer(self)
        self._perch_over.setSingleShot(True)
        self._perch_over.timeout.connect(self._leave_perch)
        self._waiting = []
        self._running = False
        self._shown_once = False
        self._hidden_at = None
        self._ease = QEasingCurve(QEasingCurve.Type.BezierSpline)

        self._ease.addCubicBezierSegment(QPointF(0.3, 0.0), QPointF(0.2, 1.1), QPointF(1.0, 1.0))
        self._later = QTimer(self)
        self._later.setSingleShot(True)
        self._later.setInterval(0)
        self._later.timeout.connect(self._place)
        panel.installEventFilter(self)
        self._apply_table(table)
        _STAGES.add(self)



    def _stored_table(self):

        if self._settings is None:
            return None
        try:
            return table_of.merged(json.loads(self._settings.mascot_table or "null"))
        except (ValueError, TypeError):
            return None

    def apply_served(self, served) -> None:

        if served == self._served:
            return
        self._served = served
        table = table_of.merged(served) if served is not None else None
        if self._settings is not None:
            self._settings.mascot_table = json.dumps(served) if table is not None else ""
        self._apply_table(table or table_of.BUILT_IN)

    def _apply_table(self, table: dict) -> None:
        self.mascot.set_table(table)
        sizes = table["sizes"]
        self._panel.header.set_mark_px(scale_px_length(sizes[HEAD]))
        self._panel.empty_state.set_mark_px(scale_px_length(sizes[HOME]))
        self.refresh_mode()
        self.sync()

    def animated(self) -> bool:
        return self._settings is None or self._settings.mascot_animated

    def refresh_mode(self) -> None:


        if not self.animated() or not self.mascot.table()["enabled"]:
            mode = STILL
        elif prefers_reduced_motion():
            mode = GAZE
        else:
            mode = LIVE
        self.mascot.set_mode(mode)



    def set_host(self, host) -> None:

        if host is self._host or host is None:
            return
        self._host.removeEventFilter(self)
        self._host = host
        host.installEventFilter(self)
        self.mascot.setParent(host)
        self.mascot.show()
        self.sync()

    def fire(self, event: str, look=None) -> None:
        if self._waiting and event.startswith(("step.", "run.think", "run.write")):
            self.mascot.stir()
            return
        self.mascot.fire(event, look)

    def watch(self, widget, event: str, when=None) -> None:

        self._watch.watch(widget, event, when)

    def solid(self, widget, name: str, perch=None) -> None:




        self._solid[widget] = name
        self._perches[widget] = perch
        widget.destroyed.connect(self._forget_solid)

    def keep_clear(self, widget) -> None:

        self._clear.append(widget)

    def _forget_solid(self, *_args) -> None:
        self._solid = {w: n for w, n in self._solid.items() if not qt_deleted(w)}
        self._perches = {w: b for w, b in self._perches.items() if not qt_deleted(w)}
        if qt_deleted(self._lit):
            self._lit = None

    def _under(self):

        cx, bottom = self.mascot.feet()
        size = self.mascot.table()["sizes"].get(self._where or HOME, 24)
        widget = self._top(cx, bottom - scale_px_length(size) * 0.45)
        while widget is not None and widget is not self._host:
            if widget in self._solid and widget.isVisible() and widget.isEnabled():
                return widget
            widget = widget.parentWidget()
        return None

    def _light(self, widget) -> None:

        old, self._lit = self._lit, widget
        app = QApplication.instance()
        for target, on in ((old, False), (widget, True)):
            if qt_deleted(target):
                continue
            target.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, on)
            app.sendEvent(target, QEvent(QEvent.Type.Enter if on else QEvent.Type.Leave))
            target.update()

    def _named(self, prefix: str, widget) -> str:
        event = f"{prefix}.{self._solid.get(widget, '')}"
        return event if event in self.mascot.table()["events"] else prefix

    def _carried(self) -> None:
        widget = self._under()

        zone = widget if widget is not None else self.mascot.feet()[1] < self._host.height() / 3.0
        if zone is self._zone and not self.mascot.take_stirred():
            return
        self._zone = zone
        self._light(widget)
        if widget is not None:
            self.mascot.fire(self._named("carry.over", widget), widget)
        else:
            self.mascot.fire("carry.high" if zone else "home.grab")

    def _dropped(self, thrown: bool = False):


        widget = self._under()
        self._light(None)
        self._zone = None
        name = None if thrown else self._solid.get(widget)
        again, self._last_drop = name is not None and name == self._last_drop, name
        if widget is None or thrown:
            return None

        self.mascot.fire("drop.again" if again else self._named("drop.on", widget), widget)
        block = self._perches.get(widget)
        if qt_deleted(block) or not block.isVisible():
            return None
        o = self._origin(block)
        size = scale_px_length(self.mascot.table()["sizes"].get(self._where or HOME, 24))
        half = size * BOX_W / BOX_H / 2.0
        lo, hi = o.x() + half + 4, o.x() + block.width() - half - 4
        cx = min(max(self.mascot.feet()[0], lo), hi)


        for text in self._clear:
            if qt_deleted(text) or not text.isVisible():
                continue
            t = self._origin(text)
            if t.y() + text.height() <= o.y() - size or t.y() >= o.y():
                continue
            left, right = t.x() - half - 6, t.x() + text.width() + half + 6
            if left < cx < right:
                sides = [x for x in (left, right) if lo <= x <= hi]
                if not sides:
                    return None
                cx = min(sides, key=lambda x: abs(x - cx))
        self._perched = (block, (o.x(), o.y(), block.width()))
        self._perch_over.start(int(self.mascot.table()["params"]["perch_s"] * 1000))
        return cx, o.y() + 2.0

    def _leave_perch(self) -> None:
        self._perch_over.stop()
        if self._perched is not None:
            self._perched = None
            self.mascot.come_back()

    def listen_to(self, dictation) -> None:


        dictation.level.connect(self.mascot.hear)
        dictation.state_changed.connect(self._dictation_state)
        dictation.text_ready.connect(lambda _text: self.mascot.fire("dictate.text", self._panel.composer))
        dictation.failed.connect(lambda _code: self.mascot.fire("dictate.failed"))

    def _dictation_state(self, state: str) -> None:
        event = {"starting": "dictate.start", "asking": "dictate.asking", "recording": "dictate.listen",
                 "transcribing": "dictate.transcribe"}.get(state)
        if event is None:
            self.rest()
        elif not self._running and not self._waiting:

            self.mascot.fire(event, self._panel.composer._dictation_ui.bar)

    def run_began(self) -> None:
        self._running = True
        self.mascot.set_lively(True)
        self.fire("run.start")
        self.sync()

    def run_ended(self, status: str) -> None:
        self._running = False
        self.mascot.set_lively(False)
        self._waiting = []
        self.rest()
        if not self._panel.quota_walled():
            self.mascot.fire({"done": "run.done", "failed": "run.failed"}.get(status, "run.stopped"))
        self.sync()

    def troubled(self) -> None:


        if self._panel.quota_walled():
            self.rest()
        else:
            self.mascot.fire("error")

    def rest(self) -> None:

        if self._running or self._waiting:
            return
        composer = getattr(self._panel, "composer", None)
        if composer is not None and composer.isVisible() and composer.text().strip():
            self._leave_perch()
            self.mascot.fire("compose.typing", composer)
        elif self._panel.quota_walled():
            self.mascot.fire("quota.wall", self._offer_look())
        elif self._at_offer() in ("low", "return", "welcome"):
            self.mascot.fire(f"offer.{self._panel.quota_card.moment}", self._offer_look())
        else:
            self.mascot.fire("rest")

    def offer_moment(self, moment: str) -> None:


        self.sync()
        if self._where != OFFER:
            return
        if moment == "copied":
            self.mascot.fire("offer.copied", self._offer_look())
            return
        self.rest()
        self.mascot.fire(f"offer.{moment}.hello", self._offer_look())

    def offer_closed(self) -> None:

        if self._where == OFFER:
            self.mascot.fire("offer.closed")

    def _at_offer(self) -> str:

        return self._panel.quota_card.moment if self._where == OFFER else ""

    def _offer_look(self):
        card = self._panel.quota_card
        return card.focus_widget() if self._where == OFFER and card.isVisible() else None

    def card_shown(self, place: str, card, event: str) -> None:

        self._waiting = [(p, c) for p, c in self._waiting if not qt_deleted(c) and c is not card] + [(place, card)]
        if place == QUESTION and self.mascot.table()["places"].get(QUESTION) and self.mascot.mode() != STILL \
                and hasattr(card, "set_seat_inset"):
            width = scale_px_length(self.mascot.table()["sizes"][QUESTION]) * BOX_W / BOX_H
            card.set_seat_inset(round(width) + 6)
        card.destroyed.connect(self._card_destroyed)
        self.mascot.fire(event, card)
        self.sync()

    def cards_changed(self) -> None:

        before = len(self._waiting)
        self._waiting = [(p, c) for p, c in self._waiting
                         if not qt_deleted(c) and (not callable(getattr(c, "is_open", None)) or c.is_open())]
        if len(self._waiting) == before:
            return
        if self._waiting:
            self.mascot.look_at(self._waiting[-1][1])
        elif self._running:
            self.mascot.fire("run.think")
        else:
            self.rest()
        self.sync()

    def _card_destroyed(self, *_args) -> None:
        self._waiting = [(p, c) for p, c in self._waiting if not qt_deleted(c)]
        self._later.start()

    def sync(self) -> None:

        self._place()
        self._later.start()



    def eventFilter(self, watched, event):  # noqa: N802
        kind = event.type()
        if kind in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.LayoutRequest):
            self._later.start()
        if kind == QEvent.Type.Show and watched is self._host:
            now = time.perf_counter()
            if not self._shown_once:
                self._shown_once = True
                self.mascot.fire("panel.open")
            elif self._hidden_at is not None and now - self._hidden_at > 60 and not self._running:
                self.mascot.fire("panel.back")
        elif kind == QEvent.Type.Hide and watched is self._host:
            self._hidden_at = time.perf_counter()
        elif kind in (QEvent.Type.Enter, QEvent.Type.WindowActivate, QEvent.Type.MouseButtonPress,
                      QEvent.Type.KeyPress) and (self.mascot.asleep() or self.mascot.settled()):
            self.mascot.stir()
        return False

    def _top(self, x: float, y: float):

        through = self.mascot.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.mascot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        try:
            return self._host.childAt(QPoint(round(x), round(y)))
        finally:
            self.mascot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, through)

    def _uncovered(self, anchor, x: float, y: float) -> bool:

        top = self._top(x, y)
        return top is not None and (top is anchor or anchor.isAncestorOf(top) or top.isAncestorOf(anchor))

    def _origin(self, widget) -> QPoint:
        return widget.mapTo(self._host, QPoint(0, 0))

    def _head(self):
        slot = self._panel.header.mark_slot()
        size = scale_px_length(self.mascot.table()["sizes"][HEAD])
        o = self._origin(slot)

        return o.x() + size * BOX_W / BOX_H / 2.0, o.y() + slot.height(), size, None

    def _card_geo(self, place: str, card):

        table = self.mascot.table()
        if not table["places"].get(place) or qt_deleted(card) or not card.isVisible():
            return None
        if callable(getattr(card, "is_open", None)) and not card.is_open():
            return None
        viewport = self._panel.message_list.viewport()
        if not viewport.isVisible():
            return None
        size = scale_px_length(table["sizes"][place])
        view_top = self._origin(viewport).y()
        view_bottom = view_top + viewport.height()
        o = self._origin(card)
        if place == CARD:
            cx = o.x() + card.width() - max(scale_px_length(_PEEK_INSET), size)
            top_edge = o.y()
            bottom = top_edge + size * _PEEK
            if bottom - size < view_top or top_edge > view_bottom - 12:
                return None
            if not self._uncovered(viewport, cx, top_edge - 3):
                return None
            self._peeked = card
            return cx, bottom, size, top_edge + 1
        seat = card.seat() if hasattr(card, "seat") else None
        if qt_deleted(seat) or not seat.isVisible():
            return None
        so = self._origin(seat)
        width = size * BOX_W / BOX_H
        cx = so.x() + seat.width() - width / 2.0
        bottom = so.y() + min(seat.height(), size * 0.9)
        if bottom - size < view_top or bottom > view_bottom:
            return None
        if not self._uncovered(viewport, cx, bottom - size * 0.4):
            return None
        return cx, bottom, size, None

    def _resolve(self):


        panel, table = self._panel, self.mascot.table()
        self._seat_held = False
        if not panel.isVisible():
            return None, None
        places = table["places"]
        if panel.activation.isVisible():
            if places.get(SIGNIN):
                slot, point = panel.activation.mascot_seat()
                o = self._origin(slot)
                return SIGNIN, (o.x() + point.x(), o.y() + point.y(), scale_px_length(table["sizes"][SIGNIN]), None)
            return HEAD, self._head()
        for place, card in reversed(self._waiting):
            geo = self._card_geo(place, card)
            if geo is not None:
                return place, geo
            break
        card = panel.quota_card
        if places.get(OFFER) and card.isVisible() and card.state and not self._running:
            card.set_seat_shown(True)
            seat = card.seat()
            o = self._origin(seat)
            size = scale_px_length(table["sizes"][OFFER])
            cx, bottom = o.x() + seat.width() / 2.0, o.y() + seat.height()
            if self._uncovered(card, cx, bottom - size * 0.5):
                return OFFER, (cx, bottom, size, None)


            self._seat_held = True
        if places.get(HOME) and panel.empty_state.isVisible() and not self._running:
            panel.empty_state.set_mark_shown(True)
            slot = panel.empty_state.mark_slot()
            o = self._origin(slot)
            size = scale_px_length(table["sizes"][HOME])
            cx, bottom = o.x() + slot.width() / 2.0, o.y() + slot.height()
            if self.mascot.held() or self._uncovered(slot, cx, bottom - size * 0.3):
                return HOME, (cx, bottom, size, None)
            return None, None
        return HEAD, self._head()

    def _waiting_card_at(self, place):
        return self._peeked if place == CARD else None

    def where(self):
        return self._where

    def _place(self) -> None:
        mascot, host = self.mascot, self._host
        if qt_deleted(host) or qt_deleted(self._panel):
            return
        try:
            where, geo = self._resolve()
        except RuntimeError:
            self._waiting = [(p, c) for p, c in self._waiting if not qt_deleted(c)]
            return
        header = self._panel.header

        if where != OFFER and not self._seat_held:
            self._panel.quota_card.set_seat_shown(False)
        self._panel.empty_state.set_mark_shown(where != OFFER)
        if where is None:
            header.set_mark_shown(False, animate=False)
            mascot.hide()
            self._where = None
            return
        now = time.perf_counter()
        live = mascot.mode() == LIVE and mascot.ticking()
        if self._perched is not None:

            widget, was = self._perched
            if where != self._where or qt_deleted(widget) or not widget.isVisible() \
                    or (self._origin(widget).x(), self._origin(widget).y(), widget.width()) != was:
                self._leave_perch()
        if where != self._where:
            travel = live and self._where is not None and self._drawn_at is not None and mascot.isVisible()
            self._light(None)
            ax, ay = mascot.take_away()
            self._from = (self._drawn_at[0] + ax, self._drawn_at[1] + ay, self._drawn_at[2]) if travel else None

            self._relay = travel and (bool({where, self._where} & {CARD, QUESTION})
                                      or {where, self._where} == {OFFER, HEAD})

            look = self._panel.quota_card.focus_widget() if where == OFFER else None
            if where == OFFER or self._where == OFFER:
                mascot.look_at(look)

            self._from_card = self._waiting_card_at(self._where) if travel else None
            self._t0 = now
            if travel and not self._relay:
                mascot.nudge((geo[0] - self._drawn_at[0]) * 1.5)

            if not (self._relay and self._where == HEAD):
                header.set_mark_shown(where == HEAD, animate=travel)
            self._where = where
            mascot.raise_()
        params = mascot.table()["params"]
        elapsed = (now - self._t0) * 1000.0
        cx, bottom, size, clip = geo
        alpha, t = 1.0, 1.0
        if self._from is not None and self._relay:
            leave, arrive = params["leave_ms"], params["arrive_ms"]
            if elapsed < leave:
                t = 0.0
                e = elapsed / leave
                cx, bottom, size = self._from
                alpha, size = 1.0 - e, size * (1.0 - 0.3 * e * e)
                clip = self._origin(self._from_card).y() + 1 \
                    if not qt_deleted(self._from_card) and self._from_card.isVisible() else None
            elif elapsed < leave + arrive:
                t = 0.5
                header.set_mark_shown(where == HEAD)
                e = 1.0 - (1.0 - (elapsed - leave) / arrive) ** 3
                if clip is not None:
                    bottom += size * (1.0 - _PEEK) * (1.0 - e)
                else:
                    alpha, size = e, size * (0.7 + 0.3 * e)
        elif self._from is not None and params["travel_ms"] > 0:
            t = elapsed / params["travel_ms"]
            if t < 1.0:
                e = self._ease.valueForProgress(t)
                fx, fb, fs = self._from
                cx, bottom, size = fx + (cx - fx) * e, fb + (bottom - fb) * e, max(4.0, fs + (size - fs) * e)
                if clip is None and not qt_deleted(self._from_card) and self._from_card.isVisible():
                    clip = self._origin(self._from_card).y() + 1
        if t >= 1.0:
            if self._from is not None:
                header.set_mark_shown(where == HEAD)
            self._from = None
        mascot.set_alpha(alpha)
        mascot.set_fast(t < 1.0)
        mascot.set_playful(where in (HOME, SIGNIN) and t >= 1.0 and mascot.mode() == LIVE)
        mascot.put(cx, bottom, size, clip)
        self._drawn_at = (cx, bottom, size)
        if not mascot.isVisible():
            mascot.show()
            mascot.raise_()


__all__ = ["CARD", "HEAD", "HOME", "OFFER", "MascotStage", "QUESTION", "SIGNIN", "refresh_mascots"]
