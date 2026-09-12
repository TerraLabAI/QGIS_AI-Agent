# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tool executor: the plugin decides, the server only annotates."""













from __future__ import annotations

import json
import math
import os
import threading
import time
import traceback
from collections import OrderedDict

from qgis.core import QgsProject, QgsVectorLayer
from qgis.PyQt.QtCore import QCoreApplication, QObject, pyqtSignal

from . import background, code_guard, follow, layer_order, limits, machine, postcondition, scratch, stalls, tuning
from .log_scrub import scrub_result, scrub_secrets
from .logger import log, log_warning
from .protocol import KNOWN_CLIENT_CODES, Approval, Danger, Decision, Mode, recommended_index  # noqa: F401  re-exported
from .protocol import ClientErrorCode as Err
from .serialization import bound_result, dump_json, neutralise_result, reads_the_outside_world
from .settings import account_dir
from .watchdog import MainThreadWatchdog
from .writeback import WriteBehind

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
from .checkpoints import KIND_AFTER, KIND_BEFORE, CheckpointHistory
from .snapshot import RunSnapshot, changed_layer_items, diff_changed

ASK_USER = "ask_user"





_LAYER_FREE_TOOLS = frozenset({
    "remove_layer", "remove_map_theme", "remove_bookmark", "remove_print_layout",
    "set_layer_visibility", "set_layers_visibility", "set_layer_order", "move_layer_to_group",
    "create_layer_group", "set_canvas_extent", "set_canvas_scale", "zoom_to_layer",
    "zoom_to_selected", "set_layer_style", "set_raster_style", "set_layer_labels", "apply_style_qml",
})


CODE_TOOL = "execute_code"


try:
    from ..tools.danger import effective_danger
except ImportError:
    effective_danger = None

IDEMPOTENCY_KEEP = 500


CANCELLED_KEEP = 200
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


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class IdempotencyTable:
    """tool_call_id -> stored answer, persisted as JSON, last 500 kept."""










    def __init__(self, path: str | None = None, keep: int = IDEMPOTENCY_KEEP, writer=None):
        self._path = path or os.path.join(account_dir(), "idempotency.json")
        self._keep = max(1, int(keep))
        self._texts: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.RLock()
        self._loaded = threading.Event()
        self._unreadable = False
        self._writer = writer or WriteBehind(name="ai-agent-idempotency")
        try:
            threading.Thread(target=self._load, name="ai-agent-idempotency-load", daemon=True).start()
        except RuntimeError:
            self._load()

    def _load(self) -> None:
        texts: OrderedDict[str, str] = OrderedDict()
        data = None
        try:
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, RecursionError) as exc:






            self._unreadable = True
            log_warning(f"The saved tool history could not be read ({exc}); it is kept as it is "
                        "and no earlier call is replayed from it this session.")
        if isinstance(data, dict) and isinstance(data.get("entries"), list):
            for entry in data["entries"][-self._keep:]:
                if isinstance(entry, dict) and entry.get("id"):
                    try:
                        texts[str(entry["id"])] = json.dumps(entry, default=str, separators=(",", ":"))
                    except (TypeError, ValueError, RecursionError):
                        continue

        with self._lock:
            for key, text in self._texts.items():
                texts.pop(key, None)
                texts[key] = text
            self._texts = texts
            while len(self._texts) > self._keep:
                self._texts.popitem(last=False)
            self._loaded.set()

    @property
    def ready(self) -> bool:
        return self._loaded.is_set()

    def _wait(self) -> None:
        if not self._loaded.is_set():
            self._loaded.wait(timeout=10.0)

    def _save(self) -> None:
        self._writer.schedule(self._path, self._text)

    def _text(self) -> str:
        with self._lock:
            return '{"entries":[' + ",".join(self._texts.values()) + "]}"

    def flush(self, wait: bool = False) -> None:
        self._writer.flush(wait=wait)

    def close(self) -> None:
        self._writer.close()

    @property
    def unreadable(self) -> bool:
        """True when the file on disk exists but could not be read."""
        self._wait()
        return self._unreadable

    def get(self, tool_call_id: str) -> dict | None:
        self._wait()
        if self._unreadable:



            with self._lock:
                text = self._texts.get(tool_call_id) if tool_call_id in self._texts else None
            return json.loads(text) if text else None
        with self._lock:
            text = self._texts.get(tool_call_id)
        if text is None:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None

    def put(self, tool_call_id: str, kind: str, payload, payload_json: str | None = None) -> None:
        """Store an answer."""

        self._wait()
        head = json.dumps({"id": tool_call_id, "kind": kind}, separators=(",", ":"))[:-1]
        if payload_json is None:
            try:
                payload_json = json.dumps(payload, default=str, separators=(",", ":"))
            except (TypeError, ValueError) as exc:
                log_warning(f"Idempotency entry not stored: {exc}")
                return
        text = f'{head},"payload":{payload_json},"ts":{time.time():.3f}}}'
        with self._lock:
            self._texts.pop(tool_call_id, None)
            self._texts[tool_call_id] = text
            while len(self._texts) > self._keep:
                self._texts.popitem(last=False)
        self._save()


