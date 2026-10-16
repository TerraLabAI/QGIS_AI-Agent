# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

import math
import re
from typing import Any

from .logger import log_warning












_SERVICE_URLS = frozenset({

    "overture_api",
    "overture_tiles",
    "osm_tiles",
    "footprints",
    "photon",

    "ban", "cartociudad", "pelias", "nominatim",
    "osrm_driving", "osrm_walking", "osrm_cycling",
    "stac_root", "pc_sign", "cdse_https_prefix",
})
_MAX_URL_CHARS = 300




_SERVICE_TOKENS = frozenset({
    "osm_themes", "overture_themes", "division_subtypes", "footprint_sources",
})
_MAX_TOKENS = 60
_MAX_TOKEN_CHARS = 40






_SERVICE_HOSTS = frozenset({"earthdata_hosts", "earthdata_suffixes", "open_data_hosts"})
_MAX_HOSTS = 40



_CAP_KM2 = (1.0, 5_000.0)
_CAP_SPAN_DEG = (0.05, 10.0)




_CAP_BY_THEME = {"divisions": ((1.0, 30_000_000.0), (0.05, 60.0))}
_MAX_CAP_ROWS = 60



_SILENT_PAGE = (1, 1_000_000)
_MAX_SILENT_PAGES = 32









_MAX_PORTAL_ROWS = 20
_MAX_SEARCH_URL_CHARS = 400
_MAX_PORTAL_NAME_CHARS = 60


def _clean_service_url(value: Any, where: str) -> str | None:

    if not isinstance(value, str):
        log_warning(f"Server policy {where} is not a string, ignored")
        return None
    text = value.strip()
    if not text or len(text) > _MAX_URL_CHARS:
        log_warning(f"Server policy {where} is empty or over {_MAX_URL_CHARS} characters, ignored")
        return None
    import urllib.parse

    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        log_warning(f"Server policy {where} cannot be parsed as a URL, ignored")
        return None
    if parts.scheme.lower() != "https" or not parts.hostname:
        log_warning(f"Server policy {where} is not an https URL, ignored")
        return None
    if parts.username or parts.password or "@" in parts.netloc:
        log_warning(f"Server policy {where} carries credentials, ignored")
        return None
    if parts.fragment:
        log_warning(f"Server policy {where} carries a fragment, ignored")
        return None



    try:
        from . import security

        if security.is_local_url(text, resolve=False) or security.is_private_url(text, resolve=False):
            log_warning(f"Server policy {where} names a local or private address, ignored")
            return None
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Server policy {where} could not be checked ({exc}), ignored")
        return None



    return text if where.endswith("_prefix") else text.rstrip("/")


def _clean_tokens(value: Any, where: str) -> list | None:

    if not isinstance(value, list):
        log_warning(f"Server policy {where} is not a list, ignored")
        return None
    out: list = []
    for item in value[:_MAX_TOKENS]:
        if not isinstance(item, str):
            continue
        token = item.strip().lower()
        if not token or len(token) > _MAX_TOKEN_CHARS or not token.replace("_", "").isalnum():
            continue
        if token not in out:
            out.append(token)
    if not out:
        log_warning(f"Server policy {where} held no usable value, ignored")
        return None
    return out


def _clean_hosts(value: Any, where: str) -> list | None:

    if not isinstance(value, list):
        log_warning(f"Server policy {where} is not a list, ignored")
        return None
    out: list = []
    for item in value[:_MAX_HOSTS]:
        if not isinstance(item, str):
            continue
        host = item.strip().lower().strip("/")
        if not host or len(host) > 253 or "/" in host or ":" in host or " " in host:
            continue
        if host not in out:
            out.append(host)
    if not out:
        log_warning(f"Server policy {where} held no usable host, ignored")
        return None
    return out


