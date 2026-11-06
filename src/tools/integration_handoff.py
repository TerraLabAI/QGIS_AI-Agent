# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





from __future__ import annotations

from qgis.core import QgsProject

from ..core import siblings, zone_of_interest
from ..core.tool_registry import tool_error
from . import sibling_setup, zone_facts
from .adapters.ai_edit_access import ACCESS
from .handoff_reads import (
    HandoffRefused,
    _aiseg_raster_layer,
    _aiseg_zone_wkt,
    edit_prompt,
    raster_layer,
    refusal,
    segmentation_arguments,
)

_EDIT_SETTINGS = ("index", "template_id", "resolution", "reference_layers", "markup_wkt")


def _edit_settings(args):

    return any(args.get(name) not in (None, "", []) for name in _EDIT_SETTINGS)


def prepare(which, args):

    presence = sibling_setup.presence(sibling_setup.sibling_of(which))
    plugin = presence["plugin"]
    if plugin is None:
        return sibling_setup.not_running(which, presence)
    api = getattr(plugin, "mcp_api", None)
    fn = getattr(api, "prepare_interactive", None)
    if not callable(fn):
        return tool_error("This plugin version has no guided map preparation.", "PLUGIN_OUTDATED",
                          hint="sibling_setup_action", which=which)
    try:
        kwargs = {"interaction": args.get("interaction") or "review"}
        source = args.get("layer_name")
        if source:
            kwargs["layer_name"] = raster_layer(source).id()
        field = "prompt" if which == "ai_edit" else "object_class"
        if field in args:
            kwargs[field] = args[field]
        if args.get("use_zone") or args.get("bbox") is not None or args.get("use_canvas_extent") \
                or args.get("zone_wkt"):
            if args.get("use_zone"):
                if zone_of_interest.read_zone() is None:
                    raise HandoffRefused("The project has no area of interest.", "zone_of_interest_missing")
            if which == "ai_segment":
                kwargs["zone_wkt"] = _aiseg_zone_wkt(plugin, args)
            else:
                if args.get("bbox") is not None and zone_facts.unknown_crs(args):
                    raise ValueError(f"crs '{zone_facts.unknown_crs(args)}' is not a CRS QGIS knows.")
                geometry = zone_facts.zone_from_args(args)
                if geometry is None:
                    raise ValueError("The requested zone could not be read.")
                if not ACCESS._holds_zone(geometry.boundingBox(), geometry):


                    kwargs["zone_wkt"] = geometry.asWkt()
            if which == "ai_segment" and not kwargs.get("zone_wkt"):
                raise ValueError("The requested zone could not be expressed in the image CRS.")
        settings = which == "ai_edit" and _edit_settings(args)
        result = fn(**kwargs)
        if isinstance(result, dict) and not result.get("_error"):
            result["inference_started"] = False
            result["panel_opened"] = True
            if settings:

                applied = ACCESS.apply_panel_settings(args)
                result["settings_applied"] = applied.get("applied", {})
                if applied.get("failed") or applied.get("_error"):
                    result["settings_failed"] = applied.get("failed") or {"all": applied["_error"]}
        return result
    except (ValueError, RuntimeError, TypeError) as exc:
        return refusal(exc)


def _api_state(api, method):

    reader = getattr(api, method, None)
    if not callable(reader):
        return {}
    value = reader()
    if not isinstance(value, dict) or value.get("_error"):
        raise ValueError("The imagery plugin could not read its current " + method + " state.")
    return value


def _markup_digest(plugin, state):

    import hashlib
    import json

    markup = state.get("markup") or {}
    if markup.get("content_digest"):
        return markup["content_digest"]



    manager = getattr(plugin, "_markup_manager", None)
    reader = getattr(manager, "layer", None)
    layer = reader() if callable(reader) else None
    if layer is None:
        return None
    digest = hashlib.sha256()
    rows = [(feature.id(), bytes(feature.geometry().asWkb()).hex(), feature.attributes())
            for feature in layer.getFeatures()]
    for row in sorted(rows, key=lambda item: item[0]):
        digest.update(json.dumps(row, ensure_ascii=False, default=str).encode("utf-8"))
    return digest.hexdigest()


