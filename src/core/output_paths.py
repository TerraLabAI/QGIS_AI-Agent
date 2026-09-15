# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a file lands when the model names it the way a person would."""













from __future__ import annotations

import os
import re
import unicodedata

DEFAULT_SUBFOLDER = "TerraLab exports"


_ALIASES = {
    "desktop": ("desktop", "area de trabalho", "bureau", "escritorio", "schreibtisch", "scrivania",
                "bureaublad", "pulpit"),
    "documents": ("documents", "documentos", "meus documentos", "mis documentos", "mes documents",
                  "my documents", "dokumente", "eigene dokumente", "documenti"),
    "downloads": ("downloads", "transferencias", "telechargements", "descargas"),
    "pictures": ("pictures", "imagens", "imagenes", "bilder", "immagini", "my pictures"),
}
_QT_LOCATION = {"desktop": "DesktopLocation", "documents": "DocumentsLocation",
                "downloads": "DownloadLocation", "pictures": "PicturesLocation"}
_FALLBACK = {"desktop": "Desktop", "documents": "Documents", "downloads": "Downloads", "pictures": "Pictures"}



_DEFAULT_FILE = {
    "export_layer": ("layer_name", ".gpkg"),
    "save_layer_to_gpkg": ("layer", ".gpkg"),
    "export_layout": ("layout_name", ".pdf"),
}
_LAYOUT_EXT = {"pdf": ".pdf", "png": ".png", "jpg": ".jpg", "jpeg": ".jpg", "svg": ".svg", "tif": ".tif"}


_NOT_A_FILE = frozenset({"memory", "memory:", "temp", "temporary_output"})
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]+:")
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_USER_SUFFIX = re.compile(r"\s+-\s+[^\\/]+$")

_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?=\.|$)", re.IGNORECASE)


def fold(name: str) -> str:
    """*name* without accents, case, or the difference between spaces, _, - and dots."""
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[\s_\-.]+", " ", text).strip()


def safe_file_name(name: str, fallback: str = "export") -> str:
    """*name* usable as a file name on every system QGIS runs on."""




    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(name or "")).strip(" .")
    cleaned = _RESERVED.sub(r"\1_", cleaned)
    return cleaned or fallback


def standard_folder(kind: str) -> str:
    """The real Desktop, Documents, Downloads or Pictures folder of this user."""
    try:
        from qgis.PyQt.QtCore import QStandardPaths

        from .qt_compat import enum_member

        location = QStandardPaths.writableLocation(
            enum_member(QStandardPaths, "StandardLocation", _QT_LOCATION[kind]))
        if location:
            return os.path.normpath(location)
    except Exception:  # nosec B110 - no Qt here: the conventional folder under home
        pass
    return os.path.join(os.path.expanduser("~"), _FALLBACK[kind])


def default_folder() -> str:
    """Next to the saved project, else Documents/TerraLab exports."""
    from . import security

    return security.project_dir() or os.path.join(standard_folder("documents"), DEFAULT_SUBFOLDER)


def _alias_kind(part: str) -> str:
    folded = fold(part)
    for candidate in (folded, fold(_USER_SUFFIX.sub("", part))):
        for kind, names in _ALIASES.items():
            if candidate in names:
                return kind
    return ""


def _is_absolute(text: str) -> bool:
    return os.path.isabs(text) or bool(_DRIVE.match(text)) or text.startswith(("\\\\", "//"))


def _redirected(path: str) -> str:
    """An absolute home/Desktop/..."""





    folder = os.path.dirname(path)
    if not folder or os.path.isdir(folder):
        return ""
    home = os.path.expanduser("~")
    here, root = os.path.normcase(os.path.normpath(path)), os.path.normcase(os.path.normpath(home))
    if not here.startswith(root + os.sep):
        return ""
    parts = [p for p in re.split(r"[\\/]+", os.path.normpath(path)[len(os.path.normpath(home)):]) if p]
    kind = _alias_kind(parts[0]) if len(parts) > 1 else ""
    if not kind:
        return ""
    base = standard_folder(kind)
    moved = os.path.join(base, *parts[1:])
    return moved if os.path.isdir(base) and os.path.normcase(moved) != here else ""


