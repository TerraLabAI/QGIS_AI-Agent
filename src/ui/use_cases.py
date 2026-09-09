# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The examples: the real GIS jobs people hand to the agent."""






























from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass

from .shared import tr




GROUP_KEYS = ("explore", "map", "analyse", "terrain", "share")




_MAX_CASES = 60
_MAX_GROUPS = 24
_MAX_ID, _MAX_NAME = 64, 64
_MAX_TITLE, _MAX_OUTCOME, _MAX_PROMPT = 80, 120, 600
_MAX_STEP, _MAX_STEPS = 100, 8
_MAX_ITEM, _MAX_ITEMS = 60, 8
_MAX_CAVEAT = 240

_served: list = []

_served_groups: list = []
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True)
class UseCase:
    """One job, and everything the library shows about it."""










    slug: str
    group: str
    glyph: str
    title: str
    outcome: str
    prompt: str
    steps: tuple = ()
    uses: tuple = ()
    connectors: tuple = ()
    scene: str = ""
    caveat: str = ""


def _builtin_groups() -> list:
    """(key, label) for the five built-in groups, in display order."""
    return [
        ("explore", tr("Explore")),
        ("map", tr("Map")),
        ("analyse", tr("Analyse")),
        ("terrain", tr("Terrain and imagery")),
        ("share", tr("Report and share")),
    ]


def _group_rows() -> list:
    """The group dicts the rail is built from: served, else cached, else built in."""






    global _served_groups
    if not _served_groups:
        try:
            from ..core.settings import Settings

            _served_groups = _clean_groups(Settings().known_use_case_groups)
        except Exception:  # noqa: BLE001 - no settings backend is the built-ins
            _served_groups = []
    rows = [dict(g) for g in _served_groups] or [
        {"key": key, "label": label, "glyph": "", "accent": ""} for key, label in _builtin_groups()]
    known = {row["key"] for row in rows}
    builtin = dict(_builtin_groups())
    for case in use_cases():
        if case.group not in known:
            known.add(case.group)
            rows.append({"key": case.group, "label": builtin.get(case.group, case.group),
                         "glyph": "", "accent": ""})
    return rows


def use_case_groups() -> list:
    """(key, label) for the groups the rail shows, in display order."""
    return [(row["key"], row["label"]) for row in _group_rows()]


def group_glyph(key: str) -> str:
    """The rail glyph the server named for a group, or "" for the built-in one."""
    for row in _group_rows():
        if row["key"] == key:
            return row["glyph"]
    return ""


def group_accent(key: str) -> str:
    """The hue the server named for a group, or "" for the built-in one."""
    for row in _group_rows():
        if row["key"] == key:
            return row["accent"]
    return ""


def haystack(case: UseCase) -> str:
    """Everything one row says, plus what its tags say about it, lowered."""






    return " ".join((
        case.title,
        case.outcome,
        case.prompt,
        case.slug.replace("-", " "),
        dict(use_case_groups()).get(case.group, case.group),
        " ".join(case.steps),
        " ".join(case.uses),
        " ".join(c.replace("_", " ") for c in case.connectors),
    )).lower()


def match_cases(cases, query: str) -> list:
    """The cases a search field asks for."""





    words = [word for word in str(query or "").strip().lower().split() if word]
    if not words:
        return list(cases)


    patterns = [re.compile(r"(?<![a-z0-9])" + re.escape(word)) for word in words]
    return [case for case in cases
            if all(pattern.search(haystack(case)) for pattern in patterns)]


def _text(value, cap: int) -> str:
    return str(value).strip()[:cap] if isinstance(value, str) else ""


def _strings(value, cap: int) -> tuple:
    if not isinstance(value, list):
        return ()
    return tuple(s for s in (_text(v, cap) for v in value[:_MAX_ITEMS]) if s)


def _clean_case(row):
    """A served row as a ``UseCase``, or None for one the library cannot show."""
    if not isinstance(row, dict):
        return None
    slug = _text(row.get("id"), _MAX_ID)
    group = _text(row.get("group"), _MAX_NAME)
    if not slug or not group:
        return None
    return UseCase(
        slug, group, _text(row.get("glyph"), _MAX_NAME),
        _text(row.get("title"), _MAX_TITLE),
        _text(row.get("outcome"), _MAX_OUTCOME),
        _text(row.get("prompt"), _MAX_PROMPT),
        steps=_strings(row.get("steps"), _MAX_STEP)[:_MAX_STEPS],
        uses=_strings(row.get("uses"), _MAX_ITEM),
        connectors=_strings(row.get("connectors"), _MAX_ITEM),
        scene=_text(row.get("scene"), _MAX_NAME),
        caveat=_text(row.get("caveat"), _MAX_CAVEAT),
    )


