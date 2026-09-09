# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tidy the text that arrives on the clipboard, before it reaches the composer."""













from __future__ import annotations

import re

__all__ = ["clean_pasted_text"]



_SEPARATORS = str.maketrans({"\u2029": "\n", "\u2028": "\n", "\u00a0": " "})





_NEWLINES = re.compile(r"\r\n?")



_BULLETS = re.compile(r"^[ \t]*[•‣▪○◦⁃⁌⁍∙][ \t]*", re.MULTILINE)

_TRAILING_SPACE = re.compile(r"[ \t]+$", re.MULTILINE)
_BLANK_RUN = re.compile(r"\n{3,}")


def clean_pasted_text(text: str) -> str:
    """The clipboard text as the composer should hold it."""




    if not text:
        return ""
    out = _NEWLINES.sub("\n", text)
    out = out.translate(_SEPARATORS)
    out = _BULLETS.sub("", out)
    out = _TRAILING_SPACE.sub("", out)


    out = _BLANK_RUN.sub("\n\n", out)
    return out.strip()
