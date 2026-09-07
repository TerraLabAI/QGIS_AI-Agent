# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Project and layer-list handlers: context snapshot, layer listing/info, remove and zoom."""


from __future__ import annotations

from qgis.core import QgsCoordinateTransform, QgsFeatureRequest, QgsProject, QgsVectorLayer
from qgis.utils import iface

from ..core import ground
from ..core.policy import get_security_context
from ..core.serialization import dump_json, size_budget
from ._widgets import AI_EDIT_KEYS, AI_SEGMENT_KEYS
from .layer_lookup import _duplicate_layer_names, _find_layer, _geometry_type_name, _layer_not_found_error




_REMOTE_VECTOR_PROVIDERS = ("WFS", "wfs", "arcgisfeatureserver", "oapif")


def _safe_feature_count(layer):
    """featureCount() for a local layer; None ("unknown") for a remote one."""




    if str(layer.providerType() or "") in _REMOTE_VECTOR_PROVIDERS:
        return None
    return layer.featureCount()










_CONTEXT_ALREADY_SENT = (
    "The project CRS, the canvas extent and scale, and the layer list are already in the context "
    "block of this turn: read them there. Call this again with verbose true, or a limit, only when "
    "you need layer ids, layer extents, or more layers than the block lists."
)


def _get_project_context(args: dict) -> dict:
    verbose = bool(args.get("verbose", False))
    raw_limit = args.get("limit")
    try:
        limit = max(int(raw_limit or 40), 1)
    except (TypeError, ValueError):
        limit = 40
    if not verbose and raw_limit is None:
        return _new_since_the_context_block()
    project = QgsProject.instance()
    canvas = iface.mapCanvas()
    project_crs = project.crs()
    canvas_crs = canvas.mapSettings().destinationCrs()

    context = {
        "project": {
            "title": project.title() or "(untitled)",
            "file_path": project.fileName() or "(not saved)",
            "crs": project_crs.authid(),
            "crs_description": project_crs.description(),
            "crs_is_geographic": project_crs.isGeographic(),
            "crs_units": "degrees" if project_crs.isGeographic() else "meters",
        },
        "canvas": {
            "crs": canvas_crs.authid(),
            "crs_is_geographic": canvas_crs.isGeographic(),
            "crs_units": "degrees" if canvas_crs.isGeographic() else "meters",
            "extent": {
                "xmin": canvas.extent().xMinimum(),
                "ymin": canvas.extent().yMinimum(),
                "xmax": canvas.extent().xMaximum(),
                "ymax": canvas.extent().yMaximum(),
            },
            "scale": canvas.scale(),
            "width_px": canvas.width(),
            "height_px": canvas.height(),
        },
    }



    root = project.layerTreeRoot()
    ambiguous = _duplicate_layer_names(project)
    all_layers = list(project.mapLayers().items())
    layers_info = []
    for lid, layer in all_layers[:limit]:
        info = {
            "name": layer.name(),
            "type": "vector" if isinstance(layer, QgsVectorLayer) else "raster",
            "crs": layer.crs().authid(),
        }
        if verbose or layer.name() in ambiguous:
            info["id"] = lid
        node = root.findLayer(lid)
        info["visible"] = node.isVisible() if node is not None else True
        if verbose:
            ext = layer.extent()
            if ext and not ext.isEmpty():
                info["extent"] = {
                    "xmin": ext.xMinimum(), "ymin": ext.yMinimum(),
                    "xmax": ext.xMaximum(), "ymax": ext.yMaximum(),
                }
        if isinstance(layer, QgsVectorLayer):
            fc = _safe_feature_count(layer)
            info["feature_count"] = fc if fc is not None else "unknown"
            info["geometry_type"] = _geometry_type_name(layer)
        layers_info.append(info)
    context["layers"] = layers_info
    context["layer_count"] = len(all_layers)
    if len(all_layers) > len(layers_info):
        context["layers_omitted"] = len(all_layers) - len(layers_info)


    active = iface.activeLayer()
    context["active_layer"] = active.name() if active else None

    context["integrations"] = _project_integrations()
    context["security"] = get_security_context()
    return context


def _project_integrations() -> dict:
    """Whether the AI Edit and AI Segment plugins are installed and ready."""
    integrations: dict = {}
    try:
        import qgis.utils
        for key in AI_SEGMENT_KEYS:
            plugin = qgis.utils.plugins.get(key)
            if plugin:
                model_loaded = hasattr(plugin, "predictor") and plugin.predictor is not None
                integrations["ai_segmentation"] = {"installed": True, "model_loaded": model_loaded,
                                                   "ready": model_loaded}
                break
        if "ai_segmentation" not in integrations:
            integrations["ai_segmentation"] = {"installed": False, "ready": False}

        for key in AI_EDIT_KEYS:
            plugin = qgis.utils.plugins.get(key)
            if plugin:
                integrations["ai_edit"] = {"installed": True, "initialized": hasattr(plugin, "_auth_manager")}
                break
        if "ai_edit" not in integrations:
            integrations["ai_edit"] = {"installed": False}
    except Exception:  # nosec B110 - integration state is optional
        pass
    return integrations


