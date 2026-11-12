# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

















from __future__ import annotations

import math
import random
import re
import time

from qgis.PyQt.QtCore import QPointF, QRect, Qt, QTimer
from qgis.PyQt.QtGui import QColor, QCursor, QPainter, QPainterPath, QPen
from qgis.PyQt.QtWidgets import QApplication, QWidget

from ..core import mascot as table_of
from . import tool_describe

BOX_W, BOX_H = 796.0, 1024.0
_NAVY, _LIME, _CEL = "#1e487b", "#cdda65", "#b7ca50"
_LEAF, _LEAF_LIGHT, _LEAF_DARK = "#227ef6", "#5aa2fa", "#1560cf"
_BLUSH = "#eaae8e"
_BODY = "M398 150C476 150 778 436 778 684C778 880 608 1024 398 1024C188 1024 18 880 18 684C18 436 320 150 398 150Z"
_BODY_CX, _APEX = 398.0, (400.0, 168.0)
_STEM = "M0 8C4-40 16-74 34-100"

_R1 = ("M30-90C22-176 150-206 198-150C206-84 108-46 30-90Z", "M30-90Q120-112 198-150C206-84 108-46 30-90Z")
_R2 = ("M26-76C8-150-96-156-124-104C-122-44-38-34 26-76Z", "M26-76Q-50-84-124-104C-122-44-38-34 26-76Z")
_PIVOT_1, _PIVOT_2 = (30.0, -90.0), (26.0, -76.0)
_EYE = {"cy": 608.0, "sp": 114.0, "rx": 94.0, "ry": 112.0, "pupil": (54.0, 64.0), "travel": (34.0, 42.0),
        "hi": (-20.0, -28.0, 21.0)}
_EYES = ((_BODY_CX - _EYE["sp"], _EYE["cy"]), (_BODY_CX + _EYE["sp"], _EYE["cy"]))

PAD_X, PAD_TOP, PAD_BOTTOM = 0.24, 0.32, 0.08

LIVE, GAZE, STILL = "live", "gaze", "still"
_FAST_MS, _SLOW_MS = 16, 33
_DIZZY_CLICKS = 4
_SHAKE_TURNS = 4
_THROW_PX_S = 900
_SETTLE_S = 2.5
_NAP_MS = 400
_STILL_S = 0.4
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_SEGMENT = re.compile(r"([MCQZ])([^MCQZ]*)")
_STEP_EVENTS = {tool_describe.TONE_FIND: "step.read", tool_describe.TONE_DATA: "step.download",
                tool_describe.TONE_MAP: "step.map", tool_describe.TONE_VIEW: "step.map",
                tool_describe.TONE_DELETE: "step.map", tool_describe.TONE_LABEL: "step.style",
                tool_describe.COLOUR_PLUGIN: "step.style"}


def pointer_pos():

    return QCursor.pos()


def step_event(name: str, args=None) -> str:


    _glyph, tone, family = tool_describe.tool_glyph(name, args)
    if family == tool_describe.FAMILY_CONNECTOR:
        return "step.search"
    return _STEP_EVENTS.get(tone, "step.other")


def _path(d: str) -> QPainterPath:

    path = QPainterPath()
    for cmd, body in _SEGMENT.findall(d):
        n = [float(x) for x in _NUMBER.findall(body)]
        if cmd == "M":
            path.moveTo(n[0], n[1])
        elif cmd == "C":
            for i in range(0, len(n), 6):
                path.cubicTo(n[i], n[i + 1], n[i + 2], n[i + 3], n[i + 4], n[i + 5])
        elif cmd == "Q":
            for i in range(0, len(n), 4):
                path.quadTo(n[i], n[i + 1], n[i + 2], n[i + 3])
        else:
            path.closeSubpath()
    return path


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, v))


