# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Main-thread stall profiler: every freeze of the QGIS window, with its cause."""


























from __future__ import annotations

import collections
import json
import os
import sys
import threading
import time
from typing import Callable

from .logger import log, log_warning








_now = time.perf_counter



_SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PLUGIN_ROOT = os.path.dirname(_SRC_ROOT)

DEFAULT_THRESHOLD_MS = 30.0
DEFAULT_SAMPLE_MS = 4.0
MAX_STALLS_KEPT = 2000
MAX_SAMPLES_PER_STALL = 250
MAX_STACK_DEPTH = 48

_ENABLED = False
_PROFILER: StallProfiler | None = None
_LOCK = threading.Lock()


_PENDING_PROVIDERS: list = []





class _ProbeStats:
    __slots__ = ("count", "total_ms", "max_ms", "over")

    def __init__(self):
        self.count = 0
        self.total_ms = 0.0
        self.max_ms = 0.0
        self.over = 0

    def add(self, ms: float, threshold_ms: float) -> None:
        self.count += 1
        self.total_ms += ms
        if ms > self.max_ms:
            self.max_ms = ms
        if ms >= threshold_ms:
            self.over += 1

    def as_dict(self) -> dict:
        return {"count": self.count, "total_ms": round(self.total_ms, 1),
                "max_ms": round(self.max_ms, 1), "over": self.over}


class _Probe:
    """One timed block on the main thread. Records into the running profiler."""

    __slots__ = ("name", "started")

    def __init__(self, name: str):
        self.name = name
        self.started = 0.0

    def __enter__(self):
        profiler = _PROFILER
        if profiler is not None:
            self.started = _now()
            profiler._probe_stack.append(self.name)
        return self

    def __exit__(self, exc_type, exc, tb):
        profiler = _PROFILER
        if profiler is None or not self.started:
            return False
        stack = profiler._probe_stack
        if stack and stack[-1] == self.name:
            stack.pop()
        else:
            try:
                stack.remove(self.name)
            except ValueError:
                pass
        profiler._record_probe(self.name, (_now() - self.started) * 1000.0)
        return False


class _NullProbe:
    __slots__ = ()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


_NULL = _NullProbe()


def probe(name: str):
    """``with probe("threads.save"):`` around a block that may hold the main thread."""
    return _Probe(name) if _ENABLED else _NULL


def timed(name: str):
    """Decorator form of ``probe`` for a whole function."""

    def wrap(fn):
        def inner(*args, **kwargs):
            if not _ENABLED:
                return fn(*args, **kwargs)
            with _Probe(name):
                return fn(*args, **kwargs)

        inner.__name__ = getattr(fn, "__name__", "fn")
        inner.__qualname__ = getattr(fn, "__qualname__", inner.__name__)
        inner.__doc__ = getattr(fn, "__doc__", None)
        inner.__wrapped__ = fn
        return inner

    return wrap





class Stall:
    __slots__ = ("wall", "started", "ms", "context", "probes", "samples", "culprit", "marks", "holders")

    def __init__(self, wall: float, started: float, ms: float, context: str, probes: tuple,
                 samples: list, culprit: str, marks: tuple, holders: tuple = ()):
        self.wall = wall
        self.started = started
        self.ms = ms
        self.context = context
        self.probes = probes
        self.samples = samples
        self.culprit = culprit
        self.marks = marks


        self.holders = holders

    def as_dict(self, stacks: bool = False) -> dict:
        row = {"wall": round(self.wall, 3), "ms": round(self.ms, 1), "culprit": self.culprit,
               "context": self.context, "probes": list(self.probes), "marks": list(self.marks),
               "samples": len(self.samples), "holders": list(self.holders)}
        if stacks:
            row["stacks"] = [[list(frame) for frame in sample] for sample in self.samples[:8]]
        else:
            row["top"] = self.top_frames()
        return row

    def top_frames(self, limit: int = 6) -> list:
        """The most common innermost plugin frames across the samples."""
        counter: collections.Counter = collections.Counter()
        for sample in self.samples:
            for frame in sample:
                if _is_plugin_frame(frame[0]):
                    counter[_short(frame)] += 1
                    break
            else:
                if sample:
                    counter["(native or outside the plugin) " + _short(sample[0])] += 1
        return [f"{n}x {name}" for name, n in counter.most_common(limit)]





