# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








































from __future__ import annotations

import re
import time

from ..core import limits, tuning
from ..core.logger import log_warning
from ..core.tool_registry import spec







def quiet_km2() -> float:

    return limits.current("FETCH_QUIET_KM2")


def quiet_sparse_km2() -> float:

    return limits.current("FETCH_QUIET_SPARSE_KM2")


def dense_max_km2() -> float:



    return limits.current("FETCH_DENSE_MAX_KM2")















OWN_OVERPASS_HOST = "overpass.terra-lab.ai"




_AREA_STATEMENT = re.compile(r"\barea\s*[\[(:]|\(\s*area\b", re.IGNORECASE)





def hard_max_features() -> int:

    return int(limits.current("FETCH_HARD_MAX_FEATURES"))













FOOTPRINTS_MAX_KM2 = 25.0

LABEL = "data volume"



ZONE_TOOLS = {
    "fetch_osm_data": "query",
    "fetch_building_footprints": "dense",
    "add_pmtiles_layer": "query",
}

COUNT_KEYS = ("max_features", "limit", "max_items", "max_results")






HELD_TOOLS = {
    "add_points_from_json": ("Filtering the records before loading, or a CSV/GeoJSON file loaded with "
                             "add_data, reads from disk whatever its size."),
    "add_arcgis_rest_layer": ("A bbox over the user's area, or a where clause on the layer's fields, reads "
                              "that part; the service's own export (GeoJSON or a file) through add_data "
                              "reads from disk whatever its size."),
}




_DENSE_RE = re.compile(
    r"building|highway|road|street|footway|path|addr|landuse|natural|water|wood|forest|"
    r"parcel|cadast|land_?cover|power=|railway", re.IGNORECASE)




HOSTED_INSTEAD = (
    ' Or fetch_overture(theme, mode="stream") for a city: 4 tiles, about 1.4 degrees a side.'
)



WHOLE_BEFORE_PART = (" A smaller box is only part of the zone; the part outside it stays "
                     "unloaded.")




FOOTPRINTS_WHOLE = (" The same footprints, merged, are fetch_overture theme buildings: one call clips 200 km², "
                    "clip_to a named place follows its outline in up to 12 clips, and full_extent reads a "
                    "whole place the user named.")





CLIP_TO_HINT = (" For a named place, fetch_overture with the theme and clip_to the place reads up to 12 such "
                "clips along its outline.")







class _Routing:


    __slots__ = ("theme_keys", "value_themes", "fields", "mixed_themes", "road_classes", "key_values",
                 "way_themes")

    def __init__(self, doc: dict):
        self.theme_keys = dict(doc["theme_keys"])
        self.value_themes = {key: (frozenset(row["values"]), row["theme"])
                             for key, row in doc["value_themes"].items()}
        self.fields = {theme: frozenset(names) for theme, names in doc["fields"].items()}
        self.mixed_themes = frozenset(doc["mixed_themes"])
        self.road_classes = frozenset(doc["road_classes"])

        self.key_values = {theme: (row["key"], frozenset(row["only"]) if "only" in row else None,
                                   frozenset(row.get("except") or ()), bool(row["bare"]))
                           for theme, row in doc["key_values"].items()}
        self.way_themes = frozenset(doc["way_themes"])


def _holds(value, only, left_out) -> bool:

    return bool(value) and (value in only if only is not None else value not in left_out)



_served: dict = {"pair": (None, None)}


def _routing() -> _Routing | None:

    doc = tuning.service_doc("osm_routing")
    if doc is None:
        return None
    seen, tables = _served["pair"]
    if seen is not doc:
        try:
            tables = _Routing(doc)
        except (KeyError, TypeError, AttributeError) as exc:
            log_warning(f"Served osm_routing could not be read ({exc}), every query goes to Overpass")
            tables = None
        _served["pair"] = (doc, tables)
    return tables



ANY_VALUE = "*"
HOSTED_MAX_THEMES = 3



