# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
















from __future__ import annotations




_DEFAULTS = {
    "pro": True,
    "step_cap": 0,
    "memory": True,


    "yolo": True,





    "efforts": ["low", "medium", "high"],
    "effort_default": "low",

    "efforts_trial_locked": [],

    "efforts_trial_note": "",
}

_MAX_TRIAL_NOTE_CHARS = 200

_features: dict = dict(_DEFAULTS)







_said: set = set()

def effort_name(value) -> str:





    name = str(value or "").strip().lower()
    return name if name in _DEFAULTS["efforts"] else ""


def set_plan_features(raw) -> None:

    global _features, _said
    if not isinstance(raw, dict) or not raw:
        _features = dict(_DEFAULTS)
        _said = set()
        return
    out = dict(_DEFAULTS)
    said = set()
    for key in ("pro", "memory", "yolo"):
        if key in raw:
            out[key] = bool(raw[key])
            said.add(key)
    try:
        out["step_cap"] = max(0, int(raw.get("step_cap") or 0))
    except (TypeError, ValueError):
        out["step_cap"] = 0
    efforts = raw.get("efforts")
    if isinstance(efforts, (list, tuple)):
        kept: list = []
        for item in efforts:
            name = effort_name(item)
            if name and name not in kept:
                kept.append(name)
        if kept:
            out["efforts"] = kept
            said.add("efforts")
    default = effort_name(raw.get("effort_default"))
    if default and default in out["efforts"]:
        out["effort_default"] = default
    trial = raw.get("efforts_trial_locked")
    if isinstance(trial, (list, tuple)):
        out["efforts_trial_locked"] = [n for n in (effort_name(i) for i in trial) if n]
    note = raw.get("efforts_trial_note")
    if isinstance(note, str):
        out["efforts_trial_note"] = note.strip()[:_MAX_TRIAL_NOTE_CHARS]
    _features = out
    _said = said


def plan_features() -> dict:

    return dict(_features)


def autopilot_allowed() -> bool:
    return bool(_features.get("yolo", True))


def memory_allowed() -> bool:
    return bool(_features.get("memory", True))


def step_cap() -> int:

    return int(_features.get("step_cap") or 0)


def efforts_allowed() -> tuple:

    return tuple(_features.get("efforts") or ("low", "medium", "high"))


def efforts_trial_locked() -> tuple:

    return tuple(_features.get("efforts_trial_locked") or ())


def efforts_trial_note() -> str:

    return str(_features.get("efforts_trial_note") or "")


def effort_allowed(effort: str) -> bool:
    return effort in efforts_allowed()


def effort_default() -> str:








    value = _features.get("effort_default")
    allowed = efforts_allowed()
    if isinstance(value, str) and value in allowed:
        return value
    return allowed[0] if allowed else "low"


def stated(key: str) -> bool:










    return key in _said


def efforts_stated() -> bool:
    return stated("efforts")
