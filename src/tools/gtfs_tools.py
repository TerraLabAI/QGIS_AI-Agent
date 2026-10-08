# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
































from __future__ import annotations

import csv
import datetime
import io
import math
import os
import posixpath
import re
import time
import urllib.error
import urllib.request
import zipfile
from collections import Counter, defaultdict

from qgis.core import QgsProject, QgsVectorLayer
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP, QCoreApplication

from ..core import limits, net, security
from ..core.background import run_on_main_thread
from ..core.host_platform import remove_tree
from ..core.policy import create_managed_temp_dir
from ..core.qt_compat import enum_member
from ..core.serialization import CodedText
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .csv_loader import _ansi_encoding, detect_encoding
from .data_common import _download_timeout, _safe_filename


def _tr(text: str) -> str:
    return QCoreApplication.translate("GtfsTools", text)





_REQUIRED = ("stops.txt", "routes.txt", "trips.txt", "stop_times.txt")

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


_ROUTE_TYPE_WORDS = {
    "0": "tram", "1": "metro", "2": "rail", "3": "bus", "4": "ferry", "5": "cable_tram",
    "6": "aerial_lift", "7": "funicular", "11": "trolleybus", "12": "monorail",
}
_ROUTE_TYPE_COLORS = {
    "tram": "#8E44AD", "metro": "#2C3E50", "rail": "#C0392B", "bus": "#2980B9",
    "ferry": "#16A085", "cable_tram": "#D35400", "aerial_lift": "#E67E22",
    "funicular": "#7F8C8D", "trolleybus": "#27AE60", "monorail": "#F39C12", "other": "#616A6B",
}




_STOP_TIMES_CHECK_EVERY = 20_000



_SHAPES_CHECK_EVERY = 20_000




_MAX_FREQUENCY_DEPARTURES_PER_WINDOW = 20_000
_ROUTE_CLASSES = 5
_VALID_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")



_DOWNLOAD_BUDGET_S = 180


def register_gtfs_tools(registry: ToolRegistry):


    registry.register(Tool(
        name="load_gtfs",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Load GTFS feed {source}"),
        input_schema={
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "date": {"type": "string"},
                "weekday": {"type": "string", "enum": list(_WEEKDAYS)},
                "merge_distance_m": {"type": "number", "minimum": 0},
                "layer_name": {"type": "string"},
            },
            "required": ["source"],
        },
        handler=_load_gtfs,



        background=True,
    ))




def _check_args(args: dict) -> dict | None:
    source = str(args.get("source") or "").strip()
    if not source:
        return tool_error("source is missing.", "INVALID_ARGS",
                          "a GTFS zip's path, a folder holding its .txt files, or an https URL.")
    date = str(args.get("date") or "").strip()
    if date and not _VALID_DATE.match(date):
        return tool_error(f"date {date!r} is not YYYY-MM-DD.", "INVALID_ARGS", "a date as YYYY-MM-DD.")
    merge_distance_m = args.get("merge_distance_m")
    if merge_distance_m is not None:
        try:
            if float(merge_distance_m) < 0:
                raise ValueError
        except (TypeError, ValueError):
            return tool_error("merge_distance_m must be a positive number of metres.", "INVALID_ARGS",
                              "It is optional, or a distance in metres such as 50.")
    return None




class _GtfsSource:


    def __init__(self):
        self._zip: zipfile.ZipFile | None = None
        self._dir: str | None = None
        self._members: dict[str, str] = {}
        self._files: dict[str, str] = {}

    def open_zip(self, path: str) -> None:
        self._zip = zipfile.ZipFile(path, "r")
        for info in self._zip.infolist():
            base = posixpath.basename(info.filename).lower()
            if base and base not in self._members:
                self._members[base] = info.filename

    def open_dir(self, path: str) -> None:
        self._dir = path
        for name in os.listdir(path):
            base = name.lower()
            if base.endswith(".txt"):
                self._files.setdefault(base, name)

    def has(self, name: str) -> bool:
        return name.lower() in self._members or name.lower() in self._files

    def rows(self, name: str):





        key = name.lower()
        if self._zip is not None:
            real = self._members.get(key)
            if real is None:
                return iter(())

            def opener():
                return self._zip.open(real)
        elif self._dir is not None:
            real = self._files.get(key)
            if real is None:
                return iter(())

            def opener():
                return open(os.path.join(self._dir, real), "rb")
        else:
            return iter(())
        return _decoded_rows(opener, name)

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()


class _GtfsEncodingError(Exception):


    def __init__(self, name: str, tried: tuple[str, ...]):
        self.name = name
        self.tried = tried
        super().__init__(f"{name}: could not decode as {' or '.join(tried)}")


