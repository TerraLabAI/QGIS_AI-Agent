# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

import hashlib
import uuid

from qgis.core import QgsProject

from .logger import log_warning

try:
    from qgis.core import QgsMapLayerStyle
except ImportError:
    QgsMapLayerStyle = None






_STYLE_CACHE: dict[str, str | None] = {}


_STYLE_WATCHED: dict[str, tuple] = {}
_STYLE_PROJECT_WATCHED = False


_STYLE_MOVES: dict[str, int] = {}


_SESSION = uuid.uuid4().hex[:12]


def _forget_style(layer_id: str) -> None:
    _STYLE_CACHE.pop(layer_id, None)
    if layer_id in _STYLE_WATCHED or layer_id in _STYLE_MOVES:
        _STYLE_MOVES[layer_id] = _STYLE_MOVES.get(layer_id, 0) + 1


def _drop_style(removed) -> None:








    for item in removed or ():
        layer_id = item if isinstance(item, str) else ""
        if not layer_id:
            try:
                layer_id = str(item.id())
            except Exception:  # nosec B112
                continue
        _forget_style(layer_id)
        _STYLE_WATCHED.pop(layer_id, None)


def _watch_project_layers() -> None:

    global _STYLE_PROJECT_WATCHED, _SESSION
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


    global _STYLE_PROJECT_WATCHED, _SESSION
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
    _STYLE_MOVES.clear()

    _SESSION = uuid.uuid4().hex[:12]


def cached_style_digest(layer_id: str) -> str | None:





    return _STYLE_CACHE.get(str(layer_id))


def _read_style_digest(layer) -> str | None:
    if QgsMapLayerStyle is None:
        return None
    try:
        style = QgsMapLayerStyle()
        style.readFromLayer(layer)
        return hashlib.sha1(style.xmlData().encode("utf-8")).hexdigest()[:16]  # nosec B324
    except Exception:  # nosec B110
        return None


def _watch(layer, layer_id: str) -> bool:

    _watch_project_layers()
    if layer_id in _STYLE_WATCHED:
        return True
    signal = getattr(layer, "styleChanged", None)
    if signal is None or not hasattr(signal, "connect"):
        return False
    try:
        slot = lambda *_args, lid=layer_id: _forget_style(lid)  # noqa: E731
        signal.connect(slot)





        fields_changed = getattr(layer, "updatedFields", None)
        if fields_changed is not None and hasattr(fields_changed, "connect"):
            fields_changed.connect(slot)
        _STYLE_WATCHED[layer_id] = (layer, slot)
    except Exception:  # noqa: BLE001
        return False
    return True


def style_mark(layer) -> list:







    try:
        layer_id = str(layer.id())
    except Exception:  # noqa: BLE001
        return [None, _read_style_digest(layer)]
    if not _watch(layer, layer_id):
        return [None, _read_style_digest(layer)]
    return [_STYLE_MOVES.get(layer_id, 0), _STYLE_CACHE.get(layer_id)]


def held_style(layer) -> str | None:




    try:
        layer_id = str(layer.id())
    except Exception:  # noqa: BLE001
        return None
    if not _watch(layer, layer_id):
        return None
    return f"{_SESSION}:{_STYLE_MOVES.get(layer_id, 0)}"


def same_style(layer, then, now) -> bool:


    if not isinstance(then, (list, tuple)) or not isinstance(now, (list, tuple)):
        return then == now
    if then[0] is None or now[0] is None:
        return then[0] is now[0] and then[1] == now[1]
    if then[0] == now[0]:
        return True
    return then[1] is not None and _style_digest(layer) == then[1]


def _style_digest(layer) -> str | None:









    try:
        layer_id = layer.id()
    except Exception:  # nosec B110
        return _read_style_digest(layer)
    if layer_id in _STYLE_WATCHED and layer_id in _STYLE_CACHE:
        return _STYLE_CACHE[layer_id]
    _watch(layer, layer_id)
    digest = _read_style_digest(layer)
    if layer_id in _STYLE_WATCHED:
        if len(_STYLE_CACHE) > 2000:
            _STYLE_CACHE.clear()
        _STYLE_CACHE[layer_id] = digest
    return digest
