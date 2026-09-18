# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only quality checks for print-layout items."""

import math

from qgis.core import QgsLayoutItemLegend, QgsLayoutItemScaleBar

_CHECKED_TYPES = (QgsLayoutItemLegend, QgsLayoutItemScaleBar)
_MAX_ITEMS = 32
_MAX_PAGES = 64


def _corners(item):
    rect = item.rect()
    return [item.mapToScene(point) for point in (
        rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()
    )]


def _inside(item, page):
    """Check item corners in page-local layout units, including page rotation."""
    page_rect = page.rect()
    tolerance = 0.01
    for point in _corners(item):
        local = page.mapFromScene(point)
        if not (page_rect.left() - tolerance <= local.x() <= page_rect.right() + tolerance
                and page_rect.top() - tolerance <= local.y() <= page_rect.bottom() + tolerance):
            return False
    return True


def assess_layout(layout) -> dict:
    """Assess exportable legends and scale bars against their containing pages."""




    try:
        pages = layout.pageCollection()
        page_count = pages.pageCount()
        if page_count == 0:
            return {"status": "unknown", "items": [], "warnings": ["Layout has no pages."]}
        if page_count > _MAX_PAGES:
            return {"status": "unknown", "items": [],
                    "warnings": [f"Layout quality check capped at {_MAX_PAGES} pages."]}
        page_data = [(index, pages.page(index)) for index in range(page_count)]
        items = [item for item in layout.items() if isinstance(item, _CHECKED_TYPES)]
    except Exception as exc:  # noqa: BLE001 - quality evidence must remain non-blocking
        return {"status": "unknown", "items": [], "warnings": [f"Could not inspect layout pages: {exc}"]}

    evidence = []
    warnings = []
    for item in items[:_MAX_ITEMS]:
        row = {"type": type(item).__name__, "uuid": "unknown", "excluded_from_exports": False}
        try:
            row["uuid"] = item.uuid()
            row["type"] = type(item).__name__
            excluded = bool(item.excludeFromExports())
            row["excluded_from_exports"] = excluded
            visible = bool(item.isVisible())
            row["visible"] = visible
            if excluded or not visible:
                row["status"] = "excluded"
                evidence.append(row)
                continue
            local_rect = item.rect()
            rect = item.mapRectToScene(local_rect)
            values = [rect.x(), rect.y(), rect.width(), rect.height(), local_rect.width(), local_rect.height()]
            finite = all(math.isfinite(float(value)) for value in values)
            if not finite or local_rect.width() <= 0 or local_rect.height() <= 0:
                raise ValueError("item geometry is empty or non-finite")
            row["geometry"] = {
                "x": round(rect.x(), 2), "y": round(rect.y(), 2),
                "width": round(rect.width(), 2), "height": round(rect.height(), 2),
            }
            containing = []
            for page_index, page in page_data:
                if _inside(item, page):
                    containing.append((page_index, page))
            if containing:
                page_index, page = containing[0]
                page_rect = page.mapRectToScene(page.rect())
                row["page_index"] = page_index
                row["page_geometry"] = {
                    "x": round(page_rect.x(), 2), "y": round(page_rect.y(), 2),
                    "width": round(page_rect.width(), 2), "height": round(page_rect.height(), 2),
                }
                row["status"] = "ok"
            else:
                row["status"] = "warning"
                row["warning"] = "Item extends outside every page and may be clipped on export."
                warnings.append(f"{row['type']} {row['uuid']} extends outside every page.")
        except Exception as exc:  # noqa: BLE001 - unknown is safer than a silent pass
            row["status"] = "unknown"
            row["warning"] = f"Could not assess item geometry: {exc}"
            warnings.append(f"Could not assess {row['type']} {row['uuid']}: {exc}")
        evidence.append(row)

    if len(items) > _MAX_ITEMS:
        warnings.append(f"Layout quality check capped at {_MAX_ITEMS} legend/scale-bar items.")
    if any(row.get("status") == "unknown" for row in evidence):
        status = "unknown"
    elif any(row.get("status") == "warning" for row in evidence):
        status = "warning"
    elif len(items) > _MAX_ITEMS:
        status = "unknown"
    else:
        status = "ok"
    return {"status": status, "scope": "legend_and_scale_bar_page_containment",
            "items": evidence, "warnings": warnings[:_MAX_ITEMS]}
