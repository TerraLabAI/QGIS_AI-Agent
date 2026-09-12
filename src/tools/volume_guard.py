# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Guard the tools that pull data into the project: only as much as was asked."""







































from __future__ import annotations

import re
import time

from ..core import limits, tuning
from ..core.logger import log_warning







def quiet_km2() -> float:
    """Square kilometres a dense fetch may cover without asking (a district)."""
    return limits.current("FETCH_QUIET_KM2")


def quiet_sparse_km2() -> float:
    """The same for a sparse one (points of interest, a few features per km²)."""
    return limits.current("FETCH_QUIET_SPARSE_KM2")


def dense_max_km2() -> float:
    """Above this a dense fetch from a public service is refused whatever the user says: the download alone is minutes, and the layer that comes."""


    return limits.current("FETCH_DENSE_MAX_KM2")

















OWN_OVERPASS_HOST = "overpass.terra-lab.ai"


def own_overpass_max_km2() -> float:
    """The dense ceiling that applies while our own instance answers."""
    return limits.current("FETCH_OWN_OVERPASS_MAX_KM2")


























SELECTIVE_MAX_KM2 = 20_000.0




_SELECTIVE_TAG = re.compile(
    r'\[\s*[\'"]?(ref(?::[\w:]+)?|int_ref|nat_ref|reg_ref|loc_ref|uic_ref|wikidata|wikipedia|'
    r'iata|icao|gnis:feature_id|osm_id)[\'"]?\s*=\s*[\'"]?[^\]~=]', re.IGNORECASE)


_AREA_STATEMENT = re.compile(r"\barea\s*[\[(:]|\(\s*area\b", re.IGNORECASE)


def quiet_features() -> int:
    """Features a call may ask for, or put into the project, without asking."""
    return int(limits.current("FETCH_QUIET_FEATURES"))








def hard_max_features() -> int:
    """The ceiling once the user has said yes."""
    return int(limits.current("FETCH_HARD_MAX_FEATURES"))













FOOTPRINTS_MAX_KM2 = 25.0
PROVIDER_MAX_KM2 = {
    "fetch_building_footprints": (
        FOOTPRINTS_MAX_KM2,
        f"the footprint service answers HTTP 400 above {FOOTPRINTS_MAX_KM2:.0f} km²",
    ),
}

LABEL = "data volume"



ZONE_TOOLS = {
    "fetch_osm_data": "query",
    "fetch_building_footprints": "dense",
    "add_pmtiles_layer": "query",
}

COUNT_KEYS = ("max_features", "limit", "max_items", "max_results")
COUNT_TOOLS = {"add_wfs_layer", "add_points_from_json", "add_arcgis_rest_layer", "get_features"}








LOAD_TOOLS = {"add_vector_from_url", "add_arcgis_rest_layer", "add_points_from_json",
              "fetch_osm_data", "fetch_building_footprints", "fetch_overture", "add_data"}









_DENSE_RE = re.compile(
    r"building|highway|road|street|footway|path|addr|landuse|natural|water|wood|forest|"
    r"parcel|cadast|land_?cover|power=|railway", re.IGNORECASE)






HOSTED_INSTEAD = (
    ' Or fetch_overture(theme, mode="stream"): no area cap.'
)












HOSTED_CAPS = {
    "buildings": (200.0, 1.0),
    "roads": (1000.0, 2.0),
    "places": (2000.0, 2.0),
    "addresses": (2000.0, 2.0),
    "pois": (2000.0, 2.0),
    "landuse": (5000.0, 3.0),
    "water_areas": (5000.0, 3.0),
    "waterways": (5000.0, 3.0),
    "power": (5000.0, 3.0),
    "railways": (5000.0, 3.0),
    "boundaries": (5000.0, 3.0),
    "divisions": (20000.0, 3.0),
}






def hosted_quiet_km2() -> float:
    return limits.current("FETCH_HOSTED_QUIET_KM2")


def hosted_quiet_sparse_km2() -> float:
    return limits.current("FETCH_HOSTED_QUIET_SPARSE_KM2")









HOSTED_THEMES = (
    ("roads", re.compile(r"\bhighway\b", re.IGNORECASE)),
    ("waterways", re.compile(r"\bwaterway\b", re.IGNORECASE)),
    ("water_areas", re.compile(r"natural\W*=\W*.?water\b|\bwater\W*=", re.IGNORECASE)),
    ("buildings", re.compile(r"\bbuilding\b", re.IGNORECASE)),
    ("landuse", re.compile(r"\blanduse\b", re.IGNORECASE)),
    ("railways", re.compile(r"\brailway\b", re.IGNORECASE)),
    ("power", re.compile(r"\bpower\W*=", re.IGNORECASE)),
    ("boundaries", re.compile(r"\bboundary\W*=", re.IGNORECASE)),
    ("pois", re.compile(r"\b(amenity|shop|tourism|leisure)\b", re.IGNORECASE)),
)
HOSTED_MAX_THEMES = 3



