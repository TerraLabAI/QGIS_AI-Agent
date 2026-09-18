# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Symbology, labeling and screenshot handlers, plus the batch-command runner (kept here since it is small and only wires into the registry)."""



from __future__ import annotations

import contextlib
import os
import time
import uuid

from qgis.core import QgsMapRendererSequentialJob, QgsMapSettings, QgsVectorLayer
from qgis.utils import iface

from ..core import layer_order, limits
from ..core.logger import log_warning
from ..core.qt_compat import enum_member
from . import guards
from ._images import image_to_base64, normalize_fmt
from .layer_lookup import _field_not_found_error, _find_layer, _layer_not_found_error



_MAX_CATEGORIES = 500





_READABLE_CATEGORIES = 30
_OTHER_LABEL = "Other"
_OTHER_COLOR = "#9e9e9e"


def _style_to_put_back(layer) -> str:
    """The style this layer draws with now, read just before a call replaces it."""




    try:
        from ..core.snapshot import style_xml

        return style_xml(layer)
    except Exception as exc:  # noqa: BLE001 - a way back is a courtesy, never a failed style
        log_warning(f"Previous style not read: {exc}")
        return ""


def _previous_style_keys(layer, kept: str) -> dict:
    """The result keys that say how to put the style this call replaced back."""







    if not kept:
        return {}
    try:
        from ..core.snapshot import remember_style

        path = remember_style(layer, kept)
    except Exception as exc:  # noqa: BLE001 - as above: the style itself is applied
        log_warning(f"Previous style not kept: {exc}")
        return {}
    if not path:
        return {}
    return {
        "previous_style_qml": path,
        "previous_style_note": ("this replaced the layer's own symbology; apply_style_qml with this "
                                "layer_name and this path puts back exactly what it looked like before "
                                "this call, and nothing else of the project"),
    }


def _failure_of(result) -> str | None:
    """The error message of one command's result, in either shape the executor reads."""
    if not isinstance(result, dict):
        return None
    if result.get("_error") is not None:
        return str(result["_error"])
    if result.get("isError") and result.get("error"):
        return str(result["error"])
    return None


def batch_status(outcomes: list, total: int) -> str:
    """Which commands of a failed batch ran, failed (with their code) and never ran, numbered from 1."""



    ran, failed = [], []
    for i, outcome in enumerate(outcomes, start=1):
        if _failure_of(outcome) is None:
            ran.append(str(i))
        else:
            code = outcome.get("code")
            failed.append(f"{i} ({code})" if code else str(i))
    parts = [f"ran: {', '.join(ran) or 'none'}", f"failed: {', '.join(failed)}"]
    if len(outcomes) < total:
        start = len(outcomes) + 1
        parts.append(f"not run: {start}" if start == total else f"not run: {start}-{total}")
    return "; ".join(parts)


def _make_batch_handler(registry):
    def _batch_commands(args: dict) -> dict:
        commands = args.get("commands")
        if not isinstance(commands, list) or not commands:
            return {"_error": "commands must be a non-empty array of {name, arguments}"}
        return _CommandRun(registry, commands, args.get("stop_on_error", True)).start()

    return _batch_commands


