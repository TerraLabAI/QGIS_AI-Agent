# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The snapshot's folders and shared state: canvas, stamps, layer records."""







from __future__ import annotations

import contextlib
import hashlib
import os

from qgis.core import QgsProviderRegistry

from .logger import log_warning
from .settings import account_dir

_SQLITE_EXTENSIONS = (".gpkg", ".sqlite", ".sqlite3", ".db")



_SNAPSHOTS = {"dir": ""}






_HELD: set[str] = set()


def hold_snapshot(path: str) -> None:
    """Keep ``path`` out of the pruner's reach until it is released."""
    if path:
        _HELD.add(os.path.normpath(str(path)))


def release_snapshot(path: str) -> None:
    _HELD.discard(os.path.normpath(str(path or "")))


def held_snapshots() -> set:
    return set(_HELD)


def snapshots_dir() -> str:
    path = os.path.join(account_dir(), "snapshots")
    if path != _SNAPSHOTS["dir"]:
        os.makedirs(path, exist_ok=True)
        _SNAPSHOTS["dir"] = path
    return path


def checkpoints_dir() -> str:
    """Where each chat's checkpoint history is kept: one JSON file per chat."""
    path = os.path.join(account_dir(), "checkpoints")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        log_warning(f"Checkpoint folder not writable: {exc}")
    return path


def folder_bytes(path: str) -> int:
    """Bytes on disk under ``path``; 0 for a folder that is missing or unreadable."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def _folder_freshness(path: str) -> float:
    """The newest mtime under ``path``: its own, or any file inside."""






    newest = 0.0
    try:
        newest = os.path.getmtime(path)
    except OSError:
        pass
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                newest = max(newest, os.path.getmtime(os.path.join(root, name)))
            except OSError:
                continue
    return newest


def inside(root: str, path: str) -> bool:
    """Whether ``path`` is ``root`` itself or below it, after resolving both."""
    if not root or not path:
        return False
    try:
        root = os.path.realpath(root)
        target = os.path.realpath(path)
        return os.path.commonpath([root, target]) == root
    except (ValueError, OSError):
        return False


def _map_canvas():
    """The QGIS map canvas, or None outside a running QGIS."""
    try:
        import qgis.utils

        return qgis.utils.iface.mapCanvas() if qgis.utils.iface is not None else None
    except Exception:  # noqa: BLE001 - no iface in tests and in headless runs
        return None


def _refresh_canvas() -> None:
    canvas = _map_canvas()
    if canvas is None:
        return
    try:
        canvas.refresh()
    except Exception:  # nosec B110 - a repaint is best effort
        pass


@contextlib.contextmanager
def _canvas_held():
    """Rendering off and the wait cursor on, for the length of a restore."""






    canvas = _map_canvas()
    rendering = True
    if canvas is not None:
        try:
            rendering = bool(canvas.renderFlag())
            canvas.stopRendering()
            canvas.setRenderFlag(False)
        except Exception:  # noqa: BLE001 - an old canvas without the flag
            canvas = None
    cursor_set = False
    try:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QApplication

        if QApplication.instance() is not None:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            cursor_set = True
            QApplication.processEvents()
    except Exception:  # noqa: BLE001 - no application in the tests
        cursor_set = False
    try:
        yield
    finally:
        if canvas is not None:
            try:
                canvas.setRenderFlag(rendering)
            except Exception:  # nosec B110 - recovery is best effort
                pass
        if cursor_set:
            try:
                from qgis.PyQt.QtWidgets import QApplication

                QApplication.restoreOverrideCursor()
            except Exception:  # nosec B110 - recovery is best effort
                pass


def _stamp(path: str) -> tuple[int, ...] | None:
    """Main ``(size, mtime_ns)`` plus the same pair for a SQLite WAL when present."""






    try:
        st = os.stat(path)
    except OSError:
        return None
    stamp = (int(st.st_size), int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))))
    if os.path.splitext(path)[1].lower() not in _SQLITE_EXTENSIONS:
        return stamp
    try:
        wal = os.stat(path + "-wal")
        wal_stamp = (int(wal.st_size), int(getattr(wal, "st_mtime_ns", int(wal.st_mtime * 1e9))))
    except OSError:
        wal_stamp = (-1, -1)
    return stamp + wal_stamp


def _file_hash(path: str) -> str | None:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def layer_file_path(layer) -> str | None:
    """The local file behind a layer, or None for remote and memory sources."""
    provider = layer.providerType() or ""
    source = layer.source() or ""
    if provider in ("memory", "wms", "wfs", "oapif", "postgres", "arcgisfeatureserver"):
        return None
    path = None
    try:
        parts = QgsProviderRegistry.instance().decodeUri(provider, source)
        path = parts.get("path") if isinstance(parts, dict) else None
    except Exception:
        path = None
    if not path:
        path = source.split("|", 1)[0]
    if path.startswith(("/vsi", "http://", "https://")):
        return None
    return path if os.path.isfile(path) else None


def _ordered_layers(project) -> list:
    """The layers a run is most likely to touch first: the active one, then the ones drawn on the map, then the rest."""


    layers = dict(project.mapLayers())
    order: list = []
    seen: set[str] = set()

    def push(layer_id: str) -> None:
        layer = layers.get(layer_id)
        if layer is not None and layer_id not in seen:
            seen.add(layer_id)
            order.append((layer_id, layer))

    try:
        from qgis.utils import iface
        active = iface.activeLayer() if iface is not None else None
        if active is not None:
            push(active.id())
    except Exception:  # nosec B110 - no iface in a headless run
        pass
    try:
        for node in project.layerTreeRoot().findLayers():
            if node.isVisible():
                push(node.layerId())
    except Exception:  # nosec B110 - a tree that will not walk falls back to the map order
        pass
    for layer_id in layers:
        push(layer_id)
    return order


def _project_state(project) -> dict:
    """The project-level facts the layer records leave out: the project CRS and the layer tree (order, groups, visibility)."""



    state: dict = {"crs": "", "tree": []}
    try:
        state["crs"] = project.crs().authid() or ""
    except Exception:  # nosec B110 - a project without a CRS is recorded blank
        pass
    try:
        root = project.layerTreeRoot()
        for node in getattr(root, "findLayers", lambda: [])():
            groups = []
            parent = node.parent()
            while parent is not None and parent is not root:
                groups.append(str(parent.name() or ""))
                parent = parent.parent()
            state["tree"].append([str(node.layerId()), "/".join(reversed(groups)), bool(node.isVisible())])
    except Exception:  # nosec B110 - a tree that will not walk is recorded empty
        pass
    return state
