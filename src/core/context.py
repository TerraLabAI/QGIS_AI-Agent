# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Project context for every turn, the empty-state suggestions, the @ candidates."""





from __future__ import annotations

import os
import re

import qgis.utils
from qgis.core import QgsApplication, QgsCoordinateTransform, QgsProject, QgsRasterLayer, QgsVectorLayer, QgsWkbTypes
from qgis.PyQt.QtCore import QCoreApplication

from . import ground, tuning
from .layer_order import positions as tree_positions
from .layer_rank import rank_layers


def _cap(name: str, default: int) -> int:
    """A context size the server may tune, or the number this plugin shipped with."""






    return tuning.limit("context", name, default)


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
    except Exception:  # nosec B110 - optional QGIS context
        pass
    return "degrees" if crs.isGeographic() else "meters"


def project_measure(project) -> dict:
    """What QGIS itself measures with: the ellipsoid, the distance and area units."""









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
    except Exception:  # nosec B110 - optional QGIS context
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
    except Exception:  # nosec B110 - optional QGIS context
        geom = layer.geometryType()
        return getattr(geom, "name", str(geom))


def source_kind(layer) -> str:
    provider = (layer.providerType() or "").lower()
    source = (layer.source() or "").lower()
    if provider == "memory":
        return "memory"
    if provider in ("wfs", "oapif", "arcgisfeatureserver"):
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
    """Only for the providers cheap enough to ask: local files, memory, gpkg/shp/geojson."""



    if not isinstance(layer, QgsVectorLayer) or kind not in ("memory", "file"):
        return None
    try:
        n = layer.featureCount()
    except Exception:  # nosec B110 - optional QGIS context
        return None
    return int(n) if n is not None and n >= 0 else None


def duplicate_names(layers) -> set[str]:
    """Names carried by more than one layer, so only those need their id spelled out."""
    seen: dict[str, int] = {}
    for layer in layers:
        try:
            name = layer.name()
        except Exception:  # nosec B110 - a broken layer is still one name we did not read
            name = ""
        if name:
            seen[name] = seen.get(name, 0) + 1
    return {name for name, count in seen.items() if count > 1}


def layer_record(layer, node=None, ambiguous: set[str] | None = None) -> dict:
    """One layer as the opening context sees it."""











    kind = source_kind(layer)
    crs = layer.crs()
    name = layer.name()
    record = {
        "name": name,
        "type": layer_kind(layer),
        "geometry": geometry_name(layer),
        "crs": crs.authid() or "",
        "visible": bool(node.isVisible()) if node is not None else True,
        "feature_count": feature_count(layer, kind),
        "source_kind": kind,
    }



    if ambiguous is None or name in ambiguous or tuning.flag("context", "always_send_layer_id", False):
        record["id"] = layer.id()
    return record




def selected_count(layer) -> int:
    try:
        if isinstance(layer, QgsVectorLayer):
            return int(layer.selectedFeatureCount())
    except Exception:  # nosec B110 - optional QGIS context
        pass
    return 0


def view_bbox_4326(extent, crs, project) -> list | None:
    """``extent`` given in ``crs`` as [west, south, east, north] in EPSG:4326, or None."""
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
    except Exception:  # noqa: BLE001 - no CRS or a broken transform: no box
        return None


def view_area_km2(extent, crs, project) -> float | None:
    """Ellipsoidal area of a rectangle given in ``crs``, in km², rounded."""
    try:
        from qgis.core import QgsDistanceArea, QgsGeometry
        measure = QgsDistanceArea()
        measure.setSourceCrs(crs, project.transformContext())
        measure.setEllipsoid("WGS84")
        km2 = abs(measure.measureArea(QgsGeometry.fromRect(extent))) / 1e6
        return round(km2, 3) if km2 < 10 else round(km2, 1)
    except Exception:  # noqa: BLE001 - no CRS or a broken transform: no number
        return None


def extent_is_cheap(layer) -> bool:
    """Whether ``layer.extent()`` answers from memory rather than from the network."""







    if not isinstance(layer, QgsVectorLayer):
        return True
    return source_kind(layer) in ("memory", "file")


