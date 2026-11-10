# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later













from __future__ import annotations

from ..core import accounts
from ..core.logger import log
from ..core.protocol import Decision
from .executor_code import tr


_MAX_CARDS = 3


class _ExecutorAccounts:
    def _execute(self, call: dict) -> None:
        tool_call_id = str(call.get("tool_call_id"))
        accounts.bind_call(tool_call_id, self._threads.get(str(call.get("run_id") or ""), ""))
        super()._execute(call)

    def _ask_account(self, call: dict, result: dict, started: float) -> bool:


        need = result.pop(accounts.MARK, None)
        failure = result.pop("failure", None)
        tool_call_id, run_id = str(call.get("tool_call_id")), str(call.get("run_id") or "")
        cards = int(call.get("account_cards") or 0)
        if not isinstance(need, dict) or cards >= _MAX_CARDS or not accounts.enabled():
            result.clear()
            result.update(failure if isinstance(failure, dict) else
                          {"_error": "The service refused the request.", "code": "PERMISSION_DENIED"})
            accounts.release_call(tool_call_id)
            return False
        call["account_cards"] = cards + 1
        call["account_wait"] = {"need": need, "failure": failure or {}, "started": started}
        call["sentence"] = tr("Access to {host}").format(host=need.get("host") or need.get("key") or "")
        self._executing.discard(tool_call_id)
        self._questions[tool_call_id] = call
        self._session.send_permission_response(tool_call_id, run_id, Decision.PENDING)
        self.account_needed.emit(tool_call_id, run_id, dict(need))
        log(f"ACCOUNT {tool_call_id}: {need.get('kind')} {need.get('host')}")
        return True

    def on_question_answered(self, tool_call_id: str, answer: str, attachments=None) -> None:
        call = self._questions.get(tool_call_id)
        wait = call.get("account_wait") if isinstance(call, dict) else None
        if not wait:
            super().on_question_answered(tool_call_id, answer, attachments)
            return
        self._questions.pop(tool_call_id, None)
        call.pop("account_wait", None)
        need = wait["need"]
        key = str(need.get("key") or "")
        answer = (answer or "").strip()
        choice = answer[len(accounts.ANSWER):] if answer.startswith(accounts.ANSWER) else ""
        thread_id = self._threads.get(str(call.get("run_id") or ""), "")
        if choice in ("once", "thread", "signed_in") and key and accounts.vault_ready(prompt=True):
            accounts.grant(key, tool_call_id, "once" if choice == "once" else "thread", thread_id)
            shown = {"once": tr("Allowed once"), "thread": tr("Allowed for this conversation"),
                     "signed_in": tr("Signed in")}[choice]
            self.question_resolved.emit(tool_call_id, shown)
            call["account_used"] = {"host": need.get("host"), "how": choice, "key": key}
            self._execute(call)
            return
        out = dict(wait["failure"])
        if answer and not choice:
            out["user_said"] = answer
            shown = answer
        else:
            out["skipped_sign_in" if need.get("kind") != "consent" else "declined"] = (
                f"The user did not give access to {need.get('host') or key}.")
            shown = ""
        accounts.release_call(tool_call_id)
        self.question_resolved.emit(tool_call_id, shown)
        self._deliver(call, out, wait["started"], held=True)