SAMPLER_THREAD_NAME = "ai-agent-stall-sampler"
_WAITING_IN = frozenset({"wait", "get", "put", "_wait_for_tstate_lock", "select", "sleep", "recv", "_recv",
                         "recv_into", "read", "readinto", "readline", "join", "acquire", "poll",
                         "accept", "connect", "sendall", "send", "settimeout"})


def _is_plugin_frame(filename: str) -> bool:
    return filename.startswith(_PLUGIN_ROOT)


def _short(frame) -> str:
    filename, line, func = frame
    if filename.startswith(_PLUGIN_ROOT):
        filename = os.path.relpath(filename, _PLUGIN_ROOT)
    else:
        filename = os.path.basename(filename)
    return f"{filename}:{line} {func}"





class StallProfiler:
    def __init__(self, threshold_ms: float = DEFAULT_THRESHOLD_MS, sample_ms: float = DEFAULT_SAMPLE_MS,
                 keep: int = MAX_STALLS_KEPT, log_path: str = ""):
        self.threshold_ms = float(threshold_ms)
        self.sample_s = max(0.001, float(sample_ms) / 1000.0)
        self.keep = int(keep)
        self.log_path = log_path
        self.stalls: collections.deque = collections.deque(maxlen=self.keep)
        self.probes: dict[str, _ProbeStats] = {}
        self.marks: collections.deque = collections.deque(maxlen=64)
        self.started_at = 0.0
        self.busy_total_ms = 0.0
        self.spans = 0
        self._context_providers: list[Callable[[], str]] = []
        self._probe_stack: list[str] = []


        self._last_tick = 0.0
        self._generation = 0
        self._last_span: tuple[int, float, float] = (0, 0.0, 0.0)
        self._main_ident = threading.main_thread().ident
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._timer = None
        self._file = None
        self._dropped = 0



    def start(self) -> bool:
        global _ENABLED, _PROFILER
        with _LOCK:
            if self._thread is not None:
                return True
            if not self._hook_timer():
                log_warning("Stall profiler: no Qt timer available; only probes are recorded")
            if self.log_path:
                try:
                    os.makedirs(os.path.dirname(self.log_path) or ".", exist_ok=True)
                    self._file = open(self.log_path, "a", encoding="utf-8")  # noqa: SIM115 - kept open on purpose
                except OSError as exc:
                    log_warning(f"Stall log not opened: {exc}")
                    self._file = None
            for fn in _PENDING_PROVIDERS:
                self.add_context_provider(fn)
            self.started_at = _now()
            self._stop.clear()
            retire_stale_samplers()
            self._thread = threading.Thread(target=self._loop, name=SAMPLER_THREAD_NAME, daemon=True)
            self._thread._ai_agent_stop = self._stop
            self._thread.start()
            _PROFILER = self
            _ENABLED = True
        log(f"Stall profiler on: threshold {self.threshold_ms:.0f} ms, sample every {self.sample_s * 1000:.0f} ms")
        return True

    def stop(self) -> None:
        global _ENABLED, _PROFILER
        with _LOCK:
            _ENABLED = False
            if _PROFILER is self:
                _PROFILER = None
            self._stop.set()
            thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=1.0)
        self._unhook()
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None

    @property
    def running(self) -> bool:
        return self._thread is not None

    def reset(self) -> None:
        with _LOCK:
            self.stalls.clear()
            self.probes.clear()
            self.marks.clear()
            self.busy_total_ms = 0.0
            self.spans = 0
            self.started_at = _now()

    def add_context_provider(self, fn: Callable[[], str]) -> None:
        if fn not in self._context_providers:
            self._context_providers.append(fn)

    def mark(self, text: str) -> None:
        """A named instant on the main-thread timeline: "run_start", "tool_call list_layers"."""
        self.marks.append((_now(), str(text)[:80]))



    def _hook_timer(self) -> bool:
        try:
            from qgis.PyQt.QtCore import Qt, QTimer

            timer = QTimer()

            precise = getattr(Qt, "PreciseTimer", None) or getattr(getattr(Qt, "TimerType", None), "PreciseTimer", None)
            if precise is not None:
                timer.setTimerType(precise)
            timer.setInterval(max(1, int(self.sample_s * 1000)))
            timer.timeout.connect(self._on_tick)
            timer.start()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Stall profiler: timer not started ({exc})")
            return False
        self._timer = timer
        self._last_tick = _now()
        return True

    def _unhook(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None:
            try:
                timer.stop()
                timer.timeout.disconnect(self._on_tick)
                timer.deleteLater()
            except Exception:  # nosec B110 - already torn down by Qt
                pass

    def _on_tick(self) -> None:
        """One heartbeat. The gap since the previous one is a busy span of the loop."""
        now = _now()
        last = self._last_tick
        self._last_tick = now
        gap = now - last
        self.spans += 1
        self.busy_total_ms += max(0.0, gap - self.sample_s) * 1000.0
        if gap * 1000.0 >= self.threshold_ms:
            self._generation += 1
            self._last_span = (self._generation, last, now)

    def _record_probe(self, name: str, ms: float) -> None:
        stats = self.probes.get(name)
        if stats is None:
            stats = self.probes[name] = _ProbeStats()
        stats.add(ms, self.threshold_ms)



    def _loop(self) -> None:
        open_since = 0.0
        samples: list = []
        probes_seen: set = set()
        context = ""
        marks: tuple = ()
        seen_span = self._last_span[0]
        threshold_s = self.threshold_ms / 1000.0
        holders: collections.Counter = collections.Counter()
        previous = _now()
        while not self._stop.is_set():
            time.sleep(self.sample_s)
            now = _now()
            if now - previous - self.sample_s >= threshold_s / 2:



                holders.update(self._worker_frames())
            previous = now
            last_tick = self._last_tick
            if now - last_tick >= threshold_s:

                if open_since != last_tick:
                    open_since = last_tick
                    samples = []
                    probes_seen = set()
                    marks = self._recent_marks(last_tick)
                    context = self._context()
                if len(samples) < MAX_SAMPLES_PER_STALL:
                    sample = self._sample()
                    if sample:
                        samples.append(sample)
                elif len(samples) == MAX_SAMPLES_PER_STALL:
                    self._dropped += 1
                    samples.append([])
                probes_seen.update(self._probe_stack)
                if not context:
                    context = self._context()
            span = self._last_span
            if span[0] != seen_span:
                seen_span = span[0]
                _gen, start, end = span
                ms = (end - start) * 1000.0
                if start != open_since:



                    samples = []
                    probes_seen = set(self._probe_stack)
                    marks = self._recent_marks(start)
                    context = self._context()
                else:
                    probes_seen.update(self._probe_stack)
                samples = [x for x in samples if x]
                if not samples:
                    late = self._sample()
                    if late:
                        samples.append(late)
                self._close(start, end, ms, context, probes_seen, samples, marks,
                            tuple(name for name, _n in holders.most_common(3)))
                open_since = 0.0
                samples = []
                context = ""
                holders = collections.Counter()

    def _sample(self) -> list:
        try:
            frame = sys._current_frames().get(self._main_ident)
        except Exception:  # noqa: BLE001
            return []
        out = []
        depth = 0
        while frame is not None and depth < MAX_STACK_DEPTH:
            code = frame.f_code
            out.append((code.co_filename, frame.f_lineno, code.co_name))
            frame = frame.f_back
            depth += 1
        return out

    def _worker_frames(self) -> list:
        """The innermost plugin frame of every thread but the main one and this one."""
        try:
            frames = sys._current_frames()
        except Exception:  # noqa: BLE001
            return []
        me = threading.get_ident()
        samplers = {t.ident for t in threading.enumerate() if t.name == SAMPLER_THREAD_NAME}
        found = []
        for ident, frame in frames.items():
            if ident in (self._main_ident, me) or ident in samplers:
                continue
            if frame.f_code.co_name in _WAITING_IN:
                continue
            depth = 0
            while frame is not None and depth < MAX_STACK_DEPTH:
                if _is_plugin_frame(frame.f_code.co_filename):
                    found.append(_short((frame.f_code.co_filename, frame.f_lineno, frame.f_code.co_name)))
                    break
                frame = frame.f_back
                depth += 1
        return found

    def _context(self) -> str:
        parts = []
        for fn in list(self._context_providers):
            try:
                text = str(fn() or "")
            except Exception:  # noqa: BLE001
                text = ""
            if text:
                parts.append(text)
        return "; ".join(parts)[:200]

    def _recent_marks(self, since: float, window_s: float = 2.0) -> tuple:
        return tuple(text for t, text in list(self.marks) if since - window_s <= t <= since + 0.001)[-4:]

    def _close(self, start: float, end: float, ms: float, context: str, probes: set, samples: list,
               marks: tuple, holders: tuple = ()) -> None:
        culprit = self._culprit(samples, probes, holders)
        stall = Stall(time.time() - (_now() - start), start, ms, context, tuple(sorted(probes)),
                      samples, culprit, marks, holders)




        with _LOCK:
            self.stalls.append(stall)
        if self._file is not None:
            try:
                self._file.write(json.dumps(stall.as_dict(), ensure_ascii=False) + "\n")
                self._file.flush()
            except (OSError, ValueError):
                pass

    @staticmethod
    def _culprit(samples: list, probes: set, holders: tuple = ()) -> str:
        """The innermost plugin frame most samples agree on."""


        ours: collections.Counter = collections.Counter()
        theirs: collections.Counter = collections.Counter()
        for sample in samples:
            for frame in sample:
                if _is_plugin_frame(frame[0]):
                    ours[_short(frame)] += 1
                    break
            else:
                if sample:
                    theirs[_short(sample[0])] += 1
        total = sum(ours.values()) + sum(theirs.values())
        if ours and sum(ours.values()) * 2 >= total:
            return ours.most_common(1)[0][0]
        if theirs:
            prefix = "(mostly outside the plugin) " if ours else "(native or outside the plugin) "
            return prefix + theirs.most_common(1)[0][0]
        if holders:
            return "(GIL held by a worker in) " + holders[0]
        if probes:
            return "probe " + "+".join(sorted(probes))
        return "(no sample: the GIL was held by a native call for the whole span)"



    def summary(self, min_ms: float | None = None) -> dict:
        """Counts, totals and the tables the report prints, as one dict."""
        min_ms = self.threshold_ms if min_ms is None else float(min_ms)





        with _LOCK:
            rows = [s for s in self.stalls if s.ms >= min_ms]
            spans, busy_total_ms, started_at = self.spans, self.busy_total_ms, self.started_at
        by_culprit: dict[str, list] = collections.defaultdict(lambda: [0, 0.0, 0.0])
        by_context: dict[str, list] = collections.defaultdict(lambda: [0, 0.0, 0.0])
        by_probe: dict[str, list] = collections.defaultdict(lambda: [0, 0.0, 0.0])
        for s in rows:
            for key, table in ((s.culprit, by_culprit), (s.context or "(no tool call in flight)", by_context)):
                entry = table[key]
                entry[0] += 1
                entry[1] += s.ms
                entry[2] = max(entry[2], s.ms)
            for name in s.probes:
                entry = by_probe[name]
                entry[0] += 1
                entry[1] += s.ms
                entry[2] = max(entry[2], s.ms)

        def table(d: dict) -> list:
            return [{"key": k, "count": v[0], "total_ms": round(v[1], 1), "max_ms": round(v[2], 1)}
                    for k, v in sorted(d.items(), key=lambda kv: -kv[1][1])]

        window = _now() - started_at if started_at else 0.0
        return {
            "threshold_ms": self.threshold_ms,
            "window_s": round(window, 1),
            "spans": spans,
            "busy_total_ms": round(busy_total_ms, 1),
            "stalls": len(rows),
            "stalled_ms": round(sum(s.ms for s in rows), 1),
            "worst_ms": round(max((s.ms for s in rows), default=0.0), 1),
            "p95_ms": round(_percentile([s.ms for s in rows], 0.95), 1),
            "by_culprit": table(by_culprit),
            "by_context": table(by_context),
            "by_probe": table(by_probe),
            "probes": {k: v.as_dict() for k, v in sorted(self.probes.items(), key=lambda kv: -kv[1].total_ms)},
            "worst": [s.as_dict() for s in sorted(rows, key=lambda s: -s.ms)[:15]],
            "samples_dropped": self._dropped,
        }

    def report(self, min_ms: float | None = None, limit: int = 25) -> str:
        data = self.summary(min_ms)
        lines = [
            f"Stalls >= {data['threshold_ms'] if min_ms is None else min_ms:.0f} ms: {data['stalls']} "
            f"({data['stalled_ms']:.0f} ms total, worst {data['worst_ms']:.0f} ms, p95 {data['p95_ms']:.0f} ms) "
            f"over {data['window_s']:.0f} s; event loop busy {data['busy_total_ms']:.0f} ms in {data['spans']} spans",
            "",
            "By culprit (innermost plugin frame):",
        ]
        for row in data["by_culprit"][:limit]:
            lines.append(f"  {row['total_ms']:8.0f} ms  {row['count']:4d}x  max {row['max_ms']:6.0f}  {row['key']}")
        lines += ["", "By tool call in flight:"]
        for row in data["by_context"][:limit]:
            lines.append(f"  {row['total_ms']:8.0f} ms  {row['count']:4d}x  max {row['max_ms']:6.0f}  {row['key']}")
        if data["by_probe"]:
            lines += ["", "Probes active during stalls:"]
            for row in data["by_probe"][:limit]:
                lines.append(f"  {row['total_ms']:8.0f} ms  {row['count']:4d}x  max {row['max_ms']:6.0f}  {row['key']}")
        if data["probes"]:
            lines += ["", "Probe timings (all calls, main thread):"]
            for name, p in list(data["probes"].items())[:limit]:
                lines.append(f"  {p['total_ms']:8.0f} ms  {p['count']:5d}x  max {p['max_ms']:6.0f}  "
                             f"over {p['over']:3d}  {name}")
        lines += ["", "Worst stalls:"]
        for row in data["worst"][:10]:
            lines.append(f"  {row['ms']:6.0f} ms  {row['culprit']}"
                         + (f"  [{row['context']}]" if row["context"] else "")
                         + (f"  probes={','.join(row['probes'])}" if row["probes"] else "")
                         + (f"  marks={','.join(row['marks'])}" if row["marks"] else ""))
            for top in row.get("top", [])[:3]:
                lines.append(f"            {top}")
        return "\n".join(lines)


def _percentile(values: list, q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return float(ordered[index])





def retire_stale_samplers() -> int:
    """Stop the sampler threads of earlier plugin instances."""







    retired = 0
    for thread in threading.enumerate():
        if thread.name != SAMPLER_THREAD_NAME or thread is threading.current_thread():
            continue
        stop = getattr(thread, "_ai_agent_stop", None)
        if stop is not None and hasattr(stop, "set"):
            stop.set()
            retired += 1
    return retired


def start(threshold_ms: float = DEFAULT_THRESHOLD_MS, sample_ms: float = DEFAULT_SAMPLE_MS,
          log_path: str = "") -> StallProfiler:
    """Start (or return) the profiler. Idempotent; call from the main thread."""
    with _LOCK:
        current = _PROFILER
    if current is not None and current.running:
        return current
    profiler = StallProfiler(threshold_ms=threshold_ms, sample_ms=sample_ms, log_path=log_path)
    profiler.start()
    return profiler


def stop() -> None:
    with _LOCK:
        current = _PROFILER
    if current is not None:
        current.stop()


def current() -> StallProfiler | None:
    return _PROFILER


def enabled() -> bool:
    return _ENABLED


def mark(text: str) -> None:
    profiler = _PROFILER
    if profiler is not None:
        profiler.mark(text)


def add_context_provider(fn: Callable[[], str]) -> None:
    """Register now or later: providers are handed to whichever profiler starts."""
    if fn not in _PENDING_PROVIDERS:
        _PENDING_PROVIDERS.append(fn)
    profiler = _PROFILER
    if profiler is not None:
        profiler.add_context_provider(fn)


def report(min_ms: float | None = None) -> str:
    profiler = _PROFILER
    return profiler.report(min_ms) if profiler is not None else "Stall profiler is not running."


def summary(min_ms: float | None = None) -> dict:
    profiler = _PROFILER
    return profiler.summary(min_ms) if profiler is not None else {}


def start_from_environment() -> None:
    """``AI_AGENT_STALLS=1`` (or a threshold in ms) starts the profiler at plugin load, logging every stall to ``<state_dir>/stalls.jsonl``."""

    raw = os.environ.get("AI_AGENT_STALLS", "").strip()
    if not raw or raw in ("0", "false", "no", "off"):
        return
    try:
        threshold = float(raw) if raw not in ("1", "true", "yes", "on") else DEFAULT_THRESHOLD_MS
    except ValueError:
        threshold = DEFAULT_THRESHOLD_MS
    try:
        from .settings import state_dir

        path = os.path.join(state_dir(), "stalls.jsonl")
    except Exception:  # noqa: BLE001
        path = ""
    start(threshold_ms=threshold, log_path=path)
