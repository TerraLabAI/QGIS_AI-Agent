# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






























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

from ..core import limits, zone_of_interest

CONFIRM_KM2 = 1.0






DETACH_KM2 = 5.0

SEGMENTATION = "AI Segmentation"

COSTLY = {
    ("ai_segment", "detect_auto"): SEGMENTATION,
    ("ai_edit", "generate"): "AI Edit",
}






COSTLY_TOOLS = {f"{name}_{action}": label for (name, action), label in COSTLY.items()}



ZONE_ARGUMENTS = (
    "use_zone true names a zone of interest already in the project (zone "
    "action get says so; zone action set makes one from a layer the user named), or bbox "
    "[xmin,ymin,xmax,ymax] in the canvas CRS, or zone_wkt (a WKT polygon), or use_canvas_extent "
    "true for the current view (get_canvas_extent gives both). Above "
    f"{CONFIRM_KM2:.0f} km², confirm_area_km2 is the user's yes to the area once stated to them."
)


def costly_label(name: str, args: dict) -> str | None:





    label = COSTLY.get((name, str((args or {}).get("action") or "")))
    return label if label is not None else COSTLY_TOOLS.get(name)


def _canvas_crs() -> QgsCoordinateReferenceSystem:
    try:
        return iface.mapCanvas().mapSettings().destinationCrs()
    except Exception:  # noqa: BLE001
        return QgsProject.instance().crs()


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


def plugin_zone() -> QgsGeometry | None:







    try:
        from .adapters.ai_segmentation import AiSegmentationAdapter
        zone = AiSegmentationAdapter().current_zone()
    except Exception:  # noqa: BLE001
        return None
    if not zone:
        return None
    geom, crs = zone
    if geom is None:
        return None
    return _to_canvas_crs(geom, crs)


def zone_geometry(args: dict, label: str = "") -> QgsGeometry | None:





    geom = zone_from_args(args)
    if args.get("use_zone"):
        return geom
    if geom is not None and args.get("zone_wkt") and label == SEGMENTATION:


        from .integration_tools import AISEG_KEYS, _aiseg_raster_layer, _find_plugin
        _, plugin = _find_plugin(AISEG_KEYS)
        layer = _aiseg_raster_layer(plugin, args.get("layer_name"))
        return _to_canvas_crs(geom, layer.crs()) if layer is not None else None
    if geom is not None:
        return geom
    if label == SEGMENTATION:
        return plugin_zone()
    if label == "AI Edit":
        from .adapters.ai_edit_access import ACCESS
        held = ACCESS.current_zone()
        return _to_canvas_crs(*held) if held is not None else None
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



EDIT_FALLBACK_CREDITS_2K = 30.0


def zone_area_km2(args: dict, label: str = "") -> float | None:
    return _area_km2(zone_geometry(args, label))


def segmentation_km2_left() -> float | None:







    try:
        from .integration_tools import AISEG_KEYS, _find_plugin

        _, plugin = _find_plugin(AISEG_KEYS)
        dock = getattr(plugin, "dock_widget", None) if plugin is not None else None
        reader = getattr(dock, "_auto_km2_left", None)
        value = reader() if callable(reader) else None
        return float(value) if value is not None else None
    except Exception:  # noqa: BLE001
        return None


def _balance_sentence(label: str, area: float) -> str:






    if label != SEGMENTATION:
        return ""
    left = segmentation_km2_left()
    if left is None:
        return ""
    if left < area:
        return (f" The account has {left:,.1f} km² of Automatic left this month, less than this zone "
                "covers.")
    return (f" The account has {left:,.1f} km² of Automatic left this month, "
            f"{left - area:,.1f} km² once this run is paid for.")


def detaches(args: dict, label: str = SEGMENTATION) -> bool:

    area = zone_area_km2(args, label)
    return area is not None and area > DETACH_KM2


def check(name: str, args: dict) -> dict:





    label = costly_label(name, args)
    if label is None:
        return {}



    geom = zone_geometry(args, label)
    area = _area_km2(geom)
    if area is None:


        return {
            "error": (f"{label} needs a measurable imagery footprint before approval; this call carries "
                      "no zone that can be measured, so it was not run."),
            "suggestion": ZONE_ARGUMENTS,
            "code": limits.CEILING_CODE,
        }
    try:
        from .integration_handoff import spending_inputs
        inputs = spending_inputs(label, args, geom)
    except (ValueError, TypeError, RuntimeError, AttributeError) as exc:
        return {"error": f"{label} inputs could not be read before approval: {exc}",
                "suggestion": "Check the source image and prepared zone in the native panel, then retry.",
                "code": limits.CEILING_CODE}
    if area > CONFIRM_KM2:
        confirmed = args.get("confirm_area_km2")
        try:
            confirmed = float(confirmed)
        except (TypeError, ValueError):
            confirmed = None
        if confirmed is None or abs(confirmed - area) > max(0.15, area * 0.1):
            return {
                "error": (f"This {label} run covers {area:.1f} km². "
                          + ("Segmentation credits follow the processed area." if label == SEGMENTATION
                             else "AI Edit credits depend on the output resolution, not this area.")
                          + f"{_balance_sentence(label, area)} confirm_area_km2={area:.1f} "
                            "states the footprint for the native approval card."),




                "suggestion": ("Keep the intended zone as one run. State its measured footprint and use "
                               "the native permission card; do not substitute a set of bounding boxes."),
                "code": limits.CEILING_CODE,
            }
    shown = f"{area:.2f} km²" if area < 1 else f"{area:.1f} km²"
    if label == SEGMENTATION:
        sentence = f"Run {label} on {shown} of imagery. This spends your {label} credits."
    else:
        resolution = inputs.get("resolution")
        size = f" at {resolution}" if resolution else " at the panel's selected resolution"
        price = inputs.get("generation_credits")
        if price is not None:
            cost = f"{price:g} credits per generation"
        else:


            cost = (f"about {EDIT_FALLBACK_CREDITS_2K:g} credits per generation"
                    " (the 2K price; the live price was unavailable)")
        sentence = f"Run AI Edit{size}: {cost}. Image footprint: {shown}; billing is per generation."
    return {"label": label, "area_km2": round(area, 3), "sentence": sentence, "inputs": inputs}
