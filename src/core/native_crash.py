# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


























from __future__ import annotations

import json
import os
import re
import sys

from .logger import log_warning
from .numbers import as_int

MAX_FRAMES = 24
MAX_CHARS = 7_800
_IMAGE_CHARS = 80
_SYMBOL_CHARS = 200
_IPS_MAX_BYTES = 4_000_000
_STACK_TXT_BYTES = 65_536


_CRASH_FOLDER_WINDOW_S = 600.0



_HANDLER_FRAMES = frozenset({
    "__pthread_kill", "pthread_kill", "raise", "abort", "faulthandler_fatal_error", "_sigtramp",
    "RaiseException", "_CxxThrowException", "CxxThrowException", "RtlRaiseException",
    "KiUserExceptionDispatcher",
})
_HANDLER_DEPTH = 10

_WIN_FRAME = re.compile(r"^>(\d+): 0x[0-9A-Fa-f]+ (.+)$")


def for_pid(pid: int, started_at: float, crash_info_at: float = 0.0) -> tuple:



    try:
        if sys.platform == "darwin":
            frames, source = _ips_frames(pid, started_at), "ips"
        elif os.name == "nt" and crash_info_at:
            frames, source = _crash_folder_frames(crash_info_at), "crash_window"
        else:
            return "", ""
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Native crash report not read: {type(exc).__name__}: {exc}")
        return "", ""
    lines: list = []
    used = 0
    for image, symbol in _past_handler(frames)[:MAX_FRAMES]:
        line = f"{image[:_IMAGE_CHARS]}\t{symbol[:_SYMBOL_CHARS]}"
        if used + len(line) + 1 > MAX_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    return ("\n".join(lines), source) if lines else ("", "")


def _past_handler(frames: list) -> list:
    last = -1
    for index, (_image, symbol) in enumerate(frames[:_HANDLER_DEPTH]):
        if symbol in _HANDLER_FRAMES:
            last = index
    return frames[last + 1:]


def _base_name(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _report_folders() -> list:
    base = os.path.join(os.path.expanduser("~"), "Library", "Logs", "DiagnosticReports")
    return [base, os.path.join(base, "Retired")]


def _ips_frames(pid: int, started_at: float) -> list:


    found = []
    for folder in _report_folders():
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            if not (name.lower().startswith("qgis") and name.endswith(".ips")):
                continue
            path = os.path.join(folder, name)
            try:
                mtime, size = os.path.getmtime(path), os.path.getsize(path)
            except OSError:
                continue
            if mtime >= started_at - 5.0 and 0 < size <= _IPS_MAX_BYTES:
                found.append((mtime, path))
    for _mtime, path in sorted(found, reverse=True):
        with open(path, encoding="utf-8", errors="replace") as handle:
            head, _, body = handle.read().partition("\n")
        try:
            header, report = json.loads(head), json.loads(body)
        except ValueError:
            continue
        if not isinstance(header, dict) or str(header.get("bug_type")) != "309":
            continue
        if not isinstance(report, dict) or as_int(report.get("pid"), 0) != pid:
            continue
        return _thread_frames(report)
    return []


def _thread_frames(report: dict) -> list:
    threads = [t for t in report.get("threads") or [] if isinstance(t, dict)]
    index = report.get("faultingThread")
    if isinstance(index, int) and 0 <= index < len(threads):
        thread = threads[index]
    else:
        thread = next((t for t in threads if t.get("triggered")), None)
    if thread is None:
        return []
    images = report.get("usedImages") or []
    frames = []
    for frame in thread.get("frames") or []:
        if not isinstance(frame, dict):
            continue
        at = frame.get("imageIndex")
        image = images[at] if isinstance(at, int) and 0 <= at < len(images) else None
        name = _base_name(str(image.get("name") or "")) if isinstance(image, dict) else ""
        frames.append((name or "?", str(frame.get("symbol") or "")))
    return frames


def _crash_roots() -> list:
    roots: list = []
    try:
        from qgis.PyQt.QtCore import QStandardPaths

        from .qt_compat import enum_member

        location = enum_member(QStandardPaths, "StandardLocation", "AppLocalDataLocation")
        roots.extend(os.path.join(folder, "crashes") for folder in QStandardPaths.standardLocations(location))
    except Exception:  # nosec B110
        pass
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.extend(os.path.join(local, "QGIS", app, "crashes") for app in ("QGIS3", "QGIS4"))
    unique: list = []
    for root in roots:
        if os.path.normcase(os.path.normpath(root)) not in (os.path.normcase(os.path.normpath(r)) for r in unique):
            unique.append(root)
    return unique


def _crash_folder_frames(crash_info_at: float) -> list:

    best = None
    for root in _crash_roots():
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for name in names:
            path = os.path.join(root, name, "stack.txt")
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            delay = mtime - crash_info_at

            rank = (delay < 0, abs(delay))
            if -5.0 <= delay <= _CRASH_FOLDER_WINDOW_S and (best is None or rank < best[0]):
                best = (rank, path)
    if best is None:
        return []
    with open(best[1], encoding="utf-8-sig", errors="replace") as handle:
        text = handle.read(_STACK_TXT_BYTES)
    frames = []
    expected = 0
    for line in text.splitlines():
        found = _WIN_FRAME.match(line.rstrip())
        if not found or int(found.group(1)) < expected or not _code_name(found.group(2)):
            continue
        expected = int(found.group(1)) + 1
        symbol = found.group(2).strip()
        if symbol != "?":
            frames.append(("?", symbol))
    return frames


def _code_name(text: str) -> bool:


    bare = text.replace("operator/=", "").replace("operator/", "")
    return not any(char in bare for char in '\\/"')
