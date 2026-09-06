# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The first-run privacy notice: has this profile accepted the current one?"""



























from __future__ import annotations

PRIVACY_NOTICE_VERSION = 1












_served_version = 0


def set_served_notice_version(version: int) -> None:
    """Record the notice version the server is on. Downgrades are ignored."""
    global _served_version, _accepted_memo
    try:
        value = int(version)
    except (TypeError, ValueError):
        return
    if value > _served_version:
        _served_version = value
        _accepted_memo = None


def current_notice_version() -> int:
    """The version a profile has to have accepted for its consent to count."""
    return max(PRIVACY_NOTICE_VERSION, _served_version)




PRIVACY_NOTICE_KEY = "AIAgent/privacy_notice_accepted_version"





_accepted_memo: bool | None = None


def _profile_settings():
    from qgis.core import QgsSettings

    return QgsSettings()


def _read_version(settings) -> int:
    try:
        return int(settings.value(PRIVACY_NOTICE_KEY, 0, type=int))
    except Exception:  # nosec B110 - a bad value counts as not accepted
        return 0


def has_accepted_privacy_notice(settings=None) -> bool:
    """True when the profile accepted the CURRENT notice version."""


    global _accepted_memo
    if settings is not None:
        return _read_version(settings) >= current_notice_version()
    if _accepted_memo is None:
        try:
            _accepted_memo = _read_version(_profile_settings()) >= current_notice_version()
        except Exception:  # nosec B110 - unreadable profile means not accepted
            return False
    return _accepted_memo


def save_privacy_notice_accepted(settings=None) -> None:
    """Record that the user read the notice and pressed Continue."""
    global _accepted_memo
    if settings is not None:
        settings.setValue(PRIVACY_NOTICE_KEY, current_notice_version())
        _accepted_memo = True
        return
    _profile_settings().setValue(PRIVACY_NOTICE_KEY, current_notice_version())
    _accepted_memo = True


def reset_privacy_notice_memo() -> None:
    """Force the next ``has_accepted_privacy_notice()`` back to QgsSettings."""
    global _accepted_memo
    _accepted_memo = None
