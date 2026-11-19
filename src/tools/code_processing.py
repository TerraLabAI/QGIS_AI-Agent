# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


































from __future__ import annotations

import functools
import uuid

from qgis.core import (
    QgsApplication,
    QgsFeedback,
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

from ..core import background
from ..core.layer_release import PROCESSING_TASKS, let_go, watch_removals
from ..core.qt_compat import disconnect_quietly, enum_member, task_flags
from . import code_clock, code_runtime


QUIET_MS = 1000


WINDOW_NAME = "terralab_code_processing_wait"

_RENDER_ALONE_S = 120.0

_WAITS: list = []
_LAYER_OUTPUTS = (QgsProcessingOutputVectorLayer, QgsProcessingOutputRasterLayer, QgsProcessingOutputMapLayer)


class Stopped(code_clock.CodeTimeout):
    pass


def tr(text: str) -> str:
    return QCoreApplication.translate("CodeProcessing", text)


class _Feedback(QgsProcessingFeedback):


    def __init__(self):
        super().__init__()
        background.prime(self)
        self.errors: list[str] = []

    def reportError(self, error, fatalError=False):  # noqa: N802
        self.errors.append(str(error))
        super().reportError(error, fatalError)


class Wait(QObject):


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
        if code_clock.clock_ran_out():
            raise code_clock.CodeTimeout()
        parameters = args[0] if args else kwargs.get("parameters")
        context = call_context()
        alg = None

        if (context is not None and isinstance(algorithm, str) and isinstance(parameters, dict)
                and len(args) <= 1 and set(kwargs) <= {"parameters"}):
            alg = QgsApplication.processingRegistry().createAlgorithmById(algorithm)
        known = alg or (QgsApplication.processingRegistry().algorithmById(algorithm)
                        if isinstance(algorithm, str) and isinstance(parameters, dict) else None)
        if known is not None:
            from .processing_guards import crash_refusal

            refusal = crash_refusal(known, parameters)
            if refusal:
                raise QgsProcessingException(refusal)
        if alg is None or not _threadable(alg):
            return real_run(algorithm, *args, **kwargs)
        return _run_in_task(alg, parameters, context)

    return run


class _RenderJob:


    def __init__(self, job):
        self.job = job

    def cancel(self) -> None:
        self.job.cancelWithoutBlocking()


def render_waited(job):








    from .code_tripwire import AS_WRITTEN
    from .render_wait import finish_on_main

    if not background.on_main_thread():
        return AS_WRITTEN
    if not job.isActive():
        return None
    context = call_context()
    if context is None:
        finish_on_main(job, _RENDER_ALONE_S)
        return None
    wait = Wait(_RenderJob(job), context.run_id, context.stop)
    done = functools.partial(wait.finished, True, {})
    job.finished.connect(done)
    try:
        wait_under_window(wait, tr("the map render"), QgsFeedback(), lambda: None)
    finally:
        disconnect_quietly(job, "finished", done)
    return None


def call_context():

    context = code_runtime.current()
    if not context.run_id or not background.on_main_thread() or background.marshalled():
        return None
    return context


def _threadable(alg) -> bool:
    from .processing_decisions import _threadable as threadable

    return threadable(alg)


def _run_in_task(alg, parameters: dict, call_context) -> dict:
    from processing.core.Processing import Processing
    from processing.tools import dataobjects

    feedback = _Feedback()
    context = dataobjects.createContext(feedback)
    ok, message = alg.checkParameterValues(parameters, context)
    if not ok:
        raise QgsProcessingException(Processing.tr("Unable to execute algorithm\n{0}").format(message))
    task = QgsProcessingAlgRunnerTask(alg, parameters, context, feedback, task_flags(QgsTask, True))
    if task.isCanceled():

        raise QgsProcessingException(feedback.errors[-1] if feedback.errors
                                     else Processing.tr("There were errors executing the algorithm."))
    wait = Wait(task, call_context.run_id, call_context.stop)


    task.executed.connect(lambda done, results, w=wait, keep=(context, feedback): w.finished(done, results))
    task.taskTerminated.connect(lambda w=wait: w.finished(False, {}))


    readers_key = "code-" + uuid.uuid4().hex[:12]
    entry = {"status": "running", "algorithm": alg.id(), "task": task, "parameters": dict(parameters)}

    def start() -> None:
        PROCESSING_TASKS[readers_key] = entry
        watch_removals()
        QgsApplication.taskManager().addTask(task)

    def settle() -> None:
        PROCESSING_TASKS.pop(readers_key, None)
        let_go(entry)

    wait_under_window(wait, alg.displayName(), feedback, start, settle)
    if not wait.ok:
        raise QgsProcessingException(feedback.errors[-1] if feedback.errors
                                     else Processing.tr("There were errors executing the algorithm."))
    return _layers_out(alg, wait.results, context)


def wait_under_window(wait: Wait, name: str, feedback, start, settle=None) -> None:







    with code_clock.CLOCK_LOCK:
        if code_clock.clock_ran_out():
            raise code_clock.CodeTimeout()
        code_clock.TASK_WAITS.append(wait.ran_out)
    _WAITS.append(wait)
    try:
        start()
        if not wait.done:
            wait.loop = quiet = QEventLoop()
            QTimer.singleShot(QUIET_MS, quiet.quit)
            QEventLoop.exec(quiet, enum_member(QEventLoop, "ProcessEventsFlag", "ExcludeUserInputEvents"))
        if not wait.done:
            _window(wait, name, feedback)
            wait.loop = QEventLoop()
            QEventLoop.exec(wait.loop)
    finally:

        wait.loop = None
        _close(wait, feedback)
        _WAITS.remove(wait)
        if settle is not None:
            settle()
        with code_clock.CLOCK_LOCK:
            code_clock.TASK_WAITS.remove(wait.ran_out)
    if wait.ended_by in ("user", "stop"):
        wait.run_stop()
        raise Stopped()
    if wait.ended_by or code_clock.clock_ran_out():
        raise code_clock.CodeTimeout()


class _Window(QDialog):








    def __init__(self, wait: Wait, name: str, parent):
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

    def busy(self) -> None:

        self._bar.setRange(0, 0)

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


def _window(wait: Wait, name: str, feedback) -> None:

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


def _close(wait: Wait, feedback) -> None:
    window, wait.window = wait.window, None
    if wait.progress is not None:
        disconnect_quietly(feedback, "progressChanged", wait.progress)
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
