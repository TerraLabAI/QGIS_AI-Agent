# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later









from __future__ import annotations

import re

from ..core import link_vault

REDACTED = "<signed url>"


def redact_signed_urls(text: str) -> str:
    return link_vault.redact(text or "", REDACTED)


def fence(text: str, language: str = "") -> str:

    longest = 0
    for run in re.findall(r"`+", text or ""):
        longest = max(longest, len(run))
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{language}\n{text}\n{ticks}"
