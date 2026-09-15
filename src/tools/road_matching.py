# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Map matching: every feature of a line layer redrawn along the roads it followed."""
























from __future__ import annotations

import json
import math
import threading
import time
import urllib.error
import urllib.request
import uuid

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
)

from ..core import limits, net, tuning
from ..core.qt_compat import enum_member, field_type
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .data_tools import _OSRM_PROFILES, _USER_AGENT, _osrm_base, _run_on_main_thread
from .layer_lookup import _find_layer, _layer_not_found_error




_MAX_COORDINATES = 10
_MAX_RADIUS_M = 25

_OVERLAP = 3





_SPACINGS_M = (250.0,)



_SLICE_SECONDS = 60.0
_REQUEST_TIMEOUT_S = 30

_MAX_CONSECUTIVE_FAILURES = 3
_JOBS_KEPT = 8
_FIELDS = ("match_status", "match_confidence", "match_reason")
_NOTE = ("Matched by the public OSRM demonstration server run by FOSSGIS, on OpenStreetMap roads, with no "
         "guarantee. Credit OSRM and OpenStreetMap contributors.")

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


def register_road_matching(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="match_lines_to_roads",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "profile": {"type": "string", "enum": ["driving", "walking", "cycling"]},
                "selected_only": {"type": "boolean"},
                "feature_ids": {"type": "array", "items": {"type": "integer"}},
                "gps_accuracy_m": {"type": "number", "minimum": 5, "maximum": 25},
                "output_name": {"type": "string"},
                "task_id": {"type": "string"},
            },
            "required": [],
        },
        handler=_match_lines_to_roads,
        background=True,
    ))





def _metres(a, b) -> float:
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(math.radians(b[0] - a[0]) / 2) ** 2)
    return 2 * 6371008.8 * math.asin(min(1.0, math.sqrt(h)))


def _length(coords) -> float:
    return sum(_metres(a, b) for a, b in zip(coords, coords[1:]))


def _resample(coords, spacing: float) -> list:
    """Positions every ``spacing`` metres along the line, both ends kept."""






    points = [tuple(c[:2]) for c in coords if c is not None]
    if len(points) < 2:
        return points
    out = [points[0]]
    carried = 0.0
    for a, b in zip(points, points[1:]):
        step = _metres(a, b)
        if step <= 0:
            continue
        position = spacing - carried
        while position <= step:
            t = position / step
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            position += spacing
        carried = step - (position - spacing)
    if _metres(out[-1], points[-1]) > spacing * 0.25:
        out.append(points[-1])
    return out


def _windows(count: int, size: int, overlap: int) -> list[tuple[int, int]]:
    """``(first, last)`` sample indices of each request, consecutive ones sharing ``overlap``."""
    if count < 2:
        return []
    if count <= size:
        return [(0, count - 1)]
    stride = size - overlap
    out, first = [], 0
    while True:
        last = min(first + size - 1, count - 1)
        out.append((first, last))
        if last == count - 1:
            return out
        first += stride


def _cuts(windows: list[tuple[int, int]], overlap: int) -> list[tuple[int, int]]:
    """The sample range each window keeps: its seams sit in the middle of each overlap."""
    kept = []
    for number, (first, last) in enumerate(windows):
        start = first if number == 0 else first + overlap // 2
        end = last if number == len(windows) - 1 else windows[number + 1][0] + overlap // 2
        kept.append((start, max(start, end)))
    return kept


def _project(p, a, b):
    """The point of segment ab closest to p, and its distance in metres (local plane)."""
    k = math.cos(math.radians(p[1]))
    ax, ay, bx, by, px, py = a[0] * k, a[1], b[0] * k, b[1], p[0] * k, p[1]
    dx, dy = bx - ax, by - ay
    span = dx * dx + dy * dy
    t = 0.0 if span == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / span))
    q = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
    return q, _metres(p, q)


def _locate(coords, point, start: int) -> tuple[int, tuple]:
    """Index of the segment of ``coords`` from ``start`` on nearest to ``point``, and the point on it."""
    best = (math.inf, start, tuple(coords[start]))
    for index in range(start, len(coords) - 1):
        q, distance = _project(point, coords[index], coords[index + 1])
        if distance < best[0]:
            best = (distance, index, q)
            if distance < 0.05:
                break
    return best[1], best[2]


