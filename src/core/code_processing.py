# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


































from __future__ import annotations

import uuid

from qgis.core import (
    QgsApplication,
    QgsMapLayer,
    QgsProcessingAlgRunnerTask,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProcessingOutputMapLayer,
    QgsProcessingOutputMultipleLayers,
    QgsProcessingOutputRasterLayer,
    QgsProcessingOutputVectorLayer,
    QgsTask,
)
from qgis.PyQt.QtCore import QCoreApplication, QEventLoop, QObject, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

from . import background, code_guard
from .qt_compat import enum_member


QUIET_MS = 1000


WINDOW_NAME = "terralab_code_processing_wait"

_WAITS: list = []
_LAYER_OUTPUTS = (QgsProcessingOutputVectorLayer, QgsProcessingOutputRasterLayer, QgsProcessingOutputMapLayer)


class Stopped(code_guard.CodeTimeout):
    pass


def tr(text: str) -> str:
    return QCoreApplication.translate("CodeProcessing", text)


class _Feedback(QgsProcessingFeedback):


    def __init__(self):
        super().__init__()
        self.errors: list[str] = []

    def reportError(self, error, fatalError=False):  # noqa: N802
        self.errors.append(str(error))
        super().reportError(error, fatalError)


class _Wait(QObject):


    clock_out = pyqtSignal()

    def __init__(self, task, run_id: str, stop_run):
        super().__init__()
        self.task = task
        self.run_id = run_id
        self.loop: QEventLoop | None = None
        self.done = False
        self.ok = False
        self.results: dict = {}
        self.ended_by = ""
        self.window = None
        self.progress = None
        self._stop_run = stop_run
        self.clock_out.connect(self._clock)

    def ran_out(self) -> None:

        self.clock_out.emit()

    def _clock(self) -> None:
        self.end("clock")

    def finished(self, ok, results) -> None:
        if self.done:
            return
        self.done = True
        self.ok = bool(ok)
        self.results = dict(results or {})
        if self.loop is not None:
            self.loop.quit()

    def end(self, why: str) -> None:

        if self.done or self.ended_by:
            return
        self.ended_by = why
        if self.window is not None:
            self.window.stopping()
        try:
            self.task.cancel()
        except RuntimeError:
            pass

    def run_stop(self) -> None:

        if self.ended_by == "user" and callable(self._stop_run):
            QTimer.singleShot(0, self._stop_run)


def stop(run_id: str = "") -> None:

    for wait in list(_WAITS):
        if not run_id or wait.run_id == run_id:
            wait.end("stop")


def threaded(real_run):


    def run(algorithm, *args, **kwargs):
        if code_guard.clock_ran_out():
            raise code_guard.CodeTimeout()
        parameters = args[0] if args else kwargs.get("parameters")
        context = _call_context()
        alg = None

        if (context is not None and isinstance(algorithm, str) and isinstance(parameters, dict)
                and len(args) <= 1 and set(kwargs) <= {"parameters"}):
            alg = QgsApplication.processingRegistry().createAlgorithmById(algorithm)
        if alg is None or not _threadable(alg):
            return real_run(algorithm, *args, **kwargs)
        return _run_in_task(alg, parameters, context)

    return run


def _call_context():

    try:
        from ..tools import code_runtime
    except ImportError:
        return None
    context = code_runtime.current()
    if not context.run_id or not background.on_main_thread() or background.marshalled():
        return None
    return context


def _threadable(alg) -> bool:
    from ..tools.processing_decisions import _threadable as threadable

    return threadable(alg)


