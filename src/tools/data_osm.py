# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Overpass fetch: endpoints and mirrors, the area guard, building footprints, and the streaming load from our own instance."""

from __future__ import annotations

import contextlib
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from qgis.core import QgsProject, QgsVectorLayer

from ..core import catalog, limits, net, tuning
from ..core.policy import create_managed_temp_dir
from . import volume_guard
from .data_common import (
    _MAX_DOWNLOAD_SIZE,
    _OSM_RECENT,
    _OVERPASS_CONNECT_TIMEOUT,
    _TOTAL_TIMEOUT_FACTOR,
    _USER_AGENT,
    _bbox_km2,
    _footprint_box,
    _json_object,
    _layer_from_source,
    _osm_recent,
    _osm_remember,
    _overpass_timeout,
    _run_on_main_thread,
    _service,
    _vector_source_from_features,
)
from .data_inspect import _human_bytes, _safe_extract_stem
from .data_osm_geometry import (
    _LIFTED_FAMILIES,
    _OSM_GEOMETRY_TYPES,
    _OSM_STREAM_CONVERT_SHARE,
    _OSM_STREAM_DISK_SHARE,
    _OSM_STREAM_DOWNLOAD_SHARE,
    _OSM_STREAM_MIN_BYTES,
    _OSM_STREAM_QUERY_SHARE,
    _OSM_STREAM_SILENCE_S,
    _OVERPASS_TIMEOUT_MARKERS,
    _beyond_bbox,
    _geometry_counts,
    _LoadStopped,
    _osm_stream_convert,
    _osm_to_geojson,
    _overpass_timeout_remark,
    _OverpassFile,
    _reach_past,
    _with_drawable_out,
    _with_overpass_settings,
)
from .data_overture import (
    _OSM_ATTRIBUTION,
    _OVERTURE_BUILDING_ATTRIBUTION,
    _OVERTURE_LICENCES,
    _overture_call,
    _overture_layer,
)
from .data_overture_extract import _lifted_deadline, _overture_clip


def _osm_from_hosted(plan: list, box, args: dict, km2: float) -> dict:
    """A fetch_osm_data call served from TerraLab's tiles, at any size."""








    base_name = str(args.get("layer_name") or "").strip()
    layers, errors, failures = [], [], []



    deadline = _lifted_deadline() if volume_guard.lifted(args) else None
    for theme, wanted in plan:
        name = base_name if base_name and len(plan) == 1 else f"{base_name} {theme}".strip()
        per_theme = dict(args)
        if name:
            per_theme["layer_name"] = name
        per_theme.pop("filter", None)
        if wanted:
            per_theme["filter"] = [dict(one) for one in wanted] if isinstance(wanted, list) else dict(wanted)



        per_theme["confirm_large"] = True
        out = _overture_clip(theme, box, per_theme, deadline)
        if out.get("_error"):
            errors.append(f"{theme}: {out['_error']}")
            failures.append(out)
            continue
        layers.append(out)
    if not layers:
        themes = ", ".join(theme for theme, _ in plan)



        first = failures[0] if failures else {}
        return {"_error": f"{km2:,.0f} km² of {themes} could not be served from TerraLab's tiles: "
                + "; ".join(errors),
                "area_km2": round(km2, 1), "code": first.get("code") or "EXECUTION_FAILED",
                "suggestion": first.get("suggestion") or ""}
    served = [layer["theme"] for layer in layers]
    made = {"served_by": "TerraLab hosted tiles", "area_km2": round(km2, 1),
            "themes": served, "layers": layers,
            "feature_count": sum(int(layer.get("feature_count") or 0) for layer in layers),
            "skipped_themes": errors, "crs": "EPSG:4326",
            "licence": ", ".join(sorted({layer.get("licence", "") for layer in layers if layer.get("licence")})),
            "_note": (f"Served from TerraLab's hosted tiles: the query's tags map to {', '.join(served)}, "
                      "filtered on the same tag values and clipped to the box by our open data API. "
                      "Overpass was not asked.")}
    filters = {theme: wanted for theme, wanted in plan if wanted}
    if filters:
        made["filters"] = filters
    return made


def _osm_public_mirrors(used: list) -> list:
    """The public mirrors this call has not asked yet, in the served order."""




    asked = {str(url).strip() for url in used}
    host = volume_guard.own_overpass_host().lower()
    return [url for url in catalog.overpass_mirrors()
            if str(url).strip() not in asked and (not host or host not in str(url).lower())]






_OVERPASS_UNREACHABLE = ("502", "503", "504", "timed out", "timeout", "refused",
                         "name or service not known", "nodename nor servname",
                         "temporary failure in name resolution", "unreachable",
                         "connection reset", "no route to host", "bad gateway")




_OVERPASS_INSTANCE_FAULTS = ("invalid response from overpass api", "open64", "file_blocks_index", "dispatcher")


