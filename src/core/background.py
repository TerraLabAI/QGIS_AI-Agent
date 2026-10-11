# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


















from __future__ import annotations

import collections
import concurrent.futures
import contextlib
import gc
import queue
import sys
import threading
import time
import traceback
import types
from typing import Any, Callable

from qgis.PyQt.QtCore import QCoreApplication, QObject, pyqtSignal, pyqtSlot

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





    entry = getattr(_CURRENT, "entry", None)
    if entry is not None:
        entry["beat"] = time.monotonic()


def running_tasks() -> list:






    now = time.monotonic()
    with _TASKS_LOCK:
        entries = list(_TASKS.values())
    out = [{"description": e["description"], "running_s": round(now - e["started"], 1),
            "quiet_s": round(now - e["beat"], 1)} for e in entries]
    out.sort(key=lambda item: item["running_s"])
    return out





class _MainThreadInvoker(QObject):














    _run = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._run.connect(self._on_run)

    @pyqtSlot(object)
    def _on_run(self, fn):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Main-thread callback failed: {exc}")

    def invoke(self, fn):
        self._run.emit(fn)


_INVOKER = None
_INVOKER_LOCK = threading.Lock()


def main_thread_invoker():

    global _INVOKER
    with _INVOKER_LOCK:
        if _INVOKER is None:
            invoker = _MainThreadInvoker()
            if not on_main_thread():


                target = main_qthread()
                if target is not None:
                    invoker.moveToThread(target)
            _INVOKER = invoker
        return _INVOKER















_MAIN_THREAD_ID = threading.main_thread().ident
try:
    _APP = QCoreApplication.instance()
    _MAIN_QTHREAD = _APP.thread() if _APP is not None else None
except Exception:  # noqa: BLE001
    _APP = _MAIN_QTHREAD = None
















def keep_worker_threads() -> None:
    try:
        from qgis.core import QgsApplication
        from qgis.PyQt.QtCore import QThreadPool

        manager = QgsApplication.taskManager()
        pools = [QThreadPool.globalInstance()]
        if manager is not None and hasattr(manager, "threadPool"):
            pools.append(manager.threadPool())
        for pool in pools:
            if pool is not None and pool.expiryTimeout() >= 0:
                pool.setExpiryTimeout(-1)
    except Exception:  # noqa: BLE001
        return


keep_worker_threads()











_KEPT_MODULE = "_terralab_ai_agent_kept_threads"


def _kept_state():
    state = sys.modules.get(_KEPT_MODULE)
    if state is None:
        state = types.ModuleType(_KEPT_MODULE)
        state.jobs = queue.SimpleQueue()
        state.lock = threading.Lock()
        state.idle = 0
        state.started = 0
        sys.modules[_KEPT_MODULE] = state
    return state


_KEPT = _kept_state()


def _kept_loop(state) -> None:
    while True:
        job = state.jobs.get()
        try:
            job()
        except BaseException:  # noqa: BLE001
            pass
        job = None
        with state.lock:
            state.idle += 1


def start_kept_thread(target: Callable[..., Any], *args, name: str = "ai-agent-worker") -> None:


    def job():
        me = threading.current_thread()
        me.name = name
        try:
            target(*args)
        except Exception:  # noqa: BLE001
            log_warning(f"{name} failed:\n{traceback.format_exc()}")
        finally:
            me.name = "ai-agent-kept"

    state = _KEPT
    with state.lock:
        spawn = state.idle == 0
        if spawn:
            state.started += 1
        else:
            state.idle -= 1
    if spawn:
        threading.Thread(target=_kept_loop, args=(state,), name="ai-agent-kept", daemon=True).start()
    state.jobs.put(job)


class KeptThreadPool(concurrent.futures.Executor):



    def __init__(self, max_workers: int, name: str = "ai-agent-pool"):
        self._max = max(1, int(max_workers))
        self._name = name
        self._lock = threading.Lock()
        self._waiting: collections.deque = collections.deque()
        self._futures: list = []
        self._running = 0
        self._closed = False

    def submit(self, fn, *args, **kwargs):
        future = concurrent.futures.Future()
        item = (future, fn, args, kwargs)
        with self._lock:
            if self._closed:
                raise RuntimeError("cannot schedule new futures after shutdown")
            self._futures.append(future)
            start = self._running < self._max
            if start:
                self._running += 1
            else:
                self._waiting.append(item)
        if start:
            start_kept_thread(self._drain, item, name=self._name)
        return future

    def _drain(self, item) -> None:
        while item is not None:
            future, fn, args, kwargs = item
            item = None
            if future.set_running_or_notify_cancel():
                try:
                    future.set_result(fn(*args, **kwargs))
                except BaseException as exc:  # noqa: BLE001
                    future.set_exception(exc)
            future = fn = args = kwargs = None
            with self._lock:
                if self._waiting:
                    item = self._waiting.popleft()
                else:
                    self._running -= 1

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        with self._lock:
            self._closed = True
            if cancel_futures:
                while self._waiting:
                    self._waiting.popleft()[0].cancel()
            futures = list(self._futures)
        if wait:
            concurrent.futures.wait(futures)


