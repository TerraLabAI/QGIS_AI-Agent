# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

import math
import os
import re

import qgis.utils
from qgis.core import (
    QgsApplication,
    QgsCoordinateTransform,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication

from . import ground, tuning
from .crs_ref import crs_ref
from .feature_requests import feature_request, first_feature
from .layer_order import is_web_service
from .layer_order import positions as tree_positions
from .layer_rank import FLOOR_ORDER, fields_named_in, named_in, offset_from_view, rank_layers, view_of
from .layer_rank import detailed_ids as choose_detailed
from .serialization import qt_temporal_text


def _cap(name: str, default: int) -> int:







    return tuning.limit("context", name, default)


def _ranking() -> tuple[tuple, frozenset | None]:


    rules = tuning.service_doc("context_rules") or {}
    order = tuple(rules.get("rank") or ()) or FLOOR_ORDER
    words = rules.get("generic_fields")
    return order, (frozenset(words) if isinstance(words, list) else None)


MAX_LAYERS = 200








MAX_FIELDS = 16
MAX_SAMPLE_VALUE_CHARS = 80


MAX_CHIP_VALUE_CHARS = 600
MAX_DETAILED_LAYERS = 25
RICH_LAYER_COUNT = 8
MAX_MORE_LAYER_NAMES = 15






MAX_CARD_FIELDS = 24



CHIP_KINDS = ("layer", "selection", "extent", "field", "layout", "file", "source", "plugin")
AI_EDIT_FOLDERS = ("QGIS_AI-Edit-Team", "QGIS_AI-Edit", "ai_edit", "AI_Edit")
AI_SEGMENTATION_FOLDERS = ("QGIS_AI-Segmentation-Team", "QGIS_AI-Segmentation",
                           "QGIS_AI_Segmentation_Team", "AI_Segmentation", "ai_segmentation")
QMS_FOLDERS = ("quick_map_services",)


def tr(text: str) -> str:
    return QCoreApplication.translate("AIAgentContext", text)


def _iface():
    return qgis.utils.iface




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


def feature_count(layer, kind: str) -> int | None:









    if not isinstance(layer, QgsVectorLayer) or kind not in ("memory", "file"):
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


def view_bbox_4326(extent, crs, project) -> list | None:

    try:
        from qgis.core import QgsCoordinateReferenceSystem
        if crs.authid() != "EPSG:4326":
            transform = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem("EPSG:4326"), project)
            extent = transform.transformBoundingBox(extent)
        west, east = extent.xMinimum(), extent.xMaximum()
        south, north = extent.yMinimum(), extent.yMaximum()
        if abs(east - west) < 0.0001 or abs(north - south) < 0.0001:
            return None
        return [round(west, 6), round(south, 6), round(east, 6), round(north, 6)]
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


def sample_attributes(layer) -> dict | None:





    if source_kind(layer) not in ("memory", "file"):
        return None
    try:
        fields = list(layer.fields())
        eligible = [(index, field) for index, field in enumerate(fields)
                    if field.typeName().lower() not in ("binary", "blob")]
        chosen = eligible[:MAX_FIELDS]
        if not chosen:
            return None
        feature = first_feature(layer, feature_request(
            attributes=[index for index, _field in chosen], geometry=False, limit=1))
        if feature is None:
            return None
        out: dict = {}
        for index, field in chosen:
            value = feature[index]
            if isinstance(value, (bytes, bytearray)):
                continue
            if type(value).__name__ in ("QDate", "QDateTime", "QTime"):
                value = qt_temporal_text(value)
            out[field.name()] = str(value)[:MAX_SAMPLE_VALUE_CHARS]
        if len(eligible) > len(chosen):
            out["_more_fields"] = len(eligible) - len(chosen)
        return out or None
    except Exception:
        return None


def extent_size(layer, canvas_crs, project, rect=None) -> dict | None:















    if rect is None:
        rect = layer_extent_rect(layer, canvas_crs, project)
    if not rect:
        return None
    try:
        extent = layer.extent()
        layer_crs = layer.crs()
        width, height = extent.width(), extent.height()


        digits = 6 if layer_crs.isGeographic() else 2
        out = {"width": round(width, digits), "height": round(height, digits),
               "crs": crs_ref(layer_crs), "units": crs_units(layer_crs)}
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


