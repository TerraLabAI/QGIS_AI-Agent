# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The executor's guards: argument checks, cost and layer ceilings, the CRS guard."""



from __future__ import annotations

import math
import os

from qgis.core import QgsProject, QgsVectorLayer
from qgis.PyQt.QtCore import QCoreApplication

from . import limits, output_paths, tuning
from .logger import log, log_warning
from .protocol import ClientErrorCode as Err
from .protocol import Danger

try:
    from ..tools import cost_guard
except ImportError:
    cost_guard = None

try:
    from ..tools import guards
except ImportError:
    guards = None

try:
    from ..tools import volume_guard
except ImportError:
    volume_guard = None

try:
    from ..tools.danger import effective_danger
except ImportError:
    effective_danger = None





_LAYER_FREE_TOOLS = frozenset({
    "remove_layer", "remove_map_theme", "remove_bookmark", "remove_print_layout",
    "set_layer_visibility", "set_layers_visibility", "set_layer_order", "move_layer_to_group",
    "create_layer_group", "set_canvas_extent", "set_canvas_scale", "zoom_to_layer",
    "zoom_to_selected", "set_layer_style", "set_raster_style", "set_layer_labels", "apply_style_qml",



    "set_layer_crs", "set_project_crs", "set_layer_filter",



    "add_features", "update_features", "delete_features", "update_feature_geometry",
    "select_features", "select_by_attribute", "select_by_geometry", "clear_selection",
})


_LIFTABLE_TOOLS = frozenset({"add_data", "fetch_osm_data", "fetch_overture"})






_LAYERS_PER_ROUND = 4


CODE_TOOL = "execute_code"


BATCH_TOOL = "batch_commands"

_DISTANCE_KEYS = ("DISTANCE", "BUFFER", "RADIUS", "TOLERANCE", "INTERVAL", "MAX_DISTANCE",
                  "OFFSET", "HUB_DISTANCE", "SEGMENT_LENGTH", "NEIGHBOR_DISTANCE")
_METRIC_ALGS = frozenset({
    "native:buffer", "native:bufferbym", "native:singlesidedbuffer", "native:offsetline",
    "native:extractwithindistance", "native:selectwithindistance", "native:joinbynearest",
    "native:pointsalonglines", "native:densifygeometriesgivenaninterval", "native:simplifygeometries",
    "native:smoothgeometry", "native:shortestline", "native:snapgeometries", "native:extendlines",
    "native:arrayoffsetlines", "native:wedgebuffers", "native:taperedbuffer", "native:hublines",
    "qgis:distancetonearesthubpoints", "qgis:distancetonearesthublinetohub",
})


def _metric_algs() -> frozenset:
    """The algorithms this guard covers, widened by the `checks` policy section."""





    return tuning.check_algs("metric_algs", _METRIC_ALGS)


_METRIC_UNITS = frozenset({"meters", "metres", "m", "kilometers", "kilometres", "km", "feet", "ft",
                           "miles", "mi", "yards", "yd"})
_DEGREE_UNITS = frozenset({"degrees", "deg", "degree"})


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def tr(text: str) -> str:
    return QCoreApplication.translate("ToolExecutor", text)


