# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The main-thread half: reading the outlet, and building the layers."""





from __future__ import annotations

import math

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsWkbTypes,
)

from ..core import limits
from ..core.qt_compat import enum_member, field_type
from ..core.tool_registry import tool_error
from .hydrology_terrain import _OUTLET_FORMS
from .layer_lookup import _find_layer, _layer_not_found_error



def _dem_facts(dem_name: str) -> dict:
    layer = _find_layer(dem_name)
    if layer is None:
        return _layer_not_found_error(dem_name)
    if not isinstance(layer, QgsRasterLayer):
        return tool_error(f"{layer.name()} is not a raster layer.", "INVALID_ARGS",
                          "dem is the elevation raster: find_datasets 'elevation' lists the DEMs, add_data loads one.")
    crs = layer.crs()
    renderer = layer.renderer()
    return {
        "name": layer.name(),
        "layer_id": layer.id(),
        "renderer": renderer.type() if renderer is not None else "",
        "source": str(layer.source() or ""),
        "crs_wkt": crs.toWkt(),
        "crs_authid": crs.authid(),
        "geographic": bool(crs.isGeographic()),
        "width": int(layer.width()),
        "height": int(layer.height()),
    }


def _outlet_error(message: str) -> dict:
    return tool_error(message, "INVALID_ARGS", _OUTLET_FORMS)


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_crs(text):
    """The CRS a string names, None when it names none, False when it is not one."""
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        raw = f"EPSG:{raw}"
    crs = QgsCoordinateReferenceSystem(raw)
    return crs if crs.isValid() else False


def _single_point(geometry) -> QgsPointXY | None:
    """The one point a geometry holds, or None for anything else."""
    if geometry is None or geometry.isNull() or geometry.isEmpty():
        return None
    kind = QgsWkbTypes.displayString(geometry.wkbType()) or ""
    if kind.startswith("MultiPoint"):
        points = geometry.asMultiPoint()
        return QgsPointXY(points[0]) if len(points) == 1 else None
    if kind.startswith("Point"):
        return QgsPointXY(geometry.asPoint())
    return None


def _guessed_crs(point: QgsPointXY):
    """The CRS of coordinates that came without one: degrees when they fit, else the project's."""
    if abs(point.x()) <= 180.0 and abs(point.y()) <= 90.0:
        return QgsCoordinateReferenceSystem("EPSG:4326")
    project_crs = QgsProject.instance().crs()
    return project_crs if project_crs.isValid() else None


def _outlet_from_layer(name: str, feature_id) -> dict:
    layer = _find_layer(name)
    if layer is None:
        return _layer_not_found_error(name)
    if not isinstance(layer, QgsVectorLayer):
        return _outlet_error(f"{layer.name()} is not a point layer.")
    how = ""
    if feature_id is not None:
        try:
            fid = int(feature_id)
        except (TypeError, ValueError):
            return _outlet_error(f"feature_id {feature_id!r} is not a feature id.")
        feature = layer.getFeature(fid)
        if not feature.isValid():
            return tool_error(f"{layer.name()} has no feature {fid}.", "INVALID_ARGS",
                              f"Call get_features on {layer.name()} to read its ids, then pass one as feature_id.")
        how = f"feature {fid}"
    else:
        selected = list(layer.selectedFeatureIds())
        if len(selected) == 1:
            feature = layer.getFeature(selected[0])
            how = f"the selected feature {selected[0]}"
        elif len(selected) > 1:
            return tool_error(f"{len(selected)} features of {layer.name()} are selected, and an outlet is one point.",
                              "INVALID_ARGS",
                              f'Pass outlet {{"layer_name": "{layer.name()}", "feature_id": <id>}}, '
                              "or select one point.")
        else:
            found = list(layer.getFeatures(QgsFeatureRequest().setLimit(2)))
            if not found:
                return _outlet_error(f"{layer.name()} holds no feature to use as the outlet.")
            if len(found) > 1:
                return tool_error(f"{layer.name()} holds several points; the outlet is one of them.", "INVALID_ARGS",
                                  f'Pass outlet {{"layer_name": "{layer.name()}", "feature_id": <id>}}: '
                                  "get_features lists the ids. Or select one point.")
            feature = found[0]
            how = f"its only feature {feature.id()}"
    point = _single_point(feature.geometry())
    if point is None:
        return _outlet_error(f"{layer.name()} {how} is not a single point.")
    return {"point": point, "crs": layer.crs(), "from": f"layer {layer.name()}, {how}", "assumed": False}


