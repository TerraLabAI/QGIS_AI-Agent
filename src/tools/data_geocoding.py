# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Geocoding providers (Nominatim, Photon, BAN, CartoCiudad, Pelias), place ranking, the backend fallback, and the geocode/reverse-geocode."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from qgis.core import QgsCoordinateReferenceSystem, QgsDistanceArea, QgsPointXY, QgsProject

from ..core import net, tuning
from ..core.logger import log_warning
from .data_common import (
    _CACHE_CATALOG_S,
    _CACHE_GEOCODE_S,
    _GEOCODE_TIMEOUT,
    _MAX_DOWNLOAD_SIZE,
    _MAX_ROUTE_WAYPOINTS,
    _NOMINATIM_URL,
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
    _run_on_main_thread,
    _service,
    _text,
    _viewbox_bounds,
    _viewbox_centre,
)










_PHOTON_URL = "https://geocode.terra-lab.ai"





_BAN_URL = "https://data.geopf.fr/geocodage"
_CARTOCIUDAD_URL = "https://www.cartociudad.es/geocoder/api/geocoder"

_DEFAULT_GEOCODE_PROVIDER = "photon"





_GENERIC_PLACE_WORDS = re.compile(
    r"\b(?:city\s+cent(?:re|er)|town\s+cent(?:re|er)|centre[\s-]?ville|downtown|cent(?:re|er)|"
    r"old\s+town|inner\s+city|cbd)\b", re.IGNORECASE)


def _without_generic_place_words(query: str) -> str:
    """The query minus its filler, when a place name is left after it."""
    parts = [part.strip() for part in query.split(",")]
    head = _GENERIC_PLACE_WORDS.sub(" ", parts[0])
    head = re.sub(r"\s{2,}", " ", head).strip(" -,")
    head = re.sub(r"^(?:de|du|d'|of|the)\s+", "", head, flags=re.IGNORECASE)
    if not head:
        return query
    return ", ".join([head] + [part for part in parts[1:] if part])


def _answers_by_name(query: str, results: list) -> bool:
    """True when a hit is actually named what the query asked for."""





    asked = _fold(query.split(",")[0])
    if not asked:
        return True
    for hit in results:
        label = _fold(hit.get("display_name"))
        if label.startswith(asked) and not label[len(asked):len(asked) + 1].isalnum():
            return True
    return False







_NATURAL_PLACES = {
    "natural": frozenset({"peak", "volcano", "massif", "mountain_range", "ridge", "glacier", "water", "bay",
                          "strait", "cape", "peninsula", "valley", "plateau", "desert"}),
    "water": frozenset({"lake", "reservoir", "lagoon", "river"}),
    "waterway": frozenset({"river"}),
    "place": frozenset({"island", "archipelago"}),
}


_PLACE_TAGS = ("natural", "water", "waterway", "place:island", "place:archipelago")


_LOCAL_VIEW_DEG = 2.0

_PLACE_CANDIDATES = 10


def _natural_place(hit: dict) -> bool:
    return str(hit.get("osm_value") or "").lower() in _NATURAL_PLACES.get(str(hit.get("class") or "").lower(), ())


def _geo_tier(hit: dict) -> int:
    """How much of a place a Photon hit is (the scale above)."""
    kind = str(hit.get("type") or "").lower()
    if kind == "country":
        return 5
    if kind == "state" or _natural_place(hit):
        return 4
    if kind == "county" or (kind == "city" and str(hit.get("osm_value") or "").lower() in ("city", "town")):
        return 3
    if kind == "city":
        return 2
    return 1 if kind in ("district", "locality") else 0




_FEATURE_WORDS = frozenset({"mount", "mont", "monte", "mt", "mountain", "lake", "lac", "lago", "see", "loch",
                            "river", "rio", "riviere", "fleuve", "isla", "ile", "island", "isola", "volcan",
                            "volcano", "vulcano", "pic", "pico", "peak", "cerro", "nevado", "le", "la", "the"})