class _ExecutorGuards:


    def _blocked_reason(self, run_id: str, danger: str) -> str:
        """The freeze this run has already had, said once, or ''."""






        sentence = self._blocked.get(run_id)
        if not sentence or danger == Danger.READ:
            return ""
        self._blocked.pop(run_id, None)
        return sentence

    def _layers_over_budget(self, run_id: str, danger: str, name: str = "", args: dict | None = None) -> dict | None:
        """Refuse when this run has already added its allowance of layers."""





        if danger == Danger.READ or name in _LAYER_FREE_TOOLS or run_id not in self._run_mode:
            return None
        adding = [(inner, inner_args) for inner, inner_args in self._calls_in(name, args or {})
                  if inner not in _LAYER_FREE_TOOLS]
        if not adding:


            return None
        if volume_guard is not None and adding and all(volume_guard.lifted(a) for _inner, a in adding):
            return None
        added = self.stacker.added_count()
        cap = max(1, int(limits.current("MAX_LAYERS_PER_RUN")))
        rounds = self._layer_rounds(run_id, added)
        ceiling = cap * _LAYERS_PER_ROUND
        if len(rounds) < cap and added < ceiling:
            return None


        liftable = volume_guard is not None and bool(adding) and all(
            inner in _LIFTABLE_TOOLS for inner, _a in adding)
        over = (f"This run has added layers in {len(rounds)} calls, the cap for one answer ({cap}); "
                f"{added} of them are still in the project."
                if len(rounds) >= cap else
                f"This run has added {added} layers to the project, the cap for one answer ({ceiling}).")
        return {
            "error": (over + " Every layer costs a redraw of the tree and the "
                      "canvas, and a project nobody asked to grow this much is its own kind of damage."
                      + (volume_guard.LIFT_HINT if liftable else "")),



            "suggestion": (("If the user named every layer to add, call again with full_extent: their words and the "
                            "files. " if liftable else "")
                           + "Remove the working layers this run created and no longer needs with remove_layer: "
                           "the cap counts the layers this run added that are still in the project, so that "
                           "frees room immediately and you can carry on. One merged or clipped layer instead "
                           "of one per source works too. Only if every layer is needed, stop, tell the user "
                           "what was added and what is left, and ask whether to continue in a new message."),
        }

    def _layer_rounds(self, run_id: str, added: int) -> list:
        """The layers this run put on the map, grouped by the call that brought them."""
















        held = getattr(self, "_layer_rounds_held", None)
        if held is None or held[0] != run_id:
            held = self._layer_rounds_held = (run_id, [])
        rounds = held[1]
        counted = sum(rounds)
        if added > counted:
            rounds.append(added - counted)
        while added < counted and rounds:
            gone = min(counted - added, rounds[-1])
            rounds[-1] -= gone
            counted -= gone
            if not rounds[-1]:
                rounds.pop()
        return rounds

    def _drop_unused_bbox(self, name: str, args: dict) -> None:
        """A bbox a tool does not take costs nothing, so it must not refuse the call."""






        if "bbox" not in args:
            return
        known = (getattr(self._registry.get_tool(name), "input_schema", None) or {}).get("properties") or {}
        if "bbox" not in known:
            args.pop("bbox", None)

    @staticmethod
    def _resolve_output_paths(name: str, args: dict) -> None:
        """Every write path as the folder it names, before the guards, the card and the handler read it."""





        if guards is None or not isinstance(args, dict):
            return
        try:
            changes = output_paths.resolve_write_paths(name, args, guards.WRITE_PATH_ARGS)
            if name == "run_processing" and effective_danger is not None:
                from ..tools.danger import _processing_output_params
                keys = _processing_output_params(str(args.get("algorithm_id") or ""))
                changes += output_paths.resolve_processing_outputs(args, keys)
            for change in changes:
                log(f"PATH {name}.{change['arg']} -> {change['to']} ({change['how']})")
        except Exception as exc:  # noqa: BLE001 - the path as written still meets every guard
            log_warning(f"output path resolution failed for {name}: {exc}")

    def _drop_unknown_flags(self, name: str, args: dict) -> None:
        """The overwrite flag is read by the guard; a tool whose schema lacks it must not see it."""
        known = (getattr(self._registry.get_tool(name), "input_schema", None) or {}).get("properties") or {}
        for key in (guards.OVERWRITE_KEYS if guards is not None else ()):
            if key in args and key not in known:
                args.pop(key)

    @staticmethod
    def _guard_check(name: str, args: dict) -> dict:
        """The argument guard's verdict, refusing the call when it cannot give one."""








        if guards is None:
            return {"error": "The argument guards are not available, so no tool call can be checked.",
                    "code": "EXECUTION_FAILED",
                    "suggestion": "Tell the user to reload the AI Agent plugin, and to report this if it persists."}
        try:
            return guards.check_call(name, args)
        except Exception as exc:  # noqa: BLE001 - a guard that cannot decide must not wave the call through
            log_warning(f"argument guard failed for {name}: {exc}")
            return {"error": f"The arguments of {name} could not be checked, so the call was not run.",
                    "code": "EXECUTION_FAILED",
                    "suggestion": "Try a simpler form of the call, and tell the user if it keeps failing."}

    @staticmethod
    def _costly_check(name: str, args: dict) -> dict:
        """The cost guard's verdict, then the volume guard's."""




        for guard in (cost_guard, volume_guard):
            if guard is None:
                continue
            try:
                verdict = guard.check(name, args)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"{guard.__name__.rsplit('.', 1)[-1]} failed for {name}: {exc}")
                continue
            if verdict:
                return verdict
        return {}

    @staticmethod
    def _calls_in(name: str, args: dict) -> list[tuple[str, dict]]:
        """The calls one tool call makes: itself, or each command of a batch."""
        if name != BATCH_TOOL:
            return [(name, args)]
        commands = args.get("commands") if isinstance(args, dict) else None
        if not isinstance(commands, list):
            return []
        return [(str(command.get("name") or ""),
                 command["arguments"] if isinstance(command.get("arguments"), dict) else {})
                for command in commands if isinstance(command, dict)]

    def _batch_check(self, args: dict) -> dict:
        """The cost and volume verdicts of a batch's commands, or why it cannot hold one."""










        card: dict = {}
        for inner, inner_args in self._calls_in(BATCH_TOOL, args):
            verdict = self._costly_check(inner, inner_args)
            if verdict.get("error"):
                return verdict



            if inner != CODE_TOOL and self._background_ok(inner, inner_args):
                return {"error": (f"{inner} cannot run inside batch_commands: it runs in the background, "
                                  "and a batch runs every command on the main thread."),
                        "suggestion": f"Call {inner} on its own; several direct calls can run at once.",
                        "code": Err.PERMISSION_DENIED}
            card = card or verdict
        return card

    @staticmethod
    def _danger_for(name: str, args: dict, server_danger) -> str:
        """The level this call runs at: the client's table, never below the server's."""





        client = None
        table_failed = False
        if effective_danger is not None:
            try:
                client = effective_danger(name, args)
            except Exception as exc:  # noqa: BLE001
                table_failed = True
                log_warning(f"effective_danger({name}) failed: {exc}")
        else:
            table_failed = True
        if table_failed or (client is None and server_danger not in Danger.RANK):
            return Danger.DESTRUCTIVE
        return Danger.most_dangerous(client, server_danger)



    def _crs_guard(self, name: str, args: dict) -> tuple[str, str] | None:
        """Refuse a metric distance on a geographic layer, alone or inside a batch."""




        for inner, inner_args in self._calls_in(name, args):
            refused = self._crs_guard_one(inner, inner_args)
            if refused is not None:
                return refused
        return None

    def _crs_guard_one(self, name: str, args: dict) -> tuple[str, str] | None:
        lowered = name.lower()
        unit_keys = ("distance_units", "DISTANCE_UNITS", "units", "unit", "UNITS", "UNIT")
        if lowered in ("run_processing", "run_algorithm"):
            alg = str(args.get("algorithm_id") or args.get("algorithm") or "").lower()
            params = args.get("parameters") if isinstance(args.get("parameters"), dict) else {}
            keys = [k for k in params if k.upper() in _DISTANCE_KEYS and _is_number(params[k])]
            distance_like = any(k.upper() in ("DISTANCE", "BUFFER", "RADIUS") for k in keys)
            if not keys or (alg not in _metric_algs() and not distance_like):
                return None
            unit = str(next((params[u] for u in unit_keys if u in params), "")).lower()
            layer_ref = params.get("INPUT") or params.get("INPUT_LAYER") or params.get("LAYER")
            what = f"{alg} with {keys[0]}={params[keys[0]]}"
        elif "buffer" in lowered or "distance" in lowered or "within" in lowered:
            value = next((args[k] for k in ("distance", "buffer", "radius", "tolerance", "buffer_distance")
                          if _is_number(args.get(k))), None)
            if value is None:
                return None
            unit = str(next((args[u] for u in unit_keys if u in args), "")).lower()
            layer_ref = next((args.get(k) for k in ("layer_name", "layer", "layer_id", "input") if args.get(k)), None)
            what = f"{name} with distance={value}"
        else:
            return None
        if unit in _METRIC_UNITS or unit in _DEGREE_UNITS:
            return None
        layer = self._resolve_layer(layer_ref)
        if layer is None:
            return None
        crs = layer.crs()
        if not crs.isValid() or not crs.isGeographic():
            return None
        utm = self._utm_for(layer)
        message = tr("{what} on '{layer}' whose CRS {crs} is geographic: the distance would be in degrees, "
                     "not meters.").format(what=what, layer=layer.name(), crs=crs.authid())
        suggestion = (f"Reproject '{layer.name()}' to a metric CRS first (native:reprojectlayer with "
                      f"TARGET_CRS={utm}), then run the operation on the reprojected layer, or pass "
                      f"DISTANCE_UNITS: meters when the tool supports it.")
        return message, suggestion

    @staticmethod
    def _resolve_layer(ref):
        if ref is None:
            return None
        if not isinstance(ref, str):
            return ref if hasattr(ref, "crs") else None
        project = QgsProject.instance()
        layer = project.mapLayer(ref)
        if layer is not None:
            return layer
        named = project.mapLayersByName(ref)
        if named:
            return named[0]
        path = ref.split("|", 1)[0]
        if os.path.isfile(path):
            probe = QgsVectorLayer(ref, "probe", "ogr")
            if probe.isValid():
                return probe
        return None

    @staticmethod
    def _utm_for(layer) -> str:
        """A metric CRS whose area of use actually covers this layer."""









        try:
            center = layer.extent().center()
            lon, lat = center.x(), center.y()
            if not (math.isfinite(lon) and math.isfinite(lat)):
                return "EPSG:3857"
            if -180 <= lon <= 180 and -80 <= lat <= 84:
                zone = min(60, max(1, int((lon + 180) // 6) + 1))
                return f"EPSG:{32600 + zone if lat >= 0 else 32700 + zone}"
        except Exception:  # nosec B110 - fallback CRS is used
            pass
        return "EPSG:3857"
