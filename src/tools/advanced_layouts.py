# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""list_layouts and export_layout: the layout exports, their world files and their credits."""
from __future__ import annotations

import math
import os

from qgis.core import QgsLayoutExporter, QgsProject, QgsRectangle

from ..core import licence, limits, output_paths
from ..core.qt_compat import enum_member
from ..core.security import validate_path


def _list_layouts(args: dict) -> dict:
    manager = QgsProject.instance().layoutManager()
    layouts = []
    for layout in manager.layouts():
        layouts.append({
            "name": layout.name(),
            "page_count": layout.pageCollection().pageCount(),
        })
    return {"layouts": layouts, "count": len(layouts)}



_EXPORT_EXTS = {".pdf": "pdf", ".png": "png", ".jpg": "jpg", ".jpeg": "jpg", ".svg": "svg",
                ".tif": "tif", ".tiff": "tif"}

_IMAGE_FORMATS = frozenset({"png", "jpg", "tif"})


def _export_format(args: dict, output_path: str) -> tuple[str, str]:
    """The format to export in, and why not when the call contradicts itself."""






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
    """Metres in one unit of *crs*, or None when its unit is not a length (degrees)."""
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
    except Exception:  # noqa: BLE001 - an unreadable unit is not a length we can promise
        return None
    return factor if factor > 0 else None


def _ground_per_pixel(exporter, reference, dpi: float) -> tuple[float, str]:
    """(size of one exported pixel on the ground, unit): metres, or map units in degrees."""
    a, _b, _c, d, _e, _f = exporter.computeWorldFileParameters(float(dpi))
    size = math.hypot(a, d)
    per_unit = _metres_per_map_unit(reference.crs())
    return (size * per_unit, "m") if per_unit else (size, "degrees")


def dpi_ceiling_advice(layout_name, max_dpi: int) -> str:
    """The sentence a refused dpi carries: the highest dpi here and what it gives on the ground."""




    layout = QgsProject.instance().layoutManager().layoutByName(str(layout_name or ""))
    reference = layout.referenceMap() if layout else None
    if reference is None:
        return ""
    size, unit = _ground_per_pixel(QgsLayoutExporter(layout), reference, max_dpi)
    if not size > 0:
        return ""
    shown = f"{size:.2f} m" if unit == "m" else f"{size:.6f} degrees"
    return (f"Pass dpi {max_dpi} at most: {shown} per pixel on this layout. Finer: run_processing "
            "native:rasterize with MAP_UNITS_PER_PIXEL and async=true.")


def _dpi_for_ground(exporter, reference, metres, max_dpi: int, measure=None):
    """The dpi that gives *metres* per pixel on the layout's map, or the refusal as a dict."""




    try:
        metres = float(metres)
    except (TypeError, ValueError):
        metres = 0.0
    if not metres > 0 or not math.isfinite(metres):
        return {"_error": "meters_per_pixel must be a positive number.", "_code": "INVALID_ARGS",
                "_suggestion": "Pass the ground size of one pixel in metres, for example 1.0."}
    if _metres_per_map_unit(reference.crs()) is None:
        return {"_error": "The layout's map is in degrees, so a pixel size in metres has no single dpi.",
                "_code": "INVALID_ARGS",
                "_suggestion": "Set the map to a projected CRS (the UTM zone of the area) first, or pass dpi."}
    at_100 = measure() if measure is not None else _ground_per_pixel(exporter, reference, 100)[0]
    if not at_100 > 0:
        return {"_error": "The layout's map has no extent to measure a pixel size from.",
                "_code": "EXECUTION_FAILED",
                "_suggestion": "Check the map item shows the area (get_layout_info), then export again."}
    dpi = 100.0 * at_100 / metres
    if dpi > max_dpi:
        finest = at_100 * 100.0 / max_dpi
        return limits.refusal(
            f"meters_per_pixel {metres:g}", f"{dpi:,.0f} dpi on this layout", f"{max_dpi} dpi",
            f"Pass meters_per_pixel {math.ceil(finest * 100) / 100:.2f} or more here, or run_processing "
            f"native:rasterize with MAP_UNITS_PER_PIXEL {metres:g} and async=true for a GeoTIFF.")
    if dpi < 10:
        coarsest = at_100 * 10.0
        return {"_error": f"meters_per_pixel {metres:g} is coarser than 10 dpi on this layout.",
                "_code": "INVALID_ARGS",
                "_suggestion": f"Pass meters_per_pixel {coarsest:.2f} or less, or make the layout page smaller."}
    return round(dpi, 4)


def _world_file_path(image_path: str) -> str:
    """The world file QGIS and GDAL pair with an image: .jgw for .jpg, .pgw for .png, .tfw for .tif."""
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
    except Exception:  # noqa: BLE001 - the world file still places the image
        return ""