def _named_as(asked: str, hit: dict, exact: bool = False) -> bool:
    """Every word of the name asked for is a word of the hit's own name ("Everest" of "Mount Everest")."""





    words = re.findall(r"\w+", _fold(asked))
    name = re.findall(r"\w+", _fold(str(hit.get("display_name") or "").split(",")[0]))
    distinctive = {word for word in words if word not in _FEATURE_WORDS} or set(words)
    if not distinctive or not distinctive <= set(name):
        return False
    return not exact or ({word for word in name if word not in _FEATURE_WORDS} or set(name)) == distinctive


def _big_place(hit: dict) -> bool:
    """A country, a state or a city proper: a place Photon matched under a name in another language (Londres is London, Munich is München) is."""

    kind = str(hit.get("type") or "").lower()
    return kind in ("country", "state") or (kind == "city" and str(hit.get("osm_value") or "").lower() == "city")


def _in_local_view(hit: dict, viewbox) -> bool:
    bounds = _viewbox_bounds(viewbox)
    if not bounds or max(bounds[2] - bounds[0], bounds[3] - bounds[1]) > _LOCAL_VIEW_DEG:
        return False
    return bounds[0] <= float(hit["lon"]) <= bounds[2] and bounds[1] <= float(hit["lat"]) <= bounds[3]


def _photon_place_hits(base: str, query: str, viewbox, lang: str) -> list:
    """Photon's hits for *query* among natural features, then among countries, states, counties and cities."""
    found: list = []
    for filters in ([("osm_tag", tag) for tag in _PLACE_TAGS], [("layer", layer) for layer in _PLACE_AREA_LAYERS]):
        extra = filters + ([("lang", lang)] if lang else [])
        try:
            payload = _geocode_fetch(_photon_forward_url(base, query, _PLACE_CANDIDATES, None, viewbox, extra=extra))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log_warning(f"Place lookup by kind failed for {query!r}: {exc}")
            continue
        found.extend(hit for hit in (_parse_photon_forward(payload, True) or []) if _is_number(hit.get("lon")))
    return found


def _rank_places(query: str, hits: list, viewbox, base: str) -> list:
    """*hits* with the place a name means first, ranked the way a geocoding client ranks places."""








    if not hits or re.search(r"\d", query):
        return hits
    asked = query.split(",")[0]
    first = hits[0]
    tier = _geo_tier(first)
    if tier >= 3 or (tier >= 1 and _in_local_view(first, viewbox)):
        return hits



    one_name = "," not in query
    better = next((hit for hit in hits[1:]
                   if _geo_tier(hit) >= 3 and not _natural_place(hit)
                   and (_named_as(asked, hit, exact=True) or (one_name and _big_place(hit)))), None)
    for lang in ("en", ""):
        if better is not None:
            break
        better = next((hit for hit in _photon_place_hits(base, query, viewbox, lang)
                       if _geo_tier(hit) > max(tier, 2) and _named_as(asked, hit, exact=True)), None)
    if better is None:
        return hits

    def same(hit):
        return (hit.get("osm_id"), hit.get("lat"), hit.get("lon")) == (better.get("osm_id"), better.get("lat"),
                                                                       better.get("lon"))

    return [better] + [hit for hit in hits if not same(hit)]


def _country_list(country_codes: str) -> list:
    """The comma-separated ISO 3166-1 alpha-2 argument as a clean lowercase list."""
    return [code.strip().lower() for code in (country_codes or "").split(",") if code.strip()]


def _join_url(base: str, path: str) -> str:
    return base.rstrip("/") + path


def _label_from(*parts) -> str:
    """Join address pieces into one display line, dropping blanks and repeats."""





    seen: set = set()
    ordered = []
    for part in parts:
        text = _text(part)
        if text and text not in seen:
            seen.add(text)
            ordered.append(text)
    return ", ".join(ordered)


def _bbox_from_corners(south, north, west, east) -> list | None:
    """A bounding box in Nominatim's [south, north, west, east] order, or None."""
    try:
        return [float(south), float(north), float(west), float(east)]
    except (TypeError, ValueError):
        return None


def _feature_point(feature: dict) -> tuple | None:
    """The (lon, lat) of a GeoJSON Point feature, or None when it is not one."""
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








