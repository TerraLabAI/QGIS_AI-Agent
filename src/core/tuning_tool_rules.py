# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later









from __future__ import annotations

import math
import re
from typing import Any

from .logger import log_warning

VERSION = 1
_MAX_ITEMS = 400
_MAX_TEXT = 120

_WORD = re.compile(r"[A-Za-z0-9_.:+ -]{1,60}")
_LOWER_WORD = re.compile(r"[a-z0-9]{1,24}")
_ALG_ID = re.compile(r"[a-z0-9_.]{1,40}:[a-z0-9_.]{1,60}")
_PARAMETER = re.compile(r"[A-Z][A-Z0-9_]{0,39}")
_PROVIDER = re.compile(r"[a-z0-9_]{1,30}")
_SUFFIX = re.compile(r"\.[a-z0-9]{1,8}")
_UNIT = re.compile(r"[a-z0-9]{1,12}")
_HINT_CODE = re.compile(r"[a-z][a-z0-9_]{0,60}")
_EXCEPTION = re.compile(r"[A-Za-z]{1,40}")


def _number(low: float, high: float, whole: bool = False):
    def check(value: Any, spot: str):
        if isinstance(value, bool) or not isinstance(value, int if whole else (int, float)) \
                or not math.isfinite(value) or not low <= value <= high:
            log_warning(f"Server policy {spot} is not a number in {low}..{high}, ignored")
            return None
        return value
    return check


def _any_number(value: Any, spot: str):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        log_warning(f"Server policy {spot} is not a finite number, ignored")
        return None
    return value


def _strings(pattern: re.Pattern, fold: bool = False):

    def check(value: Any, spot: str):
        if not isinstance(value, list):
            log_warning(f"Server policy {spot} is not a list, ignored")
            return None
        out: list = []
        for item in value[:_MAX_ITEMS]:
            text = item.strip().lower() if fold and isinstance(item, str) else item
            if isinstance(text, str) and pattern.fullmatch(text) and text not in out:
                out.append(text)
        return out or None
    return check


def _numbers(value: Any, spot: str):
    if not isinstance(value, list):
        log_warning(f"Server policy {spot} is not a list, ignored")
        return None
    out = [v for v in value[:_MAX_ITEMS]
           if not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)]
    return out or None


def _map(key_pattern: re.Pattern, item_check):

    def check(value: Any, spot: str):
        if not isinstance(value, dict):
            log_warning(f"Server policy {spot} is not a table, ignored")
            return None
        out: dict = {}
        for key, item in list(value.items())[:_MAX_ITEMS]:
            if not isinstance(key, str) or not key_pattern.fullmatch(key):
                continue
            clean = item_check(item, f"{spot}.{key}")
            if clean is not None:
                out[key] = clean
        return out or None
    return check


def _positive(value: Any, spot: str):
    return _number(1e-9, 1e12)(value, spot)


def _text(value: Any, spot: str):
    text = " ".join(value.split()) if isinstance(value, str) else ""
    if not text or len(text) > _MAX_TEXT or not text.isprintable():
        log_warning(f"Server policy {spot} is not a short text, ignored")
        return None
    return text


def _parameter_text(value: Any, spot: str):
    if isinstance(value, str) and _PARAMETER.fullmatch(value):
        return value
    log_warning(f"Server policy {spot} is not a parameter name, ignored")
    return None


def _parameter_pairs(value: Any, spot: str):
    if not isinstance(value, list):
        log_warning(f"Server policy {spot} is not a list, ignored")
        return None
    out = [[row[0], row[1]] for row in value[:_MAX_ITEMS]
           if isinstance(row, list) and len(row) == 2 and isinstance(row[0], str) and _ALG_ID.fullmatch(row[0])
           and isinstance(row[1], str) and _PARAMETER.fullmatch(row[1])]
    return out or None


def _fields(fields: dict):

    def check(value: Any, spot: str):
        out = _section(value, spot, fields)
        return out or None
    return check


def _range_row(value: Any, spot: str):
    out = _section(value, spot, {"low": _any_number, "high": _any_number, "what": _text})
    if set(out) == {"low", "high", "what"} and out["low"] < out["high"]:
        return out
    log_warning(f"Server policy {spot} is not a range with a low, a high and a name, ignored")
    return None


