# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

import re

from qgis.core import QgsProject

from .context_layers import _cap

MAX_HISTORY_ENTRIES = 5









_HISTORY_FULL_S = 600.0
_history: dict = {"rows": None, "limit": 0, "newest": None, "newest_id": -1, "read_at": 0.0}


def _history_row(entry) -> tuple | None:
    details = getattr(entry, "entry", None) or {}
    if not isinstance(details, dict):
        return None
    algorithm = str(details.get("algorithm_id") or "").strip()
    if not algorithm:
        return None
    params = details.get("parameters")
    if isinstance(params, dict) and isinstance(params.get("inputs"), dict):
        params = params["inputs"]
    stamp = getattr(entry, "timestamp", None)
    at = stamp.toString("yyyy-MM-dd HH:mm") if stamp is not None and hasattr(stamp, "toString") else ""
    return (algorithm, params if isinstance(params, dict) else None, at)


def _newest_rows(entries, limit: int) -> list[tuple]:
    rows: list[tuple] = []
    for entry in reversed(entries):
        row = _history_row(entry)
        if row is not None:
            rows.append(row)
            if len(rows) >= limit:
                break
    return rows


def _history_rows(registry, limit: int) -> list[tuple]:

    import time

    from qgis.PyQt.QtCore import QDateTime

    now = time.monotonic()
    cached, newest = _history["rows"], _history["newest"]
    if (cached is not None and newest is not None and _history["limit"] == limit
            and now - _history["read_at"] < _HISTORY_FULL_S):
        fresh = [entry for entry in registry.queryEntries(newest, QDateTime(), "processing")
                 if getattr(entry, "id", -1) > _history["newest_id"]]
        if not fresh:
            return cached
        rows = (_newest_rows(fresh, limit) + cached)[:limit]
        entries = fresh
    else:
        entries = list(registry.queryEntries(providerId="processing"))
        rows = _newest_rows(entries, limit)
        _history["read_at"] = now
    last = entries[-1] if entries else None
    last_id = getattr(last, "id", None) if last is not None else None
    stamp = getattr(last, "timestamp", None) if last is not None else None
    if isinstance(last_id, int) and stamp is not None:
        _history.update(rows=rows, limit=limit, newest=QDateTime(stamp), newest_id=last_id)
    else:
        _history.update(rows=None, newest=None, newest_id=-1)
    return rows


def _recent_processing() -> list[dict]:




    try:
        from qgis.gui import QgsGui

        registry = QgsGui.historyProviderRegistry()
        rows = _history_rows(registry, _cap("max_history_entries", MAX_HISTORY_ENTRIES))
    except Exception:  # nosec B110
        return []
    out: list[dict] = []
    names = _layer_names_by_source(QgsProject.instance()) if rows else {}
    for algorithm, params, at in rows:
        item: dict = {"algorithm": algorithm}
        if params:
            item["parameters"] = history_parameters(params, names)
        if at:
            item["at"] = at
        out.append(item)
    return out


MAX_HISTORY_PARAMETERS = 12

_EMPTY_PARAMETER_VALUES = ("", "None", "NULL", "[]", "{}")
_MEMORY_SOURCE = "memory://"
GONE_MEMORY_LAYER = "memory layer no longer in the project"


_BARE_SOURCE_PROVIDERS = ("gdal", "ogr", "mdal")


def _processing_identifier(layer, source: str) -> str:





    rule = source.replace("\\", "/").strip()
    try:
        provider = str(layer.providerType() or "")
    except Exception:  # noqa: BLE001
        return rule
    if provider and provider.lower() not in _BARE_SOURCE_PROVIDERS:
        rule = f"{provider}://{rule}"
    try:
        from qgis.core import QgsProcessingUtils

        found = QgsProcessingUtils.layerToStringIdentifier(layer)
    except Exception:  # noqa: BLE001
        return rule
    return found if isinstance(found, str) and found else rule


def _layer_names_by_source(project) -> dict:









    exact: dict = {}
    by_path: dict = {}
    try:
        layers = list(project.mapLayers().values())
    except Exception:  # nosec B110
        return {}
    for layer in layers:
        try:
            source, name = str(layer.source() or ""), str(layer.name() or "")
        except Exception:  # nosec B112
            continue
        if not source or not name:
            continue
        exact.setdefault(source, name)
        exact.setdefault(_processing_identifier(layer, source), name)
        path = source.split("|", 1)[0]
        if path != source:
            by_path.setdefault(path, set()).add(name)
    for path, found in by_path.items():
        if len(found) == 1 and path not in exact:
            exact[path] = next(iter(found))
    return exact


def _parameter_source(value) -> str:

    if isinstance(value, dict):
        source = value.get("source")
        if isinstance(source, dict):
            source = source.get("val")
        return str(source) if source is not None else str(value)
    return "" if value is None else str(value)


def history_parameters(params: dict, names: dict) -> dict:







    out: dict = {}
    for key, value in params.items():
        text = _parameter_source(value)
        if value is None or text.strip() in _EMPTY_PARAMETER_VALUES:
            continue
        name = names.get(text)
        if name is None and text.startswith(_MEMORY_SOURCE):
            name = GONE_MEMORY_LAYER
        out[str(key)] = name if name is not None else _short_param(value)
        if len(out) >= MAX_HISTORY_PARAMETERS:
            break
    return out


MAX_LOG_LINES = 3
MAX_LOG_CHARS = 200
_LOG_RING_PROPERTY = "_aiagent_msglog_buf"
_OWN_LOG_TAG = "AI Agent"


def _recent_log_problems() -> list[dict]:





    try:
        from qgis.core import QgsApplication

        ring = QgsApplication.instance().property(_LOG_RING_PROPERTY)
    except Exception:  # nosec B110
        return []
    if not ring:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for item in reversed(list(ring)):
        if not isinstance(item, dict) or item.get("level") not in ("warning", "critical"):
            continue
        if str(item.get("tag") or "") == _OWN_LOG_TAG:
            continue
        text = " ".join(str(item.get("message") or "").split())[:_cap("max_log_chars", MAX_LOG_CHARS)]
        if not text or text in seen:
            continue
        seen.add(text)
        out.append({"level": item.get("level"), "tag": str(item.get("tag") or ""), "message": text,
                    "at": str(item.get("timestamp") or "")})
        if len(out) >= _cap("max_log_lines", MAX_LOG_LINES):
            break
    return out


_TEMP_OUTPUT = "TEMPORARY_OUTPUT"


_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]|~[\\/])")


_NOT_A_PATH = ("http://", "https://", "memory:", "postgres", "wfs:", "wms:", "xyz:", "vsi", "/vsi")


def _short_param(value) -> str:











    text = str(value)
    if text == _TEMP_OUTPUT:
        return text
    name = _file_name(text)
    if name:
        return name
    return text if len(text) <= 80 else text[:77] + "..."


def _file_name(text: str) -> str:










    if not text or len(text) < 4:
        return ""





    if " " in text.strip() and not _ABSOLUTE_PATH.match(text.strip()):
        return ""
    if any(text.startswith(prefix) for prefix in _NOT_A_PATH):
        return ""
    if "/" not in text and "\\" not in text:
        return ""
    tail = text.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return tail[:80] if tail else ""
