# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

import math
import os
import threading

from qgis.core import (
    QgsCoordinateTransform,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsVectorLayerFeatureSource,
    QgsWkbTypes,
)

from . import background, ground, tuning
from .crs_ref import crs_ref
from .feature_requests import feature_request, first_feature
from .layer_order import is_web_service
from .serialization import qt_temporal_text


def _cap(name: str, default: int) -> int:







    return tuning.limit("context", name, default)










MAX_FIELDS = 16
MAX_SAMPLE_VALUE_CHARS = 80






MAX_CARD_FIELDS = 24


def crs_units(crs) -> str:
    try:
        if not crs.isValid():
            return "unknown"
        from qgis.core import QgsUnitTypes

        label = QgsUnitTypes.toString(crs.mapUnits())
        if label:
            return label
    except Exception:  # nosec B110
        pass
    return "degrees" if crs.isGeographic() else "unknown"


def project_measure(project) -> dict:










    out: dict = {}
    try:
        from qgis.core import QgsUnitTypes

        ellipsoid = str(project.ellipsoid() or "").strip()
        if ellipsoid:
            out["ellipsoid"] = ellipsoid


        distance = QgsUnitTypes.encodeUnit(project.distanceUnits())
        if distance:
            out["distance_units"] = str(distance)
        area = QgsUnitTypes.encodeUnit(project.areaUnits())
        if area:
            out["area_units"] = str(area)
    except Exception:  # nosec B110
        return {}
    return out


def layer_kind(layer) -> str:
    if isinstance(layer, QgsVectorLayer):
        return "vector"
    if isinstance(layer, QgsRasterLayer):
        return "raster"
    name = type(layer).__name__.replace("Qgs", "").replace("Layer", "").lower()
    return name or "other"


def geometry_name(layer) -> str | None:
    if not isinstance(layer, QgsVectorLayer):
        return None
    try:
        return QgsWkbTypes.geometryDisplayString(layer.geometryType())
    except Exception:  # nosec B110
        geom = layer.geometryType()
        return getattr(geom, "name", str(geom))


def source_kind(layer) -> str:
    provider = (layer.providerType() or "").lower()
    source = (layer.source() or "").lower()
    if provider == "memory":
        return "memory"
    if is_web_service(layer):
        return "wfs"
    if provider == "wms":
        return "xyz" if "type=xyz" in source else "wms"
    if provider == "postgres":
        return "postgres"
    if provider in ("ogr", "gdal", "delimitedtext", "spatialite", "gpx", "mdal", "pdal", "ept", "copc", "vectortile"):
        if source.startswith(("http://", "https://", "/vsicurl", "/vsis3", "/vsigs", "/vsiaz", "pmtiles")):
            return "other"
        return "file"
    return "other"






_FAST_COUNT_DRIVERS = frozenset({"GPKG", "ESRI Shapefile", "FlatGeobuf", "GeoJSON", "SQLite", "Parquet",
                                 "Arrow", "MapInfo File", "OpenFileGDB"})
_SCAN_COUNT_BYTES = 50_000_000


def count_is_cheap(layer) -> bool:

    if not isinstance(layer, QgsVectorLayer):
        return False
    kind = source_kind(layer)
    if kind == "memory":
        return True
    if kind != "file":
        return False
    provider = (layer.providerType() or "").lower()
    if provider in ("delimitedtext", "spatialite"):
        return True
    if provider != "ogr":
        return False
    try:

        if not layer.subsetString() and layer.dataProvider().storageType() in _FAST_COUNT_DRIVERS:
            return True
        return os.path.getsize((layer.source() or "").split("|", 1)[0]) < _SCAN_COUNT_BYTES
    except Exception:  # noqa: BLE001
        return False


def feature_count(layer, kind: str) -> int | None:









    if not isinstance(layer, QgsVectorLayer) or kind not in ("memory", "file") or not count_is_cheap(layer):
        return None
    try:
        n = layer.featureCount()
    except Exception:  # nosec B110
        return None
    return int(n) if n is not None and n >= 0 else None


