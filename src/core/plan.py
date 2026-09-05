# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the account's plan allows, as the session frame described it."""















from __future__ import annotations




_DEFAULTS = {
    "pro": True,
    "step_cap": 0,
    "memory": True,


    "yolo": True,





    "efforts": ["low", "medium", "high"],
    "effort_default": "low",
}

_features: dict = dict(_DEFAULTS)







_said: set = set()









_LEGACY_EFFORTS = {"instant": "low"}


def effort_name(value) -> str:
    """One effort level as this build spells it, or "" for a word it does not know."""





    name = str(value or "").strip().lower()
    name = _LEGACY_EFFORTS.get(name, name)
    return name if name in _DEFAULTS["efforts"] else ""


def set_plan_features(raw) -> None:
    """Store the session frame's ``plan_features``. Anything odd leaves the defaults."""
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
    _features = out
    _said = said


def plan_features() -> dict:
    """A copy of what the server said this account may use."""
    return dict(_features)


def is_pro() -> bool:
    return bool(_features.get("pro", True))


def autopilot_allowed() -> bool:
    return bool(_features.get("yolo", True))


def memory_allowed() -> bool:
    return bool(_features.get("memory", True))


def step_cap() -> int:
    """The steps a run stops at, or 0 when the server never said."""
    return int(_features.get("step_cap") or 0)


def efforts_allowed() -> tuple:
    """The slider stops this account may keep, in order."""
    return tuple(_features.get("efforts") or ("low", "medium", "high"))


def effort_allowed(effort: str) -> bool:
    return effort in efforts_allowed()


def effort_default() -> str:
    """Where a fresh install lands: Low on a free account, Medium on a paid one."""







    value = _features.get("effort_default")
    allowed = efforts_allowed()
    if isinstance(value, str) and value in allowed:
        return value
    return allowed[0] if allowed else "low"


def stated(key: str) -> bool:
    """True when the last session frame named this feature itself."""









    return key in _said


def efforts_stated() -> bool:
    return stated("efforts")
