# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





from __future__ import annotations

import base64
import os
import tempfile
import uuid

from qgis.core import (
    QgsExpression,
    QgsLayoutExporter,
    QgsLayoutFrame,
    QgsLayoutItemAttributeTable,
    QgsLayoutItemMap,
    QgsLayoutItemPicture,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsPrintLayout,
    QgsProject,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QImage

from ..core import limits
from ..core.security import expand_path, validate_path
from ..core.serialization import CodedText
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from ._compat import enum_value
from .data_tools import _safe_filename
from .layer_lookup import _find_layer, _layer_not_found_error
from .layout_elevation import camera_facts, snapshot_3d_view
from .layout_pixel_units import PixelSizeHold
from .layout_tools import _first_map_item, _mm_point, _mm_size, _resolve_layout, _with_new_item, place_item

_PICTURE_MODES = ["zoom", "stretch", "clip", "zoom_resize_frame", "frame_to_image_size"]
_ATLAS_FORMATS = ["pdf", "png", "jpg", "tif", "svg"]




_LAYOUT_TABLE_ROWS = 20


def register_harvest_layout_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="add_layout_picture",
        danger="write",
        builds_layout_at="layout_name",
        input_schema={
            "type": "object",
            "properties": {
                "layout_name": {"type": "string"},
                "path": {"type": "string"},
                "x": {"type": "number", "minimum": -10000, "maximum": 10000},
                "y": {"type": "number", "minimum": -10000, "maximum": 10000},
                "width": {"type": "number", "minimum": 0.001, "maximum": 10000},
                "height": {"type": "number", "minimum": 0.001, "maximum": 10000},
                "resize_mode": {"type": "string", "enum": _PICTURE_MODES},
            },
            "required": ["layout_name", "path"],
        },
        handler=_add_layout_picture,
    ))

    registry.register(Tool(
        name="add_layout_table",
        danger="write",
        builds_layout_at="layout_name",
        input_schema={
            "type": "object",
            "properties": {
                "layout_name": {"type": "string"},
                "layer_name": {"type": "string"},
                "x": {"type": "number", "minimum": -10000, "maximum": 10000},
                "y": {"type": "number", "minimum": -10000, "maximum": 10000},
                "width": {"type": "number", "minimum": 0.001, "maximum": 10000},
                "height": {"type": "number", "minimum": 0.001, "maximum": 10000},
                "max_rows": {"type": "integer", "minimum": 1, "maximum": 5000,
                             "default": _LAYOUT_TABLE_ROWS},
                "fields": {"type": "array", "items": {"type": "string"}, "maxItems": 1000},
                "filter_expression": {"type": "string"},
                "visible_only": {"type": "boolean"},
                "selected_only": {"type": "boolean"},
            },
            "required": ["layout_name", "layer_name"],
        },
        handler=_add_layout_table,
    ))

    registry.register(Tool(
        name="configure_atlas",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {
                "layout_name": {"type": "string"},
                "coverage_layer": {"type": "string"},
                "enabled": {"type": "boolean"},
                "page_name_expression": {"type": "string"},
                "filter_expression": {"type": "string"},
                "sort_expression": {"type": "string"},
                "sort_ascending": {"type": "boolean"},
                "drive_maps": {"type": "boolean"},
                "margin": {"type": "number", "minimum": 0, "maximum": 100},
                "hide_coverage": {"type": "boolean"},
                "scale": {"type": "number", "minimum": 1, "maximum": 1000000000},
            },
            "required": ["layout_name", "coverage_layer"],
        },
        handler=_configure_atlas,
    ))

    registry.register(Tool(
        name="export_atlas",
        danger="destructive",
        input_schema={
            "type": "object",
            "x-dpi-at-ceiling": True,
            "properties": {
                "layout_name": {"type": "string"},
                "output_path": {"type": "string"},
                "format": {"type": "string", "enum": _ATLAS_FORMATS},
                "dpi": {"type": "integer", "minimum": 10, "maximum": 600},
                "scale": {"type": "number", "minimum": 1, "maximum": 1000000000},
                "meters_per_pixel": {"type": "number", "exclusiveMinimum": 0},
                "filename_expression": {"type": "string"},
                "overwrite": {"type": "boolean"},
                "single_file": {"type": "boolean"},
            },
            "required": ["layout_name", "output_path"],
        },
        handler=_export_atlas,
        prepare=_prepare_atlas,
    ))

    registry.register(Tool(
        name="get_3d_screenshot",
        danger="read",
        input_schema={
            "type": "object",
            "x-dpi-at-ceiling": True,


            "x-ground-centred": True,
            "properties": {
                "view_index": {"type": "integer", "minimum": 0, "maximum": 1000},
                "dpi": {"type": "integer", "minimum": 10, "maximum": 600},
                "pitch": {"type": "number", "minimum": 0, "maximum": 90},
                "heading": {"type": "number", "minimum": -360000, "maximum": 360000},
                "distance": {"type": "number", "minimum": 0.001, "maximum": 1000000000},
                "save_path": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },
            "required": [],
        },
        handler=_get_3d_screenshot,
    ))






