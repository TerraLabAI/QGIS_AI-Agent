# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later









from __future__ import annotations

import os

import qgis.utils
from qgis.core import (
    QgsApplication,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication

from . import ground, tuning



from .context_history import _history, _recent_log_problems, _recent_processing  # noqa: F401
from .context_layers import (
    MAX_CARD_FIELDS,
    _cap,
    crs_units,
    degree_values,  # noqa: F401
    duplicate_names,
    extent_size,  # noqa: F401
    feature_count,  # noqa: F401
    geometry_name,
    layer_extent_rect,
    layer_filter,
    layer_kind,  # noqa: F401
    layer_record,
    project_measure,
    rich_card,
    selected_count,
    source_kind,  # noqa: F401
    view_area_km2,
    view_bbox_4326,
)
from .layer_order import positions as tree_positions
from .layer_rank import FLOOR_ORDER, fields_named_in, named_in, offset_from_view, rank_layers, view_of
from .layer_rank import detailed_ids as choose_detailed
from .qt_compat import qgis_iface


def _ranking() -> tuple[tuple, frozenset | None]:


    rules = tuning.service_doc("context_rules") or {}
    order = tuple(rules.get("rank") or ()) or FLOOR_ORDER
    words = rules.get("generic_fields")
    return order, (frozenset(words) if isinstance(words, list) else None)


MAX_LAYERS = 200


MAX_CHIP_VALUE_CHARS = 600
MAX_DETAILED_LAYERS = 25
RICH_LAYER_COUNT = 8
MAX_MORE_LAYER_NAMES = 15



CHIP_KINDS = ("layer", "selection", "extent", "field", "layout", "file", "source", "plugin")
AI_EDIT_FOLDERS = ("QGIS_AI-Edit-Team", "QGIS_AI-Edit", "ai_edit", "AI_Edit")
AI_SEGMENTATION_FOLDERS = ("QGIS_AI-Segmentation-Team", "QGIS_AI-Segmentation",
                           "QGIS_AI_Segmentation_Team", "AI_Segmentation", "ai_segmentation")
QMS_FOLDERS = ("quick_map_services",)


def tr(text: str) -> str:
    return QCoreApplication.translate("AIAgentContext", text)


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


def ai_segmentation_panel() -> dict | None:








    plugins = getattr(qgis.utils, "plugins", {}) or {}
    plugin = next((plugins[name] for name in AI_SEGMENTATION_FOLDERS if name in plugins), None)
    reader = getattr(getattr(plugin, "mcp_api", None), "get_interactive_state", None)
    if not callable(reader):
        return None
    try:
        state = reader()
    except Exception:  # nosec B110
        return None
    if not isinstance(state, dict):
        return None
    out = {}
    if state.get("drawing") in ("zone", "example"):
        out["drawing"] = state["drawing"]
    if state.get("review_pending"):
        out["review_open"] = True
    if state.get("running"):
        out["running"] = True
    if state.get("zone_wkt"):
        out["zone_set"] = True
    return out or None


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


def _slash_case(path: str) -> str:

    return os.path.normcase(path).replace("\\", "/").rstrip("/")


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


def _canvas_facts(iface, project) -> tuple:






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
    return canvas_block, canvas_scale, canvas_crs, extent_rect, active, active_name


def _layer_items(all_layers: list, root, ambiguous: set, places: dict, tree_ids: set, canvas_crs, project,
                 thread_id: str) -> list[dict]:






    items = []


    try:
        nodes = {node.layerId(): node for node in root.findLayers()}
    except Exception:  # noqa: BLE001
        nodes = {}
    for lid, layer in all_layers:
        try:
            record = layer_record(layer, nodes.get(lid), ambiguous)
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
    return items


def _detail_choice(items: list, chips, tree_ids: set, text: str, active_id, extent_rect) -> tuple:







    chip_ids = _chip_layer_ids(chips) | tree_ids
    order, generic = _ranking()
    named = named_in(text, [str(it["record"].get("name") or "") for it in items]) if text else set()
    for index, it in enumerate(items):
        it["named"] = (index in named or it["id"] in chip_ids
                       or (bool(text) and fields_named_in(text, _field_names(it["layer"])[:MAX_CARD_FIELDS * 2],
                                                          generic)))
    ranked = rank_layers(
        [{"id": it["id"], "visible": it["record"].get("visible"),
          "selected_count": it["record"].get("selected_count", 0), "extent": it["extent"],
          "named": it["named"], "by_agent": it["by_agent"]}
         for it in items],
        active_id,
        extent_rect,
        order,
    )
    order = [r["id"] for r in ranked]
    detailed = _cap("max_detailed_layers", MAX_DETAILED_LAYERS)
    detailed_ids = order[:detailed]
    rest_ids = order[detailed:]
    listed = set(detailed_ids)
    rich_ids = set(choose_detailed([r for r in ranked if r["id"] in listed], active_id,
                                   _cap("max_rich_layers", RICH_LAYER_COUNT),
                                   _cap("min_rich_layers", MIN_RICH_LAYERS)))
    return detailed_ids, rest_ids, rich_ids


def _detailed_records(detailed_ids: list, by_id: dict, rich_ids: set, active, canvas_crs, project,
                      extent_rect, canvas_box) -> list[dict]:

    layers_out = []
    for lid in detailed_ids:
        it = by_id[lid]
        record = dict(it["record"])
        if lid in rich_ids and it["layer"] is not None:
            is_active = active is not None and lid == active.id()
            record.update(rich_card(it["layer"], project, is_active))
        if it["by_agent"]:
            record["by_agent"] = True
        if record.get("source_kind") not in _NO_VIEW_KINDS:
            _place_against_view(record, it["extent"], extent_rect, canvas_box, canvas_crs, project)
        layers_out.append(record)




    layers_out.sort(key=lambda record: record.get("draw_order") or 10 ** 9)
    return layers_out


def _add_machine(context: dict) -> None:




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


def build_context(chips: list | None = None, text: str = "", thread_id: str = "") -> dict:






    project = QgsProject.instance()
    iface = qgis_iface()
    project_crs = project.crs()
    root = project.layerTreeRoot()
    all_layers_full = list(project.mapLayers().items())

    canvas_block, canvas_scale, canvas_crs, extent_rect, active, active_name = _canvas_facts(iface, project)





    ambiguous = duplicate_names([layer for _lid, layer in all_layers_full])




    places = tree_positions(root)
    tree_ids = _tree_selected_ids(iface)


    all_layers = _capped_layers(all_layers_full, places,
                                active.id() if active is not None else None)
    items = _layer_items(all_layers, root, ambiguous, places, tree_ids, canvas_crs, project, thread_id)

    active_id = active.id() if active is not None else None
    detailed_ids, rest_ids, rich_ids = _detail_choice(items, chips, tree_ids, text, active_id, extent_rect)
    by_id = {it["id"]: it for it in items}
    layers_out = _detailed_records(detailed_ids, by_id, rich_ids, active, canvas_crs, project, extent_rect,
                                   canvas_block.get("extent_4326"))

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
        "active_layer_id": active_id,
        "plugins": plugins_present(),
        "chips": clean_chips(chips),

        "layer_detail": "relevant",
    }
    panel = ai_segmentation_panel()
    if panel:
        context["ai_segmentation_panel"] = panel
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
    _add_machine(context)
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
        from .served_state import get_connectors

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
