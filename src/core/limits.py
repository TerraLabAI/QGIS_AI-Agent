# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








































from __future__ import annotations

import math









CALL_MAX_SECONDS_MAIN = 20.0











CALL_MAX_SECONDS_MAIN_LONG = 90.0


















_LONG_MAIN_CALLS = frozenset({"export_layout", "export_atlas", "get_isochrone", "execute_code",
                              "ai_segment", "ai_edit", "load_project"})





ISOCHRONE_MAX_ROADS = 150_000







CALL_MAX_SECONDS_BACKGROUND = 300.0





WATCHDOG_TICK_S = 1.0






WATCHDOG_BLOCKED_S = 5.0





EXECUTE_CODE_MAX_SECONDS = 120.0




DATASET_DOCS_TOTAL_SECONDS = 6.0
DATASET_DOCS_PROBE_SECONDS = 3.0
DATASET_DOCS_CONNECT_SECONDS = 2.0











RUN_MAX_STEPS = 600



RUN_MAX_SECONDS = 3600







MAX_LAYERS_PER_RUN = 40






MAX_FEATURES_PER_CALL = 5000







MAP_MATCH_MAX_REQUESTS = 600







SYNC_FEATURE_LOOP_MAX = 2_000








GEOMETRY_CHECK_MAX_VERTICES = 250_000





GEOMETRY_CHECK_MAX_TOTAL_VERTICES = 3_000_000
GEOMETRY_CHECK_SECONDS = 8.0




MAX_FEATURES_CREATED = 200_000






TERRAIN_MAX_CELLS = 36_000_000







MAX_FEATURES_MATERIALISED = 500_000










MAX_FETCH_KM2 = 250.0








FETCH_QUIET_KM2 = 2.0
FETCH_QUIET_SPARSE_KM2 = 25.0
FETCH_DENSE_MAX_KM2 = 50.0


FETCH_HOSTED_QUIET_KM2 = 4.0
FETCH_HOSTED_QUIET_SPARSE_KM2 = 50.0














FETCH_HARD_MAX_FEATURES = 500_000










FETCH_OWN_OVERPASS_REGION_KM2 = 20_000.0
FETCH_OWN_OVERPASS_SPLIT_KM2 = 80_000.0







MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024





MAX_STREAM_BYTES = 4 * 1024 * 1024 * 1024






MAX_RENDER_WIDTH_PX = 3840
MAX_RENDER_HEIGHT_PX = 2160
MAX_RENDER_PIXELS = MAX_RENDER_WIDTH_PX * MAX_RENDER_HEIGHT_PX






MAX_RENDER_DPI = 600







HYDROLOGY_MAX_CELLS = 4_000_000







GEE_MAX_PIXELS = 1_000_000_000






GEOREFERENCE_MAX_PIXELS = 150_000_000









FLOW_MAX_TABLE_ROWS = 200_000
FLOW_MAX_PLACES = 200_000
FLOW_MAX_LINES = 20_000










CHART_MAX_FEATURES = 500_000
CHART_MAX_BINS = 100
CHART_MAX_CATEGORIES = 40
CHART_MAX_POINTS = 20_000











SPATIAL_STATS_MAX_FEATURES = 200_000








ANIMATION_MAX_FRAMES = 1_000








ANIMATION_ENCODE_MAX_PIXELS = 400_000_000





ANIMATION_MAX_OUTPUT_BYTES = 200 * 1024 * 1024





ANIMATION_MP4_TIMEOUT_S = 90.0







PROCESSING_BATCH_PARALLEL = 4








CHECKPOINTS_PER_CHAT = 12
CHECKPOINT_CHAT_MB = 512
CHECKPOINT_DISK_MB = 2048



CHECKPOINT_LOG_CALLS = 200






GTFS_ZIP_MAX_BYTES = 300 * 1024 * 1024




GTFS_STOP_TIMES_MAX_ROWS = 2_000_000







GTFS_SHAPE_POINTS_MAX_ROWS = 1_000_000










PROMPT_MAX_CHARS = 200_000
ATTACHMENTS_TOTAL_BYTES = 12 * 1024 * 1024


def current(name: str):


















    from . import machine, tuning

    return machine.scale(name, tuning.number("limits", name.lower(), globals()[name]))


def main_budget(name: str) -> float:





    return current("CALL_MAX_SECONDS_MAIN_LONG" if name in _LONG_MAIN_CALLS
                   else "CALL_MAX_SECONDS_MAIN")




_KM_PER_DEGREE = 111.32


def bbox_km2(south: float, west: float, north: float, east: float) -> float:







    height = abs(float(north) - float(south)) * _KM_PER_DEGREE
    middle = math.radians((float(north) + float(south)) / 2.0)
    width = abs(float(east) - float(west)) * _KM_PER_DEGREE * max(math.cos(middle), 0.01)
    return height * width







FIT_MARGIN = 0.95


def shrink_bbox(box, ceiling_km2: float, margin: float = FIT_MARGIN):









    try:
        south, west, north, east = (float(v) for v in box)
        ceiling = float(ceiling_km2)
    except (TypeError, ValueError):
        return None
    area = bbox_km2(south, west, north, east)
    if ceiling <= 0 or area <= 0 or area <= ceiling:
        return None
    scale = (ceiling * float(margin) / area) ** 0.5
    mid_lat, mid_lon = (south + north) / 2.0, (west + east) / 2.0
    half_lat = (north - south) / 2.0 * scale
    half_lon = (east - west) / 2.0 * scale
    return (round(mid_lat - half_lat, 5), round(mid_lon - half_lon, 5),
            round(mid_lat + half_lat, 5), round(mid_lon + half_lon, 5))









CEILING_CODE = "INVALID_ARGS"
RUN_CODE = "RUN_BUDGET"


def _with_machine(advice: str) -> str:









    from . import machine

    note = machine.note()
    return f"{advice} {note}".strip() if note else advice


def refusal(subject: str, measured: str, allowed: str, advice: str, code: str = CEILING_CODE) -> dict:






    return {
        "error": f"{subject} is {measured}, over the limit of {allowed}.",
        "suggestion": _with_machine(advice),
        "code": code,






        "isError": True,
    }
