# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



















from __future__ import annotations

import math
import re
import unicodedata
import urllib.parse


class Unavailable(Exception):
    pass


def fold(value) -> str:

    text = unicodedata.normalize("NFD", str(value or ""))
    return "".join(ch for ch in text if not 0x300 <= ord(ch) <= 0x36F).lower()


def _enc(text: str) -> str:
    return urllib.parse.quote(str(text), safe="-_.!~*'()")


def _s(props: dict, key: str) -> str:
    value = props.get(key)
    return value if isinstance(value, str) else ""


def _key(feature: dict) -> str:
    props = feature.get("properties") or {}
    return "{}{}".format(props.get("osm_type"), props.get("osm_id"))


def _features(payload) -> list:
    features = payload.get("features") if isinstance(payload, dict) else None
    return [f for f in features if _point(f)] if isinstance(features, list) else []


def _point(feature) -> tuple | None:
    geometry = feature.get("geometry") if isinstance(feature, dict) else None
    coords = geometry.get("coordinates") if isinstance(geometry, dict) else None
    if not isinstance(coords, (list, tuple)) or len(coords) < 2:
        return None
    try:
        return float(coords[0]), float(coords[1])
    except (TypeError, ValueError):
        return None


def photon_place(feature: dict) -> dict:

    p = feature.get("properties") or {}
    street = " ".join(v for v in (_s(p, "housenumber"), _s(p, "street")) if v)
    town = " ".join(v for v in (_s(p, "postcode"), _s(p, "city") or _s(p, "town") or _s(p, "village")) if v)
    parts = [_s(p, "name"), street, town, _s(p, "state"), _s(p, "country")]
    address = ", ".join(dict.fromkeys(v for v in parts if v))
    extent = p.get("extent")
    lon, lat = _point(feature)
    return {
        "name": _s(p, "name") or street or _s(p, "city") or address,
        "address": address,
        "latitude": lat,
        "longitude": lon,
        "country": _s(p, "country") or None,
        "country_code": _s(p, "countrycode") or None,
        "postcode": _s(p, "postcode") or None,
        "kind": _s(p, "type") or _s(p, "osm_value") or "place",
        "osm": "{}={}".format(_s(p, "osm_key"), _s(p, "osm_value")),
        "source": "photon",

        "extent": [extent[0], extent[3], extent[2], extent[1]]
        if isinstance(extent, list) and len(extent) == 4 else None,
        "feature": feature,
    }


def _latin_script(text: str) -> bool:
    return all(unicodedata.name(ch, "").startswith("LATIN") for ch in text if ch.isalpha())


class _Search:


    def __init__(self, base: str, query: str, limit: int, filt: str = "") -> None:
        url = "{}/api?q={}&limit={}{}".format(base.rstrip("/"), _enc(query), limit, filt)
        self.query = query
        self.urls = [url + "&lang=en", url]

    def places(self, answers: dict, strict: bool = False) -> list:
        en, local = answers.get(self.urls[0]), answers.get(self.urls[1])
        if not isinstance(en, dict):
            if strict:
                raise Unavailable()
            return []
        features = _features(en)
        local_features = _features(local)
        local_feature = {_key(f): f for f in local_features}
        if not _latin_script(self.query) and local_features:


            en_of = {_key(f): f for f in features}
            order = [en_of[_key(f)] for f in local_features if _key(f) in en_of]
            features = order + [f for f in features if f not in order]
        out = []
        for feature in features:
            place = photon_place(feature)
            lf = local_feature.get(_key(feature))
            if lf:

                place["local_feature"] = lf
                lp = lf.get("properties") or {}
                if isinstance(lp.get("name"), str) and lp["name"] != place["name"]:
                    place["alt"] = lp["name"]
                local = " ".join(v for v in (lp.get("street"), lp.get("city"), lp.get("district"))
                                 if isinstance(v, str))
                if local:
                    place["local"] = local
            out.append(place)
        return out


class _Layer:


    def __init__(self, base: str, text: str, layers, country_code, lang: str = "&lang=en") -> None:
        params = "".join("&layer=" + layer for layer in layers)
        params += "&countrycode=" + country_code if country_code else ""
        self.url = "{}/api?q={}&limit=5{}{}".format(base.rstrip("/"), _enc(text), lang, params)

    def features(self, answers: dict) -> list:
        return _features(answers.get(self.url))


def _set(text: str) -> frozenset:
    return frozenset(text.split())