class ToolExecutor(QObject):
    tool_started = pyqtSignal(object)



    tool_finished = pyqtSignal(str, bool, str, float, str, object)
    permission_needed = pyqtSignal(str, str, str, object)
    permission_resolved = pyqtSignal(str, str)

    question_needed = pyqtSignal(str, str, str, object, bool, int, str)
    question_resolved = pyqtSignal(str, str)
    project_changed = pyqtSignal(int)


    run_blocked = pyqtSignal(str, str)

    def __init__(self, registry, session, settings, parent=None):
        super().__init__(parent)
        self._registry = registry
        self._session = session
        self._settings = settings
        self._closed = False
        self._executing: set[str] = set()
        self._table = IdempotencyTable()
        self._waiting_for_history: dict[str, dict] = {}
        self._pending: dict[str, dict] = {}
        self._questions: dict[str, dict] = {}




        self._refused_costly: dict[str, set[str]] = {}
        self._run_mode: dict[str, tuple[str, str]] = {}








        self._cancelled: OrderedDict[str, None] = OrderedDict()
        self._touched: dict[str, set] = {}
        self._snapshots: dict[str, RunSnapshot] = {}
        self._budgets: dict[str, object] = {}
        self._background: dict[str, tuple] = {}
        self._threads: dict[str, str] = {}
        self._run_index: dict[str, int] = {}
        self._prompts: dict[str, str] = {}




        self._inflight: dict[str, tuple[str, str, float, bool]] = {}


        self._answered: OrderedDict[str, None] = OrderedDict()

        self._blocked: dict[str, str] = {}
        self._layer_baseline: dict[str, int] = {}


        self._code_allowed: set[str] = set()
        self.last_snapshot: RunSnapshot | None = None
        self.history = CheckpointHistory()



        self.watchdog = MainThreadWatchdog(describe=self._inflight_description,
                                           on_blocked=self._on_main_thread_blocked,
                                           on_tick=self._sweep_deadlines)
        self.watchdog.start()
        stalls.add_context_provider(self._inflight_description)



        self.follower = follow.Follower()




        self.scratch = scratch.ScratchLedger()



        self.stacker = layer_order.Stacker()
        self._refresh_proxy()

    @staticmethod
    def _refresh_proxy() -> None:
        """Point the guarded opener at whatever proxy QGIS is set to right now."""
        try:
            from .security import apply_qgis_proxy
            apply_qgis_proxy()
        except Exception as exc:  # noqa: BLE001 - a fetch without a proxy beats no plugin
            log_warning(f"QGIS proxy not applied to tool fetches: {exc}")










    def _inflight_description(self) -> str:
        """What was running when the event loop stopped, for the log line."""
        now = time.monotonic()
        parts = [f"{name} ({now - started:.1f}s in flight)"
                 for _, name, started, in_background in self._inflight.values() if not in_background]
        return ", ".join(parts[:3])

    def _on_main_thread_blocked(self, seconds: float, who: str) -> None:
        """The watchdog saw the event loop stop."""




























        machine.note_freeze(seconds, who)
        if who:




            sentence = (f"QGIS stopped answering for {seconds:.0f} seconds during {who}. The window "
                        "was frozen for that long, so this one call is refused and the ceilings "
                        "come down for a few minutes.")
        else:
            sentence = (f"QGIS stopped answering for {seconds:.0f} seconds, with no tool call of this "
                        "run in flight. Nothing is refused; the window was busy with something else.")
        for run_id in list(self._run_mode):
            if who:
                self._blocked[run_id] = sentence
            self.run_blocked.emit(run_id, sentence)

    def _sweep_deadlines(self, now: float | None = None) -> None:
        """One tick of the event loop: cancel any call past its budget."""
        if self._waiting_for_history and self._table.ready and not self._closed:
            waiting, self._waiting_for_history = self._waiting_for_history, {}
            for call in waiting.values():
                self.handle_tool_call(call)
        now = time.monotonic() if now is None else float(now)
        for tool_call_id, (run_id, name, started, in_background) in list(self._inflight.items()):
            spent = now - started





            budget = (limits.current("CALL_MAX_SECONDS_BACKGROUND") if in_background
                      else limits.main_budget(name))
            if spent <= budget:
                continue
            if not in_background:



                continue
            self._inflight.pop(tool_call_id, None)
            entry = self._background.pop(tool_call_id, None)
            if entry is not None:
                background.cancel(entry[1])
            self._answer_once(tool_call_id)
            self._fail({"tool_call_id": tool_call_id, "run_id": run_id, "name": name},
                       Err.INVALID_ARGS,
                       f"{name} ran for {spent:.0f} seconds without answering, over the "
                       f"{budget:.0f} second budget for one tool call on this computer, and was "
                       "cancelled.",
                       "Ask for less in one call: a smaller area, fewer features, one layer instead of "
                       "the whole catalogue. Do not send the same call again unchanged.",
                       spent)

    def _answer_once(self, tool_call_id: str) -> None:
        """Remember that this call has been answered, so a late result is dropped."""
        self._answered[tool_call_id] = None
        self._answered.move_to_end(tool_call_id)
        while len(self._answered) > CANCELLED_KEEP:
            self._answered.popitem(last=False)

    def _note_slow_main_thread(self, run_id: str, name: str, duration: float) -> None:
        """A main-thread handler that came back late: name it and hold the run."""






        budget = limits.main_budget(name)




        machine.note_slow_call(name, duration, budget)
        sentence = (f"{name} held the QGIS main thread for {duration:.0f} seconds, over the "
                    f"{budget:.0f} second budget for a call that runs there. "
                    "The window was frozen for that long.")
        log_warning(sentence)
        if run_id:
            self._blocked[run_id] = sentence
            self.run_blocked.emit(run_id, sentence)



    def begin_run(self, run_id: str, mode: str, approval: str, thread_id: str = "",
                  prompt: str = "") -> None:





        self._refresh_proxy()
        self._run_mode[run_id] = (mode, approval)
        self._threads[run_id] = thread_id or ""

        self._prompts[run_id] = str(prompt or "")
        self._touched[run_id] = set()
        self._cancelled.pop(run_id, None)
        self._blocked.pop(run_id, None)
        self._layer_baseline[run_id] = self._layer_count()
        if guards is not None:
            self._budgets[run_id] = guards.RunBudget()
        self.follower.enabled = bool(getattr(self._settings, "follow_edits", True))
        self.follower.begin()
        self.scratch.begin()
        self.stacker.begin()

    def cancel_run(self, run_id: str) -> None:
        self._cancelled[run_id] = None
        self._cancelled.move_to_end(run_id)
        while len(self._cancelled) > CANCELLED_KEEP:
            self._cancelled.popitem(last=False)
        for tool_call_id, (task_run, task) in list(self._background.items()):
            if task_run == run_id:
                self._background.pop(tool_call_id, None)
                background.cancel(task)
        for tool_call_id, call in list(self._pending.items()):
            if call.get("run_id") == run_id:
                self._pending.pop(tool_call_id, None)
                self._session.send_permission_response(tool_call_id, run_id, Decision.DENY)
                self._table.put(tool_call_id, "deny", {})
                self.permission_resolved.emit(tool_call_id, Decision.DENY)
        for tool_call_id, call in list(self._questions.items()):
            if call.get("run_id") == run_id:
                self._questions.pop(tool_call_id, None)
                self._fail(call, Err.CANCELLED, tr("The run was cancelled by the user."),
                           "Stop here and wait for the next user message.")
                self.question_resolved.emit(tool_call_id, "")

    def cancel_background(self) -> None:
        """Plugin unload: every tool task still on the wire is asked to stop, and the idempotency table lands on disk before the thread that writes it."""


        self._closed = True
        self._waiting_for_history.clear()
        self._executing.clear()
        self._background.clear()
        self._inflight.clear()
        self.watchdog.stop()
        background.cancel_all()




        try:
            self.follower.end()
            self.stacker.end()
        except Exception as exc:  # noqa: BLE001 - unload never fails on a follower
            log_warning(f"Canvas follower not stopped at unload: {exc}")
        self._table.close()

    @staticmethod
    def _layer_count() -> int:
        try:
            return len(QgsProject.instance().mapLayers())
        except Exception:  # noqa: BLE001 - no project is a baseline of zero, never a failed run
            return 0

    def end_run(self, run_id: str) -> RunSnapshot | None:
        with stalls.probe("executor.end_run"):
            return self._end_run(run_id)

    def _end_run(self, run_id: str) -> RunSnapshot | None:

        if (any(c.get("run_id") == run_id for c in [*self._pending.values(), *self._questions.values()])
                or any(c.get("run_id") == run_id for c in self._waiting_for_history.values())
                or any(task_run == run_id for task_run, _task in self._background.values())):
            self.cancel_run(run_id)
        self._run_mode.pop(run_id, None)
        self._touched.pop(run_id, None)
        self._budgets.pop(run_id, None)
        self._refused_costly.pop(run_id, None)
        self._blocked.pop(run_id, None)
        self._layer_baseline.pop(run_id, None)
        self._code_allowed.discard(run_id)
        snapshot = self._snapshots.pop(run_id, None)
        thread_id = self._threads.pop(run_id, "")
        run_index = self._run_index.pop(run_id, 0)
        prompt = self._prompts.pop(run_id, "")
        if snapshot is not None and snapshot.captured:
            self.last_snapshot = snapshot
            self._record_after(run_id, thread_id, run_index, snapshot, prompt)

        self.stacker.place_pending()
        self.stacker.end()
        self.follower.now()
        self.follower.end()

        self.scratch.end()
        return snapshot

    def _record_after(self, run_id: str, thread_id: str, run_index: int, snapshot: RunSnapshot,
                      prompt: str = "") -> None:
        """The run's diff, kept on the snapshot for the controller, and a second capture when the run changed something: the state to come back to."""


        try:
            diff = snapshot.diff()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Snapshot diff failed: {exc}")
            return
        snapshot.last_diff = diff
        changed = int(diff.get("changed_layers") or 0)



        if not diff_changed(diff):
            return
        after = RunSnapshot(f"{run_id or 'run'}-after")
        try:
            captured = after.capture()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"After-run snapshot failed: {exc}")
            captured = False
        if captured:
            self.history.add(thread_id, KIND_AFTER, run_id, run_index, after, changed,
                             changed_layer_items(diff), prompt)

    def snapshot_for(self, run_id: str) -> RunSnapshot | None:
        return self._snapshots.get(run_id)

    def changed_layers(self, run_id: str) -> int:
        return len(self._touched.get(run_id, ()))



    def handle_tool_call(self, call: dict) -> None:
        if self._closed or not isinstance(call, dict):
            return
        tool_call_id = str(call.get("tool_call_id") or "")
        run_id = str(call.get("run_id") or "")
        name = str(call.get("name") or "")
        if not self._table.ready and getattr(QCoreApplication, "instance", lambda: None)() is not None:




            if tool_call_id not in self._waiting_for_history:
                if len(self._waiting_for_history) >= 128:
                    self._session.send_tool_error(tool_call_id, run_id, Err.EXECUTION_FAILED,
                                                  "The saved tool history is still loading; retry later.", "")
                    return
                self._waiting_for_history[tool_call_id] = dict(call)
            return
        if not tool_call_id:
            log_warning("tool_call without tool_call_id dropped")
            return
        entry = self._table.get(tool_call_id)
        if entry is not None:
            self._replay(entry, tool_call_id, run_id)
            return
        if (tool_call_id in self._pending or tool_call_id in self._questions
                or tool_call_id in self._executing):
            return
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        call = dict(call, args=args)
        if run_id in self._cancelled:
            self._fail(call, Err.CANCELLED, tr("The run was cancelled by the user."),
                       "Stop here and wait for the next user message.")
            return
        if run_id not in self._run_mode:





            self._fail(call, Err.CANCELLED,
                       tr("This run has ended; the call was not executed."),
                       "Stop here and wait for the next user message.")
            return
        if self._registry.get_tool(name) is None:
            self._fail(call, Err.TOOL_NOT_FOUND, f"Unknown tool: {name}",
                       "Call search_tools with a description of what you need, then call_tool with the exact name.")
            return
        if name == ASK_USER:
            self._ask_user(call)
            return
        danger = self._danger_for(name, args, call.get("danger"))
        self._drop_unused_bbox(name, args)
        verdict = self._guard_check(name, args)
        if verdict.get("error"):
            self._fail(call, verdict.get("code") or Err.INVALID_ARGS, verdict["error"], verdict.get("suggestion", ""))
            return
        if verdict.get("destructive"):
            danger = Danger.DESTRUCTIVE
        call["danger"] = danger
        call["overwrites"] = verdict.get("overwrites") or []
        self._drop_unknown_flags(name, args)



        mode = self._run_mode.get(run_id, (self._settings.mode, ""))[0]
        approval = self._settings.approval
        if mode == Mode.ASK and danger != Danger.READ:
            self._fail(call, Err.READ_ONLY_MODE,
                       tr("Question mode is read only: {tool} would modify the project.").format(tool=name),
                       "Explain what you would do and ask the user whether to go ahead.")
            return
        budget = self._budgets.get(run_id)
        over = budget.charge(poll=bool(call.get("poll"))) if budget is not None else None
        if over:
            self._fail(call, Err.RUN_BUDGET, over, "Answer the user now with what was done; do not call more tools.")
            return
        held = self._blocked_reason(run_id, danger)
        if held:
            self._fail(call, Err.RUN_BUDGET, held,
                       "Carry on with the rest of the work in smaller steps (one layer at a time, a smaller "
                       "area); reads always pass. Do not resend this exact call, and tell the user QGIS "
                       "was busy for a moment.")
            return
        crowded = self._layers_over_budget(run_id, danger, name)
        if crowded:
            self._fail(call, Err.RUN_BUDGET, crowded["error"], crowded["suggestion"])
            return
        if name == CODE_TOOL:








            refused = code_guard.refusal_for(str(args.get("code") or ""))
            if refused:
                self._fail(call, refused["code"], refused["error"], refused["suggestion"])
                return
        costly = self._costly_check(name, args)
        if costly.get("error"):







            self._fail(call, costly.get("code") or Err.EXECUTION_FAILED,
                       costly["error"], costly.get("suggestion", ""))
            return
        if costly and self._call_key(name, args) in self._refused_costly.get(run_id, ()):
            self._fail(call, Err.PERMISSION_DENIED,
                       f"The user already refused this exact {costly['label']} run in this answer.",
                       "Do not ask again with the same arguments. Say what it would have cost, "
                       "and ask the user in words for a smaller zone or another object class.")
            return
        if costly:


            call["danger"] = Danger.DESTRUCTIVE
            call["sentence"] = costly["sentence"]
            call["costly"] = True
            self._pending[tool_call_id] = call
            self._session.send_permission_response(tool_call_id, run_id, Decision.PENDING)
            self.permission_needed.emit(tool_call_id, run_id, costly["sentence"], args)
            return
        always = guards is not None and guards.always_confirm(name, args)
        if always and name == CODE_TOOL and run_id in self._code_allowed:






            log(f"ALLOW {name} (the user allowed code for this answer)")
            self._session.send_permission_response(tool_call_id, run_id, Decision.ALLOW)
            self._execute(call)
            return
        if always or self._asks(approval, danger):
            if not always and self._settings.is_allowed(self._project_path(), name):
                self._session.send_permission_response(tool_call_id, run_id, Decision.ALLOW_PROJECT)
                self._execute(call)
                return
            call["always"] = always
            self._pending[tool_call_id] = call
            sentence = str(call.get("sentence") or tr("Run {tool}").format(tool=name))
            if name == CODE_TOOL:
                sentence = sentence.rstrip() + " " + tr("Allowing also covers the other code this answer runs.")
                call["sentence"] = sentence
            self._session.send_permission_response(tool_call_id, run_id, Decision.PENDING)
            self.permission_needed.emit(tool_call_id, run_id, sentence, args)
            return
        self._execute(call)



    def _ask_user(self, call: dict) -> None:
        """ask_user never runs a handler: the panel shows the question, the answer is the result."""
        tool_call_id, run_id, args = str(call.get("tool_call_id")), str(call.get("run_id") or ""), call["args"]
        question = str(args.get("question") or "").strip()
        if not question:
            self._fail(call, Err.INVALID_ARGS, "ask_user needs a question.",
                       "Pass the question as one sentence, with 2 to 4 options when there are natural choices.")
            return
        raw = args.get("options") if isinstance(args.get("options"), list) else []
        options = [str(o).strip() for o in raw if str(o).strip()][:4]
        free_text = bool(args.get("allow_free_text", True)) or not options
        recommended = recommended_index(args.get("recommended"), options)
        why = " ".join(str(args.get("why") or "").split())[:120]
        call["sentence"] = question
        self._questions[tool_call_id] = call

        self._session.send_permission_response(tool_call_id, run_id, Decision.PENDING)
        self.question_needed.emit(tool_call_id, run_id, question, options, free_text, recommended, why)

    def on_question_answered(self, tool_call_id: str, answer: str) -> None:
        call = self._questions.pop(tool_call_id, None)
        if call is None:
            return
        run_id = str(call.get("run_id") or "")
        answer = (answer or "").strip()
        if not answer:
            self._fail(call, Err.CANCELLED, tr("The user dismissed the question."),
                       "Stop here and wait for the next user message.")
            self.question_resolved.emit(tool_call_id, "")
            return
        result = {"answer": answer}
        self._table.put(tool_call_id, "result", result)
        self._session.send_tool_result(tool_call_id, run_id, result)
        log(f"ANSWER {tool_call_id}: {answer[:80]}")
        self.tool_finished.emit(tool_call_id, True, answer, 0.0, "", result)
        self.question_resolved.emit(tool_call_id, answer)

    @staticmethod
    def _asks(approval: str, danger: str) -> bool:
        return ((approval == Approval.CAREFUL and danger != Danger.READ)
                or (approval == Approval.ASK and danger == Danger.DESTRUCTIVE))

    def on_approval_changed(self, approval: str) -> None:
        """The user picked another permission level while cards were open: the cards the new level would not have shown are allowed now."""


        for tool_call_id, call in list(self._pending.items()):
            if call.get("always") or call.get("costly"):
                continue
            if self._asks(approval, str(call.get("danger") or Danger.READ)):
                continue
            self._pending.pop(tool_call_id, None)
            run_id = str(call.get("run_id") or "")
            log(f"ALLOW {call.get('name')} (permission level is now {approval})")
            self._session.send_permission_response(tool_call_id, run_id, Decision.ALLOW)
            self.permission_resolved.emit(tool_call_id, Decision.ALLOW)
            self._execute(call)

    def on_permission_decided(self, tool_call_id: str, decision: str, edits: dict | None = None) -> None:
        """The user's answer to a permission card, with the arguments they may have changed."""











        call = self._pending.pop(tool_call_id, None)
        if call is None:
            return
        run_id = str(call.get("run_id") or "")
        name = str(call.get("name") or "")
        if decision not in (Decision.ALLOW, Decision.ALLOW_PROJECT, Decision.DENY):




            log_warning(f"Permission decision {decision!r} for {name} is not one of "
                        "allow/allow_project/deny; the card is left open.")
            self._pending[tool_call_id] = call
            return
        if decision == Decision.DENY:
            self._session.send_permission_response(tool_call_id, run_id, Decision.DENY)
            self._table.put(tool_call_id, "deny", {})
            if call.get("costly"):
                self._refused_costly.setdefault(run_id, set()).add(
                    self._call_key(name, call.get("args") or {}))
            log(f"DENY {name} (user)")
            return
        problem = self._apply_edits(call, edits)
        if problem is not None:
            self._session.send_permission_response(tool_call_id, run_id, Decision.DENY)
            self._fail(call, problem[0], problem[1], problem[2], 0.0)
            return
        if name == CODE_TOOL and run_id:


            self._code_allowed.add(run_id)
        if decision == Decision.ALLOW_PROJECT:



            if edits or name == CODE_TOOL:
                decision = Decision.ALLOW
            else:
                self._settings.allow(self._project_path(), name)
        else:
            decision = Decision.ALLOW
        self._session.send_permission_response(tool_call_id, run_id, decision)
        self._execute(call)

    def _apply_edits(self, call: dict, edits: dict | None) -> tuple[str, str, str] | None:
        """Put the user's corrections into the call, re-checked."""





        if not isinstance(edits, dict) or not edits:
            return None
        name = str(call.get("name") or "")
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        known = (getattr(self._registry.get_tool(name), "input_schema", None) or {}).get("properties") or {}
        taken = {key: value for key, value in edits.items() if key in known}
        if not taken:
            return None
        args = dict(args)
        args.update(taken)
        self._drop_unknown_flags(name, args)
        self._drop_unused_bbox(name, args)
        call["args"] = args
        log(f"EDITED {name}: {', '.join(sorted(taken))}")
        verdict = self._guard_check(name, args)
        if verdict.get("error"):
            return (str(verdict.get("code") or Err.INVALID_ARGS), str(verdict["error"]),
                    str(verdict.get("suggestion") or "Ask the user for a value the guards accept."))





        call["overwrites"] = verdict.get("overwrites") or []
        if verdict.get("destructive"):
            call["danger"] = Danger.DESTRUCTIVE
        crs = self._crs_guard(name, args)
        if crs:
            return (Err.CRS_GUARD, crs[0], crs[1])
        return None



    @staticmethod
    def _project_path() -> str:
        return QgsProject.instance().fileName() or "(unsaved)"

    def _blocked_reason(self, run_id: str, danger: str) -> str:
        """The freeze this run has already had, said once, or ''."""






        sentence = self._blocked.get(run_id)
        if not sentence or danger == Danger.READ:
            return ""
        self._blocked.pop(run_id, None)
        return sentence

    def _layers_over_budget(self, run_id: str, danger: str, name: str = "") -> dict | None:
        """Refuse when this run has already added its allowance of layers."""
        if danger == Danger.READ or name in _LAYER_FREE_TOOLS or run_id not in self._layer_baseline:
            return None
        added = self._layer_count() - self._layer_baseline[run_id]
        cap = limits.current("MAX_LAYERS_PER_RUN")
        if added < cap:
            return None
        return {
            "error": (f"This run has added {added} layers to the project, the cap for one answer "
                      f"({cap}). Every layer costs a redraw of the tree and the "
                      "canvas, and a project nobody asked to grow this much is its own kind of damage."),





            "suggestion": ("Remove the working layers this answer no longer needs with remove_layer: the cap "
                           "counts what is in the project, so that frees room immediately and you can carry "
                           "on. One merged or clipped layer instead of one per source works too. Only if "
                           "every layer is needed, stop, tell the user what was added and what is left, and "
                           "ask whether to continue in a new message."),
        }

    def _drop_unused_bbox(self, name: str, args: dict) -> None:
        """A bbox a tool does not take costs nothing, so it must not refuse the call."""









        if "bbox" not in args:
            return
        known = (getattr(self._registry.get_tool(name), "input_schema", None) or {}).get("properties") or {}
        if "bbox" not in known:
            args.pop("bbox", None)

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

    def _replay(self, entry: dict, tool_call_id: str, run_id: str) -> None:
        kind, payload = entry.get("kind"), entry.get("payload")
        log(f"Replaying stored answer for {tool_call_id} ({kind})")
        if kind == "result":
            self._session.send_tool_result(tool_call_id, run_id, payload)
        elif kind == "error" and isinstance(payload, dict):
            self._session.send_tool_error(tool_call_id, run_id, payload.get("code", Err.EXECUTION_FAILED),
                                          payload.get("message", ""), payload.get("suggestion", ""))
        else:
            self._session.send_permission_response(tool_call_id, run_id, Decision.DENY)

    def wire_answer(self, tool_call_id: str) -> dict:
        """What the server was sent for this call, and so what the model read."""






        entry = self._table.get(str(tool_call_id or "")) or {}
        kind = str(entry.get("kind") or "")
        if kind not in ("result", "error"):
            return {}
        return {"kind": kind, "payload": entry.get("payload")}

    def _fail(self, call: dict, code: str, message: str, suggestion: str, duration: float = 0.0,
              detail: str = "") -> None:
        tool_call_id, run_id = str(call.get("tool_call_id")), str(call.get("run_id") or "")
        message, suggestion, detail = scrub_secrets(message), scrub_secrets(suggestion), scrub_secrets(detail)
        self._table.put(tool_call_id, "error", {"code": code, "message": message, "suggestion": suggestion})
        self._executing.discard(tool_call_id)
        self._session.send_tool_error(tool_call_id, run_id, code, message, suggestion)
        log_warning(f"{code} {call.get('name')}: {message[:200]}")
        self.tool_finished.emit(tool_call_id, False, f"{code}: {message}", duration, detail or suggestion, None)



    def _execute(self, call: dict) -> None:
        tool_call_id = str(call.get("tool_call_id"))
        if tool_call_id in self._executing or self._closed:
            return


        self._executing.add(tool_call_id)
        try:
            self._prepare_and_execute(call)
        except Exception as exc:  # noqa: BLE001 - preparation must also answer the call
            self._fail(call, Err.EXECUTION_FAILED, f"{type(exc).__name__}: {exc}",
                       "Inspect the project before retrying the operation.")

    def _prepare_and_execute(self, call: dict) -> None:
        run_id = str(call.get("run_id") or "")
        name, args, danger = str(call.get("name")), call.get("args") or {}, call.get("danger", Danger.READ)
        guard = self._crs_guard(name, args)
        if guard is not None:
            self._fail(call, Err.CRS_GUARD, guard[0], guard[1])
            return
        if danger != Danger.READ:
            snapshot = self._prepare_snapshot(run_id, name, args, danger, call.get("overwrites") or [])
            if snapshot is not None and getattr(snapshot, "copy_errors", None):
                self._fail(call, Err.EXECUTION_FAILED, "A backup failed; this run cannot make further changes.",
                           "Check free disk space and folder access, then start a new run.")
                return
            if snapshot is not None and snapshot.has_pending_copies() and self._copy_then_run(call, snapshot):
                return
        self._execute_now(call)

    def _copy_then_run(self, call: dict, snapshot) -> bool:
        """The backups this call needs are large: copy them off the main thread, then run the call."""

        name = str(call.get("name") or "")
        tool_call_id = str(call.get("tool_call_id"))
        run_id = str(call.get("run_id") or "")

        def done(_result, error_text: str):
            self._background.pop(tool_call_id, None)
            self._inflight.pop(tool_call_id, None)
            if self._closed or tool_call_id in self._answered:
                return
            if error_text:
                log_warning(f"Backup before {name} failed: {error_text.split(chr(10), 1)[0]}")
                if not self._closed:
                    self._fail(call, Err.EXECUTION_FAILED, "The backup failed; the operation was not started.",
                               "Check free disk space and folder access before trying again.")
                return
            self._execute_now(call)

        task = background.run_off_thread(f"AI Agent: backup before {name}", snapshot.run_pending_copies, done)
        if task is None:
            snapshot.run_pending_copies()
            return False
        self._background[tool_call_id] = (run_id, task)
        self._inflight[tool_call_id] = (run_id, name, time.monotonic(), True)
        stalls.mark(f"backup off-thread before {name}")
        return True

    def _execute_now(self, call: dict) -> None:
        if self._closed or str(call.get("tool_call_id")) in self._answered:
            return
        run_id = str(call.get("run_id") or "")
        name, args, danger = str(call.get("name")), call.get("args") or {}, call.get("danger", Danger.READ)
        if run_id in self._cancelled:
            self._fail(call, Err.CANCELLED, tr("The run was cancelled by the user."),
                       "Stop here and wait for the next user message.")
            return
        self.tool_started.emit(call)
        log(f"RUN {name} danger={danger} args={self._args_digest(args)}")






        if danger != Danger.READ:
            with stalls.probe("follow.approach"):
                self.follower.approach(name, args)
        started = time.monotonic()
        tool_call_id = str(call.get("tool_call_id"))


        call["scratch_mark"] = self.scratch.mark()
        if self._start_background(call, name, args, started):
            return



        self._inflight[tool_call_id] = (run_id, name, started, False)
        try:
            with stalls.probe(f"tool.{name}"):
                result = self._registry.execute(name, args)
        except Exception as exc:  # noqa: BLE001
            result = {"_error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()[-2000:]}
        self._deliver(call, result, started)

    def _start_background(self, call: dict, name: str, args: dict, started: float) -> bool:
        """Run a handler that declared itself off-thread safe in a QgsTask."""






        if not self._background_ok(name, args):
            return False
        if call.get("overwrites"):



            return False
        tool_call_id = str(call.get("tool_call_id"))

        def work():
            return self._registry.execute(name, args)

        def done(result, error_text: str):
            self._background.pop(tool_call_id, None)
            if error_text:
                result = {"_error": error_text.split("\n", 1)[0], "traceback": error_text}
            self._deliver(call, result, started)


        task = background.run_off_thread(f"AI Agent: {name}", work, done)
        if task is None:
            return False
        self._background[tool_call_id] = (str(call.get("run_id") or ""), task)


        self._inflight[tool_call_id] = (str(call.get("run_id") or ""), name, started, True)
        return True

    def _background_ok(self, name: str, args: dict) -> bool:
        """The tool's own answer: a bool, or a predicate on the arguments for a tool like add_data whose remote branches are safe and whose local-file."""


        tool = self._registry.get_tool(name)
        flag = getattr(tool, "background", False)
        if callable(flag):
            try:
                return bool(flag(args))
            except Exception as exc:  # noqa: BLE001
                log_warning(f"background predicate for {name} failed: {exc}")
                return False
        return bool(flag)

    def _deliver(self, call: dict, result, started: float) -> None:
        """The one exit for a tool result, whichever thread produced it."""
        if self._closed:
            return
        tool_call_id, run_id = str(call.get("tool_call_id")), str(call.get("run_id") or "")
        name, args = str(call.get("name")), call.get("args") or {}
        danger = call.get("danger", Danger.READ)
        duration = time.monotonic() - started
        in_background = self._inflight.pop(tool_call_id, (None, None, None, False))[3]
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
        with stalls.probe("result.scrub"):
            result = neutralise_result(scrub_result(result), name, open_world=outside)



        with stalls.probe("layer_order.place"):
            stacked = self.stacker.place_pending()
        if stacked and isinstance(result, dict) and "_error" not in result:
            result["drawn_under"] = stacked
        error = self._error_of(result)
        if error is not None:
            code, message, suggestion = error
            detail = result.get("traceback", "") if isinstance(result, dict) else ""
            self._fail(call, code, message, suggestion, duration, detail)
            return




        if danger != Danger.READ and isinstance(result, dict):
            with stalls.probe("postcondition.verify"):
                verified = postcondition.verify(name, args, result)
            if verified:
                result["verified"] = verified


        shown = result
        with stalls.probe("result.serialise"):
            result, detail, text = self._bounded(result, registered)
        self._table.put(tool_call_id, "result", result, payload_json=text)
        self._executing.discard(tool_call_id)
        self._session.send_tool_result(tool_call_id, run_id, result)
        self.tool_finished.emit(tool_call_id, True, self._summary(name, result), duration, detail, shown)



        with stalls.probe("scratch.note"):
            self.scratch.note_call(name, args, call.get("scratch_mark"))
        if danger != Danger.READ:
            named = self._note_touched(run_id, args, result)



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
        mutates = guards is not None and name in guards.DATA_MUTATORS
        with stalls.probe("snapshot.backups"):
            if danger == Danger.DESTRUCTIVE or mutates:
                snapshot.backup_targets(args)
            if overwrites:
                snapshot.backup_files(overwrites)
            if name == "execute_code":
                snapshot.backup_mentioned(str(args.get("code") or ""))
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



    def _crs_guard(self, name: str, args: dict) -> tuple[str, str] | None:
        """Refuse a metric distance on a geographic layer. Returns (message, suggestion)."""
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
