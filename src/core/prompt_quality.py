# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








































from __future__ import annotations

import re
import unicodedata

from . import tuning

TOO_SHORT = "too_short"
NOT_A_TASK = "not_a_task"








ENOUGH_CHARS = 40
MIN_WORDS = 1
MIN_CHARS = 1



MIN_WORDS_WITH_CONTEXT = 1
MIN_CHARS_WITH_CONTEXT = 1

_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
_VOWELS = frozenset("aeiouy")


_LAYOUTS = (
    ("qwertyuiop", "asdfghjkl", "zxcvbnm"),
    ("qwertzuiop", "asdfghjkl", "yxcvbnm"),
    ("azertyuiop", "qsdfghjklm", "wxcvbn"),
)


_CYRILLIC = ("йцукенгшщзхъ", "фывапролджэ", "ячсмитьбю")


def _neighbours(rows: tuple[str, ...]) -> frozenset:


    out = set()
    for i, row in enumerate(rows):
        for j, key in enumerate(row):
            out.add((key, key))
            if j + 1 < len(row):
                out.update(((key, row[j + 1]), (row[j + 1], key)))
            if i + 1 < len(rows):
                for under in rows[i + 1][max(0, j - 1):j + 1]:
                    out.update(((key, under), (under, key)))
    return frozenset(out)


_TOUCHING = tuple(_neighbours(rows) for rows in _LAYOUTS)


MASH_RULES = 1


_UNSPACED = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


MIN_CHARS_UNSPACED = 2


def _knob(name: str, shipped: int) -> int:
    return tuning.limit("compose", name, shipped)


def _words(text: str) -> list[str]:
    return _WORD.findall(text or "")


def _looks_like_letters_only(text: str) -> bool:






    stripped = [c for c in text if not c.isspace()]
    if not stripped:
        return False
    odd = [c for c in stripped if not (c.isalnum() or unicodedata.category(c)[0] in "LMNP"
                                       or c in "+=%&@#*<>°|~$€")]
    return len(odd) > len(stripped) // 2


def _fold(word: str) -> str:

    folded = unicodedata.normalize("NFD", word.lower())
    return "".join(ch for ch in folded if unicodedata.category(ch) != "Mn")


def _step(first: str, second: str, rows: tuple[str, ...]) -> int:

    for row in rows:
        if first in row and second in row:
            delta = row.index(second) - row.index(first)
            return delta if delta in (1, -1) else 0
    return 0


def _walk_segments(word: str, rows: tuple[str, ...]) -> list[int]:

    segments = []
    length = 1
    direction = 0
    for first, second in zip(word, word[1:]):
        step = _step(first, second, rows)
        if step and (direction == 0 or step == direction):
            length += 1
            direction = step
        else:
            segments.append(length)
            length = 1
            direction = 0
    segments.append(length)
    return segments


def _slides(folded: str, rows: tuple[str, ...]) -> bool:
    n = len(folded)
    segments = _walk_segments(folded, rows)
    if n >= 6 and len(segments) <= 2 and min(segments) >= 3:
        return True
    if n == 5 and len(segments) == 1:
        return True
    return n == 4 and len(segments) == 1 and not any(c in rows[0] for c in folded)


def _is_mash(folded: str, shouted: bool) -> bool:











    n = len(folded)
    vowels = sum(c in _VOWELS for c in folded)
    for size in (1, 2, 3):
        if n >= 6 and n % size == 0 and n // size >= 3 and folded == folded[:size] * (n // size):
            return True
    touching = max(sum(pair in keys for pair in zip(folded, folded[1:])) for keys in _TOUCHING) / (n - 1)

    home = sum(c in _LAYOUTS[0][1] for c in folded) / n
    if n == 5:
        return not vowels and not shouted and (touching >= 0.75 or home >= 0.8)
    if home >= 0.85 and vowels * 5 <= n:
        return True
    return vowels <= 1 and touching >= (0.8 if n >= 7 else 1)


def _is_gibberish_word(word: str) -> bool:














    lowered = word.lower()
    if re.search(r"(.)\1{3,}", lowered):
        return True
    if re.fullmatch(r"[а-яё]+", lowered):

        return len(lowered) >= 5 and _slides(lowered, _CYRILLIC)
    folded = _fold(lowered)
    if not re.fullmatch(r"[a-z]+", folded):
        return False
    n = len(folded)
    if n >= 6 and not any(c in _VOWELS for c in folded):
        return True
    if any(_slides(folded, rows) for rows in _LAYOUTS):
        return True
    return n >= 5 and bool(_knob("mash_rules", MASH_RULES)) and _is_mash(folded, word.isupper())


def _all_mash(words: list[str], known: str) -> bool:




    long_words = [w for w in words if len(w) >= 4]
    if not long_words:
        return False
    for word in words:
        if known and word.casefold() in known:
            return False
        if len(word) >= 4:
            if not _is_gibberish_word(word):
                return False
        elif any(c in _VOWELS for c in _fold(word)) or not re.fullmatch(r"[a-z]+", _fold(word)):
            return False
    return True


def _reads_as_numbers(text: str) -> bool:


    numbers = re.findall(r"\d+", text)
    if not numbers or len(set(text) - set(" \t")) == 1:
        return False
    return len(numbers) >= 2 or len(numbers[0]) >= 4


def check_prompt(text: str, attachments: int = 0, chips: int = 0, first_message: bool = True,
                 known_names: tuple[str, ...] = ()) -> str:






    text = (text or "").strip()
    if not text:
        return TOO_SHORT
    words = _words(text)
    has_context = bool(attachments or chips)

    if not words:



        if not _looks_like_letters_only(text):
            if _reads_as_numbers(text) or (not first_message and any(c.isalnum() for c in text)):
                return ""
        if not first_message and all(unicodedata.category(c) in ("So", "Sk", "Mn", "Cf") or c.isspace()
                                     for c in text):

            return ""
        return NOT_A_TASK
    if _looks_like_letters_only(text):
        return NOT_A_TASK
    if _all_mash(words, "\n".join(known_names).casefold()):
        return NOT_A_TASK

    if not first_message:

        return ""
    if len(text) >= _knob("enough_chars", ENOUGH_CHARS):
        return ""
    if _UNSPACED.search(text):
        return "" if len(text) >= _knob("min_chars_unspaced", MIN_CHARS_UNSPACED) else TOO_SHORT
    if has_context:
        min_words = _knob("min_words_with_context", MIN_WORDS_WITH_CONTEXT)
        min_chars = _knob("min_chars_with_context", MIN_CHARS_WITH_CONTEXT)
    else:
        min_words = _knob("min_words", MIN_WORDS)
        min_chars = _knob("min_chars", MIN_CHARS)
    if len(words) < min_words or len(text) < min_chars:
        return TOO_SHORT
    return ""