def duplicate_names(layers) -> set[str]:

    seen: dict[str, int] = {}
    for layer in layers:
        try:
            name = layer.name()
        except Exception:  # nosec B110
            name = ""
        if name:
            seen[name] = seen.get(name, 0) + 1
    return {name for name, count in seen.items() if count > 1}


def layer_record(layer, node=None, ambiguous: set[str] | None = None) -> dict:












    kind = source_kind(layer)
    crs = layer.crs()
    name = layer.name()
    record = {
        "name": name,
        "type": layer_kind(layer),
        "geometry": geometry_name(layer),
        "crs": crs_ref(crs),
        "visible": bool(node.isVisible()) if node is not None else True,
        "feature_count": feature_count(layer, kind),
        "source_kind": kind,
    }



    if ambiguous is None or name in ambiguous or tuning.flag("context", "always_send_layer_id", False):
        record["id"] = layer.id()
    return record


_FILTER_CHARS = 200


def layer_filter(layer) -> str:

    try:
        if isinstance(layer, QgsVectorLayer):
            text = " ".join(str(layer.subsetString() or "").split())
            return text if len(text) <= _FILTER_CHARS else text[:_FILTER_CHARS - 3] + "..."
    except Exception:  # nosec B110
        pass
    return ""


def selected_count(layer) -> int:
    try:
        if isinstance(layer, QgsVectorLayer):
            return int(layer.selectedFeatureCount())
    except Exception:  # nosec B110
        pass
    return 0


def box_to_4326(rect, crs, project=None):






    try:
        from qgis.core import QgsCoordinateReferenceSystem

        if not crs.isValid():
            return None
        box = rect
        if crs.authid() != "EPSG:4326":
            transform = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem("EPSG:4326"),
                                               project or QgsProject.instance())
            box = transform.transformBoundingBox(rect)
    except Exception:  # noqa: BLE001
        return None
    values = (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum())
    if box.isNull() or not all(math.isfinite(v) for v in values):
        return None
    if box.yMinimum() < -90.5 or box.yMaximum() > 90.5:
        return None
    return box


def view_bbox_4326(extent, crs, project=None) -> list | None:

    box = box_to_4326(extent, crs, project)
    if box is None:
        return None
    west, east = box.xMinimum(), box.xMaximum()
    south, north = box.yMinimum(), box.yMaximum()
    if abs(east - west) < 0.0001 or abs(north - south) < 0.0001:
        return None
    return [round(west, 6), round(south, 6), round(east, 6), round(north, 6)]


def canvas_bbox_4326() -> list | None:




    try:
        from qgis.utils import iface

        canvas = iface.mapCanvas()
        return view_bbox_4326(canvas.extent(), canvas.mapSettings().destinationCrs())
    except Exception:  # noqa: BLE001
        return None


def view_area_km2(extent, crs, project) -> float | None:

    try:
        from qgis.core import QgsDistanceArea, QgsGeometry
        measure = QgsDistanceArea()
        measure.setSourceCrs(crs, project.transformContext())
        measure.setEllipsoid("WGS84")
        km2 = abs(measure.measureArea(QgsGeometry.fromRect(extent))) / 1e6
        return round(km2, 3) if km2 < 10 else round(km2, 1)
    except Exception:  # noqa: BLE001
        return None


def extent_is_cheap(layer) -> bool:








    if not isinstance(layer, QgsVectorLayer):
        return True
    return source_kind(layer) in ("memory", "file")


def layer_extent_rect(layer, canvas_crs, project) -> dict | None:








    if layer is None or canvas_crs is None or not canvas_crs.isValid():
        return None
    if not extent_is_cheap(layer):
        return None
    try:
        extent = layer.extent()
        if extent is None or extent.isNull() or extent.isEmpty():
            return None
        layer_crs = layer.crs()
        if layer_crs.isValid() and layer_crs != canvas_crs:
            transform = QgsCoordinateTransform(layer_crs, canvas_crs, project)
            extent = transform.transformBoundingBox(extent)
        return {"xmin": extent.xMinimum(), "ymin": extent.yMinimum(),
                "xmax": extent.xMaximum(), "ymax": extent.yMaximum(),
                "crs": canvas_crs.authid() or ""}
    except Exception:
        return None


