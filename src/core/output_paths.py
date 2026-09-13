# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later














from __future__ import annotations

import os
import re
import unicodedata

from . import host_platform
from .host_platform import expand_leading_env

DEFAULT_SUBFOLDER = "TerraLab exports"


_ALIASES = {
    "desktop": ("desktop", "area de trabalho", "bureau", "escritorio", "schreibtisch", "scrivania",
                "bureaublad", "pulpit"),
    "documents": ("documents", "documentos", "meus documentos", "mis documentos", "mes documents",
                  "my documents", "dokumente", "eigene dokumente", "documenti"),
    "downloads": ("downloads", "transferencias", "telechargements", "descargas"),
    "pictures": ("pictures", "imagens", "imagenes", "bilder", "immagini", "my pictures", "images",
                 "mes images"),
}
_QT_LOCATION = {"desktop": "DesktopLocation", "documents": "DocumentsLocation",
                "downloads": "DownloadLocation", "pictures": "PicturesLocation"}
_FALLBACK = {"desktop": "Desktop", "documents": "Documents", "downloads": "Downloads", "pictures": "Pictures"}




_DEFAULT_FILE = {
    "export_layer": ("layer_name", ".gpkg", ""),
    "export_3d_model": ("layer_name", ".obj", ""),
    "save_layer_to_gpkg": ("layer", ".gpkg", ""),
    "export_layout": ("layout_name", ".pdf", "format"),
    "write_html_report": ("title", ".html", ""),
}



_FILLED_WHEN_ABSENT = frozenset({"write_html_report"})
_LAYOUT_EXT = {"pdf": ".pdf", "png": ".png", "jpg": ".jpg", "jpeg": ".jpg", "svg": ".svg", "tif": ".tif"}


_NOT_A_FILE = frozenset({"memory", "memory:", "temp", "temporary_output"})
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]+:")
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_USER_SUFFIX = re.compile(r"\s+-\s+[^\\/]+$")

_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?=\.|$)", re.IGNORECASE)


def fold(name: str) -> str:

    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[\s_\-.]+", " ", text).strip()


def safe_file_name(name: str, fallback: str = "export") -> str:





    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(name or "")).strip(" .")
    cleaned = _RESERVED.sub(r"\1_", cleaned)
    return cleaned or fallback


def standard_folder(kind: str) -> str:

    try:
        from qgis.PyQt.QtCore import QStandardPaths

        from .qt_compat import enum_member

        location = QStandardPaths.writableLocation(
            enum_member(QStandardPaths, "StandardLocation", _QT_LOCATION[kind]))
        if location:
            return os.path.normpath(location)
    except Exception:  # nosec B110
        pass
    return os.path.join(os.path.expanduser("~"), _FALLBACK[kind])


def default_folder() -> str:

    from . import security

    return security.project_dir() or exports_folder()


def exports_folder() -> str:











    import tempfile

    chosen = os.environ.get("QGIS_AI_AGENT_EXPORTS", "").strip()
    if chosen:
        return os.path.normpath(os.path.expanduser(chosen))
    for base in (standard_folder("documents"), os.path.expanduser("~"), tempfile.gettempdir()):
        if base and os.path.isdir(base):
            return os.path.join(base, DEFAULT_SUBFOLDER)
    return os.path.join(tempfile.gettempdir(), DEFAULT_SUBFOLDER)


def _alias_kind(part: str) -> str:
    folded = fold(part)
    for candidate in (folded, fold(_USER_SUFFIX.sub("", part))):
        for kind, names in _ALIASES.items():
            if candidate in names:
                return kind
    return ""


def _is_absolute(text: str) -> bool:
    return os.path.isabs(text) or bool(_DRIVE.match(text)) or text.startswith(("\\\\", "//"))


def _user_roots() -> list:

    roots = [os.path.expanduser("~")]
    for name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        value = os.environ.get(name, "").strip()
        if value and value not in roots:
            roots.append(value)
    return roots


def _redirected(path: str) -> str:








    folder = os.path.dirname(path)
    if not folder or os.path.isdir(folder):
        return ""
    here = os.path.normcase(os.path.normpath(path))
    for root in _user_roots():
        top = os.path.normcase(os.path.normpath(root))
        if not here.startswith(top + os.sep):
            continue
        parts = [p for p in re.split(r"[\\/]+", os.path.normpath(path)[len(os.path.normpath(root)):]) if p]
        kind = _alias_kind(parts[0]) if len(parts) > 1 else ""
        if not kind:
            continue
        base = standard_folder(kind)
        moved = os.path.join(base, *parts[1:])
        if os.path.isdir(base) and os.path.normcase(moved) != here:
            return moved
    return ""


