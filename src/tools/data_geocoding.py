# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

import concurrent.futures
import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from qgis.core import QgsCoordinateReferenceSystem, QgsDistanceArea, QgsPointXY, QgsProject

from ..core import net, place_search, tuning
from ..core.background import run_on_main_thread
from ..core.logger import log_warning
from ..core.tool_registry import tool_error
from .data_common import (
    _CACHE_CATALOG_S,
    _CACHE_GEOCODE_S,
    _GEOCODE_TIMEOUT,
    _MAX_DOWNLOAD_SIZE,
    _MAX_ROUTE_WAYPOINTS,
    _OWN_GEOCODE_TIMEOUT,
    _ROUTE_TIMEOUT,
    _TOTAL_TIMEOUT_FACTOR,
    _USER_AGENT,
    _canvas_viewbox_4326,
    _fold,
    _http_get,
    _is_number,
    _layer_from_geojson_str,
    _osrm_base,
    _project_crs_transform,
    _service,
    _text,
    _viewbox_bounds,
    _viewbox_centre,
)











_DEFAULT_GEOCODE_PROVIDER = "photon"





_GENERIC_PLACE_WORDS = re.compile(
    r"\b(?:city\s+cent(?:re|er)|town\s+cent(?:re|er)|centre[\s-]?ville|downtown|cent(?:re|er)|"
    r"old\s+town|inner\s+city|cbd)\b", re.IGNORECASE)


def _without_generic_place_words(query: str) -> str:

    parts = [part.strip() for part in query.split(",")]
    head = _GENERIC_PLACE_WORDS.sub(" ", parts[0])
    head = re.sub(r"\s{2,}", " ", head).strip(" -,")
    head = re.sub(r"^(?:de|du|d'|of|the)\s+", "", head, flags=re.IGNORECASE)
    if not head:
        return query
    return ", ".join([head] + [part for part in parts[1:] if part])


def _answers_by_name(query: str, results: list) -> bool:






    asked = _fold(query.split(",")[0])
    if not asked:
        return True
    for hit in results:
        label = _fold(hit.get("display_name"))
        if label.startswith(asked) and not label[len(asked):len(asked) + 1].isalnum():
            return True
    return False






_RULES: list = [None, None]


def _rules() -> dict:

    doc = tuning.service_doc("geocode_rules")
    if doc is None:
        return {}
    if _RULES[0] is not doc:
        _RULES[:] = [doc, {
            "tiers": list(doc.get("tiers") or ()),
            "big_places": list(doc.get("big_places") or ()),
            "natural": {cls: frozenset(values) for cls, values in (doc.get("natural_places") or {}).items()},
            "feature_words": frozenset(doc.get("feature_words") or ()),
            "country_words": dict(doc.get("country_words") or {}),
            "local_view_deg": float(doc.get("local_view_deg") or 0.0),
            "candidates": int(doc.get("candidates") or 0),
        }]
    return _RULES[1]




_PLACE_TAGS = ("natural", "water", "waterway", "place:island", "place:archipelago")


def _natural_place(hit: dict) -> bool:
    natural = _rules().get("natural") or {}
    return str(hit.get("osm_value") or "").lower() in natural.get(str(hit.get("class") or "").lower(), ())


def _matches(row: dict, hit: dict) -> bool:

    if "type" in row and str(hit.get("type") or "").lower() != row["type"]:
        return False
    if "osm_value" in row and str(hit.get("osm_value") or "").lower() not in row["osm_value"]:
        return False
    if "class" in row and _fold(hit.get("class")) != row["class"]:
        return False
    return not row.get("natural") or _natural_place(hit)


def _geo_tier(hit: dict) -> int:

    return next((int(row["tier"]) for row in _rules().get("tiers", ()) if _matches(row, hit)), 0)


def _named_as(asked: str, hit: dict, exact: bool = False) -> bool:







    words = re.findall(r"\w+", _fold(asked))
    name = re.findall(r"\w+", _fold(str(hit.get("display_name") or "").split(",")[0]))
    kind = _fold(hit.get("type"))
    feature_words = _rules().get("feature_words", frozenset())
    distinctive = {word for word in words if word not in feature_words and word != kind} or set(words)
    if not distinctive or not distinctive <= set(name):
        return False
    return not exact or ({word for word in name if word not in feature_words} or set(name)) == distinctive


def _big_place(hit: dict) -> bool:

    return any(_matches(row, hit) for row in _rules().get("big_places", ()))



_AROUND = ("city", "county", "state", "country")


def _words(text) -> set:
    return set(re.findall(r"\w+", _fold(text)))


def _own_words(hit: dict) -> set:

    name = (hit.get("_photon") or {}).get("name") or str(hit.get("display_name") or "").split(",")[0]
    return _words(name) - _rules().get("feature_words", frozenset()) - {_fold(hit.get("type"))}


def _holders(hit: dict) -> list:

    photon = hit.get("_photon") or {}
    return [_words(photon[key]) for key in ("city", "county", "state") if photon.get(key)]


def _answers_name(query: str, hit: dict) -> bool:




    words = _words(query.split(",")[0]) - _rules().get("feature_words", frozenset())
    own = _own_words(hit)
    kind = _words(hit.get("type")) | _words(hit.get("osm_value"))
    photon = hit.get("_photon") or {}
    larger = set().union(*(_words(photon.get(key)) for key in ("county", "state", "country")))
    return bool(own) and own <= words and words <= own | kind | larger


def _placed_as_asked(query: str, place: dict, first: dict, wanted) -> bool:



    if wanted and place.get("country_code") not in wanted:
        return False
    if _words((place.get("_photon") or {}).get("name")) in _holders(first):
        return True
    later = _words(",".join(query.split(",")[1:]))
    return len(later & _words(place.get("display_name"))) >= len(later & _words(first.get("display_name")))


def _place_for_object(query: str, first: dict, candidates, wanted):


    holders = _holders(first)
    return next((hit for hit in sorted(candidates, key=lambda hit: _words((hit.get("_photon") or {}).get("name"))
                                       not in holders)
                 if _geo_tier(hit) >= 2 and _answers_name(query, hit) and _placed_as_asked(query, hit, first, wanted)),
                None)


