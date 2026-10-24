# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


















from __future__ import annotations

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsExpression,
    QgsFeatureRequest,
    QgsGeometry,
    QgsProject,
    QgsRectangle,
    QgsUnitTypes,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core import tuning
from ..core import zone_of_interest as zoi
from ..core.layer_order import keep_place
from ..core.logger import log, log_warning
from ..core.qt_compat import enum_member
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from ._layers import resolve_layer_note
from .layer_lookup import _layer_not_found_error

ZONE_ACTIONS = ("set", "get", "clear")





MAX_BUFFER_KM = 100.0







BUFFER_PROJ = ("+proj=aeqd +lat_0={lat:.6f} +lon_0={lon:.6f} +x_0=0 +y_0=0 "
               "+datum=WGS84 +units=m +no_defs")



def _area_text(area: float) -> str:

    from .cost_guard import area_text
    return area_text(area)

def register_zone_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="zone",
        danger="write",
        visible=14,
        label=QT_TRANSLATE_NOOP("AIAgent", "Area of interest[: {label}][ from {layer_name}]"),
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(ZONE_ACTIONS)},
                "layer_name": {"type": "string"},
                "expression": {"type": "string"},
                "use_selection": {"type": "boolean"},
                "wkt": {"type": "string"},
                "bbox": {
                    "type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4,
                },
                "use_canvas_extent": {"type": "boolean"},
                "crs": {"type": "string"},
                "label": {"type": "string"},
                "buffer_km": {"type": "number"},
                "zoom": {"type": "boolean"},
            },
            "required": ["action"],
        },
        handler=_zone,
    ))


def _zone(args: dict) -> dict:
    action = args.get("action")
    if action == "get":
        return _zone_get()
    if action == "clear":
        return _zone_clear()
    if action == "set":
        return _zone_set(args)
    return tool_error(f"Unknown action: {action}", "INVALID_ARGS",
                      hint="zone_actions", actions=list(ZONE_ACTIONS))





def _zone_get() -> dict:
    held = zoi.read_zone()
    if held is None:
        return {
            "zone_set": False,
            "next_step_hint": "zone_none_yet",
        }
    return _zone_result(held, "read")


def _zone_clear() -> dict:
    had = zoi.clear_zone()
    return {
        "zone_set": False,
        "cleared": had,
        "tell_user": ("The zone of interest is gone from the project."
                      if had else "There was no zone of interest to clear."),
    }





def _zone_set(args: dict) -> dict:
    found = _geometry_from_args(args)
    if isinstance(found, dict):
        return found
    geom, crs, approximate, source, parts = found
    given = str(args.get("crs") or "").strip()
    if given and not crs.isValid():
        return tool_error(f"The zone was not set: crs '{given}' is not a CRS QGIS knows.", "INVALID_ARGS",
                          hint="zone_crs_unknown", crs=given)

    buffer_km = args.get("buffer_km")
    ringed = False
    if isinstance(buffer_km, (int, float)) and not isinstance(buffer_km, bool) and buffer_km > 0:
        max_km = tuning.ceiling("zone_max_buffer_km", MAX_BUFFER_KM, 5.0)
        if buffer_km > max_km:
            return tool_error(
                f"A {buffer_km:g} km ring is wider than the zone it surrounds.",
                "INVALID_ARGS", hint="zone_buffer_too_wide", max_km=max_km)




        widened = _buffered_parts(parts, crs, float(buffer_km)) if (approximate and parts) else None
        if widened is not None:
            geom, approximate = widened, False
        else:
            widened = _buffered(geom, crs, float(buffer_km))
            if widened is None:
                return tool_error("The zone could not be widened.", "EXECUTION_FAILED",
                                  hint="zone_widen_failed")
            geom = widened
        ringed = True

    if source in ("bbox", "wkt"):


        from .cost_guard import ground_refusal
        refusal = ground_refusal(geom, crs, bool(given), "The zone was not set", given)
        if refusal is not None:
            facts = {k: v for k, v in refusal.items() if k not in ("error", "code", "suggestion")}
            return tool_error(refusal["error"], refusal["code"], **facts)

    if zoi.area_km2(geom, crs) <= 0:
        return tool_error(
            "That zone has no surface: it is a point, or several points on a line.",
            "INVALID_ARGS", hint="zone_no_surface")



    label = str(args.get("label") or "").strip() or None
    layer = zoi.write_zone(geom, crs, label=label, approximate=approximate)
    from .cost_guard import clear_declined
    clear_declined()
    if layer is None:
        return tool_error("The zone of interest layer could not be created.", "EXECUTION_FAILED",
                          hint="zone_layer_not_created")
    keep_place(layer)
    log(f"Area of interest set from {source} ({_area_text(zoi.area_km2(geom, crs))})")

    if args.get("zoom", True):
        _zoom_to(layer)



    stored = zoi.read_zone()
    held = zoi.Zone(geometry=geom, crs=crs, layer_id=layer.id(),
                    label=(stored.label if stored is not None else (label or "")),
                    approximate=approximate)
    out = _zone_result(held, source)
    out["zoom"] = bool(args.get("zoom", True))
    if ringed:
        out["widened_km"] = float(buffer_km)
    return out


