# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Delivering a tool result: scrub, bound, journal, and what the run touched."""



from __future__ import annotations

import json
import time

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QCoreApplication

from . import licence, limits, postcondition, scratch, stalls
from .checkpoints import KIND_BEFORE
from .context import stamp_thread
from .executor_guards import BATCH_TOOL
from .log_scrub import scrub_result
from .logger import log_warning
from .protocol import KNOWN_CLIENT_CODES, Danger
from .protocol import ClientErrorCode as Err
from .run_report import MAX_CALL_WARNINGS, call_warnings, written_paths
from .serialization import bound_result, dump_json, neutralise_result, reads_the_outside_world
from .snapshot import RunSnapshot

try:
    from ..tools import guards
except ImportError:
    guards = None



_TASK_READS = scratch.TASK_READS


def _task_ids_started(name: str, result) -> set:
    """The task_ids this call's result started: at its own top level, and, for batch_commands, inside each nested command's own result too."""

    def _one(candidate) -> str | None:
        if isinstance(candidate, dict) and candidate.get("task_id") and \
                str(candidate.get("status") or "").lower() == "running":
            return str(candidate["task_id"])
        return None

    found: set = set()
    if name not in _TASK_READS:
        top = _one(result)
        if top:
            found.add(top)
    if name == BATCH_TOOL and isinstance(result, dict):
        for item in result.get("results") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("name") or "") in _TASK_READS:
                continue
            inner = _one(item.get("result"))
            if inner:
                found.add(inner)
    return found


_LAYER_KEYS = ("layer_name", "layer", "layer_id", "input", "INPUT", "target_layer", "output_name", "name")


def tr(text: str) -> str:
    return QCoreApplication.translate("ToolExecutor", text)



_CHECK_NOTE_CHARS = 90


def _first_check_warning(result) -> str:
    """The first warning of a result's ``checks`` object, short enough for a row."""







    if not isinstance(result, dict):
        return ""
    checks = result.get("checks")
    warnings = checks.get("warnings") if isinstance(checks, dict) else None
    if not isinstance(warnings, (list, tuple)) or not warnings:


        verified = result.get("verified")
        note = verified.get("warning") if isinstance(verified, dict) else None
        if not note:
            return ""
        warnings = [note]
    first = " ".join(str(warnings[0]).split())
    return first[:_CHECK_NOTE_CHARS - 1] + "\u2026" if len(first) > _CHECK_NOTE_CHARS else first


