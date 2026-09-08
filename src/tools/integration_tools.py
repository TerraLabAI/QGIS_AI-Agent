# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Integration tools for TerraLab AI Edit and AI Segmentation plugins."""












from __future__ import annotations

from ..core.logger import log, log_warning
from ..core.tool_registry import Tool, ToolRegistry
from ._widgets import AI_SEGMENT_KEYS
from .adapters.ai_edit_access import ACCESS as AIEDIT

AISEG_KEYS = list(AI_SEGMENT_KEYS)

TERRALAB_SIGNUP_URL = (
    "https://terra-lab.ai/register"
    "?product=ai-agent"
    "&utm_source=qgis&utm_medium=mcp&utm_campaign=ai-agent"
)
AISEG_REGISTER_URL = "https://terra-lab.ai/ai-segmentation?utm_source=qgis&utm_medium=mcp&utm_campaign=ai-agent"


def _find_plugin(candidate_keys: list[str]):
    import qgis.utils
    for key in candidate_keys:
        plugin = qgis.utils.plugins.get(key)
        if plugin is not None:
            return key, plugin
    return None, None


def _aiseg_module(plugin, dotted: str):
    """Import a submodule of the live AI Segmentation package by dotted path under its ``src`` root (e.g."""



    import importlib
    if plugin is None:
        return None
    try:
        base = type(plugin).__module__.rsplit(".", 2)[0]
        return importlib.import_module(base + "." + dotted)
    except Exception:
        return None


def register_integration_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="ai_edit_status",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_aiedit_status,
    ))

    registry.register(Tool(
        name="ai_segment_status",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_aiseg_status,
    ))

    registry.register(Tool(
        name="ai_segment_load_model",
        input_schema={
            "type": "object",
            "properties": {
                "timeout_s": {"type": "number", "minimum": 1, "maximum": 600},
            },
            "required": [],
        },
        handler=_aiseg_load_model,
    ))

    registry.register(Tool(
        name="ai_edit_get_presets",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_aiedit_get_presets,
    ))

    registry.register(Tool(
        name="ai_edit_get_credits",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_aiedit_get_credits,
    ))

    registry.register(Tool(
        name="ai_edit_get_resolutions",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_aiedit_get_resolutions,
    ))

    _register_aiedit_generate(registry)
    _register_aiedit_actions(registry)
    _try_register_aiseg(registry)
    _try_register_aiseg_auto(registry)


def _register_aiedit_actions(registry: ToolRegistry):
    """LLM-facing AI Edit control tools (cancel, vectorize, markup, version lineage)."""



    try:
        registry.register(Tool(
            name="ai_edit_cancel",
            input_schema={
                "type": "object",
                "properties": {
                    "exit": {
                        "type": "boolean",
                    },
                },
                "required": [],
            },
            handler=_aiedit_cancel,
        ))

        registry.register(Tool(
            name="ai_edit_vectorize",
            input_schema={
                "type": "object",
                "properties": {
                    "layer_name": {
                        "type": "string",
                    },
                    "target_rgb": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 0, "maximum": 255},
                        "minItems": 3,
                        "maxItems": 3,
                    },
                    "tolerance": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 255,
                    },
                    "simplify_factor": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 100,
                    },
                    "class_label": {
                        "type": "string",
                    },
                },
                "required": ["target_rgb"],
            },
            handler=_aiedit_vectorize,
        ))

        registry.register(Tool(
            name="ai_edit_markup",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["draw", "clear", "done"],
                    },
                    "geometry_wkt": {
                        "type": "string",
                    },
                    "color": {
                        "type": "string",
                    },
                },
                "required": ["action"],
            },
            handler=_aiedit_markup,
        ))

        registry.register(Tool(
            name="ai_edit_select_version",
            input_schema={
                "type": "object",
                "properties": {
                    "index": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 1000,
                    },
                },
                "required": ["index"],
            },
            handler=_aiedit_select_version,
        ))
        log("AI Edit action tools registered (cancel, vectorize, markup, select_version)")
    except Exception as e:
        log_warning(f"Failed to register AI Edit action tools: {e}")


