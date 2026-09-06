# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Guard the tools that spend the user's imagery credits."""





















from __future__ import annotations

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsGeometry,
    QgsProject,
    QgsRectangle,
)
from qgis.utils import iface

from ..core import limits

CONFIRM_KM2 = 1.0
HARD_MAX_KM2 = 10.0

SEGMENTATION = "AI Segmentation"

COSTLY = {
    ("ai_segment", "detect_auto"): SEGMENTATION,
    ("ai_edit", "generate"): "AI Edit",
}






COSTLY_TOOLS = {f"{name}_{action}": label for (name, action), label in COSTLY.items()}



ZONE_ARGUMENTS = (
    "Call again with the zone: bbox [xmin,ymin,xmax,ymax] in the canvas CRS, or zone_wkt "
    "(a WKT polygon), or use_canvas_extent true for the current view (get_canvas_extent "
    "gives you both). State the area to the user, and pass confirm_area_km2 above "
    f"{CONFIRM_KM2:.0f} km²."
)


def costly_label(name: str, args: dict) -> str | None:
    """The product name when this call spends imagery credits, else None."""




    label = COSTLY.get((name, str((args or {}).get("action") or "")))
    return label if label is not None else COSTLY_TOOLS.get(name)


def _canvas_crs() -> QgsCoordinateReferenceSystem:
    try:
        return iface.mapCanvas().mapSettings().destinationCrs()
    except Exception:  # noqa: BLE001
        return QgsProject.instance().crs()


def zone_from_args(args: dict) -> QgsGeometry | None:
    """The zone the call carries, in the canvas CRS, or None when it carries none."""
    wkt = str(args.get("zone_wkt") or "").strip()
    if wkt:
        geom = QgsGeometry.fromWkt(wkt)
        return geom if not geom.isNull() else None
    bbox = args.get("bbox")
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
        return QgsGeometry.fromRect(QgsRectangle(xmin, ymin, xmax, ymax))
    if args.get("use_canvas_extent"):
        try:
            return QgsGeometry.fromRect(iface.mapCanvas().extent())
        except Exception:  # noqa: BLE001
            return None
    return None


def _to_canvas_crs(geom: QgsGeometry, crs) -> QgsGeometry | None:
    """``geom`` read in ``crs``, expressed in the canvas CRS. None when it cannot be moved."""
    target = _canvas_crs()
    if crs is None or target is None:
        return geom
    try:
        if not crs.isValid() or not target.isValid() or crs == target:
            return geom
        moved = QgsGeometry(geom)
        moved.transform(QgsCoordinateTransform(crs, target, QgsProject.instance()))
        return moved
    except Exception:  # noqa: BLE001 - a half-moved zone would measure ground nobody asked for
        return None


def plugin_zone() -> QgsGeometry | None:
    """The zone AI Segmentation is holding right now, in the canvas CRS."""






    try:
        from .adapters.ai_segmentation import AiSegmentationAdapter
        zone = AiSegmentationAdapter().current_zone()
    except Exception:  # noqa: BLE001 - the guard never breaks the call it inspects
        return None
    if not zone:
        return None
    geom, crs = zone
    if geom is None:
        return None
    return _to_canvas_crs(geom, crs)


def zone_geometry(args: dict, label: str = "") -> QgsGeometry | None:
    """The zone this call will run on, in the canvas CRS, or None when unknown."""




    geom = zone_from_args(args)
    if geom is not None:
        return geom
    if label == SEGMENTATION:
        return plugin_zone()
    return None


def _area_km2(geom: QgsGeometry | None) -> float | None:
    if geom is None:
        return None
    measure = QgsDistanceArea()
    measure.setSourceCrs(_canvas_crs(), QgsProject.instance().transformContext())
    measure.setEllipsoid("WGS84")
    try:
        return abs(measure.measureArea(geom)) / 1e6
    except Exception:  # noqa: BLE001
        return None


def zone_area_km2(args: dict, label: str = "") -> float | None:
    return _area_km2(zone_geometry(args, label))


def check(name: str, args: dict) -> dict:
    """{label, area_km2, sentence} when allowed, or {error, suggestion} when refused."""
    label = costly_label(name, args)
    if label is None:
        return {}
    area = zone_area_km2(args, label)
    if area is None:


        return {
            "error": (f"{label} bills by the square kilometre and this call carries no zone that can be "
                      "measured, so it was not run."),
            "suggestion": ZONE_ARGUMENTS,
            "code": limits.CEILING_CODE,
        }
    if area > HARD_MAX_KM2:
        return {
            "error": f"Zone too large for one {label} run: {area:.1f} km², the cap is {HARD_MAX_KM2:.0f} km².",
            "suggestion": ("Propose a smaller zone (under 1 km² for a first test) and ask the user which "
                           "part of the area matters most. Never split the cap into many runs without "
                           "an explicit request."),
            "code": limits.CEILING_CODE,
        }
    if area > CONFIRM_KM2:
        confirmed = args.get("confirm_area_km2")
        try:
            confirmed = float(confirmed)
        except (TypeError, ValueError):
            confirmed = None
        if confirmed is None or abs(confirmed - area) > max(0.15, area * 0.1):
            return {
                "error": (f"This {label} run covers {area:.1f} km² and spends the user's credits accordingly. "
                          "Tell the user the area and the cost in plain words, wait for a yes, then call "
                          f"again with confirm_area_km2={area:.1f}."),
                "suggestion": "Suggest a test zone under 1 km² first if the user has not run this before.",
                "code": limits.CEILING_CODE,
            }
    shown = f"{area:.2f} km²" if area < 1 else f"{area:.1f} km²"
    return {"label": label, "area_km2": round(area, 3),
            "sentence": f"Run {label} on {shown} of imagery. This spends your {label} credits."}