_BBOX_STATEMENT = re.compile(
    r"\b(node|way|relation|rel|nwr|nw|nr|wr)\s*((?:\[[^\]]*\]\s*)+)\(\s*\{\{bbox\}\}\s*\)", re.IGNORECASE)


_SELECTOR = re.compile(
    r'\[\s*(!?)\s*["\']?([A-Za-z_][\w:]*)[\"\']?\s*'
    r'(?:(=|!=|~|!~)\s*(?:"([^"]*)"|\'([^\']*)\'|([^\],"\']+?))\s*(,\s*i)?)?\s*\]')



_ELEMENT = re.compile(r"\b(node|way|relation|rel|nwr|nw|nr|wr)\s*[\[(]", re.IGNORECASE)





_POINT_VALUE = re.compile(
    r"\b(?:highway|railway)\W{0,3}("
    r"bus_stop|crossing|traffic_signals|street_lamp|stop|give_way|turning_circle|turning_loop|"
    r"mini_roundabout|motorway_junction|speed_camera|elevator|passing_place|milestone|toll_gantry|"
    r"trailhead|emergency_bay|station|halt|tram_stop|level_crossing|subway_entrance|buffer_stop|"
    r"switch|signal)\b", re.IGNORECASE)


def own_region_km2(name: str, args: dict) -> float:








    if provider_of(name) != "overpass" or not isinstance(args, dict) or not own_overpass():
        return 0.0
    if hosted_themes(args.get("query")) and not past_hosted_cap(args):
        return 0.0
    return float(limits.current("FETCH_OWN_OVERPASS_REGION_KM2"))


def past_hosted_cap(args: dict) -> bool:






    if not isinstance(args, dict) or lifted(args):
        return False
    themes = hosted_themes(args.get("query"))
    area = zone_km2(args)
    cap = hosted_cap_km2(themes) if themes else 0.0
    return bool(themes) and area is not None and 0 < cap < area


def own_split_km2(name: str, args: dict) -> float:

    region = own_region_km2(name, args)
    return max(region, float(limits.current("FETCH_OWN_OVERPASS_SPLIT_KM2"))) if region else 0.0


def _theme_of(key: str, values, routing: _Routing) -> str:

    scoped = routing.value_themes.get(key)
    if scoped is not None:
        wanted, theme = scoped

        if values and all(value in wanted for value in values) and theme in tuning.service_list("osm_themes", ()):
            return theme
        if key not in routing.theme_keys:
            return ""
    if key in routing.theme_keys:
        return routing.theme_keys[key]


    if key == "natural" and values == ["water"]:
        return "water_areas"
    return ""


def _statement_theme(selectors: list, routing: _Routing) -> str:

    for key, values in selectors:
        theme = _theme_of(key, values, routing)
        if theme:
            return theme
    return ""


def _selector(raw: str, routing: _Routing):






    match = _SELECTOR.fullmatch(raw)
    if match is None:
        return None
    negated, key, op, quoted, single, bare, flag = match.groups()
    if negated or flag or op in ("!=", "!~"):
        return None
    if op is None:
        return key.lower(), None
    value = quoted if quoted is not None else single if single is not None else (bare or "")
    value = value.strip()
    if op == "=":
        return key.lower(), [value]


    body = value[1:-1] if value.startswith("^") and value.endswith("$") else None
    exact = body is not None and ("|" not in body or (body.startswith("(") and body.endswith(")")))
    if value.startswith("^"):
        value = value[1:]
    if value.endswith("$"):
        value = value[:-1]
    if value.startswith("(") and value.endswith(")"):
        value = value[1:-1]
    parts = [part.strip() for part in value.split("|")]
    if not all(re.fullmatch(r"[A-Za-z0-9_:\- ]+", part) for part in parts):
        return None
    lowered = key.lower()
    if not exact and lowered not in routing.theme_keys and lowered not in routing.value_themes and lowered != "natural":





        return None
    return key.lower(), parts









_OSM_ONLY = {"roads": {"subtype": "road", "source": "OpenStreetMap"}}