def _resolve_outlet(outlet, dem_crs_wkt: str) -> dict:
    """The outlet in the DEM's CRS and in degrees, from any of the forms the schema takes."""
    if not isinstance(outlet, dict) or not outlet:
        return _outlet_error("outlet is empty.")
    given = _parse_crs(outlet.get("crs"))
    if given is False:
        return _outlet_error(f"outlet crs {str(outlet.get('crs'))[:40]!r} is not a CRS QGIS knows.")
    if outlet.get("layer_name") not in (None, ""):
        picked = _outlet_from_layer(str(outlet["layer_name"]), outlet.get("feature_id"))
        if "_error" in picked:
            return picked
    elif outlet.get("feature_id") is not None:
        return _outlet_error("feature_id needs layer_name, the point layer it belongs to.")
    elif outlet.get("wkt"):
        point = _single_point(QgsGeometry.fromWkt(str(outlet["wkt"])))
        if point is None:
            return _outlet_error(f"wkt is not a single point: {str(outlet['wkt'])[:60]}")
        picked = {"point": point, "crs": given or _guessed_crs(point), "from": "wkt", "assumed": given is None}
    elif outlet.get("lon") is not None or outlet.get("lat") is not None:
        lon, lat = _finite(outlet.get("lon")), _finite(outlet.get("lat"))
        if lon is None or lat is None or not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
            return _outlet_error("outlet lon and lat must both be degrees, lon within 180 and lat within 90.")
        picked = {"point": QgsPointXY(lon, lat), "crs": QgsCoordinateReferenceSystem("EPSG:4326"),
                  "from": "lon/lat", "assumed": False}
    elif outlet.get("x") is not None or outlet.get("y") is not None:
        x, y = _finite(outlet.get("x")), _finite(outlet.get("y"))
        if x is None or y is None:
            return _outlet_error("outlet x and y must both be numbers.")
        point = QgsPointXY(x, y)
        picked = {"point": point, "crs": given or _guessed_crs(point), "from": "x/y", "assumed": given is None}
    else:
        return _outlet_error(f"outlet names no point (keys: {', '.join(sorted(map(str, outlet)))[:60]}).")
    crs = picked["crs"]
    if crs is None or not crs.isValid():
        return _outlet_error("The outlet coordinates came without a crs and the project has none.")
    dem_crs = QgsCoordinateReferenceSystem.fromWkt(dem_crs_wkt)
    context = QgsProject.instance().transformContext()
    try:
        in_dem = QgsCoordinateTransform(crs, dem_crs, context).transform(picked["point"])
        in_degrees = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem("EPSG:4326"), context).transform(
            picked["point"])
    except Exception as exc:  # noqa: BLE001 - QgsCsException: the point is off the CRS's world
        return _outlet_error(f"The outlet cannot be placed in the DEM's CRS: {exc}")
    out = {"x": in_dem.x(), "y": in_dem.y(), "lon": in_degrees.x(), "lat": in_degrees.y(),
           "from": picked["from"], "crs": crs.authid() or crs.description()}
    if picked["assumed"]:
        out["crs_assumed"] = True
    return out