_PHOTON_COUNTRY_WORDS = {"usa": "United States", "us": "United States", "u.s.": "United States",
                         "u.s.a.": "United States", "uk": "United Kingdom", "u.k.": "United Kingdom",
                         "uae": "United Arab Emirates"}


def _photon_query(query: str) -> str:
    """The query with a trailing country abbreviation spelt out."""
    parts = str(query or "").split(",")
    if len(parts) > 1:
        full = _PHOTON_COUNTRY_WORDS.get(parts[-1].strip().lower())
        if full:
            parts[-1] = " " + full
    return ",".join(parts)


def _photon_forward_url(base, query, limit, country_codes, viewbox, layers=(), extra=()) -> str:
    params: dict = {"q": _photon_query(query), "limit": limit}
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
    }
    if props.get("countrycode"):
        hit["country_code"] = str(props["countrycode"]).lower()
    if verbose:
        extent = props.get("extent")
        if isinstance(extent, list) and len(extent) == 4:

            hit["boundingbox"] = _bbox_from_corners(extent[3], extent[1], extent[0], extent[2])
        hit["osm_id"] = props.get("osm_id")
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
    """``("lyon", 2)`` for either spelling of an arrondissement municipal, else None."""





    value = str(text or "").split(",")[0]
    match = _ARRONDISSEMENT_NAMED.match(value)
    if match:
        return match.group(2).lower(), int(match.group(1))
    match = _ARRONDISSEMENT_OFFICIAL.match(value)
    if match:
        return match.group(1).lower(), int(match.group(2))
    return None


def _ban_query(query: str) -> str:
    """The query without a trailing country, unless that is all there is."""
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


    if not (isinstance(r, dict) and _is_number(r.get("lat")) and _is_number(r.get("lng"))):
        return None
    lat, lon = float(r["lat"]), float(r["lng"])
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    hit = {
        "display_name": _cartociudad_label(r) or None,
        "lat": lat,
        "lon": lon,
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


def _parse_cartociudad_reverse(payload) -> dict | None:
    if isinstance(payload, list):
        payload = payload[0] if payload else None
    hit = _cartociudad_hit(payload, False)
    if not hit:
        return None
    fields = ("type", "tip_via", "address", "portalNumber", "postalCode", "poblacion", "muni",
              "province", "comunidadAutonoma", "refCatastral")
    return {
        "display_name": hit["display_name"],
        "lat": hit["lat"],
        "lon": hit["lon"],
        "address": {k: payload[k] for k in fields if _text(payload.get(k))},
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
        "base": _NOMINATIM_URL,
        "service": "nominatim",
        "scope": "worldwide",
        "forward_url": _nominatim_forward_url,
        "reverse_url": _nominatim_reverse_url,
        "parse_forward": _parse_nominatim_forward,
        "parse_reverse": _parse_nominatim_reverse,
    },
    "photon": {
        "name": "Photon (TerraLab, OpenStreetMap)",
        "base": _PHOTON_URL,
        "service": "photon",
        "scope": "worldwide",
        "forward_url": _photon_forward_url,
        "reverse_url": _photon_reverse_url,
        "parse_forward": _parse_photon_forward,
        "parse_reverse": _parse_photon_reverse,
    },
    "ban": {
        "name": "Base Adresse Nationale (France)",
        "base": _BAN_URL,
        "service": "ban",
        "scope": "France",
        "forward_url": _ban_forward_url,
        "reverse_url": _ban_reverse_url,
        "parse_forward": _parse_ban_forward,
        "parse_reverse": _parse_ban_reverse,
    },
    "cartociudad": {
        "name": "CartoCiudad (IGN Spain)",
        "base": _CARTOCIUDAD_URL,
        "service": "cartociudad",
        "scope": "Spain",
        "forward_url": _cartociudad_forward_url,
        "reverse_url": _cartociudad_reverse_url,
        "parse_forward": _parse_cartociudad_forward,
        "parse_reverse": _parse_cartociudad_reverse,
    },



    "pelias": {
        "name": "Pelias",
        "base": "",
        "scope": "the instance you point it at",
        "forward_url": _pelias_forward_url,
        "reverse_url": _pelias_reverse_url,
        "parse_forward": _parse_pelias_forward,
        "parse_reverse": _parse_pelias_reverse,
    },
}

