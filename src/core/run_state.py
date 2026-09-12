# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later









from __future__ import annotations

COMPOSING = "composing"
SENDING = "sending"
RUNNING = "running"
WAITING_USER = "waiting_user"
STOPPING = "stopping"
ENDED = "ended"


RESEND = "resend"
AWAIT_OUTCOME = "await_outcome"
AWAIT_RESUME = "await_resume"
NOTHING = ""


class RunBusy(RuntimeError):
    pass


class RunMachine:


    def __init__(self, keep: int = 8) -> None:
        self.current: dict | None = None
        self.phase: str = COMPOSING


        self.calls: dict[str, str] = {}
        self._cards: set[str] = set()
        self._seen: set[str] = set()
        self._inputs: dict[str, dict] = {}
        self._keep = max(1, int(keep))
        self.last_run_id: str = ""

    def busy(self) -> bool:

        return self.current is not None

    def owns(self, run_id: str) -> bool:

        return self.current is not None and self.current["run_id"] == run_id

    def begin(self, run: dict) -> dict:





        if self.busy():
            raise RunBusy("a run is already open")
        if not str(run.get("run_id") or ""):
            raise ValueError("run_id is required")
        record = dict(run)
        for key, default in (("cancelled", False), ("heard", False), ("resend_due", False),
                             ("resends", 0), ("resume_resent", False), ("error_message", "")):
            record.setdefault(key, default)
        self.current = record
        self.calls.clear()
        self._cards.clear()
        self._seen.clear()
        self.phase = SENDING
        return record

    def sent(self, run_id: str) -> None:

        if self.owns(run_id) and self.phase == SENDING:
            self.phase = RUNNING

    def remember(self, key: str, record: dict) -> None:




        if not key:
            return
        self._inputs.pop(key, None)
        self._inputs[key] = dict(record)
        while len(self._inputs) > self._keep:
            self._inputs.pop(next(iter(self._inputs)))

    def record(self, key: str) -> dict | None:

        return self._inputs.get(str(key or ""))

    def heard(self, run_id: str) -> bool:





        if not self.owns(run_id):
            return False
        run = self.current
        run["heard"] = True
        if not run.get("resend_due"):
            return False
        run["resend_due"] = False
        return True

    def never_started(self, run_id: str) -> bool:

        return self.owns(run_id) and not self.current["heard"]

    def open_call(self, tool_call_id: str, run_id: str) -> bool:





        if tool_call_id in self._seen:
            return False
        self._seen.add(tool_call_id)
        self.calls[tool_call_id] = run_id
        return True

    def close_call(self, tool_call_id: str) -> str:

        run_id = self.calls.pop(tool_call_id, "")
        self._cards.discard(tool_call_id)
        self._settle()
        return run_id

    def wait_user(self, tool_call_id: str) -> None:

        self._cards.add(tool_call_id)
        if self.phase in (SENDING, RUNNING):
            self.phase = WAITING_USER

    def user_answered(self, tool_call_id: str) -> None:

        self._cards.discard(tool_call_id)
        self._settle()

    def _settle(self) -> None:

        if self.phase == WAITING_USER and not self._cards and self.current is not None:
            self.phase = RUNNING

    def server_owes_frame(self) -> bool:

        if self.current is None:
            return False
        return self.current["run_id"] not in self.calls.values()

    def busy_refused(self, run_id: str, max_resends: int) -> bool:




        run = self.current
        if run is None or (run_id and not self.owns(run_id)) or run.get("cancelled"):
            return False
        if run.get("resends", 0) >= max_resends:
            return False
        run["resends"] = run.get("resends", 0) + 1
        run["resend_due"] = True
        return True

    def take_resend(self) -> dict | None:

        run = self.current
        if run is None or run.get("cancelled") or not run.get("resend_due"):
            return None
        run["resend_due"] = False
        return run

    def resend_later(self) -> None:

        run = self.current
        if run is not None and not run.get("cancelled"):
            run["resend_due"] = True

    def session_started(self, resumed) -> str:





        run = self.current
        if run is None or run.get("cancelled"):
            return NOTHING
        if resumed is not None and not run.get("heard") and not run.get("resume_resent"):





            run["resume_resent"] = True
            run["resend_due"] = True
        if run.get("resend_due"):
            return RESEND
        if resumed is False:
            return AWAIT_OUTCOME
        return AWAIT_RESUME

    def stop(self, run_id: str) -> dict | None:

        run = self.current
        if run is None or (run_id and not self.owns(run_id)):
            return None
        run["cancelled"] = True
        run["resend_due"] = False
        self.phase = STOPPING
        return run

    def cancelled(self) -> bool:

        return self.current is not None and bool(self.current.get("cancelled"))

    def finish(self, run_id: str) -> list[str]:

        if not self.owns(run_id):
            return []
        closed = [cid for cid, rid in self.calls.items() if rid == run_id]
        for cid in closed:
            self.calls.pop(cid, None)
            self._cards.discard(cid)
        self._seen.clear()
        self.last_run_id = run_id
        self.current = None
        self.phase = ENDED
        return closed