def _surrounding_names(query: str, hits: list) -> list:


    if not hits or re.search(r"\d", query) or _geo_tier(hits[0]) > 0:
        return []
    feature_words = _rules().get("feature_words", frozenset())
    asked = _words(query.split(",")[0]) - feature_words
    photon = hits[0].get("_photon") or {}
    names = []
    for key in ("city", "county", "state"):
        name = photon.get(key, "")
        if name and (_words(name) - feature_words) and _words(name) - feature_words <= asked:
            names.append(", ".join(part for part in (name, photon.get("country", "")) if part))
    return list(dict.fromkeys(names))


def _in_local_view(hit: dict, viewbox) -> bool:
    bounds = _viewbox_bounds(viewbox)
    if not bounds or max(bounds[2] - bounds[0], bounds[3] - bounds[1]) > _rules().get("local_view_deg", 0.0):
        return False
    return bounds[0] <= float(hit["lon"]) <= bounds[2] and bounds[1] <= float(hit["lat"]) <= bounds[3]


def _place_filters() -> list:

    return [[("osm_tag", tag) for tag in _PLACE_TAGS], [("layer", layer) for layer in _PLACE_AREA_LAYERS]]


def _around_place_hits(base: str, query: str, hits: list) -> list:

    lang = "en" if _latin_script(query) else ""
    areas = [("layer", layer) for layer in _PLACE_AREA_LAYERS]
    return [hit for name in _surrounding_names(query, hits)
            for hit in _photon_place_hits_by(base, name, None, lang, areas)]


def _photon_place_hits_by(base: str, query: str, viewbox, lang: str, filters: list) -> list:

    extra = filters + ([("lang", lang)] if lang else [])
    try:
        payload = _geocode_fetch(_photon_forward_url(base, query, _rules().get("candidates") or 5, None, viewbox,
                                                     extra=extra))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log_warning(f"Place lookup by kind failed for {query!r}: {exc}")
        return []
    return [hit for hit in (_parse_photon_forward(payload, True) or []) if _is_number(hit.get("lon"))]


def _photon_place_hits(base: str, query: str, viewbox, lang: str) -> list:

    found: list = []
    for filters in _place_filters():
        found.extend(_photon_place_hits_by(base, query, viewbox, lang, filters))
    return found


def _start_place_lookups(pool, base: str, query: str, viewbox):







    cancel = net.current_cancel_check()

    def ask(lang: str, filters: list) -> list:
        net.set_cancel_check(cancel)
        try:
            return _photon_place_hits_by(base, query, viewbox, lang, filters)
        finally:
            net.set_cancel_check(None)

    asked = {lang: [pool.submit(ask, lang, filters) for filters in _place_filters()] for lang in ("en", "")}
    return lambda lang: [hit for future in asked[lang] for hit in future.result()]


def _better_among(query: str, hits: list):

    asked = query.split(",")[0]
    one_name = "," not in query



    return next((hit for hit in hits[1:]
                 if _geo_tier(hit) >= 3 and not _natural_place(hit)
                 and (_named_as(asked, hit, exact=True) or (one_name and _big_place(hit)))), None)


def _needs_place_lookups(query: str, hits: list, viewbox) -> bool:

    if not hits or re.search(r"\d", query) or not _rules().get("tiers"):
        return False
    first = hits[0]
    tier = _geo_tier(first)
    if tier >= 3 or (tier >= 1 and _in_local_view(first, viewbox)):
        return False
    return _better_among(query, hits) is None


def _rank_places(query: str, hits: list, viewbox, base: str, place_hits=None, wanted=frozenset()) -> list:













    if not hits or re.search(r"\d", query) or not _rules().get("tiers"):
        return hits
    asked = query.split(",")[0]
    first = hits[0]
    tier = _geo_tier(first)
    if tier >= 3 or (tier >= 1 and _in_local_view(first, viewbox)):
        return hits
    better = _better_among(query, hits)
    if place_hits is None:
        def place_hits(lang):
            return _photon_place_hits(base, query, viewbox, lang)
    for lang in ("en", ""):
        if better is not None:
            break
        better = next((hit for hit in place_hits(lang)
                       if _geo_tier(hit) > max(tier, 2) and _named_as(asked, hit, exact=True)), None)
    if better is None and tier == 0:

        better = _place_for_object(query, first, place_hits("en") + place_hits(""), wanted)
        if better is None and _surrounding_names(query, hits):
            better = _place_for_object(query, first, _around_place_hits(base, query, hits), wanted)
    if better is None:
        return hits

    def same(hit):
        return (hit.get("osm_id"), hit.get("lat"), hit.get("lon")) == (better.get("osm_id"), better.get("lat"),
                                                                       better.get("lon"))

    return [better] + [hit for hit in hits if not same(hit)]


def _country_list(country_codes: str) -> list:

    return [code.strip().lower() for code in (country_codes or "").split(",") if code.strip()]


def _join_url(base: str, path: str) -> str:
    return base.rstrip("/") + path


def _label_from(*parts) -> str:






    seen: set = set()
    ordered = []
    for part in parts:
        text = _text(part)
        if text and text not in seen:
            seen.add(text)
            ordered.append(text)
    return ", ".join(ordered)


def _bbox_from_corners(south, north, west, east) -> list | None:

    try:
        return [float(south), float(north), float(west), float(east)]
    except (TypeError, ValueError):
        return None


def _feature_point(feature: dict) -> tuple | None:

    geometry = feature.get("geometry") if isinstance(feature, dict) else None
    coords = geometry.get("coordinates") if isinstance(geometry, dict) else None
    if not isinstance(coords, (list, tuple)) or len(coords) < 2:
        return None
    if not (_is_number(coords[0]) and _is_number(coords[1])):
        return None
    return float(coords[0]), float(coords[1])


def _geojson_features(payload) -> list:
    features = payload.get("features") if isinstance(payload, dict) else None
    return [f for f in features if isinstance(f, dict)] if isinstance(features, list) else []




