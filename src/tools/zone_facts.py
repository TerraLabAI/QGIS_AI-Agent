# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

import math

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
)
from qgis.PyQt.QtCore import QCoreApplication
from qgis.utils import iface

from ..core import limits, siblings, zone_of_interest
from ..core.follow import _crs_holds
from ..core.logger import log_warning


def _tr(text: str) -> str:
    return QCoreApplication.translate("ToolExecutor", text)


SEGMENTATION = siblings.AI_SEGMENTATION.label
EDIT = siblings.AI_EDIT.label


def _canvas_crs() -> QgsCoordinateReferenceSystem:
    try:
        return iface.mapCanvas().mapSettings().destinationCrs()
    except Exception:  # noqa: BLE001
        return QgsProject.instance().crs()


def read_crs(args: dict) -> tuple[QgsCoordinateReferenceSystem, bool]:



    named = str(args.get("crs") or "").strip()
    if named:
        return QgsCoordinateReferenceSystem(named), True
    return _canvas_crs(), False





MIN_SIDE_M = 1.0


def ground_refusal(geom: QgsGeometry, crs: QgsCoordinateReferenceSystem, named: bool,
                   subject: str, given: str = "") -> dict | None:


    try:
        return _ground_refusal(geom, crs, named, subject, given)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"The zone's ground could not be checked: {exc}")
        return None


def _ground_refusal(geom: QgsGeometry, crs: QgsCoordinateReferenceSystem, named: bool,
                    subject: str, given: str = "") -> dict | None:









    def refused(why: str, hint: str, **facts) -> dict:
        return {"error": f"{subject}: {why}", "suggestion": "", "code": limits.CEILING_CODE,
                "hint": hint, **facts}

    if named and not crs.isValid():
        return refused(f"crs '{given}' is not a CRS QGIS knows.", "zone_crs_unknown", crs=given)
    project = QgsProject.instance().crs()
    if not named and project.isValid() and crs.isValid() and project != crs:
        return refused(
            f"the numbers carry no crs, and the project ({project.authid() or project.description()}) "
            f"and the map canvas ({crs.authid() or crs.description()}) are in different CRSs, so they "
            "could be in either.", "zone_crs_ambiguous",
            project_crs=project.authid(), canvas_crs=crs.authid())
    box = geom.boundingBox()
    read_in = (crs.authid() or crs.description()) + ("" if named else " (the map canvas CRS: no crs was given)")
    numbers = f"[{box.xMinimum():.8g}, {box.yMinimum():.8g}, {box.xMaximum():.8g}, {box.yMaximum():.8g}]"
    geographic = QgsCoordinateReferenceSystem("EPSG:4326")
    try:
        place = QgsCoordinateTransform(crs, geographic, QgsProject.instance()).transformBoundingBox(box)
        placed = not place.isNull() and all(
            abs(v) < 1e9 for v in (place.xMinimum(), place.yMinimum(), place.xMaximum(), place.yMaximum()))
    except Exception:  # noqa: BLE001
        placed = False
    if not placed or not _crs_holds(crs, place):
        try:
            bounds = crs.bounds()
            where = (f" ({bounds.xMinimum():.6g} to {bounds.xMaximum():.6g} longitude, "
                     f"{bounds.yMinimum():.6g} to {bounds.yMaximum():.6g} latitude)")
        except Exception:  # noqa: BLE001
            where = ""
        return refused(f"{numbers} read in {read_in} lies outside where that CRS is defined{where}.",
                       "zone_outside_crs", crs=crs.authid())
    measure = QgsDistanceArea()
    measure.setSourceCrs(crs, QgsProject.instance().transformContext())
    measure.setEllipsoid("WGS84")
    try:
        mid_x, mid_y = box.center().x(), box.center().y()
        width = measure.measureLine(QgsPointXY(box.xMinimum(), mid_y), QgsPointXY(box.xMaximum(), mid_y))
        height = measure.measureLine(QgsPointXY(mid_x, box.yMinimum()), QgsPointXY(mid_x, box.yMaximum()))
    except Exception:  # noqa: BLE001
        return None
    if min(width, height) < MIN_SIDE_M:
        centre = place.center()
        return refused(
            f"{numbers} read in {read_in} is {width:.3g} m by {height:.3g} m of ground, at longitude "
            f"{centre.x():.6g}, latitude {centre.y():.6g}.", "zone_under_a_metre",
            width_m=round(width, 4), height_m=round(height, 4), crs=crs.authid())
    return None


