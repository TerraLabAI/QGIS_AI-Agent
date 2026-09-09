# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Ranking for the ``@`` picker."""
























from __future__ import annotations

import re

__all__ = ["group_mentions", "layer_rows", "mentions_for", "plugin_rows", "rank_mentions",
           "score_mention", "sheet_rows", "short_detail", "source_rows"]



_SEPARATORS = re.compile(r"[^0-9A-Za-z]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

NO_MATCH = 99


def _words(text: str) -> list[str]:
    out: list[str] = []
    for chunk in _SEPARATORS.split(text or ""):
        if chunk:
            out.extend(part for part in _CAMEL.split(chunk) if part)
    return out


def _initials(text: str) -> str:
    return "".join(word[0] for word in _words(text)).lower()


def score_mention(query: str, label: str, group: str = "") -> tuple[int, int, int]:
    """``(tier, where, inexact)`` for one candidate; tier ``NO_MATCH`` to drop it."""





    q = (query or "").strip().lower()
    name = (label or "").lower()
    inexact = 0 if name == q else 1
    if not q:
        return (0, 0, 1)
    if not name:
        return (NO_MATCH, 0, 1)

    if name.startswith(q):
        return (0, 0, inexact)



    at = 0
    for word in _words(label):
        at = name.find(word.lower(), at)
        if at < 0:
            at = 0
        if word.lower().startswith(q):
            return (1, at, inexact)
        at += len(word)

    if len(q) >= 2:
        acronym = _initials(label)
        if acronym.startswith(q):
            return (2, 0, inexact)

        for word in _words(group):
            if word.lower().startswith(q):
                return (3, 0, inexact)

    if len(q) >= 3:
        found = name.find(q)
        if found >= 0:
            return (4, found, inexact)

    return (NO_MATCH, 0, inexact)


def rank_mentions(query: str, items) -> list[dict]:
    """The candidates that match ``query``, best first, the rest dropped."""





    scored = []
    try:
        candidates = list(items or [])[:1000]
    except TypeError:
        candidates = []
    for index, item in enumerate(candidates):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("value") or "")
        tier, where, inexact = score_mention(query, label, str(item.get("group") or ""))
        if tier == NO_MATCH:
            continue
        scored.append(((tier, where, inexact, index), item))
    scored.sort(key=lambda pair: pair[0])
    return [item for _key, item in scored]


def group_mentions(items, sources_label: str = "") -> list[dict]:
    """The candidates with a header row before each layer tree group."""









    layers = [i for i in items or [] if isinstance(i, dict) and i.get("kind") == "layer"]
    others = [i for i in items or [] if isinstance(i, dict) and i.get("kind") != "layer"]



    root: list[dict] = []
    grouped: dict[str, list[dict]] = {}
    for item in layers:
        group = str(item.get("group") or "")
        if not group:
            root.append(item)
        else:
            grouped.setdefault(group, []).append(item)
    out: list[dict] = list(root)
    for group, rows in grouped.items():
        out.append({"kind": "header", "label": group})
        out.extend(rows)
    if others and sources_label and layers:
        out.append({"kind": "header", "label": sources_label})
    out.extend(others)
    return out


def mentions_for(word: str, items, sources_label: str = "") -> list[dict]:
    """What the sheet should list for ``@word``: the whole decision."""








    ranked = rank_mentions(word, items)
    if word:
        return ranked
    return group_mentions(ranked, sources_label)





FILES = "files"
SOURCE = "source"
PLUGIN = "plugin"
LAYER = "layer"




_DETAIL_WORDS = 6
_DETAIL_CHARS = 44
_SENTENCE_END = re.compile(r"(?<=[a-z0-9\)])[.;] ")



_DANGLING = frozenset((
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "in", "into",
    "is", "its", "of", "on", "or", "over", "such", "that", "the", "their", "then",
    "this", "to", "which", "with",
))


