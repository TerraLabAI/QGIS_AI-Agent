# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""What this computer is, how it is coping right now, and what that costs a limit."""

























































from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
import threading
import time
from collections import namedtuple

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

MB = 1024 * 1024








REFERENCE_CORES = 8
REFERENCE_RAM_MB = 16 * 1024
































_BENCH_ROUNDS = 200_000



SMALL_CORES = 4
SMALL_RAM_MB = 8 * 1024











CLASS_SCALE = {"large": 1.0, "normal": 0.6, "small": 0.3}





PRESSURE_FLOOR = 0.25





MAX_CLOCK_STRETCH = 2.5




FREEZE_MEMORY_S = 300.0




FREEZE_SATURATION_S = 15.0




SAMPLE_TTL_S = 2.0


















_SCALED: dict[str, tuple[str, float, bool]] = {


    "MAX_FEATURES_MATERIALISED": ("down", 50_000, False),



    "SYNC_FEATURE_LOOP_MAX": ("down", 250, False),

    "MAX_FEATURES_CREATED": ("down", 20_000, False),


    "MAX_FEATURES_PER_CALL": ("down", 500, False),


    "MAX_RENDER_PIXELS": ("down", 1280 * 720, False),
    "MAX_RENDER_WIDTH_PX": ("down", 1280, False),
    "MAX_RENDER_HEIGHT_PX": ("down", 720, False),

    "MAX_DOWNLOAD_BYTES": ("down", 16 * MB, True),








    "MAX_FETCH_KM2": ("down", 8.0, True),

    "MAX_LAYERS_PER_RUN": ("down", 8, False),









    "MAX_RENDER_DPI": ("down", 300, False),




    "GEOMETRY_CHECK_MAX_VERTICES": ("down", 50_000, False),
    "GEOMETRY_CHECK_MAX_TOTAL_VERTICES": ("down", 500_000, False),

    "CALL_MAX_SECONDS_MAIN": ("up", 0.0, False),
    "CALL_MAX_SECONDS_MAIN_LONG": ("up", 0.0, False),
    "CALL_MAX_SECONDS_BACKGROUND": ("up", 0.0, False),
    "RUN_MAX_SECONDS": ("up", 0.0, False),
    "GEOMETRY_CHECK_SECONDS": ("up", 0.0, False),
}


Profile = namedtuple("Profile", "cores ram_mb name scale source")
Sample = namedtuple("Sample", "available_mb total_mb swap_used_mb rss_mb os_pressure at")





def _libc():
    """libc, or None. Loaded once; a failure here means every probe answers None."""
    global _LIBC
    if _LIBC is _UNSET:
        try:
            _LIBC = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        except Exception:  # noqa: BLE001 - no libc means the reference machine
            _LIBC = None
    return _LIBC


_UNSET = object()
_LIBC = _UNSET


