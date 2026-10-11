# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




















from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

FENCE_TAG = "openui"
MAX_BLOCKS = 2
MAX_STATS = 4
MAX_BARS = 8
MAX_STEPS = 6
MAX_COLS = 6
MAX_ROWS = 12
TONES = ("neutral", "info", "success", "alert", "danger", "purple")

_MAX_SOURCE = 20_000
_MAX_DEPTH = 12
_MAX_NODES = 20_000


@dataclass
class Call:

    name: str
    args: list = field(default_factory=list)


@dataclass
class Ref:
    name: str


@dataclass
class Block:

    kind: str
    title: str = ""
    text: str = ""
    tone: str = "neutral"
    items: list = field(default_factory=list)
    more_rows: int = 0
    more_cols: int = 0


class _Syntax(ValueError):
    pass


_TOKEN = re.compile(r"""
    (?P<ws>[ \t]+)
  | (?P<str>"(?:[^"\\]|\\.)*")
  | (?P<num>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
  | (?P<id>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>[()\[\]{},=:])
""", re.VERBOSE)
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "n": "\n", "t": "\t", "r": "", "b": "", "f": ""}


def _string(raw: str) -> str:
    out, i = [], 1
    while i < len(raw) - 1:
        ch = raw[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        nxt = raw[i + 1] if i + 1 < len(raw) - 1 else ""
        if nxt == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", raw[i + 2:i + 6] or ""):
            out.append(chr(int(raw[i + 2:i + 6], 16)))
            i += 6
            continue
        out.append(_ESCAPES.get(nxt, nxt))
        i += 2
    return "".join(out)


def _tokens(line: str) -> list:
    pos, out = 0, []
    while pos < len(line):
        match = _TOKEN.match(line, pos)
        if match is None:
            raise _Syntax(line[pos:pos + 10])
        pos = match.end()
        kind = match.lastgroup
        if kind != "ws":
            out.append((kind, match.group(kind)))
    return out


class _Reader:
    def __init__(self, tokens: list):
        self.tokens = tokens
        self.i = 0

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else (None, None)

    def take(self, value: str | None = None):
        token = self.peek()
        if token[0] is None or (value is not None and token[1] != value):
            raise _Syntax(str(value))
        self.i += 1
        return token

    def expr(self, depth: int = 0) -> Any:
        if depth > _MAX_DEPTH:
            raise _Syntax("depth")
        kind, value = self.take()
        if kind == "str":
            return _string(value)
        if kind == "num":
            number = float(value)
            return int(number) if number.is_integer() and "." not in value and "e" not in value.lower() else number
        if kind == "id":
            if value == "true":
                return True
            if value == "false":
                return False
            if value == "null":
                return None
            if self.peek() == ("op", "("):
                self.take("(")
                return Call(value, self._list(")", depth))
            return Ref(value)
        if value == "[":
            return self._list("]", depth)
        if value == "{":
            obj = {}
            while self.peek() != ("op", "}"):
                key_kind, key = self.take()
                if key_kind not in ("id", "str"):
                    raise _Syntax("key")
                self.take(":")
                obj[_string(key) if key_kind == "str" else key] = self.expr(depth + 1)
                if self.peek() == ("op", ","):
                    self.take(",")
                elif self.peek() != ("op", "}"):
                    raise _Syntax("object")
            self.take("}")
            return obj
        raise _Syntax(str(value))

    def _list(self, close: str, depth: int) -> list:
        items = []
        while self.peek() != ("op", close):
            items.append(self.expr(depth + 1))
            if self.peek() == ("op", ","):
                self.take(",")
            elif self.peek() != ("op", close):
                raise _Syntax(close)
        self.take(close)
        return items


def _logical_lines(source: str) -> list:


    out, pending, depth = [], [], 0
    for line in source.splitlines():
        pending.append(line.strip())
        quoted = False
        escaped = False
        for ch in line:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = quoted
            elif ch == '"':
                quoted = not quoted
            elif not quoted and ch in "([{":
                depth += 1
            elif not quoted and ch in ")]}":
                depth -= 1
        if depth <= 0 or len(pending) > 200:
            out.append(" ".join(pending))
            pending, depth = [], 0
    if pending:
        out.append(" ".join(pending))
    return out


def statements(source: str) -> dict:

    out: dict = {}
    for line in _logical_lines(str(source or "")[:_MAX_SOURCE]):
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        try:
            reader = _Reader(_tokens(line))
            kind, name = reader.take()
            if kind != "id":
                continue
            reader.take("=")
            value = reader.expr()
            if reader.peek()[0] is not None:
                continue
        except (_Syntax, ValueError, OverflowError):
            continue
        out.setdefault(name, value)
    return out


class _Resolver:






    def __init__(self, table: dict):
        self.table = table
        self.done: dict = {}
        self.open: set = set()
        self.work = 0

    def name(self, name: str) -> Any:
        if name in self.done:
            return self.done[name]
        if name in self.open or name not in self.table:
            return None
        self.open.add(name)
        value = self.value(self.table[name], 0)
        self.open.discard(name)
        self.done[name] = value
        return value

    def value(self, value: Any, depth: int) -> Any:
        self.work += 1
        if depth > _MAX_DEPTH or self.work > _MAX_NODES:
            return None
        if isinstance(value, Ref):
            return self.name(value.name)
        if isinstance(value, Call):
            return Call(value.name, [self.value(arg, depth + 1) for arg in value.args])
        if isinstance(value, list):
            return [self.value(item, depth + 1) for item in value]
        if isinstance(value, dict):
            return {key: self.value(item, depth + 1) for key, item in value.items()}
        return value


def _text(value: Any) -> str:
    if value is None or isinstance(value, (Call, list, dict)):
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value).strip()