def _own_overpass_unreachable(errors) -> bool:
    """True when our own instance is the one that never answered."""










    host = volume_guard.own_overpass_host().lower()
    counted = []
    for item in errors or []:
        text = str(item).lower()
        if not host or host not in text:
            continue
        if any(marker in text for marker in _OVERPASS_INSTANCE_FAULTS):
            counted.append(True)
            continue
        if any(marker in text for marker in _OVERPASS_TIMEOUT_MARKERS):
            continue
        counted.append(any(word in text for word in _OVERPASS_UNREACHABLE))
    return bool(counted) and all(counted)


def _osm_endpoints(km2: float) -> list:
    """The mirrors one query may be sent to: our own instance alone."""






    if volume_guard.own_overpass_down():
        return []
    host = volume_guard.own_overpass_host().lower()
    return [url for url in catalog.overpass_mirrors() if host and host in str(url).lower()]


def _osm_fallback_warning(endpoint_used, own_failed: bool) -> str:
    """The warning a public mirror's answer carries, naming why our own instance did not give it."""





    host = urllib.parse.urlsplit(str(endpoint_used)).hostname or str(endpoint_used)
    if own_failed:
        why = "TerraLab's own Overpass instance is not answering"
    elif volume_guard.own_overpass_down():
        why = "TerraLab's own Overpass instance did not answer a call in the last few minutes"
    else:
        why = "this session's Overpass list does not name TerraLab's own instance"
    return (f"{why}, so this came from the public mirror {host}, a volunteer service that is slower and may "
            "refuse large boxes. Tell the user the answer came from a public fallback.")


def _osm_area_refusal(km2: float, box=None, args: dict | None = None) -> dict | None:
    """The refusal of a fetch_osm_data request over *km2*, or None when it may be sent."""







    if volume_guard.lifted(args or {}):


        return None
    themes, hosted_cap = volume_guard.hosted_ceiling("fetch_osm_data", args or {})
    ceiling = hosted_cap if themes else limits.current("MAX_FETCH_KM2")
    if not themes:





        ceiling = max(ceiling, volume_guard.selective_max_km2("fetch_osm_data", args or {}))
    if km2 < ceiling:
        return None




    fit = volume_guard.fitting_zone(dict(zip(("south", "west", "north", "east"), box)), ceiling) if box else {}
    if themes:
        what = (f"{km2:,.0f} square kilometres is over the {ceiling:,.0f} TerraLab's tiles clip "
                f"{', '.join(themes)} to in one fetch_osm_data call; the request was not sent."
                + volume_guard.LIFT_HINT)
    else:
        what = (f"{km2:,.0f} square kilometres is over the {ceiling:,.0f} one fetch_osm_data "
                "call may cover; the request was not sent.")
    out = {"_error": what, "area_km2": round(km2, 1), "code": limits.CEILING_CODE}
    if fit:
        out.update(fit)
        out["suggestion"] = volume_guard.fitting_sentence(fit).strip()
    if themes:
        out["suggestion"] = (out.get("suggestion", "") + volume_guard.HOSTED_INSTEAD).strip()
    return out












_FOOTPRINTS_URL = "https://terra-lab.ai/api/building-footprints"




_FOOTPRINT_SOURCES = ("microsoft", "google", "openstreetmap", "overture")
_FOOTPRINT_SOURCE_INPUTS = (*_FOOTPRINT_SOURCES, "osm")
_FOOTPRINT_DEFAULT_SOURCES = ("microsoft", "openstreetmap")
_FOOTPRINT_SERVICE_SOURCES = ("microsoft", "google", "openstreetmap")
_FOOTPRINT_LABELS = {"microsoft": "Microsoft Buildings",
                     "google": "Google Open Buildings",
                     "openstreetmap": "OSM Buildings",
                     "overture": "Overture Buildings"}





_FOOTPRINT_MAX_KM2 = volume_guard.FOOTPRINTS_MAX_KM2
_FOOTPRINT_MAX_SPAN_DEG = 0.5


_FOOTPRINT_TIMEOUT = 75