def _slice_between(coords, a, b) -> list:
    """The part of a matched line between two of its snapped positions, in travel order."""
    if len(coords) < 2:
        return [tuple(c) for c in coords]
    i, qa = _locate(coords, a, 0)
    j, qb = _locate(coords, b, i)
    return [qa, *[tuple(c) for c in coords[i + 1:j + 1]], qb]


def _append(line: list, piece: list) -> None:
    for point in piece:
        if line and _metres(line[-1], point) < 0.5:
            continue
        line.append(point)


def _part_result(samples: list, windows: list, answers: list) -> dict:
    """One line part assembled from its window answers, with what it cost in trust."""
    kept = _cuts(windows, _OVERLAP)
    line: list = []
    weighted, distance, matched, total, failed, reasons = 0.0, 0.0, 0, 0, 0, []
    for (first, _last), (start, end), answer in zip(windows, kept, answers):
        total += end - start + 1
        if not answer or not answer.get("ok"):
            failed += 1
            if answer and answer.get("reason"):
                reasons.append(answer["reason"])
            _append(line, samples[start:end + 1])
            continue
        tracepoints = answer["tracepoints"]
        local = [tracepoints[i - first] if 0 <= i - first < len(tracepoints) else None
                 for i in range(start, end + 1)]
        snapped = [p for p in local if p is not None]
        if not snapped:
            failed += 1
            reasons.append("no position of this stretch lies near a road")
            _append(line, samples[start:end + 1])
            continue
        matched += len(snapped)
        weighted += answer["confidence"] * answer["distance"]
        distance += answer["distance"]
        _append(line, _slice_between(answer["coords"], snapped[0], snapped[-1]))
    confidence = (weighted / distance if distance else 0.0) * (matched / total if total else 0.0)
    return {"coords": line, "failed": failed, "windows": len(windows), "confidence": confidence,
            "distance": distance, "reasons": reasons}





def _match_url(profile: str) -> str:
    base = _osrm_base(profile)
    if "/route/v1/" not in base:
        base = _OSRM_PROFILES.get(profile, _OSRM_PROFILES["driving"])
    return base.replace("/route/v1/", "/match/v1/")


def _ask(url: str) -> dict:
    """One window: ``{"ok": True, coords, tracepoints, confidence, distance}`` or a reason."""





    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    budget = tuning.limit("net", "route_timeout_s", _REQUEST_TIMEOUT_S)
    try:
        body = net.fetch(request, timeout=budget, max_bytes=4 * 1024 * 1024, total_timeout=budget * 2).body
    except net.FetchCancelled:
        raise
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read() or b""
        except Exception:  # noqa: BLE001 - a body we cannot read is a plain HTTP failure
            body = b""
        if not body:
            return {"ok": False, "network": exc.code >= 500 or exc.code == 429,
                    "reason": f"the map matching service answered HTTP {exc.code}"}
    except (urllib.error.URLError, OSError) as exc:
        return {"ok": False, "network": True, "reason": f"the map matching service did not answer ({exc})"}
    try:
        answer = json.loads(body)
    except (ValueError, TypeError):
        return {"ok": False, "network": True, "reason": "the map matching service sent something that is not JSON"}
    if answer.get("code") != "Ok" or not answer.get("matchings"):
        detail = answer.get("message") or answer.get("code") or "no match"
        return {"ok": False, "network": False, "reason": f"OSRM: {detail}"}
    coords: list = []
    weighted, distance = 0.0, 0.0
    for matching in answer["matchings"]:
        _append(coords, [tuple(c) for c in (matching.get("geometry") or {}).get("coordinates") or []])
        length = float(matching.get("distance") or 0.0)
        weighted += float(matching.get("confidence") or 0.0) * length
        distance += length
    tracepoints = [tuple(tp["location"]) if tp else None for tp in answer.get("tracepoints") or []]
    return {"ok": True, "coords": coords, "tracepoints": tracepoints,
            "confidence": weighted / distance if distance else 0.0, "distance": distance}





def _line_layer(layer) -> bool:
    try:
        return QgsWkbTypes.geometryType(layer.wkbType()) == enum_member(QgsWkbTypes, "GeometryType", "LineGeometry")
    except Exception:  # noqa: BLE001 - QGIS 4 spells it on Qgis
        from qgis.core import Qgis
        return layer.geometryType() == Qgis.GeometryType.Line