def _arg(call: Call, index: int) -> Any:
    return call.args[index] if index < len(call.args) else None


def _tone(value: Any) -> str:
    tone = _text(value).lower()
    return tone if tone in TONES else "neutral"


_LEADING_NUMBER = re.compile(r"^([-+]?\d[\d.,]*)([kMG](?![A-Za-z]))?")
_SCALE = {"k": 1e3, "M": 1e6, "G": 1e9}


def number_in(value: Any) -> float | None:



    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"\s", "", _text(value))
    match = _LEADING_NUMBER.match(text)
    if match is None:
        return None
    digits = match.group(1).rstrip(".,")
    comma, dot = digits.rfind(","), digits.rfind(".")
    if comma >= 0 and dot >= 0:
        decimal = "," if comma > dot else "."
        digits = digits.replace("." if decimal == "," else ",", "").replace(",", ".")
    elif comma >= 0:
        groups = digits.split(",")
        thousands = len(groups) > 2 or len(groups[1]) == 3
        digits = digits.replace(",", "") if thousands else digits.replace(",", ".")
    elif digits.count(".") > 1:
        digits = digits.replace(".", "")
    try:
        number = float(digits)
    except ValueError:
        return None
    return number * _SCALE.get(match.group(2) or "", 1.0)


def _calls(value: Any, name: str) -> list:
    return [item for item in (value if isinstance(value, list) else []) if isinstance(item, Call) and item.name == name]


def _cell(value: Any) -> Any:

    if isinstance(value, Call) and value.name == "Tag":
        text = _text(_arg(value, 0))
        return (text, _tone(_arg(value, 1))) if text else ""
    return _text(value)


def _block(call: Any) -> Block | None:
    if not isinstance(call, Call):
        return None
    name = call.name
    if name == "Stats":
        items = [{"label": _text(_arg(s, 0)), "value": _text(_arg(s, 1)), "sub": _text(_arg(s, 2))}
                 for s in _calls(_arg(call, 0), "Stat")]
        items = [item for item in items if item["value"]][:MAX_STATS]
        return Block("Stats", items=items) if items else None
    if name == "Bars":
        items = []
        for bar in _calls(_arg(call, 1), "Bar"):
            value = number_in(_arg(bar, 1))
            if value is None or not math.isfinite(value) or value < 0:
                continue
            shown = _text(_arg(bar, 2)) or _text(_arg(bar, 1))
            items.append({"label": _text(_arg(bar, 0)), "value": value, "shown": shown})
        items = items[:MAX_BARS]
        return Block("Bars", title=_text(_arg(call, 0)), items=items) if items else None
    if name == "Table":
        cols = []
        for col in _calls(_arg(call, 0), "Col"):
            values = _arg(col, 1)
            kind = _text(_arg(col, 2)).lower() or "text"
            cols.append({"name": _text(_arg(col, 0)), "kind": kind,
                         "values": [_cell(v) for v in (values if isinstance(values, list) else [])]})
        if not cols or not any(col["values"] for col in cols):
            return None
        rows = max(len(col["values"]) for col in cols)
        for col in cols:
            col["values"] = (col["values"] + [""] * (rows - len(col["values"])))[:MAX_ROWS]
        return Block("Table", items=cols[:MAX_COLS], more_rows=max(0, rows - MAX_ROWS),
                     more_cols=max(0, len(cols) - MAX_COLS))
    if name == "Callout":
        title, text = _text(_arg(call, 0)), _text(_arg(call, 1))
        if not (title or text):
            return None
        return Block("Callout", title=title, text=text, tone=_tone(_arg(call, 2)))
    if name == "Steps":
        items = [{"title": _text(_arg(s, 0)), "detail": _text(_arg(s, 1))} for s in _calls(_arg(call, 0), "Step")]
        items = [item for item in items if item["title"] or item["detail"]][:MAX_STEPS]
        return Block("Steps", items=items) if items else None
    return None


