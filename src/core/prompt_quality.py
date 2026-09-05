# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Is this message worth a run?"""























from __future__ import annotations

import re

from . import tuning

TOO_SHORT = "too_short"
NOT_A_TASK = "not_a_task"






ENOUGH_CHARS = 40
MIN_WORDS = 3
MIN_CHARS = 12


MIN_WORDS_WITH_CONTEXT = 2
MIN_CHARS_WITH_CONTEXT = 6

_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
_VOWELS = set("aeiouyàâäéèêëîïôöùûüœæ")


_LATIN = re.compile(r"^[A-Za-zÀ-ÿ]+$")


_UNSPACED = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
MIN_CHARS_UNSPACED = 5


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


def _is_gibberish_word(word: str) -> bool:
    """A long Latin word with no vowel, or one letter hammered five times."""
    lowered = word.lower()
    if re.search(r"(.)\1{4,}", lowered):
        return True
    return bool(len(lowered) >= 6 and _LATIN.match(lowered) and not (set(lowered) & _VOWELS))


def check_prompt(text: str, attachments: int = 0, chips: int = 0, first_message: bool = True) -> str:
    """``""`` to send, else ``TOO_SHORT`` or ``NOT_A_TASK``."""




    text = (text or "").strip()
    if not text:
        return TOO_SHORT
    words = _words(text)
    has_context = bool(attachments or chips)

    if not words:

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
