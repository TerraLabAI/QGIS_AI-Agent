# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




from __future__ import annotations

import math
import os
import sys
import time

from qgis.core import QgsLayoutExporter, QgsLayoutItemMap, QgsProject, QgsRectangle

from ..core import limits, net
from ..core.background import on_main_thread, run_on_main_thread
from ..core.host_platform import retry_file_op
from ..core.qt_compat import enum_member
from ..core.security import validate_path
from ..core.tool_registry import tool_error
from . import layout_child, layout_ready
from .export_rules import (
    _IMAGE_FORMATS,
    _dpi_for_ground,
    _export_failure,
    _fit_dpi,
    _ground_per_pixel,
    _heavy_layers,
    _output_folder,
    export_flags,
)
from .layout_facts import _apply_scale, _layout_credits, _page_text
from .layout_pixel_units import PixelSizeHold
from .layout_tools import furniture_check
from .report_read import _parts
from .report_tools import export_report


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
        return tool_error(f"{os.path.basename(image_path)} was written, but its world file could not be: {exc}",
                          "EXECUTION_FAILED", hint="layout_world_file_unwritable")
    if not os.path.isfile(world):
        return tool_error(f"{os.path.basename(image_path)} was written, but no world file is on disk.",
                          "EXECUTION_FAILED", hint="layout_world_file_missing")
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


def _open_to_append(path: str) -> None:
    with open(path, "ab"):
        pass


def _unwritable_target(output_path: str) -> dict | None:







    if not os.path.isfile(output_path):
        return None
    stem, extension = os.path.splitext(os.path.basename(output_path))
    if not os.access(output_path, os.W_OK):
        why, hint = "is read-only", "layout_export_target_readonly"
    else:
        try:
            retry_file_op(_open_to_append, output_path)
            return None
        except OSError:
            why, hint = "is open in another program", "layout_export_target_open"
    return tool_error(f"{output_path} {why}, so the layout cannot be exported over it.", "EXECUTION_FAILED",
                      hint=hint, alternative=f"{stem}_2{extension}")


def _restore_scale_on_refusal(reference_map, pre_scale_extent) -> None:



    if pre_scale_extent is not None:
        reference_map.zoomToExtent(pre_scale_extent)


def _save_template(layout, output_path: str, created_folder: str, scale_note: str) -> dict:





    from qgis.core import QgsReadWriteContext

    context = QgsReadWriteContext()
    context.setPathResolver(QgsProject.instance().pathResolver())
    if not layout.saveAsTemplate(output_path, context) or not os.path.isfile(output_path):
        return tool_error(f"QGIS could not write the template {output_path}.", "EXECUTION_FAILED")
    out = {"exported": output_path, "format": "qpt", "file_size": os.path.getsize(output_path),
           "reuse": "create_print_layout(name, template_path) makes a new layout from this file."}
    if scale_note:
        out["scale_note"] = scale_note
    if created_folder:
        out["created_folder"] = created_folder
    return out


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


def export_view(args: dict):









    layout = QgsProject.instance().layoutManager().layoutByName(str(args.get("layout_name") or ""))
    if layout is None:
        return None
    fmt, fmt_error = _export_format(args, str(args.get("output_path") or ""))
    if fmt_error or fmt == "qpt":
        return None
    dpi, _requested, _note, max_dpi = _asked_dpi(args)
    if getattr(layout, "pageCollection", None) is None:
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


def prepare_export(args: dict):




    return layout_ready.prepare_export(args, None if layout_child.separate(args) else export_view(args))


def _not_drawn(ready: dict, layout_name: str) -> dict:


    if ready["stopped"]:
        return tool_error("The export was stopped while its maps were being drawn; no file was written.",
                          "CANCELLED")
    late = ready["unfinished"]
    return tool_error(f"The maps of '{layout_name}' did not finish drawing in the time a call has, so "
                      f"nothing was exported. Still drawing: {', '.join(late[:6])}.",
                      "TIMEOUT", hint="layout_export_not_drawn", slow_layers=late)


def _export_layout(args: dict) -> dict:

    if not on_main_thread():
        return _export_in_separate_qgis(args)
    return _drive(_export_layout_steps(args, separate=False))


def _export_in_separate_qgis(args: dict) -> dict:



    steps = _export_layout_steps(args, separate=True)

    step = run_on_main_thread(_step, steps, None, timeout=120)
    if step[0] == "done":
        return step[1]
    budget = float(limits.current("CALL_MAX_SECONDS_BACKGROUND")) * 0.8
    report = layout_child.run(step[1], net.current_cancel_check(), budget)
    staged = step[1]["output_path"]
    if report.get("ok") and step[1]["format"] == "pdf" and os.path.exists(staged):
        from .pdf_facts import pdf_facts

        report["pdf"] = pdf_facts(staged)
    return run_on_main_thread(_step, steps, report, timeout=120)[1]


def _step(steps, sent):

    try:
        return "child", steps.send(sent)
    except StopIteration as stop:
        return "done", stop.value


def _drive(steps) -> dict:
    try:
        steps.send(None)
    except StopIteration as stop:
        return stop.value
    raise RuntimeError("a main-thread export asked for a separate QGIS")


def _export_layout_steps(args: dict, separate: bool):




    ready = layout_ready.take(str(args.get("layout_name") or ""))
    try:
        return (yield from _export_layout_with(args, ready, separate))
    finally:
        layout_ready.discard(ready)


