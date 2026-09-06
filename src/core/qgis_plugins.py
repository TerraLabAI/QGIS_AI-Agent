# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""What QGIS plugins are on this machine, read from this machine."""






















from __future__ import annotations

import configparser
import os
import threading
from typing import Any



_META_KEYS = ("name", "version", "author", "description", "about", "homepage",
              "repository", "tracker", "category", "icon", "experimental", "deprecated")



_ICON_EXT = (".png", ".svg", ".jpg", ".jpeg", ".ico", ".gif", ".xpm")




_LOGO_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "icons", "plugins")


def plugin_logo(folder: str) -> str:
    """The bundled logo for ``folder``, or "" when we ship none for it."""






    name = str(folder or "").strip()
    if not name or name != os.path.basename(name) or name.startswith("."):
        return ""
    path = os.path.join(_LOGO_DIR, name + ".png")
    return path if os.path.isfile(path) else ""


def _plugin_dirs() -> list[str]:
    """Every directory QGIS scans for plugins, absolute and existing."""
    try:
        import qgis.utils as qgis_utils

        paths = list(getattr(qgis_utils, "plugin_paths", None) or [])
    except Exception:  # noqa: BLE001
        return []
    out: list[str] = []
    for path in paths:
        try:
            resolved = os.path.realpath(os.path.expanduser(str(path)))
        except Exception:  # noqa: BLE001  # nosec B112 - a path we cannot resolve is one we skip
            continue
        if resolved and os.path.isdir(resolved) and resolved not in out:
            out.append(resolved)
    return out


def _folder_of(folder: str, roots: list[str]) -> str:
    """The directory of one installed plugin, or "" when it is not on disk."""




    name = str(folder or "").strip()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return ""
    for root in roots:
        candidate = os.path.join(root, name)
        if os.path.isdir(candidate) and os.path.isfile(os.path.join(candidate, "metadata.txt")):
            return candidate
    return ""







_METADATA_CACHE: dict = {}
_METADATA_LOCK = threading.Lock()


def _metadata_of(directory: str) -> dict[str, str]:
    path = os.path.join(directory, "metadata.txt")
    try:
        stat = os.stat(path)
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return {}
    with _METADATA_LOCK:
        cached = _METADATA_CACHE.get(path)
        if cached is not None and cached[0] == stamp:
            return dict(cached[1])
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read(path, encoding="utf-8")
        section = dict(parser["general"]) if parser.has_section("general") else {}
    except Exception:  # noqa: BLE001
        return {}
    meta = {key: str(section.get(key) or "").strip() for key in _META_KEYS if section.get(key)}
    with _METADATA_LOCK:
        _METADATA_CACHE[path] = (stamp, meta)
    return dict(meta)


_WARMED = False


def warm_metadata() -> None:
    """Parse every metadata.txt into the cache, off the main thread."""







    global _WARMED
    if _WARMED:
        return
    _WARMED = True
    roots = _plugin_dirs()

    def parse() -> None:
        for root in roots:
            try:
                names = os.listdir(root)
            except OSError:
                continue
            for name in names:
                directory = os.path.join(root, name)
                if os.path.isfile(os.path.join(directory, "metadata.txt")):
                    _metadata_of(directory)

    try:
        threading.Thread(target=parse, name="ai-agent-plugin-metadata", daemon=True).start()
    except RuntimeError:
        parse()


def _icon_of(directory: str, relative: str) -> str:
    """Absolute path of a plugin's own icon, or "" when there is not one to trust."""






    relative = str(relative or "").strip()
    if not relative:
        return ""
    if relative.startswith(("/", "\\")) or ":" in relative[:3]:
        return ""





    relative = relative.replace("\\", "/")
    candidate = os.path.realpath(os.path.join(directory, *relative.split("/")))
    root = os.path.realpath(directory)
    if not candidate.startswith(root + os.sep):
        return ""
    if os.path.splitext(candidate)[1].lower() not in _ICON_EXT:
        return ""
    return candidate if os.path.isfile(candidate) else ""


def installed_plugins() -> dict[str, dict[str, Any]]:
    """Every plugin QGIS can see here, keyed by its folder name."""






    try:
        import qgis.utils as qgis_utils

        available = list(getattr(qgis_utils, "available_plugins", None) or [])
        loaded = set((getattr(qgis_utils, "plugins", None) or {}).keys())
    except Exception:  # noqa: BLE001
        return {}
    roots = _plugin_dirs()
    out: dict[str, dict[str, Any]] = {}







    seen: dict[str, str] = {}
    for folder in available:
        folder = str(folder or "")
        directory = _folder_of(folder, roots)
        if not directory:
            continue
        real = os.path.realpath(directory)
        twin = seen.get(real)
        if twin is not None:
            if folder not in loaded or twin in loaded:
                continue
            out.pop(twin, None)
        seen[real] = folder
        meta = _metadata_of(directory)
        out[folder] = {
            "folder": folder,
            "name": meta.get("name") or folder,
            "version": meta.get("version", ""),
            "author": meta.get("author", ""),
            "description": meta.get("description", ""),
            "homepage": meta.get("homepage") or meta.get("repository", ""),
            "icon": _icon_of(directory, meta.get("icon", "")),
            "loaded": folder in loaded,
            "experimental": meta.get("experimental", "").lower() in ("true", "yes", "1"),
            "deprecated": meta.get("deprecated", "").lower() in ("true", "yes", "1"),
        }
    return out