def _statement_filter(theme: str, selectors: list, routing: _Routing):

    if theme == "roads":



        for key, _ in selectors:
            if key != "highway":
                return None
        wanted: dict = dict(_OSM_ONLY["roads"])
        for _, values in selectors:
            if values is None:
                continue
            if not all(value in routing.road_classes for value in values):
                return None
            wanted["class"] = values[0] if len(values) == 1 else values
        return wanted
    if theme == "buildings":
        for key, values in selectors:
            if key != "building":
                return None
            if values is not None and values != ["yes"]:
                return None
        return {}
    fields = routing.fields.get(theme)
    if fields is None:
        return None
    held_key, only, left_out, bare_holds = routing.key_values.get(theme, ("", None, frozenset(), True))
    wanted = {}
    for key, values in selectors:
        if key not in fields:
            return None
        if key == held_key and (not bare_holds if values is None
                                else not all(_holds(value, only, left_out) for value in values)):
            return None
        if values is None:






            if _theme_of(key, None, routing) != theme or theme == "pois":
                return None
            if theme in routing.mixed_themes:
                wanted[key] = ANY_VALUE
            continue
        wanted[key] = values[0] if len(values) == 1 else values
    return wanted


def _merge_filters(base: dict, filters: list):












    if any(wanted == base for wanted in filters):
        return dict(base)
    if len({tuple(sorted(wanted)) for wanted in filters}) != 1:
        disjunction: list = []
        for wanted in filters:
            one = dict(wanted)
            if one not in disjunction:
                disjunction.append(one)
        return disjunction
    first = filters[0]
    differing = [key for key in first if any(wanted[key] != first[key] for wanted in filters[1:])]
    if len(differing) > 1:
        return None
    merged = dict(first)
    if differing:
        key = differing[0]
        values: list = []
        for wanted in filters:
            value = wanted[key]
            for item in (value if isinstance(value, list) else [value]):
                if item not in values:
                    values.append(item)
        merged[key] = values[0] if len(values) == 1 else values
    return merged



OSM_SOURCE = "OpenStreetMap"


def hosted_plan(query) -> list:













    routing = _routing()
    text = str(query or "")
    if routing is None or "{{bbox}}" not in text or _AREA_STATEMENT.search(text):
        return []
    lowered = text.lower()
    if any(marker in lowered for marker in ("around", "poly:", "(id:", "if:", "is_in", "pivot", "{{geocode")):
        return []
    statements = list(_BBOX_STATEMENT.finditer(text))
    if not statements:
        return []
    starts = {match.start() for match in statements}



    for element in _ELEMENT.finditer(text):
        if element.start() not in starts:
            return []
    order: list = []
    filters: dict = {}
    for match in statements:
        element = match.group(1).lower()
        selectors = []
        for raw in re.findall(r"\[[^\]]*\]", match.group(2)):
            selector = _selector(raw, routing)
            if selector is None or any(selector[0] == key for key, _ in selectors):
                return []
            selectors.append(selector)
        theme = _statement_theme(selectors, routing)
        if not theme:
            return []
        if element == "node" and theme in routing.way_themes:
            return []




        if theme in {"pois", "transit_stops"} and element != "node":
            return []


        if theme != "transit_stops" and _POINT_VALUE.search(match.group(2)):
            return []
        wanted = _statement_filter(theme, selectors, routing)
        if wanted is None:
            return []
        if theme not in filters:
            order.append(theme)
            filters[theme] = []
        filters[theme].append(wanted)
    if len(order) > HOSTED_MAX_THEMES:
        return []
    plan = []
    for theme in order:
        base = dict(_OSM_ONLY.get(theme, {}))
        merged = _merge_filters(base, filters[theme])
        if merged is None:
            return []
        if theme == "buildings":




            merged = ([dict(one, source=OSM_SOURCE) for one in merged] if isinstance(merged, list)
                      else dict(merged, source=OSM_SOURCE))
        plan.append((theme, merged))
    return plan