_GEOCODE_PROVIDER_IDS = tuple(_GEOCODE_PROVIDERS)


def _resolve_geocode_provider(args: dict) -> tuple:
    """Return (provider_id, provider, base) or raise ValueError."""






    provider_id = _text(args.get("provider")).lower() or _DEFAULT_GEOCODE_PROVIDER
    provider = _GEOCODE_PROVIDERS.get(provider_id)
    if provider is None:
        raise ValueError(f"Unknown geocoding provider '{provider_id}'. "
                         f"Choose one of: {', '.join(_GEOCODE_PROVIDER_IDS)}.")

    base = _text(args.get("endpoint")) or _service(str(provider.get("service") or ""), provider["base"])
    if not base:
        raise ValueError(f"{provider['name']} has no public endpoint. Pass endpoint with your own instance, "
                         "or use 'ban' in France, 'cartociudad' in Spain, 'photon' or 'nominatim' anywhere.")
    return provider_id, provider, base















_BACKEND_GEO_TIMEOUT = 25


def _fallback_provider(answer: dict) -> str:
    """The service the backend says answered ("ban", "nominatim", "valhalla")."""




    name = str((answer or {}).get("provider") or "").strip()
    return name[:40] if re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", name) else "terralab_fallback"


def _fallback_attribution(answer: dict) -> str:
    notice = " ".join(str((answer or {}).get("attribution") or "").split())
    return notice[:300] or "credit the data source the AI Agent backend used."


def _backend_geo_url(path: str) -> str:
    """The address of one backend geo endpoint, on whichever server we are paired with."""
    from ..core.settings import Settings

    parts = urllib.parse.urlsplit(Settings().server_url)
    scheme = "http" if parts.scheme == "ws" else "https"
    return f"{scheme}://{parts.netloc}{path}"


def _backend_geo(path: str, params: dict, cache_ttl: float = 0.0) -> dict | None:
    """Ask the backend, or None when it cannot answer."""








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
    """Is this URL our own Photon instance, rather than somebody else's service?"""




    try:
        own = urllib.parse.urlsplit(_service("photon", _PHOTON_URL)).hostname or ""
        here = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return False
    return bool(own) and here.lower() == own.lower()


def _geocode_fetch(url: str):
    """Fetch a geocoder answer, parsed, or None when the body is empty."""










    budget = tuning.limit("net", "geocode_timeout_s", _GEOCODE_TIMEOUT)
    if _is_own_geocode(url):
        budget = min(budget, tuning.limit("net", "own_geocode_timeout_s", _OWN_GEOCODE_TIMEOUT))
    data = _http_get(url, timeout=budget, cache_ttl=_CACHE_GEOCODE_S)
    if not data.strip():
        return None
    return json.loads(data)








_BACKEND_GEOCODE_BATCH_MAX = 50


def _geocode_one_address(address: str, own_host: bool = True) -> tuple:
    """One address for a batch: ``(hit, own_host_failed)``."""





    own_failed = False
    if own_host:
        try:
            url = _photon_forward_url(_service("photon", _PHOTON_URL), address, 1, None, None)
            payload = _geocode_fetch(url)
            features = payload.get("features") if isinstance(payload, dict) else None
            return (_photon_hit(features[0], False) if features else None), False
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log_warning(f"geocode host failed on a batch, falling back to the backend: {exc}")
            own_failed = True
    answer = _backend_geo("/geo/geocode", {"q": address, "limit": 1}, cache_ttl=_CACHE_GEOCODE_S)
    hits = (answer or {}).get("results") or []
    return (dict(hits[0]) if hits else None), own_failed