def _geometry_from_args(args: dict):


    named = str(args.get("layer_name") or "").strip()
    expression = str(args.get("expression") or "").strip()
    wkt = str(args.get("wkt") or "").strip()
    bbox = args.get("bbox")
    canvas = bool(args.get("use_canvas_extent"))

    if named:
        return _from_layer(named, expression, bool(args.get("use_selection")))
    if wkt:
        geom = QgsGeometry.fromWkt(wkt)
        if geom is None or geom.isEmpty():
            return tool_error("That WKT does not describe a shape.", "INVALID_ARGS",
                              hint="zone_wkt_not_shape")
        return geom, _crs_of(args), False, "wkt", None
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            xmin, ymin, xmax, ymax = (float(v) for v in bbox)
        except (TypeError, ValueError):
            return tool_error("bbox must be four numbers.", "INVALID_ARGS",
                              hint="zone_bbox_form")
        if xmax <= xmin or ymax <= ymin:
            return tool_error("That bbox has no surface.", "INVALID_ARGS",
                              hint="zone_bbox_form")
        return (QgsGeometry.fromRect(QgsRectangle(xmin, ymin, xmax, ymax)),
                _crs_of(args), False, "bbox", None)
    if canvas:
        extent, crs = _canvas_extent()
        if extent is None:
            return tool_error("The map view could not be read.", "EXECUTION_FAILED",
                              hint="zone_view_unreadable")
        return QgsGeometry.fromRect(extent), crs, False, "canvas", None
    return tool_error(
        "zone action set needs an area to work from.", "INVALID_ARGS",
        hint="zone_set_needs_area")


def _from_layer(named: str, expression: str, use_selection: bool):
    layer, note = resolve_layer_note(named)
    if layer is None:
        return _layer_not_found_error(named)
    if expression:
        if not isinstance(layer, QgsVectorLayer):
            return tool_error(f"'{layer.name()}' has no features to filter.", "INVALID_ARGS",
                              hint="zone_expression_needs_vector")
        found = _filtered_outline(layer, expression)
        if isinstance(found, dict):
            return found
        geom, crs, approximate, parts = found
        source = f"layer {layer.name()!r} where {expression}"
    else:
        parts = None
        found = zoi.outline_of_layer(layer, selected_only=use_selection)
        if found is None:



            parts = _parts_of(layer, use_selection)
            found = _box_around(parts, layer.crs())
        if found is None:
            if use_selection:
                return tool_error(
                    f"Nothing is selected on '{layer.name()}'.", "INVALID_ARGS",
                    hint="zone_nothing_selected")
            return tool_error(f"'{layer.name()}' holds no shape to use as a zone.", "INVALID_ARGS",
                              hint="zone_layer_no_shape")
        geom, crs, approximate = found
        if approximate and parts is None:
            parts = _parts_of(layer, use_selection)
        source = (f"the selection on {layer.name()!r}" if use_selection
                  else f"layer {layer.name()!r}")
    if note:
        source = source + " (" + note.rstrip(".") + ")"
    return geom, crs, approximate, source, parts