def field_card(layer) -> tuple[list[dict], int]:
    try:
        fields = list(layer.fields())
    except Exception:
        return [], 0
    cap = _cap("max_card_fields", MAX_CARD_FIELDS)
    out = [{"name": f.name(), "type": f.typeName()} for f in fields[:cap]]
    return out, max(0, len(fields) - cap)








_SAMPLE_WAIT_S = 0.2
_samples: dict = {}
_samples_reading: set = set()
_samples_lock = threading.Lock()


def _sample_row(feature, chosen) -> dict | None:
    out: dict = {}
    for index, field in chosen:
        value = feature[index]
        if isinstance(value, (bytes, bytearray)):
            continue
        if type(value).__name__ in ("QDate", "QDateTime", "QTime"):
            value = qt_temporal_text(value)
        out[field.name()] = str(value)[:MAX_SAMPLE_VALUE_CHARS]
    return out or None


def sample_attributes(layer) -> dict | None:







    kind = source_kind(layer)
    if kind not in ("memory", "file"):
        return None
    try:
        fields = list(layer.fields())
        eligible = [(index, field) for index, field in enumerate(fields)
                    if field.typeName().lower() not in ("binary", "blob")]
        chosen = eligible[:MAX_FIELDS]
        if not chosen:
            return None
        request = feature_request(attributes=[index for index, _field in chosen], geometry=False, limit=1)
        if kind == "memory" or is_editing(layer):
            feature = first_feature(layer, request)
            row = _sample_row(feature, chosen) if feature is not None else None
        else:
            row = _sample_off_thread(layer, request, chosen)
        if row is None:
            return None
        out = dict(row)
        if len(eligible) > len(chosen):
            out["_more_fields"] = len(eligible) - len(chosen)
        return out
    except Exception:
        return None


def _sample_off_thread(layer, request, chosen) -> dict | None:

    lid = layer.id()
    key = (str(layer.subsetString() or ""), tuple(field.name() for _index, field in chosen))
    done = threading.Event()
    with _samples_lock:
        reading = lid in _samples_reading
        _samples_reading.add(lid)
    if not reading:
        def read(source, request):
            row = None
            try:
                feature = first_feature(source, request)
                row = _sample_row(feature, chosen) if feature is not None else None
            finally:
                with _samples_lock:
                    _samples[lid] = (key, row)
                    _samples_reading.discard(lid)
                done.set()

        try:
            background.start_kept_thread(read, QgsVectorLayerFeatureSource(layer), request,
                                         name="context sample read")
        except Exception:  # noqa: BLE001
            with _samples_lock:
                _samples_reading.discard(lid)
            return None
        done.wait(_SAMPLE_WAIT_S)
    with _samples_lock:
        kept = _samples.get(lid)
    return kept[1] if kept is not None and kept[0] == key else None


def extent_size(layer, project) -> dict | None:

















    if not extent_is_cheap(layer):
        return None
    try:
        extent = layer.extent()
        if extent is None or extent.isNull():
            return None
        layer_crs = layer.crs()
        width, height = extent.width(), extent.height()


        digits = 6 if layer_crs.isGeographic() else 2
        out = {"width": round(width, digits), "height": round(height, digits),
               "crs": crs_ref(layer_crs), "units": crs_units(layer_crs)}
        box = _box_digits(extent)
        if box:
            out["box"] = box
        degrees_box = view_bbox_4326(extent, layer_crs, project) if layer_crs.isValid() else None
        if degrees_box:
            out["box_4326"] = degrees_box
        centre = extent.center()
        scale = ground.metres_per_unit(layer_crs, centre.x(), centre.y())
    except Exception:  # noqa: BLE001
        return None
    if ground.distorted(scale):


        out["ground_width_m"] = round(width * scale, 1)
        out["ground_height_m"] = round(height * scale, 1)
    degrees = degree_values(layer_crs, extent)
    if degrees:
        out["degree_values"] = degrees
    return out