def _clean_cases(rows) -> list:
    cleaned, seen = [], set()
    for row in (rows if isinstance(rows, list) else [])[:_MAX_CASES]:
        case = _clean_case(row)
        if case is not None and case.slug not in seen and case.title and case.prompt:
            seen.add(case.slug)
            cleaned.append(case)
    return cleaned


def _clean_groups(rows) -> list:
    cleaned, seen = [], set()
    for row in (rows if isinstance(rows, list) else [])[:_MAX_GROUPS]:
        if not isinstance(row, dict):
            continue
        key = _text(row.get("id"), _MAX_NAME)
        if not key or key in seen:
            continue
        seen.add(key)
        accent = _text(row.get("accent"), 7)
        cleaned.append({
            "key": key,
            "label": _text(row.get("label"), _MAX_TITLE) or key,
            "glyph": _text(row.get("glyph"), _MAX_NAME),
            "accent": accent if _HEX.match(accent) else "",
        })
    return cleaned


def set_served_groups(rows) -> None:
    """Keep the group list the server sent, and remember it for the next start."""




    global _served_groups
    cleaned = _clean_groups(rows)
    if not cleaned or cleaned == _served_groups:
        return
    _served_groups = cleaned
    with contextlib.suppress(Exception):
        from ..core.settings import Settings

        Settings().known_use_case_groups = [r for r in rows if isinstance(r, dict)][:_MAX_GROUPS]


def set_served_cases(rows) -> None:
    """Keep the library the server sent, and remember it for the next start."""




    global _served
    cleaned = _clean_cases(rows)
    if not cleaned or cleaned == _served:
        return
    _served = cleaned

    with contextlib.suppress(Exception):
        from ..core.settings import Settings

        Settings().known_use_cases = [r for r in rows if isinstance(r, dict)][:_MAX_CASES]


def _seed() -> list:
    return [
        UseCase(
            "osm-extract", "explore", "download",
            tr("Pull OpenStreetMap data for an area"),
            tr("Schools, parks and stops for one district, as three layers."),
            tr("Download from OpenStreetMap every school, park and bus stop in one "
               "district. Work over the area my canvas is on, or pick a district of a "
               "well mapped city and say which. Put them in three layers, the parks as "
               "polygons, style each one distinctly, and tell me how many features each "
               "holds. Query the three one at a time."),
            steps=(
                tr("Takes the area from the canvas, or picks one"),
                tr("Sends three Overpass queries in turn: schools, parks, stops"),
                tr("Loads each answer as its own layer"),
                tr("Keeps the parks as polygons, the rest as points"),
                tr("Reports the feature count of the three"),
            ),
            uses=("OpenStreetMap", "Overpass API", "Nominatim"),
            connectors=("openstreetmap",), scene="fetch",
        ),
        UseCase(
            "print-layout", "map", "layout",
            tr("Build a print map and export it"),
            tr("An A4 sheet with title, legend, scale bar and north arrow."),
            tr("Build an A4 landscape layout of the current view with a title, a legend, "
               "a scale bar in metres, a north arrow and a credits line naming the data "
               "sources, then export it to PDF at 300 dpi. If the canvas is empty, add a "
               "basemap over a place you pick first, so the sheet has a map on it."),
            steps=(
                tr("Adds a basemap when the canvas has nothing on it"),
                tr("Creates an A4 landscape layout with a map frame"),
                tr("Adds title, legend, scale bar, north arrow, credits"),
                tr("Exports the sheet to PDF at 300 dpi"),
            ),
            uses=("Print layout",),
            connectors=("openstreetmap",), scene="layout",
        ),
        UseCase(
            "buffer-count", "analyse", "buffer",
            tr("Count what falls within a distance"),
            tr("A buffer band, and what sits inside it, counted."),
            tr("Buffer a line layer by 100 m in a metric CRS, count how many points fall "
               "inside each buffer, and give me the ten with the most, as a table I can "
               "read. Use my own layers when the project holds a line and a point layer; "
               "otherwise download the roads and the shops of a district you pick, and "
               "say which."),
            steps=(
                tr("Uses my lines and points, or downloads both"),
                tr("Reprojects to a metric CRS before measuring"),
                tr("Buffers the lines by 100 m"),
                tr("Counts the points inside each buffer"),
                tr("Returns the ten highest as a readable table"),
            ),
            uses=("Processing",),
            connectors=("openstreetmap",), scene="buffer",
        ),
    ]


def use_cases() -> list:
    """The served library: this session's, else the cached one, else the seed."""
    global _served
    if not _served:
        try:
            from ..core.settings import Settings

            _served = _clean_cases(Settings().known_use_cases)
        except Exception:  # noqa: BLE001 - no settings backend is the seed
            _served = []
    return list(_served) or _seed()
