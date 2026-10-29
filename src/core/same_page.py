# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

import html
import os
import re
import shutil

from .host_platform import retry_file_op

_PAGE_SUFFIXES = (".html", ".htm")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)

_HEAD_BYTES = 256 * 1024


def page_title(path: str) -> str:

    if os.path.splitext(path)[1].lower() not in _PAGE_SUFFIXES:
        return ""
    try:
        with open(path, "rb") as handle:
            head = handle.read(_HEAD_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""
    match = _TITLE.search(head)
    return " ".join(html.unescape(match.group(1)).split()) if match else ""


def fold_pages(created, own) -> dict[str, str]:





    fresh = {os.path.normcase(os.path.abspath(path)) for path in created}
    earlier: dict[tuple[str, str], str] = {}
    for path in sorted(own, key=_mtime):
        if os.path.normcase(os.path.abspath(path)) in fresh or not os.path.isfile(path):
            continue
        title = page_title(path)
        if title:
            earlier[(os.path.normcase(os.path.dirname(os.path.abspath(path))), title)] = path
    folded: dict[str, str] = {}
    if not earlier:
        return folded
    for path in created:
        if not os.path.isfile(path):
            continue
        title = page_title(path)
        kept = earlier.get((os.path.normcase(os.path.dirname(os.path.abspath(path))), title)) if title else None
        if kept and _move(path, kept):
            folded[path] = kept
    return folded


def _move(source: str, target: str) -> bool:
    try:
        retry_file_op(os.replace, source, target)
        return True
    except OSError:
        pass
    try:

        shutil.copyfile(source, target)
        retry_file_op(os.remove, source)
        return True
    except OSError:
        return False


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def rewrite_paths(value, folded: dict):

    if isinstance(value, str):
        return folded.get(value, value)
    if isinstance(value, list):
        return [rewrite_paths(item, folded) for item in value]
    if isinstance(value, dict):
        return {key: rewrite_paths(item, folded) for key, item in value.items()}
    return value


def kept_fact(folded: dict) -> str:

    return " ".join(
        f"{new} had the <title> of {kept}, a page this conversation wrote earlier: it replaced "
        f"that file, under that name, and {new} no longer exists."
        for new, kept in folded.items())
