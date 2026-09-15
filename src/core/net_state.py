# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The cancel check a run installs for the thread that runs one of its calls."""

from __future__ import annotations

import threading




_LOCAL = threading.local()


def set_cancel_check(check) -> None:
    """Install (or clear, with None) the cancel check for this thread."""
    _LOCAL.cancel = check


def current_cancel_check():
    return getattr(_LOCAL, "cancel", None)


def _cancelled(cancel) -> bool:
    check = cancel or current_cancel_check()
    if check is None:
        return False
    try:
        return bool(check())
    except Exception:  # noqa: BLE001 - a broken check never stops a fetch
        return False
