# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject

from ..core import net
from ..core.feature_requests import feature_request
from ..core.geometry_budget import VertexBudget
from ..core.logger import log_warning
from ..core.qt_compat import enum_member
from .data_common import _canvas_viewbox_4326, _fold, _is_number, _run_on_main_thread, _viewbox_bounds
from .data_geocoding import (
    _PLACE_AREA_LAYERS,
    _arrondissement_key,
    _geocode,
    _geocode_fetch,
    _parse_photon_forward,
    _photon_forward_url,
    _resolve_geocode_provider,
    _without_generic_place_words,
)
from .data_overture import (
    _CLIP_MAX_PARTS,
    _CLIP_SUBTYPE_ORDER,
    _HIT_SUBTYPES,
    _outline_contains,
    _overture_call,
    _polys_bbox,
    _rings_of,
)



_FOOTPRINT_SIDE_POINTS = 16


def _footprint_outline(layer, target):







    from qgis.core import QgsGeometry, QgsPointXY

    extent = layer.extent()
    if extent.isNull() or extent.isEmpty() or extent.width() <= 0 or extent.height() <= 0:
        return None
    xmin, ymin, xmax, ymax = extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()
    steps = _FOOTPRINT_SIDE_POINTS
    ring = ([QgsPointXY(xmin + (xmax - xmin) * i / steps, ymin) for i in range(steps)]
            + [QgsPointXY(xmax, ymin + (ymax - ymin) * i / steps) for i in range(steps)]
            + [QgsPointXY(xmax - (xmax - xmin) * i / steps, ymax) for i in range(steps)]
            + [QgsPointXY(xmin, ymax - (ymax - ymin) * i / steps) for i in range(steps)])
    ring.append(ring[0])
    geometry = QgsGeometry.fromPolygonXY([ring])
    if layer.crs().isValid() and layer.crs() != target:
        if geometry.transform(QgsCoordinateTransform(layer.crs(), target, QgsProject.instance())) != 0:
            return None
    polys = _rings_of(json.loads(geometry.asJson(7)))
    if not polys:
        return None
    kind = "raster" if not hasattr(layer, "wkbType") else "layer"
    return {"polys": polys, "label": layer.name(), "outline_source": f"footprint of the {kind} {layer.name()}",
            "selected_only": False}


def _outline_of_layer(name: str):










    def _read():


        from qgis.core import QgsGeometry, QgsWkbTypes

        polygon_type = enum_member(QgsWkbTypes, "GeometryType", "PolygonGeometry")
        wanted = name.strip().lower()
        target = QgsCoordinateReferenceSystem("EPSG:4326")
        layer = None
        footprint = None
        for candidate in QgsProject.instance().mapLayers().values():



            if wanted not in (candidate.name().strip().lower(), candidate.id().lower()):
                continue
            if hasattr(candidate, "wkbType") and hasattr(candidate, "getFeatures") and \
                    QgsWkbTypes.geometryType(candidate.wkbType()) == polygon_type:
                layer = candidate
                break

            footprint = footprint or _footprint_outline(candidate, target)
        if layer is None:
            return footprint
        selected = layer.selectedFeatureCount()
        count = selected or layer.featureCount()
        if count > _CLIP_MAX_PARTS:
            return {"_error": f"{layer.name()!r} holds {count:,} polygons, which is a map and not one outline.",
                    "code": "INVALID_ARGS",
                    "suggestion": "Select the feature to clip to, or pass the place name instead."}
        xform = None
        if layer.crs().isValid() and layer.crs() != target:
            xform = QgsCoordinateTransform(layer.crs(), target, QgsProject.instance())
        polys: list = []
        budget = VertexBudget()

        outline_request = feature_request(attributes=[])
        for feature in (layer.getSelectedFeatures(outline_request) if selected
                        else layer.getFeatures(outline_request)):
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                continue
            too_big = budget.oversize(geometry)
            if too_big or budget.exhausted():
                return {"_error": f"{layer.name()!r} is too detailed to clip to on this machine: "
                                  + (f"one polygon of {too_big:,} vertices" if too_big
                                     else f"more than {budget.total:,} vertices in total") + ".",
                        "code": "INVALID_ARGS",
                        "suggestion": "Simplify it first (native:simplifygeometries through run_processing), "
                                      "select one feature, or pass a bbox instead of clip_to."}
            if xform is not None:
                moved = QgsGeometry(geometry)
                if moved.transform(xform) != 0:
                    continue
                geometry = moved
            polys.extend(_rings_of(json.loads(geometry.asJson(7))))
        if not polys:
            return {"_error": f"{layer.name()!r} holds no polygon to clip to.",
                    "code": "INVALID_ARGS",
                    "suggestion": "Pass a boundary layer with features in it, or the place name."}
        return {"polys": polys, "label": layer.name(), "outline_source": f"layer {layer.name()}",
                "selected_only": bool(selected)}

    return _run_on_main_thread(_read, timeout=30)


