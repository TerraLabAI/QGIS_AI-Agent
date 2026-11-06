# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

import datetime

from qgis.core import Qgis, QgsTemporalNavigationObject
from qgis.PyQt.QtCore import QDateTime

from ..core.qt_compat import enum_member

_DAY_MS = 86_400_000


_JD_TO_ORDINAL = 1_721_425






def _to_date(moment: int) -> datetime.date:
    return datetime.date.fromordinal(moment // _DAY_MS - _JD_TO_ORDINAL)


def _from_date(day: datetime.date, ms_of_day: int = 0) -> int:
    return (day.toordinal() + _JD_TO_ORDINAL) * _DAY_MS + ms_of_day


def _moment(value: QDateTime) -> int:
    return value.date().toJulianDay() * _DAY_MS + value.time().msecsSinceStartOfDay()


def _navigation_mode(member: str):
    found = enum_member(Qgis, "TemporalNavigationMode", member, None)
    if found is not None:
        return found



    old = {"Disabled": "NavigationOff"}.get(member, member)
    return enum_member(QgsTemporalNavigationObject, "NavigationMode", old)


def _canvas_and_controller():

    from qgis.utils import iface

    canvas = iface.mapCanvas() if iface is not None else None
    controller = canvas.temporalController() if canvas is not None else None
    if controller is None or not hasattr(controller, "setFrameDuration"):
        return canvas, None
    return canvas, controller


def _fmt(moment: int, with_time: bool) -> str:
    day = _to_date(moment)
    if not with_time:
        return day.isoformat()
    ms = moment % _DAY_MS
    return f"{day.isoformat()}T{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d}"
