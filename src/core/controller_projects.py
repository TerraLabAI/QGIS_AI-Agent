# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The open QGIS project: its signals, its changes and the history menu."""



from __future__ import annotations

from qgis.PyQt.QtCore import QCoreApplication

from .controller_actions import _project_path
from .logger import log_warning
from .protocol import RunStatus
from .snapshot import changed_layer_items


def tr(text: str) -> str:
    return QCoreApplication.translate("AgentController", text)


class _ControllerProjects:
    def _watch_project(self) -> None:
        """Re-key the history menu when the user opens or saves a project."""
        try:
            from qgis.core import QgsProject
            project = QgsProject.instance()
            refresh = lambda *_: self._panel_call(  # noqa: E731
                "set_threads", self._store.list_threads(), _project_path())



            self._project_connections = [
                (project.readProject, refresh),
                (project.projectSaved, refresh),
                (project.projectSaved, self._on_project_saved),
                (project.cleared, refresh),
                (project.cleared, self._on_project_cleared),
                (project.readProject, self._on_project_read),
            ]
            for signal, slot in self._project_connections:
                signal.connect(slot)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Project watch failed: {exc}")

    def _unwatch_project(self) -> None:
        """Leave QgsProject as we found it: a kept connection fires into a dead panel on the next project open, once per plugin reload of the session."""

        for signal, slot in self._project_connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._project_connections = []

    def _on_project_read(self, *_args) -> None:
        """A project file was read: its checkpoints belong to this project again."""





        self._executor.history.project_opened()
        self._on_project_opened(*_args)

    def _on_project_cleared(self, *_args) -> None:
        """The open project was closed: File > New, or the first step of File > Open."""







        if self._restoring:
            return
        run = self._run
        if run is not None and self._executor.main_call_in_flight(run["run_id"]):
            return
        self._executor.history.project_replaced()
        if run is None:
            return
        run_id = run["run_id"]
        log_warning(f"Run {run_id[:8]}: the project was closed mid-run, ending the run")
        try:
            self._runs.stop(run_id)
            self._session.send_cancel(run_id)
            self._executor.project_replaced(run_id)
        except Exception as exc:  # noqa: BLE001 - the run still ends: every path that ends one reaches _finish_run
            log_warning(f"Run {run_id[:8]}: a step of ending it on project close failed: {exc}")
        self._finish_run(run_id, RunStatus.CANCELLED,
                         tr("Stopped: the project this run worked on was closed."), {}, None)
        self.notice.emit("warning", tr("The AI Agent run stopped because its project was closed."))

    def _on_project_saved(self, *_args) -> None:
        """File > Save As: this chat's checkpoints go with the file just saved."""
        self._executor.history.project_saved()

    def _on_project_changed(self, changed: int) -> None:
        """A layer changed: the chips refresh once the burst has settled."""







        self._pending_changed = int(changed)
        self._diff_timer.start()

    def _refresh_run_changes(self) -> None:
        run = self._run
        changed = self._pending_changed
        snapshot = self._executor.snapshot_for(run["run_id"]) if run else None
        touched: list = []
        if snapshot is not None and snapshot.captured:



            try:
                diff = snapshot.diff()
                touched = changed_layer_items(diff)
                changed = int(diff.get("changed_layers", changed))
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Snapshot diff failed: {exc}")
        self._panel_call("set_run_changes", changed, bool(snapshot and snapshot.captured), touched)
