# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




from __future__ import annotations

import math
import os

from qgis.core import QgsProject, QgsVectorLayer
from qgis.PyQt.QtCore import QCoreApplication

from . import limits, output_paths, tuning
from .logger import log, log_warning
from .protocol import Danger
from .tool_registry import spec

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








_LAYERS_PER_ROUND = 4


CODE_TOOL = "execute_code"


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






    return tuning.check_algs("metric_algs", _METRIC_ALGS)


_METRIC_UNITS = frozenset({"meters", "metres", "m", "kilometers", "kilometres", "km", "feet", "ft",
                           "miles", "mi", "yards", "yd"})
_DEGREE_UNITS = frozenset({"degrees", "deg", "degree"})


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def tr(text: str) -> str:
    return QCoreApplication.translate("ToolExecutor", text)


def _split_table_paths(name: str, args: dict, table_arg: str) -> list[dict]:









    if not table_arg:
        return []
    from ..tools.processing_destinations import _gpkg_table_target

    changes: list[dict] = []
    for key in guards.WRITE_PATH_ARGS.get(name, ()):
        before = args.get(key)
        target = _gpkg_table_target(before) if isinstance(before, str) else None
        if not target:
            continue
        args[key] = target[0]
        if not str(args.get(table_arg) or "").strip():
            args[table_arg] = target[1]
        changes.append({"arg": key, "from": before, "to": target[0], "how": f"{table_arg}={args[table_arg]}"})
    return changes