def _nominatim_forward_url(base, query, limit, country_codes, viewbox) -> str:
    params: dict = {"q": query, "format": "json", "limit": limit, "addressdetails": 1}
    if viewbox:
        params["viewbox"] = viewbox
        params["bounded"] = 0
    if country_codes:
        params["countrycodes"] = country_codes
    return _join_url(base, "/search") + "?" + urllib.parse.urlencode(params)


def _nominatim_reverse_url(base, lat, lon) -> str:
    params = urllib.parse.urlencode({"lat": lat, "lon": lon, "format": "json", "addressdetails": 1})
    return _join_url(base, "/reverse") + "?" + params


def _parse_nominatim_forward(payload, verbose: bool) -> list | None:
    if not isinstance(payload, list):
        return None
    hits = []
    for r in payload:
        if not (isinstance(r, dict) and _is_number(r.get("lat")) and _is_number(r.get("lon"))):
            continue
        hit = {
            "display_name": r.get("display_name"),
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "type": r.get("type"),
            "class": r.get("class"),
        }
        if verbose:


            hit["boundingbox"] = r.get("boundingbox")
            hit["osm_id"] = r.get("osm_id")
            hit["osm_type"] = r.get("osm_type")
            hit["score"] = r.get("importance")
        hits.append(hit)
    return hits


def _parse_nominatim_reverse(payload) -> dict | None:
    if not isinstance(payload, dict) or "error" in payload:
        return None
    if not (_is_number(payload.get("lat")) and _is_number(payload.get("lon"))):
        return None
    return {
        "display_name": payload.get("display_name"),
        "lat": float(payload["lat"]),
        "lon": float(payload["lon"]),
        "address": payload.get("address", {}),
        "osm_id": payload.get("osm_id"),
    }




def _photon_query(query: str) -> str:


    parts = str(query or "").split(",")
    if len(parts) > 1:
        full = (_rules().get("country_words") or {}).get(parts[-1].strip().lower())
        if full:
            parts[-1] = " " + full
    return ",".join(parts)


def _latin_script(text: str) -> bool:

    return all(unicodedata.name(ch, "").startswith("LATIN") for ch in text if ch.isalpha())


def _photon_forward_url(base, query, limit, country_codes, viewbox, layers=(), extra=()) -> str:
    params: dict = {"q": _photon_query(query), "limit": limit}
    if not (layers or extra) and _latin_script(query):



        params["lang"] = "en"
    if viewbox:






        centre = _viewbox_centre(viewbox)
        if centre:
            params["lon"], params["lat"] = f"{centre[0]:.6f}", f"{centre[1]:.6f}"
            params["zoom"] = 10


    pairs = list(params.items()) + [("layer", layer) for layer in layers] + list(extra)
    return _join_url(base, "/api") + "?" + urllib.parse.urlencode(pairs)


def _photon_reverse_url(base, lat, lon) -> str:
    return _join_url(base, "/reverse") + "?" + urllib.parse.urlencode({"lat": lat, "lon": lon, "limit": 1})


def _photon_label(props: dict) -> str:
    street = " ".join(p for p in (_text(props.get("housenumber")), _text(props.get("street"))) if p)
    return _label_from(props.get("name"), street, props.get("postcode"), props.get("city"),
                       props.get("state"), props.get("country"))


def _photon_hit(feature: dict, verbose: bool) -> dict | None:
    point = _feature_point(feature)
    if not point:
        return None
    props = feature.get("properties") or {}
    hit = {
        "display_name": _photon_label(props) or None,
        "lat": point[1],
        "lon": point[0],
        "type": props.get("type") or props.get("osm_value"),
        "class": props.get("osm_key"),

        "_photon": {key: _text(props.get(key)) for key in ("name",) + _AROUND if _text(props.get(key))},
    }
    if props.get("countrycode"):
        hit["country_code"] = str(props["countrycode"]).lower()
    if verbose:
        extent = props.get("extent")
        if isinstance(extent, list) and len(extent) == 4:

            hit["boundingbox"] = _bbox_from_corners(extent[3], extent[1], extent[0], extent[2])
        hit["osm_id"] = props.get("osm_id")

        hit["osm_type"] = props.get("osm_type")
        hit["score"] = None



    hit["osm_value"] = props.get("osm_value")
    return hit


def _parse_photon_forward(payload, verbose: bool) -> list | None:
    if not isinstance(payload, dict):
        return None
    return [hit for hit in (_photon_hit(f, verbose) for f in _geojson_features(payload)) if hit]


def _parse_photon_reverse(payload) -> dict | None:
    features = _geojson_features(payload) if isinstance(payload, dict) else []
    if not features:
        return None
    hit = _photon_hit(features[0], False)
    if not hit:
        return None
    props = features[0].get("properties") or {}
    return {
        "display_name": hit["display_name"],
        "lat": hit["lat"],
        "lon": hit["lon"],
        "address": {k: v for k, v in props.items() if k not in ("extent", "osm_id", "osm_type")},
        "osm_id": props.get("osm_id"),
    }










_BAN_COUNTRY_WORDS = ("france", "fr", "francia", "frankreich")






_ARRONDISSEMENT_CITIES = "paris|lyon|marseille"
_ARRONDISSEMENT_NAMED = re.compile(
    r"^\s*(\d{1,2})\s*(?:er|re|e|ème|eme|è|th|st|nd|rd)?\s+arrondissement\s+(?:de\s+|du\s+|of\s+)?"
    r"(" + _ARRONDISSEMENT_CITIES + r")\s*$", re.IGNORECASE)
_ARRONDISSEMENT_OFFICIAL = re.compile(
    r"^\s*(" + _ARRONDISSEMENT_CITIES + r")\s+(\d{1,2})\s*(?:er|re|e|ème|eme)?\s+arrondissement\s*$",
    re.IGNORECASE)


def _arrondissement_key(text: str):






    value = str(text or "").split(",")[0]
    match = _ARRONDISSEMENT_NAMED.match(value)
    if match:
        return match.group(2).lower(), int(match.group(1))
    match = _ARRONDISSEMENT_OFFICIAL.match(value)
    if match:
        return match.group(1).lower(), int(match.group(2))
    return None