def short_detail(text: str, words: int = _DETAIL_WORDS, chars: int = _DETAIL_CHARS) -> str:
    """The handful of words a one-line row can hold, cut at a word."""







    words = max(1, int(words or _DETAIL_WORDS))
    chars = max(8, int(chars or _DETAIL_CHARS))
    line = " ".join(str(text or "").split())
    if not line:
        return ""
    line = _SENTENCE_END.split(line)[0]
    for dash in (" - ", " – ", " — "):
        line = line.split(dash)[0]
    parts = line.split(" ")
    cut = len(parts) > words
    out = " ".join(parts[:words]).rstrip(" ,;:.")
    while len(out) > chars and " " in out:
        cut = True
        out = out.rsplit(" ", 1)[0].rstrip(" ,;:.")
    if cut and "," in out:
        out = out.rsplit(",", 1)[0].rstrip(" ,;:.")
    while cut and " " in out and out.rsplit(" ", 1)[1].lower() in _DANGLING:
        out = out.rsplit(" ", 1)[0].rstrip(" ,;:.")
    return out


def source_rows(connectors) -> list[dict]:
    """The connectors as ``@`` rows: the popular ones first, then the rest."""













    rows = []
    for row in sorted((r for r in connectors or [] if isinstance(r, dict)),
                      key=lambda r: (not r.get("popular"), str(r.get("name") or "").lower())):
        key = str(row.get("id") or "")
        if not key:
            continue
        about = " ".join(str(part) for part in (
            row.get("tagline") or "", key.replace("_", " "),
            " ".join(str(c) for c in (row.get("also_covers") or [])), row.get("coverage") or "",
        ) if part)
        rows.append({"kind": SOURCE, "glyph": str(row.get("glyph") or "globe"),
                     "label": str(row.get("name") or key)[:160], "value": key[:160],
                     "group": about[:240],
                     "detail": short_detail(row.get("tagline") or row.get("licence") or "")})
    return rows


def plugin_rows(plugins) -> list[dict]:
    """The QGIS plugins of this machine as ``@`` rows, alphabetical."""






    rows = []
    for row in sorted((r for r in plugins or [] if isinstance(r, dict)),
                      key=lambda r: str(r.get("name") or r.get("folder") or "").lower()):
        folder = str(row.get("folder") or "")
        if not folder:
            continue
        rows.append({"kind": PLUGIN, "glyph": str(row.get("glyph") or "package"),
                     "label": str(row.get("name") or folder)[:160], "value": folder[:160],
                     "detail": short_detail(row.get("summary") or row.get("description") or ""),
                     "icon": str(row.get("icon") or "")})
    return rows


def layer_rows(word: str, layers, visible_ids=()) -> list[dict]:
    """The project's own layers for the ``+`` sheet: visible first, flat."""







    rows = [dict(i, depth=0) for i in layers or []
            if isinstance(i, dict) and i.get("kind") == LAYER]
    if word:
        return rank_mentions(word, rows)
    seen = {str(i) for i in visible_ids or [] if str(i)}
    if seen:
        rows.sort(key=lambda r: str(r.get("value") or "") not in seen)
    return rows


def sheet_rows(word: str, items, connectors=None, plugins=None, labels=None) -> list[dict]:
    """Everything the ``@`` sheet lists for ``@word``, in order."""












    try:
        provided = [i for i in list(items or [])[:1000] if isinstance(i, dict)]
    except TypeError:
        provided = []
    sources = (source_rows(connectors) if connectors
               else [i for i in provided if i.get("kind") == SOURCE])
    installed = (plugin_rows(plugins) if plugins
                 else [i for i in provided if i.get("kind") == PLUGIN])
    if word:
        sources = rank_mentions(word, sources)
        installed = rank_mentions(word, installed)
    labels = labels or {}
    out: list[dict] = []
    for key, rows in (("sources", sources), ("plugins", installed)):
        if not rows:
            continue
        heading = str(labels.get(key) or "")
        if heading:
            out.append({"kind": "header", "label": heading})
        out.extend(rows[:500])
    return out