def layer_extent_rect(layer, canvas_crs, project) -> dict | None:
    """The layer's extent as a plain rect in the canvas CRS, or None if either is unknown."""
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
                "xmax": extent.xMaximum(), "ymax": extent.yMaximum()}
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
    """First feature's attributes, MAX_FIELDS of them, values cut to 80 chars, binary fields skipped."""




    if source_kind(layer) not in ("memory", "file"):
        return None
    try:
        feature = next(layer.getFeatures(), None)
    except Exception:
        feature = None
    if feature is None:
        return None
    out: dict = {}
    try:
        for field, value in zip(layer.fields(), feature.attributes()):
            if isinstance(value, (bytes, bytearray)) or field.typeName().lower() in ("binary", "blob"):
                continue
            if len(out) >= MAX_FIELDS:
                out["_more_fields"] = len(layer.fields()) - MAX_FIELDS
                break
            out[field.name()] = str(value)[:MAX_SAMPLE_VALUE_CHARS]
    except Exception:
        return None
    return out or None


def extent_size(layer, canvas_crs, project, rect=None) -> dict | None:
    """Width/height of ``rect``, or of a freshly computed one when the caller has none."""




    if rect is None:
        rect = layer_extent_rect(layer, canvas_crs, project)
    if not rect:
        return None
    return {"width": round(rect["xmax"] - rect["xmin"], 2), "height": round(rect["ymax"] - rect["ymin"], 2)}


def is_editing(layer) -> bool:
    try:
        return bool(layer.isEditable())
    except Exception:
        return False


def raster_card(layer) -> dict:
    card: dict = {}
    try:
        card["bands"] = int(layer.bandCount())
        card["pixel_size"] = {"x": round(layer.rasterUnitsPerPixelX(), 4), "y": round(layer.rasterUnitsPerPixelY(), 4)}
        if layer.bandCount() >= 1:
            provider = layer.dataProvider()
            card["band1_type"] = QgsRasterLayer.dataTypeToString(provider.dataType(1))
            if provider.sourceHasNoDataValue(1):
                card["nodata"] = provider.sourceNoDataValue(1)
    except Exception:  # nosec B110 - optional QGIS context
        pass
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
    """The layer's symbology in one short phrase, or None when it cannot be read."""






    try:
        renderer = layer.renderer()
    except Exception:  # nosec B110 - optional QGIS context
        return None
    if renderer is None:
        return None
    try:
        kind = str(renderer.type())
    except Exception:  # nosec B110 - optional QGIS context
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
    except Exception:  # nosec B110 - a renderer that answers differently keeps its type alone
        pass


    try:
        if kind == "singleSymbol" and renderer.symbol() is not None:
            parts.append(renderer.symbol().color().name())
    except Exception:  # nosec B110 - optional QGIS context
        pass
    try:
        opacity = float(layer.opacity())
        if opacity < 0.999:
            parts.append(f"opacity {round(opacity, 2)}")
    except Exception:  # nosec B110 - optional QGIS context
        pass
    try:
        if layer.labelsEnabled():
            settings = layer.labeling().settings() if layer.labeling() is not None else None
            field = str(getattr(settings, "fieldName", "") or "").strip() if settings else ""
            parts.append(f"labels {field}" if field else "labels on")
    except Exception:  # nosec B110 - rasters and unlabelled layers have neither
        pass
    return ", ".join(parts) if parts else None


def rich_card(layer, canvas_crs, project, is_active: bool, rect=None) -> dict:
    """Cheap, safe extra metadata for a top-ranked layer. No expensive stats."""
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
    except Exception:  # nosec B110 - optional QGIS context
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


def plugins_present() -> dict:
    return {"ai_edit": _plugin_present(AI_EDIT_FOLDERS),
            "ai_segmentation": _plugin_present(AI_SEGMENTATION_FOLDERS)}




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


