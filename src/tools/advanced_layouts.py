# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




from __future__ import annotations

import math
import os
import sys

from qgis.core import QgsLayoutExporter, QgsLayoutItemMap, QgsProject, QgsRectangle

from ..core import licence, limits, output_paths
from ..core.host_platform import retry_file_op
from ..core.qt_compat import enum_member
from ..core.security import validate_path
from .layout_pixel_units import PixelSizeHold


def _list_layouts(args: dict) -> dict:
    manager = QgsProject.instance().layoutManager()
    layouts = []
    for layout in manager.layouts():



        pages = getattr(layout, "pageCollection", None)
        if pages is None:
            layouts.append({"name": layout.name(), "kind": "report"})
            continue
        layouts.append({
            "name": layout.name(),
            "page_count": pages().pageCount(),
        })
    return {"layouts": layouts, "count": len(layouts)}



_EXPORT_EXTS = {".pdf": "pdf", ".png": "png", ".jpg": "jpg", ".jpeg": "jpg", ".svg": "svg",
                ".tif": "tif", ".tiff": "tif", ".qpt": "qpt"}

_IMAGE_FORMATS = frozenset({"png", "jpg", "tif"})


def _export_format(args: dict, output_path: str) -> tuple[str, str]:







    from_name = _EXPORT_EXTS.get(os.path.splitext(str(output_path))[1].lower(), "")
    asked = str(args.get("format") or "").strip().lower()
    if not asked:
        return from_name or "pdf", ""
    if asked == "jpeg":
        asked = "jpg"
    if asked == "tiff":
        asked = "tif"
    if from_name and from_name != asked:
        return "", (f"format is {asked} but {os.path.basename(str(output_path))} names a "
                    f"{from_name} file, so the file would not hold what its name promises.")
    return asked, ""


def _metres_per_map_unit(crs) -> float | None:

    try:
        from qgis.core import Qgis, QgsUnitTypes

        metres = enum_member(Qgis, "DistanceUnit", "Meters", None)
        degrees = enum_member(Qgis, "DistanceUnit", "Degrees", None)
        if metres is None:
            metres = enum_member(QgsUnitTypes, "DistanceUnit", "DistanceMeters")
            degrees = enum_member(QgsUnitTypes, "DistanceUnit", "DistanceDegrees")
        units = crs.mapUnits()
        if units == degrees:
            return None
        factor = float(QgsUnitTypes.fromUnitToUnitFactor(units, metres))
    except Exception:  # noqa: BLE001
        return None
    return factor if factor > 0 else None


def _ground_per_pixel(exporter, reference, dpi: float) -> tuple[float, str]:

    a, _b, _c, d, _e, _f = exporter.computeWorldFileParameters(float(dpi))
    size = math.hypot(a, d)
    per_unit = _metres_per_map_unit(reference.crs())
    return (size * per_unit, "m") if per_unit else (size, "degrees")


def _dpi_for_ground(exporter, reference, metres, max_dpi: int, measure=None):





    try:
        metres = float(metres)
    except (TypeError, ValueError):
        metres = 0.0
    if not metres > 0 or not math.isfinite(metres):
        return {"_error": "meters_per_pixel must be a positive number.", "_code": "INVALID_ARGS",
                "_suggestion": "The ground size of one pixel, in metres, for example 1.0."}
    if _metres_per_map_unit(reference.crs()) is None:
        return {"_error": "The layout's map is in degrees, so a pixel size in metres has no single dpi.",
                "_code": "INVALID_ARGS",
                "_suggestion": "A projected CRS (the UTM zone of the area), or dpi directly, gives one dpi."}
    at_100 = measure() if measure is not None else _ground_per_pixel(exporter, reference, 100)[0]
    if not at_100 > 0:
        return {"_error": "The layout's map has no extent to measure a pixel size from.",
                "_code": "EXECUTION_FAILED",
                "_suggestion": "get_layout_info shows whether the map item covers the area."}
    dpi = 100.0 * at_100 / metres
    if dpi > max_dpi:
        finest = at_100 * 100.0 / max_dpi
        return limits.refusal(
            f"meters_per_pixel {metres:g}", f"{dpi:,.0f} dpi on this layout", f"{max_dpi} dpi",
            f"meters_per_pixel {math.ceil(finest * 100) / 100:.2f} or more fits here, or run_processing "
            f"native:rasterize with MAP_UNITS_PER_PIXEL {metres:g} and async=true for a GeoTIFF.")
    if dpi < 10:
        coarsest = at_100 * 10.0
        return {"_error": f"meters_per_pixel {metres:g} is coarser than 10 dpi on this layout.",
                "_code": "INVALID_ARGS",
                "_suggestion": f"meters_per_pixel {coarsest:.2f} or less fits here, or a smaller layout page."}
    return round(dpi, 4)