def _smooth(t: float) -> float:
    x = _clamp(t, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def _global(event):
    return event.globalPos() if hasattr(event, "globalPos") else event.globalPosition().toPoint()


class Mascot(QWidget):


    def __init__(self, parent=None, table=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._table = table or table_of.BUILT_IN
        self._body = _path(_BODY)
        self._stem = _path(_STEM)
        self._leaf_paths = tuple(tuple(_path(d) for d in leaf) for leaf in (_R1, _R2))
        self._mode = LIVE
        app = QApplication.instance()
        self._app_active = app is None or app.applicationState() == Qt.ApplicationState.ApplicationActive


        self._lively = False
        self._size = 24.0
        self._at = None
        self._frac = (0.0, 0.0)
        self._playful = False
        self._base = "idle"
        self._over = None
        self._burst = None
        self._react = None
        self._cur = "idle"
        self._look = None
        self._run_since = None
        self._asleep = None
        self._fast = False
        self._alpha = 1.0
        self._settled = None
        self._changed = 0.0
        self._seed = random.random() * 100.0  # nosec B311
        now = time.perf_counter()
        self._t0 = self._last = self._active = now
        self._switched = -1e9
        self._attention = 0.0
        self._pointer = (None, -1e9)
        self._on_me_since = None
        self._strokes = []
        self._clicks = []
        self._held = None
        self._away = [0.0, 0.0, 0.0, 0.0]
        self._turns, self._way = [], None

        self._heard = [0.0, 0.0, 0.0, 0.0]
        self._still = False
        self._far = False
        self._stirred = False
        self.on_home = None
        self._perch = None
        self._edged = False
        self._swing = 0.0
        self._drawn = None
        self._pose = {}
        self._s = {}
        self.on_tick = None
        self.on_carry = None
        self.on_drop = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        if app is not None:
            app.applicationStateChanged.connect(self._on_app_state)

        self._nap = QTimer(self)
        self._nap.setSingleShot(True)
        self._nap.timeout.connect(self._nap_over)
        self._nap_until = 0.0
        self._snap()



    def table(self) -> dict:
        return self._table

    def set_table(self, table: dict) -> None:
        self._table = table
        if self._base not in table["moods"]:
            self._base = "idle"
        self._over = self._burst = self._react = None
        self._drawn = None
        self.update()

    def set_mode(self, mode: str) -> None:


        if mode != self._mode:
            self._mode = mode
            self._sync()

    def mode(self) -> str:
        return self._mode

    def _on_app_state(self, state) -> None:
        self._app_active = state == Qt.ApplicationState.ApplicationActive
        self._sync()

    def set_lively(self, lively: bool) -> None:

        self._lively = bool(lively)
        self.stir()

    def set_playful(self, playful: bool) -> None:

        if playful == self._playful:
            return
        self._playful = bool(playful)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not playful)
        if playful:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()
            self._held = None

    def feet(self):

        at = self._at or (0.0, 0.0)
        return at[0] + self._away[0], at[1] + self._away[1]

    def come_back(self) -> None:

        if self._perch is not None:
            self._perch = None
            self.stir()

    def take_away(self):

        away = (self._away[0], self._away[1])
        self._perch = None
        self._away[:] = [0.0, 0.0, 0.0, 0.0]
        return away

    def hear(self, level: float) -> None:

        now = time.perf_counter()
        self._heard[0], self._heard[1] = _clamp(float(level), 0.0, 1.0), now
        if self._heard[0] > self._table["params"]["voice"]["floor"]:
            self.stir()
        if not self._heard[3]:
            self._heard[3] = now

    def _voice(self, now: float, dt: float, mood: str):

        gains = self._table["moods"].get(mood, {})
        if not (gains.get("voice") or gains.get("quiet")):
            self._heard = [0.0, 0.0, 0.0, 0.0]
            return None
        v, h = self._table["params"]["voice"], self._heard
        level = h[0] if now - h[1] < 0.2 else 0.0
        h[2] += (level - h[2]) * (1.0 - math.exp(-dt / (v["attack"] if level > h[2] else v["release"])))
        if level > v["floor"] or not h[3]:
            h[3] = now
        return h[2], _clamp((now - h[3] - v["quiet_s"]) / 0.5, 0.0, 1.0)

    def take_stirred(self) -> bool:

        stirred, self._stirred = self._stirred, False
        return stirred

    def held(self) -> bool:
        return self._held is not None

    def put(self, cx: float, bottom: float, size: float, clip_bottom=None) -> None:


        self._at = (cx, bottom, size, clip_bottom)
        self._size = max(4.0, float(size))
        w = self._size * BOX_W / BOX_H
        left = cx - w / 2.0 - self._size * PAD_X + self._away[0]
        top = bottom - self._size * (1.0 + PAD_TOP) + self._away[1]
        x, y = math.floor(left), math.floor(top)
        width = math.ceil(left + w + 2 * self._size * PAD_X) - x
        height = math.ceil(top + self._size * (1.0 + PAD_TOP + PAD_BOTTOM)) - y
        if clip_bottom is not None:
            height = min(height, math.floor(clip_bottom) - y)
        frac = (left - x, top - y)
        rect = QRect(x, y, max(0, width), max(0, height))
        if rect != self.geometry():
            self.setGeometry(rect)
            self._frac = frac
            self.update()
        elif abs(frac[0] - self._frac[0]) + abs(frac[1] - self._frac[1]) > 0.1:
            self._frac = frac
            self.update()

    def set_alpha(self, alpha: float) -> None:
        alpha = _clamp(alpha, 0.0, 1.0)
        if abs(alpha - self._alpha) > 0.004:
            self._alpha = alpha
            self.update()

    def nudge(self, vx: float) -> None:

        if self._s:
            self._s["stem"][1] += _clamp(vx * 0.9, -700.0, 700.0)

    def set_mood(self, mood: str) -> None:
        if mood not in self._table["moods"]:
            return
        self._base = mood
        resting = bool(self._table["moods"][mood]["wander"])
        if resting or mood in ("ask", "curious"):
            self._run_since = None
        elif self._run_since is None:
            self._run_since = time.perf_counter()

    def base(self) -> str:

        return self._base

    def mood(self) -> str:
        if self._burst:
            return self._burst[0]
        if self._over:
            return self._over[0]
        if self._asleep is not None:
            return self._event_mood("sleep", "idle")
        params = self._table["params"]
        if self._base == "think" and self._run_since is not None \
                and time.perf_counter() - self._run_since > params["patience_s"]:
            return params["patience_mood"]
        return self._base

    def hover(self, mood, look=None) -> None:

        self._over = (mood, look) if mood in self._table["moods"] else None
        self.stir()

    def burst(self, mood: str, ms: float = 1300) -> None:
        if mood in self._table["moods"]:
            now = time.perf_counter()
            self._burst = (mood, now + ms / 1000.0)

    def react(self, name: str, seconds: float = 0.9) -> None:
        reaction = self._table["reactions"].get(name)
        if reaction is None or self._mode != LIVE:
            return
        self._react = (name, time.perf_counter(), seconds)
        for channel, (x, v) in reaction["kick"].items():
            if x is not None:
                self._s[channel][0] = x
            if v is not None:
                self._s[channel][1] = v

    def look_at(self, widget) -> None:

        self._look = widget

    def fire(self, event: str, look=None) -> None:

        action = self._table["events"].get(event)
        self.stir()
        if not action:
            return
        if "pick" in action:
            action = random.choice(action["pick"])  # nosec B311
        if "mood" in action:
            self.set_mood(action["mood"])
            self._look = look
        if "hover" in action:
            self.hover(action["hover"], look)
        if "burst" in action:
            self.burst(*action["burst"])
        if "react" in action:
            self.react(*action["react"])

    def stir(self) -> None:

        self._active = time.perf_counter()
        self._settled = None
        if self._asleep is not None:
            self._asleep = None
            wake = self._table["events"].get("wake", {}).get("burst")
            if wake and self._mode == LIVE:
                self.burst(*wake)
        self._sync()

    def asleep(self) -> bool:
        return self._asleep is not None

    def settled(self) -> bool:
        return self._settled is not None

    def ticking(self) -> bool:
        return self._timer.isActive()

    def _event_mood(self, event: str, fallback: str) -> str:
        return self._table["events"].get(event, {}).get("mood") or fallback



    def _sync(self) -> None:
        run = self._mode != STILL and self.isVisible() and self._app_active
        if run and not self._timer.isActive():
            self._last = time.perf_counter()
            self._timer.start(_SLOW_MS)
        elif not run:
            self._timer.stop()
            self._nap.stop()
            if self._mode == STILL:
                self._held = None
                self._away = [0.0, 0.0, 0.0, 0.0]
                self._snap()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._sync()

    def hideEvent(self, event):  # noqa: N802
        self._timer.stop()
        self._nap.stop()
        super().hideEvent(event)

    def _snap(self) -> None:

        o = self._target(time.perf_counter(), 0.0)
        self._s = {k: [o[k], 0.0] for k in table_of.SPRUNG}
        self._pose = o
        self._drawn = None
        self.update()

    def _gaze(self, now: float):

        try:
            look = self._over[1] if self._over and self._over[1] is not None else self._look
            parent = self.parentWidget()
            if self._at is None or parent is None:
                return None, False
            here = parent.mapToGlobal(QPointF(self._at[0] + self._away[0],
                                              self._at[1] + self._away[1] - self._size * 0.45).toPoint())
            if look is not None and look.isVisible():
                point = look.mapToGlobal(look.rect().center())
                wanted = True
            else:
                point = pointer_pos()
                key = (point.x(), point.y())
                if key != self._pointer[0]:
                    inside = parent.rect().contains(parent.mapFromGlobal(point))
                    self._pointer = (key, now if inside else self._pointer[1])
                    if inside:
                        self._active = now
                wanted = now - self._pointer[1] < self._table["params"]["attention"]["release"]
        except RuntimeError:
            self._look = self._over = None
            return None, False
        reach = max(160.0, self._size * BOX_W / BOX_H * 1.2)
        return (math.tanh((point.x() - here.x()) / reach), math.tanh((point.y() - here.y()) / reach)), wanted

    def _target(self, now: float, dt: float) -> dict:
        m = self.mood()
        if m != self._cur:

            if "sleep" not in (m, self._cur) and m != "wake":
                self._switched = now
            self._cur, self._t0 = m, now
        if self._mode == STILL:

            o = table_of.pose(self._table, "idle", 0.0, 0.0, None, 0.0, still=True)
            o["gx"], o["gy"] = self._table["params"]["still_gaze"]
            return o
        gaze, wanted = self._gaze(now)
        fade = self._table["params"]["attention"]["fade"]
        self._attention += ((1.0 if gaze and wanted else 0.0) - self._attention) * (1.0 - math.exp(-dt / fade))
        reaction = None
        if self._react:
            name, began, seconds = self._react
            progress = (now - began) / seconds
            if progress >= 1.0:
                self._react = None
            else:
                reaction = (name, progress)
        return table_of.pose(self._table, m, now + self._seed, now - self._t0, gaze, self._attention,
                             seed=self._seed, since_switch=now - self._switched, reaction=reaction,
                             still=self._mode == GAZE, settled=self._settled is not None,
                             voice=self._voice(now, dt, m))

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = min(0.1, now - self._last)
        self._last = now
        if self._burst and now > self._burst[1]:
            self._burst = None
        if self.on_tick is not None:
            self.on_tick()
        self._spring_home(dt)
        held = self._held
        if held is not None and not self._still and held[3] >= 6 \
                and now - held[4] > self._table["params"]["still_s"]:
            self._still = True
            self.fire("carry.still")
        if self._playful and self._mode == LIVE:
            self._watch_pointer(now)
        params = self._table["params"]
        resting = bool(self._table["moods"].get(self._base, {}).get("wander"))
        if self._asleep is None and resting and self._held is None and self._mode == LIVE \
                and now - self._active > params["sleep_after_s"]:
            self._asleep = now
        o = self._target(now, dt)
        s = self._s
        o["stem"] += _clamp(s["lift"][1] * 0.05, -30, 30) + _clamp(s["gx"][1] * 4, -14, 14)
        steps = max(1, math.ceil(dt / 0.009))
        h = dt / steps
        springs = params["springs"]
        for _ in range(steps):
            for k, (stiff, damp) in springs.items():
                p = s[k]
                p[1] += (stiff * (o[k] - p[0]) - damp * p[1]) * h
                p[0] += p[1] * h
        self._pose = o

        q = self._size / 170.0
        seen = (round(s["gx"][0] * 46 * q), round(s["gy"][0] * 46 * q), round(s["sy"][0] * 1024 * q),
                round(s["lift"][0] * q), round(s["roll"][0] * 15 * q), round(s["stem"][0] * 4 * q),
                round(s["lid"][0] * 150 * q), round(s["brow"][0] * 3 * q), round(s["cheek"][0] * 200 * q),
                round(s["big"][0] * 110 * q), round(s["tx"][0] * q), round(o["blink"] * 150 * q),
                round(o["flick"] * 4 * q), round(s["stem"][1] * 0.05 * q), round(self._swing * 15 * q),
                round(now * 4) if o["zz"] else 0)
        if seen != self._drawn:
            if self._drawn is None or seen[:-1] != self._drawn[:-1]:
                self._changed = now
            self._drawn = seen
            self.update()
        busy = self._react is not None or self._burst is not None or self._held is not None or self._adrift()
        if self._asleep is not None and now - self._asleep > _SETTLE_S and not busy:
            self._timer.stop()
            return

        quiet = ((resting or not self._lively) and self._mode == LIVE and not busy and self._over is None
                 and not self._fast and self._asleep is None and now - self._active > params["settle_after_s"])
        if not quiet:
            self._settled = None
        elif self._settled is None:
            self._settled = now
        elif now - self._changed > _STILL_S and not o["blink"]:
            self._timer.stop()
            self._nap_until = now + table_of.next_blink(self._table, now + self._seed, self._seed)
            self._nap.start(_NAP_MS)
            return
        want = _FAST_MS if busy or self._fast else _SLOW_MS
        if self._timer.interval() != want:
            self._timer.setInterval(want)

    def _nap_over(self) -> None:

        parent = self.parentWidget()
        if parent is None or not self.isVisible():
            return
        point = pointer_pos()
        if (point.x(), point.y()) != self._pointer[0] and parent.rect().contains(parent.mapFromGlobal(point)):
            self.stir()
        elif time.perf_counter() >= self._nap_until:
            self._sync()
        else:
            self._nap.start(_NAP_MS)

    def set_fast(self, fast: bool) -> None:

        self._fast = bool(fast)



    def _mark_rect(self) -> QRect:
        w = self._size * BOX_W / BOX_H
        return QRect(round(self._frac[0] + self._size * PAD_X), round(self._frac[1] + self._size * PAD_TOP),
                     round(w), round(self._size))

    def _watch_pointer(self, now: float) -> None:

        point = self.mapFromGlobal(pointer_pos())
        if self._held is not None or not self._mark_rect().contains(point):
            self._on_me_since = None
            self._strokes = []
            return
        if self._on_me_since is None:
            self._on_me_since = (now, point.x(), point.x(), 0)
            return
        since, last_x, turn_x, way = self._on_me_since
        dx = point.x() - last_x
        if abs(point.x() - turn_x) > 4 and dx and (dx > 0) != (way > 0):
            if way:
                self._strokes = [t for t in self._strokes if now - t < 1.6] + [now]
            way, turn_x = (1 if dx > 0 else -1), point.x()
        if dx:
            since = now
        self._on_me_since = (since, point.x(), turn_x, way)
        if len(self._strokes) >= 4 and not self._burst:
            self._strokes = []
            self.fire("home.pet")
        elif now - since > self._table["params"]["shy_after_s"] and not self._burst:
            self._on_me_since = (now + 4.0, point.x(), turn_x, way)
            self.fire("pointer.rest")

    def mousePressEvent(self, event):  # noqa: N802
        if not self._playful or event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        now = time.perf_counter()
        self._clicks = [t for t in self._clicks if now - t < 2.0] + [now]
        self._still = False
        self._turns, self._way = [], None
        point = _global(event)
        self._held = [point, (self._away[0], self._away[1]), point, 0.0, now, (0.0, 0.0),
                      event.pos().y() < self._frac[1] + self._size * (PAD_TOP + 0.2)]
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.fire("home.grab")
        event.accept()

    def mouseMoveEvent(self, event):  # noqa: N802
        held = self._held
        if held is None:
            return
        point, now = _global(event), time.perf_counter()
        dx, dy = point.x() - held[2].x(), point.y() - held[2].y()
        held[3] += abs(dx) + abs(dy)
        if abs(dx) > 3 and (dx > 0) != self._way:

            self._way = dx > 0
            self._turns = [t for t in self._turns if now - t < 1.2] + [now]
        dt = max(0.004, now - held[4])
        held[5] = (0.6 * held[5][0] + 0.4 * dx / dt, 0.6 * held[5][1] + 0.4 * dy / dt)
        held[2], held[4] = point, now
        away_x, away_y = held[1][0] + point.x() - held[0].x(), held[1][1] + point.y() - held[0].y()
        parent = self.parentWidget()
        if parent is not None and self._at is not None:

            half = self._size * BOX_W / BOX_H / 2.0
            free_x, free_y = away_x, away_y
            away_x = _clamp(away_x, half - self._at[0], parent.width() - half - self._at[0])
            away_y = _clamp(away_y, self._size * 0.6 - self._at[1], parent.height() - self._at[1])
            edged = abs(free_x - away_x) + abs(free_y - away_y) > 2.0
            if edged and not self._edged and self._s:
                self._s["sy"][1] -= 1.6
            self._edged = edged
        self._perch = None
        if self._still and abs(dx) + abs(dy) > 2:
            self._still = False
            self._stirred = True
            self.hover(None)
        self._away[:] = [away_x, away_y, 0.0, 0.0]
        self.nudge(dx * 20)
        if self._at is not None:
            self.put(*self._at)
        if self.on_carry is not None and held[3] >= 6:
            self.on_carry()
        event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802
        held, self._held = self._held, None
        if held is None:
            return
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.hover(None)
        self._edged = False
        vx, vy = held[5] if time.perf_counter() - held[4] < 0.12 else (0.0, 0.0)
        self._away[2], self._away[3] = _clamp(vx, -2500, 2500), _clamp(vy, -2500, 2500)
        self._far = math.hypot(self._away[0], self._away[1]) > self._size
        shaken = len([t for t in self._turns if time.perf_counter() - t < 1.2]) >= _SHAKE_TURNS
        thrown = math.hypot(vx, vy) > _THROW_PX_S and not shaken

        landed = self.on_drop(thrown) if self.on_drop is not None and held[3] >= 6 else None
        if landed is not None and self._at is not None:

            self._perch = (landed[0] - self._at[0], landed[1] - self._at[1])
            self._away[2], self._away[3] = self._away[2] * 0.3, max(0.0, self._away[3] * 0.3)
            self.stir()
        elif shaken or (held[3] < 6 and len(self._clicks) >= _DIZZY_CLICKS):
            self._clicks = []
            self.fire("home.shake")
        elif held[3] < 6:
            self.fire("home.leaf" if held[6] else "home.click")
        elif thrown:
            self.fire("home.throw")
        else:
            self.fire("home.drop")
        event.accept()

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if not self._playful:
            return
        self._clicks.append(time.perf_counter())
        if self._perch is not None:

            if self.on_home is not None:
                self.on_home()
            self.come_back()
            self.fire("home.call")
        else:
            self.fire("home.double")
        event.accept()

    def _adrift(self) -> bool:

        px, py = self._perch or (0.0, 0.0)
        return self._away != [px, py, 0.0, 0.0]

    def _spring_home(self, dt: float) -> None:


        a = self._away
        swing = _clamp(-self._held[5][0] * 0.02, -28.0, 28.0) if self._held is not None else 0.0
        self._swing += (swing - self._swing) * (1.0 - math.exp(-dt / 0.08))
        if abs(self._swing) < 0.05:
            self._swing = 0.0
        px, py = self._perch or (0.0, 0.0)
        if self._held is not None or not self._adrift():
            return
        steps = max(1, math.ceil(dt / 0.009))
        h = dt / steps
        parent, bumped = self.parentWidget(), 0.0
        half = self._size * BOX_W / BOX_H / 2.0
        for _ in range(steps):
            a[2] += (150 * (px - a[0]) - 11 * a[2]) * h
            a[3] += (150 * (py - a[1]) - 11 * a[3]) * h
            a[0] += a[2] * h
            a[1] += a[3] * h
            if parent is None or self._at is None:
                continue

            for i, lo, hi in ((0, half - self._at[0], parent.width() - half - self._at[0]),
                              (1, self._size * 0.6 - self._at[1], parent.height() - self._at[1])):
                if lo <= hi and not lo <= a[i] <= hi:
                    a[i] = _clamp(a[i], lo, hi)
                    bumped = max(bumped, abs(a[i + 2]))
                    a[i + 2] *= -0.55
        if bumped > 250:
            self._s["sy"][1] -= min(2.4, bumped / 600.0)
            self.fire("carry.bump")
        if abs(a[0] - px) + abs(a[1] - py) < 0.4 and abs(a[2]) + abs(a[3]) < 4:
            a[:] = [px, py, 0.0, 0.0]
            if self._far and self._perch is None and self._react is None and self._burst is None:
                self.fire("home.back")
            self._far = False
        if self._at is not None:
            self.put(*self._at)



    def paintEvent(self, event):  # noqa: N802


        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setOpacity(self._alpha)
            self.paint(painter, self._frac[0] + self._size * PAD_X, self._frac[1] + self._size * PAD_TOP)
            painter.end()
        except Exception:  # noqa: BLE001
            return

    def paint(self, p: QPainter, left: float, top: float) -> None:

        s = {k: v[0] for k, v in self._s.items()}
        o = self._pose
        k = self._size / BOX_H
        tiny = self._size * BOX_W / BOX_H < 24
        travel = 1.25 if tiny else 1.0
        gx, gy, sy = s["gx"], s["gy"], s["sy"]
        held = self._held is not None
        lime, navy, white = QColor(_LIME), QColor(_NAVY), QColor("#ffffff")
        none = Qt.PenStyle.NoPen

        p.translate(left, top)
        p.scale(k, k)
        p.translate(s["tx"] + gx * 14, s["lift"] + gy * 5)

        pivot = 150 if held or self._swing else 1010
        p.translate(398, pivot)
        p.rotate(s["roll"] + gx * 3.5 + self._swing)
        p.translate(-398, -pivot)
        p.translate(398, 1024)
        p.scale(2 - sy, sy)
        p.translate(-398, -1024)

        if o.get("zz") and self._size >= 30:
            self._paint_sleep(p, navy)


        open_up = _clamp(-s["lift"] * 0.14 + self._s["stem"][1] * 0.012, -12, 36)
        p.save()
        p.translate(*_APEX)
        p.rotate(s["stem"] - self._swing * 0.6)
        pen = QPen(QColor(_LEAF_DARK), 30)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(self._stem)
        p.setPen(none)
        for paths, pivot_xy, angle, colours in (
                (self._leaf_paths[0], _PIVOT_1, -open_up - o.get("flick", 0.0), (_LEAF, _LEAF_DARK)),
                (self._leaf_paths[1], _PIVOT_2, open_up, (_LEAF_LIGHT, _LEAF))):
            p.save()
            p.translate(*pivot_xy)
            p.rotate(angle)
            p.translate(-pivot_xy[0], -pivot_xy[1])
            p.fillPath(paths[0], QColor(colours[0]))
            p.fillPath(paths[1], QColor(colours[1]))
            p.restore()
        p.restore()


        p.fillPath(self._body, QColor(_CEL))
        p.save()
        p.setClipPath(self._body, Qt.ClipOperation.IntersectClip)
        p.translate(-34, -44)
        p.fillPath(self._body, lime)
        p.restore()

        cheek = _clamp(s["cheek"], 0.0, 1.0)
        turn = self._table["params"]["turn"]
        p.translate(gx * turn[0], gy * turn[1])
        p.setPen(none)
        p.setBrush(QColor(_BLUSH))
        p.setOpacity(self._alpha * (0.25 + 0.75 * cheek))
        for i, (cx, cy) in enumerate(_EYES):
            p.drawEllipse(QPointF(cx + (78 if i else -78), cy + 92), 50, 28)
        p.setOpacity(self._alpha)

        shut = _clamp(max(o.get("blink", 0.0), s["lid"]), 0.0, 1.0)
        closed = _smooth((shut - 0.78) / 0.22)
        grin = _smooth((cheek - 0.72) / 0.28)
        rx, ry = _EYE["rx"], _EYE["ry"]
        line = QPen(navy, 46 if tiny else 28)
        line.setCapStyle(Qt.PenCapStyle.RoundCap)
        for i, (cx, cy) in enumerate(_EYES):
            p.save()
            p.translate(cx, cy)
            p.scale(s["big"], s["big"])
            p.translate(-cx, -cy)
            p.save()
            socket = QPainterPath()
            socket.addEllipse(QPointF(cx, cy), rx + 5, ry + 5)
            p.setClipPath(socket, Qt.ClipOperation.IntersectClip)
            p.setPen(none)
            seen = (1 - closed) * (1 - grin)
            if seen > 0.01:
                p.setOpacity(self._alpha * seen)
                p.setBrush(white)
                p.drawEllipse(QPointF(cx, cy), rx, ry)
                px = cx + gx * _EYE["travel"][0] * travel
                py = cy + gy * _EYE["travel"][1] * travel
                p.setBrush(navy)
                p.drawEllipse(QPointF(px, py), _EYE["pupil"][0], _EYE["pupil"][1])
                p.setBrush(white)
                dx, dy, r = _EYE["hi"]
                p.drawEllipse(QPointF(px + dx, py + dy), r, r)
                p.setOpacity(self._alpha)

            edge = cy - ry - 10 + shut * ry * 1.32
            bow = (-0.16 + 0.58 * shut) * ry
            x0, x1 = cx - rx * 1.2, cx + rx * 1.2
            lid = QPainterPath()
            lid.moveTo(x0, cy - ry * 3)
            lid.lineTo(x0, edge)
            lid.quadTo(cx, edge + 2 * bow, x1, edge)
            lid.lineTo(x1, cy - ry * 3)
            lid.closeSubpath()
            p.save()
            p.translate(cx, cy - ry)
            p.rotate((1 if i else -1) * s["brow"])
            p.translate(-cx, -(cy - ry))
            p.fillPath(lid, lime)
            p.restore()

            p.setBrush(lime)
            p.drawEllipse(QPointF(cx, cy + ry + rx * 2.9 - cheek * ry * 1.75), rx * 2.9, rx * 2.9)
            p.restore()
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(line)
            ex = rx * 0.82

            for amount, y, bend in ((closed * (1 - grin), cy + ry * 0.18, ry * 0.62), (grin, cy + ry * 0.2, -ry * 0.8)):
                if amount > 0.01:
                    arc = QPainterPath()
                    arc.moveTo(cx - ex, y)
                    arc.quadTo(cx, y + bend, cx + ex, y)
                    p.setOpacity(self._alpha * amount)
                    p.drawPath(arc)
                    p.setOpacity(self._alpha)
            p.restore()

    def _paint_sleep(self, p: QPainter, navy: QColor) -> None:

        t = time.perf_counter()
        for i in range(2):
            q = (t * 0.4 + i * 0.5) % 1.0
            half = (90 + q * 110) * 0.22
            x, y = 660 + q * 110, 290 - q * 240
            z = QPainterPath()
            z.moveTo(x - half, y - half)
            z.lineTo(x + half, y - half)
            z.lineTo(x - half, y + half)
            z.lineTo(x + half, y + half)
            pen = QPen(navy, 16)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setOpacity(self._alpha * (math.sin(math.pi * q) * 0.7))
            p.drawPath(z)
        p.setOpacity(self._alpha)


__all__ = ["GAZE", "LIVE", "STILL", "Mascot", "step_event"]
