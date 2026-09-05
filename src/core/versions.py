# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later











from __future__ import annotations

import re






_VERSION_RE = re.compile(
    r"^[vV]?(\d{1,6})(?:\.(\d{1,6}))?(?:\.(\d{1,6}))?(?:\.\d{1,6})*(?:[-+][0-9A-Za-z.\-+]*)?$")


def read_version(text: object) -> tuple[int, int, int] | None:

    if not isinstance(text, str):
        return None
    match = _VERSION_RE.match(text.strip())
    if match is None:
        return None
    return tuple(int(g or 0) for g in match.groups())   # type: ignore[return-value]


def parse_version(text: object) -> tuple[int, int, int]:







    return read_version(text) or (0, 0, 0)


def is_newer(candidate: object, installed: object) -> bool:







    left, right = read_version(candidate), read_version(installed)
    if left is None or right is None:
        return False
    return left > right
