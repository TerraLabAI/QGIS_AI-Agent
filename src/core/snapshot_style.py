# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

import hashlib

from qgis.core import QgsProject

from .logger import log_warning

try:
    from qgis.core import QgsMapLayerStyle
except ImportError:
    QgsMapLayerStyle = None






_STYLE_CACHE: dict[str, str | None] = {}


_STYLE_WATCHED: dict[str, tuple] = {}
_STYLE_PROJECT_WATCHED = False


def _forget_style(layer_id: str) -> None:
    _STYLE_CACHE.pop(layer_id, None)


def _drop_style(removed) -> None:








    for item in removed or ():
        layer_id = item if isinstance(item, str) else ""
        if not layer_id:
            try:
                layer_id = str(item.id())
            except Exception:  # nosec B112
                continue
        _STYLE_CACHE.pop(layer_id, None)
        _STYLE_WATCHED.pop(layer_id, None)


def _watch_project_layers() -> None:

    global _STYLE_PROJECT_WATCHED
    if _STYLE_PROJECT_WATCHED:
        return
    _STYLE_PROJECT_WATCHED = True
    try:
        signal = getattr(QgsProject.instance(), "layersWillBeRemoved", None)
        if signal is not None and hasattr(signal, "connect"):
            signal.connect(_drop_style)
    except Exception:  # nosec B110
        pass


def shutdown() -> None:


    global _STYLE_PROJECT_WATCHED
    if _STYLE_PROJECT_WATCHED:
        try:
            signal = getattr(QgsProject.instance(), "layersWillBeRemoved", None)
            if signal is not None and hasattr(signal, "disconnect"):
                signal.disconnect(_drop_style)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Style project watch not disconnected: {exc}")
        _STYLE_PROJECT_WATCHED = False
    for layer_id, (layer, slot) in list(_STYLE_WATCHED.items()):
        for name in ("styleChanged", "updatedFields"):
            try:
                signal = getattr(layer, name, None)
                if signal is not None and hasattr(signal, "disconnect"):
                    signal.disconnect(slot)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Style watch on layer {layer_id} not disconnected: {exc}")
    _STYLE_WATCHED.clear()
    _STYLE_CACHE.clear()


def _read_style_digest(layer) -> str | None:
    if QgsMapLayerStyle is None:
        return None
    try:
        style = QgsMapLayerStyle()
        style.readFromLayer(layer)
        return hashlib.sha1(style.xmlData().encode("utf-8")).hexdigest()[:16]  # nosec B324
    except Exception:  # nosec B110
        return None


def _style_digest(layer) -> str | None:









    try:
        layer_id = layer.id()
    except Exception:  # nosec B110
        return _read_style_digest(layer)
    _watch_project_layers()
    if layer_id not in _STYLE_WATCHED:
        signal = getattr(layer, "styleChanged", None)
        if signal is not None and hasattr(signal, "connect"):
            try:
                slot = lambda *_args, lid=layer_id: _forget_style(lid)  # noqa: E731
                signal.connect(slot)





                fields_changed = getattr(layer, "updatedFields", None)
                if fields_changed is not None and hasattr(fields_changed, "connect"):
                    fields_changed.connect(slot)
                _STYLE_WATCHED[layer_id] = (layer, slot)
            except Exception:  # nosec B110
                pass
    elif layer_id in _STYLE_CACHE:
        return _STYLE_CACHE[layer_id]
    digest = _read_style_digest(layer)
    if layer_id in _STYLE_WATCHED:
        if len(_STYLE_CACHE) > 2000:
            _STYLE_CACHE.clear()
        _STYLE_CACHE[layer_id] = digest
    return digest
