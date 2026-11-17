# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



























from __future__ import annotations

from .numbers import as_int
from .settings import profile_settings

PRIVACY_NOTICE_VERSION = 3












_served_version = 0


def set_served_notice_version(version: int) -> None:

    global _served_version, _accepted_memo
    value = as_int(version)
    if value is None:
        return
    if value > _served_version:
        _served_version = value
        _accepted_memo = None


def current_notice_version() -> int:

    return max(PRIVACY_NOTICE_VERSION, _served_version)




PRIVACY_NOTICE_KEY = "AIAgent/privacy_notice_accepted_version"





_accepted_memo: bool | None = None


def _read_version(settings) -> int:
    try:
        return int(settings.value(PRIVACY_NOTICE_KEY, 0, type=int))
    except Exception:  # nosec B110
        return 0


def has_accepted_privacy_notice(settings=None) -> bool:



    global _accepted_memo
    if settings is not None:
        return _read_version(settings) >= current_notice_version()
    if _accepted_memo is None:
        try:
            from .settings import profile_settings

            _accepted_memo = _read_version(profile_settings()) >= current_notice_version()
        except Exception:  # nosec B110
            return False
    return _accepted_memo


def save_privacy_notice_accepted(settings=None) -> None:

    global _accepted_memo
    if settings is not None:
        settings.setValue(PRIVACY_NOTICE_KEY, current_notice_version())
        _accepted_memo = True
        return

    profile_settings().setValue(PRIVACY_NOTICE_KEY, current_notice_version())
    _accepted_memo = True

