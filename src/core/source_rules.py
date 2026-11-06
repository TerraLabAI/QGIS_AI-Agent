# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later













from __future__ import annotations

import re
from typing import Any

from .logger import log_warning

_VERSION = 1
_MAX_ROWS = 200

_WORD = re.compile(r"[A-Za-z0-9_.+%/:,=&?-]{1,60}")

_PHRASE = re.compile(r"[A-Za-z0-9_ ]{1,40}")


def _word(value: Any) -> bool:
    return isinstance(value, str) and _WORD.fullmatch(value) is not None


def _phrase(value: Any) -> bool:
    return isinstance(value, str) and _PHRASE.fullmatch(value) is not None


def _whole(low: int, high: int):
    return lambda value: isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _list(check):
    def clean(value: Any):
        if not isinstance(value, list):
            return None
        return [item for item in value[:_MAX_ROWS] if check(item)]
    return clean


def _map(key_check, value_check):
    def clean(value: Any):
        if not isinstance(value, dict):
            return None
        return {k: v for k, v in list(value.items())[:_MAX_ROWS] if key_check(k) and value_check(v)}
    return clean


def _scalar(check):
    return lambda value: value if check(value) else None


_SCHEMA: dict[str, dict[str, Any]] = {
    "files": {
        "content_type_exts": _map(_word, _word), "endpoint_exts": _list(_word),
        "direct_import_exts": _list(_word), "format_segments": _map(_word, _word),
        "streamed_exts": _map(_word, _word), "download_exts": _list(_word), "kind_by_ext": _map(_word, _word),
        "data_exts": _list(_word), "rank": _map(_word, _whole(0, 9)),
    },
    "pages": {"service_hints": _list(_word), "binary_exts": _list(_word)},
    "portals": {
        "format_words": _map(_phrase, _word), "stop_words": _list(_word), "narrow_widths": _list(_whole(1, 8)),
        "skip_formats": _list(_word), "resource_formats": _list(_word),
    },
    "sidecars": {"names": _list(_word), "levels": _scalar(_whole(0, 4)), "max_probes": _scalar(_whole(0, 8))},
    "links": {
        "arcgis_services": _list(_phrase), "arcgis_file_ext": _map(_phrase, _word),
        "webmap_services": _map(_word, _phrase), "webmap_ogc": _list(_word),
    },
    "ogc": {
        "crs_preference": _list(_word), "wmts_preferred_crs": _scalar(_word),
        "tile_format_preference": _scalar(_word),
        "wfs_warn_features": _scalar(_whole(100, 10_000_000)),
        "wfs_wire_bytes_per_feature": _scalar(_whole(10, 100_000)),
    },
}


def _departments(value: Any) -> list | None:


    if not isinstance(value, list):
        return None
    rows = []
    for row in value[:_MAX_ROWS]:
        if (isinstance(row, dict) and _word(row.get("head")) and _word(row.get("tail"))
                and _whole(1, 4)(row.get("width")) and _whole(1, 3)(row.get("short_digits"))
                and isinstance(row.get("corsica"), bool)):
            rows.append({k: row[k] for k in ("head", "tail", "width", "short_digits", "corsica")})
    return rows


def clean_source_rules(value: Any, where: str) -> dict | None:

    if not isinstance(value, dict) or value.get("version") != _VERSION:
        log_warning(f"Server policy {where} has no version {_VERSION}, ignored")
        return None
    out: dict[str, Any] = {"version": _VERSION}
    for section, fields in _SCHEMA.items():
        raw = value.get(section)
        if not isinstance(raw, dict):
            continue
        part = {}
        for name, check in fields.items():
            if name not in raw:
                continue
            clean = check(raw[name])
            if clean is None:
                log_warning(f"Server policy {where}.{section}.{name} failed its check, ignored")
            else:
                part[name] = clean
        if part:
            out[section] = part
    departments = _departments(value.get("departments"))
    if departments:
        out["departments"] = departments
    return out


def table(section: str, name: str, empty):

    from . import tuning

    doc = tuning.service_doc("source_rules")
    part = doc.get(section) if isinstance(doc, dict) else None
    found = part.get(name) if isinstance(part, dict) else None
    return found if isinstance(found, type(empty)) else empty


def departments() -> list:
    from . import tuning

    doc = tuning.service_doc("source_rules")
    found = doc.get("departments") if isinstance(doc, dict) else None
    return found if isinstance(found, list) else []