def _ban_query(query: str) -> str:

    parts = [part.strip() for part in str(query or "").split(",")]
    while len(parts) > 1 and parts[-1].lower().strip(". ") in _BAN_COUNTRY_WORDS:
        parts.pop()
    trimmed = ", ".join(part for part in parts if part)
    key = _arrondissement_key(trimmed) if len(parts) == 1 else None
    if key:
        city, number = key
        trimmed = "{} {}{} Arrondissement".format(city.capitalize(), number, "er" if number == 1 else "e")
    return trimmed or str(query or "").strip()


def _ban_forward_url(base, query, limit, country_codes, viewbox) -> str:
    params: dict = {"q": _ban_query(query), "limit": limit, "autocomplete": 0}
    centre = _viewbox_centre(viewbox)
    if centre:

        params["lon"], params["lat"] = f"{centre[0]:.6f}", f"{centre[1]:.6f}"
    return _join_url(base, "/search/") + "?" + urllib.parse.urlencode(params)


def _ban_reverse_url(base, lat, lon) -> str:
    return _join_url(base, "/reverse/") + "?" + urllib.parse.urlencode({"lat": lat, "lon": lon, "limit": 1})


def _ban_hit(feature: dict, verbose: bool) -> dict | None:
    point = _feature_point(feature)
    if not point:
        return None
    props = feature.get("properties") or {}
    hit = {
        "display_name": props.get("label"),
        "lat": point[1],
        "lon": point[0],
        "type": props.get("type"),
        "class": "address",
    }
    if verbose:
        hit["id"] = props.get("id")
        hit["score"] = props.get("score")
    return hit


def _parse_ban_forward(payload, verbose: bool) -> list | None:
    if not isinstance(payload, dict):
        return None
    return [hit for hit in (_ban_hit(f, verbose) for f in _geojson_features(payload)) if hit]


def _parse_ban_reverse(payload) -> dict | None:
    features = _geojson_features(payload) if isinstance(payload, dict) else []
    if not features:
        return None
    hit = _ban_hit(features[0], False)
    if not hit:
        return None
    props = features[0].get("properties") or {}
    return {
        "display_name": hit["display_name"],
        "lat": hit["lat"],
        "lon": hit["lon"],
        "address": {k: v for k, v in props.items() if k not in ("x", "y", "score", "distance", "_type")},
        "osm_id": None,
    }




def _cartociudad_forward_url(base, query, limit, country_codes, viewbox) -> str:


    return _join_url(base, "/candidates") + "?" + urllib.parse.urlencode({"q": query, "limit": limit})


def _cartociudad_reverse_url(base, lat, lon) -> str:
    return _join_url(base, "/reverseGeocode") + "?" + urllib.parse.urlencode({"lat": lat, "lon": lon})


def _cartociudad_point(r) -> tuple | None:






    if not (isinstance(r, dict) and _is_number(r.get("lat")) and _is_number(r.get("lng"))):
        return None
    lat, lon = float(r["lat"]), float(r["lng"])
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0) or (lat == 0.0 and lon == 0.0):
        return None
    return lat, lon


def _cartociudad_located(base: str, payload):







    rows = payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else []
    unplaced = [i for i, r in enumerate(rows) if isinstance(r, dict) and not _cartociudad_point(r)
                and _text(r.get("id")) and _text(r.get("type"))]
    if not unplaced:
        return payload
    cancel = net.current_cancel_check()

    def find(row: dict):
        net.set_cancel_check(cancel)
        try:
            url = _join_url(base, "/find") + "?" + urllib.parse.urlencode({"id": row["id"], "type": row["type"]})
            return _geocode_fetch(url)
        finally:
            net.set_cancel_check(None)

    located, failures = list(rows), []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(unplaced),
                                               thread_name_prefix="geocode-find") as pool:
        asked = {i: pool.submit(find, rows[i]) for i in unplaced}
        for i, future in asked.items():
            try:
                point = _cartociudad_point(future.result())
            except (urllib.error.URLError, OSError, ValueError) as exc:
                log_warning(f"CartoCiudad /find failed for {rows[i].get('type')} {rows[i].get('id')}: {exc}")
                failures.append(exc)
                continue
            if point:
                located[i] = dict(rows[i], lat=point[0], lng=point[1])
    if failures and not any(_cartociudad_point(r) for r in located):
        raise failures[0]
    return located


def _cartociudad_label(r: dict) -> str:
    address = _text(r.get("address"))
    via = _text(r.get("tip_via"))
    if address and via and address.upper().startswith(via.upper()):


        street = address
    else:
        street = " ".join(p for p in (via, address, _text(r.get("portalNumber"))) if p)
    town = _text(r.get("poblacion")) or _text(r.get("muni"))
    return _label_from(street, r.get("postalCode"), town, r.get("province"))


def _cartociudad_hit(r, verbose: bool) -> dict | None:
    point = _cartociudad_point(r)
    if not point:
        return None
    hit = {
        "display_name": _cartociudad_label(r) or None,
        "lat": point[0],
        "lon": point[1],
        "type": r.get("type"),
        "class": "address",
    }
    if verbose:
        hit["id"] = r.get("id")
        hit["score"] = None
    return hit


def _parse_cartociudad_forward(payload, verbose: bool) -> list | None:
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        return None
    return [hit for hit in (_cartociudad_hit(r, verbose) for r in payload) if hit]




_CARTOCIUDAD_ADDRESS_KEYS = (
    "type", "tip_via", "address", "portalNumber",
    "postalCode", "poblacion", "muni", "province", "comunidadAutonoma",
    "refCatastral",
)


def _parse_cartociudad_reverse(payload) -> dict | None:
    if isinstance(payload, list):
        payload = payload[0] if payload else None
    hit = _cartociudad_hit(payload, False)
    if not hit:
        return None
    return {
        "display_name": hit["display_name"],
        "lat": hit["lat"],
        "lon": hit["lon"],
        "address": {key: payload[key] for key in _CARTOCIUDAD_ADDRESS_KEYS if _text(payload.get(key))},
        "osm_id": None,
    }




