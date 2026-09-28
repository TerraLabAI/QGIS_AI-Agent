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
from .layer_lookup import _find_layer_note, _layer_not_found_error

ZONE_ACTIONS = ("set", "get", "clear")





MAX_BUFFER_KM = 100.0







BUFFER_PROJ = ("+proj=aeqd +lat_0={lat:.6f} +lon_0={lon:.6f} +x_0=0 +y_0=0 "
               "+datum=WGS84 +units=m +no_defs")


def register_zone_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="zone",
        danger="write",
        visible=14,
        label=QT_TRANSLATE_NOOP("AIAgent", "Zone of interest[: {label}][ from {layer_name}]"),
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
                      f"action must be one of {list(ZONE_ACTIONS)}.")





def _zone_get() -> dict:
    held = zoi.read_zone()
    if held is None:
        return {
            "zone_set": False,
            "next_step": ("No zone of interest yet. zone action set makes one from a layer the "
                          "project already has; ai_segment / ai_edit's own panel lets the person "
                          "draw one."),
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

    buffer_km = args.get("buffer_km")
    ringed = False
    if isinstance(buffer_km, (int, float)) and not isinstance(buffer_km, bool) and buffer_km > 0:
        max_km = tuning.ceiling("zone_max_buffer_km", MAX_BUFFER_KM, 5.0)
        if buffer_km > max_km:
            return tool_error(
                f"A {buffer_km:g} km ring is wider than the zone it surrounds.",
                "INVALID_ARGS",
                f"buffer_km up to {max_km:g} works; so does the wider area as the zone.")




        widened = _buffered_parts(parts, crs, float(buffer_km)) if (approximate and parts) else None
        if widened is not None:
            geom, approximate = widened, False
        else:
            widened = _buffered(geom, crs, float(buffer_km))
            if widened is None:
                return tool_error("The zone could not be widened.", "EXECUTION_FAILED",
                                  "Without buffer_km, a processing buffer widens the zone after.")
            geom = widened
        ringed = True

    if zoi.area_km2(geom, crs) <= 0:
        return tool_error(
            "That zone has no surface: it is a point, or several points on a line.",
            "INVALID_ARGS",
            "buffer_km draws a ring around it, for example 0.5 for 500 m; an area with "
            "its own width also works.")



    label = str(args.get("label") or "").strip() or None
    layer = zoi.write_zone(geom, crs, label=label, approximate=approximate)
    if layer is None:
        return tool_error("The zone of interest layer could not be created.", "EXECUTION_FAILED",
                          "zone action set tries again once the project accepts new layers.")
    keep_place(layer)
    log(f"Zone of interest set from {source} ({zoi.area_km2(geom, crs):.2f} km2)")

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
                              "wkt: a POLYGON or MULTIPOLYGON in well known text.")
        return geom, _crs_of(args), False, "wkt", None
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            xmin, ymin, xmax, ymax = (float(v) for v in bbox)
        except (TypeError, ValueError):
            return tool_error("bbox must be four numbers.", "INVALID_ARGS",
                              "bbox=[xmin, ymin, xmax, ymax].")
        if xmax <= xmin or ymax <= ymin:
            return tool_error("That bbox has no surface.", "INVALID_ARGS",
                              "bbox is [xmin, ymin, xmax, ymax], maximums above minimums.")
        return (QgsGeometry.fromRect(QgsRectangle(xmin, ymin, xmax, ymax)),
                _crs_of(args), False, "bbox", None)
    if canvas:
        extent, crs = _canvas_extent()
        if extent is None:
            return tool_error("The map view could not be read.", "EXECUTION_FAILED",
                              "A layer name or a bbox works instead.")
        return QgsGeometry.fromRect(extent), crs, False, "canvas", None
    return tool_error(
        "zone action set needs an area to work from.", "INVALID_ARGS",
        "layer_name (with expression or use_selection to narrow it), wkt, bbox, or "
        "use_canvas_extent true gives one.")


def _from_layer(named: str, expression: str, use_selection: bool):
    layer, note = _find_layer_note(named)
    if layer is None:
        return _layer_not_found_error(named)
    if expression:
        if not isinstance(layer, QgsVectorLayer):
            return tool_error(f"'{layer.name()}' has no features to filter.", "INVALID_ARGS",
                              "expression needs a vector layer.")
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
                    "expression picks features by a value.")
            return tool_error(f"'{layer.name()}' holds no shape to use as a zone.", "INVALID_ARGS",
                              "A layer with features, or a bbox, works.")
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
                          'QGIS expressions read like "name" = \'Herault\'.')
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
                    "The one area meant, or the layer itself as the zone, both narrow it.")
            box.combineExtentWith(geom.boundingBox())
            parts.append(QgsGeometry(geom))
            if geom.type() == zoi.polygon_geometry_type():
                polygons.append(QgsGeometry(geom))
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"That expression could not be run on '{layer.name()}': {exc}",
                          "INVALID_ARGS",
                          "get_layer_info shows the field names for zone to run again.")
    if not kept:
        return tool_error(
            f"No feature of '{layer.name()}' matches that expression.", "INVALID_ARGS",
            "get_features or get_field_statistics reads the values to filter on.")
    if polygons:
        united = zoi.union(polygons)
        if united is not None and not united.isEmpty():
            return united, layer.crs(), False, polygons
    if box.isNull():
        return tool_error(f"The matching features of '{layer.name()}' have no shape.",
                          "INVALID_ARGS", "The layer needs features with geometry.")
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
        "layer_name": zoi.ZONE_LAYER_NAME,
    }
    if held.label:
        out["label"] = held.label
    layer = zoi.zone_layer()
    if layer is not None:
        out["layer_name"] = layer.name()
    if held.approximate:
        out["approximate"] = True
        out["note"] = ("This zone is a box around what was asked for, not its outline: the source "
                       "had no polygons, or more than the outline budget.")
    out["tell_user"] = (
        f"The zone of interest is set: {area:.1f} km2, drawn on the map as \"{out['layer_name']}\". "
        "AI Edit and AI Segmentation offer it instead of asking for a fresh draw.")
    out["next_step"] = ("use_zone true makes ai_segment or ai_edit run on this shape. The area "
                        "belongs in the answer before anything spends credits.")
    return out


def _crs_of(args: dict) -> QgsCoordinateReferenceSystem:

    named = str(args.get("crs") or "").strip()
    if named:
        crs = QgsCoordinateReferenceSystem(named)
        if crs.isValid():
            return crs
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