def rich_card(layer, canvas_crs, project, is_active: bool, rect=None) -> dict:

    card: dict = {}
    if isinstance(layer, QgsVectorLayer):
        fields, more = field_card(layer)
        if fields:
            card["fields"] = fields
            if more:
                card["fields_more"] = more
        if is_editing(layer):
            card["editing"] = True
        size = extent_size(layer, canvas_crs, project, rect)
        if size:
            card["extent_size"] = size
        if is_active:
            sample = sample_attributes(layer)
            if sample:
                card["sample"] = sample
    elif isinstance(layer, QgsRasterLayer):
        card.update(raster_card(layer))
    style = style_summary(layer)
    if style:
        card["style"] = style
    return card




def _plugin_dirs() -> list[str]:
    dirs = []
    try:
        dirs.append(os.path.join(QgsApplication.qgisSettingsDirPath(), "python", "plugins"))
    except Exception:  # nosec B110
        pass
    dirs.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    return dirs


def _plugin_present(folders: tuple) -> bool:
    loaded = set(getattr(qgis.utils, "plugins", {}).keys()) | set(getattr(qgis.utils, "active_plugins", []))
    if any(name in loaded for name in folders):
        return True
    for base in _plugin_dirs():
        for name in folders:
            if os.path.isdir(os.path.join(base, name)):
                return True
    return False


def quickmapservices_started() -> bool:





    return any(name in getattr(qgis.utils, "plugins", {}) for name in QMS_FOLDERS)


def plugins_present() -> dict:


    return {"ai_edit": _plugin_present(AI_EDIT_FOLDERS),
            "ai_segmentation": _plugin_present(AI_SEGMENTATION_FOLDERS),
            "quickmapservices": quickmapservices_started()}




def clean_chips(chips) -> list[dict]:
    out = []
    for chip in chips or []:
        if not isinstance(chip, dict):
            continue
        kind = str(chip.get("kind") or "")
        if kind not in CHIP_KINDS:
            continue
        value = str(chip.get("value") or "")
        clean = {"kind": kind, "label": str(chip.get("label") or "")[:200],
                 "value": value[:MAX_CHIP_VALUE_CHARS]}
        if len(value) > MAX_CHIP_VALUE_CHARS:
            clean["value_chars"] = len(value)
        out.append(clean)
        if len(out) >= 20:
            break
    return out


MAX_LAYOUT_NAMES = 20
MAX_HISTORY_ENTRIES = 5









_HISTORY_FULL_S = 600.0
_history: dict = {"rows": None, "limit": 0, "newest": None, "newest_id": -1, "read_at": 0.0}


def _history_row(entry) -> tuple | None:
    details = getattr(entry, "entry", None) or {}
    if not isinstance(details, dict):
        return None
    algorithm = str(details.get("algorithm_id") or "").strip()
    if not algorithm:
        return None
    params = details.get("parameters")
    if isinstance(params, dict) and isinstance(params.get("inputs"), dict):
        params = params["inputs"]
    stamp = getattr(entry, "timestamp", None)
    at = stamp.toString("yyyy-MM-dd HH:mm") if stamp is not None and hasattr(stamp, "toString") else ""
    return (algorithm, params if isinstance(params, dict) else None, at)


def _newest_rows(entries, limit: int) -> list[tuple]:
    rows: list[tuple] = []
    for entry in reversed(entries):
        row = _history_row(entry)
        if row is not None:
            rows.append(row)
            if len(rows) >= limit:
                break
    return rows


def _history_rows(registry, limit: int) -> list[tuple]:

    import time

    from qgis.PyQt.QtCore import QDateTime

    now = time.monotonic()
    cached, newest = _history["rows"], _history["newest"]
    if (cached is not None and newest is not None and _history["limit"] == limit
            and now - _history["read_at"] < _HISTORY_FULL_S):
        fresh = [entry for entry in registry.queryEntries(newest, QDateTime(), "processing")
                 if getattr(entry, "id", -1) > _history["newest_id"]]
        if not fresh:
            return cached
        rows = (_newest_rows(fresh, limit) + cached)[:limit]
        entries = fresh
    else:
        entries = list(registry.queryEntries(providerId="processing"))
        rows = _newest_rows(entries, limit)
        _history["read_at"] = now
    last = entries[-1] if entries else None
    last_id = getattr(last, "id", None) if last is not None else None
    stamp = getattr(last, "timestamp", None) if last is not None else None
    if isinstance(last_id, int) and stamp is not None:
        _history.update(rows=rows, limit=limit, newest=QDateTime(stamp), newest_id=last_id)
    else:
        _history.update(rows=None, newest=None, newest_id=-1)
    return rows


