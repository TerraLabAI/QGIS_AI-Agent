# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Two pieces of transcript text, shared by everything that writes one."""







from __future__ import annotations

import re



_SIGNED_URL_RE = re.compile(
    r"https?://[^\s<>()\[\]\"']*[?&](?:sig|signature|token|access_token|X-Amz-[A-Za-z-]+)="
    r"[^\s<>()\[\]\"']*",
    re.IGNORECASE,
)

REDACTED = "<signed url>"


def redact_signed_urls(text: str) -> str:
    return _SIGNED_URL_RE.sub(REDACTED, text or "")


def fence(text: str, language: str = "") -> str:
    """A fenced block that cannot be broken by backticks in ``text``."""
    longest = 0
    for run in re.findall(r"`+", text or ""):
        longest = max(longest, len(run))
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{language}\n{text}\n{ticks}"