def _clean_caps(value: Any, where: str) -> dict | None:

    if not isinstance(value, dict):
        log_warning(f"Server policy {where} is not an object, ignored")
        return None
    out: dict = {}
    for theme, pair in list(value.items())[:_MAX_CAP_ROWS]:
        if not isinstance(theme, str) or not theme.strip():
            continue
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            log_warning(f"Server policy {where}.{theme} is not a [km2, degrees] pair, ignored")
            continue
        km2_range, span_range = _CAP_BY_THEME.get(theme.strip().lower(), (_CAP_KM2, _CAP_SPAN_DEG))
        km2 = _clamp_float(pair[0], *km2_range, where=f"{where}.{theme}.km2")
        span = _clamp_float(pair[1], *span_range, where=f"{where}.{theme}.degrees")
        if km2 is None or span is None:
            continue
        out[theme.strip().lower()] = (km2, span)
    return out or None


def _clean_silent_pages(value: Any, where: str) -> list | None:
    if not isinstance(value, list):
        log_warning(f"Server policy {where} is not a list, ignored")
        return None
    out: list = []
    for item in value[:_MAX_SILENT_PAGES]:
        page = _clamp_int(item, *_SILENT_PAGE, where=where)
        if page is not None and page not in out:
            out.append(page)
    return sorted(out) or None


def _clean_portals(value: Any, where: str) -> dict | None:

    if not isinstance(value, dict):
        log_warning(f"Server policy {where} is not an object, ignored")
        return None
    out: dict = {}
    for key, row in list(value.items())[:_MAX_PORTAL_ROWS]:
        if not isinstance(key, str) or not isinstance(row, dict):
            continue
        spot = f"{where}.{key}"
        clean: dict = {}
        base = _clean_service_url(row.get("base_url"), f"{spot}.base_url") if row.get("base_url") else None
        if base:
            clean["base_url"] = base
        search = row.get("search_url")
        if isinstance(search, str) and search.strip():
            text = search.strip()




            if len(text) > _MAX_SEARCH_URL_CHARS:
                log_warning(f"Server policy {spot}.search_url is over {_MAX_SEARCH_URL_CHARS} characters, ignored")
            elif text.count("{query}") != 1 or set("{}") & set(text.replace("{query}", "")):
                log_warning(f"Server policy {spot}.search_url does not carry exactly one {{query}}, ignored")
            else:
                probe = _clean_service_url(text.replace("{query}", "q"), f"{spot}.search_url")
                if probe:
                    clean["search_url"] = text
        name = row.get("name")
        if isinstance(name, str) and name.strip():
            clean["name"] = name.strip()[:_MAX_PORTAL_NAME_CHARS]


        api_type = row.get("api_type")
        if isinstance(api_type, str) and _ROUTING_THEME.fullmatch(api_type.strip()):
            clean["api_type"] = api_type.strip()
        if clean:
            out[key] = clean
    return out or None











_ROUTING_VERSION = 1
_ROUTING_TABLES = ("theme_keys", "value_themes", "fields", "mixed_themes", "road_classes", "key_values",
                   "way_themes")

_ROUTING_CODE_THEMES = frozenset({"roads", "buildings"})
_MAX_ROUTING_ROWS = 100
_MAX_ROUTING_VALUES = 200
_ROUTING_THEME = re.compile(r"[a-z0-9_]{1,40}")

_ROUTING_KEY = re.compile(r"[a-z_][a-z0-9_:]{0,59}")
_ROUTING_VALUE = re.compile(r"[A-Za-z0-9_:\-]{1,60}")


class _RoutingInvalid(ValueError):
    pass