def _layout_success():
    return enum_value((QgsLayoutExporter, "Success"), (QgsLayoutExporter, "ExportResult.Success"))


def _export_failed(result) -> bool:
    success = _layout_success()
    try:
        return int(result) != int(success)
    except (TypeError, ValueError):
        return result != success


def _expression_error(text, field_name: str):

    if not isinstance(text, str) or not text.strip():
        return None
    expression = QgsExpression(text)
    if not expression.hasParserError():
        return None
    return tool_error(
        f"{field_name} does not parse: {expression.parserErrorString()}",
        "INVALID_ARGS",
        f"Quote field names with \"double quotes\" and strings with 'single quotes'. "
        f"evaluate_expression checks {field_name} before you pass it here.",
    )


def _layout_or_error(name: str):

    layout, error = _resolve_layout(name)
    if error:
        error.setdefault("code", "INVALID_ARGS")
        error.setdefault("suggestion", "list_layouts gives the exact name; create_print_layout makes one.")
    return layout, error


def _resolve_vector_layer(name_or_id: str):

    layer = _find_layer(name_or_id)
    if not layer:
        return None, _layer_not_found_error(name_or_id)
    if not isinstance(layer, QgsVectorLayer):
        return None, tool_error(f"Layer {name_or_id!r} is not a vector layer", "INVALID_ARGS",
                                "must be a vector layer; list_layers shows each layer's type.")
    return layer, None







_PICTURE_MODE_MEMBERS = {"zoom": "Zoom", "stretch": "Stretch", "clip": "Clip",
                         "zoom_resize_frame": "ZoomResizeFrame", "frame_to_image_size": "FrameToImageSize"}


def _box_from_args(args: dict, width: float, height: float) -> dict:

    return {"x": args.get("x", 10), "y": args.get("y", 10),
            "width": args.get("width", width), "height": args.get("height", height)}


def _put_on_page(item, box: dict) -> None:
    item.attemptMove(_mm_point(box["x"], box["y"]))
    item.attemptResize(_mm_size(box["width"], box["height"]))


def _add_layout_picture(args: dict) -> dict:
    layout, error = _layout_or_error(args["layout_name"])
    if error:
        return error
    path = expand_path(args["path"])
    if not os.path.isfile(path):
        return tool_error(f"Picture file not found: {path}", "INVALID_ARGS",
                          "path must be an existing PNG, JPG or SVG; QGIS ships SVGs under "
                          "QgsApplication.svgPaths().")

    member = _PICTURE_MODE_MEMBERS[args.get("resize_mode") or "zoom"]
    resize_mode = enum_value((QgsLayoutItemPicture, member), (QgsLayoutItemPicture, f"ResizeMode.{member}"))
    picture = QgsLayoutItemPicture(layout)
    picture.setPicturePath(path)
    if resize_mode is not None:
        picture.setResizeMode(resize_mode)
    box = _box_from_args(args, 30, 30)
    _put_on_page(picture, box)
    layout.addLayoutItem(picture)
    placement = place_item(layout, picture, box, resizable=True)

    summary = _with_new_item(layout, picture)
    summary.update(placement)
    summary["path"] = path
    if hasattr(picture, "isMissingImage") and picture.isMissingImage():
        summary["warning"] = "QGIS could not read the image; check the file format"
    return summary


