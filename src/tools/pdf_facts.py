# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


















from __future__ import annotations

import os
import re
import zlib


_MM_PER_POINT = 25.4 / 72.0


_MAX_PAGES = 24
_MAX_OBJECTS = 20_000
_MAX_STREAM_BYTES = 40_000_000
_MAX_FORM_DEPTH = 6
_MAX_NESTING = 32

_OBJ = re.compile(rb"(?<![0-9])(\d{1,8})\s+(\d{1,5})\s+obj\b")

_PAINT = re.compile(rb"(?:^|[\s\]>)])(re|m|l|c|v|y|f\*|f|B\*|B|b\*|b|S|s)(?=[\s\[<(/]|$)")
_DO = re.compile(rb"/([A-Za-z0-9#._-]+)\s+Do\b")


_SPACE = frozenset(b"\x00\t\n\x0c\r ")
_ENDS_TOKEN = _SPACE | frozenset(b"()<>[]{}/%")


class _Ref(int):
    pass


class _Syntax:









    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def _skip(self) -> None:
        data, pos = self.data, self.pos
        while pos < len(data):
            if data[pos] in _SPACE:
                pos += 1
            elif data[pos] == 0x25:
                while pos < len(data) and data[pos] not in (0x0A, 0x0D):
                    pos += 1
            else:
                break
        self.pos = pos

    def _token(self) -> bytes:
        start = self.pos
        while self.pos < len(self.data) and self.data[self.pos] not in _ENDS_TOKEN:
            self.pos += 1
        return self.data[start:self.pos]

    def value(self, depth: int = 0):
        self._skip()
        data, pos = self.data, self.pos
        if pos >= len(data):
            return None
        if depth > _MAX_NESTING:
            self._step_over()
            return None
        if data.startswith(b"<<", pos):
            self.pos += 2
            return self._dictionary(depth)
        first = data[pos]
        if first == 0x2F:
            self.pos += 1
            return self._token()
        if first == 0x5B:
            self.pos += 1
            return self._array(depth)
        if first == 0x28:
            return self._literal()
        if first == 0x3C:
            end = data.find(b">", pos)
            self.pos = len(data) if end < 0 else end + 1
            return ""
        token = self._token()
        if token in (b"true", b"false", b"null"):
            return {b"true": True, b"false": False}.get(token)
        number = _number(token)
        if number is None:
            raise ValueError(f"unexpected {bytes(data[pos:pos + 12])!r}")
        if isinstance(number, int) and token.isdigit():
            mark = self.pos
            self._skip()
            generation = self._token()
            self._skip()
            if generation.isdigit() and self._token() == b"R":
                return _Ref(number)
            self.pos = mark
        return number

    def _step_over(self) -> None:

        level = 0
        while True:
            self._skip()
            data, pos = self.data, self.pos
            if pos >= len(data):
                return
            if data.startswith(b"<<", pos) or data[pos] == 0x5B:
                level += 1
                self.pos += 2 if data[pos] == 0x3C else 1
            elif data.startswith(b">>", pos) or data[pos] == 0x5D:
                level -= 1
                self.pos += 2 if data[pos] == 0x3E else 1
            elif data[pos] == 0x28:
                self._literal()
            elif data[pos] == 0x3C:
                end = data.find(b">", pos)
                self.pos = len(data) if end < 0 else end + 1
            else:
                self.pos += 1 if data[pos] == 0x2F else 0
                if not self._token():
                    self.pos += 1
            if level <= 0:
                return

    def _dictionary(self, depth: int) -> dict:
        entries: dict = {}
        while True:
            self._skip()
            if self.pos >= len(self.data):
                return entries
            if self.data.startswith(b">>", self.pos):
                self.pos += 2
                return entries
            if self.data[self.pos] != 0x2F:
                raise ValueError("a dictionary key that is not a name")
            self.pos += 1
            key = self._token()
            entries[key] = self.value(depth + 1)

    def _array(self, depth: int) -> list:
        items: list = []
        while True:
            self._skip()
            if self.pos >= len(self.data):
                return items
            if self.data[self.pos] == 0x5D:
                self.pos += 1
                return items
            items.append(self.value(depth + 1))

    def _literal(self) -> str:
        data, pos, depth = self.data, self.pos + 1, 1
        start = pos
        while pos < len(data):
            byte = data[pos]
            if byte == 0x5C:
                pos += 2
                continue
            if byte == 0x28:
                depth += 1
            elif byte == 0x29:
                depth -= 1
                if depth == 0:
                    self.pos = pos + 1
                    return data[start:pos].decode("latin-1")
            pos += 1
        self.pos = len(data)
        return data[start:].decode("latin-1")


def _number(token: bytes):

    for kind in (int, float):
        try:
            return kind(token)
        except ValueError:
            continue
    return None


def _dictionary(body: bytes) -> bytes:

    cut = body.find(b"stream")
    return body if cut < 0 else body[:cut]