def _read_tracks(args: dict) -> dict:
    """Main thread: the tracks as plain lists in EPSG:4326, plus what the output layer needs."""
    layer = _find_layer(str(args.get("layer_name") or ""))
    if layer is None:
        return _layer_not_found_error(str(args.get("layer_name") or ""))
    if not isinstance(layer, QgsVectorLayer) or not _line_layer(layer):
        return tool_error(f"'{layer.name()}' is not a line layer.", "INVALID_ARGS",
                          "Pass a layer of line tracks; points can be joined into lines with "
                          "native:pointstopath first.")
    request = QgsFeatureRequest()
    ids = [int(i) for i in args.get("feature_ids") or []]
    if ids:
        request.setFilterFids(ids)
    features = layer.getSelectedFeatures(request) if args.get("selected_only") else layer.getFeatures(request)
    cap = limits.current("MAX_FEATURES_PER_CALL")
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    to_wgs84 = QgsCoordinateTransform(layer.crs(), wgs84, QgsProject.instance())
    tracks = []
    for feature in features:
        if len(tracks) >= cap:
            return tool_error(f"More than {cap} tracks in one call.", "INVALID_ARGS",
                              "Pass feature_ids or selected_only with a smaller group, then the next.")
        geometry = feature.geometry()
        parts: list = []
        wkb = None
        if geometry is not None and not geometry.isNull() and not geometry.isEmpty():
            wkb = bytes(geometry.asWkb())
            copy = QgsGeometry(geometry)
            copy.transform(to_wgs84)
            if not copy.isEmpty():
                lines = copy.asMultiPolyline() if QgsWkbTypes.isMultiType(copy.wkbType()) else [copy.asPolyline()]
                parts = [[(p.x(), p.y()) for p in line] for line in lines if len(line) >= 2]
        tracks.append({"fid": feature.id(), "attributes": list(feature.attributes()), "parts": parts, "wkb": wkb})
    if not tracks:
        return tool_error(f"No feature to match in '{layer.name()}'.", "INVALID_ARGS",
                          "Check selected_only and feature_ids: nothing matched them.")
    return {"layer_id": layer.id(), "layer_name": layer.name(), "crs": layer.crs().toWkt(),
            "authid": layer.crs().authid(), "fields": [QgsField(f) for f in layer.fields()], "tracks": tracks}


def _unique_name(existing: set, wanted: str) -> str:
    name, number = wanted, 2
    while name.lower() in existing:
        name, number = f"{wanted}_{number}", number + 1
    existing.add(name.lower())
    return name


def _publish(job: dict) -> dict:
    """Main thread: the output layer, source attributes plus status, confidence and reason."""
    crs = QgsCoordinateReferenceSystem.fromWkt(job["crs"])
    layer = QgsVectorLayer("MultiLineString" + (f"?crs={job['authid']}" if job["authid"] else ""),
                           job["output_name"], "memory")
    if not job["authid"]:
        layer.setCrs(crs)
    provider = layer.dataProvider()
    taken = {f.name().lower() for f in job["fields"]}
    names = [_unique_name(taken, wanted) for wanted in _FIELDS]
    provider.addAttributes([*job["fields"], QgsField(names[0], field_type("String")),
                            QgsField(names[1], field_type("Double")), QgsField(names[2], field_type("String"))])
    layer.updateFields()
    back = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), crs, QgsProject.instance())
    rows = []
    for track in job["tracks"]:
        feature = QgsFeature(layer.fields())
        outcome = track["outcome"]
        if outcome["status"] == "unmatched":
            geometry = QgsGeometry()
            if track["wkb"]:
                geometry.fromWkb(track["wkb"])
                if geometry.constGet() is not None:
                    geometry.get().dropZValue()
                    geometry.get().dropMValue()
                    geometry.convertToMultiType()
        else:
            geometry = QgsGeometry.fromMultiPolylineXY(
                [[QgsPointXY(x, y) for x, y in line] for line in outcome["lines"] if len(line) >= 2])
            geometry.transform(back)
        feature.setGeometry(geometry)
        feature.setAttributes([*track["attributes"], outcome["status"], round(outcome["confidence"], 3),
                               outcome["reason"]])
        rows.append(feature)
    provider.addFeatures(rows)
    layer.updateExtents()
    QgsProject.instance().addMapLayer(layer)
    return {"layer_name": layer.name(), "layer_id": layer.id()}