def _fetch_building_footprints(args: dict) -> dict:
    box, problem = _footprint_box(args.get("bbox") or {})
    if box is None:
        return {"_error": problem}
    west, south, east, north = box




    asked = args.get("sources") or list(_FOOTPRINT_DEFAULT_SOURCES)
    if isinstance(asked, str):
        asked = [part.strip() for part in asked.split(",")]
    asked = ["openstreetmap" if name == "osm" else name for name in asked]
    known = tuning.service_list("footprint_sources", _FOOTPRINT_SOURCES)
    sources = [name for name in known if name in asked]
    if not sources:
        return {"_error": f"sources must name at least one of: {', '.join(known)}"}



    span = max(east - west, north - south)
    if span > _FOOTPRINT_MAX_SPAN_DEG:
        return {"_error": f"Each side of the box must stay under {_FOOTPRINT_MAX_SPAN_DEG} degrees.",
                "suggestion": "Ask for a smaller extent, or one arrondissement or district at a time."}
    area_km2 = _bbox_km2(south, west, north, east)
    cap_km2 = volume_guard.footprints_max_km2()
    if area_km2 > cap_km2:
        return {"_error": f"This box is {area_km2:.0f} km2 and the footprint service takes at most "
                f"{cap_km2:.0f} km2.",
                "area_km2": round(area_km2, 1),
                "suggestion": "Zoom in, or fetch the area in several smaller boxes."}

    added, empty = [], []




    planned: list = []



    if "overture" in sources:
        overture = _overture_call("buildings", (west, south, east, north))
        if overture.get("_error"):
            empty.append({"source": "overture", "status": "unavailable",
                          "message": overture["_error"]})
        else:
            features = overture.get("features") or []
            if features:
                label = _FOOTPRINT_LABELS["overture"]
                name = (f"{args['layer_name']} {label}" if args.get("layer_name") and len(sources) > 1
                        else args.get("layer_name") or label)
                planned.append(("overture", name, features,
                                overture.get("licence") or _OVERTURE_LICENCES["buildings"],
                                overture.get("attribution") or _OVERTURE_BUILDING_ATTRIBUTION))
            else:
                empty.append({"source": "overture", "status": "empty"})
    service_sources = [name for name in sources if name != "overture"]

    def _ask(names: list) -> tuple:
        """(payload, refusal) for one call; exactly one of the two is None."""
        query = urllib.parse.urlencode({"bbox": f"{west},{south},{east},{north}",
                                        "sources": ",".join(names)})
        request = urllib.request.Request(f"{_service('footprints', _FOOTPRINTS_URL)}?{query}",
                                         headers={"User-Agent": _USER_AGENT})



        budget = tuning.limit("net", "footprints_timeout_s", _FOOTPRINT_TIMEOUT)
        try:
            answer = net.fetch(request, timeout=budget, max_bytes=_MAX_DOWNLOAD_SIZE,
                               total_timeout=budget * _TOTAL_TIMEOUT_FACTOR,
                               cache_ttl=600.0)
        except urllib.error.HTTPError as error:



            detail = ""
            try:
                detail = json.loads(error.read().decode("utf-8", "replace")).get("error", "")
            except (ValueError, OSError, AttributeError):
                pass
            return None, {"_error": detail or f"The footprint service answered {error.code}.",
                          "suggestion": "Draw a smaller box." if error.code in (400, 413)
                          else "Try again shortly."}
        except (net.FetchDeadline, net.FetchTruncated, net.NetworkUnreachable, OSError) as error:
            return None, {"_error": f"Could not reach the footprint service: {error}",
                          "suggestion": "Use fetch_osm_data with way[building] for the same box instead."}
        try:
            return json.loads(answer.body), None
        except (ValueError, UnicodeDecodeError):
            return None, {"_error": "The footprint service did not answer with JSON."}

    payload: dict = {}
    if service_sources:
        payload, refusal = _ask(service_sources)
        if refusal is not None:
            if not planned:
                return refusal

            empty.append({"source": ",".join(service_sources), "status": "unavailable",
                          "message": refusal["_error"]})
            payload = {}








    if len(service_sources) > 1:
        beaten = [entry.get("sourceId", "") for entry in payload.get("sources", [])
                  if entry.get("status") == "failed" and entry.get("sourceId")]
        for name in beaten:
            alone, _ = _ask([name])
            recovered = next((entry for entry in (alone or {}).get("sources", [])
                              if entry.get("sourceId") == name
                              and (entry.get("featureCollection") or {}).get("features")), None)
            if recovered is None:
                continue
            payload["sources"] = [recovered if entry.get("sourceId") == name else entry
                                  for entry in payload["sources"]]

    for entry in payload.get("sources", []):
        source_id = entry.get("sourceId", "")
        collection = entry.get("featureCollection")
        features = (collection or {}).get("features") or []
        label = _FOOTPRINT_LABELS.get(source_id, source_id or "Buildings")
        name = args.get("layer_name") or label
        if len(sources) > 1:
            name = f"{args['layer_name']} {label}" if args.get("layer_name") else label
        if not features:






            said = {"source": source_id, "status": entry.get("status", "empty")}
            files = entry.get("files") or []
            if files:
                said["files"] = [{"url": f.get("url", ""), "size_mb": round((f.get("sizeBytes") or 0) / 1e6, 1)}
                                 for f in files[:3]]
            empty.append(said)
            continue
        planned.append((source_id, name, features, entry.get("licence", ""), entry.get("attribution", "")))

    for source_id, name, features, _licence, _attribution in planned:
        what = f"The Overture {name} answer" if source_id == "overture" else f"The {name} answer"
        refused = volume_guard.too_many(len(features), args, what)
        if refused:
            return refused

    for source_id, name, features, licence, attribution in planned:
        if source_id == "overture":
            made = _overture_layer(features, name, args)
        else:

            uri = _vector_source_from_features(features, name, "footprints")

            def _create(uri=uri, name=name):
                layer = _layer_from_source(uri, name)
                if not layer.isValid():
                    return {"_error": f"Could not build a layer from the {name} footprints."}
                QgsProject.instance().addMapLayer(layer)
                return {"layer_name": layer.name(), "feature_count": layer.featureCount()}

            made = _run_on_main_thread(_create, timeout=45)
        if made.get("_error"):
            if not added:
                return made

            empty.append({"source": source_id, "status": "failed", "message": made["_error"]})
            continue
        made.update({"source": source_id, "licence": licence, "attribution": attribution})
        added.append(made)

    if not service_sources:
        if not added:
            return {"features": 0,
                    "message": "Overture published no buildings for this box",
                    "empty_sources": empty,
                    "suggestion": "Add microsoft and openstreetmap to sources for the same box."}
        return {"layers": added, "empty_sources": empty, "crs": "EPSG:4326",
                "area_km2": round(area_km2, 1)}
    if not added:
        return {"features": 0,
                "message": "No building footprints published for this box by " + ", ".join(sources),
                "empty_sources": empty,
                "suggestion": 'OpenStreetMap covers most cities; try sources=["openstreetmap"].'}
    return {"layers": added, "empty_sources": empty, "crs": "EPSG:4326",
            "area_km2": round(area_km2, 1), "extracted_on": payload.get("extractedOn", "")}