def _place_tier(hit: dict) -> int:


    kind = str(hit.get("type") or "").lower()
    if kind in ("country", "state"):
        return 3
    if kind == "county" or (kind == "city" and str(hit.get("osm_value") or "").lower() == "city"):
        return 2
    return 1 if kind == "city" else 0


_NEAR_VIEW_DEG = 30.0


def _in_near_view(hit: dict, viewbox) -> bool:
    bounds = _viewbox_bounds(viewbox)
    if not bounds:
        return False
    west, south, east, north = bounds
    if max(east - west, north - south) > _NEAR_VIEW_DEG:
        return False
    return west <= float(hit["lon"]) <= east and south <= float(hit["lat"]) <= north


def _area_hits(name: str, viewbox) -> list:

    try:
        _provider_id, provider, base = _resolve_geocode_provider({})
    except ValueError:
        return []
    if provider.get("service") != "photon":
        return []
    url = _photon_forward_url(base, _without_generic_place_words(name), 5, None, viewbox,
                              layers=_PLACE_AREA_LAYERS)
    try:
        payload = _geocode_fetch(url)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log_warning(f"Place lookup among administrative areas failed for {name!r}: {exc}")
        return []
    return [hit for hit in (_parse_photon_forward(payload, True) or []) if _is_number(hit.get("lon"))]


def _named_near(name: str, hit: dict) -> list:

    try:
        _provider_id, provider, base = _resolve_geocode_provider({})
    except ValueError:
        return []
    if provider.get("service") != "photon" or not _is_number(hit.get("lon")) or not _is_number(hit.get("lat")):
        return []
    lon, lat = float(hit["lon"]), float(hit["lat"])
    url = _photon_forward_url(base, name, 5, None, f"{lon - 0.5},{lat - 0.5},{lon + 0.5},{lat + 0.5}")
    try:
        payload = _geocode_fetch(url)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log_warning(f"Place lookup of {name!r} alone failed: {exc}")
        return []
    return [one for one in (_parse_photon_forward(payload, True) or []) if _is_number(one.get("lon"))]


def _own_names(hit: dict) -> set:


    head = str(hit.get("display_name") or "").split(",")[0]
    return {_fold(part) for part in head.split(" / ")} - {""}


def _place_hit(name: str, hits: list) -> dict:










    asked = _fold(str(name).split(",")[0])
    if "," in str(name) and not any(asked in _own_names(hit) for hit in hits):



        again = _named_near(str(name).split(",")[0].strip(), hits[0])
        if any(asked in _own_names(hit) for hit in again):
            hits = again
    first = hits[0]






    if asked not in _own_names(first):
        same = [hit for hit in hits[1:] if _place_tier(hit) >= 2 and asked in _own_names(hit)]
        if same:
            return min(same, key=lambda hit: _box_area(_hit_box(hit) or (0, 0, 360, 180)))
    tier = _place_tier(first)
    viewbox = _run_on_main_thread(_canvas_viewbox_4326)
    if tier >= 2 or _in_near_view(first, viewbox):
        return first
    larger = next((hit for hit in hits[1:] if _place_tier(hit) >= 2), None)
    if larger is not None:
        return larger
    if tier == 0 and str(first.get("class") or "") != "place":



        placed = next((hit for hit in hits[1:] if str(hit.get("class") or "") == "place"), None)
        if placed is not None:
            return placed
    if tier == 0:



        qualified = "," in str(name)
        for hit in _area_hits(name, viewbox):
            found = _place_tier(hit)
            own = _fold(str(hit.get("display_name") or "").split(",")[0]) == asked
            if (found == 3 and (own or not qualified)) or (found == 2 and own):
                return hit
    return first


def _divisions_payloads(box, subtypes) -> dict:

















    import concurrent.futures

    if len(subtypes) == 1:
        subtype = subtypes[0]
        return {subtype: _overture_call("divisions", box, subtype=subtype)}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(subtypes)) as pool:
        futures = {subtype: pool.submit(_overture_call, "divisions", box, subtype=subtype)
                   for subtype in subtypes}
        return {subtype: future.result() for subtype, future in futures.items()}