def _filtered_outline(layer: QgsVectorLayer, expression: str):




    request = QgsFeatureRequest()
    parsed = QgsExpression(expression)
    if not parsed.hasParserError():
        referenced = list(parsed.referencedColumns())
        if "*" not in referenced:
            request.setSubsetOfAttributes(referenced, layer.fields())
    try:
        request.setFilterExpression(expression)
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"That expression could not be read: {exc}", "INVALID_ARGS",
                          hint="zone_expression_unreadable")
    polygons = []
    parts = []
    box = QgsRectangle()
    kept = 0
    try:
        for feature in layer.getFeatures(request):
            geom = feature.geometry()
            if geom is None or geom.isEmpty():
                continue
            kept += 1
            if kept > zoi.MAX_UNION_FEATURES:
                return tool_error(
                    f"That expression keeps more than {zoi.MAX_UNION_FEATURES} features.",
                    "INVALID_ARGS",
                    hint="zone_union_too_many")
            box.combineExtentWith(geom.boundingBox())
            parts.append(QgsGeometry(geom))
            if geom.type() == zoi.polygon_geometry_type():
                polygons.append(QgsGeometry(geom))
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"That expression could not be run on '{layer.name()}': {exc}",
                          "INVALID_ARGS",
                          hint="zone_expression_failed")
    if not kept:
        return tool_error(
            f"No feature of '{layer.name()}' matches that expression.", "INVALID_ARGS",
            hint="zone_expression_no_match")
    if polygons:
        united = zoi.union(polygons)
        if united is not None and not united.isEmpty():
            return united, layer.crs(), False, polygons
    if box.isNull():
        return tool_error(f"The matching features of '{layer.name()}' have no shape.",
                          "INVALID_ARGS", hint="zone_features_no_shape")
    return QgsGeometry.fromRect(box), layer.crs(), True, parts


def _box_around(parts: list[QgsGeometry], crs: QgsCoordinateReferenceSystem):

    if not parts:
        return None
    box = QgsRectangle()
    for part in parts:
        box.combineExtentWith(part.boundingBox())
    if box.isNull():
        return None
    return QgsGeometry.fromRect(box), crs, True


def _parts_of(layer: QgsVectorLayer, use_selection: bool) -> list[QgsGeometry]:





    if not isinstance(layer, QgsVectorLayer):
        return []
    try:

        features = (layer.selectedFeatures() if use_selection
                    else layer.getFeatures(QgsFeatureRequest().setSubsetOfAttributes([])))
        out = []
        for feature in features:
            geom = feature.geometry()
            if geom is None or geom.isEmpty():
                continue
            out.append(QgsGeometry(geom))
            if len(out) > zoi.MAX_UNION_FEATURES:
                return []
        return out
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Zone parts could not be read from {layer.name()!r}: {exc}")
        return []


def _metric_crs(geom: QgsGeometry, crs: QgsCoordinateReferenceSystem
                ) -> QgsCoordinateReferenceSystem | None:





    try:
        metres = enum_member(QgsUnitTypes, "DistanceUnit", "DistanceMeters")
        if not crs.isGeographic() and crs.mapUnits() == metres:
            return None
    except Exception:  # noqa: BLE001  # nosec B110
        pass
    wgs = QgsCoordinateReferenceSystem("EPSG:4326")
    here = geom if crs == wgs else to_wgs(geom, crs)
    if here is None:
        return None
    point = here.centroid()
    if point is None or point.isEmpty():
        point = QgsGeometry.fromPointXY(here.boundingBox().center())
    centre = point.asPoint()
    built = QgsCoordinateReferenceSystem()
    built.createFromProj(BUFFER_PROJ.format(lat=centre.y(), lon=centre.x()))
    return built if built.isValid() else None