GENERIC = _set(
    "old town city centre center downtown historic historical district quarter area near around the vieille "
    "ville centre-ville vieux quartier des les du de la le motorway highway freeway exit junction interchange "
    "sortie autoroute echangeur centro storico storica historico historica casco antiguo altstadt innenstadt "
    "zentrum stadtmitte binnenstad citta baixa di del della da do dos das von van")
NAME_FILLER = _set("city town old new centre center downtown ville vieille citta the des les du de la le di del "
                   "della da do dos das von van")

STOPWORDS = _set("the des les du de la le near around di del della da do dos das von van")


TRANSIT = re.compile(
    r"^(highway=bus_stop|highway=platform|public_transport=(platform|stop_position)$|aerialway=station|"
    r"railway=(platform|tram_stop|stop|subway_entrance)$|"
    r"amenity=(parking|bus_station|taxi|bicycle_rental|car_sharing|charging_station)$)")
ASKS_STOP = re.compile(
    r"\b(stop|arret|arrêt|haltestelle|parada|fermata|paragem|halte|tram|bus|metro|parking|station|gare|bahnhof|"
    r"estacion|estación|stazione|estacao|estação|dworzec|eki)\b|駅", re.IGNORECASE)

REGION = re.compile(r"^(place=(state|county|region|province)|boundary=administrative)$")

SETTLEMENT = re.compile(
    r"^(place=(city|town|village|hamlet|municipality|suburb|locality|isolated_dwelling)|boundary=administrative)$")

LONE = re.compile(r"^place=(isolated_dwelling|farm)$")

ROAD_REF = re.compile(r"^[a-z]{1,2}-?[0-9]{1,4}$", re.IGNORECASE)

MINOR = re.compile(
    r"^(place=(hamlet|farm|isolated_dwelling|locality|neighbourhood|quarter|square|plot)|"
    r"(highway|office|landuse|amenity|shop|railway|public_transport|craft)=)")
NEAR = re.compile(
    r"\s(?:near|around|close to|outside|près de|pres de|autour de|bei|nahe|in der nähe von|cerca de|vicino a|"
    r"perto de|nabij|bij)\s+(.+)$", re.IGNORECASE)
POSTCODE = re.compile(r"\b[0-9]{4,5}\b|\b[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}\b", re.ASCII)
TOP = 1e6


def _region_last(p: dict) -> int:
    return 1 if REGION.match(p["osm"]) else 0


def _stop_penalty(query: str):
    asks = bool(ASKS_STOP.search(query))
    return lambda p: (0 if asks or not TRANSIT.match(p["osm"]) else 1) + (1 if LONE.match(p["osm"]) else 0)


def key_tokens(query: str) -> list:
    return [t for t in re.split(r"[^a-z0-9]+", fold(query)) if len(t) >= 3 and t not in GENERIC]


def _text_of(p: dict, own: bool = False) -> str:
    if own:
        return fold("{} {}".format(p["name"], p.get("alt") or ""))
    return fold("{} {} {} {}".format(p["name"], p.get("alt") or "", p["address"], p.get("local") or ""))


def matches(p: dict, tokens) -> int:
    text = _text_of(p)
    return sum(1 for t in tokens if t in text)


def own_matches(p: dict, tokens) -> int:

    text = _text_of(p, own=True)
    return sum(1 for t in tokens if t in text)


def _word_in(t: str, text: str) -> bool:
    return re.search(r"(^|[^a-z0-9])" + re.escape(t) + r"($|[^a-z0-9])", fold(text)) is not None


def _bare_name(value) -> str:
    return re.sub(r"[^a-z0-9]+", " ", fold(value)).strip()


def _acronym_of(p: dict, t: str) -> bool:

    words = t.split(" ")
    name = _bare_name(p["name"])
    return (len(words) >= 3 and re.match(r"^[a-z]{3,}$", name) is not None
            and re.search(r"[a-z][A-Z]|[A-Z]{3}", p["name"]) is not None
            and name == "".join(w[0] for w in words))


def _exact_name(p: dict, text: str) -> bool:
    t = _bare_name(text)
    return bool(t) and (_bare_name(p["name"]) == t or (bool(p.get("alt")) and _bare_name(p["alt"]) == t)
                        or _acronym_of(p, t))


def _town_by_start(p: dict, text: str) -> bool:
    t = _bare_name(text)
    return bool(t) and re.match(r"^place=(city|town|municipality)$", p["osm"]) is not None \
        and _bare_name(p["name"]).startswith(t + " ")