def _pelias_forward_url(base, query, limit, country_codes, viewbox) -> str:
    params: dict = {"text": query, "size": limit}
    bounds = _viewbox_bounds(viewbox)
    if bounds:
        west, south, east, north = bounds
        params["boundary.rect.min_lon"] = f"{west:.6f}"
        params["boundary.rect.min_lat"] = f"{south:.6f}"
        params["boundary.rect.max_lon"] = f"{east:.6f}"
        params["boundary.rect.max_lat"] = f"{north:.6f}"
    countries = _country_list(country_codes)
    if countries:
        params["boundary.country"] = ",".join(countries).upper()
    return _join_url(base, "/search") + "?" + urllib.parse.urlencode(params)


def _pelias_reverse_url(base, lat, lon) -> str:
    params: dict = {"point.lat": lat, "point.lon": lon, "size": 1}
    return _join_url(base, "/reverse") + "?" + urllib.parse.urlencode(params)


def _pelias_hit(feature: dict, verbose: bool) -> dict | None:
    point = _feature_point(feature)
    if not point:
        return None
    props = feature.get("properties") or {}
    hit = {
        "display_name": props.get("label"),
        "lat": point[1],
        "lon": point[0],
        "type": props.get("layer"),
        "class": props.get("source"),
    }
    if verbose:
        bbox = feature.get("bbox")
        if isinstance(bbox, list) and len(bbox) == 4:
            hit["boundingbox"] = _bbox_from_corners(bbox[1], bbox[3], bbox[0], bbox[2])
        hit["id"] = props.get("gid")
        hit["score"] = props.get("confidence")
    return hit


def _parse_pelias_forward(payload, verbose: bool) -> list | None:
    if not isinstance(payload, dict):
        return None
    return [hit for hit in (_pelias_hit(f, verbose) for f in _geojson_features(payload)) if hit]


def _parse_pelias_reverse(payload) -> dict | None:
    features = _geojson_features(payload) if isinstance(payload, dict) else []
    if not features:
        return None
    hit = _pelias_hit(features[0], False)
    if not hit:
        return None
    return {
        "display_name": hit["display_name"],
        "lat": hit["lat"],
        "lon": hit["lon"],
        "address": features[0].get("properties") or {},
        "osm_id": None,
    }




_GEOCODE_PROVIDERS = {
    "nominatim": {
        "name": "OpenStreetMap Nominatim",
        "licence": "ODbL 1.0", "attribution": "© OpenStreetMap contributors",
        "service": "nominatim",
        "forward_url": _nominatim_forward_url,
        "reverse_url": _nominatim_reverse_url,
        "parse_forward": _parse_nominatim_forward,
        "parse_reverse": _parse_nominatim_reverse,
    },
    "photon": {
        "name": "Photon (TerraLab, OpenStreetMap)",
        "licence": "ODbL 1.0", "attribution": "© OpenStreetMap contributors",
        "service": "photon",
        "forward_url": _photon_forward_url,
        "reverse_url": _photon_reverse_url,
        "parse_forward": _parse_photon_forward,
        "parse_reverse": _parse_photon_reverse,
    },
    "ban": {
        "name": "Base Adresse Nationale (France)",
        "licence": "Licence Ouverte / Etalab 2.0", "attribution": "Base Adresse Nationale",
        "service": "ban",
        "forward_url": _ban_forward_url,
        "reverse_url": _ban_reverse_url,
        "parse_forward": _parse_ban_forward,
        "parse_reverse": _parse_ban_reverse,
    },
    "cartociudad": {
        "name": "CartoCiudad (IGN Spain)",
        "licence": "CC BY 4.0", "attribution": "CartoCiudad, Instituto Geográfico Nacional de España",
        "service": "cartociudad",
        "forward_url": _cartociudad_forward_url,
        "reverse_url": _cartociudad_reverse_url,
        "locate": _cartociudad_located,
        "parse_forward": _parse_cartociudad_forward,
        "parse_reverse": _parse_cartociudad_reverse,
    },



    "pelias": {
        "name": "Pelias",
        "forward_url": _pelias_forward_url,
        "reverse_url": _pelias_reverse_url,
        "parse_forward": _parse_pelias_forward,
        "parse_reverse": _parse_pelias_reverse,
    },
}

_GEOCODE_PROVIDER_IDS = tuple(_GEOCODE_PROVIDERS)


class _NoEndpoint(ValueError):


    def __init__(self, provider_name: str):
        super().__init__(f"{provider_name} has no public endpoint.")
        self.provider_name = provider_name


def _provider_refusal(error: ValueError) -> dict:

    if isinstance(error, _NoEndpoint):
        return tool_error(str(error), "INVALID_ARGS", hint="geocode_endpoint_missing",
                          provider=error.provider_name)
    return tool_error(str(error), "INVALID_ARGS")


def _resolve_geocode_provider(args: dict) -> tuple:







    provider_id = _text(args.get("provider")).lower() or _DEFAULT_GEOCODE_PROVIDER
    provider = _GEOCODE_PROVIDERS.get(provider_id)
    if provider is None:
        raise ValueError(f"Unknown geocoding provider '{provider_id}'. "
                         f"Providers: {', '.join(_GEOCODE_PROVIDER_IDS)}.")

    base = _text(args.get("endpoint")) or (_service(provider["service"]) if provider.get("service") else "")
    if not base:
        raise _NoEndpoint(provider["name"])
    return provider_id, provider, base















_BACKEND_GEO_TIMEOUT = 25


def _fallback_provider(answer: dict) -> str:





    name = str((answer or {}).get("provider") or "").strip()
    return name[:40] if re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", name) else "terralab_fallback"


def _fallback_attribution(answer: dict) -> str:
    notice = " ".join(str((answer or {}).get("attribution") or "").split())
    return notice[:300] or "credit the data source the AI Agent backend used."


def _backend_geo_url(path: str) -> str:

    from ..core.settings import Settings

    parts = urllib.parse.urlsplit(Settings().server_url)
    scheme = "http" if parts.scheme == "ws" else "https"
    return f"{scheme}://{parts.netloc}{path}"