def _decoded_rows(opener, name: str):














    raw = opener()
    try:
        sample = raw.peek(65536)
    except (AttributeError, OSError):
        sample = b""
    guess = detect_encoding(sample)
    encoding = "utf-8-sig" if guess == "UTF-8" else guess
    text = io.TextIOWrapper(raw, encoding=encoding, newline="")
    try:
        yield from csv.DictReader(text)
        return
    except UnicodeDecodeError:
        pass
    text.close()
    fallback = _ansi_encoding()
    if fallback == encoding:
        raise _GtfsEncodingError(name, (encoding,))
    raw = opener()
    text = io.TextIOWrapper(raw, encoding=fallback, newline="")
    try:
        yield from csv.DictReader(text)
    except UnicodeDecodeError as exc:
        raise _GtfsEncodingError(name, (encoding, fallback)) from exc


def _local_source(source: str) -> tuple[_GtfsSource | None, dict | None]:

    expanded = security.expand_path(source)
    error = security.validate_path(expanded)
    if error:
        return None, tool_error(error, "PERMISSION_DENIED",
                                "" if isinstance(error, CodedText) else "a path under your home folder or the project.")
    if os.path.isdir(expanded):
        gtfs = _GtfsSource()
        gtfs.open_dir(expanded)
        return gtfs, None
    if not os.path.isfile(expanded):
        return None, tool_error(f"{source} does not exist.", "INVALID_ARGS",
                                "a GTFS zip's path, a folder of its .txt files, or an https URL.")
    try:
        gtfs = _GtfsSource()
        gtfs.open_zip(expanded)
    except zipfile.BadZipFile:
        return None, tool_error(f"{source} is not a valid ZIP archive.", "INVALID_ARGS", hint="gtfs_not_a_zip")
    return gtfs, None


def _download(url: str, tmp_dir: str) -> tuple[str | None, dict | None]:






    net.check_url(url)
    cap = limits.current("GTFS_ZIP_MAX_BYTES")
    req = urllib.request.Request(url, headers={"User-Agent": net.user_agent()})
    target = os.path.join(tmp_dir, "feed.zip")
    try:
        net.fetch_to_file(req, target, timeout=_download_timeout(), max_bytes=cap,
                          total_timeout=_DOWNLOAD_BUDGET_S,
                          cancel=net.current_cancel_check())
    except net.FetchTooLarge:
        return None, tool_error(
            f"That feed is over the {_human_bytes(cap)} this tool downloads.", "INVALID_ARGS",
            hint="gtfs_feed_too_large", max_size=_human_bytes(cap))
    except net.FetchCancelled:
        return None, tool_error("The run was stopped.", "CANCELLED", "No file was loaded.")
    except net.FetchDeadline as exc:
        return None, tool_error(f"The download did not finish in time: {exc}", "TIMEOUT",
                                hint="gtfs_download_timeout")
    except (urllib.error.URLError, OSError) as exc:
        return None, tool_error(f"The download failed: {net.describe_failure(exc) or exc}", "EXECUTION_FAILED",
                                net.NETWORK_SUGGESTION)
    return target, None


def _human_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{n} B"




def _feed_validity(calendar_rows: list, calendar_dates_rows: list) -> tuple[str | None, str | None]:
    starts = [r["start_date"] for r in calendar_rows if r.get("start_date")]
    ends = [r["end_date"] for r in calendar_rows if r.get("end_date")]
    dates = [r["date"] for r in calendar_dates_rows if r.get("date")]
    los = starts + dates
    his = ends + dates
    return (min(los) if los else None), (max(his) if his else None)


def _yyyymmdd(text: str) -> datetime.date:
    return datetime.date(int(text[0:4]), int(text[4:6]), int(text[6:8]))


def _resolve_date(args: dict, calendar_rows: list, calendar_dates_rows: list) -> tuple[str | None, str, dict | None]:

    asked_date = str(args.get("date") or "").strip()
    if asked_date:
        compact = asked_date.replace("-", "")
        try:
            _yyyymmdd(compact)
        except ValueError:
            return None, "", tool_error(f"{asked_date} is not a real date.", "INVALID_ARGS", "a valid date.")
        return compact, f"the date you asked for ({asked_date})", None
    lo, hi = _feed_validity(calendar_rows, calendar_dates_rows)
    if lo is None:
        return None, "", tool_error(
            "This feed names no service date in calendar.txt or calendar_dates.txt.", "INVALID_ARGS",
            "The feed is not usable without at least one of those files.")
    lo_date = _yyyymmdd(lo)
    hi_date = _yyyymmdd(hi) if hi else None
    asked_weekday = str(args.get("weekday") or "").strip().lower()
    if asked_weekday in _WEEKDAYS:
        target_wd = _WEEKDAYS.index(asked_weekday)
        why = f"the next {asked_weekday.capitalize()} inside the feed's service dates, as asked"
    else:
        target_wd = _busiest_weekday(calendar_rows, calendar_dates_rows)
        if target_wd is None:
            return lo, f"the first date this feed names ({lo}); no weekday pattern could be read", None
        why = (f"a representative {_WEEKDAYS[target_wd].capitalize()}, the weekday with the most scheduled "
               f"service in this feed, inside its {lo} to {hi or lo} service dates")
    delta = (target_wd - lo_date.weekday()) % 7
    candidate = lo_date + datetime.timedelta(days=delta)
    if hi_date is not None and candidate > hi_date:
        candidate = lo_date
        why = f"the first date this feed names ({lo}); it is too short to reach a {_WEEKDAYS[target_wd]}"
    return candidate.strftime("%Y%m%d"), why, None