def _recent_processing() -> list[dict]:
    """The last Processing runs of this QGIS (toolbox, model, or the agent), newest first."""



    try:
        from qgis.gui import QgsGui

        registry = QgsGui.historyProviderRegistry()
        entries = registry.queryEntries(providerId="processing")
    except Exception:  # nosec B110 - optional QGIS context
        return []
    out: list[dict] = []
    for entry in reversed(list(entries)):
        details = getattr(entry, "entry", None) or {}
        if not isinstance(details, dict):
            continue
        algorithm = str(details.get("algorithm_id") or "").strip()
        if not algorithm:
            continue
        item: dict = {"algorithm": algorithm}
        params = details.get("parameters")
        if isinstance(params, dict) and isinstance(params.get("inputs"), dict):
            params = params["inputs"]
        if isinstance(params, dict) and params:
            item["parameters"] = {str(k): _short_param(v) for k, v in list(params.items())[:12]}
        stamp = getattr(entry, "timestamp", None)
        if stamp is not None and hasattr(stamp, "toString"):
            item["at"] = stamp.toString("yyyy-MM-dd HH:mm")
        out.append(item)
        if len(out) >= _cap("max_history_entries", MAX_HISTORY_ENTRIES):
            break
    return out


MAX_LOG_LINES = 3
MAX_LOG_CHARS = 200
_LOG_RING_PROPERTY = "_aiagent_msglog_buf"
_OWN_LOG_TAG = "AI Agent"


def _recent_log_problems() -> list[dict]:
    """The last warnings and errors of the QGIS message log, newest first."""




    try:
        from qgis.core import QgsApplication

        ring = QgsApplication.instance().property(_LOG_RING_PROPERTY)
    except Exception:  # nosec B110 - optional QGIS context
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
    """The temporal controller's current range when the map is animated, else None."""
    try:
        if not canvas.mapSettings().isTemporal():
            return None
        span = canvas.temporalRange()
        begin, end = span.begin(), span.end()
        if not begin.isValid() or not end.isValid():
            return None
        return {"begin": begin.toString("yyyy-MM-dd HH:mm"), "end": end.toString("yyyy-MM-dd HH:mm")}
    except Exception:  # nosec B110 - QGIS before 3.14 has no temporal canvas
        return None


_TEMP_OUTPUT = "TEMPORARY_OUTPUT"


_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]|~[\\/])")


def _slash_case(path: str) -> str:
    """A path in one spelling, so two of them can be compared on Windows."""
    return os.path.normcase(path).replace("\\", "/").rstrip("/")


_NOT_A_PATH = ("http://", "https://", "memory:", "postgres", "wfs:", "wms:", "xyz:", "vsi", "/vsi")


def _short_param(value) -> str:
    """One Processing parameter, small enough to be worth carrying."""














    text = str(value)
    if text == _TEMP_OUTPUT:
        return text
    name = _file_name(text)
    if name:
        return name
    return text if len(text) <= 80 else text[:77] + "..."


def _file_name(text: str) -> str:
    """The file name of a path-like value, or "" when it is not one."""









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
    """A directory with the user's home folder swapped for "~"."""





    if not text:
        return ""
    home = os.path.expanduser("~")



    here, root = _slash_case(text), _slash_case(home)
    if here == root:
        return "~"
    if here.startswith(root + "/"):
        return "~" + text[len(home):]
    return text


def _layout_names(project) -> list[str]:
    """The print layouts of the project, so the model reuses one instead of creating a twin."""
    try:
        cap = _cap("max_layout_names", MAX_LAYOUT_NAMES)
        return [layout.name() for layout in project.layoutManager().printLayouts()][:cap]
    except Exception:  # nosec B110 - optional QGIS context
        return []


def _capped_layers(layers: list, places: dict, active_id) -> list:
    """The layers the context can carry, chosen by what the user is looking at."""





    cap = _cap("max_layers", MAX_LAYERS)
    if len(layers) <= cap:
        return layers

    def rank(item):
        lid = item[0]
        place = places.get(lid)
        return (0 if lid == active_id else 1,
                place["index"] if place else 10 ** 9)
    return sorted(layers, key=rank)[:cap]