def _table_filter(args: dict, layer):






    parts = []
    if args.get("filter_expression"):
        parts.append(f"({args['filter_expression']})")
    if args.get("selected_only"):
        selected = list(layer.selectedFeatureIds())
        parts.append("$id IN (" + ",".join(str(fid) for fid in selected) + ")" if selected else "FALSE")
    return " AND ".join(parts) if parts else None


def _add_layout_table(args: dict) -> dict:
    layout, error = _layout_or_error(args["layout_name"])
    if error:
        return error
    layer, error = _resolve_vector_layer(args["layer_name"])
    if error:
        return error

    columns = list(args.get("fields") or [])
    missing = [name for name in columns if layer.fields().indexOf(name) < 0]
    if missing:
        return tool_error(f"Unknown field(s) {missing} on layer {layer.name()!r}", "INVALID_ARGS",
                          f"Available: {[field.name() for field in layer.fields()]}")


    problem = _expression_error(args.get("filter_expression"), "filter_expression")
    if problem:
        return problem
    follow_map = None
    if args.get("visible_only"):
        follow_map = _first_map_item(layout)
        if follow_map is None:
            return tool_error("visible_only needs a map frame in the layout", "INVALID_ARGS",
                              "add_layout_map adds one")

    table = QgsLayoutItemAttributeTable.create(layout)
    table.setVectorLayer(layer)
    table.setMaximumNumberOfFeatures(int(args.get("max_rows") or _LAYOUT_TABLE_ROWS))
    if columns:
        table.setDisplayedFields(columns)
    feature_filter = _table_filter(args, layer)
    if feature_filter is not None:
        table.setFilterFeatures(True)
        table.setFeatureFilter(feature_filter)
    if follow_map is not None:
        table.setMap(follow_map)
        table.setDisplayOnlyVisibleFeatures(True)
    layout.addMultiFrame(table)


    frame = QgsLayoutFrame(layout, table)
    box = _box_from_args(args, 180, 80)
    _put_on_page(frame, box)
    table.addFrame(frame)
    placement = place_item(layout, frame, box, resizable=True)

    summary = _with_new_item(layout, frame)
    summary.update(placement)
    summary["table_uuid"] = table.uuid()
    summary["layer"] = layer.name()
    summary["max_rows"] = table.maximumNumberOfFeatures()
    return summary


def _configure_atlas(args: dict) -> dict:
    layout, error = _layout_or_error(args["layout_name"])
    if error:
        return error
    layer, error = _resolve_vector_layer(args["coverage_layer"])
    if error:
        return error





    for key in ("filter_expression", "sort_expression", "page_name_expression"):
        problem = _expression_error(args.get(key), key)
        if problem:
            return problem

    atlas = layout.atlas()
    atlas.setEnabled(bool(args.get("enabled", True)))
    atlas.setCoverageLayer(layer)
    atlas.setHideCoverage(bool(args.get("hide_coverage", False)))
    if args.get("page_name_expression"):
        atlas.setPageNameExpression(args["page_name_expression"])
    refusal = _apply_atlas_filter(atlas, args)
    if refusal:
        return refusal
    _apply_atlas_sort(atlas, args)
    driven = _follow_atlas(layout, args.get("margin", 0.1)) if args.get("drive_maps", True) else 0







    wanted_scale = args.get("scale")
    if wanted_scale is not None:
        _changed, scale_error = _apply_atlas_scale(layout, wanted_scale, args["layout_name"])
        if scale_error:
            return scale_error

    atlas.updateFeatures()
    summary = {
        "layout": layout.name(),
        "coverage_layer": layer.name(),
        "enabled": atlas.enabled(),
        "count": atlas.count(),
        "driven_maps": driven,
        "page_names": _leading_page_names(atlas, 5),
    }
    if driven == 0:
        summary["note"] = ("no map frame in the layout follows the atlas; a frame follows it when "
                           "configure_atlas runs with that frame already in the layout")
    if wanted_scale is not None:
        summary["scale"] = int(wanted_scale)
        summary["scale_mode"] = "fixed"
    return summary