def _name_rank(p: dict, subject: str, tokens, region: bool = False) -> float:


    if region and _exact_name(p, subject) and SMALL.match(p["osm"]):
        return TOP - 5
    if region and names_region(p, key_tokens(subject)) and not _exact_name(p, subject):
        return TOP - 3
    if _exact_name(p, subject):
        return TOP - 2 if MINOR.match(p["osm"]) else TOP
    if _town_by_start(p, subject):
        return TOP - 1
    return matches(p, tokens)








REGION_KIND = re.compile(
    r"^(boundary=(protected_area|national_park)|leisure=nature_reserve|place=(region|island|archipelago)|"
    r"natural=(water|bay|wetland|wood|heath|scrub|grassland|strait))$")

REGION_FILTER = ("&osm_tag=boundary:protected_area&osm_tag=boundary:national_park&osm_tag=leisure:nature_reserve"
                 "&osm_tag=place:region&osm_tag=place:island&osm_tag=place:archipelago")
SMALL = re.compile(
    r"^(place=(hamlet|farm|isolated_dwelling|locality|neighbourhood|quarter|square|plot)|"
    r"(highway|office|landuse|amenity|shop|railway|public_transport|craft|building|information|man_made|tourism|"
    r"barrier|emergency)=)")
SUMMIT = re.compile(r"^natural=(peak|volcano|saddle|glacier)$")


def _own_all(p: dict, tokens) -> bool:
    return bool(tokens) and own_matches(p, tokens) == len(tokens)


def names_region(p: dict, tokens) -> bool:
    e = p.get("extent")
    span = max(e[2] - e[0], e[3] - e[1]) if e else 0
    return REGION_KIND.match(p["osm"]) is not None and span >= 0.15 and _own_all(p, tokens)


def _region_asked(query: str, places: list, tokens) -> bool:

    return (not looks_like_address(query) and not any(SUMMIT.match(p["osm"]) and _own_all(p, tokens) for p in places)
            and any(names_region(p, tokens) for p in places))


def _extra_words(tokens):

    if not tokens:
        return lambda p: 0

    def extra_in(name: str) -> int:
        return min(len([t for t in re.split(r"[^a-z0-9]+", fold(part))
                        if len(t) >= 2 and t not in tokens and t not in NAME_FILLER])
                   for part in re.split(r"\s[/;]\s", name))

    def extra(p: dict) -> float:
        alt = p.get("alt")
        return min(extra_in(p["name"]), extra_in(alt) if alt and re.search(r"[a-z]", fold(alt)) else math.inf)
    return extra


def subject_of(query: str) -> str:

    parts = [x.strip() for x in query.split(",") if x.strip()]
    if len(parts) == 1:
        return NEAR.sub("", parts[0], count=1).strip()
    return parts[0] if parts else ""


def looks_like_address(query: str) -> bool:
    return re.search(r"[0-9]", query) is not None





CATEGORIES = [
    {"words": re.compile(r"\b(city hall|town hall|hotel de ville|mairie|rathaus|ayuntamiento|municipio|"
                         r"palazzo comunale|stadhuis|gemeentehuis|camara municipal|prefeitura|ratusz)\b", re.ASCII),
     "include": "osm.amenity.townhall"},
    {"words": re.compile(r"\b(train station|railway station|gare sncf|bahnhof|hauptbahnhof|estacion de tren|"
                         r"stazione ferroviaria|stazione centrale|treinstation|estacao ferroviaria|dworzec)\b",
                         re.ASCII),
     "include": "osm.railway.station"},



    {"words": re.compile("".join(r"(?<!\b" + w + " )" for w in (
        "gas", "petrol", "fuel", "service", "fire", "police", "charging", "ski", "bus", "coach", "radio",
        "power", "weather", "space", "filling", "tv", "pumping", "lifeboat", "ranger", "research", "polling",
        "bike", "bicycle", "taxi", "car", "ambulance", "la", "du", "des"))
        + r"\b(station|gare)\b(?![ -](?:de ski|balneaire|thermale|service|d.epuration|de lavage|routiere|maritime"
          r"|de peage|de recharge))", re.ASCII),
     "include": "osm.railway.station", "town_only": True},
    {"words": re.compile(r"\b(airport|aeroport|flughafen|aeropuerto|aeroporto|luchthaven|lotnisko)\b", re.ASCII),
     "include": "osm.aeroway.aerodrome", "reach": 0.5},
    {"words": re.compile(r"\b(hospital|hopital|krankenhaus|ospedale|ziekenhuis|szpital)\b", re.ASCII),
     "include": "osm.amenity.hospital"},
    {"words": re.compile(r"\b(bus stop|tram stop|arret de bus|arret de tram|arret|haltestelle|parada de autobus|"
                         r"parada|fermata|paragem de autocarro|paragem|autocarro|bushalte|przystanek)\b", re.ASCII),
     "include": "osm.highway.bus_stop,osm.public_transport.platform,osm.railway.tram_stop"},
    {"words": re.compile(r"\b(motorway exit|highway exit|freeway exit|exit|sortie|echangeur|ausfahrt|"
                         r"anschlussstelle|uscita|afrit)\b", re.ASCII),
     "include": "osm.highway.motorway_junction"},
]

