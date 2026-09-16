# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





















from __future__ import annotations

from qgis.core import QgsProject

from .logger import log_warning

_CONNECTED = False
_HAS_3D = False


def _open_views() -> list:
    if not _HAS_3D:
        return []
    try:
        from qgis.utils import iface

        getter = getattr(iface, "mapCanvases3D", None)
        return list(getter()) if getter is not None else []
    except Exception:  # nosec B110
        return []


def _layer_id(item) -> str:
    if isinstance(item, str):
        return item
    try:
        return str(item.id())
    except Exception:  # noqa: BLE001
        return ""


def release_from_3d_views(removed) -> int:





    gone = {layer_id for layer_id in (_layer_id(item) for item in removed or ()) if layer_id}
    if not gone:
        return 0
    changed = 0
    for canvas in _open_views():
        try:
            settings = canvas.mapSettings()
            if settings is None:
                continue
            layers = list(settings.layers())
            keep = [layer for layer in layers if _layer_id(layer) not in gone]
            if len(keep) != len(layers):
                settings.setLayers(keep)
                changed += 1
        except Exception as exc:  # noqa: BLE001
            log_warning(f"3D view not updated before a layer removal: {exc}")
    return changed


def install() -> None:

    global _CONNECTED, _HAS_3D
    try:
        import qgis._3d  # noqa: F401

        _HAS_3D = True
    except Exception:  # nosec B110
        _HAS_3D = False
    if _CONNECTED or not _HAS_3D:
        return
    try:
        QgsProject.instance().layersWillBeRemoved.connect(release_from_3d_views)
        _CONNECTED = True
    except Exception as exc:  # noqa: BLE001
        log_warning(f"3D view guard not installed: {exc}")


def shutdown() -> None:

    global _CONNECTED
    if not _CONNECTED:
        return
    try:
        QgsProject.instance().layersWillBeRemoved.disconnect(release_from_3d_views)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"3D view guard not disconnected: {exc}")
    _CONNECTED = False