def _read_zone(args: dict, label: str):


    given = str(args.get("crs") or "").strip()
    if args.get("use_zone"):
        held = zone_of_interest.read_zone()
        return (held.geometry, held.crs, True, "") if held is not None else None
    if str(args.get("zone_wkt") or "").strip() and label == SEGMENTATION:

        return None
    box = _bbox_rect(args.get("bbox"))
    if box is None:
        return None
    crs, named = read_crs(args)
    return QgsGeometry.fromRect(box), crs, named, given


def _bbox_rect(bbox) -> QgsRectangle | None:
    if isinstance(bbox, dict):
        try:
            bbox = [bbox["xmin"], bbox["ymin"], bbox["xmax"], bbox["ymax"]]
        except KeyError:
            return None
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            xmin, ymin, xmax, ymax = (float(v) for v in bbox)
        except (TypeError, ValueError):
            return None
        return QgsRectangle(xmin, ymin, xmax, ymax)
    return None


def unknown_crs(args: dict) -> str:

    crs, named = read_crs(args)
    return str(args.get("crs")).strip() if named and not crs.isValid() else ""


def zone_from_args(args: dict) -> QgsGeometry | None:








    if args.get("use_zone"):
        held = zone_of_interest.read_zone()
        if held is not None:
            moved = zone_of_interest.to_crs(held.geometry, held.crs, _canvas_crs())
            if moved is not None:
                return moved
        return None
    wkt = str(args.get("zone_wkt") or "").strip()
    if wkt:
        geom = QgsGeometry.fromWkt(wkt)
        return geom if not geom.isNull() else None
    if args.get("bbox") is not None:
        box = _bbox_rect(args.get("bbox"))
        crs, _named = read_crs(args)
        if box is None or not crs.isValid():
            return None
        return _to_canvas_crs(QgsGeometry.fromRect(box), crs)
    if args.get("use_canvas_extent"):
        try:
            return QgsGeometry.fromRect(iface.mapCanvas().extent())
        except Exception:  # noqa: BLE001
            return None
    return None


def _to_canvas_crs(geom: QgsGeometry, crs) -> QgsGeometry | None:

    target = _canvas_crs()
    if crs is None or target is None:
        return geom
    try:
        if not crs.isValid() or not target.isValid() or crs == target:
            return geom
        moved = QgsGeometry(geom)
        moved.transform(QgsCoordinateTransform(crs, target, QgsProject.instance()))
        return moved
    except Exception:  # noqa: BLE001
        return None


def _area_km2(geom: QgsGeometry | None) -> float | None:
    if geom is None:
        return None
    measure = QgsDistanceArea()
    measure.setSourceCrs(_canvas_crs(), QgsProject.instance().transformContext())
    measure.setEllipsoid("WGS84")
    try:
        area = abs(measure.measureArea(geom)) / 1e6
    except Exception:  # noqa: BLE001
        return None
    if math.isfinite(area):
        return area



    from ..core.invariants import metres_per_map_unit

    factor = metres_per_map_unit(_canvas_crs())
    flat = abs(geom.area()) * factor * factor / 1e6 if factor else float("nan")
    return flat if math.isfinite(flat) and flat > 0 else None


DECLINED_PROPERTY = "terralab/zone_declined"


def clear_declined() -> None:
    layer = zone_of_interest.zone_layer()
    if layer is not None:
        layer.removeCustomProperty(DECLINED_PROPERTY)


def area_text(area: float) -> str:


    if area >= 1:
        return _tr("{area} km²").format(area=f"{area:.1f}")
    if area >= 0.1:
        return _tr("{area} km²").format(area=f"{area:.2f}")
    if area >= 0.0001:
        return _tr("{area} ha").format(area=f"{area * 100:.2f}")
    return _tr("{area} m²").format(area=f"{area * 1e6:.0f}")
