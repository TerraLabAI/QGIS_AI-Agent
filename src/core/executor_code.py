# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





















































from __future__ import annotations

import contextlib
import hashlib
import os
import time

from qgis.PyQt.QtCore import QCoreApplication

from . import code_effects as ce
from . import tuning
from .logger import log, log_warning
from .protocol import Approval, Decision, Mode
from .serialization import error_details
from .snapshot import RunSnapshot
from .snapshot_in_place import put_back_in_place
from .tool_registry import coded_fact

try:
    from ..tools import code_runtime, guards
except ImportError:
    code_runtime = guards = None

CODE_TOOL = "execute_code"

FILE_WRITES_GRANT = "execute_code:file_writes"










NO_CARD_BUDGET_S = 60.0
CARD_BUDGET_S = 120.0


def tr(text: str) -> str:
    return QCoreApplication.translate("ToolExecutor", text)


def _fingerprint(code: str) -> str:
    return hashlib.sha256(" ".join(str(code or "").split()).encode("utf-8", "replace")).hexdigest()[:24]


def _classes_on() -> bool:

    from .code_tripwire import enabled

    return enabled()


def _editing_layer_ids() -> set:
    from qgis.core import QgsProject, QgsVectorLayer

    ids = set()
    for layer in list(QgsProject.instance().mapLayers().values()):
        try:
            if isinstance(layer, QgsVectorLayer) and layer.isEditable():
                ids.add(layer.id())
        except RuntimeError:
            continue
    return ids


def _discard_new_edit_sessions(before: set) -> None:

    from qgis.core import QgsProject, QgsVectorLayer

    for layer in list(QgsProject.instance().mapLayers().values()):
        try:
            if isinstance(layer, QgsVectorLayer) and layer.isEditable() and layer.id() not in before:
                layer.rollBack()
                log(f"execute_code: discarded the edit session it opened on {layer.name()}")
        except RuntimeError:
            continue


def _ceiling(call: dict | None = None) -> str:





    return ce.FW if call is not None and call.get("served_recipe") is True else ce.FP





_DECLARED = {"read": ce.R, "project": ce.P, "write": ce.FW, "delete": ce.D}


def _declared(call: dict) -> str | None:
    if call.get("served_recipe") is not True:
        return None
    return _DECLARED.get(str(call.get("served_effect") or ""))


def _libraries(unknown: list) -> list:

    names = {str(item)[len("import "):].split(".")[0] for item in unknown or []
             if str(item).startswith("import ")}
    return sorted(n for n in names if n)


def _unknown_items(unknown: list) -> list:

    return [f"import {name}" for name in _libraries(unknown)] + [
        str(item) for item in unknown or [] if not str(item).startswith("import ")]