def _busiest_weekday(calendar_rows: list, calendar_dates_rows: list) -> int | None:
    tally: Counter = Counter()
    for row in calendar_rows:
        for index, day in enumerate(_WEEKDAYS):
            if row.get(day) == "1":
                tally[index] += 1
    if tally:
        return tally.most_common(1)[0][0]
    for row in calendar_dates_rows:
        if row.get("exception_type") == "1" and row.get("date"):
            try:
                tally[_yyyymmdd(row["date"]).weekday()] += 1
            except (ValueError, KeyError):
                continue
    return tally.most_common(1)[0][0] if tally else None


def _active_services(date_compact: str, calendar_rows: list, calendar_dates_rows: list) -> set:
    weekday = _WEEKDAYS[_yyyymmdd(date_compact).weekday()]
    active: set = set()
    for row in calendar_rows:
        service_id = row.get("service_id")
        if not service_id:
            continue
        start, end = row.get("start_date") or "", row.get("end_date") or ""
        if start <= date_compact <= end and row.get(weekday) == "1":
            active.add(service_id)
    for row in calendar_dates_rows:
        if row.get("date") != date_compact:
            continue
        service_id = row.get("service_id")
        if not service_id:
            continue
        if row.get("exception_type") == "1":
            active.add(service_id)
        elif row.get("exception_type") == "2":
            active.discard(service_id)
    return active




def _gtfs_seconds(text: str) -> int | None:

    parts = (text or "").strip().split(":")
    if len(parts) != 3:
        return None
    try:
        h, m, s = (int(p) for p in parts)
    except ValueError:
        return None
    if m < 0 or m > 59 or s < 0 or s > 59 or h < 0:
        return None
    return h * 3600 + m * 60 + s


def _clock(seconds: int) -> str:
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"