def _strings_in(value, depth: int = 0):
    """Every string inside a command's arguments, however deep, up to a sane depth."""
    if depth > 8:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings_in(item, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings_in(item, depth + 1)


def _path_key(text: str) -> str:
    """A path-like string as one comparable key: the file part, absolute, case as the OS compares it."""
    head = text.split("|", 1)[0].strip()
    if not ("/" in head or "\\" in head or head.startswith("~")):
        return ""
    return os.path.normcase(os.path.abspath(os.path.expanduser(head)))


class _CommandRun:
    """One batch_commands call: its commands in the model's order."""














    def __init__(self, registry, commands: list, stop_on_error):
        self.registry = registry
        self.commands = commands
        self.stop_on_error = stop_on_error
        self.results: list[dict] = []
        self.failed: list[int] = []
        self.position = 0

        self.tasks: dict[str, int] = {}
        self.open: set[str] = set()
        self.deferred = False
        self.stopped = False
        self._advancing = False
        self._again = False
        self.run_token = layer_order.current_run()
        self.task_id = "cmds-" + uuid.uuid4().hex[:12]
        self.entry = {"status": "running", "progress": 0, "algorithm": "batch_commands",
                      "started_at": time.strftime("%H:%M:%S"), "sequence": self}

    def start(self) -> dict:
        from .processing_tools import _POLL_INTERVAL_S, _PROCESSING_TASKS

        self._run_on()
        if not self.deferred:
            return self.report()
        _PROCESSING_TASKS[self.task_id] = self.entry
        waiting = self.commands[self.position]
        name = waiting.get("name") if isinstance(waiting, dict) else None
        return {**self.report(), "task_id": self.task_id, "status": "running",
                "note": (f"Command {self.position + 1} ({name}) waits for a command before it that is still "
                         "running in the background; the rest run in order. Poll get_task_status(task_id)."),
                "poll": {"tool": "get_task_status", "args": {"task_id": self.task_id},
                         "interval_s": _POLL_INTERVAL_S, "label": f"Running {len(self.commands)} commands"}}

    def _done(self) -> bool:
        left = self.position < len(self.commands) and not self.stopped \
            and not (self.failed and self.stop_on_error)
        return not left and not (self.deferred and self.open)

    def _run_on(self) -> None:
        """Run the commands in order until one has to wait for a task still running, or none is left."""
        while self.position < len(self.commands) and not self.stopped \
                and not (self.failed and self.stop_on_error):
            command = self.commands[self.position]
            if self.open and self._waits(command):
                self.deferred = True
                return
            self.position += 1
            result = self._execute(command)
            index = len(self.results)
            self.results.append({"name": command.get("name") if isinstance(command, dict) else None,
                                 "result": result})
            self._watch(index, result)
            if _failure_of(result) is not None:
                self.failed.append(index)

    def _execute(self, command) -> dict:
        if not isinstance(command, dict) or not command.get("name"):
            return {"_error": "each command needs a 'name'"}
        if command["name"] == "batch_commands":
            return {"_error": "batch_commands cannot be nested"}
        if guards.always_confirm(command["name"], command.get("arguments")):
            return {"_error": f"{command['name']} needs its own confirmation card and cannot run inside "
                              "batch_commands"}
        arguments = command.get("arguments", {}) or {}
        if not self.deferred:
            return self.registry.execute(command["name"], arguments)

        try:
            with layer_order.adopted(self.run_token):
                outcome = self.registry.execute(command["name"], arguments)
        except Exception as exc:  # noqa: BLE001 - it runs from a Qt signal, where nothing may raise
            log_warning(f"batch_commands: {command['name']} raised {exc}")
            outcome = {"_error": f"{exc.__class__.__name__}: {exc}"}
        return outcome

    def _watch(self, index: int, result) -> None:
        """Keep a command's background task, and go on when its task signals that it ended."""
        from .processing_tools import _PROCESSING_TASKS

        if not isinstance(result, dict) or str(result.get("status") or "").lower() != "running":
            return
        task_id = str(result.get("task_id") or "")
        entry = _PROCESSING_TASKS.get(task_id)
        if entry is None or entry.get("status") != "running":
            return
        self.tasks[task_id] = index
        self.open.add(task_id)
        if self.stopped:


            from .processing_tools import _cancel_task

            _cancel_task({"task_id": task_id})
            return
        task = entry.get("task")
        for signal_name in ("executed", "taskTerminated"):
            signal = getattr(task, signal_name, None)
            if signal is not None:
                signal.connect(lambda *_args: self._ended())

    def _waits(self, command) -> bool:
        """Whether the command names what a command still running writes: its output_name or a path it was given."""
        names, paths = set(), set()
        for task_id in self.open:
            earlier = self.commands[self._command_index(task_id)]
            arguments = earlier.get("arguments") if isinstance(earlier, dict) else None
            if isinstance(arguments, dict) and isinstance(arguments.get("output_name"), str):
                names.add(arguments["output_name"].strip().casefold())
            paths.update(key for key in map(_path_key, _strings_in(arguments)) if key)
        arguments = command.get("arguments") if isinstance(command, dict) else None
        for text in _strings_in(arguments):
            if text.strip().casefold() in names or (paths and _path_key(text) in paths):
                return True
        return False

    def _command_index(self, task_id: str) -> int:
        """The position in commands of the command whose result carries this task (results skip nothing)."""
        return self.tasks[task_id]

    def _settle(self) -> None:
        """Put each ended task's final status in place of its running answer."""
        from .processing_tools import _PROCESSING_TASKS, _get_task_status, _sync_with_qgis

        for task_id in sorted(self.open):
            _sync_with_qgis(task_id)
            if (_PROCESSING_TASKS.get(task_id) or {}).get("status") == "running":
                continue
            self.open.discard(task_id)
            final = {k: v for k, v in _get_task_status({"task_id": task_id}).items() if k != "poll"}
            if final.get("status") in ("error", "canceled") and not final.get("_error"):
                final["_error"] = str(final.get("error") or f"The task ended {final.get('status')}.")
            index = self.tasks[task_id]
            self.results[index]["result"] = final
            if _failure_of(final) is not None and index not in self.failed:
                self.failed.append(index)
                self.failed.sort()

    def advance(self) -> None:
        """Settle what ended, then run the commands that no longer wait (from a poll or a task's signal)."""
        from .processing_tools import _PROCESSING_TASKS

        if self._advancing:
            self._again = True
            return
        self._advancing = True
        try:
            while self.entry["status"] == "running" and _PROCESSING_TASKS.get(self.task_id) is self.entry:
                self._again = False
                self._settle()
                self._run_on()
                if self._done():
                    self.entry.update(status="error" if self.failed else "complete", progress=100)
                    return
                self.entry["progress"] = int(100 * len(self.results) / len(self.commands))
                if not self._again:
                    return
        finally:
            self._advancing = False

    def _ended(self) -> None:
        from .processing_tools import _PROCESSING_TASKS

        if _PROCESSING_TASKS.get(self.task_id) is not self.entry:
            return
        try:
            self.advance()
        except Exception as exc:  # noqa: BLE001 - a Qt slot must not raise
            log_warning(f"batch_commands: could not go on after a task ended: {exc}")

    def cancel(self) -> None:
        """Stop: every task the batch started is cancelled, and no command after them runs."""
        from .processing_tools import _cancel_task

        self.stopped = True
        for task_id in sorted(self.open):
            _cancel_task({"task_id": task_id})

    def report(self) -> dict:
        """The batch as batch_commands has always answered it; its failure only once nothing is left to run."""
        results = [dict(item) for item in self.results]
        outcome = {"results": results, "executed": len(results),
                   "stopped_at": self.failed[0] if self.failed and self.stop_on_error else None}
        if not self.failed or (self.deferred and not self._done()):
            return outcome




        first = results[self.failed[0]]
        inner = first["result"]



        outcome["_error"] = (f"Command {self.failed[0] + 1} of {len(self.commands)} ({first['name']}) failed: "
                             f"{_failure_of(inner)} "
                             f"({batch_status([item['result'] for item in results], len(self.commands))}).")
        for key in ("code", "suggestion", "traceback"):
            if inner.get(key):
                outcome[key] = inner[key]
        return outcome


def _set_layer_style(args: dict) -> dict:
    from qgis.core import (
        QgsCategorizedSymbolRenderer,
        QgsGraduatedSymbolRenderer,
        QgsProperty,
        QgsRenderContext,
        QgsRendererCategory,
        QgsSingleSymbolRenderer,
        QgsStyle,
        QgsSymbol,
        QgsWkbTypes,
    )
    from qgis.PyQt.QtGui import QColor







    for theirs, ours in (("layer", "layer_name"), ("fill_color", "color"), ("symbol_size", "size")):
        if args.get(ours) in (None, "") and args.get(theirs) not in (None, ""):
            args[ours] = args[theirs]
    target = args.get("layer_name")
    if not target:
        return {"_error": "layer_name is required.", "code": "INVALID_ARGS",
                "suggestion": "Name the layer to style, or pass its id."}
    layer = _find_layer(target)
    if not layer:
        return _layer_not_found_error(target)

    if not isinstance(layer, QgsVectorLayer):






        return _style_raster_layer(layer, target, args)

    style_type = str(args.get("style_type") or "").strip()
    if not style_type:
        return {"_error": "style_type is required.", "code": "INVALID_ARGS",
                "suggestion": "single, categorized, graduated or cluster."}

    bad_argument = _style_args_error(layer, args)
    if bad_argument:
        return bad_argument




    if not layer.isSpatial():
        return {"_error": f"Layer {layer.name()!r} has no geometry, so there is nothing on the map to style.",
                "code": "INVALID_ARGS",
                "suggestion": "Style the layer that draws these rows, or join this table to it first."}
    if (args.get("size_expression") and style_type != "cluster"
            and layer.geometryType() == enum_member(QgsWkbTypes, "GeometryType", "PolygonGeometry")):
        return {"_error": (f"size_expression sizes point markers or line widths, and {layer.name()!r} "
                           "is a polygon layer. Nothing was changed."),
                "code": "INVALID_ARGS",
                "suggestion": "Leave size_expression out, or use a graduated style on the field to show its values."}






    style_type, coerced_note = _style_for_geometry(layer, style_type, args)



    color_ramp_name = args.get("color_ramp")
    ramp = None
    if color_ramp_name and style_type in ("categorized", "graduated"):
        default_style = QgsStyle.defaultStyle()
        ramp = default_style.colorRamp(color_ramp_name)
        if ramp is None:


            same = [n for n in default_style.colorRampNames() if n.lower() == color_ramp_name.lower()]
            if same:
                ramp = default_style.colorRamp(same[0])
            else:
                try:
                    from qgis.core import QgsColorBrewerColorRamp
                    scheme = next((s for s in QgsColorBrewerColorRamp.listSchemeNames()
                                   if s.lower() == color_ramp_name.lower()), None)
                    ramp = QgsColorBrewerColorRamp(scheme, 9) if scheme else None
                except Exception:  # nosec B110 - fallback only
                    ramp = None
        if ramp is None:
            return {
                "_error": f"Unknown color ramp: {color_ramp_name!r}.",
                "available_ramps": default_style.colorRampNames(),
            }



    kept_style = _style_to_put_back(layer)

    fold_note: dict = {}
    if style_type == "single":
        color = args.get("color", "#3388ff")
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.setColor(QColor(color))
        renderer = QgsSingleSymbolRenderer(symbol)
        layer.setRenderer(renderer)

    elif style_type == "categorized":
        field = args.get("field")
        if not field:
            return {"_error": "Field is required for categorized style"}

        idx = layer.fields().indexOf(field)
        if idx < 0:
            return _field_not_found_error(layer, field)





        unique_values = list(layer.uniqueValues(idx, _MAX_CATEGORIES + 1))
        n = len(unique_values)




        overflow = n > _MAX_CATEGORIES





        with contextlib.suppress(TypeError):
            unique_values = sorted(unique_values, key=lambda v: (str(type(v)), v))





        max_classes = _requested_classes(args) or _READABLE_CATEGORIES
        folded, scanned = [], 0
        if n > max_classes:
            counts, scanned = _value_frequencies(layer, idx, limits.current("MAX_FEATURES_MATERIALISED"))
            if overflow:
                n = len(counts)
                by_text = {str(v): v for v in unique_values}
                ranked = sorted(counts, key=lambda text: (-counts[text], text))
                unique_values = [by_text.get(text, text) for text in ranked[:max_classes]]
                folded = None
            else:
                ranked = sorted(unique_values, key=lambda v: (-counts.get(str(v), 0), str(v)))
                kept = {str(v) for v in ranked[:max_classes]}
                folded = [v for v in unique_values if str(v) not in kept]
                unique_values = [v for v in unique_values if str(v) in kept]

        shown = len(unique_values)
        categories = []
        for i, value in enumerate(unique_values):
            symbol = QgsSymbol.defaultSymbol(layer.geometryType())
            if ramp is not None:
                frac = i / (shown - 1) if shown > 1 else 0.0
                symbol.setColor(ramp.color(frac))
            else:
                hue = (i * 37) % 360
                symbol.setColor(QColor.fromHsl(hue, 178, 128))
            categories.append(QgsRendererCategory(value, symbol, _category_label(value)))
        if folded or folded is None:




            other = QgsSymbol.defaultSymbol(layer.geometryType())
            other.setColor(QColor(_OTHER_COLOR))
            tail = list(folded) if folded else ""
            categories.append(QgsRendererCategory(tail, other, _OTHER_LABEL))
            n_folded = len(folded) if folded else max(n - shown, 0)
            fold_note = {
                "classes_shown": shown,
                "classes_folded": n_folded,
                "warning": (
                    f"{n} distinct values in {field}: the {shown} most frequent got a class each, "
                    f"{n_folded} others share one grey class; a graduated style or a field with "
                    "fewer values reads better"
                ),
            }
            if scanned and scanned < layer.featureCount():
                fold_note["warning"] += f" (frequencies measured on the first {scanned} features)"

        renderer = QgsCategorizedSymbolRenderer(field, categories)
        layer.setRenderer(renderer)

    elif style_type == "graduated":
        field = args.get("field")
        if not field:
            return {"_error": "Field is required for graduated style"}

        idx = layer.fields().indexOf(field)
        if idx < 0:
            return _field_not_found_error(layer, field)




        if not layer.fields().at(idx).isNumeric():
            type_name = layer.fields().at(idx).typeName() or "not numeric"
            numeric_names = [f.name() for f in layer.fields() if f.isNumeric()]
            if numeric_names:
                suggestion = (
                    "Numeric fields: " + ", ".join(numeric_names[:12])
                    + ". Use categorized for text, or convert the field to a number first."
                )
            else:
                suggestion = (
                    "This layer has no numeric field. Use categorized for text, "
                    "or add a numeric field computed from this one first."
                )
            return {
                "_error": f"Field {field!r} is {type_name}, and a graduated style needs a numeric field.",
                "code": "INVALID_ARGS",
                "suggestion": suggestion,
            }

        mode_names = {
            "equal_interval": "EqualInterval",
            "quantile": "Quantile",
            "jenks": "Jenks",
            "pretty": "Pretty",
        }
        mode_name = mode_names.get(args.get("classification_mode", "equal_interval"), "EqualInterval")
        classes = int(args.get("classes", 5) or 5)
        base_symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        if ramp is None:
            ramp = QgsStyle.defaultStyle().colorRamp("Viridis") or QgsStyle.defaultStyle().colorRamp("Blues")
        try:


            from qgis.core import QgsApplication
            method = QgsApplication.classificationMethodRegistry().method(mode_name)
            renderer = QgsGraduatedSymbolRenderer(field, [])
            renderer.setSourceSymbol(base_symbol)
            renderer.setClassificationMethod(method)
            if ramp is not None:
                renderer.setSourceColorRamp(ramp.clone())
            renderer.updateClasses(layer, classes)
        except Exception:
            try:
                fallback = enum_member(QgsGraduatedSymbolRenderer, "Mode", "EqualInterval")
                mode = getattr(QgsGraduatedSymbolRenderer, mode_name, fallback)
                renderer = QgsGraduatedSymbolRenderer.createRenderer(
                    layer, field, classes, mode, base_symbol, ramp.clone() if ramp is not None else None
                )
            except Exception as exc:
                return {
                    "_error": f"Graduated renderer is not available on this QGIS version: {exc}",
                    "code": "EXECUTION_FAILED",
                }
        if len(renderer.ranges()) == 0:
            return {
                "_error": f"Field {field!r} has no values to classify.",
                "code": "INVALID_ARGS",
                "suggestion": "Check the field holds numbers on at least one feature, or pick another field.",
            }
        _readable_range_labels(renderer, args.get("units"))
        layer.setRenderer(renderer)

    elif style_type == "cluster":
        renderer = _cluster_renderer(layer, args)
        if isinstance(renderer, dict):
            return renderer
        layer.setRenderer(renderer)

    else:
        return {"_error": f"Unknown style type: {style_type}"}

    applied = _apply_symbol_tweaks(renderer, args) if style_type != "cluster" else {"stroke_color"}

    if args.get("size_expression") and style_type != "cluster":
        for symbol in renderer.symbols(QgsRenderContext()):
            if hasattr(symbol, "setDataDefinedSize"):
                symbol.setDataDefinedSize(QgsProperty.fromExpression(args["size_expression"]))
            elif hasattr(symbol, "setDataDefinedWidth"):
                symbol.setDataDefinedWidth(QgsProperty.fromExpression(args["size_expression"]))

    opacity = args.get("opacity")
    if opacity is not None:
        try:
            layer.setOpacity(float(max(0.0, min(1.0, opacity))))
        except (TypeError, ValueError):
            pass

    layer.triggerRepaint()

    if iface is not None and iface.layerTreeView() is not None:
        iface.layerTreeView().refreshLayerSymbology(layer.id())
    result = {"styled": layer.name(), "type": style_type}
    result.update(_previous_style_keys(layer, kept_style))
    result.update(_wider_than_the_selection(layer))
    if coerced_note:
        result["style_type_changed"] = coerced_note
    if ramp is not None:
        result["color_ramp"] = color_ramp_name
    if args.get("classification_mode"):
        result["classification_mode"] = args["classification_mode"]


    not_applied = []
    for key in ("stroke_color", "stroke_width", "size", "fill"):
        if args.get(key) is None:
            continue
        if key in applied:
            result[key] = args[key]
        else:
            not_applied.append(key)
    if not_applied:
        result["not_applied"] = not_applied
        result["not_applied_note"] = ("these arguments have no place on this layer's symbols and changed nothing: "
                                      "size sizes point markers, fill is a polygon's inside, stroke_width and "
                                      "stroke_color are a line or an outline")
    if style_type == "graduated":
        result["classes"] = len(renderer.ranges())
    elif style_type == "categorized":
        result["classes"] = len(renderer.categories())
        result.update(fold_note)
    if style_type == "cluster":
        result["cluster_distance_mm"] = renderer.tolerance()
        result["separates_on_zoom"] = True
    if args.get("null_class_color"):
        result["null_class_note"] = "Use apply_style_qml to add the requested NULL rule."
    return result





_VECTOR_ONLY_ARGS = ("field", "fill", "stroke_color", "stroke_width", "size", "size_expression",
                     "cluster_distance", "min_size", "max_size", "label_color", "null_class_color")


def _wider_than_the_selection(layer) -> dict:
    """Say when a style call just repainted a whole layer to point at a selection."""









    try:
        selected = int(layer.selectedFeatureCount())
        total = int(layer.featureCount())
    except Exception:  # noqa: BLE001 - a provider with no counts says nothing
        return {}
    if selected <= 0 or total <= selected:
        return {}
    return {
        "affects": f"how all {total:,} features of {layer.name()!r} draw, not only the {selected} selected",
        "highlight_instead": ("a style is permanent and covers the whole layer; to point at particular "
                              "features without touching symbology, call flash_features with their fids "
                              "or an expression: it blinks them three times over half a second and "
                              "changes nothing"),
    }


def _style_raster_layer(layer, target: str, args: dict) -> dict:
    """set_layer_style on a raster: a colour ramp with legend labels a reader can use."""
    from qgis.core import QgsRasterLayer

    from .elevation_style import apply_ramp_style

    if not isinstance(layer, QgsRasterLayer):
        kind = type(layer).__name__.replace("Qgs", "").replace("Layer", "").lower() or "unknown"
        return {"_error": f"Layer {target!r} is a {kind} layer, and set_layer_style paints vectors and rasters.",
                "code": "INVALID_ARGS",
                "suggestion": "Use set_layer_property to change its opacity, or apply_style_qml with a "
                              "style file written for this kind of layer."}

    bands = layer.bandCount()
    opacity = args.get("opacity")
    if bands != 1:


        return {"_error": f"Layer {target!r} has {bands} bands, so it is drawn as a colour image and has no "
                          "single value to classify.",
                "code": "INVALID_ARGS",
                "suggestion": "Colour ramps need one band. Use set_layer_property to change its opacity, "
                              "or pick one band with run_processing native:rastercalc first."}

    color_ramp = str(args.get("color_ramp") or "").strip()
    kept_style = _style_to_put_back(layer)
    outcome = apply_ramp_style(
        layer, band=int(args.get("band") or 1), ramp_name=color_ramp,
        classes=_requested_classes(args), unit=str(args.get("units") or ""))
    if outcome.get("_error"):
        return outcome

    applied_opacity = _apply_layer_opacity(layer, opacity)
    layer.triggerRepaint()
    if iface is not None and iface.layerTreeView() is not None:
        iface.layerTreeView().refreshLayerSymbology(layer.id())
    result = {"styled": layer.name(), "type": "raster_ramp", **outcome}
    result.update(_previous_style_keys(layer, kept_style))
    if color_ramp:
        result["color_ramp"] = color_ramp
    elif args.get("color"):
        result["color_note"] = ("One colour paints every pixel the same, so the classes use the default "
                                "elevation tints. Pass color_ramp for a named QGIS ramp (Terrain, Viridis, "
                                "Spectral, Blues).")
    if applied_opacity is not None:
        result["opacity"] = applied_opacity
    ignored = [key for key in _VECTOR_ONLY_ARGS if args.get(key) not in (None, "")]
    if ignored:
        result["not_applied"] = ignored
        result["not_applied_note"] = ("a raster has no symbols, so these changed nothing; a raster takes "
                                      "color_ramp, classes, band, units and opacity")
    mode = str(args.get("classification_mode") or "").strip()
    if mode and mode != "equal_interval":
        result["classification_mode"] = "equal_interval"
        result["classification_note"] = (f"{mode} classes need a histogram of every pixel; the classes here "
                                         "are equal intervals over the band's range.")
    return result


def _apply_layer_opacity(layer, opacity):
    """Set the layer opacity when one was asked for; the value that landed, or None."""
    if opacity is None:
        return None
    try:
        value = float(max(0.0, min(1.0, float(opacity))))
    except (TypeError, ValueError):
        return None
    layer.setOpacity(value)
    return value


def _requested_classes(args: dict) -> int:
    """The class ceiling the caller asked for, 0 when they asked for nothing."""






    for key in ("max_classes", "classes"):
        value = args.get(key)
        if value in (None, ""):
            continue
        try:
            asked = int(value)
        except (TypeError, ValueError):
            continue
        if asked > 0:
            return min(asked, _MAX_CATEGORIES)
    return 0


def _value_frequencies(layer, index: int, scan_cap: int) -> tuple[dict, int]:
    """Main thread: how often each value of ``index`` occurs, keyed by ``str``."""







    from qgis.core import QgsFeatureRequest

    request = QgsFeatureRequest().setSubsetOfAttributes([index])
    flag = enum_member(QgsFeatureRequest, "Flag", "NoGeometry", None)
    if flag is not None:
        request.setFlags(flag)
    counts: dict = {}
    scanned = 0
    features = layer.getFeatures(request)
    try:
        for feature in features:
            key = str(feature[index])
            counts[key] = counts.get(key, 0) + 1
            scanned += 1
            if scanned >= scan_cap:
                break
    finally:
        with contextlib.suppress(Exception):
            features.close()
    return counts, scanned


def _readable_range_labels(renderer, unit=None) -> None:
    """Rewrite a graduated renderer's class labels so a legend reads them."""






    try:
        ranges = list(renderer.ranges())
        if not ranges:
            return
        span = abs(float(ranges[-1].upperValue()) - float(ranges[0].lowerValue()))
        digits = 0 if span >= 50 else (1 if span >= 5 else (2 if span >= 0.5 else 4))
        tail = f" {unit}" if unit else ""
        for index, item in enumerate(ranges):
            low, high = float(item.lowerValue()), float(item.upperValue())
            renderer.updateRangeLabel(index, f"{low:,.{digits}f} - {high:,.{digits}f}{tail}")
    except Exception as exc:  # noqa: BLE001 - a renderer that will not relabel keeps QGIS's own labels
        log_warning(f"_readable_range_labels: relabeling failed: {exc}")


def _category_label(value) -> str:
    """What a categorized class prints in the legend: '(no value)' rather than 'NULL'."""
    try:
        if value is None:
            return "(no value)"
        text = str(value).strip()
    except Exception:  # noqa: BLE001 - a value that will not print is an empty class
        return "(no value)"
    return text if text and text.upper() != "NULL" else "(no value)"


def _geometry_word(layer) -> str:
    """'point', 'line' or 'polygon' for this layer, '' when it has no geometry."""
    from qgis.core import QgsWkbTypes

    for word, member in (("point", "PointGeometry"), ("line", "LineGeometry"), ("polygon", "PolygonGeometry")):
        if layer.geometryType() == enum_member(QgsWkbTypes, "GeometryType", member, None):
            return word
    return ""


def _style_for_geometry(layer, style_type: str, args: dict) -> tuple:
    """``(style to apply, what changed and why)``: a style the layer's geometry can wear."""





    if style_type != "cluster":
        return style_type, ""
    word = _geometry_word(layer)
    if word in ("", "point"):
        return style_type, ""
    field = args.get("field")
    chosen = "categorized" if field and layer.fields().indexOf(str(field)) >= 0 else "single"
    return chosen, (f"Clusters group points and {layer.name()!r} is a {word} layer, so it was styled "
                    f"{chosen} instead. Nothing else about the request changed.")


def _symbol_layer_property(member: str):
    """``QgsSymbolLayer.Property.Size`` on Qt6, ``PropertySize`` on Qt5."""
    from qgis.core import QgsSymbolLayer

    scoped = getattr(QgsSymbolLayer, "Property", None)
    found = getattr(scoped, member, None) if scoped is not None else None
    if found is not None:
        return found
    return getattr(QgsSymbolLayer, f"Property{member}")


def _cluster_renderer(layer, args: dict):
    """Dense points as clusters that carry their count and split as you zoom in."""











    from qgis.core import (
        QgsFontMarkerSymbolLayer,
        QgsMarkerSymbol,
        QgsPointClusterRenderer,
        QgsProperty,
        QgsSimpleMarkerSymbolLayer,
        QgsSingleSymbolRenderer,
        QgsSymbol,
        QgsWkbTypes,
    )
    from qgis.PyQt.QtGui import QColor, QFont





    if layer.geometryType() != enum_member(QgsWkbTypes, "GeometryType", "PointGeometry"):
        return {"_error": f"Layer {layer.name()!r} is not a point layer.",
                "suggestion": "Clusters group points. Use single, categorized or graduated here."}

    color = QColor(str(args.get("color") or "#2b83ba"))
    label_color = QColor(str(args.get("label_color") or "#ffffff"))
    stroke = QColor(str(args.get("stroke_color") or "#ffffff"))
    try:
        distance = float(args.get("cluster_distance", 12) or 12)
    except (TypeError, ValueError):
        distance = 12.0
    distance = max(1.0, min(60.0, distance))
    try:
        min_size = float(args.get("min_size", 8) or 8)
        max_size = float(args.get("max_size", 26) or 26)
    except (TypeError, ValueError):
        min_size, max_size = 8.0, 26.0
    if max_size < min_size:
        min_size, max_size = max_size, min_size



    disc = QgsSimpleMarkerSymbolLayer()
    disc.setColor(color)
    disc.setStrokeColor(stroke)
    disc.setStrokeWidth(0.4)
    disc.setDataDefinedProperty(
        _symbol_layer_property("Size"),
        QgsProperty.fromExpression(
            f"coalesce(scale_linear(@cluster_size, 1, 250, {min_size}, {max_size}), {min_size})"
        ),
    )



    count = QgsFontMarkerSymbolLayer()
    count.setColor(label_color)
    count.setSize(max(6.0, min_size * 0.55))
    count.setFontFamily(QFont().family())
    count.setDataDefinedProperty(
        _symbol_layer_property("Character"), QgsProperty.fromExpression("@cluster_size")
    )
    count.setDataDefinedProperty(
        _symbol_layer_property("Size"),
        QgsProperty.fromExpression(
            f"coalesce(scale_linear(@cluster_size, 1, 250, {min_size * 0.55}, {max_size * 0.42}), {min_size * 0.55})"
        ),
    )

    cluster_symbol = QgsMarkerSymbol()
    cluster_symbol.changeSymbolLayer(0, disc)
    cluster_symbol.appendSymbolLayer(count)

    renderer = QgsPointClusterRenderer()
    renderer.setClusterSymbol(cluster_symbol)
    renderer.setTolerance(distance)
    from qgis.core import QgsUnitTypes

    renderer.setToleranceUnit(enum_member(QgsUnitTypes, "RenderUnit", "RenderMillimeters"))



    single = QgsSymbol.defaultSymbol(layer.geometryType())
    single.setColor(color)
    if hasattr(single, "setSize"):
        single.setSize(max(1.6, min_size * 0.3))
    renderer.setEmbeddedRenderer(QgsSingleSymbolRenderer(single))
    return renderer


def _apply_symbol_tweaks(renderer, args: dict) -> set:
    """Write the per-symbol arguments onto every class symbol; the keys that landed."""







    from qgis.core import QgsLineSymbolLayer
    from qgis.PyQt.QtGui import QColor

    stroke_color = args.get("stroke_color")
    stroke_width = args.get("stroke_width")
    size = args.get("size")
    fill = args.get("fill")
    applied: set = set()

    symbols = []
    if hasattr(renderer, "symbols"):
        try:
            from qgis.core import QgsRenderContext

            symbols = list(renderer.symbols(QgsRenderContext()))
        except Exception:
            symbols = []
    if not symbols and hasattr(renderer, "symbol"):
        symbols = [renderer.symbol()]

    for symbol in symbols:
        if symbol is None:
            continue
        if size is not None and hasattr(symbol, "setSize"):
            try:
                symbol.setSize(float(size))
                applied.add("size")
            except (TypeError, ValueError):
                pass
        for i in range(symbol.symbolLayerCount()):
            sl = symbol.symbolLayer(i)
            line_layer = isinstance(sl, QgsLineSymbolLayer)
            if fill in ("none", "solid") and hasattr(sl, "setBrushStyle"):
                from qgis.PyQt.QtCore import Qt as _Qt

                sl.setBrushStyle(_Qt.BrushStyle.NoBrush if fill == "none" else _Qt.BrushStyle.SolidPattern)
                applied.add("fill")
            if stroke_color is not None:
                if line_layer:
                    sl.setColor(QColor(stroke_color))
                    applied.add("stroke_color")
                elif hasattr(sl, "setStrokeColor"):
                    sl.setStrokeColor(QColor(stroke_color))
                    applied.add("stroke_color")
            if stroke_width is not None:
                try:
                    if line_layer:
                        sl.setWidth(float(stroke_width))
                        applied.add("stroke_width")
                    elif hasattr(sl, "setStrokeWidth"):
                        sl.setStrokeWidth(float(stroke_width))
                        applied.add("stroke_width")
                except (TypeError, ValueError):
                    pass
    return applied


def _capture_format(args: dict, default: str) -> tuple[str, int]:
    """Resolve (format, quality) for a capture. save_path extension wins if it names one."""
    fmt = args.get("format")
    save_path = args.get("save_path")
    if not fmt and save_path:
        fmt = os.path.splitext(str(save_path))[1].lstrip(".")
    quality = max(min(int(args.get("quality", 95) or 95), 100), 10)
    return normalize_fmt(fmt, default), quality


def _take_screenshot(args: dict) -> dict:




    max_width = max(100, min(int(args.get("max_width", 1600) or 1600), limits.current("MAX_RENDER_WIDTH_PX")))
    fmt, quality = _capture_format(args, "jpeg")






    layers_only = bool(args.get("layers_only", False))

    canvas = iface.mapCanvas()
    if canvas.width() <= 0 or canvas.height() <= 0:
        return {"_error": "Map canvas has no size (window may be minimized)"}




    if hasattr(canvas, "waitWhileRendering"):
        canvas.waitWhileRendering()

    if not layers_only:
        from qgis.PyQt.QtCore import Qt

        image = canvas.grab().toImage()
        if not image.isNull():
            width = image.width()
            height = image.height()
            if width > max_width:
                image = image.scaledToWidth(max_width, Qt.TransformationMode.SmoothTransformation)
                width, height = image.width(), image.height()
            save_path = args.get("save_path")
            if save_path:
                if not image.save(save_path):
                    return {"_error": f"Could not write screenshot to {save_path}"}
                return {"saved_path": save_path, "width": width, "height": height, "format": fmt, "overlays": True}
            return {
                "image_base64": image_to_base64(image, fmt, quality),
                "width": width,
                "height": height,
                "format": fmt,
                "overlays": True,
            }


    settings = QgsMapSettings(canvas.mapSettings())

    width = min(canvas.width(), max_width)
    height = int(width * canvas.height() / canvas.width())

    from qgis.PyQt.QtCore import QSize

    settings.setOutputSize(QSize(width, height))

    job = QgsMapRendererSequentialJob(settings)
    job.start()
    job.waitForFinished()

    image = job.renderedImage()
    save_path = args.get("save_path")
    if save_path:
        if not image.save(save_path):
            return {"_error": f"Could not write screenshot to {save_path}"}
        return {"saved_path": save_path, "width": width, "height": height, "format": fmt}
    return {"image_base64": image_to_base64(image, fmt, quality), "width": width, "height": height, "format": fmt}





_FLASH_DEFAULT_FLASHES = 3
_FLASH_DEFAULT_DURATION_MS = 500
_FLASH_MAX_FLASHES = 10
_FLASH_MAX_DURATION_MS = 5000


_FLASH_MAX_FEATURES = 200


def _flash_ids(layer, args: dict) -> tuple:
    """``(ids, error)``: the feature ids this call points at, read from the layer."""





    from qgis.core import QgsFeatureRequest

    raw = args.get("fids")
    expression = str(args.get("expression") or "").strip()
    if raw is None and not expression:
        return [], {"_error": "flash_features needs 'fids' or 'expression'.",
                    "_code": "INVALID_ARGS",
                    "_suggestion": "Pass fids: [554], or expression: \"name = 'Ilha'\". "
                                   "get_features returns the fid of every row it prints."}
    if raw is not None and expression:
        return [], {"_error": "Pass either 'fids' or 'expression', not both.",
                    "_code": "INVALID_ARGS",
                    "_suggestion": "Use 'fids' when you already know the ids, 'expression' otherwise."}

    if raw is not None:
        wanted = list(raw) if isinstance(raw, (list, tuple, set)) else [raw]
        try:
            wanted = [int(value) for value in wanted]
        except (TypeError, ValueError):
            return [], {"_error": "'fids' must be whole feature ids.",
                        "_code": "INVALID_ARGS",
                        "_suggestion": "Pass the fid values get_features printed, as numbers."}
        if not wanted:
            return [], {"_error": "'fids' is empty.", "_code": "INVALID_ARGS",
                        "_suggestion": "Name at least one feature id, or pass an expression."}



        wanted = list(dict.fromkeys(wanted))
        request = QgsFeatureRequest().setFilterFids(wanted)
        request.setNoAttributes()
    else:
        wanted = None
        invalid = _expression_error(layer, expression, "Expression")
        if invalid:
            return [], invalid
        request = QgsFeatureRequest().setFilterExpression(expression)
    request.setLimit(_FLASH_MAX_FEATURES + 1)

    found: list = []
    try:
        for feature in layer.getFeatures(request):
            found.append(int(feature.id()))
    except Exception as e:  # noqa: BLE001 - a provider that cannot answer says so
        return [], {"_error": f"Reading the features to flash failed: {e}"}
    if len(found) > _FLASH_MAX_FEATURES:
        return [], {"_error": f"More than {_FLASH_MAX_FEATURES} features match: a flash that covers the map "
                              "points at nothing.",
                    "_code": "INVALID_ARGS",
                    "_suggestion": "Narrow the expression, or use set_layer_style to show the whole group."}
    if not found:
        if wanted is not None:
            return [], {"_error": f"No feature of {layer.name()!r} carries any of these ids: "
                                  f"{', '.join(str(v) for v in wanted[:20])}.",
                        "_code": "INVALID_ARGS",
                        "_suggestion": "Call get_features on this layer and use the fid it prints."}
        return [], {"_error": f"No feature of {layer.name()!r} matches {expression!r}.",
                    "_code": "INVALID_ARGS",
                    "_suggestion": "Call get_features with the same expression to see what it selects."}
    return found, None


def _flash_features(args: dict) -> dict:
    """Blink features on the map canvas, changing no style, no selection, no data."""








    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])
    if not isinstance(layer, QgsVectorLayer):
        return {"_error": f"Layer {layer.name()!r} is not a vector layer, and only features can be flashed.",
                "_code": "INVALID_ARGS",
                "_suggestion": "Name a vector layer. To point at part of a raster, zoom to it instead."}

    canvas = iface.mapCanvas() if iface is not None else None
    if canvas is None or not hasattr(canvas, "flashFeatureIds"):
        return {"_error": "This QGIS has no map canvas feature flash.",
                "_code": "EXECUTION_FAILED",
                "_suggestion": "Use select_features to mark the features, then zoom_to_selected."}

    ids, error = _flash_ids(layer, args)
    if error:
        return error

    flashes = max(1, min(int(args.get("flashes") or _FLASH_DEFAULT_FLASHES), _FLASH_MAX_FLASHES))
    duration = max(50, min(int(args.get("duration") or _FLASH_DEFAULT_DURATION_MS), _FLASH_MAX_DURATION_MS))

    from qgis.PyQt.QtGui import QColor

    try:


        canvas.flashFeatureIds(layer, ids, QColor(255, 0, 0, 255), QColor(255, 0, 0, 0), flashes, duration)
    except Exception as e:  # noqa: BLE001 - a canvas that refuses the flash says why
        return {"_error": f"Flashing the features failed: {e}"}

    return {
        "layer": layer.name(),
        "flashed": len(ids),
        "feature_ids": ids[:50],
        "flashes": flashes,
        "duration_ms": duration,
        "changed_nothing": True,
        "note": (f"{len(ids)} feature(s) of {layer.name()!r} blink {flashes} times over {duration} ms. "
                 "The layer's symbology, its selection and its data are untouched and nothing stays on "
                 "screen, so this is what points at a feature temporarily; a style call is permanent and "
                 "covers the whole layer."),
    }


