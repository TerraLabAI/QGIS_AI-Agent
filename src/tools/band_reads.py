# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later













from __future__ import annotations

import contextlib

_READY: dict = {}
_ACTIVE: list = []


def hand_over(layer_id: str, answers: dict) -> None:

    _READY.clear()
    _READY[layer_id] = answers


@contextlib.contextmanager
def answers_for(layer):

    _ACTIVE.append(_READY.pop(layer.id(), {}) if layer is not None else {})
    try:
        yield
    finally:
        _ACTIVE.pop()


def read_ahead(key: tuple, compute):

    answers = _ACTIVE[-1] if _ACTIVE else {}
    return answers[key] if key in answers else compute()