_OVERPASS_REJECTED = ("400", "414")


_OVERPASS_HTTP_PREFIX = re.compile(r"^HTTP Error \d{3}:\s*")


def _overpass_reason(exc) -> str:
    """The Error lines out of an Overpass 400 page, or the plain HTTP reason."""





    try:
        body = exc.read(4096).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - a body we cannot read is not worth an error of its own
        body = ""



    with contextlib.suppress(Exception):
        exc.close()



    text = re.sub(r"(?i)</p>|<br\s*/?>", "\n", body)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    seen, kept = set(), []
    for line in text.splitlines():
        line = " ".join(line.split()).strip(" .")
        if not line or not line.lower().startswith("error"):
            continue

        line = line[len("error"):].lstrip(" :")
        if not line or line.lower() in seen:
            continue
        seen.add(line.lower())
        kept.append(line)
        if len(kept) == 3:
            break
    return "; ".join(kept) if kept else f"HTTP {exc.code}"


def _overpass_rejection(errors) -> str:
    """The reason, when every mirror refused the query rather than failing to answer."""





    messages = [_OVERPASS_HTTP_PREFIX.sub("", str(item).split(": ", 1)[-1].strip())
                for item in (errors or [])]
    if not messages:
        return ""
    if not all(any(code in text for code in _OVERPASS_REJECTED) or "parse error" in text.lower()
               for text in messages):
        return ""
    return messages[0]


def _overpass_why(errors) -> str:
    """A short ", because ..." naming the first mirror failure, or nothing."""
    for item in errors or []:
        text = str(item).strip()
        if text:
            return f" ({text[:160]})"
    return ""


def _fetch_osm_data_preflight(args: dict) -> dict:
    """What fetch_osm_data refuses before it sends anything: the executor asks it before a size card."""
    return _fetch_osm_data(args, check_only=True)


