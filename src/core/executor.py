# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tool executor: the plugin decides, the server only annotates."""













from __future__ import annotations

from collections import OrderedDict

from qgis.PyQt.QtCore import QObject, pyqtSignal

from . import background, follow, layer_order, scratch, stalls
from .checkpoints import CheckpointHistory
from .executor_calls import _ExecutorCalls
from .executor_deliver import _ExecutorDeliver
from .executor_execute import _ExecutorExecute
from .executor_guards import _ExecutorGuards
from .executor_idempotency import IdempotencyTable
from .executor_runs import _ExecutorRuns
from .snapshot import RunSnapshot
from .watchdog import MainThreadWatchdog


__all__ = ["ToolExecutor", "background"]


class ToolExecutor(_ExecutorRuns, _ExecutorCalls, _ExecutorExecute, _ExecutorDeliver, _ExecutorGuards, QObject):
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
        self._journal: dict[str, list[dict]] = {}
        self._snapshots: dict[str, RunSnapshot] = {}
        self._written: dict[str, list[str]] = {}
        self._call_warnings: dict[str, list[dict]] = {}

        self._ended_run: tuple = ("", (), ())
        self._budgets: dict[str, object] = {}
        self._background: dict[str, tuple] = {}
        self._task_ids: dict[str, set] = {}
        self._threads: dict[str, str] = {}
        self._run_index: dict[str, int] = {}
        self._prompts: dict[str, str] = {}




        self._inflight: dict[str, tuple[str, str, float, bool]] = {}


        self._answered: OrderedDict[str, None] = OrderedDict()

        self._blocked: dict[str, str] = {}

        self._replaced: set[str] = set()


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


        self.stacker.in_call = lambda: bool(self._inflight)
        self._refresh_proxy()
