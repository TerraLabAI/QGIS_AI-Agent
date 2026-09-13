# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


from __future__ import annotations

import threading




_LOCAL = threading.local()


def set_cancel_check(check) -> None:

    _LOCAL.cancel = check


def current_cancel_check():
    return getattr(_LOCAL, "cancel", None)


def _cancelled(cancel) -> bool:
    check = cancel or current_cancel_check()
    if check is None:
        return False
    try:
        return bool(check())
    except Exception:  # noqa: BLE001
        return False
