# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

from qgis.core import QgsCoordinateTransform, QgsGeometry, QgsProject, QgsRasterLayer

from ..core.logger import log_warning
from ..core.tool_registry import tool_error
from . import zone_facts


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


def _aiseg_raster_layer(plugin, layer_name):

    name = str(layer_name or "").strip()
    if name:
        try:
            return raster_layer(name)
        except ValueError:
            return None
    fn = getattr(plugin, "_get_active_raster_layer", None)
    if callable(fn):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return None
    return None


def _aiseg_zone_wkt(plugin, args: dict) -> str:








    wkt = str(args.get("zone_wkt") or "").strip()
    if wkt and not args.get("use_zone"):
        return wkt
    if args.get("bbox") is not None and zone_facts.unknown_crs(args):
        raise ValueError(f"crs '{zone_facts.unknown_crs(args)}' is not a CRS QGIS knows.")
    geom = zone_facts.zone_from_args(args)
    if geom is None:
        if args.get("use_zone") or args.get("bbox") is not None or args.get("use_canvas_extent"):
            raise ValueError("The requested area of interest could not be read; prepare a valid zone first.")
        return ""
    layer = _aiseg_raster_layer(plugin, args.get("layer_name"))
    try:
        source = zone_facts._canvas_crs()
        target = layer.crs() if layer is not None else None
        if target is not None and source is not None and target.isValid() and source.isValid() \
                and target != source:
            geom = QgsGeometry(geom)
            geom.transform(QgsCoordinateTransform(source, target, QgsProject.instance()))
        return geom.asWkt()
    except Exception as exc:  # noqa: BLE001
        log_warning(f"AI Segmentation zone could not be expressed in the layer CRS: {exc}")
        raise ValueError("The zone could not be transformed into the input image CRS.") from exc