def _world_file_path(image_path: str) -> str:

    stem, ext = os.path.splitext(image_path)
    ext = ext.lstrip(".")
    return f"{stem}.{ext[0]}{ext[-1]}w" if len(ext) >= 2 else f"{stem}.wld"


def _crs_wkt(crs) -> str:
    try:
        from qgis.core import Qgis, QgsCoordinateReferenceSystem

        variant = enum_member(Qgis, "CrsWktVariant", "Wkt1Gdal", None)
        if variant is None:
            variant = enum_member(QgsCoordinateReferenceSystem, "CrsWktVariant", "WKT1_GDAL")
        return crs.toWkt(variant) or ""
    except Exception:  # noqa: BLE001
        return ""


def _write_georeference(exporter, reference, image_path: str, dpi: float, fmt: str) -> dict:









    a, b, c, d, e, f = exporter.computeWorldFileParameters(float(dpi))
    world = _world_file_path(image_path)
    lines = (a, d, b, e, c + (a + b) / 2.0, f + (d + e) / 2.0)
    try:
        with open(world, "w", encoding="ascii", newline="") as handle:
            handle.write("".join(f"{value:.12f}\r\n" for value in lines))
    except OSError as exc:
        return {"_error": f"{os.path.basename(image_path)} was written, but its world file could not be: {exc}",
                "_code": "EXECUTION_FAILED",
                "_suggestion": "The image is not georeferenced; a writable folder allows the world file."}
    if not os.path.isfile(world):
        return {"_error": f"{os.path.basename(image_path)} was written, but no world file is on disk.",
                "_code": "EXECUTION_FAILED",
                "_suggestion": "The image is not georeferenced."}
    crs = reference.crs()
    out = {"georeferenced": True, "world_file": world, "crs": crs.authid() or crs.description()}
    wkt = _crs_wkt(crs) if fmt != "tif" else ""
    if wkt:
        aux = image_path + ".aux.xml"
        escaped = wkt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        try:
            with open(aux, "w", encoding="utf-8") as handle:
                handle.write(f"<PAMDataset>\n  <SRS>{escaped}</SRS>\n</PAMDataset>\n")
            out["crs_file"] = aux
        except OSError:
            pass
    size, unit = _ground_per_pixel(exporter, reference, dpi)
    if unit == "m":
        out["meters_per_pixel"] = round(size, 4)
    else:
        out["degrees_per_pixel"] = round(size, 10)
    return out


def _image_size(path: str) -> dict:
    try:
        from qgis.PyQt.QtGui import QImageReader

        size = QImageReader(path).size()
        if size.width() > 0:
            return {"width_px": size.width(), "height_px": size.height()}
    except Exception as exc:  # noqa: BLE001
        try:
            from qgis.core import Qgis, QgsMessageLog

            QgsMessageLog.logMessage(
                f"Could not read image size for {path}: {exc}", "AI Agent", level=Qgis.MessageLevel.Info)
        except Exception:  # nosec B110
            pass
    return {}


