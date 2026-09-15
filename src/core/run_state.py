# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The open run of a chat: one small object that says what each event means."""








from __future__ import annotations

COMPOSING = "composing"
SENDING = "sending"
RUNNING = "running"
WAITING_USER = "waiting_user"
STOPPING = "stopping"
ENDED = "ended"
PHASES = (COMPOSING, SENDING, RUNNING, WAITING_USER, STOPPING, ENDED)


RESEND = "resend"
AWAIT_OUTCOME = "await_outcome"
AWAIT_RESUME = "await_resume"
NOTHING = ""


class RunBusy(RuntimeError):
    """begin() while a run is open."""


class RunMachine:
    """The run state, the open calls of the run and the inputs to resend."""

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
        """True while a run is open."""
        return self.current is not None

    def owns(self, run_id: str) -> bool:
        """True when ``run_id`` names the open run."""
        return self.current is not None and self.current["run_id"] == run_id

    def begin(self, run: dict) -> dict:
        """Open the run: the one moment Send is allowed."""




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
        """The message left the socket: the server owes the first frame."""
        if self.owns(run_id) and self.phase == SENDING:
            self.phase = RUNNING

    def remember(self, key: str, record: dict) -> None:
        """Store what Retry sends back, newest last, at most ``keep`` of them."""



        if not key:
            return
        self._inputs.pop(key, None)
        self._inputs[key] = dict(record)
        while len(self._inputs) > self._keep:
            self._inputs.pop(next(iter(self._inputs)))

    def record(self, key: str) -> dict | None:
        """The stored record itself, not a copy, or None."""
        return self._inputs.get(str(key or ""))

    def heard(self, run_id: str) -> bool:
        """A frame for the run arrived: the server holds the run."""




        if not self.owns(run_id):
            return False
        run = self.current
        run["heard"] = True
        if not run.get("resend_due"):
            return False
        run["resend_due"] = False
        return True

    def never_started(self, run_id: str) -> bool:
        """True when the open run is this one and no frame came back for it."""
        return self.owns(run_id) and not self.current["heard"]

    def open_call(self, tool_call_id: str, run_id: str) -> bool:
        """Note a tool call of the open run; False when it is a replay."""




        if tool_call_id in self._seen:
            return False
        self._seen.add(tool_call_id)
        self.calls[tool_call_id] = run_id
        return True

    def close_call(self, tool_call_id: str) -> str:
        """The call is done: its run id, empty when it was not open."""
        run_id = self.calls.pop(tool_call_id, "")
        self._cards.discard(tool_call_id)
        self._settle()
        return run_id

    def wait_user(self, tool_call_id: str) -> None:
        """This card of the run waits for the user."""
        self._cards.add(tool_call_id)
        if self.phase in (SENDING, RUNNING):
            self.phase = WAITING_USER

    def user_answered(self, tool_call_id: str) -> None:
        """The card no longer waits: the run goes on when it was the last."""
        self._cards.discard(tool_call_id)
        self._settle()

    def _settle(self) -> None:
        """A card closed: with no card left, a run that waited on the user runs again."""
        if self.phase == WAITING_USER and not self._cards and self.current is not None:
            self.phase = RUNNING

    def server_owes_frame(self) -> bool:
        """True when no tool runs here and no card of the run is open."""
        if self.current is None:
            return False
        return self.current["run_id"] not in self.calls.values()

    def busy_refused(self, run_id: str, max_resends: int) -> bool:
        """The service refused the run with BUSY: may it leave again?"""



        run = self.current
        if run is None or (run_id and not self.owns(run_id)) or run.get("cancelled"):
            return False
        if run.get("resends", 0) >= max_resends:
            return False
        run["resends"] = run.get("resends", 0) + 1
        run["resend_due"] = True
        return True

    def take_resend(self) -> dict | None:
        """The run to send again, or None. Clears ``resend_due``."""
        run = self.current
        if run is None or run.get("cancelled") or not run.get("resend_due"):
            return None
        run["resend_due"] = False
        return run

    def resend_later(self) -> None:
        """The resend could not leave (socket down): keep it due."""
        run = self.current
        if run is not None and not run.get("cancelled"):
            run["resend_due"] = True

    def session_started(self, resumed) -> str:
        """A session frame arrived (``resumed`` is raw: True, False or None)."""




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
        """Stop pressed: cancel the open run, or None when it is not ours."""
        run = self.current
        if run is None or (run_id and not self.owns(run_id)):
            return None
        run["cancelled"] = True
        run["resend_due"] = False
        self.phase = STOPPING
        return run

    def cancelled(self) -> bool:
        """True when the open run was cancelled."""
        return self.current is not None and bool(self.current.get("cancelled"))

    def finish(self, run_id: str) -> list[str]:
        """The single exit: close the run, return the tool calls it left open."""
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