HOSTED_WAY_THEMES = frozenset({
    "roads", "waterways", "water_areas", "buildings", "landuse", "railways", "power", "boundaries",
})


_ELEMENT = re.compile(r"\b(node|way|relation|rel|nwr|nw|nr|wr)\s*[\[(]", re.IGNORECASE)






_POINT_VALUE = re.compile(
    r"\b(?:highway|railway)\W{0,3}("
    r"bus_stop|crossing|traffic_signals|street_lamp|stop|give_way|turning_circle|turning_loop|"
    r"mini_roundabout|motorway_junction|speed_camera|elevator|passing_place|milestone|toll_gantry|"
    r"trailhead|emergency_bay|station|halt|tram_stop|level_crossing|subway_entrance|buffer_stop|"
    r"switch|signal)\b", re.IGNORECASE)


def asks_for_points(query) -> bool:
    """True when the query wants points off the network rather than the network."""
    text = str(query or "")
    elements = {m.group(1).lower() for m in _ELEMENT.finditer(text)}
    return (elements == {"node"}) or bool(_POINT_VALUE.search(text))


def is_selective(query) -> bool:
    """True when the query pins an identifier tag to one exact value."""




    text = str(query or "")
    if _AREA_STATEMENT.search(text):



        return False
    return bool(_SELECTIVE_TAG.search(text))


def selective_max_km2(name: str, args: dict) -> float:
    """The wider ceiling a selective Overpass query earns, or 0.0."""






    if name != "fetch_osm_data" or not isinstance(args, dict):
        return 0.0
    if not is_selective(args.get("query")) or not own_overpass():
        return 0.0
    if hosted_themes(args.get("query")):
        return 0.0

    return min(SELECTIVE_MAX_KM2, tuning.number("limits", "selective_max_km2", SELECTIVE_MAX_KM2))


def hosted_themes(query) -> list:
    """The hosted themes an Overpass query's tags map to, in query order."""
    text = str(query or "")
    points = asks_for_points(text)
    found = []
    for theme, pattern in HOSTED_THEMES:
        if points and theme in HOSTED_WAY_THEMES:
            continue
        match = pattern.search(text)
        if match:
            found.append((match.start(), theme))
    return [theme for _, theme in sorted(found)][:HOSTED_MAX_THEMES]


def hosted_caps() -> dict:
    """``{theme: (km2, degrees)}``: the service's own box caps as it states them."""







    return tuning.service_caps("hosted_caps", HOSTED_CAPS)


def footprints_max_km2() -> float:
    """What the footprint service refuses above, as the service states it."""
    pair = hosted_caps().get("footprints")
    if isinstance(pair, (list, tuple)) and pair:
        return float(pair[0])
    return FOOTPRINTS_MAX_KM2


def provider_max_km2(name: str) -> tuple:
    """``(km2, why)`` for a tool whose provider stops before we do."""
    if name == "fetch_building_footprints":
        km2 = footprints_max_km2()
        return km2, f"the footprint service answers HTTP 400 above {km2:.0f} km²"
    return PROVIDER_MAX_KM2.get(name, (0.0, ""))


def own_overpass_host() -> str:
    """The host of our own Overpass instance, as the served mirror list names it."""










    import urllib.parse

    try:
        from ..core import catalog

        mirrors = list(catalog.overpass_mirrors())
    except Exception as exc:  # noqa: BLE001 - no served list is the shipped host
        log_warning(f"Overpass mirror list unreadable, keeping the shipped host: {exc}")
        return OWN_OVERPASS_HOST
    for url in mirrors:
        host = (urllib.parse.urlsplit(str(url)).hostname or "").lower()
        if host.endswith(".terra-lab.ai") or host == "terra-lab.ai":
            return host
    return OWN_OVERPASS_HOST


def hosted_cap_km2(themes) -> float:
    """The box our open data API clips for these themes in one call: the tightest of them."""
    table = hosted_caps()
    caps = [table[theme][0] for theme in themes if theme in table]
    return min(caps) if caps else 0.0


