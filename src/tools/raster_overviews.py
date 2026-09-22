# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


















from __future__ import annotations

import os
import time

from qgis.core import QgsLayoutItemMap, QgsProject, QgsRasterLayer

from ..core import net
from ..core.host_platform import remove_quietly
from ..core.logger import log, log_warning
from ..core.snapshot_paths import layer_file_path


LARGE_RASTER_PIXELS = 25_000_000


def _layout_named(args: dict):

    manager = QgsProject.instance().layoutManager()
    wanted = str(args.get("layout_name") or "").strip()
    if wanted:
        return manager.layoutByName(wanted)
    try:
        layouts = list(manager.printLayouts())
    except (AttributeError, RuntimeError):
        return None
    return layouts[0] if len(layouts) == 1 else None


def _map_layers(item) -> list:

    project = QgsProject.instance()
    try:
        if item.keepLayerSet():
            return list(item.layers())
        if item.followVisibilityPreset():
            return list(project.mapThemeCollection().mapThemeVisibleLayers(item.followVisibilityPresetName()))
    except (AttributeError, RuntimeError):
        pass
    return [node.layer() for node in project.layerTreeRoot().findLayers() if node.isVisible()]


def _needs_overviews(layer) -> str | None:

    if not isinstance(layer, QgsRasterLayer) or layer.providerType() != "gdal":
        return None
    try:
        if int(layer.width()) * int(layer.height()) < LARGE_RASTER_PIXELS:
            return None
        if layer.dataProvider().hasPyramids():
            return None
    except (AttributeError, RuntimeError):
        return None
    return layer_file_path(layer)


def layout_rasters(layout) -> dict:

    found = {}
    if layout is None or getattr(layout, "pageCollection", None) is None:
        return found
    for item in layout.items():
        if not isinstance(item, QgsLayoutItemMap):
            continue
        for layer in _map_layers(item):
            if layer is None or layer.id() in found:
                continue
            path = _needs_overviews(layer)
            if path:
                found[layer.id()] = path
    return found


def _levels(width: int, height: int) -> list:
    size, levels, level = max(width, height), [], 2
    while size / level >= 256:
        levels.append(level)
        level *= 2
    return levels


def _build(path: str) -> str:

    from osgeo import gdal

    from .georeference_tools import _build_overviews

    ovr = path + ".ovr"
    existed = os.path.exists(ovr)
    cancel = net.current_cancel_check()

    def progress(_complete, _message, _data):
        return 0 if cancel is not None and cancel() else 1

    try:

        dataset = gdal.Open(path, gdal.GA_ReadOnly)
    except Exception as exc:  # noqa: BLE001
        return str(exc) or "GDAL could not open it"
    if dataset is None:
        return "GDAL could not open it"
    failure, band = "", None
    try:
        band = dataset.GetRasterBand(1)

        method = "NEAREST" if band is not None and band.GetColorTable() is not None else "AVERAGE"
        levels = _levels(dataset.RasterXSize, dataset.RasterYSize)
        if levels:


            set_local = getattr(gdal, "SetThreadLocalConfigOption", None)
            if callable(set_local):
                set_local("GDAL_NUM_THREADS", "ALL_CPUS")
            try:
                _build_overviews(gdal, dataset, method, levels, progress)
            finally:
                if callable(set_local):
                    set_local("GDAL_NUM_THREADS", None)


            if band is None or band.GetOverviewCount() < 1:
                failure = gdal.GetLastErrorMsg() or "no overview was written"
    except Exception as exc:  # noqa: BLE001
        failure = str(exc) or type(exc).__name__

    band = None
    del dataset
    if cancel is not None and cancel():
        failure = failure or "stopped"
    if failure and not existed:

        remove_quietly(ovr)
    return failure


def prepare_layout(args: dict):





    wanted = layout_rasters(_layout_named(args))
    if not wanted:
        return None

    def work():
        built, started = [], time.perf_counter()
        for layer_id, path in wanted.items():
            reason = _build(path)
            if reason:
                log_warning(f"Overviews not built for {path}: {reason}")
            else:
                built.append(layer_id)
        seconds = round(time.perf_counter() - started, 1)

        def finish() -> dict:
            names = []
            for layer_id in built:
                layer = QgsProject.instance().mapLayer(layer_id)
                if layer is None:
                    continue
                try:
                    layer.dataProvider().reloadData()
                    layer.triggerRepaint()
                except (AttributeError, RuntimeError) as exc:
                    log_warning(f"{layer.name()} not reopened on its overviews: {exc}")
                    continue
                names.append(layer.name())
            if names:
                log(f"Overviews built in {seconds} s before drawing the layout: {', '.join(names)}")
            return {"overviews_built": names, "seconds": seconds} if names else {}

        return finish

    return work


def prepare_render(args: dict):

    if str(args.get("target") or "canvas").strip().lower() != "layout":
        return None
    return prepare_layout(args)
