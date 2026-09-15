# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pure layer-of-interest ranking, no PyQGIS import so it stays unit-testable with plain python3."""





from __future__ import annotations

import functools
import math
import unicodedata
from typing import Any


_GENERIC_FIELDS = frozenset({"name", "type", "code", "date", "area", "value", "geom", "geometry", "layer",
                             "label", "class", "count", "length", "comment", "source", "status", "height"})
_OCTANTS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def normalized(text: Any) -> str:
    """Case folded, no accents or other marks, every run of anything but a letter or digit of any script one space."""
    decomposed = unicodedata.normalize("NFKD", str(text or "").casefold())
    kept = "".join(ch if unicodedata.category(ch)[0] in "LMN" else " "
                   for ch in decomposed if not unicodedata.combining(ch))

    return unicodedata.normalize("NFC", " ".join(kept.split()))


@functools.lru_cache(maxsize=4)
def _said(text: str) -> str:
    """The message normalized once: the context asks about it for every layer, on the main thread at Send, and a 60 000 character message over 200."""

    return normalized(text)


def _extent(text: str) -> float:
    """How much a name says: one per character, two and a half for a wide one (an ideograph, a kana, a Hangul syllable), so a two-character word."""


    return sum(2.5 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _carries_on(ch: str) -> bool:
    """A character that continues a word: a letter with case, or a digit."""
    return ch.isdigit() or unicodedata.category(ch) in ("Ll", "Lu", "Lt")


def _mentions(said: str, needle: str) -> bool:
    """Whether `said` holds `needle` as words, in any script."""






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
    """The positions of `names` a message refers to: by the whole name, or by a word only one name has."""





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


def fields_named_in(text: Any, fields: list[str]) -> bool:
    """Whether a message names one of these fields (a generic or short field name never counts)."""
    said = _said(str(text or ""))
    for field in fields:
        word = normalized(field)
        if _extent(word) >= 4 and word not in _GENERIC_FIELDS and _mentions(said, word):
            return True
    return False


def view_of(extent: dict | None, view: dict | None) -> str:
    """"in", "part" or "out": where a layer's extent sits against the view, both in one CRS. "" unknown."""
    if not extent or not view:
        return ""
    try:
        if not _intersects(extent, view):
            return "out"
        inside = (extent["xmin"] >= view["xmin"] and extent["xmax"] <= view["xmax"]
                  and extent["ymin"] >= view["ymin"] and extent["ymax"] <= view["ymax"])
    except (KeyError, TypeError):
        return ""
    return "in" if inside else "part"


def offset_from_view(view_box: list | None, layer_box: list | None) -> tuple[float, str] | None:
    """(km, compass point) from the centre of the view to the centre of a layer, both [w, s, e, n] degrees."""
    if not view_box or not layer_box or len(view_box) != 4 or len(layer_box) != 4:
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
        return not (
            extent["xmax"] < rect["xmin"]
            or extent["xmin"] > rect["xmax"]
            or extent["ymax"] < rect["ymin"]
            or extent["ymin"] > rect["ymax"]
        )
    except (KeyError, TypeError):
        return False


def _rank_key(item: dict[str, Any], active_id: str | None, extent_rect: dict | None) -> int:


    if item.get("named"):
        return -2
    if item.get("selected_count"):
        return 0
    if active_id is not None and item.get("id") == active_id:
        return 1
    if item.get("by_agent"):
        return 2
    visible = bool(item.get("visible"))
    if visible and extent_rect is not None and _intersects(item.get("extent"), extent_rect):
        return 3
    if visible:
        return 4
    return 5


def rank_layers(items: list[dict], active_id: str | None, extent_rect: dict | None) -> list[dict]:
    """Sort plain layer dicts (`id`, `visible`, `selected_count`, `extent`, optional `named` and `by_agent`) by interest."""





    return sorted(items, key=lambda item: _rank_key(item, active_id, extent_rect))


def detailed_ids(ranked: list[dict], active_id: str | None, most: int, least: int) -> list[str]:
    """The layers whose schema, style and bands travel: the ones the message is about."""





    about = [item["id"] for item in ranked
             if item.get("named") or item.get("selected_count") or item.get("by_agent")
             or (active_id is not None and item.get("id") == active_id)][:max(0, most)]
    for item in ranked:
        if len(about) >= max(0, least):
            break
        if item["id"] not in about:
            about.append(item["id"])
    return about