def _register_aiedit_generate(registry: ToolRegistry):




    try:
        registry.register(Tool(
            name="ai_edit_generate",
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                    },
                    "bbox": {
                        "oneOf": [
                            {
                                "type": "object",
                                "properties": {
                                    "xmin": {"type": "number"},
                                    "ymin": {"type": "number"},
                                    "xmax": {"type": "number"},
                                    "ymax": {"type": "number"},
                                },
                                "required": ["xmin", "ymin", "xmax", "ymax"],
                            },
                            {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
                        ],
                    },
                    "use_canvas_extent": {
                        "type": "boolean",
                    },
                    "resolution": {
                        "type": "string",
                        "enum": ["1K", "2K", "4K"],
                    },
                    "reference_layers": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "template_id": {
                        "type": "string",
                    },
                    "confirm_area_km2": {
                        "type": "number",
                    },
                },
                "required": ["prompt"],
            },
            handler=_aiedit_generate,
        ))
        log("AI Edit integration tools registered")

    except Exception as e:
        log_warning(f"Failed to register AI Edit tools: {e}")


def _try_register_aiseg(registry: ToolRegistry):
    try:
        key, plugin = _find_plugin(AISEG_KEYS)
        if not plugin:
            log("AI Segmentation plugin not detected, ai_segment_get_presets not available")
            return

        registry.register(Tool(
            name="ai_segment_get_presets",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=_aiseg_get_presets,
        ))
        log(f"AI Segmentation integration tools registered (key: {key})")

    except Exception as e:
        log_warning(f"Failed to register AI Segmentation tools: {e}")


def _try_register_aiseg_auto(registry: ToolRegistry):
    try:
        key, plugin = _find_plugin(AISEG_KEYS)
        if not plugin:
            log("AI Segmentation plugin not detected, automatic detection tools not available")
            return

        registry.register(Tool(
            name="ai_segment_set_mode",
            input_schema={
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["interactive", "automatic"],
                    },
                },
                "required": ["mode"],
            },
            handler=_aiseg_set_mode,
        ))

        registry.register(Tool(
            name="ai_segment_set_zone",
            input_schema={
                "type": "object",
                "properties": {
                    "zone_wkt": {
                        "type": "string",
                    },
                },
                "required": [],
            },
            handler=_aiseg_set_zone,
        ))

        registry.register(Tool(
            name="ai_segment_detect_auto",
            input_schema={
                "type": "object",
                "properties": {
                    "object_class": {
                        "type": "string",
                    },
                    "zone_wkt": {
                        "type": "string",
                    },
                    "bbox": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 4,
                        "maxItems": 4,
                    },
                    "use_canvas_extent": {
                        "type": "boolean",
                    },
                    "layer_name": {
                        "type": "string",
                    },
                    "detail": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 7,
                    },
                    "exemplars": {
                        "type": "array",
                        "maxItems": 100,
                        "items": {
                            "type": "object",
                            "properties": {
                                "bbox": {
                                    "type": "array",
                                    "items": {"type": "number"},
                                    "minItems": 4,
                                    "maxItems": 4,
                                },
                                "label": {
                                    "type": "integer",
                                    "enum": [0, 1],
                                },
                            },
                            "required": ["bbox"],
                        },
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0.05,
                        "maximum": 0.95,
                    },
                    "refine": {
                        "type": "object",
                        "properties": {
                            "simplify_px": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1000,
                            },
                            "expand_px": {
                                "type": "integer",
                                "minimum": -1000,
                                "maximum": 1000,
                            },
                            "fill_holes": {"type": "boolean"},
                            "right_angles": {
                                "type": "boolean",
                            },
                            "min_size_m2": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1000000000000,
                            },
                        },
                    },
                    "confirm_area_km2": {
                        "type": "number",
                        "minimum": 0,
                    },
                    "accept_weak_class": {
                        "type": "boolean",
                    },
                },
                "required": [],
            },
            handler=_aiseg_detect_auto,
        ))

        registry.register(Tool(
            name="ai_segment_review_filter",
            input_schema={
                "type": "object",
                "properties": {
                    "confidence": {
                        "type": "number",
                        "minimum": 0.05,
                        "maximum": 0.95,
                    },
                },
                "required": ["confidence"],
            },
            handler=_aiseg_review_filter,
        ))

        registry.register(Tool(
            name="ai_segment_set_display_mode",
            input_schema={
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["normal", "outline", "confidence", "random"],
                    },
                },
                "required": ["mode"],
            },
            handler=_aiseg_set_display_mode,
        ))

        registry.register(Tool(
            name="ai_segment_auto_status",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=_aiseg_auto_status,
        ))

        registry.register(Tool(
            name="ai_segment_auto_cancel",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=_aiseg_auto_cancel,
        ))

        log(f"AI Segmentation automatic detection tools registered (key: {key})")

    except Exception as e:
        log_warning(f"Failed to register AI Segmentation automatic detection tools: {e}")


