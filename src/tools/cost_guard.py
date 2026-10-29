# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






























from __future__ import annotations

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
)
from qgis.PyQt.QtCore import QCoreApplication, QTimer
from qgis.utils import iface

from ..core import canvas_glide, limits, zone_of_interest
from ..core.logger import log, log_warning

CONFIRM_KM2 = 1.0


def _tr(text: str) -> str:
    return QCoreApplication.translate("ToolExecutor", text)






DETACH_KM2 = 5.0

SEGMENTATION = "AI Segmentation"

COSTLY = {
    ("ai_segment", "detect_auto"): SEGMENTATION,
    ("ai_edit", "generate"): "AI Edit",
}






COSTLY_TOOLS = {f"{name}_{action}": label for (name, action), label in COSTLY.items()}

def costly_label(name: str, args: dict) -> str | None:





    label = COSTLY.get((name, str((args or {}).get("action") or "")))
    return label if label is not None else COSTLY_TOOLS.get(name)


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








    from ..core.follow import _crs_holds

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


def _zone_name_on_card(args: dict) -> str:


    held = zone_of_interest.read_zone() if args.get("use_zone") else None
    layer = QgsProject.instance().mapLayer(held.layer_id) if held is not None else zone_of_interest.zone_layer()
    return layer.name() if layer is not None else zone_of_interest.zone_layer_name()


def show_outline(args: dict, costly: dict):














    wkt = costly.get("outline_wkt")
    if not wkt:
        return None
    geom = QgsGeometry.fromWkt(wkt)
    held = zone_of_interest.read_zone() if args.get("use_zone") else None
    prior = None
    if held is None:
        prior = zone_of_interest.read_zone()
        layer = zone_of_interest.write_zone(geom, _canvas_crs())
        clear_declined()
        costly["zone_proposed"] = prior is None
        if layer is None:
            log_warning("The zone of interest layer could not be written before the credits card.")
            _frame(geom.boundingBox())
            return None
        from ..core.layer_order import keep_place
        keep_place(layer)
        if zone_from_args(args) is not None:


            for key in ("zone_wkt", "bbox", "crs", "use_canvas_extent"):
                args.pop(key, None)
            args["use_zone"] = True

    _frame(geom.boundingBox())
    return prior


DECLINED_PROPERTY = "terralab/zone_declined"


def mark_declined() -> None:



    held = zone_of_interest.read_zone()
    layer = zone_of_interest.zone_layer()
    if held is not None and layer is not None:
        layer.setCustomProperty(DECLINED_PROPERTY, held.geometry.asWkt())
        log("Credits card refused: the proposed zone stays on the map, marked as declined.")


def clear_declined() -> None:
    layer = zone_of_interest.zone_layer()
    if layer is not None:
        layer.removeCustomProperty(DECLINED_PROPERTY)


def zone_declined() -> bool:

    layer = zone_of_interest.zone_layer()
    held = zone_of_interest.read_zone()
    marked = layer.customProperty(DECLINED_PROPERTY) if layer is not None else None
    if not marked or held is None:
        return False
    try:
        return bool(held.geometry.equals(QgsGeometry.fromWkt(str(marked))))
    except Exception:  # noqa: BLE001
        return False


def restore_zone(prior) -> None:

    if prior is None:
        return
    try:
        if zone_of_interest.write_zone(prior.geometry, prior.crs, label=prior.label or "") is not None:
            log("Credits card refused: the zone of interest is back to the one the project held before.")
    except Exception as exc:  # noqa: BLE001
        log_warning(f"The previous zone of interest could not be put back: {exc}")




_REFRAME_MS = (0, 1500)


_FRAMING = 0


def stop_framing() -> None:

    global _FRAMING
    _FRAMING += 1


def _same_box(a, b) -> bool:
    tol = max(b.width(), b.height(), 1e-9) * 1e-6
    return all(abs(x - y) <= tol for x, y in ((a.xMinimum(), b.xMinimum()), (a.yMinimum(), b.yMinimum()),
                                             (a.xMaximum(), b.xMaximum()), (a.yMaximum(), b.yMaximum())))


def _frame(box: QgsRectangle) -> None:








    from ..core.follow import hold_view

    global _FRAMING
    _FRAMING += 1
    framing = _FRAMING
    box = QgsRectangle(box)
    box.scale(1.1)
    crs = _canvas_crs()
    try:
        before = QgsRectangle(iface.mapCanvas().extent())
    except Exception:  # noqa: BLE001
        before = None

    def put(again: bool, last: bool) -> None:
        try:
            canvas = iface.mapCanvas()
            if again and framing != _FRAMING:

                return
            if again and (before is None or not _same_box(canvas.extent(), before)):


                if last:
                    log("Credits card: the map is left where it is; a pan from now on stays.")
                return
            if again and crs is not None and _canvas_crs() != crs:

                return
            if again and canvas.extent().intersects(box):
                if last:
                    log("Credits card: the map shows the zone; it is left where it is, and a pan from "
                        "now on stays.")
                return

            moved = hold_view(canvas, target=(box, crs)) if crs is not None else None
            if moved is None:
                with canvas_glide.gliding(canvas):
                    canvas.setExtent(box)
                    canvas.refresh()
            log(("Credits card: the map had left the zone and is moved back onto it"
                 if again else "Credits card: the map is moved onto the zone")
                + (f" (project CRS {moved['from']} -> {moved['to']})" if moved else "") + ".")
        except Exception as exc:  # noqa: BLE001
            log_warning(f"The map could not be moved onto the zone before the credits card: {exc}")

    put(False, False)
    final = len(_REFRAME_MS) - 1
    for i, delay in enumerate(_REFRAME_MS):
        QTimer.singleShot(delay, lambda last=(i == final): put(True, last))


