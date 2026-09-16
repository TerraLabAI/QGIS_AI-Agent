# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later











import math

from qgis.core import QgsLayoutItem, QgsLayoutItemPage, QgsUnitTypes

from .qt_compat import enum_member

_MAX_ITEMS = 32
_MAX_PAGES = 64


def _local_rect(item):






    rect = item.rect()
    if rect.width() > 0 and rect.height() > 0:
        return rect, False
    scene = item.sceneBoundingRect()
    return scene, True


def _corners(item):
    rect, in_scene = _local_rect(item)
    points = (rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft())
    return list(points) if in_scene else [item.mapToScene(point) for point in points]


def _mm(layout, value: float) -> float:

    try:
        unit = enum_member(QgsUnitTypes, "LayoutUnit", "LayoutMillimeters")
        return round(float(layout.convertFromLayoutUnits(float(value), unit).length()), 1)
    except Exception:  # noqa: BLE001
        return round(float(value), 1)


def _box_mm(layout, rect) -> dict:
    return {"x_mm": _mm(layout, rect.x()), "y_mm": _mm(layout, rect.y()),
            "width_mm": _mm(layout, rect.width()), "height_mm": _mm(layout, rect.height())}


def _item_name(item) -> str:

    try:
        given = str(item.id() or "").strip()
    except (AttributeError, RuntimeError):
        given = ""
    return given or type(item).__name__.replace("QgsLayoutItem", "").lower() or "item"


def _inside(item, page):

    page_rect = page.rect()
    tolerance = 0.01
    for point in _corners(item):
        local = page.mapFromScene(point)
        if not (page_rect.left() - tolerance <= local.x() <= page_rect.right() + tolerance
                and page_rect.top() - tolerance <= local.y() <= page_rect.bottom() + tolerance):
            return False
    return True


def assess_layout(layout) -> dict:





    try:
        pages = layout.pageCollection()
        page_count = pages.pageCount()
        if page_count == 0:
            return {"status": "unknown", "items": [], "warnings": ["Layout has no pages."]}
        if page_count > _MAX_PAGES:
            return {"status": "unknown", "items": [],
                    "warnings": [f"Layout quality check capped at {_MAX_PAGES} pages."]}
        page_data = [(index, pages.page(index)) for index in range(page_count)]


        items = [item for item in layout.items()
                 if isinstance(item, QgsLayoutItem) and not isinstance(item, QgsLayoutItemPage)]
    except Exception as exc:  # noqa: BLE001
        return {"status": "unknown", "items": [], "warnings": [f"Could not inspect layout pages: {exc}"]}

    page_rows = []
    for page_index, page in page_data:
        try:
            page_rows.append({"page": page_index + 1, **_box_mm(layout, page.mapRectToScene(page.rect()))})
        except (AttributeError, RuntimeError):
            continue

    evidence = []
    warnings = []
    for item in items[:_MAX_ITEMS]:
        row = {"type": type(item).__name__, "uuid": "unknown", "excluded_from_exports": False}
        try:
            row["uuid"] = item.uuid()
            row["type"] = type(item).__name__
            row["item"] = _item_name(item)
            excluded = bool(item.excludeFromExports())
            row["excluded_from_exports"] = excluded
            visible = bool(item.isVisible())
            row["visible"] = visible
            if excluded or not visible:
                row["status"] = "excluded"
                evidence.append(row)
                continue
            local_rect, in_scene = _local_rect(item)
            rect = local_rect if in_scene else item.mapRectToScene(local_rect)
            values = [rect.x(), rect.y(), rect.width(), rect.height(), local_rect.width(), local_rect.height()]
            finite = all(math.isfinite(float(value)) for value in values)
            if not finite or local_rect.width() <= 0 or local_rect.height() <= 0:
                raise ValueError("item geometry is empty or non-finite")
            row["geometry"] = {
                "x": round(rect.x(), 2), "y": round(rect.y(), 2),
                "width": round(rect.width(), 2), "height": round(rect.height(), 2),
            }
            row["box_mm"] = _box_mm(layout, rect)
            containing = []
            for page_index, page in page_data:
                if _inside(item, page):
                    containing.append((page_index, page))
            if containing:
                page_index, page = containing[0]
                page_rect = page.mapRectToScene(page.rect())
                row["page_index"] = page_index
                row["page"] = page_index + 1
                row["page_geometry"] = {
                    "x": round(page_rect.x(), 2), "y": round(page_rect.y(), 2),
                    "width": round(page_rect.width(), 2), "height": round(page_rect.height(), 2),
                }
                row["inside_page"] = True
                row["status"] = "ok"
            else:


                from ..tools.postconditions import _Coded

                row["inside_page"] = False
                row["status"] = "warning"
                row["warning"] = "Item extends outside every page and is clipped on export."
                box = row["box_mm"]
                warnings.append(_Coded(
                    f"{row['item']} at {box['x_mm']}, {box['y_mm']} mm "
                    f"({box['width_mm']} x {box['height_mm']} mm) extends outside every page: "
                    "move or resize it with attemptMove and attemptResize, then look at the page again.",
                    "layout_outside_page", item=row["item"], x_mm=box["x_mm"], y_mm=box["y_mm"],
                    width_mm=box["width_mm"], height_mm=box["height_mm"]))
        except Exception as exc:  # noqa: BLE001
            row["status"] = "unknown"
            row["warning"] = f"Could not assess item geometry: {exc}"
            warnings.append(f"Could not assess {row['type']} {row['uuid']}: {exc}")
        evidence.append(row)

    if len(items) > _MAX_ITEMS:
        warnings.append(f"Layout quality check capped at {_MAX_ITEMS} items.")
    if any(row.get("status") == "unknown" for row in evidence):
        status = "unknown"
    elif any(row.get("status") == "warning" for row in evidence):
        status = "warning"
    elif len(items) > _MAX_ITEMS:
        status = "unknown"
    else:
        status = "ok"
    shown = warnings[:_MAX_ITEMS]
    result = {"status": status, "scope": "item_page_containment", "pages_mm": page_rows,
              "items": evidence, "warnings": [str(text) for text in shown]}




    from ..tools.postconditions import _Coded

    findings = [{**text.finding, "at": at} for at, text in enumerate(shown) if isinstance(text, _Coded)]
    if findings:
        result["findings"] = findings
    return result
