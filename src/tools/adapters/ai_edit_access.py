# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later














from __future__ import annotations

import importlib
import time
from typing import Any

from ...core.logger import log_warning
from .._widgets import AI_EDIT_KEYS, process_events


RESOLUTION_LABELS = ("1K", "2K", "4K")
DEFAULT_RESOLUTION = "1K"




OVERLAY_POLL_MS = 1500
OVERLAY_IDLE_TICKS = 2
OVERLAY_WATCH_SECONDS = 1200


def _find_plugin() -> tuple[str | None, Any]:

    import qgis.utils
    for key in AI_EDIT_KEYS:
        inst = qgis.utils.plugins.get(key)
        if inst is not None:
            return key, inst
    return None, None


def _attr(inst, *names):

    if inst is None:
        return None
    for name in names:
        if hasattr(inst, name):
            return getattr(inst, name)
    return None


def _module_func(inst, name):

    try:
        mod = importlib.import_module(type(inst).__module__)
        return getattr(mod, name, None)
    except Exception:
        return None


def _plugin_module(inst, dotted: str):



    if inst is None:
        return None
    try:
        base = type(inst).__module__.rsplit(".", 2)[0]
        return importlib.import_module(base + "." + dotted)
    except Exception:  # noqa: BLE001
        return None


def _covers_zone(inst, layer, zone_extent, zone_crs) -> bool:










    renderer = _plugin_module(inst, "ui.layer_renderer")
    misses = getattr(renderer, "layer_misses_zone", None) if renderer else None
    if not callable(misses):
        return True
    try:
        return not bool(misses([layer], zone_extent, zone_crs))
    except Exception:  # noqa: BLE001
        return True


def _whitelist_usage(raw: dict) -> dict:

    if not isinstance(raw, dict):
        return {}
    return {
        "used": raw.get("images_used"),
        "limit": raw.get("images_limit"),
        "is_free": raw.get("is_free_tier"),
        "error": raw.get("error"),
        "code": raw.get("code"),
    }









KNOWN_BASEMAP_XYZ = {
    "openstreetmap": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
}


def _basemap_url(name: str) -> str:










    key = (name or "").strip().lower()
    shipped = KNOWN_BASEMAP_XYZ.get(key, "")
    try:
        from ...core.catalog import basemaps

        rows = basemaps().items()
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Basemap catalog unreadable, keeping the shipped list: {exc}")
        return shipped
    for row_id, row in rows:

        if row.get("kind") == "vectortile":
            continue
        if str(row_id).strip().lower() == key or str(row.get("name") or "").strip().lower() == key:
            url = str(row.get("url") or "")
            if url:
                return url
    return shipped


def _find_project_layer(name: str):

    from qgis.core import QgsProject
    if not name:
        return None
    target = name.strip().lower()
    best = None
    for lyr in QgsProject.instance().mapLayers().values():
        ln = (lyr.name() or "").strip().lower()
        if ln == target:
            return lyr
        if best is None and target and target in ln:
            best = lyr
    return best


def _ensure_basemap_layer(name: str):



    found = _find_project_layer(name)
    if found is not None:
        return found
    url = _basemap_url(name)
    if not url:
        return None
    from qgis.core import QgsProject, QgsRasterLayer

    from ...core.provider_uri import encode_uri_url



    uri = f"type=xyz&url={encode_uri_url(url)}&zmin=0&zmax=21"
    layer = QgsRasterLayer(uri, name, "wms")
    if not layer.isValid():
        return None
    QgsProject.instance().addMapLayer(layer, True)
    return layer