def _aiedit_get_presets(args: dict) -> dict:
    try:
        return AIEDIT.presets()
    except Exception as e:
        return {"_error": f"Failed to get presets: {str(e)}"}


def _aiedit_get_credits(args: dict) -> dict:
    try:
        return AIEDIT.credits()
    except Exception as e:
        return {"_error": f"Failed to get credits: {str(e)}"}


def _aiedit_get_resolutions(args: dict) -> dict:
    try:
        return AIEDIT.resolutions()
    except Exception as e:
        return {"_error": f"Failed to get resolutions: {str(e)}"}


def _aiedit_status(args: dict) -> dict:
    try:
        status = AIEDIT.status()
        if not status.get("installed"):
            status.setdefault(
                "action_required",
                "Install 'AI Edit by TerraLab' from QGIS Plugin Manager.",
            )
            status.setdefault("signup_url", TERRALAB_SIGNUP_URL)
        return status
    except Exception as e:
        return {"installed": False, "ready": False, "_error": str(e)}


def _aiedit_generate(args: dict) -> dict:




    use_canvas = bool(args.get("use_canvas_extent"))
    if args.get("bbox") is None and not use_canvas:
        return {"_error": "Provide bbox [xmin,ymin,xmax,ymax] (or {xmin,...}) or use_canvas_extent:true."}
    try:
        return AIEDIT.run_generation({
            "prompt": args.get("prompt", ""),
            "bbox": args.get("bbox"),
            "use_canvas_extent": use_canvas,
            "resolution": args.get("resolution", "1K"),
            "reference_layers": args.get("reference_layers"),
            "template_id": args.get("template_id"),
        })
    except Exception as e:
        log_warning(f"AI Edit generation failed: {e}")
        return {"_error": f"AI Edit generation failed: {str(e)}"}


def _aiedit_cancel(args: dict) -> dict:
    try:
        return AIEDIT.cancel(bool(args.get("exit", False)))
    except Exception as e:
        log_warning(f"AI Edit cancel failed: {e}")
        return {"_error": f"AI Edit cancel failed: {str(e)}"}


def _aiedit_vectorize(args: dict) -> dict:
    try:
        return AIEDIT.vectorize({
            "layer_name": args.get("layer_name"),
            "target_rgb": args.get("target_rgb"),
            "tolerance": args.get("tolerance"),
            "simplify_factor": args.get("simplify_factor"),
            "class_label": args.get("class_label"),
        })
    except Exception as e:
        log_warning(f"AI Edit vectorize failed: {e}")
        return {"_error": f"AI Edit vectorize failed: {str(e)}"}


def _aiedit_markup(args: dict) -> dict:
    try:
        return AIEDIT.markup(
            args.get("action", ""),
            geometry_wkt=args.get("geometry_wkt"),
            color=args.get("color"),
        )
    except Exception as e:
        log_warning(f"AI Edit markup failed: {e}")
        return {"_error": f"AI Edit markup failed: {str(e)}"}


def _aiedit_select_version(args: dict) -> dict:
    try:
        return AIEDIT.select_version(args.get("index"))
    except Exception as e:
        log_warning(f"AI Edit select_version failed: {e}")
        return {"_error": f"AI Edit select_version failed: {str(e)}"}