def _recent_processing() -> list[dict]:




    try:
        from qgis.gui import QgsGui

        registry = QgsGui.historyProviderRegistry()
        rows = _history_rows(registry, _cap("max_history_entries", MAX_HISTORY_ENTRIES))
    except Exception:  # nosec B110
        return []
    out: list[dict] = []
    names = _layer_names_by_source(QgsProject.instance()) if rows else {}
    for algorithm, params, at in rows:
        item: dict = {"algorithm": algorithm}
        if params:
            item["parameters"] = history_parameters(params, names)
        if at:
            item["at"] = at
        out.append(item)
    return out


MAX_HISTORY_PARAMETERS = 12

_EMPTY_PARAMETER_VALUES = ("", "None", "NULL", "[]", "{}")
_MEMORY_SOURCE = "memory://"
GONE_MEMORY_LAYER = "memory layer no longer in the project"


_BARE_SOURCE_PROVIDERS = ("gdal", "ogr", "mdal")


def _processing_identifier(layer, source: str) -> str:





    rule = source.replace("\\", "/").strip()
    try:
        provider = str(layer.providerType() or "")
    except Exception:  # noqa: BLE001
        return rule
    if provider and provider.lower() not in _BARE_SOURCE_PROVIDERS:
        rule = f"{provider}://{rule}"
    try:
        from qgis.core import QgsProcessingUtils

        found = QgsProcessingUtils.layerToStringIdentifier(layer)
    except Exception:  # noqa: BLE001
        return rule
    return found if isinstance(found, str) and found else rule


def _layer_names_by_source(project) -> dict:









    exact: dict = {}
    by_path: dict = {}
    try:
        layers = list(project.mapLayers().values())
    except Exception:  # nosec B110
        return {}
    for layer in layers:
        try:
            source, name = str(layer.source() or ""), str(layer.name() or "")
        except Exception:  # nosec B112
            continue
        if not source or not name:
            continue
        exact.setdefault(source, name)
        exact.setdefault(_processing_identifier(layer, source), name)
        path = source.split("|", 1)[0]
        if path != source:
            by_path.setdefault(path, set()).add(name)
    for path, found in by_path.items():
        if len(found) == 1 and path not in exact:
            exact[path] = next(iter(found))
    return exact


def _parameter_source(value) -> str:

    if isinstance(value, dict):
        source = value.get("source")
        if isinstance(source, dict):
            source = source.get("val")
        return str(source) if source is not None else str(value)
    return "" if value is None else str(value)


def history_parameters(params: dict, names: dict) -> dict:







    out: dict = {}
    for key, value in params.items():
        text = _parameter_source(value)
        if value is None or text.strip() in _EMPTY_PARAMETER_VALUES:
            continue
        name = names.get(text)
        if name is None and text.startswith(_MEMORY_SOURCE):
            name = GONE_MEMORY_LAYER
        out[str(key)] = name if name is not None else _short_param(value)
        if len(out) >= MAX_HISTORY_PARAMETERS:
            break
    return out


MAX_LOG_LINES = 3
MAX_LOG_CHARS = 200
_LOG_RING_PROPERTY = "_aiagent_msglog_buf"
_OWN_LOG_TAG = "AI Agent"


def _recent_log_problems() -> list[dict]:





    try:
        from qgis.core import QgsApplication

        ring = QgsApplication.instance().property(_LOG_RING_PROPERTY)
    except Exception:  # nosec B110
        return []
    if not ring:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for item in reversed(list(ring)):
        if not isinstance(item, dict) or item.get("level") not in ("warning", "critical"):
            continue
        if str(item.get("tag") or "") == _OWN_LOG_TAG:
            continue
        text = " ".join(str(item.get("message") or "").split())[:_cap("max_log_chars", MAX_LOG_CHARS)]
        if not text or text in seen:
            continue
        seen.add(text)
        out.append({"level": item.get("level"), "tag": str(item.get("tag") or ""), "message": text,
                    "at": str(item.get("timestamp") or "")})
        if len(out) >= _cap("max_log_lines", MAX_LOG_LINES):
            break
    return out


def _temporal_range(canvas) -> dict | None:

    try:
        if not canvas.mapSettings().isTemporal():
            return None
        span = canvas.temporalRange()
        begin, end = span.begin(), span.end()
        if not begin.isValid() or not end.isValid():
            return None
        return {"begin": begin.toString("yyyy-MM-dd HH:mm"), "end": end.toString("yyyy-MM-dd HH:mm")}
    except Exception:  # nosec B110
        return None