def hosted_ceiling(name: str, args: dict) -> tuple[list, float]:
    """``(themes, cap_km2)`` when the call's tags name hosted themes, else ``([], 0.0)``."""
    if name != "fetch_osm_data" or not isinstance(args, dict):
        return [], 0.0
    themes = hosted_themes(args.get("query"))
    return (themes, hosted_cap_km2(themes)) if themes else ([], 0.0)










OWN_OVERPASS_DOWN_S = 300.0
_own_overpass_down_since: float | None = None


def note_own_overpass_down() -> None:
    """Remember that our own Overpass did not answer, as of now."""
    global _own_overpass_down_since
    _own_overpass_down_since = time.monotonic()
    log_warning("Own Overpass instance unreachable; the wider own-host ceilings "
                f"are closed for {OWN_OVERPASS_DOWN_S:.0f}s.")


def own_overpass_down() -> bool:
    """Is our own Overpass inside the window of a failure we have just seen?"""
    since = _own_overpass_down_since
    return since is not None and (time.monotonic() - since) < OWN_OVERPASS_DOWN_S


def forget_own_overpass_down() -> None:
    """Drop the memo. For the suite, and for a session that wants to try again."""
    global _own_overpass_down_since
    _own_overpass_down_since = None


def own_overpass() -> bool:
    """True when the first Overpass mirror this build would ask is our own instance."""







    if own_overpass_down():
        return False
    try:
        from ..core import catalog

        first = (catalog.overpass_mirrors() or [""])[0]
    except Exception:  # noqa: BLE001 - no catalog, no claim
        return False
    return own_overpass_host() in str(first or "").lower()


def dense_hard_km2(name: str) -> float:
    """The dense ceiling for *name*: ours when we serve the query, the public one otherwise."""
    if name == "fetch_osm_data" and own_overpass():
        return own_overpass_max_km2()
    return dense_max_km2()


def hard_cap_km2(name: str, args: dict) -> float:
    """The area above which one *name* call is refused outright."""
    dense = is_dense(name, args)
    hard = dense_hard_km2(name) if dense else limits.current("MAX_FETCH_KM2")
    served, _ = provider_max_km2(name)
    if dense and served and served < hard:
        hard = served
    themes, hosted = hosted_ceiling(name, args)
    if themes:
        hard = hosted
    return max(hard, selective_max_km2(name, args)) if not themes else hard







FIT_MARGIN = 0.95









NEAR_MISS = 1.5


def clamp_to_cap(name: str, args: dict) -> dict:
    """Shrink a bbox that is only just over *name*'s ceiling back to it, in place."""







    box = bbox_of(args)
    if box is None or not isinstance(args, dict):
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
    return {"asked_km2": round(float(area), 1), "area_km2": used,
            "bbox": {"south": south, "west": west, "north": north, "east": east},
            "note": (f"The box asked for measured {area:,.1f} km² and one call covers {hard:,.0f} km², so it "
                     f"was read at the size that fits, {used:,.1f} km² about the same centre. The bbox in "
                     "this result is what is on the map: say so, and call again for the rest if the user "
                     "wants it.")}


def largest_fitting_bbox(box, hard: float):
    """The bbox of *box* shrunk about its centre to just under *hard* km2."""
    try:
        south, west, north, east = (float(v) for v in box)
        area = float(limits.bbox_km2(south, west, north, east))
        hard = float(hard)
    except (TypeError, ValueError):
        return None
    if hard <= 0 or area <= 0 or area <= hard:
        return None
    scale = (hard * FIT_MARGIN / area) ** 0.5
    mid_lat, mid_lon = (south + north) / 2.0, (west + east) / 2.0
    half_lat = (north - south) / 2.0 * scale
    half_lon = (east - west) / 2.0 * scale
    return (round(mid_lat - half_lat, 5), round(mid_lon - half_lon, 5),
            round(mid_lat + half_lat, 5), round(mid_lon + half_lon, 5))


def fitting_zone(args: dict, hard: float) -> dict:
    """``{max_bbox, max_bbox_km2}`` for a refused zone, or ``{}`` when there is none."""



    box = bbox_of(args) or (_canvas_bbox() if args.get("use_canvas_extent") else None)
    fitted = largest_fitting_bbox(box, hard) if box else None
    if fitted is None:
        return {}
    south, west, north, east = fitted
    return {"max_bbox": {"south": south, "west": west, "north": north, "east": east},
            "max_bbox_km2": round(float(limits.bbox_km2(south, west, north, east)), 1)}