def _aiseg_status(args: dict) -> dict:
    try:
        _, plugin = _find_plugin(AISEG_KEYS)
        if not plugin:
            return {
                "installed": False,
                "ready": False,
                "state": "NOT_INSTALLED",
                "action_required": "Install 'AI Segmentation by TerraLab' from QGIS Plugin Manager.",
                "register_url": AISEG_REGISTER_URL,
            }

        api = getattr(plugin, "mcp_api", None)
        if api is None:
            return {"installed": True, "ready": False, "state": "NEEDS_UPDATE",
                    "action_required": "Update AI Segmentation from QGIS Plugin Manager for MCP support."}

        status = api.get_status()
        if isinstance(status, dict) and status.get("state") == "MODEL_NOT_LOADED":
            status = _aiseg_without_local_model(status)
        return status
    except Exception as e:
        return {"installed": False, "ready": False, "_error": str(e)}


def _aiseg_without_local_model(status: dict) -> dict:
    """The status when the on-device model is installed but not loaded."""








    from qgis.core import QgsProject, QgsRasterLayer
    rasters = [layer.name() for layer in QgsProject.instance().mapLayers().values()
               if isinstance(layer, QgsRasterLayer)]
    status = dict(status)
    status["model_loaded"] = False
    status["available_raster_layers"] = rasters
    status["on_device_model"] = (
        "Not loaded. Only the panel's Semi-Auto clicks use it; detect_auto is a cloud "
        "call and runs without it. ai_segment action 'load_model' loads it for free "
        "if you need it; never send the user to the panel for that."
    )
    if rasters:
        status["ready"] = True
        status["state"] = "READY"
        status.pop("action_required", None)
        status["hint"] = "detect_auto can run now: pass the zone and object_class."
    else:
        status["ready"] = False
        status["state"] = "NO_RASTER_LAYER"
        status["action_required"] = "No raster layer in the project. Load imagery first (add_data)."
    return status


def _aiseg_load_model(args: dict) -> dict:
    """Load the on-device model the panel's Semi-Auto mode uses. Free, local."""
    try:
        _, plugin = _find_plugin(AISEG_KEYS)
        if not plugin:
            return {"_error": "AI Segmentation plugin is not installed."}
        api = getattr(plugin, "mcp_api", None)
        fn = getattr(api, "load_model", None) if api is not None else None
        if fn is None:
            return {"_error": "This AI Segmentation version has no load_model call. "
                              "Update it from QGIS Plugin Manager."}
        timeout_s = args.get("timeout_s")
        result = fn(timeout_s=timeout_s) if timeout_s is not None else fn()
        if isinstance(result, dict) and result.get("loaded"):
            result.setdefault("hint", "The local model is loaded. detect_auto never needed it; "
                                      "the panel's Semi-Auto clicks do.")
        return result
    except Exception as e:
        log_warning(f"AI Segmentation load_model failed: {e}")
        return {"_error": f"AI Segmentation load_model failed: {e}"}


def _aiseg_set_mode(args: dict) -> dict:
    try:
        _, plugin = _find_plugin(AISEG_KEYS)
        if not plugin:
            return {"_error": "AI Segmentation plugin is not installed."}

        api = getattr(plugin, "mcp_api", None)
        if api is None:
            return {"_error": "AI Segmentation needs updating for MCP support. Update from QGIS Plugin Manager."}

        fn = getattr(api, "set_mode", None)
        if fn is None:
            return {"_error": "AI Segmentation plugin too old for this command (needs 1.3.0+)."}

        return fn(mode=args["mode"])
    except Exception as e:
        log_warning(f"AI Segmentation set_mode failed: {e}")
        return {"_error": f"AI Segmentation set_mode failed: {str(e)}"}


def _aiseg_set_zone(args: dict) -> dict:
    try:
        _, plugin = _find_plugin(AISEG_KEYS)
        if not plugin:
            return {"_error": "AI Segmentation plugin is not installed."}

        api = getattr(plugin, "mcp_api", None)
        if api is None:
            return {"_error": "AI Segmentation needs updating for MCP support. Update from QGIS Plugin Manager."}

        fn = getattr(api, "set_auto_zone", None)
        if fn is None:
            return {"_error": "AI Segmentation plugin too old for this command (needs 1.3.0+)."}

        zone_wkt = args.get("zone_wkt") or None
        return fn(zone_wkt=zone_wkt)
    except Exception as e:
        log_warning(f"AI Segmentation set_auto_zone failed: {e}")
        return {"_error": f"AI Segmentation set_auto_zone failed: {str(e)}"}