def _mask_geometry(name: str, dem_crs_wkt: str) -> dict:
    """The polygons of a layer (its selection when there is one) in the DEM's CRS, as WKB."""
    layer = _find_layer(name)
    if layer is None:
        return _layer_not_found_error(name)
    if not isinstance(layer, QgsVectorLayer):
        return tool_error(f"{layer.name()} is not a polygon layer.", "INVALID_ARGS",
                          "mask_layer takes a polygon layer: the watershed delineate_watershed added, or an area.")
    selected = list(layer.selectedFeatureIds())
    request = QgsFeatureRequest().setFilterFids(selected) if selected else QgsFeatureRequest()
    cap = int(limits.current("SYNC_FEATURE_LOOP_MAX"))
    request.setLimit(cap + 1)
    dem_crs = QgsCoordinateReferenceSystem.fromWkt(dem_crs_wkt)
    transform = QgsCoordinateTransform(layer.crs(), dem_crs, QgsProject.instance().transformContext())
    wkbs, xmin, ymin, xmax, ymax = [], math.inf, math.inf, -math.inf, -math.inf
    for count, feature in enumerate(layer.getFeatures(request)):
        if count >= cap:
            return tool_error(f"{layer.name()} holds more than {cap:,} polygons.", "INVALID_ARGS",
                              f"Select the polygon of the area in {layer.name()} first, or dissolve the layer, "
                              "then pass it as mask_layer.")
        geometry = QgsGeometry(feature.geometry())
        kind = QgsWkbTypes.displayString(geometry.wkbType()) or ""
        if geometry.isNull() or geometry.isEmpty() or not ("Polygon" in kind or "Surface" in kind):
            continue
        try:
            geometry.transform(transform)
        except Exception as exc:  # noqa: BLE001 - QgsCsException
            return tool_error(f"{layer.name()} cannot be placed in the DEM's CRS: {exc}", "INVALID_ARGS",
                              "Check the CRS of the mask layer with get_layer_info.")
        box = geometry.boundingBox()
        xmin, ymin = min(xmin, box.xMinimum()), min(ymin, box.yMinimum())
        xmax, ymax = max(xmax, box.xMaximum()), max(ymax, box.yMaximum())
        wkbs.append(bytes(geometry.asWkb()))
    if not wkbs:
        return tool_error(f"{layer.name()} holds no polygon to use as the area.", "INVALID_ARGS",
                          "mask_layer takes a polygon layer: the watershed delineate_watershed added, or an area.")
    return {"wkbs": wkbs, "extent": (xmin, ymin, xmax, ymax), "name": layer.name(),
            "features": len(wkbs), "selected": bool(selected)}


def _style_streams(layer, orders) -> None:
    """Graduated by Strahler order: pale and thin headwaters, dark and wide rivers."""
    from qgis.core import QgsGraduatedSymbolRenderer, QgsLineSymbol, QgsRendererRange
    from qgis.PyQt.QtGui import QColor

    low, high = min(orders), max(orders)
    span = float(max(1, high - low))
    ranges = []
    for order in range(low, high + 1):
        t = (order - low) / span
        color = QColor(int(round(158 + (8 - 158) * t)), int(round(202 + (48 - 202) * t)),
                       int(round(225 + (107 - 225) * t)))
        symbol = QgsLineSymbol.createSimple({"line_color": color.name(),
                                             "line_width": f"{0.25 + 0.3 * (order - 1):.2f}",
                                             "capstyle": "round", "joinstyle": "round"})
        ranges.append(QgsRendererRange(order - 0.5, order + 0.5, symbol, f"Order {order}"))
    layer.setRenderer(QgsGraduatedSymbolRenderer("strahler", ranges))


def _valid_line(wkb: bytes) -> QgsGeometry | None:
    """A line QgsGeometry from a raw raster-to-vector WKB, fixed or dropped: never an invalid or empty one reaches a layer."""








    geometry = QgsGeometry()
    geometry.fromWkb(wkb)
    if geometry.isEmpty():
        return None
    if not geometry.isGeosValid():
        geometry = geometry.makeValid()
        if geometry is None or geometry.isEmpty():
            return None
        if QgsWkbTypes.geometryType(geometry.wkbType()) != enum_member(QgsWkbTypes, "GeometryType", "LineGeometry"):
            return None
    if geometry.length() <= 0.0:
        return None
    return geometry