def fitting_sentence(fit: dict) -> str:
    if not fit:
        return ""
    box = fit["max_bbox"]




    return (f" Retry with south={box['south']}, west={box['west']}, north={box['north']}, "
            f"east={box['east']} ({fit['max_bbox_km2']:,.1f} km2), or smaller.")


def hosted_fallback(name: str, args: dict) -> list:
    """The themes a fetch_osm_data call is served from TerraLab's tiles for, or []."""




    themes, cap = hosted_ceiling(name, args)
    if not themes:
        return []
    area = zone_km2(args)
    if area is None:
        return []
    quiet = quiet_km2() if is_dense(name, args) else quiet_sparse_km2()
    if area <= quiet or area > cap:
        return []
    return themes






ZONE_ARGUMENTS = (
    "Say the area in km², get the user's yes, then call again with confirm_area_km2 (one decimal) "
    "from {quiet:.0f} km² up. One zone is one call: narrow it, never tile it."
)


def _num(value) -> float | None:
    try:
        if isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def bbox_of(args: dict) -> tuple[float, float, float, float] | None:
    """``(south, west, north, east)`` in degrees from either convention, or None."""
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
    except Exception:  # noqa: BLE001 - no canvas, no zone
        return None


def zone_km2(args: dict) -> float | None:
    """The ground area of the call's zone in km², or None when it carries none."""
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








def _stale_confirm(name: str, args: dict, area: float) -> dict:
    """The refusal for a confirm_area_km2 that does not match the bbox, or {}."""
    confirmed = _num(args.get("confirm_area_km2"))
    if confirmed is None:
        return {}
    return {
        "error": (f"{name}: confirm_area_km2 says {confirmed:,.1f} km² but this bbox measures "
                  f"{area:,.1f} km²."),

        "suggestion": (f"Both must be the same call: pass confirm_area_km2={area:.1f}, or send back the "
                       f"bbox whose {confirmed:,.1f} km² you announced."),
        "code": limits.CEILING_CODE,
    }


def check(name: str, args: dict) -> dict:
    """{label, sentence} when the call needs the card, {error, suggestion} when refused, {} when it may run quietly."""

    args = args if isinstance(args, dict) else {}
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
            selective = selective_max_km2(name, args)
            if selective > hard:






                hard, quiet, provider = selective, selective, ""
            hosted, hosted_cap = hosted_ceiling(name, args)




            ceiling, asked = (hosted_cap if hosted else hard), area
            if 0 < ceiling < area <= ceiling * NEAR_MISS and bbox_of(args) is not None:
                area = min(area, ceiling)
            if hosted and area > hosted_cap:
                fit = fitting_zone(args, hosted_cap)
                return {
                    "error": (f"Zone too large for one {name} call: {area:,.1f} km², and TerraLab's tiles "
                              f"clip {', '.join(hosted)} to at most {hosted_cap:,.0f} km² in one call."),



                    "suggestion": ((fitting_sentence(fit).strip() if fit else "")
                                   + HOSTED_INSTEAD + " Ask the user which part matters most."),
                    "code": limits.CEILING_CODE,
                }
            if hosted and area > quiet:
                quiet = hosted_quiet_km2() if dense else hosted_quiet_sparse_km2()
                themes = ", ".join(hosted)
                if area <= quiet:
                    return {}
                if not (_confirmed(args, area) or _confirmed(args, asked)):
                    return _stale_confirm(name, args, asked) or {
                        "error": (f"{name} over {area:,.1f} km² is served from TerraLab tiles ({themes}), "
                                  "and the user has not been told the size."),
                        "suggestion": (f"Say the area ({area:,.1f} km²) to the user, get a yes, and call again "
                                       f"with confirm_area_km2={area:.1f}. Do not narrow or tile the zone: "




                                       f"one call serves up to {hosted_cap:,.0f} km²."),
                        "code": limits.CEILING_CODE,
                    }
                return {"label": LABEL, "area_km2": area,
                        "sentence": (f"Load {themes} over {area:,.1f} km² from TerraLab tiles with {name}? "
                                     "The layer can slow QGIS down.")}
            if area > hard:
                fit = fitting_zone(args, hard)
                return {
                    "error": (f"Zone too large for one {name} call: {area:,.1f} km², and {provider}."
                              if provider else



                              f"Zone too large for one {name} call: {area:,.1f} km², the cap for "
                              f"{'dense features' if dense else 'a fetch'} is {hard:.0f} km². The answer "
                              "would be more than one download carries."
                              if dense and hard == own_overpass_max_km2() else
                              f"Zone too large for one {name} call: {area:,.1f} km², the cap for "
                              f"{'dense features' if dense else 'a fetch'} is {hard:.0f} km². Loading it "
                              "would take minutes and leave a layer QGIS cannot draw."),









                    "suggestion": (fitting_sentence(fit).strip()
                                   + (HOSTED_INSTEAD
                                      if name == "fetch_osm_data" and hosted_themes(args.get("query"))
                                      else " Ask the user which part matters most.")
                                   + " " + ZONE_ARGUMENTS.format(quiet=quiet)),
                    "code": limits.CEILING_CODE,
                }
            if area > quiet:
                if not (_confirmed(args, area) or _confirmed(args, asked)):
                    return _stale_confirm(name, args, asked) or {
                        "error": (f"{name} over {area:,.1f} km² loads more than a district's worth of "
                                  f"{'dense ' if dense else ''}features, and the user has not been told the size."),
                        "suggestion": ZONE_ARGUMENTS.format(quiet=quiet),
                        "code": limits.CEILING_CODE,
                    }
                return {"label": LABEL, "area_km2": area,
                        "sentence": (f"Load {'dense ' if dense else ''}data over {area:,.1f} km² with {name}? "
                                     "This can take a while and slow QGIS down.")}
    if name in COUNT_TOOLS:
        count = requested_count(args)
        if count is not None:
            if count > hard_max_features():
                return {
                    "error": (f"{name} asks for {count:,} features, over the {hard_max_features():,} one call "
                              "may put into the project."),
                    "suggestion": (f"Ask for at most {quiet_features():,} first, look at them, then filter by "
                                   "attribute or by the current view instead of loading everything."),
                    "code": limits.CEILING_CODE,
                }
            if count > quiet_features() and not args.get("confirm_large"):
                return {
                    "error": (f"{name} asks for {count:,} features; above {quiet_features():,} the user decides."),
                    "suggestion": (f"Start with {quiet_features():,} or fewer. If the user asked for all of them, "
                                   "say how many that is and pass confirm_large true."),
                    "code": limits.CEILING_CODE,
                }
            if count > quiet_features():
                return {"label": LABEL,
                        "sentence": f"Load up to {count:,} features with {name}? This can slow QGIS down."}
    if name in LOAD_TOOLS and args.get("confirm_large") and name not in ZONE_TOOLS:
        return {"label": LABEL,
                "sentence": f"Load a large dataset with {name}? This can take a while and slow QGIS down."}
    return {}


