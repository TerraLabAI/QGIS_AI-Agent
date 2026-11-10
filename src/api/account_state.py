# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
























from __future__ import annotations

SIGNED_OUT = "signed_out"
PAIRING = "pairing"
ACTIVATED = "activated"
LOCKED = "locked"
EXPIRED = "expired"
STATES = (SIGNED_OUT, PAIRING, ACTIVATED, LOCKED, EXPIRED)



NO_KEY, KEY, KEY_LOCKED, KEY_REFUSED = "none", "key", "locked", "refused"

PAIR_START = "pair_start"
PAIR_OK = "pair_ok"
PAIR_END = "pair_end"
SIBLING_KEY = "sibling_key"
KEY_OK = "key_ok"
KEY_DEAD = "key_dead"
KEY_GONE = "key_gone"
LOCK = "lock"
UNLOCK = "unlock"
SIGN_OUT = "sign_out"

_REST = "rest"
_EVERY = STATES


_MOVES: dict[str, dict[str, str]] = {
    PAIR_START: dict.fromkeys(_EVERY, PAIRING),

    PAIR_OK: dict.fromkeys(_EVERY, ACTIVATED),
    PAIR_END: {PAIRING: _REST},
    SIBLING_KEY: {SIGNED_OUT: ACTIVATED},


    KEY_OK: {SIGNED_OUT: ACTIVATED, EXPIRED: ACTIVATED, LOCKED: ACTIVATED, ACTIVATED: ACTIVATED,
             PAIRING: PAIRING},
    KEY_DEAD: {SIGNED_OUT: EXPIRED, EXPIRED: EXPIRED, LOCKED: EXPIRED, ACTIVATED: EXPIRED,
               PAIRING: PAIRING},
    KEY_GONE: {ACTIVATED: SIGNED_OUT, LOCKED: SIGNED_OUT, SIGNED_OUT: SIGNED_OUT, EXPIRED: EXPIRED,
               PAIRING: PAIRING},
    LOCK: {SIGNED_OUT: LOCKED, EXPIRED: LOCKED, ACTIVATED: LOCKED, LOCKED: LOCKED, PAIRING: PAIRING},
    UNLOCK: {LOCKED: ACTIVATED},
    SIGN_OUT: dict.fromkeys(_EVERY, SIGNED_OUT),
}


_NEEDS = {ACTIVATED: KEY, LOCKED: KEY_LOCKED, SIGNED_OUT: NO_KEY, EXPIRED: KEY_REFUSED}


def resting(stored: str) -> str:

    return {KEY: ACTIVATED, KEY_LOCKED: LOCKED, KEY_REFUSED: EXPIRED}.get(stored, SIGNED_OUT)


def step(state: str, event: str, stored: str) -> str | None:







    after = _MOVES.get(event, {}).get(state)
    if after is None:
        return None
    if after == _REST:
        return resting(stored)
    if _NEEDS.get(after, stored) != stored:
        return None
    return after
