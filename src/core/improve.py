# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later























from __future__ import annotations

IMPROVE_ENABLED_KEY = "TerraLab/improve_enabled"


def _profile_settings():
    from qgis.core import QgsSettings

    return QgsSettings()







_SESSION_CHOICE: bool | None = None


def is_improve_enabled(settings=None) -> bool:







    if _SESSION_CHOICE is not None:
        return _SESSION_CHOICE
    try:
        target = settings if settings is not None else _profile_settings()
        return bool(target.value(IMPROVE_ENABLED_KEY, True, type=bool))
    except Exception:  # nosec B110
        return False


def set_improve_enabled(enabled: bool, settings=None) -> bool:

    global _SESSION_CHOICE

    _SESSION_CHOICE = bool(enabled)
    try:
        target = settings if settings is not None else _profile_settings()
        target.setValue(IMPROVE_ENABLED_KEY, bool(enabled))
    except Exception:  # noqa: BLE001
        return False
    return True