def consented(args: dict) -> bool:
    """The user said yes to this call's size: confirm_large, or a confirmed zone."""
    args = args if isinstance(args, dict) else {}
    if args.get("confirm_large"):
        return True
    area = zone_km2(args)
    return area is not None and _confirmed(args, area)


def _cap_fitting_sentence(count: int, args: dict, cap: int) -> str:
    """The zone that holds about *cap* features at the density just measured, as a sentence."""










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
            "Use it or a smaller one; do not guess your way down.")


def too_many(count: int, args: dict, what: str = "this source") -> dict | None:
    """The refusal to put ``count`` features into the project, or None when they may go in."""






    try:
        count = int(count)
    except (TypeError, ValueError):
        return None
    if count < 0:
        return None
    if count > hard_max_features():
        return {
            "_error": (f"{what} holds {count:,} features, over the {hard_max_features():,} one call may put "
                       "into the project. It was not added."),
            "suggestion": ("Narrow the request: a smaller zone, a bbox on the service, an attribute filter, "
                           "or a regional extract. Never split the cap into many calls without an explicit "
                           "request." + _cap_fitting_sentence(count, args, hard_max_features())),
            "feature_count": count,
        }
    if count > quiet_features() and not consented(args):
        return {
            "_error": (f"{what} holds {count:,} features; above {quiet_features():,} the user decides. "
                       "It was not added."),
            "suggestion": (f"Narrow the zone or the filter so fewer than {quiet_features():,} features come back. "
                           f"If the user asked for all of them, say that it is {count:,} features, get a yes, and "
                           "call again with confirm_large true (the answer is kept for ten minutes, so the "
                           "second call does not download again)."
                           + _cap_fitting_sentence(count, args, quiet_features())),
            "feature_count": count,
        }
    return None


def too_many_features(layer, args: dict, what: str = "this source") -> dict | None:
    """``too_many`` for a layer already built: the count is read from it."""
    try:
        count = int(layer.featureCount())
    except Exception:  # noqa: BLE001
        return None
    return too_many(count, args, what)
