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
    from ..tools import code_runtime, guards, isolated_code
except ImportError:
    code_runtime = guards = isolated_code = None

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


def _user_layer_reasons(layers, reasons: list) -> list:




    from qgis.core import QgsProject

    project = QgsProject.instance()
    names: list[str] = []
    for text in sorted(layers or ()):
        found = project.mapLayer(text)
        name = found.name() if found is not None else str(text)
        if name not in names:
            names.append(name)
    effects: list[str] = []
    for reason in reasons:
        verb, _, what = str(reason).partition(" ")
        if verb in ("set", "sets", "change") and what and what not in effects:
            effects.append(what)
    if not names:
        said = tr("changes a layer you had before this run")
    elif len(names) == 1:
        said = tr("changes your layer {name}").format(name=names[0])
    else:
        said = tr("changes your layers {names}").format(names=", ".join(names[:3]))
    if effects:
        said = f"{said}: {', '.join(effects[:3])}"
    return [said] + [r for r in reasons if r == "writes a file" or str(r).startswith(("saves ", "deletes "))]


def _card_words(reasons: list) -> str:


    return "; ".join(tr("its changes to your project cannot be shown before it runs")
                     if r == ce.UNSEEN else str(r) for r in reasons[:4])


class _ExecutorCode:


    def _code_plan(self, call: dict, args: dict, danger: str) -> str:

        if not _classes_on():
            call["code_plan"] = {"cls": ce.ASK, "granted": ce.D, "reasons": []}
            return danger
        code = str(args.get("code") or "")
        run_id = str(call.get("run_id") or "")
        user_layer = self._user_layer_test(run_id)
        own_files = self._own_files_of(run_id)
        def commit_class(strings: list, deletes: bool = False) -> tuple[str, str]:
            return self._commit_class(strings, deletes, own_files)

        verdict = ce.classify(code, tool_class=self._code_tool_class, commit_class=commit_class,
                              user_layer=user_layer, own_names=self._own_kept_names(run_id, user_layer))
        cls = verdict.cls
        declared = _declared(call)
        if cls == ce.ASK and declared is not None:
            cls = declared
        elif (cls == ce.ASK and not verdict.unprovable and not verdict.user
              and ce.RANK[verdict.floor] <= ce.RANK[ce.FP]):




            cls = ce.higher(verdict.floor, ce.P)
        remembered = self._code_escalated.get(_fingerprint(code))
        if remembered and ce.RANK[remembered] > ce.RANK[cls]:
            cls = remembered
        reasons = verdict.reasons
        if verdict.user and cls in (ce.P, ce.FP):
            reasons = _user_layer_reasons(verdict.layers, reasons)
        call["code_plan"] = {"cls": cls, "granted": cls if cls != ce.ASK else ce.FW,
                             "reasons": reasons, "user": verdict.user,
                             "layers": None if verdict.layers is None else sorted(verdict.layers)}
        if cls == ce.FW and _ceiling(call) == ce.FW:
            return ce.DANGER[ce.FP]
        return ce.DANGER[cls]

    def _user_layer_test(self, run_id: str):





        from qgis.core import QgsProject

        from .context import _made_here

        snapshot = self._snapshots.get(run_id)
        before = snapshot.layer_ids if snapshot is not None and snapshot.captured else None
        thread_id = self._threads.get(run_id, "")
        project = QgsProject.instance()

        def test(text: str) -> bool:
            try:
                found = project.mapLayer(text)
                layers = [found] if found is not None else project.mapLayersByName(text)
            except Exception:  # noqa: BLE001
                return False
            return any((before is None or each.id() in before) and not _made_here(each, thread_id)
                       for each in layers)

        return test

    def _own_kept_names(self, run_id: str, user_layer) -> set:



        if code_runtime is None:
            return set()
        from qgis.core import QgsMapLayer
        from qgis.PyQt.QtCore import QObject

        def no_user_layer(value, depth: int = 0) -> bool:
            if isinstance(value, QgsMapLayer):
                try:
                    return not user_layer(value.id())
                except Exception:  # noqa: BLE001
                    return False
            if isinstance(value, QObject) or depth > 2:
                return False
            if isinstance(value, dict):
                return all(no_user_layer(v, depth + 1) for v in value.values())
            if isinstance(value, (list, tuple, set, frozenset)):
                return all(no_user_layer(v, depth + 1) for v in value)
            return True

        try:
            kept = code_runtime.kept(self._threads.get(run_id, "") or run_id)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"execute_code kept names not read: {exc}")
            return set()
        return {name for name, value in kept.items() if no_user_layer(value)}

    def _code_refused_before_card(self, call: dict) -> bool:






        code = str((call.get("args") or {}).get("code") or "")
        try:
            compile(code, "<snippet>", "exec")
        except SyntaxError as exc:
            self._fail(call, "INVALID_ARGS", f"execute_code raised SyntaxError: {exc.msg} (line {exc.lineno})", "",
                       details=error_details({"hint": "code_syntax_error", "line": exc.lineno, "executed": False}))
            return True
        except (ValueError, RecursionError, MemoryError):
            return False
        try:
            missing = ce.undefined_name(code, self._snippet_names(call))
        except Exception as exc:  # noqa: BLE001
            log_warning(f"execute_code name check failed: {exc}")
            return False
        if missing is None:
            return False
        name, line = missing
        chat = self._threads.get(str(call.get("run_id") or ""), "")
        if code_runtime is not None and code_runtime.dropped_help(chat, name):

            return False
        self._fail(call, "INVALID_ARGS",
                   f"name {name!r} is not defined (line {line}): the snippet would raise NameError there. "
                   "Nothing was run.", "",
                   details=error_details({"name": name, "line": line, "executed": False}))
        return True

    def _snippet_names(self, call: dict) -> set:



        import builtins
        import sys

        from qgis.core import QgsProject

        from .code_guard import DENIED_NAMES, build_safe_builtins
        from .code_namespace import PRELUDE, console_names
        from .code_owners import HELPER

        chat = self._threads.get(str(call.get("run_id") or ""), "")
        names = set(dir(builtins)) | set(build_safe_builtins()) | set(PRELUDE) | set(sys.modules)
        names |= {HELPER, "processing", "qgis", "print", "read_text", "validate_path"}
        names |= set(console_names(DENIED_NAMES))
        names |= set(code_runtime.bindings(None, QgsProject.instance())) | set(code_runtime.kept(chat))
        return names

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
    def _commit_class(strings: list, deletes: bool = False, own=frozenset()) -> tuple[str, str]:


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
        paths = {str(lyr.source()).split("|", 1)[0] for lyr in named
                 if getattr(lyr, "providerType", lambda: "")() != "memory"} - {""}
        files = sorted({os.path.basename(path) for path in paths} - {""})
        if not files:
            return ce.P, ""
        shown = ", ".join(files[:3])
        if deletes:
            return ce.D, tr("deletes features from {files}").format(files=shown)
        from .code_tripwire import is_own

        if own and all(is_own(os.path.abspath(path), own) for path in paths):
            return ce.FP, tr("saves an edit into {files}, which this conversation wrote").format(files=shown)
        return ce.FW, tr("saves an edit into {files}").format(files=shown)

    def _code_always(self, call: dict, always: bool, approval: str) -> bool:




        if approval == Approval.AUTO:
            return False
        plan = call.get("code_plan") or {}
        cls = plan.get("cls", ce.ASK)
        if ce.RANK[cls] <= ce.RANK[_ceiling(call)] and not (plan.get("user") and cls != ce.R):
            return False
        if self._code_class_allowed(call):
            return False
        if plan.get("reasons"):
            call["sentence"] = tr("Run Python code ({what}).").format(what=_card_words(plan["reasons"]))
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
            granted=granted, chat=self._threads.get(run_id, "") or run_id, own_files=self._own_files_of(run_id),
            timeout_s=(tuning.ceiling("execute_code_card_budget_s", CARD_BUDGET_S, 30.0) if carded
                       else tuning.ceiling("execute_code_no_card_budget_s", NO_CARD_BUDGET_S, 20.0)),
            registry=self._registry, run_id=run_id, stop=lambda: self.stop_requested.emit(run_id),
            before_in_qgis=lambda: self._code_point_now(call)))



    @staticmethod
    def _code_note_edit_sessions(call: dict) -> None:


        call["code_editing_before"] = _editing_layer_ids()

    def _code_restore_point(self, call: dict, fresh: bool) -> bool:



        call.pop("code_point_due", None)
        call.pop("code_isolated", None)
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
        if self._code_runs_isolated(call):





            call["code_point_due"] = True
            return True
        return self._code_take_point(call)

    def _code_runs_isolated(self, call: dict) -> bool:


        if isolated_code is None:
            return False
        try:
            isolated = bool(isolated_code.runs_isolated(call.get("args") or {}))
        except Exception as exc:  # noqa: BLE001
            log_warning(f"execute_code isolation check before its restore point failed: {exc}")
            isolated = False
        call["code_isolated"] = isolated
        return isolated

    def _code_point_now(self, call: dict) -> bool:


        if not call.pop("code_point_due", False):
            return True
        return self._code_take_point(call)

    def _code_take_point(self, call: dict) -> bool:
        plan = call.get("code_plan") or {}
        run_id = str(call.get("run_id") or "")
        snapshot = RunSnapshot(f"{run_id or 'run'}-code")
        try:
            ok = snapshot.capture(layer_ids=self._code_point_layers(run_id, plan.get("layers")))
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Restore point before execute_code failed: {exc}")
            ok = False
        if not ok:
            return bool(call.get("carded"))
        call["code_point"] = (snapshot, True)
        return True

    def _code_point_layers(self, run_id: str, named) -> list | None:




        before = getattr(self._snapshots.get(run_id), "layer_ids", None)
        if named is None or not before:
            return None
        from qgis.core import QgsProject

        project = QgsProject.instance()
        ids = set()
        for text in named:
            found = project.mapLayer(text)
            for layer in [found] if found is not None else project.mapLayersByName(text):
                if layer.id() in before:
                    ids.add(layer.id())
        return sorted(ids)

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

        if self._code_refused_before_card(call):
            return
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
        if not self._code_always(call, False, approval) and not self._asks(approval, call["danger"]):
            self._execute(call)
            return
        self._code_card(call)

    def _code_card(self, call: dict) -> None:
        tool_call_id, run_id = str(call.get("tool_call_id")), str(call.get("run_id") or "")
        plan = call.get("code_plan") or {}
        reasons = plan.get("reasons") or []
        call["always"] = True
        if reasons:
            call["sentence"] = tr("Run Python code ({what}).").format(what=_card_words(reasons))
        self._executing.discard(tool_call_id)
        self._pending[tool_call_id] = call
        self._session.send_permission_response(tool_call_id, run_id, Decision.PENDING)
        self.permission_needed.emit(tool_call_id, run_id, str(call.get("sentence") or tr("Run Python code")),
                                    call.get("args") or {})

    @staticmethod
    def _code_decided(call: dict) -> None:

        call["carded"] = True