def _new_since_the_context_block() -> dict:
    """The answer to a call with no arguments: only what the turn does not carry."""
    project = QgsProject.instance()
    project_crs = project.crs()
    out = {
        "note": _CONTEXT_ALREADY_SENT,
        "project_crs_description": project_crs.description(),
        "integrations": _project_integrations(),
        "security": get_security_context(),
    }
    try:
        canvas = iface.mapCanvas()
        out["canvas_size_px"] = {"width": canvas.width(), "height": canvas.height()}
    except Exception:  # nosec B110 - no canvas is not a reason to fail the call
        pass
    return out


def _get_project_info(args: dict) -> dict:
    project = QgsProject.instance()
    return {
        "title": project.title() or "(untitled)",
        "file_path": project.fileName() or "(not saved)",
        "crs": project.crs().authid(),
        "layer_count": len(project.mapLayers()),
    }


def _list_layers(args: dict) -> dict:
    verbose = bool(args.get("verbose", False))
    try:
        limit = max(int(args.get("limit", 60) or 60), 1)
    except (TypeError, ValueError):
        limit = 60
    project = QgsProject.instance()
    ambiguous = _duplicate_layer_names(project)
    all_layers = list(project.mapLayers().items())
    result = []
    for lid, layer in all_layers[:limit]:
        info = {
            "name": layer.name(),
            "type": "vector" if isinstance(layer, QgsVectorLayer) else "raster",
            "crs": layer.crs().authid(),
        }
        if verbose or layer.name() in ambiguous:
            info["layer_id"] = lid
        if isinstance(layer, QgsVectorLayer):
            fc = _safe_feature_count(layer)
            info["feature_count"] = fc if fc is not None else "unknown"
            info["geometry_type"] = _geometry_type_name(layer)
        result.append(info)
    out = {"layers": result, "count": len(all_layers)}
    if len(all_layers) > len(result):
        out["layers_omitted"] = len(all_layers) - len(result)
    return out



_SURVEY_FEATURES = 1_500
_SURVEY_FIELDS = 80


_SAMPLE_BUDGET_CHARS = 3_000


def _field_entries(layer, compute_unique: bool) -> tuple[list, dict]:
    """One field entry per field with up to 15 distinct values, and what was left out."""















    countable = [(i, f) for i, f in enumerate(layer.fields()) if f.type() in (2, 4, 10)]





    surveyed = countable[:_SURVEY_FIELDS]
    skipped_fields = {i for i, _ in countable[_SURVEY_FIELDS:]}
    distinct: dict[int, set] = {i: set() for i, _ in surveyed} if compute_unique else {}
    sampled = 0
    if distinct:
        request = QgsFeatureRequest().setFlags(QgsFeatureRequest.Flag.NoGeometry)
        request.setSubsetOfAttributes([i for i, _ in surveyed])
        request.setLimit(_SURVEY_FEATURES + 1)
        open_fields = set(distinct)
        for feature in layer.getFeatures(request):
            sampled += 1
            if sampled > _SURVEY_FEATURES:
                sampled = _SURVEY_FEATURES
                break
            for i in list(open_fields):
                values = distinct[i]
                values.add(feature[i])
                if len(values) > 15:
                    open_fields.discard(i)
            if not open_fields:
                break
    total = _safe_feature_count(layer)
    partial = sampled >= _SURVEY_FEATURES and total is not None and total > _SURVEY_FEATURES

    entries = []
    countable_indexes = {i for i, _ in countable}
    spent = 0
    sampled_fields = 0
    for i, f in enumerate(layer.fields()):
        entry = {"name": f.name(), "type": f.typeName()}
        if i in countable_indexes and compute_unique and i not in skipped_fields and spent < _SAMPLE_BUDGET_CHARS:
            if len(distinct[i]) <= 15:
                entry["unique_values"] = sorted(
                    [v for v in distinct[i] if v is not None], key=lambda x: str(x),
                )
                if partial:
                    entry["unique_values_from_first"] = _SURVEY_FEATURES
            else:
                entry["unique_value_count"] = ">15"
            spent += len(dump_json(entry))
            sampled_fields += 1
        entries.append(entry)



    notes: dict = {}
    countable_total = len(countable)
    if countable_total and sampled_fields < countable_total:
        notes["field_values_sampled"] = sampled_fields
        notes["field_values_note"] = (
            "layer too large for a distinct-value scan" if not compute_unique
            else f"distinct values for the first {sampled_fields} of {countable_total} countable fields; "
            "get_unique_values reads any other field"
        )
    return entries, notes