def hosted_themes(query) -> list:

    return [theme for theme, _ in hosted_plan(query)]


def hosted_caps() -> dict:





    return tuning.service_caps("hosted_caps", {})


def footprints_max_km2() -> float:

    pair = hosted_caps().get("footprints")
    if isinstance(pair, (list, tuple)) and pair:
        return float(pair[0])
    return FOOTPRINTS_MAX_KM2


def provider_of(name: str) -> str:

    declared = spec(name)
    return declared.provider if declared is not None else ""


def provider_max_km2(name: str) -> tuple:

    if provider_of(name) == "footprints":
        km2 = footprints_max_km2()
        return km2, f"the footprint service answers HTTP 400 above {km2:.0f} km²"
    return 0.0, ""


def own_overpass_host() -> str:











    import urllib.parse

    try:
        from ..core import catalog

        mirrors = list(catalog.overpass_mirrors())
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Overpass mirror list unreadable, keeping the shipped host: {exc}")
        return OWN_OVERPASS_HOST
    for url in mirrors:
        host = (urllib.parse.urlsplit(str(url)).hostname or "").lower()
        if host.endswith(".terra-lab.ai") or host == "terra-lab.ai":
            return host
    return OWN_OVERPASS_HOST


def hosted_cap_km2(themes) -> float:

    table = hosted_caps()
    caps = [table[theme][0] for theme in themes if theme in table]
    return min(caps) if caps else 0.0


def hosted_ceiling(name: str, args: dict) -> tuple[list, float]:

    if provider_of(name) != "overpass" or not isinstance(args, dict):
        return [], 0.0
    themes = hosted_themes(args.get("query"))
    return (themes, hosted_cap_km2(themes)) if themes else ([], 0.0)










OWN_OVERPASS_DOWN_S = 300.0
_own_overpass_down_since: float | None = None


def note_own_overpass_down() -> None:

    global _own_overpass_down_since
    _own_overpass_down_since = time.monotonic()
    log_warning("Own Overpass instance unreachable; the wider own-host ceilings "
                f"are closed for {OWN_OVERPASS_DOWN_S:.0f}s.")


def own_overpass_down() -> bool:

    since = _own_overpass_down_since
    return since is not None and (time.monotonic() - since) < OWN_OVERPASS_DOWN_S


def own_overpass() -> bool:








    if own_overpass_down():
        return False
    try:
        from ..core import catalog

        first = (catalog.overpass_mirrors() or [""])[0]
    except Exception:  # noqa: BLE001
        return False
    return own_overpass_host() in str(first or "").lower()


def dense_hard_km2(name: str) -> float:

    return dense_max_km2()


def hard_cap_km2(name: str, args: dict) -> float:

    dense = is_dense(name, args)
    hard = dense_hard_km2(name) if dense else limits.current("MAX_FETCH_KM2")
    served, _ = provider_max_km2(name)
    if dense and served and served < hard:
        hard = served
    themes, hosted = hosted_ceiling(name, args)
    if themes:
        hard = hosted

    return max(hard, own_split_km2(name, args))








FIT_MARGIN = limits.FIT_MARGIN








NEAR_MISS = 1.5


def clamp_to_cap(name: str, args: dict) -> dict:








    box = bbox_of(args)
    if box is None or not isinstance(args, dict) or lifted(args):
        return {}
    area, hard = zone_km2(args), hard_cap_km2(name, args)
    if area is None or hard <= 0 or area <= hard or area > hard * NEAR_MISS:
        return {}
    fitted = largest_fitting_bbox(box, hard)
    if fitted is None:
        return {}
    south, west, north, east = fitted
    used = round(float(limits.bbox_km2(south, west, north, east)), 1)
    raw = args.get("bbox")
    if isinstance(raw, dict) and "xmin" in raw:
        args["bbox"] = {"xmin": west, "ymin": south, "xmax": east, "ymax": north}
    elif isinstance(raw, (list, tuple)):
        args["bbox"] = [west, south, east, north]
    else:
        args["bbox"] = {"south": south, "west": west, "north": north, "east": east}
    if _num(args.get("confirm_area_km2")) is not None:
        args["confirm_area_km2"] = used
    return {"asked_km2": round(float(area), 1), "box_km2": used,
            "bbox": {"south": south, "west": west, "north": north, "east": east},
            "note": (f"The box asked for measured {area:,.1f} km² and one call covers {hard:,.0f} km², so it "
                     f"was read at the size that fits, {used:,.1f} km² about the same centre. The bbox in "
                     "this result is what is on the map; the rest of the zone needs another call.")}