def _routing_word(value: Any, pattern, spot: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise _RoutingInvalid(f"{spot} holds {str(value)[:60]!r}")
    return value


def _routing_words(value: Any, pattern, spot: str, empty: bool = False) -> list:
    if not isinstance(value, list) or len(value) > _MAX_ROUTING_VALUES or (not value and not empty):
        raise _RoutingInvalid(f"{spot} is not a list of 1 to {_MAX_ROUTING_VALUES} values")
    out: list = []
    for item in value:
        word = _routing_word(item, pattern, spot)
        if word not in out:
            out.append(word)
    return out


def _routing_rows(value: Any, spot: str, empty: bool = False) -> dict:
    if not isinstance(value, dict) or len(value) > _MAX_ROUTING_ROWS or (not value and not empty):
        raise _RoutingInvalid(f"{spot} is not an object of 1 to {_MAX_ROUTING_ROWS} rows")
    return value


def _routing_document(value: Any, where: str) -> dict:

    if not isinstance(value, dict):
        raise _RoutingInvalid(f"{where} is not an object")
    version = value.get("version")
    if isinstance(version, bool) or version != _ROUTING_VERSION:
        raise _RoutingInvalid(f"{where}.version is not {_ROUTING_VERSION}")
    missing = [table for table in _ROUTING_TABLES if table not in value]
    if missing:
        raise _RoutingInvalid(f"{where} has no {', '.join(missing)}")
    fields: dict = {}
    for theme, names in _routing_rows(value["fields"], f"{where}.fields").items():
        _routing_word(theme, _ROUTING_THEME, f"{where}.fields")
        fields[theme] = _routing_words(names, _ROUTING_KEY, f"{where}.fields.{theme}")
    known = set(fields) | _ROUTING_CODE_THEMES

    def theme_named(name: Any, spot: str) -> str:
        if not isinstance(name, str) or name not in known:
            raise _RoutingInvalid(f"{spot} names {str(name)[:60]!r}, a theme with no field list")
        return name

    theme_keys: dict = {}
    for key, theme in _routing_rows(value["theme_keys"], f"{where}.theme_keys").items():
        _routing_word(key, _ROUTING_KEY, f"{where}.theme_keys")
        theme_keys[key] = theme_named(theme, f"{where}.theme_keys.{key}")
    value_themes: dict = {}
    for key, row in _routing_rows(value["value_themes"], f"{where}.value_themes", empty=True).items():
        spot = f"{where}.value_themes.{_routing_word(key, _ROUTING_KEY, f'{where}.value_themes')}"
        if not isinstance(row, dict):
            raise _RoutingInvalid(f"{spot} is not an object")
        value_themes[key] = {"theme": theme_named(row.get("theme"), f"{spot}.theme"),
                             "values": _routing_words(row.get("values"), _ROUTING_VALUE, f"{spot}.values")}
    key_values: dict = {}
    for theme, row in _routing_rows(value["key_values"], f"{where}.key_values", empty=True).items():
        spot = f"{where}.key_values.{str(theme)[:40]}"
        if theme not in fields or not isinstance(row, dict):
            raise _RoutingInvalid(f"{spot} is not an object for a theme with a field list")
        if not isinstance(row.get("bare"), bool):
            raise _RoutingInvalid(f"{spot}.bare is not true or false")
        if row.get("key") not in fields[theme]:
            raise _RoutingInvalid(f"{spot}.key is not one of that theme's fields")
        if ("only" in row) == ("except" in row):
            raise _RoutingInvalid(f"{spot} carries neither or both of only and except")
        clean = {"key": row["key"], "bare": row["bare"]}
        if "only" in row:
            clean["only"] = _routing_words(row["only"], _ROUTING_VALUE, f"{spot}.only")
        else:
            clean["except"] = _routing_words(row["except"], _ROUTING_VALUE, f"{spot}.except", empty=True)
        key_values[theme] = clean
    mixed = _routing_words(value["mixed_themes"], _ROUTING_THEME, f"{where}.mixed_themes", empty=True)
    for theme in mixed:
        if theme not in fields:
            raise _RoutingInvalid(f"{where}.mixed_themes names {theme!r}, a theme with no field list")
    way_themes = [theme_named(theme, f"{where}.way_themes") for theme in
                  _routing_words(value["way_themes"], _ROUTING_THEME, f"{where}.way_themes", empty=True)]
    return {
        "version": _ROUTING_VERSION,
        "theme_keys": theme_keys,
        "value_themes": value_themes,
        "fields": fields,
        "mixed_themes": mixed,
        "road_classes": _routing_words(value["road_classes"], _ROUTING_VALUE, f"{where}.road_classes"),
        "key_values": key_values,
        "way_themes": way_themes,
    }


def _clean_osm_routing(value: Any, where: str) -> dict | None:
    try:
        return _routing_document(value, where)
    except _RoutingInvalid as exc:
        log_warning(f"Server policy {exc}: the whole {where} is ignored, every query goes to Overpass")
        return None












_CODE_CLASSES_VERSION = 1
_CODE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,79}")
_CODE_EXTRA_TABLES = frozenset({"processing_refused", "network_modules", "process_modules"})
_MAX_CODE_NAMES = 2000