def _is_blank_given(args: dict, key: str) -> bool:

    return key in args and not str(args[key] or "").strip()


def _apply_atlas_filter(atlas, args: dict):





    if _is_blank_given(args, "filter_expression"):
        atlas.setFilterFeatures(False)
        atlas.setFilterExpression("")
        return None
    expression = args.get("filter_expression")
    if not expression:
        return None
    atlas.setFilterFeatures(True)

    answer = atlas.setFilterExpression(expression)
    if isinstance(answer, (list, tuple)):
        accepted, message = answer[0], answer[1]
    else:
        accepted, message = bool(answer), ""
    if accepted:
        return None
    atlas.setFilterFeatures(False)
    return tool_error(f"Invalid filter_expression: {message}", "INVALID_ARGS",
                      "evaluate_expression checks an expression against the coverage layer.")


def _apply_atlas_sort(atlas, args: dict) -> None:

    if _is_blank_given(args, "sort_expression"):
        atlas.setSortFeatures(False)
    elif args.get("sort_expression"):
        atlas.setSortFeatures(True)
        atlas.setSortExpression(args["sort_expression"])
        atlas.setSortAscending(bool(args.get("sort_ascending", True)))


def _follow_atlas(layout, margin) -> int:

    fraction = float(margin)

    if fraction > 1.0:
        fraction = fraction / 100.0
    auto = enum_value((QgsLayoutItemMap, "Auto"), (QgsLayoutItemMap, "AtlasScalingMode.Auto"))
    frames = [item for item in layout.items() if isinstance(item, QgsLayoutItemMap)]
    for frame in frames:
        frame.setAtlasDriven(True)
        if auto is not None:
            frame.setAtlasScalingMode(auto)
        frame.setAtlasMargin(fraction)
    return len(frames)


def _leading_page_names(atlas, limit: int) -> list:

    names = []
    for page in range(min(atlas.count(), limit)):
        try:
            name = atlas.nameForPage(page)
        except Exception:  # noqa: BLE001
            break
        if name:
            names.append(name)
    return names


def _apply_atlas_scale(layout, wanted_scale, layout_name: str):







    from .advanced_tools import _apply_scale

    driven = [item for item in layout.items()
              if isinstance(item, QgsLayoutItemMap) and item.atlasDriven()]
    if not driven:
        return [], tool_error(
            f"Layout '{layout_name}' has no map driven by the atlas, so there is no map to hold "
            "that scale.",
            "INVALID_ARGS",
            "configure_atlas with drive_maps true (the default) drives a map; so does add_layout_map.",
        )
    fixed_mode = enum_value((QgsLayoutItemMap, "Fixed"), (QgsLayoutItemMap, "AtlasScalingMode.Fixed"))
    changed = []
    for item in driven:
        prev_mode = item.atlasScalingMode()
        prev_scale = item.scale()
        applied = _apply_scale(item, wanted_scale, layout_name)
        if isinstance(applied, dict):
            _restore_atlas_scale(changed)
            return [], applied
        if fixed_mode is not None:
            item.setAtlasScalingMode(fixed_mode)
        changed.append((item, prev_mode, prev_scale))
    return changed, None


def _restore_atlas_scale(changed) -> None:



    for item, prev_mode, prev_scale in changed:
        try:
            item.setAtlasScalingMode(prev_mode)
            item.setScale(prev_scale)
        except (RuntimeError, AttributeError):
            pass


