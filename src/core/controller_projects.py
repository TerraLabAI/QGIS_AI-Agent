# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




from __future__ import annotations

from qgis.PyQt.QtCore import QCoreApplication, QTimer

from .controller_actions import _project_path
from .logger import log, log_warning
from .protocol import CANCEL_PROJECT_CLOSED, RunStatus
from .snapshot import changed_layer_items


def tr(text: str) -> str:
    return QCoreApplication.translate("AgentController", text)


class _ControllerProjects:
    def _watch_project(self) -> None:

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
                (project.layersAdded, self._schedule_scratch_marks),
                (project.layerTreeRoot().addedChildren, self._schedule_scratch_marks),
            ]
            for signal, slot in self._project_connections:
                signal.connect(slot)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Project watch failed: {exc}")

    def _unwatch_project(self) -> None:


        for signal, slot in self._project_connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._project_connections = []
        self._clear_scratch_marks()

    def _on_project_read(self, *_args) -> None:






        self._executor.history.project_opened()
        self._on_project_opened(*_args)

    def _on_project_cleared(self, *_args) -> None:








        if self._restoring:
            return
        run = self._run
        if run is not None and self._executor.main_call_in_flight(run["run_id"]):
            return

        self._executor.drop_pending_after()
        self._executor.history.project_replaced()
        if run is None:
            return
        run_id = run["run_id"]
        log_warning(f"Run {run_id[:8]}: the project was closed mid-run, ending the run")
        try:
            self._runs.stop(run_id)
            self._session.send_cancel(run_id, CANCEL_PROJECT_CLOSED)
            self._executor.project_replaced(run_id)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Run {run_id[:8]}: a step of ending it on project close failed: {exc}")
        self._finish_run(run_id, RunStatus.CANCELLED,
                         tr("Stopped: the project this run worked on was closed."), {}, None)
        self.notice.emit("warning", tr("The AI Agent run stopped because its project was closed."))

    def _on_project_saved(self, *_args) -> None:


        self._executor.history.project_saved()
        if getattr(self, "_storing_layers", False) or self._restoring:
            return
        from .checkpoints import _in_snapshots, _open_project_file

        path = _open_project_file()
        if not path or _in_snapshots(path):
            return
        if self._run is not None:

            self._executor.durable_after_run = True
            return

        QTimer.singleShot(0, self._store_agent_layers)

    def _store_agent_layers(self, save: bool = True) -> None:







        from qgis.core import QgsProject

        from ..tools.layer_tools import _agent_layers_made_durable

        self._storing_layers = True
        try:
            stored = _agent_layers_made_durable(QgsProject.instance(), save=save)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Agent layers not stored beside the project: {exc}")
            stored = {}
        finally:
            self._storing_layers = False
        if stored:
            log(f"Stored {len(stored.get('layers') or [])} agent layers in {stored.get('gpkg_path')}")
        self._schedule_scratch_marks()



    def _schedule_scratch_marks(self, *_args) -> None:

        timer = getattr(self, "_marks_timer", None)
        if timer is None:
            timer = self._marks_timer = QTimer()
            timer.setSingleShot(True)
            timer.setInterval(200)
            timer.timeout.connect(self._refresh_scratch_marks)
        timer.start()

    def _clear_scratch_marks(self) -> None:
        view = _layer_tree_view()
        for node, indicator in getattr(self, "_scratch_marks", []):
            try:
                if view is not None:
                    view.removeIndicator(node, indicator)
            except (RuntimeError, TypeError):
                pass
        self._scratch_marks = []

    def _refresh_scratch_marks(self) -> None:



        self._clear_scratch_marks()
        view = _layer_tree_view()
        if view is None:
            return
        try:
            from qgis.core import QgsApplication, QgsProject
            from qgis.gui import QgsLayerTreeViewIndicator

            from ..tools.layer_tools import scratch_layers

            project = QgsProject.instance()
            _memory, temporary = scratch_layers(project)
            root = project.layerTreeRoot()
            for layer in temporary:
                node = root.findLayer(layer.id())
                if node is None:
                    continue
                indicator = QgsLayerTreeViewIndicator(view)
                indicator.setIcon(QgsApplication.getThemeIcon("/mIndicatorMemory.svg"))
                indicator.setToolTip(tr("Temporary file in the AI Agent's scratch folder. Saving the project "
                                        "stores it beside the project."))
                view.addIndicator(node, indicator)
                self._scratch_marks.append((node, indicator))
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Scratch layer marks not drawn: {exc}")

    def _on_project_changed(self, changed: int) -> None:








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


def _layer_tree_view():

    try:
        from qgis.utils import iface
        return iface.layerTreeView() if iface is not None else None
    except Exception:  # noqa: BLE001  # nosec B110
        return None
