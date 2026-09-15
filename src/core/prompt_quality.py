# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Is this message worth a run?"""
































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


_UNSPACED = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


MIN_CHARS_UNSPACED = 2


def _knob(name: str, shipped: int) -> int:
    return tuning.limit("compose", name, shipped)


def _words(text: str) -> list[str]:
    return _WORD.findall(text or "")


def _looks_like_letters_only(text: str) -> bool:
    """True when the text is mostly not letters, spaces or sane punctuation."""
    stripped = [c for c in text if not c.isspace()]
    if not stripped:
        return False
    odd = [c for c in stripped if not (c.isalnum() or c in ".,;:!?'\"()[]{}/\\-_+=%&@#*<>°|~$€")]
    return len(odd) > len(stripped) // 2


def _fold(word: str) -> str:
    """Lowercase, then drop combining marks: "ą" -> "a", "ü" -> "u"."""
    folded = unicodedata.normalize("NFD", word.lower())
    return "".join(ch for ch in folded if unicodedata.category(ch) != "Mn")


def _step(first: str, second: str, rows: tuple[str, ...]) -> int:
    """+1 or -1 when the second key is the next or previous key on the first one's row, else 0."""
    for row in rows:
        if first in row and second in row:
            delta = row.index(second) - row.index(first)
            return delta if delta in (1, -1) else 0
    return 0


def _walk_segments(word: str, rows: tuple[str, ...]) -> list[int]:
    """Lengths of the pieces the word splits into, each a slide along one row in one direction."""
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


def _is_gibberish_word(word: str) -> bool:
    """A mash is one letter hit four times, six letters with no vowel, or a slide along one keyboard row (QWERTY, QWERTZ or AZERTY) in one."""











    lowered = word.lower()
    if re.search(r"(.)\1{3,}", lowered):
        return True
    folded = _fold(lowered)
    if not re.fullmatch(r"[a-z]+", folded):
        return False
    n = len(folded)
    if n >= 6 and not any(c in _VOWELS for c in folded):
        return True
    for rows in _LAYOUTS:
        segments = _walk_segments(folded, rows)
        if n >= 6 and len(segments) <= 2 and min(segments) >= 3:
            return True
        if n == 5 and len(segments) == 1:
            return True
        if n == 4 and len(segments) == 1 and not any(c in rows[0] for c in folded):
            return True
    return False


def check_prompt(text: str, attachments: int = 0, chips: int = 0, first_message: bool = True) -> str:
    """``""`` to send, else ``TOO_SHORT`` or ``NOT_A_TASK``."""




    text = (text or "").strip()
    if not text:
        return TOO_SHORT
    words = _words(text)
    has_context = bool(attachments or chips)

    if not words:



        if not first_message and any(c.isalnum() for c in text) and not _looks_like_letters_only(text):
            return ""
        return NOT_A_TASK
    if _looks_like_letters_only(text):
        return NOT_A_TASK
    if len(words) <= 2 and all(_is_gibberish_word(w) for w in words):
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