def _atlas_dpi_for_ground(layout, atlas, fmt: str, metres, max_dpi: int):







    from .advanced_tools import _IMAGE_FORMATS, _dpi_for_ground, _ground_per_pixel

    if fmt not in _IMAGE_FORMATS:
        return tool_error(f"meters_per_pixel sets the pixel size of an image, and {fmt} is not one.",
                          "INVALID_ARGS", "png, jpg and tif take a ground resolution.")
    reference = layout.referenceMap()
    if reference is None:
        return tool_error(f"Layout '{layout.name()}' has no map item, so a pixel size on the ground has no dpi.",
                          "INVALID_ARGS", "add_layout_map adds a map; configure_atlas then drives it for "
                                          "meters_per_pixel.")
    exporter = QgsLayoutExporter(layout)
    widths = []

    def widest() -> float:
        if not atlas.beginRender():
            return 0.0
        try:
            for index in range(atlas.count()):
                if not atlas.seekTo(index):
                    break
                size = _ground_per_pixel(exporter, reference, 100)[0]
                if size > 0:
                    widths.append(size)
        finally:
            atlas.endRender()
        return max(widths, default=0.0)

    dpi = _dpi_for_ground(exporter, reference, metres, max_dpi, measure=widest)
    if isinstance(dpi, dict):
        return dpi
    finest, coarsest = min(widths) * 100.0 / dpi, max(widths) * 100.0 / dpi
    facts = {"meters_per_pixel": round(coarsest, 4)}
    if coarsest > finest * 1.01:
        facts["meters_per_pixel_range"] = [round(finest, 4), round(coarsest, 4)]
        facts["meters_per_pixel_note"] = ("Each page fits its own feature: the widest page has meters_per_pixel, "
                                          "narrower pages finer pixels. scale gives one resolution on every page.")
    return dpi, facts


def _atlas_dpi(args: dict) -> tuple:







    max_dpi = int(limits.current("MAX_RENDER_DPI"))
    requested = int(args.get("dpi") or min(300, max_dpi))
    return max(10, min(requested, max_dpi)), requested, max_dpi


def atlas_view(args: dict):







    from . import layout_ready
    from .advanced_layouts import export_flags

    layout = QgsProject.instance().layoutManager().layoutByName(str(args.get("layout_name") or ""))
    atlas = layout.atlas() if layout is not None and hasattr(layout, "atlas") else None
    if (atlas is None or not atlas.enabled() or atlas.coverageLayer() is None
            or args.get("meters_per_pixel") is not None):
        return None
    fmt = (args.get("format") or "pdf").lower()
    dpi = _atlas_dpi(args)[0]
    wanted = args.get("scale")

    def scale_setter(target):
        _apply_atlas_scale(target, wanted, target.name())

    return layout_ready.paint_dpi(fmt, dpi), export_flags(fmt), (scale_setter if wanted is not None else None)


def _prepare_atlas(args: dict):
    from . import layout_ready

    return layout_ready.prepare_atlas(args)


def _export_atlas(args: dict) -> dict:
    from . import layout_ready



    ready = layout_ready.take("atlas:" + str(args.get("layout_name") or ""))
    try:
        return _export_atlas_with(args, ready)
    finally:
        layout_ready.discard(ready)