def largest_fitting_bbox(box, hard: float):

    return limits.shrink_bbox(box, hard, FIT_MARGIN)


def fitting_zone(args: dict, hard: float) -> dict:




    box = bbox_of(args) or (_canvas_bbox() if args.get("use_canvas_extent") else None)
    fitted = largest_fitting_bbox(box, hard) if box else None
    if fitted is None:
        return {}
    south, west, north, east = fitted
    return {"max_bbox": {"south": south, "west": west, "north": north, "east": east},
            "max_bbox_km2": round(float(limits.bbox_km2(south, west, north, east)), 1)}


def partial_sentence(fit: dict, area: float) -> str:





    if not fit or not area:
        return ""
    box = fit["max_bbox"]
    share = max(1, round(100 * fit["max_bbox_km2"] / area)) if area > 0 else 0
    return (f" Only part of the zone ({share}%): south={box['south']}, west={box['west']}, "
            f"north={box['north']}, east={box['east']}.")


def whole_zone_route(name: str, args: dict, area: float) -> str:

    provider = provider_of(name)
    themes = hosted_themes(args.get("query")) if provider == "overpass" else []
    if provider == "footprints":
        pair = hosted_caps().get("buildings")
        per_call = f", {pair[0]:,.0f} km² a call" if pair else ""
        return (f" Whole zone: fetch_overture theme buildings, same bbox (the same footprints merged"
                f"{per_call}), clip_to a named place, or full_extent.")
    if themes:
        return " Whole zone: full_extent if the user named the place, else fetch_overture clip_to it."
    if provider == "overpass" and own_overpass_down():
        return (" Whole zone: TerraLab's Overpass reads it in tiles once it answers again (minutes); "
                "or full_extent if the user named the place.")
    return " Whole zone: full_extent if the user named the place (read to disk, no area cap)."


def fitting_sentence(fit: dict) -> str:
    if not fit:
        return ""
    box = fit["max_bbox"]




    return (f" south={box['south']}, west={box['west']}, north={box['north']}, "
            f"east={box['east']} ({fit['max_bbox_km2']:,.1f} km2), or smaller, fits.")


def hosted_fallback(name: str, args: dict) -> list:





    themes, cap = hosted_ceiling(name, args)
    if not themes:
        return []
    area = zone_km2(args)
    if area is None or (area > cap and not lifted(args)):


        return []
    return themes




ZONE_ARGUMENTS = (
    "The area in km² needs the user's yes; confirm_area_km2 (one decimal) from {quiet:.0f} km² up "
    "then continues the call. One zone is one call: full_extent loads the whole named place in the "
    "same call."
)

def _num(value) -> float | None:
    try:
        if isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def bbox_of(args: dict) -> tuple[float, float, float, float] | None:

    raw = args.get("bbox")
    if isinstance(raw, dict):
        south = _num(raw.get("south", raw.get("ymin")))
        west = _num(raw.get("west", raw.get("xmin")))
        north = _num(raw.get("north", raw.get("ymax")))
        east = _num(raw.get("east", raw.get("xmax")))
    elif isinstance(raw, (list, tuple)) and len(raw) == 4:
        west, south, east, north = (_num(v) for v in raw)
    else:
        south = _num(args.get("south"))
        west = _num(args.get("west"))
        north = _num(args.get("north"))
        east = _num(args.get("east"))
    if None in (south, west, north, east):
        return None
    if not (-90 <= south <= 90 and -90 <= north <= 90 and -180 <= west <= 180 and -180 <= east <= 180):
        return None
    if north <= south or east <= west:
        return None
    return (south, west, north, east)


