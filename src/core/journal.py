# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
















from __future__ import annotations

import os
import time
import unicodedata

from qgis.core import QgsVectorLayer

from . import tuning



MAX_ITEMS = 20
MAX_FIELDS = 20
MAX_TEXT = 200





def _items() -> int:
    return tuning.ceiling("journal_max_items", MAX_ITEMS, 5)


def _fields() -> int:
    return tuning.ceiling("journal_max_fields", MAX_FIELDS, 5)



_FIELD_KEYS = ("field_name", "field", "fields", "new_name", "old_name", "new_field_name", "target_field",
               "column", "columns", "field_names")
_FIELD_MAP_KEYS = ("attributes", "values")


def _text(value) -> str:
    return str(value if value is not None else "")[:tuning.ceiling("journal_max_text", MAX_TEXT, 50)]


def _unique(values) -> list:
    return list(dict.fromkeys(values))




_STAT_ALONE = 16
_STAT_THREADS = 8


class _Disk:










    def __init__(self, paths, stamps: bool = True):
        from .snapshot import _sidecars, _stamp

        self._files: dict[str, list[str] | None] = {}
        self._stamps: dict[str, tuple | None] = {}
        paths = list(dict.fromkeys(p for p in paths if p))
        per_folder: dict[str, int] = {}
        for path in paths:
            folder = os.path.dirname(path) or "."
            per_folder[folder] = per_folder.get(folder, 0) + 1
        listings: dict[str, _Listing | None] = {}
        for path in paths:
            folder = os.path.dirname(path) or "."
            if folder not in listings:


                listings[folder] = _listing(folder) if per_folder[folder] > 1 else None
            names = listings[folder]
            if names is None:
                self._files[path] = _sidecars(path) if os.path.isfile(path) else None
            elif names.get(_name_key(os.path.basename(path))):
                self._files[path] = _sidecars(path, names)
            else:
                self._files[path] = None
        if not stamps:
            return
        wanted = list(dict.fromkeys(name for group in self._files.values() for name in group or ()))
        if len(wanted) <= _STAT_ALONE:
            stamps = [_stamp(name) for name in wanted]
        else:
            from concurrent.futures import ThreadPoolExecutor


            with ThreadPoolExecutor(max_workers=_STAT_THREADS) as pool:
                stamps = list(pool.map(_stamp, wanted))
        self._stamps = dict(zip(wanted, stamps))

    def is_file(self, path: str) -> bool:
        return self._files.get(path) is not None

    def stamps(self, path: str) -> list | None:

        stamps = [self._stamps.get(name) for name in self._files.get(path) or ()]
        return [list(stamp) if stamp else None for stamp in stamps] if any(stamps) else None


def _name_key(name: str) -> str:
    return os.path.normcase(name)


class _Listing(dict):





    def __init__(self, entries, folder: str):
        super().__init__(entries)
        self._folder = folder
        self._folded = {unicodedata.normalize("NFC", name).casefold() for name in self}

    def get(self, key, default=None):
        if key in self:
            return self[key]
        if unicodedata.normalize("NFC", key).casefold() in self._folded:
            return os.path.isfile(os.path.join(self._folder, key))
        return default


def _listing(folder: str) -> _Listing | None:

    try:
        with os.scandir(folder) as entries:
            return _Listing({_name_key(entry.name): entry.is_file() for entry in entries}, folder)
    except OSError:
        return None


def _paths(layers, files: bool) -> dict:

    from .snapshot import source_path

    paths = {}
    if not files:
        return paths
    for lid, layer in layers:
        try:
            paths[lid] = source_path(layer)
        except Exception:  # noqa: BLE001
            paths[lid] = None
    return paths


def layer_state(layer, files: bool = True, disk: _Disk | None = None) -> dict:






    from .snapshot import source_path

    state: dict = {"name": _text(layer.name())}
    try:
        state["provider"] = str(layer.providerType() or "")
    except Exception:  # noqa: BLE001
        state["provider"] = ""
    try:
        path = source_path(layer)
    except Exception:  # noqa: BLE001
        path = None
    if path and files:
        if disk is None:
            disk = _Disk([path])
        if not disk.is_file(path):
            path = None
    if path:
        state["path"] = path
    if path and files:


        state["stamp"] = disk.stamps(path)
    try:
        state["crs"] = str(layer.crs().authid() or "")
    except Exception:  # noqa: BLE001
        state["crs"] = ""
    try:
        from .snapshot_style import style_mark



        state["style"] = style_mark(layer)
    except Exception:  # noqa: BLE001
        state["style"] = None
    if isinstance(layer, QgsVectorLayer):
        try:
            state["subset"] = str(layer.subsetString() or "")
        except Exception:  # noqa: BLE001
            state["subset"] = ""
        if state["provider"] == "memory" or path:
            try:
                count = int(layer.featureCount())
                state["count"] = count if count >= 0 else None
            except Exception:  # noqa: BLE001
                state["count"] = None
        try:
            state["fields"] = [str(field.name()) for field in layer.fields()][:500]
        except Exception:  # noqa: BLE001
            state["fields"] = []
    return state


def _same_style(layer, then, now) -> bool:
    try:
        from .snapshot_style import same_style

        return same_style(layer, then, now)
    except Exception:  # noqa: BLE001
        return then == now


