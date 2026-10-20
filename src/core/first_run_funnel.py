# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later























from __future__ import annotations

import re
import time

SCHEMA = 1

SEND_DELAY_S = 20.0
CODES_MAX = 6
CODE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
_STATES = frozenset({"connecting", "online", "offline", "signed_out", "error"})
_LEN_CAP = 100_000

_FIRST_RUN_KEY = "AIAgent/telemetry_first_run_sent"

_sender = None
_loaded_at = time.monotonic()
_state: dict = {}
_visible_since: float | None = None
_has_text = False
_dirty = False
_timer_armed = False
_stopped = False


def _reset() -> None:
    global _state, _visible_since, _has_text, _dirty, _timer_armed, _stopped
    _state = {"shown": 0, "visible_s": 0.0, "focus": 0, "typed": 0, "cleared": 0, "max_len": 0,
              "send_pressed": 0, "notice_shown": 0, "notice_declined": 0, "banners": [],
              "signed_in": None, "signed_out_seen": False, "conn": ""}
    _visible_since = None
    _has_text = False
    _dirty = False
    _timer_armed = False
    _stopped = False


_reset()


def set_sender(sender) -> None:

    global _sender
    _sender = sender


def _active() -> bool:

    global _stopped
    if _stopped:
        return False
    try:
        from .privacy_notice import has_accepted_privacy_notice
        from .settings import profile_settings
        from .telemetry import is_telemetry_enabled

        if not is_telemetry_enabled():
            return False
        if has_accepted_privacy_notice() or profile_settings().value(_FIRST_RUN_KEY, False, type=bool):
            _stopped = True
            return False
        return True
    except Exception:  # nosec B110
        return False


def _touch() -> None:

    global _dirty, _timer_armed
    _dirty = True
    if _timer_armed:
        return
    try:
        from qgis.PyQt.QtCore import QTimer

        _timer_armed = True
        QTimer.singleShot(int(SEND_DELAY_S * 1000), _on_timer)
    except Exception:  # nosec B110
        _timer_armed = False


def _on_timer() -> None:
    global _timer_armed
    _timer_armed = False
    send("timer")





def note_panel(visible: bool) -> None:
    global _visible_since
    if not _active():
        return
    now = time.monotonic()
    if visible and _visible_since is None:
        _visible_since = now
        _state["shown"] += 1
        _touch()
    elif not visible and _visible_since is not None:
        _state["visible_s"] += now - _visible_since
        _visible_since = None
        send("hidden")


def note_focus() -> None:
    if _active():
        _state["focus"] += 1
        _touch()


def note_text(length: int) -> None:

    global _has_text
    try:
        length = max(0, min(int(length), _LEN_CAP))
    except (TypeError, ValueError):
        return
    moved = (length > 0) != _has_text
    if not moved:

        if length > _state["max_len"] and not _stopped:
            _state["max_len"] = length
        return
    if not _active():

        _has_text = length > 0
        return
    _has_text = length > 0
    _state["typed" if _has_text else "cleared"] += 1
    _state["max_len"] = max(_state["max_len"], length)
    _touch()


def note_banner(code: str) -> None:

    code = str(code or "")
    if not CODE_RE.match(code) or not _active():
        return
    banners = _state["banners"]
    if code not in banners and len(banners) < CODES_MAX:
        banners.append(code)
        _touch()


def note_send_pressed() -> None:
    if _active():
        _state["send_pressed"] += 1
        _touch()


def note_notice(declined: bool) -> None:
    if _active():
        _state["notice_declined" if declined else "notice_shown"] += 1
        _touch()


def note_connection(state: str) -> None:
    state = str(state or "").strip().lower()
    if state not in _STATES or not _active():
        return
    _state["conn"] = state
    if state == "signed_out":
        _state["signed_in"] = False
        _state["signed_out_seen"] = True
    elif state == "online":
        _state["signed_in"] = True
    _touch()





def snapshot(why: str) -> dict:
    visible_s = _state["visible_s"]
    if _visible_since is not None:
        visible_s += time.monotonic() - _visible_since
    out = {key: value for key, value in _state.items() if key not in ("visible_s", "banners", "signed_in")}
    out.update({
        "v": SCHEMA, "why": why, "visible_s": int(visible_s), "banners": list(_state["banners"]),
        "since_load_s": int(time.monotonic() - _loaded_at),
    })
    if _state["signed_in"] is not None:
        out["signed_in"] = bool(_state["signed_in"])
    return out


def send(why: str) -> bool:

    global _dirty
    if not _dirty or _sender is None or not _active():
        return False
    try:
        sent = bool(_sender(snapshot(why)))
    except Exception:  # nosec B110
        return False
    if sent:
        _dirty = False
    return sent


def shutdown() -> None:

    global _visible_since, _dirty
    if _active():
        if _visible_since is not None:
            _state["visible_s"] += time.monotonic() - _visible_since
            _visible_since = None
        _dirty = True
        send("unload")
    set_sender(None)