def _number(value) -> float | None:
    try:
        return float(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def area_text(area: float) -> str:


    if area >= 1:
        return _tr("{area} km²").format(area=f"{area:.1f}")
    if area >= 0.1:
        return _tr("{area} km²").format(area=f"{area:.2f}")
    if area >= 0.0001:
        return _tr("{area} ha").format(area=f"{area * 100:.2f}")
    return _tr("{area} m²").format(area=f"{area * 1e6:.0f}")


def detaches(args: dict, label: str = SEGMENTATION) -> bool:

    area = zone_area_km2(args, label)
    return area is not None and area > DETACH_KM2


def check(name: str, args: dict) -> dict:





    label = costly_label(name, args)
    if label is None:
        return {}
    if args.get("use_zone") and zone_declined():

        return {
            "error": (f"{label} was not run: the Area of interest holds the zone the user declined on the "
                      "last card."),
            "suggestion": "", "code": limits.CEILING_CODE, "hint": "zone_declined", "label": label,
        }


    try:
        read = _read_zone(args, label)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"The zone's numbers could not be read for the ground check: {exc}")
        read = None
    refusal = ground_refusal(*read[:3], f"{label} was not run", read[3]) if read is not None else None
    if refusal is not None:
        return {**refusal, "label": label}



    geom = zone_geometry(args, label)
    if read is None and geom is not None and not args.get("use_canvas_extent"):


        refusal = ground_refusal(geom, _canvas_crs(), True, f"{label} was not run")
        if refusal is not None:
            return {**refusal, "label": label}
    area = _area_km2(geom)
    if area is None:


        return {
            "error": (f"{label} needs a measurable imagery footprint before approval; this call carries "
                      "no zone that can be measured, so it was not run."),


            "suggestion": "",
            "code": limits.CEILING_CODE,
            "hint": "zone_unmeasurable", "label": label, "confirm_km2": CONFIRM_KM2,
        }
    said = _number(args.get("confirm_area_km2"))
    if said is not None and said > 0 and not (0.5 <= area / said <= 2) and area_text(area) != area_text(said):

        where = "the Area of interest holds" if args.get("use_zone") else "this call's zone covers"
        return {
            "error": (f"{label} was not run: {where} {area_text(area)}, and confirm_area_km2 says "
                      f"{area_text(said)}. The zone is what was last set, not the area named since."),
            "suggestion": "", "code": limits.CEILING_CODE, "hint": "zone_not_the_area_said",
            "label": label, "area_km2": round(area, 4), "said_km2": said,
        }
    zone_name = _zone_name_on_card(args)
    try:
        from .integration_handoff import spending_inputs
        inputs = spending_inputs(label, args, geom)
    except (ValueError, TypeError, RuntimeError, AttributeError) as exc:
        return {"error": f"{label} inputs could not be read before approval: {exc}",
                "suggestion": "", "code": limits.CEILING_CODE,
                "hint": "spending_inputs_unreadable", "label": label}
    if area > CONFIRM_KM2:
        confirmed = args.get("confirm_area_km2")
        try:
            confirmed = float(confirmed)
        except (TypeError, ValueError):
            confirmed = None
        if confirmed is None or abs(confirmed - area) > max(0.15, area * 0.1):
            return {
                "error": f"This {label} run covers {area:.1f} km².{_balance_sentence(label, area)}",



                "suggestion": "",
                "code": limits.CEILING_CODE,
                "hint": "area_confirmation_needed", "variant": "segmentation" if label == SEGMENTATION else "edit",
                "label": label, "area_km2": round(area, 1),
            }
    shown = area_text(area)
    if zone_name:
        shown = _tr("the '{layer}' layer ({area})").format(layer=zone_name, area=shown)
    if label == SEGMENTATION:
        sentence = _tr("Run {label} on {zone}. This spends your {label} credits.").format(label=label, zone=shown)
    else:
        resolution = inputs.get("resolution")
        size = (_tr(" at {resolution}").format(resolution=resolution) if resolution
                else _tr(" at the panel's selected resolution"))
        price = inputs.get("generation_credits")
        if price is not None:
            cost = _tr("{price} credits per generation").format(price=f"{price:g}")
        else:


            cost = _tr("about {price} credits per generation (the 2K price; the live price was unavailable)"
                       ).format(price=f"{EDIT_FALLBACK_CREDITS_2K:g}")
        sentence = _tr("Run AI Edit{size}: {cost}. Image footprint: {zone}; billing is per generation."
                       ).format(size=size, cost=cost, zone=shown)
    return {"label": label, "area_km2": round(area, 3), "sentence": sentence, "inputs": inputs,
            "outline_wkt": geom.asWkt()}