def _aiseg_color_by_instance(result) -> None:
    """Give the agent's finished detection the in-app review look: one distinct colour per instance."""




    try:
        if not isinstance(result, dict) or result.get("_error"):
            return
        layer_name = result.get("layer_name")
        inst = result.get("instances") or 0
        if not layer_name or not inst:
            return


        if inst > 3000:
            return
        import colorsys

        from qgis.core import QgsCategorizedSymbolRenderer, QgsFillSymbol, QgsProject, QgsRendererCategory
        layers = QgsProject.instance().mapLayersByName(layer_name)
        if not layers:
            return
        layer = layers[0]
        cats = []
        for i, feat in enumerate(layer.getFeatures()):
            hue = (i * 0.61803398875) % 1.0
            r, g, b = colorsys.hsv_to_rgb(hue, 0.68, 0.98)
            rr, gg, bb = int(r * 255), int(g * 255), int(b * 255)
            sym = QgsFillSymbol.createSimple({
                "color": f"{rr},{gg},{bb},110",
                "outline_color": f"{rr},{gg},{bb},255",
                "outline_width": "0.5",
            })
            cats.append(QgsRendererCategory(feat.id(), sym, str(feat.id())))
        if cats:
            layer.setRenderer(QgsCategorizedSymbolRenderer("$id", cats))
            layer.triggerRepaint()
    except Exception as exc:  # noqa: BLE001
        log_warning(f"AI Segmentation instance colouring skipped: {exc}")


def _aiseg_class_preflight(api, object_class: str, args: dict) -> dict | None:
    """Refuse a detect_auto class the catalogue cannot serve, before it bills."""













    describe = getattr(api, "describe_object_class", None)
    if not callable(describe):
        return None
    try:
        described = describe(object_class)
    except Exception as err:  # noqa: BLE001 - a pre-flight never fails a run
        log_warning(f"AI Segmentation class pre-flight skipped: {err}")
        return None
    if not isinstance(described, dict):
        return None

    detail = described.get("_error")
    if detail:
        out = {
            "_error": (
                f"'{object_class}' is not an object class AI Segmentation can detect, "
                f"and running it would spend credits for nothing. {detail}"
            ),
            "_suggestion": (
                "Pick a catalogue token with ai_segment presets (action 'presets'), or pass "
                "exemplars: example boxes around one instance, which need no class word at all."
            ),
        }
        nearest = described.get("_suggestions")
        if isinstance(nearest, list) and nearest:
            out["nearest_classes"] = [str(token) for token in nearest]
        return out

    if described.get("weak") and not args.get("accept_weak_class"):
        token = str(described.get("token") or object_class)
        return {
            "_error": (
                f"'{token}' names a kind of ground cover, not a countable object, so a zone run "
                "returns soft ragged outlines rather than separate instances."
            ),
            "_suggestion": (
                "Tell the user what this class actually returns and ask whether to go ahead. "
                "To run it anyway, call again with accept_weak_class: true. For countable "
                "objects, pick another token from ai_segment presets."
            ),
            "weak_class": token,
        }
    return None


def _aiseg_raster_layer(plugin, layer_name):
    """The raster the run will read: the one named, else the plugin's active one."""
    from qgis.core import QgsProject, QgsRasterLayer
    name = str(layer_name or "").strip()
    if name:
        for layer in QgsProject.instance().mapLayersByName(name):
            if isinstance(layer, QgsRasterLayer):
                return layer
    fn = getattr(plugin, "_get_active_raster_layer", None)
    if callable(fn):
        try:
            return fn()
        except Exception:  # noqa: BLE001 - a plugin's internals are not a contract
            return None
    return None


def _aiseg_zone_wkt(plugin, args: dict) -> str:
    """The zone this run sweeps, as WKT in the raster layer CRS."""







    wkt = str(args.get("zone_wkt") or "").strip()
    if wkt:
        return wkt
    from . import cost_guard
    geom = cost_guard.zone_from_args(args)
    if geom is None:
        return ""
    layer = _aiseg_raster_layer(plugin, args.get("layer_name"))
    try:
        from qgis.core import QgsCoordinateTransform, QgsGeometry, QgsProject
        source = cost_guard._canvas_crs()
        target = layer.crs() if layer is not None else None
        if target is not None and source is not None and target.isValid() and source.isValid() \
                and target != source:
            geom = QgsGeometry(geom)
            geom.transform(QgsCoordinateTransform(source, target, QgsProject.instance()))
        return geom.asWkt()
    except Exception as exc:  # noqa: BLE001 - a half-moved zone would sweep the wrong ground
        log_warning(f"AI Segmentation zone could not be expressed in the layer CRS: {exc}")
        return ""


