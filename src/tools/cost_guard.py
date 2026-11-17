# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






























from __future__ import annotations

from qgis.core import (
    QgsGeometry,
    QgsProject,
    QgsRectangle,
)
from qgis.PyQt.QtCore import QTimer
from qgis.utils import iface

from ..core import canvas_glide, limits, siblings, zone_of_interest
from ..core.follow import hold_view
from ..core.logger import log, log_warning
from ..core.numbers import as_float
from . import integration_handoff
from .adapters.ai_edit_access import ACCESS
from .handoff_reads import _aiseg_raster_layer
from .zone_facts import (
    DECLINED_PROPERTY,
    EDIT,
    SEGMENTATION,
    _area_km2,
    _canvas_crs,
    _read_zone,
    _to_canvas_crs,
    _tr,
    area_text,
    clear_declined,
    ground_refusal,
    zone_from_args,
)

CONFIRM_KM2 = 1.0







DETACH_KM2 = 5.0


COSTLY = {
    ("ai_segment", "detect_auto"): SEGMENTATION,
    ("ai_edit", "generate"): EDIT,
}






COSTLY_TOOLS = {f"{name}_{action}": label for (name, action), label in COSTLY.items()}

def costly_label(name: str, args: dict) -> str | None:





    label = COSTLY.get((name, str((args or {}).get("action") or "")))
    return label if label is not None else COSTLY_TOOLS.get(name)


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


        _, plugin = siblings.find(siblings.AI_SEGMENTATION)
        layer = _aiseg_raster_layer(plugin, args.get("layer_name"))
        return _to_canvas_crs(geom, layer.crs()) if layer is not None else None
    if geom is not None:
        return geom
    if label == SEGMENTATION:
        return plugin_zone()
    if label == EDIT:
        held = ACCESS.current_zone()
        return _to_canvas_crs(*held) if held is not None else None
    return None



EDIT_FALLBACK_CREDITS_2K = 30.0


def zone_area_km2(args: dict, label: str = "") -> float | None:
    return _area_km2(zone_geometry(args, label))


def segmentation_km2_left() -> float | None:







    try:
        _, plugin = siblings.find(siblings.AI_SEGMENTATION)
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
        if zone_from_args(args) is not None:


            for key in ("zone_wkt", "bbox", "crs", "use_canvas_extent"):
                args.pop(key, None)
            args["use_zone"] = True

    _frame(geom.boundingBox())
    return prior


def mark_declined() -> None:



    held = zone_of_interest.read_zone()
    layer = zone_of_interest.zone_layer()
    if held is not None and layer is not None:
        layer.setCustomProperty(DECLINED_PROPERTY, held.geometry.asWkt())
        log("Credits card refused: the proposed zone stays on the map, marked as declined.")


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
    said = as_float(args.get("confirm_area_km2"))
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
        inputs = integration_handoff.spending_inputs(label, args, geom)
    except (ValueError, TypeError, RuntimeError, AttributeError) as exc:
        return {"error": f"{label} inputs could not be read before approval: {exc}",
                "suggestion": "", "code": limits.CEILING_CODE,
                "hint": "spending_inputs_unreadable", "label": label}
    if area > CONFIRM_KM2:
        confirmed = args.get("confirm_area_km2")
        confirmed = as_float(confirmed)
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
