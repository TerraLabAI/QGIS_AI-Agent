# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later































from __future__ import annotations

import json
import math
import re

VERSION = 1
SPRUNG = ("gx", "gy", "sy", "lift", "roll", "stem", "lid", "brow", "cheek", "big", "tx")
CHANNELS = SPRUNG + ("blink", "flick", "zz")
PLACES = ("head", "home", "card", "question", "signin", "offer")
_FUNCS = ("const", "sin", "cos", "abssin", "hop", "keys", "hold")
_CLOCKS = ("T", "tau", "p")
_TERM_KEYS = {"f", "a", "w", "per", "p", "clock", "decay", "delay", "from", "to", "at", "d", "pts", "loop",
              "env", "side", "calm"}
_NAME = re.compile(r"^[a-z][a-z0-9_.]{0,31}$")
MAX_BYTES = 64_000
_W = 2 * math.pi


def _s(a, w, p=0.0, **kw):
    return dict({"f": "sin", "a": a, "w": w, "p": p}, **kw)


def _c(a, w, p=0.0, **kw):
    return dict({"f": "cos", "a": a, "w": w, "p": p}, **kw)


def _abs(a, w, **kw):
    return dict({"f": "abssin", "a": a, "w": w}, **kw)


def _hop(a, at, d, clock="tau"):
    return {"f": "hop", "a": a, "at": at, "d": d, "clock": clock}


def _k(a, **kw):
    return dict({"f": "const", "a": a}, **kw)


def _pts(f, pts, clock="tau", loop=0.0):
    return {"f": f, "pts": pts, "clock": clock, "loop": loop}


def _env(a):
    return {"f": "const", "a": a, "env": True}


def _rs(a, turns, **kw):

    return dict({"f": "sin", "a": a, "w": _W * turns, "clock": "p", "env": True}, **kw)