def _clean_code_classes(value: Any, where: str) -> dict | None:
    from .code_effects import SHIPPED_TABLES

    if not isinstance(value, dict) or value.get("version") != _CODE_CLASSES_VERSION:
        log_warning(f"Server policy {where} has no version {_CODE_CLASSES_VERSION}, ignored")
        return None
    out: dict = {"version": _CODE_CLASSES_VERSION}
    for table, names in value.items():
        if table == "version" or (table not in SHIPPED_TABLES and table not in _CODE_EXTRA_TABLES):
            continue
        spot = f"{where}.{table}"
        if table == "processing_refused":
            clean = _clean_alg_names(names, spot)
            if clean is None and names:
                return None
            out[table] = clean or []
            continue
        if (not isinstance(names, list) or len(names) > _MAX_CODE_NAMES
                or not all(isinstance(n, str) and _CODE_NAME.fullmatch(n) for n in names)):
            log_warning(f"Server policy {spot} is not a list of names: the whole {where} is ignored")
            return None
        out[table] = sorted(set(names))
    return out








_MAX_WITHDRAWN = 200
_MAX_REASON_CHARS = 400
_HOST_KEY = re.compile(r"\.?[a-z0-9-]+(\.[a-z0-9-]+)+")


def _clean_withdrawn(value: Any, where: str) -> dict | None:
    if not isinstance(value, dict):
        log_warning(f"Server policy {where} is not an object, ignored")
        return None
    out: dict = {}
    for host, reason in list(value.items())[:_MAX_WITHDRAWN]:
        key = host.strip().lower() if isinstance(host, str) else ""
        if len(key) > 253 or not _HOST_KEY.fullmatch(key):
            log_warning(f"Server policy {where} names {str(host)[:60]!r}, not a host, ignored")
            continue
        text = " ".join(reason.split()) if isinstance(reason, str) else ""
        if not text:
            continue
        out[key] = text[:_MAX_REASON_CHARS]
    return out or None







_RULES_VERSION = 1
_MAX_RULE_ROWS = 100
_MAX_RULE_TEXT = 300
_PARAMETER = re.compile(r"[A-Z][A-Z0-9_]{0,39}")
_STYLE_MODES = frozenset({"jenks", "quantile", "equal_interval", "pretty"})
_STYLE_METHODS = frozenset({"Jenks", "Quantile", "EqualInterval", "Pretty"})
_SCALES = frozenset({"linear", "area", "flannery", "exponential"})
_GEOMETRIES = frozenset({"point", "line", "polygon"})
_GRID_FAMILIES = frozenset({"grid_parameters", "grid_parameters_in_place", "warp_inputs", "reproject_inputs",
                            "refuse_mixed_crs"})
_RANK_SIGNALS = ("named", "selected", "active", "by_agent", "visible_in_view", "visible")
_STACK_KINDS = ("backdrop", "raster", "polygon", "line", "point")


def _rule_number(value: Any, low: float, high: float, spot: str, whole: bool = False):


    if isinstance(value, bool) or not isinstance(value, int if whole else (int, float)):
        log_warning(f"Server policy {spot} is not a number, ignored")
        return None
    if not (math.isfinite(value) and low <= value <= high):
        log_warning(f"Server policy {spot}={value} is outside {low}..{high}, ignored")
        return None
    return value