def _map_layers(item) -> list:

    from qgis.core import QgsProject

    try:
        return list(item.layersToRender())
    except (RuntimeError, AttributeError, TypeError):
        pass
    project = QgsProject.instance()
    try:
        if item.followVisibilityPreset():
            name = item.followVisibilityPresetName()
            themes = project.mapThemeCollection()
            if themes.hasMapTheme(name):
                return list(themes.mapThemeVisibleLayers(name))
        if item.keepLayerSet():
            return list(item.layers())
    except (RuntimeError, AttributeError):
        pass
    return list(project.layerTreeRoot().checkedLayers())


def _layout_credits(layout) -> str:





    from qgis.core import QgsLayoutItemLabel, QgsLayoutItemMap

    layers = []
    seen = set()
    for item in layout.items():
        if not isinstance(item, QgsLayoutItemMap):
            continue
        source = _map_layers(item)
        for layer in source:
            try:
                layer_id = layer.id()
            except (RuntimeError, AttributeError):
                continue
            if layer_id not in seen:
                seen.add(layer_id)
                layers.append(layer)

    credits = []
    for layer in layers:
        try:
            text = str(layer.attribution() or "").strip()
            if not text:
                rights = layer.metadata().rights()
                text = next((str(entry).strip() for entry in rights if str(entry).strip()), "")
        except (RuntimeError, AttributeError):
            continue
        if text and text not in credits:
            credits.append(text)

    shown = []
    for item in layout.items():
        if isinstance(item, QgsLayoutItemLabel):
            shown.append(str(item.text() or ""))
    credits = [credit for credit in credits if not any(credit in label for label in shown)]
    joined = " | ".join(credits)
    return joined if len(joined) <= 600 else joined[:597].rstrip() + "..."






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
        from .layout_tools import _page_summary

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


        suggestion = "Another folder, such as the project folder, may accept it."
    else:
        suggestion = ("A lower dpi, the heaviest layer hidden with set_layers_visibility, or a "
                      "smaller page reduces it.")
    return {"_error": f"The layout was not exported: {name}.",
            "_code": "EXECUTION_FAILED",
            "_suggestion": suggestion}


def _open_to_append(path: str) -> None:
    with open(path, "ab"):
        pass


def _unwritable_target(output_path: str) -> dict | None:







    if not os.path.isfile(output_path):
        return None
    stem, extension = os.path.splitext(os.path.basename(output_path))
    if not os.access(output_path, os.W_OK):
        why, then = "is read-only", "making it writable also works"
    else:
        try:
            retry_file_op(_open_to_append, output_path)
            return None
        except OSError:
            why, then = "is open in another program", "closing it there also works"
    return {"_error": f"{output_path} {why}, so the layout cannot be exported over it.",
            "_code": "EXECUTION_FAILED",
            "_suggestion": f"Another file name, such as {stem}_2{extension}, avoids it; {then}."}


def _add_without_undo(layout, label) -> None:

    undo = layout.undoStack()
    undo.blockCommands(True)
    try:
        layout.addLayoutItem(label)
    finally:
        undo.blockCommands(False)


def _add_credit_label(layout, text: str):

    if not text:
        return None
    if layout.pageCollection().pageCount() == 0:
        return None
    try:
        from qgis.core import QgsLayoutItemLabel, QgsLayoutPoint, QgsLayoutSize, QgsUnitTypes
        from qgis.PyQt.QtGui import QFont

        mm = enum_member(QgsUnitTypes, "LayoutUnit", "LayoutMillimeters")
        page = layout.pageCollection().page(0)
        size = page.pageSize()
        width, height = size.width(), size.height()
        label = QgsLayoutItemLabel(layout)
        label.setText(licence.literal_label_text(text))
        font = QFont()
        font.setPointSizeF(6.5)
        label.setFont(font)


        margin = 10.0 * max(0.7, min(2.2, ((width * height) / (210.0 * 297.0)) ** 0.5))
        label.attemptMove(QgsLayoutPoint(margin, max(0.0, height - 8), mm))
        label.attemptResize(QgsLayoutSize(max(10.0, width - 2 * margin), 6, mm))
        _add_without_undo(layout, label)
        return label
    except Exception:  # noqa: BLE001
        return None