_TEMP_OUTPUT = "TEMPORARY_OUTPUT"


_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]|~[\\/])")


def _slash_case(path: str) -> str:

    return os.path.normcase(path).replace("\\", "/").rstrip("/")


_NOT_A_PATH = ("http://", "https://", "memory:", "postgres", "wfs:", "wms:", "xyz:", "vsi", "/vsi")


def _short_param(value) -> str:











    text = str(value)
    if text == _TEMP_OUTPUT:
        return text
    name = _file_name(text)
    if name:
        return name
    return text if len(text) <= 80 else text[:77] + "..."


def _file_name(text: str) -> str:










    if not text or len(text) < 4:
        return ""





    if " " in text.strip() and not _ABSOLUTE_PATH.match(text.strip()):
        return ""
    if any(text.startswith(prefix) for prefix in _NOT_A_PATH):
        return ""
    if "/" not in text and "\\" not in text:
        return ""
    tail = text.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return tail[:80] if tail else ""


def _home_relative_dir(text: str) -> str:






    if not text:
        return ""
    home = os.path.expanduser("~")



    here, root = _slash_case(text), _slash_case(home)
    if here == root:
        return "~"
    if here.startswith(root + "/"):
        return "~" + text[len(home):]
    return text


def _layout_names(project) -> list[str] | None:



    try:
        cap = _cap("max_layout_names", MAX_LAYOUT_NAMES)
        return [layout.name() for layout in project.layoutManager().printLayouts()][:cap]
    except Exception:  # nosec B110
        return None


def _capped_layers(layers: list, places: dict, active_id) -> list:






    cap = _cap("max_layers", MAX_LAYERS)
    if len(layers) <= cap:
        return layers

    def rank(item):
        lid = item[0]
        place = places.get(lid)
        return (0 if lid == active_id else 1,
                place["index"] if place else 10 ** 9)
    return sorted(layers, key=rank)[:cap]


THREAD_PROPERTY = "ai_agent/thread"


MIN_RICH_LAYERS = 3
_NO_VIEW_KINDS = ("xyz", "wms")


def stamp_thread(layers, thread_id: str) -> None:





    if not thread_id:
        return
    for layer in layers or ():
        try:
            layer.setCustomProperty(THREAD_PROPERTY, thread_id)
        except Exception:  # nosec B112
            continue


def _made_here(layer, thread_id: str) -> bool:
    if not thread_id or layer is None:
        return False
    try:
        return str(layer.customProperty(THREAD_PROPERTY, "") or "") == thread_id
    except Exception:  # noqa: BLE001
        return False


def _field_names(layer) -> list[str]:
    if not isinstance(layer, QgsVectorLayer):
        return []
    try:
        return [f.name() for f in layer.fields()]
    except Exception:  # noqa: BLE001
        return []


def _chip_layer_ids(chips: list) -> set:
    return {str(chip.get("value") or "") for chip in chips or []
            if isinstance(chip, dict) and chip.get("kind") in ("layer", "selection")}


def _tree_selected_ids(iface) -> set:







    try:
        view = iface.layerTreeView() if iface is not None else None
        if view is None:
            return set()
        reader = getattr(view, "selectedLayersRecursive", None) or view.selectedLayers
        ids = {layer.id() for layer in reader() if layer is not None}
    except Exception:  # nosec B110
        return set()
    return ids if len(ids) > 1 else set()


def _place_against_view(record: dict, rect, extent_rect, canvas_box, canvas_crs, project) -> None:

    where = view_of(rect, extent_rect)
    if not where:
        return
    record["view"] = where
    if where != "out" or not canvas_box or canvas_crs is None:
        return
    try:
        box = view_bbox_4326(QgsRectangle(rect["xmin"], rect["ymin"], rect["xmax"], rect["ymax"]), canvas_crs, project)
    except Exception:  # noqa: BLE001
        box = None
    offset = offset_from_view(canvas_box, box)
    if offset is not None:
        record["view_km"], record["view_dir"] = offset


