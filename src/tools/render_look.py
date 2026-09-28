# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later












from __future__ import annotations

import os
import uuid

from qgis.PyQt.QtCore import QSize

from ..core.qt_compat import enum_member
from ..core.security import validate_path




_DEFAULT_LONGEST_PX = 1200

_PIXMAP_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"})


def crop_box(args: dict):






    crop = args.get("crop")
    if crop in (None, {}, ""):
        return None
    if not isinstance(crop, dict):
        return {"_error": "crop is an object with x, y, width and height as fractions of the page, "
                          'for example {"x": 0.0, "y": 0.6, "width": 0.4, "height": 0.4}.',
                "_code": "INVALID_ARGS"}
    try:
        box = tuple(float(crop.get(key, default))
                    for key, default in (("x", 0.0), ("y", 0.0), ("width", 1.0), ("height", 1.0)))
    except (TypeError, ValueError):
        return {"_error": "crop takes numbers: x, y, width and height as fractions of the page.",
                "_code": "INVALID_ARGS"}
    x, y, width, height = box
    if width <= 0 or height <= 0 or not (0.0 <= x < 1.0 and 0.0 <= y < 1.0):
        return {"_error": f"crop {box} is not a piece of the page.", "_code": "INVALID_ARGS",
                "_suggestion": "x and y are the top-left corner from 0 to 1, width and height the size "
                               "from just over 0 to 1."}


    return (x, y, min(width, 1.0 - x), min(height, 1.0 - y))


def _zoom(args: dict) -> float:

    box = crop_box(args)
    if not isinstance(box, tuple):
        return 1.0
    return min(8.0, 1.0 / max(box[2], box[3], 0.02))


def _sized(width_units: float, height_units: float, args: dict) -> tuple:







    from .advanced_render import _clamp_render_size

    asked_width, asked_height = args.get("width"), args.get("height")
    zoom = _zoom(args)
    if asked_width and asked_height:
        width, height = int(asked_width * zoom), int(asked_height * zoom)
    else:
        aspect = (float(width_units) / float(height_units)) if height_units else 1.0
        if not 0.05 <= aspect <= 20.0:
            aspect = 1.0
        longest = int((asked_width or asked_height or _DEFAULT_LONGEST_PX) * zoom)
        width, height = ((longest, max(64, round(longest / aspect))) if aspect >= 1.0
                         else (max(64, round(longest * aspect)), longest))
    width, height, _note = _clamp_render_size(width, height)
    return width, height


def cropped(image, args: dict):

    from qgis.PyQt.QtCore import QRect

    box = crop_box(args)
    if not isinstance(box, tuple):
        return image, {}
    x, y, width, height = box
    left, top = int(round(x * image.width())), int(round(y * image.height()))
    piece = image.copy(QRect(left, top,
                             max(1, int(round(width * image.width()))),
                             max(1, int(round(height * image.height())))))
    if piece.isNull():
        return image, {}
    return piece, {"crop": {"x": round(x, 3), "y": round(y, 3),
                            "width": round(width, 3), "height": round(height, 3)},
                   "crop_note": (f"This is the {round(width * 100)}% by {round(height * 100)}% piece of the "
                                 f"page starting at {round(x * 100)}%, {round(y * 100)}% from the top left, "
                                 "drawn at full size. Without crop the whole page shows.")}


def _print_layouts(manager) -> list:
    try:
        return list(manager.printLayouts())
    except (AttributeError, RuntimeError):
        return []