def _expression_error(layer, text: str, argument: str = "Label expression") -> dict | None:
    """None when *text* is an expression this layer can evaluate, else why not."""




    from qgis.core import QgsExpression, QgsExpressionContext, QgsExpressionContextUtils

    expression = QgsExpression(text)
    context = QgsExpressionContext()
    context.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    expression.prepare(context)
    if expression.hasParserError():


        return {"_error": f"{argument} parse error: {expression.parserErrorString().strip()}",
                "_code": "EXPRESSION_INVALID",
                "suggestion": "Call validate_expression to see the error, or pass a field name.",
                "fields": [f.name() for f in layer.fields()]}
    names = {f.name() for f in layer.fields()}
    unknown = sorted(str(column) for column in expression.referencedColumns() if str(column) not in names)
    if unknown:
        return _field_not_found_error(layer, unknown[0])
    return None


def _label_expression_error(layer, field: str) -> dict | None:
    return _expression_error(layer, field, "Label expression")


def _color_error(value, argument: str) -> dict | None:
    """None when Qt understands *value* as a colour, else why not."""





    from qgis.PyQt.QtGui import QColor

    if value in (None, ""):
        return None
    text = str(value)
    valid = getattr(QColor, "isValidColorName", None) or getattr(QColor, "isValidColor", None)
    if valid is not None and valid(text):
        return None
    if QColor(text).isValid():
        return None
    return {"_error": f"{argument} {text!r} is not a colour Qt understands.",
            "code": "INVALID_ARGS",
            "suggestion": ("Pass a hex colour such as '#2b83ba', or one of the SVG colour names "
                           "('steelblue', 'darkgreen'). Descriptive phrases are not colours.")}






