# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run a tool handler off the Qt main thread and deliver its result on it."""

















from __future__ import annotations

import contextlib
import gc
import queue
import threading
import time
import traceback
from typing import Any, Callable

from qgis.PyQt.QtCore import QCoreApplication, QObject, QThread, pyqtSignal, pyqtSlot

from . import net
from .logger import log_warning



_ALIVE: set = set()













_TASKS_LOCK = threading.Lock()
_TASKS: dict[int, dict] = {}
_CURRENT = threading.local()


def _task_started(key: int, description: str) -> dict:
    entry = {"description": str(description or "")[:120], "started": time.monotonic(), "beat": time.monotonic()}
    with _TASKS_LOCK:
        _TASKS[key] = entry
    return entry


def _task_ended(key: int) -> None:
    with _TASKS_LOCK:
        _TASKS.pop(key, None)


def heartbeat() -> None:
    """Mark the current worker as alive."""




    entry = getattr(_CURRENT, "entry", None)
    if entry is not None:
        entry["beat"] = time.monotonic()


def running_tasks() -> list:
    """The background work in flight: description, seconds running, seconds quiet."""





    now = time.monotonic()
    with _TASKS_LOCK:
        entries = list(_TASKS.values())
    out = [{"description": e["description"], "running_s": round(now - e["started"], 1),
            "quiet_s": round(now - e["beat"], 1)} for e in entries]
    out.sort(key=lambda item: item["running_s"])
    return out





class _MainThreadInvoker(QObject):
    """Runs a callable on the Qt main thread when emitted from a worker thread."""













    _run = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._run.connect(self._on_run)

    @pyqtSlot(object)
    def _on_run(self, fn):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - never unwind a Qt slot into C++
            log_warning(f"Main-thread callback failed: {exc}")

    def invoke(self, fn):
        self._run.emit(fn)


_INVOKER = None
_INVOKER_LOCK = threading.Lock()


def main_thread_invoker():
    """The shared invoker, created on the main thread and kept there."""
    global _INVOKER
    with _INVOKER_LOCK:
        if _INVOKER is None:
            app = _app()
            invoker = _MainThreadInvoker()
            if app is not None and invoker.thread() is not app.thread():


                invoker.moveToThread(app.thread())
            _INVOKER = invoker
        return _INVOKER


def _app():
    """The running application, or None."""

    try:
        return QCoreApplication.instance()
    except Exception:  # noqa: BLE001
        return None


def on_main_thread() -> bool:
    app = _app()
    if app is None:
        return True
    try:
        return QThread.currentThread() is app.thread()
    except Exception:  # noqa: BLE001
        return True






SLOW_MAIN_THREAD_S = 0.1


def _describe(fn) -> str:
    """A name for the log line: the function's own, then its module."""
    name = getattr(fn, "__qualname__", None) or getattr(fn, "__name__", None) or repr(fn)
    module = getattr(fn, "__module__", "") or ""
    return f"{module.rsplit('.', 1)[-1]}.{name}" if module else str(name)


def run_on_main_thread(fn, *args, timeout=10):
    """Execute *fn* on the Qt main thread and return what it returned."""












    if on_main_thread():
        return fn(*args)

    result_queue: queue.Queue = queue.Queue()
    expired = threading.Event()
    cancel_check = net.current_cancel_check()

    def _trampoline():


        started = time.perf_counter()
        try:
            if expired.is_set() or (cancel_check is not None and cancel_check()):
                raise InterruptedError("Main-thread work cancelled before it started")
            result_queue.put(("ok", fn(*args)))
        except Exception as exc:  # noqa: BLE001 - handed back to the caller
            result_queue.put(("err", exc))
        finally:
            spent = time.perf_counter() - started
            if spent > SLOW_MAIN_THREAD_S:
                log_warning(f"Main thread held {spent * 1000:.0f} ms by {_describe(fn)}: "
                            f"QGIS was blocked for that long. Move what does not need "
                            f"PyQGIS back to the worker.")





    heartbeat()
    main_thread_invoker().invoke(_trampoline)
    try:
        tag, payload = result_queue.get(timeout=timeout)
        heartbeat()
    except queue.Empty as exc:
        expired.set()
        raise TimeoutError(
            f"Main-thread work did not finish within {timeout}s. QGIS may be busy "
            f"(rendering tiles, loading a large project). Queued work is cancelled, "
            f"but work already started may still finish. Inspect the project before retrying."
        ) from exc
    if tag == "err":
        raise payload
    return payload


def _task_flags(task_cls, hidden: bool):
    """CanCancel and CancelWithoutPrompt, plus Hidden/Silent when this QGIS knows them."""





    holder = getattr(task_cls, "Flag", task_cls)
    flags = getattr(holder, "CanCancel", None)
    if flags is None:
        return None



    quiet = getattr(holder, "CancelWithoutPrompt", None)
    if quiet is not None:
        flags = flags | quiet
    if hidden:
        for name in ("Hidden", "Silent"):
            extra = getattr(holder, name, None)
            if extra is not None:
                flags = flags | extra
    return flags