def _export_atlas_with(args: dict, ready) -> dict:
    from . import layout_ready

    layout, error = _layout_or_error(args["layout_name"])
    if error:
        return error
    atlas = layout.atlas()
    if not atlas.enabled() or atlas.coverageLayer() is None:
        return tool_error("Atlas not enabled on this layout", "INVALID_ARGS", "configure_atlas enables it")

    output_path = expand_path(args["output_path"])
    fmt = (args.get("format") or "pdf").lower()
    dpi, requested, max_dpi = _atlas_dpi(args)


    at_ceiling = ({"dpi_note": f"Exported at {dpi} dpi, this computer's ceiling at the moment, rather "
                               f"than {requested}.", "dpi_lowered_from": requested}
                  if requested > max_dpi and args.get("meters_per_pixel") is None else {})
    path_error = validate_path(output_path, write=True)
    if path_error:
        hint = "" if isinstance(path_error, CodedText) else "output_path must be writable"
        return tool_error(path_error, "INVALID_ARGS", hint)

    if args.get("filename_expression"):
        outcome = atlas.setFilenameExpression(args["filename_expression"])
        ok, detail = (outcome[0], outcome[1]) if isinstance(outcome, (list, tuple)) else (bool(outcome), "")
        if not ok:
            return tool_error(f"Invalid filename_expression: {detail}", "INVALID_ARGS",
                              "filename_expression needs a distinct string per feature, e.g. 'page_'||\"name\".")
    atlas.updateFeatures()
    if atlas.count() == 0:
        return tool_error("The atlas has no page: the coverage layer is empty or the filter matches nothing",
                          "INVALID_ARGS", "configure_atlas sets the coverage layer and filter_expression.")

    wanted_scale = args.get("scale")
    scale_changed = []
    if wanted_scale is not None:
        scale_changed, scale_error = _apply_atlas_scale(layout, wanted_scale, layout.name())
        if scale_error:
            return scale_error

    ground = {}
    if args.get("meters_per_pixel") is not None:
        planned = _atlas_dpi_for_ground(layout, atlas, fmt, args["meters_per_pixel"], max_dpi)
        if isinstance(planned, dict):
            _restore_atlas_scale(scale_changed)
            return planned
        dpi, ground = planned

    if (ready is not None and (ready["stopped"] or ready["unfinished"])
            and ready["dpi"] == layout_ready.paint_dpi(fmt, dpi)):


        _restore_atlas_scale(scale_changed)
        if ready["stopped"]:
            return {"_error": "The atlas export was stopped while its maps were being drawn; no file was "
                              "written.", "code": "CANCELLED"}
        late = ready["unfinished"]
        return {"_error": (f"The maps of the atlas of '{layout.name()}' did not finish drawing in the time a "
                           f"call has, so nothing was exported. Still drawing: {', '.join(late[:6])}."),
                "_code": "TIMEOUT", "slow_layers": late,
                "_suggestion": "A lower dpi, fewer pages (filter_expression), or hiding a layer drawn over the "
                               "network, draws less."}
    job = {"layout": layout, "atlas": atlas, "path": output_path, "fmt": fmt, "dpi": dpi,
           "scale": wanted_scale, "at_ceiling": at_ceiling, "ground": ground, "ready": ready}
    if fmt == "pdf" and args.get("single_file") is not False:
        outcome = _atlas_to_one_pdf(job)
    else:
        outcome = _atlas_to_folder(job)
    if "_error" in outcome:

        _restore_atlas_scale(scale_changed)
    return outcome


def _atlas_writer(job: dict, base: str):

    atlas, dpi, fmt = job["atlas"], job["dpi"], job["fmt"]
    if fmt == "pdf":


        pdf = QgsLayoutExporter.PdfExportSettings()
        pdf.dpi = dpi
        return lambda: QgsLayoutExporter.exportToPdfs(atlas, base, pdf)
    if fmt == "svg":
        svg = QgsLayoutExporter.SvgExportSettings()
        svg.dpi = dpi
        return lambda: QgsLayoutExporter.exportToSvg(atlas, base, svg)
    image = QgsLayoutExporter.ImageExportSettings()
    image.dpi = dpi
    return lambda: QgsLayoutExporter.exportToImage(atlas, base, fmt, image)


def _atlas_to_one_pdf(job: dict) -> dict:

    target, dpi = job["path"], job["dpi"]
    folder = os.path.dirname(target)
    if folder:
        os.makedirs(folder, exist_ok=True)
    pdf = QgsLayoutExporter.PdfExportSettings()
    pdf.dpi = dpi
    (code, message), pixel_units = _held_export(
        job, lambda: QgsLayoutExporter.exportToPdf(job["atlas"], target, pdf))
    if _export_failed(code):
        return tool_error(f"Atlas export failed: {message or code}")
    if not os.path.exists(target):
        return tool_error(f"Export reported success but no file was found at {target}")
    written = {"exported": target, "format": job["fmt"], "dpi": dpi, "pages": job["atlas"].count(),
               "file_size": os.path.getsize(target)}
    if job["scale"] is not None:
        written["scale"] = int(job["scale"])
    if pixel_units:
        written["pixel_units"] = pixel_units
    written.update(job["at_ceiling"])
    return written