def to_wgs(geom: QgsGeometry, crs: QgsCoordinateReferenceSystem) -> QgsGeometry | None:
    return zoi.to_crs(geom, crs, QgsCoordinateReferenceSystem("EPSG:4326"))


def _buffered_parts(parts: list[QgsGeometry], crs: QgsCoordinateReferenceSystem,
                    km: float) -> QgsGeometry | None:

    whole = zoi.union(parts)
    if whole is None:
        return None
    metric = _metric_crs(whole, crs)
    rings = []
    for part in parts:
        moved = QgsGeometry(part) if metric is None else zoi.to_crs(part, crs, metric)
        if moved is None:
            return None
        try:
            grown = moved.buffer(km * 1000.0, 12)
        except Exception:  # noqa: BLE001
            return None
        if grown is not None and not grown.isEmpty():
            rings.append(grown)
    if not rings:
        return None
    united = zoi.union(rings)
    if united is None or united.isEmpty():
        return None
    return united if metric is None else zoi.to_crs(united, metric, crs)


def _buffered(geom: QgsGeometry, crs: QgsCoordinateReferenceSystem,
              km: float) -> QgsGeometry | None:

    metric = _metric_crs(geom, crs)
    moved = QgsGeometry(geom) if metric is None else zoi.to_crs(geom, crs, metric)
    if moved is None:
        return None
    try:
        grown = moved.buffer(km * 1000.0, 12)
    except Exception:  # noqa: BLE001
        return None
    if grown is None or grown.isEmpty():
        return None
    return grown if metric is None else zoi.to_crs(grown, metric, crs)





def _zone_result(held: zoi.Zone, source: str) -> dict:
    box = held.geometry.boundingBox()
    area = held.area_km2()
    out = {
        "zone_set": True,
        "source": source,
        "area_km2": round(area, 3),
        "crs": held.crs.authid() or held.crs.description(),
        "bbox": [box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()],
        "layer_id": held.layer_id,
        "layer_name": zoi.zone_layer_name(),
    }
    if held.label:
        out["label"] = held.label
    layer = zoi.zone_layer()
    if layer is not None:
        out["layer_name"] = layer.name()
    if held.approximate:
        out["approximate"] = True
        out["note_hint"] = "zone_is_box"
    out["area_text"] = _area_text(area)
    out["tell_user_hint"] = "zone_set"
    out["next_step_hint"] = "zone_set_next"
    return out


def _crs_of(args: dict) -> QgsCoordinateReferenceSystem:


    if str(args.get("crs") or "").strip():
        return QgsCoordinateReferenceSystem(str(args["crs"]).strip())
    _extent, crs = _canvas_extent()
    return crs


def _canvas_extent():

    try:
        from qgis.utils import iface
        canvas = iface.mapCanvas() if iface is not None else None
        if canvas is not None:
            return QgsRectangle(canvas.extent()), canvas.mapSettings().destinationCrs()
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Zone: the map view could not be read: {exc}")
    return None, QgsProject.instance().crs()


def _zoom_to(layer) -> None:

    try:
        from qgis.utils import iface
        canvas = iface.mapCanvas() if iface is not None else None
        if canvas is None:
            return
        extent = zoi.to_crs(QgsGeometry.fromRect(layer.extent()), layer.crs(),
                            canvas.mapSettings().destinationCrs())
        if extent is None:
            return
        box = extent.boundingBox()
        box.scale(1.1)
        canvas.setExtent(box)
        canvas.refresh()
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Zone: the map could not be moved to it: {exc}")