def parse(source: str) -> Block | None:




    table = statements(source)
    names = ["root"] if "root" in table else []
    names += [name for name, value in table.items() if name != "root" and isinstance(value, Call)]
    for name in names:
        block = _block(_Resolver(table).name(name))
        if block is not None:
            return block
    return None


def more_line(block: Block) -> str:

    out = []
    if block.more_rows:
        out.append(f"+{block.more_rows} more row" + ("s" if block.more_rows > 1 else ""))
    if block.more_cols:
        out.append(f"+{block.more_cols} more column" + ("s" if block.more_cols > 1 else ""))
    return ", ".join(out)


def to_text(block: Block) -> str:

    if block.kind == "Stats":
        return "\n".join(f"- {s['label']}: {s['value']}" + (f" ({s['sub']})" if s["sub"] else "")
                         for s in block.items)
    if block.kind == "Bars":
        lines = [block.title] if block.title else []
        return "\n".join(lines + [f"- {b['label']}: {b['shown']}" for b in block.items])
    if block.kind == "Table":
        def cell(value):
            return (value[0] if isinstance(value, tuple) else value).replace("|", "/")
        head = "| " + " | ".join(col["name"] for col in block.items) + " |"
        rule = "|" + "|".join("---" for _ in block.items) + "|"
        rows = ["| " + " | ".join(cell(col["values"][r]) for col in block.items) + " |"
                for r in range(len(block.items[0]["values"]))]
        return "\n".join([head, rule, *rows] + ([more_line(block)] if more_line(block) else []))
    if block.kind == "Callout":
        return f"{block.title}: {block.text}" if block.title and block.text else block.title or block.text
    if block.kind == "Steps":
        return "\n".join(f"{i}. {s['title']}" + (f": {s['detail']}" if s["detail"] else "")
                         for i, s in enumerate(block.items, 1))
    return ""




_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*" + FENCE_TAG + r"[ \t]*$")


def split(text: str) -> list:




    lines = str(text or "").split("\n")
    parts: list = []
    prose: list = []
    blocks = 0
    i = 0
    while i < len(lines):
        match = _FENCE_OPEN.match(lines[i])
        if match is None:
            prose.append(lines[i])
            i += 1
            continue
        fence = match.group(1)
        close = re.compile(r"^ {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*$")
        end = next((j for j in range(i + 1, len(lines)) if close.match(lines[j])), None)
        source = "\n".join(lines[i + 1:end if end is not None else len(lines)])
        blocks += 1
        if blocks > MAX_BLOCKS:

            prose += lines[i:(end + 1) if end is not None else len(lines)]
        else:
            if prose:
                parts.append(("md", "\n".join(prose)))
                prose = []
            parts.append(("block", source, end is not None))
        i = (end + 1) if end is not None else len(lines)
    if prose or not parts:
        parts.append(("md", "\n".join(prose)))
    return parts


def readable(text: str) -> str:

    if FENCE_TAG not in str(text or ""):
        return text
    out = []
    for part in split(text):
        if part[0] == "md":
            out.append(part[1])
            continue
        block = parse(part[1])
        out.append(to_text(block) if block is not None else part[1])
    return "\n".join(out)
