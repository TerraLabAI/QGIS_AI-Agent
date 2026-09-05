# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""May we use what you send to make the product better?"""























from __future__ import annotations

IMPROVE_ENABLED_KEY = "TerraLab/improve_enabled"


def _profile_settings():
    from qgis.core import QgsSettings

    return QgsSettings()







_SESSION_CHOICE: bool | None = None


def is_improve_enabled(settings=None) -> bool:
    """The stored preference; an unreadable profile does not grant content access."""






    if _SESSION_CHOICE is not None:
        return _SESSION_CHOICE
    try:
        target = settings if settings is not None else _profile_settings()
        return bool(target.value(IMPROVE_ENABLED_KEY, True, type=bool))
    except Exception:  # nosec B110 - a settings read must never stop a connection
        return False


def set_improve_enabled(enabled: bool, settings=None) -> bool:
    """Persist the answer for every TerraLab plugin. True when it was written."""
    global _SESSION_CHOICE

    _SESSION_CHOICE = bool(enabled)
    try:
        target = settings if settings is not None else _profile_settings()
        target.setValue(IMPROVE_ENABLED_KEY, bool(enabled))
    except Exception:  # noqa: BLE001 - a settings write must never stop QGIS
        return False
    return True