def spending_inputs(label, args, geometry):






    import json
    import math

    edit = label == siblings.AI_EDIT.label
    _, plugin = siblings.find(siblings.AI_EDIT if edit else siblings.AI_SEGMENTATION)
    api = getattr(plugin, "mcp_api", None)
    state = _api_state(api, "get_interactive_state")
    if state.get("drawing"):
        raise HandoffRefused("A drawing is still open in the native panel.", "native_drawing_open")
    wkt = geometry.asWkt() if callable(getattr(geometry, "asWkt", None)) else None
    crs = zone_facts._canvas_crs()
    out = {"zone_wkt": wkt, "zone_crs": crs.authid() or crs.toWkt()}
    if edit:
        source = state.get("input_layer") or _api_state(api, "get_input_layer")
        wanted = args.get("layer_name")
        out["source_id"] = raster_layer(wanted).id() if wanted else source.get("layer_id")
        if not out["source_id"] and plugin is not None:
            dock = ACCESS.dock(plugin)
            reader = getattr(dock, "selected_input_layer", None)
            layer = reader() if callable(reader) else None
            out["source_id"] = layer.id() if layer is not None else None
        resolutions = _api_state(api, "get_resolutions") if api is not None else {}
        if not resolutions and plugin is not None:
            resolutions = ACCESS.resolutions()
        resolution = args.get("resolution") or resolutions.get("current")


        offered = resolutions.get("allowed") or resolutions.get("resolutions")
        if args.get("resolution") and isinstance(offered, list) and offered and resolution not in offered:
            raise HandoffRefused(f"AI Edit does not run resolution '{resolution}' for this account; it runs "
                                 f"{', '.join(map(str, offered))}.", "ai_edit_resolution_unavailable",
                                 resolutions=list(offered))
        out["resolution"] = resolution
        price = (resolutions.get("credit_costs") or {}).get(resolution)
        out["generation_credits"] = (price if isinstance(price, (int, float)) and not isinstance(price, bool)
                                     and math.isfinite(price) and price >= 0 else None)
        out["prompt"] = edit_prompt(args, state)
        out["panel_prompt"] = state.get("prompt")
        out["template_id"] = args.get("template_id")
        versions = _api_state(api, "list_versions")
        selected = versions.get("selected_index")
        out["selected_version"] = {"index": selected, "versions": [
            {key: row.get(key) for key in ("index", "layer_id", "request_id")}
            for row in versions.get("versions") or [] if row.get("index") == selected]}
        references = _api_state(api, "list_references")
        out["references"] = [{key: row.get(key) for key in ("id", "kind", "note", "size_bytes")}
                             for row in references.get("references") or []]
        out["reference_layers"] = []
        for name in args.get("reference_layers") or []:
            layer = QgsProject.instance().mapLayer(name)
            matches = QgsProject.instance().mapLayersByName(name) if layer is None else [layer]
            out["reference_layers"].append(matches[0].id() if len(matches) == 1 else name)
        out["markup_digest"] = _markup_digest(plugin, state)
    else:
        effective = segmentation_arguments(plugin, args)
        layer = _aiseg_raster_layer(plugin, effective.get("layer_name"))
        out["source_id"] = layer.id() if layer is not None else None
        out["object_class"] = str(effective.get("object_class") or "").strip()
        out["exemplars"] = effective.get("exemplars") or []
        out["detail"] = effective.get("detail")
        dock = getattr(plugin, "dock_widget", None)
        reader = getattr(dock, "get_auto_confidence", None)
        confidence = effective.get("confidence")
        out["confidence"] = (max(0.05, min(0.95, float(confidence))) if confidence is not None
                             else reader() if callable(reader) else None)
        reader = getattr(plugin, "_auto_review_preset", None)
        refinement = reader() if callable(reader) else {}
        out["refine"] = {**(refinement or {}), **(effective.get("refine") or {})}

    return json.loads(json.dumps(out, ensure_ascii=False, default=str))