def _plan(tracks: list, ceiling: int) -> tuple[float, int, int]:
    """``(spacing, requests, tracks that fit)``: the densest spacing inside the ceiling."""
    size = max(_OVERLAP + 2, tuning.limit("net", "osrm_match_max_coordinates", _MAX_COORDINATES))
    best = None
    for spacing in _SPACINGS_M:
        per_track = [sum(len(_windows(len(_resample(part, spacing)), size, _OVERLAP)) for part in track["parts"])
                     for track in tracks]
        total = sum(per_track)
        fit, running = 0, 0
        for cost in per_track:
            if running + cost > ceiling:
                break
            running, fit = running + cost, fit + 1
        best = (spacing, total, fit)
        if total <= ceiling:
            return best
    return best


def _prepare(job: dict, spacing: float) -> None:
    size = max(_OVERLAP + 2, tuning.limit("net", "osrm_match_max_coordinates", _MAX_COORDINATES))
    radius = min(float(job["radius"]), float(tuning.limit("net", "osrm_match_max_radius_m", _MAX_RADIUS_M)))
    base = _match_url(job["profile"])
    queue = []
    for t, track in enumerate(job["tracks"]):
        track["samples"] = [_resample(part, spacing) for part in track["parts"]]
        track["windows"] = [_windows(len(samples), size, _OVERLAP) for samples in track["samples"]]
        track["answers"] = [[None] * len(windows) for windows in track["windows"]]
        for p, windows in enumerate(track["windows"]):
            for w, (first, last) in enumerate(windows):
                chunk = track["samples"][p][first:last + 1]
                coordinates = ";".join(f"{x:.6f},{y:.6f}" for x, y in chunk)
                radii = ";".join(f"{radius:g}" for _ in chunk)
                url = (f"{base}/{coordinates}?geometries=geojson&overview=full&gaps=ignore&tidy=false"
                       f"&radiuses={radii}")
                queue.append((t, p, w, url))
    job.update({"queue": queue, "next": 0, "spacing": spacing, "failures_in_a_row": 0})


def _finish_track(track: dict) -> dict:
    lines, failed, windows, weighted, distance, reasons = [], 0, 0, 0.0, 0.0, []
    for samples, part_windows, answers in zip(track["samples"], track["windows"], track["answers"]):
        if not part_windows:
            continue
        part = _part_result(samples, part_windows, answers)
        lines.append(part["coords"])
        failed += part["failed"]
        windows += part["windows"]
        weighted += part["confidence"] * max(part["distance"], 1.0)
        distance += max(part["distance"], 1.0)
        reasons.extend(part["reasons"])
    if not windows:
        return {"status": "unmatched", "confidence": 0.0, "lines": [],
                "reason": "no line to match: the geometry is empty or shorter than two positions"}
    if failed == windows:
        return {"status": "unmatched", "confidence": 0.0, "lines": [],
                "reason": "kept as recorded: " + (reasons[0] if reasons else "no stretch matched a road")}
    confidence = weighted / distance if distance else 0.0
    if failed:
        return {"status": "partial", "confidence": confidence * (1 - failed / windows), "lines": lines,
                "reason": f"{failed} of {windows} stretches kept their recorded shape: "
                          + (reasons[0] if reasons else "no match")}
    recorded = sum(_length(s) for s in track["samples"] if len(s) >= 2)
    matched = sum(_length(line) for line in lines if len(line) >= 2)
    reason = ""
    if recorded and matched > recorded * 1.25:
        reason = (f"the matched line is {round(100 * (matched / recorded - 1))}% longer than the track: "
                  "check it for detours")
    return {"status": "matched", "confidence": confidence, "lines": lines, "reason": reason}


def _progress(job: dict) -> dict:
    done, total = job["next"], len(job["queue"])
    return {"status": "running", "task_id": job["task_id"], "progress": round(100 * done / total) if total else 100,
            "requests_done": done, "requests_total": total, "tracks": len(job["tracks"]),
            "spacing_m": job["spacing"],
            "poll": {"tool": "match_lines_to_roads", "args": {"task_id": job["task_id"]}, "interval_s": 1,
                     "timeout_s": 900 + 3 * total, "label": "Matching tracks to roads"}}