def _run_in_task(alg, parameters: dict, call_context) -> dict:
    from processing.core.Processing import Processing
    from processing.tools import dataobjects

    feedback = _Feedback()
    context = dataobjects.createContext(feedback)
    ok, message = alg.checkParameterValues(parameters, context)
    if not ok:
        raise QgsProcessingException(Processing.tr("Unable to execute algorithm\n{0}").format(message))
    task = QgsProcessingAlgRunnerTask(alg, parameters, context, feedback, background._task_flags(QgsTask, True))
    if task.isCanceled():

        raise QgsProcessingException(feedback.errors[-1] if feedback.errors
                                     else Processing.tr("There were errors executing the algorithm."))
    wait = _Wait(task, call_context.run_id, call_context.stop)


    task.executed.connect(lambda done, results, w=wait, keep=(context, feedback): w.finished(done, results))
    task.taskTerminated.connect(lambda w=wait: w.finished(False, {}))
    with code_guard.CLOCK_LOCK:
        if code_guard.clock_ran_out():
            raise code_guard.CodeTimeout()
        code_guard.TASK_WAITS.append(wait.ran_out)
    _WAITS.append(wait)


    from ..tools import processing_run

    readers_key = "code-" + uuid.uuid4().hex[:12]
    processing_run._PROCESSING_TASKS[readers_key] = entry = {
        "status": "running", "algorithm": alg.id(), "task": task, "parameters": dict(parameters)}
    processing_run.watch_removals()
    try:
        QgsApplication.taskManager().addTask(task)
        if not wait.done:
            wait.loop = quiet = QEventLoop()
            QTimer.singleShot(QUIET_MS, quiet.quit)
            QEventLoop.exec(quiet, enum_member(QEventLoop, "ProcessEventsFlag", "ExcludeUserInputEvents"))
        if not wait.done:
            _window(wait, alg.displayName(), feedback)
            wait.loop = QEventLoop()
            QEventLoop.exec(wait.loop)
    finally:

        wait.loop = None
        _close(wait, feedback)
        _WAITS.remove(wait)
        processing_run._PROCESSING_TASKS.pop(readers_key, None)
        processing_run._let_go(entry)
        with code_guard.CLOCK_LOCK:
            code_guard.TASK_WAITS.remove(wait.ran_out)
    if wait.ended_by in ("user", "stop"):
        wait.run_stop()
        raise Stopped()
    if wait.ended_by or code_guard.clock_ran_out():
        raise code_guard.CodeTimeout()
    if not wait.ok:
        raise QgsProcessingException(feedback.errors[-1] if feedback.errors
                                     else Processing.tr("There were errors executing the algorithm."))
    return _layers_out(alg, wait.results, context)


class _Window(QDialog):








    def __init__(self, wait: _Wait, name: str, parent):
        super().__init__(parent)
        self._wait = wait
        self.setObjectName(WINDOW_NAME)
        self.setWindowTitle(tr("AI Agent"))
        self.setWindowModality(enum_member(Qt, "WindowModality", "ApplicationModal"))
        self._label = QLabel(tr("Running {name}...").format(name=name), self)
        self._label.setWordWrap(True)
        self._bar = QProgressBar(self)
        self._bar.setRange(0, 100)
        self._button = QPushButton(tr("Stop"), self)
        self._button.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._button)
        layout = QVBoxLayout(self)
        layout.addWidget(self._label)
        layout.addWidget(self._bar)
        layout.addLayout(row)
        self.setMinimumWidth(360)

    def labelText(self) -> str:  # noqa: N802
        return self._label.text()

    def setValue(self, value) -> None:  # noqa: N802
        self._bar.setValue(max(0, min(100, int(value))))

    def stopping(self) -> None:
        self._label.setText(tr("Stopping..."))
        self._button.setEnabled(False)

    def reject(self) -> None:
        self._wait.end("user")

    def accept(self) -> None:
        self._wait.end("user")

    def done(self, _result) -> None:
        self._wait.end("user")

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()
        self._wait.end("user")


def _window(wait: _Wait, name: str, feedback) -> None:

    try:
        from qgis.utils import iface

        parent = iface.mainWindow() if iface is not None else None
    except Exception:  # noqa: BLE001
        return
    if parent is None:
        return
    window = _Window(wait, name, parent)
    window.setValue(feedback.progress())
    wait.progress = window.setValue
    feedback.progressChanged.connect(wait.progress)
    wait.window = window
    window.show()


def _close(wait: _Wait, feedback) -> None:
    window, wait.window = wait.window, None
    if wait.progress is not None:
        try:
            feedback.progressChanged.disconnect(wait.progress)
        except (TypeError, RuntimeError):
            pass
        wait.progress = None
    if window is not None:
        window.hide()
        window.deleteLater()


def _layers_out(alg, results: dict, context) -> dict:

    for out in alg.outputDefinitions():
        name = out.name()
        if name not in results:
            continue
        if isinstance(out, _LAYER_OUTPUTS):
            if not isinstance(results[name], QgsMapLayer):
                layer = context.takeResultLayer(results[name])
                if layer:
                    results[name] = layer
        elif isinstance(out, QgsProcessingOutputMultipleLayers) and results[name]:
            taken = []
            for item in results[name]:
                layer = None if isinstance(item, QgsMapLayer) else context.takeResultLayer(item)
                taken.append(layer or item)
            results[name] = taken
    return results