def _write_georeference(exporter, reference, image_path: str, dpi: float, fmt: str) -> dict:
    """Write the world file (and the CRS beside a png or jpg), check it is on disk, say what it holds."""








    a, b, c, d, e, f = exporter.computeWorldFileParameters(float(dpi))
    world = _world_file_path(image_path)
    lines = (a, d, b, e, c + (a + b) / 2.0, f + (d + e) / 2.0)
    try:
        with open(world, "w", encoding="ascii", newline="") as handle:
            handle.write("".join(f"{value:.12f}\r\n" for value in lines))
    except OSError as exc:
        return {"_error": f"{os.path.basename(image_path)} was written, but its world file could not be: {exc}",
                "_code": "EXECUTION_FAILED",
                "_suggestion": "Tell the user the image is not georeferenced; export to a writable folder."}
    if not os.path.isfile(world):
        return {"_error": f"{os.path.basename(image_path)} was written, but no world file is on disk.",
                "_code": "EXECUTION_FAILED",
                "_suggestion": "Tell the user the image is not georeferenced; do not call it georeferenced."}
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
    except Exception as exc:  # noqa: BLE001 -- the size is a courtesy, the file is what was asked for
        try:
            from qgis.core import Qgis, QgsMessageLog

            QgsMessageLog.logMessage(
                f"Could not read image size for {path}: {exc}", "AI Agent", level=Qgis.MessageLevel.Info)
        except Exception:  # nosec B110 - logging the courtesy failure is itself a courtesy
            pass
    return {}


def _map_layers(item) -> list:
    """The layers a layout map draws: its locked layers, the map theme it follows, else the visible layers."""
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
    """The credit lines of the layers this layout's maps draw, minus those a label already shows."""




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


def _add_without_undo(layout, label) -> None:
    """Add the credit label without an undo step: the user never placed it."""
    undo = layout.undoStack()
    undo.blockCommands(True)
    try:
        layout.addLayoutItem(label)
    finally:
        undo.blockCommands(False)


def _add_credit_label(layout, text: str):
    """A small label with the credits at the foot of the first page, or None. Removed after the export."""
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
        label.attemptMove(QgsLayoutPoint(5, max(0.0, height - 8), mm))
        label.attemptResize(QgsLayoutSize(max(10.0, width - 10), 6, mm))
        _add_without_undo(layout, label)
        return label
    except Exception:  # noqa: BLE001 - the export goes on without the credit line
        return None


def _remove_credit_label(layout, label) -> None:
    """Take the export's credit label away again, without an undo step in the user's layout history."""
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
    """'A3 portrait, 297 x 420 mm' for the first page; the name is 'custom' when no standard size matches."""
    try:
        from .layout_tools import _page_summary

        page = _page_summary(layout)
        name = page["page_size"] or "custom"
        return f"{name} {page['orientation']}, {page['width_mm']:g} x {page['height_mm']:g} mm"
    except Exception:  # noqa: BLE001 - the page size is a courtesy, the file is what was asked for
        return ""


def _ground_text(reference_map, rect) -> str:
    """'5.0 x 3.8 km' in metres for a projected map, '0.0450 x 0.0340 degrees' otherwise."""
    factor = _metres_per_map_unit(reference_map.crs())
    if factor is not None:
        return f"{rect.width() * factor / 1000:.1f} x {rect.height() * factor / 1000:.1f} km"
    return f"{rect.width():.4f} x {rect.height():.4f} degrees"


def _apply_scale(reference_map, wanted, layout_name: str):
    """Set the layout's main map to exactly 1:wanted around its centre, or the refusal as a dict."""



    try:
        wanted = float(wanted)
    except (TypeError, ValueError):
        wanted = 0.0
    if not math.isfinite(wanted) or wanted < 1:
        return {"_error": "scale must be a number of 1 or more, the denominator of 1:scale.",
                "_code": "INVALID_ARGS", "_suggestion": "Pass 25000 for a map at 1:25000."}
    if reference_map is None:
        return {"_error": f"Layout '{layout_name}' has no map item, so it has no scale to set.",
                "_code": "INVALID_ARGS",
                "_suggestion": "Add a map with add_layout_map first, then export with scale."}
    before = QgsRectangle(reference_map.extent())
    reference_map.setScale(wanted)
    got = reference_map.scale()
    if not got > 0 or abs(got - wanted) > wanted * 0.001:
        reference_map.zoomToExtent(before)
        return {"_error": f"The map could not be set to 1:{wanted:,.0f}; it reads 1:{got:,.0f}.",
                "_code": "EXECUTION_FAILED",
                "_suggestion": "Check the map item has a CRS and an extent (get_layout_info), then export again."}
    after = reference_map.extent()
    if after.width() < before.width() * 0.999 or after.height() < before.height() * 0.999:
        return (f"At 1:{wanted:,.0f} the map frame shows {_ground_text(reference_map, after)} around "
                f"the same centre, less than the {_ground_text(reference_map, before)} it showed; a "
                "larger page or map frame, or a larger scale number, shows more.")
    return ""


