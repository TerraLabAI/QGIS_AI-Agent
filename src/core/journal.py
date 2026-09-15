# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The run log: what each modifying tool call changed, as facts read from QGIS."""













from __future__ import annotations

import os
import time

from qgis.core import QgsVectorLayer



MAX_ITEMS = 20
MAX_FIELDS = 20
MAX_TEXT = 200

_FIELD_KEYS = ("field_name", "field", "fields", "new_name", "old_name", "new_field_name", "target_field",
               "column", "columns", "field_names")
_FIELD_MAP_KEYS = ("attributes", "values")


def _text(value) -> str:
    return str(value if value is not None else "")[:MAX_TEXT]


def _unique(values) -> list:
    return list(dict.fromkeys(values))


def layer_state(layer) -> dict:
    """What a layer holds now: name, provider, file and its stamp, feature count and field names."""
    from .snapshot import _sidecars, _stamp, layer_file_path

    state: dict = {"name": _text(layer.name())}
    try:
        state["provider"] = str(layer.providerType() or "")
    except Exception:  # noqa: BLE001 - a layer that will not say
        state["provider"] = ""
    try:
        path = layer_file_path(layer)
    except Exception:  # noqa: BLE001 - no file known
        path = None
    if path:
        state["path"] = path


        stamps = [_stamp(name) for name in _sidecars(path)]
        state["stamp"] = [list(stamp) if stamp else None for stamp in stamps] if any(stamps) else None
    try:
        state["crs"] = str(layer.crs().authid() or "")
    except Exception:  # noqa: BLE001 - a layer without a CRS
        state["crs"] = ""
    try:
        from .snapshot_style import _style_digest


        state["style"] = _style_digest(layer)
    except Exception:  # noqa: BLE001 - no digest: a restyle goes unnamed, never wrongly named
        state["style"] = None
    if isinstance(layer, QgsVectorLayer):
        try:
            state["subset"] = str(layer.subsetString() or "")
        except Exception:  # noqa: BLE001 - a provider without a filter
            state["subset"] = ""
        if state["provider"] == "memory" or path:
            try:
                count = int(layer.featureCount())
                state["count"] = count if count >= 0 else None
            except Exception:  # noqa: BLE001 - a count the provider refuses
                state["count"] = None
        try:
            state["fields"] = [str(field.name()) for field in layer.fields()][:500]
        except Exception:  # noqa: BLE001 - a stub or a provider without fields
            state["fields"] = []
    return state


def fields_named(args) -> list[str]:
    """The field names a call's arguments name."""
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
    return _unique(names)[:MAX_FIELDS]


def backed_up(snapshot, lid: str, path: str) -> bool:
    """Whether the run's snapshot already holds a copy of this layer's or this path's file."""
    if snapshot is None:
        return False
    if lid and lid in (snapshot.backups or {}):
        return True
    if not path:
        return False
    real = os.path.realpath(path)
    if real in (snapshot.file_backups or {}):
        return True
    return any(os.path.realpath(src) == real for copies in snapshot.backups.values() for src, _dst in copies)


def before_call(project, layer_ids, paths, snapshot=None) -> dict:
    """Main thread, before a modifying call (its backups made): the layers and what the named ones hold."""
    layers = project.mapLayers()
    named = {}
    for lid in _unique(layer_ids)[:MAX_ITEMS]:
        layer = layers.get(lid)
        if layer is not None:
            state = layer_state(layer)
            state["backup"] = backed_up(snapshot, lid, state.get("path") or "")
            named[lid] = state
    return {"ids": {lid: _text(layer.name()) for lid, layer in layers.items()},
            "layers": named,
            "files": {path: {"existed": os.path.isfile(path), "backup": backed_up(snapshot, "", path)}
                      for path in _unique(paths)[:MAX_ITEMS]}}


def after_call(project, tool: str, args, before: dict, layer_ids, paths, ok: bool) -> dict | None:
    """Main thread, once the call answered: its log entry, or None when it changed nothing it can name."""
    layers = project.mapLayers()
    ids_before = before.get("ids") or {}
    items: list[dict] = []
    for lid in [lid for lid in layers if lid not in ids_before][:MAX_ITEMS]:
        now = layer_state(layers[lid])
        items.append({"id": lid, "name": now["name"], "what": "added", "after": now.get("count"),
                      "provider": now.get("provider", ""), "path": now.get("path") or ""})
    for lid in [lid for lid in ids_before if lid not in layers][:MAX_ITEMS]:
        then = (before.get("layers") or {}).get(lid) or {}
        items.append({"id": lid, "name": then.get("name") or ids_before.get(lid) or lid, "what": "removed",
                      "before": then.get("count"), "provider": then.get("provider", ""),
                      "path": then.get("path") or ""})
    touched = fields_named(args)
    for lid in _unique([*(before.get("layers") or {}), *layer_ids])[:MAX_ITEMS]:
        then = (before.get("layers") or {}).get(lid)
        if then is None or lid not in layers:
            continue
        now = layer_state(layers[lid])
        item = {"id": lid, "name": now["name"], "what": "changed", "provider": now.get("provider", ""),
                "path": now.get("path") or ""}
        if then.get("count") is not None and now.get("count") is not None and then["count"] != now["count"]:
            item.update(what="features", before=then["count"], after=now["count"])
        was, has = then.get("fields") or [], now.get("fields") or []
        fields = _unique([*[f for f in has if f not in was], *[f for f in was if f not in has],
                          *[f for f in touched if f in has or f in was]])[:MAX_FIELDS]
        if fields:
            item["fields"] = fields
            if item["what"] == "changed":
                item["what"] = "fields"
        if item["path"]:
            item["file_changed"] = then.get("stamp") != now.get("stamp")
            item["backup"] = bool(then.get("backup"))
        if item["what"] == "changed" and not item.get("file_changed") and all(
                then.get(key) == now.get(key) for key in ("name", "crs", "style", "subset")):
            continue
        items.append(item)
    known = before.get("files") or {}
    files = [{"path": path, "existed": bool((known.get(path) or {}).get("existed")),
              "backup": bool((known.get(path) or {}).get("backup"))}
             for path in _unique(paths)[:MAX_ITEMS] if os.path.exists(path)]
    if not items and not files:
        return None
    try:
        project_file = project.fileName() or ""
    except Exception:  # noqa: BLE001 - no project name
        project_file = ""
    return {"tool": _text(tool), "at": round(time.time(), 3), "ok": bool(ok), "project_file": project_file,
            "layers": items, "files": files}
