# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




from __future__ import annotations

import math
import os

from qgis.core import QgsLayoutExporter

from ..core import limits, output_paths
from ..core.numbers import as_float
from ..core.qt_compat import enum_member
from ..core.tool_registry import tool_error
from .layout_facts import _map_layers, _metres_per_map_unit, _page_summary

_IMAGE_FORMATS = frozenset({"png", "jpg", "tif"})


def export_flags(fmt: str):

    if fmt == "pdf":
        return QgsLayoutExporter.PdfExportSettings().flags
    if fmt == "svg":
        return QgsLayoutExporter.SvgExportSettings().flags
    return QgsLayoutExporter.ImageExportSettings().flags


def _ground_per_pixel(exporter, reference, dpi: float) -> tuple[float, str]:

    a, _b, _c, d, _e, _f = exporter.computeWorldFileParameters(float(dpi))
    size = math.hypot(a, d)
    per_unit = _metres_per_map_unit(reference.crs())
    return (size * per_unit, "m") if per_unit else (size, "degrees")


def _dpi_for_ground(exporter, reference, metres, max_dpi: int, measure=None):





    metres = as_float(metres, 0.0)
    if not metres > 0 or not math.isfinite(metres):
        return tool_error("meters_per_pixel must be a positive number.", "INVALID_ARGS",
                          hint="layout_ground_size_invalid")
    if _metres_per_map_unit(reference.crs()) is None:
        return tool_error("The layout's map is in degrees, so a pixel size in metres has no single dpi.",
                          "INVALID_ARGS", hint="layout_pixel_size_geographic")
    at_100 = measure() if measure is not None else _ground_per_pixel(exporter, reference, 100)[0]
    if not at_100 > 0:
        return tool_error("The layout's map has no extent to measure a pixel size from.",
                          "EXECUTION_FAILED", hint="layout_map_extent_empty")
    dpi = 100.0 * at_100 / metres
    if dpi > max_dpi:
        finest = at_100 * 100.0 / max_dpi
        refused = limits.refusal(
            f"meters_per_pixel {metres:g}", f"{dpi:,.0f} dpi on this layout", f"{max_dpi} dpi", "")
        refused.update(hint="layout_pixel_size_too_fine", finest=math.ceil(finest * 100) / 100, metres=metres)
        return refused
    if dpi < 10:
        coarsest = at_100 * 10.0
        return tool_error(f"meters_per_pixel {metres:g} is coarser than 10 dpi on this layout.",
                          "INVALID_ARGS", hint="layout_pixel_size_coarse", coarsest=coarsest)
    return round(dpi, 4)






_HEAVY_PROVIDERS = frozenset({"wms", "wmts", "xyz", "wfs", "arcgismapserver",
                              "arcgisfeatureserver", "afs", "ams", "vectortile", "oapif"})


def _heavy_layers(layout) -> list:

    from qgis.core import QgsLayoutItemMap, QgsRasterLayer

    names: list = []
    for item in layout.items():
        if not isinstance(item, QgsLayoutItemMap):
            continue
        for layer in _map_layers(item):
            try:
                provider = str(getattr(layer, "providerType", lambda: "")() or "").lower()
                if (provider in _HEAVY_PROVIDERS or isinstance(layer, QgsRasterLayer)) \
                        and layer.name() not in names:
                    names.append(layer.name())
            except (AttributeError, RuntimeError):
                continue
    return names




_REMOTE_SOURCES = ("/vsicurl", "/vsis3", "/vsigs", "/vsiaz", "/vsiadls", "/vsioss", "/vsiswift",
                   "http://", "https://")


def _remote_layers(layout) -> list:





    from qgis.core import QgsLayoutItemMap, QgsRasterLayer

    names: list = []
    for item in layout.items():
        if not isinstance(item, QgsLayoutItemMap):
            continue
        for layer in _map_layers(item):
            try:
                provider = str(getattr(layer, "providerType", lambda: "")() or "").lower()
                source = str(layer.source() or "").lower() if isinstance(layer, QgsRasterLayer) else ""
                remote = provider in _HEAVY_PROVIDERS or any(mark in source for mark in _REMOTE_SOURCES)
                if remote and layer.name() not in names:
                    names.append(layer.name())
            except (AttributeError, RuntimeError):
                continue
    return names


def _page_pixels(layout, dpi: float) -> float:

    try:
        page = _page_summary(layout)
        width, height = float(page["width_mm"] or 0), float(page["height_mm"] or 0)
        if width <= 0 or height <= 0:
            return 0.0
        return (width / 25.4 * dpi) * (height / 25.4 * dpi)
    except Exception:  # noqa: BLE001
        return 0.0




_MIN_LOWERED_DPI = 96





_A4_MM2 = 210.0 * 297.0


_PRINT_DPI = 300


def _print_ceiling() -> float:


    dpi = min(_PRINT_DPI, float(limits.current("MAX_RENDER_DPI")))
    return _A4_MM2 / (25.4 * 25.4) * dpi * dpi * 1.01


def _fit_dpi(layout, dpi: int, fmt: str) -> tuple:









    remote = _remote_layers(layout)
    if not remote:
        return dpi, ""
    pixels = _page_pixels(layout, dpi)
    ceiling = _print_ceiling()
    if not pixels > ceiling:
        return dpi, ""
    fitted = int(max(_MIN_LOWERED_DPI, dpi * math.sqrt(ceiling / pixels)))
    if fitted >= dpi:
        return dpi, ""
    return fitted, (f"Exported at {fitted} dpi rather than {dpi}, by this tool's own rule, before drawing: "
                    f"nothing timed out. At {dpi} dpi this page is {pixels / 1e6:,.0f} megapixels of {fmt}, "
                    f"each fetched over the network from {', '.join(remote[:4])}, which is more than an A4 "
                    f"sheet at {_PRINT_DPI} dpi and can run longer than a call may. Hide those layers, or use "
                    "a smaller page, to print at the dpi asked for.")


_EXPORT_RESULT_WORDS = {
    "Canceled": "the export was cancelled",
    "MemoryError": "QGIS ran out of memory for an image this size",
    "FileError": "the file could not be written",
    "PrintError": "the printer device refused the page",
    "SvgLayerError": "the SVG layers could not be written",
    "IteratorError": "the atlas iterator failed",
}


def _export_failure(result) -> dict:

    name = ""
    for member, sentence in _EXPORT_RESULT_WORDS.items():
        if result == enum_member(QgsLayoutExporter, "ExportResult", member, None):
            name = sentence
            break
    if not name:
        name = f"the exporter answered {result}"
    if result == enum_member(QgsLayoutExporter, "ExportResult", "FileError", None):


        hint = "layout_export_file_refused"
    else:
        hint = "layout_export_heavy"
    return tool_error(f"The layout was not exported: {name}.", "EXECUTION_FAILED", hint=hint)


def _output_folder(output_path: str, args: dict) -> tuple:








    folder = os.path.dirname(output_path) or "."
    if os.path.isdir(folder):
        return "", None
    meant = "" if args.get("create_folder") else output_paths.near_existing_folder(folder)
    if meant:
        return "", tool_error(f"The folder {folder} does not exist, and {meant} does.", "INVALID_ARGS",
                              hint="layout_export_folder_misspelt")
    os.makedirs(folder, exist_ok=True)
    return folder, None
