# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



from __future__ import annotations

import os

from qgis.core import QgsCoordinateTransform, QgsCsException, QgsFeatureRequest, QgsProject, QgsVectorLayer
from qgis.utils import iface

from ..core import ground, tuning
from ..core.crs_ref import crs_ref
from ..core.feature_requests import feature_request
from ..core.follow import hold_view
from ..core.layer_order import is_remote_vector as _is_remote_vector
from ..core.layer_order import is_web_service
from ..core.policy import get_security_context
from ..core.serialization import dump_json, size_budget
from ..core.snapshot import layer_file_path
from ..core.vsi import streamed_in_place
from ._widgets import AI_EDIT_KEYS, sibling_plugin
from .layer_lookup import _duplicate_layer_names, _find_layer, _geometry_type_name, _layer_not_found_error







def _safe_feature_count(layer):






    if not _is_remote_vector(layer):
        return layer.featureCount()
    if (str(layer.providerType() or "").casefold() == "ogr" and streamed_in_place(layer)
            and not is_web_service(layer)):
        count = int(layer.featureCount())
        return count if count >= 0 else None
    return None


def _extent_omission():
    return {"extent_available": False,
            "extent_note": "Remote extent was not queried: QGIS may fetch provider data on the main thread."}


def _extent_block(extent, crs) -> dict:














    from ..core.context import crs_units

    block = {"xmin": extent.xMinimum(), "ymin": extent.yMinimum(),
             "xmax": extent.xMaximum(), "ymax": extent.yMaximum(),
             "crs": crs_ref(crs)}
    if crs is None or not crs.isValid():
        return block
    try:
        block["units"] = crs_units(crs)

        digits = 6 if crs.isGeographic() else 2
        width, height = extent.width(), extent.height()
        block["width"] = round(width, digits)
        block["height"] = round(height, digits)
        centre = extent.center()
        scale = ground.metres_per_unit(crs, centre.x(), centre.y())
    except Exception:  # noqa: BLE001
        return block
    if ground.distorted(scale):
        block["ground_width_m"] = round(width * scale, 1)
        block["ground_height_m"] = round(height * scale, 1)
    return block


def _layer_kind(layer) -> str:






    from ..core.context import layer_kind

    return layer_kind(layer)