def _box_digits(extent) -> list | None:

    values = (extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum())
    if not all(math.isfinite(v) for v in values):
        return None
    span = max(extent.width(), extent.height())
    digits = 6 if span <= 0 else min(6, max(0, 3 - math.floor(math.log10(span))))
    return [round(v, digits) for v in values]




_WORLD_WIDTH_DEGREES = 90.0


def degree_values(crs, extent) -> str:






    try:
        if not crs.isValid() or crs.isGeographic() or extent.isNull() or extent.isEmpty():
            return ""
    except Exception:  # noqa: BLE001
        return ""
    name = crs.authid() or "its CRS"
    size = f"extent {extent.width():.4g} x {extent.height():.4g} {name} units"
    if not (extent.xMinimum() >= -180 and extent.xMaximum() <= 180
            and extent.yMinimum() >= -90 and extent.yMaximum() <= 90):
        return ""
    try:
        from qgis.core import QgsCoordinateReferenceSystem

        bounds = crs.bounds()
        if bounds.isEmpty() or bounds.width() >= _WORLD_WIDTH_DEGREES:
            return f"{size}, every coordinate within the range of longitude/latitude degrees"
        box = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem("EPSG:4326"),
                                     QgsProject.instance()).transformBoundingBox(extent)
        if bounds.contains(box):
            return ""
    except Exception:  # noqa: BLE001
        return f"{size}, outside {name}'s area of use; the values fit longitude/latitude degrees"
    return f"{size}, outside {name}'s area of use; the values fit longitude/latitude degrees"


def is_editing(layer) -> bool:
    try:
        return bool(layer.isEditable())
    except Exception:
        return False


MAX_BAND_NAMES = 8
MAX_BAND_NAME_CHARS = 40


def _band_names(provider, count: int) -> list[str]:

    describe = getattr(provider, "bandDescription", None)
    if not callable(describe):
        return []
    names = []
    for band in range(1, min(count, MAX_BAND_NAMES) + 1):
        try:
            names.append(str(describe(band) or "").strip()[:MAX_BAND_NAME_CHARS])
        except Exception:  # noqa: BLE001
            names.append("")
    return names if any(names) else []