def _aiseg_imagery_preflight(name: str) -> dict | None:
    """Refuse a layer_name that names no raster in the project, with the ones it could mean."""








    try:
        from qgis.core import QgsProject, QgsRasterLayer

        layers = list(QgsProject.instance().mapLayers().values())
    except Exception:  # noqa: BLE001 - a preflight never fails the call it inspects
        return None
    rasters = [layer.name() for layer in layers if isinstance(layer, QgsRasterLayer)]
    if name in rasters:
        return None
    if any(layer.name() == name for layer in layers):
        return {
            "_error": f"'{name}' is not a raster: layer_name is the imagery to read, not the layer to write.",
            "code": "INVALID_ARGS",
            "_suggestion": ("Name the imagery instead: " + ", ".join(f"'{r}'" for r in rasters[:8])
                            if rasters else "Add a satellite or aerial basemap first, then call again."),
        }
    return {
        "_error": f"No layer named '{name}'. layer_name is the imagery detect_auto reads, not the result's name.",
        "code": "INVALID_ARGS",
        "_suggestion": ("Pass one of these, or leave layer_name out to use the active raster: "
                        + ", ".join(f"'{r}'" for r in rasters[:8])
                        if rasters else "Add a satellite or aerial basemap first, then call again."),
    }


def _aiseg_detect_auto(args: dict) -> dict:
    try:
        _, plugin = _find_plugin(AISEG_KEYS)
        if not plugin:
            return {"_error": "AI Segmentation plugin is not installed."}

        api = getattr(plugin, "mcp_api", None)
        if api is None:
            return {"_error": "AI Segmentation needs updating for MCP support. Update from QGIS Plugin Manager."}

        fn = getattr(api, "detect_auto", None)
        if fn is None:
            return {"_error": "AI Segmentation plugin too old for this command (needs 1.3.0+)."}




        object_class = (args.get("object_class") or "").strip()
        exemplars = args.get("exemplars")
        if not object_class and not exemplars:
            return {
                "_error": "Provide object_class or exemplars.",
                "_suggestion": "Give a class token (e.g. 'building') or draw exemplar boxes for reference-image mode.",
            }





        if object_class and not exemplars:
            refusal = _aiseg_class_preflight(api, object_class, args)
            if refusal is not None:
                return refusal

        wanted = (args.get("layer_name") or "").strip()
        if wanted:
            refusal = _aiseg_imagery_preflight(wanted)
            if refusal is not None:
                return refusal




        kwargs = {
            "zone_wkt": _aiseg_zone_wkt(plugin, args),
            "object_class": object_class,
            "layer_name": args.get("layer_name"),
        }
        if exemplars:
            try:
                import inspect
                if "exemplars" in inspect.signature(fn).parameters:
                    kwargs["exemplars"] = exemplars
                else:
                    return {"_error": (
                        "This AI Segmentation version does not support reference-image "
                        "exemplars. Update the plugin from QGIS Plugin Manager."
                    )}
            except (TypeError, ValueError):
                kwargs["exemplars"] = exemplars




        detail = args.get("detail")
        if detail is not None:
            try:
                import inspect
                if "detail" in inspect.signature(fn).parameters:
                    kwargs["detail"] = int(detail)
            except (TypeError, ValueError):
                pass






        confidence = args.get("confidence")
        if confidence is not None:
            try:
                if hasattr(plugin, "_ensure_dock_widget"):
                    plugin._ensure_dock_widget()
                dock = getattr(plugin, "dock_widget", None)
                spin = getattr(dock, "auto_confidence_spin", None) if dock is not None else None
                if spin is not None:
                    spin.setValue(max(0.05, min(0.95, float(confidence))))
            except (TypeError, ValueError, RuntimeError, AttributeError) as err:
                log_warning(f"AI Segmentation set confidence failed: {err}")





        restore = _aiseg_apply_refine_override(plugin, args.get("refine"))
        try:
            result = fn(**kwargs)


            _aiseg_color_by_instance(result)
            return result
        finally:
            if restore is not None:
                restore()
    except Exception as e:
        log_warning(f"AI Segmentation detect_auto failed: {e}")
        return {"_error": f"AI Segmentation detect_auto failed: {str(e)}"}