_CONTEXT_ALREADY_SENT = (
    "The project CRS, the canvas extent and scale, and the layer list are already in the context "
    "block of this turn. verbose true, or a limit, adds layer ids, layer extents, or more layers "
    "than the block lists."
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
    from ..core.context import crs_units

    context = {
        "project": {
            "title": project.title() or "(untitled)",
            "file_path": project.fileName() or "(not saved)",
            "crs": project_crs.authid(),
            "crs_description": project_crs.description(),
            "crs_is_geographic": project_crs.isGeographic(),
            "crs_units": crs_units(project_crs),
        },
        "canvas": {
            "crs": canvas_crs.authid(),
            "crs_is_geographic": canvas_crs.isGeographic(),
            "crs_units": crs_units(canvas_crs),



            "extent": _extent_block(canvas.extent(), canvas_crs),
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
            "type": _layer_kind(layer),
            "crs": crs_ref(layer.crs()),
        }
        if verbose or layer.name() in ambiguous:
            info["id"] = lid
        node = root.findLayer(lid)
        info["visible"] = node.isVisible() if node is not None else True
        if verbose:
            if _is_remote_vector(layer):
                info.update(_extent_omission())
            else:
                ext = layer.extent()
                if ext and not ext.isEmpty():
                    info["extent"] = _extent_block(ext, layer.crs())
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

    integrations: dict = {}
    try:
        from .integration_tools import aiseg_presence
        presence = aiseg_presence()
        plugin = presence["plugin"]
        if plugin is not None:
            model_loaded = hasattr(plugin, "predictor") and plugin.predictor is not None
            integrations["ai_segmentation"] = {"installed": True, "model_loaded": model_loaded,
                                               "ready": model_loaded}
        elif presence["state"] == "absent":
            integrations["ai_segmentation"] = {"installed": False, "ready": False}
        else:

            integrations["ai_segmentation"] = {"installed": True, "ready": False, "state": presence["state"]}

        _, plugin = sibling_plugin(AI_EDIT_KEYS)
        if plugin:
            integrations["ai_edit"] = {"installed": True, "initialized": hasattr(plugin, "_auth_manager")}
        else:
            integrations["ai_edit"] = {"installed": False}
    except Exception:  # nosec B110
        pass
    return integrations


def _new_since_the_context_block() -> dict:

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
    except Exception:  # nosec B110
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





_WHERE_BUDGET_CHARS = 12_000
_SOURCE_CHARS = 300


def _origin(address: str) -> str:



    import urllib.parse

    text = str(address or "").strip()
    while text.startswith("/vsi"):
        parts = text.split("/", 2)
        if len(parts) < 3:
            return ""
        text = parts[2]
    try:
        url = urllib.parse.urlsplit(text)
        host, port = url.hostname, url.port
    except ValueError:
        return ""
    if not url.scheme or not host:
        return ""
    return f"{url.scheme.lower()}://{host}" + (f":{port}" if port else "")


def _layer_source(layer) -> str:



    from qgis.core import QgsDataSourceUri, QgsProviderRegistry

    provider = layer.providerType() or ""
    source = layer.source() or ""
    try:
        parts = QgsProviderRegistry.instance().decodeUri(provider, source) or {}
    except Exception:  # noqa: BLE001
        parts = {}
    path = layer_file_path(layer)
    if path:
        name = parts.get("layerName")
        return f"{path}|layername={name}"[:_SOURCE_CHARS] if name else path
    if provider in ("memory", "virtual"):
        return provider


    encoded = QgsDataSourceUri()
    encoded.setEncodedUri(source)
    plain = QgsDataSourceUri(source[3:] if source.startswith("PG:") else source)
    for address in (parts.get("url"), parts.get("path"), encoded.param("url"), plain.param("url"),
                    source[3:] if source.startswith("PG:") else None):
        origin = _origin(address) if isinstance(address, str) else ""
        if origin:
            return f"{provider}: {origin}"
    host = parts.get("host") or plain.host()
    return f"{provider}: {host}" if host else provider


def _layer_extent(layer) -> list | None:




    if _is_remote_vector(layer):
        return None
    try:
        box = layer.extent()
        if box.isNull():
            return None
        digits = 6 if layer.crs().isGeographic() else 2
        return [round(v, digits) for v in (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum())]
    except Exception:  # noqa: BLE001
        return None


def _list_layers(args: dict) -> dict:
    verbose = bool(args.get("verbose", False))
    try:
        limit = max(int(args.get("limit", 60) or 60), 1)
    except (TypeError, ValueError):
        limit = 60
    project = QgsProject.instance()
    ambiguous = _duplicate_layer_names(project)





    root = project.layerTreeRoot()
    layers = project.mapLayers()
    ordered = [layer for layer in root.layerOrder() if layer is not None and layer.id() in layers]
    placed = {layer.id() for layer in ordered}
    all_layers = [(layer.id(), layer) for layer in ordered]
    all_layers += [(lid, layer) for lid, layer in layers.items() if lid not in placed]
    result = []
    where_chars = where_layers = 0
    for position, (lid, layer) in enumerate(all_layers[:limit], start=1):
        node = root.findLayer(lid)
        info = {
            "name": layer.name(),
            "type": _layer_kind(layer),
            "crs": crs_ref(layer.crs()),
        }
        if node is not None:
            info["position"] = position
            info["visible"] = node.isVisible()
        if hasattr(layer, "opacity") and layer.opacity() < 1:
            info["opacity"] = round(layer.opacity(), 2)
        if verbose or layer.name() in ambiguous:
            info["layer_id"] = lid
        if isinstance(layer, QgsVectorLayer):
            fc = _safe_feature_count(layer)
            info["feature_count"] = fc if fc is not None else "unknown"
            info["geometry_type"] = _geometry_type_name(layer)
        if verbose and where_chars < _WHERE_BUDGET_CHARS:
            where = {"extent": _layer_extent(layer)}
            try:
                where["source"] = _layer_source(layer)
            except Exception:  # noqa: BLE001
                where["source"] = None
            where = {k: v for k, v in where.items() if v}
            where_chars += len(dump_json(where))
            info.update(where)
            where_layers += 1
        result.append(info)
    out = {"layers": result, "count": len(all_layers)}
    if verbose and where_layers < len(result):
        out["extent_and_source_note"] = (f"extent and source for the first {where_layers} layers; "
                                         f"the rest passed the {_WHERE_BUDGET_CHARS} character budget")
    if len(all_layers) > len(result):
        out["layers_omitted"] = len(all_layers) - len(result)
    return out






_SURVEY_FEATURES = 1_500
_SURVEY_FIELDS = 80


_SAMPLE_BUDGET_CHARS = 3_000


def _field_entries(layer, compute_unique: bool) -> tuple[list, dict]:
















    countable = [(i, f) for i, f in enumerate(layer.fields()) if f.type() in (2, 4, 10)]





    survey_rows = tuning.ceiling("project_survey_features", _SURVEY_FEATURES, 100)
    survey_fields = tuning.ceiling("project_survey_fields", _SURVEY_FIELDS, 10)
    sample_chars = tuning.ceiling("project_sample_budget_chars", _SAMPLE_BUDGET_CHARS, 500)
    surveyed = countable[:survey_fields]
    skipped_fields = {i for i, _ in countable[survey_fields:]}
    distinct: dict[int, set] = {i: set() for i, _ in surveyed} if compute_unique else {}
    sampled = 0
    if distinct:
        request = QgsFeatureRequest().setFlags(QgsFeatureRequest.Flag.NoGeometry)
        request.setSubsetOfAttributes([i for i, _ in surveyed])
        request.setLimit(survey_rows + 1)
        open_fields = set(distinct)
        for feature in layer.getFeatures(request):
            sampled += 1
            if sampled > survey_rows:
                sampled = survey_rows
                break
            for i in list(open_fields):
                values = distinct[i]
                values.add(feature[i])
                if len(values) > 15:
                    open_fields.discard(i)
            if not open_fields:
                break
    total = _safe_feature_count(layer)
    partial = sampled >= survey_rows and total is not None and total > survey_rows

    entries = []
    countable_indexes = {i for i, _ in countable}
    spent = 0
    sampled_fields = 0
    for i, f in enumerate(layer.fields()):
        entry = {"name": f.name(), "type": f.typeName()}
        if i in countable_indexes and compute_unique and i not in skipped_fields and spent < sample_chars:
            if len(distinct[i]) <= 15:
                entry["unique_values"] = sorted(
                    [v for v in distinct[i] if v is not None], key=str,
                )
                if partial:
                    entry["unique_values_from_first"] = survey_rows
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

    try:
        name = str(QgsProject.instance().ellipsoid() or "").strip()
    except Exception:  # noqa: BLE001
        return ""
    return "" if name.upper() in ("", "NONE") else name





_UNFILTERED_COUNT_MAX_BYTES = 20_000_000
_COUNT_KEPT_SUFFIXES = (".gpkg", ".shp", ".fgb", ".sqlite")
_UNFILTERED_COUNT_PROVIDERS = frozenset({"ogr", "delimitedtext", "spatialite"})


def filter_facts(layer) -> dict:









    from ..core.context import layer_filter

    subset = layer_filter(layer)
    if not subset:
        return {}
    out: dict = {"filter": subset}
    total = _unfiltered_count(layer)
    if total is not None:
        out["source_feature_count"] = total
        out["filter_note"] = f"layer.source() keeps this filter as |subset=; the source holds {total} rows."
    return out


def _unfiltered_count(layer) -> int | None:

    try:
        from qgis.core import QgsDataProvider, QgsProviderRegistry

        provider = layer.providerType()
        if provider not in _UNFILTERED_COUNT_PROVIDERS:
            return None
        registry = QgsProviderRegistry.instance()
        parts = dict(registry.decodeUri(provider, layer.source()) or {})
        path = str(parts.get("path") or "")
        if not parts.pop("subset", None) or not os.path.isfile(path):
            return None
        if not path.lower().endswith(_COUNT_KEPT_SUFFIXES) and os.path.getsize(path) > _UNFILTERED_COUNT_MAX_BYTES:
            return None
        source = registry.createProvider(provider, registry.encodeUri(provider, parts),
                                         QgsDataProvider.ProviderOptions())
        if source is None or not source.isValid():
            return None
        count = int(source.featureCount())
    except Exception:  # noqa: BLE001
        return None
    return count if count >= 0 else None




_VERTEX_SAMPLE = 5


def _sampled_vertices(layer) -> dict | None:

    counts = []
    try:
        for feature in layer.getFeatures(feature_request(attributes=[], limit=_VERTEX_SAMPLE)):
            geometry = feature.geometry()
            if geometry is not None and not geometry.isNull():
                counts.append(geometry.constGet().nCoordinates())
    except Exception:  # noqa: BLE001
        return None
    if not counts:
        return None
    return {"features": len(counts), "mean": round(sum(counts) / len(counts)), "max": max(counts)}


def _get_layer_info(args: dict) -> dict:
    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])





    detail = str(args.get("detail") or "summary").strip().casefold()
    if detail not in ("summary", "full"):
        return {"_error": "detail must be 'summary' or 'full'.", "code": "INVALID_ARGS",
                "suggestion": "The default summary covers metadata; detail='full' adds samples."}
    full_detail = detail == "full"
    remote = _is_remote_vector(layer)




    samples = full_detail and not remote

    info = {
        "name": layer.name(),
        "layer_id": layer.id(),
        "type": _layer_kind(layer),
        "crs": crs_ref(layer.crs()),
    }
    skip_remote_extent = not full_detail and remote
    if skip_remote_extent:
        info.update(_extent_omission())
    else:
        info["extent"] = _extent_block(layer.extent(), layer.crs())
        if not remote:


            from .crs_landing import layer_check
            info.update(layer_check(layer))
            from ..core.context import degree_values
            degrees = degree_values(layer.crs(), layer.extent())
            if degrees:
                info["degree_values"] = degrees


    source_path = layer_file_path(layer)
    if source_path:
        info["source"] = source_path


        try:
            info["source_bytes"] = os.path.getsize(source_path.split("|", 1)[0])
        except (OSError, ValueError):
            pass
    if isinstance(layer, QgsVectorLayer) and not remote:
        vertices = _sampled_vertices(layer)
        if vertices is not None:
            info["vertices_per_feature_sampled"] = vertices




    scale = None if skip_remote_extent else ground.layer_metres_per_unit(layer)
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



        ratio = ground.area_expression_error(layer) if samples else None
        if ground.area_distorted(ratio):
            info["area_expression_ratio"] = round(ratio, 4)
            info["measure_note"] = (
                f"This project measures planar (Project Properties > General, ellipsoid NONE), so a bare "
                f"$area here answers {ratio:.3g} times the true ground area. add_field measures $area, "
                f"$perimeter and $length on the WGS84 ellipsoid for you and writes ground metres onto "
                f"this layer, so a reprojected copy leaves "
                f"'{layer.name()}' without the field the user asked for. run_processing measures on the "
                f"same ellipsoid, but writes a new layer."
            )

    if isinstance(layer, QgsVectorLayer):
        fc = _safe_feature_count(layer)
        info["feature_count"] = fc if fc is not None else "unknown"
        info.update(filter_facts(layer))



        if not samples:



            info["fields"] = [{"name": field.name(), "type": field.typeName()}
                               for field in layer.fields()]
            skipped = ["field_values", "sample_features", "area_measurement"]
            if fc is None:
                skipped.append("feature_count")
            if skip_remote_extent:
                skipped.extend(("extent", "ground_metres_per_unit"))
            info["details_skipped"] = skipped
            info["details_note"] = (
                "Fast metadata summary: distinct field values and feature samples were not read. "
                + ("The remote provider's feature count was not queried. " if fc is None else "")
                + ("Its rows come over the network, so detail='full' reads none either: get_features reads "
                   "them off the main thread." if remote else
                   "get_layer_info with detail='full', or get_features/get_field_statistics, reads them.")
            )
            return info

        compute_unique = fc is not None and 0 <= fc <= 100_000
        info["fields"], field_notes = _field_entries(layer, compute_unique)
        info.update(field_notes)

        def _truncate(v):
            if isinstance(v, str) and len(v) > 80:
                return v[:80] + "…"
            return v

        features = []
        for feat in layer.getFeatures(feature_request(geometry=False, limit=5)):
            features.append({f.name(): _truncate(feat[f.name()]) for f in layer.fields()})


        features, omitted = size_budget(features, 4_000)
        info["sample_features"] = features
        if omitted:
            info["sample_omitted"] = omitted
    else:
        try:
            info["band_count"] = layer.bandCount()
        except Exception:  # noqa: BLE001  # nosec B110
            pass
        info.update(ground.pixel_facts(layer))
        from .query_tools import _remote_raster

        if _remote_raster(layer):

            info["provider"] = layer.providerType()
            info["remote"] = ("Its pixels live on the service: every pixel or block read is a request over "
                              "the network, and QGIS waits for the answer.")

    return info


