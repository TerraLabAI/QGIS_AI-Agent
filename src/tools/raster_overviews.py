# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
























from __future__ import annotations

import contextlib
import os
import time

from qgis.core import QgsLayoutItemMap, QgsProject, QgsRasterLayer, QgsRectangle

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


def _work_for(wanted: dict, before: str, reads: dict | None = None):







    def work():
        built, started = [], time.perf_counter()
        for layer_id, path in wanted.items():
            reason = _build(path)
            if reason:
                log_warning(f"Overviews not built for {path}: {reason}")
            else:
                built.append(layer_id)
        seconds = round(time.perf_counter() - started, 1)
        answers = _read(reads, reopen=bool(built)) if reads else {}

        def finish() -> dict:
            if reads:
                _READY.clear()
                _READY[reads["layer_id"]] = answers
                reads.pop("clone", None)
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
                log(f"Overviews built in {seconds} s before {before}: {', '.join(names)}")
            return {"overviews_built": names, "seconds": seconds} if names else {}

        return finish

    return work


def prepare_layout(args: dict):

    wanted = layout_rasters(_layout_named(args))
    return _work_for(wanted, "drawing the layout") if wanted else None



_MEASURED_STYLES = ("singleband_pseudocolor", "singleband_gray", "multiband_color")


def prepare_raster_style(args: dict):





    if str(args.get("style_type") or "") not in _MEASURED_STYLES:
        return None
    typed = args.get("min_value") is not None and args.get("max_value") is not None
    if typed and args.get("cumulative_cut") in (None, "", []) and args.get("min_max") == "min_max":
        return None
    from ._layers import resolve_layer

    layer = resolve_layer(str(args.get("layer_name") or ""))
    path = _needs_overviews(layer)
    reads = _plan_reads(layer, args)
    if not path and not reads:
        return None
    return _work_for({layer.id(): path} if path else {}, "measuring its range for the style", reads)











_STATS_SAMPLE = 250_000
_READY: dict = {}
_ACTIVE: list = []


def _bands(args: dict, count: int) -> list:
    style = str(args.get("style_type") or "")
    names = (("red_band", 1), ("green_band", 2), ("blue_band", 3)) if style == "multiband_color" else (("band", 1),)
    out = []
    for name, fallback in names:
        try:
            band = int(args.get(name, fallback))
        except (TypeError, ValueError):
            continue
        if 1 <= band <= count and band not in out:
            out.append(band)
    return out


def _plan_reads(layer, args: dict) -> dict | None:


    if not isinstance(layer, QgsRasterLayer) or layer.providerType() != "gdal":
        return None
    try:
        provider = layer.dataProvider()
        bands = _bands(args, provider.bandCount())
        if not bands:
            return None
        from .style_defaults import _rules

        rules = _rules("stretch")
        cut = (rules.get("cut_low"), rules.get("cut_high"))
        return {"layer_id": layer.id(), "clone": provider.clone(), "bands": bands,
                "extent": QgsRectangle(layer.extent()), "cut": cut if None not in cut else None}
    except (AttributeError, RuntimeError) as exc:
        log_warning(f"Band reads not made ahead: {exc}")
        return None


def _read(reads: dict, reopen: bool = False) -> dict:




    from ._compat import RASTER_STATS_ALL
    from .elevation_style import min_max_stats
    from .style_defaults import _RASTER_SAMPLE

    clone, extent, answers = reads["clone"], reads["extent"], {}
    if reopen:
        clone.reloadData()
    cancel = net.current_cancel_check()
    for band in reads["bands"]:
        if cancel is not None and cancel():
            break
        try:
            stats = clone.bandStatistics(band, RASTER_STATS_ALL, QgsRectangle(), _STATS_SAMPLE)
            answers[("stats", band)] = (stats.minimumValue, stats.maximumValue)
            stats = clone.bandStatistics(band, min_max_stats(), extent, 250000)
            answers[("range", band)] = (stats.minimumValue, stats.maximumValue)
            if reads["cut"] is not None:
                low, high = reads["cut"]
                answers[("cut", band, low, high)] = tuple(clone.cumulativeCut(band, low / 100.0, high / 100.0,
                                                                               extent, _RASTER_SAMPLE))
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Band {band} not read ahead: {exc}")
    return answers


@contextlib.contextmanager
def answers_for(layer):

    _ACTIVE.append(_READY.pop(layer.id(), {}) if layer is not None else {})
    try:
        yield
    finally:
        _ACTIVE.pop()


def read_ahead(key: tuple, compute):

    answers = _ACTIVE[-1] if _ACTIVE else {}
    return answers[key] if key in answers else compute()


def prepare_render(args: dict):

    if str(args.get("target") or "canvas").strip().lower() != "layout":
        return None
    return prepare_layout(args)