def build_context(chips: list | None = None) -> dict:
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
        except Exception:  # nosec B110 - optional QGIS context
            pass





    ambiguous = duplicate_names([layer for _lid, layer in all_layers_full])




    places = tree_positions(root)





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
        sel = selected_count(layer) if layer is not None else 0
        if sel:
            record["selected_count"] = sel
        place = places.get(lid)
        if place is not None:
            record["draw_order"] = place["index"]
            if place["group"]:
                record["group"] = place["group"]
            if not place["group_visible"]:
                record["group_visible"] = False
        rect = layer_extent_rect(layer, canvas_crs, project) if layer is not None else None
        items.append({"id": lid, "layer": layer, "record": record, "extent": rect})

    ranked = rank_layers(
        [{"id": it["id"], "visible": it["record"].get("visible"),
          "selected_count": it["record"].get("selected_count", 0), "extent": it["extent"]}
         for it in items],
        active.id() if active is not None else None,
        extent_rect,
    )
    by_id = {it["id"]: it for it in items}
    order = [r["id"] for r in ranked]
    detailed = _cap("max_detailed_layers", MAX_DETAILED_LAYERS)
    detailed_ids = order[:detailed]
    rest_ids = order[detailed:]

    layers_out = []
    for idx, lid in enumerate(detailed_ids):
        it = by_id[lid]
        record = dict(it["record"])
        if idx < RICH_LAYER_COUNT and it["layer"] is not None:
            is_active = active is not None and lid == active.id()
            record.update(rich_card(it["layer"], canvas_crs, project, is_active, it["extent"]))
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
    }
    measure = project_measure(project)
    if measure:
        context["project_measure"] = measure
    if layouts:
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
                              ("MAX_FEATURES_PER_CALL", "max_features_per_call"),
                              ("MAX_RENDER_WIDTH_PX", "max_render_width_px"),
                              ("MAX_RENDER_HEIGHT_PX", "max_render_height_px")):
                try:
                    caps[key] = limits.current(name)
                except Exception:  # noqa: BLE001  # nosec B112 - a ceiling that will not answer is left out
                    continue
            if caps:
                context["limits"] = caps
    except Exception:  # nosec B110 - the context is never worth a failed run
        pass
    return context




def _ranked_project_layers() -> list:
    """Every layer in the open project, ordered by the same interest ranking as build_context."""
    project = QgsProject.instance()
    iface = _iface()
    root = project.layerTreeRoot()
    layers = list(project.mapLayers().items())
    if not layers:
        return []
    canvas_crs = None
    extent_rect = None
    active = None
    if iface is not None:
        try:
            canvas = iface.mapCanvas()
            canvas_crs = canvas.mapSettings().destinationCrs()
            extent = canvas.extent()
            extent_rect = {"xmin": extent.xMinimum(), "ymin": extent.yMinimum(),
                           "xmax": extent.xMaximum(), "ymax": extent.yMaximum()}
            active = iface.activeLayer()
        except Exception:  # nosec B110 - optional QGIS context
            pass
    items = []
    for lid, layer in layers:
        node = root.findLayer(lid)
        visible = bool(node.isVisible()) if node is not None else True
        rect = layer_extent_rect(layer, canvas_crs, project)
        items.append({"id": lid, "visible": visible, "selected_count": selected_count(layer),
                      "extent": rect, "_layer": layer})
    ranked = rank_layers(items, active.id() if active is not None else None, extent_rect)
    return [it["_layer"] for it in ranked]


def _vectors_by_geometry(ordered_layers) -> dict:
    groups = {"Polygon": [], "Line": [], "Point": []}
    for layer in ordered_layers:
        if isinstance(layer, QgsVectorLayer):
            name = geometry_name(layer) or ""
            for key in groups:
                if name.startswith(key):
                    groups[key].append(layer)
    return groups


_ID_FIELDS = {"fid", "id", "gid", "oid", "objectid", "ogc_fid", "osm_id", "pk", "rowid", "uid", "uuid"}


def _first_numeric_field(layer) -> str | None:
    """A numeric field worth styling by: never an identifier, which only makes a rainbow."""
    try:
        for field in layer.fields():
            name = field.name()
            if field.isNumeric() and name.lower() not in _ID_FIELDS and not name.lower().endswith("_id"):
                return name
    except Exception:  # nosec B110 - optional QGIS context
        pass
    return None


