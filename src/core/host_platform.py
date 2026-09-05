# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Everything in the tool catalog that has to know which OS it runs on."""



from __future__ import annotations

import os
import sys
import time

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"




_FILE_OP_PAUSES_S = (0.05, 0.1, 0.2)


def retry_file_op(op, *args):
    """Call ``op(*args)`` again after a transient PermissionError, then give up."""




    if not IS_WINDOWS:
        return op(*args)
    for pause in _FILE_OP_PAUSES_S:
        try:
            return op(*args)
        except PermissionError:
            time.sleep(pause)
    return op(*args)


def peak_memory_mb() -> float | None:
    """Peak resident memory of this process, in MB, or None if unavailable."""




    if IS_WINDOWS:
        try:
            import ctypes
            from ctypes import wintypes

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
            return round(counters.PeakWorkingSetSize / (1024 * 1024), 1)
        except Exception:
            return None

    try:
        import resource

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

        rss = rss / (1024 * 1024) if IS_MACOS else rss / 1024
        return round(rss, 1)
    except Exception:
        return None
