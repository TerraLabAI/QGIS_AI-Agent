# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every hard limit the plugin enforces, with the reason for each number."""







































from __future__ import annotations

import math









CALL_MAX_SECONDS_MAIN = 20.0











CALL_MAX_SECONDS_MAIN_LONG = 90.0










_LONG_MAIN_CALLS = frozenset({"export_layout", "export_atlas", "get_isochrone"})







CALL_MAX_SECONDS_BACKGROUND = 300.0





WATCHDOG_TICK_S = 1.0






WATCHDOG_BLOCKED_S = 5.0









RUN_MAX_STEPS = 120


RUN_MAX_SECONDS = 2400







MAX_LAYERS_PER_RUN = 40






MAX_FEATURES_PER_CALL = 5000







SYNC_FEATURE_LOOP_MAX = 2_000









GEOMETRY_CHECK_MAX_VERTICES = 250_000





GEOMETRY_CHECK_MAX_TOTAL_VERTICES = 3_000_000
GEOMETRY_CHECK_SECONDS = 8.0




MAX_FEATURES_CREATED = 200_000







MAX_FEATURES_MATERIALISED = 500_000










MAX_FETCH_KM2 = 250.0








FETCH_QUIET_KM2 = 2.0
FETCH_QUIET_SPARSE_KM2 = 25.0
FETCH_DENSE_MAX_KM2 = 50.0
FETCH_OWN_OVERPASS_MAX_KM2 = 80.0


FETCH_HOSTED_QUIET_KM2 = 4.0
FETCH_HOSTED_QUIET_SPARSE_KM2 = 50.0






FETCH_QUIET_FEATURES = 20_000
FETCH_HARD_MAX_FEATURES = 250_000







MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024






MAX_RENDER_WIDTH_PX = 3840
MAX_RENDER_HEIGHT_PX = 2160
MAX_RENDER_PIXELS = MAX_RENDER_WIDTH_PX * MAX_RENDER_HEIGHT_PX






MAX_RENDER_DPI = 600


def current(name: str):
    """The value of ``name`` in force here, now: shipped, then the server, then this machine."""

















    from . import machine, tuning

    return machine.scale(name, tuning.number("limits", name.lower(), globals()[name]))


def main_budget(name: str) -> float:
    """Seconds ``name`` may hold the Qt main thread before the run is held."""




    return current("CALL_MAX_SECONDS_MAIN_LONG" if name in _LONG_MAIN_CALLS
                   else "CALL_MAX_SECONDS_MAIN")




_KM_PER_DEGREE = 111.32


def bbox_km2(south: float, west: float, north: float, east: float) -> float:
    """The area of a lon/lat bbox in square kilometres, near enough to judge it."""






    height = abs(float(north) - float(south)) * _KM_PER_DEGREE
    middle = math.radians((float(north) + float(south)) / 2.0)
    width = abs(float(east) - float(west)) * _KM_PER_DEGREE * max(math.cos(middle), 0.01)
    return height * width









CEILING_CODE = "INVALID_ARGS"
RUN_CODE = "RUN_BUDGET"


def _with_machine(advice: str) -> str:
    """*advice*, followed by why the limit is lower here than the tool description says."""








    from . import machine

    note = machine.note()
    return f"{advice} {note}".strip() if note else advice


def refusal(subject: str, measured: str, allowed: str, advice: str, code: str = CEILING_CODE) -> dict:
    """The sentence a refused call sends back: the limit, the measured value, the way round."""





    return {
        "error": f"{subject} is {measured}, over the limit of {allowed}.",
        "suggestion": _with_machine(advice),
        "code": code,






        "isError": True,
    }