TRANSIT_WORDS = re.compile(r"\b(bus|busway|tram|metro|stop|stops|autocarro|autobus|omnibus|ligne|line|linha|linea)\b"
                           r"|\b[0-9]+\b", re.ASCII)


def category_of(text: str):
    folded = fold(text)
    return next((c for c in CATEGORIES if c["words"].search(folded)), None)


def _without_kind(text: str, category) -> str:
    return category["words"].sub(" ", fold(text), count=1)


def _names_text(feature: dict, text: str) -> bool:

    t = _bare_name(text)
    n = _bare_name((feature.get("properties") or {}).get("name") or "")
    return bool(t and n and (n == t or n.startswith(t + " ") or t.startswith(n + " ")))


def _box_around(feature: dict) -> list:
    lon, lat = _point(feature)
    props = feature.get("properties") or {}
    e = props.get("extent")

    pad = 0.03
    if isinstance(e, list) and len(e) == 4:
        return [e[0] - pad, e[3] - pad, e[2] + pad, e[1] + pad]
    r = 0.12 if props.get("osm_value") == "city" else 0.05
    c = math.cos(lat * math.pi / 180)
    return [lon - r / c, lat - r, lon + r / c, lat + r]




COUNTRY_ALIAS = {"usa": "US", "us": "US", "unitedstatesofamerica": "US", "uk": "GB", "greatbritain": "GB",
                 "england": "GB", "scotland": "GB", "wales": "GB", "uae": "AE"}
_TOWN_LAYERS = ("city", "locality", "district", "county", "state")


class _TownNamed:


    def __init__(self, base: str, text: str, country_code) -> None:
        self.text = text
        self.country_code = country_code
        self.en = _Layer(base, text, _TOWN_LAYERS, country_code)
        self.local = _Layer(base, text, _TOWN_LAYERS, country_code, "")
        self.urls = [self.en.url, self.local.url]

    def anchor(self, answers: dict):
        local_of = {_key(f): f for f in self.local.features(answers)}
        town = next((f for f in self.en.features(answers) if _names_text(f, self.text)
                     or (_key(f) in local_of and _names_text(local_of[_key(f)], self.text))), None)
        if town is None:
            return None
        props = town.get("properties") or {}
        return {"name": str(props.get("name")),
                "country_code": str(props.get("countrycode") or "") or self.country_code,
                "bbox": _box_around(town), "center": list(_point(town))}


def _in_anchor(p: dict, a: dict) -> bool:
    if a["bbox"]:
        b = a["bbox"]
        return b[0] <= p["longitude"] <= b[2] and b[1] <= p["latitude"] <= b[3]
    return p.get("country_code") == a["country_code"]


def _in_box(p: dict, b, pad: float) -> bool:
    return b[0] - pad <= p["longitude"] <= b[2] + pad and b[1] - pad <= p["latitude"] <= b[3] + pad


def _kind_town_lookups(base: str, subject: str, category, code) -> list:

    left = [t for t in re.split(r"[^a-z0-9]+", TRANSIT_WORDS.sub(" ", _without_kind(subject, category)))
            if len(t) >= 2 and t not in GENERIC and t not in ("of", "von", "del", "di", "do", "da", "van")]
    keep = set(left)

    words = [w for w in re.split(r"[^\w'-]+", subject) if w and fold(w) in keep]
    if "bus_stop" in category["include"]:

        tries = [" ".join(words), " ".join(words[-2:]), " ".join(words[-1:])]
    else:
        tries = [" ".join(words)]
    return [_TownNamed(base, t, code) for t in dict.fromkeys(t for t in tries if t and len(t) >= 3)]