def _stream_stop_times(gtfs: _GtfsSource, active_trip_route: dict, want_sequence_for: set,
                       freq_trip_ids: set, cancel, max_rows: int) -> dict:















    per_stop_trips: Counter = Counter()
    per_stop_first: dict = {}
    per_stop_last: dict = {}
    hour_tally: Counter = Counter()
    sequences: dict = defaultdict(list)
    freq_patterns: dict = defaultdict(list)
    rows_read = 0
    cut = False
    for row in gtfs.rows("stop_times.txt"):
        rows_read += 1
        if rows_read % _STOP_TIMES_CHECK_EVERY == 0:
            if cancel is not None and cancel():
                raise InterruptedError("Stopped while stop_times.txt was read")
        if rows_read > max_rows:
            cut = True
            break
        trip_id = row.get("trip_id")
        if trip_id in want_sequence_for:
            seq_text = row.get("stop_sequence") or ""
            try:
                seq = int(seq_text)
            except ValueError:
                seq = len(sequences[trip_id])
            sequences[trip_id].append((seq, row.get("stop_id") or ""))
        if trip_id in freq_trip_ids:
            stop_id = row.get("stop_id") or ""
            seconds = _gtfs_seconds(row.get("departure_time") or row.get("arrival_time") or "")
            if stop_id and seconds is not None:
                seq_text = row.get("stop_sequence") or ""
                try:
                    seq = int(seq_text)
                except ValueError:
                    seq = len(freq_patterns[trip_id])
                freq_patterns[trip_id].append((seq, stop_id, seconds))
            continue
        if active_trip_route.get(trip_id) is None:
            continue
        stop_id = row.get("stop_id") or ""
        if not stop_id:
            continue
        per_stop_trips[stop_id] += 1
        departure = _gtfs_seconds(row.get("departure_time") or row.get("arrival_time") or "")
        if departure is not None:
            hour_tally[(departure // 3600) % 24] += 1
            if stop_id not in per_stop_first or departure < per_stop_first[stop_id]:
                per_stop_first[stop_id] = departure
            if stop_id not in per_stop_last or departure > per_stop_last[stop_id]:
                per_stop_last[stop_id] = departure
    return {
        "per_stop_trips": per_stop_trips, "per_stop_first": per_stop_first, "per_stop_last": per_stop_last,
        "hour_tally": hour_tally, "sequences": sequences, "freq_patterns": freq_patterns,
        "rows_read": rows_read, "cut": cut,
    }




def _frequency_windows(freq_rows: list, active_trip_route: dict) -> dict:





    windows: dict = defaultdict(list)
    for row in freq_rows:
        trip_id = row.get("trip_id")
        if trip_id not in active_trip_route:
            continue
        start = _gtfs_seconds(row.get("start_time") or "")
        end = _gtfs_seconds(row.get("end_time") or "")
        try:
            headway = int(row.get("headway_secs") or "0")
        except ValueError:
            headway = 0
        if start is None or end is None or headway <= 0 or end <= start:
            continue
        windows[trip_id].append((start, end, headway))
    return windows


def _apply_frequencies(windows_by_trip: dict, stream: dict, active_trip_route: dict) -> tuple[int, Counter, int, int]:










    added = 0
    route_extra: Counter = Counter()
    matched = 0
    unmatched = 0
    for trip_id, windows in windows_by_trip.items():
        pattern = stream["freq_patterns"].get(trip_id)
        if not pattern:
            unmatched += 1
            continue
        ordered = sorted(pattern)
        first_seconds = ordered[0][2]
        offsets = [(stop_id, seconds - first_seconds) for _seq, stop_id, seconds in ordered]
        route_id = active_trip_route.get(trip_id)
        trip_added = 0
        for start, end, headway in windows:
            count = min((end - start + headway - 1) // headway, _MAX_FREQUENCY_DEPARTURES_PER_WINDOW)
            for i in range(count):
                departure_start = start + i * headway
                for stop_id, offset in offsets:
                    clock = departure_start + offset
                    stream["per_stop_trips"][stop_id] += 1
                    first = stream["per_stop_first"].get(stop_id)
                    if first is None or clock < first:
                        stream["per_stop_first"][stop_id] = clock
                    last = stream["per_stop_last"].get(stop_id)
                    if last is None or clock > last:
                        stream["per_stop_last"][stop_id] = clock
                    stream["hour_tally"][(clock // 3600) % 24] += 1
            trip_added += count
        if trip_added:
            matched += 1
            added += trip_added
            if route_id:
                route_extra[route_id] += trip_added - 1
    return added, route_extra, matched, unmatched




def _merge_stops(stop_rows: list, stream: dict, merge_distance_m: float | None) -> tuple[list, int, bool]:

    by_id = {row["stop_id"]: row for row in stop_rows if row.get("stop_id")}
    has_parent = any((row.get("parent_station") or "").strip() for row in stop_rows)
    groups: dict = {}
    for stop_id, row in by_id.items():
        parent = (row.get("parent_station") or "").strip()
        target = parent if has_parent and parent in by_id else stop_id
        groups.setdefault(target, []).append(stop_id)
    records = []
    merged_away = 0
    for target, members in groups.items():
        target_row = by_id[target]
        total_trips = sum(stream["per_stop_trips"].get(m, 0) for m in members)
        firsts = [stream["per_stop_first"][m] for m in members if m in stream["per_stop_first"]]
        lasts = [stream["per_stop_last"][m] for m in members if m in stream["per_stop_last"]]
        try:
            lat, lon = float(target_row.get("stop_lat") or "nan"), float(target_row.get("stop_lon") or "nan")
        except ValueError:
            continue
        if math.isnan(lat) or math.isnan(lon):
            continue
        if len(members) > 1:
            merged_away += len(members) - 1
        records.append({
            "stop_id": target, "name": target_row.get("stop_name") or target,
            "lat": lat, "lon": lon, "location_type": target_row.get("location_type") or "0",
            "trips_per_day": total_trips, "merged_children": len(members) - 1,
            "first": min(firsts) if firsts else None, "last": max(lasts) if lasts else None,
        })
    if not has_parent and merge_distance_m:
        records, extra = _cluster_by_distance(records, float(merge_distance_m))
        merged_away += extra
    return records, merged_away, has_parent


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _cluster_by_distance(records: list, distance_m: float) -> tuple[list, int]:







    if distance_m <= 0 or len(records) < 2:
        return records, 0
    cell_deg = max(distance_m, 1.0) / 111_000.0
    buckets: dict = defaultdict(list)
    for index, record in enumerate(records):
        buckets[(round(record["lat"] / cell_deg), round(record["lon"] / cell_deg))].append(index)
    parent = list(range(len(records)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for (kx, ky), members in buckets.items():
        neighbours = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neighbours.extend(buckets.get((kx + dx, ky + dy), []))
        for i in members:
            for j in neighbours:
                if j <= i:
                    continue
                if _haversine_m(records[i]["lat"], records[i]["lon"],
                                records[j]["lat"], records[j]["lon"]) <= distance_m:
                    union(i, j)
    groups: dict = defaultdict(list)
    for index in range(len(records)):
        groups[find(index)].append(index)
    merged = []
    extra = 0
    for indices in groups.values():
        if len(indices) == 1:
            merged.append(records[indices[0]])
            continue
        extra += len(indices) - 1
        members = [records[i] for i in indices]
        weight = sum(max(m["trips_per_day"], 0) for m in members) or len(members)
        lat = sum(m["lat"] * (max(m["trips_per_day"], 0) or 1) for m in members) / weight
        lon = sum(m["lon"] * (max(m["trips_per_day"], 0) or 1) for m in members) / weight
        busiest = max(members, key=lambda m: m["trips_per_day"])
        firsts = [m["first"] for m in members if m["first"] is not None]
        lasts = [m["last"] for m in members if m["last"] is not None]
        merged.append({
            "stop_id": busiest["stop_id"], "name": busiest["name"], "lat": lat, "lon": lon,
            "location_type": busiest["location_type"],
            "trips_per_day": sum(m["trips_per_day"] for m in members),
            "merged_children": sum(m["merged_children"] for m in members) + len(members) - 1,
            "first": min(firsts) if firsts else None, "last": max(lasts) if lasts else None,
        })
    return merged, extra




def _stream_shapes(gtfs: _GtfsSource, used_shape_ids: set, cancel, max_rows: int) -> tuple[dict, bool, int]:

















    pending: dict = defaultdict(list)
    rows_read = 0
    kept = 0
    cut = False
    cut_shape_id = None
    for row in gtfs.rows("shapes.txt"):
        rows_read += 1
        if rows_read % _SHAPES_CHECK_EVERY == 0:
            if cancel is not None and cancel():
                raise InterruptedError("Stopped while shapes.txt was read")
        shape_id = row.get("shape_id")
        if not shape_id or shape_id not in used_shape_ids:
            continue
        try:
            seq = int(row.get("shape_pt_sequence") or "0")
            lat, lon = float(row["shape_pt_lat"]), float(row["shape_pt_lon"])
        except (TypeError, ValueError, KeyError):
            continue
        pending[shape_id].append((seq, lon, lat))
        kept += 1
        if kept > max_rows:
            cut = True
            cut_shape_id = shape_id
            break
    if cut_shape_id is not None:
        pending.pop(cut_shape_id, None)
    shape_points = {}
    for shape_id, points in pending.items():
        ordered = [(lon, lat) for _seq, lon, lat in sorted(points)]
        if len(ordered) >= 2:
            shape_points[shape_id] = ordered
    return shape_points, cut, kept




def _route_lines(route_rows: list, trip_rows: list, shape_points: dict, stop_coords: dict,
                 stream: dict, per_route_trips: Counter) -> tuple[list, int]:







    trips_by_route: dict = defaultdict(list)
    for row in trip_rows:
        route_id = row.get("route_id")
        if route_id:
            trips_by_route[route_id].append(row)
    without_geometry = 0
    routes = []
    for row in route_rows:
        route_id = row.get("route_id")
        if not route_id:
            continue
        trips = trips_by_route.get(route_id, [])
        shape_ids = Counter(t["shape_id"] for t in trips if (t.get("shape_id") or "") in shape_points)
        line = None
        source = ""
        if shape_ids:
            best_shape = shape_ids.most_common(1)[0][0]
            line = shape_points[best_shape]
            source = "shape"
        else:
            patterns: Counter = Counter()
            for trip in trips:
                seq = stream["sequences"].get(trip.get("trip_id"))
                if not seq:
                    continue
                ordered = tuple(stop_id for _s, stop_id in sorted(seq))
                patterns[ordered] += 1
            if patterns:
                best_pattern = patterns.most_common(1)[0][0]
                points = [stop_coords[s] for s in best_pattern if s in stop_coords]
                if len(points) >= 2:
                    line = points
                    source = "stop_sequence"
        if line is None or len(line) < 2:
            without_geometry += 1
        routes.append({
            "route_id": route_id,
            "short_name": row.get("route_short_name") or "", "long_name": row.get("route_long_name") or "",
            "route_type": _ROUTE_TYPE_WORDS.get(str(row.get("route_type") or "").strip(), "other"),
            "route_color": (row.get("route_color") or "").strip().lstrip("#"),
            "trips_per_day": per_route_trips.get(route_id, 0),
            "line": line, "geometry_source": source,
        })
    return routes, without_geometry




def _write_geopackage(path: str, stops: list, routes: list) -> None:
    from osgeo import ogr, osr

    wgs84 = osr.SpatialReference()
    wgs84.ImportFromEPSG(4326)
    wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    target = ogr.GetDriverByName("GPKG").CreateDataSource(path)
    try:
        stop_layer = target.CreateLayer("stops", wgs84, ogr.wkbPoint, options=["SPATIAL_INDEX=YES"])
        for name, kind in (("stop_id", ogr.OFTString), ("name", ogr.OFTString),
                           ("location_type", ogr.OFTString), ("trips_per_day", ogr.OFTInteger),
                           ("merged_children", ogr.OFTInteger), ("first_departure", ogr.OFTString),
                           ("last_departure", ogr.OFTString)):
            stop_layer.CreateField(ogr.FieldDefn(name, kind))
        target.StartTransaction()
        for record in stops:
            feature = ogr.Feature(stop_layer.GetLayerDefn())
            feature.SetField("stop_id", record["stop_id"])
            feature.SetField("name", record["name"])
            feature.SetField("location_type", record["location_type"])
            feature.SetField("trips_per_day", int(record["trips_per_day"]))
            feature.SetField("merged_children", int(record["merged_children"]))
            feature.SetField("first_departure", _clock(record["first"]) if record["first"] is not None else "")
            feature.SetField("last_departure", _clock(record["last"]) if record["last"] is not None else "")
            geometry = ogr.Geometry(ogr.wkbPoint)
            geometry.AddPoint(record["lon"], record["lat"])
            feature.SetGeometry(geometry)
            stop_layer.CreateFeature(feature)
        target.CommitTransaction()

        route_layer = target.CreateLayer("routes", wgs84, ogr.wkbLineString, options=["SPATIAL_INDEX=YES"])
        for name, kind in (("route_id", ogr.OFTString), ("short_name", ogr.OFTString),
                           ("long_name", ogr.OFTString), ("route_type", ogr.OFTString),
                           ("route_color", ogr.OFTString), ("trips_per_day", ogr.OFTInteger),
                           ("geometry_source", ogr.OFTString)):
            route_layer.CreateField(ogr.FieldDefn(name, kind))
        target.StartTransaction()
        for record in routes:
            if not record["line"] or len(record["line"]) < 2:
                continue
            feature = ogr.Feature(route_layer.GetLayerDefn())
            feature.SetField("route_id", record["route_id"])
            feature.SetField("short_name", record["short_name"])
            feature.SetField("long_name", record["long_name"])
            feature.SetField("route_type", record["route_type"])
            feature.SetField("route_color", record["route_color"])
            feature.SetField("trips_per_day", int(record["trips_per_day"]))
            feature.SetField("geometry_source", record["geometry_source"])
            geometry = ogr.Geometry(ogr.wkbLineString)
            for lon, lat in record["line"]:
                geometry.AddPoint(lon, lat)
            feature.SetGeometry(geometry)
            route_layer.CreateFeature(feature)
        target.CommitTransaction()
    finally:
        del target




def _quantile_breaks(values: list, classes: int) -> list:

    ordered = sorted(values)
    if not ordered:
        return [0.0] * (classes + 1)
    if ordered[0] == ordered[-1]:
        return [float(ordered[0])] * (classes + 1)
    breaks = [float(ordered[0])]
    for i in range(1, classes):
        index = min(len(ordered) - 1, int(round(i * (len(ordered) - 1) / classes)))
        breaks.append(float(ordered[index]))
    breaks.append(float(ordered[-1]))

    for i in range(1, len(breaks)):
        if breaks[i] <= breaks[i - 1]:
            breaks[i] = breaks[i - 1] + 1
    return breaks


def _style_stops(layer, values: list) -> None:
    from qgis.core import QgsGraduatedSymbolRenderer, QgsMarkerSymbol, QgsRendererRange
    from qgis.PyQt.QtGui import QColor

    breaks = _quantile_breaks(values, min(_ROUTE_CLASSES, max(1, len(set(values)))))
    ranges = []
    for i in range(len(breaks) - 1):
        t = i / max(1, len(breaks) - 2)
        color = QColor(int(round(255 + (49 - 255) * t)), int(round(245 + (54 - 245) * t)),
                       int(round(235 + (149 - 235) * t)))
        symbol = QgsMarkerSymbol.createSimple({"color": color.name(), "size": f"{2.0 + 2.0 * t:.2f}",
                                               "outline_color": "#333333", "outline_width": "0.2"})
        label = _tr("{lo} - {hi} trips/day").format(lo=f"{int(breaks[i]):,}", hi=f"{int(breaks[i + 1]):,}")
        ranges.append(QgsRendererRange(breaks[i], breaks[i + 1], symbol, label))
    layer.setRenderer(QgsGraduatedSymbolRenderer("trips_per_day", ranges))


def _style_routes(layer) -> None:







    from qgis.core import QgsLineSymbol, QgsProperty, QgsSingleSymbolRenderer, QgsSymbolLayer

    cases = " ".join(f"WHEN \"route_type\" = '{word}' THEN '{color}'"
                     for word, color in _ROUTE_TYPE_COLORS.items() if word != "other")
    other = _ROUTE_TYPE_COLORS["other"]
    expression = (
        'CASE WHEN "route_color" IS NOT NULL AND "route_color" != \'\' '
        'THEN \'#\' || "route_color" '
        f"ELSE CASE {cases} ELSE '{other}' END END"
    )
    symbol = QgsLineSymbol.createSimple({"line_width": "0.8"})
    symbol.symbolLayer(0).setDataDefinedProperty(
        enum_member(QgsSymbolLayer, "Property", "PropertyStrokeColor"), QgsProperty.fromExpression(expression))
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))




def _stopped() -> dict:
    return tool_error("Stopped before the feed finished loading.", "CANCELLED", "No layer was added.")


def _load_gtfs(args: dict) -> dict:
    refused = _check_args(args)
    if refused:
        return refused
    started = time.monotonic()
    cancel = net.current_cancel_check()
    source_text = str(args["source"]).strip()
    is_url = source_text.lower().startswith(("http://", "https://"))
    tmp_dir = create_managed_temp_dir("gtfs") if is_url else None
    gtfs: _GtfsSource | None = None
    try:
        if is_url:
            if not source_text.lower().startswith("https://"):
                return tool_error("Only an https URL is downloaded.", "INVALID_ARGS",
                                  "an https link, a local zip's path, or a folder.")
            zip_path, refusal = _download(source_text, tmp_dir)
            if refusal:
                return refusal
            try:
                gtfs = _GtfsSource()
                gtfs.open_zip(zip_path)
            except zipfile.BadZipFile:
                return tool_error(f"{source_text} did not download a valid ZIP archive.", "INVALID_ARGS",
                                  hint="gtfs_download_not_zip")
        else:
            gtfs, refusal = _local_source(source_text)
            if refusal:
                return refusal
        missing = [name for name in _REQUIRED if not gtfs.has(name)]
        if missing:
            return tool_error(f"This feed is missing {', '.join(missing)}.", "INVALID_ARGS",
                              hint="gtfs_missing_files", missing=", ".join(missing))
        if not gtfs.has("calendar.txt") and not gtfs.has("calendar_dates.txt"):
            return tool_error("This feed has neither calendar.txt nor calendar_dates.txt.", "INVALID_ARGS",
                              "Without one of them no date can say which trips run.")
        if net.is_cancelled(cancel):
            return _stopped()

        stop_rows = list(gtfs.rows("stops.txt"))
        route_rows = list(gtfs.rows("routes.txt"))
        trip_rows = list(gtfs.rows("trips.txt"))
        calendar_rows = list(gtfs.rows("calendar.txt"))
        calendar_dates_rows = list(gtfs.rows("calendar_dates.txt"))
        freq_rows = list(gtfs.rows("frequencies.txt")) if gtfs.has("frequencies.txt") else []

        date_compact, why, refusal = _resolve_date(args, calendar_rows, calendar_dates_rows)
        if refusal:
            return refusal
        active_services = _active_services(date_compact, calendar_rows, calendar_dates_rows)
        active_trips = [row for row in trip_rows if row.get("service_id") in active_services]
        active_trip_route = {row["trip_id"]: row["route_id"] for row in active_trips
                             if row.get("trip_id") and row.get("route_id")}
        if not active_trip_route:
            return tool_error(
                f"No trip runs on {date_compact[:4]}-{date_compact[4:6]}-{date_compact[6:]} "
                f"({why}).", "INVALID_ARGS",
                hint="gtfs_no_trip_on_date", date=f"{date_compact[:4]}-{date_compact[4:6]}-{date_compact[6:]}")

        if net.is_cancelled(cancel):
            return _stopped()







        used_shape_ids = {row.get("shape_id") for row in trip_rows if (row.get("shape_id") or "").strip()}
        max_shape_rows = limits.current("GTFS_SHAPE_POINTS_MAX_ROWS")
        if gtfs.has("shapes.txt") and used_shape_ids:
            shape_points, shapes_cut, shape_points_kept = _stream_shapes(gtfs, used_shape_ids, cancel, max_shape_rows)
        else:
            shape_points, shapes_cut, shape_points_kept = {}, False, 0

        want_sequence_for = {
            row["trip_id"] for row in active_trips
            if row.get("trip_id") and (row.get("shape_id") or "") not in shape_points
        }
        freq_windows_by_trip = _frequency_windows(freq_rows, active_trip_route)
        freq_trip_ids = set(freq_windows_by_trip)

        if net.is_cancelled(cancel):
            return _stopped()
        max_rows = limits.current("GTFS_STOP_TIMES_MAX_ROWS")
        stream = _stream_stop_times(gtfs, active_trip_route, want_sequence_for, freq_trip_ids, cancel, max_rows)
        frequency_trips_added, freq_route_extra, freq_matched, freq_unmatched = _apply_frequencies(
            freq_windows_by_trip, stream, active_trip_route)

        merge_distance_m = args.get("merge_distance_m")
        stops, merged_away, used_parent_station = _merge_stops(stop_rows, stream, merge_distance_m)
        if not stops:
            return tool_error("No stop has usable coordinates in this feed.", "INVALID_ARGS",
                              "stops.txt needs stop_lat and stop_lon both as numbers.")

        stop_coords = {row["stop_id"]: (float(row["stop_lon"]), float(row["stop_lat"]))
                      for row in stop_rows
                      if row.get("stop_id") and _is_number(row.get("stop_lat")) and _is_number(row.get("stop_lon"))}
        per_route_trips = Counter(active_trip_route.values())
        for route_id, extra in freq_route_extra.items():
            per_route_trips[route_id] += extra
        routes, routes_without_geometry = _route_lines(route_rows, trip_rows, shape_points, stop_coords,
                                                        stream, per_route_trips)

        if net.is_cancelled(cancel):
            return _stopped()

        stem = _safe_filename(str(args.get("layer_name") or "").strip() or "gtfs_feed", "gtfs_feed")
        folder = create_managed_temp_dir("gtfs-output")
        gpkg_path = os.path.join(folder, f"{stem}.gpkg")
        try:
            _write_geopackage(gpkg_path, stops, routes)
        except Exception as exc:  # noqa: BLE001
            remove_tree(folder)
            return tool_error(f"The GeoPackage could not be written: {exc}", "EXECUTION_FAILED",
                              hint="gtfs_write_failed")
        if net.is_cancelled(cancel):
            remove_tree(folder)
            return _stopped()
    except _GtfsEncodingError as exc:
        return tool_error(
            f"{exc.name} is not readable as {' or '.join(exc.tried)}.", "INVALID_ARGS",
            hint="gtfs_encoding_unreadable", file=exc.name, tried=" or ".join(exc.tried))
    finally:
        if gtfs is not None:
            gtfs.close()
        if tmp_dir is not None:
            remove_tree(tmp_dir)

    label = str(args.get("layer_name") or "").strip() or stem
    values = [record["trips_per_day"] for record in stops]

    def _create():
        stop_name = _tr("{name} stops").format(name=label)
        route_name = _tr("{name} routes").format(name=label)
        stop_layer = QgsVectorLayer(f"{gpkg_path}|layername=stops", stop_name, "ogr")
        route_layer = QgsVectorLayer(f"{gpkg_path}|layername=routes", route_name, "ogr")
        if not stop_layer.isValid() or not route_layer.isValid():
            return tool_error("QGIS could not open the GeoPackage this tool just wrote.", "EXECUTION_FAILED",
                              hint="gtfs_geopackage_unreadable")
        _style_stops(stop_layer, values)
        _style_routes(route_layer)
        QgsProject.instance().addMapLayer(stop_layer)
        QgsProject.instance().addMapLayer(route_layer)
        return {"stops_layer_id": stop_layer.id(), "stops_layer_name": stop_layer.name(),
               "routes_layer_id": route_layer.id(), "routes_layer_name": route_layer.name()}

    added = run_on_main_thread(_create, timeout=60)
    if isinstance(added, dict) and added.get("_error") is not None:
        remove_tree(folder)
        return added

    peak_hour, peak_count = (None, 0)
    if stream["hour_tally"]:
        peak_hour, peak_count = stream["hour_tally"].most_common(1)[0]
    result = {
        "output_path": gpkg_path,
        "date": f"{date_compact[:4]}-{date_compact[4:6]}-{date_compact[6:]}", "date_reason": why,
        "stops": len(stops), "stops_before_merge": len(stop_rows), "stops_merged_away": merged_away,
        "merge_method": "parent_station" if used_parent_station else (
            "distance" if args.get("merge_distance_m") else "none"),
        "routes": len(routes), "routes_without_geometry": routes_without_geometry,
        "trips_active": len(active_trip_route), "stop_times_rows_read": stream["rows_read"],
        "stop_times_cut": stream["cut"],
        "shape_points_kept": shape_points_kept, "shapes_cut": shapes_cut,
        "seconds": round(time.monotonic() - started, 1),
    }
    if peak_hour is not None:
        result["peak_hour"] = f"{peak_hour:02d}:00-{(peak_hour + 1) % 24:02d}:00"
        result["peak_hour_departures"] = peak_count
    if freq_rows:
        result["frequency_trips"] = frequency_trips_added
        result["frequency_trips_expanded_from"] = freq_matched
        if freq_unmatched:
            result["frequency_trips_unmatched"] = freq_unmatched
    warnings = []
    if stream["cut"]:
        warnings.append(f"stop_times.txt was cut at {max_rows:,} rows: trip counts may be "
                        "incomplete for stops reached late in the file.")
    if shapes_cut:
        warnings.append(f"shapes.txt was cut at {max_shape_rows:,} points kept: the routes still "
                        "building a shape at that point use their trips' stop sequence instead.")
    if freq_unmatched:
        warnings.append(f"{freq_unmatched} frequencies.txt trip(s) named no usable stop_times.txt "
                        "time and were left out of the trip counts.")
    if warnings:
        result["warning"] = " ".join(warnings)
    result.update(added)
    return result


def _is_number(text) -> bool:
    try:
        float(text)
    except (TypeError, ValueError):
        return False
    return True