def _advance(job: dict) -> dict:
    """Work the next slice; publish when the last request is in."""
    if job.get("result") is not None:
        return job["result"]
    if not job["lock"].acquire(blocking=False):
        return _progress(job)
    try:
        cancel = net.current_cancel_check()
        started = time.monotonic()
        while job["next"] < len(job["queue"]):
            if cancel is not None and cancel():
                return {"status": "canceled", "task_id": job["task_id"], "requests_done": job["next"]}
            if time.monotonic() - started > _SLICE_SECONDS:
                return _progress(job)
            t, p, w, url = job["queue"][job["next"]]
            try:
                answer = _ask(url)
            except net.FetchCancelled:
                return {"status": "canceled", "task_id": job["task_id"], "requests_done": job["next"]}
            job["tracks"][t]["answers"][p][w] = answer
            job["next"] += 1
            job["failures_in_a_row"] = job["failures_in_a_row"] + 1 if answer.get("network") else 0
            if job["failures_in_a_row"] >= _MAX_CONSECUTIVE_FAILURES:
                reason = answer.get("reason") or "the map matching service stopped answering"
                for rest_t, rest_p, rest_w, _ in job["queue"][job["next"]:]:
                    job["tracks"][rest_t]["answers"][rest_p][rest_w] = {"ok": False, "reason": reason}
                job["gave_up"] = reason
                job["next"] = len(job["queue"])
        for track in job["tracks"]:
            track["outcome"] = _finish_track(track)
        out = _run_on_main_thread(_publish, job, timeout=60)
        if not isinstance(out, dict) or out.get("_error") or out.get("code"):
            return out
        outcomes = [track["outcome"] for track in job["tracks"]]
        counts = {status: sum(1 for o in outcomes if o["status"] == status)
                  for status in ("matched", "partial", "unmatched")}
        kept = [o["confidence"] for o in outcomes if o["status"] != "unmatched"]
        result = {
            "status": "complete", "task_id": job["task_id"], **out, "tracks": len(outcomes), **counts,
            "mean_confidence": round(sum(kept) / len(kept), 3) if kept else 0.0,
            "requests": len(job["queue"]), "spacing_m": job["spacing"], "profile": job["profile"],
            "source_layer": job["layer_name"],
            "not_matched": [{"fid": track["fid"], "status": track["outcome"]["status"],
                             "reason": track["outcome"]["reason"]}
                            for track in job["tracks"] if track["outcome"]["status"] != "matched"][:5],
            "fields": {"match_status": "matched, partial or unmatched (kept as recorded)",
                       "match_confidence": "0 to 1: OSRM's confidence times the share of positions matched",
                       "match_reason": "why a track is partial or unmatched, or a detour warning"},
            "note": _NOTE,
        }
        if job.get("gave_up"):
            result["warning"] = (f"The service stopped answering part way: {job['gave_up']}. "
                                 "Run again later for the rest.")
        job["result"] = result
        return result
    finally:
        job["lock"].release()


def _match_lines_to_roads(args: dict) -> dict:
    task_id = str(args.get("task_id") or "").strip()
    if task_id:
        job = _JOBS.get(task_id)
        if job is None:
            return tool_error(f"No map matching job {task_id} in this QGIS session.", "INVALID_ARGS",
                              "Start it again with layer_name: jobs do not survive a plugin reload.")
        return _advance(job)
    if not str(args.get("layer_name") or "").strip():
        return tool_error("layer_name is required to start matching.", "INVALID_ARGS",
                          "Pass the line layer to match, or task_id to follow a running job.")
    read = _run_on_main_thread(_read_tracks, args, timeout=60)
    if not isinstance(read, dict) or "tracks" not in read:
        return read
    ceiling = limits.current("MAP_MATCH_MAX_REQUESTS")
    spacing, requests, fit = _plan(read["tracks"], ceiling)
    if requests > ceiling:
        return tool_error(
            f"Matching these {len(read['tracks'])} tracks needs about {requests} requests to the public map "
            f"matching service, past the {ceiling} one call may send (one a second, on a shared "
            "demonstration server).",
            "INVALID_ARGS",
            f"Match them in groups: pass feature_ids with about {max(1, fit)} tracks, and the next group when "
            "that one completes.")
    profile = args.get("profile") if args.get("profile") in _OSRM_PROFILES else "driving"
    job = {
        "task_id": "roadmatch-" + uuid.uuid4().hex[:12], "lock": threading.Lock(), "result": None,
        "profile": profile, "radius": args.get("gps_accuracy_m") or _MAX_RADIUS_M,
        "output_name": str(args.get("output_name") or "").strip() or f"{read['layer_name']} matched to roads",
        **read,
    }
    _prepare(job, spacing)
    with _JOBS_LOCK:
        _JOBS[job["task_id"]] = job
        while len(_JOBS) > _JOBS_KEPT:
            _JOBS.pop(next(iter(_JOBS)))
    return _advance(job)