def _atlas_to_folder(job: dict) -> dict:

    folder, fmt, atlas = job["path"], job["fmt"], job["atlas"]
    if os.path.exists(folder) and not os.path.isdir(folder):
        what = "PDF" if fmt == "pdf" else "image"
        return tool_error(f"Atlas {what} output_path is not a folder: {folder}", "INVALID_ARGS",
                          "a folder for one file per page")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as exc:
        return tool_error(f"Could not create atlas output folder {folder}: {exc}", "EXECUTION_FAILED",
                          "the folder must be writable.")




    page_names, refusal = _atlas_page_names(atlas)
    if refusal:
        return refusal
    already_there = set(os.listdir(folder))

    base = os.path.join(folder, _safe_filename(job["layout"].name(), "layout"))
    (code, message), pixel_units = _held_export(job, _atlas_writer(job, base))
    if _export_failed(code):
        return tool_error(f"Atlas export failed: {message or code}")

    new_files = sorted(name for name in set(os.listdir(folder)) - already_there
                       if os.path.isfile(os.path.join(folder, name)))
    kind = "pdf" if fmt == "pdf" else "image"


    written = {"exported": folder, "format": fmt, "dpi": job["dpi"], "pages": atlas.count(),
               "files": [{"path": os.path.join(folder, name), "kind": kind} for name in new_files[:20]],
               "file_count": len(new_files)}
    if page_names:
        written["page_names"] = page_names[:20]
    if len(new_files) != atlas.count():


        written["file_count_note"] = (f"{atlas.count()} pages were rendered and {len(new_files)} file names are "
                                      f"new in the folder; the rest replaced files that were already there.")
    if job["scale"] is not None:
        written["scale"] = int(job["scale"])
    written.update(job["ground"])
    written.update(job["at_ceiling"])
    if pixel_units:
        written["pixel_units"] = pixel_units
    return written


def _held_export(job: dict, run):


    from . import layout_ready

    layout, dpi = job["layout"], job["dpi"]
    held = PixelSizeHold(layout, dpi)
    try:
        answer = layout_ready.drawn(layout, job.get("ready"), run, layout_ready.paint_dpi(job["fmt"], dpi))
    finally:
        held.restore()
    return answer, held.report()


def _atlas_page_names(atlas) -> tuple:













    try:
        if not (atlas.filenameExpression() or "").strip():
            return [], None
    except Exception:  # nosec B110
        pass
    try:
        count = int(atlas.count())
    except Exception:  # noqa: BLE001
        return [], None

    names = []
    try:
        if not atlas.beginRender():
            return [], None
        try:
            for index in range(count):
                if not atlas.seekTo(index):
                    break
                names.append(str(atlas.currentFilename() or ""))
        finally:
            atlas.endRender()
    except Exception as exc:  # noqa: BLE001


        return [], tool_error(
            f"The page names of this atlas could not be worked out before exporting ({exc}), so the export "
            f"was not started.",
            "EXECUTION_FAILED",
            "Without filename_expression, pages are named after the layout.",
        )

    problems = _bad_page_names(names)
    if problems:
        return names, tool_error(
            "The filename expression produces page names that are not plain file names: "
            + "; ".join(problems[:5]) + ("" if len(problems) <= 5 else f" (and {len(problems) - 5} more)"),
            "INVALID_ARGS",
            "Every page must be one ordinary file name, unique within the folder. replace('\"name\"', '/', '-') "
            "gives a plain name; the feature id makes duplicates distinct.",
        )
    return names, None