def _kind_name(subject: str, category, anchor: dict) -> str:

    town = set(re.split(r"[^a-z0-9]+", fold(anchor["name"])))
    words = re.split(r"[^a-z0-9]+", TRANSIT_WORDS.sub(" ", _without_kind(subject, category)))
    return " ".join(w for w in words if w and w not in town)


def _round(*groups):

    return list(dict.fromkeys(url for group in groups for url in group))


def search(query: str, limit: int, photon: str, ign: str):


    found = yield from _search_once(query, limit, photon, ign)


    category = None if found else category_of(query)
    if not category:
        return found
    bare = re.sub(r"\s+", " ", TRANSIT_WORDS.sub(lambda m: m.group(0) if re.search(r"[0-9]", m.group(0)) else " ",
                                                  _without_kind(query, category))).strip()
    if bare and bare != fold(query).strip():
        return (yield from _search_once(bare, limit, photon, ign))
    return found


def _search_once(query: str, limit: int, photon: str, ign: str):
    wide = max(limit, 10)
    subject = subject_of(query)
    parts = [x.strip() for x in query.split(",")]
    start = next((i for i, x in enumerate(parts) if re.search(r"[0-9]", x)), -1)


    ign_url = None
    if ign and looks_like_address(query):
        ign_text = ", ".join(parts[start:]) if start > 0 else query
        ign_url = "{}/search?q={}&limit=1".format(ign.rstrip("/"), _enc(ign_text))


    tokens = key_tokens(query)
    words = [w for w in re.split(r"[\s,]+", query) if w]
    bare = " ".join(w for w in words if fold(w) not in GENERIC and not ROAD_REF.match(w))
    dropped = any(fold(w) in GENERIC and fold(w) not in STOPWORDS for w in words)
    retry = bool(tokens and bare and bare != query.strip())
    main = _Search(photon, query, wide)
    bare_search = _Search(photon, bare, 5) if retry else None


    clean = [x.strip() for x in query.split(",") if x.strip()]
    rest = clean[1:]
    near = NEAR.search(clean[0]) if len(clean) == 1 else None
    if near:
        rest = [near.group(1)]
    last = POSTCODE.sub("", rest[-1]).strip() if rest else ""
    countries = [_Layer(photon, last, ("country",), None), _Layer(photon, last, ("country",), None, "")] if rest else []

    kind = category_of(subject)
    category = None if kind and kind.get("town_only") else kind

    answers = yield _round(main.urls, bare_search.urls if retry and dropped else (), [ign_url] if ign_url else (),
                           [c.url for c in countries])
    answers = dict(answers)
    places = main.places(answers, strict=True)


    if not looks_like_address(query) and key_tokens(subject) and not any(
            _exact_name(p, subject) and not SMALL.match(p["osm"]) for p in places):
        areas = _Search(photon, subject, 5, REGION_FILTER)
        answers.update((yield _round(areas.urls)))
        seen_places = {(p["osm"], p["latitude"], p["longitude"]) for p in places}
        places = places + [a for a in areas.places(answers)
                           if (a["osm"], a["latitude"], a["longitude"]) not in seen_places]
    late_retry = retry and not dropped and not any(matches(p, tokens) == len(tokens) for p in places)

    country_code = None
    country_name = ""
    if rest:
        alias = COUNTRY_ALIAS.get(re.sub(r"[^a-z]", "", fold(last)))
        country = next((f for c in countries for f in c.features(answers) if _names_text(f, last)), None)
        if country is not None or alias:
            props = (country or {}).get("properties") or {}
            country_code = alias or (str(props.get("countrycode") or "") or None)
            country_name = str(props.get("name") or last)
            rest = rest[:-1]
    town_parts = [x for x in (POSTCODE.sub("", r).strip() for r in rest[-3:]) if x and not re.search(r"[0-9]", x)]
    towns = [_TownNamed(photon, x, country_code) for x in town_parts]
    kind_towns = _kind_town_lookups(photon, subject, category, country_code) if category else []
    answers.update((yield _round(bare_search.urls if late_retry else (), *(t.urls for t in towns),
                                 *(t.urls for t in kind_towns))))

    if retry and (dropped or late_retry):
        places = places + bare_search.places(answers)
    ranked = _ranked(query, places, wide, tokens, dropped)

    anchor = next((a for a in reversed([t.anchor(answers) for t in towns]) if a), None)
    if anchor is None and category:
        anchor = next((a for a in (t.anchor(answers) for t in kind_towns) if a), None)
    if anchor is None and country_code:
        anchor = {"name": country_name, "country_code": country_code, "bbox": None, "center": None}

    if anchor:
        local = _anchored_searches(photon, query, anchor, wide)
        answers.update((yield _round(*(s.urls for s in local["searches"]), *(s.urls for s in local["of_kind"]))))
        places = _anchored(query, ranked, local, answers, wide)
    else:
        places = ranked
    places = places[:limit]



    first = places[0] if places else None
    french = (first or {}).get("country_code") == "FR" or (anchor or {}).get("country_code") == "FR"
    feature = (_features(answers.get(ign_url)) or [None])[0] if ign_url else None
    if french and feature is not None:
        props = feature.get("properties") or {}
        lon, lat = _point(feature)
        city = fold(props.get("city") or "")
        same_town = (not (anchor or {}).get("bbox") or _in_anchor({"latitude": lat, "longitude": lon}, anchor)
                     or (city and city in fold(query)))
        score = props.get("score")
        if same_town and isinstance(score, (int, float)) and score >= 0.6 and props.get("type") == "housenumber":
            base = dict(first) if first else {"osm": "", "extent": None, "kind": "address"}

            for key in ("alt", "feature", "local_feature", "local"):
                base.pop(key, None)
            base.update({"osm": "", "extent": None})
            base.update({"name": props.get("name") or base.get("name") or props.get("label"),
                         "address": "{}, France".format(props.get("label")), "latitude": lat, "longitude": lon,
                         "country": "France", "country_code": "FR", "kind": "address",
                         "postcode": props.get("postcode") or base.get("postcode"), "source": "ban",
                         "ban": feature})

            same_door = bool(first) and abs(first["latitude"] - lat) < 0.002 and abs(first["longitude"] - lon) < 0.003
            places = ([base] + places[1 if same_door else 0:])[:limit]
    return places