def resolve(value, default_name: str = ""):




    if not isinstance(value, str):
        return value, ""
    text = value.strip()
    if not text or text.lower() in _NOT_A_FILE or text.lower().startswith("/vsi"):
        return value, ""
    if posix_root_problem(text):


        return value, ""
    if text.startswith("~"):
        text = os.path.expanduser(text)



    text = expand_leading_env(text)
    from . import security

    if _is_absolute(text):
        if security.refused_share(text):


            return text, ""
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
    if security.refused_share(path):
        return path, how
    if default_name and (not rest or os.path.isdir(path)):
        path = os.path.join(path, default_name)
    return path, how


def posix_root_problem(path) -> str:






    if not host_platform.IS_WINDOWS:
        return ""
    text = str(path or "").strip()
    if not text.startswith("/") or text.startswith("//") or text.lower().startswith("/vsi"):
        return ""
    folder = exports_folder()
    example = os.path.join(folder, safe_file_name(os.path.basename(text.rstrip("/")), "export.gpkg"))
    return (f"{text} is a Linux or macOS path, and this computer runs Windows: it would land at the root of "
            f"the drive. A bare file name lands in {folder}; a full Windows path looks like "
            f"{example}.")


def _default_name(name: str, args: dict) -> str:
    source, ext, format_arg = _DEFAULT_FILE.get(name, ("", "", ""))
    if not source:
        return ""
    if format_arg:
        ext = _LAYOUT_EXT.get(str(args.get(format_arg) or "").lower(), ext)
    return safe_file_name(str(args.get(source) or "")) + ext


def resolve_write_paths(name: str, args: dict, write_path_args: dict) -> list[dict]:

    changes: list[dict] = []
    if not isinstance(args, dict):
        return changes
    for key in write_path_args.get(name, ()):
        before = args.get(key)
        if not isinstance(before, str) or not before.strip():
            if name in _FILLED_WHEN_ABSENT and _default_name(name, args):
                args[key] = os.path.join(default_folder(), _default_name(name, args))
                changes.append({"arg": key, "from": before, "to": args[key], "how": "default_folder"})
            continue
        after, how = resolve(before, _default_name(name, args))
        if after != before:
            args[key] = after
            changes.append({"arg": key, "from": before, "to": after, "how": how})
    return changes


def resolve_processing_outputs(params: dict, output_keys) -> list[dict]:









    changes: list[dict] = []
    if not isinstance(params, dict) or not output_keys:
        return changes
    for key in output_keys:
        before = params.get(key)
        if not isinstance(before, str):
            continue
        table = _gpkg_table(before)
        if table:


            make_exports_folder(table)
            continue
        if "|" in before:
            continue
        extension = os.path.splitext(before.strip())[1]
        if len(extension) < 2 or not extension[1:].isalnum():
            continue
        after, how = resolve(before)
        if after != before:
            params[key] = after
            changes.append({"arg": f"parameters.{key}", "from": before, "to": after, "how": how})
        make_exports_folder(after)
    return changes


def _gpkg_table(value: str) -> str:

    try:
        from ..tools.processing_destinations import _gpkg_table_target
    except Exception:  # noqa: BLE001
        return ""
    target = _gpkg_table_target(value)
    return str(target[0]) if target else ""


def make_exports_folder(path) -> bool:








    if not isinstance(path, str) or not _is_absolute(path.strip()):
        return False
    from . import security

    folder = os.path.dirname(os.path.abspath(path.strip()))
    if not folder or security.refused_share(folder) or os.path.isdir(folder):
        return False
    if os.path.basename(folder).casefold() != DEFAULT_SUBFOLDER.casefold():
        return False
    if not os.path.isdir(os.path.dirname(folder)):
        return False
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return False
    return True




_MAX_SIBLINGS = 5000


def _distance(a: str, b: str) -> int:

    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def close_names(asked: str, existing: str) -> bool:






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







    if not isinstance(folder, str) or not folder.strip():
        return ""
    from . import security

    current = os.path.normpath(folder)
    missing: list[str] = []
    while security.refused_share(current) or not os.path.isdir(current):
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