def _bad_page_names(names: list) -> list:

    from .data_tools import _WINDOWS_FORBIDDEN_CHARS, _WINDOWS_RESERVED_NAMES

    problems = []
    seen: dict = {}
    for name in names:
        text = str(name or "")
        if not text.strip():
            problems.append("one page has an empty name")
            continue
        if os.path.isabs(text) or (len(text) > 1 and text[1] == ":"):
            problems.append(f"{text!r} is an absolute path")
            continue
        if "/" in text or "\\" in text:
            problems.append(f"{text!r} carries a path separator")
            continue
        if text in (".", "..") or ".." in text.split(os.sep):
            problems.append(f"{text!r} points outside the output folder")
            continue
        forbidden = sorted({c for c in text if c in _WINDOWS_FORBIDDEN_CHARS or ord(c) < 32})
        if forbidden:
            problems.append(f"{text!r} holds {' '.join(repr(c) for c in forbidden)}, which Windows refuses")
            continue
        if os.path.splitext(text)[0].strip(" .").lower() in _WINDOWS_RESERVED_NAMES:
            problems.append(f"{text!r} is a reserved device name on Windows")
            continue
        key = text.lower()
        if key in seen:

            problems.append(f"{text!r} is used by more than one page, so they would overwrite each other")
            continue
        seen[key] = True
    return problems


def _get_3d_screenshot(args: dict) -> dict:
    view, error = snapshot_3d_view(args)
    if error:
        return error



    ceiling = int(limits.current("MAX_RENDER_DPI"))
    asked_dpi = int(args.get("dpi", 96))
    dpi = max(10, min(asked_dpi, ceiling))

    keep_at = args.get("save_path")
    if keep_at:
        keep_at = expand_path(keep_at)
        refused = validate_path(keep_at, write=True)
        if refused:
            hint = "" if isinstance(refused, CodedText) else "save_path must be writable"
            return tool_error(refused, "INVALID_ARGS", hint)
        png_path = keep_at
    else:
        png_path = os.path.join(tempfile.gettempdir(), f"agent_3d_{uuid.uuid4().hex[:8]}.png")



    code = _render_3d_page(view, dpi, png_path)
    if _export_failed(code) or not os.path.exists(png_path):
        return tool_error(f"3D layout export failed (export code {code})", "EXECUTION_FAILED",
                          "the 3D view must render on screen; a lower dpi needs less memory.")

    picture = QImage(png_path)
    facts = {"width": picture.width(), "height": picture.height(), "format": "png",
             "view_index": view["index"], "open_3d_views": view["count"], "camera": camera_facts(view["pose"])}
    if asked_dpi > ceiling:
        facts.update(dpi=dpi, dpi_lowered_from=asked_dpi)
    if keep_at:
        return {"saved_path": png_path, **facts}
    return {"image_base64": base64.b64encode(_read_and_remove(png_path)).decode("ascii"), **facts}




_CAPTURE_PAGE_MM = 200.0


def _render_3d_page(view: dict, dpi: int, png_path: str):

    from qgis._3d import QgsLayoutItem3DMap

    window = view["canvas"].size()
    page = QgsLayoutSize(_CAPTURE_PAGE_MM,
                         _CAPTURE_PAGE_MM * max(1, window.height()) / max(1, window.width()))
    sheet = QgsPrintLayout(QgsProject.instance())
    sheet.initializeDefaults()
    sheet.pageCollection().page(0).setPageSize(page)
    scene = QgsLayoutItem3DMap(sheet)
    scene.setMapSettings(view["settings"])
    scene.setCameraPose(view["pose"])

    scene.setBackgroundColor(view["settings"].backgroundColor())
    scene.setBackgroundEnabled(True)
    scene.attemptResize(page)
    scene.attemptMove(QgsLayoutPoint(0, 0))
    sheet.addLayoutItem(scene)
    options = QgsLayoutExporter.ImageExportSettings()
    options.dpi = dpi
    return QgsLayoutExporter(sheet).exportToImage(png_path, options)


def _read_and_remove(path: str) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
