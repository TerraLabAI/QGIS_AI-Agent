# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later









































from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass

from .shared import tr





_MAX_CASES = 300
_MAX_GROUPS = 24
_MAX_ID, _MAX_NAME = 64, 64
_MAX_TITLE, _MAX_OUTCOME, _MAX_PROMPT = 80, 120, 600
_MAX_STEP, _MAX_STEPS = 100, 8
_MAX_ITEM, _MAX_ITEMS = 60, 8
_MAX_CAVEAT = 240
_MAX_URL = 512
_MAX_SAMPLES, _MAX_LICENCE = 4, 80

_served: list = []

_served_groups: list = []
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True)
class Sample:


    name: str
    url: str
    licence: str = ""


@dataclass(frozen=True)
class UseCase:











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
    image: str = ""
    samples: tuple = ()
    listed: bool = True


def _builtin_groups() -> list:

    return [
        ("explore", tr("Explore")),
        ("map", tr("Map")),
        ("analyse", tr("Analyse")),
        ("terrain", tr("Terrain and imagery")),
        ("share", tr("Report and share")),
    ]


def _group_rows() -> list:







    global _served_groups
    if not _served_groups:
        try:
            from ..core.settings import Settings

            _served_groups = _clean_groups(Settings().known_use_case_groups)
        except Exception:  # noqa: BLE001
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

    return [(row["key"], row["label"]) for row in _group_rows()]


def use_case_group_looks() -> dict:

    return {row["key"]: (row.get("glyph") or "", row.get("accent") or "") for row in _group_rows()}


def haystack(case: UseCase) -> str:







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


def _https(value) -> str:





    url = _text(value, _MAX_URL)
    if not url.lower().startswith("https://"):
        return ""
    try:
        from urllib.parse import urlsplit

        parsed = urlsplit(url)
    except ValueError:
        return ""
    if not parsed.hostname or parsed.username or parsed.password:
        return ""
    return url


def _sample_name(value) -> str:


    if isinstance(value, dict):
        value = value.get("en") or next((v for v in value.values() if isinstance(v, str) and v), "")
    return _text(value, _MAX_ITEM)


def _samples(value) -> tuple:
    if not isinstance(value, list):
        return ()
    out = []
    for row in value[:_MAX_SAMPLES]:
        if not isinstance(row, dict):
            continue
        url = _https(row.get("url"))
        name = _sample_name(row.get("name")) or (url.rsplit("/", 1)[-1] if url else "")
        if url and name:
            out.append(Sample(name, url, _text(row.get("licence"), _MAX_LICENCE)))
    return tuple(out)


def _clean_case(row):

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
        image=_https(row.get("image")),
        samples=_samples(row.get("samples")),
        listed=row.get("listed") is not False,
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





    global _served_groups
    cleaned = _clean_groups(rows)
    if not cleaned or cleaned == _served_groups:
        return
    _served_groups = cleaned
    with contextlib.suppress(Exception):
        from ..core.settings import Settings

        Settings().known_use_case_groups = [r for r in rows if isinstance(r, dict)][:_MAX_GROUPS]


def set_served_cases(rows) -> None:




    global _served
    cleaned = _clean_cases(rows)
    if not cleaned or cleaned == _served:
        return
    _served = cleaned

    with contextlib.suppress(Exception):
        from ..core.settings import Settings

        Settings().known_use_cases = [r for r in rows if isinstance(r, dict)][:_MAX_CASES]


def use_cases() -> list:

    global _served
    if not _served:
        try:
            from ..core.settings import Settings

            _served = _clean_cases(Settings().known_use_cases)
        except Exception:  # noqa: BLE001
            _served = []
    return list(_served)