class _ExecutorDeliver:
    def _deliver(self, call: dict, result, started: float) -> None:
        """The one exit for a tool result, whichever thread produced it."""
        if self._closed:
            return
        tool_call_id, run_id = str(call.get("tool_call_id")), str(call.get("run_id") or "")
        name, args = str(call.get("name")), call.get("args") or {}
        danger = call.get("danger", Danger.READ)
        duration = time.monotonic() - started
        in_background = self._inflight.pop(tool_call_id, (None, None, None, False))[3]








        started_task_ids = _task_ids_started(name, result)
        if started_task_ids:
            if run_id in self._cancelled:




                for task_id in started_task_ids:
                    try:
                        self._registry.execute("cancel_task", {"task_id": task_id})
                    except Exception as exc:  # noqa: BLE001 - Stop never fails on a late task
                        log_warning(f"Task {task_id} not cancelled on late Stop: {exc}")
            else:
                self._task_ids.setdefault(run_id, set()).update(started_task_ids)
        if tool_call_id in self._answered:
            self._executing.discard(tool_call_id)


            log_warning(f"{name} answered after its deadline ({duration:.0f}s); the late result is dropped.")
            return
        if not in_background and duration > limits.main_budget(name):
            self._note_slow_main_thread(run_id, name, duration)
        if run_id in self._cancelled:




            self._fail(call, Err.CANCELLED, tr("The run was cancelled by the user."),
                       "Stop here and wait for the next user message.", duration)
            return





        name = str(call.get("name") or "")
        registered = self._registry.get_tool(name)



        declared = getattr(registered, "open_world", None)
        outside = reads_the_outside_world(name) if declared is None else bool(declared)





        with stalls.probe("licence.credit"):
            added = self.stacker.take_added()
            licence.credit_added(added, result)

            stamp_thread(added, self._threads.get(run_id, ""))
        with stalls.probe("result.scrub"):
            result = neutralise_result(scrub_result(result), name, open_world=outside)



        with stalls.probe("layer_order.place"):
            stacked = self.stacker.place_pending()
            grouped = self.stacker.take_grouped()
        if stacked and isinstance(result, dict) and "_error" not in result:
            result["drawn_under"] = stacked



        if grouped and isinstance(result, dict) and "_error" not in result:
            result["grouped_into"] = grouped
        error = self._error_of(result)
        if error is not None:
            code, message, suggestion = error
            detail = result.get("traceback", "") if isinstance(result, dict) else ""
            self._fail(call, code, message, suggestion, duration, detail)
            if name == BATCH_TOOL and danger != Danger.READ:
                self._note_batch_landed(run_id, args, result)
            if danger != Danger.READ:
                self._journal_note(run_id, call, result, False)
            return




        if danger != Danger.READ and isinstance(result, dict):
            with stalls.probe("postcondition.verify"):
                verified = postcondition.verify(name, args, result)
            if verified:
                result["verified"] = verified



        if danger != Danger.READ or (name == "get_task_status" and isinstance(args, dict)
                                     and str(args.get("task_id") or "") in self._task_ids.get(run_id, ())):
            found = call_warnings(name, result)
            if found:
                kept = self._call_warnings.setdefault(run_id, [])
                kept.extend(found[:max(0, MAX_CALL_WARNINGS - len(kept))])


        shown = result
        with stalls.probe("result.serialise"):
            result, detail, text = self._bounded(result, registered)
        self._table.put(tool_call_id, "result", result, payload_json=text)
        self._executing.discard(tool_call_id)
        self._session.send_tool_result(tool_call_id, run_id, result)
        self.tool_finished.emit(tool_call_id, True, self._summary(name, result), duration, detail, shown)



        with stalls.probe("scratch.note"):
            self.scratch.note_call(name, args, call.get("scratch_mark"), shown)




        written = written_paths(name, args, shown)
        if written:
            self._written.setdefault(run_id, []).extend(written)
        if danger != Danger.READ:
            named = self._note_touched(run_id, args, result)
            self._journal_note(run_id, call, shown, True)



            self.follower.request(named)

    def _prepare_snapshot(self, run_id: str, name: str, args: dict, danger: str, overwrites: list):
        snapshot = self._snapshots.get(run_id)
        if snapshot is None:
            snapshot = RunSnapshot(run_id or "run")
            self._snapshots[run_id] = snapshot
        with stalls.probe("snapshot.capture"):
            if not snapshot.captured and snapshot.capture():
                thread_id = self._threads.get(run_id, "")
                index = self.history.next_run_index(thread_id)
                self._run_index[run_id] = index
                self.history.add(thread_id, KIND_BEFORE, run_id, index, snapshot,
                                 prompt=self._prompts.get(run_id, ""))
        with stalls.probe("snapshot.backups"):



            for inner, inner_args in self._calls_in(name, args):
                inner_danger = danger if inner == name else self._danger_for(inner, inner_args, None)
                mutates = guards is not None and inner in guards.DATA_MUTATORS
                if inner_danger == Danger.DESTRUCTIVE or mutates:
                    snapshot.backup_targets(inner_args)
                if inner == "execute_code":
                    snapshot.backup_mentioned(str(inner_args.get("code") or ""))
            if overwrites:
                snapshot.backup_files(overwrites)
        return snapshot

    @staticmethod
    def _error_of(result) -> tuple[str, str, str] | None:
        if not isinstance(result, dict):
            return None
        message = result.get("_error")
        if message is None and result.get("isError") and result.get("error"):
            message = result.get("error")
        if message is None:
            return None
        message = str(message)
        code = str(result.get("code") or "")






        if code not in KNOWN_CLIENT_CODES:
            lowered = message.lower()
            if "layer" in lowered and ("not found" in lowered or "no layer" in lowered):
                code = Err.LAYER_NOT_FOUND
            elif any(word in lowered for word in ("invalid", "required", "must be", "unknown parameter", "missing")):
                code = Err.INVALID_ARGS
            else:
                code = Err.EXECUTION_FAILED
        suggestion = str(result.get("suggestion") or "")
        if not suggestion:
            suggestion = {
                Err.LAYER_NOT_FOUND: "Call list_layers and use the exact layer name or id.",
                Err.INVALID_ARGS: "Check the tool's parameter schema and fix the arguments.",
                Err.CANCELLED: "Stop and wait for the next user message.",
            }.get(code, "Read the message, adjust the approach, and try a different call if needed.")
        return code, message, suggestion

    @staticmethod
    def _bounded(result, registered=None) -> tuple[object, str, str | None]:
        """The result for the wire, the detail for the panel, and the JSON text of the wire result when it is exactly what was serialised here."""








        schema = getattr(registered, "input_schema", None) if registered is not None else None
        properties = schema.get("properties") if isinstance(schema, dict) else None
        return bound_result(result, narrow_with=list(properties) if isinstance(properties, dict) else None)

    @staticmethod
    def _summary(name: str, result) -> str:
        if isinstance(result, dict):
            if result.get("task_id") and str(result.get("status", "")).lower() == "running":
                return tr("running in the background (task {id})").format(id=result["task_id"])
            if result.get("_truncated"):
                return tr("done, result cut to {n} characters").format(
                    n=int(result.get("total_chars", 0)) - int(result.get("cut_chars", 0)))
            parts = []





            warning = _first_check_warning(result)
            if warning:
                parts.append(f"check: {warning}")
            for key in ("message", "layer_name", "name", "count", "feature_count", "status", "path"):
                if key in result and result[key] not in (None, "", [], {}):
                    parts.append(f"{key}: {str(result[key])[:60]}")
                if len(parts) == 3:
                    break
            if parts:
                return ", ".join(parts)
            return tr("done ({n} fields)").format(n=len(result))
        if isinstance(result, list):
            return tr("done ({n} items)").format(n=len(result))
        return tr("done")

    @staticmethod
    def _call_key(name: str, args: dict) -> str:
        """One string for "the same call again": the tool and its arguments."""




        try:
            return name + "|" + json.dumps(args, sort_keys=True, default=str)
        except Exception:  # noqa: BLE001
            return name + "|" + str(sorted(args.items()))

    @staticmethod
    def _args_digest(args: dict) -> str:
        try:
            return dump_json(scrub_result(args))[:300]
        except Exception:
            return str(args)[:300]

    def _call_layers(self, args: dict, result) -> list[str]:
        """The layers one call named, as ids where the project knows them."""





        values = [args.get(key) for key in _LAYER_KEYS]
        params = args.get("parameters")
        if isinstance(params, dict):
            values += [params.get(key) for key in ("INPUT", "OUTPUT", "LAYER")]
        if isinstance(result, dict):
            values += [result.get(key) for key in ("layer_id", "layer_name", "layer", "output_layer")]
            layers = result.get("layers")
            if isinstance(layers, list):
                values += [item.get("id") for item in layers[:50] if isinstance(item, dict)]
        project = QgsProject.instance()
        out: list[str] = []
        for value in values:
            if not isinstance(value, str) or not value:
                continue

            layer = project.mapLayer(value)
            if layer is None:
                named = project.mapLayersByName(value)
                layer = named[0] if named else None
            resolved = layer.id() if layer is not None else value
            if resolved not in out:
                out.append(resolved)
        return out

    def _note_touched(self, run_id: str, args: dict, result) -> list[str]:
        touched = self._touched.setdefault(run_id, set())
        named = self._call_layers(args, result)
        touched.update(named)
        self.project_changed.emit(len(touched))
        return named

    def _note_batch_landed(self, run_id: str, args: dict, result) -> None:
        """Count the layers a failed batch's successful writes changed."""





        commands = args.get("commands") if isinstance(args, dict) else None
        items = result.get("results") if isinstance(result, dict) else None
        if not isinstance(commands, list) or not isinstance(items, list):
            return
        for command, item in zip(commands, items):
            if not isinstance(command, dict) or not isinstance(item, dict):
                continue
            inner_result = item.get("result")
            if self._error_of(inner_result) is not None:
                continue
            inner = str(command.get("name") or "")
            inner_args = command["arguments"] if isinstance(command.get("arguments"), dict) else {}
            if self._danger_for(inner, inner_args, None) != Danger.READ:
                self._note_touched(run_id, inner_args, inner_result)

    def _journal_before(self, run_id: str, name: str, args: dict):
        """What the layers and files a modifying call names hold before it runs (core/journal.py)."""
        from . import journal

        try:
            layer_ids, paths = [], []
            for inner, inner_args in self._calls_in(name, args):
                layer_ids += self._call_layers(inner_args, None)
                paths += written_paths(inner, inner_args, None)
            return journal.before_call(QgsProject.instance(), layer_ids, paths, self._snapshots.get(run_id))
        except Exception as exc:  # noqa: BLE001 - a log never stops a call
            log_warning(f"Run log not started for {name}: {exc}")
            return None

    def _journal_note(self, run_id: str, call: dict, result, ok: bool) -> None:
        """One modifying call's entry in its run's log, kept with the run's checkpoint at end_run."""
        before = call.pop("journal_before", None)
        if before is None or run_id not in self._journal:
            return
        from . import journal

        name, args = str(call.get("name") or ""), call.get("args") or {}
        try:
            layer_ids, paths = [], []
            for inner, inner_args in self._calls_in(name, args):
                own = result if inner == name else None
                layer_ids += self._call_layers(inner_args, own)
                paths += written_paths(inner, inner_args, own)
            entry = journal.after_call(QgsProject.instance(), name, args, before, layer_ids, paths, ok)
            cap = int(limits.current("CHECKPOINT_LOG_CALLS"))
        except Exception as exc:  # noqa: BLE001 - a log never breaks a result
            log_warning(f"Run log entry for {name} not kept: {exc}")
            return
        if entry is not None and len(self._journal[run_id]) < cap:
            self._journal[run_id].append(entry)