def _rounded(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return float(f"{number:.6g}")


def _whole_band_range(renderer) -> bool:

    try:
        from qgis.core import Qgis, QgsRasterMinMaxOrigin


        limits = getattr(getattr(Qgis, "RasterRangeLimit", None), "MinimumMaximum", None)
        extent = getattr(getattr(Qgis, "RasterRangeExtent", None), "WholeRaster", None)
        if limits is None or extent is None:

            limits, extent = QgsRasterMinMaxOrigin.Limits.MinMax, QgsRasterMinMaxOrigin.Extent.WholeRaster
        origin = renderer.minMaxOrigin()
        return origin.limits() == limits and origin.extent() == extent
    except Exception:  # noqa: BLE001
        return False


def _band1_span(layer) -> tuple[str, list] | None:









    try:
        renderer = layer.renderer()
        kind = str(renderer.type()) if renderer is not None else ""
        if kind == "singlebandpseudocolor":
            span = [_rounded(renderer.classificationMin()), _rounded(renderer.classificationMax())]
        elif kind == "singlebandgray" and renderer.contrastEnhancement() is not None:
            enhancement = renderer.contrastEnhancement()
            span = [_rounded(enhancement.minimumValue()), _rounded(enhancement.maximumValue())]
        else:
            return None
        if None in span:
            return None
        return ("band1_values" if _whole_band_range(renderer) else "band1_stretch"), span
    except Exception:  # noqa: BLE001
        return None


def raster_card(layer) -> dict:




    card: dict = {}
    try:
        card["bands"] = int(layer.bandCount())
        card["pixel_size"] = {"x": round(layer.rasterUnitsPerPixelX(), 4), "y": round(layer.rasterUnitsPerPixelY(), 4)}
        provider = layer.dataProvider() if layer.bandCount() >= 1 else None
    except Exception:  # nosec B110
        return card
    try:
        card["size_px"] = [int(layer.width()), int(layer.height())]
    except Exception:  # nosec B110
        pass
    if provider is None:
        return card
    try:
        card["band1_type"] = ground.data_type_name(provider, 1)
    except Exception:  # nosec B110
        pass
    try:
        if provider.sourceHasNoDataValue(1):
            card["nodata"] = provider.sourceNoDataValue(1)
    except Exception:  # nosec B110
        pass
    names = _band_names(provider, card["bands"])
    if names:
        card["band_names"] = names
    span = _band1_span(layer)
    if span is not None:
        card[span[0]] = span[1]
    return card





_RENDERER_NAMES = {
    "singleSymbol": "single",
    "categorizedSymbol": "categorized",
    "graduatedSymbol": "graduated",
    "RuleRenderer": "rule-based",
    "pointCluster": "cluster",
    "pointDisplacement": "displaced points",
    "heatmapRenderer": "heatmap",
    "nullSymbol": "not drawn",
    "invertedPolygonRenderer": "inverted polygon",
    "mergedFeatureRenderer": "merged features",
    "25dRenderer": "2.5d",
    "singlebandgray": "grey band",
    "singlebandpseudocolor": "pseudocolour",
    "multibandcolor": "RGB bands",
    "paletted": "palette",
    "hillshade": "hillshade",
    "singlebandcolordata": "colour data",
    "contour": "contour",
}


def style_summary(layer) -> str | None:







    try:
        renderer = layer.renderer()
    except Exception:  # nosec B110
        return None
    if renderer is None:
        return None
    try:
        kind = str(renderer.type())
    except Exception:  # nosec B110
        return None
    parts = [_RENDERER_NAMES.get(kind, kind)]
    try:
        field = getattr(renderer, "classAttribute", None)
        if callable(field):
            name = str(field() or "").strip()
            if name:
                parts.append(f"on {name}")
        for count, word in ((getattr(renderer, "categories", None), "classes"),
                            (getattr(renderer, "ranges", None), "classes"),
                            (getattr(renderer, "rootRule", None), "rules")):
            if not callable(count):
                continue
            got = count()
            n = len(got.children()) if word == "rules" and hasattr(got, "children") else len(got)
            if n:
                parts.append(f"{n} {word}")
            break
    except Exception:  # nosec B110
        pass


    try:
        if kind == "singleSymbol" and renderer.symbol() is not None:
            parts.append(renderer.symbol().color().name())
    except Exception:  # nosec B110
        pass
    try:
        opacity = float(layer.opacity())
        if opacity < 0.999:
            parts.append(f"opacity {round(opacity, 2)}")
    except Exception:  # nosec B110
        pass
    try:
        if layer.labelsEnabled():
            settings = layer.labeling().settings() if layer.labeling() is not None else None
            field = str(getattr(settings, "fieldName", "") or "").strip() if settings else ""
            parts.append(f"labels {field}" if field else "labels on")
    except Exception:  # nosec B110
        pass
    return ", ".join(parts) if parts else None


def rich_card(layer, project, is_active: bool) -> dict:

    card: dict = {}
    if isinstance(layer, QgsVectorLayer):
        fields, more = field_card(layer)
        if fields:
            card["fields"] = fields
            if more:
                card["fields_more"] = more
        if is_editing(layer):
            card["editing"] = True
        size = extent_size(layer, project)
        if size:
            card["extent_size"] = size
        if is_active:
            sample = sample_attributes(layer)
            if sample:
                card["sample"] = sample
    elif isinstance(layer, QgsRasterLayer):
        card.update(raster_card(layer))

        size = None if source_kind(layer) in ("wms", "xyz") else extent_size(layer, project)
        if size:
            card["extent_size"] = size
    style = style_summary(layer)
    if style:
        card["style"] = style
    return card