def not_degrees(west, south, east, north) -> str:







    try:
        west, south, east, north = (float(v) for v in (west, south, east, north))
    except (TypeError, ValueError):
        return ""
    if -180 <= west <= 180 and -180 <= east <= 180 and -90 <= south <= 90 and -90 <= north <= 90:
        return ""
    return (f"bbox west={west:g}, south={south:g}, east={east:g}, north={north:g} is not in EPSG:4326 degrees "
            "(longitude -180 to 180, latitude -90 to 90): these look like projected metres. Transform the box "
            "to degrees first, or name the place.")


def _canvas_bbox() -> tuple[float, float, float, float] | None:
    try:
        from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject
        from qgis.utils import iface

        canvas = iface.mapCanvas()
        extent = canvas.extent()
        crs = canvas.mapSettings().destinationCrs()
        wgs = QgsCoordinateReferenceSystem("EPSG:4326")
        if crs.isValid() and crs != wgs:
            extent = QgsCoordinateTransform(crs, wgs, QgsProject.instance()).transformBoundingBox(extent)
        return (extent.yMinimum(), extent.xMinimum(), extent.yMaximum(), extent.xMaximum())
    except Exception:  # noqa: BLE001
        return None


def zone_km2(args: dict) -> float | None:

    box = bbox_of(args)
    if box is None and args.get("use_canvas_extent"):
        box = _canvas_bbox()
    if box is None:
        return None
    try:
        return float(limits.bbox_km2(*box))
    except Exception:  # noqa: BLE001
        return None


def is_dense(name: str, args: dict) -> bool:
    kind = ZONE_TOOLS.get(name, "query")
    if kind == "dense":
        return True
    if kind == "sparse":
        return False
    text = " ".join(str(v) for k, v in args.items() if isinstance(v, str) and k != "layer_name")
    return bool(_DENSE_RE.search(text))


def requested_count(args: dict) -> int | None:
    for key in COUNT_KEYS:
        value = args.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _confirmed(args: dict, area: float) -> bool:
    confirmed = _num(args.get("confirm_area_km2"))
    return confirmed is not None and abs(confirmed - area) <= max(0.15, area * 0.1)








FULL_EXTENT = "full_extent"


LIFT_HINT = " full_extent loads it whole when the user's own words ask for all of it: their words and the place."


def lifted(args) -> bool:








    value = args.get(FULL_EXTENT) if isinstance(args, dict) else None
    return (isinstance(value, dict) and bool(str(value.get("quote") or "").strip())
            and bool(str(value.get("place") or "").strip()))


