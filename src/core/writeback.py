# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Coalesced, off-thread file writes for the plugin's own state files."""




















from __future__ import annotations

import os
import queue
import tempfile
import threading
import time
from typing import Callable

from .host_platform import retry_file_op
from .logger import log_warning

DEFAULT_DELAY_MS = 150


def _qt_app():
    try:
        from qgis.PyQt.QtCore import QCoreApplication

        return QCoreApplication.instance()
    except Exception:  # noqa: BLE001 - no Qt at all
        return None


def write_atomic(path: str, text: str) -> None:
    """Write ``text`` to ``path`` through a temporary file and a rename."""


    fd, tmp = tempfile.mkstemp(prefix=".agent-", suffix=".tmp", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        retry_file_op(os.replace, tmp, path)
    finally:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass


_DRAIN_TIMEOUT_S = 2.0


class WriteBehind:
    """One coalescing timer, one writer thread, any number of files."""

    def __init__(self, delay_ms: int = DEFAULT_DELAY_MS, name: str = "ai-agent-writeback"):
        self._delay_ms = int(delay_ms)
        self._name = name
        self._pending: dict[str, Callable[[], str | None]] = {}
        self._jobs: list[Callable[[], None]] = []
        self._queue: queue.Queue = queue.Queue()


        self._queued_writes: dict[str, list] = {}
        self._thread: threading.Thread | None = None
        self._timer = None
        self._lock = threading.Lock()
        self._closed = False



    def schedule(self, path: str, produce: Callable[[], str | None]) -> None:
        """Write ``produce()`` to ``path`` soon."""

        if self._closed:
            self._write_now(path, produce)
            return
        self._pending[path] = produce
        if not self._arm():
            self.flush()

    def schedule_job(self, job: Callable[[], None]) -> None:
        """Run ``job`` on the writer thread after the pending writes (a prune, a directory listing)."""

        if self._closed:
            self._safe(job)
            return
        if job not in self._jobs:
            self._jobs.append(job)
        if not self._arm():
            self.flush()

    def _arm(self) -> bool:
        """Start (or restart) the coalescing timer. False without a Qt loop."""
        if self._timer is None:
            app = _qt_app()
            if app is None:
                return False
            try:
                from qgis.PyQt.QtCore import QTimer

                timer = QTimer()
                timer.setSingleShot(True)
                timer.setInterval(self._delay_ms)
                timer.timeout.connect(self.flush)
            except Exception:  # noqa: BLE001 - a Qt without timers is no Qt
                return False
            self._timer = timer
        try:
            self._timer.start()
        except RuntimeError:

            self._timer = None
            return False
        return True



    def flush(self, wait: bool = False) -> None:
        """Produce every pending text on this thread and hand it to the writer."""

        pending, self._pending = self._pending, {}
        jobs, self._jobs = self._jobs, []
        if self._timer is not None:
            try:
                self._timer.stop()
            except RuntimeError:
                self._timer = None
        for path, produce in pending.items():
            try:
                text = produce()
            except Exception as exc:  # noqa: BLE001 - one bad producer never blocks the rest
                log_warning(f"State file not serialised ({os.path.basename(path)}): {exc}")
                continue
            if text is None:
                continue
            self._submit(path, text)
        for job in jobs:
            self._submit_job(job)
        if wait:
            self._drain()

    def _submit(self, path: str, text: str) -> None:
        if not self._start_thread():
            self._safe(lambda: write_atomic(path, text), path)
            return
        with self._lock:
            item = self._queued_writes.get(path)
            if item is not None:
                item[1] = text
                return
            item = [path, text]
            self._queued_writes[path] = item
            self._queue.put(item)

    def _submit_job(self, job: Callable[[], None]) -> None:
        if not self._start_thread():
            self._safe(job)
            return
        with self._lock:


            self._queued_writes.clear()
            self._queue.put((None, job))

    def _write_now(self, path: str, produce: Callable[[], str | None]) -> None:
        try:
            text = produce()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"State file not serialised ({os.path.basename(path)}): {exc}")
            return
        if text is not None:
            self._safe(lambda: write_atomic(path, text), path)

    @staticmethod
    def _safe(job: Callable[[], None], path: str = "") -> None:
        try:
            job()
        except Exception as exc:  # noqa: BLE001 - a failed state write is a log line, not a crash
            log_warning(f"State file not written{' (' + os.path.basename(path) + ')' if path else ''}: {exc}")



    def _start_thread(self) -> bool:
        with self._lock:
            if self._thread is not None:
                return True
            if _qt_app() is None:
                return False
            thread = threading.Thread(target=self._loop, name=self._name, daemon=True)
            try:
                thread.start()
            except RuntimeError:
                return False
            self._thread = thread
            return True

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                with self._lock:
                    path, payload = item
                    if path is not None and self._queued_writes.get(path) is item:
                        self._queued_writes.pop(path)
                if path is None:
                    self._safe(payload)
                else:
                    self._write(path, payload)
            finally:
                self._queue.task_done()

    def _write(self, path: str, text: str) -> None:
        self._safe(lambda: write_atomic(path, text), path)

    def _drain(self) -> None:
        """Wait for the writer, but never longer than ``_DRAIN_TIMEOUT_S``."""








        thread = self._thread
        if thread is None:
            return
        deadline = time.monotonic() + _DRAIN_TIMEOUT_S
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        if self._queue.unfinished_tasks:
            log_warning("State files still being written at unload; QGIS is not kept waiting for them.")

    def close(self) -> None:
        """Flush, wait for the disk, stop the thread. Plugin unload."""
        if self._closed:
            return
        self.flush(wait=True)
        self._closed = True
        with self._lock:
            thread, self._thread = self._thread, None
        if thread is not None:
            self._queue.put(None)
            thread.join(timeout=2.0)
        timer, self._timer = self._timer, None
        if timer is not None:
            try:
                timer.stop()
                timer.deleteLater()
            except RuntimeError:
                pass

    @property
    def pending(self) -> int:
        return len(self._pending) + len(self._jobs)

    def delete(self, path: str) -> None:
        """Delete after every already queued write, without resurrecting history."""
        self._pending.pop(path, None)

        def remove():
            try:
                retry_file_op(os.remove, path)
            except FileNotFoundError:
                pass

        if self._closed:
            self._safe(remove, path)
        else:
            self._submit_job(remove)