def _export_layout_with(args: dict, ready, separate: bool = False):
    layout_name = args["layout_name"]
    output_path = args["output_path"]
    fmt, fmt_error = _export_format(args, output_path)
    if fmt_error:
        return tool_error(fmt_error, "INVALID_ARGS", hint="layout_format_mismatch")
    dpi, requested, ceiling_note, max_dpi = _asked_dpi(args)
    wanted_ground = args.get("meters_per_pixel")

    georeference = bool(args.get("georeference")) or wanted_ground is not None




    overwrite = args.get("overwrite", False)

    path_error = validate_path(output_path, write=True)
    if path_error:
        return tool_error(path_error, "INVALID_ARGS")

    if not overwrite and os.path.exists(output_path):
        return tool_error(f"File already exists: {output_path}. overwrite:true replaces it.", "INVALID_ARGS")
    refused = _unwritable_target(output_path)
    if refused:
        return refused

    manager = QgsProject.instance().layoutManager()
    layout = manager.layoutByName(layout_name)
    if not layout:
        return tool_error(f"Layout not found: {layout_name}", "INVALID_ARGS")
    if getattr(layout, "pageCollection", None) is None:


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
        return tool_error(f"meters_per_pixel sets the pixel size of an image, and {fmt} is not one.",
                          "INVALID_ARGS", hint="layout_ground_size_format")
    if georeference and fmt in ("svg", "qpt"):
        return tool_error(f"{'An SVG' if fmt == 'svg' else 'A layout template'} cannot carry a georeference.",
                          "INVALID_ARGS", hint="layout_georeference_format")
    exporter = QgsLayoutExporter(layout)


    reference = reference_map if georeference and fmt in _IMAGE_FORMATS else None
    if georeference and fmt in _IMAGE_FORMATS and reference is None:
        return tool_error(f"Layout '{layout_name}' has no map item, so the image cannot be georeferenced.",
                          "INVALID_ARGS", hint="layout_georeference_needs_map")
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



    missing_credits = _layout_credits(layout)


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
    separate_qgis = None
    try:
        if fmt not in _IMAGE_FORMATS and fmt not in ("pdf", "svg"):
            return tool_error(f"Unsupported format: {fmt}", "INVALID_ARGS")
        result = None
        if separate:
            separate_qgis, result = yield from _drawn_in_separate_qgis(
                layout_name, fmt, output_path, dpi, args, held)
            if result is None:
                held = PixelSizeHold(layout, dpi)
        if result is None:

            result = layout_ready.drawn(layout, ready, export, paint_dpi)
        exported = True
    finally:
        held.restore()
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
            return tool_error(f"Export reported success but no output file was found at {output_path}",
                              "EXECUTION_FAILED")

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
    if missing_credits:
        out["credits_not_on_page"] = missing_credits
    pixel_units = held.report()
    if pixel_units:
        out["pixel_units"] = pixel_units
    if fmt == "pdf":



        from .pdf_facts import pdf_facts

        out["pdf"] = (separate_qgis or {}).pop("pdf", None) or pdf_facts(actual_path)
    if fmt in _IMAGE_FORMATS:
        out.update(_image_size(actual_path))
        if georeference:
            geo = _write_georeference(exporter, reference, actual_path, dpi, fmt)
            if geo.get("_error"):
                return geo
            out.update(geo)


    from .layout_quality import assess_layout
    out["layout_checks"] = assess_layout(layout)
    furniture = furniture_check(layout)
    if furniture is not None:
        out["furniture_check"] = furniture
    if created_folder:
        out["created_folder"] = created_folder
    if separate_qgis:
        out["separate_qgis"] = separate_qgis
    return out


def _put_in_place(staged: str, output_path: str) -> None:


    import shutil

    try:
        retry_file_op(os.replace, staged, output_path)
        return
    except OSError as exc:
        if not os.path.exists(staged) or os.path.dirname(os.path.abspath(staged)) == os.path.dirname(
                os.path.abspath(output_path)):
            raise
        near = output_path + ".part"
        try:
            shutil.copyfile(staged, near)
            retry_file_op(os.replace, near, output_path)
        except OSError:
            if os.path.exists(near):
                os.remove(near)
            raise exc from None
    os.remove(staged)


def _drawn_in_separate_qgis(layout_name, fmt, output_path, dpi, args, held):







    started = time.monotonic()
    try:
        copy = layout_child.write_copy()
    except Exception as exc:  # noqa: BLE001
        held.restore()
        return {"drawn": False, "reason": f"project copy not written: {exc}"}, None
    held.restore()


    staged = os.path.join(os.path.dirname(copy), "page." + fmt)
    job = {"project_path": copy, "layout_name": layout_name, "format": fmt, "output_path": staged,
           "dpi": dpi, "appendGeoreference": bool(args.get("georeference")),
           "forceVectorOutput": bool(args.get("force_vector")), "settings": layout_child.settings()}
    copy_s = round(time.monotonic() - started, 2)
    report = yield job
    report = report if isinstance(report, dict) else {}
    facts = {"copy_s": copy_s, **{key: report[key] for key in ("init_s", "read_s", "simplify_s", "export_s",
                                                                 "wall_s", "simplified", "pdf") if report.get(key)}}
    if report.get("stopped"):
        return facts, enum_member(QgsLayoutExporter, "ExportResult", "Canceled")
    success = enum_member(QgsLayoutExporter, "ExportResult", "Success")
    success = int(getattr(success, "value", success))
    if report.get("ok") and report.get("result") == success and os.path.exists(staged):
        try:
            _put_in_place(staged, output_path)
        except OSError as exc:
            return {"drawn": False, "reason": f"the page could not take the place of {output_path}: {exc}",
                    **facts}, None
        return {"drawn": True, **facts}, enum_member(QgsLayoutExporter, "ExportResult", "Success")
    reason = str(report.get("error") or report.get("error_text") or f"export result {report.get('result')}")
    return {"drawn": False, "reason": reason[:300], **facts}, None
