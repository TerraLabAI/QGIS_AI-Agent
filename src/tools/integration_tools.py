# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later










from __future__ import annotations

from ..core.logger import log, log_warning
from ..core.tool_registry import Tool, ToolRegistry
from . import cost_guard
from . import sibling_setup as _setup
from ._widgets import AI_EDIT_KEYS, AI_SEGMENT_KEYS, sibling_plugin
from .adapters.ai_edit_access import ACCESS as AIEDIT

AISEG_KEYS = list(AI_SEGMENT_KEYS)


def _find_plugin(candidate_keys: list[str]):
    return sibling_plugin(candidate_keys)


def aiseg_presence() -> dict:









    return _setup.presence(AISEG_KEYS)


def aiseg_version(plugin) -> str:

    try:
        import qgis.utils
        key = next((k for k, v in (getattr(qgis.utils, "plugins", None) or {}).items() if v is plugin), "")
        return str(qgis.utils.pluginMetadata(key, "version") or "") if key else ""
    except Exception:  # noqa: BLE001
        return ""


def _aiseg_outdated(plugin, command: str) -> dict:


    version = aiseg_version(plugin)
    return {"_error": (f"AI Segmentation {version or '(this version)'} has no {command}: the agent needs "
                       f"AI Segmentation 1.3.0 or later."),
            "code": "PLUGIN_OUTDATED",
            "installed_version": version,
            "suggestion": ("ai_segment action setup opens the Plugin Manager on AI Segmentation, where the "
                           "person clicks Upgrade; that is the one step left.")}


def aiseg_not_running_status(presence: dict) -> dict:

    out = _setup.not_running("ai_segment", presence)
    if presence.get("folder"):
        out["plugin_folder"] = presence["folder"]
    return out


def _aiseg_module(plugin, dotted: str):




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
        name="ai_segment_status",
        danger="read",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_aiseg_status,
    ))

    registry.register(Tool(
        name="ai_segment_load_model",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {
                "timeout_s": {"type": "number", "minimum": 1, "maximum": 600},
            },
            "required": [],
        },
        handler=_aiseg_load_model,
    ))

    _register_aiedit_generate(registry)
    _register_aiedit_actions(registry)
    _try_register_aiseg_auto(registry)


