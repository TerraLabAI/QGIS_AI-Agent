# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The date a dataset describes the world, in the catalog's format."""






from __future__ import annotations

import calendar
import re
from datetime import date

LIVE = "live"
_YEAR = re.compile(r"^(\d{4})$")
_SPAN = re.compile(r"^(\d{4})-(\d{4})$")
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _year_ok(year: int) -> bool:
    return 1800 <= year <= 2200


def parse(text) -> tuple[str, date, date] | None:
    """``(kind, first day, last day)`` for a well formed value, ``None`` otherwise."""




    value = str(text or "").strip().lower()
    if not value:
        return None
    if value == LIVE:
        today = date.today()
        return (LIVE, today, today)
    try:
        match = _YEAR.match(value)
        if match:
            year = int(match.group(1))
            return ("year", date(year, 1, 1), date(year, 12, 31)) if _year_ok(year) else None
        match = _SPAN.match(value)
        if match:
            first, last = int(match.group(1)), int(match.group(2))
            if _year_ok(first) and _year_ok(last) and first <= last:
                return ("span", date(first, 1, 1), date(last, 12, 31))
            return None
        match = _MONTH.match(value)
        if match:
            year, month = int(match.group(1)), int(match.group(2))
            if not _year_ok(year):
                return None
            return ("month", date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1]))
        match = _DAY.match(value)
        if match:
            day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            return ("day", day, day) if _year_ok(day.year) else None
    except ValueError:
        return None
    return None


def clean(text) -> str:
    """The value as the catalog writes it when it parses, else an empty string."""
    value = str(text or "").strip().lower()
    return value if parse(value) is not None else ""


def name_label(text) -> str:
    """What a layer name carries in parentheses: the year or the span, else nothing."""



    parsed = parse(text)
    if parsed is None or parsed[0] == LIVE:
        return ""
    kind, first, last = parsed
    if kind == "span":
        return f"{first.year}-{last.year}" if first.year != last.year else str(first.year)
    return str(first.year)


def from_range(start, end=None) -> str:
    """The catalog value for an acquisition read at load time (a STAC item, an Earth Engine image)."""




    def _day(value):
        text = str(value or "").strip()[:10]
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None

    first = _day(start)
    last = _day(end) or first
    if first is None:
        first = last
    if first is None or not (_year_ok(first.year) and _year_ok(last.year)):
        return ""
    if last < first:
        first, last = last, first
    if first == last:
        return first.isoformat()
    if (first.year, first.month) == (last.year, last.month):
        return f"{first.year:04d}-{first.month:02d}"
    if first.year == last.year:
        return f"{first.year:04d}"
    return f"{first.year:04d}-{last.year:04d}"