DEFAULT = {
    "v": VERSION,
    "enabled": True,

    "sizes": {"head": 24, "home": 40, "card": 40, "question": 26, "signin": 52, "offer": 40},
    "places": {"home": True, "card": True, "question": True, "signin": True, "offer": True},
    "params": {

        "springs": {"gx": [330, 30], "gy": [330, 30], "sy": [230, 13], "lift": [700, 42], "roll": [170, 15],
                    "stem": [120, 7], "lid": [300, 26], "brow": [220, 22], "cheek": [300, 24], "big": [280, 17],
                    "tx": [420, 22]},
        "blink": {"slot": 4.2, "dur": 0.19, "double": 0.2, "gap": 0.34},


        "wander": {"hold": 1.9, "reach": 0.16, "centre": 0.4, "x": 1.2, "y": 0.7, "home": [-0.4, -0.37]},
        "fidget": {"slot": 9.0},
        "attention": {"release": 2.5, "fade": 0.3},
        "tilt": 4.0,


        "turn": [24.0, 16.0],


        "still_gaze": [-0.67, -0.62],


        "voice": {"attack": 0.04, "release": 0.22, "floor": 0.08, "quiet_s": 1.6},
        "travel_ms": 620,


        "leave_ms": 120,
        "arrive_ms": 220,
        "patience_s": 30.0,
        "patience_mood": "wait",
        "settle_after_s": 8.0,
        "sleep_after_s": 300.0,
        "shy_after_s": 0.9,
        "still_s": 1.6,
        "perch_s": 8.0,
    },

    "rest": {"sy": [1, _s(0.014, _W / 3.4)], "lift": [-5, _c(5, _W / 3.4)], "roll": _s(1.6, _W / 5.3, calm=True),
             "stem": [_s(5, _W / 5.3, -1.1), _s(2, _W / 2.3)], "tx": _s(7, _W / 6.1, calm=True)},
    "moods": {
        "idle": {"wander": 1, "fidget": True, "blink": 1, "tilt": True},
        "calm": {"wander": 0.5, "blink": 1, "tilt": True, "set": {"lid": 0.34}, "add": {"stem": 12}},
        "listen": {"blink": 0.6, "tilt": True, "set": {"big": 1.1, "stem": [-12, _s(3, 2)], "lift": -6}},



        "hear": {"blink": 0.5, "set": {"big": 1.06, "stem": [-10, _s(2, 2.4)], "lift": -4, "roll": -3},
                 "voice": {"stem": -38, "sy": 0.06, "lift": -8, "big": 0.08, "cheek": 0.7},
                 "quiet": {"roll": 11, "brow": -5, "stem": 16, "big": -0.03}},
        "think": {"blink": 1, "follow": False, "set": {
            "gx": [0.55, _c(0.3, 1.9)], "gy": [-0.8, _s(0.16, 1.9)], "roll": [-5, _s(1.4, 1.2)], "lid": 0.2,
            "brow": -4, "lift": [-8, _s(-5, 1.2)], "stem": [_s(9, 3.1), _s(4, 5.5)]}},
        "work": {"follow": False, "set": {
            "gx": _pts("keys", [[0, -0.9], [1.075, 0.906], [1.25, -0.9]], "T", 1.25), "gy": 0.28, "lid": 0.22,
            "brow": -10, "lift": _abs(-14, 5.2), "stem": _s(15, 10.4)},
            "around": {"gx": _s(0.25, 5), "gy": 0}},
        "search": {"blink": 1, "follow": False, "set": {
            "gx": _s(0.95, 2.1), "gy": -0.12, "lid": 0.3, "brow": -5, "tx": _s(26, 2.1, -0.5),
            "roll": _s(4, 2.1, -0.5), "stem": _c(-10, 2.1)}},
        "load": {"blink": 1, "follow": False, "set": {
            "gy": 0.75, "big": 1.06, "lift": _abs(16, 3.6), "sy": [1, _abs(-0.05, 3.6)],
            "stem": [-8, _abs(16, 3.6)]}},
        "draw": {"follow": False, "set": {
            "gx": _c(0.55, 3.4), "gy": [0.35, _s(0.32, 3.4)], "roll": _c(4, 3.4), "lid": 0.14,
            "stem": _s(20, 6.8), "lift": -5},
            "around": {"gx": _c(0.3, 3.4), "gy": _s(0.2, 3.4)}},
        "ask": {"blink": 0.5, "set": {
            "gx": 0.75, "gy": 0.45, "big": 1.15, "brow": 7, "lid": 0,
            "lift": [-18, _s(7, 3.2, decay=2, delay=3.5)], "stem": [-15, _s(5, 8, decay=2, delay=3.5)],
            "roll": _k(7, side=True)}},
        "hope": {"set": {"big": 1.18, "cheek": 0.3, "roll": _s(3, 3), "lid": 0, "lift": [-14, _s(5, 6)],
                         "stem": [-20, _s(9, 12)]}},
        "worry": {"set": {"big": 0.96, "brow": 17, "lid": 0.1, "stem": 24, "sy": 0.975, "tx": _s(5, 30)}},
        "happy": {"follow": False, "set": {
            "cheek": _pts("hold", [[0, 1], [1.15, 0.42]]), "lid": 0, "big": 1.05,
            "lift": [_hop(150, 0.13, 0.42), _hop(80, 0.6, 0.34)],
            "roll": _s(4.5, _W / 0.92, -0.13 * _W / 0.92, clock="tau", to=1.05),
            "sy": _pts("hold", [[0, 0.87], [0.13, 1], [0.55, 0.91], [0.63, 1], [0.94, 0.91], [1.02, 1]]),
            "stem": _s(28, 22, clock="tau", decay=1.6)}},
        "error": {"blink": 1, "follow": False, "set": {
            "gx": -0.2, "gy": 0.65, "roll": -8, "lid": 0.3, "brow": 18, "stem": 42, "sy": 0.965}},
        "curious": {"blink": 0.5, "set": {"roll": _k(9, side=True), "big": 1.12, "stem": [-16, _s(4, 3)],
                                          "lift": -8}},
        "read": {"blink": 1, "follow": False, "set": {
            "gx": _pts("keys", [[0, -0.85], [1.5, 0.86], [1.6667, -0.84]], "T", 1.6667),
            "gy": _pts("hold", [[0, 0.05], [1.6667, 0.31], [3.3333, 0.57]], "T", 5.0), "lid": 0.16, "stem": 6}},
        "write": {"follow": False, "set": {"gy": 0.62, "gx": _s(0.35, 7), "lift": _abs(-5, 7), "stem": _s(9, 14),
                                           "lid": 0.14, "roll": 3}},
        "wait": {"blink": 1, "follow": {"period": 6, "on": 1.4}, "set": {
            "roll": _s(3.5, 0.9), "lid": 0.24, "stem": [10, _s(6, 0.9)], "gx": [-0.35, _s(0.1, 0.5)],
            "gy": -0.4}},
        "proud": {"blink": 1, "set": {"sy": 1.04, "lift": -10, "cheek": _pts("hold", [[0, 1], [1.3, 0.45]]),
                                      "big": 1.04, "stem": [-22, _s(7, 9)]}},
        "shy": {"follow": False, "set": {"gx": -0.65, "gy": 0.5, "roll": -6, "lid": 0.22, "cheek": 0.35,
                                         "stem": [20, _s(3, 2)], "sy": 0.98}},
        "dizzy": {"follow": 1.8, "set": {
            "gx": _c(0.85, 9, decay=0.9), "gy": _s(0.85, 9, decay=0.9), "roll": _s(9, 4.5, decay=0.9),
            "stem": _s(32, 4.5, decay=0.9), "lid": _k(0.2, decay=0.9)}},
        "wake": {"set": {
            "lid": _pts("keys", [[0, 1], [0.77, 0]]), "sy": [1, _s(0.1, math.pi / 1.3, clock="tau", to=1.3)],
            "stem": _s(-26, math.pi / 1.3, clock="tau", to=1.3), "lift": _s(-10, math.pi / 1.3, clock="tau", to=1.3),
            "roll": _pts("keys", [[0, 4], [1.3, 0]])}},
        "alert": {"set": {"big": 1.2, "lift": [-4, _k(-16, decay=3)], "stem": [-28, _s(5, 10)], "lid": 0}},
        "sleep": {"follow": False, "set": {"lid": 1, "gy": 0.5, "roll": 4, "stem": 36, "sy": [0.985, _s(0.03, 1.1)],
                                           "zz": 1}},
    },
    "reactions": {
        "nod": {"dur": 0.9, "add": {"gy": _rs(0.9, 2), "lift": _rs(14, 2)}, "max": {"cheek": _env(0.3)}},
        "shake": {"dur": 0.9, "add": {"gx": _rs(1, 2.5), "roll": _rs(7, 2.5)}, "max": {"brow": _env(12)}},
        "wave": {"dur": 1.3, "add": {"stem": _rs(46, 3), "roll": _env(5)}, "max": {"cheek": _env(0.4)}},
        "jump": {"dur": 0.9, "add": {"sy": _k(-0.14, clock="p", to=0.18), "lift": _hop(190, 0.18, 0.7, "p")}},
        "wiggle": {"dur": 0.9, "add": {"roll": _rs(10, 4), "stem": _rs(30, 4)}, "max": {"cheek": _env(0.4)}},
        "duck": {"dur": 0.9, "add": {"sy": _env(-0.2), "lift": _env(40)}, "max": {"lid": _env(0.5)}},
        "stretch": {"dur": 1.2, "add": {"sy": _env(0.14), "stem": _env(-30), "lift": _env(-10)},
                    "max": {"lid": _env(0.7)}},
        "bounce": {"dur": 0.9, "add": {"lift": [_hop(70, 0, 0.45, "p"), _hop(40, 0.5, 0.4, "p")],
                                       "stem": _rs(18, 3)}},
        "poke": {"dur": 0.9, "max": {"blink": _k(1, clock="p", to=0.35)},
                 "kick": {"sy": [0.8, None], "big": [0.86, None], "stem": [None, 600]}},
    },



    "events": {
        "panel.open": {"react": ["wave", 1.3]},
        "panel.back": {"burst": ["wake", 1300]},
        "rest": {"mood": "idle"},
        "compose.typing": {"mood": "listen"},
        "compose.send.hover": {"hover": "hope"},
        "home.row.hover": {"hover": "curious"},
        "run.start": {"mood": "think", "react": ["nod", 0.7]},
        "run.think": {"mood": "think"},
        "run.write": {"mood": "write"},
        "step.search": {"mood": "search"},
        "step.read": {"mood": "read"},
        "step.download": {"mood": "load"},
        "step.map": {"mood": "work"},
        "step.style": {"mood": "draw"},
        "step.other": {"mood": "think"},
        "card.permission": {"mood": "ask"},
        "permission.hover.allow": {"hover": "hope"},
        "permission.hover.deny": {"hover": "worry"},
        "permission.allow": {"react": ["nod", 0.9]},
        "permission.deny": {"react": ["shake", 0.9]},
        "card.question": {"mood": "curious"},
        "question.hover": {"hover": "curious"},
        "question.answer": {"react": ["nod", 0.9]},
        "run.done": {"pick": [{"burst": ["happy", 1500]},
                              {"burst": ["proud", 1600], "react": ["wiggle", 1.0]},
                              {"burst": ["proud", 1600], "react": ["bounce", 1.0]}]},
        "run.stopped": {"burst": ["calm", 2500], "react": ["duck", 0.9]},
        "run.failed": {"burst": ["error", 2500]},
        "error": {"burst": ["error", 2500]},

        "quota.wall": {"mood": "calm"},



        "offer.low": {"mood": "curious"},
        "offer.low.hello": {"react": ["wave", 0.9]},
        "offer.wall.hello": {"react": ["nod", 0.7]},
        "offer.return": {"mood": "curious"},
        "offer.return.hello": {"react": ["nod", 0.7]},
        "offer.welcome": {"mood": "happy"},
        "offer.copied": {"burst": ["happy", 1600], "react": ["bounce", 0.9]},
        "offer.closed": {"react": ["wave", 0.9]},


        "plan.hover": {"hover": "hope"},
        "plan.open": {"react": ["nod", 0.7]},
        "plan.started": {"burst": ["happy", 2400], "react": ["jump", 0.9]},
        "history.hover": {"hover": "curious"},
        "feedback.hover": {"hover": "listen"},
        "chip.hover": {"hover": "curious"},
        "mode.changed": {"react": ["nod", 0.7]},
        "effort.changed": {"react": ["stretch", 1.0]},
        "attach.done": {"react": ["nod", 0.7]},
        "example.picked": {"react": ["bounce", 0.9]},
        "dictate.press": {"burst": ["listen", 1500]},
        "dictate.start": {"mood": "hear", "react": ["stretch", 0.7]},
        "dictate.asking": {"mood": "wait"},
        "dictate.listen": {"mood": "hear"},
        "dictate.transcribe": {"mood": "think"},
        "dictate.text": {"react": ["nod", 0.8], "burst": ["happy", 1000]},
        "dictate.failed": {"burst": ["worry", 1600]},
        "rate.up": {"burst": ["proud", 1400]},
        "rate.down": {"react": ["nod", 0.9]},
        "feedback.sent": {"react": ["nod", 0.9]},
        "undo": {"burst": ["calm", 1400]},
        "signout": {"react": ["wave", 1.3]},
        "signin.hover": {"hover": "hope"},
        "signin.wait": {"mood": "wait"},
        "signin.success": {"burst": ["happy", 1500]},
        "drop.enter": {"hover": "alert"},
        "drop.done": {"react": ["nod", 0.9]},
        "sleep": {"mood": "sleep"},
        "wake": {"burst": ["wake", 1300]},
        "head.click": {"react": ["poke", 0.9]},
        "home.click": {"pick": [{"react": ["poke", 0.9]}, {"react": ["wiggle", 0.9]}, {"react": ["bounce", 0.9]},
                                {"react": ["wave", 1.3]}, {"react": ["nod", 0.9]}, {"react": ["stretch", 1.2]}]},
        "home.leaf": {"react": ["wave", 1.3]},
        "home.double": {"react": ["jump", 0.9], "burst": ["hope", 900]},
        "home.grab": {"hover": "hope"},
        "home.shake": {"burst": ["dizzy", 2200]},
        "home.drop": {"react": ["bounce", 0.9]},
        "home.throw": {"react": ["duck", 0.7], "burst": ["alert", 700]},
        "home.pet": {"burst": ["proud", 1600]},


        "carry.high": {"hover": "alert"},
        "carry.still": {"hover": "shy"},
        "carry.bump": {"react": ["duck", 0.5]},
        "home.back": {"pick": [{"react": ["nod", 0.8]}, {"react": ["wiggle", 0.8]}, {"react": ["stretch", 0.9]}]},
        "home.call": {"react": ["jump", 0.9], "burst": ["happy", 900]},
        "drop.again": {"pick": [{"react": ["wiggle", 0.9]}, {"react": ["jump", 0.9]}, {"react": ["wave", 1.1]},
                                {"burst": ["proud", 1200]}]},
        "carry.over": {"hover": "curious"},
        "carry.over.send": {"hover": "hope"},
        "carry.over.plan": {"hover": "hope"},
        "carry.over.composer": {"hover": "listen"},
        "drop.on": {"react": ["bounce", 0.9]},
        "drop.on.composer": {"burst": ["listen", 1400], "react": ["bounce", 0.9]},
        "drop.on.send": {"burst": ["hope", 1400], "react": ["bounce", 0.9]},
        "drop.on.plan": {"burst": ["hope", 1400], "react": ["bounce", 0.9]},
        "drop.on.data": {"burst": ["curious", 1400], "react": ["bounce", 0.9]},
        "drop.on.examples": {"burst": ["curious", 1400], "react": ["bounce", 0.9]},
        "drop.on.tutorial": {"burst": ["read", 1400], "react": ["bounce", 0.9]},
        "drop.on.mic": {"burst": ["listen", 1400], "react": ["bounce", 0.9]},
        "pointer.rest": {"burst": ["shy", 1800]},
    },
}