def _remove_credit_label(layout, label) -> None:

    if label is None:
        return
    try:
        undo = layout.undoStack()
        undo.blockCommands(True)
        try:
            layout.removeLayoutItem(label)
        finally:
            undo.blockCommands(False)
    except (RuntimeError, AttributeError):
        pass


def _page_text(layout) -> str:

    try:
        from .layout_tools import _page_summary

        page = _page_summary(layout)
        name = page["page_size"] or "custom"
        return f"{name} {page['orientation']}, {page['width_mm']:g} x {page['height_mm']:g} mm"
    except Exception:  # noqa: BLE001
        return ""


def _ground_text(reference_map, rect) -> str:

    factor = _metres_per_map_unit(reference_map.crs())
    if factor is not None:
        return f"{rect.width() * factor / 1000:.1f} x {rect.height() * factor / 1000:.1f} km"
    return f"{rect.width():.4f} x {rect.height():.4f} degrees"


def _apply_scale(reference_map, wanted, layout_name: str):




    try:
        wanted = float(wanted)
    except (TypeError, ValueError):
        wanted = 0.0
    if not math.isfinite(wanted) or wanted < 1:
        return {"_error": "scale must be a number of 1 or more, the denominator of 1:scale.",
                "_code": "INVALID_ARGS", "_suggestion": "25000 gives a map at 1:25000."}
    if reference_map is None:
        return {"_error": f"Layout '{layout_name}' has no map item, so it has no scale to set.",
                "_code": "INVALID_ARGS",
                "_suggestion": "add_layout_map adds a map; scale then applies to it."}
    before = QgsRectangle(reference_map.extent())
    reference_map.setScale(wanted)
    got = reference_map.scale()
    if not got > 0 or abs(got - wanted) > wanted * 0.001:
        reference_map.zoomToExtent(before)
        return {"_error": f"The map could not be set to 1:{wanted:,.0f}; it reads 1:{got:,.0f}.",
                "_code": "EXECUTION_FAILED",
                "_suggestion": "get_layout_info shows whether the map item has a CRS and an extent."}
    after = reference_map.extent()
    if after.width() < before.width() * 0.999 or after.height() < before.height() * 0.999:
        return (f"At 1:{wanted:,.0f} the map frame shows {_ground_text(reference_map, after)} around "
                f"the same centre, less than the {_ground_text(reference_map, before)} it showed; a "
                "larger page or map frame, or a larger scale number, shows more.")
    return ""


def _restore_scale_on_refusal(reference_map, pre_scale_extent) -> None:



    if pre_scale_extent is not None:
        reference_map.zoomToExtent(pre_scale_extent)


def _save_template(layout, output_path: str, created_folder: str, scale_note: str) -> dict:





    from qgis.core import QgsReadWriteContext

    context = QgsReadWriteContext()
    context.setPathResolver(QgsProject.instance().pathResolver())
    if not layout.saveAsTemplate(output_path, context) or not os.path.isfile(output_path):
        return {"_error": f"QGIS could not write the template {output_path}.", "_code": "EXECUTION_FAILED"}
    out = {"exported": output_path, "format": "qpt", "file_size": os.path.getsize(output_path),
           "reuse": "create_print_layout(name, template_path) makes a new layout from this file."}
    if scale_note:
        out["scale_note"] = scale_note
    if created_folder:
        out["created_folder"] = created_folder
    return out


def _output_folder(output_path: str, args: dict) -> tuple:








    folder = os.path.dirname(output_path) or "."
    if os.path.isdir(folder):
        return "", None
    meant = "" if args.get("create_folder") else output_paths.near_existing_folder(folder)
    if meant:
        return "", {"_error": f"The folder {folder} does not exist, and {meant} does.",
                    "_code": "INVALID_ARGS",
                    "_suggestion": f"{meant} exists; create_folder:true makes a new folder, which the user "
                                   "asks for."}
    os.makedirs(folder, exist_ok=True)
    return folder, None