def build_context(chips: list | None = None, text: str = "", thread_id: str = "") -> dict:






    project = QgsProject.instance()
    iface = _iface()
    project_crs = project.crs()
    root = project.layerTreeRoot()
    all_layers_full = list(project.mapLayers().items())

    canvas_block = {"extent": None, "crs": "", "units": "", "scale": None}
    canvas_scale = None
    canvas_crs = None
    extent_rect = None
    active = None
    active_name = None
    if iface is not None:
        try:
            canvas = iface.mapCanvas()
            canvas_crs = canvas.mapSettings().destinationCrs()
            extent = canvas.extent()
            extent_rect = {"xmin": extent.xMinimum(), "ymin": extent.yMinimum(),
                           "xmax": extent.xMaximum(), "ymax": extent.yMaximum()}
            canvas_block = {
                "extent": dict(extent_rect),
                "crs": canvas_crs.authid() or "",
                "units": crs_units(canvas_crs),
                "scale": round(float(canvas.scale()), 2),
            }




            canvas_scale = ground.canvas_metres_per_unit(canvas)
            if ground.distorted(canvas_scale):
                canvas_block["ground_metres_per_unit"] = round(canvas_scale, 4)
            area = view_area_km2(extent, canvas_crs, project)
            if area is not None:


                canvas_block["area_km2"] = area
            box = view_bbox_4326(extent, canvas_crs, project)
            if box is not None:



                canvas_block["extent_4326"] = box
            active = iface.activeLayer()
            if active is not None:
                active_name = active.name()
        except Exception:  # nosec B110
            pass





    ambiguous = duplicate_names([layer for _lid, layer in all_layers_full])




    places = tree_positions(root)
    tree_ids = _tree_selected_ids(iface)





    all_layers = _capped_layers(all_layers_full, places,
                                active.id() if active is not None else None)
    items = []
    for lid, layer in all_layers:
        try:
            record = layer_record(layer, root.findLayer(lid), ambiguous)
        except Exception as exc:
            name_attr = getattr(layer, "name", None)
            name = name_attr() if callable(name_attr) else lid
            record = {"name": name, "id": lid, "type": "other",
                      "geometry": None, "crs": "", "visible": True, "feature_count": None,
                      "source_kind": "other", "error": str(exc)[:120]}
            layer = None
        subset = layer_filter(layer) if layer is not None else ""
        if subset:


            record["filter"] = subset
        sel = selected_count(layer) if layer is not None else 0
        if sel:
            record["selected_count"] = sel


            record["selected_only"] = True
        if lid in tree_ids:
            record["tree_selected"] = True
        place = places.get(lid)
        if place is not None:
            record["draw_order"] = place["index"]
            if place["group"]:
                record["group"] = place["group"]
            if not place["group_visible"]:
                record["group_visible"] = False
        rect = layer_extent_rect(layer, canvas_crs, project) if layer is not None else None
        items.append({"id": lid, "layer": layer, "record": record, "extent": rect,
                      "by_agent": _made_here(layer, thread_id)})



    chip_ids = _chip_layer_ids(chips) | tree_ids
    order, generic = _ranking()
    named = named_in(text, [str(it["record"].get("name") or "") for it in items]) if text else set()
    for index, it in enumerate(items):
        it["named"] = (index in named or it["id"] in chip_ids
                       or (bool(text) and fields_named_in(text, _field_names(it["layer"])[:MAX_CARD_FIELDS * 2],
                                                          generic)))
    active_id = active.id() if active is not None else None
    ranked = rank_layers(
        [{"id": it["id"], "visible": it["record"].get("visible"),
          "selected_count": it["record"].get("selected_count", 0), "extent": it["extent"],
          "named": it["named"], "by_agent": it["by_agent"]}
         for it in items],
        active_id,
        extent_rect,
        order,
    )
    by_id = {it["id"]: it for it in items}
    order = [r["id"] for r in ranked]
    detailed = _cap("max_detailed_layers", MAX_DETAILED_LAYERS)
    detailed_ids = order[:detailed]
    rest_ids = order[detailed:]
    listed = set(detailed_ids)
    rich_ids = set(choose_detailed([r for r in ranked if r["id"] in listed], active_id,
                                   _cap("max_rich_layers", RICH_LAYER_COUNT),
                                   _cap("min_rich_layers", MIN_RICH_LAYERS)))
    canvas_box = canvas_block.get("extent_4326")

    layers_out = []
    for lid in detailed_ids:
        it = by_id[lid]
        record = dict(it["record"])
        if lid in rich_ids and it["layer"] is not None:
            is_active = active is not None and lid == active.id()
            record.update(rich_card(it["layer"], canvas_crs, project, is_active, it["extent"]))
        if it["by_agent"]:
            record["by_agent"] = True
        if record.get("source_kind") not in _NO_VIEW_KINDS:
            _place_against_view(record, it["extent"], extent_rect, canvas_box, canvas_crs, project)
        layers_out.append(record)




    layers_out.sort(key=lambda record: record.get("draw_order") or 10 ** 9)

    layouts = _layout_names(project)
    project_path = project.fileName() or ""





    project_scale = canvas_scale if canvas_crs is not None and canvas_crs == project_crs else None
    context = {
        "project_crs": project_crs.authid() or "",
        "project_crs_units": crs_units(project_crs),

        **({"project_crs_ground_metres_per_unit": round(project_scale, 4)}
           if ground.distorted(project_scale) else {}),


        "project_file": os.path.basename(project_path) if project_path else "",
        "project_folder": _home_relative_dir(os.path.dirname(project_path)) if project_path else "",
        "canvas": canvas_block,
        "layers": layers_out,
        "active_layer": active_name,
        "plugins": plugins_present(),
        "chips": clean_chips(chips),

        "layer_detail": "relevant",
    }
    measure = project_measure(project)
    if measure:
        context["project_measure"] = measure
    if layouts or layouts is None:
        context["layouts"] = layouts
    history = _recent_processing()
    if history:
        context["processing_history"] = history
    problems = _recent_log_problems()
    if problems:
        context["log_problems"] = problems
    temporal = _temporal_range(iface.mapCanvas()) if iface is not None else None
    if temporal:
        context["temporal"] = temporal
    if rest_ids:
        names = [str(by_id[lid]["record"].get("name") or lid)
                 for lid in rest_ids[:_cap("max_more_layer_names", MAX_MORE_LAYER_NAMES)]]
        context["more_layers"] = {"count": len(rest_ids), "names": names}
    if len(all_layers_full) > len(all_layers):
        context["layers_omitted"] = len(all_layers_full) - len(all_layers)







    try:
        from . import limits, machine

        note = machine.note()
        if note:
            context["machine"] = note










            caps = {}






            for name, key in (("MAX_FETCH_KM2", "max_fetch_km2"),
                              ("FETCH_OWN_OVERPASS_REGION_KM2", "osm_region_km2"),
                              ("MAX_FEATURES_PER_CALL", "max_features_per_call"),
                              ("MAX_RENDER_WIDTH_PX", "max_render_width_px"),
                              ("MAX_RENDER_HEIGHT_PX", "max_render_height_px"),
                              ("MAX_DOWNLOAD_BYTES", "max_download_bytes")):
                try:
                    caps[key] = limits.current(name)
                except Exception:  # noqa: BLE001  # nosec B112
                    continue
            if caps:
                context["limits"] = caps
    except Exception:  # nosec B110
        pass
    return context




