# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



















from __future__ import annotations

import os
import re
import stat
import unicodedata

from qgis.core import QgsProject

from ..core.host_platform import IS_WINDOWS
from ..core.logger import log_warning
from ..core.policy import create_managed_temp_dir
from ..core.security import fits_path
from .processing_destinations import _gpkg_sink_uri, _gpkg_table_target






















_PATH_FRAGILE_PROVIDERS = frozenset({"grass", "grass7", "saga", "sagang", "pcraster", "lastools"})



_UNRELEASED: list = []


def fragile_provider(algorithm_id: str) -> str:

    provider = str(algorithm_id or "").split(":", 1)[0].lower()
    return provider if provider in _PATH_FRAGILE_PROVIDERS else ""


def unsafe_path(path: str) -> bool:

    text = str(path or "")
    return any(ch.isspace() for ch in text) or (IS_WINDOWS and not text.isascii())


def _what_breaks() -> str:
    return "a space or an accented letter" if IS_WINDOWS else "a space"


def safe_output_name(algorithm_id: str, key: str, path: str) -> tuple[str, str]:





    provider = fragile_provider(algorithm_id)
    folder, name = os.path.split(path)
    if not provider or not unsafe_path(name):
        return path, ""
    text = name
    if IS_WINDOWS:
        text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
        text = "".join(ch if ch.isascii() else "_" for ch in text)
    text = re.sub(r"\s+", "_", text.strip()) or "output"
    new = os.path.join(folder, text)
    return new, f"{key}: {provider} cannot take {_what_breaks()} in a file name, so it was written as {new}"


_KERNEL32 = None


def _kernel32():
    global _KERNEL32
    if _KERNEL32 is None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetShortPathNameW.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
        kernel32.GetShortPathNameW.restype = wintypes.DWORD
        _KERNEL32 = kernel32
    return _KERNEL32


def _short_path(path: str) -> str:






    if not IS_WINDOWS or not os.path.exists(path):
        return ""
    try:
        import ctypes

        kernel32 = _kernel32()
        target = os.path.normpath(os.path.abspath(path))
        size = kernel32.GetShortPathNameW(target, None, 0)
        if not size:
            return ""
        buffer = ctypes.create_unicode_buffer(size)
        written = kernel32.GetShortPathNameW(target, buffer, size)
        return buffer.value if 0 < written < size else ""
    except (OSError, AttributeError, ValueError) as exc:
        log_warning(f"GetShortPathNameW on {path} failed: {exc}")
        return ""


def _is_link(path: str) -> bool:

    try:
        info = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    junction = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", -1)
    return bool(IS_WINDOWS and getattr(info, "st_reparse_tag", 0) == junction)


def _has_sidecars(path: str) -> bool:

    folder, name = os.path.split(os.path.abspath(path))
    stem = os.path.splitext(name)[0].casefold() + "."
    try:
        return any(other.casefold().startswith(stem) and other.casefold() != name.casefold()
                   for other in os.listdir(folder))
    except OSError:
        return True


