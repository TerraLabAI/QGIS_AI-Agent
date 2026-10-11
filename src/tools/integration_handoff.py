# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





from __future__ import annotations

from qgis.core import QgsProject, QgsRasterLayer

from ..core.tool_registry import tool_error
from . import sibling_setup
from ._widgets import AI_EDIT_KEYS, AI_SEGMENT_KEYS


class HandoffRefused(ValueError):


    def __init__(self, message: str, hint: str, **facts):
        super().__init__(message)
        self.hint = hint
        self.facts = facts


def refusal(exc: ValueError) -> dict:

    return tool_error(str(exc), "INVALID_ARGS", hint=getattr(exc, "hint", ""), **getattr(exc, "facts", {}))


def raster_layer(value):

    project = QgsProject.instance()
    layer = project.mapLayer(value)
    matches = [layer] if layer is not None else list(project.mapLayersByName(value))
    if len(matches) != 1:
        raise HandoffRefused("The imagery is missing or its name is ambiguous.", "imagery_not_unique",
                             layer=str(value), matches=len(matches))
    if not isinstance(matches[0], QgsRasterLayer) or not matches[0].isValid():
        raise ValueError("The input must be a valid raster layer in this project.")
    return matches[0]


def interactive_state(api):
    reader = getattr(api, "get_interactive_state", None)
    return reader() if callable(reader) else None


def edit_prompt(args, state):

    value = args.get("prompt") if "prompt" in args else (state or {}).get("prompt")
    if not isinstance(value, str) or not value.strip():
        raise HandoffRefused("No prompt was supplied and the AI Edit panel holds none.", "ai_edit_prompt_missing")
    return value.strip()


def segmentation_arguments(plugin, args):






    if args.get("zone_wkt") or args.get("use_zone") or args.get("bbox") is not None \
            or args.get("use_canvas_extent"):
        return args
    state = interactive_state(getattr(plugin, "mcp_api", None))
    if not isinstance(state, dict) or not state.get("zone_wkt"):
        return args
    if state.get("exemplars") and state.get("examples_match_source") is False:
        raise HandoffRefused("The drawn examples belong to another image.", "ai_segment_examples_other_image")
    source = state.get("source") or {}
    source_id = source.get("id") or source.get("layer_id")
    wanted = args.get("layer_name")
    if wanted and raster_layer(wanted).id() != source_id:
        raise HandoffRefused("The prepared zone and examples belong to another image.",
                             "ai_segment_zone_other_image", layer=str(wanted))
    out = dict(args, zone_wkt=state["zone_wkt"])
    if source_id:
        out["layer_name"] = source_id
    prompt = state.get("object_class") or ""
    if "object_class" not in out:
        out["object_class"] = prompt
    if "exemplars" not in out and out.get("object_class") == prompt and state.get("exemplars"):
        out["exemplars"] = state["exemplars"]
    return out


_EDIT_SETTINGS = ("index", "template_id", "resolution", "reference_layers", "markup_wkt")


def _edit_settings(args):

    return any(args.get(name) not in (None, "", []) for name in _EDIT_SETTINGS)


def prepare(which, args):

    presence = sibling_setup.presence(AI_EDIT_KEYS if which == "ai_edit" else AI_SEGMENT_KEYS)
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
            from . import cost_guard
            if args.get("use_zone"):
                from ..core import zone_of_interest
                if zone_of_interest.read_zone() is None:
                    raise HandoffRefused("The project has no area of interest.", "zone_of_interest_missing")
            if which == "ai_segment":
                from .integration_tools import _aiseg_zone_wkt
                kwargs["zone_wkt"] = _aiseg_zone_wkt(plugin, args)
            else:
                geometry = cost_guard.zone_from_args(args)
                if geometry is None:
                    raise ValueError("The requested zone could not be read.")
                from .adapters.ai_edit_access import ACCESS
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

                from .adapters.ai_edit_access import ACCESS
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

    from . import cost_guard
    from ._widgets import sibling_plugin

    edit = label == "AI Edit"
    _, plugin = sibling_plugin(AI_EDIT_KEYS if edit else AI_SEGMENT_KEYS)
    api = getattr(plugin, "mcp_api", None)
    state = _api_state(api, "get_interactive_state")
    if state.get("drawing"):
        raise HandoffRefused("A drawing is still open in the native panel.", "native_drawing_open")
    wkt = geometry.asWkt() if callable(getattr(geometry, "asWkt", None)) else None
    crs = cost_guard._canvas_crs()
    out = {"zone_wkt": wkt, "zone_crs": crs.authid() or crs.toWkt()}
    if edit:
        from .adapters.ai_edit_access import ACCESS

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
        from .integration_tools import _aiseg_raster_layer

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