def fields_named(args) -> list[str]:

    names: list[str] = []
    if not isinstance(args, dict):
        return names
    for key in _FIELD_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value:
            names.append(value)
        elif isinstance(value, (list, tuple)):
            names.extend(str(item) for item in value if isinstance(item, str) and item)
    for key in _FIELD_MAP_KEYS:
        value = args.get(key)
        if isinstance(value, dict):
            names.extend(str(name) for name in value if isinstance(name, str))
    return _unique(names)[:_fields()]


def backup_check(snapshot):








    if snapshot is None:
        return lambda lid, path: False
    copied = None

    def backed_up(lid: str, path: str) -> bool:
        nonlocal copied
        if lid and lid in (snapshot.backups or {}):
            return True
        if not path:
            return False
        if copied is None:
            copied = snapshot.copied_files()
        return snapshot.real_path(path) in copied

    return backed_up


def watched_layer_ids(project, files: bool = True) -> list:







    from .snapshot import source_path

    layers = list(project.mapLayers().items())
    paths = _paths(layers, files)
    disk = _Disk(paths.values(), stamps=False) if files else None
    ids = []
    for lid, layer in layers:
        try:
            path = paths.get(lid) if files else source_path(layer)
            if layer.providerType() == "memory" or (path and (disk is None or disk.is_file(path))):
                ids.append(lid)
        except Exception:  # noqa: BLE001  # nosec B112
            continue
    return ids


def before_call(project, layer_ids, paths, snapshot=None, files: bool = True) -> dict:










    layers = project.mapLayers()
    backed_up = backup_check(snapshot)
    named = {}
    walked = [(lid, layers[lid]) for lid in _unique(layer_ids) if layers.get(lid) is not None]
    disk = _Disk(_paths(walked, files).values()) if files else None
    for lid, layer in walked:
        if layer is not None:
            state = layer_state(layer, files, disk)
            state["backup"] = backed_up(lid, state.get("path") or "") if files else False
            named[lid] = state
    return {"ids": {lid: _text(layer.name()) for lid, layer in layers.items()},
            "layers": named, "files_read": files,
            "files": {path: {"existed": os.path.isfile(path), "backup": backed_up("", path)}
                      for path in _unique(paths)[:_items()]}}


def after_call(project, tool: str, args, before: dict, layer_ids, paths, ok: bool) -> dict | None:

    layers = project.mapLayers()
    ids_before = before.get("ids") or {}
    items: list[dict] = []
    added = [lid for lid in layers if lid not in ids_before][:_items()]
    files_read = before.get("files_read", True)
    named = [lid for lid in _unique([*(before.get("layers") or {}), *layer_ids])
             if (before.get("layers") or {}).get(lid) is not None and lid in layers]


    disk = _Disk([*_paths([(lid, layers[lid]) for lid in added], True).values(),
                  *_paths([(lid, layers[lid]) for lid in named], files_read).values()])
    for lid in added:
        now = layer_state(layers[lid], True, disk)
        items.append({"id": lid, "name": now["name"], "what": "added", "after": now.get("count"),
                      "provider": now.get("provider", ""), "path": now.get("path") or ""})
    for lid in [lid for lid in ids_before if lid not in layers][:_items()]:
        then = (before.get("layers") or {}).get(lid) or {}
        items.append({"id": lid, "name": then.get("name") or ids_before.get(lid) or lid, "what": "removed",
                      "before": then.get("count"), "provider": then.get("provider", ""),
                      "path": then.get("path") or ""})
    touched = fields_named(args)
    for lid in _unique([*(before.get("layers") or {}), *layer_ids]):
        if len(items) >= _items():
            break
        then = (before.get("layers") or {}).get(lid)
        if then is None or lid not in layers:
            continue
        now = layer_state(layers[lid], files_read, disk)
        item = {"id": lid, "name": now["name"], "what": "changed", "provider": now.get("provider", ""),
                "path": now.get("path") or ""}
        if then.get("count") is not None and now.get("count") is not None and then["count"] != now["count"]:
            item.update(what="features", before=then["count"], after=now["count"])
        was, has = then.get("fields") or [], now.get("fields") or []
        fields = _unique([*[f for f in has if f not in was], *[f for f in was if f not in has],
                          *[f for f in touched if f in has or f in was]])[:_fields()]
        if fields:
            item["fields"] = fields
            if item["what"] == "changed":
                item["what"] = "fields"
        if item["path"]:
            item["file_changed"] = then.get("stamp") != now.get("stamp")
            item["backup"] = bool(then.get("backup"))
        if item["what"] == "changed" and not item.get("file_changed") and all(
                then.get(key) == now.get(key) for key in ("name", "crs", "subset")) and _same_style(
                layers[lid], then.get("style"), now.get("style")):
            continue
        items.append(item)
    known = before.get("files") or {}
    files = [{"path": path, "existed": bool((known.get(path) or {}).get("existed")),
              "backup": bool((known.get(path) or {}).get("backup"))}
             for path in _unique(paths)[:_items()] if os.path.exists(path)]
    if not items and not files:
        return None
    try:
        project_file = project.fileName() or ""
    except Exception:  # noqa: BLE001
        project_file = ""
    return {"tool": _text(tool), "at": round(time.time(), 3), "ok": bool(ok), "project_file": project_file,
            "layers": items, "files": files}
