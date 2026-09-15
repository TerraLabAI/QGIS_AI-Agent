# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The place the user named: outline extraction from layers or geocoding, and resolution of the named area for clipping."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject

from ..core import net
from ..core.geometry_budget import VertexBudget
from ..core.logger import log_warning
from ..core.qt_compat import enum_member
from .data_common import _canvas_viewbox_4326, _fold, _is_number, _run_on_main_thread, _viewbox_bounds
from .data_geocoding import (
    _PLACE_AREA_LAYERS,
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


def _outline_of_layer(name: str):
    """The outline of a polygon layer already in the project, or None/an error."""





    def _read():


        from qgis.core import QgsGeometry, QgsWkbTypes

        from ._layers import resolve_layer

        layer = resolve_layer(name)
        if layer is None or not hasattr(layer, "getFeatures"):
            return None
        if name.strip().lower() not in (layer.name().strip().lower(), layer.id().lower()):



            return None
        polygon_type = enum_member(QgsWkbTypes, "GeometryType", "PolygonGeometry")
        if QgsWkbTypes.geometryType(layer.wkbType()) != polygon_type:
            return {"_error": f"The layer {layer.name()!r} draws no outline: it is not polygons.",
                    "code": "INVALID_ARGS",
                    "suggestion": "Pass the boundary layer, or the name of the place itself."}
        selected = layer.selectedFeatureCount()
        count = selected or layer.featureCount()
        if count > _CLIP_MAX_PARTS:
            return {"_error": f"{layer.name()!r} holds {count:,} polygons, which is a map and not one outline.",
                    "code": "INVALID_ARGS",
                    "suggestion": "Select the feature to clip to, or pass the place name instead."}
        target = QgsCoordinateReferenceSystem("EPSG:4326")
        xform = None
        if layer.crs().isValid() and layer.crs() != target:
            xform = QgsCoordinateTransform(layer.crs(), target, QgsProject.instance())
        polys: list = []
        budget = VertexBudget()
        for feature in (layer.getSelectedFeatures() if selected else layer.getFeatures()):
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
    """How much of a place a geocoder hit is: 3 a country or a state, 2 a county or a city, 1 any other settlement, 0 a part of one (a quarter, a."""

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
    """Photon's hits for *name* among countries, states, counties and cities, or []."""
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


def _place_hit(name: str, hits: list) -> dict:
    """The hit a clip_to name means, ranked the way a geocoding client ranks places."""









    first = hits[0]
    tier = _place_tier(first)
    viewbox = _run_on_main_thread(_canvas_viewbox_4326)
    if tier >= 2 or _in_near_view(first, viewbox):
        return first
    larger = next((hit for hit in hits[1:] if _place_tier(hit) >= 2), None)
    if larger is not None:
        return larger
    if tier == 0:
        asked = _fold(str(name).split(",")[0])
        for hit in _area_hits(name, viewbox):
            found = _place_tier(hit)
            if found == 3 or (found == 2 and _fold(str(hit.get("display_name") or "").split(",")[0]) == asked):
                return hit
    return first


def _outline_of_place(name: str):
    """The administrative outline of a named place, out of our own divisions."""






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


    span = 0.003
    box = (lon - span, lat - span, lon + span, lat + span)

    wanted = {_fold(str(name).split(",")[0]), _fold(str(hit.get("display_name") or "").split(",")[0])} - {""}






    kind = _HIT_SUBTYPES.get(str(hit.get("type") or "").lower(), "")
    code = str(hit.get("country_code") or "").upper()
    order = ((kind,) + tuple(s for s in _CLIP_SUBTYPE_ORDER if s != kind)) if kind else _CLIP_SUBTYPE_ORDER
    fallback = None
    for subtype in order:
        payload = _overture_call("divisions", box, subtype=subtype)
        if payload.get("code") == net.NETWORK_ERROR:


            return payload
        if payload.get("_error"):
            continue
        for feature in payload.get("features") or []:
            polys = _rings_of(feature.get("geometry"))
            bounds = _polys_bbox(polys)
            if not bounds:
                continue
            if not _outline_contains(lon, lat, {"polys": polys, "bbox": bounds}):
                continue
            label = str((feature.get("properties") or {}).get("name") or name)
            props = feature.get("properties") or {}
            outline = {"polys": polys, "label": label,
                       "outline_source": f"Overture divisions, subtype {subtype}",
                       "division": {"subtype": subtype, "country": props.get("country"),
                                    "region": props.get("region")}}
            if _fold(label) in wanted or (subtype == kind and (
                    subtype != "country" or not code or str(props.get("country") or "").upper() == code)):
                return outline


            if subtype not in ("neighborhood", "macrohood"):
                fallback = fallback or outline
    if fallback:




        asked = str(name).split(",")[0].strip()
        fallback["note"] = (
            f"No division is named {asked!r} at any level from neighbourhood up, so the clip used "
            f"the smallest division covering it, {fallback['label']}. For the place itself, pass a "
            f"bbox about 2 km around the geocoded point instead of clip_to and say so. Do not ask "
            f"the user.")
        return fallback
    return {"_error": f"{name!r} was found, but no administrative outline covers it.",
            "code": "INVALID_ARGS",
            "suggestion": ("Load the boundary first (theme divisions) and pass its layer name as clip_to, "
                           "or pass a bbox.")}


def _resolve_outline(clip_to: str, layers: bool = True):
    """``(outline, None)`` for a name, or ``(None, error)``."""







    made = _outline_of_layer(clip_to) if layers else None
    if made is None:
        made = _outline_of_place(clip_to)
    if not isinstance(made, dict) or made.get("_error"):
        return None, (made if isinstance(made, dict) else {"_error": f"Could not resolve {clip_to!r}."})
    bounds = _polys_bbox(made.get("polys") or [])
    if not bounds:
        return None, {"_error": f"{clip_to!r} resolved to an empty outline.", "code": "INVALID_ARGS",
                      "suggestion": "Pass a bbox instead of clip_to."}
    made["bbox"] = bounds
    return made, None
