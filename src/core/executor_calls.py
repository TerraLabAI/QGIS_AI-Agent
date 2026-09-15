# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Receiving tool calls and the cards they show: the executor's entry point."""




from __future__ import annotations

import copy
import time

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QCoreApplication

from . import background, code_guard, stalls
from .executor_guards import BATCH_TOOL, CODE_TOOL
from .log_scrub import scrub_result, scrub_secrets
from .logger import log, log_warning
from .protocol import Approval, Danger, Decision, Mode, recommended_index
from .protocol import ClientErrorCode as Err
from .run_report import VERIFY_RUN, build_report

try:
    from ..tools import guards
except ImportError:
    guards = None

try:
    from ..tools._layers import pin_layer_names
except ImportError:
    pin_layer_names = None

ASK_USER = "ask_user"


def tr(text: str) -> str:
    return QCoreApplication.translate("ToolExecutor", text)


class _ExecutorCalls:


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
        if name == VERIFY_RUN:
            self._verify_run(call)
            return
        self._resolve_output_paths(name, args)
        danger = self._danger_for(name, args, call.get("danger"))
        self._drop_unused_bbox(name, args)
        loose = self._pin_loose_names(name, args, danger)
        if loose is not None:
            self._fail(call, *loose)
            return
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
        crowded = self._layers_over_budget(run_id, danger, name, args)
        if crowded:
            self._fail(call, Err.RUN_BUDGET, crowded["error"], crowded["suggestion"])
            return
        if name == CODE_TOOL:








            refused = code_guard.refusal_for(str(args.get("code") or ""))
            if refused:
                self._fail(call, refused["code"], refused["error"], refused["suggestion"])
                return
        costly = self._batch_check(args) if name == BATCH_TOOL else self._costly_check(name, args)
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
            if not self._preflight_before_card(call, costly):
                self._costly_card(call, costly)
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

    def _costly_card(self, call: dict, costly: dict) -> None:
        """Imagery credits or a data volume: a card every time, whatever the approval setting or the per-project allow list, with the size the user is."""

        tool_call_id, run_id = str(call.get("tool_call_id")), str(call.get("run_id") or "")
        call["danger"] = Danger.DESTRUCTIVE
        call["sentence"] = costly["sentence"]
        call["costly"] = True
        self._pending[tool_call_id] = call
        self._session.send_permission_response(tool_call_id, run_id, Decision.PENDING)
        self.permission_needed.emit(tool_call_id, run_id, costly["sentence"], call.get("args") or {})

    def _preflight_before_card(self, call: dict, costly: dict) -> bool:
        """The tool's own refusals, before its size card."""










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
                           "Stop here and wait for the next user message.")
                return
            if error_text:
                log_warning(f"preflight for {name} failed: {error_text.splitlines()[0]}")
            refused = None if error_text else self._error_of(result)
            if refused is not None:
                log(f"REFUSED BEFORE CARD {name}: {refused[1][:120]}")
                self._fail(call, *refused)
                return
            self._costly_card(call, costly)


        self._executing.add(tool_call_id)
        task = None
        if self._background_ok(name, call.get("args") or {}):
            task = background.run_off_thread(f"AI Agent: check {name}", lambda: check(args), done)
        if task is None:
            try:
                result, error_text = check(args), ""
            except Exception as exc:  # noqa: BLE001 - a check that cannot decide leaves the card
                result, error_text = None, f"{type(exc).__name__}: {exc}"
            done(result, error_text)
            return True

        self._background[tool_call_id] = (run_id, task)
        self._inflight[tool_call_id] = (run_id, name, time.monotonic(), True)
        return True



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

    def _verify_run(self, call: dict) -> None:
        """The server's delivery check before the final answer: facts read off the project (core/run_report.py), never a handler, never a card, never."""

        tool_call_id, run_id = str(call.get("tool_call_id")), str(call.get("run_id") or "")
        with stalls.probe("run_report.build"):
            report = scrub_result(build_report(self._snapshots.get(run_id), self._written.get(run_id, ()),
                                               call_warnings=self._call_warnings.get(run_id, ()),
                                               working_copies=self.scratch.working_copies(),
                                               hidden=self.scratch.hidden_ids()))
        self._table.put(tool_call_id, "result", report)
        self._session.send_tool_result(tool_call_id, run_id, report)
        log(f"VERIFY_RUN {run_id[:8]}: {len(report.get('warnings') or [])} warnings")

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
        recard = call.pop("recard", None)
        if recard:


            log(f"ASK AGAIN {name}: the edit changed what the card priced")
            if not self._preflight_before_card(call, recard):
                self._costly_card(call, recard)
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

    def _pin_loose_names(self, name: str, args: dict, danger: str) -> tuple[str, str, str] | None:
        """Point a data-changing call's layer names at one layer, or the refusal saying why not."""






        if pin_layer_names is None:
            return None
        for inner, inner_args in self._calls_in(name, args):
            inner_danger = danger if name != BATCH_TOOL else self._danger_for(inner, inner_args, None)
            if not (inner_danger == Danger.DESTRUCTIVE
                    or (guards is not None and inner in guards.DATA_MUTATORS)):
                continue
            loose = pin_layer_names(inner_args, inner)
            if loose:
                if name == BATCH_TOOL:
                    message = f"{inner} in batch_commands: {loose['_error']} Nothing in the batch ran."
                else:
                    message = loose["_error"]
                return (Err.LAYER_NOT_FOUND, message, loose["suggestion"])
        return None

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
        self._resolve_output_paths(name, args)
        self._drop_unknown_flags(name, args)
        self._drop_unused_bbox(name, args)
        call["args"] = args
        log(f"EDITED {name}: {', '.join(sorted(taken))}")




        loose = self._pin_loose_names(name, args, str(call.get("danger") or Danger.READ))
        if loose is not None:
            return loose
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




        costly = self._batch_check(args) if name == BATCH_TOOL else self._costly_check(name, args)
        if costly.get("error") and name != BATCH_TOOL and "confirm_area_km2" in known:
            costly = self._confirmed_by_card(name, args) or costly
        if costly.get("error"):
            return (str(costly.get("code") or Err.EXECUTION_FAILED), str(costly["error"]),
                    str(costly.get("suggestion") or "Ask the user for a value the guards accept."))
        if costly and (not call.get("costly") or costly.get("sentence") != call.get("sentence")):


            call["recard"] = costly
        return None

    def _confirmed_by_card(self, name: str, args: dict) -> dict:
        """The cost verdict of an edited credit call whose new card is the confirmation, or {}."""







        from . import executor_guards

        guard = executor_guards.cost_guard
        if guard is None:
            return {}
        try:
            label = guard.costly_label(name, args)
            area = guard.zone_area_km2(args, label) if label else None
        except Exception as exc:  # noqa: BLE001 - the guard's own refusal stands
            log_warning(f"cost_guard could not measure the edited {name}: {exc}")
            return {}
        if area is None or area > guard.HARD_MAX_KM2:
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
        """Keep the answer to a call the panel answered itself (a proposal card)."""







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
