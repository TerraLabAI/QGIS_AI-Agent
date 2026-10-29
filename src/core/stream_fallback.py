# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


















from __future__ import annotations

import os
from datetime import datetime, timezone

from qgis.core import QgsProject, QgsRasterLayer
from qgis.PyQt.QtCore import QCoreApplication

from . import telemetry
from . import telemetry_events as ev
from .logger import log_warning
from .qt_compat import qgis_iface

_STREAMED = "/vsicurl/"

_TRIED: set[str] = set()
_CANVAS = None


def tr(text: str) -> str:
    return QCoreApplication.translate("AIAgentStreamFallback", text)


def _box_to_read(layer) -> list | None:

    from .context_layers import canvas_bbox_4326, view_bbox_4326

    view = canvas_bbox_4326()
    own = view_bbox_4326(layer.extent(), layer.crs())
    if not view or not own:
        return view or own
    box = [max(view[0], own[0]), max(view[1], own[1]), min(view[2], own[2]), min(view[3], own[3])]
    return box if box[0] < box[2] and box[1] < box[3] else None


def on_render_error(message: str, layer) -> None:

    try:
        if not isinstance(layer, QgsRasterLayer) or layer.providerType() != "gdal":
            return
        source = str(layer.source() or "")
        if not source.startswith(_STREAMED) or not source[len(_STREAMED):].startswith(("http://", "https://")):
            return
        from .licence import ORIGIN_PROPERTY

        if layer.id() in _TRIED or not layer.customProperty(ORIGIN_PROPERTY, False):
            return
        box = _box_to_read(layer)
        if box is None:
            return
        _TRIED.add(layer.id())
        from .background import start_kept_thread

        start_kept_thread(_copy, layer.id(), layer.name(), source, str(message or ""), box,
                          name="ai-agent-stream-fallback")
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Streamed raster fallback not started: {exc}")


def _copy(layer_id: str, name: str, source: str, error: str, box: list) -> None:

    from ..tools.stac_tools import _read_window
    from .background import run_on_main_thread

    window = _read_window(source[len(_STREAMED):], box, name)
    if window.get("_error"):
        run_on_main_thread(_say, name, tr("{name} could not be read online, and a local copy of the area on "
                                          "view failed too: {reason}").format(name=name,
                                                                                reason=window["_error"]),
                           timeout=10)
        return
    run_on_main_thread(_repoint, layer_id, source, window["path"], error, box, timeout=30)


def _repoint(layer_id: str, source: str, path: str, error: str, box: list) -> None:

    from qgis.core import QgsDataProvider

    from .host_platform import remove_tree

    layer = QgsProject.instance().mapLayer(layer_id)
    if layer is None or layer.source() != source:

        remove_tree(os.path.dirname(path))
        return
    metadata = layer.metadata()
    renderer = layer.renderer().clone() if layer.renderer() is not None else None
    options = QgsDataProvider.ProviderOptions()
    options.transformContext = QgsProject.instance().transformContext()
    layer.setDataSource(path, layer.name(), "gdal", options)
    if not layer.isValid():
        layer.setDataSource(source, layer.name(), "gdal", options)
        layer.setMetadata(metadata)
        _say(layer.name(), tr("{name} could not be read online, and its local copy did not open.").format(
            name=layer.name()))
        return
    if renderer is not None:
        layer.setRenderer(renderer)
    from .provenance import plain_source

    metadata.addHistoryItem(tr("{day} UTC, the online source stopped answering ({error}); the layer now reads "
                               "a local copy of {box} (EPSG:4326) taken from {source}").format(
        day=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"), error=error[:200],
        box=", ".join(f"{v:.4f}" for v in box), source=plain_source(source[len(_STREAMED):])))
    layer.setMetadata(metadata)
    layer.triggerRepaint()
    _say(layer.name(), tr("{name} could not be read online: it now shows a local copy of the area on view.").format(
        name=layer.name()), warning=False)


def _say(name: str, text: str, warning: bool = True) -> None:
    log_warning(text)
    telemetry.track(ev.NOTICE_SHOWN, {"surface": "stream_fallback", "kind": "warning" if warning else "info"})
    iface = qgis_iface()
    if iface is None:
        return
    try:
        from qgis.core import Qgis

        level = Qgis.MessageLevel.Warning if warning else Qgis.MessageLevel.Info
        iface.messageBar().pushMessage("AI Agent", text, level, 8)
    except Exception:  # nosec B110
        pass


def install() -> None:

    global _CANVAS
    if _CANVAS is not None:
        return
    iface = qgis_iface()
    try:
        canvas = iface.mapCanvas() if iface is not None else None
        if canvas is not None:
            canvas.renderErrorOccurred.connect(on_render_error)
            _CANVAS = canvas
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Streamed raster fallback not installed: {exc}")


def shutdown() -> None:

    global _CANVAS
    canvas, _CANVAS = _CANVAS, None
    if canvas is None:
        return
    try:
        canvas.renderErrorOccurred.disconnect(on_render_error)
    except Exception:  # nosec B110
        pass