def check(name: str, args: dict) -> dict:


    args = args if isinstance(args, dict) else {}
    if lifted(args):


        return {}
    if name in ZONE_TOOLS:
        area = zone_km2(args)
        if area is not None:
            dense = is_dense(name, args)
            quiet = quiet_km2() if dense else quiet_sparse_km2()
            hard = dense_hard_km2(name) if dense else limits.current("MAX_FETCH_KM2")
            served, refuses = provider_max_km2(name)
            provider = refuses if dense and served and served < hard else ""
            if provider:
                hard = served
            own = own_split_km2(name, args)
            if own > hard:






                hard, quiet, provider = own, own, ""
            hosted, hosted_cap = hosted_ceiling(name, args)
            if hosted and own and area > hosted_cap:





                hosted = []




            ceiling, asked = (hosted_cap if hosted else hard), area
            if 0 < ceiling < area <= ceiling * NEAR_MISS and bbox_of(args) is not None:
                area = min(area, ceiling)
            if hosted and area > hosted_cap:
                fit = fitting_zone(args, hosted_cap)
                return {


                    "error": (f"This zone is too large to load at once: {area:,.1f} km², and TerraLab's tiles "
                              f"clip {', '.join(hosted)} to at most {hosted_cap:,.0f} km² at a time."),
                    "routes": (CLIP_TO_HINT + LIFT_HINT + WHOLE_BEFORE_PART).strip(),


                    "suggestion": (whole_zone_route(name, args, area)
                                   + partial_sentence(fit, area)).strip(),
                    "code": limits.CEILING_CODE,
                    **coded(hint="area_cap", variant=name),
                }
            if hosted:



                return {}
            if area > hard:
                fit = fitting_zone(args, hard)
                return {
                    "error": (f"This zone is too large to load at once: {area:,.1f} km², and {provider}."
                              if provider else


                              f"This zone is too large to load at once: {area:,.1f} km², the most one "
                              f"load reads in tiles from TerraLab's Overpass is {hard:,.0f} km²."
                              if own and hard == own else
                              f"This zone is too large to load at once: {area:,.1f} km², the cap for "
                              f"{'dense features' if dense else 'one load'} is {hard:.0f} km². Loading it "
                              "would take minutes and leave a layer QGIS cannot draw."),








                    "routes": ((FOOTPRINTS_WHOLE if provider else LIFT_HINT) + WHOLE_BEFORE_PART).strip(),




                    "suggestion": (whole_zone_route(name, args, area)
                                   + partial_sentence(fit, area)).strip(),
                    "code": limits.CEILING_CODE,
                    **coded(hint="area_cap", variant=name),
                }
            if area > quiet:
                if not (_confirmed(args, area) or _confirmed(args, asked)):




                    args["confirm_area_km2"] = round(float(asked), 1)
                return {"label": LABEL, "area_km2": area,
                        "sentence": (f"Load {'dense ' if dense else ''}data over {area:,.1f} km² with {name}? "
                                     "This can take a while and slow QGIS down.")}
    if name in HELD_TOOLS:
        count = requested_count(args)
        if count is not None and count > hard_max_features():
            return {
                "error": (f"{name} asks for {count:,} features, over the {hard_max_features():,} QGIS can "
                          "hold in memory on this computer."),
                "suggestion": HELD_TOOLS[name],
                "code": limits.CEILING_CODE,
                **coded(hint="feature_ceiling", variant="held"),
            }
    return {}


def _cap_fitting_sentence(count: int, args: dict, cap: int) -> str:









    area = zone_km2(args)
    if area is None or area <= 0 or count <= 0 or cap <= 0 or count <= cap:
        return ""
    fits = area * cap / float(count)
    fit = fitting_zone(args, fits)
    if not fit:
        return ""
    box = fit["max_bbox"]
    return (f" At the {count / area:,.0f} features per km2 this zone just returned, {cap:,} of them "
            f"is about {fits:,.1f} km2: south={box['south']}, west={box['west']}, "
            f"north={box['north']}, east={box['east']} ({fit['max_bbox_km2']:,.1f} km2), same centre. "
            "That box or a smaller one fits.")


def coded(hint: str, variant: str = "") -> dict:



    return {"hint": hint, "variant": variant} if variant else {"hint": hint}


def too_many(count: int, args: dict, what: str = "this source", held: str = "") -> dict | None:








    if not held:
        return None
    try:
        count = int(count)
    except (TypeError, ValueError):
        return None
    if count <= hard_max_features():
        return None
    return {
        "_error": (f"{what} holds {count:,} features, over the {hard_max_features():,} QGIS can hold in "
                   "memory on this computer. It was not added."),
        "suggestion": HELD_TOOLS.get(held, "") + _cap_fitting_sentence(count, args, hard_max_features()),
        "feature_count": count,
        **coded(hint="feature_ceiling", variant="held"),
    }


def too_many_features(layer, args: dict, what: str = "this source", held: str = "") -> dict | None:

    try:
        count = int(layer.featureCount())
    except Exception:  # noqa: BLE001
        return None
    return too_many(count, args, what, held=held)