def build_suggestions() -> list[str]:
    """Three lines from the open project for the empty state."""
    project = QgsProject.instance()
    layers = list(project.mapLayers().values())
    if not layers:
        return [tr("Add an OpenStreetMap basemap"),
                tr("Load a dataset from a file or a URL"),
                tr("What can you do in this project?")]
    ordered = _ranked_project_layers()



    ordered = [lyr for lyr in ordered
               if not isinstance(lyr, QgsVectorLayer)
               or feature_count(lyr, source_kind(lyr)) != 0] or ordered
    groups = _vectors_by_geometry(ordered)
    rasters = [lyr for lyr in ordered if isinstance(lyr, QgsRasterLayer)]
    vectors = [lyr for lyr in ordered if isinstance(lyr, QgsVectorLayer)]
    selected = next((lyr for lyr in vectors if lyr.selectedFeatureCount() > 0), None)
    out: list[str] = []

    styled = next((lyr for lyr in groups["Polygon"] if _first_numeric_field(lyr)), None)
    if styled is not None:
        out.append(tr("Style {layer} by {field}").format(layer=styled.name(), field=_first_numeric_field(styled)))
    elif vectors:
        out.append(tr("Style {layer} with a categorized renderer").format(layer=vectors[0].name()))
    else:
        out.append(tr("Describe the layers in this project"))

    if selected is not None:
        out.append(tr("Clip {layer} to the selection").format(layer=selected.name()))
    elif groups["Line"]:
        out.append(tr("Buffer {layer} by 100 m into a new layer").format(layer=groups["Line"][0].name()))
    elif groups["Point"] and groups["Polygon"]:
        out.append(tr("Count {points} per {polygons}").format(points=groups["Point"][0].name(),
                                                              polygons=groups["Polygon"][0].name()))
    elif rasters and groups["Point"]:
        out.append(tr("Sample the elevation at the selected points"))
    elif rasters and not vectors:
        out.append(tr("Describe the raster bands and their value range"))
    elif vectors:
        out.append(tr("Check the geometry validity of {layer}").format(layer=vectors[0].name()))
    else:
        out.append(tr("Load a vector dataset to work with"))

    if rasters:
        out.append(tr("Detect buildings with AI Segmentation"))
    else:
        out.append(tr("Export a map layout of the current view"))
    return out[:3]




def layer_geometry_label(layer) -> str:
    """``Point``, ``Line``, ``Polygon``, ``Raster``, ``Table``: the muted word in the @ popup and the kind line of a layer card."""

    try:
        if isinstance(layer, QgsVectorLayer):
            if layer.geometryType() == QgsWkbTypes.GeometryType.NullGeometry:
                return tr("Table")
            return geometry_name(layer) or tr("Table")
        if isinstance(layer, QgsRasterLayer):
            return tr("Raster")
    except Exception:  # nosec B110 - optional QGIS context
        pass
    return ""


def mention_candidates() -> list[dict]:
    """[{kind, label, value, detail}] for the @ completion."""






    project = QgsProject.instance()
    out: list[dict] = []
    try:
        ordered = [n.layer() for n in project.layerTreeRoot().findLayers() if n.layer() is not None]
    except Exception:  # nosec B110 - optional QGIS context
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
    except Exception:  # nosec B110 - optional QGIS context
        depths, groups = {}, {}
    for layer in ordered[:MAX_LAYERS]:
        out.append({"kind": "layer", "label": layer.name(), "value": layer.id(),
                    "detail": layer_geometry_label(layer), "depth": depths.get(layer.id(), 0),
                    "group": groups.get(layer.id(), "")})
    out.extend(source_candidates())
    return out


def source_candidates() -> list[dict]:
    """Every connector as an ``@`` candidate, alphabetical."""





    try:
        from ..ui.shared import get_connectors

        rows = get_connectors()
    except Exception:  # noqa: BLE001 - the popup must open with layers alone
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
