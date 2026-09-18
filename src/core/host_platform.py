# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Everything in the tool catalog that has to know which OS it runs on."""



from __future__ import annotations

import os
import re
import sys
import time

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"





_FILE_OP_PAUSES_S = (0.05, 0.1, 0.2, 0.3, 0.5, 0.85)





_TRANSIENT_WINERRORS = frozenset((5, 32, 33, 145, 1224))


def _is_transient(exc: OSError) -> bool:
    if isinstance(exc, PermissionError):
        return True
    return getattr(exc, "winerror", None) in _TRANSIENT_WINERRORS


def retry_file_op(op, *args):
    """Call ``op(*args)`` again after a transient Windows refusal, then give up."""




    if not IS_WINDOWS:
        return op(*args)
    for pause in _FILE_OP_PAUSES_S:
        try:
            return op(*args)
        except OSError as exc:
            if not _is_transient(exc):
                raise
            time.sleep(pause)
    return op(*args)


_LEADING_WINDOWS_VAR = re.compile(r"^%[A-Za-z_][A-Za-z0-9_()]*%(?:[\\/]|$)")


def expand_leading_env(text: str) -> str:
    """``%USERPROFILE%\\Desktop\\map.pdf`` with its variable expanded, on Windows only."""




    if IS_WINDOWS and isinstance(text, str) and _LEADING_WINDOWS_VAR.match(text):
        return os.path.expandvars(text)
    return text


def release_pooled_handles(layer) -> None:
    """Close the file handle QGIS's OGR connection pool keeps for ``layer``."""








    if not IS_WINDOWS:
        return
    try:
        if layer.providerType() != "ogr":
            return
        provider = layer.dataProvider()
        if provider is not None:
            provider.reloadData()
    except Exception:  # nosec B110 - nothing here may raise; the write that follows reports the lock
        pass


def remove_quietly(path: str) -> bool:
    """Delete one file with the Windows retry; False when it is still there."""
    try:
        retry_file_op(os.remove, path)
    except FileNotFoundError:
        pass
    except OSError:
        return not os.path.lexists(path)
    return True


def remove_tree(path: str) -> list:
    """Delete a folder tree and return the entries that could not go."""





    import shutil
    import stat

    failed = []

    def attempt(func, target):


        if failed:
            func(target)
        else:
            retry_file_op(func, target)

    def on_error(func, target, _exc):
        if func not in (os.remove, os.unlink, os.rmdir):
            failed.append(target)
            return
        try:
            if not os.lstat(target).st_mode & stat.S_IWRITE:
                os.chmod(target, stat.S_IWRITE)
        except OSError:
            pass
        try:
            attempt(func, target)
        except FileNotFoundError:
            pass
        except OSError:
            failed.append(target)

    if not os.path.lexists(path):
        return failed
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=on_error)
            else:
                shutil.rmtree(path, onerror=on_error)
        else:
            retry_file_op(os.remove, path)
    except FileNotFoundError:
        pass
    except OSError:
        failed.append(path)
    return failed


def _windows_memory_counters():
    """This process's PROCESS_MEMORY_COUNTERS from GetProcessMemoryInfo, or None."""
    try:
        import ctypes
        import ctypes.wintypes as wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _Counters()
        counters.cb = ctypes.sizeof(_Counters)

        get_handle = ctypes.windll.kernel32.GetCurrentProcess
        get_handle.restype = wintypes.HANDLE


        query = ctypes.windll.psapi.GetProcessMemoryInfo
        query.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
        query.restype = wintypes.BOOL

        if not query(get_handle(), ctypes.byref(counters), counters.cb):
            return None
        return counters
    except Exception:
        return None


def working_set_mb() -> float | None:
    """Memory this Windows process holds in RAM right now, in MB, or None elsewhere."""
    counters = _windows_memory_counters() if IS_WINDOWS else None
    return round(counters.WorkingSetSize / (1024 * 1024), 1) if counters is not None else None


def peak_memory_mb() -> float | None:
    """Peak resident memory of this process, in MB, or None if unavailable."""




    if IS_WINDOWS:
        counters = _windows_memory_counters()
        return round(counters.PeakWorkingSetSize / (1024 * 1024), 1) if counters is not None else None

    try:
        import resource

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

        rss = rss / (1024 * 1024) if IS_MACOS else rss / 1024
        return round(rss, 1)
    except Exception:
        return None


_OS_INFO = None


def os_info() -> tuple:
    """``(system, release, machine)`` as platform.system/release/machine name them."""






    global _OS_INFO
    if _OS_INFO is not None:
        return _OS_INFO
    system = release = machine = ""
    try:
        if IS_WINDOWS:
            system = "Windows"
            ver = sys.getwindowsversion()
            if ver.major == 10 and ver.build >= 22000:
                release = "11"
            elif ver.major == 10:
                release = "10"
            else:
                release = f"{ver.major}.{ver.minor}"

            machine = (os.environ.get("PROCESSOR_ARCHITEW6432")
                       or os.environ.get("PROCESSOR_ARCHITECTURE") or "")
        else:
            import platform
            system, release, machine = platform.system(), platform.release(), platform.machine()
    except Exception:  # nosec B110 - an OS label is optional
        system = system or ("Darwin" if IS_MACOS else sys.platform)
    _OS_INFO = (system, release, machine)
    return _OS_INFO


def os_label() -> str:
    """``"Windows 11"``, ``"Darwin 24.1.0"``: system and release in one string."""
    system, release, _machine = os_info()
    return f"{system} {release}".strip()