def _register_aiedit_actions(registry: ToolRegistry):











    try:
        registry.register(Tool(
            name="ai_edit_vectorize",
            danger="destructive",
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
        log("AI Edit action tools registered (vectorize)")
    except Exception as e:
        log_warning(f"Failed to register AI Edit action tools: {e}")


def _register_aiedit_generate(registry: ToolRegistry):




    try:
        registry.register(Tool(
            name="ai_edit_generate",
            danger="destructive",
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
                    "use_zone": {
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
            },
            handler=_aiedit_generate,
        ))
        log("AI Edit integration tools registered")

    except Exception as e:
        log_warning(f"Failed to register AI Edit tools: {e}")


def _try_register_aiseg_auto(registry: ToolRegistry):
    try:
        key, plugin = _find_plugin(AISEG_KEYS)
        if not plugin:
            log("AI Segmentation plugin not detected, automatic detection tools not available")
            return

        registry.register(Tool(
            name="ai_segment_set_mode",
            danger="write",
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
            name="ai_segment_review_filter",
            danger="write",
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
            danger="write",
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


def _aiedit_status(args: dict) -> dict:

    try:
        found = _setup.presence(AI_EDIT_KEYS)
        if found["plugin"] is None:
            return _setup.not_running("ai_edit", found)
        status = AIEDIT.status()
        status["resolutions"] = AIEDIT.resolutions()
        from .integration_handoff import interactive_state
        state = interactive_state(getattr(found["plugin"], "mcp_api", None))
        if state is not None:
            status["interactive"] = state
        if _setup.signed_in("ai_edit", found["plugin"]) is False or status.get("state") == "NEEDS_ACTIVATION":
            return _setup.signed_out("ai_edit", status)
        if status.get("state") == "NO_PANEL":
            status["action_required"] = "The AI Edit panel is closed. setup opens it."
            status["next_step"] = _setup.SETUP_HINT.format(tool="ai_edit")
        return status
    except Exception as e:
        return {"ready": False, "_error": f"AI Edit status failed: {e}"}


def _aiedit_generate(args: dict) -> dict:




    use_canvas = bool(args.get("use_canvas_extent"))
    use_zone = bool(args.get("use_zone"))
    if args.get("bbox") is None and not use_canvas and not use_zone and AIEDIT.current_zone() is None:
        return {"_error": ("Needs use_zone:true (the project's zone of interest), bbox "
                           "[xmin,ymin,xmax,ymax], use_canvas_extent:true, or a zone prepared in AI Edit.")}


    shown = _setup.show_panel("ai_edit")
    try:
        params = {
            "bbox": args.get("bbox"),
            "use_canvas_extent": use_canvas,
            "use_zone": use_zone,
            "resolution": args.get("resolution"),
            "layer_name": args.get("layer_name"),
            "reference_layers": args.get("reference_layers"),
            "template_id": args.get("template_id"),
        }
        if "prompt" in args:
            params["prompt"] = args["prompt"]
        result = AIEDIT.run_generation(params)

        if isinstance(result, dict) and "ai_edit_generation_status" in str(result.get("note") or ""):
            result["note"] = result["note"].replace("ai_edit_generation_status", "ai_edit action generation_status")
        if shown and isinstance(result, dict) and "_error" not in result:
            result["panel_opened"] = True
            result["tell_user"] = ("The AI Edit panel is open beside the chat: the generation, its versions "
                                   "and the before and after slider are there.")
        return result
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


def _show_sibling(which: str) -> bool:








    try:
        return bool(_setup.show_panel(which))
    except Exception as exc:  # noqa: BLE001
        log_warning(f"{which} panel not raised: {exc}")
        return False


def _aiedit_select_version(args: dict) -> dict:
    try:
        return AIEDIT.select_version(args.get("index"))
    except Exception as e:
        log_warning(f"AI Edit select_version failed: {e}")
        return {"_error": f"AI Edit select_version failed: {str(e)}"}


def _aiseg_status(args: dict) -> dict:









    try:
        presence = aiseg_presence()
        plugin = presence["plugin"]
        if plugin is None:
            return aiseg_not_running_status(presence)

        api = getattr(plugin, "mcp_api", None)
        version = aiseg_version(plugin)
        if _setup.outdated("ai_segment", plugin):


            return {"installed": True, "ready": False, "state": "NEEDS_UPDATE", "installed_version": version,
                    "action_required": (f"AI Segmentation {version or ''} is too old for the agent (1.3.0 or "
                                        "later). setup opens the Plugin Manager on it; the person clicks Upgrade."),
                    "next_step": _setup.SETUP_HINT.format(tool="ai_segment")}


        status = (api.get_status(mode="automatic") if callable(getattr(api, "get_interactive_state", None))
                  else api.get_status())
        if not isinstance(status, dict):
            return {"ready": False, "_error": "AI Segmentation returned no status."}
        if status.get("state") in ("MODEL_NOT_DOWNLOADED", "MODEL_NOT_LOADED"):
            status = _aiseg_cloud_ready(status)
        for key in ("model_loaded", "register_url"):
            status.pop(key, None)
        _aiseg_add_balance(plugin, status)
        from .integration_handoff import interactive_state
        state = interactive_state(api)
        if state is not None:
            status["interactive"] = state
        if version:
            status["installed_version"] = version
        if _setup.signed_in("ai_segment", plugin) is False:
            return _setup.signed_out("ai_segment", status)
        return status
    except Exception as e:

        return {"ready": False, "_error": f"AI Segmentation status failed: {e}"}


def _aiseg_add_balance(plugin, status: dict) -> None:








    try:
        dock = getattr(plugin, "dock_widget", None)
        reader = getattr(dock, "_auto_km2_left", None)
        left = reader() if callable(reader) else None
        if left is not None:
            status["auto_km2_left_this_month"] = round(float(left), 1)
            if float(left) <= 0:



                status["can_run_auto"] = False
                status["blocker"] = ("auto_km2_left_this_month is 0.0: the account has no Automatic detection "
                                     "area left, and detect_auto is refused for credits until it renews")
                if str(status.get("hint") or "").startswith("detect_auto can run now"):
                    status.pop("hint")
    except Exception as exc:  # noqa: BLE001
        log_warning(f"AI Segmentation balance not read: {exc}")


def _aiseg_cloud_ready(status: dict) -> dict:





    from qgis.core import QgsProject, QgsRasterLayer
    rasters = [layer.name() for layer in QgsProject.instance().mapLayers().values()
               if isinstance(layer, QgsRasterLayer)]
    status = dict(status)
    status["available_raster_layers"] = rasters
    status["runs_in"] = "cloud: detect_auto needs no local model, download or Install click"
    if rasters:
        status["ready"] = True
        status["state"] = "READY"
        status.pop("action_required", None)
        status["hint"] = "detect_auto can run now, with the zone and object_class."
    else:
        status["ready"] = False
        status["state"] = "NO_RASTER_LAYER"
        status["action_required"] = "No raster layer in the project; add_data loads imagery."
    return status


def _aiseg_load_model(args: dict) -> dict:

    try:
        _, plugin = _find_plugin(AISEG_KEYS)
        if not plugin:
            return {"_error": "AI Segmentation plugin is not installed."}
        api = getattr(plugin, "mcp_api", None)
        fn = getattr(api, "load_model", None) if api is not None else None
        if fn is None:
            return {"_error": "This AI Segmentation version has no load_model call; "
                              "QGIS Plugin Manager has an update."}
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
            return {"_error": "AI Segmentation needs updating for MCP support, from QGIS Plugin Manager."}

        fn = getattr(api, "set_mode", None)
        if fn is None:
            return _aiseg_outdated(plugin, "set_mode")

        _show_sibling("ai_segment")
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
            return {"_error": "AI Segmentation needs updating for MCP support, from QGIS Plugin Manager."}

        fn = getattr(api, "set_auto_zone", None)
        if fn is None:
            return _aiseg_outdated(plugin, "set_auto_zone")
        if args.get("layer_name") and callable(getattr(api, "prepare_interactive", None)):
            from .integration_handoff import prepare
            return prepare("ai_segment", args)





        zone_wkt = _aiseg_zone_wkt(plugin, args) or None
        _show_sibling("ai_segment")
        return fn(zone_wkt=zone_wkt)
    except Exception as e:
        log_warning(f"AI Segmentation set_auto_zone failed: {e}")
        return {"_error": f"AI Segmentation set_auto_zone failed: {str(e)}"}


def _aiseg_detect_points(args: dict) -> dict:






    try:
        _, plugin = _find_plugin(AISEG_KEYS)
        if not plugin:
            return {"_error": "AI Segmentation plugin is not installed."}
        api = getattr(plugin, "mcp_api", None)
        fn = getattr(api, "detect_points", None) if api is not None else None
        if fn is None:
            return {"_error": "This AI Segmentation version does not support interactive point refinement; "
                              "QGIS Plugin Manager has an update."}
        positive = args.get("positive_points")
        if not positive:
            return {"_error": "positive_points needs at least one [x, y] point."}
        kwargs = {
            "positive": positive,
            "negative": args.get("negative_points") or [],
        }
        if args.get("layer_name"):
            kwargs["layer_name"] = args["layer_name"]
        return fn(**kwargs)
    except Exception as e:
        log_warning(f"AI Segmentation point detection failed: {e}")
        return {"_error": f"AI Segmentation point detection failed: {e}"}


def _aiseg_class_preflight(api, object_class: str, args: dict) -> dict | None:














    describe = getattr(api, "describe_object_class", None)
    if not callable(describe):
        return None
    try:
        described = describe(object_class)
    except Exception as err:  # noqa: BLE001
        log_warning(f"AI Segmentation class pre-flight skipped: {err}")
        return None
    if not isinstance(described, dict):
        return None

    detail = described.get("_error")
    if detail:
        nearest = described.get("_suggestions")
        nearest = [str(token) for token in nearest] if isinstance(nearest, list) else []
        return {
            "error": (
                f"'{object_class}' is not an object class AI Segmentation can detect, "
                f"and running it would spend credits for nothing. {detail}"
            ),
            "code": "INVALID_ARGS",
            "suggestion": (
                ("Nearest catalogue tokens: " + ", ".join(f"'{t}'" for t in nearest) + ". "
                 if nearest else "")
                + "ai_segment presets (action 'presets') lists catalogue tokens; exemplars "
                "(example boxes around one instance) need no class word."
            ),
        }

    if described.get("weak") and not args.get("accept_weak_class"):
        token = str(described.get("token") or object_class)
        return {
            "error": (
                f"'{token}' names a kind of ground cover, not a countable object, so a zone run "
                "returns soft ragged outlines rather than separate instances."
            ),
            "code": "INVALID_ARGS",
            "suggestion": (
                "Running this spends credits on soft ragged outlines. accept_weak_class: true "
                "runs it once the user agrees; countable objects need another ai_segment presets token."
            ),
        }
    return None


def _aiseg_raster_layer(plugin, layer_name):

    name = str(layer_name or "").strip()
    if name:
        from .integration_handoff import raster_layer
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
    from . import cost_guard
    geom = cost_guard.zone_from_args(args)
    if geom is None:
        if args.get("use_zone") or args.get("bbox") is not None or args.get("use_canvas_extent"):
            raise ValueError("The requested area of interest could not be read; prepare a valid zone first.")
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
    except Exception as exc:  # noqa: BLE001
        log_warning(f"AI Segmentation zone could not be expressed in the layer CRS: {exc}")
        raise ValueError("The zone could not be transformed into the input image CRS.") from exc


def _aiseg_imagery_name(name: str) -> str:







    try:
        from qgis.core import QgsProject

        layer = QgsProject.instance().mapLayer(name)
    except Exception:  # noqa: BLE001
        return name
    if layer is not None:
        if len(QgsProject.instance().mapLayersByName(layer.name())) != 1:
            raise ValueError("This AI Segmentation version only accepts names, and this image name is ambiguous.")
        return layer.name()
    return name


def _aiseg_imagery_refusal(args: dict) -> dict | None:








    wanted = str(args.get("layer_name") or "").strip()
    if not wanted:
        return None
    try:
        from .integration_handoff import raster_layer
        raster_layer(wanted)
        return None
    except ValueError as exc:
        return {
            "error": str(exc),
            "code": "INVALID_ARGS",
            "suggestion": "layer_name is the source imagery ID, not the output layer name.",
        }




def _aiseg_detect_params(fn) -> frozenset | None:

    try:
        import inspect

        return frozenset(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        return None


def _aiseg_zone_outside_refusal(plugin, args: dict) -> dict | None:







    try:

        args = {**args, "layer_name": _aiseg_imagery_name(str(args.get("layer_name") or "").strip())}
        layer = _aiseg_raster_layer(plugin, args["layer_name"])
        provider = layer.dataProvider() if layer is not None else None
        if provider is None or provider.name() != "gdal":
            return None
        wkt = _aiseg_zone_wkt(plugin, args)
        if not wkt:
            return None
        from qgis.core import QgsGeometry

        zone = QgsGeometry.fromWkt(wkt)
        extent = layer.extent()
        if zone is None or zone.isEmpty() or extent.isEmpty() or zone.boundingBox().intersects(extent):
            return None
    except Exception:  # noqa: BLE001
        return None
    return {
        "error": (f"The zone lies outside '{layer.name()}': that raster covers none of it, so the run "
                  "would read blank tiles and AI Segmentation refuses it. "
                  + _aiseg_zone_facts(plugin, args, layer, zone)),
        "code": "INVALID_ARGS",
        "suggestion": ("Online imagery (a satellite basemap) covers any zone; a local raster covers only its "
                       "own extent."),
    }


def _aiseg_zone_facts(plugin, args: dict, layer, zone) -> str:







    try:
        from qgis.core import QgsCoordinateTransform, QgsProject, QgsRectangle

        from . import cost_guard
        layer_crs = layer.crs()
        if str(args.get("zone_wkt") or "").strip():
            read_in, box, extent = layer_crs, zone.boundingBox(), layer.extent()
        else:
            read_in = cost_guard._canvas_crs()
            box = cost_guard.zone_from_args(args)
            box = box.boundingBox() if box is not None else None
            extent = layer.extent()
            if read_in is not None and read_in.isValid() and layer_crs.isValid() and read_in != layer_crs:
                extent = QgsCoordinateTransform(layer_crs, read_in, QgsProject.instance()).transformBoundingBox(extent)
        if box is None or read_in is None or not read_in.isValid():
            return ""

        def fmt(r: QgsRectangle) -> str:
            return f"[{r.xMinimum():.8g}, {r.yMinimum():.8g}, {r.xMaximum():.8g}, {r.yMaximum():.8g}]"

        return (f"The zone was read in {read_in.authid() or read_in.description()}"
                f"{' (the map canvas CRS, as bbox and use_canvas_extent are)' if not args.get('zone_wkt') else ''}"
                f" as {fmt(box)} (xmin, ymin, xmax, ymax); the raster covers {fmt(extent)} in that CRS.")
    except Exception:  # noqa: BLE001
        return ""


def aiseg_argument_refusal(args: dict) -> dict | None:










    if args.get("action") != "detect_auto":
        return None
    try:
        _, plugin = _find_plugin(AISEG_KEYS)
        from .integration_handoff import segmentation_arguments
        args = segmentation_arguments(plugin, args)
    except ValueError as exc:
        return {"error": str(exc), "code": "INVALID_ARGS"}
    refusal = _aiseg_imagery_refusal(args)
    if refusal is not None:
        return refusal
    object_class = str(args.get("object_class") or "").strip()
    exemplars = args.get("exemplars")
    if not object_class and not exemplars:
        return {"error": "Needs object_class or exemplars.", "code": "INVALID_ARGS",
                "suggestion": "A class token (e.g. 'building'), or exemplar boxes for reference-image mode."}
    try:
        _, plugin = _find_plugin(AISEG_KEYS)
    except Exception:  # noqa: BLE001
        return None
    api = getattr(plugin, "mcp_api", None) if plugin else None
    fn = getattr(api, "detect_auto", None) if api is not None else None
    if fn is None:
        return None
    if object_class and not exemplars:
        refusal = _aiseg_class_preflight(api, object_class, args)
        if refusal is not None:
            return refusal
    params = _aiseg_detect_params(fn)
    if exemplars and params is not None and "exemplars" not in params:
        return {"error": "This AI Segmentation version does not support reference-image exemplars.",
                "code": "INVALID_ARGS", "suggestion": "QGIS Plugin Manager has an update."}
    if args.get("refine") and params is not None and "refine" not in params:
        return {"error": "This AI Segmentation version cannot retain refinement settings for this run.",
                "code": "INVALID_ARGS", "suggestion": (
                    "Update AI Segmentation from QGIS Plugin Manager, "
                    "or omit refine to use its own settings.")}

    if (params is None or "wait" not in params) and cost_guard.detaches(args):
        area = cost_guard.zone_area_km2(args, cost_guard.SEGMENTATION) or 0.0
        return {
            "error": (f"This zone is {area:,.1f} km² and the installed AI Segmentation can only "
                      "run a sweep the caller waits on, which it gives up and cancels after "
                      "about 5 minutes. A zone this size needs a newer build."),
            "code": "INVALID_ARGS",
            "suggestion": ("QGIS Plugin Manager updates AI Segmentation; a zone of a few km² "
                           "also finishes inside that window."),
        }
    return _aiseg_zone_outside_refusal(plugin, args)


def _aiseg_detect_auto(args: dict) -> dict:
    try:
        _, plugin = _find_plugin(AISEG_KEYS)
        if not plugin:
            return {"_error": "AI Segmentation plugin is not installed."}

        api = getattr(plugin, "mcp_api", None)
        if api is None:
            return {"_error": "AI Segmentation needs updating for MCP support, from QGIS Plugin Manager."}

        fn = getattr(api, "detect_auto", None)
        if fn is None:
            return _aiseg_outdated(plugin, "detect_auto")

        from .integration_handoff import segmentation_arguments
        args = segmentation_arguments(plugin, args)




        object_class = (args.get("object_class") or "").strip()
        exemplars = args.get("exemplars")
        wanted = (args.get("layer_name") or "").strip()
        if wanted and not callable(getattr(api, "get_interactive_state", None)):
            args = {**args, "layer_name": _aiseg_imagery_name(wanted)}

        kwargs = {
            "zone_wkt": _aiseg_zone_wkt(plugin, args),
            "object_class": object_class,
            "layer_name": args.get("layer_name"),
        }
        params = _aiseg_detect_params(fn)


        if exemplars is not None:
            kwargs["exemplars"] = exemplars




        detached = params is not None and "wait" in params
        if detached:
            kwargs["wait"] = False




        detail = args.get("detail")
        if detail is not None and params is not None and "detail" in params:
            try:
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




        if args.get("refine"):
            kwargs["refine"] = {
                "ortho" if key == "right_angles" else key: value
                for key, value in args["refine"].items()
            }



        shown = _setup.show_panel("ai_segment")
        result = fn(**kwargs)

        if shown and isinstance(result, dict) and "_error" not in result:
            result["panel_opened"] = True
            result["tell_user"] = ("The AI Segmentation panel is open beside the chat: the run, its "
                                   "progress and what it costs are shown there.")
        if detached and isinstance(result, dict) and "_error" not in result and result.get("running"):
            result["poll"] = {
                "tool": "ai_segment", "args": {"action": "auto_status"},
                "interval_s": 3, "timeout_s": 900,
            }
            if result.get("run_id"):
                result["poll"]["args"]["run_id"] = result["run_id"]
            result["tell_user"] = ("The sweep is running in the AI Segmentation panel, showing "
                                   "the tiles, progress and cost. Its result is available after "
                                   "processing and saving finish; another run would spend credits again.")
        return result
    except Exception as e:
        log_warning(f"AI Segmentation detect_auto failed: {e}")
        return {"_error": f"AI Segmentation detect_auto failed: {str(e)}"}


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
                    "review; 'confidence' works directly on ai_segment_detect_auto, or a detection kept open "
                    "in the panel."
                )
            }





        _show_sibling("ai_segment")
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
        _show_sibling("ai_segment")
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


def _aiseg_progress_sentence(status: dict) -> str:









    if not isinstance(status, dict) or not status.get("running"):
        return ""
    prog = status.get("progress")
    if not isinstance(prog, dict):
        return ""
    found = prog.get("found_so_far")
    word = prog.get("object") or ""
    parts = []
    if isinstance(found, int):


        parts.append(f"{found} found so far")
    pct = prog.get("percent")
    if isinstance(pct, int):
        parts.append(f"{pct}% done")
    left = prog.get("time_left")
    if left:



        text = str(left).strip()
        parts.append(text[:1].lower() + text[1:])
    elapsed = prog.get("elapsed")
    if elapsed and not left:
        parts.append(f"running for {elapsed}")
    if not parts:
        return ""
    lead = f"Still sweeping for {word}" if word else "Still sweeping"
    return f"{lead}: " + ", ".join(parts) + "."


def _aiseg_auto_status(args: dict) -> dict:
    try:
        _, plugin = _find_plugin(AISEG_KEYS)
        if not plugin:
            return {"_error": "AI Segmentation plugin is not installed."}

        api = getattr(plugin, "mcp_api", None)
        if api is None:
            return {"_error": "AI Segmentation needs updating for MCP support, from QGIS Plugin Manager."}

        fn = getattr(api, "auto_detect_status", None)
        if fn is None:
            return _aiseg_outdated(plugin, "auto_detect_status")

        kwargs = {}
        if args.get("run_id"):
            params = _aiseg_detect_params(fn)
            if params is not None and "run_id" not in params:
                return _aiseg_outdated(plugin, "auto_detect_status(run_id)")
            kwargs["run_id"] = args["run_id"]
        status = fn(**kwargs)
        if isinstance(status, dict) and not status.get("_error") \
                and (status.get("running") or status.get("finishing")) and status.get("run_id"):
            status["poll"] = {
                "tool": "ai_segment", "args": {"action": "auto_status", "run_id": status["run_id"]},
                "interval_s": 3, "timeout_s": 900,
            }
        sentence = _aiseg_progress_sentence(status)
        if sentence:
            status["tell_user"] = sentence
        return status
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
            return {"_error": "AI Segmentation needs updating for MCP support, from QGIS Plugin Manager."}

        fn = getattr(api, "cancel_auto", None)
        if fn is None:
            return _aiseg_outdated(plugin, "cancel_auto")

        return fn()
    except Exception as e:
        log_warning(f"AI Segmentation cancel_auto failed: {e}")
        return {"_error": f"AI Segmentation cancel_auto failed: {str(e)}"}