def _backend_geo(path: str, params: dict, cache_ttl: float = 0.0) -> dict | None:









    from ..core.settings import Settings

    key = Settings().activation_key
    if not key:
        return None
    try:
        url = f"{_backend_geo_url(path)}?{urllib.parse.urlencode(params, doseq=True)}"
        request = urllib.request.Request(url, headers={
            "User-Agent": _USER_AGENT, "Accept": "application/json",
            "Authorization": f"Bearer {key}"})
        raw = net.fetch(request, timeout=_BACKEND_GEO_TIMEOUT, max_bytes=_MAX_DOWNLOAD_SIZE,
                        total_timeout=_BACKEND_GEO_TIMEOUT * _TOTAL_TIMEOUT_FACTOR,
                        cache_ttl=cache_ttl).body
        answer = json.loads(raw)
        return answer if isinstance(answer, dict) and not answer.get("error") else None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log_warning(f"backend geo fallback failed: {exc}")
        return None


def _is_own_geocode(url: str) -> bool:





    try:
        own = urllib.parse.urlsplit(tuning.service_url("photon", "")).hostname or ""
        here = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return False
    return bool(own) and here.lower() == own.lower()


def _geocode_fetch(url: str):











    budget = tuning.limit("net", "geocode_timeout_s", _GEOCODE_TIMEOUT)
    if _is_own_geocode(url):
        budget = min(budget, tuning.limit("net", "own_geocode_timeout_s", _OWN_GEOCODE_TIMEOUT))
    data = _http_get(url, timeout=budget, cache_ttl=_CACHE_GEOCODE_S)
    if not data.strip():
        return None
    return json.loads(data)








_BACKEND_GEOCODE_BATCH_MAX = 50


def _geocode_one_address(address: str, own_host: bool = True) -> tuple:






    own_failed = False
    if own_host:
        try:
            url = _photon_forward_url(_service("photon"), address, 1, None, None)
            payload = _geocode_fetch(url)
            features = payload.get("features") if isinstance(payload, dict) else None
            return (_photon_hit(features[0], False) if features else None), False
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log_warning(f"geocode host failed on a batch, falling back to the backend: {exc}")
            own_failed = True
    answer = _backend_geo("/geo/geocode", {"q": address, "limit": 1}, cache_ttl=_CACHE_GEOCODE_S)
    hits = (answer or {}).get("results") or []
    return (dict(hits[0]) if hits else None), own_failed


def _place_search(query: str, limit: int, base: str, verbose: bool):


    rounds = place_search.search(query, limit, base, tuning.service_url("ban", ""))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="geocode-places") as pool:
        try:
            urls = next(rounds)
            while True:
                futures = [(url, pool.submit(_geocode_fetch, url)) for url in urls]
                answers = {}
                for url, future in futures:
                    try:
                        answers[url] = future.result()
                    except (urllib.error.URLError, OSError, ValueError):
                        answers[url] = None
                urls = rounds.send(answers)
        except StopIteration as done:
            places = done.value or []
        except place_search.Unavailable:
            return None

    local_names = not _latin_script(query)
    hits = []
    for place in places:
        ban = place.get("ban")
        if ban is None:
            hit = _photon_hit(place.get("local_feature") if local_names and place.get("local_feature")
                              else place["feature"], verbose)
        else:
            hit = _ban_hit(ban, verbose)
            if hit:
                hit.update(display_name=place["address"], country_code="fr", provider="ban")
        if hit:
            hits.append(hit)
    return hits