def layout_page_plan(args: dict) -> dict:





    from qgis.core import QgsLayoutExporter, QgsProject

    from ..core.layout_quality import assess_layout

    manager = QgsProject.instance().layoutManager()
    wanted = str(args.get("layout_name") or "").strip()
    layouts = _print_layouts(manager)
    names = []
    for item in layouts:
        try:
            names.append(item.name())
        except (AttributeError, RuntimeError):
            continue
    layout = manager.layoutByName(wanted) if wanted else (layouts[0] if len(layouts) == 1 else None)
    if layout is None:
        if not names:
            return {"_error": "This project has no print layout to look at.",
                    "_code": "INVALID_ARGS",
                    "_suggestion": "create_print_layout and add_layout_map build one."}
        if wanted:
            return {"_error": f"Layout not found: {wanted}", "_code": "INVALID_ARGS",
                    "_suggestion": f"Layouts: {', '.join(names[:8])}."}
        return {"_error": "This project has more than one print layout.", "_code": "INVALID_ARGS",
                "_suggestion": f"Layouts: {', '.join(names[:8])}."}

    pages = layout.pageCollection()
    count = int(pages.pageCount())
    if count <= 0:
        return {"_error": f"Layout '{layout.name()}' has no page.", "_code": "INVALID_ARGS",
                "_suggestion": "A layout made with a page size has a page."}
    try:
        page = int(args.get("page") or 1)
    except (TypeError, ValueError):
        page = 1
    if not 1 <= page <= count:
        return {"_error": f"Layout '{layout.name()}' has {count} page(s), so page {page} does not exist.",
                "_code": "INVALID_ARGS", "_suggestion": f"Pages run 1 to {count}."}

    facts = assess_layout(layout)
    page_box = next((row for row in facts.get("pages_mm") or [] if row.get("page") == page), None)
    rect = pages.page(page - 1).rect()
    width, height = _sized(rect.width() or 1.0, rect.height() or 1.0, args)
    image = QgsLayoutExporter(layout).renderPageToImage(page - 1, QSize(width, height), 0)
    if image is None or image.isNull():
        return {"_error": f"QGIS could not draw page {page} of '{layout.name()}'.",
                "_code": "EXECUTION_FAILED",
                "_suggestion": "get_layout_info shows the page size and the map item's extent."}

    outside = [row.get("item") for row in facts.get("items") or []
               if row.get("status") == "warning" and row.get("inside_page") is False]
    out = {"layout_name": layout.name(), "page": page, "page_count": count,
           "layout_items": facts.get("items") or [], "layout_status": facts.get("status")}
    if page_box:
        out["page_mm"] = {key: value for key, value in page_box.items() if key != "page"}
    if outside:
        out["items_outside_page"] = outside
        out["note"] = (f"Off the page and cut on export: {', '.join(str(name) for name in outside[:8])}. "
                       "The boxes are in millimetres in layout_items: move or resize each one inside "
                       "page_mm with a short execute_code script (attemptMove, attemptResize), then look "
                       "at this page again.")
    if facts.get("warnings"):
        out["layout_warnings"] = facts["warnings"][:8]
    return {"image": image, "facts": out}


def _svg_image(path: str, args: dict):

    try:
        from qgis.PyQt.QtGui import QImage, QPainter
        from qgis.PyQt.QtSvg import QSvgRenderer
    except ImportError:  # pragma: no cover
        return None, "this QGIS has no QtSvg, so an SVG cannot be rasterised here"
    renderer = QSvgRenderer(path)
    if not renderer.isValid():
        return None, "the SVG did not parse"
    size = renderer.defaultSize()
    width, height = _sized(size.width() or 1, size.height() or 1, args)
    image = QImage(width, height, enum_member(QImage, "Format", "Format_ARGB32"))
    image.fill(0xFFFFFFFF)
    painter = QPainter(image)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    return image, ""


def _pdf_with_qt(path: str, page: int, args: dict):

    QPdfDocument = None
    for module in ("qgis.PyQt.QtPdf", "PyQt6.QtPdf", "PyQt5.QtPdf"):
        try:
            QPdfDocument = __import__(module, fromlist=["QPdfDocument"]).QPdfDocument
            break
        except (ImportError, AttributeError):
            continue
    if QPdfDocument is None:
        return None, ""
    document = QPdfDocument(None)
    document.load(path)
    count = int(document.pageCount() or 0)
    if count <= 0:
        return None, "the PDF has no page this QGIS can read"
    if page > count:
        return None, f"the PDF has {count} page(s), so page {page} does not exist"
    size = document.pagePointSize(page - 1)
    width, height = _sized(size.width() or 1.0, size.height() or 1.0, args)
    image = document.render(page - 1, QSize(width, height))
    if image is None or image.isNull():
        return None, "Qt could not draw that PDF page"
    return image, ""