class PathAliases:


    def __init__(self, provider: str):
        self.provider = provider
        self.pairs: list[tuple[str, str]] = []
        self.links: list[str] = []
        self.owner = ""
        self.problem = ""
        self._folder = None
        self._by_folder: dict[str, str] = {}

    def _link_folder(self) -> str:
        if self._folder is None:
            folder = create_managed_temp_dir("pathalias")
            if unsafe_path(folder):
                short = _short_path(folder)
                if short and not unsafe_path(short):
                    folder = short
                else:
                    self.problem = (f"the plugin's own temporary folder ({os.path.dirname(folder)}) has "
                                    f"{_what_breaks()} in it as well")
                    try:
                        os.rmdir(folder)
                    except OSError:
                        pass
                    folder = ""
            self._folder = folder
        return self._folder

    def _link(self, target: str) -> str:
        base = self._link_folder()
        if not base:
            return ""
        link = os.path.join(base, f"d{len(self.links)}")
        try:
            if IS_WINDOWS:

                import _winapi

                _winapi.CreateJunction(os.path.normpath(os.path.abspath(target)), link)
            else:
                os.symlink(os.path.abspath(target), link, target_is_directory=True)
        except (OSError, AttributeError, ImportError) as exc:
            self.problem = f"a link to its folder could not be made ({exc})"
            return ""
        self.links.append(link)
        return link

    def folder(self, real: str) -> str:

        key = os.path.normcase(os.path.abspath(real))
        if key not in self._by_folder:
            alias = _short_path(real)
            if not alias or unsafe_path(alias):
                alias = self._link(real)
            if alias:
                self.pairs.append((alias, real))
            self._by_folder[key] = alias
        return self._by_folder[key]

    def existing(self, real: str) -> str:








        if os.path.isdir(real):
            return self.folder(real)
        if unsafe_path(os.path.basename(real)) and not _has_sidecars(real):
            short = _short_path(real)
            if short and not unsafe_path(short):
                self.pairs.append((short, real))
                return short
        return self.destination(real)

    def destination(self, real: str) -> str:

        parent, name = os.path.split(os.path.abspath(real))
        if unsafe_path(name):
            self.problem = self.problem or "the file name itself has one, and only a folder aliases without a copy"
            return ""
        if not unsafe_path(parent):
            return real
        if not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        alias = self.folder(parent)
        return os.path.join(alias, name) if alias else ""

    def restore(self, value):

        if isinstance(value, str):
            return self._restore_text(value)
        if isinstance(value, dict):
            return {key: self.restore(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return type(value)(self.restore(item) for item in value)
        return value

    def _restore_text(self, text: str) -> str:
        probe = text.replace("\\", "/")
        for alias, real in sorted(self.pairs, key=lambda pair: -len(pair[0])):
            prefix = alias.replace("\\", "/").rstrip("/")
            head, tail = probe[:len(prefix)], probe[len(prefix):]
            same = head.lower() == prefix.lower() if IS_WINDOWS else head == prefix
            if same and (not tail or tail[0] in "/|"):
                return real + text[len(prefix):]
        return text

    def remove(self) -> None:

        for link in self.links:
            if not _is_link(link):
                continue
            try:
                if IS_WINDOWS:
                    os.rmdir(link)
                else:
                    os.unlink(link)
            except OSError as exc:


                log_warning(f"path alias {link} not removed: {exc}")
        self.links = []
        if self._folder:
            try:
                os.rmdir(self._folder)
            except OSError:
                pass
        if self in _UNRELEASED:
            _UNRELEASED.remove(self)


def _file_behind(item: str, project, destination: bool) -> tuple[str, str, str]:

    if destination:
        table = _gpkg_table_target(item)
        if table is not None:
            return table[0], "", table[1]
        path = item.split("|", 1)[0]
        return (path, item[len(path):], "") if os.path.isabs(path) else ("", "", "")
    layer = project.mapLayer(item) if project is not None else None
    if layer is not None:
        subset = getattr(layer, "subsetString", None)

        if callable(subset) and subset():
            return "", "", ""
        source = str(layer.source() or "")
        path, _sep, rest = source.partition("|")
        if "subset=" in rest or not os.path.isfile(path):
            return "", "", ""
        return path, source[len(path):], ""
    path = item.split("|", 1)[0]
    if os.path.isabs(path) and os.path.exists(path):
        return path, item[len(path):], ""
    return "", "", ""


def alias_fragile_paths(algorithm_id: str, parameters: dict, destination_names) -> tuple:






    provider = fragile_provider(algorithm_id)
    if not provider:
        return parameters, [], None, None
    for earlier in [a for a in _UNRELEASED if not a.owner]:
        earlier.remove()
    project = QgsProject.instance()
    aliases = PathAliases(provider)
    out, notes = dict(parameters), []
    for key, value in parameters.items():
        items = list(value) if isinstance(value, (list, tuple)) else [value]
        changed = False
        destination = key in destination_names
        for index, item in enumerate(items):
            if not isinstance(item, str) or not item:
                continue
            real, rest, table = _file_behind(item, project, destination)
            if not real or not unsafe_path(real):
                continue
            alias = aliases.destination(real) if destination else aliases.existing(real)
            built = (_gpkg_sink_uri(alias, table) if table else alias + rest) if alias else None
            if not built or not fits_path(alias):
                reason = aliases.problem or ("its alias would pass the 260 characters Windows opens"
                                             if built else "no short name or link of its folder could be made")
                aliases.remove()
                return parameters, [], None, {
                    "_error": (f"{key} is {real}, and {provider} cannot open a path with {_what_breaks()} in it; "
                               f"no safe alias of it could be made here: {reason}. Nothing was run."),
                    "code": "INVALID_ARGS",
                    "suggestion": "",
                    "hint": "processing_path_unsafe",
                    "parameter": key,
                    "provider": provider,
                    "path": real,
                    "reason": reason,
                    **({"variant": "windows"} if IS_WINDOWS else {}),
                }
            items[index] = built
            changed = True
            notes.append(f"{key}: {provider} cannot take {_what_breaks()} in a path, so it was handed {alias} "
                         f"for {real} (the same file, nothing was copied)")
        if changed:
            out[key] = items if isinstance(value, (list, tuple)) else items[0]
    if not notes:
        aliases.remove()
        return parameters, [], None, None
    _UNRELEASED.append(aliases)
    return out, notes, aliases, None