def _add_stream_layers(crs_wkt: str, name: str, rows: list, path: dict | None) -> dict:
    """The stream segments as one styled line layer, and the longest flow path as a second."""





    from qgis.PyQt.QtGui import QColor

    crs = QgsCoordinateReferenceSystem.fromWkt(crs_wkt)
    project = QgsProject.instance()
    layer = QgsVectorLayer(f"LineString?crs={crs.toWkt()}", name, "memory")
    provider = layer.dataProvider()
    provider.addAttributes([QgsField("strahler", field_type("Int")), QgsField("length_m", field_type("Double")),
                            QgsField("upstream_km2", field_type("Double"))])
    layer.updateFields()
    features = []
    kept = []
    dropped = 0
    for wkb, strahler, length_m, upstream_km2 in rows:
        geometry = _valid_line(wkb)
        if geometry is None:
            dropped += 1
            continue
        feature = QgsFeature(layer.fields())
        feature.setGeometry(geometry)
        feature.setAttributes([strahler, length_m, upstream_km2])
        features.append(feature)
        kept.append((strahler, length_m))
    provider.addFeatures(features)
    layer.updateExtents()
    if kept:
        _style_streams(layer, [strahler for strahler, _length_m in kept])
    project.addMapLayer(layer)
    counts: dict[int, int] = {}
    for strahler, _length_m in kept:
        counts[strahler] = counts.get(strahler, 0) + 1
    out = {
        "layer_id": layer.id(), "layer_name": layer.name(),
        "segments": len(kept),
        "segments_by_order": {str(k): counts[k] for k in sorted(counts)},
        "max_order": max(counts) if counts else 0,
        "length_km": round(sum(length_m for _strahler, length_m in kept) / 1000.0, 2),
    }
    if dropped:
        out["dropped"] = dropped
    if path:
        geometry = _valid_line(path["wkb"])
        if geometry is not None:
            line = QgsVectorLayer(f"LineString?crs={crs.toWkt()}", path["name"], "memory")
            line.dataProvider().addAttributes([QgsField("length_km", field_type("Double")),
                                               QgsField("drop_m", field_type("Double"))])
            line.updateFields()
            feature = QgsFeature(line.fields())
            feature.setGeometry(geometry)
            feature.setAttributes([path["length_km"], path["drop_m"]])
            line.dataProvider().addFeatures([feature])
            line.updateExtents()
            symbol = line.renderer().symbol()
            symbol.setColor(QColor("#d7301f"))
            symbol.setWidth(0.9)
            project.addMapLayer(line)
            out["path_layer_id"], out["path_layer_name"] = line.id(), line.name()
        else:
            out["path_dropped"] = True
    return out


def _ellipsoid_area_km2(wkb: bytes, crs_wkt: str) -> float:
    geometry = QgsGeometry()
    geometry.fromWkb(wkb)
    measure = QgsDistanceArea()
    measure.setSourceCrs(QgsCoordinateReferenceSystem.fromWkt(crs_wkt), QgsProject.instance().transformContext())
    measure.setEllipsoid("EPSG:7030")
    return float(measure.measureArea(geometry)) / 1e6


def _add_watershed_layer(wkb: bytes, crs_wkt: str, name: str, attributes: dict) -> dict:
    crs = QgsCoordinateReferenceSystem.fromWkt(crs_wkt)
    layer = QgsVectorLayer(f"Polygon?crs={crs.toWkt()}", name, "memory")
    provider = layer.dataProvider()
    fields = [
        QgsField("area_km2", field_type("Double")),
        QgsField("mean_slope_deg", field_type("Double")),
        QgsField("outlet_lon", field_type("Double")),
        QgsField("outlet_lat", field_type("Double")),
        QgsField("cells", field_type("Int")),
    ]
    provider.addAttributes(fields)
    layer.updateFields()
    feature = QgsFeature(layer.fields())
    geometry = QgsGeometry()
    geometry.fromWkb(wkb)
    feature.setGeometry(geometry)
    for key, value in attributes.items():
        feature.setAttribute(key, value)
    provider.addFeatures([feature])
    layer.updateExtents()
    QgsProject.instance().addMapLayer(layer)
    return {"layer_id": layer.id(), "layer_name": layer.name()}


def _style_dem(layer_id: str, stretch: dict) -> dict:
    """The elevation ramp on a DEM still in QGIS's default grey. Main thread."""
    from .elevation_style import apply_elevation_style

    layer = QgsProject.instance().mapLayer(layer_id)
    if layer is None:
        return {}
    styled = apply_elevation_style(layer, stretch)
    if styled:
        layer.triggerRepaint()
    renderer = layer.renderer()
    return {"renderer": renderer.type() if renderer is not None else "", **({"styled": styled} if styled else {})}


def _add_drainage_layers(crs_wkt: str, streams_name: str, rows: list, watershed_wkb: bytes, watershed_name: str,
                         attributes: dict) -> dict:
    """The watershed as an outline over the DEM, and the streams, in one main-thread call."""



    from qgis.core import QgsFillSymbol

    watershed = _add_watershed_layer(watershed_wkb, crs_wkt, watershed_name, attributes)
    layer = QgsProject.instance().mapLayer(watershed["layer_id"])
    if layer is not None and layer.renderer() is not None:


        layer.renderer().setSymbol(QgsFillSymbol.createSimple(
            {"style": "no", "outline_color": "#08306b", "outline_width": "0.8"}))
        layer.triggerRepaint()
    streams = _add_stream_layers(crs_wkt, streams_name, rows, None) if rows else None
    return {"watershed": watershed, "streams": streams}