class _ExecutorCode:


    def _code_plan(self, call: dict, args: dict, danger: str) -> str:

        if not _classes_on():
            call["code_plan"] = {"cls": ce.ASK, "granted": ce.D, "reasons": [], "libraries": [],
                                 "floor": ce.D, "doors": True}
            return danger
        code = str(args.get("code") or "")
        verdict = ce.classify(code, tool_class=self._code_tool_class, commit_class=self._commit_class)
        cls = verdict.cls
        declared = _declared(call)
        if cls == ce.ASK and declared is not None:
            cls = declared
        remembered = self._code_escalated.get(_fingerprint(code))
        if remembered and ce.RANK[remembered] > ce.RANK[cls]:
            cls = remembered



        call["code_plan"] = {"cls": cls, "granted": cls if cls != ce.ASK else ce.FW,
                             "reasons": verdict.reasons, "proven": cls != ce.ASK and verdict.cls != ce.ASK,
                             "libraries": _libraries(verdict.unknown),
                             "floor": verdict.floor if verdict.cls == ce.ASK else verdict.cls,
                             "doors": verdict.unprovable}
        if cls == ce.FW and _ceiling(call) == ce.FW:
            return ce.DANGER[ce.FP]
        return ce.DANGER[cls]

    @staticmethod
    def _code_class_of(call: dict) -> str:

        if call.get("name") != CODE_TOOL:
            return ""
        return str((call.get("code_plan") or {}).get("cls") or "")

    def _code_tool_class(self, name: str) -> str | None:
        if code_runtime is None:
            return None
        try:
            return code_runtime.tool_code_class(name, registry=self._registry)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"tool_code_class({name}) failed: {exc}")
            return None

    @staticmethod
    def _commit_class(strings: list, deletes: bool = False) -> tuple[str, str]:

        from qgis.core import QgsProject

        project = QgsProject.instance()
        named = []
        for text in strings:
            layer = project.mapLayer(text)
            found = [layer] if layer is not None else project.mapLayersByName(text)
            named.extend(found)
        if not named:


            from qgis.core import QgsVectorLayer

            named = [lyr for lyr in project.mapLayers().values() if isinstance(lyr, QgsVectorLayer)]
        files = sorted({os.path.basename(str(lyr.source()).split("|", 1)[0]) for lyr in named
                        if getattr(lyr, "providerType", lambda: "")() != "memory"} - {""})
        if not files:
            return ce.P, ""
        shown = ", ".join(files[:3])
        if deletes:
            return ce.D, tr("deletes features from {files}").format(files=shown)
        return ce.FW, tr("saves an edit into {files}").format(files=shown)

    def _code_always(self, call: dict, always: bool, approval: str) -> bool:




        if approval == Approval.AUTO:
            return False
        plan = call.get("code_plan") or {}
        cls = plan.get("cls", ce.ASK)
        if ce.RANK[cls] <= ce.RANK[_ceiling(call)]:
            return False
        if self._code_class_allowed(call):
            return False
        if plan.get("reasons"):
            call["sentence"] = tr("Run Python code ({what}).").format(what="; ".join(plan["reasons"][:4]))
        return True

    def _code_class_allowed(self, call: dict) -> bool:

        plan = call.get("code_plan") or {}
        return bool(plan.get("cls") == ce.FW
                    and self._settings.is_allowed(self._project_path(), FILE_WRITES_GRANT))



    def _code_context(self, call: dict):


        name = str(call.get("name") or "")
        if code_runtime is None:
            return contextlib.nullcontext()
        run_id = str(call.get("run_id") or "")
        if name != CODE_TOOL:
            return code_runtime.calling(code_runtime.CallContext(
                chat=self._threads.get(run_id, "") or run_id, registry=self._registry,
                restore_previous=getattr(self, "restore_previous", None)))
        plan = call.get("code_plan") or {}
        granted = plan.get("granted", ce.D)
        carded = bool(call.get("carded")) or plan.get("cls", ce.ASK) == ce.ASK
        return code_runtime.calling(code_runtime.CallContext(
            granted=granted, chat=self._threads.get(run_id, "") or run_id,
            timeout_s=(tuning.ceiling("execute_code_card_budget_s", CARD_BUDGET_S, 30.0) if carded
                       else tuning.ceiling("execute_code_no_card_budget_s", NO_CARD_BUDGET_S, 20.0)),
            registry=self._registry))



    @staticmethod
    def _code_note_edit_sessions(call: dict) -> None:


        call["code_editing_before"] = _editing_layer_ids()

    def _code_restore_point(self, call: dict, fresh: bool) -> bool:

        plan = call.get("code_plan") or {}
        if ce.RANK[plan.get("granted", ce.D)] < ce.RANK[ce.P]:
            return True
        run_id = str(call.get("run_id") or "")
        if fresh:

            snapshot = self._snapshots.get(run_id)
            if snapshot is not None and snapshot.captured:
                call["code_point"] = (snapshot, False)
                return True

            return bool(call.get("carded"))
        snapshot = RunSnapshot(f"{run_id or 'run'}-code")
        try:
            ok = snapshot.capture()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Restore point before execute_code failed: {exc}")
            ok = False
        if not ok:
            return bool(call.get("carded"))
        call["code_point"] = (snapshot, True)
        return True

    @staticmethod
    def _unsaved_edits() -> list:







        from .snapshot import unsaved_edit_layers

        return [layer.name() for layer in unsaved_edit_layers()]

    def _code_roll_back(self, call: dict) -> None:
        point = call.pop("code_point", None)
        if point is None:
            return
        snapshot, own = point






        before = call.pop("code_editing_before", None)
        opened = before is None or bool(_editing_layer_ids() - before)
        if before is not None:
            _discard_new_edit_sessions(before)
        held = self._unsaved_edits()
        if held:
            log_warning("execute_code not rolled back: unsaved edits on " + ", ".join(held[:4]))
            plan = call.get("code_plan") or {}
            plan["reasons"] = list(plan.get("reasons") or []) + [
                tr("ran with unsaved edits open on {layers}, so nothing was put back").format(
                    layers=", ".join(held[:3]))]
            if own:
                snapshot.discard()
            return
        if not opened and snapshot.unchanged_since_capture():

            log("execute_code stopped before it changed anything, the project is kept as it is")
            if own:
                snapshot.discard()
            return
        tool_call_id = str(call.get("tool_call_id"))



        self._inflight[tool_call_id] = (str(call.get("run_id") or ""), CODE_TOOL, time.monotonic(), False)
        try:



            outcome = put_back_in_place(snapshot) or snapshot.restore()
            log(f"execute_code rolled back: {outcome.get('message', '') if isinstance(outcome, dict) else ''}")
        except Exception as exc:  # noqa: BLE001
            log_warning(f"execute_code roll back failed: {exc}")
        finally:
            self._inflight.pop(tool_call_id, None)
            self.history.project_restored()
        if own:
            snapshot.discard()

    def _code_release(self, call: dict) -> None:
        point = call.pop("code_point", None)
        if point is not None and point[1]:
            point[0].discard()

    def _code_no_point(self, call: dict) -> None:

        plan = call.get("code_plan") or {}
        plan["reasons"] = list(plan.get("reasons") or []) + [
            tr("could not be saved first, so Undo could not take it back")]
        self._code_card(call)



    def _code_tripped(self, call: dict, result: dict) -> None:

        tool_call_id = str(call.get("tool_call_id"))
        need = result.get("needs_permission") or {}
        cls = str(need.get("class") or ce.D)
        reason = str(need.get("reason") or "")
        plan = call.setdefault("code_plan", {"reasons": []})
        code = str((call.get("args") or {}).get("code") or "")
        self._code_escalated[_fingerprint(code)] = cls
        if result.get("isolated"):

            self._code_release(call)
        else:
            self._code_roll_back(call)
        self._executing.discard(tool_call_id)
        plan.update({"cls": cls, "granted": cls, "reasons": [reason] if reason else plan.get("reasons", [])})
        call["danger"] = ce.DANGER[cls]
        log(f"execute_code tripped at {cls}: {reason}")
        run_id = str(call.get("run_id") or "")
        mode = self._run_mode.get(run_id, (self._settings.mode, ""))[0]
        if mode == Mode.ASK:
            self._fail(call, "READ_ONLY_MODE",
                       tr("Question mode is read only: the snippet {what}.").format(what=reason),
                       "", details=error_details(coded_fact(hint="question_mode_read_only", reason=reason)))
            return
        approval = self._approval_now()
        if ((not self._code_always(call, False, approval) and not self._asks(approval, call["danger"]))
                or self._code_run_granted(call)):
            if self._code_run_granted(call):
                log("ALLOW execute_code after a trip (the user allowed code for this run)")
            self._execute(call)
            return
        self._code_card(call)

    def _code_card(self, call: dict) -> None:
        tool_call_id, run_id = str(call.get("tool_call_id")), str(call.get("run_id") or "")
        plan = call.get("code_plan") or {}
        reasons = plan.get("reasons") or []
        call["always"] = True
        if reasons:
            call["sentence"] = tr("Run Python code ({what}).").format(what="; ".join(reasons[:4]))
        self._executing.discard(tool_call_id)
        self._pending[tool_call_id] = call
        self._session.send_permission_response(tool_call_id, run_id, Decision.PENDING)
        self.permission_needed.emit(tool_call_id, run_id, str(call.get("sentence") or tr("Run Python code")),
                                    call.get("args") or {})

    def grant_for(self, tool_call_id: str) -> str:








        call = self._pending.get(str(tool_call_id or "")) or {}
        if call.get("name") != CODE_TOOL or call.get("costly") or call.get("unvouched"):
            return ""
        plan = call.get("code_plan") or {}
        offers = ["file_writes"] if plan.get("cls") == ce.FW else []
        if self._run_grant_offered(call):
            offers.append("run")
            if not plan.get("proven"):
                held = self._code_run_unknown.get(str(call.get("run_id") or ""), set())
                libraries = sorted(set(plan.get("libraries") or []) | {
                    item[len("import "):] for item in held if item.startswith("import ")})
                offers.append("libs:" + ",".join(libraries) if libraries else "calls")
        return " ".join(offers)

    def _run_grant_offered(self, call: dict) -> bool:





        plan = call.get("code_plan") or {}
        if not plan.get("proven") and (plan.get("doors", True) or plan.get("floor", ce.D) == ce.D):
            return False
        return (self._approval_now() == Approval.ASK and _classes_on()
                and plan.get("cls", ce.ASK) != ce.D and plan.get("granted", ce.D) != ce.D)

    def _code_unknown(self, call: dict, run_id: str) -> ce.Verdict:



        plan = call.get("code_plan") or {}
        held = {item[len("import "):] for item in self._code_run_unknown.get(run_id, set())
                if item.startswith("import ")}
        return ce.classify(str((call.get("args") or {}).get("code") or ""), tool_class=self._code_tool_class,
                           commit_class=self._commit_class,
                           libraries=frozenset(held | set(plan.get("libraries") or [])))

    def drop_run_grant(self, run_id: str) -> None:

        self._code_run_grants.discard(str(run_id or ""))
        self._code_run_unknown.pop(str(run_id or ""), None)

    def _code_run_granted(self, call: dict) -> bool:









        run_id = str(call.get("run_id") or "")
        if not (run_id and run_id in self._code_run_grants and run_id not in self._cancelled
                and self._run_grant_offered(call)):
            return False
        plan = call.get("code_plan") or {}
        if plan.get("proven"):
            return True
        verdict = self._code_unknown(call, run_id)
        if verdict.cls != ce.ASK:
            return verdict.cls != ce.D
        if verdict.unprovable or verdict.floor == ce.D:
            return False
        return set(_unknown_items(verdict.unknown)) <= self._code_run_unknown.get(run_id, set())

    def _code_decided(self, call: dict, decision: str) -> str:

        call["carded"] = True
        plan = call.get("code_plan") or {}
        if decision == Decision.ALLOW_RUN:

            if self._run_grant_offered(call):
                run_id = str(call.get("run_id") or "")
                self._code_run_grants.add(run_id)
                if not plan.get("proven"):
                    held = self._code_run_unknown.setdefault(run_id, set())
                    held.update(_unknown_items(self._code_unknown(call, run_id).unknown))
            return Decision.ALLOW
        if decision == Decision.ALLOW_PROJECT and plan.get("cls") == ce.FW:
            self._settings.allow(self._project_path(), FILE_WRITES_GRANT)
            return Decision.ALLOW_PROJECT
        return Decision.ALLOW