def _remove_layer(args: dict) -> dict:
    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])





    from .processing_run import remove_layers

    name, layer_id = layer.name(), layer.id()
    remove_layers([layer])
    return {"removed": name, "layer_id": layer_id, "asked_for": args["layer_name"]}


def _zoom_to_layer(args: dict) -> dict:
    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])

    import math

    canvas = iface.mapCanvas()
    extent = layer.extent()



    corners = (extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum())
    if extent.isNull() or not all(math.isfinite(value) for value in corners):
        return {"_error": (f"Layer {layer.name()!r} has no extent to zoom to: it holds no feature with a "
                           f"geometry. The view was not moved."),
                "code": "INVALID_ARGS",
                "suggestion": "get_layer_info shows feature_count."}
    layer_crs = layer.crs()
    canvas_crs = canvas.mapSettings().destinationCrs()
    framed = True
    if layer_crs.isValid() and canvas_crs.isValid() and layer_crs != canvas_crs:
        try:
            transform = QgsCoordinateTransform(layer_crs, canvas_crs, QgsProject.instance())
            extent = transform.transformBoundingBox(extent)
        except QgsCsException:


            framed = False
    if framed:
        canvas.setExtent(extent)
        canvas.refresh()
    reprojected = hold_view(canvas, prefer=layer_crs, target=(layer.extent(), layer_crs))
    if not framed and not reprojected:
        return {"_error": (f"Layer {layer.name()!r} cannot be drawn in the canvas CRS "
                           f"{canvas_crs.authid()}. The view was not moved."),
                "code": "INVALID_ARGS",
                "suggestion": "The zoom works once the project CRS is the layer's (set_project_crs)."}
    canvas_crs = canvas.mapSettings().destinationCrs()
    ext = canvas.extent()
    return {
        **({"project_crs_changed": reprojected} if reprojected else {}),
        "zoomed_to": layer.name(),
        "layer_id": layer.id(),
        "canvas_crs": canvas.mapSettings().destinationCrs().authid(),


        **({"layer_crs": crs_ref(layer_crs)} if layer_crs.isValid() and layer_crs != canvas_crs else {}),
        "scale": canvas.scale(),



        "extent": _extent_block(ext, canvas.mapSettings().destinationCrs()),
    }