def _restore_scale_on_refusal(reference_map, pre_scale_extent) -> None:
    """Undo _apply_scale's mutation: a refusal after it ran means no export happened, so the user's layout should read exactly as it did before."""


    if pre_scale_extent is not None:
        reference_map.zoomToExtent(pre_scale_extent)


def _export_layout(args: dict) -> dict:
    layout_name = args["layout_name"]
    output_path = args["output_path"]
    fmt, fmt_error = _export_format(args, output_path)
    if fmt_error:
        return {"_error": fmt_error, "_code": "INVALID_ARGS",
                "_suggestion": "Pass format that matches the file name, or drop format and let the "
                               "extension decide."}





    max_dpi = int(limits.current("MAX_RENDER_DPI"))
    dpi = max(10, min(int(args.get("dpi") or min(300, max_dpi)), max_dpi))
    wanted_ground = args.get("meters_per_pixel")

    georeference = bool(args.get("georeference")) or wanted_ground is not None




    overwrite = args.get("overwrite", False)

    path_error = validate_path(output_path, write=True)
    if path_error:
        return {"_error": path_error}

    if not overwrite and os.path.exists(output_path):
        return {"_error": f"File already exists: {output_path}. Use overwrite:true to replace it."}

    manager = QgsProject.instance().layoutManager()
    layout = manager.layoutByName(layout_name)
    if not layout:
        return {"_error": f"Layout not found: {layout_name}"}

    wanted_scale = args.get("scale")
    reference_map = layout.referenceMap()

    if wanted_ground is not None and fmt not in _IMAGE_FORMATS:
        return {"_error": f"meters_per_pixel sets the pixel size of an image, and {fmt} is not one.",
                "_code": "INVALID_ARGS", "_suggestion": "Export to png, jpg or tif for a ground resolution."}
    if georeference and fmt == "svg":
        return {"_error": "An SVG cannot carry a georeference.", "_code": "INVALID_ARGS",
                "_suggestion": "Export to pdf for a GeoPDF, or png, jpg or tif for an image with a world file."}
    exporter = QgsLayoutExporter(layout)


    reference = reference_map if georeference and fmt in _IMAGE_FORMATS else None
    if georeference and fmt in _IMAGE_FORMATS and reference is None:
        return {"_error": f"Layout '{layout_name}' has no map item, so the image cannot be georeferenced.",
                "_code": "INVALID_ARGS",
                "_suggestion": "Add a map with add_layout_map first, or export without georeference."}
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






    folder = os.path.dirname(output_path) or "."
    created_folder = ""
    if not os.path.isdir(folder):
        meant = "" if args.get("create_folder") else output_paths.near_existing_folder(folder)
        if meant:
            _restore_scale_on_refusal(reference_map, pre_scale_extent)
            return {"_error": f"The folder {folder} does not exist, and {meant} does.",
                    "_code": "INVALID_ARGS",
                    "_suggestion": f"Write under {meant} instead; pass create_folder:true only if the user "
                                   "asked for a new folder."}
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            _restore_scale_on_refusal(reference_map, pre_scale_extent)
            raise
        created_folder = folder



    credits = _layout_credits(layout)
    credit_label = _add_credit_label(layout, credits)

    exported = False
    try:
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
            result = exporter.exportToPdf(output_path, settings)
        elif fmt in _IMAGE_FORMATS:
            settings = QgsLayoutExporter.ImageExportSettings()
            settings.dpi = dpi



            settings.generateWorldFile = georeference
            result = exporter.exportToImage(output_path, settings)
        elif fmt == "svg":
            settings = QgsLayoutExporter.SvgExportSettings()
            settings.dpi = dpi
            result = exporter.exportToSvg(output_path, settings)
        else:
            return {"_error": f"Unsupported format: {fmt}"}
        exported = True
    finally:
        _remove_credit_label(layout, credit_label)
        if not exported:

            _restore_scale_on_refusal(reference_map, pre_scale_extent)

    if result != enum_member(QgsLayoutExporter, "ExportResult", "Success"):
        _restore_scale_on_refusal(reference_map, pre_scale_extent)
        return {"_error": f"Export failed with code: {result}"}



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
    if credit_label is not None:
        out["attribution"] = credits
    if fmt in _IMAGE_FORMATS:
        out.update(_image_size(actual_path))
        if georeference:
            geo = _write_georeference(exporter, reference, actual_path, dpi, fmt)
            if geo.get("_error"):
                return geo
            out.update(geo)
    if created_folder:
        out["created_folder"] = created_folder
    return out