def _fetch_osm_data(args: dict, check_only: bool = False) -> dict:

    query = args["query"]



    clamped = volume_guard.clamp_to_cap("fetch_osm_data", args)
    bbox = args["bbox"]
    layer_name = args.get("layer_name", "OSM Data")



    if "xmin" in bbox:
        south = bbox.get("ymin", bbox.get("south"))
        west = bbox.get("xmin", bbox.get("west"))
        north = bbox.get("ymax", bbox.get("north"))
        east = bbox.get("xmax", bbox.get("east"))
    else:
        south = bbox.get("south")
        west = bbox.get("west")
        north = bbox.get("north")
        east = bbox.get("east")
    if None in (south, west, north, east):
        return {"_error": f"bbox must provide either {{south, west, north, east}} or "
                f"{{xmin, ymin, xmax, ymax}}; got keys {sorted(bbox)}.",
                "code": "INVALID_ARGS",
                "suggestion": "Pass the four numbers in EPSG:4326 degrees under one of those two key sets."}
    try:
        inverted = float(east) <= float(west) or float(north) <= float(south)
    except (TypeError, ValueError):
        inverted = False
    if inverted:


        return {"_error": f"bbox is inverted: west {west}, east {east}, south {south}, north {north}.",
                "code": "INVALID_ARGS",
                "suggestion": "Pass west < east and south < north, in EPSG:4326 degrees."}

    try:
        area_km2 = _bbox_km2(south, west, north, east)
    except (TypeError, ValueError):
        area_km2 = 0.0
    plan = volume_guard.hosted_plan(query) if volume_guard.hosted_fallback("fetch_osm_data", args) else []
    if plan:
        if check_only:

            return {}
        served = _osm_from_hosted(plan, (west, south, east, north), args, area_km2)
        if clamped and not served.get("_error"):
            served["asked_km2"] = clamped["asked_km2"]
            served["bbox"] = clamped["bbox"]
            served["_note"] = clamped["note"] + " " + str(served.get("_note") or "")
        return served
    refused = _osm_area_refusal(area_km2, (south, west, north, east), args)
    if refused or check_only:
        return refused or {}

    bbox_str = f"{south},{west},{north},{east}"
    final_query = query.replace("{{bbox}}", bbox_str)

    final_query = _with_drawable_out(final_query)
    if volume_guard.lifted(args):


        return _osm_stream_load(query, final_query, args, area_km2, layer_name, (west, south, east, north))
    final_query = _with_overpass_settings(final_query)

    post_data = urllib.parse.urlencode({"data": final_query}).encode("utf-8")

    budget = _overpass_timeout()
    parsed_answers = {}



    timeout_answers = {}

    def decode_answer(raw):
        try:
            answer = _json_object(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw, "elements")
            if not isinstance(answer, dict) or not isinstance(answer.get("elements"), list):
                raise ValueError("missing elements array")
            if any(not isinstance(el, dict) or "type" not in el for el in answer["elements"]):
                raise ValueError("invalid element")
            return answer
        except (ValueError, TypeError) as exc:

            raise ValueError("Invalid response from Overpass API") from exc

    def ask(url: str):
        req = urllib.request.Request(url, data=post_data, headers={"User-Agent": _USER_AGENT})
        try:
            raw = net.fetch(req, timeout=budget, max_bytes=_MAX_DOWNLOAD_SIZE,
                            total_timeout=budget * _TOTAL_TIMEOUT_FACTOR,
                            connect_timeout=_OVERPASS_CONNECT_TIMEOUT).body


            answer = decode_answer(raw)
            remark = _overpass_timeout_remark(answer)
            if remark:
                if any(marker in remark.lower() for marker in _OVERPASS_INSTANCE_FAULTS):


                    raise ValueError(f"Invalid response from Overpass API: {remark[:120]}")




                timeout_answers[url] = (answer, remark)
                raise ValueError(f"Overpass query timed out: {remark}")
            parsed_answers[url] = answer
            return raw
        except urllib.error.HTTPError as exc:




            if exc.code not in (400, 414):
                raise
            raise urllib.error.HTTPError(
                exc.url, exc.code, _overpass_reason(exc), exc.headers, None) from exc





    cached = _osm_recent(final_query)
    errors: list = []
    fallback_note = ""
    fell_back = False
    if cached is not None:
        endpoint_used, raw = cached


        own_host = volume_guard.own_overpass_host().lower()
        if not own_host or own_host not in str(endpoint_used or "").lower():
            fallback_note = _osm_fallback_warning(endpoint_used, False)
    else:
        endpoints = _osm_endpoints(area_km2)
        endpoint_used, raw = None, None
        if endpoints:
            endpoint_used, raw, errors = net.race([(url, lambda u=url: ask(u)) for url in endpoints])
        own_failed = bool(endpoints) and raw is None and _own_overpass_unreachable(errors)
        fell_back = own_failed or not endpoints
        public_cap = (volume_guard.dense_max_km2() if volume_guard.is_dense("fetch_osm_data", args)
                      else limits.current("MAX_FETCH_KM2"))
        if not endpoints and area_km2 > public_cap:



            return {"_error": (f"{area_km2:,.0f} square kilometres needs TerraLab's own Overpass instance, "
                               "which is not answering or not served to this session."),
                    "area_km2": round(area_km2, 1), "code": limits.CEILING_CODE,
                    "suggestion": (f"Ask for at most {public_cap:,.0f} km\u00b2, or call "
                                   "fetch_overture with the matching theme, which is served from our tiles.")}
        if raw is None and fell_back:






            if own_failed:
                volume_guard.note_own_overpass_down()
            fallen: list = []
            for url in _osm_public_mirrors(endpoints):
                try:
                    raw, endpoint_used = ask(url), url
                    break
                except Exception as exc:  # noqa: BLE001 - reported, not raised
                    fallen.append(f"{url}: {exc}")



            errors = fallen if (fallen and _overpass_rejection(fallen)) else errors + fallen
            if raw is not None:
                fallback_note = _osm_fallback_warning(endpoint_used, own_failed)

    truncated_remark = ""
    if raw is None:
        down_prefix = ("TerraLab's Overpass instance is down and the public mirrors failed too. "
                       if fell_back else "")
        if timeout_answers:





            endpoint_used, (osm_data, truncated_remark) = max(
                timeout_answers.items(), key=lambda kv: len(kv[1][0].get("elements") or []))
            own_host = volume_guard.own_overpass_host().lower()
            if fell_back and not fallback_note and (not own_host or own_host not in str(endpoint_used).lower()):

                fallback_note = _osm_fallback_warning(endpoint_used, own_failed)
            elements = osm_data["elements"]
            if not elements:

                return {"_error": f"Overpass timed out before answering: {truncated_remark}",
                        "code": "TIMEOUT",
                        "query": final_query}


        elif any("Invalid response from Overpass API" in str(error) for error in errors):
            return {"_error": f"{down_prefix}Invalid response from Overpass API; no mirror returned usable data."}
        else:





            rejected = _overpass_rejection(errors)
            if rejected:


                return {"_error": f"Overpass rejected the query: {rejected}",
                        "query": final_query,
                        "suggestion": "The servers are up; fix the query it names, or narrow the box, "
                                      "and call again."}



            turbo_query = urllib.parse.quote(final_query, safe="")
            turbo_url = f"https://overpass-turbo.eu/?Q={turbo_query}&R"
            why = _overpass_why(errors)
            return {"_error": f"{down_prefix}Overpass API timed out{why}. Fallback overpass_turbo: {turbo_url}"}
    else:
        try:
            osm_data = parsed_answers.get(endpoint_used)
            if osm_data is None:
                osm_data = decode_answer(raw)
        except ValueError:
            _OSM_RECENT.pop(final_query, None)
            return {"_error": "Invalid response from Overpass API"}
        if cached is None:
            _osm_remember(final_query, endpoint_used, raw)

        elements = osm_data["elements"]
        if not elements:
            if fallback_note:
                return {"features": 0, "empty": True,
                        "served_by": "public Overpass mirror (fallback)", "warning": fallback_note}
            return {"features": 0, "empty": True}

    geojson = _osm_to_geojson(elements, query)
    if not geojson["features"]:




        return {
            "_error": f"Overpass returned {len(elements)} element(s) and none of them could be drawn, "
            "so no layer was added.",
            "query": final_query,
        }
    refused = volume_guard.too_many(len(geojson["features"]), args, "The OpenStreetMap answer")
    if refused:
        refused["area_km2"] = round(area_km2, 1)
        return refused

    uri = _vector_source_from_features(geojson["features"], layer_name, "osm")

    def _create():
        layer = _layer_from_source(uri, layer_name)
        if not layer.isValid():
            return {"_error": "Failed to create layer from OSM data"}
        QgsProject.instance().addMapLayer(layer)
        return {
            "layer_name": layer.name(),
            "layer_id": layer.id(),
            "feature_count": layer.featureCount(),
            "geometry_type": (
                layer.geometryType().name
                if hasattr(layer.geometryType(), "name")
                else str(layer.geometryType())
            ),
            "crs": "EPSG:4326",
            "source_endpoint": endpoint_used,


            "licence": "ODbL 1.0",
            "attribution": _OSM_ATTRIBUTION,
        }

    mix = _geometry_counts(geojson["features"])

    out = _run_on_main_thread(_create, timeout=30)
    if not out.get("_error"):
        own_host = volume_guard.own_overpass_host()
        if fallback_note:
            out["served_by"] = "public Overpass mirror (fallback)"
        elif own_host and own_host in str(endpoint_used or "").lower():
            out["served_by"] = "TerraLab Overpass"



        if len(mix) > 1:
            out["geometry_mix"] = mix
        out["area_km2"] = round(area_km2, 1)
        if truncated_remark:
            out["truncated"] = True
            out["warning"] = (f"Overpass gave up partway through this query and answered with what it had: "
                              f"{truncated_remark}. Some features are likely missing; ask for a smaller "
                              "bbox for a complete answer.")
        if fallback_note:
            out["warning"] = (out.get("warning", "") + " " + fallback_note).strip()
        reach = _beyond_bbox(geojson["features"], (west, south, east, north))
        if reach:
            out["beyond_bbox"] = reach
        if clamped:
            out["asked_km2"] = clamped["asked_km2"]
            out["bbox"] = clamped["bbox"]
            out["_note"] = clamped["note"]
    return out