def _rule_text(value: Any, spot: str) -> str | None:
    text = " ".join(value.split()) if isinstance(value, str) else ""
    if not text or len(text) > _MAX_RULE_TEXT or not text.isprintable():
        log_warning(f"Server policy {spot} is not a short sentence, ignored")
        return None
    return text


def _rule_part(raw: Any, spot: str, fields: dict) -> dict:


    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for name, check in fields.items():
        if name in raw:
            clean = check(raw[name], f"{spot}.{name}")
            if clean is not None:
                out[name] = clean
    return out


def _one_of(choices) -> Any:
    def check(value: Any, spot: str):
        if value in choices:
            return value
        log_warning(f"Server policy {spot}={str(value)[:40]!r} is not one of {sorted(choices)}, ignored")
        return None
    return check


def _subset_of(choices) -> Any:
    def check(value: Any, spot: str):
        if isinstance(value, list) and all(item in choices for item in value):
            return list(dict.fromkeys(value))
        log_warning(f"Server policy {spot} is not a list of {sorted(choices)}, ignored")
        return None
    return check


def _in_range(low: float, high: float, whole: bool = False) -> Any:
    return lambda value, spot: _rule_number(value, low, high, spot, whole)


def _versioned(value: Any, where: str) -> bool:
    if isinstance(value, dict) and value.get("version") == _RULES_VERSION:
        return True
    log_warning(f"Server policy {where} has no version {_RULES_VERSION}, ignored")
    return False


def _clean_style_rules(value: Any, where: str) -> dict | None:
    if not _versioned(value, where):
        return None
    sections = {
        "graduated": {"skewed_mode": _one_of(_STYLE_MODES), "even_mode": _one_of(_STYLE_MODES),
                      "skew_min": _in_range(0.3, 3.0), "codes_max_distinct": _in_range(2, 64, whole=True)},
        "dominant": {"share": _in_range(0.5, 0.95), "spread_modes": _subset_of(_STYLE_METHODS)},
        "raster_codes": {"max_codes": _in_range(2, 255, whole=True), "max_consecutive": _in_range(2, 255, whole=True)},
        "stretch": {"cut_low": _in_range(0.0, 20.0), "cut_high": _in_range(80.0, 100.0),
                    "tail_ratio": _in_range(1.5, 10.0)},
        "proportional": {"marker_scale": _one_of(_SCALES), "marker_exponent": _in_range(0.1, 3.0),
                         "line_scale": _one_of(_SCALES), "line_exponent": _in_range(0.1, 3.0)},
        "categories": {"readable": _in_range(2, 500, whole=True)},
    }
    out: dict = {"version": _RULES_VERSION}
    for section, fields in sections.items():
        part = _rule_part(value.get(section), f"{where}.{section}", fields)
        if part:
            out[section] = part
    return out


def _alg_id(value: Any) -> str:

    clean = _clean_alg_names([value], "") if isinstance(value, str) else None
    return clean[0] if clean else ""