def _inflate(dictionary: bytes, raw: bytes) -> bytes:

    if not raw:
        return b""
    if b"/Filter" not in dictionary:
        return raw[:_MAX_STREAM_BYTES]
    if b"/FlateDecode" not in dictionary:
        return b""
    try:
        data = zlib.decompressobj().decompress(raw, _MAX_STREAM_BYTES)
    except zlib.error:
        return b""
    if b"/Predictor" in dictionary:
        return b""
    return data


def _values_of(value, key: bytes) -> list:





    if isinstance(value, dict):
        found = [value[key]] if key in value else []
        for inner in value.values():
            found += _values_of(inner, key)
        return found
    if isinstance(value, list):
        return [item for inner in value for item in _values_of(inner, key)]
    return []


def _whole(value) -> int:

    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return 0
    return int(value)


class _Document:


    def __init__(self, data: bytes) -> None:
        self.objects: dict[int, tuple[bytes, bytes]] = {}
        self._values: dict[int, object] = {}
        self._scan(data)
        self._expand_object_streams()

    def _scan(self, data: bytes) -> None:
        for match in _OBJ.finditer(data):
            if len(self.objects) >= _MAX_OBJECTS:
                return
            number = int(match.group(1))
            end = data.find(b"endobj", match.end())
            body = data[match.end():end if end >= 0 else len(data)]
            stream = b""
            cut = body.find(b"stream")
            if cut >= 0:
                start = cut + len(b"stream")
                if body[start:start + 2] == b"\r\n":
                    start += 2
                elif body[start:start + 1] in (b"\n", b"\r"):
                    start += 1
                stop = body.find(b"endstream", start)
                stream = body[start:stop if stop >= 0 else len(body)]
            self.objects[number] = (_dictionary(body), stream)

    def _expand_object_streams(self) -> None:

        for number, (dictionary, raw) in list(self.objects.items()):
            if b"/ObjStm" not in dictionary:
                continue
            entries = self.entries(number)
            if entries.get(b"Type") != b"ObjStm":
                continue
            data = _inflate(dictionary, raw)
            first = self.direct(entries.get(b"First"))
            count = self.direct(entries.get(b"N"))
            if not data or not isinstance(first, int) or not isinstance(count, int):
                continue
            offset = first
            pairs = data[:offset].split()
            total = min(count, len(pairs) // 2)
            for index in range(total):
                try:
                    packed = int(pairs[index * 2])
                    start = offset + int(pairs[index * 2 + 1])


                    stop = (offset + int(pairs[index * 2 + 3])) if index + 1 < total else len(data)
                except (ValueError, IndexError):
                    continue
                if packed in self.objects or len(self.objects) >= _MAX_OBJECTS:
                    continue
                self.objects[packed] = (data[start:stop], b"")

    def dictionary(self, number: int) -> bytes:
        return self.objects.get(number, (b"", b""))[0]

    def value(self, number: int):

        if number not in self._values:
            try:
                self._values[number] = _Syntax(self.dictionary(number)).value()
            except (ValueError, RecursionError):
                self._values[number] = None
        return self._values[number]

    def entries(self, number: int) -> dict:
        found = self.value(number)
        return found if isinstance(found, dict) else {}

    def direct(self, value):

        return self.value(int(value)) if isinstance(value, _Ref) else value

    def resolve(self, entries: dict, key: bytes) -> dict:

        found = self.direct(entries.get(key))
        return found if isinstance(found, dict) else {}

    def content(self, page: dict) -> bytes:

        contents = page.get(b"Contents")
        if isinstance(contents, _Ref) and isinstance(self.value(int(contents)), list):
            contents = self.value(int(contents))
        references = contents if isinstance(contents, list) else [contents]
        parts = []
        for reference in references:
            if isinstance(reference, _Ref):
                dictionary, raw = self.objects.get(int(reference), (b"", b""))
                parts.append(_inflate(dictionary, raw))
        return b"\n".join(parts)


def _pages(document: _Document) -> list[int]:

    root = 0
    for number, (dictionary, _stream) in document.objects.items():
        if b"/Catalog" in dictionary and document.entries(number).get(b"Type") == b"Catalog":
            pages = document.entries(number).get(b"Pages")
            root = int(pages) if isinstance(pages, _Ref) else 0
            break
    ordered: list[int] = []
    if root:
        stack = [root]
        seen = set()
        while stack and len(ordered) < _MAX_PAGES:
            number = stack.pop(0)
            if number in seen:
                continue
            seen.add(number)
            entries = document.entries(number)
            if entries.get(b"Type") == b"Page":
                ordered.append(number)
                continue
            kids = document.direct(entries.get(b"Kids"))
            if isinstance(kids, list):
                stack = [int(kid) for kid in kids if isinstance(kid, _Ref)] + stack
    if ordered:
        return ordered
    return sorted(number for number, (dictionary, _s) in document.objects.items()
                  if b"/Page" in dictionary and document.entries(number).get(b"Type") == b"Page")[:_MAX_PAGES]


def _media_box(document: _Document, number: int) -> tuple:

    seen = 0
    while number and seen < 8:
        entries = document.entries(number)
        box = document.direct(entries.get(b"MediaBox"))
        if (isinstance(box, list) and len(box) == 4
                and all(isinstance(side, (int, float)) and not isinstance(side, bool) for side in box)):
            left, bottom, right, top = (float(side) for side in box)
            return abs(right - left), abs(top - bottom)
        parent = entries.get(b"Parent")
        number = int(parent) if isinstance(parent, _Ref) else 0
        seen += 1
    return 0.0, 0.0


def _drawn(document: _Document, resources: dict, stream: bytes, depth: int,
           images: list, fonts: set, seen: set) -> int:





    strokes = len(_PAINT.findall(stream))

    for font in document.resolve(resources, b"Font").values():
        for name in _values_of(document.direct(font), b"BaseFont"):
            if isinstance(name, bytes):
                fonts.add(name.decode("latin-1"))
    drawn_names = set(_DO.findall(stream))
    for name, reference in document.resolve(resources, b"XObject").items():
        if name not in drawn_names or not isinstance(reference, _Ref):
            continue
        number = int(reference)
        entries = document.entries(number)
        kind = entries.get(b"Subtype")
        if kind == b"Image":

            chain = document.direct(entries.get(b"Filter"))
            chain = chain if isinstance(chain, list) else [chain]
            codec = next((item for item in reversed(chain) if isinstance(item, bytes)), b"none")
            images.append({"width": _whole(document.direct(entries.get(b"Width"))),
                           "height": _whole(document.direct(entries.get(b"Height"))),
                           "encoding": codec.decode("latin-1")})
        elif kind == b"Form" and depth < _MAX_FORM_DEPTH and number not in seen:
            seen.add(number)
            dictionary, raw = document.objects.get(number, (b"", b""))
            strokes += _drawn(document, document.resolve(entries, b"Resources"),
                              _inflate(dictionary, raw), depth + 1, images, fonts, seen)
    return strokes



_VECTOR_FLOOR = 40


def pdf_facts(path: str) -> dict:





    facts: dict = {"file_bytes": 0, "readable": False}
    try:
        facts["file_bytes"] = os.path.getsize(path)
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        facts["why"] = f"the file could not be read ({exc})"
        return facts
    if not data.startswith(b"%PDF-"):
        facts["why"] = "the file does not start with %PDF-"
        return facts
    try:
        document = _Document(data)
        numbers = _pages(document)
    except Exception as exc:  # noqa: BLE001
        facts["why"] = f"the objects could not be read ({exc})"
        return facts
    if not numbers:
        facts["why"] = "no page object was found"
        return facts

    facts["readable"] = True
    facts["pdf_version"] = data[5:8].decode("ascii", "replace")
    facts["pages"] = len(numbers)
    fonts: set = set()
    rows = []
    for index, number in enumerate(numbers, start=1):
        page = document.entries(number)
        width_pt, height_pt = _media_box(document, number)
        images: list = []
        try:
            strokes = _drawn(document, document.resolve(page, b"Resources"),
                             document.content(page), 0, images, fonts, set())
        except Exception as exc:  # noqa: BLE001
            rows.append({"page": index, "drawing": "unknown", "why": str(exc)})
            continue
        biggest = max((row["width"] * row["height"] for row in images), default=0)
        if images and strokes >= _VECTOR_FLOOR:
            drawing = "mixed"
        elif images:
            drawing = "raster image"
        elif strokes:
            drawing = "vector"
        else:
            drawing = "empty"
        row = {"page": index, "drawing": drawing, "paint_ops": strokes, "images": len(images)}
        if width_pt and height_pt:
            row["width_mm"] = round(width_pt * _MM_PER_POINT, 1)
            row["height_mm"] = round(height_pt * _MM_PER_POINT, 1)
            row["orientation"] = "landscape" if width_pt > height_pt else "portrait"
        if images:
            row["image_pixels"] = [f"{item['width']}x{item['height']} {item['encoding']}"
                                   for item in images[:6]]
            row["largest_image_megapixels"] = round(biggest / 1_000_000.0, 2)
        rows.append(row)
    facts["page_details"] = rows
    facts["fonts"] = sorted(fonts)[:20]
    kinds = {row.get("drawing") for row in rows}
    facts["note"] = (
        "Read from the file's own objects, form XObjects walked into: "
        + ("every page draws vector lines and text" if kinds == {"vector"}
           else "every page is one flattened picture, so nothing in it is selectable or searchable"
           if kinds == {"raster image"}
           else "pages carry both vector drawing and pictures" if kinds == {"mixed"}
           else "the pages differ: " + ", ".join(sorted(str(kind) for kind in kinds)))
        + ". paint_ops counts the line and curve operators on the page.")
    return facts