class _ExecutorGuards:


    def _blocked_reason(self, run_id: str, danger: str) -> str:







        sentence = self._blocked.get(run_id)
        if not sentence or danger == Danger.READ:
            return ""
        self._blocked.pop(run_id, None)
        return sentence

    def _withdraw_past_cap(self, run_id: str, name: str, args: dict | None) -> dict | None:












        if run_id not in self._run_mode:
            return None
        fresh = self.stacker.fresh()
        if not fresh:
            return None
        if volume_guard is not None and volume_guard.lifted(args or {}):
            return None
        added = self.stacker.added_count()
        before = max(0, added - len(fresh))
        cap = max(1, int(limits.current("MAX_LAYERS_PER_RUN")))
        rounds = self._layer_rounds(run_id, before)
        ceiling = cap * tuning.ceiling("executor_guards_layers_per_round", _LAYERS_PER_ROUND, 1)
        if len(rounds) < cap and before < ceiling:
            return None
        gone = self.stacker.withdraw(fresh)
        if not gone:
            return None


        liftable = volume_guard is not None and self._takes_full_extent(name)
        over = (f"This run has added layers in {len(rounds)} calls, the cap for one answer ({cap}); "
                f"{before} of them are still in the project."
                if len(rounds) >= cap else
                f"This run has added {before} layers to the project, the cap for one answer ({ceiling}).")
        names = ", ".join(repr(entry["name"]) for entry in gone[:6])

        return dict(hint="layer_cap", variant="lift" if liftable else "", layers=gone, **{
            "error": (over + f" So the layers this call added ({names}) were taken back out of the project; "
                      "whatever it wrote to disk is kept at the source listed. Every layer costs a redraw "
                      "of the tree and the canvas, and a project nobody asked to grow this much is its own "
                      "kind of damage."
                      + (volume_guard.LIFT_HINT if liftable else "")),



            "suggestion": (("If the user named every layer to add, call again with full_extent: their words and the "
                            "files. " if liftable else "")
                           + "Remove the working layers this run created and no longer needs with remove_layer: "
                           "the cap counts the layers this run added that are still in the project, so that "
                           "frees room immediately and you can carry on. One merged or clipped layer instead "
                           "of one per source works too. Only if every layer is needed, stop, tell the user "
                           "what was added and what is left, and ask whether to continue in a new message."),
        })

    def _takes_full_extent(self, name: str) -> bool:

        schema = getattr(self._registry.get_tool(name), "input_schema", None) or {}
        return volume_guard is not None and volume_guard.FULL_EXTENT in (schema.get("properties") or {})

    def _layer_rounds(self, run_id: str, added: int) -> list:

















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







        if "bbox" not in args:
            return
        known = (getattr(self._registry.get_tool(name), "input_schema", None) or {}).get("properties") or {}
        if "bbox" not in known:
            args.pop("bbox", None)

    @staticmethod
    def _resolve_output_paths(name: str, args: dict) -> None:






        if guards is None or not isinstance(args, dict):
            return
        try:
            declared = spec(name)
            changes = _split_table_paths(name, args, declared.table_at) if declared is not None else []
            changes += output_paths.resolve_write_paths(name, args, guards.WRITE_PATH_ARGS)
            if declared is not None and declared.processing is not None and effective_danger is not None:
                from ..tools.danger import _processing_output_params
                algorithm, parameter_sets = declared.processing(args)
                keys = _processing_output_params(algorithm)
                for parameters in parameter_sets:
                    changes += output_paths.resolve_processing_outputs(parameters, keys)
            for change in changes:
                log(f"PATH {name}.{change['arg']} -> {change['to']} ({change['how']})")
        except Exception as exc:  # noqa: BLE001
            log_warning(f"output path resolution failed for {name}: {exc}")

    def _drop_unknown_flags(self, name: str, args: dict) -> None:

        known = (getattr(self._registry.get_tool(name), "input_schema", None) or {}).get("properties") or {}
        for key in (guards.OVERWRITE_KEYS if guards is not None else ()):
            if key in args and key not in known:
                args.pop(key)

    @staticmethod
    def _guard_check(name: str, args: dict, own_files: frozenset = frozenset()) -> dict:









        if guards is None:
            return {"error": "The argument guards are not available, so no tool call can be checked.",
                    "code": "EXECUTION_FAILED",
                    "suggestion": "Tell the user to reload the AI Agent plugin, and to report this if it persists."}
        try:
            return guards.check_call(name, args, own_files)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"argument guard failed for {name}: {exc}")
            return {"error": f"The arguments of {name} could not be checked, so the call was not run.",
                    "code": "EXECUTION_FAILED",
                    "suggestion": "Try a simpler form of the call, and tell the user if it keeps failing."}

    @staticmethod
    def _costly_check(name: str, args: dict) -> dict:





        for guard in (cost_guard, volume_guard):
            if guard is None:
                continue
            try:
                verdict = guard.check(name, args)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"{guard.__name__.rsplit('.', 1)[-1]} failed for {name}: {exc}")
                continue
            if verdict:


                if guard is volume_guard and not verdict.get("error"):
                    verdict = {**verdict, "slow_only": True}
                return verdict
        return {}

    @staticmethod
    def _danger_for(name: str, args: dict, server_danger, own_files: frozenset = frozenset()) -> str:






        client = None
        table_failed = False
        if effective_danger is not None:
            try:
                client = effective_danger(name, args, own_files)
            except Exception as exc:  # noqa: BLE001
                table_failed = True
                log_warning(f"effective_danger({name}) failed: {exc}")
        else:
            table_failed = True
        if table_failed or (client is None and server_danger not in Danger.RANK):
            return Danger.DESTRUCTIVE
        return Danger.most_dangerous(client, server_danger)



    def _crs_guard(self, name: str, args: dict) -> tuple[str, str] | None:

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
        if isinstance(ref, dict):




            ref = next((ref[key] for key in ("source", "layer", "layer_name") if key in ref), None)
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










        try:
            center = layer.extent().center()
            lon, lat = center.x(), center.y()
            if not (math.isfinite(lon) and math.isfinite(lat)):
                return "EPSG:3857"
            if -180 <= lon <= 180 and -80 <= lat <= 84:
                zone = min(60, max(1, int((lon + 180) // 6) + 1))
                return f"EPSG:{32600 + zone if lat >= 0 else 32700 + zone}"
        except Exception:  # nosec B110
            pass
        return "EPSG:3857"