def _geocode(args: dict) -> dict:




    query = _without_generic_place_words(str(args.get("query") or "").strip())
    if not query:
        return tool_error("The place to look up is empty.", "INVALID_ARGS", hint="geocode_query_empty")



    try:
        limit = max(1, min(int(args.get("limit") or 5), 10))
    except (TypeError, ValueError):
        return tool_error(f"limit must be a whole number from 1 to 10, got {args.get('limit')!r}.",
                          "INVALID_ARGS", hint="geocode_limit")
    country_codes = (args.get("country_codes") or "").strip()

    try:
        provider_id, provider, base = _resolve_geocode_provider(args)
    except ValueError as e:
        return _provider_refusal(e)



    try:
        viewbox = run_on_main_thread(_canvas_viewbox_4326)
    except Exception:  # nosec B110
        viewbox = None



    fetched = max(limit, _rules().get("candidates") or 0) if provider_id == "photon" else limit
    url = provider["forward_url"](base, query, fetched, country_codes, viewbox)

    def ask(address: str):

        found = _geocode_fetch(address)
        return provider["locate"](base, found) if "locate" in provider and found is not None else found



    unbiased = None
    if provider_id == "photon" and viewbox is None:
        unbiased = _place_search(str(args.get("query") or "").strip(), max(limit, 10), base,
                                 bool(args.get("verbose", False)))

    fell_back = False
    if unbiased is not None:
        failure, results = None, unbiased
    elif provider_id == "photon" and viewbox is None:

        failure, results, fell_back = tool_error("Geocoding request failed: Photon did not answer."), [], True
    else:
        try:
            payload = ask(url)
        except urllib.error.HTTPError as e:




            payload = None
            if e.code == 400 and viewbox:
                try:
                    payload = ask(provider["forward_url"](base, query, limit, country_codes, None))
                except (urllib.error.URLError, OSError, ValueError):


                    payload = None
            if payload is None:
                failure = tool_error(f"Geocoding request failed: {e}")
                fell_back = True
            else:
                failure = None
        except (urllib.error.URLError, OSError) as e:
            broken = net.describe_failure(e)
            failure = (tool_error(f"Geocoding request failed: {broken}", net.NETWORK_ERROR) if broken
                       else tool_error(f"Geocoding request failed: {e}"))
            payload, fell_back = None, True
        except ValueError:
            failure = tool_error("The geocoding answer was unreadable.", hint="geocode_unreadable")
            payload, fell_back = None, True
        else:
            failure = None

        results = provider["parse_forward"](payload, bool(args.get("verbose", False))) if payload is not None else []
        if results is None:
            failure = tool_error(f"Geocoding service error: {str(payload)[:200]}")
            results, fell_back = [], True



    wanted = set(_country_list(args.get("country_codes") or ""))

    def _keep_wanted(hits: list) -> list:
        if not (wanted and hits):
            return hits
        kept = [hit for hit in hits if not hit.get("country_code") or hit["country_code"] in wanted]
        return kept or hits

    results = _keep_wanted(results)





    check_plain = bool(viewbox and results and not fell_back and not _answers_by_name(query, results))
    ranks = provider_id == "photon" and bool(results) and not fell_back and unbiased is None



    pool = None
    place_hits = None
    if ranks and (check_plain or _needs_place_lookups(query, results, viewbox)):
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=2 * len(_place_filters()),
                                                     thread_name_prefix="geocode-kinds")
        place_hits = _start_place_lookups(pool, base, query, viewbox)
    try:
        if check_plain:
            try:
                plain = provider["parse_forward"](
                    ask(provider["forward_url"](base, query, fetched, country_codes, None)),
                    bool(args.get("verbose", False)))
            except (urllib.error.URLError, OSError, ValueError):
                plain = None
            if plain and _answers_by_name(query, _keep_wanted(plain)):
                results = _keep_wanted(plain)

        if ranks:


            results = _rank_places(query, results, viewbox, base, place_hits, wanted)
    finally:
        if pool is not None:
            pool.shutdown(wait=False)

    if fell_back:



        answer = _backend_geo("/geo/geocode", {"q": query, "limit": limit, "country": country_codes},
                              cache_ttl=_CACHE_GEOCODE_S)
        hits = (answer or {}).get("results") or []
        if not hits:
            return failure
        results, provider_id = hits, _fallback_provider(answer)
    output = results[:limit]
    if not output:
        return {"results": [], "count": 0, "provider": provider_id,
                "message": f"No results found for '{query}'"}

    verbose = bool(args.get("verbose", False))
    for hit in output:
        hit.pop("_photon", None)
        if not verbose:
            hit.pop("osm_value", None)


    project_crs = None
    try:
        transformed = run_on_main_thread(
            _project_crs_transform, [(r["lon"], r["lat"]) for r in output]
        )
        if transformed:
            authid, coords = transformed
            project_crs = authid
            for r, (x, y) in zip(output, coords):
                r["x_project_crs"] = x
                r["y_project_crs"] = y
                if verbose:
                    r["project_crs"] = authid
    except Exception:  # nosec B110
        pass

    out = {"results": output, "count": len(output), "provider": provider_id}
    if fell_back:
        out["note"] = ("The geocoder you asked for did not answer, so this came from the AI Agent backend's "
                       f"fallback. Attribution: {_fallback_attribution(answer)}")
    elif provider_id == "photon" and not re.search(r"\d", query) and not any(
            set(re.findall(r"\w+", _fold(query.split(",")[0])))
            <= set(re.findall(r"\w+", _fold(str(hit.get("display_name") or "").split(",")[0])))
            for hit in output):




        out["approximate"] = True
        out["note"] = f"No result carries the name {query.split(',')[0].strip()!r} as written."
        out["note_hint"] = "geocode_approximate"
        out["note_name"] = query.split(",")[0].strip()



    coded = [str(hit.get("country_code") or "") for hit in output if hit.get("country_code")]
    if wanted and coded and not any(code in wanted for code in coded):

        out["warning"] = f"No result lies in {', '.join(sorted(wanted)).upper()}."
        out["warning_hint"] = "geocode_none_in_country"
        out["warning_countries"] = ", ".join(sorted(wanted)).upper()
    if project_crs:

        out["project_crs"] = project_crs
    layer_name = str(args.get("layer_name") or "").strip()
    if layer_name:
        try:
            out["layer"] = run_on_main_thread(_add_geocode_layer, layer_name, query, output[0])

            if fell_back:
                out["layer"]["attribution"] = _fallback_attribution(answer)
            else:
                out["layer"].update({key: provider[key] for key in ("licence", "attribution") if provider.get(key)})
        except Exception as e:  # noqa: BLE001
            out["layer_error"] = f"Could not add the point layer: {e}"
    return out


def _add_geocode_layer(name: str, query: str, hit: dict) -> dict:

    from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsProject, QgsVectorLayer

    layer = QgsVectorLayer(
        "Point?crs=EPSG:4326&field=name:string(200)&field=display_name:string(500)&field=lat:double&field=lon:double",
        name, "memory",
    )
    feature = QgsFeature(layer.fields())
    feature.setAttributes([query, hit.get("display_name"), hit["lat"], hit["lon"]])
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(hit["lon"], hit["lat"])))
    layer.dataProvider().addFeatures([feature])
    layer.updateExtents()
    QgsProject.instance().addMapLayer(layer)
    try:
        from qgis.utils import iface
        canvas = iface.mapCanvas()
        canvas.setExtent(canvas.mapSettings().layerExtentToOutputExtent(layer, layer.extent()).buffered(0))
        canvas.zoomScale(5000)
        canvas.refresh()
    except Exception:  # nosec B110
        pass
    return {"layer_id": layer.id(), "layer_name": name, "feature_count": 1, "crs": "EPSG:4326",
            "added_to_project": True}


def _reverse_geocode(args: dict) -> dict:
    lat = args["lat"]
    lon = args["lon"]

    try:
        provider_id, provider, base = _resolve_geocode_provider(args)
    except ValueError as e:
        return _provider_refusal(e)

    url = provider["reverse_url"](base, lat, lon)

    try:
        payload = _geocode_fetch(url)
    except (urllib.error.URLError, OSError) as e:
        payload, failure = None, tool_error(f"Reverse geocoding failed: {e}")
    except ValueError:
        payload = None
        failure = tool_error("Reverse geocoding gave an unreadable answer.", hint="geocode_unreadable")
    else:
        failure = None

    out = provider["parse_reverse"](payload) if payload is not None else None
    if out is None:





        answer = _backend_geo("/geo/reverse", {"lat": lat, "lon": lon}, cache_ttl=_CACHE_GEOCODE_S)
        if not (answer or {}).get("display_name"):
            return failure or tool_error(f"{provider['name']} found no address at that point.")
        out, provider_id = dict(answer), _fallback_provider(answer)
    out["provider"] = provider_id


    try:
        transformed = run_on_main_thread(
            _project_crs_transform, [(out["lon"], out["lat"])]
        )
        if transformed:
            authid, coords = transformed
            out["x_project_crs"] = coords[0][0]
            out["y_project_crs"] = coords[0][1]
            out["project_crs"] = authid
    except Exception:  # nosec B110
        pass

    return out