def _outline_of_place(name: str):







    found = _geocode({"query": name, "limit": 5, "verbose": True})
    hits = [hit for hit in (found.get("results") or []) if _is_number(hit.get("lon"))]
    if not hits and (found.get("code") == net.NETWORK_ERROR or found.get("_error")):


        return found
    if not hits:
        return {"_error": f"No place was found under the name {name!r}.",
                "code": "INVALID_ARGS",
                "suggestion": "Check the spelling, add the country, or pass a bbox instead of clip_to."}
    hit = _place_hit(name, hits)
    lon, lat = float(hit["lon"]), float(hit["lat"])




    span = _NAME_SEARCH_HALF_DEG
    box = (lon - span, lat - span, lon + span, lat + span)
    hit_box = _hit_box(hit)

    asked_name = {_fold(str(name).split(",")[0])} - {""}
    wanted = asked_name | ({_fold(str(hit.get("display_name") or "").split(",")[0])} - {""})









    kind = (_HIT_SUBTYPES.get(str(hit.get("osm_value") or "").lower())
            or _HIT_SUBTYPES.get(str(hit.get("type") or "").lower(), ""))
    arrondissement = _arrondissement_key(name)
    code = str(hit.get("country_code") or "").upper()
    order = ((kind,) + tuple(s for s in _CLIP_SUBTYPE_ORDER if s != kind)) if kind else _CLIP_SUBTYPE_ORDER
    fallback = None



    named: list = []
    payloads = _divisions_payloads(box, order)
    for rank, subtype in enumerate(order):
        payload = payloads[subtype]
        if payload.get("code") == net.NETWORK_ERROR:


            return payload
        if payload.get("_error"):
            continue
        widest = 0.0
        for feature in payload.get("features") or []:
            polys = _rings_of(feature.get("geometry"))
            bounds = _polys_bbox(polys)
            if not bounds:
                continue
            covers = _outline_contains(lon, lat, {"polys": polys, "bbox": bounds})
            if covers:
                widest = max(widest, _box_area(bounds))
            label = str((feature.get("properties") or {}).get("name") or name)
            props = feature.get("properties") or {}
            outline = {"polys": polys, "label": label,
                       "outline_source": f"Overture divisions, subtype {subtype}",
                       "division": {"subtype": subtype, "country": props.get("country"),
                                    "region": props.get("region")}}


            same_arrondissement = arrondissement is not None and _arrondissement_key(label) == arrondissement
            exact = _fold(label) in wanted or same_arrondissement or (covers and subtype == kind and (
                subtype != "country" or not code or str(props.get("country") or "").upper() == code))




            extra = None if exact else _extra_words(label, asked_name)
            if exact or extra is not None:
                named.append(((exact, covers, _box_overlap(bounds, hit_box), -(extra or 0), -rank), outline))
            elif covers and subtype not in ("neighborhood", "macrohood"):


                fallback = fallback or outline
        best = max(named, key=lambda item: item[0]) if named else None
        if best is not None and best[0][0] and best[0][1] and (
                hit_box is None or best[0][2] >= _GOOD_OVERLAP
                or (subtype != kind and widest >= _PAST_HIT_BOX * _box_area(hit_box))):



            break
    if named:
        return max(named, key=lambda item: item[0])[1]




    hit_label = str(hit.get("display_name") or "").split(",")[0]
    named_after = _extra_words(hit_label, asked_name) is not None
    if _place_tier(hit) == 0 and not named_after:





        return _outline_around(hit, name)
    if fallback and named_after:
        fallback["note"] = (f"{str(name).split(',')[0].strip()!r} is the name of {hit_label.strip()}, which "
                            f"lies in {fallback['label']}: the clip used that division.")
        return fallback
    if fallback:




        asked = str(name).split(",")[0].strip()
        fallback["note"] = (
            f"No division within about 1 km of the geocoded point carries the name {asked!r}, at any "
            f"level from neighbourhood up, so the clip used "
            f"the smallest division covering it, {fallback['label']}. For the place itself, pass a "
            f"bbox about 2 km around the geocoded point instead of clip_to and say so. Do not ask "
            f"the user.")
        return fallback

    return dict(_error=f"{name!r} was found, but no administrative outline covers it.",  # noqa: C408
                code="INVALID_ARGS", hint="no_outline")




_NAME_SEARCH_HALF_DEG = 0.01


_GOOD_OVERLAP = 0.5


_PAST_HIT_BOX = 4.0


