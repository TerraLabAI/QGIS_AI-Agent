# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later













from __future__ import annotations

import re
from html.parser import HTMLParser


_NULL_TOKENS = frozenset({"", "<null>", "null", "&lt;null&gt;"})

MIN_PAIRS = 2
_INT_RE = re.compile(r"^-?(?:0|[1-9]\d{0,17})$")
_FLOAT_RE = re.compile(r"^-?(?:\d+\.\d*|\.\d+|\d+(?:\.\d*)?[eE][-+]?\d+)$")


class _TableReader(HTMLParser):


    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[tuple[str, str]]]] = []
        self._tables: list[list] = []
        self._rows: list[list] = []
        self._cells: list[list] = []
        self.outside: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._tables.append([])
        elif tag == "tr" and self._tables:
            self._rows.append([])
        elif tag in ("td", "th") and self._rows:
            self._cells.append([tag, []])
        elif tag == "br" and self._cells:
            self._cells[-1][1].append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cells:
            kind, parts = self._cells.pop()
            if self._rows:
                self._rows[-1].append((kind, " ".join("".join(parts).split())))
        elif tag == "tr" and self._rows:
            row = self._rows.pop()
            if self._tables:
                self._tables[-1].append(row)
        elif tag == "table" and self._tables:
            self.tables.append(self._tables.pop())

    def handle_data(self, data):
        if self._cells:
            self._cells[-1][1].append(data)
        elif not self._rows and data.strip():
            self.outside.append(data.strip())


def _pairs_of(rows: list) -> tuple[list[tuple[str, str]], bool]:

    cells = [row for row in rows if row]
    if (len(cells) == 2 and len(cells[0]) == len(cells[1]) >= 2
            and all(kind == "th" for kind, _ in cells[0]) and not all(kind == "th" for kind, _ in cells[1])):

        return [(name, value) for (_k, name), (_v, value) in zip(cells[0], cells[1]) if name], False
    pairs, other = [], False
    for row in cells:
        if len(row) == 2 and row[0][1]:
            pairs.append((row[0][1], row[1][1]))
        elif len(row) >= 3:
            other = True

    return pairs, other


def parse(html) -> tuple[list[tuple[str, str]], bool]:







    text = str(html or "")
    if "<" not in text:
        return [], bool(text.strip())
    reader = _TableReader()
    try:
        reader.feed(text)
        reader.close()
    except Exception:  # noqa: BLE001
        return [], True
    pairs: list[tuple[str, str]] = []
    leftover = bool(reader.outside)
    for rows in reader.tables:
        found, other = _pairs_of(rows)
        pairs.extend(found)
        leftover = leftover or other
    if len(pairs) < MIN_PAIRS:
        return [], bool(text.strip())
    return pairs, leftover


def null_value(value: str) -> bool:
    return value.strip().lower() in _NULL_TOKENS


class Columns:


    def __init__(self, taken: set | None = None):

        self._taken = taken if taken is not None else set()
        self.names: dict[str, str] = {}
        self._kind: dict[str, str] = {}
        self._valued: set[str] = set()
        self.tabular = 0
        self.leftover = 0
        self.seen = 0

    def column(self, key: str) -> str:
        name = self.names.get(key)
        if name is None:
            base = " ".join(key.split())[:60] or "field"
            name, number = base, 2
            while name.casefold() in self._taken:
                name, number = f"{base}_{number}", number + 1
            self._taken.add(name.casefold())
            self.names[key] = name
            self._kind[name] = "int"
        return name

    def read(self, html) -> dict:

        if html is None or not str(html).strip():
            return {}
        self.seen += 1
        pairs, leftover = parse(html)
        if leftover:
            self.leftover += 1
        if not pairs:
            return {}
        self.tabular += 1
        row = {}
        for key, value in pairs:
            name = self.column(key)
            if null_value(value):
                row.setdefault(name, None)
                continue
            row[name] = value
            self._valued.add(name)
            kind = self._kind[name]
            if kind == "int" and not _INT_RE.match(value):
                kind = "float"
            if kind == "float" and not (_INT_RE.match(value) or _FLOAT_RE.match(value)):
                kind = "text"
            self._kind[name] = kind
        return row

    def kind(self, name: str) -> str:
        return self._kind.get(name, "text") if name in self._valued else "text"

    def worth_it(self) -> bool:

        return self.tabular > 0 and self.tabular * 2 >= self.seen

    def keeps_raw(self) -> bool:

        return self.leftover > 0 or self.tabular < self.seen