_QGIS_MENUS = frozenset({
    "project", "edit", "view", "layer", "settings", "plugins", "vector", "raster",
    "database", "web", "mesh", "processing", "window", "help", "file", "3d views",
})


def _normalise(text: str) -> str:
    return " ".join(str(text or "").replace("&", "").lower().split()).strip(" .\u2026")


def _candidates(folder: str) -> list[str]:
    """The names a plugin's menu might carry: its display name and its folder."""
    names = [_normalise(folder), _normalise(str(folder).replace("_", " ").replace("-", " "))]
    try:
        from qgis.utils import pluginMetadata

        display = _normalise(pluginMetadata(str(folder), "name") or "")
        if display:
            names.insert(0, display)
    except Exception:  # noqa: BLE001
        pass  # nosec B110
    return [n for n in dict.fromkeys(names) if n]


def _search_menu_bar(names: list[str], menu, depth: int = 0):
    """The submenu belonging to a plugin, looked for below the top level too."""








    try:
        actions = list(menu.actions())
    except Exception:  # noqa: BLE001
        return None
    for action in actions:
        try:
            submenu = action.menu()
            title = _normalise(action.text())
        except Exception:  # noqa: BLE001  # nosec B112 - a menu entry that will not answer is one we do not match
            continue
        if submenu is None or not title:
            continue
        if not (depth == 0 and title in _QGIS_MENUS) and len(title) >= 4:
            for name in names:
                if title == name or title in name or name in title:
                    return submenu
        if depth < 2:
            found = _search_menu_bar(names, submenu, depth + 1)
            if found is not None:
                return found
    return None


def menu_of(folder: str):
    """The QMenu a plugin owns, or None."""






    try:
        import qgis.utils as qgis_utils

        plugin = (getattr(qgis_utils, "plugins", None) or {}).get(str(folder))
        if plugin is None:
            return None
        menu = getattr(plugin, "menu", None)
        if menu is not None and hasattr(menu, "actions"):
            return menu
        from qgis.utils import iface

        return _search_menu_bar(_candidates(folder), iface.mainWindow().menuBar())
    except Exception:  # noqa: BLE001
        return None


def explore_menu(menu, prefix: str = "") -> list[str]:
    """Every leaf action path under a QMenu, '/' between levels."""
    items: list[str] = []
    try:
        actions = list(menu.actions())
    except Exception:  # noqa: BLE001
        return items
    for action in actions:
        try:
            if action.isSeparator():
                continue
            submenu = action.menu()
            text = action.text().replace("&", "")
        except Exception:  # noqa: BLE001  # nosec B112 - a menu entry that will not answer is one we do not match
            continue
        if not text:
            continue
        path = f"{prefix}{text}" if prefix else text
        if submenu is not None:
            items.extend(explore_menu(submenu, path + "/"))
        else:
            items.append(path)
    return items


def plugin_actions(folder: str, limit: int = 0) -> list[str]:
    """What the agent can trigger in one plugin, right now. Empty when nothing."""
    menu = menu_of(folder)
    if menu is None:
        return []
    paths = explore_menu(menu)
    return paths[:limit] if limit else paths


class _StartReason:
    """Keeps the traceback QGIS throws away when a plugin refuses to start."""












    def __init__(self):
        self.message = ""
        self._original = None

    def __enter__(self):
        import qgis.utils as qgis_utils

        self._original = getattr(qgis_utils, "showException", None)
        if callable(self._original):
            def capture(etype, value, tb, msg, *_rest, **_kwargs):
                text = str(value or "").strip() or str(msg or "").strip()
                name = getattr(etype, "__name__", "")
                self.message = f"{name}: {text}" if name and text else (text or name)

            qgis_utils.showException = capture
        return self

    def __exit__(self, *_exc):
        if self._original is not None:
            import qgis.utils as qgis_utils

            qgis_utils.showException = self._original
            self._original = None
        return False


def enable_plugin(folder: str) -> tuple[bool, str]:
    """Switch a plugin on in QGIS, the way its own plugin manager does."""














    name = str(folder or "").strip()
    if not name:
        return False, ""
    try:
        import qgis.utils as qgis_utils

        if name in (getattr(qgis_utils, "plugins", None) or {}):
            return True, ""
        with _StartReason() as reason:
            if not qgis_utils.loadPlugin(name):
                return False, reason.message
            started = bool(qgis_utils.startPlugin(name))
        if not started:
            return False, reason.message
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    try:
        from qgis.core import QgsSettings

        QgsSettings().setValue("PythonPlugins/" + name, True)
    except Exception:  # noqa: BLE001
        pass  # nosec B110 - it is running now; it just may not come back next start
    return True, ""
