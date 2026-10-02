# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


























from __future__ import annotations

import os
import re
import sys

from qgis.PyQt.QtCore import QUrl



_FENCED = re.compile(r"```.*?```|~~~.*?~~~|\[[^\]]*\]\([^)]*\)", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]*)`")









_POSIX = r"~?/[^\n\x00<>\"'`|*?]*"
_WINDOWS = r"[A-Za-z]:[\\/][^\n\x00<>\"'`|*?]*"



_UNC = r"\\\\[^\s\\/\x00<>\"'`|*?]+\\[^\n\x00<>\"'`|*?]*"
_CANDIDATE = re.compile(rf"(?<![\w~\\]){_UNC}|(?<![\w~]){_WINDOWS}|(?<![\w~]){_POSIX}")

_TRAILING = ".,;:!?"




_MAX_EXISTS_CHECKS = 40







_UNC_PREFIX = re.compile(r"^[\\/]{2}")














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






    name = str(path or "").rstrip(". \t")
    return os.path.splitext(name)[1].lstrip(".").lower()


def is_remote_or_device(path: str) -> bool:




    return bool(_UNC_PREFIX.match(str(path or "").lstrip()))


def is_safe_to_open(path: str) -> bool:









    text = str(path or "")
    if not text or is_remote_or_device(text):
        return False
    suffix = suffix_of(text)
    if suffix in BUNDLE_SUFFIXES or suffix.startswith("{") or ":" in os.path.splitdrive(text)[1]:
        return False
    if os.path.isdir(text):
        return True
    return suffix in SAFE_TO_OPEN


def reveal_target(path: str) -> str:

    text = str(path or "")
    if is_remote_or_device(text):
        return text
    parent = os.path.dirname(text.rstrip("/\\")) or text
    return parent if os.path.isdir(parent) else text


class _ExistsBudget:







    __slots__ = ("left",)

    def __init__(self, count: int = _MAX_EXISTS_CHECKS):
        self.left = count

    def spend(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        return True


def _is_remote_windows_root(path: str) -> bool:






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







    encoded = bytes(QUrl.fromLocalFile(os.path.expanduser(path)).toEncoded()).decode("ascii")
    return encoded.replace("(", "%28").replace(")", "%29")


def path_from_url(url: str) -> str:

    text = str(url or "")
    if not text.startswith("file:"):
        return ""
    return QUrl(text).toLocalFile()


def escape_markdown_label(text: str) -> str:








    return str(text).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _link(path: str) -> str:








    name = os.path.basename(os.path.normpath(path)) or path
    title = path.replace("\\", "\\\\").replace('"', '\\"')
    return f'[{escape_markdown_label(name)}]({file_url(path)} "{title}")'


def _plain(text: str) -> str:







    return text.replace("\\", "\\\\")






_URL = re.compile(r"(?<![\w</])https?://[^\s<>\"'`\x00]+", re.IGNORECASE)
_URL_TRAILING = _TRAILING + "*_~"
_URL_PAIRS = {")": "(", "]": "["}

_URL_FILE = re.compile(r"^[^/?#]+\.[A-Za-z0-9]{2,5}$")


def url_label(url: str) -> str:

    from urllib.parse import unquote, urlsplit

    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    segment = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1]) if parts.path else ""
    if segment and _URL_FILE.match(segment) and not segment.lower().startswith("index."):
        return segment
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or url


def _link_url(match) -> str:
    url = match.group(0)
    tail = ""
    while url and (url[-1] in _URL_TRAILING or (
            url[-1] in _URL_PAIRS and url.count(_URL_PAIRS[url[-1]]) < url.count(url[-1]))):
        tail = url[-1] + tail
        url = url[:-1]
    if "://" not in url or url.endswith("://"):
        return match.group(0)
    target = (url.replace("(", "%28").replace(")", "%29").replace(" ", "%20")
              .replace("[", "%5B").replace("]", "%5D"))
    title = url.replace("\\", "\\\\").replace('"', '\\"')
    return f'[{escape_markdown_label(url_label(url))}]({target} "{title}")' + tail


def _link_bare(match, budget: _ExistsBudget) -> str:
    candidate = match.group(0)
    path = _longest_existing(candidate, budget)
    if not path:
        return _plain(candidate)



    return _link(path) + _CANDIDATE.sub(lambda m: _link_bare(m, budget), candidate[len(path):])


def _link_inline_code(match, budget: _ExistsBudget) -> str:

    inner = match.group(1).strip()
    path = _longest_existing(inner, budget) if inner else ""
    if not path or path != inner:
        return match.group(0)
    return _link(path)


def linkify_paths(text: str) -> str:











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
    masked = _INLINE_CODE.sub(keep, masked)


    masked = _URL.sub(_link_url, masked)
    masked = _FENCED.sub(keep, masked)
    masked = _CANDIDATE.sub(lambda m: _link_bare(m, budget), masked)
    return re.sub(r"\x00(\d+)\x00", restore, masked)
