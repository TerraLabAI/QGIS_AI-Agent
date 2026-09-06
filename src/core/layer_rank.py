# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

import functools
import math
import unicodedata
from typing import Any


FLOOR_ORDER = ("named",)
_OCTANTS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def normalized(text: Any) -> str:

    decomposed = unicodedata.normalize("NFKD", str(text or "").casefold())
    kept = "".join(ch if unicodedata.category(ch)[0] in "LMN" else " "
                   for ch in decomposed if not unicodedata.combining(ch))

    return unicodedata.normalize("NFC", " ".join(kept.split()))


@functools.lru_cache(maxsize=4)
def _said(text: str) -> str:


    return normalized(text)


def _extent(text: str) -> float:



    return sum(2.5 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _carries_on(ch: str) -> bool:

    return ch.isdigit() or unicodedata.category(ch) in ("Ll", "Lu", "Lt")


def _mentions(said: str, needle: str) -> bool:







    if not needle:
        return False
    open_left, open_right = not _carries_on(needle[0]), not _carries_on(needle[-1])
    start = said.find(needle)
    while start >= 0:
        end = start + len(needle)
        if ((open_left or start == 0 or not _carries_on(said[start - 1]))
                and (open_right or end == len(said) or not _carries_on(said[end]))):
            return True
        start = said.find(needle, start + 1)
    return False


def named_in(text: Any, names: list[str]) -> set[int]:






    said = _said(str(text or ""))
    if not said:
        return set()
    found: set[int] = set()
    words_of: dict[str, set[int]] = {}
    for index, name in enumerate(names):
        whole = normalized(name)
        if _extent(whole) >= 3 and _mentions(said, whole):
            found.add(index)
        for word in set(whole.split()):
            if _extent(word) >= 5 and not word.isdigit():
                words_of.setdefault(word, set()).add(index)
    for word, owners in words_of.items():
        if len(owners) == 1 and _mentions(said, word):
            found |= owners
    return found


def fields_named_in(text: Any, fields: list[str], generic: frozenset | None = None) -> bool:



    if generic is None:
        return False
    said = _said(str(text or ""))
    for field in fields:
        word = normalized(field)
        if _extent(word) >= 4 and word not in generic and _mentions(said, word):
            return True
    return False


def view_of(extent: dict | None, view: dict | None) -> str:

    if not extent or not view:
        return ""
    try:
        if not all(math.isfinite(box[key]) for box in (extent, view)
                   for key in ("xmin", "ymin", "xmax", "ymax")):
            return ""
        if not _intersects(extent, view):
            return "out"
        inside = (extent["xmin"] >= view["xmin"] and extent["xmax"] <= view["xmax"]
                  and extent["ymin"] >= view["ymin"] and extent["ymax"] <= view["ymax"])
    except (KeyError, TypeError):
        return ""
    return "in" if inside else "part"


def offset_from_view(view_box: list | None, layer_box: list | None) -> tuple[float, str] | None:

    if not view_box or not layer_box or len(view_box) != 4 or len(layer_box) != 4:
        return None
    try:
        if not all(math.isfinite(value) for box in (view_box, layer_box) for value in box):
            return None
        if any(abs(box[index]) > 90 for box in (view_box, layer_box) for index in (1, 3)):
            return None
    except TypeError:
        return None
    lon1, lat1 = (view_box[0] + view_box[2]) / 2, (view_box[1] + view_box[3]) / 2
    lon2, lat2 = (layer_box[0] + layer_box[2]) / 2, (layer_box[1] + layer_box[3]) / 2
    p1, p2, dlon = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    km = 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(a)))
    bearing = math.degrees(math.atan2(math.sin(dlon) * math.cos(p2),
                                      math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlon)))
    point = _OCTANTS[int(((bearing % 360) + 22.5) // 45) % 8]
    return (round(km, 1) if km < 10 else float(round(km))), point


def _intersects(extent: dict | None, rect: dict | None) -> bool:
    if not extent or not rect:
        return False
    try:
        if not all(math.isfinite(box[key]) for box in (extent, rect)
                   for key in ("xmin", "ymin", "xmax", "ymax")):
            return False
        return not (
            extent["xmax"] < rect["xmin"]
            or extent["xmin"] > rect["xmax"]
            or extent["ymax"] < rect["ymin"]
            or extent["ymin"] > rect["ymax"]
        )
    except (KeyError, TypeError):
        return False


def _signals(item: dict[str, Any], active_id: str | None, extent_rect: dict | None) -> dict[str, bool]:
    visible = bool(item.get("visible"))
    return {
        "named": bool(item.get("named")),
        "selected": bool(item.get("selected_count")),
        "active": active_id is not None and item.get("id") == active_id,
        "by_agent": bool(item.get("by_agent")),
        "visible_in_view": visible and extent_rect is not None and _intersects(item.get("extent"), extent_rect),
        "visible": visible,
    }


def rank_layers(items: list[dict], active_id: str | None, extent_rect: dict | None,
                order: tuple | list = FLOOR_ORDER) -> list[dict]:






    order = tuple(order) or FLOOR_ORDER

    def key(item: dict) -> int:
        shown = _signals(item, active_id, extent_rect)
        return next((place for place, signal in enumerate(order) if shown.get(signal)), len(order))

    return sorted(items, key=key)


def detailed_ids(ranked: list[dict], active_id: str | None, most: int, least: int) -> list[str]:






    most = max(0, most)
    least = max(0, min(least, most))
    about = [item["id"] for item in ranked
             if item.get("named") or item.get("selected_count") or item.get("by_agent")
             or (active_id is not None and item.get("id") == active_id)][:max(0, most)]
    for item in ranked:
        if len(about) >= max(0, least):
            break
        if item["id"] not in about:
            about.append(item["id"])
    return about