def _geocode(args: dict) -> dict:




    query = _without_generic_place_words(str(args.get("query") or "").strip())
    if not query:
        return {"_error": "The place to look up is empty.", "code": "INVALID_ARGS",
                "suggestion": "Pass the place name, address or postcode to geocode."}



    try:
        limit = max(1, min(int(args.get("limit") or 5), 10))
    except (TypeError, ValueError):
        return {"_error": f"limit must be a whole number from 1 to 10, got {args.get('limit')!r}.",
                "code": "INVALID_ARGS", "suggestion": "Pass limit as an integer, or leave it out for 5."}
    country_codes = (args.get("country_codes") or "").strip()

    try:
        provider_id, provider, base = _resolve_geocode_provider(args)
    except ValueError as e:
        return {"_error": str(e)}



    try:
        viewbox = _run_on_main_thread(_canvas_viewbox_4326)
    except Exception:  # nosec B110 - canvas transform is optional
        viewbox = None



    fetched = max(limit, _PLACE_CANDIDATES) if provider_id == "photon" else limit
    url = provider["forward_url"](base, query, fetched, country_codes, viewbox)

    fell_back = False
    try:
        payload = _geocode_fetch(url)
    except urllib.error.HTTPError as e:




        payload = None
        if e.code == 400 and viewbox:
            try:
                payload = _geocode_fetch(provider["forward_url"](base, query, limit, country_codes, None))
            except (urllib.error.URLError, OSError, ValueError):


                payload = None
        if payload is None:
            failure = {"_error": f"Geocoding request failed: {e}"}
            fell_back = True
        else:
            failure = None
    except (urllib.error.URLError, OSError) as e:
        broken = net.describe_failure(e)
        failure = ({"_error": f"Geocoding request failed: {broken}", "code": net.NETWORK_ERROR,
                    "suggestion": net.NETWORK_SUGGESTION} if broken
                   else {"_error": f"Geocoding request failed: {e}"})
        payload, fell_back = None, True
    except ValueError:
        failure = {"_error": "Geocoding service returned an unreadable answer. Try again in a moment."}
        payload, fell_back = None, True
    else:
        failure = None

    results = provider["parse_forward"](payload, bool(args.get("verbose", False))) if payload is not None else []
    if results is None:
        failure = {"_error": f"Geocoding service error: {str(payload)[:200]}"}
        results, fell_back = [], True



    wanted = set(_country_list(args.get("country_codes") or ""))

    def _keep_wanted(hits: list) -> list:
        if not (wanted and hits):
            return hits
        kept = [hit for hit in hits if not hit.get("country_code") or hit["country_code"] in wanted]
        return kept or hits

    results = _keep_wanted(results)





    if viewbox and results and not fell_back and not _answers_by_name(query, results):
        try:
            plain = provider["parse_forward"](
                _geocode_fetch(provider["forward_url"](base, query, fetched, country_codes, None)),
                bool(args.get("verbose", False)))
        except (urllib.error.URLError, OSError, ValueError):
            plain = None
        if plain and _answers_by_name(query, _keep_wanted(plain)):
            results = _keep_wanted(plain)

    if provider_id == "photon" and results and not fell_back:


        results = _rank_places(query, results, viewbox, base)

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
    if not verbose:
        for hit in output:
            hit.pop("osm_value", None)


    project_crs = None
    try:
        transformed = _run_on_main_thread(
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
    except Exception:  # nosec B110 - canvas transform is optional
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
        out["note"] = (f"No result carries the name {query.split(',')[0].strip()!r} as written: it may be the "
                       "place under another name, or only a similar spelling. Check the first one is the place "
                       "meant before using it, and say so.")



    coded = [str(hit.get("country_code") or "") for hit in output if hit.get("country_code")]
    if wanted and coded and not any(code in wanted for code in coded):

        out["warning"] = (f"No result lies in {', '.join(sorted(wanted)).upper()}: the geocoder does not hold this "
                          "name there. Try the name in English or French, or the local name.")
    if project_crs:

        out["project_crs"] = project_crs
    layer_name = str(args.get("layer_name") or "").strip()
    if layer_name:
        try:
            out["layer"] = _run_on_main_thread(_add_geocode_layer, layer_name, query, output[0])
        except Exception as e:  # noqa: BLE001 - the hits are still a full answer
            out["layer_error"] = f"Could not add the point layer: {e}"
    return out


def _add_geocode_layer(name: str, query: str, hit: dict) -> dict:
    """One-point memory layer for the first geocode hit, added to the project and zoomed to."""
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
    except Exception:  # nosec B110 - zoom is a convenience
        pass
    return {"layer_id": layer.id(), "layer_name": name, "feature_count": 1, "crs": "EPSG:4326",
            "added_to_project": True}


def _reverse_geocode(args: dict) -> dict:
    lat = args["lat"]
    lon = args["lon"]

    try:
        provider_id, provider, base = _resolve_geocode_provider(args)
    except ValueError as e:
        return {"_error": str(e)}

    url = provider["reverse_url"](base, lat, lon)

    try:
        payload = _geocode_fetch(url)
    except (urllib.error.URLError, OSError) as e:
        payload, failure = None, {"_error": f"Reverse geocoding failed: {e}"}
    except ValueError:
        payload = None
        failure = {"_error": "Reverse geocoding returned an unreadable answer. Try again in a moment."}
    else:
        failure = None

    out = provider["parse_reverse"](payload) if payload is not None else None
    if out is None:





        answer = _backend_geo("/geo/reverse", {"lat": lat, "lon": lon}, cache_ttl=_CACHE_GEOCODE_S)
        if not (answer or {}).get("display_name"):
            return failure or {"_error": f"{provider['name']} found no address at that point."}
        out, provider_id = dict(answer), _fallback_provider(answer)
    out["provider"] = provider_id


    try:
        transformed = _run_on_main_thread(
            _project_crs_transform, [(out["lon"], out["lat"])]
        )
        if transformed:
            authid, coords = transformed
            out["x_project_crs"] = coords[0][0]
            out["y_project_crs"] = coords[0][1]
            out["project_crs"] = authid
    except Exception:  # nosec B110 - project CRS transform is optional
        pass

    return out






_PLACE_AREA_LAYERS = ("country", "state", "county", "city")


def _get_route(args: dict) -> dict:
    waypoints = args["waypoints"]
    profile = args.get("profile", "driving")
    layer_name = args.get("layer_name", "Route")

    if len(waypoints) < 2:
        return {"_error": "At least 2 waypoints required"}
    if len(waypoints) > _MAX_ROUTE_WAYPOINTS:
        return {"_error": f"Too many waypoints (max {_MAX_ROUTE_WAYPOINTS} per route)"}

    coords_str = ";".join(f"{wp['lon']},{wp['lat']}" for wp in waypoints)
    base = _osrm_base(profile)
    url = f"{base}/{coords_str}?overview=full&geometries=geojson&steps=false"

    _osrm_hint = (
        "The public OSRM server (routing.openstreetmap.de) is rate-limited and "
        "frequently slow or down. Retry, reduce waypoints, or for measuring straight-line "
        "distance use measure_distance instead."
    )
    failure = None
    result = {}
    try:
        budget = tuning.limit("net", "route_timeout_s", _ROUTE_TIMEOUT)
        data = _http_get(url, timeout=budget, cache_ttl=_CACHE_CATALOG_S)
    except (urllib.error.URLError, OSError) as e:
        reason = str(e) or repr(e) or "network timeout"
        failure = {
            "_error": f"OSRM routing request failed: {reason}",
            "_code": "OSRM_UNREACHABLE",
            "_suggestion": _osrm_hint,
        }
    else:
        try:
            result = json.loads(data)
        except (ValueError, TypeError) as e:
            snippet = (data[:200].decode("utf-8", "replace") if isinstance(data, (bytes, bytearray))
                       else str(data)[:200])
            failure = {
                "_error": f"OSRM returned a non-JSON response ({e}): {snippet}",
                "_code": "OSRM_BAD_RESPONSE",
                "_suggestion": _osrm_hint,
            }
        else:
            if result.get("code") != "Ok":
                detail = result.get("message") or result.get("code") or "unknown OSRM error"
                failure = {"_error": f"OSRM error: {detail}", "_code": "OSRM_ERROR",
                           "_suggestion": _osrm_hint}
            elif not result.get("routes"):
                failure = {
                    "_error": "No route found between the given waypoints",
                    "_code": "INVALID_ARGS",
                    "_suggestion": _osrm_hint,
                }

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
            return {"_error": "Failed to create route layer"}
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

    return _run_on_main_thread(_create, timeout=10)


def _measure_distance(args: dict) -> dict:
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
    "_PHOTON_URL",
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
    "_run_on_main_thread",
    "_without_generic_place_words",
]