def _signatures(value: Any, spot: str):
    if not isinstance(value, list):
        log_warning(f"Server policy {spot} is not a list, ignored")
        return None
    out = [[row[0], row[1]] for row in value[:_MAX_ITEMS]
           if isinstance(row, list) and len(row) == 2 and isinstance(row[0], str) and 8 <= len(row[0]) <= _MAX_TEXT
           and row[0].isprintable() and isinstance(row[1], str) and _HINT_CODE.fullmatch(row[1])]
    return out or None


def _hint_codes(value: Any, spot: str):
    return _map(_EXCEPTION, lambda v, s: v if isinstance(v, str) and _HINT_CODE.fullmatch(v) else None)(value, spot)


_ALGS = _strings(_ALG_ID, fold=True)
_PARAMETERS = _strings(_PARAMETER)

_SECTIONS: dict[str, dict] = {
    "algorithm_search": {
        "stopwords": _strings(_LOWER_WORD, fold=True),
        "min_word_len": _number(1, 8, whole=True),
        "weights": _map(re.compile(r"[a-z_]{1,20}"), _number(0.0, 20.0)),
    },
    "guards": {
        "distance": _fields({"degrees_max": _number(1.0, 90.0), "span_ratio": _number(5.0, 1000.0),
                             "floor": _number(1_000.0, 10_000_000.0)}),
        "window_ratio_max": _number(1.5, 10.0),
        "raster_input_keys": _PARAMETERS,
        "raster_list_keys": _PARAMETERS,
        "window_keys": _PARAMETERS,
        "window_algorithms": _ALGS,
        "resample_algorithms": _ALGS,
        "external_providers": _strings(_PROVIDER, fold=True),
        "nodata_sentinels": _numbers,
        "hillshade": _fields({"algorithm_words": _strings(_WORD, fold=True), "name_words": _strings(_WORD, fold=True)}),
        "ground_method": _map(re.compile(r"[a-z0-9_]{1,40}"), _PARAMETERS),
    },
    "crs_checks": {
        "percentage_parameters": _parameter_pairs,
        "degrees_allowed": _number(0.001, 0.1),
        "half_cell_max": _number(1.0, 5.0),
        "area_of_use_margin": _number(0.5, 10.0),
    },
    "decisions": {"nodata_default": _number(-1e12, 1e12)},
    "paths": {
        "fragile_providers": _strings(_PROVIDER, fold=True),
        "narrow_open_suffixes": _strings(_SUFFIX, fold=True),
    },
    "destinations": {
        "optional_providers": _map(_PROVIDER, _text),
        "sibling_extensions": _strings(_SUFFIX, fold=True),
    },
    "invariants": {
        "area_units": _map(_UNIT, _positive),
        "length_units": _map(_UNIT, _positive),
        "area_words": _strings(_LOWER_WORD, fold=True),
        "length_words": _strings(_LOWER_WORD, fold=True),
        "perimeter_words": _strings(_LOWER_WORD, fold=True),
        "not_a_measure": _strings(_LOWER_WORD, fold=True),
        "unit_tolerance": _number(1.1, 100.0),
        "aoi_tolerance": _number(0.0, 1.0),
        "zone_tolerance": _number(0.0, 1.0),
        "zone_min_pixels": _number(1, 100_000, whole=True),
        "ranges": _map(re.compile(r"[a-z_]{1,30}"), _range_row),
        "calculators": _map(_ALG_ID, _parameter_text),
    },
    "exception_hints": {"signatures": _signatures, "types": _hint_codes},
}


def _section(raw: Any, where: str, fields: dict) -> dict:
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for name, check in fields.items():
        if name in raw:
            clean = check(raw[name], f"{where}.{name}")
            if clean is not None:
                out[name] = clean
    return out


def clean_tool_rules(value: Any, where: str) -> dict | None:

    if not isinstance(value, dict) or value.get("version") != VERSION:
        log_warning(f"Server policy {where} has no version {VERSION}, ignored")
        return None
    out: dict = {"version": VERSION}
    for name, fields in _SECTIONS.items():
        part = _section(value.get(name), f"{where}.{name}", fields)
        if part:
            out[name] = part
    return out