def _ranked(query: str, places: list, limit: int, tokens, dropped: bool) -> list:

    if tokens:
        extra_score = _extra_words(tokens)
        stop = _stop_penalty(query)
        subject = subject_of(query)

        def town(p: dict) -> float:

            if re.match(r"^place=(city|town|municipality)$", p["osm"]):
                return 0
            if p["osm"] == "place=village":
                return 0.2
            return 0.5 if re.match(r"^(place=|boundary=administrative)", p["osm"]) else 1

        region = _region_asked(query, places, key_tokens(subject))
        keyed = [((-_name_rank(p, subject, tokens, region), stop(p) + (town(p) if dropped else 0),
                   -own_matches(p, tokens), extra_score(p), _region_last(p), i), p) for i, p in enumerate(places)]
        places = [p for _k, p in sorted(keyed, key=lambda kp: kp[0])]
    return places[:limit]


def _anchored_searches(photon: str, query: str, town_anchor: dict, limit: int) -> dict:

    subject = subject_of(query)
    bare = " ".join(w for w in subject.split() if fold(w) not in GENERIC and not ROAD_REF.match(w))
    kind = category_of(subject)

    kind_only = kind is not None and not key_tokens(_without_kind(subject, kind))
    category = None if (kind_only or (kind or {}).get("town_only")) and not town_anchor["bbox"] else kind
    reach = (category or {}).get("reach", 0)
    anchor = dict(town_anchor)
    if reach and anchor["bbox"]:
        b = anchor["bbox"]
        anchor["bbox"] = [b[0] - reach, b[1] - reach, b[2] + reach, b[3] + reach]
    if anchor["bbox"]:
        filt = "&bbox=" + ",".join(f"{v:.5f}" for v in anchor["bbox"])
    else:
        filt = "&countrycode=" + str(anchor["country_code"])
    searches = [_Search(photon, text, max(limit, 5), filt) for text in dict.fromkeys(t for t in (subject, bare) if t)]
    of_kind = []
    if category:
        if anchor["bbox"]:
            texts = [anchor["name"], "", _kind_name(subject, category, anchor)]
        else:

            texts = [_without_kind(subject, category).strip() or subject]
        of_kind = [_Search(photon, text, 10, filt + "&include=" + category["include"]) for text in dict.fromkeys(texts)]
    return {"anchor": anchor, "category": category, "searches": searches, "of_kind": of_kind}