def _clean_processing_rules(value: Any, where: str) -> dict | None:
    if not _versioned(value, where):
        return None
    out: dict = {"version": _RULES_VERSION}
    defaults: dict = {}
    raw = value.get("geometry_defaults")
    for alg, by_geometry in list(raw.items() if isinstance(raw, dict) else ())[:_MAX_RULE_ROWS]:
        key = _alg_id(alg)
        if not key or not isinstance(by_geometry, dict):
            continue
        rows: dict = {}
        for geometry, row in by_geometry.items():
            spot = f"{where}.geometry_defaults.{key}.{geometry}"
            if geometry not in _GEOMETRIES or not isinstance(row, dict) or not isinstance(row.get("set"), dict):
                continue
            values = {name: v for name, v in row["set"].items()
                      if isinstance(name, str) and _PARAMETER.fullmatch(name)
                      and isinstance(v, (bool, int, float)) and not (isinstance(v, float) and not math.isfinite(v))}
            reason = _rule_text(row.get("reason"), f"{spot}.reason")
            undo = _rule_text(row.get("undo"), f"{spot}.undo")
            if values and reason and undo:
                rows[geometry] = {"set": values, "reason": reason, "undo": undo}
        if rows:
            defaults[key] = rows
    if defaults:
        out["geometry_defaults"] = defaults
    nodata: dict = {}
    raw = value.get("nodata_outputs")
    for alg, row in list(raw.items() if isinstance(raw, dict) else ())[:_MAX_RULE_ROWS]:
        key = _alg_id(alg)
        if not key or not isinstance(row, dict):
            continue
        parameter = row.get("parameter")
        cells = _rule_text(row.get("cells"), f"{where}.nodata_outputs.{key}.cells")
        if isinstance(parameter, str) and _PARAMETER.fullmatch(parameter) and cells:
            nodata[key] = {"parameter": parameter, "cells": cells}
    if nodata:
        out["nodata_outputs"] = nodata
    grids: dict = {}
    raw = value.get("grids")
    for alg, family in list(raw.items() if isinstance(raw, dict) else ())[:_MAX_RULE_ROWS]:
        key = _alg_id(alg)
        if key and family in _GRID_FAMILIES:
            grids[key] = family
    reasons = _rule_part(value.get("grid_reasons"), f"{where}.grid_reasons",
                         dict.fromkeys(_GRID_FAMILIES, _rule_text))
    if grids:
        out["grids"] = grids
        out["grid_reasons"] = reasons
    return out


def _clean_stack_rules(value: Any, where: str) -> dict | None:



    if not _versioned(value, where):
        return None
    out: dict = {"version": _RULES_VERSION}
    order = value.get("order")
    if (isinstance(order, list) and len(order) == len(set(order)) == len(_STACK_KINDS)
            and set(order) == set(_STACK_KINDS)):
        out["order"] = list(order)
    elif order is not None:
        log_warning(f"Server policy {where}.order is not the {len(_STACK_KINDS)} kinds once each, ignored")
    out.update(_rule_part(value, where, {"larger_ratio": _in_range(1.0, 100.0),
                                         "raster_over_filled": _one_of((True, False))}))
    return out


def _clean_context_rules(value: Any, where: str) -> dict | None:
    if not _versioned(value, where):
        return None
    out: dict = {"version": _RULES_VERSION}
    rank = _subset_of(_RANK_SIGNALS)(value.get("rank"), f"{where}.rank") if "rank" in value else None
    if rank is not None:
        out["rank"] = rank
    words = value.get("generic_fields")
    if isinstance(words, list):
        out["generic_fields"] = sorted({w.strip().casefold() for w in words[:_MAX_NAMES]
                                        if isinstance(w, str) and 0 < len(w.strip()) <= 40})
    return out


def _read_services(raw: Any) -> dict:

    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for key, value in list(raw.items())[:100]:
        if not isinstance(key, str):
            continue
        where = f"services.{key}"
        if key in _SERVICE_URLS:
            clean = _clean_service_url(value, where)
        elif key in _SERVICE_TOKENS:
            clean = _clean_tokens(value, where)
        elif key in _SERVICE_HOSTS:
            clean = _clean_hosts(value, where)
        elif key == "hosted_caps":
            clean = _clean_caps(value, where)
        elif key == "wfs_silent_pages":
            clean = _clean_silent_pages(value, where)
        elif key == "open_data_portals":
            clean = _clean_portals(value, where)
        elif key == "osm_routing":
            clean = _clean_osm_routing(value, where)
        elif key in ("code_classes", "code_tables"):
            clean = _clean_code_classes(value, where)
        elif key == "withdrawn_hosts":
            clean = _clean_withdrawn(value, where)
        elif key == "style_rules":
            clean = _clean_style_rules(value, where)
        elif key == "processing_rules":
            clean = _clean_processing_rules(value, where)
        elif key == "context_rules":
            clean = _clean_context_rules(value, where)
        elif key == "stack_rules":
            clean = _clean_stack_rules(value, where)
        else:
            continue
        if clean is not None:
            out[key] = clean
    return out
