def _aiseg_apply_refine_override(plugin, refine):
    """Temporarily override plugin._auto_review_preset() so a headless auto run exports with caller-supplied refine settings."""


    if not refine or not isinstance(refine, dict):
        return None
    original = getattr(plugin, "_auto_review_preset", None)
    if not callable(original):
        return None


    overrides = {}
    if refine.get("simplify_px") is not None:
        try:
            overrides["simplify_px"] = float(refine["simplify_px"])
        except (TypeError, ValueError):
            pass
    if refine.get("expand_px") is not None:
        try:
            overrides["expand_px"] = int(refine["expand_px"])
        except (TypeError, ValueError):
            pass
    if refine.get("fill_holes") is not None:
        overrides["fill_holes"] = bool(refine["fill_holes"])
    if refine.get("right_angles") is not None:
        overrides["ortho"] = bool(refine["right_angles"])
    if refine.get("min_size_m2") is not None:
        try:
            overrides["min_size_m2"] = float(refine["min_size_m2"])
        except (TypeError, ValueError):
            pass
    if not overrides:
        return None

    def _patched():
        base = original() or {}
        base.update(overrides)
        return base

    try:
        plugin._auto_review_preset = _patched
    except (AttributeError, RuntimeError):
        return None

    def _restore():
        try:

            del plugin._auto_review_preset
        except (AttributeError, RuntimeError):
            try:
                plugin._auto_review_preset = original
            except (AttributeError, RuntimeError):
                pass
    return _restore


def _aiseg_get_presets(args: dict) -> dict:
    try:
        _, plugin = _find_plugin(AISEG_KEYS)
        if not plugin:
            return {"_error": "AI Segmentation plugin is not installed."}
        mod = _aiseg_module(plugin, "core.presets.segmentation_presets")
        if mod is None:
            return {"_error": "AI Segmentation preset library (segmentation_presets) not found in this version."}
        out = {}
        all_presets = getattr(mod, "all_presets", None)
        fallback_categories = getattr(mod, "fallback_categories", None)
        try:
            if callable(fallback_categories):
                out["categories"] = fallback_categories()
        except Exception as err:
            log_warning(f"AI Segmentation fallback_categories failed: {err}")
        try:
            if callable(all_presets):
                presets = all_presets()
                out["presets"] = presets
                out["tokens"] = sorted(
                    {str(p.get("prompt")) for p in presets if isinstance(p, dict) and p.get("prompt")}
                )
        except Exception as err:
            log_warning(f"AI Segmentation all_presets failed: {err}")
        if not out:
            return {"_error": "AI Segmentation presets could not be read."}
        return out
    except Exception as e:
        log_warning(f"AI Segmentation get_presets failed: {e}")
        return {"_error": f"AI Segmentation get_presets failed: {str(e)}"}