def _app():


    if _APP is not None:
        return _APP
    try:
        return QCoreApplication.instance()
    except Exception:  # noqa: BLE001
        return None


def on_main_thread() -> bool:
    if _app() is None:
        return True
    return threading.get_ident() == _MAIN_THREAD_ID


def main_qthread():

    if _MAIN_QTHREAD is not None:
        return _MAIN_QTHREAD
    app = _app()
    return app.thread() if app is not None else None




if _APP is not None and on_main_thread():
    with contextlib.suppress(Exception):
        main_thread_invoker()
















_HANDED: dict = {}

_HANDED_LOCK = threading.RLock()
_HANDED_KEYS = [0]


class Handed:


    __slots__ = ("_key",)

    def __init__(self, key: int):
        self._key = key

    def peek(self):

        _main_thread_only("Handed.peek")
        with _HANDED_LOCK:
            entry = _HANDED.get(self._key)
        return entry[0] if entry is not None else None

    def take(self):


        _main_thread_only("Handed.take")
        with _HANDED_LOCK:
            entry = _HANDED.pop(self._key, None)
        return entry[0] if entry is not None else None

    def release(self) -> None:

        key = self._key
        with _HANDED_LOCK:
            if key not in _HANDED:
                return
        if on_main_thread():
            _drop_handed(key)
            return
        with contextlib.suppress(Exception):
            main_thread_invoker().invoke(lambda: _drop_handed(key))

    def __del__(self):


        try:
            self.release()
        except Exception:  # nosec B110
            pass


def hand_over(obj, drop: Callable[[Any], None] | None = None) -> Handed:






    if not on_main_thread() and callable(getattr(obj, "moveToThread", None)):
        target = main_qthread()
        if target is not None:
            obj.moveToThread(target)
    with _HANDED_LOCK:
        _HANDED_KEYS[0] += 1
        key = _HANDED_KEYS[0]
        _HANDED[key] = (obj, drop)
    return Handed(key)


def take(handle):

    return handle.take() if handle is not None else None


def delete_here(obj) -> None:






    if obj is None:
        return
    try:
        from qgis.PyQt import sip

        if not sip.isdeleted(obj) and sip.ispyowned(obj):
            sip.delete(obj)
    except (ImportError, TypeError, RuntimeError):
        return


def _drop_handed(key: int) -> None:

    with _HANDED_LOCK:
        entry = _HANDED.pop(key, None)
    if entry is None:
        return
    obj, drop = entry
    entry = None
    try:
        (drop or delete_here)(obj)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Dropping what a worker handed over failed: {exc}")


def _main_thread_only(what: str) -> None:
    if not on_main_thread():
        raise RuntimeError(f"{what} is for the main thread: a worker keeps the handle, never the object.")






SLOW_MAIN_THREAD_S = 0.1


class MainThreadBusy(TimeoutError):
    pass









def _describe(fn) -> str:

    name = getattr(fn, "__qualname__", None) or getattr(fn, "__name__", None) or repr(fn)
    module = getattr(fn, "__module__", "") or ""
    return f"{module.rsplit('.', 1)[-1]}.{name}" if module else str(name)




_CALL = threading.local()


def current_call() -> str:

    return getattr(_CALL, "tool_call_id", "")


@contextlib.contextmanager
def calling(tool_call_id: str):

    previous = current_call()
    _CALL.tool_call_id = str(tool_call_id or "")
    try:
        yield
    finally:
        _CALL.tool_call_id = previous




_EARLY_ANSWERS: dict = {}

_DIALOG_LOOK_MS = 200


@contextlib.contextmanager
def answerable(tool_call_id: str, answer: Callable[[Any], bool]):

    key = str(tool_call_id or "")
    _EARLY_ANSWERS[key] = answer
    try:
        yield
    finally:
        _EARLY_ANSWERS.pop(key, None)