_CHECK_TABLES = frozenset({
    "metric_algs",
    "join_algorithms",
    "overlay_algorithms",
    "reduces_rows",
    "preserves_rows",
    "field_to_a_copy",
    "raster_preserves",
    "raster_masked",
    "unsafe_threading",
    "terrain_by_cell",
    "zonal_algorithms",
    "never_grows",
    "slope_algorithms",
    "aspect_algorithms",
    "clips_to_overlay",
    "zonal_histogram",
    "clip_vector",
    "clip_raster",
    "excluded_algorithms",
    "paid_algorithms",
    "raster_keeps_values",
})


_ALG_ID_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789_:"
_MAX_ALG_NAMES = 200
_MAX_ALG_CHARS = 80


def _clean_alg_names(value: Any, where: str) -> list | None:

    if not isinstance(value, list):
        log_warning(f"Server policy {where} is not a list, ignored")
        return None
    out: list = []
    for item in value[:_MAX_ALG_NAMES]:
        name = str(item or "").strip().lower()
        if not name or len(name) > _MAX_ALG_CHARS or name.count(":") != 1:
            continue
        provider, _, alg = name.partition(":")
        if not provider or not alg:
            continue
        if set(name) - set(_ALG_ID_CHARS):
            continue
        if name not in out:
            out.append(name)
    return out or None


def _read_checks(raw: Any) -> dict:

    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for key, value in list(raw.items())[:100]:
        if not isinstance(key, str) or key not in _CHECK_TABLES:
            continue
        clean = _clean_alg_names(value, f"checks.{key}")
        if clean is not None:
            out[key] = clean
    return out












_NAME_TABLES: dict[str, str] = {
    "sql_never": "lower",
    "denied_schemes": "lower",
    "vsi_cloud": "lower",
    "remote_markers": "lower",
    "outside_functions": "keep",
    "outside_function_groups": "keep",
    "off_machine_providers": "keep",
    "live_names": "keep",
    "live_modules": "keep",
    "live_attrs": "keep",
}
_MAX_NAMES = 200
_MAX_NAME_CHARS = 120
_MIN_NAME_CHARS = 2


def _clean_names(value: Any, fold: str, where: str) -> list | None:

    if not isinstance(value, list):
        log_warning(f"Server policy {where} is not a list, ignored")
        return None
    out: list = []
    for item in value[:_MAX_NAMES]:
        if not isinstance(item, str):
            continue
        name = item.strip()
        if fold == "lower":
            name = name.lower()
        if not _MIN_NAME_CHARS <= len(name) <= _MAX_NAME_CHARS or not name.isprintable():
            continue
        if name not in out:
            out.append(name)
    return out or None


def _read_names(raw: Any) -> dict:

    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for key, value in list(raw.items())[:100]:
        if not isinstance(key, str) or key not in _NAME_TABLES:
            continue
        clean = _clean_names(value, _NAME_TABLES[key], f"names.{key}")
        if clean is not None:
            out[key] = clean
    return out


def _clamp_int(value: Any, low: int, high: int, where: str) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        log_warning(f"Server policy {where} is not an integer, ignored")
        return None
    if value < low or value > high:
        log_warning(f"Server policy {where}={value} is outside {low}..{high}, clamped")
    return max(low, min(high, value))


def _clamp_float(value: Any, low: float, high: float, where: str) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        log_warning(f"Server policy {where} is not a number, ignored")
        return None
    if isinstance(value, float) and not math.isfinite(value):
        log_warning(f"Server policy {where} is not finite, ignored")
        return None
    if value < low or value > high:
        log_warning(f"Server policy {where}={value} is outside {low}..{high}, clamped")
    return max(low, min(high, float(value)))