class AiEditAccess:







    def __init__(self):


        self._overlay_armed = False
        self._overlay_timer = None
        self._overlay_deadline = 0.0
        self._overlay_idle_ticks = 0



    def instance(self) -> tuple[str | None, Any]:
        return _find_plugin()

    def is_loaded(self) -> bool:
        _, inst = _find_plugin()
        return inst is not None

    def auth(self, inst):
        return _attr(inst, "_auth_manager", "auth_manager")

    def client(self, inst):
        return _attr(inst, "_client", "client")

    def dock(self, inst):
        return _attr(inst, "_dock_widget", "dock_widget")

    def _mcp_api(self, inst):

        return getattr(inst, "mcp_api", None) if inst is not None else None



    def signed_in(self, inst) -> bool:
        auth = self.auth(inst)
        fn = getattr(auth, "has_activation_key", None) if auth is not None else None
        try:
            return bool(fn()) if callable(fn) else False
        except Exception:
            return False

    def is_free_tier(self, inst) -> bool | None:
        dock = self.dock(inst)
        if dock is None:
            return None
        val = getattr(dock, "_is_free_tier", None)
        return bool(val) if val is not None else None

    def status(self) -> dict:

        key, inst = _find_plugin()
        if inst is None:
            return {"installed": False, "ready": False, "state": "NOT_INSTALLED"}

        api = self._mcp_api(inst)
        if api is not None and hasattr(api, "get_status"):
            try:
                return dict(api.get_status())
            except Exception:  # nosec B110
                pass

        signed = self.signed_in(inst)
        is_free = self.is_free_tier(inst)
        out: dict[str, Any] = {
            "installed": True,
            "ready": signed,
            "state": "READY" if signed else "NEEDS_ACTIVATION",
            "plan": ("free" if is_free else "pro") if is_free is not None else "",
            "is_free_tier": is_free,
        }
        if not signed:
            out["action_required"] = "Activate AI Edit: paste a TerraLab key in the AI Edit dock."
        usage = self.credits()
        if "_error" not in usage:
            out["usage"] = usage
        return out



    def presets(self) -> dict:

        key, inst = _find_plugin()
        if inst is None:
            return {"_error": "AI Edit plugin is not installed."}

        api = self._mcp_api(inst)
        if api is not None and hasattr(api, "get_presets"):
            try:
                return dict(api.get_presets())
            except Exception:  # nosec B110
                pass



        get_all = _module_func(inst, "get_all_categories")
        if not callable(get_all):

            try:
                mod = importlib.import_module(
                    type(inst).__module__.rsplit(".", 2)[0] + ".core.prompts.prompt_presets"
                )
                get_all = getattr(mod, "get_all_categories", None)
            except Exception:  # nosec B110
                get_all = None
        if not callable(get_all):
            return {"_error": "AI Edit preset catalog is not available."}

        dock = self.dock(inst)
        catalog = getattr(dock, "_server_catalog", None) if dock is not None else None
        try:
            categories = get_all(server_catalog=catalog)
        except Exception as err:
            return {"_error": f"Failed to read presets: {err}"}
        return {"categories": categories, "catalog_loaded": catalog is not None}



    def credits(self) -> dict:

        key, inst = _find_plugin()
        if inst is None:
            return {"_error": "AI Edit plugin is not installed."}

        api = self._mcp_api(inst)
        if api is not None and hasattr(api, "get_credits"):
            try:
                return dict(api.get_credits())
            except Exception:  # nosec B110
                pass

        auth = self.auth(inst)
        if auth is None or not hasattr(auth, "get_usage_info"):
            return {"_error": "AI Edit usage source is not available."}
        try:
            return _whitelist_usage(auth.get_usage_info())
        except Exception as err:
            return {"_error": f"Failed to read credits: {err}"}



    def resolutions(self) -> dict:

        key, inst = _find_plugin()
        if inst is None:
            return {"_error": "AI Edit plugin is not installed."}

        api = self._mcp_api(inst)
        if api is not None and hasattr(api, "get_resolutions"):
            try:
                return dict(api.get_resolutions())
            except Exception:  # nosec B110
                pass

        dock = self.dock(inst)
        costs = getattr(dock, "_resolution_credit_costs", None) if dock is not None else None
        is_free = self.is_free_tier(inst)
        current = None
        if dock is not None and hasattr(dock, "get_selected_resolution"):
            try:
                current = dock.get_selected_resolution()
            except Exception:
                current = None
        return {
            "resolutions": list(RESOLUTION_LABELS),
            "credit_costs": dict(costs) if isinstance(costs, dict) else None,
            "current": current,
            "is_free_tier": is_free,
            "free_tier_locked_to": DEFAULT_RESOLUTION if is_free else None,
        }



    def is_busy(self, inst) -> bool:

        worker = _attr(inst, "_worker")
        if worker is not None and hasattr(worker, "is_active"):
            try:
                if worker.is_active():
                    return True
            except Exception:  # nosec B110
                pass
        return _attr(inst, "_pending_generation") is not None

    def apply_resolution(self, inst, label: str) -> dict:






        label = (label or DEFAULT_RESOLUTION).strip()
        if label not in RESOLUTION_LABELS:
            return {"_error": f"Unknown resolution '{label}'. Allowed: {', '.join(RESOLUTION_LABELS)}."}

        is_free = self.is_free_tier(inst)
        if is_free and label != DEFAULT_RESOLUTION:
            return {
                "_error": (
                    f"Resolution '{label}' requires a paid plan. "
                    f"Free tier is limited to {DEFAULT_RESOLUTION}."
                ),
                "plan_restricted": True,
            }

        dock = self.dock(inst)
        setter = getattr(dock, "_on_resolution_selected", None) if dock is not None else None
        if not callable(setter):

            return {"resolution": label, "applied": False}
        try:
            setter(label)
        except Exception as err:
            return {"_error": f"Failed to set resolution: {err}"}

        applied = label
        if hasattr(dock, "get_selected_resolution"):
            try:
                applied = dock.get_selected_resolution()
            except Exception:
                applied = label
        return {"resolution": applied, "applied": True}

    def current_resolution(self, inst) -> str:





        dock = self.dock(inst)
        getter = getattr(dock, "get_selected_resolution", None) if dock is not None else None
        if callable(getter):
            try:
                label = getter()
                if label:
                    return str(label)
            except Exception:  # noqa: BLE001  # nosec B110
                pass
        return DEFAULT_RESOLUTION

    def write_prompt(self, inst, prompt: str) -> bool:







        dock = self.dock(inst)
        box = _attr(dock, "_prompt_input", "prompt_input") if dock is not None else None
        setter = getattr(box, "setPlainText", None) if box is not None else None
        if not callable(setter):
            return False
        try:
            setter(prompt)
            process_events()
            return True
        except Exception as err:  # noqa: BLE001
            log_warning(f"AI Edit prompt box not written: {err}")
            return False

    def attach_reference_layers(self, inst, names, zone_extent, zone_crs) -> dict:









        dock = self.dock(inst)
        if dock is None:
            return {"_error": "AI Edit dock is not available for references."}
        widget = _attr(dock, "_reference_widget", "reference_widget")
        if widget is None:
            return {"_error": "AI Edit reference widget is not available."}


        try:
            if hasattr(dock, "set_reference_target_extent"):
                dock.set_reference_target_extent(zone_extent, zone_crs)
            elif hasattr(widget, "set_target_extent"):
                widget.set_target_extent(zone_extent, zone_crs)
        except Exception:  # nosec B110
            pass
        try:
            if hasattr(widget, "clear"):
                widget.clear()
        except Exception:  # nosec B110
            pass






        drop_empty = {}
        try:
            import inspect
            if "drop_if_empty" in inspect.signature(widget.add_layers).parameters:
                drop_empty = {"drop_if_empty": True}
        except (TypeError, ValueError):
            pass

        attached, missing, skipped, renamed = [], [], [], []
        for name in names:
            layer = _ensure_basemap_layer(name)
            if layer is None:
                missing.append(name)
                continue
            real = layer.name()



            if (real or "").strip().lower() != (name or "").strip().lower():
                renamed.append({"asked": name, "used": real})
            if not _covers_zone(inst, layer, zone_extent, zone_crs):
                skipped.append(real)
                continue
            try:
                widget.add_layers([layer], **drop_empty)
                attached.append(real)
            except Exception as err:
                missing.append(f"{name} ({err})")
        store = _attr(inst, "_reference_store", "reference_store")
        count = store.count() if store is not None and hasattr(store, "count") else len(attached)


        if drop_empty and isinstance(count, int) and count < len(attached):
            skipped.extend(attached[count:])
            attached = attached[:count]
        out = {"attached": attached, "missing": missing, "count": count}
        if skipped:
            out["skipped_off_zone"] = skipped
        if renamed:
            out["matched_by_name"] = renamed
        return out

    def run_generation(self, params: dict) -> dict:











        params = params or {}
        key, inst = _find_plugin()
        if inst is None:
            return {"_error": "AI Edit plugin is not installed."}

        prompt = (params.get("prompt") or "").strip()
        if not prompt:
            return {"_error": "prompt is required"}






        if self.is_busy(inst):
            return {
                "_error": "AI Edit is already generating; ai_edit_generation_status reports when idle.",
                "busy": True,
            }

        extent = self._resolve_extent(params)
        if isinstance(extent, dict):
            return extent


        outline = self._zone_of_interest() if params.get("use_zone") else None







        res_label = params.get("resolution")
        if res_label:
            res_result = self.apply_resolution(inst, res_label)
            if "_error" in res_result:
                return res_result
            applied_resolution = res_result.get("resolution", res_label)
        else:
            applied_resolution = self.current_resolution(inst)






        template_info = None
        template_id = params.get("template_id")
        if template_id:
            template_info = self.arm_template(inst, str(template_id))






        submitted = False
        try:
            zone = _attr(inst, "_on_zone_selected")
            if callable(zone):
                try:





                    if outline is not None:
                        try:
                            zone(extent, outline)
                        except TypeError:
                            zone(extent)
                    else:
                        zone(extent)
                except Exception as err:
                    return {"_error": f"Zone selection failed: {err}"}
            else:
                inst._selected_extent = extent
                if outline is not None:
                    inst._selected_polygon = outline







            ref_info = None
            ref_names = params.get("reference_layers")
            if ref_names:
                if isinstance(ref_names, str):
                    ref_names = [ref_names]
                try:
                    from qgis.utils import iface
                    zcrs = iface.mapCanvas().mapSettings().destinationCrs()
                except Exception:
                    from qgis.core import QgsProject
                    zcrs = QgsProject.instance().crs()
                ref_info = self.attach_reference_layers(inst, ref_names, extent, zcrs)
                if "_error" in ref_info:
                    return ref_info







            written = self.write_prompt(inst, prompt)

            generate = _attr(inst, "_on_generate")
            if not callable(generate):
                return {"_error": "AI Edit generation entry point is not available."}
            try:
                generate(prompt)
            except Exception as err:
                return {"_error": f"Generation start failed: {err}"}
            process_events()
            submitted = True



            result = {
                "ok": True,
                "status": "submitted",
                "prompt_len": len(prompt),
                "resolution": applied_resolution,
                "note": "Generation runs async; ai_edit_generation_status reports it.",
            }
            result["resolution_source"] = "call" if res_label else "panel"
            result["prompt_shown_in_panel"] = written
            if not written:



                result["tell_user"] = (
                    "AI Edit's prompt box could not be written, so the panel does not show "
                    f'the request. Quote it to the user: "{prompt}".'
                )
            if ref_info is not None:
                result["references_attached"] = ref_info.get("attached")
                result["references_count"] = ref_info.get("count")
                if ref_info.get("missing"):
                    result["references_missing"] = ref_info.get("missing")
                if ref_info.get("skipped_off_zone"):
                    result["references_skipped_off_zone"] = ref_info["skipped_off_zone"]
            if template_info is not None:
                result["template"] = template_info
            return result
        finally:
            if submitted:
                self._arm_overlay_cleanup()
            else:
                self.clear_zone_overlay(inst)



    def clear_zone_overlay(self, inst=None) -> dict:

















        self._overlay_armed = False
        self._stop_overlay_watch()
        if inst is None:
            _, inst = _find_plugin()
        if inst is None:
            return {"cleared": False, "reason": "AI Edit plugin is not installed."}

        cleared: list[str] = []
        clear_rect = _attr(inst, "_clear_selection_rectangle")
        if callable(clear_rect):
            try:
                clear_rect()
                cleared.append("outline")
            except Exception as err:
                log_warning(f"AI Edit zone outline teardown failed: {err}")
        tool = _attr(inst, "_map_tool")
        set_has_zone = getattr(tool, "set_has_zone", None) if tool is not None else None
        if callable(set_has_zone):
            try:
                set_has_zone(False)
                cleared.append("badge")
            except Exception as err:
                log_warning(f"AI Edit zone badge teardown failed: {err}")
        for name in ("_selected_extent", "_selected_polygon"):
            try:
                setattr(inst, name, None)
            except Exception as err:
                log_warning(f"AI Edit {name} reset failed: {err}")
        try:
            inst._pills_armed = False
        except Exception as err:
            log_warning(f"AI Edit pill disarm failed: {err}")
        process_events()
        return {"cleared": bool(cleared), "parts": cleared}

    def _arm_overlay_cleanup(self) -> None:








        self._overlay_armed = True
        self._overlay_idle_ticks = 0
        self._overlay_deadline = time.monotonic() + OVERLAY_WATCH_SECONDS
        if self._overlay_timer is not None:
            return
        try:
            from qgis.PyQt.QtCore import QTimer

            timer = QTimer()
            timer.setInterval(OVERLAY_POLL_MS)
            timer.timeout.connect(self._overlay_tick)
            timer.start()
            self._overlay_timer = timer
        except Exception as err:

            self._overlay_timer = None
            log_warning(f"AI Edit overlay watchdog unavailable: {err}")

    def _overlay_tick(self) -> None:



        if not self._overlay_armed:
            self._stop_overlay_watch()
            return
        _, inst = _find_plugin()
        if inst is None:
            self._overlay_armed = False
            self._stop_overlay_watch()
            return
        if self.is_busy(inst):
            self._overlay_idle_ticks = 0
            if time.monotonic() > self._overlay_deadline:


                self.clear_zone_overlay(inst)
            return
        self._overlay_idle_ticks += 1
        if self._overlay_idle_ticks >= OVERLAY_IDLE_TICKS:
            self.clear_zone_overlay(inst)

    def _stop_overlay_watch(self) -> None:
        timer, self._overlay_timer = self._overlay_timer, None
        if timer is None:
            return
        try:
            timer.stop()
            timer.timeout.disconnect(self._overlay_tick)
        except Exception as err:
            log_warning(f"AI Edit overlay watchdog stop failed: {err}")

    def _resolve_extent(self, params: dict):

        from qgis.core import QgsRectangle
        from qgis.utils import iface

        if params.get("use_zone"):
            found = self._zone_of_interest()
            if found is None:
                return {"_error": ("This project holds no zone of interest. zone action set makes one from a "
                                   "layer the user named; bbox or use_canvas_extent also works.")}
            return QgsRectangle(found.boundingBox())
        if params.get("use_canvas_extent"):
            return iface.mapCanvas().extent()

        bbox = params.get("bbox")
        if isinstance(bbox, dict):
            try:
                return QgsRectangle(
                    float(bbox["xmin"]), float(bbox["ymin"]),
                    float(bbox["xmax"]), float(bbox["ymax"]),
                )
            except (KeyError, TypeError, ValueError):
                return {"_error": "bbox dict needs numeric xmin, ymin, xmax, ymax."}
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            try:
                return QgsRectangle(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
            except (TypeError, ValueError):
                return {"_error": "bbox must be 4 numbers [xmin,ymin,xmax,ymax]."}
        return {"_error": "Needs bbox [xmin,ymin,xmax,ymax] (or {xmin,...}) or use_canvas_extent:true."}

    @staticmethod
    def _zone_of_interest():






        from qgis.core import QgsProject
        from qgis.utils import iface

        from ...core import zone_of_interest
        held = zone_of_interest.read_zone()
        if held is None:
            return None
        try:
            target = iface.mapCanvas().mapSettings().destinationCrs()
        except Exception:  # noqa: BLE001
            target = QgsProject.instance().crs()
        return zone_of_interest.to_crs(held.geometry, held.crs, target)

    def generation_status(self) -> dict:

        key, inst = _find_plugin()
        if inst is None:
            return {"_error": "AI Edit plugin is not installed."}

        from qgis.core import QgsProject

        dock = self.dock(inst)
        status_text = ""
        if dock is not None:
            lbl = getattr(dock, "_status_label", None) or getattr(dock, "status_label", None)
            if lbl is not None and hasattr(lbl, "text"):
                try:
                    status_text = lbl.text()
                except Exception:
                    status_text = ""

        current_resolution = None
        if dock is not None and hasattr(dock, "get_selected_resolution"):
            try:
                current_resolution = dock.get_selected_resolution()
            except Exception:
                current_resolution = None

        busy = self.is_busy(inst)
        last_request_id = _attr(inst, "_last_completed_request_id")


        if not busy and self._overlay_armed:
            self.clear_zone_overlay(inst)







        err = str(_attr(inst, "_last_generation_error") or "")
        err_code = str(_attr(inst, "_last_generation_error_code") or "")

        if busy:
            state = "generating"
        elif err:
            state = "failed"
        elif last_request_id:
            state = "done"
        else:
            state = "idle"

        out = {
            "in_flight": busy,
            "state": state,
            "dock_status": status_text,
            "result_layers": self._result_layer_names(QgsProject.instance()),
            "current_resolution": current_resolution,
            "last_completed_request_id": last_request_id,
        }
        if state == "failed":
            out["error"] = err
            if err_code:
                out["error_code"] = err_code
            out["tell_user"] = f"The last AI Edit generation failed: {err}"
        return out

    def _result_layer_names(self, project) -> list[str]:


        try:
            root = project.layerTreeRoot()
        except Exception:
            return []
        group = self._find_group(root, "AI-Edit")
        if group is None:
            return []
        names: list[str] = []
        try:
            for child in group.findLayers():
                lyr = child.layer()
                if lyr is not None:
                    names.append(lyr.name())
        except Exception:
            return []
        return names[-10:]

    def _find_group(self, node, name: str):


        try:
            from qgis.core import QgsLayerTreeGroup
        except Exception:
            return None
        for child in getattr(node, "children", lambda: [])():
            if isinstance(child, QgsLayerTreeGroup):
                if child.name() == name:
                    return child
                found = self._find_group(child, name)
                if found is not None:
                    return found
        return None



    def _aiedit_submodule(self, inst, dotted: str):




        base = type(inst).__module__.rsplit(".", 2)[0]
        return importlib.import_module(base + "." + dotted)



    def arm_template(self, inst, template_id: str) -> dict:




        dock = self.dock(inst)
        if dock is None or not hasattr(dock, "get_active_template"):
            return {"_warning": "Template arming unavailable (no dock or old AI Edit)."}
        name = ""
        try:
            for cat in (self.presets() or {}).get("categories") or []:
                for p in (cat.get("presets") or []):
                    if str(p.get("id")) == template_id:
                        name = str(p.get("label") or p.get("name") or "")
                        break
                if name:
                    break
        except Exception:  # nosec B110
            pass
        try:
            dock._active_template_id = template_id
            dock._active_template_name = name or None
        except Exception as err:
            return {"_warning": f"Template arming failed: {err}"}
        armed = None
        try:
            armed = dock.get_active_template()
        except Exception:  # nosec B110
            armed = None
        return {"template_id": template_id, "template_name": name, "armed": bool(armed)}



    def cancel(self, exit_session: bool = False) -> dict:




        key, inst = _find_plugin()
        if inst is None:
            return {"_error": "AI Edit plugin is not installed."}
        was_busy = self.is_busy(inst)
        if exit_session:
            fn = _attr(inst, "_on_exit_clicked")
            if not callable(fn):
                return {"_error": "AI Edit exit handler (_on_exit_clicked) is not available."}
            action = "exit"
        else:
            fn = _attr(inst, "_on_stop")
            if not callable(fn):
                return {"_error": "AI Edit stop handler (_on_stop) is not available."}
            action = "stop"
        try:
            fn()
        except Exception as err:
            return {"_error": f"AI Edit {action} failed: {err}"}
        process_events()



        self.clear_zone_overlay(inst)
        return {
            "ok": True,
            "action": action,
            "was_generating": bool(was_busy),
            "in_flight": self.is_busy(inst),
        }



    def _version_strip(self, inst):
        dock = self.dock(inst)
        return getattr(dock, "_version_strip", None) if dock is not None else None

    def list_versions(self) -> dict:

        key, inst = _find_plugin()
        if inst is None:
            return {"_error": "AI Edit plugin is not installed."}
        strip = self._version_strip(inst)
        if strip is None:
            return {"_error": "AI Edit version strip not available; a generation makes it."}
        try:
            count = int(strip.count())
            sel = int(strip.selected_index())
        except Exception as err:
            return {"_error": f"Failed to read version strip: {err}"}
        tiles = getattr(strip, "_tiles", []) or []
        versions = []
        for i in range(count):
            try:
                label = strip.label_for(i)
            except Exception:
                label = "Original" if i == 0 else f"V{i}"
            prompt = getattr(tiles[i], "_prompt", "") if i < len(tiles) else ""
            versions.append({
                "index": i, "label": label,
                "prompt": prompt or "", "selected": i == sel,
            })
        return {"count": count, "selected_index": sel, "versions": versions}

    def select_version(self, index) -> dict:



        key, inst = _find_plugin()
        if inst is None:
            return {"_error": "AI Edit plugin is not installed."}
        dock = self.dock(inst)
        strip = self._version_strip(inst)
        if dock is None or strip is None:
            return {"_error": "AI Edit version strip not available; a generation makes it."}
        try:
            count = int(strip.count())
        except Exception:
            count = 0
        if count <= 0:
            return {"_error": "No versions yet; a generation creates one."}
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return {"_error": "index must be an integer."}
        if idx < 0 or idx >= count:
            return {"_error": f"index out of range (valid 0..{count - 1}).", "count": count}
        try:
            if hasattr(dock, "select_version"):
                dock.select_version(idx)
            handler = getattr(dock, "_on_version_selected", None)
            if callable(handler):
                handler(idx)
        except Exception as err:
            return {"_error": f"Version selection failed: {err}"}
        process_events()
        try:
            label = strip.label_for(idx)
        except Exception:
            label = None
        listing = self.list_versions()
        return {
            "ok": True,
            "selected_index": idx,
            "label": label,
            "versions": listing.get("versions"),
        }



    def _newest_result_raster(self, project):

        from qgis.core import QgsRasterLayer
        try:
            root = project.layerTreeRoot()
        except Exception:
            return None
        group = self._find_group(root, "AI-Edit")
        if group is None:
            return None
        raster = None
        try:
            for child in group.findLayers():
                lyr = child.layer()
                if isinstance(lyr, QgsRasterLayer):
                    raster = lyr
        except Exception:
            return None
        return raster

    def vectorize(self, params: dict) -> dict:



        params = params or {}
        key, inst = _find_plugin()
        if inst is None:
            return {"_error": "AI Edit plugin is not installed."}
        from qgis.core import QgsProject, QgsRasterLayer

        layer_name = (params.get("layer_name") or "").strip()
        if layer_name:
            raster = _find_project_layer(layer_name)
            if raster is None:
                return {"_error": f"No project layer named '{layer_name}'."}
        else:
            raster = self._newest_result_raster(QgsProject.instance())
            if raster is None:
                return {"_error": "No AI Edit result raster in the AI-Edit group; layer_name names one."}
        if not isinstance(raster, QgsRasterLayer):
            return {"_error": f"Layer '{raster.name()}' is not a raster."}

        target_rgb = params.get("target_rgb")
        if not (isinstance(target_rgb, (list, tuple)) and len(target_rgb) == 3):
            return {"_error": "target_rgb must be [r, g, b] with 0-255 values."}
        try:
            rgb = tuple(max(0, min(255, int(c))) for c in target_rgb)
        except (TypeError, ValueError):
            return {"_error": "target_rgb values must be integers 0-255."}

        try:
            svc = self._aiedit_submodule(inst, "core.generation.vectorization_service")
        except Exception as err:
            return {"_error": f"Cannot load AI Edit vectorization service: {err}"}
        vbc = getattr(svc, "vectorize_by_color", None)
        if not callable(vbc):
            return {"_error": "vectorize_by_color is not available in this AI Edit version."}

        kwargs: dict[str, Any] = {}
        if params.get("tolerance") is not None:
            try:
                kwargs["tolerance"] = int(params["tolerance"])
            except (TypeError, ValueError):
                return {"_error": "tolerance must be an integer."}
        if params.get("simplify_factor") is not None:
            try:
                kwargs["simplify_factor"] = float(params["simplify_factor"])
            except (TypeError, ValueError):
                return {"_error": "simplify_factor must be a number."}
        class_label = str(params.get("class_label") or "")
        out_name = f"{raster.name()} vectorized"

        try:
            layer = vbc(raster, rgb, layer_name=out_name, class_label=class_label, **kwargs)
        except Exception as err:

            msg = getattr(err, "message", None) or str(err)
            return {"_error": f"Vectorize failed: {msg}",
                    "_suggestion": "A wider tolerance, target_rgb or simplify_factor may vectorize it."}
        if layer is None or not layer.isValid():
            return {"_error": "Vectorize produced no valid layer."}


        try:
            QgsProject.instance().addMapLayer(layer, False)
            root = QgsProject.instance().layerTreeRoot()
            group = self._find_group(root, "AI-Edit")
            (group or root).addLayer(layer)
        except Exception as err:
            return {"_error": f"Vectorized layer built but adding it failed: {err}"}

        return {
            "ok": True,
            "layer_name": layer.name(),
            "layer_id": layer.id(),
            "feature_count": layer.featureCount(),
            "source_raster": raster.name(),
            "target_rgb": list(rgb),
        }



    def markup(self, action: str, geometry_wkt: str | None = None,
               color: str | None = None) -> dict:





        key, inst = _find_plugin()
        if inst is None:
            return {"_error": "AI Edit plugin is not installed."}
        action = (action or "").strip().lower()
        if action not in ("draw", "clear", "done"):
            return {"_error": "action must be 'draw', 'clear', or 'done'."}

        in_panel = _attr(inst, "_in_tool_panel")


        if action in ("draw", "clear") and in_panel != "markup":
            enter = _attr(inst, "_on_markup_clicked")
            if not callable(enter):
                return {
                    "_error": "AI Edit markup entry (_on_markup_clicked) not available. "
                    "Launch AI Edit and draw a zone first."
                }
            try:
                enter()
            except Exception as err:
                return {"_error": f"Entering markup mode failed: {err}"}
            process_events()

        manager = _attr(inst, "_markup_manager")

        if action == "clear":
            fn = _attr(inst, "_on_markup_clear_clicked")
            try:
                if callable(fn):
                    fn()
                elif manager is not None and hasattr(manager, "clear_all"):
                    manager.clear_all()
                else:
                    return {"_error": "Markup manager unavailable."}
            except Exception as err:
                return {"_error": f"Markup clear failed: {err}"}
            process_events()
            count = manager.annotation_count() if (manager is not None and hasattr(manager, "annotation_count")) else 0
            return {"ok": True, "action": "clear", "annotation_count": count}

        if action == "done":
            fn = _attr(inst, "_on_markup_done_clicked")
            if not callable(fn):
                return {"_error": "AI Edit markup done handler (_on_markup_done_clicked) not available."}
            try:
                fn()
            except Exception as err:
                return {"_error": f"Markup done failed: {err}"}
            process_events()
            return {"ok": True, "action": "done",
                    "note": "Marks composited into the zone reference; dock returned to the prompt state."}


        if not geometry_wkt:
            return {"_error": "geometry_wkt is required for 'draw' (LINESTRING/MULTILINESTRING/POLYGON in canvas CRS)."}
        if manager is None or not hasattr(manager, "commit"):
            return {"_error": "Markup manager unavailable (launch AI Edit and draw a zone first)."}
        from qgis.core import QgsGeometry, QgsWkbTypes
        from qgis.PyQt.QtGui import QColor

        geom = QgsGeometry.fromWkt(str(geometry_wkt))
        if geom is None or geom.isEmpty():
            return {"_error": "Invalid geometry_wkt."}
        shape = "pencil"
        try:
            gtype = geom.type()
            if gtype == QgsWkbTypes.GeometryType.PolygonGeometry:


                line = geom.convertToType(QgsWkbTypes.GeometryType.LineGeometry, False)
                if line is not None and not line.isEmpty():
                    geom = line
                shape = "circle"
            elif gtype == QgsWkbTypes.GeometryType.PointGeometry:
                return {"_error": "A single point can't be a markup stroke; pass a LINESTRING or POLYGON."}
        except Exception:  # nosec B110
            pass

        qcolor = QColor(str(color)) if color else QColor(230, 0, 230)
        if not qcolor.isValid():
            qcolor = QColor(230, 0, 230)
        before = manager.annotation_count() if hasattr(manager, "annotation_count") else None
        try:
            manager.commit(geom, qcolor, shape)
        except Exception as err:
            return {"_error": f"Markup draw failed: {err}"}
        process_events()
        after = manager.annotation_count() if hasattr(manager, "annotation_count") else None
        added = before is None or after is None or after > before
        return {
            "ok": bool(added),
            "action": "draw",
            "shape": shape,
            "annotation_count": after,
            "note": None if added else "Stroke rejected (likely entirely outside the selected zone).",
        }





ACCESS = AiEditAccess()
