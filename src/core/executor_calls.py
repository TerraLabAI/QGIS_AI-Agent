# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





from __future__ import annotations

import copy
import time

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QCoreApplication

from . import background, code_guard, security, stalls
from .executor_guards import CODE_TOOL
from .log_scrub import scrub_result, scrub_secrets
from .logger import log, log_warning
from .plan import autopilot_allowed
from .protocol import Approval, Danger, Decision, Mode, recommended_index
from .protocol import ClientErrorCode as Err
from .run_report import VERIFY_RUN, build_report
from .serialization import DETAILS_MARKER, cut_string, dump_json, error_details
from .tool_registry import spec

try:
    from ..tools import guards
except ImportError:
    guards = None

try:
    from ..tools._layers import pin_layer_names
except ImportError:
    pin_layer_names = None

def _coded(refusal: dict) -> str:


    return error_details({k: v for k, v in refusal.items() if k in ("hint", "variant")})


def tr(text: str) -> str:
    return QCoreApplication.translate("ToolExecutor", text)


def _shown_value(value):

    if isinstance(value, str):
        return cut_string(value, 300)
    if isinstance(value, (dict, list)):
        text = dump_json(value)
        return value if len(text) <= 300 else cut_string(text, 300)
    return value


class _ExecutorCalls:


    def handle_tool_call(self, call: dict) -> None:
        if self._closed or not isinstance(call, dict):
            return
        self._wake_watchdog()
        tool_call_id = str(call.get("tool_call_id") or "")
        run_id = str(call.get("run_id") or "")
        name = str(call.get("name") or "")
        if not self._table.ready and getattr(QCoreApplication, "instance", lambda: None)() is not None:




            if tool_call_id not in self._waiting_for_history:
                if len(self._waiting_for_history) >= 128:
                    self._session.send_tool_error(tool_call_id, run_id, Err.EXECUTION_FAILED,
                                                  "The saved tool history is still loading, briefly.", "")
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
        if tool_call_id in self._pending or tool_call_id in self._questions:




            self._session.send_permission_response(tool_call_id, run_id, Decision.PENDING)
            return
        if tool_call_id in self._executing:
            return
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        call = dict(call, args=args)
        if run_id in self._cancelled:
            self._fail(call, Err.CANCELLED, tr("The run was cancelled by the user."),
                       "The user stopped the run.")
            return
        if run_id not in self._run_mode:





            self._fail(call, Err.CANCELLED,
                       tr("This run has ended; the call was not executed."),
                       "The run is over.")
            return
        if self._registry.get_tool(name) is None:
            self._fail(call, Err.TOOL_NOT_FOUND, f"Unknown tool: {name}",
                       "search_tools finds a tool by description; call_tool then takes the exact name.")
            return
        declared = spec(name)
        if getattr(declared, "waits_on_user", False):
            self._ask_user(call)
            return
        if name == VERIFY_RUN:
            self._verify_run(call)
            return
        self._resolve_output_paths(name, args)
        own_files = self._own_files_of(run_id)
        danger = self._danger_for(name, args, call.get("danger"), own_files)
        if name == CODE_TOOL:

            danger = self._code_plan(call, args, danger)
        self._drop_unused_bbox(name, args)
        loose = self._pin_loose_names(call, danger)
        if loose is not None:
            self._fail(call, *loose)
            return
        removes = getattr(declared, "removes_layer_at", "")
        if removes and danger == Danger.DESTRUCTIVE and self._added_this_run(run_id, args.get(removes)):



            danger = Danger.WRITE
        verdict = self._guard_check(name, args, own_files)
        if verdict.get("error"):
            self._fail(call, verdict.get("code") or Err.INVALID_ARGS, verdict["error"], verdict.get("suggestion", ""))
            return
        if verdict.get("destructive"):
            danger = Danger.DESTRUCTIVE
        call["danger"] = danger
        call["overwrites"] = verdict.get("overwrites") or []
        call["creates"] = verdict.get("creates") or []
        self._drop_unknown_flags(name, args)





        mode = self._run_mode.get(run_id, (self._settings.mode, ""))[0]
        approval = self._approval_now()
        if mode == Mode.ASK and danger != Danger.READ:
            self._fail(call, Err.READ_ONLY_MODE,
                       tr("Question mode is read only: {tool} would modify the project.").format(tool=name),
                       "Nothing changes in Question mode unless the user says yes.")
            return
        budget = self._budgets.get(run_id)
        over = budget.charge(poll=bool(call.get("poll"))) if budget is not None else None
        if over:
            self._fail(call, Err.RUN_BUDGET, over, "This run allows no further tool call.")
            return
        held = self._blocked_reason(run_id, danger)
        if held:





            self._fail(call, Err.RUN_BUDGET, held + " This call did not run and nothing changed.",
                       "The hold is spent; the same call runs now. A smaller step (one layer, a smaller "
                       "area) avoids it again. QGIS was busy.")
            return
        if name == CODE_TOOL:








            refused = code_guard.refusal_for(str(args.get("code") or ""))
            if refused:
                self._fail(call, refused["code"], refused["error"], refused["suggestion"], details=_coded(refused))
                return








        unvouched = (guards.unvouched_urls(name, args, call.get("listed_urls") or ())
                     if guards is not None and approval != Approval.AUTO else [])
        if unvouched:
            call["unvouched"] = unvouched
        costly = self._costly_check(name, args)
        if costly.get("error"):






            self._fail(call, costly.get("code") or Err.EXECUTION_FAILED,
                       costly["error"], costly.get("suggestion", ""), details=_coded(costly))
            return
        if costly and self._call_key(name, args) in self._refused_costly.get(run_id, ()):
            self._fail(call, Err.PERMISSION_DENIED,
                       f"The user already refused this exact {costly['label']} run in this answer.",
                       "The same arguments get the same refusal; a smaller zone or another object "
                       "class costs less.")
            return
        if costly and approval == Approval.AUTO and costly.get("slow_only") and not unvouched:





            costly = {}
        if costly:
            if not self._preflight_before_card(call, costly):
                self._costly_card(call, costly)
            return
        always = guards is not None and guards.always_confirm(name, args)
        if name == CODE_TOOL:
            always = self._code_always(call, always, approval)
        if unvouched:
            always = True
        asks = always or self._asks(approval, danger)






        per_answer = not always
        if asks and per_answer and name in self._run_denied.get(run_id, ()):



            self._fail(call, Err.PERMISSION_DENIED,
                       f"The user declined {name} for this answer.",
                       "Further calls of it in this answer are refused the same way.")
            return
        if asks and per_answer and name in self._run_allowed.get(run_id, ()):







            log(f"ALLOW {name} (the user allowed it for this answer)")
            self._session.send_permission_response(tool_call_id, run_id, Decision.ALLOW)
            self._execute(call)
            return
        if asks:
            if not always and (self._settings.is_allowed(self._project_path(), name)
                               or (name == CODE_TOOL and self._code_class_allowed(call))):
                self._session.send_permission_response(tool_call_id, run_id, Decision.ALLOW_PROJECT)
                self._execute(call)
                return
            call["always"] = always
            self._pending[tool_call_id] = call
            sentence = str(call.get("sentence") or tr("Run {tool}").format(tool=name))







            if per_answer:
                sentence = sentence.rstrip() + " " + tr(
                    "Your answer covers the other {tool} calls this answer makes.").format(tool=name)
                call["sentence"] = sentence
            self._session.send_permission_response(tool_call_id, run_id, Decision.PENDING)
            self.permission_needed.emit(tool_call_id, run_id, sentence, args)
            return
        self._execute(call)

    def unvouched_for(self, tool_call_id: str) -> list[str]:

        call = self._pending.get(str(tool_call_id or "")) or {}
        return list(call.get("unvouched") or [])

    def card_group(self, tool_call_id: str) -> str:









        call = self._pending.get(str(tool_call_id or "")) or {}
        name = str(call.get("name") or "")
        if not call or call.get("costly") or name == CODE_TOOL:
            return ""
        if guards is not None and guards.always_confirm(name, call.get("args") or {}):
            return ""
        return str(call.get("run_id") or "")

    def _costly_card(self, call: dict, costly: dict) -> None:


        tool_call_id, run_id = str(call.get("tool_call_id")), str(call.get("run_id") or "")
        call["danger"] = Danger.DESTRUCTIVE
        call["sentence"] = costly["sentence"]
        call["costly"] = True
        self._pending[tool_call_id] = call
        self._session.send_permission_response(tool_call_id, run_id, Decision.PENDING)
        self.permission_needed.emit(tool_call_id, run_id, costly["sentence"], call.get("args") or {})

    def _preflight_before_card(self, call: dict, costly: dict) -> bool:











        name = str(call.get("name") or "")
        check = getattr(self._registry.get_tool(name), "preflight", None)
        if not callable(check):
            return False
        tool_call_id, run_id = str(call.get("tool_call_id")), str(call.get("run_id") or "")


        args = copy.deepcopy(call.get("args") or {})

        def done(result, error_text: str) -> None:
            self._background.pop(tool_call_id, None)
            self._inflight.pop(tool_call_id, None)
            self._executing.discard(tool_call_id)
            if self._closed or tool_call_id in self._answered:
                return
            if run_id in self._cancelled or run_id not in self._run_mode:
                self._fail(call, Err.CANCELLED, tr("The run was cancelled by the user."),
                           "The user stopped the run.")
                return
            if error_text:
                log_warning(f"preflight for {name} failed: {error_text.splitlines()[0]}")
            refused = None if error_text else self._error_of(result)
            if refused is not None:
                log(f"REFUSED BEFORE CARD {name}: {refused[1][:120]}")
                self._fail(call, *refused, details=error_details(scrub_result(result)))
                return
            self._costly_card(call, costly)


        self._executing.add(tool_call_id)
        task = None
        if self._background_ok(name, call.get("args") or {}):
            task = background.run_off_thread(f"AI Agent: check {name}", lambda: check(args), done)
        if task is None:
            try:
                result, error_text = check(args), ""
            except Exception as exc:  # noqa: BLE001
                result, error_text = None, f"{type(exc).__name__}: {exc}"
            done(result, error_text)
            return True

        self._background[tool_call_id] = (run_id, task)
        self._inflight[tool_call_id] = (run_id, name, time.monotonic(), True)
        return True



    def _ask_user(self, call: dict) -> None:

        tool_call_id, run_id, args = str(call.get("tool_call_id")), str(call.get("run_id") or ""), call["args"]
        question = str(args.get("question") or "").strip()
        if not question:
            self._fail(call, Err.INVALID_ARGS, "ask_user needs a question.",
                       "One sentence works, with 2 to 4 options when there are natural choices.")
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
                       "The user gave no answer.")
            self.question_resolved.emit(tool_call_id, "")
            return
        result = {"answer": answer}
        self._table.put(tool_call_id, "result", result)
        self._session.send_tool_result(tool_call_id, run_id, result)
        log(f"ANSWER {tool_call_id}: {answer[:80]}")
        self.tool_finished.emit(tool_call_id, True, answer, 0.0, "", result)
        self.question_resolved.emit(tool_call_id, answer)

    def _verify_run(self, call: dict) -> None:


        tool_call_id, run_id = str(call.get("tool_call_id")), str(call.get("run_id") or "")
        with stalls.probe("run_report.build"):
            report = scrub_result(build_report(self._snapshots.get(run_id), self._written.get(run_id, ()),
                                               call_warnings=self._call_warnings.get(run_id, ()),
                                               working_copies=self.scratch.working_copies(),
                                               hidden=self.scratch.hidden_ids()))
        self._table.put(tool_call_id, "result", report)
        self._session.send_tool_result(tool_call_id, run_id, report)
        log(f"VERIFY_RUN {run_id[:8]}: {len(report.get('warnings') or [])} warnings")

    def _added_this_run(self, run_id: str, layer) -> bool:


        snapshot = self._snapshots.get(run_id)
        if snapshot is None or not snapshot.captured:
            return False
        value = str(layer or "")
        project = QgsProject.instance()
        layer = project.mapLayer(value) if value else None
        found = [layer] if layer is not None else (project.mapLayersByName(value) if value else [])
        return bool(found) and all(each.id() not in snapshot.layer_ids for each in found)

    def _own_files_of(self, run_id: str) -> frozenset:


        return frozenset(self._own_files.get(run_id, ()))

    def _approval_now(self) -> str:

        approval = self._settings.approval
        if approval == Approval.AUTO and not autopilot_allowed():
            return Approval.ASK
        return approval

    @staticmethod
    def _asks(approval: str, danger: str) -> bool:
        return ((approval == Approval.CAREFUL and danger != Danger.READ)
                or (approval == Approval.ASK and danger == Danger.DESTRUCTIVE))

    def open_cards(self) -> int:



        return len(self._pending)

    def on_permission_decided(self, tool_call_id: str, decision: str, edits: dict | None = None) -> None:












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
            elif run_id and name and not call.get("always"):
                self._run_denied.setdefault(run_id, set()).add(name)
            log(f"DENY {name} (user)")
            return
        problem = self._apply_edits(call, edits)
        if problem is not None:
            self._session.send_permission_response(tool_call_id, run_id, Decision.DENY)

            edited = {"edited_by_user": {"values": call["user_edits"]}} if call.get("user_edits") else {}
            self._fail(call, problem[0], problem[1], problem[2], 0.0, details=error_details(edited))
            return
        recard = call.pop("recard", None)
        if recard:


            log(f"ASK AGAIN {name}: the edit changed what the card priced")
            if not self._preflight_before_card(call, recard):
                self._costly_card(call, recard)
            return
        if (run_id and name and not edits and not call.get("costly")
                and not call.get("always")):




            self._run_allowed.setdefault(run_id, set()).add(name)
        if call.get("unvouched") and not edits:


            security.vouch_for_urls(call["unvouched"])
        if decision == Decision.ALLOW_PROJECT:




            if name == CODE_TOOL and not edits and not call.get("unvouched"):

                decision = self._code_decided(call, decision)
            elif edits or name == CODE_TOOL or call.get("unvouched"):
                decision = Decision.ALLOW
            else:
                self._settings.allow(self._project_path(), name)
        else:
            decision = Decision.ALLOW
            if name == CODE_TOOL:
                self._code_decided(call, decision)
        self._session.send_permission_response(tool_call_id, run_id, decision)
        self._execute(call)

    def _pin_loose_names(self, call: dict, danger: str) -> tuple[str, str, str] | None:









        name = str(call.get("name") or "")
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        if pin_layer_names is None:
            return None
        if not (danger == Danger.DESTRUCTIVE or (guards is not None and name in guards.DATA_MUTATORS)):
            return None
        pins: dict = {}
        loose = pin_layer_names(args, name, pins)
        call["layer_pins"] = pins
        if loose:
            return (Err.LAYER_NOT_FOUND, loose["_error"], loose["suggestion"])
        return None

    def _apply_edits(self, call: dict, edits: dict | None) -> tuple[str, str, str] | None:






        if not isinstance(edits, dict) or not edits:
            return None
        name = str(call.get("name") or "")
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        known = (getattr(self._registry.get_tool(name), "input_schema", None) or {}).get("properties") or {}
        taken = {key: value for key, value in edits.items() if key in known}
        if not taken:
            return None
        asked = args
        args = dict(args)
        args.update(taken)
        self._resolve_output_paths(name, args)
        self._drop_unknown_flags(name, args)
        self._drop_unused_bbox(name, args)
        call["args"] = args
        log(f"EDITED {name}: {', '.join(sorted(taken))}")



        changed = {key: {"before": _shown_value(asked.get(key)), "after": _shown_value(args.get(key))}
                   for key in taken if asked.get(key) != args.get(key)}
        if changed:
            call["user_edits"] = changed




        loose = self._pin_loose_names(call, str(call.get("danger") or Danger.READ))
        if loose is not None:
            return loose
        verdict = self._guard_check(name, args, self._own_files_of(str(call.get("run_id") or "")))
        if verdict.get("error"):
            return (str(verdict.get("code") or Err.INVALID_ARGS), str(verdict["error"]),
                    str(verdict.get("suggestion") or "The guards refuse this value."))





        call["overwrites"] = verdict.get("overwrites") or []
        call["creates"] = verdict.get("creates") or []
        if verdict.get("destructive"):
            call["danger"] = Danger.DESTRUCTIVE
        crs = self._crs_guard(name, args)
        if crs:
            return (Err.CRS_GUARD, crs[0], crs[1])






        costly = self._costly_check(name, args)
        if costly.get("error") and "confirm_area_km2" in known:
            costly = self._confirmed_by_card(name, args) or costly
        if costly.get("error"):
            return (str(costly.get("code") or Err.EXECUTION_FAILED), str(costly["error"]),
                    str(costly.get("suggestion") or "The guards refuse this value."))
        if costly and (not call.get("costly") or costly.get("sentence") != call.get("sentence")):


            call["recard"] = costly
        return None

    def _confirmed_by_card(self, name: str, args: dict) -> dict:








        from . import executor_guards

        guard = executor_guards.cost_guard
        if guard is None:
            return {}
        try:
            label = guard.costly_label(name, args)
            area = guard.zone_area_km2(args, label) if label else None
        except Exception as exc:  # noqa: BLE001
            log_warning(f"cost_guard could not measure the edited {name}: {exc}")
            return {}
        if area is None:
            return {}
        trial = dict(args, confirm_area_km2=round(float(area), 1))
        verdict = self._costly_check(name, trial)
        if not verdict or verdict.get("error"):
            return {}
        args["confirm_area_km2"] = trial["confirm_area_km2"]
        return verdict

    @staticmethod
    def _project_path() -> str:
        return QgsProject.instance().fileName() or "(unsaved)"

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

    def remember_answer(self, tool_call_id: str, result=None, error: dict | None = None) -> None:








        tool_call_id = str(tool_call_id or "")
        if not tool_call_id or self._closed or not self._table.ready:
            return
        if isinstance(error, dict):
            self._table.put(tool_call_id, "error", {
                "code": str(error.get("code") or Err.CANCELLED),
                "message": scrub_secrets(str(error.get("message") or "")),
                "suggestion": scrub_secrets(str(error.get("suggestion") or ""))})
        else:
            self._table.put(tool_call_id, "result", scrub_result(result))

    def _fail(self, call: dict, code: str, message: str, suggestion: str, duration: float = 0.0,
              detail: str = "", details: str = "") -> None:






        tool_call_id, run_id = str(call.get("tool_call_id")), str(call.get("run_id") or "")
        message, suggestion, detail = scrub_secrets(message), scrub_secrets(suggestion), scrub_secrets(detail)
        wire = f"{message}{DETAILS_MARKER}{scrub_secrets(details)}" if details else message
        self._table.put(tool_call_id, "error", {"code": code, "message": wire, "suggestion": suggestion})
        self._executing.discard(tool_call_id)
        self._session.send_tool_error(tool_call_id, run_id, code, wire, suggestion, self._code_class_of(call))
        log_warning(f"{code} {call.get('name')}: {message[:200]}")
        self.tool_finished.emit(tool_call_id, False, f"{code}: {message}", duration, detail or suggestion, None)