def _asked_dpi(args: dict) -> tuple:








    max_dpi = int(limits.current("MAX_RENDER_DPI"))
    requested = int(args.get("dpi") or min(300, max_dpi))
    dpi = max(10, min(requested, max_dpi))
    ceiling_note = (f"Exported at {dpi} dpi, this computer's ceiling at the moment, rather than {requested}."
                    if requested > max_dpi else "")
    return dpi, requested, ceiling_note, max_dpi


def _scaled_extent(reference, wanted):




    try:
        wanted = float(wanted)
        current = float(reference.scale())
        if (not math.isfinite(wanted) or wanted < 1 or not current > 0
                or (reference.atlasDriven() and reference.atlasScalingMode() == enum_member(
                    QgsLayoutItemMap, "AtlasScalingMode", "Fixed"))):
            return None
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None
    extent = QgsRectangle(reference.extent())
    if abs(wanted - current) > 4 * sys.float_info.epsilon:
        extent.scale(wanted / current)
    return extent


def export_flags(fmt: str):

    if fmt == "pdf":
        return QgsLayoutExporter.PdfExportSettings().flags
    if fmt == "svg":
        return QgsLayoutExporter.SvgExportSettings().flags
    return QgsLayoutExporter.ImageExportSettings().flags


def export_view(args: dict):









    from . import layout_ready

    layout = QgsProject.instance().layoutManager().layoutByName(str(args.get("layout_name") or ""))
    if layout is None:
        return None
    fmt, fmt_error = _export_format(args, str(args.get("output_path") or ""))
    if fmt_error or fmt == "qpt":
        return None
    dpi, _requested, _note, max_dpi = _asked_dpi(args)
    if getattr(layout, "pageCollection", None) is None:
        from .report_tools import _parts

        if (fmt != "pdf" and fmt not in _IMAGE_FORMATS) or any(
                args.get(key) not in (None, False) for key in ("scale", "meters_per_pixel", "georeference")):
            return None
        for _label, part in _parts(layout):
            dpi = min(dpi, _fit_dpi(part, int(dpi), fmt)[0])
        return layout_ready.paint_dpi(fmt, dpi), export_flags(fmt), {}, "report", None
    extents, ratio = {}, 1.0
    reference = layout.referenceMap()
    if args.get("scale") is not None:
        scaled = _scaled_extent(reference, args.get("scale")) if reference is not None else None
        if scaled is None or not reference.extent().width() > 0:
            return None
        ratio = scaled.width() / reference.extent().width()
        extents[reference.uuid()] = scaled
    if args.get("meters_per_pixel") is None:
        dpi = _fit_dpi(layout, int(dpi), fmt)[0]
    else:
        if fmt not in _IMAGE_FORMATS or reference is None:
            return None
        exporter = QgsLayoutExporter(layout)

        dpi = _dpi_for_ground(exporter, reference, args.get("meters_per_pixel"), max_dpi,
                              measure=lambda: _ground_per_pixel(exporter, reference, 100)[0] * ratio)
        if isinstance(dpi, dict):
            return None
    return (layout_ready.paint_dpi(fmt, dpi), export_flags(fmt), extents, "layout",
            dpi if fmt in _IMAGE_FORMATS else None)


def _not_drawn(ready: dict, layout_name: str) -> dict:


    if ready["stopped"]:
        return {"_error": "The export was stopped while its maps were being drawn; no file was written.",
                "code": "CANCELLED"}
    late = ready["unfinished"]
    return {"_error": (f"The maps of '{layout_name}' did not finish drawing in the time a call has, so "
                       f"nothing was exported. Still drawing: {', '.join(late[:6])}."),
            "_code": "TIMEOUT", "slow_layers": late,
            "_suggestion": "A lower dpi, a smaller page or map frame, or hiding a layer drawn over the "
                           "network, draws less."}


def _export_layout(args: dict) -> dict:
    from . import layout_ready



    ready = layout_ready.take(str(args.get("layout_name") or ""))
    try:
        return _export_layout_with(args, ready)
    finally:
        layout_ready.discard(ready)