def _hit_box(hit: dict):

    bounds = hit.get("boundingbox")
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
        return None
    try:
        south, north, west, east = (float(value) for value in bounds)
    except (TypeError, ValueError):
        return None
    if not (west < east and south < north):
        return None
    return west, south, east, north


def _box_area(box) -> float:
    return max(box[2] - box[0], 0.0) * max(box[3] - box[1], 0.0)


def _box_overlap(box, other) -> float:

    if not box or not other:
        return 0.0
    inter = _box_area((max(box[0], other[0]), max(box[1], other[1]), min(box[2], other[2]), min(box[3], other[3])))
    union = _box_area(box) + _box_area(other) - inter
    return inter / union if union > 0 else 0.0


def _extra_words(label: str, wanted: set):

    words = _fold(label).split()
    best = None
    for name in wanted:
        asked = name.split()
        if not asked or len(asked) >= len(words):
            continue
        if any(words[i:i + len(asked)] == asked for i in range(len(words) - len(asked) + 1)):
            extra = len(words) - len(asked)
            best = extra if best is None else min(best, extra)
    return best





_AROUND_HALF_DEG = 0.0045
_AROUND_MIN_HALF_DEG = 0.0009


def _outline_around(hit: dict, name: str) -> dict:

    import math

    lon, lat = float(hit["lon"]), float(hit["lat"])
    squash = max(math.cos(math.radians(lat)), 0.05)
    bounds = hit.get("boundingbox")
    extent = None
    if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
        try:
            south, north, west, east = (float(value) for value in bounds)
        except (TypeError, ValueError):
            south = north = west = east = None
        if south is not None and south <= lat <= north and west <= lon <= east:
            extent = (west, south, east, north)
    if extent is None:
        half = _AROUND_HALF_DEG
        extent = (lon - half / squash, lat - half, lon + half / squash, lat + half)
        how = f"a box about {2 * half * 111:.0f} km a side around the geocoded point"
    else:
        how = "the geocoder's own extent of it"
    west, south, east, north = extent
    half_x = max((east - west) / 2, _AROUND_MIN_HALF_DEG / squash)
    half_y = max((north - south) / 2, _AROUND_MIN_HALF_DEG)
    middle_x, middle_y = (west + east) / 2, (south + north) / 2
    west, east, south, north = middle_x - half_x, middle_x + half_x, middle_y - half_y, middle_y + half_y
    ring = [(west, south), (east, south), (east, north), (west, north), (west, south)]
    label = str(hit.get("display_name") or name).split(",")[0].strip() or name
    kind = str(hit.get("type") or hit.get("osm_value") or "place")
    return {"polys": [(ring, [])], "label": label,
            "outline_source": f"box around the geocoded {kind} (no administrative outline)",
            "note": (f"{label!r} is a {kind}, which has no administrative outline: the clip is {how}, "
                     f"{(east - west) * 111 * squash:.1f} by {(north - south) * 111:.1f} km, never the town "
                     "around it. For another size, pass a bbox instead of clip_to and say so.")}


def _one_side_of_antimeridian(made: dict) -> None:






    polys = made.get("polys") or []
    weight = {True: 0, False: 0}
    for exterior, _holes in polys:
        east = sum(x for x, _y in exterior) >= 0
        weight[east] += len(exterior)
    east_side = weight[True] >= weight[False]
    kept = [part for part in polys if (sum(x for x, _y in part[0]) >= 0) == east_side]
    if kept and len(kept) < len(polys):
        made["polys"] = kept
        made["note"] = ((made.get("note") + " ") if made.get("note") else "") + (
            f"The outline crosses the 180th meridian: the clip kept its {len(kept)} parts at "
            f"{'positive' if east_side else 'negative'} longitudes") + (
            f" and left out {len(polys) - len(kept)}; load those with a bbox of their own if needed.")


def _resolve_outline(clip_to: str, layers: bool = True):








    made = _outline_of_layer(clip_to) if layers else None
    if made is None:
        made = _outline_of_place(clip_to)
    if not isinstance(made, dict) or made.get("_error"):
        return None, (made if isinstance(made, dict) else {"_error": f"Could not resolve {clip_to!r}."})
    bounds = _polys_bbox(made.get("polys") or [])
    if bounds and bounds[2] - bounds[0] > 180.0:
        _one_side_of_antimeridian(made)
        bounds = _polys_bbox(made.get("polys") or [])
    if not bounds:
        return None, {"_error": f"{clip_to!r} resolved to an empty outline.", "code": "INVALID_ARGS",
                      "suggestion": "Pass a bbox instead of clip_to."}
    made["bbox"] = bounds
    return made, None