def _osm_stream_load(query: str, final_query: str, args: dict, area_km2: float, layer_name: str, box) -> dict:
    """``_fetch_osm_data`` for a call the server verified as the user's request for all of it."""
    import shutil
    import time

    started = time.monotonic()
    clock = float(limits.current("CALL_MAX_SECONDS_BACKGROUND"))
    query_timeout = max(_overpass_timeout(), int(clock * _OSM_STREAM_QUERY_SHARE))


    final_query = re.sub(r"(?<![A-Za-z_])(out\b[^;]*?)\s+qt\s*;", r"\1;", final_query)
    final_query = _with_overpass_settings(final_query, timeout=query_timeout)
    cancelled = net.current_cancel_check()
    directory = create_managed_temp_dir("osm-full")
    kept = False

    def check() -> None:
        if cancelled is not None and cancelled():
            raise _LoadStopped("cancelled")

    try:
        free = net.free_disk_bytes(directory)
        cap = int(limits.current("MAX_STREAM_BYTES"))
        if free is not None:
            cap = min(cap, max(0, free - net.STREAM_KEEP_FREE_BYTES) // _OSM_STREAM_DISK_SHARE)
        if cap < _OSM_STREAM_MIN_BYTES:
            return {"_error": (f"The disk has {(free or 0) / 1024 ** 3:.1f} GB free, too little to write a load of "
                               "this size and convert it."),
                    "code": limits.CEILING_CODE,
                    "suggestion": "Say so: the user can free some disk space, or ask for a smaller area."}
        fetched = _osm_stream_fetch(final_query, directory, area_km2, args,
                                    started + clock * _OSM_STREAM_DOWNLOAD_SHARE, query_timeout, cap)
        if fetched.get("_error") or fetched.get("empty"):
            return fetched
        answer_path, endpoint_used = fetched["path"], fetched["endpoint"]
        stem = _safe_extract_stem(layer_name)
        gpkg = os.path.join(directory, f"{stem}.gpkg")
        converted = _osm_stream_convert(
            _OverpassFile(answer_path), query, final_query, gpkg, stem, check,
            lambda: time.monotonic() > started + clock * _OSM_STREAM_CONVERT_SHARE)
        download_bytes = os.path.getsize(answer_path)
        for name in os.listdir(directory):
            if name.endswith(".json"):
                os.remove(os.path.join(directory, name))
        if converted.get("_error"):
            return converted
        warning = fetched.get("warning", "")
        if not converted["elements"]:
            if fetched.get("remark"):


                return {"_error": f"Overpass timed out before answering: {fetched['remark']}", "code": "TIMEOUT",
                        "query": final_query}
            empty = {"features": 0, "empty": True}
            if warning:
                empty.update({"served_by": "public Overpass mirror (fallback)", "warning": warning})
            return empty
        counts, tables = converted["counts"], converted["tables"]
        total = sum(counts.values())
        if not total:
            return {"_error": (f"Overpass returned {converted['elements']} element(s) and none of them could be drawn, "
                               "so no layer was added."), "query": final_query}
        several = len(tables) > 1

        def _create():
            added = []
            for family in _LIFTED_FAMILIES:
                if family not in tables:
                    continue
                shown = f"{layer_name} {family}" if several else layer_name
                layer = QgsVectorLayer(f"{gpkg}|layername={tables[family]}", shown, "ogr")
                if not layer.isValid():
                    continue
                QgsProject.instance().addMapLayer(layer)
                added.append({"layer_name": layer.name(), "layer_id": layer.id(), "feature_count": counts[family],
                              "geometry_type": _OSM_GEOMETRY_TYPES[family], "licence": "ODbL 1.0",
                              "attribution": _OSM_ATTRIBUTION})
            return added

        check()
        added = _run_on_main_thread(_create, timeout=30)
        if not added:
            return {"_error": f"QGIS could not read the GeoPackage written for {layer_name}.",
                    "code": "EXECUTION_FAILED"}
        kept = True
        size = os.path.getsize(gpkg)
        wall = time.monotonic() - started
        out = {"crs": "EPSG:4326", "source_endpoint": endpoint_used, "licence": "ODbL 1.0",
               "attribution": _OSM_ATTRIBUTION, "lifted": True, "feature_count": total, "size_bytes": size,
               "size": _human_bytes(size), "download_bytes": download_bytes, "wall_s": round(wall, 1),
               "path": gpkg, "area_km2": round(area_km2, 1)}
        if several:
            out["layers"] = added
            out["geometry_mix"] = {_OSM_GEOMETRY_TYPES[family]: count for family, count in counts.items() if count}
        else:
            out.update(added[0])
        own_host = volume_guard.own_overpass_host()
        if warning:
            out["served_by"] = "public Overpass mirror (fallback)"
        elif own_host and own_host in str(endpoint_used or "").lower():
            out["served_by"] = "TerraLab Overpass"
        remark = fetched.get("remark")
        out["_note"] = (f"{'Loaded in part' if remark or converted['stopped'] else 'Loaded in full'}, as the user "
                        f"asked for all of it: {total:,} features, {_human_bytes(size)} on disk in a GeoPackage, "
                        f"streamed from Overpass ({_human_bytes(download_bytes)} of answer) in {wall:.0f} s. "
                        "Tell the user the size.")
        if remark or converted["stopped"]:
            out["truncated"] = True
            out["warning"] = (f"Overpass gave up partway through this query and answered with what it had: {remark}. "
                              "Some features are likely missing." if remark else
                              "Writing the layer stopped when this call's time ran out, so part of the answer is "
                              "missing: say so, and offer the rest in a smaller area.")
        if warning:
            out["warning"] = (out.get("warning", "") + " " + warning).strip()
        reach = _reach_past(converted["extent"], box)
        if reach:
            out["beyond_bbox"] = reach
        return out
    except (_LoadStopped, net.FetchCancelled):
        return {"_error": "The load was stopped before it finished.", "code": "CANCELLED"}
    except ValueError:

        return {"_error": "Invalid response from Overpass API"}
    finally:
        if not kept:
            shutil.rmtree(directory, ignore_errors=True)


def _osm_stream_fetch(final_query: str, directory: str, area_km2: float, args: dict, deadline: float,
                      query_timeout: int, cap: int) -> dict:
    """The Overpass answer written to ``directory``, asked the way ``_fetch_osm_data`` asks."""





    import time

    post_data = urllib.parse.urlencode({"data": final_query}).encode("utf-8")
    partial: dict = {}
    asked = [0]

    def ask(url: str) -> str:
        asked[0] += 1
        path = os.path.join(directory, f"overpass-{asked[0]}.json")
        left = deadline - time.monotonic()
        if left <= 1:
            raise net.FetchDeadline("No time was left in this call to download the answer.")
        request = urllib.request.Request(url, data=post_data, headers={"User-Agent": _USER_AGENT})
        try:
            net.fetch_to_file(request, path, timeout=query_timeout + _OSM_STREAM_SILENCE_S, max_bytes=cap,
                              total_timeout=left, connect_timeout=_OVERPASS_CONNECT_TIMEOUT)
        except urllib.error.HTTPError as exc:
            if exc.code not in (400, 414):
                raise
            raise urllib.error.HTTPError(exc.url, exc.code, _overpass_reason(exc), exc.headers, None) from exc
        try:
            remark = _OverpassFile(path).remark()
        except ValueError:
            os.remove(path)
            raise
        if remark:
            if any(marker in remark.lower() for marker in _OVERPASS_INSTANCE_FAULTS):
                os.remove(path)
                raise ValueError(f"Invalid response from Overpass API: {remark[:120]}")
            partial[url] = (path, remark)
            raise ValueError(f"Overpass query timed out: {remark}")
        return path


    links: list = []

    def sweep(urls) -> tuple:
        failed = []
        for url in urls:
            try:
                return url, ask(url), failed
            except net.FetchCancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                failed.append(f"{url}: {exc}")
                links.append(net.describe_failure(exc) or "")
        return None, None, failed

    def link_failure() -> dict:
        return {"_error": f"Overpass could not deliver this load: {links[-1]}",
                "code": net.NETWORK_ERROR, "suggestion": net.NETWORK_SUGGESTION}

    endpoints = _osm_endpoints(area_km2)
    endpoint_used, path, errors = sweep(endpoints)
    own_failed = bool(endpoints) and path is None and _own_overpass_unreachable(errors)
    fell_back = own_failed or not endpoints
    public_cap = (volume_guard.dense_max_km2() if volume_guard.is_dense("fetch_osm_data", args)
                  else limits.current("MAX_FETCH_KM2"))
    if not endpoints and area_km2 > public_cap:
        return {"_error": (f"{area_km2:,.0f} square kilometres needs TerraLab's own Overpass instance, "
                           "which is not answering or not served to this session."),
                "area_km2": round(area_km2, 1), "code": limits.CEILING_CODE,
                "suggestion": (f"Ask for at most {public_cap:,.0f} km², or call "
                               "fetch_overture with the matching theme, which is served from our tiles.")}
    if path is None and own_failed and area_km2 > public_cap:


        volume_guard.note_own_overpass_down()
        if links and all(links):
            return link_failure()
        return {"_error": (f"TerraLab's own Overpass instance is not answering, and {area_km2:,.0f} square "
                           "kilometres is more than a public mirror is sent."),
                "area_km2": round(area_km2, 1), "code": limits.CEILING_CODE,
                "suggestion": (f"Say so. Try again in a few minutes, ask for at most {public_cap:,.0f} km\u00b2, or "
                               "call fetch_overture with the matching theme, which is served from our tiles.")}
    warning = ""
    if path is None and fell_back:
        if own_failed:
            volume_guard.note_own_overpass_down()
        endpoint_used, path, fallen = sweep(_osm_public_mirrors(endpoints))
        errors = fallen if (fallen and _overpass_rejection(fallen)) else errors + fallen
        if path is not None:
            warning = _osm_fallback_warning(endpoint_used, own_failed)
    if path is not None:
        return {"path": path, "endpoint": endpoint_used, "warning": warning}
    down_prefix = ("TerraLab's Overpass instance is down and the public mirrors failed too. " if fell_back else "")
    if partial:

        endpoint_used, (path, remark) = max(partial.items(), key=lambda kv: os.path.getsize(kv[1][0]))
        return {"path": path, "endpoint": endpoint_used, "remark": remark, "warning": warning}
    if any("Invalid response from Overpass API" in str(error) for error in errors):
        return {"_error": f"{down_prefix}Invalid response from Overpass API; no mirror returned usable data."}
    rejected = _overpass_rejection(errors)
    if rejected:
        return {"_error": f"Overpass rejected the query: {rejected}", "query": final_query,
                "suggestion": "The servers are up; fix the query it names, or narrow the box, and call again."}
    if links and all(links):


        return link_failure()
    return {"_error": f"{down_prefix}Overpass could not deliver this load{_overpass_why(errors)}.",
            "suggestion": ("Say what stopped it. A smaller area, or the ways with out geom; instead of a recursion, "
                           "asks less of the server.")}