_COLOUR_ARGS = (("color", "color"), ("stroke_color", "stroke_color"),
                ("label_color", "label_color"), ("null_class_color", "null_class_color"))


def _style_args_error(layer, args: dict) -> dict | None:
    for key, label in _COLOUR_ARGS:
        bad = _color_error(args.get(key), label)
        if bad:
            return bad
    expression = args.get("size_expression")
    if expression:
        bad = _expression_error(layer, str(expression), "size_expression")
        if bad:
            return bad
    return None


def _set_layer_labels(args: dict) -> dict:
    from qgis.core import Qgis, QgsPalLayerSettings, QgsTextFormat, QgsVectorLayerSimpleLabeling, QgsWkbTypes
    from qgis.PyQt.QtGui import QColor, QFont

    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])
    if not isinstance(layer, QgsVectorLayer):
        return {"_error": f"Layer '{args['layer_name']}' is not a vector layer"}

    enabled = args.get("enabled", True)
    if not enabled:
        kept_style = _style_to_put_back(layer)
        layer.setLabelsEnabled(False)
        layer.triggerRepaint()
        return {"labels": "disabled", "layer": args["layer_name"],
                **_previous_style_keys(layer, kept_style)}

    field = args["field"]
    size = args.get("size", 10)
    color = args.get("color", "#000000")
    for key, label in (("color", "color"), ("buffer_color", "buffer_color")):
        bad = _color_error(args.get(key), label)
        if bad:
            return bad

    settings = QgsPalLayerSettings()
    settings.fieldName = field



    is_expression = layer.fields().indexOf(field) < 0
    if is_expression:
        error = _label_expression_error(layer, field)
        if error:
            return error
    settings.isExpression = is_expression




    if layer.geometryType() == enum_member(QgsWkbTypes, "GeometryType", "LineGeometry"):
        settings.placement = enum_member(Qgis, "LabelPlacement", "Line")

    text_format = QgsTextFormat()
    font = QFont()
    font.setPointSizeF(size)
    text_format.setFont(font)
    text_format.setSize(size)
    text_format.setColor(QColor(color))
    buffer_size = args.get("buffer_size")
    if buffer_size:
        halo = text_format.buffer()
        halo.setEnabled(True)
        halo.setSize(float(buffer_size))
        halo.setColor(QColor(args.get("buffer_color", "#ffffff")))
        text_format.setBuffer(halo)
    settings.setFormat(text_format)

    avoid_overlaps = bool(args.get("avoid_overlaps", True))
    settings.displayAll = not avoid_overlaps
    if avoid_overlaps:
        try:
            from qgis.core import Qgis
            placement = settings.placementSettings()
            placement.setOverlapHandling(Qgis.LabelOverlapHandling.PreventOverlap)
            settings.setPlacementSettings(placement)
        except Exception:  # nosec B110 - older QGIS has no overlap handling
            pass




    min_scale = float(args.get("min_scale") or 0)
    max_scale = float(args.get("max_scale") or 0)
    if min_scale or max_scale:
        settings.scaleVisibility = True
        settings.minimumScale = min_scale
        settings.maximumScale = max_scale

    kept_style = _style_to_put_back(layer)
    labeling = QgsVectorLayerSimpleLabeling(settings)
    layer.setLabeling(labeling)
    layer.setLabelsEnabled(True)
    layer.triggerRepaint()
    out = {"labels": "enabled", "layer": args["layer_name"], "field": field}
    out.update(_previous_style_keys(layer, kept_style))
    if min_scale or max_scale:
        out["scale_visibility"] = {
            "min_scale": min_scale, "max_scale": max_scale,
            "note": ("labels draw between 1:max_scale (zoomed in) and "
                     "1:min_scale (zoomed out); 0 is no limit")}
    return out