def _release_later(task) -> None:
    """Drop the last Python reference on the next event-loop turn."""





    try:
        from qgis.PyQt.QtCore import QTimer

        QTimer.singleShot(0, lambda: _ALIVE.discard(task))
    except Exception:  # noqa: BLE001 - no timer: drop it now rather than leak
        _ALIVE.discard(task)


class on_any_failure:
    """``with on_any_failure(handle):`` is ``try: ..."""







    def __init__(self, handle: Callable[[BaseException], None]):
        self._handle = handle

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            return False
        self._handle(exc)
        return True



_TASK = {"class": None}


def _task_class():
    """Build the QgsTask subclass once, lazily: importing qgis.core at module import time would tie the executor's import to a real QGIS."""

    if _TASK["class"] is not None:
        return _TASK["class"]
    from qgis.core import QgsTask

    class _CallTask(QgsTask):
        """Runs *work* in a worker thread, calls *on_done* on the main thread."""





        def __init__(self, description: str, work: Callable[[], Any],
                     on_done: Callable[[Any, str], None], flags):
            super().__init__(description, flags)



            self._description = description
            self._work = work
            self._on_done = on_done
            self._result: Any = None
            self._error: str = ""

        def run(self) -> bool:



            net.set_cancel_check(self.isCanceled)




            _CURRENT.entry = _task_started(id(self), self._description)
            try:

                with on_any_failure(self._fail):
                    if self.isCanceled():
                        raise InterruptedError("Background work cancelled before it started")
                    self._result = self._work()
            finally:
                net.set_cancel_check(None)
                _task_ended(id(self))
                _CURRENT.entry = None
            return True

        def _fail(self, exc: BaseException) -> None:
            self._result = None
            self._error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-2000:]}"

        def finished(self, ok: bool) -> None:


            try:
                self._on_done(self._result, self._error)
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Background tool callback failed: {exc}")
            finally:
                _release_later(self)

    _TASK["class"] = _CallTask
    return _CallTask



BREATHE_EVERY = 500

_GC_LOCK = threading.Lock()
_GC_DEPTH = 0
_GC_WAS_ENABLED = True


@contextlib.contextmanager
def gc_paused():
    """No automatic garbage collection while a worker builds a large structure."""












    global _GC_DEPTH, _GC_WAS_ENABLED
    with _GC_LOCK:
        if _GC_DEPTH == 0:
            _GC_WAS_ENABLED = gc.isenabled()
            gc.disable()
        _GC_DEPTH += 1
    try:
        yield
    finally:
        with _GC_LOCK:
            _GC_DEPTH -= 1
            if _GC_DEPTH == 0 and _GC_WAS_ENABLED:
                gc.enable()


SLICE_GAP_MS = 1


def run_sliced(steps, budget_ms: float, still_wanted: Callable[[], bool] | None = None,
               around: Callable[[], Any] | None = None, on_done: Callable[[], None] | None = None) -> None:
    """Run ``steps`` (callables, in order) on this thread a ``budget_ms`` slice at a time, with the event loop between slices."""













    steps = list(steps)
    budget = max(0.001, float(budget_ms) / 1000.0)
    if _app() is None:
        for step in steps:
            step()
        if on_done is not None:
            on_done()
        return
    from qgis.PyQt.QtCore import QTimer

    def run_from(start: int) -> None:
        if still_wanted is not None and not still_wanted():
            return
        index = start
        total = len(steps)






        failed = ""
        with (around() if around is not None else contextlib.nullcontext()):
            deadline = time.perf_counter() + budget
            while index < total:
                try:
                    steps[index]()
                except Exception:  # noqa: BLE001 - the Qt boundary is right here
                    failed = traceback.format_exc()
                    index = total
                    break
                index += 1
                if time.perf_counter() >= deadline:
                    break
        if failed:
            log_warning(f"Sliced build stopped at step {start}: {failed}")
        if index < total:




            QTimer.singleShot(SLICE_GAP_MS, lambda: run_from(index))
        elif on_done is not None:
            on_done()

    run_from(0)


def breathe(index: int, every: int = BREATHE_EVERY) -> None:
    """Worker loops call this once per item so the main thread gets its turn."""









    if index % every == 0:
        time.sleep(0)



        heartbeat()


def run_off_thread(description: str, work: Callable[[], Any],
                   on_done: Callable[[Any, str], None], hidden: bool = True):
    """Start *work* in a QgsTask."""




    task = None
    try:
        from qgis.core import QgsApplication, QgsTask

        manager = QgsApplication.taskManager()
        flags = _task_flags(QgsTask, hidden)
        if manager is None or flags is None:
            return None
        main_thread_invoker()
        task = _task_class()(description, work, on_done, flags)
        _ALIVE.add(task)
        task_id = manager.addTask(task)
        if task_id == 0:
            _ALIVE.discard(task)
            return None
    except Exception as exc:  # noqa: BLE001 - no task manager: the caller runs inline
        if task is not None:
            _ALIVE.discard(task)
        log_warning(f"Background tool run unavailable, running on the main thread: {exc}")
        return None
    return task


def cancel_all() -> None:
    """Ask every running tool task to stop."""

    for task in list(_ALIVE):
        cancel(task)


def cancel(task) -> None:
    """Ask a running task to stop."""

    if task is None:
        return
    try:
        task.cancel()
    except Exception:  # nosec B110 - cancelling a finished task is not an error
        pass