def answer_when_dialog_opens(result: dict) -> None:








    tool_call_id = current_call()
    if not tool_call_id or tool_call_id not in _EARLY_ANSWERS:
        return
    from qgis.PyQt.QtCore import QTimer
    from qgis.PyQt.QtWidgets import QApplication

    already = QApplication.activeModalWidget()

    def look() -> None:
        answer = _EARLY_ANSWERS.get(tool_call_id)
        if answer is None:
            return
        try:
            dialog = QApplication.activeModalWidget()
            if dialog is None or dialog is already:
                QTimer.singleShot(_DIALOG_LOOK_MS, look)
                return
            title = str(dialog.windowTitle() or "")
            if answer(dict(result, dialog_open=title)):
                _EARLY_ANSWERS.pop(tool_call_id, None)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Early answer for {tool_call_id} not sent: {type(exc).__name__}: {exc}")

    QTimer.singleShot(0, look)


def _without_locals(exc: BaseException) -> BaseException:







    seen = set()
    link = exc
    while link is not None and id(link) not in seen:
        seen.add(id(link))
        traceback.clear_frames(link.__traceback__)
        link = link.__cause__ or link.__context__
    return exc


def run_on_main_thread(fn, *args, timeout=10):













    if on_main_thread():
        return fn(*args)

    result_queue: queue.Queue = queue.Queue()
    cancel_check = net.current_cancel_check()
    caller = current_call()
    wait = _Wait(cancel_check)

    def _trampoline():


        started = time.perf_counter()
        _WAITS.append(wait)
        try:
            if wait.given_up or (cancel_check is not None and cancel_check()):
                raise InterruptedError("Main-thread work cancelled before it started")
            with calling(caller):
                result_queue.put(("ok", fn(*args)))
        except Exception as exc:  # noqa: BLE001
            result_queue.put(("err", _without_locals(exc)))
        finally:
            _WAITS.pop()
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
        if not wait.give_up():


            try:
                tag, payload = result_queue.get(timeout=max(timeout, _COMMITTED_GRACE_S))
            except queue.Empty:
                raise MainThreadBusy(
                    f"Main-thread work did not finish within {timeout}s, and it had already started "
                    f"changing the project, which may be partly changed."
                ) from exc
        else:
            raise MainThreadBusy(
                f"The main-thread work this call queued did not finish within {timeout}s. "
                f"Nothing it would have added to the project was added."
            ) from exc
    if tag == "err":
        raise payload
    return payload



_COMMITTED_GRACE_S = 30.0

_WAITS: list = []


class _Wait:



    def __init__(self, cancel_check):
        self._lock = threading.Lock()
        self._cancel_check = cancel_check
        self.given_up = False
        self.committed = False

    def commit(self) -> bool:
        with self._lock:
            if self.given_up or (self._cancel_check is not None and self._cancel_check()):
                self.given_up = True
                return False
            self.committed = True
            return True

    def give_up(self) -> bool:

        with self._lock:
            if self.committed:
                return False
            self.given_up = True
            return True


def still_awaited() -> bool:









    return _WAITS[-1].commit() if _WAITS else True


def marshalled() -> bool:





    return bool(_WAITS)


def _task_flags(task_cls, hidden: bool):






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






    try:
        from qgis.PyQt.QtCore import QTimer

        QTimer.singleShot(0, lambda: _ALIVE.discard(task))
    except Exception:  # noqa: BLE001
        _ALIVE.discard(task)


class on_any_failure:








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


    if _TASK["class"] is not None:
        return _TASK["class"]
    from qgis.core import QgsTask

    class _CallTask(QgsTask):






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
                except Exception:  # noqa: BLE001
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










    if index % every == 0:
        time.sleep(0)



        heartbeat()


def run_off_thread(description: str, work: Callable[[], Any],
                   on_done: Callable[[Any, str], None], hidden: bool = True):





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
    except Exception as exc:  # noqa: BLE001
        if task is not None:
            _ALIVE.discard(task)
        log_warning(f"Background tool run unavailable, running on the main thread: {exc}")
        return None
    return task


def cancel_all() -> None:


    for task in list(_ALIVE):
        cancel(task)


def cancel(task) -> None:


    if task is None:
        return
    try:
        task.cancel()
    except Exception:  # nosec B110
        pass