def _sysctl_int(name: str, width: int = 8):
    """One integer sysctl on macOS, or None. About twenty microseconds."""
    libc = _libc()
    if libc is None:
        return None
    try:
        buf = ctypes.create_string_buffer(width)
        size = ctypes.c_size_t(width)
        if libc.sysctlbyname(name.encode(), buf, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
            return None
        return int.from_bytes(buf.raw[: size.value], sys.byteorder)
    except Exception:  # noqa: BLE001
        return None


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _VMStatistics64(ctypes.Structure):
    """``vm_statistics64_data_t``: what ``host_statistics64`` fills in."""






    _fields_ = [
        ("free_count", ctypes.c_uint32), ("active_count", ctypes.c_uint32),
        ("inactive_count", ctypes.c_uint32), ("wire_count", ctypes.c_uint32),
        ("zero_fill_count", ctypes.c_uint64), ("reactivations", ctypes.c_uint64),
        ("pageins", ctypes.c_uint64), ("pageouts", ctypes.c_uint64),
        ("faults", ctypes.c_uint64), ("cow_faults", ctypes.c_uint64),
        ("lookups", ctypes.c_uint64), ("hits", ctypes.c_uint64),
        ("purges", ctypes.c_uint64), ("purgeable_count", ctypes.c_uint32),
        ("speculative_count", ctypes.c_uint32), ("decompressions", ctypes.c_uint64),
        ("compressions", ctypes.c_uint64), ("swapins", ctypes.c_uint64),
        ("swapouts", ctypes.c_uint64), ("compressor_page_count", ctypes.c_uint32),
        ("throttled_count", ctypes.c_uint32), ("external_page_count", ctypes.c_uint32),
        ("internal_page_count", ctypes.c_uint32),
        ("total_uncompressed_pages_in_compressor", ctypes.c_uint64),
    ]


_HOST_VM_INFO64 = 4


def total_memory_mb() -> int | None:
    """Physical memory of the machine in MB, or None when it cannot be read."""
    try:
        if IS_WINDOWS:
            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return int(status.ullTotalPhys // MB)
        if IS_MACOS:
            size = _sysctl_int("hw.memsize", 8)
            return int(size // MB) if size else None
        pages = os.sysconf("SC_PHYS_PAGES")
        page = os.sysconf("SC_PAGE_SIZE")
        return int(pages * page // MB)
    except Exception:  # noqa: BLE001 - an unreadable machine is the reference machine
        return None


def _macos_available_mb() -> int | None:
    """Free plus reclaimable memory on macOS, through mach."""





    libc = _libc()
    if libc is None:
        return None
    try:
        libc.mach_host_self.restype = ctypes.c_uint
        stats = _VMStatistics64()
        count = ctypes.c_uint(ctypes.sizeof(_VMStatistics64) // ctypes.sizeof(ctypes.c_int))
        if libc.host_statistics64(libc.mach_host_self(), _HOST_VM_INFO64,
                                  ctypes.byref(stats), ctypes.byref(count)) != 0:
            return None
        page = _sysctl_int("hw.pagesize", 8) or 4096
        pages = (stats.free_count + stats.inactive_count
                 + stats.purgeable_count + stats.speculative_count)
        return int(pages * page // MB)
    except Exception:  # noqa: BLE001
        return None


def _linux_meminfo() -> tuple[int | None, int | None]:
    """(available MB, swap used MB) from /proc/meminfo, or (None, None)."""
    try:
        values = {}
        with open("/proc/meminfo", encoding="ascii", errors="replace") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                if key in ("MemAvailable", "MemFree", "SwapTotal", "SwapFree"):
                    values[key] = int(rest.strip().split()[0]) // 1024
        available = values.get("MemAvailable", values.get("MemFree"))
        swap_used = None
        if "SwapTotal" in values and "SwapFree" in values:
            swap_used = max(0, values["SwapTotal"] - values["SwapFree"])
        return available, swap_used
    except Exception:  # noqa: BLE001
        return None, None


def _macos_swap_used_mb() -> int | None:
    """Swap in use on macOS, from ``vm.swapusage``'s xsw_usage struct."""
    libc = _libc()
    if libc is None:
        return None
    try:
        buf = ctypes.create_string_buffer(32)
        size = ctypes.c_size_t(32)
        if libc.sysctlbyname(b"vm.swapusage", buf, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
            return None
        used = int.from_bytes(buf.raw[16:24], sys.byteorder)
        return int(used // MB)
    except Exception:  # noqa: BLE001
        return None


def resident_memory_mb() -> float | None:
    """Resident memory of *this* process right now, in MB, or None."""





    try:
        if IS_WINDOWS:
            from .host_platform import peak_memory_mb




            return peak_memory_mb()
        if IS_LINUX:
            with open("/proc/self/statm", encoding="ascii") as handle:
                pages = int(handle.read().split()[1])
            return round(pages * os.sysconf("SC_PAGE_SIZE") / MB, 1)
        if IS_MACOS:
            libc = _libc()
            if libc is None:
                return None



            buf = ctypes.create_string_buffer(256)
            got = libc.proc_pidinfo(os.getpid(), 4, ctypes.c_uint64(0), buf, ctypes.c_int(256))
            if got <= 16:
                return None
            resident = int.from_bytes(buf.raw[8:16], sys.byteorder)
            return round(resident / MB, 1)
    except Exception:  # noqa: BLE001
        return None
    return None


def _os_pressure() -> float | None:
    """The operating system's own answer, 0.0 calm to 1.0 critical, or None."""





    try:
        if IS_MACOS:
            level = _sysctl_int("kern.memorystatus_vm_pressure_level", 4)
            return {1: 0.0, 2: 0.6, 4: 1.0}.get(level) if level is not None else None
        if IS_WINDOWS:
            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None


            return max(0.0, (float(status.dwMemoryLoad) - 70.0) / 30.0)
    except Exception:  # noqa: BLE001
        return None
    return None


def _time_loop(rounds: int = _BENCH_ROUNDS) -> float:
    """Milliseconds for a fixed arithmetic loop."""






    start = time.perf_counter()
    value = 0
    for index in range(rounds):
        value = (value + index * 3) % 1000003
    return (time.perf_counter() - start) * 1000.0


def measure_bench_ms(runs: int = 5) -> float:
    """Milliseconds for the fixed loop, best of *runs*."""










    try:
        _time_loop(_BENCH_ROUNDS // 10)


        return round(min(_time_loop() for _ in range(max(1, runs))), 3)
    except Exception:  # noqa: BLE001
        return 0.0




_LOCK = threading.Lock()
_PROFILE: Profile | None = None


def _class_of(cores: int, ram_mb: int) -> str:
    """Which band the machine is in, from the two facts that are exact."""







    if ram_mb < SMALL_RAM_MB or cores < SMALL_CORES:
        return "small"
    if ram_mb < REFERENCE_RAM_MB or cores < REFERENCE_CORES:
        return "normal"
    return "large"


def _parse_override(text: str) -> Profile | None:
    """``AI_AGENT_MACHINE``: a class name, or ``cores=2,ram_mb=4096,speed=0.35``."""
    text = (text or "").strip().lower()
    if not text:
        return None
    if text in CLASS_SCALE:


        cores, ram_mb = {"small": (2, 4096), "normal": (4, 8192),
                         "large": (REFERENCE_CORES, REFERENCE_RAM_MB)}[text]
        return Profile(cores, ram_mb, text, CLASS_SCALE[text], "override")
    fields: dict[str, float] = {}
    for part in text.replace(";", ",").split(","):
        key, _, value = part.partition("=")
        key = key.strip()
        if key in ("cores", "ram_mb"):
            try:
                fields[key] = float(value)
            except ValueError:
                return None
    if not fields:
        return None
    cores = int(fields.get("cores", os.cpu_count() or 4))
    ram_mb = int(fields.get("ram_mb", total_memory_mb() or REFERENCE_RAM_MB))
    name = _class_of(cores, ram_mb)
    return Profile(cores, ram_mb, name, CLASS_SCALE[name], "override")


def profile() -> Profile:
    """What this machine is."""





    global _PROFILE
    with _LOCK:
        override = _parse_override(os.environ.get("AI_AGENT_MACHINE", ""))
        if override is not None:
            return override
        if _PROFILE is not None:
            return _PROFILE
        cores = os.cpu_count() or 2
        ram_mb = total_memory_mb()
        if ram_mb is None:





            _PROFILE = Profile(cores, REFERENCE_RAM_MB, "large", CLASS_SCALE["large"], "unreadable")
            return _PROFILE
        name = _class_of(cores, ram_mb)
        _PROFILE = Profile(cores, ram_mb, name, CLASS_SCALE[name], "measured")
        return _PROFILE


def forget_profile() -> None:
    """Drop the cached profile. For the tests and for a support session."""
    global _PROFILE
    with _LOCK:
        _PROFILE = None




_SAMPLE: Sample | None = None
_FREEZES: list[tuple[float, float]] = []


def sample(force: bool = False) -> Sample:
    """A live reading of memory and pressure, cached for ``SAMPLE_TTL_S``."""
    global _SAMPLE
    now = time.monotonic()
    cached = _SAMPLE
    if not force and cached is not None and now - cached.at < SAMPLE_TTL_S:
        return cached
    total = total_memory_mb()
    available = swap_used = None
    if IS_MACOS:
        available, swap_used = _macos_available_mb(), _macos_swap_used_mb()
    elif IS_LINUX:
        available, swap_used = _linux_meminfo()
    elif IS_WINDOWS:
        try:
            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                available = int(status.ullAvailPhys // MB)
                swap_used = int((status.ullTotalPageFile - status.ullAvailPageFile) // MB)
        except Exception:  # nosec B110 - a machine whose memory cannot be read reports None and
            pass
    fresh = Sample(available, total, swap_used, resident_memory_mb(), _os_pressure(), now)
    _SAMPLE = fresh
    return fresh


def note_freeze(seconds: float, _who: str = "") -> None:
    """The watchdog saw the event loop stop for *seconds*."""






    try:
        now = time.monotonic()
        _FREEZES.append((now, float(seconds)))
        cutoff = now - FREEZE_MEMORY_S
        while _FREEZES and _FREEZES[0][0] < cutoff:
            _FREEZES.pop(0)
    except Exception:  # nosec B110 - called from a Qt slot: an exception thrown out of one crosses
        pass


def note_slow_call(name: str, seconds: float, budget: float) -> None:
    """A main-thread handler came back far later than its budget."""












    try:
        over = float(seconds) - float(budget)
        if over > 0.0:
            note_freeze(over, name)
    except Exception:  # nosec B110 - same rule as note_freeze above, and the caller is the
        pass


def recent_freeze_seconds(now: float | None = None) -> float:
    """Seconds of freeze inside the memory window, faded by how long ago they were."""





    now = time.monotonic() if now is None else now
    total = 0.0
    for when, seconds in list(_FREEZES):
        age = now - when
        if age >= FREEZE_MEMORY_S:
            continue
        total += seconds * (1.0 - age / FREEZE_MEMORY_S)
    return total


def forget_freezes() -> None:
    """Drop the freeze history. For the tests and for a support session."""
    _FREEZES.clear()


def _pressure_override() -> float | None:
    """``AI_AGENT_MACHINE_PRESSURE``: pin what the machine is feeling, 0.0 to 1.0."""









    text = os.environ.get("AI_AGENT_MACHINE_PRESSURE", "").strip()
    if not text:
        return None
    try:
        return max(0.0, min(1.0, float(text)))
    except ValueError:
        return None


def pressure() -> float:
    """How close this machine is to not coping, 0.0 calm to 1.0 saturated."""






    pinned = _pressure_override()
    if pinned is not None:
        return pinned
    now = sample()
    worst = 0.0
    if now.available_mb is not None and now.total_mb:



        free_share = max(0.0, min(1.0, now.available_mb / float(now.total_mb)))
        worst = max(worst, min(1.0, max(0.0, (0.25 - free_share) / 0.25)))
    if now.os_pressure is not None:
        worst = max(worst, now.os_pressure)
    if now.swap_used_mb is not None and now.total_mb:













        share = now.swap_used_mb / float(now.total_mb)
        term = min(1.0, max(0.0, (share - 0.25) / 0.75))
        if now.os_pressure is not None:
            term = min(term, 0.3 + 0.7 * now.os_pressure)
        worst = max(worst, term)
    frozen = recent_freeze_seconds()
    if frozen > 0.0:
        worst = max(worst, min(1.0, frozen / FREEZE_SATURATION_S))
    return round(worst, 3)


def factor() -> float:
    """The multiplier a ceiling gets on this machine, right now."""






    live = 1.0 - (1.0 - PRESSURE_FLOOR) * pressure()
    return round(max(PRESSURE_FLOOR, min(1.0, live)) * profile().scale, 4)


def link_factor() -> float:
    """What the connection is worth as a multiplier, or 1.0 when it is unmeasured."""






    try:
        from . import net

        return net.link_factor()
    except Exception:  # noqa: BLE001 - an unmeasured link is a fast link
        return 1.0


def clock_stretch() -> float:
    """How much longer a call is allowed to take here than on the reference machine."""






    scale = profile().scale
    if scale >= 1.0:
        return 1.0
    return round(min(MAX_CLOCK_STRETCH, 1.0 / max(scale, 0.01)), 3)





def scale(name: str, value):
    """*value* adjusted for this machine: a ceiling lowered, a clock stretched."""






    rule = _SCALED.get(name)
    if rule is None or not isinstance(value, (int, float)) or isinstance(value, bool):
        return value
    direction, floor, link_bound = rule
    try:
        if direction == "up":
            adjusted = float(value) * clock_stretch()
            return type(value)(adjusted) if isinstance(value, int) else adjusted
        adjusted = max(float(floor), float(value) * factor() * (link_factor() if link_bound else 1.0))
        adjusted = min(adjusted, float(value))
        return int(adjusted) if isinstance(value, int) else round(adjusted, 3)
    except Exception:  # noqa: BLE001 - an unscalable value is the shipped value
        return value


def scales(name: str) -> bool:
    """Whether *name* is a limit this machine adjusts. Read by the tests and the report."""
    return name in _SCALED


def link_bound(name: str) -> bool:
    """Whether the connection pays for *name* as well as the processor."""






    rule = _SCALED.get(name)
    return bool(rule and rule[2])





def describe() -> str:
    """One line for the log: what the machine is and what it is currently worth."""
    who = profile()
    return (f"{who.name} ({who.cores} cores, {who.ram_mb // 1024} GB), "
            f"pressure {pressure():.2f}, ceilings at {factor():.2f}x")


def note() -> str:
    """The sentence the model is given, so it asks for a size that fits the first time."""






    share = factor()
    if share >= 0.999:
        return ""
    who = profile()





    if who.scale < 1.0:
        what = f"This computer is {who.name} ({who.cores} cores, {who.ram_mb // 1024} GB)"
    else:
        what = "This computer is busy right now (little free memory)"
    return (f"{what} and is at {share:.0%} of the standard limits. Ask for smaller areas, fewer "
            "features and smaller images than the tool descriptions allow, and split long jobs "
            "into steps.")


def report() -> dict:
    """Everything this module knows, for the debug menu and the perf matrix."""
    who, now = profile(), sample(force=True)
    return {
        "class": who.name,
        "cores": who.cores,
        "ram_mb": who.ram_mb,
        "bench_ms": measure_bench_ms(),
        "source": who.source,
        "available_mb": now.available_mb,
        "swap_used_mb": now.swap_used_mb,
        "process_rss_mb": now.rss_mb,
        "os_pressure": now.os_pressure,
        "recent_freeze_s": round(recent_freeze_seconds(), 1),
        "pressure": pressure(),
        "factor": factor(),
        "clock_stretch": clock_stretch(),
        "link_factor": link_factor(),
    }