def _project_ellipsoid() -> str:
    """The project's measurement ellipsoid, or "" when it measures planar."""
    try:
        name = str(QgsProject.instance().ellipsoid() or "").strip()
    except Exception:  # noqa: BLE001 - a note about the work never breaks it
        return ""
    return "" if name.upper() in ("", "NONE") else name


def _get_layer_info(args: dict) -> dict:
    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])

    info = {
        "name": layer.name(),
        "layer_id": layer.id(),
        "type": "vector" if isinstance(layer, QgsVectorLayer) else "raster",
        "crs": layer.crs().authid(),
        "extent": {
            "xmin": layer.extent().xMinimum(),
            "ymin": layer.extent().yMinimum(),
            "xmax": layer.extent().xMaximum(),
            "ymax": layer.extent().yMaximum(),
        },
    }




    scale = ground.layer_metres_per_unit(layer)
    if ground.distorted(scale):
        info["ground_metres_per_unit"] = round(scale, 4)
        info["crs_note"] = (
            f"One {layer.crs().authid()} unit is {scale:.4g} m of ground here. A number in these units is "
            f"wrong by that much: a buffer distance, a Processing distance parameter, a planar distance()."
        )








    ellipsoid = _project_ellipsoid()
    if ellipsoid:
        info["measure_note"] = (
            f"$area, $perimeter and $length are measured on the {ellipsoid} ellipsoid and come back in "
            f"ground square metres and metres whatever this layer's CRS is. Reprojecting in order to "
            f"measure changes nothing and costs a layer: add_field with an expression writes the value "
            f"onto this layer in place."
        )
    else:



        ratio = ground.area_expression_error(layer)
        if ground.area_distorted(ratio):
            info["area_expression_ratio"] = round(ratio, 4)
            info["measure_note"] = (
                f"This project measures planar (Project Properties > General, ellipsoid NONE), so a bare "
                f"$area here answers {ratio:.3g} times the true ground area. add_field measures $area, "
                f"$perimeter and $length on the WGS84 ellipsoid for you and writes ground metres onto "
                f"this layer, so do not reproject in order to measure: a reprojected copy leaves "
                f"'{layer.name()}' without the field the user asked for. run_processing's field "
                f"calculator has no such correction and no such layer."
            )

    if isinstance(layer, QgsVectorLayer):
        fc = _safe_feature_count(layer)
        info["feature_count"] = fc if fc is not None else "unknown"



        compute_unique = fc is not None and 0 <= fc <= 100_000
        info["fields"], field_notes = _field_entries(layer, compute_unique)
        info.update(field_notes)

        def _truncate(v):
            if isinstance(v, str) and len(v) > 80:
                return v[:80] + "…"
            return v

        features = []
        for feat in layer.getFeatures(QgsFeatureRequest().setLimit(5)):
            features.append({f.name(): _truncate(feat[f.name()]) for f in layer.fields()})


        features, omitted = size_budget(features, 4_000)
        info["sample_features"] = features
        if omitted:
            info["sample_omitted"] = omitted
    else:
        try:
            info["band_count"] = layer.bandCount()
        except Exception:  # noqa: BLE001  # nosec B110 - a raster with no band count is left without one
            pass
        info.update(ground.pixel_facts(layer))

    return info


def _remove_layer(args: dict) -> dict:
    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])





    name, layer_id = layer.name(), layer.id()
    QgsProject.instance().removeMapLayer(layer_id)
    return {"removed": name, "layer_id": layer_id, "asked_for": args["layer_name"]}


def _zoom_to_layer(args: dict) -> dict:
    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])

    canvas = iface.mapCanvas()
    extent = layer.extent()
    layer_crs = layer.crs()
    canvas_crs = canvas.mapSettings().destinationCrs()
    if layer_crs.isValid() and canvas_crs.isValid() and layer_crs != canvas_crs:
        transform = QgsCoordinateTransform(layer_crs, canvas_crs, QgsProject.instance())
        extent = transform.transformBoundingBox(extent)
    canvas.setExtent(extent)
    canvas.refresh()
    ext = canvas.extent()
    return {
        "zoomed_to": layer.name(),
        "layer_id": layer.id(),
        "canvas_crs": canvas.mapSettings().destinationCrs().authid(),
        "scale": canvas.scale(),
        "extent": {"xmin": ext.xMinimum(), "ymin": ext.yMinimum(), "xmax": ext.xMaximum(), "ymax": ext.yMaximum()},
    }
