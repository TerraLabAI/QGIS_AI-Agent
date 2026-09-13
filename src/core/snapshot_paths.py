# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

import contextlib
import hashlib
import os
import time
import xml.etree.ElementTree as ET  # nosec B405

from qgis.core import QgsProviderRegistry

from .logger import log_warning
from .settings import account_dir

_SQLITE_EXTENSIONS = (".gpkg", ".sqlite", ".sqlite3", ".db")



_SNAPSHOTS = {"dir": ""}






_HELD: set[str] = set()


def hold_snapshot(path: str) -> None:

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

    path = os.path.join(account_dir(), "checkpoints")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        log_warning(f"Checkpoint folder not writable: {exc}")
    return path


def folder_bytes(path: str) -> int:

    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def _folder_freshness(path: str) -> float:







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

    if not root or not path:
        return False
    try:
        root = os.path.realpath(root)
        target = os.path.realpath(path)
        return os.path.commonpath([root, target]) == root
    except (ValueError, OSError):
        return False


def _map_canvas():

    try:
        import qgis.utils

        return qgis.utils.iface.mapCanvas() if qgis.utils.iface is not None else None
    except Exception:  # noqa: BLE001
        return None


def _refresh_canvas() -> None:
    canvas = _map_canvas()
    if canvas is None:
        return
    try:
        canvas.refresh()
    except Exception:  # nosec B110
        pass


@contextlib.contextmanager
def _canvas_held():







    canvas = _map_canvas()
    rendering = True
    if canvas is not None:
        try:
            rendering = bool(canvas.renderFlag())
            canvas.stopRendering()
            canvas.setRenderFlag(False)
        except Exception:  # noqa: BLE001
            canvas = None
    cursor_set = False
    try:
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QApplication

        if QApplication.instance() is not None:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            cursor_set = True
            QApplication.processEvents()
    except Exception:  # noqa: BLE001
        cursor_set = False
    try:
        yield
    finally:
        if canvas is not None:
            try:
                canvas.setRenderFlag(rendering)
            except Exception:  # nosec B110
                pass
        if cursor_set:
            try:
                from qgis.PyQt.QtWidgets import QApplication

                QApplication.restoreOverrideCursor()
            except Exception:  # nosec B110
                pass


def _stamp(path: str) -> tuple[int, ...] | None:







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

    path = source_path(layer)
    return path if path and os.path.isfile(path) else None


def source_path(layer) -> str | None:






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
    return path


def _ordered_layers(project) -> list:



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
    except Exception:  # nosec B110
        pass
    try:
        for node in project.layerTreeRoot().findLayers():
            if node.isVisible():
                push(node.layerId())
    except Exception:  # nosec B110
        pass
    for layer_id in layers:
        push(layer_id)
    return order




LAYOUT_DIGEST_SECONDS = 0.1


def _xml_digest(data: bytes) -> str:


    digest = hashlib.sha1(usedforsecurity=False)
    for element in ET.fromstring(data).iter():  # nosec B314
        digest.update(repr((element.tag, sorted(element.attrib.items()),
                            (element.text or "").strip())).encode("utf-8", "replace"))
    return digest.hexdigest()[:16]


def _layout_marks(project) -> list:



    from qgis.core import QgsReadWriteContext
    from qgis.PyQt.QtXml import QDomDocument

    marks: list = []
    deadline = time.monotonic() + LAYOUT_DIGEST_SECONDS
    for layout in sorted(project.layoutManager().layouts(), key=lambda item: str(item.name())):
        digest = ""
        if time.monotonic() < deadline:
            try:
                document = QDomDocument()
                document.appendChild(layout.writeXml(document, QgsReadWriteContext()))
                digest = _xml_digest(bytes(document.toByteArray()))
            except Exception:  # nosec B110
                digest = ""
        marks.append([str(layout.name()), digest])
    return marks


def _project_state(project) -> dict:






    state: dict = {"crs": "", "tree": [], "layouts": []}
    try:
        state["crs"] = project.crs().authid() or ""
    except Exception:  # nosec B110
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
    except Exception:  # nosec B110
        pass
    try:
        state["layouts"] = _layout_marks(project)
    except Exception:  # nosec B110
        pass
    return state