def resolve(value, default_name: str = ""):
    """(path, how) for a path the model wrote."""



    if not isinstance(value, str):
        return value, ""
    text = value.strip()
    if not text or text.lower() in _NOT_A_FILE or text.lower().startswith("/vsi"):
        return value, ""
    if text.startswith("~"):
        text = os.path.expanduser(text)
    if _is_absolute(text):
        moved = _redirected(text)
        path, how = (moved, "known_folder") if moved else (text, "")
        if default_name and os.path.isdir(path):
            return os.path.join(path, default_name), how or "default_file"
        return (path, how) if how or path != value else (value, "")
    if _SCHEME.match(text):
        return value, ""
    parts = [p for p in re.split(r"[\\/]+", text) if p not in ("", ".")]
    if not parts or ".." in parts:
        return value, ""
    kind = _alias_kind(parts[0])
    if kind:
        base, rest, how = standard_folder(kind), parts[1:], "known_folder"
    else:
        base, rest, how = default_folder(), parts, "default_folder"
    path = os.path.join(base, *rest)
    if default_name and (not rest or os.path.isdir(path)):
        path = os.path.join(path, default_name)
    return path, how


def _default_name(name: str, args: dict) -> str:
    source, ext = _DEFAULT_FILE.get(name, ("", ""))
    if not source:
        return ""
    if name == "export_layout":
        ext = _LAYOUT_EXT.get(str(args.get("format") or "").lower(), ext)
    return safe_file_name(str(args.get(source) or "")) + ext


def resolve_write_paths(name: str, args: dict, write_path_args: dict) -> list[dict]:
    """Rewrite, in place, every write path of this call."""





    changes: list[dict] = []
    if not isinstance(args, dict):
        return changes
    for key in write_path_args.get(name, ()):
        before = args.get(key)
        if not isinstance(before, str) or not before.strip():
            continue
        after, how = resolve(before, _default_name(name, args))
        if after != before:
            args[key] = after
            changes.append({"arg": key, "from": before, "to": after, "how": how})
    if name == "batch_commands":
        for command in args.get("commands") or []:
            if isinstance(command, dict) and isinstance(command.get("arguments"), dict):
                inner = str(command.get("name") or "")
                if inner and inner != "batch_commands":
                    changes += resolve_write_paths(inner, command["arguments"], write_path_args)
    return changes




_MAX_SIBLINGS = 5000


def _distance(a: str, b: str) -> int:
    """Levenshtein distance between two short strings."""
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def close_names(asked: str, existing: str) -> bool:
    """True when *existing* is what someone typing *asked* most likely meant."""





    a, b = fold(asked), fold(existing)
    if not a or not b or asked == existing:
        return False
    if a == b or a.replace(" ", "") == b.replace(" ", ""):
        return True
    short, long_ = sorted((a.split(), b.split()), key=len)
    if len(long_) == len(short) + 1 and len(short) >= 2:
        if any(long_[:i] + long_[i + 1:] == short for i in range(len(long_))):
            return True
    if re.findall(r"\d+", a) != re.findall(r"\d+", b) or min(len(a), len(b)) < 5:
        return False
    return _distance(a, b) <= (1 if max(len(a), len(b)) <= 8 else 2)


def _close_sibling(parent: str, part: str, project: str) -> str:
    try:
        with os.scandir(parent) as entries:
            names = []
            for index, entry in enumerate(entries):
                if index >= _MAX_SIBLINGS:
                    break
                try:
                    if entry.is_dir():
                        names.append(entry.name)
                except OSError:
                    continue
    except OSError:
        return ""
    matches = [n for n in names if close_names(part, n)]
    if len(matches) > 1 and project:

        target = os.path.normcase(os.path.normpath(project))
        on_path = [n for n in matches
                   if (target + os.sep).startswith(os.path.normcase(os.path.join(parent, n)) + os.sep)]
        matches = on_path or matches
    return matches[0] if len(matches) == 1 else ""


def near_existing_folder(folder: str) -> str:
    """The existing folder a missing *folder* was most likely meant to be, or ""."""






    if not isinstance(folder, str) or not folder.strip():
        return ""
    from . import security

    current = os.path.normpath(folder)
    missing: list[str] = []
    while not os.path.isdir(current):
        parent = os.path.dirname(current)
        if not parent or parent == current:
            return ""
        missing.append(os.path.basename(current))
        current = parent
    project = security.project_dir()
    swapped = False
    for index, part in enumerate(reversed(missing)):
        exact = os.path.join(current, part)
        if os.path.isdir(exact):
            current = exact
            continue
        sibling = _close_sibling(current, part, project)
        if not sibling:
            current = os.path.join(current, *list(reversed(missing))[index:])
            break
        current, swapped = os.path.join(current, sibling), True
    return current if swapped else ""