def _export_layout_with(args: dict, ready) -> dict:
    from . import layout_ready

    layout_name = args["layout_name"]
    output_path = args["output_path"]
    fmt, fmt_error = _export_format(args, output_path)
    if fmt_error:
        return {"_error": fmt_error, "_code": "INVALID_ARGS",
                "_suggestion": "format matching the file name, or no format at all, lets the "
                               "extension decide."}
    dpi, requested, ceiling_note, max_dpi = _asked_dpi(args)
    wanted_ground = args.get("meters_per_pixel")

    georeference = bool(args.get("georeference")) or wanted_ground is not None




    overwrite = args.get("overwrite", False)

    path_error = validate_path(output_path, write=True)
    if path_error:
        return {"_error": path_error}

    if not overwrite and os.path.exists(output_path):
        return {"_error": f"File already exists: {output_path}. overwrite:true replaces it."}
    refused = _unwritable_target(output_path)
    if refused:
        return refused

    manager = QgsProject.instance().layoutManager()
    layout = manager.layoutByName(layout_name)
    if not layout:
        return {"_error": f"Layout not found: {layout_name}"}
    if getattr(layout, "pageCollection", None) is None:


        from .report_tools import _parts, export_report

        if ready is not None and (ready["stopped"] or ready["unfinished"]):
            return _not_drawn(ready, layout_name)
        held = PixelSizeHold(layout, dpi)
        try:

            out = layout_ready.drawn([part for _label, part in _parts(layout)], ready,
                                     lambda: export_report(layout, args, fmt, output_path, dpi))
        finally:
            held.restore()
        pixel_units = held.report()
        if pixel_units and isinstance(out, dict) and not out.get("_error"):
            out["pixel_units"] = pixel_units
        if ceiling_note and isinstance(out, dict) and not out.get("_error"):
            out["dpi_note"] = ceiling_note
            out["dpi_lowered_from"] = requested
        return out

    wanted_scale = args.get("scale")
    reference_map = layout.referenceMap()

    if wanted_ground is not None and fmt not in _IMAGE_FORMATS:
        return {"_error": f"meters_per_pixel sets the pixel size of an image, and {fmt} is not one.",
                "_code": "INVALID_ARGS", "_suggestion": "png, jpg and tif take a ground resolution."}
    if georeference and fmt in ("svg", "qpt"):
        return {"_error": f"{'An SVG' if fmt == 'svg' else 'A layout template'} cannot carry a georeference.",
                "_code": "INVALID_ARGS",
                "_suggestion": "pdf gives a GeoPDF; png, jpg and tif give an image with a world file."}
    exporter = QgsLayoutExporter(layout)


    reference = reference_map if georeference and fmt in _IMAGE_FORMATS else None
    if georeference and fmt in _IMAGE_FORMATS and reference is None:
        return {"_error": f"Layout '{layout_name}' has no map item, so the image cannot be georeferenced.",
                "_code": "INVALID_ARGS",
                "_suggestion": "add_layout_map adds a map; without georeference the export needs none."}
    scale_note = ""
    pre_scale_extent = None
    if wanted_scale is not None:
        pre_scale_extent = QgsRectangle(reference_map.extent()) if reference_map is not None else None
        applied = _apply_scale(reference_map, wanted_scale, layout_name)
        if isinstance(applied, dict):
            return applied
        scale_note = applied
    if wanted_ground is not None:
        planned = _dpi_for_ground(exporter, reference, wanted_ground, max_dpi)
        if isinstance(planned, dict):
            _restore_scale_on_refusal(reference_map, pre_scale_extent)
            return planned
        dpi = planned



    dpi_note = ""
    if wanted_ground is None and fmt != "qpt":
        dpi, dpi_note = _fit_dpi(layout, int(dpi), fmt)
        dpi_note = " ".join(note for note in (ceiling_note, dpi_note) if note)

    try:
        created_folder, folder_error = _output_folder(output_path, args)
    except OSError:
        _restore_scale_on_refusal(reference_map, pre_scale_extent)
        raise
    if folder_error:
        _restore_scale_on_refusal(reference_map, pre_scale_extent)
        return folder_error

    if fmt == "qpt":
        return _save_template(layout, output_path, created_folder, scale_note)
    paint_dpi = layout_ready.paint_dpi(fmt, dpi)
    if (ready is not None and (ready["stopped"] or ready["unfinished"])
            and layout_ready.applies(layout, ready, paint_dpi)):
        _restore_scale_on_refusal(reference_map, pre_scale_extent)
        return _not_drawn(ready, layout_name)



    credits = _layout_credits(layout)
    credit_label = _add_credit_label(layout, credits)


    held = PixelSizeHold(layout, dpi)

    def export():
        if fmt == "pdf":
            settings = QgsLayoutExporter.PdfExportSettings()
            settings.dpi = dpi

            if args.get("georeference"):
                try:
                    settings.appendGeoreference = True
                except AttributeError:
                    pass
            if args.get("force_vector"):
                try:
                    settings.forceVectorOutput = True
                except AttributeError:
                    pass
            return exporter.exportToPdf(output_path, settings)
        if fmt in _IMAGE_FORMATS:
            settings = QgsLayoutExporter.ImageExportSettings()
            settings.dpi = dpi



            settings.generateWorldFile = georeference
            return exporter.exportToImage(output_path, settings)
        settings = QgsLayoutExporter.SvgExportSettings()
        settings.dpi = dpi
        return exporter.exportToSvg(output_path, settings)

    exported = False
    try:
        if fmt not in _IMAGE_FORMATS and fmt not in ("pdf", "svg"):
            return {"_error": f"Unsupported format: {fmt}"}

        result = layout_ready.drawn(layout, ready, export, paint_dpi)
        exported = True
    finally:
        held.restore()
        _remove_credit_label(layout, credit_label)
        if not exported:

            _restore_scale_on_refusal(reference_map, pre_scale_extent)

    if result != enum_member(QgsLayoutExporter, "ExportResult", "Success"):
        _restore_scale_on_refusal(reference_map, pre_scale_extent)
        failure = _export_failure(result)
        heavy = _heavy_layers(layout)
        if heavy:
            failure["slow_layers"] = heavy
        return failure



    actual_path = output_path
    if not os.path.exists(actual_path):
        try:
            generated = exporter.generateFileName(output_path) if hasattr(exporter, "generateFileName") else None
        except Exception:
            generated = None
        if generated and os.path.exists(generated):
            actual_path = generated
        else:
            _restore_scale_on_refusal(reference_map, pre_scale_extent)
            return {"_error": f"Export reported success but no output file was found at {output_path}"}

    out = {
        "exported": actual_path,
        "format": fmt,
        "dpi": dpi,
        "file_size": os.path.getsize(actual_path),
    }
    if reference_map is not None:
        try:
            out["scale"] = int(round(reference_map.scale()))
        except (RuntimeError, AttributeError, TypeError, ValueError):
            pass
    page_text = _page_text(layout)
    if page_text:
        out["page_size"] = page_text
    if scale_note:
        out["scale_note"] = scale_note
    if dpi_note:
        out["dpi_note"] = dpi_note
        out["dpi_lowered_from"] = requested
    if credit_label is not None:
        out["attribution"] = credits
    pixel_units = held.report()
    if pixel_units:
        out["pixel_units"] = pixel_units
    if fmt == "pdf":



        from .pdf_facts import pdf_facts

        out["pdf"] = pdf_facts(actual_path)
    if fmt in _IMAGE_FORMATS:
        out.update(_image_size(actual_path))
        if georeference:
            geo = _write_georeference(exporter, reference, actual_path, dpi, fmt)
            if geo.get("_error"):
                return geo
            out.update(geo)


    from ..core.layout_quality import assess_layout
    out["layout_checks"] = assess_layout(layout)
    from .layout_tools import furniture_check
    furniture = furniture_check(layout)
    if furniture is not None:
        out["furniture_check"] = furniture
    if created_folder:
        out["created_folder"] = created_folder
    return out