def _aiseg_review_filter(args: dict) -> dict:
    try:
        _, plugin = _find_plugin(AISEG_KEYS)
        if not plugin:
            return {"_error": "AI Segmentation plugin is not installed."}
        conf = args.get("confidence")
        if conf is None:
            return {"_error": "confidence is required (0.05-0.95)."}
        try:
            conf = max(0.05, min(0.95, float(conf)))
        except (TypeError, ValueError):
            return {"_error": "confidence must be a number 0.05-0.95."}

        objects = getattr(plugin, "_auto_objects", None)
        review = getattr(plugin, "_auto_review", None)
        if not objects or review is None:
            return {
                "_error": (
                    "No open detection review to re-filter. MCP ai_segment_detect_auto auto-exports and clears its "
                    "review, so pass 'confidence' to ai_segment_detect_auto instead, or run a detection in the panel "
                    "and keep it in review."
                )
            }





        try:
            plugin._auto_confidence = conf
        except (AttributeError, RuntimeError):
            return {"_error": "Cannot set the review confidence on this plugin version."}
        try:
            dock = getattr(plugin, "dock_widget", None)
            spin = getattr(dock, "auto_confidence_spin", None) if dock is not None else None
            if spin is not None:
                spin.blockSignals(True)
                spin.setValue(conf)
                spin.blockSignals(False)
        except (RuntimeError, AttributeError):
            pass

        kept = None
        passes = getattr(plugin, "_passes_review_filters", None)
        widget_params = getattr(plugin, "_widget_review_params", None)
        if callable(passes) and callable(widget_params):
            try:
                params = widget_params()
                params["conf"] = conf
                removed = getattr(plugin, "_auto_manual_removed", None) or set()
                kept = sum(
                    1 for det_idx, (g, s, a) in enumerate(objects)
                    if det_idx not in removed and g is not None and passes(s, a, params)
                )
            except Exception as err:
                log_warning(f"AI Segmentation review count failed: {err}")
        if kept is None:
            kept = sum(1 for (g, s, a) in objects if g is not None and s >= conf)



        reslice = getattr(plugin, "_start_auto_reslice", None)
        if callable(reslice):
            try:
                reslice()
            except Exception as err:  # nosec B110
                log_warning(f"AI Segmentation reslice failed: {err}")

        return {"ok": True, "confidence": conf,
                "kept_instances": kept, "total_found": len(objects)}
    except Exception as e:
        log_warning(f"AI Segmentation review_filter failed: {e}")
        return {"_error": f"AI Segmentation review_filter failed: {str(e)}"}


def _aiseg_set_display_mode(args: dict) -> dict:
    try:
        _, plugin = _find_plugin(AISEG_KEYS)
        if not plugin:
            return {"_error": "AI Segmentation plugin is not installed."}
        mode = (args.get("mode") or "").strip().lower()
        if mode not in ("normal", "outline", "confidence", "random"):
            return {"_error": "mode must be one of: normal, outline, confidence, random."}
        dock = getattr(plugin, "dock_widget", None)
        setter = getattr(dock, "set_auto_display_mode", None) if dock is not None else None
        if not callable(setter):
            return {
                "_error": "AI Segmentation display-mode control (set_auto_display_mode) not available in this version."
            }
        try:
            setter(mode)
        except Exception as err:
            return {"_error": f"Set display mode failed: {err}"}


        applier = getattr(plugin, "_on_auto_display_mode_changed", None)
        if callable(applier):
            try:
                applier(mode)
            except Exception as err:  # nosec B110
                log_warning(f"AI Segmentation apply display mode failed: {err}")
        return {"ok": True, "mode": mode}
    except Exception as e:
        log_warning(f"AI Segmentation set_display_mode failed: {e}")
        return {"_error": f"AI Segmentation set_display_mode failed: {str(e)}"}


def _aiseg_auto_status(args: dict) -> dict:
    try:
        _, plugin = _find_plugin(AISEG_KEYS)
        if not plugin:
            return {"_error": "AI Segmentation plugin is not installed."}

        api = getattr(plugin, "mcp_api", None)
        if api is None:
            return {"_error": "AI Segmentation needs updating for MCP support. Update from QGIS Plugin Manager."}

        fn = getattr(api, "auto_detect_status", None)
        if fn is None:
            return {"_error": "AI Segmentation plugin too old for this command (needs 1.3.0+)."}

        return fn()
    except Exception as e:
        log_warning(f"AI Segmentation auto_detect_status failed: {e}")
        return {"_error": f"AI Segmentation auto_detect_status failed: {str(e)}"}


def _aiseg_auto_cancel(args: dict) -> dict:
    try:
        _, plugin = _find_plugin(AISEG_KEYS)
        if not plugin:
            return {"_error": "AI Segmentation plugin is not installed."}

        api = getattr(plugin, "mcp_api", None)
        if api is None:
            return {"_error": "AI Segmentation needs updating for MCP support. Update from QGIS Plugin Manager."}

        fn = getattr(api, "cancel_auto", None)
        if fn is None:
            return {"_error": "AI Segmentation plugin too old for this command (needs 1.3.0+)."}

        return fn()
    except Exception as e:
        log_warning(f"AI Segmentation cancel_auto failed: {e}")
        return {"_error": f"AI Segmentation cancel_auto failed: {str(e)}"}