_PLACE_AREA_LAYERS = ("country", "state", "county", "city")


def _get_route(args: dict) -> dict:
    waypoints = args["waypoints"]
    profile = args.get("profile", "driving")
    layer_name = args.get("layer_name", "Route")

    if len(waypoints) < 2:
        return tool_error("At least 2 waypoints required", "INVALID_ARGS")
    if len(waypoints) > _MAX_ROUTE_WAYPOINTS:
        return tool_error(f"Too many waypoints (max {_MAX_ROUTE_WAYPOINTS} per route)", "INVALID_ARGS")

    coords_str = ";".join(f"{wp['lon']},{wp['lat']}" for wp in waypoints)
    base = _osrm_base(profile)
    url = f"{base}/{coords_str}?overview=full&geometries=geojson&steps=false"

    _osrm_hint = "osrm_failed"
    failure = None
    result = {}
    try:
        budget = tuning.limit("net", "route_timeout_s", _ROUTE_TIMEOUT)
        data = _http_get(url, timeout=budget, cache_ttl=_CACHE_CATALOG_S)
    except (urllib.error.URLError, OSError) as e:
        reason = str(e) or repr(e) or "network timeout"
        failure = tool_error(f"OSRM routing request failed: {reason}", "OSRM_UNREACHABLE",
                             hint=_osrm_hint, waypoints=len(waypoints))
    else:
        try:
            result = json.loads(data)
        except (ValueError, TypeError) as e:
            snippet = (data[:200].decode("utf-8", "replace") if isinstance(data, (bytes, bytearray))
                       else str(data)[:200])
            failure = tool_error(f"OSRM returned a non-JSON response ({e}): {snippet}", "OSRM_BAD_RESPONSE",
                                 hint=_osrm_hint, waypoints=len(waypoints))
        else:
            if result.get("code") != "Ok":
                detail = result.get("message") or result.get("code") or "unknown OSRM error"
                failure = tool_error(f"OSRM error: {detail}", "OSRM_ERROR",
                                     hint=_osrm_hint, waypoints=len(waypoints))
            elif not result.get("routes"):
                failure = tool_error("No route found between the given waypoints", "INVALID_ARGS",
                                     hint=_osrm_hint, waypoints=len(waypoints))

    fell_back = failure is not None
    if fell_back:



        answer = _backend_geo("/geo/route", {"point": [f"{wp['lat']},{wp['lon']}" for wp in waypoints],
                                             "profile": profile}, cache_ttl=_CACHE_CATALOG_S)
        geometry = (answer or {}).get("geometry")
        if not geometry:
            return failure
        distance_km = round(float(answer["distance_m"]) / 1000, 2)
        duration_min = round(float(answer["duration_s"]) / 60, 1)
    else:
        route = result["routes"][0]
        distance_km = round(route["distance"] / 1000, 2)
        duration_min = round(route["duration"] / 60, 1)
        geometry = route["geometry"]

    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "distance_km": distance_km,
                "duration_min": duration_min,
                "profile": profile,
            },
        }],
    }
    geojson_str = json.dumps(geojson)
    fallback_credit = _fallback_attribution(answer) if fell_back else ""

    def _create():
        layer = _layer_from_geojson_str(geojson_str, layer_name, "route")
        if not layer.isValid():
            return tool_error("Failed to create route layer")
        QgsProject.instance().addMapLayer(layer)
        return {
            "layer_name": layer.name(),


            "layer_id": layer.id(),
            "distance_km": distance_km,
            "duration_min": duration_min,
            "profile": profile,
            "waypoint_count": len(waypoints),
            "note": ("The public OSRM demo server did not answer, so this route came from the AI Agent "
                     f"backend's fallback. Attribution: {fallback_credit}"
                     if fell_back else
                     "Routed by the public OSRM demo server run by FOSSGIS: a demonstration service with no "
                     "guarantee, not for production use. Credit OSRM and OpenStreetMap contributors."),
        }

    return run_on_main_thread(_create, timeout=10)


def _measure_distance(args: dict) -> dict:


    for lon_key, lat_key in (("from_lon", "from_lat"), ("to_lon", "to_lat")):
        lon, lat = float(args[lon_key]), float(args[lat_key])
        if not (-180.0 <= lon <= 360.0 and -90.0 <= lat <= 90.0):
            return tool_error(
                f"measure_distance reads longitude and latitude in EPSG:4326, and {lon_key}={lon:g}, "
                f"{lat_key}={lat:g} is outside -180 to 180 and -90 to 90: projected units, not degrees.",
                "INVALID_ARGS", hint="distance_not_degrees", lon=lon, lat=lat)
    d = QgsDistanceArea()
    d.setEllipsoid("WGS84")
    d.setSourceCrs(
        QgsCoordinateReferenceSystem("EPSG:4326"),
        QgsProject.instance().transformContext(),
    )

    p1 = QgsPointXY(args["from_lon"], args["from_lat"])
    p2 = QgsPointXY(args["to_lon"], args["to_lat"])

    dist_m = d.measureLine(p1, p2)
    return {
        "distance_m": round(dist_m, 1),
        "distance_km": round(dist_m / 1000, 3),
    }




__all__ = [
    "_BACKEND_GEOCODE_BATCH_MAX",
    "_DEFAULT_GEOCODE_PROVIDER",
    "_GEOCODE_PROVIDERS",
    "_GEOCODE_PROVIDER_IDS",
    "_PLACE_AREA_LAYERS",
    "_add_geocode_layer",
    "_arrondissement_key",
    "_backend_geo",
    "_geocode",
    "_geocode_fetch",
    "_geocode_one_address",
    "_get_route",
    "_http_get",
    "_measure_distance",
    "_parse_cartociudad_forward",
    "_parse_nominatim_forward",
    "_parse_photon_forward",
    "_photon_forward_url",
    "_resolve_geocode_provider",
    "_reverse_geocode",
    "_without_generic_place_words",
]