def _anchored(query: str, places: list, local: dict, answers: dict, limit: int) -> list:

    anchor, category = local["anchor"], local["category"]
    searches = [s.places(answers) for s in local["searches"]]
    of_kind = [p for s in local["of_kind"] for p in s.places(answers)]
    subject = subject_of(query)
    pool = places + [p for found in searches for p in found] + of_kind
    if not any(_in_anchor(p, anchor) for p in pool):
        return places
    tokens = key_tokens(subject)
    all_tokens = key_tokens(query)
    address = looks_like_address(query)


    town = searches[0][0] if searches and searches[0] else None
    full_match = any(_in_anchor(p, anchor) and SETTLEMENT.match(p["osm"])
                     and all(_word_in(t, "{} {}".format(p["name"], p.get("alt") or "")) for t in tokens) for p in pool)
    exonym = town if (town is not None and not category and not full_match and not matches(town, tokens)
                      and not address and len(subject.split()) <= 3 and town["kind"] in ("city", "locality")) else None


    anchor_words = set(key_tokens(anchor["name"]))
    leftover = [t for t in key_tokens(_without_kind(subject, category)) if t not in anchor_words] if category else None
    centre = anchor["center"]
    town_word = re.compile(r"(^|[^a-z])" + re.sub(r"[^a-z0-9]+", "[^a-z0-9]+", fold(anchor["name"])) + r"($|[^a-z])")
    reach = (category or {}).get("reach")
    of_town = [p for p in of_kind if _in_anchor(p, anchor)
               and (reach or town_word.search(fold(p.get("local") or p["address"])))]

    def dist(p):
        return math.hypot(p["longitude"] - centre[0], p["latitude"] - centre[1]) if centre else 0

    def size(p):
        e = p.get("extent")
        return (e[2] - e[0]) * (e[3] - e[1]) if e else 0

    def size_class(p):

        return math.floor(math.log(size(p)) / math.log(5)) if size(p) > 0 else -99

    def names_town(p):
        return 1 if town_word.search(fold("{} {}".format(p["name"], p.get("alt") or ""))) else 0

    def minor(p):
        return 1 if re.search(r"\b(quartier|annexe|annex|district|bezirk|ortsteil|delegacion|circoscrizione)\b",
                              fold(p["name"])) else 0

    if reach:
        def order(ip):
            i, p = ip
            return (-names_town(p), -size_class(p), i)
    else:
        def order(ip):
            i, p = ip
            return (minor(p), -(1 if p.get("extent") else 0), i, dist(p))




    named = ([p for p in of_town if all(_word_in(t, _text_of(p, own=True)) for t in leftover or ())]
             if not leftover or anchor["bbox"] else [])
    main = min(enumerate(named), key=order)[1] if leftover is not None and named else None

    def same(a, b):
        return a is not None and a["latitude"] == b["latitude"] and a["longitude"] == b["longitude"]



    stop = _stop_penalty(query)
    extra_score = _extra_words(tokens)

    def tier(p):
        if _in_anchor(p, anchor):
            return 2
        return 1 if anchor["bbox"] and _in_box(p, anchor["bbox"], 0.25) else 0

    region = _region_asked(query, pool, tokens)
    rows = []
    for i, p in enumerate(pool):
        chosen = same(main, p) or same(exonym, p)
        t = tier(p)
        m = TOP if chosen else _name_rank(p, subject, tokens, region)
        r = 1 if region and names_region(p, tokens) else 0
        s = stop(p)
        n = 0 if address else own_matches(p, tokens)
        w = matches(p, all_tokens) if address else 0
        x = 0 if _town_by_start(p, subject) else extra_score(p)
        f = (chosen or (_exact_name(p, subject) and not (region and SMALL.match(p["osm"])))
             or (bool(tokens) and all(_word_in(tok, _text_of(p)) for tok in tokens)))
        if anchor["bbox"]:
            key = (-(t > 0), -m, s, -t, -w, -n, x, _region_last(p), i)
        else:


            key = (-f, -r, x if f else 0, -(t > 0), -m, s, -n, x, -w, _region_last(p), i)
        rows.append((key, p))
    seen = set()
    out = []
    for _sort_key, p in sorted(rows, key=lambda r: r[0]):
        k = "{}|{:.4f}|{:.4f}".format(p["name"], p["latitude"], p["longitude"])
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out[:limit]