def _num(value, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("not a number")
    if not lo <= value <= hi:
        raise ValueError("number out of range")
    return float(value)


def _name(value) -> str:
    if not isinstance(value, str) or not _NAME.match(value):
        raise ValueError("bad name")
    return value


def _turn(value) -> list:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("turn is a pair")
    return [_num(v, 0, 60) for v in value]


def _gaze_pair(value) -> list:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("a gaze is a pair")
    return [_num(v, -1, 1) for v in value]


def _dict(value, limit: int = 96) -> dict:
    if not isinstance(value, dict) or len(value) > limit:
        raise ValueError("not a table")
    return value


def _term(term, reaction: bool) -> dict:
    if not isinstance(term, dict):
        term = {"f": "const", "a": term}
    if set(term) - _TERM_KEYS or term.get("f") not in _FUNCS:
        raise ValueError("unknown term")
    clock = term.get("clock", "T" if term["f"] in ("sin", "cos", "abssin") else "tau")
    if clock not in _CLOCKS or (clock == "p" and not reaction):
        raise ValueError("bad clock")
    out = {"f": term["f"], "a": _num(term.get("a", 1), -1e4, 1e4), "clock": clock,
           "p": _num(term.get("p", 0), -1e3, 1e3), "decay": _num(term.get("decay", 0), 0, 100),
           "delay": _num(term.get("delay", 0), 0, 600), "from": _num(term.get("from", -1e9), -1e9, 1e4),
           "to": _num(term.get("to", 1e9), -1e4, 1e9), "at": _num(term.get("at", 0), -10, 600),
           "d": _num(term.get("d", 1), 0.01, 600), "loop": _num(term.get("loop", 0), 0, 3600)}
    out["w"] = _W / _num(term["per"], 0.02, 3600) if "per" in term else _num(term.get("w", 0), -400, 400)
    for flag in ("env", "side", "calm"):
        if not isinstance(term.get(flag, False), bool):
            raise ValueError("bad flag")
        out[flag] = term.get(flag, False)
    if out["env"] and not reaction:
        raise ValueError("env outside a reaction")
    if term["f"] in ("keys", "hold"):
        pts = term.get("pts")
        if not isinstance(pts, list) or not 1 <= len(pts) <= 24:
            raise ValueError("bad pts")
        out["pts"] = [(_num(t, -10, 3600), _num(v, -1e4, 1e4)) for t, v in pts]
        if any(b[0] < a[0] for a, b in zip(out["pts"], out["pts"][1:])):
            raise ValueError("pts not in order")
    return out


def _value(value, reaction: bool = False) -> list:
    terms = value if isinstance(value, list) else [value]
    if not 1 <= len(terms) <= 8:
        raise ValueError("too many terms")
    return [_term(t, reaction) for t in terms]


def _channels(value, reaction: bool = False, allowed=CHANNELS) -> dict:
    out = {}
    for channel, terms in _dict(value, len(CHANNELS)).items():
        if channel not in allowed:
            raise ValueError("unknown channel")
        out[channel] = _value(terms, reaction)
    return out


def _mood(mood) -> dict:
    mood = _dict(mood, 12)
    if set(mood) - {"set", "add", "around", "follow", "blink", "wander", "fidget", "tilt", "voice", "quiet"}:
        raise ValueError("unknown mood key")
    follow = mood.get("follow", True)
    if isinstance(follow, dict):
        follow = {"period": _num(follow.get("period"), 0.1, 600), "on": _num(follow.get("on"), 0, 600)}
    elif not isinstance(follow, bool):
        follow = _num(follow, 0, 600)
    for flag in ("fidget", "tilt"):
        if not isinstance(mood.get(flag, False), bool):
            raise ValueError("bad flag")
    return {"set": _channels(mood.get("set", {})), "add": _channels(mood.get("add", {})),
            "around": _channels(mood.get("around", {}), allowed=("gx", "gy")), "follow": follow,
            "blink": _num(mood.get("blink", 0), 0, 1), "wander": _num(mood.get("wander", 0), 0, 1),
            "fidget": mood.get("fidget", False), "tilt": mood.get("tilt", False),
            "voice": _gains(mood.get("voice", {})), "quiet": _gains(mood.get("quiet", {}))}


def _gains(value) -> dict:

    gains = {}
    for channel, gain in _dict(value, len(SPRUNG)).items():
        if channel not in SPRUNG:
            raise ValueError("unknown channel")
        gains[channel] = _num(gain, -200, 200)
    return gains


def _reaction(reaction) -> dict:
    reaction = _dict(reaction, 8)
    if set(reaction) - {"dur", "add", "max", "kick"}:
        raise ValueError("unknown reaction key")
    kick = {}
    for channel, pair in _dict(reaction.get("kick", {}), len(SPRUNG)).items():
        if channel not in SPRUNG or not isinstance(pair, list) or len(pair) != 2:
            raise ValueError("bad kick")
        kick[channel] = tuple(None if x is None else _num(x, -5000, 5000) for x in pair)
    return {"dur": _num(reaction.get("dur", 0.9), 0.1, 10), "add": _channels(reaction.get("add", {}), True),
            "max": _channels(reaction.get("max", {}), True), "kick": kick}


def _action(action, moods, reactions, deep: bool = True) -> dict:
    action = _dict(action, 6)
    if set(action) - {"mood", "hover", "burst", "react", "pick"}:
        raise ValueError("unknown action key")
    out = {}
    for key in ("mood", "hover"):
        if key in action:
            if action[key] not in moods:
                raise ValueError("unknown mood")
            out[key] = action[key]
    if "burst" in action:
        mood, ms = action["burst"]
        if mood not in moods:
            raise ValueError("unknown mood")
        out["burst"] = (mood, _num(ms, 100, 10_000))
    if "react" in action:
        name, seconds = action["react"]
        if name not in reactions:
            raise ValueError("unknown reaction")
        out["react"] = (name, _num(seconds, 0.1, 10))
    if "pick" in action:
        picks = action["pick"]
        if not deep or not isinstance(picks, list) or not 1 <= len(picks) <= 12:
            raise ValueError("bad pick")
        out["pick"] = [_action(p, moods, reactions, deep=False) for p in picks]
    return out


def _params(params) -> dict:
    params = _dict(params, 24)
    springs = {}
    for channel, pair in _dict(params["springs"], len(SPRUNG)).items():
        if channel not in SPRUNG or not isinstance(pair, list) or len(pair) != 2:
            raise ValueError("bad spring")

        springs[channel] = (_num(pair[0], 20, 2000), _num(pair[1], 2, 200))
    if set(springs) != set(SPRUNG):
        raise ValueError("a spring is missing")
    blink, wander, attention = _dict(params["blink"]), _dict(params["wander"]), _dict(params["attention"])
    voice = _dict(params["voice"])
    return {
        "springs": springs,
        "blink": {"slot": _num(blink["slot"], 1, 60), "dur": _num(blink["dur"], 0.05, 1),
                  "double": _num(blink["double"], 0, 1), "gap": _num(blink["gap"], 0.1, 2)},
        "wander": {"hold": _num(wander["hold"], 0.3, 60), "reach": _num(wander["reach"], 0.02, 5),
                   "centre": _num(wander["centre"], 0, 1), "x": _num(wander["x"], 0, 2), "y": _num(wander["y"], 0, 2),
                   "home": _gaze_pair(wander["home"])},
        "fidget": {"slot": _num(_dict(params["fidget"])["slot"], 3, 600)},
        "attention": {"release": _num(attention["release"], 0.2, 60), "fade": _num(attention["fade"], 0.02, 5)},
        "tilt": _num(params["tilt"], -15, 15),
        "turn": _turn(params["turn"]),
        "still_gaze": _gaze_pair(params["still_gaze"]),
        "voice": {"attack": _num(voice["attack"], 0.005, 5), "release": _num(voice["release"], 0.005, 5),
                  "floor": _num(voice["floor"], 0, 1), "quiet_s": _num(voice["quiet_s"], 0.2, 600)},
        "travel_ms": _num(params["travel_ms"], 0, 3000),
        "leave_ms": _num(params["leave_ms"], 0, 3000),
        "arrive_ms": _num(params["arrive_ms"], 0, 3000),
        "patience_s": _num(params["patience_s"], 1, 3600),
        "patience_mood": _name(params["patience_mood"]),
        "settle_after_s": _num(params["settle_after_s"], 1, 86_400),
        "sleep_after_s": _num(params["sleep_after_s"], 10, 86_400),
        "shy_after_s": _num(params["shy_after_s"], 0.2, 60),
        "still_s": _num(params["still_s"], 0.3, 60),
        "perch_s": _num(params["perch_s"], 0.5, 600),
    }


def _checked(table) -> dict:

    table = _dict(table, 12)
    if table.get("v") != VERSION or not isinstance(table.get("enabled"), bool):
        raise ValueError("another version")
    moods = {_name(k): _mood(v) for k, v in _dict(table["moods"]).items()}
    reactions = {_name(k): _reaction(v) for k, v in _dict(table["reactions"]).items()}
    params = _params(table["params"])
    if "idle" not in moods or params["patience_mood"] not in moods:
        raise ValueError("a needed mood is missing")
    sizes = {place: _num(_dict(table["sizes"]).get(place), 12, 160) for place in PLACES}
    places = {}
    for place, on in _dict(table["places"]).items():
        if place not in PLACES or not isinstance(on, bool):
            raise ValueError("bad place")
        places[place] = on
    return {"v": VERSION, "enabled": table["enabled"], "sizes": sizes, "places": places, "params": params,
            "rest": _channels(table["rest"], allowed=SPRUNG), "moods": moods, "reactions": reactions,
            "events": {_name(k): _action(v, moods, reactions) for k, v in _dict(table["events"], 200).items()}}


BUILT_IN = _checked(DEFAULT)


def merged(served) -> dict | None:




    try:
        if not isinstance(served, dict) or served.get("v") != VERSION:
            return None
        if len(json.dumps(served)) > MAX_BYTES:
            return None
        table = dict(DEFAULT)
        for key, value in served.items():
            if key in ("v", "enabled"):
                table[key] = value
            elif key == "params":
                params = dict(DEFAULT["params"])
                for name, part in _dict(value, 24).items():
                    if name not in params:
                        raise ValueError("unknown param")
                    params[name] = dict(params[name], **part) if isinstance(params[name], dict) else part
                table["params"] = params
            elif key in ("moods", "reactions", "events", "sizes", "places", "rest"):
                table[key] = dict(DEFAULT[key], **_dict(value, 200))

        return _checked(table)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        return None





def table_for(served) -> dict:


    return merged(served) or BUILT_IN


def _hash(n: float) -> float:
    x = math.sin(n * 127.1) * 43758.5453
    return x - math.floor(x)


def _smooth(t: float) -> float:
    x = min(1.0, max(0.0, t))
    return x * x * (3.0 - 2.0 * x)


def _eval(terms, big_t: float, tau: float, p: float, side: float, calm: float) -> float:
    total = 0.0
    for term in terms:
        f = term["f"]
        t = big_t if term["clock"] == "T" else p if term["clock"] == "p" else tau
        if not term["from"] <= t < term["to"]:
            continue
        a = term["a"]
        if f == "sin":
            v = a * math.sin(term["w"] * t + term["p"])
        elif f == "cos":
            v = a * math.cos(term["w"] * t + term["p"])
        elif f == "abssin":
            v = a * abs(math.sin(term["w"] * t + term["p"]))
        elif f == "hop":
            u = (t - term["at"]) / term["d"]
            v = -a * 4.0 * u * (1.0 - u) if 0.0 < u < 1.0 else 0.0
        elif f == "const":
            v = a
        else:
            pts = term["pts"]
            if term["loop"]:
                t = t % term["loop"]
            v = pts[0][1]
            for i, (at, value) in enumerate(pts):
                if t < at:
                    break
                v = value
                if f == "keys" and i + 1 < len(pts) and pts[i + 1][0] > at:
                    v = value + (pts[i + 1][1] - value) * min(1.0, (t - at) / (pts[i + 1][0] - at))
            v *= a
        if term["decay"]:
            v *= math.exp(-term["decay"] * max(0.0, tau - term["delay"]))
        if term["env"]:
            v *= math.sin(math.pi * p)
        if term["side"]:
            v *= side
        if term["calm"]:
            v *= calm
        total += v
    return total


def next_blink(table: dict, big_t: float, seed: float = 0.0) -> float:

    b = table["params"]["blink"]
    slot = math.floor(big_t / b["slot"])
    for n in (slot, slot + 1):
        start = n * b["slot"] + 0.5 + _hash(n + seed) * (b["slot"] - 1.3)
        if start > big_t:
            return start - big_t
    return b["slot"]


def pose(table: dict, mood: str, big_t: float, tau: float, gaze, attention: float, rest_lid: float = 0.0,
         seed: float = 0.0, since_switch: float = 1e9, reaction=None, still: bool = False,
         settled: bool = False, voice=None) -> dict:







    m = table["moods"].get(mood) or table["moods"]["idle"]
    params = table["params"]
    side = -1.0 if gaze and gaze[0] < 0 else 1.0
    calm = 1.0 - 0.7 * attention
    blinks, still = not still, still or settled
    t_body, tau_body = (0.0, 30.0) if still else (big_t, tau)
    o = dict.fromkeys(CHANNELS, 0.0)
    o["sy"] = o["big"] = 1.0
    o["lid"] = rest_lid
    if not still:
        for channel, terms in table["rest"].items():
            o[channel] = _eval(terms, big_t, tau, 0.0, side, calm)


    b = params["blink"]
    slot = math.floor(big_t / b["slot"])
    u = big_t - slot * b["slot"] - (0.5 + _hash(slot + seed) * (b["slot"] - 1.3))

    def lid_shut(x: float) -> float:
        return math.sin(math.pi * x / b["dur"]) if 0.0 < x < b["dur"] else 0.0

    blink = max(lid_shut(u), lid_shut(u - b["gap"]) if _hash(slot * 7 + seed) > 1.0 - b["double"] else 0.0,
                lid_shut(since_switch))

    if m["wander"] and not still:

        w = params["wander"]

        hx, hy = w["home"]

        def spot(n: float):
            if _hash(n * 3.7 + seed) < w["centre"]:
                return hx, hy
            return (max(-1.0, min(1.0, hx + (_hash(n + 0.1 + seed) - 0.5) * w["x"])),
                    max(-1.0, min(1.0, hy + (_hash(n + 0.2 + seed) - 0.5) * w["y"])))

        n = math.floor(big_t / w["hold"])
        k = _smooth((big_t - n * w["hold"]) / w["reach"])
        (x0, y0), (x1, y1) = spot(n - 1), spot(n)
        o["gx"] = (x0 + (x1 - x0) * k) * m["wander"]
        o["gy"] = (y0 + (y1 - y0) * k) * m["wander"]
    elif m["wander"]:

        o["gx"], o["gy"] = (v * m["wander"] for v in params["wander"]["home"])
    if m["fidget"] and not still and attention < 0.5:

        period = params["fidget"]["slot"]
        n = math.floor(big_t / period)
        au = big_t - n * period - 2.0 - _hash(n * 5 + seed) * (period - 4.0)
        if 0.0 < au < 1.4:
            e = math.sin(math.pi * au / 1.4)
            kind = math.floor(_hash(n * 11 + seed) * 3)
            if kind == 0:
                o["flick"] = 22 * math.sin(_W * au / 0.25) * (1 - au / 0.5) if au < 0.5 else 0.0
            elif kind == 1:
                o["sy"] += 0.035 * e
                o["lift"] -= 8 * e
                o["lid"] = max(o["lid"], 0.3 * e)
            else:
                o["gx"] = 0.25 * e + o["gx"] * (1 - e)
                o["gy"] = -0.85 * e + o["gy"] * (1 - e)
                o["stem"] += 9 * math.sin(_W * au / 0.7) * e

    for channel, terms in m["set"].items():
        o[channel] = _eval(terms, t_body, tau_body, 0.0, side, calm)
    for channel, terms in m["add"].items():
        o[channel] += _eval(terms, t_body, tau_body, 0.0, side, calm)
    if voice and not still:
        for channel, gain in m["voice"].items():
            o[channel] += gain * voice[0]
        for channel, gain in m["quiet"].items():
            o[channel] += gain * voice[1]
    o["blink"] = blink * m["blink"] if blinks else 0.0
    if m["tilt"]:
        o["roll"] += params["tilt"]

    follow = m["follow"]
    if isinstance(follow, dict):
        follow = (big_t % follow["period"]) < follow["on"]
        if follow and not gaze:
            o["gx"] = o["gy"] = 0.0
    elif not isinstance(follow, bool):
        follow = tau >= follow
    if gaze and m["around"]:
        o["gx"] = gaze[0] + _eval(m["around"].get("gx", ()), t_body, tau_body, 0.0, side, calm)
        o["gy"] = gaze[1] + _eval(m["around"].get("gy", ()), t_body, tau_body, 0.0, side, calm)
    elif gaze and follow:
        o["gx"] += (gaze[0] - o["gx"]) * attention
        o["gy"] += (gaze[1] - o["gy"]) * attention

    if reaction and not still:
        r = table["reactions"].get(reaction[0])
        p = reaction[1]
        if r and 0.0 <= p < 1.0:
            for channel, terms in r["add"].items():
                o[channel] += _eval(terms, big_t, tau, p, side, calm)
            for channel, terms in r["max"].items():
                o[channel] = max(o[channel], _eval(terms, big_t, tau, p, side, calm))
    return o


__all__ = ["BUILT_IN", "CHANNELS", "DEFAULT", "MAX_BYTES", "PLACES", "SPRUNG", "VERSION", "merged", "pose"]
