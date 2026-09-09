# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The files an answer names, as things to open, inside the chat itself."""


















from __future__ import annotations

import os
import re
import sys

from qgis.PyQt.QtCore import QUrl



_FENCED = re.compile(r"```.*?```|~~~.*?~~~|\[[^\]]*\]\([^)]*\)", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]*)`")









_POSIX = r"~?/[^\n\x00<>\"'`|*?]*"
_WINDOWS = r"[A-Za-z]:[\\/][^\n\x00<>\"'`|*?]*"
_CANDIDATE = re.compile(rf"(?<![\w~]){_WINDOWS}|(?<![\w~]){_POSIX}")

_TRAILING = ".,;:!?"




_MAX_EXISTS_CHECKS = 40



_UNC_PREFIX = re.compile(r"^\\\\[^\\]")














SAFE_TO_OPEN = frozenset((

    "gpkg", "shp", "shx", "dbf", "prj", "cpg", "qix", "geojson", "json",
    "kml", "kmz", "gml", "gpx", "dxf", "csv", "tsv", "tab", "mif", "mid",
    "fgb", "parquet", "gdb",

    "tif", "tiff", "img", "jp2", "png", "jpg", "jpeg", "gif", "bmp", "webp",
    "asc", "vrt", "nc", "hdf", "hgt", "dem", "ecw", "sid", "xyz", "pgw",
    "tfw", "jgw", "aux", "ovr",

    "las", "laz", "copc", "ply", "obj",

    "qgz", "qgs", "qlr", "qml", "qpt", "sld", "qmd", "gqs",

    "pdf", "txt", "md", "rst", "log", "xml", "yaml", "yml", "ini", "cfg",
    "svg", "html", "htm", "xlsx", "xls", "ods", "docx", "odt", "odp", "pptx",
    "zip", "gz", "tar", "7z",
))






BUNDLE_SUFFIXES = frozenset((
    "app", "bundle", "framework", "kext", "plugin", "prefpane", "qlgenerator",
    "saver", "service", "appex", "workflow", "wdgt", "scptd", "osax",
    "component", "mdimporter", "xpc", "action", "docset",
))


def suffix_of(path: str) -> str:
    """The extension a desktop would dispatch on, lowered."""





    name = str(path or "").rstrip(". \t")
    return os.path.splitext(name)[1].lstrip(".").lower()


def is_safe_to_open(path: str) -> bool:
    """Whether this path may be handed to the desktop's own handler."""






    text = str(path or "")
    if not text:
        return False
    suffix = suffix_of(text)
    if suffix in BUNDLE_SUFFIXES:
        return False
    if os.path.isdir(text):
        return True
    return suffix in SAFE_TO_OPEN


def reveal_target(path: str) -> str:
    """The folder to show instead of opening ``path`` itself."""
    text = str(path or "")
    parent = os.path.dirname(text.rstrip("/\\")) or text
    return parent if os.path.isdir(parent) else text


class _ExistsBudget:
    """How many ``os.path.exists`` calls one message may still spend."""






    __slots__ = ("left",)

    def __init__(self, count: int = _MAX_EXISTS_CHECKS):
        self.left = count

    def spend(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        return True


def _is_remote_windows_root(path: str) -> bool:
    """True when ``path``'s drive letter is a mapped network share."""





    if sys.platform != "win32":
        return False
    try:
        import ctypes
        root = os.path.splitdrive(path)[0] + "\\"
        if not root or root == "\\":
            return False
        return ctypes.windll.kernel32.GetDriveTypeW(root) == 4
    except Exception:
        return False


def _exists(path: str, budget: _ExistsBudget) -> bool:
    if _UNC_PREFIX.match(path) or _is_remote_windows_root(path):
        return False
    if not budget.spend():
        return False
    try:
        return os.path.exists(os.path.expanduser(path))
    except (OSError, ValueError):
        return False


def _longest_existing(candidate: str, budget: _ExistsBudget) -> str:
    """The longest head of ``candidate`` that is a path on disk, else ""."""










    text = candidate
    while text:
        if budget.left <= 0:
            return ""
        trimmed = text.rstrip(_TRAILING + " ")
        if len(trimmed) >= 4 and _exists(trimmed, budget):
            return trimmed
        cut = text.rfind(" ")
        if cut <= 0:
            return ""
        text = text[:cut]
    return ""


def file_url(path: str) -> str:
    """``path`` as a fully encoded ``file:`` URL."""






    encoded = bytes(QUrl.fromLocalFile(os.path.expanduser(path)).toEncoded()).decode("ascii")
    return encoded.replace("(", "%28").replace(")", "%29")


def path_from_url(url: str) -> str:
    """The local path of a ``file:`` link, else "". """
    text = str(url or "")
    if not text.startswith("file:"):
        return ""
    return QUrl(text).toLocalFile()


def escape_markdown_label(text: str) -> str:
    """A path as the visible words of a markdown link."""







    return str(text).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _link(path: str) -> str:
    """The markdown link for ``path``, keeping the path as the words: the reader still sees where the file went, and can now click it."""

    return f"[{escape_markdown_label(path)}]({file_url(path)})"


def _link_bare(match, budget: _ExistsBudget) -> str:
    candidate = match.group(0)
    path = _longest_existing(candidate, budget)
    if not path:
        return candidate
    return _link(path) + candidate[len(path):]


def _link_inline_code(match, budget: _ExistsBudget) -> str:
    """A path alone in backticks becomes the link; any other code stays."""
    inner = match.group(1).strip()
    path = _longest_existing(inner, budget) if inner else ""
    if not path or path != inner:
        return match.group(0)
    return _link(path)


def linkify_paths(text: str) -> str:
    """The answer with every path that exists on disk turned into a link."""










    text = (text or "").replace("\x00", "")
    if "/" not in text and "\\" not in text:
        return text

    guarded: list[str] = []

    def keep(match):
        guarded.append(match.group(0))
        return f"\x00{len(guarded) - 1}\x00"

    def restore(match):
        index = int(match.group(1))
        return guarded[index] if 0 <= index < len(guarded) else match.group(0)

    budget = _ExistsBudget()
    masked = _FENCED.sub(keep, text)
    masked = _INLINE_CODE.sub(lambda m: _link_inline_code(m, budget), masked)

    masked = _FENCED.sub(keep, masked)
    masked = _CANDIDATE.sub(lambda m: _link_bare(m, budget), masked)
    return re.sub(r"\x00(\d+)\x00", restore, masked)