def layer_geometry_label(layer) -> str:


    try:
        if isinstance(layer, QgsVectorLayer):
            if layer.geometryType() == QgsWkbTypes.GeometryType.NullGeometry:
                return tr("Table")
            return geometry_name(layer) or tr("Table")
        if isinstance(layer, QgsRasterLayer):
            return tr("Raster")
    except Exception:  # nosec B110
        pass
    return ""


def mention_candidates() -> list[dict]:







    project = QgsProject.instance()
    out: list[dict] = []
    try:
        ordered = [n.layer() for n in project.layerTreeRoot().findLayers() if n.layer() is not None]
    except Exception:  # nosec B110
        ordered = []
    seen = {layer.id() for layer in ordered}
    ordered.extend(layer for layer in project.mapLayers().values() if layer.id() not in seen)
    depths, groups = {}, {}
    try:
        for node in project.layerTreeRoot().findLayers():
            depth, parent, names = 0, node.parent(), []


            while parent is not None and parent.parent() is not None:
                depth += 1
                names.append(parent.name())
                parent = parent.parent()
            depths[node.layerId()] = depth
            groups[node.layerId()] = " / ".join(reversed(names))
    except Exception:  # nosec B110
        depths, groups = {}, {}
    for layer in ordered[:MAX_LAYERS]:
        out.append({"kind": "layer", "label": layer.name(), "value": layer.id(),
                    "detail": layer_geometry_label(layer), "depth": depths.get(layer.id(), 0),
                    "group": groups.get(layer.id(), "")})
    out.extend(source_candidates())
    return out


def source_candidates() -> list[dict]:






    try:
        from ..ui.shared import get_connectors

        rows = get_connectors()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for row in sorted(rows, key=lambda r: str(r.get("name") or "")):
        key = str(row.get("id") or "")
        if not key:
            continue
        out.append({"kind": "source", "glyph": str(row.get("glyph") or "globe"),
                    "label": str(row.get("name") or key), "value": key,
                    "detail": str(row.get("licence") or "")})
    return out