def _pdf_with_gdal(path: str, args: dict):





    try:
        from osgeo import gdal
    except ImportError:  # pragma: no cover
        return None, "GDAL is not importable here"








    memory = f"/vsimem/render_look_{uuid.uuid4().hex}.png"
    dataset = None
    try:
        gdal.UseExceptions()
        dataset = gdal.Open(path)
        if dataset is None:
            return None, "GDAL has no PDF driver in this QGIS build"
        width, height = _sized(dataset.RasterXSize or 1, dataset.RasterYSize or 1, args)
        gdal.Translate(memory, dataset, format="PNG", width=width, height=height)
        handle = gdal.VSIFOpenL(memory, "rb")
        if handle is None:
            return None, "GDAL wrote no image for that PDF"
        try:
            gdal.VSIFSeekL(handle, 0, 2)
            length = gdal.VSIFTellL(handle)
            gdal.VSIFSeekL(handle, 0, 0)
            raw = gdal.VSIFReadL(1, length, handle)
        finally:
            gdal.VSIFCloseL(handle)
    except Exception as exc:  # noqa: BLE001
        return None, f"GDAL could not rasterise the PDF ({exc})"
    finally:
        dataset = None
        try:
            from osgeo import gdal as _gdal

            _gdal.Unlink(memory)
        except Exception:  # nosec B110
            pass
    from qgis.PyQt.QtGui import QImage

    image = QImage()
    if not image.loadFromData(raw):
        return None, "the page GDAL drew did not decode"
    return image, ""


def file_image(args: dict) -> dict:






    path = str(args.get("path") or "").strip()
    if not path:
        return {"_error": "render_map with target file needs path, the file to look at.",
                "_code": "INVALID_ARGS",
                "_suggestion": "export_file returns the path this needs, for example the PDF it wrote."}
    path_error = validate_path(path)
    if path_error:
        return {"_error": path_error, "_code": "INVALID_ARGS"}
    if not os.path.isfile(path):
        return {"_error": f"No file at {path}.", "_code": "INVALID_ARGS",
                "_suggestion": "export_file answers with the path it wrote; that is what this needs."}
    try:
        page = int(args.get("page") or 1)
    except (TypeError, ValueError):
        page = 1
    suffix = os.path.splitext(path)[1].lower()

    if suffix == ".pdf":
        from .pdf_facts import pdf_facts

        structure = pdf_facts(path)
        image, why = _pdf_with_qt(path, page, args)
        if image is None:
            fallback, gdal_why = _pdf_with_gdal(path, args)
            if fallback is not None:
                note = ("This QGIS reads a PDF through GDAL, which draws the first page only."
                        if page > 1 else "")
                return {"image": fallback, "facts": {"source_file": path, "page": 1, "pdf": structure,
                                                     **({"note": note} if note else {})}}



            return {"_error": f"This QGIS cannot turn {os.path.basename(path)} into a picture: "
                              f"{why or gdal_why}.",
                    "_code": "EXECUTION_FAILED",
                    "pdf": structure,
                    "_suggestion": "A PNG from export_file opens here; target layout shows the layout "
                                   "page itself."}
        return {"image": image, "facts": {"source_file": path, "page": page, "pdf": structure}}

    if suffix == ".svg":
        image, why = _svg_image(path, args)
        if image is None:
            return {"_error": f"Could not read {os.path.basename(path)}: {why}.",
                    "_code": "EXECUTION_FAILED",
                    "_suggestion": "A PNG from export_file opens here."}
        return {"image": image, "facts": {"source_file": path}}

    if suffix not in _PIXMAP_SUFFIXES:
        return {"_error": f"render_map cannot draw {suffix or 'that file'}.", "_code": "INVALID_ARGS",
                "_suggestion": "It reads PNG, JPG, WebP, GIF, BMP, TIFF, SVG and PDF. For a layout, "
                               "pass target layout and the layout name."}
    from qgis.PyQt.QtGui import QImage

    image = QImage(path)
    if image.isNull():
        return {"_error": f"{os.path.basename(path)} did not decode as a picture.",
                "_code": "EXECUTION_FAILED",
                "_suggestion": "A run that wrote it can export it again."}
    facts = {"source_file": path, "file_pixels": {"width": image.width(), "height": image.height()}}
    return {"image": _under_render_cap(image, args), "facts": facts}


def _under_render_cap(image, args: dict):





    from qgis.PyQt.QtCore import Qt

    from .advanced_render import _clamp_render_size

    width, height = _sized(image.width() or 1, image.height() or 1, args)
    width, height, _note = _clamp_render_size(width, height)
    if width >= image.width() and height >= image.height():
        return image
    smooth = enum_member(Qt, "TransformationMode", "SmoothTransformation")
    return image.scaled(QSize(width, height),
                        enum_member(Qt, "AspectRatioMode", "KeepAspectRatio"), smooth)
