# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later












from __future__ import annotations

import re
from typing import Any

from .logger import log_warning

_VERSION = 1
_MAX_ITEMS = 200
_MAX_TEXT = 500
_WORD = re.compile(r"[^\s]{1,60}")


def _num(low: float, high: float, whole: bool = False):
    def check(value: Any):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = int(value) if whole else float(value)
        return min(max(number, low), high)
    return check


def _text(value: Any):
    return value.strip()[:_MAX_TEXT] if isinstance(value, str) and value.strip() else None


def _words(value: Any):
    if not isinstance(value, list):
        return None
    out = [item for item in value[:_MAX_ITEMS] if isinstance(item, str) and _WORD.fullmatch(item.replace(" ", "_"))]
    return out or None


def _word_lists(value: Any):
    if not isinstance(value, list):
        return None
    out = [_words(item) for item in value[:20]]
    return out if out and all(out) else None


def _map(inner):
    def check(value: Any):
        if not isinstance(value, dict):
            return None
        out = {k: v for k, v in ((k, inner(v)) for k, v in list(value.items())[:_MAX_ITEMS] if isinstance(k, str)
                                 and 0 < len(k) <= 60) if v is not None}
        return out or None
    return check


def _pair(low: float, high: float):
    def check(value: Any):
        if not isinstance(value, list) or len(value) != 2:
            return None
        out = [_num(low, high)(item) for item in value]
        return out if None not in out else None
    return check


def _ints(low: int, high: int):
    def check(value: Any):
        if not isinstance(value, list):
            return None
        out = [_num(low, high, True)(item) for item in value[:10]]
        return out if out and None not in out else None
    return check


_TEXTS = _map(_text)
_WORD_MAP = _map(_text)
_WORD_LISTS = _map(_words)
_COUNT = _num(1, 100_000, True)



_SPEC: dict[str, dict[str, Any]] = {
    "stac": {
        "band_names": _WORD_MAP, "mask_roles": _words, "true_colour": _words, "picture_role": _text,
        "reflectance_role": _text, "browse_keys": _words, "notes": _TEXTS,
        "signed_prefixes": _words, "signed_hosts": _words, "signed_suffixes": _words,
    },
    "geocode": {
        "generic_place_words": _words, "leading_words": _words, "ban_country_words": _words,
        "arrondissement": {
            "cities": _words, "ordinals": _words, "official_ordinals": _words, "linkers": _words,
            "word": _text, "form": _text, "first_ordinal": _text, "other_ordinal": _text,
        },
    },
    "places": {
        "near_view_deg": _num(0.1, 360.0), "name_search_half_deg": _num(0.0001, 1.0),
        "good_overlap": _num(0.01, 1.0), "past_hit_box": _num(1.0, 100.0),
        "around_half_deg": _num(0.0001, 1.0), "around_min_half_deg": _num(0.00001, 1.0),
    },
    "osm": {
        "unreachable": _words, "instance_faults": _words, "no_history": _text, "rejected_codes": _words,
        "licence": _text, "attribution": _text, "footprint_labels": _TEXTS, "footprint_default_sources": _words,
        "footprint_max_span_deg": _num(0.01, 5.0),
    },
    "overture": {
        "tile_zoom": _num(1, 14, True), "max_tiles": _num(1, 16, True), "subtype_order": _words,
        "subtype_waves": _word_lists, "hit_subtypes": _TEXTS, "division_words": _TEXTS,
        "division_subtypes_per_call": _num(1, 8, True), "clip_max_parts": _COUNT,
        "clip_max_boxes": _num(1, 50, True), "clip_max_grid": _num(1, 50_000, True),
    },
    "overture_extract": {
        "class_fields": _words, "cover_key": _text, "cover_cells": _num(1_000, 4_000_000, True),
        "window_grid": _num(2, 128, True), "max_windows": _num(1, 64, True),
    },
    "cases": {
        "relaxed_queries": {
            "keep_tail": _num(1, 10, True), "min_parts": _num(1, 20, True), "min_left": _num(1, 20, True),
            "drop_leading": _ints(1, 10), "max_forms": _num(1, 10, True),
        },
        "road_name_hints": _words,
        "isochrone": {
            "fringe_m": _num(0.0, 1_000.0), "snap_m": _num(1.0, 5_000.0), "max_starts": _num(1, 200, True),
            "cover": _num(0.1, 1.0), "island_share": _num(0.0, 0.5), "method": _text,
        },
        "topology_defaults": _WORD_LISTS,
        "stem_sidecars": _words, "name_sidecars": _words,
    },
    "georeference": {
        "frame_35mm_mm": _pair(1.0, 500.0),
        "sensor_mm": _pair(0.1, 500.0), "nadir_pitch_deg": _num(-90.0, 0.0), "how": _TEXTS, "notes": _TEXTS,
    },
    "basemaps": {
        "synonyms": _TEXTS, "bare_osm": {"words": _words, "id": _text}, "vector_penalty": _num(0, 10, True),
        "gibs_matrix_prefix": _text, "default_max_zoom": _num(1, 24, True),
    },
    "local_harvest": {
        "x_names": _words, "y_names": _words, "address_words": _words, "skip_dirs": _words,
    },
}


def _clean_part(value: Any, spec: Any, where: str, whole: bool = False):

    if isinstance(spec, dict):
        if not isinstance(value, dict):
            log_warning(f"Server policy {where} is not an object, ignored")
            return None
        out = {}
        for key, inner in spec.items():
            clean = _clean_part(value[key], inner, f"{where}.{key}", True) if key in value else None
            if clean is not None:
                out[key] = clean
            elif whole:
                log_warning(f"Server policy {where} lacks a usable {key}, ignored")
                return None
        return out or None
    clean = spec(value)
    if clean is None:
        log_warning(f"Server policy {where} is malformed, ignored")
    return clean


def clean(value: Any, where: str = "services.geodata_rules") -> dict | None:

    if not isinstance(value, dict) or value.get("version") != _VERSION:
        log_warning(f"Server policy {where} has no usable version, ignored")
        return None
    out: dict = {"version": _VERSION}
    for section, spec in _SPEC.items():
        if section in value:
            part = _clean_part(value[section], spec, f"{where}.{section}")
            if part is not None:
                out[section] = part
    return out


def rule(section: str, *path: str):

    from . import tuning
    from .tool_registry import ServedValueMissing

    found: Any = tuning.service_doc("geodata_rules") or {}
    found = found.get(section)
    for key in path:
        found = found.get(key) if isinstance(found, dict) else None
    if found is None:
        raise ServedValueMissing(".".join(("services", "geodata_rules", section, *path)))
    return found


def fill(template: str, **values) -> str:

    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), template)


def note(section: str, name: str, **values) -> str | None:

    from . import tuning

    texts = ((tuning.service_doc("geodata_rules") or {}).get(section) or {}).get("notes") or {}
    text = texts.get(name)
    return fill(text, **values) if isinstance(text, str) else None
